import { DatabaseSync } from 'node:sqlite';
import { readFileSync, writeFileSync, appendFileSync, mkdirSync, existsSync, statSync, renameSync, realpathSync, watch, openSync, readSync, closeSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, basename, join, resolve, relative, isAbsolute } from 'node:path';
import { homedir, tmpdir } from 'node:os';
import { createHash, randomUUID } from 'node:crypto';
import { execFile, execFileSync } from 'node:child_process';

// The dashboard folder: <skill>/dashboard. Its parent holds the unchanged todo.py.
export const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
export const SKILL_DIR = dirname(ROOT);
export const defaults = JSON.parse(readFileSync(join(ROOT, 'defaults.json'), 'utf8'));
export const encode = value => JSON.stringify(value, (_, v) => typeof v === 'bigint' ? v.toString() : v);
export const digest = value => createHash('sha256').update(encode(value)).digest('hex');
const quote = name => '"' + name.replaceAll('"', '""') + '"';
const caseFold = process.platform === 'win32' || process.platform === 'darwin';
const sameName = (a, b) => caseFold ? a.toLowerCase() === b.toLowerCase() : a === b;

export class BoardError extends Error {}

export function timestamp(value) {
  if (value == null || value === '') return null;
  if (typeof value === 'number') return new Date(value < 1e12 ? value * 1000 : value).toISOString();
  const text = String(value);
  const date = new Date(/(?:Z|[+-]\d\d:\d\d)$/.test(text) ? text : text.replace(' ', 'T') + 'Z');
  return Number.isNaN(+date) ? null : date.toISOString();
}
function fileInfo(path) {
  try { const s = statSync(path); return { bytes: s.size, modifiedAt: s.mtime.toISOString() }; }
  catch { return null; }
}
const realOr = path => { try { return realpathSync.native(path); } catch { return resolve(path); } };

// ---------------------------------------------------------------- resolution

/** The same walk as todo.py's find_board(): the nearest EXISTING .claude/todo.db. */
export function findBoard(start) {
  let directory = resolve(start);
  for (;;) {
    const candidate = join(directory, '.claude', 'todo.db');
    if (existsSync(candidate)) return candidate;
    const parent = dirname(directory);
    if (parent === directory) return null;
    directory = parent;
  }
}

/** Never creates anything. --db is used as given; otherwise walk up from --project or the caller's directory. */
export function resolveBoard({ cwd = process.cwd(), project = null, db = null } = {}) {
  const start = resolve(cwd, project ?? '.');
  if (project != null && !(existsSync(start) && statSync(start).isDirectory())) {
    throw new BoardError(`--project ${start} is not an existing directory.`);
  }
  let path, how;
  if (db != null) {
    path = resolve(cwd, db); how = '--db';
    if (!existsSync(path)) throw new BoardError(`No board at ${path}. --db never creates a file; a board is created only by the todo tool's explicit init.`);
  } else {
    path = findBoard(start); how = 'nearest .claude/todo.db at or above ' + start;
    if (!path) throw new BoardError(`No board. Nothing at or above ${start} has .claude/todo.db. Nothing was created. If this project should have one, use the todo tool's explicit init.`);
  }
  if (!statSync(path).isFile()) throw new BoardError(`${path} is not a file, so it cannot be a board.`);
  const boardDir = dirname(path);
  const projectRoot = sameName(basename(boardDir), '.claude') ? dirname(boardDir) : start;
  return { dbPath: path, projectRoot, start, how };
}

const inside = (child, parent) => { const r = relative(parent, child); return r === '' || (!!r && !r.startsWith('..') && !isAbsolute(r)); };

/** The per-user cache root for change history: never the project, never the installed skill. */
export function userDataRoot(env = process.env) {
  if (env.SIJAV_TODO_DASHBOARD_HOME) return resolve(env.SIJAV_TODO_DASHBOARD_HOME);
  const base = process.platform === 'win32' ? env.LOCALAPPDATA || join(homedir(), 'AppData', 'Local')
    : process.platform === 'darwin' ? join(homedir(), 'Library', 'Caches')
    : env.XDG_CACHE_HOME || join(homedir(), '.cache');
  return join(base, 'sijav-todo-dashboard');
}
/** A stable key for one board file: its real path, case-folded where the file system is. */
export function boardKey(dbPath) {
  const real = realOr(dbPath);
  const hash = createHash('sha256').update(caseFold ? real.toLowerCase() : real).digest('hex').slice(0, 16);
  const project = basename(dirname(dirname(real))).replace(/[^\p{L}\p{N}._-]+/gu, '-').slice(0, 40) || 'board';
  return `${project}-${hash}`;
}
/** Explicit --data-dir as given; otherwise a per-user cache folder keyed by the board's identity. */
export function resolveDataDir({ cwd = process.cwd(), dataDir = null, dbPath, env = process.env }) {
  const path = dataDir != null ? resolve(cwd, dataDir) : join(userDataRoot(env), boardKey(dbPath));
  if (inside(realOr(path), realOr(SKILL_DIR)) || inside(path, SKILL_DIR)) throw new BoardError(`The data directory ${path} is inside the installed skill (${SKILL_DIR}). Choose another --data-dir.`);
  return path;
}

