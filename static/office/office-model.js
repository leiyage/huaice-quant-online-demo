/** Read-only adapter. No financial calculation or API writes. */
export const employees=[
  {id:'intelligence',name:'INTELLIGENCE AGENT',role:'全球情报分析师',department:'全球情报部',route:'/intelligence',color:'#65d7c3',x:135,y:140,skin:'#d2a386',hair:'#192b38',description:'识别事件 · 整理全球情报'},
  {id:'risk',name:'RISK AGENT',role:'金融风险分析师',department:'风险管理部',route:'/intelligence?view=butterfly&scenario=SCENARIO-OIL-001',color:'#d68f9b',x:470,y:135,skin:'#b88066',hair:'#141e2b',description:'传播路径 · 风险证据'},
  {id:'quant',name:'QUANT AGENT',role:'量化研究员',department:'量化研究部',route:'/#research',color:'#cbb982',x:135,y:470,skin:'#dfb896',hair:'#23394a',description:'历史验证 · 策略研究'},
  {id:'trading',name:'TRADING AGENT',role:'模拟组合研究员',department:'模拟交易部',route:'/#replay',color:'#82b9ec',x:470,y:470,skin:'#a37d69',hair:'#18212f',description:'模拟持仓 · 研究记录'},
];
export const stateLabels={IDLE:'空闲',WORKING:'工作中',ANALYZING:'分析中',WAITING:'等待任务',COMPLETED:'已完成',ERROR:'任务异常'};
export function employeeSnapshot(employee,task){
  if(task?.department&&task.department!==employee.id)return {state:'IDLE',logs:[],task:'当前未分配研究任务',progress:'未参与此任务',note:'此任务仅由量化研究部执行。'};
  const logs=(task?.logs||[]).filter(log=>log.stage===employee.id),last=logs.at(-1);
  let state='IDLE';
  if(last?.status==='running')state=['intelligence','risk'].includes(employee.id)?'ANALYZING':'WORKING';
  else if(last?.status==='completed')state='COMPLETED';
  else if(['failed'].includes(last?.status))state='ERROR';
  else if(task?.status==='cancelled'||task?.status==='pending')state='WAITING';
  else if(task)state='WAITING';
  return {state,logs,task:task?.title||'当前无研究任务',progress:task?.department?task.progress:last?.status==='completed'?'本阶段完成':last?.status==='skipped'?'本阶段已跳过':last?.status==='blocked'?'等待确认':last?.status==='running'?'阶段执行中；接口未提供内部百分比':'尚未开始',note:last?.message||'等待分配任务；未生成分析结果。'};
}
export const demoFrames=[
  {id:'intelligence',state:'ANALYZING',message:'DEMO · 情报分析师正在识别能源供应情景。'},
  {id:'intelligence',state:'WORKING',message:'DEMO · 整理事件字段，准备传递情景资料。'},
  {id:'quant',state:'WORKING',complete:['intelligence'],handoff:{from:'intelligence',to:'quant'},message:'DEMO · 情报资料传递给量化研究员；演示历史研究动作，未执行回测。'},
  {id:'quant',state:'WAITING',message:'DEMO · 演示等待数据；不显示虚构回测收益。'},
  {id:'risk',state:'ANALYZING',handoff:{from:'quant',to:'risk',kind:'status'},message:'DEMO · 量化资料交给风险分析师，查看假设性风险路径。'},
  {id:'risk',state:'COMPLETED',message:'DEMO · 风险审阅动作完成；不代表真实风险评估结论。'},
  {id:'trading',state:'ANALYZING',handoff:{from:'risk',to:'trading'},message:'DEMO · 风险资料传递至模拟交易研究；不生成订单。'},
  {id:'trading',state:'COMPLETED',message:'DEMO · 协作巡演结束，研究资料汇总到中央管理区。'},
];

/** Derive the live handoff strictly from confirmed logs, never from the DEMO order. */
export function confirmedTransfer(task){
  if(task?.status!=='running')return null;
  const known=new Set([...employees.map(e=>e.id),'report']),logs=task.logs||[];
  let index=-1;
  for(let i=logs.length-1;i>=0;i--)if(logs[i].stage===task.stage&&logs[i].status==='running'){index=i;break;}
  if(index<0||!known.has(task.stage))return null;
  const previous=logs.slice(0,index).reverse().find(log=>known.has(log.stage)&&log.stage!==task.stage&&['completed','skipped'].includes(log.status));
  return previous?{from:previous.stage,to:task.stage,kind:previous.status==='skipped'?'status':'result'}:null;
}
