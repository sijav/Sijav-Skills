#!/usr/bin/env python3
"""Copy one codex session's context into a session of the logged-in account.

    python copy_session.py <source thread id> [<target thread id>] [--model M] [--cwd DIR] [--log FILE]

The owner, 2026-09-26: "a python file that takes a session id from codex and
takes another one (if empty creates another first and at the end return the
id) and replace the second ... with first with all the decrypted data"; and
"if the session is too much, the next one will send in batches ... and after
the last batch continue with the session".

What it does: reads the source session file and takes its readable history,
word for word (codex_context.export_context: every message, tool call, tool
result and readable reasoning summary; the encrypted parts are bound to the
account that made them and can be read by no one but OpenAI). It starts a
fresh session under the account codex is logged in with and puts that history
into it through `codex app-server` (thread/inject_items). History bigger than
half of the model's context window goes in batches, with a compaction
(thread/compact/start, finished when its turn completes) after every batch
but the last, so the session never holds more than its window. It prints
the new session's id on the last line.

A target id means "replace this account's session": codex's app-server can
append to a session but cannot empty one (thread/revert drops only whole
turns), so the replacement is the new session, and the target stays on disk
as it was. The caller records the returned id in place of the target.

A manual tool: the runner (codex_session.py) continues a session directly
on whatever account is logged in, which worked across the owner's two
accounts (2026-09-26). Use this when a direct continue fails with
"Encrypted content organization_id did not match the target organization",
then point the job's record at the printed id.

Every failure names its exact cause and the log file.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import codex_context as cc  # noqa: E402

BINARY = r"C:\Program Files\nodejs\codex.cmd"
DEFAULT_WINDOW = 200_000      # tokens, when the source file names no window
CHARS_PER_TOKEN = 3.5         # a cautious average for Turkish, English and JSON
BATCH_SHARE = 0.5             # of the window, per batch
COMPACT_WAIT = 15 * 60        # seconds for one compaction


def window_of(rollout: Path) -> int:
    """The model's context window the source session ran with (its
    task_started events), in tokens."""
    with rollout.open(encoding="utf-8") as fh:
        for line in fh:
            o = json.loads(line)
            p = o.get("payload") or {}
            if o.get("type") == "event_msg" and p.get("type") == "task_started" and p.get("model_context_window"):
                return int(p["model_context_window"])
    return DEFAULT_WINDOW


def batches(items: list[dict], budget_chars: int) -> list[list[dict]]:
    """Consecutive batches of at most budget_chars each. An item larger than
    a whole batch is cut to it, with a note of its real size: the model could
    not read it whole in any session."""
    out, cur, size = [], [], 0
    for it in items:
        text = it["content"][0]["text"]
        if len(text) > budget_chars:
            it = cc._message(it["role"], text[:budget_chars] + f"\n... (cut here: {len(text):,} characters in all)")
            text = it["content"][0]["text"]
        if cur and size + len(text) > budget_chars:
            out.append(cur)
            cur, size = [], 0
        cur.append(it)
        size += len(text)
    if cur:
        out.append(cur)
    return out


def copy_session(source: str, target: str | None = None, *, model: str = "gpt-6-astra",
                 cwd: Path | None = None, log: Path | None = None, binary: str = BINARY,
                 batch_chars: int | None = None) -> str:
    cwd = cwd or Path.cwd()
    log = log or Path.cwd() / f"copy-session-{dt.datetime.now():%Y%m%dT%H%M%S}.log"
    rollout = cc.rollout_path(source)
    items = cc.export_context(rollout)
    budget = batch_chars or int(window_of(rollout) * BATCH_SHARE * CHARS_PER_TOKEN)
    parts = batches(items, budget)
    with log.open("a", encoding="utf-8") as fh:
        fh.write(f"copy {source} ({rollout.name}, account {cc.creator_account(rollout)}) -> "
                 f"{'a new session replacing ' + target if target else 'a new session'} under account "
                 f"{cc.current_account_id()}: {len(items)} readable items in {len(parts)} batch(es) of at most "
                 f"{budget:,} characters\n")
    server = cc._AppServer(binary, cwd, log, [])
    try:
        server.send("initialize", {"clientInfo": {"name": "copy_session", "version": "1"}})
        server.send("initialized", notify=True)
        started = server.send("thread/start", {"cwd": str(cwd), "ephemeral": False, "model": model})
        thread = ((started or {}).get("thread") or {}).get("id")
        if not thread:
            raise cc.ContextError(f"thread/start gave no thread id: {json.dumps(started)[:500]}; log {log}")
        for n, part in enumerate(parts, 1):
            server.send("thread/inject_items", {"threadId": thread, "items": part})
            if n < len(parts):
                server.send("thread/compact/start", {"threadId": thread})
                turn = server.wait_turn(thread, COMPACT_WAIT)
                with log.open("a", encoding="utf-8") as fh:
                    fh.write(f"batch {n} of {len(parts)} compacted in {(turn.get('durationMs') or 0) / 1000:.1f}s\n")
        return thread
    finally:
        server.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("source")
    ap.add_argument("target", nargs="?", default=None)
    ap.add_argument("--model", default="gpt-6-astra")
    ap.add_argument("--cwd", default=None)
    ap.add_argument("--log", default=None)
    ap.add_argument("--batch-chars", type=int, default=None,
                    help="characters per batch (default: half the source model's window)")
    a = ap.parse_args(argv)
    try:
        thread = copy_session(a.source, a.target or None, model=a.model,
                              cwd=Path(a.cwd) if a.cwd else None, log=Path(a.log) if a.log else None,
                              batch_chars=a.batch_chars)
    except (cc.ContextError, OSError, ValueError) as e:
        print(f"COPY FAILED: {e}", file=sys.stderr)
        return 1
    print(thread)
    return 0


if __name__ == "__main__":
    sys.exit(main())
