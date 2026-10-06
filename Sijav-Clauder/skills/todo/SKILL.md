---
name: todo
description: "The project board, a real SQLite database, for projects that have a .claude/todo.db. In those projects use it instead of TodoWrite: picking the next task, creating one, changing a status, or recording anything found mid-task."
---

# The board

`<project>/.claude/todo.db` is a SQLite database. It is the only record of what
is to be done on that project.

## The skill is global, the board is not

**This skill lives in the sijav-clauder plugin and works in every project.** The
board it opens is chosen by **where you run it**, not by where the script sits:
it walks up from the working directory to the nearest `.claude/todo.db` that
already exists, and refuses when there is none (see below).

That split is the whole point, and it was got wrong once in a way worth
remembering: the script used to live inside one project's `agent/` folder and
resolve the board from its own path, so every project it was called from wrote
the *first* project's board. The comment above that line claimed the opposite.
A skill kept inside one repository can only ever serve that repository.

**Every project that uses this skill keeps working on its own board.** The
owner's directive, 2026-09-14: "I care about ALL PROJECTS compatibility". So a
change to this skill only ever adds: a new table, a new column, a new command.
For test state, an old board opts in on its first valid test-state claim;
legacy reads and invalid claims do not migrate it or change its old output.
The new state stays on the same `.claude/todo.db`, with no second board. Check
a change against a copy of every board on the machine, never against the
boards themselves.

**More than one session can be pointed at the same directory.** Two of them
writing one board will clobber each other. Before starting work, check that no
other session is running against this project. Two processes touching a board
at the same moment, a loop and a roast, are fine: both halves wait up to five
seconds for SQLite's lock rather than failing at once, which Node's default of
zero did.

## Setup

**Node 22.13 or newer. That is the whole list.**

- **SQLite is not a separate install.** It ships inside Node as `node:sqlite`.
  Nothing to download, no `sqlite3` binary, no native build step.
- **No `npm install` is needed for the board.** The script imports only Node
  built-ins, so it works in a repository with no `node_modules` at all.
- **Base tables create themselves** when the board is opened; there is no
  migration step to run by hand. Test-state tables on an old board are added
  only by its first valid test-state claim, never by legacy reads or invalid
  claims.

`node:sqlite` has been unflagged since **22.13 and 23.4**. Before those it
exists behind `--experimental-sqlite`, and older still it is absent. The script
checks and says so plainly rather than failing with a stack trace. An earlier
version of this said Node 24, which was wrong by two minor releases.

## How to call it

Either runtime. They are the same tool, on the same database, with the same
commands and the same output, byte for byte:

```bash
node   "${CLAUDE_SKILL_DIR}/todo.mjs" next     # needs Node 22.13 or newer
python "${CLAUDE_SKILL_DIR}/todo.py"   next    # needs Python 3.9+
```

**Use whichever the machine has.** Neither needs anything installed: SQLite
ships inside Node as `node:sqlite` and inside Python as `sqlite3`, and both
scripts import only their runtime's built-ins, so they work in a checkout with
no `node_modules` and no virtualenv. They can be run against the same board in
any order.

The earlier parity check drove 90 commands through BOTH halves, against a
fresh board each, and compared stdout, stderr and the exit code as bytes.
That is historical evidence, not a current case count or a passing result for
the test-state changes. Run the current parity check with:

```bash
python "${CLAUDE_SKILL_DIR}/test-parity.py"
```

**If you change one half, change the other and verify parity.** When the owner
invokes the global development-round procedure, run that verification in its
test pass after the area's source and test source are built. Two implementations
of one tool drift silently, and the drift shows up as a board that reads
differently depending on which runtime happened to be installed. It has: Python
on Windows printed `\r\n` where Node printed `\n`, and the test, reading pipes
as text, could not see it. It reads bytes now, and Python writes UTF-8 with bare
newlines.

`todo` below is shorthand for either one. There is deliberately no
`npm run todo`: an npm script lives in one `package.json` and this skill is meant
to work in every project, including one with no `package.json` at all.

## This replaces the built-in to-do tool

**Do not use TodoWrite here.** It is private scratch that vanishes with the
session, it carries none of the nine fields the owner requires, and `next` does
not read it. Anything tracked there is invisible to the owner and to the next
iteration, which is the same as not tracking it.

Everything goes in this board instead: what the owner asks for, what is
discovered mid-task, what a roast finds. Write it down while you are holding it,
then carry on with what you were doing.

## The board is never created by accident

`todo` walks UP from where you run it looking for a `.claude/todo.db` that
already exists, and **refuses if there is none**, naming the path and exiting
non-zero. It does not create one.

