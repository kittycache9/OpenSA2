package com.sa2recomp.app;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.res.ColorStateList;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Build;
import android.os.Bundle;
import android.util.Log;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.CompoundButton;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.SeekBar;
import android.widget.Switch;
import android.widget.TextView;
import android.widget.Toast;

import java.io.File;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * Game settings, opened from the setup screen. An overview lists the areas, each with its current
 * value: Tweaks (Sa2TweaksActivity), Display, Touch controls, Controller, Sound and Saves, then the
 * start-up switch, Debug and Reset all. Each area opens in place as its own page with a back arrow.
 * One-of-many choices open a dialog, ranges are sliders, and on/off settings are switch rows. Each
 * row says how it shows the settings (mRefresh), so update() is one pass over them. Each change is
 * saved at once (GameSettings) and applies from the next session; every setting starts at today's
 * behaviour, and Reset all puts them back.
 *
 * Saves imports, exports, backs up and restores the game's saves (SaveTools); it changes no setting,
 * and Reset all leaves the saves alone. Debug's Debug Export zips the latest play session, or one
 * picked from the sessions the app keeps, with every log and the start and end saves (DebugExport),
 * and opens the share sheet with it.
 */
public class Sa2SettingsActivity extends Activity {
    private static final int NOTE = Ui.ON_SURFACE_VARIANT;

    private GameSettings mS;
    private int mLong, mShort;
    private boolean mUpdating;
    private final List<Runnable> mRefresh = new ArrayList<>();          // each row's way to show the settings
    private Ui.Pages mPages;                                             // the overview and a page per area
    private View mBackupNow, mExportLatest, mExportPick;
    private TextView mDebugNote;