/** Python 3.9+ for the picker: --python, then SIJAV_TODO_PYTHON, then PATH names. Nothing machine-specific is shipped. */
export function discoverPython(explicit = null, env = process.env) {
  const candidates = explicit ? [[explicit]] : env.SIJAV_TODO_PYTHON ? [[env.SIJAV_TODO_PYTHON]]
    : process.platform === 'win32' ? [['python'], ['py', '-3'], ['python3']] : [['python3'], ['python']];
  const tried = [];
  for (const [command, ...args] of candidates) {
    try {
      const out = execFileSync(command, [...args, '-c', 'import sys;print(sys.executable);sys.exit(0 if sys.version_info>=(3,9) else 3)'],
        { encoding: 'utf8', timeout: 20000, windowsHide: true, stdio: ['ignore', 'pipe', 'ignore'] }).trim();
      if (out) return { command: out, args: [], found: [command, ...args].join(' '), tried };
    } catch (error) { tried.push([command, ...args].join(' ') + (error.status === 3 ? ' (older than 3.9)' : '')); }
  }
  return { command: null, tried, reason: explicit ? `The configured Python (${explicit}) is not a runnable Python 3.9+.` : `No Python 3.9+ was found (tried ${tried.join(', ')}). Pass --python <path> or set SIJAV_TODO_PYTHON.` };
}

/** The skill's todo.py: --todo-py, else the parent of this dashboard folder (<skill>/todo.py). */
export function discoverTodo(explicit = null, cwd = process.cwd()) {
  const path = explicit ? resolve(cwd, explicit) : join(SKILL_DIR, 'todo.py');
  if (existsSync(path) && statSync(path).isFile()) return { path, reason: null };
  return { path: null, expected: path, reason: explicit ? `--todo-py ${path} does not exist.` : `todo.py was not found beside the dashboard at ${path}. Pass --todo-py <path to the skill's todo.py>.` };
}

// ---------------------------------------------------------------- reading

const SUFFIXES = ['', '-wal', '-shm', '-journal'];
/** Size, mtime (ns) and identity of the board and its SQLite sidecars. */
export function fileSignature(path) {
  return encode(SUFFIXES.map(suffix => {
    try { const s = statSync(path + suffix, { bigint: true }); return [suffix, s.size, s.mtimeNs, s.ino]; }
    catch { return [suffix, null]; }
  }));
}
function header(path) {
  const bytes = Buffer.alloc(20), fd = openSync(path, 'r');
  try { return readSync(fd, bytes, 0, 20, 0) < 20 ? null : bytes; } finally { closeSync(fd); }
}
function isIdleWal(path) {
  const bytes = header(path);
  return !!bytes && bytes[18] === 2 && !existsSync(path + '-wal') && !existsSync(path + '-shm');
}
function isWalHeader(path) { try { return header(path)?.[18] === 2; } catch { return false; } }
function plain(value) {
  if (typeof value === 'bigint') return value >= BigInt(-Number.MAX_SAFE_INTEGER) && value <= BigInt(Number.MAX_SAFE_INTEGER) ? Number(value) : value.toString();
  if (value instanceof Uint8Array) return { $blob: Buffer.from(value).toString('hex') };
  if (typeof value === 'number' && !Number.isFinite(value)) return { $float: Number.isNaN(value) ? 'nan' : value > 0 ? 'inf' : '-inf' };
  return value;
}
const sortedTables = tables => Object.fromEntries(Object.keys(tables).sort().map(name => [name, tables[name]]));
/** The digest the Python picker's snapshot is checked against: column values in column order. */
export const rowsDigest = rowsByTable => digest(Object.entries(sortedTables(rowsByTable)));

/**
 * One consistent read of every table, read-only and query-only. An idle WAL
 * board (no -wal, no -shm) is opened immutable so that reading it creates no
 * sidecar files; that read is discarded if the file changed meanwhile.
 */
