#!/usr/bin/env python3
"""Codex hook entry point for the Sijav loop.

    loop_hook.py stop            # Stop hook
    loop_hook.py session-start   # SessionStart hook, matcher "compact"

Reads the hook's JSON from stdin and prints at most one JSON object to stdout:
nothing to allow a stop, {"decision": "block", "reason": ...} to continue,
{"hookSpecificOutput": ...} to reload the law after compaction, or
{"systemMessage": ...} when something failed. Diagnostics go to stderr. The
exit code is always 0, because exit 2 would make Codex treat stderr as a block
reason and turn an error into a continuation.

So any non-zero status is a launcher failure, and the one that matters is
CPython's exit 2 for a script it cannot open: Codex would read it as a block.
The hook commands therefore never name this file as Python's script. They run
`python -c "<bootstrap>" "<path to loop_hook.py>" <event>`: the bootstrap
drops the working directory from sys.path, runs this file with runpy when it
exists, and otherwise prints {"systemMessage": "... did not start ..."} and
exits 0. Other launcher failures exit non-zero but never 2 -- a missing
interpreter gives 1 under PowerShell, 9009 under cmd, 127 under sh; a missing
sijav_loop.py gives 1 -- and Codex reports them as failed hook runs, which
continue nothing.

The command is one line valid in every hook shell Codex may use: PowerShell
(`-Command`, which this Windows deployment uses), cmd (`/C "<command>"`) and
sh (`-lc`). It needs an unquoted interpreter (a quoted first token is a
PowerShell parse error, exit 1) and a bootstrap free of double quotes, $, %,
backticks, backslashes and shell operators.
"""

import contextlib
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sijav_loop  # noqa: E402

HANDLERS = {"stop": sijav_loop.stop_hook, "session-start": sijav_loop.session_start_hook}


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if len(argv) != 1 or argv[0] not in HANDLERS:
        output = sijav_loop._failed(f"loop_hook.py needs one of {', '.join(HANDLERS)}, got {argv!r}")
    else:
        try:
            event = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
        except ValueError as exc:
            output = sijav_loop._failed(f"the hook input is not JSON ({exc}); nothing done")
        else:
            try:
                output = HANDLERS[argv[0]](event)
            except Exception as exc:  # a bug must not become a continuation
                traceback.print_exc()
                output = sijav_loop._failed(f"internal error, nothing done: {type(exc).__name__}: {exc}")
    if output is not None:
        sys.stdout.write(json.dumps(output, ensure_ascii=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
