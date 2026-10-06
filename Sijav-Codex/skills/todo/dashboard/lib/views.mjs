// The dashboard's paginated queries. The page never gets a board whole: on
// connect it gets each board's details (counts, order, rules), then asks for
// what it shows -- a list five at a time, one task's record, a table's rows,
// the totals for its filters -- and a change on a board is pushed as that
// board's details and the keys of the tasks that changed.
//
// Every answer is computed from the monitor's snapshot with the page's own
// ordering, testing and work-classification code, so the server and the page
// can never order or count a list differently. That snapshot is this server's
// own (lib/board.mjs): it always has a boards array, and each board its queue
// and rules, so nothing here guards against their absence.
import { sortLane, sortQueue, pickerReady, nextTask, firstOpen, testingSummary, testingState, supportsTesting, matchesTestingFilter } from '../public/board-ui.mjs';
import { classifyWork } from '../public/work-context.mjs';
import { buildAmbientModel } from '../public/ambient-ui.mjs';
import { createHash } from 'node:crypto';

export const PAGE = 5;
const FACETS = { areas: 'area', types: 'type', topics: 'topic' };
// A card carries a task's stored fields, each long one cut to this; the full
// record comes with the `task` query when the task is opened.
const CARD_TEXT = 1200;
// A card's title, story and why travel once, as the card's own fields, never again inside its stored fields.
const ON_THE_CARD = new Set(['title', 'descr', 'story', 'why']);

const digest = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');
const brief = t => t ? { key: t.key, sourceId: t.sourceId, id: t.id, title: t.title, status: t.status } : null;
const cut = value => typeof value === 'string' && value.length > CARD_TEXT ? value.slice(0, CARD_TEXT) + '…' : value;

/** The queue without its per-task parts (reasons and sort keys come with each card and record). */
function lightQueue(q = {}) {
  const { deferred, rankKeys, children, tables, ...rest } = q;
  return rest;
}

/** A board's details: everything but its tasks and its tables' rows. */
export function boardMeta(b) {
  const { tasks = [], tables = {}, ...rest } = b;
  return { ...rest, queue: lightQueue(b.queue), total: tasks.length,
    tableCounts: Object.fromEntries(Object.entries(tables).map(([name, rows]) => [name, rows.length])),
    phases: tables.phase || [], head: brief(pickerReady(b) ? nextTask(b) : null), firstOpen: brief(pickerReady(b) ? firstOpen(b) : null) };
}

/** What a client gets on connect: each board's details, nothing per task. History is asked for. */
export function hello(snapshot, transitionsLimit = 50) {
  const { boards = [], statusTransitions = [], ...rest } = snapshot;
  return { ...rest, type: 'hello', boards: boards.map(boardMeta), statusTransitions: statusTransitions.slice(0, transitionsLimit) };
}

/** Each board's details' fingerprint, to push only the boards whose details changed. */
export function metaPrints(boards) {
  return new Map(boards.map(b => [b.id, digest(b)]));
}

/** A task as a card: its stored fields (long ones cut), counts of its related rows, and why the tool does not offer it. */
export function card(t, b) {
  const { related = {}, notes = [], roasts = [], raw = {}, ...rest } = t;
  const reasons = pickerReady(b) ? (b.queue.deferred?.[t.id] || []).filter(r => r.kind !== 'check') : [];
  return { ...rest, raw: Object.fromEntries(Object.entries(raw).filter(([key]) => !ON_THE_CARD.has(key)).map(([key, value]) => [key, cut(value)])),
    relatedCounts: Object.fromEntries(Object.entries(related).map(([name, rows]) => [name, rows.length])),
    openFindingRows: b.kind === 'loop' ? (related.finding || []).filter(f => f.status === b.tableDefaults?.finding?.status).length : null,
    notesCount: notes.length, roastsCount: roasts.length, queueReasons: reasons };
}

/** The whole record of one task, with the tool's evidence for its place in the order and its links. */
export function record(snapshot, key) {
  for (const b of snapshot.boards) {
    const t = b.tasks.find(x => x.key === key);
    if (!t) continue;
    const q = b.queue, ready = pickerReady(b);
    const known = id => brief(b.tasks.find(x => x.id === id)) || { id, missing: true };
    return { task: t, links: Object.fromEntries([...new Set([...t.dependencies, ...t.dependents, ...t.children, ...(t.parentTask ? [String(t.parentTask)] : [])])].map(id => [id, known(id)])),
      evidence: ready ? { rankKey: q.rankKeys?.[t.id] ?? null, rankError: q.rankErrors?.[t.id] ?? null, deferred: q.deferred?.[t.id] || [], children: q.children?.[t.id] ?? null } : null };
  }
  return null;
}

const workContext = t => classifyWork(t);

