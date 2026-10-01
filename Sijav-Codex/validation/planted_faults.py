"""Plant one fault at a time in a throwaway copy of the package and check that its test catches it.

    python -B validation/planted_faults.py [--check-only] [--only "jev:" "verify:" ...]

Each fault edits helper code in a fresh temporary copy (the stage is never edited), runs the named
test module from the copy, and expects it to FAIL. A fault may need several edits where the helper
deliberately guards a contract twice (both guards are removed, so the fault is real). Prints one line
per fault and exits 1 if any fault survived (its tests still passed) or could not be planted.
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

STAGE = Path(__file__).resolve().parents[1]
IGNORE = shutil.ignore_patterns("validation", "node_modules", "__pycache__", "*.pyc")
CS, JEV, VERIFY, SETUP, SMOKE = ("skills/claude/claude_session.py", "skills/roast/jev.py", "skills/research/verify.py",
                                 "tools/sijav_codex_setup.py", "tests/permission_smoke.py")
T_CS, T_JEV, T_VERIFY, T_SETUP, T_SMOKE = ("tests.test_claude_session", "tests.test_jev", "tests.test_verify",
                                           "tests.test_setup", "tests.test_permission_smoke")

# (name, file, [(old, new), ...], test module)
FAULTS = [
    ("claude: a failed unconfirmed attempt is promoted to a confirmed id", CS,
     [('state.update(status="unconfirmed", session_id=None, candidate_session_id=asked, candidate_reported=reported)',
       'state.update(status="ready", session_id=asked, candidate_session_id=None, candidate_reported=False)')], T_CS),
    ("claude: an id without a delivered turn counts as confirmed", CS,
     [("delivered = bool(seen) and stream.delivered", "delivered = bool(seen)")], T_CS),
    ("claude: rules not re-required before a delivered turn", CS,
     [('if not state["rules_delivered"] and not rules and not a.no_project_rules:',
       'if state["status"] == "new" and not rules and not a.no_project_rules:')], T_CS),
    ("claude: a follow-up starts a new conversation", CS,
     [('return state["session_id"], ["--resume", state["session_id"]], "resume"',
       'return state["session_id"], ["--session-id", str(uuid.uuid4())], "resume"')], T_CS),
    ("claude: technical mode may write", CS,
     [('TECHNICAL_TOOLS = ("Read", "Glob", "Grep", "WebFetch")',
       'TECHNICAL_TOOLS = ("Read", "Glob", "Grep", "WebFetch", "Write")')], T_CS),
    ("claude: code mode pre-approves writes anywhere (bare Edit)", CS,
     [('PROJECT_EDIT = "Edit(./**)"', 'PROJECT_EDIT = "Edit"')], T_CS),
    ("claude: .git/.claude/.codex edits not denied", CS,
     [('*[t for t in COMMAND_TOOLS if t not in run_tools], *PROTECTED_EDITS, *SECRET_READS]',
       '*[t for t in COMMAND_TOOLS if t not in run_tools], *SECRET_READS]')], T_CS),
    ("claude: permission mode left to user settings", CS,
     [('"--permission-mode", "manual", "--permission-prompts", "none"', '"--permission-prompts", "none"')], T_CS),
    ("claude: init permission mode not checked", CS,
     [('if i.get("permissionMode") not in NORMAL_MODES:', "if False:")], T_CS),
    ("claude: init apiKeySource not checked", CS,
     [('if i.get("apiKeySource") != "none":', "if False:")], T_CS),
    ("claude: provider environment inherited", CS,
     [("    for k in dropped:\n        del env[k]\n", "    dropped = []\n")], T_CS),
    ("claude: a missing or late init is accepted", CS,
     [("if self.events == 1 and not (kind == \"system\" and ev.get(\"subtype\") == \"init\"):", "if False:")], T_CS),
    ("claude: starting model not checked", CS,
     [("        if i.get(\"model\") != MODEL:\n", "        if False:\n")], T_CS),
    ("claude: replying model not checked", CS,
     [("    wrong = sorted({m for m in served if m != MODEL})", "    wrong = []")], T_CS),
    ("claude: SIJAV_CLAUDE=off ignored", CS,
     [('return os.environ.get("SIJAV_CLAUDE", "on").strip().lower() in OFF_VALUES', "return False")], T_CS),
    ("claude: no per-purpose lock", CS,
     [("    with purpose_lock(d, purpose):\n        state = read_state(d, purpose, root)\n        if state is None:\n"
       "            if not (a.scope",
       "    with contextlib.nullcontext():\n        state = read_state(d, purpose, root)\n        if state is None:\n"
       "            if not (a.scope")], T_CS),
    ("claude: leftover descendants survive the call", CS,
     [("            tree.stop(proc)  # descendants the CLI left behind end with the call\n", "")], T_CS),
    ("claude: a failed final state write loses the reply", CS,
     [("        try:\n            write_json_atomic(d / \"state.json\", state)\n        except SessionError as exc:\n"
       "            state_problem = str(exc)\n",
       "        write_json_atomic(d / \"state.json\", state)\n")], T_CS),
    ("claude: settings files may widen approvals (no --restricted)", CS,
     [('"--safe-mode", "--restricted",', '"--safe-mode",')], T_CS),
    ("claude: denied tool uses are not reported", CS,
     [("    if outcome[\"permission_denials\"]:\n", "    if False:\n")], T_CS),
    ("claude: NODE_OPTIONS and TLS weakening inherited", CS,
     [("|CLAUDE_CODE_SKIP_\\w+_AUTH|\"\n                      r\"NODE_OPTIONS|NODE_TLS_REJECT_UNAUTHORIZED)$\"",
       "|CLAUDE_CODE_SKIP_\\w+_AUTH)$\"")], T_CS),
    ("claude: a refused new purpose leaves a folder", CS,
     [("        if not rules and not a.no_project_rules:\n            raise SessionError(\"safe mode loads no CLAUDE.md: a new",
       "        if False:\n            raise SessionError(\"safe mode loads no CLAUDE.md: a new")], T_CS),
    ("claude: a stray .claude folder moves the root", CS,
     [("for test in (lambda p: (p / \".claude\" / \"todo.db\").is_file(), lambda p: (p / \".git\").exists(),\n"
       "                 lambda p: (p / \".claude\").is_dir()):",
       "for test in (lambda p: (p / \".claude\").is_dir(), lambda p: (p / \".git\").exists(),\n"
       "                 lambda p: (p / \".claude\" / \"todo.db\").is_file()):")], T_CS),
    ("claude: session ids from state not validated (both guards)", CS,
     [("        if not _uuid_or_none(data.get(key)):", "        if False:"),
      ("        if not UUID_RE.match(asked_id):", "        if False:")], T_CS),
    ("claude: a state copied from another purpose or project accepted", CS,
     [("    elif purpose is not None and data[\"purpose\"] != purpose:", "    elif False:"),
      ("    elif root is not None and not same_path(data[\"project_root\"], root):", "    elif False:")], T_CS),
    ("claude: a CLI in the current folder is picked from PATH", CS,
     [("        if any(same_path(entry, a) for a in avoid):\n            continue\n", "")], T_CS),
    ("jev: critical question dropped", JEV,
     [("        questions[CRITICAL] = CRITICAL_QUESTION\n", "        pass\n")], T_JEV),
    ("jev: missing answers accepted", JEV,
     [('        out.append(f"Jev did not answer {missing}")', "        pass")], T_JEV),
    ("jev: facts sent with their sources", JEV,
     [('"checked_facts": [f["fact"].strip() for f in facts]',
       '"checked_facts": [f"{f[\'fact\']} ({f[\'source\']})" for f in facts]')], T_JEV),
    ("jev: code in questions not checked", JEV,
     [('out += [f"code would reach Jev at {hit}" for hit in code_in({"state": sent_state, "questions": sent_questions})]',
       'out += [f"code would reach Jev at {hit}" for hit in code_in({"state": sent_state})]')], T_JEV),
    ("jev: a stray .claude folder moves the root", JEV,
     [("for test in (lambda p: (p / \".claude\" / \"todo.db\").is_file(), lambda p: (p / \".git\").exists(),\n"
       "                 lambda p: (p / \".claude\").is_dir()):",
       "for test in (lambda p: (p / \".claude\").is_dir(), lambda p: (p / \".git\").exists(),\n"
       "                 lambda p: (p / \".claude\" / \"todo.db\").is_file()):")], T_JEV),
    ("jev: SDK retries left at their default", JEV,
     [("        policy = ts.RetryPolicy(max_retries=0)\n", "        policy = None\n")], T_JEV),
    ("jev: no in-flight state, so a crashed call can be asked again", JEV,
     [('        state.update(status="calling", calling={"request": c, "started": now(), "helper_pid": os.getpid()})\n',
       "")], T_JEV),
    ("jev: an interrupt during the call is not recorded", JEV,
     [("        except BaseException as exc:  # an interrupt: record what is known, then stop\n            with contextlib.suppress(Exception):\n",
       "        except BaseException as exc:  # an interrupt: record what is known, then stop\n            with contextlib.suppress(Exception):\n                raise\n")],
     T_JEV),
    ("jev: a brief edited after open is accepted", JEV,
     [("    if hashlib.sha256(raw_brief).hexdigest() != state[\"brief_sha256\"]:", "    if False:")], T_JEV),
    ("jev: facts nested in the state are accepted", JEV,
     [("            for where in reserved_keys(state) if where not in SET_BY_HELPER]",
       "            for where in reserved_keys(state) if False]")], T_JEV),
    ("jev: an outcome recorded before the state cannot be adopted (stuck calling)", JEV,
     [('    if c is None or request_number(old) != c or old.get("status") not in ("judged", "unjudged"):',
       "    if True:")], T_JEV),
    ("jev: the outcome is written in place, not published atomically", JEV,
     [("        os.link(tmp, path)\n", "        with open(path, \"xb\") as out:\n            out.write(data[:len(data) // 2])\n"
                                       "        os.link(tmp, path)\n")], T_JEV),
    ("jev: another request's recorded outcome is adopted", JEV,
     [("    if c is None or request_number(old) != c or old.get", "    if c is None or old.get")], T_JEV),
    ("verify: supported threshold lowered", VERIFY,
     [("SUPPORTED, UNSUPPORTED = 0.65, 0.35", "SUPPORTED, UNSUPPORTED = 0.45, 0.35")], T_VERIFY),
    ("verify: quotes off their page sent to Jev", VERIFY,
     [('ask = [r["i"] for r in rows if r["on_page"] or (r["on_page"] is None and r.get("in_findings"))]',
       'ask = [r["i"] for r in rows if "page" in r]')], T_VERIFY),
    ("verify: failed pages refetched on resume", VERIFY,
     [("    todo = [u for u in urls if done[u] is None]", "    todo = urls")], T_VERIFY),
    ("verify: adversarial ellipsis pieces accepted", VERIFY,
     [("MIN_PIECE_WORDS, MAX_GAP, MAX_SPAN = 4, 300, 2000", "MIN_PIECE_WORDS, MAX_GAP, MAX_SPAN = 1, 100000, 2000")],
     T_VERIFY),
    ("verify: pieces matched inside other words", VERIFY,
     [('    return re.compile(r"(?<!\\w)" + re.escape(p) + r"(?!\\w)", re.I)',
       "    return re.compile(re.escape(p), re.I)")], T_VERIFY),
    ("verify: an interrupted Jev batch is asked again on rerun", VERIFY,
     [("        if sent_before is not None and not response.exists() and not error.exists():\n            if not recover:",
       "        if False:\n            if not recover:")], T_VERIFY),
    ("verify: a page in a wrong or unknown charset is read as garbage", VERIFY,
     [("        return raw.decode(used), used\n", "        return raw.decode(\"utf-8\", errors=\"replace\"), used\n")],
     T_VERIFY),
    ("verify: a truncated page counts as 'quote not on its page'", VERIFY,
     [("        elif not r[\"on_page\"] and r.get(\"truncated\"):", "        elif False:")], T_VERIFY),
    ("verify: changed inputs resume silently", VERIFY,
     [("    changed = [k for k in fingerprint if old.get(k) != fingerprint[k]]", "    changed = []")], T_VERIFY),
    ("verify: snapshot files outside the run folder accepted", VERIFY,
     [("        if rel.is_absolute() or not f.is_relative_to(root):", "        if False:")], T_VERIFY),
    ("verify: the connection follows a second DNS answer", VERIFY,
     [("        self.sock = socket.create_connection((self.pinned_ip, self.port), self.timeout)\n        self.peer",
       "        self.sock = socket.create_connection((self.host, self.port), self.timeout)\n        self.peer")],
     T_VERIFY),
    ("setup: success claimed without plugin list showing it enabled", SETUP,
     [("    if not any(p.get(\"installed\") is True and p.get(\"enabled\") is True for p in entries):",
       "    if False:")], T_SETUP),
    ("setup: marketplace add's name falls back silently", SETUP,
     [("    problems = contract_problems(added, MARKETPLACE_ADD_FIELDS)\n",
       "    problems = []\n    added.setdefault(\"marketplaceName\", market)\n    added.setdefault(\"installedRoot\", str(ROOT))\n")],
     T_SETUP),
    ("setup: a hung command's process tree is left running", SETUP,
     [("            stop_tree(proc)\n            code = f\"timeout", "            proc.kill()\n            code = f\"timeout")],
     T_SETUP),
    ("setup: a pipe held by an escaped descendant blocks the setup", SETUP,
     [("            t.join(max(0.0, deadline - time.monotonic()))", "            t.join()")], T_SETUP),
    ("smoke: an allowed outside write or secret read passes the check", SMOKE,
     [('            expect(f"{label} is in permission_denials", denied(path))',
       '            expect(f"{label} is in permission_denials", True)')], T_SMOKE),
    ("smoke: a non-empty folder is prepared over", SMOKE,
     [("    if target.exists() and (not target.is_dir() or any(target.iterdir())):", "    if False:")], T_SMOKE),
    ("setup: parent folder accepted as source", SETUP,
     [('if e.get("source") not in ({"source": "local", "path": "./"}, {"source": "local", "path": "."}):',
       'if False:')], T_SETUP),
    ("setup: rules skill may be invoked implicitly", SETUP,
     [('OWNER_INVOKED = ("dev-round", "loop", "rules")', 'OWNER_INVOKED = ("dev-round", "loop")')], T_SETUP),
]


def main() -> int:
    if "--check-only" in sys.argv[1:]:  # every fault's text occurs exactly once; no test is run
        bad = 0
        for name, rel, edits, _module in FAULTS:
            text = (STAGE / rel).read_text(encoding="utf-8")
            counts = [text.count(old) for old, _ in edits]
            ok = counts == [1] * len(edits)
            bad += not ok
            print(f"{'plantable' if ok else 'NOT PLANTABLE'}  {name} {counts}")
        print(f"{len(FAULTS) - bad} of {len(FAULTS)} faults can be planted")
        return 1 if bad else 0
    survived = 0
    only = sys.argv[sys.argv.index("--only") + 1:] if "--only" in sys.argv else []
    faults = [f for f in FAULTS if not only or f[0].startswith(tuple(only))]
    for name, rel, edits, module in faults:
        with tempfile.TemporaryDirectory(prefix="sijav fault ") as tmp:
            copy = Path(tmp) / "pkg"
            shutil.copytree(STAGE, copy, ignore=IGNORE)
            target = copy / rel
            text = target.read_text(encoding="utf-8")
            counts = [text.count(old) for old, _ in edits]
            if counts != [1] * len(edits):
                print(f"NOT PLANTED  {name}: each edited text must occur once; found {counts}")
                survived += 1
                continue
            for old, new in edits:
                text = text.replace(old, new)
            target.write_text(text, encoding="utf-8")
            r = subprocess.run([sys.executable, "-B", "-m", "unittest", module], cwd=str(copy),
                               capture_output=True, timeout=1200)
            tail = r.stderr.decode("utf-8", "replace").strip().splitlines()[-1:]
            if r.returncode == 0:
                survived += 1
                print(f"SURVIVED     {name} ({module}: {tail})")
            else:
                print(f"caught       {name} ({module}: {tail[0] if tail else r.returncode})")
    print(f"{len(faults) - survived} of {len(faults)} planted faults caught")
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
