"""Prism transition in the real phone UI, with simulated CameraX and geometry.

Optional --scene PATH records a campus-photo preview; no phone FPS/accuracy claim.
"""
import argparse
import base64
import io
import json
import os
import time
from functools import partial
from http.server import ThreadingHTTPServer
from threading import Thread

from PIL import Image, ImageOps
from playwright.sync_api import expect, sync_playwright

from frame_pipeline_flow import ROOT, Site, MOCK
from native_app_flow import browser_path
from native_capture_flow import NATIVE


SCENE = r"""
qa.center=false;qa.effectSamples=[];qa.effectZoom=[];
const sampleCamera=Capacitor.Plugins.NativeCamera.sample;
Capacitor.Plugins.NativeCamera.sample=async options=>{
 qa.effectSamples.push({at:performance.now(),phase:document.querySelector('#overlay')?.dataset.framing});
 return sampleCamera(options);
};
const zoomCamera=Capacitor.Plugins.NativeCamera.setZoom;
Capacitor.Plugins.NativeCamera.setZoom=async options=>{
 qa.effectZoom.push({at:performance.now(),phase:document.querySelector('#framing-glass')?.dataset.phase});
 const info=await zoomCamera(options);
 // A still-image camera stand-in for this preview only, never production code.
 document.querySelector('#qa-camera').style.transform=`scale(${info.zoom})`;
 return info;
};
const requestGeometry=Capacitor.Plugins.CapacitorHttp.request;
Capacitor.Plugins.CapacitorHttp.request=async options=>{
 const r=await requestGeometry(options);
 if(options.url.endsWith('/api/session'))Object.assign(r.data.reference,{width:390,height:844,
   box:[.17,.43,.77,.87],centerTolerance:.08});
 if(options.url.includes('/frame?')) {
  const scale=(qa.info.zoom||1)/1.6,stage=!qa.center?'background':scale<.97?'framing':'position';
  r.data={stage,ready:false,mode:'adjust',message:stage==='background'?'缓慢转动手机，让白点靠近白环':
    stage==='framing'?'保持手机平稳，正在匹配景别':'请模特站进参考人物框',hold:0,boxes:[],selected:null,pose:null,
    skipped:[],completed:qa.center?['background']:[],checks:[qa.center,scale>=.97,false,false],
    background:{tracking:true,sceneLocked:true,anchorId:1,inliers:36,current:[.54,.49],zoom:scale,
      scaleOk:scale>=.97,centerOk:qa.center,anchors:[],control:'scale'},timing:{serverMs:5}};
 }
 return r;
};
"""


