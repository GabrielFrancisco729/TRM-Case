// LeadLoop pipeline UI: Workflow page, stage/lead overlays, AD inbox, manager loop, roadmap.
// Depends on helpers from app.js ($, esc, num, pct, date, btn, kpi, api, toast, app, getUser).
const wf={stages:null,executors:{},aiConfigured:false,run:null,running:false,tracing:false,filter:'ALL',search:''};
const EXEC_META={integration:{icon:'⇄',label:'Integration',short:'Integration'},automation:{icon:'⚙',label:'Workflow rule',short:'Rule'},ai:{icon:'✦',label:'AI · Claude',short:'AI'},human:{icon:'●',label:'Human',short:'Human'}};
const STATUS_LABEL={ok:'Passed',fixed:'Fixed by LeadLoop',merged:'Merged',ai:'AI step',hold:'Held for a person',route:'Routed to CS',flag:'Flagged',skip:'Waiting',pending:'Waiting on AD'};
const execChip=(e,small)=>`<span class="wf-chip wf-${e}${small?' sm':''}"><b>${EXEC_META[e].icon}</b>${EXEC_META[e].label}</span>`;
const money=v=>v>=1e6?`$${(v/1e6).toFixed(1)}M`:`$${Math.round(v/1e3)}k`;
// Snake layout: row 1 left→right, row 2 right→left, marketing node closes the loop back to Capture.
const WF_GRID={capture:[1,1],identity:[1,2],enrich:[1,3],qualify:[1,4],validate:[1,5],route:[2,5],notify:[2,4],workspace:[2,3],feedback:[2,2]};

async function workflowPage(){
 setNav('workflow');
 const el=$('workflowView');
 if(!wf.stages){el.innerHTML='<div class="panel"><div class="panel-body">Loading workflow…</div></div>';
  try{const [s,latest]=await Promise.all([api('/api/pipeline/stages'),api('/api/pipeline/latest')]);wf.stages=s.stages;wf.executors=s.executors;wf.aiConfigured=s.ai_configured;wf.run=latest.run}catch(e){toast(e.message,true);return}}
 const r=wf.run;
 el.innerHTML=`<div class="dashboard-heading"><div><div class="eyebrow">02 · THE WORKFLOW · RUNS ON THE REAL Q3 FORM STREAM</div><h2>From a website form to an AD’s list</h2><p>Nine stages, each run by a workflow rule, an integration, AI or a person. Click any box for the full mechanics. Then run the 547 Q3 form submissions through it and watch it build every AD’s list.</p></div><div class="wf-legend">${Object.keys(EXEC_META).map(k=>execChip(k)).join('')}</div></div>
 <section class="wf-canvas" id="wfCanvas"><svg class="wf-wires" id="wfWires"></svg>
  <div class="wf-entry">Lead fills a form on the website</div>
  ${wf.stages.map(s=>{const [row,col]=WF_GRID[s.id];const st=r?.stages.find(x=>x.id===s.id);return `<button type="button" class="wf-box" id="wfbox-${s.id}" data-stage="${s.id}" style="grid-row:${row+1};grid-column:${col}"><span class="wf-num">${s.n}<em>details ↗</em></span><strong>${esc(s.title)}</strong><span class="wf-execs">${s.executors.map(e=>`<i class="wf-tag wf-${e}">${EXEC_META[e].icon} ${EXEC_META[e].short}</i>`).join('')}</span><span class="wf-count" id="wfcount-${s.id}">${st?num(st.in):''}</span></button>`}).join('')}
  <button type="button" class="wf-box wf-marketing" id="wfbox-marketing" data-stage="marketing" style="grid-row:3;grid-column:1"><span class="wf-num">↺</span><strong>Marketing Ops</strong><span class="wf-execs"><i class="wf-tag wf-human">● Human</i></span><span class="wf-count">rules · reasons</span></button>
 </section>
 <div class="wf-runbar"><div><strong>Q3 replay · 547 form submissions, Jul 1 – Sep 30</strong><span>${wf.aiConfigured?'AI steps call Claude (cached per lead).':'AI steps run on an offline stand-in with the same output contract. Add ANTHROPIC_API_KEY to run them on Claude.'}</span></div><div class="button-row">${btn(r?'↻ Run again':'▶ Run the pipeline','btn-primary','wfRun')}</div></div>
 <div class="wf-trace-row"><span class="micro muted">Trace one lead through every box:</span>${(r?.examples||[]).map(x=>`<button type="button" class="wf-example" data-trace="${esc(x.sid)}">${esc(x.label)}</button>`).join('')||'<span class="micro muted">run the pipeline first</span>'}</div>
 <div id="wfTraceLog"></div>
 <div id="wfResults">${r?wfResults(r):''}</div>`;
 document.querySelectorAll('[data-stage]').forEach(b=>b.onclick=()=>openStage(b.dataset.stage));
 $('wfRun').onclick=runPipeline;
 bindResults();
 requestAnimationFrame(drawWires);
}
window.addEventListener('resize',()=>{if(app.view==='workflow')drawWires()});

