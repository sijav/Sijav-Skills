#!/usr/bin/env python3
"""A stand-in for the Claude CLI in tests. It never calls a model.

Strict where the real CLI is known (tests/fixtures/claude-2.1.286): the options, their argument
shapes and listed choices are parsed from the real 2.1.286 `--help`, and parsed the way commander
does (variadic values stop at the next dash argument; an optional value is not taken from a dash
argument; --name=value works). An unknown option, a bad choice, a positional prompt, a non-UUID
--session-id, a --resume of a session this fake never created, or a --session-id that already exists
fails with exit 1. The init event is the real 2.1.286 init (init.json) with this run's cwd, session
id, model and tools; tools are listed sorted, as the real init listed them.

Settings files, as the 2.1.286 help describes them: without --restricted (or a --setting-sources
list that leaves a source out), the fake reads the project's .claude/settings.json and
.claude/settings.local.json and logs what they would apply ("settings_applied": their permission
allow rules and env names). --restricted makes it ignore them, as the help says.

Seen in the real 2.1.286 smoke runs of October 1 (05:00 and 05:04 UTC): with --permission-mode manual
the init reports "default". Not verified against the real CLI, and marked so: the exact wording of
the "No conversation found" and "already in use" errors, and the shape of permission_denials entries
(tool_name, tool_use_id, tool_input, as documented for the SDK). A session is stored (so it can be
resumed) only once a turn was delivered; FAKE_CLAUDE_PERSIST=1 stores it even when none was.

Appends one JSON line per invocation to $FAKE_CLAUDE_LOG (argv, the exact stdin bytes as base64, cwd,
pid, and the names of ANTHROPIC_*/CLAUDE_CODE_* variables it saw). $FAKE_CLAUDE_STORE is its session
store. $FAKE_CLAUDE_MODE:
  ok               init, a top-level assistant message (with a thinking block and signature), success
  gate             init, then waits for the file $FAKE_CLAUDE_GATE, then as ok
  sleep            init, then sleeps 60 s
  fail_no_id       nothing on stdout; "not logged in" on stderr; exit 1
  error_result     init, then an error result with no turn (expired sign-in); exit 1
  wrong_model      init reports another model, then sleeps 60 s
  wrong_reply_model  init reports the asked model; the reply comes from another model
  extra_tools      init lists Write and Bash too, then sleeps 60 s
  extra_safe_tool  init lists Task too, then sleeps 60 s
  mcp              init lists an MCP server, then sleeps 60 s
  accept_edits     init reports permission mode acceptEdits, then sleeps 60 s
  api_key          init reports apiKeySource ANTHROPIC_API_KEY, then sleeps 60 s
  no_init          an assistant message and a result, no init
  init_not_first   a rate_limit_event before the init, then sleeps 60 s
  bad_session_id   init reports the session id "-c"
  resume_new_id    as ok, but a resumed call reports a different session id
  garbage          a line that is not JSON; exit 0
  orphan           starts a grandchild that inherits stdout (the launcher's pid goes to
                   $FAKE_CLAUDE_CHILD_PID). In the folder $FAKE_CLAUDE_HOLDER the process that holds stdout
                   takes an exclusive lock on holder.lock for its whole life, then writes its parent's pid
                   to holder.ppid and its own pid to holder.pid, and ends when a file named release appears
                   there or after 60 s. Observations only: before starting the launcher the fake writes
                   fake.job.json there, and the holder writes holder.job.json after its lock and before
                   holder.pid (JOB_RECORD: each process's own job, as seen at that moment). The fake
                   waits up to 10 s for holder.pid (else "holder not ready" on stderr, exit 3, no init),
                   prints the init, and exits 0 without a result
  denied_read      as ok, but the result lists a denied Read
  zero_turns       init, then a success result with num_turns 0 and no assistant message (the shape of
                   the October 3 incident's result); exit 0
  pre_init_only    the $FAKE_CLAUDE_PRE_INIT events, then exit 0 without an init

Before the init, $FAKE_CLAUDE_PRE_INIT names a JSON file holding a list of lines to print first: an
object is printed as one JSON line (any string value "$SID" becomes the session id), a string as it
is; "$CMD" becomes the first stream-json message's uuid, "$NEW" a fresh uuid, and the line "$REPLAY"
prints the first message's replay there (and not again after the init). Lifecycle events
({"type": "command_lifecycle", "command_uuid", "state", "uuid", "session_id"}) are written this way in
the shape seen from the real CLI on October 3 (queued, then started, both naming the first message's
uuid, before the init); the type is undocumented. $FAKE_CLAUDE_LIFECYCLE=1 prints queued and started
for each later message as it arrives (=unmatched adds one for an unknown command).
$FAKE_CLAUDE_INIT_CHANGE (JSON object) changes fields of the first init. $FAKE_CLAUDE_PRELUDE=N prints,
right after the init (and replay), N zero-turn success results with null terminal_reason and stop_reason
before any work. A real interrupted resume printed exactly one, after a pre-init task_notification
(give that with $FAKE_CLAUDE_PRE_INIT on a resume); N=2 or no notification is the unobserved shape.
$FAKE_CLAUDE_EXTRA_TOOLS (comma list) adds those tools to the init, as a CLI that ignored a denial would.
$FAKE_CLAUDE_COMMAND runs one Bash command `python stage.py` before the answer, with the event shapes Claude
Code 2.1.288 printed in live runs: a main-thread tool_use, system/task_started {task_id, tool_use_id,
description, is_backgrounded, task_type: local_bash}, then the tool result, whose user event carries
tool_use_result {stdout, stderr, interrupted, isImage, noOutputExpected[, backgroundTaskId]}.
  foreground  is_backgrounded false and no backgroundTaskId (as every live foreground command).
  background  the tool_use asks for run_in_background. With CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1 this fake
              runs it in the foreground (its stand-in for the documented behaviour, not verified live); with
              $FAKE_CLAUDE_IGNORE_DISABLE=1 it starts it in the background as 2.1.288 did with the variable
              unset: is_backgrounded true and a backgroundTaskId.
  auto        a foreground request that, with $FAKE_CLAUDE_IGNORE_DISABLE=1, is moved to the background: a
              task_started with is_backgrounded false, then a result naming a backgroundTaskId. Not observed
              live; the backgroundTaskId key is the one live background results carried.
In gate mode with stream-json input the command's result comes after the gate, so follow-ups sent while the
gate is shut arrive during the command. The log records the four environment variables the helper controls
(controlled_env).
$FAKE_CLAUDE_TURN_RESULTS (stream-json input; comma list by turn: ok, error_zero, success_zero) ends
that turn with a zero-turn is_error result or a zero-turn success instead of an answer, and then keeps
reading stdin, as a streaming CLI stays alive after an error result. $FAKE_CLAUDE_PRE_INIT_PAUSE
seconds are slept after the pre-init lines. $FAKE_CLAUDE_SECOND_INIT (JSON object)
prints a second init with those fields changed right after the first. $FAKE_CLAUDE_REPLY_MODEL makes
the reply text come from that model.

--input-format stream-json (documented; this fake's handling is a stand-in, not verified against the
real CLI): stdin is read line by line; each line must be a user message {"type": "user", "message":
{"role": "user", "content": ...}, "parent_tool_use_id": null, "uuid": ...}, else exit 1. Each line
received is appended to $FAKE_CLAUDE_LOG.stdin.jsonl. With --replay-user-messages each message is
printed back as {"type": "user", ..., "isReplay": true} with its uuid. $FAKE_CLAUDE_INIT_ORDER:
init_then_replay (default: the first message is read, then the init, then its replay), replay_first
(the replay before the init) or init_first (the init before stdin is read). $FAKE_CLAUDE_INIT_GATE: the
init waits for that file. $FAKE_CLAUDE_REPLAY: receipt (default: a message is replayed when it
arrives and, mid-turn, joins that turn), dequeue (a message arriving mid-turn is replayed and answered
as the next turn), fresh_uuid (as receipt, with a new uuid) or none. In gate mode the first turn
prints an assistant tool_use, then waits for the gate while messages arrive. Every turn's reply is
"<reply> (turn N, M message(s))"; after a result the next message starts a turn; stdin's end ends the
CLI with exit 0.
"""

