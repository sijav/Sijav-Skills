#!/usr/bin/env python3
"""Run codex with ONE SESSION PER PURPOSE, kept in the project's .claude folder.

    python <this folder>/codex_session.py --purpose plan-1362 prompt.md [reply.md]
        [--search] [--model gpt-6.1-sol] [--effort medium] [--fresh] [--project DIR]

The owner, 2026-09-23: every codex call runs in a session chosen by the nature
of the work (plan, plan roast, task roast, jev question, research, ...), and the
session lives under the project's .claude folder. A call with a purpose that
already has a session RESUMES it, so codex keeps the context of that line of
work instead of starting blind every time.

Where things go (under <project>/.claude/codex-sessions/):
  <purpose>.json          the live session: thread id, model, times, call count
  <purpose>.<thread>.json a session retired because its thread was full
  logs/<purpose>-<UTC>.log the FULL stdout and stderr of every call, plus the
                           command, working directory, times and exit status

The project is --project, else $CLAUDE_PROJECT_DIR, else the TOPMOST folder
above the current directory that holds a .claude folder (the home folder's
.claude is the user's, not a project's). Sub-repositories with their own
.claude therefore share the project's sessions.

Models: the GPT-6 models share one allowance. The runner uses the requested
model (default gpt-6.1-sol at medium effort; owner, 2026-09-30) and no other: when codex says the allowance is
used up, the call fails with codex's own words, and the caller asks the owner
through the input tool to change the codex account (owner, 2026-09-26:
"instead of gpt_reserved just ask for user input via input tool to change the
account when finished"). A session continues directly on the new account. The one
exception is a plain web search: the search skill retries once on gpt-reserve, a luna that
stays free when the allowance is used up (owner, 2026-09-30). A used-up allowance raises
CodexExhausted, so a caller can tell it from any other failure.

Every failure raises CodexError with the exact cause and the log path.

Switch (owner, 2026-09-28: "there should be an option to disable/fallback"):
SIJAV_CODEX=off turns codex off for every caller. run() then raises CodexOff at
once, without starting codex, and the caller does the work another way (the
skills say how). Set it per user or per project in Claude Code's settings "env".
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

BINARIES = [
    r"C:\Program Files\nodejs\codex.cmd",
    str(Path.home() / ".codex" / ".sandbox-bin" / "codex.exe"),
]


def with_path_codex(binaries: list[str], found: str | None) -> list[str]:
    """The known places, then whatever codex the PATH finds, unless it is one of them already.
    Windows paths compare without case: the PATH reports C:\\Program Files\\nodejs\\codex.CMD."""
    known = {os.path.normcase(b) for b in binaries}
    return binaries + ([found] if found and os.path.normcase(found) not in known else [])


# On another PC codex may live elsewhere: whatever the PATH finds is tried after the known places.
BINARIES = with_path_codex(BINARIES, shutil.which("codex"))
DEFAULT_MODEL = "gpt-6.1-sol"  # owner, 2026-09-30: 6.1 sol replaces 6 sol
EXHAUSTED = ("usage limit", "quota", "rate limit", "allowance", "exceeded your")
OUT_OF_ROOM = "ran out of room"
# Another run is writing to the purpose's thread (seen 2026-09-28: a loop session's plan roast and
# a manual roast in the same project). Another binary cannot help: the thread itself is taken.
BUSY = "already has an active writer"
PURPOSE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")


class CodexError(RuntimeError):
    """A failure whose message is the exact cause, with the log to read."""


class CodexOff(CodexError):
    """codex is switched off (SIJAV_CODEX=off); nothing was started."""


class CodexBusy(CodexError):
    """Another run is writing to this purpose's thread; nothing was changed."""


class CodexExhausted(CodexError):
    """The account's shared GPT-6 allowance is used up: ask the owner to switch the codex account.
    Only a plain web search may retry on gpt-reserve instead (owner, 2026-09-30)."""


def codex_off() -> bool:
    return os.environ.get("SIJAV_CODEX", "on").strip().lower() in {"off", "0", "false", "no"}


def project_root(explicit: str | None = None, start: Path | None = None) -> Path:
    if explicit:
        root = Path(explicit).resolve()
        if not (root / ".claude").is_dir():
            raise CodexError(f"--project {root} has no .claude folder")
        return root
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env and (Path(env) / ".claude").is_dir():
        return Path(env).resolve()
    home = Path.home().resolve()
    here = (start or Path.cwd()).resolve()
    found = None
    for p in (here, *here.parents):
        if p != home and (p / ".claude").is_dir():
            found = p  # keep walking: the TOPMOST one is the project
    if found is None:
        raise CodexError(
            f"no project found: no folder above {here} holds a .claude folder; "
            "pass --project or set CLAUDE_PROJECT_DIR"
        )
    return found


