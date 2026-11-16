/** Phase-one event contract. Future API/WS adapters must normalize into this shape.
 * @typedef {Object} IntelligenceEvent
 * @property {string} event_id
 * @property {string} title
 * @property {string} description
 * @property {string} source
 * @property {string|null} source_url
 * @property {string} published_at UTC ISO timestamp, distinct from captured_at.
 * @property {string} captured_at UTC ISO timestamp.
 * @property {string} country
 * @property {string} region
 * @property {number} latitude
 * @property {number} longitude
 * @property {string} category
 * @property {number} importance Integer 1..5.
 * @property {'negative'|'positive'|'neutral'} sentiment
 * @property {string[]} related_assets Research labels, not executable instruments.
 * @property {'queued'|'completed'} analysis_status
 * @property {number|null} confidence Demonstration value, NOT prediction probability.
 * @property {boolean} is_demo
 * @property {Object} analysis Preset narrative; a future model adapter must add provenance.
 */
export function validateSnapshot(data) {
  if (data?.schema_version !== 1 || !/^\d{4}-\d{2}-\d{2}$/.test(data.snapshot_date) || !Array.isArray(data.events) || !data.events.length) {
    throw new Error('事件快照格式不兼容或没有事件。');
  }
  const ids = new Set();
  const utc = value => typeof value === 'string' && /Z$/.test(value) && Number.isFinite(Date.parse(value));
  for (const e of data.events) {
    if (!e || ['event_id','title','description','source','country','region','category'].some(k => typeof e[k] !== 'string' || !e[k].trim()) || ids.has(e.event_id)) throw new Error('事件必填字段缺失或编号重复。');
    ids.add(e.event_id);
    if (e.is_demo !== true) throw new Error('第一阶段仅接受明确标记的 DEMO 事件。');
    if (!utc(e.published_at) || !utc(e.captured_at) || Date.parse(e.captured_at) < Date.parse(e.published_at)) throw new Error('事件必须使用 UTC 时间，捕捉时间不能早于发布时间。');
    if (!Number.isFinite(e.latitude) || Math.abs(e.latitude) > 90 || !Number.isFinite(e.longitude) || Math.abs(e.longitude) > 180) throw new Error('事件地理坐标无效。');
    if (!Number.isInteger(e.importance) || e.importance < 1 || e.importance > 5 || !['negative','positive','neutral'].includes(e.sentiment) || !['queued','completed'].includes(e.analysis_status)) throw new Error('事件等级或状态无效。');
    if (e.confidence !== null && (!Number.isFinite(e.confidence) || e.confidence < 0 || e.confidence > 1)) throw new Error('模拟置信度字段无效。');
    if (!Array.isArray(e.related_assets) || !e.related_assets.every(a => typeof a === 'string') || !e.analysis || !Array.isArray(e.analysis.sectors) || !e.analysis.sectors.every(s => typeof s === 'string') || !Array.isArray(e.analysis.path) || e.analysis.path.length !== 3 || !e.analysis.path.every(s => typeof s === 'string') || ['summary','reliability','result'].some(k => typeof e.analysis[k] !== 'string')) throw new Error('结构化分析字段不完整。');
    if (e.source_url !== null) {
      let url;
      try { url = new URL(e.source_url); } catch { throw new Error('来源链接无效。'); }
      if (!['https:','http:'].includes(url.protocol) || url.username || url.password) throw new Error('来源链接协议无效。');
    }
  }
  return data;
}

export class DemoEventSource {
  async load({signal} = {}) {
    const response = await fetch('/static/intelligence/demo-events.json', {signal, cache: 'no-store'});
    if (!response.ok) throw new Error(`本地数据请求失败（HTTP ${response.status}）。`);
    return validateSnapshot(await response.json());
  }
}

/** Pure filtering shared by the feed, map and timeline. No trading state or writes. */
export function filterEvents(events, {query = '', country = 'all', category = 'all', priority = false, hours = 'all', sort = 'time'} = {}) {
  const words = query.trim().toLocaleLowerCase();
  const lastCapture = Math.max(...events.map(e => Date.parse(e.captured_at)));
  return events.filter(e => (country === 'all' || e.country === country) && (category === 'all' || e.category === category) && (!priority || e.importance >= 4) && (hours === 'all' || Date.parse(e.captured_at) >= lastCapture - Number(hours) * 3600000) && (!words || [e.title,e.description,e.source,e.country,e.region,e.category,...e.related_assets].join(' ').toLocaleLowerCase().includes(words)))
    .sort((a,b) => (sort === 'importance' ? b.importance - a.importance : 0) || Date.parse(b.captured_at) - Date.parse(a.captured_at) || a.event_id.localeCompare(b.event_id));
}
