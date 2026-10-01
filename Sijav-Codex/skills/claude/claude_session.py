#!/usr/bin/env python3
"""Sijav-Codex: one persistent external Claude conversation per purpose, for code and technical roast.

    call     --purpose NAME --mode code|technical [--scope TEXT] [--effort high]
             [--prompt-file FILE]            (else the prompt is read from stdin, bytes unchanged)
             [--rules-file FILE ... | --no-project-rules]
             [--allow-command "Bash(<command> *)" ...]           (code mode only)
             [--resume-unconfirmed new|existing] [--timeout SECONDS] [--project DIR]
    list     [--project DIR] [--json]
    status   --purpose NAME [--project DIR] [--json]
    context  --purpose NAME [--project DIR] [--full]
    reset    --purpose NAME --authorized-by "<the owner's words>" [--project DIR]
    recover  --purpose NAME --confirm-stopped [--pid-is-other-program] [--project DIR]

Only `call` launches the Claude CLI, always on the exact model claude-opus-5-5 at the requested
effort (low, medium, high, xhigh or max; default high). No option selects another model and no
fallback model is passed. The CLI runs with -p and these flags from its 2.1.286 help:
  --safe-mode            customizations (CLAUDE.md, skills, hooks, MCP, custom agents...) off; sign-in,
                         model selection and permissions work normally. Its init still LISTS installed
                         plugins and skills (observed); their names are recorded in each result.
  --restricted           user, project and local settings files are ignored (so a reviewed project's
                         .claude/settings.json cannot widen approvals or redirect the provider), file
                         tools stay inside the working directory, bypass is refused, and writes to
                         settings, git and tool-configuration files need a person; the command tools
                         and WebFetch stay available only because --tools names them.
  --disable-slash-commands  no skill can be triggered from the prompt.
  --strict-mcp-config, --output-format stream-json --verbose, the prompt on stdin,
  --permission-mode manual (the normal mode; init reports "default", seen in the 2.1.286 smoke) and
  --permission-prompts none, so anything that would ask is denied. Never a bypass or bare mode.
The child environment drops ANTHROPIC_* and CLAUDE_CODE_USE_* provider settings, NODE_OPTIONS and
NODE_TLS_REJECT_UNAUTHORIZED, so the CLI keeps its own OAuth sign-in on verified TLS rather than an
inherited API key, base URL, provider, injected runtime code or disabled certificate checks.
CLAUDE_CODE_OAUTH_TOKEN, CLAUDE_CONFIG_DIR, proxy and CA-bundle variables are kept so ordinary
sign-in and networks work; the names (never values) of everything dropped or kept are recorded.

Permissions follow Claude Code's rule syntax (https://code.claude.com/docs/en/permissions): reads
inside the project (the working directory) need no approval and reads elsewhere would ask, so they
are denied; deny rules beat allow rules.
  code       tools Read, Glob, Grep, Write, Edit (+ Bash/PowerShell only when a scoped command rule
             names them). Pre-approved: Edit(./**) (all file writes inside the project) and each
             --allow-command rule, e.g. "Bash(python -m unittest *)". Denied: edits under .git,
             .claude, .codex, .githooks and .husky; NotebookEdit; unscoped command tools.
  technical  tools Read, Glob, Grep and WebFetch only. Pre-approved: WebFetch. Denied: Write, Edit,
             NotebookEdit, Bash, PowerShell.
  Both deny reads of common secrets (.env, .env.*, *.pem, *.key, ~/.ssh, ~/.aws, ~/.claude, ~/.codex
  and similar). Rules bind Claude's tools and the file commands Claude Code recognizes; a program a
  granted command runs (a test suite) is not confined by them.

The CLI's first event must be its init and must report: model claude-opus-5-5, exactly no tool
beyond those offered, no MCP server, the normal permission mode, apiKeySource "none" (its own
sign-in), the expected session id and the project as cwd. Anything else stops the CLI at that
event, before it acts. A reply from another model is kept in the record but not used. Tool uses the
CLI reports as denied (permission_denials) are printed as a note and recorded with the result, so a
review that could not read what it needed does not pass silently.

The first call of a conversation must carry the project's law/rules (--rules-file, each sent with
its path before the prompt) or say --no-project-rules, because safe mode loads no CLAUDE.md; so must
every call until a call has actually delivered a turn. A new purpose also needs --scope.

State: <project>/.codex/claude-sessions/<purpose>/state.json; runs/<run>/ holds, created once per
call, brief.json, prompt.txt (as sent), command.json, raw stdout.jsonl and stderr.txt from launch,
result.json and reply.md; generations/ keeps retired conversations. The project is --project, else
the nearest folder at or above the working directory holding .claude/todo.db, else .git, else a
.claude folder, below the home folder; never this script's location.

Sessions: the first call passes a fresh --session-id. It is confirmed (status ready) only when the
CLI reports that id and delivers a turn; later calls pass --resume <that id> (never --continue).
If no turn was delivered the purpose is `unconfirmed` and refuses calls until the owner picks one
attempt: --resume-unconfirmed new (the same id again with --session-id; for a CLI that never
started) or existing (--resume of that id; for one that reported the id). A failed attempt leaves it
unconfirmed. A different reported id is a `conflict`. A helper that died mid-call leaves `running`;
`recover --confirm-stopped` reads what that run kept. One call per purpose at a time (OS lock).
On Windows the CLI and everything it starts run in a Job Object that is closed when the call ends or
the helper dies; on POSIX they share a process group, which a descendant that calls setsid escapes.

No retry, no other model: every failure ends the call with its cause and record. SIJAV_CLAUDE=off
sends nothing (exit 3). SIJAV_CLAUDE_BIN names the CLI explicitly; else PATH is searched, skipping
the current and project folders.

Exit codes: 0 reply printed; 1 the call failed (record kept); 2 usage; 3 switched off; 4 busy;
5 the purpose needs resolution; 6 the reply was printed but its record or the purpose's state
could not be saved (the message says which).
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

if os.name == "nt":
    import ctypes
    import msvcrt
    from ctypes import wintypes
else:
    import fcntl

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

SCHEMA = "sijav-codex-claude-session/1"
MODEL = "claude-opus-5-5"
PINNED_CLI = "2.1.286"  # the version whose help and init the tests pin
EFFORTS = ("low", "medium", "high", "xhigh", "max")
DEFAULT_EFFORT = "high"
MODES = ("code", "technical")
STATUSES = ("new", "ready", "running", "unconfirmed", "conflict")
NORMAL_MODES = ("default", "manual")  # init reports "default"; "manual" is its documented alias
TECHNICAL_TOOLS = ("Read", "Glob", "Grep", "WebFetch")
CODE_TOOLS = ("Read", "Glob", "Grep", "Write", "Edit")
COMMAND_TOOLS = ("Bash", "PowerShell")
WRITE_OR_RUN = ("Write", "Edit", "NotebookEdit", "Bash", "PowerShell")
PROJECT_EDIT = "Edit(./**)"
PROTECTED_EDITS = tuple(f"Edit({p})" for p in (".git/**", ".claude/**", ".codex/**", ".githooks/**", ".husky/**"))
SECRET_READS = tuple(f"Read({p})" for p in (
    ".env", ".env.*", "*.pem", "*.key", "~/.ssh/**", "~/.aws/**", "~/.azure/**", "~/.gnupg/**", "~/.kube/**",
    "~/.docker/config.json", "~/.claude/**", "~/.codex/**", "~/.config/gh/**", "~/.git-credentials",
    "~/.netrc", "~/.npmrc", "~/.pypirc"))
PURPOSE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
ALLOW_RE = re.compile(r"^(Bash|PowerShell)\(([^()\r\n]+)\)$")
BATCH_UNSAFE = set('"%!^&|<>\r\n')
OFF_VALUES = {"off", "0", "false", "no"}
# Provider settings a parent process may carry that would move billing or routing away from the
# CLI's own sign-in. CLAUDE_CODE_OAUTH_TOKEN is the CLI's own OAuth token and is kept.
DROP_ENV = re.compile(r"^(ANTHROPIC_\w*|CLAUDE_CODE_USE_\w+|AWS_BEARER_TOKEN_BEDROCK|CLAUDE_CODE_SKIP_\w+_AUTH|"
                      r"NODE_OPTIONS|NODE_TLS_REJECT_UNAUTHORIZED)$", re.I)
# Kept so ordinary sign-in and networks work, and recorded by name so a proxy is never unrecorded.
NOTED_ENV = re.compile(r"^(CLAUDE_CODE_OAUTH_TOKEN|CLAUDE_CONFIG_DIR|HTTPS?_PROXY|ALL_PROXY|NO_PROXY|"
                       r"NODE_EXTRA_CA_CERTS|SSL_CERT_FILE|SSL_CERT_DIR|REQUESTS_CA_BUNDLE)$", re.I)
LOCK_WAIT = 2.0
DEFAULT_TIMEOUT = 3600
STATE_WRITE_SECONDS = 5.0
DRAIN_SECONDS = 10.0
READ_CHUNK = 65536
MAX_LINE = 32 * 1024 * 1024
RULES_PREAMBLE = (
    "Safe mode loaded no CLAUDE.md and no project settings. The orchestrator supplies the"
    " project's law and rules below, each with its source path; follow them.\n\n"
)
AUTH_RE = re.compile(r"\b401\b|\bunauthori[sz]ed\b|\bnot logged in\b|\bplease run /login\b|\binvalid api key\b|"
                     r"\b(oauth|access) token\b[^.\n]{0,40}\bexpired\b|\bauthentication (failed|error)\b", re.I)
LIMIT_RE = re.compile(r"\b429\b|\brate[- ]limit|\busage limit\b|\bquota\b|\bcredit balance\b|\boverloaded\b|"
                      r"\blimit reached\b", re.I)

OK, FAILED, USAGE, OFF, BUSY, NEEDS_RESOLUTION, NOT_SAVED = 0, 1, 2, 3, 4, 5, 6


class SessionError(Exception):
    """A failure with its exact cause and the exit code it ends the command with."""

    def __init__(self, message: str, code: int = FAILED):
        super().__init__(message)
        self.code = code


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")


def stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def switched_off() -> bool:
    return os.environ.get("SIJAV_CLAUDE", "on").strip().lower() in OFF_VALUES


def same_path(a, b) -> bool:
    try:
        return os.path.normcase(os.path.realpath(str(a))) == os.path.normcase(os.path.realpath(str(b)))
    except OSError:
        return False


# ---------------------------------------------------------------- locations


def project_root(explicit: str | None, start: Path | None = None) -> Path:
    """--project, else below the home folder the nearest board (.claude/todo.db), else the nearest
    .git, else the nearest .claude folder: a stray .claude a CLI left in a subfolder never wins
    over the repository or board above it."""
    if explicit:
        root = Path(explicit).expanduser()
        if not root.is_dir():
            raise SessionError(f"--project {root} is not an existing folder", USAGE)
        return root.resolve()
    home = Path.home().resolve()
    here = (start or Path.cwd()).resolve()
    chain = []
    for p in (here, *here.parents):
        if p == home:
            break
        chain.append(p)
    for test in (lambda p: (p / ".claude" / "todo.db").is_file(), lambda p: (p / ".git").exists(),
                 lambda p: (p / ".claude").is_dir()):
        for p in chain:
            if test(p):
                return p
    raise SessionError(
        f"no project found at or above {here}: no .claude/todo.db, .git or .claude folder below the home"
        " folder. Pass --project DIR; nothing was created.", USAGE)


def sessions_dir(root: Path) -> Path:
    return root / ".codex" / "claude-sessions"


def purpose_dir(root: Path, purpose: str) -> Path:
    return sessions_dir(root) / purpose


def check_purpose(purpose: str) -> str:
    if not PURPOSE_RE.match(purpose or ""):
        raise SessionError(
            f"purpose {purpose!r} is not a purpose name: use lowercase letters, digits, '.', '_'"
            " or '-' (for example roast-technical, plan-1362, impl-board)", USAGE)
    return purpose


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
def purpose_lock(d: Path, purpose: str, wait: float = LOCK_WAIT):
    """Hold the purpose's lock for the whole call. The OS releases it if the holder dies."""
    path = d / "lock"
    try:
        handle = open(path, "a+b")
    except OSError as exc:
        raise SessionError(f"cannot open the lock file {path}: {exc}") from exc
    try:
        deadline = time.monotonic() + wait
        while True:
            try:
                _lock(handle)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise SessionError(
                        f"the {purpose!r} Claude purpose is busy: another process holds {path}."
                        " Nothing was sent. Wait for that call (status shows its run), or give"
                        " this separate work its own purpose.", BUSY) from None
                time.sleep(0.05)
        try:
            yield
        finally:
            _unlock(handle)
    finally:
        handle.close()


