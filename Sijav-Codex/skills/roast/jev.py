#!/usr/bin/env python3
"""Sijav-Codex roast: check a prepared framing against its brief, ask Jev, keep the run's record.

    open      --brief FILE [--project DIR]                  start a run from the orchestrator's brief
    available                                               say whether Jev can be asked (no call)
    check     --run DIR --framing FILE                      validate a framing; nothing is sent
    ask       --run DIR --framing FILE                      validate, then ask Jev once in one batch
    finalize  --run DIR --interpretation FILE [--technical FILE ...] [--dispositions FILE] [--close-jev WHY]
    note      --run DIR --file FILE --kind disposition|correction|other
    show      --run DIR [--json]
    recover   --run DIR --confirm-stopped                   close a run whose helper stopped mid-call

This helper is one step of a roast, not the roast. The orchestrator (Codex) writes the brief from
the original ask and the reviewer's checked facts; a native logical agent writes the framing (the
state and typed questions, in plain words); this helper refuses code, checks the framing against
the brief, asks Jev (jev-latest, TypeSafe SDK TypeSafeClient.system_one) and records everything.
Claude's technical findings, Codex's interpretation and what happened to each finding are added
with finalize/note; nothing here writes them, judges them or gates anything.

The brief (JSON):
  {"mode": "plan|task|technical|search", "item": "", "title": "", "why": "", "exit_condition": "",
   "did": "", "files": "", "ask": ["the asker's own questions"],
   "facts": [{"fact": "plain sentence", "source": "path:line, command or URL", "by": "who checked"}]}
title/why/exit_condition are the asker's own words: they reach Jev unchanged as what_was_asked_for.
Facts are the reviewer's checked facts; only their text reaches Jev, as checked_facts. did and files
stay in the record and never reach Jev.

The framing (JSON), from the native agent:
  {"state": {...}, "questions": {"<id>": <question>}, "observed": [<facts chosen from the brief>]}
A question is {"type": "noul", "instructions": "...", "criteria": {"true": "...", "false": "..."}?},
{"type": "choice", "instructions": "...", "criteria": {"<option>": "...", ...}} (two or more options)
or {"type": "score", "instructions": "...", "criteria": ["<lowest>", ..., "<highest>"]} (two or more).
"observed" is optional: by default every brief fact is sent; when given, each entry must be one of
the brief's facts exactly (fact and source). A framing may not write what_was_asked_for, checked_facts
or anything_critical itself: the helper sets them, and in plan mode adds the owner's critical
question word for word. Each of the asker's questions must appear word for word in some question's
instructions. In plan mode the state needs the_plan.

A framing that breaks the contract is refused with its exact problems (exit 4) so the orchestrator
can send them back to its author once; the corrected framing is the last chance (then exit 1, run
closed). Jev's own refusal as too long (max_tokens_exceeded) is reported (exit 5) and the run
accepts one shortened framing, which gets the same one correction.

Jev is not asked, and the run records why (exit 3, "unjudged"), when SIJAV_JEV=off, the key is
missing, the TypeSafe SDK is not installed or is not the 0.7.x series (0.7.1 checked; it needs
Python 3.10+, while this helper runs on 3.9+), or the call fails. No score is invented and nothing is
retried: the SDK's own retries are off (RetryPolicy(max_retries=0)) and each HTTP operation has a
60 s timeout. A failure records what is known about the request: not sent, answered with an error,
or outcome unknown (it may have reached Jev). The key comes from the file SIJAV_JEV_KEY_FILE names,
else TYPESAFE_API_KEY; never from a file in the plugin, and its value is never written or printed.

While a request is in flight the run is `calling`. If the helper stops then (a crash, a kill, an
interrupt), every command refuses until `recover --confirm-stopped` records the outcome as unknown,
or judges a reply that was already saved. Jev is never asked twice in one run.

Records: <project>/.codex/roasts/<UTC time>-<mode>[-<item>]-<tag>/ holds brief.json (its sha256 is kept
in state.json; a brief edited after open is refused), framing-<n>.json and check-<n>.json for every
framing received, request-<n>.json and response-<n>.json (the reply and its raw body) or error-<n>.json
for every Jev call, jev.json (the outcome), and after finalize record.md (once) plus notes/. Run
files are created once and never overwritten; state.json tracks the step. <project>/.codex/roasts/
latest.md points to the newest finalized record.

Exit codes: 0 done; 1 failed or refused for good (record kept); 2 usage; 3 Jev unjudged (record kept);
4 framing problems to send back once; 5 Jev needs a shorter framing (once).
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import uuid
from pathlib import Path

if os.name == "nt":
    import msvcrt
else:
    import fcntl

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

SCHEMA = "sijav-codex-roast-run/1"
JEV_MODEL = "jev-latest"
SDK_TESTED, SDK_SERIES = "0.7.1", "0.7."
STATE_WRITE_SECONDS = 5.0
HTTP_TIMEOUT = 60.0  # seconds per HTTP operation (the SDK default is 10); not a wall-clock deadline
CLIENT_OPTIONS: dict = {}  # extra TypeSafeClient options; only tests set it, to inject a local transport
STATUSES = ("open", "needs_correction", "needs_shortening", "calling", "judged", "unjudged", "framing_rejected")
MODES = ("plan", "task", "technical", "search")
KINDS = ("noul", "choice", "score")
NOTE_KINDS = ("disposition", "correction", "other")
OFF_VALUES = {"off", "0", "false", "no"}
LIMITS = ("Jev's limits (https://docs.typesafe.ai/models): 32k tokens for the state plus the single longest"
          " question, and 64k tokens for the state plus all questions. They count tokens, not characters.")
CRITICAL = "anything_critical"
# The owner's direct question, word for word as agreed with Jev in September 2026 (source roast.py).
# A plan roast always asks it in the same batch; this helper adds it so it cannot be dropped or reworded.
CRITICAL_QUESTION = {
    "type": "noul",
    "instructions": (
        "Given what_was_asked_for and the_plan's logic, is anything critical: it would definitely break"
        " the whole thing asked for, must be fixed right away, and is neither a later task nor a feature?"
        " True means such a critical problem exists. Small refinements, later tasks, and features do not count."
    ),
}
SET_BY_HELPER = ("what_was_asked_for", "checked_facts")
# Jev judges logic; code is Claude's (owner, 2026-09-23). These catch code in the forms plain prose
# does not take (source roast.py, unchanged).
CODE_SIGNS = {
    "a fenced code block": re.compile(r"```"),
    "a diff": re.compile(r"^(diff --git |@@ -\d|--- a/|\+\+\+ b/|--- /dev/null)", re.M),
    "a definition": re.compile(r"^\s*((async\s+)?def|function)\s+\w+\s*\(|^\s*class\s+\w+\s*[(:]", re.M),
    "an export": re.compile(r"^\s*export\s+(default\s+)?(async\s+)?(function|class|const|let|var|interface|type)\b", re.M),
    "an import": re.compile(r"^\s*(import\s+[\w.]+(\s+as\s+\w+)?\s*$|from\s+[\w.]+\s+import\s+[\w*(])", re.M),
    "a declaration": re.compile(r"^\s*(const|let|var)\s+\w+\s*=", re.M),
    "an assignment from a call": re.compile(r"^\s*[A-Za-z_][\w.]*\s*=\s*[A-Za-z_][\w.]*\(", re.M),
    "a line that opens a block": re.compile(r"\{\s*$", re.M),
}
# Keys a framing may not put anywhere in its state: the helper sets the asked-for words and the checked
# facts from the brief, and the critical question; facts never come from the framing's author.
RESERVED_STATE_KEYS = ("what_was_asked_for", "checked_facts", "anything_critical", "facts")

OK, FAILED, USAGE, UNJUDGED, PROBLEMS, TOO_LONG = 0, 1, 2, 3, 4, 5
MAX_ATTEMPTS = 2  # a framing and its one correction, per phase


class JevError(Exception):
    """A failure whose message is the exact cause, with the exit code it ends the command with."""

    def __init__(self, message: str, code: int = FAILED):
        super().__init__(message)
        self.code = code


class JevUnavailable(JevError):
    """Jev cannot be asked: switched off, no key or no SDK."""

    def __init__(self, message: str):
        super().__init__(message, UNJUDGED)


class JevCallFailed(JevError):
    """The call failed; `body` is what the service said, when it said anything; `outcome` is what is
    known about the request: not_sent, answered or unknown."""

    def __init__(self, message: str, status=None, body=None, too_long: bool = False, outcome: str = "unknown"):
        super().__init__(message, TOO_LONG if too_long else UNJUDGED)
        self.status, self.body, self.too_long, self.outcome = status, body, too_long, outcome


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")


# ---------------------------------------------------------------- provider


def switched_off() -> bool:
    return os.environ.get("SIJAV_JEV", "on").strip().lower() in OFF_VALUES


def jev_key() -> str:
    """The TypeSafe key, from the file SIJAV_JEV_KEY_FILE names, else TYPESAFE_API_KEY. Never from a
    file inside the plugin: an installed plugin folder is copied into a cache, key files included."""
    named = os.environ.get("SIJAV_JEV_KEY_FILE", "").strip()
    if named:
        f = Path(named)
        if not f.is_file():
            raise JevUnavailable(f"no Jev key: SIJAV_JEV_KEY_FILE names {f}, which does not exist")
        try:
            key = f.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as exc:
            raise JevUnavailable(f"the Jev key file {f} cannot be read: {type(exc).__name__}") from exc
        if not key:
            raise JevUnavailable(f"the Jev key file {f} is empty")
        return key
    key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if key:
        return key
    raise JevUnavailable("no Jev key: set SIJAV_JEV_KEY_FILE to the key's file, or TYPESAFE_API_KEY")


def load_sdk():
    """The TypeSafe SDK, if it is the 0.7.x contract this helper was checked against (0.7.1 needs
    Python 3.10+; the helper itself runs on 3.9 and records Jev as unjudged without it)."""
    try:
        import typesafe_sdk  # noqa: PLC0415 -- optional: the helper works without it
    except ImportError as exc:
        raise JevUnavailable(f"the TypeSafe SDK (typesafe-sdk) is not installed for {sys.executable}"
                             f" ({exc})") from exc
    version = str(getattr(typesafe_sdk, "__version__", ""))
    if not version.startswith(SDK_SERIES) or not hasattr(typesafe_sdk, "RetryPolicy"):
        raise JevUnavailable(f"typesafe-sdk {version or '(unknown version)'} is not the {SDK_SERIES}x contract this"
                             f" helper was checked against ({SDK_TESTED}); Jev was not asked")
    return typesafe_sdk


def unavailable() -> str:
    """Why Jev cannot be asked, or '' when it can. Reads the key's presence, never prints it."""
    if switched_off():
        return "switched off (SIJAV_JEV=off)"
    try:
        jev_key()
        load_sdk()
    except JevUnavailable as exc:
        return str(exc)
    return ""


