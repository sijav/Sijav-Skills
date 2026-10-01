# Development rules

Loaded by `$sijav-codex-rules dev`, on top of `general.md`. The history of each rule is in
`log.md` next to this file.

## D1. Keep the full output of commands

Capture every long or background command's full output (stdout and stderr) in
a log file from the moment it starts. Never cut output with `tail` or `head`
before it is saved; read excerpts from the saved log. Note the command,
folder, start time and exit status in the log.

## D2. Never run the same work twice

Before starting or retrying a command, check whether the same work is already
running: look at full command lines, not process names. A timeout, silence or
an empty process search means "unknown", not "finished". Stop only runs you
started, and check that they really ended. When a monitor tails a log with
`tail -F`, wrap it in `timeout` shorter than the monitor's own timeout.

## D3. Failures

Tools you write print the exact cause of each failure. A failing check is a
result to act on. Investigate a failure with the logs, exit codes and
timestamps you have before retrying. Never re-run a check just to change its
answer, and never repeat an exhausted check without a new lead.

## D4. Tests and rounds of to-dos

In the $sijav-codex-dev-round skill (moved there on 2026-09-28). Use it for every code
to-do.

## D5. Branches and pushes, by phase

- MVP: push to main after every to-do. No checks, and don't ask.
- Phase 0 (fixing the MVP): push to development; when phase 0 is done,
  development goes to main.
- After phase 0: development, staging and main. When a story is done, push
  development to staging. Pushing to main is the owner's, unless the owner
  tells Codex to do it.
- No other check before a push.
- Each project's rule set says which phase it is in.
