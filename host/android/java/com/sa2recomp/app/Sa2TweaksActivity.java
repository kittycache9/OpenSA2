package com.sa2recomp.app;

import android.app.Activity;
import android.app.AlertDialog;
import android.os.Bundle;
import android.text.Editable;
import android.text.TextUtils;
import android.text.TextWatcher;
import android.util.Log;
import android.view.View;
import android.widget.Button;
import android.widget.Switch;
import android.widget.TextView;
import android.widget.Toast;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * The Tweaks screen: every changes-to-the-game switch, under GameSettings.TWEAK_GROUPS's headings,
 * each group's switches in the group's own order (GameSettings.tweakGroups). An overview lists the
 * groups, each with how many of its tweaks are on, and opens each as its own page; its search field
 * shows the tweaks whose label or note match, across groups. Opened from Game settings. A switch is
 * its tweak's by name (one on its group's page, one in the search results, kept alike), and the
 * switches are saved in the preferences like every other setting; nothing here names a tweak except
 * the Chao boosts row.
 */
public class Sa2TweaksActivity extends Activity {
    private GameSettings mS;
    private boolean mUpdating;
    private Ui.Pages mPages;
    private final Map<String, List<Switch>> mSwitches = new HashMap<>();   // each GameSettings.TWEAKS name's switches
    private final List<Runnable> mRefresh = new ArrayList<>();
    private final Map<String, View> mResults = new HashMap<>();             // each tweak's row in the search results
    private Ui.Group mResultGroup, mGroups;
    private TextView mCount, mNoMatch;
    private Button mTurnOff;
    private String mQuery = "";

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

    private static int icon(String heading) {
        switch (heading) {
            case "Gameplay and assists": return Ui.Icon.STAR;
            case "Story and presentation": return Ui.Icon.BOOK;
            case "Controls": return Ui.Icon.GAMEPAD;
            case "Audio": return Ui.Icon.SOUND;
            case "Chao": return Ui.Icon.EGG;
            case "Fixes": return Ui.Icon.CHECK;
            default: return Ui.Icon.DOTS;
        }
    }

    private int on(List<GameSettings.Tweak> tweaks) {
        int n = 0;
        for (GameSettings.Tweak t : tweaks) if (mS.tweaksOn.contains(t.name)) n++;
        return n;
    }

    private void buildUi() {
        mTurnOff = Ui.button(this, "Turn every tweak off", v -> confirmTurnOff());
        Ui.style(mTurnOff, Ui.TEXT);
        mTurnOff.setText("Turn all off");
        mTurnOff.setContentDescription("Turn every tweak off");

        Ui.Column root = new Ui.Column(this, dp(640));
        root.setPadding(dp(16), 0, dp(16), dp(40));
        mPages = new Ui.Pages(this, "Tweaks", root, mTurnOff);

        mCount = Ui.text(this, "", Ui.BODY_MEDIUM, Ui.ON_SURFACE_VARIANT);
        mCount.setPadding(dp(16), 0, dp(16), 0);
        root.addView(mCount, Ui.full(dp(4)));

        Ui.SearchField search = new Ui.SearchField(this, "Search tweaks");
        search.edit.addTextChangedListener(new TextWatcher() {
            @Override public void beforeTextChanged(CharSequence s, int a, int b, int c) {}
            @Override public void onTextChanged(CharSequence s, int a, int b, int c) {}
            @Override public void afterTextChanged(Editable s) {
                mQuery = s.toString().trim().toLowerCase(Locale.ROOT);
                filter();
            }
        });
        root.addView(search.view, Ui.full(dp(16)));

        mNoMatch = Ui.text(this, "No tweak matches that.", Ui.BODY_MEDIUM, Ui.ON_SURFACE_VARIANT);
        mNoMatch.setPadding(dp(16), 0, dp(16), 0);
        mNoMatch.setVisibility(View.GONE);
        root.addView(mNoMatch, Ui.full(dp(24)));

        List<String> problems = new ArrayList<>();
        List<GameSettings.TweakGroup> groups =
                GameSettings.tweakGroups(GameSettings.TWEAK_GROUPS, GameSettings.TWEAKS, problems);
        for (String problem : problems) Log.w("sa2recomp", "sa2 tweaks screen: " + problem);

        mResultGroup = new Ui.Group(this, false);
        mResultGroup.view.setVisibility(View.GONE);
        root.addView(mResultGroup.view, Ui.full(dp(16)));
        mGroups = new Ui.Group(this, true);
        root.addView(mGroups.view, Ui.full(dp(16)));

        for (GameSettings.TweakGroup group : groups) {
            Ui.ListRow row = new Ui.ListRow(this, Ui.badge(this, icon(group.heading)), group.heading, "",
                                            Ui.chevron(this), v -> mPages.open(group.heading));
            mGroups.add(row.view);
            mRefresh.add(() -> {
                int n = on(group.tweaks);
                row.supporting.setText(n == 0 ? group.tweaks.size() + " tweaks, all off"
                                              : n + " of " + group.tweaks.size() + " on");
                row.supporting.setVisibility(View.VISIBLE);
            });

            Ui.Column page = mPages.add(group.heading);
            Ui.Group list = new Ui.Group(this, false);
            page.addView(list.view, Ui.full(dp(8)));
            for (GameSettings.Tweak tweak : group.tweaks) {
                list.add(tweakRow(tweak));
                View result = tweakRow(tweak);
                mResults.put(tweak.name, result);
                mResultGroup.add(result);
            }
            if ("Chao".equals(group.heading)) {
                Ui.ListRow boosts = new Ui.ListRow(this, "Turn on the Chao boosts",
                        "Chao stats x5 and Rings over time in Chao World together, after a backup", null,
                        v -> chaoBoosts());
                list.add(boosts.view);
                mRefresh.add(() -> boosts.setEnabled(!mS.tweaksOn.containsAll(Arrays.asList(GameSettings.CHAO_BOOSTS))));
            }
            page.addView(Ui.footnote(this, "Tap a note to read all of it. Changes apply the next time the game starts."),
                         Ui.full(dp(10)));
        }
        mPages.start();
    }

