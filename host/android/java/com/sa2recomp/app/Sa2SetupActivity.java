package com.sa2recomp.app;

import android.app.Activity;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.database.Cursor;
import android.content.res.ColorStateList;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.InsetDrawable;
import android.graphics.drawable.RippleDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.provider.OpenableColumns;
import android.text.SpannableStringBuilder;
import android.text.Spanned;
import android.util.Log;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * The app's first screen. With no installation it asks for a ZIP of the game folder, picked with
 * the system file picker, copied into the app's cache and verified and extracted by the installer
 * (lib/arm64-v8a/libsa2install.so, host/install's ZIP route: every file the game reads hashed
 * against the install table) into the app's private store, config and overlay; or it verifies a
 * game folder copied into the app's external files folder, game/ (the folder route). With one it runs the installer's
 * quick check (--verify) and starts the play screen (Sa2Activity). When a session ends it shows
 * what the session wrote to files/last-session.txt.
 *
 * When that session did not end cleanly (CrashReport.abnormal), the screen says so. Report on GitHub
 * copies the crash report (CrashReport) and opens the project's bug-report form with the platform
 * and version filled in, for the player to paste it; Copy report, Copy summary (cut to fit a forum
 * comment) and Share hand the text
 * to the clipboard or another app. The report itself never goes into the URL. The app then
 * does not start the game by itself the next time it opens, so a new session does not replace the
 * crashed one's records before the player has copied them; Play or a copy marks the crash seen.
 *
 * Game settings opens the settings screen (Sa2SettingsActivity). Opened while a check runs, it keeps
 * that check from starting the game: the player comes back here and taps Play.
 *
 * When the installer refuses the game, InstallVerdict puts the cause and the fix above its lines; while
 * it runs, a line under the buttons counts the time, since a check can take minutes with no output.
 * A phone whose system declares no Vulkan 1.1 is told so before it imports anything: the renderer's
 * device needs 1.1 (host/d3d9gpu; VulkanSupport).
 *
 * The installer runs with the environment the native session sets (HOME = the files folder,
 * XDG_CONFIG_HOME = files/config, XDG_DATA_HOME = files/data), so both read the same config.
 *
 * Intent extras: sa2.stream (and Sa2Activity's other extras) start the play screen's stream mode
 * at once, the renderer's check with no game; sa2.no_autoplay keeps this screen up. The game starts
 * by itself when the app opens, or when an install finishes, only with Game settings > "Start the game
 * when the app opens" on (off by default); sa2.play, from the Play shortcut, starts it at once.
 *
 * The app's files folder is private on a release build, so this screen copies each kept
 * session's logs and records, and last-session.txt, into the external files folder's sessions/
 * when it opens and when a session ends, where adb and a computer's file manager can read them.
 */
public class Sa2SetupActivity extends Activity {
    private static final String TAG = "sa2recomp";
    public static final String EXTRA_NO_AUTOPLAY = "sa2.no_autoplay";
    public static final String EXTRA_PLAY = "sa2.play";
    private static final String GAME_EXE = "sonic2app.exe";
    private static final int PICK_ZIP = 1;
    private static final String PROJECT = "https://github.com/kittycache9/OpenSA2";
    private static final String STEAM = "https://store.steampowered.com/app/213610/Sonic_Adventure_2/";
    private static final String ISSUES = PROJECT + "/issues";
    /** Who has contributed to OpenSA2 besides its maker, named under the footer's maker line. */
    private static final String[] CONTRIBUTORS = {"heyjoeway"};
    private static final int COPY = 0, SHARE = 1, GITHUB = 2, SUMMARY = 3;

    private TextView mStatus, mBanner, mProgress, mLog;
    private LinearLayout mActions;       // Play and the install buttons, arranged by layoutActions
    private Button mZip, mCheck, mPlay, mSettings, mGithub, mCopy, mSummary, mShare;
    private final List<String> mInstallOut = new ArrayList<>();      // the last installer run's lines
    private String mInstallVerdict;      // InstallVerdict's account of the last refused run; null otherwise
    private int mWaitedForPlay;          // 2 s checks spent waiting for a closing play process
    private final Runnable mRefresh = this::refresh;
    private final Handler mTicker = new Handler(Looper.getMainLooper());
    private boolean mBusy, mAutoPlayDone, mInstallTried, mPlayLaunched, mShowingLast;
    private boolean mHoldPlay;           // the settings were opened during a check: if it passes, it does not start the game
    private final ArrayDeque<String> mLines = new ArrayDeque<>();

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        if (state != null) {
            mAutoPlayDone = state.getBoolean("autoplay_done");
            mInstallTried = state.getBoolean("install_tried");
        }
        buildUi();
        if (state == null) {
            importZip().delete();
            takeIntent(getIntent());
        }
        exportSessions();
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        takeIntent(intent);
    }

    private void takeIntent(Intent it) {
        if (it == null) return;
        if (it.getBooleanExtra(EXTRA_NO_AUTOPLAY, false)) mAutoPlayDone = true;
        if (it.getBooleanExtra(EXTRA_PLAY, false) && configFile().isFile() && !mBusy) {
            mAutoPlayDone = true;
            verifyThenPlay();
        }
        if (it.getStringExtra(Sa2Activity.EXTRA_STREAM) != null) {
            mAutoPlayDone = true;
            Intent play = new Intent(this, Sa2Activity.class);
            play.putExtras(it);
            launchPlay(play);
        }
    }

    @Override
    protected void onSaveInstanceState(Bundle out) {
        super.onSaveInstanceState(out);
        out.putBoolean("autoplay_done", mAutoPlayDone);
        out.putBoolean("install_tried", mInstallTried);
    }

    @Override
    protected void onResume() {
        super.onResume();
        refresh();
    }

    // ------------------------------------------------------------ paths

    private File gameDir() {
        File ext = getExternalFilesDir(null);
        return ext == null ? null : new File(ext, "game");
    }

    private File configFile() {
        return new File(getFilesDir(), "config/sa2recomp/sa2recomp.cfg");
    }

    /** Where a picked ZIP is copied for the installer, which reads a path; removed after the run. */
    private File importZip() {
        return new File(getCacheDir(), "import.zip");
    }

    private File installer() {
        return new File(getApplicationInfo().nativeLibraryDir, "libsa2install.so");
    }

    /** The folder holding sonic2app.exe: game/ itself, or a folder in it (the copied folder
     * whole), or one below that; null when there is none. */
    private File findGameRoot() {
        File g = gameDir();
        if (g == null) return null;
        File hit = holdsExe(g);
        if (hit != null) return hit;
        File[] subs = g.listFiles();
        if (subs == null) return null;
        for (File s : subs) {
            if (!s.isDirectory()) continue;
            hit = holdsExe(s);
            if (hit != null) return hit;
            File[] deeper = s.listFiles();
            if (deeper == null) continue;
            for (File d : deeper) {
                if (d.isDirectory() && holdsExe(d) != null) return d;
            }
        }
        return null;
    }

    private static File holdsExe(File dir) {
        String[] names = dir.list();
        if (names == null) return null;
        for (String n : names) {
            if (n.equalsIgnoreCase(GAME_EXE)) return dir;
        }
        return null;
    }

    static String readText(File f) {
        if (!f.isFile()) return null;
        try (FileInputStream in = new FileInputStream(f)) {
            byte[] b = new byte[(int) Math.min(f.length(), 1 << 20)];
            int n = 0;
            while (n < b.length) {
                int r = in.read(b, n, b.length - n);
                if (r < 0) break;
                n += r;
            }
            return new String(b, 0, n, StandardCharsets.UTF_8);
        } catch (Exception e) {
            return null;
        }
    }

    private File lastSessionFile() {
        return new File(getFilesDir(), "last-session.txt");
    }

    private SharedPreferences prefs() {
        return getSharedPreferences("setup", Context.MODE_PRIVATE);
    }

    /** True, with the screen saying so and a check in 2 s, while the last session's play process is still
     * going away with no "ended:" line written (a third Back returns here at once): its end is judged
     * only once the system has recorded how it ended, for at most a minute. */
    private boolean waitForPlay() {
        String last = readText(lastSessionFile());
        if (last == null || last.contains("\nended: ") || !playRunning()) {
            mWaitedForPlay = 0;
            return false;
        }
        if (mWaitedForPlay++ >= 30) return false;
        mStatus.setText("Waiting for the game to finish closing...");
        mTicker.removeCallbacks(mRefresh);
        mTicker.postDelayed(mRefresh, 2000);
        return true;
    }

    private boolean playRunning() {
        android.app.ActivityManager am = (android.app.ActivityManager) getSystemService(Context.ACTIVITY_SERVICE);
        List<android.app.ActivityManager.RunningAppProcessInfo> ps = am == null ? null : am.getRunningAppProcesses();
        if (ps == null) return false;
        for (android.app.ActivityManager.RunningAppProcessInfo p : ps) {
            if (p.processName != null && p.processName.endsWith(":play")) return true;
        }
        return false;
    }

    /** The last session ended badly and the player has neither copied its report nor played since. */
    private boolean crashUnseen() {
        File f = lastSessionFile();
        return f.isFile() && CrashReport.abnormal(this, readText(f)) && prefs().getLong("crash_seen", 0) != f.lastModified();
    }

    private void markCrashSeen() {
        prefs().edit().putLong("crash_seen", lastSessionFile().lastModified()).apply();
    }

    private String configValue(String key) {
        String text = readText(configFile());
        if (text == null) return null;
        for (String line : text.split("\n")) {
            if (line.startsWith(key + "=")) return line.substring(key.length() + 1);
        }
        return null;
    }

    // ------------------------------------------------------------ the screen

    private void refresh() {
        if (mBusy) return;
        File g = gameDir();
        if (g != null && !g.isDirectory() && !g.mkdirs()) Log.w(TAG, "cannot create " + g);
        if (waitForPlay()) return;
        boolean crashed = crashUnseen();
        String last = mPlayLaunched || crashed || mShowingLast ? readText(lastSessionFile()) : null;
        mPlayLaunched = false;
        mShowingLast = last != null;
        // A shown session holds the screen (autoplay_done is saved with the instance), so a recreated
        // activity does not start the game over the report the player may still be filing.
        if (mShowingLast) mAutoPlayDone = true;
        if (configFile().isFile()) {
            if (mInstallVerdict != null) {
                // A refused reinstall keeps its card until the next install or session.
            } else if (last != null && CrashReport.abnormal(this, last)) {
                banner("The game stopped unexpectedly: " + CrashReport.headline(this, last) + "\n\n"
                       + "Tap Report on GitHub: it copies a crash report and opens the bug-report form, where you "
                       + "paste it and say what you were doing. If you closed the game yourself, just tap Play.");
            } else {
                banner(null);
            }
            mStatus.setText(("Sonic Adventure 2 is installed." + vulkanNote()).trim());
            layoutActions(true);
            if (last != null) {
                exportSessions();
                setLog("The last session:\n" + last);
            } else if (!mAutoPlayDone) {
                mAutoPlayDone = true;
                if (GameSettings.load(this).autoPlay) verifyThenPlay();
            }
            return;
        }
        layoutActions(false);
        if (mInstallVerdict == null) banner(null);
        File root = findGameRoot();
        String where = g == null ? "(no external storage)" : g.getAbsolutePath();
        String note = vulkanNote().trim();
        mStatus.setText((note.isEmpty() ? "" : note + "\n\n") +
                "First run: this app needs your own copy of Sonic Adventure 2 for PC: the version Steam "
                + "installs today (the 2012 release), with no mods.\n\n"
                + "1. On your computer, open the game's folder (Steam: right-click Sonic Adventure 2, "
                + "Manage, Browse local files) and put everything in it into a ZIP.\n"
                + "2. Copy the ZIP to this phone: USB, a cloud drive, or any other way.\n"
                + "3. Tap Choose game ZIP and pick it. Every file the game reads is checked against the "
                + "known hashes and copied into the app's own storage; the ZIP can be deleted afterwards.\n\n"
                + "Or copy the folder itself into\n"
                + "   Internal storage/Android/data/" + getPackageName() + "/files/game/\n"
                + "   (adb push \"Sonic Adventure 2/.\" " + where.replace("/storage/emulated/0", "/sdcard") + "/)\n"
                + "and tap Check game folder.\n\n"
                + (root != null ? "Found " + GAME_EXE + " in " + root.getAbsolutePath()
                                 : "No " + GAME_EXE + " under " + where + " yet."));
        if (root != null && !mInstallTried) {
            mInstallTried = true;
            install();
        }
    }

    // ------------------------------------------------------------ session records

    private static void copyFile(File from, File to) throws java.io.IOException {
        File tmp = new File(to.getPath() + ".part");
        try (InputStream in = new FileInputStream(from); OutputStream out = new FileOutputStream(tmp)) {
            byte[] buf = new byte[1 << 16];
            int n;
            while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
        }
        if (!tmp.renameTo(to)) throw new java.io.IOException("cannot rename " + tmp);
    }

    private static void removeTree(File f) {
        File[] subs = f.listFiles();
        if (subs != null) for (File s : subs) removeTree(s);
        f.delete();
    }

    /** Mirrors files/sessions/<stamp>/* (regular files only: the FIFOs and play/ are left) and
     * files/last-session.txt into the external files folder's sessions/, removing the copies of
     * sessions the native side has since dropped. */
    private void exportSessions() {
        final File ext = getExternalFilesDir(null);
        if (ext == null) return;
        new Thread(() -> {
            File out = new File(ext, "sessions");
            File[] dirs = new File(getFilesDir(), "sessions").listFiles(File::isDirectory);
            java.util.HashSet<String> kept = new java.util.HashSet<>();
            if (dirs != null) {
                for (File d : dirs) {
                    kept.add(d.getName());
                    File[] files = d.listFiles(File::isFile);
                    if (files == null) continue;
                    File o = new File(out, d.getName());
                    if (!o.isDirectory() && !o.mkdirs()) continue;
                    for (File f : files) {
                        File t = new File(o, f.getName());
                        if (t.length() == f.length() && t.lastModified() >= f.lastModified()) continue;
                        try {
                            copyFile(f, t);
                        } catch (Exception e) {
                            Log.w(TAG, "cannot copy " + f + ": " + e);
                        }
                    }
                }
            }
            File[] old = out.listFiles(File::isDirectory);
            if (old != null) for (File o : old) if (!kept.contains(o.getName())) removeTree(o);
            File last = new File(getFilesDir(), "last-session.txt");
            if (last.isFile()) {
                try {
                    out.mkdirs();
                    copyFile(last, new File(out, "last-session.txt"));
                } catch (Exception e) {
                    Log.w(TAG, "cannot copy " + last + ": " + e);
                }
            }
        }).start();
    }

    private void setLog(String text) {
        mLines.clear();
        for (String l : text.split("\n")) mLines.add(l);
        mLog.setText(text);
    }

    private void addLine(String line) {
        mLines.add(line);
        while (mLines.size() > 60) mLines.removeFirst();
        StringBuilder b = new StringBuilder();
        for (String l : mLines) b.append(l).append('\n');
        mLog.setText(b.toString());
    }

    private void setBusy(boolean busy) {
        mBusy = busy;
        if (busy) getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        else getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        mZip.setEnabled(!busy);
        mCheck.setEnabled(!busy);
        mPlay.setEnabled(!busy);
    }

    // ------------------------------------------------------------ the installer

    private interface Done {
        void exited(int code);
    }

    private void runInstaller(final String what, final String[] args, final Done done) {
        setBusy(true);
        setLog(what + "...");
        mInstallOut.clear();
        mInstallVerdict = null;
        final long t0 = SystemClock.elapsedRealtime();
        final Runnable tick = new Runnable() {
            @Override
            public void run() {
                long s = (SystemClock.elapsedRealtime() - t0) / 1000;
                mProgress.setText(String.format(java.util.Locale.US, "%s... %d:%02d\nThis can take several minutes, "
                                  + "and the screen may not change. Keep the app open.", what, s / 60, s % 60));
                mTicker.postDelayed(this, 1000);
            }
        };
        mProgress.setVisibility(View.VISIBLE);
        tick.run();
        new Thread(() -> {
            int code;
            try {
                String[] cmd = new String[args.length + 1];
                cmd[0] = installer().getAbsolutePath();
                System.arraycopy(args, 0, cmd, 1, args.length);
                ProcessBuilder pb = new ProcessBuilder(cmd);
                File files = getFilesDir();
                Map<String, String> env = pb.environment();
                env.put("HOME", files.getAbsolutePath());
                env.put("XDG_CONFIG_HOME", new File(files, "config").getAbsolutePath());
                env.put("XDG_DATA_HOME", new File(files, "data").getAbsolutePath());
                env.put("TMPDIR", getCacheDir().getAbsolutePath());
                new File(files, "logs").mkdirs();
                pb.directory(files);
                pb.redirectErrorStream(true);
                Process p = pb.start();
                try (BufferedReader r = new BufferedReader(new InputStreamReader(p.getInputStream(), StandardCharsets.UTF_8))) {
                    String line;
                    while ((line = r.readLine()) != null) {
                        final String l = line;
                        Log.i(TAG, "installer: " + l);
                        runOnUiThread(() -> {
                            if (mInstallOut.size() < 50000) mInstallOut.add(l);
                            addLine(l);
                        });
                    }
                }
                code = p.waitFor();
            } catch (Exception e) {
                final String m = "cannot run the installer: " + e;
                Log.e(TAG, m);
                runOnUiThread(() -> addLine(m));
                code = -1;
            }
            final int c = code;
            runOnUiThread(() -> {
                mTicker.removeCallbacks(tick);
                mProgress.setVisibility(View.GONE);
                setBusy(false);
                addLine(what + ": exit " + c);
                done.exited(c);
            });
        }).start();
    }

    private void install() {
        final File root = findGameRoot();
        if (root == null) {
            File g = gameDir();
            setLog("No " + GAME_EXE + " under " + (g == null ? "(no external storage)" : g.getAbsolutePath())
                   + ": copy the game folder there first.");
            return;
        }
        File logs = new File(getFilesDir(), "logs");
        runInstaller("Checking the game folder", new String[] {"--source", "folder", root.getAbsolutePath(), "--yes",
                     "--threads", "0", "--record", new File(logs, "install.runrec").getAbsolutePath()}, code -> {
            if (code == 0) {
                final boolean start = GameSettings.load(this).autoPlay;
                addLine("The game folder verified." + (start && !mHoldPlay ? " Starting the game." : " Tap Play."));
                mAutoPlayDone = true;
                refresh();
                if (start) playUnlessHeld();
                else mHoldPlay = false;
            } else {
                mHoldPlay = false;
                mInstallVerdict = InstallVerdict.explain(mInstallOut, code, false);
                banner("The game folder did not pass the check.\n\n" + mInstallVerdict
                                + "\n\nIf you are stuck, tap Report on GitHub: it copies the check's output.");
            }
        });
    }

    // ------------------------------------------------------------ the ZIP route

    private void pickZip() {
        Intent pick = new Intent(Intent.ACTION_OPEN_DOCUMENT);
        pick.addCategory(Intent.CATEGORY_OPENABLE);
        pick.setType("*/*");
        pick.putExtra(Intent.EXTRA_MIME_TYPES, new String[] {"application/zip", "application/x-zip-compressed",
                      "application/x-zip", "multipart/x-zip", "application/octet-stream"});
        try {
            startActivityForResult(pick, PICK_ZIP);
        } catch (android.content.ActivityNotFoundException e) {
            setLog("This device has no file picker: copy the game folder instead (see above).");
        }
    }

    @Override
    protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        if (request != PICK_ZIP || result != RESULT_OK || data == null || data.getData() == null) return;
        mAutoPlayDone = true;
        mHoldPlay = false;
        installZip(data.getData());
    }

    /** The picked document's display name and size in bytes (-1 when the provider does not say). */
    private Object[] describe(Uri uri) {
        String name = uri.getLastPathSegment();
        long size = -1;
        try (Cursor c = getContentResolver().query(uri, new String[] {OpenableColumns.DISPLAY_NAME,
                                                   OpenableColumns.SIZE}, null, null, null)) {
            if (c != null && c.moveToFirst()) {
                if (!c.isNull(0)) name = c.getString(0);
                if (!c.isNull(1)) size = c.getLong(1);
            }
        } catch (RuntimeException e) {
            Log.w(TAG, "cannot describe the picked ZIP: " + e.getClass().getSimpleName());
        }
        return new Object[] {name, size};
    }

    private static String gb(long bytes) {
        return String.format(java.util.Locale.US, "%.2f GB", bytes / 1e9);
    }

    /** Copies the picked ZIP into the cache (the installer reads a path, and a provider's stream
     * may not seek), then runs the installer's ZIP route on the copy and removes it. */
    private void installZip(final Uri uri) {
        final File copy = importZip();
        final long size = (Long) describe(uri)[1];
        long free = getCacheDir().getUsableSpace();
        if (size > 0 && free < size + (64L << 20)) {
            setLog("Not enough free space to copy the ZIP (" + gb(size) + "; " + gb(free) + " free). "
                   + "The import needs room for the ZIP and the extracted game together.");
            return;
        }
        setBusy(true);
        setLog("Copying the ZIP" + (size > 0 ? " (" + gb(size) + ")" : "") + "...");
        new Thread(() -> {
            String err = null;
            long done = 0;
            try (InputStream in = getContentResolver().openInputStream(uri);
                 OutputStream out = new FileOutputStream(copy)) {
                if (in == null) throw new java.io.IOException("the provider gave no stream");
                byte[] buf = new byte[1 << 20];
                long shown = 0;
                int n;
                while ((n = in.read(buf)) > 0) {
                    out.write(buf, 0, n);
                    done += n;
                    if (done - shown >= (64L << 20)) {
                        shown = done;
                        final String line = "  copied " + gb(done) + (size > 0 ? " of " + gb(size) : "");
                        runOnUiThread(() -> addLine(line));
                    }
                }
            } catch (Exception e) {
                // The provider's message can carry the file's full path: the kind of failure is enough.
                err = e.getClass().getSimpleName();
            }
            final String failure = err;
            final long copied = done;
            runOnUiThread(() -> {
                setBusy(false);
                if (failure != null) {
                    mHoldPlay = false;
                    copy.delete();
                    addLine("Cannot copy the ZIP: " + failure + ". Copy it to the phone again and retry.");
                    return;
                }
                addLine("Copied " + gb(copied) + ".");
                File logs = new File(getFilesDir(), "logs");
                runInstaller("Checking the game ZIP", new String[] {"--source", "zip", copy.getAbsolutePath(), "--yes",
                             "--threads", "0", "--record", new File(logs, "install.runrec").getAbsolutePath()}, code -> {
                    copy.delete();
                    if (code == 0) {
                        final boolean start = GameSettings.load(this).autoPlay;
                        addLine("The game ZIP verified." + (start && !mHoldPlay ? " Starting the game." : " Tap Play."));
                        mAutoPlayDone = true;
                        refresh();
                        if (start) playUnlessHeld();
                        else mHoldPlay = false;
                    } else {
                        mHoldPlay = false;
                        mInstallVerdict = InstallVerdict.explain(mInstallOut, code, true);
                        banner("The game ZIP did not pass the check.\n\n" + mInstallVerdict
                                        + "\n\nIf you are stuck, tap Report on GitHub: it copies the check's output.");
                    }
                });
            });
        }).start();
    }

    private void verifyThenPlay() {
        File logs = new File(getFilesDir(), "logs");
        runInstaller("Checking the installation", new String[] {"--verify", "--threads", "0", "--record",
                     new File(logs, "verify.runrec").getAbsolutePath()}, code -> {
            if (code == 0) {
                playUnlessHeld();
            } else {
                mHoldPlay = false;
                banner("The installation no longer verifies (exit " + code + "). Reinstall it from a ZIP, or "
                       + "copy the game folder again and tap Reinstall from folder.");
            }
        });
    }

    /** Starts the game after a check, unless the settings screen was opened while it ran: the
     * player is then back here to tap Play. */
    private void playUnlessHeld() {
        if (mHoldPlay) {
            mHoldPlay = false;
            addLine("The game did not start because the settings were opened. Tap Play.");
            return;
        }
        play();
    }

    private void openSettings() {
        mAutoPlayDone = true;
        mHoldPlay = mBusy;
        startActivity(new Intent(this, Sa2SettingsActivity.class));
    }

    private void play() {
        markCrashSeen();
        mShowingLast = false;
        mInstallVerdict = null;
        launchPlay(new Intent(this, Sa2Activity.class));
    }

    // ------------------------------------------------------------ the crash report

    /** Builds the report off the UI thread (it reads the logs, runs logcat and asks EGL for the GPU),
     * then copies it, offers it to another app, or copies it and opens the bug-report form. */
    private void report(final int mode) {
        setReportEnabled(false);
        final String setupLog = mInstallOut.isEmpty() ? mLog.getText().toString() : String.join("\n", mInstallOut);
        final String verdict = mInstallVerdict;
        Toast.makeText(this, "Collecting the crash report...", Toast.LENGTH_SHORT).show();
        new Thread(() -> {
            String text;
            try {
                // The summary fits a forum comment; the rest take the whole report.
                text = CrashReport.build(getApplicationContext(), setupLog,
                                         mode == SUMMARY ? CrashReport.SUMMARY_CHARS : CrashReport.FULL_CHARS, verdict);
            } catch (RuntimeException e) {
                text = "OpenSA2 crash report\napp: " + CrashReport.appVersion(this) + "\nthe report failed: " + e + "\n";
            }
            final String report = text;
            // Also kept where a computer's file manager (and adb) can read it.
            File ext = getExternalFilesDir(null);
            if (ext != null) {
                try (OutputStream out = new FileOutputStream(new File(ext, "crash-report.txt"))) {
                    out.write(report.getBytes(StandardCharsets.UTF_8));
                } catch (Exception e) {
                    Log.w(TAG, "cannot save the crash report: " + e);
                }
            }
            runOnUiThread(() -> {
                setReportEnabled(true);
                if (verdict == null) markCrashSeen();
                if (mode == SHARE) {
                    Intent send = new Intent(Intent.ACTION_SEND);
                    send.setType("text/plain");
                    send.putExtra(Intent.EXTRA_SUBJECT, "OpenSA2 crash report");
                    send.putExtra(Intent.EXTRA_TEXT, report);
                    try {
                        startActivity(Intent.createChooser(send, "Share the crash report"));
                    } catch (android.content.ActivityNotFoundException e) {
                        Toast.makeText(this, "No app can take the report; use Copy instead.", Toast.LENGTH_LONG).show();
                    }
                    return;
                }
                ClipboardManager cm = (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
                if (cm == null) {
                    setLog(report);
                    Toast.makeText(this, "No clipboard: the report is below, select and copy it.", Toast.LENGTH_LONG).show();
                    return;
                }
                cm.setPrimaryClip(ClipData.newPlainText("OpenSA2 crash report", report));
                if (mode == GITHUB) {
                    openIssueForm(report, verdict);
                    return;
                }
                Toast.makeText(this, (mode == SUMMARY ? "Summary copied (" : "Report copied (") + report.length()
                               + " characters). Paste it into a bug report or a comment.", Toast.LENGTH_LONG).show();
            });
        }).start();
    }

    private void setReportEnabled(boolean on) {
        mGithub.setEnabled(on);
        mCopy.setEnabled(on);
        mSummary.setEnabled(on);
        mShare.setEnabled(on);
    }

    /** The bug-report form (.github/ISSUE_TEMPLATE/bug_report.yml) with its title, platform and
     * version fields, and the device in its Hardware and software field, filled in from the query (no
     * part of the report itself); the player pastes the copied report into its Crash report box. An
     * install failure is titled as one, not as a crash of an older session. */
    private void openIssueForm(String report, String installVerdict) {
        String title;
        if (installVerdict != null) {
            int dot = installVerdict.indexOf(". ");
            title = "[Bug] Android install: " + (dot > 0 ? installVerdict.substring(0, dot) : installVerdict);
        } else {
            String last = readText(lastSessionFile());
            title = last == null ? "[Bug] Android problem report"
                  : CrashReport.abnormal(this, last) ? "[Bug] Android: " + CrashReport.headline(this, last)
                  : "[Bug] Android problem report (" + CrashReport.headline(this, last) + ")";
        }
        if (title.length() > 120) title = title.substring(0, 117) + "...";
        String environment = Build.MANUFACTURER + " " + Build.MODEL + ", Android " + Build.VERSION.RELEASE
                             + " (API " + Build.VERSION.SDK_INT + "), Vulkan " + CrashReport.vulkanShort(this);
        Uri form = Uri.parse(ISSUES + "/new").buildUpon()
                .appendQueryParameter("template", "bug_report.yml")
                .appendQueryParameter("title", title)
                .appendQueryParameter("platform", "Android arm64")
                .appendQueryParameter("version", "OpenSA2 " + versionName())
                .appendQueryParameter("environment", environment)
                .build();
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, form));
            Toast.makeText(this, "Report copied. Paste it into the Crash report box, then fill in the steps.",
                           Toast.LENGTH_LONG).show();
        } catch (android.content.ActivityNotFoundException e) {
            setLog("No browser to open " + ISSUES + ". The report is copied; paste it into a new issue there.\n\n"
                   + report);
        }
    }

    private void launchPlay(Intent play) {
        mPlayLaunched = true;
        startActivity(play);
    }

    /** A warning when the system declares no Vulkan 1.1, which the renderer needs; empty otherwise. */
    private String vulkanNote() {
        return VulkanSupport.setupNote(CrashReport.vulkanVersion(this));
    }

    private String versionName() {
        try {
            String v = getPackageManager().getPackageInfo(getPackageName(), 0).versionName;
            return v == null ? "" : v;
        } catch (Exception e) {
            return "";
        }
    }

    // ------------------------------------------------------------ the layout

    private int dp(float v) {
        return Ui.dp(this, v);
    }

    /** Installed: Play on its own, the settings, then the two ways to install again. Not installed:
     * Choose game ZIP on its own, then the folder check and the settings. */
    private void layoutActions(boolean installed) {
        mActions.removeAllViews();
        if (installed) {
            mZip.setText("Reinstall from ZIP");
            mCheck.setText("Reinstall from folder");
            Ui.style(mPlay, Ui.PRIMARY);
            Ui.style(mZip, Ui.SECONDARY);
            Ui.style(mCheck, Ui.SECONDARY);
            Ui.style(mSettings, Ui.SECONDARY);
            Ui.detach(mPlay);
            Ui.detach(mSettings);
            mActions.addView(mPlay, Ui.full(0));
            mActions.addView(mSettings, Ui.full(dp(8)));
            mActions.addView(Ui.row(this, mZip, mCheck), Ui.full(dp(8)));
        } else {
            mZip.setText("Choose game ZIP");
            mCheck.setText("Check game folder");
            Ui.style(mZip, Ui.PRIMARY);
            Ui.style(mCheck, Ui.SECONDARY);
            Ui.style(mSettings, Ui.QUIET);
            Ui.detach(mZip);
            Ui.detach(mCheck);
            Ui.detach(mSettings);
            mActions.addView(mZip, Ui.full(0));
            mActions.addView(mCheck, Ui.full(dp(8)));
            mActions.addView(mSettings, Ui.full(dp(8)));
        }
        mZip.setEnabled(!mBusy);
        mCheck.setEnabled(!mBusy);
        mPlay.setEnabled(!mBusy);
    }

    /** The card above the buttons for something the player must act on (a crash, a refused install),
     * with the report button turned red while it shows; null hides both. */
    private void banner(String text) {
        mBanner.setText(text == null ? "" : text);
        mBanner.setVisibility(text == null ? View.GONE : View.VISIBLE);
        Ui.style(mGithub, text == null ? Ui.QUIET : Ui.ALERT);
    }

    /** The footer: who makes the app and who has contributed to it (a tap opens GitHub's contributors
     * page), buttons to its GitHub page (source, releases and issues), to check for a newer release (the
     * releases page; the app asks for no internet permission and compares nothing itself), to the
     * game's Steam page, and to tell a friend about it (the releases page, through the system's share
     * sheet), and that it is not SEGA's. */
    private LinearLayout about() {
        LinearLayout f = new LinearLayout(this);
        f.setOrientation(LinearLayout.VERTICAL);
        f.setGravity(Gravity.CENTER_HORIZONTAL);

        View bar = new View(this);
        GradientDrawable accent = new GradientDrawable();
        accent.setCornerRadius(dp(2));
        accent.setColor(Color.rgb(0x2B, 0x6B, 0xE8));
        bar.setBackground(accent);
        f.addView(bar, new LinearLayout.LayoutParams(dp(40), dp(3)));

        TextView who = new TextView(this);
        SpannableStringBuilder b = new SpannableStringBuilder();
        int bright = Color.rgb(0xE8, 0xED, 0xF7);
        appendStyled(b, "OpenSA2", bright, true);
        b.append(" is an open-source project by ");
        appendStyled(b, "kittycache9", bright, true);
        who.setText(b);
        who.setTextColor(Color.rgb(0x8A, 0x97, 0xB2));
        who.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
        who.setGravity(Gravity.CENTER_HORIZONTAL);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
                                                                     ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.topMargin = dp(14);
        f.addView(who, lp);

        int linksGap = dp(12);
        if (CONTRIBUTORS.length > 0) {
            TextView with = new TextView(this);
            SpannableStringBuilder c = new SpannableStringBuilder("With contributions from ");
            StringBuilder names = new StringBuilder();
            for (int i = 0; i < CONTRIBUTORS.length; i++) {
                String join = i == 0 ? "" : i == CONTRIBUTORS.length - 1 ? " and " : ", ";
                c.append(join);
                appendStyled(c, CONTRIBUTORS[i], bright, true);
                names.append(join).append(CONTRIBUTORS[i]);
            }
            with.setText(c);
            with.setTextColor(Color.rgb(0x8A, 0x97, 0xB2));
            with.setTextSize(TypedValue.COMPLEX_UNIT_SP, 12);
            with.setGravity(Gravity.CENTER);
            with.setMinHeight(dp(40));   // a tap target as tall as the pills below; the ripple stays on the text
            with.setMinimumHeight(dp(40));
            with.setContentDescription("With contributions from " + names + ". Opens the contributors page on GitHub.");
            GradientDrawable mask = new GradientDrawable();
            mask.setCornerRadius(dp(6));
            mask.setColor(Color.WHITE);
            with.setBackground(new RippleDrawable(ColorStateList.valueOf(0x40FFFFFF), null,
                                                  new InsetDrawable(mask, 0, dp(10), 0, dp(10))));
            with.setPadding(dp(8), 0, dp(8), 0);
            String contributors = PROJECT + "/graphs/contributors";
            with.setOnClickListener(v -> {
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(contributors)));
                } catch (android.content.ActivityNotFoundException e) {
                    Toast.makeText(this, contributors, Toast.LENGTH_LONG).show();
                }
            });
            lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
            lp.topMargin = -dp(6);   // the 40 dp box centres the text, which sits 6 dp under the maker's line
            f.addView(with, lp);
            linksGap = dp(2);        // and the pills keep 14 dp under the text
        }

        Button github = pill("View on GitHub", v -> openLink(PROJECT));
        Button updates = pill("Check for updates", v -> openLink(PROJECT + "/releases"));
        Button steam = pill("Get the game on Steam", v -> openLink(STEAM));
        Button tell = pill("Share OpenSA2", v -> {
            Intent send = new Intent(Intent.ACTION_SEND);
            send.setType("text/plain");
            send.putExtra(Intent.EXTRA_SUBJECT, "OpenSA2");
            send.putExtra(Intent.EXTRA_TEXT, "OpenSA2 runs Sonic Adventure 2 natively on Android. It's an open-source "
                          + "port, and you use your own copy of the PC version from Steam. Get it here: "
                          + PROJECT + "/releases");
            try {
                startActivity(Intent.createChooser(send, "Share OpenSA2"));
            } catch (android.content.ActivityNotFoundException e) {
                Toast.makeText(this, PROJECT + "/releases", Toast.LENGTH_LONG).show();
            }
        });
        LinearLayout links = new LinearLayout(this);
        links.setOrientation(LinearLayout.HORIZONTAL);
        links.setGravity(Gravity.CENTER_HORIZONTAL);
        LinearLayout.LayoutParams gap = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
                                                                      ViewGroup.LayoutParams.WRAP_CONTENT);
        gap.rightMargin = dp(8);
        links.addView(github, gap);
        links.addView(tell);
        lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.topMargin = linksGap;
        f.addView(links, lp);

        LinearLayout more = new LinearLayout(this);
        more.setOrientation(LinearLayout.HORIZONTAL);
        more.setGravity(Gravity.CENTER_HORIZONTAL);
        LinearLayout.LayoutParams gap2 = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
                                                                       ViewGroup.LayoutParams.WRAP_CONTENT);
        gap2.rightMargin = dp(8);
        more.addView(updates, gap2);
        more.addView(steam);
        lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.topMargin = dp(8);
        f.addView(more, lp);

        TextView legal = new TextView(this);
        legal.setText("Not affiliated with SEGA or Sonic Team.");
        legal.setTextColor(Color.rgb(0x66, 0x72, 0x8D));
        legal.setTextSize(TypedValue.COMPLEX_UNIT_SP, 12);
        legal.setGravity(Gravity.CENTER_HORIZONTAL);
        lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.topMargin = dp(12);
        f.addView(legal, lp);
        return f;
    }

    /** Opens `url` in the browser (or the Steam app for a Steam link); with none installed, shows the address. */
    private void openLink(String url) {
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)));
        } catch (android.content.ActivityNotFoundException e) {
            Toast.makeText(this, url, Toast.LENGTH_LONG).show();
        }
    }

    /** A small rounded, outlined button for the footer's links. */
    private Button pill(String text, View.OnClickListener click) {
        Button b = Ui.button(this, text, click);
        GradientDrawable shape = new GradientDrawable();
        shape.setCornerRadius(dp(20));
        shape.setColor(Color.rgb(0x18, 0x22, 0x3D));
        shape.setStroke(dp(1), Color.rgb(0x3A, 0x48, 0x66));
        b.setBackground(new RippleDrawable(ColorStateList.valueOf(0x40FFFFFF), shape, null));
        b.setTextColor(Color.rgb(0x8C, 0xB6, 0xFF));
        b.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
        b.setTypeface(Typeface.DEFAULT_BOLD);
        b.setMinHeight(dp(40));
        b.setMinimumHeight(dp(40));
        b.setMinWidth(0);
        b.setMinimumWidth(0);
        b.setPadding(dp(18), 0, dp(18), 0);
        return b;
    }

    private static void appendStyled(SpannableStringBuilder b, String text, int color, boolean bold) {
        int start = b.length();
        b.append(text);
        b.setSpan(new android.text.style.ForegroundColorSpan(color), start, b.length(), Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
        if (bold) b.setSpan(new android.text.style.StyleSpan(Typeface.BOLD), start, b.length(), Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
    }

    private void buildUi() {
        int pad = dp(16);
        Ui.Column col = new Ui.Column(this, dp(640));
        col.setPadding(pad, pad, pad, pad);

        TextView title = new TextView(this);
        title.setText("SA2 Recompiled " + versionName());
        title.setTextColor(Color.WHITE);
        title.setTextSize(TypedValue.COMPLEX_UNIT_SP, 24);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        col.addView(title);

        mBanner = new TextView(this);
        mBanner.setTextColor(Color.rgb(0xFF, 0xE4, 0xE4));
        mBanner.setTextSize(TypedValue.COMPLEX_UNIT_SP, 15);
        mBanner.setTextIsSelectable(true);
        GradientDrawable card = new GradientDrawable();
        card.setCornerRadius(dp(12));
        card.setColor(Color.rgb(0x3A, 0x1C, 0x26));
        card.setStroke(dp(1), Color.rgb(0xD9, 0x4F, 0x4F));
        mBanner.setBackground(card);
        mBanner.setPadding(dp(14), dp(12), dp(14), dp(12));
        mBanner.setVisibility(View.GONE);
        col.addView(mBanner, Ui.full(dp(12)));

        mStatus = new TextView(this);
        mStatus.setTextColor(Color.rgb(0xE0, 0xE6, 0xF0));
        mStatus.setTextSize(TypedValue.COMPLEX_UNIT_SP, 15);
        mStatus.setTextIsSelectable(true);
        col.addView(mStatus, Ui.full(dp(12)));

        mPlay = Ui.button(this, "Play", v -> {
            mAutoPlayDone = true;
            mHoldPlay = false;
            verifyThenPlay();
        });
        mSettings = Ui.button(this, "Game settings", v -> openSettings());
        mZip = Ui.button(this, "Choose game ZIP", v -> pickZip());
        mCheck = Ui.button(this, "Check game folder", v -> {
            mAutoPlayDone = true;
            mHoldPlay = false;
            install();
        });
        mActions = new LinearLayout(this);
        mActions.setOrientation(LinearLayout.VERTICAL);
        col.addView(mActions, Ui.full(dp(16)));

        mProgress = new TextView(this);
        mProgress.setTextColor(Color.rgb(0xFF, 0xD8, 0x70));
        mProgress.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
        mProgress.setVisibility(View.GONE);
        col.addView(mProgress, Ui.full(dp(12)));

        TextView caption = new TextView(this);
        caption.setText("Something went wrong?");
        caption.setTextColor(Color.rgb(0x8A, 0x97, 0xB2));
        caption.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13);
        col.addView(caption, Ui.full(dp(24)));

        mGithub = Ui.button(this, "Report on GitHub", v -> report(GITHUB));
        mCopy = Ui.button(this, "Copy report", v -> report(COPY));
        mSummary = Ui.button(this, "Copy summary", v -> report(SUMMARY));
        mShare = Ui.button(this, "Share report", v -> report(SHARE));
        Ui.style(mGithub, Ui.QUIET);
        Ui.style(mCopy, Ui.QUIET);
        Ui.style(mSummary, Ui.QUIET);
        Ui.style(mShare, Ui.QUIET);
        col.addView(mGithub, Ui.full(dp(8)));
        col.addView(Ui.row(this, mCopy, mSummary, mShare), Ui.full(dp(8)));
        col.addView(about(), Ui.full(dp(32)));

        mLog = new TextView(this);
        mLog.setTextColor(Color.rgb(0xA8, 0xB4, 0xC8));
        mLog.setTypeface(Typeface.MONOSPACE);
        mLog.setTextSize(TypedValue.COMPLEX_UNIT_SP, 11);
        mLog.setTextIsSelectable(true);
        col.addView(mLog, Ui.full(dp(16)));

        FrameLayout center = new FrameLayout(this);
        center.addView(col, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                                                         ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.CENTER_HORIZONTAL));
        ScrollView scroll = new ScrollView(this);
        scroll.setBackgroundColor(Ui.BACKGROUND);
        Ui.padForSystemBars(scroll);
        scroll.addView(center, new ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                                                           ViewGroup.LayoutParams.WRAP_CONTENT));
        setContentView(scroll);
        layoutActions(configFile().isFile());
    }
}
