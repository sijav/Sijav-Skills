"""Tests for the Sijav-Codex loop adapter.

Every file these tests create is under a fresh temporary directory. The hook
cases run the real skills/loop/scripts/loop_hook.py as a subprocess with a
simulated Codex payload on stdin; the launcher cases run the plugin's real
hook command strings through every hook shell Codex 0.159.2 may use that is
available here (PowerShell `-Command`, `cmd /C "<command>"`, `$SHELL -lc`);
failure injection runs in-process. Codex identity variables are removed from every environment
these tests use unless a test sets them on purpose. None of this proves that
a Codex runtime delivers Stop continuations or the compaction refeed -- that
is the separate live test.

    python -m unittest -v tests/test_loop.py
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
import threading
import unittest
from unittest import mock

sys.dont_write_bytecode = True
STAGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(STAGE, "skills", "loop", "scripts")
HOOK = os.path.join(SCRIPTS, "loop_hook.py")
CLI = os.path.join(SCRIPTS, "sijav_loop.py")
PLUGIN_HOOKS = os.path.join(STAGE, "hooks", "hooks.json")
sys.path.insert(0, SCRIPTS)
import sijav_loop  # noqa: E402

PY = sys.executable
IDENTITY = ("CODEX_THREAD_ID", "CODEX_SESSION_ID")
ENV = {k: v for k, v in os.environ.items() if k not in IDENTITY}
ENV.update(PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
OWNER = "019a0000-0000-7000-8000-00000000aaaa"
OTHER = "019a0000-0000-7000-8000-00000000bbbb"
PROMISE = "COMPLETE"
VARIANT = "commandWindows" if os.name == "nt" else "command"
LAW_BODY = "Step 1: read the board.\nStep 2: finish the item.\nExit when the board is empty.\n"
CLAUDE_FRONT = (
    "---\n"
    'session: "claude-session-xyz"\n'
    "iteration: 7\n"
    "max_iterations: 40\n"
    'completion_promise: "ALL DONE"\n'
    "---\n"
)
CLEAR = ("--clear-sentinels", "--clear-claude-pause")


def env_with(**identity):
    return dict(ENV, **identity)


def run_hook(kind, payload, cwd, env=None):
    """Run the hook as Codex would; returns (stdout object or None, stderr)."""
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    proc = subprocess.run([PY, HOOK, kind], input=raw, capture_output=True, cwd=cwd, env=env or ENV, timeout=60)
    out = proc.stdout.decode("utf-8").strip()
    assert proc.returncode == 0, (proc.returncode, out, proc.stderr)
    assert "\n" not in out, out
    return (json.loads(out) if out else None), proc.stderr.decode("utf-8")


def run_cli(args, cwd, env=None):
    proc = subprocess.run([PY, CLI, *args], capture_output=True, cwd=cwd, env=env or ENV, timeout=60)
    return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")


def _hook_shells():
    """The shells Codex 0.159.2 may run a hook command with, as available here
    (hooks/src/engine/command_runner.rs build_command; core/src/shell.rs
    derive_exec_args):
      cmd          COMSPEC /C "<command>", passed raw (the empty-program default)
      PowerShell   <powershell|pwsh> [-NoProfile] -Command <command>, as one
                   argument -- the live run showed Codex using PowerShell here
      sh           $SHELL -lc <command>
    Each entry maps (command, cwd, env, stdin) to a CompletedProcess."""
    shells = []
    if os.name == "nt":
        def cmd(command, cwd, env, raw):
            comspec = env.get("COMSPEC") or "cmd.exe"
            program = f'"{comspec}"' if " " in comspec else comspec
            # A str is the command line verbatim on Windows, as raw_arg makes it.
            return subprocess.run(f'{program} /C "{command}"', input=raw, capture_output=True,
                                  cwd=cwd, env=env, timeout=120)
        shells.append(("cmd /C", cmd))
        for name in ("powershell", "pwsh"):
            exe = shutil.which(name)
            if not exe:
                continue
            for flags in (["-NoProfile", "-Command"], ["-Command"]):
                def ps(command, cwd, env, raw, exe=exe, flags=flags):
                    return subprocess.run([exe, *flags, command], input=raw, capture_output=True,
                                          cwd=cwd, env=env, timeout=120)
                shells.append((f"{name} {' '.join(flags)}", ps))
    else:
        def sh(command, cwd, env, raw):
            return subprocess.run([env.get("SHELL") or "/bin/sh", "-lc", command], input=raw,
                                  capture_output=True, cwd=cwd, env=env, timeout=120)
        shells.append(("$SHELL -lc", sh))
    return shells


HOOK_SHELLS = _hook_shells()


def native_spawn(command_line, payload, cwd, env=None, shell=None):
    """Spawn a hook command through one of HOOK_SHELLS (default: the first)."""
    env = env or ENV
    raw = json.dumps(payload).encode("utf-8")
    run = dict(HOOK_SHELLS)[shell] if shell else HOOK_SHELLS[0][1]
    return run(command_line, cwd, env, raw)


def stop_event(session, cwd, message, turn="turn-1", active=False, **extra):
    event = {
        "session_id": session,
        "transcript_path": None,
        "cwd": cwd,
        "hook_event_name": "Stop",
        "model": "gpt-test",
        "permission_mode": "default",
        "turn_id": turn,
        "stop_hook_active": active,
        "last_assistant_message": message,
    }
    event.update(extra)
    return event


def compact_event(session, cwd, source="compact"):
    return {"session_id": session, "transcript_path": None, "cwd": cwd,
            "hook_event_name": "SessionStart", "model": "gpt-test", "source": source}


def quiet(fn, *args, **kwargs):
    """Call an in-process function with its stdout and stderr captured."""
    out = io.StringIO()
    with contextlib.redirect_stderr(out), contextlib.redirect_stdout(out):
        result = fn(*args, **kwargs)
    return result, out.getvalue()


class LoopCase(unittest.TestCase):
    """A disposable project whose path has spaces and a quote, with a
    Claude-style law under .claude and a subdirectory to work from."""

    def setUp(self):
        environ = mock.patch.dict(os.environ)
        environ.start()
        self.addCleanup(environ.stop)
        for name in IDENTITY:
            os.environ.pop(name, None)
        self._tmp = tempfile.TemporaryDirectory(prefix="sijav loop ")
        self.base = self._tmp.name
        self.root = os.path.join(self.base, "My Project's root")
        self.sub = os.path.join(self.root, "src", "deep dir")
        os.makedirs(self.sub)
        os.makedirs(os.path.join(self.root, ".claude"))
        self.law = os.path.join(self.root, ".claude", "proj-loop.local.md")
        with open(self.law, "w", encoding="utf-8", newline="\n") as f:
            f.write(CLAUDE_FRONT + LAW_BODY)
        with open(self.law, "rb") as f:
            self.law_bytes = f.read()
        self.state = sijav_loop.state_path(self.root)
        self.events = sijav_loop.events_path(self.root)
        self.sentinel = os.path.join(self.root, ".stop")

    def tearDown(self):
        self._tmp.cleanup()

    def start(self, session=OWNER, cap=5, *extra, cwd=None, expect=0, env=None):
        args = ["start", "--project-root", self.root, "--law", ".claude/proj-loop.local.md",
                "--max-iterations", str(cap), "--promise", PROMISE, *extra]
        if session is not None:
            args += ["--session", session]
        code, out, err = run_cli(args, cwd or self.sub, env)
        self.assertEqual(code, expect, out + err)
        return out + err

    def read_state(self):
        with open(self.state, encoding="utf-8") as f:
            return json.load(f)

    def write_state(self, state):
        with open(self.state, "w", encoding="utf-8") as f:
            json.dump(state, f)

    def events_list(self):
        if not os.path.exists(self.events):
            return []
        with open(self.events, encoding="utf-8") as f:
            return [json.loads(line) for line in f]

    def stop(self, session=OWNER, message="working", cwd=None, env=None, **kw):
        return run_hook("stop", stop_event(session, cwd or self.sub, message, **kw), self.sub, env)

    def assertLawUntouched(self):
        with open(self.law, "rb") as f:
            self.assertEqual(f.read(), self.law_bytes, "the Claude law was modified")

    def make_sentinel(self, path=None, text="owner pause\n"):
        path = path or self.sentinel
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path


class StartTests(LoopCase):
    def test_start_from_subdirectory_records_absolute_paths(self):
        self.start()
        state = self.read_state()
        self.assertEqual(state["schema"], sijav_loop.SCHEMA)
        self.assertTrue(sijav_loop.same_path(state["project_root"], self.root))
        self.assertTrue(sijav_loop.same_path(state["law_path"], self.law))
        self.assertEqual(state["owner_session"], OWNER)
        self.assertEqual((state["status"], state["iteration"], state["max_iterations"]), ("active", 0, 5))
        self.assertEqual(state["completion_promise"], PROMISE)
        self.assertEqual(state["pause_sentinels"], [self.sentinel])
        self.assertLawUntouched()

    def test_start_requires_every_parameter(self):
        base = ["start", "--project-root", self.root, "--law", self.law, "--session", OWNER]
        for missing in (["--promise", PROMISE], ["--max-iterations", "3"]):
            code, _, err = run_cli(base + missing, self.root)
            self.assertEqual(code, 2, err)
        code, _, err = run_cli(["start", "--project-root", self.root, "--law", self.law,
                                "--max-iterations", "3", "--promise", PROMISE], self.root)
        self.assertEqual(code, 2, err)
        self.assertIn("--session", err)
        self.assertFalse(os.path.exists(self.state))

    def test_start_rejects_bad_values(self):
        for extra, cause in (
            (["--max-iterations", "0", "--promise", PROMISE], "positive"),
            (["--max-iterations", "3", "--promise", ""], "completion_promise"),
            (["--max-iterations", "3", "--promise", " DONE"], "surrounding spaces"),
            (["--max-iterations", "3", "--promise", "<b>"], "angle brackets"),
        ):
            code, _, err = run_cli(["start", "--project-root", self.root, "--law", self.law,
                                    "--session", OWNER, *extra], self.root)
            self.assertEqual(code, 1, err)
            self.assertIn(cause, err)
        code, _, err = run_cli(["start", "--project-root", self.root, "--law", "missing.md",
                                "--session", OWNER, "--max-iterations", "3", "--promise", PROMISE], self.root)
        self.assertEqual(code, 1)
        self.assertIn("is not a file", err)
        code, _, err = run_cli(["start", "--project-root", self.root, "--law", self.law,
                                "--session", "has space", "--max-iterations", "3", "--promise", PROMISE], self.root)
        self.assertEqual(code, 1)
        self.assertIn("not a session id", err)
        self.assertFalse(os.path.exists(self.state))

    def test_values_from_law_only_when_selected_and_present(self):
        code, out, err = run_cli(["start", "--project-root", self.root, "--law", self.law, "--session", OWNER,
                                  "--max-iterations-from-law", "--promise-from-law"], self.root)
        self.assertEqual(code, 0, err)
        state = self.read_state()
        self.assertEqual((state["max_iterations"], state["completion_promise"]), (40, "ALL DONE"))
        self.assertEqual(state["max_iterations_source"], "law front matter")
        self.assertLawUntouched()

        bare = os.path.join(self.root, "bare law.md")
        with open(bare, "w", encoding="utf-8") as f:
            f.write("---\nmax_iterations: 0\n---\nBody\n")
        code, _, err = run_cli(["start", "--project-root", self.root, "--law", bare, "--session", OWNER,
                                "--max-iterations-from-law", "--promise", PROMISE], self.root)
        self.assertEqual(code, 1)
        self.assertIn("0, 'unlimited'", err)
        code, _, err = run_cli(["start", "--project-root", self.root, "--law", bare, "--session", OWNER,
                                "--max-iterations", "3", "--promise-from-law"], self.root)
        self.assertEqual(code, 1)
        self.assertIn("has no completion_promise", err)
        code, _, err = run_cli(["start", "--project-root", self.root, "--law", self.law, "--session", OWNER,
                                "--max-iterations", "3", "--max-iterations-from-law", "--promise", PROMISE], self.root)
        self.assertEqual(code, 2, "explicit and law-derived cap are mutually exclusive")

    def test_the_owners_cap_is_not_limited(self):
        """The cap is the owner's policy: a real law uses 10000000."""
        big = os.path.join(self.root, "big law.md")
        with open(big, "w", encoding="utf-8") as f:
            f.write("---\nmax_iterations: 10000000\ncompletion_promise: DONE\n---\nBody\n")
        code, _, err = run_cli(["start", "--project-root", self.root, "--law", big, "--session", OWNER,
                                "--max-iterations-from-law", "--promise-from-law"], self.root)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.read_state()["max_iterations"], 10000000)
        out, _ = self.stop(message="a")
        self.assertEqual(out["decision"], "block")
        self.assertIn("1 of 10000000", out["reason"])

    def test_law_problems_are_reported(self):
        broken = os.path.join(self.root, "broken.md")
        with open(broken, "w", encoding="utf-8") as f:
            f.write("---\nmax_iterations: 3\nno closing line\n")
        code, _, err = run_cli(["start", "--project-root", self.root, "--law", broken, "--session", OWNER,
                                "--max-iterations", "3", "--promise", PROMISE], self.root)
        self.assertEqual(code, 1)
        self.assertIn("never closes it", err)
        empty = os.path.join(self.root, "empty.md")
        with open(empty, "w", encoding="utf-8") as f:
            f.write("---\na: b\n---\n\n")
        code, _, err = run_cli(["start", "--project-root", self.root, "--law", empty, "--session", OWNER,
                                "--max-iterations", "3", "--promise", PROMISE], self.root)
        self.assertEqual(code, 1)
        self.assertIn("no body", err)


