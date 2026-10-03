#!/usr/bin/env python3
"""The loop skill's Stop hook: sends the session's own loop file back as its next prompt.

A loop is per session. Its file, `.claude/<name>loop<...>.local.md`, names the one session it
drives (`session:`), may name its board (`board:`) and its areas (`areas:`), which the to-do script
reads on every command, and says `driver: skill` when this skill's hooks drive it rather than the
project's own. The skill registers this hook only in a session that invokes the skill, so it never
runs anywhere else, and here it drives only a loop file that names this session and says
`driver: skill`. Anything else, including a loop the project's own hook drives: nothing printed,
the stop allowed. It never exits 2 and never raises.

Front matter it reads:

  driver: skill               this skill's hooks drive the loop
  session: "<session id>"     the only session it drives
  active: true                false: not armed
  iteration: 0                counted here, one per re-feed
  max_iterations: 0           0 means no cap
  completion_promise: "..."   the stop is allowed once the last reply holds <promise>...</promise>

`.stop` in the project root pauses every loop; `.claude/<loop file name>.stop` pauses this one.
Every decision is one line in `.claude/loop-stop.log`.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone


def split_law(text: str) -> tuple[dict[str, str], str]:
    """The front matter as strings, and the body after it."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fields = {}
    for line in text[3:end].splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip():
            fields[key.strip()] = value.strip().strip("\"'")
    return fields, text[end + 4 :].lstrip("\r\n")


def set_field(text: str, key: str, value: str) -> str:
    """The loop file with one front-matter field set (added when missing)."""
    end = text.find("\n---", 3)
    head, rest = text[:end], text[end:]
    line = re.compile(rf"^{re.escape(key)}:.*$", re.M)
    head = line.sub(f"{key}: {value}", head, count=1) if line.search(head) else f"{head}\n{key}: {value}"
    return head + rest


def own_loop(start: str, session: str) -> tuple[str, str] | None:
    """(project root, loop file) of the armed loop file this skill drives for this session."""
    if not session:
        return None
    directory = os.path.abspath(start)
    while True:
        folder = os.path.join(directory, ".claude")
        if os.path.isdir(folder):
            for name in sorted(os.listdir(folder)):
                if "loop" in name and name.endswith(".local.md"):
                    path = os.path.join(folder, name)
                    with open(path, encoding="utf-8") as f:
                        fields, _ = split_law(f.read())
                    if (fields.get("session") == session and fields.get("driver") == "skill"
                            and fields.get("active", "true").lower() != "false"):
                        return directory, path
        parent = os.path.dirname(directory)
        if parent == directory:
            return None
        directory = parent


def last_assistant_text(transcript_path: str | None) -> str:
    """The text of the last assistant message in the session's transcript (JSON lines)."""
    if not transcript_path or not os.path.exists(transcript_path):
        return ""
    last = ""
    with open(transcript_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if record.get("type") != "assistant":
                continue
            content = (record.get("message") or {}).get("content")
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                text = "".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
            else:
                text = ""
            if text:
                last = text
    return last


def log(root: str, line: str) -> None:
    try:
        with open(os.path.join(root, ".claude", "loop-stop.log"), "a", encoding="utf-8") as f:
            f.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {line}\n")
    except OSError:
        pass


def handle(event: dict) -> dict | None:
    """The Stop hook's output for this event, or None to print nothing and allow the stop."""
    session = str(event.get("session_id") or "").strip()
    start = event.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    found = own_loop(start, session)
    if found is None:
        return None
    root, path = found
    name = os.path.basename(path)[: -len(".local.md")]
    for stop in (os.path.join(root, ".stop"), path[: -len(".local.md")] + ".stop"):
        if os.path.exists(stop):
            log(root, f"{name} session={session} paused ({os.path.basename(stop)}): stop allowed")
            return None
    with open(path, encoding="utf-8") as f:
        text = f.read()
    fields, body = split_law(text)
    promise = fields.get("completion_promise", "")
    if promise and f"<promise>{promise}</promise>" in last_assistant_text(event.get("transcript_path")):
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(set_field(text, "active", "false"))
        log(root, f"{name} session={session} promise kept: loop finished, stop allowed")
        return None
    iteration = int(fields.get("iteration", "0") or 0)
    cap = int(fields.get("max_iterations", "0") or 0)
    if cap and iteration >= cap:
        log(root, f"{name} session={session} iteration cap {cap} reached: stop allowed")
        return None
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(set_field(text, "iteration", str(iteration + 1)))
    log(root, f"{name} session={session} loop file sent back (iteration {iteration + 1})")
    areas = fields.get("areas", "")
    header = (f"Loop {name}, iteration {iteration + 1}."
              + (f" Your areas: {areas}; the to-do script takes them from this loop file." if areas else "")
              + f" To pause: create .claude{os.sep}{name}.stop."
              + (f" Finish only when the exit condition holds, by replying <promise>{promise}</promise>." if promise else ""))
    return {"decision": "block", "reason": f"{header}\n\n{body}", "systemMessage": f"{name}: iteration {iteration + 1}"}


def main() -> int:
    try:
        event = json.loads(sys.stdin.read() or "{}")
        out = handle(event if isinstance(event, dict) else {})
        if out is not None:
            print(json.dumps(out))  # ASCII escapes: a Windows hook's stdout may be cp1252
    except Exception:  # a broken loop file or transcript must never block or disturb a session
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
