"""Exercise real UI/export code with a simulated camera and guide responses.

python tests/photo_controls_flow.py [--executable path/to/chrome.exe]
Requires Pillow and Playwright. Does not verify real autofocus or AI inference.
"""
import argparse
import io
import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread

from PIL import Image
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "build" / "photo-controls-test"

CAMERA = """
window.focusCalls=[]; window.focusEnabled=true; window.focusBehavior='normal';
Object.defineProperty(navigator.mediaDevices,'getSupportedConstraints', {
  value:()=>({focusMode:true,focusDistance:true,pointsOfInterest:true})});
Object.defineProperty(navigator.mediaDevices,'getUserMedia',{value:async()=>{
  const canvas=document.createElement('canvas'); canvas.width=1920; canvas.height=1080;
  const ctx=canvas.getContext('2d');
  const render=()=>{ctx.fillStyle='rgb(180,90,40)';ctx.fillRect(0,0,1920,1080);};
  render(); const timer=setInterval(render,100);
  const stream=canvas.captureStream(10), track=stream.getVideoTracks()[0];
  const stop=track.stop.bind(track); track.stop=()=>{clearInterval(timer);stop();};
  let settings={focusMode:'continuous',focusDistance:2};
  track.getCapabilities=()=>window.focusEnabled ? {focusMode:['continuous','single-shot','manual'],
    focusDistance:{min:0,max:10,step:.5}} : {};
  track.getSettings=()=>settings;
  track.getConstraints=()=>({width:{ideal:1920},facingMode:{ideal:'environment'}});
  track.applyConstraints=async constraints=>{
    window.focusCalls.push(constraints);
    const behavior=window.focusBehavior;
    if(behavior==='reject') throw new DOMException('Rejected','OverconstrainedError');
    if(behavior==='delay') await new Promise(resolve=>{window.finishFocus=resolve;});
    if(behavior!=='ignore') settings={...settings,...constraints.advanced.at(-1)};
  };
  return stream;
}});
"""


