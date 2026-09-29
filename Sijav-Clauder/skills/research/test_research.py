"""research: plan, search side by side, close the gaps, write once, check every citation."""

import json
import re

import pytest

import research as r

PLAN = {"restated_question": "Which X?", "assumptions": [],
        "sub_questions": [{"question": "What is A?", "look_for": "a version"},
                          {"question": "What is B?", "look_for": ""}]}
GAPS = {"gaps": ["C is unknown"], "conflicts": [], "follow_ups": [{"question": "What is C?", "look_for": ""}]}
FOUND = {"What is A?": '## Answer\nA is 3.\n## Sources\n- A notes | https://a.example | 2026-09-01 | "A is at version 3" (supports: A)',
         "What is B?": '## Answer\nB is 5.\n## Sources\n- B notes | https://b.example | no date | "B — five" (supports: B)',
         "What is C?": '## Answer\nC is 7.\n## Sources\n- C notes | https://c.example | 2026-08-01 | "C ships 7" (supports: C)'}
CLAIMS = {"claims": [{"n": 1, "claim": "A is 3", "source": "https://a.example", "quote": "A is at version 3"},
                     {"n": 1, "claim": "A came first", "source": "https://a.example", "quote": "A is at version 3"},
                     {"n": 2, "claim": "B is 5", "source": "https://b.example", "quote": "“B - five”"},
                     {"n": 3, "claim": "C is 9", "source": "https://c.example", "quote": "C ships 9"}]}
REPORT = "# X\n## Summary\nA is 3 [1], B is 5 [2].\n## Sources\n[1] A notes | https://a.example\n\n```json\n" + json.dumps(CLAIMS) + "\n```"


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / ".claude").mkdir()
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def codex(monkeypatch):
    """A codex stand-in that answers by step, and remembers every call."""
    calls = []

    def run(prompt, purpose, **k):
        calls.append((prompt, purpose, k))
        if purpose.endswith("-plan"):
            return "Here is the plan:\n```json\n" + json.dumps(PLAN) + "\n```"
        if "-gaps" in purpose:
            return json.dumps(GAPS)
        if purpose.endswith("-report"):
            return REPORT
        return next(v for q, v in FOUND.items() if f"Question: {q}" in prompt)

    monkeypatch.setattr(r.cs, "run", run)
    return calls


def is_search(purpose):
    return re.search(r"-r\d+-q\d+$", purpose) is not None


def no_jev(monkeypatch):
    monkeypatch.setattr(r.jevlib, "without_jev", lambda: "switched off (SIJAV_JEV=off)")


def test_a_run_plans_searches_fills_gaps_writes_once_and_records_it(project, codex, monkeypatch, capsys):
    no_jev(monkeypatch)
    assert r.main(["Which", "X?", "--why", "to pick one", "--depth", "standard"]) == 0
    steps = [p.rsplit("-", 1)[-1] for _, p, _ in codex]
    assert steps[0] == "plan" and steps[-1] == "report"
    assert sorted(steps[1:3]) == ["q1", "q2"] and steps[3] == "gaps2" and steps[4] == "q1"
    searches = [(p, k) for _, p, k in codex if is_search(p)]
    assert all(k["search"] and k["fresh"] and k["model"] == "gpt-6-luna" for _, k in searches)
    assert len({p for p, _ in searches}) == 3  # every search its own conversation, so they can run side by side
    thinking = [k for _, p, k in codex if not is_search(p)]
    assert all(not k.get("search") and k["model"] == "gpt-6-astra" and k["effort"] == "high" for k in thinking)
    assert "(A good answer covers: a version)" in codex[1][0] + codex[2][0]
    folder = next((project / ".claude" / "research").iterdir())
    for name in ("brief.md", "plan.json", "round-1-q1.md", "round-1-q2.md", "gaps-2.json", "round-2-q1.md",
                 "check.md", "report.md"):
        assert (folder / name).is_file(), name
    report = (folder / "report.md").read_text(encoding="utf-8")
    assert "```json" not in report and "## Citation check" in report
    assert "why: to pick one" in (folder / "brief.md").read_text(encoding="utf-8")
    assert "C is unknown" in codex[-1][0]  # the gaps reach the writer
    assert "## Citation check" in capsys.readouterr().out
    assert all(r.cs.PURPOSE_RE.match(p) for _, p, _ in codex)  # every conversation name is one codex accepts


def test_quotes_are_looked_for_in_the_findings(project, codex, monkeypatch):
    """A quote the searches never brought back was not read in a source ("C ships 9"); curly
    quotes and dashes do not count as a difference ("B - five" against "B — five")."""
    no_jev(monkeypatch)
    r.main(["Which", "X?"])
    check = next((project / ".claude" / "research").iterdir()).joinpath("check.md").read_text(encoding="utf-8")
    rows = [line for line in check.splitlines() if line.startswith("| ") and line[2].isdigit()]
    assert len(rows) == 4  # one row per claim, even when two claims cite the same source
    assert rows[2].split("|")[4].strip() == "yes"
    assert rows[3].endswith("| quote not in the findings |")
    assert "jev did not check the citations: switched off (SIJAV_JEV=off)." in check


