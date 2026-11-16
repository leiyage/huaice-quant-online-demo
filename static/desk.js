(() => {
  'use strict';
  const $=id=>document.getElementById(id), text=(id,v)=>{$(id).textContent=v;};
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const number=(v,d=2)=>Number.isFinite(Number(v))?Number(v).toLocaleString('en-US',{maximumFractionDigits:d,minimumFractionDigits:d}):'—';
  let state=null,collection=null,radar=null,moving=!matchMedia('(prefers-reduced-motion: reduce)').matches,lastFrame=0,disposed=false;
  const palettes=['#ecedf2','#3295ea','#e66b80','#39ac88','#b687d9','#ebaf3e'];
  let hovered=-1;
  function meter(id,value){$(id).style.width=Math.max(0,Math.min(100,value))+'%';}
  async function request(url,body){
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);
    try{
      const response=await fetch(url,{signal:controller.signal,...(body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});
      const data=await response.json();if(!response.ok||data.error)throw new Error(data.error||'连接失败');return data;
    }finally{clearTimeout(timer);}
  }
  function prepare(id){const c=$(id),r=c.getBoundingClientRect(),dpr=Math.min(devicePixelRatio||1,2),width=Math.round(r.width*dpr),height=Math.round(r.height*dpr);if(c.width!==width)c.width=width;if(c.height!==height)c.height=height;const g=c.getContext('2d');g.setTransform(dpr,0,0,dpr,0,0);return {g,w:r.width,h:r.height};}
  function chart(id,bars){
    const {g,w,h}=prepare(id);g.clearRect(0,0,w,h);if(!w||!h)return;
    g.strokeStyle='#202332';g.lineWidth=.5;for(let i=1;i<5;i++){g.beginPath();g.moveTo(0,h*i/5);g.lineTo(w,h*i/5);g.stroke();}
    const data=(bars||[]).filter(b=>[b.o,b.h,b.l,b.c].every(v=>v!==null&&Number.isFinite(v))).slice(-85);
    if(!data.length){g.fillStyle='#717082';g.font='11px sans-serif';g.fillText('等待真实历史 K 线',15,h/2);return;}
    let min=Math.min(...data.map(b=>b.l)),max=Math.max(...data.map(b=>b.h));const range=max-min||Math.max(1,max*.01);min-=range*.07;max+=range*.07;
    const y=v=>h-16-(v-min)/(max-min)*(h-28),dx=(w-30)/data.length;
    data.forEach((b,i)=>{let x=10+(i+.5)*dx;g.fillStyle=g.strokeStyle=b.c>=b.o?'#63caa2':'#e7786a';g.beginPath();g.moveTo(x,y(b.h));g.lineTo(x,y(b.l));g.stroke();g.fillRect(x-Math.max(1,dx*.25),y(Math.max(b.c,b.o)),Math.max(2,dx*.5),Math.max(1,Math.abs(y(b.c)-y(b.o))));});
    g.fillStyle='#817989';g.font='8px Consolas';g.fillText(data[0].d||'',7,h-3);g.fillText(data.at(-1).d||'',Math.max(7,w-75),h-3);
  }
  function equity(){
    const {g,w,h}=prepare('equity');g.clearRect(0,0,w,h);const series=state?.series,values=series?.equity||[],benchmark=series?.benchmark||[];
    if(!values.length){g.fillStyle='#777582';g.font='12px sans-serif';g.fillText('等待模拟净值',20,h/2);return;}
    const min=Math.min(...values),max=Math.max(...values),range=max-min||Math.max(1,max*.02),bottom=h-19;
    const x=i=>15+i/Math.max(1,values.length-1)*(w-28),y=v=>bottom-(v-min)/range*(h-36);
    g.strokeStyle='#292230';g.lineWidth=.5;for(let i=1;i<4;i++){g.beginPath();g.moveTo(0,h*i/4);g.lineTo(w,h*i/4);g.stroke();}
    function line(v,color){g.strokeStyle=color;g.lineWidth=1.7;g.beginPath();v.forEach((n,i)=>i?g.lineTo(x(i),y(n)):g.moveTo(x(i),y(n)));g.stroke();}
    const gradient=g.createLinearGradient(0,0,0,bottom);gradient.addColorStop(0,'#439a705a');gradient.addColorStop(1,'#439a7000');
    g.beginPath();g.moveTo(x(0),bottom);values.forEach((n,i)=>g.lineTo(x(i),y(n)));g.lineTo(x(values.length-1),bottom);g.closePath();g.fillStyle=gradient;g.fill();line(values,'#70d6ad');
    const n=Math.min(100,values.length),stride=Math.max(1,Math.floor(values.length/n));for(let i=0;i<n;i++){const index=Math.min(values.length-1,i*stride),change=index?values[index]-values[index-1]:0;g.fillStyle=change>=0?'#477d65':'#88555b';g.fillRect(x(index),h-12,Math.max(2,w/n*.5),Math.min(11,2+Math.abs(change)/Math.max(1,range)*100));}
    g.font='8px Consolas';g.fillStyle='#899489';g.fillText('VIRTUAL EQUITY / HISTORICAL REPLAY',12,12);
  }
  function render(){
    if(!state)return;const m=state.market,a=state.account,latest=collection?.tasks?.[0];
    text('chiefValue',(state.assets?.[0]||'—')+' · '+m.signal);text('chiefNote',m.date+' / '+(state.running?'回放中':'暂停或就绪'));
    text('sizeValue',number(a.position_value)+' '+state.currency);text('sizeNote','模拟仓位 '+number(a.exposure_pct,1)+'%');
    text('fillsValue',state.metrics.trades+' 笔');text('fillsNote','累计模拟手续费 '+number(a.fees_paid));
    text('riskValue','DD '+number(a.drawdown_pct,2)+'%');text('riskNote',a.paused?'风控暂停':'历史模拟 · 无真实订单');
    meter('chiefMeter',m.target_weight*100);meter('sizeMeter',a.exposure_pct);meter('fillsMeter',m.progress);meter('riskMeter',Math.abs(a.drawdown_pct));
    $('progress').value=m.progress;text('progressText',number(m.progress,1)+'%');text('marketTitle',(state.assets?.[0]||'')+' / HISTORICAL K-LINE');text('marketDate',m.date);
    text('ticker','PRICE '+number(m.price)+' '+state.currency+' / '+state.strategy);text('balanceMeta','回放日 '+m.date+' · 历史行情 / 非实时');
    text('balanceValue',number(a.equity)+' '+state.currency);text('balanceReturn','模拟收益 '+number(state.metrics.total_return*100,2)+'%');
    $('assetCoins').innerHTML=(state.assets||[]).slice(0,6).map(s=>'<a class="coin" href="/#replay">'+esc(s)+'</a>').join('');
    $('trades').innerHTML=(state.trades||[]).slice(0,5).map(t=>'<div class="trade-row"><span>'+esc(t.date)+'</span><span>'+esc(t.symbol)+'</span><span>'+esc(t.action)+'</span><span>'+number(t.price)+'</span></div>').join('')||'回放区间内尚无模拟成交';
    text('params','// ACTUAL APPLIED PARAMETERS\n// '+state.strategy+'\n\n'+JSON.stringify(state.params,null,2));
    if(collection){
      text('scanValue',collection.total+' 条入库');text('scanNote',latest?'最近任务：'+latest.status:'尚未采集');text('vetValue',collection.rejected_total+' 条隔离');
      text('crawlerValue',collection.sources.length+' 个来源');text('crawlerNote',collection.running?'采集中':latest?'最近：'+latest.status:'尚无任务');
      meter('scanMeter',latest?.counts?.received?latest.counts.stored/latest.counts.received*100:0);meter('vetMeter',latest?.counts?.received?latest.counts.cleaned/latest.counts.received*100:0);meter('crawlerMeter',collection.sources.length?collection.sources.filter(s=>s.enabled).length/collection.sources.length*100:0);
      text('roomMessage',collection.messages?.[0]?.text||'尚无真实消息，点击采集工位添加来源。');
      $('messages').innerHTML=(collection.messages||[]).slice(0,20).map(m=>'<div class="message"><small>'+esc(m.asset)+' / '+esc(m.source_name)+' / 首次收到 '+esc(m.captured)+'</small>'+esc(m.text)+'</div>').join('')||'共享库没有消息，不用教学样本替代。';
    }
    if(radar){const snap=radar.snapshot||[];text('socialValue',snap.length?'z '+number(snap[0].z,3):'无可得消息');text('socialNote',snap.length?snap[0].asset+' · '+radar.visible_messages+' 条可得':'当前时点为空');meter('socialMeter',snap[0]?.coverage*100||0);}
    chart('candles',state.ohlc);chart('analysisChart',state.ohlc);equity();
  }
  function scene(time){
    const {g,w,h}=prepare('roomCanvas');if(!w||!h)return;g.clearRect(0,0,w,h);g.scale(w/1000,h/560);
    const phase=time/1000;
    const rect=(x,y,a,b,c)=>{g.fillStyle=c;g.fillRect(x,y,a,b);};
    function line(points,color,width=1){g.beginPath();points.forEach(([x,y],i)=>i?g.lineTo(x,y):g.moveTo(x,y));g.strokeStyle=color;g.lineWidth=width;g.stroke();}
    function poly(points,fill,stroke){g.beginPath();points.forEach(([x,y],i)=>i?g.lineTo(x,y):g.moveTo(x,y));g.closePath();g.fillStyle=fill;g.fill();if(stroke){g.strokeStyle=stroke;g.stroke();}}
    function label(s,x,y,size=7,color='#7e8192'){g.fillStyle=color;g.font=size+'px Consolas';g.textAlign='center';g.fillText(s,x,y);g.textAlign='left';}
    function tinyPlot(x,y,width,height,color,index=0){const v=state?.series?.equity||[];if(v.length<2)return;const sample=v.slice(-35),min=Math.min(...sample),range=Math.max(...sample)-min||1;line(sample.map((n,i)=>[x+i/(sample.length-1)*width,y+height-(n-min)/range*height]),color,1);}
    function historyPlot(x,y,width,height){
      const bars=(state?.ohlc||[]).filter(b=>[b.o,b.h,b.l,b.c].every(v=>v!==null&&Number.isFinite(v))).slice(-26);
      rect(x,y,width,height,'#070f18');
      for(let row=1;row<5;row++)line([[x,y+height*row/5],[x+width,y+height*row/5]],'#24323f',.5);
      if(!bars.length){label('WAITING FOR HISTORY',x+width/2,y+height/2,6);return;}
      const low=Math.min(...bars.map(b=>b.l)),high=Math.max(...bars.map(b=>b.h)),range=high-low||Math.max(1,high*.01);
      const py=v=>y+height-5-(v-low)/range*(height-10),step=width/bars.length;
      bars.forEach((b,i)=>{const cx=x+(i+.5)*step,color=b.c>=b.o?'#6bd5b0':'#f18d95';line([[cx,py(b.h)],[cx,py(b.l)]],color,.8);rect(cx-step*.28,py(Math.max(b.o,b.c)),Math.max(1,step*.56),Math.max(1,Math.abs(py(b.o)-py(b.c))),color);});
      const close=bars.at(-1).c;g.save();g.setLineDash([2,3]);line([[x,py(close)],[x+width,py(close)]],'#d9c299',.5);g.restore();
    }
    function screen(x,y,width=110,height=50,index=0){rect(x-4,y-4,width+8,height+8,'#222635');rect(x,y,width,height,'#080d16');line([[x,y],[x+width,y],[x+width,y+height]],'#364151');rect(x+width*.4,y+height+5,width*.2,7,'#20242d');tinyPlot(x+7,y+9,width-14,height-18,palettes[index%6],index);label(['RESEARCH','SENTIMENT','EXPERIMENT','PERFORMANCE','PAPER FILLS','RISK CHECK'][index%6],x+width/2,y+height-4,5);}
    function desk(x,y,index){
      g.save();g.shadowColor='#000b';g.shadowBlur=18;g.shadowOffsetY=9;
      const surface=g.createLinearGradient(x,y-10,x,y+22);surface.addColorStop(0,'#323c4c');surface.addColorStop(1,'#151c2a');
      poly([[x,y],[x+133,y],[x+147,y+20],[x-12,y+20]],surface,hovered===index?palettes[index]:'#485568');g.restore();
      poly([[x-12,y+20],[x+147,y+20],[x+147,y+30],[x-12,y+30]],'#0d121b','#303d4b');rect(x+2,y+27,5,53,'#323849');rect(x+129,y+27,5,53,'#252a36');rect(x+4,y+30,34,43,'#10131b');line([[x+8,y+40],[x+33,y+40],[x+33,y+48]],'#303741');screen(x+14,y-63,104,55,index);poly([[x+56,y+11],[x+106,y+11],[x+112,y+15],[x+58,y+15]],'#444955');
      for(let row=0;row<2;row++)for(let key=0;key<10;key++)rect(x+60+key*4,y+12+row*2,2,1,'#8492a0');
      rect(x+115,y+13,7,2,'#577066');rect(x+51,y+36,34,34,'#171b26');rect(x+57,y+65,24,13,'#161a23');rect(x+65,y+78,5,12,'#282c3a');line([[x+51,y+91],[x+80,y+91]],'#343748');
      // Cup, mouse pad, small notepad and cable are purely decorative.
      poly([[x+18,y+7],[x+38,y+7],[x+43,y+13],[x+23,y+13]],'#667587','#9ca8b4');
      line([[x+23,y+9],[x+36,y+9]],'#b7c4cf',.5);
      rect(x+119,y+6,13,8,'#111a25');g.fillStyle='#8ba0b7';g.beginPath();g.ellipse(x+125,y+9,3,2,0,0,7);g.fill();
      rect(x+2,y-7,7,11,'#7c8f9d');g.strokeStyle='#9eabb4';g.lineWidth=1;g.strokeRect(x+8,y-4,3,5);g.fillStyle='#1f181a';g.beginPath();g.ellipse(x+5.5,y-7,3.5,1.6,0,0,7);g.fill();
      line([[x+19,y+31],[x+23,y+62],[x+41,y+70]],'#212e3e',1.2);
      line([[x+20,y+29],[x+116,y+29]],hovered===index?palettes[index]:'#294353',2);
      if(hovered===index){g.save();g.globalAlpha=.12;g.fillStyle=palettes[index];g.fillRect(x-10,y-68,154,100);g.restore();}
    }
    function plant(x,y,scale=1){g.save();g.translate(x,y);g.scale(scale,scale);poly([[-12,0],[13,0],[9,24],[-8,24]],'#252b2c','#59605d');for(let i=0;i<7;i++){const a=i*.85;g.beginPath();g.ellipse(Math.cos(a)*10,-15-i%3*5,4,15,a,0,Math.PI*2);g.fillStyle=i%2?'#476f39':'#284e33';g.fill();}line([[0,0],[0,-31]],'#667b43');g.restore();}
    function ghost(x,y,color,index,facing=0,walking=false,wave=false){
      const bounce=Math.sin(phase*(walking?11:2)+index)*(walking?2.8:1.5);
      g.save();g.translate(x,y);g.fillStyle='#0009';g.beginPath();g.ellipse(0,20,17,5,0,0,Math.PI*2);g.fill();g.translate(0,bounce);
      g.shadowColor=color+'55';g.shadowBlur=10;
      const material=g.createRadialGradient(-5,-8,1,3,4,23);material.addColorStop(0,'#ffffff');material.addColorStop(.28,color);material.addColorStop(1,color+'88');g.fillStyle=material;
      g.beginPath();g.moveTo(-13,12);g.bezierCurveTo(-20,-24,22,-24,15,12);g.lineTo(9,17);g.lineTo(2,12);g.lineTo(-6,17);g.closePath();g.fill();g.shadowBlur=0;
      g.fillStyle='#141b2a';g.beginPath();g.ellipse(-4+facing*2,-2,2.7,5,-.2,0,7);g.ellipse(5+facing*2,-1,2.7,5,-.2,0,7);g.fill();
      g.fillStyle='#dddff466';g.beginPath();g.ellipse(-8,-8,2,5,-.5,0,7);g.fill();
      if(wave){line([[12,6],[20,Math.sin(phase*7+index)*5-3]],color,3);g.fillStyle=color;g.beginPath();g.arc(20,Math.sin(phase*7+index)*5-3,2.2,0,7);g.fill();}
      if(walking){rect(-10,16+Math.sin(phase*8)*2,6,3,color);rect(5,16-Math.sin(phase*8)*2,6,3,color);}
      g.restore();
    }
    const ambient=g.createRadialGradient(495,240,5,495,300,420);ambient.addColorStop(0,'#17182c60');ambient.addColorStop(1,'#070a1400');rect(0,0,1000,560,ambient);
    if(moving){
      const hub={x:500,y:205},targets=[{x:310,y:231},{x:520,y:231},{x:730,y:231},{x:310,y:351},{x:520,y:351},{x:730,y:351}];
      targets.forEach((point,index)=>{
        g.save();g.globalAlpha=hovered===index?.55:.12;line([[hub.x,hub.y],[point.x,point.y]],palettes[index]);
        const p=window.DeskMotion.particle(hub,point,phase*.22+index/6);g.globalAlpha=.65;g.shadowColor=palettes[index];g.shadowBlur=8;rect(p.x-1,p.y-1,2,2,palettes[index]);g.restore();
      });
      g.save();g.strokeStyle='#55bcbc77';g.lineWidth=.7;g.beginPath();g.arc(hub.x,hub.y,5+Math.sin(phase)*2,0,7);g.stroke();g.restore();
    }
    // Architectural displays and an oblique wall screen.
    poly([[0,209],[78,134],[78,320],[0,395]],'#182231','#536579');
    poly([[2,214],[74,145],[74,316],[2,385]],'#080f19','#394b62');
    g.save();g.transform(.33,-.33,0,1,4,223);
    label((state?.assets?.[0]||'—')+' / HISTORY',100,1,9,'#bdddcf');historyPlot(0,10,200,117);
    label(state?.market?.date||'WAITING',100,139,8,'#96bfb1');label('HISTORICAL / NO ORDERS',100,150,6,'#b38eaa');g.restore();
    line([[72,324],[72,352],[52,372]],'#445267',3);poly([[19,389],[69,342],[86,349],[35,402]],'#171f2e','#4b5a70');
    poly([[191,70],[213,37],[235,70],[235,204],[191,204]],'#0b0f18','#414255');label('HC',213,146,24,'#ecedf6');
    poly([[724,122],[746,91],[768,122],[768,290],[724,290]],'#0b0f18','#414255');label('HC',746,207,24,'#eceaf4');
    const cast=palettes.map((color,i)=>({color,index:i,...window.DeskMotion.pose(i,phase)}));
    const visitors=cast.filter(p=>p.boss);
    function speech(p,s){const x=p.x+(p.index<3?-26:26),y=p.y-39;g.save();g.fillStyle='#182232ee';g.strokeStyle=p.color||'#eae9f0';g.lineWidth=.8;g.beginPath();g.roundRect(x-29,y-10,58,19,5);g.fill();g.stroke();line([[x-4,y+9],[p.x,p.y-19],[x+4,y+9]],p.color||'#eae9f0');label(s,x,y+3,8,'#f3eaf4');g.restore();}
    rect(827,281,120,17,'#19212c');rect(833,297,6,21,'#2c3441');rect(932,297,6,21,'#2c3441');ghost(880,269,'#ede9f4',6,visitors.length?-1:0,false,visitors.length>0);label('HEAD OF THE ROOM',886,303,6);
    if(visitors.length)speech({x:880,y:269,index:6,color:'#ede9f4'},'欢迎～');
    desk(245,290,0);desk(456,290,1);desk(667,290,2);desk(245,410,3);desk(456,410,4);desk(667,410,5);
    cast.sort((a,b)=>a.y-b.y).forEach(p=>ghost(p.x,p.y,p.color,p.index,p.facing,p.walking,p.wave));
    cast.filter(p=>p.chat).forEach(p=>speech(p,p.chat));
    // Sofa and floor plants.
    rect(430,487,164,35,'#202434');rect(419,478,174,21,'#29303e');for(let i=0;i<4;i++){rect(425+i*42,481,37,16,'#151e2a');rect(435+i*40,503,33,17,'#151b28');}rect(416,495,14,32,'#262d3b');rect(588,495,14,32,'#262d3b');rect(427,527,7,8,'#222738');rect(580,527,7,8,'#222738');
    plant(134,194,1.2);plant(186,433,.8);plant(389,505,1);plant(636,505,1);plant(957,275,.7);plant(507,531,.65);
    // Radar dish and rocket plinth, matching the reference's right-side landmarks.
    poly([[802,421],[884,421],[895,445],[796,445]],'#171e2b','#3c4454');label('SOURCE RADAR',845,437,7);line([[843,413],[849,384]],'#808994',3);g.save();g.translate(847,378);g.rotate(-.5+(moving?Math.sin(phase*.4)*.1:0));g.strokeStyle='#626e82';g.lineWidth=2;g.beginPath();g.ellipse(0,0,26,20,0,0,7);g.stroke();for(let i=-2;i<=2;i++)line([[-23,i*6],[23,i*6]],'#384859');line([[0,-18],[0,19]],'#839297');g.restore();
    rect(812,353,70,13,'#171f2b');label('RESEARCH / NO ORDERS',847,362,5);g.fillStyle='#d8d4e2';g.beginPath();g.moveTo(846,310);g.bezierCurveTo(831,332,833,346,846,349);g.bezierCurveTo(859,346,862,332,846,310);g.fill();g.fillStyle='#23303d';g.beginPath();g.arc(846,331,4,0,7);g.fill();poly([[835,338],[828,350],[838,347]],'#8692aa');poly([[857,338],[865,350],[854,347]],'#8692aa');
    // Server rack and left cabinet.
    rect(929,354,43,156,'#171d2a');rect(935,361,29,137,'#090f18');for(let i=0;i<10;i++){rect(938,367+i*12,23,9,'#1c2531');rect(942,370+i*12,2,2,i%3?'#456f4d':'#779b5a');}
    // Left display desk with bevels, drawers, keyboard, cable and a floor wastebasket.
    poly([[83,346],[158,346],[169,360],[73,360]],'#303c4f','#59677b');poly([[73,360],[169,360],[169,367],[73,367]],'#141c29','#344252');
    rect(79,365,6,36,'#394457');rect(156,365,6,36,'#27364b');rect(88,366,31,30,'#111925');
    for(let i=0;i<2;i++){rect(91,370+i*12,24,9,'#243042');rect(99,373+i*12,8,1,'#7d8fa3');}
    screen(89,306,61,36,4);historyPlot(92,310,55,23);label('HISTORICAL K-LINE',124,403,5,'#8eafa4');
    poly([[113,350],[145,350],[151,356],[118,356]],'#53657a','#7d8da1');for(let i=0;i<7;i++)rect(119+i*4,352,2,1,'#b2c2d3');
    rect(84,341,6,10,'#7390a2');line([[161,366],[167,385],[143,397]],'#1d2c3c',1);
    g.save();g.translate(104,455);g.fillStyle='#0009';g.beginPath();g.ellipse(0,33,21,5,0,0,7);g.fill();
    poly([[-17,-2],[17,-2],[13,31],[-13,31]],'#253242','#6a7a8f');
    for(let i=0;i<5;i++)line([[-12+i*6,2],[-10+i*5,28]],'#8392a666',.6);
    for(let i=1;i<5;i++)line([[-14,i*6],[14,i*6]],'#8190a655',.6);
    g.fillStyle='#c9ced4';for(let i=0;i<3;i++){g.beginPath();g.arc(-8+i*7,-3-(i%2)*3,4,0,7);g.fill();}
    g.save();g.translate(-17,-6);g.rotate(-.07-.07*Math.sin(phase*.9));poly([[0,-3],[34,-3],[37,2],[0,2]],'#44536a','#8b9eb2');rect(12,-6,11,3,'#9aabb8');g.restore();
    label('PAPER ONLY',0,43,5,'#8999ad');g.restore();
    if(collection?.running){const t=(phase%2)/2;g.strokeStyle='#58cdd3';g.lineWidth=1;g.beginPath();g.arc(847,378,30+t*15,-.5,1);g.stroke();}
    if(state?.running){line([[390,237],[456,228],[570,237]],'#32968d');}
  }
  async function pollState(){if(disposed)return;try{state=await request('/api/state');text('connection','本机数据已连接 / '+(state.running?'历史回放中':'就绪'));$('connection').classList.remove('error');text('updateTime','状态同步 '+new Date().toLocaleTimeString('zh-CN'));render();}catch(e){text('connection','状态连接失败：'+e.message);$('connection').classList.add('error');}finally{if(!disposed)setTimeout(pollState,1200);}}
  async function pollResearch(){if(disposed)return;const results=await Promise.allSettled([request('/api/collection'),request('/api/sentiment?source=collection')]);if(results[0].status==='fulfilled')collection=results[0].value;else{text('crawlerNote','共享库读取失败');text('scanNote','数据读取失败');}if(results[1].status==='fulfilled')radar=results[1].value;else text('socialNote','雷达读取失败');render();if(!disposed)setTimeout(pollResearch,5000);}
  // One pointer-transparent layer crosses real DOM panels; never changes their data.
  const crawlerRig=window.DeskMotion.createCrawlerRig();
  const crawlerGrab=window.DeskMotion.createGrabEffects();
  let crawlerPath=null,crawlerLayout='';
  function crawler(seconds){
    const {g,w,h}=prepare('crawlerOverlay');g.clearRect(0,0,w,h);
    const bounds=$('crawlerOverlay').getBoundingClientRect();
    const point=(el,fx,fy)=>{const r=el.getBoundingClientRect();return {x:r.left-bounds.left+r.width*fx,y:r.top-bounds.top+r.height*fy};};
    const cards=Array.from(document.querySelectorAll('.agent'));
    const room=$('roomCanvas'),bottom=document.querySelector('.parameters');
    const messageArea=$('messages').hidden?$('params'):$('messages');
    const points=[point(cards[6],.3,.75),point(room,.5,.18),point(room,.35,.5),point(room,.09,.32),
      point(room,.25,.64),point(room,.43,.24),point(messageArea,.36,.36),point(messageArea,.73,.68),
      point(room,.75,.19),point(room,.85,.46),point(room,.45,.58),point(messageArea,.22,.8),
      ...[0,4,1,5,2,6,3,7].map(i=>point(cards[i],.5,.58)),point(room,.6,.22)];
    const layout=points.map(p=>Math.round(p.x)+','+Math.round(p.y)).join(';');
    if(layout!==crawlerLayout){crawlerLayout=layout;crawlerPath=window.DeskMotion.buildCrawlerPath(points);}
    const scale=Math.max(.4,Math.min(.95,w/1150));
    const motion=window.DeskMotion.crawlerLocomotion(seconds,scale);
    const p=window.DeskMotion.sampleCrawlerPath(crawlerPath,motion.travelTime,230*scale);
    const rig=crawlerRig.update(p,seconds,scale,motion);
    const grab=crawlerGrab.update(rig,seconds,scale,motion);
    if(grab.leg)rig.legs[grab.leg.index]=grab.leg;
    // Every decorative contact leaves one expanding/fading rectangular screen imprint.
    g.save();
    grab.marks.forEach(mark=>{
      const s=mark.scale,pop=Math.min(1,mark.age/.16),fade=Math.max(0,1-mark.age/3.2);
      const width=(12+24*(1-(1-pop)**3))*s,height=(6+12*pop)*s;
      g.globalAlpha=fade;g.shadowColor=mark.id%2?'#65edcf':'#ee72b6';g.shadowBlur=(8+5*(1-pop))*s;
      g.fillStyle=mark.id%2?'#6cebcf22':'#ef72b62b';g.fillRect(mark.x-width/2,mark.y-height/2,width,height);
      g.strokeStyle=mark.id%2?'#75e6d3':'#ee7dba';g.lineWidth=1.1*s;g.strokeRect(mark.x-width/2,mark.y-height/2,width,height);
      g.shadowBlur=0;g.strokeStyle='#c6fff0';
      const corner=4*s;for(const [x,y,dx,dy] of [[-1,-1,1,1],[1,-1,-1,1],[-1,1,1,-1],[1,1,-1,-1]]){
        const cx=mark.x+x*width/2,cy=mark.y+y*height/2;
        g.beginPath();g.moveTo(cx+dx*corner,cy);g.lineTo(cx,cy);g.lineTo(cx,cy+dy*corner);g.stroke();
      }
      if(mark.age<.45){g.globalAlpha=fade*(1-mark.age/.45);const spread=(5+mark.age*33)*s;
        for(let i=0;i<4;i++){const a=i*Math.PI/2+mark.id;g.fillStyle='#aefff0';g.fillRect(mark.x+Math.cos(a)*spread,mark.y+Math.sin(a)*spread,1.8*s,1.8*s);}}
    });g.restore();
    // Ground shadow and a small landing pulse make the flight arc readable.
    g.save();g.fillStyle=`rgba(60,190,170,${.1-motion.height/(48*scale)*.06})`;
    g.beginPath();g.ellipse(p.x,p.y+15*scale,(21+motion.height*.12)*scale,5*scale,0,0,Math.PI*2);g.fill();
    if(motion.mode==='land'){g.strokeStyle=`rgba(113,229,211,${.35*(1-motion.landing)})`;g.lineWidth=1;
      g.beginPath();g.ellipse(p.x,p.y+14*scale,(20+motion.landing*36)*scale,(5+motion.landing*9)*scale,0,0,Math.PI*2);g.stroke();}
    if(motion.mode==='slide'){
      for(let i=1;i<=4;i++){const old=window.DeskMotion.crawlerLocomotion(Math.max(0,seconds-i*.045),scale);
        const q=window.DeskMotion.sampleCrawlerPath(crawlerPath,old.travelTime,230*scale);
        g.strokeStyle=`rgba(101,224,214,${.22*(1-i/5)})`;g.lineWidth=1.2;
        g.beginPath();g.moveTo(q.x-7*scale,q.y);g.lineTo(q.x+7*scale,q.y);g.stroke();}
    }g.restore();
    g.save();g.lineCap='round';g.lineJoin='round';g.shadowColor='#68dbc8';g.shadowBlur=3;
    g.translate(0,-motion.height);
    if(grab.contact){g.save();g.strokeStyle='#b5ffeb';g.lineWidth=1.2*scale;g.shadowBlur=9;
      g.beginPath();g.arc(grab.target.x,grab.target.y,4.5*scale,0,Math.PI*2);g.stroke();g.restore();}
    rig.legs.forEach((leg,i)=>{
      g.beginPath();g.moveTo(leg.root.x,leg.root.y);g.lineTo(leg.knee.x,leg.knee.y);g.lineTo(leg.foot.x,leg.foot.y);
      g.strokeStyle=i%2?'#db6babbb':'#6fd8c4bb';g.lineWidth=1.05*scale;g.stroke();
      [leg.knee,leg.foot].forEach((q,j)=>{g.fillStyle=j?'#ed72b9':'#76e2cc';g.beginPath();g.arc(q.x,q.y,(j?1.8:1.3)*scale,0,Math.PI*2);g.fill();});
    });
    // Small articulated wireframe thorax, not a cartoon ghost with eyes.
    g.translate(rig.x,rig.y+rig.bob);g.rotate(rig.angle);g.scale(scale/motion.squash,scale*motion.squash);
    g.strokeStyle='#88e6d7cc';g.lineWidth=.85;g.beginPath();g.moveTo(0,-17);g.lineTo(8,-3);g.lineTo(6,12);g.lineTo(0,23);g.lineTo(-6,12);g.lineTo(-8,-3);g.closePath();
    g.moveTo(0,-17);g.lineTo(0,23);g.moveTo(-8,-3);g.lineTo(6,12);g.moveTo(8,-3);g.lineTo(-6,12);g.stroke();
    g.fillStyle='#9b9ee9';g.beginPath();g.arc(0,-5,2.7,0,Math.PI*2);g.fill();g.restore();
  }
  let crawlerSeconds=0,crawlerLast=null;
  function tick(time){if(disposed)return;if(crawlerLast!==null&&moving)crawlerSeconds+=Math.min((time-crawlerLast)/1000,.1);crawlerLast=time;if(time-lastFrame>30){lastFrame=time;scene(crawlerSeconds*1000);crawler(crawlerSeconds);}const clock=new Date().toISOString().slice(11,19);text('clock',clock+' UTC');text('roomClock',clock.slice(0,5));requestAnimationFrame(tick);}
  async function control(command,button){button.disabled=true;try{state=await request('/api/control',{command});render();}catch(e){text('connection','操作失败：'+e.message);}finally{button.disabled=false;}}
  $('play').onclick=()=>control('start',$('play'));$('pause').onclick=()=>control('pause',$('pause'));$('step').onclick=()=>control('step',$('step'));
  $('end').onclick=()=>control('fastforward',$('end'));
  function setMotion(){text('motion','角色动画：'+(moving?'开':'关'));$('motion').setAttribute('aria-pressed',String(moving));}
  $('motion').onclick=()=>{moving=!moving;setMotion();};setMotion();
  function panel(show){$('messages').hidden=!show;$('params').hidden=show;$('showMessages').classList.toggle('active',show);$('showParams').classList.toggle('active',!show);}
  $('showMessages').onclick=()=>panel(true);$('showParams').onclick=()=>panel(false);
  document.querySelectorAll('.room-hotspots a').forEach((link,index)=>{
    const enter=()=>{hovered=index;text('workstationHint',link.textContent+' · 点击进入详细模块；角色与数据流为视觉装饰');};
    const leave=()=>{hovered=-1;text('workstationHint','角色与数据流为视觉装饰 · 工位小屏为同一净值可视化 · 点击进入模块');};
    link.addEventListener('pointerenter',enter);link.addEventListener('focus',enter);link.addEventListener('pointerleave',leave);link.addEventListener('blur',leave);
  });
  window.addEventListener('resize',render);window.addEventListener('pagehide',()=>{disposed=true;});
  window.addEventListener('pageshow',event=>{if(event.persisted){disposed=false;pollState();pollResearch();requestAnimationFrame(tick);}});
  pollState();pollResearch();requestAnimationFrame(tick);
})();
