import {FINANCIAL_CENTERS} from './atlas.js';
import {createEarthTexture} from './earth-texture.js';
import {RAD,clamp,focusAngles,projectGeo,zoomFromDelta} from './globe-math.js';
import {NeuralNetwork} from './neural-network.js';
import {ButterflyVisualization} from './butterfly-visualization.js';

const VERTEX=`attribute vec2 position;void main(){gl_Position=vec4(position,0.,1.);}`;
const FRAGMENT=`precision mediump float;
uniform vec2 resolution;uniform float yaw;uniform float pitch;uniform float zoom;uniform sampler2D earth;
void main(){
 vec2 p=(gl_FragCoord.xy/resolution-.5)*2.;p.x*=resolution.x/resolution.y;p/=(.86*zoom);
 float rr=dot(p,p);float edge=sqrt(rr);
 if(rr>1.){float glow=exp(-(edge-1.)*34.)*.28;gl_FragColor=vec4(vec3(.10,.65,.35)*glow,glow);return;}
 vec3 n=vec3(p,sqrt(max(0.,1.-rr)));
 float cp=cos(pitch),sp=sin(pitch),cy=cos(yaw),sy=sin(yaw);
 vec3 a=vec3(n.x,cp*n.y+sp*n.z,-sp*n.y+cp*n.z);
 vec3 world=vec3(cy*a.x-sy*a.z,a.y,sy*a.x+cy*a.z);
 vec2 uv=vec2(atan(world.x,world.z)/6.28318530718+.5,.5-asin(clamp(world.y,-1.,1.))/3.14159265359);
 vec3 surface=texture2D(earth,uv).rgb;
 float light=.47+.57*max(0.,dot(n,normalize(vec3(-.55,.5,1.))));
 float rim=pow(1.-n.z,3.);
 vec3 color=surface*light+vec3(.04,.32,.17)*rim*.42;
 float alpha=1.-smoothstep(.995,1.,edge);
 gl_FragColor=vec4(color,alpha);
}`;

