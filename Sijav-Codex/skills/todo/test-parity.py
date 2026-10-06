#!/usr/bin/env python3
"""Proves todo.py and todo.mjs are the same tool.

Two implementations of one thing drift, silently, and the drift shows up as a
board that reads differently depending on which runtime happened to be
installed. So this does not check that both "work": it runs the SAME sequence of
commands through each, against its own fresh board, and compares what they print
character for character.

Run it after changing either half:

    python <this folder>/test-parity.py
"""

import hashlib
import io
import os
import re
import runpy
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Some steps carry invisible characters on purpose. Printed where the console's
# code page has no such character, they come out as escapes instead of an error.
for _stream in (sys.stdout, sys.stderr):
    _stream.reconfigure(errors="backslashreplace")

HERE = os.path.dirname(os.path.abspath(__file__))
NODE_SCRIPT = os.path.join(HERE, "todo.mjs")
NODE_EXE = os.environ.get("SIJAV_TODO_NODE", "node")
PY_SCRIPT = os.path.join(HERE, "todo.py")

# Areas (EF-002): a session works only in the areas the owner gave it, one, a
# list or all, and a parent in another area still counts once it is done. Each
# step carries what its output must say and must not say, so the two halves
# agreeing on a wrong pick does not pass.
AREA_STEPS = [
    (["add", "--id", "SB-020", "--title", "a back task", "--desc", "d", "--why", "w",
      "--severity", "high", "--points", "2", "--exit", "e", "--area", "back"], "Added SB-020", None),
    (["add", "--id", "SB-021", "--title", "a front task after the back one", "--desc", "d", "--why", "w",
      "--severity", "critical", "--points", "1", "--exit", "e", "--area", "front", "--parent", "SB-020"],
     "Added SB-021", None),
    (["add", "--id", "SB-022", "--title", "an ai task", "--desc", "d", "--why", "w",
      "--severity", "medium", "--points", "1", "--exit", "e", "--area", "ai"], "Added SB-022", None),
    (["next", "--area", "front"], "Nothing eligible in area front. 1 task(s) waiting on unfinished parents.", "SB-0"),
    (["next", "--area", "back"], "in area back: highest severity", "SB-021"),
    (["next", "--area", "ai,front"], "SB-022", "SB-020"),
    (["next", "--area", "all"], "ALREADY STARTED, finish this first", "in area"),
    (["next", "--area", "nowhere"], "Nothing left in area nowhere.", "SB-0"),
    (["next", "--area", "unset"], "ALREADY STARTED in area unset, finish this first", "SB-02"),
    (["move", "SB-020", "in_progress"], "SB-020", None),
    (["next", "--area", "back"], "ALREADY STARTED in area back, finish this first", "SB-021"),
    (["next", "--area", "front"], "Nothing eligible in area front.", "SB-021  ["),
    (["move", "SB-020", "done"], "SB-020", None),
    (["next", "--area", "front"], "SB-021", "SB-022"),
    (["list", "--area", "back,front"], "SB-021", "SB-022"),
]

# One script of work, exercising every command and both kinds of parent. Ids are
# given explicitly so the two runs produce the same ones and a diff means a real
# difference rather than a numbering race.
SCRIPT = [
    ["list"],
    ["add", "--id", "SB-001", "--title", "the parent", "--desc", "d", "--why", "w",
     "--severity", "high", "--points", "2", "--exit", "e", "--area", "api"],
    ["add", "--id", "SB-002", "--title", "a blocker", "--desc", "d", "--why", "w",
     "--severity", "critical", "--points", "1", "--exit", "e"],
    ["next"],
    ["move", "SB-002", "in_progress"],
    ["next"],
    ["move", "SB-002", "done"],
    ["move", "SB-001", "done"],
    ["add", "--id", "SB-003", "--title", "first finding", "--desc", "d", "--why", "w",
     "--severity", "medium", "--points", "1", "--exit", "e", "--parent-task", "SB-001"],
    ["add", "--id", "SB-004", "--title", "second finding", "--desc", "d", "--why", "w",
     "--severity", "low", "--points", "3", "--exit", "e", "--parent-task", "SB-001"],
    ["show", "SB-001"],
    ["show", "SB-003"],
    # A grandchild attempt, which must flatten identically in both.
    ["add", "--id", "SB-005", "--title", "a grandchild", "--desc", "d", "--why", "w",
     "--severity", "low", "--points", "1", "--exit", "e", "--parent-task", "SB-003"],
    ["move", "SB-003", "done"],
    ["move", "SB-005", "dropped"],
    ["move", "SB-004", "done"],
    ["edit", "SB-004", "--severity", "critical", "--points", "5"],
    ["edit", "SB-004", "--parent-task", ""],
    ["list"],
    ["next"],
    # An id left to the board keeps the board's prefix, KN-482: it was a
    # hardcoded SB- in both halves, whatever the project.
    ["add", "--title", "an id left to the board", "--desc", "d", "--why", "w",
     "--severity", "low", "--points", "2", "--exit", "e"],
    # Phases, which the Python half did not have at all until KN-482.
    ["phase"],
    ["phase", "add", "MVP", "--goal", "ship the first version"],
    ["phase", "add", "Later", "--goal", "what comes after it"],
    ["phase", "add", "MVP", "--goal", "a second one of the same name"],
    ["add", "--id", "SB-007", "--title", "for later", "--desc", "d", "--why", "w",
     "--severity", "critical", "--points", "1", "--exit", "e", "--phase", "Later"],
    ["add", "--id", "SB-008", "--title", "in no known phase", "--desc", "d", "--why", "w",
     "--severity", "low", "--points", "1", "--exit", "e", "--phase", "Nowhere"],
    ["list"],
    ["next"],
    ["show", "SB-007"],
    ["edit", "SB-007", "--phase", "Nowhere"],
    ["phase", "done", "MVP"],
    ["phase", "open", "Nowhere"],
    ["phase", "frobnicate"],
    ["edit", "SB-007", "--phase", ""],
    ["list"],
    # What an older JSON board tool had and this one lacked: objectives,
    # blocks, notes, what closed a task, roast rounds, validate, render and rm.
    ["okr"],
    ["okr", "add", "--name", "The first objective", "--description", "what it is for"],
    ["okr", "add", "--name", "The first objective", "--description", "a second one of the same name"],
    ["okr", "add", "--id", "OKR-9", "--name", "Placed", "--description", "at a taken position", "--position", "1"],
    ["okr", "add", "--name", "No description"],
    ["okr", "edit", "OKR-3", "--name", "The first objective, renamed"],
    ["okr", "edit", "OKR-3", "--colour", "red"],
    ["okr", "done", "Later"],
    ["okr"],
    ["okr", "open", "Nowhere"],
    ["okr", "frobnicate"],
    ["add", "--id", "SB-009", "--title", "serving an objective", "--desc", "d", "--why", "w",
     "--severity", "high", "--points", "3", "--exit", "a condition long enough to check", "--okr", "OKR-3"],
    ["add", "--id", "SB-010", "--title", "for no objective", "--desc", "d", "--why", "w",
     "--severity", "medium", "--points", "1", "--exit", "a condition long enough to check", "--okr", "none"],
    ["add", "--id", "SB-011", "--title", "an unknown objective", "--desc", "d", "--why", "w",
     "--severity", "low", "--points", "1", "--exit", "e", "--okr", "OKR-77"],
    ["show", "SB-009"],
    ["move", "SB-009", "blocked"],
    ["move", "SB-009", "blocked", "--reason", "waiting on the owner's answer"],
    ["list"],
    ["list", "--status", "blocked"],
    ["list", "--severity", "medium"],
    ["next"],
    ["show", "SB-009"],
    ["move", "SB-009", "in_progress"],
    ["move", "SB-010", "in_progress"],
    ["set", "SB-010", "--note", "the first thing learned"],
    ["edit", "SB-010", "--note", "the second thing learned", "--points", "2"],
    ["move", "SB-010", "done", "--evidence", "the check that was run"],
    ["move", "SB-010", "backlog"],
    ["move", "SB-006", "dropped", "--reason", "no longer wanted"],
    ["show", "SB-010"],
    ["show", "SB-006"],
    ["roast", "SB-010"],
    ["roast", "SB-010", "--file", "roasts/sb-010-1.md", "--score", "7.5", "--criticals", "1"],
    ["roast", "SB-010", "--file", "roasts/sb-010-1.md", "--filed", "SB-009, SB-007"],
    ["roast", "SB-010", "--file", "roasts/sb-010-2.md", "--score", "nine"],
    ["roast", "SB-010", "--file", "roasts/sb-010-2.md", "--filed", "none"],
    ["show", "SB-010"],
    ["validate"],
    ["render", "--out", "BOARD.md"],
    ["render", "--out", "BOARD.md", "--check"],
    ["edit", "SB-009", "--title", "serving an objective, retitled"],
    ["render", "--check", "--out", "BOARD.md"],
    ["rm", "SB-002", "--reason", "a mistake"],
    ["rm", "SB-007"],
    ["rm", "SB-001", "--reason", "it has findings"],
    ["rm", "SB-001", "--reason", "it has findings", "--force"],
    ["show", "SB-003"],
    ["okr", "done", "OKR-3"],
    ["next"],
    *[step for step, _must, _never in AREA_STEPS],
    # Failure paths print to stderr and exit non-zero; those must match too.
    ["show", "SB-999"],
    ["add", "--title", "no fields"],
    ["move", "SB-001", "nonsense"],
    ["edit", "SB-001"],
    ["frobnicate"],
]


# The session ids Claude Code and Codex give every command. A run sees one only
# when a step sets it, so where the test itself runs never changes a result.
SESSION_VARIABLES = ("CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID", "CODEX_THREAD_ID")


def run(interpreter, script, directory, args, session=None):
    # Bytes, decoded as UTF-8 and nothing else. text=True read the pipes in
    # universal-newline mode, which folds \r\n into \n, so Python printing \r\n
    # on Windows while Node printed \n passed here and differed in every file
    # either half's output was written to, KN-482.
    env = {key: value for key, value in os.environ.items() if key not in SESSION_VARIABLES}
    if session:
        env["CLAUDE_CODE_SESSION_ID"] = session
    result = subprocess.run([interpreter, script, *args], cwd=directory, capture_output=True, env=env)
    stdout = result.stdout.decode("utf-8")
    stderr = result.stderr.decode("utf-8")
    # stdout, stderr and the exit code all count: a difference in which stream a
    # message goes to, or in whether the command failed, is a real difference.
    #
    # The sandbox path is masked first. Each half runs in its own temp directory
    # and the "Started a new board at ..." line names it, so without this the
    # very first command always differs for a reason that is not a difference
    # between the two implementations.
    text = f"out:{stdout}err:{stderr}code:{result.returncode}"
    return text.replace(directory, "<sandbox>").replace(directory.replace(chr(92), chr(92) * 2), "<sandbox>")


def transcript(interpreter, script):
    directory = tempfile.mkdtemp(prefix="todo-parity-")
    try:
        open(os.path.join(directory, "package.json"), "w").write('{"name":"sandbox"}\n')

        # A board has to be created EXPLICITLY now. This used to make an empty
        # .claude and let the first `list` bring a board into being, and when
        # todo stopped doing that the whole transcript quietly became twenty
        # five identical refusals that still agreed with each other. Parity
        # means both halves say the same thing, not that either is right.
        run(interpreter, script, directory, ["init", "--here"])

        return [run(interpreter, script, directory, args) for args in SCRIPT]
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def refuses_without_a_board(interpreter, script):
    """No board, and none created: the exit condition of SB-130.

    Separate from the transcript because it needs a directory with NO board
    anywhere above it, the opposite of what every other case needs. Asserting
    the FILESYSTEM matters as much as the message: if the mkdir ever moves
    back above the refusal, the words and the exit code stay right and a
    directory appears anyway.
    """
    directory = tempfile.mkdtemp(prefix="todo-boardless-")
    try:
        said = run(interpreter, script, directory, ["list"])
        leaked = os.path.exists(os.path.join(directory, ".claude"))
        return said + "claude-dir-created:" + str(leaked)
    finally:
        shutil.rmtree(directory, ignore_errors=True)


# A session's loop file sets its board and its areas, and the tool reads it on every
# command. Each step: the session running it, the command, what it must print, what
# it must never print.
LOOP_FILE = '---\nsession: "loop-session"\nareas: back,ai\n---\n\n# The law\n'
BOARD_LOOP_FILE = '---\nsession: "board-session"\nboard: sub/.claude/todo.db\n---\n'
MISSING_LOOP_FILE = '---\nsession: "missing-session"\nboard: nowhere/.claude/todo.db\n---\n'
LOOP_SETUP = [
    ["add", "--id", "SB-101", "--title", "a back task", "--desc", "d", "--why", "w",
     "--severity", "high", "--points", "2", "--exit", "e", "--area", "back"],
    ["add", "--id", "SB-102", "--title", "a front task", "--desc", "d", "--why", "w",
     "--severity", "critical", "--points", "1", "--exit", "e", "--area", "front"],
    ["add", "--id", "SB-103", "--title", "an ai task", "--desc", "d", "--why", "w",
     "--severity", "medium", "--points", "1", "--exit", "e", "--area", "ai"],
    ["add", "--id", "SB-104", "--title", "a task with no area", "--desc", "d", "--why", "w",
     "--severity", "low", "--points", "1", "--exit", "e"],
    ["init", "sub"],
]
LOOP_STEPS = [
    ("loop-session", ["next"], "NEXT in area back, ai: highest severity", "SB-102"),
    ("loop-session", ["next", "--area", "ai"], "SB-103", "SB-101"),
    ("loop-session", ["next", "--area", "front"], "front: not one of this session's areas (back, ai), set in", "SB-1"),
    ("loop-session", ["move", "SB-102", "in_progress"], "SB-102 is in area front; this session works in back, ai", "->"),
    ("loop-session", ["move", "SB-103", "in_progress"], "SB-103: backlog -> in_progress", None),
    ("loop-session", ["next"], "ALREADY STARTED in area back, ai, finish this first", "SB-102"),
    (None, ["next"], "ALREADY STARTED, finish this first", "in area"),
    ("another-session", ["next"], "ALREADY STARTED, finish this first", "in area"),
    ("board-session", ["list"], "The board is empty.", "SB-101"),
    ("missing-session", ["list"], "names the board", "SB-101"),
]


def loop_transcript(interpreter, script):
    directory = tempfile.mkdtemp(prefix="todo-loop-")
    try:
        run(interpreter, script, directory, ["init", "--here"])
        for args in LOOP_SETUP:
            run(interpreter, script, directory, args)
        for name, body in (("work-loop.local.md", LOOP_FILE), ("board-loop.local.md", BOARD_LOOP_FILE),
                           ("missing-loop.local.md", MISSING_LOOP_FILE)):
            with open(os.path.join(directory, ".claude", name), "w", encoding="utf-8") as handle:
                handle.write(body)
        said = [run(interpreter, script, directory, args, session) for session, args, _must, _never in LOOP_STEPS]
        created = os.path.exists(os.path.join(directory, "nowhere"))
        return said, created
    finally:
        shutil.rmtree(directory, ignore_errors=True)


