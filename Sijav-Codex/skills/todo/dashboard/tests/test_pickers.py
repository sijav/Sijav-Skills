"""Picker contracts on synthetic boards; no operational board is opened.

The two CLIs run as real children. In-process cases exercise the same readers
with ordinary tool copies; only the otherwise timing-dependent idle-WAL race
uses a documented stat_signature double. This file is test source, not plugin
runtime code, and the normal whole-area runner discovers it under coverage.py.
"""
import ast
from contextlib import closing
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

DASHBOARD = Path(__file__).resolve().parents[1]
TODO = DASHBOARD.parent / "todo.py"
sys.path.insert(0, str(DASHBOARD))
import picker
import loop_picker


def physical_lines(source):
    """AST lineno counts LF boundaries, not separators inside Python strings."""
    parts = source.split("\n")
    return [part + "\n" for part in parts[:-1]] + [parts[-1]]


def altered(source, name, body):
    """Replace exactly one physical function span in a synthetic tool copy."""
    matches = [node for node in ast.parse(source).body
               if isinstance(node, ast.FunctionDef) and node.name == name]
    if len(matches) != 1:
        raise AssertionError(f"fixture anchor {name!r} occurs {len(matches)} times")
    node = matches[0]
    lines = physical_lines(source)
    candidate = "".join(lines[:node.lineno - 1]) + body.rstrip() + "\n" + "".join(lines[node.end_lineno:])
    compile(candidate, "<synthetic picker tool>", "exec")
    replacement = ast.parse(body).body[0]
    inserted = [item for item in ast.parse(candidate).body
                if isinstance(item, ast.FunctionDef) and item.name == replacement.name]
    if len(inserted) != 1 or ast.dump(inserted[0], include_attributes=False) != ast.dump(replacement, include_attributes=False):
        raise AssertionError(f"fixture replacement {name!r} was not exact")
    return candidate