function drawWires(){
 const svg=$('wfWires'),canvas=$('wfCanvas');if(!svg||!canvas)return;
 const c=canvas.getBoundingClientRect();svg.setAttribute('viewBox',`0 0 ${c.width} ${c.height}`);
 const box=id=>{const b=$('wfbox-'+id).getBoundingClientRect();return {l:b.left-c.left,r:b.right-c.left,t:b.top-c.top,b:b.bottom-c.top,cx:(b.left+b.right)/2-c.left,cy:(b.top+b.bottom)/2-c.top}};
 const order=['capture','identity','enrich','qualify','validate','route','notify','workspace','feedback','marketing'];
 let paths='';
 for(let i=0;i<order.length-1;i++){const a=box(order[i]),b=box(order[i+1]);let d;
  if(Math.abs(a.cy-b.cy)<4)d=a.cx<b.cx?`M${a.r} ${a.cy} L${b.l-6} ${b.cy}`:`M${a.l} ${a.cy} L${b.r+6} ${b.cy}`;
  else if(Math.abs(a.cx-b.cx)<4)d=`M${a.cx} ${a.b} L${b.cx} ${b.t-6}`;
  else d=`M${a.l} ${a.cy} L${b.cx} ${a.cy} L${b.cx} ${b.t-6}`;
  paths+=`<path class="wf-wire" id="wire-${order[i]}" d="${d}" marker-end="url(#wfArrow)"/>`}
 const m=box('marketing'),cap=box('capture');
 paths+=`<path class="wf-wire wf-loop" d="M${m.cx} ${m.t} L${cap.cx} ${cap.b+6}" marker-end="url(#wfArrow)"/>`;
 const e=$('wfCanvas').querySelector('.wf-entry').getBoundingClientRect();
 paths+=`<path class="wf-wire" d="M${(e.left+e.right)/2-c.left} ${e.bottom-c.top} L${cap.cx} ${cap.t-6}" marker-end="url(#wfArrow)"/>`;
 svg.innerHTML=`<defs><marker id="wfArrow" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="#8fb3bd"/></marker></defs>${paths}`;
}

const sleep=ms=>new Promise(r=>setTimeout(r,ms));
function lit(id,on,cls='active'){const b=$('wfbox-'+id);if(b)b.classList.toggle(cls,on)}

async function runPipeline(){
 if(wf.running)return;wf.running=true;$('wfRun').disabled=true;$('wfRun').textContent='Running…';
 const call=api('/api/pipeline/run',{method:'POST',body:JSON.stringify({ai:'auto'})});
 for(const s of wf.stages){lit(s.id,true);$('wfcount-'+s.id).textContent='…';await sleep(260);lit(s.id,false);lit(s.id,true,'done')}
 try{wf.run=await call}catch(e){toast(e.message,true);wf.running=false;workflowPage();return}
 for(const s of wf.stages){const st=wf.run.stages.find(x=>x.id===s.id);const el=$('wfcount-'+s.id);let n=0;const target=st.in,step=Math.max(1,Math.round(target/12));
  while(n<target){n=Math.min(target,n+step);el.textContent=num(n);await sleep(14)}el.textContent=num(target)}
 wf.running=false;toast(`Pipeline finished: ${num(wf.run.outcomes['AD inbox']||0)} leads in AD lists, ${num(wf.run.outcomes['Marketing review']||0)} held for review.`);
 workflowPage();
}