/** The page's filters as one test on a full task. */
export function matcher(filters = {}) {
  const f = { area: 'all', type: 'all', topic: 'all', phase: 'all', status: 'all', severity: 'all', verification: 'all', finishedOnly: false, blocked: false, query: '', ...filters };
  const facet = (t, name, value) => value === 'all' || (value === 'missing' ? !workContext(t)[name].length : workContext(t)[name].some(x => x.label === value));
  const query = String(f.query || '').toLocaleLowerCase();
  return t => facet(t, 'areas', f.area) && facet(t, 'types', f.type) && facet(t, 'topics', f.topic)
    && (f.phase === 'all' || (f.phase === 'none' ? !t.phase : t.phase === f.phase))
    && (f.status === 'all' || t.status === f.status) && (f.severity === 'all' || t.severity === f.severity)
    && matchesTestingFilter(t, f.verification) && (!f.finishedOnly || t.isFinished) && (!f.blocked || t.isExplicitlyBlocked || t.isWaiting)
    && (!query || JSON.stringify({ id: t.id, raw: t.raw, notes: t.notes, roasts: t.roasts, blocked: t.blocked, related: t.related, dependencies: t.dependencies, children: t.children }).toLocaleLowerCase().includes(query));
}

const chosen = (snapshot, board) => snapshot.boards.filter(b => board == null || board === 'all' || b.id === board);
const severityRank = (b, s) => { const list = b.rules.severities; const i = list.indexOf(s); return i < 0 ? list.length : i; };

/** One list's tasks in the page's order, before paging. */
function ordered(snapshot, { list, board, status, filters = {} }) {
  const test = matcher(filters), boards = chosen(snapshot, board);
  if (list === 'lane') return boards.flatMap(b => sortLane(b.tasks.filter(t => t.status === status && test(t)), status, b).map(t => [t, b]));
  if (list === 'doing') return boards.flatMap(b => sortLane(b.tasks.filter(t => t.isDoing && test(t)), b.rules?.doingStatuses?.[0], b).map(t => [t, b]));
  if (list === 'register') {
    const sort = filters.sort || 'next', pairs = boards.flatMap(b => sortQueue(b.tasks.filter(test), b).map(t => [t, b]));
    if (sort === 'recent') return pairs.sort(([a], [c]) => (c.lastRecordedAt || '').localeCompare(a.lastRecordedAt || ''));
    if (sort === 'severity') return pairs.map((p, i) => [p, i]).sort(([[a, ab], i], [[c, cb], j]) => severityRank(ab, a.severity) - severityRank(cb, c.severity) || i - j).map(([p]) => p);
    if (sort === 'id') return pairs.sort(([a], [c]) => a.id.localeCompare(c.id, undefined, { numeric: true }));
    return pairs;
  }
  throw new Error(`Unknown list "${list}".`);
}

/** A page of a list: `limit` cards from `offset`, and the list's length. */
export function list(snapshot, { list: name, board = 'all', status = null, filters = {}, offset = 0, limit = PAGE }) {
  const all = ordered(snapshot, { list: name, board, status, filters });
  const start = Math.max(0, Number(offset) || 0), size = Math.min(100, Math.max(1, Number(limit) || PAGE));
  return { items: all.slice(start, start + size).map(([t, b]) => card(t, b)), total: all.length, offset: start };
}

/** The dated records the report's activity panel shows, newest first. */
function events(tasks, limit) {
  const time = value => { if (value == null || value === '') return null; if (typeof value === 'number') return new Date(value < 1e12 ? value * 1000 : value).toISOString(); const text = String(value); const d = new Date(/(?:Z|[+-]\d\d:\d\d)$/.test(text) ? text : text.replace(' ', 'T') + 'Z'); return Number.isNaN(+d) ? null : d.toISOString(); };
  const out = [];
  for (const t of tasks) {
    const add = (at, label, text = '') => { const when = time(at); if (when) out.push({ at: when, raw: at, label, text: String(text ?? '').slice(0, 160), key: t.key, title: t.title, id: t.id, sourceId: t.sourceId }); };
    add(t.raw.created, 'Task created'); add(t.raw.closed, 'Task done', t.raw.evidence || ''); add(t.raw.updated, 'Task last updated');
    for (const r of t.blocked) add(r.since, 'Blocked', r.reason);
    for (const n of t.notes) add(n.at, 'Note recorded', n.text);
    for (const r of t.roasts) add(r.at, 'Roast round ' + r.round + ' recorded', r.file);
  }
  return out.sort((a, b) => b.at.localeCompare(a.at)).slice(0, limit);
}

