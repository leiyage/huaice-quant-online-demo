import {DemoEventSource, filterEvents} from './event-store.js';
import {GlobeAtlas} from './globe.js';
import {EventRepository,FinancialKnowledgeGraph,ImpactPropagationEngine,ScenarioManager} from './impact-engine.js';

const $ = id => document.getElementById(id);
const source = new EventRepository(new DemoEventSource());
const scenarios = new ScenarioManager(), propagation = new ImpactPropagationEngine();
const controls = ['search','country','category','sort','time-range'];
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
let events = [], visible = [], selectedId = null, priority = false;
let tourTimer = null, clockTimer = null, loadController = null;
let atlas=null,butterflyMode=false,scenarioId='SCENARIO-OIL-001',impactScenario=null,impactNetwork=null,impactStep=0,impactTimer=null,selectedImpactNodeId=null;
const impactControls=()=>({query:$('impact-node-search')?.value||'',risk:$('impact-risk-filter')?.value||'all',speed:Number($('impact-speed')?.value||1)});
function createAtlas() {
  return new GlobeAtlas($('atlas'), id => selectEvent(id,true), state => {
    $('globe-status').textContent=state.is3D?'3D 地球 · 原生 WebGL · 帧率上限 30 FPS':'二维地图 · 事件联动可用';
    $('globe-help').textContent=state.is3D?'拖动旋转 / 滚轮缩放 / 点击节点':'二维兼容视图 / 点击节点查看事件';
    $('globe-auto').disabled=!state.is3D;
    $('globe-auto').textContent=`自动旋转：${state.autoRotate?'开启':'关闭'}`;
    $('globe-auto').setAttribute('aria-pressed',String(state.autoRotate));
    $('globe-neural').disabled=!state.neuralAvailable;
    $('globe-neural').textContent=`神经网：${state.neuralEnabled?(state.neuralAnimated?'开启':'静态'):'关闭'}`;
    $('globe-neural').setAttribute('aria-pressed',String(state.neuralEnabled));
    ['globe-in','globe-out','globe-reset'].forEach(id=>$(id).disabled=!state.is3D);
    $('globe-mode').textContent=state.is3D?'切换二维':'尝试 3D';
    $('globe-warning').hidden=!state.message;$('globe-warning').textContent=state.message;
    if(atlas&&butterflyMode)syncButterfly();
  });
}
atlas = createAtlas();

