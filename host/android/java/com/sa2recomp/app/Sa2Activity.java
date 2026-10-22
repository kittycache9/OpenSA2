package com.sa2recomp.app;

import android.content.Intent;
import android.os.Build;
import android.os.Bundle;
import android.os.SystemClock;
import android.view.InputDevice;
import android.view.KeyEvent;
import android.view.View;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.view.WindowManager;
import android.widget.Toast;

import java.io.File;
import java.util.ArrayList;

import org.libsdl.app.SDLActivity;

/**
 * The play screen: SDL's activity running the native session (host/android/native,
 * sa2_android_main in libmain.so), which starts the game and runs host 2's player on SDL's
 * thread. Its own process (":play" in the manifest), ended when the activity is destroyed, so a
 * second session starts with fresh native state.
 *
 * Intent extras (from Sa2SetupActivity): sa2.stream (a recorded stream file to play with no
 * game: the renderer's check), sa2.max_frames, sa2.no_audio, sa2.no_pace, and sa2.tweaks (tweak
 * names this session turns on beside the stored ones: GameSettings.CHAO_LAUNCH from the Chao World
 * button).
 *
 * The session is also given the screen's size in pixels in its orientation (--screen WxH), from
 * which it sizes the game's back buffer (SA2_PLAY_RES, runtime/sa2_widescreen.c), so the picture
 * fills the screen with no bars, and the settings screen's choices (GameSettings): the orientation
 * (--orientation, and this activity asks the system for it: the manifest's sensorLandscape is the
 * default's) and the SA2_ settings that are not at their defaults (--setting SA2_K=V).
 */
public class Sa2Activity extends SDLActivity {
    public static final String EXTRA_STREAM = "sa2.stream";
    public static final String EXTRA_MAX_FRAMES = "sa2.max_frames";
    public static final String EXTRA_NO_AUDIO = "sa2.no_audio";
    public static final String EXTRA_NO_PACE = "sa2.no_pace";
    public static final String EXTRA_TWEAKS = "sa2.tweaks";

    private static final long BACK_AGAIN_MS = 2500;
    private boolean mQuitSent;
    private long mLastBack;
    private GameSettings mSettings;

    @Override
    protected String[] getLibraries() {
        return new String[] {"SDL3", "main"};
    }

    @Override
    protected String getMainFunction() {
        return "sa2_android_main";
    }

    @Override
    protected String[] getArguments() {
        ArrayList<String> a = new ArrayList<>();
        a.add("--files");
        a.add(getFilesDir().getAbsolutePath());
        a.add("--cache");
        a.add(getCacheDir().getAbsolutePath());
        a.add("--libdir");
        a.add(getApplicationInfo().nativeLibraryDir);
        GameSettings s = settings();
        Intent it = getIntent();
        String[] launchTweaks = it == null ? null : it.getStringArrayExtra(EXTRA_TWEAKS);
        int[] screen = GameSettings.screen(this);
        if (screen != null) {
            a.add("--screen");
            a.add(s.screenArgument(screen[0], screen[1]));
        }
        if (!GameSettings.LANDSCAPE.equals(s.orientation)) {
            a.add("--orientation");
            a.add(s.orientation);
        }
        if (screen != null) {
            for (String kv : s.sessionSettings(screen[0], screen[1], launchTweaks == null ? new String[0] : launchTweaks)) {
                a.add("--setting");
                a.add(kv);
            }
        }
        File ext = getExternalFilesDir(null);
        if (ext != null) {
            a.add("--external");
            a.add(ext.getAbsolutePath());
        }
        if (it != null) {
            String stream = it.getStringExtra(EXTRA_STREAM);
            if (stream != null && !stream.isEmpty()) {
                a.add("--stream");
                a.add(stream);
                String frames = it.getStringExtra(EXTRA_MAX_FRAMES);
                if (frames != null && !frames.isEmpty()) {
                    a.add("--max-frames");
                    a.add(frames);
                }
            }
            if (it.getBooleanExtra(EXTRA_NO_AUDIO, false)) a.add("--no-audio");
            if (it.getBooleanExtra(EXTRA_NO_PACE, false)) a.add("--no-pace");
        }
        return a.toArray(new String[0]);
    }

    private GameSettings settings() {
        if (mSettings == null) mSettings = GameSettings.load(this);
        return mSettings;
    }

