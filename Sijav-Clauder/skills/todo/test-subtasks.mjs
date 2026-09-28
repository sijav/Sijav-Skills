// Drives the real todo skill through the parent-and-children cycle.
//
// Against a throwaway project directory, so nothing here can touch a real
// board. The skill walks UP for a board that already exists and refuses to
// invent one, so the sandbox creates its own explicitly with `init --here`.

import { spawnSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { homedir } from 'node:os'
import { join } from 'node:path'

const SKILL = join(homedir(), '.claude', 'skills', 'todo', 'todo.mjs')
const dir = mkdtempSync(join(tmpdir(), 'todo-subtask-'))
writeFileSync(join(dir, 'package.json'), '{"name":"sandbox"}\n')
mkdirSync(join(dir, '.claude'), { recursive: true })

const todo = (...args) => {
  const result = spawnSync(process.execPath, [SKILL, ...args], { cwd: dir, encoding: 'utf8' })
  return `${result.stdout ?? ''}${result.stderr ?? ''}`
}

// A board is only ever created deliberately now. This test used to rely on
// the first command bringing one into being.
todo('init', '--here')

const add = (title, extra = []) =>
  todo('add', '--title', title, '--desc', 'd', '--why', 'w', '--severity', 'high', '--points', '1', '--exit', 'e', ...extra)

const failures = []
const check = (label, run) => {
  const problem = run()
  if (problem) failures.push(`${label}: ${problem}`)
  else process.stdout.write(`  ok   ${label}\n`)
}

try {
  const parentOut = add('the parent task')
  const parent = /Added (\S+):/.exec(parentOut)?.[1]
  if (!parent) throw new Error(`could not add a parent: ${parentOut}`)

  check('closing a task tells you to roast it and to file findings as children', () => {
    const out = todo('move', parent, 'done')
    if (!/Roast \S+ now/.test(out)) return `it did not ask for a roast:\n${out}`
    return out.includes(`--parent-task ${parent}`) ? null : `it did not name the filing command:\n${out}`
  })

  const childA = /Added (\S+):/.exec(add('first finding', ['--parent-task', parent]))?.[1]
  const childB = /Added (\S+):/.exec(add('second finding', ['--parent-task', parent]))?.[1]

  check('a child records its parent, and the card shows both directions', () => {
    const childCard = todo('show', childA)
    if (!childCard.includes(`from : ${parent}`)) return `the child does not show where it came from:\n${childCard}`
    const parentCard = todo('show', parent)
    if (!parentCard.includes(`parts: ${childA}, ${childB}`)) return `the parent does not list its parts:\n${parentCard}`
    return /still open, so this is not finished yet/.test(parentCard) ? null : 'the parent does not say it is unfinished'
  })

  check('closing one child says how many are left, and does NOT call for the group roast', () => {
    const out = todo('move', childA, 'done')
    if (!out.includes(`${parent} is waiting on 1 more`)) return `it did not report the remaining sibling:\n${out}`
    return /THAT WAS THE LAST ONE/.test(out) ? 'it called for the group roast too early' : null
  })

  check('closing the LAST child calls for a roast of the parent with all of them', () => {
    const out = todo('move', childB, 'done')
    if (!/THAT WAS THE LAST ONE/.test(out)) return `it did not notice the group was finished:\n${out}`
    if (!out.includes(`Roast ${parent} together with all of them: ${childA}, ${childB}`)) {
      return `it did not name the parent and every child:\n${out}`
    }
    return /whether the parent is actually finished now/.test(out) ? null : 'it did not say what the round is for'
  })

  check('a dropped child does not hold the group open', () => {
    const other = /Added (\S+):/.exec(add('third finding', ['--parent-task', parent]))?.[1]
    const out = todo('move', other, 'dropped')
    const card = todo('show', parent)
    if (/still open, so this is not finished yet/.test(card)) return 'a dropped child still counts as open'
    return out ? null : 'no output at all'
  })

  check('naming a child as a parent flattens to one level and says so', () => {
    const out = add('a grandchild attempt', ['--parent-task', childA])
    if (!out.includes(`is itself a child of ${parent}`)) return `it did not report the flattening:\n${out}`
    const id = /Added (\S+):/.exec(out)?.[1]
    const card = todo('show', id)
    return card.includes(`from : ${parent}`) ? null : `it did not attach to the grandparent:\n${card}`
  })

  check('closing a parent that still has open children is allowed, and reports them', () => {
    // The no-gate property, stated as a test: the tool says what is unfinished
    // and moves the task anyway.
    const other = /Added (\S+):/.exec(add('a parent with a loose end'))?.[1]
    todo('move', other, 'done')
    const kid = /Added (\S+):/.exec(add('an open finding', ['--parent-task', other]))?.[1]
    const out = todo('move', other, 'done')
    if (!out.includes('still has 1 open child task')) return `it did not report the open child:\n${out}`
    const card = todo('show', other)
    return /status\s*$|done/.test(card) && card.includes(kid) ? null : 'the move did not take effect'
  })

  check('edit can reparent a finding and cut one loose', () => {
    const id = /Added (\S+):/.exec(add('a misfiled finding'))?.[1]
    const attach = todo('edit', id, '--parent-task', parent)
    if (!attach.includes(`is now a child of ${parent}`)) return `it did not attach:
${attach}`
    if (!todo('show', id).includes(`from : ${parent}`)) return 'the card does not show the new parent'
    const clear = todo('edit', id, '--parent-task', '')
    if (!clear.includes('no longer a child')) return `it did not clear:
${clear}`
    return todo('show', id).includes('from :') ? 'the card still shows a parent' : null
  })

  check('a task cannot be made its own parent', () => {
    const id = /Added (\S+):/.exec(add('a self-parent attempt'))?.[1]
    const out = todo('edit', id, '--parent-task', id)
    return /cannot be its own parent/.test(out) ? null : `it allowed it:
${out}`
  })
} finally {
  rmSync(dir, { recursive: true, force: true })
}

process.stdout.write(failures.length ? `\n${failures.length} failed:\n  - ${failures.join('\n  - ')}\n` : '\nAll checks passed.\n')
process.exit(failures.length ? 1 : 0)
