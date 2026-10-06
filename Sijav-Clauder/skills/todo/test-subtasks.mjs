// Drives the real todo skill through the parent-and-children cycle.
//
// Against a throwaway project directory, so nothing here can touch a real
// board. The skill walks UP for a board that already exists and refuses to
// invent one, so the sandbox creates its own explicitly with `init --here`.

import { spawnSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync, readFileSync } from 'node:fs'
import { DatabaseSync } from 'node:sqlite'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

// The todo.mjs beside this test: the skill ships in the plugin, not under ~/.claude/skills.
const SKILL = join(dirname(fileURLToPath(import.meta.url)), 'todo.mjs')
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

  // Test states (RE-173). A finding is part of its parent's story: an open one
  // clears a tested parent, keeping what it had in a note, and nothing ever
  // marks a parent tested on its children's behalf. Read from the board itself,
  // not from what the tool says about it.
  const board = join(dir, '.claude', 'todo.db')
  const stateOf = (id) => {
    const db = new DatabaseSync(board, { readOnly: true })
    try {
      const row = db.prepare('SELECT tested, e2e_tested FROM task WHERE id = ?').get(id)
      const notes = db.prepare('SELECT text FROM note WHERE task = ? ORDER BY rowid').all(id).map((note) => note.text)
      return { tested: row?.tested, e2e: row?.e2e_tested, last: notes.at(-1) }
    } finally {
      db.close()
    }
  }
  const idOf = (out) => /Added (\S+):/.exec(out)?.[1]
  const doneAndTested = (title, evidence, path) => {
    const id = idOf(add(title))
    todo('move', id, 'done')
    todo('tested', id, '--evidence', evidence)
    if (path) todo('e2e', id, '--evidence', path)
    return id
  }

  check('a parent with an open finding is not tested, and a tested finding never marks it', () => {
    const top = idOf(add('a parent with work left'))
    todo('move', top, 'done')
    const kid = idOf(add('its open finding', ['--parent-task', top]))
    const refused = todo('tested', top, '--evidence', 'the parent suite')
    if (!refused.includes(`${top} has open findings: ${kid}. Nothing was recorded.`)) return `it was tested anyway:\n${refused}`
    todo('move', kid, 'done')
    todo('tested', kid, '--evidence', 'the finding suite')
    const parent = stateOf(top)
    if (parent.tested !== 0 || parent.e2e !== 0) return `the parent reads ${JSON.stringify(parent)} after its finding was tested`
    return stateOf(kid).tested === 1 ? null : 'the finding itself was not recorded as tested'
  })

  check('a new open finding clears its tested parent, keeps what it had, and leaves a tested sibling alone', () => {
    const top = doneAndTested('a parent tested end to end', 'suite one', 'path one')
    const sibling = idOf(add('a finding already fixed', ['--parent-task', top, '--status', 'done']))
    todo('tested', sibling, '--evidence', 'the sibling suite')
    if (stateOf(top).tested !== 1) return 'a finding filed already done cleared the parent'
    const out = add('a late finding', ['--parent-task', top])
    if (!out.includes(`${top}'s test state is cleared: it has an open finding now.`)) return `it did not say so:\n${out}`
    const parent = stateOf(top)
    if (parent.tested !== 0 || parent.e2e !== 0) return `the parent still reads ${JSON.stringify(parent)}`
    const kid = idOf(out)
    if (parent.last !== `test state cleared: ${kid} is an open finding of it; it had tested: suite one; e2e tested: path one`) {
      return `the note says ${JSON.stringify(parent.last)}`
    }
    return stateOf(sibling).tested === 1 ? null : 'clearing the parent cleared its tested finding too'
  })

  check('a finding filed dropped clears nothing', () => {
    const top = doneAndTested('a parent with a dropped finding', 'suite two')
    add('a finding dropped at once', ['--parent-task', top, '--status', 'dropped'])
    return stateOf(top).tested === 1 ? null : 'a dropped finding cleared the parent'
  })

  check('reopening a finding clears it and its parent', () => {
    const top = idOf(add('a parent whose finding reopens'))
    todo('move', top, 'done')
    const kid = idOf(add('a finding that reopens', ['--parent-task', top]))
    todo('move', kid, 'done')
    todo('tested', kid, '--evidence', 'the finding suite')
    todo('tested', top, '--evidence', 'the parent suite')
    todo('move', kid, 'backlog')
    const child = stateOf(kid)
    const parent = stateOf(top)
    if (child.tested !== 0 || child.last !== 'test state cleared: status done -> backlog; it had tested: the finding suite') {
      return `the finding reads ${JSON.stringify(child)}`
    }
    if (parent.tested !== 0 || parent.last !== `test state cleared: ${kid}, a finding of it, is open again; it had tested: the parent suite`) {
      return `the parent reads ${JSON.stringify(parent)}`
    }
    return null
  })

  check('a grandchild hangs off the top parent and clears it', () => {
    const top = idOf(add('a parent with a grandchild'))
    todo('move', top, 'done')
    const kid = idOf(add('a closed finding', ['--parent-task', top, '--status', 'done']))
    todo('tested', top, '--evidence', 'the top suite')
    const out = add('a grandchild', ['--parent-task', kid])
    if (!out.includes(`is itself a child of ${top}`)) return `it did not flatten:\n${out}`
    return stateOf(top).tested === 0 ? null : 'the top parent kept its test state'
  })

  check('filing an open card under a tested task with edit clears it', () => {
    const top = doneAndTested('a parent a card is filed under', 'suite three')
    const loose = idOf(add('a loose card'))
    const out = todo('edit', loose, '--parent-task', top)
    if (!out.includes(`${top}'s test state is cleared: it has an open finding now.`)) return `it did not say so:\n${out}`
    const parent = stateOf(top)
    return parent.tested === 0 && parent.last === `test state cleared: ${loose} was filed under it as an open finding; it had tested: suite three`
      ? null
      : `the parent reads ${JSON.stringify(parent)}`
  })

  check('a writer that never heard of test states moving a tested task leaves no stale claim', () => {
    const id = doneAndTested('a task an older tool moves', 'suite four')
    // What the tool before test states runs for `move <id> backlog`.
    const writer = new DatabaseSync(board)
    try {
      writer.prepare('UPDATE task SET status = ?, updated = ? WHERE id = ?').run('backlog', new Date().toISOString(), id)
    } finally {
      writer.close()
    }
    const state = stateOf(id)
    return state.tested === 0 && state.last === 'test state cleared: status done -> backlog; it had tested: suite four'
      ? null
      : `it reads ${JSON.stringify(state)}`
  })

  check('a finding and its parent each keep their own evidence', () => {
    const top = idOf(add('a parent with its own proof'))
    todo('move', top, 'done')
    const kid = idOf(add('a finding with its own proof', ['--parent-task', top, '--status', 'done']))
    todo('tested', kid, '--evidence', 'the finding proof')
    todo('tested', top, '--evidence', 'the parent proof')
    const db = new DatabaseSync(board, { readOnly: true })
    try {
      const how = (id) => db.prepare('SELECT tested_how FROM task WHERE id = ?').get(id).tested_how
      return how(top) === 'the parent proof' && how(kid) === 'the finding proof' ? null : `the parent reads ${how(top)}, the finding ${how(kid)}`
    } finally {
      db.close()
    }
  })

  check('a writer that never heard of test states filing, refiling or reopening a finding leaves no stale claim', () => {
    const filed = doneAndTested('a parent an older tool files under', 'suite five')
    const refiled = doneAndTested('a parent an older tool refiles a card under', 'suite six')
    const reopened = idOf(add('a parent whose finding an older tool reopens'))
    todo('move', reopened, 'done')
    const closed = idOf(add('a closed finding', ['--parent-task', reopened, '--status', 'done']))
    todo('tested', reopened, '--evidence', 'suite seven')
    const loose = idOf(add('a loose card an older tool refiles'))
    // What the tool before test states runs for add --parent-task, edit --parent-task and move.
    const writer = new DatabaseSync(board)
    try {
      writer
        .prepare("INSERT INTO task (id, title, descr, why, severity, points, status, exit_cond, parent_task) VALUES (?, 't', 'd', 'w', 'low', 1, 'backlog', 'e', ?)")
        .run('OLD-1', filed)
      writer.prepare('UPDATE task SET parent_task = ? WHERE id = ?').run(refiled, loose)
      writer.prepare("UPDATE task SET status = 'backlog' WHERE id = ?").run(closed)
    } finally {
      writer.close()
    }
    for (const [parent, said] of [
      [filed, 'test state cleared: OLD-1 is an open finding of it; it had tested: suite five'],
      [refiled, `test state cleared: ${loose} was filed under it as an open finding; it had tested: suite six`],
      [reopened, `test state cleared: ${closed}, a finding of it, is open again; it had tested: suite seven`],
    ]) {
      const state = stateOf(parent)
      if (state.tested !== 0 || state.last !== said) return `${parent} reads ${JSON.stringify(state)}`
    }
    return null
  })

  check('blocker diamonds retain shared edges, refuse direct/indirect cycles and replace only requested parents', () => {
    const invoke=(...args)=>spawnSync(process.execPath,[SKILL,...args],{cwd:dir,encoding:'utf8'});
    const output=r=>String(r.stdout||'')+String(r.stderr||'');
    for(const id of ['GRAPH-A','GRAPH-B','GRAPH-C','GRAPH-D']) {
      const added=invoke('add','--id',id,'--title',id,'--desc','d','--why','w','--severity','high','--points','1','--exit','e');
      if(added.status!==0)return output(added);
    }
    for(const [id,parents] of [['GRAPH-B','GRAPH-A'],['GRAPH-C','GRAPH-A'],['GRAPH-D','GRAPH-B,GRAPH-C']]) {
      const edited=invoke('edit',id,'--parent',parents);if(edited.status!==0)return output(edited);
    }
    const bytes=()=>readFileSync(board).toString('base64'),before=bytes();
    for(const parent of ['GRAPH-A','GRAPH-D']) {
      const rejected=invoke('edit','GRAPH-A','--parent',parent);
      if(rejected.status!==1||!/makes a cycle/.test(output(rejected)))return 'cycle was not refused: '+output(rejected);
      if(bytes()!==before)return 'a refused cycle changed the original board bytes';
    }
    const replaced=invoke('edit','GRAPH-D','--parent','GRAPH-C');if(replaced.status!==0)return output(replaced);
    const readParents=()=>{const db=new DatabaseSync(board,{readOnly:true});try{return db.prepare('SELECT parent FROM blocked_by WHERE task=? ORDER BY parent').all('GRAPH-D').map(row=>row.parent);}finally{db.close();}};
    if(JSON.stringify(readParents())!==JSON.stringify(['GRAPH-C']))return 'replacement kept an old or lost a requested blocker';
    const cleared=invoke('edit','GRAPH-D','--parent','');if(cleared.status!==0)return output(cleared);
    return readParents().length===0?null:'clearing retained blocker rows';
  })
} finally {
  rmSync(dir, { recursive: true, force: true })
}

process.stdout.write(failures.length ? `\n${failures.length} failed:\n  - ${failures.join('\n  - ')}\n` : '\nAll checks passed.\n')
process.exit(failures.length ? 1 : 0)