function element(tag, className, text) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text !== undefined) el.textContent = String(text);
  return el;
}
function time(value, full = false) {
  return new Date(value).toISOString().slice(full ? 0 : 11, full ? 19 : 16).replace('T',' ') + ' UTC';
}
function announce(message) { $('notice').textContent = message; $('notice').hidden = !message; }
function statusLabel(e) { return e.analysis_status === 'queued' ? '待分析 · DEMO' : '模拟分析完成'; }
function sentimentLabel(e) { return {negative:'风险倾向',positive:'积极倾向',neutral:'待评估'}[e.sentiment]; }
function configureImpact(event,preset=scenarios.forEvent(event)) {
  impactScenario=preset||scenarios.fromEvent(event);if(preset)scenarioId=preset.scenario_id;
  const graph=new FinancialKnowledgeGraph(impactScenario),source=impactScenario.nodes.find(item=>item.node_type==='EVENT');
  impactNetwork={...propagation.propagate(graph,source.node_id,{maxDepth:4,maxNodes:120}),edges:impactScenario.edges,graph};
  selectedImpactNodeId=source.node_id;impactStep=0;
  $('impact-scenario').value=preset?.scenario_id||'';
}
function syncButterfly() {
  $('workspace').classList.toggle('butterfly-mode',butterflyMode);
  $('analysis-panel')?.classList.toggle('butterfly-view',butterflyMode);
  $('impact-analysis').hidden=!butterflyMode;
  $('view-mode').textContent=butterflyMode?'返回 GLOBAL VIEW':'进入 BUTTERFLY VIEW';
  $('view-mode').setAttribute('aria-pressed',String(butterflyMode));
  $('impact-step').textContent=butterflyMode?`L${impactStep} / L${impactNetwork?.maxDepth??4} · SIMULATED SCENARIO`:'GLOBAL VIEW · DEMO';
  atlas?.setButterfly(butterflyMode?impactNetwork:null,impactStep,selectedImpactNodeId,selectImpactNode,impactControls());
  const selected=events.find(event=>event.event_id===selectedId);renderImpactPanel(selected);
}
function selectImpactNode(id) { selectedImpactNodeId=id;syncButterfly(); }
function renderImpactPanel(event) {
  const panel=$('impact-analysis');if(!butterflyMode||!impactNetwork||!impactScenario){panel.hidden=true;return;}
  panel.hidden=false;const layers=impactNetwork.nodes.filter(item=>item.level<=impactStep),all=impactNetwork.nodes.filter(item=>item.level>0),atType=type=>all.filter(item=>item.node.node_type===type);
  const source=impactNetwork.graph.nodes.get(impactNetwork.sourceId),direct=all.filter(item=>item.level===1).length,indirect=all.filter(item=>item.level>1).length;
  $('impact-title').textContent=`BUTTERFLY EFFECT ANALYSIS · ${impactScenario.name}`;
  $('impact-level').textContent='SIMULATED SCENARIO';
  const summary=$('impact-summary');summary.replaceChildren();
  const card=(title,body,note)=>{const block=element('div','impact-summary-card');block.append(element('strong','',title),element('p','',body),element('small','',note));summary.append(block);};
  card('事件 / EVENT',source.name,`${event?'DEMO 案例时间 '+time(event.published_at,true):'示例时间未提供'} · ${impactScenario.region} / ${impactScenario.country} · 来源 ${event?.source||'无真实来源'} · DEMO，非真实来源`);
  card('当前已展示层级',`${layers.length} 个节点 · ${impactNetwork.paths.filter(path=>path.depth<=impactStep).length} 条可见传播关系`,`总图 ${impactNetwork.nodes.length} 个可达节点 · ${impactNetwork.paths.length} 条简单路径；无环遍历深度上限 ${impactNetwork.maxDepth}`);
  card('直接 / 间接影响',`${direct} 个直接节点 / ${indirect} 个后续节点`,`直接影响数按 L1 节点数统计；不是损失或收益估算`);
  const countries=[...new Set(atType('COUNTRY').map(item=>item.node.name))],industries=[...new Set(all.filter(item=>item.node.node_type==='INDUSTRY').map(item=>item.node.name))],assets=atType('ASSET').map(item=>item.node.asset_symbol||item.node.name);
  const grid=element('div','impact-stat-grid');for(const[label,value]of [['潜在国家 / 地区',countries.join('、')||'未到达该层'],['潜在行业',industries.join('、')||'未到达该层'],['潜在资产标签',assets.join('、')||'未到达该层'],['情景关系',`${impactScenario.edges.length} 条 / DEMO`]]){const cell=element('div','impact-stat');cell.append(element('span','',label),element('b','',value));grid.append(cell);}summary.append(grid);
  card('风险解释',impactScenario.description,impactScenario.limitation);
  $('impact-chain-toggle').setAttribute('aria-expanded',String(!$('impact-chain').hidden));
  $('impact-evidence-toggle').setAttribute('aria-expanded',String(!$('impact-evidence').hidden));
  renderImpactChain(layers);renderImpactEvidence();renderImpactNodeDetail(selectedImpactNodeId);
}
function renderImpactChain(layers=[]) {
  const target=$('impact-chain');target.replaceChildren();
  if(!layers.length){target.append(element('p','','按 STEP FORWARD 或 PLAY 展开传播层级。'));return;}
  for(const item of layers){const block=element('div','impact-chain-item');block.dataset.direction=item.direction||'uncertain';block.append(element('strong','',`L${item.level} · ${item.node.name} · ${item.direction||'source'}`),element('small','',`${item.node.node_type} · ${item.path_count||0} 条路径 · 假设净强度 ${(item.net_strength||0).toFixed(2)} · ${item.node.description}`));target.append(block);}
}
function renderImpactEvidence() {
  const target=$('impact-evidence');target.replaceChildren();if(!impactNetwork)return;
  const shown=new Set(impactNetwork.nodes.filter(item=>item.level<=impactStep).map(item=>item.node.node_id));
  const edges=impactScenario.edges.filter(relation=>shown.has(relation.target_node));
  if(!edges.length){target.append(element('p','','尚未展开关系。'));return;}
  for(const relation of edges){const block=element('div','impact-evidence-item');const source=impactNetwork.graph.nodes.get(relation.source_node),destination=impactNetwork.graph.nodes.get(relation.target_node);block.append(element('b','',`${source.name} → ${destination.name}`),element('div','',relation.evidence),element('small','',`方向 ${relation.impact_direction} · 情景强度 ${relation.impact_strength.toFixed(2)} · 证据支持度 ${relation.confidence.toFixed(2)} · 时限 ${relation.time_horizon}`));target.append(block);}
}
function renderImpactNodeDetail(id) {
  const target=$('impact-node-detail');target.replaceChildren();const item=impactNetwork?.nodes.find(entry=>entry.node.node_id===id);
  if(!item){target.append(element('p','','选择一个已显示的传播节点查看解释和证据。'));return;}
  const risk=Math.abs(item.net_strength||0)>=.65?'高':Math.abs(item.net_strength||0)>=.35?'中':'低';
  target.append(element('strong','',`L${item.level} · ${item.node.name} · ${item.direction||'source'} · 风险 ${risk}`),element('p','',item.node.description));
  const paths=impactNetwork.paths.filter(path=>path.id===id).slice(0,3);
  if(!paths.length)target.append(element('p','','传播来源：事件源；无上游传播路径。'));
  paths.forEach(path=>{const names=path.path.map(nodeId=>impactNetwork.graph.nodes.get(nodeId).name).join(' → ');target.append(element('p','',`传播来源与路径：${names}；方向 ${path.direction<0?'负向':path.direction>0?'正向':'不确定'}，假设强度 ${path.strength.toFixed(2)}，证据支持度 ${path.confidence.toFixed(2)}。${path.relation.evidence}`));});
  const assetIds=new Set(impactNetwork.paths.filter(path=>path.path.includes(id)&&impactNetwork.graph.nodes.get(path.id)?.node_type==='ASSET').map(path=>path.id));
  const assets=[...assetIds].map(assetId=>{const asset=impactNetwork.graph.nodes.get(assetId);return asset.asset_symbol||asset.name;});
  target.append(element('p','',`关联事件：${impactScenario.name} · 潜在影响资产：${assets.join('、')||'当前传播图未定义资产节点'}`),element('p','','数据来源：DEMO 规则关系；情景假设，不代表实际市场关联。'));
}
function setImpactStep(step) {
  if(!impactNetwork)return;impactStep=Math.max(0,Math.min(impactNetwork.maxDepth,step));
  if(selectedImpactNodeId&&!impactNetwork.nodes.some(item=>item.node.node_id===selectedImpactNodeId&&item.level<=impactStep))selectedImpactNodeId=impactNetwork.sourceId;
  $('impact-step').textContent=`L${impactStep} / L${impactNetwork.maxDepth} · SIMULATED SCENARIO`;
  atlas?.setButterfly(butterflyMode?impactNetwork:null,impactStep,selectedImpactNodeId,selectImpactNode,impactControls());renderImpactPanel(events.find(event=>event.event_id===selectedId));
}
function pauseImpact() { clearInterval(impactTimer);impactTimer=null;$('impact-play').setAttribute('aria-pressed','false'); }
function playImpact() {
  if(!butterflyMode||!impactNetwork){announce('请先进入 BUTTERFLY VIEW 并选择模拟事件。');return;}
  pauseImpact();if(impactStep>=impactNetwork.maxDepth)setImpactStep(0);$('impact-play').setAttribute('aria-pressed','true');
  impactTimer=setInterval(()=>{if(impactStep>=impactNetwork.maxDepth){pauseImpact();return;}setImpactStep(impactStep+1);if(impactStep>=impactNetwork.maxDepth)pauseImpact();},1250/impactControls().speed);
}
function setButterflyMode(enabled) {
  butterflyMode=Boolean(enabled);pauseImpact();const event=events.find(item=>item.event_id===selectedId);
  if(butterflyMode&&event){const preset=scenarios.forEvent(event);configureImpact(event,preset);atlas?.focusEvent(event.event_id,false);}
  if(!butterflyMode){$('impact-chain').hidden=true;$('impact-evidence').hidden=true;}
  syncButterfly();
}

