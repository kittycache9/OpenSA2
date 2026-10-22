package com.sa2recomp.app;

import android.content.Context;
import android.net.Uri;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.TreeMap;
import java.util.regex.Pattern;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;
import java.util.zip.ZipOutputStream;

/**
 * The settings screen's Saves card: import a main-game or Chao World save into the write overlay,
 * back up the saves before every import, restore a backup, and export the saves the game sees as a
 * ZIP to a document the person picks (the system's "create document" picker).
 *
 * The game reads its saves from resource/gd_PC/SAVEDATA, the overlay's copy over the game
 * folder's (the runtime's file system, runtime/sa2_files.c), and writes them to the overlay
 * alone. So an import writes the overlay's SAVEDATA and nothing else; the game folder is the
 * user's verified copy and is never written.
 *
 * What is a save. SONIC2B__S01..S99 (main game, 24,576 bytes) and SONIC2B__ALF (Chao World,
 * 65,536 bytes), by name without regard to case, at exactly those sizes. A file of another name
 * or size is refused, not imported: the game reads a save blind, and a wrong file is a corrupt
 * save. The ALF's checksum is not checked here (its table is in the executable, which the app
 * does not read; tools/saves.py checks it on a desktop).
 *
 * What can be imported. One or more save files picked at once, or a ZIP holding them: a Debug
 * Export (DebugExport, sa2recomp --debug-export) with its session/start-saves/ and
 * session/end-saves/ (one of which the caller chooses), a zipped SAVEDATA folder, or any ZIP whose save files have one copy each. An import replaces only
 * the files it brings; the overlay's other saves stay.
 *
 * Backups. Before an import or a restore, and on Back up saves now (backupNow), every save in the
 * overlay's SAVEDATA is copied to <files>/save-backups/<UTC time>/ (an empty folder when there were
 * none, so a restore can put the overlay back to having none); the newest KEEP are kept. A restore
 * makes the overlay's SAVEDATA exactly the backup's.
 */
final class SaveTools {
    static final int KEEP = 10;
    private static final Pattern MAIN = Pattern.compile("(?i)SONIC2B__S\\d\\d");
    private static final Pattern CHAO = Pattern.compile("(?i)SONIC2B__ALF");
    private static final int MAIN_BYTES = 24576, CHAO_BYTES = 65536;
    private static final String SAVEDATA = "resource/gd_PC/SAVEDATA";

    private SaveTools() {}

    /** Why `name` with `bytes` is not a save, or null when it is one. */
    static String notASave(String name, long bytes) {
        if (MAIN.matcher(name).matches()) return bytes == MAIN_BYTES ? null : name + " is " + bytes + " bytes, not " + MAIN_BYTES;
        if (CHAO.matcher(name).matches()) return bytes == CHAO_BYTES ? null : name + " is " + bytes + " bytes, not " + CHAO_BYTES;
        return name + " is not a save's name (SONIC2B__S01..S99 or SONIC2B__ALF)";
    }

    private static boolean isSaveName(String name) {
        return MAIN.matcher(name).matches() || CHAO.matcher(name).matches();
    }

    /** The overlay's SAVEDATA folder (made when absent), or null when the app is not installed yet. */
    static File overlaySaves(Context c) {
        String overlay = configValue(c, "overlay");
        if (overlay == null || overlay.isEmpty()) return null;
        File d = folded(new File(overlay), SAVEDATA, true);
        return d != null && (d.isDirectory() || d.mkdirs()) ? d : null;
    }

    /** The game folder's SAVEDATA, or null. */
    static File installSaves(Context c) {
        String root = configValue(c, "read_root");
        return root == null || root.isEmpty() ? null : folded(new File(root), SAVEDATA, false);
    }

    /** One picked file or ZIP entry: its name and bytes. */
    static final class Found {
        final String name, from;
        final byte[] data;
        Found(String name, String from, byte[] data) { this.name = name; this.from = from; this.data = data; }
    }

