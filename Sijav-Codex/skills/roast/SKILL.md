---
name: sijav-codex-roast
description: Review a plan, finished task, task run or research question using concrete failing scenarios; Claude Opus 5.5 alone reviews code and technical mechanisms, while native Codex agents and Jev handle logical reasoning.
---

# Roast the concrete mechanism

Give the real ask, the item title/ID where applicable, why, exit condition,
what was planned or done, affected paths and observed evidence. Ask about the
mechanism most likely to fail; generic requests produce generic answers.

Modes retain their meanings: `plan` before building; `task` for a finished
task; `technical` for interactions across a run of tasks; `search` for a
question needing source-backed investigation. The actual content determines
whether code/technical review is required, not the mode's label alone.

## Who does what

A roast is a sequence the orchestrator (Codex) drives. Two helpers automate
fixed steps; everything else is native work.

| Step | Done by |
| --- | --- |
| Technical/code inspection, technical findings | Claude Opus 5.5 through `claude/claude_session.py` (helper) |
| Checking noncode facts, framing the state and typed questions | native Codex agents via `$sijav-codex-agents` (instructions) |
| Refusing code, checking the framing against the brief, asking Jev, keeping the record | `roast/jev.py` (helper) |
| The logical interpretation: failing scenarios read from the evidence and Jev's numbers | a native Codex agent, in the roast's purpose (instructions) |
| Judging each finding (fix, file, dismiss and why), filing on the board, finalizing the record | the orchestrator (instructions) |

Resolve both helpers from the installed `skills/` folder (siblings of this
skill) and pass absolute paths; they find the project from `--project` or the
nearest `.claude/todo.db`, `.git` or `.claude` above the working directory, never
from their own location, and create no board.

## Route and preserve context

- Claude Opus 5.5 alone examines implementation code or performs technical
  code review, including technical portions of a plan/task review. Do not fall
  back to a Codex code reviewer or coder when Claude fails, is
  unavailable/disabled or its calls have been postponed.
- Native Codex agents handle noncoding logical reasoning, official-source
  research, code-free Jev questions and interpretation. Use
  `$sijav-codex-agents`: default `gpt-6.1-sol`, medium; research/search mode
  uses `gpt-6-astra`, high. Preserve explicit project/owner settings.
- Preserve established `roast-<mode>` purposes and tool-owned context. A
  manual review concurrent with a loop needs its own stable purpose; never
  run both in one conversation. Record any separate purpose chosen because
  an existing one is busy. Keep corrections and debate with their original
  line of work.

### The Claude technical review

A purpose's first call names its scope and carries the project's law (or says
`--no-project-rules`), because Claude runs in safe mode without the project's
`CLAUDE.md`; a later call of that existing purpose sends only the brief:

```
python "<skills>/claude/claude_session.py" call --purpose roast-technical --mode technical --scope "technical roast of <item or function>" --rules-file "<project>/<law file>" --prompt-file "<brief.md>"
python "<skills>/claude/claude_session.py" call --purpose roast-technical --mode technical --prompt-file "<follow-up.md>"
```

Technical mode offers Claude only Read, Glob, Grep and WebFetch: it cannot write
or run commands, reads outside the project or of common secret files are
denied, and no argument adds tools or picks another model. Later calls resume
the exact recorded conversation. Exit 4 means the purpose is busy in another
call: wait, or give a concurrent manual review its own purpose. Exit 5 means the
purpose needs the owner's resolution (for example an unconfirmed first call).
The reply goes to stdout; the full record is under
`<project>/.codex/claude-sessions/<purpose>/runs/`. See `$sijav-codex-agents` for
the helper's full contract.

## Facts, Jev and interpretation

1. Obtain the reviewer's checked facts and their sources, then frame the
   logic in plain words and typed questions for Jev. Native agents must not
   replace Claude's technical inspection by reading code themselves to roast
   it. Reproduce factual reviewer evidence faithfully; label unknowns.
2. Send no code to Jev: no fenced source, diffs, definitions, imports,
   declarations or block-opening source lines. A brief's title, why and exit
   must also be code-free. If the framing violates this, return the exact
   problems to its author once; do not strip context silently or send a bad
   framing. Include checked fact text without source syntax, the asker's
   own requested result, and in plan mode the critical question verbatim.
3. Use `jev-latest` to answer the questions in a batch and check that each has
   an answer. Respect Jev's token limits: 32k for state plus the longest
   question; 64k for state plus all questions. If Jev reports
   `max_tokens_exceeded`, shorten once and retry once, recording that change.
4. A native Codex agent writes the reading as concrete failing scenarios with
   evidence and actionable findings. Label this as Codex's interpretation. The
   orchestrator then judges what happens to each finding. Show Jev's
   numbers faithfully: a noul is a probability without confidence; a choice
   or score's confidence describes how concentrated its probabilities are.
   A number is not a gate or automatic instruction.

Steps 2 and 3 and the record are `jev.py`; its `--help` gives the exact JSON
shapes.

Each command is one line; `open` prints the run folder, `available` makes no
call, `check` validates only, and `[...]` marks an optional part:

