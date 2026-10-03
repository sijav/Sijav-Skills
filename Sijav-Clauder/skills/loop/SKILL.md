---
name: loop
description: Engage this session's own loop and follow its law for execution, pausing, and completion. A loop is per session; its loop file sets its board and areas.
hooks:
  SessionStart:
    - matcher: compact
      hooks:
        - type: command
          command: python "${CLAUDE_SKILL_DIR}/compact.py"
  Stop:
    - hooks:
        - type: command
          command: python "${CLAUDE_SKILL_DIR}/loop_stop.py"
---

# A loop is per session

A project may run several loops at once, one per session. Each loop has its own loop file, `.claude/<name>loop<...>.local.md`: its law, with front matter saying

- `session:` the one session it drives;
- `board:` its to-do board (optional; else the nearest `.claude/todo.db`);
- `areas:` the owner's areas for it, `back,ai` (optional; else every area);
- `driver: skill` when this skill's hooks drive it; a loop the project's own hook drives leaves it out;
- `active`, `iteration`, `max_iterations` and `completion_promise` for the Stop hook.

The to-do script reads the loop file of the session running it, on every command: `next` offers only the loop's areas, `--area` can only narrow them, and starting a task outside them is refused. So a loop never needs to remember its areas, and two loops with different areas never take the same task.

Invoking this skill registers its hooks in this session only: they never run in another session, and they drive only a loop file that names this session and says `driver: skill`. A loop the project's own hook drives is left to that hook. Never read, follow, edit or pause another session's loop file, and never change another session's hooks or settings.

# Engage the loop

1. Locate the project root.
2. Find your loop file: the `.claude/*loop*.local.md` whose `session:` is this session (`CLAUDE_CODE_SESSION_ID` in a command's environment).
3. If the project's own Stop hook drives your loop (no `driver: skill`), confirm in `.claude/settings.local.json` that its Stop hook and a SessionStart hook with matcher `compact` re-feed it, and report the exact missing path or setting if not.
4. If your loop file says `driver: skill`, this skill's own hooks re-feed it: `loop_stop.py` after each turn, `compact.py` after a compaction.
5. Read and follow the law in force; apply its requirements and exceptions instead of conflicting skill defaults. Resolve setup choices under the project's rules.
6. When the owner instructs you to start or resume (an explicit `/sijav-clauder:loop` invocation counts), start the loop the way its law says. Honor later owner instructions postponing or cancelling it; unrelated messages and hook invocations do not authorize it.
7. Perform each turn according to the law and the rules it names. Let the Stop hook continue the loop after each turn; use the compaction re-feed at once after a compaction.

# Rules

The loop runs under the rules its law names. Read them when the loop starts.

# Pause and finish

- `.stop` in the project root pauses every loop whose hook honours it; `.claude/<loop file name>.stop` (beside `<name>.local.md`) pauses only that loop. Create either only when the owner explicitly asks to stop or pause. A used-up allowance means asking the owner to switch accounts, not pausing. Killed commands, interrupted calls, explanation requests, and other blockers do not authorize a pause.
- Keep paused work marked as unfinished.
- Read the exit condition and completion promise from the law.
- End with `<promise>VALUE</promise>` only when that exit condition holds, with `VALUE` the law's exact completion promise.
- Keep project-specific procedures and decisions in the project's law.
