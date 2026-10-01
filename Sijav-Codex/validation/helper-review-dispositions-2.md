# Dispositions: second helper reviews

Reviews:
- `../other-helper-technical-review.md`: Jev, research verification and setup.
- `../helper-postreview.md`: the Claude helper after its rewrite, items R1-R6.

Primary facts used:
- The installed typesafe-sdk 0.7.1 public source (`typesafe_sdk/__init__.py`, `_core/retry.py`,
  `_core/errors.py`, `_core/response_types.py`, `_core/client/sync/client.py`, `_core/transport.py`,
  `_core/config.py`), with `../typesafe-sdk-contract.md`.
- The Claude Code 2.1.286 public help, and Claude Code's permission documentation.
- Public `command.json`/`result.json` fields of the TEMP smoke runs at 05:00 and 05:04 UTC.
- The Codex app-server `PluginSummary` schema in `../native-hook-research/`.
- The root's native-CLI JSON field list.

No Jev or model request was made and no key was read. The real SDK was exercised only through an
in-process `httpx2.MockTransport`.

## Jev, verify and setup (other-helper-technical-review.md)

| Finding | Disposition |
| --- | --- |
| H1 elided quotes could be stitched into invented or reversed meaning | **Fixed.** Matching is word for word, on word boundaries (`can` never matches inside `cannot`). Typography, spacing and case are ignored. An elided quote is accepted only when every piece has at least 4 words and each piece follows the last within 300 characters; otherwise the verdict is `unconfirmed: elided quote too fragmentary to check`, never "supported". Jev is sent the page's own matched wording, gap included, not the quote as given. Tests: adversarial cases (piece soup, a reversed `can`/`cannot`, pieces 600+ characters apart, a word fragment) and the matched passage sent to Jev. Faults planted: lowered piece limits; boundary-free matching. |
| H2 a crash or unexpected exception after the request could lead to a second ask and a lost reply | **Fixed.** `jev.py` marks the run `calling` (request number, start time, helper pid) before the call. Every other command refuses while a call is in flight. `recover --confirm-stopped` closes the run: it judges a reply already saved, or records the outcome as unknown, and never asks again. Any exception (unexpected SDK or library errors, a reply that is not JSON) is recorded in `error-N.json` with what is known: not sent, answered, or unknown. An interrupt is recorded before it propagates. `finalize` refuses a `calling` run and never says "no Jev step was run" when requests were made. `verify.py` writes each batch's request before the call and records every failure kind the same way. A request left with neither reply nor error makes the rerun refuse until `--recover-interrupted` records it as interrupted (outcome unknown). Tests: a real process kill mid-call, an interrupt, unexpected and unserializable replies, recovery of a saved reply, and verify's interrupted and unexpected batches. Faults planted: no in-flight state; an unrecorded interrupt; verify asking an interrupted batch again. |
| H3 the fake SDK defined the contract | **Fixed against the installed 0.7.1 source.** The call passes `retry=RetryPolicy(max_retries=0)` to the client and the call (`retry=None` would mean the default two retries), plus a 60 s positive, finite HTTP timeout. The SDK must be the 0.7.x series; anything else is unjudged. Structured `Noul.instructions` are valid `JSONContent`, so they were not changed (the review's own premise was wrong there). New `tests/test_jev_sdk.py` drives the **real** SDK through `httpx2.MockTransport` and checks: the wire body and auth header; the 60 s timeout; exactly one HTTP attempt for a 503 and for a read timeout; integer score levels serialised as strings; an unknown answer type dropped by the SDK and caught as missing; a malformed 200 as `TypeSafeAPIResponseValidationError`; a malformed key refused before any request. The fake SDK now mirrors 0.7.1's names, signatures and error shapes and logs the retry and timeout it receives. Fault planted: retries left at the default. Limit: `test_jev_sdk` runs only where the SDK is installed (Python 3.13 here) and skips on the bundled 3.12. |
| M1 DNS rebinding, redirects, NAT64 | **Fixed.** Every resolved address must be public, including the IPv4 inside NAT64 (`64:ff9b::/96`, `64:ff9b:1::/48`), 6to4, Teredo and IPv4-mapped IPv6. A mixed DNS answer is refused. The connection goes to the checked address (pinned `HTTPConnection`/`HTTPSConnection`, with SNI and certificate verification for the host name). The peer address is compared after connecting. Every redirect is resolved and checked again, and at most 5 are followed. Tests: a simulated rebinding answer connects only to the first, checked address with one lookup; a redirect to a private host; a redirect loop. Fault planted: connecting by host name. |
| M2 charset, truncation, missing `</head>` | **Fixed.** Decoding is strict, using the header charset, else the HTML `<meta>` charset, else UTF-8. An unknown or wrong charset makes the page unread with its cause. A page over 5 MB is recorded as truncated, and a quote missing from it is `unconfirmed: page truncated...`. `head` is no longer skipped as a whole (its end tag is optional); only `title`, scripts and styles are. Any unexpected error for one page is recorded on that page. Tests use a loopback server. Faults planted: lossy decoding; truncation counted as "not on its page". |
| M3 snapshots counted as fresh pages from arbitrary files | **Fixed.** A snapshot must be a file inside the run folder, with its `sha256`, `fetched_at` and `by`. A file outside the folder, a `..` path, a hash mismatch or missing provenance refuses the whole check before anything runs. Matches are labelled "snapshot by X, fetched T" in the table and records. Fault planted: an outside file accepted. |
| M4 a resumed check silently used stale inputs | **Fixed.** `inputs.json` fingerprints the claim list, the snapshot index, the findings, the fetch mode and Jev's availability. A rerun with any of them changed is refused and names what changed. Fault planted: no comparison. |
| M5 the brief could change after open | **Fixed.** `open` records the brief's sha256 in `state.json`. Every command re-hashes and revalidates the brief, and checks the state's fields, refusing with exact causes instead of a traceback. Fault planted: hash check removed. |
| M6 setup hang and unverified install | **Fixed.** Commands run with a configurable timeout (`--command-timeout`, default 600 s). On timeout the whole process tree is stopped (`taskkill /T` on Windows, the process group on POSIX) and the output is read with a deadline. Postconditions, with no fallback: `marketplace add` must report `marketplaceName` (equal to the catalog name), `installedRoot` (an existing folder) and `alreadyAdded`; `plugin add` must report `pluginId`, `name`, `marketplaceName`, `version`, `installedPath` (an existing folder) and `authPolicy`. `plugin list` must show `sijav-codex` with `installed: true` and `enabled: true`. Otherwise "INSTALL NOT VERIFIED" is printed, success is never claimed, and the exit code is 1. Tests: each missing field, a disabled plugin, a missing plugin, a hung command whose child holds the pipe (killed within the timeout). Faults planted: success without the list check; a silent name fallback; the tree left running. Limit: the outer shape of `plugin list --json` is not proven for the CLI, so the setup searches it for entries carrying those explicit booleans. The real install by the root is the proof. |
| LOW code forms | **Fixed.** Added `export ...` declarations, unified-diff headers and assignment from a call (`x = load(y)`). A bare `return x` line was not added, to avoid refusing ordinary prose. |
| LOW invented facts under other keys | **Fixed.** `what_was_asked_for`, `checked_facts`, `anything_critical` and `facts` are refused at any depth of a framing's state. Fault planted. |
| LOW request files for calls never made | **Fixed.** The field is now `handed_to_sdk_at`, and each failure records whether the request was sent. |
| LOW secret scan | **Fixed.** Text files are scanned for private-key blocks and common token formats (`sk-`, GitHub, AWS, Slack). A bare "token" in a file name no longer flags harmless files. Symbolic links are refused. |
| LOW one shared root function | **Not merged.** The three helpers stay independent files on purpose, so each runs alone from an installed copy. All three use the same order (board, then `.git`, then `.claude`), and each has a test. |
| LOW batch size and the token limit | **Kept.** 40 claims per batch, as in the source. A refusal for length leaves that batch unjudged, which fails safe. No new shortening loop was added. |
| LOW malformed records | **Fixed.** `open_run` and `verify status` report damaged files with their cause; tests cover them. |

## Claude helper (helper-postreview.md)

| Finding | Disposition |
| --- | --- |
| R1 settings files could widen approvals or redirect the provider | **Fixed with the supported flag.** `--restricted` is added beside `--safe-mode` (2.1.286 help: it ignores user, project and local settings files and confines file tools to the working directories; `--tools` still names the command tools and WebFetch). It is not a bypass. The fake now reads a project's `.claude/settings.json` unless `--restricted` is given, and a test shows the same file would apply without the flag. Fault planted: flag removed. **Not yet live:** the root's next TEMP smoke should plant `allow: ["Bash"]` and an `env` entry to prove it on the real CLI. |
| R2 permission semantics and silent denials | **Partly proved, partly fixed.** The 05:00 and 05:04 smoke runs confirm that `--permission-mode manual` is accepted and that init reports `default`; the 05:04 run had `num_turns` 2 and no denials. A Read inside the project has not yet been shown to succeed under these rules (the root's fourth smoke is pending). `permission_denials` are now printed as a note and kept in `result.json` (test and fault). `Edit(./**)` covering Write and the deny strings follow the documentation, not a live run. |
| R3 plugins and skills under safe mode | **Fixed in claims and flags.** `--disable-slash-commands` is added. The docstring and prose now say that the init still lists installed plugins and skills, as observed in the smoke runs: eight plugin names and 18 skills. Their names are recorded in every result. Whether a listed plugin's hooks run is not proved; `--include-hook-events` was not added, because the hook event shape is unknown and guessing it would be a false check. |
| R4 runtime and TLS environment | **Fixed.** `NODE_OPTIONS` and `NODE_TLS_REJECT_UNAUTHORIZED` are dropped with the provider variables. `CLAUDE_CODE_OAUTH_TOKEN`, `CLAUDE_CONFIG_DIR`, proxy and CA-bundle variables are kept so ordinary OAuth sign-in and corporate networks still work. Their names (never values) are recorded in `command.json` as `kept_environment_noted`. Fault planted. |
| R5 the not-saved report wording | **Fixed.** Separate messages for "run record not saved" and "session state not saved"; exit 6 only when a reply was printed. Unit test. |
| R6 a refused new purpose left a folder | **Fixed.** A new purpose's rule, scope and `--resume-unconfirmed` checks run before its folder is created. Test and fault. |

## Planted faults

`validation/planted_faults.py` now has 52 faults, each run against a throwaway copy of the package.
- `--check-only` confirmed that all 52 plant (their text occurs exactly once).
- Results run so far are in `validation/planted-faults-*.txt`.
- The root's final `run_all` reruns all of them.

## 2026-10-01 · Verification after the recorded reviews

The tables and no-model-request statement above describe their original
review. They are retained as history; later verification made real model
calls in disposable TEMP fixtures.

- The fifth live technical helper call, `051503Z`, reused conversation
  `bd063f17-0778-4a84-b008-962d2ef7d32f` on `claude-opus-5-5` and exercised
  Read with safe/restricted/disable-slash settings and normal permissions.
- The separate real code-permission smoke at 06:03 UTC passed all 18
  assertions: inside Write succeeded; outside Write and planted `.env` and
  `dummy.key` reads were denied; no shell was offered; fixture project
  provider/hook settings were ignored. Public output is
  `claude-code-permission-smoke.txt`.
- The completed companion unit run passed 126/126 on Python 3.13.14, including
  seven tests using the real typesafe-sdk 0.7.1 through local mock transport.
  Full stdout/stderr is `helpers-final-unittest.txt`. No live Jev key/call.
- The fault plan now contains 57 plantable cases, superseding the earlier
  52-case plan above. Its final completed report is pending; progress across
  batches is not an all-passed final result. Earlier raw failure logs remain.
- Focused validation of the newly identified atomic-outcome/runner fixes is
  pending. No actual project board or active project loop was used for the
  verification described here.

## 2026-10-01 · Final focused verification

The atomic outcome publication issue is resolved. All 46 focused Jev,
real-SDK, permission-smoke and validation runner checks passed after that
fix (`final-focused-unittest.txt`), including fsync/link/interrupt
regressions. The earlier full run passed 126 units; the current 128 helper
cases are covered across runs, not one full 128-case execution.

The final matrix has 58 plantable cases (`final-fault-plantability.txt`).
The earlier 57 were caught across documented runs, and the three new or
repointed outcome-publication mutants were caught after the final small fix.
This closes the pending fault/focused-check status above without replacing
historical results. No single full 58-case execution is claimed.

Portable provenance copies are `reviews/typesafe-sdk-contract.md` and
`reviews/last-fixes-technical-review.md` beside this record. No live Jev
request/key was used. Final source copy and cache refresh are separate,
still-pending release operations.
