#!/usr/bin/env python3
"""The loop skill's SessionStart hook (matcher "compact"): puts the
project's loop law back in context after a compaction.

A project's Stop hook re-feeds its law only when a turn ends. A compaction
in the middle of a turn summarises the law out of the context, and nothing
brought it back until that turn ended: on 2026-09-23 a
compaction at 21:50 UTC was followed by 36 minutes of work without the law
in view -- step 1 was skipped and a codex output went to jev unchecked.
The skill registers this hook in its frontmatter, so every project that
engages the loop gets it for the rest of that session.

It finds the law as the skill does: the project's `.claude/*loop*.local.md`.
It prints nothing when there is no law, when a `.stop` sentinel pauses the
loop, or when the project's own settings already run a SessionStart
"compact" hook -- one copy of the
law, not two. Every run appends one line to `.claude/loop-after-compact.log`
in the project, so whether it fired after a compaction can be checked.

This file used to rebuild context from agent/RALPH.md and todo.mjs for the
skill's first design; nothing has referenced it since the skill became
"engage the project's own loop".
"""

from __future__ import annotations

import glob
import json
import os
import sys
from datetime import UTC, datetime

ORDER = (
    "A COMPACTION JUST HAPPENED. The loop law is below. The summary above is"
    " a claim, not a record: check a fact before you rely on it."
)


def project_root(start: str) -> str | None:
    """The nearest directory holding `.claude/*loop*.local.md`."""
    current = os.path.abspath(start)
    while True:
        if glob.glob(os.path.join(current, ".claude", "*loop*.local.md")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def has_own_hook(root: str) -> bool:
    """Whether the project's settings already run a SessionStart hook on
    compaction."""
    for name in ("settings.local.json", "settings.json"):
        path = os.path.join(root, ".claude", name)
        try:
            with open(path, encoding="utf-8") as f:
                hooks = json.load(f).get("hooks", {})
        except (OSError, ValueError):
            continue
        for entry in hooks.get("SessionStart", []):
            if entry.get("matcher") in ("compact", "*", ""):
                return True
    return False


def body(text: str) -> str:
    """The law without its front matter (the loop's own counters)."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4 :].lstrip("\r\n")
    return text


def log(root: str, source: str, action: str) -> None:
    try:
        with open(
            os.path.join(root, ".claude", "loop-after-compact.log"),
            "a",
            encoding="utf-8",
        ) as f:
            f.write(f"{datetime.now(UTC).isoformat()} source={source} {action}\n")
    except OSError as exc:
        print(f"compact.py: could not write the log: {exc}", file=sys.stderr)


def main() -> int:
    try:
        event = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        event = {}
    source = str(event.get("source") or "unknown")
    start = os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or os.getcwd()
    root = project_root(start)
    if root is None:
        print(f"compact.py: no .claude/*loop*.local.md above {start}", file=sys.stderr)
        return 0
    if os.path.exists(os.path.join(root, ".stop")):
        log(root, source, "paused (.stop): nothing printed")
        return 0
    if has_own_hook(root):
        log(root, source, "deferred: the project runs its own compaction hook")
        return 0
    law_path = min(glob.glob(os.path.join(root, ".claude", "*loop*.local.md")))
    with open(law_path, encoding="utf-8") as f:
        law = body(f.read())
    log(root, source, f"printed {law_path}")
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": f"{ORDER}\n\n# THE LAW ({law_path})\n\n{law}",
                }
            }
        )  # ASCII escapes: a Windows hook's stdout may be cp1252
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