def write_json_atomic(path: Path, data: dict, seconds: float = STATE_WRITE_SECONDS) -> None:
    """Write through a temporary file and one rename. Windows refuses a rename onto a file another
    program holds open (a reader of status, a scanner), so retry with backoff for `seconds`."""
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    try:
        fd, tmp = tempfile.mkstemp(prefix=".state-", suffix=".tmp", dir=str(path.parent))
    except OSError as exc:
        raise SessionError(f"could not write {path}: {exc}") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        deadline, pause = time.monotonic() + seconds, 0.05
        while True:
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(pause)
                pause = min(pause * 2, 0.5)
    except OSError as exc:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise SessionError(f"could not write {path}: {exc}") from exc


def write_new(path: Path, data) -> None:
    """Create a run file once; an existing file is never replaced."""
    raw = data if isinstance(data, bytes) else (
        json.dumps(data, indent=2, ensure_ascii=False) + "\n" if isinstance(data, dict) else data
    ).encode("utf-8")
    with open(path, "xb") as f:
        f.write(raw)


def read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# ------------------------------------------------------------------- state


def _uuid_or_none(v) -> bool:
    return v is None or (isinstance(v, str) and bool(UUID_RE.match(v)))


def state_problems(data, purpose: str | None = None, root: Path | None = None) -> list[str]:
    if not isinstance(data, dict):
        return ["it is not a JSON object"]
    out = []
    if data.get("schema") != SCHEMA:
        out.append(f"schema is {data.get('schema')!r}, not {SCHEMA!r}")
    if not isinstance(data.get("purpose"), str) or not PURPOSE_RE.match(data.get("purpose") or ""):
        out.append("purpose is missing or not a purpose name")
    elif purpose is not None and data["purpose"] != purpose:
        out.append(f"it names purpose {data['purpose']!r} but sits in the folder of {purpose!r}")
    if not isinstance(data.get("project_root"), str):
        out.append("project_root is missing")
    elif root is not None and not same_path(data["project_root"], root):
        out.append(f"it belongs to the project {data['project_root']}, not {root} (copied or moved)")
    if not isinstance(data.get("scope"), str) or not data.get("scope").strip():
        out.append("scope is missing")
    if data.get("mode") not in MODES:
        out.append(f"mode {data.get('mode')!r} is not code or technical")
    if data.get("model") != MODEL:
        out.append(f"model {data.get('model')!r} is not {MODEL}")
    if not isinstance(data.get("generation"), int) or isinstance(data.get("generation"), bool) or data["generation"] < 1:
        out.append("generation is not a positive integer")
    if data.get("status") not in STATUSES:
        out.append(f"status {data.get('status')!r} is not one of {', '.join(STATUSES)}")
    for key in ("session_id", "candidate_session_id"):
        if not _uuid_or_none(data.get(key)):
            out.append(f"{key} {data.get(key)!r} is not a session UUID")
    for key in ("calls", "failures"):
        if not isinstance(data.get(key), int) or isinstance(data.get(key), bool) or data[key] < 0:
            out.append(f"{key} is not a non-negative integer")
    for key in ("rules_delivered", "candidate_reported"):
        if not isinstance(data.get(key), bool):
            out.append(f"{key} is not true or false")
    status = data.get("status")
    if status == "ready" and not data.get("session_id"):
        out.append("status is ready but no session_id is recorded")
    if status == "unconfirmed" and not data.get("candidate_session_id"):
        out.append("status is unconfirmed but no candidate_session_id is recorded")
    if status == "new" and (data.get("session_id") or data.get("candidate_session_id")):
        out.append("status is new but a session id is recorded")
    running = data.get("running")
    if status == "running":
        if not (isinstance(running, dict) and isinstance(running.get("run_dir"), str)
                and isinstance(running.get("first"), bool) and isinstance(running.get("attempt"), str)
                and UUID_RE.match(str(running.get("asked_id") or ""))
                and running.get("previous_status") in ("new", "ready", "unconfirmed")):
            out.append("status is running but the running run (run_dir, asked_id, first, attempt,"
                       " previous_status) is not fully recorded")
    history = data.get("history")
    if not isinstance(history, list):
        out.append("history is not a list")
    else:
        for i, h in enumerate(history):
            if not (isinstance(h, dict) and isinstance(h.get("generation"), int) and isinstance(h.get("archive"), str)
                    and isinstance(h.get("reset_at"), str) and isinstance(h.get("authorized_by"), str)
                    and _uuid_or_none(h.get("session_id"))):
                out.append(f"history[{i}] is incomplete")
    return out


