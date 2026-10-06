// Publisher protocol and its real timers, driven by node:test's supported clock.
// The WebSocket double exposes the normal event interface; this checks timing
// contracts without waiting 25 seconds and makes no real-relay claim.
import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { createMonitor } from '../lib/board.mjs';
import { startPublisher } from '../lib/publish.mjs';
import defaults from '../defaults.json' with { type: 'json' };
import { richProject, tempDir, cleanup } from './helpers.mjs';

test.after(cleanup);
function fixture() {
  const p = richProject('publisher clock');
  const reader = createMonitor({ dbPath: p.db, projectRoot: p.root, dataDir: tempDir('publisher snapshot '), pickerReason: 'timing fixture' });
  const snapshot = reader.snapshot(); reader.close();
  let listener = null, unsubscribed = false;
  const monitor = { snapshot: () => snapshot, changes: () => [],
    subscribe: value => { listener = value; return () => { unsubscribed = true; listener = null; }; } };
  const sockets = [];
  class Socket extends EventEmitter {
    constructor(url, options) { super(); this.url = url; this.options = options; this.readyState = 0; this.sent = []; this.pings = 0; sockets.push(this); }
    send(text) { this.sent.push(JSON.parse(text)); }
    ping() { this.pings++; }
    terminate() { this.readyState = 3; this.emit('close', 1000); }
    open() { this.readyState = 1; this.emit('open'); }
    disconnect(code = 1006) { this.readyState = 3; this.emit('close', code); }
  }
  return { monitor, Socket, sockets, snapshot, notify: () => listener?.(snapshot), unsubscribed: () => unsubscribed };
}

test('relay disconnects grow the reconnect delay to its cap and a successful connection resets it', context => {
  const f = fixture(), messages = [];
  context.mock.timers.enable({ apis: ['setTimeout', 'setInterval'] });
  const publisher = startPublisher({ url: 'wss://relay.example/dashboard', token: 'synthetic-token', monitor: f.monitor,
    settings: defaults, WebSocket: f.Socket, log: value => messages.push(value) });
  try {
    assert.equal(f.sockets.length, 1); assert.equal(publisher.status().state, 'connecting');
    for (const wait of [1000, 2000, 4000, 8000, 16000, 32000, 60000, 60000]) {
      const socket = f.sockets.at(-1), count = f.sockets.length;
      socket.disconnect();
      assert.equal(publisher.status().state, 'disconnected');
      assert.match(messages.at(-1), new RegExp(`trying again in ${wait / 1000} s`));
      context.mock.timers.tick(wait - 1); assert.equal(f.sockets.length, count);
      context.mock.timers.tick(1); assert.equal(f.sockets.length, count + 1);
    }
    f.sockets.at(-1).emit('error', new Error('relay unavailable'));
    assert.equal(publisher.status().lastError, 'relay unavailable');
    f.sockets.at(-1).open();
    assert.deepEqual(publisher.status(), { state: 'published', lastError: null });
    const count = f.sockets.length;
    f.sockets.at(-1).disconnect();
    context.mock.timers.tick(999); assert.equal(f.sockets.length, count);
    context.mock.timers.tick(1); assert.equal(f.sockets.length, count + 1);
  } finally { publisher.close(); }
  const count = f.sockets.length;
  context.mock.timers.tick(120000);
  assert.equal(f.sockets.length, count, 'close cancels the pending reconnect');
  assert.equal(f.unsubscribed(), true);
});

test('keepalive only pings an open relay, requests are read only, and shutdown clears keepalive', context => {
  const f = fixture();
  context.mock.timers.enable({ apis: ['setTimeout', 'setInterval'] });
  const publisher = startPublisher({ url: 'wss://relay.example/dashboard', token: 'synthetic-token', monitor: f.monitor,
    settings: defaults, WebSocket: f.Socket });
  const socket = f.sockets[0];
  try {
    f.notify(); assert.equal(socket.sent.length, 0, 'nothing sent before an open socket');
    socket.open();
    assert.equal(socket.options.headers.Authorization, 'Bearer synthetic-token');
    assert.equal(socket.sent[0].type, 'hello'); assert.equal(socket.sent[0].boards[0].tasks, undefined);
    context.mock.timers.tick(24999); assert.equal(socket.pings, 0);
    context.mock.timers.tick(1); assert.equal(socket.pings, 1);
    socket.readyState = 0;
    context.mock.timers.tick(25000); assert.equal(socket.pings, 1);
    const count = socket.sent.length;
    for (const text of ['bad json', 'null', JSON.stringify({ type: 'unexpected' })]) socket.emit('message', text);
    assert.equal(socket.sent.length, count);
    socket.emit('message', JSON.stringify({ type: 'ask', relay: 'closed', request: { type: 'hello' } }));
    assert.equal(socket.sent.length, count, 'a reply waits for an open socket');
    socket.readyState = 1;
    socket.emit('message', JSON.stringify({ type: 'ask', relay: 'hello', request: { type: 'hello' } }));
    assert.equal(socket.sent.at(-1).relay, 'hello'); assert.equal(socket.sent.at(-1).data.type, 'hello');
    socket.emit('message', JSON.stringify({ type: 'ask', relay: 'bad', request: { type: 'not-a-read-query' } }));
    assert.match(socket.sent.at(-1).error, /Unknown request/);
    f.notify(); assert.equal(socket.sent.at(-1).type, 'changed');
  } finally { publisher.close(); }
  context.mock.timers.tick(100000);
  assert.equal(socket.pings, 1);
  assert.equal(f.sockets.length, 1);
  assert.equal(f.unsubscribed(), true);
});
test('a declared already-dispatched timer callback cannot reconnect a closed publisher',context=>{
  const f=fixture();let dispatched;
  // Explicit scheduler-boundary double: no actual relay or native timer race claim.
  context.mock.method(globalThis,'setTimeout',(callback)=>{dispatched=callback;return 1;});
  context.mock.method(globalThis,'clearTimeout',()=>{});
  const publisher=startPublisher({url:'wss://relay.example/dashboard',token:'synthetic-token',monitor:f.monitor,settings:defaults,WebSocket:f.Socket});
  f.sockets[0].disconnect();assert.equal(typeof dispatched,'function');
  publisher.close();dispatched();
  assert.equal(f.sockets.length,1);assert.equal(f.unsubscribed(),true);
});