class MisarmingTests(LoopCase):
    def test_subdirectory_root_below_the_laws_project_is_refused(self):
        sub = os.path.join(self.root, "src")
        code, _, err = run_cli(["start", "--project-root", ".", "--law", self.law, "--session", OWNER,
                                "--max-iterations", "3", "--promise", PROMISE], sub)
        self.assertEqual(code, 1)
        self.assertIn("is below", err)
        self.assertIn("--confirm-root", err)
        self.assertFalse(os.path.exists(sijav_loop.state_dir(sub)))
        code, _, err = run_cli(["start", "--project-root", ".", "--law", self.law, "--session", OWNER,
                                "--max-iterations", "3", "--promise", PROMISE, "--confirm-root"], sub)
        self.assertEqual(code, 0, err)

    def test_root_inside_a_repository_is_refused(self):
        os.makedirs(os.path.join(self.base, ".git"))
        out = self.start(expect=1)
        self.assertIn("inside the repository", out)
        self.assertFalse(os.path.exists(self.state))
        self.start(OWNER, 5, "--confirm-root")

    def test_codex_session_working_outside_the_root_is_refused(self):
        root_env = env_with(CODEX_THREAD_ID=OWNER, CODEX_SESSION_ID=OWNER)
        out = self.start(OWNER, 5, cwd=self.base, env=root_env, expect=1)
        self.assertIn("would never find the state", out)
        self.start(OWNER, 5, cwd=self.sub, env=root_env)