function wfResults(r){
 const o=r.outcomes,v=r.value,f=r.feedback;
 const top=r.ad_lists.find(a=>a.hand)||r.ad_lists[0];const lead=top?.top[0];
 return `<div class="wf-section-title"><div class="eyebrow">RESULT OF THE RUN · ${esc(r.ran_at.replace('T',' ').slice(0,16))} UTC</div><h3>Where the 547 submissions ended up</h3></div>
 <div class="wf-outcomes">${[['AD inbox','In an AD’s list','teal'],['CS handoff','Customer → CS','blue'],['Marketing review','Held for Marketing Ops','amber'],['Manager triage','Manager triage','amber'],['Merged','Merged repeat fills','grey']].map(([k,l,c])=>`<button type="button" class="wf-out ${c}" data-outfilter="${k}"><strong>${num(o[k]||0)}</strong><span>${l}</span></button>`).join('')}</div>
 <section class="panel"><div class="panel-head"><div><h3>AD lists built by this run</h3><p>Every lead has a named, active owner. Hand-raisers first. Open the workspace as any AD to work the list.</p></div></div><div class="wf-ads">${r.ad_lists.map(a=>`<div class="wf-ad"><div class="wf-ad-head"><strong>${esc(a.name)}</strong><span>${esc(a.ad)} · ${esc(a.region)}</span></div><div class="wf-ad-stats"><span><b>${a.count}</b> leads</span><span><b>${a.hand}</b> hand-raisers</span></div>${a.top.map(t=>`<button type="button" class="wf-ad-lead" data-trace-lead="${esc(t.sid)}"><span>${esc(t.name)}</span><em>${esc(t.form)} · ${t.score??'—'}</em></button>`).join('')}<button type="button" class="btn btn-soft wf-open-ad" data-open-ad="${esc(a.ad)}">Open as ${esc(a.name.split(' ')[0])} →</button></div>`).join('')}</div></section>
 <div class="split wf-split">
  <section class="panel"><div class="panel-head"><div><h3>Q3 as it happened vs. with LeadLoop</h3><p>Same 547 submissions, same people, same dates.</p></div></div><div class="table-wrap"><table class="data-table wf-compare"><thead><tr><th>What</th><th>Q3 actual</th><th>With LeadLoop</th></tr></thead><tbody>${r.compare.map(c=>`<tr><td><strong>${esc(c.metric)}</strong><div class="sub">${esc(c.note)}</div></td><td class="wf-before">${esc(c.actual)}</td><td class="wf-after">${esc(c.leadloop)}</td></tr>`).join('')}</tbody></table></div></section>
  <section class="panel"><div class="panel-head"><div><h3>Alerts that would have fired</h3><p>In reality these were found in October, by this case. LeadLoop raises them on the first event.</p></div></div><div class="panel-body wf-monitors">${r.monitors.map(m=>`<div class="wf-mon"><div class="wf-mon-date">${esc(date(m.date))}</div><div><strong>${esc(m.alert)}</strong><span>${esc(m.change)}</span><em>${esc(m.action)} · ${num(m.count)} leads affected</em></div></div>`).join('')}</div>
   <div class="wf-value"><small>PIPELINE AT RISK IN Q3 (ORDER OF MAGNITUDE)</small><strong>≈ ${money(v.pipeline_at_risk)}</strong><span>${v.lost_hand_raisers} lost hand-raisers + ${v.delivery_risk_leads} leads with routing/alert failures × observed opportunity rate (${v.rate_hand_raiser}% hand-raisers, ${v.rate_content}% content) × average New Business ${money(v.avg_new_business)}.</span><em>${esc(v.basis)}</em></div></section>
 </div>
 <div class="split wf-split">
  <section class="panel"><div class="panel-head"><div><h3>The alert the AD actually gets</h3><p>Slack message with buttons: the AD acts without opening another tool. Clicks are recorded as that AD.</p></div>${tag('simulated')}</div><div class="panel-body">${lead?slackCard(lead,top):'<div class="empty">No lead.</div>'}</div></section>
  <section class="panel"><div class="panel-head"><div><h3>Feedback loop · live</h3><p>Every AD decision lands here as structured data for marketing.</p></div></div><div class="panel-body" id="wfFeedback">${feedbackBlock(f)}</div></section>
 </div>
 <section class="panel"><div class="panel-head"><div><h3>Every lead, every step</h3><p>Click a row to see its full trace: what each stage decided, who executed it and why.</p></div><span class="pill">${num(r.leads.length)} submissions</span></div><div class="filters"><div class="field"><label for="wfFilter">Outcome</label><select id="wfFilter"><option value="ALL">All outcomes</option>${Object.keys(o).map(k=>`<option ${wf.filter===k?'selected':''}>${esc(k)}</option>`).join('')}<option value="UNKNOWN" ${wf.filter==='UNKNOWN'?'selected':''}>New CMS forms (lost in Q3)</option></select></div><div class="field search"><label for="wfSearch">Search</label><input id="wfSearch" value="${esc(wf.search)}" placeholder="Name, account, AD, form…"/></div></div><div class="table-wrap" id="wfLeadTable">${leadTable(r)}</div></section>`;
}

function leadTable(r){
 const q=wf.search.toLowerCase();
 const rows=r.leads.filter(l=>(wf.filter==='ALL'||(wf.filter==='UNKNOWN'?l.unknown_form:l.outcome===wf.filter))&&(!q||[l.name,l.account,l.owner,l.form,l.sid].join(' ').toLowerCase().includes(q)));
 return `<table class="data-table"><thead><tr><th>Submitted</th><th>Lead</th><th>Form</th><th>Outcome</th><th>Owner</th><th>Score</th></tr></thead><tbody>${rows.slice(0,150).map(l=>`<tr data-trace-lead="${esc(l.sid)}" tabindex="0"><td>${date(l.ts)}</td><td><div class="person">${esc(l.name)}</div><div class="sub">${esc(l.account||'No CRM account')} · ${esc(l.sid)}</div></td><td>${esc(l.form)}${l.hand?' <span class="pill accepted">hand-raiser</span>':''}${l.unknown_form?' <span class="pill failed">new CMS form</span>':''}</td><td>${esc(l.outcome)}</td><td>${esc(getUser(l.owner)?.name||l.owner||'—')}</td><td class="score">${l.score??'—'}</td></tr>`).join('')}</tbody></table>${rows.length>150?`<div class="bar-foot" style="padding:12px 25px">Showing 150 of ${num(rows.length)}. Narrow with the filters.</div>`:''}`;
}

