// Runs the real browser entry (public/app.js) with a minimal DOM stand-in. Its
// settings are the ones the real server embeds in index.html, and its socket is
// bridged to the real server for a real fixture board: every typed request the
// page sends is answered by the server, and every push reaches the page. This
// catches runtime errors, missing data and stale claims; it is not a visual check.
import test from 'node:test';
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
import { join } from 'node:path';
import { renameSync } from 'node:fs';
import { startDashboard } from '../server.mjs';
import { richProject, loopProject, LONG_IDS, sha, tempDir, cleanup, todo, testEnv, STAGE, TODO_PY, until } from './helpers.mjs';

test.after(cleanup);
const PUBLIC = pathToFileURL(join(STAGE, 'public') + '/').href;
const { WebSocket } = await import(pathToFileURL(join(STAGE, 'node_modules', 'ws', 'wrapper.mjs')).href);

class Element {
  constructor(id) { Object.assign(this, { id, innerHTML: '', textContent: '', hidden: false, value: '', open: false, scrollTop: 0, dataset: {}, listeners: {}, inert: false }); this.style = { setProperty() {} }; this.classList = { toggle() {}, add() {}, remove() {} }; }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  closest() { return this; } querySelectorAll() { return []; } querySelector() { return null; }
  setAttribute() {} focus() {} showModal() { this.open = true; } close() { this.open = false; } getBoundingClientRect() { return { left: 0 }; } getClientRects() { return []; }
}
let run = 0;
const pageHides = [], openSockets = new Set();
test.after(() => { for (const hide of pageHides.splice(0)) hide(); for (const socket of openSockets) socket.terminate(); }); // stops the Relax wall clock
const unescapeAttr = text => text.replaceAll('&quot;', '"').replaceAll('&#39;', "'").replaceAll('&lt;', '<').replaceAll('&gt;', '>').replaceAll('&amp;', '&');

/**
 * Load app.js against the server's own page settings, its socket bridged to the live server.
 * Resolves once the page has been greeted and every question it asked has been answered.
 * With `published`, the page is set up as a relay's host page sets it up, signed in with `token`.
 */
async function renderApp(live, hash, { published = null, token = null } = {}) {
  for (const socket of openSockets) socket.terminate();
  openSockets.clear();
  const elements = new Map(), bridges = [], listeners = {};
  const el = id => { if (!elements.has(id)) elements.set(id, new Element(id)); return elements.get(id); };
  el('dashboard-settings').textContent = published ? JSON.stringify({ ...JSON.parse(live.settingsJson), published }) : live.settingsJson;
  const errors = [];
  class Bridge {
    static OPEN = 1;
    constructor(url) {
      // Published with a sign-in, it acts as the relay does: nothing reaches the page before the token.
      Object.assign(this, { url, messages: [], greeting: null, held: published?.auth ? [] : null, readyState: 0, sent: 0, answered: 0, greeted: false, last: Date.now() });
      bridges.push(this);
      this.real = new WebSocket(live.wsUrl, { headers: { Origin: live.origin } });
      openSockets.add(this.real);
      this.real.on('open', () => { this.readyState = 1; this.onopen?.(); });
      this.real.on('message', data => { if (this.held) this.held.push(String(data)); else this.receive(String(data)); });
      this.real.on('close', () => { this.readyState = 3; });
      this.real.on('error', () => {});
    }
    receive(text) {
      const message = JSON.parse(text);
      if (message.type === 'reply') this.answered++;
      if (message.type === 'hello') { this.greeted = true; this.greeting = text; }
      this.last = Date.now();
      try { this.onmessage?.({ data: text }); } catch (error) { errors.push(error); }
    }
    send(text) {
      const message = JSON.parse(text);
      this.messages.push(message); this.last = Date.now();
      if (message.type === 'auth' && this.held) { const held = this.held; this.held = null; for (const t of held) this.receive(t); return; } // the relay keeps the token
      this.sent++; this.real.send(text);
    }
    close() { this.real.terminate(); }
  }
  const globals = {
    document: { getElementById: el, querySelector: () => el('shell'), querySelectorAll: () => [], addEventListener: (type, fn) => (listeners[type] ??= []).push(fn), documentElement: { dataset: {} }, body: new Element('body'), activeElement: null, visibilityState: 'visible', fullscreenElement: null },
    window: { matchMedia: () => ({ matches: false, addEventListener() {} }), addEventListener: (type, fn) => { if (type === 'pagehide') pageHides.push(fn); }, localStorage: { getItem: () => null, setItem() {} }, dashboardAuth: token ? async () => token : undefined },
    location: { hash, protocol: 'http:', host: '127.0.0.1:1' }, history: { replaceState: (a, b, h) => { globalThis.location.hash = h; } },
    navigator: {}, WebSocket: Bridge,
  };
  for (const [name, value] of Object.entries(globals)) Object.defineProperty(globalThis, name, { value, configurable: true, writable: true });
  try { await import(PUBLIC + 'app.js?run=' + (++run)); } catch (error) { errors.push(error); }
  const settled = () => until(() => { const b = bridges.at(-1); return b && b.greeted && b.sent === b.answered && Date.now() - b.last > 60; }, 'the page asked and was answered');
  await settled();
  /** Click every "Show more" on the page until each list is whole. */
  const loadAll = async (limit = 60) => {
    for (let round = 0; round < limit; round++) {
      const keys = [...el('content').innerHTML.matchAll(/data-more-list="([^"]+)"/g)].map(m => unescapeAttr(m[1]));
      if (!keys.length) return;
      for (const key of keys) for (const fn of listeners.click || []) fn({ target: { closest: sel => sel === '[data-more-list]' ? { dataset: { moreList: key } } : null } });
      await settled();
    }
  };
  /** A message as the relay would send it. */
  const deliver = message => bridges.at(-1).onmessage?.({ data: typeof message === 'string' ? message : JSON.stringify(message) });
  return { el, errors, settled, loadAll, deliver, bridge: () => bridges.at(-1) };
}

