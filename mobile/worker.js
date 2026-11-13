import {loadPyodide} from "../vendor/pyodide/pyodide.mjs";
import * as ort from "../vendor/ort/ort.wasm.min.mjs";

const root = new URL("../", import.meta.url);
ort.env.wasm.numThreads = 1;
ort.env.wasm.proxy = false;
ort.env.wasm.wasmPaths = new URL("vendor/ort/", root).href;
const networks = new Map();
const labels = {"yolox-tiny.onnx":"定位人物", "rtmpose-m.onnx":"提取关节", "pphumanseg.onnx":"描绘人物轮廓",
  "segformer-b0-ade-q8.onnx":"识别地标轮廓", "xfeat.onnx":"匹配背景"};
let loading, activeId, preparingReference;
function status(message) {
  self.postMessage({id:activeId, status:message});
}

self.runGuideModel = async (name, array, shape, outputs) => {
  // Copy before the first await. Python owns the source buffer and its borrowed proxy.
  const buffer = array.getBuffer("f32");
  let input;
  try {
    input = new ort.Tensor("float32", buffer.data.slice(), JSON.parse(shape));
  } finally {
    buffer.release();
  }
  if (preparingReference) status("正在" + labels[name]);
  let session = networks.get(name);
  if (!session) {
    session = await ort.InferenceSession.create(new URL("models/" + name, root).href, {
      executionProviders:["wasm"], enableCpuMemArena:false, enableMemPattern:false
    });
    networks.set(name, session);
  }
  const names = JSON.parse(outputs) || session.outputNames;
  const result = await session.run({[session.inputNames[0]]:input}, names);
  return names.map(key => {
    const tensor = result[key];
    if (tensor.type === "int64") {
      return {dims:tensor.dims, dtype:"int32", data:Int32Array.from(tensor.data, Number)};
    }
    return {dims:tensor.dims, dtype:tensor.type, data:tensor.data};
  });
};

async function initialize() {
  status("正在启动本机识别引擎");
  const py = await loadPyodide({indexURL:new URL("vendor/pyodide/", root).href});
  await py.loadPackage(["numpy", "opencv-python"]);
  const response = await fetch(new URL("guide.zip", root));
  if (!response.ok) throw new Error("无法打开导拍程序，请重新下载离线包");
  py.unpackArchive(await response.arrayBuffer(), "zip", {extractDir:"/guide"});
  py.runPython("import sys\nsys.path.insert(0, '/guide')\nimport device");
  return py.pyimport("device");
}

const clocks = new Map();
async function handle({id, operation, sid, pixels, width, height, options}) {
  activeId = id;
  preparingReference = operation === "open";
  if (!loading) loading = initialize().catch(error => {loading = null; throw error;});
  const device = await loading;
  if (operation === "close") {
    device.close(sid);
    clocks.delete(sid);
    return null;
  }
  if (operation === "open") {
    return JSON.parse(await device.open_reference(sid, pixels, width, height, JSON.stringify(options)));
  }
  const start = performance.now() / 1000;
  const previous = clocks.get(sid);
  // Count consecutive observations on slow phones. An actual pause between requests
  // still triggers the same 1.6 s reset as on desktop.
  const idle = previous ? start - previous.finished : 0;
  options.now = previous ? previous.time + (idle > 1.6 ? idle : Math.min(.5, start - previous.started)) : 0;
  const result = await device.analyze(sid, pixels, width, height, JSON.stringify(options));
  clocks.set(sid, {started:start, finished:performance.now()/1000, time:options.now});
  return JSON.parse(result);
}

// A new reference or a tap can arrive during inference. Never mutate two guides concurrently.
let queue = Promise.resolve();
self.onmessage = ({data}) => {
  queue = queue.then(async () => {
    try {
      self.postMessage({id:data.id, result:await handle(data)});
    } catch (error) {
      console.error(error);
      self.postMessage({id:data.id, error:"本机识别失败：" + (error.message || error).toString().trim().split("\n").pop()});
    }
  });
};
