package cn.campus.photoguide;

import android.os.Bundle;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        // Register before BridgeActivity creates the bridge and its plugin headers.
        registerPlugin(PhotoExportPlugin.class);
        registerPlugin(MotionAnchorPlugin.class);
        super.onCreate(savedInstanceState);
    }
}
