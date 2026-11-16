import {LAND_POLYGONS} from './earth-land.js';

// Offline Natural Earth outlines, styled as a fictional digital globe.
// Geometry provenance/license: earth-assets.md. Not satellite or survey imagery.
export function createEarthTexture() {
  const canvas=document.createElement('canvas');canvas.width=2048;canvas.height=1024;
  const ctx=canvas.getContext('2d');
  if(!ctx)throw new Error('无法创建本地地表纹理。');
  const {width,height}=canvas;
  const ocean=ctx.createLinearGradient(0,0,0,height);
  ocean.addColorStop(0,'#041914');ocean.addColorStop(.5,'#031510');ocean.addColorStop(1,'#041813');
  ctx.fillStyle=ocean;ctx.fillRect(0,0,width,height);
  const land=new Path2D();
  for(const polygon of LAND_POLYGONS)for(const ring of polygon){
    ring.forEach(([lon,lat],i)=>{const x=(lon+180)/360*width,y=(90-lat)/180*height;if(i)land.lineTo(x,y);else land.moveTo(x,y);});land.closePath();
  }
  ctx.save();ctx.clip(land,'evenodd');
  const surface=ctx.createLinearGradient(0,0,0,height);
  surface.addColorStop(0,'#071f1c');surface.addColorStop(.35,'#07231d');surface.addColorStop(.65,'#061b19');surface.addColorStop(1,'#09211e');
  ctx.fillStyle=surface;ctx.fillRect(0,0,width,height);
  // Fixed seeded particle atlas, clipped to coastlines. Colors are fictional FX,
  // independent of news, countries, sentiment, assets and financial risk.
  let seed=7183;
  const sample=()=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return seed/4294967296;};
  for(let y=3;y<height;y+=6)for(let x=3;x<width;x+=6){
    const px=x+(sample()-.5)*5,py=y+(sample()-.5)*5;
    const field=Math.sin(px*.012+Math.sin(py*.016)*2.4)*Math.cos(py*.017-px*.003);
    const red=field>.39,bright=sample(),size=1.2+sample()*2.9;
    ctx.fillStyle=red?`rgba(239,65,110,${(.45+bright*.5).toFixed(2)})`:`rgba(${bright>.86?'171,255,222':'65,221,167'},${(.36+bright*.6).toFixed(2)})`;
    ctx.fillRect(px,py,size,size);
    if(bright>.985){ctx.fillStyle=red?'rgba(255,86,125,.15)':'rgba(100,255,210,.17)';ctx.beginPath();ctx.arc(px+size/2,py+size/2,size*2.6,0,Math.PI*2);ctx.fill();}
  }
  // Fixed decorative symbols, never financial values or generated news.
  ctx.font='11px Consolas,monospace';ctx.textAlign='center';const glyphs='010110:<>1010';
  for(let x=12,column=0;x<width;x+=28,column++)for(let y=10,row=0;y<height;y+=19,row++){
    const strength=(Math.sin(column*1.7+row*.37)+1)*.022+.025;
    ctx.fillStyle=`rgba(111,238,158,${strength.toFixed(3)})`;ctx.fillText(glyphs[(column*7+row*3)%glyphs.length],x,y);
  }
  ctx.restore();
  // Layered shorelines preserve bays and islands without inventing curved coastlines.
  ctx.strokeStyle='rgba(44,176,139,.10)';ctx.lineWidth=3;ctx.stroke(land);
  ctx.strokeStyle='rgba(79,224,165,.30)';ctx.lineWidth=.85;ctx.stroke(land);
  ctx.strokeStyle='rgba(84,175,128,.11)';ctx.lineWidth=.65;
  for(let lon=-180;lon<=180;lon+=15){const x=(lon+180)/360*width;ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,height);ctx.stroke();}
  for(let lat=-75;lat<=75;lat+=15){const y=(90-lat)/180*height;ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(width,y);ctx.stroke();}
  canvas.dataset.texture='natural-earth-110m-matrix';
  return canvas;
}