export function readTables(path, { busyMs = 800 } = {}) {
  if (!existsSync(path)) throw new Error(`The board file ${path} is missing. Nothing was created; waiting for it to return.`);
  for (let attempt = 0; attempt < 5; attempt++) {
    const before = fileSignature(path), idle = isIdleWal(path);
    let target = path;
    if (idle) { target = pathToFileURL(path); target.searchParams.set('immutable', '1'); }
    const db = new DatabaseSync(target, { readOnly: true, timeout: busyMs });
    let out;
    try {
      db.exec('PRAGMA query_only=ON');
      db.exec('BEGIN');
      const schema = db.prepare("SELECT name, sql FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").all().map(r => ({ name: r.name, sql: r.sql }));
      const indexes = db.prepare("SELECT name, tbl_name, sql FROM sqlite_schema WHERE type IN ('index','trigger','view') AND name NOT LIKE 'sqlite_%' ORDER BY name").all().map(r => ({ ...r }));
      const tables = {}, primaryKeys = {}, tableColumns = {}, rowArrays = {};
      for (const table of schema) {
        const columns = db.prepare(`PRAGMA table_info(${quote(table.name)})`).all();
        tableColumns[table.name] = columns.map(c => c.name);
        primaryKeys[table.name] = columns.filter(c => c.pk).sort((a, b) => a.pk - b.pk).map(c => c.name);
        const keys = primaryKeys[table.name];
        let statement;
        try { statement = db.prepare(`SELECT * FROM ${quote(table.name)} ORDER BY ${keys.length ? keys.map(quote).join(',') : 'rowid'}`); }
        catch { statement = db.prepare(`SELECT * FROM ${quote(table.name)}`); }
        statement.setReadBigInts(true);
        tables[table.name] = statement.all().map(row => Object.fromEntries(Object.entries(row).map(([k, v]) => [k, plain(v)])));
        rowArrays[table.name] = tables[table.name].map(row => tableColumns[table.name].map(c => row[c]));
      }
      db.exec('COMMIT');
      out = { schema, indexes, tables, primaryKeys, tableColumns, rowsDigest: rowsDigest(rowArrays) };
    } finally { db.close(); }
    if (!idle || fileSignature(path) === before) return out;
  }
  throw new Error('The board kept changing while it was read. It is read again on its next change.');
}

/** The values a column's CHECK (col IN (...)) constraint allows, from the stored schema. */
export function checkedValues(sql, field) {
  const match = sql?.match(new RegExp('\\b' + field + '\\b[^,]*?CHECK\\s*\\(\\s*' + field + '\\s+IN\\s*\\(([^)]*)\\)', 'i'));
  return match ? [...match[1].matchAll(/'((?:[^']|'')*)'/g)].map(m => m[1].replaceAll("''", "'")) : [];
}

/**
 * Every table and row of the board, with each task's related rows attached by
 * their `task` column. Status meaning (doing, done, ...) is NOT decided here:
 * it comes from the tool's own policy, applied by classify().
 */
export function readBoard(path, options) {
  const data = readTables(path, options);
  const { tables, tableColumns } = data;
  const rows = tables.task || [];
  const related = {};
  for (const [name, columns] of Object.entries(tableColumns)) {
    if (name === 'task' || !columns.includes('task')) continue;
    for (const row of tables[name]) ((related[String(row.task)] ??= {})[name] ??= []).push(row);
  }
  const dependents = new Map(), children = new Map();
  for (const row of tables.blocked_by || []) {
    if (!dependents.has(String(row.parent))) dependents.set(String(row.parent), []);
    dependents.get(String(row.parent)).push(String(row.task));
  }
  for (const row of rows) if (row.parent_task != null && row.parent_task !== '') {
    if (!children.has(String(row.parent_task))) children.set(String(row.parent_task), []);
    children.get(String(row.parent_task)).push(String(row.id));
  }
  const tasks = rows.map(raw => {
    const id = String(raw.id), own = related[id] || {};
    const notes = own.note || [], roasts = own.roast || [], blocked = own.blocked || [];
    const createdAt = timestamp(raw.created ?? raw.created_at), closedAt = timestamp(raw.closed ?? raw.closed_at), updatedAt = timestamp(raw.updated ?? raw.updated_at);
    const times = [createdAt, closedAt, updatedAt, ...[...notes, ...roasts].map(r => timestamp(r.at)), ...blocked.map(r => timestamp(r.since))].filter(Boolean).sort();
    return {
      key: 'board:' + id, sourceId: 'board', id, title: raw.title, status: raw.status, severity: raw.severity ?? null,
      priority: raw.priority ?? null, points: raw.points ?? null, description: raw.descr ?? raw.story ?? null, why: raw.why ?? null,
      exit: raw.exit_cond ?? null, area: raw.area ?? null, phase: raw.phase ?? null, parentTask: raw.parent_task ?? null,
      createdAt, closedAt, updatedAt, lastRecordedAt: times.at(-1) || null,
      dependencies: (own.blocked_by || []).map(d => String(d.parent)), dependents: dependents.get(id) || [], children: children.get(id) || [],
      notes, roasts, blocked, related: Object.fromEntries(Object.entries(own).filter(([name]) => !['blocked_by', 'note', 'roast', 'blocked'].includes(name))),
      raw, isFinding: raw.parent_task != null && raw.parent_task !== '',
    };
  });
  const statuses = {}; for (const t of tasks) statuses[t.status] = (statuses[t.status] || 0) + 1;
  const taskSql = data.schema.find(s => s.name === 'task')?.sql;
  const allTimes = tasks.map(t => t.lastRecordedAt).filter(Boolean).sort();
  return {
    available: true, stale: false, error: null, lastSuccessfulReadAt: new Date().toISOString(), ...data, taskTable: tables.task ? 'task' : null,
    tasks, statuses, checkedStatuses: checkedValues(taskSql, 'status'), checkedSeverities: checkedValues(taskSql, 'severity'),
    latestRecordedAt: allTimes.at(-1) || null,
    file: fileInfo(path), wal: fileInfo(path + '-wal'), journalMode: existsSync(path) ? (isWalHeader(path) ? 'wal' : 'rollback') : null,
    hash: digest({ schema: data.schema, indexes: data.indexes, tables }),
  };
}

