package com.sa2recomp.app;

import android.app.Activity;
import android.app.AlertDialog;
import android.graphics.Color;
import android.os.Bundle;
import android.util.Log;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Switch;
import android.widget.TextView;
import android.widget.Toast;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * The Tweaks screen: every changes-to-the-game switch, under GameSettings.TWEAK_GROUPS's headings,
 * one card per group, each group's switches in the group's own order (GameSettings.tweakGroups).
 * Opened from Game settings. A switch is its tweak's by name, and the switches are saved in the
 * preferences like every other setting; nothing here names a tweak except the Chao boosts button.
 */
public class Sa2TweaksActivity extends Activity {
    private static final int TEXT = Color.rgb(0xE8, 0xED, 0xF7), NOTE = Color.rgb(0x8A, 0x97, 0xB2);

    private GameSettings mS;
    private boolean mUpdating;
    private final Map<String, Switch> mSwitches = new HashMap<>();   // each GameSettings.TWEAKS name's switch
    private TextView mCount;
    private Button mTurnOff, mChaoBoosts;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        mS = GameSettings.load(this);
        buildUi();
        update();
    }

    @Override
    protected void onResume() {
        super.onResume();
        mS = GameSettings.load(this);
        update();
    }

    private int dp(float v) {
        return Ui.dp(this, v);
    }

    private void save() {
        mS.save(this);
        update();
    }

    private TextView text(String s, int sp, int color, boolean bold) {
        TextView t = new TextView(this);
        t.setText(s);
        t.setTextSize(TypedValue.COMPLEX_UNIT_SP, sp);
        t.setTextColor(color);
        if (bold) t.setTypeface(android.graphics.Typeface.DEFAULT_BOLD);
        return t;
    }

    private void buildUi() {
        int pad = dp(16);
        Ui.Column col = new Ui.Column(this, dp(640));
        col.setPadding(pad, pad, pad, pad);

        Button back = Ui.button(this, "Game settings", v -> finish());
        Ui.style(back, Ui.QUIET);
        col.addView(back, Ui.full(0));

        col.addView(text("Tweaks", 26, Color.WHITE, true), Ui.full(dp(12)));
        mCount = text("", 14, NOTE, false);
        col.addView(mCount, Ui.full(dp(4)));
        col.addView(text("Every tweak is off until you turn it on. Changes apply the next time the game "
                         + "starts. Replays never use them.", 14, NOTE, false), Ui.full(dp(8)));

        mTurnOff = Ui.button(this, "Turn every tweak off", v -> confirmTurnOff());
        Ui.style(mTurnOff, Ui.SECONDARY);
        col.addView(mTurnOff, Ui.full(dp(16)));

        List<String> problems = new ArrayList<>();
        List<GameSettings.TweakGroup> groups =
                GameSettings.tweakGroups(GameSettings.TWEAK_GROUPS, GameSettings.TWEAKS, problems);
        for (String problem : problems) Log.w("sa2recomp", "sa2 tweaks screen: " + problem);
        for (GameSettings.TweakGroup group : groups) {
            col.addView(Ui.heading(this, group.heading), Ui.full(dp(24)));
            LinearLayout card = Ui.card(this);
            for (GameSettings.Tweak tweak : group.tweaks) {
                if (tweak != group.tweaks.get(0)) card.addView(Ui.divider(this), Ui.full(dp(12)));
                Switch w = Ui.switchStyle(this, tweak.label);
                w.setOnCheckedChangeListener((b, on) -> {
                    if (mUpdating) return;
                    if (on) mS.tweaksOn.add(tweak.name);
                    else mS.tweaksOn.remove(tweak.name);
                    save();
                });
                mSwitches.put(tweak.name, w);
                card.addView(w, Ui.full(dp(12)));
                TextView note = text(tweak.description, 13, NOTE, false);
                card.addView(note, Ui.full(dp(2)));
            }
            if ("Chao".equals(group.heading)) {
                mChaoBoosts = Ui.button(this, "Turn on the Chao boosts", v -> chaoBoosts());
                Ui.style(mChaoBoosts, Ui.SECONDARY);
                card.addView(Ui.divider(this), Ui.full(dp(12)));
                card.addView(mChaoBoosts, Ui.full(dp(12)));
            }
            col.addView(card, Ui.full(dp(8)));
        }

        FrameLayout center = new FrameLayout(this);
        center.addView(col, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                                                         ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.CENTER_HORIZONTAL));
        ScrollView scroll = new ScrollView(this);
        scroll.setBackgroundColor(Ui.BACKGROUND);
        Ui.padForSystemBars(scroll);
        scroll.addView(center, new ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                                                           ViewGroup.LayoutParams.WRAP_CONTENT));
        setContentView(scroll);
    }

    private void update() {
        mUpdating = true;
        int total = GameSettings.TWEAKS.length, on = 0;
        for (GameSettings.Tweak t : GameSettings.TWEAKS) if (mS.tweaksOn.contains(t.name)) on++;
        mCount.setText(on == 0 ? "All " + total + " are off" : on + " of " + total + " on");
        for (Map.Entry<String, Switch> e : mSwitches.entrySet()) e.getValue().setChecked(mS.tweaksOn.contains(e.getKey()));
        mTurnOff.setEnabled(on > 0);
        if (mChaoBoosts != null)
            mChaoBoosts.setEnabled(!mS.tweaksOn.containsAll(Arrays.asList(GameSettings.CHAO_BOOSTS)));
        mUpdating = false;
    }

    private void confirmTurnOff() {
        new AlertDialog.Builder(this)
                .setMessage("Turn every tweak off, so the game plays as it was made?")
                .setPositiveButton("Turn off", (dlg, w) -> {
                    Log.i("sa2recomp", "sa2 host tweaks: vanilla: every tweak turned off on the Tweaks screen (were "
                          + mS.tweaksOff() + ")");
                    save();
                })
                .setNegativeButton("Cancel", null)
                .show();
    }

    /** The Chao boosts: GameSettings.CHAO_BOOSTS together. Both write to the save and stay in it when
     * switched off again, so the first question offers the backup the Saves card makes. */
    private void chaoBoosts() {
        new AlertDialog.Builder(this).setTitle("Turn on the Chao boosts?")
                .setMessage("This turns on \"Chao stats x5\" and \"Rings over time in Chao World\". The stat "
                        + "points and rings they add are written to your save and stay when you turn the boosts "
                        + "off. You can back up your saves first, then restore that backup to undo it.")
                .setPositiveButton("Back up, then turn on", (dlg, w) -> new Thread(() -> {
                    String msg;
                    boolean backedUp;
                    try {
                        msg = SaveTools.backupNow(this);
                        backedUp = true;
                    } catch (Exception e) {
                        msg = "No backup was made (" + e.getMessage() + "), so the Chao boosts stay off.";
                        backedUp = false;
                    }
                    final String m = msg;
                    final boolean on = backedUp;
                    runOnUiThread(() -> {
                        Toast.makeText(this, m, Toast.LENGTH_LONG).show();
                        if (on) applyChaoBoosts();
                    });
                }, "save-backup").start())
                .setNeutralButton("Turn on", (dlg, w) -> applyChaoBoosts())
                .setNegativeButton("Cancel", null)
                .show();
    }

    private void applyChaoBoosts() {
        mS.tweaksOn.addAll(Arrays.asList(GameSettings.CHAO_BOOSTS));
        save();
    }
}
