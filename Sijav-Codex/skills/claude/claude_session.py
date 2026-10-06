#!/usr/bin/env python3
"""Sijav-Codex: one persistent external Claude conversation per purpose, for code and technical roast.

    call     --purpose NAME --mode code|technical [--scope TEXT] [--effort high]
             [--prompt-file FILE]            (else the prompt is read from stdin, bytes unchanged)
             [--rules-file FILE ... | --no-project-rules]
             [--allow-command "Bash(<command> *)" ...]           (code mode only)
             [--resume-unconfirmed new|existing] [--timeout SECONDS] [--project DIR]
             [--command-timeout SECONDS]     (code mode with --allow-command: each command's bound)
             [--accept-follow-ups]           (a managed call that `follow-up` can add messages to)
    follow-up --purpose NAME [--prompt-file FILE] [--wait SECONDS] [--project DIR]
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
The child environment drops ANTHROPIC_* and CLAUDE_CODE_USE_* provider settings, NODE_OPTIONS,
NODE_TLS_REJECT_UNAUTHORIZED and CLAUDE_CODE_RESUME_INTERRUPTED_TURN (which would make a resume
continue a stale interrupted turn instead of the new prompt), so the CLI keeps its own OAuth sign-in on verified TLS rather than an
inherited API key, base URL, provider, injected runtime code or disabled certificate checks.
CLAUDE_CODE_OAUTH_TOKEN, CLAUDE_CONFIG_DIR, proxy and CA-bundle variables are kept so ordinary
sign-in and networks work; the names (never values) of everything dropped or kept are recorded.
The helper also sets, replacing any inherited value, and records with their values in command.json
(set_environment; none is a secret; replaced inherited names in replaced_inherited_environment):
  CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1  commands are foreground-only and finite: no run_in_background
                         and no automatic backgrounding (docs: code.claude.com/docs/en/env-vars). A -p call
                         cannot await a background shell: TaskOutput is deprecated and 2.1.288 does not
                         offer it, and a background shell ends about 5 s after the final result once stdin
                         closes (docs: headless). TaskOutput and TaskStop are denied in both modes.
  CLAUDE_CODE_TMPDIR=<project>/.codex/claude-sessions/<purpose>/cli-tmp  the CLI's internal temporary files
                         (it appends claude, or claude-UID on Unix) are configured to go to the project,
                         so a command's overflow output saved there could be opened with Read (not yet
                         exercised live); no read outside the project is granted and the secret-file
                         denies still apply. The folder is not cleaned between runs.
  BASH_DEFAULT_TIMEOUT_MS = BASH_MAX_TIMEOUT_MS = --command-timeout x 1000 (code mode with a command rule
                         only): configured default and ceiling. --command-timeout must be at least 1
                         and below --timeout; the default is the smaller of 600 s and half of --timeout.
                         An omitted tool timeout uses that default; a smaller explicit request stops
                         earlier. The actual offered schema/maximum and model compliance are unproved.
                         Separate guidance precedes the exact original prompt suffix and has one
                         command_guidance_sha256; no guidance is sent for no-command calls or repeated
                         in managed follow-ups. The whole-call deadline starts at launch, and remaining
                         lifetime is unknown. This adds no command authority or background workaround.
                         A command bound is refused (exit 2, nothing created) if no command is offered.
Background work is a breach of that contract, recognized only from structured events that earlier
Claude Code 2.1.288 runs made with background tasks enabled: a system/task_started with
is_backgrounded true, or a tool result whose tool_use_result names a backgroundTaskId. (With
background disabled, the live 2.1.288 runs emitted no task events at all.) A run with either is
`incomplete` (exit 1, kind "background breach") even if the CLI reported success and exited 0; a
managed call's inbox closes at once and no answer from a result after the breach counts as answered.
A run whose output had not ended DRAIN_SECONDS (10 s) after the CLI exited is `incomplete` too (exit
1, kind "output not settled") even with a success and exit 0; any other failure gets that appended
("also: ..."). Why the output had not ended is not determined (a writer outside the owned tree, or the
reader still processing). The reply is kept in reply.md, not printed; a delivered turn still confirms
the session; nothing is retried. result.json output_settlement records {ended, waited_seconds,
job_active_then} (the Job Object's running count then, or null).
A run_in_background request alone is only recorded (commands.background_requests): with background
disabled 2.1.288 rejects the option ("An unexpected parameter run_in_background was provided") and the
command does not run. Any task event is recorded in result.json commands.tasks. A managed follow-up is
read at the next tool boundary, so during a command it waits until that command's actual end
plus a step, subject to the whole-call deadline; a configured allowance is not proof of the tool's
actual maximum. 2.1.288 acknowledged it (its replay) only at that boundary.

Permissions follow Claude Code's rule syntax (https://code.claude.com/docs/en/permissions): reads
inside the project (the working directory) need no approval and reads elsewhere would ask, so they
are denied; deny rules beat allow rules.
  code       tools Read, Glob, Grep, Write, Edit (+ Bash/PowerShell only when a scoped command rule
             names them). Pre-approved: Edit(./**) (all file writes inside the project) and each
             --allow-command rule, e.g. "Bash(python -m unittest *)". Denied: edits under .git,
             .claude, .codex, .githooks and .husky; NotebookEdit; TaskOutput and TaskStop; unscoped
             command tools. Commands run in the foreground until they end or reach --command-timeout.
  technical  tools Read, Glob, Grep and WebFetch only. Pre-approved: WebFetch. Denied: Write, Edit,
             NotebookEdit, Bash, PowerShell, TaskOutput, TaskStop.
  Both deny reads of common secrets (.env, .env.*, *.pem, *.key, ~/.ssh, ~/.aws, ~/.claude, ~/.codex
  and similar). Rules bind Claude's tools and the file commands Claude Code recognizes; a program a
  granted command runs (a test suite) is not confined by them.

The CLI's init must come before anything it does, and every init must report: model
claude-opus-5-5, exactly no tool beyond those offered, no MCP server, the normal permission mode,
apiKeySource "none" (its own sign-in), the expected session id and the project as cwd. Before the init
only two things are accepted: on a resume (attempt resume or unconfirmed-existing) up to 16
system/task_notification events of the asked session, which the CLI replays for background tasks an
interrupted turn left (recorded in result.json pre_init_events, never sent on or acted on); and in a
managed call, the CLI's replay of a message the helper itself wrote, and command_lifecycle events
{type, command_uuid, state, uuid, session_id} of the asked session whose command_uuid is the call's
first message (the only one written before the init), "queued" then "started", each once. That event
type is not documented: it is accepted only in the shape the October 3 app run showed (both events
named that run's first message uuid). "started" shows only that the CLI began processing the message;
if such a run then fails, the record says whether the message reached the transcript is unknown.
Anything else -- an assistant, user, result, hook or plugin event, another lifecycle state, key,
session or command, a lifecycle event in a text-input call, a notification of another or no session
or on a first attempt, more than 16 lines that are not JSON, a line over 64 KiB, more than 19 events
-- stops the CLI there, before it acts. A
run whose final result is a success that delivered no turn (no assistant message, num_turns 0) is a
failure. Exactly one shape is a prelude, and at most one per run: in a run whose resume sent a
task_notification before the init, the first result, a success with is_error false and integer
num_turns 0, before any main-thread work (a real interrupted resume printed exactly that, then worked
and answered). It is kept in result.json prelude_results, never a turn or an answer. Every other result
counts: it answers (positive num_turns, or a main-thread assistant event since the previous result) or
it is terminal (an error, or a zero-turn result after work or an answer). A terminal result closes a
managed call's input at once, fails the call if it is an error, and marks the messages acknowledged
before it "ended by" that result, not answered. Error paths are tested offline only. A reply from another
model is kept in the record but not used. Tool uses the CLI reports as denied (permission_denials) are
printed as a note and recorded with the result, so a review that could not read what it needed does
not pass silently.

Follow-ups (--accept-follow-ups, opt-in): the CLI runs with --input-format stream-json and
--replay-user-messages instead of text input, and everything else unchanged. The prompt goes as one
user message (stdin.jsonl keeps every line written) and stdin stays open. `follow-up` never takes
the purpose lock and never launches the CLI: it needs a running managed call whose helper and CLI are
alive and whose init was validated, puts the message in runs/<run>/inbox/ under inbox.lock, and waits
for the helper. The helper writes it to stdin as the next user message. Per the CLI docs the message
is read after the current tool calls finish (within the turn) or starts the next turn: it queues and
steers, it does not interrupt. Three levels are kept apart: submitted (written to stdin), acknowledged
(the CLI's replay seen), answered (an answering result came after the acknowledgment; inferred). A follow-up is taken only
once its receipt is saved; a file that is not valid UTF-8 text is rejected. Lifecycle events
after the init are recorded per command in result.json as extra evidence only. When every
message is answered the helper closes the inbox and stdin; a follow-up that arrives after that is
refused (exit 5) and needs a normal call. If no acknowledgment comes within 60 s after the last answering result the
inbox closes too and the record says so. A stopped call records submitted, unanswered messages as
"unknown: call stopped". Exit codes of follow-up (the helper process's own): 0 submitted, 1 rejected
or not confirmed, 2 usage, 3 switched off, 5 no live managed call to take it (finished, not managed,
not yet initialized, or its helper or CLI gone). `powershell -Command` and `pwsh -Command` report 1 for
any native exit other than 0 or 1, so every follow-up also ends with an outcome record on stdout --
`follow-up outcome: <outcome> (exit <code>, sent <yes|no|unknown>)`, or one JSON object with --json --
that names the outcome (for example not_running for a finished call, not_initialized during start-up,
rejected or failed for a protocol or I/O failure). Orchestration should run Python directly, or end a
PowerShell command with `; exit $LASTEXITCODE`, to keep the code itself. sent is "unknown" once the
message is in the inbox until the helper's receipts say more; argparse's own argument errors (exit 2)
come before any outcome record.

The first call of a conversation must carry the project's law/rules (--rules-file, each sent with
its path before the prompt) or say --no-project-rules, because safe mode loads no CLAUDE.md; so must
every call until a call has actually delivered a turn. A new purpose also needs --scope.

State: <project>/.codex/claude-sessions/<purpose>/state.json; runs/<run>/ holds, created once per
call, brief.json, prompt.txt (as sent), command.json, raw stdout.jsonl and stderr.txt from launch,
result.json and reply.md (a managed call adds stdin.jsonl, inbox/, acks/ and replies/<turn>.md, and
reply.md is its last turn); generations/ keeps retired conversations. The project is --project, else
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
Measured limit: with the CLI run by a venv made from a Microsoft Store Python, the Job's lifetime count
was 1 (the CLI's child interpreter never joined it), so what that child starts is not ended with the
call; if such a process keeps the output open, the run reports its output as not settled.

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
import queue
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
# Commands are foreground-only and finite (CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1): a -p call cannot await a
# background shell (TaskOutput is deprecated and Claude Code 2.1.288 does not offer it; a background shell
# ends about 5 s after the final result once stdin closes), so the task tools are removed in both modes.
TASK_TOOLS = ("TaskOutput", "TaskStop")
DEFAULT_COMMAND_TIMEOUT = 600  # seconds; never more than half the call's --timeout by default
# Set in the CLI's environment, replacing any inherited value (none is a secret; all are recorded).
CONTROLLED_ENV = ("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS", "CLAUDE_CODE_TMPDIR", "BASH_DEFAULT_TIMEOUT_MS",
                  "BASH_MAX_TIMEOUT_MS")
CLI_TMP = "cli-tmp"  # <purpose>/cli-tmp: the CLI's internal temp folder (it appends claude, or claude-UID on Unix)
WRITE_OR_RUN = ("Write", "Edit", "NotebookEdit", "Bash", "PowerShell")
PROJECT_EDIT = "Edit(./**)"
PROTECTED_EDITS = tuple(f"Edit({p})" for p in (".git/**", ".claude/**", ".codex/**", ".githooks/**", ".husky/**"))
SECRET_READS = tuple(f"Read({p})" for p in (
    ".env", ".env.*", "*.pem", "*.key", "~/.ssh/**", "~/.aws/**", "~/.azure/**", "~/.gnupg/**", "~/.kube/**",
    "~/.docker/config.json", "~/.claude/**", "~/.codex/**", "~/.config/gh/**", "~/.git-credentials",
    "~/.netrc", "~/.npmrc", "~/.pypirc"))
PURPOSE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
INBOX_NAME_RE = re.compile(r"^[0-9]{4,}-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.json$")
ALLOW_RE = re.compile(r"^(Bash|PowerShell)\(([^()\r\n]+)\)$")
BATCH_UNSAFE = set('"%!^&|<>\r\n')
OFF_VALUES = {"off", "0", "false", "no"}
# Provider settings a parent process may carry that would move billing or routing away from the
# CLI's own sign-in, and the switch that would resume a stale interrupted turn instead of the prompt.
# CLAUDE_CODE_OAUTH_TOKEN is the CLI's own OAuth token and is kept.
DROP_ENV = re.compile(r"^(ANTHROPIC_\w*|CLAUDE_CODE_USE_\w+|CLAUDE_CODE_RESUME_INTERRUPTED_TURN|"
                      r"AWS_BEARER_TOKEN_BEDROCK|CLAUDE_CODE_SKIP_\w+_AUTH|"
                      r"NODE_OPTIONS|NODE_TLS_REJECT_UNAUTHORIZED)$", re.I)
# Kept so ordinary sign-in and networks work, and recorded by name so a proxy is never unrecorded.
NOTED_ENV = re.compile(r"^(CLAUDE_CODE_OAUTH_TOKEN|CLAUDE_CONFIG_DIR|HTTPS?_PROXY|ALL_PROXY|NO_PROXY|"
                       r"NODE_EXTRA_CA_CERTS|SSL_CERT_FILE|SSL_CERT_DIR|REQUESTS_CA_BUNDLE)$", re.I)
LOCK_WAIT = 2.0
DEFAULT_TIMEOUT = 3600
STATE_WRITE_SECONDS = 5.0
DRAIN_SECONDS = 10.0
# Whether this Python can see a child exit without reaping it (POSIX waitid with WNOWAIT); see ProcessTree.
OBSERVE_EXIT = all(hasattr(os, n) for n in ("waitid", "P_PID", "WEXITED", "WNOWAIT", "WNOHANG"))
READ_CHUNK = 65536
MAX_LINE = 32 * 1024 * 1024
CLASSIFIER = 3  # result.json "classifier": 2 accepted a resume's task notifications and own replays before init;
# 3 also accepts a managed call's command_lifecycle queued/started of its first message
LIFECYCLE_KEYS = {"type", "command_uuid", "state", "uuid", "session_id"}  # exactly these (observed, not documented)
LIFECYCLE_STATES = ("queued", "started")  # the only order accepted before init, each once
PRE_INIT_ATTEMPTS = ("resume", "unconfirmed-existing")  # only a resumed conversation replays notifications
PRE_INIT_MAX = 16  # task notifications, and lines that are not JSON, accepted before the init
PRE_INIT_LINE = 64 * 1024  # bytes per line before the init, except the init and replays of the helper's own message
PRE_INIT_EVENTS = len(LIFECYCLE_STATES) + PRE_INIT_MAX + 1  # lifecycle events, notifications, the one replay
INIT_FIELDS = ("model", "tools", "permissionMode", "mcp_servers", "claude_code_version", "cwd", "apiKeySource",
               "session_id")
MAX_FOLLOW_UP = 1024 * 1024
INBOX_POLL = 0.2
INBOX_LOCK_WAIT = 5.0
ACK_WAIT = 60.0  # after a result, how long an unacknowledged message keeps the inbox open
FOLLOW_UP_WAIT = 30
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

    def __init__(self, message: str, code: int = FAILED, outcome: str | None = None):
        super().__init__(message)
        self.code = code
        self.outcome = outcome  # a stable token for `follow-up`'s outcome line, when one applies


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


@contextlib.contextmanager
def inbox_lock(run_dir: Path, wait: float = INBOX_LOCK_WAIT):
    """The short lock of a managed call's inbox: `follow-up` adds a message under it, the calling
    helper takes or rejects messages and closes the inbox under it."""
    path = run_dir / "inbox.lock"
    try:
        handle = open(path, "a+b")
    except OSError as exc:
        raise SessionError(f"cannot open the inbox lock {path}: {exc}") from exc
    try:
        deadline = time.monotonic() + wait
        while True:
            try:
                _lock(handle)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise SessionError(f"the inbox lock {path} stayed held for {wait} s") from None
                time.sleep(0.02)
        try:
            yield
        except BaseException:  # keep the cause in flight: if unlocking fails too, closing the handle releases it
            with contextlib.suppress(OSError):
                _unlock(handle)
            raise
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


def helper_sha256() -> str | None:
    """The SHA-256 of this helper's source file as read when the run launches. It names the code that ran
    only for a file left unchanged while the helper ran; it is not a hash of the bytes Python loaded."""
    try:
        return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    except OSError:
        return None


def child_env(settings: dict | None = None) -> tuple[dict, list[str], list[str], list[str]]:
    """(the environment for the CLI, names dropped, names of noted variables kept, names of inherited
    controlled variables replaced). Every CONTROLLED_ENV variable is removed and only `settings` sets it,
    so a parent cannot re-enable background work, move the CLI's temp folder or change the bounds."""
    env = dict(os.environ)
    dropped = sorted(k for k in env if DROP_ENV.match(k) and k.upper() != "CLAUDE_CODE_OAUTH_TOKEN")
    for k in dropped:
        del env[k]
    overridden = sorted(k for k in env if k.upper() in CONTROLLED_ENV)
    for k in overridden:
        del env[k]
    env.update(settings or {})
    kept = sorted(k for k in env if NOTED_ENV.match(k))
    return env, dropped, kept, overridden