# Test states, RE-173: done, tested and tested by a real user are three facts,
# each claim needs what proved it, and anything that changes what it proved
# clears it. Each step: the command, what it must print (one text, or several
# that must all appear), and what it must never print. A run reads
# "out:...err:...code:N", so a step can name its exit code too.
LONG_EXIT = "the page shows the saved value after a reload"


def task_args(task_id, title, *extra):
    return ["add", "--id", task_id, "--title", title, "--desc", f"the story of {title}", "--why", "w",
            "--severity", "high", "--points", "2", "--exit", f"{title}: {LONG_EXIT}", *extra]


TEST_STEPS = [
    (task_args("TS-001", "a feature"), "Added TS-001", None),
    # A board that never recorded a test reads exactly as it did.
    (["tests"], "This board records no test states yet.", "NOT TESTED"),
    (["show", "TS-001"], "TS-001  [high/2pt]  backlog", "tested:"),
    # Built is not tested, and nothing is tested before it is done.
    (["tested", "TS-001", "--evidence", "the area suite passed"], "TS-001 is backlog, not done. Nothing was recorded.\ncode:1", None),
    (["move", "TS-001", "in_progress"], "TS-001: backlog -> in_progress", None),
    (["tested", "TS-001", "--evidence", "the area suite passed"], "TS-001 is in_progress, not done. Nothing was recorded.\ncode:1", None),
    (["move", "TS-001", "wait_for_roast"], "TS-001: in_progress -> wait_for_roast", None),
    (["tested", "TS-001", "--evidence", "the area suite passed"], "TS-001 is wait_for_roast, not done. Nothing was recorded.\ncode:1", None),
    (["move", "TS-001", "done", "--evidence", "built and roasted"], "TS-001: wait_for_roast -> done", "tested"),
    (["show", "TS-001"], "  evidence: built and roasted", "tested:"),
    # No claim without what proved it, and no e2e before tested.
    (["tested", "TS-001"], 'tested TS-001 needs --evidence "...": what was run and what it showed. Nothing was recorded.\ncode:1', None),
    (["tested", "TS-001", "--evidence", " \t "], "tested TS-001 needs --evidence", "code:0"),
    # Blank is one set in both halves and the board: whatever either trims, and the byte order mark.
    (["tested", "TS-001", "--evidence", "\x85"], "tested TS-001 needs --evidence", "code:0"),
    (["tested", "TS-001", "--evidence", "﻿"], "tested TS-001 needs --evidence", "code:0"),
    (["tested", "TS-001", "--evidence", "\x1c　 \xa0"], "tested TS-001 needs --evidence", "code:0"),
    (["tested"], 'tested needs a task: todo tested SB-003 --evidence "what was run and what it showed"\ncode:1', None),
    (["tested", "--evidence", "the area suite passed"], "tested needs a task", "code:0"),
    (["tested", "TS-404", "--evidence", "the area suite passed"], "No task TS-404.\ncode:1", None),
    (["e2e", "TS-001", "--evidence", "the owner clicked through"], "TS-001 is not tested yet, and e2e comes after it. Nothing was recorded.\ncode:1", None),
    (["edit", "TS-001", "--tested", "1"], "Nothing to change.", "code:0"),
    (["untest", "TS-001", "--reason", "nothing to undo"], "TS-001: this board records no test states, so there is nothing to clear.\nerr:code:0", None),
    (["untest", "TS-404", "--reason", "nothing to undo"], "No task TS-404.\ncode:1", "records no test states"),
    (["tests"], "This board records no test states yet.", None),
    (["validate"], "Board is valid. 1 task(s).", "test state"),
    # The first claim that passes every check starts recording them.
    (["tested", "TS-001", "--evidence", "the area suite: 14 passed"],
     "TS-001: tested, the area suite: 14 passed\n  This board records test states from now on.", None),
    (["show", "TS-001"], "  tested: yes, the area suite: 14 passed\n  e2e   : no\n", None),
    (["tests"], "TESTED, NOT E2E TESTED (1)\n    TS-001  [high/2pt]  a feature\n      tested: the area suite: 14 passed\n", "DONE, NOT TESTED"),
    (["tested", "TS-001", "--evidence", "the area suite: 15 passed"], "TS-001: tested, the area suite: 15 passed\nerr:", "from now on"),
    (["e2e", "TS-001", "--evidence", "the owner saved and reloaded the page"], "TS-001: e2e tested, the owner saved and reloaded the page", None),
    (["tests"], "E2E TESTED (1)\n    TS-001  [high/2pt]  a feature\n      tested: the area suite: 15 passed\n      e2e   : the owner saved and reloaded the page\n", "TESTED, NOT E2E"),
    # What does not change the story keeps the proof.
    (["move", "TS-001", "done"], "TS-001: done -> done", "test state is cleared"),
    (["edit", "TS-001", "--title", "a feature, retitled"], "TS-001: title changed", "test state is cleared"),
    (["edit", "TS-001", "--desc", "the story of a feature"], "TS-001: desc changed", "test state is cleared"),
    (["edit", "TS-001", "--exit", f"a feature: {LONG_EXIT}"], "TS-001: exit changed", "test state is cleared"),
    (["edit", "TS-001", "--note", "a plain note"], "TS-001: note changed", "test state is cleared"),
    (["roast", "TS-001", "--file", "roasts/ts-001.md", "--filed", "none"], "TS-001 roast round 1", None),
    (["show", "TS-001"], "  tested: yes, the area suite: 15 passed\n  e2e   : yes, the owner saved and reloaded the page\n", "test state cleared"),
    (["render", "--out", "BOARD.md"], "rendered BOARD.md", None),
    (["render", "--out", "BOARD.md", "--check"], "BOARD.md is in sync with the board.", None),
    (["validate"], "Board is valid. 1 task(s).", None),
    # What changes the story clears it, keeping what it had in a note.
    (["edit", "TS-001", "--exit", "a feature: the page shows the saved value after two reloads"],
     "TS-001's test state is cleared: what it proved has changed. What it had is kept in a note.", None),
    (["show", "TS-001"],
     ("  tested: no\n  e2e   : no\n", "    - test state cleared: its exit condition changed; it had tested: the area suite: 15 passed;"
      " e2e tested: the owner saved and reloaded the page\n"), "tested: yes"),
    (["render", "--out", "BOARD.md", "--check"], "BOARD.md is stale", "code:0"),
    (["tested", "TS-001", "--evidence", "the suite, run again"], "TS-001: tested, the suite, run again\nerr:", None),
    (["edit", "TS-001", "--desc", "a changed story"], "TS-001's test state is cleared: what it proved has changed.", None),
    (["show", "TS-001"], "    - test state cleared: its description changed; it had tested: the suite, run again\n", "tested: yes"),
    (["tested", "TS-001", "--evidence", "the third run"], "TS-001: tested, the third run", None),
    # edit writes one field at a time, so the first changed field is the one named,
    # and the exit write that follows finds nothing left to clear. The whole notes
    # block, bounded by `notes:` and `roasts:`, proves the earlier history is kept
    # and that this edit added exactly one note, with the description's reason.
    (["edit", "TS-001", "--desc", "a twice changed story", "--exit", "a feature: the page shows the saved value after three reloads"],
     "TS-001's test state is cleared", None),
    (["show", "TS-001"],
     ("  tested: no\n  e2e   : no\n",
      "  notes:\n"
      "    - a plain note\n"
      "    - test state cleared: its exit condition changed; it had tested: the area suite: 15 passed;"
      " e2e tested: the owner saved and reloaded the page\n"
      "    - test state cleared: its description changed; it had tested: the suite, run again\n"
      "    - test state cleared: its description changed; it had tested: the third run\n"
      "  roasts:\n"), "tested: yes"),
    (["tested", "TS-001", "--evidence", "the fourth run"], "TS-001: tested, the fourth run", None),
    (["move", "TS-001", "backlog"],
     "TS-001: done -> backlog\n  TS-001's test state is cleared: it is backlog now. What it had is kept in a note.\n", None),
    (["show", "TS-001"], "    - test state cleared: status done -> backlog; it had tested: the fourth run\n", "tested: yes"),
    (["tests"], "Nothing is done yet.", "TS-001"),
    (["move", "TS-001", "done"], "TS-001: backlog -> done", "cleared"),
    (["tests"], "DONE, NOT TESTED (1)\n    TS-001  [high/2pt]  a feature, retitled\n", "TESTED, NOT"),
    (["show", "TS-001"], "  tested: no\n  e2e   : no\n", None),
    # Blocked or with an open finding, a task is not tested.
    (["move", "TS-001", "blocked", "--reason", "waiting on the owner"], "TS-001: done, blocked: waiting on the owner", None),
    (["tested", "TS-001", "--evidence", "the area suite passed"], "TS-001 is blocked: waiting on the owner. Nothing was recorded.\ncode:1", None),
    (["move", "TS-001", "done"], "  no longer blocked: waiting on the owner", None),
    (task_args("TS-002", "a finding", "--parent-task", "TS-001"), "Added TS-002", "test state is cleared"),
    (["tested", "TS-001", "--evidence", "the area suite passed"], "TS-001 has open findings: TS-002. Nothing was recorded.\ncode:1", None),
    (["move", "TS-002", "done"], "THAT WAS THE LAST ONE", None),
    (["tests"], "DONE, NOT TESTED (2)\n    TS-001  [high/2pt]  a feature, retitled\n    TS-002  [high/2pt]  a finding\n", None),
    # A tested child never marks its parent, and clearing a parent never clears its children.
    (["tested", "TS-002", "--evidence", "the finding's suite"], "TS-002: tested, the finding's suite", None),
    (["show", "TS-001"], "  tested: no\n", None),
    (["tested", "TS-001", "--evidence", "the parent's suite"], "TS-001: tested, the parent's suite", None),
    (["e2e", "TS-001", "--evidence", "the parent's path"], "TS-001: e2e tested, the parent's path", None),
    (task_args("TS-003", "a second finding", "--parent-task", "TS-001"),
     "  a child of TS-001, which now has 1 open child task(s).\n  TS-001's test state is cleared: it has an open finding now. What it had is kept in a note.", None),
    (["show", "TS-001"],
     "    - test state cleared: TS-003 is an open finding of it; it had tested: the parent's suite; e2e tested: the parent's path\n", "tested: yes"),
    (["show", "TS-002"], "  tested: yes, the finding's suite\n", None),
    (["move", "TS-003", "dropped", "--reason", "not a real finding"], "TS-003: backlog -> dropped", "cleared"),
    (["tested", "TS-001", "--evidence", "the parent's suite again"], "TS-001: tested, the parent's suite again", None),
    # A finding filed already finished clears nothing; one reopened clears the parent and itself.
    (task_args("TS-004", "a finding already fixed", "--parent-task", "TS-001", "--status", "done"), "Added TS-004", "cleared"),
    (["show", "TS-001"], "  tested: yes, the parent's suite again\n", None),
    (["move", "TS-002", "in_progress"],
     ("TS-002: done -> in_progress\n  TS-002's test state is cleared: it is in_progress now. What it had is kept in a note.\n"
      "  TS-001's test state is cleared: it has an open finding now. What it had is kept in a note.\n"), None),
    (["show", "TS-001"], "    - test state cleared: TS-002, a finding of it, is open again; it had tested: the parent's suite again\n", "tested: yes"),
    (["show", "TS-002"], "    - test state cleared: status done -> in_progress; it had tested: the finding's suite\n", "tested: yes"),
    (["move", "TS-002", "done"], "TS-002: in_progress -> done", None),
    (["tested", "TS-001", "--evidence", "after the reopened finding"], "TS-001: tested, after the reopened finding", None),
    # A grandchild hangs off the top parent, and clears it.
    (task_args("TS-005", "a grandchild", "--parent-task", "TS-002"),
     ("TS-002 is itself a child of TS-001, so this hangs off TS-001. One level.", "TS-001's test state is cleared"), None),
    (["move", "TS-005", "done"], "TS-005: backlog -> done", None),
    (["tested", "TS-001", "--evidence", "after the grandchild"], "TS-001: tested, after the grandchild", None),
    # Filing an open card under a tested task clears it too.
    (task_args("TS-006", "a loose card"), "Added TS-006", "cleared"),
    (["edit", "TS-006", "--parent-task", "TS-001"],
     "TS-006 is now a child of TS-001.\n  TS-001's test state is cleared: it has an open finding now. What it had is kept in a note.\n", None),
    (["show", "TS-001"], "    - test state cleared: TS-006 was filed under it as an open finding; it had tested: after the grandchild\n", "tested: yes"),
    (["edit", "TS-006", "--parent-task", ""], "TS-006 is no longer a child of anything.", None),
    # Clearing by hand says why, and keeps what it had.
    (["tested", "TS-001", "--evidence", "the suite before e2e"], "TS-001: tested, the suite before e2e", None),
    (["e2e", "TS-001", "--evidence", "the owner's path"], "TS-001: e2e tested, the owner's path", None),
    (["untest", "TS-001", "e2e"], 'untest TS-001 needs --reason "...": a cleared test state says why. Nothing was changed.\ncode:1', None),
    (["untest", "TS-001", "e2e", "--reason", "﻿\x85\x1f"], "untest TS-001 needs --reason", "code:0"),
    (["untest", "TS-001", "e2e", "--reason", "the page changed since"], "TS-001: e2e test state cleared. What it had is kept in a note.", None),
    (["show", "TS-001"],
     ("  tested: yes, the suite before e2e\n  e2e   : no\n",
      "    - test state cleared by hand: the page changed since; it had e2e tested: the owner's path\n"), None),
    (["untest", "TS-001", "e2e", "--reason", "again"], "TS-001 is not e2e tested, so there is nothing to clear.\nerr:code:0", None),
    (["e2e", "TS-001", "--evidence", "the owner's path, again"], "TS-001: e2e tested, the owner's path, again", None),
    (["untest", "TS-001", "--reason", "the suite was wrong"], "TS-001: test state cleared. What it had is kept in a note.", None),
    (["show", "TS-001"],
     ("  tested: no\n  e2e   : no\n",
      "    - test state cleared by hand: the suite was wrong; it had tested: the suite before e2e; e2e tested: the owner's path, again\n"), None),
    (["untest", "TS-001", "--reason", "again"], "TS-001 is not tested, so there is nothing to clear.\nerr:code:0", None),
    (["untest"], 'untest needs a task: todo untest SB-003 [e2e] --reason "why the proof no longer holds"\ncode:1', None),
    (["untest", "TS-404", "--reason", "again"], "No task TS-404.\ncode:1", None),
    # The report narrows to areas, and the board still holds together.
    (["tests", "--area", "nowhere"], "Nothing is done in area nowhere yet.", "TS-0"),
    (task_args("TS-007", "an api task", "--area", "api"), "Added TS-007", None),
    (["move", "TS-007", "done"], "TS-007: backlog -> done", None),
    # What is stored is the evidence without the blank around it, the same from either half.
    (["tested", "TS-007", "--evidence", "﻿ the api suite\x85　"], "TS-007: tested, the api suite\nerr:", None),
    (["tests", "--area", "api"], "TESTED, NOT E2E TESTED in area api (1)\n    TS-007  [high/2pt]  an api task\n      tested: the api suite\n", "TS-001"),
    (["validate"], "Board is valid. 7 task(s).", "test state"),
    (["render", "--out", "BOARD.md"], "rendered BOARD.md", None),
    (["render", "--out", "BOARD.md", "--check"], "BOARD.md is in sync with the board.", None),
    (["list"], "TS-006", "tested"),
    (["next"], ("TS-006", "  tested: no\n  e2e   : no\n"), None),
    (["frobnicate"],
     'err:Unknown command "frobnicate". Try: list, next, show, add, edit, set, move, phase, okr, roast, validate, render, rm.\ncode:1', None),
]

