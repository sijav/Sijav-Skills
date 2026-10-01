#!/usr/bin/env python3
"""Sijav-Codex loop: project-local state, the operator commands and the hook logic.

The loop's only durable state is <project>/.codex/sijav-loop/state.json. It
names the project root and the law by absolute path, the one Codex session
that owns the loop, the iteration count, the cap, the exact completion promise
and the pause sentinels. The law is read and never written: a Claude loop that
keeps its own session and counters in the law's front matter is left alone.

    start   --project-root DIR --law FILE (--session ID | --session-from-env)
            (--max-iterations N | --max-iterations-from-law)
            (--promise TEXT | --promise-from-law)
            [--sentinel FILE ...] [--takeover PREVIOUS_SESSION] [--restart]
            [--clear-sentinels [--clear-claude-pause]] [--confirm-root]
    resume  (--session ID | --session-from-env) [--takeover PREVIOUS_SESSION]
            [--clear-sentinels [--clear-claude-pause]]
    pause   --reason TEXT [--session ID]
    stop    --reason TEXT [--session ID]
    reset   (--session ID | --session-from-env) [--takeover PREVIOUS_SESSION]
    reset   --discard-malformed
    status  [--session ID] [--json]

Every command but start finds the state from --project-root, or by walking up
from --cwd (default: this process's directory).

Identity: inside Codex, CODEX_SESSION_ID names the root session and
CODEX_THREAD_ID the thread running the command; they are equal only in the
root. When either is present, start, resume and reset refuse unless both are
present and equal, and --session must match them; --session-from-env takes
CODEX_SESSION_ID once that check passes. Outside Codex neither is set and
--session must name the real Codex session id. The hooks (loop_hook.py) call
stop_hook() and session_start_hook() below.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone

if os.name == "nt":
    import msvcrt
else:
    import fcntl

SCHEMA = "sijav-codex-loop/1"
STATE_DIR = os.path.join(".codex", "sijav-loop")
STATUSES = ("active", "paused", "stopped", "complete", "exhausted")
# Seconds. Well inside the hooks' 30 s timeout, so a held lock is reported by
# this code with its cause instead of by Codex killing the hook.
LOCK_TIMEOUT = 10.0
# Windows refuses a rename onto a file another program (a virus scanner, an
# editor) holds open; that clears in milliseconds, so retry briefly.
REPLACE_ATTEMPTS = 5
EVENTS_MAX_BYTES = 1 << 20
IDENTITY_VARIABLES = ("CODEX_THREAD_ID", "CODEX_SESSION_ID")

ORDER = (
    "A COMPACTION JUST HAPPENED. The Sijav loop law is below, re-read in full"
    " from disk. The summary above is a claim, not a record: check a fact"
    " against the project's records before relying on it."
)


class LoopError(Exception):
    """A failure with a cause the operator can act on."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def warn(message: str) -> None:
    print(f"[sijav-loop] {message}", file=sys.stderr)


# ---------------------------------------------------------------- locations


def state_dir(root: str) -> str:
    return os.path.join(root, STATE_DIR)


def state_path(root: str) -> str:
    return os.path.join(state_dir(root), "state.json")


def lock_path(root: str) -> str:
    return os.path.join(state_dir(root), "state.lock")


def events_path(root: str) -> str:
    return os.path.join(state_dir(root), "events.jsonl")


