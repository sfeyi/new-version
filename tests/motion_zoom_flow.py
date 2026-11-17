"""Sensor anchoring, camera zoom and glass transition; fake hardware, real browser/Worker."""
import io
import json
from functools import partial
from http.server import ThreadingHTTPServer
from threading import Thread

from PIL import Image
from playwright.sync_api import expect, sync_playwright

from frame_pipeline_flow import ROOT, Site, MOCK
from native_app_flow import browser_path


CASES = r"""async()=>{
  const {SceneMotion,matrixBasis}=await import('/static/scene-motion.js');
  const {CameraZoom}=await import('/static/camera-zoom.js');
  const {FramingSequence}=await import('/static/framing.js');
  const check=(ok,msg)=>{if(!ok)throw new Error(msg);};
  const pause=ms=>new Promise(r=>setTimeout(r,ms));
  const layer=document.createElement('div'),marker=document.createElement('i');
  document.body.append(layer);layer.append(marker);
  const m=new SceneMotion(layer,marker);m.configure(300,400,720,1280,.75);m.setActive(true);
  const orient=(a,t)=>m.orientation({alpha:a,beta:90,gamma:0},t);
  const observation=p=>({background:{tracking:true,current:p,inliers:30,sceneLocked:true,anchorId:1}});
  orient(0,1000);const captured=m.snapshot(1000);
  orient(6,1100);m.observe(observation([.7,.5]),captured,1100);
  check(m.locked&&m.raw[0]>.75,'Network latency used the receive-time orientation');
  const point=[...m.point],ray=[...m.ray];
  for(let i=0;i<10;i++)m.observe(observation([i%2?.2:.85,.42]),m.snapshot(1100),1200+i*100);
  check(JSON.stringify(ray)===JSON.stringify(m.ray)&&JSON.stringify(point)===JSON.stringify(m.point),'Repeated matches relocated the locked point');
  m.observe({background:null},null,2200);
  check(!layer.hidden&&layer.classList.contains('uncertain'),'A missed match removed the inertial marker');
  orient(10,2300);check(m.point[0]>point[0],'Phone rotation did not move the marker');
  const before=[...m.raw];orient(100,2350);
  check(Math.hypot(...m.raw.map((v,i)=>v-before[i]))<.001,'Calibration jump teleported the marker');
  m.reset();check(!m.locked&&layer.hidden,'New source retained the old anchor');
  m.setActive(true);orient(359,3000);m.observe(observation([.5,.5]),m.snapshot(3000),3000);orient(1,3100);
  check(Math.abs(m.raw[0]-.5)<.08,'Heading wraparound jumped across the frame');
  m.dispose();layer.remove();
  let callback,starts=0,stops=0,removed=0;
  const plugin={addListener:async(_,fn)=>{callback=fn;return {remove:async()=>{removed++;}};},
    start:async()=>{starts++;return {available:true};},stop:async()=>{stops++;}};
  const native=new SceneMotion(document.createElement('div'),document.createElement('i'),plugin);
  native.setActive(true);await native.nativeChain;
  callback({matrix:[1,0,0,0,1,0,0,0,1],session:1});
  check(native.snapshot().domain==='native'&&starts===1,'Native relative rotation did not activate');
  native.setActive(false);await native.nativeChain;
  check(stops===1&&removed===1,'Native sensor listener leaked after leaving alignment');native.dispose();
  const rotated=matrixBasis([1,0,0,0,1,0,0,0,1],90);
  check(Math.abs(rotated.right[1]-1)<1e-6&&rotated.forward[2]===-1,'Native rotation lost screen-orientation axes');

  let actual=1,active=0,peak=0,calls=[];
  const track={getCapabilities:()=>({zoom:{min:1,max:6,step:.05}}),getSettings:()=>({zoom:actual}),
    applyConstraints:async o=>{active++;peak=Math.max(peak,active);calls.push(o.advanced[0].zoom);await pause(35);actual=o.advanced[0].zoom;active--;}};
  const z=new CameraZoom();z.attach(track);check(z.max===6&&z.mode==='camera','Native zoom range was ignored');
  z.set(2);z.set(2.5);z.set(3);await pause(350);
  check(actual===3&&z.hardware===3&&z.digital===1&&peak===1,'Lens zoom queued commands or applied a double software crop');
  z.set(4);await pause(105);z.set(5);z.cancel();await pause(400);
  check(actual===4&&calls.at(-1)===4,'Pause allowed a queued zoom to run');
  z.attach({getCapabilities:()=>({}),getSettings:()=>({})});z.set(1.4);
  check(z.mode==='digital'&&z.digital===1.4,'Unsupported camera did not fall back to a bounded crop');
  actual=1;z.attach({...track,applyConstraints:async()=>{}});z.set(2);await pause(200);
  check(z.mode==='digital'&&z.value<=1.6,'Ignored hardware zoom was reported as successful');
  actual=1;z.attach(track);z.set(2);await pause(105);z.attach(null);await pause(100);
  check(z.hardware===1&&z.mode==='digital','Old camera promise changed the new camera');
  const sequence=new FramingSequence();sequence.max=6;sequence.enter(0);sequence.sample(1350);
  check(sequence.beginZoom(1,4,1350)&&sequence.sample(2200).zoom===4,'Framing still clamped real lens zoom to 1.6');
  return {capture_time_rotation:true,anchor_does_not_rematch:true,loss_keeps_marker:true,
    rotation_wrap_and_calibration:true,native_sensor_lifecycle:true,hardware_zoom_serialized:true,no_double_crop:true,
    cancellation_and_fallback:true,hardware_range:true};
}"""