import base64
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
PINNED = HERE / "claude-2.1.286"
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
# The orphan mode's record of a process's own job, written once (exclusively) to `path`. Only the calling
# process and its own job are asked: IsProcessInJob(GetCurrentProcess(), NULL) for any job, and
# QueryInformationJobObject(NULL, JobObjectBasicProcessIdList) for the innermost job's ids, with room for
# 64. A failed call is recorded as unknown, never as an empty job, and a list shorter than the job's count
# as incomplete. Nested or foreign jobs make the list no proof of which job it is. It is true when it is
# written: nothing here says whether a job is assigned later. Never raises: a failure goes to stderr.
JOB_RECORD = '''def write_job_record(path):
    import json, os, sys, time
    record = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "pid": os.getpid(), "ppid": os.getppid(),
              "executable": sys.executable, "base_executable": getattr(sys, "_base_executable", None)}
    try:
        if os.name != "nt":
            record["job"] = "not applicable: not Windows"
        else:
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.GetCurrentProcess.argtypes, k32.GetCurrentProcess.restype = [], wintypes.HANDLE
            k32.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
            k32.IsProcessInJob.restype = wintypes.BOOL
            k32.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                      wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
            k32.QueryInformationJobObject.restype = wintypes.BOOL
            in_job = wintypes.BOOL()
            if k32.IsProcessInJob(k32.GetCurrentProcess(), None, ctypes.byref(in_job)):
                record["in_any_job"] = bool(in_job.value)
            else:
                record["in_any_job"] = f"unknown: IsProcessInJob failed, error {ctypes.get_last_error()}"

            class Ids(ctypes.Structure):  # JOBOBJECT_BASIC_PROCESS_ID_LIST; ULONG_PTR is pointer-sized
                _fields_ = [("assigned", wintypes.DWORD), ("listed", wintypes.DWORD), ("ids", ctypes.c_size_t * 64)]

            ids, returned = Ids(), wintypes.DWORD()
            if k32.QueryInformationJobObject(None, 3, ctypes.byref(ids), ctypes.sizeof(ids), ctypes.byref(returned)):
                listed = min(ids.listed, 64)
                record["innermost_job"] = {"assigned": ids.assigned, "listed": ids.listed, "ids": list(ids.ids[:listed]),
                                           "complete": ids.listed == ids.assigned}
            else:
                error = ctypes.get_last_error()
                record["innermost_job"] = (f"incomplete: more than 64 ids (error {error}, ERROR_MORE_DATA); not read"
                                           if error == 234 else f"unknown: QueryInformationJobObject failed, error {error}")
    except Exception as exc:
        record["job_error"] = f"unknown: {type(exc).__name__}: {exc}"
    try:
        with open(path, "x", encoding="utf-8") as f:
            json.dump(record, f)
    except Exception as exc:
        sys.stderr.write(f"job record not written: {path}: {type(exc).__name__}: {exc}\\n")
        sys.stderr.flush()
'''
exec(JOB_RECORD)  # the fake's own copy; the holder runs the same text
# The orphan mode's stdout holder (argv[1] is its folder): the lock comes first, so a written pid
# means the lock is held and its job record was attempted; the OS releases it when the process ends,
# however it ends.
HOLDER = JOB_RECORD + """import os, sys, time
folder = sys.argv[1]
lock = os.open(os.path.join(folder, "holder.lock"), os.O_RDWR | os.O_CREAT)
if os.name == "nt":
    import msvcrt
    msvcrt.locking(lock, msvcrt.LK_NBLCK, 1)
else:
    import fcntl
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
with open(os.path.join(folder, "holder.ppid"), "w") as f:
    f.write(str(os.getppid()))
write_job_record(os.path.join(folder, "holder.job.json"))
with open(os.path.join(folder, "holder.pid.tmp"), "w") as f:
    f.write(str(os.getpid()))
os.replace(os.path.join(folder, "holder.pid.tmp"), os.path.join(folder, "holder.pid"))
end = time.monotonic() + 60
while time.monotonic() < end and not os.path.exists(os.path.join(folder, "release")):
    time.sleep(0.1)
"""


