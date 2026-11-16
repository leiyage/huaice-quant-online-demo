/* {{APP_NAME}} v3.0 —— 前端逻辑（名字在 branding.py 里改）
   只与本机 127.0.0.1 的后端通信；不发任何真实订单。 */
'use strict';
const FRONT_VERSION = '3.0';
const $ = (id) => document.getElementById(id);
let state = null, report = null, hasHeavy = false, activeTab = 'replay', filesReady = false;
let currentMarket = 'crypto', allFiles = [], defaultCrypto = [], defaultUS = [];
let reportRequestId = 0;

/* ---------------- 通用工具 ---------------- */
const num = (v, d = 2) => (v === null || v === undefined || !isFinite(v)) ? '—'
  : Number(v).toLocaleString('zh-CN', { minimumFractionDigits: d, maximumFractionDigits: d });
const money = (v, d = 2) => (v === null || v === undefined || !isFinite(v)) ? '—' : num(v, d);
const pct = (v, d = 2, signed = true) => (v === null || v === undefined || !isFinite(v)) ? '—'
  : (signed && v >= 0 ? '+' : '') + (v * 100).toFixed(d) + '%';
const colorOf = (v) => (v > 0 ? 'up' : v < 0 ? 'down' : '');
const setText = (id, txt, cls) => { const el = $(id); if (!el) return; el.textContent = txt; if (cls !== undefined) el.className = cls; };
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

/* ---------------- 与后端通信 ---------------- */
async function post(command, extra) {
  const body = Object.assign({ command: command }, extra || {});
  const res = await fetch('/api/control', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
  });
  const data = await res.json();
  if (data.error) { alert('操作失败：' + data.error); return; }
  if (command === 'reset') clearPreviousRun();
  render(data);
  if (command === 'reset' && activeTab === 'perf') loadReport(false);
}

async function poll() {
  try {
    const res = await fetch('/api/state');
    render(await res.json());
  } catch (e) { /* 单次失败忽略 */ }
}

async function loadFiles() {
  try {
    const res = await fetch('/api/files');
    const data = await res.json();
    const box = $('assetBox');
    if (!data.files || !data.files.length) {
      box.innerHTML = '<span class="muted">data 文件夹里没有 CSV（将使用合成演示数据）</span>';
      return;
    }
    allFiles = data.files;
    defaultCrypto = data.default || [];
    defaultUS = data.default_us || [];
    selectMarket(currentMarket);
    filesReady = true;
  } catch (e) { /* 忽略 */ }
}

function selectedAssets() {
  return Array.from(document.querySelectorAll('.assetChk')).filter((c) => c.checked).map((c) => c.value);
}

function selectMarket(market, selected) {
  currentMarket = market;
  const defaults = selected || (market === 'us' ? defaultUS : defaultCrypto);
  $('btnMarketCrypto').className = market === 'crypto' ? 'primary' : '';
  $('btnMarketUS').className = market === 'us' ? 'primary' : '';
  $('currencyLabel').textContent = market === 'us' ? 'USD' : 'USDT';
  const files = allFiles.filter((f) => f.market === market || f.market === 'other');
  $('assetBox').innerHTML = files.map((f) =>
    '<label><input type="checkbox" class="assetChk" value="' + esc(f.name) + '"' +
    (defaults.includes(f.name) ? ' checked' : '') + '>' + esc(f.name) +
    ' <span class="muted">(' + f.rows + (f.market === 'us' ? (f.provenance && f.provenance.verified ? ' · 真实/复权' : ' · 来源未验证') : '') + ')</span></label>').join('') ||
    '<span class="muted">此市场暂无本地行情文件</span>';
}

function collectParams() {
  const g = (id) => $(id) ? $(id).value : '';
  const b = (id) => $(id) ? $(id).checked : false;
  return {
    market: currentMarket,
    assets: selectedAssets(),
    initial_cash: Number(g('initial_cash')),
    ma_type: g('ma_type'), fast: Number(g('fast')), slow: Number(g('slow')),
    regime_filter: b('regime_filter'), use_sentiment: b('use_sentiment'),
    sentiment_source: g('sentiment_source'), sentiment_include_general: b('sentiment_include_general'),
    vol_lookback: Number(g('vol_lookback')), vol_target: Number(g('vol_target')),
    use_vol_target: b('use_vol_target'), fixed_weight: Number(g('fixed_weight')),
    max_weight: Number(g('max_weight')),
    use_stop: b('use_stop'), stop_mode: g('stop_mode'), stop_atr: Number(g('stop_atr')),
    stop_reentry_days: Number(g('stop_reentry_days')), time_stop_days: Number(g('time_stop_days')),
    rebalance_band: Number(g('rebalance_band')), fee_bps: Number(g('fee_bps')),
    slippage_bps: Number(g('slippage_bps')), impact_coef_bps: Number(g('impact_coef_bps')),
    risk_limit_pct: Number(g('risk_limit_pct')), risk_pause_days: Number(g('risk_pause_days')),
    daily_loss_limit_pct: Number(g('daily_loss_limit_pct')), cooldown_days: Number(g('cooldown_days')),
    alloc_method: g('alloc_method'), weights: g('weights'),
    interval: Number(g('interval'))
  };
}

function fillForm(pv, assets) {
  if (!pv) return;
  const set = (id, v) => { const el = $(id); if (el && v !== undefined && v !== null) el.value = v; };
  const chk = (id, v) => { const el = $(id); if (el) el.checked = !!v; };
  set('initial_cash', pv.initial_cash); set('ma_type', pv.ma_type);
  set('fast', pv.fast); set('slow', pv.slow);
  chk('regime_filter', pv.regime_filter); chk('use_sentiment', pv.use_sentiment);
  set('sentiment_source', pv.sentiment_source); chk('sentiment_include_general', pv.sentiment_include_general);
  set('vol_lookback', pv.vol_lookback); set('vol_target', pv.vol_target); set('max_weight', pv.max_weight);
  chk('use_vol_target', pv.use_vol_target); set('fixed_weight', pv.fixed_weight);
  chk('use_stop', pv.use_stop); set('stop_mode', pv.stop_mode); set('stop_atr', pv.stop_atr);
  set('stop_reentry_days', pv.stop_reentry_days); set('time_stop_days', pv.time_stop_days);
  set('rebalance_band', pv.rebalance_band); set('fee_bps', pv.fee_bps);
  set('slippage_bps', pv.slippage_bps); set('impact_coef_bps', pv.impact_coef_bps);
  set('risk_limit_pct', pv.risk_limit_pct); set('risk_pause_days', pv.risk_pause_days);
  set('daily_loss_limit_pct', pv.daily_loss_limit_pct); set('cooldown_days', pv.cooldown_days);
  set('alloc_method', pv.alloc_method); set('weights', pv.weights);
  if (assets && assets.length) {
    document.querySelectorAll('.assetChk').forEach((c) => { c.checked = assets.indexOf(c.value) >= 0; });
  }
}

/* ---------------- 主渲染 ---------------- */
function clearPreviousRun() {
  reportRequestId++;
  report = null;
  hasHeavy = false;
  ['perfMetrics', 'perfMetrics2', 'monthlyTable', 'legsBody', 'corrTable',
   'annualHead', 'annualBody', 'gridTable', 'mcStats', 'researchMetrics', 'researchComparisons', 'researchFiles'].forEach(function (id) {
    const el = $(id); if (el) el.innerHTML = '';
  });
  setText('perfNote', '新回测已重置；打开本页会重新计算全周期报告');
  setText('rtSummary', '');
  setText('wfSummary', '');
  setText('robustStatus', '');
  setText('gridNote', '');
  setText('researchStatus', '参数已重设，请重新生成实验');
  setText('researchAudit', ''); setText('researchPeriod', '');
  $('rtBody').innerHTML = '<tr><td colspan="10">新回测尚无交易明细报告</td></tr>';
  $('wfBody').innerHTML = '<tr><td colspan="9">请重新生成稳健性检验</td></tr>';
  ['sharpeChart', 'mcChart'].forEach(function (id) {
    const canvas = $(id); if (canvas) canvas.getContext('2d').clearRect(0, 0, canvas.width, canvas.height);
  });
  const evidence = $('tradeEvidence');
  if (evidence) { evidence.style.display = 'none'; evidence.textContent = ''; }
}

