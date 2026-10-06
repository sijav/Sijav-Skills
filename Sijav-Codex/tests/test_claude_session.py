"""claude_session.py with a strict fake Claude CLI (tests/fixtures/fake_claude.py) in temporary folders.

The fake parses its options from the real Claude Code 2.1.286 help and builds its init from a real
2.1.286 init event (tests/fixtures/claude-2.1.286), rejects unknown flags and choices, and resumes
only sessions it actually created. Every test runs the real helper as a subprocess, as the
orchestrator would, unless it tests one function. No model is called.

Expected arguments and permission rules are written out literally here from Claude Code's
documented syntax (https://code.claude.com/docs/en/permissions), not read back from the helper.
"""

import argparse
import base64
import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

STAGE = Path(__file__).resolve().parents[1]
HELPER = STAGE / "skills" / "claude" / "claude_session.py"
FAKE = STAGE / "tests" / "fixtures" / "fake_claude.py"
sys.path.insert(0, str(HELPER.parent))
import claude_session as cs  # noqa: E402

PROMPT = "Line one\r\nline two with trailing spaces   \n\n\tindented line\nnon-ASCII: \u00e9\u2014\u4e2d\n"
UUID = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
COMMON = ["-p", "--model", "claude-opus-5-5", "--effort", "high", "--safe-mode", "--restricted",
          "--disable-slash-commands", "--strict-mcp-config",
          "--input-format", "text", "--output-format", "stream-json", "--verbose", "--permission-mode", "manual",
          "--permission-prompts", "none"]
SECRETS = ["Read(.env)", "Read(.env.*)", "Read(*.pem)", "Read(*.key)", "Read(~/.ssh/**)", "Read(~/.aws/**)",
           "Read(~/.azure/**)", "Read(~/.gnupg/**)", "Read(~/.kube/**)", "Read(~/.docker/config.json)",
           "Read(~/.claude/**)", "Read(~/.codex/**)", "Read(~/.config/gh/**)", "Read(~/.git-credentials)",
           "Read(~/.netrc)", "Read(~/.npmrc)", "Read(~/.pypirc)"]
PROTECTED = ["Edit(.git/**)", "Edit(.claude/**)", "Edit(.codex/**)", "Edit(.githooks/**)", "Edit(.husky/**)"]
BANNED = ("--continue", "-c", "--last", "--fork-session", "--dangerously-skip-permissions",
          "--allow-dangerously-skip-permissions", "--bare", "--fallback-model", "bypassPermissions", "--settings",
          "--setting-sources", "--add-dir")


def read_pid(path):
    try:
        return int(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def lock_free(path) -> bool:
    """Whether nothing holds the exclusive lock on `path` (taken and given back at once)."""
    with open(path, "r+b") as f:
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(f, fcntl.LOCK_UN)
        except OSError:
            return False
    return True


def wait_lock_free(path, seconds) -> bool:
    deadline = time.monotonic() + seconds
    while not lock_free(path):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)
    return True


CASE_EVIDENCE = "SIJAV_CASE_EVIDENCE_DIR"  # opt-in, for one case only; env() keeps SIJAV_* from the helper