function slackCard(lead,ad){
 const reasons=app.reasonCodes.length?app.reasonCodes:['No fit / wrong segment','Not interested / no intent','Duplicate / already engaged','Existing customer / CS','Wrong contact / role','Timing / nurture','Bad data / enrichment','Other / needs investigation'];
 return `<div class="wf-slack"><div class="wf-slack-head"><span class="wf-slack-logo">L</span><strong>LeadLoop</strong><em>APP · DM to ${esc(ad.name)}</em></div><div class="wf-slack-body"><strong>🔥 Hand-raiser · ${esc(lead.form)} request</strong><div>${esc(lead.name)} · ${esc(lead.account||'Unmatched account')}</div><div class="wf-slack-meta">Score ${lead.score} · SLA 24h · routed to you as account owner</div></div><div class="wf-slack-actions"><button type="button" class="wf-sbtn ok" data-slack="accept" data-sid="${esc(lead.sid)}">Accept</button><select id="wfSlackReason"><option value="">Reason…</option>${reasons.map(x=>`<option>${esc(x)}</option>`).join('')}</select><button type="button" class="wf-sbtn" data-slack="reject" data-sid="${esc(lead.sid)}">Reject</button><button type="button" class="wf-sbtn" data-slack="return" data-sid="${esc(lead.sid)}">Return to marketing</button><button type="button" class="wf-sbtn ghost" data-trace-lead="${esc(lead.sid)}">Why this lead?</button></div></div><p class="micro muted" style="margin-top:10px">If the channel fails (as #mql-emea did on Aug 18), the same message goes by DM + email and Marketing Ops is alerted.</p>`;
}

function feedbackBlock(f){
 if(!f||!f.total)return '<div class="empty">No AD decisions yet. Use the Slack buttons or open an AD workspace.</div>';
 return `<div class="wf-fb-stats"><span><b>${f.accept}</b>accepted</span><span><b>${f.reject}</b>rejected</span><span><b>${f.return}</b>returned</span></div>${Object.keys(f.by_reason).length?`<div class="micro muted" style="margin:10px 0 6px">Reasons</div>${Object.entries(f.by_reason).map(([k,v])=>`<div class="metric-pair"><span>${esc(k)}</span><strong>${v}</strong></div>`).join('')}`:''}<div class="micro muted" style="margin:12px 0 6px">Latest</div>${f.recent.map(x=>`<div class="drawer-event"><strong>${esc(x.decision)} · ${esc(getUser(x.actor)?.name||x.actor)} via ${esc(x.via)}</strong><span>${esc(x.submission_id)}${x.reason_code?' · '+esc(x.reason_code):''}${x.note?' · '+esc(x.note):''}</span></div>`).join('')}`;
}

function bindResults(){
 document.querySelectorAll('[data-trace]').forEach(b=>b.onclick=()=>traceLive(b.dataset.trace));
 document.querySelectorAll('[data-trace-lead]').forEach(b=>{b.onclick=()=>openLeadTrace(b.dataset.traceLead);b.onkeydown=e=>{if(e.key==='Enter')openLeadTrace(b.dataset.traceLead)}});
 document.querySelectorAll('[data-open-ad]').forEach(b=>b.onclick=()=>openAsAd(b.dataset.openAd));
 document.querySelectorAll('[data-outfilter]').forEach(b=>b.onclick=()=>{wf.filter=b.dataset.outfilter;$('wfFilter').value=wf.filter;$('wfLeadTable').innerHTML=leadTable(wf.run);bindResults();$('wfLeadTable').scrollIntoView({behavior:'smooth',block:'start'})});
 document.querySelectorAll('[data-slack]').forEach(b=>b.onclick=()=>slackDecision(b.dataset.sid,b.dataset.slack));
 if($('wfFilter'))$('wfFilter').onchange=()=>{wf.filter=$('wfFilter').value;$('wfLeadTable').innerHTML=leadTable(wf.run);bindResults()};
 if($('wfSearch')){let t;$('wfSearch').oninput=()=>{wf.search=$('wfSearch').value;clearTimeout(t);t=setTimeout(()=>{$('wfLeadTable').innerHTML=leadTable(wf.run);bindResults()},180)}}
}

async function slackDecision(sid,decision){
 const reason=$('wfSlackReason')?.value;
 if(decision!=='accept'&&!reason)return toast('Pick a reason first: rejections and returns need one.',true);
 try{const r=await api('/api/pipeline/decision',{method:'POST',body:JSON.stringify({submission_id:sid,decision,reason_code:reason||null,via:'slack'})});wf.run.feedback=r.feedback;$('wfFeedback').innerHTML=feedbackBlock(r.feedback);toast(`Recorded as ${getUser(r.actor)?.name||r.actor} via Slack. Marketing sees it now.`)}catch(e){toast(e.message,true)}
}

async function openAsAd(ad){
 try{await doLogin({role:'ad',user_id:ad});app.adTab='inbox';workspace()}catch(e){toast(e.message,true)}
}

