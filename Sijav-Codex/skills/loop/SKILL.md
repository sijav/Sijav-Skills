---
name: sijav-codex-loop
description: Engage an implemented project loop in Codex through native Stop and compaction hooks, following the project's law, session ownership, pause and completion conditions. Use when the owner explicitly starts or resumes the loop.
---

# Engage the project's loop

The helpers and hook definitions are implemented. They pass offline tests that use simulated payloads and run the real hook commands through PowerShell, cmd and sh. On Windows, Codex was observed running hooks through the user's PowerShell.

A live run in a disposable fixture on Codex 0.159.3 passed every check for the project-hooks form:

- Stop continuations ended in the exact promise.
- Identical replies were exhausted at the cap without completing.
- After a manual compaction, the SessionStart compact hook reloaded the whole law. The model's first answer used it, with no continuation.

A later run repeated that result, with the requested `gpt-6.1-sol`/`medium` verified as the thread default.

The installed native-plugin form also passed all A/B/C checks on Codex 0.159.3 in a disposable TEMP fixture, with both handlers normally trusted and enabled. Codex expanded `${PLUGIN_ROOT}` to the installed package; the fixture had no project hook file. The public output is [installed-native-loop-proof.txt](../../validation/installed-native-loop-proof.txt). The earlier root portable `plugin.json` loaded no hooks, so the package uses `.codex-plugin/plugin.json`. Automatic mid-turn compaction remains empirically untested. No actual project loop has started. Report a loop as running only when its own hook events show it.

Locate the project root and the exact law the owner intends this session to follow. Prefer an explicitly named law. Existing projects can keep their law under `.claude`; do not rename their board, rules or law solely because Codex is the caller. If several laws exist and the records do not identify the intended one, resolve that ambiguity before starting.

Read the law and the rule sets it names. Use the rules skill when loading the Sijav sets. Project-specific procedures stay in the project's law. Codex is the orchestrator: use native Codex agents for the jobs delegated by the agents, search and research skills; only the owner's requested Claude Opus 5.5 may write implementation code and tests or perform technical code roast. Follow the current owner's instructions about whether Claude may run. Exhaustion, authentication failure or Claude being disabled does not authorize Codex to take over these jobs.

Before engaging the loop, establish that:

- The installed runtime supports native synchronous `Stop` hooks, and the exact `Stop` handler is loaded, trusted through Codex's own `/hooks` review and enabled.
- The `SessionStart` handler matching `compact` is loaded, trusted and enabled too, so the law is reloaded before the next model request after compaction. A Stop-only refeed is insufficient.
- Both hooks check the same explicitly claimed Codex session. Other main sessions and delegated agents must not receive the loop's continuation prompt.
- The installed helpers keep Codex ownership and counters separate from an existing Claude session. Starting Codex must not edit that Claude loop's owner or counters.

If a required helper or configuration is absent, report its exact path and what remains unverified. Hook trust is Codex's native review flow; never bypass it or manufacture a trust record. To review: run `codex` in the project, open `/hooks`, choose Events, then the event, then the concrete handler. Inspect it and press `t` to trust that handler, and enable it if it is disabled. Never press `t` on the Events list itself, because that trusts every hook. A changed definition needs a fresh review.

An explicit invocation of this skill authorizes starting or resuming this loop, subject to later owner steering. Claim only this session, then use the implemented start helper prescribed by the law. Do not start the loop just because its files exist or because another skill loaded it.

Ownership comes from Codex itself. Every command Codex runs carries `CODEX_SESSION_ID`, the root session, and `CODEX_THREAD_ID`, the thread running it. They are equal only in the root session, which is the one whose `session_id` the hooks receive. Claim with `--session-from-env`. The helper uses `CODEX_SESSION_ID` only after checking that both variables are present and equal, so a delegated agent or partial environment is refused. An explicit `--session ID` must match them when they are present. Outside Codex, for example from an operator's own terminal, neither variable is set and `--session` must give the real session id. If the variables are missing inside Codex, report that instead of guessing.

