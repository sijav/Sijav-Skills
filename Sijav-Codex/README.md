# Sijav-Codex

Sijav's eight skills for Codex, ported from Sijav-Clauder. Codex orchestrates and
native Codex agents do research, search, project prose and logical reasoning.
Claude Opus 5.5 alone writes implementation and test code and performs technical
code review. Installing the package starts nothing: no loop, no dashboard and
no model call.

## What is in it

| Part | What it is |
| --- | --- |
| Skill prose (8 skills) | ported; rules, dev-round and loop are invoked only explicitly |
| `skills/claude/claude_session.py` | runs Claude Code for code, tests and technical review: one conversation per purpose, its model, tools, sign-in and folder checked before it acts, commands in the foreground with time limits, and follow-ups while a call runs; tested offline with a stand-in Claude |
| `skills/roast/jev.py` | asks jev through the TypeSafe SDK; tested offline, with an injected local transport and a fake SDK |
| `skills/research/verify.py` | checks a report's citations; tested offline with fixture pages, a loopback server and a simulated DNS answer |
| `skills/todo/todo.py`, `todo.mjs` | the source board tools, copied byte for byte |
| `skills/todo/dashboard/` | the read-only board dashboard; see its own README |
| Loop helpers and hooks | the loop adapter and its native Codex hooks; tested offline |
| `tools/sijav_codex_setup.py` | validation and native install commands; tested offline |

## Requirements

- **Codex CLI** with plugin marketplaces. The catalog path and source rules were
  checked against the 0.159.2 source. The package is a native Codex plugin: its
  manifest is `.codex-plugin/plugin.json`, and there is no root `plugin.json`.
  Codex 0.159.3 loads a root `plugin.json` as a portable Agent Plugin, prefers
  it, and drops its hooks. The former portable manifest is kept only as
  `archive/portable-plugin.example.json`.
- **Python 3.9 or newer** for every helper and `todo.py` (standard library only).
  Python 3.11+ also lets the setup check the agent profile examples.
- **Node 22.13 or newer** for `todo.mjs`. The dashboard needs **Node ^22.16.0
  or >=24.0.0** (its `package.json` engines; older versions are refused at
  start). Neither is needed when you use only `todo.py`.
- **For Claude work:** the Claude Code CLI on PATH, signed in. `SIJAV_CLAUDE_BIN`
  can name another executable. The helper's arguments and init checks are
  pinned to Claude Code 2.1.286; a different version is recorded in each run.
- **For Jev (optional):**
  - Install typesafe-sdk 0.7.x for the same Python. 0.7.1 is the version
    checked, and the SDK itself needs Python 3.10+.
  - Put a TypeSafe key in a file outside this package named by
    `SIJAV_JEV_KEY_FILE`, or in `TYPESAFE_API_KEY`.
  - The helpers pass `RetryPolicy(max_retries=0)` and a 60 s HTTP timeout.
  - Without the SDK or a key, or with another SDK series, Jev steps are
    recorded as unjudged with the exact cause. The helpers still run on
    Python 3.9 without the SDK.

## Install

All commands are single lines; quote every path. Replace angle-bracketed path
placeholders such as `<package>`, `<skills>` and `<project>` with the actual
absolute paths before running an example.

Validate first. This changes nothing and makes no model call:

```
python "<package>/tools/sijav_codex_setup.py"
python "<package>/tools/sijav_codex_setup.py" --json
```

It checks:

- the native manifest `.codex-plugin/plugin.json` (name, version, description,
  `skills: "./skills"`, `hooks: "./hooks/hooks.json"`), its hook file, and
  that no root `plugin.json` exists;
- the catalog `.agents/plugins/marketplace.json`: one plugin, source `./`
  (this folder and nothing above it);
- each skill's `SKILL.md` and `agents/openai.yaml`;
- every helper a skill or hook names;
- the example agent profiles;
- that no key, `.env`, `.claude` or `.codex` state is inside the package.

