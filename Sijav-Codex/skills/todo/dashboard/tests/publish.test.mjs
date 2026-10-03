// Publishing through a relay (--publish): one outbound socket, proven by a token read from a file,
// that greets the relay, answers the questions it passes on, and pushes what changed.
import test from 'node:test';
import assert from 'node:assert/strict';
import { writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { startDashboard, describe } from '../server.mjs';
import { cleanup, listing, loopProject, sha, STAGE, tempDir, testEnv, until } from './helpers.mjs';

test.after(cleanup);
const { WebSocketServer } = await import(pathToFileURL(join(STAGE, 'node_modules', 'ws', 'wrapper.mjs')).href);

/** A relay on this machine that records what the dashboard says and can pass it questions. */
async function fakeRelay() {
  const server = new WebSocketServer({ host: '127.0.0.1', port: 0 });
  await new Promise(ok => server.once('listening', ok));
  const relay = { messages: [], headers: null, socket: null, answers: new Map(), url: `ws://127.0.0.1:${server.address().port}/relay` };
  server.on('connection', (socket, request) => {
    relay.socket = socket; relay.headers = request.headers;
    socket.on('message', data => { const m = JSON.parse(String(data)); if (m.type === 'answer') relay.answers.set(m.relay, m); else relay.messages.push(m); });
  });
  relay.ask = async (n, request) => { relay.socket.send(JSON.stringify({ type: 'ask', relay: n, request })); return until(() => relay.answers.get(n), 'the answer to ' + n); };
  relay.close = () => new Promise(ok => { for (const c of server.clients) c.terminate(); server.close(ok); });
  return relay;
}

test('the dashboard publishes through a relay with the token from its file: a greeting, answers and changes', { timeout: 120000 }, async () => {
  const p = loopProject(), relay = await fakeRelay();
  const tokenFile = join(tempDir('publish token '), 'token.txt');
  writeFileSync(tokenFile, 'relay-token-for-the-test\n');
  const untouched = sha(p.db); // before the dashboard starts
  const started = await startDashboard({ db: p.db, publish: relay.url, publishTokenFile: tokenFile }, { cwd: p.root, env: testEnv() });
  let before = null;
  try {
    const hello = await until(() => relay.messages.find(m => m.type === 'hello'), 'the greeting');
    assert.equal(relay.headers.authorization, 'Bearer relay-token-for-the-test', 'the token comes from the file');
    assert.equal(hello.boards[0].tasks, undefined, 'the relay is never sent a board whole');
    await until(() => relay.messages.some(m => m.boards?.[0]?.queue?.state === 'ready'), 'the order pushed');
    const page = await relay.ask(1, { type: 'list', list: 'register', board: 'all', offset: 0, limit: 5 });
    assert.deepEqual(page.data.items.map(t => t.id), ['1', '4', '6', '2', '3']);
    assert.equal(page.data.total, 6);
    const record = await relay.ask(2, { type: 'task', key: 'board:2' });
    assert.equal(record.data.task.title, 'Needs foundation');
    assert.match((await relay.ask(3, { type: 'nonsense' })).error, /Unknown request/);
    const greeting = (await relay.ask(4, { type: 'hello' })).data;
    assert.equal(greeting.type, 'hello', 'a relay that keeps nothing asks for the greeting of each page it signs in');
    assert.equal(greeting.boards[0].tasks, undefined);
    assert.equal(sha(p.db), untouched, 'greeting the relay and answering its first questions never wrote the board');
    p.run('start', '6');
    await until(() => relay.messages.some(m => m.type === 'changed' && m.keys.includes('board:6')), 'the change pushed');
    before = sha(p.db); // the board as its own tool left it
    for (const [n, request] of [[5, { type: 'summary', board: 'all' }], [6, { type: 'task', key: 'board:6' }], [7, { type: 'relax' }]]) await relay.ask(n, request);
    assert.match(describe(started), /Publish: +ws:\/\/127\.0\.0\.1:\d+\/relay · published/);
  } finally { await started.close(); await relay.close(); }
  assert.equal(sha(p.db), before, 'answering the relay never writes the board');
  assert.ok(listing(p.dir).every(name => ['board.db', 'board.py', 'tool'].includes(name)), 'nothing created beside the board');
});

test('publishing is refused without a safe address or a readable token file', async () => {
  const p = loopProject(), tokens = tempDir('publish token ');
  const token = join(tokens, 'token.txt'), empty = join(tokens, 'empty.txt');
  writeFileSync(token, 'a-token\n'); writeFileSync(empty, '  \n');
  const start = options => startDashboard({ db: p.db, ...options }, { cwd: p.root, env: testEnv() });
  await assert.rejects(start({ publish: 'http://relay.example/x', publishTokenFile: token }), /needs a wss:\/\/ address/);
  await assert.rejects(start({ publish: 'ws://relay.example/x', publishTokenFile: token }), /needs a wss:\/\/ address/);
  await assert.rejects(start({ publish: 'wss://relay.example/x' }), /--publish-token-file/);
  await assert.rejects(start({ publish: 'wss://relay.example/x', publishTokenFile: join(tokens, 'missing.txt') }), /cannot be read/);
  await assert.rejects(start({ publish: 'wss://relay.example/x', publishTokenFile: empty }), /is empty/);
});