def scrub(text: str) -> str:
    try:
        key = jev_key()
    except JevError:
        return text
    return text.replace(key, "[the Jev key]") if key else text


def plain(value):
    """A JSON-ready copy of an SDK object or plain data; anything else is refused, never coerced."""
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"a {type(value).__name__} is not JSON data")


def call(state: dict, questions: dict) -> dict:
    """One batched system_one call on jev-latest, with the SDK's retries off (RetryPolicy(max_retries=0))
    and a finite HTTP timeout. Returns Jev's whole reply as plain JSON (served model, answers, usage,
    the request id and the raw response body when the SDK gives them).

    Failures say what is known about the request: JevCallFailed.outcome is "not_sent" (refused
    before any request), "answered" (the service replied with an error) or "unknown" (it may have
    reached the service)."""
    if switched_off():
        raise JevUnavailable("switched off (SIJAV_JEV=off)")
    key = jev_key()
    ts = load_sdk()
    kinds = {"noul": ts.Noul, "choice": ts.Choice, "score": ts.Score}
    try:
        typed = {qid: kinds[q["type"]](**{k: v for k, v in q.items() if k in ("instructions", "criteria")})
                 for qid, q in questions.items()}
        policy = ts.RetryPolicy(max_retries=0)
        client = ts.TypeSafeClient(api_key=key, retry=policy, timeout=HTTP_TIMEOUT, **CLIENT_OPTIONS)
    except Exception as exc:  # noqa: BLE001 -- nothing was sent; the exact cause is kept
        raise JevCallFailed(scrub(f"the Jev request could not be prepared: {type(exc).__name__}: {exc}")[:2000],
                            outcome="not_sent") from exc
    try:
        with client:
            response = client.system_one(state, typed, model=JEV_MODEL, retry=policy, timeout=HTTP_TIMEOUT)
            try:
                reply = plain(response)
            except (TypeError, ValueError) as exc:
                raw = None  # keep what the service actually sent, when the SDK exposes it
                with contextlib.suppress(Exception):
                    raw = {"raw_body": scrub(response.raw_http_response.text)}
                    with contextlib.suppress(Exception):
                        raw["request_id"] = str(response.request_id)
                raise JevCallFailed(f"Jev replied, but the SDK's reply ({type(response).__name__}) is not JSON data"
                                    f" that can be recorded: {exc}", body=raw, outcome="answered") from exc
            with contextlib.suppress(Exception):
                rid = response.request_id
                if rid:
                    reply.setdefault("request_id", str(rid))
            with contextlib.suppress(Exception):
                reply.setdefault("raw_body", response.raw_http_response.text)
            json.dumps(reply)
            return reply
    except JevCallFailed:
        raise
    except getattr(ts, "TypeSafeAPIError", ()) as exc:
        body = plain_or_text(getattr(exc, "body", None))
        status = getattr(exc, "status", None)
        text = scrub(json.dumps(body, ensure_ascii=False) if not isinstance(body, str) else body)
        raise JevCallFailed(f"Jev answered HTTP {status} ({type(exc).__name__}): {text[:2000]}", status,
                            scrub_value(body), too_long="max_tokens_exceeded" in text, outcome="answered") from exc
    except Exception as exc:  # noqa: BLE001 -- connection, timeout, a reply that is not JSON, anything else
        raise JevCallFailed(scrub(f"the Jev call failed: {type(exc).__name__}: {exc}")[:2000],
                            outcome="unknown") from exc