function render(s) {
  if (!s || s.error) return;
  if (state && Number.isInteger(s.generation) && Number.isInteger(state.generation) &&
      s.generation < state.generation) return;
  const first = !state;
  if (!first && s.generation !== undefined && state.generation !== undefined &&
      s.generation !== state.generation) clearPreviousRun();
  state = s;
  if (first) setText('verLabel', 'v' + s.version);
  if (first && s.market_type) {
    selectMarket(s.market_type, s.assets);
  }
  if (first && filesReady) fillForm(s.params_view, s.assets);

  /* 版本检测：后台过旧时明确提示 */
  const stale = (s.ohlc === undefined) || (s.version === undefined || s.version !== FRONT_VERSION);
  const box = $('staleBox');
  if (box) {
    box.innerHTML = stale
      ? '<div class="err" style="margin:12px 18px 0">检测到后台程序版本与界面不一致（前台 ' + FRONT_VERSION +
        '）。请关闭黑色窗口，重新双击「启动自动交易.bat」。' +
        (s.ohlc === undefined ? ' 原因：后台没有返回 K 线数据。' : '') + '</div>'
      : '';
    if (stale) console.error('[版本] 后台返回 version=' + s.version + '，ohlc=' + (s.ohlc === undefined ? '缺失' : '正常'));
  }

  /* 顶部状态 */
  const st = $('status');
  if (s.account.paused) { st.textContent = '风险冷静期中'; st.className = 'pill risk'; }
  else if (s.running) { st.textContent = '● 回放中'; st.className = 'pill running'; }
  else if (s.finished) { st.textContent = '■ 回放完毕'; st.className = 'pill done'; }
  else if (s.metrics.days > 1) { st.textContent = 'Ⅱ 已暂停'; st.className = 'pill paused'; }
  else { st.textContent = '就绪，等待开始'; st.className = 'pill'; }

  $('warnBox').innerHTML = (s.warning ? '<div class="warn">' + esc(s.warning) + '</div>' : '') +
    (s.sentiment_sample_mode ? '<div class="warn">合成舆情风控实验：并非真实历史新闻，回测结果不能证明预测力。</div>' : '');
  if (s.sentiment_collection_mode) $('warnBox').innerHTML += '<div class="warn">共享库减仓实验：冻结 ' + Number(s.sentiment_collection_count || 0) + ' 条消息；仅在交易日前已经可得且资产匹配时生效。历史行情早于首次采集时间时不会触发减仓。</div>';
  if (s.sentiment_event_mode) $('warnBox').innerHTML += '<div class="warn">TSLA公司事件规则实验：无有效利好不新增买入；冲突或失效消息不用于入场。不是AI预测，不发送真实订单。</div>';
  setText('srcLabel', s.data_source + (s.params.use_sentiment ? (s.params.sentiment_source === 'event' ? ' · TSLA公司事件模拟实验' : s.params.sentiment_source === 'collection' ? ' · 共享库减仓实验' : ' · 合成舆情风控实验') : ''));

  /* 指标卡片（已回放区间） */
  const m = s.metrics, fm = s.full_metrics, a = s.account;
  const ccy = (s.currency || (s.market_type === 'us' ? 'USD' : 'USDT'));
  setText('cEquity', money(a.equity) + ' ' + ccy);
  setText('cEquityS', '初始 ' + money(a.initial_cash, 0) + ' · 第 ' + s.market.day + '/' + s.market.total_days + ' 根');
  setText('cReturn', pct(m.total_return), 'v ' + colorOf(m.total_return));
  setText('cReturnS', '基准 ' + pct(m.benchmark_return, 1) + ' · 超额 ' + pct(m.excess_return, 1));
  setText('cDD', (m.max_drawdown * 100).toFixed(2) + '%', 'v ' + (m.max_drawdown < -0.001 ? 'down' : ''));
  setText('cDDS', '已回放区间内');
  setText('cSharpe', isFinite(m.sharpe) ? m.sharpe.toFixed(2) : '—');
  setText('cSharpeS', 'Sortino ' + (isFinite(m.sortino) ? m.sortino.toFixed(2) : '—') +
    ' · Calmar ' + (isFinite(m.calmar) ? m.calmar.toFixed(2) : '—'));
  setText('cExpo', (a.exposure_pct).toFixed(1) + '%');
  setText('cExpoS', '目标 ' + (s.market.target_weight * 100).toFixed(0) + '% · 波动 ' +
    (m.ann_vol * 100).toFixed(1) + '%');
  const riskText = a.paused ? '冷静中' : (s.risk_events.length ? '正常（历史 ' + s.risk_events.length + ' 次熔断）' : '正常');
  setText('cRisk', riskText, 'v ' + (a.paused ? 'warncol' : (s.risk_events.length ? '' : 'up')));
  setText('cRiskS', '回撤 ' + (a.drawdown_pct).toFixed(1) + '% / 熔断线 ' + s.params.risk_limit_pct + '%');

  /* 账户面板 */
  setText('aCash', money(a.cash) + ' ' + ccy);
  setText('aPos', money(a.position_value) + ' ' + ccy);
  setText('aTarget', (s.market.target_weight * 100).toFixed(1) + '% / ' + (s.market.weight * 100).toFixed(1) + '%');
  setText('aSignal', s.market.signal);
  setText('aMA', money(s.market.fast_ma) + ' / ' + money(s.market.slow_ma));
  setText('aATR', money(s.market.atr) + ' / 止损 ' + (s.market.stop_level === null ? '未持仓' : money(s.market.stop_level)));
  setText('aVol', (s.market.vol === null ? '—' : (s.market.vol * 100).toFixed(1) + '%'));
  setText('aFee', money(a.fees_paid) + ' ' + ccy);
  setText('aTrades', m.trades + ' 笔（买 ' + m.buys + ' / 卖 ' + m.sells + ' / 止损 ' + m.stops + '）');
  setText('aEvent', s.market.event || '—');
  setText('dayLabel', 'K线日期 ' + s.market.date + ' · 第 ' + s.market.day + ' / ' + s.market.total_days +
    ' 根（' + s.market.progress + '%）');
  setText('priceLabel', '收盘价 ' + money(s.market.price) + ' ' + ccy + ' · 策略：' + s.strategy);
  setText('mainSymbol', s.scenario.split('+')[0].trim());

  const pb = $('posBody');
  const pos = s.positions || [];
  pb.innerHTML = pos.length ? pos.map(function (p) {
    return '<tr><td>' + esc(p.symbol) + '</td><td>' + money(p.price) + '</td><td>' + p.qty.toFixed(5) + '</td>' +
      '<td>' + money(p.value) + '</td><td>' + (p.weight * 100).toFixed(1) + '%</td>' +
      '<td class="muted">' + (p.target * 100).toFixed(1) + '%</td></tr>';
  }).join('') : '<tr><td colspan="6" class="muted">等待回放</td></tr>';

  /* 成交记录 */
  if (activeTab === 'trades' || first || s.market.day === 1) {
    renderRoundTrips(s.round_trips || []);
  }
  const tb = $('tradeBody');
  if (!s.trades.length) { tb.innerHTML = '<tr><td colspan="8" class="muted">暂无成交</td></tr>'; }
  else {
    tb.innerHTML = s.trades.map((t) => {
      const cls = t.action === 'BUY' ? 'buy' : (t.action === 'STOP' ? 'stop' : 'sell');
      const label = t.action === 'BUY' ? '买入' : (t.action === 'STOP' ? '止损' : '卖出');
      return '<tr data-evidence-index="' + Number(t.evidence_index) + '" style="cursor:pointer"><td>' + esc(t.date) + '</td><td>' + esc(t.symbol || '') + '</td><td><span class="tag ' + cls + '">' + label + '</span></td>' +
        '<td>' + money(t.price) + '</td><td>' + Number(t.quantity).toFixed(6) + '</td>' +
        '<td>' + money(t.amount) + '</td><td>' + Number(t.fee).toFixed(2) + '</td>' +
        '<td style="text-align:left;color:#94a3b8">' + esc(t.note || '') + '</td></tr>';
    }).join('');
  }

  /* 风控事件 */
  tb.querySelectorAll('[data-evidence-index]').forEach(function (row) {
    row.onclick = function () { showTradeEvidence(Number(row.dataset.evidenceIndex)); };
  });
  const rb = $('riskEventBody');
  rb.innerHTML = s.risk_events && s.risk_events.length
    ? s.risk_events.slice().reverse().map((e) =>
      '<tr><td>' + esc(e.date) + '</td><td>' + esc(e.symbol || '组合') + '</td>' +
      '<td>' + Number(e.drawdown).toFixed(2) + '%</td><td>' + e.pause_days + '</td></tr>').join('')
    : '<tr><td colspan="4" class="muted">暂无风控事件</td></tr>';

  /* 日志 */
  $('logBox').innerHTML = s.logs.length
    ? s.logs.map((l) => '<div><span class="t">' + esc(l.time) + '</span>' + esc(l.text) + '</div>').join('')
    : '—';

  /* 按钮可用性 */
  $('btnStart').disabled = s.running || s.finished;
  $('btnPause').disabled = !s.running;
  $('btnStep').disabled = s.finished;
  $('btnFast').disabled = s.finished;
  $('btnReset').disabled = s.running;

  /* 图表 */
  try {
    if (activeTab === 'replay') { drawCandles(s.ohlc, s.trades); drawEquity(s.series); }
    if (activeTab === 'sentiment') loadSentiment(false);
  }
  catch (err) { console.error('[图表] 绘制异常:', err); }
}
/* ---------------- 画布基础设施 ---------------- */
async function showTradeEvidence(index) {
  const box = $('tradeEvidence');
  box.style.display = '';
  box.textContent = '正在读取交易证据链…';
  try {
    const res = await fetch('/api/trade-evidence?index=' + encodeURIComponent(index));
    const e = await res.json();
    if (!res.ok || e.error) throw new Error(e.error || 'Request failed');
    const t = e.trade, p = e.prior, b = e.bar, se = e.sentiment;
    const fmt = (v) => v == null ? '—' : num(v, 3);
    box.innerHTML = '<b>淮策交易证据链 · ' + esc(t.date) + ' ' + esc(t.symbol) + ' ' + esc(t.action) + '</b>' +
      '<div>模拟成交：' + fmt(t.quantity) + ' × ' + fmt(t.price) + ' ' + esc(e.currency) +
      '；手续费 ' + fmt(e.cost.fee) + '；成交原因：' + esc(t.note || '—') + '</div>' +
      '<div>前一交易日收盘信息（' + esc(p ? p.date : '—') + '）：' + fmt(p && p.close) +
      '；快/慢均线 ' + fmt(p && p.fast_ma) + ' / ' + fmt(p && p.slow_ma) +
      '；波动率 ' + fmt(p && p.vol) + '；价格策略仓位 ' + pct(b.base_target_weight, 1, false) +
      '；舆情 z=' + fmt(b.sentiment_z) + '，覆盖率=' + fmt(b.sentiment_coverage) +
      '，仓位乘数=' + fmt(b.sentiment_multiplier) + '；最终目标仓位 ' + pct(b.target_weight, 1, false) + '</div>' +
      '<div>当日开高低收（事后核对，开盘时尚未知晓完整 K 线）：' +
      [b.open, b.high, b.low, b.close].map(fmt).join(' / ') + '</div>' +
      '<div>成本假设：手续费 ' + fmt(e.cost.fee_bps) + 'bps，固定滑点 ' +
      fmt(e.cost.slippage_bps) + 'bps，冲击系数 ' + fmt(e.cost.impact_coef_bps) + 'bps</div>' +
      '<div><b>舆情背景（仅实际可得时间早于 ' + esc(se.cutoff) + '):</b> ' + esc(se.note) + '</div>' +
      (se.error ? '<div>舆情读取错误：' + esc(se.error) + '</div>' : '') +
      (se.events || []).map(function (v) { return '<div>· ' + esc(v.available_at) + ' [' + esc(v.source) +
        '] ' + esc(v.text) + '</div>'; }).join('') +
      (e.company_events || []).map(function(v){return '<div><b>公司事件：</b>'+esc(v.event_type)+' · '+esc(v.signal)+'；依据：'+esc((v.evidence || []).join(' / '))+'</div>';}).join('') +
      '<div>仅展示最近8条消息背景；' +
      (e.sentiment_active ? '已开启舆情仓位风控，实际决策分数见上方；' : '舆情未参与该笔成交；') +
      (se.sample ? '合成数据不能证明真实预测力。' : '规则识别与来源真实性需核验；不等于已验证的预测力。') + '</div>';
  } catch (err) { box.textContent = '证据链不可用：' + err.message; console.error('[evidence]', err); }
}

