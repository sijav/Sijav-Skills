#!/usr/bin/env python3
"""Bounded live proof of the Sijav loop hooks in a real Codex runtime.

Use only an empty, disposable folder under the system temp directory. The
unit tests never run this.

    python tests/native_proof.py --prepare FIXTURE
        Writes FIXTURE/.codex/hooks.json -- the plugin's hook commands (the
        shell-neutral `python -c <bootstrap> <script> <event>` form) with
        ${PLUGIN_ROOT} and the interpreter replaced by concrete absolute
        paths, the interpreter unquoted -- and the harness marker. Re-preparing
        a fixture first copies its old marker and hooks.json into proof-runs/.
        A fixture holding anything else, such as an owner's .stop, is refused
        and left untouched. Starts no process, calls no model.

    python tests/native_proof.py --prepare FIXTURE --installed-plugin-root ROOT [--plugin-id ID]
        The installed-plugin form. ROOT is the plugin root Codex installed
        (its cache copy); it must hold exactly the staged, reviewed package
        (native .codex-plugin/plugin.json, hooks.json and scripts
        byte-identical) and no root plugin.json, which Codex 0.159.3 would
        load as a portable Agent Plugin without hooks. Writes only the marker:
        the fixture gets no project hooks.json, so the only loop hooks are
        the plugin's, with ${PLUGIN_ROOT} expanded by Codex itself.

    python tests/native_proof.py --check FIXTURE [--codex PATH] [--expect-codex-version V]
        Starts `codex app-server`, initializes, calls hooks/list and stops.
        No thread, no turn, no model call.

    python tests/native_proof.py --run FIXTURE [--model M] [--effort E] [--codex PATH]
                                               [--expect-codex-version V]
        Repeats the hooks/list check and calls no model unless both handlers
        of the fixture's form are trusted, enabled and reported with this
        platform's command variant, and no foreign Stop/SessionStart handler
        is loaded. Then runs three fixtures, each on its own new root thread,
        armed for that thread id through the real sijav_loop.py CLI:
          A  three Stop continuations, then the exact promise -> complete
          B  identical replies that never complete -> exhausted at cap 2
          C  text history, then a law with a fresh marker is written outside
             the fixture, the loop is armed, thread/compact/start runs, and
             the next reply to a direct question for the marker must return
             it: only the SessionStart compact hook can have supplied it.
             Any tool use fails the run.
        Afterwards a fixture state left active or paused is stopped through
        the CLI, so no fixture stays armed.

Each run proves one form: the project-hooks form (.codex/hooks.json with
concrete paths, the default) or the installed-plugin form. Only manual
compaction is exercised, not automatic mid-turn compaction.

Every frame exchanged with the app-server, its stderr, the process record,
CLI calls, adapter events, state snapshots and a summary go to
FIXTURE/proof-runs/<stamp>/. Secrets are redacted before writing; auth files
are never read. Codex identity variables are removed from the environment of
every child process; nothing else is. Trust is never written or bypassed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone

sys.dont_write_bytecode = True
STAGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(STAGE, "skills", "loop", "scripts")
HOOK_SCRIPT = os.path.join(SCRIPTS, "loop_hook.py")
CLI_SCRIPT = os.path.join(SCRIPTS, "sijav_loop.py")
PLUGIN_HOOKS = os.path.join(STAGE, "hooks", "hooks.json")
sys.path.insert(0, SCRIPTS)
import sijav_loop  # noqa: E402

HARNESS = "sijav-native-proof/3"
MARKER = ".sijav-native-proof.json"
CONTROLLED = {MARKER, ".codex", "proof-runs"}
CODEX_CONTROLLED = {"hooks.json", "sijav-loop"}
DEFAULT_MODEL = "gpt-6-luna"
DEFAULT_EFFORT = "low"
REQUEST_TIMEOUT = 120.0
TURN_TIMEOUT = 600.0
RUN_DEADLINE = 2400.0
CAPS = {"A": 5, "B": 2, "C": 2}
EVENTS = {"stop": ("Stop", "stop"), "sessionStart": ("SessionStart", "session-start")}
MATCHERS = {"stop": None, "sessionStart": "compact"}
# Items a text-only fixture turn may contain. Anything else -- a command,
# a file read or change, an MCP or dynamic tool, a web search -- fails it.
ALLOWED_ITEMS = {"userMessage", "hookPrompt", "agentMessage", "reasoning", "contextCompaction"}
IDENTITY_ENV = re.compile(r"^CODEX_[A-Z0-9_]*(THREAD|SESSION|TURN)_ID$")
VARIANT = "commandWindows" if os.name == "nt" else "command"
OTHER_VARIANT = "command" if os.name == "nt" else "commandWindows"
FORM_PROJECT = ("project hooks: FIXTURE/.codex/hooks.json with concrete absolute paths. The installed-plugin"
                " form is proved only by a separate run with --installed-plugin-root.")
FORM_PLUGIN = ("installed plugin: hooks loaded by Codex from the installed package's hooks/hooks.json with"
               " ${PLUGIN_ROOT} expanded by Codex; the fixture has no project hooks.json.")
NATIVE_MANIFEST = ".codex-plugin/plugin.json"
# Codex 0.159.3 reads a root plugin.json as a portable Agent Plugin, prefers it
# over .codex-plugin/plugin.json even with an overlay, and drops that format's
# hooks (core-plugins/src/plugin_namespace.rs 38-57, manifest.rs 153-179,
# loader.rs 894-904, as investigated by the root). The package must therefore
# have no root plugin.json, and its hooks come from the native manifest.
PORTABLE_MANIFEST = "plugin.json"
# The native fields this package uses, nothing more: name, version and
# description identify it; skills and hooks are paths relative to the root.
NATIVE_MANIFEST_FIELDS = {"name": "sijav-codex", "version": "0.1.0", "skills": "./skills",
                          "hooks": "./hooks/hooks.json"}
PACKAGE_FILES = (NATIVE_MANIFEST, "hooks/hooks.json", "skills/loop/scripts/loop_hook.py",
                 "skills/loop/scripts/sijav_loop.py")


class ProofError(Exception):
    """The proof cannot continue; the message says why."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


same_path = sijav_loop.same_path
inside = sijav_loop.inside


def child_env() -> tuple[dict, list[str]]:
    """This process's environment without Codex identity variables, and the
    names removed. Values are never read; auth configuration is untouched."""
    removed = sorted(k for k in os.environ if IDENTITY_ENV.match(k.upper()))
    return {k: v for k, v in os.environ.items() if k not in removed}, removed


# ------------------------------------------------------------------ redaction

_SECRET_KEY = re.compile(
    r"^(access_?token|refresh_?token|id_?token|api_?key|authorization|cookie|"
    r"set-cookie|password|client_?secret|email)$|secret|password|credential",
    re.I,
)
_SECRET_TEXT = re.compile(
    r"(Bearer\s+[A-Za-z0-9._~+/=-]{8,}|sk-[A-Za-z0-9_-]{8,}|"
    r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+)"
)


