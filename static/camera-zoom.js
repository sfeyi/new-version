const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));

// Camera zoom changes the actual stream. Only the residual software crop belongs
// in CSS, snapshots and frame sampling; otherwise the image would be zoomed twice.
export class CameraZoom {
  constructor(changed=()=>{}) {this.changed=changed;this.epoch=0;this.attach(null);}
  attach(track) {
    ++this.epoch;clearTimeout(this.timer);this.timer=0;this.inflight=false;
    this.track=track;this.cap=null;this.base=this.hardware=this.value=this.digital=1;
    this.min=1;this.max=1.6;this.settleUntil=0;this.cancelled=false;
    try {
      const cap=track?.getCapabilities?.().zoom,base=track?.getSettings?.().zoom;
      if (cap && Number.isFinite(cap.min) && Number.isFinite(cap.max) && cap.max>cap.min && cap.min>0 && track.applyConstraints) {
        this.cap=cap;this.base=Number.isFinite(base)&&base>0?base:cap.min;
        this.max=Math.max(1,Math.min(8,cap.max/this.base));
      }
    } catch {}
  }
  get mode() {return this.cap?'camera':'digital';}
  get busy() {return this.inflight || !!this.timer || performance.now()<this.settleUntil;}
  set(value) {
    if (!Number.isFinite(value)) return;
    this.cancelled=false;this.value=clamp(value,this.min,this.max);
    if (!this.cap) {this.digital=this.value/this.hardware;this.changed();return;}
    if (!this.inflight && !this.timer && Math.abs(this.value-this.hardware)>.002)
      this.timer=setTimeout(()=>{this.timer=0;this.flush();},90);
    this.changed();
  }
  async flush() {
    if (!this.cap || this.inflight) return;
    const epoch=this.epoch,track=this.track,cap=this.cap,step=cap.step||.01;
    const target=clamp(cap.min+Math.round((this.value*this.base-cap.min)/step)*step,cap.min,cap.max);
    this.inflight=true;
    try {
      await track.applyConstraints({advanced:[{zoom:target}]});
      if (epoch!==this.epoch) return;
      const reported=track.getSettings?.().zoom;
      // An exposed but ignored constraint is not evidence of a working lens control.
      if (!Number.isFinite(reported) || Math.abs(reported-target)>Math.max(step*1.5,.025)) throw new Error('Camera ignored zoom');
      this.hardware=reported/this.base;this.digital=1;
      this.settleUntil=performance.now()+180;
    } catch {
      if (epoch!==this.epoch) return;
      const reported=track.getSettings?.().zoom;
      if (Number.isFinite(reported)&&reported>0) this.hardware=reported/this.base;
      this.cap=null;this.min=Math.max(1,this.hardware);this.max=Math.max(this.hardware,Math.min(8,this.hardware*1.6));
      this.value=clamp(this.value,this.min,this.max);this.digital=this.value/this.hardware;
    } finally {
      if (epoch===this.epoch) {
        this.inflight=false;
        if (this.cancelled) this.value=this.hardware*this.digital;
        if (!this.cancelled && this.cap && Math.abs(this.value-this.hardware)>Math.max(.002,(cap.step||.01)/this.base*.6))
          this.timer=setTimeout(()=>{this.timer=0;this.flush();},90);
        this.changed();
      }
    }
  }
  cancel() {
    clearTimeout(this.timer);this.timer=0;this.cancelled=true;
    // An in-flight device command cannot be cancelled. Its latest requested value
    // is allowed to settle, then analysis uses the actual returned stream.
    if (!this.inflight) {this.value=this.hardware*this.digital;this.changed();}
  }
}
