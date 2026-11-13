export const onDevice = !!document.querySelector('meta[name="guide-runtime"][content="device"]');
const client = onDevice ? import("../mobile/client.js") : null;

async function json(response) {
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw Object.assign(new Error(data.error || "分析失败，请重试"), {status:response.status});
  return data;
}

export async function openGuide(form, image, progress) {
  if (onDevice) return (await client).openGuide(form, image, progress);
  return json(await fetch("/api/session", {method:"POST", body:form}));
}

export async function analyzeFrame(id, query, blob) {
  if (onDevice) return (await client).analyzeFrame(id, query, blob);
  return json(await fetch("/api/session/" + id + "/frame?" + query, {method:"POST", body:blob}));
}

export function releaseGuide(id) {
  if (!id) return;
  if (onDevice) client.then(api => api.releaseGuide(id)).catch(() => {});
  else fetch("/api/session/" + id, {method:"DELETE", keepalive:true}).catch(() => {});
}
