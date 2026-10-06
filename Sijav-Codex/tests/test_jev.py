"""jev.py run as the orchestrator would, with a fake TypeSafe SDK (tests/fixtures/fake_sdk) and fake keys
in temporary folders. No network, no real key."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

STAGE = Path(__file__).resolve().parents[1]
HELPER = STAGE / "skills" / "roast" / "jev.py"
FAKE_SDK = STAGE / "tests" / "fixtures" / "fake_sdk"
NO_SDK = STAGE / "tests" / "fixtures" / "no_sdk"
sys.path.insert(0, str(HELPER.parent))
import jev  # noqa: E402

KEY = "ts-fake-key-0123456789"


def brief(**change):
    b = {"mode": "task", "item": "T-12", "title": "Resume keeps saved answers",
         "why": "People lose answers when the app restarts.",
         "exit_condition": "After a restart the form shows every saved answer and asks only for the rest.",
         "did": "Saved answers after each step; resume reads them.", "files": "app/form.py, app/store.py",
         "ask": ["Would a crash between saving and showing lose an answer?"],
         "facts": [{"fact": "The store writes each answer as soon as it is given.", "source": "app/store.py:40",
                    "by": "claude roast-technical"},
                   {"fact": "Resume shows saved answers before asking.", "source": "app/form.py:88"}]}
    b.update(change)
    return b


def framing(**change):
    f = {"state": {"what_was_done": "Each answer is saved when given; on restart the saved ones are shown.",
                   "doubt": "Whether a crash between save and display loses anything."},
         "questions": {
             "crash_loses": {"type": "noul",
                             "instructions": "Would a crash between saving and showing lose an answer? Judge from the steps."},
             "risk": {"type": "choice", "instructions": "Which step is weakest?",
                      "criteria": {"saving": "the save step", "showing": "the display step"}},
             "fit": {"type": "score", "instructions": "How well does the work meet the exit condition?",
                     "criteria": ["not at all", "partly", "fully"]}}}
    f.update(change)
    return f


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sijav jev "))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.project = self.tmp / "a project"
        (self.project / ".claude").mkdir(parents=True)
        self.sub = self.project / "app" / "sub dir"
        self.sub.mkdir(parents=True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.key_file = self.tmp / "keys" / "typesafe.key"
        self.key_file.parent.mkdir()
        self.key_file.write_text(KEY + "\n", encoding="utf-8")
        self.log = self.tmp / "ts.jsonl"

    def env(self, sdk=FAKE_SDK, **extra):
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("SIJAV_", "FAKE_TS", "TYPESAFE_")) and k != "PYTHONPATH"}
        env.update(PYTHONPATH=str(sdk), SIJAV_JEV_KEY_FILE=str(self.key_file), FAKE_TS_LOG=str(self.log),
                   FAKE_TS_COUNT=str(self.tmp / "count"), USERPROFILE=str(self.home), HOME=str(self.home),
                   TYPESAFE_API_KEY="env-key-must-not-win", PYTHONDONTWRITEBYTECODE="1")
        env.update(extra)
        return {k: v for k, v in env.items() if v is not None}

    def jev(self, *args, **env):
        r = subprocess.run([sys.executable, str(HELPER), *args], cwd=str(self.sub), capture_output=True,
                           env=self.env(**env), timeout=60)
        return r.returncode, r.stdout.decode("utf-8"), r.stderr.decode("utf-8")

    def file(self, name, data):
        p = self.tmp / name
        p.write_text(json.dumps(data) if not isinstance(data, str) else data, encoding="utf-8")
        return str(p)

    def open(self, **change):
        code, out, err = self.jev("open", "--brief", self.file("brief.json", brief(**change)))
        self.assertEqual(code, 0, err)
        return Path(out.strip())

    def ask(self, run, f, *extra, **env):
        return self.jev("ask", "--run", str(run), "--framing", self.file(f"framing-{len(list(self.tmp.glob('framing-*')))}.json", f),
                        *extra, **env)

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(x) for x in self.log.read_text(encoding="utf-8").splitlines()]

    def j(self, path):
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def assert_no_key(self, run):
        for p in run.rglob("*"):
            if p.is_file():
                self.assertNotIn(KEY, p.read_text(encoding="utf-8", errors="replace"), p)


class Brief(Base):
    def test_open_from_a_subdirectory_records_the_brief_unchanged(self):
        run = self.open()
        self.assertEqual(run.parent, (self.project / ".codex" / "roasts").resolve())
        self.assertRegex(run.name, r"^\d{8}T\d{6}Z-task-T-12-[0-9a-f]{6}$")
        self.assertEqual(self.j(run / "brief.json"), brief())
        self.assertEqual(self.j(run / "state.json")["status"], "open")

    def test_incomplete_or_code_bearing_briefs_create_no_run(self):
        for change, code, words in (({"facts": []}, 2, "facts is missing"),
                                    ({"facts": [{"fact": "x"}]}, 2, "needs a plain-sentence fact and its source"),
                                    ({"title": "", "why": "", "exit_condition": ""}, 2, "original ask is missing"),
                                    ({"mode": "review"}, 2, "mode"),
                                    ({"title": "Fix ```x = 1```"}, 4, "fenced code block"),
                                    ({"why": "def save(a):\n  pass"}, 4, "a definition"),
                                    ({"facts": [{"fact": "const a = 1", "source": "x.js:1"}]}, 4, "a declaration"),
                                    ({"ask": ["import os"]}, 4, "an import")):
            c, _out, err = self.jev("open", "--brief", self.file("b.json", brief(**change)))
            self.assertEqual(c, code, (change, err))
            self.assertIn(words, err)
        self.assertFalse((self.project / ".codex").exists())

    def test_a_stray_claude_folder_does_not_move_the_root(self):
        (self.project / ".git").mkdir()
        (self.sub / ".claude").mkdir()
        run = self.open()
        self.assertEqual(run.parent, (self.project / ".codex" / "roasts").resolve())
        self.assertFalse((self.sub / ".codex").exists())

    def test_no_project_outside_one(self):
        loose = self.home / "loose"
        loose.mkdir()
        r = subprocess.run([sys.executable, str(HELPER), "open", "--brief", self.file("b.json", brief())],
                           cwd=str(loose), capture_output=True, env=self.env(), timeout=60)
        self.assertEqual(r.returncode, 2)
        self.assertIn(b"no project found", r.stderr)


class Availability(Base):
    def test_available_reports_each_cause_without_the_key(self):
        cases = [({"SIJAV_JEV": "off"}, "switched off"),
                 ({"SIJAV_JEV_KEY_FILE": str(self.tmp / "missing.key")}, "does not exist"),
                 ({"SIJAV_JEV_KEY_FILE": None, "TYPESAFE_API_KEY": None}, "no Jev key"),
                 ({"sdk": NO_SDK}, "SDK (typesafe-sdk) is not installed")]
        for env, words in cases:
            c, out, _ = self.jev("available", **env)
            self.assertEqual(c, 3, env)
            self.assertIn(words, out)
        empty = self.tmp / "empty.key"
        empty.write_text("  \n")
        c, out, _ = self.jev("available", SIJAV_JEV_KEY_FILE=str(empty))
        self.assertIn("is empty", out)
        c, out, err = self.jev("available")
        self.assertEqual(c, 0)
        self.assertNotIn(KEY, out + err)
        self.assertEqual(self.calls(), [], "available makes no call")


class Ask(Base):
    def test_judged_run_sends_exactly_what_the_contract_says(self):
        run = self.open()
        code, out, err = self.ask(run, framing(), "--framed-by", "native purpose roast-task")
        self.assertEqual(code, 0, err)
        [call] = self.calls()
        self.assertEqual(call["api_key"], KEY, "the key file wins over TYPESAFE_API_KEY")
        self.assertEqual(call["model"], "jev-latest")
        self.assertEqual(call["state"]["checked_facts"], [f["fact"] for f in brief()["facts"]])
        self.assertEqual(call["state"]["what_was_asked_for"],
                         {k: brief()[k] for k in ("title", "why", "exit_condition")})
        self.assertNotIn("app/store.py:40", json.dumps(call), "sources stay out of what Jev gets")
        self.assertNotIn("did", call["state"])
        self.assertEqual(set(call["questions"]), {"crash_loses", "risk", "fit"})
        self.assertNotIn("anything_critical", call["questions"])
        self.assertEqual(call["questions"]["risk"]["criteria"], framing()["questions"]["risk"]["criteria"])

        request = self.j(run / "request-1.json")
        self.assertEqual(request["state"], call["state"])
        self.assertEqual(request["facts_with_sources"], brief()["facts"])
        response = self.j(run / "response-1.json")["reply"]
        self.assertEqual(response["model"], "jev-1.13.0")
        self.assertEqual(response["request_id"], "req_fake_123")
        outcome = self.j(run / "jev.json")
        self.assertEqual((outcome["status"], outcome["served_model"], outcome["requested_model"]),
                         ("judged", "jev-1.13.0", "jev-latest"))
        self.assertEqual(outcome["usage"], {"input_tokens": 900, "output_tokens": None})
        self.assertEqual(self.j(run / "check-1.json")["framed_by"], "native purpose roast-task")
        lines = out.splitlines()
        at = lines.index("- crash_loses (noul): Would a crash between saving and showing lose an answer?"
                         " Judge from the steps.")
        self.assertEqual(lines[at + 1], "  probability true 0.10", "a noul is a probability, with no confidence")
        self.assertIn("  picked saving (confidence 0.55); probabilities: saving 0.70, showing 0.30", lines)
        self.assertIn("  expected level 1.00 (confidence 0.20); levels: 0 = not at all 0.33; 1 = partly 0.33;"
                      " 2 = fully 0.33", lines)
        self.assertEqual((run / "framing-1.json").read_text(encoding="utf-8"), json.dumps(framing()))
        self.assert_no_key(run)

        c, _o, err = self.ask(run, framing())
        self.assertEqual(c, 1)
        self.assertIn("asked once per run", err)
        self.assertEqual(len(self.calls()), 1)

    def test_plan_mode_adds_the_owners_critical_question_word_for_word(self):
        run = self.open(mode="plan")
        c, _o, err = self.ask(run, framing())
        self.assertEqual(c, 4)
        self.assertIn("the_plan", err)
        state = dict(framing()["state"], the_plan="Save each answer as given; on resume show the saved ones.")
        c, out, err = self.ask(run, framing(state=state))
        self.assertEqual(c, 0, err)
        [call] = self.calls()
        self.assertEqual(call["questions"].get(jev.CRITICAL), jev.CRITICAL_QUESTION, "the question is sent, word for word")
        self.assertIn("Given what_was_asked_for and the_plan's logic, is anything critical", out)

    def test_framing_may_not_supply_what_the_helper_sets(self):
        run = self.open(mode="plan")
        state = dict(framing()["state"], the_plan="p", what_was_asked_for={"title": "something else"},
                     checked_facts=["invented fact"])
        questions = dict(framing()["questions"], anything_critical={"type": "noul", "instructions": "Anything bad?"})
        c, _o, err = self.ask(run, framing(state=state, questions=questions))
        self.assertEqual(c, 4)
        for words in ("may not set 'what_was_asked_for'", "may not set 'checked_facts'",
                      "may not write 'anything_critical'"):
            self.assertIn(words, err)
        self.assertEqual(self.calls(), [])

    def test_every_field_is_checked_for_code_and_problems_go_back_once(self):
        run = self.open()
        bad = framing(
            state={"what_was_done": "It runs:\n```\nsave()\n```", "x": "if ready {"},
            questions={"q1": {"type": "noul", "instructions": "def resume(a):\n  return a"},
                       "q2": {"type": "choice", "instructions": "Which?",
                              "criteria": {"import os": "x", "b": "from store import save"}},
                       "q3": {"type": "score", "instructions": "How?", "criteria": ["only one"]},
                       "q4": {"type": "noul", "instructions": "ok", "criteria": {"maybe": "x"}},
                       "q5": {"type": "rank", "instructions": "?"}},
            observed=[{"fact": "The store is fast.", "source": "app/store.py:1"}])
        c, _o, err = self.ask(run, bad)
        self.assertEqual(c, 4, err)
        for words in ("a fenced code block", "a line that opens a block", "a definition", "(the key): an import",
                      "q2.criteria.b: an import", "score 'q3' needs at least two", "noul 'q4': criteria",
                      "'q5' is not a noul, choice or score", "observed[0] is not one of the brief's checked facts",
                      "the asker's question 1"):
            self.assertIn(words, err)
        self.assertEqual(self.calls(), [])
        check = self.j(run / "check-1.json")
        self.assertEqual(check["attempt"], 1)
        self.assertTrue(check["problems"])
        self.assertEqual(self.j(run / "state.json")["status"], "needs_correction")

        c, _o, err = self.ask(run, bad)
        self.assertEqual(c, 1)
        self.assertIn("after one correction", err)
        self.assertEqual(self.j(run / "jev.json")["status"], "framing_rejected")
        self.assertEqual(self.calls(), [])
        c, _o, err = self.ask(run, framing())
        self.assertEqual(c, 1, "a rejected run takes no more framings")

    def test_a_corrected_framing_is_judged_and_observed_selects_brief_facts(self):
        run = self.open()
        c, _o, _e = self.ask(run, framing(state={"x": "```"}))
        self.assertEqual(c, 4)
        chosen = [brief()["facts"][1]]
        c, _o, err = self.ask(run, framing(observed=chosen))
        self.assertEqual(c, 0, err)
        self.assertEqual(self.calls()[0]["state"]["checked_facts"], ["Resume shows saved answers before asking."])

    def test_check_validates_without_recording_or_sending(self):
        run = self.open()
        c, _o, err = self.jev("check", "--run", str(run), "--framing", self.file("f.json", framing(state={"x": "```"})))
        self.assertEqual(c, 4)
        self.assertIn("nothing recorded or sent", err)
        c, out, _ = self.jev("check", "--run", str(run), "--framing", self.file("f.json", framing()))
        self.assertEqual(c, 0)
        self.assertEqual(sorted(p.name for p in run.iterdir()), ["brief.json", "lock", "state.json"])
        self.assertEqual(self.calls(), [])


class Unjudged(Base):
    def assert_unjudged(self, run, code, err, words):
        self.assertEqual(code, 3, err)
        self.assertIn(words, err)
        outcome = self.j(run / "jev.json")
        self.assertEqual(outcome["status"], "unjudged")
        self.assertIn(words, outcome["cause"])
        self.assertNotIn("answers", outcome, "no invented numbers")

    def test_switched_off_missing_key_and_missing_sdk_make_no_call(self):
        for env, words in (({"SIJAV_JEV": "off"}, "switched off (SIJAV_JEV=off)"),
                           ({"SIJAV_JEV_KEY_FILE": None, "TYPESAFE_API_KEY": None}, "no Jev key"),
                           ({"sdk": NO_SDK}, "TypeSafe SDK (typesafe-sdk) is not installed")):
            run = self.open()
            c, _o, err = self.ask(run, framing(), **env)
            self.assert_unjudged(run, c, err, words)
            self.assertTrue((run / "framing-1.json").exists(), "the framing received is kept")
        self.assertEqual(self.calls(), [])

    def test_answers_are_checked_one_by_one(self):
        for mode, words in (("missing", "Jev did not answer ['crash_loses']"),
                            ("extra", "questions that were not asked: ['not_asked']"),
                            ("wrong_type", "is not a noul answer"),
                            ("out_of_range", "no noul probability between 0 and 1")):
            run = self.open()
            c, out, err = self.ask(run, framing(), FAKE_TS_MODE=mode)
            self.assert_unjudged(run, c, err, words)
            self.assertTrue((run / "response-1.json").exists(), "the reply is kept as received")
            self.assertEqual(out, "")

    def test_service_failures_are_unjudged_with_their_cause_and_no_key(self):
        run = self.open()
        c, out, err = self.ask(run, framing(), FAKE_TS_MODE="forbidden")
        self.assert_unjudged(run, c, err, "Jev answered HTTP 403")
        self.assertIn("[the Jev key]", err)
        self.assertNotIn(KEY, out + err)
        self.assert_no_key(run)
        self.assertEqual(self.j(run / "error-1.json")["status"], 403)
        run = self.open()
        c, _o, err = self.ask(run, framing(), FAKE_TS_MODE="connection")
        self.assert_unjudged(run, c, err, "TypeSafeAPIConnectionError: Connection error: connection reset by peer")
        self.assertIn("its outcome is unknown", self.j(run / "jev.json")["cause"])
        self.assertEqual(self.j(run / "error-1.json")["outcome"], "unknown")
        self.assertEqual(len(self.calls()), 2, "no retry")

    def test_too_long_needs_one_shortened_framing(self):
        run = self.open()
        c, _o, err = self.ask(run, framing(), FAKE_TS_MODE="too_long_once")
        self.assertEqual(c, 5, err)
        self.assertIn("max_tokens_exceeded", err)
        self.assertIn("--shortened", err)
        self.assertEqual(self.j(run / "error-1.json")["body"]["detail"]["error_type"], "max_tokens_exceeded")
        c, _o, err = self.ask(run, framing(), FAKE_TS_MODE="too_long_once")
        self.assertEqual(c, 2)
        self.assertIn("must be the shortened one", err)
        short = framing(state={"what_was_done": "Answers saved when given; shown on restart."})
        c, out, err = self.ask(run, short, "--shortened", FAKE_TS_MODE="too_long_once")
        self.assertEqual(c, 0, err)
        request = self.j(run / "request-2.json")
        self.assertEqual(request["phase"], "shortened")
        self.assertIn("max_tokens_exceeded", request["shortened_because"])
        self.assertIn("max_tokens_exceeded", self.j(run / "jev.json")["shortened_because"])
        self.assertEqual(len(self.calls()), 2)

    def test_too_long_twice_is_unjudged(self):
        run = self.open()
        self.assertEqual(self.ask(run, framing(), FAKE_TS_MODE="too_long")[0], 5)
        c, _o, err = self.ask(run, framing(), "--shortened", FAKE_TS_MODE="too_long")
        self.assert_unjudged(run, c, err, "refused the shortened request as too long too")
        self.assertEqual(len(self.calls()), 2)


class CallSafety(Base):
    def test_sdk_retries_are_off_and_the_timeout_is_finite(self):
        run = self.open()
        self.assertEqual(self.ask(run, framing())[0], 0)
        [call] = self.calls()
        self.assertEqual((call["client_retry"], call["call_retry"]), ({"max_retries": 0}, {"max_retries": 0}))
        self.assertEqual((call["client_timeout"], call["call_timeout"]), (60.0, 60.0))
        self.assertEqual(self.j(run / "request-1.json")["sdk"],
                         {"retry": "RetryPolicy(max_retries=0)", "http_timeout_seconds": 60.0})

    def test_an_sdk_outside_the_checked_contract_is_not_used(self):
        run = self.open()
        c, _o, err = self.ask(run, framing(), FAKE_TS_VERSION="0.6.4")
        self.assertEqual(c, 3)
        self.assertIn("typesafe-sdk 0.6.4 is not the 0.7.x contract", err)
        self.assertEqual(self.calls(), [])

    def test_every_failure_says_what_is_known_about_the_request(self):
        cases = [("unexpected", "unknown", "RuntimeError: an HTTP library error the SDK did not wrap"),
                 ("timeout", "unknown", "TypeSafeAPITimeoutError: Request timed out (timeout=60.0)."),
                 ("unserializable", "answered", "is not JSON data that can be recorded"),
                 ("forbidden", "answered", "Jev answered HTTP 403 (TypeSafePermissionDeniedError)")]
        for mode, outcome, words in cases:
            with self.subTest(mode):
                run = self.open()
                c, _o, err = self.ask(run, framing(), FAKE_TS_MODE=mode)
                self.assertEqual(c, 3, err)
                self.assertIn(words, err)
                self.assertEqual(self.j(run / "error-1.json")["outcome"], outcome)
                if mode == "unserializable":
                    self.assertEqual(self.j(run / "error-1.json")["body"],
                                     {"raw_body": '{"model": "jev-1.13.0", "answers": "raw wire body"}',
                                      "request_id": "req_fake_unserializable"}, "the wire body is kept")
                self.assertEqual(self.j(run / "jev.json")["request_outcome"], outcome)
                self.assertEqual(self.j(run / "state.json")["status"], "unjudged")
                self.assertNotIn("answers", self.j(run / "jev.json"))
        self.assertEqual(len(self.calls()), len(cases), "one call each, never retried")

    def test_an_interrupt_during_the_call_is_recorded_as_unknown(self):
        run = self.open()
        c, _o, err = self.ask(run, framing(), FAKE_TS_MODE="interrupt")
        self.assertNotEqual(c, 0)
        self.assertTrue((run / "error-1.json").is_file(), "the interrupt is recorded")
        self.assertEqual(self.j(run / "error-1.json")["outcome"], "unknown")
        self.assertTrue((run / "jev.json").is_file(), "the run is closed with an outcome")
        self.assertIn("interrupted (KeyboardInterrupt)", self.j(run / "jev.json")["cause"])
        self.assertEqual(self.ask(run, framing())[0], 1, "the run is closed; Jev is not asked again")
        self.assertEqual(len(self.calls()), 1)

    def test_a_helper_that_dies_mid_call_blocks_every_reask_until_recovered(self):
        run = self.open()
        c, _o, _e = self.ask(run, framing(), FAKE_TS_MODE="crash")
        self.assertEqual(c, 9, "the process ended during the call")
        self.assertEqual(self.j(run / "state.json")["status"], "calling")
        self.assertTrue((run / "request-1.json").exists())
        for args in (["ask", "--run", str(run), "--framing", self.file("again.json", framing())],
                     ["check", "--run", str(run), "--framing", self.file("again.json", framing())],
                     ["finalize", "--run", str(run), "--interpretation", self.file("i.md", "x")]):
            c, _o, err = self.jev(*args)
            self.assertEqual(c, 1, args)
            self.assertIn("its outcome was never recorded", err)
        self.assertEqual(self.jev("recover", "--run", str(run))[0], 2)
        c, _o, err = self.jev("recover", "--run", str(run), "--confirm-stopped")
        self.assertEqual(c, 3, err)
        self.assertIn("the outcome is unknown and Jev is not asked again", err)
        self.assertEqual(self.j(run / "error-1.json")["outcome"], "unknown")
        self.assertEqual(len(self.calls()), 1, "Jev was asked exactly once")
        self.assertEqual(self.jev("finalize", "--run", str(run), "--interpretation", self.file("i.md", "x"))[0], 0)
        self.assertIn("the outcome is unknown", (run / "record.md").read_text(encoding="utf-8"))

    def test_recover_adopts_only_the_same_requests_recorded_outcome(self):
        run = self.open()
        self.assertEqual(self.ask(run, framing(), FAKE_TS_MODE="crash")[0], 9)
        jev_file = run / "jev.json"
        jev_file.write_text(json.dumps({"status": "unjudged", "cause": "another request", "request": 2}), encoding="utf-8")
        before = jev_file.read_bytes()
        c, _o, err = self.jev("recover", "--run", str(run), "--confirm-stopped")
        self.assertEqual(c, 1, err)
        self.assertIn("not an outcome of request 1", err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(jev_file.read_bytes(), before, "never overwritten")
        self.assertEqual(self.j(run / "state.json")["status"], "calling")
        jev_file.write_text(json.dumps({"status": "unjudged", "cause": "recorded before the state was saved",
                                        "request": 1, "request_outcome": "unknown"}), encoding="utf-8")
        before = jev_file.read_bytes()
        c, _o, err = self.jev("recover", "--run", str(run), "--confirm-stopped")
        self.assertEqual(c, 3, err)
        self.assertIn("recorded before the state was saved", err)
        self.assertEqual(jev_file.read_bytes(), before, "the recorded outcome is adopted as it is")
        self.assertEqual(self.j(run / "state.json")["status"], "unjudged")
        self.assertEqual(len(self.calls()), 1)

    def test_a_failed_or_interrupted_publication_never_leaves_a_partial_outcome(self):
        from unittest import mock
        run = self.open()
        self.assertEqual(self.ask(run, framing(), FAKE_TS_MODE="crash")[0], 9)
        outcome = {"status": "unjudged", "cause": "x" * 50000, "request": 1, "request_outcome": "unknown"}
        for patch, raised in ((mock.patch.object(jev.os, "fsync", side_effect=OSError(28, "No space left on device")),
                               jev.JevError),
                              (mock.patch.object(jev.os, "link", side_effect=OSError(28, "No space left on device")),
                               jev.JevError),
                              (mock.patch.object(jev.os, "link", side_effect=KeyboardInterrupt), KeyboardInterrupt)):
            with self.subTest(str(raised)), patch:
                with self.assertRaises(raised) as caught:
                    jev.record_outcome(run, outcome, 1)
                if raised is jev.JevError:
                    self.assertIn("could not be published", str(caught.exception))
                    self.assertIn("stays calling", str(caught.exception))
                self.assertFalse((run / "jev.json").exists(), "no partial canonical record")
                self.assertEqual(list(run.glob(".jev-*.tmp")), [], "the temporary file is removed")
            (run / "jev.json").unlink(missing_ok=True)  # each case starts, as the first did, with no record
        self.assertEqual(self.j(run / "state.json")["status"], "calling")
        self.assertTrue((run / "request-1.json").exists())
        (run / "jev.json").write_text(json.dumps({"status": "unjudged", "cause": "other", "request": 2}),
                                      encoding="utf-8")
        before = (run / "jev.json").read_bytes()
        with self.assertRaises(jev.JevError):
            jev.record_outcome(run, outcome, 1)
        self.assertEqual((run / "jev.json").read_bytes(), before, "an existing record is never replaced")
        (run / "jev.json").unlink()
        c, _o, err = self.jev("recover", "--run", str(run), "--confirm-stopped")
        self.assertEqual(c, 3, err)
        published = self.j(run / "jev.json")
        self.assertEqual((published["status"], published["request"], published["request_outcome"]),
                         ("unjudged", 1, "unknown"))
        self.assertEqual(len(self.calls()), 1, "Jev was asked once in all of this")

    @unittest.skipUnless(os.name == "nt", "a rename onto an open file fails only on Windows")
    def test_an_outcome_whose_state_could_not_be_saved_is_adopted_by_recover(self):
        for mode, recover_code, words in (("ok", 0, "probability true 0.10"), ("forbidden", 3, "Jev answered HTTP 403")):
            with self.subTest(mode):
                run = self.open()
                gate = self.tmp / f"gate-{mode}"
                env = self.env(FAKE_TS_MODE=mode, FAKE_TS_GATE=str(gate))
                proc = subprocess.Popen([sys.executable, str(HELPER), "ask", "--run", str(run), "--framing",
                                         self.file(f"f-{mode}.json", framing())], cwd=str(self.sub),
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
                deadline = time.monotonic() + 60
                while self.j(run / "state.json")["status"] != "calling" and time.monotonic() < deadline:
                    time.sleep(0.05)
                holder = open(run / "state.json", "rb")  # Windows: blocks the rename onto state.json
                try:
                    gate.write_text("go")
                    out, err = proc.communicate(timeout=120)
                finally:
                    holder.close()
                self.assertEqual(proc.returncode, 1, err.decode())
                self.assertIn(b"could not write", err)
                self.assertTrue((run / "jev.json").exists(), "the outcome was recorded before the state")
                self.assertEqual(self.j(run / "state.json")["status"], "calling")
                calls = len(self.calls())
                c, out, err = self.jev("recover", "--run", str(run), "--confirm-stopped")
                self.assertEqual(c, recover_code, err)
                self.assertIn(words, out + err)
                self.assertEqual(len(self.calls()), calls, "Jev is not asked again")
                self.assertNotEqual(self.j(run / "state.json")["status"], "calling")

    def test_recover_judges_a_reply_saved_before_the_helper_died(self):
        run = self.open()
        self.assertEqual(self.ask(run, framing(), FAKE_TS_MODE="crash")[0], 9)
        request = self.j(run / "request-1.json")
        answers = {"crash_loses": {"type": "noul", "noul": 0.2},
                   "risk": {"type": "choice", "choice": "saving", "confidence": 0.5,
                            "probabilities": {"saving": 0.75, "showing": 0.25}},
                   "fit": {"type": "score", "score": 1.5, "confidence": 0.3,
                           "legend": {"0": "not at all", "1": "partly", "2": "fully"},
                           "probabilities": {"0": 0.1, "1": 0.3, "2": 0.6}}}
        self.assertEqual(set(answers), set(request["questions"]))
        (run / "response-1.json").write_text(json.dumps({"received_at": "t", "reply": {
            "model": "jev-1.13.0", "usage": {"input_tokens": 1, "output_tokens": None}, "answers": answers}}),
            encoding="utf-8")
        c, out, err = self.jev("recover", "--run", str(run), "--confirm-stopped")
        self.assertEqual(c, 0, err)
        self.assertIn("probability true 0.20", out)
        self.assertEqual(self.j(run / "jev.json")["status"], "judged")
        self.assertEqual(len(self.calls()), 1)

    def test_an_edited_brief_or_a_malformed_state_is_refused(self):
        run = self.open()
        brief_file = run / "brief.json"
        original = brief_file.read_bytes()
        brief_file.write_bytes(original.replace(b"Resume keeps saved answers", b"Resume keeps SOME answers"))
        c, _o, err = self.ask(run, framing())
        self.assertEqual(c, 1)
        self.assertIn("changed after the run was opened", err)
        brief_file.write_bytes(original)
        state = self.j(run / "state.json")
        (run / "state.json").write_text(json.dumps(dict(state, status="sideways")), encoding="utf-8")
        c, _o, err = self.ask(run, framing())
        self.assertEqual(c, 1)
        self.assertIn("status 'sideways' is not one of", err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(self.calls(), [])

    def test_framings_cannot_hide_facts_or_code_in_other_forms(self):
        run = self.open()
        bad = framing(state={"what_was_done": "fine", "extra": {"facts": ["invented"], "notes": [{"checked_facts": 1}]},
                             "code": "export function save(a) {}\nx = load(y)\n--- a/app/store.py"})
        c, _o, err = self.jev("check", "--run", str(run), "--framing", self.file("f.json", bad))
        self.assertEqual(c, 4)
        for words in ("may not carry 'extra.facts'", "may not carry 'extra.notes[0].checked_facts'", "an export",
                      "an assignment from a call", "a diff"):
            self.assertIn(words, err)


class Records(Base):
    def test_finalize_writes_one_record_and_notes_add_to_it(self):
        run = self.open()
        self.assertEqual(self.ask(run, framing())[0], 0)
        interp = self.file("interp.md", "Codex's reading: a crash after save and before display loses nothing.")
        tech = self.file("tech.md", "Claude: store.py flushes before returning (store.py:40).")
        c, out, err = self.jev("finalize", "--run", str(run), "--interpretation", interp, "--technical", tech)
        self.assertEqual(c, 0, err)
        record = (run / "record.md").read_text(encoding="utf-8")
        for words in ("# roast: task -- Resume keeps saved answers", "## What was asked for (the asker's own words)",
                      "- exit_condition: After a restart", "app/store.py:40; checked by claude roast-technical",
                      "## Claude's technical findings", "flushes before returning",
                      "## Codex's reading (its interpretation, not Jev's)", "probability true 0.10",
                      "jev-1.13.0 (asked for jev-latest)", "### request-1.json", "### response-1.json",
                      "Not yet recorded"):
            self.assertIn(words, record)
        self.assertEqual((run.parent / "latest.md").read_text(encoding="utf-8").strip(),
                         f"Latest roast record: {run / 'record.md'}")
        c, _o, err = self.jev("finalize", "--run", str(run), "--interpretation", interp)
        self.assertEqual(c, 1)
        self.assertIn("never overwritten", err)
        self.assertEqual((run / "record.md").read_text(encoding="utf-8"), record)
        c, out, _ = self.jev("note", "--run", str(run), "--kind", "disposition",
                             "--file", self.file("d.md", "Finding 1 filed as T-13."))
        self.assertEqual(c, 0)
        self.assertEqual(Path(out.strip()).read_text(encoding="utf-8"), "Finding 1 filed as T-13.")
        c, out, _ = self.jev("show", "--run", str(run), "--json")
        files = json.loads(out)["files"]
        self.assertIn("record.md", files)
        self.assertTrue(any(f.startswith("notes/") and f.endswith("-disposition.md") for f in files))

    def test_finalize_without_jev_says_so(self):
        run = self.open()
        self.assertEqual(self.ask(run, framing(state={"x": "```"}))[0], 4)
        interp = self.file("interp.md", "Reading.")
        c, _o, err = self.jev("finalize", "--run", str(run), "--interpretation", interp)
        self.assertEqual(c, 2)
        self.assertIn("--close-jev", err)
        c, _o, err = self.jev("finalize", "--run", str(run), "--interpretation", interp,
                              "--close-jev", "owner asked to finish without Jev")
        self.assertEqual(c, 0, err)
        record = (run / "record.md").read_text(encoding="utf-8")
        self.assertIn("unjudged: closed while needs_correction: owner asked to finish without Jev", record)
        self.assertIn("Jev gave no numbers for this run", record)
        run2 = self.open()
        self.assertEqual(self.jev("finalize", "--run", str(run2), "--interpretation", interp)[0], 0)
        self.assertIn("no Jev step was run", (run2 / "record.md").read_text(encoding="utf-8"))


class Units(unittest.TestCase):
    def test_shown_follows_jevs_terms(self):
        self.assertEqual(jev.shown({"type": "noul", "noul": 0.42}), "probability true 0.42")
        self.assertIn("confidence 0.50", jev.shown({"type": "choice", "choice": "a", "confidence": 0.5,
                                                     "probabilities": {"a": 0.6, "b": 0.4}}))

    def test_answer_checks(self):
        qs = {"c": {"type": "choice", "criteria": {"a": "", "b": ""}},
              "s": {"type": "score", "criteria": ["x", "y", "z"]}}
        good = {"answers": {"c": {"type": "choice", "choice": "a", "confidence": 0.2, "probabilities": {"a": 0.6, "b": 0.4}},
                            "s": {"type": "score", "score": 1.4, "confidence": 0.3, "probabilities": {"0": 0.1, "1": 0.4, "2": 0.5}}}}
        self.assertEqual(jev.answer_problems(qs, good), [])
        bad = json.loads(json.dumps(good))
        bad["answers"]["c"]["choice"] = "z"
        bad["answers"]["s"]["probabilities"] = {"0": 1.0}
        bad["answers"]["s"]["score"] = 7
        problems = " ".join(jev.answer_problems(qs, bad))
        for words in ("picked 'z'", "not 0..2", "no expected score"):
            self.assertIn(words, problems)
        self.assertIn("no 'answers' map", jev.answer_problems(qs, {"model": "x"})[0])


if __name__ == "__main__":
    unittest.main()