def redact(value):
    """A copy with credential-shaped keys and strings replaced."""
    if isinstance(value, dict):
        return {k: "[redacted]" if _SECRET_KEY.search(str(k)) else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return _SECRET_TEXT.sub("[redacted]", value)
    return value


# -------------------------------------------------------------------- fixture


def fixture_hooks(python: str, root: str = STAGE) -> dict:
    """The plugin's hooks.json with the interpreter (the first token) and
    ${PLUGIN_ROOT} replaced by concrete paths. Everything else -- the
    launcher bootstrap, matcher, timeout, context limit -- stays the plugin's.
    The interpreter is not quoted, because a quoted first token is a parse
    error in PowerShell, the hook shell Codex used on Windows; prepare()
    refuses an interpreter path that would need quoting. `command` gets
    forward slashes and `commandWindows` backslashes, so the two are
    equivalent but hooks/list shows which one Codex selected."""
    with open(PLUGIN_HOOKS, encoding="utf-8") as f:
        spec = json.load(f)
    for groups in spec["hooks"].values():
        for group in groups:
            for handler in group["hooks"]:
                for key, convert in (("command", lambda p: p.replace("\\", "/")),
                                     ("commandWindows", lambda p: p.replace("/", "\\"))):
                    rest = handler[key].split(" ", 1)[1]
                    handler[key] = f"{convert(python)} " + rest.replace("${PLUGIN_ROOT}", convert(root))
    return spec


_SHELL_SPECIAL = set(" \t\"'$`%;&|<>()^!,=@{}[]#*?~")


def interpreter_problem(python: str) -> str | None:
    """Why `python` cannot be the unquoted first token of a hook command."""
    if not os.path.isabs(python) or not os.path.isfile(python):
        return f"the interpreter {python} must be an existing absolute path"
    special = sorted(set(python) & _SHELL_SPECIAL)
    if special:
        return (f"the interpreter path {python} contains {''.join(special)!r}; it must be usable unquoted"
                " in PowerShell, cmd and sh (choose an interpreter path without spaces or shell characters)")
    return None


def handler_of(spec: dict, event: str) -> dict:
    return spec["hooks"][EVENTS[event][0]][0]["hooks"][0]


def _unsafe_path(path: str) -> bool:
    return any(c in path for c in '"$`%\r\n')


def fixture_problem(fixture: str, prepared: bool, caller_cwd: str | None = None,
                    outside_temp_ok: bool = False) -> str | None:
    """Why `fixture` must not be used, or None."""
    caller_cwd = os.path.abspath(caller_cwd or os.getcwd())
    if not os.path.isdir(fixture):
        return f"{fixture} is not an existing directory"
    if _unsafe_path(fixture):
        return f"{fixture} contains a quote, $, %, backtick or newline; choose a plain path"
    if not outside_temp_ok and not (inside(fixture, tempfile.gettempdir())
                                    and not same_path(fixture, tempfile.gettempdir())):
        return f"{fixture} is not below the system temp directory {tempfile.gettempdir()}"
    for name, other in (("the staging tree", STAGE), ("the caller's working directory", caller_cwd)):
        if inside(fixture, other) or inside(other, fixture):
            return f"{fixture} overlaps {name} {other}; use a separate disposable folder"
    current = os.path.abspath(fixture)
    while True:
        if os.path.exists(os.path.join(current, ".git")):
            return f"{current} is a git repository; the fixture must not be in a repository or source tree"
        if current != os.path.abspath(fixture) and os.path.lexists(sijav_loop.state_path(current)):
            return (f"{current} holds Sijav loop state; hooks in the fixture would find it while the"
                    " fixture is unarmed, so use a folder outside that project")
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    entries = set(os.listdir(fixture))
    if not entries:
        return "the fixture has not been prepared (run --prepare first)" if prepared else None
    if MARKER not in entries:
        return f"{fixture} is not empty and is not a prepared fixture: {sorted(entries)}"
    foreign = entries - CONTROLLED
    if foreign:
        return f"{fixture} holds files the harness does not own: {sorted(foreign)}"
    codex_dir = os.path.join(fixture, ".codex")
    if os.path.isdir(codex_dir):
        foreign = set(os.listdir(codex_dir)) - CODEX_CONTROLLED
        if foreign:
            return f"{codex_dir} holds files the harness does not own: {sorted(foreign)}"
    return None


def read_marker(fixture: str) -> dict:
    with open(os.path.join(fixture, MARKER), encoding="utf-8") as f:
        marker = json.load(f)
    if marker.get("harness") != HARNESS:
        raise ProofError(
            f"{MARKER} was written by {marker.get('harness')}, not {HARNESS}; run --prepare again in this"
            " fixture (earlier records are kept) and review the changed handlers in /hooks"
        )
    return marker


def file_sha256(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def package_digest(root: str) -> dict:
    return {rel: file_sha256(os.path.join(root, *rel.split("/"))) for rel in PACKAGE_FILES}


def native_manifest_problem(root: str) -> str | None:
    """Why the package at `root` would not load its hooks as a native Codex
    plugin, or None."""
    if os.path.lexists(os.path.join(root, PORTABLE_MANIFEST)):
        return (f"{os.path.join(root, PORTABLE_MANIFEST)} exists: Codex 0.159.3 loads a root plugin.json as a"
                " portable Agent Plugin, ahead of .codex-plugin/plugin.json, and discards that format's hooks;"
                " remove it so the native manifest is used")
    path = os.path.join(root, *NATIVE_MANIFEST.split("/"))
    try:
        with open(path, encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, ValueError) as exc:
        return f"the native manifest {path} cannot be read: {exc}"
    if not isinstance(manifest, dict):
        return f"the native manifest {path} is not a JSON object"
    for key, expected in NATIVE_MANIFEST_FIELDS.items():
        if manifest.get(key) != expected:
            return f"the native manifest {path} has {key} {manifest.get(key)!r}, expected {expected!r}"
    if not isinstance(manifest.get("description"), str) or not manifest["description"].strip():
        return f"the native manifest {path} has no description"
    extra = sorted(set(manifest) - set(NATIVE_MANIFEST_FIELDS) - {"description"})
    if extra:
        return f"the native manifest {path} has fields this package does not define: {extra}"
    for key in ("skills", "hooks"):
        target = os.path.join(root, *manifest[key][2:].split("/"))
        if not (os.path.isdir(target) if key == "skills" else os.path.isfile(target)):
            return f"the native manifest's {key} path {manifest[key]} does not exist under {root}"
    return None


def installed_package_problem(root: str) -> str | None:
    """Why `root` is not an installed copy of exactly the staged, reviewed
    package, or None."""
    if not os.path.isabs(root) or not os.path.isdir(root):
        return f"the installed plugin root {root} is not an existing absolute directory"
    if _unsafe_path(root):
        return f"the installed plugin root {root} contains a quote, $, %, backtick or newline"
    problem = native_manifest_problem(root)
    if problem:
        return problem
    for rel in PACKAGE_FILES:
        if not os.path.isfile(os.path.join(root, *rel.split("/"))):
            return f"the installed package lacks {rel}"
    installed, staged = package_digest(root), package_digest(STAGE)
    differing = [rel for rel in PACKAGE_FILES if installed[rel] != staged[rel]]
    if differing:
        return (f"the installed package at {root} differs from the staged, reviewed package in {differing};"
                " install the reviewed package again")
    return None


class ProjectForm:
    """Hooks from FIXTURE/.codex/hooks.json with concrete paths."""

    name = "project"
    description = FORM_PROJECT

    def __init__(self, python: str, fixture: str):
        self.python = python
        self.hooks_path = os.path.join(fixture, ".codex", "hooks.json")
        self.spec = fixture_hooks(python)
        self.cli_script = CLI_SCRIPT

    def handler(self, event: str) -> dict:
        return handler_of(self.spec, event)

    def display(self, event: str, variant: str = VARIANT) -> str:
        return self.handler(event)[variant]

    def matches(self, command, event: str, variant: str) -> bool:
        return command == self.display(event, variant)

    def owns_hook(self, hook: dict) -> bool:
        source = str(hook.get("sourcePath") or "")
        return bool(source) and same_path(source, self.hooks_path)

    owns_run = owns_hook

    def record(self) -> dict:
        return {"form": self.name, "hooks_path": self.hooks_path, "python": self.python}


class PluginForm:
    """Hooks Codex loads from the installed plugin, ${PLUGIN_ROOT} expanded
    by Codex. A handler is ours only when Codex reports it as a plugin hook
    whose source file lies under the installed root (and, when pinned, with
    that pluginId), and its command is the reviewed template with the
    placeholder replaced by that same root."""

    name = "plugin"
    description = FORM_PLUGIN

    def __init__(self, root: str, plugin_id: str | None = None):
        self.root = os.path.abspath(root)
        self.plugin_id = plugin_id
        with open(os.path.join(self.root, "hooks", "hooks.json"), encoding="utf-8") as f:
            self.spec = json.load(f)
        self.cli_script = os.path.join(self.root, "skills", "loop", "scripts", "sijav_loop.py")

    def handler(self, event: str) -> dict:
        return handler_of(self.spec, event)

    def display(self, event: str, variant: str = VARIANT) -> str:
        return self.handler(event)[variant].replace("${PLUGIN_ROOT}", self.root)

    def matches(self, command, event: str, variant: str) -> bool:
        template = self.handler(event)[variant]
        pre, placeholder, post = template.partition("${PLUGIN_ROOT}")
        if not placeholder or not isinstance(command, str):
            return command == template
        if len(command) <= len(pre) + len(post) or not (command.startswith(pre) and command.endswith(post)):
            return False
        expanded = command[len(pre):len(command) - len(post)]
        return "${" not in expanded and same_path(expanded, self.root)

    def owns_run(self, run: dict) -> bool:
        return run.get("source") == "plugin" and inside(str(run.get("sourcePath") or ""), self.root)

    def owns_hook(self, hook: dict) -> bool:
        return self.owns_run(hook) and (self.plugin_id is None or hook.get("pluginId") == self.plugin_id)

    def record(self) -> dict:
        return {"form": self.name, "plugin_root": self.root, "plugin_id": self.plugin_id}


def form_of(marker: dict, fixture: str):
    if marker.get("form", "project") == "plugin":
        return PluginForm(marker["plugin_root"], marker.get("plugin_id"))
    return ProjectForm(marker["python"], fixture)


def trust_instructions(fixture: str, form) -> str:
    where = ("the installed sijav-codex plugin" if form.name == "plugin"
             else os.path.join(fixture, ".codex", "hooks.json"))
    lines = [
        "Native review is required before any model call. In a real terminal:",
        f'  cd "{fixture}"',
        "  codex",
        "  (if Codex asks whether to trust this folder, answer through its normal prompt)",
        "  /hooks",
        f"  Events -> Stop -> select the handler from {where} whose command is",
        f"      {form.display('stop')}",
        "    inspect it, press t to trust that handler, and enable it if it is disabled.",
        f"  Events -> SessionStart -> select the handler from {where} with matcher compact and command",
        f"      {form.display('sessionStart')}",
        "    inspect it, press t, and enable it if needed.",
        "  Never press t on the Events list itself: that trusts every hook.",
        "  A changed definition shows as modified and needs this review again.",
    ]
    if form.name == "plugin":
        lines += ["  The displayed path is the installed root as given to --prepare; Codex's own expansion",
                  "  of ${PLUGIN_ROOT} must name the same directory, which --check verifies.",
                  "  While the plugin is enabled its hooks run in every Codex session; unarmed sessions stop normally."]
    lines += ["  Leave Codex, then verify without any model call (PowerShell):",
              f'  & "{sys.executable}" "{os.path.abspath(__file__)}" --check "{fixture}"']
    return "\n".join(lines)


def prepare(fixture: str, python: str, outside_temp_ok: bool = False,
            plugin_root: str | None = None, plugin_id: str | None = None) -> int:
    fixture = os.path.abspath(fixture)
    problem = fixture_problem(fixture, prepared=False, outside_temp_ok=outside_temp_ok)
    if problem:
        raise ProofError(f"refusing to prepare: {problem}")
    if plugin_root:
        return prepare_plugin(fixture, python, outside_temp_ok, os.path.abspath(plugin_root), plugin_id)
    problem = interpreter_problem(python)
    if problem:
        raise ProofError(f"refusing to prepare: {problem}")
    if not os.path.isfile(HOOK_SCRIPT) or _unsafe_path(HOOK_SCRIPT):
        raise ProofError(f"{HOOK_SCRIPT} must be an existing absolute path without quotes, $, % or backticks")
    archived = archive_preparation(fixture)
    os.makedirs(os.path.join(fixture, ".codex"), exist_ok=True)
    spec = fixture_hooks(python)
    sijav_loop.write_json_atomic(os.path.join(fixture, ".codex", "hooks.json"), spec)
    marker = {
        "harness": HARNESS,
        "form": "project",
        "prepared_at": now(),
        "fixture": fixture,
        "stage": STAGE,
        "python": python,
        "hook_script": HOOK_SCRIPT,
        "hooks_sha256": hashlib.sha256(json.dumps(spec, sort_keys=True).encode("utf-8")).hexdigest(),
        "outside_temp_ok": outside_temp_ok,
    }
    sijav_loop.write_json_atomic(os.path.join(fixture, MARKER), marker)
    print(f"Prepared {fixture} (project-hooks form)")
    if archived:
        print(f"Kept the previous marker and hook definitions in {archived}")
    print(f"Wrote {os.path.join(fixture, '.codex', 'hooks.json')}")
    print(f"Launcher dependencies: Codex's hook shell (PowerShell -Command, cmd /C or sh -lc), {python}"
          f" usable unquoted, and {HOOK_SCRIPT} with sijav_loop.py beside it.")
    print(trust_instructions(fixture, ProjectForm(python, fixture)))
    return 0


def prepare_plugin(fixture: str, python: str, outside_temp_ok: bool, root: str, plugin_id: str | None) -> int:
    """The installed-plugin form: verify the installed package is the
    reviewed one and write only the marker. A fixture that already has a
    project hooks.json is refused, since those hooks would run too."""
    if os.path.lexists(os.path.join(fixture, ".codex", "hooks.json")):
        raise ProofError("refusing to prepare: the installed-plugin form needs a fixture without"
                         " .codex/hooks.json; use a new empty fixture")
    problem = installed_package_problem(root)
    if problem:
        raise ProofError(f"refusing to prepare: {problem}")
    if not os.path.isabs(python) or not os.path.isfile(python):
        raise ProofError(f"refusing to prepare: the interpreter {python} for CLI calls must be an existing absolute path")
    archived = archive_preparation(fixture)
    marker = {
        "harness": HARNESS,
        "form": "plugin",
        "prepared_at": now(),
        "fixture": fixture,
        "stage": STAGE,
        "python": python,
        "plugin_root": root,
        "plugin_id": plugin_id,
        "package_sha256": package_digest(root),
        "outside_temp_ok": outside_temp_ok,
    }
    sijav_loop.write_json_atomic(os.path.join(fixture, MARKER), marker)
    print(f"Prepared {fixture} (installed-plugin form; no project hooks were written)")
    if archived:
        print(f"Kept the previous marker in {archived}")
    print(f"The installed package at {root} matches the staged, reviewed package.")
    print("Launcher dependencies: Codex's hook shell, `python` on the hook environment's PATH, and the"
          " installed loop_hook.py with sijav_loop.py beside it.")
    print(trust_instructions(fixture, PluginForm(root, plugin_id)))
    return 0


def archive_preparation(fixture: str) -> str | None:
    """Copy an existing marker and hooks.json into proof-runs before they are
    replaced, so an earlier run's records still show what was trusted."""
    sources = [p for p in (os.path.join(fixture, MARKER), os.path.join(fixture, ".codex", "hooks.json"))
               if os.path.isfile(p)]
    if not sources:
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = os.path.join(fixture, "proof-runs", f"{stamp}-superseded-preparation")
    os.makedirs(target)
    for source in sources:
        shutil.copy2(source, os.path.join(target, os.path.basename(source)))
    return target


def check_fixture_files(fixture: str) -> dict:
    marker = read_marker(fixture) if os.path.exists(os.path.join(fixture, MARKER)) else {}
    problem = fixture_problem(fixture, prepared=True, outside_temp_ok=bool(marker.get("outside_temp_ok")))
    if problem:
        raise ProofError(f"refusing to run: {problem}")
    marker = read_marker(fixture)
    if marker.get("form", "project") == "plugin":
        if os.path.lexists(os.path.join(fixture, ".codex", "hooks.json")):
            raise ProofError("refusing to run: an installed-plugin fixture has a project .codex/hooks.json")
        problem = installed_package_problem(marker["plugin_root"])
        if problem:
            raise ProofError(f"refusing to run: {problem}")
        return marker
    with open(os.path.join(fixture, ".codex", "hooks.json"), encoding="utf-8") as f:
        on_disk = json.load(f)
    if on_disk != fixture_hooks(marker["python"]):
        raise ProofError(
            "the fixture's .codex/hooks.json no longer matches the adapter's hook definitions;"
            " run --prepare again in this fixture and review the changed handlers in /hooks"
        )
    return marker


# ------------------------------------------------------------- hooks/list


def verify_hooks(result: dict, fixture: str, form, allow_foreign: bool) -> dict:
    """Check hooks/list for exactly the two handlers of `form`, reported with
    this platform's command variant, trusted and enabled, and no other Stop
    or SessionStart hook (including any other plugin's or project's)."""
    problems, warnings, foreign = [], [], []
    found: dict[str, list] = {}
    plugin_root = getattr(form, "root", None)
    for entry in result.get("data", []):
        for err in entry.get("errors", []):
            path = str(err.get("path") or "")
            ours = inside(path, fixture) or (plugin_root is not None and inside(path, plugin_root))
            (problems if ours else warnings).append(f"hooks/list error at {path}: {err.get('message')}")
        warnings.extend(entry.get("warnings", []))
        for hook in entry.get("hooks", []):
            if form.owns_hook(hook):
                found.setdefault(hook.get("eventName"), []).append(hook)
            elif hook.get("eventName") in EVENTS:
                foreign.append({k: hook.get(k) for k in ("eventName", "source", "pluginId", "sourcePath",
                                                         "command", "enabled", "trustStatus")})
    for event in EVENTS:
        hooks = found.get(event, [])
        if len(hooks) != 1:
            problems.append(f"expected one {event} handler from the {form.name} form, hooks/list shows {len(hooks)}")
            continue
        hook, handler = hooks[0], form.handler(event)
        command = hook.get("command")
        if (form.matches(command, event, OTHER_VARIANT) and handler[OTHER_VARIANT] != handler[VARIANT]
                and not form.matches(command, event, VARIANT)):
            problems.append(f"{event}: hooks/list reports the {OTHER_VARIANT} variant; Codex should select {VARIANT} here")
        elif not form.matches(command, event, VARIANT):
            problems.append(f"{event} command is {command!r}, expected the {VARIANT} variant {form.display(event)!r}")
        if (hook.get("matcher") or None) != MATCHERS[event]:
            problems.append(f"{event} matcher is {hook.get('matcher')!r}, expected {MATCHERS[event]!r}")
        if hook.get("trustStatus") != "trusted":
            problems.append(f"{event} handler trust status is {hook.get('trustStatus')!r}, not 'trusted'")
        if hook.get("enabled") is not True:
            problems.append(f"{event} handler is disabled")
        if hook.get("timeoutSec") != handler["timeout"]:
            problems.append(f"{event} timeout is {hook.get('timeoutSec')!r}, expected {handler['timeout']}")
        if event == "sessionStart" and hook.get("additionalContextLimit") != 0:
            problems.append(f"sessionStart additionalContextLimit is {hook.get('additionalContextLimit')!r}, expected 0")
    if foreign and not allow_foreign:
        problems.append(
            "other Stop/SessionStart hooks would also run in the fixture and confound the proof: "
            + json.dumps(foreign)
        )
    plugin_ids = sorted({str(h.get("pluginId")) for hooks in found.values() for h in hooks})
    if form.name == "plugin" and len(plugin_ids) > 1:
        problems.append(f"the plugin handlers come from several pluginIds {plugin_ids}; pin one with --plugin-id")
    return {"ok": not problems, "problems": problems, "warnings": warnings, "foreign": foreign,
            "form": form.record(), "plugin_ids": plugin_ids if form.name == "plugin" else None,
            "selected_variant": VARIANT, "fixture_hooks": found}


# ------------------------------------------------------------- app-server


class AppServer:
    """One `codex app-server` over stdio, owned by this harness."""

    def __init__(self, codex: str, cwd: str, run_dir: str, deadline: float, env: dict):
        self.run_dir = run_dir
        self.deadline = deadline
        self.command = [codex, "app-server"]
        self.cwd = cwd
        self.log = open(os.path.join(run_dir, "protocol.jsonl"), "a", encoding="utf-8")
        self.stderr_log = open(os.path.join(run_dir, "app-server.stderr.log"), "a", encoding="utf-8")
        self.messages: queue.Queue = queue.Queue()
        self.notes: list[dict] = []  # notifications, in arrival order
        self.responses: dict = {}
        self.server_requests: list[dict] = []
        self.next_id = 0
        self.seq = 0
        self.t0 = time.monotonic()
        self.closed = False
        self.record = {"command": self.command, "cwd": cwd, "started_at": now(), "pid": None,
                       "ended_at": None, "exit_code": None, "termination": None}
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        try:
            self.proc = subprocess.Popen(self.command, cwd=cwd, env=env, stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
        except OSError as exc:
            self.log.close()
            self.stderr_log.close()
            raise ProofError(f"could not start {self.command}: {exc}") from exc
        self.record["pid"] = self.proc.pid
        self._write_record()
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _write_record(self):
        sijav_loop.write_json_atomic(os.path.join(self.run_dir, "process.json"), self.record)

    def _log(self, direction: str, payload):
        self.seq += 1
        line = {"seq": self.seq, "t": round(time.monotonic() - self.t0, 3), "at": now(),
                "dir": direction, "msg": redact(payload)}
        if not self.log.closed:
            self.log.write(json.dumps(line, ensure_ascii=False) + "\n")
            self.log.flush()
        return self.seq

    def _read_stdout(self):
        for raw in self.proc.stdout:
            text = raw.decode("utf-8", "replace").rstrip("\r\n")
            if not text:
                continue
            try:
                self.messages.put(("msg", json.loads(text)))
            except ValueError:
                self.messages.put(("raw", text))
        self.messages.put(("eof", None))

    def _read_stderr(self):
        for raw in self.proc.stderr:
            text = _SECRET_TEXT.sub("[redacted]", raw.decode("utf-8", "replace"))
            try:
                self.stderr_log.write(f"{now()} {text}")
                self.stderr_log.flush()
            except ValueError:  # closed after the process was stopped
                return

    def _send(self, message: dict):
        self._log("send", message)
        try:
            self.proc.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
            self.proc.stdin.flush()
        except OSError as exc:
            raise ProofError(f"could not write to the app-server: {exc}") from exc

    def _until(self, timeout: float) -> float:
        end = min(time.monotonic() + timeout, self.deadline)
        if end <= time.monotonic():
            raise ProofError("the run's wall-clock deadline has passed")
        return end

    def _pump_one(self, until: float) -> None:
        remaining = until - time.monotonic()
        if remaining <= 0:
            return
        try:
            kind, payload = self.messages.get(timeout=min(remaining, 0.5))
        except queue.Empty:
            return
        if kind == "eof":
            try:
                code = self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                code = None
            self._log("eof", {"exit_code": code})
            raise ProofError(f"the app-server closed its output (exit code {code}); see app-server.stderr.log")
        if kind == "raw":
            self._log("recv-raw", payload)
            return
        seq = self._log("recv", payload)
        if "id" in payload and "method" in payload:
            # A server request (an approval, an input prompt). The fixtures use
            # no tools, so nothing is granted: answer with an error and record it.
            self.server_requests.append({"seq": seq, "method": payload["method"]})
            self._send({"id": payload["id"], "error": {
                "code": -32601, "message": f"the Sijav native proof grants nothing ({payload['method']})"}})
        elif "id" in payload:
            self.responses[payload["id"]] = payload
        elif "method" in payload:
            self.notes.append({"seq": seq, "method": payload["method"], "params": payload.get("params") or {}})

    def request(self, method: str, params: dict | None = None, timeout: float = REQUEST_TIMEOUT) -> dict:
        request_id = self.next_id
        self.next_id += 1
        message = {"id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)
        until = self._until(timeout)
        while request_id not in self.responses:
            if time.monotonic() >= until:
                raise ProofError(f"no response to {method} within {timeout:g}s")
            self._pump_one(until)
        response = self.responses.pop(request_id)
        if "error" in response:
            raise ProofError(f"{method} failed: {json.dumps(redact(response['error']))}")
        return response.get("result") or {}

    def notify(self, method: str, params: dict | None = None):
        message = {"method": method}
        if params is not None:
            message["params"] = params
        self._send(message)

    def wait_for(self, predicate, timeout: float, what: str, start: int = 0) -> dict:
        until = self._until(timeout)
        index = start
        while True:
            while index < len(self.notes):
                note = self.notes[index]
                index += 1
                if predicate(note):
                    return note
            if time.monotonic() >= until:
                raise ProofError(f"timed out after {timeout:g}s waiting for {what}")
            self._pump_one(until)

    def close(self, failed: bool):
        """End the owned process: EOF on stdin, a bounded wait, then terminate
        only this process tree. The record and logs are written whatever
        happens on the way."""
        if self.closed:
            return
        self.closed = True
        termination = "exited after stdin closed"
        try:
            try:
                self.proc.stdin.close()
            except OSError:
                pass
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                termination = "terminated by the harness" + (" after a failure" if failed else "")
                if not kill_tree(self.proc):
                    termination += "; the process had not exited when the harness gave up waiting"
        except BaseException as exc:  # still record what is known
            termination = f"cleanup interrupted: {type(exc).__name__}: {exc}"
            raise
        finally:
            self.record.update(ended_at=now(), exit_code=self.proc.poll(), termination=termination)
            try:
                self._write_record()
            finally:
                self.log.close()
                self.stderr_log.close()


def kill_tree(proc: subprocess.Popen) -> bool:
    """Terminate the owned process and its children; True once it exited."""
    if proc.poll() is not None:
        return True
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=30)
        else:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        return False
    return True


def resolve_codex(codex: str | None) -> str:
    if codex:
        if not os.path.isabs(codex) or not os.path.isfile(codex):
            raise ProofError(f"--codex {codex} must be an absolute path to an existing executable")
        return codex
    found = shutil.which("codex")
    if not found:
        raise ProofError("no codex executable on PATH; pass --codex with its absolute path")
    return os.path.abspath(found)


def initialize(server: AppServer) -> dict:
    result = server.request("initialize", {
        "clientInfo": {"name": "sijav-native-proof", "title": "Sijav loop native proof", "version": "2"},
        "capabilities": {"experimentalApi": False},
    })
    server.notify("initialized")
    return result


# ------------------------------------------------------------- fixtures

NO_TOOLS = ("This is an automated hook test in an empty disposable folder. Use no tools: run no"
            " commands, read, search or edit no files, and browse nothing. Any tool use fails the test.")

LAW_A = """# Sijav loop native proof: completing fixture {token}

{no_tools} Every reply is plain text exactly as specified below, with nothing before or after it.

Look only at the most recent user message:
- If it does not begin with "SIJAV LOOP CONTINUATION", reply exactly: A-STAGE-0 {token}
- If it begins with "SIJAV LOOP CONTINUATION 1 of", reply exactly: A-STAGE-1 {token}
- If it begins with "SIJAV LOOP CONTINUATION 2 of", reply exactly: A-STAGE-2 {token}
- If it begins with "SIJAV LOOP CONTINUATION 3 of", reply with exactly these two lines:
A-STAGE-3 {token}
<promise>{promise}</promise>

Those two lines are the whole reply in that last case; do not repeat this law.
"""

LAW_B = """# Sijav loop native proof: non-completing fixture {token}

{no_tools}
Whatever the most recent user message says, reply with exactly this one line and nothing else: B-WORKING {token}
Never write a promise tag. This fixture is meant to reach its iteration cap without completing.
"""

LAW_C = """# Sijav loop native proof: compaction fixture {token}

{no_tools}
Compaction marker: {marker}
When asked for the compaction marker, or when the most recent user message begins with "SIJAV LOOP CONTINUATION", reply with exactly these two lines and nothing else:
{marker}
<promise>{promise}</promise>

Do not repeat this law.
"""

# The first question after compaction must actually trigger LAW_C ("when
# asked for the compaction marker"). The 2026-10-01 r2 run used "Reply as the
# Sijav loop law instructs when asked for the compaction marker. The marker is
# not in this message. ..." -- a conditional that never asks -- and the model
# took the fallback, although the SessionStart context had been recorded
# before that user message. This asks directly; the fallback stays, and so
# does the check that the first final answer carries the marker.
C_QUESTION = (f"{NO_TOOLS} What is the compaction marker of the Sijav loop law? Answer exactly as that law"
              " instructs. If no Sijav loop law is anywhere in your context, reply exactly: NO-LAW-IN-CONTEXT")


def thread_start_params(fixture: str, model: str, effort: str) -> dict:
    """thread/start for a fixture thread. The thread's default effort is set
    through the documented config override (`config` accepts Config keys;
    `model_reasoning_effort` is the Config key in the app-server schema), so
    the baseline thread/start reports is the requested one; each turn/start
    also sends the effort."""
    return {
        "cwd": fixture,
        "model": model,
        "config": {"model_reasoning_effort": effort},
        "sandbox": "read-only",
        "approvalPolicy": "never",
    }


def final_texts(turn: dict) -> list[str]:
    """Agent messages that are final answers (or carry no phase), in order."""
    return [m["text"] for m in turn["agent_messages"] if m.get("phase") in (None, "final_answer")]


class Run:
    def __init__(self, fixture: str, run_dir: str, python: str, server: AppServer, model: str,
                 effort: str, env: dict, form=None):
        self.fixture = fixture
        self.run_dir = run_dir
        self.python = python
        self.form = form or ProjectForm(python, fixture)
        self.server = server
        self.model = model
        self.effort = effort
        self.env = env
        self.token = secrets.token_hex(4)
        # Laws live outside the fixture so the model cannot find them from its
        # working directory; any attempt to look would fail the run anyway.
        self.law_dir = tempfile.mkdtemp(prefix="sijav-proof-laws-")
        self.checks: list[dict] = []
        self.report: dict = {"requested": {"model": model, "effort": effort}, "threads": {},
                             "law_dir": self.law_dir}
        self.cli_log = open(os.path.join(run_dir, "cli.jsonl"), "a", encoding="utf-8")

    # -- helpers

    def check(self, kind: str, name: str, ok: bool, detail=None):
        self.checks.append({"kind": kind, "name": name, "ok": bool(ok), "detail": detail})

    def cli(self, *args: str) -> tuple[int, str]:
        command = [self.python, self.form.cli_script, *args]
        proc = subprocess.run(command, capture_output=True, cwd=self.fixture, env=self.env, timeout=60)
        out = proc.stdout.decode("utf-8", "replace") + proc.stderr.decode("utf-8", "replace")
        self.cli_log.write(json.dumps({"at": now(), "command": command, "exit_code": proc.returncode,
                                       "output": out}, ensure_ascii=False) + "\n")
        self.cli_log.flush()
        return proc.returncode, out

    def state(self) -> dict | None:
        path = sijav_loop.state_path(self.fixture)
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def snapshot(self, label: str):
        state = self.state()
        if state is not None:
            sijav_loop.write_json_atomic(os.path.join(self.run_dir, f"state-{label}.json"), state)
        return state

    def events(self) -> list[dict]:
        path = sijav_loop.events_path(self.fixture)
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def write_law(self, name: str, text: str) -> str:
        path = os.path.join(self.law_dir, name)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        return path

    def finish(self):
        """Copy the laws into the run record and remove their folder."""
        for name in sorted(os.listdir(self.law_dir)):
            shutil.copy(os.path.join(self.law_dir, name), os.path.join(self.run_dir, name))
        shutil.rmtree(self.law_dir, ignore_errors=True)
        self.cli_log.close()

    def disarm(self):
        """Reset the fixture's own state, as its recorded owner."""
        state = self.state()
        if state is None:
            if os.path.lexists(sijav_loop.state_path(self.fixture)):
                code, out = self.cli("reset", "--project-root", self.fixture, "--discard-malformed")
                if code:
                    raise ProofError(f"could not discard the fixture's malformed state: {out}")
            return
        code, out = self.cli("reset", "--project-root", self.fixture, "--session", state["owner_session"])
        if code:
            raise ProofError(f"could not reset the fixture's state: {out}")

    def arm(self, thread_id: str, law: str, cap: int, promise: str):
        code, out = self.cli("start", "--project-root", self.fixture, "--law", law, "--session", thread_id,
                             "--max-iterations", str(cap), "--promise", promise)
        if code:
            raise ProofError(f"could not arm the loop for thread {thread_id}: {out}")
        state = self.state()
        self.check("adapter", f"armed only thread {thread_id}", state and state["owner_session"] == thread_id,
                   {"owner_session": state and state["owner_session"]})

    def new_thread(self, label: str) -> str:
        result = self.server.request("thread/start", thread_start_params(self.fixture, self.model, self.effort))
        thread = result.get("thread") or {}
        thread_id = thread.get("id")
        if not thread_id:
            raise ProofError(f"thread/start returned no thread id: {json.dumps(redact(result))[:500]}")
        session_id = thread.get("sessionId")
        self.report["threads"][label] = {
            "thread_id": thread_id,
            "session_id": session_id,
            # What thread/start reports is the thread's baseline. Each turn/start
            # also sends model and effort; app-server reports no per-turn
            # effective value, so none is claimed as "served".
            "thread_baseline_model": result.get("model"),
            "thread_baseline_reasoning_effort": result.get("reasoningEffort"),
            "turn_override_sent": {"model": self.model, "effort": self.effort},
            "model_provider": result.get("modelProvider"),
            "service_tier": result.get("serviceTier"),
            "cwd": result.get("cwd"),
        }
        self.check("native", f"{label}: a new root thread's sessionId is its id",
                   session_id == thread_id, {"id": thread_id, "sessionId": session_id})
        self.check("native", f"{label}: thread/start applied the requested model and reasoning effort as the"
                   " thread baseline",
                   result.get("model") == self.model and result.get("reasoningEffort") == self.effort,
                   {"requested": {"model": self.model, "effort": self.effort},
                    "thread_baseline": {"model": result.get("model"), "effort": result.get("reasoningEffort")}})
        return thread_id

    def turn(self, thread_id: str, text: str) -> dict:
        start = len(self.server.notes)
        result = self.server.request("turn/start", {
            "threadId": thread_id,
            "input": [{"type": "text", "text": text}],
            "model": self.model,
            "effort": self.effort,
        })
        turn_id = (result.get("turn") or {}).get("id")

        def done(note):
            p = note["params"]
            return (note["method"] == "turn/completed" and p.get("threadId") == thread_id
                    and (turn_id is None or (p.get("turn") or {}).get("id") == turn_id))

        completed = self.server.wait_for(done, TURN_TIMEOUT, f"turn/completed on thread {thread_id}", start)
        notes = [n for n in self.server.notes[start:] if n["params"].get("threadId") == thread_id]
        turn = completed["params"].get("turn") or {}
        return {
            "turn_id": turn_id or turn.get("id"),
            "status": turn.get("status"),
            "error": turn.get("error"),
            "start_index": start,
            "agent_messages": [
                {"seq": n["seq"], "text": n["params"]["item"].get("text"), "phase": n["params"]["item"].get("phase")}
                for n in notes
                if n["method"] == "item/completed" and (n["params"].get("item") or {}).get("type") == "agentMessage"
            ],
            "item_types": item_types(notes),
            "hook_runs": hook_runs(notes, self.form.owns_run),
            "reroutes": [n["params"] for n in notes if n["method"] == "model/rerouted"],
        }

    # -- fixtures

    def fixture_a(self):
        promise = f"PROOF-A-{self.token}"
        law_text = LAW_A.format(token=self.token, promise=promise, no_tools=NO_TOOLS)
        law = self.write_law("law-a.md", law_text)
        thread_id = self.new_thread("A")
        self.disarm()
        self.arm(thread_id, law, CAPS["A"], promise)
        before = len(self.events())
        turn = self.turn(thread_id, "Start the disposable Sijav loop proof. Follow this loop law exactly.\n\n" + law_text)
        stops = [e for e in self.events()[before:] if e["event"] == "Stop" and e["session_id"] == thread_id]
        state = self.snapshot("A")
        self.report["threads"]["A"].update(turn=summarize_turn(turn), stop_events=stops)
        self.checks.extend(evaluate_a(turn, stops, state, self.token))

    def fixture_b(self):
        promise = f"PROOF-B-{self.token}"
        law_text = LAW_B.format(token=self.token, no_tools=NO_TOOLS)
        law = self.write_law("law-b.md", law_text)
        thread_id = self.new_thread("B")
        self.disarm()
        self.arm(thread_id, law, CAPS["B"], promise)
        before = len(self.events())
        turn = self.turn(thread_id, "Start the disposable Sijav loop proof. Follow this loop law exactly.\n\n" + law_text)
        stops = [e for e in self.events()[before:] if e["event"] == "Stop" and e["session_id"] == thread_id]
        state = self.snapshot("B")
        self.report["threads"]["B"].update(turn=summarize_turn(turn), stop_events=stops)
        self.checks.extend(evaluate_b(turn, stops, state, self.token))
        return thread_id

    def fixture_c(self, previous_owner: str):
        promise = f"PROOF-C-{self.token}"
        marker = f"C-MARKER-{secrets.token_hex(6).upper()}"
        thread_id = self.new_thread("C")
        # History first, while the loop still belongs to fixture B's thread.
        before = len(self.events())
        history = self.turn(thread_id, f"{NO_TOOLS} Reply with exactly this line and nothing else: C-HISTORY {self.token}")
        unowned = [e for e in self.events()[before:] if e["session_id"] == thread_id]
        self.check("adapter", "C history turn: a session that does not own the loop stops normally",
                   [e["outcome"] for e in unowned if e["event"] == "Stop"] == ["other_session"]
                   and not any(r["status"] == "blocked" for r in history["hook_runs"]),
                   {"events": unowned, "owner_then": previous_owner})
        self.check("native", "C history turn used no tools", not disallowed(history["item_types"]),
                   history["item_types"])
        # The law and its marker exist only from here on, after that response,
        # and outside the fixture.
        law_text = LAW_C.format(token=self.token, marker=marker, promise=promise, no_tools=NO_TOOLS)
        law = self.write_law("law-c.md", law_text)
        self.disarm()
        self.arm(thread_id, law, CAPS["C"], promise)
        armed_state = self.snapshot("C-armed")
        before = len(self.events())
        start = len(self.server.notes)
        self.server.request("thread/compact/start", {"threadId": thread_id})
        compaction = self.server.wait_for(
            lambda n: n["method"] == "item/completed" and n["params"].get("threadId") == thread_id
            and (n["params"].get("item") or {}).get("type") == "contextCompaction",
            TURN_TIMEOUT, "the contextCompaction item to complete", start)
        compact_turn = (compaction["params"] or {}).get("turnId")
        if compact_turn:
            self.server.wait_for(
                lambda n: n["method"] == "turn/completed" and n["params"].get("threadId") == thread_id
                and (n["params"].get("turn") or {}).get("id") == compact_turn,
                REQUEST_TIMEOUT, "the compaction turn to complete", start)
        turn = self.turn(thread_id, C_QUESTION)
        notes = [n for n in self.server.notes[start:] if n["params"].get("threadId") == thread_id]
        events = [e for e in self.events()[before:] if e["session_id"] == thread_id]
        state = self.snapshot("C")
        _, body = sijav_loop.read_law(law)
        expected_context = sijav_loop.compaction_context(armed_state, body)
        self.report["threads"]["C"].update(
            history_turn=summarize_turn(history), turn=summarize_turn(turn), marker=marker,
            compaction_item_seq=compaction["seq"], adapter_events=events)
        self.checks.extend(evaluate_c(turn, notes, events, state, compaction["seq"], marker, body,
                                      expected_context, self.form.owns_run))

    def run(self):
        self.fixture_a()
        b_thread = self.fixture_b()
        self.fixture_c(b_thread)


def item_types(notes: list[dict]) -> list[str]:
    seen = []
    for n in notes:
        if n["method"] in ("item/started", "item/completed"):
            kind = (n["params"].get("item") or {}).get("type")
            if kind not in seen:
                seen.append(kind)
    return seen


def disallowed(types: list[str]) -> list[str]:
    return [t for t in types if t not in ALLOWED_ITEMS]


def hook_runs(notes: list[dict], owns_run) -> list[dict]:
    """Completed hook runs; `fixture_hook` marks the runs of the form under
    test (owns_run(run summary) is true)."""
    runs = []
    for n in notes:
        if n["method"] != "hook/completed":
            continue
        run = n["params"].get("run") or {}
        runs.append({
            "seq": n["seq"],
            "event": run.get("eventName"),
            "status": run.get("status"),
            "fixture_hook": bool(owns_run(run)),
            "source": run.get("source"),
            "entries": run.get("entries") or [],
            "turn_id": n["params"].get("turnId"),
            "duration_ms": run.get("durationMs"),
        })
    return runs


def summarize_turn(turn: dict) -> dict:
    return {k: turn[k] for k in ("turn_id", "status", "error", "agent_messages", "item_types", "hook_runs", "reroutes")}


# ------------------------------------------------------------- evaluation


def expected_same_as_previous(stops: list[dict]) -> list[bool]:
    """What the adapter's diagnostic must say for these callbacks: the hash
    covers stop_hook_active and the message, so a callback matches its
    predecessor only when both are equal."""
    return [i > 0 and stops[i].get("stop_hook_active") == stops[i - 1].get("stop_hook_active")
            and stops[i].get("message_sha256") == stops[i - 1].get("message_sha256")
            for i in range(len(stops))]


def _stop_checks(label: str, turn: dict, stops: list[dict], outcomes: list[str]) -> list[dict]:
    checks = []

    def add(kind, name, ok, detail=None):
        checks.append({"kind": kind, "name": f"{label}: {name}", "ok": bool(ok), "detail": detail})

    add("native", "the turn completed", turn["status"] == "completed", {"status": turn["status"], "error": turn["error"]})
    add("native", "no tool or other non-text item ran", not disallowed(turn["item_types"]), turn["item_types"])
    add("adapter", f"Stop outcomes are {outcomes}", [e["outcome"] for e in stops] == outcomes,
        [e["outcome"] for e in stops])
    add("native", "every Stop callback carried one native turn_id",
        len({e.get("turn_id") for e in stops}) == 1 and stops, sorted({str(e.get("turn_id")) for e in stops}))
    add("native", "the hook's turn_id is the app-server turn id",
        stops and stops[0].get("turn_id") == turn["turn_id"], {"hook": stops and stops[0].get("turn_id"), "app_server": turn["turn_id"]})
    active = [e.get("stop_hook_active") for e in stops]
    add("native", "stop_hook_active is false first, then true", active == [False] + [True] * (len(stops) - 1), active)
    add("adapter", "same_as_previous_stop follows stop_hook_active and the message hash",
        bool(stops) and [e.get("same_as_previous_stop") for e in stops] == expected_same_as_previous(stops),
        {"recorded": [e.get("same_as_previous_stop") for e in stops], "expected": expected_same_as_previous(stops)})
    runs = [r for r in turn["hook_runs"] if r["event"] == "stop" and r["fixture_hook"]]
    add("native", "Codex reported one fixture Stop hook run per adapter Stop event", len(runs) == len(stops),
        {"hook_runs": [(r["status"], r["entries"]) for r in runs]})
    add("native", "no fixture Stop hook run failed", not any(r["status"] == "failed" for r in runs),
        [r["status"] for r in runs])
    return checks


def evaluate_a(turn: dict, stops: list[dict], state: dict | None, token: str) -> list[dict]:
    checks = _stop_checks("A", turn, stops, ["continued"] * 3 + ["complete"])
    checks.append({"kind": "adapter", "name": "A: final state is complete after 3 continuations",
                   "ok": bool(state) and (state["status"], state["iteration"]) == ("complete", 3),
                   "detail": state and {k: state[k] for k in ("status", "status_reason", "iteration", "max_iterations")}})
    texts = final_texts(turn)
    expected = [f"A-STAGE-{i} {token}" for i in range(3)] + [f"A-STAGE-3 {token}\n<promise>PROOF-A-{token}</promise>"]
    checks.append({"kind": "model", "name": "A: the final answers were exactly the staged replies",
                   "ok": [(t or "").strip().replace("\r\n", "\n") for t in texts] == expected,
                   "detail": {"final": texts, "all": turn["agent_messages"]}})
    return checks


def evaluate_b(turn: dict, stops: list[dict], state: dict | None, token: str) -> list[dict]:
    checks = _stop_checks("B", turn, stops, ["continued", "continued", "exhausted"])
    checks.append({"kind": "adapter", "name": "B: final state is exhausted at the cap, not complete",
                   "ok": bool(state) and (state["status"], state["iteration"]) == ("exhausted", 2),
                   "detail": state and {k: state[k] for k in ("status", "status_reason", "iteration", "max_iterations")}})
    hashes = [e.get("message_sha256") for e in stops]
    checks.append({"kind": "model", "name": "B: every final message was identical (each was counted)",
                   "ok": len(set(hashes)) == 1 and len(hashes) == 3,
                   "detail": {"final": final_texts(turn), "hashes": hashes}})
    return checks


def evaluate_c(turn, notes, events, state, compaction_seq, marker, body, expected_context, owns_run) -> list[dict]:
    checks = []

    def add(kind, name, ok, detail=None):
        checks.append({"kind": kind, "name": f"C: {name}", "ok": bool(ok), "detail": detail})

    types = item_types(notes)
    runs = [r for r in hook_runs(notes, owns_run) if r["event"] == "sessionStart" and r["fixture_hook"]]
    contexts = [e["text"] for r in runs for e in r["entries"] if e.get("kind") == "context"]
    starts = [e for e in events if e["event"] == "SessionStart"]
    add("native", "no tool or other non-text item ran after compaction", not disallowed(types), types)
    add("native", "the contextCompaction item completed", compaction_seq is not None, compaction_seq)
    add("adapter", "the SessionStart hook saw source compact and reloaded the law",
        [(e.get("source"), e.get("outcome")) for e in starts] == [("compact", "law_reloaded")],
        [(e.get("source"), e.get("outcome")) for e in starts])
    add("native", "Codex reported a completed fixture SessionStart run with context",
        len(runs) == 1 and runs[0]["status"] == "completed" and len(contexts) == 1,
        [(r["status"], [e.get("kind") for e in r["entries"]]) for r in runs])
    add("native", "the context entry carries the whole law body", contexts and body in contexts[0],
        {"context_chars": [len(c) for c in contexts], "law_chars": len(body)})
    add("native", "the context entry equals the adapter's compaction context exactly",
        contexts and contexts[0] == expected_context, None)
    finals = [m for m in turn["agent_messages"] if m.get("phase") in (None, "final_answer")]
    first = finals[0] if finals else None
    delivered = [r for r in runs if r["status"] == "completed"
                 and any(e.get("kind") == "context" for e in r["entries"])]
    add("native", "the law context arrived before the next final answer",
        bool(delivered) and bool(first) and delivered[0]["seq"] < first["seq"],
        {"context_run_seq": delivered[0]["seq"] if delivered else None,
         "first_reply_seq": first and first["seq"]})
    add("model", "the first final answer after compaction returned the injected marker",
        first and marker in (first["text"] or ""), first and first["text"])
    stops = [e for e in events if e["event"] == "Stop"]
    add("adapter", "the marker reply completed the loop without a continuation",
        [e["outcome"] for e in stops] == ["complete"] and state and state["status"] == "complete",
        {"stops": [e["outcome"] for e in stops], "status": state and state["status"]})
    return checks


# ------------------------------------------------------------- commands


def open_run_dir(fixture: str, label: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = os.path.join(fixture, "proof-runs", f"{stamp}-{label}")
    os.makedirs(run_dir)
    return run_dir


def check_hooks(server: AppServer, fixture: str, form, allow_foreign: bool) -> dict:
    result = server.request("hooks/list", {"cwds": [fixture]})
    return verify_hooks(result, fixture, form, allow_foreign)


def cli_version(codex: str, env: dict) -> dict:
    """`codex --version` (no thread, no model) as the CLI reports it."""
    try:
        proc = subprocess.run([codex, "--version"], capture_output=True, env=env, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"output": None, "error": f"{type(exc).__name__}: {exc}"}
    return {"output": (proc.stdout + proc.stderr).decode("utf-8", "replace").strip(), "exit_code": proc.returncode}


def version_problem(expected: str | None, versions: dict, init: dict | None) -> str | None:
    """Why the runtime is not the requested version, or None. Both the CLI's
    --version and the app-server's userAgent must name it."""
    if not expected:
        return None
    agent = str((init or {}).get("userAgent") or "")
    output = str(versions.get("output") or "")
    missing = [name for name, text in (("codex --version", output), ("app-server userAgent", agent))
               if expected not in text]
    if missing:
        return f"requested Codex version {expected} is not what {' and '.join(missing)} report: {output!r} / {agent!r}"
    return None


def settle_fixture_state(fixture: str, marker: dict, form, env: dict) -> dict:
    """Leave no fixture loop armed: a state still active or paused after the
    run is stopped through the CLI (terminal states are left as they are)."""
    path = sijav_loop.state_path(fixture)
    if not os.path.exists(path):
        return {"state": None, "action": "none"}
    try:
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError) as exc:
        return {"state": "unreadable", "action": "none", "detail": str(exc)}
    status = state.get("status") if isinstance(state, dict) else None
    if status not in ("active", "paused"):
        return {"state": status, "action": "none"}
    command = [marker["python"], form.cli_script, "stop", "--project-root", fixture,
               "--reason", "native proof run ended; fixture state stopped by the harness"]
    proc = subprocess.run(command, capture_output=True, env=env, timeout=60, cwd=fixture)
    return {"state": status, "action": "stop", "exit_code": proc.returncode,
            "output": (proc.stdout + proc.stderr).decode("utf-8", "replace")}


def command_check(args) -> int:
    fixture = os.path.abspath(args.check)
    marker = check_fixture_files(fixture)
    form = form_of(marker, fixture)
    run_dir = open_run_dir(fixture, "check")
    env, removed = child_env()
    codex = resolve_codex(args.codex)
    versions = cli_version(codex, env)
    server = AppServer(codex, fixture, run_dir, time.monotonic() + 300, env)
    failed, init, verdict = True, None, None
    try:
        init = initialize(server)
        verdict = check_hooks(server, fixture, form, args.allow_foreign_hooks)
        problem = version_problem(args.expect_codex_version, versions, init)
        if problem:
            verdict["ok"] = False
            verdict["problems"].append(problem)
        failed = False
    finally:
        server.close(failed)
        summary = {"mode": "check", "run_dir": run_dir, "form": form.description, "form_detail": form.record(),
                   "codex": codex, "codex_version": {"requested": args.expect_codex_version, "cli": versions,
                                                     "app_server_user_agent": (init or {}).get("userAgent")},
                   "initialize": redact(init), "hooks": verdict, "removed_identity_variables": removed,
                   "process": server.record}
        sijav_loop.write_json_atomic(os.path.join(run_dir, "summary.json"), redact(summary))
        print(f"Run record: {run_dir}")
        print(f"Codex: {versions.get('output')} / {(init or {}).get('userAgent')}"
              f" (requested {args.expect_codex_version or 'any'})")
    if verdict["ok"]:
        print(f"Both {form.name}-form hook handlers are listed with the {VARIANT} variant, trusted and enabled."
              " --run may proceed.")
        return 0
    for p in verdict["problems"]:
        print(f"PROBLEM: {p}")
    print(trust_instructions(fixture, form))
    return 2


def command_run(args) -> int:
    fixture = os.path.abspath(args.run)
    marker = check_fixture_files(fixture)
    form = form_of(marker, fixture)
    run_dir = open_run_dir(fixture, "run")
    deadline = time.monotonic() + args.deadline
    codex = resolve_codex(args.codex)
    env, removed = child_env()
    versions = cli_version(codex, env)
    summary: dict = {"mode": "run", "run_dir": run_dir, "fixture": fixture, "codex": codex,
                     "form": form.description, "form_detail": form.record(),
                     "codex_version": {"requested": args.expect_codex_version, "cli": versions},
                     "requested": {"model": args.model, "effort": args.effort},
                     "removed_identity_variables": removed,
                     "proof_scope": ("Stop continuations and a manual thread/compact/start refeed in a disposable"
                                     " fixture, for the form named above. Automatic mid-turn compaction is not"
                                     " exercised here.")}
    server = AppServer(codex, fixture, run_dir, deadline, env)
    failed = True
    code = 1
    try:
        init = initialize(server)
        summary["initialize"] = redact(init)
        summary["codex_version"]["app_server_user_agent"] = init.get("userAgent")
        verdict = check_hooks(server, fixture, form, args.allow_foreign_hooks)
        problem = version_problem(args.expect_codex_version, versions, init)
        if problem:
            verdict["ok"] = False
            verdict["problems"].append(problem)
        summary["hooks"] = verdict
        if not verdict["ok"]:
            for p in verdict["problems"]:
                print(f"PROBLEM: {p}")
            print(trust_instructions(fixture, form))
            failed = False
            code = 2
            return code
        run = Run(fixture, run_dir, marker["python"], server, args.model, args.effort, env, form)
        try:
            run.run()
        except ProofError as exc:
            run.check("harness", "the run finished", False, str(exc))
        finally:
            summary.update(report=run.report, checks=run.checks, server_requests=server.server_requests)
            run.finish()
        failed = not all(c["ok"] for c in run.checks) or bool(server.server_requests)
        code = 1 if failed else 0
        return code
    finally:
        try:
            server.close(failed)
        finally:
            summary["process"] = server.record
            summary["exit_code"] = code
            try:
                summary["fixture_state_after_run"] = settle_fixture_state(fixture, marker, form, env)
            except (OSError, subprocess.TimeoutExpired) as exc:
                summary["fixture_state_after_run"] = {"action": "failed", "detail": f"{type(exc).__name__}: {exc}"}
            events = sijav_loop.events_path(fixture)
            if os.path.exists(events):
                shutil.copy(events, os.path.join(run_dir, "events.jsonl"))
            sijav_loop.write_json_atomic(os.path.join(run_dir, "summary.json"), redact(summary))
            print(f"Run record: {run_dir}")
            print(f"Form: {form.description}")
            cv = summary["codex_version"]
            print(f"Codex: {(cv.get('cli') or {}).get('output')} / {cv.get('app_server_user_agent')}"
                  f" (requested {cv.get('requested') or 'any'})")
            print(f"Fixture state after the run: {summary['fixture_state_after_run']}")
            for c in summary.get("checks", []):
                print(f"{'PASS' if c['ok'] else 'FAIL'} [{c['kind']}] {c['name']}")
            for request in summary.get("server_requests", []):
                print(f"FAIL [native] unexpected server request {request['method']} (refused)")
            threads = (summary.get("report") or {}).get("threads", {})
            for label, info in threads.items():
                print(f"thread {label}: {info.get('thread_id')} sessionId={info.get('session_id')}"
                      f" requested {args.model}/{args.effort};"
                      f" thread baseline {info.get('thread_baseline_model')}/{info.get('thread_baseline_reasoning_effort')};"
                      f" per-turn override sent {args.model}/{args.effort} (no per-turn effective value is reported)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bounded live proof of the Sijav loop hooks.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", metavar="FIXTURE", help="write the fixture's hooks; no process, no model")
    mode.add_argument("--check", metavar="FIXTURE", help="hooks/list only; no model call")
    mode.add_argument("--run", metavar="FIXTURE", help="the live proof; calls the model")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--effort", default=DEFAULT_EFFORT)
    parser.add_argument("--codex", help="absolute path of the codex executable (default: codex on PATH)")
    parser.add_argument("--python", default=sys.executable, help="interpreter the fixture hooks run (--prepare)")
    parser.add_argument("--deadline", type=float, default=RUN_DEADLINE, help="wall-clock seconds for --run")
    parser.add_argument("--allow-foreign-hooks", action="store_true",
                        help="proceed although other Stop/SessionStart hooks are loaded")
    parser.add_argument("--fixture-outside-temp", action="store_true",
                        help="allow a fixture outside the system temp directory (--prepare)")
    parser.add_argument("--installed-plugin-root", metavar="ROOT",
                        help="--prepare the installed-plugin form against the plugin root Codex installed")
    parser.add_argument("--plugin-id", help="pin the pluginId hooks/list must report (installed-plugin form)")
    parser.add_argument("--expect-codex-version", metavar="VERSION",
                        help="refuse --check/--run unless codex --version and the app-server userAgent name it")
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    try:
        if args.prepare:
            return prepare(args.prepare, os.path.abspath(args.python), args.fixture_outside_temp,
                           args.installed_plugin_root, args.plugin_id)
        if args.installed_plugin_root or args.plugin_id:
            raise ProofError("--installed-plugin-root and --plugin-id belong to --prepare; the fixture remembers them")
        if args.check:
            return command_check(args)
        return command_run(args)
    except (ProofError, sijav_loop.LoopError) as exc:
        print(f"NATIVE PROOF STOPPED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
