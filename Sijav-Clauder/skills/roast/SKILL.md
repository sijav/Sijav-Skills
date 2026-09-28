---
name: roast
description: "Have codex push back on a plan before building, a finished task, a run of tasks, or a question: codex checks the work itself and frames typed questions, jev judges the logic, codex writes its reading."
---

Run from the repository folder. Give the real ask, point at the files, and ask about the mechanism most likely to fail.

## Usage by mode

Plan before building (codex may research official sources when an outside premise needs checking):
```sh
python "${CLAUDE_SKILL_DIR}/roast.py" plan --item "Item id" --title "Plan" --why "Purpose" --exit-condition "Success criteria" --did "The plan is <path>" --files "<the plan> <files it changes>" --ask "Which mechanism could fail, and why?"
```

Finished task:
```sh
python "${CLAUDE_SKILL_DIR}/roast.py" task --item "Item id" --title "Finished task" --why "Purpose" --exit-condition "Success criteria" --did "Changes and verification" --files "Changed paths" --ask "Does the implementation satisfy the exit condition?"
```

Run of tasks:
```sh
python "${CLAUDE_SKILL_DIR}/roast.py" technical --title "Task run" --did "Completed tasks and evidence" --files "Affected paths" --ask "Where could these changes interact incorrectly?"
```

Question (research; codex researches official sources first):
```sh
python "${CLAUDE_SKILL_DIR}/roast.py" search --title "Question" --did "Context and evidence" --ask "What evidence resolves this specific mechanism?"
```

Repeat `--ask` for more questions. `--item` is optional; it goes into the record's file name.
`--session NAME` gives the run a codex conversation of its own instead of `roast-<mode>`. Use it for a manual roast in a project whose loop also roasts, so the two never share one conversation. If `roast-<mode>` is busy in another run anyway, the roast switches to a conversation of its own by itself, and the record names it.

## What happens

1. codex reads the named files itself, checks the claims, and returns: the state for jev (logic in plain words), typed questions, and the facts it checked with their sources.
2. roast.py refuses code in anything jev would get (fenced blocks, diffs, definitions, imports, declarations, lines opening a block) and sends a framing that breaks the contract back to codex once, with the exact problems. It then sets three things itself: `checked_facts` (the words of every fact codex checked, without their sources), `what_was_asked_for` (the asker's own `--title`, `--why` and `--exit-condition`; code there stops the roast before codex is called), and in plan mode the owner's critical question (`anything_critical`) word for word.
3. `jev-latest` answers every question in one batch; the tool checks that each has an answer. jev's limits are in tokens: 32k for the state plus the longest question, 64k for the state plus all questions (docs.typesafe.ai/models). If jev refuses the request as too long (`max_tokens_exceeded`), codex shortens it once and jev is asked again; the record says so.
4. codex writes its reading: each concern as a failing scenario with evidence, then findings. It is labelled as codex's interpretation; jev's answers are shown as numbers only (a noul is a probability with no confidence; a choice or score confidence only says how concentrated its probabilities are).
5. The run's own record goes to `<repo>/.claude/roasts/<time>-<mode>[-<item>]-<tag>.md` and is never overwritten: what was asked, the checked facts with sources, jev's answers, the served model and usage, codex's reading, a section for what was done with each finding, and the exact request and reply. `<repo>/.claude/roast-result.md` is a copy of the latest record. The record is also printed.

codex runs in the `roast-<mode>` session on gpt-6-sol at medium effort; search mode is research and runs on gpt-6-astra at high effort.
The repository is the nearest ancestor containing `.git` or a `.claude` folder, otherwise the current folder.
Failures print their exact cause and exit non-zero. Nothing gates anything: the numbers are for the reader to judge.

## Switches and fallbacks

Set these per user or per project in Claude Code's settings, under `env`.

- **The jev key:** the file `SIJAV_JEV_KEY_FILE` names, else `TYPESAFE_API_KEY`. Keep the key file outside the plugin's folder: Claude Code copies an installed plugin into its own cache, files and all.
- **`SIJAV_JEV=off`:** the roast runs without jev: codex reviews alone and the record's jev line says why. The same happens by itself when jev cannot be used (no key, or the TypeSafe SDK is missing) and when jev fails during the run (it refuses, or a request is still too long after one shortening).
- **`SIJAV_CODEX=off`:** no roast at all; `roast.py` exits with code 3 and starts nothing. Review the work yourself instead: the concrete failing scenarios, whether anything is critical (it would definitely break the whole thing asked for, must be fixed right away, and is neither a later task nor a feature), and the findings worth acting on. Say in your report that codex was off.
- **codex fails** (not installed, or its allowance is used up): the roast fails with codex's own words. For a used-up allowance, ask the owner to switch the codex account; for a missing codex, review the work yourself as above.
