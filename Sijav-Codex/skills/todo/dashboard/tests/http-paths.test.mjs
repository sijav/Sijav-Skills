// Native HTTP boundaries and optional pagination fields; all fixtures are owned here.
import test from 'node:test';
import assert from 'node:assert/strict';
import { symlinkSync } from 'node:fs';
import { EventEmitter } from 'node:events';
import { connect } from 'node:net';
import { join } from 'node:path';
import { WebSocket, WebSocketServer } from 'ws';
import { createApp, callerDirectory, isEntry } from '../server.mjs';
import { createMonitor, defaults } from '../lib/board.mjs';
import { STAGE, richProject, tempDir, cleanup, sha, until } from './helpers.mjs';
test.after(cleanup);
test('GET/HEAD, history integer parsing, the snapshot ETag, not-found paths and the manual recheck are exact',async()=>{
  const p=richProject('native HTTP boundaries'),before=sha(p.db),monitor=createMonitor({dbPath:p.db,projectRoot:p.root,dataDir:tempDir('HTTP history ')});
  const server=createApp(monitor,{WebSocket,WebSocketServer});
  await new Promise((ok,err)=>{server.once('error',err);server.listen(0,'127.0.0.1',ok);});
  const base='http://127.0.0.1:'+server.address().port+'/';
  try{
    for(const path of ['api/health','api/snapshot','api/export','app.js','favicon.svg','']){
      const response=await fetch(base+path,{method:'HEAD'});assert.equal(response.status,200,path);assert.equal(await response.text(),'');
    }
    for(const query of ['','?before=1&after=0&limit=1','?before=bad&after=&limit=all','?item=absent','?item=absent&limit=bad']){
      const body=await(await fetch(base+'api/changes'+query)).json();
      assert.equal(body.total,0);assert.deepEqual(body.changes,[]);assert.equal(body.complete,true);
    }
    const snap=await fetch(base+'api/snapshot'),etag=snap.headers.get('etag');
    assert.equal((await fetch(base+'api/snapshot',{headers:{'If-None-Match':etag}})).status,304);
    assert.equal((await fetch(base+'missing')).status,404);
    assert.equal((await fetch(base+'api/queue/recheck')).status,200);
  }finally{monitor.close();await new Promise(ok=>server.close(ok));}
  assert.equal(sha(p.db),before);
});

/** One raw HTTP request through Node's own parser, as any local client could send it; resolves with the whole reply text. */
const raw=(port,lines)=>new Promise((ok,fail)=>{
  const socket=connect({host:'127.0.0.1',port});let text='';
  socket.setTimeout(5000,()=>socket.destroy(new Error('raw request timed out')));
  socket.on('error',fail);socket.on('data',bytes=>{text+=bytes;});socket.on('close',()=>ok(text));
  socket.on('connect',()=>socket.write(lines.join('\r\n')+'\r\n\r\n'));
});

test('requests from a foreign host, a cross-site origin or a cross-site fetch are refused, and the board stays served',async()=>{
  const p=richProject('foreign request refusals'),before=sha(p.db),monitor=createMonitor({dbPath:p.db,projectRoot:p.root,dataDir:tempDir('foreign request history ')});
  const server=createApp(monitor,{WebSocket,WebSocketServer});
  await new Promise((ok,err)=>{server.once('error',err);server.listen(0,'127.0.0.1',ok);});
  const port=server.address().port,host=`Host: 127.0.0.1:${port}`;
  try{
    for(const [headers,said] of [
      [['Host: foreign.example'],/^HTTP\/1\.1 403 [\s\S]*Local host only\./],
      [[host,'Origin: http://foreign.example'],/^HTTP\/1\.1 403 [\s\S]*Same-origin access only\./],
      [[host,'Sec-Fetch-Site: cross-site'],/^HTTP\/1\.1 403 [\s\S]*Same-origin access only\./],
    ])assert.match(await raw(port,['GET /api/health HTTP/1.1',...headers,'Connection: close']),said,headers.join(' · '));
    assert.equal((await fetch(`http://127.0.0.1:${port}/api/health`)).status,200,'the dashboard still answers');
  }finally{monitor.close();await new Promise(ok=>server.close(ok));}
  assert.equal(sha(p.db),before);
});

