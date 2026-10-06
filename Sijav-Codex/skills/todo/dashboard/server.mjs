#!/usr/bin/env node
// Read-only live dashboard for a project's .claude/todo.db, or a loop board named with --db.
//   node <skill>/dashboard/server.mjs [--project <dir>] [--db <file>] [--port <n>] [--data-dir <dir>]
//                                     [--python <exe>] [--todo-py <file>] [--tool <file>] [--host 127.0.0.1]
import { createServer } from 'node:http';
import { readFileSync, realpathSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { join, resolve, dirname, basename } from 'node:path';
import { ROOT, KINDS, defaults, encode, BoardError, resolveBoard, resolveDataDir, discoverPython, discoverTool, createMonitor, combineMonitors } from './lib/board.mjs';
import { createSession } from './lib/session.mjs';
import { startPublisher } from './lib/publish.mjs';

const LOOPBACK = new Set(['127.0.0.1', 'localhost', '::1']);
export const USAGE = `Read-only live dashboard for a project's to-do board: the to-do skill's
.claude/todo.db, or a loop board (an SQLite file with item and dep tables).

  node ${join(ROOT, 'server.mjs')} [options]

  --project <dir>    Start the board lookup here instead of the current directory.
                     The nearest existing .claude/todo.db in <dir> or an ancestor
                     is used.
  --db <file>        Use this board file; a loop board is always opened this way. It
                     must exist; nothing is ever created. Any other file is refused.
                     Give --db more than once to show several boards on one page,
                     each read with its own tool and keeping its own history.
  --areas <a,b,...>  For the loop board of the --db before it: offer as next only its
                     items in these areas, as its tool's next --area does for the
                     sessions working them; "-" stands for items with no area. The
                     rest are listed after them, each with its area as the reason.
  --port <n>         Port to listen on. Default 0: a free port chosen by the OS,
                     which changes on every start. Pass a port for a stable address.
  --host <addr>      Loopback address to bind: 127.0.0.1 (default), localhost or ::1.
  --data-dir <dir>   Where change history is kept. Default: a per-user cache folder
                     keyed by the board (never inside the project or the skill).
  --python <exe>     Python 3.9+ for the tool's picker. Default: SIJAV_TODO_PYTHON, then PATH.
  --todo-py <file>   The skill's todo.py. Default: ../todo.py beside this dashboard folder.
  --tool <file>      A loop board's own tool. Default: the .py named after the board
                     file, beside it (<name>.db -> <name>.py). With several loop
                     boards each uses its own, so --tool is refused there.
  --publish <url>    Also publish the page through a relay (wss://...): one outbound
                     socket that answers the relay's questions and pushes changes.
  --publish-token-file <file>
                     The file holding the relay's bearer token (never the token
                     itself on the command line).
  --help             Show this text.`;

export function parseArgs(argv) {
  const options = { project: null, db: null, port: 0, host: '127.0.0.1', dataDir: null, python: null, todoPy: null, tool: null, publish: null, publishTokenFile: null, help: false };
  const names = { '--project': 'project', '--db': 'db', '--areas': 'areas', '--port': 'port', '--host': 'host', '--data-dir': 'dataDir', '--python': 'python', '--todo-py': 'todoPy', '--tool': 'tool', '--publish': 'publish', '--publish-token-file': 'publishTokenFile' };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === '--help' || arg === '-h') { options.help = true; continue; }
    const [flag, inline] = arg.includes('=') ? [arg.slice(0, arg.indexOf('=')), arg.slice(arg.indexOf('=') + 1)] : [arg, null];
    if (!names[flag]) throw new BoardError(`Unknown option ${arg}.\n\n${USAGE}`);
    const value = inline ?? argv[++i];
    if (value == null || value === '') throw new BoardError(`${flag} needs a value.`);
    if (flag === '--areas') { // the areas of the board whose --db came last (the only board when none did)
      const board = Math.max(0, (options.dbs?.length ?? 1) - 1), areas = value.split(',').map(a => a.trim()).filter(Boolean);
      if (!areas.length) throw new BoardError('--areas needs at least one area name.');
      if (options.areas?.[board]) throw new BoardError('--areas is given twice for one board; give it once, after that board\'s --db.');
      (options.areas ??= {})[board] = areas; continue;
    }
    if (flag === '--db') (options.dbs ??= []).push(value);
    if (flag === '--db' && options.db != null) continue; // the first --db stays the main board
    options[names[flag]] = value;
  }
  const port = Number(options.port);
  if (!Number.isInteger(port) || port < 0 || port > 65535) throw new BoardError('--port must be a whole number from 0 to 65535.');
  options.port = port;
  if (!LOOPBACK.has(options.host)) throw new BoardError('--host must be a loopback address (127.0.0.1, localhost or ::1). The dashboard is local only.');
  return options;
}

