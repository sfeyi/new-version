// Color-only effects: the guide always receives the unmodified camera frame.
export const FILTERS = Object.freeze({
  none: {name:"原图", saturation:1, sepia:0, contrast:1, brightness:1},
  fresh: {name:"清新", saturation:1.18, sepia:0, contrast:1.04, brightness:1.04},
  warm: {name:"暖色", saturation:1.08, sepia:.25, contrast:1.02, brightness:1.04},
  film: {name:"复古", saturation:.78, sepia:.35, contrast:.90, brightness:1.04},
  mono: {name:"黑白", saturation:0, sepia:0, contrast:1.08, brightness:1},
});

export function filterSettings(id = "none", strength = 1) {
  if (!Object.hasOwn(FILTERS, id) || !Number.isFinite(strength)) throw new RangeError("滤镜参数无效");
  return {id, strength:Math.max(0, Math.min(1, strength))};
}

function values(settings) {
  const {id, strength} = filterSettings(settings.id, settings.strength);
  const p = FILTERS[id];
  return {s:1+(p.saturation-1)*strength, t:p.sepia*strength,
    c:1+(p.contrast-1)*strength, b:1+(p.brightness-1)*strength};
}

export function filterCSS(settings) {
  if (settings.id === "none" || settings.strength === 0) return "none";
  const {s,t,c,b} = values(settings);
  return `saturate(${s}) sepia(${t}) contrast(${c}) brightness(${b})`;
}

const clamp = v => Math.max(0, Math.min(255, v));

// Pixel fallback is intentional: CanvasRenderingContext2D.filter is not available
// in every mobile browser. Use the same sRGB operations as the CSS preview.
export function filterPixels(pixels, settings) {
  const {s,t,c,b} = values(settings);
  if (settings.id === "none" || settings.strength === 0) return pixels;
  for (let i=0; i<pixels.length; i+=4) {
    let r=pixels[i], g=pixels[i+1], blue=pixels[i+2];
    const gray=.213*r+.715*g+.072*blue;
    r=clamp(gray+s*(r-gray)); g=clamp(gray+s*(g-gray)); blue=clamp(gray+s*(blue-gray));
    const rr=clamp((1-.607*t)*r+.769*t*g+.189*t*blue);
    const gg=clamp(.349*t*r+(1-.314*t)*g+.168*t*blue);
    const bb=clamp(.272*t*r+.534*t*g+(1-.869*t)*blue);
    pixels[i]=clamp(clamp(c*rr+127.5*(1-c))*b);
    pixels[i+1]=clamp(clamp(c*gg+127.5*(1-c))*b);
    pixels[i+2]=clamp(clamp(c*bb+127.5*(1-c))*b);
  }
  return pixels;
}

export function applyPhotoFilter(canvas, settings) {
  if (settings.id === "none" || settings.strength === 0) return canvas;
  const ctx = canvas.getContext("2d");
  const frame = ctx.getImageData(0, 0, canvas.width, canvas.height);
  filterPixels(frame.data, settings);
  ctx.putImageData(frame, 0, 0);
  return canvas;
}