// RE-321. A request target URL cannot parse is answered, never thrown out of the request or upgrade
// listener. Whether Node's HTTP parser lets each target through to the dashboard is Node's own
// behaviour: one it refuses itself is answered 400 by Node, and the dashboard's catch then has no
// producer here; that stays a reported residual, never a passed claim.
test('request targets URL cannot parse are answered without ending the dashboard, over plain HTTP and the upgrade',async()=>{
  const p=richProject('unparsable request targets'),before=sha(p.db),monitor=createMonitor({dbPath:p.db,projectRoot:p.root,dataDir:tempDir('unparsable target history ')});
  const server=createApp(monitor,{WebSocket,WebSocketServer});
  await new Promise((ok,err)=>{server.once('error',err);server.listen(0,'127.0.0.1',ok);});
  const port=server.address().port,host=`127.0.0.1:${port}`;
  try{
    for(const target of ['http://x:99999/','http://[','//[']){
      const plain=await raw(port,[`GET ${target} HTTP/1.1`,`Host: ${host}`,'Connection: close']);
      assert.match(plain,/^HTTP\/1\.1 (500 [\s\S]*Dashboard error: |400 )/,'plain '+target+' is answered: '+plain.slice(0,120));
      const upgraded=await raw(port,[`GET ${target} HTTP/1.1`,`Host: ${host}`,'Connection: Upgrade','Upgrade: websocket','Sec-WebSocket-Version: 13',
        'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==',`Origin: http://${host}`]);
      assert.match(upgraded,/^HTTP\/1\.1 (403|400) /,'upgrade '+target+' is refused: '+upgraded.slice(0,120));
      const health=await fetch(`http://${host}/api/health`);
      assert.equal(health.status,200,'after '+target+' the dashboard still serves');
    }
  }finally{monitor.close();await new Promise(ok=>server.close(ok));}
  assert.equal(sha(p.db),before);
});

test('the canonical npm caller and native directory alias identify this package only',()=>{
  const p=richProject('canonical npm caller'),elsewhere=tempDir('foreign npm package ');
  const ours={npm_lifecycle_event:'start',INIT_CWD:p.sub,npm_package_json:join(STAGE,'package.json')};
  assert.equal(callerDirectory(ours,STAGE),p.sub);
  assert.equal(callerDirectory({...ours,npm_package_json:join(elsewhere,'package.json')},STAGE),STAGE);
  assert.equal(callerDirectory(ours,elsewhere),elsewhere);
  assert.equal(callerDirectory({npm_lifecycle_event:'start',INIT_CWD:p.sub},STAGE),STAGE);
  assert.equal(callerDirectory({},p.sub),p.sub);
  assert.equal(isEntry(undefined),false);
  assert.equal(isEntry(join(elsewhere,'server.mjs')),false,'a missing foreign entry is resolved without becoming this module');
  assert.equal(isEntry(join(STAGE,'lib','board.mjs')),false);
  const dir=tempDir('canonical entry alias '),alias=join(dir,'dashboard');
  symlinkSync(STAGE,alias,process.platform==='win32'?'junction':'dir');
  assert.equal(isEntry(join(alias,'server.mjs')),true,'the native alias resolves to the canonical entry');
  assert.equal(callerDirectory({...ours,npm_package_json:join(alias,'package.json')},alias),p.sub);
});