def fail(message):
    sys.stderr.write(message + "\n")
    sys.exit(1)


def options():
    """{name: (kind, choices)} from the real help; kind is flag, required, optional or variadic."""
    lines = (PINNED / "help.txt").read_text(encoding="utf-8").splitlines()
    start = lines.index("Options:") + 1
    end = lines.index("Commands:")
    spec, current = {}, None
    head = re.compile(r"^  (?:(-\w), )?(--[\w-]+)(?:, (--[\w-]+))?(?: (<[^>]+>|\[[^\]]+\]))?")
    for line in lines[start:end]:
        m = head.match(line)
        if m:
            arg = m.group(4)
            kind = ("flag" if not arg else "optional" if arg.startswith("[")
                    else "variadic" if arg.endswith("...>") else "required")
            current = [kind, [], ""]
            for name in (m.group(1), m.group(2), m.group(3)):
                if name:
                    spec[name] = current
            current[2] = line
        elif current is not None:
            current[2] += " " + line.strip()
    for name, entry in spec.items():
        text = re.sub(r"\s+", " ", entry[2])
        m = re.search(r"\(choices: ((?:\"[^\"]+\"(?:, )?)+)", text)
        if m:
            entry[1] = re.findall(r"\"([^\"]+)\"", m.group(1))
        elif name == "--effort":
            entry[1] = re.search(r"\(([a-z]+(?:, [a-z]+)+)\)", text).group(1).split(", ")
    return spec