/** The tool's status policy as display rules; empty and explicitly unknown when there is no current policy. */
export function rulesFrom(policy, reason = null) {
  if (!policy) return { known: false, reason: reason || 'todo.py has not been read yet.', severities: [], openStatuses: [], doingStatuses: [],
    finishedStatuses: [], discardedStatuses: [], otherStatuses: [], satisfying: [], closed: [], statuses: [], rankLabels: null, docs: {} };
  const g = policy.groups;
  return { known: true, reason: null, severities: policy.severities || [], openStatuses: g.open, doingStatuses: g.doing, finishedStatuses: g.finished,
    discardedStatuses: g.discarded, otherStatuses: [...g.other, ...g.unknown], unknownStatuses: g.unknown, satisfying: policy.satisfying, closed: policy.closed,
    statuses: policy.statuses, rankLabels: policy.rankLabels, docs: policy.docs || {}, probes: policy.probes };
}

/** Apply the tool's policy to the stored rows: groups, waiting parents, open findings and counts. */
export function classify(board, rules, queue = null) {
  const byId = new Map(board.tasks.map(t => [t.id, t]));
  const has = (list, status) => rules.known && list.includes(status);
  const ready = queue?.state === 'ready';
  const tasks = board.tasks.map(t => {
    const isComplete = has(rules.closed, t.status);
    const unresolved = rules.known ? t.dependencies.filter(p => !byId.has(p) || !rules.satisfying.includes(byId.get(p).status)) : [];
    const openChildren = rules.known ? t.children.filter(k => !has(rules.closed, byId.get(k)?.status)) : t.children;
    const reasons = ready ? queue.deferred?.[t.id] || [] : null;
    const isOpen = has(rules.openStatuses, t.status), isDoing = has(rules.doingStatuses, t.status);
    const isWaiting = ready ? reasons.some(r => r.kind === 'parent') && !isDoing : isOpen && unresolved.length > 0;
    return { ...t, unresolved, openChildren, openFindings: openChildren.length, isOpen, isDoing, isComplete,
      isFinished: has(rules.finishedStatuses, t.status), isDiscarded: has(rules.discardedStatuses, t.status),
      isExplicitlyBlocked: !isComplete && t.blocked.length > 0, isWaiting, isBlocked: (!isComplete && t.blocked.length > 0) || isWaiting };
  });
  const count = fn => rules.known ? tasks.filter(fn).length : null;
  return { ...board, tasks,
    supportedStatuses: [...new Set([...rules.statuses, ...(board.checkedStatuses || []), ...Object.keys(board.statuses || {})])],
    severities: [...new Set([...rules.severities, ...(board.checkedSeverities || []), ...tasks.map(t => t.severity).filter(Boolean)])],
    counts: { total: tasks.length, open: count(t => t.isOpen), doing: count(t => t.isDoing), finished: count(t => t.isFinished),
      discarded: count(t => t.isDiscarded), complete: count(t => t.isComplete), unfinished: count(t => !t.isComplete),
      blocked: count(t => t.isExplicitlyBlocked), waiting: count(t => t.isWaiting),
      openFindings: count(t => t.isFinding && !t.isComplete), findings: tasks.filter(t => t.isFinding).length,
      notes: (board.tables?.note || []).length, reviews: (board.tables?.roast || []).length } };
}