async function traceLive(sid){
 if(wf.tracing)return;wf.tracing=true;
 let t;try{t=await api('/api/pipeline/trace/'+encodeURIComponent(sid))}catch(e){toast(e.message,true);wf.tracing=false;return}
 document.querySelectorAll('.wf-box').forEach(b=>b.classList.remove('active','done','visited'));
 const log=$('wfTraceLog');log.innerHTML=`<section class="panel wf-tracelog"><div class="panel-head"><div><h3>Tracing ${esc(t.lead.name)} · ${esc(t.lead.account||'no CRM account')}</h3><p>${esc(t.lead.sid)} · ${esc(t.lead.form)} · submitted ${date(t.lead.ts)}</p></div><button type="button" class="btn btn-secondary" id="wfTraceFull">Open full trace</button></div><div class="panel-body" id="wfTraceSteps"></div></section>`;
 $('wfTraceFull').onclick=()=>openLeadTrace(sid);
 log.scrollIntoView({behavior:'smooth',block:'nearest'});
 for(const s of t.steps){lit(s.stage,true);$('wire-'+s.stage)?.classList.add('lit');
  $('wfTraceSteps').insertAdjacentHTML('beforeend',stepRow(s));await sleep(650);lit(s.stage,false);lit(s.stage,true,'visited')}
 wf.tracing=false;
}

function stepRow(s){const st=wf.stages.find(x=>x.id===s.stage);return `<div class="wf-step st-${s.status}"><span class="wf-step-n">${st?st.n:''}</span><div><div class="wf-step-top"><strong>${esc(st?st.title:s.stage)}</strong>${execChip(s.executor,true)}<span class="wf-status">${esc(STATUS_LABEL[s.status]||s.status)}</span></div><div class="wf-step-title">${esc(s.title)}</div>${s.detail?`<div class="wf-step-detail">${esc(s.detail)}</div>`:''}</div></div>`}

function modal(html){$('modalRoot').innerHTML=`<div class="drawer-backdrop" id="wfOverlay"></div><div class="wf-modal" role="dialog" aria-modal="true"><button class="drawer-close" id="wfClose" aria-label="Close">×</button>${html}</div>`;$('wfOverlay').onclick=closeDrawer;$('wfClose').onclick=closeDrawer;document.addEventListener('keydown',escapeDrawer,{once:true})}

function openStage(id){
 if(id==='marketing'){modal(`<div class="eyebrow">CLOSES THE LOOP</div><h2>Marketing Ops</h2><div class="wf-modal-execs">${execChip('human')}</div><p class="wf-lede">Owns the rules the pipeline runs on, without an engineer.</p><div class="wf-detail"><h4>What they control</h4><ol><li>The form registry (which form IDs create MQLs) and confirmations for unknown forms.</li><li>The rejection-reason list ADs must pick from.</li><li>Scoring weights, with justification and undo.</li><li>Held leads: approve, enrich or nurture, with the AI suggestion logged next to the decision.</li></ol><h4>What they get back</h4><p>Accept / reject / return decisions by form, campaign and reason, so they can change targeting and rules on evidence instead of opinion.</p><h4>In this demo</h4><p>Manager login → Qualification review, Priority rules (weights + reasons) and LeadLoop feedback.</p></div>`);return}
 const s=wf.stages.find(x=>x.id===id),st=wf.run?.stages.find(x=>x.id===id);
 modal(`<div class="eyebrow">STAGE ${s.n} OF 09</div><h2>${esc(s.title)}</h2><div class="wf-modal-execs">${s.executors.map(e=>execChip(e)).join('')}</div><p class="wf-lede">${esc(s.summary)}</p>
 ${st?`<div class="wf-modal-run"><div><small>IN THE Q3 RUN</small><strong>${num(st.in)}</strong><span>leads reached this stage</span></div>${Object.entries(st.by_status).map(([k,v])=>`<div><small>${esc((STATUS_LABEL[k]||k).toUpperCase())}</small><strong>${num(v)}</strong></div>`).join('')}${Object.entries(st.by_executor).map(([k,v])=>`<div><small>${esc(EXEC_META[k].label.toUpperCase())}</small><strong>${num(v)}</strong></div>`).join('')}</div>`:'<div class="notice">Run the pipeline to see how many Q3 leads each branch handled.</div>'}
 <div class="wf-detail"><h4>Trigger</h4><p>${esc(s.trigger)}</p><h4>Inputs</h4><ul>${s.inputs.map(x=>`<li>${esc(x)}</li>`).join('')}</ul><h4>Logic, step by step</h4><ol>${s.logic.map(x=>`<li>${esc(x)}</li>`).join('')}</ol>${s.ai?`<div class="wf-callout ai"><strong>✦ Where AI acts</strong><p>${esc(s.ai)}</p></div>`:''}${s.human?`<div class="wf-callout human"><strong>● Where a person decides</strong><p>${esc(s.human)}</p></div>`:''}<h4>Output</h4><ul>${s.outputs.map(x=>`<li>${esc(x)}</li>`).join('')}</ul><h4>Failure handling and alerts</h4><p>${esc(s.failure)}</p><h4>In production</h4><p>${esc(s.production)}</p><h4>In this demo</h4><p>${esc(s.demo)}</p></div>
 <div id="wfStageExamples"></div>`);
 if(wf.run)stageExamples(id);
}
async function stageExamples(id){
 // Pull a few traces where this stage did something other than pass.
 const pool=wf.run.leads.filter(l=>id==='capture'?l.unknown_form:id==='identity'?l.outcome==='Merged':id==='enrich'||id==='qualify'?l.outcome==='Marketing review'||l.outcome==='CS handoff':id==='route'?l.outcome==='Manager triage':id==='workspace'||id==='feedback'?l.outcome==='AD inbox':true).slice(0,40);
 const picked=[];for(const l of pool){if(picked.length>=4)break;try{const t=await api('/api/pipeline/trace/'+encodeURIComponent(l.sid));const s=t.steps.find(x=>x.stage===id);if(s&&(s.status!=='ok'||['workspace','feedback'].includes(id)))picked.push({l,s})}catch(e){}}
 const el=$('wfStageExamples');if(!el||!picked.length)return;
 el.innerHTML=`<div class="wf-detail"><h4>Real examples from the run</h4>${picked.map(({l,s})=>`<button type="button" class="wf-ex-row" data-trace-lead="${esc(l.sid)}"><strong>${esc(l.name)} · ${esc(l.account||'no CRM account')}</strong><span>${esc(s.title)}</span><em>${esc(s.detail)}</em></button>`).join('')}</div>`;
 el.querySelectorAll('[data-trace-lead]').forEach(b=>b.onclick=()=>openLeadTrace(b.dataset.traceLead));
}

