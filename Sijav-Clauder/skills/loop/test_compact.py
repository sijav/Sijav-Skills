"""compact.py: the loop skill's post-compaction hook puts the project's
law back in context -- once, and only for an armed, unpaused loop."""

import io
import json
import sys

import pytest

import compact


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "demo-loop.local.md").write_text(
        "---\niteration: 3\n---\n\n# Rules\n- One.\n", encoding="utf-8"
    )
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    return tmp_path


def run(monkeypatch, capsys, event):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(event)))
    assert compact.main() == 0
    return capsys.readouterr()


def logged(project):
    return (project / ".claude" / "loop-after-compact.log").read_text(encoding="utf-8")


def test_the_law_comes_back_with_the_order_to_run_step_one(
    project, monkeypatch, capsys
):
    out = json.loads(run(monkeypatch, capsys, {"source": "compact"}).out)
    context = out["hookSpecificOutput"]["additionalContext"]
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert context.startswith("A COMPACTION JUST HAPPENED. The loop law is below.")
    assert context.endswith("\n\n# Rules\n- One.\n")
    assert "iteration: 3" not in context
    assert "source=compact printed" in logged(project)


def test_found_from_a_folder_below_the_project(project, monkeypatch, capsys):
    below = project / "server" / "app"
    below.mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(below))
    assert "# Rules" in run(monkeypatch, capsys, {"source": "compact"}).out


def test_a_project_with_its_own_hook_gets_one_copy_not_two(
    project, monkeypatch, capsys
):
    settings = {"hooks": {"SessionStart": [{"matcher": "compact", "hooks": []}]}}
    (project / ".claude" / "settings.local.json").write_text(
        json.dumps(settings), encoding="utf-8"
    )
    assert run(monkeypatch, capsys, {"source": "compact"}).out == ""
    assert "deferred: the project runs its own compaction hook" in logged(project)


def test_a_paused_loop_gets_nothing(project, monkeypatch, capsys):
    (project / ".stop").write_text("", encoding="utf-8")
    assert run(monkeypatch, capsys, {"source": "compact"}).out == ""
    assert "paused (.stop): nothing printed" in logged(project)


def two_loops(project):
    """Two loops in one project, each law naming its own session and areas."""
    (project / ".claude" / "demo-loop.local.md").unlink()
    (project / ".claude" / "a-loop.local.md").write_text(
        "---\nsession: \"session-a\"\nareas: back,ai\n---\n\n# Law A\n", encoding="utf-8"
    )
    (project / ".claude" / "b-loop.local.md").write_text(
        "---\nsession: session-b\nareas: dashboard\n---\n\n# Law B\n", encoding="utf-8"
    )


def test_each_session_gets_only_its_own_law_and_areas(project, monkeypatch, capsys):
    two_loops(project)
    a = json.loads(run(monkeypatch, capsys, {"source": "compact", "session_id": "session-a"}).out)
    context_a = a["hookSpecificOutput"]["additionalContext"]
    assert "# Law A" in context_a and "# Law B" not in context_a
    assert "areas: back,ai." in context_a and "todo next --area back,ai" in context_a
    b = json.loads(run(monkeypatch, capsys, {"source": "compact", "session_id": "session-b"}).out)
    context_b = b["hookSpecificOutput"]["additionalContext"]
    assert "# Law B" in context_b and "# Law A" not in context_b
    assert "todo next --area dashboard" in context_b


def test_a_session_no_law_names_gets_nothing(project, monkeypatch, capsys):
    two_loops(project)
    assert run(monkeypatch, capsys, {"source": "compact", "session_id": "someone-else"}).out == ""
    assert run(monkeypatch, capsys, {"source": "compact"}).out == ""
    assert "no law names session someone-else: nothing printed" in logged(project)


def test_a_law_naming_no_session_counts_only_when_it_is_the_only_one(project, monkeypatch, capsys):
    assert "# Rules" in run(monkeypatch, capsys, {"source": "compact", "session_id": "any"}).out
    (project / ".claude" / "other-loop.local.md").write_text(
        "---\nsession: session-b\n---\n\n# Law B\n", encoding="utf-8"
    )
    assert run(monkeypatch, capsys, {"source": "compact", "session_id": "any"}).out == ""


def test_one_loop_pauses_alone(project, monkeypatch, capsys):
    two_loops(project)
    (project / ".claude" / "a-loop.stop").write_text("", encoding="utf-8")
    assert run(monkeypatch, capsys, {"source": "compact", "session_id": "session-a"}).out == ""
    assert "paused (a-loop.stop): nothing printed" in logged(project)
    assert "# Law B" in run(monkeypatch, capsys, {"source": "compact", "session_id": "session-b"}).out


def test_no_law_means_nothing_and_says_where_it_looked(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    captured = run(monkeypatch, capsys, {})
    assert captured.out == ""
    assert f"no .claude/*loop*.local.md above {tmp_path}" in captured.err
