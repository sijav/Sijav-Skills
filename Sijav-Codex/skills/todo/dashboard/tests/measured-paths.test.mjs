// Causal paths use todo.py/loop-tool boards, owned legacy SQL (including nullable loop columns),
// real monitors, canonical pickers and owned native file writers. Native measurement alone does not
// establish an input's production provenance. Explicit caller settings without activityLimit exercise
// the public monitor fallback; selected Python -c children are protocol-negative producers, not the
// canonical picker. Owned-element DOM capability denials and watch/EMFILE doubles are labelled unit
// evidence; they do not prove a native browser denial or a native watcher event.
import test from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { EventEmitter } from 'node:events';
import { writeFileSync, readFileSync, mkdirSync, existsSync, copyFileSync, renameSync, rmSync } from 'node:fs';
import { join, parse, dirname } from 'node:path';
import { homedir } from 'node:os';
import { Worker } from 'node:worker_threads';
import * as board from '../lib/board.mjs';
import * as views from '../lib/views.mjs';
import * as ui from '../public/board-ui.mjs';
import { buildAmbientModel, renderAmbient, renderAmbientModel } from '../public/ambient-ui.mjs';
import { createDisplayController } from '../public/display-mode.mjs';
import { richProject, loopProject, tempDir, cleanup, todo, TODO_PY, PYTHON, sha, until, nativeBridge } from './helpers.mjs';

