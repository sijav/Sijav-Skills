#!/usr/bin/env python3
"""Roast with jev: codex checks the work and frames typed questions, jev (TypeSafe) judges, codex writes its reading.

  python <this folder>/roast.py plan|task|technical|search \
      [--item ID] [--title T] [--why W] [--exit-condition E] [--did D] [--files F] [--ask Q ...]
      [--session NAME]   # a codex session of its own instead of roast-<mode>, e.g. for a manual roast

Every run keeps its own record in <repo>/.claude/roasts/: what was asked, the facts codex checked with
their sources, the exact state and questions jev got, jev's full reply (served model, usage) and codex's
reading. <repo>/.claude/roast-result.md is a copy of the latest record.
Every failure prints its exact cause and exits non-zero; nothing here gates anything.

Switches (owner, 2026-09-28: "there should be an option to disable/fallback"), set per user or per
project in Claude Code's settings "env":
  SIJAV_JEV=off        run without jev: codex reviews alone and the record says why. The same
                       happens when jev cannot be used: no key, or the TypeSafe SDK is missing.
  SIJAV_JEV_KEY_FILE   the file holding the TypeSafe key, kept outside the plugin; else
                       TYPESAFE_API_KEY.
  SIJAV_CODEX=off      no roast at all (exit 3): the caller reviews the work itself.
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
import uuid
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "codex"))
import codex_session  # noqa: E402  -- the shared runner: one codex session per purpose

JEV_MODEL = "jev-latest"
# Roasts run on sol at medium effort; a search roast is research, on astra at high effort
# (owner, 2026-09-28); sol is gpt-6.1-sol since 2026-09-30. Passed on every call rather than
# left to the runner's default.
ROAST_MODEL = ("gpt-6.1-sol", "medium")
MODEL_OF = {"search": ("gpt-6-astra", "high")}
MODES = ("plan", "task", "technical", "search")
WEB = {
    "plan": " Research current official sources on the web when an outside premise needs checking, and give their URLs.",
    "search": " Research current official sources on the web first, and give their URLs.",
}
# Checked 2026-09-28 by a codex web search of https://docs.typesafe.ai/models.
LIMITS = ("jev's limits (https://docs.typesafe.ai/models): 32k tokens for the state plus the single longest "
          "question, and 64k tokens for the state plus all questions. They count tokens, not characters.")
CRITICAL = "anything_critical"
# The owner's direct question, word for word as agreed with jev in September 2026. A plan roast
# always asks it in the same batch; roast.py adds it itself so it cannot be dropped or reworded.
CRITICAL_QUESTION = {
    "type": "noul",
    "instructions": (
        "Given what_was_asked_for and the_plan's logic, is anything critical: it would definitely break"
        " the whole thing asked for, must be fixed right away, and is neither a later task nor a feature?"
        " True means such a critical problem exists. Small refinements, later tasks, and features do not count."
    ),
}
# jev judges logic; code is Claude's (owner, 2026-09-23). The prompt keeps code away from jev; these
# catch what slips through, in forms plain prose does not take.
CODE_SIGNS = {
    "a fenced code block": re.compile(r"```"),
    "a diff": re.compile(r"^(diff --git |@@ -\d)", re.M),
    "a definition": re.compile(r"^\s*((async\s+)?def|function)\s+\w+\s*\(|^\s*class\s+\w+\s*[(:]", re.M),
    "an import": re.compile(r"^\s*(import\s+[\w.]+(\s+as\s+\w+)?\s*$|from\s+[\w.]+\s+import\s+[\w*(])", re.M),
    "a declaration": re.compile(r"^\s*(const|let|var)\s+\w+\s*=", re.M),
    "a line that opens a block": re.compile(r"\{\s*$", re.M),
}


class RoastError(Exception):
    """A failure whose message is the exact cause, for the reader to act on."""


class JevError(RoastError):
    """jev could not answer: the service refused or failed. The roast falls back to codex alone."""


class TooLong(JevError):
    """jev refused the request as over its token limits (HTTP 400, max_tokens_exceeded)."""


def repo_root(start: Path) -> Path:
    for p in (start, *start.parents):
        if (p / ".git").exists() or (p / ".claude").is_dir():
            return p
    return start


def jev_key() -> str:
    """The TypeSafe key, from the file SIJAV_JEV_KEY_FILE names, else from TYPESAFE_API_KEY.

    Never from a file inside the plugin: Claude Code copies an installed plugin's folder into its
    own cache, key files included (seen 2026-09-28), so the key lives outside the plugin."""
    named = os.environ.get("SIJAV_JEV_KEY_FILE", "").strip()
    if named:
        f = Path(named)
        if not f.is_file():
            raise RoastError(f"no jev key: SIJAV_JEV_KEY_FILE names {f}, which does not exist")
        key = f.read_text(encoding="utf-8").strip()
        if not key:
            raise RoastError(f"the jev key file {f} is empty")
        return key
    key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if key:
        return key
    raise RoastError("no jev key: set SIJAV_JEV_KEY_FILE to the key's file, or TYPESAFE_API_KEY")


def scrub(text: str) -> str:
    """An error message with the jev key blanked, should a service ever echo it back."""
    try:
        key = jev_key()
    except RoastError:
        return text
    return text.replace(key, "[the jev key]")


def switched_off(name: str) -> bool:
    return os.environ.get(name, "on").strip().lower() in {"off", "0", "false", "no"}


def without_jev() -> str:
    """Why this roast runs without jev, or '' when jev can be used."""
    if switched_off("SIJAV_JEV"):
        return "switched off (SIJAV_JEV=off)"
    try:
        jev_key()
    except RoastError as e:
        return str(e)
    try:
        import typesafe_sdk  # noqa: F401
    except ImportError:
        return f"the TypeSafe SDK is not installed for {sys.executable}"
    return ""


def model_of(mode: str) -> tuple[str, str]:
    return MODEL_OF.get(mode, ROAST_MODEL)


# The codex session each roast-<mode> purpose uses in this run, when not its own name: set by
# --session, or when roast-<mode> is busy in another run (a loop session's plan roast and a manual
# roast in the same project collided on 2026-09-28).
SESSIONS: dict[str, str] = {}


def session_for(purpose: str) -> str:
    return SESSIONS.get(purpose, purpose)


def codex(prompt: str, search: bool, purpose: str) -> str:
    """Codex in the `purpose` session (roast-<mode>) under the project's .claude
    folder (owner, 2026-09-23), on its mode's model and effort. Binaries, the
    full log and the used-up allowance message live in the shared runner; its
    exact failure cause becomes a RoastError. When that session is busy in
    another run, this run switches to a session of its own and keeps it."""
    model, effort = model_of(purpose.removeprefix("roast-"))
    try:
        return codex_session.run(prompt, session_for(purpose), search=search, model=model, effort=effort)
    except codex_session.CodexBusy as e:
        if purpose in SESSIONS:
            raise RoastError(str(e)) from e
        SESSIONS[purpose] = f"{purpose}-{uuid.uuid4().hex[:6]}"
        print(f"[roast] {e}\n[roast] this run uses its own codex session: {SESSIONS[purpose]}", file=sys.stderr)
        try:
            return codex_session.run(prompt, SESSIONS[purpose], search=search, model=model, effort=effort)
        except codex_session.CodexError as e2:
            raise RoastError(str(e2)) from e2
    except codex_session.CodexError as e:
        raise RoastError(str(e)) from e


def jev(state, questions: dict) -> dict:
    """Ask jev through the official TypeSafe SDK (it sends its own User-Agent and retries 408/429/5xx).
    Returns jev's whole reply: the served model, the answers and the usage."""
    try:
        import typesafe_sdk as ts
    except ImportError as e:
        raise RoastError(f"the TypeSafe SDK is not installed for {sys.executable}: run "
                         f"'{sys.executable} -m pip install --user typesafe-sdk' ({e})") from e
    kinds = {"noul": ts.Noul, "choice": ts.Choice, "score": ts.Score}
    try:
        typed = {qid: kinds[q["type"]](**{k: v for k, v in q.items() if k in ("instructions", "criteria")})
                 for qid, q in questions.items()}
    except (KeyError, TypeError, ValueError) as e:
        raise RoastError(f"a question codex wrote is not a valid jev question ({e!r}): {json.dumps(questions)[:600]}") from e
    try:
        with ts.TypeSafeClient(api_key=jev_key()) as client:
            return client.system_one(state=state, questions=typed, model=JEV_MODEL).model_dump(mode="json")
    except ts.TypeSafeAPIError as e:
        kind = TooLong if "max_tokens_exceeded" in str(e.body) else JevError
        raise kind(scrub(f"jev answered HTTP {e.status}: {str(e.body)[:800]}")) from e
    except ts.TypeSafeError as e:
        raise JevError(scrub(f"jev call failed: {type(e).__name__}: {e}")) from e


