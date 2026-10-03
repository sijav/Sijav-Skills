import {classifyWork,facetSupport} from './work-context.mjs';
import {renderAmbientModel} from './ambient-ui.mjs';
import {themeStorageKey,resolveTheme,toggleTheme,readThemePreference,storeThemePreference,createDisplayController} from './display-mode.mjs';
import {sortLane,firstOpen,nextTask,sortQueue,queuePosition,pickerOrder,pickerReady,finishedAt,formatDate,renderFieldDiff,testingState,testingSummary,matchesTestingFilter,supportsTesting,clampText} from './board-ui.mjs';
const $=id=>document.getElementById(id);
const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const settings=JSON.parse($('dashboard-settings').textContent);
// Published through a relay (a team page on a site): the socket's path comes from the host page, which may
// require a sign-in token (window.dashboardAuth) as the socket's first message. The local-only actions go.
const published=settings.published||null;
if(published){for(const id of ['export','print'])if($(id))$(id).hidden=true;$('refresh-note').textContent='Published · Read only · Connecting';}
const projectName=String(settings.project||'').split(/[\\/]/).filter(Boolean).at(-1)||'Project';
document.title=projectName+' · To-do board · read only';
$('brand-name').innerHTML=escape(projectName)+'<small>TO-DO BOARD MONITOR</small>';$('brand-mark').textContent=(projectName.match(/[\p{L}\p{N}]/u)?.[0]||'T').toUpperCase();
$('ambient-brand').innerHTML=escape(projectName)+' <small>RELAX MODE</small>';$('project-eyebrow').textContent=projectName.toUpperCase()+' · READ ONLY';
// Status groups and severity order are todo.py's own policy, read by the picker; never constants here.
const UNKNOWN_RULES={known:false,reason:'Waiting for the board.',severities:[],openStatuses:[],doingStatuses:[],finishedStatuses:[],discardedStatuses:[],otherStatuses:[],satisfying:[],closed:[],statuses:[],rankLabels:null,docs:{}};
// The board whose part of the page is being drawn: each board is described by its own tool, kind and rules.
let drawing=null;
const inBoard=(board,draw)=>{const previous=drawing;drawing=board||null;try{return draw();}finally{drawing=previous;}};
const boardNow=()=>drawing||state.data?.boards?.[0];
const rules=()=>boardNow()?.rules||state.data?.rules||UNKNOWN_RULES;
// The board's own tool and the step that gives its order: todo.py next for the to-do skill's board,
// a loop tool's board_order() for a loop board (whose `next` may also measure the machine, which this page never runs).
const isLoop=()=>(boardNow()?.boardKind??state.data?.server?.kind)==='loop';
const tool=()=>boardNow()?.tool||state.data?.server?.tool||(isLoop()?'the board’s tool':'todo.py');
const nextName=()=>isLoop()?`${tool()}’s board_order()`:`${tool()} next`;
const parents=()=>isLoop()?'blockers':'parents';
const summaryFields=()=>isLoop()?settings.ui.loopSummaryFields:settings.ui.summaryFields;
// Open findings: on a to-do board, unfinished tasks that came out of a roast; on a loop board, the finding rows filed
// against the given items that still hold the finding table's default status (the tool's `resolve` moves them on).
const openFindingsOf=tasks=>tasks.reduce((n,t)=>{const b=sourceFor(t.sourceId);if(b?.boardKind!=='loop')return n+(t.isFinding&&!t.isComplete?1:0);
  const open=b.tableDefaults?.finding?.status;return n+(open==null?0:(t.related?.finding||[]).filter(f=>f.status===open).length);},0);
const known=(value,unknown='—')=>rules().known?value:unknown;
const severityRank=value=>{const list=rules().severities||[],i=list.indexOf(value);return i<0?list.length:i;};
const availableSeverities=()=>[...new Set(state.data.boards.flatMap(b=>b.severities||[]).filter(Boolean))].sort((a,b)=>severityRank(a)-severityRank(b)||a.localeCompare(b));
const date=formatDate;
// A human date with the stored value kept beside it.
const dated=(value,prefix='')=>value==null||value===''?`<span class="muted">${escape(prefix)}Not recorded</span>`:`<span title="${escape('Stored value: '+value)}">${escape(prefix+date(value))}</span>`;
const relative=value=>{if(!value)return 'No timestamp';const mins=Math.max(0,Math.floor((Date.now()-new Date(value))/60000));return mins<1?'Just now':mins<60?`${mins}m ago`:mins<1440?`${Math.floor(mins/60)}h ago`:`${Math.floor(mins/1440)}d ago`;};
let socket=null,reconnectTimer=null,pendingState=null,greetedSocket=null; // the socket whose greeting the page holds
const state={expanded:new Set(),lists:new Map(),summary:null,summaryWanted:null,records:new Map(),rows:new Map(),relaxModel:null,relaxWanted:false,changesLoaded:false,changesLoading:false,data:null,source:'all',view:'report',query:'',status:'all',severity:'all',sort:'next',area:'all',type:'all',topic:'all',phase:'all',tableSelection:{},recordLimits:{},verification:'all',finishedOnly:false,blocked:false,paused:false,selected:null,changes:[],changesComplete:false,taskHistory:{},feedLimit:settings.ui.changePageSize};
let themeStorage;try{themeStorage=window.localStorage;}catch{}
const systemTheme=window.matchMedia('(prefers-color-scheme: dark)');
let savedTheme=readThemePreference(themeStorage),clockTimer=null,previousFocus=null,previousHash=null;
function setTheme(theme,persist=false){
  document.documentElement.dataset.theme=theme;
  document.querySelectorAll('[data-theme-toggle]').forEach(button=>{const dark=theme==='dark';button.textContent=dark?'Light mode':'Dark mode';button.setAttribute('aria-label','Switch to '+(dark?'light':'dark')+' mode');button.setAttribute('aria-pressed',String(dark));});
  if(persist){savedTheme=theme;if(!storeThemePreference(themeStorage,theme))toast('Theme changed. Browser storage is unavailable; it will not be remembered.');}
}
setTheme(resolveTheme(savedTheme,systemTheme.matches));
$('ambient-display').style.setProperty('--relax-cycle',settings.ui.relax.backgroundCycleMs+'ms');
systemTheme.addEventListener('change',event=>{if(!savedTheme)setTheme(resolveTheme(null,event.matches));});
window.addEventListener('storage',event=>{if(event.key===themeStorageKey){savedTheme=readThemePreference(themeStorage);setTheme(resolveTheme(savedTheme,systemTheme.matches));}});
function updateClock(){
  const now=new Date();$('ambient-clock-time').textContent=new Intl.DateTimeFormat(undefined,{hour:'2-digit',minute:'2-digit'}).format(now);$('ambient-clock-time').dateTime=now.toISOString();
  $('ambient-clock-date').textContent=new Intl.DateTimeFormat(undefined,{weekday:'long',month:'long',day:'numeric'}).format(now);
}
function updateAmbient(){
  if(!relax.active)return;
  const openDetails=new Set([...$('ambient-pane').querySelectorAll('details[open][data-ambient-details]')].map(e=>e.dataset.ambientDetails));
  // Asked only once greeted: a relay wants the sign-in first, and a #relax bookmark opens Relax before it.
  if(!state.relaxModel&&!state.relaxWanted&&state.data&&socket?.readyState===WebSocket.OPEN){state.relaxWanted=true;ask('relax').then(model=>{state.relaxWanted=false;state.relaxModel=model;updateAmbient();},()=>{state.relaxWanted=false;});}
  $('ambient-pane').innerHTML=state.relaxModel?renderAmbientModel(state.relaxModel,{connected:socket?.readyState===WebSocket.OPEN,connectionLabel:$('connection-text').textContent,paused:state.paused}):'<div class="ambient-empty"><p>Loading…</p></div>';
  $('ambient-pane').querySelectorAll('details[data-ambient-details]').forEach(e=>e.open=openDetails.has(e.dataset.ambientDetails));
  updateClock();
}
const relax=createDisplayController({element:$('ambient-display'),document,wakeLock:navigator.wakeLock,
  onChange:active=>{
    $('ambient-display').hidden=!active;document.body.classList.toggle('relax-active',active);document.querySelector('.shell').inert=active;
    if(active){
      previousFocus=document.activeElement;previousHash=location.hash==='#relax'||location.hash.startsWith('#task=')?'#'+state.view:location.hash||'#'+state.view;
      if($('task-dialog').open)$('task-dialog').close();history.replaceState(null,'','#relax');
      updateAmbient();$('ambient-exit').focus();
      // This timer updates only the wall clock. Board data arrives via WebSocket.
      clearInterval(clockTimer);clockTimer=setInterval(updateClock,settings.ui.relax.clockTickMs);
    }else{
      clearInterval(clockTimer);clockTimer=null;history.replaceState(null,'',previousHash||'#'+state.view);previousFocus?.focus();
    }
  },
  onStatus:status=>{$('ambient-fullscreen').hidden=status.fullscreen==='full';$('ambient-fullscreen').disabled=status.fullscreen==='requesting';$('ambient-display-status').textContent=(status.fullscreen==='full'?'Full screen':status.fullscreen==='requesting'?'Opening full screen':'Window display')+' · '+(status.wake==='active'?'Screen kept awake':status.wake==='requesting'?'Requesting screen wake lock':'Screen wake lock unavailable');}
});
document.querySelectorAll('[data-theme-toggle]').forEach(button=>button.addEventListener('click',()=>setTheme(toggleTheme(document.documentElement.dataset.theme),true)));
$('relax-open').addEventListener('click',()=>relax.enter());$('ambient-exit').addEventListener('click',()=>relax.exit());$('ambient-fullscreen').addEventListener('click',()=>relax.requestFullscreen());
document.addEventListener('fullscreenchange',relax.fullscreenChanged);document.addEventListener('visibilitychange',relax.visibilityChanged);
window.addEventListener('pagehide',()=>{clearInterval(clockTimer);relax.dispose();});
window.addEventListener('hashchange',()=>{if(location.hash==='#relax'&&!relax.active)relax.enter();else if(location.hash!=='#relax'&&relax.active)relax.exit();});
document.addEventListener('keydown',event=>{
  if(!relax.active)return;
  if(event.key==='Escape'){event.preventDefault();relax.exit();return;}
  if(event.key==='Tab'){
    const controls=[...$('ambient-display').querySelectorAll('button:not([disabled]),summary,a[href]')].filter(e=>e.getClientRects().length);
    const first=controls[0],last=controls.at(-1);
    if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}
    else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}
  }
});
const pill=(value,kind=value)=>`<span class="pill ${escape(kind)}">${escape(value??'not recorded')}</span>`;
const sourceFor=id=>state.data?.boards.find(b=>b.id===id);
const selectedBoards=()=>state.data?.boards.filter(b=>state.source==='all'||b.id===state.source)||[];
const showId=t=>/^\d+$/.test(t.id)?'#'+t.id:t.id;
const taskColumns=b=>b?.tableColumns?.[b.taskTable]||[];
const support=()=>facetSupport(state.data?.boards.flatMap(taskColumns)||[]);
const phaseOf=(b,name)=>(b?.phases||[]).find(p=>p.name===name);
// The board is never sent whole. The page asks the server typed, paginated questions over the
// socket -- a list five at a time, the report's totals, a task's record, a table's rows -- and keeps
// the answers until the filters change or a change is pushed.
const PAGE=5;let requestSeq=0;const waiting=new Map();
// Nothing is asked on a socket before its greeting: a relay wants the sign-in first.
function ask(type,params={}){return new Promise((resolve,reject)=>{if(!socket||socket.readyState!==WebSocket.OPEN||socket!==greetedSocket){reject(Error('The dashboard is not connected.'));return;}const id=++requestSeq;waiting.set(id,{resolve,reject});socket.send(JSON.stringify({...params,id,type}));});}
const filters=()=>({area:state.area,type:state.type,topic:state.topic,phase:state.phase,status:state.status,severity:state.severity,verification:state.verification,finishedOnly:state.finishedOnly,blocked:state.blocked,query:state.query,sort:state.sort});
const listKey=spec=>JSON.stringify([spec.list,spec.board,spec.status??null,filters()]);
function loadList(spec,offset,limit=PAGE){const key=listKey(spec);let entry=state.lists.get(key);if(!entry){entry={spec,items:[],total:null,loading:false,error:null};state.lists.set(key,entry);}if(entry.loading)return;entry.loading=true;entry.error=null;
  ask('list',{...spec,filters:filters(),offset,limit}).then(page=>{entry.loading=false;if(state.lists.get(key)!==entry)return;entry.items=offset===0?page.items:[...entry.items.slice(0,offset),...page.items];entry.total=page.total;render();},error=>{entry.loading=false;entry.error=error.message;render();});}