def command_timeout(mode: str, allow: list[str], timeout: int, explicit: int | None) -> tuple[int | None, str]:
    """(seconds each command may run, where that came from). Only a call that offers a command tool has one:
    --command-timeout when given (at least 1 and below --timeout), else the smaller of 600 s and half of
    --timeout. Anything else is refused before anything is created or launched."""
    if not (mode == "code" and allow):
        if explicit is not None:
            raise SessionError("--command-timeout bounds command tools, and this call offers none (it needs code"
                               " mode and --allow-command). Nothing was sent.", USAGE)
        return None, "no command tools"
    if explicit is not None:
        if not 1 <= explicit < timeout:
            raise SessionError(f"--command-timeout {explicit} must be at least 1 and below --timeout {timeout},"
                               " so the call can still answer after a command ends. Nothing was sent.", USAGE)
        return explicit, "--command-timeout"
    seconds = min(DEFAULT_COMMAND_TIMEOUT, timeout // 2)
    if seconds < 1:
        raise SessionError(f"--timeout {timeout} leaves no room to bound a command: give a --timeout of at least"
                           " 2 s. Nothing was sent.", USAGE)
    return seconds, f"default: the smaller of {DEFAULT_COMMAND_TIMEOUT} s and half of --timeout"


def cli_settings(purpose_d: Path, seconds: int | None) -> dict:
    """The CLI environment settings this helper decides. None is a secret; all are recorded in command.json."""
    out = {"CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1", "CLAUDE_CODE_TMPDIR": str(purpose_d / CLI_TMP)}
    if seconds is not None:
        out.update(BASH_DEFAULT_TIMEOUT_MS=str(seconds * 1000), BASH_MAX_TIMEOUT_MS=str(seconds * 1000))
    return out


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
        return list(TECHNICAL_TOOLS), ["WebFetch"], [*WRITE_OR_RUN, *TASK_TOOLS, *SECRET_READS]
    run_tools = sorted({rule.split("(", 1)[0] for rule in allow})
    denied = ["NotebookEdit", *TASK_TOOLS,
              *[t for t in COMMAND_TOOLS if t not in run_tools], *PROTECTED_EDITS, *SECRET_READS]
    return list(CODE_TOOLS) + run_tools, [PROJECT_EDIT, *allow], denied


def build_argv(prefix: list[str], mode: str, effort: str, allow: list[str],
               session_args: list[str], follow_ups: bool = False) -> list[str]:
    """The CLI's arguments. A managed call (follow_ups) differs only in its input: stream-json user
    messages, each replayed on stdout for acknowledgment."""
    tools, allowed, denied = mode_tools(mode, allow)
    stdin_args = ["--input-format", "stream-json"] if follow_ups else ["--input-format", "text"]
    return [*prefix, "-p", "--model", MODEL, "--effort", effort, "--safe-mode", "--restricted",
            "--disable-slash-commands", "--strict-mcp-config",
            *stdin_args, "--output-format", "stream-json", "--verbose", *(["--replay-user-messages"] * follow_ups),
            "--permission-mode", "manual", "--permission-prompts", "none", *session_args,
            "--tools", ",".join(tools), "--allowedTools", *allowed, "--disallowedTools", *denied]


def command_guidance(seconds: int | None, source: str, timeout: int) -> bytes:
    """The configured foreground allowance, not a claim about the offered tool's schema."""
    if seconds is None:
        return b""
    return (
        "<command-guidance>\n"
        f"Configured foreground command default and ceiling: {seconds} s ({seconds * 1000} ms), from {source}.\n"
        "BASH_DEFAULT_TIMEOUT_MS and BASH_MAX_TIMEOUT_MS are set to that value for this call.\n"
        "Omitting the tool's timeout parameter uses the configured default; a smaller explicit timeout stops "
        "that command earlier. The tool's actual offered limit has not been verified.\n"
        f"The whole call has a {timeout} s deadline from launch, not a fresh allowance for each command. "
        "A command started late can be cut short; the remaining call lifetime is not known here.\n"
        "Background and automatic background tasks are disabled. Use only the already authorized scoped "
        "commands, in the foreground. This section grants no new command authority.\n"
        "If the offered tool cannot accept a needed duration, report that before starting; do not use a "
        "background task, hidden runner or other workaround.\n"
        "</command-guidance>\n\n"
    ).encode("utf-8")


def compose(rules: list[tuple[str, bytes]], prompt: bytes, guidance: bytes = b"") -> bytes:
    if not rules:
        return guidance + prompt
    parts = [RULES_PREAMBLE.encode("utf-8")]
    for path, data in rules:
        tail = b"" if data.endswith(b"\n") else b"\n"
        parts.append(f'<project-rules source="{path}">\n'.encode("utf-8") + data + tail
                     + b"</project-rules>\n\n")
    return b"".join(parts) + guidance + prompt


# ------------------------------------------------------------- the stream


class Stream:
    """What the stream-json output reports, gathered line by line. Thinking text and signatures are
    never copied out of the raw log.

    Nothing the CLI does may come before a valid init. Accepted before it: on a resume (PRE_INIT_ATTEMPTS)
    up to PRE_INIT_MAX system/task_notification events of the asked session with a task_id and status
    (recorded, never acted on); and in a managed call the CLI's replay of a message the helper wrote, and
    command_lifecycle events of exactly LIFECYCLE_KEYS for the call's first message (command_uuid equal to
    the uuid the helper gave it, the only message written before the init), "queued" then "started", each
    once. command_lifecycle is not a documented type: its acceptance rests on the observed October 3 app
    run, so anything beyond that shape -- another state, key, session or command -- is refused. At most
    PRE_INIT_EVENTS events in all. hook_* and plugin_install events, which the docs also allow before the
    init, are refused: under --safe-mode neither should appear, so one that does is not trusted. Every
    init is checked. After the init, lifecycle events are only recorded (an unknown command_uuid is
    counted); acknowledgment still comes only from the documented replay."""

    def __init__(self, mode: str, offered: list[str], expect_id: str, root: Path, attempt: str = "first",
                 follow_ups: bool = False):
        self.mode, self.offered, self.expect_id, self.root = mode, offered, expect_id, root
        self.attempt, self.follow_ups = attempt, follow_ups
        self.first: str | None = None  # the uuid of the call's first message, when known
        self.lifecycle: dict[str, list[dict]] = {}  # command_uuid -> its states and when they were seen
        self.lifecycle_ids: set[str] = set()
        self.tasks: dict[str, dict] = {}  # task_id -> what task_started/task_updated/task_notification reported
        self.breach: list[dict] = []  # structured evidence that a background task started (background is disabled)
        self.breach_results: int | None = None  # how many results came before the first breach evidence
        self.background_requests = 0  # main-thread tool uses asking for run_in_background (a request, not a start)
        self.unmatched_lifecycle = 0
        self.first_started = False
        self.session_ids: list[str] = []
        self.bad_ids: list[str] = []
        self.init: dict | None = None
        self.init_extra: dict = {}
        self.inits = 0
        self.pre_init: list[dict] = []
        self.events = 0
        self.last_event = time.monotonic()
        self.assistant_models: list[str] = []
        self.assistant_seen = False
        self.result: dict | None = None
        self.results: list[dict] = []  # every result except the initial prelude: answers and terminal results
        self.answering: list[bool] = []  # per result: positive num_turns or main-thread work since the previous one
        self.ended_by: str | None = None  # the first terminal result: an error, or a turn that ended with no work
        self.preludes: list[dict] = []  # at most one: the initial non-error zero-turn success after a notification
        self.working = False  # a main-thread assistant event arrived since the previous result
        self.answered_at: float | None = None  # monotonic time of the last answering result
        self.sent: dict[str, str] = {}  # uuid -> exact text of each user message the helper wrote
        self.acks: dict[str, dict] = {}  # uuid -> when the CLI replayed it, and how many results came before
        self.unmatched_replays = 0
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
            ev = None
        if not isinstance(ev, dict):
            self.unparsed += 1
            if self.init is None and (self.unparsed > PRE_INIT_MAX or len(raw) > PRE_INIT_LINE):
                self.refuse(f"before its init the CLI printed more than {PRE_INIT_MAX} lines that are not JSON"
                            f" events, or one over {PRE_INIT_LINE} bytes")
            return
        self.events += 1
        self.last_event = time.monotonic()
        sid = ev.get("session_id")
        if isinstance(sid, str) and sid:
            if not UUID_RE.match(sid):
                if sid not in self.bad_ids:
                    self.bad_ids.append(sid)
            elif sid not in self.session_ids:
                self.session_ids.append(sid)
        kind, subtype = ev.get("type"), ev.get("subtype")
        if kind == "system" and subtype == "init":
            self.inits += 1
            seen = {k: ev.get(k) for k in INIT_FIELDS}
            if self.init is None:
                self.init = seen
                self.init_extra = {"plugins": [p.get("name") for p in ev.get("plugins") or [] if isinstance(p, dict)],
                                   "skills": len(ev.get("skills") or []), "agents": ev.get("agents"),
                                   "output_style": ev.get("output_style"), "capabilities": ev.get("capabilities")}
            problems = self.init_problems(seen)
            if problems:
                self.refuse(("" if self.inits == 1 else f"init event {self.inits}: ") + "; ".join(problems))
        elif self.init is None:
            self.before_init(ev, kind, subtype, len(raw))
        elif kind == "command_lifecycle":
            self.note_lifecycle(ev)
        elif kind == "system" and subtype in ("task_started", "task_updated", "task_notification"):
            self.note_task(ev, subtype)
        elif kind == "assistant" and ev.get("parent_tool_use_id") is None:
            self.assistant_seen = self.working = True
            message = ev.get("message") if isinstance(ev.get("message"), dict) else {}
            for c in message.get("content") or []:
                if (isinstance(c, dict) and c.get("type") == "tool_use" and isinstance(c.get("input"), dict)
                        and c["input"].get("run_in_background") is True):
                    self.background_requests += 1  # a request only: 2.1.288 rejects it with background disabled
            model = message.get("model")
            if isinstance(model, str) and model not in self.assistant_models:
                self.assistant_models.append(model)
        elif kind == "user" and ev.get("isReplay") is True:
            self.replay(ev)
        elif kind == "user":
            data = ev.get("tool_use_result")
            if isinstance(data, dict) and isinstance(data.get("backgroundTaskId"), str) and data["backgroundTaskId"]:
                tool_use_id = next((c.get("tool_use_id") for c in ((ev.get("message") or {}).get("content") or [])
                                    if isinstance(c, dict) and c.get("type") == "tool_result"), None)
                self.note_breach("tool_use_result.backgroundTaskId", data["backgroundTaskId"], tool_use_id)
        elif kind == "result":
            self.result = ev
            turns = ev.get("num_turns")
            positive = isinstance(turns, int) and not isinstance(turns, bool) and turns > 0
            if (self.notified and not self.preludes and not self.working and not self.results
                    and ev.get("subtype") == "success" and ev.get("is_error") is False
                    and turns == 0 and not isinstance(turns, bool)):
                # the one shape seen live, once: after a resume's pre-init task notification, an initial
                # non-error zero-turn success before any work or answer
                self.preludes.append({k: ev.get(k) for k in ("subtype", "is_error", "num_turns", "terminal_reason",
                                                             "stop_reason")} | {"after_answers": len(self.results)})
            else:
                answering = self.working or positive
                self.results.append(ev)
                self.answering.append(answering)
                if answering and ev.get("subtype") == "success" and not ev.get("is_error"):
                    self.answered_at = time.monotonic()
                elif self.ended_by is None:  # terminal: an error, or a turn that ended with no work
                    self.ended_by = (f"the CLI ended turn {len(self.results)} with a {ev.get('subtype')!r} result"
                                     f" (is_error {ev.get('is_error')}, num_turns {turns!r}"
                                     + ("" if answering else ", no turn") + ")")
            self.working = False

    def note_task(self, ev: dict, subtype: str) -> None:
        """Record a task event after the init. With background tasks enabled, 2.1.288 reported each Bash
        command as system/task_started with task_id, tool_use_id and is_backgrounded (true for a background
        command); task_updated carries patch.status and task_notification a status. With background
        disabled, as these calls run, the live 2.1.288 runs emitted no task events."""
        tid = ev.get("task_id") if isinstance(ev.get("task_id"), str) else repr(ev.get("task_id"))
        task = self.tasks.setdefault(tid, {"task_id": tid, "tool_use_id": None, "is_backgrounded": None,
                                           "task_type": None, "started_at": None, "status": None})
        if subtype == "task_started":
            task.update(tool_use_id=ev.get("tool_use_id"), is_backgrounded=ev.get("is_backgrounded"),
                        task_type=ev.get("task_type"), started_at=now())
            if ev.get("is_backgrounded") is True:
                self.note_breach("task_started is_backgrounded", tid, ev.get("tool_use_id"))
        elif subtype == "task_updated":
            patch = ev.get("patch") if isinstance(ev.get("patch"), dict) else {}
            if "status" in patch:
                task["status"] = patch["status"]
        else:
            task["status"] = ev.get("status")

    def note_breach(self, evidence: str, task_id, tool_use_id) -> None:
        """Structured evidence that a background task started although background tasks are disabled."""
        if self.breach_results is None:
            self.breach_results = len(self.results)
        self.breach.append({"evidence": evidence, "task_id": task_id, "tool_use_id": tool_use_id, "at": now(),
                            "after_results": len(self.results)})

    @property
    def breached(self) -> str:
        """The cause when the CLI started a background task, else ''."""
        if not self.breach:
            return ""
        ids = sorted({b["task_id"] for b in self.breach if b["task_id"]})
        return (f"background commands are disabled for these calls (CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1), but the"
                f" CLI started {len(ids)} background task(s) {ids}; their work is not known to be complete and"
                " no answer after it is trusted")

    def note_lifecycle(self, ev: dict) -> None:
        """Record a command_lifecycle event (never acted on)."""
        cmd = ev.get("command_uuid")
        key = cmd if isinstance(cmd, str) else repr(cmd)
        if key not in self.sent:
            self.unmatched_lifecycle += 1
        self.lifecycle.setdefault(key, []).append({"state": ev.get("state"), "at": now()})
        if cmd == self.first_uuid and ev.get("state") == "started":
            self.first_started = True

    def before_init(self, ev: dict, kind, subtype, size: int) -> None:
        what = f"{kind!r}/{subtype!r}"
        if len(self.pre_init) >= PRE_INIT_EVENTS:
            return self.refuse(f"the CLI sent more than {PRE_INIT_EVENTS} events before its init")
        if kind == "user" and ev.get("isReplay") is True and self.follow_ups:
            if ev.get("session_id") != self.expect_id:
                return self.refuse(f"before its init the CLI replayed a user message of session"
                                   f" {ev.get('session_id')!r}, not {self.expect_id}")
            if not self.replay(ev):
                return self.refuse("before its init the CLI replayed a user message the helper never wrote")
            self.pre_init.append({"subtype": "user_replay", "uuid": ev.get("uuid")})
            return None
        if size > PRE_INIT_LINE:
            return self.refuse(f"a {size}-byte {what} event came before the CLI's init (at most {PRE_INIT_LINE}"
                               " bytes are accepted before it)")
        if kind == "command_lifecycle":
            return self.lifecycle_before_init(ev)
        if not (kind == "system" and subtype == "task_notification"):
            return self.refuse((f"the CLI's first event was {what}" if self.events == 1 else
                                f"the CLI sent {what} before its init") + ", not its init, so the model, tools and"
                               " permissions could not be checked before it acted")
        if self.attempt not in PRE_INIT_ATTEMPTS:
            return self.refuse(f"the CLI sent a task_notification before its init on a {self.attempt} attempt;"
                               " only a resumed conversation replays one")
        if ev.get("session_id") != self.expect_id:
            return self.refuse(f"a task_notification before the init names session {ev.get('session_id')!r},"
                               f" not {self.expect_id}")
        if not (isinstance(ev.get("task_id"), str) and isinstance(ev.get("status"), str)):
            return self.refuse("a task_notification before the init has no task_id or status string")
        if sum(e["subtype"] == "task_notification" for e in self.pre_init) >= PRE_INIT_MAX:
            return self.refuse(f"the CLI sent more than {PRE_INIT_MAX} task_notifications before its init")
        summary = ev.get("summary")
        self.pre_init.append({"subtype": "task_notification", "task_id": ev["task_id"], "status": ev["status"],
                              "summary": summary[:500] if isinstance(summary, str) else None})
        return None

    def lifecycle_before_init(self, ev: dict) -> None:
        """Accept a command_lifecycle event before the init only in the observed shape, bound by identity
        to the call's first message (decision A1 of the review)."""
        why = "a command_lifecycle event came before the CLI's init"
        if not self.follow_ups:
            return self.refuse(f"{why} in a text-input call, where none has been observed")
        if set(ev) != LIFECYCLE_KEYS:
            return self.refuse(f"{why} with keys {sorted(ev)}, not exactly {sorted(LIFECYCLE_KEYS)}")
        if ev["session_id"] != self.expect_id:
            return self.refuse(f"{why} naming session {ev['session_id']!r}, not {self.expect_id}")
        if not all(isinstance(ev[k], str) and UUID_RE.match(ev[k]) for k in ("uuid", "command_uuid")):
            return self.refuse(f"{why} whose uuid or command_uuid is not a UUID")
        if ev["uuid"] in self.lifecycle_ids:
            return self.refuse(f"{why} repeating event uuid {ev['uuid']}")
        if self.first_uuid is None or ev["command_uuid"] != self.first_uuid:
            return self.refuse(f"{why} for command {ev['command_uuid']}, not the call's first message"
                               f" {self.first_uuid}")
        seen = [e["state"] for e in self.pre_init if e["subtype"] == "command_lifecycle"]
        expected = LIFECYCLE_STATES[len(seen)] if len(seen) < len(LIFECYCLE_STATES) else None
        if ev["state"] != expected:
            return self.refuse(f"{why} with state {ev['state']!r} after {seen}; only"
                               f" {' then '.join(LIFECYCLE_STATES)}, each once, is accepted before it")
        self.lifecycle_ids.add(ev["uuid"])
        self.note_lifecycle(ev)
        self.pre_init.append({"subtype": "command_lifecycle", "state": ev["state"], "command_uuid": ev["command_uuid"],
                              "bound_by": "uuid"})
        return None

    def replay(self, ev: dict) -> bool:
        """Match the CLI's replay of a user message the helper wrote: by its uuid, else by its exact
        text. Returns whether it matched; the first match of a message is its acknowledgment."""
        u = ev.get("uuid")
        by = "uuid" if u in self.sent and u not in self.acks else None
        if by is None:
            message = ev.get("message") if isinstance(ev.get("message"), dict) else {}
            content = message.get("content")
            if (isinstance(content, list) and len(content) == 1 and isinstance(content[0], dict)
                    and content[0].get("type") == "text"):
                content = content[0].get("text")
            u = next((k for k, t in self.sent.items() if k not in self.acks and t == content), None)
            by = "text" if u else None
        if by is None:
            self.unmatched_replays += 1
            return False
        self.acks[u] = {"at": now(), "results_before": len(self.results), "matched_by": by}
        return True

    def refuse(self, why: str) -> None:
        if self.refused is None:
            self.refused = why

    @property
    def notified(self) -> bool:
        """Whether a task notification came before the init: the context in which a prelude was observed."""
        return any(e["subtype"] == "task_notification" for e in self.pre_init)

    @property
    def init_ok(self) -> bool:
        return self.init is not None and self.refused is None and not self.bad_ids

    @property
    def other_model(self) -> bool:
        return any(m != MODEL for m in self.assistant_models)

    def init_problems(self, i: dict) -> list[str]:
        out = []
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
        """Whether a turn reached the model in this run (the prompt is then in the conversation). A task
        notification, a replay, a zero-turn prelude or a zero-turn error never counts."""
        return self.assistant_seen or any(isinstance(r.get("num_turns"), int) and not isinstance(r.get("num_turns"), bool)
                                          and r["num_turns"] > 0 for r in self.results)

    @property
    def final_result(self) -> dict | None:
        """The result a run is judged by: in a text-input call its last result (so a final zero-turn
        success still fails as undelivered), in a managed call its last answering result (a terminal
        error is judged separately, as a failed turn)."""
        answers = [r for r, a in zip(self.results, self.answering) if a]
        return (answers or self.results)[-1] if self.follow_ups and self.results else self.result

    def turns(self) -> list[dict]:
        """Each result after the prelude as a turn: its reply, whether it answered (else it was terminal)
        and the follow-ups the CLI acknowledged before it (after the previous result)."""
        out = []
        for n, (r, answering) in enumerate(zip(self.results, self.answering), 1):
            after = [u for u, a in self.acks.items() if a["results_before"] == n - 1 and u != self.first_uuid]
            out.append({"turn": n, "reply": r.get("result") if isinstance(r.get("result"), str) else "",
                        "subtype": r.get("subtype"), "is_error": r.get("is_error"), "num_turns": r.get("num_turns"),
                        "answering": answering, "after_follow_ups": after})
        return out

    def ended(self, n: int) -> str:
        """How result n (1-based) ended the messages acknowledged before it: '' if it answered them. A result
        after a background breach answers nothing that can be trusted."""
        if self.breach_results is not None and n > self.breach_results:
            return f"ended by a background-command breach: turn {n}'s answer is not trusted (incomplete)"
        r, answering = self.results[n - 1], self.answering[n - 1]
        if answering and r.get("subtype") == "success" and not r.get("is_error"):
            return ""
        return (f"ended by a {r.get('subtype')!r} result (is_error {r.get('is_error')}, num_turns"
                f" {r.get('num_turns')!r}{'' if answering else ', no turn'}; turn {n})")

    @property
    def first_uuid(self) -> str | None:
        return self.first or next(iter(self.sent), None)


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
    result = stream.final_result or {}
    exit_code = proc.get("exit_code")
    reply = result.get("result") if isinstance(result.get("result"), str) else ""
    served = [m for m in [(stream.init or {}).get("model"), *stream.assistant_models] if m]
    wrong = sorted({m for m in served if m != MODEL})
    stderr_tail = tail_of(run_dir / "stderr.txt")
    bad_turn = next((r for r in stream.results if r.get("is_error") or r.get("subtype") != "success"), None)
    success = bool(result) and not result.get("is_error") and result.get("subtype") == "success"
    failed = result if bad_turn is None else bad_turn  # a managed call's earlier turn may have failed
    success = success and bad_turn is None
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
        text = failed.get("result") if isinstance(failed.get("result"), str) else ""
        cause = f"the CLI reported {failed.get('subtype')!r} (is_error {failed.get('is_error')}): {text[:2000]}"
    elif not stream.delivered:
        status = "failed"
        cause = (f"the CLI reported success after {result.get('num_turns')!r} turns with no assistant message:"
                 " no turn reached the model, so nothing it printed is used")
    elif exit_code not in (0, None) and not proc.get("timed_out"):
        status, cause = "failed", f"the CLI exited {exit_code} after reporting success"
    elif wrong:
        status, cause = "wrong_model", f"the CLI served {wrong}, not {MODEL}; the reply is kept but not used"
    elif not reply.strip():
        status, cause = "failed", "the CLI reported success with an empty reply"
    else:
        status, cause = "ok", ""
    incomplete_kind = ""
    if stream.breached:  # incomplete even with a success result and exit 0; never a silent waiting checkpoint
        if status == "ok":
            status, cause, incomplete_kind = "incomplete", stream.breached, "background breach"
        else:
            cause += f"; also: {stream.breached}"
    settlement = proc.get("output_settlement")
    if isinstance(settlement, dict) and settlement.get("ended") is False:  # never a silent ok either
        waited = settlement.get("waited_seconds")
        after = f"{waited:g} s after" if isinstance(waited, (int, float)) else "after"
        unsettled = (f"the CLI's output had not ended {after} the CLI exited (cause not determined: a writer"
                     " outside the owned tree, or the reader still processing)")
        if status == "ok":
            status, incomplete_kind = "incomplete", "output not settled"
            cause = unsettled + ("; the CLI's answer is kept in the run's reply.md, not printed"
                                 if reply.strip() else "")
        else:
            cause += f"; also: {unsettled}"
    if status != "ok" and stream.first_started and not stream.delivered:
        cause += ("; the first message was started by the CLI (command_lifecycle started); whether it is in the"
                  " transcript is unknown, so a later call may repeat that prompt")
    failure_text = f"{stderr_tail}\n{reply if not success else ''}\n{cause}"
    usage_models = sorted((result.get("modelUsage") or {}).keys()) if isinstance(result.get("modelUsage"), dict) else []
    denials = result.get("permission_denials")
    return {"status": status, "cause": cause,
            "kind": "" if status == "ok" else incomplete_kind if status == "incomplete" else kind_of(failure_text),
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
    when the helper dies). POSIX: the CLI's own process group.

    The order is wait, stop, reap, settled, close. On POSIX the CLI's exit is only observed by wait
    (os.waitid with WNOWAIT), so the CLI stays unreaped and its process group id stays reserved while
    stop signals the group; once reap has collected it, nothing is signalled again, since the id may
    then belong to an unrelated group. A Python without WNOWAIT reaps the CLI as soon as it is seen to
    exit, so what it left running is not signalled, and settled says None.

    reap is bounded. Once it times out nothing is stopped again; settled is still only a query. On
    Windows close still closes the owned kill-on-close Job, which ends any member still running (no
    process is signalled by its id); on POSIX nothing more is sent. If the CLI is reaped outside this
    tree (os.waitid raises ChildProcessError), the tree no longer owns it: its group is never signalled,
    its exit is unknown and settled says None. `stops` records what each stop, a timed-out reap, a lost
    child and the close observed; nothing interprets it. `problems` holds the causes a caller reports."""

    def __init__(self):
        self.job = None
        self.note = ""
        self.group = None
        self.owned = True
        self.reap_tried = False
        self.stops = []
        self.problems = []

    def popen(self, argv, cwd: Path, env: dict, stderr, stdin=subprocess.PIPE,
              stdout=subprocess.PIPE) -> subprocess.Popen:
        if os.name != "nt":
            self.note = "POSIX process group (a descendant that calls setsid escapes it)" + (
                "" if OBSERVE_EXIT else "; this Python cannot see the CLI exit without reaping it"
                " (no os.waitid WNOWAIT), so what the CLI leaves running when it exits is not stopped")
            proc = subprocess.Popen(argv, stdin=stdin, stdout=stdout, stderr=stderr, cwd=str(cwd), env=env,
                                    start_new_session=True)
            self.group = proc.pid
            return proc
        k32 = _kernel32()
        job = k32.CreateJobObjectW(None, None)
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not job or not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            self.note = f"no Job Object (error {ctypes.get_last_error()}); taskkill /T is used instead"
            if job:
                k32.CloseHandle(job)
            return subprocess.Popen(argv, stdin=stdin, stdout=stdout, stderr=stderr, cwd=str(cwd), env=env)
        try:
            proc = subprocess.Popen(argv, stdin=stdin, stdout=stdout, stderr=stderr,
                                    cwd=str(cwd), env=env, creationflags=0x4)  # CREATE_SUSPENDED
        except BaseException:
            k32.CloseHandle(job)
            raise
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
            raise OSError("the process could not be resumed after it was placed in its Job Object")
        return proc

    def wait(self, proc, timeout: float) -> bool:
        """Whether the CLI is no longer running as this tree's child, waiting at most `timeout` seconds.
        On POSIX with WNOWAIT the exit is only observed and the CLI is left for reap; otherwise this is
        Popen.wait, which reaps. True after a ChildProcessError means the child is no longer observable
        as ours (it was reaped elsewhere), not that it exited 0."""
        if os.name == "nt" or not OBSERVE_EXIT or proc.returncode is not None or not self.owned:
            try:
                proc.wait(timeout=timeout)
                return True
            except subprocess.TimeoutExpired:
                return False
        deadline = time.monotonic() + timeout
        while True:
            try:
                seen = os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOWAIT | os.WNOHANG)
            except ChildProcessError:
                self.owned, self.group = False, None
                cause = "the child was reaped outside this tree; its group is not signalled"
                self.stops.append({"phase": "wait", "at": now(), "lost": cause})
                self.problems.append(cause)
                return True
            if seen is not None:
                return True
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            time.sleep(min(0.05, left))

    def stop(self, proc, phase: str = "exit") -> None:
        """Stop the CLI and every descendant still in the tree, and record what was observed. The CLI is
        not reaped here. On Windows the Job Object's own member list is recorded just before it is
        terminated (members_before); nothing reads it."""
        at = now()
        if self.reap_tried:
            self.stops.append({"phase": phase, "at": at, "skipped": "a reap timed out; nothing is stopped again"})
            return
        if os.name == "nt":
            if self.job:
                members = self.members()
                ok = bool(_kernel32().TerminateJobObject(self.job, 1))
                seen = {"phase": phase, "at": at, "members_before": members, "terminate_ok": ok}
                if not ok:
                    seen["last_error"] = ctypes.get_last_error()
                seen.update(active_after=self.active(), active_at=now())
                self.stops.append(seen)
            elif proc.poll() is None:
                with contextlib.suppress(OSError, subprocess.SubprocessError):
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, timeout=30)
                self.stops.append({"phase": phase, "at": at, "fallback": "taskkill /T"})
            else:
                self.stops.append({"phase": phase, "at": at, "fallback": "no Job Object; the CLI had exited"})
            if proc.poll() is None:
                with contextlib.suppress(OSError):
                    proc.kill()
            return
        if not self.owned:
            self.stops.append({"phase": phase, "at": at, "skipped": "the child was reaped outside this tree"})
            return
        if proc.returncode is not None:
            self.stops.append({"phase": phase, "at": at, "skipped": "reaped: its group id is no longer ours to signal"})
            return
        try:
            os.killpg(self.group, signal.SIGKILL)
            self.stops.append({"phase": phase, "at": at, "killpg": "sent"})
        except OSError as exc:
            self.stops.append({"phase": phase, "at": at, "killpg": f"{type(exc).__name__}: {exc}"})
            with contextlib.suppress(OSError):
                proc.kill()

    def reap(self, proc, timeout: float = 10.0):
        """Collect the CLI's exit status, waiting at most `timeout` seconds (after stop, so this is
        normally at once). None when it timed out (and nothing is stopped again) or when the child was
        reaped outside this tree, whose exit is unknown."""
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.reap_tried = True
            self.stops.append({"phase": "reap", "at": now(), "timed_out": True})
            self.problems.append(f"reap timed out after {timeout:g} s; nothing is stopped again")
            return None
        return code if self.owned else None

    def active(self):
        """How many processes of the Job Object are running, or None without one (or when Windows
        does not say). Only while the Job Object is open."""
        if os.name != "nt" or not self.job:
            return None
        info = _JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
        if not _kernel32().QueryInformationJobObject(self.job, 1, ctypes.byref(info), ctypes.sizeof(info), None):
            return None
        return info.ActiveProcesses

    def total_processes(self):
        """How many processes were ever associated with the Job Object, ended ones included (Windows
        aggregates a nested job's accounting into its parent's): the same accounting query as active().
        A count names no process. None without one; a failed call or an exception is recorded as unknown,
        never as a count. Only while the Job Object is open; never raises."""
        if os.name != "nt" or not self.job:
            return None
        try:
            info = _JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
            if not _kernel32().QueryInformationJobObject(self.job, 1, ctypes.byref(info), ctypes.sizeof(info), None):
                return f"unknown: error {ctypes.get_last_error()}"
            return info.TotalProcesses
        except Exception as exc:
            return f"unknown: {type(exc).__name__}: {exc}"

    def members(self):
        """The Job Object's process ids, asked on its own handle only (JobObjectBasicProcessIdList, room
        for 64): {assigned, listed, ids, complete}, or a string saying why they are unknown, never an
        empty list for a failed call. None without one. Observation only: no process is opened or
        signalled, and an id says only that the Job counted that process then. Never raises."""
        if os.name != "nt" or not self.job:
            return None
        try:
            info = _JOBOBJECT_BASIC_PROCESS_ID_LIST()
            if not _kernel32().QueryInformationJobObject(self.job, 3, ctypes.byref(info), ctypes.sizeof(info), None):
                error = ctypes.get_last_error()
                return (f"incomplete: error {error} (ERROR_MORE_DATA); not read" if error == 234
                        else f"unknown: error {error}")
            listed = min(info.NumberOfProcessIdsInList, 64)
            return {"assigned": info.NumberOfAssignedProcesses, "listed": info.NumberOfProcessIdsInList,
                    "ids": list(info.ProcessIdList[:listed]),
                    "complete": info.NumberOfProcessIdsInList == info.NumberOfAssignedProcesses}
        except Exception as exc:
            return f"unknown: {type(exc).__name__}: {exc}"

    def settled(self, timeout: float = 10.0):
        """After stop and reap: True once nothing of the owned tree runs, False if something still
        answers when `timeout` ends, None when it cannot be known. Windows: the Job Object's own count,
        before close. POSIX: signal 0 to the group, which sends nothing; ProcessLookupError means the
        group is gone. A group that still answers may still have members, or (its id being free once
        it is empty) be another group, so that answer is only False. A descendant that called setsid
        or left the job is outside what this observes."""
        if os.name != "nt" and (not OBSERVE_EXIT or self.group is None):
            return None
        deadline = time.monotonic() + timeout
        while True:
            if os.name == "nt":
                count = self.active()
                if count is None:
                    return None
                if count == 0:
                    return True
            else:
                try:
                    os.killpg(self.group, 0)
                except ProcessLookupError:
                    return True
                except OSError:
                    return None
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)

    def close(self) -> None:
        if self.job:
            # The owned kill-on-close Job ends any member still running when this handle closes.
            self.stops.append({"phase": "close", "at": now(), "active_before_close": self.active(),
                               "total_processes": self.total_processes()})
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

    class _JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):  # JobObjectBasicAccountingInformation (1)
        _fields_ = [(n, ctypes.c_int64) for n in ("TotalUserTime", "TotalKernelTime", "ThisPeriodTotalUserTime",
                                                  "ThisPeriodTotalKernelTime")] + \
                   [(n, wintypes.DWORD) for n in ("TotalPageFaultCount", "TotalProcesses", "ActiveProcesses",
                                                  "TotalTerminatedProcesses")]

    class _JOBOBJECT_BASIC_PROCESS_ID_LIST(ctypes.Structure):  # JobObjectBasicProcessIdList (3), room for 64 ids
        _fields_ = [("NumberOfAssignedProcesses", wintypes.DWORD), ("NumberOfProcessIdsInList", wintypes.DWORD),
                    ("ProcessIdList", ctypes.c_size_t * 64)]  # ULONG_PTR, pointer-sized

    _K32 = None
    _NT = None

    def _kernel32():
        global _K32
        if _K32 is None:
            k = ctypes.WinDLL("kernel32", use_last_error=True)
            k.CreateJobObjectW.restype = wintypes.HANDLE
            k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
            k.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                                    ctypes.c_void_p]
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


# --------------------------------------------------------------- follow-ups


def user_line(u: str, text: str) -> bytes:
    """One documented stream-json input line: a user message carrying the exact text."""
    return (json.dumps({"type": "user", "message": {"role": "user", "content": text}, "parent_tool_use_id": None,
                        "uuid": u}, ensure_ascii=False) + "\n").encode("utf-8")


def sent_from_log(run_dir: Path) -> dict[str, str]:
    """uuid -> text of each user message a managed run wrote to the CLI (its stdin.jsonl)."""
    out = {}
    with contextlib.suppress(OSError):
        for line in (run_dir / "stdin.jsonl").read_bytes().splitlines():
            with contextlib.suppress(ValueError, AttributeError, TypeError):
                ev = json.loads(line)
                out[ev["uuid"]] = ev["message"]["content"]
    return out


class Inbox:
    """A managed call's messages. `follow-up` creates inbox/<seq>-<uuid>.json under inbox.lock unless
    inbox/closed exists. The helper, under the same lock, takes each new file (acks/<uuid>.taken.json); a
    writer thread writes it to the CLI's stdin, appends the line to stdin.jsonl and records
    acks/<uuid>.submitted.json; the CLI's replay is recorded as acks/<uuid>.acknowledged.json. A file the
    helper will not send gets acks/<uuid>.rejected.json. Each of these files is written once."""

    def __init__(self, run_dir: Path, stream: Stream):
        self.run_dir, self.stream = run_dir, stream
        self.dir, self.acks = run_dir / "inbox", run_dir / "acks"
        self.queue: queue.Queue = queue.Queue()
        self.order: list[str] = []  # every message taken, the prompt first
        self.unwritten: set[str] = set()
        self.recorded: set[str] = set()
        self.closed: str | None = None
        self.write_error: str | None = None
        self.writer: threading.Thread | None = None
        self.stdin_log = None

    def prepare(self, text: str) -> str:
        """Queue the call's prompt as its first message; returns its uuid."""
        self.dir.mkdir()
        self.acks.mkdir()
        self.stream.first = self.enqueue(str(uuid.uuid4()), text)
        return self.stream.first

    def enqueue(self, u: str, text: str) -> str:
        self.stream.sent[u] = text  # before the write, so its replay is recognized
        self.order.append(u)
        self.unwritten.add(u)
        self.queue.put((u, text))
        return u

    def start(self, stdin) -> None:
        self.stdin_log = open(self.run_dir / "stdin.jsonl", "xb")
        self.writer = threading.Thread(target=self.write_all, args=(stdin,), daemon=True)
        self.writer.start()

    def write_all(self, stdin) -> None:
        try:
            while True:
                item = self.queue.get()
                if item is None:
                    with contextlib.suppress(OSError, ValueError):
                        stdin.close()
                    return
                u, text = item
                try:
                    line = user_line(u, text)
                except (UnicodeError, ValueError, TypeError) as exc:  # nothing was written: reject this one only
                    self.reject(u, f"the message cannot be serialized as UTF-8 JSON ({exc}); nothing was sent", "no")
                    self.unwritten.discard(u)
                    continue
                if self.write_error is not None:  # never attempted: an earlier write failed
                    self.reject(u, f"not written: an earlier write to the CLI's stdin failed ({self.write_error})",
                                "no")
                    self.unwritten.discard(u)
                    continue
                started = now()  # before the write, so it never follows the CLI's replay of the line
                try:
                    stdin.write(line)
                    stdin.flush()
                except (OSError, ValueError) as exc:
                    self.write_error = f"{type(exc).__name__}: {exc}"
                    self.reject(u, f"the write to the CLI's stdin failed ({self.write_error}); whether the CLI read"
                                   " any of it is unknown", "unknown")
                else:
                    with contextlib.suppress(OSError, ValueError):
                        self.stdin_log.write(line)
                        self.stdin_log.flush()
                    self.mark(u, "submitted", {"submitted_at": started, "written_at": now(),
                                               "line_sha256": hashlib.sha256(line).hexdigest()})
                self.unwritten.discard(u)
        finally:
            with contextlib.suppress(OSError, ValueError):
                self.stdin_log.close()

    def mark(self, u: str, kind: str, data: dict) -> None:
        with contextlib.suppress(OSError):
            write_new(self.acks / f"{u}.{kind}.json", {"uuid": u, **data})

    def reject(self, u: str, reason: str, sent: str) -> None:
        """A rejection receipt. `sent` is explicit: "no" only when nothing of it reached the CLI, else
        "unknown"; readers never infer it from the reason text."""
        self.mark(u, "rejected", {"reason": reason, "sent": sent})

    def has(self, u: str, *kinds: str) -> bool:
        return any((self.acks / f"{u}.{k}.json").exists() for k in kinds)

    def record_acks(self) -> None:
        for u, a in list(self.stream.acks.items()):
            if u not in self.recorded:
                self.recorded.add(u)
                self.mark(u, "acknowledged", {"acknowledged_at": a["at"], "matched_by": a["matched_by"]})

    def poll(self) -> None:
        """Called by the helper's main loop: record acknowledgments, take new follow-ups once the init is
        validated, and close the inbox when the call can no longer take one or every message is answered."""
        self.record_acks()
        if self.closed:
            return
        try:
            with inbox_lock(self.run_dir, 0):
                forced = self.forced()
                if forced:
                    return self.close(forced)
                if not self.stream.init_ok:
                    return None
                for f in sorted(self.dir.glob("*.json")):
                    self.take(f)
                done = self.settled()
                if done:
                    self.close(done)
        except SessionError:  # `follow-up` holds the lock for a moment: the next poll
            pass
        return None

    def forced(self) -> str:
        if self.stream.refused:
            return f"the call was refused: {self.stream.refused}"
        if self.stream.bad_ids:
            return f"the CLI reported session ids that are not UUIDs: {self.stream.bad_ids}"
        if self.stream.other_model:
            return f"the CLI served {self.stream.assistant_models}, not only {MODEL}"
        if self.stream.ended_by:  # a streaming CLI stays alive after an error result: close its input now
            return self.stream.ended_by
        if self.stream.breached:  # nothing more is sent into a call whose work is no longer finite
            return self.stream.breached
        if self.write_error:
            return f"the CLI's stdin could not be written ({self.write_error})"
        return ""

    def settled(self) -> str:
        results, acks = len(self.stream.results), self.stream.acks
        if not results or self.unwritten:
            return ""
        unacked = [u for u in self.order if u not in acks]
        if not unacked and all(results > acks[u]["results_before"] for u in self.order):
            return "every message was answered"
        in_flight = [u for u in self.order if u in acks and results <= acks[u]["results_before"]]
        if (unacked and not in_flight and not self.stream.working
                and time.monotonic() - self.stream.answered_at >= ACK_WAIT):
            return (f"the CLI did not acknowledge {unacked} within {ACK_WAIT:.0f} s after its last answering result,"
                    " so stdin was closed")
        return ""

    def take(self, f: Path) -> None:
        m = INBOX_NAME_RE.match(f.name)
        u = m.group(1) if m else None
        if not u or u in self.stream.sent or self.has(u, "taken", "rejected", "withdrawn"):
            return
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        text = data.get("text") if isinstance(data, dict) else None
        if not isinstance(text, str) or data.get("uuid") != u:
            return self.reject(u, "the inbox file is not a follow-up the helper can read", "no")
        try:
            raw = text.encode("utf-8")  # strict: a lone surrogate from a tampered file is not text
        except UnicodeEncodeError:
            return self.reject(u, "the follow-up is not valid UTF-8 text", "no")
        if len(raw) > MAX_FOLLOW_UP or hashlib.sha256(raw).hexdigest() != data.get("sha256"):
            return self.reject(u, "the follow-up is over the size limit or does not match its sha256", "no")
        try:  # the receipt must exist before the message can be sent: `follow-up` withdraws only untaken files
            write_new(self.acks / f"{u}.taken.json", {"uuid": u, "taken_at": now(), "file": f.name})
        except OSError as exc:
            return self.reject(u, f"its taken receipt could not be saved ({exc}); nothing was sent", "no")
        self.enqueue(u, text)
        return None

    def close(self, reason: str) -> None:
        """Under the inbox lock (or once the helper alone is left): no further follow-up is taken, any
        file not yet taken is rejected with the reason, and stdin is closed after what was taken."""
        self.closed = reason
        with contextlib.suppress(OSError):
            write_new(self.dir / "closed", {"closed_at": now(), "reason": reason})
        for f in sorted(self.dir.glob("*.json")):
            m = INBOX_NAME_RE.match(f.name)
            if m and m.group(1) not in self.stream.sent and not self.has(m.group(1), "taken", "rejected", "withdrawn"):
                self.reject(m.group(1), f"the call stopped taking follow-ups ({reason}); nothing was sent. Send it"
                                        " as a normal call.", "no")
        self.queue.put(None)

    def finish(self, stopped: str | None) -> None:
        """After the CLI has ended (or in `recover`): close the inbox if it is still open."""
        self.record_acks()
        if not self.closed:
            try:
                with inbox_lock(self.run_dir):
                    self.close(f"the call stopped ({stopped})" if stopped else "the CLI ended")
            except SessionError:
                self.close(f"the call stopped ({stopped})" if stopped else "the CLI ended")
        if self.writer is not None:
            self.writer.join(2)


