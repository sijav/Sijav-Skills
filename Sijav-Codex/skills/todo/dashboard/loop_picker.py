"""Read a loop board's own order without running any of its commands.

A loop board is an SQLite file with an `item` table and a `dep` table
(item, blocker), driven by its own tool, a Python file. The tool keeps the
order in functions that take a connection: board_order(conn), which pairs every
open item with whether it can be started, or the parts it is built from --
open_items(conn), blocked_ids(conn), parked_ids(conn) and sort_key(row). They
may live in the tool itself or in a module it imports (a tool/ folder beside it).

The tool is imported, never run: its commands run only from its main(), and
importing it must not touch a database. The board file is opened read-only and
copied with SQLite's backup into a temporary folder, exactly as picker.py does,
and every function of the tool runs only on that copy, which is deleted
afterwards. The real board path is never given to the tool's code.

Everything shown is the tool's own behaviour on the copy:

  * the order is board_order()'s (or sorted(open_items(), key=sort_key) with
    blocked and parked items not startable, when the tool has no board_order);
  * what each status means comes from probes on throwaway rows inside a rolled
    back savepoint: whether open_items() still lists an item in that status
    (closed or not), whether a blocker in that status still blocks (satisfying),
    and whether a fresh item in that status is startable. The item table's own
    default status is the status of new, not yet started work;
  * the reasons an item cannot be started are its open blockers and its parked
    note, each checked by a rolled back probe that removes them.

What the tool's `next` adds on top of that order -- measurements of the
machine, such as whether a database it needs is ready -- is not run here: it
runs commands and reads files, which a read-only view must not do.

Output is one JSON object on stdout, the shape picker.py prints. Failures are
{"error": "..."}, exit 1.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import inspect
import io
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from picker import JsonArguments, Probe, ToolChanged, compact_json, plain, schema_of, snapshot_to, table_rows  # noqa: E402

PROBE_TITLE = "__dashboard_probe__"
NEEDED = ("open_items", "sort_key")


def load_tool(tool_path):
    """The tool as a module, imported under a name that never runs its main()."""
    path = Path(tool_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"No loop board tool at {path}.")
    raw = path.read_bytes()
    spec = importlib.util.spec_from_file_location("loop_tool_readonly", str(path))
    module = importlib.util.module_from_spec(spec)
    before = set(sys.modules)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        spec.loader.exec_module(module)
    folder = path.parent
    imported = [sys.modules[name] for name in set(sys.modules) - before
                if folder in Path(getattr(sys.modules[name], "__file__", None) or "/").resolve().parents]
    return module, imported, hashlib.sha256(raw).hexdigest()


def find(name, module, imported):
    """The tool's own function: in the tool, else in a module it imported from its folder."""
    for owner in (module, *imported):
        value = getattr(owner, name, None)
        if callable(value):
            return value
    return None


def default_status(conn):
    for _cid, name, _kind, _notnull, default, _pk in conn.execute('PRAGMA table_info("item")'):
        if name == "status" and default is not None:
            return str(default).strip().strip("'\"")
    return None


def severity_values(conn, module, imported):
    """The severities, most severe first: the tool's SEV_RANK, else the item table's CHECK list."""
    for owner in (module, *imported):
        ranks = getattr(owner, "SEV_RANK", None)
        if isinstance(ranks, dict) and ranks:
            return [name for name, _ in sorted(ranks.items(), key=lambda pair: pair[1])]
    sql = conn.execute("SELECT sql FROM sqlite_schema WHERE name='item'").fetchone()
    match = re.search(r"severity\b[^,]*?CHECK\s*\(\s*severity\s+IN\s*\(([^)]*)\)", (sql or [""])[0] or "", re.I)
    return re.findall(r"'([^']*)'", match.group(1)) if match else []


class Order:
    """The tool's order on one connection: (row, startable) pairs, and the sets it is made from."""

    def __init__(self, module, imported):
        self.board_order = find("board_order", module, imported)
        self.open_items = find("open_items", module, imported)
        self.sort_key = find("sort_key", module, imported)
        self.blocked_ids = find("blocked_ids", module, imported)
        self.parked_ids = find("parked_ids", module, imported)
        if self.board_order is None and not (self.open_items and self.sort_key):
            raise ToolChanged("the tool has neither board_order() nor open_items() and sort_key(), so its order"
                              " cannot be reproduced; nothing is guessed.")
        if self.open_items is None:
            raise ToolChanged("the tool has no open_items(), so which statuses it treats as closed cannot be read.")

    def pairs(self, conn):
        if self.board_order is not None:
            return [(tuple(row), bool(ok)) for row, ok in self.board_order(conn)]
        skip = set(self.blocked_ids(conn) if self.blocked_ids else ()) | set(self.parked_ids(conn) if self.parked_ids else ())
        return [(tuple(row), row[0] not in skip) for row in sorted(self.open_items(conn), key=self.sort_key)]

    def open_ids(self, conn):
        return {row[0] for row in self.open_items(conn)}


