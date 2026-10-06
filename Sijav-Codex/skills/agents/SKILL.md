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
python "<skills>/claude/claude_session.py" call --purpose impl-board --mode code --prompt-file "<brief.md>" --accept-follow-ups
python "<skills>/claude/claude_session.py" follow-up --purpose impl-board --prompt-file "<steer.md>"
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
- Commands are foreground-only and finite:
  - A `-p` call cannot await a background shell. `TaskOutput` is deprecated,
    and Claude Code 2.1.288 does not offer it. Per the headless docs, a background shell ends about 5 s after
    the final result once stdin closes.
  - So every call sets `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`, which turns
    off `run_in_background` and automatic backgrounding, and denies
    `TaskOutput` and `TaskStop` in both modes. An init that still lists either
    is refused as offering an ungranted tool.
  - `--command-timeout SECONDS` configures the foreground default and ceiling
    as `BASH_DEFAULT_TIMEOUT_MS` and `BASH_MAX_TIMEOUT_MS`, only in code mode
    with `--allow-command`. An omitted tool timeout uses the configured default;
    a smaller explicit request stops earlier. A separate guidance section before
    the exact original prompt suffix records one `command_guidance_sha256` and
    grants no extra authority. It is absent with no command and is not repeated
    in managed follow-ups. The offered tool's actual schema/maximum and model
    compliance remain unproved; report an offered refusal before starting, with
    no background or hidden runner. `--timeout` starts at whole-call launch,
    not per command; the guidance does not know remaining lifetime.
    - It must be at least 1 and below `--timeout`.
    - The default is the smaller of 600 s and half of `--timeout`.
    - It is refused (exit 2, nothing created or launched) for a call with no
      command tool, and when `--timeout` is too short to bound a command.
  - The CLI's internal temporary files are configured to go to
    `<project>/.codex/claude-sessions/<purpose>/cli-tmp`, through the
    documented `CLAUDE_CODE_TMPDIR`. Output too long for a command's result
    could then be opened with Read inside the project; that has not been
    tested live. No read outside the project is granted, the secret-file
    denies still apply, and the folder is not cleaned between runs.
  - These settings replace any inherited value, and are recorded by name and
    value in `command.json` (`set_environment`, plus
    `replaced_inherited_environment`). None is a secret.
  - A brief for a code call should ask for finite foreground commands whose
    output is short. Timers, background shells, temp-folder transcript
    readers, new conversations and process-age checks are not substitutes.
  - **Background work is a breach.** It is recognized only from structured
    events that earlier 2.1.288 runs emitted with background tasks enabled:
    `system/task_started` with `is_backgrounded` true, or a tool result whose
    `tool_use_result` names a `backgroundTaskId`. With background disabled,
    the live runs emitted no task events at all. Such a run is `incomplete`
    (exit 1, kind `background breach`) even if the CLI reported success and
    exited 0. A managed call's inbox closes at once, and no answer after the
    breach counts as answered. A delivered turn still keeps the session
    `ready`. Recognizing these signals is tested offline only.
  - **Output that has not ended is never a silent success.** If Claude's
    output has not ended 10 s after Claude exited, a run that would be `ok`
    is `incomplete` (exit 1, kind `output not settled`); any other failure
    gets the same note appended ("also: ..."). Why the output had not ended
    is not determined: a writer outside the owned process tree, or the
    reader still processing. The reply is kept in the run's `reply.md` and
    not printed, a delivered turn still keeps the session `ready`, and
    nothing is retried. `result.json` records `output_settlement`.
  - A `run_in_background` request alone is only counted
    (`commands.background_requests`). With background disabled, 2.1.288
    rejects the option ("An unexpected parameter run_in_background was
    provided") and the command does not run. Any task event is recorded in
    `result.json` `commands.tasks`.
  - A managed follow-up is read at the next tool boundary. During a command it
    waits until that command actually ends, plus one model step, subject to
    the whole-call deadline. The configured allowance does not prove an offered
    tool's maximum. It never interrupts a command. In 2.1.288 the follow-up's
    `command_lifecycle` `queued` appeared at once, but its replay
    acknowledgement came only at the command's boundary. A `follow-up` whose
    `--wait` is shorter than the command's remaining time therefore prints
    "not acknowledged within N s" and still exits 0 as submitted.
  - **Not proved live:**
    - a native foreground command longer than the prior 120 s observation,
      a 5,400 s configured allowance's acceptance or enforcement, the actual
      offered tool schema/maximum, or model compliance with the new guidance;
    - the `backgroundTaskId` signal of a command moved to the background
      automatically;
    - CLI temp files actually written to `cli-tmp`, and Read of overflow;
    - the PowerShell tool under the same bounds;
    - which process a timeout kills (not inferred from the exit code or from
      Claude's reading of it).
- The CLI's init must come before anything it does, and every init must report
  the exact model, no tool beyond those offered, no MCP server, the normal
  permission mode, its own sign-in (`apiKeySource` none), the expected session
  id and the project as its folder. Before the init the helper accepts only:
  - on a resume, up to 16 `task_notification` events of that same session,
    which the CLI replays for background tasks an interrupted turn left. They
    are recorded in the run's `pre_init_events`, never sent on or acted on;
  - in a managed call, the CLI's replay of a message the helper wrote;
  - in a managed call, `command_lifecycle` events of exactly the fields
    `type`, `command_uuid`, `state`, `uuid` and `session_id`. They must name
    the asked session and the call's first message (the only one written
    before the init), as `queued` then `started`, each once. This event type
    is not documented. It is accepted only in the shape a real managed resume
    showed on October 3, where both events named that run's first-message
    uuid. Any other state, key, session or command, or such an event in a
    text-input call, is refused. `started` shows only that the CLI began
    processing. If such a run then fails, its record says whether the prompt
    reached the transcript is unknown, so a later call may repeat it. No
    retry, no reset. After the init these events are only recorded, per
    command.

  Anything else stops it there, before it acts: an assistant, user, result,
  hook or plugin event, a notification of another or no session or on a first
  attempt, more than 16 lines that are not JSON, a line over 64 KiB, more than
  19 events in all. Before
  this repair any notification ahead of the init was refused, so a resumed
  conversation that had been interrupted mid-task refused every call; that loop
  is gone without loosening the init checks. A run whose final result is a
  success that delivered no turn (`num_turns` 0, no assistant message) is a
  failure. A `ready` session stays `ready`, and a new purpose with no
  delivered turn stays `unconfirmed`.

  Exactly one shape is a prelude, and a run has at most one. It is the first
  result of a resume that sent a `task_notification` before its init, before
  any work: a `success` with `is_error` false and integer `num_turns` 0. A real
  interrupted resume printed exactly that, then did its work and answered. The
  prelude is kept as evidence (`prelude_results`), and it is never a turn, an
  answer or a reason to close a managed call's inbox. A second zero-turn
  success, or one in a run with no notification, is terminal.

  Every other result either answers (positive `num_turns`, or main-thread work
  since the previous result) or is terminal. A terminal result is an error, or
  a zero-turn result after an answer. It closes a managed call's input at once,
  because a streaming CLI stays alive after an error. If it is an error, it
  fails the call with its own cause, even after an earlier successful turn.
  Messages acknowledged before it are recorded as "ended by" that result, not
  as answered. These error paths are tested offline only.

  A reply from another model is kept in the record but not printed.
- `CLAUDE_CODE_RESUME_INTERRUPTED_TURN` is removed from Claude's environment
  too (its name recorded), so a resume answers the new prompt rather than
  continuing a stale turn.
- Follow-ups are opt-in: `call --accept-follow-ups` starts a managed call
  whose CLI reads stream-json user messages (`--input-format stream-json
  --replay-user-messages`) with every other flag unchanged; the default call
  keeps its one prompt on text stdin. While it runs, `follow-up` adds one
  message. It never takes the purpose lock and never launches Claude: it needs
  that call's helper and CLI alive and its init validated, else it exits 5 and
  writes nothing. Per Claude Code's docs the message is read after the current
  tool calls finish, within the turn, or starts the next turn: **it queues and
  steers; it does not interrupt.** The record keeps three levels apart:
  - submitted: written to stdin;
  - acknowledged: the CLI's replay was seen;
  - answered: an answering result came after the acknowledgment, which is
    inferred.

  A follow-up is taken only once its receipt is saved; a file that is not
  valid UTF-8 text, or whose receipt cannot be saved, is rejected with its
  reason and never sent. When every message is answered the inbox closes, and a later follow-up is
  refused (exit 5) and needs a normal call. A file that arrives as the call
  ends is either rejected with its reason, or sent and then recorded as
  answered, ended by a terminal result, or unknown; never dropped silently.
  A stopped call records unanswered messages as `unknown: call
  stopped`. stdout prints each turn's reply under a header naming the
  follow-up it came after.

  Every `follow-up` ends with an outcome record on stdout:
  `follow-up outcome: <outcome> (exit <code>, sent <yes|no|unknown>)`, or one
  JSON object with `--json`. Outcomes by exit code:
  - 0: `submitted`;
  - 1: `rejected`, `withdrawn`, `write_unconfirmed` or `failed` (a protocol
    or I/O failure);
  - 2: `usage`;
  - 3: `off`;
  - 5: `not_running` or `closed` (the call has finished), `not_initialized`
    (still starting up), `not_managed`, `helper_dead`, `cli_dead`,
    `foreign_run` or `needs_resolution`;
  - `interrupted` (Ctrl-C): the record shows exit `none`, and the process
    then ends with Python's own interruption exit.

  `sent` means:
  - `no` until the message is in the inbox;
  - then `unknown` until the helper's receipts settle it: `yes` once it is
    submitted, `no` once it is withdrawn or a rejection receipt says `no`.

  A rejection receipt states `sent` explicitly; one without that field counts
  as `unknown`. If the wait fails or is interrupted after the message is in
  the inbox, the same locked withdrawal runs as at the end of the wait. If
  that cleanup cannot get the lock, `sent` stays `unknown` and the original
  cause is reported. Argument errors that argparse itself reports (exit 2)
  come before any outcome record.

  Run the helper with Python directly so its own exit code is kept.
  `powershell -Command` and `pwsh -Command` report 1 for any native exit
  other than 0 or 1; end such a command with `; exit $LASTEXITCODE`.
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
  descendant that calls `setsid` escapes. Measured limit: with Claude run by a
  venv made from a Microsoft Store Python, the Job's lifetime count was 1 (the
  child interpreter never joined it), so what that child starts is not ended
  with the call; if such a process keeps the output open, the run reports its
  output as not settled. On POSIX the group is signalled while Claude is still
  unreaped (its exit is only observed, with `os.waitid` and
  `WNOWAIT`), so its id cannot belong to another group yet; nothing is signalled
  after Claude is reaped. A Python without `WNOWAIT` reaps Claude as soon as it
  exits, so what Claude leaves running then is not stopped, and the run's
  `process_tree` says so. Collecting Claude's exit waits at most 10 s; after
  that, or once Claude was reaped outside the helper (its exit is then
  unknown), nothing is signalled again. `process_tree_stops` records what each
  stop observed; it is not an interpretation.
- Each call keeps an immutable run folder: the brief, the exact prompt sent,
  the command, working folder and PIDs, raw stdout and stderr from launch, and
  the result (status, cause, requested and served model, usage, session ids,
  CLI version, pre-init events). A managed call adds `stdin.jsonl` (every line
  written to Claude), its `inbox/` and `acks/` files and one reply per turn.
  Stdout gets only the final reply (each turn's, in a managed call); thinking
  and signatures stay in the raw log.
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
manual`; init reported `default` and no tool use was denied. The latest also ran with `--restricted` and `--disable-slash-commands`. A
separate live code-mode check in a disposable project passed: a write inside
the project worked, a write outside it and reads of planted secret files were
denied, no shell was offered, and the project's own settings were ignored. The
failure and recovery paths are tested offline with a fake CLI pinned to that
version's help and init; they are proved live only when a real call exercises
them.

The fake's stream-json input follows the documented interface and the pinned
help's flags.
