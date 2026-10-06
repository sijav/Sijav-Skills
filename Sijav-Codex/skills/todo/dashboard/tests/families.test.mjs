// The dashboard's remaining reader, monitor and startup paths, each from the producer that has it:
// the exported board functions on boards made by todo.py, the loop tool or owned SQL, the public
// monitor over real files and real picker children, and startDashboard/describe on canonical boards.
// One case is labelled where it stands: a programmatic caller passing an address the CLI refuses, to
// reach the kernel's own listen refusal.
import test from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { mkdirSync, writeFileSync, renameSync, readFileSync, copyFileSync } from 'node:fs';
import { createServer } from 'node:net';
import { join, dirname } from 'node:path';
import * as board from '../lib/board.mjs';
import { startDashboard, describe } from '../server.mjs';
import { relax } from '../lib/views.mjs';
import { renderAmbientModel } from '../public/ambient-ui.mjs';
import { richProject, loopProject, project, tempDir, cleanup, todo, testEnv, TODO_PY, PYTHON, until } from './helpers.mjs';

test.after(cleanup);
const python = { command: PYTHON, args: [] };
/** A port free on this machine now, for a dashboard asked to use a fixed one. */
const freePort = () => new Promise((ok, fail) => {
  const server = createServer();
  server.once('error', fail);
  server.listen(0, '127.0.0.1', () => { const { port } = server.address(); server.close(() => ok(port)); });
});

test('resolution and discovery refuse what they cannot use, by name', () => {
  const p = richProject('families resolution');
  assert.throws(() => board.resolveBoard({ cwd: p.root, db: dirname(p.db) }), /is not a file, so it cannot be a board/);
  const missing = join(tempDir('families python '), 'python.exe');
  const found = board.discoverPython(null, { SIJAV_TODO_PYTHON: missing });
  assert.equal(found.command, null);
  assert.match(found.reason, /^No Python 3\.9\+ was found \(tried .*python\.exe\)\. Pass --python <path> or set SIJAV_TODO_PYTHON\.$/);
  const loop = loopProject('families tool');
  assert.equal(board.discoverTool({ explicit: loop.tool, cwd: loop.root, kind: 'loop', dbPath: loop.db }).path, loop.tool);
  assert.match(board.discoverTool({ explicit: 'missing.py', cwd: loop.root, kind: 'loop', dbPath: loop.db }).reason, /^--tool .*missing\.py does not exist\.$/);
});

test('a loop board without a finding table and without a tool, a corrupt baseline, a long torn journal and a folder with no Latin letters', () => {
  // An owned loop board with its item and dep tables only, in a folder named in Arabic-Indic digits.
  const root = join(tempDir('families loop '), '٣٣'), path = join(root, 'board.db');
  mkdirSync(root, { recursive: true });
  const db = new DatabaseSync(path);
  try {
    db.exec(`CREATE TABLE item(id INTEGER PRIMARY KEY,title TEXT NOT NULL,status TEXT NOT NULL,severity TEXT NOT NULL,priority INTEGER NOT NULL DEFAULT 0);
      CREATE TABLE dep(item INTEGER NOT NULL,blocker INTEGER NOT NULL);INSERT INTO item VALUES(1,'only item','open','high',0);`);
  } finally { db.close(); }
  const dataDir = tempDir('families history ');
  writeFileSync(join(dataDir, 'baseline.json'), '{ not json');
  writeFileSync(join(dataDir, 'changes.jsonl'), Array.from({ length: 12 }, (_, i) => 'torn line ' + i).join('\n') + '\n');
  const monitor = board.createMonitor({ dbPath: path, projectRoot: root, dataDir, kind: 'loop' });
  try {
    const s = monitor.snapshot(), b = s.boards[0];
    assert.equal(b.tool, "the board's tool", 'a loop board with no tool found names its tool generically');
    assert.equal(b.short, 'TD', 'a folder name with no Latin letter or digit');
    assert.match(s.persistenceError, /12 unreadable change-history line\(s\) were skipped \(line 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, …\)/);
    assert.doesNotThrow(() => JSON.parse(readFileSync(join(dataDir, 'baseline.json'), 'utf8')), 'the unreadable baseline was started again from this read');
    assert.equal(b.counts.findings, 0, 'no finding table: no findings, not an error');
    assert.equal(b.counts.openFindings, 0);
  } finally { monitor.close(); }
});

