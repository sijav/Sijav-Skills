#!/usr/bin/env python3
"""Sijav-Codex setup: validate this package, then (only when asked) add it with Codex's own plugin CLI.

    python tools/sijav_codex_setup.py                     validate; print the commands an install runs
    python tools/sijav_codex_setup.py --json              the same, as JSON
    python tools/sijav_codex_setup.py --install           validate, then:
        codex plugin marketplace add <package root> --json
        codex plugin add sijav-codex@<marketplace> --json
        codex plugin list --marketplace <marketplace> --available --json
    python tools/sijav_codex_setup.py --dashboard-deps [--dashboard-dir DIR]
        npm ci --omit=dev --prefix <skills/todo/dashboard, or DIR>   (locked dependencies only)

Options: --codex PATH, --npm PATH, --node PATH, --claude PATH (a .py path runs under this Python;
used by tests), --log FILE (append every command's full output, with command, folder, times and
exit status), --skip-tools (validate the package only; no tool is run, not even --version).

What it never does: write Codex configuration or hook trust (hooks are reviewed and trusted in
Codex's own /hooks screen), pass a bypass flag, start or arm a loop, start the dashboard, register
any folder but this package (its catalog lists only source "./"), publish, commit or push.

Validation (offline): Python 3.9+; the native manifest .codex-plugin/plugin.json (name, semantic
version, description, skills "./skills", hooks "./hooks/hooks.json") and its hook file, with no
root plugin.json (Codex 0.159.3 loads that as a portable Agent Plugin and drops its hooks);
.agents/plugins/marketplace.json (the catalog path Codex 0.159.2 reads) with exactly this
plugin from "./"; each skill's SKILL.md name/description and agents/openai.yaml (rules, dev-round
and loop must say allow_implicit_invocation: false); every helper a SKILL.md or hook names exists;
the example agent profiles in skills/agents/profiles pin their documented model and effort (Python
3.11+ parses them); no key, credential, .env, .claude or .codex state inside the package (an added
marketplace may be copied). Tool versions are reported: codex, node (todo.mjs needs 22.13+, the
dashboard ^22.16.0 or >=24.0.0), claude and typesafe-sdk; only a missing codex blocks --install.

Exit codes: 0 valid (and every requested command succeeded); 1 problems or a failed command; 2 usage.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parents[1]
NATIVE_MANIFEST = Path(".codex-plugin") / "plugin.json"  # the manifest Codex loads with its hooks
PORTABLE_MANIFEST = "plugin.json"  # must not exist at the root (see check_package)
MARKETPLACE = Path(".agents") / "plugins" / "marketplace.json"
OTHER_CATALOGS = (".agents/plugins/api_marketplace.json", ".claude-plugin/marketplace.json",
                  ".cursor-plugin/marketplace.json")
PLUGIN = "sijav-codex"
SKILLS = ("agents", "dev-round", "loop", "research", "roast", "rules", "search", "todo")
OWNER_INVOKED = ("dev-round", "loop", "rules")
TESTED_CODEX = "0.159.3"  # the CLI the native install and core loop proof ran on
NODE_MIN = (22, 13)  # todo.mjs (built-in node:sqlite)
# Example native-agent profiles (skills/agents/profiles): the model and effort each must pin.
PROFILES = {"sijav_sol": ("gpt-6.1-sol", "medium"), "sijav_luna_search": ("gpt-6-luna", "low"),
            "sijav_astra_research": ("gpt-6-astra", "high"), "sijav_astra_rnd": ("gpt-6-astra", "xhigh")}


def dashboard_node_ok(version: tuple[int, int, int]) -> bool:
    """The dashboard's engines range, ^22.16.0 || >=24.0.0."""
    return (version[0] == 22 and version[1] >= 16) or version[0] >= 24
# Named in prose only to say they are NOT used (the source plugin's external Codex runner).
NOT_IN_PACKAGE = {"codex_session.py", "copy_session.py"}
SECRET_NAMES = re.compile(r"(^\.env($|\.)|\.key$|\.pem$|\.p12$|\.pfx$|^auth\.json$|credential|secret|"
                          r"(^|[._-])tokens?(\.json|\.txt)?$)", re.I)
