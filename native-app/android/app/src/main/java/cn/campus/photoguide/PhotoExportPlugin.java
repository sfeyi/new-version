package cn.campus.photoguide;

import android.app.Activity;
import android.content.ClipData;
import android.content.ContentResolver;
import android.content.ContentValues;
import android.content.Intent;
import android.graphics.BitmapFactory;
import android.net.Uri;
import android.os.Environment;
import android.provider.MediaStore;
import android.util.Base64;
import androidx.core.content.FileProvider;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.OutputStream;

/** Exports only JPEGs supplied by the local camera UI after an explicit user action. */
@CapacitorPlugin(name = "PhotoExport")
public class PhotoExportPlugin extends Plugin {
    // A distinct provider class keeps this authority separate from Capacitor's file picker.
    public static class SharedPhotoProvider extends FileProvider { }

    private static final int MAX_PHOTO_BYTES = 24 * 1024 * 1024;
    private static final int MAX_BASE64_LENGTH = ((MAX_PHOTO_BYTES + 2) / 3) * 4;

    @PluginMethod
    public void savePhoto(PluginCall call) {
        // Capacitor's task handler keeps decoding and disk I/O off the UI thread.
        execute(() -> {
            ContentResolver resolver = getContext().getContentResolver();
            Uri photoUri = null;
            boolean published = false;
            try {
                byte[] jpeg = readJpeg(call);
                String filename = safeFilename(call.getString("filename"));
                ContentValues values = new ContentValues();
                values.put(MediaStore.Images.Media.DISPLAY_NAME, filename);
                values.put(MediaStore.Images.Media.MIME_TYPE, "image/jpeg");
                values.put(MediaStore.Images.Media.RELATIVE_PATH,
                    Environment.DIRECTORY_PICTURES + "/CampusPhotoGuide");
                values.put(MediaStore.Images.Media.IS_PENDING, 1);
                photoUri = resolver.insert(
                    MediaStore.Images.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY), values);
                if (photoUri == null) throw new IOException("MediaStore insert failed");
                try (OutputStream output = resolver.openOutputStream(photoUri, "w")) {
                    if (output == null) throw new IOException("Photo stream unavailable");
                    output.write(jpeg);
                }
                ContentValues completed = new ContentValues();
                completed.put(MediaStore.Images.Media.IS_PENDING, 0);
                if (resolver.update(photoUri, completed, null, null) != 1) {
                    throw new IOException("MediaStore publish failed");
                }
                published = true;
                JSObject result = new JSObject();
                result.put("uri", photoUri.toString());
                call.resolve(result);
            } catch (IllegalArgumentException error) {
                call.reject("照片数据无效或超过 24 MB，请重新拍摄。", "INVALID_PHOTO");
            } catch (Exception error) {
                call.reject("保存照片失败，请检查手机剩余空间后重试。", "SAVE_FAILED");
            } finally {
                // Never leave a partially written, pending image in the photo library.
                if (photoUri != null && !published) {
                    try { resolver.delete(photoUri, null, null); } catch (Exception ignored) { }
                }
            }
        });
    }

    @PluginMethod
    public void sharePhoto(PluginCall call) {
        execute(() -> {
            File photoFile = null;
            try {
                byte[] jpeg = readJpeg(call);
                String filename = safeFilename(call.getString("filename"));
                File directory = new File(getContext().getCacheDir(), "shared_photos");
                if (!directory.isDirectory() && !directory.mkdirs()) {
                    throw new IOException("Share directory unavailable");
                }
                removeExpiredShares(directory);
                photoFile = File.createTempFile("photo-", ".jpg", directory);
                try (OutputStream output = new FileOutputStream(photoFile)) {
                    output.write(jpeg);
                }
                Uri photoUri = FileProvider.getUriForFile(getContext(),
                    getContext().getPackageName() + ".photos", photoFile);
                Intent send = new Intent(Intent.ACTION_SEND);
                send.setType("image/jpeg");
                send.putExtra(Intent.EXTRA_STREAM, photoUri);
                send.putExtra(Intent.EXTRA_TITLE, filename);
                send.setClipData(ClipData.newUri(getContext().getContentResolver(), filename, photoUri));
                send.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
                Intent chooser = Intent.createChooser(send, "分享照片");
                chooser.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
                chooser.setClipData(send.getClipData());
                File sharedFile = photoFile;
                Activity activity = getActivity();
                activity.runOnUiThread(() -> {
                    try {
                        if (activity.isFinishing() || activity.isDestroyed()) {
                            throw new IllegalStateException("Activity unavailable");
                        }
                        activity.startActivity(chooser);
                        JSObject result = new JSObject();
                        result.put("uri", photoUri.toString());
                        // This reports that the chooser opened, not that the recipient saved it.
                        call.resolve(result);
                    } catch (Exception error) {
                        sharedFile.delete();
                        call.reject("无法打开分享面板，请先保存到相册。", "SHARE_FAILED");
                    }
                });
            } catch (IllegalArgumentException error) {
                if (photoFile != null) photoFile.delete();
                call.reject("照片数据无效或超过 24 MB，请重新拍摄。", "INVALID_PHOTO");
            } catch (Exception error) {
                if (photoFile != null) photoFile.delete();
                call.reject("准备分享失败，请检查手机剩余空间后重试。", "SHARE_FAILED");
            }
        });
    }

    private static byte[] readJpeg(PluginCall call) {
        String base64 = call.getString("base64");
        if (base64 == null || base64.isEmpty() || base64.length() > MAX_BASE64_LENGTH) {
            throw new IllegalArgumentException("Invalid photo size");
        }
        byte[] jpeg = Base64.decode(base64, Base64.NO_WRAP);
        if (jpeg.length < 4 || jpeg.length > MAX_PHOTO_BYTES
                || (jpeg[0] & 0xff) != 0xff || (jpeg[1] & 0xff) != 0xd8
                || (jpeg[2] & 0xff) != 0xff
                || (jpeg[jpeg.length - 2] & 0xff) != 0xff
                || (jpeg[jpeg.length - 1] & 0xff) != 0xd9) {
            throw new IllegalArgumentException("Invalid JPEG");
        }
        BitmapFactory.Options bounds = new BitmapFactory.Options();
        bounds.inJustDecodeBounds = true;
        BitmapFactory.decodeByteArray(jpeg, 0, jpeg.length, bounds);
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0 || !"image/jpeg".equals(bounds.outMimeType)) {
            throw new IllegalArgumentException("Unreadable JPEG");
        }
        return jpeg;
    }

    private static String safeFilename(String requested) {
        String name = requested == null ? "" : requested;
        if (name.length() > 120) name = name.substring(0, 120);
        name = name.replaceAll("[^\\p{L}\\p{N}._-]", "_")
            .replaceFirst("(?i)\\.jpe?g$", "").replaceAll("^[._-]+", "");
        if (name.isEmpty()) name = "CampusPhotoGuide-" + System.currentTimeMillis();
        return name + ".jpg";
    }

    private static void removeExpiredShares(File directory) {
        File[] previous = directory.listFiles();
        if (previous == null) return;
        long cutoff = System.currentTimeMillis() - 24L * 60 * 60 * 1000;
        for (File file : previous) {
            if (file.isFile() && file.getName().startsWith("photo-") && file.lastModified() < cutoff) {
                file.delete();
            }
        }
    }
}
