package com.sa2recomp.app;

import android.content.Context;
import android.content.res.ColorStateList;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.RippleDrawable;
import android.graphics.drawable.StateListDrawable;
import android.os.Build;
import android.util.TypedValue;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.Switch;
import android.widget.TextView;

/** The setup and settings screens' look, built in code: rounded buttons, rows of them, a column
 * no wider than a phone held upright, and the system bars' insets as padding. */
final class Ui {
    static final int PRIMARY = 0, SECONDARY = 1, QUIET = 2, ALERT = 3;
    static final int BACKGROUND = Color.rgb(0x10, 0x18, 0x30);

    private Ui() {}

    static int dp(Context c, float v) {
        return Math.round(v * c.getResources().getDisplayMetrics().density);
    }

    static Button button(Context c, String text, View.OnClickListener click) {
        Button b = new Button(c);
        b.setText(text);
        b.setAllCaps(false);
        b.setStateListAnimator(null);
        b.setOnClickListener(click);
        return b;
    }

    /** A rounded button: PRIMARY (the one main action), SECONDARY, QUIET (outlined), or ALERT (the
     * report button after a crash). Pressed and disabled states included. */
    static void style(Button b, int kind) {
        Context c = b.getContext();
        int fill, stroke = 0, text;
        switch (kind) {
            case PRIMARY: fill = Color.rgb(0x2B, 0x6B, 0xE8); text = Color.WHITE; break;
            case ALERT: fill = Color.rgb(0xD9, 0x4F, 0x4F); text = Color.WHITE; break;
            case SECONDARY: fill = Color.rgb(0x24, 0x30, 0x4D); text = Color.rgb(0xE8, 0xED, 0xF7); break;
            default: fill = Color.TRANSPARENT; stroke = Color.rgb(0x3A, 0x48, 0x66); text = Color.rgb(0xB8, 0xC4, 0xDA);
        }
        GradientDrawable shape = new GradientDrawable();
        shape.setCornerRadius(dp(c, 12));
        shape.setColor(fill);
        if (stroke != 0) shape.setStroke(dp(c, 1), stroke);
        GradientDrawable off = new GradientDrawable();
        off.setCornerRadius(dp(c, 12));
        off.setColor(kind == QUIET ? Color.TRANSPARENT : Color.rgb(0x1A, 0x23, 0x40));
        if (kind == QUIET) off.setStroke(dp(c, 1), Color.rgb(0x26, 0x30, 0x48));
        StateListDrawable bg = new StateListDrawable();
        bg.addState(new int[] {-android.R.attr.state_enabled}, off);
        bg.addState(new int[] {}, new RippleDrawable(ColorStateList.valueOf(0x40FFFFFF), shape, null));
        b.setBackground(bg);
        b.setTextColor(new ColorStateList(new int[][] {{-android.R.attr.state_enabled}, {}},
                                          new int[] {Color.rgb(0x5D, 0x6A, 0x85), text}));
        b.setTextSize(TypedValue.COMPLEX_UNIT_SP, kind == PRIMARY ? 18 : kind == QUIET ? 14 : 15);
        b.setTypeface(kind == PRIMARY || kind == ALERT ? Typeface.DEFAULT_BOLD : Typeface.DEFAULT);
        int h = dp(c, kind == PRIMARY ? 56 : kind == QUIET ? 44 : 48);
        b.setMinHeight(h);
        b.setMinimumHeight(h);
        b.setPadding(dp(c, kind == QUIET ? 6 : 12), 0, dp(c, kind == QUIET ? 6 : 12), 0);
    }

    /** Buttons side by side at equal widths, 8 dp apart. */
    static LinearLayout row(Context c, Button... buttons) {
        LinearLayout row = new LinearLayout(c);
        row.setOrientation(LinearLayout.HORIZONTAL);
        for (int i = 0; i < buttons.length; i++) {
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
            lp.leftMargin = i == 0 ? 0 : dp(c, 4);
            lp.rightMargin = i == buttons.length - 1 ? 0 : dp(c, 4);
            detach(buttons[i]);
            row.addView(buttons[i], lp);
        }
        return row;
    }

    static LinearLayout.LayoutParams full(int topMargin) {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                                                                     ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.topMargin = topMargin;
        return lp;
    }

