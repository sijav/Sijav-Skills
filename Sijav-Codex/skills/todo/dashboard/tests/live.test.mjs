import test from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { existsSync, copyFileSync, renameSync, readFileSync, writeFileSync, symlinkSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { pathToFileURL } from 'node:url';
import { spawn, execFileSync } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';
import { findBoard } from '../lib/board.mjs';
import { tmpdir } from 'node:os';
import { richProject, duplicateBoard, tempDir, cleanup, todo, todoWith, add, sha, listing, install, until, testEnv } from './helpers.mjs';

const children = new Set();
/** Kill every child process (and, on Windows, its whole tree) whatever happened in the test. */
function killAll() {
  for (const child of children) {
    if (child.exitCode != null || child.signalCode != null) continue;
    try { if (process.platform === 'win32') execFileSync('taskkill', ['/pid', String(child.pid), '/T', '/F'], { stdio: 'ignore' }); else child.kill('SIGKILL'); } catch {}
  }
  children.clear();
}
test.after(() => { killAll(); cleanup(); });
const inst = install();
const { startDashboard, parseArgs, nodeSupported, isEntry, callerDirectory } = await import(inst.serverUrl);
const { WebSocket } = await import(pathToFileURL(join(inst.dash, 'node_modules', 'ws', 'wrapper.mjs')).href);

/**
 * A client of the dashboard's socket, as the page is: `hello` gives each board's details, each
 * `changed` replaces the boards it names and adds history, and `ask` sends a typed request and
 * resolves with its reply. Pushes are kept in `messages`; replies are not.
 */
function live(url) {
  const messages = [], replies = new Map(), socket = new WebSocket(url.replace('http', 'ws') + 'api/live', { headers: { Origin: url.replace(/\/$/, '') } });
  let view = null, seq = 0;
  socket.on('message', data => {
    const message = JSON.parse(data);
    if (message.type === 'reply') { replies.get(message.id)?.(message); replies.delete(message.id); return; }
    messages.push(message);
    if (message.type === 'hello') view = message;
    else if (message.type === 'changed' && view) {
      const { boards, keys, changes, type, ...top } = message;
      view = { ...view, ...top, boards: view.boards.map(b => boards.find(x => x.id === b.id) || b) };
    }
  });
  const opened = new Promise((ok, fail) => { socket.once('open', ok); socket.once('error', fail); });
  const ask = (type, params = {}) => new Promise((ok, fail) => { const id = ++seq; replies.set(id, m => m.error ? fail(new Error(m.error)) : ok(m.data)); socket.send(JSON.stringify({ ...params, id, type })); });
  return { messages, opened, socket, ask, latest: () => view, close: () => socket.terminate() };
}
const board0 = snap => snap?.boards[0];
const ready = snap => board0(snap)?.queue.state === 'ready' && snap;
const installedFiles = () => listing(inst.dash).concat(listing(join(inst.dash, 'public')), listing(dirname(inst.dash)));
/** Start the CLI and resolve once it has printed its state, or exited. */
function run(command, args, { cwd, env = testEnv(), shell = false, ready = /Ctrl\+C/ } = {}) {
  return new Promise(resolveRun => {
    const child = spawn(command, args, { cwd, env, windowsHide: true, shell });
    children.add(child);
    let out = '', err = '';
    child.stdout.on('data', d => { out += d; if (ready.test(out)) resolveRun({ child, out, err, url: out.match(/^URL:\s+(\S+)/m)?.[1] }); });
    child.stderr.on('data', d => { err += d; });
    child.on('exit', code => resolveRun({ code, out, err }));
  });
}
const stop = async child => {
  if (!child || child.exitCode != null || child.signalCode != null) return; // already gone: 'exit' will not fire again
  const exited = new Promise(r => child.once('exit', r)); child.kill(); await exited;
};

test('installed copy started from a project subdirectory serves the board read-only, history outside the project', async t => {
  const p = richProject("my $' & $$ project"), before = sha(p.db), installed = installedFiles(), projectFiles = listing(p.root);
  const env = testEnv();
  const started = await startDashboard({}, { cwd: p.sub, env });
  t.after(() => started.close());
  assert.match(started.url, /^http:\/\/127\.0\.0\.1:\d+\/$/); assert.notEqual(started.port, 0);
  assert.equal(started.board.dbPath, p.db);
  assert.ok(started.dataDir.startsWith(env.SIJAV_TODO_DASHBOARD_HOME), 'history in the per-user cache root');
  assert.equal(started.todo.path, join(inst.skill, 'todo.py'), 'the installed skill’s own todo.py is the default');
  const page = await fetch(started.url);
  assert.equal(page.headers.get('x-frame-options'), 'DENY'); assert.match(page.headers.get('content-security-policy'), /frame-ancestors 'none'/);
  const html = await page.text();
  const settings = JSON.parse(html.match(/id="dashboard-settings"[^>]*>(.*?)<\/script>/s)[1]);
  assert.equal(settings.project, p.root, "a project path with $' $& and $$ is embedded literally");
  assert.match(html, /<script type="module" src="app.js"><\/script><\/head>/, 'the rest of the page is intact (its paths relative, so it also works under a sub-path)');
  for (const [asset, type] of [['app.js', 'javascript'], ['board-ui.mjs', 'javascript'], ['ambient-ui.mjs', 'javascript'], ['display-mode.mjs', 'javascript'], ['work-context.mjs', 'javascript'], ['styles.css', 'css'], ['theme.css', 'css'], ['ambient.css', 'css'], ['favicon.svg', 'svg']]) {
    const response = await fetch(started.url + asset);
    assert.equal(response.status, 200, asset); assert.match(response.headers.get('content-type'), new RegExp(type));
  }
  assert.equal((await fetch(started.url + 'api/snapshot', { method: 'POST' })).status, 405);
  assert.equal((await fetch(started.url + 'api/snapshot', { headers: { Origin: 'http://evil.example' } })).status, 403);
  assert.equal((await fetch(started.url + 'api/queue/recheck', { headers: { 'Sec-Fetch-Site': 'cross-site' } })).status, 403, 'a cross-site <img> cannot trigger work');
  const snap = await until(async () => ready(await (await fetch(started.url + 'api/snapshot')).json()), 'picker');
  assert.equal(board0(snap).queue.headId, 'MP-001'); assert.equal(board0(snap).tasks.length, 11);
  assert.equal(board0(snap).queue.nextText, todo(duplicateBoard(p.db), 'next'));
  assert.deepEqual(board0(snap).rules.doingStatuses, ['in_progress', 'wait_for_roast'], 'status groups read from todo.py');
  assert.equal(sha(p.db), before); assert.deepEqual(installedFiles(), installed, 'nothing written inside the installed skill');
  assert.deepEqual(listing(p.root), projectFiles, 'nothing written inside the project');
});

test('a committed WAL change is pushed by the file watcher; pushes carry only new history; no polling', async t => {
  const p = richProject();
  const started = await startDashboard({ project: p.root }, { cwd: tempDir(), env: testEnv() });
  const client = live(started.url);
  let writer = null;
  t.after(async () => { client.close(); writer?.close(); await started.close(); });
  await client.opened;
  await until(() => ready(client.latest()), 'initial view');
  assert.equal(client.messages[0].type, 'hello');
  assert.equal(client.messages[0].boards[0].tasks, undefined, 'no board is sent whole');
  assert.equal(client.messages[0].boards[0].total, 11);
  writer = new DatabaseSync(p.db);
  writer.exec('PRAGMA journal_mode=WAL; PRAGMA wal_autocheckpoint=0');
  await until(() => board0(client.latest()).journalMode === 'wal' && ready(client.latest()), 'WAL mode observed');
  const quietCount = client.messages.length, quietReads = client.latest().readCount;
  await delay(1500);
  assert.equal(client.messages.length, quietCount, 'no push without a file event');
  assert.equal((await (await fetch(started.url + 'api/snapshot')).json()).readCount, quietReads, 'no timed rereads');
  const seqBefore = client.latest().latestSeq, mainBefore = sha(p.db);
  writer.exec("UPDATE task SET status='in_progress', updated='2026-10-01T10:00:00.000Z' WHERE id='MP-004'");
  const pushed = await until(() => { const s = client.latest(); return client.messages.slice(quietCount).some(m => m.keys.includes('board:MP-004')) && board0(s).queue.headId === 'MP-004' && ready(s); }, 'pushed WAL change');
  assert.equal((await client.ask('task', { key: 'board:MP-004' })).task.status, 'in_progress', 'the task is asked for, not pushed');
  assert.equal(board0(pushed).queue.headKind, 'started');
  assert.equal(sha(p.db), mainBefore, 'the change is only in the WAL; nothing was checkpointed');
  const later = client.messages.slice(quietCount);
  assert.ok(later.every(m => m.type === 'changed' && m.changes.every(c => c.seq > seqBefore)), 'later pushes carry only new entries');
  assert.ok(later.every(m => m.boards.every(b => b.tasks === undefined && b.tables === undefined)), 'a push never carries a board whole');
  assert.equal(later.flatMap(m => m.changes).filter(c => c.itemId === 'MP-004' && c.table === 'task').length, 1, 'each entry is sent once');
  assert.ok(later.length <= 4, `a change produces a few coalesced pushes, not one per internal step (${later.length})`);
  const transition = pushed.statusTransitions.find(c => c.itemId === 'MP-004');
  assert.equal(transition.fromStatus, 'backlog'); assert.equal(transition.before, undefined, 'transitions are slim');
  assert.equal(transition.sourceId, 'board', 'a transition names its board, so the Observed status transitions panel lists it');
  writer.close(); writer = null;
  todo(p.root, 'edit', 'MP-007', '--note', 'Pushed by the watcher');
  await until(async () => client.messages.some(m => m.keys?.includes('board:MP-007')) && (await client.ask('task', { key: 'board:MP-007' })).task.notes.some(n => n.text === 'Pushed by the watcher'), 'pushed tool write');
  const all = await (await fetch(started.url + 'api/changes?limit=all')).json();
  const pageOne = await (await fetch(started.url + 'api/changes?limit=1')).json();
  const pageTwo = await (await fetch(started.url + `api/changes?limit=1&before=${pageOne.changes[0].seq}`)).json();
  assert.equal(all.total, all.changes.length); assert.equal(all.complete, true);
  assert.deepEqual([pageOne.changes[0].seq, pageTwo.changes[0].seq], all.changes.slice(0, 2).map(c => c.seq), 'pages are contiguous, newest first');
  const mine = await (await fetch(started.url + 'api/changes?item=MP-004')).json();
  assert.ok(mine.changes.length >= 1 && mine.changes.every(c => c.itemId === 'MP-004'));
});

test('a test state recorded by todo.py is pushed live and counted under its label; before it, the board says not recorded', async t => {
  const p = richProject(), untouched = sha(p.db);
  const started = await startDashboard({ project: p.root }, { cwd: tempDir(), env: testEnv() });
  const client = live(started.url);
  t.after(async () => { client.close(); await started.close(); });
  await client.opened;
  await until(() => ready(client.latest()), 'initial view');
  const flags = async () => (await client.ask('summary')).perBoard[board0(client.latest()).id].testing.flags.map(f => [f.label, f.supported, f.passed, f.pending, f.unknown]);
  assert.deepEqual(await flags(), [['Tested', false, 0, 0, 2], ['E2E tested', false, 0, 0, 2]], 'not recorded, never "not tested"');
  assert.equal(sha(p.db), untouched, 'the dashboard wrote nothing');
  const seen = client.messages.length;
  todo(p.root, 'tested', 'MP-010', '--evidence', 'Ran the area suite: 12 passed');
  await until(() => client.messages.slice(seen).some(m => m.type === 'changed' && m.keys.includes('board:MP-010')) && ready(client.latest()), 'pushed test state');
  const record = (await client.ask('task', { key: 'board:MP-010' })).task;
  assert.equal(record.raw.tested, 1); assert.equal(record.raw.tested_how, 'Ran the area suite: 12 passed'); assert.equal(record.raw.e2e_tested, 0);
  assert.deepEqual(await flags(), [['Tested', true, 1, 1, 0], ['E2E tested', true, 0, 2, 0]], 'done is not tested; tested is not e2e tested');
  const recorded = sha(p.db);
  await client.ask('task', { key: 'board:MP-006' });
  assert.equal(sha(p.db), recorded, 'reading the change wrote nothing');
});

test('a failed board read withdraws the pick and order; reading again restores them (H1, M1)', async t => {
  const p = richProject();
  const started = await startDashboard({ db: p.db }, { cwd: tempDir(), env: testEnv() });
  const client = live(started.url);
  t.after(async () => { client.close(); await started.close(); });
  await client.opened;
  await until(() => ready(client.latest()), 'initial view');
  const aside = p.db + '.aside';
  await until(() => { try { renameSync(p.db, aside); return true; } catch { return false; } }, 'move the board away');
  const failed = await until(() => { const s = client.latest(); return board0(s).available === false && s; }, 'read failure pushed');
  const q = board0(failed).queue;
  assert.equal(q.state, 'unavailable'); assert.match(q.error, /could not be read/);
  for (const key of ['headId', 'rankedIds', 'nextText', 'startableIds', 'previous']) assert.equal(q[key], undefined, `${key} withdrawn`);
  assert.equal(board0(failed).stale, true); assert.equal(board0(failed).total, 11, 'last successful data stays visible');
  const { renderAmbientModel } = await import(pathToFileURL(join(inst.dash, 'public', 'ambient-ui.mjs')).href);
  const relaxed = await client.ask('relax');
  assert.equal(relaxed.boards[0].head, null);
  assert.doesNotMatch(renderAmbientModel(relaxed), /Nothing left|picks nothing/);
  await until(() => { try { renameSync(aside, p.db); return true; } catch { return false; } }, 'move the board back');
  const back = await until(() => { const s = client.latest(); return board0(s).available && ready(s); }, 'recovered');
  assert.equal(board0(back).queue.headId, 'MP-001');
});

test('the tool is watched: a broken todo.py withdraws policy and pick; a changed one is read again (policy watch)', async t => {
  const own = install(), p = richProject();
  const { startDashboard: start } = await import(own.serverUrl);
  const tool = join(own.skill, 'todo.py'), original = readFileSync(tool, 'utf8');
  const started = await start({ project: p.root }, { cwd: tempDir(), env: testEnv() });
  const client = live(started.url);
  t.after(async () => { client.close(); await started.close(); });
  await client.opened;
  await until(() => ready(client.latest()), 'initial view');
  todo(p.root, 'move', 'MP-004', 'wait_for_roast');
  await until(() => { const s = ready(client.latest()); return s && board0(s).queue.headId === 'MP-004' && s; }, 'wait_for_roast resumed first');
  writeFileSync(tool, original + '\nthis is not python(\n');
  const broken = await until(() => { const s = client.latest(); return board0(s).queue.state === 'error' && s; }, 'picker error pushed');
  assert.equal(board0(broken).queue.headId, undefined); assert.equal(board0(broken).queue.rankedIds, undefined);
  assert.equal(board0(broken).rules.known, false, 'no stale policy is kept for a changed tool');
  assert.match(board0(broken).rules.reason, /could not be read|changed/);
  writeFileSync(tool, original.replace('("in_progress", "wait_for_roast") and not is_blocked', '("in_progress",) and not is_blocked'));
  const changed = await until(() => { const s = ready(client.latest()); return s && board0(s).rules.known && s; }, 'changed policy pushed');
  assert.deepEqual(board0(changed).rules.doingStatuses, ['in_progress']);
  assert.notEqual(board0(changed).queue.headId, 'MP-004');
  assert.equal(board0(changed).queue.nextText, todoWith(tool, duplicateBoard(p.db), 'next'));
  assert.ok(!(await client.ask('task', { key: 'board:MP-004' })).task.isDoing);
});

test('atomic replacement of the board file is picked up through the directory watch', async t => {
  const p = richProject();
  const started = await startDashboard({ db: p.db }, { cwd: tempDir(), env: testEnv() });
  const client = live(started.url);
  t.after(async () => { client.close(); await started.close(); });
  await client.opened;
  await until(() => ready(client.latest()), 'initial view');
  const work = duplicateBoard(p.db);
  add(work, 'MP-1001', 'Arrived by replacement', ['--severity', 'critical', '--points', '1']);
  const temp = p.db + '.replace-tmp';
  copyFileSync(join(work, '.claude', 'todo.db'), temp);
  // Windows refuses a rename over a file another process has open; SQLite opens briefly, so retry.
  await until(() => { try { renameSync(temp, p.db); return true; } catch { return false; } }, 'rename over the board');
  const snap = await until(() => { const s = ready(client.latest()); return s && board0(s).total === 12 && board0(s).queue.headId === 'MP-1001' && s; }, 'replacement pushed');
  assert.equal(board0(snap).queue.nextText, todo(work, 'next'));
});

test('the CLI prints its state, keeps history across restarts, refuses missing boards, and starts through a junction', async () => {
  const p = richProject(), env = testEnv();
  const first = await run(process.execPath, [inst.server, '--port', '0'], { cwd: p.sub, env });
  try {
    assert.ok(first.child, first.err);
    for (const label of ['URL:', 'Project:', 'Board:', 'Read-only:', 'Watching:', 'Live push:', 'Picker:', 'History:']) assert.match(first.out, new RegExp('^' + label, 'm'));
    assert.match(first.out, /changes on restart, pass --port/);
    assert.ok(first.out.includes(env.SIJAV_TODO_DASHBOARD_HOME), 'the history path is printed');
    const a = await until(async () => ready(await (await fetch(first.url + 'api/snapshot')).json()), 'first picker');
    todo(p.root, 'move', 'MP-001', 'in_progress');
    const b = await until(async () => { const s = ready(await (await fetch(first.url + 'api/snapshot')).json()); return s && s.changeCount > a.changeCount && board0(s).queue.headKind === 'started' && s; }, 'change recorded');
    await stop(first.child);
    const second = await run(process.execPath, [inst.server, '--db', p.db, '--port', '0'], { cwd: tempDir(), env });
    const c = await until(async () => ready(await (await fetch(second.url + 'api/snapshot')).json()), 'second picker');
    assert.equal(c.changeCount, b.changeCount); assert.equal(c.trackingSince, a.trackingSince);
    assert.deepEqual(board0(c).queue.rankedIds, board0(b).queue.rankedIds, 'same order from --db as from a subdirectory');
    await stop(second.child);
  } finally { await stop(first.child); }
  const nowhere = tempDir('cli no board ');
  assert.equal(findBoard(nowhere), null, 'precondition: no ancestor board, so a real board can never be opened here');
  const files = listing(nowhere);
  const refused = await run(process.execPath, [inst.server], { cwd: nowhere, env });
  assert.equal(refused.code, 2); assert.match(refused.err, /No board/); assert.deepEqual(listing(nowhere), files);
  const missing = await run(process.execPath, [inst.server, '--db', join(nowhere, 'x', 'todo.db')], { cwd: nowhere, env });
  assert.equal(missing.code, 2); assert.match(missing.err, /never creates/); assert.ok(!existsSync(join(nowhere, 'x')));
  // M3: through a directory junction (Windows) or symlink, the entry check still recognises the server.
  const link = join(tempDir('linked skill '), 'dashboard link');
  symlinkSync(inst.dash, link, process.platform === 'win32' ? 'junction' : 'dir');
  const viaLink = await run(process.execPath, [join(link, 'server.mjs'), '--help'], { cwd: nowhere, env });
  assert.equal(viaLink.code, 0); assert.match(viaLink.out, /--project <dir>/);
  const started = await run(process.execPath, [join(link, 'server.mjs'), '--project', p.root], { cwd: nowhere, env });
  try { assert.ok(started.url, started.err); } finally { await stop(started.child); }
  for (const [version, ok] of [['22.13.0', false], ['22.15.1', false], ['22.16.0', true], ['23.11.0', false], ['24.0.0', true], ['24.16.0', true]]) assert.equal(nodeSupported(version), ok, version);
  assert.equal(isEntry(join(link, 'server.mjs'), pathToFileURL(inst.server).href), true, 'junction path recognised');
  assert.equal(isEntry(join(inst.dash, 'lib', 'board.mjs'), pathToFileURL(inst.server).href), false);
  const ours = { npm_lifecycle_event: 'start', INIT_CWD: p.sub, npm_package_json: join(inst.dash, 'package.json') };
  assert.equal(callerDirectory(ours, inst.dash), p.sub, 'this package’s own npm start: the caller’s directory');
  assert.equal(callerDirectory({ ...ours, npm_package_json: join(nowhere, 'package.json') }, inst.dash), inst.dash, 'another package’s npm variables are ignored');
  assert.equal(callerDirectory(ours, nowhere), nowhere, 'not running in this package folder: npm variables are ignored');
  assert.equal(callerDirectory({ npm_lifecycle_event: 'start', INIT_CWD: p.sub }, inst.dash), inst.dash, 'no package.json named: ignored');
  assert.equal(callerDirectory({}, p.sub), p.sub);
  assert.throws(() => parseArgs(['--host', '0.0.0.0']), /loopback/);
  assert.throws(() => parseArgs(['--port', 'abc']), /--port/);
  assert.equal(parseArgs(['--port=8123']).port, 8123);
});

test('npm start resolves the board from the caller’s directory, not the install folder (M4)', async () => {
  const p = richProject();
  assert.equal(findBoard(inst.dash), null, 'precondition: no board above the install folder');
  // The real npm, through the shell as a user types it (one command string, no separately passed arguments).
  const result = await run(`npm --prefix "${inst.dash}" start -- --port 0`, [], { cwd: p.sub, shell: true });
  try {
    assert.ok(result.url, result.err + result.out);
    assert.ok(result.out.includes(p.db), 'the caller’s board, found from INIT_CWD');
  } finally { killAll(); }
  const simulated = await run(process.execPath, [inst.server, '--port', '0'], { cwd: inst.dash, env: testEnv({ npm_lifecycle_event: 'start', INIT_CWD: p.sub, npm_package_json: join(inst.dash, 'package.json') }) });
  try { assert.ok(simulated.out.includes(p.db)); } finally { await stop(simulated.child); }
});

test('npm variables inherited from an unrelated npm process never select another board (N1)', async () => {
  const p = richProject(), other = richProject('other project');
  const otherPackage = join(dirname(other.root), 'package.json');
  writeFileSync(otherPackage, '{"name":"unrelated","version":"1.0.0"}');
  for (const dir of [p.sub, other.sub, dirname(otherPackage)]) assert.ok(dir.startsWith(tmpdir()), 'synthetic temp boards only');
  // As if this server were started by a script of some other package, or by a parent `npm test`.
  const inherited = { npm_lifecycle_event: 'test', npm_lifecycle_script: 'node --test', npm_package_name: 'unrelated', npm_package_json: otherPackage, INIT_CWD: other.sub };
  for (const [label, cwd] of [['from the project', p.sub], ['from the install folder', inst.dash]]) {
    const result = await run(process.execPath, [inst.server, '--port', '0', ...(cwd === inst.dash ? ['--project', p.root] : [])], { cwd, env: testEnv(inherited) });
    try {
      assert.ok(result.url, label + ': ' + result.err);
      assert.ok(result.out.includes(p.db), label + ': the caller’s own board');
      assert.ok(!result.out.includes(other.db), label + ': never the board named by inherited INIT_CWD');
    } finally { await stop(result.child); }
  }
  const fromInstall = await run(process.execPath, [inst.server, '--port', '0'], { cwd: inst.dash, env: testEnv(inherited) });
  try { assert.equal(fromInstall.code, 2, 'from the install folder with foreign npm variables: no board, not the foreign one'); assert.match(fromInstall.err, /No board/); }
  finally { await stop(fromInstall.child); }
});

test('installed tests and the demo use the parent todo.py; the demo refuses real-board overrides (M7, M8)', async () => {
  const env = testEnv(); delete env.TODO_SKILL_DIR;
  const resolved = execFileSync(process.execPath, ['--input-type=module', '-e', "import('./tests/helpers.mjs').then(h=>console.log(h.TODO_PY))"], { cwd: inst.dash, env, encoding: 'utf8' }).trim();
  assert.equal(resolved, join(inst.skill, 'todo.py'));
  for (const flag of ['--db', '--project', '--data-dir']) {
    const refused = await run(process.execPath, [join(inst.dash, 'tests', 'demo-fixture.mjs'), flag, tempDir()], { cwd: inst.dash, env });
    assert.equal(refused.code, 2, flag); assert.match(refused.err, /demo .*does not accept/i);
  }
  const demo = await run(process.execPath, [join(inst.dash, 'tests', 'demo-fixture.mjs'), '--port', '0'], { cwd: inst.dash, env, ready: /Seen in the browser/ });
  try {
    assert.ok(demo.url, demo.err);
    const root = demo.out.match(/^Disposable fixture project: (.+)$/m)[1].trim();
    assert.ok(root.startsWith(tmpdir()), 'the fixture is in the temp folder');
    const s = await until(async () => ready(await (await fetch(demo.url + 'api/snapshot')).json()), 'demo picker');
    assert.equal(board0(s).tasks.length, 12); assert.equal(s.server.todo, join(inst.skill, 'todo.py'));
    assert.ok(s.server.dataDir.startsWith(dirname(root)), 'demo history stays inside the disposable fixture');
  } finally { await stop(demo.child); }
});
