// Reuse the last small analysis JPEG. Never blur or read back the live video.
export class FramingGlass {
  constructor(layer,image) {this.layer=layer;this.image=image;this.serial=0;this.url=null;this.animation=null;}
  clear() {
    ++this.serial;this.animation?.cancel();this.animation=null;
    this.layer.hidden=true;this.image.hidden=true;this.image.removeAttribute('src');
    if (this.url) URL.revokeObjectURL(this.url);
    this.url=null;
  }
  async play(blob) {
    this.clear();const serial=this.serial;
    if (blob) {
      this.url=URL.createObjectURL(blob);this.image.src=this.url;
      try {await this.image.decode();if(serial===this.serial)this.image.hidden=false;} catch {}
      if (serial!==this.serial) return;
    }
    this.layer.hidden=false;
    const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
    this.animation=this.layer.animate([{opacity:0},{opacity:.85,offset:.18},{opacity:.65,offset:.55},{opacity:0}],
      {duration:reduced?200:1050,easing:'ease-in-out',fill:'both'});
    this.animation.finished.then(()=>{if(serial===this.serial)this.clear();}).catch(()=>{});
  }
}
