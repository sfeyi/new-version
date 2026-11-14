// Image preparation only. Detection, pose and matching still run on the computer.
let canvas;
self.onmessage = async ({data: {id, bitmap, quality}}) => {
  try {
    const started = performance.now();
    if (!canvas || canvas.width !== bitmap.width || canvas.height !== bitmap.height)
      canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
    canvas.getContext("2d", {alpha:false}).drawImage(bitmap, 0, 0);
    bitmap.close();
    const blob = await canvas.convertToBlob({type:"image/jpeg", quality});
    const base64 = new FileReaderSync().readAsDataURL(blob).split(",")[1];
    self.postMessage({id, blob, base64, workerMs:performance.now()-started});
  } catch (error) {
    bitmap?.close();
    self.postMessage({id, error:error.message || "图片准备失败"});
  }
};
