import {mountCommandCenter} from './office-command.js';
import {employees,stateLabels,employeeSnapshot,demoFrames,confirmedTransfer} from './office-model.js';
import {buildOffice,character} from './office-scene.js';
import {OfficeTour} from './office-tour.js';
import {OfficeCamera} from './office-camera.js';
const $=id=>document.getElementById(id),scene=document.querySelector('.office-scene');
$('office-svg').innerHTML=buildOffice();
let tour;
const camera=new OfficeCamera($('office-svg'),{onManual:()=>tour?.stop(),label:$('office-zoom-label'),hideTooltip:()=>{$('office-tooltip').hidden=true;}});
const transferPaths=[...document.querySelectorAll('.office-flow')];
function setTransfer(transfer){for(const path of transferPaths){path.classList.toggle('active',!!transfer&&path.dataset.from===transfer.from&&path.dataset.to===transfer.to);path.dataset.kind=transfer?.kind||'result';}}
function showEmployee(open){$('office-employee-card').hidden=!open;$('office-panel-toggle').setAttribute('aria-expanded',String(open));}
$('office-panel-toggle').addEventListener('click',()=>showEmployee($('office-employee-card').hidden));
$('office-panel-close').addEventListener('click',()=>showEmployee(false));
$('office-zoom-in').addEventListener('click',()=>{tour.stop();camera.zoom(1.15);});
$('office-zoom-out').addEventListener('click',()=>{tour.stop();camera.zoom(1/1.15);});
$('office-camera-reset').addEventListener('click',()=>{tour.stop();camera.reset();});
const agentNodes=new Map(employees.map(e=>[e.id,document.querySelector(`[data-agent="${e.id}"]`)]));
const deskNodes=new Map(employees.map(e=>[e.id,document.querySelector(`[data-workstation="${e.id}"]`)]));
for(const e of employees){
  const button=document.createElement('button');button.type='button';button.dataset.department=e.id;button.style.setProperty('--accent',e.color);button.setAttribute('aria-label',`突出显示${e.department}`);
  const light=document.createElement('i');light.className='state-light';light.setAttribute('aria-hidden','true');const name=document.createElement('b');name.textContent=e.department;const state=document.createElement('small');state.textContent='正在读取';button.append(light,name,state);$('department-status').append(button);
}
let lastRendered='',lastLogKey='',commandTask=null;
let selected=employees[0],task=null,mode='live',demoIndex=-1,demoStates={},paused=false,demoTimer=null,polling=false,disposed=false;
tour=new OfficeTour({visit:index=>{select(employees[index].id,{open:false});camera.focus(agentNodes.get(employees[index].id),950);},changed:active=>{$('office-camera-tour').setAttribute('aria-pressed',String(active));$('office-camera-tour').textContent=active?'停止巡游':'自动巡游';}});
$('office-camera-focus').addEventListener('click',()=>{tour.stop();camera.focus(agentNodes.get(selected.id));});
$('office-camera-tour').addEventListener('click',()=>{if(tour.active)tour.stop();else if(!document.body.matches('.low-motion,.paused'))tour.start();else $('office-camera-tour').textContent='请先恢复动态效果';});
document.addEventListener('visibilitychange',()=>{tour.suspend(document.hidden||paused);if(document.hidden)camera.cancel();});
const controller=new AbortController(),motion=matchMedia('(prefers-reduced-motion: reduce)');
document.body.classList.toggle('low-motion',motion.matches);
$('office-reduced').setAttribute('aria-pressed',String(motion.matches));
function snapshot(e){
  if(mode==='live')return employeeSnapshot(e,commandTask||task);
  const state=demoStates[e.id]||'WAITING';
  return {state,task:'DEMO · 能源供应情景协作巡演',progress:`演示步骤 ${Math.max(0,demoIndex+1)} / ${demoFrames.length}，非研究进度`,logs:demoFrames.slice(0,demoIndex+1).filter(f=>f.id===e.id).map(f=>({message:f.message})),note:'独立预设动画；未调用研究计算、未生成报告或订单。'};
}
function globalWorkflowTask(){return task;}
function render(){
  const task=commandTask||globalWorkflowTask();
  const renderKey=JSON.stringify([selected.id,mode,demoIndex,demoStates,task?.task_id,task?.updated_at,task?.status,task?.logs?.length]);
  if(renderKey===lastRendered)return;lastRendered=renderKey;
  for(const e of employees){const value=snapshot(e),agent=agentNodes.get(e.id),desk=deskNodes.get(e.id);agent.dataset.state=value.state;desk.dataset.state=value.state;agent.classList.toggle('selected',e.id===selected.id);desk.classList.toggle('selected',e.id===selected.id);agent.setAttribute('aria-label',`${e.name}，${stateLabels[value.state]}，查看员工详情`);
    const status=$('department-status').querySelector(`[data-department="${e.id}"]`);status.classList.toggle('selected',e.id===selected.id);status.dataset.state=value.state;status.querySelector('small').textContent=value.progress==='本阶段已跳过'?'已跳过':stateLabels[value.state];status.setAttribute('aria-pressed',String(e.id===selected.id));
  }
  const current=snapshot(selected);
  scene.dataset.selection=selected.id;document.querySelector('.employee-panel').dataset.state=current.state;
  document.querySelector('.panel-top .eyebrow').textContent=`TEAM MEMBER / ${String(employees.indexOf(selected)+1).padStart(2,'0')}`;
  $('employee-name').textContent=selected.name;$('employee-role').textContent=selected.role;$('employee-department').textContent=selected.department;
  $('employee-state').textContent=stateLabels[current.state];$('employee-task').textContent=current.task;$('employee-progress').textContent=current.progress;$('employee-note').textContent=current.note;
  if($('employee-portrait').dataset.employee!==selected.id){$('employee-portrait').innerHTML=`<svg viewBox="-45 -110 90 145" style="--accent:${selected.color}">${character(selected)}</svg>`;$('employee-portrait').dataset.employee=selected.id;}
  $('employee-detail').href=selected.route;$('employee-detail').replaceChildren(document.createTextNode(`进入${selected.department} ↗`));
  const logs=current.logs.slice(-3).reverse();
  const logKey=JSON.stringify([selected.id,mode,logs]);
  if(logKey!==lastLogKey){lastLogKey=logKey;$('employee-logs').replaceChildren();for(const log of logs.length?logs:[{message:'暂无工作记录；未执行研究。'}]){const li=document.createElement('li');li.textContent=log.message;if(log.created_at){const time=document.createElement('time');time.textContent=log.created_at;li.prepend(time);}$('employee-logs').append(li);}}
  $('office-live').classList.toggle('active',mode==='live');$('office-demo').classList.toggle('active',mode==='demo');
  $('office-provenance').textContent=mode==='demo'?'DEMO / 预设动画':(commandTask?(commandTask.is_demo?'DEMO 数据计算':'本地量化任务快照'):'任务接口快照');
  $('office-task-id').textContent=mode==='demo'?'只改变场景；不写入研究数据':task?`任务 ${task.task_id.slice(0,12)} · ${task.status} · 非自主 Agent`:'当前无任务 · 员工待命';
  if(mode==='live'){
    $('office-story').textContent=task?`${task.title} · ${task.status==='completed'?'流程已结束，具体结果以各阶段日志为准。':'读取当前阶段状态。'}`:'员工待命。可以查看工作站，或播放明确标记的 DEMO 巡演。';
    scene.dataset.flow=task?.status==='running'?task.stage:'';
    setTransfer(confirmedTransfer(task));
  }
}
function select(id,{focus=false,open=true}={}){const e=employees.find(item=>item.id===id);if(e){selected=e;render();if(open)showEmployee(true);if(focus){tour.stop();camera.focus(agentNodes.get(e.id));}}}
$('office-svg').addEventListener('click',event=>{
  $('office-tooltip').hidden=true;
  const agent=event.target.closest('[data-agent]');if(agent){select(agent.dataset.agent,{focus:true});return;}
  const module=event.target.closest('[data-module]');if(module){const e=employees.find(item=>item.id===module.dataset.module);if(e)location.assign(e.route);return;}
  const label=event.target.closest('[data-department-label]');if(label){select(label.dataset.departmentLabel,{open:false});return;}
  const desk=event.target.closest('[data-workstation]');if(desk)select(desk.dataset.workstation);
});
$('department-status').addEventListener('click',event=>{const button=event.target.closest('[data-department]');if(button)select(button.dataset.department,{open:false});});
function tooltip(agent,x,y){
  const e=employees.find(item=>item.id===agent.dataset.agent);if(!e)return;const value=snapshot(e),tip=$('office-tooltip');
  const name=document.createElement('strong'),detail=document.createElement('span');name.textContent=e.name;detail.textContent=`${e.role} · ${stateLabels[value.state]}${mode==='demo'?' · DEMO':''}`;tip.replaceChildren(name,detail);tip.hidden=false;
  tip.style.left=`${Math.max(8,Math.min(innerWidth-240,x+16))}px`;tip.style.top=`${Math.max(8,Math.min(innerHeight-85,y+16))}px`;
}
$('office-svg').addEventListener('pointerover',event=>{const agent=event.target.closest('[data-agent]');if(agent&&!agent.contains(event.relatedTarget))tooltip(agent,event.clientX,event.clientY);});
$('office-svg').addEventListener('pointerout',event=>{const agent=event.target.closest('[data-agent]');if(agent&&!agent.contains(event.relatedTarget))$('office-tooltip').hidden=true;});
$('office-svg').addEventListener('focusin',event=>{const agent=event.target.closest('[data-agent]');if(agent){const rect=agent.getBoundingClientRect();tooltip(agent,rect.right,rect.top);}});
$('office-svg').addEventListener('focusout',()=>{$('office-tooltip').hidden=true;});
$('office-svg').addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();event.target.dispatchEvent(new MouseEvent('click',{bubbles:true}));}});
async function refresh(){
  if(polling||disposed)return;polling=true;
  try{
    const list=await fetch('/api/workflow',{cache:'no-store',signal:controller.signal});if(!list.ok)throw Error(`HTTP ${list.status}`);
    const data=await list.json(),latest=data.tasks.find(item=>item.status==='running')||data.tasks[0];
    if(latest){const response=await fetch(`/api/workflow?id=${encodeURIComponent(latest.task_id)}`,{cache:'no-store',signal:controller.signal});if(!response.ok)throw Error(`HTTP ${response.status}`);task=await response.json();}else task=null;
    $('office-connection').textContent='任务服务已连接 · 只读';document.querySelector('.connection').dataset.online='true';
  }catch(error){if(error.name==='AbortError')return;task=null;$('office-connection').textContent='任务服务暂不可用 · 无运行状态';document.querySelector('.connection').dataset.online='false';}
  finally{polling=false;if(!disposed)render();}
}
function demoStep(){
  if(paused||mode!=='demo'||disposed)return;
  demoIndex++;
  if(demoIndex>=demoFrames.length){demoIndex=demoFrames.length-1;scene.dataset.flow='report';setTransfer({from:'trading',to:'report'});$('office-demo').innerHTML='重新播放巡演 <em>DEMO</em>';return;}
  const frame=demoFrames[demoIndex];demoStates[frame.id]=frame.state;selected=employees.find(e=>e.id===frame.id);
  for(const id of frame.complete||[])demoStates[id]='COMPLETED';
  setTransfer(frame.handoff||null);
  $('office-story').textContent=frame.message;scene.dataset.flow=frame.id;render();
  demoTimer=setTimeout(demoStep,2600);
}
$('office-demo').addEventListener('click',()=>{clearTimeout(demoTimer);mode='demo';demoIndex=-1;demoStates={};paused=false;tour.stop();camera.reset();document.body.classList.remove('paused');$('office-pause').textContent='暂停动画';$('office-pause').setAttribute('aria-pressed','false');demoStep();});
$('office-live').addEventListener('click',()=>{clearTimeout(demoTimer);mode='live';render();refresh();});
$('office-pause').addEventListener('click',()=>{paused=!paused;tour.suspend(paused||document.hidden);if(paused)camera.cancel();document.body.classList.toggle('paused',paused);$('office-pause').setAttribute('aria-pressed',String(paused));$('office-pause').textContent=paused?'继续动画':'暂停动画';clearTimeout(demoTimer);if(!paused&&mode==='demo'&&demoIndex<demoFrames.length-1)demoTimer=setTimeout(demoStep,2600);});
$('office-reduced').addEventListener('click',()=>{const low=document.body.classList.toggle('low-motion');if(low){tour.stop();camera.cancel();}$('office-reduced').setAttribute('aria-pressed',String(low));});
$('office-showcase').addEventListener('click',()=>{const on=document.body.classList.toggle('showcase');$('office-showcase').setAttribute('aria-pressed',String(on));$('office-showcase').textContent=on?'退出 SHOWCASE':'SHOWCASE 录屏';});
$('office-fullscreen').addEventListener('click',async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await document.documentElement.requestFullscreen();}catch{$('office-fullscreen').textContent='浏览器不支持全屏';}});
document.addEventListener('fullscreenchange',()=>{$('office-fullscreen').textContent=document.fullscreenElement?'退出全屏':'全屏';});
function tick(){$('office-clock').textContent=new Date().toISOString().slice(11,19)+' UTC';}
const destroyCommand=mountCommandCenter((value,explicit)=>{commandTask=value;if(explicit){clearTimeout(demoTimer);mode='live';}render();});
tick();render();refresh();const clock=setInterval(tick,1000),poll=setInterval(()=>{if(!document.hidden)refresh();},5000);
window.addEventListener('pagehide',event=>{if(!event.persisted){disposed=true;destroyCommand();controller.abort();tour.destroy();camera.destroy();clearTimeout(demoTimer);clearInterval(clock);clearInterval(poll);}});