def probe_item(probe, conn, severity, status, title=PROBE_TITLE):
    """A throwaway item; only the columns this board has (an older board may lack exit_cmd or created_at)."""
    columns = {row[1] for row in conn.execute('PRAGMA table_info("item")')}
    values = {"title": title, "severity": severity, "status": status, "priority": 0,
              "exit_cmd": "dashboard probe", "created_at": 0, "occurrences": 0}
    probe.insert("item", {name: value for name, value in values.items() if name in columns})
    return conn.execute("SELECT max(id) FROM item").fetchone()[0]


def derive_policy(order, conn, statuses, fresh, severities):
    """Which statuses the tool lists as open, offers to start, treats as satisfying a blocker, and closes."""
    severity = severities[-1]  # the least severe: a probe row must satisfy the table's CHECK, nothing more
    by_status = {}
    for status in statuses:
        result = {"started": None, "offered": None, "satisfies": None, "closed": None, "errors": []}
        try:
            with Probe(conn) as probe:
                mine = probe_item(probe, conn, severity, status)
                result["closed"] = mine not in order.open_ids(conn)
                result["offered"] = any(row[0] == mine and ok for row, ok in order.pairs(conn))
                result["started"] = bool(result["offered"]) and fresh is not None and status != fresh
        except Exception as error:
            result["errors"].append(f"open/offer probe: {type(error).__name__}: {error}")
        by_status[status] = result
    if fresh is None or not by_status.get(fresh, {}).get("offered"):
        raise ToolChanged("the item table's default status is not offered by the tool, so a fresh item's status is"
                          " unknown; its policy cannot be read.")
    for status in statuses:
        result = by_status[status]
        try:
            with Probe(conn) as probe:
                blocker = probe_item(probe, conn, severity, status, PROBE_TITLE + "blocker")
                child = probe_item(probe, conn, severity, fresh, PROBE_TITLE + "child")
                probe.insert("dep", {"item": child, "blocker": blocker})
                result["satisfies"] = any(row[0] == child and ok for row, ok in order.pairs(conn))
        except Exception as error:
            result["errors"].append(f"blocker probe: {type(error).__name__}: {error}")
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
        "severities": severities,
        "points": None,
        "statuses": statuses,
        "groups": groups,
        "satisfying": [s for s, r in by_status.items() if r["satisfies"]],
        "closed": [s for s, r in by_status.items() if r["closed"]],
        "probes": by_status,
        "freshStatus": fresh,
    }


