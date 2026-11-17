"""Native camera bridge contract: independent still capture, no WebView frame reads.

CameraX/MediaStore hardware is simulated. Android compilation/lint runs separately.
"""
import base64
import io
import json
from functools import partial
from http.server import ThreadingHTTPServer
from threading import Thread

from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright, expect

from frame_pipeline_flow import ROOT, Site, MOCK
from native_app_flow import browser_path


NATIVE = r"""
qa.starts=[];qa.stops=[];qa.samples=0;qa.captures=0;qa.saves=[];qa.shares=[];qa.zoomCommands=[];
qa.nativeActive=0;qa.nativePeak=0;qa.failCapture=false;qa.failStart=false;qa.failZoom=false;qa.focuses=[];
Object.defineProperty(navigator.mediaDevices,'getUserMedia',{value:async()=>{throw new Error('Native mode opened WebView camera');}});
const originalDraw=CanvasRenderingContext2D.prototype.drawImage;
CanvasRenderingContext2D.prototype.drawImage=function(...args){
 if(args[0] instanceof HTMLVideoElement)throw new Error('Native mode copied a WebView video frame');
 return originalDraw.apply(this,args);
};
const plugin={
 addListener:async(name,callback)=>{qa.cameraListener=callback;return{remove:async()=>{}};},
 start:async options=>{
  qa.starts.push(options);if(qa.failStart)throw new Error('原生相机初始化失败');
  qa.info={token:options.token,active:true,width:960,height:1280,photoWidth:1848,photoHeight:4000,zoom:1,minZoom:1,maxZoom:6,binding:1};
  return {...qa.info};
 },
 stop:async options=>{qa.stops.push(options);},
 sample:async()=>{
  qa.samples++;qa.nativeActive++;qa.nativePeak=Math.max(qa.nativePeak,qa.nativeActive);
  try{await new Promise(r=>setTimeout(r,40));return{token:qa.info.token,base64:qa.jpeg,width:296,height:640,processingMs:15};}
  finally{qa.nativeActive--;}
 },
 takePhoto:async options=>{
  qa.captures++;await new Promise(r=>setTimeout(r,120));
  if(qa.failCapture)throw new Error('原生拍照失败，请重试');
  return {token:options.token,photoId:'a'.repeat(31)+String(qa.captures),width:1848,height:4000,bytes:2512345,previewBase64:qa.jpeg};
 },
 setZoom:async options=>{
  qa.zoomCommands.push(options);if(qa.failZoom)throw new Error('Lens rejected zoom');
  qa.info.zoom=options.zoom;return {...qa.info};
 },
 focus:async options=>{qa.focuses.push(options);}
};
Capacitor.Plugins.NativeCamera=plugin;
Capacitor.Plugins.PhotoExport={savePhoto:async data=>{qa.saves.push(data);return {uri:'content://qa/original'};},
 sharePhoto:async data=>{qa.shares.push(data);return {uri:'content://qa/shared-original'};}};
const originalRequest=Capacitor.Plugins.CapacitorHttp.request;
Capacitor.Plugins.CapacitorHttp.request=async options=>{
 const result=await originalRequest(options);
 if(options.url.endsWith('/api/recommend')) {qa.recommendPayload=options.data;result.data={scene_used:true,recommendations:[]};}
 return result;
};
"""