# Credential formats that are recognizable inside any text file of the package.
SECRET_TEXT = re.compile(rb"-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----|\bsk-[A-Za-z0-9_-]{24,}|"
                         rb"\bgh[pousr]_[A-Za-z0-9]{30,}|\bAKIA[0-9A-Z]{16}\b|\bxox[baprs]-[A-Za-z0-9-]{10,}")
TEXT_SUFFIXES = {".md", ".txt", ".json", ".py", ".mjs", ".js", ".toml", ".yaml", ".yml", ".html", ".css", ".ps1", ".sh"}
STATE_DIRS = {".claude", ".codex"}
BATCH_UNSAFE = set('"%!^&|<>\r\n')
COMMAND_TIMEOUT = 600.0  # seconds per command; --command-timeout changes it
VERSION_TIMEOUT = 30.0


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------- validation


def frontmatter(text: str) -> dict:
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n", text, re.S)
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        k, sep, v = line.partition(":")
        if sep and not line.startswith((" ", "\t")):
            out[k.strip()] = v.strip().strip('"')
    return out


def yaml_value(text: str, section: str, key: str) -> str | None:
    """A scalar under a top-level section of a simple YAML file (the documented openai.yaml shape)."""
    current = None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith((" ", "\t")):
            current = line.split(":", 1)[0].strip()
            continue
        k, sep, v = line.strip().partition(":")
        if current == section and sep and k.strip() == key:
            return v.strip().strip('"')
    return None


def references(text: str) -> set[str]:
    """Package files a SKILL.md names: backticked .py/.mjs paths and relative Markdown links."""
    refs = {r for r in re.findall(r"`([^`\s<>]+\.(?:py|mjs))`", text) if not r.startswith(("/", "."))}
    refs |= {r.split("#")[0] for r in re.findall(r"\]\(([^)\s]+)\)", text)
             if not re.match(r"^[a-z]+:", r) and not r.startswith("#")}
    return {r for r in refs if r}