# One board, the two halves taking turns: whatever one wrote, the other reads
# and changes, guards included. Run once with each half going first; when they
# are the same tool the two runs print the same thing.
MIXED_STEPS = [
    (task_args("MX-001", "a shared task"), "Added MX-001", None),
    (["move", "MX-001", "done"], "MX-001: backlog -> done", None),
    (["tested", "MX-001", "--evidence", "run by one half"], "This board records test states from now on.", None),
    (["show", "MX-001"], "  tested: yes, run by one half\n  e2e   : no\n", None),
    (["e2e", "MX-001", "--evidence", "checked by the other half"], "MX-001: e2e tested, checked by the other half", None),
    (["tests"], "E2E TESTED (1)\n    MX-001", None),
    (task_args("MX-002", "a finding", "--parent-task", "MX-001"), "MX-001's test state is cleared", None),
    (["show", "MX-001"], "    - test state cleared: MX-002 is an open finding of it; it had tested: run by one half; e2e tested: checked by the other half\n", "tested: yes"),
    (["move", "MX-002", "done"], "THAT WAS THE LAST ONE", None),
    (["tested", "MX-001", "--evidence", "run again"], "MX-001: tested, run again\nerr:", "from now on"),
    (["render", "--out", "BOARD.md"], "rendered BOARD.md", None),
    (["render", "--out", "BOARD.md", "--check"], "BOARD.md is in sync with the board.", None),
    (["move", "MX-001", "backlog"], "MX-001's test state is cleared: it is backlog now.", None),
    (["show", "MX-001"], "    - test state cleared: status done -> backlog; it had tested: run again\n", "tested: yes"),
    (["validate"], "Board is valid. 2 task(s).", None),
]

# The tool's less travelled paths, on three synthetic projects: "My Project"
# and "sandbox", whose names give an empty board its id prefix, and "Checks",
# a board edited by hand with sqlite3 so validate has every problem to name.
# Each step: the project, the command, what it must print (one text, or
# several), what it must never print, and the session it runs in. A step
# ("setup", function, project) changes files the way a person or another tool
# would, between commands. Both halves run every step, and must agree byte for
# byte as well as say what they must.
EDGE_PROJECTS = {"main": "My Project", "other": "sandbox", "checks": "Checks"}


def edge_add(title, *extra):
    """An add with no --id, so the board picks the id."""
    return ["add", "--title", title, "--desc", f"the story of {title}", "--why", "w", "--severity", "high", "--points", "2",
            "--exit", f"{title}: {LONG_EXIT}", *extra]


def edge_loop_files(project):
    """Two names a loop file could have that are not loop files: a folder, and a file without front matter."""
    folder = os.path.join(project, ".claude")
    os.makedirs(os.path.join(folder, "x-loop.local.md"))
    with open(os.path.join(folder, "notes-loop.local.md"), "w", encoding="utf-8") as handle:
        handle.write("plain notes, no front matter\nsession: edge-session\n")


def edge_hand_edits(project):
    """What a hand edit can leave behind, written straight into the board."""
    writer = sqlite3.connect(os.path.join(project, ".claude", "todo.db"), isolation_level=None)
    try:
        for statement in (
            "INSERT INTO blocked_by (task, parent) VALUES ('VC-001', 'VC-404')",
            "INSERT INTO blocked_by (task, parent) VALUES ('VC-001', 'VC-002')",
            "INSERT INTO blocked_by (task, parent) VALUES ('VC-003', 'VC-002')",
            "UPDATE task SET phase = 'NOPE' WHERE id = 'VC-002'",
            "UPDATE task SET parent_task = 'VC-404' WHERE id = 'VC-004'",
            "UPDATE task SET parent_task = 'VC-006' WHERE id = 'VC-005'",
            "UPDATE task SET parent_task = 'VC-004' WHERE id = 'VC-006'",
            "INSERT INTO blocked_by (task, parent) VALUES ('VC-007', 'VC-008')",
            "INSERT INTO blocked_by (task, parent) VALUES ('VC-008', 'VC-007')",
        ):
            writer.execute(statement)
    finally:
        writer.close()


NOT_A_NUMBER = "roast: --score must be a number.\ncode:1"
EDGE_STEPS = [
    # init is told where, and never makes a second board.
    ("main", ["init"], "todo init needs to be told where: `todo init --here`, or `todo init <path>`.\ncode:2", None, None),
    ("main", ["init", "--here"], "Started a new board at <sandbox>", None, None),
    ("main", ["init", "--here"], ("There is already a board at <sandbox>", "code:2"), "Started", None),
    # An empty board's first id comes from its project folder: its capitals, or its first two letters.
    # An id with no number in it is passed over when the next one is worked out.
    ("main", edge_add("first"), "Added MP-001: first\n", None, None),
    ("main", task_args("X-ALPHA", "an id with no number"), "Added X-ALPHA: an id with no number\n", None, None),
    ("main", edge_add("second"), "Added MP-002: second\n", None, None),
    ("other", ["init", "--here"], "Started a new board", None, None),
    ("other", edge_add("in the sandbox"), "Added SA-001: in the sandbox\n", None, None),
    # A folder with a loop file's name, and a file with no front matter, are both passed over.
    ("setup", edge_loop_files, "main"),
    ("main", ["list"], ("MP-001  [high/2pt]  first", "X-ALPHA"), "not one of this session's areas", "edge-session"),
    # add checks what it is given before it writes anything.
    ("main", edge_add("bad severity", "--severity", "urgent"), "severity must be one of critical, high, medium, low\ncode:1", "Added", None),
    ("main", edge_add("bad points", "--points", "4"), "points must be one of 1, 2, 3, 5, 8, 13\ncode:1", "Added", None),
    ("main", edge_add("unknown blocker", "--parent", "MP-404"),
     "No such task: MP-404. A parent that does not exist can never be done, so the task would never become eligible", "Added", None),
    ("main", edge_add("unknown origin", "--parent-task", "MP-404"), "No task MP-404 to hang this off.\ncode:1", "Added", None),
    # edit: objectives, its own parent, a parent that was never tested, and refused values.
    ("main", ["edit", "MP-001", "--okr", "OKR-77"],
     'No objective called OKR-77. Add it first: todo okr add --id OKR-77 --name "..." --description "..."\ncode:1', None, None),
    ("main", ["edit", "MP-001", "--okr", "none"], "MP-001: phase changed\n", None, None),
    ("main", ["edit", "MP-001", "--parent-task", "MP-001"], "MP-001 cannot be its own parent.\ncode:1", None, None),
    ("main", ["edit", "MP-002", "--parent-task", "MP-001"], "MP-002 is now a child of MP-001.\n", "test state is cleared", None),
    ("main", ["edit", "MP-001", "--severity", "urgent"], "severity must be one of critical, high, medium, low\ncode:1", "changed", None),
    ("main", ["edit", "MP-001", "--points", "4"], "points must be one of 1, 2, 3, 5, 8, 13\ncode:1", "changed", None),
    # edit --parent: set, refused when unknown or when it would make a cycle, through a shared blocker, and cleared.
    ("main", ["edit", "MP-001", "--parent", "X-ALPHA"], ("MP-001: parent changed\n", "  after: X-ALPHA\n"), None, None),
    ("main", ["edit", "MP-001", "--parent", "MP-404"], "No such task: MP-404. A parent that does not exist can never be done.\ncode:1", None, None),
    ("main", ["edit", "X-ALPHA", "--parent", "MP-001"],
     "That parent makes a cycle: X-ALPHA would wait on itself, through MP-001.\ncode:1", None, None),
    ("main", ["edit", "MP-002", "--parent", "X-ALPHA"], ("MP-002: parent changed\n", "  after: X-ALPHA\n"), None, None),
    ("main", task_args("MP-003", "two blockers sharing one"), "Added MP-003", None, None),
    ("main", ["edit", "MP-003", "--parent", "MP-001,MP-002"], ("MP-003: parent changed\n", "  after: MP-001, MP-002\n"), "cycle", None),
    ("main", ["edit", "MP-001", "--parent", ""], ("MP-001: parent changed\n", "  after: nothing\n"), None, None),
    # Direct self-cycle keeps the live empty-seen diagnostic. A replacement
    # keeps exactly the requested blocker, not the previous shared graph.
    ("main", ["edit", "MP-001", "--parent", "MP-001"],
     "That parent makes a cycle: MP-001 would wait on itself, through MP-001.\ncode:1", None, None),
    ("main", ["edit", "MP-003", "--parent", "MP-002"],
     ("MP-003: parent changed\n", "  after: MP-002\n"), "  after: MP-001, MP-002", None),
    ("main", ["edit", "MP-003", "--parent-task", "   "],
     "MP-003 is no longer a child of anything.\n", "is now a child", None),
    # Closing a task with an open finding says so, and closes it.
    ("main", ["move", "MP-001", "done"], ("MP-001: backlog -> done\n", "MP-001 still has 1 open child task(s): MP-002\n"), None, None),
    # roast: a score or criticals that are not a finite number, a dismissal, and a whole score shown whole.
    ("main", ["roast", "MP-001", "--file", "r1.md", "--score", ""], NOT_A_NUMBER, None, None),
    ("main", ["roast", "MP-001", "--file", "r1.md", "--score", "nan"], NOT_A_NUMBER, None, None),
    ("main", ["roast", "MP-001", "--file", "r1.md", "--score", "inf"], NOT_A_NUMBER, None, None),
    ("main", ["roast", "MP-001", "--file", "r1.md", "--criticals", "many"], "roast: --criticals must be a number.\ncode:1", None, None),
    ("main", ["roast", "MP-001", "--file", "r1.md", "--score", "8", "--dismissed", "a style nit"],
     ("MP-001 roast round 1\n", "  Now judge it"), None, None),
    ("main", ["show", "MP-001"], "    round 1: r1.md, score 8, not judged yet, dismissed: a style nit\n", "8.0", None),
    ("main", ["validate"], "1 task(s) have a roast round nobody recorded as judged: MP-001.\n", None, None),
    # Objectives: a taken id, positions that are not whole numbers from 1, and okr edit with nothing to do.
    ("main", ["okr", "add", "--name", "First", "--description", "the first"], "Added OKR-1 First at position 1.\n", None, None),
    ("main", ["okr", "add", "--id", "OKR-1", "--name", "Again", "--description", "d"], "okr add: OKR-1 already exists.\ncode:1", None, None),
    ("main", ["okr", "add", "--name", "Zero", "--description", "d", "--position", "0"],
     "okr add: --position is a whole number from 1, the order they are met in.\ncode:1", None, None),
    ("main", ["okr", "add", "--name", "Text", "--description", "d", "--position", "x"],
     "okr add: --position is a whole number from 1, the order they are met in.\ncode:1", None, None),
    ("main", ["okr", "add", "--name", "Half", "--description", "d", "--position", "1.5"],
     "okr add: --position is a whole number from 1, the order they are met in.\ncode:1", None, None),
    ("main", ["okr", "edit"], "okr edit: (no id) is not an objective this board holds.\ncode:1", None, None),
    ("main", ["okr", "edit", "OKR-1"], "okr edit: nothing to change. Pass --name, --description or --position.\ncode:1", None, None),
    ("main", ["okr", "edit", "OKR-1", "--position", "0"], "okr edit: --position is a whole number from 1.\ncode:1", None, None),
    ("main", ["phase", "add", "NEXT"], 'A phase needs a name and what it is for: todo phase add MVP --goal "..."\ncode:1', None, None),
    # list by one status leaves out the blocked tasks.
    ("main", ["move", "X-ALPHA", "blocked", "--reason", "waiting on the owner"], "X-ALPHA: backlog, blocked: waiting on the owner\n", None, None),
    ("main", ["list", "--status", "backlog"], ("BACKLOG (2)", "MP-003"), "BLOCKED", None),
    # A later objective, read by okr, phase, list and render, on a board where every open task has one.
    ("other", ["okr", "add", "--name", "First", "--description", "ship it"], "Added OKR-1 First at position 1.\n", None, None),
    ("other", ["edit", "SA-001", "--okr", "OKR-1"], "SA-001: phase changed\n", None, None),
    ("other", ["okr", "add", "--id", "LATER", "--name", "Later", "--description", "what follows"], "Added LATER Later at position 2.\n", None, None),
    ("other", edge_add("for later", "--okr", "LATER"), "Added SA-002: for later\n", None, None),
    ("other", ["okr"], ("1. OKR-1 First (now): 1 open, 0 done, 1 in all\n", "2. LATER Later (later): 1 open, 0 done, 1 in all\n"),
     "serve no objective", None),
    ("other", ["phase"], ("  1. OKR-1 (now): 1 open, 0 done\n", "  2. LATER (later): 1 open, 0 done\n"), "in no phase", None),
    ("other", ["list"], "    SA-002  [high/2pt]  for later  · LATER\n", "in no phase", None),
    ("other", ["render", "--out", "BOARD.md"], "rendered BOARD.md\n", None, None),
    ("other", ["render", "--out", "BOARD.md", "--check"], "BOARD.md is in sync with the board.\n", None, None),
    # next, when everything left is blocked, names each block.
    ("other", ["move", "SA-001", "blocked", "--reason", "the owner decides"], "SA-001: backlog, blocked: the owner decides\n", None, None),
    ("other", ["move", "SA-002", "blocked", "--reason", "after SA-001"], "SA-002: backlog, blocked: after SA-001\n", None, None),
    ("other", ["next"], "out:2 task(s) blocked:\n  SA-001: the owner decides\n  SA-002: after SA-001\nerr:code:0", "Nothing", None),
    # validate names every problem a hand edit left.
    ("checks", ["init", "--here"], "Started a new board", None, None),
    ("checks", task_args("VC-001", "a severe task"), "Added VC-001", None, None),
    ("checks", task_args("VC-002", "a minor blocker", "--severity", "low"), "Added VC-002", None, None),
    ("checks", task_args("VC-003", "done early"), "Added VC-003", None, None),
    ("checks", ["move", "VC-003", "done"], "VC-003: backlog -> done", None, None),
    ("checks", task_args("VC-004", "an orphaned finding"), "Added VC-004", None, None),
    ("checks", task_args("VC-005", "a finding of a finding"), "Added VC-005", None, None),
    ("checks", task_args("VC-006", "a finding"), "Added VC-006", None, None),
    ("checks", task_args("VC-007", "in a cycle"), "Added VC-007", None, None),
    ("checks", task_args("VC-008", "in the same cycle"), "Added VC-008", None, None),
    ("setup", edge_hand_edits, "checks"),
    ("checks", ["validate"],
     ("  - VC-001: its blocker VC-404 does not exist\n",
      "  - VC-001 (high) waits on VC-002 (low), which is less severe, so next would never pick it first\n",
      "  - VC-002: its phase NOPE does not exist\n",
      "  - VC-003 is done, but its blocker VC-002 is backlog\n",
      "  - VC-004: it came out of VC-404, which does not exist\n",
      "  - VC-005: it came out of VC-006, which itself came out of VC-004; one level only\n",
      "  - VC-006: it came out of VC-004, which itself came out of VC-404; one level only\n",
      "  - a cycle: VC-007 -> VC-008 -> VC-007\n",
      "code:1"), "Board is valid", None),
]


