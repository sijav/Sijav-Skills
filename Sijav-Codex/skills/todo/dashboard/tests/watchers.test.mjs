// Native watcher lifecycle and the documented watch-factory boundary.
import test from 'node:test';
import assert from 'node:assert/strict';
import { watch as fsWatch, copyFileSync, rmSync, utimesSync, existsSync, writeFileSync, appendFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { startDashboard, createApp, describe } from '../server.mjs';
import { createMonitor, discoverPython, readBoard } from '../lib/board.mjs';
import { richProject, tempDir, cleanup, testEnv, sha, TODO_PY, PYTHON, until } from './helpers.mjs';
import { WebSocket, WebSocketServer } from 'ws';

test.after(cleanup);
const python = discoverPython(PYTHON);
const copyTool = () => {
  const folder = tempDir('native watcher tool '), file = join(folder, 'todo.py');
  copyFileSync(TODO_PY, file); return { folder, file };
};
const pieces = (monitor, p, tool, dataDir) => ({ monitor, board: { projectRoot: p.root, dbPath: p.db, kind: 'todo', how: 'synthetic board' },
  dataDir, todo: { path: tool }, python, pickerReason: null, url: 'http://127.0.0.1/', fixedPort: false });

for (const boundary of ['board', 'tool']) test(`a synchronous EMFILE at the ${boundary} watch boundary keeps read-only serving and policy intact`, async () => {
  const p = richProject(`EMFILE ${boundary}`), tool = copyTool(), before = sha(p.db), dataDir = tempDir('watch refusal history ');
  const failedFolder = boundary === 'board' ? dirname(p.db) : tool.folder;
  // Normal factory injection: every other watcher is the real fs.watch. This
  // checks a deterministic startup failure and makes no native-EPERM claim.
  const watch = (folder, listener) => {
    if (resolve(folder) === resolve(failedFolder)) throw Object.assign(new Error(`EMFILE: too many open files, watch '${folder}'`), { code: 'EMFILE' });
    return fsWatch(folder, listener);
  };
  const monitor = createMonitor({ dbPath: p.db, projectRoot: p.root, dataDir, todo: tool.file, python, watch });
  const server = createApp(monitor, { WebSocket, WebSocketServer });
  try {
    await new Promise((ok, fail) => { server.once('error', fail); server.listen(0, '127.0.0.1', ok); });
    const snap = await monitor.settled();
    assert.equal(snap.watchState[boundary].active, false);
    assert.match(snap.watchState[boundary].error, /EMFILE/);
    assert.equal(snap.watchState[boundary === 'board' ? 'tool' : 'board'].active, true);
    assert.equal(snap.boards[0].queue.state, 'ready');
    assert.equal(snap.boards[0].tasks.length, 11);
    assert.match(describe(pieces(monitor, p, tool.file, dataDir)), /NOT ACTIVE: .*EMFILE/);
    const health = await fetch(`http://127.0.0.1:${server.address().port}/api/health`);
    assert.equal(health.status, 200);
    const body = await health.json();
    assert.equal(body.readOnly, true); assert.equal(body.watchState[boundary].active, false);
    assert.deepEqual(readBoard(p.db).tasks.map(task => task.id), snap.boards[0].tasks.map(task => task.id));
  } finally { monitor.close(); await new Promise(ok => server.close(ok)); }
  assert.equal(sha(p.db), before);
});

for (const boundary of ['board', 'tool']) test(`Windows native removal of the ${boundary} directory ends that watch and leaves health readable`, {
  skip: process.platform !== 'win32' ? 'Windows native directory-removal scenario requires native Windows' : false,
}, async () => {
  const p = richProject(`native removal ${boundary}`), tool = copyTool(), before = sha(p.db);
  const events = [], handles = new Map(), dataDir = tempDir('native removal history ');
  // Pass through fs.watch; no emitted error or synthetic event supplies native proof.
  const watch = (folder, listener) => {
    const handle = fsWatch(folder, (event, name) => {
      events.push({ folder: resolve(folder), event, name: name == null ? null : String(name) });
      listener(event, name);
    });
    handles.set(resolve(folder), handle);
    handle.on('error', error => events.push({ folder: resolve(folder), error: error.code, message: error.message }));
    handle.on('close', () => events.push({ folder: resolve(folder), closed: true }));
    return handle;
  };
  const monitor = createMonitor({ dbPath: p.db, projectRoot: p.root, dataDir, todo: tool.file, python, watch });
  const server = createApp(monitor, { WebSocket, WebSocketServer });
  await new Promise((ok, fail) => { server.once('error', fail); server.listen(0, '127.0.0.1', ok); });
  const live = { ...pieces(monitor, p, tool.file, dataDir), url: `http://127.0.0.1:${server.address().port}/`,
    close: async () => { monitor.close(); await new Promise(ok => server.close(ok)); } };
  try {
    const ready = await live.monitor.settled();
    assert.equal(ready.boards[0].queue.state, 'ready');
    assert.equal(ready.watchState[boundary].active, true);
    const target = boundary === 'board' ? dirname(p.db) : tool.folder;
    // Targets are the just-created synthetic owners, resolved before deletion.
    assert.equal(resolve(target), resolve(boundary === 'board' ? join(p.root, '.claude') : tool.folder));
    assert.equal(sha(p.db), before, 'the dashboard wrote nothing before the deliberate fixture deletion');
    assert.ok(handles.has(resolve(target)), 'the actual native watcher was created for this exact directory');
    rmSync(target, { recursive: true });
    assert.equal(existsSync(target), false, 'the synthetic watched directory really was removed');
    const snap = await until(() => {
      const value = live.monitor.snapshot();
      return value.watchState[boundary].error && value;
    }, `native ${boundary} watcher error`);
    assert.equal(snap.watchState[boundary].active, false);
    await until(() => events.some(event => event.folder === resolve(target) && event.closed), 'native watcher close');
    const native = events.filter(event => event.folder === resolve(target));
    console.log(JSON.stringify({ scenario: `native removal ${boundary}`, node: process.versions.node, uv: process.versions.uv, events: native }));
    // Native removal can name the watched directory or the contained file.
    // Its actual absence, inactive state and real close are independently required.
    if (native.some(event => event.error === 'EPERM')) assert.match(snap.watchState[boundary].error, /EPERM/);
    else {
      const removedFile = boundary === 'board' ? p.db : tool.file;
      assert.ok(native.some(event => event.event === 'rename' && event.name != null
        && (resolve(event.name) === resolve(target) || resolve(target, event.name) === resolve(removedFile))),
        'the raw native rename identifies the removed directory or its contained file');
      assert.match(snap.watchState[boundary].error, /Watched directory removed:/);
    }
    assert.match(describe(live), /NOT ACTIVE: /);
    const health = await fetch(live.url + 'api/health');
    assert.equal(health.status, 200);
    const body = await health.json();
    assert.equal(body.readOnly, true); assert.equal(body.watchState[boundary].active, false);
    if (boundary === 'tool') assert.equal(sha(p.db), before);
  } finally { await live.close(); }
});

test('a real tool file event with unchanged bytes does not rerun a current picker', async () => {
  const p = richProject('unchanged tool event'), tool = copyTool(), events = [];
  const watch = (folder, listener) => fsWatch(folder, (event, name) => {
    listener(event, name);
    if (resolve(folder) === resolve(tool.folder) && (name == null || String(name) === 'todo.py')) events.push({ event, name });
  });
  const monitor = createMonitor({ dbPath: p.db, projectRoot: p.root, dataDir: tempDir('unchanged tool history '),
    todo: tool.file, python, watch });
  try {
    const before = await monitor.settled(), bytes = sha(tool.file), boardBytes = sha(p.db);
    assert.equal(before.boards[0].queue.state, 'ready');
    const changedTime = new Date(Date.now() + 2000);
    utimesSync(tool.file, changedTime, changedTime);
    await until(() => events.length, 'the actual tool fs.watch event');
    const after = await monitor.settled();
    assert.equal(sha(tool.file), bytes);
    assert.equal(after.boards[0].queue.checkedAt, before.boards[0].queue.checkedAt);
    assert.equal(after.boards[0].queue.tool.sha256, before.boards[0].queue.tool.sha256);
    assert.equal(after.boards[0].queue.state, 'ready');
    assert.equal(sha(p.db), boardBytes);
  } finally { monitor.close(); }
});

test('a native tool-file event publishes changed identity while the board remains unreadable', async () => {
  const p = richProject('unavailable board tool event'), tool = copyTool(), events = [], pushes = [];
  const watch = (folder, listener) => {
    // Labelled EMFILE at the board boundary prevents an unrelated board event
    // from supplying the tool-event assertion; the tool uses actual fs.watch.
    if (resolve(folder) === resolve(dirname(p.db))) throw Object.assign(new Error('EMFILE: controlled board watch refusal'), { code: 'EMFILE' });
    return fsWatch(folder, (event, name) => {
      events.push({ folder: resolve(folder), event, name: name == null ? null : String(name) });
      listener(event, name);
    });
  };
  const monitor = createMonitor({ dbPath: p.db, projectRoot: p.root, dataDir: tempDir('unavailable tool history '),
    todo: tool.file, python, watch });
  let unsubscribe = () => {};
  try {
    const ready = await monitor.settled();
    assert.equal(ready.boards[0].queue.state, 'ready');assert.equal(ready.watchState.tool.active, true);
    assert.match(ready.watchState.board.error, /EMFILE/);
    // Only this newly created synthetic owner is deliberately damaged.
    writeFileSync(p.db, 'deliberately unreadable synthetic SQLite board');
    monitor.readNow();
    const unavailable = await monitor.settled(), boardBytes = sha(p.db), oldTool = sha(tool.file), history = monitor.changes();
    assert.equal(unavailable.boards[0].available, false);assert.equal(unavailable.boards[0].queue.state, 'unavailable');
    unsubscribe = monitor.subscribe(value => pushes.push(value));
    appendFileSync(tool.file, '\n# owned native tool event while board unavailable\n');
    const changedTool = sha(tool.file);assert.notEqual(changedTool, oldTool);
    await until(() => events.some(event => event.folder === resolve(tool.folder)
      && (event.name == null || event.name === 'todo.py')), 'the actual changed-tool fs.watch event');
    await until(() => pushes.some(value => value.server.toolSha === changedTool), 'the tool event publishes its changed byte identity');
    const after = await monitor.settled();
    assert.ok(after.revision > unavailable.revision);
    assert.equal(after.server.toolSha, changedTool);assert.equal(after.boards[0].available, false);
    assert.equal(after.boards[0].queue.state, 'unavailable');
    assert.equal(after.boards[0].queue.error, unavailable.boards[0].queue.error, 'a tool event does not manufacture a new board check');
    assert.equal(after.boards[0].queue.headId, undefined);assert.equal(after.boards[0].queue.rankedIds, undefined);
    assert.equal(after.boards[0].rules.known, false, 'changed tool bytes cannot reuse old policy');
    assert.deepEqual(monitor.changes(), history);assert.equal(sha(p.db), boardBytes, 'the monitor did not alter the damaged owned bytes');
    console.log(JSON.stringify({ scenario: 'native tool event while board unavailable', node: process.versions.node, uv: process.versions.uv, events }));
  } finally { unsubscribe();monitor.close(); }
});