/**
 * The declared range, unchanged: 22.16 or later on the 22 line, or 24 or later; anything else is
 * refused at start. A board is opened by URL so an idle WAL board can be read immutable, which
 * node:sqlite accepts from v22.15.0 and v23.10.0 (Node API docs, sqlite "History"). The busy wait
 * is SQLite's PRAGMA busy_timeout (lib/board.mjs), which needs no particular Node version.
 */
export function nodeSupported(version = process.versions.node) {
  const [major, minor] = version.split('.').map(Number);
  return major >= 24 || (major === 22 && minor >= 16);
}

const realPath = path => { try { return realpathSync.native(path); } catch { return resolve(path); } };
const samePath = (a, b) => process.platform === 'win32' ? realPath(a).toLowerCase() === realPath(b).toLowerCase() : realPath(a) === realPath(b);

/**
 * The caller's directory. Under `npm start` npm runs in the package folder and
 * records the caller in INIT_CWD. That is trusted only when npm is running
 * THIS package's script: the package.json npm names is this dashboard's (by
 * real path) and the process runs in this folder. npm variables inherited from
 * some other npm process (a parent `npm test`, another package) are ignored.
 */
export function callerDirectory(env = process.env, cwd = process.cwd(), root = ROOT) {
  const ours = env.npm_lifecycle_event && env.INIT_CWD && env.npm_package_json
    && samePath(dirname(env.npm_package_json), root) && samePath(cwd, root);
  return ours ? resolve(env.INIT_CWD) : cwd;
}

/** True when `entry` (process.argv[1]) is this module, through symlinks, junctions or drive-letter case. */
export function isEntry(entry, moduleUrl = import.meta.url) {
  return !!entry && samePath(resolve(entry), fileURLToPath(moduleUrl));
}

const STATIC = { '/': 'index.html', '/app.js': 'app.js', '/board-ui.mjs': 'board-ui.mjs', '/work-context.mjs': 'work-context.mjs', '/styles.css': 'styles.css',
  '/theme.css': 'theme.css', '/ambient.css': 'ambient.css', '/ambient-ui.mjs': 'ambient-ui.mjs', '/display-mode.mjs': 'display-mode.mjs', '/favicon.svg': 'favicon.svg' };
const TYPES = { html: 'text/html; charset=utf-8', js: 'text/javascript; charset=utf-8', mjs: 'text/javascript; charset=utf-8', css: 'text/css; charset=utf-8', svg: 'image/svg+xml' };
const whole = value => value != null && /^\d+$/.test(value) ? Number(value) : null;