class Site(SimpleHTTPRequestHandler):
    frames = []
    capture_started = Event()
    release_capture = Event()
    ready = True
    hold_capture = False
    reject_capture = False

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/":
            self.path = "/static/index.html"
        super().do_GET()

    def respond(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_DELETE(self):
        self.send_response(204)
        self.end_headers()

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        if self.path == "/api/session":
            self.respond({"id": "test", "reference": {"width": 300, "height": 400,
                "boxes": [], "box": None, "points": [], "edges": [], "outline": None,
                "landmarks": [], "viewpoint": "模拟参考", "centerTolerance": .08}}, 201)
            return
        with Image.open(io.BytesIO(body)) as image:
            self.frames.append({"size": image.size, "pixel": image.getpixel((10, 10)), "url": self.path})
            capture = image.height > 960
        if capture and self.hold_capture:
            self.capture_started.set()
            self.release_capture.wait(8)
        if capture and self.reject_capture:
            type(self).ready = False
        self.respond({"stage": "ready", "mode": "ready", "ready": self.ready,
            "message": "模拟导拍已就绪", "boxes": [], "selected": None, "background": None,
            "position": None, "pose": None, "checks": [True, True, True], "skipped": []})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Site, directory=ROOT))
    Thread(target=server.serve_forever, daemon=True).start()
    image = io.BytesIO()
    Image.new("RGB", (300, 400), (180, 90, 40)).save(image, "PNG")
    photo = {"name": "reference.png", "mimeType": "image/png", "buffer": image.getvalue()}
    errors = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=args.executable, headless=True)
            page = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=1)
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.add_init_script(CAMERA)
            page.goto(f"http://127.0.0.1:{server.server_port}/")
            page.locator(".photo-controls summary").click()
            expect(page.locator("#focus-auto")).to_be_disabled()
            page.locator("#ref-file").set_input_files(photo)
            page.locator("#camera").click()
            expect(page.locator("#shutter")).to_be_enabled()
            expect(page.locator("#focus-auto")).to_be_enabled()

            # The effect is visible but never sent to the guide. The shutter
            # freezes both image and filter even while verification is pending.
            page.locator("#photo-filter").select_option("mono")
            assert "saturate(0)" in page.locator("#video").evaluate("el=>el.style.filter")
            Site.hold_capture = True
            with page.expect_download() as event:
                page.locator("#shutter").click()
                assert Site.capture_started.wait(5)
                page.locator("#photo-filter").select_option("warm")
                Site.release_capture.set()
            destination = OUT / "monochrome.jpg"
            event.value.save_as(destination)
            with Image.open(destination) as saved:
                assert saved.size == (810, 1080)
                for position in [(10, 10), (405, 540), (800, 1070)]:
                    pixel = saved.getpixel(position)
                    assert max(pixel)-min(pixel) <= 2, pixel
            assert all(row["pixel"][0]-row["pixel"][1] > 70 for row in Site.frames)
            assert any(row["size"] == (810, 1080) for row in Site.frames)
            print("PASS: shutter-time filter export and unfiltered guide frames", flush=True)

            # Readiness must still be rechecked before an export.
            Site.reject_capture = True
            downloads = []
            page.on("download", lambda download: downloads.append(download))
            page.locator("#shutter").click()
            expect(page.locator("#shutter")).to_be_disabled()
            page.wait_for_timeout(400)
            assert not downloads
            Site.reject_capture = False
            Site.ready = True
            expect(page.locator("#shutter")).to_be_enabled()

            page.locator("#focus-point").click()
            overlay = page.locator("#overlay")
            box = overlay.bounding_box()
            overlay.click(position={"x": box["width"]*.25, "y": box["height"]*.75})
            expect(page.locator("#focus-status")).to_contain_text("已请求点按对焦")
            target = page.evaluate("focusCalls.at(-1).advanced.at(-1).pointsOfInterest[0]")
            assert abs(target["x"]-.39453125) < .006 and abs(target["y"]-.75) < .006, target
            page.locator("#focus-distance").fill("26")
            page.locator("#focus-distance").dispatch_event("change")
            expect(page.locator("#focus-status")).to_contain_text("已请求手动调焦")
            assert page.evaluate("focusCalls.at(-1).advanced.at(-1).focusDistance") == 2.5
            expect(page.locator("#focus-point")).to_have_attribute("aria-pressed", "false")

            page.evaluate("focusBehavior='reject'")
            page.locator("#focus-auto").click()
            expect(page.locator("#focus-status")).to_contain_text("未能应用")
            expect(page.locator("#focus-auto")).to_be_enabled()
            page.evaluate("focusBehavior='ignore'")
            page.locator("#focus-auto").click()
            expect(page.locator("#focus-status")).to_contain_text("未确认")

            # Switching to an unsupported lens clears the previous controls;
            # completing an old request cannot change the new lens's status.
            page.evaluate("focusBehavior='delay'")
            page.locator("#focus-auto").click()
            page.wait_for_function("typeof finishFocus === 'function'")
            page.evaluate("focusEnabled=false")
            page.locator("#flip").click()
            expect(page.locator("#focus-status")).to_contain_text("未开放网页对焦控制")

            print("PASS: focus coordinates, manual range, errors and stale lens requests", flush=True)
            page.evaluate("finishFocus()")
            expect(page.locator("#focus-auto")).to_be_disabled()
            expect(page.locator("#focus-point")).to_be_disabled()
            expect(page.locator("#focus-manual-row")).to_be_hidden()
            expect(page.locator("#focus-status")).to_contain_text("未开放网页对焦控制")

            page.locator("#live-file").set_input_files(photo)
            expect(page.locator("#focus-status")).to_contain_text("照片试拍")
            expect(page.locator("#focus-auto")).to_be_disabled()
            assert "sepia(0.25)" in page.locator("#live-photo").evaluate("el=>el.style.filter")
            for width in (320, 390, 1280):
                page.set_viewport_size({"width": width, "height": 900})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.set_viewport_size({"width": 390, "height": 844})
            page.screenshot(path=OUT / "mobile.png", full_page=True)

            # Compare the actual CSS preview to the portable pixel exporter.
            colors = [(80, 120, 160), (240, 25, 5), (20, 230, 160)]
            for preset in ("fresh", "warm", "film", "mono"):
                expected = page.evaluate("""async ({preset,colors})=>{
                  const f=await import('/static/filters.js');
                  document.getElementById('filter-test')?.remove();
                  const c=document.createElement('canvas');c.id='filter-test';c.width=300;c.height=100;
                  c.style.filter=f.filterCSS(f.filterSettings(preset));document.body.append(c);
                  colors.forEach((v,i)=>{const x=c.getContext('2d');x.fillStyle=`rgb(${v})`;x.fillRect(i*100,0,100,100);});
                  return colors.map(v=>Array.from(f.filterPixels(new Uint8ClampedArray([...v,255]),f.filterSettings(preset))));
                }""", {"preset": preset, "colors": colors})
                screenshot = page.locator("#filter-test").screenshot()
                with Image.open(io.BytesIO(screenshot)) as rendered:
                    for i, color in enumerate(expected):
                        actual = rendered.getpixel((i*100+50, 50))
                        assert max(abs(actual[j]-color[j]) for j in range(3)) <= 3, (preset, actual, color)
            assert not errors, errors
            browser.close()
    finally:
        Site.release_capture.set()
        server.shutdown()
        server.server_close()
    print("PASS: preview/export, raw guide frames, shutter verification, focus fallback/races and mobile layout")


if __name__ == "__main__":
    main()
