---
name: sijav-codex-agents
description: Delegate noncoding work to native Codex agents with the required model, effort and a recorded purpose; use for research, lookups, logical reasoning and project prose, while Claude alone codes and performs technical review.
---

# Native agents by purpose

Codex is the orchestrator. Use the environment's native agent tools for
noncoding work instead of recursively launching `codex exec`, a Codex binary
or the source plugin's external `codex_session.py`. Claude Opus 5.5 alone
implements code and tests and performs code/technical roast. Do not send those
jobs to native Codex agents.

## Select and preserve the line of work

Before delegation, locate the intended absolute project root, inspect its
native agent records under `.codex/agent-sessions/`, and inventory existing
`.claude/codex-sessions/` names and their available records. Identify function
and line of work from evidence rather than assuming scope from a name.

- Reuse a matching purpose's exact spelling. Preserve `board-text`,
  `commit-messages` and tool-managed `roast-<mode>` streams. Do not invent
  aliases or split function-wide prose streams into item-specific duplicates;
  identify the current item in each brief instead.
- Keep distinct task plans under `plan-<id>`. For a new purpose, state whether
  it is function-wide or specific to a task/topic in its first brief. Use only
  lowercase letters, digits, `.`, `_` and `-` in purpose names.
- Follow-ups, corrections, debate, retries and later turns on the same work
  retain that purpose. Create one only when no matching purpose exists.
- Each independent plain search through `$sijav-codex-search` is the explicit
  exception: give its fresh native agent a unique one-shot purpose. This
  creates a new search, not a reset of an existing purpose; follow-ups and
  retries for that search keep its purpose.
- A project's `.txt` tool-session record belongs to its owning tool. Use that
  prescribed entry point and session selection only after confirming that its
  routing follows this port: native Codex agents for noncoding work, Claude
  Opus 5.5 for implementation, tests and code/technical roast. If the caller
  is incompatible, preserve its records and context, report the mismatch and
  leave that call unfinished. Do not treat its record as a native agent ID,
  run an external Codex bypass or create a parallel stream for the same work.
- Resolve ambiguous matching sessions by reading their records. Ask only when
  that evidence cannot identify the established work.

Associate a native purpose with its real agent ID, scope, model and effort,
briefs, replies and durable context. Reuse the live agent with follow-up tools
and await existing work before another call; never run concurrent turns for
one purpose. Native IDs are runtime-specific: a saved ID does not prove an
agent still exists after a restart. If its mapping is stale or continuation
fails, record and report the exact cause and inspect saved context. Do not
silently spawn a duplicate and claim it resumed. Any necessary recovery keeps
the established purpose and records that continuity limitation.

Do not delete records or reset context to bypass an inconvenient result. A
fresh reset requires an explicit owner instruction and retains the purpose.
An existing external runner's retirement/reset mechanism does not apply to a
native agent automatically.

## Models and briefs

Follow the project's settings first. Otherwise use:

- `gpt-6.1-sol`, medium effort, for ordinary delegated work.
- `gpt-6-luna`, low effort, for a plain one-question web lookup through
  `$sijav-codex-search`.
- `gpt-6-astra`, high effort, for `$sijav-codex-research`; use `xhigh` for R&D
  research or the effort the owner explicitly requests.

Pass the exact chosen model and effort to the native tool when supported.
Record what was requested and what the runtime actually reports. If a model
or effort cannot be selected, say so instead of silently substituting. Changing
settings does not by itself change the line of work.

How the model and effort reach a native agent depends on the runtime's spawn
tool. Codex resolves each setting from an explicit spawn value, then the
`[agents]` defaults in `config.toml`, then the parent's own value, and a custom
agent file's `model` and `model_reasoning_effort` take precedence over all of
them ([subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents)).
So:

- A spawn tool that accepts a model and reasoning effort: pass both. For the
  desktop `collaboration.spawn_agent`, also set `fork_turns` to `"none"` or a
  positive integer string when overriding either setting. An omitted value or
  `"all"` forks the full history, inherits the parent model and effort, and
  does not accept overrides. Follow the exposed tool contract in other
  runtimes.
