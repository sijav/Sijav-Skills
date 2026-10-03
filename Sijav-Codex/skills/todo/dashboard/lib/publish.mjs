// Publishing the dashboard through a relay (for example a team page on a public site). The
// dashboard keeps ONE outbound socket to the relay, proven by a bearer token read from a file:
// it greets the relay with the boards' details, answers each question the relay passes on with
// the same typed queries the local page uses, and pushes what changed. The relay keeps nothing
// about the boards; nothing here can write a board.
//
// Relay protocol: the dashboard sends `hello` and `changed` (as the local page gets them) and
// `{type: "answer", relay, data | error}`; the relay sends `{type: "ask", relay, request}`.
import { encode } from './board.mjs';
import { createSession } from './session.mjs';

export function startPublisher({ url, token, monitor, settings, WebSocket, log = () => {} }) {
  const session = createSession(monitor, settings);
  let socket = null, closed = false, retry = null, ping = null, wait = 1000, sentUpTo = 0, state = 'connecting', lastError = null;
  const connect = () => {
    if (closed) return;
    state = 'connecting';
    const current = socket = new WebSocket(url, { headers: { Authorization: `Bearer ${token}` } });
    current.on('open', () => {
      wait = 1000; state = 'published'; lastError = null;
      sentUpTo = monitor.snapshot().latestSeq;
      current.send(encode(session.greeting()));
      log(`Published to ${url}.`);
      // A proxy may close a socket that says nothing for minutes; a ping keeps it, it reads no board.
      ping = setInterval(() => { if (current.readyState === 1) current.ping(); }, 25000);
    });
    current.on('message', text => {
      let message = null;
      try { message = JSON.parse(String(text)); } catch {}
      if (message?.type !== 'ask') return;
      let reply;
      try { reply = { type: 'answer', relay: message.relay, data: session.answerRequest(message.request) }; }
      catch (error) { reply = { type: 'answer', relay: message.relay, error: error.message }; }
      if (current.readyState === 1) current.send(encode(reply));
    });
    current.on('close', code => {
      clearInterval(ping);
      state = 'disconnected';
      if (closed) return;
      log(`The relay closed the connection (${code}); trying again in ${Math.round(wait / 1000)} s.`);
      retry = setTimeout(connect, wait);
      wait = Math.min(wait * 2, 60000);
    });
    current.on('error', error => { lastError = error.message; });
  };
  const unsubscribe = monitor.subscribe(snapshot => {
    const base = session.change(snapshot);
    if (socket?.readyState !== 1) return;
    const changes = monitor.changes({ after: sentUpTo });
    sentUpTo = snapshot.latestSeq;
    socket.send(encode({ ...base, changes }));
  });
  connect();
  return {
    url, status: () => ({ state, lastError }),
    close: () => { closed = true; clearTimeout(retry); clearInterval(ping); unsubscribe(); socket?.terminate(); },
  };
}
