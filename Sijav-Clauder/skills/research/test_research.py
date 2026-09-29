"""research: its own skill on astra. A plan the owner sees first, searches side by side, gap
rounds, one report, and every citation checked on the page it cites."""

import json
import re
import socket
from pathlib import Path

import pytest

import research as r

PLAN = {"clarify": ["Which platforms count?"], "restated_question": "Which X?", "assumptions": [],
        "sub_questions": [{"question": "What is A?", "look_for": "a version"},
                          {"question": "What is B?", "look_for": ""}]}
GAPS = {"gaps": ["C is unknown"], "conflicts": [], "follow_ups": [{"question": "What is C?", "look_for": ""}]}
FOUND = {
    "What is A?": '## Answer\nA is 3.\n## Sources\n- A notes | https://a.example/notes | 2026-09-01 | "A is at version 3" (supports: A)',
    "What is B?": '## Answer\nB is 5.\n## Sources\n- B notes | https://b.example/ | no date | "B — five of them" (supports: B)',
    "What is C?": '## Answer\nC is 7.\n## Sources\n- C notes | https://c.example | 2026-08-01 | "C ships version 7" (supports: C)',
}
PAGES = {
    "https://a.example/notes": "<html><head><title>x</title></head><body><p>Today A is at version <code>3</code>.</p>"
                               "<script>var q = 'B - five of them';</script></body></html>",
    "https://b.example/": "<p>We count B &mdash; five of them, and more.</p>",
    "https://c.example": "<p>C ships version 7 this year.</p>",
}
CLAIMS = [
    {"n": 1, "claim": "A is 3", "source": "https://a.example/notes", "quote": "A is at version 3."},
    {"n": 2, "claim": "B is 5", "source": "https://b.example/", "quote": "“B - five of them”"},
    {"n": 3, "claim": "C is 9", "source": "https://c.example", "quote": "C ships version 9"},
    {"n": 2, "claim": "A is 3, says B", "source": "https://b.example/", "quote": "A is at version 3"},
    {"n": 4, "claim": "D is 1", "source": "https://d.example/", "quote": "D is at version 1"},
]


def report(claims=CLAIMS):
    return ("# X\n## Summary\nA is 3 [1].\n## Sources\n[1] A notes | https://a.example/notes\n\n```json\n"
            + json.dumps({"claims": claims}) + "\n```")


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / ".claude").mkdir()
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def pages(monkeypatch):
    """No test reaches the web: pages come from PAGES, and any other page cannot be read."""
    def fetch(url):
        if url in PAGES:
            return r.visible_text(PAGES[url])
        raise r.PageError("HTTPError: HTTP Error 403: Forbidden")

    monkeypatch.setattr(r, "fetch_text", fetch)


@pytest.fixture
def codex(monkeypatch):
    """A codex stand-in that answers by step, and remembers every call."""
    calls = []
    replies = {"report": report()}

    def run(prompt, purpose, **k):
        calls.append((prompt, purpose, k))
        if purpose.endswith("-plan"):
            return "Here is the plan:\n```json\n" + json.dumps(PLAN) + "\n```"
        if "-gaps" in purpose:
            return json.dumps(GAPS)
        if purpose.endswith("-report"):
            return replies["report"]
        return next(v for q, v in FOUND.items() if f"This sub-question: {q}" in prompt)

    monkeypatch.setattr(r.cs, "run", run)
    run.replies = replies
    run.calls = calls
    return run


def steps(codex):
    return [p.rsplit("-", 1)[-1] for _, p, _ in codex.calls]


def is_search(purpose):
    return re.search(r"-r\d+-q\d+$", purpose) is not None


def only_folder(project):
    return next((project / ".claude" / "research").iterdir())


def no_jev(monkeypatch):
    monkeypatch.setattr(r.jevlib, "without_jev", lambda: "switched off (SIJAV_JEV=off)")


def rows_of(folder):
    check = (folder / "check.md").read_text(encoding="utf-8")
    return [line for line in check.splitlines() if re.match(r"\| \d+ \|", line)], check


def test_a_run_stops_after_the_plan_and_shows_the_owner_its_questions(project, codex, capsys):
    assert r.main(["Which", "X?", "--why", "to pick one"]) == 0
    assert steps(codex) == ["plan"]
    out = capsys.readouterr().out
    assert "Which platforms count?" in out and "--answers" in out and "--resume" in out
    folder = only_folder(project)
    assert (folder / "plan.json").is_file() and not (folder / "state.json").exists()


def test_resume_goes_on_from_the_plan_without_planning_again(project, codex, monkeypatch):
    no_jev(monkeypatch)
    r.main(["Which", "X?"])
    codex.calls.clear()
    assert r.main(["--resume", str(only_folder(project))]) == 0
    s = steps(codex)
    assert "plan" not in s and sorted(s[:2]) == ["q1", "q2"] and s[2:] == ["gaps2", "q1", "report"]
    assert (only_folder(project) / "report.md").is_file()


