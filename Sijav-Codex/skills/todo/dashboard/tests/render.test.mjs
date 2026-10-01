// Runs the real browser entry (public/app.js) with a minimal DOM stand-in. Its
// settings are the ones the real server embeds in index.html, and its data are
// the real server's WebSocket messages for a real fixture board. This catches
// runtime errors, missing data and stale claims; it is not a visual check.
import test from 'node:test';
import assert from 'node:assert/strict';
import { registerHooks } from 'node:module';
import { pathToFileURL } from 'node:url';
import { join } from 'node:path';
import { renameSync } from 'node:fs';
import { startDashboard } from '../server.mjs';
import { richProject, tempDir, cleanup, todo, testEnv, STAGE, TODO_PY, until } from './helpers.mjs';

test.after(cleanup);
const PUBLIC = pathToFileURL(join(STAGE, 'public') + '/').href;
registerHooks({ resolve: (specifier, context, next) => /^\/[\w-]+\.m?js$/.test(specifier) && context.parentURL?.startsWith(PUBLIC) ? next(PUBLIC + specifier.slice(1), context) : next(specifier, context) });
const { WebSocket } = await import(pathToFileURL(join(STAGE, 'node_modules', 'ws', 'wrapper.mjs')).href);

class Element {
  constructor(id) { Object.assign(this, { id, innerHTML: '', textContent: '', hidden: false, value: '', open: false, scrollTop: 0, dataset: {}, listeners: {}, inert: false }); this.style = { setProperty() {} }; this.classList = { toggle() {}, add() {}, remove() {} }; }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  closest() { return this; } querySelectorAll() { return []; } querySelector() { return null; }
  setAttribute() {} focus() {} showModal() { this.open = true; } close() { this.open = false; } getBoundingClientRect() { return { left: 0 }; } getClientRects() { return []; }
}
let run = 0;
const pageHides = [];
test.after(() => { for (const hide of pageHides.splice(0)) hide(); }); // stops the Relax wall clock
/** Load app.js against the server's own page settings and feed it the server's own messages. */
async function renderApp(settingsJson, messages, hash) {
  const elements = new Map(), sockets = [];
  const el = id => { if (!elements.has(id)) elements.set(id, new Element(id)); return elements.get(id); };
  el('dashboard-settings').textContent = settingsJson;
  const errors = [];
  const globals = {
    document: { getElementById: el, querySelector: () => el('shell'), querySelectorAll: () => [], addEventListener() {}, documentElement: { dataset: {} }, body: new Element('body'), activeElement: null, visibilityState: 'visible', fullscreenElement: null },
    window: { matchMedia: () => ({ matches: false, addEventListener() {} }), addEventListener: (type, fn) => { if (type === 'pagehide') pageHides.push(fn); }, localStorage: { getItem: () => null, setItem() {} } },
    location: { hash, protocol: 'http:', host: '127.0.0.1:1' }, history: { replaceState: (a, b, h) => { globalThis.location.hash = h; } },
    navigator: {}, WebSocket: class { static OPEN = 1; readyState = 1; constructor() { sockets.push(this); } close() {} },
  };
  for (const [name, value] of Object.entries(globals)) Object.defineProperty(globalThis, name, { value, configurable: true, writable: true });
  try { await import(PUBLIC + 'app.js?run=' + (++run)); } catch (error) { errors.push(error); }
  for (const message of messages) sockets.at(-1).onmessage({ data: JSON.stringify(message) });
  return { el, errors };
}
/** A live dashboard on a fixture, its embedded page settings, and a WebSocket recording its messages. */
async function serve(p, options = {}) {
  const started = await startDashboard({ db: p.db, todoPy: TODO_PY, ...options }, { cwd: tempDir(), env: testEnv() });
  const html = await (await fetch(started.url)).text();
  const settingsJson = html.match(/id="dashboard-settings"[^>]*>(.*?)<\/script>/s)[1];
  const messages = [], socket = new WebSocket(started.url.replace('http', 'ws') + 'api/live', { headers: { Origin: started.url.replace(/\/$/, '') } });
  socket.on('message', data => messages.push(JSON.parse(data)));
  await new Promise((ok, fail) => { socket.once('open', ok); socket.once('error', fail); });
  return { started, settingsJson, messages, close: async () => { socket.terminate(); await started.close(); } };
}
const clean = html => assert.doesNotMatch(html, /undefined|\[object Object\]|NaN/);