function renderFeed() {
  const list = $('event-feed');
  list.replaceChildren();
  if (!visible.length) list.append(element('p','empty','没有匹配的事件。请调整筛选条件或点击“重置筛选”。'));
  for (const e of visible) {
    const button = element('button', `event-card sentiment-${e.sentiment}`);
    button.type = 'button'; button.dataset.eventId = e.event_id;
    const top = element('div','card-top');
    const stamp = element('time','',time(e.captured_at)); stamp.dateTime = e.captured_at;
    top.append(stamp,element('span','',e.region),element('span','event-category',e.category));
    const bottom = element('div','card-bottom');
    bottom.append(element('span','',statusLabel(e)),element('span','importance',`${e.importance}/5 ${e.importance>=4?'HIGH':'NORMAL'}`));
    button.append(top,element('strong','',e.title),bottom,element('div','card-source',e.source));
    button.title = `${e.source} · ${e.country} · ${e.event_id}`;
    button.addEventListener('click',() => selectEvent(e.event_id, true));
    list.append(button);
  }
  $('visible-count').textContent = `${visible.length} / ${events.length} 条模拟事件`;
}

function renderTimeline() {
  $('timeline').replaceChildren();
  const chronological = [...visible].sort((a,b) => Date.parse(a.captured_at)-Date.parse(b.captured_at));
  for (const e of chronological) {
    const button = element('button',`timeline-event sentiment-${e.sentiment}`);
    button.type = 'button'; button.dataset.eventId = e.event_id;
    const stamp = element('time','',time(e.captured_at)); stamp.dateTime = e.captured_at;
    button.append(stamp,element('strong','',e.title),element('small','',`${e.region} / ${e.category} / DEMO`));
    button.addEventListener('click',() => selectEvent(e.event_id, true));
    $('timeline').append(button);
  }
  if (!visible.length) $('timeline').append(element('p','empty','当前筛选范围没有事件。'));
}