/** HTTP + WebSocket front for a monitor. GET/HEAD only; there is no mutation API. */
export function createApp(monitor, { WebSocketServer, WebSocket }, settings = defaults) {
  let hosts = new Set();
  const page = settings.ui.changePageSize;
  const server = createServer(async (req, res) => {
    res.setHeader('Cache-Control', 'no-store'); res.setHeader('X-Content-Type-Options', 'nosniff'); res.setHeader('X-Frame-Options', 'DENY');
    res.setHeader('Referrer-Policy', 'no-referrer');
    const port = server.address()?.port;
    res.setHeader('Content-Security-Policy', `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self' ws://127.0.0.1:${port} ws://localhost:${port}; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'`);
    if (!hosts.has(req.headers.host)) { res.writeHead(403); res.end('Local host only.'); return; }
    if (req.headers.origin && ![...hosts].some(h => 'http://' + h === req.headers.origin)) { res.writeHead(403); res.end('Same-origin access only.'); return; }
    // Browsers label cross-site requests (an <img> on another site, say); only this page and direct visits are served.
    const site = req.headers['sec-fetch-site'];
    if (site && !['same-origin', 'none'].includes(site)) { res.writeHead(403); res.end('Same-origin access only.'); return; }
    if (!['GET', 'HEAD'].includes(req.method)) { res.writeHead(405, { Allow: 'GET, HEAD' }); res.end('Read-only dashboard.'); return; }
    const json = value => { res.setHeader('Content-Type', 'application/json; charset=utf-8'); res.end(req.method === 'HEAD' ? '' : encode(value)); };
    try {
      // Inside the try: a request target Node's HTTP parser accepts but URL rejects is answered as an error,
      // never left as an unhandled rejection that would end the dashboard.
      const url = new URL(req.url, 'http://' + req.headers.host);
      if (url.pathname === '/api/health') { const s = monitor.snapshot(); return json({ app: s.app, readOnly: true, ...s.server, watchState: s.watchState }); }
      if (url.pathname === '/api/snapshot') {
        const snapshot = monitor.snapshot(), etag = '"' + snapshot.instanceId + ':' + snapshot.revision + '"';
        res.setHeader('ETag', etag);
        if (req.headers['if-none-match'] === etag) { res.writeHead(304); res.end(); return; }
        return json(snapshot);
      }
      if (url.pathname === '/api/queue/recheck') return json(await monitor.recheck());
      if (url.pathname === '/api/changes') {
        const q = url.searchParams, item = q.get('item'), before = whole(q.get('before')), after = whole(q.get('after'));
        const limit = q.get('limit') === 'all' ? null : whole(q.get('limit')) ?? (item != null ? null : page);
        const changes = monitor.changes({ before, after, item, limit }), s = monitor.snapshot();
        const total = item != null ? monitor.changes({ item }).length : s.changeCount;
        return json({ changes, total, complete: limit == null || changes.length < limit, trackingSince: s.trackingSince, latestSeq: s.latestSeq });
      }
      if (url.pathname === '/api/export') { res.setHeader('Content-Disposition', 'attachment; filename="todo-board-report.json"'); return json({ ...monitor.snapshot(), changes: monitor.changes() }); }
      const file = STATIC[url.pathname];
      if (file) {
        res.setHeader('Content-Type', TYPES[file.split('.').pop()]);
        let body = readFileSync(join(ROOT, 'public', file));
        if (file === 'index.html') {
          const s = monitor.snapshot();
          const embedded = encode({ ui: settings.ui, transport: settings.transport, project: s.server.project, db: s.server.db }).replaceAll('<', '\\u003c');
          // A function replacement: `$&`, `$'` and `$$` in a path are inserted literally.
          body = body.toString().replace('__DASHBOARD_SETTINGS__', () => embedded);
        }
        res.end(req.method === 'HEAD' ? '' : body); return;
      }
      res.writeHead(404); res.end('Not found.');
    } catch (error) { if (!res.headersSent) res.writeHead(500); res.end('Dashboard error: ' + error.message); }
  });
  const wss = new WebSocketServer({ noServer: true, clientTracking: true, maxPayload: settings.transport.maxMessageBytes });
  const sentUpTo = new WeakMap(), session = createSession(monitor, settings);
  const write = (socket, message) => {
    if (socket.readyState !== WebSocket.OPEN) return;
    if (socket.bufferedAmount > settings.transport.maxBufferedBytes) { socket.terminate(); return; }
    socket.send(encode(message));
  };
  // A (re)connecting client gets each board's details, never a board whole.
  const greet = socket => { sentUpTo.set(socket, monitor.snapshot().latestSeq); write(socket, session.greeting()); };
  // Every request is a typed question with an id; it gets one reply with that id: a page of a
  // list, a task's record, a table's rows, a page of history, the report's totals or Relax's view.
  const reply = (socket, text) => {
    let request = null;
    try { request = JSON.parse(String(text)); } catch {}
    const id = request?.id ?? null;
    try { write(socket, { type: 'reply', id, data: session.answerRequest(request) }); }
    catch (error) { write(socket, { type: 'reply', id, error: error.message }); }
  };
  // A change is pushed as what changed: the boards whose details changed, the keys of the tasks
  // that changed, and the history entries this client has not seen.
  const pushChange = snapshot => {
    const base = session.change(snapshot);
    for (const socket of wss.clients) {
      const changes = monitor.changes({ after: sentUpTo.get(socket) ?? 0 });
      sentUpTo.set(socket, snapshot.latestSeq);
      write(socket, { ...base, changes });
    }
  };
  server.on('upgrade', (req, socket, head) => {
    const site = req.headers['sec-fetch-site'], base = 'http://' + req.headers.host;
    // A target URL cannot parse is refused like any other, never thrown from this listener.
    if (req.method !== 'GET' || !hosts.has(req.headers.host) || req.headers.origin !== base || (site && !['same-origin', 'none'].includes(site))
      || !URL.canParse(req.url, base) || new URL(req.url, base).pathname !== '/api/live') {
      socket.end('HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n'); return;
    }
    wss.handleUpgrade(req, socket, head, ws => wss.emit('connection', ws, req));
  });
  wss.on('connection', socket => { socket.on('error', () => {}); socket.on('message', text => reply(socket, text)); greet(socket); });
  const unsubscribe = monitor.subscribe(pushChange);
  server.on('listening', () => { const p = server.address().port; hosts = new Set([`127.0.0.1:${p}`, `localhost:${p}`, `[::1]:${p}`]); });
  const originalClose = server.close.bind(server);
  server.close = callback => { unsubscribe(); for (const socket of wss.clients) socket.terminate(); wss.close(); server.closeAllConnections?.(); return originalClose(callback); };
  return server;
}

