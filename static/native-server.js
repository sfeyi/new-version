import {nativeAndroid} from "./native-export.js";

const key = "campus-photo-computer";
let base = "";
export const getServerBase = () => base;

export function normalizeServer(value) {
  let url;
  try {url = new URL(value.trim());} catch {throw new Error("请输入完整电脑地址，例如 http://192.168.1.20:7862");}
  const host = url.hostname.toLowerCase();
  const octets = host.split(".").map(Number);
  const ipv4 = octets.length === 4 && octets.every(n => Number.isInteger(n) && n >= 0 && n < 256);
  const local = host === "localhost" || host === "[::1]" || host.endsWith(".local") ||
    /^\[(?:fc|fd|fe8|fe9|fea|feb)/.test(host) || (ipv4 && (
      octets[0] === 10 || octets[0] === 127 ||
      (octets[0] === 172 && octets[1] >= 16 && octets[1] <= 31) ||
      (octets[0] === 192 && octets[1] === 168) || (octets[0] === 169 && octets[1] === 254)));
  if (url.protocol !== "https:" && !(url.protocol === "http:" && local))
    throw new Error("请使用电脑的局域网 HTTP 地址，或 HTTPS 服务地址");
  if (url.username || url.password || url.search || url.hash)
    throw new Error("服务地址不能包含用户名、密码、查询参数或锚点");
  return url.href.replace(/\/?$/, "/");
}

export async function encodeBlob(blob) {
  if (!(blob instanceof Blob) || blob.size > 24*1024*1024) throw new Error("请选择小于 24 MB 的图片");
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result.split(",")[1]);
    reader.onerror = () => reject(new Error("无法读取图片，请重新选择"));
    reader.readAsDataURL(blob);
  });
}

export async function serverRequest(path, options = {}, server = base) {
  if (!server) throw new Error("请先在页面顶部连接电脑导拍服务");
  const http = globalThis.Capacitor?.Plugins?.CapacitorHttp;
  if (!nativeAndroid || !http) throw new Error("手机网络接口不可用，请重新打开 APP");
  const root = new URL(server), target = new URL(path, root);
  // Catalog URLs may be absolute, but images/API calls must stay on the chosen computer.
  if (target.origin !== root.origin || target.username || target.password)
    throw new Error("照片地址不属于当前电脑服务");
  let response;
  try {
    response = await http.request({method:"GET", connectTimeout:8000, readTimeout:95000,
      responseType:"json", disableRedirects:true, ...options, url:target.href});
  } catch {
    throw Object.assign(new Error("无法连接电脑，请确认服务已启动、手机与电脑在同一网络，并允许防火墙访问"), {code:"SERVER_UNREACHABLE"});
  }
  if (!(response.status > 0)) throw Object.assign(new Error("电脑连接已中断"), {code:"SERVER_UNREACHABLE"});
  if (response.status < 200 || response.status >= 300) {
    throw Object.assign(new Error(response.data?.error || "电脑服务返回错误（" + response.status + "），请重试"),
      {status:response.status});
  }
  return response;
}

export async function serverBlob(path, server = base, shouldDecode = () => true) {
  const response = await serverRequest(path, {responseType:"arraybuffer", readTimeout:30000}, server);
  if (!shouldDecode()) return null;
  const type = Object.entries(response.headers || {}).find(([name]) => name.toLowerCase() === "content-type")?.[1]?.split(";")[0] || "";
  if (!type.startsWith("image/") || typeof response.data !== "string" || response.data.length > 32*1024*1024)
    throw new Error("照片格式或大小不符合要求");
  let bytes;
  try {
    const binary = atob(response.data);
    bytes = Uint8Array.from(binary, ch => ch.charCodeAt(0));
  } catch {throw new Error("电脑返回的照片无法读取，请重试");}
  return new Blob([bytes], {type});
}

function setupConnection() {
  const section = document.createElement("section");
  section.className = "library-panel";
  section.id = "computer-connection";
  section.setAttribute("aria-label", "连接电脑导拍服务");
  section.innerHTML = `<div class="library-content">
    <form id="computer-connect" style="display:flex;gap:12px;flex-wrap:wrap;align-items:end">
      <label style="flex:1;min-width:210px">电脑服务地址
        <input id="computer-server" type="url" required placeholder="http://192.168.1.20:7862" autocomplete="url" style="width:100%">
      </label><button id="computer-connect-button" type="submit">连接电脑</button>
    </form><p id="computer-status" role="status">电脑运行导拍服务后，填写它显示的地址；手机负责取景，电脑负责识别。</p>
  </div>`;
  document.querySelector("main").prepend(section);
  const input = section.querySelector("input"), button = section.querySelector("button");
  const status = section.querySelector("#computer-status");
  let saved = "";
  try {saved = localStorage.getItem(key) || "";} catch {}
  input.value = saved;
  async function connect() {
    button.disabled = true;
    status.textContent = "正在连接电脑导拍服务…";
    try {
      const next = normalizeServer(input.value);
      const response = await serverRequest("api/health", {readTimeout:8000}, next);
      if (response.data?.status !== "ok" || response.data?.service !== "campus-photo-guide" || response.data?.version !== 1)
        throw new Error("该地址不是匹配版本的校园导拍服务，请更新并启动电脑端");
      const previous = base;
      base = next;
      input.value = next;
      try {localStorage.setItem(key, next);} catch {}
      status.textContent = "已连接电脑 · 识别与推荐在电脑运行";
      window.dispatchEvent(new CustomEvent("campus-server-changed", {detail:{base, previous}}));
    } catch (error) {
      status.textContent = error.message + (base ? "；当前仍使用之前连接的电脑" : "");
    } finally {button.disabled = false;}
  }
  section.querySelector("form").onsubmit = event => {event.preventDefault(); connect();};
  if (saved) queueMicrotask(connect);
}

if (nativeAndroid) setupConnection();