def follow_up_record(run_dir: Path, stream: Stream, stopped: str | None) -> dict:
    """What happened to each message of a managed call: submitted (written to stdin), acknowledged (the
    CLI replayed it), answered (a result came after the acknowledgment; inferred), or why not."""
    acks, results = run_dir / "acks", len(stream.results)

    def one(u: str, name: str | None = None) -> dict:
        got = {k: read_json(acks / f"{u}.{k}.json") for k in ("taken", "submitted", "rejected", "withdrawn")}
        ack = stream.acks.get(u)
        answered = ack["results_before"] + 1 if ack and results > ack["results_before"] else None
        if got["withdrawn"]:
            outcome = f"withdrawn: {got['withdrawn'].get('reason')}"
        elif got["rejected"]:
            outcome = f"rejected: {got['rejected'].get('reason')}"
        elif answered:
            outcome = stream.ended(answered) or f"answered (turn {answered})"
        elif got["submitted"]:
            outcome = (f"unknown: call stopped ({stopped})" if stopped else
                       "unknown: acknowledged but no result came after it" if ack else
                       "unknown: submitted but never acknowledged (no replay of it was seen)")
        elif got["taken"]:
            outcome = "unknown: taken but its write to stdin was not confirmed"
        else:
            outcome = "not sent"
        return {"uuid": u, "file": name, "submitted_at": (got["submitted"] or {}).get("submitted_at"),
                "acknowledged_at": (ack or {}).get("at"), "acknowledged_by": (ack or {}).get("matched_by"),
                "followed_by_result": answered, "outcome": outcome}

    messages = []
    for f in sorted((run_dir / "inbox").glob("*.json")):
        m = INBOX_NAME_RE.match(f.name)
        messages.append(one(m.group(1), f.name) if m else {"file": f.name, "outcome": "ignored: not a follow-up file"})
    return {"prompt_message": one(stream.first_uuid) if stream.first_uuid else None, "messages": messages,
            "inbox_closed": read_json(run_dir / "inbox" / "closed"), "unmatched_replays": stream.unmatched_replays}