def sessions_dir(root: Path) -> Path:
    d = root / ".claude" / "codex-sessions"
    (d / "logs").mkdir(parents=True, exist_ok=True)
    return d


def read_session(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise CodexError(f"session file {path} is not valid JSON ({e}); fix or delete it") from e
    return data if data.get("thread_id") else None


def thread_id_of(jsonl: str) -> str | None:
    for line in jsonl.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        for key in ("thread_id", "session_id", "conversation_id"):
            if ev.get(key):
                return str(ev[key])
            item = ev.get("item")
            if isinstance(item, dict) and item.get(key):
                return str(item[key])
    return None


def command(binary: str, model: str, effort: str, search: bool,
            thread: str | None, last_msg: Path) -> list[str]:
    common = [
        "-m", model,
        "-c", f"model_reasoning_effort={effort}",
        "-c", 'sandbox_mode="read-only"',
        "--skip-git-repo-check",
        "--json",
        "-o", str(last_msg),
    ]
    head = [binary] + (["--search"] if search else []) + ["exec"]
    if thread:
        return head + ["resume", *common, thread, "-"]
    return head + [*common, "-"]


def _stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _call(argv: list[str], prompt: str, root: Path, log: Path, timeout: int):
    """Run codex once. Its output goes straight into the log while it runs (dev rule D1:
    captured from the moment it starts), so a hang or a kill still leaves all it said;
    it is read back from the log afterwards."""
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    with log.open("a", encoding="utf-8") as fh:
        fh.write(f"=== {started}\ncommand: {subprocess.list2cmdline(argv)}\ncwd: {root}\n"
                 "--- stdout (and stderr, as they arrive)\n")
    start = log.stat().st_size
    with log.open("a", encoding="utf-8", errors="replace") as fh:
        try:
            r = subprocess.run(argv, input=prompt, stdout=fh, stderr=subprocess.STDOUT,
                               text=True, encoding="utf-8", errors="replace",
                               timeout=timeout, cwd=root)
            code, out, err = r.returncode, r.stdout, r.stderr
        except subprocess.TimeoutExpired:
            code, out, err = "timeout", None, None
        except OSError as e:
            code, out, err = "oserror", None, f"{type(e).__name__}: {e}"
    if out is None:  # a real run wrote everything it said into the log
        with log.open("rb") as fh:
            fh.seek(start)
            out = fh.read().decode("utf-8", "replace")
    else:  # a stand-in that handed its output back instead of writing it
        with log.open("a", encoding="utf-8") as fh:
            fh.write(f"{out}\n--- stderr\n{err or ''}\n")
    err = err or ""
    with log.open("a", encoding="utf-8") as fh:
        if code == "oserror":
            fh.write(f"{err}\n")
        fh.write(f"--- exit: {code}  ended: {dt.datetime.now(dt.timezone.utc).isoformat()}\n")
    return code, out, err


@contextlib.contextmanager
def purpose_lock(d: Path, purpose: str, timeout: int):
    """One run at a time per purpose. Without it, two new runs of one purpose could each
    start a thread and each save it, and the last save would lose the other's thread (the
    roast of 2026-09-29). A second run gets CodexBusy at once, and its caller can use another
    purpose; a lock older than any run could last is left by a run that died, and is cleared."""
    lock = d / f"{purpose}.lock"
    for _ in range(2):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                age = time.time() - lock.stat().st_mtime
            except FileNotFoundError:
                continue  # the other run just finished
            if age > timeout + 300:
                lock.unlink(missing_ok=True)
                continue
            raise CodexBusy(
                f"the {purpose!r} codex session is in use by another run (for {age:.0f} s); "
                "nothing was sent. Wait for that run, or use another purpose."
            ) from None
    else:
        raise CodexBusy(f"the {purpose!r} codex session could not be locked; nothing was sent.")
    try:
        os.write(fd, f"pid {os.getpid()} since {dt.datetime.now(dt.timezone.utc).isoformat()}\n".encode())
        os.close(fd)
        yield
    finally:
        lock.unlink(missing_ok=True)


def run(prompt: str, purpose: str, *, search: bool = False, model: str | None = None,
        effort: str = "medium", fresh: bool = False, project: str | None = None,
        timeout: int = 900) -> str:
    """Send `prompt` in the `purpose` session; return codex's last message."""
    if codex_off():
        raise CodexOff("codex is switched off (SIJAV_CODEX=off); nothing was sent. Do this work without codex.")
    if not PURPOSE_RE.match(purpose or ""):
        raise CodexError(
            f"purpose {purpose!r} is not a session name: use lowercase letters, digits, "
            "'.', '_' or '-' (for example plan-1362, roast-task, jev-questions, research)"
        )
    root = project_root(project)
    d = sessions_dir(root)
    with purpose_lock(d, purpose, timeout):
        return _run_locked(prompt, purpose, d, root, search=search, model=model,
                           effort=effort, fresh=fresh, timeout=timeout)


def _run_locked(prompt: str, purpose: str, d: Path, root: Path, *, search: bool,
                model: str | None, effort: str, fresh: bool, timeout: int) -> str:
    live = d / f"{purpose}.json"
    log = d / "logs" / f"{purpose}-{_stamp()}.log"
    session = None if fresh else read_session(live)
    # A session keeps its thread, not its model: each call uses the model it is given, else the
    # default (owner, 2026-09-28; checked live: an astra thread resumed on sol).
    models = [model or DEFAULT_MODEL]
    binaries = [b for b in BINARIES if Path(b).exists()]
    if not binaries:
        raise CodexError("no codex binary at " + ", ".join(BINARIES)
                         + ". Install codex, or set SIJAV_CODEX=off so the work is done without it.")
    tried: list[str] = []
    for m in models:
        for binary in binaries:
            for attempt in ("resume", "fresh-after-full"):
                thread = session["thread_id"] if (session and attempt == "resume") else None
                last_msg = d / "logs" / f"{purpose}-{_stamp()}.reply.md"
                argv = command(binary, m, effort, search, thread, last_msg)
                code, out, err = _call(argv, prompt, root, log, timeout)
                reply = last_msg.read_text(encoding="utf-8").strip() if last_msg.is_file() else ""
                said = (out + "\n" + err).lower()
                if code == 0 and reply:
                    new_thread = thread or thread_id_of(out)
                    now = dt.datetime.now(dt.timezone.utc).isoformat()
                    record = {
                        "purpose": purpose,
                        "thread_id": new_thread,
                        "model": m,
                        "created": (session or {}).get("created", now) if thread else now,
                        "updated": now,
                        "calls": ((session or {}).get("calls", 0) + 1) if thread else 1,
                        "last_log": str(log),
                    }
                    if new_thread:
                        live.write_text(json.dumps(record, indent=1), encoding="utf-8")
                    else:
                        with log.open("a", encoding="utf-8") as fh:
                            fh.write("WARNING: no thread id in the JSON events; session not saved\n")
                    return reply
                tail = (err or out)[-800:].strip()
                tried.append(f"{m} via {Path(binary).name} ({'resume ' + thread if thread else 'new'}): exit {code}: {tail}")
                if thread and OUT_OF_ROOM in said:
                    retired = d / f"{purpose}.{thread}.json"
                    live.replace(retired)
                    session = None
                    continue  # once: a new thread in the same purpose
                if BUSY in said:
                    raise CodexBusy(
                        f"the {purpose!r} codex session is busy: another run is writing to its thread "
                        f"{thread}. Nothing was changed; wait for that run, or use another purpose. Full log: {log}"
                    )
                if any(w in said for w in EXHAUSTED):
                    # the account's allowance, not this binary: no point trying another
                    raise CodexExhausted(
                        f"codex's allowance looks used up for purpose {purpose!r} ({m}): {tail}\n"
                        "Ask the owner through the input tool to change the codex account, then run "
                        f"again; the session continues on the new account. Full log: {log}"
                    )
                break  # any other failure: next binary
    raise CodexError(
        f"codex gave no reply for purpose {purpose!r}. Full log: {log}\nTried:\n  "
        + "\n  ".join(tried)
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("prompt_file")
    ap.add_argument("reply_file", nargs="?")
    ap.add_argument("--purpose", required=True,
                    help="the session: one per kind of work, e.g. plan-1362, roast-task, research")
    ap.add_argument("--search", action="store_true", help="let codex search the web")
    ap.add_argument("--model", default=None)
    ap.add_argument("--effort", default="medium")
    ap.add_argument("--fresh", action="store_true", help="start a new session for this purpose")
    ap.add_argument("--project", default=None)
    ap.add_argument("--timeout", type=int, default=900)
    a = ap.parse_args(argv)
    try:
        prompt = Path(a.prompt_file).read_text(encoding="utf-8")
        reply = run(prompt, a.purpose, search=a.search, model=a.model, effort=a.effort,
                    fresh=a.fresh, project=a.project, timeout=a.timeout)
    except CodexOff as e:
        print(f"CODEX OFF: {e}", file=sys.stderr)
        return 3
    except (CodexError, OSError) as e:
        print(f"CODEX FAILED: {e}", file=sys.stderr)
        return 1
    if a.reply_file:
        Path(a.reply_file).write_text(reply, encoding="utf-8")
    print(reply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