test('a change to a loop board’s finding row is named for its item', () => {
  const loop = loopProject('families loop diff'), before = board.readBoard(loop.db);
  loop.run('resolve', '1');
  const change = board.diffBoard(before, board.readBoard(loop.db)).find(c => c.table === 'finding');
  assert.equal(change.isTask, false); assert.equal(change.itemId, '1'); assert.deepEqual(change.fields, ['status']);
});

test('a picker that does not answer in time is stopped and says so', async () => {
  const p = richProject('families timeout');
  await assert.rejects(board.runPicker({ python, todo: TODO_PY, dbPath: p.db, settings: { timeoutMs: 1, maxBufferBytes: 1 << 20 } }),
    /The picker timed out or was stopped\./);
});

test('a tool that changes and then fails to load: the old policy is withdrawn with the reason', async () => {
  const p = richProject('families tool change'), tool = join(tempDir('families tool '), 'todo.py');
  copyFileSync(TODO_PY, tool);
  const monitor = board.createMonitor({ dbPath: p.db, projectRoot: p.root, dataDir: tempDir('families tool history '), python, todo: tool });
  try {
    assert.equal((await monitor.settled()).boards[0].queue.state, 'ready');
    writeFileSync(tool, readFileSync(tool, 'utf8') + '\ndef deliberately_invalid(:\n');
    const s = await monitor.recheck();
    assert.equal(s.boards[0].queue.state, 'error');
    assert.equal(s.boards[0].rules.known, false);
    assert.match(s.boards[0].rules.reason, /^todo\.py changed and could not be read: .*SyntaxError/);
  } finally { monitor.close(); }
});

