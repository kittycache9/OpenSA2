package com.sa2recomp.app;

import android.content.ClipData;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.util.Log;

import java.io.BufferedReader;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.TimeZone;
import java.util.concurrent.TimeUnit;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;

/**
 * Debug Export: one ZIP of a play session, shared through Android's share
 * sheet, holding every log the app can produce and what a replay needs (tools/s7/replay_export.py):
 * the session folder the native session wrote (host/android/native/sa2_android_session.cpp; its
 * recorder's files are described in host/launch/sa2_session_files.h: start and end saves and
 * config, game-env.txt, session-info.json, input.log and pad.log), the app's own report and logcat,
 * the device and the installer's config. Never the game's stream: the session's stream goes
 * through a FIFO and is never on disk, and the FIFOs are not regular files.
 *
 * The layout is the desktop launcher's (host/launch/sa2_debug_export.h, `sa2recomp
 * --debug-export`), with app/ in place of build/:
 *
 *   MANIFEST.txt          every entry, its size, sha256 and what it is
 *   session/...           the session folder's regular files, text redacted
 *   session/end-saves/... the overlay's saves at export time when the session kept none
 *   app/device.txt        the app's version, the device, Android, the SoC, Vulkan, memory
 *   app/last-session.txt  the native session's report of the latest session
 *   app/logcat.txt        this app's logcat (both processes, every level) at export time
 *   install/sa2recomp.cfg the installer's config
 *
 * Text is redacted as the crash report is (CrashReport.Redactor: the app's folders, shared storage
 * and the game folder's path become fixed names). The ZIP goes to the cache folder's exports/
 * (the newest kKeep are kept), which ExportProvider serves to the app the person picks, read-only,
 * for that share alone.
 */
final class DebugExport {
    private static final String TAG = "sa2recomp";
    static final String AUTHORITY = "com.sa2recomp.app.exports";
    private static final long MAX_ENTRY = 256L << 20;
    private static final int LOGCAT_LINES = 20000;
    private static final int KEEP = 3;

    /** What MANIFEST.txt says each session entry is: the launcher's table (sa2_debug_export.cpp
     * kKnown), the same names. A name ending in '/' is a folder. */
    private static final String[][] KNOWN = {
        {"session-info.json", "build identity (game and libmain sha256), device, settings"},
        {"game-env.txt", "every SA2_ setting the game ran with, paths redacted"},
        {"start-saves/", "the saves the game saw at its start (resource/gd_PC/SAVEDATA); index.tsv says from where"},
        {"start-config/", "the Config/ files the game saw at its start"},
        {"end-saves/", "the saves after the game ended"},
        {"end-config/", "the Config/ files after the game ended"},
        {"input.log", "keyboard polls (SA2_INPUT_DI_LOG): replays as SA2_INPUT_DI_SCRIPT"},
        {"pad.log", "gamepad and touch-overlay pad changes by XInput call (SA2_INPUT_PAD_LOG): replays as "
                    + "SA2_INPUT_PAD_SCRIPT"},
        {"ticks.tsv", "the launch observer: tick, presents, game mode, menu at every game loop (SA2_LAUNCH_TICKS)"},
        {"game.runrec", "the game's run record: runtime counters, file log, the trap if any"},
        {"game.out", "the game's standard output (normally its run record again)"},
        {"game.log", "the game's standard error: runtime notes and traps"},
        {"counts.runrec", "the recorder's call counts"},
        {"game.fifo.status", "the stream's closing line: records, bytes, frames"},
        {"player.log", "the player's output: renderer, pads, picture, SDL"},
        {"player-report.runrec", "the player's report: frames, pace, audio, pads"},
        {"d3d9gpu.runrec", "the SDL_gpu renderer's report"},
        {"d3d9gpu.runrec.keys.json", "the renderer report's pipeline and texture keys"},
        {"play/", "the player's output folder (SA2_PLAY_OUT)"},
    };

    static final class Result {
        File zip;
        String session;
        int entries, leftOut;
        long bytes;
        String error;
    }

    private DebugExport() {}

    static File sessionsDir(Context c) {
        return new File(c.getFilesDir(), "sessions");
    }