def case_evidence_folder(name):
    """None when CASE_EVIDENCE is unset. Otherwise a new folder, made exclusively, inside the absolute,
    existing folder it names, which must lie outside the folder holding this package. Raises before the
    case starts anything; the folder it names is never created."""
    base = os.environ.get(CASE_EVIDENCE)
    if base is None:
        return None
    if not base or not os.path.isabs(base) or not os.path.isdir(base):
        raise RuntimeError(f"{CASE_EVIDENCE} must name an existing absolute folder, not {base!r}")
    base = Path(base).resolve()
    try:
        base.relative_to(STAGE.parent)
    except ValueError:
        pass
    else:
        raise RuntimeError(f"{CASE_EVIDENCE} must lie outside {STAGE.parent}, not {base}")
    folder = base / f"{name}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{os.getpid()}"
    folder.mkdir()
    print(f"case evidence: {folder}", file=sys.stderr, flush=True)
    return folder


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sijav claude "))
        self.retained = False  # set when what runs in the fixture is unknown: the folder is kept as evidence
        self.addCleanup(lambda: self.retained or shutil.rmtree(self.tmp, ignore_errors=True))
        self.project = self.tmp / "my project"
        (self.project / ".git").mkdir(parents=True)
        self.sub = self.project / "src" / "deep dir"
        self.sub.mkdir(parents=True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.log = self.tmp / "fake-claude.jsonl"
        self.store = self.tmp / "fake store"
        self.rules = self.project / "LAW.md"
        self.rules.write_text("# Law\nG1: tell the truth.\n", encoding="utf-8")
        self.prompt_file = self.tmp / "prompt one.md"
        self.prompt_file.write_bytes(PROMPT.encode("utf-8"))

    def env(self, **extra):
        env = {k: v for k, v in os.environ.items()
               if not k.upper().startswith(("SIJAV_", "FAKE_CLAUDE", "ANTHROPIC_", "CLAUDE_CODE_", "NODE_", "SSL_CERT"))
               and not cs.NOTED_ENV.match(k)}
        env.update(SIJAV_CLAUDE_BIN=str(FAKE), FAKE_CLAUDE_LOG=str(self.log), FAKE_CLAUDE_STORE=str(self.store),
                   USERPROFILE=str(self.home), HOME=str(self.home), PYTHONDONTWRITEBYTECODE="1")
        env.update(extra)
        return {k: v for k, v in env.items() if v is not None}

    def run_helper(self, *args, cwd=None, stdin=None, helper=HELPER, **env):
        return subprocess.run([sys.executable, str(helper), *args], cwd=str(cwd or self.sub),
                              input=stdin, capture_output=True, env=self.env(**env), timeout=180)

    def call(self, *extra, purpose="impl-board", mode="code", rules=True, **env):
        args = ["call", "--purpose", purpose, "--mode", mode, "--prompt-file", str(self.prompt_file), *extra]
        if not (self.project / ".codex" / "claude-sessions" / purpose / "state.json").exists() and "--scope" not in extra:
            args += ["--scope", "function-wide: board implementation"]
        if rules and "--no-project-rules" not in extra and "--rules-file" not in extra:
            args += ["--rules-file", str(self.rules)]
        return self.run_helper(*args, **env)

    def invocations(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def pdir(self, purpose="impl-board"):
        return self.project / ".codex" / "claude-sessions" / purpose

    def cli_tmp(self, purpose="impl-board"):
        """The CLI temp folder the helper sets: under the resolved project, as the helper resolves it."""
        return str(self.project.resolve() / ".codex" / "claude-sessions" / purpose / "cli-tmp")

    def state(self, purpose="impl-board"):
        return json.loads((self.pdir(purpose) / "state.json").read_text(encoding="utf-8"))

    def runs(self, purpose="impl-board"):
        return sorted((self.pdir(purpose) / "runs").iterdir())

    def result(self, run):
        return json.loads((run / "result.json").read_text(encoding="utf-8"))

    def value(self, argv, flag):
        return argv[argv.index(flag) + 1]

    def assert_clean(self, argv):
        for banned in BANNED:
            self.assertNotIn(banned, argv)

    def wait_for(self, predicate, seconds=60):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.05)
        self.fail("timed out waiting")

    def pre_init(self, *lines):
        """A FAKE_CLAUDE_PRE_INIT file: the lines the fake prints before its init."""
        path = self.tmp / f"pre-init-{time.monotonic_ns()}.json"
        path.write_text(json.dumps(list(lines)), encoding="utf-8")
        return str(path)


class FirstCallAndResume(Base):
    def test_first_call_sends_the_pinned_arguments_and_the_exact_prompt(self):
        r = self.call()
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        self.assertEqual(r.stdout, b"fake reply\n", "the reply, with its own line ending, and nothing else")
        self.assertNotIn(b"PRIVATE-THINKING-TEXT", r.stdout + r.stderr)
        self.assertNotIn(b"SIG-PRIVATE", r.stdout + r.stderr)

        [inv] = self.invocations()
        argv = inv["argv"]
        sid = self.value(argv, "--session-id")
        self.assertRegex(sid, UUID)
        self.assertEqual(argv, [*COMMON, "--session-id", sid, "--tools", "Read,Glob,Grep,Write,Edit",
                                "--allowedTools", "Edit(./**)",
                                "--disallowedTools", "NotebookEdit", "TaskOutput", "TaskStop", "Bash", "PowerShell",
                                *PROTECTED, *SECRETS])
        self.assert_clean(argv)
        self.assertEqual(Path(inv["cwd"]).resolve(), self.project.resolve())
        self.assertEqual(inv["controlled_env"], {"CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1",
                                                 "CLAUDE_CODE_TMPDIR": self.cli_tmp(),
                                                 "BASH_DEFAULT_TIMEOUT_MS": None, "BASH_MAX_TIMEOUT_MS": None},
                         "no command tool, so no command bound; background is off and temp files stay in the project")
        self.assertTrue((self.pdir() / "cli-tmp").is_dir())
        st = self.state()
        self.assertEqual((st["status"], st["session_id"], st["rules_delivered"]), ("ready", sid, True))

        sent = base64.b64decode(inv["stdin_b64"])
        self.assertTrue(sent.endswith(PROMPT.encode("utf-8")), "the prompt reaches the CLI byte for byte")
        self.assertIn(b"G1: tell the truth.", sent)
        self.assertIn(str(self.rules.resolve()).encode("utf-8"), sent)
        self.assertEqual(sent, cs.compose([(str(self.rules.resolve()), self.rules.read_bytes())], PROMPT.encode("utf-8")))
        self.assertNotIn(b"<command-guidance>", sent)

        [run] = self.runs()
        self.assertNotIn("command_guidance_sha256", json.loads((run / "brief.json").read_text(encoding="utf-8")))
        self.assertEqual((run / "prompt.txt").read_bytes(), sent)
        self.assertIn(b"PRIVATE-THINKING-TEXT", (run / "stdout.jsonl").read_bytes(), "the raw log keeps everything")
        res = self.result(run)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["served_models"], {"init": "claude-opus-5-5", "assistant": ["claude-opus-5-5"],
                                                "usage": ["claude-opus-5-5"]})
        self.assertEqual(res["init"]["claude_code_version"], "2.1.286")
        self.assertFalse(res["cli_version_differs_from_pinned"])
        self.assertEqual(res["init_extra"]["plugins"], ["figma"], "plugins the init still lists are recorded")
        self.assertEqual(res["result_event"]["usage"], {"input_tokens": 3, "output_tokens": 5})
        self.assertTrue(res["session"]["delivered"])
        self.assertNotIn("PRIVATE-THINKING-TEXT", (run / "result.json").read_text(encoding="utf-8"))
        command = json.loads((run / "command.json").read_text(encoding="utf-8"))
        self.assertEqual(command["argv"][1:], [str(FAKE), *argv])
        self.assertEqual(command["helper_sha256"], hashlib.sha256(HELPER.read_bytes()).hexdigest(),
                         "each run names the exact helper code that ran")
        self.assertIsInstance(command["claude_pid"], int)
        if os.name == "nt":
            self.assertEqual(command["process_tree"], "Windows Job Object (kill on close)")
        self.assertEqual((run / "reply.md").read_text(encoding="utf-8"), "fake reply")

    def test_follow_up_resumes_the_same_exact_id_without_rules(self):
        self.assertEqual(self.call().returncode, 0)
        sid = self.state()["session_id"]
        r = self.call("--effort", "xhigh", rules=False)
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        first, second = self.invocations()
        argv = second["argv"]
        self.assertEqual(argv[argv.index("--resume"):argv.index("--resume") + 2], ["--resume", sid])
        self.assertNotIn("--session-id", argv)
        self.assertEqual(self.value(argv, "--effort"), "xhigh")
        self.assert_clean(argv)
        self.assertNotIn(b"G1: tell the truth.", base64.b64decode(second["stdin_b64"]))
        self.assertEqual((self.state()["calls"], self.state()["session_id"]), (2, sid))

    def test_a_resume_the_cli_cannot_find_keeps_the_session(self):
        self.assertEqual(self.call().returncode, 0)
        sid = self.state()["session_id"]
        (self.store / f"{sid}.json").unlink()
        r = self.call(rules=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn(b"No retry", r.stderr)
        st = self.state()
        self.assertEqual((st["status"], st["session_id"], st["failures"]), ("ready", sid, 1))
        self.assertIn(b"No conversation found", (self.runs()[-1] / "stderr.txt").read_bytes())
        self.assertEqual(len(self.invocations()), 2, "no retry, no new conversation")

    def test_an_authentication_failure_on_resume_keeps_the_session(self):
        self.assertEqual(self.call().returncode, 0)
        sid = self.state()["session_id"]
        r = self.call(rules=False, FAKE_CLAUDE_MODE="fail_no_id")
        self.assertEqual(r.returncode, 1)
        self.assertIn(b"(failed, authentication)", r.stderr)
        self.assertEqual((self.state()["status"], self.state()["session_id"]), ("ready", sid))
        self.assertEqual(self.call(rules=False).returncode, 0)
        self.assertEqual(self.value(self.invocations()[-1]["argv"], "--resume"), sid)

    def test_prompt_on_stdin_is_kept_exactly(self):
        r = self.run_helper("call", "--purpose", "p1", "--mode", "code", "--scope", "x", "--no-project-rules",
                            stdin=PROMPT.encode("utf-8"))
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        [inv] = self.invocations()
        self.assertEqual(base64.b64decode(inv["stdin_b64"]), PROMPT.encode("utf-8"))

    def test_first_call_needs_rules_or_an_explicit_no_and_a_scope(self):
        r = self.run_helper("call", "--purpose", "p1", "--mode", "code", "--scope", "x",
                            "--prompt-file", str(self.prompt_file))
        self.assertEqual(r.returncode, 2)
        self.assertIn(b"--rules-file", r.stderr)
        self.assertFalse(self.pdir("p1").exists(), "a refused new purpose leaves no folder")
        r = self.run_helper("call", "--purpose", "p3", "--mode", "code", "--scope", "x", "--no-project-rules",
                            "--resume-unconfirmed", "new", "--prompt-file", str(self.prompt_file))
        self.assertEqual(r.returncode, 2)
        self.assertFalse(self.pdir("p3").exists())
        r = self.run_helper("call", "--purpose", "p2", "--mode", "code", "--no-project-rules",
                            "--prompt-file", str(self.prompt_file))
        self.assertEqual(r.returncode, 2)
        self.assertIn(b"--scope", r.stderr)
        self.assertFalse(self.pdir("p2").exists(), "nothing is created for a refused new purpose")
        self.assertEqual(self.invocations(), [])
        self.assertEqual(self.call(purpose="p1").returncode, 0)
        r = self.call("--scope", "something else", purpose="p1", rules=False)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(len(self.invocations()), 1)


class Unconfirmed(Base):
    def test_a_first_call_without_an_id_stays_unconfirmed_through_a_failed_attempt(self):
        r = self.call(FAKE_CLAUDE_MODE="fail_no_id")
        self.assertEqual(r.returncode, 1)
        self.assertIn(b"(failed, authentication)", r.stderr)
        st = self.state()
        cand = self.value(self.invocations()[0]["argv"], "--session-id")
        self.assertEqual((st["status"], st["candidate_session_id"], st["candidate_reported"], st["session_id"]),
                         ("unconfirmed", cand, False, None))
        self.assertIn(b"Not logged in", (self.runs()[0] / "stderr.txt").read_bytes())

        r = self.call(rules=False)
        self.assertEqual(r.returncode, 5)
        self.assertIn(b"never reported it", r.stderr)
        self.assertEqual(len(self.invocations()), 1)

        r = self.call("--resume-unconfirmed", "existing")
        self.assertEqual(r.returncode, 1, "the CLI has no such conversation")
        st = self.state()
        self.assertEqual((st["status"], st["candidate_session_id"], st["session_id"]), ("unconfirmed", cand, None),
                         "a failed attempt never turns a guessed id into a confirmed one")
        self.assertEqual(self.value(self.invocations()[1]["argv"], "--resume"), cand)

        r = self.call("--resume-unconfirmed", "new", rules=False)
        self.assertEqual(r.returncode, 2, "rules never reached a delivered turn")
        r = self.call("--resume-unconfirmed", "new")
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        argv = self.invocations()[2]["argv"]
        self.assertEqual(self.value(argv, "--session-id"), cand)
        self.assertIn(b"G1: tell the truth.", base64.b64decode(self.invocations()[2]["stdin_b64"]))
        st = self.state()
        self.assertEqual((st["status"], st["session_id"], st["candidate_session_id"], st["rules_delivered"]),
                         ("ready", cand, None, True))
        ids = {self.value(i["argv"], "--session-id" if "--session-id" in i["argv"] else "--resume")
               for i in self.invocations()}
        self.assertEqual(ids, {cand}, "one conversation id throughout")

    def test_an_id_reported_without_a_turn_needs_the_rules_again(self):
        r = self.call(FAKE_CLAUDE_MODE="error_result")
        self.assertEqual(r.returncode, 1)
        self.assertIn(b"OAuth token has expired", r.stderr)
        st = self.state()
        cand = self.value(self.invocations()[0]["argv"], "--session-id")
        self.assertEqual((st["status"], st["candidate_session_id"], st["candidate_reported"], st["rules_delivered"]),
                         ("unconfirmed", cand, True, False))
        status = self.run_helper("status", "--purpose", "impl-board").stdout.decode()
        self.assertIn("--resume-unconfirmed existing is the likely choice", status)
        self.assertIn("the next call needs --rules-file", status)
        r = self.call("--resume-unconfirmed", "existing")
        self.assertEqual(r.returncode, 1, "this fake did not keep a conversation with no turn")
        self.assertEqual(self.state()["status"], "unconfirmed")
        r = self.call("--resume-unconfirmed", "new")
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        self.assertEqual((self.state()["status"], self.state()["session_id"]), ("ready", cand))

    def test_an_existing_conversation_is_resumed_with_the_rules_and_never_recreated(self):
        r = self.call(FAKE_CLAUDE_MODE="error_result", FAKE_CLAUDE_PERSIST="1")
        self.assertEqual(r.returncode, 1)
        cand = self.state()["candidate_session_id"]
        r = self.call("--resume-unconfirmed", "new")
        self.assertEqual(r.returncode, 1, "the CLI refuses a session id already in use")
        self.assertIn(b"already in use", (self.runs()[-1] / "stderr.txt").read_bytes())
        self.assertEqual((self.state()["status"], self.state()["candidate_session_id"]), ("unconfirmed", cand))
        r = self.call("--resume-unconfirmed", "existing")
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        self.assertIn(b"G1: tell the truth.", base64.b64decode(self.invocations()[-1]["stdin_b64"]))
        self.assertEqual((self.state()["status"], self.state()["session_id"]), ("ready", cand))
        self.assertEqual(self.call(rules=False).returncode, 0)

    def test_resume_unconfirmed_on_another_status_is_refused(self):
        self.assertEqual(self.call().returncode, 0)
        r = self.call("--resume-unconfirmed", "new", rules=False)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(len(self.invocations()), 1)


class Modes(Base):
    def test_technical_mode_offers_read_only_tools(self):
        r = self.call(purpose="roast-technical", mode="technical")
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        argv = self.invocations()[-1]["argv"]
        sid = self.value(argv, "--session-id")
        self.assertEqual(argv, [*COMMON, "--session-id", sid, "--tools", "Read,Glob,Grep,WebFetch",
                                "--allowedTools", "WebFetch",
                                "--disallowedTools", "Write", "Edit", "NotebookEdit", "Bash", "PowerShell",
                                "TaskOutput", "TaskStop", *SECRETS])
        self.assertEqual(self.invocations()[-1]["controlled_env"]["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"], "1")
        sent = base64.b64decode(self.invocations()[-1]["stdin_b64"])
        self.assertEqual(sent, cs.compose([(str(self.rules.resolve()), self.rules.read_bytes())], PROMPT.encode("utf-8")))
        self.assertNotIn(b"<command-guidance>", sent)
        brief = json.loads((self.runs("roast-technical")[0] / "brief.json").read_text(encoding="utf-8"))
        self.assertNotIn("command_guidance_sha256", brief)

    def test_technical_mode_refuses_command_rules_and_no_option_picks_a_model(self):
        r = self.call("--allow-command", "Bash(python -m unittest *)", purpose="roast-technical", mode="technical")
        self.assertEqual(r.returncode, 2)
        self.assertIn(b"read-only", r.stderr)
        r = self.call("--model", "claude-sonnet-5-5")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.invocations(), [])

    def test_a_purpose_keeps_its_mode(self):
        self.assertEqual(self.call(purpose="roast-technical", mode="technical").returncode, 0)
        r = self.call(purpose="roast-technical", mode="code", rules=False)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(len(self.invocations()), 1)

    def test_code_mode_grants_only_scoped_commands(self):
        r = self.call("--allow-command", "Bash(python -m unittest *)", "--allow-command", "Bash(npm run test:*)")
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        argv = self.invocations()[-1]["argv"]
        self.assertEqual(self.value(argv, "--tools"), "Read,Glob,Grep,Write,Edit,Bash")
        allowed = argv[argv.index("--allowedTools") + 1:argv.index("--disallowedTools")]
        self.assertEqual(allowed, ["Edit(./**)", "Bash(python -m unittest *)", "Bash(npm run test:*)"])
        denied = argv[argv.index("--disallowedTools") + 1:]
        self.assertNotIn("Bash", denied)
        self.assertIn("PowerShell", denied)
        self.assertEqual(denied[:3], ["NotebookEdit", "TaskOutput", "TaskStop"])
        self.assertEqual(self.invocations()[-1]["controlled_env"],
                         {"CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1", "CLAUDE_CODE_TMPDIR": self.cli_tmp(),
                          "BASH_DEFAULT_TIMEOUT_MS": "600000", "BASH_MAX_TIMEOUT_MS": "600000"},
                         "default bound: the smaller of 600 s and half of the 3600 s call timeout")
        for bad in ("Bash", "Bash(*)", "Bash( * )", "Bash(:*)", "Python(x)", "Bash(a)(b)", "Bash(* --version)",
                    "Bash(command:rm *)", "Bash(py* -m x)"):
            r = self.call("--allow-command", bad, purpose="p-bad")
            self.assertEqual(r.returncode, 2, bad)
        self.assertEqual(len(self.invocations()), 1)

    RULE = ("--allow-command", "Bash(python stage.py *)")

    def test_a_finite_foreground_command_is_ok_and_recorded(self):
        r = self.call(*self.RULE, FAKE_CLAUDE_COMMAND="foreground")
        self.assertEqual((r.returncode, r.stdout), (0, b"fake reply\n"), r.stderr.decode())
        res = self.result(self.runs()[0])
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["commands"]["tasks"][0]["is_backgrounded"], False)
        self.assertEqual((res["commands"]["background_breach"], res["commands"]["background_requests"]), ([], 0))
        command = json.loads((self.runs()[0] / "command.json").read_text(encoding="utf-8"))
        self.assertEqual(command["set_environment"]["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"], "1")
        self.assertEqual(command["set_environment"]["BASH_MAX_TIMEOUT_MS"], "600000")
        brief = json.loads((self.runs()[0] / "brief.json").read_text(encoding="utf-8"))
        self.assertEqual((brief["command_timeout_seconds"], brief["command_timeout_source"][:8]), (600, "default:"))


    def test_command_guidance_is_sent_with_exact_prompt_suffix_and_one_independent_input_hash(self):
        for seconds, outer in ((5400, 7200), (20, 3600)):
            with self.subTest(seconds=seconds):
                purpose = f"guided-{seconds}"
                r = self.call(*self.RULE, "--command-timeout", str(seconds), "--timeout", str(outer), purpose=purpose)
                self.assertEqual(r.returncode, 0, r.stderr.decode())
                sent = base64.b64decode(self.invocations()[-1]["stdin_b64"])
                self.assertEqual(sent[-len(PROMPT.encode("utf-8")):], PROMPT.encode("utf-8"))
                self.assertEqual(sent.count(b"<command-guidance>"), 1)
                start = sent.index(b"<command-guidance>")
                end = sent.index(b"</command-guidance>") + len(b"</command-guidance>\n\n")
                guidance = sent[start:end]
                self.assertIn(b"G1: tell the truth.", sent[:start], "the original first-use rules remain first")
                self.assertEqual(sent[end:], PROMPT.encode("utf-8"))
                self.assertIn(f"{seconds} s ({seconds * 1000} ms)".encode(), guidance)
                self.assertIn(f"{outer} s deadline from launch".encode(), guidance)
                for words in (b"Omitting the tool's timeout parameter uses the configured default",
                              b"a smaller explicit timeout", b"actual offered limit has not been verified",
                              b"remaining call lifetime is not known", b"grants no new command authority",
                              b"report that before starting"):
                    self.assertIn(words, guidance)
                run = self.runs(purpose)[0]
                brief = json.loads((run / "brief.json").read_text(encoding="utf-8"))
                self.assertEqual(brief["command_guidance_sha256"], hashlib.sha256(guidance).hexdigest())
                self.assertEqual(brief["prompt_sha256"], hashlib.sha256(PROMPT.encode("utf-8")).hexdigest())
                self.assertEqual(brief["sent_sha256"], hashlib.sha256(sent).hexdigest())
                self.assertEqual((run / "prompt.txt").read_bytes(), sent)
                inv = self.invocations()[-1]
                self.assertEqual(inv["controlled_env"]["BASH_DEFAULT_TIMEOUT_MS"], str(seconds * 1000))
                self.assertEqual(inv["controlled_env"]["BASH_MAX_TIMEOUT_MS"], str(seconds * 1000))

    def test_default_and_resumed_command_guidance_preserve_the_original_suffix_without_repeating_rules(self):
        r = self.call(*self.RULE)
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        initial = base64.b64decode(self.invocations()[-1]["stdin_b64"])
        self.assertIn(b"600 s (600000 ms), from default:", initial)
        r = self.call(*self.RULE, "--command-timeout", "45", rules=False)
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        sent = base64.b64decode(self.invocations()[-1]["stdin_b64"])
        self.assertTrue(sent.startswith(b"<command-guidance>\n"))
        self.assertNotIn(b"G1: tell the truth.", sent)
        self.assertEqual(sent.split(b"</command-guidance>\n\n", 1)[1], PROMPT.encode("utf-8"))
        brief = json.loads((self.runs()[-1] / "brief.json").read_text(encoding="utf-8"))
        guidance = sent[:-len(PROMPT.encode("utf-8"))]
        self.assertEqual(brief["command_guidance_sha256"], hashlib.sha256(guidance).hexdigest())

    def test_no_command_call_has_no_guidance_or_new_guidance_hash(self):
        r = self.call("--no-project-rules")
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        self.assertEqual(base64.b64decode(self.invocations()[-1]["stdin_b64"]), PROMPT.encode("utf-8"))
        brief = json.loads((self.runs()[0] / "brief.json").read_text(encoding="utf-8"))
        self.assertNotIn("command_guidance_sha256", brief)

    def test_a_background_request_the_cli_runs_in_the_foreground_is_not_a_breach(self):
        r = self.call(*self.RULE, FAKE_CLAUDE_COMMAND="background")
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        res = self.result(self.runs()[0])
        self.assertEqual((res["status"], res["commands"]["background_requests"]), ("ok", 1))
        self.assertEqual(res["commands"]["background_breach"], [])

    def test_a_started_background_task_makes_a_successful_run_incomplete(self):
        for i, command in enumerate(("background", "auto")):
            with self.subTest(command=command):
                purpose = f"p-{i}"
                r = self.call(*self.RULE, purpose=purpose, FAKE_CLAUDE_COMMAND=command, FAKE_CLAUDE_IGNORE_DISABLE="1")
                self.assertEqual((r.returncode, r.stdout), (1, b""), "never printed as a successful answer")
                self.assertIn(b"(incomplete, background breach)", r.stderr)
                res = self.result(self.runs(purpose)[0])
                self.assertEqual((res["status"], res["exit_code"], res["result_event"]["subtype"]),
                                 ("incomplete", 0, "success"), "incomplete although the CLI exited 0 with success")
                self.assertIn("background commands are disabled", res["cause"])
                evidence = {b["evidence"] for b in res["commands"]["background_breach"]}
                self.assertEqual(evidence, {"tool_use_result.backgroundTaskId"} | (
                    {"task_started is_backgrounded"} if command == "background" else set()))
                st = self.state(purpose)
                self.assertEqual((st["status"], st["calls"], st["failures"]), ("ready", 0, 1),
                                 "the delivered turn keeps the session; the call counts as failed")

    def test_an_init_listing_a_denied_task_tool_is_refused_before_any_work(self):
        for i, extra in enumerate(("TaskStop", "TaskOutput")):
            with self.subTest(extra=extra):
                purpose = f"p-{i}"
                r = self.call(*self.RULE, purpose=purpose, FAKE_CLAUDE_MODE="sleep", FAKE_CLAUDE_EXTRA_TOOLS=extra)
                self.assertEqual((r.returncode, r.stdout), (1, b""))
                res = self.result(self.runs(purpose)[0])
                self.assertEqual(res["status"], "refused")
                self.assertIn(f"tools that were not granted: ['{extra}']", res["cause"])

    def test_command_bounds_are_resolved_or_refused_before_anything_is_created(self):
        for args, words in (((*self.RULE, "--command-timeout", "0"), b"at least 1 and below --timeout"),
                            ((*self.RULE, "--command-timeout", "30", "--timeout", "30"), b"below --timeout 30"),
                            ((*self.RULE, "--timeout", "1"), b"leaves no room to bound a command"),
                            (("--command-timeout", "30"), b"this call offers none")):
            with self.subTest(args=args):
                r = self.call(*args, purpose="p-bad")
                self.assertEqual(r.returncode, 2, r.stderr.decode())
                self.assertIn(words, r.stderr)
                self.assertFalse(self.pdir("p-bad").exists(), "nothing was created")
        r = self.call("--command-timeout", "30", purpose="roast-technical", mode="technical")
        self.assertEqual(r.returncode, 2, "technical mode has no command tools")
        self.assertEqual(self.invocations(), [], "nothing was launched")
        r = self.call(*self.RULE, "--command-timeout", "45", "--timeout", "90", purpose="p-explicit")
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        self.assertEqual(self.invocations()[-1]["controlled_env"]["BASH_DEFAULT_TIMEOUT_MS"], "45000")
        r = self.call(*self.RULE, "--timeout", "40", purpose="p-short")
        self.assertEqual(r.returncode, 0, "a short call timeout still runs, with a bound of half of it")
        self.assertEqual(self.invocations()[-1]["controlled_env"]["BASH_MAX_TIMEOUT_MS"], "20000")
        self.assertEqual(cs.command_timeout("code", [], 1, None), (None, "no command tools"),
                         "a call with no command tool needs no command bound, however short its timeout")
        self.assertEqual(cs.command_timeout("technical", [], 3600, None), (None, "no command tools"))

    def test_inherited_controlled_variables_are_replaced_and_recorded(self):
        outside = str(self.tmp / "outside temp")
        r = self.call(*self.RULE, "--command-timeout", "10", CLAUDE_CODE_DISABLE_BACKGROUND_TASKS="0",
                      CLAUDE_CODE_TMPDIR=outside, BASH_DEFAULT_TIMEOUT_MS="99999999", BASH_MAX_TIMEOUT_MS="99999999")
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        self.assertEqual(self.invocations()[-1]["controlled_env"],
                         {"CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1", "CLAUDE_CODE_TMPDIR": self.cli_tmp(),
                          "BASH_DEFAULT_TIMEOUT_MS": "10000", "BASH_MAX_TIMEOUT_MS": "10000"})
        command = json.loads((self.runs()[0] / "command.json").read_text(encoding="utf-8"))
        self.assertEqual(command["replaced_inherited_environment"],
                         ["BASH_DEFAULT_TIMEOUT_MS", "BASH_MAX_TIMEOUT_MS", "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS",
                          "CLAUDE_CODE_TMPDIR"])
        self.assertNotIn(outside, command["set_environment"].values(), "an inherited outside path is replaced")
        r = self.call(purpose="roast-technical", mode="technical", BASH_MAX_TIMEOUT_MS="99999999")
        self.assertEqual(r.returncode, 0)
        self.assertIsNone(self.invocations()[-1]["controlled_env"]["BASH_MAX_TIMEOUT_MS"],
                          "no command tool: an inherited bound is removed, none is set")

    def test_every_argument_shape_parses_under_the_pinned_2_1_286_help(self):
        sid = "bd063f17-0778-4a84-b008-962d2ef7d32f"
        shapes = [(cs.build_argv([sys.executable, str(FAKE)], mode, effort, allow, session, follow_ups), follow_ups)
                  for mode, allow in (("technical", []), ("code", []), ("code", ["Bash(python -m unittest *)"]),
                                      ("code", ["PowerShell(Get-ChildItem *)"]))
                  for effort in cs.EFFORTS for session in (["--session-id", sid],) for follow_ups in (False, True)]
        env = self.env(FAKE_CLAUDE_MODE="garbage")
        for argv, follow_ups in shapes:
            r = subprocess.run(argv, input=b"" if follow_ups else b"x", capture_output=True, env=env,
                               cwd=str(self.project), timeout=60)
            self.assertEqual((r.returncode, r.stderr), (0, b""), argv)
        managed = cs.build_argv([], "technical", "high", [], ["--session-id", sid], follow_ups=True)
        self.assertEqual(managed[:managed.index("--session-id")],
                         [*COMMON[:COMMON.index("--input-format")], "--input-format", "stream-json",
                          "--output-format", "stream-json", "--verbose", "--replay-user-messages",
                          "--permission-mode", "manual", "--permission-prompts", "none"],
                         "a managed call changes only its input format and asks for replays")
        r = subprocess.run([sys.executable, str(FAKE), "-p", "--replay-user-messages"], input=b"x",
                           capture_output=True, env=env, cwd=str(self.project), timeout=60)
        self.assertIn(b"requires --input-format=stream-json", r.stderr)

    def test_the_pinned_parser_rejects_what_2_1_286_would(self):
        env = self.env()
        for argv, words in ((["-p", "--bogus"], b"unknown option '--bogus'"),
                            (["-p", "--permission-mode", "default"], b"argument 'default' is invalid"),
                            (["-p", "--effort", "extreme"], b"argument 'extreme' is invalid"),
                            (["-p", "--session-id", "not-a-uuid"], b"Must be a valid UUID"),
                            (["-p", "--resume", "bd063f17-0778-4a84-b008-962d2ef7d32f"], b"No conversation found")):
            r = subprocess.run([sys.executable, str(FAKE), *argv], input=b"x", capture_output=True, env=env,
                               cwd=str(self.project), timeout=60)
            self.assertEqual(r.returncode, 1, argv)
            self.assertIn(words, r.stderr)

    def assert_refused_quickly(self, words, purpose="impl-board", mode="code", **env):
        started = time.monotonic()
        r = self.call(purpose=purpose, mode=mode, **env)
        self.assertLess(time.monotonic() - started, 45, "stopped at its first events, not after 60 s")
        self.assertEqual(r.returncode, 1, r.stderr.decode())
        self.assertEqual(r.stdout, b"")
        res = self.result(self.runs(purpose)[-1])
        self.assertEqual(res["status"], "refused")
        self.assertIn(words, res["cause"])
        self.assertEqual(len(self.invocations()), 1, "no retry, no other model")
        self.log.unlink()

    def test_an_unexpected_init_stops_the_cli_before_it_acts(self):
        cases = [("technical", "extra_tools", "tools that were not granted: ['Bash', 'Write']"),
                 ("code", "extra_safe_tool", "tools that were not granted: ['Task']"),
                 ("code", "mcp", "loaded MCP servers"),
                 ("code", "accept_edits", "permission mode 'acceptEdits'"),
                 ("code", "api_key", "apiKeySource 'ANTHROPIC_API_KEY'"),
                 ("code", "wrong_model", "started on model 'claude-sonnet-5-5', not claude-opus-5-5"),
                 ("code", "init_not_first", "first event was 'rate_limit_event'"),
                 ("code", "no_init", "first event was 'assistant'")]
        for i, (mode, fake, words) in enumerate(cases):
            with self.subTest(fake=fake):
                self.assert_refused_quickly(words, purpose=f"p-{i}", mode=mode, FAKE_CLAUDE_MODE=fake)

    def test_a_non_uuid_session_id_from_the_cli_is_refused(self):
        r = self.call(FAKE_CLAUDE_MODE="bad_session_id")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, b"")
        self.assertIn("'-c'", self.result(self.runs()[-1])["cause"])

    def test_reply_from_another_model_is_not_used(self):
        r = self.call(FAKE_CLAUDE_MODE="wrong_reply_model")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, b"")
        res = self.result(self.runs()[0])
        self.assertEqual(res["status"], "wrong_model")
        self.assertEqual(res["served_models"]["assistant"], ["claude-sonnet-5-5"])
        self.assertTrue((self.runs()[0] / "reply.md").exists(), "kept in the record, not printed")
        self.assertEqual(self.state()["calls"], 0)

    def test_provider_runtime_and_tls_settings_are_not_inherited_but_oauth_and_networks_are(self):
        r = self.call(ANTHROPIC_API_KEY="sk-test-must-not-pass", ANTHROPIC_BASE_URL="https://proxy.invalid",
                      CLAUDE_CODE_USE_BEDROCK="1", CLAUDE_CODE_OAUTH_TOKEN="oauth-kept",
                      NODE_OPTIONS="--require ./evil.js", NODE_TLS_REJECT_UNAUTHORIZED="0",
                      HTTPS_PROXY="http://user:pw@corp-proxy.invalid:8080", CLAUDE_CONFIG_DIR=str(self.tmp / "cfg"))
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        [inv] = self.invocations()
        self.assertEqual(inv["env"], ["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS", "CLAUDE_CODE_OAUTH_TOKEN",
                                      "CLAUDE_CODE_TMPDIR"])
        run = self.runs()[0]
        command = json.loads((run / "command.json").read_text(encoding="utf-8"))
        self.assertEqual(command["dropped_environment"], ["ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL",
                                                          "CLAUDE_CODE_USE_BEDROCK", "NODE_OPTIONS",
                                                          "NODE_TLS_REJECT_UNAUTHORIZED"])
        self.assertEqual(command["kept_environment_noted"], ["CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CONFIG_DIR",
                                                             "HTTPS_PROXY"])
        for p in run.iterdir():
            for secret in (b"sk-test-must-not-pass", b"oauth-kept", b"user:pw", b"evil.js"):
                self.assertNotIn(secret, p.read_bytes(), p.name)

    def test_project_settings_files_cannot_widen_approvals_or_redirect_the_provider(self):
        (self.project / ".claude").mkdir()
        (self.project / ".claude" / "settings.json").write_text(json.dumps(
            {"permissions": {"allow": ["Bash", "Edit(//**)"]}, "env": {"ANTHROPIC_BASE_URL": "https://evil.invalid"}}),
            encoding="utf-8")
        r = self.call("--allow-command", "Bash(python -m unittest *)")
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        [inv] = self.invocations()
        self.assertEqual(inv["settings_applied"], [], "--restricted: the project's settings file is ignored")
        r = subprocess.run([sys.executable, str(FAKE), "-p", "--model", "claude-opus-5-5"], input=b"x",
                           capture_output=True, env=self.env(), cwd=str(self.project), timeout=60)
        self.assertEqual(self.invocations()[-1]["settings_applied"],
                         ["allow Bash", "allow Edit(//**)", "env ANTHROPIC_BASE_URL"],
                         "without --restricted the same file would apply")

    def test_denied_tool_uses_are_reported_not_hidden(self):
        r = self.call(purpose="roast-technical", mode="technical", FAKE_CLAUDE_MODE="denied_read")
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        self.assertIn(b"the CLI denied 1 tool use(s): ['Read']", r.stderr)
        res = self.result(self.runs("roast-technical")[0])
        self.assertEqual(res["permission_denials"][0]["tool_name"], "Read")