def _canonical(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


def same_path(a: str, b: str) -> bool:
    return _canonical(a) == _canonical(b)


def inside(path: str, parent: str) -> bool:
    """Whether `path` is `parent` or below it, after resolving links."""
    path, parent = _canonical(path), _canonical(parent)
    return path == parent or path.startswith(parent.rstrip(os.sep) + os.sep)


def find_root(start: str) -> str | None:
    """The nearest directory at or above `start` holding loop state, or None.

    A state.json that exists but cannot be read still counts as found, so a
    broken state is reported instead of silently skipped for an outer one.
    """
    current = os.path.abspath(start)
    while True:
        if os.path.lexists(state_path(current)):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


# ----------------------------------------------------------------- identity


def native_identity(environ=None) -> tuple[str | None, str | None]:
    """(verified root session id or None, problem or None).

    Codex gives every command it runs CODEX_SESSION_ID (the root session) and
    CODEX_THREAD_ID (the thread running it). Neither set: not inside Codex.
    Both set and equal: the root session. Anything else is a descendant or an
    environment that cannot be verified.
    """
    env = os.environ if environ is None else environ
    thread = (env.get("CODEX_THREAD_ID") or "").strip()
    session = (env.get("CODEX_SESSION_ID") or "").strip()
    if not thread and not session:
        return None, None
    if not thread or not session:
        missing = "CODEX_THREAD_ID" if not thread else "CODEX_SESSION_ID"
        return None, f"{missing} is not set while the other is, so this cannot be verified as the root session"
    if thread != session:
        return None, (
            f"this runs in a descendant thread (CODEX_THREAD_ID {thread} is not"
            f" CODEX_SESSION_ID {session}); only the root session may claim the loop"
        )
    return session, None


def _session_arg(value: str) -> str:
    session = (value or "").strip()
    if not session or len(session) > 256 or any(c.isspace() or ord(c) < 32 for c in session):
        raise LoopError(f"--session {value!r} is not a session id; pass the real Codex session id")
    return session


def claimant(args) -> str:
    """The session id a claiming command acts for, checked against Codex's
    own identity variables when they are present."""
    verified, problem = native_identity()
    if problem:
        raise LoopError(problem)
    if getattr(args, "session_from_env", False):
        if not verified:
            raise LoopError(
                "--session-from-env: CODEX_SESSION_ID and CODEX_THREAD_ID are not set, so this"
                " is not running inside a Codex session; pass --session with the real session id"
            )
        return verified
    session = _session_arg(args.session)
    if verified and session != verified:
        raise LoopError(f"--session {session} is not this Codex session ({verified})")
    return session


# ----------------------------------------------------------- lock and write


def _lock(handle) -> None:
    handle.seek(0)
    if os.name == "nt":
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(handle) -> None:
    handle.seek(0)
    if os.name == "nt":
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def locked(root: str, timeout: float = LOCK_TIMEOUT):
    """Hold the state lock. The OS releases it if the holder dies, so a
    killed hook never leaves a stale lock behind."""
    path = lock_path(root)
    try:
        handle = open(path, "a+b")
    except OSError as exc:
        raise LoopError(f"cannot open the lock file {path}: {exc}") from exc
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                _lock(handle)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise LoopError(
                        f"could not lock {path} within {timeout:g}s; another"
                        f" process holds it ({exc.strerror or exc})"
                    ) from exc
                time.sleep(0.02)
        try:
            yield
        finally:
            _unlock(handle)
    finally:
        handle.close()


def write_json_atomic(path: str, data: dict) -> None:
    """Write `data` to `path` through a temporary file and one rename: a
    reader sees the old state or the new one, never half of either."""
    directory = os.path.dirname(path)
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    try:
        fd, tmp = tempfile.mkstemp(prefix=".state-", suffix=".tmp", dir=directory)
    except OSError as exc:
        raise LoopError(f"could not create a temporary file in {directory}: {exc}") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(REPLACE_ATTEMPTS):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(0.05)
    except OSError as exc:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise LoopError(f"could not write {path}: {exc}") from exc


def record_event(root: str, entry: dict) -> None:
    """Append one line to the project's hook record. Ids, counts and a hash
    of the final message are kept; message text, transcript paths and
    environment are not. A failure here never changes a decision."""
    path = events_path(root)
    try:
        if os.path.exists(path) and os.path.getsize(path) > EVENTS_MAX_BYTES:
            os.replace(path, path[: -len(".jsonl")] + ".1.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=True) + "\n")
    except OSError as exc:
        warn(f"could not record the event in {path}: {exc}")


# -------------------------------------------------------------------- state


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def promise_problem(promise) -> str | None:
    if not isinstance(promise, str) or not promise:
        return (
            "completion_promise is missing or empty; it has no safe default,"
            " because guessing it guesses the word that ends the run"
        )
    if promise != promise.strip() or any(c in promise for c in "<>\r\n"):
        return (
            f"completion_promise {promise!r} must be one line without"
            " surrounding spaces or angle brackets"
        )
    return None


def sentinel_problem(path: str, root: str, law: str) -> str | None:
    """Why `path` may not be a pause sentinel of the project at `root`. A
    sentinel is moved aside on an explicit clear, so it must never name the
    law, the loop's own state, or anything outside the project."""
    if not inside(path, root) or same_path(path, root):
        return f"pause sentinel {path} is not inside the project root {root}"
    if same_path(path, law):
        return f"pause sentinel {path} is the law itself"
    if inside(path, state_dir(root)):
        return f"pause sentinel {path} is inside the loop's own state directory"
    if os.path.isdir(path):
        return f"pause sentinel {path} is a directory"
    return None


def state_problem(state, root: str) -> str | None:
    """Why `state` cannot drive the loop at `root`, or None."""
    if not isinstance(state, dict):
        return "the state is not a JSON object"
    if state.get("schema") != SCHEMA:
        return f"schema is {state.get('schema')!r}, expected {SCHEMA!r}"
    for key in ("project_root", "law_path"):
        value = state.get(key)
        if not isinstance(value, str) or not os.path.isabs(value):
            return f"{key} must be an absolute path, got {value!r}"
    if not same_path(state["project_root"], root):
        return (
            f"project_root is {state['project_root']!r} but the state lives"
            f" under {root!r}; it was copied or moved and does not describe"
            " this project"
        )
    if inside(state["law_path"], state_dir(root)):
        return "law_path is inside the loop's own state directory"
    owner = state.get("owner_session")
    if not isinstance(owner, str) or not owner.strip():
        return "owner_session is missing or empty"
    if state.get("status") not in STATUSES:
        return f"status is {state.get('status')!r}, expected one of {', '.join(STATUSES)}"
    if not isinstance(state.get("status_reason"), str):
        return "status_reason is missing"
    cap = state.get("max_iterations")
    if not _is_int(cap) or cap < 1:
        return f"max_iterations must be a positive integer, got {cap!r}"
    iteration = state.get("iteration")
    if not _is_int(iteration) or not 0 <= iteration <= cap:
        return f"iteration must be an integer from 0 to {cap}, got {iteration!r}"
    problem = promise_problem(state.get("completion_promise"))
    if problem:
        return problem
    sentinels = state.get("pause_sentinels")
    if (
        not isinstance(sentinels, list)
        or not sentinels
        or not all(isinstance(s, str) and os.path.isabs(s) for s in sentinels)
    ):
        return f"pause_sentinels must be a non-empty list of absolute paths, got {sentinels!r}"
    for sentinel in sentinels:
        problem = sentinel_problem(sentinel, root, state["law_path"])
        if problem and "is a directory" not in problem:
            return problem
    last = state.get("last_stop_fingerprint")
    if last is not None and not isinstance(last, str):
        return "last_stop_fingerprint must be a string or null"
    return None


def read_state(root: str) -> dict:
    path = state_path(root)
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError) as exc:
        raise LoopError(f"cannot read {path}: {exc}") from exc
    try:
        state = json.loads(text)
    except ValueError as exc:
        raise LoopError(f"{path} is not valid JSON: {exc}") from exc
    problem = state_problem(state, root)
    if problem:
        raise LoopError(f"{path}: {problem}")
    return state


def write_state(root: str, state: dict) -> None:
    problem = state_problem(state, root)
    if problem:  # a bug, not an operator error; never write what cannot be read
        raise LoopError(f"refusing to write an invalid state: {problem}")
    write_json_atomic(state_path(root), state)


def present_sentinels(state: dict) -> list[str]:
    return [s for s in state["pause_sentinels"] if os.path.lexists(s)]


# ---------------------------------------------------------------------- law

_FRONT = re.compile(r"---[ \t]*\n(.*?)^---[ \t]*(?:\n|\Z)", re.S | re.M)


def split_front_matter(text: str, path: str) -> tuple[dict | None, str]:
    """(fields, body). Fields are the flat `key: value` lines of a front
    matter that opens the file; None when there is none."""
    if not re.match(r"---[ \t]*\n", text):
        return None, text
    m = _FRONT.match(text)
    if not m:
        raise LoopError(
            f"the law {path} opens a front matter with --- but never closes it"
        )
    fields: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" not in line or line[:1] in (" ", "\t", "#"):
            continue
        key, value = line.split(":", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        fields.setdefault(key.strip(), value)
    return fields, text[m.end():].lstrip("\n")


def read_law(path: str) -> tuple[dict | None, str]:
    try:
        with open(path, encoding="utf-8-sig") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError) as exc:
        raise LoopError(f"cannot read the law {path}: {exc}") from exc
    front, body = split_front_matter(text, path)
    if not body.strip():
        raise LoopError(f"the law {path} has no body to send")
    return front, body


def cap_from_law(front: dict | None, path: str) -> int:
    value = (front or {}).get("max_iterations")
    if value is None:
        raise LoopError(f"--max-iterations-from-law: {path} has no max_iterations in its front matter")
    if not re.fullmatch(r"[0-9]+", value) or int(value) < 1:
        raise LoopError(
            f"--max-iterations-from-law: {path} has max_iterations {value!r};"
            " the Codex loop needs a positive whole number (0, 'unlimited',"
            " is not accepted)"
        )
    return int(value)


def promise_from_law(front: dict | None, path: str) -> str:
    value = (front or {}).get("completion_promise")
    if value is None:
        raise LoopError(f"--promise-from-law: {path} has no completion_promise in its front matter")
    problem = promise_problem(value)
    if problem:
        raise LoopError(f"--promise-from-law: {path}: {problem}")
    return value


# ------------------------------------------------------------- completion

_FENCE = re.compile(r"[ ]{0,3}(`{3,}|~{3,})")


def promise_matched(message, promise: str) -> bool:
    """Whether `message` -- the turn's final assistant message -- completes
    the loop.

    It does exactly when its last non-blank line, with trailing whitespace
    removed, is `<promise>PROMISE</promise>` with no leading whitespace and
    that line is not inside an open ``` or ~~~ fence. Case and spacing are
    exact. A tag elsewhere in the message, quoted, indented, in backticks, in
    a blockquote, or in an earlier message does not count.
    """
    if not isinstance(message, str) or not promise:
        return False
    lines = message.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    last = next((i for i in range(len(lines) - 1, -1, -1) if lines[i].strip()), None)
    if last is None or lines[last].rstrip() != f"<promise>{promise}</promise>":
        return False
    fence = None
    for line in lines[:last]:
        m = _FENCE.match(line)
        if not m:
            continue
        marker = m.group(1)
        if fence is None:
            fence = marker
        elif marker[0] == fence[0] and len(marker) >= len(fence) and not line[m.end():].strip():
            fence = None
    return fence is None


def stop_fingerprint(session: str, event: dict) -> str:
    """A hash of what the hook can see of one Stop callback, kept only for
    diagnostics. It cannot tell a replay from a new callback: a native
    continuation keeps the session, turn_id and stop_hook_active, and a model
    can legitimately repeat its final message while making progress the hook
    never sees. So every handled callback is counted against the cap, and an
    actual duplicate delivery costs one iteration instead of halting the loop
    or escaping the cap. stop_hook_active is part of the hash, so a turn's
    first Stop (false) never matches its second (true)."""
    message = event.get("last_assistant_message")
    parts = (
        session,
        str(event.get("turn_id")),
        "1" if event.get("stop_hook_active") else "0",
        message if isinstance(message, str) else "\0null",
    )
    h = hashlib.sha256()
    for part in parts:
        data = part.encode("utf-8", "surrogatepass")
        h.update(len(data).to_bytes(8, "big") + data)
    return h.hexdigest()


# -------------------------------------------------------------------- hooks


def _failed(message: str) -> dict:
    """Stop or SessionStart output for an error: valid JSON that drives
    nothing, with the cause on stderr and in the UI."""
    warn(message)
    return {"systemMessage": f"Sijav loop: {message}"}


def _identity(event, expected: str) -> tuple[str, str]:
    if not isinstance(event, dict):
        raise LoopError("the hook input is not a JSON object")
    name = event.get("hook_event_name")
    if name != expected:
        raise LoopError(f"hook_event_name is {name!r}, expected {expected!r}")
    session = event.get("session_id")
    if not isinstance(session, str) or not session.strip():
        raise LoopError("session_id is missing or empty")
    cwd = event.get("cwd")
    if not isinstance(cwd, str) or not os.path.isabs(cwd):
        raise LoopError(f"cwd must be an absolute path, got {cwd!r}")
    return session.strip(), cwd


def _message_digest(message) -> dict:
    if not isinstance(message, str):
        return {"message_chars": None, "message_sha256": None}
    data = message.encode("utf-8", "surrogatepass")
    return {"message_chars": len(message), "message_sha256": hashlib.sha256(data).hexdigest()}


def continuation(state: dict, body: str) -> str:
    return (
        f"SIJAV LOOP CONTINUATION {state['iteration']} of {state['max_iterations']}."
        " Continue the current item from its first unfinished step under the"
        " law below. Check facts against the project's records; a summary is a"
        " claim, not proof.\n"
        f"End your final message with the line <promise>{state['completion_promise']}</promise>"
        " only when the law's exit condition is actually met. Reaching the"
        " iteration cap is not completion. Only the owner may pause, by"
        f" creating {state['pause_sentinels'][0]}.\n\n"
        f"# THE LAW ({state['law_path']})\n\n{body}"
    )


def compaction_context(state: dict, body: str) -> str:
    return (
        f"{ORDER} Loop iteration {state['iteration']} of {state['max_iterations']};"
        f" end a final message with <promise>{state['completion_promise']}</promise>"
        " only when the law's exit condition holds.\n\n"
        f"# THE LAW ({state['law_path']})\n\n{body}"
    )


def _finish(root: str, state: dict, entry: dict, fingerprint: str, status: str, reason: str) -> dict:
    state.update(status=status, status_reason=reason, updated_at=now(), last_stop_fingerprint=fingerprint)
    entry.update(outcome=status, detail=reason)
    try:
        write_state(root, state)
    except LoopError as exc:
        entry.update(outcome="error", detail=f"{status} not recorded: {exc}")
        return _failed(
            f"stop allowed ({reason}), but the {status} status was not saved: {exc}."
            f" The saved state still says active at iteration {state['iteration']}"
            f" of {state['max_iterations']}, so this session's next stop will"
            " continue the loop until the cap; run `sijav_loop.py stop` to end it"
        )
    warn(f"{status}: {reason}; stop allowed")
    return {"systemMessage": f"Sijav loop {status}: {reason}"}


def _decide_stop(root: str, session: str, event: dict, entry: dict) -> dict | None:
    try:
        state = read_state(root)
    except LoopError as exc:
        entry.update(outcome="error", detail=str(exc))
        return _failed(f"no continuation sent: {exc}")
    owner = state["owner_session"]
    entry.update(owner_session=owner, iteration=state["iteration"], max_iterations=state["max_iterations"])
    if session != owner:
        entry["outcome"] = "other_session"
        warn(f"session {session} does not own the loop (owner {owner}); stop allowed")
        return None
    if state["status"] != "active":
        entry.update(outcome="not_active", detail=f"{state['status']}: {state['status_reason']}")
        warn(f"the loop is {state['status']} ({state['status_reason']}); stop allowed")
        return None
    fingerprint = stop_fingerprint(session, event)
    entry["same_as_previous_stop"] = fingerprint == state.get("last_stop_fingerprint")
    turn = event.get("turn_id")
    restarted = state.get("restarted_from") or {}
    if turn is not None and turn == restarted.get("last_turn_id"):
        entry["restarted_within_turn"] = True
        warn(f"the loop was restarted inside turn {turn}, whose earlier run had reached iteration"
             f" {restarted.get('iteration')}; the restart is recorded in events.jsonl")
    if promise_matched(event.get("last_assistant_message"), state["completion_promise"]):
        reason = f"the exact completion promise ended the final assistant message of turn {turn}"
        return _finish(root, state, entry, fingerprint, "complete", reason)
    sentinels = present_sentinels(state)
    if sentinels:
        entry.update(outcome="paused_by_sentinel", detail=sentinels[0])
        warn(f"pause sentinel {sentinels[0]} exists; stop allowed")
        return None
    if state["iteration"] >= state["max_iterations"]:
        reason = f"iteration cap {state['max_iterations']} reached without the completion promise; this is not completion"
        return _finish(root, state, entry, fingerprint, "exhausted", reason)
    try:
        _, body = read_law(state["law_path"])
    except LoopError as exc:
        entry.update(outcome="error", detail=str(exc))
        return _failed(f"no continuation sent: {exc}")
    state["iteration"] += 1
    state["last_stop_fingerprint"] = fingerprint
    state["last_continued"] = {"turn_id": turn, "at": now()}
    state["updated_at"] = now()
    try:
        write_state(root, state)
    except LoopError as exc:
        entry.update(outcome="error", detail=f"iteration {state['iteration']} not recorded: {exc}")
        return _failed(f"loop halted without a continuation: could not record iteration {state['iteration']}: {exc}")
    entry.update(outcome="continued", iteration=state["iteration"])
    return {
        "decision": "block",
        "reason": continuation(state, body),
        "systemMessage": (
            f"Sijav loop continuation {state['iteration']}/{state['max_iterations']}:"
            f" law re-fed. Pause: create {state['pause_sentinels'][0]}."
        ),
    }


def _decide_compact(root: str, session: str, source, entry: dict) -> dict | None:
    if source != "compact":
        entry["outcome"] = "ignored_source"
        return None
    try:
        state = read_state(root)
    except LoopError as exc:
        entry.update(outcome="error", detail=str(exc))
        return _failed(f"the law was not reloaded after compaction: {exc}")
    owner = state["owner_session"]
    entry.update(owner_session=owner, iteration=state["iteration"], max_iterations=state["max_iterations"])
    if session != owner:
        entry["outcome"] = "other_session"
        warn(f"session {session} does not own the loop (owner {owner}); no law added")
        return None
    if state["status"] != "active":
        entry.update(outcome="not_active", detail=f"{state['status']}: {state['status_reason']}")
        warn(f"the loop is {state['status']}; no law added")
        return None
    sentinels = present_sentinels(state)
    if sentinels:
        entry.update(outcome="paused_by_sentinel", detail=sentinels[0])
        warn(f"pause sentinel {sentinels[0]} exists; no law added")
        return None
    try:
        _, body = read_law(state["law_path"])
    except LoopError as exc:
        entry.update(outcome="error", detail=str(exc))
        return _failed(f"the law was not reloaded after compaction: {exc}")
    entry["outcome"] = "law_reloaded"
    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": compaction_context(state, body),
        }
    }


