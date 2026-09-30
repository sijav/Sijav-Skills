"""Every failure of roast.py names its exact cause; jev gets logic, never code; every run keeps its own record."""

import json

import pytest

import roast


def test_codex_runs_in_the_purpose_session_and_names_failures(monkeypatch):
    """Each roast mode has its own codex session (owner, 2026-09-23), on sol at
    medium effort, passed on every call (owner, 2026-09-28); the runner's exact
    failure cause reaches the reader as a RoastError. The runner's own failures
    are tested in ../codex/test_codex_session.py."""
    seen = {}

    def run(prompt, purpose, **k):
        seen.update(purpose=purpose, **k)
        raise roast.codex_session.CodexError(r"no codex binary at Z:\nowhere\codex.cmd")

    monkeypatch.setattr(roast.codex_session, "run", run)
    with pytest.raises(roast.RoastError, match=r"no codex binary at Z:"):
        roast.codex("hi", search=True, purpose="roast-plan")
    assert seen == {"purpose": "roast-plan", "search": True, "model": "gpt-6.1-sol", "effort": "medium"}


def test_an_empty_key_file_is_named(monkeypatch, tmp_path):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    (tmp_path / "typesafe.key").write_text("  \n")
    monkeypatch.setenv("SIJAV_JEV_KEY_FILE", str(tmp_path / "typesafe.key"))
    with pytest.raises(roast.RoastError, match="is empty"):
        roast.jev_key()


def test_a_key_file_beside_the_script_is_never_read(monkeypatch, tmp_path):
    """Claude Code copies an installed plugin's folder into its cache, key files included."""
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("SIJAV_JEV_KEY_FILE", raising=False)
    monkeypatch.setattr(roast, "HERE", tmp_path)
    (tmp_path / "ts").write_text("a-key-that-must-stay-unread")
    with pytest.raises(roast.RoastError, match="no jev key"):
        roast.jev_key()


def test_codex_prose_instead_of_json_is_quoted():
    with pytest.raises(roast.RoastError, match="(?s)no JSON object.*I think"):
        roast.extract_json("I think the plan is fine.")


