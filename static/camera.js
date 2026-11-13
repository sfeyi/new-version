import {drawReference, drawGuide} from "./overlay.js";
import {onDevice, openGuide, analyzeFrame, releaseGuide} from "./transport.js";

const el = id => document.getElementById(id);
const video = el("video"), livePhoto = el("live-photo"), refPhoto = el("reference");
const surface = el("surface"), refSurface = el("ref-surface");
const stages = ["background", "position", "pose", "ready"];
const stageNames = ["机位", "站位", "姿势", "拍摄"];
const actors = ["摄影者 · 调整机位", "模特 · 调整站位", "模特 · 调整姿势", "摄影者 · 拍摄"];
const skipped = new Set();
let shownStage = null;
let session = null, reference = null, referenceFile = null, refUrl = null;
let stream = null, liveUrl = null, source = null, facing = "environment";
let referenceVersion = 0, sourceVersion = 0, generation = 0;
let seq = 0, pendingTap = null, latest = null, busy = false, shotRequested = false;
let lastAnalyzed = 0, lastVideoTime = -1, lastMessage = "";
let cameraZoom = 1;
let refMirrored = false, manualDirection = false, refTap = null;
let mirrorFrames = 0, autoFlippedSource = -1;

function setCameraZoom(value) {
  cameraZoom = value;
  video.style.transform = livePhoto.style.transform = "scale(" + value + ")";
  const name = {camera:"相机取景", photo:"照片试拍"}[source] || "尚未取景";
  el("source-state").textContent = value === 1 ? name : name + " · " + value.toFixed(2) + "×";
}

function alignZoom(result) {
  const factor = result.background?.zoomFactor;
  if (result.stage !== "background" || !factor || skipped.has("background")) return false;
  const wanted = Math.max(1, Math.min(1.6, cameraZoom * factor));
  if (Math.abs(wanted - cameraZoom) < .005) {
    if (source === "photo") {
      result.message = factor < 1 ? "这张试拍照取景太近，请换一张更广的照片，或跳过机位"
        : "放大到 1.60× 后地标仍偏小，请换一张取景更近的照片，或跳过机位";
    } else {
      result.message = factor < 1 ? "已到最广的 1×，请后退一些，让地标在画面中变小"
        : "已放大到 1.60×，请向地标靠近一些";
    }
    return false;
  }
  setCameraZoom(wanted);
  invalidate("background");
  tell("摄影者 · 匹配景别", "已调至 " + wanted.toFixed(2) + "×，正在核对新画面中的地标");
  return true;
}

function tell(who, message, mode = "") {
  el("who").textContent = who;
  if (message !== lastMessage) {
    el("hint").textContent = message;
    lastMessage = message;
  }
  el("indicator").dataset.mode = mode;
}

function showReferenceGuide() {
  drawReference(el("ref-overlay"), reference, shownStage);
  if (!reference) return;
  let note = reference.subjectHint || (reference.boxes.length > 1
      ? "金色框标出当前主角，点按其他人物可切换参考动作。"
      : "白线是取景器中的参考动作。取景时可点按画面中的主角。");
  if (shownStage === "background") {
    if (reference.buildingOutline) note = "移动镜头，让眼前的" + reference.buildingOutline.name + "贴合取景器中的金色参考轮廓。";
    else if (reference.landmarks?.length) note = "对照两幅画面的编号找地标。白色菱形是参考位置，浅蓝点是镜头里认出的位置。";
  }
  el("subject-note").textContent = (refMirrored ? "参考照已水平翻转。" : "") + note;
}

