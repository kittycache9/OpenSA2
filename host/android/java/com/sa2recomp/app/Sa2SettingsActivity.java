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
 * The game settings screen, opened from the setup screen: the orientation (landscape, portrait or
 * auto-rotate), the full-width picture, the HUD at 4:3, the picture's aspect and resolution, the
 * touch controls' size, opacity and places (Sa2LayoutActivity), what each touch or controller
 * button sends, and the tweaks (changes to the game's own behaviour, none made by default: a
 * switch for each line of GameSettings.TWEAKS, under GameSettings.TWEAK_GROUPS's headings). Each
 * change is saved at once (GameSettings) and applies from the next session.
 * Every setting starts at today's behaviour, and "Reset all" puts them back.
 *
 * The Saves card imports, exports, backs up and restores the game's saves (SaveTools). It changes
 * no setting, and "Reset all" leaves the saves alone.
 *
 * The Debug section is last: Debug Export zips the latest play session, or one picked from the
 * sessions the app keeps, with every log and the start and end saves (DebugExport), and opens the
 * share sheet with it. Later debug rows go in debugRows.
 */
public class Sa2SettingsActivity extends Activity {
    private static final int TEXT = Ui.ON_SURFACE, NOTE = Ui.ON_SURFACE_VARIANT;

    private GameSettings mS;
    private int mLong, mShort;
    private Button[] mOrientation, mAspect;
    private Switch mWidescreen, mHud, mRecord, mAutoPlay;
    private Button mTweaksEntry, mBackupNow;   // the row that opens the Tweaks screen; Back up saves now
    private Button mResolution, mLayoutLandscape, mLayoutPortrait;
    private TextView mOrientationNote, mPictureNote, mSizeLabel, mOpacityLabel, mPlaceLabel, mPlaceNote;
    private SeekBar mSize, mOpacity, mPlace, mMusic, mVoice, mSfx;
    private TextView mMusicLabel, mVoiceLabel, mSfxLabel;
    private boolean mUpdating;
    private Button[] mFrameskip;
    private TextView mDebugNote;
    private Button mExportLatest, mExportPick;

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

    private TextView text(String s, int sp, int color, boolean bold) {
        TextView t = new TextView(this);
        t.setText(s);
        t.setTextSize(TypedValue.COMPLEX_UNIT_SP, sp);
        t.setTextColor(color);
        if (bold) t.setTypeface(Ui.medium());
        return t;
    }

    /** A section's title: a card's, or a group's inside a card. TalkBack can move from one to the next. */
    private TextView heading(String title) {
        return Ui.heading(this, title);
    }

    /** A rounded card holding one section's rows. */
    private LinearLayout card(LinearLayout col, String title) {
        col.addView(heading(title), Ui.full(dp(28)));
        LinearLayout c = Ui.card(this);
        col.addView(c, Ui.full(dp(8)));
        return c;
    }

    private void label(LinearLayout c, String s, boolean first) {
        c.addView(Ui.text(this, s, Ui.TITLE_MEDIUM, TEXT), Ui.full(dp(first ? 8 : 20)));
    }

    private TextView note(LinearLayout c, String s) {
        TextView t = Ui.text(this, s, Ui.BODY_MEDIUM, NOTE);
        c.addView(t, Ui.full(dp(4)));
        return t;
    }

    /** Choices side by side, the chosen one filled. */
    private Button[] segments(LinearLayout c, String[] labels, View.OnClickListener[] clicks) {
        Button[] bs = new Button[labels.length];
        for (int i = 0; i < labels.length; i++) {
            bs[i] = Ui.button(this, labels[i], clicks[i]);
            Ui.style(bs[i], Ui.QUIET);
            bs[i].setMinWidth(0);
            bs[i].setMinimumWidth(0);
        }
        c.addView(Ui.row(this, bs), Ui.full(dp(8)));
        return bs;
    }

    /** The chosen one filled, and said to be selected (TalkBack reads the state, not the colour). */
    private static void choose(Button[] bs, int chosen) {
        for (int i = 0; i < bs.length; i++) {
            Button b = bs[i];
            Ui.style(b, i == chosen ? Ui.SECONDARY : Ui.QUIET);
            b.setPadding(Ui.dp(b.getContext(), 8), 0, Ui.dp(b.getContext(), 8), 0);
            b.setSelected(i == chosen);
            if (Build.VERSION.SDK_INT >= 30) b.setStateDescription(i == chosen ? "Selected" : null);
            else b.setContentDescription(b.getText() + (i == chosen ? ", selected" : ""));
        }
    }

    private Switch toggle(LinearLayout c, String s, CompoundButton.OnCheckedChangeListener change) {
        Switch w = Ui.switchStyle(this, s);
        w.setOnCheckedChangeListener((b, on) -> {
            if (!mUpdating) change.onCheckedChanged(b, on);
        });
        c.addView(w, Ui.full(dp(8)));
        return w;
    }

    /** A Sound slider moved: the levels apply through volume-levels, so it goes on with them. */
    private void volumeChanged() {
        mS.tweaksOn.add(GameSettings.VOLUME_LEVELS);
        save();
    }

    /** A Sound slider and its label at `percent` (0 to 100 in steps of 5). */
    private static void volumeRow(SeekBar b, TextView label, String name, int percent) {
        label.setText(String.format(Locale.US, "%s: %d%%", name, percent));
        b.setProgress(percent / 5);
        describe(b, label, percent + "%");
    }

    /** A slider from lo to hi percent in steps of `step`, named by `label`. */
    private SeekBar slider(LinearLayout c, TextView label, int lo, int hi, int step,
                           java.util.function.IntConsumer change) {
        SeekBar b = new SeekBar(this);
        b.setId(View.generateViewId());
        label.setLabelFor(b.getId());
        b.setMax((hi - lo) / step);
        b.setProgressTintList(ColorStateList.valueOf(Ui.PRIMARY_COLOR));
        b.setProgressBackgroundTintList(ColorStateList.valueOf(Ui.SECONDARY_CONTAINER));
        b.setThumbTintList(ColorStateList.valueOf(Ui.PRIMARY_COLOR));
        b.setOnSeekBarChangeListener(new SeekBar.OnSeekBarChangeListener() {
            @Override
            public void onProgressChanged(SeekBar s, int p, boolean user) {
                if (user && !mUpdating) change.accept(lo + step * p);
            }

            @Override
            public void onStartTrackingTouch(SeekBar s) {}

            @Override
            public void onStopTrackingTouch(SeekBar s) {}
        });
        c.addView(b, Ui.full(dp(4)));
        return b;
    }

    private Button action(LinearLayout c, String s, View.OnClickListener click) {
        Button b = Ui.button(this, s, click);
        Ui.listButton(b);
        c.addView(b, Ui.full(dp(4)));
        return b;
    }

    private void buildUi() {
        LinearLayout bar = Ui.topAppBar(this, "Game settings", null);
        int pad = dp(16);
        Ui.Column col = new Ui.Column(this, dp(640));
        col.setPadding(pad, 0, pad, dp(32));
        TextView intro = Ui.text(this, "Changes apply the next time the game starts.", Ui.BODY_MEDIUM, NOTE);
        intro.setPadding(dp(4), 0, dp(4), 0);
        col.addView(intro, Ui.full(dp(4)));

        // Starting
        LinearLayout st = card(col, "Starting the game");
        mAutoPlay = toggle(st, "Start the game when the app opens", (b, on) -> {
            mS.autoPlay = on;
            save();
        });
        note(st, "Off: the app opens to its own screen and you tap Play. On: the game starts at once when the "
                + "app opens, and when an install finishes.");

        // Display
        LinearLayout d = card(col, "Display");
        label(d, "Orientation", true);
        View.OnClickListener[] oc = new View.OnClickListener[3];
        for (int i = 0; i < 3; i++) {
            final String o = ORIENTATIONS[i];
            oc[i] = v -> {
                mS.orientation = o;
                save();
            };
        }
        mOrientation = segments(d, new String[] {"Landscape", "Portrait", "Auto-rotate"}, oc);
        mOrientationNote = note(d, "");
        mPlaceLabel = text("", 15, TEXT, true);
        d.addView(mPlaceLabel, Ui.full(dp(16)));
        mPlace = slider(d, mPlaceLabel, 0, 50, 5, v -> {
            mS.pictureY = v;
            save();
        });
        mPlaceNote = note(d, "Where the picture sits when the phone is upright. Higher leaves more room for the "
                             + "touch controls below it.");
        mWidescreen = toggle(d, "Full-width picture", (b, on) -> {
            mS.widescreen = on;
            save();
        });
        note(d, "Draws the game at the screen's shape. Off keeps the game's own 16:9 picture, with bars at "
                + "the sides of a wider screen.");
        label(d, "Aspect ratio", false);
        View.OnClickListener[] ac = new View.OnClickListener[GameSettings.ASPECTS.length];
        for (int i = 0; i < ac.length; i++) {
            final String a = GameSettings.ASPECTS[i];
            ac[i] = v -> {
                mS.aspect = a;
                save();
            };
        }
        mAspect = segments(d, new String[] {"Screen", "21:9", "16:9", "4:3"}, ac);
        label(d, "Resolution", false);
        mResolution = Ui.button(this, "", v -> pickResolution());
        Ui.navButton(mResolution);
        d.addView(mResolution, Ui.full(dp(0)));
        note(d, "Fewer lines can run faster on a slower phone.");
        label(d, "Low-FPS speed fix (experimental)", false);
        View.OnClickListener[] fc = new View.OnClickListener[GameSettings.FRAMESKIPS.length];
        for (int i = 0; i < fc.length; i++) {
            final String f = GameSettings.FRAMESKIPS[i];
            fc[i] = v -> {
                mS.frameskip = f;
                save();
            };
        }
        mFrameskip = segments(d, new String[] {"Off", "30 FPS", "20 FPS", "Auto"}, fc);
        note(d, "Keeps stage speed right when the phone can't hold 60 FPS by running extra game ticks per "
                + "drawn frame. Menus and cutscenes still slow down.");
        mHud = toggle(d, "HUD at 4:3 positions", (b, on) -> {
            mS.hud43 = on;
            save();
        });
        note(d, "Keeps the score, time and rings near the middle of a wide picture.");
        mPictureNote = Ui.text(this, "", Ui.BODY_MEDIUM, Ui.TERTIARY);
        d.addView(mPictureNote, Ui.full(dp(16)));

        // Touch controls
        LinearLayout t = card(col, "Touch controls");
        mSizeLabel = text("", 15, TEXT, true);
        t.addView(mSizeLabel);
        mSize = slider(t, mSizeLabel, 50, 200, 10, v -> {
            mS.touchSize = v;
            save();
        });
        mOpacityLabel = text("", 15, TEXT, true);
        t.addView(mOpacityLabel, Ui.full(dp(12)));
        mOpacity = slider(t, mOpacityLabel, 0, 200, 10, v -> {
            mS.touchOpacity = v;
            save();
        });
        note(t, "At 0% the controls are hidden but still work.");
        mLayoutLandscape = action(t, "", v -> editLayout(GameSettings.LANDSCAPE));
        mLayoutPortrait = action(t, "", v -> editLayout(GameSettings.PORTRAIT));
        Ui.navButton(mLayoutLandscape);
        Ui.navButton(mLayoutPortrait);
        Ui.navButton(action(t, "Change what the touch buttons do", v -> pickMap(true, -1)));

        // Sound: the levels volume-levels plays at (GameSettings.VOLUME_LEVELS); moving a slider turns it on
        LinearLayout so = card(col, "Sound");
        mMusicLabel = text("", 15, TEXT, true);
        so.addView(mMusicLabel);
        mMusic = slider(so, mMusicLabel, 0, 100, 5, v -> {
            mS.musicVolume = v;
            volumeChanged();
        });
        mVoiceLabel = text("", 15, TEXT, true);
        so.addView(mVoiceLabel, Ui.full(dp(12)));
        mVoice = slider(so, mVoiceLabel, 0, 100, 5, v -> {
            mS.voiceVolume = v;
            volumeChanged();
        });
        mSfxLabel = text("", 15, TEXT, true);
        so.addView(mSfxLabel, Ui.full(dp(12)));
        mSfx = slider(so, mSfxLabel, 0, 100, 5, v -> {
            mS.sfxVolume = v;
            volumeChanged();
        });
        note(so, "100% is the game's own level. The PC version's music is loud; the Dreamcast's sat lower. "
                + "Moving a slider turns on Volume levels under Tweaks, Audio; turn it off there for the game's levels. "
                + "Short music cues such as the 1-up keep their level.");

        // Controller
        LinearLayout p = card(col, "Controller");
        Ui.navButton(action(p, "Change what the controller buttons do", v -> pickMap(false, -1)));
        note(p, "Buttons are named by where they are: the bottom face button is A, as on an Xbox controller.");

        // Tweaks: their own screen (Sa2TweaksActivity), with a count of the ones on.
        LinearLayout tw = card(col, "Tweaks");
        mTweaksEntry = Ui.button(this, "", v -> startActivity(new Intent(this, Sa2TweaksActivity.class)));
        Ui.navButton(mTweaksEntry);
        tw.addView(mTweaksEntry, Ui.full(dp(0)));
        note(tw, "Changes to the game's own behaviour, each off until you turn it on: fixes, assists, look, sound, "
                + "and Chao. Each one says what it changes, and which ones change your save.");
        // Saves: the game's own saves, imported, exported and restored (SaveTools).
        LinearLayout sv = card(col, "Saves");
        action(sv, "Import saves", v -> pickSaves());
        action(sv, "Export saves", v -> pickExport());
        mBackupNow = action(sv, "Back up saves now", v -> backupNow());
        action(sv, "Restore a backup", v -> pickBackup());
        note(sv, "Import takes save files (SONIC2B__S01 and the like for the story, SONIC2B__ALF for Chao World) "
                + "or a ZIP of them, or a Debug Export's saves. It replaces only the saves it brings, and backs up the current ones first (the "
                + "last " + SaveTools.KEEP + " are kept). Export writes the saves the game sees to a ZIP you choose. "
                + "Changes apply from the next session.");

        // Debug, last
        LinearLayout g = card(col, "Debug");
        debugRows(g);

        Button reset = Ui.button(this, "Reset all settings", v -> new AlertDialog.Builder(this)
                .setMessage("Put every game setting back to its default?")
                .setPositiveButton("Reset", (dlg, w) -> {
                    mS = new GameSettings();
                    save();
                })
                .setNegativeButton("Cancel", null)
                .show());
        Ui.style(reset, Ui.QUIET);
        col.addView(reset, Ui.full(dp(32)));
        Ui.screen(this, bar, col);
    }

    // ------------------------------------------------------------ the state shown

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
        boolean fixed = mS.fixed43();
        choose(mOrientation, indexOf(ORIENTATIONS, mS.orientation));
        mOrientationNote.setText(GameSettings.PORTRAIT.equals(mS.orientation)
                ? "Hold the phone upright: the game is drawn at 4:3 across the screen, with the touch controls "
                  + "below it where the screen leaves room."
                : GameSettings.AUTO.equals(mS.orientation)
                ? "The game turns with the phone when the phone's auto-rotate is on. It is drawn at 4:3 so it "
                  + "fits either way; upright, the touch controls go below it where the screen leaves room."
                : "Hold the phone sideways, as before.");
        mWidescreen.setChecked(!fixed && mS.widescreen);
        mWidescreen.setEnabled(!fixed);
        boolean wide = !fixed && mS.widescreen;
        choose(mAspect, fixed ? 3 : indexOf(GameSettings.ASPECTS, mS.aspect));
        for (Button b : mAspect) b.setEnabled(wide);
        mResolution.setText(resolutionName(shownResolution()));
        mResolution.setEnabled(fixed || wide);
        mHud.setChecked(mS.hud43);
        mAutoPlay.setChecked(mS.autoPlay);
        if (mRecord != null) mRecord.setChecked(mS.recordSessions);
        int on = 0;
        for (GameSettings.Tweak t : GameSettings.TWEAKS) if (mS.tweaksOn.contains(t.name)) on++;
        mTweaksEntry.setText(on == 0 ? "Tweaks: all off" : "Tweaks: " + on + " of " + GameSettings.TWEAKS.length + " on");
        int[] pic = mS.picture(mLong, mShort);
        mPictureNote.setText(pic == null
                ? "The game keeps its own size (" + GameSettings.CONFIG_W + "x" + GameSettings.CONFIG_H + ")."
                : "The game will draw at " + size(pic) + (fixed ? ", 4:3, as portrait and auto-rotate always do." : "."));
        mSizeLabel.setText(String.format(Locale.US, "Size: %d%%", mS.touchSize));
        mSize.setProgress((mS.touchSize - 50) / 10);
        describe(mSize, mSizeLabel, mS.touchSize + "%");
        mOpacityLabel.setText(String.format(Locale.US, "Opacity: %d%%", mS.touchOpacity));
        mOpacity.setProgress(mS.touchOpacity / 10);
        describe(mOpacity, mOpacityLabel, mS.touchOpacity + "%");
        volumeRow(mMusic, mMusicLabel, "Music", mS.musicVolume);
        volumeRow(mVoice, mVoiceLabel, "Voices", mS.voiceVolume);
        volumeRow(mSfx, mSfxLabel, "Sound effects", mS.sfxVolume);
        mPlaceLabel.setText("Picture position: " + placeName(mS.pictureY));
        mPlace.setProgress(mS.pictureY / 5);
        describe(mPlace, mPlaceLabel, placeName(mS.pictureY));
        for (View v : new View[] {mPlaceLabel, mPlace, mPlaceNote}) v.setVisibility(fixed ? View.VISIBLE : View.GONE);
        boolean landscape = !GameSettings.PORTRAIT.equals(mS.orientation);
        boolean portrait = !GameSettings.LANDSCAPE.equals(mS.orientation);
        mLayoutLandscape.setVisibility(landscape ? View.VISIBLE : View.GONE);
        mLayoutPortrait.setVisibility(portrait ? View.VISIBLE : View.GONE);
        mLayoutLandscape.setText(portrait ? "Move the controls (landscape)" : "Move the controls");
        mLayoutPortrait.setText(landscape ? "Move the controls (portrait)" : "Move the controls");
        choose(mFrameskip, indexOf(GameSettings.FRAMESKIPS, mS.frameskip));
        mUpdating = false;
    }

    // ------------------------------------------------------------ the choices

    /** The default first (the largest picture, or the game's own size where the session keeps it,
     * on a screen no wider than 4:3, which then also offers the full screen), then fewer lines
     * than the largest picture has. */
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

    /** The Debug section's rows. */
    private void debugRows(LinearLayout g) {
        mRecord = toggle(g, "Record sessions", (b, on) -> {
            mS.recordSessions = on;
            save();
        });
        note(g, "Keeps each session's saves and settings from before and after it, and the controller's "
                + "input, so an export can be replayed. Off, a session keeps only its logs. Applies from the "
                + "next session.");
        label(g, "Debug Export", false);
        mDebugNote = note(g, "");
        mExportLatest = action(g, "Export the latest session", v -> export(null));
        mExportPick = action(g, "Export another session", v -> pickSession());
        note(g, "Makes one ZIP of a play session's logs and saves, with what is needed to replay it when the "
                + "session was recorded, and shares it. It holds no pictures of the game.");
    }

    private void updateDebug() {
        if (mDebugNote == null) return;
        List<File> all = DebugExport.sessions(this);
        mDebugNote.setText(all.isEmpty() ? "No session yet: play once first."
                : "Latest session: " + all.get(0).getName() + " (" + all.size() + " kept)");
        mExportLatest.setEnabled(!all.isEmpty());
        mExportPick.setEnabled(all.size() > 1);
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
        mExportLatest.setEnabled(false);
        mExportPick.setEnabled(false);
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