def launch(argv: list[str], sent: bytes, cwd: Path, run_dir: Path, timeout: int, stream: Stream,
           on_start=None, inbox: Inbox | None = None, on_init=None, settings: dict | None = None) -> dict:
    """Run the CLI once. Raw stdout goes byte for byte into stdout.jsonl as it arrives (a reader
    thread, so a pipe a descendant keeps open cannot hang the call), stderr straight into stderr.txt.
    The tree is stopped on refusal, timeout, any exception (KeyboardInterrupt included) and, after the
    CLI exits, to end descendants it left behind. With an inbox (a managed call) stdin stays open for
    the inbox's messages; on_init is called once the init has been validated."""
    settings = {"CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1", **(settings or {})}
    env, dropped, kept, overridden = child_env(settings)
    tree = ProcessTree()
    info = {"argv": argv, "cwd": str(cwd), "started": now(), "helper_pid": os.getpid(), "claude_pid": None,
            "timeout_seconds": timeout, "stdin": "stdin.jsonl" if inbox else "prompt.txt",
            "helper_sha256": helper_sha256(),
            "dropped_environment": dropped, "kept_environment_noted": kept,
            "set_environment": settings, "replaced_inherited_environment": overridden}
    state = {"exit_code": None, "timed_out": False, "stdin_error": None, "reader_error": None,
             "drained": True, "output_settlement": None, "stopped_by_helper": None}
    with open(run_dir / "stdout.jsonl", "xb") as out, open(run_dir / "stderr.txt", "xb") as err:
        try:
            proc = tree.popen(argv, cwd, env, err)
        except OSError as exc:
            info["launch_error"] = f"{type(exc).__name__}: {exc}"
            write_new(run_dir / "command.json", info)
            if inbox is not None:
                inbox.finish("the CLI could not be started")
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
                            if stream.init is None:
                                stream.refuse(f"a line over {MAX_LINE} bytes came before the CLI's init")
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
            if inbox is None:
                feeder.start()
            else:
                inbox.start(proc.stdin)
            reader.start()
            deadline = time.monotonic() + timeout
            next_poll, init_told = 0.0, False
            while not tree.wait(proc, 0.1):
                if inbox is not None and time.monotonic() >= next_poll:
                    if on_init and not init_told and stream.init_ok:
                        init_told = True
                        on_init()
                    inbox.poll()
                    next_poll = time.monotonic() + INBOX_POLL
                if stop_now.is_set():
                    state["stopped_by_helper"] = "refused" if stream.refused else "reader error"
                    tree.stop(proc, state["stopped_by_helper"])
                elif time.monotonic() >= deadline:
                    state["timed_out"] = True
                    state["stopped_by_helper"] = "timeout"
                    tree.stop(proc, "timeout")
            tree.stop(proc)  # descendants the CLI left behind end with the call
            state["exit_code"] = tree.reap(proc)
            reader.join(DRAIN_SECONDS)
            state["drained"] = not reader.is_alive()
            # Observed while the Job Object is still open; why output has not ended is not determined here.
            state["output_settlement"] = {"ended": state["drained"], "waited_seconds": DRAIN_SECONDS,
                                          "job_active_then": tree.active()}
            if inbox is None:
                feeder.join(1)
            else:
                inbox.finish(state["stopped_by_helper"])
        except BaseException as exc:
            tree.stop(proc, "exception")
            with contextlib.suppress(NameError):
                reader.join(DRAIN_SECONDS)
            if inbox is not None:
                with contextlib.suppress(Exception):
                    inbox.finish(f"the helper was interrupted: {type(exc).__name__}")
            raise
        finally:
            # Not reaped (an exception): stopped, then reaped, never signalled after; not after a reap timed out.
            if proc.returncode is None and not tree.reap_tried:
                tree.stop(proc, "finally")
                tree.reap(proc)
            tree.close()
            reader_done = "reader" not in locals() or not reader.is_alive()
            if reader_done:  # a pipe still held by an escaped descendant is left to process exit
                with contextlib.suppress(OSError):
                    proc.stdout.close()
    return {**state, "ended": now(), "process_tree": tree.note, "process_tree_stops": tree.stops}


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
    command_seconds, command_source = command_timeout(a.mode, allow, a.timeout, a.command_timeout)
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
        managed = bool(a.accept_follow_ups)
        argv = build_argv(prefix, a.mode, a.effort, allow, session_args, follow_ups=managed)
        check_batch_safe(argv)
        guidance = command_guidance(command_seconds, command_source, a.timeout)
        sent = compose(rules, prompt, guidance)
        offered = mode_tools(a.mode, allow)[0]
        stream = Stream(a.mode, offered, asked_id, root, attempt=attempt, follow_ups=managed)

        runs = d / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        run_dir = runs / f"{stamp()}-g{state['generation']}-{uuid.uuid4().hex[:6]}"
        run_dir.mkdir()
        inbox = Inbox(run_dir, stream) if managed else None
        first_uuid = inbox.prepare(sent.decode("utf-8")) if inbox else None
        write_new(run_dir / "prompt.txt", sent)
        write_new(run_dir / "brief.json", {
            "purpose": purpose, "scope": state["scope"], "mode": a.mode, "generation": state["generation"],
            "requested_model": MODEL, "effort": a.effort, "project_root": str(root),
            "attempt": attempt, "session": {"asked": asked_id, "args": session_args},
            "prompt_source": str(Path(a.prompt_file).resolve()) if a.prompt_file else "stdin",
            "prompt_sha256": hashlib.sha256(prompt).hexdigest(),
            "sent_sha256": hashlib.sha256(sent).hexdigest(),
            "rules_files": [{"path": p, "sha256": hashlib.sha256(b).hexdigest()} for p, b in rules],
            "no_project_rules": bool(a.no_project_rules), "allow_command": allow,
            "accept_follow_ups": managed, "first_message_uuid": first_uuid,
            "command_timeout_seconds": command_seconds, "command_timeout_source": command_source,
            **({"command_guidance_sha256": hashlib.sha256(guidance).hexdigest()} if guidance else {})})
        settings = cli_settings(d, command_seconds)
        (d / CLI_TMP).mkdir(exist_ok=True)
        running = {"run_dir": str(run_dir), "helper_pid": os.getpid(), "claude_pid": None, "started": now(),
                   "first": status == "new", "attempt": attempt, "asked_id": asked_id, "previous_status": status,
                   "follow_ups": managed}
        if managed:
            running["init_ok"] = False
        state.update(status="running", updated=now(), running=running)
        write_json_atomic(d / "state.json", state)

        def started(pid: int) -> None:
            running["claude_pid"] = pid
            with contextlib.suppress(SessionError):
                write_json_atomic(d / "state.json", state, seconds=1.0)

        def initialized() -> None:  # `follow-up` waits for this before it adds a message
            running["init_ok"] = True
            with contextlib.suppress(SessionError):
                write_json_atomic(d / "state.json", state, seconds=1.0)

        proc = launch(argv, sent, root, run_dir, a.timeout, stream, on_start=started, inbox=inbox,
                      on_init=initialized if managed else None, settings=settings)
        extra = ""
        if proc.get("launch_error"):
            extra = f"the Claude CLI could not be started: {proc['launch_error']}"
        elif proc.get("reader_error"):
            extra = f"the helper could not read the CLI's output ({proc['reader_error']}); the CLI was stopped"
        outcome = outcome_of(stream, proc, run_dir, extra)
        outcome["turns"] = stream.turns() if managed else []
        if managed:
            record = follow_up_record(run_dir, stream, proc.get("stopped_by_helper"))
            outcome["follow_up_notes"] = [f"follow-up {m.get('uuid')}: {m['outcome']}" for m in record["messages"]] + [
                f"inbox closed: {(record['inbox_closed'] or {}).get('reason')}"]
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
    for line in outcome.get("follow_up_notes") or []:
        print(f"[claude-session] {line}", file=sys.stderr)
    if outcome["status"] == "ok":
        turns = [t for t in outcome.get("turns") or [] if t.get("answering", True)]  # a terminal result prints nothing
        text = outcome["reply"] if len(turns) < 2 else "\n".join(
            f"----- turn {t['turn']} of {len(turns)}"
            + (f", after follow-up {', '.join(t['after_follow_ups'])}" if t["after_follow_ups"] else "")
            + f" -----\n{t['reply']}" for t in turns)
        raw = getattr(sys.stdout, "buffer", None)
        if raw is None:
            print(text)
        else:  # bytes, so the reply keeps its own line endings on Windows too
            sys.stdout.flush()
            raw.write(text.encode("utf-8") + b"\n")
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
    result = stream.final_result or {}
    if outcome["reply"] and not (run_dir / "reply.md").exists():
        write_new(run_dir / "reply.md", outcome["reply"])
    managed = bool(running.get("follow_ups"))
    turns = [{k: v for k, v in t.items() if k != "reply"} for t in stream.turns()]
    if managed:
        (run_dir / "replies").mkdir(exist_ok=True)
        for t, full in zip(turns, stream.turns()):
            t["reply_file"] = None
            if full["reply"]:
                with contextlib.suppress(FileExistsError):
                    write_new(run_dir / "replies" / f"{t['turn']}.md", full["reply"])
                t["reply_file"] = f"replies/{t['turn']}.md"
    stopped = proc.get("stopped_by_helper") or ("the helper was interrupted" if recovered else None)
    write_new(run_dir / "result.json", {
        "schema": SCHEMA, "classifier": CLASSIFIER, "purpose": state["purpose"], "scope": state["scope"],
        "mode": mode,
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
        "output_drained": proc.get("drained", True), "output_settlement": proc.get("output_settlement"),
        "process_tree": proc.get("process_tree"),
        "process_tree_stops": proc.get("process_tree_stops"),
        "stdin_error": proc.get("stdin_error"), "reader_error": proc.get("reader_error"), "ended": proc.get("ended"),
        "reply_file": "reply.md" if outcome["reply"] else None,
        "stdout_lines": stream.lines, "unparsed_stdout_lines": stream.unparsed, "oversized_lines": stream.oversized,
        "init": stream.init, "init_extra": stream.init_extra, "init_events": stream.inits,
        "pre_init_events": stream.pre_init,
        "command_lifecycle": {"by_command": stream.lifecycle, "unmatched": stream.unmatched_lifecycle,
                              "first_message_started": stream.first_started},
        "cli_version_differs_from_pinned": bool(stream.init) and (stream.init.get("claude_code_version") != PINNED_CLI),
        "accept_follow_ups": managed, "turns": turns,
        "follow_ups": follow_up_record(run_dir, stream, stopped) if managed else None,
        "result_event": {k: result.get(k) for k in (
            "subtype", "is_error", "num_turns", "duration_ms", "duration_api_ms", "total_cost_usd",
            "usage", "modelUsage", "permission_denials", "stop_reason", "terminal_reason")}
        if result else None,
        "prelude_results": stream.preludes,
        "commands": {"tasks": list(stream.tasks.values()), "background_breach": stream.breach,
                     "background_requests": stream.background_requests}})


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
        managed = running.get("follow_ups") is True
        stream = Stream(state["mode"], mode_tools(state["mode"], brief.get("allow_command") or [])[0],
                        running["asked_id"], root, attempt=running["attempt"], follow_ups=managed)
        if managed:  # the same first-message binding as the live run
            stream.first = brief.get("first_message_uuid")
            stream.sent.update(sent_from_log(run_dir))
        with contextlib.suppress(OSError):
            with open(run_dir / "stdout.jsonl", "rb") as f:
                for line in f:
                    stream.feed(line)
        if managed and (run_dir / "inbox").is_dir():  # nothing more is taken; unsent files are rejected
            Inbox(run_dir, stream).finish("the helper was interrupted")
        kept = read_json(run_dir / "result.json")
        if kept:  # the run was recorded; only the purpose's state was not saved
            outcome = outcome_of(stream, {"exit_code": kept.get("exit_code"), "timed_out": kept.get("timed_out"),
                                          "output_settlement": kept.get("output_settlement")}, run_dir)
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