function display(result, pendingStage) {
  latest = result;
  const previousStage = shownStage;
  shownStage = result?.stage ?? (reference && source ? pendingStage ?? stages.find(s => !skipped.has(s)) : null);
  drawGuide(el("overlay"), result, reference, shownStage);
  if (previousStage !== shownStage) showReferenceGuide();
  const index = stages.indexOf(shownStage);
  [...el("steps").children].forEach((li, i) => {
    const bypassed = skipped.has(stages[i]);
    li.className = bypassed ? "skipped" : i === index ? "active" : i < index ? "done" : "";
    li.setAttribute("aria-label", stageNames[i] + (bypassed ? "，已跳过" : ""));
    if (i === index) li.setAttribute("aria-current", "step");
    else li.removeAttribute("aria-current");
  });
  el("hold").hidden = !result || result.mode !== "hold";
  el("hold").value = result?.hold || 0;
  el("shutter").disabled = !result?.ready || !!shotRequested;
  const skipButton = el("skip-stage");
  skipButton.hidden = index < 0 || shownStage === "ready";
  if (!skipButton.hidden) skipButton.textContent = "跳过" + stageNames[index];
  el("restore-steps").hidden = !skipped.size || !reference;
  const background = shownStage === "background";
  const building = background && reference?.buildingOutline;
  const standing = shownStage === "position";
  const posing = shownStage === "pose";
  el("legend").hidden = !(background || (standing && reference?.box) ||
    (posing && (reference?.outline || reference?.points.length)));
  el("legend").dataset.stage = shownStage || "";
  el("legend").dataset.background = background ? (building ? "outline" : "rings") : "";
  const poseLabel = {person:"参考动作轮廓", joints:"简化动作轮廓"}[reference?.outline?.kind] || "参考关节";
  el("target-label").textContent = building ? building.name + "参考轮廓" : background ? "机位容差" : standing ? "目标站位" : poseLabel;
  el("current-label").textContent = background ? "地标中心" : standing ? "当前人物" : "实时关节";
  el("current-key").hidden = !(background && !building && result?.background?.tracking || standing && result?.selected ||
    posing && result?.pose?.actual.some(Boolean));
  el("scene-thumb").hidden = !background || result?.mode === "hold";
  if (result) {
    const adjustingScale = background && ["coverage", "steady", "scale"].includes(result.background?.control);
    tell(adjustingScale ? "摄影者 · 匹配景别" : actors[index], result.message, result.mode);
  }
}

function invalidate(stage, tap = null) {
  generation++;
  mirrorFrames = 0;
  pendingTap = tap;
  shotRequested = false;
  lastVideoTime = -1;
  display(null, stage);
}

function applySteps(nextStage) {
  invalidate(nextStage, pendingTap);
  if (!shownStage) {promptForInput(); return;}
  tell(actors[stages.indexOf(shownStage)], "正在读取取景画面");
  analyze();
}

el("skip-stage").onclick = () => {
  const index = stages.indexOf(shownStage);
  if (index < 0 || shownStage === "ready") return;
  skipped.add(shownStage);
  applySteps(stages.slice(index + 1).find(stage => !skipped.has(stage)));
};

el("restore-steps").onclick = () => {
  skipped.clear();
  applySteps("background");
};

function fit(element, parent, aspect) {
  const w = Math.min(parent.clientWidth, parent.clientHeight * aspect);
  element.style.width = w + "px";
  element.style.height = w / aspect + "px";
}

function layout() {
  const ratio = refPhoto.naturalWidth / refPhoto.naturalHeight;
  if (ratio) fit(refSurface, el("ref-box"), ratio);
  const sourceRatio = source === "camera" ? video.videoWidth / video.videoHeight : livePhoto.naturalWidth / livePhoto.naturalHeight;
  fit(surface, el("viewport"), ratio || sourceRatio || 4/3);
  showReferenceGuide();
  drawGuide(el("overlay"), latest, reference, shownStage);
}

const resize = new ResizeObserver(layout);
resize.observe(el("viewport"));
resize.observe(el("ref-box"));

function promptForInput() {
  if (!session) tell("准备", "请先打开已选好的参考照");
  else if (!source) tell("摄影者", "开启相机，或载入一张照片试拍");
  else {
    display(null);
    let message = "正在读取取景画面";
    if (shownStage === "background") message = reference.buildingOutline
      ? "转动镜头，让" + reference.buildingOutline.name + "贴近金色参考轮廓"
      : "让参考地标入镜，转动镜头，让青色圆心进入中央金圈";
    tell(actors[stages.indexOf(shownStage)], message);
  }
}