def upgrade(data: dict) -> dict:
    """Fill fields added after the first states were written, from what those states record."""
    if isinstance(data, dict):
        data.setdefault("failures", 0)
        data.setdefault("rules_delivered", bool(data.get("calls")))
        data.setdefault("candidate_reported", False)
    return data


def read_state(d: Path, purpose: str, root: Path) -> dict | None:
    path = d / "state.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SessionError(
            f"{path} cannot be read as JSON ({exc}). It was left untouched and nothing was sent;"
            " inspect it before any further call.", NEEDS_RESOLUTION) from exc
    problems = state_problems(upgrade(data), purpose, root)
    if problems:
        raise SessionError(
            f"{path} is not a valid {SCHEMA} state for this purpose and project: " + "; ".join(problems)
            + ". It was left untouched and nothing was sent.", NEEDS_RESOLUTION)
    return data


def new_state(purpose: str, mode: str, scope: str, root: Path) -> dict:
    t = now()
    return {"schema": SCHEMA, "purpose": purpose, "scope": scope, "mode": mode, "model": MODEL,
            "project_root": str(root), "generation": 1, "status": "new", "session_id": None,
            "candidate_session_id": None, "candidate_reported": False, "rules_delivered": False,
            "calls": 0, "failures": 0, "created": t, "updated": t, "generation_started": t,
            "last_run": None, "running": None, "conflict": None, "history": []}


# ----------------------------------------------------------------- command


def find_on_path(name: str, avoid: list[Path]) -> str | None:
    """`name` on PATH, skipping relative entries and the folders in `avoid` (the current folder and
    the project): Windows' own lookup would try the current folder first."""
    exts = [e for e in os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";") if e] if os.name == "nt" else [""]
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        entry = entry.strip().strip('"')
        if not entry or not os.path.isabs(entry):
            continue
        if any(same_path(entry, a) for a in avoid):
            continue
        for ext in exts:
            cand = os.path.join(entry, name + ext)
            if os.path.isfile(cand) and (os.name == "nt" or os.access(cand, os.X_OK)):
                return cand
    return None


def claude_command(root: Path) -> list[str]:
    """The Claude CLI: SIJAV_CLAUDE_BIN when set (a .py launcher runs under this Python), else
    `claude` on PATH outside the current and project folders."""
    named = os.environ.get("SIJAV_CLAUDE_BIN", "").strip()
    if named:
        if not Path(named).is_file():
            raise SessionError(f"SIJAV_CLAUDE_BIN names {named}, which does not exist. Nothing was sent.")
        path = named
    else:
        path = find_on_path("claude", [Path.cwd(), root]) or ""
        if not path:
            raise SessionError("no Claude CLI: `claude` is not on PATH (outside the current and project"
                               " folders) and SIJAV_CLAUDE_BIN is not set. Nothing was sent.")
    if path.lower().endswith(".py"):
        return [sys.executable, path]
    return [path]


def child_env() -> tuple[dict, list[str], list[str]]:
    """(the environment for the CLI, names dropped, names of noted variables kept)."""
    env = dict(os.environ)
    dropped = sorted(k for k in env if DROP_ENV.match(k) and k.upper() != "CLAUDE_CODE_OAUTH_TOKEN")
    for k in dropped:
        del env[k]
    kept = sorted(k for k in env if NOTED_ENV.match(k))
    return env, dropped, kept


def check_batch_safe(argv: list[str]) -> None:
    """A .cmd/.bat launcher passes its arguments through cmd.exe, which would reinterpret these."""
    if not argv[0].lower().endswith((".cmd", ".bat")):
        return
    for arg in argv[1:]:
        bad = sorted(set(arg) & BATCH_UNSAFE)
        if bad:
            raise SessionError(f"the Claude launcher {argv[0]} is a batch file and the argument {arg!r}"
                               f" holds {bad}, which cmd.exe would reinterpret. Nothing was sent.", USAGE)


def check_allow(mode: str, allow: list[str]) -> list[str]:
    if allow and mode == "technical":
        raise SessionError("technical mode is read-only: --allow-command is refused (Read, Glob, Grep and"
                           " WebFetch only). Nothing was sent.", USAGE)
    for rule in allow:
        m = ALLOW_RE.match(rule)
        spec = m.group(2).strip() if m else ""
        head = spec[:-2] if spec.endswith(":*") else spec
        first = head.split()[0] if head.split() else ""
        if not m or not first or "*" in first or re.match(r"^[A-Za-z_]+:", head):
            raise SessionError(
                f"--allow-command {rule!r} is not a scoped command rule: write Bash(<command> ...) or"
                " PowerShell(<command> ...) whose first word is the program, with any * after it, e.g."
                " \"Bash(python -m unittest *)\" (Claude Code's rule syntax). Nothing was sent.", USAGE)
    return allow


def mode_tools(mode: str, allow: list[str]) -> tuple[list[str], list[str], list[str]]:
    """(tools offered, rules pre-approved, rules denied) for a mode."""
    if mode == "technical":
        return list(TECHNICAL_TOOLS), ["WebFetch"], [*WRITE_OR_RUN, *SECRET_READS]
    run_tools = sorted({rule.split("(", 1)[0] for rule in allow})
    denied = ["NotebookEdit", *[t for t in COMMAND_TOOLS if t not in run_tools], *PROTECTED_EDITS, *SECRET_READS]
    return list(CODE_TOOLS) + run_tools, [PROJECT_EDIT, *allow], denied


def build_argv(prefix: list[str], mode: str, effort: str, allow: list[str],
               session_args: list[str]) -> list[str]:
    tools, allowed, denied = mode_tools(mode, allow)
    return [*prefix, "-p", "--model", MODEL, "--effort", effort, "--safe-mode", "--restricted",
            "--disable-slash-commands", "--strict-mcp-config",
            "--input-format", "text", "--output-format", "stream-json", "--verbose",
            "--permission-mode", "manual", "--permission-prompts", "none", *session_args,
            "--tools", ",".join(tools), "--allowedTools", *allowed, "--disallowedTools", *denied]


def compose(rules: list[tuple[str, bytes]], prompt: bytes) -> bytes:
    if not rules:
        return prompt
    parts = [RULES_PREAMBLE.encode("utf-8")]
    for path, data in rules:
        tail = b"" if data.endswith(b"\n") else b"\n"
        parts.append(f'<project-rules source="{path}">\n'.encode("utf-8") + data + tail
                     + b"</project-rules>\n\n")
    return b"".join(parts) + prompt


# ------------------------------------------------------------- the stream