def _run_hook(event, name: str, decide, lock_timeout: float) -> dict | None:
    try:
        session, cwd = _identity(event, name)
    except LoopError as exc:
        return _failed(f"{name} hook input rejected: {exc}")
    root = find_root(cwd)
    if root is None:
        warn(f"not armed: no {os.path.join(STATE_DIR, 'state.json')} at or above {cwd}")
        return None
    entry = {
        "ts": now(),
        "event": name,
        "session_id": session,
        "turn_id": event.get("turn_id"),
        "cwd": cwd,
    }
    # The payload's session_id is the shared root session, so a descendant
    # thread's hook would look like the owner. If Codex gives the hook process
    # thread and session ids that differ, this is a descendant: drive nothing.
    _, descendant = native_identity()
    if descendant and "descendant" in descendant:
        entry.update(outcome="descendant_thread", detail=descendant)
        record_event(root, entry)
        warn(f"{descendant}; nothing done")
        return None
    try:
        with locked(root, lock_timeout):
            output = decide(root, session, entry)
            record_event(root, entry)
            return output
    except LoopError as exc:  # the lock itself
        entry.update(outcome="error", detail=str(exc))
        record_event(root, entry)
        return _failed(f"nothing done: {exc}")


def stop_hook(event, lock_timeout: float = LOCK_TIMEOUT) -> dict | None:
    """The Stop hook's stdout object, or None to print nothing (allow the stop)."""

    def decide(root, session, entry):
        entry.update(stop_hook_active=event.get("stop_hook_active"), **_message_digest(event.get("last_assistant_message")))
        return _decide_stop(root, session, event, entry)

    return _run_hook(event, "Stop", decide, lock_timeout)