    /** What a pick holds: the saves found, and for a Debug Export the two sets it carries. */
    static final class Picked {
        final List<Found> saves = new ArrayList<>();
        final List<Found> start = new ArrayList<>(), end = new ArrayList<>();
        final List<String> refused = new ArrayList<>();
        String error;
        boolean isExport() { return !start.isEmpty() || !end.isEmpty(); }
    }

    /** Reads the picked documents: save files, or one ZIP. */
    static Picked read(Context c, List<Uri> uris) {
        Picked p = new Picked();
        try {
            for (Uri u : uris) {
                String name = displayName(c, u);
                byte[] data = readAll(c.getContentResolver().openInputStream(u));
                if (name.toLowerCase(Locale.US).endsWith(".zip") || looksZip(data)) {
                    if (uris.size() > 1) { p.error = "Pick one ZIP on its own."; return p; }
                    readZip(data, p);
                } else {
                    String why = notASave(name, data.length);
                    if (why != null) p.refused.add(why);
                    else p.saves.add(new Found(name, name, data));
                }
            }
        } catch (Exception e) {
            p.error = "Could not read the pick: " + e.getMessage();
        }
        if (p.error == null && p.saves.isEmpty() && !p.isExport())
            p.error = p.refused.isEmpty() ? "No save files were found." : "No save files were found: " + p.refused.get(0);
        return p;
    }

    private static void readZip(byte[] zip, Picked p) throws Exception {
        Map<String, List<Found>> byName = new TreeMap<>(String.CASE_INSENSITIVE_ORDER);
        try (ZipInputStream z = new ZipInputStream(new java.io.ByteArrayInputStream(zip))) {
            for (ZipEntry e; (e = z.getNextEntry()) != null; ) {
                if (e.isDirectory()) continue;
                String path = e.getName(), name = path.substring(path.lastIndexOf('/') + 1);
                if (!isSaveName(name)) continue;
                byte[] data = readAll(z);
                String why = notASave(name, data.length);
                if (why != null) { p.refused.add(path + ": " + why); continue; }
                Found f = new Found(name, path, data);
                // A Debug Export (session/start-saves/, session/end-saves/): its two sets, chosen between.
                if (path.startsWith("session/start-saves/")) p.start.add(f);
                else if (path.startsWith("session/end-saves/")) p.end.add(f);
                else byName.computeIfAbsent(name, k -> new ArrayList<>()).add(f);
            }
        }
        if (p.isExport()) return;
        for (Map.Entry<String, List<Found>> e : byName.entrySet()) {
            if (e.getValue().size() > 1) {
                p.error = "The ZIP holds " + e.getKey() + " more than once (" + e.getValue().get(0).from + ", "
                        + e.getValue().get(1).from + "): zip one save folder.";
                return;
            }
            p.saves.add(e.getValue().get(0));
        }
    }

    /** Backs up the overlay's saves, then writes `saves` into it. Returns a summary. */
    static String importSaves(Context c, List<Found> saves) throws Exception {
        File dir = overlaySaves(c);
        if (dir == null) throw new Exception("The game is not installed yet: install it first.");
        File backup = backup(backupRoot(c), dir);
        List<String> names = new ArrayList<>();
        for (Found f : saves) {
            File target = existing(dir, f.name);
            if (target == null) target = new File(dir, f.name.toUpperCase(Locale.US));   // the game's own spelling
            writeAtomically(target, f.data);
            names.add(target.getName());
        }
        return "Imported " + String.join(", ", names) + ". The old saves are in backup " + backup.getName() + ".";
    }

    /** Backs up the overlay's saves when asked to (the Saves card's button and the Chao boosts). */
    static String backupNow(Context c) throws Exception {
        File dir = overlaySaves(c);
        if (dir == null) throw new Exception("The game is not installed yet: install it first.");
        return backupNow(dir, backupRoot(c));
    }