def edge_transcript(half):
    """Every EDGE_STEPS step through one half, each project in a fresh folder of its own."""
    base = tempfile.mkdtemp(prefix="todo-edge-")
    projects = {key: os.path.join(base, name) for key, name in EDGE_PROJECTS.items()}
    try:
        for folder in projects.values():
            os.makedirs(folder)
        said = []
        for step in EDGE_STEPS:
            if step[0] == "setup":
                step[1](projects[step[2]])
                continue
            key, args, _must, _never, session = step
            said.append(run(*half, projects[key], args, session))
        return said
    finally:
        shutil.rmtree(base, ignore_errors=True)


def python_without_reconfigure():
    """todo.py run in-process with streams that cannot be reconfigured prints exactly what a real run prints.

    io.StringIO has no reconfigure(), as with any captured stream, so the
    start-up's attempt to set UTF-8 and a bare newline is passed over. The
    real run of the same command on the same board is the expected output.
    Returns what went wrong.
    """
    directory, _board = made_board(PYTHON, "todo-inprocess-", [task_args("IP-001", "an in-process read")])
    try:
        expected = run(*PYTHON, directory, ["list"])
        out, err = io.StringIO(), io.StringIO()
        saved = (sys.argv, sys.stdout, sys.stderr, os.getcwd())
        try:
            sys.argv, sys.stdout, sys.stderr = [PY_SCRIPT, "list"], out, err
            os.chdir(directory)
            namespace = runpy.run_path(PY_SCRIPT, run_name="__main__")
            namespace["db"].close()
        finally:
            sys.argv, sys.stdout, sys.stderr = saved[:3]
            os.chdir(saved[3])
        said = f"out:{out.getvalue()}err:{err.getvalue()}code:0"
        return [] if said == expected else [f"in-process output {said!r} is not the real run's {expected!r}"]
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def node_without_sqlite():
    """todo.mjs on a Node that has node:sqlite switched off says what it needs, exits 1 and prints nothing else.

    --no-experimental-sqlite is Node's own switch for it, from v22.6.0. Returns what went wrong.
    """
    directory, _board = made_board(NODE, "todo-nosqlite-", [])
    try:
        version = subprocess.run([NODE_EXE, "--version"], capture_output=True, text=True).stdout.strip()
        done = subprocess.run([NODE_EXE, "--no-experimental-sqlite", NODE_SCRIPT, "list"], cwd=directory,
                              capture_output=True, env=child_env())
        expected = (f"This board needs node:sqlite, and this is Node {version}.\n"
                    "Node 22.13 or newer, or 23.4 or newer, has it built in. From 22.5 to 22.12, or 23.0 to 23.3,"
                    " run with --experimental-sqlite.\n"
                    "There is nothing to install: SQLite ships inside Node, and this script has no dependencies.\n")
        said = (done.returncode, done.stdout.decode("utf-8"), done.stderr.decode("utf-8"))
        return [] if said == (1, "", expected) else [f"with node:sqlite switched off it said {said!r}"]
    finally:
        shutil.rmtree(directory, ignore_errors=True)


NODE = (NODE_EXE, NODE_SCRIPT)
PYTHON = (sys.executable, PY_SCRIPT)


def steps_transcript(prefix, steps, halves):
    """Every step on one fresh board, step i through halves[i % len(halves)]."""
    directory = tempfile.mkdtemp(prefix=prefix)
    try:
        run(*halves[0], directory, ["init", "--here"])
        return [run(*halves[index % len(halves)], directory, args) for index, (args, _must, _never) in enumerate(steps)]
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def wrong(said, must, never):
    """Why a step's output is wrong, or None when it is right."""
    for text in must if isinstance(must, tuple) else (must,):
        if text not in said:
            return f"must say {text!r}"
    if never is not None and never in said:
        return f"must never say {never!r}"
    return None


def board_files(board):
    """The board and the files SQLite keeps beside it, each by its content."""
    out = []
    for suffix in ("", "-wal", "-shm", "-journal"):
        path = board + suffix
        out.append((suffix, hashlib.sha256(open(path, "rb").read()).hexdigest() if os.path.exists(path) else None))
    return out


# Each: the command, its exit code, and whether it refuses inside its own
# transaction, after taking the board's write lock, and so closes the board.
REFUSALS = (
    (["tested", "RF-001", "--evidence", "the suite"], 1, True),
    (["tested", "RF-002"], 1, False),
    (["tested", "RF-002", "--evidence", " \t "], 1, False),
    (["e2e", "RF-002", "--evidence", "the owner's path"], 1, True),
    (["tested", "RF-003", "--evidence", "the suite"], 1, True),
    (["tested", "RF-005", "--evidence", "the suite"], 1, True),
    (["tested", "RF-404", "--evidence", "the suite"], 1, True),
    (["edit", "RF-002", "--tested", "1"], 1, False),
    (["untest", "RF-002"], 1, False),
    (["untest", "RF-002", "--reason", "nothing to undo"], 0, False),
    (["untest", "RF-404", "--reason", "nothing to undo"], 1, False),
    (["tests"], 0, False),
    (["show", "RF-002"], 0, False),
    (["list"], 0, False),
    (["next"], 0, False),
    (["okr"], 0, False),
    (["phase"], 0, False),
    (["validate"], 1, False),
    (["render", "--check"], 1, False),
)


def settle(board, journal=None):
    """Open and close the board as any reader would, first setting its journal mode when one is given.

    Closing the last connection folds back and removes the -wal and -shm files
    a process left by exiting without closing; with an empty WAL nothing is
    written to the board doing so.
    """
    connection = sqlite3.connect(board, isolation_level=None)
    try:
        if journal:
            connection.execute(f"PRAGMA journal_mode={journal}").fetchone()
    finally:
        connection.close()


