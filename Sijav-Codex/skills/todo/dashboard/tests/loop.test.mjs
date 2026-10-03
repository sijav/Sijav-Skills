// Loop boards: an SQLite file with item and dep tables, driven by its own tool.
// Every board here is made by the test loop tool (tests/loop-fixture) in the OS temp
// folder, through that tool's own commands, and every folder is removed afterwards.
import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { BoardError, discoverTool, findBoard, kindOf, readBoard, resolveBoard } from '../lib/board.mjs';
import { describe, startDashboard } from '../server.mjs';
import { PYTHON, STAGE, TODO_PY, cleanEnv, cleanup, listing, loopProject, richProject, sha, tempDir, testEnv, todo, until } from './helpers.mjs';

test.after(cleanup);

/** An SQLite file that is not a board: one unrelated table. */
function otherDatabase() {
  const path = join(tempDir('not a board '), 'notes.db');
  const db = new DatabaseSync(path);
  db.exec('CREATE TABLE notes (text TEXT)');
  db.close();
  return path;
}

test('a loop board is told apart by its tables, and any other SQLite file is refused, never shown as an empty board', () => {
  assert.equal(kindOf({}), 'todo', 'a board the to-do tool has not filled yet');
  assert.equal(kindOf({ task: ['id'] }), 'todo');
  assert.equal(kindOf({ item: ['id', 'title', 'status', 'severity', 'priority'], dep: ['item', 'blocker'] }), 'loop');
  assert.equal(kindOf({ item: ['id', 'title'], dep: ['item', 'blocker'] }), null, 'an item table without status, severity and priority');
  assert.equal(kindOf({ notes: ['text'] }), null);
  const other = otherDatabase();
  assert.throws(() => resolveBoard({ cwd: dirname(other), db: other }), e => e instanceof BoardError && /is not a to-do board this dashboard reads/.test(e.message));
  assert.throws(() => readBoard(other), /is not a to-do board this dashboard reads/);
  const cli = spawnSync(process.execPath, [join(STAGE, 'server.mjs'), '--db', other, '--port', '0'], { cwd: dirname(other), encoding: 'utf8', env: testEnv(), timeout: 30000 });
  assert.equal(cli.status, 2, cli.stderr);
  assert.match(cli.stderr, /is not a to-do board this dashboard reads/);
});

test('a loop board is named with --db, never guessed, and the page is named after the project folder it sits in', () => {
  const p = loopProject();
  assert.equal(findBoard(p.dir), null, 'only .claude/todo.db is found by walking up');
  assert.throws(() => resolveBoard({ cwd: p.dir }), /No board\..*a loop board is opened with --db/s);
  const fromProject = resolveBoard({ cwd: p.root, db: 'loop/board.db' });
  assert.deepEqual([fromProject.kind, fromProject.dbPath, fromProject.projectRoot], ['loop', p.db, p.root]);
  const fromItsFolder = resolveBoard({ cwd: p.dir, db: 'board.db' });
  assert.deepEqual([fromItsFolder.kind, fromItsFolder.dbPath, fromItsFolder.projectRoot], ['loop', p.db, p.dir]);
  const elsewhere = resolveBoard({ cwd: tempDir(), db: p.db });
  assert.equal(elsewhere.projectRoot, p.dir, 'started outside it, the project is the board’s own folder');
  assert.equal(discoverTool({ kind: 'loop', dbPath: p.db }).path, p.tool, 'the tool named after the board, beside it');
  const alone = join(tempDir('board alone '), 'other.db');
  const missing = discoverTool({ kind: 'loop', dbPath: alone });
  assert.equal(missing.path, null);
  assert.match(missing.reason, /other\.py, named after the board, was not found beside it.*Pass --tool/);
});

