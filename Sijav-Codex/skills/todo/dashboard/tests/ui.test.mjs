import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {setTimeout as delay} from 'node:timers/promises';
import {sortLane,firstOpen,nextTask,sortQueue,queuePosition,pickerOrder,diffLines,changedFields,renderFieldDiff,formatDate,clampText} from '../public/board-ui.mjs';
import {rulesFrom} from '../lib/board.mjs';
import {buildAmbientModel,renderAmbient} from '../public/ambient-ui.mjs';
import {classifyWork,facetSupport} from '../public/work-context.mjs';
import {themeStorageKey,resolveTheme,readThemePreference,storeThemePreference,toggleTheme,createDisplayController} from '../public/display-mode.mjs';

const defaults=JSON.parse(readFileSync(new URL('../defaults.json',import.meta.url),'utf8'));
// The policy shape the picker derives from todo.py (see picker.test.mjs for the derivation itself).
const rules=rulesFrom({severities:['critical','high','medium','low'],statuses:['backlog','in_progress','wait_for_roast','done','dropped'],
  groups:{doing:['in_progress','wait_for_roast'],open:['backlog'],finished:['done'],discarded:['dropped'],other:[],unknown:[]},satisfying:['done'],closed:['done','dropped'],rankLabels:null,docs:{}});
const task=(id,status='backlog',extra={})=>({id:String(id),key:'board:'+id,sourceId:'board',title:'Task '+id,status,severity:'high',points:2,
  createdAt:'2026-09-01T00:00:00.000Z',closedAt:null,updatedAt:null,lastRecordedAt:'2026-09-01T00:00:00.000Z',statusObservedAt:null,phase:null,
  isOpen:rules.openStatuses.includes(status),isDoing:rules.doingStatuses.includes(status),isFinished:rules.finishedStatuses.includes(status),
  isComplete:[...rules.finishedStatuses,...rules.discardedStatuses].includes(status),isExplicitlyBlocked:false,isWaiting:false,
  dependencies:[],unresolved:[],dependents:[],children:[],openChildren:[],notes:[],roasts:[],blocked:[],related:{},
  raw:{id:String(id),title:'Task '+id,status,descr:'Full story for task '+id,why:'Reason for task '+id,area:null,...extra.raw},...extra});
const columns=['id','title','descr','why','severity','points','status','exit_cond','area','created','parent_task','phase','evidence','closed','reason','updated'];
function snapshot(extra={}) {
  const tasks=[task(1,'in_progress'),task(2,'wait_for_roast'),task(3),task(4),task(5),task(6),task(7,'done',{closedAt:'2026-09-20T00:00:00.000Z'})];
  const board={id:'board',name:'Fixture project',available:true,stale:false,error:null,picker:{configured:true},rules,tasks,taskTable:'task',tableColumns:{task:columns},tables:{phase:[]},
    queue:{state:'ready',stale:false,checkedAt:'2026-09-30T12:00:00Z',error:null,headId:'2',headKind:'started',
      startedIds:['2','1'],eligibleIds:['5','3'],startableIds:['2','1','5','3'],rankedIds:['2','1','5','3','6','4'],
      deferred:{'6':[{kind:'blocked',message:'Blocked: owner decision'}],'4':[{kind:'parent',message:'Waits on 6 (backlog): Task 6'}]},nextText:'ALREADY STARTED, finish this first\n\n2  ...\n',currentPhase:null}};
  return {checkedAt:'2026-09-30T12:00:00Z',boards:[board],rules,ui:{...defaults.ui,activityLimit:1},workClassification:{},statusTransitions:[],watchState:{board:{active:true,error:null}},persistenceError:null,...extra};
}

test('unfinished lanes follow only the picker result; without one they keep ID order',()=>{
  const data=snapshot(),board=data.boards[0];
  assert.deepEqual(sortLane(board.tasks.filter(t=>t.isOpen),'backlog',board).map(t=>t.id),['5','3','6','4']);
  assert.deepEqual(sortLane(board.tasks.filter(t=>t.isDoing),'in_progress',board).map(t=>t.id),['2','1']);
  const noPicker={...board,queue:{state:'unconfigured'}};
  assert.deepEqual(sortLane(board.tasks.filter(t=>t.isOpen),'backlog',noPicker).map(t=>t.id),['3','4','5','6']);
  assert.equal(nextTask(noPicker),null,'no picker result never invents a next task');
  assert.equal(firstOpen(noPicker),null);
  assert.equal(nextTask(board).id,'2');assert.equal(firstOpen(board).id,'5');
  assert.deepEqual(queuePosition(board.tasks[1],board),{index:0,current:true,group:'started',head:true});
  assert.equal(queuePosition(board.tasks[5],board).group,'waiting');
  assert.deepEqual(sortQueue(board.tasks,board).map(t=>t.id),['2','1','5','3','6','4','7']);
  assert.equal(board.tasks[0].id,'1','sorting does not mutate the source list');
});