class Failures(Base):
    def test_switched_off_sends_nothing_and_creates_nothing(self):
        r = self.call(SIJAV_CLAUDE="off")
        self.assertEqual(r.returncode, 3)
        self.assertIn(b"CLAUDE OFF", r.stderr)
        self.assertEqual(self.invocations(), [])
        self.assertFalse((self.project / ".codex").exists())

    def test_output_that_is_not_json_fails_with_its_log(self):
        r = self.call(FAKE_CLAUDE_MODE="garbage")
        self.assertEqual(r.returncode, 1)
        self.assertIn(b"never sent its init event", r.stderr)
        run = self.runs()[0]
        self.assertEqual((run / "stdout.jsonl").read_bytes().replace(b"\r\n", b"\n"), b"this is not json\n")
        self.assertEqual(self.result(run)["unparsed_stdout_lines"], 1)
        self.assertEqual((self.state()["status"], self.state()["candidate_reported"]), ("unconfirmed", False))

    def test_timeout_stops_the_process_tree_and_keeps_its_output(self):
        r = self.call("--timeout", "2", FAKE_CLAUDE_MODE="sleep")
        self.assertEqual(r.returncode, 1)
        run = self.runs()[0]
        res = self.result(run)
        self.assertEqual((res["status"], res["timed_out"], res["stopped_by_helper"]), ("timeout", True, "timeout"))
        self.assertIn(b'"subtype": "init"', (run / "stdout.jsonl").read_bytes())
        self.assertEqual((self.state()["status"], self.state()["candidate_reported"]), ("unconfirmed", True))

    def release_holder(self, holder):
        """The orphan fixture's own end: its release file, then its lifetime lock taken within 10 s.
        Nothing is signalled. If the lock is still held, the holder's state is unknown: the fixture
        folder is kept and this cleanup fails on its own, apart from the test's result."""
        (holder / "release").write_text("", encoding="utf-8")
        lock = holder / "holder.lock"
        if lock.exists() and wait_lock_free(lock, 10):
            return
        self.retained = True
        raise RuntimeError(f"cleanup: the orphan fixture's holder lock was {'still held' if lock.exists() else 'never'}"
                           f" taken 10 s after its release file was written; its state is unknown, so {self.tmp}"
                           " is kept")

    def keep_case(self, folder, stage, got, launcher_file, holder, summary=None):
        """The opted-in case's evidence, each file written once and exclusively into `folder`: at the "call"
        stage before its assertions, and at the "end" stage after the holder's release and before the
        fixture folder is removed. The helper's bytes and byte copies of the run's and the fixture's files
        are made once, at the first stage that runs. Liveness is observation only (a pid may be reused);
        nothing is signalled. A failed write keeps the fixture folder, and the end stage then fails as a
        cleanup, apart from the case's result."""
        errors = got.setdefault("errors", [])

        def write(name, data):
            try:
                with open(folder / name, "xb") as f:
                    f.write(data)
            except Exception as exc:
                errors.append(f"{name}: {type(exc).__name__}: {exc}")
                self.retained = True

        def observe(fn, *args):
            try:
                return fn(*args)
            except Exception as exc:
                return f"{type(exc).__name__}: {exc}"

        record = {"stage": stage, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "fixture": str(self.tmp)}
        if got.get("copied"):
            record["files"] = f"copied at the {got['copied']} stage"
        else:
            got["copied"], files = stage, {}
            out = got.get("r", got.get("timeout"))  # the helper's exact pipe bytes, never decoded
            for name in ("stdout", "stderr"):
                data = getattr(out, name, None)
                if data is None:
                    files[f"helper.{name}"] = "absent: the call neither returned nor timed out" if out is None \
                        else "absent: not captured"
                    continue
                write(f"helper.{name}", data)
                files[f"helper.{name}"] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            runs = sorted((self.pdir() / "runs").glob("*"))
            record["runs"] = [p.name for p in runs]
            sources = [(f"run.{n}", runs[0] / n) for n in ("result.json", "stdout.jsonl", "stderr.txt", "command.json")
                       ] if runs else []
            sources += [("launcher.pid", launcher_file), ("holder.pid", holder / "holder.pid"),
                        ("holder.ppid", holder / "holder.ppid"), ("fake.job.json", holder / "fake.job.json"),
                        ("holder.job.json", holder / "holder.job.json")]
            for name, path in sources:
                try:
                    data = path.read_bytes()
                except FileNotFoundError:
                    files[name] = "absent"
                    continue
                except Exception as exc:
                    files[name] = f"unreadable: {type(exc).__name__}: {exc}"
                    continue
                write(name, data)
                files[name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            record["files"] = files
        if summary is not None:
            record["summary"] = json.loads(summary)
        if stage == "end":
            lock = holder / "holder.lock"
            record.update(
                # Kept by the release when the holder's lock was still held, or by a failed capture.
                fixture_kept=self.retained,
                holder_lock_free=observe(lock_free, lock) if lock.exists() else None,
                launcher_alive=observe(cs.pid_alive, read_pid(launcher_file)),
                holder_alive=observe(cs.pid_alive, read_pid(holder / "holder.pid")))
        record["capture_errors"] = list(errors)
        write(f"{stage}.json", json.dumps(record, indent=2).encode("utf-8"))
        if stage == "end" and errors:
            self.retained = True
            raise RuntimeError(f"case evidence: {len(errors)} capture error(s) in {folder}: {errors}; {self.tmp} is kept")

    def test_a_descendant_holding_stdout_cannot_hang_the_call(self):
        kept, got = case_evidence_folder("original-caller-case"), {}
        launcher_file, holder = self.tmp / "launcher.pid", self.tmp / "holder"
        holder.mkdir()
        if kept:  # runs after the release below and before the fixture folder's removal
            self.addCleanup(self.keep_case, kept, "end", got, launcher_file, holder)
        self.addCleanup(self.release_holder, holder)
        started = time.monotonic()
        try:
            r = self.call("--timeout", "120", FAKE_CLAUDE_MODE="orphan", FAKE_CLAUDE_CHILD_PID=str(launcher_file),
                          FAKE_CLAUDE_HOLDER=str(holder))
        except subprocess.TimeoutExpired as exc:  # its output is kept by the end stage
            got["timeout"] = exc
            raise
        got["r"] = r
        elapsed = time.monotonic() - started
        # What this call left is read before any assertion, so a failure reports it. A pid that is alive
        # may be a reused one; the holder's lock is its own instance's.
        runs = sorted((self.pdir() / "runs").glob("*"))
        res = self.result(runs[0]) if runs and (runs[0] / "result.json").exists() else {}
        launcher, holder_pid = read_pid(launcher_file), read_pid(holder / "holder.pid")
        evidence = json.dumps({
            "seconds": round(elapsed, 1), "exit": r.returncode, "stderr_tail": r.stderr.decode("utf-8", "replace")[-300:],
            **{k: res.get(k) for k in ("status", "process_tree", "process_tree_stops", "output_drained",
                                       "stopped_by_helper", "exit_code")},
            "cause": str(res.get("cause"))[:300], "launcher_pid": launcher, "launcher_alive": cs.pid_alive(launcher),
            "holder_pid": holder_pid, "holder_alive": cs.pid_alive(holder_pid),
            # Observations only: the holder's own parent, and whether the launcher role is the holder itself.
            "holder_parent_pid": read_pid(holder / "holder.ppid"),
            "launcher_is_holder": launcher is not None and launcher == holder_pid,
            "holder_lock_free": lock_free(holder / "holder.lock") if (holder / "holder.lock").exists() else None})
        if kept:
            try:
                self.keep_case(kept, "call", got, launcher_file, holder, summary=evidence)
            except Exception as exc:  # a capture never replaces the case's verdict; the end stage reports it
                got.setdefault("errors", []).append(f"call: {type(exc).__name__}: {exc}")
                self.retained = True
        self.assertLess(elapsed, 40, "the call ends when the CLI ends, not after 60 s; " + evidence)
        self.assertEqual(r.returncode, 1, evidence)
        self.assertIn("without a result event", str(res.get("cause")), evidence)
        if res.get("process_tree") == "Windows Job Object (kill on close)":
            close = next((s for s in res.get("process_tree_stops") or [] if s.get("phase") == "close"), {})
            total = close.get("total_processes")
            self.assertTrue(isinstance(total, int) and total >= 2,
                            f"the Job's lifetime count was {total!r}; at least 2 is required for this fixture's"
                            " containment check (count 1 was measured with a venv made from a Microsoft Store"
                            " Python); " + evidence)
        self.assertTrue(res.get("output_drained"), evidence)
        self.assertTrue(wait_lock_free(holder / "holder.lock", 10),
                        "the CLI's leftover descendant that held stdout was stopped with the call; " + evidence)
        deadline = time.monotonic() + 10
        while cs.pid_alive(launcher) and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertFalse(cs.pid_alive(launcher), "the CLI's leftover descendant was stopped with the call; " + evidence)

    def test_resume_reporting_another_id_is_a_conflict(self):
        self.assertEqual(self.call().returncode, 0)
        sid = self.state()["session_id"]
        r = self.call(rules=False, FAKE_CLAUDE_MODE="resume_new_id")
        self.assertEqual(r.returncode, 1)
        st = self.state()
        self.assertEqual((st["status"], st["session_id"]), ("conflict", sid))
        self.assertEqual(self.call(rules=False).returncode, 5)
        self.assertEqual(len(self.invocations()), 2)

    def write_state(self, data, purpose="impl-board"):
        self.pdir(purpose).mkdir(parents=True, exist_ok=True)
        text = data if isinstance(data, str) else json.dumps(data)
        (self.pdir(purpose) / "state.json").write_text(text, encoding="utf-8")
        return text

    def good_state(self, **change):
        st = cs.new_state("impl-board", "code", "s", self.project.resolve())
        st.update(status="ready", session_id="bd063f17-0778-4a84-b008-962d2ef7d32f", rules_delivered=True, calls=1)
        st.update(change)
        return st

    def test_tampered_copied_or_malformed_state_is_refused_untouched(self):
        cases = {"not json": "{not json",
                 "another model": self.good_state(model="claude-sonnet-5-5"),
                 "an option as session id": self.good_state(session_id="-c"),
                 "--continue as session id": self.good_state(session_id="--continue"),
                 "another purpose's state": self.good_state(purpose="impl-other"),
                 "another project's state": self.good_state(project_root=str(self.tmp / "elsewhere")),
                 "missing counters": {k: v for k, v in self.good_state().items() if k not in ("failures", "scope")},
                 "running without its run": self.good_state(status="running", running={"run_dir": "x"})}
        for name, data in cases.items():
            with self.subTest(name):
                text = self.write_state(data)
                r = self.call(rules=False)
                self.assertEqual(r.returncode, 5, r.stderr.decode())
                self.assertNotIn(b"Traceback", r.stderr)
                self.assertEqual((self.pdir() / "state.json").read_text(encoding="utf-8"), text)
        self.assertEqual(self.invocations(), [])
        r = self.run_helper("list")
        self.assertEqual(r.returncode, 0)
        self.assertIn(b"UNREADABLE", r.stdout)

    def test_missing_cli_is_reported(self):
        r = self.call(SIJAV_CLAUDE_BIN=str(self.tmp / "no-such-claude.exe"))
        self.assertEqual(r.returncode, 1)
        self.assertIn(b"does not exist", r.stderr)
        self.assertFalse((self.project / ".codex").exists())


class OutputSettlement(Base):
    def unsettled_helper(self):
        """A labelled double: the real helper runs the real (fake) CLI through its real launch, and only the
        observed settlement is then marked unended. No process is left holding the output, and nothing
        here is evidence about native containment."""
        wrapper = self.tmp / "helper_unsettled_output.py"
        wrapper.write_text(f"import sys\nsys.path.insert(0, {str(HELPER.parent)!r})\nimport claude_session as cs\n"
                           "real = cs.launch\n\n\ndef launch(*args, **kwargs):\n"
                           "    got = real(*args, **kwargs)\n"
                           "    got['output_settlement'] = dict(got['output_settlement'] or {}, ended=False)\n"
                           "    got['drained'] = False\n"
                           "    return got\n\n\n"
                           "cs.launch = launch\nsys.exit(cs.main())\n", encoding="utf-8")
        return wrapper

    def test_a_success_whose_output_did_not_end_is_incomplete_and_keeps_its_reply_and_session(self):
        r = self.call(helper=self.unsettled_helper())
        self.assertEqual((r.returncode, r.stdout), (1, b""), "never printed as a successful answer")
        self.assertIn(b"(incomplete, output not settled)", r.stderr)
        self.assertIn(b"had not ended 10 s after the CLI exited", r.stderr)
        res = self.result(self.runs()[0])
        self.assertEqual((res["status"], res["kind"], res["exit_code"], res["result_event"]["subtype"]),
                         ("incomplete", "output not settled", 0, "success"))
        self.assertEqual((res["output_drained"], res["output_settlement"]["ended"],
                          res["output_settlement"]["waited_seconds"]), (False, False, 10.0))
        self.assertIn("cause not determined", res["cause"])
        self.assertIn("kept in the run's reply.md", res["cause"])
        self.assertEqual((self.runs()[0] / "reply.md").read_text(encoding="utf-8"), "fake reply")
        [inv] = self.invocations()
        sid = self.value(inv["argv"], "--session-id")
        st = self.state()
        self.assertEqual((st["status"], st["session_id"], st["calls"], st["failures"]), ("ready", sid, 0, 1),
                         "the delivered turn keeps the session; the call counts as failed")
        r = self.call(rules=False)
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        self.assertEqual(len(self.invocations()), 2, "nothing was retried")
        self.assertEqual(self.value(self.invocations()[1]["argv"], "--resume"), sid, "the same conversation goes on")

    def test_a_failure_whose_output_did_not_end_says_so_too(self):
        settled = self.call(purpose="p-a", FAKE_CLAUDE_MODE="error_result")
        unsettled = self.call(purpose="p-b", FAKE_CLAUDE_MODE="error_result", helper=self.unsettled_helper())
        self.assertEqual((settled.returncode, unsettled.returncode, unsettled.stdout), (1, 1, b""))
        a, b = self.result(self.runs("p-a")[0]), self.result(self.runs("p-b")[0])
        self.assertEqual((b["status"], b["kind"]), (a["status"], a["kind"]))
        self.assertEqual(b["cause"], a["cause"] + "; also: the CLI's output had not ended 10 s after the CLI exited"
                         " (cause not determined: a writer outside the owned tree, or the reader still processing)")
        self.assertEqual(len(self.invocations()), 2, "one invocation each; nothing was retried")

    def test_settled_output_leaves_a_success_ok(self):
        r = self.call()
        self.assertEqual((r.returncode, r.stdout), (0, b"fake reply\n"), r.stderr.decode())
        res = self.result(self.runs()[0])
        self.assertEqual((res["status"], res["kind"], res["output_settlement"]["ended"],
                          res["output_settlement"]["waited_seconds"]), ("ok", "", True, 10.0))
        if res["process_tree"] == "Windows Job Object (kill on close)":
            self.assertIsInstance(res["output_settlement"]["job_active_then"], int)
        else:
            self.assertIsNone(res["output_settlement"]["job_active_then"])


class Concurrency(Base):
    def start_gated(self, *extra):
        gate = self.tmp / "gate"
        args = [sys.executable, str(HELPER), "call", "--purpose", "impl-board", "--mode", "code",
                "--scope", "s", "--no-project-rules", "--prompt-file", str(self.prompt_file), *extra]
        proc = subprocess.Popen(args, cwd=str(self.sub), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=self.env(FAKE_CLAUDE_MODE="gate", FAKE_CLAUDE_GATE=str(gate)))
        self.addCleanup(lambda: gate.exists() or gate.write_text("x"))

        def close():
            proc.kill()
            proc.communicate(timeout=60)

        self.addCleanup(close)

        def init_logged():
            return any(out.stat().st_size for out in (self.pdir() / "runs").glob("*/stdout.jsonl"))

        self.wait_for(init_logged)
        return proc, gate

    def fake_pid(self):
        return self.invocations()[0]["pid"]

    def test_a_second_caller_of_a_busy_purpose_sends_nothing(self):
        first, gate = self.start_gated()
        r = self.call(rules=False)
        self.assertEqual(r.returncode, 4, r.stderr.decode())
        self.assertIn(b"busy", r.stderr)
        self.assertEqual(len(self.invocations()), 1)
        status = json.loads(self.run_helper("status", "--purpose", "impl-board", "--json").stdout)
        self.assertEqual(status["status"], "running")
        self.assertIsInstance(status["running"]["claude_pid"], int, "the Claude pid is recorded at launch")
        gate.write_text("go")
        out, err = first.communicate(timeout=60)
        self.assertEqual(first.returncode, 0, err.decode())
        self.assertEqual(self.state()["status"], "ready")
        self.assertEqual(self.call(purpose="impl-other").returncode, 0, "another purpose is independent")

    def test_a_killed_helper_leaves_running_until_recovered(self):
        helper, gate = self.start_gated()
        helper.kill()
        helper.wait(timeout=30)
        pid = self.fake_pid()
        if os.name == "nt":
            deadline = time.monotonic() + 10
            while cs.pid_alive(pid) and time.monotonic() < deadline:
                time.sleep(0.1)
            self.assertFalse(cs.pid_alive(pid), "the Job Object ends the CLI when its helper dies")
        else:
            os.kill(pid, 9)
        self.assertEqual(self.state()["status"], "running")
        r = self.call(rules=False)
        self.assertEqual(r.returncode, 5)
        self.assertIn(b"recover", r.stderr)
        self.assertEqual(len(self.invocations()), 1)
        self.assertEqual(self.run_helper("recover", "--purpose", "impl-board").returncode, 2)
        r = self.run_helper("recover", "--purpose", "impl-board", "--confirm-stopped")
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        self.assertEqual(self.run_helper("recover", "--purpose", "impl-board", "--confirm-stopped").returncode, 2,
                         "a second recover finds nothing to do")
        st = self.state()
        cand = self.value(self.invocations()[0]["argv"], "--session-id")
        self.assertEqual((st["status"], st["candidate_session_id"], st["candidate_reported"]),
                         ("unconfirmed", cand, True))
        res = self.result(self.runs()[0])
        self.assertEqual(res["status"], "interrupted")
        self.assertTrue(res["recovered_after_interruption"])
        self.assertEqual(self.call(rules=False).returncode, 5)
        r = self.call("--resume-unconfirmed", "new", "--no-project-rules")
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        self.assertEqual((self.state()["status"], self.state()["session_id"]), ("ready", cand))

    @unittest.skipUnless(os.name == "nt", "a rename onto an open file fails only on Windows")
    def test_a_final_state_write_failure_still_prints_the_reply(self):
        helper, gate = self.start_gated()
        holder = open(self.pdir() / "state.json", "rb")  # Windows: blocks the rename onto state.json
        try:
            gate.write_text("go")
            out, err = helper.communicate(timeout=120)
        finally:
            holder.close()
        self.assertEqual(helper.returncode, 6, err.decode())
        self.assertEqual(out, b"fake reply\n")
        self.assertIn(b"STATE NOT SAVED", err)
        run = self.runs()[0]
        self.assertEqual(self.result(run)["status"], "ok")
        self.assertEqual(self.state()["status"], "running")
        r = self.run_helper("recover", "--purpose", "impl-board", "--confirm-stopped")
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        self.assertEqual(self.result(run)["status"], "ok", "the recorded result is kept, not rewritten")
        st = self.state()
        self.assertEqual((st["status"], st["calls"]), ("ready", 1))
        self.assertEqual(self.call(rules=False).returncode, 0)
        self.assertEqual(self.value(self.invocations()[-1]["argv"], "--resume"), st["session_id"])


class Interruptions(Base):
    def launch(self, stream_patch=None, monotonic=None):
        run_dir = self.tmp / "run"
        run_dir.mkdir()
        sid = "bd063f17-0778-4a84-b008-962d2ef7d32f"
        argv = cs.build_argv([sys.executable, str(FAKE)], "code", "high", [], ["--session-id", sid])
        stream = cs.Stream("code", list(cs.CODE_TOOLS), sid, self.project)
        if stream_patch:
            stream.feed = stream_patch
        env = self.env(FAKE_CLAUDE_MODE="sleep")
        with mock.patch.dict(os.environ, env, clear=True):
            if monotonic:
                with mock.patch.object(cs.time, "monotonic", monotonic):
                    return run_dir, cs.launch(argv, b"prompt", self.project, run_dir, 120, stream)
            return run_dir, cs.launch(argv, b"prompt", self.project, run_dir, 120, stream)

    def claude_pid(self, run_dir):
        return json.loads((run_dir / "command.json").read_text(encoding="utf-8"))["claude_pid"]

    def test_an_output_error_stops_the_cli(self):
        def broken(line):
            raise MemoryError("simulated")

        started = time.monotonic()
        run_dir, proc = self.launch(stream_patch=broken)
        self.assertLess(time.monotonic() - started, 40)
        self.assertIn("MemoryError", proc["reader_error"])
        self.assertFalse(cs.pid_alive(self.claude_pid(run_dir)))

    def test_an_interrupt_stops_the_cli_before_it_propagates(self):
        calls = {"n": 0}
        real = time.monotonic

        def interrupting():
            calls["n"] += 1
            if calls["n"] > 5:
                raise KeyboardInterrupt
            return real()

        with self.assertRaises(KeyboardInterrupt):
            self.launch(monotonic=interrupting)
        run_dir = self.tmp / "run"
        pid = self.claude_pid(run_dir)
        deadline = real() + 10
        while cs.pid_alive(pid) and real() < deadline:
            time.sleep(0.1)
        self.assertFalse(cs.pid_alive(pid))


def notification(sid="$SID", task="b7x2k", status="stopped", **change):
    """A system/task_notification as the CLI replays one on resume (the October 3 incident's shape)."""
    ev = {"type": "system", "subtype": "task_notification", "session_id": sid, "task_id": task, "status": status,
          "output_file": "C:/tmp/task.output", "summary": "Background command \"python sleep_fixture.py 300\" was stopped"}
    ev.update(change)
    return {k: v for k, v in ev.items() if v is not None}


class PreInit(Base):
    """What may come before the CLI's init: a resume's task notifications of its own session, nothing else."""

    def ready(self, purpose="impl-board"):
        self.assertEqual(self.call(purpose=purpose).returncode, 0)
        return self.state(purpose)["session_id"]

    def assert_refused(self, words, purpose="impl-board", *extra, **env):
        started = time.monotonic()
        r = self.call(*extra, purpose=purpose, rules=False, **env)
        self.assertLess(time.monotonic() - started, 25, "stopped at that event, not after the fake's pause")
        self.assertEqual(r.returncode, 1, r.stderr.decode())
        self.assertEqual(r.stdout, b"")
        run = self.runs(purpose)[-1]
        res = self.result(run)
        self.assertEqual(res["status"], "refused", res["cause"])
        self.assertIn(words, res["cause"])
        self.assertNotIn(b"fake reply", (run / "stdout.jsonl").read_bytes(), "nothing after the refused event ran")
        return res

    def test_a_resume_may_follow_task_notifications_of_its_own_session(self):
        sid = self.ready()
        lines = self.pre_init(notification(), notification(task="c9", status="completed", summary="done"))
        r = self.call(rules=False, FAKE_CLAUDE_PRE_INIT=lines)
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        self.assertEqual(r.stdout, b"fake reply\n")
        res = self.result(self.runs()[-1])
        self.assertEqual((res["status"], res["classifier"], res["init_events"]), ("ok", 3, 1))
        self.assertEqual(res["pre_init_events"], [
            {"subtype": "task_notification", "task_id": "b7x2k", "status": "stopped",
             "summary": "Background command \"python sleep_fixture.py 300\" was stopped"},
            {"subtype": "task_notification", "task_id": "c9", "status": "completed", "summary": "done"}])
        st = self.state()
        self.assertEqual((st["status"], st["session_id"], st["calls"]), ("ready", sid, 2))
        self.assertEqual(self.call(rules=False, FAKE_CLAUDE_PRE_INIT=lines).returncode, 0, "no refusal loop")

    def test_a_notification_on_a_first_or_new_id_attempt_is_refused(self):
        lines = self.pre_init(notification())
        self.assert_refused("on a first attempt", "impl-board", "--rules-file", str(self.rules),
                            "--scope", "s", FAKE_CLAUDE_PRE_INIT=lines, FAKE_CLAUDE_PRE_INIT_PAUSE="30")
        st = self.state()
        self.assertEqual((st["status"], st["candidate_reported"]), ("unconfirmed", True))
        self.assert_refused("on a unconfirmed-new attempt", "impl-board", "--resume-unconfirmed", "new",
                            "--rules-file", str(self.rules), FAKE_CLAUDE_PRE_INIT=lines, FAKE_CLAUDE_PRE_INIT_PAUSE="30")
        self.assertEqual(self.state()["status"], "unconfirmed")

    def test_a_notification_of_no_another_or_a_malformed_session_is_refused(self):
        other = "0b1c2d3e-0000-4000-8000-000000000001"
        for i, (sid, words) in enumerate(((None, "names session None"), ("-c", "names session '-c'"),
                                          (other, f"names session '{other}'"))):
            with self.subTest(sid=sid):
                purpose = f"p-{i}"
                kept = self.ready(purpose)
                self.assert_refused(words, purpose, FAKE_CLAUDE_PRE_INIT=self.pre_init(notification(sid=sid)),
                                    FAKE_CLAUDE_PRE_INIT_PAUSE="30")
                st = self.state(purpose)
                self.assertEqual((st["status"], st["session_id"]), ("conflict" if sid == other else "ready", kept))

    def test_too_many_or_oversized_notifications_are_refused(self):
        self.ready()
        self.assert_refused("more than 16 task_notifications",
                            FAKE_CLAUDE_PRE_INIT=self.pre_init(*[notification(task=f"t{i}") for i in range(17)]),
                            FAKE_CLAUDE_PRE_INIT_PAUSE="30")
        self.assert_refused("came before the CLI's init (at most 65536 bytes",
                            FAKE_CLAUDE_PRE_INIT=self.pre_init(notification(summary="x" * 70000)),
                            FAKE_CLAUDE_PRE_INIT_PAUSE="30")
        self.assertEqual(self.state()["status"], "ready")
        self.assertEqual(self.call(rules=False, FAKE_CLAUDE_PRE_INIT=self.pre_init(
            *[notification(task=f"t{i}") for i in range(16)])).returncode, 0, "sixteen are accepted")

    def test_anything_else_before_the_init_is_refused_before_it_takes_effect(self):
        self.ready()
        cases = [({"type": "assistant", "parent_tool_use_id": None, "session_id": "$SID",
                   "message": {"model": "claude-opus-5-5", "content": [{"type": "text", "text": "acted"}]}},
                  "sent 'assistant'/None before its init"),
                 ({"type": "user", "session_id": "$SID", "message": {"role": "user", "content": "x"}},
                  "sent 'user'/None before its init"),
                 ({"type": "user", "isReplay": True, "uuid": "0b1c2d3e-0000-4000-8000-000000000002",
                   "session_id": "$SID", "message": {"role": "user", "content": "x"}},
                  "sent 'user'/None before its init"),
                 ({"type": "result", "subtype": "success", "is_error": False, "session_id": "$SID", "num_turns": 1,
                   "result": "acted"}, "sent 'result'/'success' before its init"),
                 ({"type": "permission_denied", "session_id": "$SID"}, "sent 'permission_denied'/None"),
                 ({"type": "system", "subtype": "hook_started", "session_id": "$SID"}, "'system'/'hook_started'"),
                 ({"type": "system", "subtype": "plugin_install", "session_id": "$SID"}, "'system'/'plugin_install'"),
                 ({"type": "stream_event", "session_id": "$SID"}, "'stream_event'/None")]
        for event, words in cases:
            with self.subTest(event=event["type"]):
                self.assert_refused(words, FAKE_CLAUDE_PRE_INIT=self.pre_init(notification(), event),
                                    FAKE_CLAUDE_PRE_INIT_PAUSE="30")
        res = self.assert_refused("first event was 'assistant'", FAKE_CLAUDE_PRE_INIT=self.pre_init(cases[0][0]),
                                  FAKE_CLAUDE_PRE_INIT_PAUSE="30")
        self.assertFalse(res["session"]["delivered"], "an assistant event before the init never counts")
        self.assertEqual(self.state()["status"], "ready")

    def test_a_notification_then_a_bad_init_is_refused_for_that_init(self):
        cases = [("wrong_model", "started on model 'claude-sonnet-5-5'"), ("extra_safe_tool", "['Task']"),
                 ("mcp", "loaded MCP servers"), ("accept_edits", "permission mode 'acceptEdits'"),
                 ("api_key", "apiKeySource 'ANTHROPIC_API_KEY'")]
        for i, (fake, words) in enumerate(cases):
            with self.subTest(fake=fake):
                self.ready(f"p-{i}")
                self.assert_refused(words, f"p-{i}", FAKE_CLAUDE_MODE=fake,
                                    FAKE_CLAUDE_PRE_INIT=self.pre_init(notification()))

    def test_every_init_is_checked_not_only_the_first(self):
        other = "0b1c2d3e-0000-4000-8000-000000000003"
        for i, (change, words) in enumerate(((dict(model="claude-sonnet-5-5"), "init event 2: the CLI started on"),
                                             (dict(cwd=str(self.tmp)), "init event 2: the CLI reported working folder"),
                                             (dict(session_id=other), "init event 2: the CLI reported session"),
                                             (dict(tools=["Bash", "Read"]), "init event 2: the CLI offered tools"))):
            with self.subTest(change=change):
                self.ready(f"p-{i}")
                self.assert_refused(words, f"p-{i}", FAKE_CLAUDE_MODE="sleep",
                                    FAKE_CLAUDE_SECOND_INIT=json.dumps(change))

    def test_a_notification_then_the_end_of_output_keeps_the_session(self):
        sid = self.ready()
        r = self.call(rules=False, FAKE_CLAUDE_MODE="pre_init_only", FAKE_CLAUDE_PRE_INIT=self.pre_init(notification()))
        self.assertEqual(r.returncode, 1)
        self.assertIn(b"never sent its init event", r.stderr)
        st = self.state()
        self.assertEqual((st["status"], st["session_id"], st["failures"]), ("ready", sid, 1))

    def test_a_success_with_no_turn_is_a_failure_and_no_loop(self):
        sid = self.ready()
        r = self.call(rules=False, FAKE_CLAUDE_MODE="zero_turns", FAKE_CLAUDE_PRE_INIT=self.pre_init(notification()))
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, b"", "the zero-turn result's text is not printed")
        res = self.result(self.runs()[-1])
        self.assertEqual(res["status"], "failed")
        self.assertIn("no turn reached the model", res["cause"])
        self.assertFalse(res["session"]["delivered"])
        self.assertEqual(res["result_event"]["num_turns"], 0)
        self.assertEqual((self.state()["status"], self.state()["session_id"]), ("ready", sid))
        r = self.call(rules=False, FAKE_CLAUDE_PRE_INIT=self.pre_init(notification()))
        self.assertEqual(r.returncode, 0, "the next call is not refused the same way")
        r = self.call(FAKE_CLAUDE_MODE="zero_turns", purpose="p-new")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.state("p-new")["status"], "unconfirmed", "a zero-turn first call confirms nothing")

    def test_noise_before_the_init_is_bounded(self):
        self.ready()
        self.assertEqual(self.call(rules=False, FAKE_CLAUDE_PRE_INIT=self.pre_init(*["not json"] * 16)).returncode, 0)
        self.assert_refused("more than 16 lines that are not JSON",
                            FAKE_CLAUDE_PRE_INIT=self.pre_init(*["not json"] * 17), FAKE_CLAUDE_PRE_INIT_PAUSE="30")
        self.assert_refused("or one over 65536 bytes", FAKE_CLAUDE_PRE_INIT=self.pre_init("y" * 70000),
                            FAKE_CLAUDE_PRE_INIT_PAUSE="30")

    def test_the_stale_interrupted_turn_switch_is_not_inherited(self):
        r = self.call(CLAUDE_CODE_RESUME_INTERRUPTED_TURN="1")
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        self.assertEqual(self.invocations()[0]["env"], ["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS", "CLAUDE_CODE_TMPDIR"])
        command = json.loads((self.runs()[0] / "command.json").read_text(encoding="utf-8"))
        self.assertEqual(command["dropped_environment"], ["CLAUDE_CODE_RESUME_INTERRUPTED_TURN"])

    def test_an_interrupted_resume_is_recovered_and_resumed_in_the_same_session(self):
        sid = self.ready()
        gate = self.tmp / "gate"
        self.addCleanup(lambda: gate.exists() or gate.write_text("x"))
        args = [sys.executable, str(HELPER), "call", "--purpose", "impl-board", "--mode", "code",
                "--prompt-file", str(self.prompt_file)]
        helper = subprocess.Popen(args, cwd=str(self.sub), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  env=self.env(FAKE_CLAUDE_MODE="gate", FAKE_CLAUDE_GATE=str(gate),
                                               FAKE_CLAUDE_PRE_INIT=self.pre_init(notification(status="running"))))
        self.addCleanup(lambda: helper.poll() is not None or helper.kill())
        self.wait_for(lambda: len(self.runs()) == 2 and (self.runs()[1] / "stdout.jsonl").exists()
                      and b'"subtype": "init"' in (self.runs()[1] / "stdout.jsonl").read_bytes())
        interrupted = self.runs()[-1]
        helper.kill()
        helper.communicate(timeout=30)
        pid = self.invocations()[-1]["pid"]
        if os.name != "nt":
            os.kill(pid, 9)
        self.wait_for(lambda: not cs.pid_alive(pid), 10)
        self.assertEqual(self.state()["status"], "running")
        r = self.run_helper("recover", "--purpose", "impl-board", "--confirm-stopped")
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        res = self.result(interrupted)
        self.assertEqual(res["status"], "interrupted", "recover classifies the replayed log with the run's attempt")
        self.assertEqual([e["status"] for e in res["pre_init_events"]], ["running"])
        self.assertEqual((self.state()["status"], self.state()["session_id"]), ("ready", sid))
        r = self.call(rules=False, FAKE_CLAUDE_PRE_INIT=self.pre_init(notification()))
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        self.assertEqual(r.stdout, b"fake reply\n")
        self.assertEqual(self.value(self.invocations()[-1]["argv"], "--resume"), sid)
        st = self.state()
        self.assertEqual((st["status"], st["session_id"]), ("ready", sid))
        self.assertEqual(self.result(interrupted)["status"], "interrupted", "the earlier record is kept as written")
        self.assertEqual([self.result(r)["status"] for r in self.runs()], ["ok", "interrupted", "ok"])
        self.assertEqual(self.call(rules=False).returncode, 0)


