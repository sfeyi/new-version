"""Real browser/video/Worker + PC inference; delay the native HTTP boundary.

Checks that slow recognition does not pause the video, queue frames or resurrect
stale guidance. Synthetic camera, not evidence of Xiaomi hardware frame rate.
"""
import base64
import functools
import io
import json
import sys
from http.server import ThreadingHTTPServer
from threading import Thread

from PIL import Image, ImageOps
from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

from native_app_flow import (ROOT, BRIDGE, Site, QuietServer, SW_SPY,
                             NATIVE_GLOBALS, PLUGIN_EXPORT, browser_path, native_http, upload)


PREVIEW_SPY = r"""
window.previewQA = {frames:[], active:0, peak:0, delay:0, ticks:0, cameraRequests:[]};
setInterval(()=>previewQA.ticks++, 50);
window.previewRequest = async options => {
  if (options.url.endsWith('/thumbnail')) await new Promise(r=>setTimeout(r,300));
  if (!options.url.includes('/frame?')) return qaNativeHttp(options);
  const frame = {started:performance.now(), data:options.data};
  previewQA.frames.push(frame);
  previewQA.active++; previewQA.peak=Math.max(previewQA.peak,previewQA.active);
  try {
    await new Promise(r=>setTimeout(r,previewQA.delay));
    const result = await qaNativeHttp(options);
    frame.ended = performance.now(); frame.timing = result.data.timing;
    return result;
  } finally {previewQA.active--;}
};
Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {value:async options=>{
  previewQA.cameraRequests.push(options);
  const image = new Image(); image.src = window.previewPhoto;
  await image.decode();
  const canvas = document.createElement('canvas');
  canvas.width=1280; canvas.height=720;
  const context = canvas.getContext('2d');
  const width = 720*image.naturalWidth/image.naturalHeight;
  let n = 0;
  const paint = () => {
    context.fillStyle='#1a2722'; context.fillRect(0,0,1280,720);
    context.drawImage(image,(1280-width)/2,0,width,720);
    context.fillStyle = n++%2 ? '#ffffff' : '#000000';
    context.fillRect(640,8,10,10);
  };
  paint();
  const timer=setInterval(paint,1000/30);
  const stream=canvas.captureStream(30);
  const track=stream.getVideoTracks()[0], stop=track.stop.bind(track);
  track.stop=()=>{clearInterval(timer);stop();};
  return stream;
}});
"""


