// Unfinished work is ordered ONLY by the tool's own picker result (rankedIds:
// started, then eligible, then not pickable, as todo.py's choose() picked
// them). There is no dashboard ranking rule.
//   * current result ('ready'): its order, its pick and its badges;
//   * board changed and being re-checked: the previous order, labelled as such,
//     with no pick and no "Next" badge;
//   * failed, unreadable or no picker: stored ID order, and the page says why.
// Status meaning (doing, done, ...) is the tool's policy in board.rules, never
// a constant here; with no known policy nothing is grouped.
const idNumber=id=>Number(String(id).replace(/^\D+/,''))||0;
const lexical=(a,b)=>String(a).localeCompare(String(b));
const byId=(a,b)=>idNumber(a.id)-idNumber(b.id)||lexical(a.id,b.id);
export const pickerReady=board=>board?.available!==false&&!board?.stale&&board?.queue?.state==='ready'&&!board.queue.stale;
const orders=new WeakMap();
/** {ids, current} for the order to show, or null when no trustworthy order exists. */
export function pickerOrder(board){
  const q=board?.queue;if(!q||board.available===false)return null;
  const source=pickerReady(board)?{ids:q.rankedIds,current:true}:q.state==='checking'&&q.previous?.rankedIds?{ids:q.previous.rankedIds,current:false,checkedAt:q.previous.checkedAt}:null;
  if(!source||!Array.isArray(source.ids))return null;
  if(!orders.has(source.ids))orders.set(source.ids,new Map(source.ids.map((id,index)=>[String(id),index])));
  return {...source,index:orders.get(source.ids)};
}
export const hasPickerOrder=board=>!!pickerOrder(board);
export function byPicker(a,b,board){
  const order=pickerOrder(board);if(!order)return byId(a,b);
  const ai=order.index.get(String(a.id)),bi=order.index.get(String(b.id));
  return ai==null?(bi==null?byId(a,b):1):bi==null?-1:ai-bi;
}
const rulesOf=board=>board?.rules||{known:false,finishedStatuses:[],discardedStatuses:[],openStatuses:[],doingStatuses:[]};
// Completion recency: the stored closure time, then a closed task's last update.
export const finishedAt=task=>task.closedAt||(task.isComplete?task.updatedAt:null)||null;
const byRecentFinish=(a,b)=>(finishedAt(b)||'').localeCompare(finishedAt(a)||'')||idNumber(b.id)-idNumber(a.id)||lexical(b.id,a.id);
export function sortLane(tasks,status,board,rules=rulesOf(board)) {
  const rows=tasks.slice();
  if(rules.known&&[...rules.finishedStatuses,...rules.discardedStatuses].includes(status))return rows.sort(byRecentFinish);
  return rows.sort((a,b)=>byPicker(a,b,board));
}
/** The tool's pick, only from a current picker result. */
export function nextTask(board) {
  return pickerReady(board)&&board.queue.headId!=null?board.tasks.find(t=>t.id===String(board.queue.headId))||null:null;
}
/** The first backlog task the tool would pick once started work is finished. */
export function firstOpen(board,rules=rulesOf(board)) {
  if(!pickerReady(board))return null;
  return (board.queue.eligibleIds||[]).map(id=>board.tasks.find(t=>t.id===String(id))).find(t=>t&&rules.openStatuses.includes(t.status))||null;
}
export function sortQueue(tasks,board) {
  return tasks.slice().sort((a,b)=>Number(!!a.isComplete)-Number(!!b.isComplete)||(a.isComplete?byRecentFinish(a,b):byPicker(a,b,board)));
}
/** Position in the shown order. `current` false means the previous result; `head` only for a current pick. */
export function queuePosition(task,board) {
  const order=pickerOrder(board);if(!order)return null;
  const i=order.index.get(String(task.id));if(i==null)return null;
  const q=board.queue;
  if(!order.current)return {index:i,current:false,group:'previous',head:false};
  return {index:i,current:true,group:(q.startedIds||[]).includes(String(task.id))?'started':(q.eligibleIds||[]).includes(String(task.id))?'eligible':'waiting',head:String(q.headId)===String(task.id)};
}

