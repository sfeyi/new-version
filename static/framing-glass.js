// One cached analysis JPEG, static gradients and compositor transforms/opacity.
// This decoration never samples the camera, starts zoom or approves a guide stage.
export class FramingGlass {
  constructor(layer,image) {
    this.layer=layer;this.image=image;this.serial=0;this.url=null;
    this.animations=new Map();this.phase='idle';this.timer=0;
    for(const name of ['veil','lock','frame','haze','field','sweep'])
      this[name]=layer.querySelector('.prism-'+name);
  }
  get active() {return this.phase!=='idle';}
  animate(name,element,frames,duration,delay=0) {
    this.animations.get(name)?.cancel();
    const animation=element.animate(frames,{duration,delay,easing:'cubic-bezier(.22,.65,.25,1)',fill:'both'});
    this.animations.set(name,animation);
    return animation;
  }
  clear() {
    ++this.serial;clearTimeout(this.timer);this.timer=0;
    for(const animation of this.animations.values())animation.cancel();
    this.animations.clear();this.phase='idle';this.expanded=null;this.layer.dataset.phase='idle';
    this.layer.hidden=true;this.image.hidden=true;this.image.removeAttribute('src');
    if(this.url)URL.revokeObjectURL(this.url);
    this.url=null;
  }
  play(blob) {
    this.clear();const serial=this.serial;
    this.reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
    this.phase='intro';this.layer.dataset.phase='outline';this.layer.hidden=false;
    if(blob) {
      this.url=URL.createObjectURL(blob);this.image.src=this.url;
      this.image.decode().then(()=>{if(serial===this.serial)this.image.hidden=false;}).catch(()=>{});
    }
    if(this.reduced) {
      this.animate('frame',this.frame,[{opacity:0},{opacity:.75},{opacity:0}],240);
      this.timer=setTimeout(()=>this.clear(),260);
      return;
    }
    this.animate('veil',this.veil,[{opacity:0},{opacity:.26,offset:.25},{opacity:.12,offset:.65},{opacity:0}],1250);
    this.animate('lock',this.lock,[{opacity:0,transform:'scale(.7)'},{opacity:.95,transform:'scale(1)',offset:.22},
      {opacity:0,transform:'scale(2.3)'}],650);
    this.animate('frame',this.frame,[{opacity:0,transform:'scale(.93)'},{opacity:1,transform:'scale(1)',offset:.24},
      {opacity:.8,transform:'scale(1)'}],1350);
    this.animate('haze',this.haze,[{opacity:0,transform:'scale(.8)'},{opacity:.75,offset:.4},
      {opacity:0,transform:'scale(1.2)'}],1250);
    this.animate('field',this.field,[{opacity:0,transform:'scale(.75)'},{opacity:.9,transform:'scale(.98)',offset:.35},
      {opacity:0,transform:'scale(1.15)'}],1250,80);
    this.animate('sweep',this.sweep,[{opacity:0,transform:'translateY(-46%)'},{opacity:.65,offset:.2},
      {opacity:.65,offset:.65},{opacity:0,transform:'translateY(46%)'}],650,650);
    // Slow/no scale response must never leave an animated decoration running forever.
    this.timer=setTimeout(()=>this.finish(),3600);
  }
  sync(state) {
    if(!this.active || this.reduced || this.phase==='ending')return;
    if(state.phase==='zoom' && this.phase==='intro') {
      this.phase='zoom';this.layer.dataset.phase='zoom';clearTimeout(this.timer);
      const x=(this.layer.clientWidth-10)/Math.max(1,this.frame.clientWidth);
      const y=(this.layer.clientHeight-10)/Math.max(1,this.frame.clientHeight);
      this.expanded=`scale(${x},${y})`;
      this.animate('frame',this.frame,[{opacity:.8,transform:'scale(1)'},{opacity:1,offset:.65},
        {opacity:.9,transform:this.expanded}],850);
      const dial=document.getElementById('zoom-dial');
      if(dial) {
        let glow=dial.querySelector('.prism-dial');
        if(!glow){glow=document.createElement('i');glow.className='prism-dial';glow.setAttribute('aria-hidden','true');dial.append(glow);}
        this.animate('dial',glow,[{opacity:0,transform:'rotate(-60deg)'},{opacity:1,offset:.22},
          {opacity:1,offset:.72},{opacity:0,transform:'rotate(100deg)'}],1200);
      }
      this.timer=setTimeout(()=>this.finish(),1100);
    } else if(this.phase==='zoom' && ['verify','adjust'].includes(state.phase))this.finish();
  }
  finish() {
    if(!this.active || this.phase==='ending')return;
    clearTimeout(this.timer);this.phase='ending';this.layer.dataset.phase='settle';
    const serial=this.serial;
    const end=this.expanded || 'scale(1)';
    this.animate('frame',this.frame,[{opacity:.85,transform:end},{opacity:0,transform:end}],400)
      .finished.then(()=>{if(serial===this.serial)this.clear();}).catch(()=>{});
  }
}