    /** SDL asks for the orientation its hint names when it makes the window: the settings' instead,
     * unless they keep landscape, SDL's own answer to the session's landscape hint. */
    @Override
    public void setOrientationBis(int w, int h, boolean resizable, String hint) {
        int o = settings().requestedOrientation();
        if (o == -1) super.setOrientationBis(w, h, resizable, hint);
        else setRequestedOrientation(o);
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        // Back's note belongs to one session: an older one's must not match this session's end.
        new File(getFilesDir(), CrashReport.END_ASKED_FILE).delete();
        // Portrait or auto-rotate from the start, so the window is not made landscape first.
        int o = settings().requestedOrientation();
        if (o != -1) setRequestedOrientation(o);
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        // The sustained-performance mode holds the clocks to what the device can keep up for
        // hours, below what a frame of the game needs: left off (the native session's header,
        // "Speed on a phone"), whatever else asked for it. The screen stays on.
        if (Build.VERSION.SDK_INT >= 24) getWindow().setSustainedPerformanceMode(false);
        if (Build.VERSION.SDK_INT >= 28) {
            WindowManager.LayoutParams lp = getWindow().getAttributes();
            lp.layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES;
            getWindow().setAttributes(lp);
        }
        hideSystemBars();
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus) hideSystemBars();
    }

    /** Back (the system's, not a gamepad's: SDL takes a pad's) twice within BACK_AGAIN_MS asks the
     * session to end: the player's end request stops the game through its own code, then SDL
     * finishes this activity. Once the end was asked, Back leaves at once. SDL 3 would otherwise
     * hand Back to the player as a key (SDL_SCANCODE_AC_BACK) and never call onBackPressed. */
    @Override
    public boolean dispatchKeyEvent(KeyEvent event) {
        if (event.getKeyCode() == KeyEvent.KEYCODE_BACK && !fromGamepad(event)) {
            if (event.getAction() == KeyEvent.ACTION_UP) backRequest();
            return true;
        }
        return super.dispatchKeyEvent(event);
    }

    @Override
    public void onBackPressed() {
        backRequest();
    }

    private static boolean fromGamepad(KeyEvent event) {
        int s = event.getSource();
        return (s & InputDevice.SOURCE_GAMEPAD) == InputDevice.SOURCE_GAMEPAD
               || (s & InputDevice.SOURCE_JOYSTICK) == InputDevice.SOURCE_JOYSTICK;
    }

    private void backRequest() {
        if (mBrokenLibraries || mQuitSent) {
            if (!isFinishing()) finish();
            return;
        }
        long now = SystemClock.uptimeMillis();
        if (mLastBack != 0 && now - mLastBack <= BACK_AGAIN_MS) {
            mQuitSent = true;
            noteEndRequest();
            SDLActivity.nativeSendQuit();
            return;
        }
        mLastBack = now;
        Toast.makeText(this, "Press Back again to end the game", Toast.LENGTH_SHORT).show();
    }

    /** Writes files/end-requested (CrashReport.END_ASKED_FILE): this process's pid, which names the
     * session, and the time Back asked for the end. A session whose process goes before it writes its
     * "ended:" line is then told apart: gone at once (a third Back finishes this activity) is the
     * player's close; still stopping long after is a game that stopped answering. */
    private void noteEndRequest() {
        File f = new File(getFilesDir(), CrashReport.END_ASKED_FILE);
        try (java.io.FileOutputStream out = new java.io.FileOutputStream(f)) {
            out.write((android.os.Process.myPid() + " " + System.currentTimeMillis() + "\n")
                      .getBytes(java.nio.charset.StandardCharsets.UTF_8));
        } catch (java.io.IOException e) {
            // The setup screen then judges the session by the system's exit record alone.
        }
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        // The native session's state (the player's statics, SDL's) ends with this process.
        System.exit(0);
    }

    @SuppressWarnings("deprecation")
    private void hideSystemBars() {
        if (Build.VERSION.SDK_INT >= 30) {
            getWindow().setDecorFitsSystemWindows(false);
            WindowInsetsController c = getWindow().getInsetsController();
            if (c != null) {
                c.hide(WindowInsets.Type.systemBars());
                c.setSystemBarsBehavior(WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE);
            }
        } else {
            getWindow().getDecorView().setSystemUiVisibility(
                    View.SYSTEM_UI_FLAG_FULLSCREEN | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                    | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                    | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION | View.SYSTEM_UI_FLAG_LAYOUT_STABLE);
        }
    }
}