async function loadReference(file, tap) {
  const version = ++referenceVersion;
  const newPhoto = file !== referenceFile;
  if (newPhoto) {
    skipped.clear();
    refMirrored = false;
    manualDirection = false;
    refTap = null;
    autoFlippedSource = -1;
  }
  if (tap !== undefined) refTap = tap;
  releaseGuide(session);
  session = null;
  reference = null;
  setCameraZoom(1);
  invalidate();
  referenceFile = file;
  el("filename").textContent = file.name;
  el("viewpoint").textContent = "";
  const mirrorButton = el("mirror-ref");
  mirrorButton.disabled = true;
  mirrorButton.setAttribute("aria-pressed", String(refMirrored));
  mirrorButton.textContent = refMirrored ? "还原参考照方向" : "水平翻转参考照";
  refPhoto.style.transform = el("scene-thumb").style.transform = refMirrored ? "scaleX(-1)" : "";
  tell("参考照", "正在定位主角和地标");
  try {
    if (newPhoto) {
      if (refUrl) URL.revokeObjectURL(refUrl);
      refUrl = URL.createObjectURL(file);
      refPhoto.src = refUrl;
      el("scene-thumb").src = refUrl;
      await refPhoto.decode();
      if (version !== referenceVersion) return;
    }
    refSurface.hidden = false;
    mirrorButton.hidden = false;
    el("ref-empty").hidden = true;
    layout();
    const form = new FormData();
    form.append("reference", file);
    form.append("mirror", refMirrored ? "1" : "0");
    if (refTap) {form.append("x", refTap[0]); form.append("y", refTap[1]);}
    const data = await openGuide(form, refPhoto, message => {
      if (version === referenceVersion) tell("参考照", message);
    });
    if (version !== referenceVersion) {
      releaseGuide(data.id);
      return;
    }
    session = data.id;
    reference = data.reference;
    document.body.dataset.reference = "loaded";
    el("viewpoint").textContent = reference.viewpoint;
    layout();
    promptForInput();
  } catch (error) {
    if (version === referenceVersion) {
      document.body.dataset.reference = "empty";
      tell("参考照", error.name === "EncodingError" ? "无法打开参考照，请使用 JPG、PNG 或 WebP" : error.message, "search");
    }
  } finally {
    if (version === referenceVersion) mirrorButton.disabled = false;
  }
}

function flipReference(manual) {
  if (!referenceFile) return;
  if (manual) manualDirection = true;
  else autoFlippedSource = sourceVersion;
  refMirrored = !refMirrored;
  const box = reference?.boxes.length > 1 ? reference.box : null;
  const selected = refTap || (box && [(box[0]+box[2])/2, (box[1]+box[3])/2]);
  loadReference(referenceFile, selected ? [1-selected[0], selected[1]] : null);
}

function positionIn(event) {
  const rect = event.currentTarget.getBoundingClientRect();
  return [(event.clientX - rect.left) / rect.width, (event.clientY - rect.top) / rect.height];
}
el("choose-ref").onclick = () => el("ref-file").click();
el("mirror-ref").onclick = () => flipReference(true);
el("ref-file").onchange = e => {
  const file = e.target.files[0];
  if (file) loadReference(file);
  e.target.value = "";
};
el("ref-overlay").onclick = e => {
  if (referenceFile) loadReference(referenceFile, positionIn(e));
};
el("overlay").onclick = e => {
  if (!session || !source) return;
  invalidate();
  pendingTap = positionIn(e);
  tell("主角", "正在确认点选的人物");
};

function stopTracks() {
  if (stream) for (const track of stream.getTracks()) track.stop();
  stream = null;
  video.srcObject = null;
}

function showSource(kind) {
  source = kind;
  setCameraZoom(1);
  invalidate();
  surface.hidden = !kind;
  el("empty").hidden = !!kind;
  video.hidden = kind !== "camera";
  livePhoto.hidden = kind !== "photo";
  el("camera").textContent = kind === "camera" ? "关闭相机" : "开启相机";
  el("flip").hidden = kind !== "camera";
  layout();
  promptForInput();
}

async function startCamera() {
  if (!navigator.mediaDevices?.getUserMedia) {
    tell("相机", "请在 localhost 或 HTTPS 页面开启相机，也可以用照片试拍", "search");
    return;
  }
  const version = ++sourceVersion;
  stopTracks();
  showSource(null);
  tell("相机", "正在开启相机");
  try {
    const acquired = await navigator.mediaDevices.getUserMedia({
      audio:false, video:{facingMode:{ideal:facing}, width:{ideal:1920}, height:{ideal:1080}}
    });
    if (version !== sourceVersion) {acquired.getTracks().forEach(t => t.stop()); return;}
    stream = acquired;
    video.srcObject = acquired;
    await video.play();
    if (version !== sourceVersion) return;
    const track = acquired.getVideoTracks()[0];
    track.onended = () => {
      if (stream !== acquired) return;
      stopTracks(); showSource(null); tell("相机", "相机已断开，可以重新开启", "search");
    };
    showSource("camera");
  } catch (error) {
    if (version !== sourceVersion) return;
    stopTracks(); showSource(null);
    tell("相机", error.name === "NotAllowedError" ? "请允许浏览器使用摄像头，或用照片试拍" : "未能开启摄像头，可重新连接或用照片试拍", "search");
  }
}

el("camera").onclick = () => {
  if (!stream) {startCamera(); return;}
  ++sourceVersion;
  stopTracks(); showSource(null);
};
el("flip").onclick = () => {
  facing = facing === "environment" ? "user" : "environment";
  startCamera();
};
el("choose-live").onclick = () => el("live-file").click();
el("live-file").onchange = async e => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  const version = ++sourceVersion;
  stopTracks(); showSource(null);
  if (liveUrl) URL.revokeObjectURL(liveUrl);
  liveUrl = URL.createObjectURL(file);
  livePhoto.src = liveUrl;
  try {
    await livePhoto.decode();
    if (version === sourceVersion) showSource("photo");
  } catch {
    if (version === sourceVersion) tell("照片试拍", "无法打开试拍照片，请使用 JPG、PNG 或 WebP", "search");
  }
};