class Fixtures(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="todo-picker-contract-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "Test Project"
        self.root.mkdir()
        # Same board opt-in boundary as the tool; a temporary folder below a
        # real project is never a valid fixture owner.
        for parent in (self.root, *self.root.parents):
            if (parent / ".claude" / "todo.db").exists():
                self.fail("the synthetic fixture is beneath an existing board")
        self.db = self.root / ".claude" / "todo.db"
        self.source = TODO.read_text(encoding="utf-8")
        self.tool = Path(self.tmp.name) / "todo-copy.py"
        self.tool.write_text(self.source, encoding="utf-8")

    def todo(self, *args):
        env = {key: value for key, value in os.environ.items() if key not in ("CLAUDE_SESSION_ID", "CODEX_THREAD_ID", "CODEX_SESSION_ID")}
        done = subprocess.run([sys.executable, "-B", str(TODO), *args], cwd=self.root, env=env,
                              capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return done.stdout

    def board(self, statuses=("backlog",)):
        self.todo("init", "--here")
        for n, status in enumerate(statuses, 1):
            self.todo("add", "--id", f"TP-{n:03d}", "--title", f"Synthetic {n}", "--desc", "As a reader I want a reliable next task",
                      "--why", "So the fixture can demonstrate the picker contract", "--severity", "high", "--points", "2",
                      "--exit", "python -c \"raise SystemExit(0)\"")
            with closing(sqlite3.connect(self.db)) as conn, conn:
                conn.execute("PRAGMA ignore_check_constraints=ON")
                conn.execute("UPDATE task SET status=? WHERE id=?", (status, f"TP-{n:03d}"))
        return self.db

    def read(self, source=None):
        if source is not None:
            self.tool.write_text(source, encoding="utf-8")
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        names = sorted(path.name for path in self.db.parent.iterdir())
        try:
            return picker.read_picker(self.db, self.tool)
        finally:
            self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before, "the original synthetic board changed")
            self.assertEqual(sorted(path.name for path in self.db.parent.iterdir()), names)

    def loop(self, *, default=True, check=True, rank=True, board_order=True, source_extra=""):
        db = self.root / "loop.db"
        with closing(sqlite3.connect(db)) as conn, conn:
            status_default = "DEFAULT 'open'" if default else ""
            severity_check = "CHECK(severity IN ('critical','high','low'))" if check else ""
            conn.executescript(f"""CREATE TABLE item(id INTEGER PRIMARY KEY, title TEXT NOT NULL,
                status TEXT NOT NULL {status_default}, severity TEXT NOT NULL {severity_check}, priority INTEGER NOT NULL,
                parked TEXT); CREATE TABLE dep(item INTEGER NOT NULL, blocker INTEGER NOT NULL);
                INSERT INTO item VALUES(1,'Work','open','high',1,NULL);
                INSERT INTO item VALUES(2,'Waiting','open','high',2,NULL);
                INSERT INTO item VALUES(3,'Closed without satisfying','dropped','low',3,NULL);
                INSERT INTO item VALUES(4,'Satisfied','done','low',4,NULL);
                INSERT INTO dep VALUES(2,999); INSERT INTO dep VALUES(2,4);""")
        source = '''
def open_items(conn):
    return conn.execute("SELECT id,title,status,severity,priority,parked FROM item WHERE status NOT IN ('done','dropped')").fetchall()
def sort_key(row):
    return (row[4], row[0])
def blocked_ids(conn):
    return {item for item, blocker, status in conn.execute("SELECT d.item,d.blocker,i.status FROM dep d LEFT JOIN item i ON i.id=d.blocker") if status != 'done'}
def parked_ids(conn):
    return {row[0] for row in open_items(conn) if row[5] is not None}
'''
        if rank:
            source += "SEV_RANK = {'critical':0,'high':1,'low':2}\n"
        if board_order:
            source += "def board_order(conn):\n    skip = blocked_ids(conn) | parked_ids(conn)\n    return [(row,row[0] not in skip) for row in sorted(open_items(conn),key=sort_key)]\n"
        path = self.root / "loop.py"
        path.write_text(source + source_extra, encoding="utf-8")
        return db, path, source


class EntryContracts(Fixtures):
    def test_both_cli_errors_are_json_with_native_exit_one(self):
        loop_tool = self.root / "missing-board-loop.py"
        loop_tool.write_text("def open_items(conn):\n    return []\ndef sort_key(row):\n    return row[0]\n", encoding="utf-8")
        for file, option in (("picker.py", "--todo"), ("loop_picker.py", "--tool")):
            tool_path = self.tool if file == "picker.py" else loop_tool
            for args, expected in (([], "required"), (["--db", str(self.db), option, str(tool_path)], "No board file")):
                with self.subTest(file=file, args=args):
                    done = subprocess.run([sys.executable, "-B", str(DASHBOARD / file), *args], capture_output=True,
                                          text=True, encoding="utf-8", timeout=30, cwd=self.root)
                    self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
                    self.assertEqual(set(json.loads(done.stdout)), {"error"})
                    self.assertIn(expected, json.loads(done.stdout)["error"])
        self.board()
        done = subprocess.run([sys.executable, "-B", str(DASHBOARD / "loop_picker.py"), "--db", str(self.db),
                               "--tool", str(self.root / "missing.py")], capture_output=True, text=True, encoding="utf-8", timeout=30, cwd=self.root)
        self.assertEqual(done.returncode, 1)
        self.assertIn("No loop board tool", json.loads(done.stdout)["error"])
        self.assertFalse((self.root / "missing.py").exists())

    def test_the_direct_cli_python_requirement_is_the_shared_policy(self):
        with self.assertRaisesRegex(RuntimeError, r"Python 3.9 or newer.*3.8"):
            picker.require_python((3, 8, 18))
        self.assertIsNone(picker.require_python((3, 9, 0)))
        self.assertIsNone(picker.require_python((3, 13, 0)))
        self.assertIs(loop_picker.require_python, picker.require_python)

    def test_idle_wal_that_changes_on_all_five_attempts_is_explicitly_refused(self):
        self.board()
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("PRAGMA journal_mode=WAL")
        target = str(self.root / "copy.db")
        before = self.db.read_bytes()
        # Deterministic race double: each before/after stat differs. The actual
        # read-only SQLite backup still runs; no coverage count is synthesized.
        with patch.object(picker, "stat_signature", side_effect=[[n] for n in range(10)]) as signature:
            with self.assertRaisesRegex(RuntimeError, "kept changing"):
                picker.snapshot_to(self.db, target)
        self.assertEqual(signature.call_count, 10)
        self.assertFalse(Path(target).exists())
        self.assertEqual(self.db.read_bytes(), before)
        self.assertFalse(Path(str(self.db) + "-wal").exists())
        self.assertFalse(Path(str(self.db) + "-shm").exists())


class TodoRules(Fixtures):
    def test_annotated_literal_and_class_are_loaded_without_running_startup(self):
        self.board()
        added = "EXTRA_LITERAL: str = 'fixture'\nclass ExtraPolicy:\n    pass\n"
        result = self.read(self.source.replace("def by_rule(task):", added + "\ndef by_rule(task):"))
        self.assertEqual(result["headId"], "TP-001")
        self.assertIn("EXTRA_LITERAL", result["tool"]["constants"])

    def test_no_next_branch_or_additive_schema_is_refused(self):
        with self.assertRaisesRegex(picker.ToolChanged, "next"):
            self.tool.write_text(self.source.replace('elif command == "next":', 'elif command == "next_removed":'), encoding="utf-8")
            picker.load_tool(self.tool)
        tree = ast.parse(self.source)
        lines = physical_lines(self.source)
        drop = set()
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom)):
                continue
            targets = picker.targets_of(node)
            target_names = [target.id for target in targets if isinstance(target, ast.Name)]
            if target_names == ["BOARD"] or (target_names == ["db"] and isinstance(node, ast.Assign)):
                continue
            if targets and all(isinstance(target, ast.Name) and picker.is_constant_name(target.id) for target in targets):
                continue
            if isinstance(node, ast.If) and picker.is_command_test(node.test, "phase"):
                continue  # preserve the complete CLI dispatch, including next
            if "db" in picker.names_in(node):
                drop.update(range(node.lineno - 1, node.end_lineno))
        no_schema = "".join(line for n, line in enumerate(lines) if n not in drop)
        compile(no_schema, "<synthetic missing-schema tool>", "exec")
        self.assertIn('elif command == "next":', no_schema)
        self.tool.write_text(no_schema, encoding="utf-8")
        with self.assertRaisesRegex(picker.ToolChanged, "schema"):
            picker.load_tool(self.tool)

    def test_physical_rewrites_keep_unicode_separators_inside_their_strings(self):
        source = "MARK = 'a\u2028b\u2029c'\ndef choose(tasks):\n    return None\ndef after():\n    return MARK\n"
        candidate = altered(source, "choose", "def choose(tasks):\n    return tasks[0]")
        self.assertIn("MARK = 'a\u2028b\u2029c'", candidate)
        self.assertEqual(ast.get_source_segment(candidate, ast.parse(candidate).body[-1]), "def after():\n    return MARK")
        self.assertEqual(len(physical_lines(source)), 6)
        with self.assertRaisesRegex(AssertionError, "occurs 0"):
            altered(source, "missing", "def missing():\n    pass")

    def test_multiple_by_rule_returns_have_no_invented_rank_labels(self):
        self.board()
        source = altered(self.source, "by_rule", "def by_rule(task):\n    if task['points'] > 1:\n        return (task['points'],task['id'])\n    return (0,task['id'])")
        self.assertIsNone(self.read(source)["policy"]["rankLabels"])

    def test_missing_severities_and_a_tool_that_never_offers_fresh_work_are_refused(self):
        self.board()
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("DELETE FROM task")
        source = self.source.replace('SEVERITIES = ["critical", "high", "medium", "low"]', 'SEVERITIES = []')
        if source == self.source:
            node = next(node for node in ast.parse(self.source).body if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "SEVERITIES" for t in node.targets))
            rows = physical_lines(self.source)
            source = "".join(rows[:node.lineno - 1]) + "SEVERITIES = []\n" + "".join(rows[node.end_lineno:])
        with self.assertRaisesRegex(picker.ToolChanged, "SEVERITIES/POINTS"):
            self.read(source)
        source = altered(self.source, "choose", "def choose(tasks, areas=None):\n    return [],None")
        with self.assertRaisesRegex(picker.ToolChanged, "No status is offered"):
            self.read(source)

    def test_blocked_parent_wrongly_picked_is_recorded_as_unknown_policy(self):
        self.board()
        source = altered(self.source, "choose", "def choose(tasks, areas=None):\n    offered=[t for t in tasks if t['status']=='backlog']\n    blocked=next((t for t in offered if block_of(t['id']) is not None),None)\n    return [], blocked if blocked is not None else (offered[0] if offered else None)")
        result = self.read(source)
        self.assertIn("backlog", result["policy"]["groups"]["unknown"])
        self.assertTrue(any("a blocked task was still picked" in error for error in result["policy"]["probes"]["backlog"]["errors"]))

    def test_started_work_left_after_aside_and_a_disagreeing_pick_are_refused(self):
        self.board(("in_progress", "backlog"))
        bad_started = altered(self.source, "choose", "def choose(tasks, areas=None):\n    started=[t for t in all_tasks() if t['status']=='in_progress']\n    return started,started[0] if started else None")
        with self.assertRaisesRegex(picker.ToolChanged, "after it was set aside"):
            self.read(bad_started)
        bad_pick = altered(self.source, "choose", "def choose(tasks, areas=None):\n    started=[t for t in tasks if t['status']=='in_progress']\n    fresh=[t for t in tasks if t['status']=='backlog']\n    return started,fresh[0] if fresh else (started[0] if started else None)")
        with self.assertRaisesRegex(RuntimeError, "did not reproduce"):
            self.read(bad_pick)

    def test_missing_parent_is_deferred_and_cannot_be_confirmed(self):
        self.board()
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("INSERT INTO blocked_by VALUES('TP-001','missing')")
        reasons = self.read()["deferred"]["TP-001"]
        self.assertTrue(any("not on this board" in reason["message"] for reason in reasons))
        self.assertFalse(reasons[-1]["verified"])

    def test_non_json_and_incomparable_keys_preserve_deferred_records(self):
        self.board(("parked", "parked"))
        source = altered(self.source, "by_rule", "def by_rule(task):\n    if task['id']=='TP-001':\n        return {'recorded key'}\n    return (2,task['id'])")
        result = self.read(source)
        self.assertEqual(result["rankKeys"]["TP-001"], "{'recorded key'}")
        self.assertEqual(result["rankedIds"], ["TP-001", "TP-002"])
        self.assertEqual(result["deferred"]["TP-001"][-1]["verified"], True)

    def test_a_block_reason_failure_is_explained_without_losing_other_rows(self):
        self.board(("parked",))
        source = altered(self.source, "block_of", "def block_of(task_id):\n    if task_id=='TP-001':\n        raise ValueError('legacy block cannot be read')\n    row=db.execute('SELECT reason FROM blocked WHERE task=?',(task_id,)).fetchone()\n    return row['reason'] if row else None")
        source = altered(source, "is_blocked", "def is_blocked(task):\n    return db.execute('SELECT 1 FROM blocked WHERE task=?',(task['id'],)).fetchone() is not None")
        reasons = self.read(source)["deferred"]["TP-001"]
        self.assertEqual(reasons[0]["kind"], "error")
        self.assertIn("block_of failed: ValueError: legacy block cannot be read", reasons[0]["message"])

    def test_status_constraint_rejecting_probe_rows_is_unknown_not_guessed(self):
        # Add the CHECK to the actual CREATE TABLE in an empty synthetic board;
        # migrations and all policy probes still use the ordinary reader.
        self.root.joinpath(".claude").mkdir()
        sqlite3.connect(self.db).close()
        source = self.source.replace("CHECK (status IN ('backlog','in_progress','wait_for_roast','done','dropped'))", "CHECK (status IN ('backlog','done'))")
        self.assertNotEqual(source, self.source, "fixture anchor drift")
        result = self.read(source)
        self.assertIn("in_progress", result["policy"]["groups"]["unknown"])
        self.assertTrue(any("IntegrityError" in error for error in result["policy"]["probes"]["in_progress"]["errors"]))

    def test_rank_failure_is_retained_for_a_legacy_row_the_tool_never_offers(self):
        self.board(("parked", "backlog"))
        source = altered(self.source, "by_rule", """def by_rule(task):
    if task['id']=='TP-001':
        raise ValueError('unrankable legacy task')
    return (task['points'],task['id'])""")
        result = self.read(source)
        self.assertEqual(result["headId"], "TP-002")
        self.assertEqual(result["rankedIds"], ["TP-002", "TP-001"])
        self.assertIn("ValueError: unrankable legacy task", result["rankErrors"]["TP-001"])
        self.assertNotIn("TP-001", result["rankKeys"])

    def test_a_non_tuple_single_return_has_no_invented_rank_labels(self):
        self.board()
        result = self.read(altered(self.source, "by_rule", "def by_rule(task):\n    return [task['points'],task['id']]"))
        self.assertIsNone(result["policy"]["rankLabels"])
        self.assertEqual(result["headId"], "TP-001")

    def test_policy_probe_failure_without_a_blocked_table_is_unknown(self):
        # Call the policy reader on an explicitly incomplete copied schema;
        # normal read_picker adds schema on its isolated copy first.
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.executescript("CREATE TABLE task(id TEXT,title TEXT,severity TEXT,points INT,status TEXT,parent_task TEXT);")
            conn.row_factory = sqlite3.Row
            functions = {"SEVERITIES": ["high"], "STATUSES": ["open", "done"], "POINTS": [1],
                "all_tasks": lambda: [dict(row) for row in conn.execute("SELECT * FROM task")],
                "choose": lambda rows: ([], next((row for row in rows if row["status"] == "open"), None)),
                "open_children": lambda tid: [dict(row) for row in conn.execute("SELECT * FROM task WHERE parent_task=? AND status!='done'", (tid,))]}
            result = picker.derive_policy(functions, conn, [])
            self.assertIn("the board has no blocked table", result["probes"]["open"]["errors"][0])
            self.assertIn("open", result["groups"]["unknown"])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM task").fetchone()[0], 0)

    def test_confirmation_preserves_missing_satisfying_status_and_other_rule_refusals(self):
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.executescript("CREATE TABLE task(id TEXT,status TEXT);CREATE TABLE blocked(task TEXT);INSERT INTO task VALUES('child','open'),('parent','open');INSERT INTO blocked VALUES('child');")
            task = {"id": "child", "status": "open"}
            all_tasks = lambda: [dict(zip(("id","status"), row)) for row in conn.execute("SELECT id,status FROM task")]
            functions = {"all_tasks": all_tasks, "choose": lambda rows: ([], None)}
            failed = picker.confirm(functions, conn, task, "held", ["parent"], {"parent": {}}, set(), True)
            self.assertIn("no status satisfies a parent", failed["message"])
            rejected = picker.confirm(functions, conn, task, "held", [], {}, {"done"}, True)
            self.assertFalse(rejected["verified"])
            self.assertIn("something else", rejected["message"])
            # An authoritative picker may return another satisfied row first.
            # Confirm must set it aside, ask again, and accept a started child.
            functions["choose"] = lambda rows: ([row for row in rows if row["id"] == "child"] if not any(row["id"]=="parent" for row in rows) else [],
                                                next((row for row in rows if row["id"]=="parent"), None))
            conn.execute("UPDATE task SET status='done' WHERE id='parent'")
            accepted = picker.confirm(functions, conn, task, "held", [], {}, {"done"}, True)
            self.assertTrue(accepted["verified"])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM blocked").fetchone()[0], 1, "savepoint always restores the fixture")


    def test_confirmation_exhausts_an_authoritative_filtered_list_without_claiming_the_target(self):
        # Explicit tool policy: its all_tasks query omits this row. This is a
        # controlled policy input with real SQLite/savepoint restoration, not
        # an OS override or a fabricated native producer.
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.executescript("CREATE TABLE task(id TEXT,status TEXT);CREATE TABLE blocked(task TEXT);INSERT INTO task VALUES('child','open'),('parent','done');INSERT INTO blocked VALUES('child');")
            before={table:conn.execute("SELECT * FROM "+table).fetchall() for table in ("task","blocked")}
            chosen=[]
            def choose(rows):
                pick=rows[0];chosen.append(pick["id"])
                return [],pick
            functions={"all_tasks":lambda:[dict(zip(("id","status"),row)) for row in conn.execute("SELECT id,status FROM task WHERE id!='child'")],
                       "choose":choose}
            result=picker.confirm(functions,conn,{"id":"child","status":"open"},"held",[],{},{"done"},True)
            self.assertFalse(result["verified"])
            self.assertIn("still does not offer it",result["message"])
            self.assertEqual(chosen,["parent"],"the list was exhausted instead of ending on a missing pick")
            self.assertEqual(before,{table:conn.execute("SELECT * FROM "+table).fetchall() for table in before})