It used to stop at the first `.git` or `package.json` and make a board there.
Every member of a workspace has a `package.json`, so running it from `apps/web`
made a SECOND board two levels below the real one. That happened three times in
one session, and one of those runs printed:

```
Started a new board at ...pps\web\.claude	odo.db
Nothing left.
```

**"Nothing left" is the loop's completion condition**, said about a database
invented one line earlier.

To create one, say where:

```bash
todo init --here          # in the current directory
todo init <path>          # somewhere named
```

Bare `todo init` refuses too. An init that guessed would only make the accident
opt-in.

## Before touching any file

```bash
todo next
```

Prints the task to work on and why it was picked. The rule is the owner's and is
not to be overridden in your head: **the current objective first, then highest
severity, then fewest story points, then lowest id, never one whose parent is
unfinished and never one that is blocked.** Anything already `in_progress` comes
first, so work in flight gets finished before anything new starts.

That holds for work in flight too. A started task (`in_progress` or
`wait_for_roast`) one of whose parents is not done, a parent added after it
started say, is not resumed: `next` offers other work and names the task, with
each parent's status, since a `dropped` parent or one no longer on the board
never becomes done. It resumes once every parent is done. With nothing to
pick, such a task counts among those waiting on unfinished parents, and `next`
never says "Nothing left" while one waits.

The lowest id is a number made of every ASCII digit in the id, joined, and
compared exactly at any length: `P2-010` is 2010 and `SB-1e3` is 13. Equal
numbers fall back to the id itself, character by character by Unicode code
point, so `X1` comes before `x1`. Arabic-Indic, fullwidth and other non-ASCII
digits are not part of the number. Both halves order the same way.

**A loop is per session, and its loop file sets its board and areas.** The
file is `.claude/<name>loop<...>.local.md`, naming the session (`session:`),
optionally its board (`board: .claude/todo.db`) and its areas (`areas: back,ai`),
set by the owner. The tool reads the loop file of the session running it on
every command (Claude Code gives each command `CLAUDE_CODE_SESSION_ID`, Codex
`CODEX_SESSION_ID`): `next` offers only the loop's areas, `--area` can only
narrow them, and `move <id> in_progress` on a task outside them is refused. A
parent in another area still counts once it is done. Two loops with different
areas never get the same task. A session with no loop file works as before:
the nearest board, every area, and `--area` picks freely (`unset` names tasks
with no area).

If the pick looks wrong, correct that task's severity, points or parents and run
it again. Do not simply pick something else.

Then:

```bash
todo move SB-001 in_progress
```

## Creating a task

Nine fields, and `add` refuses without them.

```bash
todo add \
  --title "Short imperative title" \
  --desc "What is actually to be built." \
  --why "The story: what breaks, or stays broken, without it, and for whom." \
  --severity high \
  --points 3 \
  --parent "SB-002,SB-003" \
  --exit "A condition someone else could check: a named test, or a scenario in a browser."
```

- `severity`: `critical`, `high`, `medium`, `low`
- `points`: 1, 2, 3, 5, 8, 13
- `parent`: comma separated ids that must be `done` first; omit if nothing blocks it
- `status`: record completed work as `done` under the project's closure rules,
  with observed completion evidence. The roast fires **after** that, in the
  background. The 2026-09-10 instruction to test before closing describes the
  earlier cadence; when the owner invokes the global development-round
  procedure, the three-pass cadence below applies. `wait_for_roast` is a
  leftover: a task is never closed pending a roast, and never held open waiting
  for one
- `exit`: checkable by someone who did not do the work. "It works" is not an exit
  condition. "The header renders at the design height in fa-IR dark" is.

**Ids keep the board's own prefix.** Leave `--id` out, or give it empty, and the
next id follows the highest one on the board, so a KN board gets KN-483 and an
SB board SB-181. The highest is the id whose trailing run of ASCII digits is the
largest number; that number goes up by one exactly, however long it is, padded
to at least three digits (`SB-0099` gives `SB-100`). An id that does not end in
an ASCII digit is passed over. An empty board takes the capitals of its project
folder's name (MyProject gives `MP`), or the first two letters when there are
fewer than two.

## Done, tested and E2E tested

`done`, `tested` and `e2e_tested` are separate records. Test states default to
false; completing a task or passing its close exit does not create test or
real-user evidence.

When the owner invokes the global development-round procedure, build the
area's tasks and findings with their test source first, running none except a
project-prescribed close exit. Mark completed work done with its observed
completion evidence, then roast it in the background. After the area's source
work is built, run its full test suite and reach 100% passing before recording
successful test proof. Only after all required work is tested does the later
real-user pass record E2E proof. This cadence applies to that invoked procedure;
outside it, follow the project's existing closure and verification rules.