/** Native WebGL sphere ray renderer. Owns its GPU resources and event listeners. */
export class WebGLGlobe {
  constructor(container,onSelect,onFailure,onMotionChange) {
    this.container=container;this.onSelect=onSelect;this.onFailure=onFailure;this.onMotionChange=onMotionChange;
    this.disposed=false;this.frame=null;this.lastFrame=0;this.dirty=true;this.onScreen=true;
    this.yaw=-20*RAD;this.pitch=20*RAD;this.zoom=1;this.focusTarget=null;this.drag=null;
    this.autoRotate=!matchMedia('(prefers-reduced-motion: reduce)').matches;
    this.events=[];this.selectedId=null;this.buttons=new Map();this.eventPositions=new Map();this.cityLabels=[];
    this.abort=new AbortController();this.resources={};
    this.stage=document.createElement('div');this.stage.className='globe-stage';
    this.canvas=document.createElement('canvas');this.canvas.className='globe-canvas';this.canvas.tabIndex=0;
    this.canvas.setAttribute('aria-label','3D 情报地球：拖动旋转，滚轮缩放；键盘方向键旋转，加减号缩放。');
    this.overlay=document.createElement('div');this.overlay.className='globe-overlay';this.overlay.setAttribute('aria-label','3D 地球可见事件与金融中心');
    this.stage.append(this.canvas,this.overlay);container.replaceChildren(this.stage);
    try {
      this.gl=this.canvas.getContext('webgl',{alpha:true,antialias:false,powerPreference:'low-power',preserveDrawingBuffer:false});
      if(!this.gl)throw new Error('浏览器未提供 WebGL');
      this.initGraphics();this.initCities();this.initControls();
      this.resizeObserver=new ResizeObserver(()=>this.resize());this.resizeObserver.observe(container);
      this.intersectionObserver=new IntersectionObserver(entries=>{this.onScreen=entries[0].isIntersecting;if(this.onScreen)this.invalidate();else this.stop();});this.intersectionObserver.observe(container);
      document.addEventListener('visibilitychange',()=>{if(document.hidden)this.stop();else this.invalidate();},{signal:this.abort.signal});
      this.motionQuery=matchMedia('(prefers-reduced-motion: reduce)');
      this.motionQuery.addEventListener('change',event=>{
        this.neural?.setReducedMotion(event.matches);
        if(event.matches){this.setAutoRotate(false);this.focusTarget=null;}
        this.onMotionChange?.();this.invalidate();
      },{signal:this.abort.signal});
      this.canvas.addEventListener('webglcontextlost',event=>{event.preventDefault();this.stop();this.onFailure('WebGL 上下文已丢失，已回退二维视图。');},{signal:this.abort.signal});
      // Optional entertainment layer: a failed effect must not disable the globe.
      try {this.neural=new NeuralNetwork(this.stage,this.motionQuery.matches);} catch {this.neural=null;}
      try {this.butterfly=new ButterflyVisualization(this.stage,this.overlay);} catch {this.butterfly=null;}
      this.resize();
    } catch(error) {this.dispose();throw error;}
  }
  initGraphics() {
    const gl=this.gl;
    const compile=(type,source)=>{
      const shader=gl.createShader(type);gl.shaderSource(shader,source);gl.compileShader(shader);
      if(!gl.getShaderParameter(shader,gl.COMPILE_STATUS)){const message=gl.getShaderInfoLog(shader);gl.deleteShader(shader);throw new Error(message||'地球着色器编译失败');}return shader;
    };
    const shaders=[];
    try {
      shaders.push(compile(gl.VERTEX_SHADER,VERTEX));shaders.push(compile(gl.FRAGMENT_SHADER,FRAGMENT));
      const program=gl.createProgram();this.resources.program=program;shaders.forEach(s=>gl.attachShader(program,s));gl.linkProgram(program);
      if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw new Error('地球着色程序连接失败');
      gl.useProgram(program);
      const buffer=gl.createBuffer();this.resources.buffer=buffer;gl.bindBuffer(gl.ARRAY_BUFFER,buffer);
      gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,1,-1,-1,1,-1,1,1,-1,1,1]),gl.STATIC_DRAW);
      const attribute=gl.getAttribLocation(program,'position');gl.enableVertexAttribArray(attribute);gl.vertexAttribPointer(attribute,2,gl.FLOAT,false,0,0);
      const texture=gl.createTexture();this.resources.texture=texture;gl.bindTexture(gl.TEXTURE_2D,texture);
      gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,gl.RGBA,gl.UNSIGNED_BYTE,createEarthTexture());
      gl.generateMipmap(gl.TEXTURE_2D);
      gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.LINEAR_MIPMAP_LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.REPEAT);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);
      this.uniforms=Object.fromEntries(['resolution','yaw','pitch','zoom','earth'].map(name=>[name,gl.getUniformLocation(program,name)]));
      gl.uniform1i(this.uniforms.earth,0);gl.clearColor(0,0,0,0);
      if(gl.getError()!==gl.NO_ERROR)throw new Error('WebGL 地表初始化失败');
    } finally {shaders.forEach(s=>gl.deleteShader(s));}
  }
  initCities() {
    const offsets={'纽约':[0,-25],'伦敦':[-23,-29],'上海':[13,-27],'香港':[-27,24],'东京':[25,-19],'新加坡':[-4,27],'法兰克福':[32,-6],'迪拜':[22,22]};
    FINANCIAL_CENTERS.forEach(([name,lat,lon])=>{
      const label=document.createElement('button');label.type='button';label.className='globe-city';label.textContent=name;
      label.setAttribute('aria-label',`金融中心 ${name}，定位并选择该地区事件`);
      label.addEventListener('click',()=>{this.setAutoRotate(false);this.focus(lat,lon);const event=this.events.find(e=>e.region===name);if(event)this.onSelect(event.event_id);},{signal:this.abort.signal});
      this.overlay.append(label);this.cityLabels.push({element:label,lat,lon,name,offset:offsets[name]});
    });
  }
  initControls() {
    const signal=this.abort.signal;
    this.canvas.addEventListener('pointerdown',event=>{
      if(event.button!==0||this.drag)return;
      this.setAutoRotate(false);this.focusTarget=null;this.drag={id:event.pointerId,x:event.clientX,y:event.clientY};
      this.canvas.setPointerCapture(event.pointerId);this.canvas.classList.add('dragging');
    },{signal});
    this.canvas.addEventListener('pointermove',event=>{
      if(!this.drag||event.pointerId!==this.drag.id)return;
      this.yaw+=(event.clientX-this.drag.x)*.006;this.pitch=clamp(this.pitch+(event.clientY-this.drag.y)*.006,-85*RAD,85*RAD);
      this.drag.x=event.clientX;this.drag.y=event.clientY;this.invalidate();
    },{signal});
    const finish=event=>{if(this.drag?.id===event.pointerId){this.drag=null;this.canvas.classList.remove('dragging');}};
    ['pointerup','pointercancel','lostpointercapture'].forEach(name=>this.canvas.addEventListener(name,finish,{signal}));
    this.stage.addEventListener('wheel',event=>{event.preventDefault();const delta=event.deltaY*(event.deltaMode===1?16:event.deltaMode===2?this.height:1);this.zoom=zoomFromDelta(this.zoom,delta);this.invalidate();},{passive:false,signal});
    this.canvas.addEventListener('keydown',event=>{
      if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','+','=','-','0'].includes(event.key))return;
      event.preventDefault();this.setAutoRotate(false);this.focusTarget=null;
      if(event.key==='ArrowLeft')this.yaw-=.12;if(event.key==='ArrowRight')this.yaw+=.12;
      if(event.key==='ArrowUp')this.pitch=clamp(this.pitch-.12,-85*RAD,85*RAD);if(event.key==='ArrowDown')this.pitch=clamp(this.pitch+.12,-85*RAD,85*RAD);
      if(event.key==='+'||event.key==='=')this.adjustZoom(-100);if(event.key==='-')this.adjustZoom(100);if(event.key==='0')this.zoom=1;
      this.invalidate();
    },{signal});
  }
  resize() {
    if(this.disposed)return;
    const box=this.container.getBoundingClientRect();this.width=Math.max(1,box.width);this.height=Math.max(1,box.height);
    const dpr=Math.min(devicePixelRatio||1,1.5);this.canvas.width=Math.round(this.width*dpr);this.canvas.height=Math.round(this.height*dpr);
    this.gl.viewport(0,0,this.canvas.width,this.canvas.height);this.neural?.resize(this.width,this.height,dpr);this.butterfly?.resize(this.width,this.height,dpr);this.invalidate();
  }
  setEvents(events,selectedId) {
    const oldFocus=document.activeElement?.dataset?.eventId;
    this.events=events;this.selectedId=selectedId;
    const ids=new Set(events.map(e=>e.event_id));
    for(const [id,button] of this.buttons)if(!ids.has(id)){button.remove();this.buttons.delete(id);}
    const groups=new Map();this.eventPositions.clear();
    events.forEach(e=>{const key=`${e.latitude},${e.longitude}`;if(!groups.has(key))groups.set(key,[]);groups.get(key).push(e);});
    groups.forEach(group=>group.forEach((e,i)=>this.eventPositions.set(e.event_id,{event:e,dx:(i-(group.length-1)/2)*17})));
    events.forEach(e=>{
      let button=this.buttons.get(e.event_id);
      if(!button){
        button=document.createElement('button');button.type='button';button.dataset.eventId=e.event_id;
        button.className=`globe-event sentiment-${e.sentiment}${this.motionQuery.matches?'':' event-arrival'}`;
        const core=document.createElement('span');core.className='globe-marker-core';button.append(core);
        button.addEventListener('animationend',()=>button.classList.remove('event-arrival'),{once:true});
        button.addEventListener('click',()=>this.onSelect(e.event_id));this.buttons.set(e.event_id,button);this.overlay.append(button);
      }
      button.setAttribute('aria-label',`DEMO ${e.region}：${e.title}`);button.title=`DEMO · ${e.region} · ${e.title}`;button.setAttribute('aria-pressed',String(e.event_id===selectedId));
      button.classList.remove('sentiment-negative','sentiment-neutral','sentiment-positive');button.classList.add(`sentiment-${e.sentiment}`);
    });
    this.cityLabels.forEach(({element,name})=>{element.disabled=!events.some(e=>e.region===name);element.title=element.disabled?`${name}：当前筛选下没有事件`:`${name}：选择当地事件`;});
    this.invalidate();
    if(oldFocus&&this.buttons.has(oldFocus))this.buttons.get(oldFocus).focus({preventScroll:true});
  }
  focus(latitude,longitude,animate=true) {
    const target=focusAngles(latitude,longitude,this.yaw);
    if(!animate||this.motionQuery.matches){this.yaw=target.yaw;this.pitch=target.pitch;this.focusTarget=null;}
    else this.focusTarget={...target,startYaw:this.yaw,startPitch:this.pitch,started:performance.now()};
    this.invalidate();
  }
  adjustZoom(delta){this.zoom=zoomFromDelta(this.zoom,delta);this.invalidate();}
  setAutoRotate(value){this.autoRotate=Boolean(value);this.onMotionChange?.(this.autoRotate);this.invalidate();}
  setNeuralEnabled(value){this.neural?.setEnabled(value);this.onMotionChange?.();this.invalidate();}
  setButterfly(network,step,selectedId,onSelect,controls={}){if(!this.butterfly)return;if(!network){this.butterfly.setEnabled(false);this.invalidate();return;}this.butterfly.setNetwork(network.nodes,network.edges,step,selectedId,onSelect);this.butterfly.setFilters(controls);if(controls.speed!==undefined)this.butterfly.setSpeed(controls.speed);this.butterfly.setEnabled(true);this.invalidate();}
  invalidate(){if(this.disposed)return;this.dirty=true;if(this.frame===null&&this.onScreen&&!document.hidden)this.frame=requestAnimationFrame(time=>this.tick(time));}
  stop(){if(this.frame!==null)cancelAnimationFrame(this.frame);this.frame=null;this.lastFrame=0;}
  tick(now) {
    this.frame=null;if(this.disposed||document.hidden||!this.onScreen)return;
    if(this.lastFrame&&now-this.lastFrame<1000/30){this.frame=requestAnimationFrame(time=>this.tick(time));return;}
    const delta=this.lastFrame?Math.min((now-this.lastFrame)/1000,.1):0;this.lastFrame=now;
    if(this.focusTarget){const t=clamp((now-this.focusTarget.started)/650,0,1),ease=1-(1-t)**3;this.yaw=this.focusTarget.startYaw+(this.focusTarget.yaw-this.focusTarget.startYaw)*ease;this.pitch=this.focusTarget.startPitch+(this.focusTarget.pitch-this.focusTarget.startPitch)*ease;if(t===1)this.focusTarget=null;this.dirty=true;}
    else if(this.autoRotate&&!this.drag){this.yaw+=delta*.12;this.dirty=true;}
    if(this.dirty){this.draw();this.dirty=false;}
    this.neural?.render({yaw:this.yaw,pitch:this.pitch,zoom:this.zoom,width:this.width,height:this.height},delta);
    this.butterfly?.render({yaw:this.yaw,pitch:this.pitch,zoom:this.zoom,width:this.width,height:this.height},delta);
    if(this.autoRotate||this.focusTarget||this.neural?.animated||this.butterfly?.animated)this.frame=requestAnimationFrame(time=>this.tick(time));else this.lastFrame=0;
  }
  draw() {
    const gl=this.gl;if(gl.isContextLost())return;
    gl.clear(gl.COLOR_BUFFER_BIT);gl.useProgram(this.resources.program);
    gl.uniform2f(this.uniforms.resolution,this.canvas.width,this.canvas.height);gl.uniform1f(this.uniforms.yaw,this.yaw);gl.uniform1f(this.uniforms.pitch,this.pitch);gl.uniform1f(this.uniforms.zoom,this.zoom);
    gl.drawArrays(gl.TRIANGLES,0,6);
    this.canvas.dataset.yaw=this.yaw.toFixed(5);this.canvas.dataset.pitch=this.pitch.toFixed(5);this.canvas.dataset.zoom=this.zoom.toFixed(3);
    this.canvas.dataset.frames=String(Number(this.canvas.dataset.frames||0)+1);
    const place=(el,lat,lon,dx=0,dy=0)=>{
      const point=projectGeo(lat,lon,this.yaw,this.pitch,this.width,this.height,this.zoom);
      el.hidden=!point.visible;el.style.transform=`translate(${(point.x+dx).toFixed(1)}px,${(point.y+dy).toFixed(1)}px) translate(-50%,-50%)`;
    };
    this.cityLabels.forEach(({element,lat,lon,offset})=>place(element,lat,lon,...offset));
    this.eventPositions.forEach(({event:e,dx},id)=>place(this.buttons.get(id),e.latitude,e.longitude,dx,0));
  }
  dispose() {
    if(this.disposed)return;this.disposed=true;this.stop();this.abort?.abort();
    this.resizeObserver?.disconnect();this.intersectionObserver?.disconnect();
    this.neural?.dispose();
    this.butterfly?.dispose();
    const gl=this.gl;
    if(gl){if(this.resources.texture)gl.deleteTexture(this.resources.texture);if(this.resources.buffer)gl.deleteBuffer(this.resources.buffer);if(this.resources.program)gl.deleteProgram(this.resources.program);}
    this.buttons.clear();this.eventPositions.clear();this.cityLabels=[];this.resources={};this.stage?.remove();
    // Detached canvases can otherwise retain a GPU context until nondeterministic GC.
    if(gl&&!gl.isContextLost())gl.getExtension('WEBGL_lose_context')?.loseContext();
  }
}