function snapshot(maxEdge) {
  const input = source === "camera" ? video : livePhoto;
  const w = input.videoWidth || input.naturalWidth, h = input.videoHeight || input.naturalHeight;
  const ratio = reference.width / reference.height;
  const cw = Math.min(w, h * ratio) / cameraZoom, ch = cw / ratio;
  const scale = Math.min(1, maxEdge / Math.max(cw, ch));
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(cw * scale));
  canvas.height = Math.max(1, Math.round(ch * scale));
  canvas.getContext("2d").drawImage(input, (w-cw)/2, (h-ch)/2, cw, ch, 0, 0, canvas.width, canvas.height);
  return canvas;
}

function jpeg(canvas, quality) {
  return new Promise((resolve, reject) => canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error("无法读取取景画面")), "image/jpeg", quality));
}

async function analyze() {
  if (busy || !session || !source || document.hidden) return;
  if (!shotRequested && source === "camera" && (video.readyState < 2 || video.currentTime === lastVideoTime)) return;
  if (source === "photo" && latest?.ready && !shotRequested && !pendingTap && performance.now() - lastAnalyzed < 650) return;
  const version = generation, id = session, tap = pendingTap, taking = shotRequested;
  shotRequested = false;
  busy = true;
  if (source === "camera") lastVideoTime = video.currentTime;
  try {
    // 快门请求分析和下载使用同一次采样，辅助线画在独立的 canvas 上。
    const frame = taking || snapshot(960);
    const blob = await jpeg(frame, taking ? .94 : .9);
    if (version !== generation) return;
    const query = new URLSearchParams({seq:++seq, source:sourceVersion, skip:[...skipped].join(",")});
    if (tap) {query.set("x", tap[0]); query.set("y", tap[1]);}
    const result = await analyzeFrame(id, query, blob);
    if (version !== generation) return;
    pendingTap = null;
    lastAnalyzed = performance.now();
    mirrorFrames = result.stage === "background" && result.background?.control === "mirror" ? mirrorFrames + 1 : 0;
    if (mirrorFrames >= 2 && !taking && !manualDirection && autoFlippedSource !== sourceVersion) {
      flipReference(false);
      return;
    }
    if (!taking && alignZoom(result)) return;
    display(result);
    if (taking && result.ready) {
      exportShot(blob);
    }
  } catch (error) {
    if (version === generation) {
      if (error.status === 404) {loadReference(referenceFile); return;}
      display(null); tell("取景", error.message, "search");
    }
  } finally {
    busy = false;
  }
}

let lastShotUrl, lastShotFile;
function exportShot(blob) {
  if (lastShotUrl) URL.revokeObjectURL(lastShotUrl);
  lastShotUrl = URL.createObjectURL(blob);
  lastShotFile = new File([blob], "校园人像-" + Date.now() + ".jpg", {type:"image/jpeg"});
  const link = el("download-shot");
  link.href = lastShotUrl;
  link.download = lastShotFile.name;
  if (onDevice) {
    el("last-shot").src = lastShotUrl;
    el("export").hidden = false;
    el("share-shot").hidden = !navigator.canShare?.({files:[lastShotFile]});
    tell("摄影者 · 拍摄", "已拍好，可保存下方的照片");
  } else {
    link.click();
    tell("摄影者 · 拍摄", "照片已保存");
  }
}
el("share-shot").onclick = async () => {
  try {await navigator.share({files:[lastShotFile]});}
  catch (error) {
    if (error.name !== "AbortError") tell("保存照片", "可以点击下载 JPEG，或长按刚拍摄的照片保存");
  }
};

el("shutter").onclick = () => {
  if (!latest?.ready || shotRequested) return;
  shotRequested = snapshot(Infinity);
  el("shutter").disabled = true;
  analyze();
};

setInterval(() => {
  if (source === "camera" && latest && (!onDevice || !busy) && performance.now() - lastAnalyzed > 1750) {
    display(null);
    tell("相机", "等待新的取景画面", "search");
  }
  analyze();
}, 110);

document.addEventListener("visibilitychange", () => {
  if (document.hidden) invalidate();
});
window.addEventListener("pagehide", () => {releaseGuide(session); stopTracks();});
window.addEventListener("pageshow", e => {if (e.persisted) location.reload();});