class Stream:
    """What the stream-json output reports, gathered line by line. Thinking text and signatures are
    never copied out of the raw log."""

    def __init__(self, mode: str, offered: list[str], expect_id: str, root: Path):
        self.mode, self.offered, self.expect_id, self.root = mode, offered, expect_id, root
        self.session_ids: list[str] = []
        self.bad_ids: list[str] = []
        self.init: dict | None = None
        self.init_extra: dict = {}
        self.events = 0
        self.assistant_models: list[str] = []
        self.assistant_seen = False
        self.result: dict | None = None
        self.refused: str | None = None
        self.lines = 0
        self.unparsed = 0
        self.oversized = 0

    def feed(self, raw: bytes) -> None:
        self.lines += 1
        text = raw.decode("utf-8", "replace").strip()
        if not text:
            return
        try:
            ev = json.loads(text)
        except ValueError:
            self.unparsed += 1
            return
        if not isinstance(ev, dict):
            self.unparsed += 1
            return
        self.events += 1
        sid = ev.get("session_id")
        if isinstance(sid, str) and sid:
            if not UUID_RE.match(sid):
                if sid not in self.bad_ids:
                    self.bad_ids.append(sid)
            elif sid not in self.session_ids:
                self.session_ids.append(sid)
        kind = ev.get("type")
        if self.events == 1 and not (kind == "system" and ev.get("subtype") == "init"):
            self.refuse(f"the CLI's first event was {kind!r}/{ev.get('subtype')!r}, not its init, so the"
                        " model, tools and permissions could not be checked before it acted")
        if kind == "system" and ev.get("subtype") == "init" and self.init is None:
            self.init = {k: ev.get(k) for k in ("model", "tools", "permissionMode", "mcp_servers",
                                                 "claude_code_version", "cwd", "apiKeySource", "session_id")}
            self.init_extra = {"plugins": [p.get("name") for p in ev.get("plugins") or [] if isinstance(p, dict)],
                               "skills": len(ev.get("skills") or []), "agents": ev.get("agents"),
                               "output_style": ev.get("output_style")}
            problems = self.init_problems()
            if problems:
                self.refuse("; ".join(problems))
        elif kind == "assistant" and ev.get("parent_tool_use_id") is None:
            self.assistant_seen = True
            message = ev.get("message") if isinstance(ev.get("message"), dict) else {}
            model = message.get("model")
            if isinstance(model, str) and model not in self.assistant_models:
                self.assistant_models.append(model)
        elif kind == "result":
            self.result = ev

    def refuse(self, why: str) -> None:
        if self.refused is None:
            self.refused = why

    def init_problems(self) -> list[str]:
        out, i = [], self.init
        if i.get("model") != MODEL:
            out.append(f"the CLI started on model {i.get('model')!r}, not {MODEL}")
        tools = i.get("tools")
        if not isinstance(tools, list):
            out.append("the CLI did not report its tools")
        else:
            extra = [t for t in tools if t not in self.offered]
            if extra:
                out.append(f"the CLI offered tools that were not granted: {extra}")
        if i.get("mcp_servers") not in ([], None):
            out.append(f"the CLI loaded MCP servers despite --strict-mcp-config: {i.get('mcp_servers')}")
        if i.get("permissionMode") not in NORMAL_MODES:
            out.append(f"the CLI reported permission mode {i.get('permissionMode')!r}, not the normal mode")
        if i.get("apiKeySource") != "none":
            out.append(f"the CLI reported apiKeySource {i.get('apiKeySource')!r}: it would not use its own"
                       " sign-in")
        if i.get("session_id") != self.expect_id:
            out.append(f"the CLI reported session {i.get('session_id')!r}, not {self.expect_id}")
        if not (isinstance(i.get("cwd"), str) and same_path(i["cwd"], self.root)):
            out.append(f"the CLI reported working folder {i.get('cwd')!r}, not the project {self.root}")
        return out

    @property
    def delivered(self) -> bool:
        """Whether a turn reached the model in this run (the prompt is then in the conversation)."""
        turns = (self.result or {}).get("num_turns")
        return self.assistant_seen or (isinstance(turns, int) and turns > 0)


def kind_of(text: str) -> str:
    if LIMIT_RE.search(text):
        return "allowance or rate limit"
    if AUTH_RE.search(text):
        return "authentication"
    return "error"


def tail_of(path: Path, limit: int = 1500) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    return data[-limit:].decode("utf-8", "replace").strip()


def outcome_of(stream: Stream, proc: dict, run_dir: Path, extra_cause: str = "") -> dict:
    """The run's status and exact cause from what the CLI reported."""
    result = stream.result or {}
    exit_code = proc.get("exit_code")
    reply = result.get("result") if isinstance(result.get("result"), str) else ""
    served = [m for m in [(stream.init or {}).get("model"), *stream.assistant_models] if m]
    wrong = sorted({m for m in served if m != MODEL})
    stderr_tail = tail_of(run_dir / "stderr.txt")
    success = bool(stream.result) and not result.get("is_error") and result.get("subtype") == "success"
    if extra_cause:
        status, cause = "failed", extra_cause
    elif stream.refused:
        status, cause = "refused", f"stopped at the CLI's first events: {stream.refused}"
    elif stream.bad_ids:
        status, cause = "refused", f"the CLI reported session ids that are not UUIDs: {stream.bad_ids}"
    elif proc.get("timed_out") and not success:
        status = "timeout"
        cause = ("the call exceeded its timeout and the Claude process tree was stopped"
                 + ("" if stream.init else "; the CLI had not sent its init event"))
    elif stream.init is None:
        status = "failed"
        cause = ("the CLI never sent its init event" + (f"; stderr ends: {stderr_tail}" if stderr_tail else "")
                 + ("" if stream.lines else "; it printed nothing on stdout") + exit_text(exit_code))
    elif not stream.result:
        status = "failed"
        cause = (f"the CLI ended{exit_text(exit_code)} without a result event"
                 + (f"; stderr ends: {stderr_tail}" if stderr_tail else ""))
    elif not success:
        status = "failed"
        cause = f"the CLI reported {result.get('subtype')!r} (is_error {result.get('is_error')}): {reply[:2000]}"
    elif exit_code not in (0, None) and not proc.get("timed_out"):
        status, cause = "failed", f"the CLI exited {exit_code} after reporting success"
    elif wrong:
        status, cause = "wrong_model", f"the CLI served {wrong}, not {MODEL}; the reply is kept but not used"
    elif not reply.strip():
        status, cause = "failed", "the CLI reported success with an empty reply"
    else:
        status, cause = "ok", ""
    failure_text = f"{stderr_tail}\n{reply if not success else ''}\n{cause}"
    usage_models = sorted((result.get("modelUsage") or {}).keys()) if isinstance(result.get("modelUsage"), dict) else []
    denials = result.get("permission_denials")
    return {"status": status, "cause": cause, "kind": "" if status == "ok" else kind_of(failure_text),
            "permission_denials": denials if isinstance(denials, list) else [],
            "reply": reply, "served_models": {"init": (stream.init or {}).get("model"),
                                              "assistant": stream.assistant_models,
                                              "usage": usage_models},
            "other_models_in_usage": [m for m in usage_models if m != MODEL],
            "stopped_after_result": bool(proc.get("timed_out") and success),
            "stderr_tail": stderr_tail}


def exit_text(code) -> str:
    return "" if code is None else f" (exit {code})"


# ------------------------------------------------------------- the process


class ProcessTree:
    """The CLI and everything it starts. Windows: a Job Object closed with the call (and by the OS
    when the helper dies). POSIX: the CLI's own process group."""

    def __init__(self):
        self.job = None
        self.note = ""

    def popen(self, argv, cwd: Path, env: dict, stderr) -> subprocess.Popen:
        if os.name != "nt":
            self.note = "POSIX process group (a descendant that calls setsid escapes it)"
            return subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr,
                                    cwd=str(cwd), env=env, start_new_session=True)
        k32 = _kernel32()
        job = k32.CreateJobObjectW(None, None)
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not job or not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            self.note = f"no Job Object (error {ctypes.get_last_error()}); taskkill /T is used instead"
            if job:
                k32.CloseHandle(job)
            return subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr,
                                    cwd=str(cwd), env=env)
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr,
                                cwd=str(cwd), env=env, creationflags=0x4)  # CREATE_SUSPENDED
        assigned = k32.AssignProcessToJobObject(job, int(proc._handle))
        if assigned:
            self.job, self.note = job, "Windows Job Object (kill on close)"
        else:
            self.note = f"Job Object assignment failed (error {ctypes.get_last_error()}); taskkill /T is used instead"
            k32.CloseHandle(job)
        if _ntdll().NtResumeProcess(int(proc._handle)) != 0:
            with contextlib.suppress(OSError):
                proc.kill()
            self.close()
            raise OSError("the Claude CLI could not be resumed after it was placed in its Job Object")
        return proc

    def stop(self, proc) -> None:
        """Stop the CLI and every descendant still in the tree."""
        if os.name == "nt":
            if self.job:
                _kernel32().TerminateJobObject(self.job, 1)
            elif proc.poll() is None:
                with contextlib.suppress(OSError, subprocess.SubprocessError):
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, timeout=30)
        else:
            with contextlib.suppress(OSError):
                os.killpg(proc.pid, signal.SIGKILL)
        if proc.poll() is None:
            with contextlib.suppress(OSError):
                proc.kill()

    def close(self) -> None:
        if self.job:
            _kernel32().CloseHandle(self.job)
            self.job = None


