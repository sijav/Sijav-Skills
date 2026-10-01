# Dispositions: final helper review (all-helper-final-review.md)

No Jev or model request was made. The real SDK 0.7.1 was exercised only through an in-process mock
transport. No real Claude call was made by the coder.

| Finding | Disposition |
| --- | --- |
| M-A a run could stay `calling` forever | **Fixed.** `write_state` retries a Windows `PermissionError` with backoff for 5 s, the same as `claude_session.py`. Closing a run goes through `settle`. It creates `jev.json` once; if the file already exists, it is adopted only when it records an outcome (judged or unjudged) of the **same** request number. It is never overwritten, never re-asked and no status is guessed. Anything else is refused with a clean error that leaves the file untouched. `recover --confirm-stopped` adopts a recorded `jev.json` first. Tests: the real Windows sequence on both paths. A helper process whose final state write is blocked by a held `state.json` handle exits 1 with `jev.json` recorded and the state still `calling`. `recover` then closes the run (judged with exit 0; unjudged with exit 3) with no second Jev call. Also: a mismatched request number is refused and leaves the bytes unchanged; the same request's unjudged record is adopted as is. Faults planted: create-only with no adoption (stuck); adopting another request's outcome. |
| L-B the setup could hang on a pipe held by an escaped descendant | **Fixed.** `run()` uses its own daemon reader threads. It waits for the process with the timeout, stops the process tree, then joins the readers with a 10 s deadline. Any pipe still held is left to process exit and never closed (closing it blocks on the reader lock). The output read so far is kept, with a note. Test: a grandchild orphaned so that `taskkill /T` cannot reach it holds stdout; setup returns in under 60 s with the partial output and the note. Fault planted: an unbounded join. |
| L-C version queries were unbounded | **Fixed.** `version_of` goes through the bounded `run()` with a 30 s timeout. |
| L-D a non-JSON reply lost its raw body | **Fixed.** `error-N.json` keeps `raw_body` (scrubbed of the key) and `request_id` when the SDK exposes them. The outcome stays "answered". SDK errors raised before anything is sent are still recorded as "unknown", as the review allows; this is conservative, and Jev is still never asked twice. Test. |
| L-E tested Codex version | **Fixed.** `TESTED_CODEX = "0.159.3"`, the CLI the native install and core proof ran on. The catalog path note about the 0.159.2 source stays true. |

## The real-CLI permission fixture (new)

`tests/permission_smoke.py` makes no model call. `--prepare <new empty folder under TEMP>` refuses a non-empty folder, anything outside TEMP, the package or current folder, and any folder below a project (a board, `.git`, `.claude` or `.codex` below the home folder; a board or live loop state at or above it).

It writes only synthetic files:
- a fixture project with LAW and PROMPT;
- a fake `.env` and `dummy.key`, both "not a real secret";
- a hostile `.claude/settings.json` that allows Bash and every path, sets `ANTHROPIC_BASE_URL` to the unroutable `http://127.0.0.1:9`, and adds a PreToolUse hook that would write `hook-fired.txt`;
- an owned empty `outside/` sibling.

It prints the one helper command the root runs: code mode, a fresh purpose, `--scope`, `--rules-file`, `--prompt-file`, `--project`, a 300 s timeout and **no** `--allow-command`.

`--check` reads only `command.json`, `result.json`, the tool-use names and targets from `stdout.jsonl` (never thinking text), `reply.md` and the created files. It expects:
- the argv flags;
- init: Opus 5.5, mode `default`, `apiKeySource` `none`, no Bash;
- a completed call (an applied provider redirect would have failed);
- the inside marker written;
- no outside file, and the outside Write in `permission_denials`;
- `.env`/`dummy.key` reads in `permission_denials`, and no fake value in any reply or record;
- no Bash use, and no hook marker.

Exit codes: 0 pass, 1 fail, 3 inconclusive (a step not attempted, or no run). Seven tests (synthetic records only) and two planted faults.

## Not proven, by design of this role

- Live Jev.
- The real CLI's handling of project settings, outside writes and secret reads. This needs the root's run of the fixture above.
- Hook trust in the installed plugin.

Everything else is offline as described.
