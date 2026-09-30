---
name: codex
description: "Call codex (GPT-6) through the purpose-session runner for research, web lookups, and project prose or jev questions where a project's rules say codex writes them."
---

# Call through a purpose session

Use the global runner for every agent-initiated Codex call:
`python "${CLAUDE_SKILL_DIR}/codex_session.py" --purpose <name> <prompt-file> [<reply-file>] [--search] [--model M] [--effort E] [--fresh] [--project DIR]`
Quote paths containing spaces.
Write the prompt to a file and pass its path, never prompt text, as the positional argument.
The runner passes the prompt through stdin and runs Codex read-only. Apply reviewed results yourself under the project's change process.
The runner discovers the project from `$CLAUDE_PROJECT_DIR` or the topmost ancestor of the working directory containing `.claude`. Pass `--project` with the absolute project root for certainty; discovery does not require it.
Supply a reply-file path when the returned text must be saved. Read the runner's reply or that file; do not parse raw CLI display delimiters.
Do not invoke raw `codex exec`, `codex exec resume`, or a Codex binary yourself. Runner failure does not authorize a raw fallback.
Before selecting a purpose, inspect the intended project's `.claude/codex-sessions/`. Read the existing names: `<purpose>.json` for runner sessions and `.txt` files for project-tool sessions.
Identify the work's function and line of work, then match them to existing sessions using names and available records. Do not assume a session's scope from its name alone when ambiguous.
Reuse an existing matching purpose with its exact spelling. Preserve `board-text`, `commit-messages`, and `roast-<mode>` managed by `roast.py`; do not invent aliases such as `commit-message` for `commit-messages`.
For a matching project-tool session, use its owning tool and existing session selection. Do not pass a `.txt` session name to the runner or create a parallel `.json` session for that same work.
Treat follow-ups, corrections, debate, retries, and later turns on the same work as the same line of work. Keep its selected purpose unless the function changes or the owner explicitly requests a reset.
Treat existing function-wide purposes such as `board-text` and `commit-messages` as shared streams for that function. Identify the current item in each prompt; do not split those streams into per-item aliases or use them for unrelated functions.
Create a purpose only after finding no matching existing session. Use a stable function name and, where separate context is needed, a stable task or topic identifier.
For new lines of work, use names such as `plan-<id>`, `plan-roast`, `task-roast`, `jev-question`, and `research`. These are naming examples, not instructions to create sessions that already have a matching name or tool-managed session.
Keep distinct task plans in `plan-<id>` sessions. For other new purposes, state whether the session serves a function-wide stream or a specific task/topic in its first prompt; follow that scope thereafter.
If several sessions appear to match, inspect their available records to identify the established session. Do not guess, rename, or start another session to avoid resolving the match; ask only if the records cannot resolve it.
Use only lowercase letters, digits, `.`, `_`, and `-` in purpose names.
An existing purpose resumes its session; a new purpose starts one. Records live under the project's `.claude/codex-sessions/<purpose>.json`.
Do not delete session records or use `--fresh` to bypass context. Use `--fresh` only for an explicit owner-requested reset; retain the work's purpose.
Let the runner perform its built-in full-thread retirement once. Do not add manual fresh retries around it.
Do not run concurrent calls against the same purpose session. Inspect and await existing work before another call.
Use `.claude/codex-sessions/logs/` for each call's full stdout, stderr, command, working directory, times, and exit status.
Preserve the exact failure cause and log path. Investigate before retrying; never replace a failed session call with a purpose-less call.
Pass the chosen model and effort through `--model` and `--effort`; run simple web lookups as one plain question with `--search`, not the full `roast.py search` research pipeline. (owner, 2026-09-27)
Use the runner's `--search` option for web research. Keep all model, allowance, research, and review rules below.

# Existing callers and exceptions

Use the roast skill's existing `roast.py` entry point; it already uses the runner with purpose `roast-<mode>`.
Where a project prescribes its own codex entry points, use them. Keep their existing sessions under `.claude/codex-sessions/`; do not wrap them in another Codex call.
These session-aware implementations satisfy the session requirement. They do not authorize other raw calls or new bypass launchers.
Where a project's rules name callers that must make fresh calls, leave them unchanged and use them only for their intended work. Do not route unrelated prompts through them or extend their exception to other callers.
For every other agent-initiated call, use the runner with an explicit purpose.

Follow the project's model and reasoning settings. Otherwise use the runner's default: gpt-6.1-sol at medium effort.

Ask for official, current sources, cited URLs, and quoted lines.
Ask codex to state where the documentation is silent.
Never fix code on a guess.
Research the current documentation first, then change code based on it.

Use `gpt-6.1-sol` at medium effort by default (owner, 2026-09-30: 6.1 sol replaces 6 sol; it needs codex 0.159 or newer). Use `gpt-6-astra` for research (the roast's search mode and the research skill use it at high effort) or when the owner asks for it, and `gpt-6-luna` for fast, cheap tasks, such as a plain web search at low effort (the search skill).
A session keeps its thread when the model changes: each call uses the model it is given, else the default.
Treat all GPT-6 models as sharing ONE allowance; there is no separate astra allowance to exhaust.
When the shared GPT-6 allowance is used up, ask the owner through the question/input tool to change the codex account; do not switch to `gpt-reserve`. (owner, 2026-09-26) The one exception is a plain web search: the search skill retries once on `gpt-reserve`, a luna that stays free when the allowance is used up (owner, 2026-09-30).
Wait for the owner's answer confirming the account switch, then run again through the runner in the same session. If direct continuation fails with "Encrypted content organization_id did not match the target organization", use `copy_session.py` in this skill's folder as a manual tool, not a runner fallback, and point the job's record at the session id it prints. (owner, 2026-09-26)
Do not create `.stop` or schedule resumption because of allowance exhaustion; leave login changes to the owner and never sign in or read or copy login tokens. (owner, 2026-09-26)

Project rules about when codex writes and how its output is checked live in each project's rule set (`.claude\rulesets\` at the project root).

# Switch and fallback

- `SIJAV_CODEX=off`, set per user or per project in Claude Code's settings under `env`: the runner starts nothing, raises `CodexOff` at once, and the command exits with code 3. Do the work yourself instead: research with your own tools, write the text yourself, and say that codex was off.
- If codex is not installed, the runner says so; install it, or set `SIJAV_CODEX=off`.
- codex signs in with its own login, which the owner manages; this skill holds no codex token.
