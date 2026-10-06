// Stored SQLite values and application-defined key collations, on a synthetic
// owner only. These later native checks decide whether the ordering fallback
// is necessary; no application callback is installed in either reader.
import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { startDashboard } from '../server.mjs';
import { readBoard, rowsDigest, runPicker, discoverPython } from '../lib/board.mjs';
import { richProject, TODO_PY, PYTHON, sha, listing, cleanup, testEnv, until } from './helpers.mjs';
import { dirname } from 'node:path';

test.after(cleanup);
const python = discoverPython(PYTHON);
const pick = db => runPicker({ dbPath: db, todo: TODO_PY, python });
const writeFixture = (db, program) => execFileSync(PYTHON, ['-B', '-c', program, db], { env: testEnv(), encoding: 'utf8', timeout: 30000 });

test('BLOB, infinities and integers beyond the JSON safe range agree between canonical Node and Python reads', async () => {
  const p = richProject('stored value contract');
  writeFixture(p.db, `import sqlite3,sys
from contextlib import closing
with closing(sqlite3.connect(sys.argv[1])) as conn, conn:
    conn.executescript("ALTER TABLE task ADD COLUMN legacy_payload BLOB; UPDATE task SET legacy_payload=X'0001ff' WHERE id='MP-001'; CREATE TABLE legacy_values(id INTEGER PRIMARY KEY, bytes BLOB, positive REAL, negative REAL, wide INTEGER); INSERT INTO legacy_values VALUES(1,X'00abff',9e999,-9e999,9007199254740993); INSERT INTO legacy_values VALUES(2,X'00abff',9e999,-9e999,-9007199254740993);")
`);
  const before = sha(p.db), names = listing(dirname(p.db));
  const node = readBoard(p.db), result = await pick(p.db);
  assert.equal(rowsDigest(result.tables), node.rowsDigest);
  assert.deepEqual(result.tables.legacy_values, [
    [1, { $blob: '00abff' }, { $float: 'inf' }, { $float: '-inf' }, '9007199254740993'],
    [2, { $blob: '00abff' }, { $float: 'inf' }, { $float: '-inf' }, '-9007199254740993']
  ]);
  assert.deepEqual(node.tasks.find(task => task.id === 'MP-001').raw.legacy_payload, { $blob: '0001ff' });
  assert.equal(result.headId, 'MP-001');
  assert.equal(sha(p.db), before); assert.deepEqual(listing(dirname(p.db)), names);
});

test('a key collation unavailable to both readers falls back to stored rows and still serves the canonical dashboard', async () => {
  const p = richProject('custom collation contract');
  writeFixture(p.db, `import sqlite3,sys
from contextlib import closing
with closing(sqlite3.connect(sys.argv[1])) as conn, conn:
    conn.create_collation('board_fold',lambda a,b:(a.casefold()>b.casefold())-(a.casefold()<b.casefold()))
    conn.executescript("CREATE TABLE custom_order(k TEXT PRIMARY KEY COLLATE board_fold,v TEXT); INSERT INTO custom_order VALUES('z','inserted first'); INSERT INTO custom_order VALUES('a','inserted second'); CREATE TABLE ordinary_order(k TEXT PRIMARY KEY,v TEXT); INSERT INTO ordinary_order VALUES('z','inserted first'); INSERT INTO ordinary_order VALUES('a','inserted second');")
`);
  const before = sha(p.db), names = listing(dirname(p.db));
  const node = readBoard(p.db), result = await pick(p.db);
  assert.deepEqual(result.tables.custom_order, [['z', 'inserted first'], ['a', 'inserted second']], 'the fallback reads stored order');
  assert.deepEqual(result.tables.ordinary_order, [['a', 'inserted second'], ['z', 'inserted first']], 'ordinary primary keys still sort');
  assert.equal(rowsDigest(result.tables), node.rowsDigest, 'the two native SQLite readers saw exactly the same rows');
  const live = await startDashboard({ db: p.db, todoPy: TODO_PY }, { env: testEnv() });
  try {
    const ready = await until(async () => {
      const snap = await (await fetch(live.url + 'api/snapshot')).json();
      return snap.boards[0]?.queue.state === 'ready' && snap;
    }, 'the canonical collation-board picker');
    assert.equal(ready.boards[0].queue.headId, 'MP-001');
    assert.equal(ready.boards[0].tables.custom_order.length, 2);
    assert.equal((await fetch(live.url + 'api/health')).status, 200);
  } finally { await live.close(); }
  assert.equal(sha(p.db), before); assert.deepEqual(listing(dirname(p.db)), names);
});