test('while re-checking, the previous order is kept but labelled, with no pick and no head (M1)',()=>{
  const board=snapshot().boards[0];
  board.queue={state:'checking',stale:true,error:null,previous:{checkedAt:'2026-09-30T11:00:00Z',rankedIds:['2','1','5','3','6','4']}};
  const order=pickerOrder(board);assert.equal(order.current,false);assert.equal(order.checkedAt,'2026-09-30T11:00:00Z');
  assert.deepEqual(sortLane(board.tasks.filter(t=>t.isOpen),'backlog',board).map(t=>t.id),['5','3','6','4']);
  assert.equal(nextTask(board),null);assert.equal(firstOpen(board),null);
  assert.deepEqual(queuePosition(board.tasks[1],board),{index:0,current:false,group:'previous',head:false});
});
test('failed picker or unreadable board: no order, no pick, ID order (H1, M1)',()=>{
  for(const change of [b=>{b.queue={state:'error',stale:true,error:'SyntaxError'};},b=>{b.available=false;b.stale=true;b.queue={state:'unavailable',stale:true,error:'missing'};},
    b=>{b.available=false;b.stale=true;}]){
    const board=snapshot().boards[0];change(board);
    assert.equal(pickerOrder(board),null);assert.equal(nextTask(board),null);assert.equal(queuePosition(board.tasks[1],board),null);
    assert.deepEqual(sortLane(board.tasks.filter(t=>t.isOpen),'backlog',board).map(t=>t.id),['3','4','5','6']);
  }
});
test('with no known policy no lane is treated as closed or recency-sorted',()=>{
  const board={...snapshot().boards[0],rules:rulesFrom(null,'No Python')};
  const done=[task(1,'done',{closedAt:'2026-09-28T00:00:00Z'}),task(2,'done',{closedAt:'2026-09-30T00:00:00Z'})];
  board.queue={state:'unconfigured'};
  assert.deepEqual(sortLane(done,'done',board).map(t=>t.id),['1','2'],'ID order: "done" means nothing without the tool');
});

test('done lane: most recent completion first; dropped by its last update; undated last',()=>{
  const board=snapshot().boards[0];
  const done=[task(1,'done',{closedAt:'2026-09-28T00:00:00Z',lastRecordedAt:'2026-09-30T23:00:00Z'}),task(2,'done',{closedAt:'2026-09-30T00:00:00Z'}),task(3,'done')];
  assert.deepEqual(sortLane(done,'done',board).map(t=>t.id),['2','1','3']);
  const dropped=[task(4,'dropped',{updatedAt:'2026-09-01T00:00:00Z'}),task(5,'dropped',{updatedAt:'2026-09-05T00:00:00Z'})];
  assert.deepEqual(sortLane(dropped,'dropped',board).map(t=>t.id),['5','4']);
});