    static void detach(View v) {
        if (v.getParent() instanceof ViewGroup) ((ViewGroup) v.getParent()).removeView(v);
    }

    /** A column no wider than maxWidth, so the buttons do not stretch across a landscape screen. */
    static final class Column extends LinearLayout {
        private final int maxWidth;

        Column(Context c, int maxWidth) {
            super(c);
            this.maxWidth = maxWidth;
            setOrientation(VERTICAL);
        }

        @Override
        protected void onMeasure(int widthSpec, int heightSpec) {
            if (MeasureSpec.getSize(widthSpec) > maxWidth) {
                widthSpec = MeasureSpec.makeMeasureSpec(maxWidth, MeasureSpec.EXACTLY);
            }
            super.onMeasure(widthSpec, heightSpec);
        }
    }

    /** Android 15 draws apps edge to edge whatever fitsSystemWindows says: the system bars' insets
     * become `v`'s padding, so scrolled content stays clear of the status bar and the gesture bar. */
    @SuppressWarnings("deprecation")
    static void padForSystemBars(View v) {
        v.setOnApplyWindowInsetsListener((view, insets) -> {
            int l, t, r, b;
            if (Build.VERSION.SDK_INT >= 30) {
                android.graphics.Insets i = insets.getInsets(android.view.WindowInsets.Type.systemBars()
                                                             | android.view.WindowInsets.Type.displayCutout());
                l = i.left; t = i.top; r = i.right; b = i.bottom;
            } else {
                l = insets.getSystemWindowInsetLeft(); t = insets.getSystemWindowInsetTop();
                r = insets.getSystemWindowInsetRight(); b = insets.getSystemWindowInsetBottom();
            }
            view.setPadding(l, t, r, b);
            return insets;
        });
    }
    /** A section's title in the accent colour, read as a heading by TalkBack. */
    static TextView heading(Context c, String title) {
        TextView head = new TextView(c);
        head.setText(title);
        head.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13);
        head.setTextColor(Color.rgb(0x8C, 0xB6, 0xFF));
        head.setTypeface(Typeface.DEFAULT_BOLD);
        head.setAllCaps(true);
        head.setLetterSpacing(0.08f);
        if (Build.VERSION.SDK_INT >= 28) head.setAccessibilityHeading(true);
        return head;
    }

    /** A rounded card that holds one section's rows. */
    static LinearLayout card(Context c) {
        LinearLayout card = new LinearLayout(c);
        card.setOrientation(LinearLayout.VERTICAL);
        GradientDrawable bg = new GradientDrawable();
        bg.setCornerRadius(dp(c, 14));
        bg.setColor(Color.rgb(0x18, 0x22, 0x3D));
        bg.setStroke(dp(c, 1), Color.rgb(0x26, 0x30, 0x48));
        card.setBackground(bg);
        card.setPadding(dp(c, 16), dp(c, 6), dp(c, 16), dp(c, 14));
        return card;
    }

    /** A hairline between two rows of a card. */
    static View divider(Context c) {
        View v = new View(c);
        v.setBackgroundColor(Color.rgb(0x26, 0x30, 0x48));
        return v;
    }

    /** A switch in the app's blue; its label is part of the switch, so TalkBack reads both. */
    static Switch switchStyle(Context c, String label) {
        Switch w = new Switch(c);
        w.setText(label);
        w.setTextSize(TypedValue.COMPLEX_UNIT_SP, 16);
        w.setTypeface(Typeface.DEFAULT_BOLD);
        int[][] states = {{-android.R.attr.state_enabled}, {android.R.attr.state_checked}, {}};
        w.setThumbTintList(new ColorStateList(states, new int[] {Color.rgb(0x3A, 0x48, 0x66),
                                              Color.rgb(0x2B, 0x6B, 0xE8), Color.rgb(0xB8, 0xC4, 0xDA)}));
        w.setTrackTintList(new ColorStateList(states, new int[] {Color.rgb(0x24, 0x30, 0x4D),
                                              Color.rgb(0x6F, 0x9C, 0xF0), Color.rgb(0x3A, 0x48, 0x66)}));
        w.setTextColor(new ColorStateList(new int[][] {{-android.R.attr.state_enabled}, {}},
                                          new int[] {Color.rgb(0x5D, 0x6A, 0x85), Color.rgb(0xE8, 0xED, 0xF7)}));
        return w;
    }
}
