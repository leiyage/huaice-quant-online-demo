/** Presentation of persisted workflow logs. No financial operations or task writes. */
export const departmentStages = ['intelligence','risk','quant','trading'];
const departmentIds = ['global-intelligence','risk-management','quant-research','paper-trading'];
const names = {intelligence:'全球情报部',risk:'风险管理部',quant:'量化研究部',trading:'模拟交易部',report:'研究报告'};
const labels = {waiting:'待命',running:'处理中',completed:'已完成',skipped:'已跳过 · 查看原因',blocked:'等待确认',failed:'执行失败'};

export function logFrames(task) {
  return (task?.logs||[]).filter(log=>Object.hasOwn(names,log.stage)&&Object.hasOwn(labels,log.status))
    .map((log,index)=>({...log,key:`${task.task_id}:${log.seq??index}`}));
}

export function stageStates(frames) {
  const states=Object.fromEntries(Object.keys(names).map(stage=>[stage,'waiting']));
  for(const frame of frames) if(Object.hasOwn(states,frame.stage)&&Object.hasOwn(labels,frame.status)) states[frame.stage]=frame.status;
  return states;
}

export class CollaborationView {
  constructor(root=document) {
    this.root=root;this.task=null;this.frames=[];this.queue=[];this.displayed=[];this.seen=new Set();this.timeout=null;this.paused=false;this.active=false;
    this.floor=root.querySelector('.bank-floor');
    this.cards=departmentStages.map((stage,i)=>root.querySelector(`[data-department="${departmentIds[i]}"]`));
    this.cards.forEach(card=>{
      const line=card?.querySelector('.agent-line');if(!line)return;
      const meter=root.createElement('span');meter.className='agent-meter';meter.setAttribute('aria-hidden','true');
      meter.innerHTML='<i></i><i></i><i></i><i></i><i></i>';line.append(meter);
    });
    this.observer=new ResizeObserver(()=>this.drawConnections());if(this.floor)this.observer.observe(this.floor);
    root.getElementById('collaboration-replay')?.addEventListener('click',()=>this.replay());
    root.getElementById('collaboration-pause')?.addEventListener('click',()=>this.togglePause());
    this.drawConnections();
  }

  update(task,{animate=false}={}) {
    const changed=this.task?.task_id!==task?.task_id;
    this.task=task;this.frames=logFrames(task);
    const actual=this.root.getElementById('collaboration-actual');
    if(actual)actual.textContent=task?`任务 ${task.task_id.slice(0,8)} · 实际状态：${task.status==='completed'?'流程结束':task.status==='running'?'执行中':labels[task.status]||task.status}`:'尚未启动研究任务';
    const replay=this.root.getElementById('collaboration-replay');if(replay)replay.disabled=!this.frames.length||task?.status==='running';
    if(changed){this.stop();this.displayed=[];this.seen.clear();}
    const fresh=this.frames.filter(frame=>!this.seen.has(frame.key));
    fresh.forEach(frame=>this.seen.add(frame.key));
    if(animate||(!changed&&fresh.length)) {
      this.queue.push(...fresh);
      if(!this.active&&!this.paused)this.next();
    }else if(!this.active&&!this.paused) {
      this.displayed=[...this.frames];this.render(this.frames.at(-1));
    }
  }

  replay() {
    if(!this.frames.length||this.task?.status==='running')return;
    this.stop();this.displayed=[];this.queue=[...this.frames];this.next();
  }

  next() {
    if(this.paused)return;
    const frame=this.queue.shift();
    if(!frame){this.active=false;this.render(this.displayed.at(-1));return;}
    this.active=true;this.displayed.push(frame);this.render(frame);
    this.timeout=setTimeout(()=>{this.timeout=null;this.next();},1600);
  }

  togglePause() {
    if(!this.active&&!this.paused)return;
    this.paused=!this.paused;
    if(this.paused){clearTimeout(this.timeout);this.timeout=null;this.active=false;this.render(this.displayed.at(-1));}
    else this.next();
  }

  render(frame) {
    const states=stageStates(this.displayed);
    this.floor.dataset.transfer=frame?.stage||'';
    this.floor.dataset.replaying=String(this.active&&!this.paused);
    this.floor.dataset.paused=String(this.paused);
    this.cards.forEach((card,i)=>{
      if(!card)return;const stage=departmentStages[i],state=states[stage];
      card.dataset.agentState=state;card.dataset.active=String(state==='running');
      card.querySelector('.agent-line').dataset.agentState=state==='running'?'working':state;
      card.querySelector(`[data-agent-status="${stage}"]`).textContent=labels[state];
      const last=this.displayed.filter(log=>log.stage===stage).at(-1);
      card.title=last?.message||`${names[stage]}等待任务`;
    });
    const heading=this.root.getElementById('collaboration-phase');
    if(heading)heading.textContent=frame?`${names[frame.stage]} · ${labels[frame.status]}`:'四部门协作网络';
    const message=this.root.getElementById('collaboration-message');if(message)message.textContent=frame?.message||'启动金融研究后，按照任务接口返回的日志展示部门交接。';
    const mode=this.root.getElementById('collaboration-mode');if(mode)mode.textContent=this.paused?'日志回放已暂停':this.active?'任务日志回放 · 非实时计算耗时':'任务日志快照';
    const pause=this.root.getElementById('collaboration-pause');if(pause){pause.disabled=!this.active&&!this.paused;pause.textContent=this.paused?'继续回放':'暂停回放';}
    this.root.getElementById('collaboration-progress')?.setAttribute('style',`--progress:${this.frames.length?Math.min(100,this.displayed.length/this.frames.length*100):0}%`);
  }

  drawConnections() {
    const svg=this.root.getElementById('department-transfers');if(!svg||!this.floor)return;
    const bounds=this.floor.getBoundingClientRect();svg.setAttribute('viewBox',`0 0 ${bounds.width} ${bounds.height}`);svg.replaceChildren();
    this.cards.slice(0,-1).forEach((card,i)=>{
      if(!card||!this.cards[i+1])return;
      const a=card.getBoundingClientRect(),b=this.cards[i+1].getBoundingClientRect();
      const x1=a.left+a.width/2-bounds.left,x2=b.left+b.width/2-bounds.left,y1=a.bottom-bounds.top+3,y2=b.bottom-bounds.top+3;
      const lane=Math.min(bounds.height-9,Math.max(y1,y2)+16);
      const d=`M ${x1} ${y1} C ${x1} ${lane} ${x2} ${lane} ${x2} ${y2}`;
      for(const className of ['transfer-track','transfer-packet']){
        const path=this.root.createElementNS('http://www.w3.org/2000/svg','path');path.setAttribute('d',d);path.setAttribute('class',className);path.dataset.to=departmentStages[i+1];svg.append(path);
      }
    });
  }

  stop(){clearTimeout(this.timeout);this.timeout=null;this.queue=[];this.active=false;this.paused=false;}
  destroy(){this.stop();this.observer.disconnect();}
}
