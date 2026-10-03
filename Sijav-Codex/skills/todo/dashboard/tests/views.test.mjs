// The dashboard's paginated queries (lib/views.mjs), on a real loop board made by the test loop tool.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createMonitor, discoverTool } from '../lib/board.mjs';
import { hello, list, summary, record, rows, answer, fingerprints, changedKeys, PAGE } from '../lib/views.mjs';
import { cleanup, loopProject, tempDir, until, PYTHON } from './helpers.mjs';

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
