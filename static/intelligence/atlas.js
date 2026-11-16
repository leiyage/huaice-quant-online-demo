// Replaceable renderer: setEvents(events, selectedId). No engine or data-source access.
// Coarse hand-authored land silhouettes are geographic context, not survey boundaries.
const NS = 'http://www.w3.org/2000/svg';
export const FINANCIAL_CENTERS = [
  ['纽约',40.7128,-74.006,-26,-14],['伦敦',51.5074,-0.1278,-36,-14],
  ['上海',31.2304,121.4737,-22,-17],['香港',22.3193,114.1694,-34,23],
  ['东京',35.6762,139.6503,11,-8],['新加坡',1.3521,103.8198,-25,23],
  ['法兰克福',50.1109,8.6821,13,1],['迪拜',25.2048,55.2708,10,18],
];
export const LAND = [
  [[-168,70],[-142,70],[-130,59],[-123,50],[-124,40],[-116,31],[-107,23],[-98,16],[-87,16],[-81,8],[-77,9],[-86,22],[-82,25],[-81,31],[-75,36],[-67,45],[-53,53],[-60,60],[-80,63],[-94,73],[-123,73]],
  [[-73,83],[-25,80],[-20,67],[-43,59],[-56,64]],
  [[-81,12],[-68,11],[-59,6],[-48,0],[-35,-7],[-40,-22],[-52,-33],[-65,-55],[-74,-48],[-71,-30],[-80,-6]],
  [[-17,35],[-1,36],[12,33],[30,31],[35,23],[44,12],[51,11],[43,-13],[35,-21],[28,-34],[18,-35],[12,-17],[8,2],[-3,5],[-14,11],[-17,22]],
  [[-10,36],[-10,44],[-2,49],[5,54],[10,58],[7,65],[20,71],[30,70],[35,61],[55,70],[90,76],[125,73],[155,66],[179,65],[174,52],[150,48],[142,38],[129,40],[122,31],[115,22],[109,19],[108,10],[103,1],[99,9],[94,18],[88,22],[80,7],[73,20],[66,25],[57,25],[52,15],[43,13],[37,30],[27,40],[14,40],[8,43],[2,40]],
  [[-8,58],[-2,59],[1,52],[-5,50]],[[130,33],[139,36],[145,45],[141,45],[135,36]],
  [[113,-22],[122,-17],[130,-12],[139,-13],[145,-17],[153,-27],[151,-38],[139,-39],[131,-32],[116,-35]],
  [[47,-13],[50,-16],[47,-25],[44,-24]],[[166,-35],[178,-39],[173,-46],[166,-46]],
  [[95,5],[105,-3],[115,-8],[129,-8],[141,-7],[149,-7],[143,-3],[131,-3],[118,-4],[110,-5],[102,2]],
];
function svg(tag, attrs = {}, text) {
  const el = document.createElementNS(NS, tag);
  for (const [k,v] of Object.entries(attrs)) el.setAttribute(k, String(v));
  if (text !== undefined) el.textContent = text;
  return el;
}
export function project(latitude, longitude) { return [26 + (longitude + 180) / 360 * 648, 24 + (80 - latitude) / 140 * 252]; }
export class EventAtlas {
  constructor(container, onSelect) {
    this.onSelect = onSelect;
    this.root = svg('svg', {viewBox:'0 0 700 310',role:'group','aria-label':'全球事件二维定位图，使用键盘 Tab 可选择事件节点'});
    const grid = svg('g', {class:'map-grid'});
    for (let lat = -60; lat <= 80; lat += 20) { const [x,y] = project(lat,-180); grid.append(svg('path',{d:`M${x},${y} H674`})); }
    for (let lon = -180; lon <= 180; lon += 30) { const [x] = project(0,lon); grid.append(svg('path',{d:`M${x},24 V276`})); }
    this.root.append(grid);
    const land = svg('g', {class:'map-land','aria-hidden':'true'});
    LAND.forEach(shape => land.append(svg('polygon',{points:shape.map(([lon,lat]) => project(lat,lon).join(',')).join(' ')})));
    this.root.append(land);
    const cities = svg('g', {class:'map-city','aria-hidden':'true'});
    FINANCIAL_CENTERS.forEach(([name,lat,lon,dx,dy]) => { const [x,y] = project(lat,lon); const g = svg('g'); g.append(svg('circle',{cx:x,cy:y,r:2}), svg('text',{x:x+dx,y:y+dy},name)); cities.append(g); });
    this.root.append(cities);
    this.markers = svg('g'); this.root.append(this.markers); container.replaceChildren(this.root);
  }
  setEvents(events, selectedId) {
    this.markers.replaceChildren();
    const groups = new Map();
    events.forEach(e => { const key = `${e.latitude},${e.longitude}`; if (!groups.has(key)) groups.set(key,[]); groups.get(key).push(e); });
    groups.forEach(group => group.forEach((e,i) => {
      const [x,y] = project(e.latitude,e.longitude);
      const dx = group.length > 1 ? (i - (group.length-1)/2)*13 : 0;
      const marker = svg('g',{class:`map-event sentiment-${e.sentiment}`,role:'button',tabindex:0,'aria-label':`DEMO ${e.region}：${e.title}`,'aria-pressed':e.event_id===selectedId,'data-event-id':e.event_id});
      marker.append(svg('title',{},`DEMO · ${e.region} · ${e.title}`));
      if (dx) marker.append(svg('path',{class:'map-line',d:`M${x},${y} L${x+dx},${y}`}));
      marker.append(svg('circle',{class:'halo',cx:x+dx,cy:y,r:e.event_id===selectedId?14:10}),svg('circle',{class:'core',cx:x+dx,cy:y,r:4}));
      if (e.event_id===selectedId) marker.append(svg('circle',{class:'focus-ring',cx:x+dx,cy:y,r:19}));
      marker.addEventListener('click',() => this.onSelect(e.event_id));
      marker.addEventListener('keydown',event => { if (event.key==='Enter' || event.key===' ') {event.preventDefault(); this.onSelect(e.event_id);} });
      this.markers.append(marker);
    }));
  }
  dispose() { this.root.remove(); }
}