function prep(canvas, height) {
  if (!canvas) return null;
  const w = canvas.clientWidth;
  if (!w || w < 60) return null;               /* 隐藏的标签页宽度为 0，跳过绘制 */
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.round(w * ratio);
  canvas.height = Math.round(height * ratio);
  const g = canvas.getContext('2d');
  g.setTransform(ratio, 0, 0, ratio, 0, 0);
  g.clearRect(0, 0, w, height);
  g.font = '11px Consolas';
  return { g: g, w: w, h: height };
}

function grid(g, x0, x1, y0, y1, rows, fmt) {
  g.strokeStyle = '#232e44'; g.fillStyle = '#64748b'; g.lineWidth = 1; g.textAlign = 'right';
  for (let k = 0; k <= rows; k++) {
    const y = y0 + (y1 - y0) * k / rows;
    g.beginPath(); g.moveTo(x0, y); g.lineTo(x1, y); g.stroke();
    g.fillText(fmt(k), x0 - 8, y + 4);
  }
}

/* ---------------- K 线蜡烛图 ---------------- */
function drawCandles(rawBars, trades) {
  const cv = $('kchart');
  const H = 360;
  const c = prep(cv, H);
  if (!c) return;
  const g = c.g, w = c.w;

  /* 步骤 1：数据校验与规范化（防止 NaN 坐标导致静默不绘制） */
  const rawCount = Array.isArray(rawBars) ? rawBars.length : 0;
  const bars = []; let dropped = 0;
  if (Array.isArray(rawBars)) {
    rawBars.forEach(function (b, i) {
      if (!b) { dropped++; return; }
      const o = Number(b.o), hh = Number(b.h), ll = Number(b.l), cc = Number(b.c);
      if (![o, hh, ll, cc].every(Number.isFinite)) {
        dropped++;
        if (dropped <= 3) console.warn('[K线] 第 ' + i + ' 根数据无效，已跳过:', b);
        return;
      }
      const f = Number(b.f), s = Number(b.s), st = Number(b.stop);
      bars.push({
        d: b.d, o: o, h: hh, l: ll, c: cc, tgt: Number(b.tgt), pos: Number(b.pos), ev: b.ev,
        f: Number.isFinite(f) ? f : null, s: Number.isFinite(s) ? s : null,
        stop: Number.isFinite(st) ? st : null
      });
    });
  }
  console.log('[K线] 收到 ' + rawCount + ' 根 | 有效 ' + bars.length + ' 根 | 丢弃 ' + dropped + ' 根');
  if (bars.length) {
    const f0 = bars[0], f1 = bars[bars.length - 1];
    console.log('[K线] 首根 ' + f0.d + '  O=' + f0.o + ' H=' + f0.h + ' L=' + f0.l + ' C=' + f0.c);
    console.log('[K线] 末根 ' + f1.d + '  O=' + f1.o + ' H=' + f1.h + ' L=' + f1.l + ' C=' + f1.c);
  }
  const diag = $('klineDiag');
  if (diag) {
    diag.textContent = bars.length ? ('K线 ' + bars.length + ' 根 · ' + (bars[bars.length - 1].d || '')) : '无 K 线数据';
    diag.style.color = bars.length ? '#64748b' : '#f59e0b';
  }

  /* 步骤 2：数据不足时明确提示，而不是画一片空白 */
  if (bars.length < 2) {
    g.fillStyle = '#f59e0b'; g.font = '13px "Microsoft YaHei"';
    g.fillText(rawCount === 0
      ? '未收到 K 线数据 —— 请关闭黑色窗口，重新双击「启动自动交易.bat」'
      : 'K 线数据不足，无法绘图（有效 ' + bars.length + ' 根）', 16, H / 2);
    if (rawCount === 0) console.error('[K线] 后台没有返回 ohlc 字段（后台版本过旧）');
    return;
  }

  /* 步骤 3：坐标范围（严格保证有限值） */
  const padL = 78, padR = 14, padT = 12, padB = 52;
  const plotH = H - padT - padB;
  const bandTop = padT + plotH + 10, bandH = 13;
  let lo = Infinity, hi = -Infinity;
  bars.forEach(function (b) {
    lo = Math.min(lo, b.l); hi = Math.max(hi, b.h);
    [b.f, b.s].forEach(function (v) { if (v !== null && Number.isFinite(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); } });
  });
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) {
    console.error('[K线] 价格区间计算失败 lo=' + lo + ' hi=' + hi);
    g.fillStyle = '#f59e0b'; g.fillText('K 线价格数据异常，无法绘图', 16, H / 2); return;
  }
  if (hi - lo < 1e-9) { const mid = (hi + lo) / 2 || 1; lo = mid * 0.99; hi = mid * 1.01; }
  const span = hi - lo;
  lo -= span * 0.06; hi += span * 0.06;
  console.log('[K线] 价格区间 ' + lo.toFixed(2) + ' ~ ' + hi.toFixed(2) + ' | 画布宽 ' + w + 'px');

  const X = function (i) { return padL + (w - padL - padR) * (i + 0.5) / bars.length; };
  const Y = function (v) { return padT + plotH - (v - lo) / (hi - lo) * plotH; };
  const step = (w - padL - padR) / bars.length;
  const bodyW = Math.max(1, Math.min(step * 0.62, 14));

  grid(g, padL, w - padR, padT, padT + plotH, 5,
    function (k) { return money(hi - (hi - lo) * k / 5); });

  /* 步骤 4：逐根画蜡烛 */
  let drawn = 0;
  bars.forEach(function (b, i) {
    const x = X(i);
    const yh = Y(b.h), yl = Y(b.l), yo = Y(b.o), yc = Y(b.c);
    if (![x, yh, yl, yo, yc].every(Number.isFinite)) return;
    const up = b.c >= b.o;
    const color = up ? '#22c55e' : '#ef4444';
    g.strokeStyle = color; g.fillStyle = color; g.lineWidth = 1;
    g.beginPath(); g.moveTo(x, yh); g.lineTo(x, yl); g.stroke();
    const top = Math.min(yo, yc), hgt = Math.max(Math.abs(yc - yo), 1);
    g.fillRect(x - bodyW / 2, top, bodyW, hgt);
    drawn++;
  });
  console.log('[K线] 实际绘制 ' + drawn + ' 根蜡烛');

  /* 步骤 5：均线与 ATR 止损线 */
  function line(key, color, width, dash) {
    g.beginPath(); g.strokeStyle = color; g.lineWidth = width;
    if (dash) { g.save(); g.setLineDash(dash); }
    let started = false;
    bars.forEach(function (b, i) {
      const v = b[key];
      if (v === null || !Number.isFinite(v)) { started = false; return; }
      const x = X(i), y = Y(v);
      if (!Number.isFinite(y) || y < padT - 4 || y > padT + plotH + 4) { started = false; return; }
      if (!started) { g.moveTo(x, y); started = true; } else { g.lineTo(x, y); }
    });
    g.stroke();
    if (dash) g.restore();
  }
  line('s', '#a78bfa', 1.4);
  line('f', '#f59e0b', 1.4);
  line('stop', '#f97316', 1.4, [6, 4]);

  /* 步骤 6：买卖标记 */
  const buys = {}, sells = {};
  (trades || []).forEach(function (t) {
    if (!t || !t.date) return;
    if (t.action === 'BUY') buys[t.date] = 1;
    else if (t.action === 'SELL' || t.action === 'STOP') sells[t.date] = t.action;
  });
  bars.forEach(function (b, i) {
    const x = X(i);
    if (buys[b.d]) {
      const y = Y(b.l) + 14;
      g.fillStyle = '#3b82f6';
      g.beginPath(); g.moveTo(x, y); g.lineTo(x - 5, y + 9); g.lineTo(x + 5, y + 9); g.closePath(); g.fill();
    }
    if (sells[b.d]) {
      const y = Y(b.h) - 14;
      g.fillStyle = sells[b.d] === 'STOP' ? '#f97316' : '#e879f9';
      g.beginPath(); g.moveTo(x, y); g.lineTo(x - 5, y - 9); g.lineTo(x + 5, y - 9); g.closePath(); g.fill();
    }
  });

  /* 步骤 7：底部仓位带（0~100%）与目标仓位刻度 */
  g.fillStyle = '#0e1522'; g.fillRect(padL, bandTop, w - padL - padR, bandH);
  g.strokeStyle = '#27324a'; g.strokeRect(padL + .5, bandTop + .5, w - padL - padR - 1, bandH - 1);
  bars.forEach(function (b, i) {
    const pos = Number.isFinite(b.pos) ? Math.max(0, Math.min(1, b.pos)) : 0;
    const tgt = Number.isFinite(b.tgt) ? Math.max(0, Math.min(1, b.tgt)) : 0;
    if (pos > 0) {
      g.fillStyle = 'rgba(96,165,250,.85)';
      g.fillRect(X(i) - bodyW / 2, bandTop + 1, bodyW, bandH - 2);
    }
    if (tgt > 0) {
      g.fillStyle = '#f59e0b';
      g.fillRect(X(i) - bodyW / 2, bandTop + bandH - 3, bodyW, 2);
    }
  });
  g.fillStyle = '#64748b'; g.textAlign = 'right'; g.fillText('仓位', padL - 8, bandTop + 10);

  /* 步骤 8：日期与最新一根的 OHLC */
  const last = bars[bars.length - 1];
  g.fillStyle = '#64748b'; g.textAlign = 'left';
  g.fillText(bars[0].d, padL, H - 6);
  g.textAlign = 'right';
  g.fillText(last.d, w - padR, H - 6);
  g.textAlign = 'left';
  g.fillStyle = '#e8edf7';
  g.fillText('最新：开 ' + money(last.o) + '  高 ' + money(last.h) + '  低 ' + money(last.l) + '  收 ' + money(last.c), padL + 4, padT + 14);
}