def session_start_hook(event, lock_timeout: float = LOCK_TIMEOUT) -> dict | None:
    """The SessionStart hook's stdout object, or None to print nothing."""

    def decide(root, session, entry):
        entry["source"] = event.get("source")
        return _decide_compact(root, session, event.get("source"), entry)

    return _run_hook(event, "SessionStart", decide, lock_timeout)


# ---------------------------------------------------------------- commands


def _unique_paths(paths: list[str]) -> list[str]:
    seen, out = set(), []
    for p in paths:
        key = os.path.normcase(os.path.abspath(p))
        if key not in seen:
            seen.add(key)
            out.append(os.path.abspath(p))
    return out


def _locate(args) -> str:
    if args.project_root:
        root = os.path.abspath(args.project_root)
        if not os.path.lexists(state_path(root)):
            raise LoopError(f"there is no loop state at {state_path(root)}")
        return root
    start = os.path.abspath(args.cwd or os.getcwd())
    root = find_root(start)
    if root is None:
        raise LoopError(f"there is no loop state at or above {start}")
    return root


def _check_claim(state: dict | None, session: str, takeover: str | None, root: str) -> str | None:
    """The previous owner when this is an authorized takeover, else None.
    Raises when another session holds the claim and no matching takeover
    was given."""
    if state is None:
        if takeover:
            raise LoopError(f"--takeover {takeover}: there is no existing claim to take over")
        return None
    owner = state["owner_session"]
    if owner == session:
        return None
    if takeover != owner:
        hint = (
            f"--takeover {takeover} names a different session"
            if takeover
            else f"pass --takeover {owner} only when the owner has authorized moving the loop"
        )
        raise LoopError(
            f"the loop at {root} is claimed by session {owner}"
            f" (status {state['status']}: {state['status_reason']}); {hint}"
        )
    return owner