// ---------------------------------------------------------------- changes

function rowMap(rows, keys) {
  const map = new Map();
  for (const row of rows || []) {
    const base = keys?.length ? encode(keys.map(k => row[k])) : digest(row);
    let key = base, occurrence = 1; while (map.has(key)) key = base + ':' + (++occurrence);
    map.set(key, row);
  }
  return map;
}
/** Row-level changes between two successful reads, for every table and the schema. */
export function diffBoard(before, after, at = new Date().toISOString()) {
  if (!before || before.hash === after.hash) return [];
  const changes = [];
  for (const table of new Set([...Object.keys(before.tables), ...Object.keys(after.tables)])) {
    const keys = after.primaryKeys[table] || before.primaryKeys?.[table];
    const old = rowMap(before.tables[table], keys), now = rowMap(after.tables[table], keys);
    for (const key of new Set([...old.keys(), ...now.keys()])) {
      const a = old.get(key) || null, b = now.get(key) || null;
      if (encode(a) === encode(b)) continue;
      const row = b || a;
      const item = table === 'task' ? row.id : row.task ?? null;
      const task = after.tasks.find(t => t.id === String(item)) || before.tasks?.find(t => t.id === String(item));
      const fields = a && b ? Object.keys({ ...a, ...b }).filter(k => encode(a[k]) !== encode(b[k])) : Object.keys(row);
      changes.push({ id: randomUUID(), at, sourceId: 'board', sourceName: after.name, table, kind: a ? (b ? 'updated' : 'removed') : 'added',
        itemId: item == null ? null : String(item), title: task?.title || row.title || row.name || table, fields, before: a, after: b });
    }
  }
  const schemaOf = board => ({ tables: board.schema, other: board.indexes ?? [] });
  if (encode(schemaOf(before)) !== encode(schemaOf(after))) {
    changes.push({ id: randomUUID(), at, sourceId: 'board', sourceName: after.name, table: 'schema', kind: 'updated', itemId: null, title: 'Database schema',
      fields: ['schema'], before: Object.fromEntries([...before.schema, ...(before.indexes ?? [])].map(s => [s.name, s.sql])),
      after: Object.fromEntries([...after.schema, ...(after.indexes ?? [])].map(s => [s.name, s.sql])) });
  }
  return changes;
}

/** Read a JSONL journal, keeping every valid entry and reporting the lines that could not be read. */
export function loadJournal(path) {
  if (!existsSync(path)) return { entries: [], bad: [], endsClean: true };
  const text = readFileSync(path, 'utf8'), entries = [], bad = [];
  text.split('\n').forEach((line, index) => {
    if (!line.trim()) return;
    try { const entry = JSON.parse(line); if (entry && typeof entry === 'object' && typeof entry.at === 'string') entries.push(entry); else bad.push(index + 1); }
    catch { bad.push(index + 1); }
  });
  entries.forEach((entry, index) => { entry.seq ??= index + 1; });
  return { entries, bad, endsClean: text === '' || text.endsWith('\n') };
}
const transitionOf = c => c.table === 'task' && c.itemId != null && c.before && c.after && c.before.status !== c.after.status
  ? { seq: c.seq, id: c.id, at: c.at, observedAt: c.at, sourceId: c.sourceId, itemId: c.itemId, taskKey: 'board:' + c.itemId, title: c.title, fromStatus: c.before.status, toStatus: c.after.status } : null;

// ---------------------------------------------------------------- picker

/** Run picker.py once from a neutral directory. Resolves with its JSON or rejects with its structured error. */
export function runPicker({ python, todo, dbPath, settings = defaults.picker }) {
  return new Promise((resolveResult, reject) => {
    const child = execFile(python.command, [...python.args, '-B', join(ROOT, 'picker.py'), '--db', dbPath, '--todo', todo], {
      cwd: tmpdir(), shell: false, windowsHide: true, timeout: settings.timeoutMs, maxBuffer: settings.maxBufferBytes,
      env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1', PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
    }, (error, stdout) => {
      let result = null;
      try { result = JSON.parse(stdout); } catch {}
      if (error || !result || typeof result.error === 'string') {
        reject(new Error(typeof result?.error === 'string' ? result.error : error?.killed ? 'The picker timed out or was stopped.' : `The picker failed${error?.code != null ? ' (' + error.code + ')' : ''} without a readable result.`));
        return;
      }
      if (!Array.isArray(result.rankedIds) || !Array.isArray(result.startableIds) || typeof result.tables !== 'object' || !result.policy) { reject(new Error('The picker returned an incomplete result.')); return; }
      resolveResult(result);
    });
    child.stdin?.end();
  });
}
const fileSha = path => { try { return createHash('sha256').update(readFileSync(path)).digest('hex'); } catch { return null; } };

