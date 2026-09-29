"""search: one codex web search per question, with its sources, saved as a record."""

import pytest

import search as s


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / ".claude").mkdir()
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_a_search_asks_codex_once_with_its_web_search_on(project, monkeypatch, capsys):
    calls = []

    def fake_run(prompt, purpose, **k):
        calls.append((prompt, purpose, k))
        return "## Answer\nIt is 3.2.\n## Sources\n- Notes | https://x.example | 2026-09-01 | \"3.2\""

    monkeypatch.setattr(s.cs, "run", fake_run)
    assert s.main(["What", "version", "is", "X?"]) == 0
    prompt, purpose, k = calls[0]
    assert purpose == "search" and k["search"] is True and k["fresh"] is True
    assert k["model"] == "gpt-6-luna" and k["effort"] == "high"
    assert "Question: What version is X?" in prompt and "exact quote" in prompt
    saved = list((project / ".claude" / "searches").glob("*-what-version-is-x.md"))
    assert len(saved) == 1 and "It is 3.2." in saved[0].read_text(encoding="utf-8")
    assert "It is 3.2." in capsys.readouterr().out


def test_a_busy_search_conversation_gets_a_fresh_one(project, monkeypatch):
    purposes = []

    def fake_run(prompt, purpose, **k):
        purposes.append(purpose)
        if purpose == "search":
            raise s.cs.CodexBusy("busy")
        return "ok"

    monkeypatch.setattr(s.cs, "run", fake_run)
    assert s.main(["q"]) == 0
    assert purposes[0] == "search" and purposes[1].startswith("search-")


def test_switched_off_codex_sends_nothing_and_says_so(project, monkeypatch, capsys):
    def off(*a, **k):
        raise s.cs.CodexOff("codex is switched off (SIJAV_CODEX=off); nothing was sent.")

    monkeypatch.setattr(s.cs, "run", off)
    assert s.main(["q"]) == 3
    assert "your own web tools" in capsys.readouterr().err
    assert not (project / ".claude" / "searches").exists()