def evaluate(module, imported, sha, tool_path, conn):
    rows = table_rows(conn)  # the board as stored, before any probe
    stored = schema_of(conn)
    if "item" not in stored or "dep" not in stored:
        raise ToolChanged("this file has no item and dep tables, so it is not a loop board.")
    order = Order(module, imported)
    conn.isolation_level = None  # probes manage their own savepoints
    fresh = default_status(conn)
    severities = severity_values(conn, module, imported)
    if not severities:
        raise ToolChanged("the tool names no severities (SEV_RANK) and the item table checks none; probe rows cannot be made.")
    stored_statuses = [r[0] for r in conn.execute("SELECT DISTINCT status FROM item ORDER BY status")]
    statuses = list(dict.fromkeys([*([fresh] if fresh else []), *stored_statuses]))
    policy = derive_policy(order, conn, statuses, fresh, severities)
    satisfying = set(policy["satisfying"])

    pairs = order.pairs(conn)
    has_parked = "parked" in stored["item"]
    items = {r[0]: {"id": r[0], "title": r[1], "status": r[2], "parked": r[3]}
             for r in conn.execute(f"SELECT id, title, status, {'parked' if has_parked else 'NULL'} FROM item")}
    blockers = {}
    for item, blocker in conn.execute("SELECT item, blocker FROM dep"):
        blockers.setdefault(item, []).append(blocker)
    startable = [row[0] for row, ok in pairs if ok]
    waiting = [row[0] for row, ok in pairs if not ok]
    rank_keys = {}
    if order.sort_key is not None:
        for row, _ok in pairs:
            with contextlib.suppress(Exception):
                rank_keys[str(row[0])] = plain(list(order.sort_key(row)))

    deferred = {}
    for item_id in waiting:
        item = items.get(item_id, {"title": "", "parked": None})
        reasons, unmet = [], []
        if item.get("parked") is not None:
            reasons.append({"kind": "blocked", "message": f"Parked: {item['parked']}"})
        for blocker in blockers.get(item_id, []):
            other = items.get(blocker)
            if other is None:
                reasons.append({"kind": "parent", "message": f"Waits on {blocker}, which is not on this board."})
                unmet.append(blocker)
            elif other["status"] not in satisfying:
                reasons.append({"kind": "parent", "message": f"Waits on {blocker} ({other['status']}): {other['title']}"})
                unmet.append(blocker)
        reasons.append(confirm(order, conn, item_id, unmet, items, satisfying, has_parked))
        deferred[str(item_id)] = reasons

    text = io.StringIO()
    for row, ok in pairs:
        severity = row[2] if len(row) > 2 else ""
        priority = row[3] if len(row) > 3 else ""
        text.write(f"{'->' if ok else '  '} {row[0]:<6} [{severity} p{priority}] {str(row[1])[:70]}\n")
    text.write(f"\n{len(pairs)} open, {len(startable)} startable.\n")

    docs = {}
    for name in ("board_order", "open_items", "blocked_ids", "parked_ids", "sort_key"):
        function = find(name, module, imported)
        if function is not None:
            docs[name] = inspect.getdoc(function) or ""
    head = startable[0] if startable else None
    return {
        "checkedAt": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "kind": "loop",
        "tool": {"path": str(Path(tool_path).resolve()), "sha256": sha, "constants": {},
                 "python": sys.executable, "pythonVersion": ".".join(map(str, sys.version_info[:3]))},
        "boardHadTaskTable": True,
        "schemaAddedOnCopy": [],
        "policy": {**policy, "rankLabels": ["severity rank", "priority", "repeats (negative)", "created"], "docs": docs},
        "currentPhase": None,
        "phases": [],
        "headId": None if head is None else str(head),
        "headKind": None if head is None else "eligible",
        "startedIds": [],
        "eligibleIds": [str(i) for i in startable],
        "startableIds": [str(i) for i in startable],
        "rankedIds": [str(i) for i in startable + waiting],
        "deferred": deferred,
        "rankKeys": rank_keys,
        "rankErrors": {},
        "children": {},
        "nextText": text.getvalue(),
        "nextExitCode": 0,
        "tables": rows,
    }


def confirm(order, conn, item_id, unmet, items, satisfying, has_parked):
    """Check with the tool's order that removing the stated reasons makes the item startable."""
    fixable = [b for b in unmet if b in items]
    if len(fixable) != len(unmet):
        return {"kind": "check", "verified": False,
                "message": "A blocker is missing from the board, so the tool can never start this item until that link changes."}
    done = next(iter(sorted(satisfying)), None)
    try:
        with Probe(conn):
            if has_parked:
                conn.execute("UPDATE item SET parked = NULL WHERE id = ?", (item_id,))
            if fixable:
                if done is None:
                    raise ToolChanged("no status satisfies a blocker")
                conn.executemany("UPDATE item SET status = ? WHERE id = ?", [(done, b) for b in fixable])
            ok = any(row[0] == item_id and startable for row, startable in order.pairs(conn))
    except Exception as error:
        return {"kind": "check", "verified": False,
                "message": f"The tool could not evaluate this item: {type(error).__name__}: {error}"}
    if ok:
        return {"kind": "check", "verified": True, "message": "Checked with the tool: removing the reasons above makes it startable."}
    return {"kind": "check", "verified": False,
            "message": "The tool still does not offer it after those reasons are removed; something else in its rules applies."}


def read_picker(db_path, tool_path):
    module, imported, sha = load_tool(tool_path)
    isolated = tempfile.mkdtemp(prefix="sijav-loop-picker-")
    home = os.getcwd()
    try:
        board = os.path.join(isolated, "board.db")
        snapshot_to(db_path, board)
        conn = sqlite3.connect(board)
        try:
            os.chdir(isolated)
            return evaluate(module, imported, sha, tool_path, conn)
        finally:
            os.chdir(home)
            conn.close()
    finally:
        shutil.rmtree(isolated, ignore_errors=True)


def main():
    parser = JsonArguments(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--tool", required=True)
    try:
        args = parser.parse_args()
        if sys.version_info < (3, 9):
            raise RuntimeError("Python 3.9 or newer is needed.")
        result = read_picker(args.db, args.tool)
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
