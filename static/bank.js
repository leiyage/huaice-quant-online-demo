import {DemoEventSource} from './intelligence/event-store.js';
import {EventRepository, ImpactPropagationEngine, ScenarioManager} from './intelligence/impact-engine.js';
import {CollaborationView, logFrames, stageStates} from './bank-collaboration.js';

const $ = id => document.getElementById(id);
const repository = new EventRepository(new DemoEventSource());
const scenarios = new ScenarioManager();
const propagation = new ImpactPropagationEngine();
const stageNames = {intelligence:'情报识别',risk:'传播分析',quant:'历史验证',trading:'模拟组合',report:'研究报告'};
const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)');
const collaboration = new CollaborationView();
let runningTask = false, launchingTask = false, latestTask = null, timer = null;
document.body.classList.toggle('no-motion',reduceMotion.matches);

async function request(url, options) {
  const response = await fetch(url, {cache:'no-store', ...options});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `本地服务返回 HTTP ${response.status}`);
  return data;
}
function post(payload) {
  return request('/api/workflow', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
}
function text(id, value) { const node=$(id); if(node) node.textContent=value; }
function node(tag, className, content) { const el=document.createElement(tag); if(className) el.className=className; if(content!==undefined) el.textContent=content; return el; }
function time(value) { try { return new Date(value).toLocaleString('zh-CN',{hour12:false,timeZone:'UTC'})+' UTC'; } catch { return value||'时间未提供'; } }

function updateFlow(task) {
  const states=stageStates(logFrames(task));
  for(const span of document.querySelectorAll('[data-flow-stage]')){
    const state=states[span.dataset.flowStage];
    span.dataset.state=state==='completed'?'done':state==='running'?'active':state;
    span.title=state==='skipped'?'该阶段已跳过，未产生研究结果':state==='blocked'?'等待确认或补齐数据':stageNames[span.dataset.flowStage];
  }
  collaboration.update(task,{animate:runningTask});
  if(task){
    const progress=task.status==='completed'?100:Math.round(Object.keys(stageNames).indexOf(task.stage)/Object.keys(stageNames).length*100);
    text('workflow-current',`${task.task_id.slice(0,12)} · ${task.status.toUpperCase()} · ${stageNames[task.stage]||task.stage} · 任务进度 ${progress}% · SIMULATED SCENARIO`);
  }else text('workflow-current','尚无跨部门研究任务。');
  const resume=$('resume-oil-study'),start=$('run-oil-study');
  if(resume){resume.hidden=!(task?.status==='running'&&!runningTask);resume.disabled=runningTask;}
  if(start){start.disabled=runningTask||launchingTask;start.textContent=runningTask?'研究任务正在执行':launchingTask?'正在连接研究接口':'启动金融研究';}
}

function showReport(task) {
  const host=$('research-report'); if(!host)return;
  const report=task?.result?.report?.report;
  if(!report){host.hidden=true;return;}
  host.replaceChildren();host.hidden=false;
  host.append(node('h3', '', `研究报告 · ${report.title} · DEMO`));
  for(const [heading,value] of Object.entries(report.sections||{})){
    const p=node('p');const b=node('b','',`${heading}：`);p.append(b,document.createTextNode(value));host.append(p);
  }
  host.append(node('p','report-limit',report.limitation));
}

async function renderTaskList() {
  const host=$('workflow-list'); if(!host)return;
  const {tasks}=await request('/api/workflow');
  const activeCount=tasks.filter(task=>task.status==='running').length;
  text('task-count',String(tasks.length));
  text('task-summary',tasks.length?`${activeCount} 项进行中 · 最近任务 ${tasks[0].status==='completed'?'已完成':tasks[0].status==='running'?'进行中':tasks[0].status}`:'尚无跨部门研究任务');
  const detailed=await Promise.all(tasks.slice(0,5).map(task=>request(`/api/workflow?id=${encodeURIComponent(task.task_id)}`)));
  host.replaceChildren();
  if(!detailed.length){host.append(node('p','','尚无跨部门研究任务。'));latestTask=null;updateFlow(null);return;}
  latestTask=detailed.find(task=>task.status==='running')||detailed[0];
  updateFlow(latestTask);showReport(latestTask);
  for(const task of detailed){
    const card=node('article','task-card'),head=node('header');
    head.append(node('b','',`${task.title} · ${task.status.toUpperCase()}`),node('small','',time(task.updated_at)));
    card.append(head,node('small','',`ID ${task.task_id} · ${stageNames[task.stage]||task.stage} · ${task.workflow_type}`));
    const last=task.logs.at(-1);if(last)card.append(node('p','',last.message));
    const details=document.createElement('details'),summary=node('summary','','阶段日志');details.append(summary);
    const pre=node('pre','',task.logs.map(log=>`${log.created_at} [${log.stage}/${log.status}] ${log.message}${Object.keys(log.details||{}).length?`\n${JSON.stringify(log.details)}`:''}`).join('\n'));
    details.append(pre);card.append(details);host.append(card);
  }
}

async function refreshState() {
  try{
    const state=await request('/api/state');
    const badge=$('service-status');badge.textContent='本机服务在线';badge.className='service-pill online';
    text('engine-status','量化服务已连接');text('engine-mode',state.market_type==='us'?'美股研究':'加密资产研究');
    text('engine-data',state.data_source||'行情来源未说明');
    text('portfolio-equity',`${Number(state.account?.equity||0).toLocaleString('en-US',{maximumFractionDigits:2})} ${state.currency||''}`);
    text('portfolio-state',state.safety||'本地模拟账户');
    text('replay-status',state.running?'历史回放运行中':state.finished?'历史回放已结束':'历史回放空闲');
    text('replay-progress',`${state.market?.day||0} / ${state.market?.total_days||0} 根 · ${state.assets?.join(' / ')||''}`);
    text('portfolio-note',`当前账户状态来自 /api/state；仓位 ${Number(state.account?.exposure_pct||0).toFixed(2)}%，成交 ${state.metrics?.trade_count||0} 笔。该状态不是原油事件的模拟结果。`);
  }catch(error){const badge=$('service-status');badge.textContent='量化服务不可用';badge.className='service-pill error';text('engine-status',error.message);text('engine-mode','离线');}
}

async function refresh() {
  await refreshState();
  try{await renderTaskList();}catch(error){text('workflow-message',`任务库暂不可用：${error.message}`);}
}

async function loadRecentEvents() {
  const host=$('recent-events');
  try{
    const snapshot=await repository.load();host.replaceChildren();
    for(const event of snapshot.events.slice().sort((a,b)=>Date.parse(b.captured_at)-Date.parse(a.captured_at)).slice(0,4)){
      const card=node('article','recent-event');card.append(node('b','',event.title),node('small','',`${event.region} · ${event.category} · ${time(event.captured_at)} · DEMO`));host.append(card);
    }
  }catch(error){host.replaceChildren(node('p','',`本地情报快照不可用：${error.message}`));}
}

async function submitStep(task, stage, status, message, result) {
  return post({command:'advance',task_id:task.task_id,stage,status,message,result});
}

async function buildRiskResult(event) {
  const scenario=scenarios.forEvent(event);
  if(!scenario)throw new Error('现有传播引擎没有此事件的结构化情景。');
  const graph=scenarios.graph(scenario.scenario_id);
  const source= scenario.nodes.find(item=>item.node_type==='EVENT');
  if(!source)throw new Error('传播情景缺少事件源节点。');
  const result=propagation.propagate(graph,source.node_id,{maxDepth:4,maxNodes:120});
  return {scenario_id:scenario.scenario_id,relation_count:scenario.edges.length,path_count:result.paths.length,node_count:result.nodes.length,max_depth:result.maxDepth,
    paths:result.paths.slice(0,32).map(path=>({nodes:path.path.map(id=>graph.nodes.get(id)?.name||id),direction:path.direction<0?'negative':path.direction>0?'positive':'uncertain',strength:path.strength,confidence:path.confidence,evidence:path.relation.evidence})),
    limitation:scenario.limitation,provenance:'SIMULATED SCENARIO · 现有前端规则传播引擎'};
}

async function runStage(task) {
  const stage=task.stage;
  if(stage==='intelligence'){
    const snapshot=await repository.load();const event=snapshot.events.find(item=>item.event_id===task.input.event_id);
    if(!event)throw new Error('本地 DEMO 事件列表中找不到 DEMO-008。');
    const result={event_id:event.event_id,title:event.title,description:event.description,source:event.source,source_url:event.source_url,published_at:event.published_at,captured_at:event.captured_at,country:event.country,region:event.region,latitude:event.latitude,longitude:event.longitude,category:event.category,related_assets:event.related_assets,is_demo:event.is_demo,identification:'采用本地结构化 DEMO 事件字段；未调用外部 AI。'};
    return submitStep(task,stage,'completed','全球情报部已读取并校验 DEMO-008 事件结构。',result);
  }
  if(stage==='risk'){
    const snapshot=await repository.load(),event=snapshot.events.find(item=>item.event_id===task.input.event_id);
    const result=await buildRiskResult(event);
    return submitStep(task,stage,'completed',`风险管理部调用现有传播引擎，得到 ${result.path_count} 条有界 DEMO 路径。`,result);
  }
  if(stage==='quant'){
    const snapshot=await repository.load(),event=snapshot.events.find(item=>item.event_id===task.input.event_id);
    const available=await request('/api/files');
    const names=(available.files||[]).map(item=>String(item.name).toUpperCase());
    const symbols=(event?.related_assets||[]).map(value=>String(value).toUpperCase());
    const required=['BRENT','WTI'];
    const matched=required.filter(symbol=>names.includes(symbol));
    if(!matched.length){
      const result={validation:'unavailable',required_assets:required,available_local_assets:names,reason:'本地行情目录没有 BRENT 或 WTI；未调用回测引擎，也未使用其他资产替代。',event_asset_labels:symbols};
      return submitStep(task,stage,'skipped','量化研究部未找到原油历史行情，历史验证标记为尚未验证。',result);
    }
    const result={validation:'available',matched_assets:matched,reason:'发现本地对应资产；尚未启动回测。'};
    return submitStep(task,stage,'blocked','发现相关行情，但需在回测前确认数据区间与实验参数。',result);
  }
  if(stage==='trading'){
    const result={analysis:'not_generated',orders_generated:0,live_execution:false,reason:'历史资产验证缺失；不形成事件关联模拟组合或订单。'};
    return submitStep(task,stage,'skipped','模拟交易部仅确认安全边界；缺少历史验证，不生成组合或订单。',result);
  }
  if(stage==='report'){
    const current=await request(`/api/workflow?id=${encodeURIComponent(task.task_id)}`);
    const eventResult=current.result.intelligence||{},riskResult=current.result.risk||{},quantResult=current.result.quant||{},tradingResult=current.result.trading||{};
    const chain=(riskResult.paths||[]).slice(0,8).map(path=>path.nodes.join(' → ')).join('；')||'未形成传播路径';
    const report={title:'国际原油供应冲击 · DEMO 研究记录',created_at:new Date().toISOString(),status:'部分完成',
      sections:{
        '事件描述':`${eventResult.title||'原油运输扰动情景'}。${eventResult.description||''}`,
        '数据来源':`${eventResult.source||'本地 DEMO 快照'}；发布时间 ${eventResult.published_at||'未提供'}；系统捕捉时间 ${eventResult.captured_at||'未提供'}。该事件为模拟数据。`,
        '事件传播路径':chain,
        '相关资产':(eventResult.related_assets||['BRENT','WTI']).join('、')+'（研究标签，不是交易信号）',
        '历史分析结果':quantResult.validation==='unavailable'?'尚未验证：缺少 BRENT/WTI 本地历史行情；未运行历史回测。':'历史行情已发现但尚未运行实验。',
        '模拟组合分析':tradingResult.reason||'未生成与事件相关的模拟组合分析。',
        '风险提示':'规则传播路径是情景假设，不证明因果关系，也不代表实际市场方向或价格预测。',
      },
      limitation:'DEMO / HYPOTHETICAL。当前无原油历史数据验证、供应弹性估计或真实来源核验；不得据此交易。'};
    return submitStep(task,stage,'completed','研究报告已生成并保存到本地任务记录；历史验证缺口已明确列出。',{report});
  }
  throw new Error(`未知研究阶段：${stage}`);
}

async function runTask(task) {
  if(runningTask)return;
  runningTask=true;text('workflow-message','研究任务正在按阶段运行；所有结果都写入本地任务日志。');updateFlow(task);
  try{
    while(task.status==='running'){
      task=await runStage(task);latestTask=task;updateFlow(task);showReport(task);text('workflow-message',`已完成：${stageNames[Object.keys(stageNames)[Math.max(0,Object.keys(stageNames).indexOf(task.stage)-1)]]||'研究阶段'}。`);
      await refreshState();
      if(task.status==='blocked'||task.status==='failed')break;
    }
    if(task.status==='completed')text('workflow-message','研究流程完成；报告已生成，历史数据缺口已如实标注。');
  }catch(error){
    text('workflow-message',`当前阶段发生错误：${error.message}`);
    try{task=await submitStep(task,task.stage,'failed',`阶段失败：${error.message}`,{error:error.message});latestTask=task;}catch{}
  }finally{runningTask=false;await refresh();}
}

async function startStudy() {
  if(runningTask||launchingTask)return;
  launchingTask=true;updateFlow(latestTask);
  try{
    let task=latestTask?.status==='running'?latestTask:await post({command:'create',workflow_type:'oil_supply_demo',input:{event_id:'DEMO-008',is_demo:true}});
    latestTask=task;await runTask(task);
  }catch(error){text('workflow-message',`无法启动研究流程：${error.message}`);}
  finally{launchingTask=false;updateFlow(latestTask);}
}

const clock=$('bank-clock'),motion=$('bank-motion'),fullscreen=$('bank-fullscreen');
function updateClock(){if(clock)clock.textContent=new Date().toISOString().slice(11,19)+' UTC';}
updateClock();timer=setInterval(updateClock,1000);
motion?.addEventListener('click',()=>{const on=motion.getAttribute('aria-pressed')!=='true';motion.setAttribute('aria-pressed',String(on));motion.textContent=`环境动效：${on?'开启':'关闭'}`;document.body.classList.toggle('no-motion',!on||reduceMotion.matches);});
reduceMotion.addEventListener?.('change',event=>document.body.classList.toggle('no-motion',event.matches||motion?.getAttribute('aria-pressed')==='false'));
fullscreen?.addEventListener('click',async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await document.documentElement.requestFullscreen();}catch{fullscreen.textContent='全屏不可用';}});
document.addEventListener('fullscreenchange',()=>{if(fullscreen)fullscreen.textContent=document.fullscreenElement?'退出全屏':'全屏演示';});
$('run-oil-study')?.addEventListener('click',startStudy);
$('resume-oil-study')?.addEventListener('click',startStudy);
$('bank-presentation')?.addEventListener('click',()=>{
  const on=document.body.classList.toggle('presentation-mode');
  $('bank-presentation').setAttribute('aria-pressed',String(on));
  $('bank-presentation').textContent=on?'退出录屏模式':'16:9 录屏模式';
});
await Promise.all([refresh(),loadRecentEvents()]);
const pollTimer=setInterval(()=>{if(!runningTask&&!launchingTask)refresh();},5000);
window.addEventListener('pagehide',event=>{if(!event.persisted){clearInterval(timer);clearInterval(pollTimer);collaboration.destroy();}});
