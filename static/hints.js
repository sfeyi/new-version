// Hold one instruction long enough to read; only replace it with a sustained new cue.
const MIN_DISPLAY_MS = 2500;
const CANDIDATE_MS = 650;
const urgentModes = new Set(["ready", "error", "fatal"]);

function same(a, b) {
  return a && b && a.key === b.key && a.who === b.who && a.message === b.message && a.mode === b.mode;
}

export class HintPacer {
  constructor() {this.reset();}

  reset() {
    this.current = null;
    this.candidate = null;
    this.shownAt = null;
    this.lastUpdate = null;
    this.protectedUntil = 0;
  }

  show(hint, now) {
    this.current = {...hint};
    this.shownAt = now;
    this.candidate = null;
    return {...hint};
  }

  update({who = "", message = "", mode = "", key = ""}, now, {immediate = false} = {}) {
    if (this.lastUpdate !== null && now < this.lastUpdate) this.reset();
    this.lastUpdate = now;
    const hint = {who, message, mode, key};
    if (same(hint, this.current)) {
      // Returning to the visible cue cancels any older pending direction.
      this.candidate = null;
      return null;
    }
    // Save/share feedback from a user action must survive repeated "ready" frames.
    if (!immediate && hint.key === this.current?.key && now < this.protectedUntil && !["error", "fatal"].includes(mode))
      return null;
    if (!this.current || hint.key !== this.current.key || immediate || urgentModes.has(mode)) {
      this.protectedUntil = immediate ? now + MIN_DISPLAY_MS : 0;
      return this.show(hint, now);
    }
    if (!this.candidate || !same(hint, this.candidate.hint)) this.candidate = {hint, since:now};
    if (now-this.shownAt >= MIN_DISPLAY_MS && now-this.candidate.since >= CANDIDATE_MS)
      return this.show(hint, now);
    return null;
  }
}