def extract_json(text: str) -> dict:
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b <= a:
        raise RoastError("codex returned no JSON object when asked for jev's questions. Its reply began:\n" + text[:600])
    try:
        return json.loads(text[a:b + 1])
    except json.JSONDecodeError as e:
        raise RoastError(f"codex's questions are not valid JSON ({e}). Its reply began:\n" + text[:600]) from e


def code_in(value, where: str = "") -> list[str]:
    """Every place in `value` that looks like code, as 'where: what (the line)'."""
    if isinstance(value, dict):
        return [hit for k, v in value.items() for hit in code_in(v, f"{where}.{k}" if where else str(k))]
    if isinstance(value, list):
        return [hit for i, v in enumerate(value) for hit in code_in(v, f"{where}[{i}]")]
    if not isinstance(value, str):
        return []
    hits = []
    for what, sign in CODE_SIGNS.items():
        m = sign.search(value)
        if m:
            line = value[value.rfind("\n", 0, m.start()) + 1:].split("\n", 1)[0].strip()
            hits.append(f"{where}: {what} ({line[:80]!r})")
    return hits


def asked(a) -> dict:
    """What was asked for, in the asker's own words: codex's paraphrase never replaces them."""
    return {k: v for k, v in (("title", a.title), ("why", a.why), ("exit_condition", a.exit_condition)) if v}


