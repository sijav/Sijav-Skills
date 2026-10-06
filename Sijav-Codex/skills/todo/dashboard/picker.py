"""Read the to-do skill's own picker without running its writing CLI.

todo.py migrates the board (CREATE TABLE / ALTER TABLE) and runs a command at
import time, so it is never imported or executed as a whole. Its source is
parsed, and every top-level statement is classified:

  * imports, function definitions and constants are compiled;
  * its additive schema statements (the top-level statements that use `db`)
    are compiled;
  * the body of its `next` command is compiled;
  * `BOARD = ...` and `db = sqlite3.connect(...)` are replaced;
  * CLI start-up statements are skipped, and only if they neither define nor
    change anything the compiled code uses. Anything else is refused
    explicitly, never silently kept at an old value.

The board file is opened read-only (mode=ro, query_only; an idle WAL board
immutable, so no -wal/-shm files appear) and copied with SQLite's backup into
an isolated temporary project. The compiled code runs only against that copy,
with BOARD pointing at it and the working directory inside it. The real board
path is never given to the tool's code, and the copy is deleted afterwards.

Everything the dashboard explains is taken from the tool's own behaviour on
that copy:

  * the order is the sequence of picks choose() makes;
  * status groups are probed with choose() and open_children() on throwaway
    rows inside a savepoint that is rolled back;
  * reasons for tasks next does not offer are block_of() and parent statuses,
    each checked by a rolled-back probe.

Output is one JSON object on stdout. Failures are {"error": "..."}, exit 1;
run directly on a Python older than 3.9, that error says so.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile

sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

REQUIRED = ("all_tasks", "choose", "by_rule", "block_of", "current_phase", "all_phases", "children", "open_children")
SAFE_INTEGER = 2 ** 53 - 1
PROBE = "__dashboard_probe_"
MINIMUM_PYTHON = (3, 9)


def require_python(version_info=sys.version_info):
    """Refuse a Python older than todo.py needs. The dashboard never starts a picker on one;
    run directly, a picker gives this as its JSON error instead of failing deep inside."""
    if tuple(version_info[:2]) < MINIMUM_PYTHON:
        raise RuntimeError(f"Python 3.9 or newer is needed, as for todo.py; this is {version_info[0]}.{version_info[1]}.")


class JsonArguments(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


class ToolChanged(RuntimeError):
    """todo.py has a shape this reader does not know how to reproduce safely."""


def compact_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def canonical(value):
    """The value as the Node reader encodes it, so both sides digest one snapshot."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"$blob": bytes(value).hex()}
    if isinstance(value, float) and not math.isfinite(value):
        return {"$float": "nan" if value != value else ("inf" if value > 0 else "-inf")}
    if isinstance(value, int) and not isinstance(value, bool) and abs(value) > SAFE_INTEGER:
        return str(value)
    return value


def plain(value):
    """A JSON-safe form of a value the tool's code returned."""
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    if isinstance(value, dict):
        return {str(key): plain(item) for key, item in value.items()}
    if value is None or isinstance(value, (str, bool, int, float)):
        return canonical(value)
    return repr(value)


def stat_signature(path):
    parts = []
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            s = os.stat(path + suffix)
            parts.append((suffix, s.st_size, s.st_mtime_ns, s.st_ino))
        except FileNotFoundError:
            parts.append((suffix, None))
    return parts


def is_wal(path):
    with open(path, "rb") as handle:
        header = handle.read(20)
    return len(header) >= 20 and header[18] == 2