def main():
    photo = next(ROOT.parent.glob("校园摄影*/**/REF_029.webp"))
    with Image.open(photo) as original:
        image = ImageOps.exif_transpose(original).convert("RGB")
        image.thumbnail((720, 720))
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=90)
    data_url = "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()
    qa = ROOT / "build/qa"
    qa.mkdir(parents=True, exist_ok=True)
    site = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Site, directory=ROOT / "build/android-web"))
    sys.path.insert(0, str(ROOT))
    from app import app
    computer = make_server("127.0.0.1", 0, app, threaded=True, request_handler=QuietServer)
    for server in (site, computer):
        Thread(target=server.serve_forever, daemon=True).start()
    report = {"passed": False, "scope": "Synthetic 30 FPS video, real Worker and PC models, delayed native HTTP; not a physical Android benchmark"}
    errors = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=browser_path())
            context = browser.new_context(viewport={"width":390,"height":844}, is_mobile=True, has_touch=True)
            context.expose_binding("qaNativeHttp", lambda source, options: native_http(options))
            context.add_init_script(SW_SPY + PREVIEW_SPY + "window.previewPhoto=" + json.dumps(data_url) + ";" +
                                    NATIVE_GLOBALS.replace("window.qaNativeHttp(call.options)", "window.previewRequest(call.options)") +
                                    BRIDGE.read_text(encoding="utf-8") + PLUGIN_EXPORT)
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{site.server_port}/")
            page.locator("#guide-mode").select_option("live")
            page.locator("#computer-server").fill(f"http://127.0.0.1:{computer.server_port}")
            page.locator("#computer-connect-button").click()
            expect(page.locator(".photo-card").first).to_be_visible(timeout=30000)
            page.locator("#camera").click()
            expect(page.locator("#source-state")).to_have_text("相机取景")
            page.wait_for_timeout(1000)
            before = page.evaluate("({time:video.currentTime, images:nativeQA.httpCalls.filter(c=>c.url.endsWith('/thumbnail')).length})")
            page.wait_for_timeout(2200)
            assert page.evaluate("previewQA.frames.length") == 0, "No reference must mean no frame analysis"
            assert page.evaluate("video.currentTime") - before["time"] > 1.5
            assert page.evaluate("nativeQA.httpCalls.filter(c=>c.url.endsWith('/thumbnail')).length") == before["images"]
            gallery_count = before["images"]
            constraints = page.evaluate("previewQA.cameraRequests[0].video")
            assert constraints["height"]["ideal"] == 720 and constraints["frameRate"]["max"] == 30
            report["no_reference_sends_no_frames"] = True
            report["gallery_loading_pauses_during_preview"] = True

            page.locator("#ref-file").set_input_files(upload(photo))
            expect(page.locator("body")).to_have_attribute("data-reference", "loaded", timeout=90000)
            page.wait_for_function("previewQA.frames.filter(f=>f.ended).length >= 4", timeout=45000)
            assert page.evaluate("window.__guidePerformance.worker"), "Actual image preparation Worker must run"
            frames = page.evaluate("previewQA.frames.filter(f=>f.ended)")
            intervals = [b["started"]-a["started"] for a,b in zip(frames,frames[1:])]
            # Animation phases may introduce longer gaps; preparation jitter allows a small margin.
            assert min(intervals) > 900, intervals
            sizes = []
            for frame in frames:
                with Image.open(io.BytesIO(base64.b64decode(frame["data"], validate=True))) as sample:
                    assert max(sample.size) <= 640 and sample.format == "JPEG"
                    sizes.append(list(sample.size))
                assert frame["timing"]["serverMs"] >= frame["timing"]["analyzeMs"]
            report.update(worker_runs=True, sample_dimensions=sizes, min_interval_ms=round(min(intervals)), peak_inflight=page.evaluate("previewQA.peak"))
            assert report["peak_inflight"] == 1

            page.evaluate("previewQA.delay=5000; window.slowStart=previewQA.frames.length")
            page.wait_for_function("previewQA.frames.length>slowStart && previewQA.active===1", timeout=15000)
            before = page.evaluate("({time:video.currentTime,ticks:previewQA.ticks,frames:previewQA.frames.length})")
            page.wait_for_timeout(1800)
            after = page.evaluate("({time:video.currentTime,ticks:previewQA.ticks,frames:previewQA.frames.length})")
            assert after["time"]-before["time"] > 1.2, (before,after)
            assert after["ticks"]-before["ticks"] > 20, (before,after)
            assert after["frames"] == before["frames"], "Do not enqueue frames while the computer is busy"
            report["video_seconds_during_1_8s_wait"] = round(after["time"]-before["time"],2)
            expect(page.locator("#hint")).to_contain_text("等待较新的识别结果", timeout=15000)
            expect(page.locator("#shutter")).to_be_disabled()
            report["stale_results_do_not_enable_capture"] = True

            page.locator("#pause-analysis").click()
            expect(page.locator("#pause-analysis")).to_have_text("继续识别")
            expect(page.locator("#legend")).to_be_hidden()
            count = page.evaluate("previewQA.frames.length")
            time_before = page.evaluate("video.currentTime")
            page.wait_for_timeout(2500)
            assert page.evaluate("previewQA.frames.length") == count
            assert page.evaluate("video.currentTime") - time_before > 1.5
            assert page.evaluate("window.__guidePerformance.analysisPaused")
            report["pause_stops_sampling_but_not_video"] = True
            page.evaluate("previewQA.delay=0")
            page.locator("#pause-analysis").click()
            page.wait_for_function("n=>previewQA.frames.length>n", arg=count, timeout=15000)
            report["resume_samples_new_frames"] = True
            page.screenshot(path=qa / "preview-priority.png", full_page=True)

            # Verify supported fallback without a Worker remains low frequency and functional.
            page.locator("#camera").click()
            page.wait_for_function("n=>nativeQA.httpCalls.filter(c=>c.url.endsWith('/thumbnail')).length>n", arg=gallery_count, timeout=15000)
            page.reload()
            page.locator("#guide-mode").select_option("live")
            page.evaluate("Object.defineProperty(window,'Worker',{value:undefined})")
            page.locator("#ref-file").set_input_files(upload(photo))
            expect(page.locator("body")).to_have_attribute("data-reference", "loaded", timeout=90000)
            page.locator("#camera").click()
            page.wait_for_function("previewQA.frames.some(f=>f.ended)", timeout=30000)
            assert not page.evaluate("window.__guidePerformance.worker")
            report["fallback_works_without_worker"] = True
            assert not errors, errors
            report["page_errors"] = errors
            report["passed"] = True
            context.close()
            browser.close()
    finally:
        for server in (site, computer):
            server.shutdown()
            server.server_close()
        (qa / "preview-priority-results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