class Managed(Base):
    """Helpers for managed calls (call --accept-follow-ups) against the fake's stream-json input."""

    FOLLOW = "Also end with the word PINEAPPLE\r\nnon-ASCII: \u00e9\u2014\u4e2d  \n"

    def start(self, *extra, purpose="impl-board", wait_init=True, helper=HELPER, **env):
        gate = self.tmp / f"gate-{purpose}"
        self.addCleanup(lambda: gate.exists() or gate.write_text("x"))
        args = [sys.executable, str(helper), "call", "--purpose", purpose, "--mode", "code",
                "--prompt-file", str(self.prompt_file), "--accept-follow-ups", *extra]
        if not self.pdir(purpose).exists():
            args += ["--scope", "s", "--rules-file", str(self.rules)]
        proc = subprocess.Popen(args, cwd=str(self.sub), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=self.env(**{"FAKE_CLAUDE_MODE": "gate", "FAKE_CLAUDE_GATE": str(gate), **env}))

        def close():
            if proc.poll() is None:
                proc.kill()
            proc.communicate(timeout=60)

        self.addCleanup(close)
        if wait_init:
            self.wait_for(lambda: ((self.read_state(purpose) or {}).get("running") or {}).get("init_ok") is True)
        return proc, gate

    def read_state(self, purpose):
        return cs.read_json(self.pdir(purpose) / "state.json")

    def follow_up(self, text=None, purpose="impl-board", wait="20", data=None, **env):
        f = self.tmp / f"follow-{time.monotonic_ns()}.md"
        f.write_bytes(data if data is not None else (text or self.FOLLOW).encode("utf-8"))
        return self.run_helper("follow-up", "--purpose", purpose, "--prompt-file", str(f), "--wait", wait, **env)

    def received(self):
        path = Path(str(self.log) + ".stdin.jsonl")
        if not path.exists():
            return []
        return [base64.b64decode(json.loads(line)["line_b64"]) for line in path.read_text(encoding="utf-8").splitlines()]

    def finish(self, proc, gate):
        gate.write_text("go")
        out, err = proc.communicate(timeout=120)
        return out, err