test('readBoard maps loop items: story, why, exit command, blockers, parked notes and their findings', () => {
  const p = loopProject();
  const before = sha(p.db), files = listing(p.dir);
  const board = readBoard(p.db);
  assert.equal(board.kind, 'loop');
  assert.equal(board.taskTable, 'item');
  assert.deepEqual(board.checkedStatuses, ['open', 'doing', 'closed']);
  assert.deepEqual(board.checkedSeverities, ['critical', 'high', 'medium', 'low']);
  assert.equal(board.tableDefaults.finding.status, 'open', 'a finding is open while it keeps the table’s default status');
  const t = id => board.tasks.find(task => task.id === String(id));
  assert.deepEqual(board.tasks.map(task => task.id), ['1', '2', '3', '4', '5', '6']);
  assert.deepEqual([t(1).description, t(1).why, t(1).exit, t(1).points], ['Story of Foundation', 'Why Foundation', 'check foundation', 3]);
  assert.deepEqual(board.tasks.map(task => task.area), ['back', null, null, 'front', null, 'back'], 'each item’s stored area');
  assert.deepEqual(t(2).dependencies, ['1']);
  assert.deepEqual(t(1).dependents, ['2']);
  assert.deepEqual(t(3).blocked.map(b => b.reason), ['Owner decides the wording']);
  assert.deepEqual(t(1).related.finding.map(f => [f.text, f.status]), [['First finding', 'open']]);
  assert.deepEqual(t(5).related.finding.map(f => [f.text, f.status]), [['Second finding', 'resolved']]);
  assert.equal(t(1).createdAt, new Date(1700000000 * 1000).toISOString());
  assert.equal(t(5).closedAt, new Date(1700000100 * 1000).toISOString());
  assert.equal(sha(p.db), before, 'reading never writes the board');
  assert.deepEqual(listing(p.dir), files, 'reading creates nothing beside the board');
});

test('loop_picker.py gives the tool’s own order and status meaning from a copy, and never writes the board', () => {
  const p = loopProject();
  assert.equal(p.run('next'), 'NEXT 1 Foundation', 'the tool’s own answer');
  const before = sha(p.db), files = listing(p.dir);
  const out = JSON.parse(execFileSync(PYTHON, ['-B', join(STAGE, 'loop_picker.py'), '--db', p.db, '--tool', p.tool],
    { cwd: tempDir(), encoding: 'utf8', env: cleanEnv({ PYTHONDONTWRITEBYTECODE: '1' }) }));
  assert.equal(out.error, undefined, out.error);
  assert.equal(out.kind, 'loop');
  assert.equal(out.headId, '1', 'the same head as the tool’s next');
  assert.deepEqual(out.startableIds, ['1', '4', '6']);
  assert.deepEqual(out.rankedIds, ['1', '4', '6', '2', '3'], 'startable in board_order(), then the items it cannot start');
  assert.deepEqual(out.policy.groups, { doing: ['doing'], open: ['open'], finished: ['closed'], discarded: [], other: [], unknown: [] });
  assert.deepEqual(out.policy.satisfying, ['closed']);
  assert.equal(out.policy.freshStatus, 'open');
  assert.deepEqual(out.policy.severities, ['critical', 'high', 'medium', 'low'], 'from the tool’s SEV_RANK');
  const reasons = id => out.deferred[id].map(r => r.message);
  assert.deepEqual(reasons('2'), ['Waits on 1 (open): Foundation', 'Checked with the tool: removing the reasons above makes it startable.']);
  assert.deepEqual(reasons('3'), ['Parked: Owner decides the wording', 'Checked with the tool: removing the reasons above makes it startable.']);
  assert.match(out.nextText, /^-> 1 /m);
  assert.match(out.nextText, /^ {3}2 /m, 'a waiting item is listed without the startable mark');
  assert.match(out.policy.docs.board_order, /Every open item in order/);
  assert.equal(sha(p.db), before, 'the board is never written');
  assert.deepEqual(listing(p.dir), files, 'nothing is created beside the board');
});

