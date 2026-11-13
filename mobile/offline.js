const banner = document.getElementById("offline");
const button = document.getElementById("offline-download");
const label = document.getElementById("offline-status");
banner.hidden = false;
let registration, preparing, ready = false;
const mb = value => Math.ceil(value / 1000000) + " MB";

async function service() {
  if (!("serviceWorker" in navigator) || !window.isSecureContext) {
    throw new Error("请在手机 Chrome 或 Safari 中通过 HTTPS 测试链接打开");
  }
  if (!registration) {
    const reg = await navigator.serviceWorker.register(new URL("../sw.js", import.meta.url));
    const worker = reg.installing || reg.waiting;
    if (!reg.active && worker) {
      await new Promise((resolve, reject) => {
        function changed() {
          if (worker.state === "activated") {worker.removeEventListener("statechange", changed); resolve();}
          if (worker.state === "redundant") {
            worker.removeEventListener("statechange", changed);
            reject(new Error("离线组件下载失败，请检查网络后重试"));
          }
        }
        worker.addEventListener("statechange", changed);
        changed();
      });
    }
    registration = await navigator.serviceWorker.ready;
  }
  return navigator.serviceWorker.controller || registration.active;
}

async function communicate(action) {
  const worker = await service();
  return new Promise((resolve, reject) => {
    const channel = new MessageChannel();
    channel.port1.onmessage = ({data}) => {
      if (data.error) {channel.port1.close(); reject(new Error(data.error)); return;}
      if (data.done) {
        ready = true;
        label.textContent = "离线包已就绪 · 可断网取景";
        button.hidden = true;
      } else if (action === "prepare") {
        label.textContent = "正在下载离线包 · " + mb(data.bytes) + " / " + mb(data.total);
      } else {
        label.textContent = "外拍前请在 Wi-Fi 下准备离线包（" + mb(data.total) + "）";
      }
      if (data.done || action === "status") {channel.port1.close(); resolve();}
    };
    worker.postMessage(action, [channel.port2]);
  });
}

export async function prepareOffline() {
  if (ready) return;
  if (!preparing) {
    button.disabled = true;
    label.textContent = "正在检查离线文件";
    preparing = communicate("prepare").catch(error => {
      label.textContent = error.message;
      button.textContent = "继续下载";
      throw error;
    }).finally(() => {preparing = null; button.disabled = false;});
  }
  return preparing;
}

button.onclick = () => prepareOffline().catch(() => {});
communicate("status").catch(error => {label.textContent = error.message;});
