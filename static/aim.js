// Only presentation is filtered. Stage/capture decisions always use fresh geometry.
export class AimMotion {
  constructor(aspect = 1) {this.reset(aspect);}

  reset(aspect = 1, preserveCompletion = false) {
    const completed = preserveCompletion && this.completed;
    const successAt = preserveCompletion ? this.successAt : null;
    this.aspect = aspect;
    this.point = this.target = this.candidate = null;
    this.lastSeen = this.lastFrame = this.missingSince = null;
    this.near = false;
    this.progress = 0;
    this.stage = null;
    this.completed = !!completed;
    this.successAt = successAt;
    this.anchorId = null;
    this.ttl = 1800;
  }

  distance(a, b) {
    return Math.hypot((a[0]-b[0])*this.aspect, a[1]-b[1]) / Math.min(1, this.aspect);
  }

  update(result, now) {
    const bg = result?.background;
    const valid = bg?.tracking && bg.current?.length === 2 && bg.current.every(Number.isFinite);
    if (!this.completed && result?.completed?.includes('background')) {
      this.completed = true;
      this.successAt = now;
    }
    this.stage = result?.stage;
    this.near = false;
    this.progress = 0;
    if (this.completed || (result && result.stage !== 'background')) {
      this.point = this.target = null;
      return;
    }
    if (!valid) {
      if (this.missingSince === null) this.missingSince = now;
      this.candidate = null;
      return;
    }
    // A large one-frame mismatch must be confirmed before moving the marker.
    const p = [...bg.current];
    if (bg.anchorId != null && bg.anchorId !== this.anchorId) {
      // A newly acquired scenery anchor fades in at its own location, never slides across the scene.
      this.point = this.target = null;
      this.lastSeen = null;
      this.anchorId = bg.anchorId;
    }
    const expired = this.lastSeen === null || now - this.lastSeen > this.ttl ||
      (this.missingSince !== null && now - this.missingSince > 600);
    if (!expired && this.target && this.distance(p, this.target) > .16) {
      if (!this.candidate || now-this.candidate.time > this.ttl || this.distance(p, this.candidate.point) > .065) {
        this.candidate = {point:p, time:now};
        return;
      }
    }
    this.candidate = null;
    if (this.lastSeen !== null) this.ttl = Math.max(1800, Math.min(4500, (now-this.lastSeen)*2.5));
    if (expired || !this.point) this.point = [...p];
    this.lastSeen = now;
    this.missingSince = null;
    this.near = !!bg.centerOk;
    this.progress = this.near ? Math.max(0, Math.min(1, result?.hold || 0)) : 0;
    if (expired || !this.target) this.target = p;
    else {
      const error = this.distance(p, this.target);
      // Quiet at rest, responsive during a deliberate pan; a tiny dead band stops shimmer.
      const alpha = bg.sceneLocked ? (error < .0025 ? 0 : 1) : error < .004 ? 0 : error < .035 ? .24 : .78;
      this.target = this.target.map((v, i) => v + (p[i]-v)*alpha);
    }
  }

  sample(now) {
    const dt = this.lastFrame === null ? 16 : Math.min(64, Math.max(0, now-this.lastFrame));
    this.lastFrame = now;
    const age = this.lastSeen === null ? Infinity : now-this.lastSeen;
    const loss = this.missingSince === null ? Math.max(0, age-this.ttl+350) : now-this.missingSince;
    const opacity = Math.max(0, Math.min(1, 1-loss/350));
    const near = this.near && this.missingSince === null && age < this.ttl;
    if (this.point && this.target) {
      // Remain on the tracked scenery even near the ring; visual snapping used to detach the dot.
      const target = this.target;
      const error = this.distance(target, this.point);
      const alpha = 1-Math.exp(-dt/(error > .06 ? 75 : 145));
      this.point = error < .0005 ? [...target] : this.point.map((v, i) => v+(target[i]-v)*alpha);
    }
    const elapsed = this.successAt === null ? Infinity : now-this.successAt;
    return {point:opacity > 0 && this.point ? [...this.point] : null, opacity, near:near && opacity > 0,
      completed:this.completed, progress:near ? this.progress : 0, success:elapsed >= 0 && elapsed < 1600 ? elapsed/1600 : null};
  }

  animation(now) {
    if (this.successAt !== null && now-this.successAt < 1600) return {active:true};
    if (!this.point || this.lastSeen === null) return {active:false};
    const fadeAt = this.missingSince ?? this.lastSeen+this.ttl-350;
    if (now >= fadeAt+350) return {active:false};
    if (now >= fadeAt || this.target && this.distance(this.point,this.target) >= .0005) return {active:true};
    // An unchanged marker sleeps until it must fade, instead of repainting the entire view.
    return {active:false, wakeAt:fadeAt};
  }
}