/* ---------------- 净值 / 基准 / 回撤 ---------------- */
function drawEquity(series) {
  const cv = $('echart');
  const c = prep(cv, 240);
  if (!c) return;
  const g = c.g, w = c.w, H = c.h;
  const eq = series.equity || [], bm = series.benchmark || [], dd = series.drawdown || [];
  if (eq.length < 2) {
    g.fillStyle = '#64748b'; g.font = '13px "Microsoft YaHei"';
    g.fillText('开始回放后显示组合净值曲线……', 16, H / 2); return;
  }
  const padL = 78, padR = 14, padT = 10, padB = 28;
  const eqH = 122, gap = 16, ddH = H - padT - padB - eqH - gap;
  const eqTop = padT, ddTop = padT + eqH + gap;

  /* 纵轴用对数刻度：否则基准涨 26 倍会把策略曲线压成一条直线 */
  const all = eq.concat(bm).filter(function (v) { return v > 0; });
  let lo = Math.max(Math.min.apply(null, all), 1e-6), hi = Math.max.apply(null, all);
  const lLo = Math.log(lo * 0.90), lHi = Math.log(hi * 1.12);
  const n = eq.length;
  const X = function (i) { return padL + (w - padL - padR) * i / Math.max(1, n - 1); };
  const Y = function (v) { return eqTop + eqH - (Math.log(Math.max(v, 1e-6)) - lLo) / (lHi - lLo) * eqH; };

  grid(g, padL, w - padR, eqTop, eqTop + eqH, 4,
    function (k) { return money(Math.exp(lHi - (lHi - lLo) * k / 4), 0); });
  g.fillStyle = '#64748b'; g.textAlign = 'left';
  g.fillText('对数刻度', padL + 4, eqTop + 11);

  const grad = g.createLinearGradient(0, eqTop, 0, eqTop + eqH);
  grad.addColorStop(0, 'rgba(59,130,246,.32)');
  grad.addColorStop(1, 'rgba(59,130,246,0)');
  g.beginPath(); g.moveTo(X(0), Y(eq[0]));
  eq.forEach(function (v, i) { g.lineTo(X(i), Y(v)); });
  g.lineTo(X(n - 1), eqTop + eqH); g.lineTo(X(0), eqTop + eqH); g.closePath();
  g.fillStyle = grad; g.fill();

  g.beginPath(); g.moveTo(X(0), Y(bm[0] || eq[0]));
  bm.forEach(function (v, i) { g.lineTo(X(i), Y(v)); });
  g.strokeStyle = '#94a3b8'; g.lineWidth = 1.4; g.stroke();

  g.beginPath(); g.moveTo(X(0), Y(eq[0]));
  eq.forEach(function (v, i) { g.lineTo(X(i), Y(v)); });
  g.strokeStyle = '#60a5fa'; g.lineWidth = 1.9; g.stroke();

  if (state && state.account.initial_cash > 0) {
    g.save(); g.setLineDash([5, 5]); g.strokeStyle = '#475569';
    g.beginPath(); g.moveTo(padL, Y(state.account.initial_cash)); g.lineTo(w - padR, Y(state.account.initial_cash)); g.stroke();
    g.restore();
  }

  const dLo = Math.min(-5, Math.min.apply(null, dd)), dHi = 0;
  const DY = function (v) { return ddTop + (dHi - v) / (dHi - dLo) * ddH; };
  g.beginPath(); g.moveTo(X(0), ddTop);
  dd.forEach(function (v, i) { g.lineTo(X(i), DY(v)); });
  g.lineTo(X(n - 1), ddTop); g.closePath();
  g.fillStyle = 'rgba(239,68,68,.28)'; g.fill();
  g.beginPath(); g.moveTo(X(0), DY(dd[0]));
  dd.forEach(function (v, i) { g.lineTo(X(i), DY(v)); });
  g.strokeStyle = '#f87171'; g.lineWidth = 1.2; g.stroke();
  g.fillStyle = '#64748b'; g.textAlign = 'left';
  g.fillText('回撤 ' + dd[dd.length - 1].toFixed(2) + '%', padL + 4, ddTop + 12);
  g.fillText(series.dates[0], padL, H - 8);
  g.textAlign = 'right';
  g.fillText(series.dates[series.dates.length - 1], w - padR, H - 8);
}

/* ---------------- 滚动夏普 ---------------- */
function drawSharpe(a, b) {
  const c = prep($('sharpeChart'), 200);
  if (!c) return;
  const g = c.g, w = c.w, H = c.h;
  const dates = a.dates || [], va = a.values || [];
  if (va.length < 3) { g.fillStyle = '#64748b'; g.fillText('数据不足', 16, H / 2); return; }
  const padL = 56, padR = 12, padT = 10, padB = 24;
  const vb = (b && b.values) ? b.values : [];
  const all = va.concat(vb, [0]);
  let lo = Math.min.apply(null, all), hi = Math.max.apply(null, all);
  const padv = Math.max((hi - lo) * 0.1, 0.2); lo -= padv; hi += padv;
  const X = function (i) { return padL + (w - padL - padR) * i / (va.length - 1); };
  const Y = function (v) { return padT + (H - padT - padB) - (v - lo) / (hi - lo) * (H - padT - padB); };
  grid(g, padL, w - padR, padT, H - padB, 4, function (k) { return (hi - (hi - lo) * k / 4).toFixed(1); });
  g.save(); g.setLineDash([4, 4]); g.strokeStyle = '#475569';
  g.beginPath(); g.moveTo(padL, Y(0)); g.lineTo(w - padR, Y(0)); g.stroke(); g.restore();
  if (vb.length === va.length) {
    g.beginPath(); vb.forEach(function (v, i) { const y = Y(v); i ? g.lineTo(X(i), y) : g.moveTo(X(i), y); });
    g.strokeStyle = '#94a3b8'; g.lineWidth = 1.2; g.stroke();
  }
  g.beginPath(); va.forEach(function (v, i) { const y = Y(v); i ? g.lineTo(X(i), y) : g.moveTo(X(i), y); });
  g.strokeStyle = '#60a5fa'; g.lineWidth = 1.8; g.stroke();
  g.fillStyle = '#64748b'; g.textAlign = 'left';
  g.fillText(dates[0] || '', padL, H - 6);
  g.textAlign = 'right'; g.fillText(dates[dates.length - 1] || '', w - padR, H - 6);
  g.fillStyle = '#94a3b8'; g.fillText('蓝 = 策略   灰 = 买入持有', w - padR, padT + 11);
}

