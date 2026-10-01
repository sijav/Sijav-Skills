**No high or medium defects remain in these fixes.** M-A, L-B, L-C and L-E are fixed and L-D is effectively fixed. One low gap is left, in how `jev.json` is first written. I read the source and tests only and ran nothing.

| Item | Disposition |
|---|---|
| **M-A** | **Fixed.** Every recorded outcome now goes through `settle` → `record_outcome` (`jev.py:612-636`, 906-919). It writes `jev.json` once. If one already exists, it is adopted only when it belongs to the exact request in flight and says judged or unjudged; anything else is refused with a clear error, left untouched, and the run stays `calling`. The state is saved only after the record is on disk. `recover` adopts an existing record before anything else and never asks Jev again (945-949). `write_state` now retries for 5 s with backoff (587-609). The tests trigger the real defect: `test_recover_adopts_only_the_same_requests_recorded_outcome` covers wrong-request refusal and same-request adoption, with byte-identical files and no new call. The Windows-only test holds `state.json` open during a real `ask`, then shows `recover` closing the run without a new call. |
| **L-B** | **Fixed.** `run()` now reads output on its own background threads and never closes a pipe that a reader may still hold (`sijav_codex_setup.py:344-371`). After the process tree is stopped, the threads get at most 10 s; whatever they read is kept, along with a note that a pipe was left open. In the new test, the process that starts the pipe-holder exits immediately, so the pipe-holder escapes the process tree. On Windows that is the exact trigger. The test asserts the run stays under 60 s, the partial output is kept, and the note appears. |
| **L-C** | **Fixed.** `version_of` goes through the bounded `run()` with a 30 s timeout (380-386). There is no test of a hanging `--version`, but it uses the same code path as the escape test. |
| **L-D** | **Effectively fixed.** A reply that can't be recorded keeps the scrubbed raw body and request id, and this is tested. Errors the SDK raises before sending are still labelled "unknown". That label is conservative, and a framing that passes validation can't trigger those errors anyway. |
| **L-E** | **Fixed.** `TESTED_CODEX = "0.159.3"`, and the fake tool reports the same version. |

**Remaining low gap: a partially written `jev.json` blocks the run for good.**
- **Cause:** `record_outcome` creates `jev.json` with a plain create-only write, which isn't atomic (`jev.py:627`).
- **Trigger:** The helper is killed, or the disk fills, during that write.
- **Effect:** A truncated file stays on disk. `recover` then refuses with "exists but cannot be read; left untouched", and `finalize` refuses any `calling` run, so the run can never be closed.
- **Fix:** Write to a temporary file, then `os.link` it to `jev.json`. That fails if the file already exists, so it never overwrites.

**What these tests and smokes don't prove:**
- The M-A test that holds `state.json` open runs only on Windows; elsewhere only the planted-file test applies.
- The L-B "pipe still held" check is only asserted on Windows.
- No live Jev call has been made through these helpers.
- Your permission smoke shows a planted project-settings hook being ignored. It doesn't show that hooks from globally installed plugins stay off under `--safe-mode`.
- The shape of the setup's `plugin list` output is confirmed only by today's real install.