test('loop_picker.py refuses a tool whose order it cannot read, instead of guessing one', () => {
  const p = loopProject();
  const empty = join(dirname(p.tool), 'empty_tool.py');
  writeFileSync(empty, 'VALUE = 1\n');
  const run = spawnSync(PYTHON, ['-B', join(STAGE, 'loop_picker.py'), '--db', p.db, '--tool', empty], { cwd: tempDir(), encoding: 'utf8', env: cleanEnv({ PYTHONDONTWRITEBYTECODE: '1' }) });
  assert.equal(run.status, 1);
  assert.match(JSON.parse(run.stdout).error, /neither board_order\(\) nor open_items\(\) and sort_key\(\)/);
});

test('the server reads a loop board live: its tool, order, open findings and a change, with the board untouched', { timeout: 120000 }, async () => {
  const p = loopProject();
  const started = await startDashboard({ db: p.db }, { cwd: p.root, env: testEnv() });
  try {
    const ready = () => { const b = started.monitor.snapshot().boards[0]; return b.queue?.state === 'ready' && b; };
    const board = await until(ready, 'the loop picker');
    const s = started.monitor.snapshot();
    assert.deepEqual([s.server.kind, s.server.tool, s.server.project], ['loop', 'board.py', p.root]);
    assert.equal(board.queue.headId, '1');
    assert.equal(board.counts.openFindings, 1, 'one finding still has the table’s default status');
    assert.equal(board.counts.findings, 2);
    assert.equal(board.counts.doing, 1);
    assert.deepEqual([board.counts.blocked, board.counts.waiting], [1, 1], 'the parked item and the item whose blocker is open');
    const text = describe(started);
    assert.match(text, /a loop board; --db/);
    assert.match(text, /Picker: +board\.py /);
    p.run('close', '1', '--did', 'Laid the foundation', '--at', '1700000200');
    const after = await until(() => { const b = ready(); return b && b.queue.headId === '2' && b; }, 'the change and the new head');
    assert.equal(after.tasks.find(t => t.id === '1').status, 'closed');
  } finally { await started.close(); }
  const files = listing(p.dir);
  assert.ok(files.every(name => ['board.db', 'board.py', 'tool'].includes(name)), 'nothing but the tool’s own files: ' + files.join(', '));
});

test('an older loop board (no parked, exit or created columns) and a tool with only open_items(), blocked_ids() and sort_key()', () => {
  const dir = join(tempDir('older loop '), 'older project');
  mkdirSync(dir);
  const db = new DatabaseSync(join(dir, 'board.db'));
  db.exec(`CREATE TABLE item (id INTEGER PRIMARY KEY, title TEXT NOT NULL, severity TEXT NOT NULL CHECK (severity IN ('high','low')),
    priority INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'todo' CHECK (status IN ('todo','done')));
    CREATE TABLE dep (item INTEGER NOT NULL, blocker INTEGER NOT NULL);
    INSERT INTO item (title, severity, priority) VALUES ('A', 'low', 0), ('B', 'high', 1);
    INSERT INTO item (title, severity, priority, status) VALUES ('C', 'high', 0, 'done');
    INSERT INTO dep VALUES (1, 2);`);
  db.close();
  writeFileSync(join(dir, 'board.py'), [
    'SEV_RANK = {"high": 0, "low": 1}',
    'def open_items(conn):',
    '    return conn.execute("SELECT id,title,severity,priority FROM item WHERE status!=\'done\'").fetchall()',
    'def blocked_ids(conn):',
    '    return {r[0] for r in conn.execute("SELECT d.item FROM dep d JOIN item b ON b.id=d.blocker WHERE b.status!=\'done\'")}',
    'def sort_key(row):',
    '    return (SEV_RANK[row[2]], row[3], row[0])', ''].join('\n'));
  const before = sha(join(dir, 'board.db'));
  const out = JSON.parse(execFileSync(PYTHON, ['-B', join(STAGE, 'loop_picker.py'), '--db', join(dir, 'board.db'), '--tool', join(dir, 'board.py')],
    { cwd: tempDir(), encoding: 'utf8', env: cleanEnv({ PYTHONDONTWRITEBYTECODE: '1' }) }));
  assert.equal(out.error, undefined, out.error);
  assert.deepEqual([out.headId, out.startableIds, out.rankedIds], ['2', ['2'], ['2', '1']]);
  assert.deepEqual(out.policy.groups, { doing: [], open: ['todo'], finished: ['done'], discarded: [], other: [], unknown: [] });
  assert.deepEqual(out.deferred['1'].map(r => r.message), ['Waits on 2 (todo): B', 'Checked with the tool: removing the reasons above makes it startable.']);
  const board = readBoard(join(dir, 'board.db'));
  assert.deepEqual([board.kind, board.tasks.find(t => t.id === '1').blocked, board.tasks.find(t => t.id === '1').exit], ['loop', [], null]);
  assert.equal(sha(join(dir, 'board.db')), before);
});

