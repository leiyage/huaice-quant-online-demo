/** Camera-only tour: never changes task state or starts research. */
export class OfficeTour {
  constructor({visit,changed=()=>{},schedule=(fn,ms)=>setTimeout(fn,ms),clear=id=>clearTimeout(id)}){Object.assign(this,{visit,changed,schedule,clear});this.timer=null;this.active=false;this.suspended=false;this.index=0;}
  start(){this.stop();this.active=true;this.index=0;this.changed(true);this.step();}
  step(){if(!this.active||this.suspended)return;this.visit(this.index);this.index=(this.index+1)%4;this.timer=this.schedule(()=>{this.timer=null;this.step();},5200);}
  suspend(value){this.suspended=value;this.clear(this.timer);this.timer=null;if(!value&&this.active)this.timer=this.schedule(()=>{this.timer=null;this.step();},5200);}
  stop(){this.clear(this.timer);this.timer=null;this.active=false;this.changed(false);}
  destroy(){this.stop();}
}