class IdentityTests(LoopCase):
    def test_descendant_or_partial_identity_cannot_claim(self):
        for env, cause in (
            (env_with(CODEX_THREAD_ID=OTHER, CODEX_SESSION_ID=OWNER), "descendant thread"),
            (env_with(CODEX_SESSION_ID=OWNER), "CODEX_THREAD_ID is not set"),
            (env_with(CODEX_THREAD_ID=OWNER), "CODEX_SESSION_ID is not set"),
        ):
            self.assertIn(cause, self.start(OWNER, env=env, expect=1))
            self.assertIn(cause, self.start(None, 5, "--session-from-env", env=env, expect=1))
        self.assertFalse(os.path.exists(self.state))

    def test_root_session_must_match_the_explicit_session(self):
        root_env = env_with(CODEX_THREAD_ID=OWNER, CODEX_SESSION_ID=OWNER)
        self.assertIn(f"is not this Codex session ({OWNER})", self.start(OTHER, env=root_env, expect=1))
        self.start(None, 5, "--session-from-env", env=root_env)
        self.assertEqual(self.read_state()["owner_session"], OWNER)

    def test_session_from_env_needs_codex(self):
        out = self.start(None, 5, "--session-from-env", expect=1)
        self.assertIn("not running inside a Codex session", out)

    def test_resume_and_reset_refuse_descendants(self):
        self.start()
        descendant = env_with(CODEX_THREAD_ID=OTHER, CODEX_SESSION_ID=OWNER)
        for args in (["resume", "--cwd", self.sub, "--session", OWNER],
                     ["reset", "--cwd", self.sub, "--session", OWNER],
                     ["reset", "--cwd", self.sub, "--session-from-env"]):
            code, _, err = run_cli(args, self.sub, descendant)
            self.assertEqual(code, 1, args)
            self.assertIn("descendant thread", err)
        self.assertEqual(self.read_state()["owner_session"], OWNER)

    def test_descendant_hook_drives_nothing(self):
        self.start()
        descendant = env_with(CODEX_THREAD_ID=OTHER, CODEX_SESSION_ID=OWNER)
        out, err = self.stop(env=descendant)
        self.assertIsNone(out)
        self.assertIn("descendant thread", err)
        out, _ = run_hook("session-start", compact_event(OWNER, self.sub), self.sub, descendant)
        self.assertIsNone(out)
        self.assertEqual(self.read_state()["iteration"], 0)
        self.assertEqual([e["outcome"] for e in self.events_list() if e["event"] in ("Stop", "SessionStart")],
                         ["descendant_thread", "descendant_thread"])
        root_env = env_with(CODEX_THREAD_ID=OWNER, CODEX_SESSION_ID=OWNER)
        out, _ = self.stop(env=root_env, message="root")
        self.assertEqual(out["decision"], "block")


class OwnershipTests(LoopCase):
    def test_unarmed_session_stops_normally(self):
        out, err = self.stop()
        self.assertIsNone(out)
        self.assertIn("not armed", err)
        out, err = run_hook("session-start", compact_event(OWNER, self.sub), self.sub)
        self.assertIsNone(out)
        self.assertFalse(os.path.exists(sijav_loop.state_dir(self.root)))

    def test_other_session_stops_normally_and_is_recorded(self):
        self.start()
        out, err = self.stop(session=OTHER)
        self.assertIsNone(out)
        self.assertIn("does not own the loop", err)
        self.assertEqual(self.read_state()["iteration"], 0)
        last = self.events_list()[-1]
        self.assertEqual((last["event"], last["session_id"], last["outcome"]), ("Stop", OTHER, "other_session"))

    def test_claim_cannot_be_replaced_without_takeover(self):
        self.start()
        out = self.start(OTHER, expect=1)
        self.assertIn(f"claimed by session {OWNER}", out)
        self.assertIn(f"--takeover {OWNER}", out)
        out = self.start(OTHER, 5, "--takeover", "someone-else", expect=1)
        self.assertIn("names a different session", out)
        self.assertEqual(self.read_state()["owner_session"], OWNER)
        self.start(OTHER, 5, "--takeover", OWNER)
        state = self.read_state()
        self.assertEqual((state["owner_session"], state["takeover_from"]), (OTHER, OWNER))
        out, _ = self.stop(session=OWNER)
        self.assertIsNone(out, "the old owner stops normally after a takeover")
        out, _ = self.stop(session=OTHER)
        self.assertEqual(out["decision"], "block")

    def test_takeover_of_nothing_is_refused(self):
        out = self.start(OWNER, 5, "--takeover", OTHER, expect=1)
        self.assertIn("no existing claim", out)

    def test_claim_survives_terminal_status(self):
        self.start()
        code, _, err = run_cli(["stop", "--cwd", self.sub, "--reason", "owner said stop"], self.sub)
        self.assertEqual(code, 0, err)
        self.assertIn("claimed by session", self.start(OTHER, expect=1))

    def test_reset_requires_the_owner(self):
        self.start()
        code, _, err = run_cli(["reset", "--cwd", self.sub, "--session", OTHER], self.sub)
        self.assertEqual(code, 1)
        self.assertIn("claimed by session", err)
        code, out, err = run_cli(["reset", "--cwd", self.sub, "--session", OWNER], self.sub)
        self.assertEqual(code, 0, err)
        self.assertFalse(os.path.exists(self.state))
        archived = [n for n in os.listdir(sijav_loop.state_dir(self.root)) if n.startswith("state.reset-")]
        self.assertEqual(len(archived), 1)
        out, err = self.stop()
        self.assertIsNone(out)
        self.assertIn("not armed", err)
        self.start(OTHER)
        self.assertLawUntouched()

    def test_restarting_a_live_counter_needs_an_explicit_flag(self):
        self.start()
        self.start()  # nothing counted yet: a plain start replaces it
        self.stop(message="a", turn="T7")
        out = self.start(expect=1)
        self.assertIn("--restart", out)
        self.assertEqual(self.read_state()["iteration"], 1)
        out = self.start(OTHER, 5, "--takeover", OWNER, expect=1)
        self.assertIn("--restart", out)
        self.start(OWNER, 5, "--restart")
        state = self.read_state()
        self.assertEqual(state["iteration"], 0)
        self.assertEqual(state["restarted_from"]["iteration"], 1)
        self.assertEqual(state["restarted_from"]["last_turn_id"], "T7")
        out, err = self.stop(message="b", turn="T7", active=True)
        self.assertEqual(out["decision"], "block")
        self.assertIn("restarted inside turn T7", err)
        self.assertTrue(self.events_list()[-1]["restarted_within_turn"])