class LoopRules(Fixtures):
    def read_loop(self, db, path):
        before = db.read_bytes()
        try:
            return loop_picker.read_picker(db, path)
        finally:
            self.assertEqual(db.read_bytes(), before)

    def test_check_severities_and_parts_order_are_used_when_rank_and_board_order_are_absent(self):
        db, path, _ = self.loop(rank=False, board_order=False)
        result = self.read_loop(db, path)
        self.assertEqual(result["policy"]["severities"], ["critical", "high", "low"])
        self.assertEqual(result["headId"], "1")
        self.assertEqual(result["rankedIds"], ["1", "2"])
        self.assertIn("dropped", result["policy"]["groups"]["discarded"])
        self.assertNotIn("dropped", result["policy"]["satisfying"])
        reasons = result["deferred"]["2"]
        self.assertTrue(any("999" in reason["message"] and "not on this board" in reason["message"] for reason in reasons))
        self.assertFalse(reasons[-1]["verified"])
        self.assertFalse(any("Waits on 4" in reason["message"] for reason in reasons), "already-satisfying blockers are retained and ignored")

    def test_no_status_default_and_no_severities_are_refused(self):
        db, path, _ = self.loop(default=False)
        with self.assertRaisesRegex(picker.ToolChanged, "fresh item's status is unknown"):
            self.read_loop(db, path)
        db.unlink()
        db, path, _ = self.loop(check=False, rank=False)
        with self.assertRaisesRegex(picker.ToolChanged, "names no severities"):
            self.read_loop(db, path)

    def test_board_order_without_open_items_and_parts_without_sort_key_are_refused(self):
        db, path, source = self.loop()
        source = altered(source, "open_items", "def open_items_removed(conn):\n    return []")
        path.write_text(source, encoding="utf-8")
        with self.assertRaisesRegex(picker.ToolChanged, "no open_items"):
            self.read_loop(db, path)
        db.unlink()
        db, path, source = self.loop(board_order=False)
        source = altered(source, "sort_key", "def sort_key_removed(row):\n    return row[0]")
        path.write_text(source, encoding="utf-8")
        with self.assertRaisesRegex(picker.ToolChanged, "neither board_order"):
            self.read_loop(db, path)

    def test_board_order_remains_authoritative_without_a_sort_key(self):
        db, path, source = self.loop()
        source = altered(source, "sort_key", "def sort_key_removed(row):\n    return row[0]")
        source = altered(source, "board_order", """def board_order(conn):
    skip = blocked_ids(conn) | parked_ids(conn)
    rows = sorted(open_items(conn), key=lambda row: (row[4], row[0]))
    return [(row, row[0] not in skip) for row in rows]""")
        path.write_text(source, encoding="utf-8")
        result = self.read_loop(db, path)
        self.assertEqual(result["headId"], "1")
        self.assertEqual(result["rankedIds"], ["1", "2"])
        self.assertEqual(result["rankKeys"], {}, "no key is invented for an absent function")
        self.assertNotIn("sort_key", result["policy"]["docs"])

    def test_unrelated_database_is_refused_and_never_migrated(self):
        db = self.root / "other.db"
        with closing(sqlite3.connect(db)) as conn, conn:
            conn.execute("CREATE TABLE unrelated(x)")
        _, path, _ = self.loop()
        with self.assertRaisesRegex(picker.ToolChanged, "no item and dep"):
            self.read_loop(db, path)

    def test_open_items_errors_are_recorded_as_unknown_policy(self):
        db, path, source = self.loop()
        with closing(sqlite3.connect(db)) as conn, conn:
            conn.execute("INSERT INTO item VALUES(5,'Legacy','broken','low',9,NULL)")
        source = altered(source, "open_items", '''def open_items(conn):
    if conn.execute("SELECT 1 FROM item WHERE title='__dashboard_probe__' AND status='broken'").fetchone():
        raise ValueError('legacy status rejected')
    return conn.execute("SELECT id,title,status,severity,priority,parked FROM item WHERE status NOT IN ('done','dropped')").fetchall()''')
        path.write_text(source, encoding="utf-8")
        result = self.read_loop(db, path)
        self.assertIn("broken", result["policy"]["groups"]["unknown"])
        self.assertIn("open/offer probe: ValueError: legacy status rejected", result["policy"]["probes"]["broken"]["errors"])


    def test_blocker_probe_errors_remain_unknown_with_the_actual_error(self):
        db, path, source = self.loop()
        source = altered(source, "open_items", """def open_items(conn):
    if conn.execute("SELECT 1 FROM item WHERE title='__dashboard_probe__blocker' AND status='dropped'").fetchone():
        raise ValueError('legacy blocker rejected')
    return conn.execute("SELECT id,title,status,severity,priority,parked FROM item WHERE status NOT IN ('done','dropped')").fetchall()""")
        path.write_text(source, encoding="utf-8")
        result = self.read_loop(db, path)
        self.assertIn("dropped", result["policy"]["groups"]["unknown"])
        probe = result["policy"]["probes"]["dropped"]
        self.assertTrue(probe["closed"])
        self.assertIsNone(probe["satisfies"])
        self.assertIn("blocker probe: ValueError: legacy blocker rejected", probe["errors"])

    def test_a_status_open_but_neither_offered_nor_started_is_other(self):
        db, path, source = self.loop()
        with closing(sqlite3.connect(db)) as conn, conn:
            conn.execute("INSERT INTO item VALUES(5,'Manual','manual','low',9,NULL)")
        source = altered(source, "board_order", """def board_order(conn):
    skip = blocked_ids(conn) | parked_ids(conn)
    return [(row,row[0] not in skip) for row in sorted(open_items(conn),key=sort_key) if row[2]!='manual']""")
        path.write_text(source, encoding="utf-8")
        result = self.read_loop(db, path)
        self.assertIn("manual", result["policy"]["groups"]["other"])
        self.assertNotIn("manual", result["policy"]["groups"]["unknown"])

    def test_loop_confirmation_without_satisfying_status_with_errors_and_other_rules(self):
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.executescript("CREATE TABLE item(id INT,status TEXT,parked TEXT);INSERT INTO item VALUES(1,'open','held'),(2,'open',NULL)")
            class Order:
                def __init__(self, mode): self.mode = mode
                def pairs(self, conn):
                    if self.mode == "error": raise ValueError("legacy item order failed")
                    return [((1,), self.mode == "yes")]
            for mode, expected in (("yes", True), ("no", False), ("error", False)):
                result = loop_picker.confirm(Order(mode), conn, 1, [], {}, {"done"}, True)
                self.assertEqual(result["verified"], expected)
                if mode == "error": self.assertIn("ValueError: legacy item order failed", result["message"])
            result = loop_picker.confirm(Order("yes"), conn, 1, [2], {2: {}}, set(), True)
            self.assertIn("no status satisfies a blocker", result["message"])
            self.assertEqual(conn.execute("SELECT parked FROM item WHERE id=1").fetchone()[0], "held")



if __name__ == "__main__":
    unittest.main()