function listOf(spec){const key=listKey(spec);if(!state.lists.has(key))loadList(spec,0);return state.lists.get(key);}
function loadMore(key){const entry=state.lists.get(key);if(!entry||entry.loading||entry.total==null||entry.items.length>=entry.total)return;loadList(entry.spec,entry.items.length);}
function moreButton(spec,entry){const left=(entry.total??0)-entry.items.length;return left>0?`<button class="quiet-button list-more" data-more-list="${escape(listKey(spec))}">${entry.loading?'Loading…':`Show ${Math.min(PAGE,left)} more · ${left} left`}</button>`:'';}
function listItems(spec,draw,empty){const entry=listOf(spec);if(!entry||entry.total==null)return entry?.error?`<div class="empty">${escape(entry.error)}</div>`:'<div class="empty loading">Loading…</div>';return (entry.items.map(draw).join('')||empty)+moreButton(spec,entry);}
const listTotal=spec=>state.lists.get(listKey(spec))?.total;
// More of a list loads as its "Show more" comes into view, five at a time.
let moreWatcher=null;
function watchMore(){if(typeof IntersectionObserver==='undefined')return;moreWatcher?.disconnect();moreWatcher=new IntersectionObserver(seen=>{for(const e of seen)if(e.isIntersecting)loadMore(e.target.dataset.moreList);},{rootMargin:'200px'});document.querySelectorAll('[data-more-list]').forEach(el=>moreWatcher.observe(el));}
function summaryNow(){const key=JSON.stringify([state.source,filters()]);if(state.summary?.key===key)return state.summary.data;if(state.summaryWanted!==key){state.summaryWanted=key;ask('summary',{board:state.source,filters:filters()}).then(data=>{if(state.summaryWanted!==key)return;state.summary={key,data};state.summaryWanted=null;render();},error=>{state.summaryWanted=null;toast(error.message);});}return null;}
function recordOf(key){if(!state.records.has(key)){state.records.set(key,{loading:true});ask('task',{key}).then(data=>{state.records.set(key,{data,missing:!data});if(state.selected===key)renderDetail();},error=>{state.records.set(key,{error:error.message});if(state.selected===key)renderDetail();});}return state.records.get(key);}
function rowsOf(board,table,limit){const key=JSON.stringify([board,table]),entry=state.rows.get(key);if(!entry||(!entry.loading&&entry.limit<limit)){const fresh={loading:true,limit,data:entry?.data};state.rows.set(key,fresh);ask('rows',{board,table,offset:0,limit}).then(data=>{fresh.loading=false;fresh.data=data;render();},error=>{fresh.loading=false;fresh.error=error.message;render();});}return state.rows.get(key);}
// New filters or another board: every list and the totals are asked again.
function resetQueries(){state.lists.clear();state.summary=null;state.summaryWanted=null;}
// A pushed change: the totals, every list window on the page, the open record and Relax are asked again.
function refreshQueries(keys){
  state.summary=null;state.summaryWanted=null;state.rows.clear();state.relaxModel=null;
  for(const [key,entry] of state.lists){const limit=Math.max(PAGE,entry.items.length);if(entry.loading)continue;entry.loading=true;
    ask('list',{...entry.spec,filters:JSON.parse(key)[3],offset:0,limit}).then(page=>{entry.loading=false;if(state.lists.get(key)!==entry)return;entry.items=page.items;entry.total=page.total;render();},()=>{entry.loading=false;});}
  for(const key of keys)state.records.delete(key);
  for(const key of keys)delete state.taskHistory[key];
}
const testingLabel=(key,value)=>{const info=settings.ui.testingFlags[key];return value==='passed'?info.label:value==='pending'?info.pendingLabel:info.unknownLabel;};
let contextCache=new WeakMap();
const workContext=t=>{if(!contextCache.has(t))contextCache.set(t,classifyWork(t,settings.workClassification||{}));return contextCache.get(t);};
const facetNames={areas:'Area',types:'Work type',topics:'Topic'};
const matchesFacet=(t,facet,value)=>value==='all'||(value==='missing'?!workContext(t)[facet].length:workContext(t)[facet].some(x=>x.label===value));
function facetOptions(facet,s){const f=s?.facets?.[facet];if(!f)return '';return f.labels.map(label=>`<option value="${escape(label)}">${escape(label)}</option>`).join('')+(f.missing?'<option value="missing">Not recorded</option>':'');}
function contextTags(t){const s=facetSupport(taskColumns(sourceFor(t.sourceId)));return Object.entries({areas:'Area',types:'Type',topics:'Topic'}).map(([facet,label])=>workContext(t)[facet].map(x=>`<span class="card-tag context-tag recorded" title="${escape('Recorded field · '+x.evidence.map(e=>e.field+': '+e.text).join(' | '))}">${label} · ${escape(x.label)}</span>`).join('')||(s[facet].length?`<span class="card-tag context-tag unknown">${label} · not recorded</span>`:'')).join('');}
function contextEvidence(t){const context=workContext(t),s=facetSupport(taskColumns(sourceFor(t.sourceId)));return `<div class="detail-section work-context"><h3>Work area, type & topic</h3><p class="note-line">Only stored fields classify work. Nothing is derived from text, and these labels never change the next order.</p>${Object.entries(facetNames).map(([facet,label])=>`<section><h4>${label}</h4>${context[facet].map(x=>`<div class="context-evidence"><strong>${escape(x.label)}</strong> <span class="context-origin">Recorded field</span>${x.evidence.map(e=>`<p><code>${escape(e.field)}</code> <q dir="auto">${escape(e.text)}</q></p>`).join('')}</div>`).join('')||`<p class="muted">${s[facet].length?`Not recorded: ${s[facet].map(f=>escape(f)+' is '+(t.raw[f]==null?'NULL':'empty')).join(', ')}.`:`Not recorded: this board has no ${escape(label.toLowerCase())} field (${escape(facetFieldsText(facet))}).`}</p>`}</section>`).join('')}</div>`;}
const facetFieldsText=facet=>({areas:'area, work_area',types:'type, work_type, category',topics:'tags, domains'})[facet];
function storyBlock(t){const story=t.description;return `<div class="card-story"><span class="story-label">Story</span><p dir="auto">${story==null||story===''?'<span class="muted">No story recorded.</span>':clampText(story,t.key+':story',state.expanded)}</p>${t.why?`<span class="story-label">Why</span><p dir="auto">${clampText(t.why,t.key+':why',state.expanded)}</p>`:''}</div>`;}
const link=t=>`<button class="text-button" data-task="${escape(t.key)}">${escape(showId(t))} · ${escape(t.title)}</button>`;