function section(title, text) {
  const block = element('section','detail-section');
  block.append(element('h4','',title),element('p','',text));
  return block;
}
function renderDetail(e) {
  const detail = $('analysis-detail'); detail.replaceChildren();
  $('transmission-path').replaceChildren();
  $('location-card').replaceChildren(element('span','eyebrow','SELECTED LOCATION'));
  if (!e) {
    detail.append(element('p','empty','当前筛选下没有可分析事件。重置筛选可恢复全部模拟场景。'));
    $('location-card').append(element('strong','','无匹配事件'),element('span','','等待选择'));
    $('transmission-path').append(element('p','empty','没有选中事件。'));
    return;
  }
  $('location-card').append(element('strong','',`${e.region} / ${e.country}`),element('span','',`${Math.abs(e.latitude).toFixed(2)}° ${e.latitude>=0?'N':'S'} · ${Math.abs(e.longitude).toFixed(2)}° ${e.longitude>=0?'E':'W'} / DEMO`));
  detail.append(element('div','detail-id',`${e.event_id} / DEMO SCENARIO`),element('h3','',e.title));
  const tags = element('div','detail-tags');
  tags.append(element('span','tag',e.category),element('span',`tag sentiment-tag sentiment-${e.sentiment}`,sentimentLabel(e)),element('span','tag',`重要程度 ${e.importance}/5`));
  detail.append(tags,section('事件摘要 / SUMMARY',e.analysis.summary));
  const grid = element('dl','detail-grid');
  for (const [label,value] of [['涉及国家 / 地区',e.country],['行业',e.analysis.sectors.join('、')],['来源可靠性',e.analysis.reliability],['分析状态',statusLabel(e)]]) {
    const cell = element('div'); cell.append(element('dt','',label),element('dd','',value)); grid.append(cell);
  }
  detail.append(grid);
  const assets = element('section','detail-section'); assets.append(element('h4','','潜在关联资产 / RESEARCH LABELS'));
  const assetTags = element('div','asset-tags'); e.related_assets.forEach(asset => assetTags.append(element('span','tag',asset))); assets.append(assetTags); detail.append(assets);
  detail.append(section('模拟分析结果 / PRESET ANALYSIS',e.analysis.result));
  const confidence = element('section','detail-section');
  const heading = element('div','confidence-head'); heading.append(element('h4','','分析置信度 · 模拟字段'),element('b','',e.analysis_status==='queued'||e.confidence===null?'待分析':`${Math.round(e.confidence*100)}%`));
  const track = element('div','confidence-track'); const fill = element('span'); fill.style.width = `${e.analysis_status==='queued'?0:(e.confidence??0)*100}%`; track.append(fill);
  confidence.append(heading,track,element('p','','预置演示值，非经校准的事实或收益预测概率。')); detail.append(confidence);
  const times = element('div','detail-times');
  times.append(element('span','',`发布时间 ${time(e.published_at,true)}`),element('span','',`捕捉时间 ${time(e.captured_at,true)}`),element('span','',`来源 ${e.source}`));
  if (e.source_url) {
    const link = element('a','','查看来源 ↗'); link.href = e.source_url; link.target='_blank'; link.rel='noopener noreferrer'; times.append(link);
  } else times.append(element('span','','虚构演示事件，无真实新闻原文链接。'));
  detail.append(times);
  e.analysis.path.forEach((text,i) => {
    if (i) $('transmission-path').append(element('span','path-arrow','→'));
    const node = element('div','path-node'); node.append(element('span','',`0${i+1} / HYPOTHESIS`),element('div','',text)); $('transmission-path').append(node);
  });
}