def plain_or_text(value):
    try:
        return plain(value)
    except TypeError:
        return repr(value)


def scrub_value(value):
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, dict):
        return {k: scrub_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub_value(v) for v in value]
    return value


# ------------------------------------------------------------- validation


def code_in(value, where: str = "") -> list[str]:
    """Every place in `value` that looks like code, as 'where: what (the line)'."""
    if isinstance(value, dict):
        hits = []
        for k, v in value.items():
            at = f"{where}.{k}" if where else str(k)
            hits += [h.replace(f"{at}:", f"{at} (the key):", 1) for h in code_in(str(k), at)]
            hits += code_in(v, at)
        return hits
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


def question_problems(qid: str, q) -> list[str]:
    if not isinstance(q, dict) or q.get("type") not in KINDS:
        return [f"question {qid!r} is not a noul, choice or score"]
    ins = q.get("instructions")
    has = (isinstance(ins, str) and ins.strip()) or (isinstance(ins, (dict, list)) and ins)
    out = [] if has else [f"question {qid!r} has no instructions"]
    extra = sorted(set(q) - {"type", "instructions", "criteria"})
    if extra:
        out.append(f"question {qid!r} has fields Jev does not take: {extra}")
    c = q.get("criteria")
    if q["type"] == "noul" and c is not None and not (isinstance(c, dict) and set(c) <= {"true", "false"}):
        out.append(f"noul {qid!r}: criteria may only say what counts as true and as false")
    if q["type"] == "choice" and not (isinstance(c, dict) and len(c) >= 2):
        out.append(f"choice {qid!r} needs at least two options in criteria")
    if q["type"] == "score" and not (isinstance(c, list) and len(c) >= 2):
        out.append(f"score {qid!r} needs at least two ordered levels in criteria")
    return out


def brief_problems(brief) -> tuple[list[str], list[str]]:
    """(structural problems, code problems) of the orchestrator's brief."""
    if not isinstance(brief, dict):
        return ["the brief is not a JSON object"], []
    out = []
    if brief.get("mode") not in MODES:
        out.append(f"mode {brief.get('mode')!r} is not one of {', '.join(MODES)}")
    for k in ("item", "title", "why", "exit_condition", "did", "files"):
        if not isinstance(brief.get(k, ""), str):
            out.append(f"{k} is not a string")
    if not any(str(brief.get(k) or "").strip() for k in ("title", "why", "exit_condition")):
        out.append("the brief has none of title, why or exit_condition: the original ask is missing")
    ask = brief.get("ask", [])
    if not isinstance(ask, list) or not all(isinstance(q, str) and q.strip() for q in ask):
        out.append("ask is not a list of the asker's questions")
    facts = brief.get("facts")
    if not isinstance(facts, list) or not facts:
        out.append("facts is missing or empty: give the reviewer's checked facts, each with its source")
    else:
        for i, f in enumerate(facts):
            if not (isinstance(f, dict) and isinstance(f.get("fact"), str) and f["fact"].strip()
                    and isinstance(f.get("source"), str) and f["source"].strip()):
                out.append(f"facts[{i}] needs a plain-sentence fact and its source")
    extra = sorted(set(brief) - {"mode", "item", "title", "why", "exit_condition", "did", "files", "ask", "facts"})
    if extra:
        out.append(f"the brief has unknown fields {extra}")
    if out:
        return out, []
    code = code_in(asked(brief), "what_was_asked_for")
    code += code_in([f["fact"] for f in facts], "facts")
    code += code_in(ask, "ask")
    return [], code


def asked(brief: dict) -> dict:
    """What was asked for, in the asker's own words."""
    return {k: brief[k] for k in ("title", "why", "exit_condition") if str(brief.get(k) or "").strip()}


def chosen_facts(brief: dict, observed) -> tuple[list[dict], list[str]]:
    facts = brief["facts"]
    if observed is None:
        return facts, []
    if not isinstance(observed, list) or not observed:
        return [], ["'observed' must be a non-empty list of the brief's facts, or left out to send them all"]
    known = {(f["fact"], f["source"]) for f in facts}
    out, problems = [], []
    for i, o in enumerate(observed):
        pair = (o.get("fact"), o.get("source")) if isinstance(o, dict) else None
        if pair not in known:
            problems.append(f"observed[{i}] is not one of the brief's checked facts word for word"
                            " (fact and source): facts come from the reviewer, not the framing")
        else:
            out.append(next(f for f in facts if (f["fact"], f["source"]) == pair))
    return out, problems


def reserved_keys(value, where: str = "") -> list[str]:
    """Paths of reserved keys anywhere in a framing's state."""
    out = []
    if isinstance(value, dict):
        for k, v in value.items():
            at = f"{where}.{k}" if where else str(k)
            if str(k) in RESERVED_STATE_KEYS:
                out.append(at)
            out += reserved_keys(v, at)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            out += reserved_keys(v, f"{where}[{i}]")
    return out