It also asks codex, node and claude for their versions and reports whether
typesafe-sdk is present. `--skip-tools` validates the files only.

Then install with Codex's own plugin CLI. The setup runs exactly these three
commands and prints their output:

```
python "<package>/tools/sijav_codex_setup.py" --install --log "<package>/setup-log.txt"
```

1. `codex plugin marketplace add "<package>" --json`
2. `codex plugin add sijav-codex@sijav-codex-local --json`
3. `codex plugin list --marketplace sijav-codex-local --available --json`

Installing enables the plugin. Its hooks are reviewed and trusted in Codex's own
`/hooks` screen, and only when the owner starts a loop (`$sijav-codex-loop`).
An earlier install, made with a root portable `plugin.json`, listed no hooks.
After updating the package, run the same `codex plugin add` again; Codex
replaces its cached copy. Then check that `/hooks` shows the plugin's Stop and
SessionStart handlers.
The setup never writes Codex configuration or trust, never passes a bypass
flag, never starts a loop or the dashboard, and never publishes or pushes.
Install only from this Sijav-Codex folder, never from the parent Sijav-Skills
repository.

## Skills

Invoke a skill by name in Codex, for example `$sijav-codex-roast`.

| Skill | Invocation |
| --- | --- |
| `$sijav-codex-rules` (`dev`, `design`) | explicit only |
| `$sijav-codex-dev-round` | explicit only |
| `$sijav-codex-loop` | explicit only; starts or resumes the owner's loop |
| `$sijav-codex-agents` | delegation of noncoding work, and the Claude caller |
| `$sijav-codex-search` | one plain web search |
| `$sijav-codex-research` | deep research with checked citations |
| `$sijav-codex-roast` | review by concrete failing scenarios |
| `$sijav-codex-todo` | the project's existing `.claude/todo.db` board and its dashboard |

### Native agent model and effort

When the runtime's spawn tool accepts a model and reasoning effort, the skills
pass them. For desktop `collaboration.spawn_agent`, a model or effort override
requires `fork_turns: "none"` or a positive integer string. An omitted value or
`"all"` forks the full history, inherits the parent model and effort, and does
not accept overrides. Follow the exposed tool contract in other runtimes.
When the tool accepts only an agent type, use a custom agent whose file pins
the pair. Examples are in `skills/agents/profiles/`:

| Profile | Model | Effort |
| --- | --- | --- |
| `sijav_sol` | gpt-6.1-sol | medium |
| `sijav_luna_search` | gpt-6-luna | low |
| `sijav_astra_research` | gpt-6-astra | high |
| `sijav_astra_rnd` | gpt-6-astra | xhigh |