class FollowUps(Managed):
    """call --accept-follow-ups and follow-up."""

    def test_a_follow_up_mid_tool_is_submitted_acknowledged_and_answered_in_that_turn(self):
        proc, gate = self.start()
        argv = self.invocations()[0]["argv"]
        self.assertEqual(argv[argv.index("--input-format"):argv.index("--input-format") + 2],
                         ["--input-format", "stream-json"])
        self.assertIn("--replay-user-messages", argv)
        self.assertEqual(self.value(argv, "--model"), "claude-opus-5-5")
        self.assert_clean(argv)
        r = self.follow_up()
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        self.assertIn(b"submitted at", r.stdout)
        self.assertIn(b"acknowledged by the CLI at", r.stdout)
        out, err = self.finish(proc, gate)
        self.assertEqual(proc.returncode, 0, err.decode())
        self.assertEqual(out, b"fake reply (turn 1, 2 message(s))\n", "one turn read both messages")
        run = self.runs()[0]
        res = self.result(run)
        [msg] = res["follow_ups"]["messages"]
        self.assertEqual((msg["outcome"], msg["acknowledged_by"], msg["followed_by_result"]), ("answered (turn 1)", "uuid", 1))
        self.assertTrue(msg["submitted_at"] <= msg["acknowledged_at"])
        self.assertEqual(res["follow_ups"]["prompt_message"]["outcome"], "answered (turn 1)")
        self.assertEqual(res["follow_ups"]["inbox_closed"]["reason"], "every message was answered")
        self.assertEqual(res["turns"][0]["after_follow_ups"], [msg["uuid"]])
        lines = (run / "stdin.jsonl").read_bytes()
        self.assertEqual(lines, b"".join(self.received()), "stdin.jsonl holds exactly the bytes the CLI read")
        first, second = [json.loads(line) for line in lines.splitlines()]
        self.assertEqual(first["message"]["content"].encode("utf-8"), (run / "prompt.txt").read_bytes())
        self.assertEqual(second["message"]["content"], self.FOLLOW, "the follow-up's text is sent exactly")
        self.assertEqual((second["type"], second["parent_tool_use_id"], second["uuid"]), ("user", None, msg["uuid"]))
        self.assertEqual((run / "reply.md").read_text(encoding="utf-8"), "fake reply (turn 1, 2 message(s))")
        self.assertEqual((run / "replies" / "1.md").read_text(encoding="utf-8"), "fake reply (turn 1, 2 message(s))")
        self.assertEqual(self.state()["status"], "ready")


    def test_managed_guidance_is_only_in_the_initial_message_not_an_exact_follow_up(self):
        proc, gate = self.start("--allow-command", "Bash(python stage.py *)",
                                "--command-timeout", "5400", "--timeout", "7200",
                                FAKE_CLAUDE_COMMAND="foreground")
        r = self.follow_up()
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        _out, err = self.finish(proc, gate)
        self.assertEqual(proc.returncode, 0, err.decode())
        first, second = [json.loads(line) for line in b"".join(self.received()).splitlines()]
        initial = first["message"]["content"].encode("utf-8")
        self.assertEqual(initial.count(b"<command-guidance>"), 1)
        self.assertIn(b"5400 s (5400000 ms)", initial)
        self.assertTrue(initial.endswith(PROMPT.encode("utf-8")))
        self.assertEqual(second["message"]["content"], self.FOLLOW)
        self.assertNotIn("<command-guidance>", second["message"]["content"])
        run = self.runs()[0]
        self.assertEqual((run / "prompt.txt").read_bytes(), initial)
        brief = json.loads((run / "brief.json").read_text(encoding="utf-8"))
        begin = initial.index(b"<command-guidance>")
        end = initial.index(b"</command-guidance>") + len(b"</command-guidance>\n\n")
        self.assertEqual(brief["command_guidance_sha256"], hashlib.sha256(initial[begin:end]).hexdigest())

    def test_a_follow_up_during_a_long_foreground_command_is_answered_in_that_turn(self):
        proc, gate = self.start("--allow-command", "Bash(python stage.py *)", "--command-timeout", "60",
                                FAKE_CLAUDE_COMMAND="foreground")
        r = self.follow_up()
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        self.assertIn(b"acknowledged by the CLI at", r.stdout)
        out, err = self.finish(proc, gate)
        self.assertEqual(proc.returncode, 0, err.decode())
        res = self.result(self.runs()[0])
        self.assertEqual((res["status"], res["follow_ups"]["messages"][0]["outcome"]), ("ok", "answered (turn 1)"))
        self.assertEqual(res["commands"]["tasks"][0]["is_backgrounded"], False)
        self.assertEqual(res["commands"]["background_breach"], [])
        stdout = (self.runs()[0] / "stdout.jsonl").read_text(encoding="utf-8")
        self.assertLess(stdout.index('"uuid": "' + res["follow_ups"]["messages"][0]["uuid"] + '", "isReplay": true'),
                        stdout.index('"tool_use_result"'), "replayed while the command ran, before its result")

    def test_a_breach_in_a_managed_call_closes_its_input_and_answers_nothing(self):
        proc, gate = self.start("--allow-command", "Bash(python stage.py *)", FAKE_CLAUDE_COMMAND="background",
                                FAKE_CLAUDE_IGNORE_DISABLE="1", wait_init=False)
        self.wait_for(lambda: (self.pdir() / "runs").is_dir() and len(self.runs()) == 1)
        run = self.runs()[0]
        self.wait_for(lambda: (run / "inbox" / "closed").exists(), 30)
        self.assertIn("background commands are disabled", cs.read_json(run / "inbox" / "closed")["reason"])
        r = self.follow_up()
        self.assertEqual(r.returncode, 5, "nothing more is taken")
        out, err = self.finish(proc, gate)
        self.assertEqual((proc.returncode, out), (1, b""))
        res = self.result(run)
        self.assertEqual(res["status"], "incomplete")
        self.assertEqual(res["follow_ups"]["prompt_message"]["outcome"],
                         "ended by a background-command breach: turn 1's answer is not trusted (incomplete)")
        self.assertEqual(self.state()["status"], "ready")

    def test_a_follow_up_replayed_only_when_dequeued_is_answered_as_the_next_turn(self):
        proc, gate = self.start(FAKE_CLAUDE_REPLAY="dequeue")
        r = self.follow_up(wait="2")
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        self.assertIn(b"not acknowledged within 2 s", r.stdout)
        out, err = self.finish(proc, gate)
        self.assertEqual(proc.returncode, 0, err.decode())
        u = self.result(self.runs()[0])["follow_ups"]["messages"][0]["uuid"]
        self.assertEqual(out.decode("utf-8"), "----- turn 1 of 2 -----\nfake reply (turn 1, 1 message(s))\n"
                                              f"----- turn 2 of 2, after follow-up {u} -----\n"
                                              "fake reply (turn 2, 1 message(s))\n")
        res = self.result(self.runs()[0])
        self.assertEqual(res["follow_ups"]["messages"][0]["outcome"], "answered (turn 2)")
        self.assertEqual(res["reply_file"], "reply.md")
        self.assertEqual((self.runs()[0] / "reply.md").read_text(encoding="utf-8"), "fake reply (turn 2, 1 message(s))")

    def test_each_documented_order_of_init_and_replay_is_accepted(self):
        for i, (order, replay) in enumerate((("init_then_replay", "receipt"), ("replay_first", "receipt"),
                                             ("init_first", "receipt"), ("init_then_replay", "fresh_uuid"))):
            with self.subTest(order=order, replay=replay):
                r = self.call("--accept-follow-ups", purpose=f"p-{i}", FAKE_CLAUDE_INIT_ORDER=order,
                              FAKE_CLAUDE_REPLAY=replay)
                self.assertEqual(r.returncode, 0, r.stderr.decode())
                self.assertEqual(r.stdout, b"fake reply (turn 1, 1 message(s))\n")
                res = self.result(self.runs(f"p-{i}")[0])
                self.assertEqual(res["pre_init_events"][0]["subtype"] if res["pre_init_events"] else None,
                                 "user_replay" if order == "replay_first" else None)
                self.assertEqual(res["follow_ups"]["prompt_message"]["acknowledged_by"],
                                 "text" if replay == "fresh_uuid" else "uuid")
                self.assertEqual(self.state(f"p-{i}")["status"], "ready")

    def test_a_replay_before_the_init_of_another_message_or_session_is_refused(self):
        r = self.call("--accept-follow-ups", FAKE_CLAUDE_INIT_ORDER="init_first", FAKE_CLAUDE_MODE="sleep",
                      FAKE_CLAUDE_PRE_INIT=self.pre_init({"type": "user", "isReplay": True, "session_id": "$SID",
                                                          "uuid": "0b1c2d3e-0000-4000-8000-000000000004",
                                                          "message": {"role": "user", "content": "not sent"}}))
        self.assertEqual(r.returncode, 1)
        self.assertIn("replayed a user message the helper never wrote", self.result(self.runs()[0])["cause"])

    def test_without_acknowledgments_the_inbox_closes_after_the_wait(self):
        wrapper = self.tmp / "helper_short_ack_wait.py"
        wrapper.write_text(f"import sys\nsys.path.insert(0, {str(HELPER.parent)!r})\nimport claude_session as cs\n"
                           "cs.ACK_WAIT = 1.0\nsys.exit(cs.main())\n", encoding="utf-8")
        r = self.call("--accept-follow-ups", FAKE_CLAUDE_REPLAY="none", helper=wrapper)
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        res = self.result(self.runs()[0])
        self.assertIn("did not acknowledge", res["follow_ups"]["inbox_closed"]["reason"])
        self.assertEqual(res["follow_ups"]["prompt_message"]["outcome"],
                         "unknown: submitted but never acknowledged (no replay of it was seen)")
        self.assertEqual(self.state()["status"], "ready", "the turn itself was delivered")

    def test_a_follow_up_without_a_live_initialized_managed_call_sends_nothing(self):
        r = self.follow_up(purpose="no-such")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.call().returncode, 0)
        r = self.follow_up()
        self.assertEqual(r.returncode, 5)
        self.assertIn(b"no call is running (status ready)", r.stderr)
        self.assertEqual(len(self.invocations()), 1, "a follow-up never launches the CLI")
        # a running call that does not take follow-ups
        gate = self.tmp / "gate"
        self.addCleanup(lambda: gate.exists() or gate.write_text("x"))
        plain = subprocess.Popen([sys.executable, str(HELPER), "call", "--purpose", "impl-board", "--mode", "code",
                                  "--prompt-file", str(self.prompt_file)], cwd=str(self.sub), stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, env=self.env(FAKE_CLAUDE_MODE="gate", FAKE_CLAUDE_GATE=str(gate)))
        self.addCleanup(lambda: plain.poll() is not None or plain.kill())
        self.wait_for(lambda: ((self.read_state("impl-board") or {}).get("running") or {}).get("claude_pid"))
        r = self.follow_up()
        self.assertEqual(r.returncode, 5)
        self.assertIn(b"not started with --accept-follow-ups", r.stderr)
        gate.write_text("go")
        plain.communicate(timeout=60)
        # a managed call whose init has not come yet
        proc, gate = self.start(purpose="p-init", wait_init=False, FAKE_CLAUDE_INIT_GATE=str(self.tmp / "init-gate"))
        self.wait_for(lambda: ((self.read_state("p-init") or {}).get("running") or {}).get("claude_pid"))
        r = self.follow_up(purpose="p-init")
        self.assertEqual(r.returncode, 5)
        self.assertIn(b"init has not been validated yet", r.stderr)
        self.assertEqual(list((self.runs("p-init")[0] / "inbox").iterdir()), [])
        (self.tmp / "init-gate").write_text("go")
        out, err = self.finish(proc, gate)
        self.assertEqual(proc.returncode, 0, err.decode())
        # recorded states whose helper or CLI is gone
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        for i, (helper_pid, claude_pid, words) in enumerate(((dead.pid, os.getpid(), b"helper (pid"),
                                                            (os.getpid(), dead.pid, b"Claude CLI (pid"))):
            with self.subTest(words=words):
                purpose = f"p-dead-{i}"
                run_dir = self.pdir(purpose) / "runs" / "20261003T000000000000Z-g1-abcdef"
                (run_dir / "inbox").mkdir(parents=True)
                st = cs.new_state(purpose, "code", "s", self.project.resolve())
                st.update(status="running", running={
                    "run_dir": str(run_dir), "helper_pid": helper_pid, "claude_pid": claude_pid, "first": True,
                    "attempt": "first", "asked_id": "bd063f17-0778-4a84-b008-962d2ef7d32f",
                    "previous_status": "new", "follow_ups": True, "init_ok": True})
                (self.pdir(purpose) / "state.json").write_text(json.dumps(st), encoding="utf-8")
                r = self.follow_up(purpose=purpose)
                self.assertEqual(r.returncode, 5, r.stderr.decode())
                self.assertIn(words, r.stderr)
                self.assertEqual(list((run_dir / "inbox").iterdir()), [], "nothing was written")
                self.assertEqual(len(list((self.pdir(purpose) / "runs").iterdir())), 1, "no run was created")
        self.assertEqual(len(self.invocations()), 3)

    def test_finish_race_a_follow_up_is_either_answered_or_refused_never_silent(self):
        # 1. created under the lock as the result arrives: taken before the inbox can close, then answered
        proc, gate = self.start()
        run = self.runs()[0]
        u = "0b1c2d3e-0000-4000-8000-0000000000aa"
        with open(run / "inbox.lock", "a+b") as held:
            cs._lock(held)
            gate.write_text("go")
            self.wait_for(lambda: b'"type": "result"' in (run / "stdout.jsonl").read_bytes())
            cs.write_new(run / "inbox" / f"0001-{u}.json",
                         {"uuid": u, "sha256": cs.hashlib.sha256(b"late").hexdigest(), "bytes": 4, "text": "late"})
            cs._unlock(held)
        out, err = proc.communicate(timeout=120)
        self.assertEqual(proc.returncode, 0, err.decode())
        self.assertIn(f"----- turn 2 of 2, after follow-up {u} -----".encode(), out)
        [msg] = self.result(run)["follow_ups"]["messages"]
        self.assertEqual(msg["outcome"], "answered (turn 2)")
        # 2. after the inbox closed: refused with exit 5, nothing written
        proc, gate = self.start(purpose="p-late")
        run = self.runs("p-late")[0]
        gate.write_text("go")
        self.wait_for(lambda: (run / "inbox" / "closed").exists())
        r = self.follow_up(purpose="p-late")
        self.assertEqual(r.returncode, 5, r.stderr.decode())
        self.assertTrue(b"stopped taking follow-ups" in r.stderr or b"no call is running" in r.stderr, r.stderr)
        proc.communicate(timeout=120)
        self.assertEqual(self.result(run)["follow_ups"]["messages"], [])
        self.assertEqual(len(self.received()), 3, "two prompts and the answered follow-up; nothing else")

    def test_a_call_that_stops_records_its_follow_ups_as_unknown_or_rejected(self):
        proc, gate = self.start("--timeout", "8")
        r = self.follow_up()
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        out, err = proc.communicate(timeout=120)
        self.assertEqual(proc.returncode, 1)
        res = self.result(self.runs()[0])
        self.assertEqual(res["status"], "timeout")
        self.assertEqual(res["follow_ups"]["messages"][0]["outcome"], "unknown: call stopped (timeout)")
        self.assertIn(b"unknown: call stopped (timeout)", err)
        # a helper killed mid-call: recover rejects what was never taken and records the rest as unknown
        proc, gate = self.start(purpose="p-kill")
        r = self.follow_up(purpose="p-kill")
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        run = self.runs("p-kill")[0]
        proc.kill()
        proc.communicate(timeout=60)
        pid = self.invocations()[-1]["pid"]
        if os.name != "nt":
            os.kill(pid, 9)
        self.wait_for(lambda: not cs.pid_alive(pid), 10)
        r = self.follow_up(purpose="p-kill")
        self.assertEqual(r.returncode, 5)
        self.assertIn(b"helper (pid", r.stderr)
        u = "0b1c2d3e-0000-4000-8000-0000000000bb"
        cs.write_new(run / "inbox" / f"0009-{u}.json", {"uuid": u, "sha256": "x", "bytes": 1, "text": "x"})
        r = self.run_helper("recover", "--purpose", "p-kill", "--confirm-stopped")
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        taken, unsent = self.result(run)["follow_ups"]["messages"]
        self.assertEqual(taken["outcome"], "unknown: call stopped (the helper was interrupted)")
        self.assertTrue(unsent["outcome"].startswith("rejected: the call stopped taking follow-ups"))

    def test_bad_follow_ups_are_refused_and_a_wrong_model_call_takes_none(self):
        proc, gate = self.start(FAKE_CLAUDE_REPLY_MODEL="claude-sonnet-5-5")
        r = self.follow_up(data=b"x" * (cs.MAX_FOLLOW_UP + 1))
        self.assertEqual(r.returncode, 2)
        r = self.follow_up(data=b"\xff\xfe not utf-8")
        self.assertEqual(r.returncode, 2)
        self.assertIn(b"not UTF-8", r.stderr)
        run = self.runs()[0]
        self.assertEqual(list((run / "inbox").iterdir()), [])
        u = "0b1c2d3e-0000-4000-8000-0000000000cc"
        with open(run / "inbox.lock", "a+b") as held:
            cs._lock(held)
            gate.write_text("go")
            self.wait_for(lambda: b'"type": "result"' in (run / "stdout.jsonl").read_bytes())
            cs.write_new(run / "inbox" / f"0001-{u}.json", {"uuid": u, "sha256": cs.hashlib.sha256(b"steer").hexdigest(),
                                                           "bytes": 5, "text": "steer"})
            cs._unlock(held)
        out, err = proc.communicate(timeout=120)
        self.assertEqual((proc.returncode, out), (1, b""))
        res = self.result(run)
        self.assertEqual(res["status"], "wrong_model")
        self.assertIn("rejected: the call stopped taking follow-ups (the CLI served", res["follow_ups"]["messages"][0]["outcome"])
        self.assertTrue((run / "acks" / f"{u}.rejected.json").exists())
        self.assertNotIn(b"steer", b"".join(self.received()))

    def test_a_follow_up_never_takes_the_purpose_lock(self):
        proc, gate = self.start()
        r = self.call(rules=False)
        self.assertEqual(r.returncode, 4, "a second call of the purpose is still busy")
        r = self.follow_up(text="steer")
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        r = self.follow_up(SIJAV_CLAUDE="off")
        self.assertEqual(r.returncode, 3)
        out, err = self.finish(proc, gate)
        self.assertEqual(proc.returncode, 0, err.decode())
        self.assertEqual(len(self.invocations()), 1)
        self.assertEqual(self.call("--accept-follow-ups", rules=False).returncode, 0, "the next call resumes")
        self.assertEqual(self.value(self.invocations()[-1]["argv"], "--resume"), self.state()["session_id"])


def lifecycle(state, cmd="$CMD", sid="$SID", **change):
    """A command_lifecycle event in the shape the real CLI printed before its init on October 3 (undocumented)."""
    ev = {"type": "command_lifecycle", "command_uuid": cmd, "state": state, "uuid": "$NEW", "session_id": sid}
    ev.update(change)
    return {k: v for k, v in ev.items() if v is not None}


QUEUED, STARTED = lifecycle("queued"), lifecycle("started")


