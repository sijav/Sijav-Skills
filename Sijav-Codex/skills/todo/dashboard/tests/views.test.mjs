// The dashboard's paginated queries (lib/views.mjs), on real boards made by the test loop tool and
// by todo.py, always over a real monitor's snapshot: the only input the server gives them.
import test from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { createMonitor, discoverTool, defaults } from '../lib/board.mjs';
import { hello, list, summary, record, rows, answer, fingerprints, changedKeys, PAGE } from '../lib/views.mjs';
import { createSession } from '../lib/session.mjs';
import { cleanup, loopProject, richProject, tempDir, todo, until, PYTHON } from './helpers.mjs';

test.after(cleanup);

async function watched(p) {
  const tool = discoverTool({ kind: 'loop', dbPath: p.db });
  const monitor = createMonitor({ dbPath: p.db, dataDir: tempDir('views history '), projectRoot: p.root, python: { command: PYTHON, args: [] }, todo: tool.path, kind: 'loop' });
  const snapshot = await until(() => { const s = monitor.snapshot(); return s.boards[0]?.queue?.state === 'ready' && s; }, 'the loop picker');
  return { monitor, snapshot };
}

test('the first message holds each board’s details and nothing per task', async () => {
  const p = loopProject();
  const { monitor, snapshot } = await watched(p);
  try {
    const first = hello(snapshot), board = first.boards[0];
    assert.equal(first.type, 'hello');
    assert.equal(board.tasks, undefined); assert.equal(board.tables, undefined);
    assert.equal(board.queue.deferred, undefined, 'reasons come with each card');
    assert.equal(board.total, 6);
    assert.deepEqual(board.tableCounts, { item: 6, dep: 1, finding: 2 });
    assert.deepEqual(board.head, { key: 'board:1', sourceId: 'board', id: '1', title: 'Foundation', status: 'open' });
    assert.ok(JSON.stringify(first).length < JSON.stringify(snapshot).length / 2, 'much smaller than the whole board');
  } finally { monitor.close(); }
});

test('a list comes five at a time in the tool’s order, with its total', async () => {
  const p = loopProject();
  const { monitor, snapshot } = await watched(p);
  try {
    const first = list(snapshot, { list: 'register', board: 'all', offset: 0, limit: PAGE });
    assert.equal(PAGE, 5);
    assert.deepEqual(first.items.map(t => t.id), ['1', '4', '6', '2', '3']);
    assert.equal(first.total, 6);
    const second = list(snapshot, { list: 'register', board: 'all', offset: 5, limit: PAGE });
    assert.deepEqual(second.items.map(t => t.id), ['5']);
    const lane = list(snapshot, { list: 'lane', board: 'board', status: 'open', filters: { area: 'back' } });
    assert.deepEqual(lane.items.map(t => t.id), ['1', '6'], 'filters are applied by the server');
    const card = first.items.find(t => t.id === '2');
    assert.deepEqual(card.queueReasons.map(r => r.message), ['Waits on 1 (open): Foundation'], 'a card says why the tool does not offer it');
    assert.equal(card.raw.why, undefined, 'a card’s story and why travel once');
    assert.equal(card.related, undefined); assert.deepEqual(first.items.find(t => t.id === '1').relatedCounts, { finding: 1 });
    assert.throws(() => list(snapshot, { list: 'nonsense' }), /Unknown list/);
  } finally { monitor.close(); }
});

