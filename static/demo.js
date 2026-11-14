// Presentation-only anchor. It never reads camera pixels or claims visual matching.
const rad = Math.PI/180;
const clamp = (x,a,b) => Math.max(a,Math.min(b,x));
const dot = (a,b) => a.reduce((sum,v,i)=>sum+v*b[i],0);

export function cameraBasis(alpha, beta, gamma, screen = 0, front = false) {
  const [a,b,g,s] = [alpha,beta,gamma,screen].map(v=>v*rad);
  // DeviceOrientation intrinsic Z-X'-Y'' rotation; device +z points out of the screen.
  const rotate = ([x,y,z]) => {
    const x1=Math.cos(g)*x+Math.sin(g)*z, z1=-Math.sin(g)*x+Math.cos(g)*z;
    const y2=Math.cos(b)*y-Math.sin(b)*z1, z2=Math.sin(b)*y+Math.cos(b)*z1;
    return [Math.cos(a)*x1-Math.sin(a)*y2,Math.sin(a)*x1+Math.cos(a)*y2,z2];
  };
  const sign=front?-1:1;
  return {right:rotate([sign*Math.cos(s),sign*Math.sin(s),0]),
    up:rotate([-Math.sin(s),Math.cos(s),0]),forward:rotate([0,0,front?1:-1])};
}

export class OrientationAnchor {
  constructor() {this.reset();}
  reset() {this.basis=null;this.ray=null;this.point=[.70,.42];this.aspect=.75;}
  place(point) {
    this.point=point.map(x=>clamp(x,.03,.97));
    this.ray=null;
    if (this.basis) this.lock();
    return this.point;
  }
  lock() {
    const ty=Math.tan(30*rad), tx=ty*this.aspect;
    const x=(this.point[0]-.5)*2*tx, y=(.5-this.point[1])*2*ty;
    this.ray=this.basis.forward.map((v,i)=>v+x*this.basis.right[i]+y*this.basis.up[i]);
  }
  update(basis) {
    this.basis=basis;
    if (!this.ray) this.lock();
    const depth=dot(this.ray,basis.forward), ty=Math.tan(30*rad), tx=ty*this.aspect;
    const divisor=Math.max(.03,depth);
    this.point=[.5+dot(this.ray,basis.right)/(2*tx*divisor),.5-dot(this.ray,basis.up)/(2*ty*divisor)];
    return this.point;
  }
}

export class DemoMarker {
  constructor(layer, marker, aligned) {
    this.layer=layer;this.marker=marker;this.aligned=aligned;
    this.anchor=new OrientationAnchor();this.active=false;this.timer=0;
    this.width=this.height=1;this.front=false;this.lastSensorAt=0;this.sensorSeen=false;
    this.listener=e=>this.orientation(e);
    window.addEventListener('deviceorientation',this.listener);
  }
  requestSensors() {
    const permission=globalThis.DeviceOrientationEvent?.requestPermission;
    if (permission) permission.call(DeviceOrientationEvent).catch(()=>{});
  }
  reset() {
    clearTimeout(this.timer);this.timer=0;this.anchor.reset();this.sensorSeen=false;
    this.layer.classList.remove('near');this.render();
  }
  configure(width,height,aspect,front) {
    this.width=width;this.height=height;this.anchor.aspect=aspect;this.front=front;
    this.render();
  }
  show(active) {
    this.active=active;this.layer.hidden=!active;
    if (!active) {clearTimeout(this.timer);this.timer=0;this.layer.classList.remove('near');}
    else this.render();
  }
  place(point) {this.anchor.place(point);this.render();}
  orientation(event) {
    if (!this.active || document.hidden || ![event.alpha,event.beta,event.gamma].every(Number.isFinite)) return;
    const now=performance.now();
    if (now-this.lastSensorAt<32) return;
    const basis=cameraBasis(event.alpha,event.beta,event.gamma,screen.orientation?.angle || 0,this.front);
    // Sensor calibration jumps should not throw the demonstration marker across the screen.
    if (this.anchor.basis && now-this.lastSensorAt<300 && dot(basis.forward,this.anchor.basis.forward)<.8) {
      this.anchor.ray=null;
      this.anchor.point=this.anchor.point.map(x=>clamp(x,.04,.96));
    }
    this.lastSensorAt=now;this.sensorSeen=true;
    this.anchor.update(basis);this.render();
  }
  render() {
    const [x,y]=this.anchor.point;
    const px=clamp(x*this.width,12,this.width-12), py=clamp(y*this.height,12,this.height-12);
    this.marker.style.transform=`translate3d(${px}px,${py}px,0) translate(-50%,-50%)`;
    this.marker.dataset.offscreen=String(x<0||x>1||y<0||y>1);
    if (!this.active) return;
    const near=Math.hypot((x-.5)*this.width,(y-.5)*this.height)<.11*Math.min(this.width,this.height);
    this.layer.classList.toggle('near',near);
    if (near && !this.timer) this.timer=setTimeout(()=>{
      this.timer=0;
      if(this.active && !document.hidden && this.layer.classList.contains('near')) this.aligned();
    },650);
    if (!near) {clearTimeout(this.timer);this.timer=0;}
  }
  dispose() {this.show(false);window.removeEventListener('deviceorientation',this.listener);}
}
