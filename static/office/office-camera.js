/** SVG viewBox camera. No continuous render loop; RAF runs only during input/focus. */
export const homePose=Object.freeze({cx:600,cy:310,zoom:1.05});
export function clampPose(pose){
  const finite=(v,fallback)=>Number.isFinite(v)?v:fallback;
  return {cx:Math.max(-100,Math.min(1300,finite(pose.cx,600))),cy:Math.max(-150,Math.min(950,finite(pose.cy,310))),zoom:Math.max(.75,Math.min(2.4,finite(pose.zoom,1.05)))};
}
export function cameraBox(pose){const p=clampPose(pose),w=1080/p.zoom,h=650/p.zoom;return {x:p.cx-w/2,y:p.cy-h/2,w,h};}
export function zoomAround(pose,factor,point){
  const p=clampPose(pose),zoom=clampPose({...p,zoom:p.zoom*factor}).zoom,ratio=p.zoom/zoom;
  return clampPose({cx:point.x+(p.cx-point.x)*ratio,cy:point.y+(p.cy-point.y)*ratio,zoom});
}
export class OfficeCamera{
  constructor(svg,{label,hideTooltip=()=>{},onManual=()=>{}}={}){
    this.onManual=onManual;this.svg=svg;this.label=label;this.hideTooltip=hideTooltip;this.pose={...homePose};this.raf=null;this.pointer=null;this.suppressClick=false;this.abort=new AbortController();
    const on=(type,handler,options={})=>svg.addEventListener(type,handler,{...options,signal:this.abort.signal});
    on('wheel',event=>{event.preventDefault();this.onManual();this.hideTooltip();this.cancel();this.pose=zoomAround(this.pose,Math.exp(-Math.max(-150,Math.min(150,event.deltaY))*.002),this.point(event.clientX,event.clientY));this.schedule();},{passive:false});
    on('pointerdown',event=>{if(event.button!==0||event.isPrimary===false)return;this.onManual();this.cancel();this.suppressClick=false;this.pointer={id:event.pointerId,x:event.clientX,y:event.clientY,pose:{...this.pose},dragged:false};});
    on('pointermove',event=>{
      const p=this.pointer;if(!p||p.id!==event.pointerId)return;
      const dx=event.clientX-p.x,dy=event.clientY-p.y;
      if(!p.dragged&&Math.hypot(dx,dy)<5)return;
      if(!p.dragged){p.dragged=true;svg.setPointerCapture(event.pointerId);svg.classList.add('camera-dragging');this.hideTooltip();}
      const matrix=svg.getScreenCTM()?.inverse();if(!matrix)return;
      this.pose=clampPose({...p.pose,cx:p.pose.cx-dx*matrix.a-dy*matrix.c,cy:p.pose.cy-dx*matrix.b-dy*matrix.d});this.schedule();
    });
    const finish=event=>{if(this.pointer?.id!==event.pointerId)return;this.suppressClick=this.pointer.dragged;this.pointer=null;svg.classList.remove('camera-dragging');if(svg.hasPointerCapture(event.pointerId))svg.releasePointerCapture(event.pointerId);};
    on('pointerup',finish);on('pointercancel',finish);
    on('click',event=>{if(this.suppressClick){event.preventDefault();event.stopImmediatePropagation();this.suppressClick=false;}},{capture:true});
    on('keydown',event=>{
      if(event.target!==svg)return;
      this.onManual();
      const shifts={ArrowLeft:[-35,0],ArrowRight:[35,0],ArrowUp:[0,-35],ArrowDown:[0,35]};
      if(shifts[event.key]){event.preventDefault();this.cancel();const [x,y]=shifts[event.key];this.pose=clampPose({...this.pose,cx:this.pose.cx+x/this.pose.zoom,cy:this.pose.cy+y/this.pose.zoom});this.schedule();}
      else if(['+','=','-'].includes(event.key)){event.preventDefault();this.zoom(event.key==='-'?1/1.15:1.15);}
      else if(event.key==='Home'){event.preventDefault();this.reset();}
    });
    this.write();
  }
  point(x,y){const matrix=this.svg.getScreenCTM()?.inverse();return matrix?new DOMPoint(x,y).matrixTransform(matrix):{x:this.pose.cx,y:this.pose.cy};}
  write(){const b=cameraBox(this.pose);this.svg.setAttribute('viewBox',`${b.x} ${b.y} ${b.w} ${b.h}`);if(this.label)this.label.textContent=`${Math.round(this.pose.zoom*100)}%`;}
  schedule(){if(this.raf===null)this.raf=requestAnimationFrame(()=>{this.raf=null;this.write();});}
  cancel(){if(this.raf!==null)cancelAnimationFrame(this.raf);this.raf=null;}
  move(target,duration=440){
    this.cancel();const from={...this.pose},to=clampPose(target);
    if(document.body.matches('.low-motion,.paused')||matchMedia('(prefers-reduced-motion: reduce)').matches){this.pose=to;this.write();return;}
    const start=performance.now();
    const animate=now=>{const t=Math.min(1,(now-start)/duration),ease=1-Math.pow(1-t,3);this.pose={cx:from.cx+(to.cx-from.cx)*ease,cy:from.cy+(to.cy-from.cy)*ease,zoom:from.zoom+(to.zoom-from.zoom)*ease};this.write();this.raf=t<1?requestAnimationFrame(animate):null;};
    this.raf=requestAnimationFrame(animate);
  }
  focus(element,duration=440){const r=element.getBoundingClientRect(),point=this.point(r.left+r.width/2,r.top+r.height/2);this.move({cx:point.x+70,cy:point.y+30,zoom:1.8},duration);}
  zoom(factor){this.move({...this.pose,zoom:this.pose.zoom*factor});}
  reset(){this.move(homePose);}
  destroy(){this.cancel();this.abort.abort();}
}