def parse(argv):
    spec = options()
    got, i = {}, 0
    while i < len(argv):
        a = argv[i]
        if not a.startswith("-"):
            fail(f"fake: a positional argument {a!r} was given; the helper sends the prompt on stdin")
        name, _, inline = a.partition("=") if a.startswith("--") else (a, "", "")
        if name not in spec:
            fail(f"error: unknown option '{name}'")
        kind, choices, _ = spec[name]
        key = max((n for n, e in spec.items() if e is spec[name]), key=len)
        if kind == "flag":
            value = True
        elif inline:
            value = [inline] if kind == "variadic" else inline
        elif kind == "required":
            if i + 1 >= len(argv):
                fail(f"error: option '{name}' argument missing")
            i += 1
            value = argv[i]
        elif kind == "optional":
            if i + 1 < len(argv) and not argv[i + 1].startswith("-"):
                i += 1
                value = argv[i]
            else:
                value = True
        else:
            value = []
            while i + 1 < len(argv) and not argv[i + 1].startswith("-"):
                i += 1
                value.append(argv[i])
            if not value:
                fail(f"error: option '{name}' argument missing")
        if choices and value not in choices:
            fail(f"error: option '{name}' argument '{value}' is invalid. Allowed choices are {', '.join(choices)}.")
        got[key] = value
        i += 1
    return got


def settings_applied(opts):
    """What project settings files would apply: nothing under --restricted."""
    if "--restricted" in opts:
        return []
    sources = opts.get("--setting-sources")
    if isinstance(sources, str) and "project" not in sources.split(",") and "local" not in sources.split(","):
        return []
    out = []
    for name in ("settings.json", "settings.local.json"):
        f = Path(os.getcwd()) / ".claude" / name
        if f.is_file():
            data = json.loads(f.read_text(encoding="utf-8"))
            out += [f"allow {r}" for r in (data.get("permissions") or {}).get("allow", [])]
            out += [f"env {k}" for k in (data.get("env") or {})]
    return out


argv = sys.argv[1:]
stream_in = "--input-format=stream-json" in argv or any(
    a == "--input-format" and argv[i + 1:i + 2] == ["stream-json"] for i, a in enumerate(argv))