class SentinelTests(LoopCase):
    def test_sentinels_outside_the_project_or_special_are_refused_before_writing(self):
        other = os.path.join(self.base, "Other project")
        os.makedirs(other)
        other_stop = self.make_sentinel(os.path.join(other, ".stop"), "another project's pause\n")
        for sentinel, cause in (
            (os.path.join("..", "Other project", ".stop"), "not inside the project root"),
            (other_stop, "not inside the project root"),
            (".claude/proj-loop.local.md", "is the law itself"),
            (".codex/sijav-loop/state.json", "own state directory"),
            (".", "not inside the project root"),
            ("src", "is a directory"),
        ):
            out = self.start(OWNER, 5, "--sentinel", sentinel, *CLEAR, expect=1)
            self.assertIn(cause, out, sentinel)
        self.assertFalse(os.path.exists(sijav_loop.state_dir(self.root)), "nothing was written")
        self.assertTrue(os.path.exists(other_stop))
        self.assertLawUntouched()

    def test_a_state_naming_an_outside_sentinel_is_malformed(self):
        self.start()
        other_stop = self.make_sentinel(os.path.join(self.base, "outside.stop"))
        state = self.read_state()
        state["pause_sentinels"].append(other_stop)
        self.write_state(state)
        out, _ = self.stop()
        self.assertNotIn("decision", out)
        self.assertIn("not inside the project root", out["systemMessage"])
        code, _, err = run_cli(["resume", "--cwd", self.sub, "--session", OWNER, *CLEAR], self.sub)
        self.assertEqual(code, 1)
        self.assertTrue(os.path.exists(other_stop))

    def test_start_keeps_sentinels_unless_explicitly_cleared(self):
        self.make_sentinel(text="the owner's words\n")
        out = self.start(expect=1)
        self.assertIn("--clear-sentinels", out)
        self.assertFalse(os.path.exists(self.state))
        out = self.start(OWNER, 5, "--clear-sentinels", expect=1)
        self.assertIn("claude-session-xyz", out)
        self.assertIn("--clear-claude-pause", out)
        self.assertFalse(os.path.exists(self.state))
        self.assertTrue(os.path.exists(self.sentinel))
        out = self.start(OWNER, 5, *CLEAR)
        self.assertFalse(os.path.exists(self.sentinel))
        self.assertIn("Moved pause sentinels aside", out)
        archive = os.path.join(sijav_loop.state_dir(self.root), "cleared-sentinels")
        (kept,) = os.listdir(archive)
        with open(os.path.join(archive, kept), encoding="utf-8") as f:
            self.assertEqual(f.read(), "the owner's words\n")
        self.assertLawUntouched()

    def test_law_without_a_claude_session_needs_only_clear_sentinels(self):
        law = os.path.join(self.root, "codex law.md")
        with open(law, "w", encoding="utf-8") as f:
            f.write("Codex-only law\n")
        self.make_sentinel()
        code, _, err = run_cli(["start", "--project-root", self.root, "--law", law, "--session", OWNER,
                                "--max-iterations", "3", "--promise", PROMISE, "--clear-sentinels"], self.sub)
        self.assertEqual(code, 0, err)
        self.assertFalse(os.path.exists(self.sentinel))

    def test_owner_pause_and_resume(self):
        self.start()
        self.stop(message="one")
        code, out, err = run_cli(["pause", "--cwd", self.sub, "--reason", "owner asked to pause",
                                  "--session", OWNER], self.sub)
        self.assertEqual(code, 0, out + err)
        self.assertTrue(os.path.isfile(self.sentinel))
        state = self.read_state()
        self.assertEqual(state["status"], "paused")
        self.assertIn("owner asked to pause", state["status_reason"])
        out, err = self.stop(message="two")
        self.assertIsNone(out)
        self.assertIn("paused", err)
        out, _ = run_hook("session-start", compact_event(OWNER, self.sub), self.sub)
        self.assertIsNone(out)
        code, _, err = run_cli(["resume", "--cwd", self.sub, "--session", OWNER], self.sub)
        self.assertEqual(code, 1)
        self.assertIn("--clear-sentinels", err)
        self.assertEqual(self.read_state()["status"], "paused")
        code, out, err = run_cli(["resume", "--cwd", self.sub, "--session", OWNER, *CLEAR], self.sub)
        self.assertEqual(code, 0, err)
        self.assertFalse(os.path.exists(self.sentinel))
        out, _ = self.stop(message="three")
        self.assertEqual(out["decision"], "block")
        self.assertEqual(self.read_state()["iteration"], 2)

    def test_law_named_sentinel_pauses_and_is_only_cleared_by_explicit_request(self):
        extra = os.path.join(self.root, "ops", ".stop")
        os.makedirs(os.path.dirname(extra))
        self.start(OWNER, 5, "--sentinel", "ops/.stop")
        self.assertIn(extra, self.read_state()["pause_sentinels"])
        self.make_sentinel(extra)
        for i in range(3):
            out, err = self.stop(message=f"You've hit your usage limit ({i})", active=i > 0)
            self.assertIsNone(out)
            self.assertIn("pause sentinel", err)
        run_hook("session-start", compact_event(OWNER, self.sub), self.sub)
        run_cli(["status", "--cwd", self.sub], self.sub)
        self.assertTrue(os.path.exists(extra), "hooks and status never clear a sentinel")
        self.assertEqual(self.read_state()["iteration"], 0)
        self.assertEqual(self.events_list()[-2]["outcome"], "paused_by_sentinel")
        code, out, err = run_cli(["resume", "--cwd", self.sub, "--session", OWNER, *CLEAR], self.sub)
        self.assertEqual(code, 0, err)
        self.assertFalse(os.path.exists(extra))

    def test_pause_holds_when_the_state_cannot_be_written(self):
        self.start()
        args = ["pause", "--project-root", self.root, "--reason", "owner asked"]
        with mock.patch.object(sijav_loop.os, "replace", side_effect=OSError(5, "simulated: disk refused")):
            code, out = quiet(sijav_loop.main, args)
        self.assertEqual(code, 3, out)
        self.assertIn("PAUSE IN EFFECT", out)
        self.assertIn("simulated: disk refused", out)
        self.assertTrue(os.path.isfile(self.sentinel))
        self.assertEqual(self.read_state()["status"], "active")
        out, err = self.stop(message="still running?")
        self.assertIsNone(out)
        self.assertIn("pause sentinel", err)

    def test_pause_holds_with_an_unreadable_state_or_a_held_lock(self):
        self.start()
        with sijav_loop.locked(self.root):
            code, out = quiet(sijav_loop.main, ["pause", "--project-root", self.root, "--reason", "now",
                                                "--lock-timeout", "0.2"])
        self.assertEqual(code, 3, out)
        self.assertIn("could not lock", out)
        os.remove(self.sentinel)
        with open(self.state, "w", encoding="utf-8") as f:
            f.write("{broken")
        code, out, err = run_cli(["pause", "--project-root", self.root, "--reason", "now"], self.sub)
        self.assertEqual(code, 3, out + err)
        self.assertIn("not valid JSON", err)
        self.assertTrue(os.path.isfile(self.sentinel))

    def test_stop_command_records_reason(self):
        self.start()
        code, out, err = run_cli(["stop", "--project-root", self.root, "--reason", "owner ended it"], self.base)
        self.assertEqual(code, 0, err)
        state = self.read_state()
        self.assertEqual(state["status"], "stopped")
        self.assertIn("owner ended it", state["status_reason"])
        self.assertIsNone(self.stop()[0])


