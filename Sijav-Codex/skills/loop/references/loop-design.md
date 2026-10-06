# Codex loop mechanism and implementation handoff

Status on October 1, 2026: native capability verified in documentation and local feature discovery. The installed Codex CLI reported 0.159.2 and its hooks feature was stable and enabled. Claude then implemented the adapter described under [Implemented adapter](#implemented-adapter) and the [live proof harness](#live-proof-harness). An independent technical review then found defects, and the fixes are in place.

A first live run on October 1, in the disposable fixture `%TEMP%\sijav-loop-proof-20261001-empty`, failed safely. `--check` had passed with both handlers trusted, enabled and reported with the `commandWindows` variant. During `--run`, Codex nonetheless ran every fixture hook through the user's PowerShell. That shell rejected the quoted-interpreter command line with a parse error and exit 1, so Python never started: no adapter event, no continuation, no compaction context. The command form was then changed to one valid in PowerShell, cmd and sh. That fixture is kept as evidence, paused by its `.stop`.

A second live run on October 1, in `%TEMP%\sijav-loop-proof-20261001-r2`, used the project-hooks form. By then the runtime had updated itself: app-server userAgent `Codex Desktop/0.159.3`. Results:

- **Fixture A** (three Stop continuations, then the exact promise) passed every check.
- **Fixture B** (identical replies, exhausted at cap 2) passed every check.
- **Fixture C**, after a manual `thread/compact/start`:
  - SessionStart(compact) ran at the start of the next turn and completed. Its context entry was the adapter's exact text, including the whole law, and was reported before the user message and the reply.
  - The model's first final answer was nonetheless the fixture's fallback `NO-LAW-IN-CONTEXT`. The marker came only after the Stop refeed, so the two delivery checks failed.
  - The context was recorded: the run's own synthetic rollout holds a developer hook message with the marker at 04:12:33.943Z, and the 0.159.3 source agrees (see below). The fixture's question never actually asked for the marker, and was corrected.

The strengthened rerun, `proof-runs/20261001T043110Z-run` in the same fixture, passed every A, B and C check. It ran on Codex 0.159.3, requesting `gpt-6.1-sol` with effort `medium`. C's first final answer after the manual compaction returned the injected marker and promise with no continuation, and the fixture state ended `complete`.

The project-hooks form is therefore proven live, for Stop continuation, cap exhaustion and the manual-compaction refeed. Automatic mid-turn compaction remains empirically untested. That run's summary printed thread effort `low`. This was the thread baseline from `thread/start`, before each turn's `medium` override. The harness has since been corrected; see [Live proof harness](#live-proof-harness). With the correction, a later project-form run (`20261001T045941Z-run`) passed A, B and C again, with the requested and thread-default `gpt-6.1-sol`/`medium` verified.

The first installed-plugin attempt used the earlier root portable manifest. Codex loaded it with zero hook handlers. The package was then changed to a native plugin (see [Plugin manifest format](#plugin-manifest-format)), reinstalled and proved. Both installed handlers were trusted and enabled through Codex's normal TUI after the owner's approval. In `%TEMP%/sijav-loop-native-plugin-proof-20261001`, check `20261001T060449Z-check` and run `20261001T060506Z-run` passed on Codex 0.159.3. Every A/B/C adapter, native and model check passed, including Codex's `${PLUGIN_ROOT}` expansion, three continuations to the promise, identical replies exhausting cap two, and the whole-law context before C's first answer after manual compaction. No tools ran, and the final fixture state was `complete`. No actual project loop has started.

Read-only study of the source established the following. The first five points come from 0.159.2, recorded in the runtime evidence note; the compaction path comes from the 0.159.3 tag.

- Automatic Stop continuations keep the protocol `turn_id`. `stop_hook_active` is false on a turn's first Stop and true on later ones.
- Every command Codex runs gets `CODEX_SESSION_ID` (the root session) and `CODEX_THREAD_ID` (the running thread). Hook payloads carry the root session id. The two variables are equal only in the root, and the desktop root session was confirmed to have both, equal.
- Plugin discovery picks `commandWindows` on Windows and substitutes `${PLUGIN_ROOT}` itself, without relying on the shell (`hooks/src/engine/discovery.rs`).
- On this Windows deployment, hooks run through the user's PowerShell as `<powershell> [-NoProfile] -Command <command>`. The live run showed this: every hook failed with PowerShell's parse error, and the same command succeeds under cmd. `core/src/shell.rs` (`derive_exec_args`) gives that argument form. `hooks/src/engine/command_runner.rs` (`build_command`) runs a configured hook shell as `<program> <args> <command>`. Only when no shell program is configured does it fall back to `%COMSPEC% /C "<command>"` on Windows or `$SHELL -lc <command>` elsewhere; cmd is not the shell observed here. The [0.159.3 session setup](https://github.com/openai/codex/blob/rust-v0.159.3/codex-rs/core/src/session/mod.rs#L4968) configures the hook shell using the selected local shell's `derive_exec_args("", false)`. A spawn failure or timeout yields no exit code.
- The native trust flow is `/hooks` in the interactive CLI.
- SessionStart context path in 0.159.3 (`codex-rs/core/src`):
  - **Manual compaction** (`thread/compact/start`): `replace_compacted_history` queues `SessionStartSource::Compact` (`session/mod.rs`, near line 4207).
  - **Next turn:** `run_turn` first records context updates (`session/turn.rs`, near 283). It then runs the pending SessionStart hooks (near 320) and only then records the user input (near 366).
  - **Automatic mid-turn compaction:** `run_turn` runs the pending SessionStart hooks right after the compaction and before resampling (near 634).
  - **Recording:** in `hook_runtime.rs` (near 128-178 and 849-873), the hook's `additionalContext` passes through `record_additional_contexts` and `HookAdditionalContext`. It becomes a developer-role message in history: content kind `hooks.additional_context`, no wrapper markers, body as given.
  - **Prompt:** the sampling input is `clone_history().for_prompt()` (`turn.rs`, near 515). `normalize_history` removes only orphaned call outputs and unsupported media, never developer items.
  - **So:** in both paths the law reaches the model's input as a developer message before the next model request. App-server exposes no thread item for developer messages, so the harness can show the hook's completed context entry and its order, but not the model input itself.

## Native interfaces

[OpenAI's hook documentation](https://learn.chatgpt.com/docs/hooks) defines a synchronous Stop decision that blocks stopping and supplies the next prompt as the reason. Codex's common hook input supplies the current session ID; Stop also supplies the last assistant message. Completion detection should use that message rather than the Claude transcript reader.

The same documentation describes SessionStart with source compact. Its context reaches the immediate continuation after automatic mid-turn compaction. This is the refeed needed by the Sijav law. The two hooks must share arming and ownership checks.

Hook definitions can live in a plugin or the project's Codex configuration. Non-managed definitions require native review and trust; changed definitions need fresh review. Background hooks cannot drive continuation. Do not add a second supervisor or a duplicate hook source unless a measured runtime limitation requires it.

## Ownership and state

The existing Claude project uses its own law frontmatter as state. Its session helper relies on a Claude-specific session environment variable, and its stop helper parses Claude transcript records. Those cannot be assumed to be Codex-compatible.

The Codex port should use dedicated project-local Codex state with an explicit law path, owning session, iteration count, configured cap and exact completion promise. It must read the actual law without replacing an existing Claude loop's ownership or iteration counter. An agent working on a delegated task is not the orchestrator's owning session. The law's named operator pause sentinel remains the operator control; do not move it silently.

Durable state must be written atomically and concurrent session claims must fail with an exact cause rather than race. An error saving the increment must not claim a successful increment or leave a continuation unbounded. Invalid state is an explicit error, not an unlimited loop with invented defaults.

## Implementation sequence for Claude

The adapter and companion helpers were implemented using this sequence. It records the handoff order; current proof results and remaining runtime limits are given below.

Once the owner explicitly authorizes Claude again, delegate implementation to the exact model `claude-opus-5-5`. Use the existing signed-in Claude Code installation; do not inspect or transfer login tokens, bypass approvals or choose a substitute model.

Implement the loop state/start/status helpers and synchronous Stop/compact hooks first. Keep changes inside the new Sijav-Codex tree and disposable fixtures. Do not engage the owner's real project board or alter its current law/session. Produce a complete command/output log for every test run.

Verify independent observable cases: another session stops normally; an unarmed session stops normally; the owner session continues on multiple successive Stop events; a real pause stops it; the exact completion promise stops it; a false or earlier promise does not; the configured cap stops without declaring completion; malformed state and failed state writes report their actual cause; compaction reloads the entire selected law immediately only for the owner session; paths work with spaces and from a subdirectory.

Then run a bounded live Codex fixture that reaches multiple natural stop boundaries and finally completes. Separately observe a compaction refeed in the target runtime. Simulated hook payload tests do not establish runtime continuation or compaction delivery. Review and trust only the disposable fixture's concrete hooks through the supported native flow.

If that proof passes, implement the external Claude purpose-session helper and the Jev framing/record helpers required by the other skills. Native Codex agents handle non-code jobs; an unavailable native agent ID must not be treated as a resumable process merely because it appears in a record. A coding provider's failure or owner-disabled state must never fall back to Codex implementation or technical roast.

## Plugin manifest format

The package is a native Codex plugin, with `.codex-plugin/plugin.json` and no root `plugin.json`.

An earlier version shipped a root portable `plugin.json` that named the hooks under `extensions.com.openai`. Installed in Codex 0.159.3 as `sijav-codex@sijav-codex-local`, it loaded with zero hook handlers. The root's source investigation of the 0.159.3 tag explains why:

- a root `plugin.json` is read as a portable Agent Plugin and takes precedence over `.codex-plugin/plugin.json`, even with an overlay (`utils/plugins/src/plugin_namespace.rs` 38-57, `core-plugins/src/manifest.rs` 153-179);
- that format's hooks are discarded (`core-plugins/src/loader.rs` 894-904);
- native manifests load their hooks (`loader.rs` 1113-1167);
- the hooks feature is stable and on by default, so no configuration setting restores them.

The native manifest carries only the five fields above. Display fields such as an `interface` object are left out until their native field names are confirmed from source. The former portable manifest is kept unchanged as `archive/portable-plugin.example.json`, where no loader looks for it.

The installed hook definitions and both helper scripts matched the staged package during the native-plugin proof. Codex loaded both handlers from the native manifest, expanded `${PLUGIN_ROOT}` itself and retained their normal trust through the passing A/B/C run.

## Implemented adapter

Files: `.codex-plugin/plugin.json` (the native manifest: `name`, `version`, `description`, `skills: "./skills"`, `hooks: "./hooks/hooks.json"`), `hooks/hooks.json`, `skills/loop/scripts/sijav_loop.py` (state, CLI, hook logic), `skills/loop/scripts/loop_hook.py` (hook entry), `tests/test_loop.py`, `tests/native_proof.py` (live harness) and `tests/test_native_proof.py` (its offline tests). Only the Python standard library is used.

Hook definitions: `Stop` with no matcher and `SessionStart` with matcher `compact` each run `loop_hook.py stop` or `loop_hook.py session-start` under `${PLUGIN_ROOT}/skills/loop/scripts/`. The variants differ only in syntax:

- `command` is `python3 -c "<bootstrap>" "${PLUGIN_ROOT}/skills/loop/scripts/loop_hook.py" <event>`.
- `commandWindows` is the same with `python` and backslashes.

Both have a 30-second timeout and no `async`. The compact handler sets `additionalContextLimit` to `0` so the whole law reaches the model instead of a preview. `loop_hook.py` always exits 0 and prints at most one JSON object. Exit 2 is never used, because Codex would read stderr as a block reason.

Launcher: the command never names `loop_hook.py` as Python's script, because CPython exits 2 for a script it cannot open and Codex would read that as a block. The bootstrap, run with `-c`, takes the script path as an argument and does three things:

- turns off bytecode writing and drops the working directory from `sys.path`;
- runs the script with `runpy` when it exists;
- otherwise prints `{"systemMessage": "Sijav loop <event> hook did not start: <path> is missing, ..."}` and exits 0.

The remaining launcher failures exit non-zero but never 2, and Codex reports them as failed hook runs, which continue nothing:

- a missing interpreter: 1 under PowerShell, 9009 under cmd, 127 under sh;
- an update missing `sijav_loop.py`: 1.

The command line is valid in every hook shell Codex may use: PowerShell `-Command` (5.1 and 7, with or without a profile), cmd `/C "<command>"` and sh `-lc`. Three rules keep it that way:

- The interpreter token is never quoted, because a quoted first token followed by another string is a PowerShell parse error. An interpreter path must therefore be usable unquoted.
- The bootstrap contains no double quotes, `$`, `%`, backticks, backslashes or shell operators, and there is no `||`, which Windows PowerShell 5.1 cannot parse.
- A path substituted for `${PLUGIN_ROOT}` must not contain `"`, `$` or a backtick.

A PowerShell profile that prints to stdout would corrupt hook output. Codex would then reject it as invalid JSON, which also continues nothing.

State: `<project>/.codex/sijav-loop/state.json` holds schema `sijav-codex-loop/1`, the absolute project root and law path, the owning session, status (`active`, `paused`, `stopped`, `complete` or `exhausted`) with its reason, iteration, a positive cap, the exact promise, the pause sentinels and a diagnostic hash of the last handled Stop callback. A state whose recorded root is not the directory it was found under was copied or moved and is refused, as is one whose sentinels leave the project or name the law or the state. The law is only read. Hooks find the nearest state at or above the payload's `cwd`, never the process directory.

Identity:

- `start`, `resume` and `reset` refuse when exactly one of `CODEX_THREAD_ID` and `CODEX_SESSION_ID` is set, or when they differ (a descendant thread).
- When both are set and equal, an explicit `--session` must match them, and `--session-from-env` takes `CODEX_SESSION_ID`. Outside Codex neither is set and `--session` is required.
- The hooks drive nothing when their own environment shows differing ids. That guard is defensive: whether descendant threads fire `SessionStart(compact)` at all is unverified.

Misarming: `start` refuses, unless `--confirm-root` is given:

- a root strictly below the law's project, meaning the folder holding the law or its `.claude`/`.codex` parent;
- a root inside an ancestor git repository;
- inside Codex, a working directory outside the root.

There is no ceiling on the cap; it is the owner's policy.

Writes: one lock file per project (`msvcrt`/`fcntl`, released by the OS if the holder dies), waited for at most 10 seconds, then reported with its cause. State is written to a temporary file, flushed and renamed into place. If the increment cannot be saved, no continuation is sent and the error is reported.

Stop decides, in order: no state, not armed; a descendant environment; another owner; not `active`; the exact promise, which sets `complete`; a present sentinel; the cap reached, which sets `exhausted`; an unreadable law; otherwise iteration plus one, saved, and `{"decision":"block","reason":...}` with the law body after its front matter, verbatim. Errors print `{"systemMessage": ...}` and drive nothing. `stop_hook_active` is recorded, not used to suppress.

Counting: the native payload has no per-delivery ID. A continuation keeps the session, `turn_id` and `stop_hook_active`, and a model can repeat its final message word for word while making progress the hook never sees. So no fingerprint can tell a duplicate delivery from a new callback, and none is used to suppress one. Every handled Stop callback of the owning session counts as one iteration under the lock. A real duplicate delivery therefore costs one iteration of the budget. It never halts the loop early, declares completion or escapes the cap. Each event record carries `same_as_previous_stop` for diagnosis only. Its hash covers `stop_hook_active`, so a turn's first and second callbacks never match even with identical text. A run restarted while its turn is still going is flagged `restarted_within_turn`. If saving `complete` fails, the stop is still allowed. The message then says plainly that the state remains active and the next stop will continue toward the cap.

Completion: the final message's last non-blank line, with trailing whitespace removed, must equal `<promise>VALUE</promise>`; it may not be indented or sit inside an open fenced block. Nothing else, including the transcript, is read.

Sentinels are `<project>/.stop` plus every `--sentinel` path. Each must lie inside the project root, and none may be the root, the law or anything in the state folder. All of this is checked before the lock is taken or anything is written.

- Clearing: `start` and `resume` leave present sentinels alone and refuse, unless `--clear-sentinels` is given. When the law's front matter names a Claude session that shares them, `--clear-claude-pause` is also needed. Clearing moves each sentinel into `.codex/sijav-loop/cleared-sentinels/` after the state is saved; nothing is deleted.
- Pausing: `pause` creates `<project>/.stop` before touching the state, so the pause holds even when the state write fails. It exits 0 when both worked, 3 when only one did (naming the other's cause), and 1 when neither did.
- Restarting: replacing a run that is active or paused and already counted needs `--restart`.

Proven live (project-hooks form, Codex 0.159.3, runs `20261001T043110Z-run` and `20261001T045941Z-run`; installed native-plugin form, run `20261001T060506Z-run`):

- Stop continuations ending in the exact promise;
- exhaustion at the cap without completion;
- after a manual compaction, the SessionStart(compact) hook delivering the exact, whole-law context, with the model's first answer using it and no continuation needed.

Not yet verified by a passing live run:

- automatic mid-turn compaction, which the harness does not exercise; the source path is described above;
- whether descendant threads fire `SessionStart(compact)`;
- whether a workspace-write sandbox would stop an owning agent from editing `.codex/sijav-loop` itself.

## Live proof harness

`tests/native_proof.py` works only in an explicitly named folder that is empty or holds only its own files. That folder must sit below the system temp directory; overriding this needs `--fixture-outside-temp`. The harness refuses a folder that overlaps the staging tree or the caller's working directory, sits in a git repository, or is below an ancestor with loop state, because hooks walk upward. Fixtures from the earlier harness version are refused. The unit tests never start Codex.

- `--prepare FIXTURE` writes `FIXTURE/.codex/hooks.json`, with no process started:
  - It contains the plugin's commands, with the interpreter (unquoted) and `${PLUGIN_ROOT}` replaced by concrete absolute paths.
  - An interpreter path containing spaces or shell characters is refused.
  - The two variants stay distinguishable (forward versus back slashes) but equivalent.
  - Re-preparing a fixture first copies its old marker and `hooks.json` into `proof-runs/<stamp>-superseded-preparation/`.
  - A fixture holding anything else, such as an owner's `.stop`, is refused and left untouched.
- `--prepare FIXTURE --installed-plugin-root ROOT [--plugin-id ID]` prepares the installed-plugin form instead:
  - ROOT is the plugin root Codex installed. It must hold exactly the staged package: a byte-identical native `.codex-plugin/plugin.json` with exactly its five fields and existing `skills` and `hooks` paths, a byte-identical `hooks/hooks.json`, `loop_hook.py` and `sijav_loop.py`, and no root `plugin.json`. A root `plugin.json` would make Codex load the package as a portable Agent Plugin and drop the hooks.
  - Only the marker is written. A fixture that already has a project `.codex/hooks.json` is refused, so the plugin's hooks are the only loop hooks.
  - `--check` and `--run` re-verify the installed package before starting Codex.
- `--check FIXTURE` starts `codex app-server` over stdio, sends `initialize`/`initialized`, calls `hooks/list` and stops, with no model call. It records `codex --version` and the app-server userAgent. With `--expect-codex-version V`, it fails unless both name V.
- `--run FIXTURE [--model gpt-6-luna] [--effort low] [--codex PATH] [--expect-codex-version V]` repeats that check. It calls no model unless both of the form's handlers meet every condition below. Otherwise it prints the `/hooks` steps and exits 2, and it never writes trust. The conditions:
  - Codex lists them with this platform's command variant. In the plugin form, this is the reviewed template with `${PLUGIN_ROOT}` expanded by Codex to the installed root, with no placeholder left.
  - They come from the right source: the fixture's `hooks.json`, or a `plugin` source under the installed root (and the pinned pluginId, if given).
  - Matcher, timeout and context limit are as written.
  - Both are trusted and enabled.
  - No other Stop or SessionStart hook would run, whether a project hook, another plugin's hook, or an untrusted one.
- After a run, a fixture state left `active` or `paused` is stopped through the CLI and recorded, so no fixture stays armed.

Every child process gets the caller's environment minus Codex identity variables (`CODEX_*THREAD_ID`, `CODEX_*SESSION_ID`, `CODEX_*TURN_ID`). Nothing else is removed, so auth configuration stays as Codex expects; no credential is read.

The run uses a new root thread per fixture. It checks `thread.sessionId == thread.id` and arms that id through the real `start` CLI.

Model and effort are set in two places:

- `thread/start` sets the thread's default with `config: {"model_reasoning_effort": <effort>}`. A native check fails unless the model and `reasoningEffort` that `thread/start` reports equal the requested ones.
- Every `turn/start` sends the model and effort again.

App-server reports no per-turn effective model or effort. The summary therefore records the requested values, the thread baseline and the per-turn override sent, and calls none of them "served". Laws are written outside the fixture and copied into the record afterwards. Any turn containing an item other than `userMessage`, `hookPrompt`, `agentMessage`, `reasoning` or `contextCompaction` fails, which covers every command, file, MCP, dynamic-tool and web-search call. Staged replies are judged on final answers only (phase `final_answer` or none).

- A: three continuations, then the promise; `complete` at iteration 3.
- B: identical replies; `exhausted` at cap 2, with `same_as_previous_stop` matching what the native flags imply.
- C: a text turn that, as a non-owner, stops normally. Then a law with a fresh marker is written and the loop armed. `thread/compact/start` runs, and the harness waits for the `contextCompaction` item. Two checks then apply:
  - The first final answer to the direct question "What is the compaction marker of the Sijav loop law? ..." must contain the marker, and the marker reply must complete the loop without a continuation.
  - Before that answer, a completed SessionStart run of the form under test must have reported a context entry equal to the adapter's text with the whole law.

  This is manual compaction only. The fallback `NO-LAW-IN-CONTEXT` remains in the question.

Each run directory under `FIXTURE/proof-runs/` keeps the redacted protocol log, the server's stderr and the process record; the record is written even when termination times out. It also keeps the CLI calls, adapter events, state snapshots, laws and a summary. The summary separates adapter, native and model checks. It names the form that was proved and the requested and actual Codex versions. For model and effort it gives the requested values, the thread defaults `thread/start` reported and the per-turn overrides sent; app-server reports no actual per-turn value. It also records the fixture state after the run.

## Provider readiness

An initial tool-free Claude readiness request failed before inference with an expired OAuth session. The owner temporarily prohibited Claude calls, then reauthorized them after signing in. A new readiness request to `claude-opus-5-5` completed successfully on October 1, 2026. Implementation and technical roast are authorized again. No actual project loop has been started; only bounded loops in disposable TEMP fixtures have run.
