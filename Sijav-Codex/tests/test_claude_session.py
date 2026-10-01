"""claude_session.py with a strict fake Claude CLI (tests/fixtures/fake_claude.py) in temporary folders.

The fake parses its options from the real Claude Code 2.1.286 help and builds its init from a real
2.1.286 init event (tests/fixtures/claude-2.1.286), rejects unknown flags and choices, and resumes
only sessions it actually created. Every test runs the real helper as a subprocess, as the
orchestrator would, unless it tests one function. No model is called.

Expected arguments and permission rules are written out literally here from Claude Code's
documented syntax (https://code.claude.com/docs/en/permissions), not read back from the helper.
"""

import base64
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


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sijav claude "))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
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
                                "--disallowedTools", "NotebookEdit", "Bash", "PowerShell", *PROTECTED, *SECRETS])
        self.assert_clean(argv)
        self.assertEqual(Path(inv["cwd"]).resolve(), self.project.resolve())
        st = self.state()
        self.assertEqual((st["status"], st["session_id"], st["rules_delivered"]), ("ready", sid, True))

        sent = base64.b64decode(inv["stdin_b64"])
        self.assertTrue(sent.endswith(PROMPT.encode("utf-8")), "the prompt reaches the CLI byte for byte")
        self.assertIn(b"G1: tell the truth.", sent)
        self.assertIn(str(self.rules.resolve()).encode("utf-8"), sent)

        [run] = self.runs()
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
                                "--disallowedTools", "Write", "Edit", "NotebookEdit", "Bash", "PowerShell", *SECRETS])

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
        for bad in ("Bash", "Bash(*)", "Bash( * )", "Bash(:*)", "Python(x)", "Bash(a)(b)", "Bash(* --version)",
                    "Bash(command:rm *)", "Bash(py* -m x)"):
            r = self.call("--allow-command", bad, purpose="p-bad")
            self.assertEqual(r.returncode, 2, bad)
        self.assertEqual(len(self.invocations()), 1)

    def test_every_argument_shape_parses_under_the_pinned_2_1_286_help(self):
        sid = "bd063f17-0778-4a84-b008-962d2ef7d32f"
        shapes = [cs.build_argv([sys.executable, str(FAKE)], mode, effort, allow, session)
                  for mode, allow in (("technical", []), ("code", []), ("code", ["Bash(python -m unittest *)"]),
                                      ("code", ["PowerShell(Get-ChildItem *)"]))
                  for effort in cs.EFFORTS for session in (["--session-id", sid],)]
        env = self.env(FAKE_CLAUDE_MODE="garbage")
        for argv in shapes:
            r = subprocess.run(argv, input=b"x", capture_output=True, env=env, cwd=str(self.project), timeout=60)
            self.assertEqual((r.returncode, r.stderr), (0, b""), argv)

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
        self.assertEqual(inv["env"], ["CLAUDE_CODE_OAUTH_TOKEN"])
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

    def test_a_descendant_holding_stdout_cannot_hang_the_call(self):
        pid_file = self.tmp / "grandchild.pid"
        started = time.monotonic()
        r = self.call("--timeout", "120", FAKE_CLAUDE_MODE="orphan", FAKE_CLAUDE_CHILD_PID=str(pid_file))
        self.assertLess(time.monotonic() - started, 40, "the call ends when the CLI ends, not after 60 s")
        self.assertEqual(r.returncode, 1)
        res = self.result(self.runs()[0])
        self.assertIn("without a result event", res["cause"])
        self.assertTrue(res["output_drained"])
        grandchild = int(pid_file.read_text())
        deadline = time.monotonic() + 10
        while cs.pid_alive(grandchild) and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertFalse(cs.pid_alive(grandchild), "the CLI's leftover descendant was stopped with the call")

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


class Concurrency(Base):
    def wait_for(self, predicate, seconds=60):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.05)
        self.fail("timed out waiting")

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

    def test_failure_kinds_need_whole_words(self):
        self.assertEqual(cs.kind_of("Error: Not logged in \u00b7 Please run /login"), "authentication")
        self.assertEqual(cs.kind_of("OAuth token has expired"), "authentication")
        self.assertEqual(cs.kind_of("API Error: 429 Too Many Requests"), "allowance or rate limit")
        self.assertEqual(cs.kind_of("You've hit your usage limit"), "allowance or rate limit")
        for innocent in ("session 4c01-1401a-401f took 1401ms", "the cache entry expired", "see the login page docs"):
            self.assertEqual(cs.kind_of(innocent), "error", innocent)


if __name__ == "__main__":
    unittest.main()
