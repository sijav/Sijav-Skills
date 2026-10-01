# Sijav-Codex

Sijav's eight skills for Codex, ported from Sijav-Clauder. Codex orchestrates and
native Codex agents do research, search, project prose and logical reasoning.
Claude Opus 5.5 alone writes implementation and test code and performs technical
code review. Installing the package starts nothing: no loop, no dashboard and
no model call.

## Status

| Part | State |
| --- | --- |
| Skill prose (8 skills) | ported; rules, dev-round and loop are invoked only explicitly |
| `skills/claude/claude_session.py` | Five live technical calls on `claude-opus-5-5` kept one conversation in TEMP; the latest (`051503Z`) exercised Read with safe/restricted/disable-slash settings and normal permissions. A separate code-permission fixture at 06:03 UTC passed 18 assertions: inside Write succeeded; outside Write and planted `.env`/`dummy.key` reads were denied; no shell was offered; project provider/hook settings were ignored. Public output: `validation/claude-code-permission-smoke.txt`. A full 126-unit run and the final 46 focused checks passed; the current 128 helper cases are covered across runs, not one full 128-case execution |
| `skills/roast/jev.py` | Tested offline against the real typesafe-sdk 0.7.1 through an injected local transport, and with a fake SDK for every failure path. No live Jev call has been made, and no key was used |
| `skills/research/verify.py` | Tested offline with fixture pages, a loopback server and a simulated DNS answer. No live report checked yet |
| `skills/todo/todo.py`, `todo.mjs` | the source board tools, copied byte for byte |
| `skills/todo/dashboard/` | the read-only board dashboard; see its own README and test output |
| Loop helpers and hooks | 111 offline checks passed. Project hooks and installed native plugin A/B/C proofs passed on Codex 0.159.3 in TEMP. Installed run `20261001T060506Z-run` continued three times to the promise, counted identical answers to cap two, and delivered the full law before the first response after manual compaction. All cases used no tools; final state was complete. Both hooks were approved and normally trusted/enabled. Public output: `validation/installed-native-loop-proof.txt`. Automatic mid-turn compaction remains empirically untested |
| `tools/sijav_codex_setup.py` | validation and native install commands; offline tests pass |

Results with exact outputs are in `validation/`, including
`final-focused-unittest.txt` and `final-fault-plantability.txt`. All 58 final
fault cases plant; coverage combines the earlier 57 caught across documented
runs and three new or repointed mutants caught after the atomic publication
fix. No single full 58-case execution is claimed. The final source package
was copied to this repository's `Sijav-Codex` folder: 111 files, zero hash
mismatches. Its native setup installed/refreshed the same plugin ID from that
source; version `0.1.0` is installed and enabled. Locked dashboard dependency
installation passed with zero audit findings. Final discovery, retained hook
trust, cache dependency and hash checks are reported separately; no new model
proof is implied by the refresh.

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

Later call of the existing purpose:

```
python "<package>/skills/claude/claude_session.py" call --purpose impl-board --mode code --prompt-file "<follow-up.md>"
```

A technical review in its own purpose, first call:

```
python "<package>/skills/claude/claude_session.py" call --purpose roast-technical --mode technical --scope "technical roast" --rules-file "<project>/<law file>" --prompt-file "<review-brief.md>"
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
python -B -m unittest -v tests.test_claude_session tests.test_jev tests.test_jev_sdk tests.test_verify tests.test_setup tests.test_permission_smoke tests.test_run_all
python -B validation/planted_faults.py --check-only
python -B validation/planted_faults.py
python -B -m unittest -v tests.test_loop tests.test_native_proof
python "skills/todo/test-parity.py"
node "skills/todo/test-subtasks.mjs"
python -B validation/run_all.py
```

`validation/run_all.py` runs all of these and writes each one's complete output
to `validation/`.

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