async function openLeadTrace(sid){
 try{const t=await api('/api/pipeline/trace/'+encodeURIComponent(sid));const l=t.lead;
  if(!wf.stages){const s=await api('/api/pipeline/stages');wf.stages=s.stages}
  const h=l.hist||{};
  const actual=!h.mql?'No MQL was created (the form never reached anyone).':[h.unassigned?'created in the system queue (no owner)':`assigned to ${h.owner_at_mql}`,h.delivered?'alert delivered':'no delivered alert',h.owner_active_today===false?'owner had left':null,h.customer?'existing customer sent to an AD':null,h.worked?'status changed within SLA':'not worked within SLA'].filter(Boolean).join(' · ');
  modal(`<div class="eyebrow">LEAD TRACE · ${esc(l.sid)}</div><h2>${esc(l.name)}</h2><p class="wf-lede">${esc(l.account||'No CRM account')} · ${esc(l.form)}${l.hand?' · hand-raiser':''} · submitted ${date(l.ts)} · outcome <strong>${esc(l.outcome)}</strong>${l.owner?` → ${esc(getUser(l.owner)?.name||l.owner)}`:''}</p><div class="wf-steps">${t.steps.map(stepRow).join('')}</div><div class="wf-callout before"><strong>What actually happened in Q3</strong><p>${esc(actual)}${h.mql_id?` (${esc(h.mql_id)})`:''}</p></div>`)}catch(e){toast(e.message,true)}
}

