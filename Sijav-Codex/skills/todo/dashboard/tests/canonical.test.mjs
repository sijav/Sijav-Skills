// The dashboard's own server.mjs and demo command, started from this folder as a
// user starts them, not from an installed copy, and stopped as a parent stops
// them: by letting go of the IPC channel they were started with, the one stop
// request Windows does not turn into a forced kill. Each must exit 0 by
// itself. When the run is measured, each must also leave its own coverage
// file, so a server whose data was lost is a failure here, not just lines that
// read as never run.
import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { STAGE, richProject, sha, cleanup, testEnv, until, launch, stopGracefully, coverageFilesOf } from './helpers.mjs';

test.after(cleanup);
const ready = snap => snap?.boards?.[0]?.queue?.state === 'ready' && snap;
const snapshot = async url => (await fetch(url + 'api/snapshot')).json();

/** That the process left its own coverage file, when this run is measured. */
const measured = (pid, what) => {
  const files = coverageFilesOf(pid);
  if (files !== null) assert.ok(files.length > 0, `${what} (pid ${pid}) wrote no coverage file: its data is lost`);
};

test('the canonical server, started by its CLI, serves a board read-only and exits 0 when its parent lets go', async () => {
  const p = richProject('canonical server'), before = sha(p.db);
  const server = await launch(join(STAGE, 'server.mjs'), ['--port', '0'], { cwd: p.sub, env: testEnv() });
  let code;
  try {
    assert.ok(server.url, server.err);
    for (const label of ['URL:', 'Board:', 'Read-only:', 'Picker:', 'History:']) assert.match(server.out, new RegExp('^' + label, 'm'));
    assert.ok(server.out.includes(p.db), 'the board found from the caller’s folder');
    const snap = await until(async () => ready(await snapshot(server.url)), 'picker');
    assert.equal(snap.boards[0].queue.headId, 'MP-001');
    assert.equal(snap.boards[0].tasks.length, 11);
    assert.equal((await fetch(server.url + 'api/snapshot', { method: 'POST' })).status, 405, 'no write method');
  } finally {
    code = await stopGracefully(server.child);
  }
  assert.equal(code, 0, 'it exited by itself once its parent let go');
  assert.equal(sha(p.db), before, 'the board was never written');
  measured(server.child.pid, 'the canonical server');
});

test('the canonical demo command serves its disposable fixture and removes it when its parent lets go', async () => {
  const demo = await launch(join(STAGE, 'tests', 'demo-fixture.mjs'), ['--port', '0'], { cwd: STAGE, env: testEnv(), ready: /Seen in the browser/ });
  let code, root;
  try {
    assert.ok(demo.url, demo.err);
    root = demo.out.match(/^Disposable fixture project: (.+)$/m)[1].trim();
    assert.ok(root.startsWith(tmpdir()), 'the fixture is in the temp folder');
    const snap = await until(async () => ready(await snapshot(demo.url)), 'demo picker');
    assert.equal(snap.boards[0].tasks.length, 12);
    assert.ok(snap.server.dataDir.startsWith(join(root, '..')), 'its history stays inside the disposable fixture');
  } finally {
    code = await stopGracefully(demo.child);
  }
  assert.equal(code, 0, 'it exited by itself once its parent let go');
  assert.ok(!existsSync(root), 'its disposable fixture was removed on the way out');
  measured(demo.child.pid, 'the demo command');
});