ENHANCEMENTS = r"""
qa.center=false;qa.zoom=1;qa.lensCalls=[];qa.unstable=false;qa.pose=false;
const originalHttp=Capacitor.Plugins.CapacitorHttp.request;
Capacitor.Plugins.CapacitorHttp.request=async options=>{
  const response=await originalHttp(options);
  if(options.url.endsWith('/api/session'))Object.assign(response.data.reference,{
    centerTolerance:.08,box:[.25,.4,.45,.9],
    backgroundOutline:{paths:[[[.15,.1],[.85,.1],[.85,.8]]]},
    outline:{kind:'person',contours:[[[.25,.4],[.45,.4],[.45,.9],[.25,.9]]],details:[]}});
  if(options.url.includes('/frame?')){
    const skipped=(new URL(options.url).searchParams.get('skip')||'').split(',');
    const zoom=qa.zoom*.5,stage=!qa.center?'background':zoom<.9&&!skipped.includes('framing')?'framing':qa.pose?'pose':'position';
    response.data={stage,ready:false,mode:'adjust',message:'跟随提示',hold:0,boxes:[],selected:null,pose:null,
      skipped,completed:qa.center?['background']:[],checks:[qa.center,zoom>=.9,false,false],
      background:{tracking:true,sceneLocked:true,anchorId:1,inliers:36,current:qa.center?[.5,.5]:[qa.unstable?.25:.7,.5],
        zoom,scaleOk:zoom>=.9,centerOk:qa.center,anchors:[],control:'scale'},timing:{serverMs:5}};
  }
  return response;
};
const getCamera=navigator.mediaDevices.getUserMedia;
Object.defineProperty(navigator.mediaDevices,'getUserMedia',{value:async options=>{
 const stream=await getCamera(options),track=stream.getVideoTracks()[0],settings=track.getSettings.bind(track);
 track.getCapabilities=()=>options.video.zoom===true?{zoom:{min:1,max:6,step:.05}}:{};
 track.getSettings=()=>({...settings(),zoom:qa.zoom});
 track.applyConstraints=async constraints=>{
   const command={at:performance.now(),zoom:constraints.advanced[0].zoom};qa.lensCalls.push(command);
   await new Promise(r=>setTimeout(r,25));qa.zoom=command.zoom;command.done=performance.now();
 };
 return stream;
}});
qa.alpha=0;
setInterval(()=>window.dispatchEvent(new DeviceOrientationEvent('deviceorientation',{alpha:qa.alpha,beta:90,gamma:0})),40);
"""


