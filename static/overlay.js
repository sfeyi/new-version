const gold = "#ffcf69";
const mint = "#53f2c8";
const white = "#fffdf8";

function drawing(canvas) {
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (!w || !h) return null;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.lineCap = ctx.lineJoin = "round";
  return {ctx, w, h, point: ([x, y]) => [x * w, y * h]};
}

function path(d, points, color, width = 2, dash = []) {
  if (!points.length) return;
  const c = d.ctx;
  c.strokeStyle = color;
  c.lineWidth = width;
  c.setLineDash(dash);
  c.beginPath();
  c.moveTo(...d.point(points[0]));
  for (const p of points.slice(1)) c.lineTo(...d.point(p));
  c.stroke();
  c.setLineDash([]);
}

function circle(d, p, radius, color, filled = false) {
  const c = d.ctx;
  c.beginPath();
  c.arc(...d.point(p), radius, 0, Math.PI * 2);
  if (filled) {
    c.fillStyle = color;
    c.fill();
  } else {
    c.strokeStyle = color;
    c.lineWidth = 2.4;
    c.stroke();
  }
}

function corners(d, box, color) {
  const [x, y, r, b] = box;
  const dx = Math.min((r - x) / 4, 12 / d.w), dy = Math.min((b - y) / 4, 12 / d.h);
  for (const [a, v, sx, sy] of [[x,y,1,1], [r,y,-1,1], [x,b,1,-1], [r,b,-1,-1]]) {
    path(d, [[a + dx*sx,v], [a,v], [a,v + dy*sy]], color, 2);
  }
}

function arrow(d, from, to, color = gold) {
  const [x, y] = d.point(from), [r, b] = d.point(to);
  const len = Math.hypot(r - x, b - y);
  if (len < 10) return;
  const angle = Math.atan2(b - y, r - x), head = Math.min(11, len / 3);
  path(d, [from, to], color, 2);
  const c = d.ctx;
  c.beginPath(); c.moveTo(r, b);
  for (const turn of [-.5, .5]) c.lineTo(r - head*Math.cos(angle + turn), b - head*Math.sin(angle + turn));
  c.closePath(); c.fillStyle = color; c.fill();
}

function referenceJoints(d, points, edges) {
  const visible = points.filter(Boolean);
  if (!visible.length) return;
  const xs = visible.map(p => p[0]), ys = visible.map(p => p[1]);
  const span = Math.max((Math.max(...xs)-Math.min(...xs))*d.w, (Math.max(...ys)-Math.min(...ys))*d.h);
  const stroke = Math.max(1, Math.min(2.2, span*.012));
  for (const [a, b] of edges) {
    if (points[a] && points[b]) path(d, [points[a], points[b]], white, stroke, [stroke*2.6, stroke*1.9]);
  }
  for (const point of visible) circle(d, point, Math.max(1, stroke*1.45), white, span < 95);
}

function standingBox(d, box) {
  const [x, y, r, b] = box;
  d.ctx.fillStyle = "#ffcf6914";
  d.ctx.fillRect(x*d.w, y*d.h, (r-x)*d.w, (b-y)*d.h);
  path(d, [[x,y], [r,y], [r,b], [x,b], [x,y]], gold, 2, [7, 5]);
}

function poseOutline(d, outline) {
  d.ctx.save();
  d.ctx.shadowColor = "#142322cc";
  d.ctx.shadowBlur = 3;
  for (const contour of outline.contours) path(d, [...contour, contour[0]], white, 2);
  for (const stroke of outline.details) path(d, stroke, "#fffdf8cc", 1.2);
  d.ctx.restore();
}

function buildingOutline(d, outline) {
  // 始终使用参考画幅的位置；拍摄者能直接看见金线下方的实际建筑。
  for (const stroke of outline.paths) {
    path(d, stroke, "#18251fdf", 3.8);
    path(d, stroke, gold, 2);
  }
}