def check_package(root: Path) -> tuple[list[str], list[str]]:
    """(problems, notes) for the package at `root`."""
    problems, notes = [], []
    if sys.version_info < (3, 9):
        problems.append(f"Python {sys.version.split()[0]} is older than 3.9")

    # Codex 0.159.3 reads a root plugin.json as a portable Agent Plugin, prefers it over
    # .codex-plugin/plugin.json and drops that format's hooks: an install of it listed zero handlers.
    if (root / PORTABLE_MANIFEST).exists():
        problems.append(f"{PORTABLE_MANIFEST} exists at the package root; Codex 0.159.3 would load the package as a"
                        f" portable Agent Plugin and discard its hooks. Use only {NATIVE_MANIFEST.as_posix()}")
    label = NATIVE_MANIFEST.as_posix()
    try:
        plugin = json.loads((root / NATIVE_MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        problems.append(f"{label} cannot be read: {exc}")
        plugin = {}
    if not isinstance(plugin, dict):
        problems.append(f"{label} is not a JSON object")
        plugin = {}
    if plugin:
        if plugin.get("name") != PLUGIN:
            problems.append(f"{label} name is {plugin.get('name')!r}, not {PLUGIN}")
        if not re.fullmatch(r"\d+\.\d+\.\d+([-+][\w.]+)?", str(plugin.get("version", ""))):
            problems.append(f"{label} version {plugin.get('version')!r} is not a semantic version")
        if not str(plugin.get("description") or "").strip():
            problems.append(f"{label} has no description")
        if plugin.get("skills") != "./skills" or not (root / "skills").is_dir():
            problems.append(f"{label} skills is {plugin.get('skills')!r}; it must be './skills', an existing folder")
        hooks = plugin.get("hooks")
        if hooks != "./hooks/hooks.json":
            problems.append(f"{label} hooks is {hooks!r}; it must be './hooks/hooks.json'")
        hp = (root / "hooks" / "hooks.json").resolve()
        if not hp.is_file():
            problems.append(f"{label} names hooks ./hooks/hooks.json, which does not exist")
        else:
            problems += hook_problems(root, hp)

    cat = root / MARKETPLACE
    try:
        market = json.loads(cat.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        problems.append(f"{MARKETPLACE.as_posix()} cannot be read: {exc}")
        market = {}
    if market:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", str(market.get("name", ""))):
            problems.append(f"the catalog name {market.get('name')!r} is not a kebab-case name")
        entries = market.get("plugins")
        if not isinstance(entries, list) or len(entries) != 1:
            problems.append("the catalog must list exactly one plugin: this package")
        else:
            e = entries[0]
            if e.get("name") != PLUGIN:
                problems.append(f"the catalog lists {e.get('name')!r}, not {PLUGIN}")
            if e.get("source") not in ({"source": "local", "path": "./"}, {"source": "local", "path": "."}):
                problems.append(f"the catalog source is {e.get('source')!r}; it must be the package itself:"
                                ' {"source": "local", "path": "./"} (no parent folder, absolute path or other source)')
            pol = e.get("policy") or {}
            if pol.get("installation", "AVAILABLE") not in ("AVAILABLE", "INSTALLED_BY_DEFAULT", "NOT_AVAILABLE"):
                problems.append(f"policy.installation {pol.get('installation')!r} is not a documented value")
            if pol.get("authentication", "ON_INSTALL") not in ("ON_INSTALL", "ON_FIRST_USE"):
                problems.append(f"policy.authentication {pol.get('authentication')!r} is not a documented value")
    for other in OTHER_CATALOGS:
        if (root / other).exists():
            problems.append(f"{other} exists; only {MARKETPLACE.as_posix()} may describe this package")

    names = {}
    for skill in SKILLS:
        d = root / "skills" / skill
        md = d / "SKILL.md"
        if not md.is_file():
            problems.append(f"skills/{skill}/SKILL.md is missing")
            continue
        text = md.read_text(encoding="utf-8")
        fm = frontmatter(text)
        name = fm.get("name", "")
        if name != f"sijav-codex-{skill}":
            problems.append(f"skills/{skill}/SKILL.md name is {name!r}, not sijav-codex-{skill}")
        if name in names:
            problems.append(f"skill name {name} is used twice")
        names[name] = skill
        if not fm.get("description"):
            problems.append(f"skills/{skill}/SKILL.md has no description")
        yml = d / "agents" / "openai.yaml"
        if not yml.is_file():
            problems.append(f"skills/{skill}/agents/openai.yaml is missing")
        else:
            y = yml.read_text(encoding="utf-8")
            implicit = yaml_value(y, "policy", "allow_implicit_invocation")
            want = "false" if skill in OWNER_INVOKED else "true"
            if (implicit or "true") != want:
                problems.append(f"skills/{skill}/agents/openai.yaml allow_implicit_invocation is {implicit!r};"
                                f" this skill needs {want}")
            for k in ("display_name", "short_description"):
                if not yaml_value(y, "interface", k):
                    problems.append(f"skills/{skill}/agents/openai.yaml has no interface.{k}")
        for ref in sorted(references(text)):
            if Path(ref).name in NOT_IN_PACKAGE:
                continue
            if not ((d / ref).exists() or (root / "skills" / ref).exists()):
                problems.append(f"skills/{skill}/SKILL.md names {ref}, which is not in the package")
    problems += profile_problems(root, notes)
    extra = sorted(p.name for p in (root / "skills").iterdir() if p.is_dir() and (p / "SKILL.md").exists()
                   and p.name not in SKILLS) if (root / "skills").is_dir() else []
    if extra:
        notes.append(f"skills not in the expected eight: {extra}")

    for p in root.rglob("*"):
        rel = p.relative_to(root)
        parts = set(rel.parts)
        if "node_modules" in parts:
            continue
        if p.is_symlink():
            problems.append(f"{rel.as_posix()} is a symbolic link; a copy of the package could pull in what it points to")
        elif p.is_dir() and p.name in STATE_DIRS:
            problems.append(f"{rel.as_posix()} is inside the package; it could carry private state into a copy")
        elif p.is_file() and SECRET_NAMES.search(p.name):
            problems.append(f"{rel.as_posix()} looks like a key or credential file; keep it outside the package")
        elif p.is_file() and p.suffix == ".pyc":
            notes.append(f"{rel.as_posix()} is compiled bytecode; remove it before packaging")
        elif p.is_file() and p.suffix.lower() in TEXT_SUFFIXES:
            try:
                if SECRET_TEXT.search(p.read_bytes()):
                    problems.append(f"{rel.as_posix()} contains text shaped like a private key or access token")
            except OSError as exc:
                problems.append(f"{rel.as_posix()} cannot be read for the secret check ({exc})")
    return problems, notes


def profile_problems(root: Path, notes: list[str]) -> list[str]:
    """The example custom-agent files: the documented required fields and the model/effort they pin."""
    d = root / "skills" / "agents" / "profiles"
    try:
        import tomllib
    except ImportError:
        notes.append("agent profile examples not parsed: tomllib needs Python 3.11+")
        return []
    out = []
    for name, (model, effort) in PROFILES.items():
        f = d / f"{name}.toml"
        try:
            data = tomllib.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            out.append(f"skills/agents/profiles/{name}.toml cannot be read as TOML: {exc}")
            continue
        for key in ("name", "description", "developer_instructions"):
            if not str(data.get(key) or "").strip():
                out.append(f"skills/agents/profiles/{name}.toml has no {key}")
        if data.get("name") != name:
            out.append(f"skills/agents/profiles/{name}.toml names itself {data.get('name')!r}")
        if (data.get("model"), data.get("model_reasoning_effort")) != (model, effort):
            out.append(f"skills/agents/profiles/{name}.toml pins {data.get('model')!r} at"
                       f" {data.get('model_reasoning_effort')!r}, not {model} at {effort}")
    return out


def hook_problems(root: Path, hooks_file: Path) -> list[str]:
    out = []
    try:
        hooks = json.loads(hooks_file.read_text(encoding="utf-8"))
    except ValueError as exc:
        return [f"{hooks_file.name} is not valid JSON: {exc}"]
    text = json.dumps(hooks)
    for ref in sorted(set(re.findall(r"\$\{PLUGIN_ROOT\}[\\/]+([\w./\\-]+\.py)", text))):
        rel = ref.replace("\\\\", "/").replace("\\", "/")
        if not (root / rel).is_file():
            out.append(f"the hooks name {rel}, which is not in the package")
    return out


# ------------------------------------------------------------- tools


def launcher(path: str) -> list[str]:
    return [sys.executable, path] if path.lower().endswith(".py") else [path]


def find_tool(explicit: str | None, name: str) -> str | None:
    if explicit:
        return explicit if Path(explicit).is_file() else None
    return shutil.which(name)


def stop_tree(proc: subprocess.Popen) -> None:
    """Stop a command and everything it started: a .cmd launcher's node child would otherwise keep
    the output pipes open after cmd.exe is killed."""
    if os.name == "nt":
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=30)
    else:
        with contextlib.suppress(OSError):
            os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(OSError):
        proc.kill()


def run(argv: list[str], log: Path | None, cwd: Path, timeout: float = None) -> tuple[int | str, str, str]:
    """Run one command; on timeout its whole process tree is stopped and its output read with a
    deadline, so a leftover descendant cannot hang the setup."""
    timeout = COMMAND_TIMEOUT if timeout is None else timeout
    if argv[0].lower().endswith((".cmd", ".bat")):
        for arg in argv[1:]:
            if set(arg) & BATCH_UNSAFE:
                return "refused", "", f"argument {arg!r} holds characters cmd.exe would reinterpret"
    started = now()
    try:
        proc = subprocess.Popen(argv, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                stdin=subprocess.DEVNULL, start_new_session=os.name != "nt")
    except OSError as exc:
        code, out_b, err_b = "oserror", b"", f"{type(exc).__name__}: {exc}".encode()
    else:
        # Own daemon reader threads: the output read so far survives any timeout, and a pipe that an
        # escaped descendant keeps open is abandoned to process exit instead of closed (closing it
        # would wait on the blocked reader, which is the hang this path exists to avoid).
        chunks = {"out": [], "err": []}

        def drain(pipe, into):
            with contextlib.suppress(OSError, ValueError):
                for chunk in iter(lambda: pipe.read1(65536), b""):
                    into.append(chunk)

        readers = [threading.Thread(target=drain, args=(proc.stdout, chunks["out"]), daemon=True),
                   threading.Thread(target=drain, args=(proc.stderr, chunks["err"]), daemon=True)]
        for t in readers:
            t.start()
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            stop_tree(proc)
            code = f"timeout after {timeout:g} s; its process tree was stopped"
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=10)
        deadline = time.monotonic() + 10
        for t in readers:
            t.join(max(0.0, deadline - time.monotonic()))
        out_b, err_b = b"".join(list(chunks["out"])), b"".join(list(chunks["err"]))
        if any(t.is_alive() for t in readers):
            err_b += (b"\n(the output pipe was still held open by a process outside the stopped tree; the output"
                      b" above is what was read until then)")
    out, err = out_b.decode("utf-8", "replace"), err_b.decode("utf-8", "replace")
    if log:
        with log.open("a", encoding="utf-8") as f:
            f.write(f"=== {started}\ncommand: {subprocess.list2cmdline(argv)}\ncwd: {cwd}\n--- stdout\n{out}\n"
                    f"--- stderr\n{err}\n--- exit: {code}  ended: {now()}\n")
    return code, out, err


def version_of(argv: list[str]) -> str | None:
    """A tool's version through the bounded run() (its tree is stopped after 30 s), or None."""
    code, out, err = run([*argv, "--version"], None, ROOT, timeout=VERSION_TIMEOUT)
    if code != 0:
        return None
    m = re.search(r"\d+\.\d+\.\d+", out + err)
    return m.group(0) if m else None


def tool_report(a) -> tuple[dict, list[str]]:
    notes, report = [], {}
    codex = find_tool(a.codex, "codex")
    report["codex"] = {"path": codex, "version": version_of(launcher(codex)) if codex else None}
    if not codex:
        notes.append("codex CLI not found: --install needs it (or pass --codex PATH)")
    elif report["codex"]["version"] != TESTED_CODEX:
        notes.append(f"codex reports {report['codex']['version']}; the catalog path was checked against"
                     f" {TESTED_CODEX}'s source")
    node = find_tool(a.node, "node")
    nv = version_of(launcher(node)) if node else None
    report["node"] = {"path": node, "version": nv}
    parts = tuple(int(x) for x in nv.split(".")[:3]) if nv else None
    if not parts or parts[:2] < NODE_MIN:
        notes.append(f"Node {NODE_MIN[0]}.{NODE_MIN[1]}+ not found (found {nv}): todo.mjs needs it;"
                     " todo.py works with Python alone")
    if not parts or not dashboard_node_ok(parts):
        notes.append(f"the dashboard needs Node ^22.16.0 or >=24.0.0 (found {nv})")
    claude = find_tool(a.claude, "claude")
    report["claude"] = {"path": claude, "version": version_of(launcher(claude)) if claude else None}
    if not claude:
        notes.append("claude CLI not found: claude_session.py cannot call Claude (set SIJAV_CLAUDE_BIN or install it)")
    try:
        import importlib.util
        report["typesafe_sdk"] = bool(importlib.util.find_spec("typesafe_sdk"))
    except (ImportError, ValueError):
        report["typesafe_sdk"] = False
    if not report["typesafe_sdk"]:
        notes.append(f"typesafe-sdk is not installed for {sys.executable}: Jev steps record 'unjudged'")
    return report, notes


def install(a, log: Path | None) -> int:
    codex = find_tool(a.codex, "codex")
    if not codex:
        print("INSTALL STOPPED: no codex CLI (pass --codex PATH).", file=sys.stderr)
        return 1
    market = json.loads((ROOT / MARKETPLACE).read_text(encoding="utf-8"))["name"]

    def step(argv: list[str], label: str):
        code, out, err = run(argv, log, ROOT)
        print(f"$ {subprocess.list2cmdline(argv)}\n{out}{err}--- exit {code}")
        if code != 0:
            print(f"INSTALL STOPPED: {label} ended with {code}; see its output above.", file=sys.stderr)
            return None
        try:
            data = json.loads(out)
        except ValueError:
            print(f"INSTALL STOPPED: {label} did not print JSON; see its output above.", file=sys.stderr)
            return None
        if not isinstance(data, dict):
            print(f"INSTALL STOPPED: {label} printed JSON that is not an object.", file=sys.stderr)
            return None
        return data

    added = step([*launcher(codex), "plugin", "marketplace", "add", str(ROOT), "--json"], "marketplace add")
    if added is None:
        return 1
    problems = contract_problems(added, MARKETPLACE_ADD_FIELDS)
    if not problems and added["marketplaceName"] != market:
        problems.append(f"Codex registered the catalog as {added['marketplaceName']!r}, not {market!r}")
    if not problems and not Path(added["installedRoot"]).is_dir():
        problems.append(f"installedRoot {added['installedRoot']} is not a folder")
    if problems:
        print("INSTALL STOPPED: marketplace add's JSON is not the verified contract:\n- " + "\n- ".join(problems),
              file=sys.stderr)
        return 1
    installed = step([*launcher(codex), "plugin", "add", f"{PLUGIN}@{market}", "--json"], "plugin add")
    if installed is None:
        return 1
    problems = contract_problems(installed, PLUGIN_ADD_FIELDS)
    if not problems:
        if (installed["name"], installed["marketplaceName"]) != (PLUGIN, market):
            problems.append(f"it installed {installed['name']!r} from {installed['marketplaceName']!r}, not {PLUGIN}"
                            f" from {market}")
        if not Path(installed["installedPath"]).is_dir():
            problems.append(f"installedPath {installed['installedPath']} is not a folder")
    if problems:
        print("INSTALL NOT VERIFIED: plugin add's JSON is not the verified contract:\n- " + "\n- ".join(problems),
              file=sys.stderr)
        return 1
    listed = step([*launcher(codex), "plugin", "list", "--marketplace", market, "--available", "--json"],
                  "plugin list")
    if listed is None:
        return 1
    entries = [p for p in plugin_entries(listed)
               if p.get("name") == PLUGIN or p.get("id") == f"{PLUGIN}@{market}"]
    if not any(p.get("installed") is True and p.get("enabled") is True for p in entries):
        seen = [{k: p.get(k) for k in ("id", "name", "installed", "enabled")} for p in entries]
        print(f"INSTALL NOT VERIFIED: plugin list does not show {PLUGIN}@{market} with installed true and enabled"
              f" true (found {seen or 'no entry'}).", file=sys.stderr)
        return 1
    print(f"Installed {PLUGIN}@{market} {installed['version']} at {installed['installedPath']}; plugin list shows"
          " it installed and enabled. Hooks are NOT trusted or enabled by this script: review them in Codex's"
          " /hooks screen only when the owner starts a loop. No loop was started.")
    return 0


# The JSON fields Codex 0.159.2's CLI prints, established by the root's native-CLI investigation
# (marketplace add: marketplaceName, installedRoot, alreadyAdded; plugin add: pluginId, name,
# marketplaceName, version, installedPath, authPolicy). plugin list entries are checked for the
# explicit booleans `installed` and `enabled` (PluginSummary in the app-server schema). Nothing is
# guessed: a missing or mistyped field stops the install with its name.
MARKETPLACE_ADD_FIELDS = {"marketplaceName": str, "installedRoot": str, "alreadyAdded": bool}
PLUGIN_ADD_FIELDS = {"pluginId": str, "name": str, "marketplaceName": str, "version": str, "installedPath": str,
                     "authPolicy": str}


def contract_problems(data: dict, fields: dict) -> list[str]:
    return [f"{k} is {'missing' if k not in data else type(data[k]).__name__ + ', not ' + t.__name__}"
            for k, t in fields.items() if not isinstance(data.get(k), t)]


def plugin_entries(value) -> list[dict]:
    """Every object in plugin list's JSON that describes a plugin (has a name or id)."""
    out = []
    if isinstance(value, dict):
        if "name" in value or "id" in value:
            out.append(value)
        for v in value.values():
            out += plugin_entries(v)
    elif isinstance(value, list):
        for v in value:
            out += plugin_entries(v)
    return out


def dashboard_deps(a, log: Path | None) -> int:
    d = Path(a.dashboard_dir).resolve() if a.dashboard_dir else ROOT / "skills" / "todo" / "dashboard"
    if not (d / "package.json").is_file() or not (d / "package-lock.json").is_file():
        print(f"DASHBOARD DEPENDENCIES NOT INSTALLED: {d} has no package.json and package-lock.json"
              " (the dashboard has not been copied in yet).", file=sys.stderr)
        return 1
    npm = find_tool(a.npm, "npm")
    if not npm:
        print("DASHBOARD DEPENDENCIES NOT INSTALLED: npm not found (pass --npm PATH).", file=sys.stderr)
        return 1
    argv = [*launcher(npm), "ci", "--omit=dev", "--prefix", str(d)]
    code, out, err = run(argv, log, d)
    print(f"$ {subprocess.list2cmdline(argv)}\n{out}{err}--- exit {code}")
    return 0 if code == 0 else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, allow_abbrev=False,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--install", action="store_true")
    ap.add_argument("--dashboard-deps", action="store_true")
    ap.add_argument("--dashboard-dir", default=None)
    ap.add_argument("--json", action="store_true")
    for tool in ("codex", "npm", "node", "claude"):
        ap.add_argument(f"--{tool}", default=None)
    ap.add_argument("--log", default=None)
    ap.add_argument("--skip-tools", action="store_true", help="validate the package without running any tool")
    ap.add_argument("--command-timeout", type=float, default=COMMAND_TIMEOUT,
                    help="seconds each install command may take before its process tree is stopped")
    a = ap.parse_args(argv)
    if not a.command_timeout > 0:
        ap.error("--command-timeout must be positive")
    globals()["COMMAND_TIMEOUT"] = a.command_timeout
    log = Path(a.log).resolve() if a.log else None
    problems, notes = check_package(ROOT)
    if a.skip_tools:
        if a.install or a.dashboard_deps:
            print("--skip-tools is for validation only; it cannot be combined with an install.", file=sys.stderr)
            return 2
        tools, tool_notes = {}, ["tool versions were not checked (--skip-tools)"]
    else:
        tools, tool_notes = tool_report(a)
    market = None
    with_name = ROOT / MARKETPLACE
    if with_name.is_file():
        try:
            market = json.loads(with_name.read_text(encoding="utf-8")).get("name")
        except ValueError:
            pass
    commands = [f'codex plugin marketplace add "{ROOT}" --json', f"codex plugin add {PLUGIN}@{market} --json",
                f"codex plugin list --marketplace {market} --available --json"]
    if a.json:
        print(json.dumps({"package": str(ROOT), "problems": problems, "notes": notes + tool_notes,
                          "tools": tools, "install_commands": commands}, indent=2))
    else:
        print(f"Sijav-Codex package: {ROOT}")
        print("Problems:" if problems else "Problems: none")
        for p in problems:
            print(f"  - {p}")
        for n in notes + tool_notes:
            print(f"  note: {n}")
        print("--install would run:")
        for c in commands:
            print(f"  {c}")
    if problems:
        if a.install or a.dashboard_deps:
            print("NOTHING INSTALLED: fix the problems above first.", file=sys.stderr)
        return 1
    code = 0
    if a.install:
        code = install(a, log) or code
    if a.dashboard_deps:
        code = dashboard_deps(a, log) or code
    return code


if __name__ == "__main__":
    sys.exit(main())
