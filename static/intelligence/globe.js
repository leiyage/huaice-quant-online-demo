import {EventAtlas} from './atlas.js';
import {WebGLGlobe} from './globe-renderer.js';

/** Renderer boundary: failures leave the feed/detail/timeline usable. */
export class GlobeAtlas {
  constructor(container,onSelect,onState) {
    this.container=container;this.onSelect=onSelect;this.onState=onState;this.events=[];this.selectedId=null;
    this.create(new URLSearchParams(location.search).get('globe')!=='2d');
  }
  create(use3D) {
    if(this.is3D&&this.renderer){this.rotationPreference=this.renderer.autoRotate;if(this.renderer.neural)this.neuralPreference=this.renderer.neural.enabled;}
    const rotationPreference=this.rotationPreference,neuralPreference=this.neuralPreference;
    this.renderer?.dispose();this.renderer=null;this.is3D=false;
    if(use3D) {
      try {
        this.renderer=new WebGLGlobe(this.container,this.onSelect,message=>this.fallback(message),()=>this.emit());
        this.is3D=true;this.container.dataset.renderer='webgl';
        if(rotationPreference!==undefined)this.renderer.setAutoRotate(rotationPreference&&!matchMedia('(prefers-reduced-motion: reduce)').matches);
        if(neuralPreference!==undefined)this.renderer.setNeuralEnabled(neuralPreference);
      } catch {this.fallback('WebGL 不可用，已回退二维地图；事件联动仍可使用。');return;}
    } else {this.renderer=new EventAtlas(this.container,this.onSelect);this.container.dataset.renderer='svg';}
    this.renderer.setEvents(this.events,this.selectedId);this.focusEvent(this.selectedId,false);this.emit();
  }
  fallback(message) {
    this.renderer?.dispose();this.renderer=new EventAtlas(this.container,this.onSelect);this.is3D=false;this.container.dataset.renderer='svg';
    this.renderer.setEvents(this.events,this.selectedId);this.emit(message);
  }
  emit(message='') {
    const neural=this.is3D?this.renderer.neural:null;
    if(this.is3D){this.rotationPreference=this.renderer.autoRotate;if(neural)this.neuralPreference=neural.enabled;}
    this.onState?.({is3D:this.is3D,autoRotate:Boolean(this.is3D&&this.renderer.autoRotate),neuralAvailable:Boolean(neural),neuralEnabled:Boolean(neural?.enabled),neuralAnimated:Boolean(neural?.animated),message});
  }
  setEvents(events,selectedId) {
    const changed=this.selectedId!==selectedId,first=this.selectedId===null;
    this.events=events;this.selectedId=selectedId;this.renderer.setEvents(events,selectedId);
    if(changed)this.focusEvent(selectedId,!first);
  }
  focusEvent(id,animate=true) {const e=this.events.find(e=>e.event_id===id);if(e&&this.is3D)this.renderer.focus(e.latitude,e.longitude,animate);}
  toggleRotation(){if(this.is3D)this.renderer.setAutoRotate(!this.renderer.autoRotate);}
  toggleNeural(){if(this.is3D&&this.renderer.neural)this.renderer.setNeuralEnabled(!this.renderer.neural.enabled);}
  setButterfly(network,step,selectedId,onSelect,controls){if(this.is3D)this.renderer.setButterfly(network,step,selectedId,onSelect,controls);}
  adjustZoom(delta){if(this.is3D)this.renderer.adjustZoom(delta);}
  resetView(){if(this.is3D){this.renderer.zoom=1;this.focusEvent(this.selectedId);this.renderer.invalidate();}}
  toggleMode(){this.create(!this.is3D);}
  dispose(){this.renderer?.dispose();this.renderer=null;}
}