    /** Backs up `dir`'s saves into a new folder of `root`, unless the newest backup already holds
     *  exactly them (repeated taps must not push the older backups out of the last KEEP). With no
     *  saves in the overlay the backup is empty, which is still the undo point: restoring it takes the
     *  overlay back to none, so the game reads the game folder's saves again. Synchronized so a double
     *  tap cannot make two backups of the same saves. */
    static synchronized String backupNow(File dir, File root) throws Exception {
        Map<String, byte[]> now = savesIn(dir);
        List<File> all = backups(root);
        boolean same = !all.isEmpty();
        if (same) {
            Map<String, byte[]> last = savesIn(all.get(0));
            same = last.keySet().equals(now.keySet());
            for (Map.Entry<String, byte[]> e : now.entrySet())
                if (same && !Arrays.equals(e.getValue(), last.get(e.getKey()))) same = false;
        }
        String name = same ? all.get(0).getName() : backup(root, dir).getName();
        if (!now.isEmpty())
            return same ? "These saves are already in backup " + name + "."
                        : "Backed up " + now.size() + " saves in backup " + name + ".";
        return "The game's saves are all in the game folder, which the app never changes, so backup " + name
                + (same ? " was already empty" : " is empty") + ". Restoring it returns to those saves.";
    }

    /** The files backup() copies from `dir`, by name. */
    private static Map<String, byte[]> savesIn(File dir) throws Exception {
        Map<String, byte[]> out = new TreeMap<>();
        File[] fs = dir.listFiles(File::isFile);
        if (fs != null) for (File f : fs)
            if (isSaveName(f.getName())) out.put(f.getName(), readAll(new java.io.FileInputStream(f)));
        return out;
    }

    /** The backups in `root` (backupRoot), newest first. */
    static List<File> backups(File root) {
        File[] fs = root.listFiles(File::isDirectory);
        List<File> out = new ArrayList<>(fs == null ? List.of() : Arrays.asList(fs));
        out.sort((a, b) -> b.getName().compareTo(a.getName()));
        return out;
    }

    /** Backs up the overlay's saves, then makes them exactly `from`'s. */
    static String restore(Context c, File from) throws Exception {
        File dir = overlaySaves(c);
        if (dir == null) throw new Exception("The game is not installed yet.");
        // Read first: the backup made next can prune `from` when it is the oldest of KEEP.
        Map<String, byte[]> restored = new TreeMap<>();
        File[] fs = from.listFiles(File::isFile);
        if (fs != null) for (File f : fs) if (notASave(f.getName(), f.length()) == null) restored.put(f.getName(), readAll(new java.io.FileInputStream(f)));
        File backup = backup(backupRoot(c), dir);
        File[] now = dir.listFiles(File::isFile);
        if (now != null) for (File f : now) if (isSaveName(f.getName())) f.delete();
        for (Map.Entry<String, byte[]> e : restored.entrySet()) writeAtomically(new File(dir, e.getKey()), e.getValue());
        int n = restored.size();
        return "Restored " + from.getName() + " (" + n + " saves). The saves before it are in backup " + backup.getName() + ".";
    }

    /** A ZIP of the saves the game sees now (the overlay's over the game folder's), written to `out`
     *  (the document the person picked). Returns the names written. */
    static List<String> exportSaves(Context c, Uri out) throws Exception {
        TreeMap<String, File> seen = new TreeMap<>(String.CASE_INSENSITIVE_ORDER);
        for (File d : new File[] {installSaves(c), overlaySaves(c)}) {
            File[] fs = d == null ? null : d.listFiles(File::isFile);
            if (fs != null) for (File f : fs) if (notASave(f.getName(), f.length()) == null) seen.put(f.getName(), f);
        }
        if (seen.isEmpty()) throw new Exception("There are no saves yet.");
        java.io.OutputStream o = c.getContentResolver().openOutputStream(out, "wt");
        if (o == null) throw new Exception("Could not open the chosen file.");
        try (ZipOutputStream z = new ZipOutputStream(o)) {
            for (File f : seen.values()) {
                z.putNextEntry(new ZipEntry("SAVEDATA/" + f.getName()));
                z.write(readAll(new java.io.FileInputStream(f)));
                z.closeEntry();
            }
        }
        return new ArrayList<>(seen.keySet());
    }

    /** The name the export's document picker suggests. */
    static String exportName() {
        return "sa2-saves-" + stamp() + ".zip";
    }

    // ------------------------------------------------------------ helpers