def root_problems(root: str, law: str, in_codex: bool) -> list[str]:
    """Signs that `root` is not the directory the owning session's hooks
    will search from, so the loop would be armed where it never fires."""
    problems = []
    law_dir = os.path.dirname(law)
    if os.path.basename(law_dir) in (".claude", ".codex"):
        law_dir = os.path.dirname(law_dir)
    if inside(root, law_dir) and not same_path(root, law_dir):
        problems.append(f"the project root {root} is below {law_dir}, the project the law belongs to")
    if not os.path.exists(os.path.join(root, ".git")):
        current = os.path.dirname(os.path.abspath(root))
        while True:
            if os.path.exists(os.path.join(current, ".git")):
                problems.append(f"the project root {root} is inside the repository at {current}")
                break
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent
    if in_codex and not inside(os.getcwd(), root):
        problems.append(
            f"this Codex session works in {os.getcwd()}, outside {root}; its hooks search"
            " upward from the session's directory and would never find the state"
        )
    return problems


def _sentinel_clearing(present: list[str], law: str, args) -> None:
    """Refuse to clear pause sentinels unless the owner explicitly asked."""
    if not present:
        return
    if not args.clear_sentinels:
        raise LoopError(
            f"pause sentinel(s) {', '.join(present)} exist; they stay unless the owner"
            " explicitly asks to clear them with --clear-sentinels"
        )
    front, _ = read_law(law)
    claude = (front or {}).get("session")
    if claude and not args.clear_claude_pause:
        raise LoopError(
            f"the law's front matter names Claude session {claude!r}, which honours the same"
            " sentinels; clearing them would also resume that Claude loop. Pass"
            " --clear-claude-pause only when the owner explicitly asks for that too"
        )


