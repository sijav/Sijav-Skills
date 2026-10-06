#!/usr/bin/env python3
"""The board. A real database, at .claude/todo.db, local to this project.

  todo                       the whole board
  todo next                  what to do next, and why it was picked
  todo next --area back,ai   the same, only among those areas' tasks

A session's loop file (`.claude/<name>loop<...>.local.md` naming the session) sets
its board and its areas. The tool reads it on every command: `next` offers only
the loop's areas, `--area` can only narrow them, and starting a task outside them
is refused. A session with no loop file works on the nearest board, all areas.
  todo add --title ...       create a task
  todo move SB-003 done      change a status
  todo show SB-003           one task in full

This is the Python half of the skill. `todo.mjs` is the Node half and they are
the same tool: same database, same schema, same commands, same output. Either
one works on a machine that has the other's runtime, which is the whole reason
both exist. They can be run against the same board in any order.

Their agreement is not a promise, it is tested: `test-parity.py` drives a
sequence of commands through BOTH and compares what they print, character for
character. If you change one, change the other and run that.

A task carries nine fields. `add` asks for six: --title, --desc, --why,
--severity, --points and --exit are required, the id is generated, status starts
at backlog, and parent is optional.

Selection rule, the owner's: highest severity, then fewest story points, then
lowest id, and never a task whose parent is unfinished. Anything already in
progress comes first, so work in flight gets finished.

Done is not tested, and tested is not tested by a real user. Each is recorded
on its own, with what proved it:
  todo tested SB-003 --evidence "..."        the area's tests ran and passed
  todo e2e SB-003 --evidence "..."           a real user's path was checked, after tested
  todo untest SB-003 [e2e] --reason "..."    clear it by hand; what it had stays in a note
  todo tests [--area back]                   which done tasks are tested, which are not
A board gains the columns and the guards that keep them honest on its first
`tested` that passes every check, and not before: until then it reads and
prints exactly what it did. A status change, a changed description or exit
condition, and an open finding each clear a test state, keeping what it had
in a note.
"""

import os
import re
import sqlite3
import sys
from datetime import datetime, timezone

# Node writes UTF-8 and a bare \n. Python on Windows writes the locale's code
# page, which cannot encode a Persian title at all, and turns every \n into
# \r\n, so the same board printed 266 more bytes through this half than through
# Node. Both are set here, before anything is printed.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", newline="\n")
    except (AttributeError, ValueError):
        pass

SEVERITIES = ["critical", "high", "medium", "low"]
STATUSES = ["backlog", "in_progress", "wait_for_roast", "done", "dropped"]
POINTS = [1, 2, 3, 5, 8, 13]

# Test states, the owner's: done, tested and tested by a real user are three
# separate facts. They are six columns on `task` that a board gains on its first
# `tested`, never on opening, so a board that does not use them keeps its bytes.
TEST_STATE_COLUMNS = (
    ("tested", "INTEGER NOT NULL DEFAULT 0"),
    ("tested_how", "TEXT"),
    ("tested_at", "TEXT"),
    ("e2e_tested", "INTEGER NOT NULL DEFAULT 0"),
    ("e2e_how", "TEXT"),
    ("e2e_at", "TEXT"),
)

# What counts as blank in a test state's evidence or a reason for clearing one:
# every character either runtime's own trimming treats as space, and the byte
# order mark. Python's strip() and JavaScript's trim() each miss some of the
# other's, so both halves and the board's guards use this one set instead.
BLANK = "\t\n\x0b\x0c\r\x1c\x1d\x1e\x1f \x85\xa0                　﻿"

# The guards live in the board, not in either half, because every writer runs
# them: this tool, the other half, and an older copy of either that has never
# heard of test states. A claim without a done task and what proved it is
# refused whoever writes it, and so is a claim newly made while a finding of
# the task is open; one that already stood is not, so clearing e2e alone still
# works. Whatever changes the story a test proved clears it, keeping what it had
# in a note. None of them refuses a status change, so an older copy moving a
# tested task still works and leaves no stale claim behind. The blank set in
# the guards is BLANK's, by code point. The text is the same, character for
# character, in todo.mjs.
TEST_STATE_TRIGGERS = (
    """CREATE TRIGGER IF NOT EXISTS todo_test_claim BEFORE UPDATE OF tested, tested_how, e2e_tested, e2e_how ON task
WHEN NEW.tested NOT IN (0, 1) OR NEW.e2e_tested NOT IN (0, 1)
  OR (NEW.tested = 1 AND (NEW.status IS NOT 'done' OR trim(coalesce(NEW.tested_how, ''), char(9, 10, 11, 12, 13, 28, 29, 30, 31, 32, 133, 160, 5760, 8192, 8193, 8194, 8195, 8196, 8197, 8198, 8199, 8200, 8201, 8202, 8232, 8233, 8239, 8287, 12288, 65279)) = ''))
  OR (NEW.e2e_tested = 1 AND (NEW.tested IS NOT 1 OR trim(coalesce(NEW.e2e_how, ''), char(9, 10, 11, 12, 13, 28, 29, 30, 31, 32, 133, 160, 5760, 8192, 8193, 8194, 8195, 8196, 8197, 8198, 8199, 8200, 8201, 8202, 8232, 8233, 8239, 8287, 12288, 65279)) = ''))
  OR (((NEW.tested = 1 AND OLD.tested IS NOT 1) OR (NEW.e2e_tested = 1 AND OLD.e2e_tested IS NOT 1))
    AND EXISTS (SELECT 1 FROM task WHERE parent_task = NEW.id AND status NOT IN ('done', 'dropped')))
BEGIN
  SELECT RAISE(ABORT, 'a test state needs a done task with no open finding and what proved it, and e2e needs tested first');
END""",
    """CREATE TRIGGER IF NOT EXISTS todo_test_claim_insert BEFORE INSERT ON task
WHEN NEW.tested NOT IN (0, 1) OR NEW.e2e_tested NOT IN (0, 1)
  OR (NEW.tested = 1 AND (NEW.status IS NOT 'done' OR trim(coalesce(NEW.tested_how, ''), char(9, 10, 11, 12, 13, 28, 29, 30, 31, 32, 133, 160, 5760, 8192, 8193, 8194, 8195, 8196, 8197, 8198, 8199, 8200, 8201, 8202, 8232, 8233, 8239, 8287, 12288, 65279)) = ''))
  OR (NEW.e2e_tested = 1 AND (NEW.tested IS NOT 1 OR trim(coalesce(NEW.e2e_how, ''), char(9, 10, 11, 12, 13, 28, 29, 30, 31, 32, 133, 160, 5760, 8192, 8193, 8194, 8195, 8196, 8197, 8198, 8199, 8200, 8201, 8202, 8232, 8233, 8239, 8287, 12288, 65279)) = ''))
  OR ((NEW.tested = 1 OR NEW.e2e_tested = 1)
    AND EXISTS (SELECT 1 FROM task WHERE parent_task = NEW.id AND status NOT IN ('done', 'dropped')))
BEGIN
  SELECT RAISE(ABORT, 'a test state needs a done task with no open finding and what proved it, and e2e needs tested first');
END""",
    """CREATE TRIGGER IF NOT EXISTS todo_test_status_clears AFTER UPDATE OF status ON task
WHEN OLD.status IS NOT NEW.status
  AND EXISTS (SELECT 1 FROM task WHERE id = NEW.id AND (tested IS NOT 0 OR e2e_tested IS NOT 0))
BEGIN
  INSERT INTO note (task, at, text)
    SELECT id, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'test state cleared: status ' || OLD.status || ' -> ' || NEW.status || '; it had '
      || CASE WHEN tested = 1 THEN 'tested: ' || coalesce(tested_how, '') ELSE 'not tested' END
      || CASE WHEN e2e_tested = 1 THEN '; e2e tested: ' || coalesce(e2e_how, '') ELSE '' END
    FROM task WHERE id = NEW.id;
  UPDATE task SET tested = 0, tested_how = NULL, tested_at = NULL, e2e_tested = 0, e2e_how = NULL, e2e_at = NULL
    WHERE id = NEW.id;
END""",
    """CREATE TRIGGER IF NOT EXISTS todo_test_story_clears AFTER UPDATE OF descr, exit_cond ON task
WHEN (OLD.descr IS NOT NEW.descr OR OLD.exit_cond IS NOT NEW.exit_cond)
  AND EXISTS (SELECT 1 FROM task WHERE id = NEW.id AND (tested IS NOT 0 OR e2e_tested IS NOT 0))
BEGIN
  INSERT INTO note (task, at, text)
    SELECT id, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'test state cleared: '
      || CASE WHEN OLD.descr IS NOT NEW.descr AND OLD.exit_cond IS NOT NEW.exit_cond THEN 'its description and exit condition changed'
        WHEN OLD.descr IS NOT NEW.descr THEN 'its description changed' ELSE 'its exit condition changed' END
      || '; it had '
      || CASE WHEN tested = 1 THEN 'tested: ' || coalesce(tested_how, '') ELSE 'not tested' END
      || CASE WHEN e2e_tested = 1 THEN '; e2e tested: ' || coalesce(e2e_how, '') ELSE '' END
    FROM task WHERE id = NEW.id;
  UPDATE task SET tested = 0, tested_how = NULL, tested_at = NULL, e2e_tested = 0, e2e_how = NULL, e2e_at = NULL
    WHERE id = NEW.id;
END""",
    """CREATE TRIGGER IF NOT EXISTS todo_test_finding_added AFTER INSERT ON task
WHEN NEW.parent_task IS NOT NULL AND NEW.status NOT IN ('done', 'dropped')
  AND EXISTS (SELECT 1 FROM task WHERE id = NEW.parent_task AND (tested IS NOT 0 OR e2e_tested IS NOT 0))
BEGIN
  INSERT INTO note (task, at, text)
    SELECT id, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'test state cleared: ' || NEW.id || ' is an open finding of it; it had '
      || CASE WHEN tested = 1 THEN 'tested: ' || coalesce(tested_how, '') ELSE 'not tested' END
      || CASE WHEN e2e_tested = 1 THEN '; e2e tested: ' || coalesce(e2e_how, '') ELSE '' END
    FROM task WHERE id = NEW.parent_task;
  UPDATE task SET tested = 0, tested_how = NULL, tested_at = NULL, e2e_tested = 0, e2e_how = NULL, e2e_at = NULL
    WHERE id = NEW.parent_task;
END""",
    """CREATE TRIGGER IF NOT EXISTS todo_test_finding_reopened AFTER UPDATE OF status ON task
WHEN NEW.parent_task IS NOT NULL AND OLD.status IN ('done', 'dropped') AND NEW.status NOT IN ('done', 'dropped')
  AND EXISTS (SELECT 1 FROM task WHERE id = NEW.parent_task AND (tested IS NOT 0 OR e2e_tested IS NOT 0))
BEGIN
  INSERT INTO note (task, at, text)
    SELECT id, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'test state cleared: ' || NEW.id || ', a finding of it, is open again; it had '
      || CASE WHEN tested = 1 THEN 'tested: ' || coalesce(tested_how, '') ELSE 'not tested' END
      || CASE WHEN e2e_tested = 1 THEN '; e2e tested: ' || coalesce(e2e_how, '') ELSE '' END
    FROM task WHERE id = NEW.parent_task;
  UPDATE task SET tested = 0, tested_how = NULL, tested_at = NULL, e2e_tested = 0, e2e_how = NULL, e2e_at = NULL
    WHERE id = NEW.parent_task;
END""",
    """CREATE TRIGGER IF NOT EXISTS todo_test_finding_attached AFTER UPDATE OF parent_task ON task
WHEN NEW.parent_task IS NOT NULL AND OLD.parent_task IS NOT NEW.parent_task AND NEW.status NOT IN ('done', 'dropped')
  AND EXISTS (SELECT 1 FROM task WHERE id = NEW.parent_task AND (tested IS NOT 0 OR e2e_tested IS NOT 0))
BEGIN
  INSERT INTO note (task, at, text)
    SELECT id, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'test state cleared: ' || NEW.id || ' was filed under it as an open finding; it had '
      || CASE WHEN tested = 1 THEN 'tested: ' || coalesce(tested_how, '') ELSE 'not tested' END
      || CASE WHEN e2e_tested = 1 THEN '; e2e tested: ' || coalesce(e2e_how, '') ELSE '' END
    FROM task WHERE id = NEW.parent_task;
  UPDATE task SET tested = 0, tested_how = NULL, tested_at = NULL, e2e_tested = 0, e2e_how = NULL, e2e_at = NULL
    WHERE id = NEW.parent_task;
END""",
)
TEST_STATE_TRIGGER_NAMES = (
    "todo_test_claim",
    "todo_test_claim_insert",
    "todo_test_status_clears",
    "todo_test_story_clears",
    "todo_test_finding_added",
    "todo_test_finding_reopened",
    "todo_test_finding_attached",
)


