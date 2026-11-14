"""Exercise the lightweight APK UI against the real computer inference server.

Run scripts/prepare_android.py and npm ci in native-app first, then:
  python tests/native_app_flow.py [path/to/campus-portrait.webp]

The installed Capacitor JS bridge is real. Its Java HTTP boundary is forwarded
to a real Flask server, with multipart/base64 decoding matching Capacitor's
Android implementation. PhotoExport responses are simulated. This is not a
device test of camera permissions, MediaStore or the Android share sheet.
"""
import argparse
import base64
import functools
import hashlib
import io
import json
import mimetypes
import os
import re
import statistics
import sys
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from uuid import uuid4

from PIL import Image, ImageOps
from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server, WSGIRequestHandler


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "native-app/node_modules/@capacitor/android/capacitor/src/main/assets/native-bridge.js"


class Site(SimpleHTTPRequestHandler):
    extensions_map = SimpleHTTPRequestHandler.extensions_map | {
        ".mjs": "text/javascript", ".wasm": "application/wasm",
    }

    def log_message(self, *args):
        pass


SW_SPY = """
window.nativeQA = {calls:[], httpCalls:[], swCalls:[], failSave:false, images:0, peakImages:0};
if (navigator.serviceWorker) {
  Object.defineProperty(navigator.serviceWorker, 'register', {value:async (...args) => {
    window.nativeQA.swCalls.push(String(args[0]));
    throw new Error('QA: service-worker registration intercepted');
  }});
}
"""

# JSExport.java injects globals, native-bridge.js and each plugin's method
# wrappers before app scripts. Keep that shape, especially the absence of
# registerPlugin: that API requires importing @capacitor/core separately.
NATIVE_GLOBALS = r"""
window.Capacitor = {DEBUG:false, isLoggingEnabled:false, Plugins:{}};
window.androidBridge = {postMessage: raw => {
  const call = JSON.parse(raw);
  if (call.pluginId === 'CapacitorHttp') {
    const image = /\/api\/library\/[^/]+\/(image|thumbnail)/.test(call.options.url);
    if (image) {nativeQA.images++; nativeQA.peakImages=Math.max(nativeQA.peakImages,nativeQA.images);}
    const record = {url:call.options.url,method:call.options.method,dataType:call.options.dataType,started:performance.now()};
    nativeQA.httpCalls.push(record);
    window.qaNativeHttp(call.options).then(data => {
      Object.assign(record,{status:data.status,stage:data.data?.stage,message:data.data?.message,error:data.data?.error,elapsed:performance.now()-record.started});
      Capacitor.fromNative({callbackId:call.callbackId,pluginId:call.pluginId,success:true,data,save:false});
    }, error => {
      Capacitor.fromNative({callbackId:call.callbackId,pluginId:call.pluginId,success:false,error:{message:error.message},save:false});
    }).finally(()=>{if(image)nativeQA.images--;});
    return;
  }
  if (call.pluginId !== 'PhotoExport') return;
  window.nativeQA.calls.push(call);
  const failed = call.methodName === 'savePhoto' && window.nativeQA.failSave;
  setTimeout(() => window.Capacitor.fromNative({
    pluginId:call.pluginId, methodName:call.methodName, callbackId:call.callbackId,
    success:!failed, save:false,
    data:failed ? undefined : {uri:'content://qa/images/1'},
    error:failed ? {message:'测试保存失败，请重试'} : undefined,
  }), 0);
}};
"""

PLUGIN_EXPORT = """
(function(w) {
  const p = w.Capacitor.Plugins.PhotoExport = {};
  for (const name of ['savePhoto', 'sharePhoto']) {
    p[name] = options => w.Capacitor.nativePromise('PhotoExport', name, options);
  }
  w.Capacitor.Plugins.CapacitorHttp = {request: options => w.Capacitor.nativePromise('CapacitorHttp', 'request', options)};
  w.Capacitor.PluginHeaders = [{name:'PhotoExport', methods:[
    {name:'savePhoto', rtype:'promise'}, {name:'sharePhoto', rtype:'promise'},
  ]}];
})(window);
"""


