import test from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { existsSync, mkdirSync, writeFileSync, appendFileSync, readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { join, dirname } from 'node:path';
import { resolveBoard, resolveDataDir, userDataRoot, boardKey, readBoard, classify, rulesFrom, diffBoard, findBoard, loadJournal, createMonitor, BoardError, SKILL_DIR, defaults } from '../lib/board.mjs';
import { testingState, testingSummary, changedFields, formatDate } from '../public/board-ui.mjs';
import { project, richProject, tempDir, cleanup, todo, sha, listing, PYTHON } from './helpers.mjs';

test.after(cleanup);
const POLICY = { severities: ['critical', 'high', 'medium', 'low'], statuses: ['backlog', 'in_progress', 'wait_for_roast', 'done', 'dropped'],
  groups: { doing: ['in_progress', 'wait_for_roast'], open: ['backlog'], finished: ['done'], discarded: ['dropped'], other: [], unknown: [] },
  satisfying: ['done'], closed: ['done', 'dropped'], rankLabels: null, docs: {} };

test('board discovery walks up from a subdirectory with spaces to the nearest existing board', () => {
  const p = project();
  const found = resolveBoard({ cwd: p.sub });
  assert.equal(found.dbPath, p.db); assert.equal(found.projectRoot, p.root);
  assert.equal(resolveBoard({ cwd: tempDir(), project: p.sub }).dbPath, p.db, '--project sets where the lookup starts');
  const inner = join(p.root, 'apps', 'web app');
  todo(inner, 'init', '--here');
  assert.equal(resolveBoard({ cwd: inner }).dbPath, join(inner, '.claude', 'todo.db'), 'a nested board wins, as in todo.py');
});

test('no board: refuses without creating anything, and never falls back to another project', () => {
  const empty = tempDir('no board ');
  assert.equal(findBoard(empty), null, 'precondition: no board above the temp folder');
  mkdirSync(join(empty, 'deep', 'er'), { recursive: true });
  const before = listing(empty);
  assert.throws(() => resolveBoard({ cwd: join(empty, 'deep', 'er') }), BoardError);
  assert.deepEqual(listing(empty), before);
  const missing = join(empty, '.claude', 'todo.db');
  assert.throws(() => resolveBoard({ cwd: empty, db: missing }), /never creates/);
  assert.ok(!existsSync(missing) && !existsSync(dirname(missing)));
  assert.throws(() => resolveBoard({ cwd: empty, project: join(empty, 'absent') }), /not an existing directory/);
});

test('history defaults to a per-user cache keyed by board identity, outside the project; --data-dir is honoured', () => {
  const p = richProject(), home = join(tempDir('cache home '), 'c');
  const env = { SIJAV_TODO_DASHBOARD_HOME: home };
  const dir = resolveDataDir({ dbPath: p.db, env });
  assert.equal(dirname(dir), home); assert.ok(!dir.startsWith(p.root), 'never inside the project');
  assert.equal(dir, resolveDataDir({ dbPath: join(p.sub, '..', '..', '.claude', 'todo.db'), env }), 'same board, same folder');
  assert.notEqual(boardKey(p.db), boardKey(project().db), 'different boards, different folders');
  assert.match(boardKey(p.db), /^my-project-[0-9a-f]{16}$/);
  assert.equal(resolveDataDir({ cwd: p.root, dataDir: 'elsewhere', dbPath: p.db }), join(p.root, 'elsewhere'));
  assert.throws(() => resolveDataDir({ dataDir: join(SKILL_DIR, 'dashboard', 'data'), dbPath: p.db }), /inside the installed skill/);
  if (process.platform === 'win32') assert.equal(userDataRoot({ LOCALAPPDATA: 'C:\\L' }), 'C:\\L\\sijav-todo-dashboard');
  const monitor = createMonitor({ dbPath: p.db, dataDir: dir, projectRoot: p.root });
  monitor.close();
  assert.ok(existsSync(join(dir, 'baseline.json'))); assert.ok(!existsSync(join(p.root, '.codex')), 'nothing is written in the project');
});

test('reading preserves every table, row and field exactly as Python sqlite3 reads them, byte-identical, no new files', () => {
  const p = richProject();
  const before = sha(p.db), files = listing(dirname(p.db));
  const board = readBoard(p.db);
  assert.equal(sha(p.db), before); assert.deepEqual(listing(dirname(p.db)), files);
  // An independent reader: Python's sqlite3, not node:sqlite.
  const dump = JSON.parse(execFileSync(PYTHON, ['-c', `
import json, sqlite3, sys
from contextlib import closing
with closing(sqlite3.connect('file:' + sys.argv[1] + '?mode=ro', uri=True)) as c:
    c.row_factory = sqlite3.Row
    out = {}
    for (name,) in c.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        out[name] = [dict(r) for r in c.execute('SELECT * FROM "%s"' % name)]
print(json.dumps(out))`, p.db.replaceAll('\\', '/')], { encoding: 'utf8' }));
  assert.deepEqual(Object.keys(board.tables).sort(), Object.keys(dump).sort(), 'every table is read');
  const canon = rows => rows.map(r => JSON.stringify(Object.fromEntries(Object.entries(r).sort()))).sort();
  for (const name of Object.keys(dump)) assert.deepEqual(canon(board.tables[name]), canon(dump[name]), `${name}: every row and field`);
  const c = classify(board, rulesFrom(POLICY));
  const t = id => c.tasks.find(x => x.id === id);
  assert.equal(t('MP-001').raw.descr, 'Story of Foundation'); assert.equal(t('MP-001').area, 'api'); assert.equal(t('MP-002').phase, 'LATER');
  assert.deepEqual(t('MP-003').unresolved, ['MP-001']); assert.ok(t('MP-003').isWaiting); assert.deepEqual(t('MP-001').dependents, ['MP-003']);
  assert.ok(t('MP-005').isExplicitlyBlocked); assert.equal(t('MP-005').blocked[0].reason, 'Waiting for the owner to choose');
  assert.deepEqual(t('MP-006').children, ['MP-007', 'MP-008']); assert.deepEqual(t('MP-006').openChildren, ['MP-007', 'MP-008']);
  assert.equal(t('MP-006').roasts[0].filed, 'MP-007, MP-008'); assert.equal(t('MP-004').notes[0].text, 'First note\nsecond line');
  assert.equal(c.counts.total, 11); assert.equal(c.counts.blocked, 1); assert.equal(c.counts.waiting, 1);
  assert.equal(c.counts.openFindings, 2); assert.equal(c.counts.finished, 2); assert.equal(c.counts.discarded, 1);
});

test('without the tool’s policy nothing is grouped: no Doing, Done or unfinished counts are claimed', () => {
  const p = richProject();
  const c = classify(readBoard(p.db), rulesFrom(null, 'No Python 3.9+ was found.'));
  assert.equal(c.counts.total, 11);
  for (const key of ['open', 'doing', 'finished', 'complete', 'unfinished', 'waiting']) assert.equal(c.counts[key], null, key);
  assert.ok(c.tasks.every(t => !t.isDoing && !t.isFinished && !t.isWaiting));
  assert.equal(rulesFrom(null, 'why').reason, 'why');
});

test('generic boards: an unused init board and an old-schema board are read without inventing data', () => {
  const p = project();
  const before = sha(p.db), files = listing(dirname(p.db));
  const empty = readBoard(p.db);
  assert.equal(empty.taskTable, null); assert.deepEqual(empty.tasks, []); assert.deepEqual(empty.tables, {});
  assert.equal(sha(p.db), before); assert.deepEqual(listing(dirname(p.db)), files);
  const old = join(tempDir('old board '), '.claude'); mkdirSync(old);
  const db = new DatabaseSync(join(old, 'todo.db'));
  db.exec(`CREATE TABLE task (id TEXT PRIMARY KEY, title TEXT NOT NULL, descr TEXT NOT NULL, why TEXT NOT NULL,
    severity TEXT NOT NULL, points INTEGER NOT NULL, status TEXT NOT NULL, exit_cond TEXT NOT NULL, created TEXT NOT NULL DEFAULT (datetime('now')));
    CREATE TABLE blocked_by (task TEXT NOT NULL, parent TEXT NOT NULL, PRIMARY KEY (task, parent));
    INSERT INTO task (id,title,descr,why,severity,points,status,exit_cond) VALUES ('OB-1','Old task','d','w','high',2,'backlog','an exit that is long enough');`);
  db.close();
  const task = readBoard(join(old, 'todo.db')).tasks[0];
  assert.equal(task.area, null); assert.equal(task.phase, null); assert.equal(task.parentTask, null);
  assert.ok(!Object.hasOwn(task.raw, 'area') && !Object.hasOwn(task.raw, 'evidence'), 'absent columns stay absent');
});

test('an idle WAL board is read without creating -wal/-shm; an active WAL is read with its uncheckpointed rows', () => {
  const p = richProject();
  const writer = new DatabaseSync(p.db); writer.exec('PRAGMA journal_mode=WAL'); writer.close();
  const files = listing(dirname(p.db)), before = sha(p.db);
  readBoard(p.db);
  assert.deepEqual(listing(dirname(p.db)), files); assert.equal(sha(p.db), before);
  const active = new DatabaseSync(p.db);
  try {
    active.exec("PRAGMA wal_autocheckpoint=0; UPDATE task SET title='Only in the WAL' WHERE id='MP-001'");
    const mainBefore = sha(p.db);
    assert.equal(readBoard(p.db).tasks.find(t => t.id === 'MP-001').title, 'Only in the WAL');
    assert.equal(sha(p.db), mainBefore);
  } finally { active.close(); }
});

test('a locked or hot board fails the read explicitly within the busy timeout, without writing', () => {
  const p = richProject(), before = sha(p.db);
  const locker = new DatabaseSync(p.db);
  try {
    locker.exec('BEGIN EXCLUSIVE; UPDATE task SET title = title');
    assert.throws(() => readBoard(p.db, { busyMs: 50 }), /locked|busy/i);
  } finally { locker.exec('ROLLBACK'); locker.close(); }
  assert.equal(sha(p.db), before);
});

test('row-level diffs name the task and every changed field', () => {
  const p = richProject();
  const a = { ...readBoard(p.db), name: 'x' };
  todo(p.root, 'move', 'MP-004', 'in_progress');
  todo(p.root, 'edit', 'MP-004', '--note', 'Added later');
  const b = { ...readBoard(p.db), name: 'x' };
  const changes = diffBoard(a, b, '2026-10-01T00:00:00.000Z');
  const update = changes.find(c => c.table === 'task' && c.itemId === 'MP-004');
  assert.equal(update.kind, 'updated'); assert.deepEqual(update.fields.sort(), ['status', 'updated']);
  assert.equal(update.before.status, 'backlog'); assert.equal(update.after.status, 'in_progress');
  const note = changes.find(c => c.table === 'note');
  assert.equal(note.kind, 'added'); assert.equal(note.after.text, 'Added later');
  assert.deepEqual(diffBoard(b, b), []);
});

test('test states are shown as todo.py recorded them: no column is not recorded, 0 is not tested, 1 is tested; reading writes nothing', () => {
  const p = richProject();
  const counts = c => testingSummary(c, c.tasks, defaults.ui.testingFlags).flags.map(f => [f.key, f.label, f.supported, f.passed, f.pending, f.unknown]);
  const before = classify(readBoard(p.db), rulesFrom(POLICY));
  assert.deepEqual(counts(before), [['tested', 'Tested', false, 0, 0, 2], ['e2e_tested', 'E2E tested', false, 0, 0, 2]],
    'a board that never recorded a test claims nothing about its two done tasks');
  assert.equal(testingState(before.tasks.find(t => t.id === 'MP-010'), 'tested'), 'unknown');
  todo(p.root, 'tested', 'MP-010', '--evidence', 'Ran the area suite: 12 passed');
  const sum = sha(p.db), files = listing(dirname(p.db));
  const after = classify(readBoard(p.db), rulesFrom(POLICY));
  assert.equal(sha(p.db), sum); assert.deepEqual(listing(dirname(p.db)), files, 'reading a board with test states writes nothing');
  assert.deepEqual(counts(after), [['tested', 'Tested', true, 1, 1, 0], ['e2e_tested', 'E2E tested', true, 0, 2, 0]]);
  const t = id => after.tasks.find(x => x.id === id);
  assert.equal(testingState(t('MP-010'), 'tested'), 'passed'); assert.equal(t('MP-010').raw.tested_how, 'Ran the area suite: 12 passed');
  assert.equal(testingState(t('MP-006'), 'tested'), 'pending', 'done is not tested');
  assert.equal(testingState(t('MP-010'), 'e2e_tested'), 'pending', 'tested is not e2e tested');
  assert.equal(testingState(t('MP-004'), 'tested'), 'pending', 'an unfinished task on a board that records tests reads not tested, not unknown');
  const update = diffBoard({ ...before, name: 'x' }, { ...after, name: 'x' }, '2026-10-01T00:00:00.000Z').find(c => c.table === 'task' && c.itemId === 'MP-010');
  assert.deepEqual(update.fields.sort(), ['e2e_at', 'e2e_how', 'e2e_tested', 'tested', 'tested_at', 'tested_how', 'updated']);
  const stamped = changedFields(update.before, update.after, Infinity, defaults.ui.dateFields).find(f => f.key === 'tested_at');
  assert.deepEqual(stamped.rows.map(r => r.text), [formatDate(update.after.tested_at)], 'when it was tested reads as a date, like every other moment');
});

test('a journal with a torn or corrupt line keeps every valid entry and appends cleanly after it', () => {
  const p = richProject(), dataDir = join(tempDir('journal '), 'data');
  let monitor = createMonitor({ dbPath: p.db, dataDir, projectRoot: p.root });
  monitor.close();
  todo(p.root, 'move', 'MP-004', 'in_progress');
  monitor = createMonitor({ dbPath: p.db, dataDir, projectRoot: p.root });
  monitor.close();
  const journal = join(dataDir, 'changes.jsonl');
  const good = loadJournal(journal).entries.length;
  assert.ok(good >= 1);
  appendFileSync(journal, 'this is not json\n{"at":"2026-10-01T00:00:00.000Z","table":"task","itemId":"X","seq":999,"fields":[],"kind":"added","before":null,"after":{}}\n{"truncated":');
  todo(p.root, 'move', 'MP-004', 'done');
  monitor = createMonitor({ dbPath: p.db, dataDir, projectRoot: p.root });
  const snap = monitor.snapshot();
  monitor.close();
  assert.match(snap.persistenceError, /2 unreadable change-history line\(s\) were skipped/);
  const after = loadJournal(journal);
  assert.equal(after.bad.length, 2, 'only the two bad lines are skipped');
  assert.ok(after.entries.length >= good + 2, 'old valid entries, the injected valid one, and the new change are all kept');
  assert.ok(after.entries.some(e => e.itemId === 'MP-004' && e.after?.status === 'done'), 'the new entry was not glued onto the torn line');
  assert.ok(readFileSync(journal, 'utf8').endsWith('\n'));
});
