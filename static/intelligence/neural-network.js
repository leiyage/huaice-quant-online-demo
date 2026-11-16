import {geoVector,rotateVector,clamp} from './globe-math.js';
import {FINANCIAL_CENTERS} from './atlas.js';
import {NeuralHud} from './neural-hud.js';

// Entertainment geometry only. No events, prices, source scores or trading APIs.
export function createNeuralTopology(count=84) {
  const nodes=FINANCIAL_CENTERS.map(([name,lat,lon],i)=>({lat,lon,hub:true,phase:i*1.71}));
  const golden=Math.PI*(3-Math.sqrt(5));
  for(let i=0;i<count;i++) {
    const y=1-2*(i+.5)/count;
    nodes.push({lat:Math.asin(y)*180/Math.PI,lon:((i*golden*180/Math.PI+180)%360)-180,hub:false,phase:i*2.399});
  }
  const vectors=nodes.map(n=>geoVector(n.lat,n.lon));
  const edges=[],keys=new Set();
  const add=(a,b,backbone=false)=>{
    if(a===b)return;const key=[Math.min(a,b),Math.max(a,b)].join(':');
    if(keys.has(key)){if(backbone)edges.find(e=>Math.min(e.a,e.b)===Math.min(a,b)&&Math.max(e.a,e.b)===Math.max(a,b)).backbone=true;return;}keys.add(key);
    edges.push({a,b,backbone,phase:(a*.173+b*.291)%1,speed:.055+((a+b)%7)*.005});
  };
  const distance=(a,b)=>vectors[a].reduce((sum,v,i)=>sum+(v-vectors[b][i])**2,0);
  // A spanning tree keeps the visual fabric connected; nearby links make a mesh.
  const connected=new Set([0]);
  while(connected.size<nodes.length) {
    let best=[0,0,Infinity];
    for(const a of connected)for(let b=0;b<nodes.length;b++)if(!connected.has(b)){const d=distance(a,b);if(d<best[2])best=[a,b,d];}
    add(best[0],best[1]);connected.add(best[1]);
  }
  nodes.forEach((_,a)=>vectors.map((__,b)=>({b,d:b===a?Infinity:distance(a,b)})).sort((x,y)=>x.d-y.d).slice(0,3).forEach(({b})=>add(a,b)));
  [[0,1],[1,6],[6,2],[2,3],[3,4],[4,5],[5,0],[0,4],[1,5]].forEach(([a,b])=>add(a,b,true));
  return {nodes,edges};
}

export function arcPoint(a,b,t,height=.12) {
  const p=a.map((v,i)=>v*(1-t)+b[i]*t);
  const length=Math.hypot(...p)||1;
  const radius=1.014+Math.sin(Math.PI*t)*height;
  return p.map(v=>v/length*radius);
}
export function projectNeural(point,view) {
  const [x,y,z]=rotateVector(point,view.yaw,view.pitch),radius=view.height*.43*view.zoom;
  return {x:view.width/2+x*radius,y:view.height/2-y*radius,
    visible:!(z<0&&x*x+y*y<1.008),depth:z};
}