class Lifecycle(Managed):
    """A managed call's command_lifecycle events before its init: queued then started, of its first message."""

    def managed(self, *extra, purpose="impl-board", **env):
        new = not (self.pdir(purpose) / "state.json").exists()
        return self.call("--accept-follow-ups", *extra, purpose=purpose, rules=new, **env)

    def ready(self, purpose="impl-board"):
        self.assertEqual(self.managed(purpose=purpose).returncode, 0)
        return self.state(purpose)["session_id"]

    def refused(self, words, *lines, purpose="impl-board", text_mode=False, **env):
        started = time.monotonic()
        env = {"FAKE_CLAUDE_PRE_INIT": self.pre_init(*lines), "FAKE_CLAUDE_PRE_INIT_PAUSE": "30", **env}
        r = (self.call(purpose=purpose, rules=False, **env) if text_mode else self.managed(purpose=purpose, **env))
        self.assertLess(time.monotonic() - started, 25, "stopped at that event, not after the fake's pause")
        self.assertEqual((r.returncode, r.stdout), (1, b""), r.stderr.decode())
        run = self.runs(purpose)[-1]
        res = self.result(run)
        self.assertEqual(res["status"], "refused", res["cause"])
        self.assertIn(words, res["cause"])
        self.assertNotIn(b"fake reply", (run / "stdout.jsonl").read_bytes(), "nothing after the refused event ran")
        return res

    def first_message(self, purpose="impl-board"):
        return json.loads((self.runs(purpose)[-1] / "brief.json").read_text(encoding="utf-8"))["first_message_uuid"]

    def test_the_incidents_order_on_a_managed_resume_is_accepted(self):
        sid = self.ready()
        r = self.managed(FAKE_CLAUDE_PRE_INIT=self.pre_init(QUEUED, STARTED, "$REPLAY"))
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        self.assertEqual(r.stdout, b"fake reply (turn 1, 1 message(s))\n")
        first = self.first_message()
        res = self.result(self.runs()[-1])
        self.assertEqual(res["classifier"], 3)
        self.assertEqual(res["pre_init_events"], [
            {"subtype": "command_lifecycle", "state": "queued", "command_uuid": first, "bound_by": "uuid"},
            {"subtype": "command_lifecycle", "state": "started", "command_uuid": first, "bound_by": "uuid"},
            {"subtype": "user_replay", "uuid": first}])
        self.assertEqual([e["state"] for e in res["command_lifecycle"]["by_command"][first]], ["queued", "started"])
        self.assertTrue(res["command_lifecycle"]["first_message_started"])
        self.assertTrue(res["session"]["delivered"])
        self.assertEqual((self.state()["status"], self.state()["session_id"]), ("ready", sid))

    def test_lifecycle_events_are_accepted_on_a_first_attempt_and_in_any_documented_placement(self):
        r = self.managed(purpose="p-fresh", FAKE_CLAUDE_PRE_INIT=self.pre_init(QUEUED, STARTED))
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        self.assertEqual(self.state("p-fresh")["status"], "ready")
        r = self.managed(purpose="p-replay", FAKE_CLAUDE_INIT_ORDER="replay_first",
                         FAKE_CLAUDE_PRE_INIT=self.pre_init(QUEUED, STARTED))
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        self.assertEqual([e["subtype"] for e in self.result(self.runs("p-replay")[0])["pre_init_events"]],
                         ["user_replay", "command_lifecycle", "command_lifecycle"])
        self.ready()
        r = self.managed(FAKE_CLAUDE_PRE_INIT=self.pre_init(notification(), QUEUED, notification(task="t2"), STARTED))
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        self.assertEqual([e["subtype"] for e in self.result(self.runs()[-1])["pre_init_events"]],
                         ["task_notification", "command_lifecycle", "task_notification", "command_lifecycle"])
        self.refused("on a first attempt", QUEUED, notification(), purpose="p-first")

    def test_lifecycle_of_another_command_or_out_of_order_is_refused(self):
        self.ready()
        for lines, words in (([lifecycle("queued", cmd="$NEW")], "not the call's first message"),
                             ([QUEUED, lifecycle("started", cmd="$NEW")], "not the call's first message"),
                             ([STARTED], "state 'started' after []"),
                             ([QUEUED, QUEUED], "state 'queued' after ['queued']"),
                             ([STARTED, QUEUED], "state 'started' after []"),
                             ([QUEUED, lifecycle("completed")], "state 'completed'"),
                             ([lifecycle("cancelled")], "state 'cancelled'"),
                             ([lifecycle("bogus")], "state 'bogus'"),
                             ([QUEUED, STARTED, STARTED], "state 'started' after ['queued', 'started']")):
            with self.subTest(lines=[ln["state"] for ln in lines]):
                self.refused(words, *lines)
        self.assertEqual(self.state()["status"], "ready")

    def test_malformed_lifecycle_events_are_refused(self):
        self.ready()
        other = "0b1c2d3e-0000-4000-8000-000000000005"
        dup = "0b1c2d3e-0000-4000-8000-000000000006"
        for lines, words in (([lifecycle("queued", sid=None)], "with keys"),
                             ([lifecycle("queued", sid="-c")], "naming session '-c'"),
                             ([lifecycle("queued", uuid="x")], "uuid or command_uuid is not a UUID"),
                             ([lifecycle("queued", cmd="x")], "uuid or command_uuid is not a UUID"),
                             ([lifecycle("queued", extra=1)], "with keys"),
                             ([lifecycle("queued", uuid=None)], "with keys"),
                             ([lifecycle("queued", uuid=dup), lifecycle("started", uuid=dup)], "repeating event uuid"),
                             ([lifecycle("queued", pad="x" * 70000)], "(at most 65536 bytes"),
                             ([lifecycle("queued", sid=other)], f"naming session '{other}'")):  # last: a conflict
            with self.subTest(words=words):
                self.refused(words, *lines)
        self.assertEqual(self.state()["status"], "conflict", "the event naming another session's UUID")

    def test_the_total_before_init_is_bounded(self):
        self.ready()
        nineteen = [*[notification(task=f"t{i}") for i in range(16)], QUEUED, STARTED, "$REPLAY"]
        self.assertEqual(self.managed(FAKE_CLAUDE_PRE_INIT=self.pre_init(*nineteen)).returncode, 0)
        self.refused("more than 19 events before its init", *nineteen, notification(task="t-extra"))

    def test_lifecycle_in_a_text_input_call_is_refused(self):
        self.assertEqual(self.call().returncode, 0)
        self.refused("in a text-input call", QUEUED, text_mode=True)

    def test_a_bad_init_after_lifecycle_events_is_refused_for_that_init(self):
        other = "0b1c2d3e-0000-4000-8000-000000000007"
        cases = [({"model": "claude-sonnet-5-5"}, "started on model 'claude-sonnet-5-5'"),
                 ({"tools": ["Edit", "Glob", "Grep", "Read", "Task", "Write"]}, "not granted: ['Task']"),
                 ({"mcp_servers": [{"name": "github"}]}, "loaded MCP servers"),
                 ({"permissionMode": "acceptEdits"}, "permission mode 'acceptEdits'"),
                 ({"apiKeySource": "ANTHROPIC_API_KEY"}, "apiKeySource 'ANTHROPIC_API_KEY'"),
                 ({"session_id": other}, f"reported session '{other}'"),
                 ({"cwd": str(self.tmp)}, "reported working folder")]
        for i, (change, words) in enumerate(cases):
            with self.subTest(change=change):
                self.ready(f"p-{i}")
                self.refused(words, QUEUED, STARTED, purpose=f"p-{i}", FAKE_CLAUDE_MODE="sleep",
                             FAKE_CLAUDE_PRE_INIT_PAUSE="0", FAKE_CLAUDE_INIT_CHANGE=json.dumps(change))
        self.ready("p-second")
        self.refused("init event 2: the CLI started on model", QUEUED, STARTED, purpose="p-second",
                     FAKE_CLAUDE_MODE="sleep", FAKE_CLAUDE_PRE_INIT_PAUSE="0",
                     FAKE_CLAUDE_SECOND_INIT=json.dumps({"model": "claude-sonnet-5-5"}))

    def test_work_before_the_init_after_started_is_refused_and_says_the_transcript_is_unknown(self):
        sid = self.ready()
        for event in ({"type": "assistant", "parent_tool_use_id": None, "session_id": "$SID",
                       "message": {"model": "claude-opus-5-5", "content": [
                           {"type": "tool_use", "id": "toolu_x", "name": "Read", "input": {"file_path": "a"}}]}},
                      {"type": "user", "session_id": "$SID", "parent_tool_use_id": None,
                       "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_x"}]}},
                      {"type": "result", "subtype": "success", "is_error": False, "session_id": "$SID",
                       "num_turns": 1, "result": "acted"}):
            with self.subTest(event=event["type"]):
                res = self.refused(f"sent '{event['type']}'", QUEUED, STARTED, event)
                self.assertIn("the first message was started by the CLI", res["cause"])
                self.assertIn("whether it is in the transcript is unknown", res["cause"])
                self.assertFalse(res["session"]["delivered"])
                self.assertEqual((self.state()["status"], self.state()["session_id"]), ("ready", sid))
        self.assertEqual(self.managed().returncode, 0, "no refusal loop, same session")
        self.assertEqual(self.value(self.invocations()[-1]["argv"], "--resume"), sid)

    def test_lifecycle_then_the_end_of_output_keeps_the_session_and_takes_no_follow_up(self):
        sid = self.ready()
        r = self.managed(FAKE_CLAUDE_MODE="pre_init_only", FAKE_CLAUDE_PRE_INIT=self.pre_init(QUEUED, STARTED))
        self.assertEqual(r.returncode, 1)
        self.assertIn(b"never sent its init event", r.stderr)
        self.assertIn(b"whether it is in the transcript is unknown", r.stderr)
        self.assertEqual((self.state()["status"], self.state()["session_id"]), ("ready", sid))
        self.assertEqual(self.follow_up().returncode, 5)
        self.assertEqual(list((self.runs()[-1] / "inbox").glob("*.json")), [])

    def test_lifecycle_after_the_init_is_recorded_but_acknowledgment_stays_the_replay(self):
        proc, gate = self.start(FAKE_CLAUDE_LIFECYCLE="unmatched")
        r = self.follow_up()
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        out, err = self.finish(proc, gate)
        self.assertEqual(proc.returncode, 0, err.decode())
        res = self.result(self.runs()[0])
        [msg] = res["follow_ups"]["messages"]
        self.assertEqual((msg["outcome"], msg["acknowledged_by"]), ("answered (turn 1)", "uuid"))
        lc = res["command_lifecycle"]
        self.assertEqual([e["state"] for e in lc["by_command"][msg["uuid"]]], ["queued", "started"])
        self.assertEqual((lc["unmatched"], len(lc["by_command"])), (2, 2), "the unknown command's two events")

    def test_recover_classifies_a_lifecycle_prefix_the_same_way(self):
        sid = self.ready()
        proc, gate = self.start(FAKE_CLAUDE_PRE_INIT=self.pre_init(QUEUED, STARTED, "$REPLAY"))
        run = self.runs()[-1]
        proc.kill()
        proc.communicate(timeout=60)
        pid = self.invocations()[-1]["pid"]
        if os.name != "nt":
            os.kill(pid, 9)
        self.wait_for(lambda: not cs.pid_alive(pid), 10)
        r = self.run_helper("recover", "--purpose", "impl-board", "--confirm-stopped")
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        res = self.result(run)
        self.assertEqual(res["status"], "interrupted", res["cause"])
        self.assertEqual([(e["subtype"], e.get("state")) for e in res["pre_init_events"]],
                         [("command_lifecycle", "queued"), ("command_lifecycle", "started"), ("user_replay", None)])
        self.assertEqual((self.state()["status"], self.state()["session_id"]), ("ready", sid))


PRELUDE = {"subtype": "success", "is_error": False, "num_turns": 0, "terminal_reason": None, "stop_reason": None,
           "after_answers": 0}


class Preludes(Managed):
    """A zero-turn success result before the work is evidence, not an answer -- in the one context it was seen
    live: once, as the first result of a resume whose task notification came before the init."""

    def prelude_seen(self, run):
        self.wait_for(lambda: b'"num_turns": 0' in (run / "stdout.jsonl").read_bytes())

    def resumed(self, **env):
        """A ready managed purpose, then a gated managed resume with the observed notification before its init."""
        self.assertEqual(self.call("--accept-follow-ups").returncode, 0)
        proc, gate = self.start(FAKE_CLAUDE_PRE_INIT=self.pre_init(notification()), **env)
        return proc, gate, self.runs()[-1]

    def test_a_managed_prelude_keeps_the_inbox_open_and_is_not_a_turn(self):
        proc, gate, run = self.resumed(FAKE_CLAUDE_PRELUDE="1")
        self.prelude_seen(run)
        time.sleep(1.0)  # several inbox polls after the prelude result
        self.assertFalse((run / "inbox" / "closed").exists(), "a zero-turn prelude does not settle the call")
        r = self.follow_up()
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        self.assertIn(b"acknowledged by the CLI at", r.stdout)
        out, err = self.finish(proc, gate)
        self.assertEqual(proc.returncode, 0, err.decode())
        self.assertEqual(out, b"fake reply (turn 1, 2 message(s))\n", "one answering turn, no empty turn")
        res = self.result(run)
        [msg] = res["follow_ups"]["messages"]
        self.assertEqual((res["status"], len(res["turns"]), res["turns"][0]["num_turns"]), ("ok", 1, 1))
        self.assertEqual(res["turns"][0]["after_follow_ups"], [msg["uuid"]])
        self.assertEqual(msg["outcome"], "answered (turn 1)")
        self.assertEqual(res["follow_ups"]["prompt_message"]["outcome"], "answered (turn 1)")
        self.assertEqual(res["prelude_results"], [PRELUDE])
        self.assertEqual(res["result_event"]["num_turns"], 1)
        self.assertTrue(res["session"]["delivered"])
        self.assertEqual(self.state()["status"], "ready")

    def test_a_text_prelude_before_a_gated_turn_is_kept_as_evidence(self):
        gate = self.tmp / "gate"
        self.addCleanup(lambda: gate.exists() or gate.write_text("x"))
        args = [sys.executable, str(HELPER), "call", "--purpose", "impl-board", "--mode", "code", "--scope", "s",
                "--no-project-rules", "--prompt-file", str(self.prompt_file)]
        self.assertEqual(subprocess.run(args, cwd=str(self.sub), capture_output=True, env=self.env()).returncode, 0)
        helper = subprocess.Popen(args, cwd=str(self.sub), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  env=self.env(FAKE_CLAUDE_MODE="gate", FAKE_CLAUDE_GATE=str(gate),
                                               FAKE_CLAUDE_PRELUDE="1",
                                               FAKE_CLAUDE_PRE_INIT=self.pre_init(notification())))
        self.addCleanup(lambda: helper.poll() is not None or helper.kill())
        self.wait_for(lambda: len(self.runs()) == 2 and (self.runs()[1] / "stdout.jsonl").exists())
        run = self.runs()[1]
        self.prelude_seen(run)
        gate.write_text("go")
        out, err = helper.communicate(timeout=120)
        self.assertEqual((helper.returncode, out), (0, b"fake reply\n"), err.decode())
        res = self.result(run)
        self.assertEqual((res["status"], res["result_event"]["num_turns"]), ("ok", 1))
        self.assertEqual(res["prelude_results"], [PRELUDE])
        self.assertEqual(self.state()["status"], "ready")

    def test_acknowledgment_waiting_starts_at_the_last_answering_result(self):
        wrapper = self.tmp / "helper_short_ack_wait.py"
        wrapper.write_text(f"import sys\nsys.path.insert(0, {str(HELPER.parent)!r})\nimport claude_session as cs\n"
                           "cs.ACK_WAIT = 1.0\nsys.exit(cs.main())\n", encoding="utf-8")
        self.assertEqual(self.call("--accept-follow-ups").returncode, 0)
        proc, gate = self.start(helper=wrapper, FAKE_CLAUDE_PRELUDE="1", FAKE_CLAUDE_REPLAY="none",
                                FAKE_CLAUDE_PRE_INIT=self.pre_init(notification()))
        run = self.runs()[-1]
        self.prelude_seen(run)
        time.sleep(2.5)
        self.assertFalse((run / "inbox" / "closed").exists(), "no answering result yet, so no acknowledgment wait")
        out, err = self.finish(proc, gate)
        self.assertEqual(proc.returncode, 0, err.decode())
        self.assertIn("after its last answering result", self.result(run)["follow_ups"]["inbox_closed"]["reason"])
        self.assertEqual(self.result(run)["prelude_results"], [PRELUDE])

    def test_a_second_or_notification_free_zero_turn_success_is_terminal_and_closes_input(self):
        for name, env in (("a second zero-turn success", dict(FAKE_CLAUDE_PRELUDE="2", resumed=True)),
                          ("no task notification before the init", dict(FAKE_CLAUDE_PRELUDE="1", resumed=False))):
            with self.subTest(name):
                purpose = "p-" + env["FAKE_CLAUDE_PRELUDE"]
                if env.pop("resumed"):
                    self.assertEqual(self.call("--accept-follow-ups", purpose=purpose).returncode, 0)
                    env["FAKE_CLAUDE_PRE_INIT"] = self.pre_init(notification())
                runs_before = len(self.runs(purpose)) if self.pdir(purpose).exists() else 0
                started = time.monotonic()
                proc, gate = self.start("--timeout", "120", purpose=purpose, wait_init=False, **env)
                self.wait_for(lambda: (self.pdir(purpose) / "runs").is_dir() and len(self.runs(purpose)) > runs_before,
                              30)
                run = self.runs(purpose)[-1]
                self.wait_for(lambda: (run / "inbox" / "closed").exists(), 30)
                self.assertLess(time.monotonic() - started, 30, "input closed at once, gate still shut")
                self.assertIn("no turn", cs.read_json(run / "inbox" / "closed")["reason"])
                r = self.follow_up(purpose=purpose)
                self.assertEqual(r.returncode, 5, "nothing more is taken")
                self.finish(proc, gate)
                res = self.result(run)
                self.assertEqual(len(res["prelude_results"]), 1 if purpose == "p-2" else 0, "at most one prelude")

    def test_a_malformed_inbox_file_is_rejected_and_the_call_goes_on(self):
        proc, gate = self.start()
        run = self.runs()[0]
        u = "0b1c2d3e-0000-4000-8000-0000000000dd"
        partial = run / "inbox" / f"0001-{u}.part"  # written whole, then renamed, so it is never read half-done
        partial.write_text(json.dumps({"uuid": u, "sha256": "0" * 64, "bytes": 5, "text": "bad \ud800"}),
                           encoding="utf-8")
        os.replace(partial, run / "inbox" / f"0001-{u}.json")
        self.wait_for(lambda: (run / "acks" / f"{u}.rejected.json").exists())
        r = self.follow_up(text="steer")
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        out, err = self.finish(proc, gate)
        self.assertEqual(proc.returncode, 0, err.decode())
        bad, good = self.result(run)["follow_ups"]["messages"]
        self.assertEqual(bad["outcome"], "rejected: the follow-up is not valid UTF-8 text")
        self.assertEqual(good["outcome"], "answered (turn 1)")
        self.assertNotIn(b"bad ", b"".join(self.received()))


