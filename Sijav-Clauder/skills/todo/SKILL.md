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
A board made before the change gains the new tables on its first run and prints
exactly what it printed until something uses them. Check a change against a
copy of every board on the machine, never against the boards themselves.

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
- **The tables create themselves** the first time a board is opened. There is no
  migration step to run by hand.

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

That they agree is tested rather than hoped for. `test-parity.py` drives 90
commands through BOTH halves, against a fresh board each, and compares stdout,
stderr and the exit code as bytes:

```bash
python "${CLAUDE_SKILL_DIR}/test-parity.py"
```

**If you change one half, change the other and run that.** Two implementations
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
- `status`: a task goes to `done` the moment it is finished and proven, which
  means its tests pass, lint and typecheck are clean, the build succeeds and it
  was actually run. The roast fires **after** that, in the background. The
  owner set this order on 2026-09-10: _"you finish the task first (with test and
  everything and you make sure it works, THEN and only THEN you put it in done
  and roast the task ... in background)"_. `wait_for_roast` is a leftover from
  before that rule and nothing should use it: a task is never closed pending a
  roast, and never held open waiting for one
- `exit`: checkable by someone who did not do the work. "It works" is not an exit
  condition. "The header renders at the design height in fa-IR dark" is.

**Ids keep the board's own prefix.** Leave `--id` out and the next id follows
the highest one on the board, so a KN board gets KN-483 and an SB board SB-181.
An empty board takes the capitals of its project folder's name (MyProject gives
`MP`), or the first two letters when there are fewer than two.

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

**Evidence and reasons are recorded when given, never demanded.** `show` prints
them, with the notes, under the card.

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
as broken.

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