async function loadWs() {
  try { return await import('ws'); }
  catch {
    throw new BoardError(`The dashboard's "ws" package is not installed. Install it once, inside the dashboard folder only:\n  npm ci --omit=dev --prefix "${ROOT}"`);
  }
}

/** Resolve, watch and serve. Returns handles so callers and tests can stop it. */
export async function startDashboard(options = {}, { cwd = process.cwd(), env = process.env } = {}) {
  const asked = options.dbs?.length > 1 ? options.dbs : [options.db ?? null];
  const boards = [];
  for (const [index, db] of asked.entries()) {
    const board = { ...resolveBoard({ cwd, project: options.project ?? null, db }), areas: options.areas?.[index] ?? null };
    if (board.areas && board.kind !== 'loop') throw new BoardError(`--areas is for a loop board; ${board.dbPath} is not one.`);
    if (!boards.some(b => samePath(b.dbPath, board.dbPath))) boards.push(board);
  }
  const loops = boards.filter(b => b.kind === 'loop').length;
  if (boards.length > 1 && loops > 1 && options.tool) throw new BoardError('--tool names one loop board\'s tool, but several loop boards were given; each uses the .py named after its board file.');
  const publish = readPublish(options, cwd); // refused before any board is watched
  const ws = await loadWs();
  // Retain each acquired monitor immediately: a later board or server setup can fail.
  const pieces = [];
  let monitor, server, publisher;
  try {
    for (const [index, board] of boards.entries()) {
      const sourceId = index === 0 ? 'board' : `board-${index + 1}`;
      const dataDir = resolveDataDir({ cwd, dataDir: options.dataDir == null ? null : boards.length === 1 ? options.dataDir : join(resolve(cwd, options.dataDir), sourceId), dbPath: board.dbPath, env });
      const todo = discoverTool({ explicit: (board.kind === 'loop' ? options.tool : options.todoPy) ?? null, cwd, kind: board.kind, dbPath: board.dbPath });
      const python = todo.path ? discoverPython(options.python ?? null, env) : { command: null, reason: null };
      const pickerReason = todo.reason || python.reason || null;
      const name = boards.length === 1 ? null : `${basename(board.projectRoot)} · ${board.kind === 'loop' ? basename(board.dbPath).replace(/\.[^.]*$/, '') : 'to-do'}`;
      const owned = createMonitor({ dbPath: board.dbPath, dataDir, projectRoot: board.projectRoot, python: python.command ? python : null, todo: todo.path, pickerReason, kind: board.kind, sourceId, name, areas: board.areas });
      pieces.push({ board, sourceId, dataDir, todo, python, pickerReason, monitor: owned });
    }
    monitor = combineMonitors(pieces.map(p => p.monitor));
    const [{ board, dataDir, todo, python, pickerReason }] = pieces;
    // The pinned ws (package.json) maps `import` to its wrapper.mjs, which names both classes.
    const { WebSocketServer, WebSocket } = ws;
    server = createApp(monitor, { WebSocketServer, WebSocket });
    try { await new Promise((ok, fail) => { server.once('error', fail); server.listen(options.port ?? 0, options.host ?? '127.0.0.1', ok); }); }
    catch (error) { throw error.code === 'EADDRINUSE' ? new BoardError(`Port ${options.port} is in use. Choose another --port, or 0 for any free port.`) : error; }
    const port = server.address().port, host = (options.host ?? '127.0.0.1') === '::1' ? '[::1]' : options.host ?? '127.0.0.1';
    const url = `http://${host}:${port}/`;
    // The last step that can fail: when it throws, there is no publisher to close below.
    publisher = publish ? startPublisher({ ...publish, monitor, settings: defaults, WebSocket, log: line => console.log('Publish:    ' + line) }) : null;
    const close = () => new Promise(done => server.close(() => { publisher?.close(); monitor.close(); done(); }));
    return { url, port, server, monitor, board, boards: pieces, dataDir, python, todo, pickerReason, publisher, close, fixedPort: (options.port ?? 0) !== 0 };
  } catch (error) {
    // Every acquired resource gets a cleanup attempt; a secondary error never replaces startup's error.
    // A publisher is never acquired here: starting it is the try's last step that can throw.
    try { server?.close(() => {}); } catch {}
    for (const piece of pieces) { try { piece.monitor.close(); } catch {} }
    throw error;
  }
}