const statusList=list=>list?.length?list.map(s=>`<code>${escape(s)}</code>`).join(', '):'<span class="muted">none</span>';
function pickerUnavailable(board){return !board.picker?.configured?`<p class="banner error">The tool’s picker is unavailable: ${escape(board.picker?.reason||'not configured')} Unfinished tasks are listed by ID; no next order or status meaning is guessed.</p>`:'';}
/** Why there is no current pick, in the state's own words. Never "nothing is pickable" unless a current result says so. */
function queueStateNote(board){
  const q=board.queue||{},order=pickerOrder(board);
  if(board.available===false)return `<p class="banner error">The board could not be read${board.stale?'; the last successful data is shown':''}. Its next pick is unknown until it can be read again. ${escape(board.error||'')}</p>`;
  if(q.state==='checking')return `<p class="note-line">Running ${escape(nextName())} on a new read-only snapshot.${order&&!order.current?` Until it finishes, cards keep the previous order (checked ${escape(date(order.checkedAt))}) and nothing is marked as next.`:''}</p>`;
  if(q.state==='error'||q.state==='unavailable')return `<p class="banner error">${escape(q.error||'The picker failed.')} No next pick or order is shown; unfinished tasks are listed by ID.</p>`;
  return '';
}
function policyReport(){
  const r=rules();
  if(!r.known)return `<p class="banner">Status meaning is unknown: ${escape(r.reason||`${tool()} has not been read.`)} Statuses are shown as stored; nothing is grouped as Doing or Done.</p>`;
  const docs=Object.entries(r.docs||{});
  return `<details class="next-output"><summary>How ${escape(tool())} decides (read from its code)</summary><p class="note-line">${isLoop()?`Status groups found by asking ${escape(tool())}’s open_items() and board_order() about throwaway rows on a copy: started (not the board’s default for new work) ${statusList(r.doingStatuses)} · offered as new work ${statusList(r.openStatuses)} · satisfies a blocker ${statusList(r.satisfying)}`:`Status groups found by asking ${escape(tool())}’s choose() and open_children() about throwaway rows on a copy: started first ${statusList(r.doingStatuses)} · offered as new work ${statusList(r.openStatuses)} · satisfies a parent ${statusList(r.satisfying)}`} · closed ${statusList(r.closed)}${r.otherStatuses.length?` · other ${statusList(r.otherStatuses)}`:''}. Severity order ${statusList(r.severities)}.</p>${r.rankLabels?`<p class="note-line">${isLoop()?'sort_key()':'by_rule()'} sorts by: ${r.rankLabels.map(l=>`<code>${escape(l)}</code>`).join(' → ')}</p>`:'<p class="note-line">by_rule() does not return a plain tuple, so its sort key is shown without labels.</p>'}${docs.map(([name,doc])=>`<p class="note-line"><code>${escape(name)}()</code></p><pre dir="auto">${escape(doc)}</pre>`).join('')}</details>`;
}
function pickerReport(board){
  const queue=board.queue||{},ready=pickerReady(board),pick=board.head,first=board.firstOpen,pending=queue.state==='checking';
  const phase=queue.currentPhase;
  return `<section class="picker-report"><div class="section-title"><h3>Next · the tool’s own pick</h3><button class="quiet-button" data-recheck-queue="${escape(board.id)}" ${pending||!board.picker?.configured||published?'disabled':''} ${published?'hidden':''}>${pending?`Running ${escape(nextName())}…`:'Run next again'}</button></div>${pickerUnavailable(board)}${queueStateNote(board)}${ready&&queue.boardHadTaskTable===false?'<p class="banner error">This board file has no task table: it was created but nothing has been recorded. That is not evidence that work is finished.</p>':''}${ready?`<div class="picker-summary"><div><span class="story-label">${queue.headKind==='started'?'Resume · already started':'Next'}</span>${pick?link(pick):`<span class="muted">${escape(tool())} picks nothing (see its output)</span>`}</div><div><span class="story-label">${isLoop()?'First startable not started':'First eligible not-started'}</span>${first?link(first):'<span class="muted">None in this result</span>'}</div><div><span class="story-label">Next order</span><strong>${isLoop()?`${queue.startableIds.length} startable · ${queue.rankedIds.length-queue.startableIds.length} waiting`:`${(queue.startedIds||[]).length} started · ${(queue.eligibleIds||[]).length} eligible · ${queue.rankedIds.length-queue.startableIds.length} not pickable`}</strong><small>Checked ${date(queue.checkedAt)}</small></div><div><span class="story-label">Current objective</span>${(board.phases||[]).length?phase?`<strong>${escape(phase.name)}${phase.label?' · '+escape(phase.label):''}</strong><small dir="auto">${escape(phase.goal)}</small>`:'<span class="muted">current_phase() returns none</span>':'<span class="muted">No objectives recorded</span>'}</div></div><details class="next-output" open><summary>${isLoop()?`The order as ${escape(tool())}’s board_order() gives it`:`Exact output of ${escape(tool())} next`}</summary><pre dir="auto">${escape(queue.nextText||'(no output)')}</pre></details>${(queue.schemaAddedOnCopy||[]).length?`<p class="note-line">This board predates part of the tool’s schema (${queue.schemaAddedOnCopy.map(a=>escape(a.table+(a.existingTable?'.'+a.columns.join(', '+a.table+'.'):''))).join('; ')}). The tool would add it on its next write; the picker added it only to an isolated copy. The board file was not changed.</p>`:''}${Object.keys(queue.rankErrors||{}).length?`<p class="note-line">${escape(tool())}’s by_rule() cannot rank ${Object.keys(queue.rankErrors).map(escape).join(', ')} (legacy values); they are listed after ranked tasks and do not affect the pick.</p>`:''}`:''}${policyReport()}<div class="path">${escape(queue.tool?.path||board.picker?.todo||'')}${queue.tool?' · sha256 '+escape(queue.tool.sha256.slice(0,12))+' · Python '+escape(queue.tool.pythonVersion):''}</div></section>`;
}
function queueEvidence(t,evidence){
  const board=sourceFor(t.sourceId),queue=board?.queue||{},position=queuePosition(t,board),ready=pickerReady(board);
  if(t.isComplete)return '';
  const key=ready?evidence?.rankKey:null,keyError=ready?evidence?.rankError:null,reasons=ready?evidence?.deferred||[]:[],labels=rules().rankLabels;
  const groupText={started:`Started: ${nextName()} resumes started work before anything new.`,eligible:isLoop()?`Startable: ${tool()} can start it, in this order.`:`Eligible: ${nextName()} would offer it in this order.`,waiting:'Not pickable now.',previous:'Position in the previous result; the board changed and is being checked again.'};
  const total=pickerOrder(board)?.index.size;
  return `<div class="detail-section"><h3>Next order (${escape(tool())})</h3>${pickerUnavailable(board)}${queueStateNote(board)}${position?`<p><strong>${position.head?(queue.headKind==='started'?'The current pick (resume)':'The current pick'):(position.current?'Position ':'Previous position ')+(position.index+1)+' of '+total}</strong> · ${escape(groupText[position.group])}</p>`:ready?'<p class="muted">Not in the current picker result.</p>':''}${Array.isArray(key)?`<p class="note-line">Sort key from ${isLoop()?'sort_key()':'by_rule()'}: ${key.map((v,i)=>`${labels?.[i]?`<code>${escape(labels[i])}</code>`:'component '+(i+1)} = ${escape(JSON.stringify(v))}`).join(' · ')}</p>`:key!=null?`<p class="note-line">Sort key from ${isLoop()?'sort_key()':'by_rule()'}: ${escape(JSON.stringify(key))}</p>`:''}${keyError?`<p class="note-line">by_rule() cannot rank this task: ${escape(keyError)}</p>`:''}${reasons.map(r=>`<p class="queue-reason${r.kind==='check'&&!r.verified?' unverified':''}">${escape(r.message)}</p>`).join('')}${ready?`<p class="note-line">Checked ${date(queue.checkedAt)}</p>`:''}</div>`;
}
async function recheckQueue(){try{const response=await fetch('api/queue/recheck');if(!response.ok)throw Error('Check failed: '+response.status);const result=await response.json();const boards=result.boards||[];toast(boards.length>1?`Checked ${boards.length} boards again: ${boards.filter(b=>b.queue?.state==='ready').length} ready.`:boards[0]?.queue?.state==='ready'?`Ran ${nextName()} again.`:`${nextName()} is unavailable. See its reason.`);}catch(error){toast(error.message);}}
function recentWork(board,s){
  const r=rules(),transitions=(state.data.statusTransitions||[]),entered=r.known?transitions.find(c=>c.sourceId===board.id&&r.doingStatuses.includes(c.toStatus)):null;
  const closed=s?.perBoard?.[board.id]?.lastClosed;
  if(!r.known)return `<section class="recent-work"><div class="section-title"><h3>Recent work</h3></div><p class="muted">Which statuses mean Doing or Done is unknown until ${escape(tool())} is read.</p></section>`;
  return `<section class="recent-work"><div class="section-title"><h3>Recent work</h3><small>Whole board · observed transitions and stored closure times</small></div><div class="recent-work-grid"><div><span class="story-label">Latest observed move to Doing</span>${entered?`<button class="text-button" data-task="${escape(entered.taskKey)}">${escape(entered.itemId)} · ${escape(entered.title)}</button><small>Observed ${date(entered.observedAt)}</small>`:'<p class="muted">No move to Doing has been observed.</p>'}</div><div><span class="story-label">Most recently done</span>${closed?`${link(closed)}<small>Closed ${dated(closed.closed)}</small>`:s?'<p class="muted">No closure time recorded.</p>':'<p class="muted">Loading…</p>'}</div><div><span class="story-label">Currently recorded Doing</span>${listItems({list:'doing',board:board.id},t=>`${link(t)}<small>${escape(t.status)} · ${t.statusObservedAt?'Move observed '+date(t.statusObservedAt):'No move observed. Created '+date(t.createdAt)} · Worker activity unverified</small>`,'<p class="muted">No tasks are recorded Doing.</p>')}</div></div></section>`;
}
function objectivesReport(board,s){
  const phases=(board.phases||[]).slice().sort((a,b)=>Number(a.position)-Number(b.position)||String(a.name).localeCompare(String(b.name)));
  if(!phases.length)return `<section class="panel"><div class="section-title"><h2>Objectives</h2><small>phase table</small></div><p class="muted">No objectives recorded on this board.</p></section>`;
  const current=board.queue?.currentPhase?.name,n=s?.perBoard?.[board.id],counts=n?.phases||{},loose=n?.loose??0;
  return `<section class="panel"><div class="section-title"><h2>Objectives</h2><small>${phases.length} recorded · in their stored order</small></div><div class="table-wrap"><table><thead><tr><th>Position</th><th>Objective</th><th>Goal</th><th>State</th><th>Open</th><th>Done</th><th>All</th></tr></thead><tbody>${phases.map(p=>{const c=counts[p.name]||{open:0,done:0,all:0};return `<tr><td>${escape(p.position)}</td><td><button class="text-button" data-phase="${escape(p.name)}">${escape(p.name)}</button>${p.label?`<span class="subtext">${escape(p.label)}</span>`:''}</td><td dir="auto">${escape(p.goal)}</td><td>${pill(p.status,'backlog')}${p.name===current?' '+pill('current','in_progress'):''}</td><td>${known(c.open)}</td><td>${known(c.done)}</td><td>${c.all}</td></tr>`;}).join('')}</tbody></table></div><p class="note-line">State is the stored phase status; “current” is what todo.py’s current_phase() returned${current===undefined?' (not known until the picker result is current)':''}.</p>${rules().known&&loose?`<p class="note-line">${loose} unfinished task(s) have no objective.</p>`:''}</section>`;
}
function transitionReport(board){const changes=(state.data.statusTransitions||[]).filter(c=>c.sourceId===board.id);return `<section class="panel transition-report"><div class="section-title"><h2>Observed status transitions</h2><small>${changes.length} observed</small></div>${changes.slice(0,settings.ui.activityLimit).map(c=>`<div class="timeline-row"><div><span class="timeline-label">${escape(c.fromStatus)} → ${escape(c.toStatus)}</span><button class="text-button" data-task="${escape(c.taskKey)}">${escape(c.itemId)} · ${escape(c.title)}</button></div><small>${date(c.observedAt)}</small></div>`).join('')||'<div class="empty">No status changes observed since tracking began.</div>'}<p class="note-line">Times show when this watcher saw the change; the board stores no start time. Full history and field comparisons are in Changes.</p></section>`;}

