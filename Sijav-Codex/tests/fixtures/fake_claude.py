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
  orphan           starts a grandchild that inherits stdout and sleeps 60 s (its pid goes to
                   $FAKE_CLAUDE_CHILD_PID), prints the init, and exits 0 without a result
  denied_read      as ok, but the result lists a denied Read
"""

import base64
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
PINNED = HERE / "claude-2.1.286"
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


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
data = sys.stdin.buffer.read()
opts = parse(argv)
log = os.environ.get("FAKE_CLAUDE_LOG")
if log:
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"argv": argv, "stdin_b64": base64.b64encode(data).decode(), "cwd": os.getcwd(),
                            "pid": os.getpid(), "settings_applied": settings_applied(opts),
                            "env": sorted(k for k in os.environ
                                          if k.upper().startswith(("ANTHROPIC_", "CLAUDE_CODE_", "NODE_")))})
                + "\n")

if "--print" not in opts:
    fail("fake: the helper always runs with -p")
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
reply_model = "claude-sonnet-5-5" if mode == "wrong_reply_model" else model
tools = sorted(t for v in (opts.get("--tools") or []) for t in v.split(",") if t)  # variadic in 2.1.286
if mode == "extra_tools":
    tools = sorted(tools + ["Write", "Bash"])
if mode == "extra_safe_tool":
    tools = sorted(tools + ["Task"])
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


def emit(event):
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


def deliver():
    (store / f"{sid}.json").write_text(json.dumps({"turns": 1}), encoding="utf-8")


if mode == "no_init":
    emit({"type": "assistant", "parent_tool_use_id": None, "session_id": sid,
          "message": {"model": model, "content": [{"type": "text", "text": reply}]}})
    emit({"type": "result", "subtype": "success", "is_error": False, "result": reply, "session_id": sid, "num_turns": 1})
    deliver()
    sys.exit(0)
if mode == "init_not_first":
    emit({"type": "rate_limit_event", "session_id": sid})
if mode == "orphan":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], stdout=sys.stdout)
    Path(os.environ["FAKE_CLAUDE_CHILD_PID"]).write_text(str(child.pid), encoding="utf-8")
emit(init)
if mode == "orphan":
    sys.exit(0)
if os.environ.get("FAKE_CLAUDE_PERSIST") == "1":
    deliver()
if mode == "gate":
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
