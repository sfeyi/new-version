import {nativeAndroid} from "./native-export.js";
import {encodeBlob, getServerBase, serverRequest} from "./native-server.js";

export const onDevice = !!document.querySelector('meta[name="guide-runtime"][content="device"]');
const client = onDevice ? import("../mobile/client.js") : null;
const sessionServers = new Map();

async function json(response) {
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw Object.assign(new Error(data.error || "分析失败，请重试"), {status:response.status});
  return data;
}

export async function openGuide(form, image, progress) {
  if (nativeAndroid) {
    const base = getServerBase();
    if (!base) throw new Error("请在左上角设置中连接电脑");
    const entries = [{key:"client", type:"string", value:"android-preview"}];
    for (const [key, value] of form) {
      if (value instanceof Blob) entries.push({key, type:"base64File", value:await encodeBlob(value),
        fileName:"reference." + ({"image/png":"png", "image/webp":"webp"}[value.type] || "jpg"), contentType:value.type});
      else entries.push({key, type:"string", value});
    }
    progress?.("电脑正在定位参考照中的主角和地标");
    const response = await serverRequest("api/session", {method:"POST", dataType:"formData",
      headers:{"Content-Type":"multipart/form-data"}, data:entries}, base);
    // Keep the server binding even if a new connection was selected while this request ran.
    sessionServers.set(response.data.id, base);
    return response.data;
  }
  if (onDevice) return (await client).openGuide(form, image, progress);
  return json(await fetch("/api/session", {method:"POST", body:form}));
}

export async function analyzeFrame(id, query, blob, encoded = null) {
  if (nativeAndroid) {
    const base = sessionServers.get(id);
    if (!base) throw Object.assign(new Error("参考会话已过期，请重新载入参考照"), {status:404});
    const response = await serverRequest("api/session/" + encodeURIComponent(id) + "/frame?" + query,
      {method:"POST", dataType:"file", readTimeout:10000, headers:{"Content-Type":"image/jpeg"}, data:encoded || await encodeBlob(blob)}, base);
    return response.data;
  }
  if (onDevice) return (await client).analyzeFrame(id, query, blob);
  return json(await fetch("/api/session/" + id + "/frame?" + query, {method:"POST", body:blob}));
}

export function releaseGuide(id) {
  if (!id) return;
  if (nativeAndroid) {
    const base = sessionServers.get(id);
    sessionServers.delete(id);
    if (base) serverRequest("api/session/" + encodeURIComponent(id), {method:"DELETE", readTimeout:5000}, base).catch(() => {});
  }
  else if (onDevice) client.then(api => api.releaseGuide(id)).catch(() => {});
  else fetch("/api/session/" + id, {method:"DELETE", keepalive:true}).catch(() => {});
}