function databaseRecords(){return '<div class="banner">Every stored row and column is shown here, including rows not attached to a task. Task filters do not hide database records. Rows load a page at a time.</div>'+selectedBoards().map(board=>{
  const names=Object.keys(board.tableCounts||{}),table=names.includes(state.tableSelection[board.id])?state.tableSelection[board.id]:board.taskTable||names[0];
  const key=JSON.stringify([board.id,table]),limit=state.recordLimits[key]??settings.ui.recordPageSize,count=board.tableCounts?.[table]??0;
  const entry=table?rowsOf(board.id,table,Math.min(limit,count)||1):null,rows=entry?.data?.rows||[];
  const schema=board.schema?.find(s=>s.name===table);
  return `<section class="panel database-records" data-record-source="${escape(board.id)}"><div class="section-title"><h2>${escape(board.name)}</h2><small>${names.length} tables</small></div>${!board.available?`<div class="banner error">${escape(board.error)}${board.stale?' · Last successful data':''}</div>`:''}<div class="table-tabs">${names.map(name=>`<button class="quiet-button ${name===table?'active':''}" data-table-source="${escape(board.id)}" data-table-name="${escape(name)}">${escape(name)} · ${board.tableCounts[name]}</button>`).join('')||'<span class="muted">This board file has no tables.</span>'}</div>${schema?`<details class="record-schema"><summary>${escape(table)} schema · ${board.tableColumns?.[table]?.length??0} columns</summary>${fields(schema)}</details>`:''}<p class="note-line">${entry?.data?`Showing ${rows.length} of ${count} rows.`:entry?.error?escape(entry.error):'Loading rows…'} All stored fields are preserved.</p><div class="database-row-list">${rows.map((row,i)=>`<details class="database-row" data-record="${escape(JSON.stringify([board.id,table,board.primaryKeys?.[table]?.length?board.primaryKeys[table].map(k=>row[k]):i]))}"><summary>Row ${i+1}${row.id!=null?' · ID '+escape(row.id):row.task!=null?' · task '+escape(row.task):row.name!=null?' · '+escape(row.name):''}${row.title?' · '+escape(row.title):''}</summary>${fields(row)}</details>`).join('')||(entry?.data?'<div class="empty">No stored rows in this table.</div>':'')}</div>${count>limit?`<div class="record-actions"><button class="button secondary" data-more-records="${escape(key)}">Show ${settings.ui.recordPageSize} more rows</button><button class="quiet-button" data-all-records="${escape(key)}">Show all ${count} rows</button></div>`:''}<div class="path">${escape(board.path)}</div></section>`;
}).join('');}
function testingFlags(t){return Object.entries(settings.ui.testingFlags).filter(([key])=>Object.hasOwn(t.raw,key)).map(([key,info])=>{const value=testingState(t,key);return `<span class="card-tag test-flag ${value}" title="${escape(t.raw[info.evidence]||'No evidence text recorded')}">${escape(testingLabel(key,value))}</span>`;}).join('');}
function testingOptions(s){return Object.entries(settings.ui.testingFlags).map(([key])=>{
  const seen=s?.testingValues?.[key]||{},values=new Set();
  if(seen.recorded){values.add('passed');values.add('pending');}
  if(seen.unknown)values.add('unknown');
  if(state.verification.startsWith(key+':'))values.add(state.verification.slice(key.length+1));
  return [...values].map(value=>`<option value="${escape(key+':'+value)}">${escape(testingLabel(key,value))}</option>`).join('');
}).join('');}
function testingCounts(flag,board,finished){
  if(!flag.supported)return `<span class="verification-unknown">Not recorded: this board has no ${escape(flag.key)} field</span>`;
  const button=(value,count)=>`<button class="verification-count ${value}" data-verification="${escape(flag.key+':'+value)}" data-verification-source="${escape(board.id)}">${count} ${escape(testingLabel(flag.key,value))}</button>`;
  return `<div class="verification-counts">${button('passed',flag.passed)}${button('pending',flag.pending)}${flag.unknown?button('unknown',flag.unknown):''}</div><div class="verification-progress" role="img" aria-label="${escape(flag.label)}: ${flag.passed} of ${finished} done tasks"><span style="width:${finished?flag.passed/finished*100:0}%"></span></div>`;
}
function verificationReport(s){return `<section class="panel verification-report"><div class="section-title"><h2>Completion & testing</h2><small>Whole board</small></div><p class="note-line">Done, tested and E2E tested are independent. Testing counts come only from stored tested / e2e_tested fields; done never implies tested.</p><div class="table-wrap"><table><thead><tr><th>Board</th><th>Done</th>${Object.values(settings.ui.testingFlags).map(info=>`<th>${escape(info.label)}</th>`).join('')}</tr></thead><tbody>${selectedBoards().map(board=>{const summary=s?.perBoard?.[board.id]?.testing;if(!summary)return '';return `<tr><td>${escape(board.name)}${board.stale?'<span class="subtext">Last successful data</span>':''}</td><td>${summary.finished}</td>${summary.flags.map(flag=>`<td>${testingCounts(flag,board,summary.finished)}</td>`).join('')}</tr>`;}).join('')}</tbody></table></div></section>`;}
function boardVerification(board,s){const n=s?.perBoard?.[board.id];if(!n)return '';const summary=n.testing;return `<div class="verification-strip"><div class="verification-stage"><span>Completion</span><strong>${summary.finished} done</strong><small>${n.allUnfinished} unfinished</small></div>${summary.flags.map(flag=>`<div class="verification-stage"><span>${escape(flag.label)}</span>${testingCounts(flag,board,summary.finished)}</div>`).join('')}</div>`;}
function testingEvidence(t){return `<div class="detail-section"><h3>Testing evidence</h3><div class="testing-evidence">${Object.entries(settings.ui.testingFlags).map(([key,info])=>{const value=testingState(t,key),recorded=Object.hasOwn(t.raw,key);return `<section><h4><span class="test-flag ${value}">${escape(testingLabel(key,value))}</span></h4>${!recorded?`<p class="muted">Not recorded: this board has no ${escape(key)} field.</p>`:value==='unknown'?`<p class="muted">Recorded value: ${escape(t.raw[key]??'NULL')}</p>`:''}${Object.hasOwn(t.raw,info.evidence)?`<span class="evidence-label">${escape(info.evidence)}</span><pre dir="auto">${escape(t.raw[info.evidence]??'No evidence text recorded')}</pre>`:''}</section>`;}).join('')}</div></div>`;}

function orderBadge(t){
  const b=sourceFor(t.sourceId),p=queuePosition(t,b);if(!p||t.isComplete)return '';
  const q=b.queue,label=!p.current?`was #${p.index+1} · re-checking`:p.head?(q.headKind==='started'?'Resume · next':'Next'):p.group==='waiting'?'Not pickable':`#${p.index+1} in next order`;
  return `<span class="card-tag order-tag ${p.head?'head':p.group}" title="${p.current?`Position from the current ${escape(nextName())}`:'Position in the previous result; the board changed and is being checked again'}">${escape(label)}</span>`;
}
function relationTags(t){
  const b=sourceFor(t.sourceId),phase=t.phase?phaseOf(b,t.phase):null,tags=[];
  if(t.phase)tags.push(`<span class="card-tag">Objective · ${escape(t.phase)}${phase?.label?' '+escape(phase.label):phase?'':' (not on board)'}</span>`);
  else if((b?.phases||[]).length)tags.push('<span class="card-tag muted-tag">No objective</span>');
  if(t.parentTask)tags.push(`<span class="card-tag">Finding of ${escape(t.parentTask)}</span>`);
  if(t.children.length)tags.push(`<span class="card-tag ${t.openChildren.length?'warn':''}">${t.children.length} finding${t.children.length===1?'':'s'} · ${t.openChildren.length} open</span>`);
  return tags.join('');
}
function taskCard(t,wide=false){return inBoard(sourceFor(t.sourceId),()=>taskCardIn(t,wide||(state.data?.boards?.length||0)>1));}
function taskCardIn(t,wide){
  const b=sourceFor(t.sourceId),time=t.isFinished?(t.closedAt?'Closed '+date(t.raw.closed??t.closedAt):'Closure time not recorded'):t.isComplete?(t.updatedAt?escape(t.status)+' · last updated '+date(t.raw.updated??t.updatedAt):'Closing time not recorded'):t.isDoing?(t.statusObservedAt?'Move to '+t.status+' observed '+date(t.statusObservedAt):'Created '+date(t.createdAt)+' · start not recorded'):'Created '+date(t.createdAt);
  const reasons=t.queueReasons||[];
  return `<article class="task-card ${t.isDoing?'doing':''}" data-card-key="${escape(t.key)}"><div class="card-top"><span class="task-id">${escape(showId(t))}${wide?' · '+escape(b.name):''}</span>${orderBadge(t)}${pill(t.severity)}</div><button class="task-title text-button" data-task="${escape(t.key)}" dir="auto">${escape(t.title)}</button><div class="card-context">${contextTags(t)}${relationTags(t)}</div>${storyBlock(t)}${reasons.length&&!t.isComplete?`<div class="queue-deferred"><span class="story-label">Not pickable by ${escape(nextName())}</span>${reasons.map(r=>`<p dir="auto">${escape(r.message)}</p>`).join('')}</div>`:''}<div class="card-meta">${pill(t.status)}${t.points!=null?`<span>${escape(t.points)} pt</span>`:''}${t.isExplicitlyBlocked?'<span class="card-tag warn">Blocked</span>':t.isWaiting?`<span class="card-tag warn">Waiting on ${parents()}</span>`:''}</div>${testingFlags(t)?`<div class="testing-flags">${testingFlags(t)}</div>`:''}<div class="card-date" title="${escape(t.raw.closed??t.raw.updated??t.raw.created??'')}">${escape(time)}</div>${t.isDoing?'<div class="recorded-status-note">Recorded status · Worker activity unverified</div>':''}<button class="card-open text-button" data-task="${escape(t.key)}">Full record & evidence ↗</button></article>`;
}