def _clear_sentinels(root: str, paths: list[str]) -> tuple[list[str], list[str]]:
    """Move present sentinels aside into the state directory (never delete)."""
    moved, failed = [], []
    archive = os.path.join(state_dir(root), "cleared-sentinels")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    for i, p in enumerate(paths):
        if not os.path.lexists(p):
            continue
        try:
            if os.path.isdir(p) and not os.path.islink(p):
                raise OSError("it is a directory; left in place")
            os.makedirs(archive, exist_ok=True)
            target = os.path.join(archive, f"{stamp}-{i}-{os.path.basename(p)}")
            os.replace(p, target)
            moved.append(f"{p} -> {target}")
        except OSError as exc:
            failed.append(f"{p}: {exc}")
    return moved, failed


def _report_sentinels(moved: list[str], failed: list[str]) -> int:
    print(f"Moved pause sentinels aside: {'; '.join(moved)}." if moved else "No pause sentinel was cleared.")
    for f in failed:
        warn(f"could not clear pause sentinel {f}; the loop stays paused until it is gone")
    return 1 if failed else 0


def cmd_start(args) -> int:
    # Everything is validated before the lock is taken or anything is written.
    root = os.path.abspath(args.project_root)
    if not os.path.isdir(root):
        raise LoopError(f"the project root {root} is not a directory")
    session = claimant(args)
    verified, _ = native_identity()
    law = os.path.abspath(os.path.join(root, args.law))
    if not os.path.isfile(law):
        raise LoopError(f"the law {law} is not a file")
    if inside(law, state_dir(root)):
        raise LoopError(f"the law {law} is inside the loop's own state directory")
    front, _ = read_law(law)
    if args.max_iterations_from_law:
        cap, cap_source = cap_from_law(front, law), "law front matter"
    else:
        cap, cap_source = args.max_iterations, "--max-iterations"
        if cap < 1:
            raise LoopError(f"--max-iterations {cap}: the cap must be a positive integer")
    if args.promise_from_law:
        promise, promise_source = promise_from_law(front, law), "law front matter"
    else:
        promise, promise_source = args.promise, "--promise"
        problem = promise_problem(promise)
        if problem:
            raise LoopError(f"--promise: {problem}")
    sentinels = _unique_paths(
        [os.path.join(root, ".stop")] + [os.path.join(root, s) for s in args.sentinel]
    )
    for sentinel in sentinels:
        problem = sentinel_problem(sentinel, root, law)
        if problem:
            raise LoopError(f"--sentinel: {problem}")
    problems = root_problems(root, law, in_codex=verified is not None)
    if problems and not args.confirm_root:
        raise LoopError("; ".join(problems) + "; pass --confirm-root if this really is the root the hooks will find")
    try:
        os.makedirs(state_dir(root), exist_ok=True)
    except OSError as exc:
        raise LoopError(f"cannot create {state_dir(root)}: {exc}") from exc
    with locked(root, args.lock_timeout):
        with contextlib.suppress(FileExistsError):
            with open(os.path.join(state_dir(root), ".gitignore"), "x", encoding="utf-8") as f:
                f.write("*\n")
        previous = None
        if os.path.lexists(state_path(root)):
            try:
                previous = read_state(root)
            except LoopError as exc:
                raise LoopError(
                    f"the existing state is unreadable, so its owner is unknown: {exc};"
                    " inspect it, or discard it with reset --discard-malformed"
                ) from exc
        takeover_from = _check_claim(previous, session, args.takeover, root)
        if previous and previous["status"] in ("active", "paused") and previous["iteration"] > 0 and not args.restart:
            raise LoopError(
                f"the run of session {previous['owner_session']} is {previous['status']} at iteration"
                f" {previous['iteration']} of {previous['max_iterations']}; starting again would reset"
                " that counter. resume continues it; pass --restart only on the owner's explicit request"
            )
        present = [s for s in sentinels if os.path.lexists(s)]
        _sentinel_clearing(present, law, args)
        stamp = now()
        reason = "started by explicit start"
        if takeover_from:
            reason += f", taking over from session {takeover_from}"
        restarted_from = None
        if previous:
            restarted_from = {
                "owner_session": previous["owner_session"],
                "status": previous["status"],
                "iteration": previous["iteration"],
                "last_turn_id": (previous.get("last_continued") or {}).get("turn_id"),
            }
        state = {
            "schema": SCHEMA,
            "project_root": root,
            "law_path": law,
            "owner_session": session,
            "status": "active",
            "status_reason": reason,
            "iteration": 0,
            "max_iterations": cap,
            "max_iterations_source": cap_source,
            "completion_promise": promise,
            "completion_promise_source": promise_source,
            "pause_sentinels": sentinels,
            "last_stop_fingerprint": None,
            "takeover_from": takeover_from,
            "restarted_from": restarted_from,
            "started_at": stamp,
            "updated_at": stamp,
        }
        write_state(root, state)
        record_event(root, {"ts": stamp, "event": "cli.start", "session_id": session,
                            "outcome": "started", "takeover_from": takeover_from,
                            "restarted_from": restarted_from, "iteration": 0, "max_iterations": cap})
        moved, failed = _clear_sentinels(root, present)
    print(f"Loop started at {root} for session {session}.")
    print(f"Law: {law}")
    print(f"Cap: {cap} continuations ({cap_source}). Promise: <promise>{promise}</promise> ({promise_source}).")
    if previous:
        print(f"Replaced the previous run of session {previous['owner_session']} ({previous['status']}, iteration {previous['iteration']}).")
    print(f"Pause sentinels: {', '.join(sentinels)}")
    return _report_sentinels(moved, failed)


