---
name: loop
description: Engage a project's own Stop-hook loop and follow its law for execution, pausing, and completion.
hooks:
  SessionStart:
    - matcher: compact
      hooks:
        - type: command
          command: python "${CLAUDE_SKILL_DIR}/compact.py"
---

# Engage the loop

1. Locate the project root.
2. Find the project's loop law file.
3. The law is the project's `.claude/*loop*.local.md` file, the one its Stop hook reads. A project may run one loop per session, each with its own law: yours is the one whose front matter names your session (`session:`). Never read, follow, edit or pause another session's law, and never let your loop feed another session.
4. Read and follow the project's law in force; apply its requirements and exceptions instead of conflicting skill defaults.
5. Inspect `.claude/settings.local.json`.
6. Confirm that its Stop hook invokes the project's loop hook.
7. Confirm that the Stop hook re-feeds the law after turns and a SessionStart hook with matcher `compact` re-feeds it after compaction, using either the project's hook or this skill's `compact.py` fallback.
8. Report the exact missing path or configuration problem if either required re-feed is absent; account for the skill hook's intentional silence when a project compaction hook takes over, `.stop` exists, or no law exists.
9. Resolve setup choices under the project's rules.
10. When the owner instructs you to start or resume (an explicit `/sijav-clauder:loop` invocation counts), start the loop the way the project's law says, for example with a command that makes this session the loop's only session and deletes `.stop`. Where the law says nothing, delete the project-root `.stop` file. Honor later owner instructions postponing or cancelling resumption; unrelated messages and hook invocations do not authorize it.
11. Perform each turn according to the project's law and the rules it names.
12. Let the Stop hook continue the loop after each turn; use the compaction re-feed immediately after compaction without waiting for a turn end.

# Areas

The owner may give a loop areas, in its law's front matter (`areas: back,ai`). Work only in them: pick every task with `todo next --area <the areas>`, never resume started work outside them, and file what you find outside them for the owner instead of doing it. Never choose or widen your own areas. After a compaction this skill's hook says the areas again.

# Rules

The loop runs under the rules its project's law names. Read them when the loop starts.

# Pause and finish

- Create the project-root `.stop` file only when the owner explicitly requests stopping or pausing the loop. A used-up allowance means asking the owner to switch accounts, not pausing. Killed commands, interrupted calls, explanation requests, and other blockers do not authorize a loop pause.
- `.stop` in the project root pauses every loop whose hook honours it; `.claude/<law>.stop` (beside `<law>.local.md`) pauses only that loop. Pause only your own loop unless the owner says otherwise.
- Keep paused work marked as unfinished.
- Read the exit condition and completion promise from the project's law.
- End with `<promise>VALUE</promise>` only when that exit condition holds.
- Replace `VALUE` with the law's exact completion promise.
- Keep project-specific procedures and decisions in the project's law.