Codex loads custom agents only from `.codex/agents/` in a project or from
`~/.codex/agents/`
([docs](https://learn.chatgpt.com/docs/agent-configuration/subagents)).
Installing the plugin does not register them; copying them there is the
owner's choice. Without either route the skills report the missing
prerequisite instead of letting an agent inherit another model.

## Helpers

Every helper prints its full contract with `--help`. Each finds the project
from `--project`, or from the working directory upward, stopping below the home
folder: the nearest `.claude/todo.db`, then the nearest `.git`, then the nearest
`.claude` folder. It never uses its own installed location and never creates a
board. Paths with spaces work.

### Claude

A new purpose's first call needs `--scope` and the project's law (or
`--no-project-rules`). Later calls of that same existing purpose send only the
brief.

First call of a new purpose:

```
python "<package>/skills/claude/claude_session.py" call --purpose impl-board --mode code --scope "board implementation" --rules-file "<project>/<law file>" --prompt-file "<brief.md>" --allow-command "Bash(python -m unittest *)"
```

Commands run in the foreground only. The helper sets
`CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`, and `--command-timeout SECONDS`
configures both `BASH_DEFAULT_TIMEOUT_MS` and `BASH_MAX_TIMEOUT_MS`; the default
is the smaller of 600 s and half of `--timeout`. Omitting the tool's timeout
uses that configured default; a smaller explicit request can stop earlier.
A command-capable call sends this allowance as separate guidance before the
unchanged prompt suffix, and records one `command_guidance_sha256`. It grants
no additional command permission and is not repeated in managed follow-ups.
The whole-call deadline starts at launch: it is not a new duration for each
command, and the guidance cannot know the remaining lifetime. The actual
offered tool schema/maximum and model compliance have not been proved by this
configuration. An offered duration refusal must be reported before work starts,
without a background or hidden runner. The CLI's temporary files are configured
to the purpose's project-local `cli-tmp`; reading overflow there remains unproved
live. A run that still starts a background task ends `incomplete` (exit 1).
So does a run that would be `ok` but whose output had not ended 10 s after the
CLI exited (kind `output not settled`; the cause is not determined); a failed
run gets that note appended. Any reply is kept in the run's `reply.md`, a
delivered turn keeps the session, and nothing is retried.

Later call of the existing purpose:

```
python "<package>/skills/claude/claude_session.py" call --purpose impl-board --mode code --prompt-file "<follow-up.md>"
```

A technical review in its own purpose, first call:

```
python "<package>/skills/claude/claude_session.py" call --purpose roast-technical --mode technical --scope "technical roast" --rules-file "<project>/<law file>" --prompt-file "<review-brief.md>"
```

A managed call that accepts follow-ups while it runs, and one follow-up. The
message is read at Claude's next tool or turn boundary; it does not interrupt.
After the call ends a follow-up exits 5, and its last stdout line says
`follow-up outcome: not_running (exit 5, sent no)`. Read that line under
`powershell -Command`, which reports 1 for exit 5. The next message is a
normal call:

```
python "<package>/skills/claude/claude_session.py" call --purpose impl-board --mode code --prompt-file "<brief.md>" --accept-follow-ups
python "<package>/skills/claude/claude_session.py" follow-up --purpose impl-board --prompt-file "<steer.md>"
```

Inspect without launching Claude:

```
python "<package>/skills/claude/claude_session.py" list
python "<package>/skills/claude/claude_session.py" status --purpose impl-board
python "<package>/skills/claude/claude_session.py" context --purpose impl-board --full
```

### Jev

```
python "<package>/skills/roast/jev.py" available
python "<package>/skills/roast/jev.py" open --brief "<brief.json>"
python "<package>/skills/roast/jev.py" ask --run "<run folder>" --framing "<framing.json>" --framed-by roast-task
python "<package>/skills/roast/jev.py" finalize --run "<run folder>" --interpretation "<reading.md>" --technical "<claude findings.md>"
python "<package>/skills/roast/jev.py" recover --run "<run folder>" --confirm-stopped
```

`recover` is only for a run whose helper stopped during a Jev call. It records
the outcome as unknown, or judges a reply already saved. It never asks Jev
again. A `brief.json` edited after `open` is refused.

### Research

```
python "<package>/skills/research/verify.py" init --question "How does X retry?" --depth standard
python "<package>/skills/research/verify.py" check --run "<run folder>" --report "<report.md>" --findings "<round-1-q1.md>"
python "<package>/skills/research/verify.py" check --run "<run folder>" --claims "<claims.json>" --snapshots "<snapshots.json>" --no-fetch
```

- **Snapshots:** the files must be inside the run folder. Each index entry
  gives `file`, `sha256`, `fetched_at` and `by`.
- **Rerunning:** a rerun with changed inputs is refused; give the new inputs a
  new `--name`.
- **Interrupted Jev batch:** one left without an outcome needs
  `--recover-interrupted`, which records the outcome as unknown.

### Records

Records go under the project:

- `.codex/claude-sessions/<purpose>/`: the purpose's state, and one immutable
  folder per call holding the prompt, the raw stdout and stderr, and the
  result.
- `.codex/roasts/<run>/`: the brief, framings, Jev requests and replies,
  `record.md` and `notes/`.
- `.codex/research/<run>/check/`: the claim list, pages, quotes, Jev and
  verdicts.

### Switches

- `SIJAV_CLAUDE=off`: no Claude call.
- `SIJAV_JEV=off`: no Jev call.
- `SIJAV_CODEX=off`: no delegated Codex work. This one is orchestrator policy.

These variables come from the process environment, as in Clauder. `off`, `0`,
`false` and `no` disable a helper, ignoring case and surrounding whitespace;
unset means on. Set them before starting Codex so its host process and helper
commands inherit them. After changing persistent user environment variables,
restart the Codex app. For a PowerShell process session:

```powershell
$env:SIJAV_CLAUDE = 'off'
$env:SIJAV_JEV = 'off'
# Restore the default on state:
Remove-Item Env:SIJAV_CLAUDE, Env:SIJAV_JEV -ErrorAction SilentlyContinue
```

The Jev token resolves exactly as in Clauder: the UTF-8 file named by
`SIJAV_JEV_KEY_FILE` wins; otherwise use `TYPESAFE_API_KEY`. An explicitly named
missing or empty file makes Jev unavailable and never falls back to the
environment key. Keep that file outside the plugin and use its absolute path:

```powershell
$env:SIJAV_JEV_KEY_FILE = '<absolute external key file>'
```

Claude uses its own CLI login; the skill holds no copied login token. When
Claude is off, coding and technical review stay pending. When Jev is off, its
judgment is recorded as unjudged. Returned input/output token usage is recorded;
Jev's token limits and one shortening after a too-long refusal follow Clauder.

An owner's instruction to postpone Claude binds the orchestrator, because the
helper cannot see it.

## The to-do dashboard

`skills/todo/dashboard/` is a read-only browser view of a project's existing
`.claude/todo.db`: the board in the tool's own next order, every field, field
diffs, both themes and a Relax screen. It never starts by itself and never
creates or falls back to another board. Its README has the full contract and
limits.

Install its locked dependency once, inside the dashboard folder only:

```
npm ci --omit=dev --prefix "<package>/skills/todo/dashboard"
```

Or let the setup run that same command. `--dashboard-dir` points it at an
installed copy instead:

```
python "<package>/tools/sijav_codex_setup.py" --dashboard-deps
```

Start it from a project or name one:

```
node "<package>/skills/todo/dashboard/server.mjs" --project "<project root>"
```

By default the OS picks a free loopback port, which changes on every start; add
`--port 8765` for a stable address. The change history goes to a per-user cache
folder, never into the project or the plugin.

## Tests

All tests are offline and use temporary folders only. Run them from the package
folder:

```
python -B -m unittest -v tests.test_claude_session tests.test_jev tests.test_jev_sdk tests.test_verify tests.test_setup tests.test_permission_smoke
python -B -m unittest -v tests.test_loop tests.test_native_proof
python "skills/todo/test-parity.py"
node "skills/todo/test-subtasks.mjs"
```

`tests.test_jev_sdk` runs only on a Python that has typesafe-sdk 0.7.x, and is
skipped elsewhere. It drives the real SDK through an in-process
`httpx2.MockTransport`; nothing is sent over the network. The dashboard's own tests (`npm test` in its folder) are
described in its README.

`tests/permission_smoke.py` prepares a disposable fixture for one real
code-mode Claude call and then checks that call's records. It never calls a
model itself; the owner or root runs the one command it prints. The fixture
plants a hostile project settings file, fake secrets and an outside folder.
`--check` expects:

- the write inside the project to succeed;
- the write outside it and the secret reads to be denied and listed in
  `permission_denials`;
- no Bash use, and the settings file's hook and provider redirect to have no
  effect.

```
python -B tests/permission_smoke.py --prepare "<new empty folder under %TEMP%>"
python -B tests/permission_smoke.py --check "<the same folder>"
```