    /** The session folders, newest first (their names start with the local time, yyyyMMdd-HHmmss). */
    static List<File> sessions(Context c) {
        File[] dirs = sessionsDir(c).listFiles(File::isDirectory);
        List<File> out = new ArrayList<>();
        if (dirs == null) return out;
        Arrays.sort(dirs, (a, b) -> b.getName().compareTo(a.getName()));
        out.addAll(Arrays.asList(dirs));
        return out;
    }

    private static String describe(String rel) {
        for (String[] k : KNOWN)
            if (k[0].endsWith("/") ? rel.startsWith(k[0]) : rel.equals(k[0])) return k[1];
        return "other";
    }

    private static boolean isText(String rel) {
        if (rel.contains("saves/") && !rel.endsWith("index.tsv")) return false;
        for (String e : new String[] {".log", ".txt", ".tsv", ".json", ".runrec", ".env", ".cfg", ".ini", ".err",
                                      ".out", ".status", ".xml"})
            if (rel.endsWith(e)) return true;
        return false;
    }

    /** The launcher's rule (sa2_debug_export.cpp left_out_by_name). */
    private static String leftOutByName(String rel) {
        if (rel.startsWith("overlay/")) return "a write root of its own";
        if (rel.startsWith("dxvk-cache/")) return "DXVK's shader cache";
        if (rel.contains("stream.bin") || rel.endsWith(".sa2")) return "a D3D stream";
        return null;
    }

    private static void walk(File dir, String rel, List<String> out) {
        File[] fs = dir.listFiles();
        if (fs == null) return;
        Arrays.sort(fs, (a, b) -> a.getName().compareTo(b.getName()));
        for (File f : fs) {
            String r = rel.isEmpty() ? f.getName() : rel + "/" + f.getName();
            if (f.isDirectory()) walk(f, r, out);
            else if (f.isFile()) out.add(r);          // a FIFO is not a file to isFile
        }
    }

    private static byte[] read(File f) throws Exception {
        try (InputStream in = new FileInputStream(f)) {
            ByteArrayOutputStream b = new ByteArrayOutputStream((int) Math.min(f.length(), Integer.MAX_VALUE - 8));
            byte[] buf = new byte[1 << 16];
            int n;
            while ((n = in.read(buf)) > 0) b.write(buf, 0, n);
            return b.toByteArray();
        }
    }

    private static String sha256(byte[] d) {
        try {
            MessageDigest m = MessageDigest.getInstance("SHA-256");
            StringBuilder s = new StringBuilder();
            for (byte b : m.digest(d)) s.append(String.format(Locale.US, "%02x", b));
            return s.toString();
        } catch (Exception e) {
            return "";
        }
    }

    private static String human(long b) {
        return b >= (1L << 20) ? String.format(Locale.US, "%.1f MB", b / 1048576.0)
                               : String.format(Locale.US, "%.1f KB", b / 1024.0);
    }

    /** A key of the installer's config (host/install/sa2_config.h: key=value lines). */
    private static String configValue(String cfg, String key) {
        if (cfg == null) return null;
        for (String l : cfg.split("\n"))
            if (l.startsWith(key + "=")) return l.substring(key.length() + 1).trim();
        return null;
    }

    /** `root`/`rel`, each component matched case-blind, as the runtime resolves a guest path. */
    private static File findFolded(String root, String rel) {
        if (root == null) return null;
        File at = new File(root);
        for (String part : rel.split("/")) {
            File[] fs = at.listFiles();
            if (fs == null) return null;
            File hit = null;
            for (File f : fs) if (f.getName().equalsIgnoreCase(part)) hit = f;
            if (hit == null) return null;
            at = hit;
        }
        return at;
    }

    private static String logcat() {
        Process p = null;
        try {
            p = new ProcessBuilder("logcat", "-d", "-b", "main,system,crash", "-v", "threadtime", "-t",
                                   String.valueOf(LOGCAT_LINES)).redirectErrorStream(true).start();
            StringBuilder b = new StringBuilder();
            try (BufferedReader in = new BufferedReader(new InputStreamReader(p.getInputStream(), StandardCharsets.UTF_8))) {
                String l;
                while ((l = in.readLine()) != null) b.append(l).append('\n');
            }
            p.waitFor(5, TimeUnit.SECONDS);
            return b.toString();
        } catch (Exception e) {
            return "logcat could not be read: " + e + "\n";
        } finally {
            if (p != null) p.destroy();
        }
    }

