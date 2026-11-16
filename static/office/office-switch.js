/** Keep the original research document alive while switching visual modes. */
const officeURL='/static/office/index.html';
const modeURLs={office:officeURL,terminal:'/',bank:'/bank'};
const officePage=location.pathname===officeURL;
if(!officePage){
  const anchor=document.querySelector('[data-mode-switch="bank"], [data-mode-switch="terminal"]');
  if(anchor&&!document.querySelector('[data-mode-switch="office"]')){
    const link=document.createElement('a');link.href=officeURL;link.dataset.modeSwitch='office';link.textContent='OFFICE MODE ↗';
    link.style.cssText='color:#8bd3bd;font-size:11px;white-space:nowrap';anchor.after(link);
  }
}
let overlay=null,frames={},previousOverflow='';
function close(){if(!overlay)return;overlay.hidden=true;document.body.style.overflow=previousOverflow;}
function show(mode){
  if((mode==='terminal'&&['/','/index.html'].includes(location.pathname))||(mode==='bank'&&location.pathname.startsWith('/bank'))){close();return;}
  if(!overlay){
    overlay=document.createElement('div');overlay.setAttribute('role','dialog');overlay.setAttribute('aria-label','可切换金融研究工作空间');
    overlay.style.cssText='position:fixed;inset:0;z-index:2147482000;background:#07101a';
    const back=document.createElement('button');back.textContent='返回原页面 ×';back.style.cssText='position:absolute;bottom:12px;right:18px;z-index:2;background:#122a37;color:#bdd9d7;border:1px solid #527c83;border-radius:4px;padding:7px 12px;font:12px sans-serif;cursor:pointer';back.addEventListener('click',close);overlay.append(back);document.body.append(overlay);
  }
  if(!frames[mode]){const frame=document.createElement('iframe');frame.title=mode.toUpperCase()+' MODE';frame.src=modeURLs[mode];frame.style.cssText='width:100%;height:100%;border:0;display:block';frame.allow='fullscreen';overlay.prepend(frame);frames[mode]=frame;}
  else if(mode==='office')frames.office.src=officeURL;
  for(const [key,frame]of Object.entries(frames))frame.style.display=key===mode?'block':'none';
  if(overlay.hidden||!document.body.style.overflow)previousOverflow=document.body.style.overflow;
  overlay.hidden=false;document.body.style.overflow='hidden';frames[mode].focus();
}
document.addEventListener('click',event=>{
  const link=event.target.closest('a');if(!link||event.button!==0||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey)return;
  let mode=link.dataset.modeSwitch;
  if(officePage&&link.getAttribute('href')==='/bank')mode='bank';
  if(mode!=='office'&&!(window.parent!==window&&(mode==='terminal'||mode==='bank')))return;
  event.preventDefault();event.stopImmediatePropagation();
  if(window.parent!==window)window.parent.postMessage({type:'one-person-bank:visual-mode',mode},location.origin);else show(mode);
},true);
window.addEventListener('message',event=>{
  if(event.origin!==location.origin||!Object.values(frames).some(frame=>frame.contentWindow===event.source))return;
  if(event.data?.type==='one-person-bank:visual-mode'&&Object.hasOwn(modeURLs,event.data.mode))show(event.data.mode);
});
document.addEventListener('keydown',event=>{if(event.key==='Escape')close();});