function selectEvent(id, scroll = false) {
  selectedId = visible.some(e => e.event_id===id) ? id : null;
  const focusedMarker = document.activeElement?.closest?.('.map-event')?.dataset.eventId;
  atlas.setEvents(visible,selectedId);
  if(scroll)atlas.focusEvent(selectedId);
  document.querySelectorAll('[data-event-id]').forEach(button => button.setAttribute('aria-pressed',String(button.dataset.eventId===selectedId)));
  if (focusedMarker) Array.from($('atlas').querySelectorAll('[data-event-id]')).find(el => el.dataset.eventId===focusedMarker)?.focus({preventScroll:true});
  const selectedEvent=visible.find(e => e.event_id===selectedId);renderDetail(selectedEvent);
  if(butterflyMode&&selectedEvent){configureImpact(selectedEvent);syncButterfly();}
  if (scroll) {
    const target = Array.from($('timeline').children).find(el => el.dataset.eventId===selectedId);
    if (target) {
      const host = $('timeline');
      host.scrollTo({left:host.scrollLeft+target.getBoundingClientRect().left-host.getBoundingClientRect().left-host.clientWidth/2+target.clientWidth/2,behavior:reducedMotion.matches?'instant':'smooth'});
    }
  }
}
function applyFilters() {
  visible = filterEvents(events,{query:$('search').value,country:$('country').value,category:$('category').value,priority,hours:$('time-range').value,sort:$('sort').value});
  renderFeed(); renderTimeline();
  selectEvent(visible.some(e => e.event_id===selectedId)?selectedId:visible[0]?.event_id);
}
function stopTour() {
  clearInterval(tourTimer); tourTimer=null;
  $('tour').textContent='事件巡览：关闭'; $('tour').setAttribute('aria-pressed','false');
}
function updateClock() { $('utc-clock').textContent = new Date().toISOString().slice(11,19)+' UTC'; }