    private static final String[] ORIENTATIONS = {GameSettings.LANDSCAPE, GameSettings.PORTRAIT, GameSettings.AUTO};

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        mS = GameSettings.load(this);
        int[] screen = GameSettings.screen(this);
        mLong = screen != null ? screen[0] : 1920;
        mShort = screen != null ? screen[1] : 1080;
        buildUi();
        update();
    }

    @Override
    protected void onResume() {
        super.onResume();
        // The layout editor saves its places itself.
        mS = GameSettings.load(this);
        update();
        updateDebug();
    }

    private int dp(float v) {
        return Ui.dp(this, v);
    }

    private void save() {
        mS.save(this);
        update();
    }

    // ------------------------------------------------------------ the screen

    private void section(LinearLayout col, String title) {
        col.addView(Ui.heading(this, title), Ui.full(dp(28)));
    }

    private Ui.Group group(LinearLayout col, boolean icons) {
        Ui.Group g = new Ui.Group(this, icons);
        col.addView(g.view, Ui.full(dp(12)));
        return g;
    }

    private TextView footnote(LinearLayout col, String s) {
        TextView t = Ui.footnote(this, s);
        col.addView(t, Ui.full(dp(10)));
        return t;
    }

    /** Keeps a row's supporting text the settings' current value. */
    private void status(Ui.ListRow r, java.util.function.Supplier<String> text) {
        mRefresh.add(() -> {
            String t = text.get();
            r.supporting.setText(t);
            r.supporting.setVisibility(t.isEmpty() ? View.GONE : View.VISIBLE);
        });
    }

    /** A row: an optional icon, its title, an optional view at the end (a chevron for a row that opens a
     * page, another screen or a picker). */
    private Ui.ListRow row(Ui.Group g, int icon, String title, View end, View.OnClickListener click) {
        Ui.ListRow r = new Ui.ListRow(this, icon < 0 ? null : Ui.badge(this, icon), title, "", end, click);
        g.add(r.view);
        return r;
    }

    /** A row that does something at once (no chevron). */
    private Ui.ListRow action(Ui.Group g, String title, String supporting, View.OnClickListener click) {
        Ui.ListRow r = row(g, -1, title, null, click);
        r.supporting.setText(supporting);
        r.supporting.setVisibility(View.VISIBLE);
        return r;
    }

    /** One of a few choices: the row shows the current one and opens a dialog of all of them. */
    private Ui.ListRow choice(Ui.Group g, String title, String[] labels, java.util.function.IntSupplier current,
                              java.util.function.IntConsumer pick) {
        Ui.ListRow r = row(g, -1, title, null, v -> new AlertDialog.Builder(this)
                .setTitle(title)
                .setSingleChoiceItems(labels, current.getAsInt(), (dlg, which) -> {
                    pick.accept(which);
                    save();
                    dlg.dismiss();
                })
                .setNegativeButton("Cancel", null)
                .show());
        status(r, () -> labels[current.getAsInt()]);
        return r;
    }

    /** An on/off setting: the row and its switch are one control. */
    private Ui.ListRow switchRow(Ui.Group g, int icon, String title, java.util.function.Supplier<String> supporting,
                                 java.util.function.BooleanSupplier get, java.util.function.Consumer<Boolean> set) {
        Switch w = Ui.switchStyle(this, "");
        w.setContentDescription(title);
        w.setOnCheckedChangeListener((b, on) -> {
            if (mUpdating) return;
            set.accept(on);
            save();
        });
        Ui.ListRow r = row(g, icon, title, w, v -> w.toggle());
        mRefresh.add(() -> w.setChecked(get.getAsBoolean()));
        if (supporting != null) status(r, supporting);
        return r;
    }

    /** A range from lo to hi in steps of `step`; `format` names a value (TalkBack reads it too). */
    private Ui.SliderRow slider(Ui.Group g, String title, int lo, int hi, int step, java.util.function.IntSupplier get,
                                java.util.function.IntConsumer set, java.util.function.IntFunction<String> format) {
        Ui.SliderRow r = new Ui.SliderRow(this, title);
        r.bar.setMax((hi - lo) / step);
        r.bar.setOnSeekBarChangeListener(new SeekBar.OnSeekBarChangeListener() {
            @Override
            public void onProgressChanged(SeekBar s, int p, boolean user) {
                if (!user || mUpdating) return;
                set.accept(lo + step * p);
                save();
            }

            @Override
            public void onStartTrackingTouch(SeekBar s) {}

            @Override
            public void onStopTrackingTouch(SeekBar s) {}
        });
        g.add(r.view);
        mRefresh.add(() -> {
            int v = get.getAsInt();
            r.value.setText(format.apply(v));
            r.bar.setProgress((v - lo) / step);
            describe(r.bar, r.title, format.apply(v));
        });
        return r;
    }

    private static String capital(String s) {
        return s.isEmpty() ? s : Character.toUpperCase(s.charAt(0)) + s.substring(1);
    }

    private int tweaksOn() {
        int on = 0;
        for (GameSettings.Tweak t : GameSettings.TWEAKS) if (mS.tweaksOn.contains(t.name)) on++;
        return on;
    }

    private static final String[] ORIENTATION_NAMES = {"Landscape", "Portrait", "Auto-rotate"};

    private void buildUi() {
        Ui.Column o = new Ui.Column(this, dp(640));
        o.setPadding(dp(16), 0, dp(16), dp(40));
        mPages = new Ui.Pages(this, "Game settings", o, null);
        TextView intro = Ui.text(this, "Changes apply the next time the game starts.", Ui.BODY_MEDIUM, NOTE);
        intro.setPadding(dp(16), 0, dp(16), 0);
        o.addView(intro, Ui.full(dp(4)));

        Ui.Group areas = group(o, true);
        Ui.ListRow tweaks = row(areas, Ui.Icon.TUNE, "Tweaks", Ui.chevron(this),
                                v -> startActivity(new Intent(this, Sa2TweaksActivity.class)));
        status(tweaks, () -> tweaksOn() == 0 ? "All " + GameSettings.TWEAKS.length + " off"
                                             : tweaksOn() + " of " + GameSettings.TWEAKS.length + " on");
        Ui.ListRow display = row(areas, Ui.Icon.DISPLAY, "Display", Ui.chevron(this), v -> mPages.open("Display"));
        status(display, () -> {
            int[] pic = mS.picture(mLong, mShort);
            return ORIENTATION_NAMES[indexOf(ORIENTATIONS, mS.orientation)] + " · "
                   + (pic == null ? GameSettings.CONFIG_W + "x" + GameSettings.CONFIG_H : size(pic));
        });
        Ui.ListRow touch = row(areas, Ui.Icon.TOUCH, "Touch controls", Ui.chevron(this), v -> mPages.open("Touch controls"));
        status(touch, () -> "Size " + mS.touchSize + "%, opacity " + mS.touchOpacity + "%");
        Ui.ListRow pad = row(areas, Ui.Icon.GAMEPAD, "Controller", Ui.chevron(this), v -> mPages.open("Controller"));
        status(pad, () -> mS.padMap.isEmpty() ? "Standard buttons" : "Custom buttons");
        Ui.ListRow sound = row(areas, Ui.Icon.SOUND, "Sound", Ui.chevron(this), v -> mPages.open("Sound"));
        status(sound, () -> mS.tweaksOn.contains(GameSettings.VOLUME_LEVELS)
                ? "Music " + mS.musicVolume + "%, voices " + mS.voiceVolume + "%, effects " + mS.sfxVolume + "%"
                : "The game's own levels");
        Ui.ListRow saves = row(areas, Ui.Icon.SAVE, "Saves", Ui.chevron(this), v -> mPages.open("Saves"));
        status(saves, () -> {
            int n = SaveTools.backups(SaveTools.backupRoot(this)).size();
            return n == 0 ? "Import, export and back up" : n + (n == 1 ? " backup" : " backups") + " kept";
        });

        section(o, "Starting");
        Ui.Group start = group(o, true);
        switchRow(start, Ui.Icon.PLAY, "Auto-start the game",
                  () -> mS.autoPlay ? "When the app opens and when an install finishes" : "The app opens to this screen",
                  () -> mS.autoPlay, on -> mS.autoPlay = on);

        section(o, "More");
        Ui.Group more = group(o, true);
        Ui.ListRow debug = row(more, Ui.Icon.CODE, "Debug", Ui.chevron(this), v -> mPages.open("Debug"));
        status(debug, () -> mS.recordSessions ? "Recording sessions" : "Session exports for bug reports");
        Ui.ListRow reset = new Ui.ListRow(this, Ui.badge(this, Ui.Icon.RESET, Ui.ERROR_CONTAINER, Ui.ON_ERROR_CONTAINER),
                "Reset all settings", "", null, v -> new AlertDialog.Builder(this)
                        .setMessage("Put every game setting back to its default? Your saves stay as they are.")
                        .setPositiveButton("Reset", (dlg, w) -> {
                            mS = new GameSettings();
                            save();
                        })
                        .setNegativeButton("Cancel", null)
                        .show());
        more.add(reset.view);
        reset.headline.setTextColor(Ui.ERROR);
        reset.supporting.setText("Your saves stay");
        reset.supporting.setVisibility(View.VISIBLE);

        buildDisplay(mPages.add("Display"));
        buildTouch(mPages.add("Touch controls"));
        buildController(mPages.add("Controller"));
        buildSound(mPages.add("Sound"));
        buildSaves(mPages.add("Saves"));
        buildDebug(mPages.add("Debug"));
        mPages.start();
    }

    private void buildDisplay(Ui.Column d) {
        Ui.Group screen = group(d, false);
        choice(screen, "Orientation", ORIENTATION_NAMES, () -> indexOf(ORIENTATIONS, mS.orientation),
               i -> mS.orientation = ORIENTATIONS[i]);
        Ui.SliderRow place = slider(screen, "Picture position", 0, 50, 5, () -> mS.pictureY, v -> mS.pictureY = v,
                                    v -> capital(placeName(v)));
        mRefresh.add(() -> screen.show(place.view, mS.fixed43()));
        TextView orientation = footnote(d, "");
        mRefresh.add(() -> orientation.setText(GameSettings.PORTRAIT.equals(mS.orientation)
                ? "Hold the phone upright. The game is drawn at 4:3, with the touch controls below it. A higher "
                  + "picture leaves them more room."
                : GameSettings.AUTO.equals(mS.orientation)
                ? "The game turns with the phone when auto-rotate is on, drawn at 4:3 so it fits either way."
                : "Hold the phone sideways."));

        section(d, "Picture");
        Ui.Group pic = group(d, false);
        Ui.ListRow wide = switchRow(pic, -1, "Full-width picture",
                () -> mS.fixed43() ? "Portrait and auto-rotate always draw at 4:3" : "Off keeps the game's own 16:9",
                () -> !mS.fixed43() && mS.widescreen, on -> mS.widescreen = on);
        mRefresh.add(() -> wide.setEnabled(!mS.fixed43()));
        Ui.ListRow aspect = choice(pic, "Aspect ratio", new String[] {"Screen", "21:9", "16:9", "4:3"},
                () -> mS.fixed43() ? 3 : indexOf(GameSettings.ASPECTS, mS.aspect), i -> mS.aspect = GameSettings.ASPECTS[i]);
        mRefresh.add(() -> aspect.setEnabled(!mS.fixed43() && mS.widescreen));
        Ui.ListRow resolution = row(pic, -1, "Resolution", null, v -> pickResolution());
        status(resolution, () -> resolutionName(shownResolution()));
        mRefresh.add(() -> resolution.setEnabled(mS.fixed43() || mS.widescreen));
        switchRow(pic, -1, "HUD at 4:3 positions", () -> "Score, time and rings near the middle",
                  () -> mS.hud43, on -> mS.hud43 = on);
        TextView size = footnote(d, "");
        mRefresh.add(() -> {
            int[] p = mS.picture(mLong, mShort);
            size.setText(p == null ? "The game keeps its own size (" + GameSettings.CONFIG_W + "x" + GameSettings.CONFIG_H
                                     + "). Fewer lines can run faster on a slower phone."
                                   : "The game will draw at " + size(p) + ". Fewer lines can run faster on a slower phone.");
        });

        section(d, "Performance");
        Ui.Group perf = group(d, false);
        choice(perf, "Low-FPS speed fix", new String[] {"Off", "30 FPS", "20 FPS", "Auto"},
               () -> indexOf(GameSettings.FRAMESKIPS, mS.frameskip), i -> mS.frameskip = GameSettings.FRAMESKIPS[i]);
        footnote(d, "Experimental. Keeps stage speed right when the phone can't hold 60 FPS, by running extra game "
                    + "ticks per drawn frame. Menus and cutscenes still slow down.");
    }

    private void buildTouch(Ui.Column t) {
        Ui.Group look = group(t, false);
        slider(look, "Size", 50, 200, 10, () -> mS.touchSize, v -> mS.touchSize = v, v -> v + "%");
        slider(look, "Opacity", 0, 200, 10, () -> mS.touchOpacity, v -> mS.touchOpacity = v, v -> v + "%");
        footnote(t, "At 0% the controls are hidden but still work.");

        section(t, "Layout");
        Ui.Group layout = group(t, false);
        Ui.ListRow land = row(layout, -1, "Landscape layout", Ui.chevron(this), v -> editLayout(GameSettings.LANDSCAPE));
        Ui.ListRow port = row(layout, -1, "Portrait layout", Ui.chevron(this), v -> editLayout(GameSettings.PORTRAIT));
        Ui.ListRow map = row(layout, -1, "Touch buttons", Ui.chevron(this), v -> pickMap(true, -1));
        status(land, () -> "Move and resize the buttons");
        status(port, () -> "Move and resize the buttons");
        status(map, () -> mS.touchMap.isEmpty() ? "Each sends its own button" : "Custom");
        mRefresh.add(() -> {
            layout.show(land.view, !GameSettings.PORTRAIT.equals(mS.orientation));
            layout.show(port.view, !GameSettings.LANDSCAPE.equals(mS.orientation));
        });
    }

    private void buildController(Ui.Column p) {
        Ui.Group g = group(p, false);
        Ui.ListRow map = row(g, -1, "Controller buttons", Ui.chevron(this), v -> pickMap(false, -1));
        status(map, () -> mS.padMap.isEmpty() ? "Standard" : "Custom");
        footnote(p, "Buttons are named by where they are: the bottom face button is A, as on an Xbox controller.");
    }

    private void buildSound(Ui.Column so) {
        Ui.Group g = group(so, false);
        slider(g, "Music", 0, 100, 5, () -> mS.musicVolume, v -> {
            mS.musicVolume = v;
            mS.tweaksOn.add(GameSettings.VOLUME_LEVELS);
        }, v -> v + "%");
        slider(g, "Voices", 0, 100, 5, () -> mS.voiceVolume, v -> {
            mS.voiceVolume = v;
            mS.tweaksOn.add(GameSettings.VOLUME_LEVELS);
        }, v -> v + "%");
        slider(g, "Sound effects", 0, 100, 5, () -> mS.sfxVolume, v -> {
            mS.sfxVolume = v;
            mS.tweaksOn.add(GameSettings.VOLUME_LEVELS);
        }, v -> v + "%");
        footnote(so, "100% is the game's own level; the PC version's music is loud. Moving a slider turns on Volume "
                     + "levels in Tweaks, Audio, and turning that off brings back the game's levels. Short music cues "
                     + "such as the 1-up keep their level.");
    }

    private void buildSaves(Ui.Column sv) {
        Ui.Group g = group(sv, false);
        action(g, "Import saves", "Save files or a ZIP; your current saves are backed up first", v -> pickSaves());
        action(g, "Export saves", "Writes the saves the game sees to a ZIP you choose", v -> pickExport());
        mBackupNow = action(g, "Back up saves now", "The last " + SaveTools.KEEP + " backups are kept", v -> backupNow()).view;
        action(g, "Restore a backup", "Puts your saves back the way a backup has them", v -> pickBackup());
        footnote(sv, "Import takes save files (SONIC2B__S01 and the like for the story, SONIC2B__ALF for Chao World), "
                     + "a ZIP of them, or a Debug Export's saves, and replaces only the saves it brings. Changes "
                     + "apply from the next session.");
    }

    private void buildDebug(Ui.Column g0) {
        Ui.Group g = group(g0, false);
        switchRow(g, -1, "Record sessions", () -> "Keeps what a replay needs. From the next session.",
                  () -> mS.recordSessions, on -> mS.recordSessions = on);
        Ui.ListRow latest = action(g, "Export the latest session", "", v -> export(null));
        mDebugNote = latest.supporting;
        mExportLatest = latest.view;
        mExportPick = action(g, "Export another session", "Pick one of the sessions the app keeps", v -> pickSession()).view;
        footnote(g0, "An export is one ZIP of a session's logs and saves, with what a replay needs when the session "
                     + "was recorded. It holds no pictures of the game. The share sheet opens with it.");
    }

    private static int indexOf(String[] a, String v) {
        for (int i = 0; i < a.length; i++) if (a[i].equals(v)) return i;
        return 0;
    }

    private String size(int[] p) {
        return p[0] + "x" + p[1];
    }

    private String resolutionName(int lines) {
        int[] p = mS.picture(mLong, mShort, lines);
        if (p == null) return "The game's own (" + GameSettings.CONFIG_W + "x" + GameSettings.CONFIG_H + ")";
        if (lines == 0) return "Screen (" + size(p) + ")";
        int[] full = mS.fullPicture(mLong, mShort);
        if (full != null && lines >= full[1]) return "Full screen (" + size(p) + ")";
        return lines + " lines (" + size(p) + ")";
    }

    /** The choice the stored resolution draws as here: as many lines as the largest picture's, or
     * more (picked in another orientation), is the largest. The stored value is kept. */
    private int shownResolution() {
        int[] full = mS.fullPicture(mLong, mShort);
        if (full == null || mS.resolution < full[1]) return mS.resolution;
        return mS.picture(mLong, mShort, 0) != null ? 0 : full[1];
    }

    /** A slider's value as TalkBack reads it (`value`: the percentage or the place), not the bar's
     * fraction. */
    private static void describe(SeekBar b, TextView label, String value) {
        if (Build.VERSION.SDK_INT >= 30) b.setStateDescription(value);
        else b.setContentDescription(label.getText());
    }

    /** The picture position's name: its centre from the top (0) to the middle of the screen (50%). */
    private static String placeName(int v) {
        return v <= 0 ? "top" : v >= 50 ? "centre" : v == 25 ? "upper half (default)" : v + "% down";
    }

    private void update() {
        mUpdating = true;
        for (Runnable r : mRefresh) r.run();
        mUpdating = false;
    }

    private void pickResolution() {
        List<Integer> lines = new ArrayList<>();
        int[] full = mS.fullPicture(mLong, mShort);
        lines.add(0);
        if (full != null && mS.picture(mLong, mShort, 0) == null) lines.add(full[1]);
        for (int r : GameSettings.RESOLUTIONS)
            if (r != 0 && (full == null || r < full[1])) lines.add(r);
        List<String> names = new ArrayList<>();
        for (int r : lines) names.add(resolutionName(r));
        int chosen = Math.max(0, lines.indexOf(shownResolution()));
        new AlertDialog.Builder(this)
                .setTitle("Resolution")
                .setSingleChoiceItems(names.toArray(new String[0]), chosen, (dlg, which) -> {
                    mS.resolution = lines.get(which);
                    save();
                    dlg.dismiss();
                })
                .setNegativeButton("Cancel", null)
                .show();
    }

    private void editLayout(String orientation) {
        startActivity(new Intent(this, Sa2LayoutActivity.class).putExtra(Sa2LayoutActivity.EXTRA_ORIENTATION, orientation));
    }

    private static final String[] TOUCH_NAMES = {"A button", "B button", "X button", "Y button", "START button",
                                                 "LT button", "RT button"};
    private static final String[] PAD_NAMES = {"Bottom face button (A)", "Right face button (B)",
                                               "Left face button (X)", "Top face button (Y)", "Left bumper (LB)",
                                               "Right bumper (RB)", "Back / Select", "Start / Menu",
                                               "Left stick press", "Right stick press", "D-pad up", "D-pad down",
                                               "D-pad left", "D-pad right"};

    private static String outputName(String out) {
        return "NONE".equals(out) ? "nothing" : out;
    }

    /** The touch (or controller) buttons and what each sends; picking one asks what it should send,
     * then shows the list again. */
    private void pickMap(boolean touch, int button) {
        String[] keys = touch ? GameSettings.TOUCH_BUTTONS : GameSettings.PAD_BUTTONS;
        String[] owns = touch ? GameSettings.TOUCH_BUTTONS : GameSettings.PAD_DEFAULTS;
        String[] names = touch ? TOUCH_NAMES : PAD_NAMES;
        String map = touch ? mS.touchMap : mS.padMap;
        if (button < 0) {
            String[] rows = new String[keys.length];
            for (int i = 0; i < keys.length; i++)
                rows[i] = names[i] + ": " + outputName(GameSettings.sends(map, keys[i], owns[i]));
            new AlertDialog.Builder(this)
                    .setTitle(touch ? "Touch buttons" : "Controller buttons")
                    .setItems(rows, (dlg, which) -> pickMap(touch, which))
                    .setNeutralButton("Reset", (dlg, w) -> {
                        if (touch) mS.touchMap = "";
                        else mS.padMap = "";
                        save();
                    })
                    .setNegativeButton("Close", null)
                    .show();
            return;
        }
        String[] outs = new String[GameSettings.OUTPUTS.length];
        for (int i = 0; i < outs.length; i++) outs[i] = outputName(GameSettings.OUTPUTS[i]);
        int chosen = indexOf(GameSettings.OUTPUTS, GameSettings.sends(map, keys[button], owns[button]));
        new AlertDialog.Builder(this)
                .setTitle(names[button] + " sends")
                .setSingleChoiceItems(outs, chosen, (dlg, which) -> {
                    String m = GameSettings.withSend(map, keys, owns, keys[button], GameSettings.OUTPUTS[which]);
                    if (touch) mS.touchMap = m;
                    else mS.padMap = m;
                    save();
                    dlg.dismiss();
                    pickMap(touch, -1);
                })
                .setNegativeButton("Back", (dlg, w) -> pickMap(touch, -1))
                .show();
    }

    // ------------------------------------------------------------ saves

    private static final int PICK_SAVES = 41, PICK_EXPORT = 42;

    /** The system's document picker: one or more save files, or one ZIP. */
    private void pickSaves() {
        Intent i = new Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("*/*")
                .putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
        try {
            startActivityForResult(i, PICK_SAVES);
        } catch (android.content.ActivityNotFoundException e) {
            Toast.makeText(this, "No app can pick files.", Toast.LENGTH_LONG).show();
        }
    }

    /** The system's "create document" picker: where the saves' ZIP goes. */
    private void pickExport() {
        Intent i = new Intent(Intent.ACTION_CREATE_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE)
                .setType("application/zip").putExtra(Intent.EXTRA_TITLE, SaveTools.exportName());
        try {
            startActivityForResult(i, PICK_EXPORT);
        } catch (android.content.ActivityNotFoundException e) {
            Toast.makeText(this, "No app can save files.", Toast.LENGTH_LONG).show();
        }
    }

    @Override
    protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        if (result != RESULT_OK || data == null) return;
        if (request == PICK_EXPORT && data.getData() != null) {
            exportSaves(data.getData());
            return;
        }
        if (request != PICK_SAVES) return;
        List<android.net.Uri> uris = new ArrayList<>();
        if (data.getClipData() != null)
            for (int i = 0; i < data.getClipData().getItemCount(); i++) uris.add(data.getClipData().getItemAt(i).getUri());
        else if (data.getData() != null) uris.add(data.getData());
        if (uris.isEmpty()) return;
        new Thread(() -> {
            SaveTools.Picked p = SaveTools.read(this, uris);
            runOnUiThread(() -> confirmImport(p));
        }, "save-import-read").start();
    }

    /** Shows what the pick holds (a Debug Export asks which of its two sets) and imports on OK. */
    private void confirmImport(SaveTools.Picked p) {
        if (isFinishing() || isDestroyed()) return;
        if (p.error != null) {
            new AlertDialog.Builder(this).setTitle("Nothing imported").setMessage(p.error)
                    .setPositiveButton("OK", null).show();
            return;
        }
        if (p.isExport()) {
            List<String> rows = new ArrayList<>();
            List<List<SaveTools.Found>> sets = new ArrayList<>();
            if (!p.end.isEmpty()) { rows.add("The saves at the session's end (" + names(p.end) + ")"); sets.add(p.end); }
            if (!p.start.isEmpty()) { rows.add("The saves at the session's start (" + names(p.start) + ")"); sets.add(p.start); }
            new AlertDialog.Builder(this).setTitle("Import from this Debug Export")
                    .setItems(rows.toArray(new String[0]), (dlg, which) -> doImport(sets.get(which)))
                    .setNegativeButton("Cancel", null).show();
            return;
        }
        String msg = "Import " + names(p.saves) + "?";
        if (!p.refused.isEmpty()) msg += "\n\nSkipped:\n" + String.join("\n", p.refused);
        new AlertDialog.Builder(this).setTitle("Import saves").setMessage(msg)
                .setPositiveButton("Import", (dlg, w) -> doImport(p.saves))
                .setNegativeButton("Cancel", null).show();
    }

    private static String names(List<SaveTools.Found> fs) {
        List<String> n = new ArrayList<>();
        for (SaveTools.Found f : fs) n.add(f.name);
        return String.join(", ", n);
    }

    private void backupNow() {
        mBackupNow.setEnabled(false);
        new Thread(() -> {
            String msg;
            try {
                msg = SaveTools.backupNow(this);
            } catch (Exception e) {
                msg = "Backup failed: " + e.getMessage();
            }
            final String m = msg;
            runOnUiThread(() -> {
                mBackupNow.setEnabled(true);
                Toast.makeText(this, m, Toast.LENGTH_LONG).show();
            });
        }, "save-backup").start();
    }

    private void doImport(List<SaveTools.Found> saves) {
        new Thread(() -> {
            String msg;
            try {
                msg = SaveTools.importSaves(this, saves);
            } catch (Exception e) {
                msg = "Import failed: " + e.getMessage();
            }
            final String m = msg;
            runOnUiThread(() -> Toast.makeText(this, m, Toast.LENGTH_LONG).show());
        }, "save-import").start();
    }

    private void exportSaves(android.net.Uri out) {
        new Thread(() -> {
            String msg;
            try {
                msg = "Exported " + String.join(", ", SaveTools.exportSaves(this, out)) + ".";
            } catch (Exception e) {
                msg = "Export failed: " + e.getMessage();
            }
            final String m = msg;
            runOnUiThread(() -> Toast.makeText(this, m, Toast.LENGTH_LONG).show());
        }, "save-export").start();
    }

    private void pickBackup() {
        List<File> all = SaveTools.backups(SaveTools.backupRoot(this));
        if (all.isEmpty()) { Toast.makeText(this, "No backups yet: one is made at every import.", Toast.LENGTH_LONG).show(); return; }
        String[] rows = new String[all.size()];
        for (int i = 0; i < rows.length; i++) {
            File[] fs = all.get(i).listFiles();
            rows[i] = all.get(i).getName() + " (" + (fs == null ? 0 : fs.length) + " saves)";
        }
        new AlertDialog.Builder(this).setTitle("Restore which backup?")
                .setItems(rows, (dlg, which) -> new AlertDialog.Builder(this).setTitle("Restore " + all.get(which).getName())
                        .setMessage("The saves will become exactly this backup's. The current saves are backed up first.")
                        .setPositiveButton("Restore", (d2, w2) -> new Thread(() -> {
                            String msg;
                            try {
                                msg = SaveTools.restore(this, all.get(which));
                            } catch (Exception e) {
                                msg = "Restore failed: " + e.getMessage();
                            }
                            final String m = msg;
                            runOnUiThread(() -> Toast.makeText(this, m, Toast.LENGTH_LONG).show());
                        }, "save-restore").start())
                        .setNegativeButton("Cancel", null).show())
                .setNegativeButton("Cancel", null).show();
    }

    // ------------------------------------------------------------ debug

    private void updateDebug() {
        if (mDebugNote == null) return;
        List<File> all = DebugExport.sessions(this);
        mDebugNote.setText(all.isEmpty() ? "No session yet: play once first."
                : "Latest session: " + all.get(0).getName() + " (" + all.size() + " kept)");
        enable(mExportLatest, !all.isEmpty());
        enable(mExportPick, all.size() > 1);
    }

    private static void enable(View row, boolean on) {
        row.setEnabled(on);
        row.setAlpha(on ? 1f : 0.38f);
    }

    private void pickSession() {
        List<File> all = DebugExport.sessions(this);
        String[] names = new String[all.size()];
        for (int i = 0; i < names.length; i++) names[i] = all.get(i).getName() + (i == 0 ? " (latest)" : "");
        new AlertDialog.Builder(this)
                .setTitle("Export which session?")
                .setItems(names, (dlg, which) -> export(all.get(which)))
                .setNegativeButton("Cancel", null)
                .show();
    }

    /** The ZIP on a worker thread (it reads every log and logcat), then the share sheet. */
    private void export(File session) {
        enable(mExportLatest, false);
        enable(mExportPick, false);
        mDebugNote.setText("Exporting...");
        new Thread(() -> {
            DebugExport.Result r = DebugExport.export(this, session);
            runOnUiThread(() -> {
                updateDebug();
                if (isFinishing() || isDestroyed()) return;
                if (r.error != null) {
                    Toast.makeText(this, r.error, Toast.LENGTH_LONG).show();
                    return;
                }
                mDebugNote.setText("Exported " + r.session + ": " + r.entries + " files");
                try {
                    startActivity(DebugExport.shareIntent(this, r.zip));
                } catch (android.content.ActivityNotFoundException e) {
                    Toast.makeText(this, "No app can take the export.", Toast.LENGTH_LONG).show();
                }
            });
        }, "debug-export").start();
    }
}
