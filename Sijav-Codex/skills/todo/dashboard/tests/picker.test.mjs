import test from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { existsSync, mkdirSync, writeFileSync, readFileSync, copyFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { runPicker, createMonitor, discoverPython, discoverTodo, rowsDigest, readBoard } from '../lib/board.mjs';
import { buildAmbientModel } from '../public/ambient-ui.mjs';
import { project, richProject, duplicateBoard, tempDir, cleanup, todo, todoWith, sha, listing, TODO_PY, PYTHON, until } from './helpers.mjs';

test.after(cleanup);
const python = discoverPython(PYTHON);
const pick = (db, tool = TODO_PY) => runPicker({ python, todo: tool, dbPath: db });
const cardId = text => text.split('\n').find(line => /^\S+\s+\[/.test(line))?.split(/\s+/)[0] ?? null;
/** A modified copy of todo.py in its own temp folder, for tests of how changes to the tool are handled. */
function toolCopy(edit) {
  const path = join(tempDir('tool copy '), 'todo.py');
  const source = readFileSync(TODO_PY, 'utf8'), changed = edit(source);
  assert.notEqual(changed, source, 'the edit applied');
  writeFileSync(path, changed);
  return path;
}
const nl = source => source.includes('\r\n') ? '\r\n' : '\n';

test('the picker prints exactly what the real todo.py next prints, on the same snapshot, without writing', async () => {
  const p = richProject();
  const before = sha(p.db), files = listing(dirname(p.db)), projectFiles = listing(p.root);
  const result = await pick(p.db);
  assert.equal(sha(p.db), before); assert.deepEqual(listing(dirname(p.db)), files); assert.deepEqual(listing(p.root), projectFiles);
  const real = todo(duplicateBoard(p.db), 'next');
  assert.equal(result.nextText, real); assert.equal(result.headId, cardId(real));
  assert.equal(rowsDigest(result.tables), readBoard(p.db).rowsDigest, 'picker and dashboard read the same snapshot');
  assert.deepEqual(result.eligibleIds, ['MP-001', 'MP-007', 'MP-004', 'MP-1000', 'MP-008', 'MP-002']);
  assert.deepEqual(result.rankedIds.slice(-2).sort(), ['MP-003', 'MP-005']);
  assert.match(result.deferred['MP-003'][0].message, /Waits on MP-001 \(backlog\)/);
  assert.match(result.deferred['MP-005'][0].message, /Blocked: Waiting for the owner to choose/);
  for (const id of ['MP-003', 'MP-005']) assert.equal(result.deferred[id].at(-1).verified, true, id + ': reasons confirmed by a todo.py probe');
  assert.deepEqual(result.children['MP-006'], { all: ['MP-007', 'MP-008'], open: ['MP-007', 'MP-008'] });
  assert.equal(result.currentPhase.name, 'MVP');
  const policy = result.policy;
  assert.deepEqual(policy.groups.doing, ['in_progress', 'wait_for_roast']); assert.deepEqual(policy.groups.open, ['backlog']);
  assert.deepEqual(policy.groups.finished, ['done']); assert.deepEqual(policy.groups.discarded, ['dropped']);
  assert.deepEqual(policy.satisfying, ['done']); assert.deepEqual(policy.closed.sort(), ['done', 'dropped']);
  assert.deepEqual(policy.rankLabels, ['phase_rank(task)', "SEVERITIES.index(task['severity'])", "task['points']", "id_number(task['id'])", "task['id']"]);
  assert.deepEqual((await pick(p.db)).rankedIds, result.rankedIds, 'stable order on an unchanged board');
});

test('at every step of working the board, the pick and its text match the real next', async () => {
  const p = richProject(), work = duplicateBoard(p.db);
  const db = join(work, '.claude', 'todo.db');
  let steps = 0;
  for (; steps < 40; steps++) {
    const result = await pick(db), real = todo(work, 'next');
    assert.equal(result.nextText, real, `step ${steps}`);
    assert.equal(result.headId, cardId(real), `step ${steps}`);
    if (steps === 3) todo(work, 'move', 'MP-005', 'backlog');
    if (steps === 6) todo(work, 'okr', 'done', 'MVP');
    if (!result.headId) break;
    if (result.headKind === 'eligible') {
      todo(work, 'move', result.headId, 'in_progress');
      const started = await pick(db);
      assert.equal(started.headId, result.headId); assert.equal(started.headKind, 'started');
      assert.equal(started.nextText, todo(work, 'next'));
    }
    todo(work, 'move', result.headId, 'done', '--evidence', 'fixture step');
  }
  assert.ok(steps >= 8, 'the walk covered the board');
  assert.equal((await pick(db)).nextText, 'Nothing left.\n');
});

test('in-progress precedence, waiting parents and a blocked started task follow the tool', async () => {
  const p = richProject();
  todo(p.root, 'move', 'MP-008', 'in_progress');
  todo(p.root, 'move', 'MP-004', 'wait_for_roast');
  todo(p.root, 'move', 'MP-003', 'in_progress');
  todo(p.root, 'move', 'MP-1000', 'in_progress');
  todo(p.root, 'move', 'MP-1000', 'blocked', '--reason', 'Paused by owner');
  const result = await pick(p.db);
  assert.equal(result.nextText, todo(duplicateBoard(p.db), 'next'));
  assert.deepEqual(result.startedIds, ['MP-003', 'MP-004', 'MP-008']);
  assert.equal(result.headId, 'MP-003'); assert.equal(result.headKind, 'started');
  assert.ok(result.rankedIds.includes('MP-1000') && !result.startableIds.includes('MP-1000'));
  assert.match(result.deferred['MP-1000'][0].message, /Paused by owner/);
});

test('a blocked task with a done parent and an unfinished parent: reasons are exact and confirmed, as the real tool resolves them (N2)', async () => {
  const p = richProject();
  todo(p.root, 'add', '--id', 'MP-020', '--title', 'Two parents, blocked', '--desc', 'd', '--why', 'w', '--severity', 'critical', '--points', '1',
    '--exit', 'a long enough exit condition', '--parent', 'MP-006,MP-001');
  todo(p.root, 'move', 'MP-020', 'blocked', '--reason', 'Waiting for a decision');
  const result = await pick(p.db);
  assert.equal(result.nextText, todo(duplicateBoard(p.db), 'next'));
  const reasons = result.deferred['MP-020'];
  assert.deepEqual(reasons.map(r => r.kind), ['blocked', 'parent', 'check']);
  assert.match(reasons[0].message, /Waiting for a decision/);
  assert.match(reasons[1].message, /^Waits on MP-001 \(backlog\)/, 'only the unfinished parent is a reason; done MP-006 is not');
  assert.equal(reasons[2].verified, true, 'the check counts the already-done parent MP-006 as done');
  assert.match(reasons[2].message, /resolving the reasons above makes it pickable/);
  // The real helper, on an isolated duplicate: resolve exactly those reasons, and the task becomes pickable.
  const work = duplicateBoard(p.db), db = join(work, '.claude', 'todo.db');
  todo(work, 'move', 'MP-001', 'done', '--evidence', 'fixture');
  todo(work, 'move', 'MP-020', 'backlog'); // any other move clears the block
  const resolved = await pick(db);
  assert.equal(resolved.nextText, todo(work, 'next'));
  assert.ok(resolved.eligibleIds.includes('MP-020'), 'the real tool’s choose() now offers it');
  todo(work, 'move', 'MP-003', 'blocked', '--reason', 'set aside so MP-020 is next');
  assert.match(todo(work, 'next'), /^NEXT[^\n]*\n\nMP-020 /, 'the real `todo.py next` picks it once the only critical/1pt peer with a lower id is set aside');
  assert.equal(resolved.deferred['MP-020'], undefined);
});

test('legacy rows the tool never ranks (an unknown severity, a legacy status) do not cost a valid pick', async () => {
  const root = join(tempDir('legacy rows '), 'legacy project'); mkdirSync(join(root, '.claude'), { recursive: true });
  const path = join(root, '.claude', 'todo.db'), db = new DatabaseSync(path);
  db.exec(`CREATE TABLE task (id TEXT PRIMARY KEY, title TEXT NOT NULL, descr TEXT NOT NULL, why TEXT NOT NULL, severity TEXT NOT NULL,
      points INTEGER NOT NULL, status TEXT NOT NULL, exit_cond TEXT NOT NULL, created TEXT NOT NULL DEFAULT (datetime('now')));
    CREATE TABLE blocked_by (task TEXT NOT NULL, parent TEXT NOT NULL, PRIMARY KEY (task, parent));
    CREATE TABLE blocked (task TEXT PRIMARY KEY, reason TEXT NOT NULL, since TEXT NOT NULL);
    INSERT INTO task (id,title,descr,why,severity,points,status,exit_cond) VALUES
      ('LG-1','Normal low','d','w','low',1,'backlog','long enough exit condition'),
      ('LG-2','Normal high','d','w','high',3,'backlog','long enough exit condition'),
      ('LG-3','Legacy urgent, blocked','d','w','urgent',1,'backlog','long enough exit condition'),
      ('LG-4','Legacy status','d','w','urgent',2,'open','long enough exit condition');
    INSERT INTO blocked VALUES ('LG-3','legacy block','2026-01-01T00:00:00.000Z');`);
  db.close();
  const before = sha(path);
  const result = await pick(path);
  assert.equal(sha(path), before);
  assert.equal(result.nextText, todo(duplicateBoard(path), 'next'), 'same as the real tool');
  assert.equal(result.headId, 'LG-2'); assert.deepEqual(result.eligibleIds, ['LG-2', 'LG-1']);
  assert.ok(result.rankErrors['LG-3'] && result.rankErrors['LG-4'], 'by_rule failures are recorded per task');
  assert.deepEqual(result.rankedIds.slice(2).sort(), ['LG-3', 'LG-4'], 'unrankable tasks are listed, not dropped');
  assert.ok(result.policy.groups.other.includes('open'), 'a legacy status is probed, not guessed');
  assert.match(result.deferred['LG-4'].map(r => r.message).join(' '), /Status open: todo.py next does not offer/);
});

test('policy is read from the tool: a changed by_rule and a changed choose() change order, labels and groups', async () => {
  const p = richProject();
  todo(p.root, 'add', '--id', 'MP-011', '--title', 'Large medium', '--desc', 'd', '--why', 'w', '--severity', 'medium', '--points', '8', '--exit', 'a long enough exit condition');
  const descending = toolCopy(s => s.replace(new RegExp('        task\\["points"\\],' + nl(s)), '        -task["points"],' + nl(s)));
  const result = await pick(p.db, descending);
  const work = duplicateBoard(p.db);
  assert.equal(result.nextText, todoWith(descending, work, 'next'), 'matches the modified tool');
  assert.ok(result.policy.rankLabels.includes("-task['points']"));
  assert.notDeepEqual(result.eligibleIds, (await pick(p.db)).eligibleIds, 'the order follows the tool, not a constant');
  todo(p.root, 'move', 'MP-004', 'wait_for_roast');
  const noRoastWait = toolCopy(s => s.replace('("in_progress", "wait_for_roast") and not is_blocked', '("in_progress",) and not is_blocked'));
  const changed = await pick(p.db, noRoastWait);
  assert.deepEqual(changed.policy.groups.doing, ['in_progress']);
  assert.ok(changed.policy.groups.other.includes('wait_for_roast'));
  assert.equal(changed.nextText, todoWith(noRoastWait, duplicateBoard(p.db), 'next'));
  assert.ok(!changed.startedIds.includes('MP-004'));
});

test('an unknown start-up change to the tool is refused explicitly instead of keeping old constants', async () => {
  const p = richProject();
  const computed = toolCopy(s => s.replace('POINTS = [1, 2, 3, 5, 8, 13]', 'POINTS = [1, 2, 3, 5, 8, 13]' + nl(s) + 'SEVERITIES = list(reversed(SEVERITIES))'));
  await assert.rejects(pick(p.db, computed), /computes SEVERITIES with a call/);
  const mutated = toolCopy(s => s.replace('POINTS = [1, 2, 3, 5, 8, 13]', 'POINTS = [1, 2, 3, 5, 8, 13]' + nl(s) + 'POINTS.append(21)'));
  await assert.rejects(pick(p.db, mutated), /uses or changes POINTS outside a function/);
  const pure = toolCopy(s => s.replace('POINTS = [1, 2, 3, 5, 8, 13]', 'POINTS = [1, 2, 3, 5, 8, 13]' + nl(s) + 'POINTS = POINTS + [21]'));
  assert.ok((await pick(p.db, pure)).policy.points.includes(21), 'a pure expression is reproduced, not dropped');
  const fake = join(tempDir('fake tool '), 'todo.py');
  writeFileSync(fake, 'import sqlite3\nSEVERITIES = ["high"]\ndef all_tasks():\n    return []\n');
  await assert.rejects(pick(p.db, fake), /does not define .*choose/);
});

test('the tool’s code only ever sees an isolated copy: writes beside BOARD or in its cwd never reach the project', async () => {
  const p = richProject(), before = sha(p.db), files = listing(p.root), boardFiles = listing(dirname(p.db));
  const leaky = toolCopy(s => s.replace('elif command == "next":' + nl(s), 'elif command == "next":' + nl(s)
    + '    open(os.path.join(os.path.dirname(BOARD), "leak.txt"), "w").write(BOARD)' + nl(s) + '    open("cwd-leak.txt", "w").write(os.getcwd())' + nl(s)));
  const result = await pick(p.db, leaky);
  assert.ok(result.headId);
  assert.deepEqual(listing(p.root), files); assert.deepEqual(listing(dirname(p.db)), boardFiles); assert.equal(sha(p.db), before);
  assert.ok(!existsSync(join(dirname(leaky), 'cwd-leak.txt')));
});

test('an old-schema board and an unused init board are evaluated on a copy only', async () => {
  const root = join(tempDir('old schema '), 'legacy project'); mkdirSync(join(root, '.claude'), { recursive: true });
  const path = join(root, '.claude', 'todo.db'), db = new DatabaseSync(path);
  db.exec(`CREATE TABLE task (id TEXT PRIMARY KEY, title TEXT NOT NULL, descr TEXT NOT NULL, why TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('critical','high','medium','low')), points INTEGER NOT NULL,
    status TEXT NOT NULL, exit_cond TEXT NOT NULL, created TEXT NOT NULL DEFAULT (datetime('now')));
    CREATE TABLE blocked_by (task TEXT NOT NULL, parent TEXT NOT NULL, PRIMARY KEY (task, parent));
    INSERT INTO task (id,title,descr,why,severity,points,status,exit_cond) VALUES
      ('LG-1','Low','d','w','low',1,'backlog','long enough exit condition'),('LG-2','High','d','w','high',3,'backlog','long enough exit condition'),
      ('LG-3','Waits','d','w','critical',1,'backlog','long enough exit condition');
    INSERT INTO blocked_by VALUES ('LG-3','LG-2');`);
  db.close();
  const before = sha(path), files = listing(dirname(path));
  const result = await pick(path);
  assert.equal(sha(path), before); assert.deepEqual(listing(dirname(path)), files);
  assert.deepEqual(result.eligibleIds, ['LG-2', 'LG-1']);
  for (const table of ['phase', 'blocked', 'note', 'roast']) assert.ok(result.schemaAddedOnCopy.map(a => a.table).includes(table), table);
  assert.equal(result.nextText, todo(duplicateBoard(path), 'next'));
  const p = project(), unused = sha(p.db);
  const empty = await pick(p.db);
  assert.equal(empty.boardHadTaskTable, false); assert.equal(empty.headId, null); assert.equal(sha(p.db), unused);
});

test('the monitor publishes the picker result for the snapshot it read, and explains a missing picker', async () => {
  const p = richProject(), dataDir = join(tempDir('monitor '), 'data');
  const before = sha(p.db);
  const monitor = createMonitor({ dbPath: p.db, dataDir, projectRoot: p.root, python, todo: TODO_PY });
  try {
    const snap = await until(async () => { const s = await monitor.settled(); return s.boards[0].queue.state === 'ready' && s; }, 'picker result');
    const board = snap.boards[0];
    assert.equal(board.queue.headId, 'MP-001'); assert.equal(board.rules.known, true);
    assert.equal(board.queue.nextText, todo(duplicateBoard(p.db), 'next'));
    assert.equal(buildAmbientModel(snap).boards[0].head.id, 'MP-001');
  } finally { monitor.close(); }
  assert.equal(sha(p.db), before);
  assert.match(discoverTodo(join(p.root, 'nope', 'todo.py')).reason, /does not exist/);
  const noPython = discoverPython(join(p.root, 'no-python.exe'));
  assert.equal(noPython.command, null); assert.match(noPython.reason, /not a runnable Python/);
  const bare = createMonitor({ dbPath: p.db, dataDir: join(tempDir(), 'data'), projectRoot: p.root, python: null, todo: null, pickerReason: noPython.reason });
  try {
    const board = bare.snapshot().boards[0];
    assert.equal(board.queue.state, 'unconfigured'); assert.equal(board.rules.known, false); assert.match(board.rules.reason, /not a runnable Python/);
    assert.equal(buildAmbientModel(bare.snapshot()).boards[0].head, null);
    assert.equal(board.tasks.length, 11); assert.equal(board.counts.doing, null, 'no Doing count without the tool');
  } finally { bare.close(); }
});