class StopTests(LoopCase):
    def test_owner_continues_on_successive_stops_in_one_turn(self):
        self.start(cap=5)
        reasons = []
        for i, active in enumerate((False, True, True), start=1):
            out, _ = self.stop(message=f"progress {i}", turn="turn-7", active=active)
            self.assertEqual(out["decision"], "block")
            self.assertTrue(out["reason"].startswith(f"SIJAV LOOP CONTINUATION {i} of 5."))
            reasons.append(out["reason"])
            self.assertEqual(self.read_state()["iteration"], i)
        self.assertTrue(reasons[0].endswith(LAW_BODY), "the law body is sent verbatim")
        self.assertNotIn("claude-session-xyz", reasons[0], "the Claude front matter is not sent")
        self.assertIn(f"<promise>{PROMISE}</promise>", reasons[0])
        outcomes = [e["outcome"] for e in self.events_list() if e["event"] == "Stop"]
        self.assertEqual(outcomes, ["continued"] * 3)
        self.assertLawUntouched()

    def test_identical_callbacks_each_count_until_exhausted(self):
        """A native continuation keeps session, turn_id and stop_hook_active,
        and the model may repeat its final message while it works on things
        the hook never sees (here, a law edited between callbacks). Each
        identical callback is counted, so the loop neither halts early nor
        escapes the cap; a true duplicate delivery would cost one iteration."""
        self.start(cap=3)
        same = dict(message="Still working on step 2.", turn="turn-3", active=True)
        for i in (1, 2, 3):
            with open(self.law, "a", encoding="utf-8") as f:
                f.write(f"Note {i}: context the hook payload does not carry.\n")
            out, _ = self.stop(**same)
            self.assertEqual(out["decision"], "block", i)
            self.assertIn(f"Note {i}:", out["reason"])
            self.assertEqual(self.read_state()["iteration"], i)
        out, _ = self.stop(**same)
        self.assertNotIn("decision", out)
        state = self.read_state()
        self.assertEqual((state["status"], state["iteration"]), ("exhausted", 3))
        stops = [e for e in self.events_list() if e["event"] == "Stop"]
        self.assertEqual([e["outcome"] for e in stops], ["continued"] * 3 + ["exhausted"])
        self.assertEqual([e["same_as_previous_stop"] for e in stops], [False, True, True, True])
        self.assertEqual(len({e["message_sha256"] for e in stops}), 1)
        self.assertEqual({e["turn_id"] for e in stops}, {"turn-3"})

    def test_native_first_stop_differs_from_the_second(self):
        """Codex sends stop_hook_active false first, true afterwards; the
        diagnostic hash covers it, so identical text gives False, False, True."""
        self.start(cap=5)
        for active in (False, True, True):
            self.stop(message="same", turn="t", active=active)
        stops = [e for e in self.events_list() if e["event"] == "Stop"]
        self.assertEqual([e["same_as_previous_stop"] for e in stops], [False, False, True])

    def test_exact_promise_completes(self):
        self.start()
        self.stop(message="earlier work")
        out, _ = self.stop(message=f"All exit checks pass.\n\n<promise>{PROMISE}</promise>\n", active=True)
        self.assertNotIn("decision", out)
        self.assertIn("complete", out["systemMessage"])
        state = self.read_state()
        self.assertEqual(state["status"], "complete")
        self.assertIn("exact completion promise", state["status_reason"])
        out, err = self.stop(message="anything after")
        self.assertIsNone(out)
        self.assertIn("is complete", err)

    def test_false_promises_do_not_stop(self):
        self.start(cap=50)
        tag = f"<promise>{PROMISE}</promise>"
        transcript = os.path.join(self.base, "transcript with promise.jsonl")
        with open(transcript, "w", encoding="utf-8") as f:
            f.write(json.dumps({"role": "assistant", "content": tag}) + "\n")
        false_messages = [
            f"{tag}\nbut there is more to do",
            f"I will print {tag} when done.",
            f"`{tag}`",
            f"> {tag}",
            f"    {tag}",
            f"<promise>{PROMISE.lower()}</promise>",
            f"<promise> {PROMISE} </promise>",
            "<promise>DONE</promise>",
            f"```\n{tag}",
            f"~~~text\nexample:\n{tag}",
            "",
            None,
        ]
        for i, message in enumerate(false_messages):
            out, _ = run_hook("stop", stop_event(OWNER, self.sub, message, turn=f"t{i}",
                                                 transcript_path=transcript), self.sub)
            self.assertEqual(out and out.get("decision"), "block", repr(message))
        self.assertEqual(self.read_state()["status"], "active")
        self.assertEqual(self.read_state()["iteration"], len(false_messages))

    def test_promise_matching_rules(self):
        m = sijav_loop.promise_matched
        tag = "<promise>COMPLETE</promise>"
        self.assertTrue(m(tag, "COMPLETE"))
        self.assertTrue(m(f"done\r\n{tag}  \r\n\r\n", "COMPLETE"))
        self.assertTrue(m(f"```\ncode\n```\n{tag}", "COMPLETE"))
        self.assertFalse(m(f"```\ncode\n{tag}", "COMPLETE"))
        self.assertFalse(m(f" {tag}", "COMPLETE"))
        self.assertFalse(m(f"{tag} and more", "COMPLETE"))
        self.assertTrue(m("<promise>a.b*c</promise>", "a.b*c"))
        self.assertFalse(m("<promise>aXb*c</promise>", "a.b*c"))

    def test_cap_exhausts_without_completion(self):
        self.start(cap=2)
        for i in (1, 2):
            out, _ = self.stop(message=f"m{i}", active=i > 1)
            self.assertEqual(out["decision"], "block")
        out, _ = self.stop(message="m3", active=True)
        self.assertNotIn("decision", out)
        self.assertIn("exhausted", out["systemMessage"])
        state = self.read_state()
        self.assertEqual((state["status"], state["iteration"]), ("exhausted", 2))
        self.assertIn("not completion", state["status_reason"])
        out, _ = self.stop(message="m4", active=True)
        self.assertIsNone(out)
        code, _, err = run_cli(["resume", "--cwd", self.sub, "--session", OWNER], self.sub)
        self.assertEqual(code, 1)
        self.assertIn("is exhausted", err)

    def test_wrong_or_malformed_input_never_blocks(self):
        self.start()
        for payload, cause in (
            (b"not json", "not JSON"),
            (b"", "not JSON"),
            ({**stop_event(OWNER, self.sub, "x"), "hook_event_name": "SessionStart"}, "hook_event_name"),
            ({**stop_event(OWNER, self.sub, "x"), "session_id": ""}, "session_id"),
            ({**stop_event(OWNER, self.sub, "x"), "cwd": "relative/dir"}, "absolute"),
            ({k: v for k, v in stop_event(OWNER, self.sub, "x").items() if k != "cwd"}, "cwd"),
        ):
            out, err = run_hook("stop", payload, self.sub)
            self.assertIn(cause, out["systemMessage"])
            self.assertNotIn("decision", out)
            self.assertIn(cause, err)
        self.assertEqual(self.read_state()["iteration"], 0)
        out, _ = run_hook("bogus", stop_event(OWNER, self.sub, "x"), self.sub)
        self.assertIn("needs one of", out["systemMessage"])


class CompactionTests(LoopCase):
    def test_owner_gets_entire_law_after_compaction(self):
        long_body = LAW_BODY + "".join(f"Rule {i}: keep the record exact.\n" for i in range(2000)) + "END-OF-LAW\n"
        with open(self.law, "w", encoding="utf-8") as f:
            f.write(CLAUDE_FRONT + long_body)
        self.start()
        out, _ = run_hook("session-start", compact_event(OWNER, self.sub), self.sub)
        spec = out["hookSpecificOutput"]
        self.assertEqual(spec["hookEventName"], "SessionStart")
        self.assertTrue(spec["additionalContext"].startswith("A COMPACTION JUST HAPPENED."))
        self.assertTrue(spec["additionalContext"].endswith(long_body))
        self.assertGreater(len(long_body), 50000)
        self.assertEqual(self.read_state()["iteration"], 0, "compaction does not count")
        self.assertEqual(self.events_list()[-1]["outcome"], "law_reloaded")

    def test_compaction_refeed_only_for_owner_and_compact_source(self):
        self.start()
        out, err = run_hook("session-start", compact_event(OTHER, self.sub), self.sub)
        self.assertIsNone(out)
        self.assertIn("does not own", err)
        for source in ("startup", "resume", "clear"):
            out, _ = run_hook("session-start", compact_event(OWNER, self.sub, source), self.sub)
            self.assertIsNone(out, source)
        outcomes = [e["outcome"] for e in self.events_list() if e["event"] == "SessionStart"]
        self.assertEqual(outcomes, ["other_session", "ignored_source", "ignored_source", "ignored_source"])

    def test_compaction_after_terminal_status_adds_nothing(self):
        self.start(cap=1)
        self.stop(message="a")
        self.stop(message="b")
        self.assertEqual(self.read_state()["status"], "exhausted")
        out, _ = run_hook("session-start", compact_event(OWNER, self.sub), self.sub)
        self.assertIsNone(out)