Use either installed runtime:

    todo tested <id> --evidence <text>
    todo e2e <id> --evidence <text>
    todo untest <id> --reason <text>
    todo untest <id> e2e --reason <text>
    todo tests [--area <area>]

`tests` is read-only. Claims require real evidence; E2E proof requires prior
`tested` proof and an eligible completed task. On an old board, the first valid
test-state claim opts it into the additive state. Legacy reads and invalid
claims do not migrate it or change its old output.

Meaningful status, description, exit or open-finding changes invalidate the
affected proof and record a note. Title edits, appended notes, roast records
and no-op changes preserve proof; none creates it. Parent and child proof are
independent: proof on one does not prove the other.

## A roast's findings are CHILDREN of the task they came out of

When a task closes it gets roasted, and what the roast finds becomes work. File
that work against the task it came from:

```bash
todo add --parent-task SB-014 --title "..." --desc "..." --why "..." \
         --severity high --points 2 --exit "..."
```

`--parent-task` is **provenance**, and it is not `--parent`. `--parent` means
this cannot start until that finishes. `--parent-task` means this exists
BECAUSE that task was roasted and the roast turned it up. A finding is not a
blocker; it is a piece of the same job discovered late. The parent is usually
already `done` by the time its findings exist.

**One level.** A child never gets children of its own. Naming a child as a
parent attaches to that child's parent instead, and says so.

### Why it is worth the bookkeeping

It lets the board answer a question nothing else can: **is that task actually
finished?** Not "did somebody mark it done", but "has everything the review
turned up been dealt with".

So `move <id> done` prints what to do next:

- Closing anything says **roast it now**, in the background, and file what comes
  back with `--parent-task <id>`.
- Closing a **child** says how many siblings are still open.
- Closing the **last** open child says so, and asks for a roast of the parent
  **together with every child**, on what was done for the whole task rather than
  for the last piece. Anything that round finds becomes a new child, and the
  cycle repeats until a round finds nothing.

None of that refuses anything. Closing a task with open children works, and the
tool tells you what is unfinished. It is a prompt, not a gate.

Checked by `node "${CLAUDE_SKILL_DIR}/test-subtasks.mjs"`, which drives the real
script against a throwaway board in a temp directory: the whole cycle, the
flattening, the reparenting, and the fact that a close with open children still
goes through.

## Recording a roast round

What a roast said, and what came of it, belongs on the card it reviewed:

```bash
todo roast SB-014 --file <the roast's answer file> [--score 8] [--criticals 1]
# judge it, file what survives with add --parent-task SB-014, then:
todo roast SB-014 --file <the same file> --filed SB-031,SB-032      # or --filed none
todo roast SB-014 --file <the same file> --dismissed "what was rejected and why"
```

Recording again against the same file updates that round rather than starting a
second one, because judging a round is a second moment of the same round. `show`
lists the rounds, and `validate` names any card with a round nobody recorded as
judged.

## Blocked, notes, and what closed a task

```bash
todo move SB-007 blocked --reason "waiting on the owner's answer"
todo move SB-007 in_progress           any other move clears the block, and says so
todo edit SB-007 --note "what was learned"      appends; a note is never replaced
todo move SB-007 done --evidence "the check that was run"
todo move SB-009 dropped --reason "why it is no longer wanted"
```

**A block needs a reason**, because a blocked task leaves `next`, and a task that
leaves the board silently is lost. The block is a row beside the status rather
than a status of its own: the task table's CHECK cannot be widened on a board
that already exists without rebuilding it, so the task keeps the status it had.
Every reader asks one question, whether a task is blocked: `next` skips it and,
when nothing is left to pick, names what is blocked and why; `list` shows it
under BLOCKED with its reason; `show` prints the reason; `render` gives it a
column.

**Closure evidence and dropped reasons are recorded when given.** `show`
prints them, with the notes, under the card. Test-state claims require real
`--evidence`; test-state resets require `--reason`, as described above.

**Two moves warn and still move.** Moving a task out of `done` says that a finding
is a new card rather than a reopened one, which is the owner's rule for the loop;
starting a second task names the one already in progress. Neither refuses, since
another project's rules may differ and a refusal would be a gate on every board.

## Everything else

