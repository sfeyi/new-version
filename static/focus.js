// Resolving applyConstraints means a request was accepted, not that the lens
// has finished moving or that the subject is sharp.
const clamp = (value, low, high) => Math.max(low, Math.min(high, value));

export function cropRect(width, height, aspect, zoom = 1) {
  if (![width,height,aspect,zoom].every(v => Number.isFinite(v) && v > 0) || zoom < 1) {
    throw new RangeError("取景尺寸无效");
  }
  const w = Math.min(width, height*aspect)/zoom, h=w/aspect;
  return {x:(width-w)/2, y:(height-h)/2, width:w, height:h};
}

export function cameraPoint(point, width, height, aspect, zoom = 1) {
  if (!point || point.length !== 2 || !point.every(Number.isFinite)) throw new RangeError("对焦坐标无效");
  const crop = cropRect(width,height,aspect,zoom);
  // W3C pointsOfInterest uses normalized [0,1] source coordinates, not CSS pixels.
  return {x:(crop.x+clamp(point[0],0,1)*crop.width)/width,
    y:(crop.y+clamp(point[1],0,1)*crop.height)/height};
}

function read(track, method) {
  try {return track?.[method]?.() || {};} catch {return {};}
}

export class FocusController {
  constructor(timeoutMs = 8000) {this.timeoutMs = timeoutMs; this.bind(null);}

  bind(track, supported = {}) {
    this.revision = (this.revision || 0)+1;
    this.track = track;
    this.busy = false;
    this.blocked = false;
    const caps = read(track,"getCapabilities");
    const usable = !!track && track.readyState !== "ended" && typeof track.applyConstraints === "function";
    this.modes = usable && Array.isArray(caps.focusMode) ? caps.focusMode : [];
    this.autoMode = this.modes.includes("continuous") ? "continuous"
      : this.modes.includes("single-shot") ? "single-shot" : null;
    this.canPoint = !!this.autoMode && supported.pointsOfInterest === true;
    const range = caps.focusDistance;
    this.distance = this.modes.includes("manual") && range &&
      Number.isFinite(range.min) && Number.isFinite(range.max) && range.max > range.min
      ? {...range} : null;
    return this;
  }

  get settings() {return read(this.track,"getSettings");}

  async apply(patch) {
    const track = this.track, revision = this.revision;
    if (!track || track.readyState === "ended" || this.blocked) return {status:"unavailable"};
    if (this.busy) return {status:"busy"};
    this.busy = true;
    let timer;
    try {
      // Keep resolution/facingMode and unrelated hardware constraints.
      const omitFocus = item => Object.fromEntries(Object.entries(item).filter(([key]) =>
        !["focusMode","focusDistance","pointsOfInterest"].includes(key)));
      const previous = read(track,"getConstraints");
      const constraints = omitFocus(previous);
      constraints.advanced = [...(previous.advanced || []).map(omitFocus).filter(item => Object.keys(item).length), patch];
      const outcome = await Promise.race([
        track.applyConstraints(constraints).then(() => "applied"),
        new Promise(resolve => {timer=setTimeout(() => resolve("timeout"),this.timeoutMs);}),
      ]);
      if (revision !== this.revision) return {status:"stale"};
      if (outcome === "timeout") {
        // The browser has no cancellation API. Require a new track before more
        // focus commands, so a late hardware operation cannot race a newer one.
        this.blocked = true;
        return {status:"timeout"};
      }
      const settings = this.settings;
      // Advanced constraints may be ignored; report observable disagreement.
      const modeMismatch = settings.focusMode && settings.focusMode !== patch.focusMode;
      const pointMismatch = patch.pointsOfInterest && (!settings.pointsOfInterest?.length ||
        ["x","y"].some(axis => !Number.isFinite(settings.pointsOfInterest[0][axis]) ||
          Math.abs(settings.pointsOfInterest[0][axis]-patch.pointsOfInterest[0][axis]) > .02));
      const distanceMismatch = patch.focusDistance !== undefined &&
        (!Number.isFinite(settings.focusDistance) || Math.abs(settings.focusDistance-patch.focusDistance) > Math.max(this.distance?.step || 0, .0001));
      return {status:modeMismatch || pointMismatch || distanceMismatch ? "unconfirmed" : "requested", settings};
    } catch (error) {
      return {status:revision === this.revision ? "failed" : "stale", error};
    } finally {
      clearTimeout(timer);
      if (revision === this.revision) this.busy = false;
    }
  }

  auto() {
    if (!this.autoMode) return Promise.resolve({status:"unavailable"});
    return this.apply({focusMode:this.autoMode, ...(this.canPoint ? {pointsOfInterest:[{x:.5,y:.5}]} : {})});
  }

  point(point) {
    if (!this.canPoint) return Promise.resolve({status:"unavailable"});
    if (![point?.x,point?.y].every(v => Number.isFinite(v) && v >= 0 && v <= 1)) {
      return Promise.resolve({status:"failed", error:new RangeError("对焦坐标无效")});
    }
    const mode = this.modes.includes("single-shot") ? "single-shot" : this.autoMode;
    return this.apply({focusMode:mode, pointsOfInterest:[point]});
  }

  manual(fraction) {
    if (!this.distance) return Promise.resolve({status:"unavailable"});
    if (!Number.isFinite(fraction)) return Promise.resolve({status:"failed"});
    const {min,max,step} = this.distance;
    let distance = min+clamp(fraction,0,1)*(max-min);
    if (Number.isFinite(step) && step > 0) distance = min+Math.round((distance-min)/step)*step;
    return this.apply({focusMode:"manual", focusDistance:clamp(distance,min,max)});
  }
}