data = b"" if stream_in else sys.stdin.buffer.read()
opts = parse(argv)
log = os.environ.get("FAKE_CLAUDE_LOG")
if log:
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"argv": argv, "stdin_b64": base64.b64encode(data).decode(), "cwd": os.getcwd(),
                            "pid": os.getpid(), "settings_applied": settings_applied(opts),
                            "stream_input": stream_in,
                            "controlled_env": {k: os.environ.get(k) for k in (
                                "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS", "CLAUDE_CODE_TMPDIR",
                                "BASH_DEFAULT_TIMEOUT_MS", "BASH_MAX_TIMEOUT_MS")},
                            "env": sorted(k for k in os.environ
                                          if k.upper().startswith(("ANTHROPIC_", "CLAUDE_CODE_", "NODE_")))})
                + "\n")

if "--print" not in opts:
    fail("fake: the helper always runs with -p")
replaying = "--replay-user-messages" in opts
if replaying and not (stream_in and opts.get("--output-format") == "stream-json"):
    fail("Error: --replay-user-messages requires --input-format=stream-json and --output-format=stream-json")
messages = queue.Queue()


def pump():
    """Stream-json input: each line must be a user message; None marks the end of stdin."""
    for line in sys.stdin.buffer:
        if log:
            with open(log + ".stdin.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps({"pid": os.getpid(), "line_b64": base64.b64encode(line).decode()}) + "\n")
        try:
            msg = json.loads(line)
            ok = (msg.get("type") == "user" and msg["message"].get("role") == "user"
                  and isinstance(msg["message"].get("content"), (str, list)) and msg.get("parent_tool_use_id") is None
                  and UUID_RE.match(msg.get("uuid") or ""))
        except (ValueError, AttributeError, KeyError, TypeError):
            ok = False
        if not ok:
            sys.stderr.write(f"fake: malformed stream-json input line {line[:200]!r}\n")
            sys.stderr.flush()
            os._exit(1)
        messages.put(msg)
    messages.put(None)


if stream_in:
    threading.Thread(target=pump, daemon=True).start()
mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
reply = os.environ.get("FAKE_CLAUDE_REPLY", "fake reply")
store = Path(os.environ.get("FAKE_CLAUDE_STORE") or (Path(os.getcwd()) / ".fake-claude-store"))
store.mkdir(parents=True, exist_ok=True)

if "--continue" in opts or "--fork-session" in opts:
    fail("fake: --continue/--fork-session would not keep the purpose's exact conversation")
new_id, resume_id = opts.get("--session-id"), opts.get("--resume")
if new_id and resume_id:
    fail("fake: --session-id and --resume were both given")
if new_id is not None:
    if not isinstance(new_id, str) or not UUID_RE.match(new_id):
        fail("Error: Invalid session ID. Must be a valid UUID.")
    if (store / f"{new_id}.json").exists():
        fail(f"Error: Session ID {new_id} is already in use.")
    sid = new_id
elif resume_id is not None:
    if resume_id is True or not UUID_RE.match(resume_id) or not (store / f"{resume_id}.json").exists():
        fail(f"No conversation found with session ID: {resume_id}")
    sid = resume_id
else:
    sid = str(uuid.uuid4())
if mode == "resume_new_id" and resume_id:
    sid = str(uuid.uuid4())

if mode == "fail_no_id":
    fail("Error: Not logged in · Please run /login")
if mode == "garbage":
    sys.stdout.write("this is not json\n")
    sys.exit(0)

model = opts.get("--model")
reply_model = "claude-sonnet-5-5" if mode == "wrong_reply_model" else os.environ.get("FAKE_CLAUDE_REPLY_MODEL", model)
tools = sorted(t for v in (opts.get("--tools") or []) for t in v.split(",") if t)  # variadic in 2.1.286
if mode == "extra_tools":
    tools = sorted(tools + ["Write", "Bash"])
if mode == "extra_safe_tool":
    tools = sorted(tools + ["Task"])
tools = sorted(tools + [t for t in (os.environ.get("FAKE_CLAUDE_EXTRA_TOOLS") or "").split(",") if t])
init = json.loads((PINNED / "init.json").read_text(encoding="utf-8"))["event"]
init.update(cwd=os.getcwd(), session_id="-c" if mode == "bad_session_id" else sid,
            model="claude-sonnet-5-5" if mode == "wrong_model" else model, tools=tools,
            permissionMode={"manual": "default", None: "default"}.get(opts.get("--permission-mode"),
                                                                       opts.get("--permission-mode")),
            apiKeySource="ANTHROPIC_API_KEY" if (mode == "api_key" or os.environ.get("ANTHROPIC_API_KEY")) else "none")
if mode == "accept_edits":
    init["permissionMode"] = "acceptEdits"
if mode == "mcp":
    init["mcp_servers"] = [{"name": "github", "status": "connected"}]
init.update(json.loads(os.environ.get("FAKE_CLAUDE_INIT_CHANGE") or "{}"))


def emit(event):
    sys.stdout.write((event if isinstance(event, str) else json.dumps(event)) + "\n")
    sys.stdout.flush()


def deliver():
    (store / f"{sid}.json").write_text(json.dumps({"turns": 1}), encoding="utf-8")


def with_sid(value):
    if isinstance(value, dict):
        return {k: with_sid(v) for k, v in value.items()}
    if isinstance(value, list):
        return [with_sid(v) for v in value]
    if value == "$CMD":
        return first["uuid"] if first else value
    if value == "$NEW":
        return str(uuid.uuid4())
    return sid if value == "$SID" else value


replay_mode = os.environ.get("FAKE_CLAUDE_REPLAY", "receipt")


def replay(msg):
    if replaying and replay_mode != "none":
        emit({"type": "user", "message": msg["message"], "parent_tool_use_id": None, "session_id": sid,
              "uuid": str(uuid.uuid4()) if replay_mode == "fresh_uuid" else msg["uuid"], "isReplay": True})


def lifecycle(msg):
    """$FAKE_CLAUDE_LIFECYCLE: command_lifecycle queued and started for a follow-up as it arrives
    ("unmatched" adds one for a command this CLI never received)."""
    marks = os.environ.get("FAKE_CLAUDE_LIFECYCLE")
    if marks:
        cmds = [msg["uuid"]] + ([str(uuid.uuid4())] if marks == "unmatched" else [])
        for cmd in cmds:
            for state in ("queued", "started"):
                emit({"type": "command_lifecycle", "command_uuid": cmd, "state": state, "uuid": str(uuid.uuid4()),
                      "session_id": sid})


def next_message(block=True):
    try:
        return messages.get(block=block, timeout=None if block else 0.05)
    except queue.Empty:
        return False


first, replayed = None, False
order = os.environ.get("FAKE_CLAUDE_INIT_ORDER", "init_then_replay")
if stream_in and order != "init_first":
    first = next_message()
    if first is None:
        fail("Error: Input must be provided through stdin when using --input-format=stream-json")
    if order == "replay_first":
        replay(first)
if os.environ.get("FAKE_CLAUDE_PRE_INIT"):
    for line in json.loads(Path(os.environ["FAKE_CLAUDE_PRE_INIT"]).read_text(encoding="utf-8")):
        if line == "$REPLAY":  # the first message's replay at this point instead of after the init
            replay(first)
            replayed = True
        else:
            emit(with_sid(line))
    time.sleep(float(os.environ.get("FAKE_CLAUDE_PRE_INIT_PAUSE", "0")))
if mode == "pre_init_only":
    sys.exit(0)
if os.environ.get("FAKE_CLAUDE_INIT_GATE"):
    while not os.path.exists(os.environ["FAKE_CLAUDE_INIT_GATE"]):
        time.sleep(0.05)

if mode == "no_init":
    emit({"type": "assistant", "parent_tool_use_id": None, "session_id": sid,
          "message": {"model": model, "content": [{"type": "text", "text": reply}]}})
    emit({"type": "result", "subtype": "success", "is_error": False, "result": reply, "session_id": sid, "num_turns": 1})
    deliver()
    sys.exit(0)
if mode == "init_not_first":
    emit({"type": "rate_limit_event", "session_id": sid})
if mode == "orphan":
    holder = Path(os.environ["FAKE_CLAUDE_HOLDER"])
    write_job_record(holder / "fake.job.json")
    child = subprocess.Popen([sys.executable, "-c", HOLDER, str(holder)], stdout=sys.stdout)
    Path(os.environ["FAKE_CLAUDE_CHILD_PID"]).write_text(str(child.pid), encoding="utf-8")
    ready = time.monotonic() + 10
    while not (holder / "holder.pid").exists():
        if time.monotonic() >= ready:
            sys.stderr.write("holder not ready\n")
            sys.exit(3)
        time.sleep(0.05)
emit(init)
if os.environ.get("FAKE_CLAUDE_SECOND_INIT"):
    emit({**init, **json.loads(os.environ["FAKE_CLAUDE_SECOND_INIT"])})
if stream_in and order == "init_then_replay" and not replayed:
    replay(first)
elif stream_in and order == "init_first":
    first = next_message()
    if first is None:
        sys.exit(0)
    replay(first)
if mode == "orphan":
    sys.exit(0)
for _ in range(int(os.environ.get("FAKE_CLAUDE_PRELUDE") or 0)):  # the result a real resume printed before work
    emit({"type": "result", "subtype": "success", "is_error": False, "num_turns": 0, "result": "", "session_id": sid,
          "terminal_reason": None, "stop_reason": None})
command = os.environ.get("FAKE_CLAUDE_COMMAND")  # foreground | background | auto
command_result = None
if command:
    if "Bash" not in tools:
        fail("fake: a command needs Bash, which was not offered")
    honoured = (os.environ.get("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS") == "1"
                and os.environ.get("FAKE_CLAUDE_IGNORE_DISABLE") != "1")
    started_bg = command == "background" and not honoured
    moved_bg = command == "auto" and not honoured
    emit({"type": "assistant", "parent_tool_use_id": None, "session_id": sid,
          "message": {"model": model, "role": "assistant", "content": [
              {"type": "tool_use", "id": "toolu_fake_cmd", "name": "Bash",
               "input": {"command": "python stage.py", "description": "Run the stage",
                         **({"run_in_background": True} if command == "background" else {})}}]}})
    emit({"type": "system", "subtype": "task_started", "task_id": "bfake01", "tool_use_id": "toolu_fake_cmd",
          "description": "Run the stage", "is_backgrounded": started_bg, "task_type": "local_bash",
          "uuid": str(uuid.uuid4()), "session_id": sid})
    data_ = {"stdout": "" if started_bg or moved_bg else "stage done\n", "stderr": "", "interrupted": False,
             "isImage": False, "noOutputExpected": False}
    if started_bg or moved_bg:
        data_["backgroundTaskId"] = "bfake01"
    cmd_text = ("Command running in background with ID: bfake01. You will be notified when it completes."
                if started_bg or moved_bg else "stage done")
    command_result = {"type": "user", "parent_tool_use_id": None, "session_id": sid, "tool_use_result": data_,
                      "message": {"role": "user", "content": [
                          {"type": "tool_result", "tool_use_id": "toolu_fake_cmd", "content": cmd_text}]}}
    if not (mode == "gate" and stream_in):
        emit(command_result)
        command_result = None
if os.environ.get("FAKE_CLAUDE_PERSIST") == "1":
    deliver()
if mode == "gate" and not stream_in:
    gate = os.environ["FAKE_CLAUDE_GATE"]
    while not os.path.exists(gate):
        time.sleep(0.05)
if mode in ("sleep", "extra_tools", "extra_safe_tool", "wrong_model", "mcp", "accept_edits", "api_key",
            "init_not_first"):
    time.sleep(60)
if mode == "error_result":
    emit({"type": "result", "subtype": "success", "is_error": True, "session_id": sid,
          "result": "OAuth token has expired. Please run /login", "num_turns": 0,
          "usage": {"input_tokens": 0, "output_tokens": 0}, "modelUsage": {}, "permission_denials": []})
    sys.exit(1)
if mode == "zero_turns":
    emit({"type": "result", "subtype": "success", "is_error": False, "session_id": sid, "result": reply,
          "num_turns": 0, "usage": {"input_tokens": 0, "output_tokens": 0}, "modelUsage": {},
          "permission_denials": []})
    sys.exit(0)


TURN_RESULTS = (os.environ.get("FAKE_CLAUDE_TURN_RESULTS") or "").split(",")


def answer(n, count):
    kind = TURN_RESULTS[n - 1] if n <= len(TURN_RESULTS) and TURN_RESULTS[n - 1] else "ok"
    if kind == "error_zero":  # an immediate error, as an expired sign-in ends a turn; stdin stays open after it
        emit({"type": "result", "subtype": "success", "is_error": True, "session_id": sid, "num_turns": 0,
              "result": "OAuth token has expired. Please run /login", "terminal_reason": None, "stop_reason": None})
        return
    if kind == "success_zero":  # a turn that ended with no work
        emit({"type": "result", "subtype": "success", "is_error": False, "session_id": sid, "num_turns": 0,
              "result": "", "terminal_reason": None, "stop_reason": None})
        return
    text = f"{reply} (turn {n}, {count} message(s))"
    emit({"type": "assistant", "parent_tool_use_id": None, "session_id": sid,
          "message": {"model": reply_model, "role": "assistant", "type": "message",
                      "content": [{"type": "text", "text": text}], "usage": {"input_tokens": 3, "output_tokens": 5}}})
    deliver()
    emit({"type": "result", "subtype": "success", "is_error": False, "result": text, "session_id": sid,
          "num_turns": n, "usage": {"input_tokens": 3, "output_tokens": 5},
          "modelUsage": {reply_model: {"inputTokens": 3, "outputTokens": 5}}, "permission_denials": [],
          "stop_reason": "end_turn", "terminal_reason": "completed"})


if stream_in:
    turn, held, count, ended = 1, [], 1, False
    if mode == "gate":  # mid-tool: messages arriving now join this turn (receipt) or wait for the next
        emit({"type": "assistant", "parent_tool_use_id": None, "session_id": sid,
              "message": {"model": model, "role": "assistant", "type": "message",
                          "content": [{"type": "tool_use", "id": "toolu_fake_read", "name": "Read",
                                       "input": {"file_path": "notes.txt"}}]}})
        gate = os.environ["FAKE_CLAUDE_GATE"]
        while not os.path.exists(gate):
            msg = next_message(block=False)
            if msg is None:
                ended = True
                break
            if msg:
                lifecycle(msg)
                if replay_mode == "dequeue":
                    held.append(msg)
                else:
                    replay(msg)
                    count += 1
    if command_result is not None:  # the command ends only once the gate opens
        emit(command_result)
    answer(turn, count)
    while True:
        was_held = bool(held)
        msg = held.pop(0) if held else None if ended else next_message()
        if msg is None:
            sys.exit(0)
        turn += 1
        if not was_held:
            lifecycle(msg)
        replay(msg)
        answer(turn, 1)
emit({"type": "system", "subtype": "thinking_tokens", "session_id": sid})
emit({"type": "assistant", "parent_tool_use_id": None, "session_id": sid,
      "message": {"model": reply_model, "role": "assistant", "type": "message",
                  "content": [{"type": "thinking", "thinking": "PRIVATE-THINKING-TEXT", "signature": "SIG-PRIVATE"},
                              {"type": "text", "text": reply}],
                  "usage": {"input_tokens": 3, "output_tokens": 5}}})
deliver()
emit({"type": "rate_limit_event", "session_id": sid})
emit({"type": "result", "subtype": "success", "is_error": False, "result": reply, "session_id": sid,
      "num_turns": 1, "duration_ms": 5, "duration_api_ms": 4, "total_cost_usd": 0.01,
      "usage": {"input_tokens": 3, "output_tokens": 5},
      "modelUsage": {reply_model: {"inputTokens": 3, "outputTokens": 5}},
      "permission_denials": [{"tool_name": "Read", "tool_use_id": "toolu_fake",
                              "tool_input": {"file_path": "outside/secret.txt"}}] if mode == "denied_read" else [],
      "stop_reason": "end_turn", "terminal_reason": "completed"})