    /** A tweak's row: its label, the first two lines of its note (tap the note for all of it), and its
     * switch; a tap anywhere else on the row flips the switch. */
    private View tweakRow(GameSettings.Tweak tweak) {
        Switch w = Ui.switchStyle(this, "");
        w.setContentDescription(tweak.label);
        w.setOnCheckedChangeListener((b, on) -> {
            if (mUpdating) return;
            if (on) mS.tweaksOn.add(tweak.name);
            else mS.tweaksOn.remove(tweak.name);
            save();
        });
        mSwitches.computeIfAbsent(tweak.name, k -> new ArrayList<>()).add(w);
        Ui.ListRow row = new Ui.ListRow(this, tweak.label, tweak.description, w, v -> w.toggle());
        TextView note = row.supporting;
        note.setMaxLines(2);
        note.setEllipsize(TextUtils.TruncateAt.END);
        note.setOnClickListener(v -> {
            boolean open = note.getMaxLines() == 2;
            note.setMaxLines(open ? Integer.MAX_VALUE : 2);
            note.setEllipsize(open ? null : TextUtils.TruncateAt.END);
        });
        return row.view;
    }

    /** With a query, the matching tweaks across groups in place of the groups. */
    private void filter() {
        boolean searching = !mQuery.isEmpty();
        int shown = 0;
        for (GameSettings.Tweak t : GameSettings.TWEAKS) {
            View row = mResults.get(t.name);
            if (row == null) continue;
            boolean hit = searching && (t.label.toLowerCase(Locale.ROOT).contains(mQuery)
                                        || t.description.toLowerCase(Locale.ROOT).contains(mQuery));
            mResultGroup.show(row, hit);
            if (hit) shown++;
        }
        mResultGroup.view.setVisibility(searching && shown > 0 ? View.VISIBLE : View.GONE);
        mGroups.view.setVisibility(searching ? View.GONE : View.VISIBLE);
        mNoMatch.setVisibility(searching && shown == 0 ? View.VISIBLE : View.GONE);
    }

    private void update() {
        mUpdating = true;
        int total = GameSettings.TWEAKS.length, on = 0;
        for (GameSettings.Tweak t : GameSettings.TWEAKS) if (mS.tweaksOn.contains(t.name)) on++;
        mCount.setText(on == 0 ? "All " + total + " tweaks are off. Each changes the game only once you turn it on."
                               : on + " of " + total + " tweaks on. Changes apply the next time the game starts.");
        for (Map.Entry<String, List<Switch>> e : mSwitches.entrySet())
            for (Switch w : e.getValue()) w.setChecked(mS.tweaksOn.contains(e.getKey()));
        for (Runnable r : mRefresh) r.run();
        mTurnOff.setEnabled(on > 0);
        mTurnOff.setVisibility(on > 0 ? View.VISIBLE : View.INVISIBLE);
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
     * switched off again, so the first question offers the backup the Saves page makes. */
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
