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
import android.widget.ImageView;
import android.widget.LinearLayout;
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
 * each group's switches in the group's own order (GameSettings.tweakGroups). A group shows as one row
 * with how many of its tweaks are on and opens in place; the search field shows the tweaks whose
 * label or note match, across groups. Opened from Game settings. A switch is its tweak's by name, and
 * the switches are saved in the preferences like every other setting; nothing here names a tweak
 * except the Chao boosts button.
 */
public class Sa2TweaksActivity extends Activity {
    /** One group on screen: its row, the rows of its tweaks, and whether it is open. */
    private static final class Group {
        final GameSettings.TweakGroup group;
        final Ui.ListRow header;
        final Ui.Icon expand;
        final LinearLayout body;
        final Map<String, View> rows = new HashMap<>();
        boolean open;

        Group(GameSettings.TweakGroup group, Ui.ListRow header, Ui.Icon expand, LinearLayout body) {
            this.group = group;
            this.header = header;
            this.expand = expand;
            this.body = body;
        }
    }

    private GameSettings mS;
    private boolean mUpdating;
    private final Map<String, Switch> mSwitches = new HashMap<>();   // each GameSettings.TWEAKS name's switch
    private final List<Group> mGroups = new ArrayList<>();
    private TextView mCount, mNoMatch;
    private Button mTurnOff, mChaoBoosts;
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

