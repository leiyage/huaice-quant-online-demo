/** CEO task UI. Polling only observes real backend execution. */
export function mountCommandCenter(onTask){
  const button=document.createElement('button');button.id='office-command-open';button.textContent='启动AI金融研究';
  document.querySelector('.office-toolbar').insertBefore(button,document.querySelector('.navigation-hint'));
  const dialog=document.createElement('dialog');dialog.className='command-center';dialog.setAttribute('aria-labelledby','command-title');
  dialog.innerHTML=`<header><div><small>CEO COMMAND CENTER</small><h2 id="command-title">中央任务指挥台</h2></div><button type="button" id="command-close" aria-label="关闭中央指挥台">×</button></header>
  <p>规则解析 · 未接入 LLM。仅运行当前加载组合的历史策略对照；不操作模拟账户。</p>
  <form id="command-form"><label for="command-description">研究任务</label><textarea id="command-description" minlength="4" maxlength="1000" required placeholder="对当前组合进行历史策略对照研究">对当前组合进行历史策略对照研究</textarea><div class="command-actions"><button type="submit" id="command-plan">生成研究计划</button><button type="button" id="command-submit" hidden>确认并执行研究</button></div></form>
  <p id="command-message" role="status" aria-live="polite"></p><section id="command-plan-view" hidden></section>
  <section><h3>研究任务</h3><div id="command-task-list"></div></section><section id="command-result" hidden></section>`;
  document.body.append(dialog);
  const $=id=>dialog.querySelector('#'+id),abort=new AbortController();let pending=false,plan=null,requestKey=null,selected=null,polling=false,disposed=false,lastListKey='',lastTaskKey='';
  const node=(tag,value)=>{const n=document.createElement(tag);n.textContent=value;return n;};
  const message=value=>{$('command-message').textContent=value;};
  async function request(payload){const response=await fetch('/api/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),signal:abort.signal});let data;try{data=await response.json();}catch{throw Error('中央任务接口尚未加载，请在安全时重启服务。');}if(!response.ok)throw Error(data.error||`HTTP ${response.status}`);return data;}
  function adapted(task){return task?{...task,status:task.status.toLowerCase(),stage:'quant',updated_at:task.updated_at+'|'+task.progress,title:task.title,logs:task.logs,result:task.result}:null;}
  function showResult(task){
    const taskKey=JSON.stringify([task?.task_id,task?.updated_at,task?.status,task?.progress]);if(taskKey===lastTaskKey)return;lastTaskKey=taskKey;
    const host=$('command-result');host.replaceChildren();host.hidden=!task;if(!task)return;
    host.append(node('h3',(task.is_demo?'DEMO · ':'')+task.title),node('p',`${task.task_id} · ${task.status}`),node('p',task.progress),node('p',task.description),node('p',task.data_warning||'行情来源以实验清单为准。'));
    if(task.error)host.append(node('p',task.error));
    if(['PENDING','RUNNING'].includes(task.status)){const cancel=node('button',task.cancel_requested?'取消请求已提交':'取消任务');cancel.disabled=task.cancel_requested;cancel.addEventListener('click',async()=>{cancel.disabled=true;try{await request({command:'cancel',task_id:task.task_id});await refresh();}catch(error){message(error.message);cancel.disabled=false;}});host.append(cancel);}
    if(task.result){const result=task.result;for(const [label,key]of [['研究目标','objective'],['数据来源','data_source'],['研究方法','method'],['主要发现','findings'],['研究结论','conclusion']])if(result[key])host.append(node('h4',label),node('p',result[key]));
      if(result.risk_warnings)host.append(node('h4','风险与局限'),node('p',result.risk_warnings.join('；')));
      const quant=result.department_results?.quant;if(quant){const table=document.createElement('table');const tr=document.createElement('tr');for(const v of ['方案','CAGR','最大回撤','Sharpe'])tr.append(node('th',v));table.append(tr);for(const [key,metrics]of Object.entries(quant.metrics)){const row=document.createElement('tr');for(const v of [quant.labels[key],Number.isFinite(metrics.cagr)?(metrics.cagr*100).toFixed(2)+'%':'未提供',Number.isFinite(metrics.max_drawdown)?(metrics.max_drawdown*100).toFixed(2)+'%':'未提供',Number.isFinite(metrics.sharpe)?metrics.sharpe.toFixed(3):'未提供'])row.append(node('td',v));table.append(row);}host.append(table,node('p','指标来自实际历史计算；行情来源完整性见报告。'));}
      for(const [key,label]of [['report_url','打开完整研究报告 ↗'],['archive_url','下载可复现实验档案']])if(result[key]){const a=node('a',label);a.href=result[key];a.target='_blank';a.rel='noopener';host.append(a);}
      if(result.note)host.append(node('p',result.note));
    }
    const details=document.createElement('details');details.append(node('summary','真实执行日志'));for(const log of task.logs)details.append(node('p',`${log.created_at} · ${log.status} · ${log.message}`));host.append(details);
  }
  async function refresh(){
    if(polling||disposed)return;polling=true;
    try{const response=await fetch('/api/command',{cache:'no-store',signal:abort.signal});if(!response.ok)throw Error(response.status===404?'中央任务接口尚未加载，请在安全时重启服务。':`任务查询失败 HTTP ${response.status}`);const {tasks}=await response.json();
      const host=$('command-task-list');
      if(!selected&&tasks.length)selected=(tasks.find(t=>['RUNNING','PENDING'].includes(t.status))||tasks[0]).task_id;
      const listKey=JSON.stringify([selected,tasks.map(t=>[t.task_id,t.status,t.updated_at,t.progress])]);if(listKey!==lastListKey){lastListKey=listKey;host.replaceChildren();if(!tasks.length)host.append(node('p','尚无中央研究任务。'));
      for(const task of tasks){const b=node('button',`${task.title} · ${task.status} · ${task.task_id.slice(0,8)}`);b.setAttribute('aria-pressed',String(task.task_id===selected));b.addEventListener('click',()=>{selected=task.task_id;showResult(task);onTask(adapted(task),true);refresh();});host.append(b);}}
      const current=tasks.find(t=>t.task_id===selected)||null;showResult(current);onTask(adapted(current));
    }catch(error){if(error.name!=='AbortError')message(error.message);}finally{polling=false;}
  }
  button.addEventListener('click',()=>{dialog.showModal();refresh();});$('command-close').addEventListener('click',()=>dialog.close());
  $('command-description').addEventListener('input',()=>{plan=null;requestKey=null;$('command-submit').hidden=true;$('command-plan-view').hidden=true;});
  $('command-form').addEventListener('submit',async event=>{event.preventDefault();if(pending)return;pending=true;$('command-plan').disabled=true;$('command-description').disabled=true;try{plan=await request({command:'plan',description:$('command-description').value});const host=$('command-plan-view');host.replaceChildren(node('h3','量化研究部 · 执行计划'),node('p',`资产：${plan.assets.join(' / ')}；${plan.parser}`),node('p',plan.method),node('p',plan.steps.join(' → ')),node('p',plan.limitations));host.hidden=false;requestKey=crypto.randomUUID();$('command-submit').hidden=false;message('计划已生成，确认后才会执行真实历史计算。');}catch(error){message(error.message);}finally{pending=false;$('command-plan').disabled=false;$('command-description').disabled=false;}});
  $('command-submit').addEventListener('click',async()=>{if(pending||!plan)return;pending=true;$('command-submit').disabled=true;$('command-description').disabled=true;try{const task=await request({command:'submit',description:$('command-description').value,request_key:requestKey,generation:plan.generation});selected=task.task_id;onTask(adapted(task),true);message('任务已提交；关闭面板不会停止后台执行。');await refresh();}catch(error){message(error.message);}finally{pending=false;$('command-submit').disabled=false;$('command-description').disabled=false;}});
  refresh();const timer=setInterval(()=>{if(!document.hidden)refresh();},1500);
  return ()=>{disposed=true;abort.abort();clearInterval(timer);};
}
