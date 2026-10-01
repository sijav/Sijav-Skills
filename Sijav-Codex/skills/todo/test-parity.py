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

import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
NODE_SCRIPT = os.path.join(HERE, "todo.mjs")
PY_SCRIPT = os.path.join(HERE, "todo.py")

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
    # Failure paths print to stderr and exit non-zero; those must match too.
    ["show", "SB-999"],
    ["add", "--title", "no fields"],
    ["move", "SB-001", "nonsense"],
    ["edit", "SB-001"],
    ["frobnicate"],
]


def run(interpreter, script, directory, args):
    # Bytes, decoded as UTF-8 and nothing else. text=True read the pipes in
    # universal-newline mode, which folds \r\n into \n, so Python printing \r\n
    # on Windows while Node printed \n passed here and differed in every file
    # either half's output was written to, KN-482.
    result = subprocess.run([interpreter, script, *args], cwd=directory, capture_output=True)
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


node = transcript("node", NODE_SCRIPT)
python = transcript(sys.executable, PY_SCRIPT)

differences = 0

boardless_node = refuses_without_a_board("node", NODE_SCRIPT)
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

if differences:
    print(f"\n{differences} command(s) differ between the two implementations.")
    sys.exit(1)
print(f"\nBoth implementations agree on all {len(SCRIPT)} commands.")
