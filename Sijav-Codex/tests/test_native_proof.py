"""Offline tests of the live-proof harness. Nothing here starts Codex or calls
a model: fixture preparation, refusals, hooks/list verification, redaction,
the environment scrub, cleanup and the result evaluators are exercised on
disposable temp folders and synthetic data. Fixture hook commands are run
through Codex's own spawn shape (see test_loop.native_spawn). The live proof
itself runs only through `native_proof.py --run`.

    python -m unittest -v tests/test_native_proof.py
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import native_proof as proof  # noqa: E402
from test_loop import ENV, HOOK_SHELLS, IDENTITY, native_spawn  # noqa: E402

PY = sys.executable


def quiet(fn, *args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        result = fn(*args)
    return result, out.getvalue()


def no_launch(*_args, **_kwargs):
    raise AssertionError("the harness tried to start Codex")


class FixtureTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="native proof ")
        self.base = self._tmp.name
        self.fixture = os.path.join(self.base, "proof fixture's dir")
        os.makedirs(self.fixture)

    def tearDown(self):
        self._tmp.cleanup()

    def prepare(self):
        code, out = quiet(proof.prepare, self.fixture, PY)
        self.assertEqual(code, 0, out)
        return out

    def command(self, spec, event):
        return proof.handler_of(spec, event)[proof.VARIANT]

    def test_prepare_writes_the_plugins_commands_with_concrete_unquoted_interpreter(self):
        out = self.prepare()
        with open(os.path.join(self.fixture, ".codex", "hooks.json"), encoding="utf-8") as f:
            written = json.load(f)
        self.assertEqual(written, proof.fixture_hooks(PY))
        with open(proof.PLUGIN_HOOKS, encoding="utf-8") as f:
            plugin = json.load(f)
        for event, groups in plugin["hooks"].items():
            for group, fixture_group in zip(groups, written["hooks"][event]):
                self.assertEqual({k: v for k, v in group.items() if k != "hooks"},
                                 {k: v for k, v in fixture_group.items() if k != "hooks"})
                for handler, fixture_handler in zip(group["hooks"], fixture_group["hooks"]):
                    same = {k: v for k, v in handler.items() if k not in ("command", "commandWindows")}
                    self.assertEqual(same, {k: v for k, v in fixture_handler.items()
                                            if k not in ("command", "commandWindows")})
                    posix, windows = fixture_handler["command"], fixture_handler["commandWindows"]
                    self.assertNotEqual(posix, windows, "the selected variant must be visible")
                    for line, plugin_line, convert in (
                            (posix, handler["command"], lambda p: p.replace("\\", "/")),
                            (windows, handler["commandWindows"], lambda p: p.replace("/", "\\"))):
                        self.assertTrue(line.startswith(f"{convert(PY)} -c \""), line)
                        self.assertFalse(line.startswith('"'), "a quoted interpreter breaks PowerShell")
                        self.assertNotIn("${PLUGIN_ROOT}", line)
                        self.assertIn(f'"{convert(proof.STAGE)}', line)
                        self.assertEqual(line.split('"', 2)[1], plugin_line.split('"', 2)[1],
                                         "the launcher bootstrap is the plugin's")
        self.assertEqual(sorted(os.listdir(self.fixture)), [".codex", proof.MARKER])
        for text in ("/hooks", "Never press t on the Events list", "Launcher dependencies", "--check",
                     "modified"):
            self.assertIn(text, out)

    def test_prepare_again_in_place_keeps_the_superseded_definitions(self):
        self.prepare()
        hooks = os.path.join(self.fixture, ".codex", "hooks.json")
        with open(hooks, encoding="utf-8") as f:
            spec = json.load(f)
        spec["hooks"]["Stop"][0]["hooks"][0]["commandWindows"] = '"old" "shape" stop'
        with open(hooks, "w", encoding="utf-8") as f:
            json.dump(spec, f)
        os.makedirs(os.path.join(self.fixture, "proof-runs", "20261001T035016Z-run"))
        out = self.prepare()
        self.assertIn("Kept the previous marker and hook definitions", out)
        runs = os.listdir(os.path.join(self.fixture, "proof-runs"))
        self.assertIn("20261001T035016Z-run", runs, "earlier records are kept")
        (kept,) = [r for r in runs if r.endswith("-superseded-preparation")]
        with open(os.path.join(self.fixture, "proof-runs", kept, "hooks.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["hooks"]["Stop"][0]["hooks"][0]["commandWindows"], '"old" "shape" stop')
        self.assertTrue(os.path.isfile(os.path.join(self.fixture, "proof-runs", kept, proof.MARKER)))
        with open(hooks, encoding="utf-8") as f:
            self.assertEqual(json.load(f), proof.fixture_hooks(PY))

    def test_an_interpreter_that_needs_quoting_is_refused(self):
        spaced = os.path.join(self.base, "python with space.exe")
        with open(spaced, "w") as f:
            f.write("")
        code, out = quiet(proof.main, ["--prepare", self.fixture, "--python", spaced])
        self.assertEqual(code, 2)
        self.assertIn("usable unquoted", out)
        self.assertEqual(os.listdir(self.fixture), [])

    def test_fixture_commands_run_the_real_hook_in_every_hook_shell(self):
        self.prepare()
        spec = proof.fixture_hooks(PY)
        event = {"session_id": "thread-1", "cwd": self.fixture, "hook_event_name": "Stop",
                 "turn_id": "t", "stop_hook_active": False, "last_assistant_message": "hi"}
        for label, _ in HOOK_SHELLS:
            proc = native_spawn(self.command(spec, "stop"), event, self.fixture, shell=label)
            self.assertEqual(proc.returncode, 0, (label, proc.stderr))
            self.assertEqual(proc.stdout.strip(), b"", label)
            self.assertIn(b"not armed", proc.stderr, label)
        law_dir = tempfile.mkdtemp(prefix="sijav-proof-laws-")
        self.addCleanup(lambda: __import__("shutil").rmtree(law_dir, ignore_errors=True))
        law = os.path.join(law_dir, "law.md")
        with open(law, "w", encoding="utf-8") as f:
            f.write("Fixture law body\n")
        cli = subprocess.run([PY, proof.CLI_SCRIPT, "start", "--project-root", self.fixture, "--law", law,
                              "--session", "thread-1", "--max-iterations", "100", "--promise", "DONE"],
                             capture_output=True, cwd=self.fixture, env=ENV, timeout=60)
        self.assertEqual(cli.returncode, 0, cli.stderr)
        for label, _ in HOOK_SHELLS:
            proc = native_spawn(self.command(spec, "stop"), event, self.fixture, shell=label)
            out = json.loads(proc.stdout)
            self.assertEqual(out["decision"], "block", label)
            self.assertTrue(out["reason"].endswith("Fixture law body\n"), label)
            compact = {"session_id": "thread-1", "cwd": self.fixture, "hook_event_name": "SessionStart",
                       "source": "compact"}
            proc = native_spawn(self.command(spec, "sessionStart"), compact, self.fixture, shell=label)
            context = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
            self.assertTrue(context.endswith("Fixture law body\n"), label)
        self.assertIsNone(proof.fixture_problem(self.fixture, prepared=True))

    def test_fixture_launcher_failures_never_exit_2(self):
        event = {"session_id": "s", "cwd": self.fixture, "hook_event_name": "Stop"}
        moved = proof.fixture_hooks(PY, root=os.path.join(self.base, "moved stage"))
        missing_python = os.path.join(tempfile.gettempdir(), "sijav-no-python",
                                      "python.exe" if os.name == "nt" else "python3")
        for label, _ in HOOK_SHELLS:
            proc = native_spawn(self.command(moved, "stop"), event, self.fixture, shell=label)
            self.assertEqual(proc.returncode, 0, (label, proc.stderr))
            self.assertIn("did not start", json.loads(proc.stdout)["systemMessage"], label)
            proc = native_spawn(self.command(proof.fixture_hooks(missing_python), "stop"), event,
                                self.fixture, shell=label)
            self.assertNotIn(proc.returncode, (0, 2), label)
            self.assertNotIn(b'"decision"', proc.stdout, label)

    def test_refuses_folders_it_does_not_own(self):
        with open(os.path.join(self.fixture, "notes.txt"), "w") as f:
            f.write("someone's work")
        self.assertIn("not empty", proof.fixture_problem(self.fixture, prepared=False))
        os.remove(os.path.join(self.fixture, "notes.txt"))
        self.prepare()
        with open(os.path.join(self.fixture, ".codex", "config.toml"), "w") as f:
            f.write("")
        self.assertIn("does not own", proof.fixture_problem(self.fixture, prepared=True))

    def test_refuses_active_trees_repositories_ancestor_state_and_unsafe_paths(self):
        problem = proof.fixture_problem
        self.assertIn("overlaps the staging tree", problem(os.path.join(proof.STAGE, "tests"), False, outside_temp_ok=True))
        self.assertIn("overlaps the staging tree", problem(os.path.dirname(proof.STAGE), False, outside_temp_ok=True))
        self.assertIn("caller's working directory", problem(self.fixture, False, caller_cwd=self.base))
        self.assertIn("caller's working directory", problem(self.fixture, False, caller_cwd=os.path.join(self.fixture)))
        elsewhere = tempfile.mkdtemp(prefix="not temp ")
        self.addCleanup(os.rmdir, elsewhere)
        with mock.patch.object(proof.tempfile, "gettempdir", return_value=elsewhere):
            self.assertIn("not below the system temp directory", problem(self.fixture, False))
            self.assertIsNone(problem(self.fixture, False, outside_temp_ok=True))
        repo = os.path.join(self.base, "repo", "fixture")
        os.makedirs(repo)
        os.makedirs(os.path.join(self.base, "repo", ".git"))
        self.assertIn("git repository", problem(repo, False))
        project = os.path.join(self.base, "project", "fixture")
        os.makedirs(project)
        state = os.path.join(self.base, "project", ".codex", "sijav-loop", "state.json")
        os.makedirs(os.path.dirname(state))
        with open(state, "w") as f:
            f.write("{}")
        self.assertIn("holds Sijav loop state", problem(project, False))
        odd = os.path.join(self.base, "100%")
        os.makedirs(odd)
        self.assertIn("plain path", problem(odd, False))
        self.assertIn("not an existing directory", problem(os.path.join(self.base, "missing"), False))

    def test_a_fixture_from_the_old_harness_is_refused(self):
        self.prepare()
        marker = os.path.join(self.fixture, proof.MARKER)
        with open(marker, encoding="utf-8") as f:
            data = json.load(f)
        data["harness"] = "sijav-native-proof/2"
        with open(marker, "w", encoding="utf-8") as f:
            json.dump(data, f)
        with self.assertRaises(proof.ProofError) as caught:
            proof.check_fixture_files(self.fixture)
        self.assertIn("run --prepare again in this fixture", str(caught.exception))

    def test_run_and_check_refuse_before_launching_codex(self):
        with mock.patch.object(proof, "AppServer", side_effect=no_launch):
            for mode in ("--run", "--check"):
                code, out = quiet(proof.main, [mode, self.fixture])
                self.assertEqual(code, 2, out)
                self.assertIn("has not been prepared", out)
            self.prepare()
            path = os.path.join(self.fixture, ".codex", "hooks.json")
            with open(path, encoding="utf-8") as f:
                spec = json.load(f)
            spec["hooks"]["Stop"][0]["hooks"][0]["timeout"] = 600
            with open(path, "w", encoding="utf-8") as f:
                json.dump(spec, f)
            code, out = quiet(proof.main, ["--run", self.fixture])
            self.assertEqual(code, 2)
            self.assertIn("no longer matches", out)

    def test_a_mode_is_required(self):
        with self.assertRaises(SystemExit):
            quiet(proof.main, [])


class EnvironmentTests(unittest.TestCase):
    def test_identity_variables_are_removed_and_nothing_else(self):
        fake = {"CODEX_THREAD_ID": "t", "CODEX_SESSION_ID": "s", "CODEX_PARENT_THREAD_ID": "p",
                "CODEX_HOME": "h", "OPENAI_API_KEY": "k", "PATH": "x"}
        with mock.patch.dict(os.environ, fake, clear=True):
            env, removed = proof.child_env()
        self.assertEqual(removed, ["CODEX_PARENT_THREAD_ID", "CODEX_SESSION_ID", "CODEX_THREAD_ID"])
        self.assertEqual(env, {"CODEX_HOME": "h", "OPENAI_API_KEY": "k", "PATH": "x"})
        for name in IDENTITY:
            self.assertNotIn(name, ENV)


def listed(fixture, stop_status="trusted", start_status="trusted", enabled=True, extra=(), errors=(),
           variant=proof.VARIANT):
    path = os.path.join(fixture, ".codex", "hooks.json")
    spec = proof.fixture_hooks(PY)

    def hook(event, status):
        return {"eventName": event, "matcher": proof.MATCHERS[event],
                "command": proof.handler_of(spec, event)[variant],
                "enabled": enabled, "trustStatus": status, "timeoutSec": 30, "sourcePath": path,
                "source": "project", "additionalContextLimit": 0 if event == "sessionStart" else None,
                "currentHash": "h", "displayOrder": 0, "isManaged": False, "key": event}

    hooks = [hook("stop", stop_status), hook("sessionStart", start_status)]
    return {"data": [{"cwd": fixture, "hooks": hooks + list(extra), "errors": list(errors), "warnings": []}]}


class VerifyHooksTests(unittest.TestCase):
    fixture = os.path.abspath(os.path.join(tempfile.gettempdir(), "never created fixture"))

    def verify(self, result, allow_foreign=False):
        return proof.verify_hooks(result, self.fixture, proof.ProjectForm(PY, self.fixture), allow_foreign)

    def test_trusted_enabled_selected_variant_passes(self):
        verdict = self.verify(listed(self.fixture))
        self.assertTrue(verdict["ok"], verdict["problems"])
        self.assertEqual(verdict["selected_variant"], proof.VARIANT)

    def test_the_other_platforms_variant_fails(self):
        problems = " ".join(self.verify(listed(self.fixture, variant=proof.OTHER_VARIANT))["problems"])
        self.assertIn(f"reports the {proof.OTHER_VARIANT} variant", problems)

    def test_untrusted_modified_disabled_or_missing_fail(self):
        self.assertIn("'untrusted'", " ".join(self.verify(listed(self.fixture, stop_status="untrusted"))["problems"]))
        self.assertIn("'modified'", " ".join(self.verify(listed(self.fixture, start_status="modified"))["problems"]))
        self.assertIn("disabled", " ".join(self.verify(listed(self.fixture, enabled=False))["problems"]))
        empty = {"data": [{"cwd": self.fixture, "hooks": [], "errors": [], "warnings": []}]}
        self.assertEqual(len(self.verify(empty)["problems"]), 2)

    def test_changed_definitions_fail(self):
        result = listed(self.fixture)
        result["data"][0]["hooks"][1]["additionalContextLimit"] = None
        result["data"][0]["hooks"][0]["command"] = "python other.py"
        problems = " ".join(self.verify(result)["problems"])
        self.assertIn("additionalContextLimit", problems)
        self.assertIn("other.py", problems)

    def test_foreign_stop_hooks_and_fixture_errors_fail(self):
        foreign = {"eventName": "stop", "source": "user", "sourcePath": "C:/Users/x/.codex/hooks.json",
                   "command": "other", "enabled": True, "trustStatus": "trusted"}
        self.assertFalse(self.verify(listed(self.fixture, extra=[foreign]))["ok"])
        self.assertTrue(self.verify(listed(self.fixture, extra=[foreign]), allow_foreign=True)["ok"])
        error = {"path": os.path.join(self.fixture, ".codex", "hooks.json"), "message": "bad json"}
        self.assertIn("bad json", " ".join(self.verify(listed(self.fixture, errors=[error]))["problems"]))
        elsewhere = {"path": "C:/elsewhere/hooks.json", "message": "unrelated"}
        verdict = self.verify(listed(self.fixture, errors=[elsewhere]))
        self.assertTrue(verdict["ok"])
        self.assertIn("unrelated", " ".join(verdict["warnings"]))


class RedactionTests(unittest.TestCase):
    def test_credentials_are_removed(self):
        data = {"account": {"email": "a@b.c", "access_token": "x", "plan": "pro"},
                "note": "Authorization: Bearer abcdefghijklmnop and sk-abcdefghijk",
                "items": [{"clientSecret": "s"}], "inputTokens": 12}
        clean = proof.redact(data)
        text = json.dumps(clean)
        for secret in ("a@b.c", '"x"', "abcdefghijklmnop", "sk-abcdefghijk", '"s"'):
            self.assertNotIn(secret, text)
        self.assertEqual(clean["account"]["plan"], "pro")
        self.assertEqual(clean["inputTokens"], 12)


class CleanupTests(unittest.TestCase):
    def server(self, run_dir):
        server = object.__new__(proof.AppServer)
        server.run_dir = run_dir
        server.closed = False
        server.record = {"pid": 1}
        server.log = open(os.path.join(run_dir, "protocol.jsonl"), "a", encoding="utf-8")
        server.stderr_log = open(os.path.join(run_dir, "app-server.stderr.log"), "a", encoding="utf-8")
        proc = mock.Mock()
        proc.stdin = io.BytesIO()
        proc.wait.side_effect = subprocess.TimeoutExpired("codex", 10)
        proc.poll.return_value = None
        server.proc = proc
        return server

    def test_the_record_is_written_when_termination_times_out_or_fails(self):
        with tempfile.TemporaryDirectory() as run_dir:
            server = self.server(run_dir)
            with mock.patch.object(proof, "kill_tree", return_value=False):
                server.close(failed=True)
            with open(os.path.join(run_dir, "process.json"), encoding="utf-8") as f:
                record = json.load(f)
            self.assertIn("had not exited", record["termination"])
            self.assertTrue(server.log.closed and server.stderr_log.closed)
            server = self.server(run_dir)
            with mock.patch.object(proof, "kill_tree", side_effect=RuntimeError("taskkill vanished")):
                with self.assertRaises(RuntimeError):
                    server.close(failed=True)
            with open(os.path.join(run_dir, "process.json"), encoding="utf-8") as f:
                self.assertIn("cleanup interrupted", json.load(f)["termination"])
            self.assertTrue(server.log.closed)

    def test_kill_tree_survives_a_process_that_will_not_die(self):
        proc = mock.Mock()
        proc.pid = 999999
        proc.poll.return_value = None
        proc.wait.side_effect = subprocess.TimeoutExpired("x", 1)
        with mock.patch.object(proof.subprocess, "run"), mock.patch.object(proof.os, "killpg", create=True):
            self.assertFalse(proof.kill_tree(proc))


class EvaluatorTests(unittest.TestCase):
    def turn(self, texts, runs, items=("userMessage", "agentMessage"), commentary=()):
        messages = [{"seq": 5 + i, "text": t, "phase": "commentary"} for i, t in enumerate(commentary)]
        messages += [{"seq": 10 + i, "text": t, "phase": "final_answer" if i % 2 else None} for i, t in enumerate(texts)]
        return {"turn_id": "T", "status": "completed", "error": None, "reroutes": [], "item_types": list(items),
                "agent_messages": messages,
                "hook_runs": [{"event": "stop", "fixture_hook": True, "status": "completed", "entries": [],
                               "seq": 20 + i} for i in range(runs)]}

    def stops(self, outcomes, hashes=None, same=None):
        """Callbacks as Codex sends them: stop_hook_active false first, then true."""
        hashes = hashes or [f"h{i}" for i in range(len(outcomes))]
        records = [{"outcome": o, "turn_id": "T", "stop_hook_active": i > 0, "message_sha256": hashes[i]}
                   for i, o in enumerate(outcomes)]
        recorded = same or proof.expected_same_as_previous(records)
        for record, flag in zip(records, recorded):
            record["same_as_previous_stop"] = flag
        return records

    def test_completing_fixture(self):
        tok = "ab12"
        texts = [f"A-STAGE-{i} {tok}" for i in range(3)] + [f"A-STAGE-3 {tok}\n<promise>PROOF-A-{tok}</promise>"]
        state = {"status": "complete", "iteration": 3, "status_reason": "r", "max_iterations": 5}
        stops = self.stops(["continued"] * 3 + ["complete"])
        checks = proof.evaluate_a(self.turn(texts, 4, commentary=["Thinking about the stage."]), stops, state, tok)
        self.assertTrue(all(c["ok"] for c in checks), [c for c in checks if not c["ok"]])
        bad = proof.evaluate_a(self.turn(texts, 4), self.stops(["continued"] * 4), dict(state, status="active"), tok)
        self.assertFalse(all(c["ok"] for c in bad))
        fenced = texts[:3] + [f"A-STAGE-3 {tok}\n```\n<promise>PROOF-A-{tok}</promise>\n```"]
        checks = proof.evaluate_a(self.turn(fenced, 4), stops, state, tok)
        self.assertFalse(next(c for c in checks if "staged replies" in c["name"])["ok"])

    def test_any_tool_use_fails_a_fixture_turn(self):
        tok = "ab12"
        texts = [f"A-STAGE-{i} {tok}" for i in range(3)] + [f"A-STAGE-3 {tok}\n<promise>PROOF-A-{tok}</promise>"]
        state = {"status": "complete", "iteration": 3, "status_reason": "r", "max_iterations": 5}
        for item in ("commandExecution", "fileChange", "mcpToolCall", "dynamicToolCall", "webSearch"):
            checks = proof.evaluate_a(self.turn(texts, 4, items=("userMessage", item, "agentMessage")),
                                      self.stops(["continued"] * 3 + ["complete"]), state, tok)
            failed = [c["name"] for c in checks if not c["ok"]]
            self.assertEqual(failed, ["A: no tool or other non-text item ran"], item)

    def test_exhausting_fixture_with_native_flags(self):
        state = {"status": "exhausted", "iteration": 2, "status_reason": "r", "max_iterations": 2}
        stops = self.stops(["continued", "continued", "exhausted"], hashes=["m", "m", "m"])
        self.assertEqual([s["same_as_previous_stop"] for s in stops], [False, False, True])
        checks = proof.evaluate_b(self.turn(["B-WORKING x"] * 3, 3), stops, state, "x")
        self.assertTrue(all(c["ok"] for c in checks), [c for c in checks if not c["ok"]])
        wrong = self.stops(["continued", "continued", "exhausted"], hashes=["m", "m", "m"], same=[False, True, True])
        checks = proof.evaluate_b(self.turn(["B-WORKING x"] * 3, 3), wrong, state, "x")
        self.assertFalse(next(c for c in checks if "same_as_previous_stop" in c["name"])["ok"])
        completed = proof.evaluate_b(self.turn(["B"] * 3, 3), self.stops(["continued", "continued", "complete"]),
                                     dict(state, status="complete"), "x")
        self.assertFalse(all(c["ok"] for c in completed))

    def test_the_live_runs_failed_hooks_pass_no_check_vacuously(self):
        """The 2026-10-01 run: every fixture hook exited 1, so the adapter saw
        no event. No check may pass on that empty evidence."""
        failed_turn = self.turn(["B-WORKING x"], 0)
        failed_turn["hook_runs"] = [{"event": "stop", "fixture_hook": True, "status": "failed", "seq": 20,
                                     "entries": [{"kind": "error", "text": "hook exited with code 1"}]}]
        state = {"status": "active", "iteration": 0, "status_reason": "r", "max_iterations": 2}
        checks = {c["name"]: c["ok"] for c in proof.evaluate_b(failed_turn, [], state, "x")}
        self.assertFalse(checks["B: same_as_previous_stop follows stop_hook_active and the message hash"])
        self.assertFalse(checks["B: no fixture Stop hook run failed"])
        fixture = os.path.abspath("fx")
        hook = {"seq": 122, "method": "hook/completed", "params": {"threadId": "C", "turnId": "T", "run": {
            "eventName": "sessionStart", "status": "failed",
            "sourcePath": os.path.join(fixture, ".codex", "hooks.json"),
            "entries": [{"kind": "error", "text": "hook exited with code 1"}]}}}
        turn = {"agent_messages": [{"seq": 133, "text": "NO-LAW-IN-CONTEXT", "phase": "final_answer"}]}
        checks = proof.evaluate_c(turn, [hook], [], {"status": "active"}, 114, "C-MARKER-1", "body",
                                  "context", proof.ProjectForm(PY, fixture).owns_run)
        passed = [c["name"] for c in checks if c["ok"]]
        self.assertNotIn("C: the law context arrived before the next final answer", passed)
        self.assertEqual(passed, ["C: no tool or other non-text item ran after compaction",
                                  "C: the contextCompaction item completed"])

    def test_compaction_fixture(self):
        fixture = os.path.abspath("fx")
        body = "Compaction marker: C-MARKER-1\n"
        context = "A COMPACTION JUST HAPPENED.\n\n" + body
        hook = {"seq": 5, "method": "hook/completed", "params": {"threadId": "C", "turnId": "T", "run": {
            "eventName": "sessionStart", "status": "completed",
            "sourcePath": os.path.join(fixture, ".codex", "hooks.json"),
            "entries": [{"kind": "context", "text": context}]}}}
        items = [{"seq": 3, "method": "item/completed", "params": {"threadId": "C", "item": {"type": t}}}
                 for t in ("contextCompaction", "userMessage", "agentMessage")]
        turn = {"agent_messages": [{"seq": 7, "text": "Let me answer.", "phase": "commentary"},
                                   {"seq": 9, "text": "C-MARKER-1\n<promise>P</promise>", "phase": "final_answer"}]}
        events = [{"event": "SessionStart", "source": "compact", "outcome": "law_reloaded"},
                  {"event": "Stop", "outcome": "complete"}]
        args = (events, {"status": "complete"}, 3, "C-MARKER-1", body, context, proof.ProjectForm(PY, fixture).owns_run)
        checks = proof.evaluate_c(turn, [hook] + items, *args)
        self.assertTrue(all(c["ok"] for c in checks), [c for c in checks if not c["ok"]])
        read = {"seq": 4, "method": "item/started", "params": {"threadId": "C", "item": {"type": "commandExecution"}}}
        checks = proof.evaluate_c(turn, [hook, read] + items, *args)
        self.assertFalse(next(c for c in checks if "no tool" in c["name"])["ok"])
        late = dict(turn, agent_messages=[{"seq": 2, "text": "C-MARKER-1", "phase": None}])
        checks = proof.evaluate_c(late, [hook] + items, *args)
        self.assertFalse(next(c for c in checks if "before the next final answer" in c["name"])["ok"])

    def test_the_r2_pattern_still_fails_c(self):
        """The 2026-10-01 r2 run: the SessionStart context was complete, exact
        and before the reply, but the first final answer was the fallback and
        only the Stop refeed produced the marker. The two checks that caught
        this stay failing; only the hook-side checks pass."""
        fixture = os.path.abspath("fx")
        body = "Compaction marker: C-MARKER-1\n"
        context = "A COMPACTION JUST HAPPENED.\n\n" + body
        hook = {"seq": 233, "method": "hook/completed", "params": {"threadId": "C", "turnId": "T", "run": {
            "eventName": "sessionStart", "status": "completed", "source": "project",
            "sourcePath": os.path.join(fixture, ".codex", "hooks.json"),
            "entries": [{"kind": "context", "text": context}]}}}
        items = [{"seq": 225, "method": "item/completed", "params": {"threadId": "C", "item": {"type": t}}}
                 for t in ("contextCompaction", "userMessage", "agentMessage", "hookPrompt")]
        turn = {"agent_messages": [{"seq": 244, "text": "NO-LAW-IN-CONTEXT", "phase": "final_answer"},
                                   {"seq": 280, "text": "C-MARKER-1\n<promise>P</promise>", "phase": "final_answer"}]}
        events = [{"event": "SessionStart", "source": "compact", "outcome": "law_reloaded"},
                  {"event": "Stop", "outcome": "continued"}, {"event": "Stop", "outcome": "complete"}]
        checks = proof.evaluate_c(turn, [hook] + items, events, {"status": "complete"}, 225, "C-MARKER-1",
                                  body, context, proof.ProjectForm(PY, fixture).owns_run)
        failed = [c["name"] for c in checks if not c["ok"]]
        self.assertEqual(failed, ["C: the first final answer after compaction returned the injected marker",
                                  "C: the marker reply completed the loop without a continuation"])

    def test_the_c_question_asks_for_the_marker_directly(self):
        self.assertIn("What is the compaction marker of the Sijav loop law?", proof.C_QUESTION)
        self.assertIn("NO-LAW-IN-CONTEXT", proof.C_QUESTION)
        self.assertNotIn("when asked", proof.C_QUESTION)
        self.assertNotIn("C-MARKER", proof.C_QUESTION, "the marker must come only from the hook context")
        self.assertIn("When asked for the compaction marker", proof.LAW_C)


def make_installed_copy(base):
    """A stand-in for Codex's installed plugin cache: the staged package's
    files copied byte for byte. Offline tests only; the live proof uses the
    root Codex itself installed."""
    root = os.path.join(base, "codex cache", "sijav-codex", "0.1.0")
    for rel in proof.PACKAGE_FILES:
        target = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(os.path.join(proof.STAGE, *rel.split("/")), target)
    return root


def listed_plugin(fixture, root, plugin_id="sijav-codex@local", status="trusted", extra=(), command_root=None):
    form = proof.PluginForm(root)
    expanded_root = command_root or root

    def hook(event):
        return {"eventName": event, "matcher": proof.MATCHERS[event],
                "command": form.handler(event)[proof.VARIANT].replace("${PLUGIN_ROOT}", expanded_root),
                "enabled": True, "trustStatus": status, "timeoutSec": 30,
                "sourcePath": os.path.join(root, "hooks", "hooks.json"), "source": "plugin", "pluginId": plugin_id,
                "additionalContextLimit": 0 if event == "sessionStart" else None,
                "currentHash": "h", "displayOrder": 0, "isManaged": False, "key": event}

    return {"data": [{"cwd": fixture, "hooks": [hook("stop"), hook("sessionStart")] + list(extra),
                      "errors": [], "warnings": []}]}


class PluginFormTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="native proof plugin ")
        self.base = self._tmp.name
        self.fixture = os.path.join(self.base, "plugin fixture")
        os.makedirs(self.fixture)
        self.root = make_installed_copy(self.base)

    def tearDown(self):
        self._tmp.cleanup()

    def prepare(self, **kw):
        code, out = quiet(proof.prepare, self.fixture, PY, False, self.root, kw.get("plugin_id"))
        self.assertEqual(code, 0, out)
        return out

    def test_prepare_writes_only_the_marker(self):
        out = self.prepare()
        self.assertEqual(os.listdir(self.fixture), [proof.MARKER])
        marker = proof.read_marker(self.fixture)
        self.assertEqual((marker["form"], marker["plugin_root"]), ("plugin", os.path.abspath(self.root)))
        self.assertEqual(marker["package_sha256"], proof.package_digest(proof.STAGE))
        self.assertIn("no project hooks were written", out)
        self.assertIn("installed sijav-codex plugin", out)
        self.assertIn("every Codex session", out)
        self.assertEqual(proof.check_fixture_files(self.fixture)["form"], "plugin")

    def test_a_changed_or_foreign_package_is_refused(self):
        with open(os.path.join(self.root, "skills", "loop", "scripts", "sijav_loop.py"), "a", encoding="utf-8") as f:
            f.write("\n# edited\n")
        with self.assertRaises(proof.ProofError) as caught:
            self.prepare()
        self.assertIn("differs from the staged, reviewed package", str(caught.exception))
        self.assertEqual(os.listdir(self.fixture), [])
        other = make_installed_copy(os.path.join(self.base, "other"))
        manifest = os.path.join(other, ".codex-plugin", "plugin.json")
        with open(manifest, encoding="utf-8") as f:
            data = json.load(f)
        with open(manifest, "w", encoding="utf-8") as f:
            json.dump(dict(data, name="someone-else"), f)
        self.assertIn("has name 'someone-else'", proof.installed_package_problem(other))
        self.assertIn("not an existing absolute directory", proof.installed_package_problem("relative/root"))

    def test_a_root_portable_manifest_is_refused_because_its_hooks_are_dropped(self):
        """Codex 0.159.3 prefers a root plugin.json, reads it as a portable
        Agent Plugin and discards its hooks: the live install listed zero
        handlers. Such a package is refused before any Codex process starts."""
        shutil.copy2(os.path.join(proof.STAGE, "archive", "portable-plugin.example.json"),
                     os.path.join(self.root, "plugin.json"))
        problem = proof.installed_package_problem(self.root)
        self.assertIn("portable Agent Plugin", problem)
        self.assertIn("discards", problem)
        with self.assertRaises(proof.ProofError):
            self.prepare()
        self.assertEqual(os.listdir(self.fixture), [])

    def test_project_hooks_and_plugin_form_never_mix(self):
        code, out = quiet(proof.prepare, self.fixture, PY)
        self.assertEqual(code, 0, out)
        with self.assertRaises(proof.ProofError) as caught:
            self.prepare()
        self.assertIn("without .codex/hooks.json", str(caught.exception))
        fresh = os.path.join(self.base, "fresh fixture")
        os.makedirs(fresh)
        self.fixture = fresh
        self.prepare()
        os.makedirs(os.path.join(fresh, ".codex"))
        with open(os.path.join(fresh, ".codex", "hooks.json"), "w", encoding="utf-8") as f:
            f.write("{}")
        with self.assertRaises(proof.ProofError) as caught:
            proof.check_fixture_files(fresh)
        self.assertIn("has a project .codex/hooks.json", str(caught.exception))

    def test_check_and_run_refuse_a_changed_install_before_launching(self):
        self.prepare()
        with open(os.path.join(self.root, "hooks", "hooks.json"), "a", encoding="utf-8") as f:
            f.write("\n")
        with mock.patch.object(proof, "AppServer", side_effect=no_launch):
            for mode in ("--check", "--run"):
                code, out = quiet(proof.main, [mode, self.fixture])
                self.assertEqual(code, 2, out)
                self.assertIn("differs from the staged", out)
            code, out = quiet(proof.main, ["--run", self.fixture, "--installed-plugin-root", self.root])
            self.assertEqual(code, 2)
            self.assertIn("belong to --prepare", out)

    def test_codex_expansion_of_plugin_root_is_matched_exactly(self):
        form = proof.PluginForm(self.root)
        template = form.handler("stop")[proof.VARIANT]
        self.assertIn("${PLUGIN_ROOT}", template)
        self.assertTrue(form.matches(template.replace("${PLUGIN_ROOT}", self.root), "stop", proof.VARIANT))
        self.assertTrue(form.matches(template.replace("${PLUGIN_ROOT}", self.root.replace("\\", "/")),
                                     "stop", proof.VARIANT), "the same directory spelled with / is the same root")
        self.assertFalse(form.matches(template, "stop", proof.VARIANT), "an unexpanded placeholder is not proof")
        self.assertFalse(form.matches(template.replace("${PLUGIN_ROOT}", self.base), "stop", proof.VARIANT))
        self.assertFalse(form.matches(template.replace("${PLUGIN_ROOT}", self.root) + " extra", "stop", proof.VARIANT))
        other = form.handler("stop")[proof.OTHER_VARIANT].replace("${PLUGIN_ROOT}", self.root)
        self.assertFalse(form.matches(other, "stop", proof.VARIANT))

    def verify(self, result, plugin_id=None):
        return proof.verify_hooks(result, self.fixture, proof.PluginForm(self.root, plugin_id), False)

    def test_verify_accepts_only_the_installed_plugins_trusted_handlers(self):
        verdict = self.verify(listed_plugin(self.fixture, self.root))
        self.assertTrue(verdict["ok"], verdict["problems"])
        self.assertEqual(verdict["plugin_ids"], ["sijav-codex@local"])
        self.assertEqual(verdict["form"]["form"], "plugin")
        self.assertTrue(self.verify(listed_plugin(self.fixture, self.root), plugin_id="sijav-codex@local")["ok"])
        self.assertIn("expected one", " ".join(self.verify(listed_plugin(self.fixture, self.root),
                                                           plugin_id="sijav-codex@other")["problems"]))
        self.assertIn("'untrusted'", " ".join(self.verify(listed_plugin(self.fixture, self.root,
                                                                        status="untrusted"))["problems"]))
        project = {"eventName": "stop", "source": "project", "sourcePath": os.path.join(self.fixture, ".codex", "hooks.json"),
                   "command": "x", "enabled": True, "trustStatus": "trusted"}
        another = {"eventName": "sessionStart", "source": "plugin", "pluginId": "other@x",
                   "sourcePath": os.path.join(self.base, "other", "hooks", "hooks.json"),
                   "command": "y", "enabled": True, "trustStatus": "untrusted"}
        for extra in (project, another):
            verdict = self.verify(listed_plugin(self.fixture, self.root, extra=[extra]))
            self.assertFalse(verdict["ok"])
            self.assertIn("confound the proof", " ".join(verdict["problems"]))
        wrong_root = listed_plugin(self.fixture, self.root, command_root=os.path.join(self.base, "elsewhere"))
        self.assertIn("expected the", " ".join(self.verify(wrong_root)["problems"]))

    def test_run_attribution_needs_a_plugin_source_under_the_root(self):
        form = proof.PluginForm(self.root)
        self.assertTrue(form.owns_run({"source": "plugin", "sourcePath": os.path.join(self.root, "hooks", "hooks.json")}))
        self.assertFalse(form.owns_run({"source": "project", "sourcePath": os.path.join(self.root, "hooks", "hooks.json")}))
        self.assertFalse(form.owns_run({"source": "plugin", "sourcePath": os.path.join(self.fixture, ".codex", "hooks.json")}))

    def test_the_installed_commands_run_the_installed_hook_in_every_shell(self):
        form = proof.PluginForm(self.root)
        event = {"session_id": "thread-p", "cwd": self.fixture, "hook_event_name": "Stop",
                 "turn_id": "t", "stop_hook_active": False, "last_assistant_message": "hi"}
        compact = {"session_id": "thread-p", "cwd": self.fixture, "hook_event_name": "SessionStart", "source": "compact"}

        def command(name):
            line = form.display(name)
            return PY + line[line.index(" "):]

        for label, _ in HOOK_SHELLS:
            proc = native_spawn(command("stop"), event, self.fixture, shell=label)
            self.assertEqual(proc.returncode, 0, (label, proc.stderr))
            self.assertIn(b"not armed", proc.stderr, label)
        os.makedirs(os.path.join(self.base, "laws"))
        law = os.path.join(self.base, "laws", "plugin law.md")  # a sibling, as the harness does
        with open(law, "w", encoding="utf-8") as f:
            f.write("Installed plugin law\n")
        cli = subprocess.run([PY, form.cli_script, "start", "--project-root", self.fixture, "--law", law,
                              "--session", "thread-p", "--max-iterations", "100", "--promise", "DONE"],
                             capture_output=True, env=ENV, timeout=60)
        self.assertEqual(cli.returncode, 0, cli.stderr)
        for label, _ in HOOK_SHELLS:
            out = json.loads(native_spawn(command("stop"), event, self.fixture, shell=label).stdout)
            self.assertEqual(out["decision"], "block", label)
            self.assertTrue(out["reason"].endswith("Installed plugin law\n"), label)
            out = json.loads(native_spawn(command("sessionStart"), compact, self.fixture, shell=label).stdout)
            self.assertTrue(out["hookSpecificOutput"]["additionalContext"].endswith("Installed plugin law\n"), label)


class NativeManifestTests(unittest.TestCase):
    """The package's manifest contract for Codex 0.159.3: a native
    .codex-plugin/plugin.json with exactly the fields this package defines,
    and no root plugin.json."""

    def copy(self):
        tmp = tempfile.TemporaryDirectory(prefix="native manifest ")
        self.addCleanup(tmp.cleanup)
        return make_installed_copy(tmp.name)

    def rewrite(self, root, **changes):
        path = os.path.join(root, ".codex-plugin", "plugin.json")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        data.update(changes)
        for key, value in list(data.items()):
            if value is None:
                del data[key]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def test_the_staged_package_is_native_with_no_root_manifest(self):
        self.assertIsNone(proof.native_manifest_problem(proof.STAGE))
        self.assertFalse(os.path.exists(os.path.join(proof.STAGE, "plugin.json")))

    def test_missing_hooks_or_skills_and_unknown_fields_are_refused(self):
        for changes, cause in (({"hooks": None}, "has hooks None"),
                               ({"skills": "./elsewhere"}, "has skills './elsewhere'"),
                               ({"description": ""}, "no description"),
                               ({"interface": {"displayName": "x"}}, "does not define: ['interface']"),
                               ({"extensions": {"com.openai": {}}}, "does not define: ['extensions']")):
            root = self.copy()
            self.rewrite(root, **changes)
            self.assertIn(cause, proof.native_manifest_problem(root), changes)

    def test_paths_must_exist_and_the_manifest_must_be_present(self):
        root = self.copy()
        os.remove(os.path.join(root, "hooks", "hooks.json"))
        self.assertIn("hooks path ./hooks/hooks.json does not exist", proof.native_manifest_problem(root))
        root = self.copy()
        os.remove(os.path.join(root, ".codex-plugin", "plugin.json"))
        self.assertIn("cannot be read", proof.native_manifest_problem(root))


class FakeServer:
    """Answers thread/start with a canned result and records the request."""

    def __init__(self, result):
        self.result = result
        self.requests = []
        self.notes = []

    def request(self, method, params=None, timeout=None):
        self.requests.append((method, params))
        return self.result


class EffortTests(unittest.TestCase):
    def test_thread_start_sets_the_thread_default_effort(self):
        params = proof.thread_start_params("C:/fx", "gpt-6.1-sol", "medium")
        self.assertEqual(params["config"], {"model_reasoning_effort": "medium"})
        self.assertEqual((params["model"], params["sandbox"], params["approvalPolicy"]),
                         ("gpt-6.1-sol", "read-only", "never"))

    def new_thread(self, baseline_model, baseline_effort):
        with tempfile.TemporaryDirectory() as run_dir:
            server = FakeServer({"thread": {"id": "T1", "sessionId": "T1"}, "model": baseline_model,
                                 "reasoningEffort": baseline_effort})
            run = proof.Run(run_dir, run_dir, PY, server, "gpt-6.1-sol", "medium", ENV)
            try:
                run.new_thread("A")
            finally:
                run.finish()
        self.assertEqual(server.requests[0][1]["config"], {"model_reasoning_effort": "medium"})
        check = next(c for c in run.checks if "thread baseline" in c["name"])
        return run.report["threads"]["A"], check

    def test_the_baseline_is_checked_and_never_called_served(self):
        info, check = self.new_thread("gpt-6.1-sol", "medium")
        self.assertTrue(check["ok"])
        self.assertEqual(info["thread_baseline_reasoning_effort"], "medium")
        self.assertEqual(info["turn_override_sent"], {"model": "gpt-6.1-sol", "effort": "medium"})
        self.assertFalse(any(key.startswith("served") for key in info))

    def test_a_refused_or_different_effort_is_a_failed_check(self):
        """The 2026-10-01 043110Z run reported baseline effort low for a
        requested medium; such a discrepancy now fails a native check."""
        _, check = self.new_thread("gpt-6.1-sol", "low")
        self.assertFalse(check["ok"])
        self.assertEqual(check["detail"]["thread_baseline"], {"model": "gpt-6.1-sol", "effort": "low"})
        _, check = self.new_thread("gpt-6-luna", "medium")
        self.assertFalse(check["ok"])


class VersionAndSettleTests(unittest.TestCase):
    def test_requested_version_must_appear_in_both_reports(self):
        cli = {"output": "codex-cli 0.159.3"}
        init = {"userAgent": "Codex Desktop/0.159.3 (Windows)"}
        self.assertIsNone(proof.version_problem(None, cli, init))
        self.assertIsNone(proof.version_problem("0.159.3", cli, init))
        problem = proof.version_problem("0.159.3", {"output": "codex-cli 0.159.2"}, init)
        self.assertIn("codex --version", problem)
        problem = proof.version_problem("0.159.3", cli, {"userAgent": "Codex Desktop/0.159.2"})
        self.assertIn("app-server userAgent", problem)
        self.assertIn("app-server userAgent", proof.version_problem("0.159.3", cli, None))

    def test_a_live_fixture_state_is_stopped_after_the_run(self):
        with tempfile.TemporaryDirectory(prefix="settle ") as fixture:
            law = os.path.join(fixture, "law.md")
            with open(law, "w", encoding="utf-8") as f:
                f.write("Law\n")
            marker = {"python": PY}
            form = proof.ProjectForm(PY, fixture)
            self.assertEqual(proof.settle_fixture_state(fixture, marker, form, ENV)["action"], "none")
            cli = subprocess.run([PY, proof.CLI_SCRIPT, "start", "--project-root", fixture, "--law", law,
                                  "--session", "t1", "--max-iterations", "3", "--promise", "DONE"],
                                 capture_output=True, env=ENV, timeout=60)
            self.assertEqual(cli.returncode, 0, cli.stderr)
            result = proof.settle_fixture_state(fixture, marker, form, ENV)
            self.assertEqual((result["state"], result["action"], result["exit_code"]), ("active", "stop", 0))
            with open(proof.sijav_loop.state_path(fixture), encoding="utf-8") as f:
                state = json.load(f)
            self.assertEqual(state["status"], "stopped")
            self.assertIn("native proof run ended", state["status_reason"])
            self.assertEqual(proof.settle_fixture_state(fixture, marker, form, ENV),
                             {"state": "stopped", "action": "none"})


if __name__ == "__main__":
    unittest.main()