test.after(cleanup);
const python={command:PYTHON,args:[]};
/** A real monitor of a board, its picker run to its first result. */
async function monitored(p,options={}){
  const monitor=board.createMonitor({dbPath:p.db,projectRoot:p.root,dataDir:tempDir('measured history '),...options});
  return {monitor,snapshot:await monitor.settled()};
}
/** A copy of todo.py in its own folder with one edit, as a project can change its own tool. */
function toolCopy(edit){
  const path=join(tempDir('measured tool copy '),'todo.py'),source=readFileSync(TODO_PY,'utf8'),changed=edit(source);
  assert.notEqual(changed,source,'the edit applied');
  writeFileSync(path,changed);
  return path;
}
test('picker order, lanes, testing states and diffs on real monitor snapshots of a picked, a failed and a tested board',async()=>{
  const p=richProject('measured board order');
  todo(p.root,'tested','MP-010','--evidence','Ran the fixture suite');
  const {monitor,snapshot}=await monitored(p,{python,todo:TODO_PY});
  try{
    const b=snapshot.boards[0];
    assert.equal(b.queue.state,'ready');assert.equal(ui.hasPickerOrder(b),true);
    const backlog=b.tasks.filter(t=>t.status==='backlog');
    assert.deepEqual(ui.sortLane(backlog,'backlog',b).map(t=>t.id),b.queue.rankedIds.filter(id=>backlog.some(t=>t.id===id)),'a lane in the tool’s order');
    assert.equal(ui.nextTask(b).id,b.queue.headId);assert.equal(ui.firstOpen(b).id,b.queue.eligibleIds[0]);
    const done=b.tasks.filter(t=>t.status==='done');
    assert.deepEqual(ui.sortLane(done,'done',b).map(t=>t.id),['MP-010','MP-006'],'the most recent completion first');
    assert.equal(ui.supportsTesting(b,'tested'),true,'todo tested gave the board its test-state columns');
    const summary=ui.testingSummary(b,b.tasks,board.defaults.ui.testingFlags).flags.find(f=>f.key==='tested');
    assert.deepEqual([summary.passed,summary.pending],[1,1],'one done task tested, the other not');
    assert.equal(ui.matchesTestingFilter(b.tasks.find(t=>t.id==='MP-010'),'tested:passed'),true);
  }finally{monitor.close();}
  // The same board read with a tool its picker cannot load: no order, every lane by id.
  const broken=toolCopy(s=>s+'\ndef deliberately_invalid(:\n');
  const failed=await monitored(p,{python,todo:broken});
  try{
    const b=failed.snapshot.boards[0];
    assert.equal(b.queue.state,'error');assert.equal(ui.hasPickerOrder(b),false);assert.equal(ui.pickerOrder(b),null);
    const backlog=b.tasks.filter(t=>t.status==='backlog');
    assert.deepEqual(ui.sortLane(backlog,'backlog',b).map(t=>t.id),backlog.map(t=>t.id).sort((x,y)=>x.localeCompare(y,undefined,{numeric:true})),'id order, which is not the next order');
  }finally{failed.monitor.close();}
  // A diff of two real reads: a BLOB, an infinity and a dated column, as plain() gives them.
  const writer=new DatabaseSync(p.db);
  try{writer.exec("CREATE TABLE extra(id INTEGER PRIMARY KEY,payload BLOB,limit_value REAL,created TEXT);INSERT INTO extra VALUES(1,x'00ff',9e999,NULL);");}finally{writer.close();}
  const first=board.readBoard(p.db);
  const second=new DatabaseSync(p.db);
  try{second.exec("UPDATE extra SET payload=x'0100',limit_value=-9e999,created='2026-10-01 01:00:00' WHERE id=1;");}finally{second.close();}
  const change=board.diffBoard(first,board.readBoard(p.db)).find(c=>c.table==='extra');
  assert.deepEqual(change.fields.sort(),['created','limit_value','payload']);
  assert.match(ui.renderFieldDiff(change.before,change.after,{...board.defaults.ui,diffMaxEdits:0,diffContextLines:1}),/diff-add/);
  for(const stored of ['','1700000000','1700000000123','2026-10-01T01:00:00+01:00','not a date'])assert.equal(typeof ui.formatDate(stored),'string');
  assert.match(ui.clampText('abcdefghijklmnop','x',null,{chars:4,toggle:false}),/^abcd…$/);
  assert.match(ui.clampText('abcdefghijklmnop','x',new Set(['x']),{toggle:false}),/^abcdefghijklmnop$/);
});
test('server query filters, every register sort, a link to a task not on the board and the totals, on a real monitor snapshot',async()=>{
  // An owned legacy board, written by SQL as an older tool or a hand edit leaves one: free-text type and tags
  // columns, a blocker and an origin that are not on the board, and a note and a roast with numeric times.
  const root=join(tempDir('measured legacy filters '),'legacy filters'),path=join(root,'.claude','todo.db');
  mkdirSync(dirname(path),{recursive:true});
  const db=new DatabaseSync(path);
  try{db.exec(`CREATE TABLE task(id TEXT PRIMARY KEY,title TEXT,status TEXT,severity TEXT,points INTEGER,area TEXT,type TEXT,tags TEXT,phase TEXT,parent_task TEXT,
      created TEXT,closed TEXT,tested INTEGER);
    CREATE TABLE blocked_by(task TEXT,parent TEXT);CREATE TABLE blocked(task TEXT PRIMARY KEY,reason TEXT,since TEXT);
    CREATE TABLE note(task TEXT,at,text TEXT);CREATE TABLE roast(task TEXT,round INTEGER,at,file TEXT);CREATE TABLE phase(name TEXT PRIMARY KEY,goal TEXT,position INTEGER,status TEXT);
    INSERT INTO phase VALUES('P','a phase',1,'open');
    INSERT INTO task VALUES('x1','First','backlog','high',1,'API','Ops','urgent',NULL,'gone','2026-10-01 00:00:00',NULL,0),
      ('y1','Second','backlog','low',1,NULL,NULL,NULL,'P',NULL,'2026-10-02 01:00:00',NULL,0),
      ('x2','Third','done','low',1,NULL,NULL,NULL,NULL,NULL,'2026-09-30 00:00:00','2026-10-01 12:00:00',1);
    INSERT INTO blocked_by VALUES('x1','gone');INSERT INTO blocked VALUES('y1','','bad');
    INSERT INTO note VALUES('x1',1700000000,NULL);INSERT INTO roast VALUES('x1',1,1700000000123,'r.md');`);}finally{db.close();}
  const monitor=board.createMonitor({dbPath:path,projectRoot:root,dataDir:tempDir('measured filter history ')});
  try{
    const snapshot=monitor.snapshot();
    for(const sort of ['next','recent','severity','id'])assert.equal(views.list(snapshot,{list:'register',filters:{sort}}).total,3,sort);
    for(const [filters,count] of [[{area:'API'},1],[{area:'missing'},2],[{area:'absent'},0],[{type:'Ops'},1],[{topic:'urgent'},1],[{phase:'none'},2],[{phase:'P'},1],
      [{status:'done'},1],[{severity:'high'},1],[{verification:'tested:passed'},1],[{finishedOnly:true},0],[{blocked:true},1],[{query:'gone'},1]])
      assert.equal(views.list(snapshot,{list:'register',filters}).total,count,JSON.stringify(filters));
    assert.equal(views.list(snapshot,{list:'doing',offset:-2,limit:0}).total,0);
    assert.equal(views.list(snapshot,{list:'register',board:'missing'}).total,0);
    assert.equal(views.record(snapshot,'board:x1').links.gone.missing,true,'a link to a task that is not on the board says so');
    const summary=views.summary(snapshot,{},{activityLimit:5,testingFlags:board.defaults.ui.testingFlags});
    assert.equal(summary.severityBase,3,'without the tool’s policy every task counts toward severity');
    assert.ok(summary.events.length>=3,'the stored creation, closure and numeric note times are dated');
  }finally{monitor.close();}
});
test('Relax from real monitor states: checking, failed, unconfigured, unreadable, resumed with nothing else eligible, and a loop board',async()=>{
  const p=richProject('measured relax states');
  // Checking: the first snapshot, taken before the real picker has answered.
  const checking=board.createMonitor({dbPath:p.db,projectRoot:p.root,dataDir:tempDir('relax checking history '),python,todo:TODO_PY});
  try{
    const model=buildAmbientModel(checking.snapshot());
    assert.equal(model.boards[0].queue.state,'checking');assert.equal(model.boards[0].head,null);
    assert.match(renderAmbientModel(model,{connected:true,paused:true}),/Live connection.*View paused/);
    await checking.settled();
  }finally{checking.close();}
  // Failed: a tool its picker cannot load. Unconfigured: no tool at all.
  const failed=await monitored(p,{python,todo:toolCopy(s=>s+'\ndef deliberately_invalid(:\n')});
  try{
    const model=buildAmbientModel(failed.snapshot);
    assert.equal(model.boards[0].queue.state,'error');assert.equal(model.boards[0].head,null);
    assert.match(renderAmbient(failed.snapshot),/The tool’s picker failed/);
  }finally{failed.monitor.close();}
  const unconfigured=await monitored(p,{pickerReason:'No Python 3.9+ was found.'});
  try{
    const model=buildAmbientModel(unconfigured.snapshot);
    assert.equal(model.boards[0].queue.state,'unconfigured');assert.equal(model.boards[0].counts.doing,null);
    assert.match(renderAmbient(unconfigured.snapshot),/Which statuses mean Doing is unknown/);
  }finally{unconfigured.monitor.close();}
  // Unreadable after a good read: the board moved away under a running monitor.
  const moved=await monitored(p,{python,todo:TODO_PY});
  try{
    renameSync(p.db,p.db+'.aside');moved.monitor.readNow();
    const model=buildAmbientModel(moved.monitor.snapshot());
    assert.equal(model.boards[0].available,false);assert.equal(model.boards[0].head,null);
    assert.match(renderAmbient(moved.monitor.snapshot()),/Read failed · last successful data/);
  }finally{moved.monitor.close();renameSync(p.db+'.aside',p.db);}
  // Resumed with nothing else eligible: one task started, every other unfinished task blocked or waiting.
  const q=richProject('measured resume only');
  todo(q.root,'move','MP-004','in_progress');
  for(const id of ['MP-001','MP-002','MP-007','MP-008','MP-1000'])todo(q.root,'move',id,'blocked','--reason','held for this check');
  const resumed=await monitored(q,{python,todo:TODO_PY});
  try{
    const shown=buildAmbientModel(resumed.snapshot).boards[0];
    assert.equal(shown.headRole,'resume');assert.equal(shown.head.id,'MP-004');assert.equal(shown.firstOpen,null);
    assert.match(renderAmbient(resumed.snapshot),/No eligible not-started task in this result/);
  }finally{resumed.monitor.close();}
  const loop=loopProject('measured relax loop');
  const loopMonitor=await monitored(loop,{python,todo:loop.tool,kind:'loop'});
  try{assert.match(renderAmbient(loopMonitor.snapshot),/board\.py’s board_order\(\)/);}
  finally{loopMonitor.monitor.close();}
});
test('display visibility, release, thrown and rejected native capability boundaries preserve session ownership',async()=>{
  const element={},document={visibilityState:'hidden',fullscreenElement:null,fullscreenEnabled:false};
  let requested=0,released=0,releaseEvent;
  const lock={release(){released++;throw Error('release rejected');},addEventListener(type,fn){releaseEvent=fn;}};
  const c=createDisplayController({element,document,wakeLock:{request(){requested++;return Promise.resolve(lock);}}});
  await c.enter();assert.equal(requested,0);
  c.visibilityChanged();assert.equal(requested,0);
  document.visibilityState='visible';c.visibilityChanged();await Promise.resolve();await Promise.resolve();
  assert.equal(requested,1);releaseEvent();c.visibilityChanged();await Promise.resolve();await Promise.resolve();
  await c.exit();await c.exit();assert.equal(c.active,false);
  // Labelled owned-element DOM unit producer, not an observed native browser denial.
  let ownedExitCalls=0;
  const ownedElement={requestFullscreen(){throw Error('controlled capability denial');}},
    ownedDocument={visibilityState:'visible',fullscreenElement:ownedElement,exitFullscreen(){ownedExitCalls++;throw Error('controlled exit denial');}};
  const throwing=createDisplayController({element:ownedElement,document:ownedDocument,wakeLock:{request(){throw Error('controlled wake denial');}}});
  await throwing.enter();await throwing.requestFullscreen();await throwing.exit();throwing.fullscreenChanged();
  assert.equal(ownedExitCalls,1,'the owned fullscreen exit was actually requested');assert.equal(throwing.active,false);
});
test('diffs of real reads across dropped and added keyless tables, a stored CHECK list, unknown policy and malformed history',()=>{
  // Two real reads of an owned board between which an older table was dropped, a keyless table with two
  // identical rows was added, and a task changed: what diffBoard is ever given.
  const p=richProject('measured legacy diff');
  const setup=new DatabaseSync(p.db);
  try{setup.exec("CREATE TABLE old_notes(title TEXT);INSERT INTO old_notes VALUES('legacy');");}finally{setup.close();}
  const first=board.readBoard(p.db);
  const change=new DatabaseSync(p.db);
  try{change.exec("DROP TABLE old_notes;CREATE TABLE extra(name TEXT CHECK(name IN ('don''t','open')));INSERT INTO extra VALUES('open'),('open');");}finally{change.close();}
  todo(p.root,'move','MP-004','in_progress');
  const second=board.readBoard(p.db),diffs=board.diffBoard(first,second);
  assert.ok(diffs.some(d=>d.table==='old_notes'&&d.kind==='removed'));
  assert.equal(diffs.filter(d=>d.table==='extra'&&d.kind==='added').length,2,'two identical keyless rows are two additions');
  assert.ok(diffs.some(d=>d.table==='schema'));assert.ok(diffs.some(d=>d.table==='task'&&d.itemId==='MP-004'));
  assert.deepEqual(board.checkedValues(second.schema.find(s=>s.name==='extra').sql,'name'),["don't",'open'],'the stored CHECK list, quotes undone');
  assert.deepEqual(board.checkedValues(second.schema.find(s=>s.name==='absent')?.sql,'status'),[],'a table the board does not have');
  assert.equal(board.kindOf({other:['x']}),null);
  const unknown=board.classify(second,board.rulesFrom(null,'no tool configured'));
  assert.equal(unknown.counts.open,null,'without the tool’s policy no count is claimed');
  const dir=tempDir('legacy history '),file=join(dir,'changes.jsonl');
  writeFileSync(file,'null\n{}\nnot json\n'+JSON.stringify({at:'2026-10-01'})+'\n');
  assert.deepEqual(board.loadJournal(file).bad,[1,2,3]);
  assert.equal(board.timestamp('bad'),null);assert.equal(board.timestamp(''),null);assert.equal(board.timestamp(1700000000123),'2023-11-14T22:13:20.123Z');
});
test('a minimal nullable board and schema changes are read by the canonical native SQLite reader',()=>{
  const root=tempDir('nullable native board '),path=join(root,'legacy.db');
  const db=new DatabaseSync(path);
  try{db.exec("CREATE TABLE task(id TEXT PRIMARY KEY,title TEXT,status TEXT,created_at TEXT,closed_at TEXT,updated_at TEXT);INSERT INTO task VALUES('1',NULL,'legacy',NULL,'',NULL);CREATE TABLE detail(task TEXT,payload TEXT);INSERT INTO detail VALUES('1',NULL);");}finally{db.close();}
  const before=sha(path),data=board.readBoard(path);
  assert.equal(data.tasks[0].severity,null);assert.equal(data.tasks[0].lastRecordedAt,null);assert.equal(data.tasks[0].related.detail.length,1);
  assert.equal(sha(path),before);
});
test('combined monitors expose readNow, recheck, settled and post-close boundaries on real boards',async()=>{
  const projects=[richProject('combined causal one'),richProject('combined causal two')];
  const monitors=projects.map((p,i)=>board.createMonitor({dbPath:p.db,projectRoot:p.root,dataDir:tempDir('combined causal history '),sourceId:'b'+i}));
  const combined=board.combineMonitors(monitors);
  try{
    combined.readNow();const snap=await combined.recheck();assert.equal(snap.boards.length,2);
    assert.equal((await combined.settled()).boards.length,2);assert.equal(combined.changes({item:'absent',before:2,after:0,limit:1}).length,0);
  }finally{combined.close();}
  assert.equal((await combined.recheck()).boards.length,2);
  combined.readNow();await combined.settled();
});