def label_of(code: int) -> str:
    return {USAGE: "USAGE", BUSY: "BUSY", NEEDS_RESOLUTION: "NEEDS RESOLUTION"}.get(code, "FAILED")


def cmd_follow_up(a) -> int:
    """Add one message to the purpose's running managed call. Never takes the purpose lock and never
    launches the CLI: without a live, initialized managed call nothing is written (exit 5).

    Every run ends with one outcome record on stdout, so the result survives a host that rewrites exit
    codes (`powershell -Command` and `pwsh -Command` report 1 for any native exit other than 0 or 1):
    a line `follow-up outcome: <outcome> (exit <code>, sent <yes|no|unknown>)`, or with --json one JSON
    object {"outcome", "exit", "sent", "purpose", "message", ...} and the human lines on stderr. Outcomes:
    submitted (0); rejected, withdrawn, write_unconfirmed, failed (1); usage (2); off (3); not_running,
    closed (the call has finished: send a normal call), not_initialized (its init is not validated
    yet), not_managed, helper_dead, cli_dead, foreign_run, needs_resolution (5); interrupted (Ctrl-C: the
    record says exit none and the process then ends as Python's own interruption does).

    sent is "no" until the message is persisted in the inbox, then "unknown" until the helper's receipts
    establish more: "yes" once submitted, "no" once withdrawn or rejected with sent "no". Any failure or
    interruption after persisting runs the same locked withdrawal as the wait's end; if that cleanup
    cannot get the lock, sent stays "unknown" and the original cause is what is reported. Argument
    errors argparse itself reports (exit 2) come before any outcome record."""
    record: dict = {"command": "follow-up", "purpose": a.purpose, "sent": "no"}
    interrupted = None
    try:
        code = follow_up(a, record, sys.stderr if a.json else sys.stdout)
    except SessionError as exc:
        code = exc.code
        record["outcome"] = exc.outcome or {USAGE: "usage", NEEDS_RESOLUTION: "needs_resolution"}.get(code, "failed")
        record["message"] = str(exc)
        print(f"CLAUDE SESSION {label_of(code)}: {exc}", file=sys.stderr)
    except OSError as exc:
        code = FAILED
        record.update(outcome="failed", message=f"{type(exc).__name__}: {exc}")
        print(f"CLAUDE SESSION FAILED: {record['message']}", file=sys.stderr)
    except KeyboardInterrupt as exc:
        code, interrupted = None, exc
        record.update(outcome="interrupted", message="interrupted (KeyboardInterrupt)")
    record["exit"] = code
    if a.json:
        print(json.dumps(record, ensure_ascii=False))
    else:
        print(f"follow-up outcome: {record['outcome']} (exit {'none' if code is None else code}, sent"
              f" {record['sent']})")
    if interrupted is not None:
        sys.stdout.flush()
        raise interrupted  # the native exit of an interrupted Python process is kept
    return code


