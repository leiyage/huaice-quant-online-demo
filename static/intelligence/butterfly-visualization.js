import {projectGeo} from './globe-math.js';

const color=direction=>direction==='negative'?'#e8848b':direction==='positive'?'#82c9a9':'#d4b77e';
export function impactRisk(item) { const strength=Math.abs(item.net_strength||0);return strength>=.65?'high':strength>=.35?'medium':'low'; }
export function filterImpactNodes(nodes,{query='',risk='all'}={}) {
  const needle=query.trim().toLocaleLowerCase();
  return nodes.filter(item=>{
    const node=item.node||{},text=[node.name,node.node_type,node.country,node.industry,node.asset_symbol,node.description].filter(Boolean).join(' ').toLocaleLowerCase();
    return (!needle||text.includes(needle))&&(risk==='all'||item.level===0||impactRisk(item)===risk);
  });
}
export function clampAnimationSpeed(value) { const parsed=Number(value);return Number.isFinite(parsed)?Math.min(3,Math.max(.25,parsed)):1; }

/** Lightweight, decorative impact overlay. Propagation is computed elsewhere. */
export class ButterflyVisualization {
  constructor(stage,overlay,onSelect=()=>{}) {
    this.stage=stage;this.overlay=overlay;this.onSelect=onSelect;this.enabled=false;this.nodes=[];this.edges=[];this.step=0;this.time=0;this.speed=1;this.filters={query:'',risk:'all'};this.buttons=new Map();
    this.canvas=document.createElement('canvas');this.canvas.className='impact-canvas';this.canvas.setAttribute('aria-hidden','true');
    this.ctx=this.canvas.getContext('2d',{alpha:true});if(!this.ctx)throw new Error('Impact overlay canvas unavailable');
    stage.append(this.canvas);
  }
  setNetwork(nodes,edges,step,selectedId,onSelect=this.onSelect) {
    this.nodes=nodes;this.edges=edges;this.step=step;this.selectedId=selectedId;this.onSelect=onSelect;
    const ids=new Set(nodes.map(item=>item.node.node_id));
    for(const [id,button] of this.buttons)if(!ids.has(id)){button.remove();this.buttons.delete(id);}
    for(const item of nodes) {
      const id=item.node.node_id;let button=this.buttons.get(id);
      if(!button){
        button=document.createElement('button');button.type='button';button.className='impact-node';button.dataset.impactNode=id;
        button.addEventListener('click',()=>this.onSelect(id));this.buttons.set(id,button);this.overlay.append(button);
      }
      button.textContent=item.node.name;button.title=`L${item.level} · ${item.node.node_type} · ${item.node.description}`;
      button.setAttribute('aria-label',`影响节点 L${item.level}：${item.node.name}。${item.node.description}`);
      button.dataset.direction=item.direction||'uncertain';button.dataset.level=String(item.level);button.dataset.nodeType=item.node.node_type;
      button.setAttribute('aria-pressed',String(id===selectedId));
    }
    this.invalidate();
  }
  setFilters(filters={}) { this.filters={...this.filters,...filters};this.invalidate(); }
  setSpeed(value) { this.speed=clampAnimationSpeed(value);this.invalidate(); }
  setEnabled(value) { this.enabled=Boolean(value);this.canvas.hidden=!this.enabled;this.overlay.classList.toggle('impact-active',this.enabled);if(!this.enabled)for(const button of this.buttons.values())button.hidden=true;this.invalidate(); }
  resize(width,height,dpr) { this.width=width;this.height=height;this.dpr=Math.min(dpr,1.25);this.canvas.width=Math.round(width*this.dpr);this.canvas.height=Math.round(height*this.dpr);this.invalidate(); }
  invalidate() { this.dirty=true; }
  get animated() { return this.enabled&&this.step>0; }
  render(view,delta=0) {
    if(!this.enabled)return;
    this.time+=Math.min(Math.max(delta,0),.1)*this.speed;
    const ctx=this.ctx;ctx.setTransform(this.dpr,0,0,this.dpr,0,0);ctx.clearRect(0,0,this.width,this.height);
    const visible=new Map(),shown=new Set(filterImpactNodes(this.nodes,this.filters).filter(item=>item.level<=this.step).map(item=>item.node.node_id));
    for(const item of this.nodes) {
      const p=projectGeo(item.node.latitude,item.node.longitude,view.yaw,view.pitch,this.width,this.height,view.zoom);
      visible.set(item.node.node_id,p);
      const button=this.buttons.get(item.node.node_id);button.hidden=!this.enabled||!shown.has(item.node.node_id)||!p.visible;
      if(!button.hidden)button.style.transform=`translate(${p.x.toFixed(1)}px,${p.y.toFixed(1)}px) translate(-50%,-50%)`;
    }
    for(const relation of this.edges) {
      const source=this.nodes.find(item=>item.node.node_id===relation.source_node),target=this.nodes.find(item=>item.node.node_id===relation.target_node);
      if(!source||!target||target.level>this.step||!shown.has(target.node.node_id))continue;
      const a=visible.get(relation.source_node),b=visible.get(relation.target_node);if(!a?.visible||!b?.visible)continue;
      const bend=Math.min(38,Math.max(14,Math.abs(a.x-b.x)*.11));
      const mx=(a.x+b.x)/2,my=(a.y+b.y)/2-bend;
      ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.quadraticCurveTo(mx,my,b.x,b.y);
      ctx.strokeStyle=color(relation.impact_direction);ctx.globalAlpha=.22+relation.confidence*.36;ctx.lineWidth=.65+relation.impact_strength*1.05;ctx.stroke();
      const t=(this.time*(.23+relation.impact_strength*.22)+(relation.edge_id.length*.071))%1;
      const x=(1-t)*(1-t)*a.x+2*(1-t)*t*mx+t*t*b.x,y=(1-t)*(1-t)*a.y+2*(1-t)*t*my+t*t*b.y;
      ctx.globalAlpha=.82;ctx.fillStyle=color(relation.impact_direction);ctx.beginPath();ctx.arc(x,y,1.8+relation.impact_strength*1.4,0,Math.PI*2);ctx.fill();
    }
    ctx.globalAlpha=1;
    for(const item of this.nodes) {
      if(item.level>this.step||!shown.has(item.node.node_id))continue;const p=visible.get(item.node.node_id);if(!p?.visible)continue;
      const radius=item.level===0?7:4.2,phase=(this.time*.55+item.level*.37)%1;
      ctx.beginPath();ctx.arc(p.x,p.y,radius+(item.level===0?phase*8:0),0,Math.PI*2);
      ctx.strokeStyle=color(item.direction);ctx.globalAlpha=item.level===0?(1-phase)*.52:.4;ctx.lineWidth=1;ctx.stroke();
    }
    ctx.globalAlpha=1;
  }
  dispose() { this.canvas.remove();for(const button of this.buttons.values())button.remove();this.buttons.clear();this.nodes=[];this.edges=[];this.ctx=null;this.stage.classList.remove('impact-active'); }
}