def upload(path):
    return {"name": path.name, "mimeType": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            "buffer": path.read_bytes()}


def browser_path():
    if os.environ.get("CHROMIUM_PATH"):
        return os.environ["CHROMIUM_PATH"]
    cached = sorted((ROOT.parent / ".browser").glob("chromium_headless_shell-*/**/chrome-headless-shell.exe"))
    return str(cached[-1]) if cached else None


class QuietServer(WSGIRequestHandler):
    def log(self, *args, **kwargs):
        pass


def native_http(options):
    """Emulate only Java's HTTP serialization; the real Flask API does the work."""
    parsed = urlparse(options["url"])
    assert parsed.hostname in {"127.0.0.1", "localhost"}, "Test requests must stay on this computer"
    headers = options.get("headers", {}).copy()
    data = options.get("data")
    payload = None
    if options.get("dataType") == "formData":
        boundary = uuid4().hex
        headers["Content-Type"] = "multipart/form-data; boundary=" + boundary
        body = io.BytesIO()
        for item in data:
            body.write(("--" + boundary + "\r\nContent-Disposition: form-data; name=\"" + item["key"] + "\"").encode())
            if item["type"] == "base64File":
                body.write(("; filename=\"" + item["fileName"] + "\"\r\nContent-Type: " + item["contentType"] + "\r\n\r\n").encode())
                body.write(base64.b64decode(item["value"]))
            else:
                body.write(("\r\n\r\n" + item["value"]).encode())
            body.write(b"\r\n")
        body.write(("--" + boundary + "--\r\n").encode())
        payload = body.getvalue()
    elif options.get("dataType") == "file":
        payload = base64.b64decode(data)
    elif data is not None:
        payload = json.dumps(data).encode()
    request = Request(options["url"], data=payload, headers=headers, method=options.get("method", "GET"))
    try:
        response = urlopen(request, timeout=95)
    except HTTPError as error:
        response = error
    with response:
        content = response.read()
        if "application/json" in response.headers.get("Content-Type", ""):
            body = json.loads(content)
        elif options.get("responseType") == "arraybuffer":
            body = base64.b64encode(content).decode()
        else:
            body = content.decode()
        return {"status": response.status, "data": body, "headers": dict(response.headers), "url": response.url}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("photo", nargs="?", type=Path)
    options = parser.parse_args()
    photo = options.photo or next(ROOT.parent.glob("校园摄影*/**/REF_029.webp"), None)
    assert photo and photo.is_file(), "Supply a local campus portrait reference"
    site = ROOT / "build/android-web"
    assert (site / "index.html").is_file(), "Run scripts/prepare_android.py first"
    assert BRIDGE.is_file(), "Run npm ci in native-app first"
    with Image.open(photo) as source:
        source_size = ImageOps.exif_transpose(source).size
    qa = ROOT / "build/qa"
    qa.mkdir(parents=True, exist_ok=True)
    report = {
        "scope": "Lightweight APK UI; real Capacitor JS and computer Flask inference; simulated Java HTTP serialization and PhotoExport responses",
        "not_tested": ["Physical Android camera", "Android permission dialogs", "MediaStore", "System share sheet"],
        "reference": photo.name, "passed": False,
    }
    started = time.monotonic()
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Site, directory=site))
    Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/"
    sys.path.insert(0, str(ROOT))
    from app import app
    computer = make_server("127.0.0.1", 0, app, threaded=True, request_handler=QuietServer)
    Thread(target=computer.serve_forever, daemon=True).start()
    computer_url = f"http://127.0.0.1:{computer.server_port}/"
    second_computer = make_server("127.0.0.1", 0, app, threaded=True, request_handler=QuietServer)
    Thread(target=second_computer.serve_forever, daemon=True).start()
    second_url = f"http://127.0.0.1:{second_computer.server_port}/"
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=browser_path())
            ctx = browser.new_context(viewport={"width":390, "height":844}, is_mobile=True, has_touch=True)
            errors, unexpected, downloads = [], [], []

            def allow_bundled_only(route):
                requested = route.request.url
                if requested.startswith("http") and (
                    urlparse(requested).netloc != urlparse(url).netloc or "/api/" in requested or
                    any(segment in requested for segment in ("/models/", "/vendor/", "/mobile/", ".onnx", ".wasm"))
                ):
                    unexpected.append(requested)
                    route.abort()
                else:
                    route.continue_()

            ctx.route("**/*", allow_bundled_only)
            ctx.expose_binding("qaNativeHttp", lambda source, options: native_http(options))
            ctx.add_init_script(SW_SPY + NATIVE_GLOBALS + BRIDGE.read_text(encoding="utf-8") + PLUGIN_EXPORT)
            page = ctx.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("download", lambda download: downloads.append(download.suggested_filename))
            page.goto(url, wait_until="domcontentloaded", timeout=90000)
            page.locator("#guide-mode").select_option("live")
            expect(page.locator("#offline")).to_be_hidden()
            expect(page.locator("#computer-connect")).to_be_visible()
            expect(page.locator("#library-connection")).to_be_hidden()
            assert page.evaluate("Capacitor.getPlatform()") == "android"
            assert page.evaluate("typeof Capacitor.registerPlugin") == "undefined"
            assert page.evaluate("async () => (await import('./static/native-export.js')).nativeAndroid")
            assert not page.evaluate("async () => (await import('./static/transport.js')).onDevice")
            assert page.evaluate("nativeQA.swCalls") == []
            assert not list(site.rglob("*.wasm")) and not list(site.rglob("*.onnx"))
            invalid = page.evaluate("""async () => {
              const m = await import('./static/native-server.js');
              return ['http://8.8.8.8:7862','https://user:password@example.org','ftp://192.168.1.1',
                'https://example.org/?secret=yes','https://example.org/#abc'].map(s=>{
                  try {m.normalizeServer(s); return false;} catch {return true;}
                });
            }""")
            assert invalid == [True] * 5
            # Selecting locally before connecting must not remove the library's server-change listener.
            page.locator("#ref-file").set_input_files(upload(photo))
            expect(page.locator("#hint")).to_contain_text("请先在页面顶部连接电脑", timeout=15000)
            page.locator("#computer-server").fill("http://127.0.0.1:1")
            page.locator("#computer-connect-button").click()
            expect(page.locator("#computer-status")).to_contain_text("无法连接电脑", timeout=15000)
            page.locator("#computer-server").fill(computer_url)
            page.locator("#computer-connect-button").click()
            expect(page.locator("#computer-status")).to_contain_text("已连接电脑", timeout=15000)
            expect(page.locator(".photo-card").first).to_be_visible(timeout=30000)
            page.wait_for_function("document.querySelector('.photo-card img')?.src.startsWith('blob:')", timeout=30000)
            assert page.evaluate("nativeQA.peakImages") <= 1
            assert page.evaluate("nativeQA.httpCalls.some(c=>c.url.endsWith('/thumbnail'))")
            # A failed replacement must not silently discard a working connection.
            page.locator("#computer-server").fill("http://127.0.0.1:1")
            page.locator("#computer-connect-button").click()
            expect(page.locator("#computer-status")).to_contain_text("当前仍使用之前连接的电脑", timeout=15000)
            assert page.evaluate("async () => (await import('./static/native-server.js')).getServerBase()") == computer_url
            print("Connection failure/recovery, library native image loading and lightweight mode passed", flush=True)

            page.locator("#ref-file").set_input_files(upload(photo))
            page.wait_for_function("document.body.dataset.reference === 'loaded' || /失败|无法|无效/.test(document.querySelector('#hint').textContent)", timeout=180000)
            assert page.locator("body").get_attribute("data-reference") == "loaded", page.locator("#hint").inner_text()
            print("Reference analyzed by the real computer backend", flush=True)
            page.locator("#live-file").set_input_files(upload(photo))
            expect(page.locator("#source-state")).to_have_text("照片试拍")
            expect(page.locator("#shutter")).to_be_enabled(timeout=150000)
            page.locator("#shutter").click()
            expect(page.locator("#export")).to_be_visible(timeout=60000)
            expect(page.locator("#download-shot")).to_have_text("保存到相册")
            expect(page.locator("#share-shot")).to_be_visible()
            expect(page.locator("#share-shot")).to_have_text("分享照片")
            assert page.evaluate("nativeQA.calls.length") == 0
            page.locator("#download-shot").click()
            expect(page.locator("#hint")).to_contain_text("已保存到相册")
            page.locator("#share-shot").click()
            page.wait_for_function("nativeQA.calls.length === 2")
            calls = page.evaluate("nativeQA.calls")
            assert [call["methodName"] for call in calls] == ["savePhoto", "sharePhoto"]
            save, share = [call["options"] for call in calls]
            assert set(save) == {"base64", "filename"}
            assert re.fullmatch(r"campus-photo-\d+\.jpg", save["filename"])
            assert re.fullmatch(r"campus-photo-\d+\.jpg", share["filename"])
            assert save["base64"] == share["base64"]
            displayed = page.evaluate("""async () => {
              const blob = await (await fetch(document.querySelector('#last-shot').src)).blob();
              return await new Promise(resolve => {
                const r = new FileReader(); r.onload=()=>resolve(r.result.split(',')[1]); r.readAsDataURL(blob);
              });
            }""")
            assert displayed == save["base64"], "Native export must preserve the captured JPEG"
            jpeg = base64.b64decode(save["base64"], validate=True)
            with Image.open(io.BytesIO(jpeg)) as output:
                assert output.format == "JPEG"
                assert output.size == source_size, (output.size, source_size)
            print("Actual inference, all guidance gates and JPEG save/share bridge payload passed", flush=True)

            page.evaluate("nativeQA.failSave=true")
            page.locator("#download-shot").click()
            expect(page.locator("#hint")).to_contain_text("测试保存失败，请重试")
            page.evaluate("nativeQA.failSave=false")
            page.locator("#download-shot").click()
            expect(page.locator("#hint")).to_contain_text("已保存到相册")
            validation = page.evaluate("""async () => {
              const m = await import('./static/native-export.js');
              const rejected = [];
              for (const action of [m.saveNativePhoto, m.shareNativePhoto]) {
                for (const file of [null, new Blob(['x'],{type:'image/png'}), {type:'image/jpeg',size:25*1024*1024}]) {
                  try {await action(file); rejected.push(null);} catch(e) {rejected.push(e.message);}
                }
              }
              return rejected;
            }""")
            assert validation == ["请保存小于 24 MB 的 JPEG 照片"] * 6
            assert page.evaluate("nativeQA.calls.length") == 4
            print("Native save errors, retry and export validation passed", flush=True)
            assert not downloads, downloads
            assert page.evaluate("nativeQA.swCalls") == []
            assert not unexpected, unexpected
            assert not errors, errors
            calls = page.evaluate("nativeQA.httpCalls")
            assert any(c.get("dataType") == "formData" and c["url"].endswith("/api/session") for c in calls)
            assert any(c.get("dataType") == "file" and "/frame?" in c["url"] for c in calls)
            old_id = next(c["url"].split("/api/session/")[1].split("/")[0] for c in calls if "/frame?" in c["url"])
            # Change origins while a live reference exists. Its DELETE belongs to the old server.
            page.locator("#computer-server").fill(second_url)
            page.locator("#computer-connect-button").click()
            expect(page.locator("#computer-status")).to_contain_text("已连接电脑", timeout=15000)
            page.wait_for_function("prefix => nativeQA.httpCalls.some(c=>c.url.startsWith(prefix) && c.url.endsWith('/api/session') && c.method==='POST')", arg=second_url, timeout=30000)
            page.wait_for_function("url => nativeQA.httpCalls.some(c=>c.url===url && c.method==='DELETE')", arg=computer_url+"api/session/"+old_id)
            try:
                expect(page.locator("#shutter")).to_be_enabled(timeout=60000)
            except Exception:
                diagnostic = page.evaluate("({hint:document.querySelector('#hint').textContent,state:document.body.dataset.reference,hidden:document.hidden,calls:nativeQA.httpCalls.slice(-15)})")
                report["switch_diagnostic"] = diagnostic
                print("Server switch:", json.dumps(diagnostic, ensure_ascii=True), flush=True)
                page.screenshot(path=qa / "native-app-failure.png", full_page=True)
                raise
            expect(page.locator("#source-state")).to_have_text("照片试拍")
            assert not errors, errors
            assert not unexpected, unexpected
            frames = [call for call in page.evaluate("nativeQA.httpCalls") if "/frame?" in call["url"]]
            previous, intervals = {}, []
            for frame in frames:
                session_path = frame["url"].split("/frame?")[0]
                if session_path in previous:
                    intervals.append(frame["started"] - previous[session_path])
                previous[session_path] = frame["started"]
            timings = {"samples": len(intervals), "median_ms": round(statistics.median(intervals), 1),
                       "max_ms": round(max(intervals), 1),
                       "below_1600_ms": sum(value < 1600 for value in intervals),
                       "scope": "Browser native-HTTP bridge test, loopback IPv4; not phone/LAN performance"}
            print("Frame intervals:", json.dumps(timings), flush=True)
            page.screenshot(path=qa / "native-app-export.png", full_page=True)
            report.update({"native_mode": True, "real_computer_reference_inference": True,
                           "no_bundled_models": True, "connection_failure_and_recovery": True,
                           "gallery_images_use_blob_urls": True, "thumbnail_concurrency_limit": 1,
                           "server_change_reloads_reference_and_releases_old_session": True,
                           "frame_intervals": timings,
                           "all_guidance_gates": True, "jpeg_size": list(source_size),
                           "jpeg_sha256": hashlib.sha256(jpeg).hexdigest(), "jpeg_bytes": len(jpeg),
                           "save_share_same_capture": True, "save_failure_and_retry": True,
                           "invalid_exports_rejected": len(validation), "service_worker_attempts": 0,
                           "external_or_api_requests": unexpected, "browser_downloads": downloads,
                           "page_errors": errors, "capacitor_bridge_sha256": hashlib.sha256(BRIDGE.read_bytes()).hexdigest()})
            ctx.close()

            # A normal browser still talks to its own Flask server without native controls.
            web = browser.new_context()
            web.add_init_script(SW_SPY)
            fallback = web.new_page()
            fallback.goto(computer_url, wait_until="domcontentloaded")
            assert not fallback.evaluate("async () => (await import('./static/native-export.js')).nativeAndroid")
            expect(fallback.locator("#computer-connection")).to_have_count(0)
            expect(fallback.locator(".photo-card").first).to_be_visible(timeout=30000)
            assert not fallback.evaluate("nativeQA.calls.length")
            report["normal_browser_uses_existing_server_path"] = True
            web.close()
            browser.close()
            report["passed"] = True
            print("Save error/retry, invalid payload guards and browser fallback passed", flush=True)
    except Exception as error:
        report["failure"] = str(error)
        raise
    finally:
        server.shutdown()
        server.server_close()
        computer.shutdown()
        computer.server_close()
        second_computer.shutdown()
        second_computer.server_close()
        report["duration_seconds"] = round(time.monotonic() - started, 2)
        (qa / "native-app-results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("QA report:", qa / "native-app-results.json", flush=True)


if __name__ == "__main__":
    sys.exit(main())