test('line comparisons preserve every old/new line and actual line numbers',()=>{
  const values=['','a','a\n','\na','a\nb','b\na','a\nb\na','a\r\nb\r\n'];
  for(const a of values)for(const b of values){
    const rows=diffLines(a,b,defaults.ui.diffMaxEdits);
    assert.equal(rows.filter(r=>r.kind!=='add').map(r=>r.text).join('\n'),a);
    assert.equal(rows.filter(r=>r.kind!=='remove').map(r=>r.text).join('\n'),b);
    assert.deepEqual(rows.filter(r=>r.oldLine!=null).map(r=>r.oldLine),a.split('\n').map((_,i)=>i+1));
    assert.deepEqual(rows.filter(r=>r.newLine!=null).map(r=>r.newLine),b.split('\n').map((_,i)=>i+1));
  }
});
test('large replacement fallback stays lossless',()=>{
  const a=Array.from({length:400},(_,i)=>'old '+i).join('\n'),b=Array.from({length:400},(_,i)=>'new '+i).join('\n');
  const rows=diffLines(a,b,20);
  assert.equal(rows.filter(r=>r.kind==='remove').map(r=>r.text).join('\n'),a);
  assert.equal(rows.filter(r=>r.kind==='add').map(r=>r.text).join('\n'),b);
});
test('changes are shown per field as removed/added lines, not raw JSON',()=>{
  const before={title:'unchanged',descr:'same\nold\nsame',status:'backlog'},after={title:'unchanged',descr:'same\nnew\nsame',status:'in_progress'};
  assert.deepEqual(changedFields(before,after).map(f=>f.key),['descr','status']);
  const html=renderFieldDiff(before,after,defaults.ui);
  assert.match(html,/diff-remove/);assert.match(html,/diff-add/);assert.match(html,/line-number/);
  assert.match(html,/<strong>Descr<\/strong><code>descr<\/code>/);assert.match(html,/<strong>Status<\/strong>/);
  assert.doesNotMatch(html,/unchanged|"status"\s*:|\{"/);
  assert.match(html,/>old<\/pre>/);assert.match(html,/>new<\/pre>/);
});
test('addition/removal and equal-text type changes remain visible',()=>{
  assert.equal(changedFields(null,{status:'backlog'})[0].rows[0].kind,'add');
  assert.equal(changedFields({status:'backlog'},null)[0].rows[0].kind,'remove');
  for(const [before,after]of [[1,'1'],[null,'NULL'],['',null]]){
    const field=changedFields({value:before},{value:after})[0];
    assert.ok(field.rows.some(r=>r.kind==='remove'));assert.ok(field.rows.some(r=>r.kind==='add'));
  }
});
test('unsafe content is escaped and literal dotted keys cannot hide a change',()=>{
  const html=renderFieldDiff({descr:'old'},{descr:'<script>alert(1)</script>'},defaults.ui);
  assert.doesNotMatch(html,/<script>/);assert.match(html,/&lt;script&gt;/);
  const fields=changedFields({'a.b':'old',a:{b:'same'}},{'a.b':'new',a:{b:'same'}});
  assert.equal(fields.length,1);assert.equal(fields[0].key,'["a.b"]');
});
test('the board’s own date formats are shown human-readably, in diffs too',()=>{
  for(const stored of ['2026-09-30 14:05:09','2026-09-30T14:05:09.123Z']){
    const human=formatDate(stored);assert.notEqual(human,stored);assert.match(human,/2026/);
  }
  assert.equal(formatDate('2026-09-30 14:05:09'),formatDate('2026-09-30T14:05:09Z'),'SQLite datetime() is UTC');
  assert.equal(formatDate(null),'Not recorded');assert.equal(formatDate('not a date'),'not a date');
  const dated=renderFieldDiff({closed:'2026-09-30T14:05:09.123Z'},{closed:'2026-10-01T09:00:00.000Z'},defaults.ui);
  assert.doesNotMatch(dated,/2026-10-01T09/);assert.match(dated,/2026/);
  const html=renderFieldDiff({exit_cond:Array.from({length:12},(_,i)=>'line '+i).join('\n')},{exit_cond:Array.from({length:12},(_,i)=>i===6?'changed':'line '+i).join('\n')},{...defaults.ui,diffContextLines:0});
  assert.match(html,/unchanged lines/);assert.doesNotMatch(html,/>line 5<\/pre>/);
});

test('work area/type come only from stored fields; absent fields are reported, never derived',()=>{
  const t=task(1,'backlog',{raw:{area:'api',descr:'A frontend widget for the backend server'}});
  const context=classifyWork(t,{});
  assert.deepEqual(context.areas.map(a=>[a.label,a.origin]),[['api','recorded']]);
  assert.deepEqual(context.types,[],'no type field: nothing derived from text');
  assert.deepEqual(facetSupport(columns),{areas:['area'],types:[],topics:[]});
  assert.deepEqual(classifyWork(task(2),{}).areas,[]);
});

test('Relax: started pick is a resume, first eligible backlog shown separately, every Doing visible',()=>{
  const data=snapshot(),before=JSON.stringify(data),model=buildAmbientModel(data),board=model.boards[0];
  assert.deepEqual(board.doing.map(row=>row.id),['2','1'],'Doing in the tool’s started order');
  assert.equal(board.head.id,'2');assert.equal(board.headRole,'resume');assert.equal(board.firstOpen.id,'5');
  assert.deepEqual(board.queued.map(row=>row.id),['5','3']);assert.deepEqual(board.queuePreview.map(row=>row.id),['5']);
  assert.equal(board.counts.startable,4);assert.equal(board.counts.deferred,2);
  assert.equal(JSON.stringify(data),before,'rendering never changes board data');
  const html=renderAmbient(data,{connected:true,connectionLabel:'Live · file watcher'});
  assert.match(html,/Resume/);assert.match(html,/Full story for task 5/);assert.match(html,/Area<\/span> not recorded/);
  assert.doesNotMatch(html,/Type<\/span>/,'a facet the board cannot record is not shown as a guess');
});
test('Relax: checking, failed, stale, unconfigured or unreadable never invent a next task',()=>{
  for(const change of [b=>b.queue.state='checking',b=>{b.queue.state='error';b.queue.error='picker failed';},b=>b.queue.stale=true,
    b=>{b.picker={configured:false,reason:'No Python 3.9+ was found.'};b.queue={state:'unconfigured'};b.rules=rulesFrom(null,'No Python 3.9+ was found.');},
    b=>{b.available=false;b.stale=true;b.error='Read failed';b.queue={state:'unavailable',stale:true,error:'The board could not be read'};}]){
    const data=snapshot();change(data.boards[0]);
    const board=buildAmbientModel(data).boards[0];
    assert.equal(board.head,null);assert.equal(board.headRole,'none');
    const html=renderAmbient(data);
    assert.match(html,/checking|unavailable|failed|stale|Read failed/i);assert.doesNotMatch(html,/picked nothing|Nothing left/);
    if(data.boards[0].rules.known)assert.equal(board.doing.length,2,'stored Doing stays visible while the pick is unknown');
    else{assert.equal(board.doing.length,0);assert.equal(board.counts.doing,null);assert.match(html,/Which statuses mean Doing is unknown/);}
  }
});
test('Relax: an empty result shows the tool’s own words; an unused board is not called finished',()=>{
  const data=snapshot();Object.assign(data.boards[0].queue,{headId:null,headKind:null,startedIds:[],eligibleIds:[],startableIds:[],nextText:'Nothing left.\n'});
  assert.match(renderAmbient(data),/Nothing left\./);
  data.boards[0].queue.boardHadTaskTable=false;
  const html=renderAmbient(data);assert.match(html,/no task table yet/);assert.doesNotMatch(html,/Nothing left/);
});
test('Relax: latest finished uses the stored closure time; unsafe text is escaped',()=>{
  const data=snapshot();
  data.boards[0].tasks.push(task(8,'done',{closedAt:'2026-09-29T00:00:00Z',lastRecordedAt:'2026-10-05T00:00:00Z'}),task(9,'done'),task(10,'dropped',{updatedAt:'2026-10-07T00:00:00Z'}));
  assert.equal(buildAmbientModel(data).boards[0].latestClosed.id,'8');
  data.boards[0].tasks[0]=task(1,'in_progress',{title:'<img src=x onerror="unsafe()">',raw:{descr:'<script>unsafe()</script> & literal',area:'<iframe src="x">'}});
  const html=renderAmbient(data,{connected:false,connectionLabel:'<script>Disconnected</script>',paused:true});
  assert.doesNotMatch(html,/<script>|<img src=x|<iframe src=/);assert.match(html,/&lt;script&gt;unsafe\(\)&lt;\/script&gt; &amp; literal/);
  assert.equal(typeof renderAmbient({boards:[]}),'string');
});

test('theme preference validates stored values, follows OS fallback and tolerates unavailable storage',()=>{
  assert.equal(themeStorageKey,'sijav-todo-dashboard-theme');
  for(const choice of ['dark','light']){assert.equal(resolveTheme(choice,true),choice);assert.equal(resolveTheme(choice,false),choice);}
  for(const invalid of [null,undefined,'','system','DARK','<script>']){assert.equal(resolveTheme(invalid,true),'dark');assert.equal(resolveTheme(invalid,false),'light');}
  let stored=null,seenKey=null;
  const storage={getItem:key=>{seenKey=key;return stored;},setItem:(key,value)=>{seenKey=key;stored=value;}};
  assert.equal(readThemePreference(storage),null);assert.equal(seenKey,themeStorageKey);
  assert.equal(storeThemePreference(storage,'dark'),true);assert.equal(readThemePreference(storage),'dark');
  assert.equal(storeThemePreference(storage,'auto'),false);assert.equal(stored,'dark');
  const denied={getItem:()=>{throw new Error('denied');},setItem:()=>{throw new Error('denied');}};
  assert.equal(readThemePreference(denied),null);assert.equal(storeThemePreference(denied,'dark'),false);
  assert.equal(toggleTheme('dark'),'light');assert.equal(toggleTheme('light'),'dark');
});
const displayDocument=()=>({fullscreenElement:null,visibilityState:'visible',addEventListener(){},removeEventListener(){},async exitFullscreen(){this.fullscreenElement=null;}});
test('Relax stays usable in a window when fullscreen is unsupported or denied',async()=>{
  for(const requestFullscreen of [undefined,async()=>{throw new Error('denied');}]){
    const statuses=[],controller=createDisplayController({element:{requestFullscreen},document:displayDocument(),wakeLock:null,onStatus:value=>statuses.push(value)});
    await controller.enter();assert.equal(controller.active,true);assert.equal(statuses.at(-1).fullscreen,'window');
    await controller.exit();assert.equal(controller.active,false);
  }
});
test('exiting Relax releases a wake lock acquired after cancellation',async()=>{
  const document=displayDocument();let resolveWake,releases=0;
  const sentinel={addEventListener(){},async release(){releases++;}};
  const element={async requestFullscreen(){document.fullscreenElement=element;}};
  const controller=createDisplayController({element,document,wakeLock:{request:()=>new Promise(r=>{resolveWake=r;})}});
  const entering=controller.enter();await delay(0);
  const exiting=controller.exit();resolveWake(sentinel);await Promise.all([entering,exiting]);
  assert.equal(controller.active,false);assert.equal(document.fullscreenElement,null);assert.equal(releases,1);
});

test('browser code has no data polling: only the Relax clock and reconnect use timers',()=>{
  const app=readFileSync(new URL('../public/app.js',import.meta.url),'utf8');
  const intervals=[...app.matchAll(/setInterval\(([^,]+),/g)].map(m=>m[1]);
  assert.deepEqual(intervals,['updateClock']);
  assert.deepEqual([...app.matchAll(/setTimeout\(([^,]+),/g)].map(m=>m[1]).sort(),['()=>$(\'toast\').hidden=true','connectLive']);
  assert.doesNotMatch(app,/fetch\('\/api\/snapshot/);
  for(const file of ['../server.mjs','../lib/board.mjs'])assert.doesNotMatch(readFileSync(new URL(file,import.meta.url),'utf8'),/setInterval/);
});

test('long text folds to its start, a long list of ids folds to a count, and Show more / Show less open and fold it',()=>{
  const ids=Array.from({length:30},(_,i)=>'c-'+String(i).padStart(12,'0')).join(', ');
  const text='Batch one. PLACES: '+ids+' and then <b>more</b> words.';
  const folded=clampText(text,'k1',new Set());
  assert.match(folded,/^Batch one\. PLACES: <span class="value-list"[^>]*>30 values<\/span>/);
  assert.ok(!folded.includes('c-000000000000'),'no id is shown while folded');
  assert.ok(folded.includes('&lt;b&gt;more&lt;/b&gt;'),'the text around the list stays, escaped');
  assert.match(folded,/data-expand="k1">Show more<\/button>$/);
  const open=clampText(text,'k1',new Set(['k1']));
  assert.ok(open.includes(ids)&&!open.includes('<b>'),'opened: every id, still escaped');
  assert.match(open,/data-expand="k1">Show less<\/button>$/);
  assert.equal(clampText('short <i>x</i>','k2',new Set()),'short &lt;i&gt;x&lt;/i&gt;','short text is only escaped');
  const few='Values: '+Array.from({length:5},(_,i)=>'value-'+i).join(', ');
  assert.equal(clampText(few,'k3',new Set()),few,'a short list stays as written');
  const long='word '.repeat(200);
  const cut=clampText(long,'k4',new Set(),{chars:100});
  assert.ok(cut.startsWith('word word')&&cut.includes('…')&&cut.length<200,'cut at a word, with an ellipsis');
  assert.doesNotMatch(clampText(long,'',null,{chars:100,toggle:false}),/button/,'no button where nothing can open it');
  assert.match(clampText(long,'a"b',new Set(),{chars:100}),/data-expand="a&quot;b"/,'the key is escaped');
});