async function load() {
  loadController?.abort(); loadController = new AbortController();
  const controller = loadController;
  const timeout = setTimeout(() => controller.abort(),10000);
  $('load-error').hidden=true; $('workspace').setAttribute('aria-busy','true'); $('retry').disabled=true;
  try {
    const snapshot = await source.load({signal:controller.signal});
    if (controller!==loadController) return;
    events=snapshot.events;
    for (const [id,key] of [['country','country'],['category','category']]) {
      const select=$(id); while(select.options.length>1) select.remove(1);
      Array.from(new Set(events.map(e=>e[key]))).sort((a,b)=>a.localeCompare(b,'zh-CN')).forEach(value=>{const option=element('option','',value);option.value=value;select.append(option);});
    }
    const sampleDay=events.filter(e=>e.captured_at.slice(0,10)===snapshot.snapshot_date);
    $('event-count').textContent=String(sampleDay.length).padStart(2,'0');
    $('priority-count').textContent=String(sampleDay.filter(e=>e.importance>=4).length).padStart(2,'0');
    $('task-count').textContent=String(sampleDay.filter(e=>e.analysis_status==='queued').length).padStart(2,'0');
    $('snapshot-date').textContent=`${snapshot.snapshot_date} / UTC / DEMO`;
    $('system-status').replaceChildren(element('i','status-dot'),document.createTextNode('页面就绪 · 本地运行'));
    $('data-status').textContent='本地 DEMO 快照已载入';
    applyFilters();
    const route=new URLSearchParams(location.search),requestedScenario=scenarios.get(route.get('scenario')||'SCENARIO-OIL-001');
    if(route.get('view')==='butterfly'&&requestedScenario){
      const requestedEvent=events.find(event=>event.event_id===requestedScenario.event_id);
      if(requestedEvent){selectedId=requestedEvent.event_id;butterflyMode=true;configureImpact(requestedEvent,requestedScenario);selectEvent(requestedEvent.event_id,true);$('impact-scenario').value=requestedScenario.scenario_id;syncButterfly();}
    }
  } catch(error) {
    if (controller!==loadController) return;
    $('load-error').hidden=false;
    $('error-message').textContent=error.name==='AbortError'?'读取超时，请确认本地服务和静态文件可访问。':error.message;
    $('data-status').textContent='本地数据加载失败'; $('system-status').textContent='需要检查数据连接';
    $('event-feed').replaceChildren(element('p','empty','加载失败；请使用上方“重新加载”按钮。'));
  } finally {
    clearTimeout(timeout);
    if(controller===loadController){$('workspace').setAttribute('aria-busy','false');$('retry').disabled=false;}
  }
}
controls.forEach(id => $(id).addEventListener(id==='search'?'input':'change',applyFilters));
$('priority-only').addEventListener('click',()=>{priority=!priority;$('priority-only').setAttribute('aria-pressed',String(priority));applyFilters();});
$('reset').addEventListener('click',()=>{stopTour();$('search').value='';$('country').value='all';$('category').value='all';$('sort').value='time';$('time-range').value='all';priority=false;$('priority-only').setAttribute('aria-pressed','false');applyFilters();});
$('tour').addEventListener('click',()=>{
  if(tourTimer){stopTour();return;}
  if(!visible.length){announce('当前没有可巡览的事件，请先调整筛选。');return;}
  announce('');$('tour').textContent='事件巡览：开启';$('tour').setAttribute('aria-pressed','true');
  tourTimer=setInterval(()=>{if(document.hidden||!visible.length)return;const index=visible.findIndex(e=>e.event_id===selectedId);selectEvent(visible[(index+1)%visible.length].event_id,true);},6000);
});
$('fullscreen').addEventListener('click',async()=>{
  try{if(document.fullscreenElement)await document.exitFullscreen();else if(document.documentElement.requestFullscreen)await document.documentElement.requestFullscreen();else throw new Error('当前浏览器不支持页面全屏。');announce('');}
  catch(error){announce(`无法切换全屏：${error.message}`);}
});
document.addEventListener('fullscreenchange',()=>{$('fullscreen').textContent=document.fullscreenElement?'⛶ 退出全屏':'⛶ 全屏';});
$('retry').addEventListener('click',load);
$('globe-auto').addEventListener('click',()=>atlas.toggleRotation());
$('globe-neural').addEventListener('click',()=>atlas.toggleNeural());
$('globe-in').addEventListener('click',()=>atlas.adjustZoom(-100));
$('globe-out').addEventListener('click',()=>atlas.adjustZoom(100));
$('globe-reset').addEventListener('click',()=>atlas.resetView());
$('globe-mode').addEventListener('click',()=>atlas.toggleMode());
document.getElementById('view-mode').addEventListener('click',()=>setButterflyMode(!butterflyMode));
$('impact-scenario').addEventListener('change',()=>{
  const preset=scenarios.get($('impact-scenario').value);if(!preset)return;
  const event=events.find(item=>item.event_id===preset.event_id);if(!event){announce('请先等待本地 DEMO 事件载入。');return;}
  $('search').value='';$('country').value='all';$('category').value='all';$('sort').value='time';$('time-range').value='all';priority=false;$('priority-only').setAttribute('aria-pressed','false');
  butterflyMode=true;applyFilters();selectedId=event.event_id;configureImpact(event,preset);selectEvent(event.event_id,true);syncButterfly();announce('已载入 SIMULATED SCENARIO；传播关系仅为规则驱动的假设。');
});
$('impact-play').addEventListener('click',()=>{
  playImpact();
});
$('impact-pause').addEventListener('click',pauseImpact);
$('impact-forward').addEventListener('click',()=>{pauseImpact();setImpactStep(impactStep+1);});
$('impact-back').addEventListener('click',()=>{pauseImpact();setImpactStep(impactStep-1);});
$('impact-reset').addEventListener('click',()=>{pauseImpact();setImpactStep(0);});
$('impact-node-search').addEventListener('input',()=>syncButterfly());
$('impact-risk-filter').addEventListener('change',()=>syncButterfly());
$('impact-speed').addEventListener('input',event=>{const wasPlaying=Boolean(impactTimer),speed=Number(event.target.value);$('impact-speed-value').textContent=`${speed.toFixed(2).replace(/0$/,'')}×`;syncButterfly();if(wasPlaying)playImpact();});
$('impact-chain-toggle').addEventListener('click',()=>{const target=$('impact-chain');target.hidden=!target.hidden;$('impact-chain-toggle').setAttribute('aria-expanded',String(!target.hidden));});
$('impact-evidence-toggle').addEventListener('click',()=>{const target=$('impact-evidence');target.hidden=!target.hidden;$('impact-evidence-toggle').setAttribute('aria-expanded',String(!target.hidden));});
function startClock(){updateClock();clockTimer=setInterval(updateClock,1000);}
window.addEventListener('pagehide',()=>{stopTour();pauseImpact();clearInterval(clockTimer);clockTimer=null;loadController?.abort();atlas.dispose();});
window.addEventListener('pageshow',event=>{if(event.persisted){atlas=createAtlas();startClock();load();}});
startClock();load();
