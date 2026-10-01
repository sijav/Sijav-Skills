"""Run every offline suite of this package and keep each one's complete output in validation/.

    python -B validation/run_all.py [--source-todo DIR]

Writes validation/<name>.txt per suite (command, folder, interpreter, start and end times, exit
status, full stdout and stderr, unfiltered) and validation/SUMMARY.txt. --source-todo names the
source skills/todo folder whose four board files the copies must equal byte for byte (read only);
by default the Sijav-Clauder/skills/todo folder beside this package. No model, key or external network is used.
"""

import argparse
import datetime as dt
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

STAGE = Path(__file__).resolve().parents[1]
OUT = STAGE / "validation"
BOARD_FILES = ("todo.py", "todo.mjs", "test-parity.py", "test-subtasks.mjs")


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def run(name: str, argv: list[str], note: str = "") -> int | str:
    started = now()
    try:
        r = subprocess.run(argv, cwd=str(STAGE), capture_output=True, timeout=1800)
        code, out, err = r.returncode, r.stdout, r.stderr
    except FileNotFoundError as exc:
        code, out, err = "not run", b"", f"{exc}".encode()
    text = (f"suite: {name}\n{note}command: {subprocess.list2cmdline(argv)}\ncwd: {STAGE}\n"
            f"python: {sys.version}\nstarted: {started}\nended: {now()}\nexit: {code}\n"
            f"----- stdout (complete) -----\n{out.decode('utf-8', 'replace')}\n"
            f"----- stderr (complete) -----\n{err.decode('utf-8', 'replace')}\n----- end -----\n")
    (OUT / f"{name}.txt").write_text(text, encoding="utf-8", newline="\n")
    return code


def compare(source: Path) -> tuple[bool, str]:
    lines, same = [], True
    for f in BOARD_FILES:
        a, b = STAGE / "skills" / "todo" / f, source / f
        try:
            ha = hashlib.sha256(a.read_bytes()).hexdigest()
            hb = hashlib.sha256(b.read_bytes()).hexdigest()
        except OSError as exc:
            same = False
            lines.append(f"{f}: cannot compare ({exc})")
            continue
        same &= ha == hb
        lines.append(f"{f}: {'identical' if ha == hb else 'DIFFERENT'} stage {ha} source {hb}")
    text = f"suite: board-copies\nsource: {source}\nchecked: {now()}\n" + "\n".join(lines) + "\n"
    (OUT / "board-copies.txt").write_text(text, encoding="utf-8", newline="\n")
    return same, text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-todo", default=str(STAGE.parent / "Sijav-Clauder" / "skills" / "todo"))
    a = ap.parse_args()
    py = [sys.executable, "-B"]
    node = shutil.which("node")
    results = {
        "helpers-unittest": run("helpers-unittest", [*py, "-W", "error::ResourceWarning", "-m", "unittest", "-v",
                                                     "tests.test_claude_session", "tests.test_jev",
                                                     "tests.test_jev_sdk", "tests.test_verify", "tests.test_setup",
                                                     "tests.test_permission_smoke", "tests.test_run_all"]),
        "planted-faults": run("planted-faults", [*py, "validation/planted_faults.py"]),
        "loop-unittest": run("loop-unittest", [*py, "-m", "unittest", "-v", "tests.test_loop", "tests.test_native_proof"],
                             "note: the loop adapter's own suites (not changed by the helper work); run to show"
                             " nothing here broke them.\n"),
        "setup-validate": run("setup-validate", [*py, "tools/sijav_codex_setup.py", "--skip-tools"],
                              "note: --skip-tools, so no codex/claude/node version query was run here.\n"),
        "todo-parity": run("todo-parity", [*py, "skills/todo/test-parity.py"]),
        "todo-subtasks": run("todo-subtasks", [node or "node", "skills/todo/test-subtasks.mjs"]),
    }
    same, compared = compare(Path(a.source_todo))
    lines = [f"Sijav-Codex offline validation, {now()}", f"python: {sys.version.split()[0]} at {sys.executable}",
             f"node: {node}", ""]
    lines += [f"{name}: exit {code}" for name, code in results.items()]
    failed = [name for name, code in results.items() if code != 0]  # a suite not run counts as failed
    lines += ["board-copies: " + ("all four identical to the source" if same else "NOT identical; see board-copies.txt"),
              "", "Each suite's complete output is in validation/<suite>.txt.",
              "result: " + ("every suite passed and the board copies are identical" if not failed and same else
                            "FAILED: " + ", ".join(failed + ([] if same else ["board-copies"])))]
    (OUT / "SUMMARY.txt").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print("\n".join(lines))
    return 0 if not failed and same else 1


if __name__ == "__main__":
    sys.exit(main())