/* ---------------- 蒙特卡洛扇形图 ---------------- */
function drawMonteCarlo(mc) {
  const c = prep($('mcChart'), 220);
  if (!c) return;
  const g = c.g, w = c.w, H = c.h;
  const paths = (mc && mc.paths) || [];
  if (paths.length < 3) { g.fillStyle = '#64748b'; g.fillText('没有足够的历史收益做蒙特卡洛检验', 16, H / 2); return; }
  const padL = 60, padR = 12, padT = 10, padB = 24;
  let lo = Infinity, hi = -Infinity;
  paths.forEach(function (p) { [p[0], p[4]].forEach(function (v) { lo = Math.min(lo, v); hi = Math.max(hi, v); }); });
  lo = Math.min(lo, 1); hi = Math.max(hi, 1);
  const padv = Math.max((hi - lo) * 0.08, 0.02); lo -= padv; hi += padv;
  const n = paths.length;
  const X = function (i) { return padL + (w - padL - padR) * i / (n - 1); };
  const Y = function (v) { return padT + (H - padT - padB) - (v - lo) / (hi - lo) * (H - padT - padB); };
  grid(g, padL, w - padR, padT, H - padB, 4, function (k) { return ((hi - (hi - lo) * k / 4 - 1) * 100).toFixed(0) + '%'; });

  function band(idxLo, idxHi, fill) {
    g.beginPath();
    paths.forEach(function (p, i) { const y = Y(p[idxHi]); i ? g.lineTo(X(i), y) : g.moveTo(X(i), y); });
    for (let i = n - 1; i >= 0; i--) g.lineTo(X(i), Y(paths[i][idxLo]));
    g.closePath(); g.fillStyle = fill; g.fill();
  }
  band(0, 4, 'rgba(96,165,250,.18)');
  band(1, 3, 'rgba(96,165,250,.34)');

  g.save(); g.setLineDash([4, 4]); g.strokeStyle = '#475569';
  g.beginPath(); g.moveTo(padL, Y(1)); g.lineTo(w - padR, Y(1)); g.stroke(); g.restore();

  g.beginPath(); paths.forEach(function (p, i) { const y = Y(p[2]); i ? g.lineTo(X(i), y) : g.moveTo(X(i), y); });
  g.strokeStyle = '#60a5fa'; g.lineWidth = 1.8; g.stroke();
  g.fillStyle = '#94a3b8'; g.textAlign = 'left';
  g.fillText('未来 ' + (mc.horizon || 365) + ' 天的 5%~95% 分位区间（蓝线为中位数）', padL + 4, padT + 12);
  g.fillText('0', padL, H - 6); g.textAlign = 'right'; g.fillText(String(mc.horizon || 365) + ' 天', w - padR, H - 6);
}
/* ---------------- 绩效分析 tab ---------------- */
const ROWS_A = [
  ['累计收益率', 'total_return', 'pct'], ['年化收益率 CAGR', 'cagr', 'pct'],
  ['年化波动率', 'ann_vol', 'pctu'], ['夏普比率', 'sharpe', 'f2'],
  ['索提诺比率', 'sortino', 'f2'], ['卡玛比率', 'calmar', 'f2'],
  ['Omega 比率', 'omega', 'f2'], ['最大回撤', 'max_drawdown', 'pct'],
  ['最长回撤天数', 'max_dd_days', 'int'], ['回撤修复天数', 'recovery_days', 'int']
];
const ROWS_B = [
  ['VaR 95%（单日）', 'var95', 'pctu'], ['CVaR 95%（尾部平均）', 'cvar95', 'pctu'],
  ['日胜率', 'win_rate', 'pctu'], ['Beta（相对基准）', 'beta', 'f2'],
  ['Alpha（年化）', 'alpha', 'pct'], ['信息比率', 'information_ratio', 'f2'],
  ['成交笔数', 'trades', 'int'], ['完整交易笔数', 'trade_count', 'int'],
  ['交易胜率', 'trade_win_rate', 'pctu'], ['盈亏比', 'payoff_ratio', 'f2'],
  ['单笔期望盈亏', 'expectancy', 'money'], ['平均持仓天数', 'avg_holding_days', 'f1'],
  ['平均仓位', 'exposure_avg', 'pctu'], ['累计手续费', 'fees_paid', 'money'],
  ['累计成交额', 'turnover', 'money'], ['最大连续亏损次数', 'max_consecutive_losses', 'int']
];

function fmtVal(v, kind) {
  if (v === null || v === undefined || (typeof v === 'number' && !isFinite(v))) return '—';
  switch (kind) {
    case 'pct': return pct(v, 2, true);
    case 'pctu': return pct(v, 1, false);
    case 'f2': return Number(v).toFixed(2);
    case 'f1': return Number(v).toFixed(1);
    case 'int': return String(v);
    case 'money': return money(v, 0);
    default: return String(v);
  }
}
function metricBlock(rows, a, b, startIdx) {
  return rowStrip(startIdx) + rows.map(function (r) {
    const va = a ? a[r[1]] : null, vb = b ? b[r[1]] : null;
    const cls = (r[2] === 'pct') ? ' class="' + colorOf(va) + '"' : '';
    return '<tr><td>' + r[0] + '</td><td' + cls + '>' + fmtVal(va, r[2]) + '</td><td class="muted">' + fmtVal(vb, r[2]) + '</td></tr>';
  }).join('');
}
function rowStrip() {
  return '<tr><th>指标</th><th>策略</th><th>买入持有基准</th></tr>';
}
function heatCell(v, d) {
  const a = Math.min(0.72, 0.10 + Math.abs(v) * 2.2);
  const bg = v >= 0 ? 'rgba(34,197,94,' + a + ')' : 'rgba(239,68,68,' + a + ')';
  return '<td style="background:' + bg + ';color:#e8edf7">' + (v * 100).toFixed(d) + '</td>';
}
function renderMonthly(mo) {
  const el = $('monthlyTable');
  if (!mo || !mo.years || !mo.years.length) { el.innerHTML = '<tr><td class="muted">数据不足</td></tr>'; return; }
  let h = '<tr><th></th>';
  for (let i = 1; i <= 12; i++) h += '<th>' + i + '月</th>';
  h += '<th>全年</th></tr>';
  mo.years.forEach(function (y, ri) {
    h += '<tr><td class="y">' + y + '</td>';
    for (let c = 0; c < 12; c++) {
      const v = mo.matrix[ri][c];
      h += (v === null || v === undefined) ? '<td style="color:#334155">·</td>' : heatCell(v, 1);
    }
    const hit = (mo.annual || []).filter(function (a) { return a.year === y; })[0];
    const ann = hit ? hit.ret : null;
    h += '<td style="font-weight:600;background:transparent;color:' + ((ann || 0) >= 0 ? '#4ade80' : '#fca5a5') + '">' +
      (ann === null ? '—' : (ann * 100).toFixed(1) + '%') + '</td></tr>';
  });
  el.innerHTML = h;
}
function renderCorr(corr) {
  const el = $('corrTable');
  if (!corr || !corr.labels || !corr.labels.length) { el.innerHTML = ''; return; }
  let h = '<tr><th></th>' + corr.labels.map(function (l) { return '<th>' + esc(l) + '</th>'; }).join('') + '</tr>';
  corr.labels.forEach(function (l, i) {
    h += '<tr><td class="y">' + esc(l) + '</td>';
    corr.matrix[i].forEach(function (v) {
      if (v === null || v === undefined) { h += '<td>—</td>'; return; }
      const a = Math.min(0.72, Math.abs(v) * 0.72);
      const bg = v > 0 ? 'rgba(239,68,68,' + a + ')' : 'rgba(34,197,94,' + a + ')';
      h += '<td style="background:' + bg + '">' + Number(v).toFixed(2) + '</td>';
    });
    h += '</tr>';
  });
  el.innerHTML = h;
}
function renderLegs(legs) {
  const el = $('legsBody');
  el.innerHTML = (legs || []).map(function (l) {
    const mm = l.metrics || {};
    return '<tr><td>' + esc(l.symbol) + '</td><td>' + (l.weight * 100).toFixed(1) + '%</td>' +
      '<td>' + money(l.final_equity, 0) + '</td><td class="' + colorOf(l.pnl) + '">' + money(l.pnl, 0) + '</td>' +
      '<td>' + (l.contribution * 100).toFixed(1) + '</td><td>' + (mm.sharpe || 0).toFixed(2) + '</td>' +
      '<td>' + pct(mm.max_drawdown, 1, false) + '</td><td>' + (l.trades || 0) + '</td></tr>';
  }).join('');
}
function renderAnnual(mo, bmo) {
  const head = $('annualHead'), body = $('annualBody');
  const smap = {}, bmap = {};
  (mo.annual || []).forEach(function (a) { smap[a.year] = a.ret; });
  (bmo && bmo.annual || []).forEach(function (a) { bmap[a.year] = a.ret; });
  const years = Object.keys(smap).concat(Object.keys(bmap)).filter(function (v, i, arr) { return arr.indexOf(v) === i; }).sort();
  head.innerHTML = '<th>年份</th><th>策略收益</th><th>买入持有</th><th>相对表现</th>';
  body.innerHTML = years.map(function (y) {
    const s = smap[y], b = bmap[y];
    const diff = (s !== undefined && b !== undefined) ? s - b : null;
    return '<tr><td>' + y + '</td><td class="' + colorOf(s) + '">' + pct(s, 1) + '</td>' +
      '<td class="muted">' + pct(b, 1) + '</td><td class="' + colorOf(diff) + '">' + pct(diff, 1) + '</td></tr>';
  }).join('');
}