def question_problems(qid: str, q) -> list[str]:
    if not isinstance(q, dict) or q.get("type") not in ("noul", "choice", "score"):
        return [f"question {qid!r} is not a noul, choice or score"]
    out = [] if str(q.get("instructions") or "").strip() else [f"question {qid!r} has no instructions"]
    c = q.get("criteria")
    if q["type"] == "noul" and c is not None and not (isinstance(c, dict) and set(c) <= {"true", "false"}):
        out.append(f"noul {qid!r}: criteria may only say what counts as true and as false")
    if q["type"] == "choice" and not (isinstance(c, dict) and len(c) >= 2):
        out.append(f"choice {qid!r} needs at least two options in criteria")
    if q["type"] == "score" and not (isinstance(c, list) and len(c) >= 2):
        out.append(f"score {qid!r} needs at least two ordered levels in criteria")
    return out


def framing_problems(framed: dict, mode: str) -> list[str]:
    """What breaks the framing contract, each said so codex can fix it."""
    state, questions, observed = framed.get("state"), framed.get("questions"), framed.get("observed")
    out = []
    if not isinstance(state, dict):
        out.append("'state' is not a JSON object")
    elif mode == "plan" and not str(state.get("the_plan") or "").strip():
        out.append("the state has no 'the_plan': the plan's logic in plain words")
    if not isinstance(questions, dict) or not questions:
        out.append("'questions' is missing or empty")
    else:
        out += [p for qid, q in questions.items() for p in question_problems(qid, q)]
    if not checked_facts(observed):
        out.append("'observed' has no fact with its source: check the work yourself and list what you saw, and where")
    out += [f"code would reach jev at {hit}"
            for hit in code_in({"state": state, "questions": questions, "observed": checked_facts(observed)})]
    return out


def checked_facts(observed) -> list[str]:
    """The words of each fact codex checked that has both words and a source. These reach jev in the
    state as checked_facts; the sources stay in the record (the live roast of 2026-09-28 found that
    a fact kept only in 'observed' never reached jev)."""
    if not isinstance(observed, list):
        return []
    return [str(o["fact"]).strip() for o in observed
            if isinstance(o, dict) and str(o.get("fact") or "").strip() and str(o.get("source") or "").strip()]


