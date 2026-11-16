/* Independent collection UI. Never changes strategy/sentiment sample state. */
(() => {
  'use strict';
  const el = id => document.getElementById(id);
  const escape = value => String(value == null ? '' : value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const labels = {official:'官方公告',minutes:'纪要 / 讲话',media:'财经媒体',research:'研究机构',analyst:'分析师',social:'社交',group:'转发',
    policy:'政策',fact:'事实陈述',opinion:'观点',emotion:'情绪',promo:'推广',done:'完成',running:'运行中',partial:'部分来源失败',failed:'失败',interrupted:'中断'};
  let snapshot = null, selected = '', busy = false, timer = null, pending = false, linkedMessage = null, customNotice = false;
  const localTime = value => value ? new Date(value).toLocaleString('zh-CN', {hour12:false}) : '—';
  const notice = value => { customNotice = true; el('collectionNotice').textContent = value; };
  function empty(text) { return '<div class="collection-empty">' + escape(text) + '</div>'; }
  function safeLink(url) {
    try { const parsed = new URL(url); return ['https:','http:'].includes(parsed.protocol) && !parsed.username && !parsed.password ? parsed.href : ''; }
    catch (_) { return ''; }
  }
  async function request(url, options) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(url, Object.assign({}, options, {signal:controller.signal}));
      const data = await response.json();
      if (!response.ok || data.error) throw new Error(data.error || '操作失败');
      return data;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('请求超时；请刷新查看任务状态，避免重复提交文件');
      throw error;
    } finally { clearTimeout(timeout); }
  }
  async function command(payload) {
    return request('/api/collection', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  }
  async function action(payload, message) {
    try { const result = await command(payload); notice(message); await load(); return result; }
    catch (error) { notice('失败：' + error.message); return null; }
  }
  function renderDetail() {
    const m = ((snapshot && snapshot.messages) || []).find(item => item.id === selected) || (linkedMessage && linkedMessage.id === selected ? linkedMessage : null);
    if (!m) { el('collectionDetail').innerHTML = empty('点击一条消息，查看时间证据链与权重。'); return; }
    const link = safeLink(m.url);
    const field = (name,value) => '<div>' + escape(name) + '<b>' + escape(value) + '</b></div>';
    el('collectionDetail').innerHTML = '<div class="collection-body">' + escape(m.text) + '</div><div class="collection-detail-grid">' +
      field('来源 / 分级',m.source_name + ' · ' + (labels[m.category] || m.category)) + field('资产 / 数据性质',m.asset + ' · ' + m.provenance) +
      field('源时间（' + (m.source_time_kind || '发布时间') + '）',localTime(m.published)) + field('首次收到时间',localTime(m.captured)) +
      field('首次可得时间',localTime(m.available) + (m.available_now ? ' · 当前可得' : ' · 尚不可得')) + field('内容类型 / 词典方向',(labels[m.content_type] || m.content_type) + ' · ' + Number(m.direction).toFixed(3)) +
      field('来源权重 × 内容权重',m.source_weight + ' × ' + m.content_weight) + field('设定半衰期 / 当前衰减',m.half_life_hours + ' 小时 / ' + Number(m.decay).toFixed(4)) +
      field('当前单条有效权重',Number(m.effective_weight).toFixed(4) + '（非预测概率）') + field('抓取观察记录',m.observations + ' 次（不等于独立证据数）') +
      field('原数据中的抓取字段（未核验）',m.original_capture || '未提供') + field('消息编号',m.id) + '</div>' +
      (link ? '<p><a target="_blank" rel="noopener noreferrer" href="' + escape(link) + '">打开原始来源 ↗</a></p>' : '<p class="hint">未提供可安全打开的原始链接</p>') +
      '<div class="collection-timeline">发布时间 → 首次收到 → 取两者较晚时间入库。衰减从发布时间计算，不因重复抓取重新获得权重。\n规则词典结果仅供研究；未合并相似事件、未推断独立来源、未接入模拟交易。</div>';
  }
  function renderMessages() {
    if (!snapshot) return;
    const query = el('collectionSearch').value.trim().toLowerCase(), filter = el('collectionFilter').value;
    const list = snapshot.messages.filter(m => (!query || [m.text,m.asset,m.source_name].join(' ').toLowerCase().includes(query)) &&
      (filter === 'all' || (filter === 'positive' && m.direction > 0) || (filter === 'negative' && m.direction < 0) ||
       (filter === 'neutral' && m.direction === 0) || (filter === 'future' && !m.available_now)));
    el('collectionMessages').innerHTML = list.length ? list.map(m => '<tr class="' + (m.id === selected ? 'selected' : '') + '"><td>' + escape(localTime(m.captured)) +
      '</td><td>' + escape(m.source_name) + '<br><b>' + escape(m.asset) + '</b></td><td><button data-message="' + escape(m.id) + '">' + escape(m.text.slice(0,120)) +
      (m.text.length > 120 ? '…' : '') + '</button></td><td class="' + (m.direction < 0 ? 'down' : m.direction > 0 ? 'up' : 'muted') + '">' + Number(m.direction).toFixed(2) +
      (!m.available_now ? '<br>尚不可得' : '') + '</td></tr>').join('') : '<tr><td colspan="4">' + empty(snapshot.total ? '没有匹配的消息' : '尚无入库消息。添加 RSS 或导入 CSV 后，这里显示实际结果。') + '</td></tr>';
    el('collectionMessageCount').textContent = snapshot.total + ' 条入库 · 筛选显示 ' + list.length;
    el('collectionMessages').querySelectorAll('[data-message]').forEach(button => button.onclick = () => { selected = button.dataset.message; renderMessages(); renderDetail(); });
  }
  function render() {
    if (!customNotice) el('collectionNotice').textContent = snapshot.total ?
      '共享库已载入 ' + snapshot.total + ' 条消息；下方“消息证据”可直接查看，雷达读取同一份数据。' :
      '共享库当前为空；请添加公开 RSS 或导入 CSV。';
    const latest = snapshot.tasks[0], count = latest ? latest.counts : {};
    el('collectionState').textContent = snapshot.running ? '正在采集 · ' + (latest.stage || '') : latest ? labels[latest.status] || latest.status : '尚未采集';
    el('collectionRun').disabled = snapshot.running || !snapshot.sources.some(s => s.enabled);
    el('collectionFile').disabled = snapshot.running;
    el('collectionExport').disabled = !snapshot.total && !snapshot.rejected_total;
    const stages = [['采集','received','本次收到条目'],['清洗','cleaned','格式与时间检查通过'],['去重','duplicate','本次重复条目'],['来源分级','classified','新增消息 · 人工先验'],['舆情评分','scored','新增消息 · 规则词典'],['研究入库','stored','本次新增消息']];
    el('collectionStages').innerHTML = stages.map(([name,key,note]) => '<div class="collection-stage"><span>' + name + '</span><b>' + (count[key] || 0) + '</b><small>' + note + '</small></div>').join('');
    el('collectionSourceCount').textContent = snapshot.sources.length + ' 个来源';
    el('collectionSources').innerHTML = snapshot.sources.length ? snapshot.sources.map(s => '<div class="collection-source"><b>' + escape(s.name) + ' · ' + (s.enabled ? '已启用' : '已停用') +
      '</b><p>' + escape(s.url) + '</p><p>' + escape(labels[s.category]) + ' / ' + escape(s.asset) + ' / ' + escape(labels[s.content_type]) +
      '</p><button data-edit="' + escape(s.id) + '">编辑</button><button data-toggle="' + escape(s.id) + '">' + (s.enabled ? '停用' : '启用') +
      '</button><button data-run="' + escape(s.id) + '"' + (snapshot.running || !s.enabled ? ' disabled' : '') + '>单源采集</button></div>').join('') : empty('没有预置或自动启用的来源');
    el('collectionSources').querySelectorAll('[data-edit]').forEach(b => b.onclick = () => {
      const s = snapshot.sources.find(v => v.id === b.dataset.edit);
      [['Id','id'],['Name','name'],['Url','url'],['Category','category'],['Asset','asset'],['Type','content_type']].forEach(([id,key]) => { el(id === 'Id' ? 'collectionSourceId' : 'collection' + id).value = s[key]; });
      el('collectionName').focus();
    });
    el('collectionSources').querySelectorAll('[data-toggle]').forEach(b => b.onclick = () => action({command:'toggle',id:b.dataset.toggle}, '来源状态已保存；不会中断已开始的任务。'));
    el('collectionSources').querySelectorAll('[data-run]').forEach(b => b.onclick = () => action({command:'start',id:b.dataset.run}, '已开始单源采集，可在下方查看日志。'));
    el('collectionExports').innerHTML = snapshot.exports.length ? snapshot.exports.map(e => '<p><a download href="' + escape(e.download_url) + '">' + escape(e.id) + '.zip</a><br>' + e.count + ' 条 · ' + escape(localTime(e.created)) + '</p>').join('') : '还没有导出';
    const openTasks = new Set(Array.from(el('collectionTasks').querySelectorAll('details[open]')).map(d => d.dataset.task));
    el('collectionTasks').innerHTML = snapshot.tasks.length ? snapshot.tasks.map((t,i) => '<details class="collection-task" data-task="' + escape(t.id) + '"' + (openTasks.has(t.id) || (!openTasks.size && i === 0) ? ' open' : '') + '><summary>' +
      escape(localTime(t.started)) + ' · ' + (t.kind === 'rss' ? 'RSS/Atom' : 'CSV 导入') + ' · ' + escape(labels[t.status] || t.status) + ' · 新增 ' + t.counts.stored + ' / 重复 ' + t.counts.duplicate +
      ' / 隔离 ' + t.counts.rejected + (t.errors ? ' / 失败来源 ' + t.errors : '') + '</summary><pre>' +
      escape((t.logs || []).map(l => localTime(l.time) + '  ' + l.text).join('\n') + (t.error ? '\n错误：' + t.error : '')) + '</pre></details>').join('') : empty('还没有采集任务');
    renderMessages(); renderDetail();
  }
  async function load() {
    if (busy) { pending = true; return; }
    busy = true;
    try {
      snapshot = await request('/api/collection'); render();
    } catch (error) { notice('读取失败：' + error.message); }
    finally { busy = false; }
    clearTimeout(timer);
    if (pending) { pending = false; Promise.resolve().then(load); }
    else if (snapshot && snapshot.running && !el('tab-collection').classList.contains('hidden')) timer = setTimeout(load, 1500);
  }
  window.loadCollection = load;
  window.showCollectionMessage = async id => {
    switchTab('collection');
    try {
      linkedMessage = await request('/api/collection-message?id=' + encodeURIComponent(id));
      selected = linkedMessage.id; renderDetail();
      el('collectionDetail').scrollIntoView({block:'center',behavior:'smooth'});
    } catch (error) { notice('消息读取失败：' + error.message); }
  };
  el('collectionRadar').onclick = () => switchTab('sentiment');
  el('collectionRefresh').onclick = () => { customNotice = false; load(); };
  el('collectionRun').onclick = () => action({command:'start'}, '已开始采集；状态由真实任务记录更新。');
  el('collectionClearSource').onclick = () => { el('collectionSourceForm').reset(); el('collectionSourceId').value = ''; };
  el('collectionSourceForm').onsubmit = async event => {
    event.preventDefault(); el('collectionSaveSource').disabled = true;
    const result = await action({command:'source',id:el('collectionSourceId').value,name:el('collectionName').value,url:el('collectionUrl').value,
      category:el('collectionCategory').value,asset:el('collectionAsset').value,content_type:el('collectionType').value}, '来源已保存。点击“采集”才会访问网络。');
    if (result) { el('collectionSourceForm').reset(); el('collectionSourceId').value = ''; }
    el('collectionSaveSource').disabled = false;
  };
  el('collectionFile').onchange = async event => {
    const file = event.target.files[0]; if (!file) return;
    if (file.size > 3 * 1024 * 1024) { notice('文件超过 3 MB，请拆分后导入。'); event.target.value = ''; return; }
    try {
      const bytes = await file.arrayBuffer();
      let text;
      try { text = new TextDecoder('utf-8', {fatal:true}).decode(bytes); }
      catch (_) { text = new TextDecoder('gb18030', {fatal:true}).decode(bytes); }
      await action({command:'import',filename:file.name,text:text}, '导入完成；历史抓取字段未用于追溯可得时间，请查看任务记录。');
    } catch (error) { notice('文件读取失败：' + error.message); }
    event.target.value = '';
  };
  el('collectionExport').onclick = async () => {
    el('collectionExport').disabled = true;
    await action({command:'export'}, '完整数据包已保存，点击“研究数据包”中的链接下载。');
    el('collectionExport').disabled = !(snapshot && (snapshot.total || snapshot.rejected_total));
  };
  el('collectionSearch').oninput = renderMessages;
  el('collectionFilter').onchange = renderMessages;
  if (location.hash === '#collection') load();
})();