/** A live dashboard on a fixture: its embedded page settings, and a client following its pushes. */
async function serve(p, options = {}) {
  const started = await startDashboard({ db: p.db, todoPy: TODO_PY, ...options }, { cwd: tempDir(), env: testEnv() });
  const html = await (await fetch(started.url)).text();
  const settingsJson = html.match(/id="dashboard-settings"[^>]*>(.*?)<\/script>/s)[1];
  const origin = started.url.replace(/\/$/, ''), wsUrl = started.url.replace('http', 'ws') + 'api/live';
  const watcher = new WebSocket(wsUrl, { headers: { Origin: origin } });
  let view = null, first = null;
  watcher.on('message', data => {
    const message = JSON.parse(data);
    if (message.type === 'hello') { view = message; first ??= message; }
    else if (message.type === 'changed' && view) { const { boards, keys, changes, type, ...top } = message; view = { ...view, ...top, boards: view.boards.map(b => boards.find(x => x.id === b.id) || b) }; }
  });
  await new Promise((ok, fail) => { watcher.once('open', ok); watcher.once('error', fail); });
  return { started, settingsJson, origin, wsUrl, view: () => view, first: () => first, full: async () => (await fetch(started.url + 'api/snapshot')).json(),
    close: async () => { watcher.terminate(); await started.close(); } };
}
const clean = html => assert.doesNotMatch(html, /undefined|\[object Object\]|NaN|\$\{/);

test('every view, the task drawer and Relax render from the real server’s settings and answers', { timeout: 180000 }, async () => {
  const p = richProject("demo $' $& project");
  const live = await serve(p);
  try {
    await until(() => live.view()?.boards[0].queue.state === 'ready', 'picker');
    todo(p.root, 'move', 'MP-004', 'in_progress');
    await until(() => { const b = live.view()?.boards[0]; return b.queue.state === 'ready' && b.queue.headId === 'MP-004'; }, 'change');
    assert.equal(live.first().type, 'hello'); assert.equal(live.first().boards[0].tasks, undefined, 'the page is never sent a board whole');
    const wire = await live.full();

    const report = await renderApp(live, '#report');
    assert.deepEqual(report.errors, [], 'settings with $ in the project path parse');
    assert.match(report.el('content').innerHTML, /Show 5 more · 6 left/, 'the register shows five, then five more on request');
    await report.loadAll();
    const html = report.el('content').innerHTML;
    for (const text of ['Next · the tool’s own pick', 'Resume · already started', 'Exact output of todo.py next', 'ALREADY STARTED, finish this first', 'Objectives', 'First release',
      'How todo.py decides (read from its code)', 'phase_rank(task)', 'Complete task register', 'Not recorded: this board has no tested field', 'MP-1000', 'Story of Foundation',
      'Waiting on unfinished parents', 'Area · api'])
      assert.ok(html.includes(text), 'report shows ' + text);
    clean(html);

    const boards = await renderApp(live, '#boards');
    assert.deepEqual(boards.errors, []);
    await boards.loadAll();
    const lanes = boards.el('content').innerHTML;
    const lane = name => lanes.slice(lanes.indexOf(`lane-header">${name}<`)).split('class="lane"')[0];
    const ids = text => [...text.matchAll(/data-card-key="board:([^"]+)"/g)].map(m => m[1]);
    assert.deepEqual(ids(lane('backlog')), wire.boards[0].queue.rankedIds.filter(id => wire.boards[0].tasks.find(t => t.id === id).status === 'backlog'), 'backlog lane in the exact picker order');
    assert.deepEqual(ids(lane('done')), ['MP-010', 'MP-006'], 'most recent completion first');
    for (const text of ['Waits on MP-001 (backlog)', 'Blocked: Waiting for the owner to choose', 'Not pickable', 'Finding of MP-006', '2 findings · 2 open', 'Objective · LATER Afterwards'])
      assert.ok(lanes.includes(text), 'board shows ' + text);
    clean(lanes);

    const changes = await renderApp(live, '#changes');
    const changesHtml = changes.el('content').innerHTML;
    assert.match(changesHtml, /diff-remove/); assert.match(changesHtml, /in_progress/); assert.match(changesHtml, /kept outside the project/);
    const data = await renderApp(live, '#data');
    for (const table of ['task', 'blocked_by', 'blocked', 'note', 'roast', 'phase']) assert.match(data.el('content').innerHTML, new RegExp(`data-table-name="${table}"`));
    assert.match(data.el('content').innerHTML, /Showing 11 of 11 rows/);

    const detail = await renderApp(live, '#task=' + encodeURIComponent('board:MP-006'));
    assert.deepEqual(detail.errors, []);
    const drawer = detail.el('detail-content').innerHTML;
    const top = drawer.indexOf('task-summary'), story = drawer.indexOf('Story &amp; conditions');
    assert.ok(top > 0 && story > top, 'small metadata first, long text after');
    for (const text of ['>id<', '>severity<', '>priority<', '>status<', '>points<', '>created<', '>closed<', '>close output<', 'Not recorded · no such field',
      'Observed the page render', 'Findings filed from this task (2 · 2 open)', 'Roast rounds (1)', 'MP-007, MP-008', 'roasts/mp-006.md', 'stored: '])
      assert.ok(drawer.includes(text), 'drawer shows ' + text);
    clean(drawer);
    const waiting = (await renderApp(live, '#task=' + encodeURIComponent('board:MP-003'))).el('detail-content').innerHTML;
    assert.match(waiting, /Parents that must be done first \(1\)/); assert.match(waiting, /Waits on MP-001/);
    assert.match(waiting, /Checked with todo.py: resolving the reasons above makes it pickable/);
    assert.match(waiting, /<code>task\[&#39;points&#39;\]<\/code> = 1/, 'sort key labelled from by_rule’s own source');

    const relax = await renderApp(live, '#relax');
    assert.deepEqual(relax.errors, []);
    assert.match(relax.el('ambient-pane').innerHTML, /Small medium/);
  } finally { await live.close(); }
});

test('after a failed read the main view shows no pick, no "Next" badge and no stale order (H1, M1)', { timeout: 120000 }, async () => {
  const p = richProject();
  const live = await serve(p);
  try {
    await until(() => live.view()?.boards[0].queue.state === 'ready', 'picker');
    await until(() => { try { renameSync(p.db, p.db + '.aside'); return true; } catch { return false; } }, 'move the board away');
    await until(() => live.view()?.boards[0].available === false, 'failure pushed');
    for (const hash of ['#report', '#boards']) {
      const page = await renderApp(live, hash);
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
    await until(() => live.view(), 'first message');
    const report = await renderApp(live, '#report');
    assert.deepEqual(report.errors, []);
    await report.loadAll();
    const html = report.el('content').innerHTML;
    assert.match(html, /Status meaning is unknown/); assert.match(html, /not a runnable Python/);
    assert.match(html, /Unknown until todo.py’s status policy is read/); assert.match(html, /All tasks by severity \(unfinished unknown\)/);
    assert.doesNotMatch(html, /Unfinished by severity|<td>11<\/td><td>11<\/td>/, 'no unfinished count is claimed');
    assert.ok(html.includes('MP-1000') && html.includes('Story of Foundation'), 'every task is still shown');
    assert.doesNotMatch(html, /Resume · already started|Exact output of todo.py next|in next order/);
    clean(html);
  } finally { await live.close(); }
});

test('a loop board renders in its own tool’s words, with long id lists folded and the item’s own fields in the drawer', { timeout: 120000 }, async () => {
  const p = loopProject();
  const before = sha(p.db);
  const live = await serve(p);
  try {
    await until(() => live.view()?.boards[0].queue.state === 'ready', 'loop picker');
    const report = await renderApp(live, '#report');
    assert.deepEqual(report.errors, []);
    const html = report.el('content').innerHTML;
    for (const text of ['The order as board.py’s board_order() gives it', 'How board.py decides (read from its code)', '3 startable · 2 waiting',
      'First startable not started', 'Findings filed against items and not yet resolved', 'IDS: <span class="value-list"', '40 values', 'data-expand="board:6:why">Show more'])
      assert.ok(html.includes(text), 'report shows ' + text);
    assert.ok(!html.includes(LONG_IDS), 'the long id list is folded');
    assert.doesNotMatch(html, /todo\.py|waiting on parents/, 'no to-do skill words on a loop board');
    clean(html);

    const page = await renderApp(live, '#boards');
    const lanes = page.el('content').innerHTML;
    for (const text of ['Waits on 1 (open): Foundation', 'Parked: Owner decides the wording', 'Waiting on blockers', 'Area · back', 'Area · front'])
      assert.ok(lanes.includes(text), 'board shows ' + text);
    const areaFilter = page.el('area').innerHTML;
    assert.ok(areaFilter.includes('value="back"') && areaFilter.includes('value="front"') && areaFilter.includes('value="missing"'), 'the area filter offers each stored area');
    clean(lanes);

    const drawer = (await renderApp(live, '#task=' + encodeURIComponent('board:1'))).el('detail-content').innerHTML;
    for (const text of ['>created at<', '>closed at<', '>occurrences<', 'Story of Foundation', 'Exit command', 'check foundation', 'Items waiting on this (1)', 'Table finding (1)', 'First finding', 'stored: 1700000000'])
      assert.ok(drawer.includes(text), 'drawer shows ' + text);
    assert.doesNotMatch(drawer, /no such field|Parents that must be done first|Work area, type|Roast rounds|Findings filed from this task|Notes \(/, 'no to-do skill sections');
    clean(drawer);
    const waiting = (await renderApp(live, '#task=' + encodeURIComponent('board:2'))).el('detail-content').innerHTML;
    assert.match(waiting, /Items this waits on \(dep\) \(1\)/);
    assert.match(waiting, /Checked with the tool: removing the reasons above makes it startable/);
    assert.match(waiting, /Sort key from sort_key\(\)/);

    const relax = await renderApp(live, '#relax');
    assert.deepEqual(relax.errors, []);
    const ambient = relax.el('ambient-pane').innerHTML;
    assert.doesNotMatch(ambient, /Show more|data-expand/, 'Relax folds without buttons');
    assert.ok(ambient.includes('Next · board.py’s board_order()'), 'Relax names the board’s own tool');
    assert.doesNotMatch(ambient, /todo[.]py/);
    clean(ambient);
  } finally { await live.close(); }
  assert.equal(sha(p.db), before, 'the board is never written');
});

test('two boards on one page: each board in its own tool’s words, cards named by board, each drawer its own', { timeout: 120000 }, async () => {
  const loop = loopProject(), board = richProject('todo project');
  const live = await serve(loop, { dbs: [loop.db, board.db] });
  try {
    await until(() => { const s = live.view(); return s?.boards.length === 2 && s.boards.every(b => b.queue.state === 'ready'); }, 'both pickers');
    const report = await renderApp(live, '#report');
    assert.deepEqual(report.errors, []);
    const html = report.el('content').innerHTML;
    for (const text of ['How board.py decides (read from its code)', 'How todo.py decides (read from its code)', 'The order as board.py’s board_order() gives it', 'Exact output of todo.py next'])
      assert.ok(html.includes(text), 'report shows ' + text);
    clean(html);
    const lanes = (await renderApp(live, '#boards')).el('content').innerHTML;
    for (const text of ['Next order from each board’s own tool', '>loop · board<', '>todo project · to-do<', 'Statuses the board and board.py allow', 'Statuses the board and todo.py allow', ' · loop · board<', ' · todo project · to-do<'])
      assert.ok(lanes.includes(text), 'board shows ' + text);
    clean(lanes);
    const loopDrawer = (await renderApp(live, '#task=' + encodeURIComponent('board:1'))).el('detail-content').innerHTML;
    assert.ok(loopDrawer.includes('>created at<') && loopDrawer.includes('Items waiting on this (1)') && !loopDrawer.includes('Findings filed from this task'));
    const todoDrawer = (await renderApp(live, '#task=' + encodeURIComponent('board-2:MP-006'))).el('detail-content').innerHTML;
    assert.ok(todoDrawer.includes('Findings filed from this task (2 · 2 open)') && todoDrawer.includes('>created<') && !todoDrawer.includes('>created at<'));
    clean(loopDrawer); clean(todoDrawer);
    const ambient = (await renderApp(live, '#relax')).el('ambient-pane').innerHTML;
    assert.ok(ambient.includes('board.py’s board_order()') && ambient.includes('todo.py next'), 'Relax names each board’s own tool');
  } finally { await live.close(); }
});

test('published through a relay: its socket path, the sign-in first, no local actions, and the offline and refused notices', { timeout: 120000 }, async () => {
  const p = richProject();
  const live = await serve(p);
  try {
    await until(() => live.view()?.boards[0].queue.state === 'ready', 'picker');
    const page = await renderApp(live, '#report', { published: { livePath: '/progress/live', auth: 'google' }, token: 'signed-in-token' });
    assert.deepEqual(page.errors, []);
    const bridge = page.bridge();
    assert.equal(new URL(bridge.url).pathname, '/progress/live', 'the socket is the relay’s');
    assert.deepEqual(bridge.messages[0], { type: 'auth', token: 'signed-in-token' }, 'the sign-in token is the first message');
    assert.ok(page.el('export').hidden && page.el('print').hidden, 'no export or print');
    assert.match(page.el('content').innerHTML, /data-recheck-queue="board" disabled hidden>/, '"Run next again" is not offered');
    assert.equal(page.el('refresh-note').textContent, 'Published · Read only · connected');

    page.deliver({ type: 'offline', reason: 'The boards are offline: the PC that has them is not connected.' });
    assert.match(page.el('content').innerHTML, /banner error">The boards are offline: the PC that has them is not connected\.</);
    assert.equal(page.el('connection-text').textContent, 'Connected · boards offline');
    page.deliver(bridge.greeting); // the PC is back, and the relay greets the page again
    await page.settled();
    assert.match(page.el('content').innerHTML, /Next · the tool’s own pick/);
    assert.doesNotMatch(page.el('content').innerHTML, /banner error/);

    page.deliver({ type: 'refused', reason: 'This account cannot see the boards.' });
    assert.match(page.el('content').innerHTML, /banner error">This account cannot see the boards\.</);
    assert.equal(page.el('connection-text').textContent, 'This account cannot see the boards.');
    await until(() => bridge.real.readyState === 3, 'the refused socket closed');
    await new Promise(ok => setTimeout(ok, JSON.parse(live.settingsJson).transport.reconnectMs + 300));
    assert.equal(page.bridge(), bridge, 'a refused page does not reconnect');
    clean(page.el('content').innerHTML);
  } finally { await live.close(); }
});