- A spawn tool that accepts only an agent type (often the CLI's): spawn the
  custom agent whose file pins that pair. `profiles/` in this skill holds
  examples: `sijav_sol` (gpt-6.1-sol, medium), `sijav_luna_search` (gpt-6-luna,
  low), `sijav_astra_research` (gpt-6-astra, high) and `sijav_astra_rnd`
  (gpt-6-astra, xhigh). Codex loads custom agents only from `.codex/agents/` in
  the project or `~/.codex/agents/`. Installing this plugin does not register
  them; the owner copies the files there, and only the owner changes global
  configuration.
- Neither available: do not spawn on whatever the agent would inherit. Report
  the missing prerequisite (the profile to install, or that this runtime cannot
  choose the model) and leave that delegation unfinished until the owner
  decides.

After a spawn, record the model and effort the runtime reports. If it reports a
different pair, or none, say so in the purpose record rather than assuming the
request took effect.

Give a concrete brief, applicable project rules and verified evidence. Ask for
official current sources, cited URLs and exact supporting passages when facts
depend on documentation; ask where documentation is silent. Never fix code
on a guess: research the premise, then hand implementation to Claude.

Save the full brief and result, purpose, agent ID, project/cwd, model, effort,
times and final status under `.codex/agent-sessions/`. Preserve full long or
background command stdout/stderr in associated logs from launch, including
command and exit status. A timeout, silence or empty process search is
inconclusive; investigate before retrying, and stop only work you started.

## Allowance and switches

Treat all GPT-6 models as sharing one allowance. Exhaustion means asking the
owner to switch the Codex account, then continuing the same purpose when the
owner confirms. Do not sign in, read/copy tokens, create `.stop` or schedule
resumption because of exhaustion. Never switch to `gpt-reserve` except the
single plain-search exception in `$sijav-codex-search`, and only when that exact
model is available.

Retain `SIJAV_CODEX=off` as a switch that starts no delegated Codex work; report
it. The orchestrator may use its own tools for noncoding work where authorized.
It never inherits permission to code or perform technical roast. A prescribed
project entry point remains confined to its own intended work.

## The Claude caller

Claude's coding and technical review go through the helper
`claude/claude_session.py` in the installed `skills/` folder, not through a
native agent. It keeps one external Claude conversation per purpose, in the
same spirit as native purposes, under `<project>/.codex/claude-sessions/`. Choosing
the purpose, writing the brief and deciding whether Claude may be called at
all (owner postponements included) remain the orchestrator's job.

A new purpose's first call names its scope and carries the project's law (or
says `--no-project-rules`); a later call of that existing purpose sends only
its brief. Each command is one line; `[...]` is optional:

```
python "<skills>/claude/claude_session.py" call --purpose impl-board --mode code --scope "board implementation" --rules-file "<project>/<law file>" --prompt-file "<brief.md>" [--allow-command "Bash(python -m unittest *)"] [--effort high]
python "<skills>/claude/claude_session.py" call --purpose impl-board --mode code --prompt-file "<follow-up.md>"
python "<skills>/claude/claude_session.py" status --purpose impl-board
python "<skills>/claude/claude_session.py" reset --purpose impl-board --authorized-by "<the owner's words>"
python "<skills>/claude/claude_session.py" recover --purpose impl-board --confirm-stopped
```

`list`, `status` and `context` never launch Claude. `--help` prints the full
contract.

- Model `claude-opus-5-5` always, at the requested effort (low, medium, high,
  xhigh or max; default high). No option selects another model and no
  fallback model is passed.
- The prompt is the file or stdin, sent byte for byte on stdin; the command is
  an argument list, never a shell string. The CLI runs with:
  - `--safe-mode`: inherited `CLAUDE.md`, hooks, skills, MCP and custom agents
    are off; sign-in and permissions work normally. Its init still lists
    installed plugins and skills, and their names are recorded.
  - `--restricted`: user, project and local settings files are ignored, so a
    reviewed project's `.claude/settings.json` cannot widen approvals or
    redirect the provider. File tools stay inside the project, and writes to
    settings, git and tool-configuration files need a person.
  - `--disable-slash-commands`: no skill can be triggered from the prompt.
  - `--strict-mcp-config` and stream-json output.
  - `--permission-mode manual`: Claude Code's normal mode, which its init
    reports as `default`.
  - `--permission-prompts none`, so anything that would ask is denied.

  Never a bypass or bare mode.
- Removed from Claude's environment, with their names recorded: inherited
  `ANTHROPIC_*` and `CLAUDE_CODE_USE_*` provider variables, `NODE_OPTIONS` and
  `NODE_TLS_REJECT_UNAUTHORIZED`. Claude therefore uses its own OAuth sign-in
  over verified TLS. `CLAUDE_CODE_OAUTH_TOKEN`, `CLAUDE_CONFIG_DIR`, proxy and
  CA-bundle variables stay so ordinary sign-in and networks work; their names
  are recorded too.
- Tool uses the CLI reports as denied are printed as a note and kept with the
  result, so a review that could not read what it needed is visible.
- Permissions use Claude Code's rule syntax. Reads inside the project need no
  approval; reads elsewhere and of common secret files (`.env`, keys, `~/.ssh`,
  `~/.claude` and similar) are denied. `code` mode offers Read, Glob, Grep,
  Write and Edit, pre-approves `Edit(./**)` (file changes inside the project)
  and each scoped `--allow-command` rule, and denies edits under `.git`,
  `.claude`, `.codex`, `.githooks` and `.husky`. A command rule must name its
  program first, as in `Bash(npm run test *)`; `Bash`, `Bash(*)` or a leading `*`
  is refused. `technical` mode offers Read, Glob, Grep and WebFetch,
  pre-approves WebFetch and denies every write and command tool. The rules bind
  Claude's own tools; a program that an approved command runs, such as a test
  suite, is not confined by them.
- The CLI's first event must be its init and must report the exact model, no
  tool beyond those offered, no MCP server, the normal permission mode, its own
  sign-in (`apiKeySource` none), the expected session id and the project as its
  folder. Anything else stops it there, before it acts. A reply from another
  model is kept in the record but not printed.
- A new purpose needs `--scope`; a purpose keeps its scope and mode. Until a call
  has delivered a turn, every call must carry the rules or say
  `--no-project-rules`.
- The first call asks for a fresh session id. It is confirmed only when the CLI
  reports it and a turn reaches the model; every later call resumes that exact
  id with `--resume`, never `--continue`. Otherwise the purpose is
  `unconfirmed` and nothing more is sent until the owner chooses one attempt:
  `--resume-unconfirmed new` (the same id again, for a CLI that never reported
  it) or `existing` (resume it, for one that did; `status` says which the
  evidence suggests), or authorizes `reset`. A failed attempt stays
  unconfirmed. A resume that reports another id is a `conflict`. A helper that
  died mid-call leaves `running`; `recover --confirm-stopped` reads what the run
  kept, refusing while that run's Claude process is still alive. `reset` needs
  the owner's words, archives the generation and keeps every earlier run.
- One call per purpose at a time (an OS lock; exit 4 busy, nothing sent). On
  Windows Claude and everything it starts run in a Job Object, ended with the
  call or when the helper dies; on POSIX they share a process group, which a
  descendant that calls `setsid` escapes.
- Each call keeps an immutable run folder: the brief, the exact prompt sent,
  the command, working folder and PIDs, raw stdout and stderr from launch, and
  the result (status, cause, requested and served model, usage, session ids,
  CLI version). Stdout gets only the final reply; thinking and signatures stay
  in the raw log.
- No retry and no other model on authentication, allowance or any other
  failure: exit 1 with the cause and the record path. `SIJAV_CLAUDE=off` exits 3
  without a call. Exit 5 means the purpose needs resolution as above. Exit 6
  means the reply was printed but its record or the purpose's state could not
  be saved (the message says which); for the state, run `recover
  --confirm-stopped`.

A native agent ID saved in `.codex/agent-sessions/` is never a Claude session id,
and a Claude purpose is never resumed as a native agent. Real technical-mode
calls through Claude Code 2.1.286 succeeded on October 1, 2026 in a disposable
project and kept one conversation. The later ones used `--permission-mode
manual`; init reported `default` and no tool use was denied. `--restricted`,
`--disable-slash-commands` and code mode have not run live yet. They, the
failure and recovery paths, and settings-file handling are tested offline with
a fake CLI pinned to that version's help and init. They are proved live only
when a real call exercises them.