/* ---------------- 稳健性检验 tab ---------------- */
function renderGrid(grid) {
  const el = $('gridTable');
  if (!grid || !grid.matrix) { el.innerHTML = ''; return; }
  const vals = [];
  grid.matrix.forEach(function (r) { r.forEach(function (v) { if (v !== null && isFinite(v)) vals.push(v); }); });
  const lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
  const cur = state ? state.params : {};
  let h = '<tr><th>慢＼快</th>' + grid.fast.map(function (f) { return '<th>' + f + '</th>'; }).join('') + '</tr>';
  grid.slow.forEach(function (s, ri) {
    h += '<tr><td class="y">' + s + '</td>';
    grid.fast.forEach(function (f, ci) {
      const v = grid.matrix[ri][ci];
      if (v === null || v === undefined || !isFinite(v)) { h += '<td style="color:#334155">—</td>'; return; }
      const rel = hi > lo ? (v - lo) / (hi - lo) : 0.5;
      const isBest = grid.best && f === grid.best.fast && s === grid.best.slow;
      const isCur = (Number(cur.fast) === f && Number(cur.slow) === s);
      const style = 'background:rgba(34,197,94,' + (0.10 + rel * 0.70) + ');' +
        (isBest ? 'outline:2px solid #60a5fa;' : (isCur ? 'outline:2px solid #f59e0b;' : ''));
      h += '<td style="' + style + '" title="夏普 ' + v.toFixed(2) + '">' + v.toFixed(2) + '</td>';
    });
    h += '</tr>';
  });
  el.innerHTML = h;
  const note = $('gridNote');
  if (note && grid.best) {
    note.textContent = '颜色越绿越好（蓝框=最优，橙框=你当前用的参数）。最优 fast=' + grid.best.fast +
      ' / slow=' + grid.best.slow + '，夏普 ' + grid.best.value.toFixed(2) +
      '。如果周边格子也都不错，说明参数稳；只有一格突出就是过拟合的信号。';
  }
}
function renderWalkForward(wf) {
  const body = $('wfBody');
  if (!wf || !wf.folds || !wf.folds.length) { body.innerHTML = '<tr><td colspan="9" class="muted">数据不足，无法做滚动前推</td></tr>'; return; }
  body.innerHTML = wf.folds.map(function (f) {
    return '<tr><td>第 ' + f.fold + ' 折</td><td style="text-align:left;white-space:normal">' + f.train_start + ' ~ ' + f.train_end + '</td>' +
      '<td style="text-align:left;white-space:normal">' + f.test_start + ' ~ ' + f.test_end + '</td>' +
      '<td>' + f.best_fast + ' / ' + f.best_slow + '</td>' +
      '<td class="' + colorOf(f.test_return) + '">' + pct(f.test_return, 1) + '</td>' +
      '<td class="muted">' + pct(f.test_benchmark, 1) + '</td>' +
      '<td>' + f.test_sharpe.toFixed(2) + '</td>' +
      '<td>' + pct(f.test_max_dd, 1, false) + '</td>' +
      '<td>' + pct(f.test_vol, 1, false) + '</td></tr>';
  }).join('');
  setText('wfSummary', '样本外平均 ' + pct(wf.oos_avg_return, 1) + '（' + wf.folds.length + ' 折中 ' +
    Math.round(wf.oos_win_rate * 100) + '% 为正，相对基准 ' + pct(wf.oos_vs_benchmark, 1) + '）');
}
function renderMcStats(mc) {
  const el = $('mcStats');
  if (!mc || !mc.final) { el.innerHTML = ''; return; }
  const f = mc.final;
  el.innerHTML = [
    ['亏损概率', pct(f.prob_loss, 0, false)],
    ['5% 悲观分位', pct(f.p05, 1)],
    ['25% 分位', pct(f.p25, 1)],
    ['中位数', pct(f.p50, 1)],
    ['75% 分位', pct(f.p75, 1)],
    ['95% 乐观分位', pct(f.p95, 1)],
    ['平均最大回撤', pct(f.avg_max_dd, 1, false)],
    ['最差路径回撤', pct(f.worst_max_dd, 1, false)]
  ].map(function (r) { return '<div>' + r[0] + '</div><div>' + r[1] + '</div>'; }).join('');
}

/* ---------------- 交易明细 tab ---------------- */
function renderRoundTrips(list) {
  const body = $('rtBody');
  if (!list || !list.length) { body.innerHTML = '<tr><td colspan="10" class="muted">暂无完整交易</td></tr>'; setText('rtSummary', ''); return; }
  body.innerHTML = list.slice().reverse().map(function (t) {
    return '<tr><td>' + esc(t.symbol || '') + '</td><td>' + esc(t.entry_date) + '</td><td>' + esc(t.exit_date) + '</td>' +
      '<td>' + t.holding_days + '</td><td>' + money(t.entry_price) + '</td><td>' + money(t.exit_price) + '</td>' +
      '<td>' + Number(t.quantity).toFixed(5) + '</td>' +
      '<td class="' + colorOf(t.pnl) + '">' + money(t.pnl, 2) + '</td>' +
      '<td class="' + colorOf(t.ret) + '">' + pct(t.ret, 2) + '</td>' +
      '<td style="text-align:left">' + esc(t.exit_reason) + '</td></tr>';
  }).join('');
  const wins = list.filter(function (t) { return t.pnl > 0; });
  const sumWin = wins.reduce(function (s, t) { return s + t.pnl; }, 0);
  const sumLoss = list.filter(function (t) { return t.pnl <= 0; }).reduce(function (s, t) { return s - t.pnl; }, 0);
  setText('rtSummary', '共 ' + list.length + ' 笔 · 胜率 ' + (wins.length / list.length * 100).toFixed(1) +
    '% · 盈亏比 ' + (sumLoss > 0 ? (sumWin / sumLoss).toFixed(2) : '∞') +
    ' · 平均持仓 ' + (list.reduce(function (s, t) { return s + t.holding_days; }, 0) / list.length).toFixed(1) + ' 天');
}

/* ---------------- 报告加载 ---------------- */
async function loadReport(heavy) {
  const requestId = ++reportRequestId;
  const generation = state && state.generation;
  if (report && (!heavy || hasHeavy)) { renderReport(); return; }
  const st = $('robustStatus');
  if (heavy && st) st.textContent = '计算中（参数网格 + 滚动前推 + 蒙特卡洛），请稍候……';
  try {
    const res = await fetch('/api/report' + (heavy ? '?heavy=1' : ''));
    const data = await res.json();
    if (requestId !== reportRequestId || (state && state.generation !== generation) ||
        (data.generation !== undefined && data.generation !== generation)) return;
    if (data.error) { alert('生成失败：' + data.error); if (st) st.textContent = ''; return; }
    report = data;
    hasHeavy = hasHeavy || !!data.heavy;
    if (st) st.textContent = data.heavy ? '已完成（全周期 ' + data.days + ' 根 K 线）' : '';
  } catch (e) {
    if (requestId !== reportRequestId) return;
    if (st) st.textContent = '生成失败：' + e.message;
    return;
  }
  renderReport();
}
function renderReport() {
  if (!report) return;
  $('perfMetrics').innerHTML = metricBlock(ROWS_A, report.metrics, report.benchmark_metrics, 0);
  $('perfMetrics2').innerHTML = metricBlock(ROWS_B, report.metrics, report.benchmark_metrics, 0);
  setText('perfNote', report.scenario + ' · ' + report.period + ' · 权重 ' +
    (report.weights || []).map(function (w) { return (w * 100).toFixed(0) + '%'; }).join('/'));
  renderMonthly(report.monthly);
  renderAnnual(report.monthly, report.benchmark_monthly);
  renderCorr(report.correlation);
  renderLegs(report.legs);
  if (report.heavy) {
    renderGrid(report.grid);
    renderWalkForward(report.walk_forward);
    renderMcStats(report.monte_carlo);
  }
  const skip = { type: 'noop' };
  try { drawSharpe(report.rolling_sharpe, report.rolling_sharpe_benchmark); } catch (e) { console.error(e); }
  try { if (report.monte_carlo) drawMonteCarlo(report.monte_carlo); } catch (e) { console.error(e); }
}

/* ---------------- 标签页切换 ---------------- */
function switchTab(name) {
  if (location.hash !== '#' + name) history.replaceState(null, '', '#' + name);
  activeTab = name;
  document.querySelectorAll('.tabs button').forEach(function (b) {
    b.classList.toggle('active', b.dataset.tab === name);
  });
  ['replay', 'perf', 'robust', 'research', 'sentiment', 'collection', 'trades', 'help'].forEach(function (t) {
    const el = $('tab-' + t);
    if (el) el.classList.toggle('hidden', t !== name);
  });
  if (name === 'replay' && state) { drawCandles(state.ohlc, state.trades); drawEquity(state.series); }
  if (name === 'perf') loadReport(false);
  if (name === 'trades' && state) renderRoundTrips(state.round_trips || []);
  if (name === 'robust' && report) renderReport();
  if (name === 'sentiment') loadSentiment(true);
  if (name === 'sentiment' && window.loadCompanyEvents) window.loadCompanyEvents();
  if (name === 'collection' && window.loadCollection) window.loadCollection();
}