function metric(label,value,foot,kind=''){return `<div class="metric ${kind}"><div class="metric-label">${label}</div><div class="metric-number">${value==null?'—':value.toLocaleString()}</div><div class="metric-foot">${foot}</div></div>`;}
/** Nothing has ever been read: show only the reason, never zero counts. */
function neverRead(board){return `<section class="panel"><div class="section-title"><h2>The board could not be read</h2></div><p class="banner error">${escape(board.error||'Unknown error.')}</p><p class="note-line">Nothing is shown from it until a read succeeds; the board file is watched and read again on its next change.</p><div class="path">${escape(board.path)}</div></section>`;}
function eventsFor(tasks){const records=[];for(const t of tasks){const add=(at,label,text='')=>{const when=normalizeTime(at);if(when)records.push({at:when,raw:at,label,text,key:t.key,title:t.title,id:showId(t),sourceId:t.sourceId});};add(t.raw.created,'Task created');add(t.raw.closed,'Task done',t.raw.evidence||'');add(t.raw.updated,'Task last updated');for(const r of t.blocked)add(r.since,'Blocked',r.reason);for(const n of t.notes)add(n.at,'Note recorded',n.text);for(const r of t.roasts)add(r.at,'Roast round '+r.round+' recorded',r.file);}return records.sort((a,b)=>b.at.localeCompare(a.at));}
function normalizeTime(value){if(value==null||value==='')return null;if(typeof value==='number')return new Date(value<1e12?value*1000:value).toISOString();const text=String(value);const d=new Date(/(?:Z|[+-]\d\d:\d\d)$/.test(text)?text:text.replace(' ','T')+'Z');return Number.isNaN(+d)?null:d.toISOString();}
function timeline(events,max=Infinity){return events.slice(0,max).map(e=>`<div class="timeline-row"><div><span class="timeline-label">${escape(e.label)}</span><button class="text-button" data-task="${escape(e.key)}">${escape(e.id)} · ${escape(e.title)}</button>${e.text?`<small class="timeline-text" dir="auto">${escape(String(e.text).slice(0,160))}</small>`:''}</div><small title="${escape(date(e.at)+' · stored '+e.raw)}">${relative(e.at)}</small></div>`).join('')||'<div class="empty">No dated records in this selection.</div>';}
function report(){const boards=selectedBoards();
if(boards.length&&boards.every(b=>b.available===false&&!b.stale))return boards.map(neverRead).join('');
const s=summaryNow();if(!s)return '<div class="empty loading">Loading the report…</div>';
const r=rules(),unknownFoot=`Unknown until ${tool()}’s status policy is read`;
const overview=boards.map(b=>{const n=s.perBoard[b.id]||{shown:0},share=v=>n.shown?v/n.shown*100:0;if(!r.known)return `<tr><td>${escape(b.name)}<span class="subtext">${!b.available?'unavailable':escape(b.journalMode||'')+' journal'}</span></td><td>${n.shown}</td><td>—</td><td>—</td><td>—</td><td>—</td><td>${relative(b.latestRecordedAt)}<span class="subtext">${date(b.latestRecordedAt)}</span></td></tr>`;return `<tr><td><button class="text-button board-name" data-source="${escape(b.id)}" data-go-view="boards">${escape(b.name)}</button><span class="subtext">${!b.available?'unavailable':escape(b.journalMode||'')+' journal'}</span></td><td>${n.shown}</td><td>${n.unfinished}</td><td>${n.doing}</td><td>${n.done}<div class="status-distribution" aria-label="${n.done} done, ${n.doing} doing, ${n.otherUnfinished} other unfinished"><span class="segment closed" style="width:${share(n.done)}%"></span><span class="segment doing" style="width:${share(n.doing)}%"></span><span class="segment open" style="width:${share(n.otherUnfinished)}%"></span></div></td><td>${n.openFindings}</td><td>${relative(b.latestRecordedAt)}<span class="subtext">${date(b.latestRecordedAt)}</span></td></tr>`;}).join('');
// Without the tool's policy "unfinished" is unknown, so severity counts cover every task and say so.
const sevTitle=r.known?'Unfinished by severity':'All tasks by severity (unfinished unknown)';
const sev=availableSeverities().map(v=>{const n=s.severities[v]||0;return `<div class="severity-row">${pill(v)}<div class="bar-track"><div class="bar ${escape(v)}" style="width:${s.severityBase?n/s.severityBase*100:0}%"></div></div><b>${n}</b></div>`;}).join('');
const registerRow=t=>`<tr><td>${escape(showId(t))}${orderBadge(t)}</td><td class="register-title"><button class="text-button" data-task="${escape(t.key)}" dir="auto">${escape(t.title)}</button>${storyBlock(t)}${t.isExplicitlyBlocked?'<span class="subtext">Blocked</span>':t.isWaiting?`<span class="subtext">Waiting on unfinished ${parents()}</span>`:''}</td><td><div class="card-context">${contextTags(t)}</div>${t.phase?`<span class="subtext">${escape(t.phase)}</span>`:''}</td><td>${pill(t.status)}<div class="testing-flags">${testingFlags(t)}</div></td><td>${pill(t.severity)}</td><td>${escape(t.points??'not recorded')}</td><td>${t.openChildren.length} open / ${t.children.length} findings<span class="subtext">${t.roastsCount} roasts · ${t.notesCount} notes</span></td><td>${date(t.lastRecordedAt)}</td></tr>`;
const registerSpec={list:'register',board:state.source},register=listOf(registerSpec);
const registerBody=!register||register.total==null?'<tr><td colspan="8"><div class="empty loading">Loading…</div></td></tr>':(register.items.map(registerRow).join('')||'<tr><td colspan="8"><div class="empty">No matching tasks. The board may be empty or your filters exclude its tasks.</div></td></tr>')+(register.items.length<register.total?`<tr><td colspan="8">${moreButton(registerSpec,register)}</td></tr>`:'');
return `${!r.known?`<p class="banner">${escape('Status meaning is unknown: '+(r.reason||''))} Counts that depend on it are shown as —.</p>`:''}<div class="metrics">${metric('All tasks',s.shown,'Every task row on the board')}${metric('Unfinished',known(s.open,null),r.known?`${s.blocked} blocked · ${s.waiting} waiting on ${parents()}`:unknownFoot)}${metric('Recorded Doing',known(s.doing,null),r.known?(isLoop()?`Statuses ${tool()} counts as started: `:`Statuses ${tool()} resumes first: `)+escape(r.doingStatuses.join(', ')):unknownFoot,'doing')}${metric('Done',known(s.done,null),r.known?(isLoop()?'Statuses that satisfy a blocker: ':'Statuses that satisfy a parent and close a finding: ')+escape(r.finishedStatuses.join(', ')):unknownFoot)}${metric('Open findings',known(s.findings,null),r.known?(isLoop()?'Findings filed against items and not yet resolved':'Unfinished tasks that came out of a roast'):unknownFoot,'findings')}</div><div class="section-title"><h2>Work recorded as Doing</h2><small>${known(s.doing+' tasks')}</small></div><div class="doing-grid">${r.known?listItems({list:'doing',board:state.source},t=>taskCard(t),'<div class="empty">No tasks are recorded as Doing in this selection.</div>'):`<div class="empty">Which statuses mean Doing is unknown until ${tool()} is read.</div>`}</div>${boards.map(b=>inBoard(b,()=>pickerReport(b)+recentWork(b,s)+objectivesReport(b,s)+transitionReport(b))).join('')}${verificationReport(s)}<section class="panel"><div class="section-title"><h2>Board overview</h2><small>The resolved board</small></div><div class="table-wrap"><table><thead><tr><th>Board</th><th>Total</th><th>Unfinished</th><th>Doing</th><th>Done</th><th>Open findings</th><th>Latest dated record</th></tr></thead><tbody>${overview}</tbody></table></div></section><div class="report-grid"><section class="panel"><div class="section-title"><h2>Latest recorded activity</h2><small>Stored timestamps</small></div>${timeline(s.events.map(e=>({...e,id:showId(e)})),settings.ui.activityLimit)}<p class="note-line">Creation, done, update, block, note and roast times stored on the board.</p></section><section class="panel"><div class="section-title"><h2>${sevTitle}</h2><small>${s.severityBase} tasks</small></div>${sev}<div class="section-title" style="margin-top:30px"><h2>Recorded statuses</h2></div><div class="relations">${Object.entries(s.statuses).map(([v,n])=>`<button class="quiet-button" data-status="${escape(v)}">${escape(v)} · ${n}</button>`).join('')||'<span class="muted">No tasks</span>'}</div></section></div><section class="panel"><div class="section-title"><h2>Complete task register</h2><small>${s.shown} tasks · five at a time · Open a row for every stored field</small></div><div class="table-wrap"><table class="register-table"><thead><tr><th>ID</th><th>Task</th><th>Area / Objective</th><th>Status / Testing</th><th>Severity</th><th>Points</th><th>Findings / Roasts / Notes</th><th>Last dated record</th></tr></thead><tbody>${registerBody}</tbody></table></div></section>${sourceInformation(boards)}`;
}
function sourceInformation(boards){return `<section class="panel"><div class="section-title"><h2>Board file & reading status</h2><small>Read only</small></div>${boards.map(b=>{const s=b.server||state.data.server||{},w=b.watchState||state.data.watchState||{};return `<div class="source-info"><p><strong>${escape(b.name)}</strong> ${b.available?pill('Readable','done'):pill('Read failed','critical')} ${b.stale?pill('Last successful data','high'):''} ${pill((b.journalMode||'unknown')+' journal','backlog')}</p><p class="muted">${escape(b.note)}</p><div class="path">${escape(b.path)}</div><p class="note-line">Project ${escape(s.project)} · history kept in ${escape(s.dataDir)} · Last successful read ${date(b.lastSuccessfulReadAt)} · Board file modified ${date(b.file?.modifiedAt)}${b.wal?' · WAL '+escape(b.wal.bytes)+' bytes, modified '+date(b.wal.modifiedAt):''} · ${b.total} tasks · ${b.counts.notes??0} notes · ${b.counts.reviews??0} roast rounds</p><p class="note-line">Watching ${w.board?.active?escape(w.board.directory)+' for '+escape(w.board.files.join(', ')):'NOT ACTIVE · '+escape(w.board?.error)}${w.tool?.file?` · tool ${w.tool.active?escape(w.tool.file):'NOT WATCHED · '+escape(w.tool.error)}`:''} · ${state.data.ignoredEvents||0} lock-only notifications ignored</p>${b.error?`<p class="banner error">${escape(b.error)}</p>`:''}<details><summary class="note-line">Database schema and table counts</summary>${(b.schema||[]).map(table=>`<details><summary>${escape(table.name)} · ${b.tableCounts?.[table.name]??0} rows</summary>${fields(table)}</details>`).join('')}${(b.indexes||[]).map(x=>`<details><summary>${escape(x.name)} · ${escape(x.tbl_name)}</summary>${fields(x)}</details>`).join('')}</details></div>`;}).join('')}</section>`;}

