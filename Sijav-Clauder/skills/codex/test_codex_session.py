"""codex_session: one codex session per purpose, in the project's .claude
folder; every failure names its exact cause and keeps the full log."""

import json
import subprocess

import pytest

import codex_session as cs


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / ".claude").mkdir()
    fake = tmp_path / "codex.cmd"
    fake.write_text("")
    monkeypatch.setattr(cs, "BINARIES", [str(fake)])
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.delenv("SIJAV_CODEX", raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def fake_codex(replies, calls):
    """A codex stand-in: records argv, writes the -o reply, emits a thread id."""

    def run(argv, **k):
        calls.append(argv)
        code, text, err = replies.pop(0)
        if code == 0:
            out = argv[argv.index("-o") + 1]
            with open(out, "w", encoding="utf-8") as fh:
                fh.write(text)
        events = json.dumps({"type": "thread.started", "thread_id": "T-1"})
        return subprocess.CompletedProcess(argv, code, events + "\n", err)

    return run


def test_a_busy_thread_is_named_and_no_other_binary_is_tried(project, monkeypatch):
    """Another run writing to the same thread (seen 2026-09-28): another binary cannot help."""
    calls = []
    second = project / "codex2.cmd"
    second.write_text("")
    monkeypatch.setattr(cs, "BINARIES", [str(project / "codex.cmd"), str(second)])
    monkeypatch.setattr(cs.subprocess, "run", fake_codex([(0, "one", "")], calls))
    cs.run("x", "roast-plan")
    busy = "thread-store conflict: thread T-1 already has an active writer"
    monkeypatch.setattr(cs.subprocess, "run", fake_codex([(1, "", busy), (0, "never", "")], calls))
    with pytest.raises(cs.CodexBusy, match="busy: another run is writing to its thread T-1"):
        cs.run("y", "roast-plan")
    assert len(calls) == 2  # the first call, then one busy attempt; the second binary was not tried


def test_the_codex_on_the_path_is_not_tried_twice():
    known = [r"C:\Program Files\nodejs\codex.cmd"]
    assert cs.with_path_codex(known, r"C:\Program Files\nodejs\codex.CMD") == known
    assert cs.with_path_codex(known, None) == known
    assert cs.with_path_codex(known, r"D:\tools\codex.exe") == known + [r"D:\tools\codex.exe"]


def test_a_purpose_starts_a_session_then_resumes_it(project, monkeypatch):
    calls = []
    monkeypatch.setattr(cs.subprocess, "run", fake_codex([(0, "one", ""), (0, "two", "")], calls))
    assert cs.run("first", "plan-1362") == "one"
    assert cs.run("second", "plan-1362") == "two"
    assert "resume" not in calls[0]
    # `codex exec resume [OPTIONS] [SESSION_ID] [PROMPT]`: the id sits before `-`
    assert "resume" in calls[1] and calls[1][-2:] == ["T-1", "-"]
    saved = json.loads((project / ".claude/codex-sessions/plan-1362.json").read_text())
    assert saved["thread_id"] == "T-1" and saved["calls"] == 2


def test_a_session_keeps_its_thread_but_takes_the_model_it_is_given(project, monkeypatch):
    calls = []
    monkeypatch.setattr(cs.subprocess, "run", fake_codex([(0, "a", ""), (0, "b", ""), (0, "c", "")], calls))
    cs.run("first", "research", model="gpt-6-astra")
    cs.run("second", "research")                       # no model given: the default, not the saved astra
    cs.run("third", "research", model="gpt-6-luna")
    assert [c[c.index("-m") + 1] for c in calls] == ["gpt-6-astra", cs.DEFAULT_MODEL, "gpt-6-luna"]
    assert all("resume" in c for c in calls[1:])       # the same session throughout


def test_different_purposes_keep_different_sessions(project, monkeypatch):
    calls = []
    monkeypatch.setattr(cs.subprocess, "run", fake_codex([(0, "a", ""), (0, "b", "")], calls))
    cs.run("x", "plan-1")
    cs.run("y", "research")
    assert "resume" not in calls[1]


def test_every_call_leaves_its_full_log(project, monkeypatch):
    monkeypatch.setattr(cs.subprocess, "run", fake_codex([(0, "ok", "")], []))
    cs.run("x", "research")
    logs = list((project / ".claude/codex-sessions/logs").glob("research-*.log"))
    text = logs[0].read_text(encoding="utf-8")
    assert "command:" in text and "--- stdout" in text and "--- exit: 0" in text


def test_a_used_up_allowance_stops_and_says_to_ask_the_owner(project, monkeypatch):
    calls = []
    monkeypatch.setattr(
        cs.subprocess, "run",
        fake_codex([(1, "", "usage limit reached"), (0, "answer", "")], calls),
    )
    with pytest.raises(cs.CodexError, match="(?s)usage limit reached.*change the codex account"):
        cs.run("x", "research")
    assert [c[c.index("-m") + 1] for c in calls] == ["gpt-6-sol"]   # no gpt-reserve, no second try


def test_a_full_thread_is_retired_and_a_new_one_started(project, monkeypatch):
    calls = []
    monkeypatch.setattr(cs.subprocess, "run", fake_codex([(0, "one", "")], calls))
    cs.run("x", "plan-9")
    monkeypatch.setattr(
        cs.subprocess, "run",
        fake_codex([(1, "", "Codex ran out of room in the model's context window"), (0, "fresh", "")], calls),
    )
    assert cs.run("y", "plan-9") == "fresh"
    assert (project / ".claude/codex-sessions/plan-9.T-1.json").is_file()
    assert "resume" not in calls[-1]


def test_a_timeout_names_itself_and_the_log(project, monkeypatch):
    def boom(argv, **k):
        raise subprocess.TimeoutExpired(cmd="codex", timeout=900)

    monkeypatch.setattr(cs.subprocess, "run", boom)
    with pytest.raises(cs.CodexError, match=r"(?s)Full log: .*exit timeout"):
        cs.run("x", "research")


def test_no_binary_names_the_paths(project, monkeypatch):
    monkeypatch.setattr(cs, "BINARIES", [r"Z:\nowhere\codex.cmd"])
    with pytest.raises(cs.CodexError, match=r"no codex binary at Z:"):
        cs.run("x", "research")


def test_a_bad_purpose_is_named(project):
    with pytest.raises(cs.CodexError, match="not a session name"):
        cs.run("x", "Plan 1362!")


def test_the_topmost_claude_folder_is_the_project(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    (tmp_path / ".claude").mkdir()
    sub = tmp_path / "sub-repository"
    (sub / ".claude").mkdir(parents=True)
    assert cs.project_root(start=sub) == tmp_path.resolve()


def test_switched_off_codex_starts_nothing(project, monkeypatch, capsys):
    """SIJAV_CODEX=off (owner, 2026-09-28: "an option to disable/fallback"): nothing is
    started, and the caller does the work another way."""
    calls = []
    monkeypatch.setattr(cs.subprocess, "run", fake_codex([(0, "never", "")], calls))
    monkeypatch.setenv("SIJAV_CODEX", "off")
    with pytest.raises(cs.CodexOff, match="switched off"):
        cs.run("x", "research")
    (project / "prompt.md").write_text("x", encoding="utf-8")
    assert cs.main([str(project / "prompt.md"), "--purpose", "research"]) == 3
    assert calls == []
    assert "CODEX OFF" in capsys.readouterr().err