class FollowUpOutcomes(Managed):
    """RE-042: a follow-up to a finished call exits 5, and every follow-up states its outcome on stdout,
    because PowerShell's -Command host reports 1 for any native exit other than 0 or 1."""

    def last_line(self, r):
        return r.stdout.decode("utf-8").strip().splitlines()[-1]

    def nothing_sent(self, calls):
        self.assertEqual(len(self.invocations()), calls, "no CLI was launched")
        self.assertEqual([p for p in self.pdir().glob("runs/*/inbox/*.json")], [], "nothing was enqueued")

    def test_a_follow_up_to_a_finished_call_exits_5_and_says_not_running(self):
        self.assertEqual(self.call("--accept-follow-ups").returncode, 0)
        before = self.state()
        r = self.follow_up()  # the observed invocation: --purpose, --prompt-file, after the call completed
        self.assertEqual(r.returncode, 5, r.stderr.decode())
        self.assertIn(b"no call is running (status ready). Nothing was sent", r.stderr)
        self.assertEqual(self.last_line(r), "follow-up outcome: not_running (exit 5, sent no)")
        r = self.run_helper("follow-up", "--purpose", "impl-board", "--prompt-file", str(self.prompt_file), "--json")
        self.assertEqual(r.returncode, 5)
        record = json.loads(r.stdout)
        self.assertEqual((record["outcome"], record["exit"], record["sent"]), ("not_running", 5, "no"))
        self.assertIn("no call is running (status ready)", record["message"])
        self.nothing_sent(1)
        after = self.state()
        self.assertEqual({k: after[k] for k in ("status", "session_id", "calls", "failures", "last_run")},
                         {k: before[k] for k in ("status", "session_id", "calls", "failures", "last_run")})

    def test_powershell_rewrites_the_exit_but_not_the_outcome_record(self):
        hosts = [h for h in ("powershell", "pwsh") if shutil.which(h)]
        if not hosts:
            self.skipTest("no PowerShell host on this machine")
        self.assertEqual(self.call("--accept-follow-ups").returncode, 0)
        args = [sys.executable, str(HELPER), "follow-up", "--purpose", "impl-board", "--prompt-file",
                str(self.prompt_file)]
        quoted = " ".join(f"'{x}'" for x in args)
        for host in hosts:
            with self.subTest(host=host):
                plain = subprocess.run([host, "-NoProfile", "-Command", f"& {quoted}"], cwd=str(self.sub),
                                       capture_output=True, env=self.env(), timeout=180)
                self.assertEqual(plain.returncode, 1, "the host's own rewrite of exit 5 (documented PowerShell)")
                self.assertEqual(self.last_line(plain), "follow-up outcome: not_running (exit 5, sent no)")
                kept = subprocess.run([host, "-NoProfile", "-Command", f"& {quoted}; exit $LASTEXITCODE"],
                                      cwd=str(self.sub), capture_output=True, env=self.env(), timeout=180)
                self.assertEqual(kept.returncode, 5)
        self.nothing_sent(1)

    def test_start_up_finished_and_submitted_outcomes_are_distinct(self):
        init_gate = self.tmp / "init-gate"
        proc, gate = self.start(wait_init=False, FAKE_CLAUDE_INIT_GATE=str(init_gate))
        self.wait_for(lambda: ((self.read_state("impl-board") or {}).get("running") or {}).get("claude_pid"))

        def outcome(*extra, **env):
            f = self.tmp / f"steer-{time.monotonic_ns()}.md"
            f.write_text("steer", encoding="utf-8")
            r = self.run_helper("follow-up", "--purpose", "impl-board", "--prompt-file", str(f), "--wait", "20",
                                "--json", *extra, **env)
            record = json.loads(r.stdout)
            self.assertEqual(record["exit"], r.returncode)
            return record

        record = outcome()
        self.assertEqual((record["outcome"], record["exit"], record["sent"]), ("not_initialized", 5, "no"))
        init_gate.write_text("go")
        self.wait_for(lambda: ((self.read_state("impl-board") or {}).get("running") or {}).get("init_ok") is True)
        record = outcome()
        self.assertEqual((record["outcome"], record["exit"], record["sent"]), ("submitted", 0, "yes"))
        self.assertTrue(record["acknowledged_at"])
        self.assertEqual(outcome(SIJAV_CLAUDE="off")["outcome"], "off")
        out, err = self.finish(proc, gate)
        self.assertEqual(proc.returncode, 0, err.decode())
        self.assertEqual(self.result(self.runs()[0])["follow_ups"]["messages"][0]["outcome"], "answered (turn 1)")
        record = outcome()
        self.assertEqual((record["outcome"], record["exit"]), ("not_running", 5))
        record = outcome("--wait", "0")
        self.assertEqual((record["outcome"], record["exit"]), ("usage", 2))


class TerminalResults(Managed):
    """Results other than the initial prelude are terminal unless they answer; a streaming CLI stays alive after
    them, so the helper closes its input at once instead of waiting for the timeout."""

    def assert_prompt(self, started):
        self.assertLess(time.monotonic() - started, 30, "ended by the result, not by the 120 s timeout")

    def test_a_managed_turn_ending_in_a_zero_turn_error_fails_at_once_and_confirms_nothing_new(self):
        self.assertEqual(self.call("--accept-follow-ups").returncode, 0)
        sid = self.state()["session_id"]
        started = time.monotonic()
        r = self.call("--accept-follow-ups", "--timeout", "120", rules=False, FAKE_CLAUDE_TURN_RESULTS="error_zero")
        self.assert_prompt(started)
        self.assertEqual((r.returncode, r.stdout), (1, b""))
        self.assertIn(b"(failed, authentication)", r.stderr)
        res = self.result(self.runs()[-1])
        self.assertEqual((res["status"], res["timed_out"]), ("failed", False))
        self.assertIn("OAuth token has expired", res["cause"])
        self.assertIn("is_error True", res["follow_ups"]["inbox_closed"]["reason"])
        self.assertTrue(res["follow_ups"]["prompt_message"]["outcome"].startswith("ended by a 'success' result"))
        self.assertEqual(res["prelude_results"], [], "an error is never a prelude")
        st = self.state()
        self.assertEqual((st["status"], st["session_id"], st["failures"]), ("ready", sid, 1),
                         "an established purpose stays ready after its failed call")
        started = time.monotonic()
        r = self.call("--accept-follow-ups", "--timeout", "120", purpose="p-new", FAKE_CLAUDE_TURN_RESULTS="error_zero")
        self.assert_prompt(started)
        self.assertEqual(r.returncode, 1)
        st = self.state("p-new")
        self.assertEqual((st["status"], st["session_id"], st["candidate_reported"]), ("unconfirmed", None, True),
                         "a new purpose with no delivered turn stays unconfirmed")

    def test_a_follow_up_turn_ending_in_a_zero_turn_error_fails_and_is_not_answered(self):
        proc, gate = self.start("--timeout", "120", FAKE_CLAUDE_REPLAY="dequeue", FAKE_CLAUDE_TURN_RESULTS="ok,error_zero")
        self.assertEqual(self.follow_up(wait="2").returncode, 0)
        started = time.monotonic()
        out, err = self.finish(proc, gate)
        self.assert_prompt(started)
        self.assertEqual((proc.returncode, out), (1, b""), "turn 1's success does not hide turn 2's error")
        self.assertIn(b"(failed, authentication)", err)
        res = self.result(self.runs()[0])
        self.assertIn("OAuth token has expired", res["cause"])
        [msg] = res["follow_ups"]["messages"]
        self.assertEqual(msg["outcome"], "ended by a 'success' result (is_error True, num_turns 0, no turn; turn 2)")
        self.assertEqual(res["follow_ups"]["prompt_message"]["outcome"], "answered (turn 1)")
        self.assertEqual([t["answering"] for t in res["turns"]], [True, False])
        self.assertEqual(self.state()["status"], "ready", "turn 1 was delivered")

    def test_a_zero_turn_success_after_a_follow_up_ends_the_call_and_says_no_turn(self):
        proc, gate = self.start("--timeout", "120", FAKE_CLAUDE_REPLAY="dequeue",
                                FAKE_CLAUDE_TURN_RESULTS="ok,success_zero")
        self.assertEqual(self.follow_up(wait="2").returncode, 0)
        started = time.monotonic()
        out, err = self.finish(proc, gate)
        self.assert_prompt(started)
        self.assertEqual((proc.returncode, out), (0, b"fake reply (turn 1, 1 message(s))\n"), err.decode())
        res = self.result(self.runs()[0])
        [msg] = res["follow_ups"]["messages"]
        self.assertEqual(msg["outcome"], "ended by a 'success' result (is_error False, num_turns 0, no turn; turn 2)")
        self.assertIn(b"ended by a 'success' result", err, "the note says the follow-up got no turn")
        self.assertIn("no turn", res["follow_ups"]["inbox_closed"]["reason"])
        self.assertEqual(res["prelude_results"], [], "a zero-turn result after an answer is not a prelude")


class InboxUnits(unittest.TestCase):
    """The inbox's receipt and writer paths, in-process."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sijav inbox "))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.run_dir = self.tmp / "run"
        self.run_dir.mkdir()
        self.stream = cs.Stream("code", list(cs.CODE_TOOLS), "bd063f17-0778-4a84-b008-962d2ef7d32f", self.tmp,
                                follow_ups=True)
        self.inbox = cs.Inbox(self.run_dir, self.stream)
        self.inbox.dir.mkdir()
        self.inbox.acks.mkdir()

    def put(self, u, text, sha=None):
        f = self.inbox.dir / f"0001-{u}.json"
        sha = sha or cs.hashlib.sha256(text.encode("utf-8")).hexdigest()
        f.write_text(json.dumps({"uuid": u, "sha256": sha, "text": text}), encoding="utf-8")
        return f

    def rejected(self, u):
        return cs.read_json(self.inbox.acks / f"{u}.rejected.json")

    def test_a_follow_up_whose_taken_receipt_cannot_be_saved_is_rejected_not_sent(self):
        u = "0b1c2d3e-0000-4000-8000-0000000000e1"
        f = self.put(u, "steer")
        real = cs.write_new

        def failing(path, data):
            if path.name.endswith(".taken.json"):
                raise OSError("simulated: disk full")
            return real(path, data)

        with mock.patch.object(cs, "write_new", failing):
            self.inbox.take(f)
        self.assertNotIn(u, self.stream.sent)
        self.assertTrue(self.inbox.queue.empty())
        self.assertIn("taken receipt could not be saved (simulated: disk full)", self.rejected(u)["reason"])
        self.assertEqual(self.rejected(u)["sent"], "no")
        self.assertFalse((self.inbox.acks / f"{u}.taken.json").exists())

    def test_a_lone_surrogate_is_rejected_before_it_is_taken(self):
        u = "0b1c2d3e-0000-4000-8000-0000000000e2"
        self.inbox.take(self.put(u, "bad \ud800", sha="0" * 64))
        self.assertEqual(self.rejected(u), {"uuid": u, "reason": "the follow-up is not valid UTF-8 text", "sent": "no"})
        self.assertTrue(self.inbox.queue.empty())

    def test_the_writer_rejects_an_unserializable_message_and_keeps_writing(self):
        class Pipe(io.BytesIO):
            def close(self):
                self.captured = self.getvalue()
                super().close()

        pipe = Pipe()
        bad, good = "0b1c2d3e-0000-4000-8000-0000000000e3", "0b1c2d3e-0000-4000-8000-0000000000e4"
        self.inbox.enqueue(bad, "bad \ud800")
        self.inbox.enqueue(good, "fine")
        self.inbox.queue.put(None)
        self.inbox.start(pipe)
        self.inbox.writer.join(10)
        self.assertFalse(self.inbox.writer.is_alive(), "the writer neither died early nor hung")
        self.assertIn("cannot be serialized", self.rejected(bad)["reason"])
        self.assertEqual(self.rejected(bad)["sent"], "no", "a malformed message never reached the pipe")
        self.assertTrue((self.inbox.acks / f"{good}.submitted.json").exists())
        self.assertEqual(pipe.captured, cs.user_line(good, "fine"))
        self.assertEqual((self.run_dir / "stdin.jsonl").read_bytes(), cs.user_line(good, "fine"))
        self.assertIsNone(self.inbox.write_error)
        self.assertEqual(self.inbox.unwritten, set())

    def test_a_failed_pipe_write_is_unknown_and_later_messages_are_not_sent(self):
        class BrokenPipe(io.BytesIO):
            def write(self, data):
                raise BrokenPipeError(32, "simulated: the CLI's stdin is gone")

        first, later = "0b1c2d3e-0000-4000-8000-0000000000e5", "0b1c2d3e-0000-4000-8000-0000000000e6"
        self.inbox.enqueue(first, "one")
        self.inbox.enqueue(later, "two")
        self.inbox.queue.put(None)
        self.inbox.start(BrokenPipe())
        self.inbox.writer.join(10)
        self.assertFalse(self.inbox.writer.is_alive())
        self.assertEqual(self.rejected(first)["sent"], "unknown", "the failed write may have reached the CLI in part")
        self.assertIn("simulated: the CLI's stdin is gone", self.rejected(first)["reason"])
        self.assertEqual(self.rejected(later)["sent"], "no", "never attempted after the earlier failure")
        self.assertIn("an earlier write", self.rejected(later)["reason"])
        self.assertEqual((self.run_dir / "stdin.jsonl").read_bytes(), b"")
        self.assertIn("simulated", self.inbox.forced(), "the inbox is closed for the write failure")


class FollowUpFailures(unittest.TestCase):
    """`follow-up` in-process against a recorded live managed run (this process stands in for its helper and
    CLI): what it reports when its wait fails, is interrupted or cannot clean up, and how receipts decide sent."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sijav follow "))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.project = self.tmp / "project"
        (self.project / ".git").mkdir(parents=True)
        d = self.project / ".codex" / "claude-sessions" / "p1"
        self.run_dir = d / "runs" / "20261003T000000000000Z-g1-abcdef"
        (self.run_dir / "inbox").mkdir(parents=True)
        (self.run_dir / "acks").mkdir()
        st = cs.new_state("p1", "code", "s", self.project.resolve())
        st.update(status="running", running={
            "run_dir": str(self.run_dir), "helper_pid": os.getpid(), "claude_pid": os.getpid(), "first": True,
            "attempt": "first", "asked_id": "bd063f17-0778-4a84-b008-962d2ef7d32f", "previous_status": "new",
            "follow_ups": True, "init_ok": True})
        (d / "state.json").write_text(json.dumps(st), encoding="utf-8")
        self.prompt = self.tmp / "steer.md"
        self.prompt.write_text("steer", encoding="utf-8")

    def follow(self, during_wait, *extra):
        """Run follow-up; `during_wait(u)` runs at its first wait step, once the message is in the inbox."""
        real_sleep, state = time.sleep, {"done": False}

        def sleep(seconds):
            if not state["done"]:
                state["done"] = True
                [f] = (self.run_dir / "inbox").glob("*.json")
                during_wait(f.name.split("-", 1)[1][:-5])
            return real_sleep(seconds)

        out = io.StringIO()
        with mock.patch.object(cs.time, "sleep", sleep), mock.patch.dict(os.environ, {"SIJAV_CLAUDE": "on"}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            try:
                code = cs.main(["follow-up", "--project", str(self.project), "--purpose", "p1",
                                "--prompt-file", str(self.prompt), "--wait", "10", *extra])
                raised = None
            except KeyboardInterrupt as exc:
                code, raised = None, exc
        return code, raised, out.getvalue().strip().splitlines()[-1]

    def ack(self, u, kind):
        return self.run_dir / "acks" / f"{u}.{kind}.json"

    def test_an_interrupted_wait_withdraws_the_untaken_message_and_keeps_the_interruption(self):
        code, raised, last = self.follow(lambda u: (_ for _ in ()).throw(KeyboardInterrupt()))
        self.assertIsInstance(raised, KeyboardInterrupt, "the interruption itself still ends the process")
        self.assertEqual(last, "follow-up outcome: interrupted (exit none, sent no)")
        [f] = (self.run_dir / "inbox").glob("*.json")
        self.assertTrue(self.ack(f.name.split("-", 1)[1][:-5], "withdrawn").exists())

    def test_a_wait_error_runs_the_same_withdrawal_and_reports_failed(self):
        code, raised, last = self.follow(lambda u: (_ for _ in ()).throw(OSError("simulated: disk gone")))
        self.assertEqual((code, raised, last), (1, None, "follow-up outcome: failed (exit 1, sent no)"))

    def test_a_taken_message_is_unknown_when_the_wait_is_interrupted(self):
        def taken_then_interrupt(u):
            cs.write_new(self.ack(u, "taken"), {"uuid": u})
            raise KeyboardInterrupt

        code, raised, last = self.follow(taken_then_interrupt)
        self.assertEqual(last, "follow-up outcome: interrupted (exit none, sent unknown)")
        self.assertEqual(list((self.run_dir / "acks").glob("*.withdrawn.json")), [], "a taken message is not withdrawn")

    def test_cleanup_without_the_lock_keeps_the_original_cause_and_says_unknown(self):
        held = open(self.run_dir / "inbox.lock", "a+b")
        self.addCleanup(held.close)

        def lock_then_interrupt(u):
            cs._lock(held)
            raise KeyboardInterrupt

        code, raised, last = self.follow(lock_then_interrupt, "--json")
        record = json.loads(last)
        self.assertIsInstance(raised, KeyboardInterrupt)
        self.assertEqual((record["outcome"], record["sent"], record["exit"]), ("interrupted", "unknown", None))
        self.assertIn("stayed held", record["cleanup_error"])
        self.assertEqual(list((self.run_dir / "acks").glob("*.withdrawn.json")), [])
        cs._unlock(held)

    def test_rejection_receipts_decide_sent_by_their_field_and_a_legacy_one_is_unknown(self):
        for receipt, sent in (({"reason": "the write to the CLI's stdin failed", "sent": "no"}, "no"),
                              ({"reason": "nothing was sent", "sent": "unknown"}, "unknown"),
                              ({"reason": "nothing was sent"}, "unknown")):
            with self.subTest(receipt=receipt):
                for f in (self.run_dir / "inbox").glob("*.json"):
                    f.unlink()
                code, raised, last = self.follow(lambda u: cs.write_new(self.ack(u, "rejected"), {"uuid": u, **receipt}))
                self.assertEqual((code, last), (1, f"follow-up outcome: rejected (exit 1, sent {sent})"))

    def test_an_io_failure_while_persisting_is_an_outcome_not_a_traceback(self):
        shutil.rmtree(self.run_dir / "inbox")
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {"SIJAV_CLAUDE": "on"}), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            code = cs.main(["follow-up", "--project", str(self.project), "--purpose", "p1", "--prompt-file",
                            str(self.prompt)])
        self.assertEqual(code, 1)
        self.assertEqual(out.getvalue().strip().splitlines()[-1], "follow-up outcome: failed (exit 1, sent no)")
        self.assertIn("CLAUDE SESSION FAILED: FileNotFoundError", err.getvalue())

    def invoke_json(self):
        """follow-up --json in-process; returns (exit or None if interrupted, raised, record, seconds)."""
        out = io.StringIO()
        started = time.monotonic()
        with mock.patch.dict(os.environ, {"SIJAV_CLAUDE": "on"}), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(io.StringIO()):
            try:
                code, raised = cs.main(["follow-up", "--project", str(self.project), "--purpose", "p1",
                                        "--prompt-file", str(self.prompt), "--wait", "10", "--json"]), None
            except KeyboardInterrupt as exc:
                code, raised = None, exc
        return code, raised, json.loads(out.getvalue().strip().splitlines()[-1]), time.monotonic() - started

    def test_a_failure_as_the_persisting_lock_is_released_still_withdraws_the_complete_file(self):
        for fault, outcome in ((OSError("simulated: unlock failed"), "failed"), (KeyboardInterrupt(), "interrupted")):
            with self.subTest(fault=type(fault).__name__):
                real, calls = cs._unlock, {"n": 0}

                def unlock(handle):
                    real(handle)
                    calls["n"] += 1
                    if calls["n"] == 1:  # the exit of the lock the message was persisted under
                        raise fault

                with mock.patch.object(cs, "_unlock", unlock):
                    code, raised, record, _ = self.invoke_json()
                self.assertEqual((record["outcome"], record["sent"]), (outcome, "no"),
                                 "a complete inbox file is withdrawn, so 'no' is established, not assumed")
                self.assertEqual(raised is not None, isinstance(fault, KeyboardInterrupt))
                self.assertEqual(len(list((self.run_dir / "inbox").glob(f"*-{record['uuid']}.json"))), 1,
                                 "the file it settles is the complete one it persisted")
                self.assertTrue(self.ack(record["uuid"], "withdrawn").exists())

    def test_an_interrupt_right_after_the_file_is_written_still_withdraws_it(self):
        class Record(dict):
            """Interrupted exactly where sent first becomes 'unknown', after write_new has returned."""

            def update(self, *args, **kwargs):
                if kwargs.get("sent") == "unknown" and not getattr(self, "cut", False):
                    self.cut = True
                    raise KeyboardInterrupt
                return super().update(*args, **kwargs)

        record = Record(command="follow-up", purpose="p1", sent="no")
        a = argparse.Namespace(purpose="p1", project=str(self.project), prompt_file=str(self.prompt), wait=10,
                               json=False)
        with mock.patch.dict(os.environ, {"SIJAV_CLAUDE": "on"}), self.assertRaises(KeyboardInterrupt):
            cs.follow_up(a, record, io.StringIO())
        self.assertTrue(record.cut, "the interrupt hit the bookkeeping after persistence")
        [f] = (self.run_dir / "inbox").glob("*.json")
        self.assertEqual((record["uuid"], record["sent"]), (f.name.split("-", 1)[1][:-5], "no"))
        self.assertTrue(self.ack(record["uuid"], "withdrawn").exists())
        stream = cs.Stream("code", list(cs.CODE_TOOLS), "bd063f17-0778-4a84-b008-962d2ef7d32f", self.project,
                           follow_ups=True)
        inbox = cs.Inbox(self.run_dir, stream)
        inbox.take(f)  # what the calling helper would do next
        self.assertNotIn(record["uuid"], stream.sent, "the withdrawn file is never delivered")
        self.assertTrue(inbox.queue.empty())

    def test_the_write_error_survives_a_failed_unlock(self):
        real_write, real_unlink = cs.write_new, Path.unlink

        def write_new(path, data):
            real_write(path, data)
            if path.parent.name == "inbox":
                raise OSError("simulated: write error")

        def unlock(handle):
            raise OSError("simulated: unlock failed")  # closing the handle releases the lock

        def unlink(path, *args, **kwargs):
            if path.parent.name == "inbox":
                raise PermissionError(13, "simulated: the file is in use")
            return real_unlink(path, *args, **kwargs)

        for file_kept in (False, True):
            with self.subTest(file_kept=file_kept), mock.patch.object(cs, "write_new", write_new), \
                    mock.patch.object(cs, "_unlock", unlock), \
                    (mock.patch.object(Path, "unlink", unlink) if file_kept else contextlib.nullcontext()):
                code, raised, record, _ = self.invoke_json()
            self.assertEqual((code, record["outcome"], record["sent"]), (1, "failed", "no"))
            self.assertIn("simulated: write error", record["message"])
            self.assertNotIn("unlock failed", record["message"], "the original cause is not replaced")
            if file_kept:
                self.assertTrue(self.ack(record["uuid"], "withdrawn").exists())

    def test_a_late_write_error_with_a_failed_unlink_is_withdrawn_under_the_held_lock(self):
        real_write, real_unlink = cs.write_new, Path.unlink

        def unlink(path, *args, **kwargs):
            if path.parent.name == "inbox":
                raise PermissionError(13, "simulated: the file is in use")
            return real_unlink(path, *args, **kwargs)

        for withdrawal_fails in (False, True):
            with self.subTest(withdrawal_fails=withdrawal_fails):
                def write_new(path, data):
                    if path.parent.name == "acks" and withdrawal_fails:
                        raise OSError("simulated: receipt disk full")
                    real_write(path, data)
                    if path.parent.name == "inbox":
                        raise OSError("simulated: late write error")

                with mock.patch.object(cs, "write_new", write_new), mock.patch.object(Path, "unlink", unlink):
                    code, raised, record, seconds = self.invoke_json()
                self.assertEqual((code, record["outcome"]), (1, "failed"))
                self.assertIn("simulated: late write error", record["message"], "the original cause is reported")
                self.assertLess(seconds, 4, "settled under the held lock, not by waiting to take it again")
                withdrawn = self.ack(record["uuid"], "withdrawn")
                if withdrawal_fails:
                    self.assertEqual(record["sent"], "unknown")
                    self.assertIn("receipt disk full", record["cleanup_error"])
                    self.assertFalse(withdrawn.exists())
                else:
                    self.assertEqual(record["sent"], "no")
                    self.assertNotIn("cleanup_error", record)
                    self.assertTrue(withdrawn.exists())


