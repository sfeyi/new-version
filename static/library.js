import {nativeAndroid} from "./native-export.js";
import {getServerBase, serverRequest, serverBlob} from "./native-server.js";
import {phoneCamera} from './mobile-ui.js';

const el = id => document.getElementById(id);
const fields = ["location_id", "person_count", "framing", "pose"];

function matches(photo, filter) {
  for (const key of fields) {
    if (filter[key] !== undefined && photo[key] !== filter[key]) return false;
  }
  return photo.viewpoint_status !== "unavailable";
}

function textScore(photo, query) {
  if (!query) return 1;
  const text = [photo.photo_id, photo.location, photo.viewpoint_group, photo.description].join(" ").toLowerCase();
  const terms = query.toLowerCase().trim().split(/\s+/).flatMap(term =>
    /^[\u3400-\u9fff]+$/.test(term) && term.length > 2 ? Array.from({length:term.length-1}, (_, i) => term.slice(i, i+2)) : [term]);
  return terms.reduce((sum, term) => sum + (term && text.includes(term) ? term.length : 0), 0);
}

export function setupLibrary(selectReference, onDevice, captureScene = null) {
  let base = nativeAndroid ? getServerBase() : new URL("../", import.meta.url).href;
  let photos = [], revision = 0;
  let renderVersion = 0, loadingImages = 0, imageQueue = [];
  let cameraActive = false;
  let connecting = null;
  const imageUrls = new Set();
  const panel = el("library-panel"), status = el("library-status");
  const setStatus = text => {status.textContent = text;};
  const imagesAllowed = () => !cameraActive || (phoneCamera && ['discover','choosing'].includes(document.body.dataset.phonePhase));
  if (nativeAndroid) el("library-connection").hidden = true;
  if (onDevice && !nativeAndroid) {
    try {base = localStorage.getItem("campus-library-server") || "";} catch {base = "";}
    el("library-server").value = base;
    el("library-connection").open = !base;
  }

  function params() {
    const data = {query:el("library-query").value.trim(), limit:5};
    for (const [id, key] of [["library-location", "location_id"], ["filter-people", "person_count"],
                            ["filter-framing", "framing"], ["filter-pose", "pose"]]) {
      const value = el(id).value;
      if (value) data[key] = key === "person_count" ? Number(value) : value;
    }
    return data;
  }

  async function json(path, init = {}) {
    if (!base) throw new Error("请先连接图库服务，或直接打开本地参考照");
    if (nativeAndroid) return (await serverRequest(path, {method:init.method || "GET",
      headers:init.headers || {}, ...(init.body ? {data:JSON.parse(init.body)} : {})}, base)).data;
    const response = await fetch(new URL(path, base), {...init, signal:AbortSignal.timeout(95000)});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "图库服务暂不可用");
    return data;
  }

  function clearImages() {
    renderVersion++;
    imageQueue = [];
    for (const url of imageUrls) URL.revokeObjectURL(url);
    imageUrls.clear();
  }

  function loadImages() {
    // Camera preview takes priority over background gallery traffic and image decoding.
    while (imagesAllowed() && loadingImages < 1 && imageQueue.length) {
      const item = imageQueue.shift();
      const {img, path, server, version} = item;
      if (version !== renderVersion) continue;
      loadingImages++;
      serverBlob(path, server, () => imagesAllowed() && version === renderVersion).then(blob => {
        if (version !== renderVersion || !img.isConnected) return;
        if (!blob || !imagesAllowed()) {imageQueue.unshift(item); return;}
        const url = URL.createObjectURL(blob);
        imageUrls.add(url);
        img.src = url;
      }).catch(() => {
        if (version === renderVersion) img.alt += "（图片加载失败，可点击重试）";
      }).finally(() => {loadingImages--; loadImages();});
    }
  }

  function render(rows) {
    if (phoneCamera) rows=rows.slice(0,5);
    clearImages();
    const container = el("library-results");
    container.replaceChildren();
    const displayBase = base;
    let selected=null, selecting=false;
    const currentRender=renderVersion;
    const focus=button=>{
      if (!button || selected===button) return;
      selected=button;
      container.querySelectorAll('.photo-card').forEach(card=>card.classList.toggle('is-current',card===button));
      if(el('use-reference'))el('use-reference').disabled=selecting;
    };
    if(phoneCamera && el('use-reference')) {
      el('use-reference').disabled=true;
      el('use-reference').onclick=()=>selected?.choosePhoto();
    }
    if(phoneCamera) container.onscroll=()=>{
      const middle=container.scrollLeft+container.clientWidth/2;
      const cards=[...container.querySelectorAll('.photo-card')];
      focus(cards.sort((a,b)=>Math.abs(a.offsetLeft+a.offsetWidth/2-middle)-Math.abs(b.offsetLeft+b.offsetWidth/2-middle))[0]);
    };
    let offset = 0;
    const more = document.createElement("button");
    more.type = "button";
    more.textContent = "查看更多参考照";
    more.className = "library-more";
    const append = () => {
      more.remove();
      for (const photo of rows.slice(offset, offset+12)) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "photo-card";
        const img = document.createElement("img");
        img.loading = "lazy";
        img.decoding = "async";
        img.alt = photo.description || photo.viewpoint || photo.photo_id;
        if (nativeAndroid) imageQueue.push({img, path:photo.thumbnail_url || photo.image_url, server:displayBase, version:renderVersion});
        else img.src = new URL(photo.thumbnail_url || photo.image_url, displayBase).href;
        const caption = document.createElement("span");
        const title = document.createElement("strong");
        title.textContent = phoneCamera ? photo.location : photo.photo_id + " · " + photo.location;
        const description = document.createElement("span");
        description.textContent = phoneCamera && Number.isFinite(photo.aesthetic_score) ? '美学 '+photo.aesthetic_score.toFixed(1)+' / 10' : (photo.viewpoint_group || photo.description || "选作参考照").replaceAll("_", " / ");
        caption.append(title, description);
        button.append(img, caption);
        button.choosePhoto = async () => {
          if(selecting)return;
          selecting=true;
          if(phoneCamera)el('use-reference').disabled=true;
          const token = ++revision;
          button.disabled = true;
          setStatus("正在打开 " + photo.photo_id);
          try {
            let blob;
            if (nativeAndroid) {
              // A thumbnail must never become the pose reference or the eventual captured photo.
              blob = !photo.thumbnail_url && img.src.startsWith("blob:") ? await (await fetch(img.src)).blob() : await serverBlob(photo.image_url, displayBase);
            } else {
              const response = await fetch(new URL(photo.image_url, displayBase), {signal:AbortSignal.timeout(30000)});
              if (!response.ok) throw new Error("无法读取推荐照片，请重新检索");
              blob = await response.blob();
            }
            if (blob.size > 24*1024*1024 || !blob.type.startsWith("image/")) throw new Error("照片格式或大小不符合要求");
            if (token !== revision) return;
            const suffix = {"image/webp":"webp", "image/png":"png"}[blob.type] || "jpg";
            const file = new File([blob], photo.photo_id + "." + suffix, {type:blob.type});
            panel.open = false;
            const outcome=await selectReference(file, photo.viewpoint || "");
            if(outcome?.error)throw new Error(outcome.error);
            if (token === revision) setStatus("已选择 " + photo.photo_id + "，可继续挑选其他参考照");
          } catch (error) {
            if (token === revision) setStatus(error.message || "读取照片失败，请重试");
          } finally {
            selecting=false;button.disabled=false;
            if(phoneCamera && currentRender===renderVersion)el('use-reference').disabled=!selected;
          }
        };
        button.onclick=phoneCamera ? ()=>{focus(button);button.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion:reduce)').matches?'instant':'smooth',block:'nearest',inline:'center'});} : button.choosePhoto;
        container.append(button);
        if(phoneCamera && !selected)focus(button);
      }
      offset += 12;
      if (offset < rows.length) container.append(more);
      if (nativeAndroid) loadImages();
    };
    more.onclick = append;
    append();
    if(phoneCamera) {
      container.scrollLeft=0;
      window.dispatchEvent(new CustomEvent('campus-recommendations',{detail:{count:rows.length}}));
      loadImages();
    }
  }

  function browse() {
    ++revision;
    const data = params();
    const rows = photos.filter(p => matches(p, data)).map(p => ({p, score:textScore(p, data.query)}))
      .filter(row => row.score > 0).sort((a, b) => b.score-a.score || a.p.photo_id.localeCompare(b.p.photo_id)).map(row => row.p);
    render(rows);
    const unknown = photos.filter(p => fields.some(key => data[key] !== undefined && p[key] == null)).length;
    setStatus("图库筛选 · " + rows.length + " 张" + (unknown ? "；另有 " + unknown + " 张的条件标签待确认" : "") +
      (rows.length ? "，点照片开始导拍" : "。可减少条件或打开本地照片"));
  }

  async function connect() {
    const token = ++revision;
    photos = [];
    render([]);
    setStatus("正在连接校园图库");
    try {
      const data = await json("api/library");
      if (token !== revision) return;
      photos = data.photos;
      const select = el("library-location");
      select.replaceChildren(new Option("全部地点", ""));
      for (const location of data.locations) select.add(new Option(location.name, location.id));
      el("library-count").textContent = "· " + data.count + " 张";
      if (onDevice && !nativeAndroid) {try {localStorage.setItem("campus-library-server", base);} catch {}}
      if(phoneCamera) setStatus('输入风格，寻找适合当前场景的参考照');
      else browse();
    } catch (error) {
      if (token === revision) setStatus(error instanceof TypeError ? "无法连接图库，请检查服务地址；也可以直接打开参考照" : error.message);
    }
  }

  el("library-search").onsubmit = event => {event.preventDefault(); browse();};
  el("library-location").onchange = browse;
  el("library-connect").onsubmit = event => {
    event.preventDefault();
    try {
      const url = new URL(el("library-server").value.trim());
      if (url.protocol !== "https:" && !(url.protocol === "http:" && ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname)))
        throw new Error("手机相机页面请连接 HTTPS 服务；电脑本机可以使用 localhost");
      if (url.username || url.password || url.search || url.hash) throw new Error("请填写服务根地址");
      base = url.href.replace(/\/?$/, "/");
      connect();
    } catch (error) {setStatus(error.message);}
  };
  const recommend = async () => {
    // An automatic search after connecting waits for catalog initialization.
    await connecting;
    const token = ++revision;
    el("recommend").disabled = true;
    setStatus("正在挑选参考照，首次加载可能稍久");
    try {
      const payload=params();
      if(phoneCamera && captureScene)payload.scene_image=await captureScene();
      if(token!==revision)return;
      const data = await json("api/recommend", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload)});
      if (token !== revision) return;
      if(payload.scene_image && !data.scene_used) throw new Error('请更新并重启电脑服务，以使用场景检索');
      render(data.recommendations);
      const reason = {no_confirmed_matches:"没有满足条件的已确认照片，可减少筛选条件", features_unavailable:"推荐特征尚未准备，可先筛选图库"};
      setStatus(data.recommendations.length ? (phoneCamera ? '场景与风格匹配 · 按美学优选' : "AI 推荐 · " + data.recommendations.length + " 张，点照片开始导拍") :
        reason[data.reason] || (data.missing_images?.length ? "推荐照片不在当前图库，请核对两端图库版本" : "暂无推荐照片"));
    } catch (error) {
      if (token === revision) setStatus(error instanceof TypeError ? "连接推荐服务失败，可先筛选图库" : error.message);
    } finally {el("recommend").disabled = false;}
  };
  el("recommend").onclick=recommend;
  const changeServer = () => {base = getServerBase(); connecting=connect();};
  const cameraActivity = ({detail}) => {
    cameraActive = !!detail.active;
    if (cameraActive) panel.open = false;
    else loadImages();
  };
  if (nativeAndroid) window.addEventListener("campus-server-changed", changeServer);
  if (nativeAndroid) window.addEventListener("campus-camera-activity", cameraActivity);
  if (base) connecting=connect();
  else setStatus(nativeAndroid ? "先点左上角设置，连接电脑" : "连接图库服务后可以检索参考照，也可以直接从相册打开照片");
  window.addEventListener("pagehide", () => {
    clearImages();
    window.removeEventListener("campus-server-changed", changeServer);
    window.removeEventListener("campus-camera-activity", cameraActivity);
  }, {once:true});
  // The camera calls this when choosing a local file; it cancels selection, not the library UI.
  const cancel=() => {++revision;};
  cancel.recommend=recommend;
  return cancel;
}
