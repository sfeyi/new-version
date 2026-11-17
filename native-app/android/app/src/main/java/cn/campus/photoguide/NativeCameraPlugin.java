package cn.campus.photoguide;

import android.Manifest;
import android.content.res.Configuration;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Color;
import android.graphics.Matrix;
import android.graphics.Rect;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Base64;
import android.util.Rational;
import android.util.Size;
import android.view.Surface;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.WebView;
import androidx.annotation.NonNull;
import androidx.camera.core.Camera;
import androidx.camera.core.CameraSelector;
import androidx.camera.core.FocusMeteringAction;
import androidx.camera.core.ImageAnalysis;
import androidx.camera.core.ImageCapture;
import androidx.camera.core.ImageCaptureException;
import androidx.camera.core.ImageProxy;
import androidx.camera.core.Preview;
import androidx.camera.core.ResolutionInfo;
import androidx.camera.core.UseCaseGroup;
import androidx.camera.core.ViewPort;
import androidx.camera.core.ZoomState;
import androidx.camera.core.resolutionselector.AspectRatioStrategy;
import androidx.camera.core.resolutionselector.ResolutionSelector;
import androidx.camera.core.resolutionselector.ResolutionStrategy;
import androidx.camera.lifecycle.ProcessCameraProvider;
import androidx.camera.view.PreviewView;
import androidx.core.content.ContextCompat;
import androidx.exifinterface.media.ExifInterface;
import com.getcapacitor.JSObject;
import com.getcapacitor.PermissionState;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;
import com.google.common.util.concurrent.ListenableFuture;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

/** Preview stays on a native surface. Only requested small samples cross the bridge. */
@CapacitorPlugin(name = "NativeCamera", permissions = {
    @Permission(alias = "camera", strings = { Manifest.permission.CAMERA })
})
public class NativeCameraPlugin extends Plugin {
    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService analysisExecutor = Executors.newSingleThreadExecutor();
    private final ExecutorService photoExecutor = Executors.newSingleThreadExecutor();
    private final AtomicReference<Sample> sample = new AtomicReference<>();
    private final AtomicBoolean sampling = new AtomicBoolean();
    private final AtomicBoolean photographing = new AtomicBoolean();
    private ProcessCameraProvider provider;
    private PreviewView view;
    private Preview preview;
    private ImageCapture capture;
    private ImageAnalysis analysis;
    private Camera camera;
    private volatile String token = "";
    private volatile boolean requested, paused, front;
    private int previewHeight = 720, bindGeneration;
    private float wantedZoom = 1f;
    private PluginCall starting;
    private View.OnLayoutChangeListener webLayout;

    private static final class Sample {
        final PluginCall call;
        final String token;
        final AtomicBoolean done = new AtomicBoolean();
        Sample(PluginCall call, String token) { this.call = call; this.token = token; }
        void fail(String message) { if (done.compareAndSet(false, true)) call.reject(message, "SAMPLE_UNAVAILABLE"); }
        void finish(JSObject result) { if (done.compareAndSet(false, true)) call.resolve(result); }
    }

    @PluginMethod
    public void start(PluginCall call) {
        main.post(() -> {
            String next = call.getString("token", "");
            if (next.isEmpty()) { call.reject("缺少相机会话"); return; }
            closeHardware();
            if (starting != null) starting.reject("相机开启已取消", "CAMERA_CHANGED");
            token = next; starting = call; requested = true; wantedZoom = 1f;
            front = "user".equals(call.getString("facing"));
            int height = call.getInt("previewHeight", 720);
            previewHeight = height == 480 || height == 1080 ? height : 720;
            if (getPermissionState("camera") != PermissionState.GRANTED) {
                requestPermissionForAlias("camera", call, "cameraPermission");
            } else prepare();
        });
    }

    @PermissionCallback
    private void cameraPermission(PluginCall call) {
        main.post(() -> {
            if (call != starting || !requested) return;
            if (getPermissionState("camera") == PermissionState.GRANTED) prepare();
            else failStart("请在应用权限中允许使用相机", "CAMERA_PERMISSION");
        });
    }