// ---- AD inbox: the list the pipeline built ----
async function renderPipelineInbox(){
 const el=$('adContent');el.innerHTML='<div class="panel"><div class="panel-body">Loading your LeadLoop inbox…</div></div>';
 try{const d=await api('/api/pipeline/inbox');if(app.adTab!=='inbox')return;
  if(!d.run){el.innerHTML=`<section class="panel"><div class="panel-body wf-inbox-empty"><h3>Your list has not been built yet</h3><p>Run the pipeline: the 547 Q3 form submissions go through capture, dedupe, enrichment, qualification, owner validation and routing, and the leads that belong to you land here.</p><div class="button-row">${btn('▶ Run the pipeline','btn-primary','inboxRun')}${btn('See the workflow','btn-secondary','goWf')}</div></div></section>`;$('inboxRun').onclick=runFromInbox;$('goWf').onclick=()=>workflowPage();return}
  const open=d.rows.filter(r=>!r.decision),hand=open.filter(r=>r.hand).length;
  el.innerHTML=`<div class="dashboard-heading"><div><div class="eyebrow">LEADLOOP INBOX · BUILT BY THE PIPELINE RUN ${esc(d.run.ran_at.slice(0,16).replace('T',' '))} UTC</div><h2>Your list, already checked</h2><p>Every lead here passed capture, dedupe, enrichment, qualification, owner validation and routing. Hand-raisers first. Decide in one click; marketing sees it immediately.</p></div><div class="button-row">${btn('↻ Run the pipeline again','btn-secondary','inboxRun')}${btn('See the workflow','btn-plain','goWf')}</div></div><div class="hero-stats">${kpi('IN YOUR LIST',num(d.rows.length),'From the Q3 replay')}${kpi('WAITING ON YOU',num(open.length),'No decision yet')}${kpi('HAND-RAISERS',num(hand),'24h SLA')}${kpi('DECIDED',num(d.rows.length-open.length),'Visible to marketing')}</div><section class="panel"><div class="table-wrap"><table class="data-table wf-inbox"><thead><tr><th>Score</th><th>Lead</th><th>Intent</th><th>Why it’s here</th><th>Decision</th></tr></thead><tbody>${d.rows.map(r=>inboxRow(r)).join('')}</tbody></table></div></section>`;
  bindInbox();$('inboxRun').onclick=runFromInbox;$('goWf').onclick=()=>workflowPage();
 }catch(e){toast(e.message,true)}
}
async function runFromInbox(){
 const b=$('inboxRun');b.disabled=true;b.textContent='Running the 9 stages…';
 try{wf.run=await api('/api/pipeline/run',{method:'POST',body:JSON.stringify({ai:'auto'})});const me=wf.run.ad_lists.find(a=>a.ad===(app.session?.user_id||app.session?.sub));toast(`Pipeline finished: ${me?me.count:0} leads routed to you, ${me?me.hand:0} hand-raisers.`);renderPipelineInbox()}catch(e){toast(e.message,true);b.disabled=false}
}
function inboxRow(r){
 const reasons=app.reasonCodes.length?app.reasonCodes:[];
 const dec=r.decision;
 return `<tr><td><span class="score">${r.score??'—'}</span></td><td><div class="person">${esc(r.name)}</div><div class="sub">${esc(r.account||'No account')} · ${date(r.ts)}</div></td><td>${esc(r.form)}${r.hand?' <span class="pill accepted">hand-raiser</span>':''}${r.outcome==='CS handoff'?' <span class="pill">CS copied</span>':''}${r.unknown_form?' <span class="pill failed">recovered CMS form</span>':''}</td><td><button type="button" class="btn btn-plain" data-trace-lead="${esc(r.sid)}">See trace ↗</button></td><td>${dec?`<span class="pill ${dec.decision==='accept'?'accepted':'disqualified'}">${esc(dec.decision)}</span><div class="sub">${esc(dec.reason_code||'')} ${dec.via==='slack'?'· via Slack':''}</div>`:`<div class="wf-inbox-actions"><button type="button" class="btn btn-soft" data-dec="accept" data-sid="${esc(r.sid)}">Accept</button><select id="rs-${esc(r.sid)}"><option value="">Reason…</option>${reasons.map(x=>`<option>${esc(x)}</option>`).join('')}</select><button type="button" class="btn btn-secondary" data-dec="reject" data-sid="${esc(r.sid)}">Reject</button><button type="button" class="btn btn-secondary" data-dec="return" data-sid="${esc(r.sid)}">Return</button></div>`}</td></tr>`;
}
function bindInbox(){
 document.querySelectorAll('[data-trace-lead]').forEach(b=>b.onclick=()=>openLeadTrace(b.dataset.traceLead));
 document.querySelectorAll('[data-dec]').forEach(b=>b.onclick=async()=>{const sid=b.dataset.sid,decision=b.dataset.dec,reason=$('rs-'+sid)?.value;
  if(decision!=='accept'&&!reason)return toast('Pick a reason from the list first.',true);
  try{await api('/api/pipeline/decision',{method:'POST',body:JSON.stringify({submission_id:sid,decision,reason_code:reason||null})});toast('Saved. Marketing sees this decision now.');renderPipelineInbox()}catch(e){toast(e.message,true)}});
}

// ---- Manager: the marketing side of the loop ----
async function drawLoop(){
 const el=$('managerContent');el.innerHTML='<div class="panel"><div class="panel-body">Loading…</div></div>';
 try{const d=await api('/api/pipeline/latest');if(app.managerTab!=='loop')return;const r=d.run;
  if(!r){el.innerHTML='<div class="empty" style="padding:40px">Run the pipeline in Workflow first.</div>';return}
  const f=r.feedback;
  el.innerHTML=`<div class="dashboard-heading"><div><div class="eyebrow">MARKETING OPS · CLOSED LOOP</div><h2>What ADs did with the pipeline’s leads</h2><p>Decisions from the AD inbox and Slack buttons, by form and reason. This is the shared definition of “worked”: a seller decision, never a robot status change.</p></div></div><div class="hero-stats">${kpi('LEADS ROUTED TO ADs',num(r.outcomes['AD inbox']||0),'Latest run')}${kpi('DECIDED',num(f.total),'Accept / reject / return')}${kpi('HELD FOR YOU',num(r.outcomes['Marketing review']||0),'Qualification review tab')}${kpi('MANAGER TRIAGE',num(r.outcomes['Manager triage']||0),'No territory: assign within 15 min')}</div><div class="split"><section class="panel"><div class="panel-head"><div><h3>By form</h3><p>Which sources ADs accept or reject</p></div></div>${renderLTable(Object.entries(f.by_form).map(([k,v])=>({form:k,...v})),[{key:'form',label:'Form'},{key:'accept',label:'Accepted'},{key:'reject',label:'Rejected'},{key:'return',label:'Returned'}])}</section><section class="panel"><div class="panel-head"><div><h3>Decision log</h3><p>Latest first</p></div></div><div class="panel-body">${feedbackBlock(f)}</div></section></div>`;
 }catch(e){toast(e.message,true)}
}