def test_an_http_error_carries_status_and_body(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    import typesafe_sdk as ts

    class Refusing:
        def __init__(self, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def system_one(self, **k):
            e = ts.TypeSafeAPIError.__new__(ts.TypeSafeAPIError)
            e.status, e.body = 403, "error code: 1010"
            raise e

    monkeypatch.setattr(ts, "TypeSafeClient", Refusing)
    with pytest.raises(roast.RoastError, match="HTTP 403: error code: 1010") as refused:
        roast.jev({}, {"q": {"type": "noul", "instructions": "?"}})
    assert not isinstance(refused.value, roast.TooLong)


def test_jevs_too_long_refusal_is_told_apart(monkeypatch):
    """The body jev sends when a request is too long."""
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    import typesafe_sdk as ts

    class TooLong:
        def __init__(self, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def system_one(self, **k):
            e = ts.TypeSafeAPIError.__new__(ts.TypeSafeAPIError)
            e.status, e.body = 400, {"detail": {"error_type": "max_tokens_exceeded"}}
            raise e

    monkeypatch.setattr(ts, "TypeSafeClient", TooLong)
    with pytest.raises(roast.TooLong, match="HTTP 400.*max_tokens_exceeded"):
        roast.jev({}, {"q": {"type": "noul", "instructions": "?"}})


def test_a_malformed_question_is_named():
    with pytest.raises(roast.RoastError, match="not a valid jev question"):
        roast.jev({}, {"q": {"type": "nope"}})


# --- a whole run, with codex and jev replaced ---

def a_framing(**change) -> str:
    framed = {
        "state": {"the_plan": "Save each answer as it is given; on resume, show what was saved and ask only for the rest."},
        "questions": {"resume_asks_again": {"type": "noul",
                                            "instructions": "On resume, would the plan ask again for an answer already given?"}},
        "observed": [{"fact": "the plan saves after every answer", "source": "plans/77.md:12"}],
    }
    framed.update(change)
    return json.dumps(framed)


@pytest.fixture
def run(monkeypatch, tmp_path):
    """Runs roast.main in a fresh repository; codex replies from a list, jev refuses the first
    `too_long` requests as too long and then answers 0.10 to every noul. Returns (exit code, what
    codex was sent, what jev was sent)."""
    (tmp_path / ".claude").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")  # jev on unless a test switches it off
    for name in ("SIJAV_JEV", "SIJAV_CODEX", "SIJAV_JEV_KEY_FILE"):
        monkeypatch.delenv(name, raising=False)

    def go(argv, replies, too_long=0, busy=()):
        to_codex, to_jev = [], []

        def codex_run(prompt, purpose, **k):
            to_codex.append({"prompt": prompt, "purpose": purpose, **k})
            if purpose in busy:
                raise roast.codex_session.CodexBusy(f"the {purpose!r} codex session is busy")
            return replies.pop(0)

        def jev(state, questions):
            to_jev.append({"state": state, "questions": questions})
            if len(to_jev) <= too_long:
                raise roast.TooLong('jev answered HTTP 400: {"detail": {"error_type": "max_tokens_exceeded"}}')
            return {"model": "jev-1.13.0", "usage": {"input_tokens": 900, "output_tokens": 9},
                    "answers": {q: {"type": "noul", "noul": 0.1} for q in questions}}

        monkeypatch.setattr(roast.codex_session, "run", codex_run)
        monkeypatch.setattr(roast, "jev", jev)
        return roast.main(argv), to_codex, to_jev

    return go


PLAN = ["plan", "--item", "77", "--title", "Resume a form", "--why", "Nobody types an answer twice",
        "--exit-condition", "a resumed form asks only for what is missing"]


def test_a_plan_roast_always_asks_the_owners_critical_question_about_the_real_ask(run):
    """The owner's critical question goes in the same batch, word for word, however codex framed
    the rest; the ask jev judges against is the asker's own words, not codex's paraphrase."""
    code, to_codex, to_jev = run(PLAN, [a_framing(state={"the_plan": "Save and resume.",
                                                       "what_was_asked_for": "codex's own summary"}),
                                        "My reading."])
    assert code == 0
    assert to_jev[0]["questions"][roast.CRITICAL] == roast.CRITICAL_QUESTION
    assert to_jev[0]["state"]["what_was_asked_for"] == {
        "title": "Resume a form", "why": "Nobody types an answer twice",
        "exit_condition": "a resumed form asks only for what is missing"}
    assert all(c["model"] == "gpt-6.1-sol" and c["effort"] == "medium" for c in to_codex)
    assert to_codex[0]["search"] is True and to_codex[1]["search"] is False


def test_a_search_roast_is_research_on_astra_at_high_effort(run):
    """Research runs on astra at high effort (owner, 2026-09-28); roast.py search is research."""
    code, to_codex, _ = run(["search", "--title", "Which free hosts run a NestJS API?"], [a_framing(), "My reading."])
    assert code == 0
    assert {(c["model"], c["effort"]) for c in to_codex} == {("gpt-6-astra", "high")}


@pytest.mark.parametrize("coded", [
    "Change it to:\n```python\nreturn 0\n```",
    "def resume(form):\n    return form.saved",
    "if (saved) {\n  skip();\n}",
])
def test_code_meant_for_jev_goes_back_to_codex_once_then_stops_the_roast(run, coded, capsys):
    bad = a_framing(state={"the_plan": coded})
    code, to_codex, to_jev = run(PLAN, [bad, bad])
    assert code == 1 and to_jev == []
    assert "state.the_plan" in to_codex[1]["prompt"]
    assert "code would reach jev at state.the_plan" in capsys.readouterr().err


def test_a_framing_corrected_on_the_second_try_goes_to_jev(run):
    code, to_codex, to_jev = run(PLAN, [a_framing(observed=[]), a_framing(), "My reading."])
    assert code == 0 and len(to_jev) == 1
    assert "'observed' has no fact with its source" in to_codex[1]["prompt"]


@pytest.mark.parametrize("prose", [
    "The class of errors this plan misses is a timeout.",
    "Import the old drafts first; then return the item to the board.",
    "If the save fails, the draft is kept {as before} and the person is told.",
    "from the start, every answer is saved",
])
def test_plain_prose_is_not_taken_for_code(prose):
    assert roast.code_in({"the_plan": prose}) == []


def test_the_askers_own_words_with_code_stop_the_roast_before_codex(run, capsys):
    code, to_codex, to_jev = run(["task", "--title", "T", "--why", "```\nrm -rf\n```"], [a_framing()])
    assert code == 1 and to_codex == [] and to_jev == []
    assert "what_was_asked_for.why: a fenced code block" in capsys.readouterr().err


def test_every_checked_fact_reaches_jev_without_its_source(run):
    """The live roast of 2026-09-28 found that a fact kept only in 'observed' never reached jev."""
    code, _, to_jev = run(PLAN, [a_framing(state={"the_plan": "Save and resume.", "checked_facts": ["unsourced"]}),
                                 "My reading."])
    assert code == 0
    assert to_jev[0]["state"]["checked_facts"] == ["the plan saves after every answer"]
    assert "plans/77.md" not in json.dumps(to_jev[0]["state"])


def test_code_in_a_checked_fact_goes_back_to_codex(run):
    coded = a_framing(observed=[{"fact": "the saver does:\ndef save(form):\n    pass", "source": "a.py:1"}])
    code, to_codex, to_jev = run(PLAN, [coded, a_framing(), "My reading."])
    assert code == 0 and len(to_jev) == 1
    assert "code would reach jev at observed[0]: a definition" in to_codex[1]["prompt"]


def test_a_request_jev_finds_too_long_is_shortened_once_and_asked_again(run, tmp_path):
    """jev counts tokens itself (32k for the state plus the longest question); a character guess
    misjudged which requests fit (a 63,613-character request refused, an 85,828-character one accepted)."""
    short = a_framing(state={"the_plan": "Save and resume."})
    code, to_codex, to_jev = run(PLAN, [a_framing(), short, "My reading."], too_long=1)
    assert code == 0 and len(to_jev) == 2
    assert "too long" in to_codex[1]["prompt"] and "32k tokens" in to_codex[1]["prompt"]
    assert to_jev[1]["state"]["the_plan"] == "Save and resume."
    assert roast.CRITICAL in to_jev[1]["questions"]
    record = next((tmp_path / ".claude" / "roasts").glob("*.md")).read_text(encoding="utf-8")
    assert "- shortened once: jev refused the first request as too long" in record


def test_a_request_still_too_long_after_shortening_falls_back_to_codex_alone(run, tmp_path):
    """jev failing is not the end of the review (owner, 2026-09-28: an option to fall back)."""
    code, to_codex, to_jev = run(PLAN, [a_framing(), a_framing(), "My critique."], too_long=2)
    assert code == 0 and len(to_jev) == 2 and len(to_codex) == 3
    assert "push back on it" in to_codex[2]["prompt"]
    record = next((tmp_path / ".claude" / "roasts").glob("*.md")).read_text(encoding="utf-8")
    assert "- jev: not used: jev failed (jev answered HTTP 400" in record
    assert "## codex's critique (codex alone; jev was not used)\n\nMy critique." in record


def test_a_busy_codex_session_makes_the_run_use_its_own(run, tmp_path):
    """A loop session's plan roast and a manual roast in the same project collided on 2026-09-28."""
    code, to_codex, _ = run(PLAN, [a_framing(), "My reading."], busy={"roast-plan"})
    assert code == 0 and to_codex[0]["purpose"] == "roast-plan"
    own = to_codex[1]["purpose"]
    assert own.startswith("roast-plan-") and to_codex[2]["purpose"] == own
    record = next((tmp_path / ".claude" / "roasts").glob("*.md")).read_text(encoding="utf-8")
    assert f"session {own}" in record


def test_the_session_option_picks_the_conversation(run):
    code, to_codex, _ = run(PLAN + ["--session", "roast-laws"], [a_framing(), "My reading."])
    assert code == 0 and {c["purpose"] for c in to_codex} == {"roast-laws"}


def test_jev_switched_off_means_codex_reviews_alone(run, monkeypatch, tmp_path):
    monkeypatch.setenv("SIJAV_JEV", "off")
    code, to_codex, to_jev = run(PLAN, ["My critique."])
    assert code == 0 and to_jev == [] and len(to_codex) == 1
    prompt = to_codex[0]["prompt"]
    assert "push back on it" in prompt and "Say whether anything is critical" in prompt
    assert to_codex[0]["model"] == "gpt-6.1-sol" and to_codex[0]["effort"] == "medium"
    record = next((tmp_path / ".claude" / "roasts").glob("*.md")).read_text(encoding="utf-8")
    assert "- jev: not used: switched off (SIJAV_JEV=off); codex reviewed alone" in record


def test_no_jev_key_falls_back_to_codex_alone(run, monkeypatch, tmp_path):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setenv("SIJAV_JEV_KEY_FILE", str(tmp_path / "missing.key"))
    code, to_codex, to_jev = run(PLAN, ["My critique."])
    assert code == 0 and to_jev == []
    record = next((tmp_path / ".claude" / "roasts").glob("*.md")).read_text(encoding="utf-8")
    assert "- jev: not used: no jev key: SIJAV_JEV_KEY_FILE names" in record and "missing.key" in record


def test_the_key_file_setting_wins(monkeypatch, tmp_path):
    (tmp_path / "typesafe.key").write_text("from-the-file\n", encoding="utf-8")
    monkeypatch.setenv("SIJAV_JEV_KEY_FILE", str(tmp_path / "typesafe.key"))
    monkeypatch.setenv("TYPESAFE_API_KEY", "from-the-environment")
    assert roast.jev_key() == "from-the-file"


def test_codex_switched_off_runs_no_roast(run, monkeypatch, capsys):
    monkeypatch.setenv("SIJAV_CODEX", "off")
    code, to_codex, to_jev = run(PLAN, [])
    assert code == 3 and to_codex == [] and to_jev == []
    assert "codex is switched off (SIJAV_CODEX=off). Review the work yourself" in capsys.readouterr().err


def test_each_roast_keeps_its_own_full_record(run, tmp_path):
    """Two roasts leave two records (they used to overwrite one file); each keeps the asker's words,
    the checked facts with sources, jev's numbers in jev's terms, the served model and usage, codex's
    reading marked as codex's, and the exact request and reply."""
    run(PLAN, [a_framing(), "First reading."])
    run(PLAN, [a_framing(), "Second reading."])
    records = sorted((tmp_path / ".claude" / "roasts").glob("*-plan-77-*.md"))
    assert len(records) == 2
    text = records[0].read_text(encoding="utf-8") + records[1].read_text(encoding="utf-8")
    for must in ("- why: Nobody types an answer twice", "the plan saves after every answer (plans/77.md:12)",
                 "probability true 0.10", "jev-1.13.0 (asked for jev-latest); 900 input tokens",
                 "## codex's reading (its interpretation, not jev's)", "First reading.", "Second reading.",
                 '"input_tokens": 900', f'"{roast.CRITICAL}"'):
        assert must in text, must
    assert "confidence" not in text.split("## jev's answers")[1].split("## codex's reading")[0]
    latest = (tmp_path / ".claude" / "roast-result.md").read_text(encoding="utf-8")
    assert latest.startswith("Latest roast. Its own record: ") and "Second reading." in latest


def test_a_choice_and_a_score_are_shown_with_their_spread():
    assert roast.shown({"type": "choice", "choice": "b", "confidence": 0.5,
                        "probabilities": {"a": 0.3, "b": 0.7}}) == "picked b (confidence 0.50); probabilities: b 0.70, a 0.30"
    assert roast.shown({"type": "score", "score": 1.7, "confidence": 0.9, "legend": {"0": "low", "2": "high"},
                        "probabilities": {"0": 0.1, "2": 0.9}}).startswith("expected level 1.70 (confidence 0.90); levels: 2 = high 0.90")
