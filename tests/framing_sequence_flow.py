"""验证背景轮廓先出现、扫描后才变焦，并要求新画面复核最终倍率。"""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from threading import Thread

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_):
        pass


CASES = """async () => {
  const {FramingSequence} = await import('/static/framing.js');
  const check=(ok,message)=>{if(!ok) throw new Error(message);};
  const close=(a,b)=>Math.abs(a-b)<1e-9;
  const findings={};

  const s=new FramingSequence();
  check(s.sample(0).phase==='idle' && !s.entered, 'A new sequence started on its own');
  check(!s.beginZoom(1,1.5,0), 'A scale command skipped the outline introduction');
  check(s.enter(1000), 'First framing entry was ignored');
  let state=s.sample(1000);
  check(state.phase==='outline' && state.reveal===0 && state.blockAnalysis, 'Outline did not begin before analysis');
  check(!s.beginZoom(1,1.5,1100), 'Zoom started while the background was being revealed');
  state=s.sample(1450);
  check(state.phase==='outline' && close(state.reveal,.5) && state.zoom===null, 'Outline reveal was not progressive');
  check(!s.enter(1500), 'Repeated frame entry restarted the introduction');
  check(s.sample(1899).phase==='outline', 'Outline vanished before its readable duration');
  state=s.sample(1900);
  check(state.phase==='scan' && state.reveal===1 && state.scan===0 && state.blockAnalysis,
        'Scan did not follow the complete outline');
  check(!s.beginZoom(1,1.5,2000), 'Zoom started before the scan finished');
  check(close(s.sample(2125).scan,.5), 'Scan progress did not follow its own time interval');
  check(s.sample(2349).phase==='scan', 'Scan finished too early');
  state=s.sample(2350);
  check(state.phase==='adjust' && state.reveal===1 && !state.blockAnalysis && state.zoom===null,
        'After scan, geometry should be sampled instead of inventing a zoom');
  findings.phaseOrder=['outline','scan','adjust'];
  findings.outlineMs=900; findings.scanMs=450;

  check(s.beginZoom(1,1.5,2350), 'A reliable post-scan zoom command was not accepted');
  state=s.sample(2350);
  check(state.phase==='zoom' && state.zoom===1 && state.blockAnalysis, 'Zoom jumped to the endpoint');
  check(!s.finishVerification(), 'Verification finished before the animated zoom');
  check(!s.beginZoom(1,1.2,2450), 'An overlapping scale command replaced the active animation');
  state=s.sample(2562.5);
  check(close(state.zoom,1.078125) && state.blockAnalysis, 'Zoom did not accelerate smoothly');
  state=s.sample(2775);
  check(close(state.zoom,1.25) && state.phase==='zoom', 'Zoom midpoint was not interpolated');
  state=s.sample(2987.5);
  check(close(state.zoom,1.421875), 'Zoom did not decelerate smoothly');
  state=s.sample(3200);
  check(state.phase==='verify' && state.zoom===1.5 && !state.blockAnalysis,
        'Zoom endpoint was not exact or did not require a fresh verification');
  check(!('ready' in state), 'Display timing manufactured a capture-ready decision');
  check(s.sample(3300).zoom===1.5, 'A second consumer lost the exact endpoint before verification');
  check(!s.beginZoom(1.5,1.6,3400), 'A stale factor started another zoom before verification');
  check(s.finishVerification(), 'Fresh verification could not release the zoom command');
  state=s.sample(3500);
  check(state.phase==='adjust' && state.zoom===null && !state.blockAnalysis,
        'Verified zoom continued overwriting camera controls');
  check(!s.finishVerification(), 'Verification completed twice');
  findings.zoomMs=850; findings.freshVerificationRequired=true;

  // Lower bounds, upper bounds, zooming back out, and no-op commands stay safe.
  check(!s.beginZoom(NaN,1.6,3500), 'NaN zoom was accepted');
  check(!s.beginZoom(1.5,Infinity,3500), 'Infinite zoom was accepted');
  check(!s.beginZoom(1.2,1.2,3500), 'A no-op zoom needlessly blocked guidance');
  check(s.beginZoom(4,.5,3500), 'Bounded zoom-out was rejected');
  let last=1.6;
  for(let t=3500;t<=4350;t+=17) {
    state=s.sample(t);
    check(state.zoom>=1 && state.zoom<=1.6 && state.zoom<=last,
          'Zoom-out escaped limits or reversed direction');
    last=state.zoom;
  }
  check(s.sample(4350).zoom===1, 'Zoom-out did not finish at the lower bound');
  findings.boundedZoomOut=true;

  const manual=new FramingSequence();
  check(!manual.cancelZoom() && manual.sample(0).phase==='idle', 'Manual cancellation activated an idle sequence');
  manual.enter(0); manual.sample(1350);
  manual.beginZoom(1,1.6,1350); manual.sample(1600);
  check(manual.cancelZoom(), 'Manual slider could not cancel an active zoom');
  state=manual.sample(5000);
  check(manual.entered && state.phase==='adjust' && state.zoom===null && !state.blockAnalysis,
        'Cancelled zoom later overwrote the manual value or replayed the intro');
  check(!manual.enter(5100), 'Manual override erased prior intro completion');
  check(manual.beginZoom(1.2,1.4,5200), 'A later verified command could not follow manual override');
  manual.cancelZoom();
  manual.reset(); manual.enter(0); manual.sample(450); manual.cancelZoom();
  check(manual.sample(451).phase==='adjust', 'Cancelling the intro still blocked manual controls');
  findings.manualControlCancelsWithoutReplay=true;

  // User override/reset cancels all pending animation and permits a clean next entry.
  const cancelled=new FramingSequence();
  cancelled.enter(0); cancelled.sample(1350);
  cancelled.beginZoom(1,1.6,1350); cancelled.sample(1500);
  cancelled.reset();
  state=cancelled.sample(5000);
  check(!cancelled.entered && state.phase==='idle' && state.zoom===null && !state.blockAnalysis,
        'Cancelled sequence later overwrote a manual value');
  check(cancelled.enter(5000) && cancelled.sample(5000).reveal===0, 'New reference inherited previous reveal progress');
  check(cancelled.sample(5450).reveal===.5, 'Restarted reveal duration changed');
  check(cancelled.sample(5100).reveal===.5, 'A rewound clock visibly reversed the effect');
  cancelled.reset();
  cancelled.enter(0);
  state=cancelled.sample(10000);
  check(state.phase==='adjust' && state.zoom===null, 'A long wait invented a scale or capture decision');
  findings.resetCancelsPendingMotion=true; findings.noSyntheticReady=true;
  return findings;
}"""


def main():
    qa = ROOT / "build/qa"
    qa.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(ROOT)))
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as pw:
            cached = list((ROOT.parent / ".browser").glob("chromium_headless_shell-*/**/chrome-headless-shell.exe"))
            executable = os.environ.get("CHROMIUM_PATH") or (str(cached[0]) if cached else None)
            browser = pw.chromium.launch(executable_path=executable)
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{server.server_port}/")
            findings = page.evaluate(CASES)
            (qa / "framing-sequence-results.json").write_text(json.dumps(findings, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(findings, indent=2))
            print("Outline, scan, smooth bounded zoom, fresh verification, and cancellation order passed.")
            browser.close()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