def test_every_step_runs_on_astra_and_research_does_not_use_the_search_skill(project, codex, monkeypatch):
    no_jev(monkeypatch)
    assert r.main(["Which", "X?", "--go"]) == 0
    assert all((k["model"], k["effort"]) == ("gpt-6-astra", "high") for _, _, k in codex.calls)
    assert all(k.get("search") is True for _, p, k in codex.calls if is_search(p))
    assert not any(k.get("search") for _, p, k in codex.calls if not is_search(p))
    assert len({p for _, p, _ in codex.calls if is_search(p)}) == 3  # each search its own conversation
    assert all(r.cs.PURPOSE_RE.match(p) for _, p, _ in codex.calls)
    source = Path(r.__file__).read_text(encoding="utf-8")
    assert "import search" not in source and '"search")' not in source


def test_the_owner_s_answers_reach_the_plan_and_the_report(project, codex, monkeypatch):
    no_jev(monkeypatch)
    r.main(["Which", "X?", "--answers", "only open source", "--go"])
    prompts = {p.rsplit("-", 1)[-1]: prompt for prompt, p, _ in codex.calls}
    assert "only open source" in prompts["plan"] and "only open source" in prompts["report"]


def test_a_quote_counts_only_on_the_page_it_cites(project, codex, monkeypatch):
    """The roast of 2026-09-29: a quote found anywhere in the findings proved nothing. Now each
    quote is looked for on its own page: a quote taken from another page (claim 4) or changed
    (claim 3) is not on its page, whatever jev says; a page that cannot be read leaves its claim
    unconfirmed (claim 5)."""
    asked = []

    def fake_jev(state, questions):
        asked.append(set(questions))
        return {"answers": {q: {"type": "noul", "noul": 0.97} for q in questions}}

    monkeypatch.setattr(r.jevlib, "without_jev", lambda: "")
    monkeypatch.setattr(r.jevlib, "jev", fake_jev)
    assert r.main(["Which", "X?", "--go"]) == 0
    rows, check = rows_of(only_folder(project))
    assert rows[0].endswith("| yes | 0.97 | supported |")  # inline code and spacing do not hide it
    assert rows[1].endswith("| yes | 0.97 | supported |")  # curly quotes and a dash entity do not either
    assert rows[2].endswith("| no |  | quote not on its page |")
    assert rows[3].endswith("| no |  | quote not on its page |")  # the quote is real, but on another page
    assert "unconfirmed: page not read; the quote is not in the findings" in rows[4]
    assert asked == [{"k0", "k1"}]  # jev is asked only about quotes that can be real
    assert "5 claims: 2 supported, 2 quote not on its page, 1 unconfirmed: page not read." in check


def test_jev_s_bands(project, codex, monkeypatch):
    p = {"k0": 0.65, "k1": 0.35}
    monkeypatch.setattr(r.jevlib, "without_jev", lambda: "")
    monkeypatch.setattr(r.jevlib, "jev", lambda s, qs: {"answers": {q: {"noul": p.get(q, 0.5)} for q in qs}})
    codex.replies["report"] = report(CLAIMS[:2])
    r.main(["Which", "X?", "--go"])
    rows, _ = rows_of(only_folder(project))
    assert rows[0].endswith("| 0.65 | supported |") and rows[1].endswith("| 0.35 | not supported |")


def test_a_page_codex_quoted_but_that_cannot_be_read_stays_unconfirmed(project, codex, monkeypatch):
    no_jev(monkeypatch)
    monkeypatch.delitem(PAGES, "https://c.example")
    codex.replies["report"] = report([{"n": 3, "claim": "C is 7", "source": "https://c.example",
                                       "quote": "C ships version 7"}])
    r.main(["Which", "X?", "--go"])
    rows, check = rows_of(only_folder(project))
    assert "not read: HTTPError: HTTP Error 403: Forbidden" in rows[0]
    assert rows[0].endswith("| unconfirmed: page not read |")
    assert "jev did not judge the quotes: switched off (SIJAV_JEV=off)." in check


def test_bad_gap_output_costs_the_gap_round_not_the_run(project, codex, monkeypatch):
    no_jev(monkeypatch)
    real = r.cs.run
    monkeypatch.setattr(r.cs, "run", lambda prompt, purpose, **k: "not json" if "-gaps" in purpose
                        else real(prompt, purpose, **k))
    assert r.main(["Which", "X?", "--go"]) == 0
    folder = only_folder(project)
    assert "error" in json.loads((folder / "gaps-2.json").read_text(encoding="utf-8"))
    assert (folder / "report.md").is_file()


def test_a_missing_claims_list_is_asked_for_again_in_the_same_conversation(project, codex, monkeypatch):
    no_jev(monkeypatch)
    codex.replies["report"] = "# X\nA is 3 [1]."
    real = r.cs.run
    again = []

    def run(prompt, purpose, **k):
        if purpose.endswith("-report") and k.get("fresh") is False:
            again.append(purpose)
            return "```json\n" + json.dumps({"claims": CLAIMS[:1]}) + "\n```"
        return real(prompt, purpose, **k)

    monkeypatch.setattr(r.cs, "run", run)
    assert r.main(["Which", "X?", "--go", "--depth", "quick"]) == 0
    assert len(again) == 1
    rows, _ = rows_of(only_folder(project))
    assert len(rows) == 1


