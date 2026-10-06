// Runs the real browser entry (public/app.js) with a minimal DOM stand-in. Its
// settings are the ones the real server embeds in index.html, and its socket is
// bridged to the real server for a real fixture board: every typed request the
// page sends is answered by the server, and every push reaches the page. This
// catches runtime errors, missing data and stale claims; it is not a visual check.
import test from 'node:test';
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
import { join } from 'node:path';
import { renameSync, readFileSync, writeFileSync, mkdirSync, rmSync, copyFileSync } from 'node:fs';
import { DatabaseSync } from 'node:sqlite';
import { startDashboard, createApp } from '../server.mjs';
import { createMonitor, combineMonitors, defaults } from '../lib/board.mjs';
import { richProject, loopProject, project, LONG_IDS, sha, tempDir, cleanup, todo, add, testEnv, STAGE, TODO_PY, PYTHON, until, nativeBridge } from './helpers.mjs';

test.after(cleanup);
const PUBLIC = pathToFileURL(join(STAGE, 'public') + '/').href;
const { WebSocket, WebSocketServer } = await import(pathToFileURL(join(STAGE, 'node_modules', 'ws', 'wrapper.mjs')).href);

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
 * With `hold`, the first socket retains actual replies to those question types without delivering them.
 * With `replyTransform`, an explicitly labelled protocol case transforms a response with its actual request.
 * Labelled host and browser contracts, never native evidence: `hostSettings` edits the settings JSON as a
 * relay's host page writes its own; `auth` replaces the host page's sign-in (a deferred or rejecting one);
 * `storage: 'refused'` is a browser whose localStorage throws, as a privacy mode or a sandboxed frame does.
 */
