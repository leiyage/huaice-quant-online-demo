/* Deterministic decorative motion; never controls prices, scores or orders. */
(function(root){
  'use strict';
  const anchors=[{x:297,y:313},{x:508,y:313},{x:719,y:313},{x:297,y:434},{x:508,y:434},{x:719,y:434}];
  function pose(index,seconds,enabled=true){
    const home=anchors[index];if(!enabled)return {...home,facing:0,walking:false,chat:null};
    const pair=index%3,side=index<3?-1:1,phase=((seconds+pair*9)%34+34)%34;
    const meeting={x:[388,593,714][pair]+side*23,y:[353,369,466][pair]};
    const neighbor={x:anchors[(index+1)%6].x+side*23,y:anchors[(index+1)%6].y+28};
    const visitor={x:[792,808,925][pair]+side*12,y:306+(side>0?22:0)};
    const curve=(a,b,t,bend=35)=>{const u=Math.max(0,Math.min(1,t)),s=u*u*(3-2*u),c={x:(a.x+b.x)/2+bend,y:Math.max(a.y,b.y)+28};
      return {x:(1-s)**2*a.x+2*(1-s)*s*c.x+s*s*b.x,y:(1-s)**2*a.y+2*(1-s)*s*c.y+s*s*b.y,facing:Math.sign(b.x-a.x),walking:true,chat:null};};
    if(phase<4)return curve(home,meeting,phase/4,side*25);
    if(phase<7)return {...meeting,facing:-side,walking:false,chat:index<3?'嗨！':'聊聊？',wave:true};
    if(phase<11)return curve(meeting,neighbor,(phase-7)/4,-side*30);
    if(phase<13)return {...neighbor,facing:side,walking:false,chat:'串个门',wave:true};
    if(phase<18)return curve(neighbor,visitor,(phase-13)/5,45);
    if(phase<21)return {...visitor,facing:Math.sign(880-visitor.x),walking:false,chat:index<3?'主管好！':'来啦！',wave:true,boss:true};
    if(phase<27)return curve(visitor,home,(phase-21)/6,-60);
    if(phase<31)return curve(home,meeting,(phase-27)/4,-side*20);
    return curve(meeting,home,(phase-31)/3,side*15);
  }
  function particle(start,end,progress){
    const t=((progress%1)+1)%1;
    return {x:start.x+(end.x-start.x)*t,y:start.y+(end.y-start.y)*t};
  }
  function spider(seconds,enabled=true){
    const t=enabled?seconds:0;
    return {x:438+Math.sin(t*.55)*29,y:285+Math.sin(t*.9)*9,
      legs:Array.from({length:8},(_,i)=>{const side=i<4?-1:1,k=i%4;
        const stride=Math.sin(t*3.4+k*1.2+(side>0?Math.PI:0));
        return {root:[side*9,(k-1.5)*5],knee:[side*(30+k*5),(k-1.5)*25+stride*8],
          foot:[side*(65+k*9)+stride*8,(k-1.5)*39-stride*7]};})};
  }
  function crawlRoute(points,seconds){
    if(!points.length)return {x:0,y:0};
    const phase=((seconds% (points.length*1.8))+points.length*1.8)%(points.length*1.8);
    const index=Math.floor(phase/1.8),t=Math.min(1,(phase/1.8-index)/.8),s=t*t*(3-2*t);
    const a=points[index],b=points[(index+1)%points.length];
    return {x:a.x+(b.x-a.x)*s,y:a.y+(b.y-a.y)*s};
  }
  // Arc-length sampled cubic path: no stop at every panel, no sharp segment corners.
  function buildCrawlerPath(points){
    if(points.length<2)return {samples:[{...(points[0]||{x:0,y:0}),distance:0}],length:0};
    const samples=[];let distance=0,previous=null;
    for(let i=0;i<points.length;i++){
      const p0=points[(i+points.length-1)%points.length],p1=points[i],p2=points[(i+1)%points.length],p3=points[(i+2)%points.length];
      const span=Math.hypot(p2.x-p1.x,p2.y-p1.y);
      const tangent=(a,b)=>{const d=Math.hypot(b.x-a.x,b.y-a.y)||1;return {x:(b.x-a.x)/d*span*.65,y:(b.y-a.y)/d*span*.65};};
      const m1=tangent(p0,p2),m2=tangent(p1,p3);
      for(let j=0;j<40;j++){
        const t=j/40,t2=t*t,t3=t2*t;
        const p={x:(2*t3-3*t2+1)*p1.x+(t3-2*t2+t)*m1.x+(-2*t3+3*t2)*p2.x+(t3-t2)*m2.x,
          y:(2*t3-3*t2+1)*p1.y+(t3-2*t2+t)*m1.y+(-2*t3+3*t2)*p2.y+(t3-t2)*m2.y};
        if(previous)distance+=Math.hypot(p.x-previous.x,p.y-previous.y);
        samples.push({...p,distance});previous=p;
      }
    }
    distance+=Math.hypot(samples[0].x-previous.x,samples[0].y-previous.y);
    samples.push({...samples[0],distance});return {samples,length:distance};
  }
  function sampleCrawlerPath(path,seconds,speed=90){
    if(!path.length)return {...path.samples[0],angle:0};
    const d=((seconds*speed)%path.length+path.length)%path.length;
    let lo=0,hi=path.samples.length-1;
    while(hi-lo>1){const mid=(lo+hi)>>1;if(path.samples[mid].distance<d)lo=mid;else hi=mid;}
    const a=path.samples[lo],b=path.samples[hi],t=(d-a.distance)/(b.distance-a.distance||1);
    return {x:a.x+(b.x-a.x)*t,y:a.y+(b.y-a.y)*t,angle:Math.atan2(b.y-a.y,b.x-a.x)+Math.PI/2};
  }
  function crawlerLocomotion(seconds,scale=1){
    const cycle=8.2,loops=Math.floor(seconds/cycle),t=seconds-loops*cycle;
    const integral=(start,duration)=>{const u=Math.max(0,Math.min(1,(t-start)/duration));return duration*(u/2-Math.sin(2*Math.PI*u)/(4*Math.PI));};
    const bump=(start,duration)=>{const u=(t-start)/duration;return u>0&&u<1?Math.sin(Math.PI*u)**2:0;};
    let mode='crawl',height=0,squash=1,landing=0,grab=null;
    if(t>=2.2&&t<2.38){mode='crouch';squash=1-.18*Math.sin((t-2.2)/.18*Math.PI/2);}
    else if(t>=2.38&&t<3.05){mode='jump';const u=(t-2.38)/.67;height=48*scale*4*u*(1-u);squash=1+.12*Math.sin(Math.PI*u);}
    else if(t>=3.05&&t<3.4){mode='land';landing=(t-3.05)/.35;squash=1-.2*Math.exp(-landing*4)*Math.cos(landing*Math.PI*3);}
    else if(t>=5&&t<6){mode='slide';squash=1-.13*bump(5,1);}
    const grabIndex=t>=.65&&t<1.5?0:t>=6.55&&t<7.4?1:-1;
    if(grabIndex>=0){mode='grab';grab={id:loops*2+grabIndex,progress:(t-(grabIndex?6.55:.65))/.85};}
    return {mode,height,squash,landing,grab,
      travelTime:seconds+loops*(.7*.67/2+1.5/2-.8*.85)+.7*integral(2.38,.67)+1.5*integral(5,1)-.8*(integral(.65,.85)+integral(6.55,.85)),
      speedFactor:1+.7*bump(2.38,.67)+1.5*bump(5,1)-.8*(bump(.65,.85)+bump(6.55,.85))};
  }
  // Decorative stamps only; no fetch, dispatch, DOM click or business-state writes.
  function createGrabEffects(){
    let active=null,lastId=null,marks=[];
    return {update(rig,time,scale,motion){
      marks=marks.filter(m=>time-m.born<3.2);
      if(!motion.grab){active=null;return {marks:marks.map(m=>({...m,age:time-m.born})),leg:null};}
      const grab=motion.grab,index=grab.id%2?4:0,side=index===0?-1:1;
      if(!active||active.id!==grab.id){
        const x=side*52*scale,y=-87*scale;
        active={id:grab.id,target:{x:rig.x+x*Math.cos(rig.angle)-y*Math.sin(rig.angle),y:rig.y+x*Math.sin(rig.angle)+y*Math.cos(rig.angle)}};
      }
      const u=grab.progress,target=active.target,base=rig.legs[index];
      const contact=u<.45?u/.45:u<.64?1:Math.max(0,(1-u)/.36),ease=contact*contact*(3-2*contact);
      const foot={x:base.foot.x+(target.x-base.foot.x)*ease,y:base.foot.y+(target.y-base.foot.y)*ease-7*scale*Math.sin(Math.PI*contact)};
      const dx=foot.x-base.root.x,dy=foot.y-base.root.y,d=Math.hypot(dx,dy)||1;
      const knee={x:base.root.x+dx*.5-dy/d*28*scale*side,y:base.root.y+dy*.5+dx/d*28*scale*side};
      if(u>=.48&&lastId!==grab.id){lastId=grab.id;marks.push({id:grab.id,x:target.x,y:target.y,born:time,scale});marks=marks.slice(-12);}
      return {leg:{index,root:base.root,knee,foot,planted:false},target,contact:u>=.45&&u<.64,marks:marks.map(m=>({...m,age:time-m.born}))};
    }};
  }
  function createCrawlerRig(){
    let previousTime=null,angle=null,feet=[],previousPosition=null;
    const world=(p,v)=>({x:p.x+v[0]*Math.cos(angle)-v[1]*Math.sin(angle),y:p.y+v[0]*Math.sin(angle)+v[1]*Math.cos(angle)});
    return {update(position,time,scale=1,motion={mode:'crawl'}){
      if(previousTime!==null&&time<previousTime){feet=[];angle=null;}
      const dt=previousTime===null?0:Math.min(.1,Math.max(0,time-previousTime));
      if(angle===null)angle=position.angle;
      const turn=Math.atan2(Math.sin(position.angle-angle),Math.cos(position.angle-angle));
      angle+=turn*(1-Math.exp(-dt*7));
      const velocity=previousPosition&&dt?{x:(position.x-previousPosition.x)/dt,y:(position.y-previousPosition.y)/dt}:{x:0,y:0};
      const speed=Math.min(180*scale,Math.hypot(velocity.x,velocity.y));
      const fast= Math.hypot(velocity.x,velocity.y)>125*scale;
      let swinging=feet.filter(f=>f.swing).length;
      const legs=Array(8);
      // Rotate scheduling priority so rear legs cannot be starved by front legs.
      for(let order=0;order<8;order++){
        const i=(order+Math.floor(time*8))%8;
        const side=i<4?-1:1,k=i%4;
        const root=world(position,[side*7*scale,(k-1.5)*7*scale]);
        const desired=world(position,[side*[66,89,86,64][k]*scale,[-70,-29,30,72][k]*scale]);
        if(!feet[i])feet[i]={x:desired.x,y:desired.y,last:-i*.08};
        // Flight tucks all legs; slide releases the floor contacts instead of stretching them.
        if(motion.mode==='jump'||motion.mode==='slide'){
          const tuck=motion.mode==='jump'?.58:.83;
          const air=world(position,[side*[66,89,86,64][k]*scale*tuck,[-70,-29,30,72][k]*scale*tuck]);
          feet[i]={x:air.x,y:air.y,last:time-.3,swing:null};
        }
        const f=feet[i],error=Math.hypot(desired.x-f.x,desired.y-f.y);
        if(!['jump','slide'].includes(motion.mode)&&!f.swing&&dt&&error>27*scale&&swinging<(fast?4:3)&&time-f.last>(fast?.1:.2)+(i%3)*.025){
          f.swing={start:time,from:{x:f.x,y:f.y},to:{x:desired.x+velocity.x*.09,y:desired.y+velocity.y*.09},duration:(fast?.14:.22)+(i%3)*.015};swinging++;
        }
        let lift=0;
        if(f.swing){const s=f.swing,t=Math.min(1,Math.max(0,(time-s.start)/s.duration)),e=t*t*(3-2*t);
          f.x=s.from.x+(s.to.x-s.from.x)*e;f.y=s.from.y+(s.to.y-s.from.y)*e;lift=Math.sin(t*Math.PI)*7*scale;
          if(t===1){f.swing=null;f.last=time;}
        }
        const dx=f.x-root.x,dy=f.y-root.y,d=Math.max(.001,Math.hypot(dx,dy)),reach=Math.min(d,119*scale);
        const bend=Math.sqrt(Math.max(0,(62*scale)**2-(reach/2)**2));
        const knee={x:root.x+dx*.5-dy/d*bend*side,y:root.y+dy*.5+dx/d*bend*side-lift};
        legs[i]={root,knee,foot:{x:f.x,y:f.y-lift},planted:!f.swing&&!['jump','slide'].includes(motion.mode)};
      }
      previousTime=time;previousPosition={x:position.x,y:position.y};
      return {x:position.x,y:position.y,angle,legs,bob:Math.sin(time*5)*.9*scale};
    }};
  }
  root.DeskMotion={pose,particle,spider,crawlRoute,buildCrawlerPath,sampleCrawlerPath,crawlerLocomotion,createCrawlerRig,createGrabEffects,anchors};
  if(typeof module!=='undefined'&&module.exports)module.exports=root.DeskMotion;
})(typeof window!=='undefined'?window:globalThis);
