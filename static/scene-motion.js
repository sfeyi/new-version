import {cameraBasis} from "./demo.js";

const dot=(a,b)=>a.reduce((s,v,i)=>s+v*b[i],0);
const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
const rayAt=(p,b,tx,ty)=>b.forward.map((v,i)=>v+(p[0]-.5)*2*tx*b.right[i]+(.5-p[1])*2*ty*b.up[i]);

export function matrixBasis(matrix,angle=0,front=false) {
  const s=angle*Math.PI/180,sign=front?-1:1;
  const rotate=v=>[0,3,6].map(i=>matrix[i]*v[0]+matrix[i+1]*v[1]+matrix[i+2]*v[2]);
  return {right:rotate([sign*Math.cos(s),sign*Math.sin(s),0]),up:rotate([-Math.sin(s),Math.cos(s),0]),
    forward:rotate([0,0,front?1:-1])};
}

// A real visual match initializes one world direction. Subsequent network results
// validate the step but never relocate that direction. Rotation runs without pixels.
export class SceneMotion {
  constructor(layer, marker, native=globalThis.Capacitor?.Plugins?.MotionAnchor) {
    this.layer=layer;this.marker=marker;this.active=false;this.visible=false;
    this.width=this.height=1;this.aspect=1;this.front=false;this.tx=this.ty=.5;
    this.native=native;this.nativeChain=Promise.resolve();this.nativeHandle=null;this.nativeRunning=false;this.nativeSession=null;
    this.reset();
    this.listener=e=>this.orientation(e,performance.now());
    window.addEventListener('deviceorientation',this.listener);
  }
  reset() {
    this.basis=null;this.ray=null;this.point=null;this.raw=null;
    this.sensorAt=-Infinity;this.verifiedAt=-Infinity;this.anchorId=null;
    this.domain=null;
    this.layer.hidden=true;
  }
  configure(width,height,videoWidth,videoHeight,aspect,zoom=1,front=false,tolerance=.08) {
    this.width=width;this.height=height;this.aspect=aspect;this.front=front;this.tolerance=tolerance;
    // Browser tracks do not expose calibrated intrinsics. Use an approximate 68°
    // long-axis field of view, accounting for portrait crop and current zoom.
    const w=videoWidth||1280,h=videoHeight||720,cw=Math.min(w,h*aspect),ch=cw/aspect;
    const unit=Math.tan(34*Math.PI/180)/Math.max(w,h)/Math.max(1,zoom);
    this.tx=cw*unit;this.ty=ch*unit;
    this.project(true);
    this.render(performance.now());
  }
  setActive(active,visible=active) {
    const changed=this.active!==active;
    this.active=active;this.visible=visible;
    if (changed && this.native) this.syncNative();
    this.render(performance.now());
  }
  snapshot(now=performance.now()) {
    return this.active && this.basis && now-this.sensorAt<500
      ? {basis:this.basis,tx:this.tx,ty:this.ty,domain:this.domain} : null;
  }
  observe(result,captured,now=performance.now()) {
    const bg=result?.background;
    const valid=bg?.tracking && bg.current?.length===2 && bg.current.every(Number.isFinite);
    if (valid) this.verifiedAt=now;
    else this.verifiedAt=-Infinity;
    if (!this.ray && valid && captured && captured.domain===this.domain && (bg.sceneLocked || bg.inliers>=12)) {
      this.ray=rayAt(bg.current,captured.basis,captured.tx,captured.ty);
      this.anchorId=bg.anchorId??null;
      this.project(true);
    }
    this.render(now);
    return !!this.ray;
  }
  orientation(e,now) {
    if (this.nativeRunning || !this.active || document.hidden || ![e.alpha,e.beta,e.gamma].every(Number.isFinite) || now-this.sensorAt<30) return;
    const basis=cameraBasis(e.alpha,e.beta,e.gamma,screen.orientation?.angle||0,this.front);
    this.domain='web';this.acceptBasis(basis,now);
  }
  nativeOrientation(data) {
    const now=performance.now();
    if (!this.active || document.hidden || !Array.isArray(data.matrix) || data.matrix.length!==9 || !data.matrix.every(Number.isFinite)) return;
    const basis=matrixBasis(data.matrix,screen.orientation?.angle||0,this.front);
    if (this.ray && (this.domain!=='native' || this.nativeSession!==data.session))
      this.ray=rayAt(this.raw||this.point,basis,this.tx,this.ty);
    this.nativeSession=data.session;this.nativeRunning=true;this.domain='native';
    this.acceptBasis(basis,now);
  }
  syncNative() {
    // Serialise start/stop across pause, camera changes and page lifecycle events.
    this.nativeChain=this.nativeChain.catch(()=>{}).then(async()=>{
      if (this.active && !this.nativeHandle) {
        this.nativeHandle=await this.native.addListener('orientation',data=>this.nativeOrientation(data));
        const result=await this.native.start();
        if (result.available && this.active) return;
      } else if (this.active) return;
      await this.native.stop();
      await this.nativeHandle?.remove();this.nativeHandle=null;this.nativeRunning=false;
    }).catch(async()=>{
      this.nativeRunning=false;
      try {await this.native.stop();} catch {}
      try {await this.nativeHandle?.remove();} catch {}
      this.nativeHandle=null;
    });
  }
  acceptBasis(basis,now) {
    if (this.ray && this.basis && now-this.sensorAt<300 && dot(basis.forward,this.basis.forward)<.8) {
      // Magnetometer recalibration must not teleport the visual anchor.
      this.ray=rayAt(this.raw||this.point,basis,this.tx,this.ty);
    }
    this.basis=basis;this.sensorAt=now;
    this.project();this.render(now);
  }
  project(immediate=false) {
    if (!this.ray || !this.basis) return;
    const depth=Math.max(.03,dot(this.ray,this.basis.forward));
    this.raw=[.5+dot(this.ray,this.basis.right)/(2*this.tx*depth),.5-dot(this.ray,this.basis.up)/(2*this.ty*depth)];
    if (immediate || !this.point) {this.point=[...this.raw];return;}
    const distance=Math.hypot((this.raw[0]-this.point[0])*this.width,(this.raw[1]-this.point[1])*this.height);
    if (distance<.5) return;
    const alpha=distance>12?.7:.35;
    this.point=this.point.map((v,i)=>v+(this.raw[i]-v)*alpha);
  }
  render(now) {
    this.layer.hidden=!this.visible || !this.point;
    if (this.layer.hidden) return;
    const [x,y]=this.point;
    this.marker.style.transform=`translate3d(${clamp(x*this.width,12,this.width-12)}px,${clamp(y*this.height,12,this.height-12)}px,0) translate(-50%,-50%)`;
    this.marker.dataset.offscreen=String(x<0||x>1||y<0||y>1);
    const verified=now-this.verifiedAt<3500;
    this.layer.classList.toggle('uncertain',!verified);
    this.layer.classList.toggle('near',verified && Math.hypot((x-.5)*this.width,(y-.5)*this.height)<=this.tolerance*Math.min(this.width,this.height));
  }
  get locked() {return !!this.ray;}
  dispose() {this.setActive(false,false);window.removeEventListener('deviceorientation',this.listener);}
}
