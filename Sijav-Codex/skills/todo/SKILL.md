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
bundled with this skill; verify their installed paths before calling them and
never claim that an absent command is implemented.

Before project work, check whether another session is already working that
project using its full command line and session records. Concurrent loop and
roast transactions are permitted by the tool's SQLite lock handling; two
independent implementation sessions on one board can clobber each other,
unless the owner gave each its own areas (below).

## Pick the tool's task

Run `next` before touching project files. It explains its pick: existing
`in_progress` first, then current objective, highest severity, fewest story
points and lowest ID, excluding blocked tasks and unfinished dependencies.
Do not choose a different task with an invented ordering. If the pick looks
wrong, correct the recorded severity, points or parents within the owner's
instructions, then run `next` again. Mark the selected task `in_progress`.

The exclusion holds for started work too. A started task (`in_progress` or
`wait_for_roast`) one of whose parents is not done, such as a parent added
after it started, is not resumed: `next` offers other work and names it, with
each parent's status (`dropped`, or not on the board, never becomes done), and
resumes it once every parent is done. With nothing to pick, `next` counts such
tasks among those waiting on unfinished parents and never reports `Nothing
left` while any waits.

The lowest ID is a number made of every ASCII digit in the ID, joined, and
compared exactly at any length (`P2-010` is 2010, `SB-1e3` is 13); equal
numbers fall back to the ID itself, compared character by character by
Unicode code point (`X1` before `x1`). Digits outside ASCII, such as
Arabic-Indic or fullwidth ones, are not part of the number. Both runtimes
order the same way.

`list` keeps the board's stored SQL ID order. `next` and rendered lanes use
their ranking keys. Before sorting, each filtered candidate is checked in stored
ID order: severity first, then points. An old row with an unknown severity or
points outside 1, 2, 3, 5, 8, 13 refuses that ranking with native exit 1 and names
the first offending task; text and NULL points are not coerced into a rank.
Started, eligible and held groups are checked in that order. Render checks its
next pick before every ranked status column: in progress, waiting for a roast,
blocked, backlog, done and dropped. An unblocked row in another status stays
outside those ranked columns. Blocked and other-status rows can stay outside
`next` while `list` and `show` remain readable; NULL ranking fields use the same
explicit `NULL` label in both ports, and a BLOB shows as `<non-text>` (severity)
or `<non-number>` (points), the labels `validate` uses. An old REAL points column
reads 2 back as 2.0: both ports show an integral number as the integer (up to
2**53) and rank it as before; a non-integral one shows as stored (2.5). The two
ports may still write a REAL outside 1e-4..1e16, or an integral REAL beyond
2**53, differently. A stored INTEGER beyond 2**53 in any column the engine reads
(points, or for example a legacy epoch `created`) is a different case: the Node
half reads integers with node:sqlite's default number mapping, so that board is
unreadable to it, while the Python half reads it exactly. node:sqlite's exact
behaviour for such a value is not verified here; nothing the engine accepts is
narrowed for it. Non-number points, including text or NULL,
make aggregate totals unknown rather than inventing a number. Stored numeric
points remain summable even when outside the supported ranking domain. `validate` reports every unknown
severity and unsupported points value; starvation comparisons involving unknown
severities are skipped. Modern board constraints are unchanged.

A loop is per session, and its loop file sets its board and areas: the file
`.claude/<name>loop<...>.local.md` names the session (`session:`) and, when the
owner gives them, its board (`board:`) and areas (`areas: back,ai`). The tool
reads the loop file of the session running it on every command (from
`CODEX_SESSION_ID`, or `CLAUDE_CODE_SESSION_ID` under Claude): `next` offers
only those areas, `--area` can only narrow them, and starting a task outside
them is refused. A parent in another area still counts once it is done; loops
with different areas never get the same task. Without a loop file the tool
works as before, and `next --area` picks freely.

If no board exists, the tool refuses. Create one only through an authorized
explicit `init --here` or `init <path>`; bare `init` also refuses. Never turn an
accidentally created empty board into a claim that all work is finished.

## Tasks, done and tests

Record every owner request, discovery and surviving roast finding while it is
known. Supply a concrete title, description, who wants what and why,
severity (`critical`, `high`, `medium`, `low`), story points
(`1`, `2`, `3`, `5`, `8`, `13`), dependencies where needed, and a checkable exit.
Leave ID assignment to the board so its established prefix survives; an empty
`--id ""` is the same as leaving it out. The next ID takes the ID whose
trailing run of ASCII digits is the highest number, and adds one to that
number exactly, however long, padded to at least three digits (`SB-0099`
gives `SB-100`). An ID that does not end in an ASCII digit is passed over. A
vague "it works" is not an exit.

`done`, `tested` and `e2e_tested` are separate records. Test states default to
false; completing a task or passing its close exit does not create test or
real-user evidence. Use the test-state commands below to record observed proof.

When the owner invokes the global development-round procedure, build the
area's tasks and findings with their test source first, running none except a
project-prescribed close exit. Mark completed work done with its observed
completion evidence, then roast it in the background. After the area's source
work is built, run its full test suite and reach 100% passing before recording
successful test proof. Only after all required work is tested does the later
real-user pass record E2E proof. This cadence applies to that invoked procedure;
outside it, follow the project's existing closure and verification rules.

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

The tool supports `list`, `show`, `add`, `edit`/`set`, `move`,
`validate`, `render`, `rm`, `phase` and `okr`, in addition to `next` and `roast`.
Read its usage for exact switches instead of guessing extensions.

Test state is additive on the same `.claude/todo.db`; it does not create a
second board. Use these commands through either installed runtime:

    tested <id> --evidence <text>
    e2e <id> --evidence <text>
    untest <id> --reason <text>
    untest <id> e2e --reason <text>
    tests [--area <area>]

`tests` is read-only. Claims require real evidence; E2E proof requires prior
`tested` proof and an eligible completed task. On an old board, the first valid
test-state claim opts it into the additive state. Legacy reads and invalid
claims do not migrate it or change its old output.

Meaningful status, description, exit or open-finding changes invalidate the
affected proof and record a note. Title edits, appended notes, roast records
and no-op changes preserve proof; none creates it. Parent and child proof are
independent: proof on one does not prove the other.

- `move <id> blocked --reason <text>` requires a reason; a block is recorded
  beside the original status and removes the item from `next`. Any other move
  clears the block. `done --evidence <text>` and `dropped --reason <text>`
  preserve what was observed or why the work was dropped.
- `edit --note <text>` appends a note. Dependency edits replace the list and
  reject missing IDs or cycles; an empty parent value clears dependencies.
  Flags are read in pairs: a flag at the end with no value is ignored and
  changes nothing, so only an explicit empty value (`--parent ""`) clears.
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
and exposes no board mutation action. The page never loads a board whole: it
asks typed, paginated questions over its WebSocket (lists five at a time as
they scroll, a task's record when opened), and file events push only what
changed.
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

Give `--db` more than once to show several boards on one page, each read with
its own tool. To show the page somewhere else, `--publish "wss://<relay>"
--publish-token-file "<file>"` adds one outbound socket to that relay: it
answers the relay's read-only questions and pushes changes, the relay keeps
nothing, and the token is read from the file, never typed.

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
