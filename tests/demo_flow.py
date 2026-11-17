"""Demonstration mode: real reference preparation, zero continuous camera readback.

Camera/orientation and native export are simulated; this does not measure Xiaomi FPS.
"""
import base64
import functools
import io
import json
import sys
from http.server import ThreadingHTTPServer
from threading import Thread

from PIL import Image
from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

from native_app_flow import ROOT, BRIDGE, Site, QuietServer, SW_SPY, NATIVE_GLOBALS, PLUGIN_EXPORT, browser_path, native_http, upload
from preview_priority_flow import PREVIEW_SPY

READBACK_SPY = """
window.demoQA={videoDraws:0,videoBitmaps:0,workers:0};
const draw=CanvasRenderingContext2D.prototype.drawImage;
CanvasRenderingContext2D.prototype.drawImage=function(...args){
  if(args[0] instanceof HTMLVideoElement) demoQA.videoDraws++;
  return draw.apply(this,args);
};
const bitmap=window.createImageBitmap;
window.createImageBitmap=(...args)=>{if(args[0] instanceof HTMLVideoElement) demoQA.videoBitmaps++;return bitmap(...args);};
window.Worker=new Proxy(window.Worker,{construct(Type,args){demoQA.workers++;return new Type(...args);}});
"""


def main():
    photo=next(ROOT.parent.glob("校园摄影*/**/REF_029.webp"))
    with Image.open(photo) as image:
        image.thumbnail((720,720));buf=io.BytesIO();image.convert('RGB').save(buf,'JPEG',quality=90)
    data_url="data:image/jpeg;base64,"+base64.b64encode(buf.getvalue()).decode()
    qa=ROOT/'build/qa';qa.mkdir(parents=True,exist_ok=True)
    site=ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Site,directory=ROOT/'build/android-web'))
    sys.path.insert(0,str(ROOT))
    from app import app
    computer=make_server('127.0.0.1',0,app,threaded=True,request_handler=QuietServer)
    for server in (site,computer):Thread(target=server.serve_forever,daemon=True).start()
    report={'passed':False,'scope':'Synthetic video and orientation; real PC reference models; not physical-phone FPS'}
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=browser_path())
            ctx=browser.new_context(viewport={'width':390,'height':844},is_mobile=True,has_touch=True)
            ctx.expose_binding('qaNativeHttp',lambda source,options:native_http(options))
            ctx.add_init_script(SW_SPY+PREVIEW_SPY+READBACK_SPY+'window.previewPhoto='+json.dumps(data_url)+';'+
                NATIVE_GLOBALS.replace('window.qaNativeHttp(call.options)','window.previewRequest(call.options)')+
                BRIDGE.read_text(encoding='utf-8')+PLUGIN_EXPORT)
            page=ctx.new_page();errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(f'http://127.0.0.1:{site.server_port}/')
            assert page.locator('#guide-mode').input_value()=='live'
            page.locator('#phone-settings').click()
            page.locator('#guide-mode').select_option('demo')
            page.locator('#computer-server').fill(f'http://127.0.0.1:{computer.server_port}')
            page.locator('#computer-connect-button').click()
            expect(page.locator('#computer-status')).to_contain_text('已连接电脑')
            page.locator('#phone-settings-sheet [data-close]').click()
            page.locator('#ref-file').set_input_files(upload(photo))
            expect(page.locator('body')).to_have_attribute('data-reference','loaded',timeout=90000)
            expect(page.locator('#source-state')).to_have_text('相机取景')
            expect(page.locator('#demo-badge')).to_be_visible()
            expect(page.locator('#pause-analysis')).to_be_hidden()
            expect(page.locator('#demo-dot')).to_be_visible()
            before=page.evaluate('video.currentTime')
            page.wait_for_timeout(8500)
            elapsed=page.evaluate('video.currentTime')-before
            assert elapsed>7.5,elapsed
            expect(page.locator('#demo-dot')).to_be_visible()
            assert page.evaluate('previewQA.frames.length')==0
            assert page.evaluate('demoQA')=={'videoDraws':0,'videoBitmaps':0,'workers':0}
            report.update(no_periodic_readback=True,no_frame_uploads=True,marker_persists_without_matching=True,video_seconds=round(elapsed,2))

            geometry=page.evaluate("""async()=>{
              const {OrientationAnchor,cameraBasis}=await import('./static/demo.js');
              const a=new OrientationAnchor();a.update(cameraBasis(0,90,0));a.place([.7,.42]);
              const turn=a.update(cameraBasis(5,90,0));
              const wrap=new OrientationAnchor();wrap.update(cameraBasis(359,90,0));wrap.place([.5,.5]);
              return {turn,wrap:wrap.update(cameraBasis(1,90,0))};
            }""")
            assert geometry['turn'][0]>.7 and abs(geometry['wrap'][0]-.5)<.1,geometry
            initial=page.locator('#demo-dot').get_attribute('style')
            page.evaluate("window.dispatchEvent(new DeviceOrientationEvent('deviceorientation',{alpha:0,beta:90,gamma:0}))")
            page.wait_for_timeout(60)
            page.evaluate("window.dispatchEvent(new DeviceOrientationEvent('deviceorientation',{alpha:4,beta:90,gamma:0}))")
            page.wait_for_timeout(200)
            assert page.locator('#demo-dot').get_attribute('style')!=initial
            report['orientation_projection']=geometry
            page.screenshot(path=qa/'demo-center.png',full_page=True)
            rect=page.locator('#demo-aim').bounding_box()
            page.locator('#demo-aim').click(position={'x':rect['width']/2,'y':rect['height']/2})
            expect(page.locator('#demo-next')).to_have_text('景别已合适',timeout=4000)
            page.wait_for_function("Number(zoom.value)>1.115 && overlay.dataset.framing==='adjust'",timeout=6000)
            assert page.locator('#auto-zoom-label').inner_text()=='演示变焦'
            page.screenshot(path=qa/'demo-framing.png',full_page=True)
            page.locator('#demo-next').click()
            expect(page.locator('#demo-next')).to_have_text('站位已合适')
            page.locator('#demo-next').click()
            expect(page.locator('#demo-next')).to_have_text('动作已摆好')
            page.screenshot(path=qa/'demo-pose.png',full_page=True)
            page.locator('#demo-next').click()
            expect(page.locator('#shutter')).to_be_enabled()
            assert page.evaluate('previewQA.frames.length')==0
            page.locator('#shutter').click()
            expect(page.locator('#export')).to_be_visible()
            assert page.evaluate('demoQA.videoDraws')==1
            assert page.evaluate('demoQA.videoBitmaps')==0
            page.locator('#download-shot').click()
            expect(page.locator('#hint')).to_contain_text('已保存到相册')
            saved=page.evaluate('nativeQA.calls.find(c=>c.methodName==="savePhoto").options.base64')
            with Image.open(io.BytesIO(base64.b64decode(saved))) as shot:
                assert shot.format=='JPEG' and max(shot.size)>600
                report['clean_photo_dimensions']=list(shot.size)
            assert page.evaluate('previewQA.frames.length')==0
            report.update(manual_steps_and_demo_zoom=True,shutter_reads_once_without_server=True)

            # A pending real inference result must not overwrite the newly selected demonstration.
            page.locator('#phone-export-sheet [data-close]').click()
            page.locator('#phone-settings').click()
            page.evaluate('previewQA.delay=3000')
            page.locator('#guide-mode').select_option('live')
            expect(page.locator('#demo-badge')).to_be_hidden()
            page.wait_for_function('previewQA.frames.length>0',timeout=15000)
            page.locator('#guide-mode').select_option('demo')
            page.locator('#phone-settings-sheet [data-close]').click()
            expect(page.locator('#demo-badge')).to_be_visible()
            count=page.evaluate('previewQA.frames.length')
            page.wait_for_timeout(4500)
            assert page.evaluate('previewQA.frames.length')==count
            expect(page.locator('#demo-next')).to_have_text('确认对准')
            expect(page.locator('#demo-dot')).to_be_visible()
            expect(page.locator('#shutter')).to_be_disabled()
            report['mode_switch_discards_inflight_live_result']=True
            assert not errors,errors
            report.update(passed=True,page_errors=errors)
            ctx.close();browser.close()
    finally:
        for server in (site,computer):server.shutdown();server.server_close()
        (qa/'demo-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':main()