// Verification is independent of lifecycle and of the other verification flag.
// An absent/null field cannot establish that the task failed or is untested.
export function testingState(task,key) {
  const value=task.raw?.[key];
  return value===1||value===true?'passed':value===0||value===false?'pending':'unknown';
}
export function supportsTesting(board,key) {
  const columns=board.tableColumns?.[board.taskTable];
  return columns?columns.includes(key):board.tasks.some(t=>Object.hasOwn(t.raw,key));
}
export function testingSummary(board,tasks,definitions) {
  const finished=tasks.filter(t=>t.isFinished);
  return {finished:finished.length,flags:Object.entries(definitions).map(([key,info])=>{
    const counts={passed:0,pending:0,unknown:0};
    for(const t of finished)counts[testingState(t,key)]++;
    return {key,...info,supported:supportsTesting(board,key),...counts};
  })};
}
export function matchesTestingFilter(task,filter) {
  if(filter==='all')return true;
  const separator=filter.lastIndexOf(':');
  return testingState(task,filter.slice(0,separator))===filter.slice(separator+1);
}
export function formatDate(value) {
  if(value==null||value==='')return 'Not recorded';
  const numeric=typeof value==='number'||/^\d+(\.\d+)?$/.test(String(value));
  const text=String(value);
  const d=new Date(numeric?Number(value)*(Number(value)<1e12?1000:1):/(?:Z|[+-]\d\d:\d\d)$/.test(text)?text:text.replace(' ','T')+'Z');
  if(Number.isNaN(+d))return text;
  const options={year:'numeric',month:'short',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',timeZoneName:'short'};
  if(d.getMilliseconds())options.fractionalSecondDigits=3;
  return new Intl.DateTimeFormat(undefined,options).format(d);
}

// Myers line diff. Bound the edit search for large, entirely replaced output;
// that fallback still preserves every removed/added line without inventing matches.
export function diffLines(before,after,maxEdits=Infinity) {
  const a=String(before).split('\n'),b=String(after).split('\n');
  let prefix=0;while(prefix<a.length&&prefix<b.length&&a[prefix]===b[prefix])prefix++;
  let suffix=0;while(suffix<a.length-prefix&&suffix<b.length-prefix&&a[a.length-1-suffix]===b[b.length-1-suffix])suffix++;
  const left=a.slice(prefix,a.length-suffix),right=b.slice(prefix,b.length-suffix);
  const v=new Map([[1,0]]),trace=[];let operations=null;
  outer:for(let d=0;d<=Math.min(left.length+right.length,maxEdits);d++) {
    trace.push(new Map(v));
    for(let k=-d;k<=d;k+=2) {
      const prev=k===-d||(k!==d&&(v.get(k-1)??-Infinity)<(v.get(k+1)??-Infinity));
      let x=prev?(v.get(k+1)??0):(v.get(k-1)??0)+1,y=x-k;
      while(x<left.length&&y<right.length&&left[x]===right[y]){x++;y++;}
      v.set(k,x);
      if(x>=left.length&&y>=right.length) {
        let xx=left.length,yy=right.length;const reversed=[];
        for(let depth=trace.length-1;depth>=0;depth--) {
          const previous=trace[depth],diagonal=xx-yy;
          const prevDiagonal=diagonal===-depth||(diagonal!==depth&&(previous.get(diagonal-1)??-Infinity)<(previous.get(diagonal+1)??-Infinity))?diagonal+1:diagonal-1;
          const prevX=previous.get(prevDiagonal)??0,prevY=prevX-prevDiagonal;
          while(xx>prevX&&yy>prevY){reversed.push({kind:'context',text:left[--xx]});yy--;}
          if(depth===0)break;
          if(xx===prevX)reversed.push({kind:'add',text:right[--yy]});else reversed.push({kind:'remove',text:left[--xx]});
        }
        operations=reversed.reverse();break outer;
      }
    }
  }
  if(!operations)operations=[...left.map(text=>({kind:'remove',text})),...right.map(text=>({kind:'add',text}))];
  const rows=[...a.slice(0,prefix).map(text=>({kind:'context',text})),...operations,...a.slice(a.length-suffix).map(text=>({kind:'context',text}))];
  let oldLine=0,newLine=0;
  return rows.map(row=>({...row,oldLine:row.kind==='add'?null:++oldLine,newLine:row.kind==='remove'?null:++newLine}));
}
const valueType=(value,exists)=>!exists?'absent':value===null?'null':Array.isArray(value)?'list':typeof value==='object'?'record':typeof value==='string'?'text':typeof value;
const valueText=(value,exists)=>!exists?'(field absent)':value===null?'NULL':Array.isArray(value)?`${value.length} entries`:typeof value==='object'?`${Object.keys(value).length} fields`:String(value);
function flatten(value,path,out) {
  if(value&&typeof value==='object'&&Object.keys(value).length) {
    for(const [key,item]of Object.entries(value)){
      const segment=/^[\w-]+$/.test(key)?key:`[${JSON.stringify(key)}]`;
      flatten(item,Array.isArray(value)?`${path}[${key}]`:path?`${path}${segment.startsWith('[')?'':'.'}${segment}`:segment,out);
    }
  } else out.set(path,value);
}
export function changedFields(before,after,maxEdits=Infinity,dateFields=[]) {
  const old=new Map(),now=new Map();
  if(before!=null)flatten(before,'',old);if(after!=null)flatten(after,'',now);
  const fields=[];
  for(const key of new Set([...old.keys(),...now.keys()])) {
    const hasBefore=old.has(key),hasAfter=now.has(key),a=old.get(key),b=now.get(key);
    if(hasBefore===hasAfter&&JSON.stringify(a)===JSON.stringify(b))continue;
    const oldType=valueType(a,hasBefore),newType=valueType(b,hasAfter);
    const dated=dateFields.includes(key.split('.').at(-1));
    const oldText=dated&&hasBefore&&a!=null?formatDate(a):valueText(a,hasBefore),newText=dated&&hasAfter&&b!=null?formatDate(b):valueText(b,hasAfter);
    let rows=!hasBefore?newText.split('\n').map((text,i)=>({kind:'add',text,oldLine:null,newLine:i+1})):!hasAfter?oldText.split('\n').map((text,i)=>({kind:'remove',text,oldLine:i+1,newLine:null})):diffLines(oldText,newText,maxEdits);
    if(oldType!==newType&&oldText===newText)rows=[{kind:'remove',text:oldText,oldLine:1,newLine:null},{kind:'add',text:newText,oldLine:null,newLine:1}];
    fields.push({key:key||'record',kind:!hasBefore?'added':!hasAfter?'removed':'changed',oldType,newType,rows});
  }
  return fields;
}
const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const friendly=key=>key.replaceAll('_',' ').replace(/^\w/,c=>c.toUpperCase());
function contextRows(rows,context) {
  const visible=new Set();
  rows.forEach((row,i)=>{if(row.kind!=='context')for(let j=Math.max(0,i-context);j<=Math.min(rows.length-1,i+context);j++)visible.add(j);});
  const out=[];for(let i=0;i<rows.length;) {
    if(visible.has(i)){out.push(rows[i++]);continue;}
    const start=i;while(i<rows.length&&!visible.has(i))i++;
    out.push({kind:'skip',count:i-start});
  }
  return out;
}
export function renderFieldDiff(before,after,settings) {
  const fields=changedFields(before,after,settings.diffMaxEdits,settings.dateFields);
  return `<div class="field-diffs">${fields.map(field=>{
    const rows=contextRows(field.rows,settings.diffContextLines),oldCount=field.rows.filter(r=>r.oldLine!=null).length,newCount=field.rows.filter(r=>r.newLine!=null).length;
    return `<section class="field-diff"><header class="field-diff-head"><strong>${escape(friendly(field.key))}</strong><code>${escape(field.key)}</code><span class="diff-kind ${field.kind}">${field.kind}</span><small>${escape(field.oldType===field.newType?field.oldType:field.oldType+' → '+field.newType)}</small></header><div class="diff-scroll"><table class="unified-diff" aria-label="${escape(field.key)} changed lines"><thead><tr><th colspan="4">@@ -${oldCount?1:0},${oldCount} +${newCount?1:0},${newCount} @@</th></tr></thead><tbody>${rows.map(row=>row.kind==='skip'?`<tr class="diff-skip"><td colspan="4">${row.count} unchanged line${row.count===1?'':'s'}</td></tr>`:`<tr class="diff-${row.kind}"><td class="line-number">${row.oldLine??''}</td><td class="line-number">${row.newLine??''}</td><td class="diff-symbol" aria-label="${row.kind==='remove'?'Removed':row.kind==='add'?'Added':'Unchanged'}">${row.kind==='remove'?'−':row.kind==='add'?'+':' '}</td><td class="diff-code"><pre dir="auto">${escape(row.text.replaceAll('\r','␍'))||'<span class="empty-line">(empty line)</span>'}</pre></td></tr>`).join('')}</tbody></table></div></section>`;
  }).join('')||'<p class="muted">No changed fields.</p>'}</div>`;
}