/* Online public demo: core research is enabled; real collection and local DB management remain desktop-only. */
function markCollectionLocalOnly() {
  const notice = $('collectionNotice');
  if (notice) notice.textContent = '\u5728\u7ebf\u4f53\u9a8c\u4e3a\u4fdd\u62a4\u8bbf\u5ba2\u4e0e\u670d\u52a1\u8d44\u6e90\uff0c\u91c7\u96c6\u3001\u5bfc\u5165\u3001\u6765\u6e90\u7ba1\u7406\u548c\u5bfc\u51fa\u4ec5\u672c\u5730\u7248\u53ef\u7528\u3002\u8bf7\u4f7f\u7528\u201c\u8206\u60c5\u96f7\u8fbe → \u6559\u5b66\u793a\u4f8b\u201d\u4f53\u9a8c\u5c55\u793a\u529f\u80fd.';
  const panel = $('tab-collection');
  if (!panel) return;
  panel.querySelectorAll('button, input, select, textarea').forEach((el) => { el.disabled = true; });
  panel.querySelectorAll('form').forEach((form) => form.addEventListener('submit', (event) => event.preventDefault()));
}
markCollectionLocalOnly();

/* ---------------- 事件绑定与初始化 ---------------- */
$('btnCollectionRiskExperiment').onclick = async function () {
  this.disabled = true;
  setText('collectionRiskResult','正在运行纯价格 / 共享库减仓对照，使用当前已应用参数…');
  try {
    const response = await fetch('/api/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({command:'collection_risk_experiment',include_general:$('sentiment_include_general').checked})});
    const result = await response.json();
    if (!response.ok || result.error) throw new Error(result.error || '实验失败');
    setText('collectionRiskResult','冻结消息 '+result.messages+' 条；可得资产日 '+result.eligible_asset_days+'；负面减仓资产日 '+result.reduced_asset_days+
      '。纯价格收益 '+pct(result.baseline.total_return)+' / 减仓实验 '+pct(result.experiment.total_return)+'。'+result.note+' 文件：'+result.saved_to);
  } catch (error) { setText('collectionRiskResult','实验失败：'+error.message); }
  finally { this.disabled = false; }
};
$('btnResearch').onclick = async function () {
  const button = $('btnResearch');
  button.disabled = true;
  setText('researchStatus', '正在运行四组实验与区块重采样…');
  try {
    const generation = state && state.generation;
    const response = await fetch('/api/control', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({command: 'research', start: $('researchStart').value, end: $('researchEnd').value,
        block: Number($('researchBlock').value), simulations: Number($('researchSims').value)})});
    const data = await response.json();
    if (!response.ok || data.error) throw new Error(data.error || '实验生成失败');
    if (state && state.generation !== generation) {
      setText('researchStatus', '期间参数已重设；旧配置实验已保存，请重新生成当前实验。');
      return;
    }
    $('researchMetrics').innerHTML = Object.entries(data.metrics).map(([key, m]) =>
      '<tr><td>' + esc(data.labels[key]) + '</td><td>' + pct(m.total_return) + '</td><td>' + pct(m.cagr) +
      '</td><td>' + pct(m.ann_vol, 2, false) + '</td><td>' + num(m.sharpe, 3) + '</td><td>' + pct(m.max_drawdown) +
      '</td><td>' + money(m.fees_paid) + '</td></tr>').join('');
    $('researchComparisons').innerHTML = data.comparisons.map((c) =>
      '<tr><td>' + esc(data.labels[c.candidate] + ' − ' + data.labels[c.reference]) + '</td><td>' +
      pct(c.annual_mean_return_difference) + '</td><td>' + pct(c.ci95_low) + '</td><td>' + pct(c.ci95_high) +
      '</td><td>' + (c.contains_zero ? '是' : '否') + '</td></tr>').join('');
    const m = data.manifest;
    setText('researchPeriod', m.effective_start + ' ~ ' + m.effective_end + ' · ' + m.evaluation_bars +
      ' 根日线 · 等权 · ' + m.annualization + ' 日年化 · ' + m.experiment_id);
    setText('researchAudit', JSON.stringify(m, null, 2));
    $('researchFiles').innerHTML = '<a target="_blank" rel="noopener" href="' + esc(data.report_url) + '">打开离线研究报告</a> · ' +
      '<a download href="' + esc(data.archive_url) + '">下载实验 ZIP</a><br>' + esc(data.saved_to);
    setText('researchStatus', '完成：配置、数据、净值、成交和源码快照已保存');
  } catch (error) {
    setText('researchStatus', '失败：' + error.message);
  } finally { button.disabled = false; }
};
$('btnMarketCrypto').onclick = function () { selectMarket('crypto'); };
$('btnMarketUS').onclick = function () { selectMarket('us'); };
$('btnStart').onclick = function () { post('start'); };
$('btnPause').onclick = function () { post('pause'); };
$('btnStep').onclick = function () { post('step'); };
$('btnFast').onclick = function () { post('fastforward'); };
$('btnReset').onclick = function () { post('reset', collectParams()); };
$('btnApply').onclick = function () { post('reset', collectParams()); };
$('btnSave').onclick = async function () {
  await post('save');
  alert('已导出到 results 文件夹：\n\n' + (state && state.saved_to ? state.saved_to : ''));
};
$('btnHeavy').onclick = function () { loadReport(true); };
$('interval').onchange = function () { post('interval', { interval: Number($('interval').value) }); };
document.querySelectorAll('.tabs button').forEach(function (b) {
  b.onclick = function () { switchTab(b.dataset.tab); };
});
window.addEventListener('resize', function () {
  if (!state) return;
  if (activeTab === 'replay') { drawCandles(state.ohlc, state.trades); drawEquity(state.series); }
  if (report) renderReport();
  if (activeTab === 'sentiment' && sentiment) renderSentiment(sentiment);
});

loadFiles().then(poll);
setInterval(poll, 700);
const hashTab = (location.hash || '').replace('#', '');
if (hashTab) setTimeout(function () { switchTab(hashTab); }, 1500);
if ((location.search || '').indexOf('heavy=1') >= 0) setTimeout(function () { loadReport(true); }, 1800);

/* ==================== 舆情雷达（只显示，不参与交易决策） ====================
   默认读采集控制台共享库；教学 CSV 只在明确选中 sample 时展示。不写价格引擎。 */
let sentiment = null, sentimentDate = null, sentimentLoadedAt = 0, sentimentMarket = null, sentimentBusy = false;
const SEN_COLORS = ['#60a5fa', '#34d399', '#f59e0b', '#f472b6', '#a78bfa'];

function selectSentimentMarket(market) {
  sentimentMarket = market;
  if (sentiment) renderSentiment(sentiment);
  else loadSentiment(true);
}
$('senCrypto').onclick = function () { selectSentimentMarket('crypto'); };
$('senUS').onclick = function () { selectSentimentMarket('us'); };
$('senAll').onclick = function () { selectSentimentMarket('all'); };
['senMode', 'senClock'].forEach(function (id) { $(id).onchange = function () {
  if (id === 'senMode') {
    $('senClock').disabled = $('senMode').value === 'sample';
    $('senClock').value = $('senMode').value === 'sample' ? 'replay' : 'now';
  }
  sentiment = null; sentimentDate = null; sentimentLoadedAt = 0;
  loadSentiment(true);
}; });
function visibleSentiment(d) {
  const market = sentimentMarket || (d.data_mode === 'collection' ? 'all' : (state && state.market_type)) || 'crypto';
  const allowed = market === 'us' ? ['AAPL', 'TSLA', 'SPY', 'NVDA'] : ['BTC', 'ETH', 'SOL', 'BNB'];
  const assets = (d.assets || []).filter(function (a) { return market === 'all' || allowed.includes(a); });
  const series = {};
  assets.forEach(function (a) { series[a] = (d.series || {})[a] || []; });
  return Object.assign({}, d, { assets: assets, series: series,
    snapshot: (d.snapshot || []).filter(function (s) { return assets.includes(s.asset); }) });
}
async function loadSentiment(force) {
  const mode = $('senMode').value;
  const clock = $('senClock').value;
  const date = (mode === 'sample' || clock === 'replay') ? ((state && state.market && state.market.date) || '') : '';
  const key = mode + ':' + clock + ':' + date;
  if (sentimentBusy) return;
  if (!force && sentiment && sentimentDate === key && (mode === 'sample' || Date.now() - sentimentLoadedAt < 5000)) { renderSentiment(sentiment); return; }
  sentimentBusy = true;
  const controller = new AbortController();
  const timeout = setTimeout(function () { controller.abort(); }, 20000);
  const st = $('senStatus');
  if (st) st.textContent = sentiment ? '更新中…' : (mode === 'collection' ? '正在读取共享消息库…' : '正在加载教学日频样本…');
  try {
    const res = await fetch('/api/sentiment?source=' + mode + '&window=240' + (date ? '&date=' + encodeURIComponent(date) : ''), {signal:controller.signal});
    const data = await res.json();
    if ($('senMode').value !== mode || $('senClock').value !== clock) return;
    sentimentLoadedAt = Date.now();
    if (!res.ok || !data.ok) {
      if (st) st.textContent = '不可用：' + (data.error || '未知错误');
      console.warn('[舆情] 不可用:', data.error, data.hint || '');
      return;
    }
    sentiment = data; sentimentDate = key;
    console.log('[舆情] 已加载 ' + (data.assets || []).join('/') + ' · asof=' + data.asof +
      ' · 来源=' + data.source + (data.is_sample ? '（教学示例数据）' : ''));
    renderSentiment(data);
  } catch (e) {
    if (st) st.textContent = '加载失败：' + e.message;
    console.error('[舆情] 加载失败', e);
  } finally { clearTimeout(timeout); sentimentBusy = false; }
}

