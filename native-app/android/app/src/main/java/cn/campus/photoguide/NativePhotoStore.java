package cn.campus.photoguide;

import android.content.Context;
import java.io.File;
import java.io.IOException;
import java.util.Arrays;
import java.util.Comparator;
import java.util.UUID;

/** Opaque photo IDs, never arbitrary filesystem paths from JavaScript. */
final class NativePhotoStore {
    private NativePhotoStore() { }

    static File create(Context context) throws IOException {
        File dir = new File(context.getCacheDir(), "camera_originals");
        if (!dir.isDirectory() && !dir.mkdirs()) throw new IOException("Cannot create photo cache");
        File[] old = dir.listFiles((folder, name) -> name.matches("[a-f0-9]{32}\\.jpg"));
        if (old != null) {
            Arrays.sort(old, Comparator.comparingLong(File::lastModified).reversed());
            for (int i = 0; i < old.length; i++) {
                if (i >= 3 || System.currentTimeMillis() - old[i].lastModified() > 86_400_000L) old[i].delete();
            }
        }
        return new File(dir, UUID.randomUUID().toString().replace("-", "") + ".jpg");
    }

    static String id(File file) { return file.getName().replace(".jpg", ""); }

    static File get(Context context, String id) throws IOException {
        if (id == null || !id.matches("[a-f0-9]{32}")) throw new IllegalArgumentException("Invalid photo ID");
        File file = new File(new File(context.getCacheDir(), "camera_originals"), id + ".jpg");
        if (!file.isFile() || file.length() < 4 || file.length() > 80L * 1024 * 1024)
            throw new IOException("Photo expired or unavailable");
        return file;
    }
}
