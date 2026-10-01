"""tools/sijav_codex_setup.py on throwaway copies of this package, with fake codex/npm/node/claude
(tests/fixtures/fake_tool.py). Nothing is installed and no real CLI is run."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

STAGE = Path(__file__).resolve().parents[1]
FAKE = STAGE / "tests" / "fixtures" / "fake_tool.py"
IGNORE = shutil.ignore_patterns("validation", "node_modules", "__pycache__", "*.pyc", "proof-runs")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sijav setup "))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.pkg = self.tmp / "Sijav Codex"
        shutil.copytree(STAGE, self.pkg, ignore=IGNORE)
        dash = self.pkg / "skills" / "todo" / "dashboard"
        if not (dash / "README.md").exists():  # stands in for the dashboard root copies in later
            dash.mkdir(parents=True, exist_ok=True)
            (dash / "README.md").write_text("# dashboard\n", encoding="utf-8")
            (dash / "server.mjs").write_text("// stand-in\n", encoding="utf-8")
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        for tool in ("codex", "npm", "node", "claude"):
            shutil.copy2(FAKE, self.bin / f"{tool}.py")
        self.log = self.tmp / "tools.jsonl"

    def setup(self, *args, mode="ok", tools=True, **more_env):
        extra = []
        if tools:
            for tool in ("codex", "npm", "node", "claude"):
                extra += [f"--{tool}", str(self.bin / f"{tool}.py")]
        env = {k: v for k, v in os.environ.items() if not k.startswith("FAKE_TOOL")}
        env.update(FAKE_TOOL_LOG=str(self.log), FAKE_TOOL_MODE=mode, PYTHONDONTWRITEBYTECODE="1",
                   FAKE_TOOL_INSTALLED=str(self.tmp / "installed plugin"), **more_env)
        (self.tmp / "installed plugin").mkdir(exist_ok=True)
        r = subprocess.run([sys.executable, str(self.pkg / "tools" / "sijav_codex_setup.py"), *extra, *args],
                           cwd=str(self.tmp), capture_output=True, env=env, timeout=120)
        return (r.returncode, r.stdout.decode("utf-8").replace("\r\n", "\n"),
                r.stderr.decode("utf-8").replace("\r\n", "\n"))

    def calls(self, skip_versions=True):
        if not self.log.exists():
            return []
        out = [json.loads(x) for x in self.log.read_text(encoding="utf-8").splitlines()]
        return [c for c in out if not (skip_versions and c["argv"] == ["--version"])]

    def problems(self):
        code, out, err = self.setup("--json")
        return code, json.loads(out)["problems"]


class Validation(Base):
    def test_the_package_validates(self):
        code, problems = self.problems()
        self.assertEqual(problems, [])
        self.assertEqual(code, 0)
        self.assertEqual(self.calls(), [], "validation runs only --version queries")
        code, out, _ = self.setup()
        self.assertIn("Problems: none", out)
        self.assertIn(f'codex plugin marketplace add "{self.pkg.resolve()}" --json', out)
        self.assertIn("codex plugin add sijav-codex@sijav-codex-local --json", out)
        self.assertIn("codex plugin list --marketplace sijav-codex-local --available --json", out)

    def test_skip_tools_runs_nothing(self):
        code, out, err = self.setup("--json", "--skip-tools")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.calls(skip_versions=False), [])
        self.assertIn("tool versions were not checked (--skip-tools)", json.loads(out)["notes"])
        self.assertEqual(self.setup("--install", "--skip-tools")[0], 2)
        self.assertEqual(self.calls(skip_versions=False), [])

    def test_dashboard_references_are_reported_until_it_is_copied_in(self):
        shutil.rmtree(self.pkg / "skills" / "todo" / "dashboard")
        code, problems = self.problems()
        self.assertEqual(code, 1)
        self.assertTrue(problems)
        self.assertTrue(all("skills/todo/SKILL.md names dashboard/" in p for p in problems), problems)

    def test_catalog_must_be_this_package_only(self):
        cat = self.pkg / ".agents" / "plugins" / "marketplace.json"
        good = json.loads(cat.read_text(encoding="utf-8"))
        for source in ({"source": "local", "path": "../"}, {"source": "local", "path": "./.."},
                       {"source": "local", "path": "C:/work/Sijav-Skills"}, {"source": "url", "url": "https://x"}):
            bad = json.loads(json.dumps(good))
            bad["plugins"][0]["source"] = source
            cat.write_text(json.dumps(bad), encoding="utf-8")
            code, problems = self.problems()
            self.assertEqual(code, 1, source)
            self.assertTrue(any("must be the package itself" in p for p in problems), (source, problems))
        bad = json.loads(json.dumps(good))
        bad["plugins"].append({"name": "sijav-clauder", "source": {"source": "local", "path": "./"}})
        cat.write_text(json.dumps(bad), encoding="utf-8")
        self.assertTrue(any("exactly one plugin" in p for p in self.problems()[1]))
        cat.write_text(json.dumps(good), encoding="utf-8")
        (self.pkg / ".claude-plugin").mkdir()
        (self.pkg / ".claude-plugin" / "marketplace.json").write_text("{}", encoding="utf-8")
        self.assertTrue(any(".claude-plugin/marketplace.json exists" in p for p in self.problems()[1]))

    def test_native_manifest_is_required_and_a_root_portable_manifest_is_refused(self):
        """Codex 0.159.3 loaded the package with a root plugin.json as a portable
        Agent Plugin and listed zero hooks; only .codex-plugin/plugin.json may be used."""
        archive = self.pkg / "archive" / "portable-plugin.example.json"
        self.assertEqual(archive.read_bytes(), (STAGE / "archive" / "portable-plugin.example.json").read_bytes())
        native = self.pkg / ".codex-plugin" / "plugin.json"
        good = json.loads(native.read_text(encoding="utf-8"))
        self.assertEqual((good["skills"], good["hooks"]), ("./skills", "./hooks/hooks.json"))

        shutil.copy2(archive, self.pkg / "plugin.json")
        code, problems = self.problems()
        self.assertEqual(code, 1)
        self.assertTrue(any("plugin.json exists at the package root" in p and "discard its hooks" in p
                            for p in problems), problems)
        (self.pkg / "plugin.json").unlink()

        for change, words in (({"hooks": None}, "hooks is None"),
                              ({"skills": "./elsewhere"}, "skills is './elsewhere'"),
                              ({"name": "other"}, "name is 'other'"),
                              ({"description": ""}, "has no description")):
            bad = dict(good, **change)
            native.write_text(json.dumps(bad), encoding="utf-8")
            code, problems = self.problems()
            self.assertEqual(code, 1, change)
            self.assertTrue(any(p.startswith(".codex-plugin/plugin.json") and words in p for p in problems),
                            (change, problems))
        native.unlink()
        self.assertTrue(any(".codex-plugin/plugin.json cannot be read" in p for p in self.problems()[1]))
        native.write_text(json.dumps(good), encoding="utf-8")
        self.assertEqual(self.problems(), (0, []))
        self.assertEqual(archive.read_bytes(), (STAGE / "archive" / "portable-plugin.example.json").read_bytes())

    def test_private_state_and_keys_are_refused(self):
        (self.pkg / ".claude").mkdir()
        (self.pkg / "skills" / "roast" / "typesafe.key").write_text("k", encoding="utf-8")
        (self.pkg / ".env").write_text("TYPESAFE_API_KEY=k", encoding="utf-8")
        (self.pkg / "x" / ".codex" / "claude-sessions").mkdir(parents=True)
        (self.pkg / "skills" / "roast" / "notes.md").write_text(
            "pasted by mistake: sk-" + "a1B2" * 8 + "\n", encoding="utf-8")
        (self.pkg / "docs-token-limits.md").write_text("Jev's token limits.\n", encoding="utf-8")
        problems = " | ".join(self.problems()[1])
        for words in (".claude is inside the package", "skills/roast/typesafe.key looks like a key",
                      ".env looks like a key", "x/.codex is inside the package",
                      "skills/roast/notes.md contains text shaped like a private key or access token"):
            self.assertIn(words, problems)
        self.assertNotIn("docs-token-limits.md", problems, "a file about tokens is not a token file")

    def test_skill_metadata_and_helper_references(self):
        y = self.pkg / "skills" / "rules" / "agents" / "openai.yaml"
        y.write_text(y.read_text(encoding="utf-8").replace("false", "true"), encoding="utf-8")
        (self.pkg / "skills" / "search" / "agents" / "openai.yaml").unlink()
        (self.pkg / "skills" / "claude" / "claude_session.py").unlink()
        (self.pkg / "skills" / "loop" / "scripts" / "loop_hook.py").unlink()
        md = self.pkg / "skills" / "agents" / "SKILL.md"
        md.write_text(md.read_text(encoding="utf-8").replace("name: sijav-codex-agents", "name: agents"), encoding="utf-8")
        problems = " | ".join(self.problems()[1])
        for words in ("skills/rules/agents/openai.yaml allow_implicit_invocation is 'true'; this skill needs false",
                      "skills/search/agents/openai.yaml is missing",
                      "names claude/claude_session.py, which is not in the package",
                      "the hooks name skills/loop/scripts/loop_hook.py, which is not in the package",
                      "skills/agents/SKILL.md name is 'agents'"):
            self.assertIn(words, problems)


class Profiles(Base):
    def test_example_agent_profiles_pin_the_requested_model_and_effort(self):
        if sys.version_info < (3, 11):
            self.skipTest("tomllib needs Python 3.11+")
        f = self.pkg / "skills" / "agents" / "profiles" / "sijav_astra_research.toml"
        f.write_text(f.read_text(encoding="utf-8").replace('model_reasoning_effort = "high"',
                                                           'model_reasoning_effort = "medium"'), encoding="utf-8")
        (self.pkg / "skills" / "agents" / "profiles" / "sijav_sol.toml").unlink()
        problems = " | ".join(self.problems()[1])
        self.assertIn("sijav_astra_research.toml pins 'gpt-6-astra' at 'medium', not gpt-6-astra at high", problems)
        self.assertIn("sijav_sol.toml cannot be read", problems)

    def test_node_ranges(self):
        sys.path.insert(0, str(self.pkg / "tools"))
        try:
            import sijav_codex_setup as setup
        finally:
            sys.path.pop(0)
        self.assertEqual([setup.dashboard_node_ok(v) for v in ((22, 13, 0), (22, 16, 0), (23, 9, 0), (24, 0, 0))],
                         [False, True, False, True])


class Install(Base):
    def test_install_uses_only_the_native_plugin_commands(self):
        log = self.tmp / "setup log.txt"
        code, out, err = self.setup("--install", "--log", str(log))
        self.assertEqual(code, 0, err + out)
        root = str(self.pkg.resolve())
        self.assertEqual([c["argv"] for c in self.calls()], [
            ["plugin", "marketplace", "add", root, "--json"],
            ["plugin", "add", "sijav-codex@sijav-codex-local", "--json"],
            ["plugin", "list", "--marketplace", "sijav-codex-local", "--available", "--json"]])
        self.assertIn("Hooks are NOT trusted or enabled by this script", out)
        self.assertIn("Installed sijav-codex@sijav-codex-local 0.1.0", out)
        self.assertIn("plugin list shows it installed and enabled", out)
        text = log.read_text(encoding="utf-8")
        self.assertEqual(text.count("=== "), 3)
        self.assertIn("--- exit: 0", text)
        self.assertIn('"marketplaceName": "sijav-codex-local"', text)

    def test_install_stops_on_problems_or_surprises(self):
        (self.pkg / ".claude").mkdir()
        code, _o, err = self.setup("--install")
        self.assertEqual(code, 1)
        self.assertIn("NOTHING INSTALLED", err)
        self.assertEqual(self.calls(), [])
        (self.pkg / ".claude").rmdir()
        for mode, stop, reached in (
                ("other_name", "registered the catalog as 'someone-else'", "marketplace"),
                ("no_name_field", "marketplaceName is missing", "marketplace"),
                ("not_json", "did not print JSON", "marketplace"),
                ("add_fails", "plugin add ended with 1", "add"),
                ("add_no_path", "INSTALL NOT VERIFIED: plugin add's JSON is not the verified contract:\n- installedPath"
                                " is missing", "add"),
                ("list_disabled", "does not show sijav-codex@sijav-codex-local with installed true and enabled true",
                 "list"),
                ("list_missing", "(found no entry)", "list")):
            with self.subTest(mode):
                self.log.unlink(missing_ok=True)
                code, out, err = self.setup("--install", mode=mode)
                self.assertEqual(code, 1, mode)
                self.assertIn(stop, err)
                self.assertNotIn("Installed sijav-codex", out, "success is never claimed")
                argvs = [c["argv"][:2] for c in self.calls()]
                self.assertEqual(["plugin", "add"] in argvs, reached in ("add", "list"), mode)
                self.assertEqual(["plugin", "list"] in argvs, reached == "list", mode)

    def test_an_escaped_descendant_holding_the_pipe_cannot_hang_setup(self):
        pid_file = self.tmp / "escaped.pid"
        import time
        sys.path.insert(0, str(STAGE / "skills" / "claude"))
        import claude_session

        def kill_escaped():
            if pid_file.exists() and claude_session.pid_alive(int(pid_file.read_text())):
                subprocess.run(["taskkill", "/F", "/PID", pid_file.read_text()] if os.name == "nt"
                               else ["kill", "-9", pid_file.read_text()], capture_output=True)

        self.addCleanup(kill_escaped)
        started = time.monotonic()
        code, out, err = self.setup("--install", "--command-timeout", "3", mode="hang_escaped",
                                    FAKE_TOOL_CHILD_PID=str(pid_file))
        self.assertLess(time.monotonic() - started, 60, "not the 120 s the escaped process sleeps")
        self.assertEqual(code, 1)
        self.assertIn("partial output before the hang", out, "output read before the timeout is kept")
        self.assertIn("timeout after 3 s", out)
        if os.name == "nt":
            self.assertIn("still held open by a process outside the stopped tree", out)

    def test_a_hung_command_is_stopped_with_its_process_tree(self):
        pid_file = self.tmp / "child.pid"
        import time
        started = time.monotonic()
        code, out, err = self.setup("--install", "--command-timeout", "3", mode="hang",
                                    FAKE_TOOL_CHILD_PID=str(pid_file))
        self.assertLess(time.monotonic() - started, 60, "not the 120 s the child sleeps")
        self.assertEqual(code, 1)
        self.assertIn("timeout after 3 s; its process tree was stopped", out)
        child = int(pid_file.read_text())
        sys.path.insert(0, str(STAGE / "skills" / "claude"))
        import claude_session
        deadline = time.monotonic() + 10
        while claude_session.pid_alive(child) and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertFalse(claude_session.pid_alive(child), "the child holding the pipe was stopped too")

    def test_no_codex_means_no_install(self):
        code, out, err = self.setup("--install", "--codex", str(self.tmp / "no-such-codex.exe"))
        self.assertEqual(code, 1)
        self.assertIn("no codex CLI", err)
        self.assertIn("codex CLI not found", out)
        self.assertEqual(self.calls(), [])


class Dashboard(Base):
    def test_locked_dependencies_only_when_asked_and_present(self):
        dash = self.pkg / "skills" / "todo" / "dashboard"
        (dash / "package-lock.json").unlink(missing_ok=True)  # an unlocked install is never run
        code, _o, err = self.setup("--dashboard-deps")
        self.assertEqual(code, 1)
        self.assertIn("no package.json and package-lock.json", err)
        self.assertEqual(self.calls(), [])
        if not (dash / "package.json").exists():
            (dash / "package.json").write_text("{}", encoding="utf-8")
        (dash / "package-lock.json").write_text("{}", encoding="utf-8")
        code, out, err = self.setup("--dashboard-deps")
        self.assertEqual(code, 0, err)
        self.assertEqual([c["argv"] for c in self.calls()], [["ci", "--omit=dev", "--prefix", str(dash.resolve())]])
        self.assertEqual([c["tool"] for c in self.calls()], ["npm"])


if __name__ == "__main__":
    unittest.main()