LIFECYCLE = r"""async()=>{
 const {FramingGlass}=await import('/static/framing-glass.js');
 const layer=document.querySelector('#framing-glass'),image=document.querySelector('#framing-still');
 const fx=new FramingGlass(layer,image),pause=ms=>new Promise(r=>setTimeout(r,ms));
 const check=(value,message)=>{if(!value)throw new Error(message);};
 const blob=new Blob([Uint8Array.from(atob(qa.jpeg),c=>c.charCodeAt(0))],{type:'image/jpeg'});
 const decode=image.decode.bind(image);let resume;
 image.decode=()=>new Promise(resolve=>resume=resolve);
 fx.play(blob);fx.clear();resume();await pause(30);
 check(layer.hidden&&image.hidden&&!image.hasAttribute('src')&&fx.animations.size===0,'Late decode resurrected a cancelled effect');
 image.decode=decode;
 fx.play(blob);await pause(300);
 check(fx.active&&image.naturalWidth<=640,'Effect did not reuse the small image');
 check(getComputedStyle(layer).pointerEvents==='none','Decoration blocks camera controls');
 for(const element of layer.querySelectorAll('*'))
   check(getComputedStyle(element).backdropFilter==='none','Live backdrop blur was introduced');
 for(const animation of fx.animations.values())for(const frame of animation.effect.getKeyframes())
   check(Object.keys(frame).every(k=>['offset','computedOffset','easing','composite','opacity','transform'].includes(k)),
     'Effect animates a paint/layout property');
 fx.clear();fx.play(null);await pause(80);fx.sync({phase:'zoom'});fx.clear();
 await pause(1200);
 check(layer.hidden&&fx.animations.size===0,'Zoom decoration survived cancellation');
 fx.play(null);await pause(4200);
 check(layer.hidden&&fx.animations.size===0,'Missing geometry left an endless animation');
 return {late_decode_cancelled:true,small_image_only:true,no_live_blur:true,
   transforms_and_opacity_only:true,non_interactive:true,cancelled_zoom_stays_hidden:true,timeout_cleans_up:true};
}"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scene')
    args = parser.parse_args()
    out = ROOT/'build/qa/framing-effects'; out.mkdir(parents=True, exist_ok=True)
    scene = Image.open(args.scene).convert('RGB') if args.scene else Image.new('RGB', (780,1688), '#567270')
    scene = ImageOps.fit(scene, (780,1688))
    photo = io.BytesIO(); scene.save(photo, 'JPEG', quality=90)
    small = io.BytesIO(); scene.resize((296,640)).save(small, 'JPEG', quality=75)
    data = base64.b64encode(small.getvalue()).decode()
    server = ThreadingHTTPServer(('127.0.0.1',0), partial(Site,directory=ROOT))
    Thread(target=server.serve_forever,daemon=True).start()
    report = {'passed':False,'scope':'Real UI animation; simulated native camera and geometric matching'}
    browser_cache=ROOT.parent/'.browser'
    if any(browser_cache.glob('ffmpeg-*')):
        os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH',str(browser_cache))
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=browser_path())
            ctx = browser.new_context(viewport={'width':390,'height':844},is_mobile=True,has_touch=True,
                record_video_dir=out/'video',record_video_size={'width':390,'height':844})
            ctx.add_init_script(MOCK.replace("'getUserMedia',{value:","'getUserMedia',{configurable:true,value:")+
                'qa.jpeg='+json.dumps(data)+';'+NATIVE+SCENE)
            recorded_at=time.monotonic()
            page=ctx.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(f'http://127.0.0.1:{server.server_port}/')
            expect(page.locator('#source-state')).to_have_text('相机取景')
            page.evaluate("""image=>{const el=document.createElement('img');el.id='qa-camera';el.src=image;
              el.style.cssText='object-fit:cover;z-index:0;transition:transform .12s linear';
              document.querySelector('#surface').prepend(el);}""",'data:image/jpeg;base64,'+base64.b64encode(photo.getvalue()).decode())
            page.locator('#phone-settings').click()
            page.locator('#computer-server').fill('http://127.0.0.1:7862')
            page.locator('#computer-connect-button').click()
            expect(page.locator('#computer-status')).to_contain_text('已连接电脑')
            page.locator('#phone-settings-sheet [data-close]').click()
            page.locator('#ref-file').set_input_files({'name':'campus.jpg','mimeType':'image/jpeg','buffer':photo.getvalue()})
            expect(page.locator('#steps .active')).to_have_text('对准')
            page.wait_for_timeout(3800)
            report['preview_start_seconds']=round(time.monotonic()-recorded_at-.8,3)
            page.evaluate('qa.center=true')
            expect(page.locator('#framing-glass')).to_be_visible(timeout=5000)
            page.wait_for_timeout(400)
            page.screenshot(path=out/'01-prism-scan.png')
            expect(page.locator('#framing-glass')).to_have_attribute('data-phase','zoom',timeout=5000)
            page.wait_for_timeout(450)
            page.screenshot(path=out/'02-prism-zoom.png')
            expect(page.locator('#steps .active')).to_have_text('站位',timeout=7000)
            expect(page.locator('#framing-glass')).to_be_hidden()
            page.wait_for_timeout(800)
            page.screenshot(path=out/'03-position.png')
            assert page.evaluate('qa.effectZoom.length')>0
            assert page.evaluate("qa.effectSamples.every(s=>!['outline','scan','zoom'].includes(s.phase))")
            assert page.evaluate('qa.nativePeak')==1
            assert page.evaluate("getComputedStyle(video).filter")=='none'
            assert not errors,errors
            report.update(zoom_tracks_native_command=True,no_extra_samples_during_effect=True,
                          single_sample_inflight=True,no_live_video_filter=True,page_errors=errors)
            report['video']=str(page.video.path())
            ctx.close()
            # Isolate lifecycle checks from the app's own controller.
            ctx=browser.new_context(viewport={'width':390,'height':844})
            page=ctx.new_page()
            page.route('**/static/camera.js',lambda route:route.fulfill(body='',content_type='text/javascript'))
            page.goto(f'http://127.0.0.1:{server.server_port}/')
            page.evaluate("""data=>{window.qa={jpeg:data};document.body.classList.add('phone-ui');
              const s=document.querySelector('#surface');s.hidden=false;s.style.cssText='width:390px;height:844px';}""",data)
            report.update(page.evaluate(LIFECYCLE))
            page.emulate_media(reduced_motion='reduce')
            assert page.evaluate("""async()=>{const {FramingGlass}=await import('/static/framing-glass.js');
              const fx=new FramingGlass(document.querySelector('#framing-glass'),document.querySelector('#framing-still'));
              fx.play(null);fx.sync({phase:'zoom'});const noZoom=fx.phase==='intro'&&fx.animations.size===1;
              await new Promise(r=>setTimeout(r,350));return noZoom&&!fx.active&&fx.layer.hidden;}""")
            report['reduced_motion_short_fade']=True
            report['passed']=True
            browser.close()
    finally:
        server.shutdown();server.server_close()
        (out/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