def framing_problems(framing, brief: dict) -> list[str]:
    """What breaks the framing contract, each said so its author can fix it."""
    if not isinstance(framing, dict):
        return ["the framing is not a JSON object"]
    out = []
    extra = sorted(set(framing) - {"state", "questions", "observed"})
    if extra:
        out.append(f"the framing has unknown fields {extra}: only state, questions and observed")
    state, questions = framing.get("state"), framing.get("questions")
    mode = brief["mode"]
    if not isinstance(state, dict) or not state:
        out.append("'state' is not a non-empty JSON object")
        state = {}
    elif mode == "plan" and not str(state.get("the_plan") or "").strip():
        out.append("the state has no 'the_plan': the plan's logic in plain words")
    for k in SET_BY_HELPER:
        if k in state:
            out.append(f"the state may not set {k!r}: the helper sets it from the brief")
    out += [f"the state may not carry {where!r}: facts and the asked-for words come from the brief"
            for where in reserved_keys(state) if where not in SET_BY_HELPER]
    if not isinstance(questions, dict) or not questions:
        out.append("'questions' is missing or empty")
        questions = {}
    else:
        if CRITICAL in questions:
            out.append(f"the framing may not write {CRITICAL!r}: in plan mode the helper adds the owner's"
                       " question word for word")
        out += [p for qid, q in questions.items() for p in question_problems(qid, q)]
        texts = [json.dumps(q.get("instructions"), ensure_ascii=False) if not isinstance(q.get("instructions"), str)
                 else q["instructions"] for q in questions.values() if isinstance(q, dict)]
        for i, a in enumerate(brief.get("ask") or []):
            if not any(a.strip() in t for t in texts):
                out.append(f"the asker's question {i + 1} ({a.strip()[:120]!r}) is not asked: put it word for"
                           " word in a question's instructions")
    facts, fact_problems = chosen_facts(brief, framing.get("observed"))
    out += fact_problems
    sent_state, sent_questions = compose(brief, framing, facts) if not out else (state, questions)
    out += [f"code would reach Jev at {hit}" for hit in code_in({"state": sent_state, "questions": sent_questions})]
    return out


def compose(brief: dict, framing: dict, facts: list[dict]) -> tuple[dict, dict]:
    """The exact state and questions Jev gets: the framing's, plus what the helper sets."""
    state = {**framing["state"], "checked_facts": [f["fact"].strip() for f in facts],
             "what_was_asked_for": asked(brief)}
    questions = dict(framing["questions"])
    if brief["mode"] == "plan":
        questions[CRITICAL] = CRITICAL_QUESTION
    return state, questions


def answer_problems(questions: dict, reply) -> list[str]:
    """Every way Jev's reply fails to answer every question in its type."""
    if not isinstance(reply, dict):
        return ["Jev's reply is not an object"]
    answers = reply.get("answers")
    if not isinstance(answers, dict):
        return ["Jev's reply has no 'answers' map"]
    out = []
    missing = sorted(set(questions) - set(answers))
    if missing:
        out.append(f"Jev did not answer {missing}")
    extra = sorted(set(answers) - set(questions))
    if extra:
        out.append(f"Jev answered questions that were not asked: {extra}")
    for qid, q in questions.items():
        a = answers.get(qid)
        if a is None:
            continue
        if not isinstance(a, dict) or a.get("type") != q["type"]:
            out.append(f"the answer to {qid!r} is not a {q['type']} answer")
            continue
        out += [f"{qid!r}: {p}" for p in one_answer_problems(q, a)]
    return out