def frame_prompt(mode: str, a) -> str:
    count = ("Ask 2 to 5 questions of your own, including every question the asker gave. roast.py adds the "
             "owner's critical question (id anything_critical) itself, so do not write your own version of it. "
             "The state must hold the_plan: the plan's logic in plain words (purpose, scenario steps with their "
             "reasons, expected outcomes, open doubts)." if mode == "plan"
             else "Ask 3 to 6 questions, including every question the asker gave.")
    return f"""You are the second engineer on this work. Another engineer planned or did what is below; your job is to push back on it.
First check it yourself: read the files it names and any other local file you need, and treat its own account as a claim, not a fact.{WEB.get(mode, "")}
Then prepare a review for jev, a judge that answers typed questions about a state with probabilities. jev judges logic and never sees code: the state and the questions hold the purpose, scenario steps, reasons, expected and observed outcomes, the facts you checked, and open doubts, all in plain words. Source code, diffs, file contents, commands, file paths and code-level questions stay with you for your own checking; none of them goes to jev.

Mode: {mode}. Item: {a.item or "none"}.
What was asked for: {json.dumps(asked(a), ensure_ascii=False)}
What was done or planned: {a.did}
Files: {a.files}
The asker's own questions: {json.dumps(a.ask, ensure_ascii=False)}

Return ONLY a JSON object: {{"state": {{...}}, "questions": {{"<id>": <question>}}, "observed": [{{"fact": "<what you checked and saw>", "source": "<path:line, command or URL>"}}]}}.
Put every fact you checked in observed, each as one plain sentence with its source. roast.py copies the sentences, without their sources, into the state as checked_facts, so jev sees every one; do not repeat them elsewhere in the state.
A question is {{"type": "noul", "instructions": "<one statement that is true or false>"}}, or {{"type": "choice", "instructions": "...", "criteria": {{"<option>": "<what it means>"}}}}, or {{"type": "score", "instructions": "...", "criteria": ["<lowest level>", "...", "<highest level>"]}}.
Ask about the few mechanisms most able to defeat what was asked for, each a judgment a knowledgeable person makes in a second given the state. {count}
Keep the state short: {LIMITS}"""


def framed_by_codex(mode: str, a, prompt: str, search: bool) -> dict:
    """codex's state, questions and checked facts. A framing that breaks the contract goes back to
    codex once, with its exact problems. roast.py then sets three things itself: the checked facts'
    words, the asker's own words and, in a plan roast, the owner's critical question."""
    purpose = f"roast-{mode}"
    framed = extract_json(codex(prompt, search=search, purpose=purpose))
    problems = framing_problems(framed, mode)
    if problems:
        framed = extract_json(codex(
            "Your JSON breaks the contract:\n- " + "\n- ".join(problems)
            + "\nReturn the whole corrected JSON object only.",
            search=False, purpose=purpose))
        problems = framing_problems(framed, mode)
        if problems:
            raise RoastError("codex's framing still breaks the contract after one correction:\n- " + "\n- ".join(problems))
    state = {**framed["state"], "checked_facts": checked_facts(framed["observed"]), "what_was_asked_for": asked(a)}
    questions = dict(framed["questions"])
    if mode == "plan":
        questions[CRITICAL] = CRITICAL_QUESTION
    return {"state": state, "questions": questions, "observed": framed["observed"]}


def judged(mode: str, a, framed: dict) -> tuple[dict, dict]:
    """jev's reply, and the framing it answered. jev counts the tokens itself, so a request it refuses
    as too long goes back to codex once to be shortened and is asked again: a refused request got no
    answer, so this is not a re-run to change one."""
    try:
        return jev(framed["state"], framed["questions"]), framed
    except TooLong as e:
        shorter = framed_by_codex(mode, a, (
            f"jev refused your state and questions as too long: {e}. {LIMITS} Return the whole JSON object "
            "again with a shorter state and shorter checked facts (both go to jev): keep what decides the "
            "questions and drop the rest."), search=False)
        shorter["shortened"] = str(e)
        return jev(shorter["state"], shorter["questions"]), shorter


