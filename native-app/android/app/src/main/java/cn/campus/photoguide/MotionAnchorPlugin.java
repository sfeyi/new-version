package cn.campus.photoguide;

import android.content.Context;
import android.hardware.Sensor;
import android.hardware.SensorEvent;
import android.hardware.SensorEventListener;
import android.hardware.SensorManager;
import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

/** Small orientation messages only; never accesses or copies camera frames. */
@CapacitorPlugin(name = "MotionAnchor")
public class MotionAnchorPlugin extends Plugin implements SensorEventListener {
    private SensorManager manager;
    private Sensor sensor;
    private boolean running;
    private boolean requested;
    private long lastSent;
    private int session;
    private final float[] matrix = new float[9];

    private boolean begin() {
        if (running) return true;
        manager = (SensorManager) getContext().getSystemService(Context.SENSOR_SERVICE);
        if (manager == null) return false;
        // Relative yaw avoids magnetometer north corrections while composing a shot.
        sensor = manager.getDefaultSensor(Sensor.TYPE_GAME_ROTATION_VECTOR);
        if (sensor == null) sensor = manager.getDefaultSensor(Sensor.TYPE_ROTATION_VECTOR);
        if (sensor == null) return false;
        lastSent = 0;
        running = manager.registerListener(this, sensor, SensorManager.SENSOR_DELAY_GAME);
        if (running) session++;
        return running;
    }

    private void end() {
        if (manager != null) manager.unregisterListener(this);
        running = false;
    }

    @PluginMethod
    public void start(PluginCall call) {
        getActivity().runOnUiThread(() -> {
            requested = true;
            JSObject result = new JSObject();
            result.put("available", begin());
            call.resolve(result);
        });
    }

    @PluginMethod
    public void stop(PluginCall call) {
        getActivity().runOnUiThread(() -> {
            requested = false;
            end();
            call.resolve();
        });
    }

    @Override
    public void onSensorChanged(SensorEvent event) {
        if (!running || event.timestamp - lastSent < 33_000_000L) return;
        lastSent = event.timestamp;
        SensorManager.getRotationMatrixFromVector(matrix, event.values);
        JSArray values = JSArray.from(matrix);
        JSObject data = new JSObject();
        data.put("matrix", values);
        data.put("session", session);
        notifyListeners("orientation", data);
    }

    @Override public void onAccuracyChanged(Sensor sensor, int accuracy) { }
    @Override protected void handleOnPause() { end(); }
    @Override protected void handleOnResume() { if (requested) begin(); }
    @Override protected void handleOnDestroy() { requested = false; end(); }
}