def cmd_resume(args) -> int:
    root = _locate(args)
    session = claimant(args)
    with locked(root, args.lock_timeout):
        state = read_state(root)
        takeover_from = _check_claim(state, session, args.takeover, root)
        if state["status"] not in ("active", "paused"):
            raise LoopError(
                f"the loop is {state['status']} ({state['status_reason']}); a finished"
                " run is not resumed, start a new one"
            )
        present = present_sentinels(state)
        _sentinel_clearing(present, state["law_path"], args)
        reason = "resumed by explicit resume"
        if takeover_from:
            reason += f", taking over from session {takeover_from}"
            state["takeover_from"] = takeover_from
        state.update(owner_session=session, status="active", status_reason=reason, updated_at=now())
        write_state(root, state)
        record_event(root, {"ts": now(), "event": "cli.resume", "session_id": session,
                            "outcome": "resumed", "takeover_from": takeover_from,
                            "iteration": state["iteration"], "max_iterations": state["max_iterations"]})
        moved, failed = _clear_sentinels(root, present)
    print(f"Loop resumed at {root} for session {session}, iteration {state['iteration']} of {state['max_iterations']}.")
    return _report_sentinels(moved, failed)


def _actor(args) -> str:
    if args.session:
        return _session_arg(args.session)
    verified, _ = native_identity()
    return verified or "operator"


def cmd_pause(args) -> int:
    """Establish the pause through the sentinel first -- the control the hooks
    honour on their own -- then record it in the state. Exit 0 when both took
    effect, 3 when the pause is in effect but one of them failed, 1 when
    neither did."""
    root = _locate(args)
    actor = _actor(args)
    reason = (args.reason or "").strip()
    if not reason:
        raise LoopError("--reason must say why")
    sentinel = os.path.join(root, ".stop")
    sentinel_error = None
    try:
        with open(sentinel, "x", encoding="utf-8") as f:
            f.write(f"Sijav loop paused at {now()} by {actor}: {reason}\n")
        print(f"Created the pause sentinel {sentinel}.")
    except FileExistsError:
        print(f"The pause sentinel {sentinel} already exists.")
    except OSError as exc:
        sentinel_error = f"the pause sentinel {sentinel} was not created: {exc}"
    state_error, state_paused = None, False
    try:
        with locked(root, args.lock_timeout):
            state = read_state(root)
            if state["status"] in ("active", "paused"):
                state.update(status="paused", status_reason=f"pause by {actor}: {reason}", updated_at=now())
                write_state(root, state)
                state_paused = True
                record_event(root, {"ts": now(), "event": "cli.pause", "session_id": actor,
                                    "outcome": "paused", "detail": reason, "iteration": state["iteration"],
                                    "sentinel_error": sentinel_error})
                print(f"Recorded the pause in {state_path(root)}.")
            else:
                print(f"The state is {state['status']} ({state['status_reason']}); left unchanged.")
    except LoopError as exc:
        state_error = f"the state was not updated: {exc}"
    if sentinel_error:
        warn(sentinel_error)
    if state_error:
        warn(state_error)
    if not sentinel_error and not state_error:
        return 0
    if not sentinel_error:
        print(f"PAUSE IN EFFECT through {sentinel}; the hooks honour it whatever the state says.")
        return 3
    if state_paused:
        print("PAUSE IN EFFECT through the paused state only; the sentinel is missing.")
        return 3
    print("PAUSE NOT IN EFFECT: neither the sentinel nor the state could be set.")
    return 1


def cmd_stop(args) -> int:
    root = _locate(args)
    actor = _actor(args)
    reason = (args.reason or "").strip()
    if not reason:
        raise LoopError("--reason must say why")
    with locked(root, args.lock_timeout):
        state = read_state(root)
        if state["status"] not in ("active", "paused"):
            raise LoopError(f"the loop is {state['status']} ({state['status_reason']}); nothing to stop")
        state.update(status="stopped", status_reason=f"stop by {actor}: {reason}", updated_at=now())
        write_state(root, state)
        record_event(root, {"ts": now(), "event": "cli.stop", "session_id": actor,
                            "outcome": "stopped", "detail": reason, "iteration": state["iteration"]})
    print(f"Loop stopped at {root}: {state['status_reason']}. A new run needs start.")
    return 0


