"""loop_stop.py: the skill's Stop hook sends a session its own loop file back, and nothing else."""

import io
import json
import sys

import pytest

import loop_stop

LOOP = (
    "---\ndriver: skill\nsession: \"session-a\"\nareas: dashboard,skills\nactive: true\n"
    "iteration: 0\nmax_iterations: 3\ncompletion_promise: \"ALL DONE\"\n---\n\n# The law\n- Work.\n"
)


@pytest.fixture
def project(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "mine-loop.local.md").write_text(LOOP, encoding="utf-8")
    return tmp_path


def stop(monkeypatch, capsys, project, session, transcript=None):
    event = {"hook_event_name": "Stop", "session_id": session, "cwd": str(project)}
    if transcript:
        event["transcript_path"] = str(transcript)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(event)))
    assert loop_stop.main() == 0
    out = capsys.readouterr().out
    return json.loads(out) if out.strip() else None


def law(project):
    return (project / ".claude" / "mine-loop.local.md").read_text(encoding="utf-8")


def test_its_own_session_gets_its_loop_file_back_and_the_count_moves(project, monkeypatch, capsys):
    out = stop(monkeypatch, capsys, project, "session-a")
    assert out["decision"] == "block"
    assert out["reason"].startswith("Loop mine-loop, iteration 1. Your areas: dashboard,skills")
    assert out["reason"].endswith("# The law\n- Work.\n")
    assert "iteration: 1" in law(project)
    assert "loop file sent back (iteration 1)" in (project / ".claude" / "loop-stop.log").read_text(encoding="utf-8")


def test_another_session_gets_nothing(project, monkeypatch, capsys):
    assert stop(monkeypatch, capsys, project, "session-b") is None
    assert stop(monkeypatch, capsys, project, "") is None
    assert "iteration: 0" in law(project)


def test_a_loop_the_project_drives_is_left_to_the_project(project, monkeypatch, capsys):
    (project / ".claude" / "mine-loop.local.md").write_text(LOOP.replace("driver: skill\n", ""), encoding="utf-8")
    assert stop(monkeypatch, capsys, project, "session-a") is None


def test_paused_by_its_own_stop_file_or_the_projects(project, monkeypatch, capsys):
    (project / ".claude" / "mine-loop.stop").write_text("", encoding="utf-8")
    assert stop(monkeypatch, capsys, project, "session-a") is None
    (project / ".claude" / "mine-loop.stop").unlink()
    (project / ".stop").write_text("", encoding="utf-8")
    assert stop(monkeypatch, capsys, project, "session-a") is None
    assert "iteration: 0" in law(project)


def test_the_promise_finishes_the_loop(project, monkeypatch, capsys):
    transcript = project / "transcript.jsonl"
    lines = [{"type": "assistant", "message": {"content": [{"type": "text", "text": "working"}]}},
             {"type": "user", "message": {"content": "next"}},
             {"type": "assistant", "message": {"content": [{"type": "text", "text": "All checked. <promise>ALL DONE</promise>"}]}}]
    transcript.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    assert stop(monkeypatch, capsys, project, "session-a", transcript) is None
    assert "active: false" in law(project)
    assert stop(monkeypatch, capsys, project, "session-a") is None  # no longer armed


def test_the_iteration_cap_allows_the_stop(project, monkeypatch, capsys):
    for expected in (1, 2, 3):
        assert stop(monkeypatch, capsys, project, "session-a")["systemMessage"] == f"mine-loop: iteration {expected}"
    assert stop(monkeypatch, capsys, project, "session-a") is None


def test_broken_input_never_blocks(project, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    assert loop_stop.main() == 0
    assert capsys.readouterr().out == ""