class ResetAndContext(Base):
    def test_reset_needs_the_owner_and_keeps_history(self):
        self.assertEqual(self.call().returncode, 0)
        old = self.state()["session_id"]
        self.assertEqual(self.run_helper("reset", "--purpose", "impl-board").returncode, 2)
        (self.pdir() / "generations").mkdir()
        (self.pdir() / "generations" / "1.json").write_text("{}", encoding="utf-8")  # an earlier, unfinished reset
        r = self.run_helper("reset", "--purpose", "impl-board", "--authorized-by", "owner: start that review afresh")
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        st = self.state()
        self.assertEqual((st["generation"], st["status"], st["session_id"], st["rules_delivered"]), (2, "new", None, False))
        self.assertEqual(st["history"][0]["session_id"], old)
        archived = json.loads(Path(st["history"][0]["archive"]).read_text(encoding="utf-8"))
        self.assertEqual(archived["session_id"], old)
        self.assertEqual((self.pdir() / "generations" / "1.json").read_text(encoding="utf-8"), "{}")
        self.assertEqual(self.call(rules=False).returncode, 2, "the new conversation needs the rules")
        self.assertEqual(self.call().returncode, 0)
        argv = self.invocations()[-1]["argv"]
        self.assertNotEqual(self.value(argv, "--session-id"), old)
        ctx = self.run_helper("context", "--purpose", "impl-board", "--full")
        self.assertEqual(ctx.returncode, 0)
        text = ctx.stdout.decode("utf-8")
        for words in ("generation 1", "generation 2", "line two with trailing spaces"):
            self.assertIn(words, text)

    def test_list_status_and_context_never_launch(self):
        self.assertEqual(self.call().returncode, 0)
        before = len(self.invocations())
        for args in (["list"], ["list", "--json"], ["status", "--purpose", "impl-board"],
                     ["status", "--purpose", "impl-board", "--json"], ["context", "--purpose", "impl-board"]):
            r = self.run_helper(*args)
            self.assertEqual(r.returncode, 0, (args, r.stderr.decode()))
        self.assertEqual(len(self.invocations()), before)
        self.assertEqual(json.loads(self.run_helper("list", "--json").stdout)["purposes"][0]["purpose"], "impl-board")
        self.assertEqual(self.run_helper("recover", "--purpose", "no-such", "--confirm-stopped").returncode, 2)
        self.assertFalse(self.pdir("no-such").exists(), "a mistyped purpose creates nothing")


class Locations(Base):
    def test_project_from_subdirectory_with_spaces(self):
        self.assertEqual(self.call().returncode, 0)
        self.assertTrue(self.pdir().is_dir())
        self.assertFalse((self.sub / ".codex").exists())

    def test_a_stray_claude_folder_does_not_move_the_root(self):
        (self.project / "src" / ".claude").mkdir()
        r = self.run_helper("call", "--purpose", "p1", "--mode", "code", "--scope", "s", "--no-project-rules",
                            "--prompt-file", str(self.prompt_file), cwd=self.project / "src")
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        self.assertTrue(self.pdir("p1").is_dir())
        self.assertFalse((self.project / "src" / ".codex").exists())

    def test_the_nearest_board_wins_over_a_nested_repository(self):
        (self.project / ".claude").mkdir()
        (self.project / ".claude" / "todo.db").write_bytes(b"")
        nested = self.project / "vendor" / "lib"
        (nested / ".git").mkdir(parents=True)
        self.assertEqual(cs.project_root(None, nested), self.project.resolve())
        self.assertEqual(cs.project_root(None, self.sub), self.project.resolve())

    def test_home_claude_is_not_a_project(self):
        (self.home / ".claude").mkdir()
        loose = self.home / "loose folder"
        loose.mkdir()
        r = self.run_helper("call", "--purpose", "p1", "--mode", "code", "--scope", "s", "--no-project-rules",
                            "--prompt-file", str(self.prompt_file), cwd=loose)
        self.assertEqual(r.returncode, 2)
        self.assertIn(b"no project found", r.stderr)
        self.assertFalse((self.home / ".codex").exists())

    def test_explicit_project_and_a_cached_copy_of_the_helper(self):
        cache = self.tmp / "plugin cache" / "sijav codex" / "skills" / "claude"
        cache.mkdir(parents=True)
        copy = cache / "claude_session.py"
        shutil.copy2(HELPER, copy)
        r = self.run_helper("call", "--purpose", "p1", "--mode", "code", "--scope", "s", "--no-project-rules",
                            "--prompt-file", str(self.prompt_file), "--project", str(self.project),
                            cwd=self.tmp, helper=copy)
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        self.assertTrue(self.pdir("p1").is_dir())
        self.assertFalse((self.tmp / "plugin cache" / ".codex").exists())
        self.assertEqual(self.run_helper("list", "--project", str(self.tmp / "missing")).returncode, 2)

    def test_bad_purpose_names_are_refused(self):
        for bad in ("Roast", "../x", "a b", "", "-x"):
            self.assertEqual(self.call(purpose=bad).returncode, 2, bad)
        self.assertEqual(self.invocations(), [])

    def test_a_cli_in_the_current_or_project_folder_is_never_picked_from_path(self):
        here, good = self.tmp / "reviewed repo", self.tmp / "bin dir"
        here.mkdir()
        good.mkdir()
        name = "claude.cmd" if os.name == "nt" else "claude"
        for d in (here, good):
            (d / name).write_text("echo\n", encoding="utf-8")
            os.chmod(d / name, 0o755)
        with mock.patch.dict(os.environ, {"PATH": os.pathsep.join([".", str(here), str(good)]),
                                          "PATHEXT": ".CMD"}):
            found = cs.find_on_path("claude", [here])
        self.assertEqual(Path(found).parent, good)


class Settlement(unittest.TestCase):
    """ProcessTree's owned order (wait, stop, reap, settled) on real children of this test."""

    # A child that starts a grandchild sleeping 30 s, then says so on stdout and sleeps too.
    PARENT = ("import subprocess, sys, time\n"
              "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
              "print('started', flush=True)\n"
              "time.sleep(30)\n")

    def owned(self, code):
        tree = cs.ProcessTree()
        proc = tree.popen([sys.executable, "-c", code], Path.cwd(), dict(os.environ), subprocess.DEVNULL,
                          stdin=subprocess.DEVNULL)
        self.addCleanup(tree.close)
        self.addCleanup(proc.stdout.close)
        return tree, proc

    def test_a_stopped_tree_with_a_grandchild_is_settled(self):
        tree, proc = self.owned(self.PARENT)
        self.assertEqual(proc.stdout.readline().strip(), b"started")
        self.assertFalse(tree.wait(proc, 0.1))
        if os.name == "nt":
            self.assertGreaterEqual(tree.active() or 0, 2, "the child and its grandchild run in the Job Object")
        tree.stop(proc)
        self.assertIsNotNone(tree.reap(proc))
        owned = tree.job if os.name == "nt" else cs.OBSERVE_EXIT
        self.assertEqual(tree.settled(10), True if owned else None, tree.note)

    @unittest.skipUnless(os.name != "nt" and cs.OBSERVE_EXIT, "POSIX with os.waitid WNOWAIT only")
    def test_a_seen_exit_leaves_the_child_unreaped_until_reap(self):
        tree, proc = self.owned("pass")
        self.assertTrue(tree.wait(proc, 30))
        self.assertIsNone(proc.returncode, "seen, not reaped: the group id stays reserved for stop")
        tree.stop(proc)
        self.assertEqual(tree.reap(proc), 0)

    def test_without_its_owned_observation_settlement_is_unknown(self):
        # Labelled doubles: no Job Object on Windows, no WNOWAIT on POSIX. Not a native observation.
        if os.name == "nt":
            double = mock.patch.object(cs._kernel32(), "CreateJobObjectW", return_value=None)
        else:
            double = mock.patch.object(cs, "OBSERVE_EXIT", False)
        with double:
            tree, proc = self.owned("pass")
            self.assertTrue(tree.wait(proc, 30))
            tree.stop(proc)
            tree.reap(proc)
            self.assertIsNone(tree.settled(1), tree.note)
        self.assertIn("no Job Object" if os.name == "nt" else "cannot see the CLI exit", tree.note)

    def test_each_stop_and_the_close_are_recorded_as_observed(self):
        tree, proc = self.owned(self.PARENT)
        self.assertEqual(proc.stdout.readline().strip(), b"started")
        tree.stop(proc, "timeout")
        self.assertIsNotNone(tree.reap(proc))
        seen = tree.stops[0]
        self.assertEqual(seen["phase"], "timeout")
        self.assertIn("at", seen)
        if os.name == "nt" and tree.job:
            self.assertIs(seen["terminate_ok"], True)
            self.assertNotIn("last_error", seen, "a last error is recorded only for a failed call")
            self.assertIn("active_after", seen)
            self.assertIn("active_at", seen)
            tree.close()
            self.assertEqual(tree.stops[-1]["phase"], "close")
            self.assertIn("active_before_close", tree.stops[-1])
        elif os.name != "nt":
            self.assertEqual(seen["killpg"], "sent")
        if os.name != "nt":
            tree.stop(proc, "finally")
            self.assertIn("skipped", tree.stops[-1], "nothing is signalled after the reap")

    @unittest.skipUnless(os.name == "nt", "Windows Job Object calls")
    def test_a_failed_termination_and_an_unanswered_count_are_recorded_as_such(self):
        # Labelled doubles of two kernel32 calls; they show what is recorded, not how Windows behaves.
        import ctypes
        tree, proc = self.owned(self.PARENT)
        self.assertEqual(proc.stdout.readline().strip(), b"started")
        if not tree.job:
            self.skipTest("no Job Object on this host")
        k32 = cs._kernel32()

        def refused(job, code):
            ctypes.set_last_error(5)
            return 0

        with mock.patch.object(k32, "TerminateJobObject", side_effect=refused), \
                mock.patch.object(k32, "QueryInformationJobObject", return_value=0):
            tree.stop(proc, "timeout")
        seen = tree.stops[0]
        self.assertEqual((seen["terminate_ok"], seen["last_error"], seen["active_after"]), (False, 5, None))
        tree.stop(proc, "exit")  # the real call ends the owned tree
        self.assertIs(tree.stops[1]["terminate_ok"], True)
        self.assertIsNotNone(tree.reap(proc))

    @unittest.skipUnless(os.name != "nt" and cs.OBSERVE_EXIT, "POSIX with os.waitid WNOWAIT only")
    def test_a_child_reaped_outside_the_tree_is_no_longer_owned(self):
        # Labelled doubles: waitid as if another reaper took the child, and a recording killpg.
        tree, proc = self.owned("pass")
        with mock.patch.object(cs.os, "waitid", side_effect=ChildProcessError(10, "No child processes")), \
                mock.patch.object(cs.os, "killpg") as killpg:
            self.assertTrue(tree.wait(proc, 5), "no longer observable as this tree's child")
            tree.stop(proc)
            self.assertIsNone(tree.reap(proc), "its exit is unknown, not 0")
            self.assertIsNone(tree.settled(1))
        killpg.assert_not_called()
        self.assertFalse(tree.owned)
        self.assertIn("reaped outside this tree", tree.problems[0])
        self.assertIn("skipped", tree.stops[-1])

    def test_a_reap_that_times_out_stops_nothing_again(self):
        tree, proc = self.owned("import time; time.sleep(2)")
        with mock.patch.object(proc, "wait", side_effect=subprocess.TimeoutExpired("child", 1)):  # labelled double
            self.assertIsNone(tree.reap(proc, 1))
        self.assertTrue(tree.reap_tried)
        self.assertIn("reap timed out after 1 s", tree.problems[0])
        self.assertEqual(tree.stops[-1]["phase"], "reap")
        tree.stop(proc, "finally")
        self.assertEqual(tree.stops[-1]["skipped"], "a reap timed out; nothing is stopped again")
        proc.wait(30)  # the test's own child, reaped here once it ends by itself


class Units(unittest.TestCase):
    def report(self, status, result_problem="", state_problem=""):
        import contextlib
        import io
        outcome = {"status": status, "kind": "", "cause": "c", "reply": "the reply", "other_models_in_usage": [],
                   "permission_denials": []}
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cs.report("p", {"status": "ready", "session_id": "s"}, Path("run"), outcome, "", result_problem,
                             state_problem)
        return code, out.getvalue(), err.getvalue()

    def test_a_not_saved_report_says_what_was_not_saved(self):
        code, out, err = self.report("ok", result_problem="result.json could not be written: disk full")
        self.assertEqual((code, out), (6, "the reply\n"))
        self.assertIn("RUN RECORD NOT SAVED", err)
        self.assertNotIn("still shows this run as running", err, "the state was saved")
        code, out, err = self.report("ok", state_problem="could not write state.json")
        self.assertEqual(code, 6)
        self.assertIn("still shows this run as running", err)
        code, out, err = self.report("failed", state_problem="could not write state.json")
        self.assertEqual((code, out), (1, ""), "6 means a reply was printed; a failed call is 1")
        self.assertIn("SESSION STATE NOT SAVED", err)

    def test_batch_launchers_refuse_reinterpreted_characters(self):
        cs.check_batch_safe(["claude.exe", 'Bash(echo "x")'])
        cs.check_batch_safe(["C:/x/claude.cmd", "Bash(python -m unittest *)"])
        for bad in ('Bash(echo "x")', "Bash(echo %PATH%)", "Bash(a & b)", "Bash(a | b)"):
            with self.assertRaises(cs.SessionError):
                cs.check_batch_safe(["C:/x/claude.CMD", bad])

    def test_only_the_initial_non_error_zero_turn_success_is_a_prelude(self):
        sid = "bd063f17-0778-4a84-b008-962d2ef7d32f"

        def stream(*events, notified=True):
            s = cs.Stream("code", list(cs.CODE_TOOLS), sid, Path("."), follow_ups=True)
            if notified:  # the observed context: a resume's task notification before the init
                s.pre_init.append({"subtype": "task_notification", "task_id": "t", "status": "stopped"})
            s.init = {}  # past a validated init; only the result boundary is under test
            for ev in events:
                s.feed(json.dumps({"session_id": sid, **ev}).encode())
            return s

        zero = {"type": "result", "subtype": "success", "is_error": False, "num_turns": 0}
        work = {"type": "assistant", "parent_tool_use_id": None, "message": {"model": cs.MODEL}}
        answer = {"type": "result", "subtype": "success", "is_error": False, "num_turns": 1}
        s = stream(zero, work, answer)
        self.assertEqual((len(s.preludes), s.answering, s.ended_by), (1, [True], None))
        for name, events in (("an error", [{**zero, "is_error": True}]),
                             ("another subtype", [{**zero, "subtype": "error_during_execution"}]),
                             ("a boolean num_turns", [{**zero, "num_turns": False}]),
                             ("after an answer", [work, answer, zero])):
            with self.subTest(name):
                s = stream(*events)
                self.assertEqual(s.preludes, [], name)
                self.assertIsNotNone(s.ended_by, name)
        s = stream(zero, zero)
        self.assertEqual((len(s.preludes), s.answering), (1, [False]), "at most one prelude; the second is terminal")
        self.assertIn("no turn", s.ended_by)
        s = stream(zero, notified=False)
        self.assertEqual((s.preludes, s.answering), ([], [False]), "no notification before the init: terminal")
        self.assertIsNotNone(s.ended_by)
        self.assertFalse(stream({**zero, "is_error": True}).delivered, "a zero-turn error delivers nothing")

    def test_failure_kinds_need_whole_words(self):
        self.assertEqual(cs.kind_of("Error: Not logged in \u00b7 Please run /login"), "authentication")
        self.assertEqual(cs.kind_of("OAuth token has expired"), "authentication")
        self.assertEqual(cs.kind_of("API Error: 429 Too Many Requests"), "allowance or rate limit")
        self.assertEqual(cs.kind_of("You've hit your usage limit"), "allowance or rate limit")
        for innocent in ("session 4c01-1401a-401f took 1401ms", "the cache entry expired", "see the login page docs"):
            self.assertEqual(cs.kind_of(innocent), "error", innocent)


if __name__ == "__main__":
    unittest.main()
