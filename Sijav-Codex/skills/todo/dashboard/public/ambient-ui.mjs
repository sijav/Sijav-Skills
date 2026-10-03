import {nextTask, firstOpen, sortQueue, formatDate, finishedAt, clampText} from './board-ui.mjs';
import {classifyWork, facetSupport} from './work-context.mjs';

const escape = value => String(value ?? '').replace(/[&<>"']/g, character => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[character]));
const taskId = task => /^\d+$/.test(task.id) ? `#${task.id}` : task.id;
const countKeys = ['total', 'open', 'doing', 'done', 'discarded', 'other'];

function recordedText(task, ...fields) {
  const raw = task.raw || {};
  for (const field of fields) {
    if (Object.hasOwn(raw, field)) return raw[field] == null || raw[field] === '' ? null : String(raw[field]);
  }
  return null;
}

function presentTask(task, board, definitions) {
  return {
    id: String(task.id), key: task.key, title: String(task.title ?? ''),
    status: task.status, severity: task.severity, points: task.points, phase: task.phase ?? null,
    story: recordedText(task, 'descr', 'story'), reason: recordedText(task, 'why'),
    closedAt: task.closedAt ?? null, statusObservedAt: task.statusObservedAt ?? null,
    context: classifyWork(task, definitions), support: facetSupport(board.tableColumns?.[board.taskTable] || Object.keys(task.raw || {})),
    queueReasons: board.queue?.deferred?.[String(task.id)] || [],
  };
}

/** A view of recorded rows and the tool's measured pick; no independent picker. */
export function buildAmbientModel(snapshot = {}) {
  const definitions = snapshot.workClassification || {};
  const limit = snapshot.ui?.activityLimit;
  const queuePreviewLimit = Number.isInteger(limit) && limit > 0 ? limit : null;
  const errors = [];
  if (snapshot.persistenceError) errors.push(String(snapshot.persistenceError));
  const boards = (snapshot.boards || []).map(board => {
    const rules = board.rules || snapshot.rules || {known: false, openStatuses: [], doingStatuses: [], finishedStatuses: [], discardedStatuses: []};
    const tasks = board.tasks || [];
    const queue = board.queue || {};
    const available = board.available !== false;
    const stale = !!board.stale;
    const boardErrors = [];
    if (!available) boardErrors.push(board.error || 'This board could not be read.');
    if (!rules.known && rules.reason) boardErrors.push(`Status groups unknown: ${rules.reason}`);
    const watcher = snapshot.watchState?.board;
    if (watcher?.error) boardErrors.push(`Board watcher: ${watcher.error}`);
    if (queue.error) boardErrors.push(String(queue.error));
    let queueState = !board.picker?.configured ? 'unconfigured' : !available ? 'unavailable' : queue.state || 'checking';
    if (queueState === 'ready' && (stale || queue.stale)) queueState = 'stale';
    let ready = queueState === 'ready';
    const selected = ready ? nextTask(board) : null;
    if (ready && queue.headId != null && !selected) {
      ready = false; queueState = 'error';
      boardErrors.push('The picker’s pick is absent from this board snapshot.');
    }
    const ordered = sortQueue(tasks, board);
    const doingTasks = ordered.filter(task => rules.doingStatuses.includes(task.status));
    const eligible = ready ? (queue.eligibleIds || []).map(id => tasks.find(task => task.id === String(id))).filter(Boolean) : [];
    const doneTasks = tasks.filter(task => rules.finishedStatuses.includes(task.status));
    const closed = doneTasks.filter(task => task.closedAt).sort((a, b) => finishedAt(b).localeCompare(finishedAt(a)))[0] || null;
    const known = value => rules.known ? value : null;
    const counts = {
      total: tasks.length,
      open: known(tasks.filter(task => rules.openStatuses.includes(task.status)).length),
      doing: known(doingTasks.length),
      done: known(doneTasks.length),
      discarded: known(tasks.filter(task => rules.discardedStatuses.includes(task.status)).length),
      other: known(tasks.filter(task => ![...rules.openStatuses, ...rules.doingStatuses, ...rules.finishedStatuses, ...rules.discardedStatuses].includes(task.status)).length),
      startable: ready ? queue.startableIds.length : null,
      deferred: ready ? Math.max(0, queue.rankedIds.length - queue.startableIds.length) : null,
    };
    const head = selected ? presentTask(selected, board, definitions) : null;
    const open = ready ? firstOpen(board, rules) : null;
    const queued = eligible.filter(task => String(task.id) !== head?.id).map(task => presentTask(task, board, definitions));
    errors.push(...boardErrors.map(error => `${board.name || board.id}: ${error}`));
    const phase = ready ? queue.currentPhase : null;
    return {
      id: board.id, name: board.name || board.id, available, stale, error: board.error || null,
      tool: board.tool || snapshot.server?.tool || 'todo.py',
      next: (board.boardKind ?? snapshot.server?.kind) === 'loop' ? `${board.tool || snapshot.server?.tool || 'the board’s tool'}’s board_order()` : `${board.tool || snapshot.server?.tool || 'todo.py'} next`,
      doing: doingTasks.map(task => presentTask(task, board, definitions)),
      head, headRole: !head ? 'none' : queue.headKind === 'started' ? 'resume' : 'next',
      firstOpen: open ? presentTask(open, board, definitions) : null,
      latestClosed: closed ? presentTask(closed, board, definitions) : null,
      counts,
      queue: {state: queueState, ready, checkedAt: queue.checkedAt ?? null, error: queue.error || board.picker?.reason || null,
        nextText: ready ? queue.nextText ?? null : null, emptyBoard: ready && queue.boardHadTaskTable === false},
      phase, hasPhases: (board.tables?.phase || []).length > 0,
      statusNames: {open: (rules.openStatuses || []).join(', '), doing: (rules.doingStatuses || []).join(', '), done: (rules.finishedStatuses || []).join(', ')},
      queued, queuePreview: queuePreviewLimit == null ? queued : queued.slice(0, queuePreviewLimit),
      errors: boardErrors,
    };
  });
  const counts = {};
  for (const key of [...countKeys, 'startable', 'deferred']) {
    counts[key] = boards.every(board => board.counts[key] != null)
      ? boards.reduce((sum, board) => sum + board.counts[key], 0) : null;
  }
  return {checkedAt: snapshot.checkedAt ?? null, boards, counts, errors: [...new Set(errors)], queuePreviewLimit};
}

function contextTags(task) {
  return Object.entries({areas: 'Area', types: 'Type', topics: 'Topic'}).map(([facet, label]) => {
    const entries = task.context[facet];
    if (entries.length) return entries.map(entry => `<span class="ambient-tag ${escape(entry.origin)}" title="${escape('Recorded field · ' + entry.evidence.map(item => item.field + ': ' + item.text).join(' | '))}"><span>${label}</span> ${escape(entry.label)}</span>`).join('');
    // A facet the board has a column for, but this task left empty, says so; a
    // facet the board cannot record at all is left off the wallboard.
    return task.support?.[facet]?.length ? `<span class="ambient-tag unknown"><span>${label}</span> not recorded</span>` : '';
  }).join('');
}

function taskCard(task, role) {
  return `<article class="ambient-task ambient-task-${escape(role)}" data-ambient-task="${escape(task.key || task.id)}">
    <div class="ambient-task-meta"><code>${escape(taskId(task))}</code><span>${escape(task.status)}</span>${task.severity ? `<span>${escape(task.severity)}</span>` : ''}${task.points != null ? `<span>${escape(task.points)} pt</span>` : ''}${task.phase ? `<span>Objective ${escape(task.phase)}</span>` : ''}</div>
    <h3 dir="auto">${escape(task.title)}</h3>
    <div class="ambient-tags">${contextTags(task)}</div>
    <div class="ambient-story"><span class="ambient-label">Story</span><p dir="auto">${task.story == null ? '<span class="ambient-muted">No story recorded.</span>' : clampText(task.story, '', null, {chars: 360, toggle: false})}</p>${task.reason ? `<span class="ambient-label">Why</span><p dir="auto">${clampText(task.reason, '', null, {chars: 360, toggle: false})}</p>` : ''}</div>
    ${task.queueReasons.length ? `<details class="ambient-technical ambient-reasons" data-ambient-details="${escape('deferred:' + (task.key || task.id))}"><summary>Not pickable · ${task.queueReasons.length} reason${task.queueReasons.length === 1 ? '' : 's'}</summary>${task.queueReasons.map(reason => `<p dir="auto">${escape(reason.message)}</p>`).join('')}</details>` : ''}
    ${role === 'doing' ? `<div class="ambient-task-foot">Recorded ${escape(task.status)}${task.statusObservedAt ? ' · change observed ' + escape(formatDate(task.statusObservedAt)) : ' · no start time recorded'}</div>` : ''}
  </article>`;
}

function noHead(board) {
  const messages = {
    unconfigured: 'The tool’s picker is unavailable, so the next task is not shown. ' + (board.queue.error || ''),
    unavailable: 'The board is unavailable. Next cannot be confirmed.',
    checking: 'Running the tool’s next. The pick appears when its result is ready.',
    stale: 'The previous pick is stale. Next awaits a current check.',
    error: 'The tool’s picker failed. Next cannot be confirmed.',
  };
  const text = board.queue.state === 'ready'
    ? (board.queue.emptyBoard ? 'This board has no task table yet: nothing has been recorded. That is not evidence that work is finished.' : board.queue.nextText?.trim() || 'The tool picked nothing.')
    : messages[board.queue.state] || messages.error;
  return `<div class="ambient-empty"><span class="ambient-empty-mark">—</span><p dir="auto">${escape(text)}</p></div>`;
}

function boardView(board) {
  const counts = board.counts;
  const showEligibleOpen = board.headRole === 'resume' && board.firstOpen != null;
  const primary = showEligibleOpen ? board.firstOpen : board.head;
  const primaryLabel = showEligibleOpen ? `Next not-started task after started work · ${board.tool}` : board.headRole === 'resume' ? `Resume · ${board.next}` : `Next · ${board.next}`;
  return `<section class="ambient-board" data-ambient-board="${escape(board.id)}">
    <header class="ambient-board-head"><h2>${escape(board.name)}</h2><span class="ambient-source-state">${!board.available ? board.stale ? 'Read failed · last successful data' : 'Read failed' : board.stale ? 'Last successful read' : 'Read-only board'}</span></header>
    <div class="ambient-metrics" aria-label="Whole board counts">${[['Not started', counts.open, board.statusNames.open], ['Doing', counts.doing, board.statusNames.doing], ['Done', counts.done, board.statusNames.done], ['All tasks', counts.total, '']].map(([label, value, statuses]) => `<div title="${escape(statuses)}"><span class="ambient-label">${label}</span><strong>${value ?? '—'}</strong></div>`).join('')}</div>
    <div class="ambient-work-grid">
      <section class="ambient-doing-section"><div class="ambient-section-head"><span class="ambient-kicker"><i class="ambient-indicator"></i>Recorded Doing</span><span class="ambient-count">${counts.doing ?? '—'}</span></div>${board.doing.map(task => taskCard(task, 'doing')).join('') || `<div class="ambient-empty"><span class="ambient-empty-mark">—</span><p>${counts.doing == null ? `Which statuses mean Doing is unknown until ${escape(board.tool)} is read.` : 'No tasks are currently recorded Doing.'}</p></div>`}</section>
      <section class="ambient-next-section"><div class="ambient-section-head"><span class="ambient-kicker">${primaryLabel}</span><span class="ambient-queue-state">${escape(board.queue.state)}</span></div>${primary ? taskCard(primary, 'next') : noHead(board)}
        ${showEligibleOpen ? `<div class="ambient-first-open"><span class="ambient-label">Resume · the tool’s current pick</span><p><code>${escape(taskId(board.head))}</code> <span dir="auto">${escape(board.head.title)}</span></p><small>${escape(board.next)} resumes this started task first. The card above is the first not-started task in its eligible order.</small></div>` : board.headRole === 'resume' ? '<div class="ambient-first-open"><span class="ambient-label">First eligible not-started</span><p class="ambient-muted">No eligible not-started task in this result.</p><small>' + escape(board.next) + ' resumes the started task above.</small></div>' : ''}
        <div class="ambient-eligibility"><div><span class="ambient-label">Pickable</span><strong>${counts.startable ?? '—'}</strong></div><div><span class="ambient-label">Not pickable</span><strong>${counts.deferred ?? '—'}</strong></div><p>Checked ${board.queue.checkedAt ? escape(formatDate(board.queue.checkedAt)) : 'not yet recorded'}</p></div>
        ${board.hasPhases ? `<div class="ambient-technical ambient-environment ready"><span class="ambient-label">Current objective</span><p dir="auto">${board.phase ? `${escape(board.phase.name)}${board.phase.label ? ' · ' + escape(board.phase.label) : ''} — ${escape(board.phase.goal)}` : board.queue.ready ? 'Every objective is met.' : 'Known once the picker result is ready.'}</p></div>` : ''}
      </section>
    </div>
    <div class="ambient-bottom-grid"><section class="ambient-closed"><span class="ambient-kicker">Most recently finished</span>${board.latestClosed ? `<code>${escape(taskId(board.latestClosed))}</code><h3 dir="auto">${escape(board.latestClosed.title)}</h3><div class="ambient-tags">${contextTags(board.latestClosed)}</div><p>Closed ${escape(formatDate(board.latestClosed.closedAt))}</p>` : '<p class="ambient-muted">No done task has a closure time recorded.</p>'}</section>
      <section class="ambient-queue-list"><details class="ambient-following" data-ambient-details="${escape('queue:' + board.id)}"><summary><span class="ambient-kicker">${showEligibleOpen ? 'Eligible not-started order' : 'Following in next order'}</span><span class="ambient-count">${board.queue.ready ? board.queued.length : '—'}</span></summary>${!board.queue.ready ? '<p class="ambient-muted">Waiting for a current picker result.</p>' : board.queuePreview.length ? `<ol>${board.queuePreview.map(task => `<li><code>${escape(taskId(task))}</code><span dir="auto">${escape(task.title)}</span><small>${escape(task.context.areas.map(entry => entry.label).join(' · ') || (task.support.areas.length ? 'Area not recorded' : ''))}</small></li>`).join('')}</ol>${board.queuePreview.length < board.queued.length ? `<p class="ambient-muted">Showing ${board.queuePreview.length} of ${board.queued.length} eligible tasks.</p>` : ''}` : `<p class="ambient-muted">${board.headRole === 'next' ? 'No other eligible task in this result.' : 'No eligible not-started task in this result.'}</p>`}</details></section></div>
  </section>`;
}

/** Render only the live pane; fullscreen, theme and pause controls live outside it. */
export function renderAmbient(snapshot, options = {}) {
  return renderAmbientModel(buildAmbientModel(snapshot || {}), options);
}

/** Relax drawn from a model the server built where every task is (lib/views.mjs relax). */
export function renderAmbientModel(model, {connected = false, connectionLabel = '', paused = false} = {}) {
  const connection = connectionLabel || (connected ? 'Live connection' : 'Disconnected');
  return `<div class="ambient-wallboard${paused ? ' is-paused' : ''}${connected ? ' is-connected' : ' is-disconnected'}">
    <header class="ambient-top"><div><span class="ambient-kicker">${escape(model.boards[0]?.name || 'Project')} / live to-do board</span><h1>Current work &amp; what comes next</h1></div><div class="ambient-live-status"><span><i class="ambient-indicator"></i>${escape(connection)}${paused ? ' · View paused' : ''}</span><small>Last board read ${model.checkedAt ? escape(formatDate(model.checkedAt)) : 'not yet recorded'}</small></div></header>
    ${model.errors.length ? `<div class="ambient-error" role="status">${model.errors.map(error => `<p dir="auto">${escape(error)}</p>`).join('')}</div>` : ''}
    ${model.boards.map(boardView).join('') || '<div class="ambient-empty"><span class="ambient-empty-mark">—</span><p>No board snapshot is available yet.</p></div>'}
    <footer class="ambient-footer"><span>Stored tasks · the tool’s own next · read only</span><span>Doing is a recorded status; worker activity is not measured by this board.</span></footer>
  </div>`;
}
