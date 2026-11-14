// Display timing only: a fresh geometric result must still verify the final zoom.
const OUTLINE_MS = 900;
const SCAN_MS = 450;
const ZOOM_MS = 850;
const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
const ease = t => t*t*(3-2*t);

export class FramingSequence {
  constructor() {this.reset();}

  reset() {
    this.entered = false;
    this.phase = "idle";
    this.enteredAt = this.zoomAt = this.lastTime = null;
    this.from = this.to = null;
  }

  clock(now) {
    if (!Number.isFinite(now)) throw new TypeError("FramingSequence requires a finite timestamp");
    this.lastTime = Math.max(this.lastTime ?? now, now);
    return this.lastTime;
  }

  enter(now) {
    if (this.entered) return false;
    this.enteredAt = this.clock(now);
    this.entered = true;
    this.phase = "outline";
    return true;
  }

  beginZoom(from, to, now) {
    if (!Number.isFinite(from) || !Number.isFinite(to)) return false;
    const state = this.sample(now);
    // Do not consume a new/stale scale command until the previous zoom was verified.
    if (state.phase !== "adjust") return false;
    from = clamp(from, 1, 1.6);
    to = clamp(to, 1, 1.6);
    if (Math.abs(to-from) < .0001) return false;
    this.from = from;
    this.to = to;
    this.zoomAt = this.lastTime;
    this.phase = "zoom";
    return true;
  }

  finishVerification() {
    if (this.phase !== "verify") return false;
    this.phase = "adjust";
    this.from = this.to = this.zoomAt = null;
    return true;
  }

  cancelZoom() {
    if (!this.entered) return false;
    // Manual controls take ownership immediately, without replaying the introduction.
    this.phase = "adjust";
    this.from = this.to = this.zoomAt = null;
    return true;
  }

  sample(now) {
    now = this.clock(now);
    let reveal = 0, scan = null, zoom = null;
    if (this.entered) {
      reveal = 1;
      if (this.phase === "outline" || this.phase === "scan") {
        const elapsed = Math.max(0, now-this.enteredAt);
        if (elapsed < OUTLINE_MS) {
          this.phase = "outline";
          reveal = ease(elapsed/OUTLINE_MS);
        } else if (elapsed < OUTLINE_MS+SCAN_MS) {
          this.phase = "scan";
          scan = (elapsed-OUTLINE_MS)/SCAN_MS;
        } else this.phase = "adjust";
      }
      if (this.phase === "zoom") {
        const progress = clamp((now-this.zoomAt)/ZOOM_MS, 0, 1);
        zoom = progress === 1 ? this.to : this.from+(this.to-this.from)*ease(progress);
        if (progress === 1) this.phase = "verify";
      } else if (this.phase === "verify") zoom = this.to;
    }
    return {phase:this.phase, reveal, scan, zoom,
      blockAnalysis:["outline", "scan", "zoom"].includes(this.phase)};
  }
}
