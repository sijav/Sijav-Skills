// One monitor's conversation with its pages, for the local socket and for a relay alike: the
// greeting, the answer to each typed question, and what changed since the last push.
import { hello, answer, fingerprints, changedKeys, metaPrints } from './views.mjs';

export function createSession(monitor, settings) {
  const page = settings.ui.changePageSize;
  /** A page of history, as /api/changes gives it. */
  const history = ({ before = null, after = null, limit = page, item = null } = {}) => {
    const size = limit === 'all' ? null : Number(limit) > 0 ? Number(limit) : page;
    const changes = monitor.changes({ before, after, item, limit: size }), s = monitor.snapshot();
    const total = item != null ? monitor.changes({ item }).length : s.changeCount;
    return { changes, total, complete: size == null || changes.length < size, latestSeq: s.latestSeq };
  };
  const greeting = () => hello(monitor.snapshot());
  // `hello` is also a question: a relay keeps nothing, so it asks for the greeting of each page it signs in.
  const answerRequest = request => request?.type === 'hello' ? greeting()
    : request?.type === 'changes' ? history(request) : answer(monitor.snapshot(), request || {}, settings);
  let prints = fingerprints(monitor.snapshot()), metas = metaPrints(greeting().boards);
  /** Call once per monitor notification: the boards whose details changed, the changed task keys, the top fields. */
  const change = snapshot => {
    const nowPrints = fingerprints(snapshot), keys = changedKeys(prints, nowPrints);
    const greet = hello(snapshot), nowMetas = metaPrints(greet.boards);
    const boards = greet.boards.filter(b => metas.get(b.id) !== nowMetas.get(b.id));
    prints = nowPrints; metas = nowMetas;
    const { boards: _all, type: _type, ...top } = greet;
    return { ...top, type: 'changed', boards, keys };
  };
  return { greeting, answerRequest, change };
}
