---
name: sijav-codex-rules
description: Load the owner's general, development or design rules and the current project's own rules only when the owner invokes this skill; also use its rule-history procedure for changing a rule.
---

# Rules

Sijav rule sets start unloaded. Load them only when the owner calls this skill.

## Loading a set

- `$sijav-codex-rules`: read [general.md](general.md).
- `$sijav-codex-rules dev`: read [general.md](general.md) and [dev.md](dev.md),
  then use `$sijav-codex-dev-round`.
- `$sijav-codex-rules design`: read [general.md](general.md) and
  [design.md](design.md).
- Then read every `.md` file except `log.md` in the current project's rule-set
  directory. Use the directory its law names; otherwise retain the existing
  `<project root>/.claude/rulesets/` contract. A project may explicitly opt into
  `.codex/rulesets/`. Do not silently move its rules or load both copies.
- A project rule wins over a general rule it clashes with in that project.
- Say in one line which files you loaded.

## Changing a rule

Every rule set has a `log.md` beside it: the old text, why it existed, the
owner's words and the evidence. The log is the guard; nobody else has to
approve a rule.

1. Find the rule by ID and its file; read its history entries in that folder's
   `log.md`.
2. If the change would bring back a problem recorded there or contradict the
   owner's words, ask the owner first. Otherwise proceed within the authorized
   work.
3. Back up the file into a dated backup folder with a `.bak` ending, outside
   any `.claude`, `.codex` or discoverable skill folder, so the copy cannot load
   as a rule.
4. Edit it and delete the replaced rule in the same edit. A rule lives in one
   place only; temporary project overrides follow G2.
5. Append the date, rule ID, old text, new text, owner's words and evidence to
   the log.

Keep these opt-in rule sets out of `AGENTS.md`, `CLAUDE.md` and memory files:
those may load without the owner's invocation.

## Codex port's division of work

Codex orchestrates and uses native agents for search, research, project prose,
logical reasoning and the other delegated work. Claude Opus 5.5 alone writes
implementation code and test code and performs code/technical roast. Use
`$sijav-codex-agents` for native work and the Claude caller described in
`$sijav-codex-roast` for coding or technical review. Do not substitute a Codex
coder when Claude is unavailable, disabled or the owner has postponed calls.

This division changes who performs the work; it does not change G1-G6 or
D1-D5. Preserve project-law self-checks and the general G3 question check.