def rejected_sent(rejected: dict | None) -> str:
    """A rejection receipt's explicit sent field; a missing or unknown value (a legacy receipt) is "unknown"."""
    sent = (rejected or {}).get("sent")
    return sent if sent in ("no", "unknown") else "unknown"


def settle_follow_up(run_dir: Path, u: str, reason: str) -> str:
    """Under the inbox lock, withdraw follow-up u unless the helper has taken it, and return what is known of
    its delivery: "yes" (submitted), "no" (withdrawn, or rejected with sent "no") or "unknown". Raises if
    the lock cannot be had or the withdrawal cannot be written."""
    with inbox_lock(run_dir):
        return settle_locked(run_dir / "acks", u, reason)


def settle_locked(acks: Path, u: str, reason: str) -> str:
    """settle_follow_up's decision, for a caller that already holds the inbox lock (it never takes it)."""
    if (acks / f"{u}.submitted.json").exists():
        return "yes"
    if (acks / f"{u}.rejected.json").exists():
        return rejected_sent(read_json(acks / f"{u}.rejected.json"))
    if (acks / f"{u}.withdrawn.json").exists():
        return "no"
    if (acks / f"{u}.taken.json").exists():
        return "unknown"
    write_new(acks / f"{u}.withdrawn.json", {"uuid": u, "withdrawn_at": now(), "reason": reason})
    return "no"