// Labelled `watch` boundary double (createMonitor's injectable option): unit evidence for how a monitor
// handles each kind of event, never native watcher evidence. It records the callbacks the monitor
// registers and hands back a watcher-shaped emitter; events are delivered by calling them.
function recordingWatch(){
  const calls=[];
  const watch=(folder,callback)=>{const watcher=Object.assign(new EventEmitter(),{close(){watcher.closed=true;}});calls.push({folder,callback,watcher});return watcher;};
  return {watch,calls};
}
test('watch-boundary unit evidence: an event without a filename, a housekeeping event, and events after close',async()=>{
  const p=richProject('measured watch double'),before=sha(p.db);
  const {watch,calls}=recordingWatch();
  const monitor=board.createMonitor({dbPath:p.db,projectRoot:p.root,dataDir:tempDir('watch double history '),python,todo:TODO_PY,watch});
  try{
    await monitor.settled();
    const [boardWatch,toolWatch]=calls;
    assert.equal(boardWatch.folder,dirname(p.db));assert.equal(toolWatch.folder,dirname(TODO_PY));
    assert.equal(monitor.snapshot().lastFileEventAt,null);
    // Node documents that an event's filename may be absent: such an event still counts as one on the board.
    const ignored=monitor.snapshot().ignoredEvents;
    boardWatch.callback('change',null);
    assert.ok(monitor.snapshot().lastFileEventAt,'an event without a filename is taken as the board’s');
    // Nothing changed the board, its WAL, SHM or journal: the event is housekeeping, counted and not read.
    await until(()=>monitor.snapshot().ignoredEvents>ignored,'the housekeeping event counted');
  }finally{monitor.close();}
  const closed=monitor.snapshot();
  calls[0].callback('change','todo.db');calls[1].callback('change','todo.py');
  // An error after close is ignored as an event is: what this observes is an unchanged watch state (a stray
  // timer would end in readNow's closed return, which these counts cannot see).
  for(const call of calls)call.watcher.emit('error',Object.assign(new Error('EPERM: labelled error after close'),{code:'EPERM'}));
  const after=monitor.snapshot();
  for(const key of ['lastFileEventAt','readCount','ignoredEvents'])assert.equal(after[key],closed[key],key+' is unchanged by an event after close');
  assert.deepEqual(after.watchState,closed.watchState,'an error after close changes no watch state');
  assert.equal(after.server.toolSha,closed.server.toolSha);
  assert.equal(sha(p.db),before);
});

