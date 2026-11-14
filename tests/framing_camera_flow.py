"""真实浏览器核验：先呈现背景轮廓，再扫描/变焦，最后新帧复核；用户操作可取消。"""
import io
import json
import logging
from pathlib import Path
import sys
from threading import Thread

import numpy as np
from PIL import Image
from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import app


RECORD = """
window.framingQA={trace:[],requests:[],responses:[],references:[]};
const originalFetch=window.fetch.bind(window);
window.fetch=async (...args)=>{
  const url=String(args[0]?.url || args[0]);
  const isFrame=url.includes('/frame?');
  const sent={at:performance.now(),phase:document.querySelector('#overlay')?.dataset.framing,
    zoom:Number(document.querySelector('#zoom')?.value)};
  if(isFrame) window.framingQA.requests.push(sent);
  const response=await originalFetch(...args);
  if(isFrame && response.ok) {
    const result=await response.clone().json();
    window.framingQA.responses.push({...sent,receivedAt:performance.now(),stage:result.stage,
      ready:result.ready,scale:result.background?.zoom,seq:result.seq});
  } else if(url.endsWith('/api/session') && response.ok) {
    window.framingQA.references.push((await response.clone().json()).reference);
  }
  return response;
};
function frame() {
  const canvas=document.querySelector('#overlay'), slider=document.querySelector('#zoom');
  if(canvas && slider) {
    const entry={at:performance.now(),phase:canvas.dataset.framing || 'idle',zoom:Number(slider.value),
      ready:!document.querySelector('#shutter').disabled,
      transform:document.querySelector('#live-photo').style.transform};
    const last=window.framingQA.trace.at(-1);
    if(!last || last.phase!==entry.phase || last.zoom!==entry.zoom || last.ready!==entry.ready)
      window.framingQA.trace.push(entry);
  }
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);
"""


