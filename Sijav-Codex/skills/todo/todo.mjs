#!/usr/bin/env node
// The board. A real database, at .claude/todo.db, local to this project.
//
//   todo                       the whole board
//   todo next                  what to do next, and why it was picked
//   todo next --area back,ai   the same, only among those areas' tasks
//
// A session's loop file (`.claude/<name>loop<...>.local.md` naming the session)
// sets its board and its areas. The tool reads it on every command: `next` offers
// only the loop's areas, `--area` can only narrow them, and starting a task
// outside them is refused. A session with no loop file works as before.
//   todo add --title ...       create a task
//   todo move SB-003 done      change a status
//   todo show SB-003           one task in full
//
// A task carries nine fields. `add` asks for six of them, because the other
// three are not yours to type: --title, --desc, --why, --severity, --points and
// --exit are required, the id is generated, status starts at backlog, and
// parent is optional. The header used to say "all nine fields required", which
// is the kind of small untruth that teaches the next reader the wrong thing.
//
// Selection rule, the owner's: highest severity, then fewest story points, then
// lowest id, and never a task whose parent is unfinished. Anything already in
// progress or review comes first, so work in flight gets finished.
//
// Done is not tested, and tested is not tested by a real user. Each is recorded
// on its own, with what proved it:
//   todo tested SB-003 --evidence "..."        the area's tests ran and passed
//   todo e2e SB-003 --evidence "..."           a real user's path was checked, after tested
//   todo untest SB-003 [e2e] --reason "..."    clear it by hand; what it had stays in a note
//   todo tests [--area back]                   which done tasks are tested, which are not
// A board gains the columns and the guards that keep them honest on its first
// `tested` that passes every check, and not before: until then it reads and
// prints exactly what it did. A status change, a changed description or exit
// condition, and an open finding each clear a test state, keeping what it had
// in a note.