// Watch-boundary unit evidence (RE-540): the labelled recordingWatch double delivers an EPERM error as some
// runtimes report a removed folder; the folder removals themselves are real. The native G9 render test is
// the only native evidence.
const eperm=()=>Object.assign(new Error('EPERM: operation not permitted, watch'),{code:'EPERM'});
test('a watcher error with its folder gone records the directory-removed reason; with the folder present it keeps its own',async()=>{
  const p=richProject('watch error folders');
  for(const removed of [true,false]){
    const dir=tempDir('owned watched tool '),tool=join(dir,'todo.py');
    copyFileSync(TODO_PY,tool);
    const {watch,calls}=recordingWatch();
    const monitor=board.createMonitor({dbPath:p.db,projectRoot:p.root,dataDir:tempDir('watch error history '),python,todo:tool,watch});
    try{
      await monitor.settled(); // no picker child holds the tool when its folder goes
      if(removed)rmSync(dir,{recursive:true}); // only this owned copy
      calls[1].watcher.emit('error',eperm());
      const s=monitor.snapshot();
      assert.equal(s.watchState.tool.active,false);
      if(!removed){assert.equal(s.watchState.tool.error,eperm().message,'a folder that is there keeps the error’s own reason');continue;}
      assert.match(s.watchState.tool.error,/^Watched directory removed: /);
      const after=await monitor.settled(),q=after.boards[0].queue;
      assert.equal(q.state,'error','the re-check the change path would ask for ran');
      assert.match(q.error,/todo\.py/,'the real picker child’s own report of its missing tool');
    }finally{monitor.close();}
  }
  const b=richProject('watch error board folder'),{watch,calls}=recordingWatch();
  const monitor=board.createMonitor({dbPath:b.db,projectRoot:b.root,dataDir:tempDir('watch error board history '),watch});
  try{
    await monitor.settled();
    await until(()=>{try{rmSync(join(b.root,'.claude'),{recursive:true,force:true});return true;}catch{return false;}},'remove the board’s folder');
    calls[0].watcher.emit('error',eperm());
    assert.match(monitor.snapshot().watchState.board.error,/^Watched directory removed: /);
    assert.equal(monitor.snapshot().watchState.board.active,false);
    await until(()=>monitor.snapshot().boards[0].available===false,'the read the error scheduled');
  }finally{monitor.close();}
});


const noWatch = () => { throw Object.assign(Error('EMFILE: labelled deterministic race boundary'),{code:'EMFILE'}); };
for(const mode of ['rows','tool'])test('native '+mode+' changes on every real picker snapshot stop after the bounded retry guard',async()=>{
  const p=richProject('bounded native '+mode),dir=tempDir('native changing picker '),tool=join(dir,'todo.py');
  copyFileSync(TODO_PY,tool);
  const bridge=nativeBridge(mode,dir),before=sha(p.db);
  const monitor=board.createMonitor({dbPath:p.db,projectRoot:p.root,dataDir:tempDir('bounded history '),todo:tool,
    python:{command:PYTHON,args:[bridge.path]},watch:noWatch});
  try{
    const snap=await monitor.settled(),q=snap.boards[0].queue;
    assert.equal(q.state,'error');
    assert.match(q.error,mode==='tool'?/kept changing during every check/:/changed during every picker check/);
    assert.equal(snap.boards[0].rules.known,false,'an unaccepted snapshot never supplies current policy');
    assert.equal(q.headId,undefined);
  }finally{monitor.close();}
  if(mode==='tool')assert.equal(sha(p.db),before,'tool races never write the synthetic board');
});
test('real in-flight picker success after close is discarded and the native child settles',async()=>{
  const p=richProject('native close'),dir=tempDir('native delayed picker '),bridge=nativeBridge('wait',dir);
  const before=sha(p.db),monitor=board.createMonitor({dbPath:p.db,projectRoot:p.root,dataDir:tempDir('native delayed history '),todo:TODO_PY,python:{command:PYTHON,args:[bridge.path]},watch:noWatch});
  const settling=monitor.settled();
  try{
    await until(()=>existsSync(bridge.marker),'the real native child started');
    monitor.close();writeFileSync(bridge.release,'release');
    const snap=await settling;assert.notEqual(snap.boards[0].queue.state,'ready');
    assert.equal(sha(p.db),before);
  }finally{writeFileSync(bridge.release,'release');monitor.close();}
});