def snapshot_to(db_path, target):
    """Copy one consistent snapshot of the board into `target`, writing nothing beside the board."""
    path = str(Path(db_path).resolve())
    if not os.path.isfile(path):
        raise FileNotFoundError(f"No board file at {path}. Nothing was created.")
    for _attempt in range(5):
        before = stat_signature(path)
        idle_wal = is_wal(path) and not os.path.exists(path + "-wal") and not os.path.exists(path + "-shm")
        uri = Path(path).as_uri() + ("?mode=ro&immutable=1" if idle_wal else "?mode=ro")
        source = sqlite3.connect(uri, uri=True, timeout=5)
        try:
            source.execute("PRAGMA query_only=ON")
            copy = sqlite3.connect(target)
            try:
                source.backup(copy)
            finally:
                copy.close()
        finally:
            source.close()
        if not idle_wal or stat_signature(path) == before:
            return
        os.remove(target)
    raise RuntimeError("The board kept changing while it was copied. It will be read again on its next change.")


def table_rows(conn):
    """Every user table in the order the Node reader uses: name, then primary key or rowid."""
    tables = {}
    names = [row[0] for row in conn.execute(
        "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    for name in names:
        quoted = '"' + name.replace('"', '""') + '"'
        info = conn.execute(f"PRAGMA table_info({quoted})").fetchall()
        keys = [column[1] for column in sorted((c for c in info if c[5]), key=lambda c: c[5])]
        order = ",".join('"' + key.replace('"', '""') + '"' for key in keys) if keys else "rowid"
        try:
            rows = conn.execute(f"SELECT * FROM {quoted} ORDER BY {order}").fetchall()
        except sqlite3.OperationalError:
            # Ordering by the key needs something this reader lacks, such as the key column's
            # application-defined collation; rows are then read in stored order.
            rows = conn.execute(f"SELECT * FROM {quoted}").fetchall()
        tables[name] = [[canonical(value) for value in row] for row in rows]
    return tables


def schema_of(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
    out = {}
    for (name,) in rows:
        quoted = '"' + name.replace('"', '""') + '"'
        out[name] = [column[1] for column in conn.execute(f"PRAGMA table_info({quoted})").fetchall()]
    return out


# ------------------------------------------------------------------ todo.py

def names_in(node):
    return {child.id for child in ast.walk(node) if isinstance(child, ast.Name)}


def is_command_test(test, name):
    return (isinstance(test, ast.Compare) and isinstance(test.left, ast.Name) and test.left.id == "command"
            and len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq)
            and isinstance(test.comparators[0], ast.Constant) and test.comparators[0].value == name)


def targets_of(node):
    if isinstance(node, ast.Assign):
        return [t for t in node.targets]
    if isinstance(node, (ast.AugAssign, ast.AnnAssign)):
        return [node.target]
    return []


def is_constant_name(name):
    return name.isupper() and not name.startswith("_")


def load_tool(todo_path):
    """Classify every top-level statement of todo.py; refuse what cannot be reproduced."""
    raw = Path(todo_path).read_bytes()  # hashed as stored, so Node's file hash matches
    source = raw.decode("utf-8")
    tree = ast.parse(source, filename=str(todo_path))
    definitions, schema, skipped, next_body, defined = [], [], [], None, set()
    functions = {}
    for index, node in enumerate(tree.body):
        if index == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue  # module docstring
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            definitions.append(node)
            continue
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            definitions.append(node)
            defined.add(node.name)
            if isinstance(node, ast.FunctionDef):
                functions[node.name] = node
            continue
        targets = targets_of(node)
        target_names = [t.id for t in targets if isinstance(t, ast.Name)]
        if target_names == ["BOARD"]:
            continue  # replaced by the isolated snapshot path
        if target_names == ["db"] and isinstance(node, ast.Assign):
            continue  # the tool's own sqlite3.connect(BOARD), replaced by the copy
        if targets and all(isinstance(t, ast.Name) and is_constant_name(t.id) for t in targets):
            value = node.value
            if value is not None and any(isinstance(child, ast.Call) for child in ast.walk(value)):
                raise ToolChanged(
                    f"todo.py line {node.lineno} computes {', '.join(target_names)} with a call at start-up. "
                    "The dashboard cannot reproduce that safely, so it shows no order rather than a stale one.")
            definitions.append(node)
            defined.update(target_names)
            continue
        if isinstance(node, ast.If) and is_command_test(node.test, "phase"):
            branch = node
            while branch is not None:
                if is_command_test(branch.test, "next"):
                    next_body = branch.body
                    break
                branch = branch.orelse[0] if len(branch.orelse) == 1 and isinstance(branch.orelse[0], ast.If) else None
            continue
        if "db" in names_in(node):
            schema.append(node)
            continue
        skipped.append(node)
    # A skipped start-up statement may read process state, but it may not
    # define or change anything the compiled code depends on.
    for node in skipped:
        touched = sorted((names_in(node) & defined) - {"BOARD"})
        if touched:
            raise ToolChanged(
                f"todo.py line {node.lineno} uses or changes {', '.join(touched)} outside a function at start-up. "
                "The dashboard cannot reproduce it, so it shows no order rather than a stale one.")
    missing = [name for name in REQUIRED if name not in functions]
    if missing:
        raise ToolChanged(f"{todo_path} does not define {', '.join(missing)}, so its order cannot be reused.")
    if next_body is None:
        raise ToolChanged(f"{todo_path} has no `next` command branch to reproduce.")
    if not schema:
        raise ToolChanged(f"{todo_path} has no schema statements; this is not the expected to-do tool.")
    labels = None
    returns = [n for n in ast.walk(functions["by_rule"]) if isinstance(n, ast.Return)]
    if len(returns) == 1 and isinstance(returns[0].value, ast.Tuple):
        labels = [ast.unparse(element) for element in returns[0].value.elts]
    docs = {name: ast.get_docstring(functions[name]) for name in ("choose", "by_rule") if ast.get_docstring(functions[name])}
    compile_module = lambda body: compile(ast.Module(body=body, type_ignores=[]), str(todo_path), "exec")
    return {
        "definitions": compile_module(definitions),
        "schema": compile_module(schema),
        "next": compile_module(next_body),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "rankLabels": labels,
        "docs": docs,
        "constants": sorted(defined - set(functions)),
    }


# ------------------------------------------------------------------ probes

class Probe:
    """Throwaway rows inside a savepoint on the isolated copy, always rolled back."""

    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        self.conn.execute("SAVEPOINT dashboard_probe")
        return self

    def __exit__(self, *_):
        self.conn.execute("ROLLBACK TO dashboard_probe")
        self.conn.execute("RELEASE dashboard_probe")
        return False

    def insert(self, table, values):
        """A row with the given values; other NOT NULL columns without a default get a neutral value."""
        info = self.conn.execute(f'PRAGMA table_info("{table}")').fetchall()
        row = dict(values)
        for _cid, name, kind, notnull, default, _pk in info:
            if name in row or not notnull or default is not None:
                continue
            row[name] = 0 if "INT" in (kind or "").upper() or "REAL" in (kind or "").upper() else "dashboard probe"
        columns = ",".join(f'"{name}"' for name in row)
        self.conn.execute(f'INSERT INTO "{table}" ({columns}) VALUES ({",".join("?" * len(row))})', list(row.values()))


def derive_policy(f, conn, board_statuses):
    """Which statuses the tool starts, offers, treats as satisfying a parent, and treats as closed."""
    constants = {name: f[name] for name in ("SEVERITIES", "STATUSES", "POINTS") if name in f}
    statuses = list(dict.fromkeys([*(constants.get("STATUSES") or []), *board_statuses]))
    severity = (constants.get("SEVERITIES") or [None])[0]
    points = (constants.get("POINTS") or [None])[0]
    tables = schema_of(conn)
    if severity is None or points is None:
        raise ToolChanged("todo.py has no SEVERITIES/POINTS constants to build its probe rows from.")
    task = lambda tid, status, **extra: {"id": PROBE + tid, "title": "probe", "severity": severity, "points": points, "status": status, **extra}
    by_status = {}
    for status in statuses:
        result = {"started": None, "offered": None, "satisfies": None, "closed": None, "errors": []}
        try:
            with Probe(conn) as probe:
                probe.insert("task", task("a", status))
                mine = [t for t in f["all_tasks"]() if t["id"] == PROBE + "a"]
                started, pick = f["choose"](mine)
                result["started"] = any(t["id"] == PROBE + "a" for t in started)
                result["offered"] = pick is not None and pick["id"] == PROBE + "a"
        except Exception as error:  # a status the schema refuses, or code that fails on it
            result["errors"].append(f"start/offer probe: {type(error).__name__}: {error}")
        by_status[status] = result
    offered = [s for s, r in by_status.items() if r["offered"] and not r["started"]]
    if not offered:
        raise ToolChanged("No status is offered by todo.py's choose() for a fresh task; its policy cannot be read.")
    fresh = offered[0]
    for status, result in by_status.items():
        try:
            with Probe(conn) as probe:
                probe.insert("task", task("parent", status))
                if "blocked" not in tables:
                    raise ToolChanged("the board has no blocked table to hold the parent out of the pick")
                probe.insert("blocked", {"task": PROBE + "parent", "reason": "probe", "since": "probe"})
                probe.insert("task", task("child", fresh))
                probe.insert("blocked_by", {"task": PROBE + "child", "parent": PROBE + "parent"})
                mine = [t for t in f["all_tasks"]() if t["id"].startswith(PROBE)]
                started, pick = f["choose"](mine)
                if pick is not None and pick["id"] == PROBE + "parent":
                    raise ToolChanged("a blocked task was still picked")
                result["satisfies"] = pick is not None and pick["id"] == PROBE + "child"
        except Exception as error:
            result["errors"].append(f"parent probe: {type(error).__name__}: {error}")
        try:
            with Probe(conn) as probe:
                probe.insert("task", task("origin", fresh))
                probe.insert("task", task("finding", status, parent_task=PROBE + "origin"))
                result["closed"] = not any(c["id"] == PROBE + "finding" for c in f["open_children"](PROBE + "origin"))
        except Exception as error:
            result["errors"].append(f"finding probe: {type(error).__name__}: {error}")
    groups = {"doing": [], "open": [], "finished": [], "discarded": [], "other": [], "unknown": []}
    for status, r in by_status.items():
        if None in (r["started"], r["offered"], r["satisfies"], r["closed"]):
            groups["unknown"].append(status)
        elif r["started"]:
            groups["doing"].append(status)
        elif r["offered"]:
            groups["open"].append(status)
        elif r["closed"] and r["satisfies"]:
            groups["finished"].append(status)
        elif r["closed"]:
            groups["discarded"].append(status)
        else:
            groups["other"].append(status)
    return {
        "severities": plain(constants.get("SEVERITIES")),
        "points": plain(constants.get("POINTS")),
        "statuses": statuses,
        "groups": groups,
        "satisfying": [s for s, r in by_status.items() if r["satisfies"]],
        "closed": [s for s, r in by_status.items() if r["closed"]],
        "probes": by_status,
        "freshStatus": fresh,
    }


# ------------------------------------------------------------------ the pick

def read_picker(db_path, todo_path):
    tool = load_tool(todo_path)
    project = Path(db_path).resolve().parent.parent.name or "project"
    isolated = tempfile.mkdtemp(prefix="sijav-todo-picker-")
    home = os.getcwd()
    try:
        board = os.path.join(isolated, project, ".claude", "todo.db")
        os.makedirs(os.path.dirname(board))
        snapshot_to(db_path, board)
        conn = sqlite3.connect(board)
        try:
            os.chdir(os.path.join(isolated, project))
            return evaluate(tool, conn, board, todo_path)
        finally:
            os.chdir(home)
            conn.close()
    finally:
        shutil.rmtree(isolated, ignore_errors=True)


def evaluate(tool, conn, board, todo_path):
    rows = table_rows(conn)  # before the tool's schema runs: this is the board as stored
    stored_schema = schema_of(conn)
    had_task_table = "task" in stored_schema
    namespace = {"__name__": "todo_readonly", "__file__": str(todo_path), "BOARD": board,
                 "argv": ["next"], "command": "next", "args": []}
    with contextlib.redirect_stdout(io.StringIO()):
        exec(tool["definitions"], namespace)
        namespace["db"] = conn
        conn.row_factory = sqlite3.Row
        exec(tool["schema"], namespace)
    conn.commit()
    conn.isolation_level = None  # probes manage their own savepoints
    copy_schema = schema_of(conn)
    added = []
    for table, columns in copy_schema.items():
        if table not in stored_schema:
            added.append({"table": table, "columns": columns})
        else:
            extra = [column for column in columns if column not in stored_schema[table]]
            if extra:
                added.append({"table": table, "columns": extra, "existingTable": True})

    f = namespace
    tasks = f["all_tasks"]()
    by_id = {task["id"]: task for task in tasks}
    started, pick = f["choose"](tasks)
    started_ids = [task["id"] for task in started]

    # The eligible order is the sequence of picks choose() makes once started
    # work is set aside: each pick is removed and choose() is asked again.
    # Removing a picked task changes no other task's eligibility (it is not
    # done, so nothing waiting on it becomes eligible either).
    rest = [task for task in tasks if task["id"] not in set(started_ids)]
    eligible_ids = []
    while True:
        again, head = f["choose"](rest)
        if again:
            raise ToolChanged("choose() still reported started work after it was set aside; its order cannot be reproduced.")
        if head is None:
            break
        eligible_ids.append(head["id"])
        rest = [task for task in rest if task["id"] != head["id"]]
    expected = started_ids[0] if started_ids else (eligible_ids[0] if eligible_ids else None)
    if expected != (pick and pick["id"]):
        raise RuntimeError("The eligible order did not reproduce choose()'s pick; refusing to show a guessed order.")

    policy = derive_policy(f, conn, sorted({task["status"] for task in tasks}))
    closed = set(policy["closed"])
    satisfying = set(policy["satisfying"])
    pickable = set(started_ids) | set(eligible_ids)
    unfinished = [task for task in tasks if task["status"] not in closed and task["id"] not in pickable]

    rank_keys, rank_errors = {}, {}
    for task in tasks:
        if task["status"] in closed:
            continue
        try:
            rank_keys[task["id"]] = plain(f["by_rule"](task))
        except Exception as error:  # a legacy row the tool never ranks; it must not cost the pick
            rank_errors[task["id"]] = f"{type(error).__name__}: {error}"

    def waiting_key(task):
        key = rank_keys.get(task["id"])
        return (0, key, task["id"]) if key is not None else (1, [], task["id"])
    try:
        unfinished.sort(key=waiting_key)
    except TypeError:
        unfinished.sort(key=lambda task: (task["id"] not in rank_keys, task["id"]))

    deferred = {}
    for task in unfinished:
        reasons = []
        try:
            block = f["block_of"](task["id"])
        except Exception as error:
            block = None
            reasons.append({"kind": "error", "message": f"block_of failed: {type(error).__name__}: {error}"})
        if block is not None:
            reasons.append({"kind": "blocked", "message": f"Blocked: {block}"})
        unmet = []
        for parent in task.get("parents") or []:
            other = by_id.get(parent)
            if other is None:
                reasons.append({"kind": "parent", "message": f"Waits on {parent}, which is not on this board."})
                unmet.append(parent)
            elif other["status"] not in satisfying:
                reasons.append({"kind": "parent", "message": f"Waits on {parent} ({other['status']}): {other['title']}"})
                unmet.append(parent)
        status_offered = task["status"] in policy["groups"]["open"] or task["status"] in policy["groups"]["doing"]
        if not status_offered:
            reasons.append({"kind": "status", "message": f"Status {task['status']}: todo.py next does not offer tasks in this status."})
        reasons.append(confirm(f, conn, task, block, unmet, by_id, satisfying, status_offered))
        deferred[task["id"]] = reasons

    family = {}
    for task in tasks:
        own = f["children"](task["id"])
        if own:
            family[task["id"]] = {"all": [child["id"] for child in own],
                                  "open": [child["id"] for child in f["open_children"](task["id"])]}

    text = io.StringIO()
    exit_code = 0
    with contextlib.redirect_stdout(text):
        try:
            exec(tool["next"], dict(namespace))
        except SystemExit as stop:
            exit_code = stop.code if isinstance(stop.code, int) else 0

    current = f["current_phase"]()
    return {
        "checkedAt": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "tool": {"path": str(Path(todo_path).resolve()), "sha256": tool["sha256"], "constants": tool["constants"],
                 "python": sys.executable, "pythonVersion": ".".join(map(str, sys.version_info[:3]))},
        "boardHadTaskTable": had_task_table,
        "schemaAddedOnCopy": added,
        "policy": {**policy, "rankLabels": tool["rankLabels"], "docs": tool["docs"]},
        "currentPhase": plain(dict(current)) if current else None,
        "phases": plain(f["all_phases"]()),
        "headId": pick["id"] if pick else None,
        "headKind": None if pick is None else ("started" if started_ids else "eligible"),
        "startedIds": started_ids,
        "eligibleIds": eligible_ids,
        "startableIds": started_ids + eligible_ids,
        "rankedIds": started_ids + eligible_ids + [task["id"] for task in unfinished],
        "deferred": deferred,
        "rankKeys": rank_keys,
        "rankErrors": rank_errors,
        "children": family,
        "nextText": text.getvalue(),
        "nextExitCode": exit_code,
        "tables": rows,
    }


def confirm(f, conn, task, block, unmet, by_id, satisfying, status_offered):
    """Check with choose() that resolving the stated reasons makes the task pickable."""
    if not status_offered:
        return {"kind": "check", "verified": True, "message": "Checked with todo.py: its status alone keeps it out of next."}
    fixable = [p for p in unmet if p in by_id]
    if len(fixable) != len(unmet):
        return {"kind": "check", "verified": False, "message": "A parent is missing from the board, so todo.py can never offer this task until that link changes."}
    satisfied = next(iter(sorted(satisfying)), None)
    try:
        with Probe(conn) as probe:
            if block is not None:
                conn.execute("DELETE FROM blocked WHERE task = ?", (task["id"],))
            if fixable:
                if satisfied is None:
                    raise ToolChanged("no status satisfies a parent")
                conn.executemany("UPDATE task SET status = ? WHERE id = ?", [(satisfied, p) for p in fixable])
            # The task with every task whose status satisfies a parent: parents that
            # were already satisfied before the probe must count too, or choose()
            # would see them as unfinished. Picks other than the task are set aside
            # and choose() is asked again, as for the eligible order.
            rest = [t for t in f["all_tasks"]() if t["id"] == task["id"] or t["status"] in satisfying]
            ok = False
            while rest:
                started, pick = f["choose"](rest)
                if any(t["id"] == task["id"] for t in started) or (pick is not None and pick["id"] == task["id"]):
                    ok = True
                    break
                if pick is None:
                    break
                rest = [t for t in rest if t["id"] != pick["id"] and not any(s["id"] == t["id"] for s in started)]
    except Exception as error:
        return {"kind": "check", "verified": False, "message": f"todo.py could not evaluate this task: {type(error).__name__}: {error}"}
    if ok:
        return {"kind": "check", "verified": True, "message": "Checked with todo.py: resolving the reasons above makes it pickable."}
    return {"kind": "check", "verified": False, "message": "todo.py still does not offer it after those reasons are resolved; something else in its rules applies."}


def main():
    parser = JsonArguments(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--todo", required=True)
    try:
        args = parser.parse_args()
        require_python()
        result = read_picker(args.db, args.todo)
        exit_code = 0
    except Exception as error:  # every failure is reported as data, never as a guessed order
        result = {"error": f"{type(error).__name__}: {error}"}
        exit_code = 1
    with contextlib.suppress(AttributeError, ValueError):
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    print(compact_json(result), flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