def main():
    server=ThreadingHTTPServer(('127.0.0.1',0),partial(Site,directory=ROOT))
    Thread(target=server.serve_forever,daemon=True).start()
    report={'scope':'Synthetic camera, orientation and native zoom; real Chromium/Worker, not phone hardware'}
    qa=ROOT/'build/qa';qa.mkdir(parents=True,exist_ok=True)
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=browser_path())
            ctx=browser.new_context(viewport={'width':390,'height':844},is_mobile=True,has_touch=True)
            ctx.add_init_script(MOCK.replace("'getUserMedia',{value:","'getUserMedia',{configurable:true,value:")+ENHANCEMENTS)
            page=ctx.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(f'http://127.0.0.1:{server.server_port}/')
            report.update(page.evaluate(CASES))
            page.locator('#phone-settings').click()
            page.locator('#computer-server').fill('http://127.0.0.1:7862')
            page.locator('#computer-connect-button').click()
            expect(page.locator('#computer-status')).to_contain_text('已连接电脑')
            page.locator('#phone-settings-sheet [data-close]').click()
            photo=io.BytesIO();Image.new('RGB',(720,960),(120,140,160)).save(photo,'JPEG')
            page.locator('#ref-file').set_input_files({'name':'reference.jpg','mimeType':'image/jpeg','buffer':photo.getvalue()})
            expect(page.locator('body')).to_have_attribute('data-reference','loaded')
            expect(page.locator('#live-aim')).to_be_visible(timeout=10000)
            before=page.locator('#live-dot').evaluate('(e)=>e.style.transform')
            page.evaluate('qa.unstable=true')
            page.wait_for_timeout(1300)
            assert before==page.locator('#live-dot').evaluate('(e)=>e.style.transform')
            page.evaluate('qa.alpha=4')
            page.wait_for_function('document.querySelector("#live-dot").style.transform!=='+json.dumps(before))
            page.locator('#viewport').screenshot(path=str(qa/'motion-anchor.png'))
            page.evaluate('qa.center=true')
            expect(page.locator('#framing-glass')).to_be_visible(timeout=5000)
            assert page.locator('#framing-still').evaluate('(e)=>e.naturalWidth>0&&e.naturalWidth<=640')
            page.wait_for_timeout(250)
            page.locator('#viewport').screenshot(path=str(qa/'framing-glass.png'))
            expect(page.locator('#live-aim')).to_be_hidden()
            page.wait_for_function('qa.lensCalls.length>0')
            expect(page.locator('#zoom-kind')).to_have_text('镜头变焦')
            page.wait_for_function('qa.zoom>=1.95',timeout=5000)
            expect(page.locator('#steps .active')).to_have_text('站位',timeout=8000)
            assert page.locator('#video').evaluate('(e)=>e.style.transform')==''
            assert page.locator('#zoom').get_attribute('max')=='6'
            for frame in page.evaluate('qa.frames.map(f=>f.at)'):
                assert not any(c['at']<=frame<=c.get('done',c['at']) for c in page.evaluate('qa.lensCalls'))
            page.locator('#viewport').screenshot(path=str(qa/'hardware-framing.png'))
            page.evaluate('qa.pose=true')
            expect(page.locator('#steps .active')).to_have_text('姿势',timeout=5000)
            expect(page.locator('#framing-glass')).to_be_hidden()
            assert page.evaluate('qa.peak')==1
            report.update(real_page_anchor_stable=True,glass_reuses_sample=True,first_scale_drives_lens=True,
                          final_zoom_verified_before_position=True,pose_after_position=True,page_errors=errors)
            # UI cancellation must also stop commands already waiting for the device,
            # not only stop the software animation. An executing command can settle.
            for action in ('auto', 'skip'):
                page.reload()
                page.locator('#ref-file').set_input_files({'name':'reference.jpg','mimeType':'image/jpeg','buffer':photo.getvalue()})
                expect(page.locator('body')).to_have_attribute('data-reference','loaded')
                page.evaluate('qa.center=true')
                page.wait_for_function('qa.lensCalls.some(c=>c.zoom>1.05)&&qa.zoom<1.6',timeout=10000)
                calls=page.evaluate("""action=>{
                    if(action==='auto') {const e=document.querySelector('#auto-zoom');e.checked=false;e.dispatchEvent(new Event('change'));}
                    else document.querySelector('#skip-stage').click();
                    return qa.lensCalls.length;
                }""",action)
                page.wait_for_timeout(1800)
                assert page.evaluate('qa.lensCalls.length')==calls, action
                assert float(page.locator('#zoom').input_value())<1.65
            report['ui_cancels_pending_lens_commands']=True
            assert not errors,errors
            report['passed']=True;browser.close()
    finally:
        server.shutdown();server.server_close()
        (qa/'motion-zoom-results.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps(report))


if __name__=='__main__':main()