test('a current native picker failure withdraws its result and recovers on a new real check',async()=>{
  const p=richProject('current native picker error'),dir=tempDir('current native picker error '),tool=join(dir,'todo.py');
  copyFileSync(TODO_PY,tool);
  const bridge=nativeBridge('wait-error',dir),before=sha(p.db),toolBefore=sha(tool);
  // The child is real; the explicit EMFILE watch double only confines this
  // generation test. It supplies no native watcher evidence.
  const monitor=board.createMonitor({dbPath:p.db,projectRoot:p.root,dataDir:tempDir('current error history '),
    todo:tool,python:{command:PYTHON,args:[bridge.path]},watch:noWatch});
  try{
    await until(()=>existsSync(bridge.marker),'the current failing native child started');
    writeFileSync(bridge.release,'release');
    const failed=await monitor.settled(),q=failed.boards[0].queue;
    assert.equal(Number(readFileSync(bridge.count,'utf8')),1);
    assert.equal(q.state,'error');assert.match(q.error,/picker failed \(7\) without a readable result/);
    assert.equal(q.stale,false);assert.equal(q.headId,undefined);assert.equal(q.rankedIds,undefined);
    assert.equal(failed.boards[0].rules.known,false);
    assert.equal(failed.server.toolSha,toolBefore,'a failed current child still binds the actual tool bytes');
    assert.equal(sha(p.db),before);assert.equal(sha(tool),toolBefore);
    const recovered=await monitor.recheck();
    assert.equal(Number(readFileSync(bridge.count,'utf8')),2,'recovery launches its own native picker');
    assert.equal(recovered.boards[0].queue.state,'ready');
    assert.equal(recovered.boards[0].queue.headId,'MP-001');assert.equal(recovered.boards[0].queue.error,null);
    assert.equal(sha(p.db),before);assert.equal(sha(tool),toolBefore);
  }finally{writeFileSync(bridge.release,'release');monitor.close();}
});

test('an actual child failure after monitor close is discarded without publishing a new error',async()=>{
  const p=richProject('native error after close'),dir=tempDir('native failed delayed picker '),bridge=nativeBridge('wait-error',dir);
  const before=sha(p.db),monitor=board.createMonitor({dbPath:p.db,projectRoot:p.root,dataDir:tempDir('native delayed error history '),todo:TODO_PY,python:{command:PYTHON,args:[bridge.path]},watch:noWatch});
  const settling=monitor.settled();
  try{
    await until(()=>existsSync(bridge.marker),'the real failing child started');
    monitor.close();writeFileSync(bridge.release,'release');
    const snap=await settling;assert.equal(snap.boards[0].queue.state,'checking');
    assert.equal(snap.boards[0].queue.error,null);assert.equal(sha(p.db),before);
  }finally{writeFileSync(bridge.release,'release');monitor.close();}
});

for(const mode of ['wait-stale-success','wait-stale-error'])test('a newer manual check rejects stale native '+mode+' and awaits the current child',async()=>{
  const p=richProject('native generation '+mode),dir=tempDir('native generation picker '),bridge=nativeBridge(mode,dir);
  const before=sha(p.db),monitor=board.createMonitor({dbPath:p.db,projectRoot:p.root,dataDir:tempDir('native generation history '),todo:TODO_PY,python:{command:PYTHON,args:[bridge.path]},watch:noWatch});
  try{
    await until(()=>existsSync(bridge.marker),'the original native generation started');
    const rechecking=monitor.recheck();
    assert.equal(monitor.snapshot().boards[0].queue.state,'checking');
    writeFileSync(bridge.release,'release');
    const snap=await rechecking;
    assert.equal(Number(readFileSync(bridge.count,'utf8')),2,'the newer request causes its own native picker');
    assert.equal(snap.boards[0].queue.state,'ready');assert.equal(snap.boards[0].queue.headId,'MP-001');
    assert.equal(snap.boards[0].queue.error,null);assert.equal(sha(p.db),before);
  }finally{writeFileSync(bridge.release,'release');monitor.close();}
});

test('five genuinely changing idle-WAL reads are refused, with unchanged SQLite bytes and no reader sidecars',async()=>{
  const dir=tempDir('native file identity race '),path=join(dir,'rows.db'),db=new DatabaseSync(path);
  try{
    db.exec("PRAGMA journal_mode=WAL;CREATE TABLE rows(id INTEGER PRIMARY KEY,payload TEXT);WITH RECURSIVE seq(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM seq WHERE x<12000) INSERT INTO rows SELECT x,printf('%0128d',x) FROM seq;");
  }finally{db.close();}
  assert.equal(existsSync(path+'-wal'),false);assert.equal(existsSync(path+'-shm'),false);
  const before=sha(path),flags=new Int32Array(new SharedArrayBuffer(8));
  // A real native worker repeatedly changes only this synthetic owner's mtime.
  // It has no reader hook and supplies no counters or manufactured stat results.
  const worker=new Worker(`
    const {parentPort,workerData}=require('node:worker_threads');
    const {utimesSync}=require('node:fs');
    const flags=new Int32Array(workerData.flags),base=Date.now()/1000;
    try{
      let count=0;
      while(!Atomics.load(flags,1)){
        utimesSync(workerData.path,base,base+(++count)/1000);
        if(count===1){Atomics.store(flags,0,1);Atomics.notify(flags,0);}
      }
      parentPort.postMessage({changes:count});
    }catch(error){parentPort.postMessage({error:error.message});}
  `,{eval:true,workerData:{path,flags:flags.buffer}});
  const result=new Promise((ok,fail)=>{worker.once('message',ok);worker.once('error',fail);});
  const exited=new Promise((ok,fail)=>{worker.once('exit',ok);worker.once('error',fail);});
  let readFailure;
  try{
    await until(()=>Atomics.load(flags,0)===1,'the genuine file writer started');
    assert.throws(()=>board.readTables(path),/kept changing while it was read/);
  }catch(error){readFailure=error;}finally{Atomics.store(flags,1,1);}
  const finished=await result;assert.equal(finished.error,undefined);assert.ok(finished.changes>5);
  assert.equal(await exited,0,'the native writer exits cleanly');
  if(readFailure)throw readFailure;
  assert.equal(sha(path),before,'the native writer changes no SQLite data bytes');
  assert.equal(existsSync(path+'-wal'),false);assert.equal(existsSync(path+'-shm'),false);
});