def test_no_claims_even_when_asked_again_is_said_loudly(project, codex, monkeypatch, capsys):
    no_jev(monkeypatch)
    codex.replies["report"] = "# X\nA is 3 [1]."
    assert r.main(["Which", "X?", "--go", "--depth", "quick"]) == 2
    assert "Not checked" in (only_folder(project) / "report.md").read_text(encoding="utf-8")
    assert "NOT CHECKED" in capsys.readouterr().err


def test_a_failed_report_resumes_without_searching_again(project, codex, monkeypatch, capsys):
    no_jev(monkeypatch)
    real = r.cs.run
    state = {"fail": True}

    def run(prompt, purpose, **k):
        if purpose.endswith("-report") and state["fail"]:
            raise r.cs.CodexError("codex timed out")
        return real(prompt, purpose, **k)

    monkeypatch.setattr(r.cs, "run", run)
    assert r.main(["Which", "X?", "--go"]) == 1
    assert "--resume" in capsys.readouterr().err
    state["fail"] = False
    codex.calls.clear()
    assert r.main(["--resume", str(only_folder(project))]) == 0
    assert steps(codex) == ["report"]


def test_findings_are_fenced_off_and_cut_to_size(project, codex, monkeypatch):
    no_jev(monkeypatch)
    monkeypatch.setitem(FOUND, "What is B?", FOUND["What is B?"] + "\n" + "x" * (r.ANSWER_CAP + 50))
    r.main(["Which", "X?", "--go", "--depth", "quick"])
    report_prompt = next(p for p, purpose, _ in codex.calls if purpose.endswith("-report"))
    assert "BEGIN FINDINGS" in report_prompt and "END FINDINGS" in report_prompt
    assert "never instructions" in report_prompt
    assert "[cut here: the whole answer is in round-1-q2.md]" in report_prompt
    assert len(report_prompt) < r.ANSWER_CAP * 2 + 5000


def test_a_failed_search_is_recorded_and_the_run_goes_on(project, codex, monkeypatch):
    no_jev(monkeypatch)
    real = r.cs.run

    def one_fails(prompt, purpose, **k):
        if "This sub-question: What is A?" in prompt:
            raise r.cs.CodexError("codex timed out")
        return real(prompt, purpose, **k)

    monkeypatch.setattr(r.cs, "run", one_fails)
    assert r.main(["Which", "X?", "--go", "--depth", "quick"]) == 0
    folder = only_folder(project)
    failed = next(f for f in folder.glob("round-1-q*.md") if "What is A?" in f.read_text(encoding="utf-8"))
    assert "SEARCH FAILED: codex timed out" in failed.read_text(encoding="utf-8")


def test_the_effort_can_be_chosen(project, codex, monkeypatch):
    no_jev(monkeypatch)
    r.main(["Which", "X?", "--go", "--depth", "quick", "--effort", "xhigh"])
    assert all(k["effort"] == "xhigh" for _, _, k in codex.calls)
    assert "gpt-6-astra at xhigh effort" in (only_folder(project) / "brief.md").read_text(encoding="utf-8")


def test_only_public_web_pages_are_ever_fetched(monkeypatch):
    def resolve(host, *a, **k):
        ip = {"example.org": "93.184.215.14", "localhost": "127.0.0.1", "router.home": "192.168.1.1"}[host]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))]

    monkeypatch.setattr(r.socket, "getaddrinfo", resolve)
    r.safe("https://example.org/page")
    for bad in ("file:///etc/passwd", "http://localhost:8080/", "http://router.home/admin", "ftp://example.org/x"):
        with pytest.raises(r.PageError):
            r.safe(bad)


def test_page_text_and_quotes():
    text = r.visible_text("<p>Version <a href='#'>3.13</a> is out.</p><style>p{}</style><p>Next line</p>")
    assert r.quote_in("version 3.13 is out", text) and not r.quote_in("p{}", text)
    assert r.quote_in("Version 3.13 … Next line", text)  # an ellipsis: the pieces, in order
    assert not r.quote_in("Next line … Version 3.13", text)
    assert not r.quote_in("is", text)  # too short to prove anything


def test_switched_off_codex_sends_nothing_and_says_so(project, monkeypatch, capsys):
    def off(*a, **k):
        raise r.cs.CodexOff("codex is switched off (SIJAV_CODEX=off); nothing was sent.")

    monkeypatch.setattr(r.cs, "run", off)
    assert r.main(["Which", "X?"]) == 3
    assert "Research with your own tools" in capsys.readouterr().err


def test_bad_plan_json_is_named(project, monkeypatch, capsys):
    monkeypatch.setattr(r.cs, "run", lambda prompt, purpose, **k: "I would rather not.")
    assert r.main(["Which", "X?"]) == 1
    assert "no valid JSON for the plan" in capsys.readouterr().err