export class NeuralNetwork {
  constructor(stage,reducedMotion=false) {
    this.stage=stage;this.hud=new NeuralHud();
    this.canvas=document.createElement('canvas');this.canvas.className='neural-canvas';this.canvas.setAttribute('aria-hidden','true');
    this.ctx=this.canvas.getContext('2d',{alpha:true});
    if(!this.ctx)throw new Error('Decorative canvas unavailable');
    this.enabled=true;this.reducedMotion=reducedMotion;this.elapsed=0;this.disposed=false;
    this.topology=createNeuralTopology();
    this.glow=document.createElement('canvas');this.glow.width=this.glow.height=32;
    const sprite=this.glow.getContext('2d');
    if(!sprite)throw new Error('Decorative sprite unavailable');
    const gradient=sprite.createRadialGradient(16,16,0,16,16,16);
    gradient.addColorStop(0,'rgba(186,255,215,1)');gradient.addColorStop(.16,'rgba(115,255,183,.85)');gradient.addColorStop(.45,'rgba(36,213,144,.24)');gradient.addColorStop(1,'rgba(36,213,144,0)');
    sprite.fillStyle=gradient;sprite.fillRect(0,0,32,32);
    this.symbols='01ｱｲｳｴｵｶｷｸｹｺﾀﾁﾂﾃﾄ<>:'.split('');
    this.glyphAtlas=document.createElement('canvas');this.glyphAtlas.width=this.symbols.length*18;this.glyphAtlas.height=22;
    const glyphCtx=this.glyphAtlas.getContext('2d');
    if(!glyphCtx)throw new Error('Decorative glyph atlas unavailable');
    glyphCtx.font='15px Consolas,"Microsoft YaHei",monospace';glyphCtx.textAlign='center';glyphCtx.fillStyle='#a5ffbb';
    this.symbols.forEach((symbol,i)=>glyphCtx.fillText(symbol,i*18+9,17));
    this.canvas.dataset.nodes=String(this.topology.nodes.length);this.canvas.dataset.links=String(this.topology.edges.length);
    stage.append(this.canvas);
    stage.classList.add('neural-cinema');
  }
  get animated(){return this.enabled&&!this.reducedMotion&&!this.disposed;}
  setEnabled(value){this.enabled=Boolean(value);this.canvas.hidden=!this.enabled;this.stage.classList.toggle('neural-cinema',this.enabled);}
  setReducedMotion(value){this.reducedMotion=Boolean(value);}
  resize(width,height,dpr) {
    this.width=width;this.height=height;this.dpr=Math.min(dpr,1.25);
    this.canvas.width=Math.round(width*this.dpr);this.canvas.height=Math.round(height*this.dpr);
  }
  render(view,delta=0) {
    if(this.disposed||!this.enabled)return;
    if(this.animated)this.elapsed+=clamp(delta,0,.1);
    const ctx=this.ctx,time=this.elapsed;
    ctx.setTransform(this.dpr,0,0,this.dpr,0,0);ctx.clearRect(0,0,this.width,this.height);
    const vertices=this.topology.nodes.map(n=>geoVector(n.lat+Math.sin(time*.11+n.phase)*(n.hub?.65:2.2),n.lon+Math.cos(time*.085+n.phase)*(n.hub?.9:3.1)));
    this.drawRain(ctx,time);
    ctx.globalCompositeOperation='lighter';
    this.drawCodeStreams(ctx,time,view);
    this.hud.render(ctx,time,view,this.glow);
    for(let i=0;i<this.topology.edges.length;i++) {
      const edge=this.topology.edges[i],a=vertices[edge.a],b=vertices[edge.b],height=edge.backbone?.29:.055;
      const steps=edge.backbone?28:12;
      let drawing=false;
      ctx.beginPath();
      for(let j=0;j<=steps;j++) {
        const p=projectNeural(arcPoint(a,b,j/steps,height),view);
        if(p.visible){if(drawing)ctx.lineTo(p.x,p.y);else ctx.moveTo(p.x,p.y);drawing=true;}else drawing=false;
      }
      const rose=edge.backbone&&i%3===0;
      ctx.strokeStyle=rose?'rgba(211,109,146,.36)':edge.backbone?'rgba(100,235,175,.40)':'rgba(48,182,130,.23)';ctx.lineWidth=edge.backbone?1:.6;ctx.stroke();
      if(i%3===0||edge.backbone) {
        const packets=edge.backbone?2:1;
        for(let packet=0;packet<packets;packet++){
          const position=(time*edge.speed*(edge.backbone?1.25:1)+edge.phase+packet/packets)%1;
          const reverse=i%2===1;
          let previous=null;
          // Ten short line segments form a continuous fading tail, not a chain of blobs.
          for(let tail=10;tail>=0;tail--){
            const travel=position-tail*.009;if(travel<0){previous=null;continue;}
            const p=projectNeural(arcPoint(a,b,reverse?1-travel:travel,height),view);
            if(p.visible&&previous?.visible){
              ctx.strokeStyle=`rgba(${rose?'255,132,163':'149,255,205'},${((1-tail/11)**1.8*(edge.backbone?.85:.6)).toFixed(3)})`;
              ctx.lineWidth=edge.backbone?1.65:1.05;ctx.beginPath();ctx.moveTo(previous.x,previous.y);ctx.lineTo(p.x,p.y);ctx.stroke();
            }
            if(tail===0&&p.visible){const size=edge.backbone?15:10;ctx.globalAlpha=.9;ctx.drawImage(this.glow,p.x-size/2,p.y-size/2,size,size);ctx.globalAlpha=1;}
            previous=p;
          }
        }
      }
    }
    vertices.forEach((v,i)=>{
      const p=projectNeural(v.map(x=>x*1.016),view);if(!p.visible)return;
      const hub=this.topology.nodes[i].hub,size=hub?17:8;
      ctx.globalAlpha=hub?.82:.45+.18*Math.sin(time*.9+i);ctx.drawImage(this.glow,p.x-size/2,p.y-size/2,size,size);
      ctx.globalAlpha=hub?.85:.65;ctx.fillStyle='#a3ffcc';ctx.beginPath();ctx.arc(p.x,p.y,hub?1.8:1,0,Math.PI*2);ctx.fill();
      if(hub){const cycle=(time*.24+i*.17)%1;ctx.globalAlpha=1;ctx.strokeStyle=`rgba(94,248,153,${((1-cycle)*.35).toFixed(3)})`;ctx.lineWidth=.7;ctx.beginPath();ctx.arc(p.x,p.y,4+cycle*13,0,Math.PI*2);ctx.stroke();}
    });
    ctx.globalAlpha=1;ctx.globalCompositeOperation='source-over';
    this.canvas.dataset.frames=String(Number(this.canvas.dataset.frames||0)+1);
    this.canvas.dataset.time=this.elapsed.toFixed(3);
  }
  drawCodeStreams(ctx,time,view) {
    // Descending meridian strips follow the globe; occlusion keeps the far side hidden.
    for(let column=0;column<30;column++){
      const lon=-180+column*12,head=82-(time*(4+column%5*.6)+column*19)%164;
      for(let row=0;row<8;row++){
        const lat=head+row*3;if(lat>84)continue;
        const p=projectNeural(geoVector(lat,lon).map(v=>v*1.02),view);
        if(!p.visible||p.depth<.12)continue;
        const glyph=(Math.floor(time*(1+column%3)) +column*7+row*3)%this.symbols.length;
        const size=(7+2*p.depth)*clamp(view.zoom,.85,1.3);
        ctx.globalAlpha=(row===0?.48:.24*(1-row/8))*Math.min(1,p.depth*1.6);
        ctx.drawImage(this.glyphAtlas,glyph*18,0,18,22,p.x-size/2,p.y-size*.6,size,size*1.22);
      }
    }
    ctx.globalAlpha=1;
  }
  drawRain(ctx,time) {
    // Edge-only decorative glyphs, never described as captured traffic or signals.
    for(let i=0;i<22;i++) {
      const side=i%2,x=side?this.width-15-Math.floor(i/2)*10:15+Math.floor(i/2)*10;
      const y=(time*(15+i%5*3)+i*37)%(this.height+130)-20;
      for(let j=0;j<9;j++) {
        ctx.globalAlpha=(j===0?.28:.14*(1-j/9))*(i%3===0?1:.5);
        const glyph=(Math.floor(time*.8)+i+j)%this.symbols.length;
        ctx.drawImage(this.glyphAtlas,glyph*18,0,18,22,x-4,y-j*11,8,10);
      }
    }
    ctx.globalAlpha=1;
  }
  dispose() {
    if(this.disposed)return;this.disposed=true;
    this.canvas.remove();this.canvas.width=this.canvas.height=1;
    this.glow.width=this.glow.height=1;this.topology=null;this.ctx=null;
    this.glyphAtlas.width=this.glyphAtlas.height=1;this.symbols=null;
    this.hud.dispose();this.stage.classList.remove('neural-cinema');
  }
}