    static File backupRoot(Context c) { return new File(c.getFilesDir(), "save-backups"); }

    private static File backup(File root, File dir) throws Exception {
        if (!root.isDirectory() && !root.mkdirs()) throw new Exception("Could not make " + root.getName());
        String stamp = stamp();
        File b = new File(root, stamp);
        // Two backups in one millisecond get distinct folders: mkdir fails on an existing one.
        for (int n = 1; !b.mkdir(); n++) {
            if (n > 1000) throw new Exception("Could not make backup " + b.getName());
            b = new File(root, stamp + "-" + n);
        }
        File[] fs = dir.listFiles(File::isFile);
        if (fs != null) for (File f : fs)
            if (isSaveName(f.getName()))
                writeAtomically(new File(b, f.getName()), readAll(new java.io.FileInputStream(f)));
        List<File> all = backups(root);
        for (int i = KEEP; i < all.size(); i++) deleteTree(all.get(i));
        return b;
    }

    private static String stamp() {
        SimpleDateFormat f = new SimpleDateFormat("yyyyMMdd-HHmmss-SSS", Locale.US);
        f.setTimeZone(java.util.TimeZone.getTimeZone("UTC"));
        return f.format(new Date());
    }

    private static File existing(File dir, String name) {
        File[] fs = dir.listFiles();
        if (fs != null) for (File f : fs) if (f.getName().equalsIgnoreCase(name)) return f;
        return null;
    }

    private static void writeAtomically(File target, byte[] data) throws Exception {
        File part = new File(target.getParentFile(), target.getName() + ".part");
        try (FileOutputStream o = new FileOutputStream(part)) {
            o.write(data);
            o.getFD().sync();
        }
        if (!part.renameTo(target)) throw new Exception("Could not write " + target.getName());
    }

    private static void deleteTree(File f) {
        File[] fs = f.listFiles();
        if (fs != null) for (File x : fs) deleteTree(x);
        f.delete();
    }

    private static byte[] readAll(InputStream in) throws Exception {
        if (in == null) throw new Exception("no data");
        try (InputStream i = in) {
            ByteArrayOutputStream o = new ByteArrayOutputStream();
            byte[] b = new byte[65536];
            for (int n; (n = i.read(b)) > 0; ) {
                o.write(b, 0, n);
                if (o.size() > 64 << 20) throw new Exception("larger than 64 MB");
            }
            return o.toByteArray();
        }
    }

    private static boolean looksZip(byte[] d) {
        return d.length >= 4 && d[0] == 'P' && d[1] == 'K' && d[2] == 3 && d[3] == 4;
    }

    private static String displayName(Context c, Uri u) {
        try (android.database.Cursor q = c.getContentResolver().query(u,
                new String[] {android.provider.OpenableColumns.DISPLAY_NAME}, null, null, null)) {
            if (q != null && q.moveToFirst() && !q.isNull(0)) return q.getString(0);
        } catch (Exception ignored) {
        }
        String s = u.getLastPathSegment();
        return s == null ? "" : s.substring(s.lastIndexOf('/') + 1);
    }

    /** A key of the installer's config (<files>/config/sa2recomp/sa2recomp.cfg, key=value lines). */
    private static String configValue(Context c, String key) {
        try {
            File cfg = new File(c.getFilesDir(), "config/sa2recomp/sa2recomp.cfg");
            if (!cfg.isFile()) return null;
            for (String line : new String(readAll(new java.io.FileInputStream(cfg)), StandardCharsets.UTF_8).split("\n"))
                if (line.startsWith(key + "=")) return line.substring(key.length() + 1).trim();
        } catch (Exception ignored) {
        }
        return null;
    }

    /** `rel` under `root`, each component matched without regard to case as the runtime does; with
     *  `make`, components that do not exist are taken as written. */
    private static File folded(File root, String rel, boolean make) {
        File cur = root;
        for (String part : rel.split("/")) {
            File hit = cur.isDirectory() ? existing(cur, part) : null;
            if (hit == null) {
                if (!make) return null;
                hit = new File(cur, part);
            }
            cur = hit;
        }
        return cur;
    }
}