class FailureTests(LoopCase):
    def write_raw_state(self, text):
        with open(self.state, "w", encoding="utf-8") as f:
            f.write(text)

    def test_malformed_state_reports_cause_and_is_left_alone(self):
        self.start()
        good = self.read_state()
        cases = [
            ("{not json", "not valid JSON"),
            (json.dumps({**good, "iteration": "3"}), "iteration must be"),
            (json.dumps({**good, "max_iterations": 0}), "max_iterations must be"),
            (json.dumps({**good, "completion_promise": ""}), "no safe default"),
            (json.dumps({**good, "owner_session": ""}), "owner_session"),
            (json.dumps({**good, "status": "running"}), "status is 'running'"),
            (json.dumps({**good, "iteration": 9, "max_iterations": 5}), "from 0 to 5"),
            (json.dumps({**good, "schema": "other/9"}), "schema"),
            (json.dumps({**good, "pause_sentinels": [self.law]}), "is the law itself"),
            (json.dumps([1, 2]), "not a JSON object"),
        ]
        for text, cause in cases:
            self.write_raw_state(text)
            for session in (OWNER, OTHER):
                out, err = self.stop(session=session, message=f"<promise>{PROMISE}</promise>")
                self.assertNotIn("decision", out)
                self.assertIn(cause, out["systemMessage"])
                self.assertIn(cause, err)
            out, _ = run_hook("session-start", compact_event(OWNER, self.sub), self.sub)
            self.assertNotIn("hookSpecificOutput", out)
            self.assertIn(cause, out["systemMessage"])
            with open(self.state, encoding="utf-8") as f:
                self.assertEqual(f.read(), text, "a malformed state is never rewritten")
        code, _, err = run_cli(["status", "--cwd", self.sub], self.sub)
        self.assertEqual(code, 1)
        self.assertIn("not a JSON object", err)
        self.assertIn("owner is unknown", self.start(OWNER, expect=1))
        code, _, err = run_cli(["reset", "--cwd", self.sub, "--discard-malformed"], self.sub)
        self.assertEqual(code, 0, err)
        self.start(OTHER)
        self.assertLawUntouched()

    def test_discard_malformed_refuses_a_readable_state(self):
        self.start()
        code, _, err = run_cli(["reset", "--cwd", self.sub, "--discard-malformed"], self.sub)
        self.assertEqual(code, 1)
        self.assertIn("is readable", err)

    def test_failed_increment_write_halts_without_continuation(self):
        self.start()
        with open(self.state, "rb") as f:
            before = f.read()
        failure = OSError(28, "simulated: no space left on device")
        with mock.patch.object(sijav_loop.os, "replace", side_effect=failure):
            out, err = quiet(sijav_loop.stop_hook, stop_event(OWNER, self.sub, "work"))
        self.assertNotIn("decision", out)
        self.assertIn("loop halted without a continuation", out["systemMessage"])
        self.assertIn("simulated: no space left", out["systemMessage"])
        with open(self.state, "rb") as f:
            self.assertEqual(f.read(), before)
        leftovers = [n for n in os.listdir(sijav_loop.state_dir(self.root)) if n.endswith(".tmp")]
        self.assertEqual(leftovers, [])
        self.assertEqual(self.events_list()[-1]["outcome"], "error")
        out, _ = self.stop(message="work")
        self.assertEqual(out["decision"], "block", "the next event works once writes do")
        self.assertEqual(self.read_state()["iteration"], 1)

    def test_rename_is_retried_then_fails_with_cause(self):
        self.start()
        calls = []

        def refuse(src, dst):
            calls.append(dst)
            raise PermissionError(13, "simulated: file in use")

        with mock.patch.object(sijav_loop.os, "replace", side_effect=refuse), \
                mock.patch.object(sijav_loop.time, "sleep"):
            out, _ = quiet(sijav_loop.stop_hook, stop_event(OWNER, self.sub, "work"))
        self.assertEqual(len(calls), sijav_loop.REPLACE_ATTEMPTS)
        self.assertIn("simulated: file in use", out["systemMessage"])
        self.assertEqual(self.read_state()["iteration"], 0)

    def test_failed_completion_write_reports_what_remains(self):
        self.start()
        with mock.patch.object(sijav_loop.os, "replace", side_effect=OSError(5, "simulated I/O error")):
            out, _ = quiet(sijav_loop.stop_hook, stop_event(OWNER, self.sub, f"<promise>{PROMISE}</promise>"))
        self.assertNotIn("decision", out)
        message = out["systemMessage"]
        self.assertIn("complete status was not saved", message)
        self.assertIn("still says active at iteration 0 of 5", message)
        self.assertIn("next stop will continue the loop until the cap", message)
        self.assertEqual(self.read_state()["status"], "active")

    def test_failed_start_write_does_not_clear_sentinel(self):
        self.make_sentinel()
        args = ["start", "--project-root", self.root, "--law", self.law, "--session", OWNER,
                "--max-iterations", "3", "--promise", PROMISE, *CLEAR]
        with mock.patch.object(sijav_loop.os, "replace", side_effect=OSError(5, "simulated I/O error")):
            code, out = quiet(sijav_loop.main, args)
        self.assertEqual(code, 1)
        self.assertIn("simulated I/O error", out)
        self.assertFalse(os.path.exists(self.state))
        self.assertTrue(os.path.exists(self.sentinel))

    def test_held_lock_times_out_with_cause(self):
        self.start()
        with sijav_loop.locked(self.root):
            out, err = quiet(sijav_loop.stop_hook, stop_event(OWNER, self.sub, "work"), lock_timeout=0.3)
            self.assertNotIn("decision", out)
            self.assertIn("could not lock", out["systemMessage"])
            out, _ = quiet(sijav_loop.session_start_hook, compact_event(OWNER, self.sub), lock_timeout=0.3)
            self.assertIn("could not lock", out["systemMessage"])
        self.assertEqual(self.read_state()["iteration"], 0)

    def test_unreadable_law_reports_without_counting(self):
        self.start()
        os.remove(self.law)
        out, err = self.stop(message="work")
        self.assertNotIn("decision", out)
        self.assertIn("cannot read the law", out["systemMessage"])
        self.assertEqual(self.read_state()["iteration"], 0)
        out, _ = run_hook("session-start", compact_event(OWNER, self.sub), self.sub)
        self.assertIn("cannot read the law", out["systemMessage"])

    def test_internal_error_never_blocks(self):
        self.start()
        script = (
            "import sys, runpy; sys.path.insert(0, sys.argv[1]); import sijav_loop;"
            "sijav_loop._decide_stop = lambda *a: 1/0;"
            "sys.argv = [sys.argv[2], 'stop']; runpy.run_path(sys.argv[0], run_name='__main__')"
        )
        proc = subprocess.run([PY, "-c", script, SCRIPTS, HOOK],
                              input=json.dumps(stop_event(OWNER, self.sub, "w")).encode(),
                              capture_output=True, env=ENV, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertIn("internal error", out["systemMessage"])
        self.assertNotIn("decision", out)


class DiscoveryTests(LoopCase):
    def test_payload_cwd_is_used_not_process_cwd(self):
        decoy = os.path.join(self.base, "decoy project")
        os.makedirs(decoy)
        with open(os.path.join(decoy, "law.md"), "w") as f:
            f.write("Decoy law\n")
        code, _, err = run_cli(["start", "--project-root", decoy, "--law", "law.md", "--session", OWNER,
                                "--max-iterations", "5", "--promise", PROMISE], decoy)
        self.assertEqual(code, 0, err)
        self.start()
        out, _ = run_hook("stop", stop_event(OWNER, self.sub, "work"), cwd=decoy)
        self.assertIn(LAW_BODY, out["reason"])
        self.assertEqual(self.read_state()["iteration"], 1)
        with open(sijav_loop.state_path(decoy), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["iteration"], 0)

    def test_moved_or_copied_state_is_stale(self):
        self.start()
        copy = os.path.join(self.base, "copied project")
        shutil.copytree(self.root, copy)
        out, err = run_hook("stop", stop_event(OWNER, os.path.join(copy, "src"), "w"), copy)
        self.assertNotIn("decision", out)
        self.assertIn("copied or moved", out["systemMessage"])

    def test_nearest_state_wins(self):
        self.start()
        inner = os.path.join(self.root, "src")
        with open(os.path.join(inner, "inner law.md"), "w") as f:
            f.write("Inner law\n")
        code, _, err = run_cli(["start", "--project-root", inner, "--law", "inner law.md",
                                "--session", OTHER, "--max-iterations", "5", "--promise", PROMISE], inner)
        self.assertEqual(code, 0, err)
        out, _ = self.stop(session=OWNER)
        self.assertIsNone(out, "the inner project's loop belongs to another session")
        out, _ = self.stop(session=OWNER, cwd=self.root)
        self.assertEqual(out["decision"], "block")

    def test_status_reports_from_subdirectory(self):
        self.start()
        code, out, err = run_cli(["status", "--json", "--session", OWNER], self.sub)
        self.assertEqual(code, 0, err)
        info = json.loads(out)
        self.assertTrue(info["this_session_owns"])
        self.assertEqual(info["status"], "active")
        code, out, _ = run_cli(["status"], self.base)
        self.assertEqual(code, 0)
        self.assertIn("Not armed", out)


class ConcurrencyTests(LoopCase):
    def test_concurrent_claims_have_one_winner(self):
        sessions = [f"019a0000-0000-7000-8000-0000000000{i:02d}" for i in range(8)]
        procs = [subprocess.Popen([PY, CLI, "start", "--project-root", self.root, "--law", self.law,
                                   "--session", s, "--max-iterations", "5", "--promise", PROMISE],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=self.sub, env=ENV)
                 for s in sessions]
        results = [(p.wait(timeout=60), p.stderr.read().decode()) for p in procs]
        for p in procs:
            p.stdout.close()
            p.stderr.close()
        winners = [s for s, (code, _) in zip(sessions, results) if code == 0]
        self.assertEqual(len(winners), 1, results)
        for code, err in results:
            if code:
                self.assertIn(f"claimed by session {winners[0]}", err)
        self.assertEqual(self.read_state()["owner_session"], winners[0])

    def test_concurrent_stops_lose_no_count(self):
        self.start(cap=100)
        n = 12
        procs = [subprocess.Popen([PY, HOOK, "stop"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, cwd=self.sub, env=ENV) for _ in range(n)]
        outs = [None] * n

        def feed(i, p):
            outs[i] = p.communicate(json.dumps(stop_event(OWNER, self.sub, f"msg {i}", turn="t")).encode(), timeout=60)

        threads = [threading.Thread(target=feed, args=(i, p)) for i, p in enumerate(procs)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        iterations = sorted(int(json.loads(o)["reason"].split()[3]) for o, _ in outs)
        self.assertEqual(iterations, list(range(1, n + 1)))
        self.assertEqual(self.read_state()["iteration"], n)
        self.assertEqual(sum(e["outcome"] == "continued" for e in self.events_list()), n)

    def test_concurrent_identical_callbacks_stay_within_cap(self):
        self.start(cap=3)
        payload = json.dumps(stop_event(OWNER, self.sub, "identical", turn="t9", active=True)).encode()
        procs = [subprocess.Popen([PY, HOOK, "stop"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, cwd=self.sub, env=ENV) for _ in range(6)]
        outs = []
        threads = [threading.Thread(target=lambda p=p: outs.append(p.communicate(payload, timeout=60)[0]))
                   for p in procs]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        decoded = [json.loads(o) if o.strip() else None for o in outs]
        blocks = [d for d in decoded if d and d.get("decision") == "block"]
        self.assertEqual(sorted(int(b["reason"].split()[3]) for b in blocks), [1, 2, 3])
        self.assertEqual(sum(1 for d in decoded if d and "exhausted" in d.get("systemMessage", "")), 1)
        state = self.read_state()
        self.assertEqual((state["status"], state["iteration"]), ("exhausted", 3))


def plugin_hooks():
    with open(PLUGIN_HOOKS, encoding="utf-8") as f:
        return json.load(f)["hooks"]


SHELL_SPECIAL = set(" \t\"'$`%;&|<>()^!,=@{}[]#*?~")


def bootstrap_of(command):
    """The -c argument of a hook command: the text between its first two
    double quotes."""
    return command.split('"', 2)[1]


class LauncherTests(unittest.TestCase):
    """The plugin's real hook command strings, run through every hook shell
    Codex may use that exists here. Discovery substitutes ${PLUGIN_ROOT}
    itself (discovery.rs), so the tests substitute it as text; the
    interpreter token is replaced by this Python, unquoted as in a prepared
    fixture, unless a test makes it missing on purpose."""

    def setUp(self):
        if set(PY) & SHELL_SPECIAL:
            self.skipTest(f"this interpreter path cannot be used unquoted: {PY}")
        self._tmp = tempfile.TemporaryDirectory(prefix="plugin root ")
        self.base = self._tmp.name
        self.plugin = os.path.join(self.base, "Sijav Codex's plugin")
        shutil.copytree(SCRIPTS, os.path.join(self.plugin, "skills", "loop", "scripts"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        self.project = os.path.join(self.base, "a project")
        os.makedirs(self.project)

    def tearDown(self):
        self._tmp.cleanup()

    def command(self, event, interpreter=None, plugin=None):
        line = plugin_hooks()[event][0]["hooks"][0][VARIANT]
        line = line.replace("${PLUGIN_ROOT}", plugin or self.plugin)
        return (interpreter or PY) + line[line.index(" "):]

    def payload(self, event):
        return (stop_event(OWNER, self.project, "x") if event == "Stop"
                else compact_event(OWNER, self.project))

    def each_shell(self, event, **kw):
        """(shell label, CompletedProcess) for every available hook shell."""
        command = self.command(event, **kw)
        for label, _ in HOOK_SHELLS:
            yield label, native_spawn(command, self.payload(event), self.project, shell=label)

    def assertSafeMessage(self, proc, event, label):
        self.assertEqual(proc.returncode, 0, (label, proc.stdout, proc.stderr))
        out = json.loads(proc.stdout.decode("utf-8").strip())
        self.assertEqual(set(out), {"systemMessage"}, label)
        arg = "stop" if event == "Stop" else "session-start"
        self.assertIn(f"Sijav loop {arg} hook did not start", out["systemMessage"], label)

    def assertFailedWithoutBlocking(self, proc, label):
        self.assertNotIn(proc.returncode, (0, 2), (label, proc.stdout, proc.stderr))
        self.assertNotIn(b'"decision"', proc.stdout, label)

    def test_shells_under_test(self):
        labels = [label for label, _ in HOOK_SHELLS]
        if os.name == "nt":
            self.assertIn("cmd /C", labels)
            self.assertTrue(any(label.startswith(("powershell", "pwsh")) for label in labels),
                            "no PowerShell found; the shell Codex used in the live run is untested")

    def test_commands_run_the_real_hook_in_every_shell(self):
        for event in ("Stop", "SessionStart"):
            for label, proc in self.each_shell(event):
                self.assertEqual(proc.returncode, 0, (label, proc.stderr))
                self.assertEqual(proc.stdout.strip(), b"", label)
                self.assertIn(b"not armed", proc.stderr, f"{label}: the payload did not reach the hook")
        law = os.path.join(self.project, "law.md")
        with open(law, "w", encoding="utf-8") as f:
            f.write("Plugin law\n")
        code, _, err = run_cli(["start", "--project-root", self.project, "--law", law, "--session", OWNER,
                                "--max-iterations", "100", "--promise", PROMISE], self.project)
        self.assertEqual(code, 0, err)
        for label, proc in self.each_shell("Stop"):
            self.assertEqual(proc.returncode, 0, (label, proc.stderr))
            out = json.loads(proc.stdout)
            self.assertEqual(out["decision"], "block", label)
            self.assertTrue(out["reason"].endswith("Plugin law\n"), label)
        for label, proc in self.each_shell("SessionStart"):
            out = json.loads(proc.stdout)
            self.assertTrue(out["hookSpecificOutput"]["additionalContext"].endswith("Plugin law\n"), label)
        stops = [e for e in sijav_loop_events(self.project) if e["event"] == "Stop"]
        self.assertEqual([e["outcome"] for e in stops], ["continued"] * len(HOOK_SHELLS))

    def test_the_live_runs_shape_fails_in_powershell(self):
        """Root cause of the failed live run: a quoted interpreter followed
        by a quoted script is a PowerShell parse error, exit 1, and Python
        never starts. The current shape works in the same shell."""
        shells = [label for label, _ in HOOK_SHELLS if label.startswith(("powershell", "pwsh"))]
        if not shells:
            self.skipTest("no PowerShell here")
        old = f'"{PY}" "{os.path.join(self.plugin, "skills", "loop", "scripts", "loop_hook.py")}" stop'
        for label in shells:
            proc = native_spawn(old, self.payload("Stop"), self.project, shell=label)
            self.assertEqual(proc.returncode, 1, label)
            self.assertNotIn(b"not armed", proc.stderr, label)
            proc = native_spawn(self.command("Stop"), self.payload("Stop"), self.project, shell=label)
            self.assertEqual(proc.returncode, 0, (label, proc.stderr))

    def test_a_script_python_cannot_open_would_exit_2(self):
        """The hazard the bootstrap exists for: CPython exits 2 when it cannot
        open the script it is given, and Codex reads a Stop exit 2 as a block."""
        proc = subprocess.run([PY, os.path.join(self.base, "gone", "loop_hook.py"), "stop"],
                              capture_output=True, env=ENV, timeout=60)
        self.assertEqual(proc.returncode, 2)

    def test_missing_or_moved_script_is_safe_in_every_shell(self):
        for event in ("Stop", "SessionStart"):
            for label, proc in self.each_shell(event, plugin=os.path.join(self.base, "gone")):
                self.assertSafeMessage(proc, event, label)
        os.rename(os.path.join(self.plugin, "skills"), os.path.join(self.plugin, "moved"))
        for event in ("Stop", "SessionStart"):
            for label, proc in self.each_shell(event):
                self.assertSafeMessage(proc, event, label)

    def test_partial_update_without_the_module_fails_without_blocking(self):
        os.remove(os.path.join(self.plugin, "skills", "loop", "scripts", "sijav_loop.py"))
        for event in ("Stop", "SessionStart"):
            for label, proc in self.each_shell(event):
                self.assertFailedWithoutBlocking(proc, label)

    def test_missing_interpreter_fails_without_blocking(self):
        missing = os.path.join(tempfile.gettempdir(), "sijav-no-such-dir",
                               "python.exe" if os.name == "nt" else "python3")
        interpreters = ["sijav-no-such-python"]
        if not set(missing) & SHELL_SPECIAL:
            interpreters.append(missing)
        for interpreter in interpreters:
            for event in ("Stop", "SessionStart"):
                for label, proc in self.each_shell(event, interpreter=interpreter):
                    self.assertFailedWithoutBlocking(proc, f"{label} {interpreter}")

    def test_variants_are_equivalent_and_shell_neutral(self):
        for event, groups in plugin_hooks().items():
            handler = groups[0]["hooks"][0]
            posix, windows = handler["command"], handler["commandWindows"]
            arg = "stop" if event == "Stop" else "session-start"
            self.assertNotEqual(posix, windows)
            code = bootstrap_of(posix)
            self.assertEqual(code, bootstrap_of(windows))
            self.assertEqual(posix, f'python3 -c "{code}" "${{PLUGIN_ROOT}}/skills/loop/scripts/loop_hook.py" {arg}')
            self.assertEqual(windows, f'python -c "{code}" "${{PLUGIN_ROOT}}\\skills\\loop\\scripts\\loop_hook.py" {arg}')
            for special in '"$`%\\!^&|<>':
                self.assertNotIn(special, code, f"{special!r} means something to some hook shell")
            compile(code, "<bootstrap>", "exec")


def sijav_loop_events(root):
    with open(sijav_loop.events_path(root), encoding="utf-8") as f:
        return [json.loads(line) for line in f]


class PackagingTests(unittest.TestCase):
    def load(self, *parts):
        with open(os.path.join(STAGE, *parts), encoding="utf-8") as f:
            return json.load(f)

    def test_native_manifest_points_at_hooks_and_no_portable_manifest_wins(self):
        """Codex 0.159.3 loads a root plugin.json as a portable Agent Plugin,
        ahead of .codex-plugin/plugin.json, and drops its hooks; the package
        therefore ships only the native manifest."""
        self.assertFalse(os.path.exists(os.path.join(STAGE, "plugin.json")))
        manifest = self.load(".codex-plugin", "plugin.json")
        self.assertEqual(set(manifest), {"name", "version", "description", "skills", "hooks"})
        self.assertEqual((manifest["name"], manifest["version"]), ("sijav-codex", "0.1.0"))
        self.assertEqual((manifest["skills"], manifest["hooks"]), ("./skills", "./hooks/hooks.json"))
        self.assertTrue(os.path.isfile(os.path.join(STAGE, "hooks", "hooks.json")))
        self.assertTrue(os.path.isfile(os.path.join(STAGE, "skills", "loop", "SKILL.md")))
        self.assertFalse(os.path.exists(os.path.join(STAGE, ".claude-plugin")))

    def test_the_portable_manifest_is_kept_only_as_an_archived_example(self):
        archived = self.load("archive", "portable-plugin.example.json")
        self.assertEqual(archived["extensions"]["com.openai"]["hooks"], "./hooks/hooks.json")
        self.assertEqual(archived["description"], self.load(".codex-plugin", "plugin.json")["description"])

    def test_hooks_shape(self):
        hooks = self.load("hooks", "hooks.json")["hooks"]
        self.assertEqual(set(hooks), {"Stop", "SessionStart"})
        (stop_group,) = hooks["Stop"]
        self.assertNotIn("matcher", stop_group)
        (compact_group,) = hooks["SessionStart"]
        self.assertEqual(compact_group["matcher"], "compact")
        self.assertEqual(compact_group["hooks"][0]["additionalContextLimit"], 0)
        for group, arg in ((stop_group, "stop"), (compact_group, "session-start")):
            (handler,) = group["hooks"]
            self.assertEqual(handler["type"], "command")
            self.assertNotIn("async", handler)
            self.assertLessEqual(sijav_loop.LOCK_TIMEOUT, handler["timeout"] / 2)
            for key in ("command", "commandWindows"):
                self.assertTrue(handler[key].endswith(f'loop_hook.py" {arg}'), handler[key])
                self.assertNotIn("||", handler[key], "Windows PowerShell 5.1 cannot parse ||")


if __name__ == "__main__":
    unittest.main()
