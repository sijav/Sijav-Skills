"""A codex session's readable history, and codex app-server over stdio: the
parts of copy_session.py, the manual copier (owner, 2026-09-26: "Keep the
copier, revert the rest").

Why a copy may be needed: codex replays a session's saved history, and its
encrypted parts (reasoning, compaction) can be refused under another account
("Encrypted content organization_id did not match the target organization",
openai/codex #13724); OpenAI offers no way to decrypt or re-encrypt them.
Between the owner's two accounts a direct continue worked (2026-09-26, a
session with 41 encrypted reasoning items and 1 encrypted compaction), so
the runner continues sessions directly; the copier is for when one fails.
The copy is the READABLE part, word for word: every message, tool call, tool
result and readable reasoning summary in the session file, in order,
including the turns a compaction later replaced, put into a fresh session
with the app-server's thread/inject_items ("persisted to the rollout and
included in subsequent model requests").

The login file (auth.json) is never read: the owner's rule is never to read,
copy or touch it or any login token. The account is named in the log only
when the caller gives it in CODEX_ACCOUNT_ID.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path


class ContextError(RuntimeError):
    """A failure whose message is the exact cause."""


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))


def current_account_id() -> str:
    """The account codex is logged in with, for the copy's log line only.

    Never read from the login file (auth.json): the owner's rule is never to
    read, copy or touch it. CODEX_ACCOUNT_ID names the account when the caller
    knows it; otherwise the log says only "the logged-in account"."""
    return os.environ.get("CODEX_ACCOUNT_ID") or "the logged-in account"


def rollout_path(thread_id: str) -> Path:
    """The session file of a thread, found by the id in its file name."""
    hits = sorted((codex_home() / "sessions").rglob(f"rollout-*-{thread_id}.jsonl"))
    if not hits:
        raise ContextError(f"no session file for thread {thread_id} under {codex_home() / 'sessions'}")
    return hits[-1]


def creator_account(rollout: Path) -> str | None:
    """The account a session was made under (its session_meta line)."""
    with rollout.open(encoding="utf-8") as fh:
        for line in fh:
            o = json.loads(line)
            if o.get("type") == "session_meta":
                return (o.get("payload") or {}).get("creator_account_id")
    return None


# Notes codex writes into a session itself as user-role messages. A new
# session writes its own, so copying them would only repeat stale ones
# (seen in 400 session files, 2026-09-26: <environment_context> 410 times,
# <recommended_plugins> 4 times; the other two are codex's AGENTS.md forms).
CODEX_OWN_NOTES = ("<environment_context>", "<recommended_plugins>", "<user_instructions>",
                   "# AGENTS.md instructions for ")


def _texts(content, drop: tuple[str, ...] = ()) -> list[str]:
    """The text parts of a message, minus parts that start with `drop`."""
    parts = [content] if isinstance(content, str) else [
        part["text"] for part in content or [] if isinstance(part, dict) and isinstance(part.get("text"), str)]
    return [t for t in parts if not t.lstrip().startswith(drop)]


def _message(role: str, text: str) -> dict:
    kind = "output_text" if role == "assistant" else "input_text"
    return {"type": "message", "role": role, "content": [{"type": kind, "text": text}]}


def export_context(rollout: Path) -> list[dict]:
    """The session's readable history as Responses API messages, in order.

    User and assistant messages are kept as they are; a tool call and its
    output become labelled assistant and user messages with the call's own
    text; a reasoning item gives its readable summary, if it has one. What
    codex writes itself is left out, because the new session gets its own:
    the developer messages (instructions, permissions) and the user-role
    notes in CODEX_OWN_NOTES (environment, plugins, AGENTS.md). Nothing
    encrypted is kept, and no item is dropped for any other reason."""
    items: list[dict] = []
    with rollout.open(encoding="utf-8") as fh:
        for line in fh:
            o = json.loads(line)
            if o.get("type") != "response_item":
                continue
            p = o.get("payload") or {}
            kind = p.get("type")
            if kind == "message" and p.get("role") in ("user", "assistant"):
                text = "\n".join(_texts(p.get("content"), CODEX_OWN_NOTES if p["role"] == "user" else ()))
                if text.strip():
                    items.append(_message(p["role"], text))
            elif kind in ("function_call", "custom_tool_call", "local_shell_call"):
                body = p.get("arguments") if kind == "function_call" else p.get("input", p.get("action"))
                body = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
                items.append(_message("assistant", f"[tool call {p.get('name') or kind}]\n{body}"))
            elif kind in ("function_call_output", "custom_tool_call_output"):
                out = p.get("output")
                out = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
                items.append(_message("user", f"[tool output]\n{out}"))
            elif kind == "reasoning":
                summary = "\n".join(_texts(p.get("summary")))
                if summary.strip():
                    items.append(_message("assistant", f"[my reasoning summary]\n{summary}"))
    return items


class _AppServer:
    """codex app-server over stdio: one JSON-RPC message per line."""

    def __init__(self, binary: str, cwd: Path, log: Path, overrides: list[str]):
        argv = [binary, "app-server", *sum((["-c", o] for o in overrides), [])]
        self.log = log
        self.proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
        self.err: list[str] = []
        threading.Thread(target=lambda: self.err.extend(self.proc.stderr), daemon=True).start()
        self.next_id = 0
        # notifications that arrived while send() waited for its answer,
        # kept for the next wait (a fast turn can end before the answer)
        self.pending: list[dict] = []
        self._write(f"command: {subprocess.list2cmdline(argv)}\ncwd: {cwd}\n")

    def _write(self, text: str) -> None:
        with self.log.open("a", encoding="utf-8") as fh:
            fh.write(text)

    def send(self, method: str, params: dict | None = None, notify: bool = False):
        msg: dict = {"method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            self.next_id += 1
            msg["id"] = self.next_id
        line = json.dumps(msg, ensure_ascii=False)
        self._write(f">>> {line[:2000]}\n")
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()
        if notify:
            return None
        while True:
            reply = self.proc.stdout.readline()
            if not reply:
                raise ContextError(f"codex app-server closed before answering {method}: "
                                   + "".join(self.err)[-800:] + f"; log {self.log}")
            self._write(f"<<< {reply.strip()[:2000]}\n")
            try:
                m = json.loads(reply)
            except ValueError:
                continue
            if m.get("id") != msg["id"]:
                if m.get("method"):
                    self.pending.append(m)  # a notification: the next wait reads it
                continue
            if "error" in m:
                raise ContextError(f"codex app-server refused {method}: {json.dumps(m['error'], ensure_ascii=False)[:800]}; log {self.log}")
            return m.get("result")

    def _read_until(self, found, what: str, timeout: float) -> dict:
        """Read notifications (those send() kept first) until found(message)
        gives a result. An error notification ends the wait with codex's own
        words, unless codex says it will retry."""
        import time
        deadline = time.time() + timeout
        result: list = []

        def check(m: dict) -> bool:
            method = m.get("method") or ""
            if method == "error" and (m.get("params") or {}).get("willRetry"):
                return False  # codex retries by itself
            if method == "error" or method.endswith("/failed"):
                result.append({"error": m})
                return True
            got = found(m)
            if got is not None:
                result.append(got)
                return True
            return False

        def read():
            while self.pending:
                if check(self.pending.pop(0)):
                    return
            while True:
                line = self.proc.stdout.readline()
                if not line:
                    return
                self._write(f"<<< {line.strip()[:2000]}\n")
                try:
                    m = json.loads(line)
                except ValueError:
                    continue
                if check(m):
                    return

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        reader.join(max(0.0, deadline - time.time()))
        if not result:
            raise ContextError(f"no {what} within {timeout:.0f}s"
                               + ("; codex app-server ended: " + "".join(self.err)[-800:] if self.proc.poll() is not None else "")
                               + f"; log {self.log}")
        if "error" in result[0]:
            raise ContextError(f"codex app-server failed while waiting for {what}: "
                               f"{json.dumps(result[0]['error'], ensure_ascii=False)[:800]}; log {self.log}")
        return result[0]

    def wait(self, method: str, thread: str, timeout: float) -> dict:
        """Read until the notification `method` for `thread` arrives."""
        def found(m):
            params = m.get("params") or {}
            return params if m.get("method") == method and params.get("threadId", thread) == thread else None
        return self._read_until(found, f"{method} for thread {thread}", timeout)

    def wait_turn(self, thread: str, timeout: float) -> dict:
        """Read until the running turn on `thread` ends. A compaction runs as
        a turn: codex 0.157.0 ends it with item/completed (a contextCompaction
        item) and turn/completed, and sends no thread/compacted (seen live on
        2026-09-26). A turn that ends in any status but "completed" raises
        with codex's own error."""
        def found(m):
            params = m.get("params") or {}
            return params if m.get("method") == "turn/completed" and params.get("threadId") == thread else None
        turn = self._read_until(found, f"turn/completed for thread {thread}", timeout).get("turn") or {}
        if turn.get("status") != "completed":
            raise ContextError(f"the turn on thread {thread} ended {turn.get('status')!r}: "
                               f"{json.dumps(turn.get('error'), ensure_ascii=False)[:800]}; log {self.log}")
        return turn

    def close(self) -> None:
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            self.proc.kill()
        self._write(f"--- app-server exit: {self.proc.returncode}\n")