// ---- Roadmap: product versions ----
function roadmapPage(){
 setNav('roadmap');
 const v=[
  {v:'V1',name:'LeadLoop core',state:'Built · this demo',goal:'Stop losing the leads we already have.',ships:['Form registry + unknown-form alert','Dedupe, CRM enrichment, protected hand-raisers','Owner validation on territory change, no fallback queue','Slack alerts with buttons, delivery fallback + escalation','AD inbox, reason list, marketing feedback loop'],gate:'Hand-raisers reaching an active AD = 100%; silent alert failures = 0; adjusted action rate above 51% baseline.',time:'Pilot: 1 region, 6 weeks'},
  {v:'V2',name:'Connect',state:'Next',goal:'Run on live systems instead of CSV exports.',ships:['Salesforce read/write sync (owner, status, reason, tasks)','CMS webhooks + event queue','Slack interactive buttons with signed requests','Enrichment provider for unmatched accounts (Clearbit / Apollo)'],gate:'Daily reconciliation LeadLoop ↔ Salesforce with zero unexplained differences for 2 weeks.',time:'4–6 weeks'},
  {v:'V3',name:'Qualify',state:'Planned',goal:'Give AI more of the qualification work, earned with evidence.',ships:['AI research on every content lead (company, role, intent signals)','Eval set built from V1/V2 human decisions','Autonomy thresholds: AI decides only where agreement with humans ≥ 90%','Weekly drift and disagreement review'],gate:'AI vs. human agreement ≥ 90% on a holdout of 300 decisions; no hand-raiser ever held.',time:'6–8 weeks'},
  {v:'V4',name:'Prepare',state:'Planned',goal:'Make the first touch faster and better.',ships:['Account brief and first-touch draft per lead','Meeting-booking link in the Slack alert','Sequences for nurture leads','AD edits and sends; nothing auto-sent'],gate:'Median time to first touch on hand-raisers < 4h; draft acceptance rate tracked.',time:'4–6 weeks'},
  {v:'V5',name:'Learn',state:'Planned',goal:'Close the loop from lead to revenue.',ships:['Outcome labels from clean V1–V4 data (meetings, SQLs, opportunities)','Scoring recalibrated on outcomes, with a holdout','Experiment framework for routing and qualification changes','Source ROI for marketing: pipeline by campaign and form'],gate:'Score beats rule baseline on a holdout quarter; marketing budget decisions use LeadLoop data.',time:'Quarterly cycle'}];
 const r=app.diagnosis?.operational||{};const n=r.total||467,p=(r.adjusted_adoption_numerator||157)/(r.adjusted_adoption_denominator||308);
 const nc=Math.round(n*0.2),nt=n-nc,mde=k=>Math.round(100*2.8*Math.sqrt(p*(1-p)*(1/(nc*k)+1/(nt*k))));
 $('roadmapView').innerHTML=`<div class="dashboard-heading"><div><div class="eyebrow">04 · ROADMAP · FROM FIX TO PRODUCT</div><h2>V1 fixes the leak. V2–V5 turn it into a product.</h2><p>Each version ships something usable and has a gate: a measured condition that must hold before the next version starts.</p></div></div>
 <div class="wf-road">${v.map((x,i)=>`<div class="wf-ver ${i===0?'built':''}"><div class="wf-ver-top"><span class="wf-ver-v">${x.v}</span><span class="pill ${i===0?'accepted':'simulated'}">${esc(x.state)}</span></div><h3>${esc(x.name)}</h3><p class="wf-ver-goal">${esc(x.goal)}</p><ul>${x.ships.map(s=>`<li>${esc(s)}</li>`).join('')}</ul><div class="wf-ver-gate"><small>GATE TO NEXT</small>${esc(x.gate)}</div><div class="wf-ver-time">${esc(x.time)}</div></div>${i<v.length-1?'<div class="wf-ver-arrow">→</div>':''}`).join('')}</div>
 <div class="split wf-split"><section class="panel"><div class="panel-head"><div><h3>Rollout of V1</h3><p>Small, measured, reversible.</p></div></div><div class="panel-body wf-plan"><div><strong>Weeks 0–2 · Fix and instrument</strong><span>Add v2 form IDs to the registry, restore #mql-emea, re-route the 28 leads on departed owners, ship the unknown-form and delivery alerts.</span></div><div><strong>Weeks 3–8 · Pilot one region</strong><span>Full pipeline for one region; a stable 20% control group keeps today’s process. Weekly review with Sales and Marketing Ops.</span></div><div><strong>Week 9 · Decide</strong><span>Roll out to all regions if the gate holds; roll back the region if hand-raiser SLA or AD acceptance gets worse.</span></div><div><strong>Stop criteria</strong><span>Any hand-raiser held or lost; AD rejection rate up more than 10 points vs. control; alert failures not caught within 1 hour.</span></div></div></section>
 <section class="panel"><div class="panel-head"><div><h3>What the control group can prove</h3><p>Honest about sample size.</p></div></div><div class="panel-body"><div class="metric-pair"><span>Baseline adjusted action rate</span><strong>${(100*p).toFixed(0)}%</strong></div><div class="metric-pair"><span>Leads per quarter · control / treatment</span><strong>${nc} / ${nt}</strong></div><div class="metric-pair"><span>Smallest lift we can detect in 1 quarter</span><strong>±${mde(1)} pts</strong></div><div class="metric-pair"><span>…in 2 quarters</span><strong>±${mde(2)} pts</strong></div><p class="micro muted" style="margin-top:12px">80% power, 5% significance, two-proportion test. A quarter can only confirm a large effect, so weekly leading indicators carry the pilot: alert delivery rate, time to first AD decision, hand-raisers reaching an active AD, share of decisions with a reason.</p></div></section></div>`;
}
