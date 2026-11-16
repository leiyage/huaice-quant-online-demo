(() => {
  const $=id=>document.getElementById(id),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let busy=false;
  async function request(url,body){const control=new AbortController(),timer=setTimeout(()=>control.abort(),30000);try{const r=await fetch(url,{signal:control.signal,...(body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});const d=await r.json();if(!r.ok||d.error)throw new Error(d.error||'读取失败');return d;}finally{clearTimeout(timer);}}
  function safeLink(url){try{const u=new URL(url);return ['https:','http:'].includes(u.protocol)&&!u.username&&!u.password?u.href:'';}catch(_){return '';}}
  window.loadCompanyEvents=async()=>{if(busy)return;busy=true;try{
    const data=await request('/api/company-events');
    $('companyEventStatus').textContent='TSLA新闻 '+data.events.length+' 条 · 本地行情截止 '+(data.quote_date||'—')+' · '+(data.quote_fresh?'价格趋势需确认':'行情不够新，当前不生成买卖操作');
    $('companyEvents').innerHTML=data.events.map(e=>'<article style="border-bottom:1px solid #27324a;padding:12px 0"><b>'+esc(e.event_type)+' · '+esc(e.action)+'</b><div class="hint">'+esc(e.source)+' · 发布时间 '+esc(e.published)+' · 首次可得 '+esc(e.available)+'</div><p>'+esc(e.text)+'</p><div>依据：'+esc(e.evidence.join(' / ')||'未匹配确定事件')+'</div><div class="hint">候选信号 '+esc(e.signal)+'；'+esc(e.block_reason)+'</div>'+(safeLink(e.url)?'<a href="'+esc(safeLink(e.url))+'" target="_blank" rel="noopener noreferrer">查看原文 ↗</a>':'')+' <button data-company-message="'+esc(e.message_id)+'">消息详情</button></article>').join('')||'尚无 TSLA 公司新闻。请在采集控制台点击“下载 TSLA 公司新闻”。';
    $('companyEvents').querySelectorAll('[data-company-message]').forEach(b=>b.onclick=()=>window.showCollectionMessage(b.dataset.companyMessage));
  }catch(e){$('companyEventStatus').textContent='读取失败：'+e.message;}finally{busy=false;}};
  $('refreshCompanyEvents').onclick=window.loadCompanyEvents;
  $('companyEventExperiment').onclick=async function(){this.disabled=true;$('companyEventExperimentResult').textContent='正在对照纯价格策略和TSLA事件策略…';try{
    const d=await request('/api/control',{command:'collection_risk_experiment',mode:'event'});
    $('companyEventExperimentResult').textContent='可得事件资产日 '+d.eligible_asset_days+'；纯价格模拟收益 '+(d.baseline.total_return*100).toFixed(2)+'% / 事件策略 '+(d.experiment.total_return*100).toFixed(2)+'%。'+d.note+' 保存至：'+d.saved_to;
  }catch(e){$('companyEventExperimentResult').textContent='实验不可用：'+e.message;}finally{this.disabled=false;}};
  $('downloadCompanyNews').onclick=async function(){this.disabled=true;$('collectionNotice').textContent='正在下载真实 TSLA 公司新闻…';try{
    const d=await request('/api/collection',{command:'company_news'});await window.loadCollection();$('collectionNotice').textContent='已下载 '+d.received+' 条 TSLA 新闻并提交入库；重复条目会去重，可在雷达查看事件依据。';
  }catch(e){$('collectionNotice').textContent='下载失败：'+e.message;}finally{this.disabled=false;}};
})();
