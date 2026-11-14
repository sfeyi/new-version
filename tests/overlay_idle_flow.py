"""Compare packaged 1.0.1 with the current UI: idle overlay work and disconnection.

Same synthetic camera and reference on both versions. This measures drawing
requests, not real-phone performance. HTTP data is controlled to isolate UI work.
"""
import base64
import functools
import io
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from zipfile import ZipFile

from PIL import Image
from playwright.sync_api import expect, sync_playwright

from native_app_flow import ROOT, BRIDGE, Site, SW_SPY, NATIVE_GLOBALS, PLUGIN_EXPORT, browser_path, upload
from preview_priority_flow import PREVIEW_SPY

DRAW_SPY = """
window.drawQA = {paints:0};
const clear = CanvasRenderingContext2D.prototype.clearRect;
CanvasRenderingContext2D.prototype.clearRect = function(...args) {
  if (this.canvas.id === 'overlay') drawQA.paints++;
  return clear.apply(this,args);
};
"""


def response(options):
    url = options["url"]
    data = {}
    if url.endswith("/api/health"):
        data = {"status":"ok", "service":"campus-photo-guide", "version":1}
    elif url.endswith("/api/library"):
        data = {"photos":[], "locations":[], "count":0}
    elif url.endswith("/api/session"):
        data = {"id":"controlled", "reference":{
            "width":720, "height":960, "boxes":[], "box":None, "points":[], "edges":[],
            "viewpoint":"", "subjectHint":"", "landmarks":[], "aimPoint":[.5,.5],
            "outline":None,"buildingOutline":None,"backgroundOutline":None}}
    elif "/frame?" in url:
        data = {"stage":"background", "background":None, "boxes":[], "selected":None,
                "completed":[], "checks":[False]*4, "pose":None, "ready":False, "mode":"search",
                "hold":0, "message":"等待匹配参考背景"}
    return {"status":200, "headers":{}, "data":data, "url":url}


def main():
    qa = ROOT / "build/qa"
    baseline = qa / "overlay-baseline-1.0.1"
    baseline.mkdir(parents=True, exist_ok=True)
    with ZipFile(ROOT / "build/apk/campus-photo-guide-1.0.1.apk") as apk:
        for entry in apk.namelist():
            if not entry.startswith("assets/public/") or entry.endswith("/"):
                continue
            target = (baseline / entry.removeprefix("assets/public/")).resolve()
            assert target.is_relative_to(baseline.resolve())
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(apk.read(entry))
    photo = next(ROOT.parent.glob("校园摄影*/**/REF_029.webp"))
    with Image.open(photo) as original:
        original.thumbnail((720,720))
        buf = io.BytesIO()
        original.convert("RGB").save(buf,"JPEG")
    data_url = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    report = {"scope":"Same synthetic camera, controlled HTTP; drawing work, not Xiaomi FPS"}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=browser_path())
        for name, directory in (("1.0.1",baseline), ("current",ROOT / "build/android-web")):
            server = ThreadingHTTPServer(("127.0.0.1",0),functools.partial(Site,directory=directory))
            Thread(target=server.serve_forever,daemon=True).start()
            errors = []
            try:
                context = browser.new_context(viewport={"width":390,"height":844},device_scale_factor=3,is_mobile=True)
                context.expose_binding("qaNativeHttp", lambda source, options: response(options))
                context.add_init_script(SW_SPY + PREVIEW_SPY + DRAW_SPY +
                    "window.previewPhoto="+json.dumps(data_url)+";previewQA.delay=12000;"+
                    NATIVE_GLOBALS.replace("window.qaNativeHttp(call.options)", "window.previewRequest(call.options)")+
                    BRIDGE.read_text(encoding="utf-8")+PLUGIN_EXPORT)
                page = context.new_page()
                page.on("pageerror",lambda error:errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{server.server_port}/")
                if page.locator("#guide-mode").count():
                    page.locator("#guide-mode").select_option("live")
                page.locator("#computer-server").fill("http://127.0.0.1:7862")
                page.locator("#computer-connect-button").click()
                expect(page.locator("#computer-status")).to_contain_text("已连接电脑")
                page.locator("#ref-file").set_input_files(upload(photo))
                expect(page.locator("body")).to_have_attribute("data-reference","loaded")
                page.locator("#camera").click()
                expect(page.locator("#source-state")).to_have_text("相机取景")
                page.wait_for_timeout(1000)
                page.evaluate("drawQA.paints=0")
                page.wait_for_timeout(3000)
                paints = page.evaluate("drawQA.paints")
                report[name] = {"unchanged_ring_paints_in_3s":paints,
                    "canvas_pixels":page.evaluate("overlay.width*overlay.height"),
                    "video_time":round(page.evaluate("video.currentTime"),2)}
                if name == "1.0.1":
                    assert paints > 60, paints
                else:
                    assert paints <= 2, paints
                    # Hide only the guide layer: analysis remains active and video continues.
                    page.locator("#show-guides").uncheck()
                    expect(page.locator("#overlay")).to_be_hidden()
                    assert page.locator("#pause-analysis").get_attribute("aria-pressed") == "false"
                    # Fail the pending transfer and ensure it does not start an endless retry loop.
                    page.evaluate("""() => {
                      window.qaNativeHttp = async () => {throw new Error('offline');};
                      previewQA.delay=0;
                    }""")
                    expect(page.locator("#pause-analysis")).to_have_text("继续识别",timeout=16000)
                    expect(page.locator("#hint")).to_contain_text("电脑连接已中断")
                    before=page.evaluate("({frames:previewQA.frames.length,time:video.currentTime})")
                    page.wait_for_timeout(2200)
                    assert page.evaluate("previewQA.frames.length") == before["frames"]
                    assert page.evaluate("video.currentTime")-before["time"] > 1.4
                    report[name]["disconnect_stops_capture_retries"] = True
                    report[name]["preview_continues_offline"] = True
                    page.screenshot(path=qa / "overlay-offline.png", full_page=True)
                assert not errors, errors
                context.close()
            finally:
                server.shutdown();server.server_close()
        browser.close()
    assert report["current"]["canvas_pixels"]*3 < report["1.0.1"]["canvas_pixels"]
    report["passed"]=True
    (qa / "overlay-idle-comparison.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,indent=2),flush=True)


if __name__ == "__main__":
    main()
