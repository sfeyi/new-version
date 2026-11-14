"""在真实浏览器里用可控时间验证白点防抖、失配和对齐动画，不依赖模型或摄像头。"""
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
  const {AimMotion} = await import('/static/aim.js');
  const findings = {};
  const check = (ok, message) => {if (!ok) throw new Error(message);};
  const result = (point, options={}) => ({
    stage:'background', hold:0, skipped:[],
    background:point ? {tracking:true, current:point, centerOk:false} : null,
    ...options
  });
  const near = (point, options={}) => result(point, {
    ...options, background:{tracking:true, current:point, centerOk:true}
  });
  const advance = (motion, from, until) => {
    let state;
    for (let t=from; t<=until; t+=16) state = motion.sample(t);
    return state;
  };
  const visible = state => state.point && state.opacity > .01;
  const deviation = values => {
    const mean = values.reduce((sum,n)=>sum+n,0)/values.length;
    return Math.sqrt(values.reduce((sum,n)=>sum+(n-mean)**2,0)/values.length);
  };

  // A motionless camera's noisy estimates should not turn into equally noisy dots.
  const jitter = new AimMotion(1);
  jitter.update(result([.73,.5]), 0);
  jitter.sample(0);
  const raw=[], shown=[];
  for (let i=1; i<=20; i++) {
    const x=.73+(i%2 ? .014 : -.014), at=i*100;
    const measurement=result([x,.5]);
    const before=JSON.stringify(measurement);
    jitter.update(measurement,at);
    const state=advance(jitter,at,at+96);
    check(JSON.stringify(measurement)===before, 'Presentation mutated the geometric result');
    check(visible(state), 'Valid tracking unexpectedly hid the marker');
    if (i>5) {raw.push(x); shown.push(state.point[0]);}
  }
  findings.jitter={rawStd:deviation(raw), displayedStd:deviation(shown)};
  check(findings.jitter.displayedStd < findings.jitter.rawStd*.8, 'Small estimate jitter was not attenuated');

  // One implausible result should not throw the marker across the frame.
  const spike = new AimMotion(1);
  spike.update(result([.75,.5]),0);
  advance(spike,0,96);
  spike.update(result([.2,.5]),100);
  let state=advance(spike,112,288);
  check(visible(state) && Math.abs(state.point[0]-.75)<.06, 'A single outlier moved the dot across the frame');
  spike.update(result([.746,.501]),300);
  state=advance(spike,304,496);
  check(Math.abs(state.point[0]-.75)<.04, 'Rejected outlier polluted the subsequent stable position');
  // Two consistent shifted samples are actual camera movement, not permanent noise.
  spike.update(result([.25,.5]),500);
  advance(spike,512,592);
  spike.update(result([.255,.5]),600);
  state=advance(spike,608,800);
  check(visible(state) && state.point[0]<.5, 'Confirmed camera movement never started');
  for (let at=900; at<=1200; at+=100) {
    spike.update(result([.255,.5]),at);
    state=advance(spike,at,at+96);
  }
  check(visible(state) && Math.abs(state.point[0]-.255)<.065, 'Persistent movement was permanently frozen');
  findings.outlierRecoveryX=state.point[0];

  const pan=new AimMotion(1);
  pan.update(result([.74,.5]),0); pan.sample(0);
  pan.update(result([.64,.5]),100);
  state=pan.sample(116);
  check(state.point[0]>.64 && state.point[0]<.74, 'Normal motion teleported or never started');
  state=advance(pan,132,596);
  check(Math.abs(state.point[0]-.64)<.035, 'Normal pan has excessive visual lag');

  // Proximity feedback must leave the dot attached to scenery, not pull it to the screen center.
  const aim=new AimMotion(1);
  aim.update(result([.56,.5]),0);
  state=advance(aim,0,96);
  check(!state.near && state.success===null, 'A close-looking but rejected sample indicated success');
  aim.update(near([.56,.5],{hold:.4}),100);
  state=advance(aim,112,496);
  check(state.near && Math.hypot(state.point[0]-.56,state.point[1]-.5)<.002,
    'Near-center feedback detached the dot from its scenery point');
  check(state.success===null, 'Near-center sample celebrated before stage verification');
  aim.update(near([.53,.5],{stage:'framing'}),500);
  state=aim.sample(516);
  check(state.success===null && !state.completed, 'Stage change without explicit completion played success');
  aim.update(near([.53,.5],{stage:'framing',completed:['background']}),600);
  state=aim.sample(616);
  check(state.success!==null && state.completed && !visible(state), 'Verified completion did not retire the dot and show success');
  findings.successStarted=true;
  check(aim.sample(2199).success!==null, 'Success feedback disappeared before 1.6 seconds');
  check(aim.sample(2200).success===null, 'Success animation did not end after 1.6 seconds');
  aim.update(near([.53,.5],{stage:'framing',completed:['background']}),2300);
  check(aim.sample(2316).success===null, 'Completed alignment replayed the success pulse');

  const skipped=new AimMotion(1);
  skipped.update(near([.54,.5],{hold:.4}),0); skipped.sample(0);
  skipped.update(near([.54,.5],{stage:'framing',skipped:['background']}),100);
  check(skipped.sample(116).success===null, 'Manual skip incorrectly played alignment success');
  check(!skipped.sample(132).completed, 'Skipping was recorded as verified completion');

  // Explicit loss removes ready feedback immediately and fades the old dot briefly.
  const lost=new AimMotion(1);
  lost.update(near([.54,.5],{hold:.5}),0); advance(lost,0,96);
  lost.update(result(null),100);
  state=lost.sample(116);
  check(!state.near && state.success===null && state.progress===0, 'Loss retained alignment feedback');
  check(visible(state), 'One missed match made the dot disappear immediately');
  state=advance(lost,132,700);
  check(!visible(state), 'Missing target remained visible beyond the loss grace period');
  findings.lossHidden=true;
  lost.update(result([.72,.5]),800); lost.sample(800);
  check(!visible(lost.sample(5400)), 'No new results left a stale target visible indefinitely');
  lost.reset(.6);
  state=lost.sample(5500);
  check(!visible(state) && !state.near && state.success===null, 'Reset retained another source target or success');

  // Neither later drift nor missing background can resurrect the completed alignment dot.
  const completed=new AimMotion(1);
  completed.update(near([.53,.5],{hold:.7}),0); completed.sample(0);
  completed.update(near([.53,.5],{stage:'framing',completed:['background']}),100); completed.sample(116);
  completed.update(result([.7,.5]),150);
  state=completed.sample(166);
  check(state.completed && !visible(state) && state.success!==null,
    'Later drift reopened the dot or discarded the verified completion pulse');
  completed.update(result(null,{stage:'framing'}),200);
  state=completed.sample(216);
  check(state.completed && !visible(state) && state.success!==null,
    'Background loss forgot an already completed alignment');
  completed.reset(.6,true);
  state=completed.sample(300);
  check(state.completed && !visible(state) && Math.abs(state.success-.125)<.0001,
    'Zoom reset discarded or restarted the existing completion animation');
  completed.update(near([.55,.5],{stage:'framing',completed:['background']}),800);
  check(Math.abs(completed.sample(900).success-.5)<.0001,
    'Zoom verification restarted the completion pulse');
  state=completed.sample(1701);
  check(state.completed && state.success===null && !visible(state),
    'Completion latch expired with its animation');
  completed.reset(.6);
  state=completed.sample(1800);
  check(!state.completed && state.success===null && !visible(state), 'Explicit restart retained completion state');
  completed.update(result([.72,.5]),1900);
  check(visible(completed.sample(1916)), 'Explicit restart could not acquire a new dot');
  findings.completionPersistsUntilReset=true;
  findings.zoomPreservesSuccessAge=true;

  // A new tracked scene must acquire its own point, never glide from another scene's old point.
  const anchored=(point,id)=>result(point,{background:{tracking:true,current:point,centerOk:false,sceneLocked:true,anchorId:id}});
  const anchor=new AimMotion(1);
  anchor.update(anchored([.74,.5],1),0); anchor.sample(0);
  for(let at=100;at<=800;at+=100) {
    anchor.update(anchored([.74+(at%200 ? .001 : -.001),.5],1),at);
    state=advance(anchor,at,at+96);
    check(Math.abs(state.point[0]-.74)<.0001,'Subpixel locked-anchor noise moved the stationary dot');
  }
  anchor.update(anchored([.24,.5],2),1000);
  state=anchor.sample(1016);
  check(visible(state) && Math.abs(state.point[0]-.24)<.002,
    'New scenery anchor slid from the old scene or was rejected as an old-anchor jump');
  anchor.update(anchored([.25,.5],2),1100);
  state=advance(anchor,1116,1496);
  check(Math.abs(state.point[0]-.25)<.003,'New scenery anchor failed to follow subsequent movement');
  findings.newAnchorDoesNotSlideAcrossScenes=true;

  // Once slow phone inference cadence is known, keep the dot between 2 s results.
  // Animation time itself must never finish the actual alignment gate.
  const slow=new AimMotion(.6);
  slow.update(near([.54,.5],{hold:.25}),0); slow.sample(0);
  slow.update(near([.542,.5],{hold:.35}),2000);
  for (let at=2016; at<4000; at+=16) {
    state=slow.sample(at);
    check(visible(state) && state.opacity>.95 && state.near,
      'Known 2 s inference cadence faded a still-current marker');
    check(state.progress<1 && state.success===null,
      'Elapsed animation time completed alignment without fresh verification');
  }
  slow.update(near([.538,.501],{hold:.55}),4000);
  for (let at=4016; at<6000; at+=16) {
    state=slow.sample(at);
    check(visible(state) && state.near && state.success===null && state.progress<1,
      'Slow inference lost its marker or produced false alignment success');
  }
  state=slow.sample(8501);
  check(!visible(state) && !state.near && state.success===null,
    'Adaptive mobile timeout retained a stale marker beyond 4.5 s');
  findings.lowFrequency={sampleIntervalMs:2000, keptBetweenSamples:true, expiredAfterMs:4501};
  return findings;
}"""


RENDER = """async () => {
  const {drawGuide} = await import('/static/overlay.js');
  document.body.innerHTML='<canvas id="aim" style="width:450px;height:600px;background:#20382f"></canvas>';
  document.body.style.margin='0';
  const canvas=document.getElementById('aim');
  const ref={aimPoint:[.5,.5],centerTolerance:.08};
  const result={stage:'background',hold:0,skipped:[],background:{tracking:true,current:[.73,.5],centerOk:false}};
  const state={point:[.73,.5],opacity:1,near:false,progress:0,success:null};
  drawGuide(canvas,result,ref,'background',state);
  const c=canvas.getContext('2d'), w=canvas.width,h=canvas.height;
  const data=c.getImageData(0,0,w,h).data;
  let whiteDot=0, whiteRing=0, yellow=0;
  for(let y=0;y<h;y++) for(let x=0;x<w;x++) {
    const i=4*(y*w+x);
    if(data[i+3]<190) continue;
    if(data[i]>220 && data[i+1]>220 && data[i+2]>210) {
      if(Math.hypot(x-.73*w,y-.5*h)<12) whiteDot++;
      if(Math.hypot(x-.5*w,y-.5*h)<.12*Math.min(w,h)) whiteRing++;
    }
    if(data[i]>220 && data[i+1]>180 && data[i+2]<120) yellow++;
  }
  if(whiteDot<15 || whiteRing<30 || yellow) throw new Error('Expected a white target and white center ring without a yellow moving dot');
  drawGuide(canvas,
    {...result,stage:'framing',completed:['background']},
    {...ref,landmarks:[]},'framing',
    {...state,point:null,completed:true,success:null});
  const finished=c.getImageData(0,0,w,h).data;
  for(let i=3;i<finished.length;i+=4) if(finished[i])
    throw new Error('Framing redrew center rings after alignment completed');
  drawGuide(canvas,result,ref,'background',state);
  window.renderAimSuccess=()=>drawGuide(canvas,
    {...result,stage:'framing',completed:['background'],background:{...result.background,centerOk:true}},
    {...ref,landmarks:[]},'framing',
    {...state,point:null,completed:true,near:false,progress:0,success:.35});
  return {whiteDot,whiteRing,yellow,completedRingsHidden:true};
}"""


def main():
    qa = ROOT / "build/qa"
    qa.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(ROOT)))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as pw:
            cached = list((ROOT.parent / ".browser").glob("chromium_headless_shell-*/**/chrome-headless-shell.exe"))
            executable = os.environ.get("CHROMIUM_PATH") or (str(cached[0]) if cached else None)
            browser = pw.chromium.launch(executable_path=executable)
            page = browser.new_page(viewport={"width":450,"height":600}, device_scale_factor=1)
            page.goto(f"http://127.0.0.1:{server.server_port}/")
            findings = page.evaluate(CASES)
            findings["render"] = page.evaluate(RENDER)
            page.locator("#aim").screenshot(path=str(qa / "aim-white-dot.png"))
            page.evaluate("window.renderAimSuccess()")
            page.locator("#aim").screenshot(path=str(qa / "aim-success.png"))
            (qa / "aim-motion-results.json").write_text(json.dumps(findings, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(findings, indent=2))
            print("Scenery-attached white dot, new-anchor acquisition, loss expiry, persistent completion, zoom reset and one-time success passed.")
            browser.close()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