function landmarkMarkers(d, landmarks = [], anchors = []) {
  const ctx = d.ctx;
  ctx.save();
  ctx.shadowColor = "#10211d";
  ctx.shadowBlur = 3;
  ctx.font = "600 11px system-ui, sans-serif";
  for (const [id, p] of landmarks.entries()) {
    const live = anchors.find(a => a.id === id)?.current;
    if (live && live.every(v => v >= 0 && v <= 1)) {
      path(d, [p, live], "#e0f0ffab", 1, [3, 5]);
      circle(d, live, 4, "#233538", true);
      circle(d, live, 2.8, "#b6ddff", true);
    }
    const [x, y] = d.point(p);
    const a = 4 / d.w, b = 4 / d.h;
    const diamond = [[p[0]-a,p[1]], [p[0],p[1]-b], [p[0]+a,p[1]], [p[0],p[1]+b], [p[0]-a,p[1]]];
    path(d, diamond, "#20322bdb", 3);
    path(d, diamond, "#fffdf8", 1.5);
    ctx.strokeStyle = "#20322b";
    ctx.lineWidth = 2.6;
    ctx.strokeText(String(id+1), x+6, y-3);
    ctx.fillStyle = "#fffdf8";
    ctx.fillText(String(id+1), x+6, y-3);
  }
  ctx.restore();
}

function backgroundRings(d, background, reference) {
  landmarkMarkers(d, reference.landmarks, background?.tracking ? background.anchors : []);
  const target = [.5, .5];
  const radius = reference.centerTolerance * Math.min(d.w, d.h);
  const current = background?.tracking ? background.current : null;
  d.ctx.shadowColor = "#10211dc9";
  d.ctx.shadowBlur = 3;
  circle(d, target, radius, "#ffcf691b", true);
  if (current) {
    const [dx, dy] = [current[0] - target[0], current[1] - target[1]];
    if (current.every(v => v >= 0 && v <= 1)) {
      if (Math.hypot(dx*d.w, dy*d.h) > radius*2) {
        path(d, [target, current], "#ffffffad", 1.5, [4, 5]);
      }
      circle(d, current, Math.max(4, Math.min(12, radius * .45)), mint);
      circle(d, current, 2.4, mint, true);
    } else {
      // 参考中心落在画外时，只指向它所在的一侧。
      const t = Math.min((.5 - 12/d.w) / Math.abs(dx), (.5 - 12/d.h) / Math.abs(dy));
      const end = [target[0] + t*dx, target[1] + t*dy];
      const length = Math.hypot(dx*d.w, dy*d.h);
      arrow(d, [end[0] - 26*dx/length, end[1] - 26*dy/length], end, mint);
    }
  }
  circle(d, target, radius, gold);
  for (const sign of [-1, 1]) {
    const inner = sign*(radius + 2), outer = sign*(radius + 8);
    path(d, [[.5+inner/d.w,.5], [.5+outer/d.w,.5]], gold);
    path(d, [[.5,.5+inner/d.h], [.5,.5+outer/d.h]], gold);
  }
  d.ctx.shadowBlur = 0;
}

export function drawReference(canvas, ref, stage) {
  const d = drawing(canvas);
  if (!d || !ref) return;
  if (stage === "background") {
    if (ref.buildingOutline) buildingOutline(d, ref.buildingOutline);
    else landmarkMarkers(d, ref.landmarks);
    return;
  }
  for (const box of ref.boxes) corners(d, box, "#ffffff9c");
  if (ref.box) corners(d, ref.box, gold);
  if (ref.outline) poseOutline(d, ref.outline);
  else referenceJoints(d, ref.points, ref.edges);
}

export function drawGuide(canvas, result, ref, stage = result?.stage) {
  const d = drawing(canvas);
  if (!d || !ref) return;
  if (stage === "background") {
    if (ref.buildingOutline) buildingOutline(d, ref.buildingOutline);
    else backgroundRings(d, result?.background, ref);
    return;
  }
  if (result && result.boxes.length > 1) {
    for (const box of result.boxes) corners(d, box, "#ffffff80");
  }
  if (stage === "position" && ref.box) standingBox(d, ref.box);
  if (result?.selected && (stage === "position" || stage === "ready" || result.boxes.length > 1))
    corners(d, result.selected, mint);
  if (stage !== "pose") return;
  if (ref.outline) poseOutline(d, ref.outline);
  else referenceJoints(d, ref.points, ref.edges);
  const pose = result?.pose;
  if (!pose) return;
  for (const p of pose.actual) {
    if (p) circle(d, p, 2, "#53f2c8c9", true);
  }
  const goal = ref.points[pose.joint];
  if (!goal) return;
  circle(d, goal, 7, white);
  const current = pose.actual[pose.joint];
  if (current) {
    circle(d, current, 3.5, mint, true);
    arrow(d, current, goal, mint);
  }
}