/** Everything the report counts, for the chosen boards and filters. */
export function summary(snapshot, { board = 'all', filters = {} } = {}, { activityLimit = 10, testingFlags = {} } = {}) {
  const test = matcher(filters), boards = chosen(snapshot, board);
  const openFindings = (b, ts) => b.kind === 'loop' ? ts.reduce((n, t) => n + (t.related?.finding || []).filter(f => f.status === b.tableDefaults?.finding?.status).length, 0) : ts.filter(t => t.isFinding && !t.isComplete).length;
  const perBoard = {}, all = [];
  for (const b of boards) {
    const ts = b.tasks.filter(test); all.push(...ts);
    const lastClosed = ts.filter(t => t.isFinished && t.closedAt).sort((a, c) => c.closedAt.localeCompare(a.closedAt))[0];
    const phases = Object.fromEntries((b.tables?.phase || []).map(p => { const mine = b.tasks.filter(t => t.phase === p.name); return [p.name, { open: mine.filter(t => !t.isComplete).length, done: mine.filter(t => t.isFinished).length, all: mine.length }]; }));
    perBoard[b.id] = { shown: ts.length, total: b.tasks.length, unfinished: ts.filter(t => !t.isComplete).length, doing: ts.filter(t => t.isDoing).length,
      done: ts.filter(t => t.isFinished).length, otherUnfinished: ts.filter(t => !t.isComplete && !t.isDoing).length, openFindings: openFindings(b, ts),
      lastClosed: lastClosed ? { ...brief(lastClosed), closed: lastClosed.raw.closed ?? lastClosed.closedAt } : null,
      testing: testingSummary(b, b.tasks, testingFlags), allUnfinished: b.tasks.filter(t => !t.isComplete).length,
      phases, loose: b.tasks.filter(t => !t.phase && !t.isComplete).length, notes: (b.tables?.note || []).length };
  }
  const statuses = {}; for (const t of all) statuses[t.status] = (statuses[t.status] || 0) + 1;
  const rules = boards[0]?.rules || {}, severities = {};
  for (const t of rules.known ? all.filter(t => !t.isComplete) : all) severities[t.severity] = (severities[t.severity] || 0) + 1;
  const everything = boards.flatMap(b => b.tasks), facets = {};
  for (const [name] of Object.entries(FACETS)) {
    facets[name] = { labels: [...new Set(everything.flatMap(t => workContext(t)[name].map(x => x.label)))].sort((a, c) => a.localeCompare(c)), missing: everything.some(t => !workContext(t)[name].length) };
  }
  return { shown: all.length, open: all.filter(t => !t.isComplete).length, doing: all.filter(t => t.isDoing).length, done: all.filter(t => t.isFinished).length,
    findings: boards.reduce((n, b) => n + openFindings(b, all.filter(t => t.sourceId === b.id)), 0), blocked: all.filter(t => t.isExplicitlyBlocked).length,
    waiting: all.filter(t => t.isWaiting).length, statuses, severities, severityBase: rules.known ? all.filter(t => !t.isComplete).length : all.length,
    perBoard, facets, events: events(all, activityLimit),
    // The testing filter's choices: passed and pending where a board records the flag, unknown where one does not or a task has no value.
    testingValues: Object.fromEntries(Object.keys(testingFlags).map(key => [key, { recorded: boards.some(b => supportsTesting(b, key)),
      unknown: boards.some(b => !supportsTesting(b, key) || b.tasks.some(t => testingState(t, key) === 'unknown')) }])) };
}

/** A page of a table's stored rows. */
export function rows(snapshot, { board, table, offset = 0, limit = 100 }) {
  const b = snapshot.boards.find(x => x.id === board);
  const all = b?.tables?.[table] || [], start = Math.max(0, Number(offset) || 0), size = Math.min(500, Math.max(1, Number(limit) || 100));
  return { rows: all.slice(start, start + size), total: all.length, offset: start, primaryKeys: b?.primaryKeys?.[table] || [] };
}

/** Relax's view, built where every task is. */
export const relax = snapshot => buildAmbientModel(snapshot);

/** Each task's fingerprint per board, to tell a client which tasks a change touched. */
export function fingerprints(snapshot) {
  return Object.fromEntries(snapshot.boards.map(b => [b.id, new Map(b.tasks.map(t => [t.key, digest(t)]))]));
}
/** The keys of tasks that are new, changed or gone between two fingerprint sets. */
export function changedKeys(before, after) {
  const keys = [];
  for (const [board, now] of Object.entries(after)) {
    const was = before?.[board] || new Map();
    for (const [key, print] of now) if (was.get(key) !== print) keys.push(key);
    for (const key of was.keys()) if (!now.has(key)) keys.push(key);
  }
  return keys;
}

/** Answer one typed request. */
export function answer(snapshot, request, settings = {}) {
  switch (request.type) {
    case 'list': return list(snapshot, request);
    case 'summary': return summary(snapshot, request, { activityLimit: settings.ui?.activityLimit ?? 10, testingFlags: settings.ui?.testingFlags || {} });
    case 'task': return record(snapshot, request.key);
    case 'rows': return rows(snapshot, request);
    case 'relax': return relax(snapshot);
    default: throw new Error(`Unknown request "${request.type}".`);
  }
}