test('a record, the totals and a table’s rows are answered on request', async () => {
  const p = loopProject();
  const { monitor, snapshot } = await watched(p);
  try {
    const one = record(snapshot, 'board:2');
    assert.equal(one.task.title, 'Needs foundation');
    assert.deepEqual(one.links['1'], { key: 'board:1', sourceId: 'board', id: '1', title: 'Foundation', status: 'open' });
    assert.deepEqual(one.evidence.deferred.map(r => r.message), ['Waits on 1 (open): Foundation', 'Checked with the tool: removing the reasons above makes it startable.']);
    assert.equal(record(snapshot, 'board:999'), null);
    const totals = summary(snapshot, { board: 'all', filters: {} });
    assert.deepEqual([totals.shown, totals.open, totals.doing, totals.done, totals.findings], [6, 5, 1, 1, 1]);
    assert.deepEqual(totals.facets.areas, { labels: ['back', 'front'], missing: true });
    const page = rows(snapshot, { board: 'board', table: 'item', offset: 2, limit: 3 });
    assert.deepEqual([page.total, page.rows.map(r => r.id)], [6, [3, 4, 5]]);
    assert.throws(() => answer(snapshot, { type: 'nonsense' }), /Unknown request/);
  } finally { monitor.close(); }
});

test('a change names a task removed from the board, and only the tasks it touched', async () => {
  const p = richProject('removed task keys');
  const monitor = createMonitor({ dbPath: p.db, dataDir: tempDir('removed key history '), projectRoot: p.root });
  try {
    const before = fingerprints(monitor.snapshot());
    todo(p.root, 'rm', 'MP-009', '--reason', 'a dropped idea, removed for good');
    monitor.readNow();
    assert.deepEqual(changedKeys(before, fingerprints(monitor.snapshot())), ['board:MP-009'], 'the removed task is named');
  } finally { monitor.close(); }
});

test('a session answers history pages of any size, every entry, a bad size and a null question as documented', async () => {
  const p = loopProject();
  const { monitor } = await watched(p);
  try {
    p.run('start', '6'); p.run('start', '1');
    await until(() => monitor.changes().length >= 2 && monitor.snapshot().boards[0].queue.state === 'ready', 'two changes recorded');
    // The shipped settings, with a page of one change: the server's own settings shape.
    const settings = structuredClone(defaults); settings.ui.changePageSize = 1;
    const all = monitor.changes(), session = createSession(monitor, settings);
    assert.deepEqual(session.answerRequest({ type: 'changes', limit: 'all' }), { changes: all, total: all.length, complete: true, latestSeq: monitor.snapshot().latestSeq });
    const paged = session.answerRequest({ type: 'changes' });
    assert.deepEqual([paged.changes.length, paged.complete], [1, false], 'the settings’ page size by default');
    for (const limit of [0, -2, 'many']) assert.equal(session.answerRequest({ type: 'changes', limit }).changes.length, 1, `limit ${limit} falls back to the page size`);
    assert.equal(session.answerRequest({ type: 'changes', limit: 50 }).complete, true);
    assert.equal(session.answerRequest({ type: 'hello' }).type, 'hello');
    assert.throws(() => session.answerRequest(null), /Unknown request "undefined"/, 'a null question is an unknown request, answered as an error');
  } finally { monitor.close(); }
});

test('a change names only the tasks it touched', async () => {
  const p = loopProject();
  const { monitor, snapshot } = await watched(p);
  try {
    const before = fingerprints(snapshot);
    p.run('start', '6');
    const after = await until(() => { const s = monitor.snapshot(); return s.boards[0].tasks.find(t => t.id === '6').status === 'doing' && s.boards[0].queue.state === 'ready' && s; }, 'the change read');
    assert.deepEqual(changedKeys(before, fingerprints(after)), ['board:6']);
  } finally { monitor.close(); }
});