def wide_photo(path):
    with Image.open(path) as original:
        original = original.convert("RGB")
        w, h = original.size
        small = original.resize((round(w*.70), round(h*.70)), Image.Resampling.LANCZOS)
        wide = Image.new("RGB", (w, h), (37, 52, 45))
        wide.paste(small, ((w-small.width)//2, (h-small.height)//2))
        buffer = io.BytesIO()
        wide.save(buffer, "JPEG", quality=95)
        return {"name":"wide.jpg", "mimeType":"image/jpeg", "buffer":buffer.getvalue()}, small


def start_case(page, address, photo, wide, mobile=False):
    page.set_viewport_size({"width":390, "height":844} if mobile else {"width":1160, "height":860})
    page.goto(address)
    page.locator("#ref-file").set_input_files(photo)
    expect(page.locator("body")).to_have_attribute("data-reference", "loaded", timeout=30000)
    page.locator("#live-file").set_input_files(wide)


def wait_zoom(page):
    page.wait_for_function("""()=>document.querySelector('#overlay').dataset.framing==='zoom' &&
        Number(document.querySelector('#zoom').value)>1.015 && Number(document.querySelector('#zoom').value)<1.35""",
        timeout=30000)


def stable_zoom(page, expected):
    # Longer than the complete850ms animation: a cancelled rAF must never overwrite the user's value.
    page.evaluate("window.cancelledAt=performance.now()")
    page.wait_for_timeout(1200)
    samples = page.evaluate("window.framingQA.trace.filter(row=>row.at>=window.cancelledAt)")
    actual = float(page.locator("#zoom").input_value())
    assert abs(actual-expected) < .012, (actual, expected, samples)
    assert all(abs(row["zoom"]-expected) < .012 for row in samples), samples
    assert page.locator("#overlay").get_attribute("data-framing") != "zoom"
    return {"zoom":actual, "checkedForMs":1200}


def main():
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    photo = next(ROOT.parent.glob("校园摄影*/**/REF_011.webp"))
    wide, small = wide_photo(photo)
    qa = ROOT / "build/qa"
    qa.mkdir(parents=True, exist_ok=True)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    Thread(target=server.serve_forever, daemon=True).start()
    address = f"http://127.0.0.1:{server.server_port}"
    findings, errors = {}, []
    try:
        with sync_playwright() as pw:
            cached = list((ROOT.parent / ".browser").glob("chromium_headless_shell-*/**/chrome-headless-shell.exe"))
            browser = pw.chromium.launch(executable_path=str(cached[0]) if cached else None)
            page = browser.new_page(accept_downloads=True)
            page.add_init_script(RECORD)
            page.on("pageerror", lambda error: errors.append(str(error)))
            start_case(page, address, photo, wide, mobile=True)
            expect(page.locator("#overlay")).to_have_attribute("data-framing", "outline", timeout=30000)
            assert float(page.locator("#zoom").input_value()) == 1
            expect(page.locator("#shutter")).to_be_disabled()
            expect(page.locator("#overlay")).to_have_attribute("data-framing", "scan", timeout=10000)
            assert float(page.locator("#zoom").input_value()) == 1
            page.locator(".camera").screenshot(path=str(qa / "07-background-scan.png"))
            wait_zoom(page)
            expect(page.locator("#shutter")).to_be_disabled()
            page.locator(".camera").screenshot(path=str(qa / "08-smooth-zoom.png"))
            expect(page.locator("#shutter")).to_be_enabled(timeout=30000)
            data = page.evaluate("window.framingQA")
            (qa / "framing-camera-trace.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            trace, requests, responses = data["trace"], data["requests"], data["responses"]
            for phase in ("outline", "scan", "zoom", "verify"):
                assert any(row["phase"] == phase for row in trace), (phase, trace)
            started = {phase:next(row["at"] for row in trace if row["phase"] == phase)
                       for phase in ("outline", "scan", "zoom", "verify")}
            assert started["outline"] < started["scan"] < started["zoom"] < started["verify"], started
            assert all(row["zoom"] == 1 and not row["ready"] for row in trace if row["phase"] in ("outline", "scan"))
            moving = [row for row in trace if row["phase"] == "zoom"]
            distinct = sorted({round(row["zoom"], 4) for row in moving if 1.005 < row["zoom"] < 1.41})
            assert len(distinct) >= 5, distinct
            assert all(not row["ready"] for row in moving), moving
            assert all(b["zoom"] >= a["zoom"] for a, b in zip(moving, moving[1:])), moving
            assert all(row["phase"] not in ("outline", "scan", "zoom") for row in requests), requests
            target = float(page.locator("#zoom").input_value())
            assert 1.40 < target < 1.46, target
            endpoint = next(row for row in trace if row["phase"] == "verify")
            first_ready = next(row for row in trace if row["ready"])
            verified = [row for row in responses if row["at"] >= endpoint["at"]-40 and
                        abs(row["zoom"]-target) < .015 and row["receivedAt"] <= first_ready["at"]+40]
            assert verified and any(row["scale"] is not None and abs(row["scale"]-1) < .04 for row in verified), verified
            assert any((ref.get("backgroundOutline") or {}).get("paths") for ref in data["references"]), "Reference lacks background contour"
            with page.expect_download() as download:
                page.locator("#shutter").click()
            with Image.open(download.value.path()) as saved:
                assert saved.format == "JPEG" and abs(saved.width-small.width) < .015*small.width
                assert abs(saved.height-small.height) < .015*small.height
                expected = np.asarray(small.resize(saved.size), dtype=float)
                assert np.abs(np.asarray(saved, dtype=float)-expected).mean() < 8
            findings["sequence"] = {"phases":list(started), "intermediateZoomValues":len(distinct),
                                    "finalZoom":target, "freshVerification":True, "cleanJpeg":True}
            print("Outline -> scan -> smooth zoom -> fresh verification and clean JPEG passed", flush=True)

            start_case(page, address, photo, wide)
            wait_zoom(page)
            page.locator("#zoom").evaluate("e=>{e.value='1.18'; e.dispatchEvent(new Event('input',{bubbles:true}));}")
            expect(page.locator("#auto-zoom")).not_to_be_checked()
            findings["manualSliderCancellation"] = stable_zoom(page, 1.18)

            start_case(page, address, photo, wide)
            wait_zoom(page)
            stopped = page.evaluate("""()=>{const e=document.querySelector('#auto-zoom');e.checked=false;
                e.dispatchEvent(new Event('change',{bubbles:true}));return Number(document.querySelector('#zoom').value)}""")
            expect(page.locator("#auto-zoom")).not_to_be_checked()
            findings["autoToggleCancellation"] = stable_zoom(page, stopped)

            start_case(page, address, photo, wide)
            wait_zoom(page)
            stopped = page.evaluate("""()=>{document.querySelector('#skip-stage').click();
                return Number(document.querySelector('#zoom').value)}""")
            findings["skipCancellation"] = stable_zoom(page, stopped)
            expect(page.locator("#steps li").nth(1)).to_have_class("skipped")
            assert not errors, errors
            (qa / "framing-camera-results.json").write_text(json.dumps(findings, ensure_ascii=False, indent=2), encoding="utf-8")
            browser.close()
            print("Slider, auto-toggle and skip cancelled the running zoom without stale writes", flush=True)
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