if os.name == "nt":
    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                                                      "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION), ("IoInfo", _IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    _K32 = None
    _NT = None

    def _kernel32():
        global _K32
        if _K32 is None:
            k = ctypes.WinDLL("kernel32", use_last_error=True)
            k.CreateJobObjectW.restype = wintypes.HANDLE
            k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
            k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            k.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
            k.CloseHandle.argtypes = [wintypes.HANDLE]
            k.OpenProcess.restype = wintypes.HANDLE
            k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            k.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            _K32 = k
        return _K32

    def _ntdll():
        global _NT
        if _NT is None:
            n = ctypes.WinDLL("ntdll")
            n.NtResumeProcess.argtypes = [wintypes.HANDLE]
            n.NtResumeProcess.restype = ctypes.c_long
            _NT = n
        return _NT


def pid_alive(pid) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        k = _kernel32()
        h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ctypes.get_last_error() == 5  # access denied: it exists
        try:
            code = wintypes.DWORD()
            return bool(k.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
        finally:
            k.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def launch(argv: list[str], sent: bytes, cwd: Path, run_dir: Path, timeout: int, stream: Stream,
           on_start=None) -> dict:
    """Run the CLI once. Raw stdout goes byte for byte into stdout.jsonl as it arrives (a reader
    thread, so a pipe a descendant keeps open cannot hang the call), stderr straight into stderr.txt.
    The tree is stopped on refusal, timeout, any exception (KeyboardInterrupt included) and, after the
    CLI exits, to end descendants it left behind."""
    env, dropped, kept = child_env()
    tree = ProcessTree()
    info = {"argv": argv, "cwd": str(cwd), "started": now(), "helper_pid": os.getpid(), "claude_pid": None,
            "timeout_seconds": timeout, "stdin": "prompt.txt", "dropped_environment": dropped,
            "kept_environment_noted": kept}
    state = {"exit_code": None, "timed_out": False, "stdin_error": None, "reader_error": None,
             "drained": True, "stopped_by_helper": None}
    with open(run_dir / "stdout.jsonl", "xb") as out, open(run_dir / "stderr.txt", "xb") as err:
        try:
            proc = tree.popen(argv, cwd, env, err)
        except OSError as exc:
            info["launch_error"] = f"{type(exc).__name__}: {exc}"
            write_new(run_dir / "command.json", info)
            return {**state, "ended": now(), "launch_error": info["launch_error"], "process_tree": tree.note}
        stop_now = threading.Event()
        try:
            info.update(claude_pid=proc.pid, process_tree=tree.note)
            write_new(run_dir / "command.json", info)
            if on_start:
                on_start(proc.pid)

            def feed() -> None:
                try:
                    proc.stdin.write(sent)
                    proc.stdin.close()
                except OSError as exc:
                    state["stdin_error"] = f"{type(exc).__name__}: {exc}"

            def read() -> None:
                pending = b""
                try:
                    while True:
                        chunk = proc.stdout.read1(READ_CHUNK)
                        if not chunk:
                            break
                        out.write(chunk)
                        out.flush()
                        pending += chunk
                        while b"\n" in pending:
                            line, pending = pending.split(b"\n", 1)
                            stream.feed(line)
                        if len(pending) > MAX_LINE:
                            stream.oversized += 1
                            pending = b""
                        if stream.refused:
                            stop_now.set()
                    if pending:
                        stream.feed(pending)
                    if stream.refused:
                        stop_now.set()
                except BaseException as exc:  # noqa: BLE001 -- reported, and the tree is stopped
                    state["reader_error"] = f"{type(exc).__name__}: {exc}"
                    stop_now.set()

            feeder = threading.Thread(target=feed, daemon=True)
            reader = threading.Thread(target=read, daemon=True)
            feeder.start()
            reader.start()
            deadline = time.monotonic() + timeout
            while True:
                try:
                    state["exit_code"] = proc.wait(timeout=0.1)
                    break
                except subprocess.TimeoutExpired:
                    pass
                if stop_now.is_set():
                    state["stopped_by_helper"] = "refused" if stream.refused else "reader error"
                    tree.stop(proc)
                elif time.monotonic() >= deadline:
                    state["timed_out"] = True
                    state["stopped_by_helper"] = "timeout"
                    tree.stop(proc)
            tree.stop(proc)  # descendants the CLI left behind end with the call
            reader.join(DRAIN_SECONDS)
            state["drained"] = not reader.is_alive()
            feeder.join(1)
        except BaseException:
            tree.stop(proc)
            with contextlib.suppress(NameError):
                reader.join(DRAIN_SECONDS)
            raise
        finally:
            if proc.poll() is None:
                tree.stop(proc)
            tree.close()
            reader_done = "reader" not in locals() or not reader.is_alive()
            if reader_done:  # a pipe still held by an escaped descendant is left to process exit
                with contextlib.suppress(OSError):
                    proc.stdout.close()
    return {**state, "ended": now(), "process_tree": tree.note}


# ---------------------------------------------------------------- the call


def read_prompt(prompt_file: str | None) -> bytes:
    if prompt_file:
        try:
            data = Path(prompt_file).read_bytes()
        except OSError as exc:
            raise SessionError(f"cannot read --prompt-file {prompt_file}: {exc}", USAGE) from exc
    else:
        if sys.stdin is None or sys.stdin.isatty():
            raise SessionError("give the prompt with --prompt-file FILE or on stdin", USAGE)
        data = sys.stdin.buffer.read()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SessionError(f"the prompt is not UTF-8 ({exc})", USAGE) from exc
    if not text.strip():
        raise SessionError("the prompt is empty", USAGE)
    return data


def read_rules(paths: list[str]) -> list[tuple[str, bytes]]:
    out = []
    for p in paths:
        path = Path(p).resolve()
        try:
            data = path.read_bytes()
            data.decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise SessionError(f"cannot read --rules-file {path} as UTF-8 text: {exc}", USAGE) from exc
        out.append((str(path), data))
    return out


def finish_state(state: dict, run_dir: Path, outcome: dict, stream: Stream, running: dict,
                 rules_sent: bool) -> str:
    """Update the purpose after a run. Returns a note on the session, or ''. A session id is
    confirmed only when the CLI reported it and a turn reached the model."""
    previous, asked, attempt = running["previous_status"], running["asked_id"], running["attempt"]
    state.update(running=None, last_run=str(run_dir), updated=now())
    if outcome["status"] == "ok":
        state["calls"] += 1
    else:
        state["failures"] += 1
    ids = stream.session_ids
    seen = ids[0] if ids else None
    if len(ids) > 1 or (seen and seen != asked):
        state["status"] = "conflict"
        state["conflict"] = (f"run {run_dir.name} asked for session {asked} but the CLI reported {ids}")
        return state["conflict"]
    delivered = bool(seen) and stream.delivered
    if delivered:
        if rules_sent:
            state["rules_delivered"] = True
        state.update(status="ready", session_id=asked, candidate_session_id=None, candidate_reported=False)
        return "" if previous == "ready" else f"session {asked} confirmed"
    if previous == "ready":
        state["status"] = "ready"
        return f"session {asked} kept: this call failed and nothing changed the conversation's id"
    reported = bool(seen) or state.get("candidate_reported", False)
    state.update(status="unconfirmed", session_id=None, candidate_session_id=asked, candidate_reported=reported)
    hint = "existing (the CLI reported this id)" if reported else "new (the CLI never reported this id)"
    return (f"no turn was delivered ({attempt} attempt), so session {asked} stays unconfirmed. Nothing more"
            f" is sent until the owner chooses --resume-unconfirmed new|existing (evidence suggests {hint})"
            " or authorizes reset")


def session_args_for(status: str, state: dict, resume_unconfirmed: str | None) -> tuple[str, list[str], str]:
    """(the id asked for, its CLI arguments, which attempt this is) for the purpose's state."""
    if status == "new":
        sid = str(uuid.uuid4())
        return sid, ["--session-id", sid], "first"
    if status == "ready":
        return state["session_id"], ["--resume", state["session_id"]], "resume"
    sid = state["candidate_session_id"]
    if resume_unconfirmed == "new":
        return sid, ["--session-id", sid], "unconfirmed-new"
    return sid, ["--resume", sid], "unconfirmed-existing"


def cmd_call(a) -> int:
    if switched_off():
        print("CLAUDE OFF: SIJAV_CLAUDE=off, so no Claude call was made and nothing was recorded. The"
              " coding or technical review stays unfinished; Codex does not take it over.", file=sys.stderr)
        return OFF
    purpose = check_purpose(a.purpose)
    if a.timeout <= 0:
        raise SessionError("--timeout must be a positive number of seconds", USAGE)
    allow = check_allow(a.mode, a.allow_command)
    if a.rules_file and a.no_project_rules:
        raise SessionError("--rules-file and --no-project-rules contradict each other", USAGE)
    root = project_root(a.project)
    prompt = read_prompt(a.prompt_file)
    rules = read_rules(a.rules_file)
    prefix = claude_command(root)
    d = purpose_dir(root, purpose)
    if not d.is_dir():  # a new purpose: refuse everything a new state would refuse, before creating anything
        if not (a.scope or "").strip():
            raise SessionError(f"{purpose!r} is a new purpose: give --scope (function-wide or the task/topic it"
                               " serves). Nothing was created.", USAGE)
        if a.resume_unconfirmed:
            raise SessionError(f"purpose {purpose!r} has no unconfirmed session (it is new). Nothing was created.",
                               USAGE)
        if not rules and not a.no_project_rules:
            raise SessionError("safe mode loads no CLAUDE.md: a new purpose's first call must pass the project's"
                               " law/rules with --rules-file, or say --no-project-rules. Nothing was created.", USAGE)
        d.mkdir(parents=True, exist_ok=True)
    with purpose_lock(d, purpose):
        state = read_state(d, purpose, root)
        if state is None:
            if not (a.scope or "").strip():
                raise SessionError(f"{purpose!r} has no state: give --scope to start it", USAGE)
            state = new_state(purpose, a.mode, a.scope.strip(), root)
        if state["mode"] != a.mode:
            raise SessionError(f"purpose {purpose!r} is a {state['mode']} purpose; this call asked for"
                               f" {a.mode}. Use a purpose of that mode. Nothing was sent.", USAGE)
        if a.scope and a.scope.strip() != state["scope"]:
            raise SessionError(f"purpose {purpose!r} has scope {state['scope']!r}; a purpose keeps its"
                               " scope. Nothing was sent.", USAGE)
        status = state["status"]
        if status == "running":
            run = state["running"]
            raise SessionError(
                f"purpose {purpose!r}: run {run.get('run_dir')} never finished recording (helper pid"
                f" {run.get('helper_pid')}, Claude pid {run.get('claude_pid')}). Make sure that Claude"
                " process has stopped, then run `recover --confirm-stopped`. Nothing was sent.",
                NEEDS_RESOLUTION)
        if status == "conflict":
            raise SessionError(f"purpose {purpose!r} is in conflict: {state.get('conflict')}. Only an"
                               " owner-authorized `reset` starts another conversation. Nothing was sent.",
                               NEEDS_RESOLUTION)
        if status == "unconfirmed" and not a.resume_unconfirmed:
            raise SessionError(
                f"purpose {purpose!r}: no turn was delivered, so session {state['candidate_session_id']} is"
                " unconfirmed (" + ("the CLI reported it" if state["candidate_reported"] else "the CLI never"
                                    " reported it") + "). Have the owner choose one attempt,"
                " --resume-unconfirmed new or existing, or authorize `reset`. Nothing was sent.", NEEDS_RESOLUTION)
        if a.resume_unconfirmed and status != "unconfirmed":
            raise SessionError(f"purpose {purpose!r} has no unconfirmed session (status {status})", USAGE)
        if not state["rules_delivered"] and not rules and not a.no_project_rules:
            raise SessionError(
                "safe mode loads no CLAUDE.md and no call of this conversation has delivered a turn yet:"
                " pass the project's law/rules with --rules-file, or say --no-project-rules."
                " Nothing was sent.", USAGE)
        asked_id, session_args, attempt = session_args_for(status, state, a.resume_unconfirmed)
        if not UUID_RE.match(asked_id):
            raise SessionError(f"session id {asked_id!r} is not a UUID; nothing was sent", NEEDS_RESOLUTION)
        argv = build_argv(prefix, a.mode, a.effort, allow, session_args)
        check_batch_safe(argv)
        sent = compose(rules, prompt)

        runs = d / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        run_dir = runs / f"{stamp()}-g{state['generation']}-{uuid.uuid4().hex[:6]}"
        run_dir.mkdir()
        write_new(run_dir / "prompt.txt", sent)
        write_new(run_dir / "brief.json", {
            "purpose": purpose, "scope": state["scope"], "mode": a.mode, "generation": state["generation"],
            "requested_model": MODEL, "effort": a.effort, "project_root": str(root),
            "attempt": attempt, "session": {"asked": asked_id, "args": session_args},
            "prompt_source": str(Path(a.prompt_file).resolve()) if a.prompt_file else "stdin",
            "prompt_sha256": hashlib.sha256(prompt).hexdigest(),
            "sent_sha256": hashlib.sha256(sent).hexdigest(),
            "rules_files": [{"path": p, "sha256": hashlib.sha256(b).hexdigest()} for p, b in rules],
            "no_project_rules": bool(a.no_project_rules), "allow_command": allow})
        running = {"run_dir": str(run_dir), "helper_pid": os.getpid(), "claude_pid": None, "started": now(),
                   "first": status == "new", "attempt": attempt, "asked_id": asked_id, "previous_status": status}
        state.update(status="running", updated=now(), running=running)
        write_json_atomic(d / "state.json", state)

        def started(pid: int) -> None:
            running["claude_pid"] = pid
            with contextlib.suppress(SessionError):
                write_json_atomic(d / "state.json", state, seconds=1.0)

        offered = mode_tools(a.mode, allow)[0]
        stream = Stream(a.mode, offered, asked_id, root)
        proc = launch(argv, sent, root, run_dir, a.timeout, stream, on_start=started)
        extra = ""
        if proc.get("launch_error"):
            extra = f"the Claude CLI could not be started: {proc['launch_error']}"
        elif proc.get("reader_error"):
            extra = f"the helper could not read the CLI's output ({proc['reader_error']}); the CLI was stopped"
        outcome = outcome_of(stream, proc, run_dir, extra)
        note = finish_state(state, run_dir, outcome, stream, running, rules_sent=bool(rules or a.no_project_rules))
        result_problem = ""
        try:
            write_result(run_dir, state, a.mode, a.effort, running, stream, proc, outcome, note)
        except OSError as exc:
            result_problem = f"result.json could not be written: {exc}"
        state_problem = ""
        try:
            write_json_atomic(d / "state.json", state)
        except SessionError as exc:
            state_problem = str(exc)

    return report(purpose, state, run_dir, outcome, note, result_problem, state_problem)


def report(purpose: str, state: dict, run_dir: Path, outcome: dict, note: str, result_problem: str,
           state_problem: str) -> int:
    record = run_dir / "result.json"
    if note:
        print(f"[claude-session] {note}", file=sys.stderr)
    if outcome["other_models_in_usage"]:
        print(f"[claude-session] usage also lists {outcome['other_models_in_usage']} (for example a tool's own"
              " model); the reply's model was checked separately", file=sys.stderr)
    if outcome["permission_denials"]:
        names = sorted({str(d.get("tool_name") or d.get("tool") or d) if isinstance(d, dict) else str(d)
                        for d in outcome["permission_denials"]})
        print(f"[claude-session] the CLI denied {len(outcome['permission_denials'])} tool use(s): {names}. The reply"
              f" may rest on less than was asked for; the denials are in {record}", file=sys.stderr)
    if outcome["status"] == "ok":
        raw = getattr(sys.stdout, "buffer", None)
        if raw is None:
            print(outcome["reply"])
        else:  # bytes, so the reply keeps its own line endings on Windows too
            sys.stdout.flush()
            raw.write(outcome["reply"].encode("utf-8") + b"\n")
            raw.flush()
    else:
        print(f"CLAUDE CALL FAILED ({outcome['status']}, {outcome['kind']}): {outcome['cause']}\n"
              f"No retry and no other model were tried. Record (full stdout/stderr kept): {run_dir}",
              file=sys.stderr)
    if result_problem:
        print(f"CLAUDE RUN RECORD NOT SAVED: {result_problem}. The raw output stays in {run_dir}.", file=sys.stderr)
    if state_problem:
        print(f"CLAUDE SESSION STATE NOT SAVED: {state_problem}. The purpose still shows this run as running;"
              " run `recover --confirm-stopped` once nothing holds state.json.", file=sys.stderr)
    if (result_problem or state_problem) and outcome["status"] == "ok":
        return NOT_SAVED  # the reply above was printed; only its bookkeeping failed
    if outcome["status"] == "ok":
        print(f"[claude-session] purpose {purpose}, session {state['session_id']}, record: {record}", file=sys.stderr)
        return NEEDS_RESOLUTION if state["status"] == "conflict" else OK
    return FAILED


def write_result(run_dir: Path, state: dict, mode: str, effort, running: dict, stream: Stream, proc: dict,
                 outcome: dict, note: str, recovered: bool = False) -> None:
    result = stream.result or {}
    if outcome["reply"] and not (run_dir / "reply.md").exists():
        write_new(run_dir / "reply.md", outcome["reply"])
    write_new(run_dir / "result.json", {
        "schema": SCHEMA, "purpose": state["purpose"], "scope": state["scope"], "mode": mode,
        "generation": state["generation"], "status": outcome["status"], "cause": outcome["cause"],
        "kind": outcome["kind"], "recovered_after_interruption": recovered,
        "requested_model": MODEL, "effort": effort, "served_models": outcome["served_models"],
        "other_models_in_usage": outcome["other_models_in_usage"],
        "permission_denials": outcome.get("permission_denials", []),
        "session": {"attempt": running["attempt"], "asked": running["asked_id"], "reported": stream.session_ids,
                    "delivered": stream.delivered, "kept": state.get("session_id"),
                    "candidate": state.get("candidate_session_id"), "purpose_status": state["status"], "note": note},
        "exit_code": proc.get("exit_code"), "timed_out": proc.get("timed_out", False),
        "stopped_by_helper": proc.get("stopped_by_helper"), "stopped_after_result": outcome["stopped_after_result"],
        "output_drained": proc.get("drained", True), "process_tree": proc.get("process_tree"),
        "stdin_error": proc.get("stdin_error"), "reader_error": proc.get("reader_error"), "ended": proc.get("ended"),
        "reply_file": "reply.md" if outcome["reply"] else None,
        "stdout_lines": stream.lines, "unparsed_stdout_lines": stream.unparsed, "oversized_lines": stream.oversized,
        "init": stream.init, "init_extra": stream.init_extra,
        "cli_version_differs_from_pinned": bool(stream.init) and (stream.init.get("claude_code_version") != PINNED_CLI),
        "result_event": {k: result.get(k) for k in (
            "subtype", "is_error", "num_turns", "duration_ms", "duration_api_ms", "total_cost_usd",
            "usage", "modelUsage", "permission_denials", "stop_reason", "terminal_reason")}
        if stream.result else None})


# ------------------------------------------------------- commands without a call


def existing_purpose_dir(root: Path, purpose: str) -> Path:
    d = purpose_dir(root, check_purpose(purpose))
    if not d.is_dir():
        raise SessionError(f"no purpose {purpose!r} in {sessions_dir(root)}", USAGE)
    return d


def all_states(root: Path) -> list[tuple[str, dict | None, str]]:
    base = sessions_dir(root)
    out = []
    if not base.is_dir():
        return out
    for d in sorted(p for p in base.iterdir() if p.is_dir()):
        try:
            st = read_state(d, d.name, root)
            out.append((d.name, st, "" if st else "no state.json (created but never started)"))
        except SessionError as exc:
            out.append((d.name, None, str(exc)))
    return out


def runs_of(d: Path) -> list[dict]:
    out = []
    runs = d / "runs"
    if not runs.is_dir():
        return out
    for r in sorted(p for p in runs.iterdir() if p.is_dir()):
        brief = read_json(r / "brief.json") or {}
        result = read_json(r / "result.json")
        out.append({"run": str(r), "generation": brief.get("generation"),
                    "status": result.get("status") if result else "no result recorded (running or interrupted)",
                    "cause": (result or {}).get("cause", ""),
                    "session": (result or {}).get("session") or brief.get("session"),
                    "prompt": str(r / "prompt.txt"),
                    "reply": str(r / "reply.md") if (r / "reply.md").is_file() else None})
    return out


def cmd_list(a) -> int:
    root = project_root(a.project)
    rows = []
    for name, state, problem in all_states(root):
        if state is None:
            rows.append({"purpose": name, "problem": problem})
        else:
            rows.append({k: state.get(k) for k in ("purpose", "mode", "scope", "generation", "status",
                                                   "session_id", "candidate_session_id", "calls", "failures",
                                                   "last_run")})
    if a.json:
        print(json.dumps({"project": str(root), "purposes": rows}, indent=2, ensure_ascii=False))
        return OK
    print(f"Claude purposes in {sessions_dir(root)}:")
    if not rows:
        print("  none")
    for r in rows:
        if "problem" in r:
            print(f"  {r['purpose']}: UNREADABLE: {r['problem']}")
        else:
            print(f"  {r['purpose']} [{r['mode']}] generation {r['generation']}, {r['status']},"
                  f" session {r['session_id'] or r['candidate_session_id'] or '-'}, {r['calls']} calls,"
                  f" {r['failures']} failed; scope: {r['scope']}")
    return OK


def cmd_status(a) -> int:
    root = project_root(a.project)
    d = existing_purpose_dir(root, a.purpose)
    state = read_state(d, a.purpose, root)
    if state is None:
        raise SessionError(f"{d} has no state.json", NEEDS_RESOLUTION)
    if a.json:
        print(json.dumps(state, indent=2, ensure_ascii=False))
        return OK
    print(f"purpose {state['purpose']} [{state['mode']}] in {root}")
    print(f"  scope: {state['scope']}")
    print(f"  generation {state['generation']}, status {state['status']}, session {state['session_id'] or '-'}")
    if state.get("candidate_session_id"):
        print(f"  unconfirmed session id: {state['candidate_session_id']}"
              f" ({'reported by the CLI: --resume-unconfirmed existing' if state['candidate_reported'] else 'never reported by the CLI: --resume-unconfirmed new'} is the likely choice)")
    if not state["rules_delivered"]:
        print("  the project's rules have not reached a delivered turn yet: the next call needs --rules-file"
              " or --no-project-rules")
    if state.get("running"):
        print(f"  unfinished run: {state['running']}")
    if state["status"] == "conflict":
        print(f"  conflict: {state.get('conflict')}")
    print(f"  {state['calls']} calls, {state['failures']} failed; last run {state['last_run']}")
    for h in state["history"]:
        print(f"  retired generation {h['generation']}: session {h.get('session_id')},"
              f" reset {h['reset_at']} ({h['authorized_by']!r}), kept in {h['archive']}")
    return OK


def cmd_context(a) -> int:
    root = project_root(a.project)
    d = existing_purpose_dir(root, a.purpose)
    state = read_state(d, a.purpose, root)
    if state is None:
        raise SessionError(f"{d} has no state.json", NEEDS_RESOLUTION)
    print(f"# Claude purpose {state['purpose']} ({state['mode']})\n\nscope: {state['scope']}\n"
          f"current generation {state['generation']}, status {state['status']},"
          f" session {state['session_id'] or '-'}\n")
    for run in runs_of(d):
        print(f"## generation {run['generation']}: {Path(run['run']).name}: {run['status']}")
        if run["cause"]:
            print(f"cause: {run['cause']}")
        print(f"session: {json.dumps(run['session'])}\nprompt: {run['prompt']}\nreply: {run['reply'] or '-'}")
        if a.full:
            with contextlib.suppress(OSError):
                print("\n--- prompt as sent ---\n" + Path(run["prompt"]).read_text(encoding="utf-8"))
            if run["reply"]:
                print("--- reply ---\n" + Path(run["reply"]).read_text(encoding="utf-8"))
        print()
    return OK


def cmd_reset(a) -> int:
    if not a.authorized_by.strip():
        raise SessionError("--authorized-by must quote the owner's instruction to start afresh", USAGE)
    root = project_root(a.project)
    purpose = a.purpose
    d = existing_purpose_dir(root, purpose)
    with purpose_lock(d, purpose):
        state = read_state(d, purpose, root)
        if state is None:
            raise SessionError(f"{d} has no state.json", NEEDS_RESOLUTION)
        if state["status"] == "running":
            raise SessionError(f"purpose {purpose!r} has an unfinished run; `recover` it first", NEEDS_RESOLUTION)
        gens = d / "generations"
        gens.mkdir(exist_ok=True)
        archive = gens / f"{state['generation']}.json"
        if archive.exists():  # an earlier reset stopped before saving the state: keep both copies
            archive = gens / f"{state['generation']}-{stamp()}.json"
        t = now()
        write_new(archive, {**state, "retired": t, "authorized_by": a.authorized_by})
        state["history"].append({"generation": state["generation"], "session_id": state["session_id"],
                                 "candidate_session_id": state.get("candidate_session_id"),
                                 "status": state["status"], "reset_at": t,
                                 "authorized_by": a.authorized_by, "archive": str(archive)})
        state.update(generation=state["generation"] + 1, status="new", session_id=None,
                     candidate_session_id=None, candidate_reported=False, rules_delivered=False,
                     calls=0, failures=0, conflict=None, updated=t, generation_started=t)
        write_json_atomic(d / "state.json", state)
    print(f"purpose {purpose}: generation {state['generation'] - 1} retired to {archive}; the next call"
          f" starts generation {state['generation']} and needs --rules-file or --no-project-rules. All"
          f" earlier runs stay under {d / 'runs'}.")
    return OK


def cmd_recover(a) -> int:
    if not a.confirm_stopped:
        raise SessionError("recover needs --confirm-stopped: first make sure the run's Claude process is"
                           " no longer running", USAGE)
    root = project_root(a.project)
    purpose = a.purpose
    d = existing_purpose_dir(root, purpose)
    with purpose_lock(d, purpose):
        state = read_state(d, purpose, root)
        if state is None or state["status"] != "running":
            raise SessionError(f"purpose {purpose!r} has no unfinished run to recover", USAGE)
        running = state["running"]
        pid = running.get("claude_pid")
        if pid_alive(pid) and not a.pid_is_other_program:
            raise SessionError(
                f"Claude pid {pid} of that run is still alive. Stop it first; if you have checked that the pid now"
                " belongs to another program, pass --pid-is-other-program.", NEEDS_RESOLUTION)
        run_dir = Path(running["run_dir"])
        brief = read_json(run_dir / "brief.json") or {}
        stream = Stream(state["mode"], mode_tools(state["mode"], brief.get("allow_command") or [])[0],
                        running["asked_id"], root)
        with contextlib.suppress(OSError):
            with open(run_dir / "stdout.jsonl", "rb") as f:
                for line in f:
                    stream.feed(line)
        kept = read_json(run_dir / "result.json")
        if kept:  # the run was recorded; only the purpose's state was not saved
            outcome = outcome_of(stream, {"exit_code": kept.get("exit_code"), "timed_out": kept.get("timed_out")},
                                 run_dir)
            outcome["cause"] = ((outcome["cause"] + "; ") if outcome["cause"] else "") + \
                "the helper recorded this run but could not save the purpose's state"
        else:
            outcome = outcome_of(stream, {"exit_code": None}, run_dir)
            outcome["cause"] = ("the helper was interrupted before recording; "
                                + (outcome["cause"] or "the CLI had reported success"))
            outcome["status"] = "interrupted"
            outcome["kind"] = "interrupted"
        rules_sent = bool(brief.get("rules_files") or brief.get("no_project_rules"))
        note = finish_state(state, run_dir, outcome, stream, running, rules_sent)
        if kept is None:
            write_result(run_dir, state, state["mode"], brief.get("effort"), running, stream, {"exit_code": None},
                         outcome, note, recovered=True)
        write_json_atomic(d / "state.json", state)
    print(f"purpose {purpose}: run {run_dir} recorded as {outcome['status']}; status now {state['status']},"
          f" session {state['session_id'] or state.get('candidate_session_id') or '-'}."
          + (f" {note}" if note else ""))
    return OK


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, allow_abbrev=False,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    c = sub.add_parser("call", help="send one prompt in the purpose's conversation", allow_abbrev=False)
    c.add_argument("--purpose", required=True)
    c.add_argument("--mode", required=True, choices=MODES)
    c.add_argument("--scope", default="", help="required for a new purpose: what line of work it holds")
    c.add_argument("--effort", default=DEFAULT_EFFORT, choices=EFFORTS)
    c.add_argument("--prompt-file", default=None, help="the prompt; else stdin")
    c.add_argument("--rules-file", action="append", default=[], help="project law/rules sent before the prompt")
    c.add_argument("--no-project-rules", action="store_true")
    c.add_argument("--allow-command", action="append", default=[], help='code mode: e.g. "Bash(python -m unittest *)"')
    c.add_argument("--resume-unconfirmed", choices=("new", "existing"), default=None,
                   help="the owner's one attempt for an unconfirmed session")
    c.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    c.add_argument("--project", default=None)

    lst = sub.add_parser("list", allow_abbrev=False)
    lst.add_argument("--project", default=None)
    lst.add_argument("--json", action="store_true")

    st = sub.add_parser("status", allow_abbrev=False)
    st.add_argument("--purpose", required=True)
    st.add_argument("--project", default=None)
    st.add_argument("--json", action="store_true")

    cx = sub.add_parser("context", allow_abbrev=False)
    cx.add_argument("--purpose", required=True)
    cx.add_argument("--project", default=None)
    cx.add_argument("--full", action="store_true")

    rs = sub.add_parser("reset", allow_abbrev=False)
    rs.add_argument("--purpose", required=True)
    rs.add_argument("--authorized-by", required=True)
    rs.add_argument("--project", default=None)

    rc = sub.add_parser("recover", allow_abbrev=False)
    rc.add_argument("--purpose", required=True)
    rc.add_argument("--confirm-stopped", action="store_true")
    rc.add_argument("--pid-is-other-program", action="store_true")
    rc.add_argument("--project", default=None)

    a = ap.parse_args(argv)
    handler = {"call": cmd_call, "list": cmd_list, "status": cmd_status, "context": cmd_context,
               "reset": cmd_reset, "recover": cmd_recover}[a.command]
    try:
        return handler(a)
    except SessionError as exc:
        label = {USAGE: "USAGE", BUSY: "BUSY", NEEDS_RESOLUTION: "NEEDS RESOLUTION"}.get(exc.code, "FAILED")
        print(f"CLAUDE SESSION {label}: {exc}", file=sys.stderr)
        return exc.code


if __name__ == "__main__":
    sys.exit(main())
