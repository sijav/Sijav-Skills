# Dispositions: independent technical review of claude_session.py

Review: `../helper-technical-review.md` (outside this package). Evidence used:

- the public Claude Code 2.1.286 help, `../claude-cli-help.txt`, now pinned byte for byte as
  `tests/fixtures/claude-2.1.286/help.txt` (sha256 13dd7186…e0b2);
- the public fields of the real init event of the October 1 smoke run, pinned as
  `tests/fixtures/claude-2.1.286/init.json`;
- Claude Code's permission documentation, https://code.claude.com/docs/en/permissions.

Only that run's `command.json` files and the public init/result fields of its `stdout.jsonl`
were read. No thinking text, credentials or other logs were read.

Every row below has a test in `tests/test_claude_session.py`. The rows marked † also have a
planted fault in `validation/planted_faults.py`.

## High

| Finding | Disposition |
| --- | --- |
| H1 failed `--resume-unconfirmed` promoted a guessed id | **Fixed.** An id is confirmed only when the CLI reports it and a turn reaches the model. A failed attempt keeps the purpose unconfirmed, with the same candidate and no `session_id`. The reviewer's "retry with `--session-id`" became the owner's explicit, single choice: `--resume-unconfirmed new` (the same id again with `--session-id`) or `existing` (`--resume`). There is no automatic fallback. `status` names the likely choice from the evidence. The fake refuses a reused `--session-id` and a resume of an unknown id, so neither choice can start a second conversation. † |
| H2 bare Read/Write/Edit pre-approved everywhere | **Fixed per official syntax.** Bare `Read`/`Glob`/`Grep` are no longer pre-approved: reads inside the working directory need no approval, and reads elsewhere would prompt, which `--permission-prompts none` denies. Code mode pre-approves only `Edit(./**)`; the docs say Edit rules govern every file-writing tool and `Write(path)` rules are never consulted. It denies `Edit(.git/**)`, `Edit(.claude/**)`, `Edit(.codex/**)`, `Edit(.githooks/**)` and `Edit(.husky/**)`; deny beats allow, and a single-segment deny matches at any depth. Both modes deny reads of `.env`, `.env.*`, `*.pem`, `*.key`, `~/.ssh`, `~/.aws`, `~/.azure`, `~/.gnupg`, `~/.kube`, `~/.docker/config.json`, `~/.claude`, `~/.codex`, `~/.config/gh`, `~/.git-credentials`, `~/.netrc`, `~/.npmrc` and `~/.pypirc`. Command rules must name their program first; `Bash`, `Bash(*)`, a leading `*` and parameter forms are refused. The tests check the exact rule strings. † **Limits, stated in the prose:** these rule strings follow the docs but have not been exercised by a live call. Permission rules bind Claude's tools and the file commands Claude Code recognizes, not programs an approved command runs (the docs say so). Technical mode still pre-approves bare `WebFetch`, so project content Claude can read could be sent to a URL it fetches. The secret-file denies narrow that; only sandboxing would close it. Finished run files are not made read-only; the `Edit(.codex/**)` deny is the protection against Claude rewriting them. |
| H3 the fake accepted anything | **Fixed.** The fake parses its options, argument shapes and choices from the pinned real help, the way commander does. It rejects unknown flags, bad choices, positional prompts and non-UUID `--session-id`. It resumes only sessions it actually created, and refuses an id already in use. Its init is the real 2.1.286 init with this run's values, tools sorted as the real one listed them. One test parses every argument shape the helper can build under the pinned help, and another checks the parser rejects what 2.1.286 would, including `--permission-mode default`. Each result records the init's `claude_code_version` and whether it differs from the pinned 2.1.286. Marked unverified in the fake: the exact wording of the "No conversation found" and "already in use" errors, and that `manual` makes init report `default`. |

## Medium-high

| Finding | Disposition |
| --- | --- |
| M1 an exception in launch left Claude running | **Fixed.** `launch` stops the whole tree on refusal, timeout, a reader error and any `BaseException` (KeyboardInterrupt included), and again in `finally`. On Windows, closing the Job Object also kills the tree. Tests: an injected MemoryError in output handling, and an injected KeyboardInterrupt. |
| M2 a failed final state write lost the reply | **Fixed.** State writes retry with backoff for 5 s. If the final write still fails, the reply and record path are printed anyway and the exit code is 6 ("STATE NOT SAVED"). `recover` then uses the run's own `result.json` instead of rewriting it. Test (Windows): `state.json` is held open, then exit 6, the reply is printed, `recover` succeeds, and the next call resumes. † |

## Medium

