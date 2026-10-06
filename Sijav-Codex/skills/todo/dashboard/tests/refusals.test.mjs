// Canonical startup and protocol refusals. Every child runs the source file
// itself and keeps its own native V8 producer data when measurement is enabled.
import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { writeFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { connect } from 'node:net';
import { startDashboard, nodeSupported } from '../server.mjs';
import { STAGE, TODO_PY, richProject, loopProject, tempDir, cleanup, testEnv, sha, until, coverageFilesOf } from './helpers.mjs';

test.after(cleanup);
const measured = (pid, label) => {
  const files = coverageFilesOf(pid);
  if (files !== null) assert.ok(files.length, `${label}, pid ${pid}: native coverage data was lost`);
};
const cli = (file, args, { executable = process.execPath, preload = [], cwd = tempDir('canonical refusal '), env = testEnv() } = {}) => {
  const result = spawnSync(executable, [...preload, join(STAGE, file), ...args], { cwd, env, encoding: 'utf8', timeout: 30000 });
  assert.equal(result.error, undefined, String(result.error));
  assert.equal(result.signal, null, result.stderr);
  measured(result.pid, file);
  return result;
};
const refused = (args, pattern, options) => {
  const result = cli('server.mjs', args, options);
  assert.equal(result.status, 2, result.stdout + result.stderr);
  assert.match(result.stderr, pattern);
  assert.doesNotMatch(result.stdout, /^URL:/m, 'a refusal never advertises a running dashboard');
  return result;
};

test('canonical CLI help, unknown and missing options, bad ports and non-loopback hosts', () => {
  const help = cli('server.mjs', ['--help']);
  assert.equal(help.status, 0); assert.match(help.stdout, /--publish-token-file/);
  for (const [args, pattern] of [
    [['--unknown'], /Unknown option/], [['--port'], /--port needs a value/],
    [['--port='], /--port needs a value/], [['--port', '-1'], /whole number/],
    [['--port', '1.5'], /whole number/], [['--port', '65536'], /whole number/],
    [['--host', '0.0.0.0'], /loopback address/],
  ]) refused(args, pattern);
});

test('the declared Node range includes the 22.16 boundary and excludes 22.15 and the 23 line', () => {
  assert.equal(nodeSupported('22.15.1'), false);
  assert.equal(nodeSupported('22.16.0'), true);
  assert.equal(nodeSupported('23.11.0'), false);
  assert.equal(nodeSupported('24.0.0'), true);
  assert.equal(nodeSupported('21.7.3'), false);
});

const oldNode = process.env.SIJAV_TODO_UNSUPPORTED_NODE;
test('a real native Node below the floor refuses canonical server startup with exit 2', {
  skip: !oldNode || !existsSync(oldNode) ? 'set SIJAV_TODO_UNSUPPORTED_NODE to a Node below the floor (22.15.1) to run this' : false,
}, () => {
  const result = refused(['--help'], /^Node 22\.15\.1 is not supported: the dashboard needs Node 22\.16\+ \(22\.x\) or 24\+\.$/m,
    { executable: oldNode });
  const lines = result.stderr.split(/\r?\n/).filter(line => line && !/ExperimentalWarning|Use .*--trace-warnings/.test(line));
  assert.equal(lines.length, 1, 'apart from native SQLite warnings, only the declared refusal is printed');
  assert.equal(result.stdout, '');
});

test('missing ws is refused through normal Node loader resolution before watchers or a URL', () => {
  const p = richProject('missing dependency refusal'), before = sha(p.db);
  const hook = join(tempDir('normal loader hook '), 'no-ws.mjs');
  writeFileSync(hook, `import { registerHooks } from 'node:module';
registerHooks({resolve(specifier, context, nextResolve) {
  if (specifier === 'ws') throw Object.assign(new Error('synthetic absent dependency'), {code:'ERR_MODULE_NOT_FOUND'});
  return nextResolve(specifier, context);
}});\n`);
  const result = refused(['--db', p.db], /"ws" package is not installed/, { preload: ['--import', pathToFileURL(hook).href] });
  assert.match(result.stderr, /npm ci --omit=dev --prefix/);
  assert.equal(sha(p.db), before);
});

test('canonical startup refuses one tool for multiple loop boards and an occupied port', async () => {
  const first = loopProject('one loop'), second = loopProject('another loop');
  refused(['--db', first.db, '--db', second.db, '--tool', first.tool], /--tool.*several|--tool.*one loop/i);
  const p = richProject('port occupied');
  const live = await startDashboard({ db: p.db, todoPy: TODO_PY }, { env: testEnv() });
  try { refused(['--db', p.db, '--port', String(live.port)], /Port .* is in use/); }
  finally { await live.close(); }
});

test('a startup failure that is not a refusal is reported as one the dashboard could not get past, exit 1', () => {
  const p = richProject('cli data dir under a file'), dir = tempDir('cli data dir obstruction '), file = join(dir, 'a file');
  writeFileSync(file, 'an owned file where a folder is asked for\n');
  const result = cli('server.mjs', ['--db', p.db, '--data-dir', join(file, 'history')]);
  assert.equal(result.status, 1, result.stdout + result.stderr);
  assert.match(result.stderr, /^The dashboard could not start: /m);
  assert.doesNotMatch(result.stdout, /^URL:/m);
});

test('canonical publish refusals cover invalid URLs, schemes and every token-file failure', () => {
  const p = richProject('relay refusal'), dir = tempDir('relay credentials ');
  const token = join(dir, 'synthetic-token.txt'), empty = join(dir, 'empty.txt');
  writeFileSync(token, 'synthetic-local-token\n'); writeFileSync(empty, ' \n');
  for (const [options, pattern] of [
    [['--publish', 'not a URL'], /not a URL/],
    [['--publish', 'http://relay.example/x', '--publish-token-file', token], /needs a wss:/],
    [['--publish', 'ws://relay.example/x', '--publish-token-file', token], /needs a wss:/],
    [['--publish', 'wss://relay.example/x'], /--publish-token-file/],
    [['--publish', 'wss://relay.example/x', '--publish-token-file', join(dir, 'missing.txt')], /cannot be read/],
    [['--publish', 'wss://relay.example/x', '--publish-token-file', empty], /is empty/],
  ]) refused(['--db', p.db, ...options], pattern);
});

test('the canonical demo refuses a real board override and malformed options without making a fixture', () => {
  const missing = join(tempDir('demo refusal '), 'must-not-be-created.db');
  for (const [args, pattern] of [[['--db', missing], /only ever serves its own disposable fixture/], [['--unknown'], /Unknown option/]]) {
    const result = cli('tests/demo-fixture.mjs', args);
    assert.equal(result.status, 2, result.stdout + result.stderr);
    assert.match(result.stderr, pattern); assert.equal(result.stdout, '');
  }
  assert.equal(existsSync(missing), false);
});

const upgrade = (port, { method = 'GET', path = '/api/live', host = `127.0.0.1:${port}`, origin = `http://${host}`, site = 'same-origin' } = {}) => new Promise((ok, fail) => {
  const socket = connect({ host: '127.0.0.1', port });
  let text = '';
  socket.setTimeout(5000, () => socket.destroy(new Error('upgrade refusal timed out')));
  socket.on('error', fail); socket.on('data', bytes => { text += bytes; }); socket.on('close', () => ok(text));
  socket.on('connect', () => socket.write(`${method} ${path} HTTP/1.1\r\nHost: ${host}\r\nConnection: Upgrade\r\nUpgrade: websocket\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n${origin ? `Origin: ${origin}\r\n` : ''}${site ? `Sec-Fetch-Site: ${site}\r\n` : ''}\r\n`));
});

test('WebSocket upgrades refuse the wrong path, host, origin, site and method while the board stays readable', async () => {
  const p = richProject('upgrade refusal'), before = sha(p.db);
  const live = await startDashboard({ db: p.db, todoPy: TODO_PY }, { env: testEnv() });
  try {
    for (const options of [{ path: '/wrong' }, { host: 'foreign.example' }, { origin: 'https://foreign.example' }, { site: 'cross-site' }, { method: 'POST' }]) {
      assert.match(await upgrade(live.port, options), /^HTTP\/1\.1 403 /);
    }
    const health = await fetch(live.url + 'api/health');
    assert.equal(health.status, 200); assert.equal((await health.json()).readOnly, true);
    const beforeCheck = (await live.monitor.settled()).boards[0].queue;
    const checked = await (await fetch(live.url + 'api/queue/recheck')).json();
    assert.equal(checked.boards[0].queue.state, 'ready');
    assert.deepEqual(checked.boards[0].queue.rankedIds, beforeCheck.rankedIds);
    await until(() => live.monitor.snapshot().boards[0].queue.state === 'ready', 'the canonical picker');
  } finally { await live.close(); }
  assert.equal(sha(p.db), before);
});
// The unmodified public API runs in a real owned child. The watch wrapper
// delegates to native fs.watch and observes actual close events; it never
// substitutes a watcher or forces the child to exit.
async function acquiredPrefixChild(config) {
  const assert=(await import('node:assert/strict')).default;
  const fs=(await import('node:fs')).default;
  const {syncBuiltinESMExports,registerHooks}=await import('node:module');
  const nativeWatch=fs.watch,watchers=[];
  fs.watch=function(...args) {
    const watcher=nativeWatch.apply(this,args);
    const record={path:String(args[0]),closed:false};
    record.done=new Promise(resolve=>watcher.once('close',()=>{record.closed=true;resolve();}));
    watchers.push(record);return watcher;
  };
  syncBuiltinESMExports();
  globalThis.startupCloseAttempts=[];
  if(config.secondaryCleanup) {
    // Labelled public-module cleanup contract: real monitor/watcher closure,
    // followed by one deliberately thrown secondary close error.
    const moduleText='export * from '+JSON.stringify(config.boardUrl)+';\n'
      +'import {createMonitor as original} from '+JSON.stringify(config.boardUrl)+';\n'
      +'export function createMonitor(options){const owned=original(options),close=owned.close.bind(owned);'
      +'owned.close=()=>{globalThis.startupCloseAttempts.push(options.sourceId);close();'
      +'if(options.sourceId==="board")throw Error("labelled secondary cleanup error");};return owned;}';
    registerHooks({resolve(specifier,context,next) {
      if(context.parentURL===config.serverUrl&&specifier==='./lib/board.mjs')
        return {url:'data:text/javascript,'+encodeURIComponent(moduleText),shortCircuit:true};
      return next(specifier,context);
    }});
  }
  let expected;
  try {fs.mkdirSync(config.obstruction,{recursive:true});}
  catch(error) {expected={code:error.code,message:error.message};}
  assert.ok(expected,'the owned regular file really refuses directory creation');
  const {startDashboard}=await import(config.serverUrl);
  let rejected;
  try {
    const unexpected=await startDashboard({dbs:config.dbs,dataDir:config.dataDir,todoPy:config.missingTool},{cwd:config.cwd,env:process.env});
    await unexpected.close();
  } catch(error) {rejected={code:error.code,message:error.message};}
  assert.deepEqual(rejected,expected,'cleanup preserves the original native directory refusal exactly');
  assert.ok(watchers.length,'at least one real native watcher was acquired before the later failure');
  assert.ok(watchers.some(w=>w.path===config.firstDirectory),'the first board directory was actually watched');
  console.log(JSON.stringify({type:'startup-refusal',error:rejected,nativeWatchers:watchers.length,labelledSecondaryCleanup:config.secondaryCleanup}));
  await Promise.all(watchers.map(w=>w.done));
  if(config.secondaryCleanup)assert.deepEqual(globalThis.startupCloseAttempts,['board','board-2'],'every acquired monitor gets its close attempt after a secondary failure');
  console.log(JSON.stringify({type:'naturally-settled',closed:watchers.every(w=>w.closed),closeAttempts:globalThis.startupCloseAttempts}));
  process.exitCode=0; // natural settlement; process.exit would conceal leaked handles
}
// Labelled public-module cleanup contract, in a real owned child: the pinned ws is loaded unchanged and
// only its server's close() is made to throw, so a real port-in-use refusal meets a failing server
// cleanup. The startup's own refusal must be the one reported.
async function serverCleanupChild(config) {
  const assert=(await import('node:assert/strict')).default;
  const {registerHooks}=await import('node:module');
  const moduleText='import * as real from '+JSON.stringify(config.wsUrl)+';\n'
    +'export * from '+JSON.stringify(config.wsUrl)+';\nexport default real.default;\n'
    +'export class WebSocketServer extends real.WebSocketServer { close() { throw new Error("labelled secondary server cleanup error"); } }\n';
  registerHooks({resolve(specifier,context,next) {
    if(specifier==='ws'&&context.parentURL===config.serverUrl)return {url:'data:text/javascript,'+encodeURIComponent(moduleText),shortCircuit:true};
    return next(specifier,context);
  }});
  const {startDashboard}=await import(config.serverUrl);
  let rejected;
  try {const unexpected=await startDashboard({db:config.db,todoPy:config.todoPy,port:config.port},{cwd:config.cwd,env:process.env});await unexpected.close();}
  catch(error) {rejected=error.message;}
  assert.match(rejected,/^Port \d+ is in use\./,'the port refusal is reported, not the cleanup error');
  console.log(JSON.stringify({type:'startup-refusal',error:rejected}));
  process.exitCode=0;
}
test('labelled secondary server cleanup failure: a real port-in-use refusal is still the error reported',async()=>{
  const p=richProject('server cleanup refusal'),before=sha(p.db);
  const holder=(await import('node:net')).createServer();
  await new Promise((ok,fail)=>{holder.once('error',fail);holder.listen(0,'127.0.0.1',ok);});
  try{
    const config={db:p.db,todoPy:TODO_PY,port:holder.address().port,cwd:p.root,serverUrl:pathToFileURL(join(STAGE,'server.mjs')).href,
      wsUrl:pathToFileURL(join(STAGE,'node_modules','ws','wrapper.mjs')).href};
    const code='('+serverCleanupChild.toString()+')(JSON.parse(process.argv[1])).catch(error=>{console.error(error.stack);process.exitCode=1;});';
    const result=spawnSync(process.execPath,['--input-type=module','--eval',code,JSON.stringify(config)],{cwd:p.root,env:testEnv(),encoding:'utf8',timeout:30000});
    assert.equal(result.error,undefined,String(result.error));assert.equal(result.status,0,result.stdout+result.stderr);
    measured(result.pid,'secondary server cleanup child');
    assert.equal(JSON.parse(result.stdout.trim().split(/\r?\n/).at(-1)).type,'startup-refusal');
  }finally{await new Promise(ok=>holder.close(ok));}
  assert.equal(sha(p.db),before);
});

for(const secondaryCleanup of [false,true])test(secondaryCleanup
  ?'labelled secondary cleanup failure preserves the startup error and closes the remaining real prefix'
  :'partial multi-board API startup closes its real acquired watcher and naturally settles',()=>{
  const projects=Array.from({length:secondaryCleanup?3:2},(_,i)=>richProject('owned startup board '+i));
  const dataDir=tempDir('owned startup history '),sourceId=secondaryCleanup?'board-3':'board-2';
  const obstruction=join(dataDir,sourceId);writeFileSync(obstruction,'owned directory obstruction\n');
  const originals=projects.map(p=>sha(p.db)),obstructionBefore=sha(obstruction);
  const config={dbs:projects.map(p=>p.db),dataDir,obstruction,secondaryCleanup,cwd:projects[0].root,
    firstDirectory:join(projects[0].root,'.claude'),missingTool:join(dataDir,'missing-tool.py'),
    serverUrl:pathToFileURL(join(STAGE,'server.mjs')).href,boardUrl:pathToFileURL(join(STAGE,'lib','board.mjs')).href};
  const code='('+acquiredPrefixChild.toString()+')(JSON.parse(process.argv[1])).catch(error=>{console.error(error.stack);process.exitCode=1;});';
  const result=spawnSync(process.execPath,['--input-type=module','--eval',code,JSON.stringify(config)],
    {cwd:projects[0].root,env:testEnv(),encoding:'utf8',timeout:30000});
  assert.equal(result.error,undefined,'natural-settlement watchdog: '+String(result.error)+'\n'+result.stdout+result.stderr);
  assert.equal(result.signal,null,result.stdout+result.stderr);assert.equal(result.status,0,result.stdout+result.stderr);
  measured(result.pid,'public startup acquired-prefix child');
  const records=result.stdout.trim().split(/\r?\n/).map(line=>JSON.parse(line));
  assert.equal(records[0].type,'startup-refusal');assert.equal(records[0].labelledSecondaryCleanup,secondaryCleanup);
  assert.equal(records.at(-1).type,'naturally-settled');assert.equal(records.at(-1).closed,true);
  assert.ok(records[0].error.message.includes(obstruction));
  projects.forEach((p,i)=>assert.equal(sha(p.db),originals[i]));
  assert.equal(sha(obstruction),obstructionBefore,'an owned obstruction is never replaced or removed to make startup pass');
});
