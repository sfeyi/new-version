// The same UI also runs in a browser; only the packaged Android app has this bridge.
export const nativeAndroid = document.querySelector('meta[name="guide-native"]')?.content === "android" &&
  globalThis.Capacitor?.getPlatform?.() === "android";
// Capacitor's Android bridge injects plugin proxies before loading this page.
// registerPlugin belongs to the optional npm JS runtime, which this plain UI does not bundle.
const photos = nativeAndroid ? globalThis.Capacitor.Plugins?.PhotoExport : null;

async function payload(file) {
  if (!file || file.type !== "image/jpeg" || file.size > 24*1024*1024) throw new Error("请保存小于 24 MB 的 JPEG 照片");
  const data = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result.split(",")[1]);
    reader.onerror = () => reject(new Error("无法读取本次照片，请重试"));
    reader.readAsDataURL(file);
  });
  return {base64:data, filename:"campus-photo-" + Date.now() + ".jpg"};
}

export async function saveNativePhoto(file) {
  if (!photos) throw new Error("原生保存接口不可用");
  return photos.savePhoto(await payload(file));
}

export async function shareNativePhoto(file) {
  if (!photos) throw new Error("原生分享接口不可用");
  return photos.sharePhoto(await payload(file));
}