| Finding | Disposition |
| --- | --- |
| M3 permission mode inherited | **Fixed with the CLI's accepted value.** The reviewer's `--permission-mode default` is not a choice in 2.1.286's help (`acceptEdits, auto, bypassPermissions, manual, dontAsk, plan`). The docs say `manual` is the alias of the normal `default` mode (v2.1.200+), so the helper passes `--permission-mode manual` and requires init to report `default` or `manual`. † |
| M4 a stray `.claude` moved the root | **Fixed in all three helpers.** The nearest board wins, then the nearest `.git`, then the nearest bare `.claude`, all below the home folder. † |
| M5 ids from state went into argv unchecked | **Fixed.** Session ids in state, in the stream, and just before launch must be UUIDs; a state's `purpose` and `project_root` must match its folder and project. The space form `--resume <uuid>` that the live run used is kept, since a UUID cannot start with `-`. † |
| M6 init not required | **Fixed.** The first parsed event must be the init. It must report: the exact model, no tool beyond those offered, no MCP server, the normal mode, `apiKeySource` `none`, the expected session id and the project cwd. Anything else stops the CLI at that event. † |
| M7 a descendant holding stdout hung the call | **Fixed.** Stdout is drained by a reader thread with a bounded join. On Windows the CLI is created suspended, put in a Job Object with kill-on-close and then resumed. After the CLI exits, the tree is stopped so leftover descendants end with the call. On POSIX, the CLI's own process group is killed; a descendant that calls `setsid` escapes it, and the prose says so. Test: an orphaned grandchild holding stdout; the call ends in under 40 s and the grandchild is gone. † |
| M8 inherited provider/proxy settings | **Fixed.** `ANTHROPIC_*`, `CLAUDE_CODE_USE_*`, `AWS_BEARER_TOKEN_BEDROCK` and `CLAUDE_CODE_SKIP_*_AUTH` are removed from the child environment; `CLAUDE_CODE_OAUTH_TOKEN` is kept. Only the removed names are recorded. Init must report `apiKeySource` `none`, which is what the real OAuth sign-in reported. † **Limit:** what an OAuth-token sign-in reports there is unverified; a different value is refused, never silently used. Ordinary `HTTP(S)_PROXY` network settings are left alone. |

## Low-medium and low

| Finding | Disposition |
| --- | --- |
| L1 rules may never have been delivered | **Fixed.** The state tracks `rules_delivered`. Until a call has delivered a turn, every call must carry the rules or `--no-project-rules`. A first call that reported an id without a turn is now unconfirmed (not ready). Follow-up return codes are asserted. † |
| L2 a CLI in the current folder could be picked | **Fixed.** The helper walks PATH itself, skipping relative entries and the current and project folders. † |
| L3 no Claude pid in state | **Fixed.** The pid is written to the state right after launch. `recover` refuses while that pid is alive unless `--pid-is-other-program` is given. |
| L4 recover/reset could fail permanently | **Fixed.** `recover` keeps an existing `result.json`/`reply.md`. A second `recover` finds nothing to do. `reset` keeps an earlier generation file and writes a timestamped one. The "exited None" message is gone. |
| L5 state validation gaps | **Fixed.** `scope`, counters, the new flags, `history` entries and the running run's fields are all validated. A missing field gives exit 5, not a traceback. |
| L6 directories created before validation | **Fixed.** Only a call with `--scope` creates a purpose folder. `status`/`context`/`reset`/`recover` need an existing one, and `list` explains a folder without state. |
| L7 substring error classification | **Fixed.** Whole-word patterns, applied to stderr and failure text only. Unit test. |
| L8 code mode allowed extra tools | **Fixed.** Any tool not offered is refused in both modes. |
| L9 timeout race / CRLF / WebFetch model | **Fixed.** A run whose success result arrived before the deadline is `ok`, with `stopped_after_result` recorded. The reply is written to stdout as bytes, keeping its own line endings. Models other than claude-opus-5-5 in `modelUsage` are recorded and noted; the served reply model is still enforced. Policy: a tool's own model (for example WebFetch's) is recorded, not refused. There is no deterministic test for the timeout race. |

## Test gaps listed by the review

All are covered now:

- a reply where only the assistant message comes from another model;
- an authentication failure on resume keeping the session;
- a failed `--resume-unconfirmed`;
- banned flags checked on resume argv, including `--fork-session`;
- a final state write failure;
- root precedence;
- a tampered or copied state.

One gap remains: the tests run on Windows. A `.cmd` launcher is covered only by the cmd.exe argument guard; there is no end-to-end `.cmd` test.
