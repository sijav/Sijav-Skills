#!/usr/bin/env node
// The board. A real database, at .claude/todo.db, local to this project.
//
//   todo                       the whole board
//   todo next                  what to do next, and why it was picked
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

import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'

// SQLite is inside Node, so this script has no dependencies and works in a
// checkout with no node_modules. It is unflagged from Node 24; on 22 and 23 it
// needs --experimental-sqlite, and before that it does not exist. Saying which
// beats letting the import throw a stack trace at someone.
let DatabaseSync
try {
  ;({ DatabaseSync } = await import('node:sqlite'))
} catch {
  console.error(`This board needs node:sqlite, and this is Node ${process.version}.`)
  console.error('Node 24 or newer has it built in. On 22 or 23, run with --experimental-sqlite.')
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

const BOARD = findBoard(process.cwd())

// Refuse BEFORE mkdir and before SQLite opens. Both create what is missing, so
// a check placed after either is not a check.
if (!BOARD) {
  console.error(`No board. Nothing at or above ${process.cwd()} has .claude/todo.db.`)
  console.error('If this project should have one: todo init --here')
  process.exit(2)
}

// Two processes on one board are normal: a loop adding a card while a roast or
// a second session reads it. node:sqlite's busy timeout defaults to zero, so
// the second writer failed at once with "database is locked", measured at 1 ms;
// with a timeout it waits for the lock. Five seconds is Python's sqlite3
// default, so both halves wait the same.
const db = new DatabaseSync(BOARD, { timeout: 5000 })

const SEVERITIES = ['critical', 'high', 'medium', 'low']
const STATUSES = ['backlog', 'in_progress', 'wait_for_roast', 'done', 'dropped']
const POINTS = [1, 2, 3, 5, 8, 13]

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

/**
 * The id after the highest one, in the board's own prefix.
 *
 * It used to be one fixed prefix whatever the board was, so a plain `add` on a
 * board with another prefix got the wrong one. An empty board has no prefix to keep, so it takes the capitals of the
 * project folder's name, or its first two letters when it has fewer than two.
 */
const nextId = () => {
  let best = null
  for (const row of db.prepare('SELECT id FROM task ORDER BY rowid').all()) {
    const match = /^(.*?)(\d+)$/.exec(row.id)
    if (match && (best === null || Number(match[2]) > best.number)) best = { prefix: match[1], number: Number(match[2]) }
  }
  if (best) return `${best.prefix}${String(best.number + 1).padStart(3, '0')}`
  const project = dirname(dirname(BOARD)).split(/[\\/]/).pop() ?? ''
  const capitals = (project.match(/[A-Z]/g) ?? []).join('')
  const prefix = capitals.length >= 2 ? capitals : project.slice(0, 2).toUpperCase()
  return `${prefix}-001`
}

const flags = (args) => {
  const out = {}
  for (let i = 0; i < args.length; i += 2) out[args[i].replace(/^--/, '')] = args[i + 1]
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

const card = (task) => {
  const label = task.phase ? allPhases().find((phase) => phase.name === task.phase)?.label : null
  const reason = blockOf(task.id)
  const notes = notesOf(task.id)
  const rounds = roastsOf(task.id)
  return [
    `${task.id}  [${task.severity}/${task.points}pt]  ${task.status}${task.area ? `  ${task.area}` : ''}`,
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

// Ids sort numerically, not as text. `localeCompare` puts SB-1000 before
// SB-999, which is correct today and wrong from the thousandth task: the worst
// kind of bug, invisible until a boundary and then quietly reordering the work.
const idNumber = (id) => Number(String(id).replace(/^\D+/, '')) || 0

// The phase comes first: what the product needs to ship is picked before what
// comes after it, however severe the later one is.
const byRule = (a, b) =>
  phaseRank(a) - phaseRank(b) ||
  SEVERITIES.indexOf(a.severity) - SEVERITIES.indexOf(b.severity) ||
  a.points - b.points ||
  idNumber(a.id) - idNumber(b.id) ||
  a.id.localeCompare(b.id)

/**
 * What `next` would pick, for `next` and for the rendered board alike.
 *
 * Work in flight is finished before anything new starts, and among several
 * started tasks the same rule decides which. Taking the first by id meant a low
 * severity task begun earlier beat a critical one. A blocked task is neither.
 */
const choose = (tasks) => {
  const done = new Set(tasks.filter((task) => task.status === 'done').map((task) => task.id))
  const started = tasks.filter((task) => (task.status === 'in_progress' || task.status === 'wait_for_roast') && !isBlocked(task)).sort(byRule)
  const eligible = tasks
    .filter((task) => task.status === 'backlog' && !isBlocked(task))
    .filter((task) => task.parents.every((parent) => done.has(parent)))
    .sort(byRule)
  return { started, pick: started[0] ?? eligible[0] }
}

const escapeCell = (text) => String(text ?? '').replace(/\|/g, '\\|').replace(/\r?\n/g, ' ')

const bySeverity = (a, b) =>
  SEVERITIES.indexOf(a.severity) - SEVERITIES.indexOf(b.severity) || a.points - b.points || idNumber(a.id) - idNumber(b.id) || a.id.localeCompare(b.id)

/** The board as Markdown, which is what a person reads and what a diff of it shows. */
const renderBoard = () => {
  const tasks = all()
  const project = dirname(dirname(BOARD)).split(/[\\/]/).pop() ?? ''
  const done = tasks.filter((task) => task.status === 'done')
  const points = tasks.reduce((sum, task) => sum + task.points, 0)
  const donePoints = done.reduce((sum, task) => sum + task.points, 0)
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
    const column = tasks.filter((task) => (key === 'blocked' ? isBlocked(task) : task.status === key && !isBlocked(task))).sort(bySeverity)
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
      `- **status** ${task.status} · **severity** ${task.severity} · **points** ${task.points} · **area** ${task.area ?? 'unset'}${task.phase ? ` · **objective** ${task.phase}` : ''}`,
    )
    out.push(`- **blocked by** ${task.parents.join(', ') || 'none'}`)
    const reason = blockOf(task.id)
    if (reason !== null) out.push(`- **blocked** ${reason}`)
    if (task.parent_task) out.push(`- **came out of** ${task.parent_task}`)
    out.push('', task.descr, '', `**Why.** ${task.why}`, '', `**Exit condition.** ${task.exit_cond}`, '')
    if (task.evidence) out.push(`**Evidence.** ${task.evidence}`, '')
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
    // A trailing flag with no value would be a key holding undefined, and an
    // edit of nothing would report itself done.
    const given = Object.fromEntries(Object.entries(flags(args.slice(2))).filter(([, value]) => value !== undefined))
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
  if (given.area) tasks = tasks.filter((task) => task.area === given.area)
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
        console.log(`    ${task.id}  [${task.severity}/${task.points}pt]  ${task.title}${later}`)
      }
    }
  }
  if (!given.status || given.status === 'blocked') {
    const blocked = tasks.filter((task) => isBlocked(task))
    if (blocked.length) {
      console.log(`\nBLOCKED (${blocked.length})`)
      for (const task of blocked) {
        console.log(`    ${task.id}  [${task.severity}/${task.points}pt]  ${task.title}`)
        console.log(`      ${blockOf(task.id)}`)
      }
    }
  }
  console.log('')
} else if (command === 'show') {
  console.log(card(one(args[0])))
} else if (command === 'next') {
  const tasks = all()
  const { started, pick } = choose(tasks)
  if (!pick) {
    const waiting = tasks.filter((task) => task.status === 'backlog' && !isBlocked(task)).length
    const blocked = tasks.filter((task) => !['done', 'dropped'].includes(task.status) && isBlocked(task))
    if (waiting) console.log(`Nothing eligible. ${waiting} task(s) waiting on unfinished parents.`)
    else if (!blocked.length) console.log('Nothing left.')
    if (blocked.length) {
      console.log(`${blocked.length} task(s) blocked:`)
      for (const task of blocked) console.log(`  ${task.id}: ${blockOf(task.id)}`)
    }
    process.exit(0)
  }

  const current = currentPhase()
  const within = current ? ` in ${current.name}` : ''
  console.log(started[0] ? 'ALREADY STARTED, finish this first\n' : `NEXT${within}: highest severity, unblocked, fewest points\n`)
  console.log(card(pick))
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

  const id = given.id ?? nextId()
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
    const wanted = (given['parent-task'] ?? '').trim()
    if (!wanted) {
      db.prepare('UPDATE task SET parent_task = NULL WHERE id = ?').run(id)
      console.log(`${id} is no longer a child of anything.`)
    } else if (wanted === id) {
      fail(`${id} cannot be its own parent.`)
    } else {
      const resolved = resolveParent(wanted)
      db.prepare('UPDATE task SET parent_task = ? WHERE id = ?').run(resolved, id)
      console.log(`${id} is now a child of ${resolved}.`)
    }
  }

  if (given.severity && !SEVERITIES.includes(given.severity)) fail(`severity must be one of ${SEVERITIES.join(', ')}`)
  if (given.points && !POINTS.includes(Number(given.points))) fail(`points must be one of ${POINTS.join(', ')}`)

  for (const field of touched) {
    const value = field === 'points' ? Number(given[field]) : given[field]
    db.prepare(`UPDATE task SET ${COLUMNS[field]} = ? WHERE id = ?`).run(value, id)
  }

  if ('parent' in given) {
    const wanted = (given.parent ?? '')
      .split(',')
      .map((each) => each.trim())
      .filter(Boolean)

    const unknown = wanted.filter((parent) => !db.prepare('SELECT 1 FROM task WHERE id = ?').get(parent))
    if (unknown.length) fail(`No such task: ${unknown.join(', ')}. A parent that does not exist can never be done.`)

    // A cycle is worse than a wrong parent: every task in it waits forever and
    // `next` reports them as blocked rather than as broken.
    const edges = (of) => (of === id ? wanted : parents(of))
    const seen = new Set()
    const stack = [...wanted]
    while (stack.length) {
      const at = stack.pop()
      if (at === id) fail(`That parent makes a cycle: ${id} would wait on itself, through ${[...seen].join(' -> ') || at}.`)
      if (seen.has(at)) continue
      seen.add(at)
      stack.push(...edges(at))
    }

    db.prepare('DELETE FROM blocked_by WHERE task = ?').run(id)
    for (const parent of wanted) db.prepare('INSERT INTO blocked_by (task, parent) VALUES (?,?)').run(id, parent)
  }

  // A note is added, never replaced: what was known when is the point of one.
  if ('note' in given && given.note) db.prepare('INSERT INTO note (task, at, text) VALUES (?,?,?)').run(id, now(), given.note)
  db.prepare('UPDATE task SET updated = ? WHERE id = ?').run(now(), id)

  const changed = [...touched, ...('parent' in given ? ['parent'] : []), ...('note' in given ? ['note'] : [])]
  console.log(`${id}: ${changed.join(', ')} changed`)
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
  const wasBlocked = blockOf(id)
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
    for (const parent of task.parents) {
      const blocker = byId.get(parent)
      if (!blocker) {
        problems.push(`${task.id}: its blocker ${parent} does not exist`)
        continue
      }
      // A blocker less severe than what it blocks is never picked ahead of it,
      // so the severe task starves behind one nobody selects.
      if (!['done', 'dropped'].includes(task.status) && blocker.status !== 'done' && SEVERITIES.indexOf(blocker.severity) > SEVERITIES.indexOf(task.severity)) {
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
    for (const parent of byId.get(id)?.parents ?? []) {
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
  const text = renderBoard()
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
} else {
  fail(`Unknown command "${command}". Try: list, next, show, add, edit, set, move, phase, okr, roast, validate, render, rm.`)
}