test('cache fallback and board identity follow the actual native host path policy',()=>{
  const empty=board.userDataRoot({});
  if(process.platform==='win32')assert.equal(empty,join(homedir(),'AppData','Local','sijav-todo-dashboard'));
  else if(process.platform==='darwin')assert.equal(empty,join(homedir(),'Library','Caches','sijav-todo-dashboard'));
  else{
    assert.equal(empty,join(homedir(),'.cache','sijav-todo-dashboard'));
    assert.equal(board.userDataRoot({XDG_CACHE_HOME:tempDir('native XDG cache ')}).endsWith('sijav-todo-dashboard'),true);
  }
  assert.match(board.boardKey(parse(tempDir('native board identity ')).root),/^board-[a-f0-9]{16}$/);
  assert.equal(typeof board.timestamp('2026-10-01T00:00:00+01:00'),'string');
});

test('default Python discovery uses the actual native PATH candidate order',()=>{
  const found=board.discoverPython(null,{});
  assert.ok(found.command,found.reason);
  const names=process.platform==='win32'?['python','py -3','python3']:['python3','python'];
  assert.ok(names.includes(found.found),'the returned interpreter came from the native policy candidates');
});

test('real legacy nullable relation rows keep normalized string IDs and unknown policy separate',()=>{
  const root=tempDir('owned legacy relations '),path=join(root,'legacy.db'),writer=new DatabaseSync(path);
  try {
    writer.exec("CREATE TABLE task(id TEXT PRIMARY KEY,title TEXT,status TEXT,parent_task TEXT,story TEXT);"+
      "INSERT INTO task VALUES('legacy-a',NULL,'manual',NULL,NULL),('legacy-b','Child','manual','legacy-a','stored story');"+
      "CREATE TABLE blocked_by(task TEXT,parent TEXT);INSERT INTO blocked_by VALUES('legacy-b','legacy-a');"+
      "CREATE TABLE detail(task TEXT,payload TEXT);INSERT INTO detail VALUES('legacy-a',NULL),('legacy-b','stored detail');");
  } finally {writer.close();}
  const before=sha(path),read=board.readBoard(path),unknown=board.classify(read,board.rulesFrom(null,'Deliberately unconfigured legacy tool'));
  const first=read.tasks.find(t=>t.id==='legacy-a'),child=read.tasks.find(t=>t.id==='legacy-b');
  assert.equal(first.title,null);assert.equal(first.parentTask,null);assert.deepEqual(first.children,['legacy-b']);
  assert.deepEqual(first.dependents,['legacy-b']);assert.deepEqual(child.dependencies,['legacy-a']);
  assert.equal(first.related.detail[0].payload,null);assert.equal(child.description,'stored story');
  assert.equal(unknown.counts.open,null);assert.equal(unknown.counts.total,2);
  assert.equal(sha(path),before,'reading/classification preserve the native original bytes');
});

for(const obstruction of ['changes.jsonl','baseline.json.tmp'])test('owned native history '+obstruction+' obstruction reports persistence refusal without changing the board',()=>{
  const p=richProject('owned persistence refusal'),dir=tempDir('owned persistence obstruction ');
  const before=sha(p.db),original=board.readBoard(p.db);
  // The watch refusal is a labelled deterministic contract; the persistence
  // error below comes from a real owned filesystem directory obstruction.
  const monitor=board.createMonitor({dbPath:p.db,projectRoot:p.root,dataDir:dir,watch:noWatch});
  try {
    const initial=monitor.snapshot(),initialBoard=initial.boards[0];
    assert.equal(initialBoard.available,true);
    assert.equal(initialBoard.hash,original.hash);
    assert.equal(initialBoard.tasks.find(t=>t.id==='MP-004').title,original.tasks.find(t=>t.id==='MP-004').title);
    assert.equal(initial.persistenceError,null);
    assert.equal(sha(p.db),before,'monitor acquisition preserves the initial actor-owned board bytes');
    assert.equal(existsSync(monitor.paths.statePath),true,'the real initial baseline was persisted');
    assert.equal(existsSync(join(dir,obstruction)),false,'the fresh baseline has no selected obstruction');
    mkdirSync(join(dir,obstruction));
    const writer=new DatabaseSync(p.db);
    try {writer.prepare('UPDATE task SET title=? WHERE id=?').run('Owner changed the synthetic title','MP-004');}
    finally {writer.close();}
    const written=sha(p.db);
    assert.notEqual(written,before,'the actual actor update changed its board file');
    monitor.readNow();
    const s=monitor.snapshot();
    assert.equal(s.boards[0].available,true);
    assert.notEqual(s.boards[0].hash,initialBoard.hash,'the read observes the actor-written board change');
    assert.match(s.persistenceError,obstruction==='changes.jsonl'?/Change journal could not be saved/:/Change baseline could not be saved/);
    assert.doesNotMatch(s.persistenceError,obstruction==='changes.jsonl'?/Change baseline could not be saved/:/Change journal could not be saved/);
    assert.equal(s.boards[0].tasks.find(t=>t.id==='MP-004').title,'Owner changed the synthetic title');
    assert.equal(sha(p.db),written,'monitor reads never alter the explicitly written fixture');
    assert.equal(existsSync(join(dir,obstruction)),true);
  } finally {monitor.close();}
});


