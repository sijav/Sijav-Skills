#!/usr/bin/env node
// Read-only live dashboard for a project's .claude/todo.db, or a loop board named with --db.
//   node <skill>/dashboard/server.mjs [--project <dir>] [--db <file>] [--port <n>] [--data-dir <dir>]
//                                     [--python <exe>] [--todo-py <file>] [--tool <file>] [--host 127.0.0.1]
import { createServer } from 'node:http';
import { readFileSync, realpathSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { join, resolve, dirname, basename } from 'node:path';
import { ROOT, KINDS, defaults, encode, BoardError, resolveBoard, resolveDataDir, discoverPython, discoverTool, createMonitor, combineMonitors } from './lib/board.mjs';

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
  --help             Show this text.`;

export function parseArgs(argv) {
  const options = { project: null, db: null, port: 0, host: '127.0.0.1', dataDir: null, python: null, todoPy: null, tool: null, help: false };
  const names = { '--project': 'project', '--db': 'db', '--port': 'port', '--host': 'host', '--data-dir': 'dataDir', '--python': 'python', '--todo-py': 'todoPy', '--tool': 'tool' };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === '--help' || arg === '-h') { options.help = true; continue; }
    const [flag, inline] = arg.includes('=') ? [arg.slice(0, arg.indexOf('=')), arg.slice(arg.indexOf('=') + 1)] : [arg, null];
    if (!names[flag]) throw new BoardError(`Unknown option ${arg}.\n\n${USAGE}`);
    const value = inline ?? argv[++i];
    if (value == null || value === '') throw new BoardError(`${flag} needs a value.`);
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
 * node:sqlite's `timeout` (busy wait) option arrived in v22.16.0 and v24.0.0 (not in any 23.x),
 * URL paths in v22.15.0 / v23.10.0 (Node API docs, sqlite "History"). Older versions would
 * silently ignore the busy timeout, so they are refused rather than run with weaker reads.
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
    const url = new URL(req.url, 'http://' + req.headers.host);
    const json = value => { res.setHeader('Content-Type', 'application/json; charset=utf-8'); res.end(req.method === 'HEAD' ? '' : encode(value)); };
    try {
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
          const embedded = encode({ ui: settings.ui, transport: settings.transport, workClassification: {}, project: s.server.project, db: s.server.db }).replaceAll('<', '\\u003c');
          // A function replacement: `$&`, `$'` and `$$` in a path are inserted literally.
          body = body.toString().replace('__DASHBOARD_SETTINGS__', () => embedded);
        }
        res.end(req.method === 'HEAD' ? '' : body); return;
      }
      res.writeHead(404); res.end('Not found.');
    } catch (error) { if (!res.headersSent) res.writeHead(500); res.end('Dashboard error: ' + error.message); }
  });
  const wss = new WebSocketServer({ noServer: true, clientTracking: true, maxPayload: settings.transport.maxMessageBytes });
  const sentUpTo = new WeakMap();
  // A (re)connecting client gets the snapshot and the newest page of history.
  // After that each push carries only the history entries it has not seen.
  const send = (socket, snapshot, first) => {
    if (socket.readyState !== WebSocket.OPEN) return;
    if (socket.bufferedAmount > settings.transport.maxBufferedBytes) { socket.terminate(); return; }
    let changes, complete = null;
    if (first) { changes = monitor.changes({ limit: page }); complete = changes.length >= snapshot.changeCount; }
    else changes = monitor.changes({ after: sentUpTo.get(socket) ?? 0 });
    sentUpTo.set(socket, snapshot.latestSeq);
    socket.send(encode({ type: 'snapshot', reset: first, snapshot, changes, changesComplete: complete }));
  };
  server.on('upgrade', (req, socket, head) => {
    const site = req.headers['sec-fetch-site'];
    if (req.method !== 'GET' || !hosts.has(req.headers.host) || req.headers.origin !== 'http://' + req.headers.host || (site && !['same-origin', 'none'].includes(site)) || new URL(req.url, 'http://' + req.headers.host).pathname !== '/api/live') {
      socket.end('HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n'); return;
    }
    wss.handleUpgrade(req, socket, head, ws => wss.emit('connection', ws, req));
  });
  wss.on('connection', socket => { socket.on('error', () => {}); socket.on('message', () => socket.close(1008, 'Read-only stream')); send(socket, monitor.snapshot(), true); });
  const unsubscribe = monitor.subscribe(snapshot => { for (const socket of wss.clients) send(socket, snapshot, false); });
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
  for (const db of asked) {
    const board = resolveBoard({ cwd, project: options.project ?? null, db });
    if (!boards.some(b => samePath(b.dbPath, board.dbPath))) boards.push(board);
  }
  const loops = boards.filter(b => b.kind === 'loop').length;
  if (boards.length > 1 && loops > 1 && options.tool) throw new BoardError('--tool names one loop board\'s tool, but several loop boards were given; each uses the .py named after its board file.');
  const ws = await loadWs();
  // One monitor per board: its own id, history folder, tool and picker. A board shown alone keeps the id `board`.
  const pieces = boards.map((board, index) => {
    const sourceId = index === 0 ? 'board' : `board-${index + 1}`;
    const dataDir = resolveDataDir({ cwd, dataDir: options.dataDir == null ? null : boards.length === 1 ? options.dataDir : join(resolve(cwd, options.dataDir), sourceId), dbPath: board.dbPath, env });
    const todo = discoverTool({ explicit: (board.kind === 'loop' ? options.tool : options.todoPy) ?? null, cwd, kind: board.kind, dbPath: board.dbPath });
    const python = todo.path ? discoverPython(options.python ?? null, env) : { command: null, reason: null };
    const pickerReason = todo.reason || python.reason || null;
    const name = boards.length === 1 ? null : `${basename(board.projectRoot)} · ${board.kind === 'loop' ? basename(board.dbPath).replace(/\.[^.]*$/, '') : 'to-do'}`;
    const monitor = createMonitor({ dbPath: board.dbPath, dataDir, projectRoot: board.projectRoot, python: python.command ? python : null, todo: todo.path, pickerReason, kind: board.kind, sourceId, name });
    return { board, sourceId, dataDir, todo, python, pickerReason, monitor };
  });
  const monitor = combineMonitors(pieces.map(p => p.monitor));
  const [{ board, dataDir, todo, python, pickerReason }] = pieces;
  const server = createApp(monitor, ws.default ? { WebSocketServer: ws.WebSocketServer ?? ws.default.WebSocketServer, WebSocket: ws.WebSocket ?? ws.default } : ws);
  try { await new Promise((ok, fail) => { server.once('error', fail); server.listen(options.port ?? 0, options.host ?? '127.0.0.1', ok); }); }
  catch (error) { monitor.close(); throw error.code === 'EADDRINUSE' ? new BoardError(`Port ${options.port} is in use. Choose another --port, or 0 for any free port.`) : error; }
  const port = server.address().port, host = (options.host ?? '127.0.0.1') === '::1' ? '[::1]' : options.host ?? '127.0.0.1';
  const url = `http://${host}:${port}/`;
  const close = () => new Promise(done => server.close(() => { monitor.close(); done(); }));
  return { url, port, server, monitor, board, boards: pieces, dataDir, python, todo, pickerReason, close, fixedPort: (options.port ?? 0) !== 0 };
}

