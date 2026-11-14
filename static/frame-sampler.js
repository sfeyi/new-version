// At most one image is being prepared. Frames are sampled on demand, never queued.
export class FrameSampler {
  constructor() {this.worker = null; this.pending = null; this.serial = 0; this.disabled = false;}

  async capture(video, aspect, zoom, edge = 640, quality = .75) {
    if (this.disabled || !globalThis.Worker || !globalThis.OffscreenCanvas || !globalThis.createImageBitmap) return null;
    if (this.pending) return null;
    let bitmap;
    try {
      if (!this.worker) {
        this.worker = new Worker(new URL("./frame-worker.js", import.meta.url), {type:"module"});
        this.worker.onmessage = ({data}) => {
          const task = this.pending;
          if (!task || task.id !== data.id) return;
          clearTimeout(task.timer);
          this.pending = null;
          if (data.error) task.reject(new Error(data.error));
          else task.resolve(data);
        };
        this.worker.onerror = () => this.dispose();
        this.worker.onmessageerror = () => this.dispose();
      }
      const w = video.videoWidth, h = video.videoHeight;
      const cw = Math.min(w, h*aspect)/zoom, ch = cw/aspect;
      const scale = Math.min(1, edge/Math.max(cw, ch));
      bitmap = await createImageBitmap(video, Math.round((w-cw)/2), Math.round((h-ch)/2), Math.round(cw), Math.round(ch),
        {resizeWidth:Math.max(1, Math.round(cw*scale)), resizeHeight:Math.max(1, Math.round(ch*scale)), resizeQuality:"low"});
      if (!this.worker) {bitmap.close(); return null;}
      const id = ++this.serial;
      const result = new Promise((resolve, reject) => {
        this.pending = {id, resolve, reject, timer:setTimeout(() => this.dispose(), 6000)};
      });
      this.worker.postMessage({id, bitmap, quality}, [bitmap]);
      return await result;
    } catch {
      bitmap?.close();
      this.dispose();
      return null; // Older WebViews use a small, low-frequency canvas fallback.
    }
  }

  dispose() {
    this.disabled = true;
    this.worker?.terminate();
    this.worker = null;
    if (this.pending) {
      clearTimeout(this.pending.timer);
      this.pending.reject(new Error("图片准备已取消"));
      this.pending = null;
    }
  }
}