test('a native nullable loop SQL row retains NULL severity and priority without inventing absent columns',()=>{
  const root=tempDir('owned nullable loop '),path=join(root,'board.db'),writer=new DatabaseSync(path);
  try { writer.exec("CREATE TABLE item(id TEXT PRIMARY KEY,title TEXT,status TEXT,severity TEXT,priority INTEGER);"+
    "CREATE TABLE dep(item TEXT,blocker TEXT);INSERT INTO item VALUES('old-loop',NULL,'manual',NULL,NULL);"); }
  finally { writer.close(); }
  const before=sha(path),read=board.readBoard(path);
  assert.equal(read.kind,'loop');assert.equal(read.tasks[0].severity,null);assert.equal(read.tasks[0].points,null);
  assert.equal(read.tasks[0].raw.severity,null);assert.equal(read.tasks[0].raw.priority,null);
  assert.equal(sha(path),before,'the canonical reader preserves the owned SQL original');
});

test('a caller-provided complete monitor settings object without activityLimit gives an unlimited ready queue preview',async()=>{
  const p=richProject('caller settings preview'),settings=JSON.parse(JSON.stringify(board.defaults));
  delete settings.ui.activityLimit; // Explicit public caller input; shipped CLI defaults still specify 10.
  const before=sha(p.db),{monitor,snapshot}=await monitored(p,{python,todo:TODO_PY,settings});
  try {
    const shown=buildAmbientModel(snapshot).boards[0];
    assert.equal(shown.queue.ready,true);assert.ok(shown.queued.length>0);
    assert.deepEqual(shown.queuePreview,shown.queued);assert.equal(snapshot.ui.activityLimit,undefined);
    assert.equal(sha(p.db),before);
  } finally {monitor.close();}
});

for(const [source,problem] of [
  ["print('not readable JSON')",/picker failed without a readable result/],
  ["print('{}')",/picker returned an incomplete result/],
])test('declared protocol-negative native child: '+source,async()=>{
  const p=richProject('negative protocol child'),before=sha(p.db);
  // The public interpreter descriptor runs a deliberately selected Python -c
  // producer. This is real native child/protocol evidence, not the canonical picker.
  await assert.rejects(board.runPicker({python:{command:PYTHON,args:['-c',source]},todo:TODO_PY,dbPath:p.db}),problem);
  assert.equal(sha(p.db),before,'this negative producer never opens the board');
});

test('a real locked board stays unavailable when its owned watched tool directory is removed',async()=>{
  const p=richProject('unavailable tool event'),dir=tempDir('owned removable tool '),tool=join(dir,'todo.py');
  copyFileSync(TODO_PY,tool);
  const settings=JSON.parse(JSON.stringify(board.defaults));settings.read.busyMs=1;
  const before=sha(p.db),monitor=board.createMonitor({dbPath:p.db,projectRoot:p.root,dataDir:tempDir('unavailable tool history '),python,todo:tool,settings});
  let writer;
  try {
    assert.equal((await monitor.settled()).boards[0].queue.state,'ready');
    assert.equal(monitor.snapshot().watchState.tool.active,true,'the native tool watcher was actually acquired');
    writer=new DatabaseSync(p.db);writer.exec('BEGIN EXCLUSIVE');
    monitor.readNow();
    assert.equal(monitor.snapshot().boards[0].available,false);assert.equal(monitor.snapshot().boards[0].queue.state,'unavailable');
    rmSync(dir,{recursive:true}); // Only this actor-owned copy; the canonical tool is never removed.
    await until(()=>!monitor.snapshot().watchState.tool.active,'the native removed-directory event');
    assert.match(monitor.snapshot().watchState.tool.error,/Watched directory removed/);
    assert.equal(monitor.snapshot().boards[0].queue.state,'unavailable');
  } finally {monitor.close();if(writer){try{writer.exec('ROLLBACK');}finally{writer.close();}}}
  assert.equal(sha(p.db),before);
});

const malformedBaselineCases=[
  ['null',null],['array',[]],['number',7],['missing board',{}],['empty nested board',{board:{}}],
  ['bad table collection',{board:{hash:'saved',tables:[],schema:[]}}],
  ['bad table rows',{board:{hash:'saved',tables:{task:[null]},schema:[]}}],
  ['bad schema row',{board:{hash:'saved',tables:{},schema:[{name:'task'}]}}],
  ['bad indexes',{board:{hash:'saved',tables:{},schema:[],indexes:{}}}],
  ['bad primary keys',{board:{hash:'saved',tables:{},schema:[],primaryKeys:{task:[3]}}}],
  ['bad saved task',{board:{hash:'saved',tables:{},schema:[],tasks:[{id:null}]}}],
  ['bad tracking date',{startedAt:4,board:null}],
];
for(const [label,saved] of malformedBaselineCases)test('owned baseline recovery: '+label,()=>{
  const p=richProject('baseline shape '+label),dir=tempDir('owned malformed baseline '),file=join(dir,'baseline.json');
  writeFileSync(file,JSON.stringify(saved));
  const before=sha(p.db),monitor=board.createMonitor({dbPath:p.db,projectRoot:p.root,dataDir:dir,watch:noWatch});
  try {
    const snapshot=monitor.snapshot(),current=snapshot.boards[0],fresh=JSON.parse(readFileSync(file,'utf8'));
    assert.equal(current.available,true);assert.equal(current.stale,false);
    assert.match(snapshot.persistenceError,/Previous baseline could not be read; history continues from now/);
    assert.match(snapshot.persistenceError,/Invalid baseline snapshot shape/);
    assert.equal(monitor.changes().length,0,'recovery does not invent a diff against malformed saved rows');
    assert.equal(fresh.board.hash,current.hash);assert.equal(fresh.dbPath,p.db);
    assert.equal(typeof fresh.startedAt,'string');assert.equal(sha(p.db),before);
  } finally {monitor.close();}
});