def find_board(start):
    """The board that already EXISTS, found by walking up.

    A board is what says which board to use. Not a .git directory, and above
    all not a package.json: every member of a workspace has one, so the old
    rule stopped at apps/web and made a SECOND board two levels below the real
    one. That happened three times in one session, and one of those runs said
    "Nothing left", which is the loop's completion condition, about a database
    it had invented one line earlier.
    """
    directory = os.path.abspath(start)
    while True:
        candidate = os.path.join(directory, ".claude", "todo.db")
        if os.path.exists(candidate):
            return candidate
        parent = os.path.dirname(directory)
        if parent == directory:
            return None
        directory = parent


def front_matter(path):
    """A loop file's front matter, the `key: value` lines between its first two `---`."""
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    fields = {}
    for line in text[3:end if end != -1 else 0].splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip():
            fields[key.strip()] = value.strip().strip("\"'")
    return fields


def session_loop(start):
    """The loop file of the session running this command, found walking up.

    A loop is per session. Its file, `.claude/<name>loop<...>.local.md`, names its
    session and may name its board and its areas. Claude Code and Codex give every
    command the session's id; without one, or with no loop file naming it, there is
    no loop and nothing changes. Returns (project root, loop file, fields) or None.
    """
    session = os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get("CODEX_SESSION_ID") or ""
    if not session:
        return None
    directory = os.path.abspath(start)
    while True:
        folder = os.path.join(directory, ".claude")
        if os.path.isdir(folder):
            for name in sorted(os.listdir(folder)):
                if "loop" in name and name.endswith(".local.md"):
                    fields = front_matter(os.path.join(folder, name))
                    if fields.get("session") == session:
                        return directory, os.path.join(folder, name), fields
        parent = os.path.dirname(directory)
        if parent == directory:
            return None
        directory = parent


def board_of(start):
    """The board of this session's loop when its loop file names one, else the nearest board."""
    loop = session_loop(start)
    if loop and loop[2].get("board"):
        return os.path.normpath(os.path.join(loop[0], loop[2]["board"]))
    return find_board(start)


_argv = sys.argv[1:]
_command = _argv[0] if _argv else "list"