def cmd_reset(args) -> int:
    root = _locate(args)
    with locked(root, args.lock_timeout):
        if args.discard_malformed:
            try:
                read_state(root)
            except LoopError as exc:
                cause = str(exc)
            else:
                raise LoopError("the state is readable; reset it as its owner (--session) instead")
            actor, previous = "operator", None
        else:
            if not args.session and not args.session_from_env:
                raise LoopError("reset needs --session or --session-from-env (the owner), or --discard-malformed")
            actor = claimant(args)
            state = read_state(root)
            _check_claim(state, actor, args.takeover, root)
            cause, previous = "reset by its owner", state["owner_session"]
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        archive = os.path.join(state_dir(root), f"state.reset-{stamp}.json")
        try:
            os.replace(state_path(root), archive)
        except OSError as exc:
            raise LoopError(f"could not move {state_path(root)} aside: {exc}") from exc
        record_event(root, {"ts": now(), "event": "cli.reset", "session_id": actor,
                            "outcome": "reset", "previous_owner": previous, "detail": cause})
    print(f"Loop state at {root} reset; the old state is kept at {archive}. No session owns the loop.")
    return 0


def cmd_status(args) -> int:
    try:
        root = _locate(args)
    except LoopError as exc:
        print(f"Not armed: {exc}.")
        return 0
    with locked(root, args.lock_timeout):
        state = read_state(root)
    info = {
        "project_root": root,
        "law_path": state["law_path"],
        "owner_session": state["owner_session"],
        "status": state["status"],
        "status_reason": state["status_reason"],
        "iteration": state["iteration"],
        "max_iterations": state["max_iterations"],
        "completion_promise": state["completion_promise"],
        "pause_sentinels": state["pause_sentinels"],
        "present_sentinels": present_sentinels(state),
    }
    if args.session:
        info["this_session_owns"] = _session_arg(args.session) == state["owner_session"]
    if args.json:
        print(json.dumps(info, indent=2, ensure_ascii=True))
        return 0
    paused = info["present_sentinels"]
    print(f"project: {root}")
    print(f"law: {info['law_path']}")
    print(f"owner session: {info['owner_session']}" + (
        "" if "this_session_owns" not in info else
        (" (this session)" if info["this_session_owns"] else " (not this session)")))
    print(f"status: {info['status']} ({info['status_reason']})")
    print(f"iteration: {info['iteration']} of {info['max_iterations']}")
    print(f"completion promise: <promise>{info['completion_promise']}</promise>")
    print("pause sentinel present: " + (", ".join(paused) if paused else "no"))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sijav_loop.py", description="Sijav-Codex loop state.")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name, help_text, locate=True):
        p = sub.add_parser(name, help=help_text)
        if locate:
            p.add_argument("--project-root", help="the project whose state to use")
            p.add_argument("--cwd", help="find the state by walking up from here (default: this directory)")
        p.add_argument("--lock-timeout", type=float, default=LOCK_TIMEOUT, help="seconds to wait for the state lock")
        return p

    def identity(p, required=True):
        group = p.add_mutually_exclusive_group(required=required)
        group.add_argument("--session", help="the real Codex session id of the owner")
        group.add_argument("--session-from-env", action="store_true",
                           help="use CODEX_SESSION_ID, only when it equals CODEX_THREAD_ID (the root session)")

    def clearing(p):
        p.add_argument("--clear-sentinels", action="store_true",
                       help="move present pause sentinels aside (only on the owner's explicit request)")
        p.add_argument("--clear-claude-pause", action="store_true",
                       help="also when the law's front matter names a Claude session sharing them")

    p = add("start", "claim the loop for one Codex session and arm it", locate=False)
    p.add_argument("--project-root", required=True, help="the project root (relative paths resolve from here)")
    p.add_argument("--law", required=True, help="the law file; relative to the project root")
    identity(p)
    cap = p.add_mutually_exclusive_group(required=True)
    cap.add_argument("--max-iterations", type=int, help="the most continuations the Stop hook may send")
    cap.add_argument("--max-iterations-from-law", action="store_true", help="use the law front matter's max_iterations")
    promise = p.add_mutually_exclusive_group(required=True)
    promise.add_argument("--promise", help="the exact completion promise, without the <promise> tags")
    promise.add_argument("--promise-from-law", action="store_true", help="use the law front matter's completion_promise")
    p.add_argument("--sentinel", action="append", default=[], help="an extra pause sentinel inside the project")
    p.add_argument("--takeover", metavar="PREVIOUS_SESSION", help="the owner-authorized takeover of this session's claim")
    p.add_argument("--restart", action="store_true", help="replace a live run's counter (owner's explicit request)")
    p.add_argument("--confirm-root", action="store_true", help="arm although the root looks like a subdirectory")
    clearing(p)
    p.set_defaults(func=cmd_start)

    p = add("resume", "re-arm a paused loop for its owner")
    identity(p)
    p.add_argument("--takeover", metavar="PREVIOUS_SESSION")
    clearing(p)
    p.set_defaults(func=cmd_resume)

    for name, func, text in (("pause", cmd_pause, "owner-requested pause"), ("stop", cmd_stop, "owner-requested stop")):
        p = add(name, text)
        p.add_argument("--reason", required=True)
        p.add_argument("--session", help="who asked, recorded with the reason")
        p.set_defaults(func=func)

    p = add("reset", "remove the claim (the old state is kept beside it)")
    identity(p, required=False)
    p.add_argument("--takeover", metavar="PREVIOUS_SESSION")
    p.add_argument("--discard-malformed", action="store_true", help="move aside a state that cannot be read")
    p.set_defaults(func=cmd_reset)

    p = add("status", "show the loop's owner, status and counters")
    p.add_argument("--session")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except LoopError as exc:
        print(f"{args.command.upper()} FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
