(() => {
  import('/static/office/office-switch.js').catch(()=>{});
  const RETURN_KEY='one-man-bank:terminal-return',TRANSITION_KEY='one-man-bank:transition';
  const allowed=/^\/(?:|intelligence\/?|desk)(?:\.html)?(?:\?[^#]*)?(?:#(?:replay|perf|robust|research|sentiment|collection|trades|help))?$/;
  try {
    const transition=sessionStorage.getItem(TRANSITION_KEY);
    if(transition){document.body.classList.add('mode-entering');sessionStorage.removeItem(TRANSITION_KEY);setTimeout(()=>document.body.classList.remove('mode-entering'),600);}
  } catch {}
  document.addEventListener('click',event=>{
    const link=event.target.closest('[data-mode-switch]');if(!link||event.defaultPrevented||event.button!==0||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey)return;
    event.preventDefault();let target=link.getAttribute('href')||'/';
    try {
      if(link.dataset.modeSwitch==='bank')sessionStorage.setItem(RETURN_KEY,location.pathname+location.search+location.hash);
      else if(link.dataset.modeSwitch==='terminal'){
        const saved=sessionStorage.getItem(RETURN_KEY);if(saved&&allowed.test(saved))target=saved;
        sessionStorage.removeItem(RETURN_KEY);
      }
      sessionStorage.setItem(TRANSITION_KEY,'1');
    } catch {}
    const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
    if(!reduced)document.body.classList.add('mode-leaving');
    setTimeout(()=>location.assign(target),reduced?0:240);
  });
})();