test('a board file replaced by a loop board under a to-do monitor is refused until a restart', () => {
  const p = richProject('families kind change'), loop = loopProject('families kind loop');
  const monitor = board.createMonitor({ dbPath: p.db, projectRoot: p.root, dataDir: tempDir('families kind history ') });
  try {
    assert.equal(monitor.snapshot().boards[0].available, true);
    copyFileSync(loop.db, p.db);
    monitor.readNow();
    const b = monitor.snapshot().boards[0];
    assert.equal(b.available, false); assert.equal(b.stale, true);
    assert.match(b.error, /^The board file is now a loop board, not the to-do skill's board\. Restart the dashboard/);
  } finally { monitor.close(); }
});

test('a fixed port on the IPv6 loopback is served and described as given', async () => {
  const p = richProject('families fixed port'), port = await freePort();
  const live = await startDashboard({ db: p.db, todoPy: TODO_PY, port, host: '::1' }, { cwd: p.root, env: testEnv() });
  try {
    assert.equal(live.url, `http://[::1]:${port}/`);
    assert.match(describe(live), new RegExp(`^URL:        http://\\[::1\\]:${port}/$`, 'm'), 'a fixed port says nothing about changing');
    assert.equal((await fetch(live.url + 'api/health')).status, 200);
  } finally { await live.close(); }
});

test('several boards on a fixed port, publishing through a relay, one of them a loop board whose tool is missing', async () => {
  const p = richProject('families several'), loop = loopProject('families toolless');
  renameSync(loop.tool, loop.tool + '.aside');
  const dir = tempDir('families relay '), token = join(dir, 'synthetic-token.txt');
  writeFileSync(token, 'synthetic-local-token\n');
  const port = await freePort(), relayPort = await freePort(); // nothing listens on the relay's port
  const live = await startDashboard({ dbs: [p.db, loop.db], todoPy: TODO_PY, port, publish: `ws://127.0.0.1:${relayPort}/progress`, publishTokenFile: token },
    { cwd: p.root, env: testEnv() });
  try {
    const text = describe(live);
    assert.match(text, new RegExp(`^URL:        http://127\\.0\\.0\\.1:${port}/$`, 'm'));
    assert.match(text, new RegExp(`^Publish:    ws://127\\.0\\.0\\.1:${relayPort}/progress · `, 'm'));
    assert.match(text, /^Picker:     unavailable · board\.py, named after the board, was not found beside it/m);
    assert.match(text, /for board\.db, board\.db-wal, board\.db-shm, board\.db-journal$/m, 'no tool watch where there is no tool');
  } finally { await live.close(); renameSync(loop.tool + '.aside', loop.tool); }
  renameSync(loop.tool, loop.tool + '.aside');
  const solo = await startDashboard({ db: loop.db }, { cwd: loop.root, env: testEnv() });
  try {
    const text = describe(solo);
    assert.match(text, /^Picker:     unavailable · /m); assert.doesNotMatch(text, /tool watch|; tool /);
  } finally { await solo.close(); renameSync(loop.tool + '.aside', loop.tool); }
});

// Labelled: startDashboard is a public API with no host check of its own (the CLI's parseArgs has it), so
// a programmatic caller can pass an address that is not this machine's; listen then fails in the kernel,
// and that error is the caller's, not a port-in-use refusal.
test('a listen failure other than a port in use is the caller’s own error, and nothing is left watching', async () => {
  const p = richProject('families listen refusal');
  await assert.rejects(startDashboard({ db: p.db, todoPy: TODO_PY, host: '192.0.2.1' }, { cwd: p.root, env: testEnv() }),
    error => !(error instanceof board.BoardError) && /EADDRNOTAVAIL|EINVAL|EACCES/.test(String(error.code ?? error.message)));
});

test('Relax over real boards: the one eligible task with nothing after it, and a preview on a board with no area field', async () => {
  const one = project('families relax one ');
  todo(one.root, 'add', '--id', 'ON-001', '--title', 'The only task', '--desc', 'd', '--why', 'w', '--severity', 'high', '--points', '1', '--exit', 'a long enough exit condition');
  const monitor = board.createMonitor({ dbPath: one.db, projectRoot: one.root, dataDir: tempDir('families relax history '), python, todo: TODO_PY });
  try {
    const html = renderAmbientModel(relax(await monitor.settled()));
    assert.match(html, /No other eligible task in this result\./);
  } finally { monitor.close(); }
  // A board older than the area column: its queue preview names no area at all.
  const root = join(tempDir('families relax old '), 'old project'), path = join(root, '.claude', 'todo.db');
  mkdirSync(dirname(path), { recursive: true });
  const db = new DatabaseSync(path);
  try {
    db.exec(`CREATE TABLE task (id TEXT PRIMARY KEY, title TEXT NOT NULL, descr TEXT NOT NULL, why TEXT NOT NULL,
      severity TEXT NOT NULL, points INTEGER NOT NULL, status TEXT NOT NULL, exit_cond TEXT NOT NULL, created TEXT NOT NULL DEFAULT (datetime('now')));
      CREATE TABLE blocked_by (task TEXT NOT NULL, parent TEXT NOT NULL, PRIMARY KEY (task, parent));
      INSERT INTO task (id,title,descr,why,severity,points,status,exit_cond) VALUES
        ('OB-1','First','d','w','high',1,'backlog','long enough exit condition'),('OB-2','Second','d','w','high',2,'backlog','long enough exit condition');`);
  } finally { db.close(); }
  const old = board.createMonitor({ dbPath: path, projectRoot: root, dataDir: tempDir('families relax old history '), python, todo: TODO_PY });
  try {
    const snapshot = await old.settled(), model = relax(snapshot);
    assert.equal(model.boards[0].queued[0].support.areas.length, 0, 'the board has no area field');
    assert.match(renderAmbientModel(model), /<li><code>OB-2<\/code><span dir="auto">Second<\/span><small><\/small><\/li>/);
  } finally { old.close(); }
});