    private void prepare() {
        if (paused || !requested) return;
        WebView web = getBridge().getWebView();
        if (view == null) {
            view = new PreviewView(getContext());
            // TextureView composes correctly underneath the transparent WebView controls.
            view.setImplementationMode(PreviewView.ImplementationMode.COMPATIBLE);
            view.setScaleType(PreviewView.ScaleType.FILL_CENTER);
            ViewGroup parent = (ViewGroup) web.getParent();
            parent.addView(view, parent.indexOfChild(web), new ViewGroup.LayoutParams(1, 1));
            webLayout = (v,l,t,r,b,ol,ot,or,ob) -> fitPreview(web);
            web.addOnLayoutChangeListener(webLayout);
        }
        web.setBackgroundColor(Color.TRANSPARENT);
        fitPreview(web); view.setVisibility(View.VISIBLE);
        final int generation = ++bindGeneration;
        ListenableFuture<ProcessCameraProvider> future = ProcessCameraProvider.getInstance(getContext());
        future.addListener(() -> {
            if (generation != bindGeneration || !requested || paused) return;
            try { provider = future.get(); view.post(() -> bind(generation)); }
            catch (Exception error) { failStart("原生相机初始化失败，请重新开启", "CAMERA_START"); }
        }, ContextCompat.getMainExecutor(getContext()));
    }

    private void fitPreview(WebView web) {
        if (view == null) return;
        ViewGroup.LayoutParams params = view.getLayoutParams();
        params.width = Math.max(1, web.getWidth()); params.height = Math.max(1, web.getHeight());
        view.setLayoutParams(params); view.setX(web.getX()); view.setY(web.getY());
    }

    private int rotation() { return view != null && view.getDisplay() != null ? view.getDisplay().getRotation() : Surface.ROTATION_0; }

    private ResolutionSelector resolution(Size preferred, boolean highest) {
        return new ResolutionSelector.Builder()
            .setAspectRatioStrategy(AspectRatioStrategy.RATIO_4_3_FALLBACK_AUTO_STRATEGY)
            .setResolutionStrategy(highest ? ResolutionStrategy.HIGHEST_AVAILABLE_STRATEGY :
                new ResolutionStrategy(preferred, ResolutionStrategy.FALLBACK_RULE_CLOSEST_HIGHER_THEN_LOWER))
            .build();
    }