function renderSentiment(d) {
  d = visibleSentiment(d);
  const market = sentimentMarket || (d.data_mode === 'collection' ? 'all' : (state && state.market_type)) || 'crypto';
  $('senCrypto').classList.toggle('primary', market === 'crypto');
  $('senUS').classList.toggle('primary', market === 'us');
  $('senAll').classList.toggle('primary', market === 'all');
  $('senValidationPanel').classList.toggle('hidden', d.data_mode === 'collection');
  const wd = Number(d.window_days || d.window || 0);
  setText('senStatus', d.data_mode === 'collection' ? (d.empty ? '该时点没有可得消息；不会回退到教学样本。' : '共享库已同步 · 可得消息 ' + d.visible_messages + ' 条 · 不参与交易') : d.insufficient_history
    ? ('正常（回放日之前历史不足：' + wd + ' 天，不展示未来数据）')
    : (d.is_sample ? '正常（当前使用教学示例数据）' : '正常'));
  setText('senSource', (d.source || '—') + (d.is_sample ? ' · 教学示例，不是真实舆情档案' : ''));
  setText('senMeta', d.start + ' → ' + d.end + ' · ' + (d.assets || []).length + ' 个资产');
  setText('senAsof', d.asof);
  setText('senWindow', d.window + ' 天' + (d.insufficient_history ? '，实际可用 ' + wd + ' 天' : ''));
  const wn = $('senWindowNote');
  if (wn) {
    if (d.insufficient_history) {
      wn.textContent = d.data_mode === 'collection' ? d.note + ' 可用历史 ' + wd + ' 天。' : '回放日之前只有 ' + wd + ' 天舆情数据；为避免前视偏差，图表只显示截止回放日的信息（不展示未来）。';
      wn.style.display = '';
    } else {
      wn.textContent = d.data_mode === 'collection' ? d.note : '';
      wn.style.display = d.data_mode === 'collection' ? '' : 'none';
    }
  }
  const legend = $('senLegend');
  if (legend) {
    legend.innerHTML = (d.assets || []).map(function (a, i) {
      return '<span><i style="background:' + SEN_COLORS[i % SEN_COLORS.length] + '"></i>' + esc(a) + '</span>';
    }).join('');
  }

  const body = $('senSnapshot');
  if (body) {
    body.innerHTML = (d.snapshot || []).map(function (s) {
      const cls = s.z > 0.05 ? 'up' : (s.z < -0.05 ? 'down' : '');
      return '<tr><td><b>' + esc(s.asset) + '</b></td>' +
        '<td class="' + cls + '">' + num(s.z, 3) + '</td>' +
        '<td>' + pct(s.coverage, 1, false) + '</td>' +
        '<td>' + num(s.evidence, 0) + '</td>' +
        '<td>' + num(s.multiplier, 2) + '</td></tr>';
    }).join('') || '<tr><td colspan="5" class="muted">暂无数据</td></tr>';
  }

  const top = $('senTop');
  if (top) {
    top.innerHTML = (d.snapshot || []).map(function (s) {
      const tag = s.top_direction > 0 ? '<span class="up">正面</span> '
        : (s.top_direction < 0 ? '<span class="down">负面</span> ' : '');
      return '<div class="row" style="align-items:flex-start">' +
        '<span style="min-width:56px"><b>' + esc(s.asset) + '</b></span>' +
        '<span style="text-align:left;color:#cbd5e1">' + tag +
        '<span class="muted">[' + esc(s.top_source) + ']</span> ' + esc(s.top_event || '—') +
        (d.data_mode === 'collection' && s.top_message_id ? ' <button data-shared-message="' + esc(s.top_message_id) + '">查看同一条消息 ↗</button>' : '') + '</span></div>';
    }).join('') || '<div class="muted">暂无数据</div>';
    top.querySelectorAll('[data-shared-message]').forEach(function (button) { button.onclick = function () {
      if (window.showCollectionMessage) window.showCollectionMessage(button.dataset.sharedMessage);
    }; });
  }

  const vb = $('senVerdict');
  if (vb) {
    const rows = (d.verdict || []).filter(function (r) { return String(r['资产']) === 'ALL'; });
    if (rows.length && rows[0]['RankIC_水平z'] === undefined) {
      vb.innerHTML = '<tr><td colspan="5" class="muted">检测到旧版验证结果（缺少「差分 Δz」列）。请重新双击「运行舆情快速自检.bat」生成新结果。</td></tr>';
      drawSentiment(d);
      return;
    }
    vb.innerHTML = rows.map(function (r) {
      const lv = Number(r['RankIC_水平z']);
      const dz = Number(r['RankIC_差分Δz']);
      const t = Number(r['t值']);
      let verdict = '<span class="muted">差分后接近 0 → 大概率是虚假相关</span>';
      if (isFinite(dz) && dz > 0.01) verdict = '<span class="up">差分后仍为正 → 可能是真信息</span>';
      else if (isFinite(dz) && dz < -0.01) verdict = '<span class="down">差分后为负 → 不成立</span>';
      return '<tr><td>' + r['持有期(日)'] + ' 日</td>' +
        '<td>' + (isFinite(lv) ? lv.toFixed(4) : '—') + '</td>' +
        '<td>' + (isFinite(dz) ? dz.toFixed(4) : '—') + '</td>' +
        '<td>' + (isFinite(t) ? t.toFixed(2) : '—') + '</td><td>' + verdict + '</td></tr>';
    }).join('') || '<tr><td colspan="5" class="muted">还没有验证结果。双击「运行舆情快速自检.bat」生成后再回来看。</td></tr>';
  }

  drawSentiment(d);
}

function drawSentiment(d) {
  const cv = $('senChart');
  const H = 240;
  const c = prep(cv, H);
  if (!c) return;                       /* 标签页隐藏时宽度为 0，跳过 */
  const g = c.g, w = c.w;
  const padL = 56, padR = 14, padT = 12, padB = 30;
  const plotH = H - padT - padB;
  const series = d.series || {};
  const names = Object.keys(series);
  if (!names.length) {
    g.fillStyle = '#f59e0b'; g.font = '13px "Microsoft YaHei"';
    g.fillText('暂无舆情曲线数据', 16, H / 2);
    return;
  }
  let dates = [];
  names.forEach(function (n) { if (series[n].length > dates.length) dates = series[n].map(function (p) { return p.d; }); });
  const idx = {};
  dates.forEach(function (dt, i) { idx[dt] = i; });
  let amax = 0.25;
  names.forEach(function (n) { series[n].forEach(function (p) { amax = Math.max(amax, Math.abs(p.z)); }); });
  amax = Math.ceil(amax * 20) / 20;

  const X = function (i) { return padL + (w - padL - padR) * (dates.length <= 1 ? 0.5 : i / (dates.length - 1)); };
  const Y = function (v) { return padT + plotH / 2 - (v / amax) * (plotH / 2); };

  g.strokeStyle = '#232e44'; g.fillStyle = '#64748b'; g.lineWidth = 1; g.textAlign = 'right';
  [amax, amax / 2, 0, -amax / 2, -amax].forEach(function (v) {
    const y = Y(v);
    g.beginPath(); g.moveTo(padL, y); g.lineTo(w - padR, y); g.stroke();
    g.fillText(v.toFixed(2), padL - 8, y + 4);
  });
  g.textAlign = 'left';
  g.fillText(dates[0], padL, H - 10);
  g.textAlign = 'right';
  g.fillText(dates[dates.length - 1], w - padR, H - 10);

  names.forEach(function (n, k) {
    g.strokeStyle = SEN_COLORS[k % SEN_COLORS.length];
    g.lineWidth = 1.6;
    const pts = [];
    g.beginPath();
    series[n].forEach(function (p) {
      const i = idx[p.d];
      if (i === undefined || p.z === null || !isFinite(p.z)) return;
      const x = X(i), y = Y(p.z);
      pts.push({ x: x, y: y });
      if (pts.length === 1) g.moveTo(x, y); else g.lineTo(x, y);
    });
    g.stroke();
    /* 回放刚开始、窗口里只有 1 天时，画一个点，避免看起来像“图坏了” */
    if (pts.length === 1) {
      g.fillStyle = SEN_COLORS[k % SEN_COLORS.length];
      g.beginPath(); g.arc(pts[0].x, pts[0].y, 3.5, 0, Math.PI * 2); g.fill();
    }
  });

  /* 回放日虚线：告诉你「当前看到的快照对应哪一天」 */
  if (d.asof_index !== null && d.asof_index !== undefined && d.asof_index >= 0 && d.asof_index < dates.length) {
    const xm = X(d.asof_index);
    g.save();
    g.strokeStyle = "#e2e8f0"; g.setLineDash([4, 4]); g.lineWidth = 1;
    g.beginPath(); g.moveTo(xm, padT); g.lineTo(xm, padT + plotH); g.stroke();
    g.restore();
    g.fillStyle = "#94a3b8"; g.textAlign = "left";
    g.fillText("回放日 " + d.asof, Math.min(xm + 5, w - padR - 110), padT + 12);
  }
}