test('a genuine persisted baseline and its owned consumer-optional shape stay readable',()=>{
  const p=richProject('genuine baseline reload'),dir=tempDir('owned saved baseline ');
  const first=board.createMonitor({dbPath:p.db,projectRoot:p.root,dataDir:dir,watch:noWatch});
  first.close();
  const file=join(dir,'baseline.json'),saved=JSON.parse(readFileSync(file,'utf8')),before=sha(p.db);
  const exact=board.createMonitor({dbPath:p.db,projectRoot:p.root,dataDir:dir,watch:noWatch});
  try {assert.equal(exact.snapshot().persistenceError,null);assert.equal(exact.changes().length,0);}
  finally {exact.close();}
  // This owned cache edit exercises consumer-optional fields; it is not an old producer.
  // The board hash stays equal, so this scenario establishes no diff-arm execution.
  delete saved.startedAt;delete saved.board.tasks;delete saved.board.indexes;delete saved.board.primaryKeys;
  writeFileSync(file,JSON.stringify(saved));
  const optional=board.createMonitor({dbPath:p.db,projectRoot:p.root,dataDir:dir,watch:noWatch});
  try {
    assert.equal(optional.snapshot().boards[0].available,true);assert.equal(optional.snapshot().persistenceError,null);
    assert.equal(optional.snapshot().trackingSince,undefined,'a standalone consumer-optional cache preserves its absent date');assert.equal(sha(p.db),before);
  } finally {optional.close();}
});

// Owned consumer-shape edits, not an old producer: persistence always writes an object board, and no
// release is claimed to have written board null.
test('a saved baseline whose board is null starts a fresh one without a warning, and combined dateless caches give no date',()=>{
  const p=richProject('baseline board null'),dir=tempDir('owned null baseline '),file=join(dir,'baseline.json');
  writeFileSync(file,JSON.stringify({startedAt:'2026-10-01T00:00:00.000Z',board:null}));
  const before=sha(p.db),monitor=board.createMonitor({dbPath:p.db,projectRoot:p.root,dataDir:dir,watch:noWatch});
  try{
    const s=monitor.snapshot();
    assert.equal(s.persistenceError,null);assert.equal(s.trackingSince,'2026-10-01T00:00:00.000Z');
    assert.equal(JSON.parse(readFileSync(file,'utf8')).board.hash,s.boards[0].hash,'a fresh baseline of the current read was saved');
    assert.equal(sha(p.db),before);
  }finally{monitor.close();}
  const dateless=[richProject('dateless cache one'),richProject('dateless cache two')].map((q,i)=>{
    const d=tempDir('owned dateless cache ');
    board.createMonitor({dbPath:q.db,projectRoot:q.root,dataDir:d,watch:noWatch}).close();
    const f=join(d,'baseline.json'),saved=JSON.parse(readFileSync(f,'utf8'));delete saved.startedAt;writeFileSync(f,JSON.stringify(saved));
    return board.createMonitor({dbPath:q.db,projectRoot:q.root,dataDir:d,watch:noWatch,sourceId:'cache'+i});
  });
  try{assert.equal(board.combineMonitors(dateless).snapshot().trackingSince,null);}
  finally{for(const m of dateless)m.close();}
});

// G7 · ordinary: Relax drawn by its own renderer from the server's model of real monitor states.
test('Relax as drawn: a board locked from its first read, a refused history save, and a started task waiting on a parent named after it started',async()=>{
  const p=richProject('relax locked first read'),settings=JSON.parse(JSON.stringify(board.defaults));settings.read.busyMs=1;
  const writer=new DatabaseSync(p.db);writer.exec('BEGIN EXCLUSIVE');
  let locked;
  try{
    locked=board.createMonitor({dbPath:p.db,projectRoot:p.root,dataDir:tempDir('relax locked history '),python,todo:TODO_PY,settings,watch:noWatch});
    const html=renderAmbientModel(views.relax(locked.snapshot()));
    assert.match(html,/<span class="ambient-source-state">Read failed<\/span>/,'never read, so no last successful data');
    assert.match(html,/The board is unavailable\. Next cannot be confirmed\./);
  }finally{locked?.close();writer.exec('ROLLBACK');writer.close();}
  const q=richProject('relax waiting and refused save'),dir=tempDir('relax obstructed history ');
  todo(q.root,'move','MP-004','in_progress');
  todo(q.root,'edit','MP-004','--parent','MP-001'); // RE-317: a parent named after MP-004 started
  const {monitor,snapshot}=await monitored(q,{python,todo:TODO_PY,dataDir:dir,watch:noWatch});
  try{
    const shown=buildAmbientModel(snapshot).boards[0];
    assert.notEqual(shown.head?.id,'MP-004','no pick of the waiting started task is invented');
    mkdirSync(join(dir,'changes.jsonl'));
    todo(q.root,'edit','MP-002','--note','a change whose history cannot be saved');
    monitor.readNow();
    const html=renderAmbientModel(views.relax(await monitor.settled()));
    assert.match(html,/<div class="ambient-error" role="status">.*Change journal could not be saved/s);
    assert.match(html,/data-ambient-task="board:MP-004"[\s\S]*?<summary>Not pickable · \d+ reasons?<\/summary>/);
  }finally{monitor.close();}
});

test('actual canonical picker child reports a structured syntax error for its owned unusable tool',async()=>{
  const p=richProject('owned syntax refusal'),dir=tempDir('owned unusable tool '),tool=join(dir,'todo.py');
  writeFileSync(tool,'def deliberately_invalid(:\n');
  const boardBefore=sha(p.db),toolBefore=sha(tool);
  const monitor=board.createMonitor({dbPath:p.db,projectRoot:p.root,dataDir:tempDir('owned syntax history '),
    todo:tool,python:{command:PYTHON,args:[]},watch:noWatch});
  try {
    const s=await monitor.settled(),b=s.boards[0];
    assert.equal(b.queue.state,'error');assert.match(b.queue.error,/SyntaxError/);
    assert.equal(b.rules.known,false,'failed policy is not a known empty policy');
    assert.equal(b.queue.headId,undefined,'a failed real child cannot publish a current head');
    assert.equal(sha(p.db),boardBefore);assert.equal(sha(tool),toolBefore);
  } finally {monitor.close();}
});