    private void bind(int generation) {
        if (generation != bindGeneration || !requested || paused) return;
        if (view.getWidth() < 2 || view.getHeight() < 2) { main.postDelayed(() -> bind(generation), 50); return; }
        try {
            int rotation = rotation();
            CameraSelector selector = front ? CameraSelector.DEFAULT_FRONT_CAMERA : CameraSelector.DEFAULT_BACK_CAMERA;
            if (!provider.hasCamera(selector)) { failStart("这台设备没有所选摄像头", "CAMERA_MISSING"); return; }
            preview = new Preview.Builder().setTargetRotation(rotation)
                .setResolutionSelector(resolution(new Size(previewHeight * 4 / 3, previewHeight), false)).build();
            preview.setSurfaceProvider(view.getSurfaceProvider());
            capture = new ImageCapture.Builder().setTargetRotation(rotation)
                .setCaptureMode(ImageCapture.CAPTURE_MODE_MAXIMIZE_QUALITY).setJpegQuality(95)
                .setFlashMode(ImageCapture.FLASH_MODE_OFF)
                .setResolutionSelector(resolution(null, true)).build();
            analysis = new ImageAnalysis.Builder().setTargetRotation(rotation)
                .setResolutionSelector(resolution(new Size(640, 480), false))
                .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST).build();
            analysis.setAnalyzer(analysisExecutor, this::analyze);
            ViewPort port = new ViewPort.Builder(new Rational(view.getWidth(), view.getHeight()), rotation)
                .setScaleType(ViewPort.FILL_CENTER).build();
            camera = provider.bindToLifecycle(getActivity(), selector, new UseCaseGroup.Builder()
                .setViewPort(port).addUseCase(preview).addUseCase(capture).addUseCase(analysis).build());
            ZoomState zoom = camera.getCameraInfo().getZoomState().getValue();
            ListenableFuture<Void> settled = camera.getCameraControl().setZoomRatio(zoom == null ? 1f :
                Math.max(zoom.getMinZoomRatio(), Math.min(wantedZoom, zoom.getMaxZoomRatio())));
            settled.addListener(() -> {
                if (generation != bindGeneration || !requested || paused) return;
                try { settled.get(); }
                catch (Exception error) { failStart("相机尚未稳定，请重新开启", "CAMERA_START"); return; }
                JSObject info = information();
                if (starting != null) { PluginCall call = starting; starting = null; call.resolve(info); }
                notifyListeners("state", info);
            }, ContextCompat.getMainExecutor(getContext()));
        } catch (Exception error) {
            failStart("原生相机无法启动，请关闭其他相机应用后重试", "CAMERA_BIND");
        }
    }

    private JSObject information() {
        JSObject result = new JSObject();
        result.put("token", token); result.put("active", camera != null && !paused); result.put("engine", "camerax"); result.put("binding",bindGeneration);
        ResolutionInfo info = preview == null ? null : preview.getResolutionInfo();
        if (info != null) {
            Size size = info.getResolution(); boolean swap = info.getRotationDegrees() % 180 != 0;
            result.put("width", swap ? size.getHeight() : size.getWidth());
            result.put("height", swap ? size.getWidth() : size.getHeight());
        }
        ZoomState zoom = camera == null ? null : camera.getCameraInfo().getZoomState().getValue();
        result.put("zoom", zoom == null ? 1 : zoom.getZoomRatio());
        result.put("minZoom", zoom == null ? 1 : Math.max(1, zoom.getMinZoomRatio()));
        result.put("maxZoom", zoom == null ? 1 : Math.min(8, zoom.getMaxZoomRatio()));
        ResolutionInfo still = capture == null ? null : capture.getResolutionInfo();
        if (still != null) {
            Rect crop = still.getCropRect(); boolean swap = still.getRotationDegrees() % 180 != 0;
            result.put("photoWidth", swap ? crop.height() : crop.width());
            result.put("photoHeight", swap ? crop.width() : crop.height());
        }
        return result;
    }

    private boolean valid(PluginCall call) {
        if (!requested || paused || camera == null || !token.equals(call.getString("token"))) {
            call.reject("相机尚未就绪，请稍后重试", "CAMERA_CHANGED"); return false;
        }
        return true;
    }

    @PluginMethod
    public void sample(PluginCall call) {
        main.post(() -> {
            if (!valid(call)) return;
            if (!sampling.compareAndSet(false, true)) { call.reject("已有画面正在读取", "SAMPLE_BUSY"); return; }
            Sample task = new Sample(call, token); sample.set(task);
            main.postDelayed(() -> {
                if (task.done.get()) return;
                boolean waiting = sample.compareAndSet(task, null);
                task.fail("暂时没有新画面，请保持相机开启");
                if (waiting) sampling.set(false);
            }, 3500);
        });
    }

    private void analyze(@NonNull ImageProxy frame) {
        Sample task = sample.getAndSet(null);
        if (task == null) { frame.close(); return; }
        Bitmap raw = null, cropped = null, upright = null, small = null;
        boolean closed = false;
        long started = SystemClock.elapsedRealtime();
        try {
            Rect crop = new Rect(frame.getCropRect()); int degrees = frame.getImageInfo().getRotationDegrees();
            boolean mirror = front;
            try { raw = frame.toBitmap(); } finally { frame.close(); closed = true; }
            if (task.done.get()) return;
            if (!token.equals(task.token) || !requested) { task.fail("相机已切换，请重新取景"); return; }
            cropped = Bitmap.createBitmap(raw, crop.left, crop.top, crop.width(), crop.height());
            Matrix transform = new Matrix(); transform.postRotate(degrees); if (mirror) transform.postScale(-1, 1);
            upright = Bitmap.createBitmap(cropped, 0, 0, cropped.getWidth(), cropped.getHeight(), transform, true);
            double factor = Math.min(1, 640.0 / Math.max(upright.getWidth(), upright.getHeight()));
            small = Bitmap.createScaledBitmap(upright, Math.max(1,(int)Math.round(upright.getWidth()*factor)),
                Math.max(1,(int)Math.round(upright.getHeight()*factor)), true);
            ByteArrayOutputStream output = new ByteArrayOutputStream(); small.compress(Bitmap.CompressFormat.JPEG, 75, output);
            JSObject result = new JSObject(); result.put("base64", Base64.encodeToString(output.toByteArray(), Base64.NO_WRAP));
            result.put("width", small.getWidth()); result.put("height", small.getHeight());
            result.put("processingMs", SystemClock.elapsedRealtime()-started); result.put("token", task.token);
            if (token.equals(task.token) && requested && !paused) task.finish(result); else task.fail("相机已切换，请重新取景");
        } catch (Exception error) { task.fail("读取相机小图失败，请重试"); }
        finally {
            if (!closed) frame.close();
            if (small != null && small != upright) small.recycle();
            if (upright != null && upright != cropped) upright.recycle();
            if (cropped != null && cropped != raw) cropped.recycle();
            if (raw != null) raw.recycle();
            sampling.set(false);
        }
    }

    @PluginMethod
    public void setZoom(PluginCall call) {
        main.post(() -> {
            if (!valid(call)) return;
            ZoomState state = camera.getCameraInfo().getZoomState().getValue();
            Double value = call.getDouble("zoom", 1.0);
            if (state == null || !Double.isFinite(value)) { call.reject("倍率无效"); return; }
            wantedZoom = (float)Math.max(Math.max(1,state.getMinZoomRatio()), Math.min(Math.min(8,state.getMaxZoomRatio()),value));
            String session = token;
            ListenableFuture<Void> future = camera.getCameraControl().setZoomRatio(wantedZoom);
            future.addListener(() -> {
                try { future.get(); if (!session.equals(token)) throw new IllegalStateException(); call.resolve(information()); }
                catch (Exception error) { call.reject("镜头变焦未完成，请重试", "ZOOM_FAILED"); }
            }, ContextCompat.getMainExecutor(getContext()));
        });
    }

    @PluginMethod
    public void focus(PluginCall call) {
        main.post(() -> {
            if (!valid(call)) return;
            float x = (float)Math.max(0,Math.min(1,call.getDouble("x", .5)));
            float y = (float)Math.max(0,Math.min(1,call.getDouble("y", .5)));
            camera.getCameraControl().startFocusAndMetering(new FocusMeteringAction.Builder(
                view.getMeteringPointFactory().createPoint(x*view.getWidth(), y*view.getHeight()))
                .setAutoCancelDuration(3, TimeUnit.SECONDS).build());
            call.resolve();
        });
    }

    @PluginMethod
    public void takePhoto(PluginCall call) {
        main.post(() -> {
            if (!valid(call)) return;
            if (!photographing.compareAndSet(false, true)) { call.reject("照片正在拍摄，请稍候", "CAPTURE_BUSY"); return; }
            String session = token;
            final File file;
            try { file = NativePhotoStore.create(getContext()); }
            catch (Exception error) { photographing.set(false); call.reject("无法创建照片，请检查手机存储空间"); return; }
            ImageCapture.Metadata metadata = new ImageCapture.Metadata(); metadata.setReversedHorizontal(front);
            try { capture.takePicture(new ImageCapture.OutputFileOptions.Builder(file).setMetadata(metadata).build(), photoExecutor,
                new ImageCapture.OnImageSavedCallback() {
                    @Override public void onImageSaved(@NonNull ImageCapture.OutputFileResults saved) {
                        try {
                            if (!session.equals(token) || !requested) throw new IllegalStateException("Camera changed");
                            JSObject result = photoResult(file); result.put("token", session); call.resolve(result);
                        } catch (Exception error) { file.delete(); call.reject("照片处理失败，请重新拍摄", "CAPTURE_FAILED"); }
                        finally { photographing.set(false); }
                    }
                    @Override public void onError(@NonNull ImageCaptureException error) {
                        file.delete(); photographing.set(false); call.reject("原生拍照失败，请重试", "CAPTURE_FAILED");
                    }
                });
            } catch (RuntimeException error) {
                file.delete(); photographing.set(false); call.reject("原生拍照未能开始，请重试", "CAPTURE_FAILED");
            }
        });
    }

    private JSObject photoResult(File file) throws Exception {
        ExifInterface exif = new ExifInterface(file); int rotation = exif.getRotationDegrees();
        BitmapFactory.Options options = new BitmapFactory.Options(); options.inJustDecodeBounds = true;
        BitmapFactory.decodeFile(file.getAbsolutePath(), options);
        int width = options.outWidth, height = options.outHeight;
        if (width <= 0 || height <= 0) throw new IllegalStateException("Invalid photo");
        options.inJustDecodeBounds = false; options.inSampleSize = 1;
        while (Math.max(width,height) / options.inSampleSize > 1080) options.inSampleSize *= 2;
        Bitmap thumb = BitmapFactory.decodeFile(file.getAbsolutePath(), options);
        if (thumb == null) throw new IllegalStateException("Invalid thumbnail");
        Matrix matrix = new Matrix(); if (exif.isFlipped()) matrix.postScale(-1,1); matrix.postRotate(rotation);
        Bitmap oriented = Bitmap.createBitmap(thumb,0,0,thumb.getWidth(),thumb.getHeight(),matrix,true);
        ByteArrayOutputStream output = new ByteArrayOutputStream(); oriented.compress(Bitmap.CompressFormat.JPEG,85,output);
        if (oriented != thumb) oriented.recycle(); thumb.recycle();
        JSObject result = new JSObject(); result.put("photoId", NativePhotoStore.id(file));
        result.put("width", rotation % 180 == 0 ? width : height); result.put("height", rotation % 180 == 0 ? height : width);
        result.put("bytes",file.length()); result.put("previewBase64",Base64.encodeToString(output.toByteArray(),Base64.NO_WRAP));
        return result;
    }

    @PluginMethod
    public void stop(PluginCall call) {
        main.post(() -> {
            if (token.equals(call.getString("token"))) {
                requested = false; closeHardware();
                if (starting != null) { starting.reject("相机开启已取消", "CAMERA_CHANGED"); starting = null; }
            }
            call.resolve();
        });
    }

    private void closeHardware() {
        bindGeneration++;
        Sample pending = sample.getAndSet(null); if (pending != null) { pending.fail("相机已暂停"); sampling.set(false); }
        if (analysis != null) analysis.clearAnalyzer();
        if (provider != null && preview != null && capture != null && analysis != null) provider.unbind(preview,capture,analysis);
        camera = null; preview = null; capture = null; analysis = null;
        if (view != null) view.setVisibility(View.INVISIBLE);
    }

    private void failStart(String message, String code) {
        closeHardware(); requested = false;
        if (starting != null) { PluginCall call = starting; starting = null; call.reject(message,code); }
        JSObject info = information(); info.put("error",message); notifyListeners("state",info);
    }

    @Override protected void handleOnPause() { main.post(() -> { paused = true; closeHardware(); notifyListeners("state",information()); }); }
    @Override protected void handleOnResume() { main.post(() -> { paused = false; if (requested && camera == null && getPermissionState("camera") == PermissionState.GRANTED) prepare(); }); }
    @Override protected void handleOnConfigurationChanged(Configuration config) {
        main.postDelayed(() -> { if (requested && !paused && !photographing.get()) { closeHardware(); prepare(); } },350);
    }
    @Override protected void handleOnDestroy() {
        main.post(() -> {
            requested = false; closeHardware();
            if (view != null) ((ViewGroup)view.getParent()).removeView(view);
            if (webLayout != null) getBridge().getWebView().removeOnLayoutChangeListener(webLayout);
            view = null; analysisExecutor.shutdown(); photoExecutor.shutdown();
        });
    }
}
