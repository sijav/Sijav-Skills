---
name: sijav-codex-todo
description: Use an existing project's .claude/todo.db board for picking, recording and updating work or viewing its local read-only dashboard; preserve deterministic next order, dependencies, findings provenance and separate done/testing states.
---

# The existing project board

For a project that has opted in, `<project>/.claude/todo.db` is its record of
work. Use it instead of a private scratch to-do tool. The global skill's own
folder never determines the board: run from the intended project and let the
tool walk up to the nearest existing `.claude/todo.db`. Do not invent a
`.codex/todo.db` or create a second board because the caller changed.

Resolve `todo.py` or `todo.mjs` from this installed skill folder and pass an
absolute path. Both are the source tool, on the same database and commands;
Python needs 3.9+, Node needs 22.13+ for built-in SQLite. Neither needs a
separate SQLite install, packages or a migration run. The source tools are
bundled unchanged in this port; verify their installed paths before calling
them and never claim that an absent command is implemented.

Before project work, check whether another session is already working that
project using its full command line and session records. Concurrent loop and
roast transactions are permitted by the tool's SQLite lock handling; two
independent implementation sessions on one board can clobber each other.

## Pick the tool's task

Run `next` before touching project files. It explains its pick: existing
`in_progress` first, then current objective, highest severity, fewest story
points and lowest ID, excluding blocked tasks and unfinished dependencies.
Do not choose a different task with an invented ordering. If the pick looks
wrong, correct the recorded severity, points or parents within the owner's
instructions, then run `next` again. Mark the selected task `in_progress`.

If no board exists, the tool refuses. Create one only through an authorized
explicit `init --here` or `init <path>`; bare `init` also refuses. Never turn an
accidentally created empty board into a claim that all work is finished.

## Tasks, done and tests

Record every owner request, discovery and surviving roast finding while it is
known. Supply a concrete title, description, who wants what and why,
severity (`critical`, `high`, `medium`, `low`), story points
(`1`, `2`, `3`, `5`, `8`, `13`), dependencies where needed, and a checkable exit.
Leave ID assignment to the board so its established prefix survives. A vague
"it works" is not an exit.

**The latest three-pass procedure supersedes the source skill's old claim
that `done` requires running every test.** Under `$sijav-codex-dev-round`, build
the area's tasks and findings with their tests, running none except a
project-prescribed close exit. Mark completed work done with its observed
evidence, then roast it in the background. In the test pass, mark successful
tests `tested`; only the later real-user pass marks `e2e tested`. These start
false and are separate from done. Never manufacture test evidence or E2E
status from a successful exit check. Use the project's existing supported
status commands/schema; if a board lacks that capability, record the limitation
and route any compatible implementation to Claude rather than claiming it is
already available.

Claude Opus 5.5 alone writes implementation and tests and performs technical
roast. Codex coordinates, records and does the other reasoning. A deferred
Claude call leaves its implementation/review unfinished.

## Findings and review rounds

- `--parent` means blocking dependencies that must be done first.
- `--parent-task` means provenance: the finding exists because that task was
  reviewed. They are different relations. Findings are one level deep; a
  child's finding attaches to that child's original parent.
- Closing a task triggers a background roast. File surviving findings as new
  children, never as a reason to reopen it or hold it in `wait_for_roast`.
- When the last open child closes, roast the parent with all its children.
  Repeat through new children until a round has no findings.
- Record each review answer file with `roast <id> --file <path>`; then record
  `--filed <ids>` or `--filed none`, plus `--dismissed` reasons when appropriate.
  Updating the same file records judgment of the same round, not another one.

## Existing commands and records

The unchanged tool supports `list`, `show`, `add`, `edit`/`set`, `move`,
`validate`, `render`, `rm`, `phase` and `okr`, in addition to `next` and `roast`.
Read its usage for exact switches instead of guessing extensions.

- `move <id> blocked --reason <text>` requires a reason; a block is recorded
  beside the original status and removes the item from `next`. Any other move
  clears the block. `done --evidence <text>` and `dropped --reason <text>`
  preserve what was observed or why the work was dropped.
- `edit --note <text>` appends a note. Dependency edits replace the list and
  reject missing IDs or cycles; an empty parent value clears dependencies.
- `validate` is read-only and reports dangling references, cycles, inconsistent
  priorities/phases/closures, missing finding parents, unjudged rounds and
  inadequate exits. A failing validation is evidence to address, not a reason
  to rerun without a new lead.
- `render` preserves readable history alongside the binary DB; use
  `--out <path> --check` when checking staleness without writing. The default
  remains `.claude/TODO_BOARD.md`.
- Phases/objectives describe ordered goals. The first open phase is current;
  tasks with no phase count as current. Preserve the board's established
  phase/OKR vocabulary and ordering.
- `rm` needs its recorded reason; force removal cuts dependent/provenance
  links. Do not delete work merely to satisfy a completion condition.

The board records work; it does not impose roast scores, extra approval gates
or automatic reopening. Existing project rules decide closure and phase-based
branch/push behavior.

## Read-only browser dashboard

When the owner asks to view this board in a webpage, use the bundled
[dashboard](dashboard/README.md). Read that guide for its requirements,
options and disposable fixture commands. Resolve paths from this installed
todo skill, not from a remembered checkout or the project folder.

The dashboard needs Node 22.16+ on the 22 line or Node 24+, Python 3.9+ for
the tool's next order, and its own `ws` dependency. Installing that dependency
does not change the board tools' dependency-free contract:

```
npm ci --omit=dev --prefix "<installed todo skill>/dashboard"
node "<installed todo skill>/dashboard/server.mjs" --project "<absolute project root>"
```

The server prints the actual local URL and chooses a free port by default.
Board discovery walks up from the selected project to an existing
`.claude/todo.db`; `--db "<absolute board file>"` explicitly selects another
existing file. Missing boards are refused. The dashboard starts no task loop
and exposes no board mutation action. File events push updates by WebSocket.
Its full board, task records, field diffs and Relax view use stored data and
the board tool's picker and status policy. An unavailable policy is reported
rather than replaced with guessed order or Doing/Done groups. Done does not
imply tested, and missing fields are shown as unrecorded.

The same dashboard reads a loop board: an SQLite file with `item` and `dep`
tables, written by its own Python tool. Start it from the project with
`--db "<subfolder>/<board>.db"`. Its tool is the `.py` named after the board
file, beside it; pass `--tool "<file>"` when it is elsewhere. Its order is the loop tool's own
`board_order()`, run on a copy; the machine checks that tool's `next` may add
are not run. Any other SQLite file is refused. Long text and long id lists are
folded on the page, with Show more and Show less.

For a visual check or live-update test, use the dashboard's disposable demo
fixture under the OS temp directory. Installed demo/tests use the parent
`todo.py`; in a separate staging folder, set `TODO_SKILL_DIR` to the intended
todo skill's absolute path. Make changes only in that
fixture; do not mutate a real project's board to demonstrate the page. Stop
the demo with Ctrl+C, then verify its cleanup. A successful synthetic-board
check does not establish every project's schema or data.

## Compatibility when the tools change

All-project compatibility is required: additive tables/columns/commands only,
and old boards retain their output until a new feature is used. Claude must
change both runtime implementations together and verify byte-level parity
and subtask behavior against throwaway copies of every discovered board, never
the real boards. Commit authorized skill changes in their owning repository;
do not push or publish outside the owner's/project's existing authorization.