def file_hash(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def refusals_leave_the_board(half, journal):
    """Every refused claim and every read leaves the board's bytes as they were, and adds no column.

    A refusal now takes the board's write lock before it refuses, so this runs
    on a board in rollback-journal mode ("delete") and on one in WAL mode.
    In rollback-journal mode the board and every file beside it are hashed
    before and after each command and must not change. In WAL mode the board
    file must not change and the -wal file must hold nothing, so no page was
    written; a refusal made inside its transaction closes the board, so it must
    leave no -wal or -shm at all. Returns what went wrong.
    """
    directory, board = made_board(half, f"todo-refusals-{journal}-", [
        task_args("RF-001", "a backlog task"),
        task_args("RF-002", "a done task"), ["move", "RF-002", "done"],
        task_args("RF-003", "a done task with a finding"), ["move", "RF-003", "done"],
        task_args("RF-004", "its finding", "--parent-task", "RF-003"),
        task_args("RF-005", "a done task that is blocked"), ["move", "RF-005", "done"],
        ["move", "RF-005", "blocked", "--reason", "waiting on the owner"],
        ["list"],
    ])
    problems = []
    try:
        settle(board, journal)
        for args, code, inside in REFUSALS:
            label = f"todo {' '.join(args)} ({journal} mode)"
            if journal == "wal":
                settle(board)
            before = board_files(board) if journal == "delete" else file_hash(board)
            said = run(*half, directory, args)
            if not said.endswith(f"code:{code}"):
                problems.append(f"{label} should exit {code}: {said!r}")
            if journal == "delete":
                if board_files(board) != before:
                    problems.append(f"{label} changed the board or a file beside it")
                continue
            if file_hash(board) != before:
                problems.append(f"{label} changed the board file")
            if os.path.exists(board + "-wal") and os.path.getsize(board + "-wal"):
                problems.append(f"{label} wrote {os.path.getsize(board + '-wal')} bytes to the WAL")
            if inside and (os.path.exists(board + "-wal") or os.path.exists(board + "-shm")):
                problems.append(f"{label} refused inside its transaction but left -wal or -shm behind")
        reader = sqlite3.connect(board, isolation_level=None)
        try:
            mode = reader.execute("PRAGMA journal_mode").fetchone()[0]
            columns = [row[1] for row in reader.execute("PRAGMA table_info(task)").fetchall()]
        finally:
            reader.close()
        if mode != journal:
            problems.append(f"the board is in {mode} mode after the commands, not {journal}")
        if "tested" in columns:
            problems.append(f"a refused claim added the test-state columns ({journal} mode)")
        return problems
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def guards_hold(half):
    """The guards live in the board, so a writer that has never heard of test states meets them too.

    sqlite3 here stands for that writer: an older copy of either half, or a
    hand edit. Returns what went wrong.
    """
    directory = tempfile.mkdtemp(prefix="todo-guards-")
    board = os.path.join(directory, ".claude", "todo.db")
    problems = []
    try:
        for args in (
            ["init", "--here"],
            task_args("GD-001", "a tested task"), ["move", "GD-001", "done"],
            task_args("GD-002", "a second tested task"), ["move", "GD-002", "done"],
            task_args("GD-003", "a backlog task"),
            task_args("GD-004", "a third tested task"), ["move", "GD-004", "done"],
            task_args("GD-005", "a done task with an open finding"), ["move", "GD-005", "done"],
            task_args("GD-006", "its open finding", "--parent-task", "GD-005"),
            ["tested", "GD-001", "--evidence", "the suite"],
            ["tested", "GD-002", "--evidence", "the suite"],
            ["tested", "GD-004", "--evidence", "the suite"],
            ["e2e", "GD-004", "--evidence", "the owner's path"],
        ):
            run(*half, directory, args)
        writer = sqlite3.connect(board, isolation_level=None)
        try:
            # An open finding filed before the task it names exists, as a hand edit could leave one.
            writer.execute(
                "INSERT INTO task (id, title, descr, why, severity, points, status, exit_cond, parent_task)"
                " VALUES ('GD-012', 't', 'd', 'w', 'low', 1, 'backlog', 'e', 'GD-011')")
            for label, statement in (
                ("evidence that is only a byte order mark", "UPDATE task SET tested_how = char(65279) WHERE id = 'GD-001'"),
                ("evidence that is only a next-line and a file separator", "UPDATE task SET tested_how = char(133, 28) WHERE id = 'GD-001'"),
                ("a claim newly made while a finding is open", "UPDATE task SET tested = 1, tested_how = 'the suite' WHERE id = 'GD-005'"),
                ("a tested task inserted with an open finding already under it",
                 "INSERT INTO task (id, title, descr, why, severity, points, status, exit_cond, tested, tested_how)"
                 " VALUES ('GD-011', 't', 'd', 'w', 'low', 1, 'done', 'e', 1, 'the suite')"),
                ("blank evidence", "UPDATE task SET tested_how = ' ' WHERE id = 'GD-001'"),
                ("a flag that is not 0 or 1", "UPDATE task SET tested = 2 WHERE id = 'GD-001'"),
                ("e2e with no evidence", "UPDATE task SET e2e_tested = 1 WHERE id = 'GD-001'"),
                ("e2e before tested", "UPDATE task SET tested = 0, e2e_tested = 1, e2e_how = 'a path' WHERE id = 'GD-001'"),
                ("a backlog task claimed tested", "UPDATE task SET tested = 1, tested_how = 'the suite' WHERE id = 'GD-003'"),
                ("a tested backlog task inserted",
                 "INSERT INTO task (id, title, descr, why, severity, points, status, exit_cond, tested, tested_how)"
                 " VALUES ('GD-009', 't', 'd', 'w', 'low', 1, 'backlog', 'e', 1, 'the suite')"),
                ("a tested done task inserted with no evidence",
                 "INSERT INTO task (id, title, descr, why, severity, points, status, exit_cond, tested)"
                 " VALUES ('GD-010', 't', 'd', 'w', 'low', 1, 'done', 'e', 1)"),
            ):
                try:
                    writer.execute(statement)
                    problems.append(f"{label} was accepted")
                except sqlite3.DatabaseError as error:
                    if "a test state needs a done task" not in str(error):
                        problems.append(f"{label} failed for another reason: {error}")
            # Changes that leave the story alone keep the proof.
            writer.execute("UPDATE task SET title = 'renamed', descr = descr, exit_cond = exit_cond WHERE id = 'GD-004'")
            if writer.execute("SELECT tested FROM task WHERE id = 'GD-004'").fetchone() != (1,):
                problems.append("a title change or a no-op edit cleared a test state")
            # An older writer moving or rewriting a tested task leaves no stale claim.
            for task_id, statement, said in (
                ("GD-001", "UPDATE task SET status = 'backlog' WHERE id = 'GD-001'",
                 "test state cleared: status done -> backlog; it had tested: the suite"),
                ("GD-002", "UPDATE task SET descr = 'another story', exit_cond = 'another exit' WHERE id = 'GD-002'",
                 "test state cleared: its description and exit condition changed; it had tested: the suite"),
            ):
                writer.execute(statement)
                state = writer.execute(
                    "SELECT tested, tested_how, tested_at, e2e_tested, e2e_how, e2e_at FROM task WHERE id = ?", (task_id,)
                ).fetchone()
                if state != (0, None, None, 0, None, None):
                    problems.append(f"{statement} left {state}")
                note = writer.execute("SELECT at, text FROM note WHERE task = ? ORDER BY rowid DESC LIMIT 1", (task_id,)).fetchone()
                if note is None or note[1] != said:
                    problems.append(f"{statement} left the note {note!r}")
                elif not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", note[0]):
                    problems.append(f"the guard's note is stamped {note[0]!r}, not the way both halves write a moment")
            # The open-finding guard applies to a claim newly made, not to one that
            # already stood: clearing e2e by hand must keep working. Only a board
            # missing the finding guard can hold a tested task with an open finding,
            # so this board loses that one guard to reach the state.
            writer.execute("DROP TRIGGER todo_test_finding_added")
            writer.execute(
                "INSERT INTO task (id, title, descr, why, severity, points, status, exit_cond, parent_task)"
                " VALUES ('GD-013', 't', 'd', 'w', 'low', 1, 'backlog', 'e', 'GD-004')")
            try:
                writer.execute("UPDATE task SET e2e_tested = 0, e2e_how = NULL, e2e_at = NULL WHERE id = 'GD-004'")
            except sqlite3.DatabaseError as error:
                problems.append(f"clearing e2e on a task whose claim already stood was refused: {error}")
            try:
                writer.execute("UPDATE task SET e2e_tested = 1, e2e_how = 'the owner again' WHERE id = 'GD-004'")
                problems.append("an e2e claim newly made while a finding is open was accepted")
            except sqlite3.DatabaseError as error:
                if "a test state needs a done task" not in str(error):
                    problems.append(f"an e2e claim newly made while a finding is open failed for another reason: {error}")
        finally:
            writer.close()
        return problems
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def child_env():
    return {key: value for key, value in os.environ.items() if key not in SESSION_VARIABLES}


def made_board(half, prefix, setup, *, fixed_project=False):
    """A fresh board; equal project basenames preserve whole-file render parity."""
    owned = tempfile.mkdtemp(prefix=prefix)
    directory = os.path.join(owned, "project") if fixed_project else owned
    try:
        os.makedirs(directory, exist_ok=True)
        for args in (["init", "--here"], *setup):
            said = run(*half, directory, args)
            if not said.endswith("code:0"):
                raise RuntimeError(f"fixture setup failed: {said}")
        return directory, os.path.join(directory, ".claude", "todo.db")
    except BaseException:
        shutil.rmtree(owned, ignore_errors=True)
        raise


def remove_fixed_project(directory):
    """Remove the unique owned parent of a fixed-name fixture project."""
    shutil.rmtree(os.path.dirname(directory), ignore_errors=True)


def read_only(board, sql, params=()):
    reader = sqlite3.connect(f"{Path(board).as_uri()}?mode=ro", uri=True)
    try:
        return reader.execute(sql, params).fetchall()
    finally:
        reader.close()


def migrated_schema(half):
    """Everything sqlite_master holds once one half's first claim has given a board test states, guards' text included."""
    directory, board = made_board(half, "todo-schema-", [
        task_args("SC-001", "a task"), ["move", "SC-001", "done"],
        ["tested", "SC-001", "--evidence", "the suite"],
    ])
    try:
        return read_only(board, "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name")
    finally:
        shutil.rmtree(directory, ignore_errors=True)


# A synthetic planted fault, the control the race scenarios must catch: the
# claim's eligibility read moved before its lock. It is not a historical
# version of either half; it is made from today's source by exact anchors, each
# of which must occur exactly once, so a source these anchors do not recognise
# is refused rather than loosely patched. The copy is written into the
# scenario's own temporary folder, outside every measured root.
PLANTED_FAULT = "PLANTED FAULT: synthetic control, eligibility read before the lock; not a historical version"
PLANTED_ANCHORS = {
    ".py": (
        '    stamp = now()\n'
        '    try:\n'
        '        db.execute("BEGIN IMMEDIATE")\n'
        '        problem = claim_problem(task_id, command)\n'
        '        if problem is not None:\n'
        '            give_up(problem)\n'
        '        added = start_test_states() if command == "tested" else False\n',
        '    stamp = now()\n'
        '    problem = claim_problem(task_id, command)\n'
        '    if problem is not None:\n'
        '        give_up(problem)\n'
        '    try:\n'
        '        db.execute("BEGIN IMMEDIATE")\n'
        '        added = start_test_states() if command == "tested" else False\n',
        "# ",
    ),
    ".mjs": (
        "  let open = false\n"
        "  try {\n"
        "    db.exec('BEGIN IMMEDIATE')\n"
        "    open = true\n"
        "    const problem = claimProblem(id, command)\n"
        "    if (problem !== null) giveUp(problem, open)\n"
        "    added = command === 'tested' ? startTestStates() : false\n",
        "  let open = false\n"
        "  const problem = claimProblem(id, command)\n"
        "  if (problem !== null) giveUp(problem, open)\n"
        "  try {\n"
        "    db.exec('BEGIN IMMEDIATE')\n"
        "    open = true\n"
        "    added = command === 'tested' ? startTestStates() : false\n",
        "// ",
    ),
}


def planted_check_before_lock(half, directory):
    """This half with the planted fault, written into `directory`; refuses a source whose anchor is not there exactly once."""
    interpreter, script = half
    suffix = Path(script).suffix
    anchor, planted, comment = PLANTED_ANCHORS[suffix]
    text = Path(script).read_bytes().decode("utf-8")
    found = text.count(anchor)
    if found != 1:
        raise RuntimeError(f"the planted fault's anchor occurs {found} time(s) in {script}, not once: an unrecognised source is refused")
    first, rest = text.split("\n", 1)  # the first line is the #! line, which Node requires to stay first
    path = Path(directory) / f"planted-check-before-lock{suffix}"
    path.write_bytes(f"{first}\n{comment}{PLANTED_FAULT}\n{rest.replace(anchor, planted)}".encode("utf-8"))
    return (interpreter, str(path))


# Each: what another session files while a claim waits, the statement that
# files it, the real tool's refusal, and what a check-before-lock claim does.
RACE_VARIANTS = (
    ("an open finding filed meanwhile",
     "INSERT INTO task (id, title, descr, why, severity, points, status, exit_cond, parent_task)"
     " VALUES ('RC-009', 'a finding filed meanwhile', 'd', 'w', 'high', 1, 'backlog', 'an exit long enough to check', 'RC-001')",
     "RC-001 has open findings: RC-009. Nothing was recorded.",
     "guarded"),
    ("a block filed meanwhile",
     "INSERT INTO blocked (task, reason, since) VALUES ('RC-001', 'held by another session', '2026-10-04T00:00:00.000Z')",
     "RC-001 is blocked: held by another session. Nothing was recorded.",
     "recorded"),
)


def claim_race(half, statement, planted=False):
    """One claim racing another session that holds the write lock with `statement` filed and not yet committed.

    The claim starts only after that session has its lock. The claim's
    start-up is measured first, as a plain read of the same board by the same
    claimant, and the session holds its lock for three times that, at least one
    second and at most 3.5, inside the claim's five-second busy wait. It then
    records whether the claim is still running, so waiting at the lock, and only
    then commits. Returns what happened; `slow` is set instead when start-up is
    too slow for that margin.
    """
    directory, board = made_board(half, "todo-race-", [task_args("RC-001", "a task"), ["move", "RC-001", "done"]])
    try:
        claimant = planted_check_before_lock(half, directory) if planted else half
        started = time.monotonic()
        run(*claimant, directory, ["show", "RC-001"])
        margin = max(1.0, 3 * (time.monotonic() - started))
        if margin > 3.5:
            return {"slow": margin / 3}
        writer = sqlite3.connect(board, isolation_level=None)
        try:
            writer.execute("BEGIN IMMEDIATE")
            writer.execute(statement)
            claim = subprocess.Popen([*claimant, "tested", "RC-001", "--evidence", "the suite"], cwd=directory,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=child_env())
            try:
                claim.wait(timeout=margin)
                waited = False
            except subprocess.TimeoutExpired:
                waited = True  # still running: waiting at the lock
            writer.execute("COMMIT")
        finally:
            writer.close()
        out, err = claim.communicate(timeout=120)
        columns = "tested" in [row[1] for row in read_only(board, "PRAGMA table_info(task)")]
        tested = read_only(board, "SELECT tested FROM task WHERE id = 'RC-001'")[0][0] if columns else None
        return {"slow": None, "margin": round(margin, 2), "waited": waited, "code": claim.returncode,
                "out": out.decode("utf-8"), "err": err.decode("utf-8"), "columns": columns, "tested": tested}
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def claims_race_safely(half):
    """A finding or a block another session files while a claim waits for the board is seen by the claim.

    Every check of a claim runs inside its own BEGIN IMMEDIATE, so a claim that
    waited at the lock checks only after the other session committed: the real
    tool refuses, in its own words, and the board gains no test-state column.

    Each race is also run against a planted fault (planted_check_before_lock),
    a synthetic copy that reads eligibility before the lock, and it must show:
      * a block filed meanwhile: the planted copy records the claim, so the
        board ends with a tested task that is blocked. No guard covers blocks.
      * an open finding filed meanwhile: the planted copy is still refused,
        because the claim guard it creates in the same transaction, before its
        UPDATE, refuses a claim newly made while a finding is open. Only the
        words change: the guard's "could not take the claim: a test state
        needs ..." instead of "has open findings". So the control requires the
        guard's words and that the refusal's own words are absent.
    A planted copy that did not wait at the lock, or that still gave the real
    refusal, saw the other session's commit before it checked: the race was
    not exercised on this run, and that is a failure, never counted as proof.
    Returns what went wrong.
    """
    problems = []
    for label, statement, refusal, planted_shows in RACE_VARIANTS:
        real = claim_race(half, statement)
        if real["slow"]:
            problems.append(f"{label}: a plain read took {real['slow']:.2f} s, too slow to hold the lock that long inside the busy wait")
            continue
        if not real["waited"]:
            problems.append(f"{label}: the claim ended within {real['margin']} s while another session held the write lock")
        if real["code"] != 1 or refusal not in real["err"] or real["columns"]:
            problems.append(f"{label}: the claim was not refused as it must be: {real!r}")
        control = claim_race(half, statement, planted=True)
        label = f"{label}, planted fault (synthetic control)"
        if control["slow"]:
            problems.append(f"{label}: a plain read took {control['slow']:.2f} s, too slow to exercise the race; the control is not proof")
        elif not control["waited"] or refusal in control["err"]:
            problems.append(f"{label}: the race scenario did not exercise the lock on this run (startup, not waiting): {control!r}")
        elif planted_shows == "recorded" and control["tested"] != 1:
            problems.append(f"{label}: a check before the lock did not record the claim on the blocked task: {control!r}")
        elif planted_shows == "guarded" and (control["code"] != 1 or control["columns"]
                                             or "could not take the claim: a test state needs a done task with no open finding" not in control["err"]):
            problems.append(f"{label}: the guard did not refuse the check-before-lock claim as documented: {control!r}")
    return problems


def a_busy_board_refuses_cleanly(half):
    """A claim or a clear that cannot have the board says so in the tool's words and leaves the board as it was.

    One connection holds the write lock, or a read, for exactly as long as the
    command runs, so the command's own BEGIN IMMEDIATE, or its COMMIT after
    real writes, runs out its busy timeout. The lock is the barrier: nothing
    here sleeps. Returns (what each case printed, what went wrong).
    """
    directory, board = made_board(half, "todo-busy-", [
        task_args("BZ-001", "a task"), ["move", "BZ-001", "done"],
        task_args("BZ-002", "a second task"), ["move", "BZ-002", "done"],
    ])
    said, problems = [], []

    def held(kind, args, must, still):
        other = sqlite3.connect(board, isolation_level=None)
        try:
            other.execute("BEGIN IMMEDIATE" if kind == "write" else "BEGIN")
            other.execute("SELECT count(*) FROM task").fetchone()
            before = board_files(board)
            text = run(*half, directory, args)
            after = board_files(board)
        finally:
            other.execute("ROLLBACK")
            other.close()
        said.append(text)
        label = f"todo {' '.join(args)} while another session holds a {kind}"
        if must not in text or not text.endswith("code:1"):
            problems.append(f"{label} said {text!r}")
        if after != before:
            problems.append(f"{label} changed the board or left a file beside it")
        if not still():
            problems.append(f"{label} recorded something anyway")

    try:
        not_migrated = lambda: "tested" not in [row[1] for row in read_only(board, "PRAGMA table_info(task)")]
        for kind in ("write", "read"):
            held(kind, ["tested", "BZ-001", "--evidence", "the suite"],
                 "BZ-001: the board could not take the claim: database is locked. Nothing was recorded.\ncode:1", not_migrated)
        run(*half, directory, ["tested", "BZ-002", "--evidence", "the suite"])
        still_tested = lambda: read_only(board, "SELECT tested, (SELECT count(*) FROM note WHERE task = 'BZ-002') FROM task WHERE id = 'BZ-002'") == [(1, 0)]
        if not still_tested():
            problems.append("the claim made once the board was free was not recorded")
        for kind in ("write", "read"):
            held(kind, ["untest", "BZ-002", "--reason", "a proof that no longer holds"],
                 "BZ-002: the board could not clear it: database is locked. Nothing was changed.\ncode:1", still_tested)
        return said, problems
    finally:
        shutil.rmtree(directory, ignore_errors=True)


# Each: what happens, whether the task is tested first, when a hand-installed
# trigger fails, how it fails, the command, and exactly what the command prints.
FAILED_TRANSACTIONS = (
    ("RAISE(ROLLBACK) while a first claim is written", False,
     "BEFORE UPDATE OF updated ON task WHEN NEW.id = 'RB-001'", "ROLLBACK",
     ["tested", "RB-001", "--evidence", "the suite"],
     "out:err:RB-001: the board could not take the claim: a synthetic failure. Nothing was recorded.\ncode:1"),
    ("RAISE(ABORT) while a first claim is written", False,
     "BEFORE UPDATE OF updated ON task WHEN NEW.id = 'RB-001'", "ABORT",
     ["tested", "RB-001", "--evidence", "the suite"],
     "out:err:RB-001: the board could not take the claim: a synthetic failure. Nothing was recorded.\ncode:1"),
    ("RAISE(ROLLBACK) while untest writes its note", True,
     "BEFORE INSERT ON note WHEN NEW.task = 'RB-001'", "ROLLBACK",
     ["untest", "RB-001", "--reason", "a proof that no longer holds"],
     "out:err:RB-001: the board could not clear it: a synthetic failure. Nothing was changed.\ncode:1"),
)


def a_failed_transaction_is_reported_as_it_happened(half):
    """When SQLite ends a claim or a clear with an error, the command says that error and the board is as it was.

    A full disk, an I/O error or an interrupt makes SQLite roll the whole
    transaction back itself, before the command's own cleanup runs; a trigger
    a hand edit could install does the same with RAISE(ROLLBACK), on a
    synthetic board. RAISE(ABORT) is the control: it undoes only the statement
    and leaves the transaction open for the command's own ROLLBACK. Either way
    the command prints exactly its message around SQLite's own words and exits
    1; the board and every file beside it are byte for byte as they were; the
    schema is unchanged, so a first claim left no test-state column or guard;
    and the task's row and the notes are unchanged, so a clear left its proof
    and wrote no note. Returns (what each case printed, what went wrong).
    """
    said, problems = [], []
    for label, tested_first, when, how, args, expected in FAILED_TRANSACTIONS:
        setup = [task_args("RB-001", "a task"), ["move", "RB-001", "done"]]
        if tested_first:
            setup.append(["tested", "RB-001", "--evidence", "the suite"])
        directory, board = made_board(half, "todo-failed-", setup)
        try:
            writer = sqlite3.connect(board, isolation_level=None)
            try:
                writer.execute(f"CREATE TRIGGER synthetic_failure {when} BEGIN SELECT RAISE({how}, 'a synthetic failure'); END")
            finally:
                writer.close()
            schema = lambda: read_only(board, "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name")
            state = lambda: read_only(board, "SELECT * FROM task WHERE id = 'RB-001'") + read_only(board, "SELECT count(*) FROM note")
            files, before, kept = board_files(board), schema(), state()
            text = run(*half, directory, args)
            said.append(text)
            if text != expected:
                problems.append(f"{label}: printed {text!r}, not {expected!r}")
            if board_files(board) != files:
                problems.append(f"{label}: the board or a file beside it changed")
            after = schema()
            if after != before:
                problems.append(f"{label}: the schema changed")
            if not tested_first and any(row[1].startswith("todo_test_") for row in after):
                problems.append(f"{label}: the failed first claim left test-state guards")
            if state() != kept:
                problems.append(f"{label}: the task or the notes changed")
        finally:
            shutil.rmtree(directory, ignore_errors=True)
    return said, problems


GUARD_NAMES = ("todo_test_claim, todo_test_claim_insert, todo_test_status_clears, todo_test_story_clears,"
               " todo_test_finding_added, todo_test_finding_reopened, todo_test_finding_attached")


def validate_reads_a_tampered_board(half):
    """validate names every claim the guards would have refused, once a hand edit has dropped them, and a board missing a column.

    Returns (what validate and the rest printed, what went wrong).
    """
    tasks = [task_args(f"VA-00{n}", f"task {n}") for n in range(1, 9)] + [["move", f"VA-00{n}", "done"] for n in range(1, 9)]
    directory, board = made_board(half, "todo-tampered-", [
        *tasks,
        task_args("VA-009", "a finding of task 6", "--parent-task", "VA-006", "--status", "done"),
        *[["tested", f"VA-00{n}", "--evidence", f"suite {n}"] for n in range(1, 7)],
        ["e2e", "VA-005", "--evidence", "path 5"],
    ])
    said, problems = [], []
    try:
        writer = sqlite3.connect(board, isolation_level=None)
        try:
            for (name,) in writer.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'").fetchall():
                writer.execute(f'DROP TRIGGER "{name}"')
            for statement in (
                "UPDATE task SET tested = 2 WHERE id = 'VA-001'",
                "UPDATE task SET status = 'backlog' WHERE id = 'VA-002'",
                "UPDATE task SET tested_how = char(133) WHERE id = 'VA-003'",
                "UPDATE task SET tested = 0, e2e_tested = 1, e2e_how = 'a path' WHERE id = 'VA-004'",
                "UPDATE task SET e2e_how = char(65279) WHERE id = 'VA-005'",
                "UPDATE task SET status = 'backlog' WHERE id = 'VA-009'",
            ):
                writer.execute(statement)
        finally:
            writer.close()
        text = run(*half, directory, ["validate"])
        said.append(text)
        for line in (
            "VA-001: tested holds 2, not 0 or 1",
            "VA-002 is tested but backlog",
            "VA-003 is tested with nothing saying what proved it",
            "VA-004 is e2e tested but not tested",
            "VA-005 is e2e tested with nothing saying what proved it",
            "VA-006 is tested, but its finding(s) VA-009 are open",
            f"the board records test states without the guard(s) {GUARD_NAMES}; the next todo tested adds them",
        ):
            if line not in text:
                problems.append(f"validate did not say {line!r}")
        if not text.endswith("code:1"):
            problems.append("validate passed a tampered board")
        # The claim the guards forbid, e2e tested but not tested, cleared by hand:
        # its note says exactly what it had.
        for args, must, never in (
            (["untest", "VA-004", "--reason", "a claim no guard allowed"],
             "VA-004: test state cleared. What it had is kept in a note.", None),
            (["show", "VA-004"], "test state cleared by hand: a claim no guard allowed; it had not tested; e2e tested: a path", None),
        ):
            text = run(*half, directory, args)
            said.append(text)
            problem = wrong(text, must, never)
            if problem:
                problems.append(f"todo {' '.join(args)} on a tampered board: {problem}")
        # A column gone: the board no longer reads as one that records test states.
        writer = sqlite3.connect(board, isolation_level=None)
        try:
            writer.execute("ALTER TABLE task DROP COLUMN e2e_at")
        finally:
            writer.close()
        for args, must, never in (
            (["validate"], "the board records test states without the column(s) e2e_at", None),
            (["show", "VA-007"], "VA-007", "tested:"),
            (["tests"], "This board records no test states yet.", None),
            (["untest", "VA-001", "--reason", "a partial board"], "VA-001: this board records no test states, so there is nothing to clear.", None),
            (["tested", "VA-008", "--evidence", "suite 8"], "VA-008: tested, suite 8\n  This board records test states from now on.", None),
            (["validate"], "VA-001: tested holds 2", "records test states without"),
        ):
            text = run(*half, directory, args)
            said.append(text)
            problem = wrong(text, must, never)
            if problem:
                problems.append(f"todo {' '.join(args)} on a tampered board: {problem}")
        return said, problems
    finally:
        shutil.rmtree(directory, ignore_errors=True)


# Measured legitimate CLI gaps, on fresh fixture owners and both equivalent
# engines. Native outputs are compared and each named behavior is asserted.
MEASURED_STEPS = [
    (["phase"], "No phases.", None),
    (["phase", "add", "P", "--goal", "Fixture shipping objective"], "Added phase", None),
    (["phase"], "P", None),
    (["okr", "done"], "(no id)", None),
    (["okr", "edit"], "(no id)", None),
    (task_args("x1", "Equal numeric suffix one"), "Added x1", None),
    (task_args("y1", "Equal numeric suffix two"), "Added y1", None),
    (["next"], "x1", None),
    (["render"], "rendered", None),
    (["render", "--check"], "in sync", None),
    (["phase", "done", "P"], "Phase P is done.", None),
    (["next"], "x1", None),
    (["okr", "add", "--name", "Second objective", "--description", "Fixture second objective", "--position", "2"], "Added", None),
    (["edit", "x1", "--parent", ""], "parent changed", None),
    (["edit", "x1", "--parent-task", ""], "no longer a child", None),
    (["edit", "x1", "--note", ""], "note changed", None),
    (["move", "x1", "in_progress"], "backlog -> in_progress", None),
    (["move", "y1", "in_progress"], "is in progress too", None),
    (["move", "x1", "done"], "Roast x1 now", None),
    (["roast", "x1", "--file", "one.md"], "roast", None),
    (["roast", "x1", "--file", "two.md", "--filed", "none"], "roast", None),
    (["list"], "y1", None),
]


def measured_legacy_transcript(half):
    directory, board = made_board(half, "todo-measured-legacy-", [
        task_args("legacy-a", "Legacy explicit stored evidence"), ["move", "legacy-a", "done"],
        ["tested", "legacy-a", "--evidence", "real fixture claim"],
        ["e2e", "legacy-a", "--evidence", "real fixture path"],
        task_args("legacy-b", "Legacy second stored evidence"), ["move", "legacy-b", "done"],
        ["tested", "legacy-b", "--evidence", "real fixture claim"],
    ], fixed_project=True)
    try:
        with __import__("contextlib").closing(sqlite3.connect(board)) as conn, conn:
            for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall():
                conn.execute('DROP TRIGGER "' + name.replace('"','""') + '"')
            conn.execute("UPDATE task SET tested_how=NULL,e2e_how=NULL WHERE id='legacy-a'")
            conn.execute("UPDATE task SET tested=0,e2e_tested=1,e2e_how=NULL WHERE id='legacy-b'")
        results = [run(*half, directory, args) for args in (
            ["validate"], ["untest", "legacy-a", "--reason", "malformed legacy evidence"],
            ["untest", "legacy-b", "e2e", "--reason", "malformed legacy evidence"], ["show", "legacy-a"], ["render"])]
        return results
    finally:
        remove_fixed_project(directory)


def scripted_transcript(prefix, steps, half):
    """Every step on one fresh board through one half. A step is (session, args, must, never), or
    ("setup", function), which changes the project folder between commands the way a person or
    another tool would."""
    directory = tempfile.mkdtemp(prefix=prefix)
    try:
        run(*half, directory, ["init", "--here"])
        said = []
        for step in steps:
            if step[0] == "setup":
                step[1](directory)
                continue
            session, args, _must, _never = step
            said.append(run(*half, directory, args, session))
        return said
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def scripted_problems(title, steps, from_node, from_python):
    """Each step of a scripted catalog: the same from both halves, and saying what it must. Prints and returns how many are wrong."""
    count = 0
    commands = [step for step in steps if step[0] != "setup"]
    for (session, args, must, never), node_said, python_said in zip(commands, from_node, from_python):
        label = f"todo {' '.join(args)} ({title}{', session ' + session if session else ''})"
        problem = wrong(python_said, must, never)
        if node_said != python_said:
            count += 1
            print(f"  DIFFER {label}")
            print(f"    node  : {node_said!r}")
            print(f"    python: {python_said!r}")
        elif problem:
            count += 1
            print(f"  WRONG  {label}: {problem}")
            print(f"    said: {python_said!r}")
        else:
            print(f"  right  {label}")
    if len(from_node) != len(commands) or len(from_python) != len(commands):
        count += 1
        print(f"  WRONG  {title}: a half ran a different number of steps")
    return count


def write_into_board(*statements):
    """A setup step: what a hand edit or an older tool left in the board, written straight into it."""
    def edit(directory):
        writer = sqlite3.connect(os.path.join(directory, ".claude", "todo.db"), isolation_level=None)
        try:
            for statement in statements:
                writer.execute(statement)
        finally:
            writer.close()
    return edit


def write_loop_file(name, body):
    """A setup step: a session's loop file, as the owner would write it."""
    def write(directory):
        with open(os.path.join(directory, ".claude", name), "w", encoding="utf-8") as handle:
            handle.write(body)
    return write


HELD = "; next does not resume it until every parent is done.\n"
# RE-317: a started task one of whose parents is unfinished, because the parent
# was named after it started, is not resumed until every parent is done; next
# offers other work, says why, and never says "Nothing left" while it waits.
STARTED_PARENT_STEPS = [
    (None, task_args("RP-001", "the parent named later"), "Added RP-001", None),
    (None, task_args("RP-002", "started before its parent", "--severity", "critical", "--points", "1"), "Added RP-002", None),
    (None, ["move", "RP-002", "in_progress"], "RP-002: backlog -> in_progress", None),
    (None, ["next"], "ALREADY STARTED, finish this first\n\nRP-002  [critical/1pt]", "waits on"),
    (None, ["edit", "RP-002", "--parent", "RP-001"], ("RP-002: parent changed\n", "  after: RP-001\n"), None),
    (None, ["next"], ("NEXT: highest severity, unblocked, fewest points\n\nRP-001  [high/2pt]",
                      "\n\n  RP-002 is in_progress and waits on RP-001 (backlog)" + HELD), "ALREADY STARTED"),
    (None, ["move", "RP-001", "blocked", "--reason", "the owner decides"], "RP-001: backlog, blocked: the owner decides", None),
    (None, ["next"], ("out:Nothing eligible. 1 task(s) waiting on unfinished parents.\n1 task(s) blocked:\n  RP-001: the owner decides\n"
                      "  RP-002 is in_progress and waits on RP-001 (backlog)" + HELD + "err:code:0",), "Nothing left"),
    (None, ["move", "RP-002", "wait_for_roast"], "RP-002: in_progress -> wait_for_roast", None),
    (None, ["next"], "  RP-002 is wait_for_roast and waits on RP-001 (backlog)" + HELD, "Nothing left"),
    (None, ["move", "RP-001", "done"], ("RP-001: backlog -> done", "no longer blocked: the owner decides"), None),
    (None, ["next"], "ALREADY STARTED, finish this first\n\nRP-002  [critical/1pt]", "waits on"),
    # A dropped parent never becomes done, so next says its status rather than "once it is done".
    (None, task_args("RP-003", "a parent that is dropped"), "Added RP-003", None),
    (None, ["edit", "RP-002", "--parent", "RP-003"], "  after: RP-003\n", None),
    (None, ["move", "RP-003", "dropped", "--reason", "no longer wanted"], "RP-003: backlog -> dropped", None),
    (None, ["next"], ("out:Nothing eligible. 1 task(s) waiting on unfinished parents.\n"
                      "  RP-002 is wait_for_roast and waits on RP-003 (dropped)" + HELD + "err:code:0",), "Nothing left"),
    # A parent gone from the board, as an older tool or a hand edit can leave one.
    ("setup", write_into_board("INSERT INTO blocked_by (task, parent) VALUES ('RP-002', 'RP-404')")),
    (None, ["next"], "  RP-002 is wait_for_roast and waits on RP-003 (dropped), RP-404, which is not on this board" + HELD, "Nothing left"),
    # Areas: a parent in another area holds a started task in this one until it is done.
    (None, task_args("RP-010", "started in back", "--area", "back", "--severity", "critical", "--points", "1"), "Added RP-010", None),
    (None, task_args("RP-011", "its parent in front", "--area", "front"), "Added RP-011", None),
    (None, ["move", "RP-010", "in_progress"], "RP-010: backlog -> in_progress", None),
    (None, ["edit", "RP-010", "--parent", "RP-011"], "  after: RP-011\n", None),
    (None, ["next", "--area", "back"], ("out:Nothing eligible in area back. 1 task(s) waiting on unfinished parents.\n"
                                        "  RP-010 is in_progress and waits on RP-011 (backlog)" + HELD + "err:code:0",), "Nothing left"),
    (None, ["next", "--area", "front"], "NEXT in area front: highest severity, unblocked, fewest points\n\nRP-011  [high/2pt]", "waits on"),
    (None, ["move", "RP-011", "done"], "RP-011: backlog -> done", None),
    (None, ["next", "--area", "back"], "ALREADY STARTED in area back, finish this first\n\nRP-010  [critical/1pt]", "waits on"),
]

# RE-311: a flag at the end with no value is passed over by both halves; it neither clears nor counts as a change.
VALUELESS_EDITS = (["--parent-task"], ["--parent"], ["--note"], ["--area"], ["--title"], ["--phase"], ["--okr"],
                   ["--severity", "low", "--points"])


def valueless_flags_change_nothing(half):
    """Each trailing valueless flag on edit: refused as nothing to change, or changing only what has a value,
    with the stored row, its links and notes otherwise untouched. Returns (what was said, what went wrong)."""
    directory, board = made_board(half, "todo-valueless-", [
        task_args("VF-000", "the origin"), task_args("VF-009", "a blocker"),
        task_args("VF-001", "a finding", "--parent-task", "VF-000", "--parent", "VF-009", "--area", "api"),
        ["edit", "VF-001", "--note", "a kept note"],
    ])
    stored = lambda: (read_only(board, "SELECT * FROM task WHERE id = 'VF-001'"),
                      read_only(board, "SELECT parent FROM blocked_by WHERE task = 'VF-001'"),
                      read_only(board, "SELECT text FROM note WHERE task = 'VF-001'"))
    said, problems = [], []
    try:
        before = stored()
        for flags in VALUELESS_EDITS[:-1]:
            text = run(*half, directory, ["edit", "VF-001", *flags])
            said.append(text)
            problem = wrong(text, ("Nothing to change.", "code:1"), "changed")
            if problem:
                problems.append(f"edit VF-001 {' '.join(flags)}: {problem}")
            if stored() != before:
                problems.append(f"edit VF-001 {' '.join(flags)} changed the stored task, its links or its notes")
        text = run(*half, directory, ["edit", "VF-001", *VALUELESS_EDITS[-1]])
        said.append(text)
        problem = wrong(text, ("VF-001: severity changed\n", "code:0"), "severity, points")
        if problem:
            problems.append(f"edit VF-001 --severity low --points: {problem}")
        after = stored()
        if (after[1], after[2]) != (before[1], before[2]):
            problems.append("a value given with a trailing valueless flag touched the links or notes")
        return said, problems
    finally:
        shutil.rmtree(directory, ignore_errors=True)


LONG_DIGITS = "7" * 4301
# RE-318: the order of explicit ids with equal severity and points, decided by the id alone: every ASCII
# digit joined, compared as an exact digit string, then the id by code point. Expected, as written here.
ID_ORDER = [
    "Beta", "T-²", "T-٣", "alpha",
    "SB-001", "SB-001a", "X1", "x1", "Ａ-1", "𝐀-1",
    "SB-002", "T-2", "K-07", "K-7", "SB-1e3", "SB-14",
    "P2-009", "P2-010", "P10-001",
    "B-9007199254740992", "A-9007199254740993",
    "LONG-" + LONG_DIGITS,
]


def ids_order_exactly(half):
    """List keeps stored SQL order; next and rendered lanes use exact numeric/code-point id order."""
    scrambled = ID_ORDER[1::2] + ID_ORDER[::2]
    directory, _board = made_board(half, "todo-id-order-",
        [task_args(task_id, "an id decides") for task_id in scrambled], fixed_project=True)
    said, problems = [], []
    try:
        listed = run(*half, directory, ["list"])
        picked = run(*half, directory, ["next"])
        rendered_result = run(*half, directory, ["render", "--out", "BOARD.md"])
        rendered = Path(directory, "BOARD.md").read_text(encoding="utf-8")
        said += [listed, picked, rendered_result, rendered]
        stored = re.findall(r"^    (\S+)  \[high/2pt\]  an id decides", listed.split("out:", 1)[-1], re.MULTILINE)
        if stored != sorted(ID_ORDER):
            problems.append(f"list did not preserve SQL id order: {stored!r}")
        ranked = re.findall(r"^\| \x60(\S+)\x60 \| an id decides \|", rendered, re.MULTILINE)
        if ranked != ID_ORDER:
            problems.append(f"render ordered the ids {ranked!r}, not {ID_ORDER!r}")
        if not all(text.endswith("code:0") for text in (listed, picked, rendered_result)):
            problems.append("list, next or render failed on these ids")
        if "\n\nBeta  [high/2pt]" not in picked:
            problems.append(f"next did not pick Beta, the first by the id order: {picked[:200]!r}")
        return said, problems
    finally:
        remove_fixed_project(directory)


# RE-319 and RE-320: an empty --id is no id, and the next id is the trailing run of ASCII digits plus one,
# worked as a digit string at any length, its padding to three kept. The board's folder is the temp
# folder "todo-ids-...", so an empty board's first id is TO-001.
ID_STEPS = [
    (None, task_args("", "an empty id"), "Added TO-001: an empty id\n", None),
    (None, task_args("T-9007199254740993", "past the double's exact integers"), "Added T-9007199254740993", None),
    (None, edge_add("the one after it"), "Added T-9007199254740994: the one after it\n", None),
    (None, task_args("L-" + "9" * 4400, "a suffix of 4400 nines"), "Added L-", None),
    (None, edge_add("one more than all the nines"), f"Added L-1{'0' * 4400}: one more than all the nines\n", None),
]
PADDING_STEPS = [
    (None, task_args("SB-0099", "four digits"), "Added SB-0099", None),
    (None, edge_add("padded to three"), "Added SB-100: padded to three\n", None),
    (None, task_args("SB-7", "a lower one"), "Added SB-7", None),
    (None, edge_add("after the highest"), "Added SB-101: after the highest\n", None),
    (None, task_args("SB-101a", "ends in a letter"), "Added SB-101a", None),
    (None, task_args("U-١٢", "Arabic-Indic digits"), "Added U-١٢", None),
    (None, task_args("Z-1", "renamed below"), "Added Z-1", None),
    # An id ending in a newline, as a hand edit can leave one, does not end in digits.
    ("setup", write_into_board("UPDATE task SET id = 'Z-900' || char(10) WHERE id = 'Z-1'")),
    (None, edge_add("still after SB-101"), "Added SB-102: still after SB-101\n", "Z-901"),
]

UNTERMINATED_LOOP_FILE = '---\nsession: "unterminated-session"\nareas: back\n'
AREA_LOOP_FILE = '---\nsession: "area-session"\nareas: back\n---\n'
# RE-301: the command paths the catalogs above do not reach, each with what it must say.
FAMILY_STEPS = [
    (None, ["phase", "add", "P1", "--goal", "the first objective"], "Added phase 1. P1", None),
    (None, ["phase", "add", "P5", "--goal", "placed by hand", "--position", "5"], "Added phase 5. P5", None),
    (None, ["okr", "done", "P1"], "P1 is met.", None),
    (None, ["okr", "open", "P1"], "P1 is open.", "is met"),
    (None, task_args("FA-001", "with an area", "--area", "api", "--phase", "P1"), "Added FA-001", None),
    (None, task_args("FA-002", "with no area", "--phase", "P1"), "Added FA-002", None),
    (None, task_args("FA-003", "a third", "--phase", "P1"), "Added FA-003", None),
    # A session whose loop file never closes its front matter has no loop: every area is open to it.
    ("setup", write_loop_file("unterminated-loop.local.md", UNTERMINATED_LOOP_FILE)),
    ("unterminated-session", ["next"], "NEXT in P1: highest severity", "in area"),
    # A session that works only in back cannot start a task with no area, which reads as unset.
    ("setup", write_loop_file("area-loop.local.md", AREA_LOOP_FILE)),
    ("area-session", ["move", "FA-002", "in_progress"], ("FA-002 is in area unset; this session works in back", "code:1"), "->"),
    # Starting a third task while two are in progress names both.
    (None, ["move", "FA-001", "in_progress"], "FA-001: backlog -> in_progress", None),
    (None, ["move", "FA-002", "in_progress"], "  FA-001 is in progress too", None),
    (None, ["move", "FA-003", "in_progress"], "  FA-001, FA-002 are in progress too; finish one before starting another.", None),
    # A roast recorded again against the same file, without --filed, keeps what was filed.
    (None, ["move", "FA-001", "done"], "FA-001: in_progress -> done", None),
    (None, ["roast", "FA-001", "--file", "r1.md", "--filed", "none"], "FA-001 roast round 1", None),
    (None, ["roast", "FA-001", "--file", "r1.md", "--score", "9"], "FA-001 roast round 1 (updated)", None),
    (None, ["show", "FA-001"], "    round 1: r1.md, score 9, filed none", "not judged yet"),
]


def rendered_next_names_its_area(half):
    """The rendered board's next pick carries its area. Returns (the rendered file, what went wrong)."""
    directory, _board = made_board(half, "todo-render-area-", [task_args("RA-001", "picked with an area", "--area", "api")], fixed_project=True)
    try:
        said = run(*half, directory, ["render", "--out", "BOARD.md"])
        with open(os.path.join(directory, "BOARD.md"), encoding="utf-8") as handle:
            rendered = handle.read()
        problems = [] if "**Next up: `RA-001` picked with an area** (high, 2 pt, api)" in rendered else [
            f"the rendered next pick does not name its area: {rendered[:400]!r}"]
        return [said, rendered], problems
    finally:
        remove_fixed_project(directory)


def legacy_severity_refusals(half):
    """Owned old SQL without ranking CHECKs: refuse candidates, keep stored rows readable."""
    # This permissive legacy schema produces text/NULL/BLOB points and NULL/BLOB severity, and with
    # a REAL points column every number reads back as a float (2 as 2.0); no constraint is removed
    # from a modern board.
    row = lambda id, severity, status="backlog", phase=None, points=2: (id, severity, status, phase, points)
    cases = (
        ("singleton", [row("U-001", "urgent")], (), (), "U-001", "U-001"),
        ("NULL severity", [row("U-001", None)], (), (), "U-001", "U-001"),
        ("phase and peer", [row("V-001", "high", phase="P1"), row("U-001", "urgent", phase="P2")], (), (), "U-001", "U-001"),
        ("stored order", [row("U-002", "urgent"), row("U-001", "urgent")], (), (), "U-001", "U-001"),
        ("started first", [row("E-001", "urgent"), row("S-001", "urgent", "in_progress")], (), (), "S-001", "S-001"),
        ("held", [row("P-001", "low"), row("U-001", "urgent", "in_progress")], (("U-001", "P-001"),), (), "U-001", "U-001"),
        ("blocked", [row("V-001", "high"), row("U-001", "urgent")], (), ("U-001",), None, "U-001"),
        ("other status", [row("V-001", "high"), row("U-001", "urgent", "open")], (), (), None, None),
        ("done severity", [row("V-001", "high"), row("U-001", "urgent", "done")], (), (), None, "U-001"),
        ("unknown blocker", [row("U-001", "urgent"), row("V-001", "critical")], (("V-001", "U-001"),), ("U-001",), None, "U-001"),
        ("unknown dependent", [row("P-001", "low"), row("U-001", "urgent")], (("U-001", "P-001"),), (), None, "U-001"),
        ("text points peer", [row("V-001", "high"), row("U-001", "high", points="two")], (), (), "U-001", "U-001"),
        ("NULL points peer", [row("V-001", "high"), row("U-001", "high", points=None)], (), (), "U-001", "U-001"),
        ("unsupported number", [row("U-001", "high", points=4)], (), (), "U-001", "U-001"),
        ("points stored order", [row("U-002", "high", points="two"), row("U-001", "high", points=None)], (), (), "U-001", "U-001"),
        ("points held", [row("P-001", "high"), row("U-001", "high", "in_progress", points=None)], (("U-001", "P-001"),), (), "U-001", "U-001"),
        ("points blocked", [row("V-001", "high"), row("U-001", "high", points="two")], (), ("U-001",), None, "U-001"),
        ("points other status", [row("V-001", "high"), row("U-001", "high", "open", points=None)], (), (), None, None),
        ("numeric points other status", [row("V-001", "high"), row("U-001", "high", "open", points=4)], (), (), None, None),
        ("points done", [row("V-001", "high"), row("U-001", "high", "done", points="two")], (), (), None, "U-001"),
        ("BLOB severity", [row("U-001", b"\x01")], (), (), "U-001", "U-001"),
        ("BLOB points blocked", [row("V-001", "high"), row("U-001", "high", points=b"\x01")], (), ("U-001",), None, "U-001"),
        # A REAL column: 2 and 5 read back as 2.0 and 5.0 and still rank; 2.5 is shown as it is.
        ("REAL points", [row("U-001", "high"), row("P-001", "high", "done", points=5), row("O-001", "high", "open", points=2.5)],
         (), (), None, None, "REAL"),
    )
    def severity_error(stored):
        id, severity, *_ = stored
        if severity in ("critical", "high", "medium", "low"):
            return None
        label = "NULL" if severity is None else "<non-text>" if isinstance(severity, bytes) else severity
        return f"{id}: its severity {label} is not one of critical, high, medium, low"

    def points_error(stored):
        id, *_, points = stored
        if type(points) in (int, float) and points in (1, 2, 3, 5, 8, 13):
            return None
        label = (points if isinstance(points, str) else "NULL" if points is None
                 else "<non-number>" if isinstance(points, bytes) else "<unsupported number>")
        return f"{id}: its points {label} is not one of 1, 2, 3, 5, 8, 13"

    said, problems = [], []
    for name, rows, dependencies, blocked, next_bad, render_bad, *points_type in cases:
        first = len(said)
        owned = tempfile.mkdtemp(prefix="todo-legacy-rank-")
        directory = os.path.join(owned, "project")
        os.makedirs(os.path.join(directory, ".claude"))
        board = os.path.join(directory, ".claude", "todo.db")
        try:
            writer = sqlite3.connect(board)
            try:
                writer.executescript("""
                    CREATE TABLE task(id TEXT PRIMARY KEY,title TEXT NOT NULL,descr TEXT NOT NULL,
                        why TEXT NOT NULL,severity TEXT,points """ + (points_type or ["INTEGER"])[0] + """,status TEXT NOT NULL,
                        exit_cond TEXT NOT NULL,phase TEXT,created TEXT NOT NULL DEFAULT (datetime('now')));
                    CREATE TABLE blocked_by(task TEXT,parent TEXT,PRIMARY KEY(task,parent));
                    CREATE TABLE blocked(task TEXT PRIMARY KEY,reason TEXT NOT NULL,since TEXT NOT NULL);
                    CREATE TABLE phase(name TEXT PRIMARY KEY,goal TEXT NOT NULL,position INTEGER NOT NULL,status TEXT NOT NULL);
                    INSERT INTO phase VALUES('P1','First fixture goal',1,'open'),('P2','Second fixture goal',2,'open');
                """)
                for id, severity, status, phase, points in rows:
                    writer.execute("INSERT INTO task(id,title,descr,why,severity,points,status,exit_cond,phase) VALUES(?,?,?,?,?,?,?,?,?)",
                        (id, "Legacy ranking " + id, "A stored old row", "Exercise old-board ranking", severity, points, status,
                         "Run the fixture and verify its recorded result", phase))
                writer.executemany("INSERT INTO blocked_by VALUES(?,?)", dependencies)
                writer.executemany("INSERT INTO blocked VALUES(?,'Owned explicit block','fixture time')", ((id,) for id in blocked))
                writer.commit()
            finally:
                writer.close()
            for args, bad in ((["next"], next_bad), (["render", "--out", "BOARD.md"], render_bad)):
                text = run(*half, directory, args)
                said.append(text)
                if bad is not None:
                    stored = next(item for item in rows if item[0] == bad)
                    expected = severity_error(stored) or points_error(stored)
                    if not text.endswith("code:1") or expected not in text or "Traceback" in text:
                        problems.append(f"{name} {args[0]} did not refuse its first unrankable candidate: {text!r}")
                elif not text.endswith("code:0"):
                    problems.append(f"{name} {args[0]} did not preserve the valid/blocked behavior: {text!r}")
                elif args[0] == "render":
                    with open(os.path.join(directory, "BOARD.md"), encoding="utf-8") as saved:
                        rendered = saved.read()
                    said.append(rendered)
                    if any(type(item[4]) not in (int, float) for item in rows) and " of unknown points." not in rendered:
                        problems.append(f"{name}: render coerced a non-number aggregate total")
                    if name == "numeric points other status" and "Project **project** · 0 of 2 tasks done · 0 of 6 points." not in rendered:
                        problems.append(f"{name}: render hid the exact summable numeric total")
            checked = run(*half, directory, ["validate"])
            listed = run(*half, directory, ["list"])
            invalid = next(item for item in rows if severity_error(item) or points_error(item))
            shown = run(*half, directory, ["show", invalid[0]])
            said += [checked, listed, shown]
            for stored in rows:
                for expected in (severity_error(stored), points_error(stored)):
                    if expected is not None and expected not in checked:
                        problems.append(f"{name}: validate omitted {expected}")
            if not checked.endswith("code:1") or "Traceback" in checked:
                problems.append(f"{name}: validate failed to report unsupported stored ranking fields: {checked!r}")
            if any(severity_error(item) for item in rows) and "which is less severe" in checked:
                problems.append(f"{name}: validate ranked an invalid severity starvation pair")
            if not listed.endswith("code:0") or not shown.endswith("code:0"):
                problems.append(f"{name}: an unsupported ranking field made stored rows unreadable")
            if (invalid[1] is None or invalid[4] is None) and "NULL" not in shown:
                problems.append(f"{name}: show omitted the explicit NULL label")
            for value, label in ((invalid[1], "<non-text>"), (invalid[4], "<non-number>")):
                if isinstance(value, bytes) and (label not in shown or label not in listed):
                    problems.append(f"{name}: list or show did not label the stored BLOB {label}")
            if any("b'" in text for text in said[first:]):
                problems.append(f"{name}: a stored BLOB was shown as Python bytes")
            if points_type:
                rendered = "".join(said[first:])
                for words in ("[high/2pt]", "[high/5pt]", "[high/2.5pt]", "(high, 2 pt)", "| 2 |",
                              "· 1 of 3 tasks done · 5 of 9.5 points."):
                    if words not in rendered:
                        problems.append(f"{name}: the integral REAL points were not shown as {words!r}")
                if ".0pt" in rendered or ".0 pt" in rendered or "5.0 of" in rendered:
                    problems.append(f"{name}: an integral REAL was shown with its fraction")
        finally:
            shutil.rmtree(owned, ignore_errors=True)
    return said, problems


def auto_id_after_zero(half):
    """An explicitly stored all-zero ASCII suffix followed by ordinary add."""
    directory, _board = made_board(half, "todo-id-zero-", [task_args("Z-0000", "all-zero suffix")])
    try:
        text = run(*half, directory, edge_add("the one after zero"))
        return [text], [] if "Added Z-001: the one after zero" in text and text.endswith("code:0") else [
            f"ordinary add did not increment the stored zero suffix: {text!r}"]
    finally:
        shutil.rmtree(directory, ignore_errors=True)


measured_node = steps_transcript("todo-measured-paths-", MEASURED_STEPS, [NODE])
measured_python = steps_transcript("todo-measured-paths-", MEASURED_STEPS, [PYTHON])
measured_legacy_node = measured_legacy_transcript(NODE)
measured_legacy_python = measured_legacy_transcript(PYTHON)


node = transcript(NODE_EXE, NODE_SCRIPT)
python = transcript(sys.executable, PY_SCRIPT)

differences = 0

for (args, must, never), from_node, from_python in zip(MEASURED_STEPS, measured_node, measured_python):
    problem = wrong(from_python, must, never)
    if from_node != from_python or problem:
        differences += 1
        print(f"  WRONG measured CLI path {args!r}: {problem or 'native port outputs differ'}")
        print(f"    node: {from_node!r}")
        print(f"    python: {from_python!r}")
    else:
        print(f"  right measured CLI path {args!r}")
if measured_legacy_node != measured_legacy_python:
    differences += 1
    print("  DIFFER malformed legacy evidence paths")
    print(f"    node: {measured_legacy_node!r}")
    print(f"    python: {measured_legacy_python!r}")
elif not ("code:1" in measured_legacy_python[0] and all(result.endswith("code:0") for result in measured_legacy_python[1:])):
    differences += 1
    print(f"  WRONG legacy evidence native exits: {measured_legacy_python!r}")


boardless_node = refuses_without_a_board(NODE_EXE, NODE_SCRIPT)
boardless_python = refuses_without_a_board(sys.executable, PY_SCRIPT)

if boardless_node == boardless_python and "claude-dir-created:False" in boardless_node:
    print("  same   todo list, with no board anywhere: refused, nothing created")
else:
    differences += 1
    print("  DIFFER todo list, with no board anywhere")
    print("    node  : " + repr(boardless_node))
    print("    python: " + repr(boardless_python))
for args, from_node, from_python in zip(SCRIPT, node, python):
    if from_node == from_python:
        print(f"  same   todo {' '.join(args)}")
        continue
    differences += 1
    print(f"  DIFFER todo {' '.join(args)}")
    print(f"    node  : {from_node!r}")
    print(f"    python: {from_python!r}")

loop_node, created_node = loop_transcript(NODE_EXE, NODE_SCRIPT)
loop_python, created_python = loop_transcript(sys.executable, PY_SCRIPT)
for (session, args, must, never), from_node, from_python in zip(LOOP_STEPS, loop_node, loop_python):
    label = f"todo {' '.join(args)} (session {session or 'none'})"
    if from_node != from_python:
        differences += 1
        print(f"  DIFFER {label}")
        print(f"    node  : {from_node!r}")
        print(f"    python: {from_python!r}")
    elif must not in from_python or (never is not None and never in from_python):
        differences += 1
        print(f"  WRONG  {label}: must say {must!r}" + (f", never {never!r}" if never else ""))
        print(f"    said: {from_python!r}")
    else:
        print(f"  right  {label}")
if created_node or created_python:
    differences += 1
    print("  WRONG  a board named by a loop file but missing was created")

# Agreeing is not enough for the area steps: each says what it must and must not print.
start = SCRIPT.index(AREA_STEPS[0][0])
for offset, (args, must, never) in enumerate(AREA_STEPS):
    said = python[start + offset]
    if must not in said or (never is not None and never in said):
        differences += 1
        print(f"  WRONG  todo {' '.join(args)}: must say {must!r}" + (f", never {never!r}" if never else ""))
        print(f"    said: {said!r}")
    else:
        print(f"  right  todo {' '.join(args)}")

# Test states: the same on both halves, and right, step by step.
tests_node = steps_transcript("todo-tests-", TEST_STEPS, [NODE])
tests_python = steps_transcript("todo-tests-", TEST_STEPS, [PYTHON])
for (args, must, never), from_node, from_python in zip(TEST_STEPS, tests_node, tests_python):
    label = f"todo {' '.join(args)} (test states)"
    problem = wrong(from_python, must, never)
    if from_node != from_python:
        differences += 1
        print(f"  DIFFER {label}")
        print(f"    node  : {from_node!r}")
        print(f"    python: {from_python!r}")
    elif problem:
        differences += 1
        print(f"  WRONG  {label}: {problem}")
        print(f"    said: {from_python!r}")
    else:
        print(f"  right  {label}")

python_first = steps_transcript("todo-mixed-", MIXED_STEPS, [PYTHON, NODE])
node_first = steps_transcript("todo-mixed-", MIXED_STEPS, [NODE, PYTHON])
for (args, must, never), from_python_first, from_node_first in zip(MIXED_STEPS, python_first, node_first):
    label = f"todo {' '.join(args)} (the halves taking turns on one board)"
    problem = wrong(from_python_first, must, never)
    if from_python_first != from_node_first:
        differences += 1
        print(f"  DIFFER {label}")
        print(f"    python first: {from_python_first!r}")
        print(f"    node first  : {from_node_first!r}")
    elif problem:
        differences += 1
        print(f"  WRONG  {label}: {problem}")
        print(f"    said: {from_python_first!r}")
    else:
        print(f"  right  {label}")

edge_node = edge_transcript(NODE)
edge_python = edge_transcript(PYTHON)
edge_steps = [step for step in EDGE_STEPS if step[0] != "setup"]
for (project, args, must, never, session), from_node, from_python in zip(edge_steps, edge_node, edge_python):
    label = f"todo {' '.join(args)} (edge cases, {EDGE_PROJECTS[project]}{', session ' + session if session else ''})"
    problem = wrong(from_python, must, never)
    if from_node != from_python:
        differences += 1
        print(f"  DIFFER {label}")
        print(f"    node  : {from_node!r}")
        print(f"    python: {from_python!r}")
    elif problem:
        differences += 1
        print(f"  WRONG  {label}: {problem}")
        print(f"    said: {from_python!r}")
    else:
        print(f"  right  {label}")

for check, problems in (
    ("todo.py in-process, with streams it cannot reconfigure, prints what a real run prints", python_without_reconfigure()),
    ("todo.mjs with node:sqlite switched off says what it needs and exits 1", node_without_sqlite()),
):
    if problems:
        differences += len(problems)
        print(f"  WRONG  {check}:")
        for problem in problems:
            print(f"    - {problem}")
    else:
        print(f"  right  {check}")

for name, half in (("node", NODE), ("python", PYTHON)):
    for check, problems in (
        ("a refused claim and every read leave the board's bytes and add no column, rollback journal", refusals_leave_the_board(half, "delete")),
        ("a refused claim and every read leave the board's bytes, write no WAL page and add no column, WAL", refusals_leave_the_board(half, "wal")),
        ("the board's own guards refuse a bad claim and clear a stale one, whoever writes", guards_hold(half)),
        ("a finding or a block filed while a claim waits is seen by it; a planted check-before-lock is caught", claims_race_safely(half)),
    ):
        if problems:
            differences += len(problems)
            print(f"  WRONG  {check} ({name}):")
            for problem in problems:
                print(f"    - {problem}")
        else:
            print(f"  right  {check} ({name})")

python_schema, node_schema = migrated_schema(PYTHON), migrated_schema(NODE)
if python_schema != node_schema or len([row for row in python_schema if row[0] == "trigger"]) != 7:
    differences += 1
    print("  DIFFER the schema a first claim leaves, its guards' text included")
    for row in sorted(set(python_schema) ^ set(node_schema), key=repr):
        print(f"    {row!r}")
else:
    print("  same   the schema a first claim leaves, its guards' text included")

for check, scenario in (
    ("a claim or a clear on a busy board refuses in the tool's words and leaves the board as it was", a_busy_board_refuses_cleanly),
    ("a claim or a clear SQLite ends with an error reports that error and leaves the board as it was", a_failed_transaction_is_reported_as_it_happened),
    ("validate names every claim a tampered board holds, and a missing column", validate_reads_a_tampered_board),
):
    said_node, problems_node = scenario(NODE)
    said_python, problems_python = scenario(PYTHON)
    problems = [f"node: {problem}" for problem in problems_node] + [f"python: {problem}" for problem in problems_python]
    for from_node, from_python in zip(said_node, said_python):
        if from_node != from_python:
            problems.append(f"the halves differ:\n      node  : {from_node!r}\n      python: {from_python!r}")
    if len(said_node) != len(said_python):
        problems.append("the halves ran a different number of steps")
    if problems:
        differences += len(problems)
        print(f"  WRONG  {check}:")
        for problem in problems:
            print(f"    - {problem}")
    else:
        print(f"  right  {check}")

for title, prefix, steps in (
    ("a started task waiting on a parent named after it started", "todo-started-parent-", STARTED_PARENT_STEPS),
    ("ids made by the board", "todo-ids-", ID_STEPS),
    ("ids made by the board, padding and other endings", "todo-ids-padding-", PADDING_STEPS),
    ("command paths no other catalog reaches", "todo-families-", FAMILY_STEPS),
):
    differences += scripted_problems(title, steps, scripted_transcript(prefix, steps, NODE), scripted_transcript(prefix, steps, PYTHON))

for check, scenario in (
    ("a trailing flag with no value changes nothing, in both halves", valueless_flags_change_nothing),
    ("explicit ids order by their ASCII digits as an exact number, then by code point", ids_order_exactly),
    ("the rendered next pick names its area", rendered_next_names_its_area),
    ("unsupported old ranking fields refuse actual candidates, and validate reports every row", legacy_severity_refusals),
    ("ordinary add increments an all-zero stored ASCII suffix", auto_id_after_zero),
):
    said_node, problems_node = scenario(NODE)
    said_python, problems_python = scenario(PYTHON)
    problems = [f"node: {problem}" for problem in problems_node] + [f"python: {problem}" for problem in problems_python]
    if len(said_node) != len(said_python):
        problems.append("the halves ran a different number of steps")
    for from_node, from_python in zip(said_node, said_python):
        if from_node != from_python:
            problems.append(f"the halves differ:\n      node  : {from_node[:600]!r}\n      python: {from_python[:600]!r}")
    if problems:
        differences += len(problems)
        print(f"  WRONG  {check}:")
        for problem in problems:
            print(f"    - {problem}")
    else:
        print(f"  right  {check}")

if differences:
    print(f"\n{differences} command(s) differ between the two implementations or print the wrong thing.")
    sys.exit(1)
print(f"\nBoth implementations agree on all {len(SCRIPT)} commands.")
print(f"They agree on all {len(TEST_STEPS)} test-state steps, all {len(MIXED_STEPS)} steps taken in turns and all"
      f" {len(edge_steps)} edge-case steps, and every step said what it must.")
scripted = sum(len([step for step in steps if step[0] != "setup"]) for steps in (STARTED_PARENT_STEPS, ID_STEPS, PADDING_STEPS, FAMILY_STEPS))
print(f"They agree on all {scripted} steps about started work, ids and the remaining command paths.")