def shown(answer: dict) -> str:
    """One answer in jev's own terms: a noul is a probability and has no confidence; the confidence of a
    choice or score only says how concentrated its probabilities are."""
    try:
        if answer["type"] == "noul":
            return f"probability true {answer['noul']:.2f}"
        spread = sorted(answer["probabilities"].items(), key=lambda kv: -kv[1])
        if answer["type"] == "choice":
            return (f"picked {answer['choice']} (confidence {answer['confidence']:.2f}); probabilities: "
                    + ", ".join(f"{k} {v:.2f}" for k, v in spread))
        if answer["type"] == "score":
            return (f"expected level {answer['score']:.2f} (confidence {answer['confidence']:.2f}); levels: "
                    + "; ".join(f"{k} = {answer['legend'].get(k)!s} {v:.2f}" for k, v in spread))
    except (KeyError, TypeError, ValueError, AttributeError):
        pass
    return json.dumps(answer, ensure_ascii=False)


def reading_prompt(mode: str, framed: dict, answers: dict) -> str:
    lines = "\n".join(f"- {qid} ({q['type']}): {q['instructions']}\n  jev: {shown(answers[qid])}"
                      for qid, q in framed["questions"].items())
    critical = ("\nanything_critical is the owner's alarm, not a verdict or a severity: say whether you can build a "
                "scenario that would definitely break the whole thing asked for, and if you cannot, say you found none."
                if mode == "plan" else "")
    return f"""Write your reading of jev's answers for the engineer who acts on them.
jev returns numbers only. Everything you write is your own interpretation: never write that jev found, said or explained more than its numbers.
A noul answer is the probability that its statement is true and has no confidence; the confidence of a choice or score only says how concentrated its probabilities are.
For every concern, from an answer or from your own checking: the concrete failing scenario, step by step, and the checked facts with their sources that support it. If a worrying number has no failing scenario you can build, say so.{critical}
End with the findings worth acting on, one line each: what, why, and the evidence. Plain sentences; name no person.

WHAT WAS ASKED FOR: {json.dumps(framed["state"]["what_was_asked_for"], ensure_ascii=False)}
THE STATE JEV JUDGED: {json.dumps(framed["state"], ensure_ascii=False)}
THE FACTS YOU CHECKED: {json.dumps(framed["observed"], ensure_ascii=False)}
THE QUESTIONS AND JEV'S ANSWERS:
{lines}"""


def alone_prompt(mode: str, a) -> str:
    """codex alone, when jev cannot be used: the same pushback, with no typed questions."""
    critical = ("Say whether anything is critical: it would definitely break the whole thing asked for, must be "
                "fixed right away, and is neither a later task nor a feature. If nothing is, say so.\n"
                if mode == "plan" else "")
    return f"""You are the second engineer on this work. Another engineer planned or did what is below; your job is to push back on it.
First check it yourself: read the files it names and any other local file you need, and treat its own account as a claim, not a fact.{WEB.get(mode, "")}

Mode: {mode}. Item: {a.item or "none"}.
What was asked for: {json.dumps(asked(a), ensure_ascii=False)}
What was done or planned: {a.did}
Files: {a.files}
The asker's own questions, answer each: {json.dumps(a.ask, ensure_ascii=False)}

Find the few mechanisms most able to defeat what was asked for. For every concern: the concrete failing scenario, step by step, and the evidence (path:line, command or URL) that shows it. If you cannot build a failing scenario for a worry, leave it out.
{critical}End with the findings worth acting on, one line each: what, why, and the evidence. Plain sentences; name no person."""


FINDINGS_NOTE = ("## What was done with the findings\n\n(Whoever acts on this roast writes here what happened to each "
                 "finding: fixed, filed, or rejected, and why.)\n")


def with_jev(framed: dict, result: dict, reading: str) -> tuple[str, str]:
    """The record's jev line and body for a roast that used jev."""
    usage = result.get("usage") or {}
    facts = "\n".join(f"- {o.get('fact')} ({o.get('source')})" for o in framed["observed"] if isinstance(o, dict))
    answers = "\n".join(f"- **{qid}** ({q['type']}): {q['instructions']}\n  - {shown(result['answers'][qid])}"
                        for qid, q in framed["questions"].items())
    exact = json.dumps({"state": framed["state"], "questions": framed["questions"], "reply": result},
                       ensure_ascii=False, indent=1)
    shortened = (f"\n- shortened once: jev refused the first request as too long ({framed['shortened']})"
                 if framed.get("shortened") else "")
    line = (f"{result.get('model')} (asked for {JEV_MODEL}); {usage.get('input_tokens')} input tokens, "
            f"{usage.get('output_tokens')} output tokens{shortened}")
    body = (f"## Facts codex checked itself\n\n{facts}\n\n## jev's answers (numbers only)\n\n{answers}\n\n"
            f"## codex's reading (its interpretation, not jev's)\n\n{reading}\n\n{FINDINGS_NOTE}\n"
            f"## Exact request and reply\n\n```json\n{exact}\n```\n")
    return line, body