def _prob(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and 0.0 <= float(x) <= 1.0


def one_answer_problems(q: dict, a: dict) -> list[str]:
    if q["type"] == "noul":
        return [] if _prob(a.get("noul")) else ["no noul probability between 0 and 1"]
    out = []
    if not _prob(a.get("confidence")):
        out.append("no confidence between 0 and 1")
    probs = a.get("probabilities")
    if not isinstance(probs, dict) or not all(_prob(v) for v in probs.values()):
        return out + ["no probabilities between 0 and 1"]
    if q["type"] == "choice":
        if set(map(str, probs)) != set(map(str, q["criteria"])):
            out.append(f"probabilities cover {sorted(map(str, probs))}, not the options {sorted(map(str, q['criteria']))}")
        if str(a.get("choice")) not in set(map(str, q["criteria"])):
            out.append(f"picked {a.get('choice')!r}, which is not an option")
    else:
        levels = {str(i) for i in range(len(q["criteria"]))}
        if set(map(str, probs)) != levels:
            out.append(f"probabilities cover levels {sorted(map(str, probs))}, not 0..{len(q['criteria']) - 1}")
        s = a.get("score")
        if not isinstance(s, (int, float)) or isinstance(s, bool) or not 0 <= s <= len(q["criteria"]) - 1:
            out.append("no expected score within the levels")
    return out


def shown(answer) -> str:
    """One answer in Jev's own terms: a noul is a probability and has no confidence; the confidence of a
    choice or score only says how concentrated its probabilities are."""
    try:
        if answer["type"] == "noul":
            return f"probability true {answer['noul']:.2f}"
        spread = sorted(answer["probabilities"].items(), key=lambda kv: -kv[1])
        if answer["type"] == "choice":
            return (f"picked {answer['choice']} (confidence {answer['confidence']:.2f}); probabilities: "
                    + ", ".join(f"{k} {v:.2f}" for k, v in spread))
        if answer["type"] == "score":
            legend = answer.get("legend") or {}
            return (f"expected level {answer['score']:.2f} (confidence {answer['confidence']:.2f}); levels: "
                    + "; ".join(f"{k} = {legend.get(k, legend.get(str(k)))!s} {v:.2f}" for k, v in spread))
    except (KeyError, TypeError, ValueError, AttributeError):
        pass
    return json.dumps(answer, ensure_ascii=False)


# ------------------------------------------------------------- the run


def project_root(explicit: str | None, start: Path | None = None) -> Path:
    """--project, else below the home folder the nearest board (.claude/todo.db), else the nearest
    .git, else the nearest .claude folder (as claude_session.py). Never this script's location."""
    if explicit:
        root = Path(explicit).expanduser()
        if not root.is_dir():
            raise JevError(f"--project {root} is not an existing folder", USAGE)
        return root.resolve()
    home = Path.home().resolve()
    here = (start or Path.cwd()).resolve()
    chain = []
    for p in (here, *here.parents):
        if p == home:
            break
        chain.append(p)
    for test in (lambda p: (p / ".claude" / "todo.db").is_file(), lambda p: (p / ".git").exists(),
                 lambda p: (p / ".claude").is_dir()):
        for p in chain:
            if test(p):
                return p
    raise JevError(f"no project found at or above {here}: pass --project DIR; nothing was created", USAGE)


def write_new(path: Path, data) -> None:
    raw = data if isinstance(data, bytes) else (
        json.dumps(data, indent=2, ensure_ascii=False) + "\n" if not isinstance(data, str) else data
    ).encode("utf-8")
    with open(path, "xb") as f:
        f.write(raw)


def write_state(path: Path, data: dict) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".state-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        # Windows refuses a rename onto a file another program holds open (a status reader, a
        # scanner): retry with backoff for STATE_WRITE_SECONDS, as claude_session.py does.
        deadline, pause = time.monotonic() + STATE_WRITE_SECONDS, 0.05
        while True:
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(pause)
                pause = min(pause * 2, 0.5)
    except OSError as exc:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise JevError(f"could not write {path}: {exc}") from exc


def request_number(outcome: dict):
    """The request number an outcome record names (an int, or 'request-N.json'), or None."""
    ref = outcome.get("request") if isinstance(outcome, dict) else None
    if isinstance(ref, int) and not isinstance(ref, bool):
        return ref
    m = re.fullmatch(r"request-(\d+)\.json", str(ref or ""))
    return int(m.group(1)) if m else None


def publish_once(path: Path, data: bytes) -> None:
    """Make `path` appear whole or not at all, and never replace it: the bytes go to a temporary file
    in the same folder, flushed and fsynced, which is then hard-linked to `path` (atomic, and refused
    with FileExistsError when `path` exists). The temporary name is always removed. A file system
    without hard links raises its OSError; there is no overwriting fallback."""
    fd, tmp = tempfile.mkstemp(prefix=".jev-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.link(tmp, path)
    finally:
        with contextlib.suppress(OSError):
            os.remove(tmp)


def record_outcome(run: Path, outcome: dict, c) -> dict:
    """Create jev.json once. If an earlier attempt already wrote it (its helper stopped before the
    state was saved), adopt it when it records the very same request; anything else is refused, never
    overwritten and never guessed."""
    path = run / "jev.json"
    if not path.exists():
        try:
            publish_once(path, (json.dumps(outcome, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
            return outcome
        except FileExistsError:
            pass  # another writer published first: validate and adopt its record below, or refuse
        except OSError as exc:
            raise JevError(f"{path} could not be published ({type(exc).__name__}: {exc}). Nothing was written"
                           " to it; the run stays calling with its request and replies kept, so `recover"
                           " --confirm-stopped` can close it once the cause is fixed. Jev is not asked again.",
                           FAILED) from exc
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise JevError(f"{path} exists but cannot be read ({exc}); it was left untouched", FAILED) from exc
    if c is None or request_number(old) != c or old.get("status") not in ("judged", "unjudged"):
        raise JevError(f"{path} already records {old.get('status')!r} for request {request_number(old)!r}, not an"
                       f" outcome of request {c!r}; it was left untouched", FAILED)
    return old


@contextlib.contextmanager
def run_lock(run: Path, wait: float = 2.0):
    """One command per run at a time; the OS releases the lock if its holder dies."""
    handle = open(run / "lock", "a+b")
    try:
        deadline = time.monotonic() + wait
        while True:
            try:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise JevError(f"the run {run} is busy in another command; nothing was sent") from None
                time.sleep(0.05)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def load_json_file(path: str, what: str):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise JevError(f"cannot read the {what} {path} as JSON: {exc}", USAGE) from exc


def state_problems(state) -> list[str]:
    if not isinstance(state, dict) or state.get("schema") != SCHEMA:
        return [f"it is not a {SCHEMA} state"]
    out = []
    if state.get("status") not in STATUSES:
        out.append(f"status {state.get('status')!r} is not one of {', '.join(STATUSES)}")
    if state.get("phase") not in ("initial", "shortened"):
        out.append(f"phase {state.get('phase')!r} is not initial or shortened")
    for k in ("attempts", "framings", "calls"):
        if not isinstance(state.get(k), int) or isinstance(state.get(k), bool) or state[k] < 0:
            out.append(f"{k} is not a non-negative integer")
    if state.get("mode") not in MODES:
        out.append(f"mode {state.get('mode')!r} is not a roast mode")
    if not isinstance(state.get("brief_sha256"), str):
        out.append("brief_sha256 is missing (the run predates brief protection, or the state was edited)")
    if state.get("status") == "calling" and not (isinstance(state.get("calling"), dict)
                                                 and isinstance(state["calling"].get("request"), int)):
        out.append("status is calling but the request in flight is not recorded")
    return out


def open_run(run_arg: str) -> tuple[Path, dict, dict]:
    """The run's state and its brief, refusing a malformed state or a brief changed since `open`."""
    run = Path(run_arg).resolve()
    state_path = run / "state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        raw_brief = (run / "brief.json").read_bytes()
        brief = json.loads(raw_brief.decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise JevError(f"{run} is not a readable roast run (state.json, brief.json): {exc}", USAGE) from exc
    problems = state_problems(state)
    if problems:
        raise JevError(f"{state_path} is not a valid run state: " + "; ".join(problems)
                       + ". It was left untouched and nothing was sent.", FAILED)
    if hashlib.sha256(raw_brief).hexdigest() != state["brief_sha256"]:
        raise JevError(f"{run / 'brief.json'} changed after the run was opened (its sha256 no longer matches"
                       " the one recorded at open). The original ask is never edited in place; nothing was"
                       " sent. Open a new run for a changed brief.", FAILED)
    structural, code = brief_problems(brief)
    if structural or code:
        raise JevError(f"{run / 'brief.json'} is not a valid brief: " + "; ".join(structural + code), FAILED)
    if state.get("mode") != brief["mode"]:
        raise JevError(f"the run's state says mode {state.get('mode')!r} but its brief says {brief['mode']!r}", FAILED)
    return run, state, brief


def refuse_if_calling(state: dict, run: Path) -> None:
    if state["status"] == "calling":
        c = state["calling"]
        raise JevError(
            f"request-{c['request']}.json was handed to the SDK at {c.get('started')} by helper pid"
            f" {c.get('helper_pid')} and its outcome was never recorded (the helper stopped). Jev may have"
            " answered it. Nothing was sent. Once that process has stopped, run: recover --run"
            f" \"{run}\" --confirm-stopped (it records the outcome as unknown; it never asks again).", FAILED)


def cmd_open(a) -> int:
    brief = load_json_file(a.brief, "brief")
    problems, code = brief_problems(brief)
    if problems:
        raise JevError("the brief is incomplete; no run was created:\n- " + "\n- ".join(problems), USAGE)
    if code:
        raise JevError("the brief holds code, which must never reach Jev; no run was created. Restate the"
                       " checked facts in plain words without source syntax. If the asker's own title, why"
                       " or exit holds code, Jev cannot be asked for this roast; say so in its record.\n- "
                       + "\n- ".join(code), PROBLEMS)
    root = project_root(a.project)
    t = dt.datetime.now(dt.timezone.utc)
    item = re.sub(r"[^\w.-]+", "-", brief.get("item") or "").strip("-")
    base = root / ".codex" / "roasts"
    base.mkdir(parents=True, exist_ok=True)
    run = base / f"{t:%Y%m%dT%H%M%SZ}-{brief['mode']}{'-' + item if item else ''}-{uuid.uuid4().hex[:6]}"
    run.mkdir()
    write_new(run / "brief.json", brief)
    digest = hashlib.sha256((run / "brief.json").read_bytes()).hexdigest()
    write_state(run / "state.json", {
        "schema": SCHEMA, "project_root": str(root), "mode": brief["mode"], "created": t.isoformat(),
        "brief_sha256": digest, "phase": "initial", "attempts": 0, "framings": 0, "calls": 0, "status": "open",
        "calling": None, "shortened_because": None, "outcome": None, "finalized": None})
    print(str(run))
    print(f"[jev] run opened for a {brief['mode']} roast; brief sha256 {digest} (recorded; an edited brief is"
          " refused)", file=sys.stderr)
    return OK


def record_framing(run: Path, state: dict, framing_path: str) -> tuple[int, object, bytes]:
    try:
        raw = Path(framing_path).read_bytes()
    except OSError as exc:
        raise JevError(f"cannot read the framing {framing_path}: {exc}; nothing was recorded or sent", USAGE) from exc
    state["framings"] += 1
    n = state["framings"]
    write_new(run / f"framing-{n}.json", raw)
    try:
        framing = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        framing = exc
    return n, framing, raw


def cmd_check(a) -> int:
    run, state, brief = open_run(a.run)
    with run_lock(run):
        refuse_if_calling(state, run)
        if state["status"] not in ("open", "needs_correction", "needs_shortening"):
            raise JevError(f"the run is {state['status']}; it takes no more framings", FAILED)
        try:
            framing = load_json_file(a.framing, "framing")
        except JevError as exc:
            print(f"FRAMING PROBLEMS: {exc}", file=sys.stderr)
            return PROBLEMS
        problems = framing_problems(framing, brief)
    if problems:
        print("FRAMING PROBLEMS (nothing recorded or sent):\n- " + "\n- ".join(problems), file=sys.stderr)
        return PROBLEMS
    print("framing passes the contract (nothing recorded or sent)")
    return OK


def cmd_ask(a) -> int:
    run, state, brief = open_run(a.run)
    with run_lock(run):
        run, state, brief = open_run(a.run)  # reread under the lock
        refuse_if_calling(state, run)
        if state["status"] not in ("open", "needs_correction", "needs_shortening"):
            raise JevError(f"the run is {state['status']}; Jev is asked once per run (a new review round"
                           " opens a new run). Nothing was sent.", FAILED)
        if state["status"] == "needs_shortening" and not a.shortened:
            raise JevError("Jev refused this run's request as too long: the next framing must be the"
                           " shortened one, passed with --shortened", USAGE)
        if a.shortened and state["status"] != "needs_shortening":
            raise JevError("--shortened is only for a run whose request Jev refused as too long", USAGE)
        if state["status"] == "needs_shortening":
            state.update(phase="shortened", attempts=0, status="open")
        why_not = unavailable()
        if why_not:
            n, _framing, _raw = record_framing(run, state, a.framing)
            return close_unjudged(run, state, f"Jev was not asked: {why_not}", framing=n)
        n, framing, raw = record_framing(run, state, a.framing)
        state["attempts"] += 1
        problems = ([f"the framing is not UTF-8 JSON: {framing}"] if isinstance(framing, Exception)
                    else framing_problems(framing, brief))
        write_new(run / f"check-{n}.json", {"framing": f"framing-{n}.json", "framed_by": a.framed_by,
                                            "phase": state["phase"], "attempt": state["attempts"],
                                            "problems": problems, "at": now()})
        if problems:
            if state["attempts"] >= MAX_ATTEMPTS:
                state.update(status="framing_rejected", outcome={
                    "status": "framing_rejected", "phase": state["phase"],
                    "cause": "the framing still breaks the contract after one correction", "problems": problems})
                write_new(run / "jev.json", state["outcome"])
                write_state(run / "state.json", state)
                print("FRAMING REJECTED: it still breaks the contract after one correction; Jev was not asked."
                      f" The run is closed. Record: {run}\n- " + "\n- ".join(problems), file=sys.stderr)
                return FAILED
            state["status"] = "needs_correction"
            write_state(run / "state.json", state)
            print("FRAMING PROBLEMS: send these back to the framing's author once; nothing was sent to Jev."
                  f" Check record: {run / f'check-{n}.json'}\n- " + "\n- ".join(problems), file=sys.stderr)
            return PROBLEMS
        facts, _ = chosen_facts(brief, framing.get("observed"))
        sent_state, sent_questions = compose(brief, framing, facts)
        state["calls"] += 1
        c = state["calls"]
        write_new(run / f"request-{c}.json", {
            "model": JEV_MODEL, "framing": f"framing-{n}.json", "phase": state["phase"],
            "shortened_because": state["shortened_because"], "handed_to_sdk_at": now(),
            "sdk": {"retry": "RetryPolicy(max_retries=0)", "http_timeout_seconds": HTTP_TIMEOUT},
            "state": sent_state, "questions": sent_questions,
            "facts_with_sources": facts, "limits": LIMITS})
        # In flight: until an outcome is recorded, every other command refuses, so a helper that dies
        # during the call can never lead to the same run asking Jev twice.
        state.update(status="calling", calling={"request": c, "started": now(), "helper_pid": os.getpid()})
        write_state(run / "state.json", state)
        try:
            reply = call(sent_state, sent_questions)
        except JevCallFailed as exc:
            write_new(run / f"error-{c}.json", {"at": now(), "status": exc.status, "body": exc.body,
                                                "cause": str(exc), "too_long": exc.too_long,
                                                "outcome": exc.outcome})
            if exc.too_long and state["phase"] == "initial":
                state.update(status="needs_shortening", shortened_because=str(exc), calling=None)
                write_state(run / "state.json", state)
                print(f"JEV NEEDS A SHORTER FRAMING: {exc}\n{LIMITS}\nAsk the framing's author once for a"
                      " shorter state and fewer or shorter checked facts (keep what decides the questions),"
                      f" then: ask --run \"{run}\" --framing <file> --shortened", file=sys.stderr)
                return TOO_LONG
            known = {"not_sent": "no request was sent", "answered": "Jev answered with an error",
                     "unknown": "the request may have reached Jev; its outcome is unknown"}[exc.outcome]
            cause = (f"Jev refused the shortened request as too long too: {exc}" if exc.too_long
                     else f"the Jev call failed ({known}): {exc}")
            return close_unjudged(run, state, cause, request=c, request_outcome=exc.outcome)
        except JevError as exc:  # JevUnavailable included: Jev was switched off or lost before the request
            write_new(run / f"error-{c}.json", {"at": now(), "cause": str(exc), "outcome": "not_sent"})
            return close_unjudged(run, state, f"Jev was not asked: {exc}", request=c, request_outcome="not_sent")
        except BaseException as exc:  # an interrupt: record what is known, then stop
            with contextlib.suppress(Exception):
                write_new(run / f"error-{c}.json", {"at": now(), "cause": f"interrupted ({type(exc).__name__})"
                                                    " during the call", "outcome": "unknown"})
                close_unjudged(run, state, f"the helper was interrupted ({type(exc).__name__}) during the Jev call;"
                               " the request may have reached Jev; its outcome is unknown", request=c,
                               request_outcome="unknown")
            raise
        code, outcome = judge_reply(run, state, sent_questions, c, reply)
    if code != OK:
        return code
    print_judged(run, outcome)
    return OK


def judge_reply(run: Path, state: dict, sent_questions: dict, c: int, reply: dict) -> tuple[int, dict | None]:
    """Record a reply, check it answers every question in its type, and close the run."""
    if not (run / f"response-{c}.json").exists():
        write_new(run / f"response-{c}.json", {"received_at": now(), "reply": reply})
    problems = answer_problems(sent_questions, reply)
    if problems:
        return settle(run, state, {"status": "unjudged", "at": now(), "request": c, "reply": f"response-{c}.json",
                                   "request_outcome": "answered",
                                   "cause": "Jev's reply does not answer every question in its type: "
                                            + "; ".join(problems)}, c)
    usage = reply.get("usage") or {}
    outcome = {"status": "judged", "requested_model": JEV_MODEL, "served_model": reply.get("model"),
               "usage": usage, "request_id": reply.get("request_id"), "request": f"request-{c}.json",
               "response": f"response-{c}.json", "shortened_because": state["shortened_because"],
               "answers": {qid: {"question": q, "answer": reply["answers"][qid],
                                 "shown": shown(reply["answers"][qid])} for qid, q in sent_questions.items()}}
    return settle(run, state, outcome, c)


def settle(run: Path, state: dict, outcome: dict, c) -> tuple[int, dict]:
    """Record the outcome (or adopt the identical request's outcome recorded before an interruption),
    then move the state to it. The record comes first, so a state that cannot be saved leaves the run
    `calling` with its outcome on disk; recover then adopts it rather than asking again."""
    recorded = record_outcome(run, outcome, c)
    summary = {"status": recorded["status"]}
    summary.update({"response": recorded["response"]} if recorded.get("response") else {"cause": recorded.get("cause")})
    state.update(status=recorded["status"], calling=None, outcome=summary)
    write_state(run / "state.json", state)
    if recorded["status"] == "judged":
        return OK, recorded
    print(f"JEV UNJUDGED: {recorded.get('cause')}\nNo Jev numbers exist for this run; the review goes on without"
          f" Jev and says why. Record: {run}", file=sys.stderr)
    return UNJUDGED, recorded


def print_judged(run: Path, outcome: dict) -> None:
    usage = outcome["usage"] or {}
    print(f"Jev ({outcome['served_model']}, asked for {JEV_MODEL}; {usage.get('input_tokens')} input tokens,"
          f" {usage.get('output_tokens')} output tokens). Numbers only; reading them is the orchestrator's:")
    for qid, item in outcome["answers"].items():
        ins = item["question"].get("instructions")
        print(f"- {qid} ({item['question']['type']}): {ins if isinstance(ins, str) else json.dumps(ins, ensure_ascii=False)}\n"
              f"  {item['shown']}")
    print(f"[jev] record: {run}", file=sys.stderr)


def cmd_recover(a) -> int:
    """Close a run whose helper stopped during a Jev call. Never asks Jev: a reply already saved is
    judged; otherwise the outcome is recorded as unknown."""
    if not a.confirm_stopped:
        raise JevError("recover needs --confirm-stopped: first make sure the helper that made the call has"
                       " stopped", USAGE)
    run, state, brief = open_run(a.run)
    with run_lock(run):
        run, state, brief = open_run(a.run)
        if state["status"] != "calling":
            raise JevError(f"the run is {state['status']}, not interrupted during a call; nothing to recover", USAGE)
        c = state["calling"]["request"]
        if (run / "jev.json").exists():  # the outcome was recorded; only the state was not saved
            code, recorded = settle(run, state, {}, c)
            if code == OK:
                print_judged(run, recorded)
            return code
        request = json.loads((run / f"request-{c}.json").read_text(encoding="utf-8"))
        saved = run / f"response-{c}.json"
        if saved.exists():
            reply = json.loads(saved.read_text(encoding="utf-8"))["reply"]
            code, outcome = judge_reply(run, state, request["questions"], c, reply)
            if code == OK:
                print_judged(run, outcome)
            return code
        if not (run / f"error-{c}.json").exists():
            write_new(run / f"error-{c}.json", {"at": now(), "outcome": "unknown",
                                                "cause": "the helper stopped during the call; recorded by recover"})
        return close_unjudged(run, state, f"request-{c}.json was handed to the SDK at"
                              f" {state['calling'].get('started')} and the helper stopped before recording an"
                              " outcome; Jev may have answered; the outcome is unknown and Jev is not asked again",
                              request=c, request_outcome="unknown")


def close_unjudged(run: Path, state: dict, cause: str, **refs) -> int:
    outcome = {"status": "unjudged", "cause": cause, "at": now(), **refs}
    code, recorded = settle(run, state, outcome, refs.get("request"))
    if code == OK:  # an earlier attempt of this very request had already recorded Jev's judgement
        print_judged(run, recorded)
    return code


def read_text_arg(path: str, what: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise JevError(f"cannot read the {what} {path}: {exc}", USAGE) from exc


def render(run: Path, brief: dict, state: dict, interpretation: str, technical: list[tuple[str, str]],
           dispositions: str | None) -> str:
    jev = json.loads((run / "jev.json").read_text(encoding="utf-8")) if (run / "jev.json").is_file() else None
    own = "\n".join(f"- {k}: {v}" for k, v in asked(brief).items())
    facts = "\n".join(f"- {f['fact']} ({f['source']}{'; checked by ' + f['by'] if f.get('by') else ''})"
                      for f in brief["facts"])
    asks = "\n".join(f"- {q}" for q in brief.get("ask") or []) or "- none"
    if jev is None:
        jev_line, jev_body = "not asked (no Jev step was run)", "Jev was not asked in this run."
    elif jev["status"] == "judged":
        usage = jev.get("usage") or {}
        jev_line = (f"{jev.get('served_model')} (asked for {JEV_MODEL}); {usage.get('input_tokens')} input tokens,"
                    f" {usage.get('output_tokens')} output tokens"
                    + (f"; shortened once: {jev['shortened_because']}" if jev.get("shortened_because") else ""))
        jev_body = "\n".join(f"- **{qid}** ({it['question']['type']}): {json.dumps(it['question'].get('instructions'), ensure_ascii=False)}\n  - {it['shown']}"
                             for qid, it in jev["answers"].items())
    else:
        jev_line = f"{jev['status']}: {jev.get('cause')}"
        jev_body = f"Jev gave no numbers for this run ({jev['status']}): {jev.get('cause')}"
    exact = []
    for p in sorted(run.glob("request-*.json")) + sorted(run.glob("response-*.json")) + sorted(run.glob("error-*.json")):
        exact.append(f"### {p.name}\n\n```json\n{p.read_text(encoding='utf-8').rstrip()}\n```")
    tech = "\n\n".join(f"### From {src}\n\n{text.strip()}" for src, text in technical) or \
        "No Claude technical findings were attached to this record."
    return f"""# roast: {brief['mode']} -- {brief.get('title') or '(no title)'}

- item: {brief.get('item') or 'none'}
- opened: {state['created']}
- finalized: {now()}
- Jev: {jev_line}
- this record: {run / 'record.md'}

## What was asked for (the asker's own words)

{own}

## What was done or planned, and the files (kept here; never sent to Jev)

- did: {brief.get('did') or 'not given'}
- files: {brief.get('files') or 'not given'}

## The asker's questions

{asks}

## Checked facts and their sources (reviewer evidence)

{facts}

## Claude's technical findings

{tech}

## Jev's answers (numbers only)

{jev_body}

## Codex's reading (its interpretation, not Jev's)

{interpretation.strip()}

## What was done with the findings

{(dispositions or '(Not yet recorded. Add each finding\'s fate with: note --kind disposition.)').strip()}

## Exact requests and replies

{chr(10).join(exact) or 'No Jev request was made.'}
"""


def cmd_finalize(a) -> int:
    run, state, brief = open_run(a.run)
    interpretation = read_text_arg(a.interpretation, "interpretation")
    technical = [(str(Path(p).resolve()), read_text_arg(p, "technical findings")) for p in a.technical]
    dispositions = read_text_arg(a.dispositions, "dispositions") if a.dispositions else None
    with run_lock(run):
        run, state, brief = open_run(a.run)
        if (run / "record.md").exists():
            raise JevError(f"{run / 'record.md'} already exists and is never overwritten; add later"
                           " decisions with: note --kind disposition", FAILED)
        refuse_if_calling(state, run)
        earlier = (f"; {state['calls']} earlier request(s) were handed to the SDK, their outcomes are in"
                   " error-*.json and response-*.json" if state["calls"] else "")
        if state["status"] in ("needs_correction", "needs_shortening"):
            if not (a.close_jev or "").strip():
                raise JevError(f"the run is waiting for a framing ({state['status']}); ask again, or finalize"
                               " with --close-jev \"<why Jev is not asked>\"", USAGE)
            write_new(run / "jev.json", {"status": "unjudged", "at": now(),
                                         "cause": f"closed while {state['status']}: {a.close_jev.strip()}{earlier}"})
            state["status"] = "unjudged"
        elif state["status"] == "open":
            cause = ("no Jev step was run before finalize" if not state["calls"]
                     else f"finalized before Jev answered{earlier}")
            write_new(run / "jev.json", {"status": "unjudged", "cause": cause, "at": now()})
            state["status"] = "unjudged"
        text = render(run, brief, state, interpretation, technical, dispositions)
        write_new(run / "record.md", text)
        state["finalized"] = now()
        write_state(run / "state.json", state)
        latest = run.parent / "latest.md"
        write_state_text(latest, f"Latest roast record: {run / 'record.md'}\n")
    print(text)
    print(f"[jev] record: {run / 'record.md'}", file=sys.stderr)
    return OK


def write_state_text(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".latest-", suffix=".tmp", dir=str(path.parent))
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def cmd_note(a) -> int:
    run, state, brief = open_run(a.run)
    text = read_text_arg(a.file, "note")
    with run_lock(run):
        notes = run / "notes"
        notes.mkdir(exist_ok=True)
        path = notes / f"{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%S%fZ}-{a.kind}.md"
        write_new(path, text)
    print(str(path))
    return OK


def cmd_show(a) -> int:
    run, state, brief = open_run(a.run)
    files = sorted(p.name for p in run.iterdir() if p.is_file() and p.name != "lock")
    if (run / "notes").is_dir():
        files += [f"notes/{p.name}" for p in sorted((run / "notes").iterdir())]
    if a.json:
        print(json.dumps({"run": str(run), "state": state, "files": files}, indent=2, ensure_ascii=False))
        return OK
    print(f"roast run {run}\n  mode {state['mode']}, status {state['status']}, phase {state['phase']},"
          f" framings {state['framings']}, Jev calls {state['calls']}, finalized {state['finalized'] or 'no'}")
    if state.get("outcome"):
        print(f"  outcome: {json.dumps(state['outcome'], ensure_ascii=False)}")
    for f in files:
        print(f"  {f}")
    return OK


def cmd_available(a) -> int:
    why = unavailable()
    if why:
        print(f"Jev unavailable: {why}")
        return UNJUDGED
    print(f"Jev can be asked: switch on, a key is set (value not shown), the TypeSafe SDK imports; model {JEV_MODEL}."
          " This makes no call, so it does not prove the key or the service works.")
    return OK


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, allow_abbrev=False,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    o = sub.add_parser("open", allow_abbrev=False)
    o.add_argument("--brief", required=True)
    o.add_argument("--project", default=None)
    sub.add_parser("available", allow_abbrev=False)
    for name in ("check", "ask"):
        p = sub.add_parser(name, allow_abbrev=False)
        p.add_argument("--run", required=True)
        p.add_argument("--framing", required=True)
        if name == "ask":
            p.add_argument("--shortened", action="store_true",
                           help="the one shortened framing after Jev refused the request as too long")
            p.add_argument("--framed-by", default="", help="the framing's author, e.g. its native agent purpose")
    f = sub.add_parser("finalize", allow_abbrev=False)
    f.add_argument("--run", required=True)
    f.add_argument("--interpretation", required=True, help="Codex's reading, labeled as its interpretation")
    f.add_argument("--technical", action="append", default=[], help="Claude's technical findings (repeatable)")
    f.add_argument("--dispositions", default=None)
    f.add_argument("--close-jev", default="", help="why a run still waiting for a framing closes without Jev")
    n = sub.add_parser("note", allow_abbrev=False)
    n.add_argument("--run", required=True)
    n.add_argument("--file", required=True)
    n.add_argument("--kind", required=True, choices=NOTE_KINDS)
    s = sub.add_parser("show", allow_abbrev=False)
    s.add_argument("--run", required=True)
    s.add_argument("--json", action="store_true")
    r = sub.add_parser("recover", allow_abbrev=False, help="close a run whose helper stopped during a Jev call")
    r.add_argument("--run", required=True)
    r.add_argument("--confirm-stopped", action="store_true")
    a = ap.parse_args(argv)
    handler = {"open": cmd_open, "available": cmd_available, "check": cmd_check, "ask": cmd_ask,
               "finalize": cmd_finalize, "note": cmd_note, "show": cmd_show, "recover": cmd_recover}[a.command]
    try:
        return handler(a)
    except JevError as exc:
        label = {USAGE: "USAGE", PROBLEMS: "PROBLEMS", UNJUDGED: "UNJUDGED"}.get(exc.code, "FAILED")
        print(f"JEV {label}: {exc}", file=sys.stderr)
        return exc.code


if __name__ == "__main__":
    sys.exit(main())