async function renderApp(live, hash, { published = null, token = null, hold = [], replyTransform = null, hostSettings = null, auth: signIn = null, storage = null, greeted = true, protocol = 'http:' } = {}) {
  for (const socket of openSockets) socket.terminate();
  openSockets.clear();
  const elements = new Map(), bridges = [], listeners = {}, windowListeners = {};
  const el = id => { if (!elements.has(id)) elements.set(id, new Element(id)); return elements.get(id); };
  const pageSettings = published ? { ...JSON.parse(live.settingsJson), published } : JSON.parse(live.settingsJson);
  el('dashboard-settings').textContent = JSON.stringify(hostSettings ? hostSettings(pageSettings) : pageSettings);
  const errors = [];
  class Bridge {
    static OPEN = 1;
    constructor(url) {
      // Published with a sign-in, it acts as the relay does: nothing reaches the page before the token.
      Object.assign(this, { url, messages: [], withheldReplies: [], greeting: null, held: published?.auth ? [] : null, readyState: 0, sent: 0, answered: 0, greeted: false, last: Date.now() });
      this.holding = bridges.length === 0 ? new Set(hold) : new Set();
      bridges.push(this);
      this.real = new WebSocket(live.wsUrl, { headers: { Origin: live.origin } });
      openSockets.add(this.real);
      this.real.on('open', () => { this.readyState = 1; this.onopen?.(); this.labelAtOpen = el('connection-text').textContent; });
      this.real.on('message', data => { if (this.held) this.held.push(String(data)); else this.receive(String(data)); });
      this.real.on('close', () => { this.readyState = 3; if (this.forwardClose) this.onclose?.(); });
      this.real.on('error', () => {});
    }
    receive(text) {
      let message = JSON.parse(text);
      if (message.type === 'reply') {
        this.answered++;
        const request=this.messages.find(m=>m.id===message.id);
        if(this.holding.has(request?.type)){this.withheldReplies.push(message);return;} // held actual response, unchanged
        if(replyTransform){message=replyTransform(message,request);text=JSON.stringify(message);}
      }
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
  // The host page's sign-in, as a relay's host page gives it: the token, and `forget` for a refused one.
  const auth = signIn ?? (token ? Object.assign(async () => token, { forgotten: 0 }) : undefined);
  if (auth && !signIn) auth.forget = () => { auth.forgotten++; };
  const browserWindow = { matchMedia: () => ({ matches: false, addEventListener() {} }), addEventListener: (type, fn) => { (windowListeners[type] ??= []).push(fn); if (type === 'pagehide') pageHides.push(fn); }, dashboardAuth: auth };
  if (storage === 'refused') Object.defineProperty(browserWindow, 'localStorage', { get() { throw new Error('The operation is insecure.'); } });
  else browserWindow.localStorage = { getItem: () => null, setItem() {} };
  // The page's kept <details> as the content's HTML has them: the only elements the stand-in parses.
  const keptDetails = () => [...el('content').innerHTML.matchAll(/ data-keep="([^"]*)"( open)?/g)].map(m => ({ open: !!m[2], dataset: { keep: unescapeAttr(m[1]) } }));
  const globals = {
    // The page's one theme button stands in for index.html's [data-theme-toggle] buttons.
    document: { getElementById: el, querySelector: () => el('shell'), querySelectorAll: sel => sel === 'details[data-keep]' ? keptDetails() : sel === '[data-theme-toggle]' ? [el('theme-toggle')] : [], addEventListener: (type, fn) => (listeners[type] ??= []).push(fn), documentElement: { dataset: {} }, body: new Element('body'), activeElement: null, visibilityState: 'visible', fullscreenElement: null },
    window: browserWindow,
    location: { hash, protocol, host: '127.0.0.1:1' }, history: { replaceState: (a, b, h) => { globalThis.location.hash = h; } },
    navigator: {}, WebSocket: Bridge,
  };
  for (const [name, value] of Object.entries(globals)) Object.defineProperty(globalThis, name, { value, configurable: true, writable: true });
  try { await import(PUBLIC + 'app.js?run=' + (++run)); } catch (error) { errors.push(error); }
  const settled = () => until(() => { const b = bridges.at(-1); return b && b.greeted && b.sent === b.answered && Date.now() - b.last > 60; }, 'the page asked and was answered');
  // `greeted: false` returns as soon as the page has loaded, for a sign-in that has not answered yet.
  if (greeted) await settled();
  else await until(() => bridges.at(-1)?.readyState === 1, 'the page’s socket opened');
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
  /** A click whose target is inside the elements `closest` names, e.g. { '[data-expand]': { dataset: { expand: 'x' } } }. */
  const click = closest => { for (const fn of listeners.click || []) fn({ target: { closest: sel => closest[sel] ?? null } }); };
  /** The reader opens or closes a kept <details>: its open attribute changes, as a click on its summary changes it. */
  const toggle = (key, open) => {
    const at = ` data-keep="${key}"`, html = el('content').innerHTML.replace(at + ' open', at);
    el('content').innerHTML = open ? html.replace(at, at + ' open') : html;
  };
  return { el, errors, settled, loadAll, deliver, click, toggle, auth, document: globals.document,
    emit: (type,event={}) => { for (const fn of listeners[type] || []) fn(event); },
    emitWindow: (type,event={}) => { for (const fn of windowListeners[type] || []) fn(event); },
    change: (id,type,value) => { el(id).value=value; for (const fn of el(id).listeners[type] || []) fn({target:el(id)}); },
    bridge: () => bridges.at(-1) };
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

/**
 * A real monitor served by the public app: its page settings and socket, as serve() gives them for a started
 * dashboard. `settings`, when given, is createApp's own public settings argument: the page and its session
 * both use it (labelled caller settings, not the shipped command line's).
 */
async function serveMonitor(monitor, settings) {
  const server = createApp(monitor, { WebSocket, WebSocketServer }, settings);
  await new Promise((ok, fail) => { server.once('error', fail); server.listen(0, '127.0.0.1', ok); });
  const url = `http://127.0.0.1:${server.address().port}/`;
  const settingsJson = (await (await fetch(url)).text()).match(/id="dashboard-settings"[^>]*>(.*?)<\/script>/s)[1];
  return { started: { url }, settingsJson, origin: url.replace(/\/$/, ''), wsUrl: url.replace('http', 'ws') + 'api/live', view: () => monitor.snapshot(),
    close: async () => { monitor.close(); await new Promise(ok => server.close(ok)); } };
}

/** A copy of todo.py in its own folder with one edit, as a project can change its own tool. */
function toolCopy(edit) {
  const path = join(tempDir('render tool copy '), 'todo.py'), source = readFileSync(TODO_PY, 'utf8'), changed = edit(source);
  assert.notEqual(changed, source, 'the edit applied');
  writeFileSync(path, changed);
  return path;
}
const nl = source => source.includes('\r\n') ? '\r\n' : '\n';

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
    assert.ok(html.includes(' data-keep="order:board"><summary>The order as board.py’s board_order() gives it'), 'the tool’s order starts folded');
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

test('the tool’s order and how it decides start folded, and stay as the reader leaves them through new cards and live changes', { timeout: 120000 }, async () => {
  const p = richProject();
  const live = await serve(p);
  try {
    await until(() => live.view()?.boards[0].queue.state === 'ready', 'picker');
    const page = await renderApp(live, '#report');
    assert.deepEqual(page.errors, []);
    const html = () => page.el('content').innerHTML, order = ' data-keep="order:board"', decides = ' data-keep="decides:board"';
    assert.ok(html().includes(order + '><summary>Exact output of todo.py next') && html().includes(decides + '><summary>How todo.py decides'), 'both start folded');
    page.toggle('order:board', true);
    assert.match(html(), /data-more-list=/, 'more cards are waiting');
    await page.loadAll();
    assert.doesNotMatch(html(), /data-more-list=/, 'the cards came, so the page was drawn again');
    assert.ok(html().includes(order + ' open>') && html().includes(decides + '>'), 'more cards change neither');
    page.toggle('order:board', false); page.toggle('decides:board', true);
    assert.ok(!html().includes('Resume · already started'));
    todo(p.root, 'move', 'MP-004', 'in_progress');
    await until(() => html().includes('Resume · already started'), 'the change drawn on the page');
    assert.ok(html().includes(order + '>') && html().includes(decides + ' open>'), 'a live change changes neither');
    clean(html());
  } finally { await live.close(); }
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

    // Reconnect after a refusal signs in again (the host page forgot the refused token), and the
    // greeting that follows clears the refusal.
    assert.equal(page.auth.forgotten, 1, 'the refused token is forgotten');
    page.el('refresh').listeners.click[0]();
    await page.settled();
    assert.notEqual(page.bridge(), bridge, 'a new socket');
    assert.equal(page.bridge().messages[0].type, 'auth', 'it signs in first again');
    assert.match(page.el('content').innerHTML, /Next · the tool’s own pick/);
    assert.notEqual(page.el('connection-text').textContent, 'This account cannot see the boards.');
    assert.deepEqual(page.errors, []);
  } finally { await live.close(); }
});

test('published, Relax opened from a bookmark signs in first and then shows Relax', { timeout: 120000 }, async () => {
  const p = richProject();
  const live = await serve(p);
  try {
    await until(() => live.view()?.boards[0].queue.state === 'ready', 'picker');
    const page = await renderApp(live, '#relax', { published: { livePath: '/progress/live', auth: 'google' }, token: 'signed-in-token' });
    assert.deepEqual(page.errors, []);
    assert.deepEqual(page.bridge().messages[0], { type: 'auth', token: 'signed-in-token' }, 'the sign-in goes first');
    await until(() => page.bridge().messages.some(m => m.type === 'relax'), 'Relax asked for once greeted');
    await page.settled();
    assert.match(page.el('ambient-pane').innerHTML, /Small medium/);
  } finally { await live.close(); }
});

test('published, Reconnect with Relax waiting signs in before anything is asked again', { timeout: 120000 }, async () => {
  const p = richProject();
  const live = await serve(p);
  try {
    await until(() => live.view()?.boards[0].queue.state === 'ready', 'picker');
    const page = await renderApp(live, '#relax', { published: { livePath: '/progress/live', auth: 'google' }, token: 'signed-in-token', hold: ['relax'] });
    assert.ok(page.bridge().messages.some(m => m.type === 'relax'), 'Relax asked, and its answer is held');
    page.el('refresh').listeners.click[0](); // Reconnect, with the page still holding the old greeting's data
    await page.settled();
    const second = page.bridge();
    assert.equal(second.messages[0].type, 'auth', 'the sign-in goes first on the new socket');
    assert.ok(second.messages.some(m => m.type === 'relax'), 'Relax asks again once greeted');
    assert.match(page.el('ambient-pane').innerHTML, /Small medium/);
    assert.deepEqual(page.errors, []);
  } finally { await live.close(); }
});

test('an open task record says when the boards go offline, through every redraw', { timeout: 120000 }, async () => {
  const p = richProject();
  const live = await serve(p);
  try {
    await until(() => live.view()?.boards[0].queue.state === 'ready', 'picker');
    const page = await renderApp(live, '#task=' + encodeURIComponent('board:MP-006'));
    assert.ok(page.el('task-dialog').open && /Findings filed from this task/.test(page.el('detail-content').innerHTML), 'the record is open');
    page.deliver({ type: 'offline', reason: 'The boards are offline: the PC that has them is not connected.' });
    const note = /^<div class="banner error">The boards are offline: the PC that has them is not connected\.<\/div>/;
    assert.match(page.el('detail-content').innerHTML, note);
    page.click({ '[data-expand]': { dataset: { expand: 'board:MP-006:story' } } }); // a section opened: the record redraws
    assert.match(page.el('detail-content').innerHTML, note, 'the note stays through the redraw');
    assert.deepEqual(page.errors, []);
  } finally { await live.close(); }
});

test('after Reconnect the page says Live only once the new socket is greeted', { timeout: 120000 }, async () => {
  const p = richProject();
  const live = await serve(p);
  try {
    await until(() => live.view()?.boards[0].queue.state === 'ready', 'picker');
    const page = await renderApp(live, '#report');
    assert.equal(page.el('connection-text').textContent, 'Live · file watcher');
    page.el('refresh').listeners.click[0]();
    await page.settled();
    assert.notEqual(page.bridge().labelAtOpen, 'Live · file watcher', 'not Live from the old greeting when the new socket opens');
    assert.equal(page.el('connection-text').textContent, 'Live · file watcher', 'Live once greeted');
    assert.deepEqual(page.errors, []);
  } finally { await live.close(); }
});

test('Reconnect settles the questions in flight, and the Changes view loads its history again', { timeout: 120000 }, async () => {
  const p = richProject();
  const live = await serve(p);
  try {
    await until(() => live.view()?.boards[0].queue.state === 'ready', 'picker');
    todo(p.root, 'move', 'MP-004', 'in_progress');
    await until(() => live.view()?.boards[0].queue.headId === 'MP-004', 'the change');
    const page = await renderApp(live, '#changes', { hold: ['changes'] });
    const first = page.bridge();
    assert.ok(first.messages.some(m => m.type === 'changes'), 'the history was asked for, and its answer is held');
    page.el('refresh').listeners.click[0](); // Reconnect while it waits
    await page.settled();
    assert.notEqual(page.bridge(), first, 'a new socket');
    assert.equal(page.el('toast').textContent, 'The dashboard reconnected.', 'the waiting question was settled');
    assert.ok(page.bridge().messages.some(m => m.type === 'changes'), 'the Changes view asked again on the new socket');
    assert.match(page.el('content').innerHTML, /in_progress/);
    assert.deepEqual(page.errors, []);
  } finally { await live.close(); }
});

test('a greeting replaces the changes queued while the view was paused', { timeout: 120000 }, async () => {
  const p = richProject();
  const live = await serve(p);
  try {
    await until(() => live.view()?.boards[0].queue.state === 'ready', 'picker');
    const page = await renderApp(live, '#report');
    assert.deepEqual(page.errors, []);
    const pause = () => page.el('pause').listeners.click[0]();
    pause();
    // Real pushes while paused: the board fails to read (moved away), then reads again.
    const first = page.bridge();
    let pushes = 0;
    first.real.on('message', data => { if (JSON.parse(String(data)).type === 'changed') pushes++; });
    await until(() => { try { renameSync(p.db, p.db + '.aside'); return true; } catch { return false; } }, 'move the board away');
    await until(() => live.view()?.boards[0].available === false && pushes > 0, 'the failed read pushed to the paused page');
    renameSync(p.db + '.aside', p.db);
    await until(() => live.view()?.boards[0].available === true && live.view().boards[0].queue.state === 'ready', 'read again');
    assert.match(page.el('content').innerHTML, /Next · the tool’s own pick/, 'paused: nothing drawn from the queued changes yet');
    page.el('refresh').listeners.click[0](); // Reconnect: a new socket, and the server's current greeting
    await page.settled();
    assert.notEqual(page.bridge(), first);
    pause(); // resume
    await page.settled();
    assert.doesNotMatch(page.el('content').innerHTML, /could not be read/, 'the changes queued before the greeting are not applied');
    assert.match(page.el('content').innerHTML, /Next · the tool’s own pick/);
    assert.deepEqual(page.errors, []);
  } finally { await live.close(); }
});

test('changes queued while paused are applied in order on resume', { timeout: 120000 }, async () => {
  const p = richProject('resume applies queued');
  const live = await serve(p);
  try {
    await until(() => live.view()?.boards[0].queue.state === 'ready', 'picker');
    const page = await renderApp(live, '#report');
    const pause = () => page.el('pause').listeners.click[0]();
    pause();
    assert.equal(page.el('connection-text').textContent, 'View paused');
    todo(p.root, 'move', 'MP-004', 'in_progress');
    await until(() => live.view()?.boards[0].queue.headId === 'MP-004', 'the change pushed while paused');
    await new Promise(ok => setTimeout(ok, 200));
    assert.doesNotMatch(page.el('content').innerHTML, /Resume · already started/, 'nothing is drawn while paused');
    pause(); // resume: every queued change is applied
    await until(() => page.el('content').innerHTML.includes('Resume · already started'), 'the queued change drawn');
    assert.deepEqual(page.errors, []);
  } finally { await live.close(); }
});

test('filters, manual recheck, absent record and close/error notices follow the reader', {timeout:120000},async context=>{
  const p=richProject('reader causal paths'),live=await serve(p);
  try{
    await until(()=>live.view()?.boards[0].queue.state==='ready','reader picker');
    const page=await renderApp(live,'#boards'),before=sha(p.db);
    for(const [id,value] of [['area','api'],['type','missing'],['topic','missing'],['phase','none'],['severity','low'],['sort','severity'],['verification','tested:unknown']]){
      page.change(id,'change',value);await page.settled();
    }
    page.change('search','input','Foundation');await page.settled();
    for(const fn of page.el('clear').listeners.click)fn({});
    await page.settled();
    assert.equal(page.el('search').value,'');assert.equal(page.el('severity').value,'all');assert.equal(page.el('sort').value,'next');
    const oldFetch=globalThis.fetch;
    // The page's relative URL has the real origin in a browser. The Node DOM
    // stand-in explicitly binds it to this real synthetic server.
    globalThis.fetch=(url,options)=>oldFetch(new URL(url,live.started.url),options);
    try{
      page.click({'[data-recheck-queue]':{}});
      await until(()=>/Ran todo.py next again/.test(page.el('toast').textContent),'actual canonical manual recheck');
    }finally{globalThis.fetch=oldFetch;}
    assert.equal(sha(p.db),before);
    // A task removed for real while the page still shows its card: the list refreshes are held, as a
    // slow reply leaves them, so the stale card stays visible and can be opened.
    await page.loadAll();
    assert.ok(page.el('content').innerHTML.includes('data-task="board:MP-009"'),'the dropped card is on the page');
    page.bridge().holding.add('list');
    todo(p.root,'rm','MP-009','--reason','removed while the page showed it');
    await until(()=>live.view()?.boards[0].total===10,'the removal pushed');
    await until(()=>page.bridge().withheldReplies.length>0,'the page asked for its lists again');
    assert.ok(page.el('content').innerHTML.includes('data-task="board:MP-009"'),'the stale card is still drawn');
    page.click({'[data-task]':{dataset:{task:'board:MP-009'}}});
    await until(()=>/Task no longer present/.test(page.el('detail-content').innerHTML),'the record answer');
    assert.match(page.el('detail-content').innerHTML,/Look in Changes for its previous record/);
    page.bridge().holding.delete('list');
    for(const frame of page.bridge().withheldReplies.splice(0))page.deliver(frame);
    // Declared browser error-event double, distinct from the real close below.
    page.bridge().onerror();assert.equal(page.el('connection-text').textContent,'Connection failed');
    // Real socket termination invokes the actual close handler; the supported
    // test clock confines the pending reconnect timer to this one scenario.
    context.mock.timers.enable({apis:['setTimeout']});
    const bridge=page.bridge();bridge.forwardClose=true;
    const closed=new Promise(ok=>bridge.real.once('close',ok));bridge.real.terminate();await closed;
    assert.equal(page.el('refresh-note').textContent,'Local files · Read only · WebSocket disconnected');
    bridge.forwardClose=false;
    assert.deepEqual(page.errors,[]);
  }finally{await live.close();}
});
test('a first unreadable board reports no zero counts and successful rows recover', {timeout:120000},async()=>{
  // startDashboard reads the board to resolve it, so a board unreadable from the first read is served
  // through the public monitor and app instead: an exclusive writer holds this rollback-journal board
  // while the monitor makes its first read. (An idle WAL board would be read immutable, past the lock.)
  const p=richProject('first unreadable reader'),locker=new DatabaseSync(p.db);
  locker.exec('BEGIN EXCLUSIVE; UPDATE task SET title = title');
  const settings=structuredClone(defaults);settings.read={busyMs:50};
  const monitor=createMonitor({dbPath:p.db,projectRoot:p.root,dataDir:tempDir('first failure history '),settings});
  let locked=true;
  const live=await serveMonitor(monitor);
  try{
    assert.equal(monitor.snapshot().boards[0].available,false);assert.equal(monitor.snapshot().boards[0].stale,false);
    for(const hash of ['#report','#boards']){
      const page=await renderApp(live,hash);
      assert.match(page.el('content').innerHTML,/Nothing is shown from it until a read succeeds/,hash);
      assert.doesNotMatch(page.el('content').innerHTML,/This board has no tasks|<td>0<\/td>/,hash+': no zero counts');
      assert.deepEqual(page.errors,[]);
    }
    const page=await renderApp(live,'#boards');
    locker.exec('ROLLBACK');locker.close();locked=false;
    todo(p.root,'edit','MP-001','--note','written once the lock is gone'); // a real write: a file event and a new read
    await until(()=>page.el('content').innerHTML.includes('Foundation'),'the rows drawn once the board reads');
    assert.deepEqual(page.errors,[]);
  }finally{if(locked){locker.exec('ROLLBACK');locker.close();}await live.close();}
});

test('two boards, one unreadable from its first read: the readable one is reported, the other says it cannot be read', {timeout:120000},async()=>{
  const good=richProject('readable beside a locked one'),bad=richProject('locked from the first read'),locker=new DatabaseSync(bad.db);
  locker.exec('BEGIN EXCLUSIVE; UPDATE task SET title = title');
  const settings=structuredClone(defaults);settings.read={busyMs:50};
  const monitor=combineMonitors([
    createMonitor({dbPath:good.db,projectRoot:good.root,dataDir:tempDir('two boards good '),sourceId:'board',name:'readable'}),
    createMonitor({dbPath:bad.db,projectRoot:bad.root,dataDir:tempDir('two boards bad '),sourceId:'board-2',name:'locked',settings}),
  ]);
  const live=await serveMonitor(monitor);
  try{
    const page=await renderApp(live,'#report');
    const html=page.el('content').innerHTML;
    assert.match(html,/The board could not be read\. Its next pick is unknown until it can be read again\./,'the locked board, read for the first time, has no last data to show');
    assert.match(html,/Foundation/,'the readable board is reported in full');
    assert.deepEqual(page.errors,[]);
  }finally{locker.exec('ROLLBACK');locker.close();await live.close();}
});

// Explicit DOM/transport contracts on actual server request IDs and data.
// They do not claim native rendering, relay/account events or OS outcomes.
test('labelled typed list/row/record refusals stay visible without inventing missing records', {timeout:120000},async()=>{
  const p=richProject('controlled typed UI refusals'),live=await serve(p),before=sha(p.db);
  try {
    await until(()=>live.view()?.boards[0].queue.state==='ready','typed UI picker');
    for(const [type,hash,target] of [['list','#report','content'],['rows','#data','content'],['task','#task='+encodeURIComponent('board:MP-006'),'detail-content']]) {
      const reason='Controlled '+type+' reply refusal';
      const page=await renderApp(live,hash,{replyTransform:(message,request)=>request?.type===type?{type:'reply',id:message.id,error:reason}:message});
      assert.ok(page.el(target).innerHTML.includes(reason),reason+' is visibly represented by the controlled DOM contract');
      if(type==='task')assert.match(page.el(target).innerHTML,/record could not be read/);
      assert.doesNotMatch(page.el(target).innerHTML,/Task no longer present/,'a refusal differs from a genuinely missing record');
      assert.deepEqual(page.errors,[]);
    }
    assert.equal(sha(p.db),before);
  } finally {await live.close();}
});

test('held genuine list/summary replies from old filters cannot replace current query results', {timeout:120000},async()=>{
  const p=richProject('controlled stale UI replies'),live=await serve(p),before=sha(p.db);
  try {
    await until(()=>live.view()?.boards[0].queue.state==='ready','stale UI picker');
    const page=await renderApp(live,'#boards',{hold:['list','summary']}),bridge=page.bridge();
    assert.match(page.el('content').innerHTML,/Loading/,'typed lists are deliberately still in flight');
    page.change('search','input','Foundation');await page.settled();
    const current=bridge.withheldReplies.filter(frame=>bridge.messages.find(r=>r.id===frame.id)?.filters?.query==='Foundation');
    const previous=bridge.withheldReplies.filter(frame=>bridge.messages.find(r=>r.id===frame.id)?.filters?.query!=='Foundation');
    assert.ok(current.some(f=>bridge.messages.find(r=>r.id===f.id)?.type==='list'));
    assert.ok(previous.some(f=>bridge.messages.find(r=>r.id===f.id)?.type==='list'));
    for(const frame of current)page.deliver(frame);
    await page.settled();
    assert.match(page.el('content').innerHTML,/Foundation/);
    assert.doesNotMatch(page.el('content').innerHTML,/data-card-key="board:MP-004"/);
    for(const frame of previous)page.deliver(frame);
    await page.settled();
    assert.doesNotMatch(page.el('content').innerHTML,/data-card-key="board:MP-004"/,'old genuine full-list data cannot repopulate a new filter cache');
    assert.match(page.el('filter-summary').textContent,/Showing/);
    assert.deepEqual(page.errors,[]);assert.equal(sha(p.db),before);
  } finally {await live.close();}
});

test('late genuine task replies keep the newly selected drawer and unknown frames are ignored', {timeout:120000},async()=>{
  const p=richProject('controlled late drawer reply'),live=await serve(p),before=sha(p.db);
  try {
    await until(()=>live.view()?.boards[0].queue.state==='ready','drawer picker');
    const page=await renderApp(live,'#task='+encodeURIComponent('board:MP-006'),{hold:['task']}),bridge=page.bridge();
    assert.match(page.el('detail-content').innerHTML,/Loading the task/);
    page.click({'[data-task]':{dataset:{task:'board:MP-010'}}});await page.settled();
    const reply=key=>bridge.withheldReplies.find(frame=>bridge.messages.find(r=>r.id===frame.id)?.key===key);
    const current=reply('board:MP-010'),previous=reply('board:MP-006');
    assert.ok(current);assert.ok(previous);
    page.deliver(current);await page.settled();
    assert.match(page.el('detail-content').innerHTML,/Second done/);
    page.deliver(previous);await page.settled();
    assert.match(page.el('detail-content').innerHTML,/Second done/);
    assert.doesNotMatch(page.el('detail-content').innerHTML,/Shipped thing/,'an older selected task cannot replace the new drawer');
    const content=page.el('content').innerHTML,detail=page.el('detail-content').innerHTML;
    page.deliver({type:'reply',id:'no pending request',data:{}});page.deliver({type:'unassigned-contract-frame'});
    assert.equal(page.el('content').innerHTML,content);assert.equal(page.el('detail-content').innerHTML,detail);
    assert.deepEqual(page.errors,[]);assert.equal(sha(p.db),before);
  } finally {await live.close();}
});

/** The text of one card, by its task key. */
const cardOf = (html, key) => { const at = html.indexOf(`data-card-key="${key}"`); return at < 0 ? '' : html.slice(at, html.indexOf('</article>', at)); };

// RE-317 on the page: the real todo.py and its picker decide, the page only says so.
test('a started task whose parent was named after it started is shown waiting; a blocked one stays blocked', { timeout: 120000 }, async () => {
  const p = richProject('started waits on a parent');
  todo(p.root, 'move', 'MP-003', 'in_progress'); // its parent MP-001 is still backlog
  todo(p.root, 'edit', 'MP-1000', '--parent', 'MP-001');
  todo(p.root, 'move', 'MP-1000', 'in_progress');
  todo(p.root, 'move', 'MP-1000', 'blocked', '--reason', 'Paused by the owner');
  const live = await serve(p);
  try {
    await until(() => live.view()?.boards[0].queue.state === 'ready', 'picker');
    const wire = await live.full(), b = wire.boards[0], task = id => b.tasks.find(t => t.id === id);
    assert.ok(!b.queue.startedIds.includes('MP-003'), 'todo.py does not resume it');
    assert.match(b.queue.nextText, /MP-003 is in_progress and waits on MP-001 \(backlog\); next does not resume it until every parent is done\./);
    assert.equal(task('MP-003').isDoing, true); assert.equal(task('MP-003').isWaiting, true);
    assert.equal(task('MP-1000').isWaiting, false, 'a blocked started task is shown as blocked'); assert.equal(task('MP-1000').isExplicitlyBlocked, true);
    const page = await renderApp(live, '#boards'); await page.loadAll();
    const html = page.el('content').innerHTML;
    assert.match(cardOf(html, 'board:MP-003'), /Waiting on parents/); assert.match(cardOf(html, 'board:MP-003'), /Not pickable by todo.py next/);
    assert.match(cardOf(html, 'board:MP-1000'), /card-tag warn">Blocked/); assert.doesNotMatch(cardOf(html, 'board:MP-1000'), /Waiting on parents/);
    assert.deepEqual(page.errors, []);
  } finally { await live.close(); }
});

// A copy of todo.py from before RE-317, which resumed started work whatever its parents: an older tool the
// page must still describe truthfully, from its own picker result.
test('an older todo.py that resumes a started task with an unfinished parent is described as doing so', { timeout: 120000 }, async () => {
  const p = richProject('older tool resumes');
  todo(p.root, 'move', 'MP-003', 'in_progress');
  const older = toolCopy(s => s.replace('            and all(parent in done for parent in task["parents"])' + nl(s), ''));
  const live = await serve(p, { todoPy: older });
  try {
    await until(() => live.view()?.boards[0].queue.state === 'ready', 'picker');
    assert.equal(live.view().boards[0].head.id, 'MP-003', 'the older tool resumes it');
    const drawer = (await renderApp(live, '#task=' + encodeURIComponent('board:MP-003'))).el('detail-content').innerHTML;
    assert.match(drawer, /1 of them is not in a status that satisfies a parent \(done\); todo.py still resumes this started task\./);
    assert.match(drawer, /The current pick \(resume\)/);
  } finally { await live.close(); }
});

test('reader schedules: Relax entered from a bare page and over an open record, keys outside Relax, expand and fold, more twice, objectives, sources and statuses', { timeout: 180000 }, async () => {
  const p = richProject('reader schedules');
  todo(p.root, 'edit', 'MP-004', '--desc', 'a long story worth folding, '.repeat(40)); // MP-004 is in the register's first five
  const live = await serve(p);
  try {
    await until(() => live.view()?.boards[0].queue.state === 'ready', 'picker');
    const bare = await renderApp(live, '');
    bare.el('relax-open').listeners.click[0]();
    assert.equal(globalThis.location.hash, '#relax'); assert.equal(bare.el('ambient-display').hidden, false);
    bare.el('ambient-exit').listeners.click[0]();
    assert.equal(globalThis.location.hash, '#report', 'leaving Relax returns to the view it came from');
    bare.emit('keydown', { key: 'Tab', preventDefault() { throw new Error('a key outside Relax is the page’s own'); } });

    const drawer = await renderApp(live, '#task=' + encodeURIComponent('board:MP-006'));
    assert.equal(drawer.el('task-dialog').open, true);
    drawer.el('relax-open').listeners.click[0]();
    assert.equal(drawer.el('task-dialog').open, false, 'Relax closes an open record first');
    drawer.el('ambient-exit').listeners.click[0]();

    const page = await renderApp(live, '#report');
    const html = () => page.el('content').innerHTML;
    const key = html().match(/data-expand="([^"]*MP-004:story)"/)[1];
    page.click({ '[data-expand]': { dataset: { expand: key } } }); assert.match(html(), new RegExp(`data-expand="${key}">Show less`));
    page.click({ '[data-expand]': { dataset: { expand: key } } }); assert.match(html(), new RegExp(`data-expand="${key}">Show more`), 'a second click folds it again');
    const more = html().match(/data-more-list="([^"]+)"/)[1].replaceAll('&quot;', '"');
    page.click({ '[data-more-list]': { dataset: { moreList: more } } }); page.click({ '[data-more-list]': { dataset: { moreList: more } } });
    await page.settled();
    assert.equal(page.bridge().messages.filter(m => m.type === 'list' && m.offset === 5).length, 1, 'a second click while the first loads asks nothing');
    assert.ok(html().includes('data-phase="MVP"'), 'the objectives table names its phases');
    page.click({ '[data-phase]': { dataset: { phase: 'MVP' } } }); await page.settled();
    assert.equal(globalThis.location.hash, '#boards'); assert.equal(page.el('phase').value, 'MVP');
    page.el('clear').listeners.click[0](); page.click({ '[data-view]': { dataset: { view: 'report' } } }); await page.settled();
    assert.ok(html().includes('data-source="board" data-go-view="boards"'), 'the overview names its board');
    page.click({ '[data-source]': { dataset: { source: 'board', goView: 'boards' } } }); await page.settled();
    assert.equal(page.el('breadcrumb').innerHTML.includes('Full board'), true);
    page.click({ '[data-view]': { dataset: { view: 'report' } } }); await page.settled();
    assert.ok(html().includes('data-status="backlog"'), 'the recorded statuses are buttons');
    page.click({ '[data-status]': { dataset: { status: 'backlog' } } }); await page.settled();
    assert.equal(page.el('status').value, 'backlog'); assert.equal(globalThis.location.hash, '#boards');
    assert.deepEqual(page.errors, []);
  } finally { await live.close(); }
});

test('held refreshes: a push while a list loads asks it once, a reset makes its late answer a no-op, and a close settles what waits', { timeout: 120000 }, async context => {
  const p = richProject('held refreshes');
  const live = await serve(p);
  try {
    await until(() => live.view()?.boards[0].queue.state === 'ready', 'picker');
    const page = await renderApp(live, '#boards'), bridge = page.bridge();
    let pushes = 0;
    bridge.real.on('message', data => { if (JSON.parse(String(data)).type === 'changed') pushes++; });
    bridge.holding.add('list');
    todo(p.root, 'move', 'MP-004', 'in_progress');
    await until(() => bridge.withheldReplies.length > 0, 'the refresh after the first push is held');
    const asked = bridge.messages.filter(m => m.type === 'list').length, seen = pushes;
    todo(p.root, 'edit', 'MP-004', '--note', 'a second change while the lists load');
    await until(() => pushes > seen, 'the second push arrived');
    await new Promise(ok => setTimeout(ok, 100));
    assert.equal(bridge.messages.filter(m => m.type === 'list').length, asked, 'a list still loading is not asked again');
    page.change('search', 'input', 'Foundation'); // new filters: the held answers belong to lists that are gone
    bridge.holding.delete('list');
    for (const frame of bridge.withheldReplies.splice(0)) page.deliver(frame);
    await page.settled();
    assert.match(page.el('content').innerHTML, /Foundation/); assert.doesNotMatch(page.el('content').innerHTML, /data-card-key="board:MP-004"/);
    // A real close of the socket while a record waits for its answer settles that question.
    bridge.holding.add('task');
    page.click({ '[data-task]': { dataset: { task: 'board:MP-001' } } });
    await until(() => bridge.withheldReplies.length > 0, 'the record question held');
    context.mock.timers.enable({ apis: ['setTimeout'] }); // the reconnect timer stays in this scenario
    bridge.forwardClose = true;
    const closed = new Promise(ok => bridge.real.once('close', ok)); bridge.real.terminate(); await closed;
    assert.match(page.el('detail-content').innerHTML, /The task’s record could not be read/);
    assert.match(page.el('detail-content').innerHTML, /The dashboard disconnected\./);
    assert.deepEqual(page.errors, []);
  } finally { await live.close(); }
});

test('the history buttons: a task’s full history, more changes and all history, while their answers are held', { timeout: 120000 }, async () => {
  const p = richProject('history buttons');
  const live = await serve(p);
  try {
    await until(() => live.view()?.boards[0].queue.state === 'ready', 'picker');
    todo(p.root, 'move', 'MP-004', 'in_progress');
    await until(() => live.view()?.boards[0].queue.headId === 'MP-004', 'the change');
    const drawer = await renderApp(live, '#task=' + encodeURIComponent('board:MP-004'), { hold: ['changes'] });
    const content = () => drawer.el('detail-content').innerHTML;
    assert.ok(content().includes('data-task-history="board:MP-004"'), 'the drawer offers the task’s full history');
    drawer.click({ '[data-task-history]': { dataset: { taskHistory: 'board:MP-004' } } });
    drawer.bridge().holding.delete('changes');
    for (const frame of drawer.bridge().withheldReplies.splice(0)) drawer.deliver(frame);
    await drawer.settled();
    assert.match(content(), /Observed changes \(\d+\)/); assert.doesNotMatch(content(), /Loading this task’s history/);

    const changes = await renderApp(live, '#changes', { hold: ['changes'] });
    const html = () => changes.el('content').innerHTML;
    assert.ok(html().includes('data-more="changes"') && html().includes('data-all-changes'), 'more and all are offered while the history loads');
    changes.click({ '[data-more]': {} });
    changes.click({ '[data-all-changes]': {} });
    changes.bridge().holding.delete('changes');
    for (const frame of changes.bridge().withheldReplies.splice(0)) changes.deliver(frame);
    await changes.settled();
    assert.ok(changes.bridge().messages.some(m => m.type === 'changes' && m.limit === 'all'), 'all of the history was asked for');
    assert.doesNotMatch(html(), /data-all-changes/, 'nothing is left to load');
    changes.change('search', 'input', 'MP-004');
    assert.match(html(), /match</);
    assert.deepEqual(changes.errors, []);
  } finally { await live.close(); }
});

// Labelled host-page, relay and browser contracts on a real server's frames: none is native host,
// relay, account or browser evidence.
test('labelled host and browser contracts: a host page with no project, a refused storage, a cross-site recheck and sign-ins that wait or fail', { timeout: 180000 }, async () => {
  const p = richProject('__');
  const live = await serve(p);
  try {
    await until(() => live.view()?.boards[0].queue.state === 'ready', 'picker');
    const symbols = await renderApp(live, '#report');
    assert.equal(symbols.el('brand-mark').textContent, 'T', 'a project named only with symbols has no letter to show');
    const host = await renderApp(live, '#report', { hostSettings: settings => { const { project, ...rest } = settings; return rest; } });
    assert.match(host.el('brand-name').innerHTML, /^Project<small>/, 'a host page that names no project');
    const refused = await renderApp(live, '#report', { storage: 'refused' });
    refused.el('theme-toggle').listeners.click[0]();
    assert.match(refused.el('toast').textContent, /Browser storage is unavailable/);
    const oldFetch = globalThis.fetch;
    // A recheck sent with a header no page sends: the real server refuses it, and the page says so.
    globalThis.fetch = (url, options) => oldFetch(new URL(url, live.started.url), { ...options, headers: { 'Sec-Fetch-Site': 'cross-site' } });
    try {
      refused.click({ '[data-recheck-queue]': {} });
      await until(() => /Check failed: 403/.test(refused.el('toast').textContent), 'the refused recheck reported');
    } finally { globalThis.fetch = oldFetch; }

    const published = { livePath: '/api/live', auth: 'google' };
    const page = await renderApp(live, '#report', { published, token: 'signed-in-token' });
    page.deliver({ type: 'offline' });
    assert.match(page.el('content').innerHTML, /The boards are offline\./, 'an offline notice without a reason');
    page.deliver(page.bridge().greeting); await page.settled();
    const bridge = page.bridge(); bridge.forwardClose = true;
    const closed = new Promise(ok => bridge.real.once('close', ok)); bridge.real.terminate(); await closed;
    assert.equal(page.el('refresh-note').textContent, 'Published · Read only · disconnected');
    page.el('refresh').listeners.click[0](); await page.settled();
    page.deliver({ type: 'refused' });
    assert.match(page.el('content').innerHTML, /This account cannot see the boards\./, 'a refusal without a reason');

    // A sign-in still pending when the page reconnects: its late token is not sent on the replaced socket.
    let answer;
    const waiting = Object.assign(() => new Promise(ok => { answer = ok; }), { forget() {} });
    const pending = await renderApp(live, '#report', { published, auth: waiting, greeted: false });
    pending.change('search', 'input', 'typed before any greeting'); // nothing to draw yet
    const first = pending.bridge(), late = answer; // the first socket's sign-in, still pending
    pending.el('refresh').listeners.click[0]();
    await until(() => pending.bridge() !== first, 'a new socket');
    late('late-token'); await new Promise(ok => setTimeout(ok, 50));
    assert.equal(first.messages.filter(m => m.type === 'auth').length, 0, 'the replaced socket is never sent the token');
    // A sign-in the host page refuses.
    const rejecting = Object.assign(async () => { throw new Error('the account chooser was closed'); }, { forget() {} });
    const failed = await renderApp(live, '#report', { published, auth: rejecting, greeted: false });
    await until(() => /Not signed in: the account chooser was closed/.test(failed.el('connection-text').textContent), 'the refused sign-in shown');
  } finally { await live.close(); }
});

test('real odd board shapes: legacy stamps, empty and missing fields, phases tied or gone, links to absent tasks, related and keyless tables, and an unused board beside it', { timeout: 240000 }, async () => {
  const p = richProject('odd shapes'), empty = project('unused board ', 'unused');
  const r = (...args) => todo(p.root, ...args);
  r('phase', 'add', 'P3', '--goal', 'a phase with no label', '--position', '2');
  r('edit', 'MP-1000', '--phase', '');
  r('add', '--id', 'MP-011', '--title', 'One finding', '--desc', 'd', '--why', 'w', '--severity', 'low', '--points', '1', '--exit', 'a long enough exit condition', '--parent-task', 'MP-010');
  r('move', 'MP-011', 'done', '--evidence', 'closed the finding');
  r('add', '--id', 'MP-012', '--title', 'Two waits', '--desc', 'd', '--why', 'w', '--severity', 'low', '--points', '1', '--exit', 'a long enough exit condition', '--parent', 'MP-001,MP-002');
  for (const n of [1, 2, 3, 4]) r('edit', 'MP-001', '--note', 'note ' + n);
  r('move', 'MP-004', 'in_progress');
  r('tested', 'MP-010', '--evidence', 'Ran the area suite'); // done, and its one finding is closed
  const db = new DatabaseSync(p.db);
  try {
    for (const { name } of db.prepare("SELECT name FROM sqlite_master WHERE type='trigger'").all()) db.exec(`DROP TRIGGER "${name}"`);
    db.exec(`UPDATE task SET phase='GONE' WHERE id='MP-008';UPDATE task SET area='' WHERE id='MP-002';
      UPDATE task SET closed=NULL WHERE id='MP-010';UPDATE task SET updated=NULL WHERE id='MP-009';
      UPDATE task SET created=1700000000 WHERE id='MP-007';UPDATE task SET created='not a date' WHERE id='MP-005';
      UPDATE task SET tested=2 WHERE id='MP-010';
      INSERT INTO blocked_by (task, parent) VALUES ('MP-012','MP-404');
      CREATE TABLE review(task TEXT, since TEXT, reason TEXT, file TEXT);
      INSERT INTO review VALUES('MP-001','2026-10-01 00:00:00','looked at',NULL),('MP-001',NULL,NULL,'');
      CREATE TABLE measure(id INTEGER PRIMARY KEY, value REAL);INSERT INTO measure VALUES(1, 9e999);`);
  } finally { db.close(); }
  const live = await serve(p, { dbs: [p.db, empty.db] });
  try {
    await until(() => { const s = live.view(); return s?.boards.length === 2 && s.boards.every(b => b.queue.state === 'ready'); }, 'both pickers');
    const report = await renderApp(live, '#report'); await report.loadAll();
    const html = report.el('content').innerHTML;
    for (const text of ['This board file has no task table', 'picks nothing (see its output)', 'None in this result', '1 unfinished task(s) have no objective',
      '>P3</button>'])
      assert.ok(html.includes(text), 'report shows ' + text);
    clean(html);
    const lanes = await renderApp(live, '#boards'); await lanes.loadAll();
    const boardHtml = lanes.el('content').innerHTML;
    for (const text of ['Objective · GONE (not on board)', 'No objective', 'Closure time not recorded', 'Closing time not recorded', '1 finding · 0 open',
      'This board file has no task table: nothing has been recorded yet'])
      assert.ok(boardHtml.includes(text), 'board shows ' + text);
    clean(boardHtml);
    const drawer = async id => { const page = await renderApp(live, '#task=' + encodeURIComponent('board:' + id)); clean(page.el('detail-content').innerHTML); return page.el('detail-content').innerHTML; };
    assert.match(await drawer('MP-002'), /area is empty/);
    assert.match(await drawer('MP-012'), /3 of them are not in a status that satisfies a parent/);
    assert.match(await drawer('MP-012'), /MP-404 · not on this board/);
    assert.match(await drawer('MP-012'), /queue-reason unverified/);
    const one = await drawer('MP-001');
    assert.match(one, /Notes \(4\)/); assert.match(one, /Table review \(2\)/); assert.match(one, /looked at/);
    assert.match(await drawer('MP-004'), /The current pick \(resume\)/);
    assert.match(await drawer('MP-004'), /Recorded status: in_progress/);
    assert.match(await drawer('MP-005'), /Blocked: Waiting for the owner to choose/);
    assert.match(await drawer('MP-007'), /Came out of roasting/);
    assert.match(await drawer('MP-010'), /None is open by todo.py’s open_children\(\)/);
    assert.match(await drawer('MP-010'), /Recorded value: 2/);
    const data = await renderApp(live, '#data');
    assert.match(data.el('content').innerHTML, /This board file has no tables\./);
    for (const [table, shown] of [['review', /Row 2 · task MP-001/], ['measure', /\$float/], ['blocked', /task MP-005/]]) {
      assert.ok(data.el('content').innerHTML.includes(`data-table-name="${table}"`), table + ' is a tab on the page');
      data.click({ '[data-table-name]': { dataset: { tableSource: 'board', tableName: table } } });
      await data.settled();
      assert.match(data.el('content').innerHTML, shown, table);
      clean(data.el('content').innerHTML);
    }
  } finally { await live.close(); }
});

test('older and legacy rows: unknown severities and statuses, a board older than the tool’s schema, and a live WAL', { timeout: 120000 }, async () => {
  const root = join(tempDir('legacy render '), 'legacy project'), path = join(root, '.claude', 'todo.db');
  mkdirSync(join(root, '.claude'), { recursive: true });
  const db = new DatabaseSync(path);
  db.exec(`PRAGMA journal_mode=WAL; PRAGMA wal_autocheckpoint=0;
    CREATE TABLE task (id TEXT PRIMARY KEY, title TEXT NOT NULL, descr TEXT NOT NULL, why TEXT NOT NULL, severity TEXT NOT NULL,
      points INTEGER NOT NULL, status TEXT NOT NULL, exit_cond TEXT NOT NULL, created TEXT NOT NULL DEFAULT (datetime('now')));
    CREATE TABLE blocked_by (task TEXT NOT NULL, parent TEXT NOT NULL, PRIMARY KEY (task, parent));
    CREATE TABLE blocked (task TEXT PRIMARY KEY, reason TEXT NOT NULL, since TEXT NOT NULL);
    INSERT INTO blocked VALUES ('LG-3','The old urgent row is deliberately not offered','fixture time');
    INSERT INTO task (id,title,descr,why,severity,points,status,exit_cond) VALUES
      ('LG-1','Normal low','d','w','low',1,'backlog','long enough exit condition'),
      ('LG-3','Legacy urgent','d','w','urgent',1,'backlog','long enough exit condition'),
      ('LG-4','Legacy status','d','w','high',2,'open','long enough exit condition');`);
  const live = await serve({ db: path, root });
  try {
    const terminal = await until(() => { const b = live.view()?.boards[0]; return b && ['ready','error','unavailable','unconfigured'].includes(b.queue.state) && b; }, 'legacy picker terminal result');
    assert.equal(terminal.queue.state, 'ready', terminal.queue.error || 'the eligible old row should be picked');
    const report = await renderApp(live, '#report');
    const html = report.el('content').innerHTML;
    assert.match(html, / · other <code>open<\/code>/, 'a status the tool neither offers nor closes');
    assert.match(html, /cannot rank LG-3/); assert.match(html, /This board predates part of the tool’s schema/);
    assert.match(html, /WAL \d+ bytes, modified/, 'the board’s uncheckpointed WAL');
    const drawer = (await renderApp(live, '#task=' + encodeURIComponent('board:LG-3'))).el('detail-content').innerHTML;
    assert.match(drawer, /by_rule\(\) cannot rank this task/);
    clean(html); clean(drawer);
  } finally { db.close(); await live.close(); }
});

test('an offered old unknown severity reports a real picker error, then blocking it recovers ready', { timeout: 120000 }, async () => {
  const root = join(tempDir('legacy picker recovery '), 'legacy project'), path = join(root, '.claude', 'todo.db');
  mkdirSync(join(root, '.claude'), { recursive: true });
  const db = new DatabaseSync(path);
  let live;
  try {
    db.exec(`CREATE TABLE task(id TEXT PRIMARY KEY,title TEXT NOT NULL,descr TEXT NOT NULL,why TEXT NOT NULL,
      severity TEXT NOT NULL,points INTEGER NOT NULL,status TEXT NOT NULL,exit_cond TEXT NOT NULL,created TEXT NOT NULL DEFAULT (datetime('now')));
      CREATE TABLE blocked_by(task TEXT,parent TEXT,PRIMARY KEY(task,parent));
      CREATE TABLE blocked(task TEXT PRIMARY KEY,reason TEXT NOT NULL,since TEXT NOT NULL);
      INSERT INTO task(id,title,descr,why,severity,points,status,exit_cond) VALUES
      ('OK-1','Valid ready row','d','w','high',2,'backlog','Run and verify the ordinary picker result'),
      ('OLD-1','Old urgent row','d','w','urgent',1,'backlog','Run and verify the old row is refused');`);
    live = await serve({ db: path, root });
    const terminal = await until(() => { const b=live.view()?.boards[0];return b && ['ready','error','unavailable','unconfigured'].includes(b.queue.state) && b; }, 'old unrankable picker terminal result');
    assert.equal(terminal.queue.state, 'error', terminal.queue.error || 'an offered unknown severity must not become a valid pick');
    assert.match(terminal.queue.error, /OLD-1: its severity urgent is not one of critical, high, medium, low/);
    db.exec("INSERT INTO blocked VALUES('OLD-1','Owner blocks the old row','fixture time')");
    const ready = await until(() => { const b=live.view()?.boards[0];return b?.queue.state==='ready' && b; }, 'picker recovery after the real old-row block');
    assert.equal(ready.queue.error,null);assert.equal(ready.queue.headId,'OK-1');
    const report=await renderApp(live,'#report');
    assert.match(report.el('content').innerHTML,/Old urgent row/);clean(report.el('content').innerHTML);
  } finally { if(live)await live.close();db.close(); }
});

test('modified tool copies: unlabelled and scalar sort keys, a tool that fails, one where nothing satisfies a parent, and two boards rechecked', { timeout: 240000 }, async () => {
  const p = richProject('tool copies');
  const listKey = toolCopy(s => s.replace('    return (' + nl(s) + '        phase_rank(task),', '    key = (' + nl(s) + '        phase_rank(task),')
    .replace('        task["id"],' + nl(s) + '    )' + nl(s) + nl(s) + nl(s) + 'def by_severity', '        task["id"],' + nl(s) + '    )' + nl(s) + '    return key' + nl(s) + nl(s) + nl(s) + 'def by_severity'));
  let live = await serve(p, { todoPy: listKey });
  try {
    await until(() => live.view()?.boards[0].queue.state === 'ready', 'picker');
    assert.equal(live.view().boards[0].rules.rankLabels, null);
    const report = (await renderApp(live, '#report')).el('content').innerHTML;
    assert.match(report, /by_rule\(\) does not return a plain tuple/);
    assert.match((await renderApp(live, '#task=' + encodeURIComponent('board:MP-001'))).el('detail-content').innerHTML, /component 1 = /);
  } finally { await live.close(); }
  const scalar = toolCopy(s => s.replace('    return (' + nl(s) + '        phase_rank(task),', '    return repr((' + nl(s) + '        phase_rank(task),')
    .replace('        task["id"],' + nl(s) + '    )' + nl(s) + nl(s) + nl(s) + 'def by_severity', '        task["id"],' + nl(s) + '    ))' + nl(s) + nl(s) + nl(s) + 'def by_severity'));
  live = await serve(p, { todoPy: scalar });
  try {
    await until(() => live.view()?.boards[0].queue.state === 'ready', 'picker');
    assert.match((await renderApp(live, '#task=' + encodeURIComponent('board:MP-001'))).el('detail-content').innerHTML, /Sort key from by_rule\(\): &quot;/);
  } finally { await live.close(); }
  const failing = toolCopy(s => s + nl(s) + 'def deliberately_invalid(:' + nl(s));
  live = await serve(p, { todoPy: failing });
  try {
    await until(() => live.view()?.boards[0].queue.state === 'error', 'the failing picker');
    const page = await renderApp(live, '#report');
    assert.match(page.el('content').innerHTML, /SyntaxError.*No next pick or order is shown/s);
    const oldFetch = globalThis.fetch;
    globalThis.fetch = (url, options) => oldFetch(new URL(url, live.started.url), options);
    try {
      page.click({ '[data-recheck-queue]': {} });
      await until(() => /todo.py next is unavailable\. See its reason\./.test(page.el('toast').textContent), 'the recheck reported');
    } finally { globalThis.fetch = oldFetch; }
  } finally { await live.close(); }
  // The second `all(parent in done ...)` is eligibility's: with it never true, no status satisfies a parent.
  const unsatisfied = toolCopy(s => { const line = '            and all(parent in done for parent in task["parents"])', at = s.lastIndexOf(line);
    return s.slice(0, at) + '            and all(parent in () for parent in task["parents"])' + s.slice(at + line.length); });
  live = await serve(p, { todoPy: unsatisfied });
  try {
    await until(() => live.view()?.boards[0].queue.state === 'ready', 'picker');
    assert.match((await renderApp(live, '#task=' + encodeURIComponent('board:MP-003'))).el('detail-content').innerHTML, /\(none known\)/);
  } finally { await live.close(); }
  const second = richProject('tool copies second');
  live = await serve(p, { dbs: [p.db, second.db] });
  try {
    await until(() => { const s = live.view(); return s?.boards.length === 2 && s.boards.every(b => b.queue.state === 'ready'); }, 'both pickers');
    const page = await renderApp(live, '#report');
    const oldFetch = globalThis.fetch;
    globalThis.fetch = (url, options) => oldFetch(new URL(url, live.started.url), options);
    try {
      page.click({ '[data-recheck-queue]': {} });
      await until(() => /Checked 2 boards again: 2 ready\./.test(page.el('toast').textContent), 'both boards rechecked');
    } finally { globalThis.fetch = oldFetch; }
  } finally { await live.close(); }
});

test('fresh loop boards, nothing started yet: no status counts as started, and their latest records are minutes and hours old', { timeout: 120000 }, async () => {
  const { cpSync } = await import('node:fs'), { execFileSync } = await import('node:child_process');
  // A new loop board made by its own tool, with one item recorded `ago` seconds before now.
  const freshLoop = (name, ago) => {
    const root = join(tempDir('fresh loop '), name), dir = join(root, 'loop');
    cpSync(join(STAGE, 'tests', 'loop-fixture'), dir, { recursive: true });
    const run = (...args) => execFileSync(PYTHON, ['-B', join(dir, 'board.py'), ...args], { cwd: dir, encoding: 'utf8', env: testEnv({ PYTHONDONTWRITEBYTECODE: '1' }) });
    run('init'); run('add', '--title', 'The first item', '--severity', 'high', '--exit', 'check it', '--created', String(Math.floor(Date.now() / 1000) - ago));
    return { db: join(dir, 'board.db'), root };
  };
  const minutes = freshLoop('minutes old', 30 * 60), hours = freshLoop('hours old', 3 * 3600);
  const live = await serve(minutes, { dbs: [minutes.db, hours.db] });
  try {
    await until(() => { const s = live.view(); return s?.boards.length === 2 && s.boards.every(b => b.queue.state === 'ready'); }, 'both loop pickers');
    const html = (await renderApp(live, '#report')).el('content').innerHTML;
    assert.match(html, /started \(not the board’s default for new work\) <span class="muted">none<\/span>/);
    assert.match(html, /<td>(29|30|31)m ago<span class="subtext">/, 'a record half an hour old');
    assert.match(html, /<td>3h ago<span class="subtext">/, 'a record three hours old');
    clean(html);
  } finally { await live.close(); }
});

// Native on whichever host runs it: the folder really goes, and the watch's own end is what the page shows.
test('removing the board’s folder ends its watch, and the page says the board is not watched', { timeout: 120000 }, async () => {
  const p = richProject('native folder removal');
  const live = await serve(p);
  try {
    await until(() => live.view()?.boards[0].queue.state === 'ready', 'picker');
    await until(() => { try { rmSync(join(p.root, '.claude'), { recursive: true, force: true }); return true; } catch { return false; } }, 'remove the board’s folder');
    await until(() => live.view()?.watchState?.board?.active === false, 'the watch ended');
    const page = await renderApp(live, '#report');
    // The runtime reports the removal as a rename naming the folder or as an error (watchers.test records which):
    // either ends the watch with its own reason.
    assert.match(page.el('content').innerHTML, /Watching NOT ACTIVE · \S/);
    assert.deepEqual(page.errors, []);
  } finally { await live.close(); }
});

test('an unreadable board with no tool, and an unused board moved away: the overview, records, data and lanes say what failed', { timeout: 120000 }, async () => {
  const p = richProject('unreadable without tool'), empty = project('unused moved ', 'unused moved');
  const live = await serve(p, { dbs: [p.db, empty.db], python: join(tempDir('no python '), 'python.exe') });
  try {
    await until(() => live.view()?.boards.length === 2, 'both boards');
    for (const path of [p.db, empty.db]) await until(() => { try { renameSync(path, path + '.aside'); return true; } catch { return false; } }, 'move a board away');
    await until(() => live.view()?.boards.every(b => b.available === false), 'both failures pushed');
    const report = (await renderApp(live, '#report')).el('content').innerHTML;
    assert.match(report, /<span class="subtext">unavailable<\/span>/, 'the overview row, with no policy to count by');
    const data = (await renderApp(live, '#data')).el('content').innerHTML;
    assert.match(data, /banner error">[^<]*· Last successful data/);
    const lanes = (await renderApp(live, '#boards')).el('content').innerHTML;
    assert.match(lanes, /Board unavailable\. /, 'the unused board had no tasks before it was moved');
    const drawer = (await renderApp(live, '#task=' + encodeURIComponent('board:MP-001'))).el('detail-content').innerHTML;
    assert.match(drawer, /Read failed\. Showing the last successful record/);
    for (const path of [p.db, empty.db]) renameSync(path + '.aside', path);
  } finally { await live.close(); }
});

// G1 · ordinary: an owned old board without CHECKs, written with SQL, read by the real server and picker.
test('an old board without CHECKs: a NULL testing value, a drop dated only by updated_at, done rows with and without a date, a started row with no title or severity, an epoch creation time', { timeout: 120000 }, async () => {
  const root = join(tempDir('old rows render '), 'legacy project'), path = join(root, '.claude', 'todo.db');
  mkdirSync(join(root, '.claude'), { recursive: true });
  const db = new DatabaseSync(path);
  try {
    db.exec(`CREATE TABLE task(id TEXT PRIMARY KEY,title TEXT,descr TEXT NOT NULL,why TEXT NOT NULL,severity TEXT,points INTEGER,
      status TEXT NOT NULL,exit_cond TEXT NOT NULL,created,updated_at TEXT,closed TEXT,tested INTEGER);
      CREATE TABLE blocked_by(task TEXT,parent TEXT,PRIMARY KEY(task,parent));
      CREATE TABLE blocked(task TEXT PRIMARY KEY,reason TEXT NOT NULL,since TEXT NOT NULL);
      INSERT INTO task VALUES
      ('OK-1','Valid ready row','d','w','high',2,'backlog','Run and verify the ordinary pick','2026-10-01 00:00:00',NULL,NULL,0),
      ('DN-1','Undated done one','d','w','low',1,'done','Run and verify the first old closure','2026-10-01 00:00:00',NULL,NULL,0),
      ('DN-2','Undated done two','d','w','low',1,'done','Run and verify the second old closure','2026-10-01 00:00:00',NULL,NULL,0),
      ('DN-3','Dated done','d','w','low',1,'done','Run and verify the dated closure','2026-10-01 00:00:00',NULL,'2026-10-02 00:00:00',NULL),
      ('DR-1','Dropped later','d','w','low',1,'dropped','Run and verify the old drop','2026-10-01 00:00:00','2026-10-03 00:00:00',NULL,0),
      ('IP-1',NULL,'d','w',NULL,1,'in_progress','Run and verify the old started row',1700000000,NULL,NULL,0);
      INSERT INTO blocked VALUES('IP-1','Waiting on the owner','2026-10-01 00:00:00');`);
  } finally { db.close(); }
  const before = sha(path), live = await serve({ db: path, root });
  try {
    const terminal = await until(() => { const b = live.view()?.boards[0]; return b && ['ready', 'error', 'unavailable', 'unconfigured'].includes(b.queue.state) && b; }, 'the picker’s result');
    assert.equal(terminal.queue.state, 'ready', terminal.queue.error || 'the valid row keeps the picker ready');
    const lanes = await renderApp(live, '#boards'); await lanes.loadAll();
    const html = lanes.el('content').innerHTML, lane = name => html.slice(html.indexOf(`lane-header">${name}<`)).split('class="lane"')[0];
    assert.deepEqual([...lane('done').matchAll(/data-card-key="board:([^"]+)"/g)].map(m => m[1]), ['DN-3', 'DN-2', 'DN-1'],
      'the dated closure first, then the undated ones in a stable order');
    assert.match(lane('dropped'), /dropped · last updated /, 'the drop is dated by its updated_at alone');
    const untested = (await renderApp(live, '#task=' + encodeURIComponent('board:DN-3'))).el('detail-content').innerHTML;
    assert.match(untested, /Recorded value: NULL/);
    const started = (await renderApp(live, '#task=' + encodeURIComponent('board:IP-1'))).el('detail-content').innerHTML;
    assert.match(started, /title="[^"]* · stored 1700000000"/, 'the epoch creation time is dated in the timeline');
    const relax = (await renderApp(live, '#relax')).el('ambient-pane').innerHTML;
    assert.match(relax, /<code>IP-1<\/code><span>in_progress<\/span><span>1 pt<\/span><\/div>\s*<h3 dir="auto"><\/h3>/,
      'Relax shows no severity and an empty title: nothing is invented');
    clean(html); clean(untested); clean(started);
  } finally { await live.close(); }
  assert.equal(sha(path), before, 'the old board is only read');
});

// G2 · ordinary CLI: an objective with no label, then met.
test('an objective with no label is shown by its name alone, and once it is met the report and Relax say what current_phase() says', { timeout: 120000 }, async () => {
  const p = project('objective render ', 'objective project');
  todo(p.root, 'phase', 'add', 'P1', '--goal', 'Ship the first slice');
  add(p.root, 'OB-001', 'In the first slice', ['--phase', 'P1']);
  const live = await serve(p);
  try {
    const first = await until(() => { const b = live.view()?.boards[0]; return b?.queue.state === 'ready' && b.queue; }, 'picker');
    assert.ok((await renderApp(live, '#report')).el('content').innerHTML.includes('<strong>P1</strong><small dir="auto">Ship the first slice</small>'),
      'the current objective, with no label');
    const lanes = await renderApp(live, '#boards'); await lanes.loadAll();
    assert.ok(lanes.el('content').innerHTML.includes('<span class="card-tag">Objective · P1</span>'));
    todo(p.root, 'phase', 'done', 'P1');
    await until(() => { const q = live.view()?.boards[0].queue; return q?.state === 'ready' && q.checkedAt !== first.checkedAt; }, 'the picker after the objective was met');
    assert.ok((await renderApp(live, '#report')).el('content').innerHTML.includes('current_phase() returns none'));
    assert.ok((await renderApp(live, '#relax')).el('ambient-pane').innerHTML.includes('Every objective is met.'));
  } finally { await live.close(); }
});

// G3 · DOM over real CLI changes on two watched boards; the page size is createApp's public settings argument.
test('changes: one board’s entries, a removed and an added row, a row with no item, search over loaded history and a second page', { timeout: 180000 }, async () => {
  const a = project('changes a ', 'board a'), b = project('changes b ', 'board b');
  add(a.root, 'CH-001', 'Kept row'); add(a.root, 'CH-002', 'Removed row');
  const settings = JSON.parse(JSON.stringify(defaults)); settings.ui.changePageSize = 2;
  const monitors = [a, b].map((p, i) => createMonitor({ dbPath: p.db, projectRoot: p.root, dataDir: tempDir('changes history '), sourceId: 'b' + i, name: 'board ' + 'ab'[i], settings }));
  const live = await serveMonitor(combineMonitors(monitors), settings);
  try {
    add(a.root, 'CH-003', 'Added row');
    todo(a.root, 'rm', 'CH-002', '--reason', 'Owner removed the duplicate');
    todo(a.root, 'phase', 'add', 'P9', '--goal', 'A phase row names no task');
    await until(() => live.view().changeCount >= 3, 'three recorded changes');
    const page = await renderApp(live, '#changes');
    page.change('search', 'input', 'row');
    assert.match(page.el('content').innerHTML, /Search covers loaded changes; load all to search the whole history\./);
    page.change('search', 'input', '');
    assert.match(page.el('content').innerHTML, /Show 2 more changes/);
    page.click({ '[data-more]': {} }); await page.settled();
    const html = page.el('content').innerHTML;
    assert.match(html, /<span class="task-id">phase<\/span>/, 'a row with no item names its table');
    assert.match(html, /\d+ recorded fields/, 'an added or removed row says its fields were recorded');
    assert.match(html, /\+0,0 @@/, 'the removed row’s diff has no new lines');
    clean(html);
    page.click({ '[data-source]': { dataset: { source: 'b1' } } });
    assert.match(page.el('content').innerHTML, /No changes observed yet\./, 'the other board, which did not change');
  } finally { await live.close(); }
});

// G4 · ordinary SQL on an owned board, and a real lock held from before the first read.
test('database records: an empty table, rows named by name, more than a page of rows, and a board locked from its first read', { timeout: 180000 }, async () => {
  const p = richProject('records render');
  const db = new DatabaseSync(p.db);
  try {
    db.exec('CREATE TABLE later(id INTEGER PRIMARY KEY); CREATE TABLE measure(id INTEGER PRIMARY KEY, value REAL);');
    const insert = db.prepare('INSERT INTO measure(value) VALUES (?)');
    for (let i = 0; i < 150; i++) insert.run(i / 2);
  } finally { db.close(); }
  const live = await serve(p);
  try {
    await until(() => live.view()?.boards[0].queue.state === 'ready', 'picker');
    const data = await renderApp(live, '#data');
    const show = async table => { data.click({ '[data-table-name]': { dataset: { tableSource: 'board', tableName: table } } }); await data.settled(); return data.el('content').innerHTML; };
    assert.match(await show('later'), /No stored rows in this table\./);
    assert.match(await show('phase'), /Row 1 · LATER[\s\S]*Row 2 · MVP/, 'named by name, in primary-key order');
    const many = await show('measure');
    assert.match(many, /Showing 100 of 150 rows\./); assert.match(many, /Show 100 more rows/);
    data.click({ '[data-more-records]': { dataset: { moreRecords: JSON.stringify(['board', 'measure']) } } }); await data.settled();
    assert.match(data.el('content').innerHTML, /Showing 150 of 150 rows\./);
  } finally { await live.close(); }
  const q = richProject('records locked'), settings = JSON.parse(JSON.stringify(defaults)); settings.read.busyMs = 1;
  const writer = new DatabaseSync(q.db); writer.exec('BEGIN EXCLUSIVE');
  try {
    const locked = await serveMonitor(createMonitor({ dbPath: q.db, projectRoot: q.root, dataDir: tempDir('records locked history '), settings }));
    try {
      const html = (await renderApp(locked, '#data')).el('content').innerHTML;
      assert.match(html, /<div class="banner error">[^<]+<\/div>/);
      assert.doesNotMatch(html, /Last successful data/, 'nothing was ever read, so nothing older is offered');
    } finally { await locked.close(); }
  } finally { writer.exec('ROLLBACK'); writer.close(); }
});

// G6 · ordinary: the loop fixture tool's own park, which records no time.
test('a parked loop item’s drawer shows its block with no recorded time', { timeout: 120000 }, async () => {
  const p = loopProject('parked render');
  const live = await serve(p);
  try {
    await until(() => live.view()?.boards[0].queue.state === 'ready', 'picker');
    const drawer = (await renderApp(live, '#task=' + encodeURIComponent('board:3'))).el('detail-content').innerHTML;
    assert.match(drawer, /Blocked: Owner decides the wording<\/p><p class="note-line">Since <span class="muted">Not recorded<\/span>/);
  } finally { await live.close(); }
});

// G8 · controlled real child gate: the second picker run (after a real `todo add`) is held; the first ran through.
test('while a real change is checked again, the drawer and lanes show the previous order, the new row after it, and no tool children', { timeout: 180000 }, async () => {
  const p = richProject('held second check'), dir = tempDir('held second picker ');
  const bridge = nativeBridge('wait', dir, { hold: 2 });
  const monitor = createMonitor({ dbPath: p.db, projectRoot: p.root, dataDir: tempDir('held second history '), todo: TODO_PY, python: { command: PYTHON, args: [bridge.path] } });
  const live = await serveMonitor(monitor);
  try {
    await until(() => monitor.snapshot().boards[0].queue.state === 'ready', 'the first real pick');
    add(p.root, 'MP-020', 'Added while checking', ['--severity', 'low', '--points', '1']);
    await until(() => readFileSync(bridge.count, 'utf8') === '2' && monitor.snapshot().boards[0].queue.state === 'checking', 'the held second check');
    const drawer = (await renderApp(live, '#task=' + encodeURIComponent('board:MP-004'))).el('detail-content').innerHTML;
    assert.match(drawer, /Previous position \d+ of \d+/);
    const finding = (await renderApp(live, '#task=' + encodeURIComponent('board:MP-006'))).el('detail-content').innerHTML;
    assert.match(finding, /open by todo\.py’s open_children\(\)\./);
    assert.doesNotMatch(finding, /from todo\.py children\(\)/, 'nothing is claimed from the tool while it is checked again');
    const lanes = await renderApp(live, '#boards'); await lanes.loadAll();
    const html = lanes.el('content').innerHTML, backlog = html.slice(html.indexOf('lane-header">backlog<')).split('class="lane"')[0];
    assert.equal([...backlog.matchAll(/data-card-key="board:([^"]+)"/g)].map(m => m[1]).at(-1), 'MP-020', 'the new row comes after the previous order');
    writeFileSync(bridge.release, 'release');
    await until(() => monitor.snapshot().boards[0].queue.state === 'ready', 'the released second check');
    assert.doesNotMatch((await renderApp(live, '#task=' + encodeURIComponent('board:MP-004'))).el('detail-content').innerHTML, /Previous position/);
  } finally { writeFileSync(bridge.release, 'release'); await live.close(); }
});

// G9 · native on this host: the owned tool folder and then the board folder really go.
test('a removed tool folder and a removed board folder: the page banner and Relax name the directory-removed reason', { timeout: 120000 }, async () => {
  const p = richProject('removed folders'), dir = tempDir('removable tool '), tool = join(dir, 'todo.py');
  copyFileSync(TODO_PY, tool);
  const live = await serve(p, { todoPy: tool });
  try {
    await until(() => live.view()?.boards[0].queue.state === 'ready', 'picker');
    rmSync(dir, { recursive: true }); // only this owned copy; the canonical tool is never removed
    await until(() => live.view()?.watchState?.tool?.active === false, 'the native tool-folder event');
    const page = await renderApp(live, '#report');
    assert.match(page.el('error-banner').textContent, /Tool watcher failed · Watched directory removed: /, 'a different reason (an EPERM error) does not satisfy this');
    await until(() => { try { rmSync(join(p.root, '.claude'), { recursive: true, force: true }); return true; } catch { return false; } }, 'remove the board’s folder');
    await until(() => live.view()?.watchState?.board?.active === false, 'the native board-folder event');
    assert.match((await renderApp(live, '#relax')).el('ambient-pane').innerHTML, /Board watcher: Watched directory removed: /);
  } finally { await live.close(); }
});

// A labelled host contract (DOM): a host page served over https. Real TLS and a real relay are not exercised.
test('published over https, the page opens its socket with wss: on the host page’s live path', { timeout: 120000 }, async () => {
  const p = richProject('https host page');
  const live = await serve(p);
  try {
    await until(() => live.view()?.boards[0].queue.state === 'ready', 'picker');
    const page = await renderApp(live, '#report', { published: { livePath: '/progress/live' }, protocol: 'https:' });
    assert.deepEqual(page.errors, []);
    assert.equal(page.bridge().url, 'wss://127.0.0.1:1/progress/live');
  } finally { await live.close(); }
});