# `init` is the ONLY thing that creates a board, and it does not guess where.
# An init inheriting the old nearest-project rule would just make the accident
# opt-in through a badly scoped command.
if _command == "init":
    _rest = _argv[1:]
    _target = os.getcwd() if _rest[:1] == ["--here"] else (_rest[0] if _rest else None)
    if not _target:
        print(
            "todo init needs to be told where: `todo init --here`, or `todo init <path>`.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    _made = os.path.join(os.path.abspath(_target), ".claude", "todo.db")
    if os.path.exists(_made):
        print(f"There is already a board at {_made}", file=sys.stderr)
        raise SystemExit(2)
    os.makedirs(os.path.dirname(_made), exist_ok=True)
    sqlite3.connect(_made).close()
    print(f"Started a new board at {_made}", file=sys.stderr)
    raise SystemExit(0)

BOARD = board_of(os.getcwd())

# Refuse BEFORE makedirs and before sqlite3.connect. Both create what is
# missing, so a check placed after either is not a check.
if BOARD is None:
    print(
        f"No board. Nothing at or above {os.getcwd()} has .claude/todo.db.",
        file=sys.stderr,
    )
    print("If this project should have one: todo init --here", file=sys.stderr)
    raise SystemExit(2)

# A board the loop file names is never created either.
if not os.path.exists(BOARD):
    print(f"This session's loop names the board {BOARD}, which does not exist. Nothing was created.", file=sys.stderr)
    raise SystemExit(2)

# Two processes on one board are normal. Five seconds of waiting for a lock is
# sqlite3's default; it is written out so the Node half, whose default is zero,
# visibly matches it.
db = sqlite3.connect(BOARD, timeout=5.0)
db.row_factory = sqlite3.Row

# A board is only ever created by `init` now, which says so itself, so there is
# nothing to announce here.

db.executescript(
    """
  CREATE TABLE IF NOT EXISTS task (
    id        TEXT PRIMARY KEY,
    title     TEXT NOT NULL,
    descr     TEXT NOT NULL,
    why       TEXT NOT NULL,
    severity  TEXT NOT NULL CHECK (severity IN ('critical','high','medium','low')),
    points    INTEGER NOT NULL CHECK (points IN (1,2,3,5,8,13)),
    status    TEXT NOT NULL CHECK (status IN ('backlog','in_progress','wait_for_roast','done','dropped')),
    exit_cond TEXT NOT NULL,
    area      TEXT,
    created   TEXT NOT NULL DEFAULT (datetime('now'))
  );
  CREATE TABLE IF NOT EXISTS blocked_by (
    task    TEXT NOT NULL REFERENCES task(id) ON DELETE CASCADE,
    parent  TEXT NOT NULL,
    PRIMARY KEY (task, parent)
  );
"""
)

# A board created before a column existed does not get it from CREATE TABLE IF
# NOT EXISTS, which does nothing when the table is already there. SQLite has no
# ADD COLUMN IF NOT EXISTS, so add it and ignore the error when it is present.
for column in ("area TEXT", "parent_task TEXT"):
    try:
        db.execute(f"ALTER TABLE task ADD COLUMN {column}")
    except sqlite3.OperationalError:
        pass

# Phases, the owner's of 2026-09-11: the board's tasks belong to a goal, and the
# first goal here is the MVP. A phase is that goal with an order, so the board can
# say what is left before the thing ships rather than only what is left in total.
#
# The CURRENT phase is the first one still open. `next` works through it before
# it offers anything from a later one, and a task with no phase counts as current
# work: an unsorted card is something to do now, and the opposite reading would
# hide it behind every later phase.
#
# A board with no phases behaves exactly as it did.
db.executescript(
    """
  CREATE TABLE IF NOT EXISTS phase (
    name     TEXT PRIMARY KEY,
    goal     TEXT NOT NULL,
    position INTEGER NOT NULL,
    status   TEXT NOT NULL CHECK (status IN ('open','done')) DEFAULT 'open'
  );
"""
)
try:
    db.execute("ALTER TABLE task ADD COLUMN phase TEXT")
except sqlite3.OperationalError:
    pass

# What an older JSON board tool carried and this one did not: an
# objective's name, a block and its reason, notes, what closed a task, and the
# roast rounds with what their adjudication filed.
#
# Every one is a new table or a new column, never a changed one, because every
# project's board opens with this code: a board made before them gains them on
# its first run and prints exactly what it printed until something uses them. A
# block is a row of its own rather than a status for the same reason: the task
# table's status CHECK cannot be widened without rebuilding the table on every
# board that already exists.
for statement in (
    "ALTER TABLE phase ADD COLUMN label TEXT",
    "ALTER TABLE task ADD COLUMN evidence TEXT",
    "ALTER TABLE task ADD COLUMN closed TEXT",
    "ALTER TABLE task ADD COLUMN reason TEXT",
    "ALTER TABLE task ADD COLUMN updated TEXT",
):
    try:
        db.execute(statement)
    except sqlite3.OperationalError:
        pass
db.executescript(
    """
  CREATE TABLE IF NOT EXISTS blocked (
    task   TEXT PRIMARY KEY REFERENCES task(id) ON DELETE CASCADE,
    reason TEXT NOT NULL,
    since  TEXT NOT NULL
  );
  CREATE TABLE IF NOT EXISTS note (
    task TEXT NOT NULL REFERENCES task(id) ON DELETE CASCADE,
    at   TEXT NOT NULL,
    text TEXT NOT NULL
  );
  CREATE TABLE IF NOT EXISTS roast (
    task               TEXT NOT NULL REFERENCES task(id) ON DELETE CASCADE,
    round              INTEGER NOT NULL,
    at                 TEXT NOT NULL,
    file               TEXT NOT NULL,
    score              REAL,
    criticals          INTEGER,
    filed              TEXT,
    dismissed          TEXT,
    reviewer_score     REAL,
    reviewer_criticals INTEGER,
    head               TEXT,
    card_digest        TEXT,
    legacy             TEXT,
    PRIMARY KEY (task, round)
  );
"""
)
db.commit()


def fail(message):
    print(message, file=sys.stderr)
    sys.exit(1)


def now():
    """A moment, the way every new column records one, and the way Node writes it."""
    moment = datetime.now(timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def task_columns():
    return [row["name"] for row in db.execute("PRAGMA table_info(task)").fetchall()]


def has_test_states():
    """Whether this board records test states at all. One that never did prints what it always printed."""
    present = task_columns()
    return all(name in present for name, _kind in TEST_STATE_COLUMNS)


def records_tests(task):
    return all(name in task for name, _kind in TEST_STATE_COLUMNS)


def is_tested(task):
    return bool(task.get("tested") or task.get("e2e_tested"))


def tested_task(task_id):
    """Whether a task holds a test state now, read around a change that may clear it."""
    row = db.execute("SELECT * FROM task WHERE id = ?", (task_id,)).fetchone() if task_id else None
    return row is not None and is_tested(dict(row))


def had(task):
    """What a test state held, in the words the board's own guards use for it."""
    text = f"tested: {task['tested_how'] or ''}" if task["tested"] == 1 else "not tested"
    return text + (f"; e2e tested: {task['e2e_how'] or ''}" if task["e2e_tested"] == 1 else "")


def test_word(task, flag, how):
    return f"yes, {task[how]}" if task[flag] == 1 else "no"


def start_test_states():
    """Give this board whatever test-state columns and guards it lacks, inside the caller's transaction.

    Only a `tested` that has passed every check calls this. Opening a board,
    reading one or a refused claim never does, so an old board keeps its bytes
    until something on it is actually tested. Returns whether columns were added.
    """
    present = task_columns()
    missing = [(name, kind) for name, kind in TEST_STATE_COLUMNS if name not in present]
    for name, kind in missing:
        db.execute(f"ALTER TABLE task ADD COLUMN {name} {kind}")
    for statement in TEST_STATE_TRIGGERS:
        db.execute(statement)
    return bool(missing)


def give_up(message):
    """Roll back what this command began, close the board, and fail in the tool's own words.

    There may be nothing to roll back: a BEGIN IMMEDIATE that never went
    through, or a transaction SQLite ended itself on an error (a full disk, an
    I/O error, an interrupt, RAISE(ROLLBACK)). ROLLBACK then fails, and only
    that one statement's error is set aside: `message` already holds the error
    that stopped the command, which is the one to report, and the board is
    closed once.
    """
    try:
        db.execute("ROLLBACK")
    except sqlite3.Error:
        pass  # nothing left to roll back; the original error is reported below
    db.close()
    fail(message)


def claim_problem(task_id, command):
    """Why `task_id` cannot be claimed tested, or e2e tested, now; None when it can.

    Asked once, inside the claim's own BEGIN IMMEDIATE, so nothing another
    session writes can land between the check and the claim: a finding filed a
    moment earlier is seen, and one filed a moment later waits for the claim.
    """
    row = db.execute("SELECT * FROM task WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        return f"No task {task_id}."
    task = dict(row)
    if task["status"] != "done":
        return f"{task_id} is {task['status']}, not done. Nothing was recorded."
    reason = block_of(task_id)
    if reason is not None:
        return f"{task_id} is blocked: {reason}. Nothing was recorded."
    still_open = open_children(task_id)
    if still_open:
        return f"{task_id} has open findings: {', '.join(child['id'] for child in still_open)}. Nothing was recorded."
    if command == "e2e" and task.get("tested") != 1:
        return f"{task_id} is not tested yet, and e2e comes after it. Nothing was recorded."
    return None


def all_phases():
    return [dict(row) for row in db.execute("SELECT * FROM phase ORDER BY position, name").fetchall()]


def current_phase():
    """The first phase still open: what the board is working on now."""
    for phase in all_phases():
        if phase["status"] == "open":
            return phase
    return None


def phase_rank(task):
    """Where a task sits in the order of phases.

    The current phase and no phase at all are both now; a phase already closed
    is behind us, so a task left in one is now as well; anything later sorts by
    its position.
    """
    phases = all_phases()
    if not phases:
        return 0
    current = current_phase()
    mine = next((phase for phase in phases if phase["name"] == task.get("phase")), None)
    if mine is None or mine["status"] == "done":
        return 0
    current_position = current["position"] if current else mine["position"]
    return 0 if mine["position"] <= current_position else mine["position"]


def phase_exists(name):
    return db.execute("SELECT 1 FROM phase WHERE name = ?", (name,)).fetchone() is not None


def parents(task_id):
    rows = db.execute("SELECT parent FROM blocked_by WHERE task = ?", (task_id,)).fetchall()
    return [row["parent"] for row in rows]


def with_parents(row):
    task = dict(row)
    task["parents"] = parents(task["id"])
    return task


def all_tasks():
    return [with_parents(row) for row in db.execute("SELECT * FROM task ORDER BY id").fetchall()]


def one(task_id):
    row = db.execute("SELECT * FROM task WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        fail(f"No task {task_id}.")
    return with_parents(row)


def block_of(task_id):
    """Why a task is blocked, or None when it is not.

    The one question `next`, `list`, `show`, `render` and `validate` all ask, so
    they all ask it here: a block lives beside the status, which keeps reading
    backlog, and a reader that looked only at the status would hand a blocked
    task out.
    """
    row = db.execute("SELECT reason FROM blocked WHERE task = ?", (task_id,)).fetchone()
    return row["reason"] if row else None


def is_blocked(task):
    return block_of(task["id"]) is not None


def notes_of(task_id):
    rows = db.execute("SELECT text FROM note WHERE task = ? ORDER BY rowid", (task_id,)).fetchall()
    return [row["text"] for row in rows]


def roasts_of(task_id):
    return [dict(row) for row in db.execute("SELECT * FROM roast WHERE task = ? ORDER BY round", (task_id,)).fetchall()]


def children(task_id):
    """Everything that came out of roasting this task."""
    rows = db.execute("SELECT * FROM task WHERE parent_task = ? ORDER BY id", (task_id,)).fetchall()
    return [dict(row) for row in rows]


def open_children(task_id):
    """The children that are still going to need doing."""
    return [task for task in children(task_id) if task["status"] not in ("done", "dropped")]


def resolve_parent(wanted):
    """Keeps the tree one level deep without refusing anything.

    Naming a child as a parent attaches to that child's parent instead, and says
    so. Refusing would be a gate, and the owner's rule is that the tool does not
    stop you; flattening gets the same shape and tells you what it did.
    """
    if not wanted:
        return None
    row = db.execute("SELECT id, parent_task FROM task WHERE id = ?", (wanted,)).fetchone()
    if row is None:
        fail(f"No task {wanted} to hang this off.")
    if not row["parent_task"]:
        return row["id"]
    print(
        f"{wanted} is itself a child of {row['parent_task']}, so this hangs off "
        f"{row['parent_task']}. One level."
    )
    return row["parent_task"]


def trailing_digits(task_id):
    """An id split into what comes before the ASCII digits it ends with, and those digits ("" when it ends otherwise)."""
    text = str(task_id)
    start = len(text)
    while start > 0 and "0" <= text[start - 1] <= "9":
        start -= 1
    return text[:start], text[start:]


def digit_order(digits):
    """A digit string's place in integer order, without converting it: its length without leading zeros, then itself."""
    significant = digits.lstrip("0")
    return (len(significant), significant)


def one_more(digits):
    """A digit string plus one, in decimal, without its leading zeros."""
    out, carry = [], 1
    for character in reversed(digits.lstrip("0") or "0"):
        value = ord(character) - ord("0") + carry
        out.append(chr(ord("0") + value % 10))
        carry = value // 10
    if carry:
        out.append("1")
    return "".join(reversed(out))


def next_id():
    """The id after the highest one, in the board's own prefix.

    It used to be one fixed prefix whatever the board was, so a plain `add` on a
    board with another prefix got the wrong one. An empty board has no prefix to keep, so it takes the capitals of the
    project folder's name, or its first two letters when it has fewer than two.

    The number is the run of ASCII digits the id ends with, at its very end,
    worked as a digit string and never converted: no length is too long, and
    the Node half takes the same run and makes the same next id.
    """
    best = None
    for row in db.execute("SELECT id FROM task ORDER BY rowid").fetchall():
        prefix, digits = trailing_digits(row["id"])
        if digits and (best is None or digit_order(digits) > digit_order(best[1])):
            best = (prefix, digits)
    if best is not None:
        return f"{best[0]}{one_more(best[1]).rjust(3, '0')}"
    project = os.path.basename(os.path.dirname(os.path.dirname(BOARD)))
    capitals = "".join(re.findall(r"[A-Z]", project))
    prefix = capitals if len(capitals) >= 2 else project[:2].upper()
    return f"{prefix}-001"


def read_flags(args):
    out = {}
    for index in range(0, len(args) - 1, 2):
        out[args[index].lstrip("-")] = args[index + 1]
    return out


def value_of(items, name):
    """The value after a flag, wherever it sits.

    `read_flags` reads pairs, so a flag that takes no value, `--force` or
    `--check`, shifts every pair after it by one and the next flag's value is
    lost. Commands that have one read their values here.
    """
    flag = f"--{name}"
    if flag not in items:
        return None
    at = items.index(flag)
    return items[at + 1] if at + 1 < len(items) else None


def area_filter(value):
    """The areas a session works in, from `--area`: one name or a comma list.

    None means every area: no `--area`, an empty one, or `all` among the names.
    `unset` names the tasks that have no area, as `list` prints them.
    """
    if value is None:
        return None
    names = [name.strip() for name in str(value).split(",") if name.strip()]
    return None if not names or "all" in names else names


def in_areas(task, areas):
    return areas is None or (task["area"] or "unset") in areas


def loop_areas():
    """This session's loop and its areas, or (None, None) when it has no loop file or no areas."""
    loop = session_loop(os.getcwd())
    return (loop, area_filter(loop[2].get("areas"))) if loop else (None, None)


def areas_for(asked):
    """The areas `next` picks from: the session's loop areas, always, narrowed by `--area`."""
    asked = area_filter(asked)
    loop, mine = loop_areas()
    if mine is None:
        return asked
    if asked is None:
        return mine
    outside = [name for name in asked if name not in mine]
    if outside:
        fail(f"{', '.join(outside)}: not one of this session's areas ({', '.join(mine)}), set in {loop[1]}.")
    return asked


def number_from(value):
    """A number from a flag, or None when it is not one."""
    if value is None or str(value).strip() == "":
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return int(number) if number.is_integer() else number


def shown(value):
    """A number the way JavaScript prints one: 8 rather than 8.0."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def round_line(roast_round):
    line = f"    round {roast_round['round']}: {roast_round['file']}"
    if roast_round["score"] is not None:
        line += f", score {shown(roast_round['score'])}"
    if roast_round["criticals"] is not None:
        line += f", {shown(roast_round['criticals'])} critical(s)"
    if roast_round["filed"] is None:
        line += ", not judged yet"
    elif roast_round["filed"] == "":
        line += ", filed none"
    else:
        line += f", filed {roast_round['filed']}"
    if roast_round["dismissed"]:
        line += f", dismissed: {roast_round['dismissed']}"
    return line


def number_text(value):
    """A stored number as both ports print it. An integral float (what a legacy REAL column returns,
    2.0 for 2) is shown as the integer while it is at most 2**53, where every integer is exact in
    both; other values are shown as Python prints them."""
    if type(value) is float and value.is_integer() and abs(value) <= 2 ** 53:
        return str(int(value))
    return value


def rank_field(value, kind):
    """Keep stored ranking fields readable, with the same labels in both ports: NULL, a BLOB as
    <non-text> (severity) or <non-number> (points), and a number as number_text shows it."""
    if value is None:
        return "NULL"
    if isinstance(value, bytes):
        return "<non-text>" if kind == "severity" else "<non-number>"
    return number_text(value)


def card(task):
    area = f"  {task['area']}" if task["area"] else ""
    label = None
    if task.get("phase"):
        label = next((phase["label"] for phase in all_phases() if phase["name"] == task["phase"]), None)
    reason = block_of(task["id"])
    notes = notes_of(task["id"])
    rounds = roasts_of(task["id"])
    lines = [
        f"{task['id']}  [{rank_field(task['severity'], 'severity')}/{rank_field(task['points'], 'points')}pt]  {task['status']}{area}",
        f"  {task['title']}",
        "",
        f"  area : {task['area'] if task['area'] else 'unset'}",
    ]
    if all_phases():
        phase_shown = task["phase"] if task.get("phase") is not None else "unset, so it counts as now"
        lines.append(f"  phase: {phase_shown}{' ' + label if label else ''}")
    lines += [
        f"  desc : {task['descr']}",
        f"  why  : {task['why']}",
        f"  exit : {task['exit_cond']}",
        f"  after: {', '.join(task['parents'])}" if task["parents"] else "  after: nothing",
    ]
    # Provenance, shown separately from blockers, because they mean different
    # things and reading a finding as a blocker is how a closed parent starts
    # holding its own findings hostage.
    if task.get("parent_task"):
        lines.append(f"  from : {task['parent_task']}, which was roasted and turned this up")
    own = children(task["id"])
    if own:
        still_open = [child for child in own if child["status"] not in ("done", "dropped")]
        lines.append(f"  parts: {', '.join(child['id'] for child in own)}")
        lines.append(
            f"         {len(still_open)} still open, so this is not finished yet"
            if still_open
            else "         all finished, so roast this task together with them"
        )
    if reason is not None:
        lines.append(f"  blocked: {reason}")
    if task.get("evidence"):
        lines.append(f"  evidence: {task['evidence']}")
    if records_tests(task):
        lines.append(f"  tested: {test_word(task, 'tested', 'tested_how')}")
        lines.append(f"  e2e   : {test_word(task, 'e2e_tested', 'e2e_how')}")
    if task.get("reason"):
        lines.append(f"  dropped: {task['reason']}")
    if notes:
        lines.append("  notes:")
        lines += [f"    - {text}" for text in notes]
    if rounds:
        lines.append("  roasts:")
        lines += [round_line(roast_round) for roast_round in rounds]
    return "\n".join(lines)


def phase_report():
    """What each phase has left, which is the only number an objective is read for."""
    phases = all_phases()
    if not phases:
        return []
    tasks = all_tasks()
    current = current_phase()
    lines = ["PHASES"]
    for phase in phases:
        mine = [task for task in tasks if task.get("phase") == phase["name"]]
        still_open = len([task for task in mine if task["status"] not in ("done", "dropped")])
        done = len([task for task in mine if task["status"] == "done"])
        if phase["status"] == "done":
            mark = "finished"
        elif current is not None and phase["name"] == current["name"]:
            mark = "now"
        else:
            mark = "later"
        lines.append(f"  {phase['position']}. {phase['name']} ({mark}): {still_open} open, {done} done")
        lines.append(f"     {phase['goal']}")
    loose = len([task for task in tasks if not task.get("phase") and task["status"] not in ("done", "dropped")])
    if loose:
        lines.append(f"  {loose} open task(s) in no phase, which counts as now.")
    return lines


def okr_report():
    """The objectives, the owner's word for phases, 2026-09-12.

    The same rows, each with the name an older board gave it, what is left in
    it, and the order they are met in.
    """
    phases = all_phases()
    if not phases:
        return ['No objectives yet. Add one: todo okr add --name "..." --description "..."']
    tasks = all_tasks()
    current = current_phase()
    lines = []
    for phase in phases:
        mine = [task for task in tasks if task.get("phase") == phase["name"]]
        still_open = len([task for task in mine if task["status"] not in ("done", "dropped")])
        done = len([task for task in mine if task["status"] == "done"])
        if phase["status"] == "done":
            state = "met"
        elif current is not None and phase["name"] == current["name"]:
            state = "now"
        else:
            state = "later"
        named = f" {phase['label']}" if phase["label"] else ""
        lines.append(
            f"{phase['position']}. {phase['name']}{named} ({state}): {still_open} open, {done} done, {len(mine)} in all"
        )
        lines.append(f"   {phase['goal']}")
    loose = len([task for task in tasks if not task.get("phase") and task["status"] not in ("done", "dropped")])
    if loose:
        lines.append(f"{loose} open task(s) serve no objective, which counts as now.")
    return lines


def id_number(task_id):
    # Ids sort numerically, not as text: SB-1000 before SB-999 is correct today
    # and wrong from the thousandth task, which is the worst kind of bug,
    # invisible until a boundary and then quietly reordering the work.
    #
    # The number is every ASCII digit of the id, joined, compared as a digit
    # string, never converted: (its length without leading zeros, the digits).
    # That is the exact integer order at any length. Converting lost it: past
    # 2**53 in the Node half, and past Python's own limit on long integer
    # strings here. Other digits, Arabic-Indic or fullwidth, are not part of
    # the number in either half, and a character `isdigit` accepts but `int`
    # refuses, a superscript two, can no longer stop `next`.
    digits = "".join(character for character in str(task_id) if "0" <= character <= "9").lstrip("0")
    return (len(digits), digits)


class UnrankableSeverity(ValueError):
    """An actual ranking candidate has no supported severity."""


class UnrankablePoints(ValueError):
    """An actual ranking candidate has no supported points value."""


def severity_problem(task):
    value = task["severity"]
    label = value if isinstance(value, str) else ("NULL" if value is None else "<non-text>")
    return None if value in SEVERITIES else (
        f"{task['id']}: its severity {label} is not one of {', '.join(SEVERITIES)}"
    )


def points_problem(task):
    value = task["points"]
    numeric = type(value) in (int, float)
    label = value if isinstance(value, str) else (
        "NULL" if value is None else "<unsupported number>" if numeric else "<non-number>"
    )
    return None if numeric and value in POINTS else (
        f"{task['id']}: its points {label} is not one of {', '.join(str(point) for point in POINTS)}"
    )


def severity_rank(task):
    problem = severity_problem(task)
    if problem is not None:
        raise UnrankableSeverity(problem)
    return SEVERITIES.index(task["severity"])


def by_rule(task):
    # The phase comes first: what the product needs to ship is picked before
    # what comes after it, however severe the later one is.
    return (
        phase_rank(task),
        SEVERITIES.index(task["severity"]),
        task["points"],
        id_number(task["id"]),
        task["id"],
    )


def by_severity(task):
    return (SEVERITIES.index(task["severity"]), task["points"], id_number(task["id"]), task["id"])


def ranked(tasks, key):
    """Validate each filtered candidate in stored id order before sorting."""
    for task in tasks:
        severity_rank(task)
        problem = points_problem(task)
        if problem is not None:
            raise UnrankablePoints(problem)
    return sorted(tasks, key=key)


def ranked_call(action):
    try:
        return action()
    except (UnrankableSeverity, UnrankablePoints) as error:
        fail(str(error))


def choose(tasks, areas=None):
    """What `next` would pick, for `next` and for the rendered board alike.

    Work in flight is finished before anything new starts, and among several
    started tasks the same rule decides which. A blocked task is neither, and
    neither is a started task one of whose parents is unfinished: a parent added
    after it started means it cannot go on until that parent is done.
    With `areas` only those areas' tasks are picked; a parent in another area
    still counts, so every task stays in `done`.
    """
    done = {task["id"] for task in tasks if task["status"] == "done"}
    started = ranked(
        [
            task
            for task in tasks
            if task["status"] in ("in_progress", "wait_for_roast") and not is_blocked(task)
            and in_areas(task, areas)
            and all(parent in done for parent in task["parents"])
        ],
        key=by_rule,
    )
    eligible = ranked(
        [
            task
            for task in tasks
            if task["status"] == "backlog"
            and in_areas(task, areas)
            and not is_blocked(task)
            and all(parent in done for parent in task["parents"])
        ],
        key=by_rule,
    )
    pick = started[0] if started else (eligible[0] if eligible else None)
    return started, pick


def unfinished_parents(task, tasks):
    """Each parent of a task that is not done, with its status, or saying it is not on the board."""
    status = {other["id"]: other["status"] for other in tasks}
    return [
        f"{parent} ({status[parent]})" if parent in status else f"{parent}, which is not on this board"
        for parent in sorted(task["parents"])
        if status.get(parent) != "done"
    ]


def started_but_waiting(tasks, areas=None):
    """The started, unblocked tasks `choose` passes over because a parent is unfinished, in its order."""
    return ranked(
        [
            task
            for task in tasks
            if task["status"] in ("in_progress", "wait_for_roast") and not is_blocked(task)
            and in_areas(task, areas) and unfinished_parents(task, tasks)
        ],
        key=by_rule,
    )


def say_held(held, tasks):
    """What `next` says about started work it does not resume, and why: a parent dropped or gone never becomes done."""
    for task in held:
        waits = ", ".join(unfinished_parents(task, tasks))
        print(f"  {task['id']} is {task['status']} and waits on {waits}; next does not resume it until every parent is done.")


def escape_cell(text):
    return re.sub(r"\r?\n", " ", str(text if text is not None else "").replace("|", "\\|"))


def render_board():
    """The board as Markdown, which is what a person reads and what a diff of it shows."""
    tasks = all_tasks()
    project = os.path.basename(os.path.dirname(os.path.dirname(BOARD)))
    done = [task for task in tasks if task["status"] == "done"]
    # An unranked old status may still be displayed. Never coerce its invalid
    # points into a total. Numeric values remain summable independently of their
    # ranking eligibility; actual ranked candidates refuse in their group order.
    points = sum(task["points"] for task in tasks) if all(type(task["points"]) in (int, float) for task in tasks) else "unknown"
    done_points = sum(task["points"] for task in done) if all(type(task["points"]) in (int, float) for task in done) else "unknown"
    out = [
        "# Board",
        "",
        "<!-- GENERATED by the todo skill from .claude/todo.db. Change the board through the skill, never this file. -->",
        "",
        f"Project **{project}** · {len(done)} of {len(tasks)} tasks done · {number_text(done_points)} of {number_text(points)} points.",
        "",
    ]
    _, pick = choose(tasks)
    if pick is not None:
        area = f", {pick['area']}" if pick["area"] else ""
        out.append(f"**Next up: `{pick['id']}` {pick['title']}** ({pick['severity']}, {number_text(pick['points'])} pt{area})")
    else:
        out.append("**Nothing is pickable.**")
    out.append("")

    phases = all_phases()
    if phases:
        current = current_phase()
        out += ["## Objectives", "", "| position | id | name | state | open | done |", "| -- | -- | ---- | ----- | ---- | ---- |"]
        for phase in phases:
            mine = [task for task in tasks if task.get("phase") == phase["name"]]
            still_open = len([task for task in mine if task["status"] not in ("done", "dropped")])
            closed = len([task for task in mine if task["status"] == "done"])
            if phase["status"] == "done":
                state = "met"
            elif current is not None and phase["name"] == current["name"]:
                state = "now"
            else:
                state = "later"
            out.append(
                f"| {phase['position']} | {escape_cell(phase['name'])} | {escape_cell(phase['label'])} | {state} | {still_open} | {closed} |"
            )
        out.append("")

    columns = [
        ("in_progress", "In progress"),
        ("wait_for_roast", "Waiting for a roast"),
        ("blocked", "Blocked"),
        ("backlog", "Backlog"),
        ("done", "Done"),
        ("dropped", "Dropped"),
    ]
    for key, heading in columns:
        if key == "blocked":
            column = [task for task in tasks if is_blocked(task)]
        else:
            column = [task for task in tasks if task["status"] == key and not is_blocked(task)]
        column = ranked(column, key=by_severity)
        if not column:
            continue
        out += [f"## {heading} ({len(column)})", ""]
        out.append("| id | title | sev | pt | area | blocked by | exit condition |")
        out.append("| -- | ----- | --- | -- | ---- | ---------- | -------------- |")
        for task in column:
            out.append(
                f"| `{task['id']}` | {escape_cell(task['title'])} | {task['severity']} | {number_text(task['points'])} | "
                f"{escape_cell(task['area'])} | {', '.join(task['parents']) or 'none'} | {escape_cell(task['exit_cond'])} |"
            )
        out.append("")

    out += ["## Cards", ""]
    for task in tasks:
        out += [f"### `{task['id']}` {task['title']}", ""]
        area = task["area"] if task["area"] is not None else "unset"
        objective = f" · **objective** {task['phase']}" if task.get("phase") else ""
        out.append(
            f"- **status** {task['status']} · **severity** {rank_field(task['severity'], 'severity')} · **points** {rank_field(task['points'], 'points')} · **area** {area}{objective}"
        )
        out.append(f"- **blocked by** {', '.join(task['parents']) or 'none'}")
        reason = block_of(task["id"])
        if reason is not None:
            out.append(f"- **blocked** {reason}")
        if task.get("parent_task"):
            out.append(f"- **came out of** {task['parent_task']}")
        out += ["", task["descr"], "", f"**Why.** {task['why']}", "", f"**Exit condition.** {task['exit_cond']}", ""]
        if task.get("evidence"):
            out += [f"**Evidence.** {task['evidence']}", ""]
        if records_tests(task):
            out += [f"**Tested.** {test_word(task, 'tested', 'tested_how')}", ""]
            out += [f"**E2E tested.** {test_word(task, 'e2e_tested', 'e2e_how')}", ""]
        if task.get("reason"):
            out += [f"**Dropped because.** {task['reason']}", ""]
        notes = notes_of(task["id"])
        if notes:
            out += ["**Notes.**", ""] + [f"- {text}" for text in notes] + [""]
        rounds = roasts_of(task["id"])
        if rounds:
            out += ["**Roasts.**", ""] + ["-" + round_line(roast_round)[3:] for roast_round in rounds] + [""]
    return "\n".join(out) + "\n"


argv = sys.argv[1:]
command = argv[0] if argv else "list"
args = argv[1:]

if command == "phase":
    action = args[0] if len(args) > 0 else None
    name = args[1] if len(args) > 1 else None
    given = read_flags(args[2:])
    if not action or action == "list":
        lines = phase_report()
        print("\n".join(lines) if lines else 'No phases. Add one: todo phase add MVP --goal "..."')
    elif action == "add":
        if not name or not given.get("goal"):
            fail('A phase needs a name and what it is for: todo phase add MVP --goal "..."')
        if db.execute("SELECT 1 FROM phase WHERE name = ?", (name,)).fetchone():
            fail(f"There is already a phase called {name}.")
        last = db.execute("SELECT MAX(position) AS at FROM phase").fetchone()["at"] or 0
        position = int(given["position"]) if given.get("position") else last + 1
        db.execute(
            "INSERT INTO phase (name, goal, position, status) VALUES (?,?,?,?)",
            (name, given["goal"], position, "open"),
        )
        db.commit()
        print(f"Added phase {position}. {name}")
    elif action in ("done", "open"):
        if not db.execute("SELECT 1 FROM phase WHERE name = ?", (name,)).fetchone():
            fail(f"No phase called {name if name is not None else 'undefined'}.")
        db.execute("UPDATE phase SET status = ? WHERE name = ?", (action, name))
        db.commit()
        print(f"Phase {name} is {action}.")
        print("\n".join(phase_report()))
    else:
        fail(
            f'Unknown phase command "{action}". Try: todo phase, todo phase add <name> --goal "...",'
            " todo phase done <name>."
        )

elif command == "okr":
    # An objective is a phase: its id is the phase's name, its name the label and
    # its description the goal, so `phase` and `okr` read and write the same rows
    # and a board that only ever used one of them keeps working.
    action = args[0] if args else None
    if not action or action == "list":
        print("\n".join(okr_report()))
    elif action == "add":
        given = read_flags(args[1:])
        if not given.get("name") or not given.get("description"):
            fail("okr add needs --name and --description: an objective nobody can read is a label.")
        phases = all_phases()
        okr_id = given["id"] if "id" in given else f"OKR-{len(phases) + 1}"
        if any(phase["name"] == okr_id for phase in phases):
            fail(f"okr add: {okr_id} already exists.")
        if any(phase["label"] == given["name"] for phase in phases):
            fail(f"okr add: there is already an objective called {given['name']}.")
        if "position" in given:
            position = number_from(given["position"])
        else:
            position = max([0] + [phase["position"] for phase in phases]) + 1
        if position is None or not isinstance(position, int) or position < 1:
            fail("okr add: --position is a whole number from 1, the order they are met in.")
        taken = next((phase for phase in phases if phase["position"] == position), None)
        if taken is not None:
            fail(f"okr add: position {position} is taken by {taken['name']}.")
        db.execute(
            "INSERT INTO phase (name, goal, position, status, label) VALUES (?,?,?,?,?)",
            (okr_id, given["description"], position, "open", given["name"]),
        )
        db.commit()
        print(f"Added {okr_id} {given['name']} at position {position}.")
    elif action in ("done", "open"):
        okr_id = args[1] if len(args) > 1 else None
        if not okr_id or not phase_exists(okr_id):
            fail(f"okr {action}: {okr_id if okr_id is not None else '(no id)'} is not an objective this board holds.")
        db.execute("UPDATE phase SET status = ? WHERE name = ?", (action, okr_id))
        db.commit()
        print(f"{okr_id} is {'met' if action == 'done' else 'open'}.")
        print("\n".join(okr_report()))
    elif action == "edit":
        okr_id = args[1] if len(args) > 1 else None
        if not okr_id or not phase_exists(okr_id):
            fail(f"okr edit: {okr_id if okr_id is not None else '(no id)'} is not an objective this board holds.")
        given = read_flags(args[2:])
        fields = {"name": "label", "description": "goal", "position": "position"}
        unknown = [key for key in given if key not in fields]
        if unknown:
            fail(f"okr edit: {', '.join(unknown)} is not a field of an objective. Pass --name, --description or --position.")
        if not given:
            fail("okr edit: nothing to change. Pass --name, --description or --position.")
        for key, column_name in fields.items():
            if key not in given:
                continue
            value = number_from(given[key]) if key == "position" else given[key]
            if key == "position" and (value is None or not isinstance(value, int) or value < 1):
                fail("okr edit: --position is a whole number from 1.")
            db.execute(f"UPDATE phase SET {column_name} = ? WHERE name = ?", (value, okr_id))
            db.commit()
        print(f"Updated {okr_id}.")
    else:
        fail(
            f'Unknown okr command "{action}". Try: todo okr, todo okr add --name "..." --description "...",'
            ' todo okr done <id>, todo okr edit <id> --name "...".'
        )

elif command == "list":
    given = read_flags(args)
    filtered = bool(given.get("status") or given.get("area") or given.get("severity"))
    tasks = all_tasks()
    if not tasks:
        print("The board is empty.")
    if area_filter(given.get("area")) is not None:
        tasks = [task for task in tasks if in_areas(task, area_filter(given["area"]))]
    if given.get("severity"):
        tasks = [task for task in tasks if task["severity"] == given["severity"]]
    report = [] if filtered else phase_report()
    if report:
        print("\n".join(report))
    for status in STATUSES:
        if given.get("status") and given["status"] != status:
            continue
        in_column = [task for task in tasks if task["status"] == status and not is_blocked(task)]
        if not in_column:
            continue
        print(f"\n{status.upper().replace('_', ' ')} ({len(in_column)})")
        # Grouped by area, because a board of seventy tasks read as one list
        # tells you how much there is and nothing about what it is.
        areas = sorted({task["area"] or "unset" for task in in_column})
        for area in areas:
            if len(areas) > 1:
                print(f"  {area}")
            for task in [task for task in in_column if (task["area"] or "unset") == area]:
                # The phase only where it is not the one being worked on: naming
                # the current phase on every line says nothing and hides the
                # ones that are deferred.
                later = f"  · {task['phase']}" if phase_rank(task) > 0 else ""
                print(f"    {task['id']}  [{rank_field(task['severity'], 'severity')}/{rank_field(task['points'], 'points')}pt]  {task['title']}{later}")
    if not given.get("status") or given["status"] == "blocked":
        blocked = [task for task in tasks if is_blocked(task)]
        if blocked:
            print(f"\nBLOCKED ({len(blocked)})")
            for task in blocked:
                print(f"    {task['id']}  [{rank_field(task['severity'], 'severity')}/{rank_field(task['points'], 'points')}pt]  {task['title']}")
                print(f"      {block_of(task['id'])}")
    print("")

elif command == "show":
    print(card(one(args[0])))

elif command == "next":
    areas = areas_for(value_of(args, "area"))
    scope = f" in area {', '.join(areas)}" if areas else ""
    tasks = all_tasks()
    started, pick = ranked_call(lambda: choose(tasks, areas))
    held = ranked_call(lambda: started_but_waiting(tasks, areas))
    if pick is None:
        mine = [task for task in tasks if in_areas(task, areas)]
        # With no pick, every unblocked task still to do waits on a parent: a
        # backlog one that was eligible, or a started one, would have been picked.
        # A started task counts too, or "Nothing left", which ends a loop, would
        # be said while it waits.
        waiting = len([
            task for task in mine
            if task["status"] in ("backlog", "in_progress", "wait_for_roast") and not is_blocked(task)
        ])
        blocked = [task for task in mine if task["status"] not in ("done", "dropped") and is_blocked(task)]
        if waiting:
            print(f"Nothing eligible{scope}. {waiting} task(s) waiting on unfinished parents.")
        elif not blocked:
            print(f"Nothing left{scope}.")
        if blocked:
            print(f"{len(blocked)} task(s) blocked:")
            for task in blocked:
                print(f"  {task['id']}: {block_of(task['id'])}")
        say_held(held, tasks)
        sys.exit(0)

    current = current_phase()
    within = f" in {current['name']}" if current else ""
    print(
        f"ALREADY STARTED{scope}, finish this first\n"
        if started
        else f"NEXT{within}{scope}: highest severity, unblocked, fewest points\n"
    )
    print(card(pick))
    if held:
        print("")
        say_held(held, tasks)

elif command == "add":
    given = read_flags(args)
    required = ["title", "desc", "why", "severity", "points", "exit"]
    missing = [field for field in required if not given.get(field)]
    if missing:
        fail(f"A task needs every field. Missing: {', '.join(missing)}")
    if given["severity"] not in SEVERITIES:
        fail(f"severity must be one of {', '.join(SEVERITIES)}")
    if int(given["points"]) not in POINTS:
        fail(f"points must be one of {', '.join(str(point) for point in POINTS)}")

    parent_ids = [each.strip() for each in given.get("parent", "").split(",") if each.strip()]
    unknown = [
        parent
        for parent in parent_ids
        if db.execute("SELECT 1 FROM task WHERE id = ?", (parent,)).fetchone() is None
    ]
    if unknown:
        fail(
            f"No such task: {', '.join(unknown)}. A parent that does not exist can never be done, "
            "so the task would never become eligible and would just vanish from `next`."
        )

    # `--parent-task` is provenance, `--parent` is a blocker. A roast finding
    # takes the first: it came out of that task, but nothing about it says the
    # parent must finish before this can start. The parent is usually already
    # done by the time its findings exist.
    parent_task = resolve_parent(given.get("parent-task"))

    # An objective is a phase, so `--okr` is the same reference `--phase` is, and
    # `--okr none` files the card under no objective, which reads as now.
    if "okr" in given and "phase" not in given:
        if given["okr"] != "none" and given["okr"] and not phase_exists(given["okr"]):
            fail(
                f"No objective called {given['okr']}. Add it first: todo okr add --id {given['okr']}"
                ' --name "..." --description "..."'
            )
        given["phase"] = "" if given["okr"] == "none" else given["okr"]

    # A new card is work for the phase being worked on, unless it is named for a
    # later one. Boards with no phases carry none, as before.
    if given.get("phase") and not phase_exists(given["phase"]):
        fail(f'No phase called {given["phase"]}. Add it first: todo phase add {given["phase"]} --goal "..."')
    if "phase" in given:
        phase = given["phase"]
    else:
        current = current_phase()
        phase = current["name"] if current else None

    task_id = given.get("id") or next_id()
    parent_was_tested = tested_task(parent_task)
    db.execute(
        "INSERT INTO task (id, title, descr, why, severity, points, status, exit_cond, area, parent_task, phase)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            task_id,
            given["title"],
            given["desc"],
            given["why"],
            given["severity"],
            int(given["points"]),
            given.get("status", "backlog"),
            given["exit"],
            given.get("area"),
            parent_task,
            phase,
        ),
    )
    for parent in parent_ids:
        db.execute("INSERT INTO blocked_by (task, parent) VALUES (?,?)", (task_id, parent))
    db.commit()
    print(f"Added {task_id}: {given['title']}")
    if parent_task:
        print(
            f"  a child of {parent_task}, which now has {len(open_children(parent_task))}"
            " open child task(s)."
        )
        if parent_was_tested and not tested_task(parent_task):
            print(f"  {parent_task}'s test state is cleared: it has an open finding now. What it had is kept in a note.")

elif command in ("edit", "set"):
    # `set` is the same command under the name some projects' rules use.
    task_id = args[0]
    before = one(task_id)
    given = read_flags(args[1:])

    if "okr" in given and "phase" not in given:
        if given["okr"] != "none" and given["okr"] and not phase_exists(given["okr"]):
            fail(
                f"No objective called {given['okr']}. Add it first: todo okr add --id {given['okr']}"
                ' --name "..." --description "..."'
            )
        given["phase"] = "" if given["okr"] == "none" else given["okr"]

    COLUMNS = {
        "title": "title",
        "desc": "descr",
        "why": "why",
        "severity": "severity",
        "points": "points",
        "exit": "exit_cond",
        "area": "area",
        "phase": "phase",
        "evidence": "evidence",
    }
    touched = [field for field in given if field in COLUMNS]
    if not touched and "parent" not in given and "parent-task" not in given and "note" not in given:
        fail(
            "Nothing to change. Pass --title, --desc, --why, --severity, --points, --exit,"
            " --phase, --okr, --evidence, --note, --parent or --parent-task."
        )
    # An empty value moves a task out of every phase, which reads as now.
    if given.get("phase") and not phase_exists(given["phase"]):
        fail(f'No phase called {given["phase"]}. Add it first: todo phase add {given["phase"]} --goal "..."')

    if "parent-task" in given:
        wanted = (given.get("parent-task") or "").strip()
        if not wanted:
            db.execute("UPDATE task SET parent_task = NULL WHERE id = ?", (task_id,))
            print(f"{task_id} is no longer a child of anything.")
        elif wanted == task_id:
            fail(f"{task_id} cannot be its own parent.")
        else:
            resolved = resolve_parent(wanted)
            parent_was_tested = tested_task(resolved)
            db.execute("UPDATE task SET parent_task = ? WHERE id = ?", (resolved, task_id))
            print(f"{task_id} is now a child of {resolved}.")
            if parent_was_tested and not tested_task(resolved):
                print(f"  {resolved}'s test state is cleared: it has an open finding now. What it had is kept in a note.")
        db.commit()

    if given.get("severity") and given["severity"] not in SEVERITIES:
        fail(f"severity must be one of {', '.join(SEVERITIES)}")
    if given.get("points") and int(given["points"]) not in POINTS:
        fail(f"points must be one of {', '.join(str(point) for point in POINTS)}")

    for field in touched:
        value = int(given[field]) if field == "points" else given[field]
        db.execute(f"UPDATE task SET {COLUMNS[field]} = ? WHERE id = ?", (value, task_id))

    if "parent" in given:
        wanted_parents = [each.strip() for each in (given.get("parent") or "").split(",") if each.strip()]
        unknown = [
            parent
            for parent in wanted_parents
            if db.execute("SELECT 1 FROM task WHERE id = ?", (parent,)).fetchone() is None
        ]
        if unknown:
            fail(f"No such task: {', '.join(unknown)}. A parent that does not exist can never be done.")

        # A cycle is worse than a wrong parent: every task in it waits forever
        # and `next` reports them as blocked rather than as broken.
        seen = []
        stack = list(wanted_parents)
        while stack:
            at = stack.pop()
            if at == task_id:
                path = " -> ".join(seen) if seen else at
                fail(f"That parent makes a cycle: {task_id} would wait on itself, through {path}.")
            if at in seen:
                continue
            seen.append(at)
            stack.extend(wanted_parents if at == task_id else parents(at))

        db.execute("DELETE FROM blocked_by WHERE task = ?", (task_id,))
        for parent in wanted_parents:
            db.execute("INSERT INTO blocked_by (task, parent) VALUES (?,?)", (task_id, parent))

    # A note is added, never replaced: what was known when is the point of one.
    if given.get("note"):
        db.execute("INSERT INTO note (task, at, text) VALUES (?,?,?)", (task_id, now(), given["note"]))
    db.execute("UPDATE task SET updated = ? WHERE id = ?", (now(), task_id))

    db.commit()
    changed = touched + (["parent"] if "parent" in given else []) + (["note"] if "note" in given else [])
    print(f"{task_id}: {', '.join(changed)} changed")
    if is_tested(before) and not tested_task(task_id):
        print(f"  {task_id}'s test state is cleared: what it proved has changed. What it had is kept in a note.")
    print(card(one(task_id)))

elif command == "move":
    task_id, status = args[0], args[1]
    given = read_flags(args[2:])
    if status == "blocked":
        # A block is a row beside the status, not a status, so the task keeps the
        # status it had and `next`, `list` and `show` read the block from the row.
        task = one(task_id)
        if not given.get("reason"):
            fail(f'move {task_id} blocked needs --reason "...": a blocked task leaves next, so it has to say why.')
        db.execute(
            "INSERT OR REPLACE INTO blocked (task, reason, since) VALUES (?,?,?)",
            (task_id, given["reason"], now()),
        )
        db.execute("UPDATE task SET updated = ? WHERE id = ?", (now(), task_id))
        db.commit()
        print(f"{task_id}: {task['status']}, blocked: {given['reason']}")
        sys.exit(0)
    if status not in STATUSES:
        fail(f"status must be one of {', '.join(STATUSES)}, or blocked")
    task = one(task_id)
    if status == "in_progress":
        loop, mine = loop_areas()
        if mine is not None and not in_areas(task, mine):
            fail(f"{task_id} is in area {task['area'] or 'unset'}; this session works in {', '.join(mine)} (its loop file, {loop[1]}).")
    was_blocked = block_of(task_id)
    parent_was_tested = tested_task(task.get("parent_task"))
    stamp = now()
    db.execute("UPDATE task SET status = ?, updated = ? WHERE id = ?", (status, stamp, task_id))
    if was_blocked is not None:
        db.execute("DELETE FROM blocked WHERE task = ?", (task_id,))
    if status == "done":
        db.execute("UPDATE task SET closed = ? WHERE id = ?", (stamp, task_id))
        if given.get("evidence"):
            db.execute("UPDATE task SET evidence = ? WHERE id = ?", (given["evidence"], task_id))
    if status == "dropped" and given.get("reason"):
        db.execute("UPDATE task SET reason = ? WHERE id = ?", (given["reason"], task_id))
    db.commit()
    print(f"{task_id}: {task['status']} -> {status}")
    if was_blocked is not None:
        print(f"  no longer blocked: {was_blocked}")
    if is_tested(task) and not tested_task(task_id):
        print(f"  {task_id}'s test state is cleared: it is {status} now. What it had is kept in a note.")
    if parent_was_tested and not tested_task(task["parent_task"]):
        print(f"  {task['parent_task']}'s test state is cleared: it has an open finding now. What it had is kept in a note.")

    # Said, not refused. A finding is a new card rather than a reopened one in
    # some projects' rules, but others may differ, so the tool names the
    # rule and moves the task; a refusal here would be a gate on every board.
    if task["status"] == "done" and status != "done":
        print(f"  {task_id} was done. A finding is a new card rather than a reopened one: add --parent-task {task_id}.")
    if status == "in_progress":
        others = [
            row["id"]
            for row in db.execute(
                "SELECT id FROM task WHERE status = 'in_progress' AND id != ? ORDER BY id", (task_id,)
            ).fetchall()
        ]
        if others:
            verb = "is" if len(others) == 1 else "are"
            print(f"  {', '.join(others)} {verb} in progress too; finish one before starting another.")

    # Everything below PRINTS. Nothing here refuses a move, including closing a
    # task that still has open children: the tool says what is worth roasting
    # and the agent decides. A refusal would be a gate nobody asked for.
    if status == "done":
        print("")
        print(f"Roast {task_id} now, in the background, and take the next task while it runs.")
        print(f"  File everything it finds with: add --parent-task {task_id}")
        print("  A finding is a child of this task, not a loose card, so the board can tell")
        print("  when everything that came out of it is finished.")

        still_open = open_children(task_id)
        if still_open:
            print("")
            print(
                f"{task_id} still has {len(still_open)} open child task(s): "
                f"{', '.join(child['id'] for child in still_open)}"
            )

        # The other half: closing a CHILD can complete its parent's group.
        if task.get("parent_task"):
            siblings = open_children(task["parent_task"])
            print("")
            if siblings:
                print(
                    f"{task['parent_task']} is waiting on {len(siblings)} more: "
                    f"{', '.join(sibling['id'] for sibling in siblings)}"
                )
            else:
                family = [child["id"] for child in children(task["parent_task"])]
                print(f"THAT WAS THE LAST ONE. Every child of {task['parent_task']} is finished.")
                print(f"  Roast {task['parent_task']} together with all of them: {', '.join(family)}")
                print("  Roast what was done for the WHOLE task, not just this last piece: the")
                print("  point of the round is whether the parent is actually finished now.")
                print(f"  Anything it finds becomes a new child of {task['parent_task']}, and the cycle")
                print("  repeats until a round finds nothing.")

elif command == "roast":
    # A round of a roast, recorded against the task it reviewed: the file the
    # reviewer's answer went to, the numbers if it gave any, and, once judged,
    # what was filed from it. Recording again against the same file updates that
    # round, since judging is a second moment of the same round.
    task_id = args[0]
    one(task_id)
    given = read_flags(args[1:])
    if not given.get("file"):
        fail("roast needs --file, the file the roast wrote its answer to.")
    last = db.execute("SELECT * FROM roast WHERE task = ? ORDER BY round DESC LIMIT 1", (task_id,)).fetchone()
    again = bool(last is not None and last["file"] == given["file"])
    roast_round = last["round"] if again else (last["round"] if last is not None else 0) + 1
    if "score" in given:
        score = number_from(given["score"])
        if score is None:
            fail("roast: --score must be a number.")
    else:
        score = last["score"] if again else None
    if "criticals" in given:
        criticals = number_from(given["criticals"])
        if criticals is None:
            fail("roast: --criticals must be a number.")
    else:
        criticals = last["criticals"] if again else None
    if "filed" not in given:
        filed = last["filed"] if again else None
    elif given["filed"] == "none":
        filed = ""
    else:
        filed = ", ".join(each.strip() for each in given["filed"].split(",") if each.strip())
    if "dismissed" in given:
        dismissed = given["dismissed"]
    else:
        dismissed = last["dismissed"] if again else None
    if again:
        db.execute(
            "UPDATE roast SET at = ?, score = ?, criticals = ?, filed = ?, dismissed = ? WHERE task = ? AND round = ?",
            (now(), score, criticals, filed, dismissed, task_id, roast_round),
        )
    else:
        db.execute(
            "INSERT INTO roast (task, round, at, file, score, criticals, filed, dismissed) VALUES (?,?,?,?,?,?,?,?)",
            (task_id, roast_round, now(), given["file"], score, criticals, filed, dismissed),
        )
    db.commit()
    print(f"{task_id} roast round {roast_round}{' (updated)' if again else ''}")
    if filed is None:
        print(
            "  Now judge it: reproduce each finding or say what it misread, file what survives with"
            f" add --parent-task {task_id},"
        )
        print("  then record it here with --filed <ids>, or --filed none.")
    elif filed == "":
        print("  Nothing survived adjudication.")
    else:
        print(f"  Filed {filed}.")

elif command == "validate":
    # Read-only. Every problem it can find, not the first, and exit 1 when there
    # is one, so a script can ask whether the board holds together.
    tasks = all_tasks()
    by_id = {task["id"]: task for task in tasks}
    phase_names = {phase["name"] for phase in all_phases()}
    problems = []
    for task in tasks:
        problem = severity_problem(task)
        if problem is not None:
            problems.append(problem)
        points_error = points_problem(task)
        if points_error is not None:
            problems.append(points_error)
        for parent in task["parents"]:
            blocker = by_id.get(parent)
            if blocker is None:
                problems.append(f"{task['id']}: its blocker {parent} does not exist")
                continue
            # A blocker less severe than what it blocks is never picked ahead of
            # it, so the severe task starves behind one nobody selects.
            if (
                task["status"] not in ("done", "dropped")
                and blocker["status"] != "done"
                and severity_problem(blocker) is None and problem is None
                and severity_rank(blocker) > severity_rank(task)
            ):
                problems.append(
                    f"{task['id']} ({task['severity']}) waits on {parent} ({blocker['severity']}),"
                    " which is less severe, so next would never pick it first"
                )
            if task["status"] == "done" and blocker["status"] != "done":
                problems.append(f"{task['id']} is done, but its blocker {parent} is {blocker['status']}")
        if task.get("phase") and task["phase"] not in phase_names:
            problems.append(f"{task['id']}: its phase {task['phase']} does not exist")
        if task.get("parent_task"):
            origin = by_id.get(task["parent_task"])
            if origin is None:
                problems.append(f"{task['id']}: it came out of {task['parent_task']}, which does not exist")
            elif origin.get("parent_task"):
                problems.append(
                    f"{task['id']}: it came out of {task['parent_task']}, which itself came out of"
                    f" {origin['parent_task']}; one level only"
                )
        # An exit condition that cannot be checked is a wish. This does not prove
        # checkability, it only catches the one-word placeholder.
        if len(task["exit_cond"].strip()) < 25:
            problems.append(f"{task['id']}: its exit condition is too short to check")
        reason = block_of(task["id"])
        if reason is not None and task["status"] in ("done", "dropped"):
            problems.append(f"{task['id']} is {task['status']} and still blocked: {reason}")
        # A claim the guards would have refused, which only a board whose guards
        # were dropped, or were never added, can hold.
        if records_tests(task):
            for flag in ("tested", "e2e_tested"):
                if task[flag] not in (0, 1):
                    problems.append(f"{task['id']}: {flag} holds {shown(task[flag])}, not 0 or 1")
            if task["tested"] == 1 and task["status"] != "done":
                problems.append(f"{task['id']} is tested but {task['status']}")
            if task["tested"] == 1 and not (task["tested_how"] or "").strip(BLANK):
                problems.append(f"{task['id']} is tested with nothing saying what proved it")
            if task["e2e_tested"] == 1 and task["tested"] != 1:
                problems.append(f"{task['id']} is e2e tested but not tested")
            if task["e2e_tested"] == 1 and not (task["e2e_how"] or "").strip(BLANK):
                problems.append(f"{task['id']} is e2e tested with nothing saying what proved it")
            still_open = [child["id"] for child in open_children(task["id"])] if is_tested(task) else []
            if still_open:
                problems.append(f"{task['id']} is tested, but its finding(s) {', '.join(still_open)} are open")

    present = task_columns()
    if any(name in present for name, _kind in TEST_STATE_COLUMNS):
        lacking = [name for name, _kind in TEST_STATE_COLUMNS if name not in present]
        if lacking:
            problems.append(f"the board records test states without the column(s) {', '.join(lacking)}")
        guards = {row["name"] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'").fetchall()}
        absent = [name for name in TEST_STATE_TRIGGER_NAMES if name not in guards]
        if absent:
            problems.append(
                f"the board records test states without the guard(s) {', '.join(absent)}; the next todo tested adds them"
            )

    # Depth-first cycle detection over the blocker edges.
    state = {}

    def walk(node, trail):
        if state.get(node) == "done":
            return
        if state.get(node) == "open":
            problems.append(f"a cycle: {' -> '.join(trail + [node])}")
            return
        state[node] = "open"
        for parent in by_id[node]["parents"] if node in by_id else []:
            if parent in by_id:
                walk(parent, trail + [node])
        state[node] = "done"

    for task in tasks:
        walk(task["id"], [])

    unjudged = [
        row["task"]
        for row in db.execute("SELECT DISTINCT task FROM roast WHERE filed IS NULL ORDER BY task").fetchall()
    ]
    listed = f": {', '.join(unjudged)}" if unjudged else ""
    print(f"{len(unjudged)} task(s) have a roast round nobody recorded as judged{listed}.")
    if not problems:
        print(f"Board is valid. {len(tasks)} task(s).")
        sys.exit(0)
    print(f"{len(problems)} problem(s):", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)
    sys.exit(1)

elif command == "render":
    # The board as Markdown, beside the database or wherever --out says, so a
    # person can read it and a change to it reads as a diff. --check writes
    # nothing and fails when the file is stale.
    out_given = value_of(args, "out")
    out = os.path.abspath(out_given) if out_given else os.path.join(os.path.dirname(BOARD), "TODO_BOARD.md")
    label = out_given if out_given is not None else ".claude/TODO_BOARD.md"
    text = ranked_call(render_board)
    if "--check" in args:
        current_text = ""
        if os.path.exists(out):
            with open(out, encoding="utf-8", newline="") as handle:
                current_text = handle.read()
        if current_text != text:
            again_with = f" --out {out_given}" if out_given else ""
            fail(f"{label} is stale: it differs from what the board renders. Run: todo render{again_with}")
        print(f"{label} is in sync with the board.")
    else:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        print(f"rendered {label}")

elif command == "rm":
    # Removing a card, which leaves only this line behind, so it says why. A card
    # that others still wait on, or that others came out of, is refused unless
    # --force, which cuts those links.
    task_id = args[0]
    rest = args[1:]
    one(task_id)
    reason = value_of(rest, "reason")
    if not reason or reason == "--force":
        fail(f'rm {task_id} needs --reason "...": a removed card leaves nothing behind but the reason.')
    waiting = [
        row["task"]
        for row in db.execute("SELECT task FROM blocked_by WHERE parent = ? ORDER BY task", (task_id,)).fetchall()
    ]
    kids = [child["id"] for child in children(task_id)]
    if (waiting or kids) and "--force" not in rest:
        fail(
            f"rm: {', '.join(waiting + kids)} still point at {task_id}. Point them elsewhere first,"
            " or pass --force to cut those links."
        )
    db.execute("DELETE FROM blocked_by WHERE task = ? OR parent = ?", (task_id, task_id))
    db.execute("UPDATE task SET parent_task = NULL WHERE parent_task = ?", (task_id,))
    for table in ("blocked", "note", "roast"):
        db.execute(f"DELETE FROM {table} WHERE task = ?", (task_id,))
    db.execute("DELETE FROM task WHERE id = ?", (task_id,))
    db.commit()
    print(f"removed {task_id}: {reason}")

elif command in ("tested", "e2e"):
    # Done is not tested. A claim needs a done task that is not blocked, has no
    # open finding, and says what proved it; e2e needs tested first. The board
    # is checked inside the claim's own transaction and before anything is
    # written, so a refused claim leaves the file as it was, a board that never
    # records a test never gains the columns, and a finding filed by another
    # session cannot land between the check and the claim.
    task_id = args[0] if args else None
    if not task_id or task_id.startswith("--"):
        fail(f'{command} needs a task: todo {command} SB-003 --evidence "what was run and what it showed"')
    evidence = (value_of(args[1:], "evidence") or "").strip(BLANK)
    if not evidence:
        fail(f'{command} {task_id} needs --evidence "...": what was run and what it showed. Nothing was recorded.')
    stamp = now()
    try:
        db.execute("BEGIN IMMEDIATE")
        problem = claim_problem(task_id, command)
        if problem is not None:
            give_up(problem)
        added = start_test_states() if command == "tested" else False
        if command == "tested":
            db.execute(
                "UPDATE task SET tested = 1, tested_how = ?, tested_at = ?, updated = ? WHERE id = ?",
                (evidence, stamp, stamp, task_id),
            )
        else:
            db.execute(
                "UPDATE task SET e2e_tested = 1, e2e_how = ?, e2e_at = ?, updated = ? WHERE id = ?",
                (evidence, stamp, stamp, task_id),
            )
        db.commit()
    except sqlite3.DatabaseError as error:
        give_up(f"{task_id}: the board could not take the claim: {error}. Nothing was recorded.")
    print(f"{task_id}: {'tested' if command == 'tested' else 'e2e tested'}, {evidence}")
    if added:
        print("  This board records test states from now on. Every other done task reads tested: no until it is tested.")

elif command == "untest":
    # Clearing a test state by hand, for a proof that no longer holds. What it
    # had goes into a note first: a cleared claim is history, not nothing. The
    # state is read inside the transaction that clears it, so two clears at once
    # leave one note, and a failure leaves neither the note nor the change.
    task_id = args[0] if args else None
    if not task_id or task_id.startswith("--"):
        fail('untest needs a task: todo untest SB-003 [e2e] --reason "why the proof no longer holds"')
    only_e2e = args[1:2] == ["e2e"]
    reason = (value_of(args[1:], "reason") or "").strip(BLANK)
    if not reason:
        fail(f'untest {task_id} needs --reason "...": a cleared test state says why. Nothing was changed.')
    if not has_test_states():
        one(task_id)
        print(f"{task_id}: this board records no test states, so there is nothing to clear.")
        sys.exit(0)
    stamp = now()
    try:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM task WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            give_up(f"No task {task_id}.")
        task = dict(row)
        if not (task["e2e_tested"] == 1 if only_e2e else is_tested(task)):
            db.rollback()
            db.close()
            print(f"{task_id} is not {'e2e tested' if only_e2e else 'tested'}, so there is nothing to clear.")
            sys.exit(0)
        if only_e2e:
            cleared = f"e2e tested: {task['e2e_how'] or ''}"
            sets = "e2e_tested = 0, e2e_how = NULL, e2e_at = NULL"
        else:
            cleared = had(task)
            sets = "tested = 0, tested_how = NULL, tested_at = NULL, e2e_tested = 0, e2e_how = NULL, e2e_at = NULL"
        db.execute(
            "INSERT INTO note (task, at, text) VALUES (?,?,?)",
            (task_id, stamp, f"test state cleared by hand: {reason}; it had {cleared}"),
        )
        db.execute(f"UPDATE task SET {sets}, updated = ? WHERE id = ?", (stamp, task_id))
        db.commit()
    except sqlite3.DatabaseError as error:
        give_up(f"{task_id}: the board could not clear it: {error}. Nothing was changed.")
    print(f"{task_id}: {'e2e test state' if only_e2e else 'test state'} cleared. What it had is kept in a note.")

elif command == "tests":
    # Read-only: which done tasks are tested, and which are not. A board that
    # records no test states says so rather than calling every task untested.
    areas = area_filter(value_of(args, "area"))
    if not has_test_states():
        print('This board records no test states yet. The first is: todo tested <id> --evidence "..."')
        sys.exit(0)
    scope = f" in area {', '.join(areas)}" if areas else ""
    done = [task for task in all_tasks() if task["status"] == "done" and in_areas(task, areas)]
    if not done:
        print(f"Nothing is done{scope} yet.")
    groups = [
        ("DONE, NOT TESTED", [task for task in done if task["tested"] != 1]),
        ("TESTED, NOT E2E TESTED", [task for task in done if task["tested"] == 1 and task["e2e_tested"] != 1]),
        ("E2E TESTED", [task for task in done if task["tested"] == 1 and task["e2e_tested"] == 1]),
    ]
    for heading, group in groups:
        if not group:
            continue
        print(f"\n{heading}{scope} ({len(group)})")
        for task in group:
            print(f"    {task['id']}  [{rank_field(task['severity'], 'severity')}/{rank_field(task['points'], 'points')}pt]  {task['title']}")
            if task["tested"] == 1:
                print(f"      tested: {task['tested_how']}")
            if task["e2e_tested"] == 1:
                print(f"      e2e   : {task['e2e_how']}")
    print("")

else:
    fail(f'Unknown command "{command}". Try: list, next, show, add, edit, set, move, phase, okr, roast, validate, render, rm.')