/** The relay to publish through, with its token read from a file; a plain ws:// only for a local relay. */
function readPublish(options, cwd) {
  if (!options.publish) return null;
  let url;
  try { url = new URL(options.publish); } catch { throw new BoardError(`--publish ${options.publish} is not a URL.`); }
  const local = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
  if (url.protocol !== 'wss:' && !(url.protocol === 'ws:' && local)) throw new BoardError('--publish needs a wss:// address (ws:// only for a relay on this machine).');
  if (!options.publishTokenFile) throw new BoardError('--publish needs --publish-token-file <file>: the token is read from a file, never typed on the command line.');
  let token;
  try { token = readFileSync(resolve(cwd, options.publishTokenFile), 'utf8').trim(); } catch { throw new BoardError(`--publish-token-file ${options.publishTokenFile} cannot be read.`); }
  if (!token) throw new BoardError(`--publish-token-file ${options.publishTokenFile} is empty.`);
  return { url: url.href, token };
}

export function describe(started) {
  const s = started.monitor.snapshot();
  const pieces = started.boards ?? [{ board: started.board, sourceId: 'board', dataDir: started.dataDir, todo: started.todo, python: started.python, pickerReason: started.pickerReason }];
  const perBoard = piece => {
    const shown = s.boards.find(b => b.id === piece.sourceId), w = shown?.watchState ?? s.watchState, queue = shown?.queue;
    return [
      `Board:      ${piece.board.dbPath}  (${KINDS[piece.board.kind].label}; ${piece.board.how})`,
      ...(piece.board.areas ? [`Areas:      ${piece.board.areas.join(', ')} (only these are offered next)`] : []),
      `Watching:   ${w.board.active ? w.board.directory + ' for ' + w.board.files.join(', ') : 'NOT ACTIVE: ' + w.board.error}` +
        (piece.todo.path ? `; ${w.tool.active ? 'tool ' + piece.todo.path : 'tool watch NOT ACTIVE: ' + w.tool.error}` : ''),
      `Picker:     ${piece.pickerReason ? 'unavailable · ' + piece.pickerReason : `${basename(piece.todo.path)} ${piece.todo.path} via ${piece.python.command}` + (queue?.state ? ` · ${queue.state}` : '')}`,
      `History:    ${piece.dataDir}`,
    ];
  };
  if (pieces.length > 1) return [
    'Sijav to-do dashboard · read only',
    `URL:        ${started.url}${started.fixedPort ? '' : '  (port chosen by the OS; it changes on restart, pass --port for a stable address)'}`,
    `Project:    ${started.board.projectRoot}`,
    `Read-only:  yes · SQLite read-only/query-only connections · no mutation API · boards never written`,
    `Live push:  WebSocket /api/live on file events · no polling`,
    `Boards:     ${pieces.length}, on one page`,
    ...pieces.flatMap(perBoard),
    ...(started.publisher ? [`Publish:    ${started.publisher.url} · ${started.publisher.status().state}`] : []),
    'No task loop is started. Ctrl+C stops the dashboard.',
  ].join('\n');
  const queue = s.boards[0]?.queue;
  return [
    'Sijav to-do dashboard · read only',
    `URL:        ${started.url}${started.fixedPort ? '' : '  (port chosen by the OS; it changes on restart, pass --port for a stable address)'}`,
    `Project:    ${started.board.projectRoot}`,
    `Board:      ${started.board.dbPath}  (${KINDS[started.board.kind].label}; ${started.board.how})`,
    ...(started.board.areas ? [`Areas:      ${started.board.areas.join(', ')} (only these are offered next)`] : []),
    `Read-only:  yes · SQLite read-only/query-only connections · no mutation API · board never written`,
    `Watching:   ${s.watchState.board.active ? s.watchState.board.directory + ' for ' + s.watchState.board.files.join(', ') : 'NOT ACTIVE: ' + s.watchState.board.error}` +
      (started.todo.path ? `; ${s.watchState.tool.active ? 'tool ' + started.todo.path : 'tool watch NOT ACTIVE: ' + s.watchState.tool.error}` : ''),
    `Live push:  WebSocket /api/live on file events · no polling`,
    `Picker:     ${started.pickerReason ? 'unavailable · ' + started.pickerReason : `${basename(started.todo.path)} ${started.todo.path} via ${started.python.command}` + (queue?.state ? ` · ${queue.state}` : '')}`,
    `History:    ${started.dataDir}`,
    ...(started.publisher ? [`Publish:    ${started.publisher.url} · ${started.publisher.status().state}`] : []),
    'No task loop is started. Ctrl+C stops the dashboard.',
  ].join('\n');
}

if (isEntry(process.argv[1])) {
  if (!nodeSupported()) { console.error(`Node ${process.versions.node} is not supported: the dashboard needs Node 22.16+ (22.x) or 24+.`); process.exit(2); }
  let options;
  try { options = parseArgs(process.argv.slice(2)); }
  catch (error) { console.error(error.message); process.exit(2); }
  if (options.help) { console.log(USAGE); process.exit(0); }
  startDashboard(options, { cwd: callerDirectory() }).then(started => {
    console.log(describe(started));
    // Ctrl+C, a stop signal, or a parent that started it with an IPC channel letting go of it.
    // On Windows a signal sent by another process is a forced kill, so the channel is how a
    // parent there asks for an ordinary exit. However many arrive, it shuts down once.
    let stopping = null;
    const shutdown = () => (stopping ??= started.close().then(() => process.exit(0)));
    process.on('SIGINT', shutdown); process.on('SIGTERM', shutdown); process.on('disconnect', shutdown);
  }, error => {
    console.error(error instanceof BoardError ? error.message : 'The dashboard could not start: ' + error.message);
    process.exit(error instanceof BoardError ? 2 : 1);
  });
}
