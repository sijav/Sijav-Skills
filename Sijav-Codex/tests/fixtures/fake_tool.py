#!/usr/bin/env python3
"""A stand-in for the codex, npm, node and claude CLIs in setup tests. It installs nothing.

Its JSON follows the Codex 0.159.2 CLI fields established by the root's native-CLI investigation:
marketplace add prints marketplaceName, installedRoot and alreadyAdded; plugin add prints pluginId,
name, marketplaceName, version, installedPath and authPolicy; plugin list entries carry id, name and
the booleans installed and enabled (PluginSummary). The list's outer shape is this fake's choice;
the setup searches it for plugin entries rather than assuming a layout.

Appends {"tool", "argv", "cwd"} to $FAKE_TOOL_LOG and answers as $FAKE_TOOL_MODE says:
  ok             every command succeeds with the fields above
  other_name     marketplace add reports another marketplace name
  no_name_field  marketplace add omits marketplaceName
  add_fails      plugin add exits 1
  add_no_path    plugin add omits installedPath
  not_json       marketplace add prints text
  list_disabled  plugin list shows the plugin installed but not enabled
  list_missing   plugin list shows no such plugin
  hang           marketplace add starts a child that holds stdout open and both sleep 120 s
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

tool = Path(sys.argv[0]).stem
argv = sys.argv[1:]
log = os.environ.get("FAKE_TOOL_LOG")
if log:
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"tool": tool, "argv": argv, "cwd": os.getcwd(), "pid": os.getpid()}) + "\n")
mode = os.environ.get("FAKE_TOOL_MODE", "ok")
installed = os.environ.get("FAKE_TOOL_INSTALLED") or os.getcwd()

if argv == ["--version"]:
    print({"codex": "codex-cli 0.159.3", "node": "v24.16.0", "claude": "2.1.286 (Claude Code)"}.get(tool, "1.0.0"))
    sys.exit(0)
if tool == "codex" and argv[:3] == ["plugin", "marketplace", "add"] and mode == "hang_escaped":
    # An intermediate process starts a grandchild that inherits stdout, then exits at once: the
    # grandchild's parent is gone, so taskkill /T from this process cannot reach it.
    print("partial output before the hang", flush=True)
    script = ("import subprocess, sys; from pathlib import Path; "
              "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'], stdout=sys.stdout); "
              f"Path({os.environ['FAKE_TOOL_CHILD_PID']!r}).write_text(str(c.pid))")
    subprocess.run([sys.executable, "-c", script], stdout=sys.stdout)
    time.sleep(120)
if tool == "codex" and argv[:3] == ["plugin", "marketplace", "add"]:
    if mode == "hang":
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], stdout=sys.stdout)
        Path(os.environ["FAKE_TOOL_CHILD_PID"]).write_text(str(child.pid), encoding="utf-8")
        time.sleep(120)
    if mode == "not_json":
        print("Added marketplace")
        sys.exit(0)
    out = {"marketplaceName": "someone-else" if mode == "other_name" else "sijav-codex-local",
           "installedRoot": argv[3], "alreadyAdded": False}
    if mode == "no_name_field":
        del out["marketplaceName"]
    print(json.dumps(out))
    sys.exit(0)
if tool == "codex" and argv[:2] == ["plugin", "add"]:
    if mode == "add_fails":
        print("error: plugin not found", file=sys.stderr)
        sys.exit(1)
    out = {"pluginId": argv[2], "name": argv[2].split("@")[0], "marketplaceName": argv[2].split("@")[1],
           "version": "0.1.0", "installedPath": installed, "authPolicy": "ON_INSTALL"}
    if mode == "add_no_path":
        del out["installedPath"]
    print(json.dumps(out))
    sys.exit(0)
if tool == "codex" and argv[:2] == ["plugin", "list"]:
    market = argv[argv.index("--marketplace") + 1]
    plugins = [] if mode == "list_missing" else [
        {"id": f"sijav-codex@{market}", "name": "sijav-codex", "installed": True, "enabled": mode != "list_disabled"}]
    print(json.dumps({"marketplaces": [{"name": market, "plugins": plugins}]}))
    sys.exit(0)
if tool == "npm" and argv[:1] == ["ci"]:
    print("added 1 package")
    sys.exit(0)
print(f"fake {tool}: unexpected {argv}", file=sys.stderr)
sys.exit(9)