function boardsView(){
  const shown=selectedBoards(),s=summaryNow();
  if(shown.length>1)return `<div class="board-order-guide"><strong>Next order from each board’s own tool</strong><span>Each board below lists its unfinished lanes in its own tool’s order, each with its reasons, and its closed lanes most recent first.</span><span>Area and objective labels are stored fields; they do not reorder anything. Cards load five at a time as a lane scrolls.</span></div>`+boardSections(shown,s);
  return inBoard(shown[0],()=>{const r=rules();return `<div class="board-order-guide"><strong>Next order from ${escape(tool())}</strong><span>${isLoop()?`Unfinished lanes follow ${escape(tool())}’s own board_order(): each item it can start, in order, then the items it cannot start, each with its reason.`:`Unfinished lanes follow the order of ${escape(tool())}’s own picks: started work, then each task choose() would pick next, then tasks it cannot pick, each with its reason.`}</span><span>${r.known?`Lanes of statuses ${escape(tool())} treats as closed show the most recent first.`:'Status meaning is unknown, so every lane is in ID order.'} Area and objective labels are stored fields; they do not reorder anything. Cards load five at a time as a lane scrolls.</span></div>`+boardSections(shown,s);});
}
function boardSections(shown,s){
  return shown.map(b=>inBoard(b,()=>{
    const r=rules();
    if(b.available===false&&!b.stale)return neverRead(b);
    const n=s?.perBoard?.[b.id],present=new Set([...(b.supportedStatuses||[]),...Object.keys(b.statuses||{})]);
    const statuses=[...new Set([...r.doingStatuses,...r.openStatuses,...(r.otherStatuses||[]),...r.finishedStatuses,...r.discardedStatuses,...present])].filter(v=>present.has(v));
    const order=pickerOrder(b);
    return `<section class="board-section"><div class="board-section-header"><h2>${escape(b.name)}</h2><span class="muted">${n?n.shown:'…'} / ${b.total} tasks</span></div><p class="board-section-note">${escape(b.note)}${!b.available?' · Read failed; last successful data shown.':''}</p>${!order?`<p class="banner">No current ${escape(tool())} result: unfinished lanes are in ID order, which is not the next order.</p>`:!order.current?`<p class="banner">The board changed; ${escape(tool())} is being run again. Cards show the previous order (checked ${escape(date(order.checkedAt))}) and nothing is marked as next.</p>`:''}<p class="status-support">Statuses the board and ${escape(tool())} allow: ${statuses.map(v=>pill(v)).join(' ')||'No status values recorded'}</p>${pickerReport(b)}${recentWork(b,s)}${boardVerification(b,s)}${b.total===0?`<div class="empty">${b.available?(b.taskTable?'This board has no tasks. All tables were read successfully.':'This board file has no task table: nothing has been recorded yet. That is not evidence that work is finished.'):'Board unavailable. '+escape(b.error)}</div>`:`<div class="board-columns" style="--columns:${statuses.length}">${statuses.map(status=>{
      const spec={list:'lane',board:b.id,status},cards=listItems(spec,t=>taskCard(t),'<div class="empty">No matching tasks</div>');
      return `<div class="lane"><div class="lane-header">${escape(status)}<span>${listTotal(spec)??'…'}</span></div><div class="lane-cards" data-scroll="${escape(b.id+'-'+status)}">${cards}</div></div>`;
    }).join('')}</div>`}${transitionReport(b)}</section>`;
  })).join('');
}