// ---------------------------------------------------------------- monitor

export function createMonitor({ dbPath, dataDir, projectRoot, python = null, todo = null, pickerReason = null, settings = defaults, name = null }) {
  mkdirSync(dataDir, { recursive: true });
  const statePath = join(dataDir, 'baseline.json'), journalPath = join(dataDir, 'changes.jsonl');
  const boardName = name || basename(projectRoot) || dbPath;
  let baseline = { startedAt: new Date().toISOString(), dbPath, board: null }, persistenceError = null, journalWarning = null;
  try { if (existsSync(statePath)) baseline = JSON.parse(readFileSync(statePath, 'utf8')); } catch (e) { persistenceError = 'Previous baseline could not be read; history continues from now: ' + e.message; }
  const loaded = loadJournal(journalPath), journal = loaded.entries;
  let needsNewline = !loaded.endsClean;
  if (loaded.bad.length) journalWarning = `${loaded.bad.length} unreadable change-history line(s) were skipped (line ${loaded.bad.slice(0, 10).join(', ')}${loaded.bad.length > 10 ? ', …' : ''}); every other entry is kept.`;
  const transitions = journal.map(transitionOf).filter(Boolean);
  let nextSeq = (journal.at(-1)?.seq ?? 0) + 1;
  const source = { id: 'board', name: boardName, short: boardName.replace(/[^A-Za-z0-9]/g, '').slice(0, 2).toUpperCase() || 'TD', kind: 'project', path: dbPath, projectRoot,
    note: 'Read-only view of this project\'s to-do board. The board is never written; status meaning, order and reasons come from the tool\'s own code.' };
  const picker = { configured: !!(python?.command && todo), python: python?.command ?? null, todo, reason: pickerReason };
  const instanceId = randomUUID(), listeners = new Set(), watchers = [];
  const watchState = { board: { active: false, error: null, directory: dirname(dbPath), files: SUFFIXES.map(s => basename(dbPath) + s) }, tool: { active: false, error: null, file: todo } };
  let lastReadable = null, board = null, revision = 0, checkedAt = null, readCount = 0, ignoredEvents = 0, lastFileEventAt = null;
  let lastSignature = null, debounce = null, forced = false, closed = false, pendingNotify = false, toolSha = todo ? fileSha(todo) : null;
  const queue = { state: picker.configured ? 'checking' : 'unconfigured', result: null, previous: null, requested: 0, processed: 0, promise: null, error: picker.configured ? null : pickerReason };

  const policyReason = () => !picker.configured ? `todo.py's policy is unavailable: ${pickerReason}`
    : !queue.result ? (queue.state === 'checking' ? 'Reading todo.py’s policy.' : `todo.py's policy could not be read: ${queue.error}`)
    : queue.result.tool.sha256 !== toolSha ? (queue.state === 'error' ? `todo.py changed and could not be read: ${queue.error}` : 'todo.py changed; its policy is being read again.') : null;
  const currentPolicy = () => policyReason() == null ? queue.result.policy : null;
  const publicQueue = () => {
    const r = queue.result, out = { state: queue.state, error: queue.error, stale: queue.state !== 'ready' && !!(r || queue.previous) };
    if (queue.state === 'ready' && r) return { ...out, checkedAt: r.checkedAt, headId: r.headId, headKind: r.headKind, startedIds: r.startedIds, eligibleIds: r.eligibleIds,
      startableIds: r.startableIds, rankedIds: r.rankedIds, deferred: r.deferred, rankKeys: r.rankKeys, rankErrors: r.rankErrors, children: r.children,
      nextText: r.nextText, nextExitCode: r.nextExitCode, currentPhase: r.currentPhase, phases: r.phases, boardHadTaskTable: r.boardHadTaskTable,
      schemaAddedOnCopy: r.schemaAddedOnCopy, tool: r.tool };
    // While the board is re-checked, the previous order may be shown, labelled; never its pick.
    if (queue.state === 'checking' && queue.previous) return { ...out, previous: queue.previous };
    return out;
  };
  const snapshot = () => {
    const latest = new Map();
    const rules = rulesFrom(currentPolicy(), policyReason());
    const shownQueue = publicQueue();
    const classified = board ? classify(board, rules, shownQueue) : null;
    for (let i = transitions.length - 1; i >= 0; i--) {
      const t = transitions[i], current = classified?.tasks.find(task => task.key === t.taskKey);
      if (current && t.toStatus === current.status && !latest.has(t.taskKey)) latest.set(t.taskKey, t.observedAt);
    }
    const shown = classified ? { ...source, ...classified, rules, picker, queue: shownQueue, tasks: classified.tasks.map(t => ({ ...t, statusObservedAt: latest.get(t.key) ?? null })) } : null;
    return { app: 'Sijav to-do dashboard', readOnly: true, instanceId, revision, checkedAt, readCount, ignoredEvents, lastFileEventAt,
      ui: settings.ui, rules, transport: settings.transport, watch: settings.watch, workClassification: {}, watchState,
      server: { project: projectRoot, db: dbPath, dataDir, readOnly: true, python: picker.python, todo: picker.todo, toolSha },
      trackingSince: baseline.startedAt, persistenceError: [persistenceError, journalWarning].filter(Boolean).join(' ') || null,
      boards: shown ? [shown] : [], statusTransitions: transitions.slice().reverse(), changeCount: journal.length, latestSeq: journal.at(-1)?.seq ?? 0 };
  };
  // Several state changes in one turn of the event loop produce one push.
  const notify = () => {
    revision++;
    if (pendingNotify || closed) return;
    pendingNotify = true;
    setImmediate(() => { pendingNotify = false; if (closed) return; const snap = snapshot(); for (const listener of listeners) listener(snap); });
  };

  const persist = (current, changes) => {
    let failed = false;
    if (changes.length) {
      for (const change of changes) change.seq = nextSeq++;
      try {
        appendFileSync(journalPath, (needsNewline ? '\n' : '') + changes.map(encode).join('\n') + '\n'); needsNewline = false;
        journal.push(...changes); transitions.push(...changes.map(transitionOf).filter(Boolean));
      } catch (e) { persistenceError = 'Change journal could not be saved: ' + e.message; failed = true; nextSeq -= changes.length; }
    }
    baseline.board = { hash: current.hash, schema: current.schema, indexes: current.indexes, tables: current.tables, primaryKeys: current.primaryKeys,
      name: boardName, tasks: current.tasks.map(t => ({ id: t.id, title: t.title })) };
    baseline.dbPath = dbPath;
    try { writeFileSync(statePath + '.tmp', encode(baseline)); renameSync(statePath + '.tmp', statePath); if (!failed) persistenceError = null; }
    catch (e) { persistenceError = 'Change baseline could not be saved: ' + e.message; }
  };

  function readNow({ requeue = false } = {}) {
    if (closed) return;
    checkedAt = new Date().toISOString(); readCount++;
    const before = fileSignature(dbPath), wasAvailable = board?.available === true;
    try {
      const current = { ...readBoard(dbPath, { busyMs: settings.read?.busyMs ?? 800 }), name: boardName };
      const after = fileSignature(dbPath);
      lastSignature = before === after ? after : null;
      if (before !== after) schedule(true);
      const previous = baseline.board;
      if (!previous || previous.hash !== current.hash) persist(current, diffBoard(previous, current, checkedAt));
      const changed = !lastReadable || lastReadable.hash !== current.hash;
      board = current; lastReadable = current;
      if (changed || requeue || !wasAvailable || queue.state === 'unavailable') requestQueue();
    } catch (e) {
      lastSignature = null; // a failed read never hides the next file event
      board = { ...(lastReadable || { tasks: [], tables: {}, schema: [], statuses: {} }), available: false, stale: !!lastReadable, error: e.message };
      // The pick belongs to rows that can no longer be confirmed: drop it, and
      // make any picker run still in flight publish nothing.
      if (picker.configured) {
        queue.requested++; queue.processed = queue.requested; queue.previous = null;
        queue.state = 'unavailable'; queue.error = 'The board could not be read, so its next pick is unknown: ' + e.message;
      }
    }
    notify();
  }
  function schedule(force = false) {
    if (closed) return;
    forced ||= force;
    clearTimeout(debounce);
    debounce = setTimeout(() => {
      const must = forced; forced = false;
      // Our own read-only opens touch SQLite lock state; a notification that left the
      // board, WAL, SHM and journal identical to the last successful read is housekeeping.
      if (!must && lastSignature !== null && fileSignature(dbPath) === lastSignature) { ignoredEvents++; return; }
      readNow();
    }, settings.watch.debounceMs);
  }

  function requestQueue() {
    if (closed || !picker.configured) return Promise.resolve();
    queue.requested++;
    if (queue.state === 'ready' && queue.result) queue.previous = { checkedAt: queue.result.checkedAt, rankedIds: queue.result.rankedIds };
    queue.state = 'checking'; queue.error = null;
    if (!queue.promise) {
      queue.promise = (async () => {
        let mismatches = 0, toolChanges = 0;
        while (!closed && queue.processed < queue.requested) {
          const generation = queue.requested;
          if (!board?.available) { queue.processed = generation; queue.previous = null; queue.state = 'unavailable'; queue.error = 'The board could not be read, so its next pick is unknown. ' + (board?.error || ''); continue; }
          try {
            const sha = todo ? fileSha(todo) : null;
            const result = await runPicker({ python, todo, dbPath, settings: settings.picker });
            if (closed) return;
            if (generation !== queue.requested) continue;
            if (result.tool.sha256 !== sha || sha !== fileSha(todo)) { // todo.py changed mid-run
              toolSha = fileSha(todo);
              if (++toolChanges > 3) { queue.processed = generation; queue.previous = null; queue.state = 'error'; queue.error = 'todo.py kept changing during every check. It is read again on its next change.'; }
              continue;
            }
            toolSha = sha;
            if (rowsDigest(result.tables) !== board.rowsDigest) {
              if (++mismatches > 3) { queue.processed = generation; queue.previous = null; queue.state = 'error'; queue.error = 'The board changed during every picker check. It is checked again on its next change.'; continue; }
              readNow(); continue; // the picker saw a later snapshot: read it, then check again
            }
            delete result.tables;
            queue.result = result; queue.previous = null; queue.processed = generation; queue.state = 'ready'; queue.error = null; mismatches = 0;
          } catch (error) {
            if (closed) return;
            if (generation !== queue.requested) continue;
            queue.processed = generation; queue.previous = null; queue.state = 'error'; queue.error = error.message;
            if (todo) toolSha = fileSha(todo);
          }
        }
      })().finally(() => {
        queue.promise = null;
        if (closed) return;
        if (queue.processed < queue.requested && board?.available) requestQueue(); else notify();
      });
    }
    notify();
    return queue.promise;
  }

  // Watch the board's directory, not the file: the watch survives atomic
  // replacement, and WAL/SHM/journal files come and go beside it.
  const names = SUFFIXES.map(s => basename(dbPath) + s);
  try {
    const watcher = watch(dirname(dbPath), (event, filename) => {
      if (closed) return;
      const file = filename == null ? null : basename(String(filename));
      if (file !== null && !names.some(n => sameName(n, file))) return;
      lastFileEventAt = new Date().toISOString();
      schedule(false);
    });
    watcher.on('error', e => { watchState.board = { ...watchState.board, active: false, error: e.message }; notify(); });
    watchers.push(watcher); watchState.board.active = true;
  } catch (e) { watchState.board.error = e.message; }
  // The tool decides order and status meaning: any change to it is read again.
  if (picker.configured) {
    try {
      const watcher = watch(dirname(todo), (event, filename) => {
        if (closed || (filename != null && !sameName(basename(String(filename)), basename(todo)))) return;
        const sha = fileSha(todo);
        if (sha === toolSha && queue.state === 'ready') return;
        toolSha = sha;
        if (board?.available) requestQueue(); else notify();
      });
      watcher.on('error', e => { watchState.tool = { ...watchState.tool, active: false, error: e.message }; notify(); });
      watchers.push(watcher); watchState.tool.active = true;
    } catch (e) { watchState.tool.error = e.message; }
  }
  readNow();

  const recheck = async () => {
    if (closed) return snapshot();
    readNow({ requeue: true });
    while (!closed && queue.promise) await queue.promise;
    return snapshot();
  };
  /** Change history, newest first: a page before a sequence number, everything after one, or one task's entries. */
  const changes = ({ before = null, after = null, limit = null, item = null } = {}) => {
    let list = journal;
    if (item != null) list = list.filter(c => c.itemId === String(item));
    if (after != null) list = list.filter(c => c.seq > after);
    if (before != null) list = list.filter(c => c.seq < before);
    list = list.slice().reverse();
    return limit != null ? list.slice(0, limit) : list;
  };
  return {
    readNow, recheck, snapshot, changes, paths: { statePath, journalPath, dataDir },
    settled: async () => { while (!closed && (queue.promise || pendingNotify)) { if (queue.promise) await queue.promise; else await new Promise(r => setImmediate(r)); } return snapshot(); },
    subscribe: listener => { listeners.add(listener); return () => listeners.delete(listener); },
    close: () => { closed = true; clearTimeout(debounce); for (const watcher of watchers) watcher.close(); listeners.clear(); },
  };
}