def note_cleanup(record: dict, cleanup: BaseException) -> None:
    """A cleanup that could not establish delivery: sent stays unknown; the original cause is still raised."""
    record.update(sent="unknown", cleanup_error=f"{type(cleanup).__name__}: {cleanup}")


def follow_up(a, record: dict, out) -> int:
    if switched_off():
        record["outcome"] = "off"
        print("CLAUDE OFF: SIJAV_CLAUDE=off, so no follow-up was sent and nothing was recorded.", file=sys.stderr)
        return OFF
    purpose = check_purpose(a.purpose)
    if a.wait <= 0:
        raise SessionError("--wait must be a positive number of seconds", USAGE)
    root = project_root(a.project)
    data = read_prompt(a.prompt_file)
    if len(data) > MAX_FOLLOW_UP:
        raise SessionError(f"the follow-up is {len(data)} bytes; at most {MAX_FOLLOW_UP} are accepted. Nothing was"
                           " sent.", USAGE)
    d = existing_purpose_dir(root, purpose)
    state = read_state(d, purpose, root)
    running = (state or {}).get("running") or {}

    def not_live(why: str, outcome: str) -> SessionError:
        return SessionError(f"purpose {purpose!r}: {why}. Nothing was sent and no call was started; send it as a"
                            " normal `call` instead.", NEEDS_RESOLUTION, outcome)

    if state is None or state["status"] != "running":
        raise not_live(f"no call is running (status {(state or {}).get('status')})", "not_running")
    run_dir = Path(running["run_dir"])
    if not same_path(run_dir.parent, d / "runs"):
        raise not_live(f"the running run {run_dir} is not under {d / 'runs'}", "foreign_run")
    if running.get("follow_ups") is not True:
        raise not_live("the running call was not started with --accept-follow-ups", "not_managed")
    if not pid_alive(running.get("helper_pid")):
        raise not_live(f"the running call's helper (pid {running.get('helper_pid')}) is not alive; once its Claude"
                       " process has stopped, run `recover --confirm-stopped`", "helper_dead")
    if not pid_alive(running.get("claude_pid")):
        raise not_live(f"the running call's Claude CLI (pid {running.get('claude_pid')}) is not alive", "cli_dead")
    if running.get("init_ok") is not True:
        raise not_live("the running call's init has not been validated yet", "not_initialized")
    u = str(uuid.uuid4())
    inbox, acks = run_dir / "inbox", run_dir / "acks"
    path, settled = None, False  # path is bound before writing: whatever ends this, a file there gets settled
    try:
        with inbox_lock(run_dir):
            if (inbox / "closed").exists():
                closed = read_json(inbox / "closed") or {}
                raise not_live(f"the running call has stopped taking follow-ups ({closed.get('reason')})", "closed")
            name = f"{len(list(inbox.glob('*.json'))) + 1:04d}-{u}.json"
            path = inbox / name
            try:
                write_new(path, {"uuid": u, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
                                 "text": data.decode("utf-8"), "created": now(), "pid": os.getpid(),
                                 "source": str(Path(a.prompt_file).resolve()) if a.prompt_file else "stdin"})
            except BaseException as exc:  # still under the lock, so the helper has not read the file: remove it
                with contextlib.suppress(OSError):
                    path.unlink()
                if not path.exists():
                    raise
                # it stays visible (a late write error, an unlink that failed): withdraw it under this same lock
                record["uuid"], record["run"], record["sent"] = u, run_dir.name, "unknown"
                settled = True
                try:
                    record["sent"] = settle_locked(acks, u, f"its inbox write failed ({type(exc).__name__})")
                except BaseException as cleanup:  # noqa: BLE001 -- the original cause is the one reported
                    note_cleanup(record, cleanup)
                raise
            record.update(uuid=u, run=run_dir.name, sent="unknown")  # persisted: unknown until receipts say more
        return await_receipts(a, purpose, run_dir, u, name, record, out)
    except BaseException as exc:
        if not settled and path is not None and path.exists():  # possibly persisted, even if bookkeeping was cut off
            record["uuid"], record["run"], record["sent"] = u, run_dir.name, "unknown"
            try:  # the lock was released (or its handle closed) on the way out, so it is taken anew, not re-entered
                record["sent"] = settle_follow_up(run_dir, u, f"follow-up ended before its outcome"
                                                              f" ({type(exc).__name__})")
            except BaseException as cleanup:  # noqa: BLE001 -- the original cause is the one reported
                note_cleanup(record, cleanup)
        raise