test('every view, the task drawer and Relax render from the real server’s settings and messages', { timeout: 120000 }, async () => {
  const p = richProject("demo $' $& project");
  const live = await serve(p);
  try {
    await until(() => live.messages.at(-1)?.snapshot.boards[0].queue.state === 'ready', 'picker');
    todo(p.root, 'move', 'MP-004', 'in_progress');
    await until(() => { const b = live.messages.at(-1)?.snapshot.boards[0]; return b.queue.state === 'ready' && b.queue.headId === 'MP-004'; }, 'change');
    const messages = live.messages.slice();
    assert.equal(messages[0].reset, true); assert.ok(messages.slice(1).every(m => !m.reset));
    const wire = messages.at(-1).snapshot;

    const report = await renderApp(live.settingsJson, messages, '#report');
    assert.deepEqual(report.errors, [], 'settings with $ in the project path parse');
    const html = report.el('content').innerHTML;
    for (const text of ['Next · the tool’s own pick', 'Resume · already started', 'Exact output of todo.py next', 'ALREADY STARTED, finish this first', 'Objectives', 'First release',
      'How todo.py decides (read from its code)', 'phase_rank(task)', 'Complete task register', 'Not recorded: this board has no tested field', 'MP-1000', 'Story of Foundation',
      'Waiting on unfinished parents', 'Area · api'])
      assert.ok(html.includes(text), 'report shows ' + text);
    clean(html);

    const boards = await renderApp(live.settingsJson, messages, '#boards');
    assert.deepEqual(boards.errors, []);
    const lanes = boards.el('content').innerHTML;
    const lane = name => lanes.slice(lanes.indexOf(`lane-header">${name}<`)).split('class="lane"')[0];
    const ids = text => [...text.matchAll(/data-card-key="board:([^"]+)"/g)].map(m => m[1]);
    assert.deepEqual(ids(lane('backlog')), wire.boards[0].queue.rankedIds.filter(id => wire.boards[0].tasks.find(t => t.id === id).status === 'backlog'), 'backlog lane in the exact picker order');
    assert.deepEqual(ids(lane('done')), ['MP-010', 'MP-006'], 'most recent completion first');
    for (const text of ['Waits on MP-001 (backlog)', 'Blocked: Waiting for the owner to choose', 'Not pickable', 'Finding of MP-006', '2 findings · 2 open', 'Objective · LATER Afterwards'])
      assert.ok(lanes.includes(text), 'board shows ' + text);
    clean(lanes);

    const changes = await renderApp(live.settingsJson, messages, '#changes');
    const changesHtml = changes.el('content').innerHTML;
    assert.match(changesHtml, /diff-remove/); assert.match(changesHtml, /in_progress/); assert.match(changesHtml, /kept outside the project/);
    const data = await renderApp(live.settingsJson, messages, '#data');
    for (const table of ['task', 'blocked_by', 'blocked', 'note', 'roast', 'phase']) assert.match(data.el('content').innerHTML, new RegExp(`data-table-name="${table}"`));

    const detail = await renderApp(live.settingsJson, messages, '#task=' + encodeURIComponent('board:MP-006'));
    assert.deepEqual(detail.errors, []);
    const drawer = detail.el('detail-content').innerHTML;
    const top = drawer.indexOf('task-summary'), story = drawer.indexOf('Story &amp; conditions');
    assert.ok(top > 0 && story > top, 'small metadata first, long text after');
    for (const text of ['>id<', '>severity<', '>priority<', '>status<', '>points<', '>created<', '>closed<', '>close output<', 'Not recorded · no such field',
      'Observed the page render', 'Findings filed from this task (2 · 2 open)', 'Roast rounds (1)', 'MP-007, MP-008', 'roasts/mp-006.md', 'stored: '])
      assert.ok(drawer.includes(text), 'drawer shows ' + text);
    clean(drawer);
    const waiting = (await renderApp(live.settingsJson, messages, '#task=' + encodeURIComponent('board:MP-003'))).el('detail-content').innerHTML;
    assert.match(waiting, /Parents that must be done first \(1\)/); assert.match(waiting, /Waits on MP-001/);
    assert.match(waiting, /Checked with todo.py: resolving the reasons above makes it pickable/);
    assert.match(waiting, /<code>task\[&#39;points&#39;\]<\/code> = 1/, 'sort key labelled from by_rule’s own source');

    const relax = await renderApp(live.settingsJson, messages, '#relax');
    assert.deepEqual(relax.errors, []);
    assert.match(relax.el('ambient-pane').innerHTML, /Small medium/);
  } finally { await live.close(); }
});

test('after a failed read the main view shows no pick, no "Next" badge and no stale order (H1, M1)', { timeout: 120000 }, async () => {
  const p = richProject();
  const live = await serve(p);
  try {
    await until(() => live.messages.at(-1)?.snapshot.boards[0].queue.state === 'ready', 'picker');
    await until(() => { try { renameSync(p.db, p.db + '.aside'); return true; } catch { return false; } }, 'move the board away');
    await until(() => live.messages.at(-1)?.snapshot.boards[0].available === false, 'failure pushed');
    for (const hash of ['#report', '#boards']) {
      const page = await renderApp(live.settingsJson, live.messages, hash);
      assert.deepEqual(page.errors, []);
      const html = page.el('content').innerHTML;
      assert.match(html, /could not be read/);
      assert.doesNotMatch(html, /Nothing is pickable|picks nothing|Exact output of todo.py next|>Next<|Resume · next|in next order/, hash + ': no stale pick or order');
      assert.ok(!html.includes('ALREADY STARTED') && !html.includes('NEXT in'), hash + ': the old next text is withdrawn');
      clean(html);
    }
    renameSync(p.db + '.aside', p.db);
  } finally { await live.close(); }
});

test('with no Python the board is shown in full and no status meaning, count or order is claimed', { timeout: 120000 }, async () => {
  const p = richProject();
  const live = await serve(p, { python: join(tempDir('no python '), 'python.exe') });
  try {
    await until(() => live.messages.length > 0, 'first message');
    const report = await renderApp(live.settingsJson, live.messages, '#report');
    assert.deepEqual(report.errors, []);
    const html = report.el('content').innerHTML;
    assert.match(html, /Status meaning is unknown/); assert.match(html, /not a runnable Python/);
    assert.match(html, /Unknown until todo.py’s status policy is read/); assert.match(html, /All tasks by severity \(unfinished unknown\)/);
    assert.doesNotMatch(html, /Unfinished by severity|<td>11<\/td><td>11<\/td>/, 'no unfinished count is claimed');
    assert.ok(html.includes('MP-1000') && html.includes('Story of Foundation'), 'every task is still shown');
    assert.doesNotMatch(html, /Resume · already started|Exact output of todo.py next|in next order/);
    clean(html);
  } finally { await live.close(); }
});