    private static String device(Context c) {
        StringBuilder b = new StringBuilder();
        b.append("app: ").append(CrashReport.appVersion(c)).append('\n');
        b.append("device: ").append(Build.MANUFACTURER).append(' ').append(Build.MODEL).append(" (")
         .append(Build.DEVICE).append(")\n");
        b.append("android: ").append(Build.VERSION.RELEASE).append(" (API ").append(Build.VERSION.SDK_INT).append(")\n");
        if (Build.VERSION.SDK_INT >= 31) b.append("soc: ").append(Build.SOC_MANUFACTURER).append(' ')
                                          .append(Build.SOC_MODEL).append('\n');
        b.append("abis: ").append(String.join(", ", Build.SUPPORTED_ABIS)).append('\n');
        b.append("vulkan: ").append(CrashReport.vulkanShort(c)).append('\n');
        b.append("build: ").append(Build.FINGERPRINT).append('\n');
        return b.toString();
    }

    /** Writes the ZIP of `session` (the newest when null). Runs on a worker thread: it reads every
     * log and logcat. */
    static Result export(Context c, File session) {
        Result r = new Result();
        if (session == null) {
            List<File> all = sessions(c);
            if (all.isEmpty()) {
                r.error = "There is no session to export yet: play once first.";
                return r;
            }
            session = all.get(0);
        }
        r.session = session.getName();
        File dir = new File(c.getCacheDir(), "exports");
        dir.mkdirs();
        File[] old = dir.listFiles((d, n) -> n.endsWith(".zip"));
        if (old != null && old.length >= KEEP) {
            Arrays.sort(old, (a, b) -> Long.compare(a.lastModified(), b.lastModified()));
            for (int i = 0; i + KEEP - 1 < old.length; i++) old[i].delete();
        }
        r.zip = new File(dir, "sa2-debug-" + session.getName() + ".zip");
        File part = new File(dir, r.zip.getName() + ".part");
        CrashReport.Redactor red = new CrashReport.Redactor(c);
        String cfgText = null;
        File cfg = new File(c.getFilesDir(), "config/sa2recomp/sa2recomp.cfg");
        try {
            if (cfg.isFile()) cfgText = new String(read(cfg), StandardCharsets.UTF_8);
        } catch (Exception ignored) {
        }
        String readRoot = configValue(cfgText, "read_root"), overlay = configValue(cfgText, "overlay");
        String sessionPath = session.getAbsolutePath();
        StringBuilder rows = new StringBuilder(), notes = new StringBuilder();
        try (ZipOutputStream z = new ZipOutputStream(new FileOutputStream(part))) {
            // One entry: text redacted (the session, the game folder and the overlay first, as the
            // native recorder names them, then the crash report's rules), stored or deflated.
            class Adder {
                void add(String name, byte[] data, String what) throws Exception {
                    if (isText(name)) {
                        String t = new String(data, StandardCharsets.UTF_8).replace(sessionPath, "<session>");
                        if (readRoot != null && !readRoot.isEmpty()) t = t.replace(readRoot, "<game>");
                        if (overlay != null && !overlay.isEmpty()) t = t.replace(overlay, "<overlay>");
                        data = red.apply(t).getBytes(StandardCharsets.UTF_8);
                    }
                    z.putNextEntry(new ZipEntry(name));
                    z.write(data);
                    z.closeEntry();
                    rows.append(name).append('\t').append(data.length).append('\t').append(sha256(data))
                        .append('\t').append(what).append('\n');
                    r.entries++;
                    r.bytes += data.length;
                }
            }
            Adder a = new Adder();
            List<String> files = new ArrayList<>();
            walk(session, "", files);
            boolean endSaves = false;
            boolean startSaves = false;
            for (String rel : files) {
                File f = new File(session, rel);
                String why = leftOutByName(rel);
                if (why == null && f.length() > MAX_ENTRY) why = "larger than " + human(MAX_ENTRY);
                byte[] data = null;
                if (why == null) {
                    try {
                        data = read(f);
                    } catch (Exception e) {
                        why = "could not be read: " + e.getMessage();
                    }
                }
                if (why != null) {
                    notes.append("# left out: session/").append(rel).append(" (").append(human(f.length()))
                         .append("): ").append(why).append('\n');
                    r.leftOut++;
                    continue;
                }
                endSaves |= rel.startsWith("end-saves/");
                startSaves |= rel.startsWith("start-saves/");
                a.add("session/" + rel, data, describe(rel));
            }
            // A session the recorder did not record (it is off unless asked for) keeps no start state.
            if (!startSaves)
                notes.append("# not replayable: the session recorder was off (Game settings > Debug > Record "
                             + "sessions), so there is no session/start-saves/ or pad.log\n");
            if (!endSaves) {
                // The saves the game would see now: the overlay's over the game folder's.
                java.util.TreeMap<String, File> seen = new java.util.TreeMap<>(String.CASE_INSENSITIVE_ORDER);
                for (String root : new String[] {readRoot, overlay}) {
                    File d = findFolded(root, "resource/gd_PC/SAVEDATA");
                    File[] fs = d == null ? null : d.listFiles(File::isFile);
                    if (fs != null) for (File f : fs) seen.put(f.getName(), f);
                }
                for (File f : seen.values())
                    a.add("session/end-saves/" + f.getName(), read(f), "a save at export time (the session kept none at its end)");
                notes.append("# session/end-saves/ was taken at export time: the session kept none at its end\n");
            }
            a.add("app/device.txt", device(c).getBytes(StandardCharsets.UTF_8), "the app, the device, Android, Vulkan");
            File last = new File(c.getFilesDir(), "last-session.txt");
            if (last.isFile()) a.add("app/last-session.txt", read(last), "the native session's report of the latest session");
            a.add("app/logcat.txt", logcat().getBytes(StandardCharsets.UTF_8),
                  "this app's logcat (both processes, every level) at export time");
            if (cfgText != null) a.add("install/sa2recomp.cfg", cfgText.getBytes(StandardCharsets.UTF_8),
                                       "the installer's config");
            SimpleDateFormat iso = new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ssXXX", Locale.US);
            iso.setTimeZone(TimeZone.getTimeZone("UTC"));
            String manifest = "# sa2recomp Debug Export, format 1 (host/launch/sa2_debug_export.h; "
                    + "host/android/java DebugExport)\n# session: " + session.getName() + "\n# exported: "
                    + iso.format(new Date()) + "\n# paths in text entries: <session> the session folder, <game> the "
                    + "game folder, <overlay> the write root, <app> and <ext> the app's folders, <storage> shared "
                    + "storage\n" + notes + "# path\tbytes\tsha256\twhat it is\n" + rows;
            z.putNextEntry(new ZipEntry("MANIFEST.txt"));
            z.write(manifest.getBytes(StandardCharsets.UTF_8));
            z.closeEntry();
            r.entries++;
        } catch (Exception e) {
            part.delete();
            r.error = "The export could not be written: " + e.getMessage();
            Log.w(TAG, "debug export: " + e);
            return r;
        }
        if (!part.renameTo(r.zip)) {
            part.delete();
            r.error = "The export could not be written.";
            return r;
        }
        Log.i(TAG, "debug export: " + r.zip.getName() + ": " + r.entries + " entries, " + r.leftOut + " left out, "
                   + human(r.zip.length()));
        return r;
    }

    /** Android's share sheet for the ZIP, through ExportProvider, readable by the app picked alone. */
    static Intent shareIntent(Context c, File zip) {
        Uri uri = new Uri.Builder().scheme("content").authority(AUTHORITY).appendPath(zip.getName()).build();
        Intent send = new Intent(Intent.ACTION_SEND);
        send.setType("application/zip");
        send.putExtra(Intent.EXTRA_STREAM, uri);
        send.putExtra(Intent.EXTRA_SUBJECT, "OpenSA2 debug export " + zip.getName());
        send.setClipData(ClipData.newRawUri(zip.getName(), uri));
        send.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        Intent chooser = Intent.createChooser(send, "Share the debug export");
        chooser.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        return chooser;
    }
}