def record(root: Path, mode: str, a, jev_line: str, body: str) -> Path:
    """The run's own file, never overwritten, and a copy as roast-result.md."""
    now = dt.datetime.now(dt.timezone.utc)
    item = re.sub(r"[^\w.-]+", "-", a.item).strip("-")
    runs = root / ".claude" / "roasts"
    runs.mkdir(parents=True, exist_ok=True)
    path = runs / f"{now:%Y%m%dT%H%M%SZ}-{mode}{'-' + item if item else ''}-{uuid.uuid4().hex[:6]}.md"
    model, effort = model_of(mode)
    own = "\n".join(f"- {k}: {v}" for k, v in asked(a).items()) or "- (nothing given)"
    text = f"""# roast: {mode} -- {a.title}

- item: {a.item or "none"}
- when: {now.isoformat(timespec="seconds")}
- codex: {model} at {effort} effort, session {session_for(f"roast-{mode}")}
- jev: {jev_line}
- this record: {path}

## What was asked for

{own}

{body}"""
    path.write_text(text, encoding="utf-8")
    (root / ".claude" / "roast-result.md").write_text(f"Latest roast. Its own record: {path}\n\n{text}", encoding="utf-8")
    return path


def alone(root: Path, a, why: str) -> Path:
    """The fallback: codex reviews alone, and the record says why jev was not used."""
    print(f"[roast] jev not used: {why}; codex reviews alone", file=sys.stderr)
    reading = codex(alone_prompt(a.mode, a), search=a.mode in WEB, purpose=f"roast-{a.mode}")
    return record(root, a.mode, a, f"not used: {why}; codex reviewed alone",
                  f"## codex's critique (codex alone; jev was not used)\n\n{reading}\n\n{FINDINGS_NOTE}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("mode", choices=MODES)
    for flag in ("--item", "--title", "--why", "--exit-condition", "--did", "--files"):
        p.add_argument(flag, default="")
    p.add_argument("--ask", action="append", default=[])
    p.add_argument("--session", default="", help="the codex session to use instead of roast-<mode>")
    a = p.parse_args(argv)
    root = repo_root(Path.cwd())
    SESSIONS.clear()
    if a.session:
        SESSIONS[f"roast-{a.mode}"] = a.session
    if codex_session.codex_off():
        print("ROAST NOT RUN: codex is switched off (SIJAV_CODEX=off). Review the work yourself: the failing "
              "scenarios, whether anything is critical, and the findings worth acting on.", file=sys.stderr)
        return 3
    try:
        why = without_jev()
        if why:
            path = alone(root, a, why)
        else:
            hits = code_in(asked(a), "what_was_asked_for")
            if hits:
                raise RoastError("the asker's own words hold code, which must not reach jev:\n- " + "\n- ".join(hits))
            framed = framed_by_codex(a.mode, a, frame_prompt(a.mode, a), search=a.mode in WEB)
            try:
                result, framed = judged(a.mode, a, framed)
            except JevError as e:  # jev is down or refuses: the review still happens, without it
                path = alone(root, a, f"jev failed ({e})")
            else:
                answers = result.get("answers")
                if not isinstance(answers, dict):
                    raise RoastError("jev's reply has no 'answers' map: " + json.dumps(result)[:800])
                missing = sorted(set(framed["questions"]) - set(answers))
                if missing:
                    raise RoastError(f"jev did not answer {missing}; its reply: " + json.dumps(result)[:800])
                reading = codex(reading_prompt(a.mode, framed, answers), search=False, purpose=f"roast-{a.mode}")
                path = record(root, a.mode, a, *with_jev(framed, result, reading))
    except RoastError as e:
        print(f"ROAST FAILED ({a.mode}): {e}", file=sys.stderr)
        return 1
    print(path.read_text(encoding="utf-8"))
    print(f"[roast] record: {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
