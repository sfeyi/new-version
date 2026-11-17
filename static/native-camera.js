import {nativeAndroid} from './native-export.js';

const plugin=nativeAndroid ? globalThis.Capacitor?.Plugins?.NativeCamera : null;
export const nativeCameraAvailable=!!plugin;

// This facade has a track-shaped zoom API, but never creates a WebView video stream.
class NativeCamera {
  constructor() {this.active=false;this.token=null;this.info={};this.serial=0;this.sampling=false;this.handle=null;}
  update(info) {
    if(info.token!==this.token)return;
    this.info={...this.info,error:null,...info};this.active=!!info.active;
    document.documentElement.classList.toggle('native-camera-active',this.active);
    document.body.classList.toggle('native-camera-active',this.active);
    window.dispatchEvent(new CustomEvent('campus-native-camera-state',{detail:this.info}));
  }
  async start({facing,previewHeight}) {
    if(!this.handle && plugin.addListener)this.handle=await plugin.addListener('state',info=>this.update(info));
    const token=this.token=`camera-${Date.now()}-${++this.serial}`;
    let info;
    try {info=await plugin.start({token,facing,previewHeight});}
    catch(error){if(this.token===token)this.update({token,active:false});throw error;}
    if(this.token!==token)throw new Error('相机开启已取消');
    this.update(info);
    const track={
      supportsDigitalCrop:false,
      getSettings:()=>({width:this.info.width,height:this.info.height,zoom:this.info.zoom||1,engine:'camerax',
        photoWidth:this.info.photoWidth,photoHeight:this.info.photoHeight}),
      getCapabilities:()=>({zoom:{min:this.info.minZoom||1,max:this.info.maxZoom||1,step:.01}}),
      applyConstraints:async options=>{
        const info=await plugin.setZoom({token,zoom:options.advanced[0].zoom});
        if(this.token!==token)throw new Error('相机已切换');this.update(info);
      },
      stop:()=>this.stop(token)
    };
    return {getVideoTracks:()=>[track],getTracks:()=>[track]};
  }
  stop(token=this.token) {
    if(!token)return;
    if(this.token===token){this.update({token,active:false});this.token=null;}
    return plugin.stop({token}).catch(()=>{});
  }
  async sample() {
    if(!this.active || this.sampling)throw new Error('相机小图尚未就绪');
    const token=this.token;this.sampling=true;
    try {
      const data=await plugin.sample({token});
      if(token!==this.token || !this.active)throw new Error('相机已切换');
      if(typeof data.base64!=='string' || data.base64.length>1400000 || Math.max(data.width,data.height)>640)throw new Error('相机小图格式无效');
      const bytes=Uint8Array.from(atob(data.base64),c=>c.charCodeAt(0));
      return {base64:data.base64,blob:new Blob([bytes],{type:'image/jpeg'}),path:'native-camerax',mainMs:0,workerMs:data.processingMs||0};
    } finally {this.sampling=false;}
  }
  async takePhoto() {
    if(!this.active)throw new Error('相机尚未就绪');
    const token=this.token,data=await plugin.takePhoto({token});
    if(token!==this.token || !this.active)throw new Error('相机已切换，请重新拍摄');
    if(!/^[a-f0-9]{32}$/.test(data.photoId) || !(data.width>0&&data.height>0) || typeof data.previewBase64!=='string')throw new Error('照片数据无效');
    return {nativePhotoId:data.photoId,width:data.width,height:data.height,bytes:data.bytes,
      preview:'data:image/jpeg;base64,'+data.previewBase64};
  }
  focus(point) {if(this.active)plugin.focus({token:this.token,x:point[0],y:point[1]}).catch(()=>{});}
}

export const nativeCamera=new NativeCamera();
