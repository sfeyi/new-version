"""Exercise a fresh PWA installation, offline relaunch and camera capture using a local reference.

python tests/mobile_flow.py path/to/a/campus-portrait.jpg [--url https://.../new-version/]
"""
import argparse
import base64
import functools
import io
import mimetypes
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import urlparse

from PIL import Image, ImageOps
from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[1]


class Site(SimpleHTTPRequestHandler):
    extensions_map = SimpleHTTPRequestHandler.extensions_map | {".mjs": "text/javascript", ".wasm": "application/wasm"}

    def translate_path(self, path):
        prefix = "/new-version/"
        if not path.startswith(prefix):
            return str(ROOT / "build" / "missing")
        return super().translate_path("/" + path[len(prefix):])

    def log_message(self, *args):
        pass


def upload(path):
    return {"name": path.name, "mimeType": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            "buffer": path.read_bytes()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("photo", type=Path)
    parser.add_argument("--url")
    parser.add_argument("--browser", choices=("chromium", "webkit"), default="chromium")
    parser.add_argument("--other", type=Path, help="An unrelated portrait to check background rejection offline")
    parser.add_argument("--tower", action="store_true", help="Expect a fixed building outline for this reference")
    options = parser.parse_args()
    url = options.url
    server = None
    if not url:
        handler = functools.partial(Site, directory=ROOT / "build" / "mobile")
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        Thread(target=server.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{server.server_port}/new-version/"
    # The browser's fake camera is fed the same composition as the selected local file.
    source = io.BytesIO()
    with Image.open(options.photo) as im:
        photo = ImageOps.exif_transpose(im).convert("RGB")
        photo.thumbnail((960, 960))
        photo.save(source, format="JPEG", quality=94)
    data_url = "data:image/jpeg;base64," + base64.b64encode(source.getvalue()).decode()
    try:
        with sync_playwright() as pw:
            engine = getattr(pw, options.browser)
            browser = engine.launch(executable_path=os.environ.get("CHROMIUM_PATH") if options.browser == "chromium" else None)
            ctx = browser.new_context(viewport={"width":390, "height":844}, is_mobile=True, has_touch=True)
            errors, unexpected = [], []
            ctx.on("request", lambda req: unexpected.append(req.url) if "/api/" in req.url or
                   (req.url.startswith("http") and urlparse(url).netloc != urlparse(req.url).netloc) else None)
            ctx.add_init_script(script="""if (navigator.mediaDevices && HTMLCanvasElement.prototype.captureStream)
            Object.defineProperty(navigator.mediaDevices,'getUserMedia',{value:async()=>{
              const img = new Image();
              img.src = 'PHOTO_DATA';
              await img.decode();
              const c = document.createElement('canvas'); c.width = img.naturalWidth; c.height = img.naturalHeight;
              const draw = () => c.getContext('2d').drawImage(img,0,0);
              draw(); window.testCameraTimer = setInterval(draw,80);
              return c.captureStream(12);
            }});""".replace("PHOTO_DATA", data_url))
            page = ctx.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", lambda msg: print("Browser:", msg.text, flush=True) if msg.type == "error" else None)
            page.goto(url, wait_until="domcontentloaded", timeout=90000)
            page.locator("#offline-download").click()
            expect(page.locator("#offline-status")).to_contain_text("已就绪", timeout=300000)
            print("Offline pack ready", flush=True)
            assert page.evaluate("!!navigator.serviceWorker.controller")
            ctx.set_offline(True)
            page.close()
            page = ctx.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", lambda msg: print("Browser:", msg.text, flush=True) if msg.type == "error" else None)
            page.goto(url, wait_until="domcontentloaded")
            expect(page.locator("#offline-status")).to_contain_text("已就绪")
            page.locator("#ref-file").set_input_files(upload(options.photo))
            try:
                page.wait_for_function("document.body.dataset.reference === 'loaded' || /失败|无法|无效/.test(document.querySelector('#hint').textContent)", timeout=180000)
                assert page.locator("body").get_attribute("data-reference") == "loaded", page.locator("#hint").inner_text()
            except Exception:
                print("Reference:", page.locator("#hint").inner_text(), flush=True)
                raise
            print("Offline reference ready", flush=True)
            camera = page.evaluate("!!(navigator.mediaDevices && HTMLCanvasElement.prototype.captureStream)")
            if camera:
                page.locator("#camera").click()
            else:
                assert options.browser == "webkit", "Missing camera replay support"
                page.locator("#live-file").set_input_files(upload(options.photo))
            expect(page.locator("#source-state")).to_have_text("相机取景" if camera else "照片试拍")
            if options.tower:
                expect(page.locator("#legend")).to_have_attribute("data-background", "outline")
            expect(page.locator("#shutter")).to_be_enabled(timeout=150000)
            page.locator("#shutter").click()
            expect(page.locator("#export")).to_be_visible(timeout=60000)
            with page.expect_download() as event:
                page.locator("#download-shot").click()
            with Image.open(event.value.path()) as output:
                assert output.format == "JPEG"
                if camera:
                    assert output.size == photo.size
                else:
                    assert abs((output.width / output.height) / (photo.width / photo.height) - 1) < .003
            print("Offline", "camera" if camera else "photo", "guidance, all three gates and JPEG capture passed", flush=True)
            if options.other:
                page.locator("#live-file").set_input_files(upload(options.other))
                expect(page.locator("#source-state")).to_have_text("照片试拍")
                expect(page.locator("#indicator")).to_have_attribute("data-mode", "search", timeout=90000)
                expect(page.locator("#shutter")).to_be_disabled()
                print("Unrelated background rejected offline", flush=True)
            page.locator("#live-file").set_input_files(upload(options.photo))
            expect(page.locator("#source-state")).to_have_text("照片试拍")
            expect(page.locator("#live-photo")).to_have_js_property("complete", True)
            for stage in ("机位", "站位", "姿势"):
                btn = page.locator("#skip-stage")
                expect(btn).to_have_text("跳过" + stage)
                btn.click()
            expect(page.locator("#hint")).to_contain_text("跳过全部", timeout=60000)
            page.locator("#restore-steps").click()
            expect(page.locator("#skip-stage")).to_have_text("跳过机位")
            assert not unexpected, unexpected
            assert not errors, errors
            print("Offline source change, stage skipping and restore passed; no API or third-party requests", flush=True)
            browser.close()
    finally:
        if server:
            server.shutdown()


if __name__ == "__main__":
    sys.exit(main())