test('two boards on one page: each read with its own tool and history, one numbering of changes', { timeout: 120000 }, async () => {
  const loop = loopProject(), board = richProject('todo project');
  const started = await startDashboard({ db: loop.db, dbs: [loop.db, board.db, loop.db], todoPy: TODO_PY }, { cwd: loop.root, env: testEnv() });
  try {
    const ready = () => { const s = started.monitor.snapshot(); return s.boards.length === 2 && s.boards.every(b => b.queue?.state === 'ready') && s; };
    const s = await until(ready, 'both pickers');
    assert.deepEqual(s.boards.map(b => [b.id, b.boardKind, b.tool]), [['board', 'loop', 'board.py'], ['board-2', 'todo', 'todo.py']], 'the same board given twice is one board');
    assert.equal(s.boards[0].queue.headId, '1', 'the loop board keeps its own order');
    assert.match(todo(board.root, 'next'), new RegExp(s.boards[1].queue.headId), 'the to-do board keeps its own');
    const keys = s.boards.flatMap(b => b.tasks.map(t => t.key));
    assert.equal(new Set(keys).size, keys.length, 'no two tasks share a key');
    assert.ok(s.boards[0].tasks.every(t => t.key === 'board:' + t.id) && s.boards[1].tasks.every(t => t.key === 'board-2:' + t.id));
    assert.deepEqual(s.server.boards.map(x => x.kind), ['loop', 'todo']);
    const text = describe(started);
    assert.match(text, /Boards: +2, on one page/);
    assert.match(text, /a loop board; --db/);
    assert.match(text, /the to-do skill's board; --db/);

    todo(board.root, 'move', 'MP-004', 'in_progress');
    loop.run('start', '6');
    const changed = await until(() => {
      const list = started.monitor.changes();
      return list.some(c => c.sourceId === 'board-2' && c.itemId === 'MP-004') && list.some(c => c.sourceId === 'board' && c.itemId === '6') && list;
    }, 'a change on each board');
    const seqs = changed.map(c => c.seq);
    assert.equal(new Set(seqs).size, seqs.length, 'every change has its own number');
    assert.deepEqual(seqs, seqs.slice().sort((a, b) => b - a), 'newest first');
    const own = started.monitor.changes({ item: 'board-2:MP-004' });
    assert.ok(own.length > 0 && own.every(c => c.sourceId === 'board-2' && c.itemId === 'MP-004'), 'one task\'s history, by its key');
    assert.equal(started.monitor.changes({ after: changed[0].seq }).length, 0);
    const after = await until(() => { const x = ready(); return x && x.boards[1].queue.headId === 'MP-004' && x; }, 'the to-do board\'s new pick');
    assert.equal(after.boards[0].tasks.find(t => t.id === '6').status, 'doing');
  } finally { await started.close(); }
  assert.ok(listing(loop.dir).every(name => ['board.db', 'board.py', 'tool'].includes(name)), 'nothing created beside the loop board');
});

test('--tool is refused when several loop boards are shown, each of which uses its own', async () => {
  const a = loopProject('loop a'), b = loopProject('loop b');
  await assert.rejects(startDashboard({ db: a.db, dbs: [a.db, b.db], tool: a.tool }, { cwd: a.root, env: testEnv() }), /several loop boards were given/);
});