test('actual local typed requests preserve IDs on malformed/unknown questions and convert row bounds',async()=>{
  const p=richProject('typed local request boundaries'),before=sha(p.db);
  const monitor=createMonitor({dbPath:p.db,projectRoot:p.root,dataDir:tempDir('typed request history ')});
  const server=createApp(monitor,{WebSocket,WebSocketServer});
  await new Promise((ok,fail)=>{server.once('error',fail);server.listen(0,'127.0.0.1',ok);});
  const origin='http://127.0.0.1:'+server.address().port,frames=[];
  const socket=new WebSocket(origin.replace('http','ws')+'/api/live',{headers:{Origin:origin}});
  socket.on('message',text=>frames.push(JSON.parse(String(text))));
  try {
    await until(()=>frames.some(f=>f.type==='hello'),'actual typed socket greeting');
    const ask=async(text,id)=>{
      const at=frames.length;socket.send(text);
      return until(()=>frames.slice(at).find(f=>f.type==='reply'&&f.id===id),'matching typed reply');
    };
    assert.match((await ask('{',null)).error,/Unknown request/);
    assert.match((await ask(JSON.stringify({id:'unknown',type:'unassigned'}),'unknown')).error,/Unknown request "unassigned"/);
    assert.match((await ask(JSON.stringify({id:'list',type:'list',list:'unassigned'}),'list')).error,/Unknown list/);
    const rowsReply=await ask(JSON.stringify({id:'rows',type:'rows',board:'board',table:'task',offset:'2',limit:'1'}),'rows');
    assert.equal(rowsReply.data.offset,2);assert.equal(rowsReply.data.rows.length,1);assert.equal(rowsReply.error,undefined);
    assert.equal((await ask(JSON.stringify({id:'gone',type:'task',key:'board:gone'}),'gone')).data,null);
    assert.equal((await fetch(origin+'/styles.css',{headers:{'Sec-Fetch-Site':'none'}})).status,200);
  } finally {socket.terminate();monitor.close();await new Promise(ok=>server.close(ok));}
  assert.equal(sha(p.db),before);
});

test('labelled WebSocket transport contracts refuse writes to closed/backpressured clients',async()=>{
  const p=richProject('controlled socket write boundary'),before=sha(p.db),events=[];
  const monitor=createMonitor({dbPath:p.db,projectRoot:p.root,dataDir:tempDir('controlled socket history ')});
  class ControlledSocket extends EventEmitter {
    constructor(state,buffer=0){super();this.readyState=state;this.bufferedAmount=buffer;this.messages=[];this.terminated=0;}
    send(value){this.messages.push(JSON.parse(value));}
    terminate(){this.terminated++;this.readyState=3;}
  }
  let wss;
  class ControlledServer extends EventEmitter {
    constructor(){super();this.clients=new Set();wss=this;}
    close(){events.push('server closed');}
  }
  const settings=structuredClone(defaults);settings.transport.maxBufferedBytes=1;
  const server=createApp(monitor,{WebSocket:{OPEN:1},WebSocketServer:ControlledServer},settings);
  try {
    const closed=new ControlledSocket(3);wss.clients.add(closed);wss.emit('connection',closed);
    assert.deepEqual(closed.messages,[]);assert.equal(closed.terminated,0);
    const full=new ControlledSocket(1,2);wss.clients.add(full);wss.emit('connection',full);
    assert.equal(full.terminated,1);assert.deepEqual(full.messages,[]);
    const open=new ControlledSocket(1);wss.clients.add(open);wss.emit('connection',open);
    assert.equal(open.messages[0].type,'hello');
    open.emit('message',JSON.stringify({id:0,type:'unassigned'}));
    assert.equal(open.messages.at(-1).id,0,'an integer zero request ID is preserved');
    assert.match(open.messages.at(-1).error,/Unknown request/);
    closed.emit('message','{');assert.deepEqual(closed.messages,[],'even an error reply cannot write to a closed client');
  } finally {monitor.close();await new Promise(ok=>server.close(()=>ok()));}
  assert.equal(events.includes('server closed'),true);assert.equal(sha(p.db),before);
});