```bash
todo                       the whole board, by column
todo list --status backlog --area web,api --severity high  any of the three
todo show <id>             one task in full
todo move <id> done        backlog, in_progress, done, dropped, or blocked --reason
                           closing prints what to roast and what is unfinished
todo add --parent-task <id>  file a roast finding against the task it came from
todo edit <id> --severity critical --points 5
todo set <id> ...          the same as edit
todo validate              read-only; exits 1 and lists every problem it finds
todo render [--out path]   the board as Markdown, .claude/TODO_BOARD.md by default
todo render --out path --check      writes nothing; fails when the file is stale
todo rm <id> --reason "..." [--force]
```

`edit` takes any of the fields `add` takes, and changes only what it is given.
This is how you correct a task when `next` picks wrong, which the rule above
tells you to do: raise its severity, lower its points, or fix its parents. It is
also how a task is reparented as the plan changes.

```bash
todo edit SB-005 --parent SB-029        replaces the parent list
todo edit SB-005 --parent SB-029,SB-030 several
todo edit SB-005 --parent ""            clears it, nothing blocks this task
```

It refuses an id that does not exist, a parent that does not exist, and a parent
that would make a cycle, naming the path. A cycle is worse than a wrong parent:
every task in it waits forever, and `next` reports them as blocked rather than
as broken. Giving a started task a parent that is not done holds it out of
`next` until that parent is done.

Flags are read in pairs, as both halves read them: a flag at the end with no
value, `todo edit SB-005 --parent`, is ignored and changes nothing; `edit` with
nothing else to change says so and exits 1. Only an explicit empty value,
`--parent ""`, clears.

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

**`validate`** reports what `edit` cannot stop from happening over time: a blocker
or a phase that no longer exists, a cycle, a blocker less severe than the open
task it holds up, which `next` would never pick first, a done task whose blocker
is not done, a finding whose parent is missing or is itself a child, an exit
condition too short to check, and a closed task still carrying a block.

**`render`** writes the board as Markdown: the counts, what `next` would pick, the
objectives, a table per column, then every card with its notes, evidence and
roast rounds. A project that commits the rendered file gets a readable diff of
every change to the board, which a binary database cannot give.

**`rm`** removes a card and leaves only its reason behind. A card that other cards
wait on, or that findings came out of, is refused unless `--force`, which cuts
those links.

## Phases, which are the objectives the tasks serve

The owner's, 2026-09-11: a board of seventy cards says how much is left and
nothing about what is left **before the thing ships**. So tasks belong to a
phase, and a phase is a goal with an order: the first one is usually the MVP.

```bash
todo phase                              every phase, what it is for, and what is left in it
todo phase add MVP --goal "..."         the next position, or --position 1
todo phase done MVP                     it is finished; the next open one becomes current
todo add ... --phase Next               a card for later; the default is the current phase
todo edit SB-005 --phase Quality        move one
todo edit SB-005 --phase ""             out of every phase, which reads as now
```

The **current** phase is the first one still open. `next` works through it
before it offers anything from a later phase, whatever the severities say: what
the product needs to ship comes before what comes after it. A task with no
phase counts as current work, because an unsorted card is something to do now,
and the opposite would hide it behind every later phase.

A board with no phases behaves exactly as it always did, so nothing has to
adopt them.

### Objectives, the same rows under the owner's word

The owner's, 2026-09-12, calls them objectives, each with an id, a name and a
description. `okr` reads and writes the phase rows: the id is the phase's name,
the name its label, the description its goal.

```bash
todo okr                                              each objective, what is left in it
todo okr add --name "MVP: the pages" --description "..." [--position n] [--id OKR-1]
todo okr done OKR-1 | todo okr open OKR-1              met, or opened again
todo okr edit OKR-1 --name "..." --description "..." --position 2
todo add ... --okr OKR-2                              the same reference --phase is
todo edit SB-005 --okr none                           out of every objective
```

The id defaults to `OKR-<count + 1>`. A name or a position already held is
refused. A board that only ever used `phase` keeps working, and `okr` shows its
phases as objectives without names.

## What this board does not do

It records work. It does not gate it. There is no passing score, no minimum, and
nothing that refuses to let a task close. A roast finding becomes a new task
here and the finished task closes; it is never a reason to reopen work.

If a gate seems necessary, tell the owner and let them decide. A previous
version of this repository had gates nobody asked for, and a full working day
went into satisfying them while the product stayed empty.

## This folder is a git repository

The plugin's folder is versioned, locally, with no remote. **Commit what you
change here.** A repository nobody commits to is the same as no repository with
extra steps, and the reason this one exists is that `roast.py` was edited five
times in one session with no way back.

- an edit you have not committed: `git restore <file>`
- an edit you have: `git revert --no-edit <commit>`

`core.fsmonitor` is off for this repository deliberately. It is on in this
machine's system git config, and a watcher daemon here has deadlocked suites
that create throwaway repositories.
