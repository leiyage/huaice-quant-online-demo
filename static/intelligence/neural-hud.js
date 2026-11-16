import {geoVector,rotateVector} from './globe-math.js';

// Reference-inspired stage dressing. Fixed geometry, not a market model.
const COLORS=['#79e9c6','#80b8ee','#dc728d','#ebcd87','#b5e5d7'];
export function createParticleShell(count=960){
  const golden=Math.PI*(3-Math.sqrt(5));
  return Array.from({length:count},(_,i)=>{
    const lat=Math.asin(1-2*(i+.5)/count)*180/Math.PI,lon=(i*golden*180/Math.PI)%360-180;
    return {position:geoVector(lat,lon),phase:i*2.399,size:.45+(i%7)*.13,red:Math.sin(lon*.035+lat*.03)>.69};
  });
}
function point(position,view,radius=1){
  const [x,y,z]=rotateVector(position.map(v=>v*radius),view.yaw,view.pitch),scale=view.height*.43*view.zoom;
  return {x:view.width/2+x*scale,y:view.height/2-y*scale,visible:z>=0||x*x+y*y>1.012,depth:z};
}
function curvePoint(a,b,c,d,t){const s=1-t;return {x:s*s*s*a.x+3*s*s*t*b.x+3*s*t*t*c.x+t*t*t*d.x,y:s*s*s*a.y+3*s*s*t*b.y+3*s*t*t*c.y+t*t*t*d.y};}

export class NeuralHud {
  constructor(){this.particles=createParticleShell();}
  render(ctx,time,view,glow){
    this.drawShell(ctx,time,view);
    this.drawFilaments(ctx,time,view);
    if(view.width>=520)this.drawChannels(ctx,time,view,glow);
    this.drawReticle(ctx,time,view);
    ctx.globalAlpha=1;
  }
  drawShell(ctx,time,view){
    for(const p of this.particles){
      const radius=1.023+.016*Math.sin(time*.24+p.phase),v=point(p.position,view,radius);
      if(!v.visible)continue;
      const fade=.22+.24*(.5+.5*Math.sin(time*.55+p.phase));
      ctx.fillStyle=p.red?'#e87197':'#a9ffe2';ctx.globalAlpha=fade;
      const size=p.size*(.8+Math.max(0,v.depth)*.5);
      ctx.fillRect(v.x-size/2,v.y-size/2,size,size);
    }
    ctx.globalAlpha=1;
  }
  drawFilaments(ctx,time,view){
    // Bent 3D meridians give the shell an organic neural texture.
    for(let line=0;line<22;line++){
      ctx.beginPath();let connected=false;
      for(let j=0;j<=64;j++){
        const lat=-83+j*166/64,lon=line*360/22-180+Math.sin(lat*.045+line*.6+time*.075)*9;
        const p=point(geoVector(lat,lon),view,1.045+.008*Math.sin(j*.3+line));
        if(p.visible){if(connected)ctx.lineTo(p.x,p.y);else ctx.moveTo(p.x,p.y);connected=true;}else connected=false;
      }
      ctx.strokeStyle=line%5===0?'rgba(195,142,170,.18)':'rgba(150,207,201,.13)';ctx.lineWidth=.55;ctx.stroke();
    }
  }
  drawChannels(ctx,time,view,glow){
    const {width,height}=view,edge=Math.max(26,width*.055),rail=width*.19;
    ctx.font='7px Consolas,monospace';
    for(let side=0;side<2;side++)for(let i=0;i<5;i++){
      const reflect=x=>side?width-x:x,y=height*(.16+i*.155),color=COLORS[(i+side)%COLORS.length];
      const a={x:reflect(edge),y},b={x:reflect(rail*.9),y:y+Math.sin(i*1.7)*33};
      const c={x:reflect(width*.30),y:height*(.17+i*.14)},d={x:reflect(width*.34),y:height*(.22+i*.12)};
      ctx.strokeStyle='rgba(154,188,201,.21)';ctx.lineWidth=.65;
      ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.bezierCurveTo(b.x,b.y,c.x,c.y,d.x,d.y);ctx.stroke();
      const travel=(time*(.075+i*.008)+i*.19+side*.28)%1;
      for(let tail=4;tail>=0;tail--){
        const t=travel-tail*.018;if(t<0)continue;const p=curvePoint(a,b,c,d,t);
        ctx.fillStyle=tail===0?'#e0f5f2':color;ctx.globalAlpha=(1-tail/5)*.8;
        ctx.beginPath();ctx.arc(p.x,p.y,tail===0?1.7:.9,0,Math.PI*2);ctx.fill();
      }
      ctx.globalAlpha=.6;ctx.drawImage(glow,a.x-10,a.y-10,20,20);ctx.globalAlpha=1;
      ctx.strokeStyle=color;ctx.lineWidth=.8;ctx.beginPath();ctx.arc(a.x,a.y,3.7,0,Math.PI*2);ctx.stroke();
      ctx.fillStyle=color;ctx.beginPath();ctx.arc(a.x,a.y,1.5,0,Math.PI*2);ctx.fill();
      ctx.textAlign=side?'right':'left';ctx.fillStyle='#a0b9b9';ctx.fillText(`${side?'OUT':'IN'} / FX 0${i+1}`,reflect(edge+11),y-8);
      // Waveforms are explicitly labeled FX; no prices, forecasts or performance values.
      ctx.strokeStyle='rgba(153,194,184,.35)';ctx.beginPath();
      for(let x=0;x<32;x++){const py=y+8+Math.sin(x*.4+i+time*.7)*2+Math.sin(x*.9+i)*1.3;if(x)ctx.lineTo(reflect(edge+11+x),py);else ctx.moveTo(reflect(edge+11+x),py);}ctx.stroke();
    }
    ctx.strokeStyle='rgba(145,180,193,.17)';ctx.lineWidth=.5;
    for(let side=0;side<2;side++)for(let i=0;i<36;i++){
      const x=side?width-rail:rail,y=height*(.11+i*.022);ctx.beginPath();ctx.moveTo(x,y);ctx.lineTo(x+(side?-1:1)*(i%5===0?5:2),y);ctx.stroke();
    }
  }
  drawReticle(ctx,time,view){
    const x=view.width/2,y=view.height/2,r=view.height*.47*view.zoom;
    ctx.strokeStyle='rgba(132,175,180,.24)';ctx.lineWidth=.6;
    for(let i=0;i<4;i++){const start=i*Math.PI/2+.16;ctx.beginPath();ctx.arc(x,y,r,start,start+.30);ctx.stroke();}
    for(let i=0;i<60;i++){
      const angle=i*Math.PI/30,length=i%5===0?4:1.7;
      ctx.beginPath();ctx.moveTo(x+Math.cos(angle)*r,y+Math.sin(angle)*r);ctx.lineTo(x+Math.cos(angle)*(r+length),y+Math.sin(angle)*(r+length));ctx.stroke();
    }
  }
  dispose(){this.particles=[];}
}