    private void buildUi() {
        mTurnOff = Ui.button(this, "Turn every tweak off", v -> confirmTurnOff());
        Ui.style(mTurnOff, Ui.TEXT);
        mTurnOff.setText("Turn all off");
        mTurnOff.setContentDescription("Turn every tweak off");
        LinearLayout bar = Ui.topAppBar(this, "Tweaks", mTurnOff);

        int pad = dp(16);
        Ui.Column col = new Ui.Column(this, dp(640));
        col.setPadding(pad, 0, pad, dp(32));

        mCount = Ui.text(this, "", Ui.BODY_MEDIUM, Ui.ON_SURFACE_VARIANT);
        mCount.setPadding(dp(4), 0, dp(4), 0);
        col.addView(mCount, Ui.full(dp(4)));

        Ui.SearchField search = new Ui.SearchField(this, "Search tweaks");
        search.edit.addTextChangedListener(new TextWatcher() {
            @Override public void beforeTextChanged(CharSequence s, int a, int b, int c) {}
            @Override public void onTextChanged(CharSequence s, int a, int b, int c) {}
            @Override public void afterTextChanged(Editable s) {
                mQuery = s.toString().trim().toLowerCase(Locale.ROOT);
                filter();
            }
        });
        col.addView(search.view, Ui.full(dp(16)));

        mNoMatch = Ui.text(this, "No tweak matches that.", Ui.BODY_MEDIUM, Ui.ON_SURFACE_VARIANT);
        mNoMatch.setPadding(dp(4), 0, dp(4), 0);
        mNoMatch.setVisibility(View.GONE);
        col.addView(mNoMatch, Ui.full(dp(24)));

        List<String> problems = new ArrayList<>();
        List<GameSettings.TweakGroup> groups =
                GameSettings.tweakGroups(GameSettings.TWEAK_GROUPS, GameSettings.TWEAKS, problems);
        for (String problem : problems) Log.w("sa2recomp", "sa2 tweaks screen: " + problem);
        LinearLayout list = Ui.group(this);
        for (GameSettings.TweakGroup group : groups) {
            if (!mGroups.isEmpty()) list.addView(Ui.divider(this), Ui.hairline(this));
            Ui.Icon expand = new Ui.Icon(this, Ui.Icon.EXPAND, Ui.ON_SURFACE_VARIANT);
            ImageView end = new ImageView(this);
            end.setImageDrawable(expand);
            end.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
            LinearLayout body = new LinearLayout(this);
            body.setOrientation(LinearLayout.VERTICAL);
            body.setVisibility(View.GONE);
            Group[] self = new Group[1];
            Ui.ListRow header = new Ui.ListRow(this, group.heading, "", end, v -> toggleGroup(self[0]));
            Ui.type(header.headline, Ui.TITLE_MEDIUM);
            self[0] = new Group(group, header, expand, body);
            mGroups.add(self[0]);
            list.addView(header.view);
            for (GameSettings.Tweak tweak : group.tweaks) {
                View row = tweakRow(tweak);
                self[0].rows.put(tweak.name, row);
                body.addView(row);
            }
            if ("Chao".equals(group.heading)) {
                mChaoBoosts = Ui.button(this, "Turn on the Chao boosts", v -> chaoBoosts());
                Ui.style(mChaoBoosts, Ui.SECONDARY);
                LinearLayout.LayoutParams lp = Ui.full(dp(4));
                lp.leftMargin = dp(16);
                lp.rightMargin = dp(16);
                lp.bottomMargin = dp(16);
                body.addView(mChaoBoosts, lp);
            }
            list.addView(body);
        }
        col.addView(list, Ui.full(dp(16)));
        Ui.screen(this, bar, col);
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
        mSwitches.put(tweak.name, w);
        Ui.ListRow row = new Ui.ListRow(this, tweak.label, tweak.description, w, v -> w.toggle());
        row.view.setPadding(dp(16), dp(10), dp(16), dp(10));
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

    private void toggleGroup(Group g) {
        if (!mQuery.isEmpty()) return;
        g.open = !g.open;
        g.body.setVisibility(g.open ? View.VISIBLE : View.GONE);
        g.expand.rotate(g.open ? 180 : 0);
        g.header.view.setContentDescription(g.group.heading + ", " + g.header.supporting.getText()
                                            + (g.open ? ", open" : ", closed"));
    }

    /** With a query, every group that holds a match is open with only the matching tweaks; without
     * one, each group is as the player left it. */
    private void filter() {
        int shown = 0;
        for (Group g : mGroups) {
            int matches = 0;
            for (GameSettings.Tweak t : g.group.tweaks) {
                boolean hit = mQuery.isEmpty() || t.label.toLowerCase(Locale.ROOT).contains(mQuery)
                              || t.description.toLowerCase(Locale.ROOT).contains(mQuery);
                g.rows.get(t.name).setVisibility(hit ? View.VISIBLE : View.GONE);
                if (hit) matches++;
            }
            boolean searching = !mQuery.isEmpty();
            g.header.view.setVisibility(searching && matches == 0 ? View.GONE : View.VISIBLE);
            g.body.setVisibility(searching ? (matches > 0 ? View.VISIBLE : View.GONE) : g.open ? View.VISIBLE : View.GONE);
            if (mChaoBoosts != null && g.body == mChaoBoosts.getParent()) mChaoBoosts.setVisibility(searching ? View.GONE : View.VISIBLE);
            g.expand.rotate(searching || g.open ? 180 : 0);
            shown += matches;
        }
        mNoMatch.setVisibility(shown == 0 ? View.VISIBLE : View.GONE);
    }

    private void update() {
        mUpdating = true;
        int total = GameSettings.TWEAKS.length, on = 0;
        for (GameSettings.Tweak t : GameSettings.TWEAKS) if (mS.tweaksOn.contains(t.name)) on++;
        mCount.setText(on == 0 ? "All " + total + " tweaks are off. Each changes the game only once you turn it on."
                               : on + " of " + total + " tweaks on. Changes apply the next time the game starts.");
        for (Map.Entry<String, Switch> e : mSwitches.entrySet()) e.getValue().setChecked(mS.tweaksOn.contains(e.getKey()));
        for (Group g : mGroups) {
            int n = 0;
            for (GameSettings.Tweak t : g.group.tweaks) if (mS.tweaksOn.contains(t.name)) n++;
            g.header.supporting.setText(n == 0 ? g.group.tweaks.size() + " tweaks, all off"
                                               : n + " of " + g.group.tweaks.size() + " on");
            g.header.supporting.setVisibility(View.VISIBLE);
        }
        mTurnOff.setEnabled(on > 0);
        mTurnOff.setVisibility(on > 0 ? View.VISIBLE : View.INVISIBLE);
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