def main():
    image = Image.new('RGB', (296, 640), '#226b54')
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 100, 250, 550), outline='white', width=8)
    data = io.BytesIO(); image.save(data, 'JPEG', quality=75)
    jpeg = data.getvalue()
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Site, directory=ROOT))
    Thread(target=server.serve_forever, daemon=True).start()
    report = {'passed': False, 'scope': 'Real UI and bridge facade; simulated CameraX/MediaStore, not device image quality'}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=browser_path())
            ctx = browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
            ctx.add_init_script(MOCK.replace("'getUserMedia',{value:", "'getUserMedia',{configurable:true,value:") +
                                'qa.jpeg=' + json.dumps(base64.b64encode(jpeg).decode()) + ';' + NATIVE)
            page = ctx.new_page(); errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(f'http://127.0.0.1:{server.server_port}/')
            expect(page.locator('#source-state')).to_have_text('相机取景')
            assert page.evaluate('qa.starts.length') == 1
            assert page.evaluate("getComputedStyle(document.body).backgroundColor") == 'rgba(0, 0, 0, 0)'
            assert page.evaluate("getComputedStyle(document.querySelector('#viewport')).backgroundColor") == 'rgba(0, 0, 0, 0)'
            page.wait_for_timeout(1300)
            assert page.evaluate('qa.samples') == 0, 'Preview alone sampled camera pixels'
            page.locator('#shutter').click()
            expect(page.locator('#photo-info')).to_contain_text('1848 × 4000')
            expect(page.locator('#export')).to_have_attribute('data-capture', 'native')
            page.locator('#download-shot').click()
            page.wait_for_function('qa.saves.length===1')
            saved = page.evaluate('qa.saves[0]')
            assert saved['photoId'] == 'a' * 31 + '1' and 'base64' not in saved
            page.locator('#share-shot').click(); page.wait_for_function('qa.shares.length===1')
            assert page.evaluate('qa.shares[0].photoId') == saved['photoId']
            assert page.evaluate('qa.samples') == 0, 'High-resolution capture used the analysis sampler'
            page.locator('#phone-export-sheet [data-close]').click()

            page.locator('#style-request').fill('自然校园')
            page.locator('#style-submit').click()
            page.locator('#computer-server').fill('http://127.0.0.1:7862')
            page.locator('#computer-connect-button').click()
            page.wait_for_function('!!qa.recommendPayload')
            assert page.evaluate('qa.samples') == 1
            assert page.evaluate('qa.recommendPayload.scene_image') == base64.b64encode(jpeg).decode()

            page.locator('#ref-file').set_input_files({'name':'portrait.jpg', 'mimeType':'image/jpeg', 'buffer':jpeg})
            expect(page.locator('body')).to_have_attribute('data-reference', 'loaded')
            expect(page.locator('#shutter')).to_be_enabled(timeout=9000)
            assert page.evaluate('qa.frames.length') > 0
            encoded = page.evaluate('qa.frames[0].data')
            with Image.open(io.BytesIO(base64.b64decode(encoded))) as frame:
                assert max(frame.size) == 640
            page.locator('#shutter').click()
            expect(page.locator('#phone-export-sheet')).to_be_visible()
            page.locator('#download-shot').click(); page.wait_for_function('qa.saves.length===2')
            assert page.evaluate('qa.saves[1].photoId') == 'a' * 31 + '2'
            page.locator('#phone-export-sheet [data-close]').click()

            page.locator('#phone-settings').click(); page.locator('#pause-analysis').click()
            page.wait_for_timeout(300)
            count = page.evaluate('qa.samples'); stops = page.evaluate('qa.stops.length')
            page.wait_for_timeout(1400)
            assert page.evaluate('qa.samples') == count and page.evaluate('qa.stops.length') == stops
            page.locator('#phone-settings-sheet [data-close]').click()
            page.locator('#zoom-dial').press('ArrowRight')
            page.wait_for_function('qa.info.zoom>1')
            assert page.evaluate("document.querySelector('#video').style.transform") == ''
            page.evaluate('qa.failZoom=true')
            page.locator('#zoom-dial').press('ArrowRight'); page.wait_for_timeout(400)
            assert page.evaluate("document.querySelector('#video').style.transform") == ''
            assert abs(float(page.locator('#zoom').input_value()) - page.evaluate('qa.info.zoom')) < .001
            page.evaluate('qa.cameraListener({...qa.info,active:false});qa.info.zoom=1.5;qa.info.binding=2;qa.cameraListener({...qa.info});')
            expect(page.locator('#zoom')).to_have_value('1.5')

            page.locator('#change-style').click()
            page.evaluate('qa.failCapture=true')
            page.locator('#shutter').click()
            expect(page.locator('#hint')).to_contain_text('原生拍照失败',timeout=6000)
            assert page.evaluate('qa.saves.length') == 2
            expect(page.locator('#phone-export-sheet')).not_to_be_visible()
            page.locator('#flip').click()
            page.wait_for_function("qa.starts.length===2 && qa.starts[1].facing==='user'")
            expect(page.locator('#source-state')).to_have_text('相机取景')
            page.evaluate("qa.cameraListener({...qa.info,active:false,error:'相机已断开'});")
            expect(page.locator('#surface')).to_be_hidden()
            page.locator('#phone-open-camera').click()
            expect(page.locator('#source-state')).to_have_text('相机取景')
            page.evaluate('qa.failStart=true')
            page.locator('#flip').click()
            expect(page.locator('#hint')).to_contain_text('原生相机初始化失败',timeout=6000)
            expect(page.locator('#surface')).to_be_hidden()
            assert page.evaluate('qa.nativePeak') == 1
            assert not errors, errors
            report.update(passed=True,no_webview_camera_or_readback=True,preview_does_not_sample=True,
                still_capture_independent=True,original_file_id_saved_and_shared=True,sample_long_edge=640,
                preview_size_independent_of_photo_size=True,pause_preserves_native_preview=True,
                native_zoom_without_css_crop=True,no_silent_low_resolution_fallback=True,single_sample_inflight=True,
                camera_switch_and_error=True,rebind_restores_zoom=True,camera_loss_recovers_without_recursion=True,page_errors=errors)
            browser.close()
    finally:
        server.shutdown(); server.server_close()
        (ROOT/'build/qa/native-capture-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__ == '__main__': main()