```
python "<skills>/roast/jev.py" open --brief "<brief.json>" [--project "<project>"]
python "<skills>/roast/jev.py" available
python "<skills>/roast/jev.py" check --run "<run>" --framing "<framing.json>"
python "<skills>/roast/jev.py" ask --run "<run>" --framing "<framing.json>" [--framed-by <purpose>] [--shortened]
python "<skills>/roast/jev.py" finalize --run "<run>" --interpretation "<reading.md>" [--technical "<claude-findings.md>"] [--dispositions "<fates.md>"] [--close-jev "<why>"]
python "<skills>/roast/jev.py" note --run "<run>" --kind disposition --file "<fate.md>"
python "<skills>/roast/jev.py" show --run "<run>"
python "<skills>/roast/jev.py" recover --run "<run>" --confirm-stopped
```

- The brief is the orchestrator's: `mode`, `item`, the asker's own `title`,
  `why` and `exit_condition`, `did`, `files`, the asker's questions `ask`, and the
  reviewer's `facts` (each a plain sentence with its `source`). `open` refuses a
  brief whose facts, questions or asked-for words hold code (exit 4) and creates
  nothing; restate facts without source syntax. If the asker's own words hold
  code, Jev cannot be asked for that roast; say so in its record.
- The framing is the native agent's: `state`, `questions` and optionally
  `observed`, a selection of the brief's facts word for word. It may not supply
  `what_was_asked_for`, `checked_facts` or `anything_critical`: the helper sets
  them from the brief and, in plan mode, adds the owner's critical question
  verbatim. Every asker question must appear word for word in some question.
  `did`, `files` and sources never reach Jev.
- Exit 4: send the listed problems back to the framing's author once. A
  corrected framing that still breaks the contract closes the run without Jev
  (exit 1). Exit 5: Jev refused the request as too long; ask the author once
  for a shorter state and fewer or shorter facts, then `ask --shortened`. A
  second refusal is final.
- Exit 0 prints Jev's numbers in Jev's terms; the reply must answer every
  question in its type, with no missing or extra answers, or the run is
  unjudged. Exit 3 (unjudged) records the exact cause and no numbers, and says
  what is known about the request: not sent, answered with an error, or outcome
  unknown (it may have reached Jev).
- Jev is asked once per run; a new review round opens a new run. While a
  request is in flight the run is `calling`. If the helper stops then, every
  command refuses until `recover --confirm-stopped` records the outcome as
  unknown, or judges a reply already saved. Nothing asks Jev again.
- `open` records the brief's sha256. A brief edited afterwards is refused, so
  the original ask cannot change after framing.

The run folder `<project>/.codex/roasts/<time>-<mode>[-<item>]-<tag>/` keeps,
never overwritten: the brief, every framing received and its check, the exact
request and reply (served model, usage, request id) or error of every Jev call,
and the outcome. `finalize` writes `record.md` once: the original ask,
checked facts with sources, Claude's technical findings as attached, Jev's
answers, requested/served model and usage, Codex's labeled interpretation,
the exact requests and replies, and what happened to each finding. Later
decisions go into `notes/` with `note`; `<project>/.codex/roasts/latest.md`
points to the newest record. Record exact failures and full background outputs,
commands, cwd, timestamps and statuses (the Claude helper does this for its
calls).

Finished tasks close before their background roast. Use the project's board
contract and `$sijav-codex-todo`: findings become provenance children of the
task, and after its last open child closes review the whole parent-plus-children
again. Record the review with the board's `roast <id> --file <record.md>`. Do
not reopen finished work or invent review gates. Apply the newest three-pass
testing rule through `$sijav-codex-dev-round`.

## Switches and failures

- `SIJAV_JEV=off`, missing key/SDK or Jev failure: complete the available
  reviewer/interpretation work without Jev and say exactly why Jev was absent
  (`jev.py` exits 3 and records the cause). Key source is `SIJAV_JEV_KEY_FILE`,
  otherwise `TYPESAFE_API_KEY`; key files stay outside the distributable
  plugin, and the helper never writes or prints the key.
- Jev needs typesafe-sdk 0.7.x (0.7.1 checked; it needs Python 3.10+). Another
  series counts as unavailable. The SDK's own retries are turned off
  (`RetryPolicy(max_retries=0)`) and each HTTP operation has a 60 s timeout, so
  nothing is retried behind the record.
- `SIJAV_CODEX=off`: start no delegated Codex work. The orchestrator may
  perform authorized noncoding checks itself and says the switch was off;
  technical/code review still belongs solely to Claude.
- Claude failure/off/postponement: preserve the exact cause and unfinished
  technical work; never reinterpret that as permission for Codex technical
  review. Do not substitute a different Claude model without owner authority.
  `SIJAV_CLAUDE=off` makes the Claude helper exit 3 without a call; it never
  retries or falls back to another model.
- Codex exhaustion: ask the owner to switch account, then continue the same
  work. Other failures keep their exact cause and log path. Nothing is retried
  merely to change an inconvenient answer.

On October 1, 2026 two real technical-mode calls through `claude/claude_session.py`
(Claude Code 2.1.286, a disposable TEMP project) both succeeded and kept one
conversation: the CLI reported `claude-opus-5-5`, only Glob, Grep, Read and
WebFetch, the normal permission mode and its own sign-in. Failure, unconfirmed,
interrupted and code-mode paths are tested offline with a fake CLI pinned to
that version's help and init. `jev.py` is tested against the real
typesafe-sdk 0.7.1 through a local, in-process transport and with a fake SDK
for every failure path. No live Jev call has been made. The service, the key
and model access are confirmed only when a real call runs.