def await_receipts(a, purpose: str, run_dir: Path, u: str, name: str, record: dict, out) -> int:
    """Wait for the helper's receipt of a persisted follow-up; at the wait's end, withdraw it if untaken."""
    inbox, acks = run_dir / "inbox", run_dir / "acks"
    deadline = time.monotonic() + a.wait
    while not (acks / f"{u}.submitted.json").exists():
        if (acks / f"{u}.rejected.json").exists():
            rejected = read_json(acks / f"{u}.rejected.json") or {}
            print(f"CLAUDE FOLLOW-UP REJECTED: {rejected.get('reason')} (follow-up {u}, {inbox / name})",
                  file=sys.stderr)
            record.update(outcome="rejected", message=rejected.get("reason"), sent=rejected_sent(rejected))
            return FAILED
        if time.monotonic() >= deadline:
            record["sent"] = settle_follow_up(run_dir, u, f"the call did not take it within {a.wait} s")
            if (acks / f"{u}.withdrawn.json").exists():
                print(f"CLAUDE FOLLOW-UP NOT SENT: the call did not take follow-up {u} within {a.wait} s; it was"
                      f" withdrawn ({inbox / name}).", file=sys.stderr)
                record["outcome"] = "withdrawn"
                return FAILED
            if not (acks / f"{u}.submitted.json").exists() and not (acks / f"{u}.rejected.json").exists():
                print(f"CLAUDE FOLLOW-UP UNKNOWN: the call took follow-up {u} but its write to the CLI was not"
                      f" confirmed within {a.wait} s; the run's result.json will record what happened ({run_dir}).",
                      file=sys.stderr)
                record.update(outcome="write_unconfirmed", sent="unknown")
                return FAILED
            continue
        time.sleep(0.05)
    submitted = read_json(acks / f"{u}.submitted.json") or {}
    record.update(outcome="submitted", sent="yes", submitted_at=submitted.get("submitted_at"))
    print(f"follow-up {u} submitted at {submitted.get('submitted_at')} to purpose {purpose}, run {run_dir}", file=out)
    while time.monotonic() < deadline and not (acks / f"{u}.acknowledged.json").exists():
        time.sleep(0.05)
    acknowledged = read_json(acks / f"{u}.acknowledged.json")
    record["acknowledged_at"] = (acknowledged or {}).get("acknowledged_at")
    if acknowledged:
        print(f"acknowledged by the CLI at {acknowledged.get('acknowledged_at')} (its replay of the message); the"
              " answer comes in the call's own output", file=out)
    else:
        print(f"not acknowledged within {a.wait} s (no replay seen yet); the run's result.json will record it",
              file=out)
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
    c.add_argument("--command-timeout", type=int, default=None,
                   help="code mode with --allow-command: seconds each command may run (default: the smaller of 600"
                        " and half of --timeout; must be below --timeout)")
    c.add_argument("--project", default=None)
    c.add_argument("--accept-follow-ups", action="store_true",
                   help="managed call: stream-json input, open for `follow-up` messages until answered")

    fu = sub.add_parser("follow-up", help="add a message to the purpose's running managed call", allow_abbrev=False)
    fu.add_argument("--purpose", required=True)
    fu.add_argument("--prompt-file", default=None, help="the message; else stdin")
    fu.add_argument("--wait", type=int, default=FOLLOW_UP_WAIT, help="seconds to wait for submission and replay")
    fu.add_argument("--project", default=None)
    fu.add_argument("--json", action="store_true", help="print the outcome as one JSON object on stdout")

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
               "reset": cmd_reset, "recover": cmd_recover, "follow-up": cmd_follow_up}[a.command]
    try:
        return handler(a)
    except SessionError as exc:
        print(f"CLAUDE SESSION {label_of(exc.code)}: {exc}", file=sys.stderr)
        return exc.code
    except OSError as exc:  # an I/O failure a command did not turn into its own cause: no traceback
        print(f"CLAUDE SESSION FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return FAILED


if __name__ == "__main__":
    sys.exit(main())