export function describe(started) {
  const s = started.monitor.snapshot();
  const pieces = started.boards ?? [{ board: started.board, sourceId: 'board', dataDir: started.dataDir, todo: started.todo, python: started.python, pickerReason: started.pickerReason }];
  const perBoard = piece => {
    const shown = s.boards.find(b => b.id === piece.sourceId), w = shown?.watchState ?? s.watchState, queue = shown?.queue;
    return [
      `Board:      ${piece.board.dbPath}  (${KINDS[piece.board.kind].label}; ${piece.board.how})`,
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
    'No task loop is started. Ctrl+C stops the dashboard.',
  ].join('\n');
  const queue = s.boards[0]?.queue;
  return [
    'Sijav to-do dashboard · read only',
    `URL:        ${started.url}${started.fixedPort ? '' : '  (port chosen by the OS; it changes on restart, pass --port for a stable address)'}`,
    `Project:    ${started.board.projectRoot}`,
    `Board:      ${started.board.dbPath}  (${KINDS[started.board.kind].label}; ${started.board.how})`,
    `Read-only:  yes · SQLite read-only/query-only connections · no mutation API · board never written`,
    `Watching:   ${s.watchState.board.active ? s.watchState.board.directory + ' for ' + s.watchState.board.files.join(', ') : 'NOT ACTIVE: ' + s.watchState.board.error}` +
      (started.todo.path ? `; ${s.watchState.tool.active ? 'tool ' + started.todo.path : 'tool watch NOT ACTIVE: ' + s.watchState.tool.error}` : ''),
    `Live push:  WebSocket /api/live on file events · no polling`,
    `Picker:     ${started.pickerReason ? 'unavailable · ' + started.pickerReason : `${basename(started.todo.path)} ${started.todo.path} via ${started.python.command}` + (queue?.state ? ` · ${queue.state}` : '')}`,
    `History:    ${started.dataDir}`,
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
    const shutdown = () => started.close().then(() => process.exit(0));
    process.on('SIGINT', shutdown); process.on('SIGTERM', shutdown);
  }, error => {
    console.error(error instanceof BoardError ? error.message : 'The dashboard could not start: ' + error.message);
    process.exit(error instanceof BoardError ? 2 : 1);
  });
}