def test_jev_judges_each_quote_against_its_claim(project, codex, monkeypatch):
    asked = []

    def fake_jev(state, questions):
        asked.append(questions)
        p = {"k0": 0.91, "k1": 0.50, "k2": 0.35, "k3": 0.97}
        return {"answers": {q: {"type": "noul", "noul": p[q]} for q in questions}}

    monkeypatch.setattr(r.jevlib, "without_jev", lambda: "")
    monkeypatch.setattr(r.jevlib, "jev", fake_jev)
    r.main(["Which", "X?"])
    assert len(asked) == 1 and set(asked[0]) == {"k0", "k1", "k2", "k3"}
    q = asked[0]["k1"]
    assert q["type"] == "noul" and q["instructions"]["claim"] == "A came first"
    assert set(q["criteria"]) == {"true", "false"}
    check = next((project / ".claude" / "research").iterdir()).joinpath("check.md").read_text(encoding="utf-8")
    rows = [line for line in check.splitlines() if line.startswith("| ") and line[2].isdigit()]
    assert rows[0].endswith("| 0.91 | supported |")
    assert rows[1].endswith("| 0.50 | unclear |")
    assert rows[2].endswith("| 0.35 | not supported |")  # 0.35 is outside jev's undecided band
    assert rows[3].endswith("| 0.97 | quote not in the findings |")  # jev cannot vouch for an unread quote


def test_a_failing_jev_leaves_the_quote_check_and_says_why(project, codex, monkeypatch):
    def broken(state, questions):
        raise r.jevlib.JevError("jev answered HTTP 503: down")

    monkeypatch.setattr(r.jevlib, "without_jev", lambda: "")
    monkeypatch.setattr(r.jevlib, "jev", broken)
    assert r.main(["Which", "X?"]) == 0
    check = next((project / ".claude" / "research").iterdir()).joinpath("check.md").read_text(encoding="utf-8")
    assert "jev did not check the citations: jev failed: jev answered HTTP 503: down." in check


def test_plan_only_stops_and_an_edited_plan_is_run(project, codex, monkeypatch, capsys):
    no_jev(monkeypatch)
    assert r.main(["Which", "X?", "--plan-only"]) == 0
    assert [p.rsplit("-", 1)[-1] for _, p, _ in codex] == ["plan"]
    plan_path = next((project / ".claude" / "research").iterdir()) / "plan.json"
    assert "--plan" in capsys.readouterr().out
    edited = json.loads(plan_path.read_text(encoding="utf-8"))
    edited["sub_questions"] = edited["sub_questions"][1:]  # the owner dropped A
    plan_path.write_text(json.dumps(edited), encoding="utf-8")
    codex.clear()
    assert r.main(["Which", "X?", "--depth", "quick", "--plan", str(plan_path)]) == 0
    steps = [p.rsplit("-", 1)[-1] for _, p, _ in codex]
    assert steps == ["q1", "report"]  # no new plan, one search, no gap round at quick depth
    assert "Question: What is B?" in codex[0][0]


def test_a_failed_search_is_recorded_and_the_run_goes_on(project, codex, monkeypatch):
    no_jev(monkeypatch)
    real = r.cs.run

    def one_fails(prompt, purpose, **k):
        if "Question: What is A?" in prompt:
            raise r.cs.CodexError("codex timed out")
        return real(prompt, purpose, **k)

    monkeypatch.setattr(r.cs, "run", one_fails)
    assert r.main(["Which", "X?", "--depth", "quick"]) == 0
    folder = next((project / ".claude" / "research").iterdir())
    failed = next(f for f in folder.glob("round-1-q*.md") if "What is A?" in f.read_text(encoding="utf-8"))
    assert "SEARCH FAILED: codex timed out" in failed.read_text(encoding="utf-8")
    report_prompt = next(p for p, purpose, _ in codex if purpose.endswith("-report"))
    assert "SEARCH FAILED" not in report_prompt and "What is B?" in report_prompt


def test_bad_json_from_codex_is_named(project, monkeypatch, capsys):
    monkeypatch.setattr(r.cs, "run", lambda prompt, purpose, **k: "I would rather not.")
    assert r.main(["Which", "X?"]) == 1
    assert "no valid JSON for the plan" in capsys.readouterr().err


def test_switched_off_codex_sends_nothing_and_says_so(project, monkeypatch, capsys):
    def off(*a, **k):
        raise r.cs.CodexOff("codex is switched off (SIJAV_CODEX=off); nothing was sent.")

    monkeypatch.setattr(r.cs, "run", off)
    assert r.main(["Which", "X?"]) == 3
    assert "Research with your own tools" in capsys.readouterr().err


def test_switched_off_codex_during_the_searches_stops_the_run(project, codex, monkeypatch):
    real = r.cs.run

    def off_after_plan(prompt, purpose, **k):
        if "-r1-" in purpose:
            raise r.cs.CodexOff("codex is switched off (SIJAV_CODEX=off); nothing was sent.")
        return real(prompt, purpose, **k)

    monkeypatch.setattr(r.cs, "run", off_after_plan)
    assert r.main(["Which", "X?"]) == 3


def test_the_models_can_be_chosen(project, codex, monkeypatch):
    no_jev(monkeypatch)
    r.main(["Which", "X?", "--depth", "quick", "--effort", "xhigh",
            "--search-model", "gpt-6-sol", "--search-effort", "medium"])
    thinking = [k for _, p, k in codex if not is_search(p)]
    searching = [k for _, p, k in codex if is_search(p)]
    assert thinking and all((k["model"], k["effort"]) == ("gpt-6-astra", "xhigh") for k in thinking)
    assert searching and all((k["model"], k["effort"]) == ("gpt-6-sol", "medium") for k in searching)
    brief = next((project / ".claude" / "research").iterdir()).joinpath("brief.md").read_text(encoding="utf-8")
    assert "gpt-6-astra at xhigh effort plans" in brief and "gpt-6-sol at medium effort searches" in brief