import { existsSync, mkdirSync, readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'

// SQLite is inside Node, so this script has no dependencies and works in a
// checkout with no node_modules. It needs no flag from Node 22.13 and 23.4;
// from 22.5 to 22.12 and from 23.0 to 23.3 it needs --experimental-sqlite, and
// before 22.5 it does not exist. Saying which beats letting the import throw a
// stack trace at someone.
let DatabaseSync
try {
  ;({ DatabaseSync } = await import('node:sqlite'))
} catch {
  console.error(`This board needs node:sqlite, and this is Node ${process.version}.`)
  console.error('Node 22.13 or newer, or 23.4 or newer, has it built in. From 22.5 to 22.12, or 23.0 to 23.3, run with --experimental-sqlite.')
  console.error('There is nothing to install: SQLite ships inside Node, and this script has no dependencies.')
  process.exit(1)
}

// The board lives in .claude, the project-local folder, and is NOT committed.
// Each project and each checkout gets its own, which is the point: two sessions
// pointed at one repository must not be writing the same board.
//
// A BOARD is what says which board to use. Not a .git directory, and above all
// not a package.json: every member of a workspace has one, so the old rule
// stopped at apps/web and made a SECOND board two levels below the real one.
// That happened three times in one session, and one of those runs printed
//
//   Started a new board at ...pps\web\.claude	odo.db
//   Nothing left.
//
// "Nothing left" is the loop's completion condition, said about a database it
// had invented one line earlier. That is why this walks up looking for a board
// that already exists, and refuses rather than creating one.
const findBoard = (start) => {
  let dir = resolve(start)
  for (;;) {
    const candidate = join(dir, '.claude', 'todo.db')
    if (existsSync(candidate)) return candidate
    const parent = dirname(dir)
    if (parent === dir) return null
    dir = parent
  }
}


/** A loop file's front matter, the `key: value` lines between its first two `---`. */
const frontMatter = (path) => {
  let text
  try {
    text = readFileSync(path, 'utf8')
  } catch {
    return {}
  }
  if (!text.startsWith('---')) return {}
  const end = text.indexOf('\n---', 3)
  const fields = {}
  for (const line of text.slice(3, end === -1 ? 0 : end).split(/\r?\n/)) {
    const at = line.indexOf(':')
    if (at > 0 && line.slice(0, at).trim()) fields[line.slice(0, at).trim()] = line.slice(at + 1).trim().replace(/^["']+|["']+$/g, '')
  }
  return fields
}

/**
 * The loop file of the session running this command, found walking up. A loop is
 * per session: its file, `.claude/<name>loop<...>.local.md`, names its session and
 * may name its board and its areas. Claude Code and Codex give every command the
 * session's id; without one, or with no loop file naming it, nothing changes.
 */
const sessionLoop = (start) => {
  const session = process.env.CLAUDE_CODE_SESSION_ID || process.env.CODEX_SESSION_ID || ''
  if (!session) return null
  let dir = resolve(start)
  for (;;) {
    const folder = join(dir, '.claude')
    let names = []
    try {
      if (statSync(folder).isDirectory()) names = readdirSync(folder).sort()
    } catch {}
    for (const name of names) {
      if (name.includes('loop') && name.endsWith('.local.md')) {
        const fields = frontMatter(join(folder, name))
        if (fields.session === session) return { root: dir, file: join(folder, name), fields }
      }
    }
    const parent = dirname(dir)
    if (parent === dir) return null
    dir = parent
  }
}

/** The board of this session's loop when its loop file names one, else the nearest board. */
const boardOf = (start) => {
  const loop = sessionLoop(start)
  if (loop && loop.fields.board) return resolve(loop.root, loop.fields.board)
  return findBoard(start)
}

const [command = 'list', ...args] = process.argv.slice(2)

// `init` is the ONLY thing that creates a board, and it does not guess where.
// An init that inherited the old nearest-project rule would just make the
// accident opt-in through a badly scoped command.
if (command === 'init') {
  const target = args[0] === '--here' ? process.cwd() : args[0]
  if (!target) {
    console.error('todo init needs to be told where: `todo init --here`, or `todo init <path>`.')
    process.exit(2)
  }
  const made = join(resolve(target), '.claude', 'todo.db')
  if (existsSync(made)) {
    console.error(`There is already a board at ${made}`)
    process.exit(2)
  }
  mkdirSync(dirname(made), { recursive: true })
  new DatabaseSync(made).close()
  console.error(`Started a new board at ${made}`)
  process.exit(0)
}

const BOARD = boardOf(process.cwd())

// Refuse BEFORE mkdir and before SQLite opens. Both create what is missing, so
// a check placed after either is not a check.
if (!BOARD) {
  console.error(`No board. Nothing at or above ${process.cwd()} has .claude/todo.db.`)
  console.error('If this project should have one: todo init --here')
  process.exit(2)
}
// A board the loop file names is never created either.
if (!existsSync(BOARD)) {
  console.error(`This session's loop names the board ${BOARD}, which does not exist. Nothing was created.`)
  process.exit(2)
}

// Two processes on one board are normal: a loop adding a card while a roast or
// a second session reads it. node:sqlite's busy timeout defaults to zero, so
// the second writer failed at once with "database is locked", measured at 1 ms;
// with a timeout it waits for the lock. Five seconds is Python's sqlite3
// default, so both halves wait the same. It is SQLite's own pragma, the
// language bindings' documented way to set it, because the constructor's
// `timeout` option is newer than the oldest Node this tool runs on.
const db = new DatabaseSync(BOARD)
db.exec('PRAGMA busy_timeout = 5000')

const SEVERITIES = ['critical', 'high', 'medium', 'low']
const STATUSES = ['backlog', 'in_progress', 'wait_for_roast', 'done', 'dropped']
const POINTS = [1, 2, 3, 5, 8, 13]

// Test states, the owner's: done, tested and tested by a real user are three
// separate facts. They are six columns on `task` that a board gains on its first
// `tested`, never on opening, so a board that does not use them keeps its bytes.
const TEST_STATE_COLUMNS = [
  ['tested', 'INTEGER NOT NULL DEFAULT 0'],
  ['tested_how', 'TEXT'],
  ['tested_at', 'TEXT'],
  ['e2e_tested', 'INTEGER NOT NULL DEFAULT 0'],
  ['e2e_how', 'TEXT'],
  ['e2e_at', 'TEXT'],
]

// What counts as blank in a test state's evidence or a reason for clearing one:
// every character either runtime's own trimming treats as space, and the byte
// order mark. Python's strip() and JavaScript's trim() each miss some of the
// other's, so both halves and the board's guards use this one set instead.
const BLANK = '\t\n\x0b\x0c\r\x1c\x1d\x1e\x1f \x85\xa0                　﻿'

// The guards live in the board, not in either half, because every writer runs
// them: this tool, the other half, and an older copy of either that has never
// heard of test states. A claim without a done task and what proved it is
// refused whoever writes it, and so is a claim newly made while a finding of
// the task is open; one that already stood is not, so clearing e2e alone still
// works. Whatever changes the story a test proved clears it, keeping what it had
// in a note. None of them refuses a status change, so an older copy moving a
// tested task still works and leaves no stale claim behind. The blank set in
// the guards is BLANK's, by code point. The text is the same, character for
// character, in todo.py.
const TEST_STATE_TRIGGERS = [
  `CREATE TRIGGER IF NOT EXISTS todo_test_claim BEFORE UPDATE OF tested, tested_how, e2e_tested, e2e_how ON task
WHEN NEW.tested NOT IN (0, 1) OR NEW.e2e_tested NOT IN (0, 1)
  OR (NEW.tested = 1 AND (NEW.status IS NOT 'done' OR trim(coalesce(NEW.tested_how, ''), char(9, 10, 11, 12, 13, 28, 29, 30, 31, 32, 133, 160, 5760, 8192, 8193, 8194, 8195, 8196, 8197, 8198, 8199, 8200, 8201, 8202, 8232, 8233, 8239, 8287, 12288, 65279)) = ''))
  OR (NEW.e2e_tested = 1 AND (NEW.tested IS NOT 1 OR trim(coalesce(NEW.e2e_how, ''), char(9, 10, 11, 12, 13, 28, 29, 30, 31, 32, 133, 160, 5760, 8192, 8193, 8194, 8195, 8196, 8197, 8198, 8199, 8200, 8201, 8202, 8232, 8233, 8239, 8287, 12288, 65279)) = ''))
  OR (((NEW.tested = 1 AND OLD.tested IS NOT 1) OR (NEW.e2e_tested = 1 AND OLD.e2e_tested IS NOT 1))
    AND EXISTS (SELECT 1 FROM task WHERE parent_task = NEW.id AND status NOT IN ('done', 'dropped')))
BEGIN
  SELECT RAISE(ABORT, 'a test state needs a done task with no open finding and what proved it, and e2e needs tested first');
END`,
  `CREATE TRIGGER IF NOT EXISTS todo_test_claim_insert BEFORE INSERT ON task
WHEN NEW.tested NOT IN (0, 1) OR NEW.e2e_tested NOT IN (0, 1)
  OR (NEW.tested = 1 AND (NEW.status IS NOT 'done' OR trim(coalesce(NEW.tested_how, ''), char(9, 10, 11, 12, 13, 28, 29, 30, 31, 32, 133, 160, 5760, 8192, 8193, 8194, 8195, 8196, 8197, 8198, 8199, 8200, 8201, 8202, 8232, 8233, 8239, 8287, 12288, 65279)) = ''))
  OR (NEW.e2e_tested = 1 AND (NEW.tested IS NOT 1 OR trim(coalesce(NEW.e2e_how, ''), char(9, 10, 11, 12, 13, 28, 29, 30, 31, 32, 133, 160, 5760, 8192, 8193, 8194, 8195, 8196, 8197, 8198, 8199, 8200, 8201, 8202, 8232, 8233, 8239, 8287, 12288, 65279)) = ''))
  OR ((NEW.tested = 1 OR NEW.e2e_tested = 1)
    AND EXISTS (SELECT 1 FROM task WHERE parent_task = NEW.id AND status NOT IN ('done', 'dropped')))
BEGIN
  SELECT RAISE(ABORT, 'a test state needs a done task with no open finding and what proved it, and e2e needs tested first');
END`,
  `CREATE TRIGGER IF NOT EXISTS todo_test_status_clears AFTER UPDATE OF status ON task
WHEN OLD.status IS NOT NEW.status
  AND EXISTS (SELECT 1 FROM task WHERE id = NEW.id AND (tested IS NOT 0 OR e2e_tested IS NOT 0))
BEGIN
  INSERT INTO note (task, at, text)
    SELECT id, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'test state cleared: status ' || OLD.status || ' -> ' || NEW.status || '; it had '
      || CASE WHEN tested = 1 THEN 'tested: ' || coalesce(tested_how, '') ELSE 'not tested' END
      || CASE WHEN e2e_tested = 1 THEN '; e2e tested: ' || coalesce(e2e_how, '') ELSE '' END
    FROM task WHERE id = NEW.id;
  UPDATE task SET tested = 0, tested_how = NULL, tested_at = NULL, e2e_tested = 0, e2e_how = NULL, e2e_at = NULL
    WHERE id = NEW.id;
END`,
  `CREATE TRIGGER IF NOT EXISTS todo_test_story_clears AFTER UPDATE OF descr, exit_cond ON task
WHEN (OLD.descr IS NOT NEW.descr OR OLD.exit_cond IS NOT NEW.exit_cond)
  AND EXISTS (SELECT 1 FROM task WHERE id = NEW.id AND (tested IS NOT 0 OR e2e_tested IS NOT 0))
BEGIN
  INSERT INTO note (task, at, text)
    SELECT id, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'test state cleared: '
      || CASE WHEN OLD.descr IS NOT NEW.descr AND OLD.exit_cond IS NOT NEW.exit_cond THEN 'its description and exit condition changed'
        WHEN OLD.descr IS NOT NEW.descr THEN 'its description changed' ELSE 'its exit condition changed' END
      || '; it had '
      || CASE WHEN tested = 1 THEN 'tested: ' || coalesce(tested_how, '') ELSE 'not tested' END
      || CASE WHEN e2e_tested = 1 THEN '; e2e tested: ' || coalesce(e2e_how, '') ELSE '' END
    FROM task WHERE id = NEW.id;
  UPDATE task SET tested = 0, tested_how = NULL, tested_at = NULL, e2e_tested = 0, e2e_how = NULL, e2e_at = NULL
    WHERE id = NEW.id;
END`,
  `CREATE TRIGGER IF NOT EXISTS todo_test_finding_added AFTER INSERT ON task
WHEN NEW.parent_task IS NOT NULL AND NEW.status NOT IN ('done', 'dropped')
  AND EXISTS (SELECT 1 FROM task WHERE id = NEW.parent_task AND (tested IS NOT 0 OR e2e_tested IS NOT 0))
BEGIN
  INSERT INTO note (task, at, text)
    SELECT id, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'test state cleared: ' || NEW.id || ' is an open finding of it; it had '
      || CASE WHEN tested = 1 THEN 'tested: ' || coalesce(tested_how, '') ELSE 'not tested' END
      || CASE WHEN e2e_tested = 1 THEN '; e2e tested: ' || coalesce(e2e_how, '') ELSE '' END
    FROM task WHERE id = NEW.parent_task;
  UPDATE task SET tested = 0, tested_how = NULL, tested_at = NULL, e2e_tested = 0, e2e_how = NULL, e2e_at = NULL
    WHERE id = NEW.parent_task;
END`,
  `CREATE TRIGGER IF NOT EXISTS todo_test_finding_reopened AFTER UPDATE OF status ON task
WHEN NEW.parent_task IS NOT NULL AND OLD.status IN ('done', 'dropped') AND NEW.status NOT IN ('done', 'dropped')
  AND EXISTS (SELECT 1 FROM task WHERE id = NEW.parent_task AND (tested IS NOT 0 OR e2e_tested IS NOT 0))
BEGIN
  INSERT INTO note (task, at, text)
    SELECT id, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'test state cleared: ' || NEW.id || ', a finding of it, is open again; it had '
      || CASE WHEN tested = 1 THEN 'tested: ' || coalesce(tested_how, '') ELSE 'not tested' END
      || CASE WHEN e2e_tested = 1 THEN '; e2e tested: ' || coalesce(e2e_how, '') ELSE '' END
    FROM task WHERE id = NEW.parent_task;
  UPDATE task SET tested = 0, tested_how = NULL, tested_at = NULL, e2e_tested = 0, e2e_how = NULL, e2e_at = NULL
    WHERE id = NEW.parent_task;
END`,
  `CREATE TRIGGER IF NOT EXISTS todo_test_finding_attached AFTER UPDATE OF parent_task ON task
WHEN NEW.parent_task IS NOT NULL AND OLD.parent_task IS NOT NEW.parent_task AND NEW.status NOT IN ('done', 'dropped')
  AND EXISTS (SELECT 1 FROM task WHERE id = NEW.parent_task AND (tested IS NOT 0 OR e2e_tested IS NOT 0))
BEGIN
  INSERT INTO note (task, at, text)
    SELECT id, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'test state cleared: ' || NEW.id || ' was filed under it as an open finding; it had '
      || CASE WHEN tested = 1 THEN 'tested: ' || coalesce(tested_how, '') ELSE 'not tested' END
      || CASE WHEN e2e_tested = 1 THEN '; e2e tested: ' || coalesce(e2e_how, '') ELSE '' END
    FROM task WHERE id = NEW.parent_task;
  UPDATE task SET tested = 0, tested_how = NULL, tested_at = NULL, e2e_tested = 0, e2e_how = NULL, e2e_at = NULL
    WHERE id = NEW.parent_task;
END`,
]
const TEST_STATE_TRIGGER_NAMES = [
  'todo_test_claim',
  'todo_test_claim_insert',
  'todo_test_status_clears',
  'todo_test_story_clears',
  'todo_test_finding_added',
  'todo_test_finding_reopened',
  'todo_test_finding_attached',
]

db.exec(`
  CREATE TABLE IF NOT EXISTS task (
    id        TEXT PRIMARY KEY,
    title     TEXT NOT NULL,
    descr     TEXT NOT NULL,
    why       TEXT NOT NULL,
    severity  TEXT NOT NULL CHECK (severity IN ('critical','high','medium','low')),
    points    INTEGER NOT NULL CHECK (points IN (1,2,3,5,8,13)),
    status    TEXT NOT NULL CHECK (status IN ('backlog','in_progress','wait_for_roast','done','dropped')),
    exit_cond TEXT NOT NULL,
    area      TEXT,
    created   TEXT NOT NULL DEFAULT (datetime('now'))
  );
  CREATE TABLE IF NOT EXISTS blocked_by (
    task    TEXT NOT NULL REFERENCES task(id) ON DELETE CASCADE,
    parent  TEXT NOT NULL,
    PRIMARY KEY (task, parent)
  );
`)

// A board created before `area` existed does not get the column from CREATE
// TABLE IF NOT EXISTS, which does nothing at all when the table is already
// there. Add it separately, and ignore the error when it is already present:
// SQLite has no ADD COLUMN IF NOT EXISTS.
try {
  db.exec('ALTER TABLE task ADD COLUMN area TEXT')
} catch {
  // already there
}

/**
 * The task this one came OUT of, which is not the same thing as `blocked_by`.
 *
 * `blocked_by` is a dependency: this cannot start until that is finished.
 * `parent_task` is provenance: this exists BECAUSE that task was roasted and
 * the roast found something. A finding is not a blocker, it is a piece of the
 * same job discovered late, and keeping the two apart is what lets the board
 * answer "everything that came out of SB-014 is finished, so look at SB-014 as
 * a whole again".
 *
 * One level, deliberately. A child never gets children of its own; anything
 * found while doing a child belongs to the same parent. The question this
 * grouping exists to answer is "is that parent finished yet", and a tree makes
 * it recursive for no gain.
 */
try {
  db.exec('ALTER TABLE task ADD COLUMN parent_task TEXT')
} catch {
  // already there
}

/**
 * Phases, the owner's of 2026-09-11: the board's tasks belong to a goal, and
 * the first goal here is the MVP. A phase is that goal with an order, so the
 * board can say what is left before the thing ships rather than only what is
 * left in total.
 *
 * The CURRENT phase is the first one still open. `next` works through it
 * before it offers anything from a later one, and a task with no phase counts
 * as current work: an unsorted card is something to do now, and the opposite
 * reading would hide it behind every later phase.
 *
 * A board with no phases behaves exactly as it did, which matters because this
 * skill is shared and another project's board has none.
 */
db.exec(`
  CREATE TABLE IF NOT EXISTS phase (
    name     TEXT PRIMARY KEY,
    goal     TEXT NOT NULL,
    position INTEGER NOT NULL,
    status   TEXT NOT NULL CHECK (status IN ('open','done')) DEFAULT 'open'
  );
`)
try {
  db.exec('ALTER TABLE task ADD COLUMN phase TEXT')
} catch {
  // already there
}

/**
 * What an older JSON board tool carried and this one did not: an
 * objective's name, a block and its reason, notes, what closed a task, and the
 * roast rounds with what their adjudication filed.
 *
 * Every one is a new table or a new column, never a changed one, because every
 * project's board opens with this code: a board made before them gains them on
 * its first run and prints exactly what it printed until something uses them.
 * A block is a row of its own rather than a status for the same reason: the
 * task table's status CHECK cannot be widened without rebuilding the table on
 * every board that already exists.
 */
for (const statement of [
  'ALTER TABLE phase ADD COLUMN label TEXT',
  'ALTER TABLE task ADD COLUMN evidence TEXT',
  'ALTER TABLE task ADD COLUMN closed TEXT',
  'ALTER TABLE task ADD COLUMN reason TEXT',
  'ALTER TABLE task ADD COLUMN updated TEXT',
]) {
  try {
    db.exec(statement)
  } catch {
    // already there
  }
}
db.exec(`
  CREATE TABLE IF NOT EXISTS blocked (
    task   TEXT PRIMARY KEY REFERENCES task(id) ON DELETE CASCADE,
    reason TEXT NOT NULL,
    since  TEXT NOT NULL
  );
  CREATE TABLE IF NOT EXISTS note (
    task TEXT NOT NULL REFERENCES task(id) ON DELETE CASCADE,
    at   TEXT NOT NULL,
    text TEXT NOT NULL
  );
  CREATE TABLE IF NOT EXISTS roast (
    task               TEXT NOT NULL REFERENCES task(id) ON DELETE CASCADE,
    round              INTEGER NOT NULL,
    at                 TEXT NOT NULL,
    file               TEXT NOT NULL,
    score              REAL,
    criticals          INTEGER,
    filed              TEXT,
    dismissed          TEXT,
    reviewer_score     REAL,
    reviewer_criticals INTEGER,
    head               TEXT,
    card_digest        TEXT,
    legacy             TEXT,
    PRIMARY KEY (task, round)
  );
`)

const fail = (message) => {
  console.error(message)
  process.exit(1)
}

/** A moment, the way every new column records one. */
const now = () => new Date().toISOString()

const taskColumns = () => db.prepare('PRAGMA table_info(task)').all().map((column) => column.name)

/** Whether this board records test states at all. One that never did prints what it always printed. */
const hasTestStates = () => {
  const present = taskColumns()
  return TEST_STATE_COLUMNS.every(([name]) => present.includes(name))
}

const recordsTests = (task) => TEST_STATE_COLUMNS.every(([name]) => Object.hasOwn(task, name))

const isTested = (task) => Boolean(task.tested || task.e2e_tested)

/** Whether a task holds a test state now, read around a change that may clear it. */
const testedTask = (id) => {
  const row = id ? db.prepare('SELECT * FROM task WHERE id = ?').get(id) : undefined
  return row !== undefined && isTested(row)
}

/** What a test state held, in the words the board's own guards use for it. */
const had = (task) =>
  (task.tested === 1 ? `tested: ${task.tested_how ?? ''}` : 'not tested') + (task.e2e_tested === 1 ? `; e2e tested: ${task.e2e_how ?? ''}` : '')

const testWord = (task, flag, how) => (task[flag] === 1 ? `yes, ${task[how]}` : 'no')

/**
 * Give this board whatever test-state columns and guards it lacks, inside the
 * caller's transaction. Only a `tested` that has passed every check calls this.
 * Opening a board, reading one or a refused claim never does, so an old board
 * keeps its bytes until something on it is actually tested. Returns whether
 * columns were added.
 */
const startTestStates = () => {
  const present = taskColumns()
  const missing = TEST_STATE_COLUMNS.filter(([name]) => !present.includes(name))
  for (const [name, kind] of missing) db.exec(`ALTER TABLE task ADD COLUMN ${name} ${kind}`)
  for (const statement of TEST_STATE_TRIGGERS) db.exec(statement)
  return missing.length > 0
}

/** Text without BLANK at either end, as Python's strip(BLANK) leaves it. */
const stripBlank = (text) => {
  let start = 0
  let end = text.length
  while (start < end && BLANK.includes(text[start])) start++
  while (end > start && BLANK.includes(text[end - 1])) end--
  return text.slice(start, end)
}

/**
 * Roll back what this command began, close the board, and fail in the tool's own
 * words. `open` is whether the command's own BEGIN IMMEDIATE went through: the
 * command keeps track itself, as not every Node this tool runs on can ask.
 *
 * SQLite ends a transaction itself on some errors (a full disk, an I/O error, an
 * interrupt, RAISE(ROLLBACK)), and then ROLLBACK fails for want of one. Only that
 * one statement's error is set aside: `message` already holds the error that
 * stopped the command, which is the one to report, and the board is closed once.
 */
const giveUp = (message, open) => {
  if (open) {
    try {
      db.exec('ROLLBACK')
    } catch {
      // nothing left to roll back; the original error is reported below
    }
  }
  db.close()
  fail(message)
}

/**
 * Why `id` cannot be claimed tested, or e2e tested, now; null when it can.
 * Asked once, inside the claim's own BEGIN IMMEDIATE, so nothing another
 * session writes can land between the check and the claim: a finding filed a
 * moment earlier is seen, and one filed a moment later waits for the claim.
 */
const claimProblem = (id, command) => {
  const task = db.prepare('SELECT * FROM task WHERE id = ?').get(id)
  if (!task) return `No task ${id}.`
  if (task.status !== 'done') return `${id} is ${task.status}, not done. Nothing was recorded.`
  const reason = blockOf(id)
  if (reason !== null) return `${id} is blocked: ${reason}. Nothing was recorded.`
  const stillOpen = openChildren(id)
  if (stillOpen.length) return `${id} has open findings: ${stillOpen.map((child) => child.id).join(', ')}. Nothing was recorded.`
  if (command === 'e2e' && task.tested !== 1) return `${id} is not tested yet, and e2e comes after it. Nothing was recorded.`
  return null
}

const allPhases = () => db.prepare('SELECT * FROM phase ORDER BY position, name').all()

/** The first phase still open: what the board is working on now. */
const currentPhase = () => allPhases().find((phase) => phase.status === 'open') ?? null

/**
 * Where a task sits in the order of phases. The current phase and no phase at
 * all are both now; a phase already closed is behind us, so a task left in one
 * is now as well; anything later sorts by its position.
 */
const phaseRank = (task) => {
  const phases = allPhases()
  if (!phases.length) return 0
  const current = currentPhase()
  const mine = phases.find((phase) => phase.name === task.phase)
  if (!mine || mine.status === 'done') return 0
  return mine.position <= (current?.position ?? mine.position) ? 0 : mine.position
}

const phaseExists = (name) => Boolean(db.prepare('SELECT 1 FROM phase WHERE name = ?').get(name))

const parents = (id) => db.prepare('SELECT parent FROM blocked_by WHERE task = ?').all(id).map((row) => row.parent)

const withParents = (task) => ({ ...task, parents: parents(task.id) })

const all = () => db.prepare('SELECT * FROM task ORDER BY id').all().map(withParents)

const one = (id) => {
  const task = db.prepare('SELECT * FROM task WHERE id = ?').get(id)
  if (!task) fail(`No task ${id}.`)
  return withParents(task)
}

/**
 * Why a task is blocked, or null when it is not. The one question `next`,
 * `list`, `show`, `render` and `validate` all ask, so they all ask it here: a
 * block lives beside the status, which keeps reading backlog, and a reader that
 * looked only at the status would hand a blocked task out.
 */
const blockOf = (id) => db.prepare('SELECT reason FROM blocked WHERE task = ?').get(id)?.reason ?? null
const isBlocked = (task) => blockOf(task.id) !== null

const notesOf = (id) => db.prepare('SELECT text FROM note WHERE task = ? ORDER BY rowid').all(id).map((row) => row.text)

const roastsOf = (id) => db.prepare('SELECT * FROM roast WHERE task = ? ORDER BY round').all(id)

/** Everything that came out of roasting this task. */
const children = (id) => db.prepare('SELECT * FROM task WHERE parent_task = ? ORDER BY id').all(id)

/** The children that are still going to need doing. */
const openChildren = (id) => children(id).filter((task) => !['done', 'dropped'].includes(task.status))

/**
 * Keeps the tree one level deep without refusing anything.
 *
 * Naming a child as a parent attaches to that child's parent instead, and says
 * so. Refusing would be a gate, and the owner's rule is that the tool does not
 * stop you; flattening gets the same shape and tells you what it did.
 */
const resolveParent = (wanted) => {
  if (!wanted) return null
  const parent = db.prepare('SELECT id, parent_task FROM task WHERE id = ?').get(wanted)
  if (!parent) fail(`No task ${wanted} to hang this off.`)
  if (!parent.parent_task) return parent.id
  console.log(`${wanted} is itself a child of ${parent.parent_task}, so this hangs off ${parent.parent_task}. One level.`)
  return parent.parent_task
}

/** An id split into what comes before the ASCII digits it ends with, and those digits ('' when it ends otherwise). */
const trailingDigits = (id) => {
  const text = String(id)
  let start = text.length
  while (start > 0 && text[start - 1] >= '0' && text[start - 1] <= '9') start--
  return { prefix: text.slice(0, start), digits: text.slice(start) }
}

/** A digit string plus one, in decimal, without its leading zeros. */
const oneMore = (digits) => {
  const out = []
  let carry = 1
  for (const character of [...(digits.replace(/^0+/, '') || '0')].reverse()) {
    const value = character.charCodeAt(0) - 48 + carry
    out.push(String.fromCharCode(48 + (value % 10)))
    carry = Math.floor(value / 10)
  }
  if (carry) out.push('1')
  return out.reverse().join('')
}

/**
 * The id after the highest one, in the board's own prefix.
 *
 * It used to be one fixed prefix whatever the board was, so a plain `add` on a
 * board with another prefix got the wrong one. An empty board has no prefix to keep, so it takes the capitals of the
 * project folder's name, or its first two letters when it has fewer than two.
 *
 * The number is the run of ASCII digits the id ends with, worked as a digit
 * string and never converted: Number() rounded past 2**53, so the id after
 * T-9007199254740993 was one that already existed.
 */
const nextId = () => {
  let best = null
  for (const row of db.prepare('SELECT id FROM task ORDER BY rowid').all()) {
    const { prefix, digits } = trailingDigits(row.id)
    if (digits && (best === null || byIdNumber(digits, best.digits) > 0)) best = { prefix, digits }
  }
  if (best) return `${best.prefix}${oneMore(best.digits).padStart(3, '0')}`
  const project = dirname(dirname(BOARD)).split(/[\\/]/).pop()
  const capitals = (project.match(/[A-Z]/g) ?? []).join('')
  const prefix = capitals.length >= 2 ? capitals : project.slice(0, 2).toUpperCase()
  return `${prefix}-001`
}

// Pairs only, as the Python half reads them: a flag at the end with no value is
// passed over, so it can neither clear a field nor count as a change. Only an
// explicit empty value, `--parent ""`, clears.
const flags = (args) => {
  const out = {}
  for (let i = 0; i + 1 < args.length; i += 2) out[args[i].replace(/^--/, '')] = args[i + 1]
  return out
}

/**
 * The value after a flag, wherever it sits. `flags` reads pairs, so a flag that
 * takes no value, `--force` or `--check`, shifts every pair after it by one and
 * the next flag's value is lost. Commands that have one read their values here.
 */
const valueOf = (list, name) => {
  const at = list.indexOf(`--${name}`)
  return at === -1 ? undefined : list[at + 1]
}

/**
 * The areas a session works in, from `--area`: one name or a comma list. null
 * means every area: no `--area`, an empty one, or `all` among the names.
 * `unset` names the tasks that have no area, as `list` prints them.
 */
const areaFilter = (value) => {
  if (value === undefined || value === null) return null
  const names = String(value).split(',').map((name) => name.trim()).filter(Boolean)
  return !names.length || names.includes('all') ? null : names
}
const inAreas = (task, areas) => areas === null || areas.includes(task.area || 'unset')
/** This session's loop and its areas, or nulls when it has no loop file or no areas. */
const loopAreas = () => {
  const loop = sessionLoop(process.cwd())
  return loop ? { loop, mine: areaFilter(loop.fields.areas) } : { loop: null, mine: null }
}
/** The areas `next` picks from: the session's loop areas, always, narrowed by `--area`. */
const areasFor = (asked) => {
  const wanted = areaFilter(asked)
  const { loop, mine } = loopAreas()
  if (mine === null) return wanted
  if (wanted === null) return mine
  const outside = wanted.filter((name) => !mine.includes(name))
  if (outside.length) fail(`${outside.join(', ')}: not one of this session's areas (${mine.join(', ')}), set in ${loop.file}.`)
  return wanted
}

/** A number from a flag, or null when it is not one: Number('') is 0, which is not what was typed. */
const numberFrom = (value) => {
  if (value === undefined || String(value).trim() === '') return null
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

const roundLine = (round) =>
  `    round ${round.round}: ${round.file}` +
  (round.score === null ? '' : `, score ${round.score}`) +
  (round.criticals === null ? '' : `, ${round.criticals} critical(s)`) +
  (round.filed === null ? ', not judged yet' : round.filed === '' ? ', filed none' : `, filed ${round.filed}`) +
  (round.dismissed ? `, dismissed: ${round.dismissed}` : '')

// Keep stored ranking fields readable, with the same labels in both ports: NULL, a BLOB as
// <non-text> (severity) or <non-number> (points), and a number as JavaScript prints it (an
// integral legacy REAL as the integer, which the Python half matches up to 2**53).
const rankField = (value, kind) =>
  value === null ? 'NULL' : value instanceof Uint8Array ? (kind === 'severity' ? '<non-text>' : '<non-number>') : value
const card = (task) => {
  const label = task.phase ? allPhases().find((phase) => phase.name === task.phase)?.label : null
  const reason = blockOf(task.id)
  const notes = notesOf(task.id)
  const rounds = roastsOf(task.id)
  return [
    `${task.id}  [${rankField(task.severity, 'severity')}/${rankField(task.points, 'points')}pt]  ${task.status}${task.area ? `  ${task.area}` : ''}`,
    `  ${task.title}`,
    '',
    `  area : ${task.area ?? 'unset'}`,
    ...(allPhases().length ? [`  phase: ${task.phase ?? 'unset, so it counts as now'}${label ? ` ${label}` : ''}`] : []),
    `  desc : ${task.descr}`,
    `  why  : ${task.why}`,
    `  exit : ${task.exit_cond}`,
    task.parents.length ? `  after: ${task.parents.join(', ')}` : '  after: nothing',
    // Provenance, shown separately from blockers, because they mean different
    // things and reading a finding as a blocker is how a closed parent starts
    // holding its own findings hostage.
    ...(task.parent_task ? [`  from : ${task.parent_task}, which was roasted and turned this up`] : []),
    ...(() => {
      const own = children(task.id)
      if (!own.length) return []
      const open = own.filter((child) => !['done', 'dropped'].includes(child.status))
      return [
        `  parts: ${own.map((child) => child.id).join(', ')}`,
        open.length
          ? `         ${open.length} still open, so this is not finished yet`
          : '         all finished, so roast this task together with them',
      ]
    })(),
    ...(reason === null ? [] : [`  blocked: ${reason}`]),
    ...(task.evidence ? [`  evidence: ${task.evidence}`] : []),
    ...(recordsTests(task) ? [`  tested: ${testWord(task, 'tested', 'tested_how')}`, `  e2e   : ${testWord(task, 'e2e_tested', 'e2e_how')}`] : []),
    ...(task.reason ? [`  dropped: ${task.reason}`] : []),
    ...(notes.length ? ['  notes:', ...notes.map((text) => `    - ${text}`)] : []),
    ...(rounds.length ? ['  roasts:', ...rounds.map(roundLine)] : []),
  ].join('\n')
}

// `command` and `args` are parsed at the top now, before the board is opened.

/** What each phase has left, which is the only number an objective is read for. */
const phaseReport = () => {
  const phases = allPhases()
  if (!phases.length) return []
  const tasks = all()
  const current = currentPhase()
  const lines = ['PHASES']
  for (const phase of phases) {
    const mine = tasks.filter((task) => task.phase === phase.name)
    const open = mine.filter((task) => !['done', 'dropped'].includes(task.status)).length
    const done = mine.filter((task) => task.status === 'done').length
    const mark = phase.status === 'done' ? 'finished' : phase.name === current?.name ? 'now' : 'later'
    lines.push(`  ${phase.position}. ${phase.name} (${mark}): ${open} open, ${done} done`)
    lines.push(`     ${phase.goal}`)
  }
  const loose = tasks.filter((task) => !task.phase && !['done', 'dropped'].includes(task.status)).length
  if (loose) lines.push(`  ${loose} open task(s) in no phase, which counts as now.`)
  return lines
}

/**
 * The objectives, the owner's word for phases, 2026-09-12: the same rows, each
 * with the name an older board gave it, what is left in it, and the order they
 * are met in.
 */
const okrReport = () => {
  const phases = allPhases()
  if (!phases.length) return ['No objectives yet. Add one: todo okr add --name "..." --description "..."']
  const tasks = all()
  const current = currentPhase()
  const lines = []
  for (const phase of phases) {
    const mine = tasks.filter((task) => task.phase === phase.name)
    const open = mine.filter((task) => !['done', 'dropped'].includes(task.status)).length
    const done = mine.filter((task) => task.status === 'done').length
    const state = phase.status === 'done' ? 'met' : phase.name === current?.name ? 'now' : 'later'
    lines.push(`${phase.position}. ${phase.name}${phase.label ? ` ${phase.label}` : ''} (${state}): ${open} open, ${done} done, ${mine.length} in all`)
    lines.push(`   ${phase.goal}`)
  }
  const loose = tasks.filter((task) => !task.phase && !['done', 'dropped'].includes(task.status)).length
  if (loose) lines.push(`${loose} open task(s) serve no objective, which counts as now.`)
  return lines
}

// Ids sort numerically, not as text. A text order puts SB-1000 before SB-999,
// which is correct today and wrong from the thousandth task: the worst kind of
// bug, invisible until a boundary and then quietly reordering the work.
//
// The number is every ASCII digit of the id, joined, compared as a digit string
// and never converted, so it is the exact integer order at any length, the
// order the Python half gives. Converting with Number() rounded past 2**53, read
// SB-1e3 as a thousand, and took only the digits after the leading letters.
const idNumber = (id) => String(id).replace(/[^0-9]/g, '').replace(/^0+/, '')
const byIdNumber = (a, b) => {
  const x = idNumber(a), y = idNumber(b)
  return x.length - y.length || (x < y ? -1 : x > y ? 1 : 0)
}
// The last tie is by code point, as Python compares strings: UTF-8 bytes sort in
// code point order. localeCompare ordered case and accents by locale, and a plain
// < compares UTF-16 units, which puts a character beyond U+FFFF too early.
const byCodePoint = (a, b) => Buffer.compare(Buffer.from(a), Buffer.from(b))

class UnrankableSeverity extends Error {}
class UnrankablePoints extends Error {}
const severityProblem = (task) => SEVERITIES.includes(task.severity) ? null :
  `${task.id}: its severity ${typeof task.severity === 'string' ? task.severity : task.severity == null ? 'NULL' : '<non-text>'} is not one of ${SEVERITIES.join(', ')}`
const pointsProblem = (task) => {
  const numeric = typeof task.points === 'number'
  const label = typeof task.points === 'string' ? task.points : task.points === null ? 'NULL' : numeric ? '<unsupported number>' : '<non-number>'
  return numeric && POINTS.includes(task.points) ? null :
    `${task.id}: its points ${label} is not one of ${POINTS.join(', ')}`
}
const severityRank = (task) => {
  const problem = severityProblem(task)
  if (problem !== null) throw new UnrankableSeverity(problem)
  return SEVERITIES.indexOf(task.severity)
}
// Validate in stored id order before sorting: even singleton collections and
// phase ties that short-circuit the comparator must refuse unsupported fields.
const ranked = (tasks, compare) => {
  for (const task of tasks) {
    severityRank(task)
    const problem = pointsProblem(task)
    if (problem !== null) throw new UnrankablePoints(problem)
  }
  return tasks.sort(compare)
}
const rankedCall = (action) => {
  try { return action() } catch (error) {
    if (!(error instanceof UnrankableSeverity) && !(error instanceof UnrankablePoints)) throw error
    fail(error.message)
  }
}

// The phase comes first: what the product needs to ship is picked before what
// comes after it, however severe the later one is.
const byRule = (a, b) =>
  phaseRank(a) - phaseRank(b) ||
  SEVERITIES.indexOf(a.severity) - SEVERITIES.indexOf(b.severity) ||
  a.points - b.points ||
  byIdNumber(a.id, b.id) ||
  byCodePoint(a.id, b.id)

/** Whether every parent of a task is done: what eligibility, and resuming started work, both wait for. */
const parentsDone = (task, done) => task.parents.every((parent) => done.has(parent))

/**
 * What `next` would pick, for `next` and for the rendered board alike.
 *
 * Work in flight is finished before anything new starts, and among several
 * started tasks the same rule decides which. Taking the first by id meant a low
 * severity task begun earlier beat a critical one. A blocked task is neither,
 * and neither is a started task one of whose parents is unfinished: a parent
 * added after it started means it cannot go on until that parent is done.
 */
const choose = (tasks, areas = null) => {
  const done = new Set(tasks.filter((task) => task.status === 'done').map((task) => task.id))
  const started = ranked(tasks
    .filter((task) => (task.status === 'in_progress' || task.status === 'wait_for_roast') && !isBlocked(task) && inAreas(task, areas))
    .filter((task) => parentsDone(task, done)), byRule)
  const eligible = ranked(tasks
    .filter((task) => task.status === 'backlog' && inAreas(task, areas) && !isBlocked(task))
    .filter((task) => parentsDone(task, done)), byRule)
  return { started, pick: started[0] ?? eligible[0] }
}

/** Each parent of a task that is not done, with its status, or saying it is not on the board. */
const unfinishedParents = (task, tasks) => {
  const status = new Map(tasks.map((other) => [other.id, other.status]))
  return [...task.parents]
    .sort(byCodePoint)
    .filter((parent) => status.get(parent) !== 'done')
    .map((parent) => (status.has(parent) ? `${parent} (${status.get(parent)})` : `${parent}, which is not on this board`))
}

/** The started, unblocked tasks `choose` passes over because a parent is unfinished, in its order. */
const startedButWaiting = (tasks, areas = null) =>
  ranked(tasks
    .filter((task) => (task.status === 'in_progress' || task.status === 'wait_for_roast') && !isBlocked(task) && inAreas(task, areas))
    .filter((task) => unfinishedParents(task, tasks).length), byRule)

/** What `next` says about started work it does not resume, and why: a parent dropped or gone never becomes done. */
const sayHeld = (held, tasks) => {
  for (const task of held) {
    console.log(`  ${task.id} is ${task.status} and waits on ${unfinishedParents(task, tasks).join(', ')}; next does not resume it until every parent is done.`)
  }
}

const escapeCell = (text) => String(text ?? '').replace(/\|/g, '\\|').replace(/\r?\n/g, ' ')

const bySeverity = (a, b) =>
  SEVERITIES.indexOf(a.severity) - SEVERITIES.indexOf(b.severity) || a.points - b.points || byIdNumber(a.id, b.id) || byCodePoint(a.id, b.id)

/** The board as Markdown, which is what a person reads and what a diff of it shows. */
const renderBoard = () => {
  const tasks = all()
  const project = dirname(dirname(BOARD)).split(/[\\/]/).pop()
  const done = tasks.filter((task) => task.status === 'done')
  // An unranked old status may still be displayed. Never coerce its invalid
  // points into a total. Numeric values remain summable independently of their
  // ranking eligibility; actual ranked candidates refuse in their group order.
  const points = tasks.every((task) => typeof task.points === 'number') ? tasks.reduce((sum, task) => sum + task.points, 0) : 'unknown'
  const donePoints = done.every((task) => typeof task.points === 'number') ? done.reduce((sum, task) => sum + task.points, 0) : 'unknown'
  const out = [
    '# Board',
    '',
    '<!-- GENERATED by the todo skill from .claude/todo.db. Change the board through the skill, never this file. -->',
    '',
    `Project **${project}** · ${done.length} of ${tasks.length} tasks done · ${donePoints} of ${points} points.`,
    '',
  ]
  const { pick } = choose(tasks)
  out.push(
    pick ? `**Next up: \`${pick.id}\` ${pick.title}** (${pick.severity}, ${pick.points} pt${pick.area ? `, ${pick.area}` : ''})` : '**Nothing is pickable.**',
    '',
  )

  const phases = allPhases()
  if (phases.length) {
    const current = currentPhase()
    out.push('## Objectives', '', '| position | id | name | state | open | done |', '| -- | -- | ---- | ----- | ---- | ---- |')
    for (const phase of phases) {
      const mine = tasks.filter((task) => task.phase === phase.name)
      const open = mine.filter((task) => !['done', 'dropped'].includes(task.status)).length
      const closed = mine.filter((task) => task.status === 'done').length
      const state = phase.status === 'done' ? 'met' : phase.name === current?.name ? 'now' : 'later'
      out.push(`| ${phase.position} | ${escapeCell(phase.name)} | ${escapeCell(phase.label)} | ${state} | ${open} | ${closed} |`)
    }
    out.push('')
  }

  const columns = [
    ['in_progress', 'In progress'],
    ['wait_for_roast', 'Waiting for a roast'],
    ['blocked', 'Blocked'],
    ['backlog', 'Backlog'],
    ['done', 'Done'],
    ['dropped', 'Dropped'],
  ]
  for (const [key, heading] of columns) {
    const column = ranked(tasks.filter((task) => (key === 'blocked' ? isBlocked(task) : task.status === key && !isBlocked(task))), bySeverity)
    if (!column.length) continue
    out.push(`## ${heading} (${column.length})`, '')
    out.push('| id | title | sev | pt | area | blocked by | exit condition |')
    out.push('| -- | ----- | --- | -- | ---- | ---------- | -------------- |')
    for (const task of column) {
      out.push(
        `| \`${task.id}\` | ${escapeCell(task.title)} | ${task.severity} | ${task.points} | ${escapeCell(task.area)} | ${task.parents.join(', ') || 'none'} | ${escapeCell(task.exit_cond)} |`,
      )
    }
    out.push('')
  }

  out.push('## Cards', '')
  for (const task of tasks) {
    out.push(`### \`${task.id}\` ${task.title}`, '')
    out.push(
      `- **status** ${task.status} · **severity** ${rankField(task.severity, 'severity')} · **points** ${rankField(task.points, 'points')} · **area** ${task.area ?? 'unset'}${task.phase ? ` · **objective** ${task.phase}` : ''}`,
    )
    out.push(`- **blocked by** ${task.parents.join(', ') || 'none'}`)
    const reason = blockOf(task.id)
    if (reason !== null) out.push(`- **blocked** ${reason}`)
    if (task.parent_task) out.push(`- **came out of** ${task.parent_task}`)
    out.push('', task.descr, '', `**Why.** ${task.why}`, '', `**Exit condition.** ${task.exit_cond}`, '')
    if (task.evidence) out.push(`**Evidence.** ${task.evidence}`, '')
    if (recordsTests(task)) {
      out.push(`**Tested.** ${testWord(task, 'tested', 'tested_how')}`, '')
      out.push(`**E2E tested.** ${testWord(task, 'e2e_tested', 'e2e_how')}`, '')
    }
    if (task.reason) out.push(`**Dropped because.** ${task.reason}`, '')
    const notes = notesOf(task.id)
    if (notes.length) out.push('**Notes.**', '', ...notes.map((text) => `- ${text}`), '')
    const rounds = roastsOf(task.id)
    if (rounds.length) out.push('**Roasts.**', '', ...rounds.map((round) => `-${roundLine(round).slice(3)}`), '')
  }
  return `${out.join('\n')}\n`
}

if (command === 'phase') {
  const [action, name, ...rest] = args
  const given = flags(rest)
  if (!action || action === 'list') {
    const lines = phaseReport()
    console.log(lines.length ? lines.join('\n') : 'No phases. Add one: todo phase add MVP --goal "..."')
  } else if (action === 'add') {
    if (!name || !given.goal) fail('A phase needs a name and what it is for: todo phase add MVP --goal "..."')
    if (db.prepare('SELECT 1 FROM phase WHERE name = ?').get(name)) fail(`There is already a phase called ${name}.`)
    const last = db.prepare('SELECT MAX(position) AS at FROM phase').get().at ?? 0
    const position = given.position ? Number(given.position) : last + 1
    db.prepare('INSERT INTO phase (name, goal, position, status) VALUES (?,?,?,?)').run(name, given.goal, position, 'open')
    console.log(`Added phase ${position}. ${name}`)
  } else if (action === 'done' || action === 'open') {
    if (!db.prepare('SELECT 1 FROM phase WHERE name = ?').get(name)) fail(`No phase called ${name}.`)
    db.prepare('UPDATE phase SET status = ? WHERE name = ?').run(action, name)
    console.log(`Phase ${name} is ${action}.`)
    console.log(phaseReport().join('\n'))
  } else {
    fail(`Unknown phase command "${action}". Try: todo phase, todo phase add <name> --goal "...", todo phase done <name>.`)
  }
} else if (command === 'okr') {
  // An objective is a phase: its id is the phase's name, its name the label
  // and its description the goal, so `phase` and `okr` read and write the same
  // rows and a board that only ever used one of them keeps working.
  const [action] = args
  if (!action || action === 'list') {
    console.log(okrReport().join('\n'))
  } else if (action === 'add') {
    const given = flags(args.slice(1))
    if (!given.name || !given.description) fail('okr add needs --name and --description: an objective nobody can read is a label.')
    const phases = allPhases()
    const id = given.id ?? `OKR-${phases.length + 1}`
    if (phases.some((phase) => phase.name === id)) fail(`okr add: ${id} already exists.`)
    if (phases.some((phase) => phase.label === given.name)) fail(`okr add: there is already an objective called ${given.name}.`)
    const position = given.position === undefined ? Math.max(0, ...phases.map((phase) => phase.position)) + 1 : numberFrom(given.position)
    if (position === null || !Number.isInteger(position) || position < 1) fail('okr add: --position is a whole number from 1, the order they are met in.')
    const taken = phases.find((phase) => phase.position === position)
    if (taken) fail(`okr add: position ${position} is taken by ${taken.name}.`)
    db.prepare('INSERT INTO phase (name, goal, position, status, label) VALUES (?,?,?,?,?)').run(id, given.description, position, 'open', given.name)
    console.log(`Added ${id} ${given.name} at position ${position}.`)
  } else if (action === 'done' || action === 'open') {
    const id = args[1]
    if (!id || !phaseExists(id)) fail(`okr ${action}: ${id ?? '(no id)'} is not an objective this board holds.`)
    db.prepare('UPDATE phase SET status = ? WHERE name = ?').run(action, id)
    console.log(`${id} is ${action === 'done' ? 'met' : 'open'}.`)
    console.log(okrReport().join('\n'))
  } else if (action === 'edit') {
    const id = args[1]
    if (!id || !phaseExists(id)) fail(`okr edit: ${id ?? '(no id)'} is not an objective this board holds.`)
    const given = flags(args.slice(2))
    const fields = { name: 'label', description: 'goal', position: 'position' }
    const unknown = Object.keys(given).filter((key) => !(key in fields))
    if (unknown.length) fail(`okr edit: ${unknown.join(', ')} is not a field of an objective. Pass --name, --description or --position.`)
    if (!Object.keys(given).length) fail('okr edit: nothing to change. Pass --name, --description or --position.')
    for (const [key, column] of Object.entries(fields)) {
      if (given[key] === undefined) continue
      const value = key === 'position' ? numberFrom(given[key]) : given[key]
      if (key === 'position' && (value === null || !Number.isInteger(value) || value < 1)) fail('okr edit: --position is a whole number from 1.')
      db.prepare(`UPDATE phase SET ${column} = ? WHERE name = ?`).run(value, id)
    }
    console.log(`Updated ${id}.`)
  } else {
    fail(`Unknown okr command "${action}". Try: todo okr, todo okr add --name "..." --description "...", todo okr done <id>, todo okr edit <id> --name "...".`)
  }
} else if (command === 'list') {
  const given = flags(args)
  const filtered = Boolean(given.status || given.area || given.severity)
  let tasks = all()
  if (!tasks.length) console.log('The board is empty.')
  if (areaFilter(given.area) !== null) tasks = tasks.filter((task) => inAreas(task, areaFilter(given.area)))
  if (given.severity) tasks = tasks.filter((task) => task.severity === given.severity)
  const report = filtered ? [] : phaseReport()
  if (report.length) console.log(report.join('\n'))
  for (const status of STATUSES) {
    if (given.status && given.status !== status) continue
    const inColumn = tasks.filter((task) => task.status === status && !isBlocked(task))
    if (!inColumn.length) continue
    console.log(`\n${status.toUpperCase().replace('_', ' ')} (${inColumn.length})`)
    // Grouped by area, because a board of seventy tasks read as one list tells
    // you how much there is and nothing about what it is.
    const areas = [...new Set(inColumn.map((task) => task.area ?? 'unset'))].sort()
    for (const area of areas) {
      if (areas.length > 1) console.log(`  ${area}`)
      for (const task of inColumn.filter((task) => (task.area ?? 'unset') === area)) {
        // The phase only where it is not the one being worked on: naming the
        // current phase on every line says nothing and hides the ones that
        // are deferred.
        const later = phaseRank(task) > 0 ? `  · ${task.phase}` : ''
        console.log(`    ${task.id}  [${rankField(task.severity, 'severity')}/${rankField(task.points, 'points')}pt]  ${task.title}${later}`)
      }
    }
  }
  if (!given.status || given.status === 'blocked') {
    const blocked = tasks.filter((task) => isBlocked(task))
    if (blocked.length) {
      console.log(`\nBLOCKED (${blocked.length})`)
      for (const task of blocked) {
        console.log(`    ${task.id}  [${rankField(task.severity, 'severity')}/${rankField(task.points, 'points')}pt]  ${task.title}`)
        console.log(`      ${blockOf(task.id)}`)
      }
    }
  }
  console.log('')
} else if (command === 'show') {
  console.log(card(one(args[0])))
} else if (command === 'next') {
  const areas = areasFor(valueOf(args, 'area'))
  const scope = areas ? ` in area ${areas.join(', ')}` : ''
  const tasks = all()
  const { started, pick } = rankedCall(() => choose(tasks, areas))
  const held = rankedCall(() => startedButWaiting(tasks, areas))
  if (!pick) {
    const mine = tasks.filter((task) => inAreas(task, areas))
    // With no pick, every unblocked task still to do waits on a parent: a
    // backlog one that was eligible, or a started one, would have been picked.
    // A started task counts too, or "Nothing left", which ends a loop, would
    // be said while it waits.
    const waiting = mine.filter((task) => ['backlog', 'in_progress', 'wait_for_roast'].includes(task.status) && !isBlocked(task)).length
    const blocked = mine.filter((task) => !['done', 'dropped'].includes(task.status) && isBlocked(task))
    if (waiting) console.log(`Nothing eligible${scope}. ${waiting} task(s) waiting on unfinished parents.`)
    else if (!blocked.length) console.log(`Nothing left${scope}.`)
    if (blocked.length) {
      console.log(`${blocked.length} task(s) blocked:`)
      for (const task of blocked) console.log(`  ${task.id}: ${blockOf(task.id)}`)
    }
    sayHeld(held, tasks)
    process.exit(0)
  }

  const current = currentPhase()
  const within = current ? ` in ${current.name}` : ''
  console.log(started[0] ? `ALREADY STARTED${scope}, finish this first\n` : `NEXT${within}${scope}: highest severity, unblocked, fewest points\n`)
  console.log(card(pick))
  if (held.length) {
    console.log('')
    sayHeld(held, tasks)
  }
} else if (command === 'add') {
  const given = flags(args)
  const required = ['title', 'desc', 'why', 'severity', 'points', 'exit']
  const missing = required.filter((field) => !given[field])
  if (missing.length) fail(`A task needs every field. Missing: ${missing.join(', ')}`)
  if (!SEVERITIES.includes(given.severity)) fail(`severity must be one of ${SEVERITIES.join(', ')}`)
  if (!POINTS.includes(Number(given.points))) fail(`points must be one of ${POINTS.join(', ')}`)

  const parentIds = (given.parent ?? '')
    .split(',')
    .map((each) => each.trim())
    .filter(Boolean)

  const unknown = parentIds.filter((parent) => !db.prepare('SELECT 1 FROM task WHERE id = ?').get(parent))
  if (unknown.length) {
    fail(
      `No such task: ${unknown.join(', ')}. A parent that does not exist can never be done, ` +
        'so the task would never become eligible and would just vanish from `next`.',
    )
  }

  // `--parent-task` is provenance, `--parent` is a blocker. A roast finding
  // takes the first: it came out of that task and belongs to it, but nothing
  // about it says the parent must finish before this can start. In fact the
  // parent is already `done` by the time its findings exist.
  const parentTask = resolveParent(given['parent-task'])

  // An objective is a phase, so `--okr` is the same reference `--phase` is, and
  // `--okr none` files the card under no objective, which reads as now.
  if (given.okr !== undefined && given.phase === undefined) {
    if (given.okr !== 'none' && given.okr && !phaseExists(given.okr)) {
      fail(`No objective called ${given.okr}. Add it first: todo okr add --id ${given.okr} --name "..." --description "..."`)
    }
    given.phase = given.okr === 'none' ? '' : given.okr
  }

  // A new card is work for the phase being worked on, unless it is named for
  // a later one. Boards with no phases carry none, as before.
  if (given.phase && !phaseExists(given.phase)) {
    fail(`No phase called ${given.phase}. Add it first: todo phase add ${given.phase} --goal "..."`)
  }
  const phase = given.phase ?? currentPhase()?.name ?? null

  // An empty --id is no id: the board picks the next one, as the Python half does.
  const id = given.id || nextId()
  const parentWasTested = testedTask(parentTask)
  db.prepare(
    'INSERT INTO task (id, title, descr, why, severity, points, status, exit_cond, area, parent_task, phase) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
  ).run(
    id,
    given.title,
    given.desc,
    given.why,
    given.severity,
    Number(given.points),
    given.status ?? 'backlog',
    given.exit,
    given.area ?? null,
    parentTask,
    phase,
  )
  for (const parent of parentIds) {
    db.prepare('INSERT INTO blocked_by (task, parent) VALUES (?,?)').run(id, parent)
  }
  console.log(`Added ${id}: ${given.title}`)
  if (parentTask) {
    const open = openChildren(parentTask).length
    console.log(`  a child of ${parentTask}, which now has ${open} open child task(s).`)
    if (parentWasTested && !testedTask(parentTask)) {
      console.log(`  ${parentTask}'s test state is cleared: it has an open finding now. What it had is kept in a note.`)
    }
  }
} else if (command === 'edit' || command === 'set') {
  // `next` picks by severity, then points, then parents. When it picks wrong
  // the fix is to correct that task, which was impossible: the only way to
  // change a field was raw SQL against the database. `set` is the same command
  // under the name some projects' rules use.
  const [id, ...rest] = args
  const task = one(id)
  const given = flags(rest)

  if (given.okr !== undefined && given.phase === undefined) {
    if (given.okr !== 'none' && given.okr && !phaseExists(given.okr)) {
      fail(`No objective called ${given.okr}. Add it first: todo okr add --id ${given.okr} --name "..." --description "..."`)
    }
    given.phase = given.okr === 'none' ? '' : given.okr
  }

  const COLUMNS = {
    title: 'title',
    desc: 'descr',
    why: 'why',
    severity: 'severity',
    points: 'points',
    exit: 'exit_cond',
    area: 'area',
    phase: 'phase',
    evidence: 'evidence',
  }
  const touched = Object.keys(given).filter((field) => field in COLUMNS)
  if (!touched.length && !('parent' in given) && !('parent-task' in given) && !('note' in given)) {
    fail('Nothing to change. Pass --title, --desc, --why, --severity, --points, --exit, --phase, --okr, --evidence, --note, --parent or --parent-task.')
  }
  // An empty value moves a task out of every phase, which reads as now.
  if (given.phase && !phaseExists(given.phase)) {
    fail(`No phase called ${given.phase}. Add it first: todo phase add ${given.phase} --goal "..."`)
  }

  // Reparenting a finding, or cutting it loose. An empty value clears it, the
  // same way `--parent ""` clears the blocker list, so a task filed against the
  // wrong parent can be moved without editing the database by hand.
  if ('parent-task' in given) {
    const wanted = given['parent-task'].trim()
    if (!wanted) {
      db.prepare('UPDATE task SET parent_task = NULL WHERE id = ?').run(id)
      console.log(`${id} is no longer a child of anything.`)
    } else if (wanted === id) {
      fail(`${id} cannot be its own parent.`)
    } else {
      const resolved = resolveParent(wanted)
      const parentWasTested = testedTask(resolved)
      db.prepare('UPDATE task SET parent_task = ? WHERE id = ?').run(resolved, id)
      console.log(`${id} is now a child of ${resolved}.`)
      if (parentWasTested && !testedTask(resolved)) {
        console.log(`  ${resolved}'s test state is cleared: it has an open finding now. What it had is kept in a note.`)
      }
    }
  }

  if (given.severity && !SEVERITIES.includes(given.severity)) fail(`severity must be one of ${SEVERITIES.join(', ')}`)
  if (given.points && !POINTS.includes(Number(given.points))) fail(`points must be one of ${POINTS.join(', ')}`)

  for (const field of touched) {
    const value = field === 'points' ? Number(given[field]) : given[field]
    db.prepare(`UPDATE task SET ${COLUMNS[field]} = ? WHERE id = ?`).run(value, id)
  }

  if ('parent' in given) {
    const wanted = given.parent
      .split(',')
      .map((each) => each.trim())
      .filter(Boolean)

    const unknown = wanted.filter((parent) => !db.prepare('SELECT 1 FROM task WHERE id = ?').get(parent))
    if (unknown.length) fail(`No such task: ${unknown.join(', ')}. A parent that does not exist can never be done.`)

    // A cycle is worse than a wrong parent: every task in it waits forever and
    // `next` reports them as blocked rather than as broken.
    const seen = new Set()
    const stack = [...wanted]
    while (stack.length) {
      const at = stack.pop()
      if (at === id) fail(`That parent makes a cycle: ${id} would wait on itself, through ${[...seen].join(' -> ') || at}.`)
      if (seen.has(at)) continue
      seen.add(at)
      stack.push(...parents(at))
    }

    db.prepare('DELETE FROM blocked_by WHERE task = ?').run(id)
    for (const parent of wanted) db.prepare('INSERT INTO blocked_by (task, parent) VALUES (?,?)').run(id, parent)
  }

  // A note is added, never replaced: what was known when is the point of one.
  if ('note' in given && given.note) db.prepare('INSERT INTO note (task, at, text) VALUES (?,?,?)').run(id, now(), given.note)
  db.prepare('UPDATE task SET updated = ? WHERE id = ?').run(now(), id)

  const changed = [...touched, ...('parent' in given ? ['parent'] : []), ...('note' in given ? ['note'] : [])]
  console.log(`${id}: ${changed.join(', ')} changed`)
  if (isTested(task) && !testedTask(id)) {
    console.log(`  ${id}'s test state is cleared: what it proved has changed. What it had is kept in a note.`)
  }
  console.log(card(one(id)))
} else if (command === 'move') {
  const [id, status, ...rest] = args
  const given = flags(rest)
  if (status === 'blocked') {
    // A block is a row beside the status, not a status, so the task keeps the
    // status it had and `next`, `list` and `show` read the block from the row.
    const task = one(id)
    if (!given.reason) fail(`move ${id} blocked needs --reason "...": a blocked task leaves next, so it has to say why.`)
    db.prepare('INSERT OR REPLACE INTO blocked (task, reason, since) VALUES (?,?,?)').run(id, given.reason, now())
    db.prepare('UPDATE task SET updated = ? WHERE id = ?').run(now(), id)
    console.log(`${id}: ${task.status}, blocked: ${given.reason}`)
    process.exit(0)
  }
  if (!STATUSES.includes(status)) fail(`status must be one of ${STATUSES.join(', ')}, or blocked`)
  const task = one(id)
  if (status === 'in_progress') {
    const { loop, mine } = loopAreas()
    if (mine !== null && !inAreas(task, mine)) fail(`${id} is in area ${task.area || 'unset'}; this session works in ${mine.join(', ')} (its loop file, ${loop.file}).`)
  }
  const wasBlocked = blockOf(id)
  const parentWasTested = testedTask(task.parent_task)
  const stamp = now()
  db.prepare('UPDATE task SET status = ?, updated = ? WHERE id = ?').run(status, stamp, id)
  if (wasBlocked !== null) db.prepare('DELETE FROM blocked WHERE task = ?').run(id)
  if (status === 'done') {
    db.prepare('UPDATE task SET closed = ? WHERE id = ?').run(stamp, id)
    if (given.evidence) db.prepare('UPDATE task SET evidence = ? WHERE id = ?').run(given.evidence, id)
  }
  if (status === 'dropped' && given.reason) db.prepare('UPDATE task SET reason = ? WHERE id = ?').run(given.reason, id)
  console.log(`${id}: ${task.status} -> ${status}`)
  if (wasBlocked !== null) console.log(`  no longer blocked: ${wasBlocked}`)
  if (isTested(task) && !testedTask(id)) console.log(`  ${id}'s test state is cleared: it is ${status} now. What it had is kept in a note.`)
  if (parentWasTested && !testedTask(task.parent_task)) {
    console.log(`  ${task.parent_task}'s test state is cleared: it has an open finding now. What it had is kept in a note.`)
  }

  // Said, not refused. A finding is a new card rather than a reopened one in
  // some projects' rules, but others may differ, so the tool names the
  // rule and moves the task; a refusal here would be a gate on every board.
  if (task.status === 'done' && status !== 'done') {
    console.log(`  ${id} was done. A finding is a new card rather than a reopened one: add --parent-task ${id}.`)
  }
  if (status === 'in_progress') {
    const others = db.prepare("SELECT id FROM task WHERE status = 'in_progress' AND id != ? ORDER BY id").all(id).map((row) => row.id)
    if (others.length) console.log(`  ${others.join(', ')} ${others.length === 1 ? 'is' : 'are'} in progress too; finish one before starting another.`)
  }

  // Everything below PRINTS. Nothing here refuses a move, including closing a
  // task that still has open children: the tool says what is worth roasting and
  // the agent decides. A refusal would be a gate nobody asked for.
  if (status === 'done') {
    console.log('')
    console.log(`Roast ${id} now, in the background, and take the next task while it runs.`)
    console.log(`  File everything it finds with: add --parent-task ${id}`)
    console.log('  A finding is a child of this task, not a loose card, so the board can tell')
    console.log('  when everything that came out of it is finished.')

    const stillOpen = openChildren(id)
    if (stillOpen.length) {
      console.log('')
      console.log(`${id} still has ${stillOpen.length} open child task(s): ${stillOpen.map((child) => child.id).join(', ')}`)
    }

    // The other half: closing a CHILD can complete its parent's group.
    if (task.parent_task) {
      const siblings = openChildren(task.parent_task)
      console.log('')
      if (siblings.length) {
        console.log(`${task.parent_task} is waiting on ${siblings.length} more: ${siblings.map((s) => s.id).join(', ')}`)
      } else {
        const family = children(task.parent_task).map((child) => child.id)
        console.log(`THAT WAS THE LAST ONE. Every child of ${task.parent_task} is finished.`)
        console.log(`  Roast ${task.parent_task} together with all of them: ${family.join(', ')}`)
        console.log('  Roast what was done for the WHOLE task, not just this last piece: the')
        console.log('  point of the round is whether the parent is actually finished now.')
        console.log(`  Anything it finds becomes a new child of ${task.parent_task}, and the cycle`)
        console.log('  repeats until a round finds nothing.')
      }
    }
  }
} else if (command === 'roast') {
  // A round of a roast, recorded against the task it reviewed: the file the
  // reviewer's answer went to, the numbers if it gave any, and, once judged,
  // what was filed from it. Recording again against the same file updates that
  // round, since judging is a second moment of the same round.
  const [id, ...rest] = args
  one(id)
  const given = flags(rest)
  if (!given.file) fail('roast needs --file, the file the roast wrote its answer to.')
  const last = db.prepare('SELECT * FROM roast WHERE task = ? ORDER BY round DESC LIMIT 1').get(id)
  const again = Boolean(last && last.file === given.file)
  const round = again ? last.round : (last?.round ?? 0) + 1
  const score = given.score === undefined ? (again ? last.score : null) : numberFrom(given.score)
  if (given.score !== undefined && score === null) fail('roast: --score must be a number.')
  const criticals = given.criticals === undefined ? (again ? last.criticals : null) : numberFrom(given.criticals)
  if (given.criticals !== undefined && criticals === null) fail('roast: --criticals must be a number.')
  const filed =
    given.filed === undefined
      ? again
        ? last.filed
        : null
      : given.filed === 'none'
        ? ''
        : given.filed
            .split(',')
            .map((each) => each.trim())
            .filter(Boolean)
            .join(', ')
  const dismissed = given.dismissed ?? (again ? last.dismissed : null)
  if (again) {
    db.prepare('UPDATE roast SET at = ?, score = ?, criticals = ?, filed = ?, dismissed = ? WHERE task = ? AND round = ?').run(
      now(),
      score,
      criticals,
      filed,
      dismissed,
      id,
      round,
    )
  } else {
    db.prepare('INSERT INTO roast (task, round, at, file, score, criticals, filed, dismissed) VALUES (?,?,?,?,?,?,?,?)').run(
      id,
      round,
      now(),
      given.file,
      score,
      criticals,
      filed,
      dismissed,
    )
  }
  console.log(`${id} roast round ${round}${again ? ' (updated)' : ''}`)
  if (filed === null) {
    console.log(`  Now judge it: reproduce each finding or say what it misread, file what survives with add --parent-task ${id},`)
    console.log('  then record it here with --filed <ids>, or --filed none.')
  } else if (filed === '') {
    console.log('  Nothing survived adjudication.')
  } else {
    console.log(`  Filed ${filed}.`)
  }
} else if (command === 'validate') {
  // Read-only. Every problem it can find, not the first, and exit 1 when there
  // is one, so a script can ask whether the board holds together.
  const tasks = all()
  const byId = new Map(tasks.map((task) => [task.id, task]))
  const phaseNames = new Set(allPhases().map((phase) => phase.name))
  const problems = []
  for (const task of tasks) {
    const problem = severityProblem(task)
    if (problem !== null) problems.push(problem)
    const pointsError = pointsProblem(task)
    if (pointsError !== null) problems.push(pointsError)
    for (const parent of task.parents) {
      const blocker = byId.get(parent)
      if (!blocker) {
        problems.push(`${task.id}: its blocker ${parent} does not exist`)
        continue
      }
      // A blocker less severe than what it blocks is never picked ahead of it,
      // so the severe task starves behind one nobody selects.
      if (!['done', 'dropped'].includes(task.status) && blocker.status !== 'done' && severityProblem(blocker) === null && problem === null && severityRank(blocker) > severityRank(task)) {
        problems.push(`${task.id} (${task.severity}) waits on ${parent} (${blocker.severity}), which is less severe, so next would never pick it first`)
      }
      if (task.status === 'done' && blocker.status !== 'done') problems.push(`${task.id} is done, but its blocker ${parent} is ${blocker.status}`)
    }
    if (task.phase && !phaseNames.has(task.phase)) problems.push(`${task.id}: its phase ${task.phase} does not exist`)
    if (task.parent_task) {
      const from = byId.get(task.parent_task)
      if (!from) problems.push(`${task.id}: it came out of ${task.parent_task}, which does not exist`)
      else if (from.parent_task) problems.push(`${task.id}: it came out of ${task.parent_task}, which itself came out of ${from.parent_task}; one level only`)
    }
    // An exit condition that cannot be checked is a wish. This does not prove
    // checkability, it only catches the one-word placeholder.
    if (task.exit_cond.trim().length < 25) problems.push(`${task.id}: its exit condition is too short to check`)
    const reason = blockOf(task.id)
    if (reason !== null && ['done', 'dropped'].includes(task.status)) problems.push(`${task.id} is ${task.status} and still blocked: ${reason}`)
    // A claim the guards would have refused, which only a board whose guards
    // were dropped, or were never added, can hold.
    if (recordsTests(task)) {
      for (const flag of ['tested', 'e2e_tested']) {
        if (task[flag] !== 0 && task[flag] !== 1) problems.push(`${task.id}: ${flag} holds ${task[flag]}, not 0 or 1`)
      }
      if (task.tested === 1 && task.status !== 'done') problems.push(`${task.id} is tested but ${task.status}`)
      if (task.tested === 1 && !stripBlank(task.tested_how ?? '')) problems.push(`${task.id} is tested with nothing saying what proved it`)
      if (task.e2e_tested === 1 && task.tested !== 1) problems.push(`${task.id} is e2e tested but not tested`)
      if (task.e2e_tested === 1 && !stripBlank(task.e2e_how ?? '')) problems.push(`${task.id} is e2e tested with nothing saying what proved it`)
      const stillOpen = isTested(task) ? openChildren(task.id).map((child) => child.id) : []
      if (stillOpen.length) problems.push(`${task.id} is tested, but its finding(s) ${stillOpen.join(', ')} are open`)
    }
  }

  const present = taskColumns()
  if (TEST_STATE_COLUMNS.some(([name]) => present.includes(name))) {
    const lacking = TEST_STATE_COLUMNS.filter(([name]) => !present.includes(name)).map(([name]) => name)
    if (lacking.length) problems.push(`the board records test states without the column(s) ${lacking.join(', ')}`)
    const guards = new Set(db.prepare("SELECT name FROM sqlite_master WHERE type = 'trigger'").all().map((row) => row.name))
    const absent = TEST_STATE_TRIGGER_NAMES.filter((name) => !guards.has(name))
    if (absent.length) problems.push(`the board records test states without the guard(s) ${absent.join(', ')}; the next todo tested adds them`)
  }

  // Depth-first cycle detection over the blocker edges.
  const state = new Map()
  const walk = (id, trail) => {
    if (state.get(id) === 'done') return
    if (state.get(id) === 'open') {
      problems.push(`a cycle: ${[...trail, id].join(' -> ')}`)
      return
    }
    state.set(id, 'open')
    // Initial and recursive calls refer only to known tasks with normalized parents.
    for (const parent of byId.get(id).parents) {
      if (byId.has(parent)) walk(parent, [...trail, id])
    }
    state.set(id, 'done')
  }
  for (const task of tasks) walk(task.id, [])

  const unjudged = db.prepare('SELECT DISTINCT task FROM roast WHERE filed IS NULL ORDER BY task').all().map((row) => row.task)
  console.log(`${unjudged.length} task(s) have a roast round nobody recorded as judged${unjudged.length ? `: ${unjudged.join(', ')}` : ''}.`)
  if (!problems.length) {
    console.log(`Board is valid. ${tasks.length} task(s).`)
    process.exit(0)
  }
  console.error(`${problems.length} problem(s):`)
  for (const problem of problems) console.error(`  - ${problem}`)
  process.exit(1)
} else if (command === 'render') {
  // The board as Markdown, beside the database or wherever --out says, so a
  // person can read it and a change to it reads as a diff. --check writes
  // nothing and fails when the file is stale.
  const outGiven = valueOf(args, 'out')
  const out = outGiven ? resolve(outGiven) : join(dirname(BOARD), 'TODO_BOARD.md')
  const label = outGiven ?? '.claude/TODO_BOARD.md'
  const text = rankedCall(renderBoard)
  if (args.includes('--check')) {
    const current = existsSync(out) ? readFileSync(out, 'utf8') : ''
    if (current !== text) fail(`${label} is stale: it differs from what the board renders. Run: todo render${outGiven ? ` --out ${outGiven}` : ''}`)
    console.log(`${label} is in sync with the board.`)
  } else {
    mkdirSync(dirname(out), { recursive: true })
    writeFileSync(out, text, 'utf8')
    console.log(`rendered ${label}`)
  }
} else if (command === 'rm') {
  // Removing a card, which leaves only this line behind, so it says why. A card
  // that others still wait on, or that others came out of, is refused unless
  // --force, which cuts those links.
  const [id, ...rest] = args
  one(id)
  const reason = valueOf(rest, 'reason')
  if (!reason || reason === '--force') fail(`rm ${id} needs --reason "...": a removed card leaves nothing behind but the reason.`)
  const waiting = db.prepare('SELECT task FROM blocked_by WHERE parent = ? ORDER BY task').all(id).map((row) => row.task)
  const kids = children(id).map((child) => child.id)
  if ((waiting.length || kids.length) && !rest.includes('--force')) {
    fail(`rm: ${[...waiting, ...kids].join(', ')} still point at ${id}. Point them elsewhere first, or pass --force to cut those links.`)
  }
  db.prepare('DELETE FROM blocked_by WHERE task = ? OR parent = ?').run(id, id)
  db.prepare('UPDATE task SET parent_task = NULL WHERE parent_task = ?').run(id)
  for (const table of ['blocked', 'note', 'roast']) db.prepare(`DELETE FROM ${table} WHERE task = ?`).run(id)
  db.prepare('DELETE FROM task WHERE id = ?').run(id)
  console.log(`removed ${id}: ${reason}`)
} else if (command === 'tested' || command === 'e2e') {
  // Done is not tested. A claim needs a done task that is not blocked, has no
  // open finding, and says what proved it; e2e needs tested first. The board
  // is checked inside the claim's own transaction and before anything is
  // written, so a refused claim leaves the file as it was, a board that never
  // records a test never gains the columns, and a finding filed by another
  // session cannot land between the check and the claim.
  const [id, ...rest] = args
  if (!id || id.startsWith('--')) fail(`${command} needs a task: todo ${command} SB-003 --evidence "what was run and what it showed"`)
  const evidence = stripBlank(valueOf(rest, 'evidence') ?? '')
  if (!evidence) fail(`${command} ${id} needs --evidence "...": what was run and what it showed. Nothing was recorded.`)
  const stamp = now()
  let added = false
  let open = false
  try {
    db.exec('BEGIN IMMEDIATE')
    open = true
    const problem = claimProblem(id, command)
    if (problem !== null) giveUp(problem, open)
    added = command === 'tested' ? startTestStates() : false
    if (command === 'tested') {
      db.prepare('UPDATE task SET tested = 1, tested_how = ?, tested_at = ?, updated = ? WHERE id = ?').run(evidence, stamp, stamp, id)
    } else {
      db.prepare('UPDATE task SET e2e_tested = 1, e2e_how = ?, e2e_at = ?, updated = ? WHERE id = ?').run(evidence, stamp, stamp, id)
    }
    db.exec('COMMIT')
  } catch (error) {
    giveUp(`${id}: the board could not take the claim: ${error.message}. Nothing was recorded.`, open)
  }
  console.log(`${id}: ${command === 'tested' ? 'tested' : 'e2e tested'}, ${evidence}`)
  if (added) console.log('  This board records test states from now on. Every other done task reads tested: no until it is tested.')
} else if (command === 'untest') {
  // Clearing a test state by hand, for a proof that no longer holds. What it
  // had goes into a note first: a cleared claim is history, not nothing. The
  // state is read inside the transaction that clears it, so two clears at once
  // leave one note, and a failure leaves neither the note nor the change.
  const [id, ...rest] = args
  if (!id || id.startsWith('--')) fail('untest needs a task: todo untest SB-003 [e2e] --reason "why the proof no longer holds"')
  const onlyE2e = rest[0] === 'e2e'
  const reason = stripBlank(valueOf(rest, 'reason') ?? '')
  if (!reason) fail(`untest ${id} needs --reason "...": a cleared test state says why. Nothing was changed.`)
  if (!hasTestStates()) {
    one(id)
    console.log(`${id}: this board records no test states, so there is nothing to clear.`)
    process.exit(0)
  }
  const stamp = now()
  let open = false
  try {
    db.exec('BEGIN IMMEDIATE')
    open = true
    const task = db.prepare('SELECT * FROM task WHERE id = ?').get(id)
    if (!task) giveUp(`No task ${id}.`, open)
    if (!(onlyE2e ? task.e2e_tested === 1 : isTested(task))) {
      db.exec('ROLLBACK')
      db.close()
      console.log(`${id} is not ${onlyE2e ? 'e2e tested' : 'tested'}, so there is nothing to clear.`)
      process.exit(0)
    }
    const cleared = onlyE2e ? `e2e tested: ${task.e2e_how ?? ''}` : had(task)
    const sets = onlyE2e
      ? 'e2e_tested = 0, e2e_how = NULL, e2e_at = NULL'
      : 'tested = 0, tested_how = NULL, tested_at = NULL, e2e_tested = 0, e2e_how = NULL, e2e_at = NULL'
    db.prepare('INSERT INTO note (task, at, text) VALUES (?,?,?)').run(id, stamp, `test state cleared by hand: ${reason}; it had ${cleared}`)
    db.prepare(`UPDATE task SET ${sets}, updated = ? WHERE id = ?`).run(stamp, id)
    db.exec('COMMIT')
  } catch (error) {
    giveUp(`${id}: the board could not clear it: ${error.message}. Nothing was changed.`, open)
  }
  console.log(`${id}: ${onlyE2e ? 'e2e test state' : 'test state'} cleared. What it had is kept in a note.`)
} else if (command === 'tests') {
  // Read-only: which done tasks are tested, and which are not. A board that
  // records no test states says so rather than calling every task untested.
  const areas = areaFilter(valueOf(args, 'area'))
  if (!hasTestStates()) {
    console.log('This board records no test states yet. The first is: todo tested <id> --evidence "..."')
    process.exit(0)
  }
  const scope = areas ? ` in area ${areas.join(', ')}` : ''
  const done = all().filter((task) => task.status === 'done' && inAreas(task, areas))
  if (!done.length) console.log(`Nothing is done${scope} yet.`)
  const groups = [
    ['DONE, NOT TESTED', done.filter((task) => task.tested !== 1)],
    ['TESTED, NOT E2E TESTED', done.filter((task) => task.tested === 1 && task.e2e_tested !== 1)],
    ['E2E TESTED', done.filter((task) => task.tested === 1 && task.e2e_tested === 1)],
  ]
  for (const [heading, group] of groups) {
    if (!group.length) continue
    console.log(`\n${heading}${scope} (${group.length})`)
    for (const task of group) {
      console.log(`    ${task.id}  [${rankField(task.severity, 'severity')}/${rankField(task.points, 'points')}pt]  ${task.title}`)
      if (task.tested === 1) console.log(`      tested: ${task.tested_how}`)
      if (task.e2e_tested === 1) console.log(`      e2e   : ${task.e2e_how}`)
    }
  }
  console.log('')
} else {
  fail(`Unknown command "${command}". Try: list, next, show, add, edit, set, move, phase, okr, roast, validate, render, rm.`)
}