function changesView(){const changes=state.changes.filter(c=>(state.source==='all'||c.sourceId===state.source)&&(!state.query||JSON.stringify(c).toLocaleLowerCase().includes(state.query.toLocaleLowerCase())));const more=changes.length>state.feedLimit||!state.changesComplete;return `<div class="banner">Changes observed by this dashboard since <strong>${date(state.data.trackingSince)}</strong>. Each entry compares two successful reads of the board. Edits between reads may be combined. The history is kept outside the project, in <code>${escape(state.data.server?.dataDir||'')}</code>, and continues across restarts.</div><section class="panel"><div class="section-title"><h2>Observed board changes</h2><small>${state.data.changeCount} recorded · ${state.changes.length} loaded${state.query?` · ${changes.length} match`:''}</small></div>${state.query&&!state.changesComplete?'<p class="note-line">Search covers loaded changes; load all to search the whole history.</p>':''}${changes.slice(0,state.feedLimit).map(c=>`<div class="change-row"><div class="change-time" title="${escape(c.at)}"><strong>${new Date(c.at).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'})}</strong>${new Date(c.at).toLocaleDateString()}</div><div><div class="change-title">${pill(c.kind,c.kind==='removed'?'critical':c.kind==='added'?'doing':'medium')}<span class="task-id">${escape(c.table)}${c.itemId?' · '+escape(c.itemId):''}</span></div>${c.itemId?`<button class="text-button" data-task="${escape(c.sourceId+':'+c.itemId)}" dir="auto">${escape(c.title)}</button>`:`<strong>${escape(c.title)}</strong>`}<p>${c.before?.status!==c.after?.status&&c.before?.status&&c.after?.status?`Status: ${escape(c.before.status)} → ${escape(c.after.status)}`:`${escape(c.fields.length)} ${c.kind==='updated'?'changed':'recorded'} fields`}</p><div class="change-fields">${escape(c.fields.join(', '))}</div>${comparison(c)}</div></div>`).join('')||'<div class="empty">No changes observed yet. The dashboard is watching the board file.</div>'}${more?`<div class="record-actions"><button class="button secondary" data-more="changes">Show ${settings.ui.changePageSize} more changes</button>${!state.changesComplete?'<button class="quiet-button" data-all-changes>Load all history</button>':''}</div>`:''}</section>`;}
/** Older history is fetched on request; nothing is silently dropped. */
async function loadChanges(all=false){
  const oldest=state.changes.at(-1)?.seq;
  const page=await ask('changes',all?{limit:'all'}:{limit:settings.ui.changePageSize,...(oldest!=null?{before:oldest}:{})}),seen=new Set(state.changes.map(c=>c.seq));
  state.changes=[...state.changes,...page.changes.filter(c=>!seen.has(c.seq))].sort((a,b)=>b.seq-a.seq);
  state.changesComplete=all||page.complete||state.changes.length>=page.total;state.changesLoaded=true;
}
async function loadTaskHistory(key){
  state.taskHistory[key]=(await ask('changes',{item:key})).changes;
}
function comparison(c,withDate=false){return `<details data-diff="${escape(c.id)}"><summary class="compare-summary">${withDate?date(c.at)+' · '+escape(c.table)+' · ':''}Compare changed fields <span class="diff-legend"><span>− removed</span><span>+ added</span></span></summary>${renderFieldDiff(c.before,c.after,settings.ui)}</details>`;}
function expandedDiffs(){return new Set([...document.querySelectorAll('[data-diff][open]')].map(el=>el.dataset.diff));}
function restoreDiffs(ids){document.querySelectorAll('[data-diff]').forEach(el=>{el.open=ids.has(el.dataset.diff);});}
function render(){if(state.offline){$('content').innerHTML=`<div class="banner error">${escape(state.offline)}</div>`;return;}if(!state.data)return;const expanded=expandedDiffs();const openedRecords=new Set([...document.querySelectorAll('[data-record][open]')].map(el=>el.dataset.record));const savedScroll=new Map([...document.querySelectorAll('[data-scroll]')].map(el=>[el.dataset.scroll,el.scrollTop]));const s=state.view==='changes'||state.view==='data'?state.summary?.data:summaryNow();
  if(state.view==='changes'&&!state.changesLoaded&&!state.changesLoading){state.changesLoading=true;loadChanges().then(()=>{state.changesLoading=false;render();},error=>{state.changesLoading=false;toast(error.message);});}
  $('source-nav').innerHTML=state.data.boards.map(b=>`<button class="source-button active" data-source="${escape(b.id)}"><span class="source-icon">${escape(b.short)}</span><span class="source-text">${escape(b.name)}<small>${!b.available?'Read failed':b.total===0?'Empty board':'Read successfully'}</small></span><span class="source-number">${b.total}</span></button>`).join('');
  $('source-health').textContent=state.data.boards.filter(b=>b.available).length+'/'+state.data.boards.length;
  const viewLabels={report:'Full report',boards:'Full board',changes:'Changes',data:'Database records'};$('breadcrumb').innerHTML=`${escape(projectName)} <span>/</span> ${viewLabels[state.view]}`;
  $('page-title').textContent={report:'The whole picture.',boards:'Every task, in next order.',changes:'Follow the changes.',data:'Every stored record.'}[state.view];
  $('page-subtitle').textContent={report:'What is in progress, what the tool picks next, what was done, and every task’s full record.',boards:'The full board. Open any card to read its complete record.',changes:'A local record of what changed while the dashboard was watching.',data:'Every table, row and field of the board, including unlinked records.'}[state.view];
  document.querySelectorAll('[data-view]').forEach(el=>el.classList.toggle('active',el.dataset.view===state.view));
  $('filters').hidden=state.view==='data';const taskFilters=state.view!=='changes',facets=support();
  for(const [id,facet,label] of [['area','areas','All areas'],['type','types','All types'],['topic','topics','All topics']]){$(id).closest('label').hidden=!taskFilters||!facets[facet].length;$(id).innerHTML='<option value="all">'+label+'</option>'+facetOptions(facet,s);$(id).value=state[id];}
  const phases=state.data.boards.flatMap(b=>(b.phases||[]).slice().sort((x,y)=>Number(x.position)-Number(y.position)));$('phase').closest('label').hidden=!taskFilters||!phases.length;$('phase').innerHTML='<option value="all">All objectives</option>'+phases.map(p=>`<option value="${escape(p.name)}">${escape(p.name)}${p.label?' · '+escape(p.label):''}</option>`).join('')+'<option value="none">No objective</option>';$('phase').value=state.phase;
  for(const id of ['status','severity','verification','finished-only','blocked'])$(id).closest('label').hidden=!taskFilters;
  $('verification').innerHTML='<option value="all">All testing states</option>'+testingOptions(s);$('verification').value=state.verification;$('finished-only').checked=state.finishedOnly;$('sort').closest('label').hidden=state.view!=='report';const nextOption=$('sort').querySelector('option[value="next"]');if(nextOption){const tools=[...new Set((state.data?.boards||[]).map(b=>b.tool||state.data?.server?.tool))];nextOption.textContent=tools.length>1?'Next order (each board’s tool)':`Next order (${tool()})`;}
  const statuses=[...new Set(state.data.boards.flatMap(b=>b.supportedStatuses||[]))];$('severity').innerHTML='<option value="all">All severities</option>'+availableSeverities().map(s=>`<option value="${escape(s)}">${escape(s)}</option>`).join('');$('severity').value=state.severity;$('status').innerHTML='<option value="all">All statuses</option>'+statuses.map(s=>`<option value="${escape(s)}">${escape(s)}</option>`).join('');$('status').value=state.status;
  const hasFilters=state.query||state.area!=='all'||state.type!=='all'||state.topic!=='all'||state.phase!=='all'||state.status!=='all'||state.severity!=='all'||state.verification!=='all'||state.finishedOnly||state.blocked;$('filter-summary').hidden=!hasFilters||['changes','data'].includes(state.view);$('filter-summary').textContent=`Showing ${s?s.shown:'…'} of ${selectedBoards().reduce((n,b)=>n+b.total,0)} tasks. Task lists follow these filters; the next pick and testing summaries cover the whole board.`;
  $('content').innerHTML=state.view==='report'?report():state.view==='boards'?boardsView():state.view==='data'?databaseRecords():changesView();watchMore();
  document.querySelectorAll('[data-scroll]').forEach(el=>{el.scrollTop=savedScroll.get(el.dataset.scroll)||0;});$('change-count').textContent=state.data.changeCount;$('checked-at').innerHTML='Last read: '+dated(state.data.checkedAt);
  const errors=[...state.data.boards.filter(b=>!b.available).map(b=>b.name+': '+b.error),...Object.entries(state.data.watchState).filter(([,w])=>w.error).map(([id,w])=>(id==='tool'?'Tool':'Board')+' watcher failed · '+w.error),state.data.persistenceError].filter(Boolean);$('error-banner').hidden=!errors.length;$('error-banner').textContent=errors.join(' · ');
  if(state.selected)renderDetail();restoreDiffs(expanded);document.querySelectorAll('[data-record]').forEach(el=>el.open=openedRecords.has(el.dataset.record));updateAmbient();}
function fieldValue(key,value){if(value&&typeof value==='object')return JSON.stringify(value);return settings.ui.testingFlags[key]?testingLabel(key,testingState({raw:{[key]:value}},key)):settings.ui.dateFields.includes(key)?date(value):value==null?'NULL':value===''?'(empty)':String(value);}
const rawLine=(key,value)=>settings.ui.dateFields.includes(key)&&value!=null&&value!==''?`<small class="raw-value">stored: ${escape(value)}</small>`:'';
// A stable key for a stored value, so a value someone opened stays open across live updates.
const textKey=text=>{let h=5381;for(let i=0;i<text.length;i++)h=(h*33+text.charCodeAt(i))>>>0;return text.length+'-'+h.toString(36);};
function fields(raw){return `<div class="raw-fields">${Object.entries(raw).map(([key,val])=>{
  const scalar=settings.ui.compactFields.includes(key)||settings.ui.dateFields.includes(key);
  const shown=String(fieldValue(key,val)??'');
  const content=val&&typeof val==='object'&&!Array.isArray(val)&&!('$blob' in val)?fields(val):`<pre dir="auto">${clampText(shown,'field:'+key+':'+textKey(shown),state.expanded,{chars:1500})}</pre>${rawLine(key,val)}`;
  return scalar?`<div class="simple-field"><span>${escape(key)}</span>${content}</div>`:`<details ${settings.ui.longTextFields.includes(key)&&val?'open':''}><summary>${escape(key)}${val==null?' · NULL':val===''?' · empty':''}</summary>${content}</details>`;
}).join('')}</div>`;}
function taskSummary(t){
  const columns=taskColumns(sourceFor(t.sourceId));
  return `<div class="task-summary">${summaryFields().map(key=>{const present=Object.hasOwn(t.raw,key),value=t.raw[key];return `<div class="summary-field ${key==='close_output'?'summary-output':''}"><span>${escape(key.replaceAll('_',' '))}</span>${!present?`<pre class="muted">Not recorded${columns.length?' · no such field':''}</pre>`:value==null||value===''?'<pre class="muted">Not recorded</pre>':`<pre dir="auto">${escape(fieldValue(key,value))}</pre>${rawLine(key,value)}`}</div>`;}).join('')}</div>`;
}
const textSections=()=>isLoop()?[['story','Story'],['why','Why'],['exit_cmd','Exit command'],['close_did','Done at close']]
  :[['descr','Story / description'],['why','Why · who wants what'],['exit_cond','Exit condition'],['evidence','Evidence recorded at done'],['reason','Dropped because']];
function longText(t){return `<div class="detail-section"><h3>Story &amp; conditions</h3>${textSections().filter(([key])=>Object.hasOwn(t.raw,key)).map(([key,label])=>`<section class="long-field"><h4>${escape(label)} <code>${escape(key)}</code></h4>${t.raw[key]==null||t.raw[key]===''?'<p class="muted">Not recorded.</p>':`<pre dir="auto">${clampText(t.raw[key],'detail:'+t.key+':'+key,state.expanded,{chars:1500})}</pre>`}</section>`).join('')}</div>`;}
function relatedRecords(records,label,empty='None recorded.'){return `<div class="detail-section"><h3>${label} (${records.length})</h3><div class="related-records">${records.map((r,i)=>`<details ${records.length<=3?'open':''}><summary>${escape(r.round!=null?'Round '+r.round:'Record '+(i+1))}${r.at||r.since?' · '+escape(date(r.at??r.since)):''}${r.text?' · '+escape(String(r.text).substring(0,100)):r.reason?' · '+escape(String(r.reason).substring(0,100)):r.file?' · '+escape(r.file):''}</summary>${fields(r)}</details>`).join('')||`<p class="muted">${escape(empty)}</p>`}</div></div>`;}
function renderDetail(){const key=state.selected;if(!key)return;const board=sourceFor(key.slice(0,key.indexOf(':')));return inBoard(board,()=>renderDetailIn(key,board));}
function renderDetailIn(key,b){const expanded=expandedDiffs();const scroll=$('task-dialog').scrollTop;const entry=recordOf(key),rec=entry?.data;
  if(!rec){$('detail-content').innerHTML=`<div class="drawer-body"><h2 id="detail-title">${entry?.missing?'Task no longer present':entry?.error?'The task’s record could not be read':'Loading the task’s record…'}</h2><p class="muted">${entry?.missing?'Look in Changes for its previous record.':escape(entry?.error||'')}</p></div>`;return;}
  const t=rec.task;b=sourceFor(t.sourceId)||b;$('detail-source').textContent=b.name+' / '+showId(t);
  const links=ids=>ids.map(id=>{const other=rec.links?.[id];return other&&!other.missing?`<button class="relation" data-task="${escape(other.key)}">${escape(showId(other))} · ${escape(other.status)} · ${escape(other.title)}</button>`:`<span class="relation">${escape(id)} · not on this board</span>`;}).join('');
  const shown=new Set([...summaryFields(),...textSections().map(([k])=>k),'title']);
  const rest=Object.fromEntries(Object.entries(t.raw).filter(([key])=>!shown.has(key)&&!Object.values(settings.ui.testingFlags).some(info=>info.evidence===key)&&!Object.hasOwn(settings.ui.testingFlags,key)));
  const full=state.taskHistory[t.key];
  if(!full&&!state.historyWanted?.has(t.key)){(state.historyWanted??=new Set()).add(t.key);loadTaskHistory(t.key).then(()=>{state.historyWanted.delete(t.key);if(state.selected===t.key)renderDetail();},error=>{state.historyWanted.delete(t.key);toast(error.message);});}
  const loaded=state.changes.filter(c=>c.sourceId===b.id&&c.itemId===t.id);
  const ownChanges=full?[...new Map([...loaded,...full].map(c=>[c.seq,c])).values()].sort((x,y)=>y.seq-x.seq):loaded,historyComplete=!!full;
  const position=queuePosition(t,b);
  const toolChildren=rec.evidence?.children;
  $('detail-content').innerHTML=`<div class="drawer-body"><div class="detail-badges">${pill(t.status)}${pill(t.severity)}${t.isExplicitlyBlocked?pill('Blocked','high'):''}${t.isWaiting?pill(`Waiting on ${parents()}`,'high'):''}${orderBadge(t)}</div><h2 id="detail-title" dir="auto">${escape(t.title)}</h2>${t.isDoing?`<div class="banner">Recorded status: ${escape(t.status)}. The board does not record whether anyone is working on it now.</div>`:''}${!b.available?`<div class="banner error">Read failed. Showing the last successful record: ${escape(b.error)}</div>`:''}${taskSummary(t)}${longText(t)}${queueEvidence(t,rec.evidence)}
  <div class="detail-section"><h3>Blocking</h3>${t.blocked.length?t.blocked.map(r=>`<p class="queue-reason" dir="auto">Blocked: ${escape(r.reason)}</p><p class="note-line">Since ${dated(r.since)}</p>`).join(''):'<p class="muted">No explicit block recorded.</p>'}<h4>${isLoop()?'Items this waits on (dep)':'Parents that must be done first'} (${t.dependencies.length})</h4><div class="relations">${links(t.dependencies)||'<span class="muted">None recorded.</span>'}</div>${t.unresolved.length&&!t.isComplete?`<p class="note-line">${t.unresolved.length} of them ${t.unresolved.length===1?'is':'are'} not in a status that satisfies a ${isLoop()?'blocker':'parent'} (${escape(rules().satisfying.join(', ')||'none known')})${position?.current&&position.group==='started'?`; ${escape(tool())} still resumes this started task`:''}.</p>`:''}<h4>${isLoop()?'Items waiting on this':'Tasks waiting on this'} (${t.dependents.length})</h4><div class="relations">${links(t.dependents)||'<span class="muted">None recorded.</span>'}</div></div>
  ${isLoop()?'':`<div class="detail-section"><h3>Findings &amp; provenance</h3>${t.parentTask?`<p class="note-line">Came out of roasting</p><div class="relations">${links([String(t.parentTask)])}</div>`:'<p class="muted">Not a finding of another task.</p>'}<h4>Findings filed from this task (${t.children.length} · ${t.openChildren.length} open)</h4><div class="relations">${links(t.children)||'<span class="muted">None recorded.</span>'}</div>${t.children.length&&rules().known?`<p class="note-line">${t.openChildren.length?`${t.openChildren.length} still open by todo.py’s open_children().`:'None is open by todo.py’s open_children().'}${toolChildren?' (from todo.py children())':''}</p>`:''}</div>`}
  ${isLoop()?'':relatedRecords(t.notes,'Notes')+relatedRecords(t.roasts,'Roast rounds')}${Object.entries(t.related).map(([name,rows])=>relatedRecords(rows,'Table '+escape(name))).join('')}
  ${isLoop()?'':contextEvidence(t)}${testingEvidence(t)}
  <div class="detail-section"><h3>Other stored fields</h3>${Object.keys(rest).length?fields(rest):'<p class="muted">Every stored field is shown above.</p>'}</div>
  <div class="detail-section"><h3>Dated records</h3><div class="detail-history">${timeline(eventsFor([t]),Infinity)}</div></div><div class="detail-section"><h3>Observed changes (${ownChanges.length}${historyComplete?'':' loaded'})</h3>${!historyComplete?`<p class="note-line">Loading this task’s history… <button class="quiet-button" data-task-history="${escape(t.key)}">Load this task’s full history</button></p>`:''}${ownChanges.map(c=>comparison(c,true)).join('')||'<p class="muted">No changes observed since monitoring started.</p>'}</div><div class="path">${escape(b.path)}</div></div>`;
  $('task-dialog').scrollTop=scroll;restoreDiffs(expanded);}
function openTask(key){state.selected=key;renderDetail();if(!$('task-dialog').open){$('task-dialog').showModal();}$('task-dialog').scrollTop=0;history.replaceState(null,'','#task='+encodeURIComponent(key));}
function closeTask(){state.selected=null;if(!relax.active)history.replaceState(null,'','#'+state.view);}
function setView(view){state.view=view;history.replaceState(null,'','#'+view);render();}
let toastTimer=null;
function toast(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,settings.ui.toastMs);}
function connection(text,kind=''){$('connection-text').textContent=text;$('connection-dot').className='status-dot '+kind;updateAmbient();}
function updateConnection(){
  if(state.refused){connection(state.refused,'offline');return;}
  if(state.paused){connection('View paused','paused');return;}
  if(!socket||socket.readyState!==WebSocket.OPEN){connection('Disconnected · reconnecting','offline');return;}
  if(state.offline){connection('Connected · boards offline','offline');return;}
  if(!state.data){connection('Connected · loading');return;}
  const watching=state.data.watchState.board?.active,failures=state.data.boards.filter(b=>!b.available).length;
  connection(failures?'Board unreadable':watching?'Live · file watcher':'Not watching the board',failures||!watching?'offline':'');
  $('refresh-note').textContent=published?'Published · Read only · connected':'Local files · Read only · WebSocket connected';
}
// The first message after (re)connecting gives each board's details and nothing per task; a change
// gives the boards whose details changed, the keys of the tasks that changed and new history.
function absorbHello(message){state.data=message;state.changes=[];state.changesComplete=false;state.changesLoaded=false;state.changesLoading=false;state.taskHistory={};state.lists.clear();state.summary=null;state.summaryWanted=null;state.records.clear();state.rows.clear();state.relaxModel=null;}
function absorbChange(message){
  const {boards=[],keys=[],changes=[],type,...top}=message;Object.assign(state.data,top);
  for(const b of boards){const i=state.data.boards.findIndex(x=>x.id===b.id);if(i<0)state.data.boards.push(b);else state.data.boards[i]=b;}
  if(changes.length){const seen=new Set(state.changes.map(c=>c.seq));state.changes=[...changes.filter(c=>!seen.has(c.seq)),...state.changes];}
  refreshQueries(keys);
}
function applyMessage(message){
  if(message.type==='hello')absorbHello(message);else absorbChange(message);
  contextCache=new WeakMap();render();updateConnection();
  if(!state.selected&&initialHash.startsWith('#task=')){openTask(decodeURIComponent(initialHash.slice(6)));initialHash='';}
}
function connectLive(){
  clearTimeout(reconnectTimer);
  // The old socket's questions are settled here, since its close handler is dropped.
  if(socket){socket.onclose=null;socket.onmessage=null;socket.close();for(const asked of waiting.values())asked.reject(Error('The dashboard reconnected.'));waiting.clear();}
  // Reconnect after a refusal: the page signs in again (the host page forgot the refused token).
  if(state.refused){state.refused=state.offline=null;$('content').innerHTML='<div class="loading">Signing in…</div>';}
  socket=new WebSocket((location.protocol==='https:'?'wss:':'ws:')+'//'+location.host+(published?.livePath||'/api/live'));
  const opened=socket;
  socket.onopen=()=>{updateConnection();if(published?.auth&&window.dashboardAuth)window.dashboardAuth().then(token=>{if(socket===opened&&socket.readyState===WebSocket.OPEN)socket.send(JSON.stringify({type:'auth',token}));},error=>connection('Not signed in: '+error.message,'offline'));};
  socket.onmessage=event=>{
    const message=JSON.parse(event.data);
    if(message.type==='reply'){const asked=waiting.get(message.id);waiting.delete(message.id);if(!asked)return;if(message.error)asked.reject(Error(message.error));else asked.resolve(message.data);return;}
    if(message.type==='offline'){state.offline=message.reason||'The boards are offline.';render();if($('task-dialog').open)$('detail-content').innerHTML=`<div class="banner error">${escape(state.offline)}</div>`+$('detail-content').innerHTML;updateConnection();return;} // an open record says so too
    // Refused (this account may not see the boards): said once, with no reconnecting. The host page forgets
    // the refused token, so Reconnect (or a reload) signs in again, with another account if need be.
    if(message.type==='refused'){state.refused=state.offline=message.reason||'This account cannot see the boards.';render();socket.onclose=null;socket.close();window.dashboardAuth?.forget?.();updateConnection();return;}
    if(message.type==='hello'){state.offline=state.refused=null;pendingState=null;greetedSocket=socket;} // a greeting replaces every change queued before it
    if(message.type!=='hello'&&message.type!=='changed')return;
    if(state.paused&&message.type==='changed'){(pendingState??=[]).push(message);return;} // every change is kept and applied on resume
    applyMessage(message);
  };
  // Reconnecting is the only timed action; the server greets each (re)connect.
  socket.onclose=()=>{for(const asked of waiting.values())asked.reject(Error('The dashboard disconnected.'));waiting.clear();updateConnection();$('refresh-note').textContent=published?'Published · Read only · disconnected':'Local files · Read only · WebSocket disconnected';reconnectTimer=setTimeout(connectLive,settings.transport.reconnectMs);};
  socket.onerror=()=>connection('Connection failed','offline');
  updateConnection();
}

function clearTaskFilters(){state.query='';state.area='all';state.type='all';state.topic='all';state.phase='all';state.status='all';state.severity='all';state.verification='all';state.finishedOnly=false;state.blocked=false;state.sort='next';$('search').value='';$('severity').value='all';$('blocked').checked=false;$('sort').value='next';}

document.addEventListener('click',e=>{const more=e.target.closest('[data-more-list]');if(more){loadMore(more.dataset.moreList);return;}const expand=e.target.closest('[data-expand]');if(expand){const key=expand.dataset.expand;if(state.expanded.has(key))state.expanded.delete(key);else state.expanded.add(key);render();if($('task-dialog').open)renderDetail();return;}
  if(e.target.closest('[data-recheck-queue]')){recheckQueue();return;}
  const history=e.target.closest('[data-task-history]');if(history){loadTaskHistory(history.dataset.taskHistory).then(render,error=>toast(error.message));return;}
  if(e.target.closest('[data-all-changes]')){loadChanges(true).then(render,error=>toast(error.message));return;}
  if(e.target.closest('[data-more]')){state.feedLimit+=settings.ui.changePageSize;if(state.changes.length<state.feedLimit&&!state.changesComplete)loadChanges().then(render,error=>toast(error.message));else render();return;}const table=e.target.closest('[data-table-name]');if(table){state.tableSelection[table.dataset.tableSource]=table.dataset.tableName;render();return;}const moreRecords=e.target.closest('[data-more-records]');if(moreRecords){const key=moreRecords.dataset.moreRecords;state.recordLimits[key]=(state.recordLimits[key]??settings.ui.recordPageSize)+settings.ui.recordPageSize;render();return;}const allRecords=e.target.closest('[data-all-records]');if(allRecords){const key=allRecords.dataset.allRecords,[id,table]=JSON.parse(key);state.recordLimits[key]=sourceFor(id).tableCounts[table];render();return;}const verification=e.target.closest('[data-verification]');if(verification){clearTaskFilters();state.source=verification.dataset.verificationSource;state.verification=verification.dataset.verification;state.finishedOnly=true;resetQueries();setView('boards');return;}const task=e.target.closest('[data-task]');if(task){openTask(task.dataset.task);return;}const phase=e.target.closest('[data-phase]');if(phase){state.phase=phase.dataset.phase;resetQueries();setView('boards');return;}const source=e.target.closest('[data-source]');if(source){state.source=source.dataset.source;if(source.dataset.goView)state.view=source.dataset.goView;render();return;}const view=e.target.closest('[data-view]');if(view){setView(view.dataset.view);return;}const status=e.target.closest('[data-status]');if(status){state.status=status.dataset.status;$('status').value=state.status;resetQueries();setView('boards');return;}});
$('search').addEventListener('input',e=>{state.query=e.target.value;resetQueries();render();});for(const field of ['area','type','topic','phase','status','severity','sort','verification'])$(field).addEventListener('change',e=>{state[field]=e.target.value;resetQueries();render();});$('finished-only').addEventListener('change',e=>{state.finishedOnly=e.target.checked;resetQueries();render();});$('blocked').addEventListener('change',e=>{state.blocked=e.target.checked;resetQueries();render();});$('clear').addEventListener('click',()=>{clearTaskFilters();resetQueries();render();});$('pause').addEventListener('click',()=>{state.paused=!state.paused;$('pause').textContent=state.paused?'Resume':'Pause';$('pause').setAttribute('aria-label',state.paused?'Resume live view':'Pause live view');if(!state.paused&&pendingState){const queued=pendingState;pendingState=null;for(const message of queued)absorbChange(message);render();}updateConnection();if(state.paused)toast('View paused. File watching and change recording continue.');});$('refresh').addEventListener('click',connectLive);$('print').addEventListener('click',()=>{setView('report');window.print();});$('close-dialog').addEventListener('click',()=>$('task-dialog').close());$('task-dialog').addEventListener('close',closeTask);$('task-dialog').addEventListener('click',e=>{if(e.target===$('task-dialog')&&e.clientX<$('task-dialog').getBoundingClientRect().left)$('task-dialog').close();});
let initialHash=location.hash;if(['#boards','#changes','#data'].includes(initialHash))state.view=initialHash.slice(1);connectLive();if(initialHash==='#relax')relax.enter();
