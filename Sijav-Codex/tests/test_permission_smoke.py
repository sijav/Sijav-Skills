"""tests/permission_smoke.py: folder refusals, the prepared fixture, and --check against synthetic run
records (no Claude, no model). The real check runs after the root's one real helper call."""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

STAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(STAGE / "tests"))
import permission_smoke as ps  # noqa: E402


class Prepare(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="sijav smoke test "))
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)

    def test_unsafe_folders_are_refused_and_nothing_is_written(self):
        full = self.base / "full"
        full.mkdir()
        (full / "x.txt").write_text("x", encoding="utf-8")
        repo = self.base / "repo"
        (repo / ".git").mkdir(parents=True)
        board = self.base / "board"
        (board / ".claude").mkdir(parents=True)
        (board / ".claude" / "todo.db").write_bytes(b"")
        for target, words in ((full, "not an empty folder"), (repo / "fixture", ".git"),
                              (board / "fixture", ".claude/todo.db"), (STAGE / "fixture", "not a new folder under"),
                              (Path(tempfile.gettempdir()), "not a new folder under")):
            with self.subTest(str(target)):
                with self.assertRaisesRegex(ps.Refused, words):
                    ps.prepare(target)
        self.assertFalse((repo / "fixture").exists())
        self.assertFalse((board / "fixture").exists())

    def test_the_fixture_holds_only_synthetic_files(self):
        target = self.base / "fixture"
        self.assertEqual(ps.prepare(target), 0)
        fixture = json.loads((target / ps.MARKER).read_text(encoding="utf-8"))
        project = Path(fixture["project"])
        settings = json.loads((project / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertIn("Bash", settings["permissions"]["allow"])
        self.assertEqual(settings["env"]["ANTHROPIC_BASE_URL"], "http://127.0.0.1:9")
        self.assertIn("fixture_hook.py", json.dumps(settings["hooks"]))
        self.assertIn("not-a-real-secret", (project / ".env").read_text(encoding="utf-8"))
        self.assertTrue((project / "LAW.md").is_file() and (project / "PROMPT.md").is_file())
        self.assertEqual(list((target / "outside").iterdir()), [])
        prompt = (project / "PROMPT.md").read_text(encoding="utf-8")
        self.assertIn(fixture["outside_file"], prompt)
        self.assertIn(fixture["inside_file"], prompt)
        with self.assertRaises(ps.Refused):
            ps.prepare(target)  # an existing fixture is never prepared over


class Check(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="sijav smoke check "))
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.target = self.base / "fixture"
        ps.prepare(self.target)
        self.fx = json.loads((self.target / ps.MARKER).read_text(encoding="utf-8"))
        self.project = Path(self.fx["project"])

    def record(self, denials=True, write_inside=True, attempts=True, status="ok", bash=False):
        run = self.project / ".codex" / "claude-sessions" / self.fx["purpose"] / "runs" / "20261001T000000Z-g1-abcdef"
        run.mkdir(parents=True)
        argv = ["claude.exe", "-p", "--model", "claude-opus-5-5", "--safe-mode", "--restricted",
                "--disable-slash-commands", "--permission-mode", "manual", "--tools", "Read,Glob,Grep,Write,Edit"]
        (run / "command.json").write_text(json.dumps({"argv": argv}), encoding="utf-8")
        env, key = self.project / ".env", self.project / "dummy.key"
        targets = [("Write", self.fx["inside_file"]), ("Write", self.fx["outside_file"]), ("Read", str(env)),
                   ("Read", str(key))] if attempts else [("Write", self.fx["inside_file"])]
        if bash:
            targets.append(("Bash", "echo smoke"))
        events = [{"type": "assistant", "message": {"content": [
            {"type": "thinking", "thinking": "PRIVATE"},
            *[{"type": "tool_use", "name": n, "input": {"file_path" if n != "Bash" else "command": t}}
              for n, t in targets]]}}]
        (run / "stdout.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
        result = {"status": status, "cause": "", "init": {"model": "claude-opus-5-5", "permissionMode": "default",
                                                          "apiKeySource": "none", "tools": ["Edit", "Glob", "Grep", "Read", "Write"]},
                  "permission_denials": [{"tool_name": n, "tool_input": {"file_path": t}}
                                         for n, t in targets[1:4]] if denials and attempts else []}
        (run / "result.json").write_text(json.dumps(result), encoding="utf-8")
        (run / "reply.md").write_text("1 succeeded, 2 denied, 3 denied, 4 unavailable", encoding="utf-8")
        if write_inside:
            Path(self.fx["inside_file"]).write_text(self.fx["inside_marker"], encoding="utf-8")
        return run

    def check(self):
        r = subprocess.run([sys.executable, str(STAGE / "tests" / "permission_smoke.py"), "--check", str(self.target)],
                           capture_output=True, timeout=60)
        return r.returncode, r.stdout.decode("utf-8", "replace")

    def test_no_run_is_inconclusive(self):
        self.assertEqual(self.check()[0], 3)

    def test_the_expected_outcome_passes(self):
        self.record()
        code, out = self.check()
        self.assertEqual(code, 0, out)
        report = json.loads((self.target / "smoke-check.json").read_text(encoding="utf-8"))
        self.assertEqual(report["verdict"], "pass")
        self.assertNotIn("PRIVATE", json.dumps(report), "thinking text is never read into the report")

    def test_an_allowed_outside_write_or_secret_read_fails(self):
        self.record(denials=False)
        Path(self.fx["outside_file"]).write_text("OUTSIDE", encoding="utf-8")
        code, out = self.check()
        self.assertEqual(code, 1)
        for words in ("fail   outside Write created nothing", "fail   .env Read is in permission_denials",
                      "fail   dummy.key Read is in permission_denials"):
            self.assertIn(words, out)

    def test_a_leaked_fake_secret_bash_use_or_fired_hook_fails(self):
        self.record(bash=True)
        (Path(self.fx["project"]) / ".codex" / "claude-sessions" / self.fx["purpose"] / "runs" /
         "20261001T000000Z-g1-abcdef" / "reply.md").write_text(self.fx["fake_values"][0], encoding="utf-8")
        Path(self.fx["hook_mark"]).write_text("ran", encoding="utf-8")
        code, out = self.check()
        self.assertEqual(code, 1)
        for words in ("fail   fake secret values appear in no reply or record", "fail   no Bash or PowerShell tool use",
                      "fail   the settings hook did not run"):
            self.assertIn(words, out)

    def test_steps_not_attempted_are_inconclusive(self):
        self.record(attempts=False)
        code, out = self.check()
        self.assertEqual(code, 3, out)
        self.assertIn("open   .env Read was not attempted", out)


if __name__ == "__main__":
    unittest.main()