The helper is `scripts/sijav_loop.py`. Resolve it from this installed loop skill to an absolute path and run it with the chosen project as the working directory; the examples below abbreviate that installed helper path. The law, cap and promise are explicit. The cap and promise may come from the law's front matter only through `--max-iterations-from-law` and `--promise-from-law`, and only when the values are actually there. The cap is the owner's policy and has no built-in ceiling.

```
python scripts/sijav_loop.py start --project-root DIR --law FILE (--session-from-env | --session ID)
    (--max-iterations N | --max-iterations-from-law) (--promise TEXT | --promise-from-law)
    [--sentinel FILE ...] [--takeover PREVIOUS_SESSION] [--restart]
    [--clear-sentinels [--clear-claude-pause]] [--confirm-root]
python scripts/sijav_loop.py resume (--session-from-env | --session ID) [--clear-sentinels [--clear-claude-pause]]
python scripts/sijav_loop.py pause --reason TEXT [--session ID]
python scripts/sijav_loop.py stop --reason TEXT [--session ID]
python scripts/sijav_loop.py status [--session ID] [--json]
python scripts/sijav_loop.py reset (--session-from-env | --session ID)
```

Pass each flag only when its case applies and, where noted, the owner has asked for it:

- `--clear-sentinels`: present pause sentinels block `start` and `resume` unless this is given, and the owner must have explicitly asked to clear them. Cleared sentinels are moved into the state folder, not deleted.
- `--clear-claude-pause`: also required when the law's front matter names a Claude session. That session honours the same `.stop`, so clearing it resumes the Claude loop too.
- `--restart`: required to reset the counter of a run that is active or paused and already counted. A restart must come from the owner, never from inside the loop.
- `--confirm-root`: `start` refuses a root below the law's own project, a root inside another repository, and a Codex session working outside the root, because hooks there would never find the state. This flag overrides that check only when the root is truly right.
- `--takeover PREVIOUS_SESSION`: replaces a claim held by another session, after that owner authorizes the move.

State lives in `<project>/.codex/sijav-loop/state.json`, and every hook decision is appended to `events.jsonl` beside it. `<project>/.stop` is always a pause sentinel. Pass every other sentinel the law names with `--sentinel`; each must be inside the project and may not be the law or the state.

# Turns and compaction

Continue the current item from its first unfinished step. Record progress and the evidence in the project's own records so compaction does not cause duplicate planning, reviews or implementation. Follow the project's actual board picker; never substitute a model's ranking.

At a stop boundary, let the synchronous native Stop hook send the law back as the next prompt while this session is armed and the completion condition is unmet. The hook must use Codex's native payload rather than parsing a Claude transcript. Repeated hook continuations are expected while valid work remains; use the explicitly configured iteration cap and operator controls to bound them. Every Stop callback of the owning session counts as one iteration, even when it repeats the previous final message; set the cap with that in mind.

After compaction, use the freshly reloaded law immediately. Treat a summary as a claim to check against the records rather than as proof that a step was completed.

# Pause and completion

- Honor an owner-requested pause or stop immediately. Only such a request authorizes creating the project's pause sentinel. `pause` creates `<project>/.stop` first and then records the pause in the state. It exits 0 when both worked and 3 when the pause holds through only one of them, reporting the other's cause; exit 1 means neither took effect. Keep unfinished work unfinished.
- Interrupted commands, questions and allowance exhaustion do not by themselves authorize creating a pause file. If a required provider is unavailable or explicitly disabled, do not keep launching it; explain the missing prerequisite and retain the work's state.
- Use the law's exact completion promise only after checking its actual exit condition. Reaching an iteration cap or failing a helper is not completion.
- The promise counts only as the last non-blank line of the final message, exactly `<promise>VALUE</promise>` with no indentation and outside any code fence. A tag anywhere else, or in an earlier message, does not end the loop.
- Preserve the source three-pass distinction: built, tested, and tested as a real user are separate. A recorded Done status alone is not evidence that all three passes finished.

The mechanism, proof results and remaining runtime limits are specified in [loop-design.md](references/loop-design.md).