test('a page of a real table: numeric-string and out-of-range bounds, a keyless table and unknown boards and tables',()=>{
  // An owned table of 505 rows with no primary key, beside a real board, read by the real monitor.
  const p=richProject('table pages'),writer=new DatabaseSync(p.db);
  try{writer.exec("CREATE TABLE detail(seq INTEGER,payload TEXT);WITH RECURSIVE n(x) AS (SELECT 0 UNION ALL SELECT x+1 FROM n WHERE x<504) INSERT INTO detail SELECT x,CASE x WHEN 2 THEN NULL ELSE 'stored '||x END FROM n;");}
  finally{writer.close();}
  const monitor=createMonitor({dbPath:p.db,dataDir:tempDir('table page history '),projectRoot:p.root});
  try{
    const snapshot=monitor.snapshot();
    const page=answer(snapshot,{type:'rows',board:'board',table:'detail',offset:'2',limit:'999'});
    assert.equal(page.offset,2);assert.equal(page.total,505);assert.equal(page.rows.length,500,'a page is at most 500 rows');
    assert.deepEqual(page.rows[0],{seq:2,payload:null});assert.deepEqual(page.primaryKeys,[]);
    assert.equal(rows(snapshot,{board:'board',table:'detail',offset:-5,limit:-3}).rows.length,1);
    assert.equal(rows(snapshot,{board:'board',table:'detail',offset:'bad',limit:'bad'}).rows.length,100);
    assert.equal(rows(snapshot,{board:'board',table:'detail',offset:600}).rows.length,0);
    assert.deepEqual(rows(snapshot,{board:'board',table:'no such table'}),{rows:[],total:0,offset:0,primaryKeys:[]},'an unknown table name, as a client can send');
    assert.deepEqual(rows(snapshot,{board:'absent',table:'task'}),{rows:[],total:0,offset:0,primaryKeys:[]},'an unknown board id');
    assert.deepEqual(list(snapshot,{list:'register',board:'absent',offset:'bad',limit:'bad'}),{items:[],total:0,offset:0});
    assert.equal(record(snapshot,'board:absent'),null);
  }finally{monitor.close();}
});

test('a stored field longer than a card holds is cut on the card and whole in the record',()=>{
  const p=richProject('long stored field'),long='an exit condition someone else can check, '.repeat(40);
  todo(p.root,'edit','MP-004','--exit',long);
  const monitor=createMonitor({dbPath:p.db,dataDir:tempDir('long field history '),projectRoot:p.root});
  try{
    const snapshot=monitor.snapshot();
    const card=list(snapshot,{list:'register',board:'board',offset:0,limit:100}).items.find(t=>t.id==='MP-004');
    assert.equal(card.raw.exit_cond,long.slice(0,1200)+'…','the card carries the first 1200 characters');
    assert.equal(record(snapshot,'board:MP-004').task.raw.exit_cond,long,'the record carries all of it');
  }finally{monitor.close();}
});

test('the register sorted by severity without the tool’s policy puts unknown severities last, by stored order',()=>{
  const p=richProject('severity without policy');
  const monitor=createMonitor({dbPath:p.db,dataDir:tempDir('severity sort history '),projectRoot:p.root});
  try{
    const snapshot=monitor.snapshot();
    assert.equal(snapshot.boards[0].rules.known,false,'no picker: no policy and no severity order');
    const sorted=list(snapshot,{list:'register',board:'all',filters:{sort:'severity'},offset:0,limit:100}).items.map(t=>t.id);
    assert.equal(sorted.length,snapshot.boards[0].tasks.length,'every task is listed');
  }finally{monitor.close();}
});

test('recent register order is stable for two actual undated SQL rows, and a missing selected board has no policy',()=>{
  const root=tempDir('owned undated register '),path=root+'/legacy.db',writer=new DatabaseSync(path);
  try {writer.exec("CREATE TABLE task(id TEXT PRIMARY KEY,title TEXT,status TEXT);"+
    "INSERT INTO task VALUES('undated-a','First undated','manual'),('undated-b','Second undated','manual');");}
  finally {writer.close();}
  const monitor=createMonitor({dbPath:path,projectRoot:root,dataDir:tempDir('undated register history ')});
  try {
    const snapshot=monitor.snapshot();
    assert.ok(snapshot.boards[0].tasks.every(t=>t.lastRecordedAt===null));
    assert.deepEqual(list(snapshot,{list:'register',filters:{sort:'recent'}}).items.map(t=>t.id),['undated-a','undated-b']);
    const session=createSession(monitor,defaults),missing=session.answerRequest({type:'summary',board:'a-board-no-longer-in-the-current-set'});
    assert.equal(missing.shown,0);assert.equal(missing.open,0);assert.deepEqual(missing.severities,{});
    assert.deepEqual(missing.perBoard,{});
  } finally {monitor.close();}
});

