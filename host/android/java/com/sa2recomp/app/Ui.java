package com.sa2recomp.app;

import android.app.Activity;
import android.content.Context;
import android.content.res.ColorStateList;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.ColorFilter;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.PixelFormat;
import android.graphics.Typeface;
import android.graphics.drawable.Drawable;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.RippleDrawable;
import android.graphics.drawable.StateListDrawable;
import android.os.Build;
import android.text.InputType;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Switch;
import android.widget.TextView;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/** The app's screens' look, built in code after Material 3 (dark scheme from a blue seed): its color
 * roles, type scale, pill buttons, top app bar, grouped lists and switches, a column no wider than a
 * phone held upright, and the system bars' insets as padding. */
final class Ui {
    // Color roles (Material 3 dark scheme).
    static final int SURFACE = 0xFF111318, CONTAINER_LOW = 0xFF191C20, CONTAINER = 0xFF1D2024,
            CONTAINER_HIGH = 0xFF282A2F, CONTAINER_HIGHEST = 0xFF33353A;
    static final int ON_SURFACE = 0xFFE2E2E9, ON_SURFACE_VARIANT = 0xFFC4C6D0, OUTLINE = 0xFF8E9099,
            OUTLINE_VARIANT = 0xFF44474F;
    static final int PRIMARY_COLOR = 0xFFADC6FF, ON_PRIMARY = 0xFF102F60, PRIMARY_CONTAINER = 0xFF2B4678,
            ON_PRIMARY_CONTAINER = 0xFFD8E2FF;
    static final int SECONDARY_CONTAINER = 0xFF3E4759, ON_SECONDARY_CONTAINER = 0xFFDAE2F9;
    static final int TERTIARY = 0xFFF2C14E;
    static final int ERROR = 0xFFFFB4AB, ON_ERROR = 0xFF690005, ERROR_CONTAINER = 0xFF93000A,
            ON_ERROR_CONTAINER = 0xFFFFDAD6;
    static final int BACKGROUND = SURFACE;

    // Button kinds: filled, tonal, outlined, filled in the error color, and text.
    static final int PRIMARY = 0, SECONDARY = 1, QUIET = 2, ALERT = 3, TEXT = 4;

    // The type scale: size in sp, and medium weight or not.
    static final int HEADLINE = 0, TITLE_LARGE = 1, TITLE_MEDIUM = 2, TITLE_SMALL = 3, BODY_LARGE = 4,
            BODY_MEDIUM = 5, BODY_SMALL = 6, LABEL_LARGE = 7;
    private static final int[] TYPE_SP = {28, 22, 16, 14, 16, 14, 12, 14};
    private static final boolean[] TYPE_MEDIUM = {false, false, true, true, false, false, false, true};

    private Ui() {}

    static int dp(Context c, float v) {
        return Math.round(v * c.getResources().getDisplayMetrics().density);
    }

    static Typeface medium() {
        return Typeface.create("sans-serif-medium", Typeface.NORMAL);
    }

    static TextView text(Context c, CharSequence s, int type, int color) {
        TextView t = new TextView(c);
        t.setText(s);
        type(t, type);
        t.setTextColor(color);
        return t;
    }

    static void type(TextView t, int type) {
        t.setTextSize(TypedValue.COMPLEX_UNIT_SP, TYPE_SP[type]);
        t.setTypeface(TYPE_MEDIUM[type] ? medium() : Typeface.DEFAULT);
        t.setLineSpacing(0, type >= BODY_LARGE ? 1.15f : 1.0f);
    }

    static Button button(Context c, String text, View.OnClickListener click) {
        Button b = new Button(c);
        b.setText(text);
        b.setAllCaps(false);
        b.setStateListAnimator(null);
        b.setOnClickListener(click);
        return b;
    }

    private static GradientDrawable pill(Context c, int fill, int stroke) {
        GradientDrawable g = new GradientDrawable();
        g.setCornerRadius(dp(c, 100));
        g.setColor(fill);
        if (stroke != 0) g.setStroke(dp(c, 1), stroke);
        return g;
    }

    /** A pill button: PRIMARY (filled, the one main action), SECONDARY (tonal), QUIET (outlined),
     * ALERT (filled in the error color, the report button after a crash) or TEXT. Pressed and disabled
     * states included. */
    static void style(Button b, int kind) {
        Context c = b.getContext();
        int fill = Color.TRANSPARENT, stroke = 0, text;
        switch (kind) {
            case PRIMARY: fill = PRIMARY_COLOR; text = ON_PRIMARY; break;
            case ALERT: fill = ERROR; text = ON_ERROR; break;
            case SECONDARY: fill = SECONDARY_CONTAINER; text = ON_SECONDARY_CONTAINER; break;
            case QUIET: stroke = OUTLINE; text = PRIMARY_COLOR; break;
            default: text = PRIMARY_COLOR;
        }
        StateListDrawable bg = new StateListDrawable();
        boolean filled = kind == PRIMARY || kind == ALERT || kind == SECONDARY;
        bg.addState(new int[] {-android.R.attr.state_enabled},
                    pill(c, filled ? 0x1FE2E2E9 : Color.TRANSPARENT, kind == QUIET ? 0x1FE2E2E9 : 0));
        bg.addState(new int[] {}, new RippleDrawable(ColorStateList.valueOf(0x33FFFFFF), pill(c, fill, stroke),
                                                     pill(c, Color.WHITE, 0)));
        b.setBackground(bg);
        b.setTextColor(new ColorStateList(new int[][] {{-android.R.attr.state_enabled}, {}},
                                          new int[] {0x61E2E2E9, text}));
        b.setTextSize(TypedValue.COMPLEX_UNIT_SP, kind == PRIMARY ? 16 : 14);
        b.setTypeface(medium());
        int h = dp(c, kind == PRIMARY ? 56 : 48);
        b.setMinHeight(h);
        b.setMinimumHeight(h);
        b.setMinWidth(0);
        b.setMinimumWidth(0);
        b.setPadding(dp(c, kind == TEXT ? 12 : 24), 0, dp(c, kind == TEXT ? 12 : 24), 0);
        b.setGravity(Gravity.CENTER);
        b.setCompoundDrawablesRelative(null, null, null, null);
    }

    /** Buttons side by side, sharing the width. */
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

    /** A vertical column no wider than maxWidth, so a tablet or a phone held sideways keeps a readable
     * line length. */
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

    /** The screen: an optional top app bar that stays put, then `col` scrolling under it, centred. */
    static void screen(Activity a, View appBar, Column col) {
        LinearLayout root = new LinearLayout(a);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(SURFACE);
        if (appBar != null) root.addView(appBar, full(0));
        FrameLayout center = new FrameLayout(a);
        center.addView(col, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                                                         ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.CENTER_HORIZONTAL));
        ScrollView scroll = new ScrollView(a);
        scroll.setClipToPadding(false);
        scroll.addView(center, new ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                                                           ViewGroup.LayoutParams.WRAP_CONTENT));
        root.addView(scroll, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));
        padForSystemBars(root);
        a.setContentView(root);
    }

    /** A small top app bar: the back arrow, the title, and an optional action at the end. */
    static LinearLayout topAppBar(Activity a, String title, View action) {
        return topAppBar(a, title, action, v -> a.finish());
    }

    static LinearLayout topAppBar(Activity a, String title, View action, View.OnClickListener onBack) {
        LinearLayout bar = new LinearLayout(a);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        bar.setGravity(Gravity.CENTER_VERTICAL);
        bar.setMinimumHeight(dp(a, 64));
        bar.setPadding(dp(a, 4), 0, dp(a, 8), 0);
        ImageView back = iconButton(a, Icon.BACK, ON_SURFACE, "Back", onBack);
        bar.addView(back, new LinearLayout.LayoutParams(dp(a, 48), dp(a, 48)));
        TextView t = text(a, title, TITLE_LARGE, ON_SURFACE);
        t.setSingleLine(true);
        if (Build.VERSION.SDK_INT >= 28) t.setAccessibilityHeading(true);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
        lp.leftMargin = dp(a, 4);
        bar.addView(t, lp);
        if (action != null) {
            detach(action);
            bar.addView(action);
        }
        return bar;
    }

    static ImageView iconButton(Context c, int icon, int color, String label, View.OnClickListener click) {
        ImageView v = new ImageView(c);
        v.setImageDrawable(new Icon(c, icon, color));
        v.setScaleType(ImageView.ScaleType.CENTER);
        v.setContentDescription(label);
        v.setBackground(new RippleDrawable(ColorStateList.valueOf(0x33FFFFFF), null, null));
        v.setOnClickListener(click);
        return v;
    }

    /** A screen of pages: the root (an overview) and pages opened from it in place, each under a top
     * app bar whose back arrow, like the system's back, returns to the root. */
    static final class Pages {
        private final Activity a;
        private final String rootTitle;
        private final Column root;
        private final View rootAction;
        private final Map<String, Column> pages = new HashMap<>();
        private String current;

        Pages(Activity a, String rootTitle, Column root, View rootAction) {
            this.a = a;
            this.rootTitle = rootTitle;
            this.root = root;
            this.rootAction = rootAction;
        }

        Column add(String title) {
            Column col = new Column(a, dp(a, 640));
            col.setPadding(dp(a, 16), 0, dp(a, 16), dp(a, 40));
            pages.put(title, col);
            return col;
        }

        void show(String title) {
            current = title;
            Column col = title == null ? root : pages.get(title);
            detach(col);
            screen(a, topAppBar(a, title == null ? rootTitle : title, title == null ? rootAction : null, v -> {
                if (!back()) a.finish();
            }), col);
        }

        /** Back to the root from a page; false when the root shows. */
        boolean back() {
            if (current == null) return false;
            show(null);
            return true;
        }
    }

    /** A section's title above its group, in the primary color, read as a heading by TalkBack. */
    static TextView heading(Context c, String title) {
        TextView head = text(c, title, TITLE_SMALL, PRIMARY_COLOR);
        head.setPadding(dp(c, 16), 0, dp(c, 16), 0);
        if (Build.VERSION.SDK_INT >= 28) head.setAccessibilityHeading(true);
        return head;
    }

    private static GradientDrawable rounded(Context c, int color, int radiusDp) {
        GradientDrawable g = new GradientDrawable();
        g.setCornerRadius(dp(c, radiusDp));
        g.setColor(color);
        return g;
    }

    /** A rounded group (a list's container) holding one section's rows, padded for free content. */
    static LinearLayout card(Context c) {
        LinearLayout card = group(c);
        card.setPadding(dp(c, 16), dp(c, 8), dp(c, 16), dp(c, 16));
        return card;
    }

    /** A rounded group whose rows reach its edges (list rows bring their own padding). */
    static LinearLayout group(Context c) {
        LinearLayout g = new LinearLayout(c);
        g.setOrientation(LinearLayout.VERTICAL);
        g.setBackground(rounded(c, CONTAINER, 20));
        g.setClipToOutline(true);
        return g;
    }

    /** A filled card in a tone: CONTAINER_HIGH for news, ERROR_CONTAINER for a problem. */
    static TextView noteCard(Context c, int fill, int textColor) {
        TextView t = text(c, "", BODY_MEDIUM, textColor);
        t.setBackground(rounded(c, fill, 16));
        t.setPadding(dp(c, 16), dp(c, 14), dp(c, 16), dp(c, 14));
        t.setTextIsSelectable(true);
        return t;
    }

    /** A hairline between two rows of a group. */
    static View divider(Context c) {
        View v = new View(c);
        v.setBackgroundColor(OUTLINE_VARIANT);
        v.setMinimumHeight(1);
        return v;
    }

    static LinearLayout.LayoutParams hairline(Context c) {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 1);
        lp.leftMargin = dp(c, 16);
        lp.rightMargin = dp(c, 16);
        return lp;
    }

    /** A switch row: the label at the start and the switch at the end, the whole row one control, so
     * TalkBack reads both. */
    static Switch switchStyle(Context c, String label) {
        Switch w = new Switch(c);
        w.setText(label);
        type(w, BODY_LARGE);
        w.setMinHeight(dp(c, 56));
        w.setMinimumHeight(dp(c, 56));
        w.setSwitchPadding(dp(c, 16));
        int[][] states = {{-android.R.attr.state_enabled}, {android.R.attr.state_checked}, {}};
        w.setThumbTintList(new ColorStateList(states, new int[] {0x61E2E2E9, ON_PRIMARY, OUTLINE}));
        w.setTrackTintList(new ColorStateList(states, new int[] {0x1FE2E2E9, PRIMARY_COLOR, OUTLINE_VARIANT}));
        w.setTrackTintMode(android.graphics.PorterDuff.Mode.SRC);
        w.setTextColor(new ColorStateList(new int[][] {{-android.R.attr.state_enabled}, {}},
                                          new int[] {0x61E2E2E9, ON_SURFACE}));
        return w;
    }

    /** A list row: a headline, optional supporting text under it, and an optional view at the end
     * (a chevron, a switch, a value). Tapping the row runs `click`. */
    static final class ListRow {
        final LinearLayout view;
        final TextView headline, supporting;
        final View end;

        ListRow(Context c, String headline, String supporting, View end, View.OnClickListener click) {
            this(c, null, headline, supporting, end, click);
        }

        ListRow(Context c, View start, String headline, String supporting, View end, View.OnClickListener click) {
            this.end = end;
            view = new LinearLayout(c);
            view.setOrientation(LinearLayout.HORIZONTAL);
            view.setGravity(Gravity.CENTER_VERTICAL);
            view.setMinimumHeight(dp(c, supporting == null ? 56 : 72));
            view.setPadding(dp(c, 16), dp(c, 12), dp(c, 16), dp(c, 12));
            if (click != null) {
                view.setBackground(new RippleDrawable(ColorStateList.valueOf(0x1FFFFFFF), null,
                                                      new android.graphics.drawable.ColorDrawable(Color.WHITE)));
                view.setOnClickListener(click);
            }
            if (start != null) {
                LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(dp(c, 40), dp(c, 40));
                lp.rightMargin = dp(c, 16);
                view.addView(start, lp);
            }
            LinearLayout texts = new LinearLayout(c);
            texts.setOrientation(LinearLayout.VERTICAL);
            this.headline = text(c, headline, BODY_LARGE, ON_SURFACE);
            texts.addView(this.headline);
            this.supporting = text(c, supporting == null ? "" : supporting, BODY_MEDIUM, ON_SURFACE_VARIANT);
            this.supporting.setVisibility(supporting == null ? View.GONE : View.VISIBLE);
            texts.addView(this.supporting, full(dp(c, 2)));
            view.addView(texts, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
            if (end != null) {
                LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
                                                                             ViewGroup.LayoutParams.WRAP_CONTENT);
                lp.leftMargin = dp(c, 16);
                view.addView(end, lp);
            }
        }

        void setEnabled(boolean on) {
            view.setEnabled(on);
            if (end != null) end.setEnabled(on);
            view.setAlpha(on ? 1f : 0.38f);
        }
    }

    /** A full-width button that reads as a list row: its text at the start. */
    static void listButton(Button b) {
        Context c = b.getContext();
        b.setAllCaps(false);
        b.setStateListAnimator(null);
        b.setBackground(new RippleDrawable(ColorStateList.valueOf(0x1FFFFFFF), null, rounded(c, Color.WHITE, 12)));
        b.setTextColor(new ColorStateList(new int[][] {{-android.R.attr.state_enabled}, {}},
                                          new int[] {0x61E2E2E9, ON_SURFACE}));
        type(b, BODY_LARGE);
        b.setGravity(Gravity.START | Gravity.CENTER_VERTICAL);
        b.setMinHeight(dp(c, 56));
        b.setMinimumHeight(dp(c, 56));
        b.setMinWidth(0);
        b.setMinimumWidth(0);
        b.setPadding(0, 0, 0, 0);
        b.setCompoundDrawablesRelative(null, null, null, null);
    }

    /** A list-row button that opens another screen: a chevron at its end. */
    static void navButton(Button b) {
        listButton(b);
        Icon chev = new Icon(b.getContext(), Icon.CHEVRON, ON_SURFACE_VARIANT);
        chev.setBounds(0, 0, chev.getIntrinsicWidth(), chev.getIntrinsicHeight());
        b.setCompoundDrawablesRelative(null, null, chev, null);
        b.setCompoundDrawablePadding(dp(b.getContext(), 16));
    }

    /** A row's leading icon in a tonal circle (40 dp). */
    static View badge(Context c, int icon, int fill, int color) {
        ImageView v = new ImageView(c);
        v.setImageDrawable(new Icon(c, icon, color));
        v.setScaleType(ImageView.ScaleType.CENTER);
        GradientDrawable circle = new GradientDrawable();
        circle.setShape(GradientDrawable.OVAL);
        circle.setColor(fill);
        v.setBackground(circle);
        v.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
        return v;
    }

    static View badge(Context c, int icon) {
        return badge(c, icon, SECONDARY_CONTAINER, ON_SECONDARY_CONTAINER);
    }

    /** A rounded group of rows with a hairline between each two shown rows, inset to where the
     * rows' text starts (72 dp after a leading icon, else 16 dp). */
    static final class Group {
        final LinearLayout view;
        private final Context c;
        private final List<View> rows = new ArrayList<>();
        private final List<View> lines = new ArrayList<>();
        private final int inset;

        Group(Context c, boolean icons) {
            this.c = c;
            view = group(c);
            inset = dp(c, icons ? 72 : 16);
        }

        <T extends View> T add(T row) {
            View line = divider(c);
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 1);
            lp.leftMargin = inset;
            if (!rows.isEmpty()) view.addView(line, lp);
            lines.add(line);
            rows.add(row);
            view.addView(row, full(0));
            return row;
        }

        /** Shows or hides a row, and keeps a hairline only between rows that show. */
        void show(View row, boolean shown) {
            row.setVisibility(shown ? View.VISIBLE : View.GONE);
            boolean above = false;
            for (int i = 0; i < rows.size(); i++) {
                boolean on = rows.get(i).getVisibility() == View.VISIBLE;
                lines.get(i).setVisibility(on && above ? View.VISIBLE : View.GONE);
                above |= on;
            }
        }
    }

    /** Small print under a group: the longer explanation that does not belong in a row. */
    static TextView footnote(Context c, String s) {
        TextView t = text(c, s, BODY_SMALL, ON_SURFACE_VARIANT);
        t.setPadding(dp(c, 16), 0, dp(c, 16), 0);
        return t;
    }

    /** A slider row: its name and value on one line, the slider under them. */
    static final class SliderRow {
        final LinearLayout view;
        final TextView title, value;
        final android.widget.SeekBar bar;

        SliderRow(Context c, String name) {
            view = new LinearLayout(c);
            view.setOrientation(LinearLayout.VERTICAL);
            view.setPadding(dp(c, 16), dp(c, 14), dp(c, 16), dp(c, 6));
            LinearLayout top = new LinearLayout(c);
            top.setOrientation(LinearLayout.HORIZONTAL);
            top.setGravity(Gravity.CENTER_VERTICAL);
            title = text(c, name, BODY_LARGE, ON_SURFACE);
            top.addView(title, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
            value = text(c, "", LABEL_LARGE, PRIMARY_COLOR);
            top.addView(value);
            view.addView(top, full(0));
            bar = new android.widget.SeekBar(c);
            bar.setId(View.generateViewId());
            title.setLabelFor(bar.getId());
            bar.setProgressTintList(ColorStateList.valueOf(PRIMARY_COLOR));
            bar.setProgressBackgroundTintList(ColorStateList.valueOf(SECONDARY_CONTAINER));
            bar.setThumbTintList(ColorStateList.valueOf(PRIMARY_COLOR));
            bar.setPadding(dp(c, 4), dp(c, 12), dp(c, 4), dp(c, 12));
            view.addView(bar, full(dp(c, 4)));
        }
    }

    static ImageView chevron(Context c) {
        ImageView v = new ImageView(c);
        v.setImageDrawable(new Icon(c, Icon.CHEVRON, ON_SURFACE_VARIANT));
        v.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
        return v;
    }

    /** A search field: a rounded bar with a magnifier, the text field and a clear button. */
    static final class SearchField {
        final LinearLayout view;
        final EditText edit;

        SearchField(Context c, String hint) {
            view = new LinearLayout(c);
            view.setOrientation(LinearLayout.HORIZONTAL);
            view.setGravity(Gravity.CENTER_VERTICAL);
            view.setBackground(rounded(c, CONTAINER_HIGH, 28));
            view.setMinimumHeight(dp(c, 56));
            view.setPadding(dp(c, 16), 0, dp(c, 4), 0);
            ImageView mag = new ImageView(c);
            mag.setImageDrawable(new Icon(c, Icon.SEARCH, ON_SURFACE_VARIANT));
            mag.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
            view.addView(mag);
            edit = new EditText(c);
            edit.setHint(hint);
            edit.setHintTextColor(ON_SURFACE_VARIANT);
            edit.setTextColor(ON_SURFACE);
            type(edit, BODY_LARGE);
            edit.setBackground(null);
            edit.setSingleLine(true);
            edit.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS);
            edit.setImeOptions(android.view.inputmethod.EditorInfo.IME_ACTION_SEARCH);
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
            lp.leftMargin = dp(c, 12);
            view.addView(edit, lp);
            ImageView clear = iconButton(c, Icon.CLOSE, ON_SURFACE_VARIANT, "Clear search", v -> edit.setText(""));
            view.addView(clear, new LinearLayout.LayoutParams(dp(c, 48), dp(c, 48)));
        }
    }

    /** Line icons on a 24-unit grid, drawn here so the app needs no icon files. */
    static final class Icon extends Drawable {
        static final int BACK = 0, CHEVRON = 1, EXPAND = 2, SEARCH = 3, CLOSE = 4, TUNE = 5, DISPLAY = 6, TOUCH = 7,
                GAMEPAD = 8, SOUND = 9, SAVE = 10, CODE = 11, PLAY = 12, RESET = 13, GEAR = 14, ARCHIVE = 15,
                FOLDER = 16, FLAG = 17, COPY = 18, SHARE = 19, DOWNLOAD = 20, BAG = 21, STAR = 22, BOOK = 23,
                EGG = 24, CHECK = 25, DOTS = 26, LIST = 27;
        private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final int kind, size;
        private float rotation;

        Icon(Context c, int kind, int color) {
            this.kind = kind;
            this.size = dp(c, 24);
            paint.setColor(color);
            paint.setStyle(Paint.Style.STROKE);
            paint.setStrokeCap(Paint.Cap.ROUND);
            paint.setStrokeJoin(Paint.Join.ROUND);
            paint.setStrokeWidth(dp(c, 2));
        }

        /** EXPAND turned by `degrees` (180 when its group is open). */
        void rotate(float degrees) {
            rotation = degrees;
            invalidateSelf();
        }

        @Override
        public void draw(Canvas canvas) {
            float s = getBounds().width() / 24f, ox = getBounds().left, oy = getBounds().top;
            canvas.save();
            canvas.translate(ox, oy);
            canvas.rotate(rotation, 12 * s, 12 * s);
            Path p = new Path();
            switch (kind) {
                case BACK:
                    p.moveTo(19 * s, 12 * s); p.lineTo(5 * s, 12 * s);
                    p.moveTo(11 * s, 6 * s); p.lineTo(5 * s, 12 * s); p.lineTo(11 * s, 18 * s);
                    break;
                case CHEVRON:
                    p.moveTo(9.5f * s, 6 * s); p.lineTo(15.5f * s, 12 * s); p.lineTo(9.5f * s, 18 * s);
                    break;
                case EXPAND:
                    p.moveTo(6 * s, 9.5f * s); p.lineTo(12 * s, 15.5f * s); p.lineTo(18 * s, 9.5f * s);
                    break;
                case SEARCH:
                    p.addCircle(10.5f * s, 10.5f * s, 6 * s, Path.Direction.CW);
                    p.moveTo(15 * s, 15 * s); p.lineTo(20 * s, 20 * s);
                    break;
                case CLOSE:
                    p.moveTo(6.5f * s, 6.5f * s); p.lineTo(17.5f * s, 17.5f * s);
                    p.moveTo(17.5f * s, 6.5f * s); p.lineTo(6.5f * s, 17.5f * s);
                    break;
                case TUNE:
                    p.moveTo(4 * s, 7 * s); p.lineTo(20 * s, 7 * s);
                    p.moveTo(4 * s, 12 * s); p.lineTo(20 * s, 12 * s);
                    p.moveTo(4 * s, 17 * s); p.lineTo(20 * s, 17 * s);
                    p.addCircle(15 * s, 7 * s, 2 * s, Path.Direction.CW);
                    p.addCircle(9 * s, 12 * s, 2 * s, Path.Direction.CW);
                    p.addCircle(13 * s, 17 * s, 2 * s, Path.Direction.CW);
                    break;
                case DISPLAY:
                    p.addRoundRect(3.5f * s, 5 * s, 20.5f * s, 16 * s, 1.5f * s, 1.5f * s, Path.Direction.CW);
                    p.moveTo(12 * s, 16 * s); p.lineTo(12 * s, 19.5f * s);
                    p.moveTo(8.5f * s, 19.5f * s); p.lineTo(15.5f * s, 19.5f * s);
                    break;
                case TOUCH:
                    p.addCircle(12 * s, 12 * s, 2.5f * s, Path.Direction.CW);
                    p.addCircle(12 * s, 12 * s, 7 * s, Path.Direction.CW);
                    break;
                case GAMEPAD:
                    p.addRoundRect(2.5f * s, 7 * s, 21.5f * s, 17 * s, 5 * s, 5 * s, Path.Direction.CW);
                    p.moveTo(6 * s, 12 * s); p.lineTo(10 * s, 12 * s);
                    p.moveTo(8 * s, 10 * s); p.lineTo(8 * s, 14 * s);
                    p.addCircle(15.5f * s, 11 * s, 0.6f * s, Path.Direction.CW);
                    p.addCircle(17.5f * s, 13 * s, 0.6f * s, Path.Direction.CW);
                    break;
                case SOUND:
                    p.moveTo(4 * s, 9.5f * s); p.lineTo(7.5f * s, 9.5f * s); p.lineTo(12 * s, 5.5f * s);
                    p.lineTo(12 * s, 18.5f * s); p.lineTo(7.5f * s, 14.5f * s); p.lineTo(4 * s, 14.5f * s); p.close();
                    p.addArc(11 * s, 8.5f * s, 18 * s, 15.5f * s, -50, 100);
                    p.addArc(10 * s, 5 * s, 21 * s, 19 * s, -50, 100);
                    break;
                case SAVE:
                    p.addRoundRect(4 * s, 4 * s, 20 * s, 20 * s, 2 * s, 2 * s, Path.Direction.CW);
                    p.moveTo(8 * s, 4 * s); p.lineTo(8 * s, 9 * s); p.lineTo(15 * s, 9 * s); p.lineTo(15 * s, 4 * s);
                    p.addCircle(12 * s, 14.5f * s, 2.5f * s, Path.Direction.CW);
                    break;
                case CODE:
                    p.moveTo(9 * s, 7 * s); p.lineTo(4 * s, 12 * s); p.lineTo(9 * s, 17 * s);
                    p.moveTo(15 * s, 7 * s); p.lineTo(20 * s, 12 * s); p.lineTo(15 * s, 17 * s);
                    break;
                case PLAY:
                    p.moveTo(9 * s, 6.5f * s); p.lineTo(17.5f * s, 12 * s); p.lineTo(9 * s, 17.5f * s); p.close();
                    break;
                case GEAR:
                    p.addCircle(12 * s, 12 * s, 3 * s, Path.Direction.CW);
                    p.addCircle(12 * s, 12 * s, 6.5f * s, Path.Direction.CW);
                    for (int i = 0; i < 8; i++) {
                        double r = Math.PI / 4 * i;
                        p.moveTo((float) (12 + 6.5 * Math.cos(r)) * s, (float) (12 + 6.5 * Math.sin(r)) * s);
                        p.lineTo((float) (12 + 9 * Math.cos(r)) * s, (float) (12 + 9 * Math.sin(r)) * s);
                    }
                    break;
                case ARCHIVE:
                    p.addRoundRect(5 * s, 3.5f * s, 19 * s, 20.5f * s, 2 * s, 2 * s, Path.Direction.CW);
                    for (int i = 0; i < 4; i++) {
                        p.moveTo(11 * s, (5.5f + 2 * i) * s); p.lineTo(13 * s, (5.5f + 2 * i) * s);
                    }
                    p.addRoundRect(10.5f * s, 14 * s, 13.5f * s, 17.5f * s, 0.8f * s, 0.8f * s, Path.Direction.CW);
                    break;
                case FOLDER:
                    p.moveTo(3.5f * s, 7 * s); p.lineTo(3.5f * s, 18.5f * s); p.lineTo(20.5f * s, 18.5f * s);
                    p.lineTo(20.5f * s, 8.5f * s); p.lineTo(11.5f * s, 8.5f * s); p.lineTo(9.5f * s, 5.5f * s);
                    p.lineTo(3.5f * s, 5.5f * s); p.close();
                    break;
                case FLAG:
                    p.moveTo(5.5f * s, 21 * s); p.lineTo(5.5f * s, 4 * s);
                    p.lineTo(18.5f * s, 4 * s); p.lineTo(15.5f * s, 8.5f * s); p.lineTo(18.5f * s, 13 * s);
                    p.lineTo(5.5f * s, 13 * s);
                    break;
                case COPY:
                    p.addRoundRect(8.5f * s, 8.5f * s, 19.5f * s, 20.5f * s, 2 * s, 2 * s, Path.Direction.CW);
                    p.moveTo(15.5f * s, 5.5f * s); p.lineTo(15.5f * s, 4.5f * s); p.lineTo(4.5f * s, 4.5f * s);
                    p.lineTo(4.5f * s, 16.5f * s); p.lineTo(5.5f * s, 16.5f * s);
                    break;
                case SHARE:
                    p.addCircle(17.5f * s, 6 * s, 2.5f * s, Path.Direction.CW);
                    p.addCircle(6.5f * s, 12 * s, 2.5f * s, Path.Direction.CW);
                    p.addCircle(17.5f * s, 18 * s, 2.5f * s, Path.Direction.CW);
                    p.moveTo(8.7f * s, 10.8f * s); p.lineTo(15.3f * s, 7.2f * s);
                    p.moveTo(8.7f * s, 13.2f * s); p.lineTo(15.3f * s, 16.8f * s);
                    break;
                case DOWNLOAD:
                    p.moveTo(12 * s, 4 * s); p.lineTo(12 * s, 15 * s);
                    p.moveTo(7.5f * s, 10.5f * s); p.lineTo(12 * s, 15 * s); p.lineTo(16.5f * s, 10.5f * s);
                    p.moveTo(5 * s, 19.5f * s); p.lineTo(19 * s, 19.5f * s);
                    break;
                case BAG:
                    p.addRoundRect(4.5f * s, 8 * s, 19.5f * s, 20.5f * s, 2 * s, 2 * s, Path.Direction.CW);
                    p.addArc(8.5f * s, 3.5f * s, 15.5f * s, 12.5f * s, 180, 180);
                    break;
                case STAR:
                    for (int i = 0; i < 10; i++) {
                        double r = -Math.PI / 2 + Math.PI / 5 * i, d = i % 2 == 0 ? 8.5 : 3.6;
                        float x = (float) (12 + d * Math.cos(r)) * s, y = (float) (12.8 + d * Math.sin(r)) * s;
                        if (i == 0) p.moveTo(x, y);
                        else p.lineTo(x, y);
                    }
                    p.close();
                    break;
                case BOOK:
                    p.moveTo(12 * s, 6.5f * s); p.lineTo(12 * s, 19.5f * s);
                    p.moveTo(12 * s, 6.5f * s); p.cubicTo(9.5f * s, 4.5f * s, 6 * s, 4.5f * s, 3.5f * s, 5.5f * s);
                    p.lineTo(3.5f * s, 18.5f * s); p.cubicTo(6 * s, 17.5f * s, 9.5f * s, 17.5f * s, 12 * s, 19.5f * s);
                    p.cubicTo(14.5f * s, 17.5f * s, 18 * s, 17.5f * s, 20.5f * s, 18.5f * s); p.lineTo(20.5f * s, 5.5f * s);
                    p.cubicTo(18 * s, 4.5f * s, 14.5f * s, 4.5f * s, 12 * s, 6.5f * s);
                    break;
                case EGG:
                    p.moveTo(12 * s, 3.5f * s);
                    p.cubicTo(16.5f * s, 3.5f * s, 19 * s, 10.5f * s, 19 * s, 14 * s);
                    p.cubicTo(19 * s, 18 * s, 16 * s, 20.5f * s, 12 * s, 20.5f * s);
                    p.cubicTo(8 * s, 20.5f * s, 5 * s, 18 * s, 5 * s, 14 * s);
                    p.cubicTo(5 * s, 10.5f * s, 7.5f * s, 3.5f * s, 12 * s, 3.5f * s);
                    break;
                case CHECK:
                    p.addCircle(12 * s, 12 * s, 8.5f * s, Path.Direction.CW);
                    p.moveTo(8 * s, 12.2f * s); p.lineTo(10.8f * s, 15 * s); p.lineTo(16 * s, 9.5f * s);
                    break;
                case DOTS:
                    p.addCircle(6 * s, 12 * s, 1.2f * s, Path.Direction.CW);
                    p.addCircle(12 * s, 12 * s, 1.2f * s, Path.Direction.CW);
                    p.addCircle(18 * s, 12 * s, 1.2f * s, Path.Direction.CW);
                    break;
                case LIST:
                    p.moveTo(9 * s, 7 * s); p.lineTo(20 * s, 7 * s);
                    p.moveTo(9 * s, 12 * s); p.lineTo(20 * s, 12 * s);
                    p.moveTo(9 * s, 17 * s); p.lineTo(20 * s, 17 * s);
                    p.addCircle(5 * s, 7 * s, 0.8f * s, Path.Direction.CW);
                    p.addCircle(5 * s, 12 * s, 0.8f * s, Path.Direction.CW);
                    p.addCircle(5 * s, 17 * s, 0.8f * s, Path.Direction.CW);
                    break;
                default:
                    p.addArc(5 * s, 5 * s, 19 * s, 19 * s, -60, 300);
                    p.moveTo(15.5f * s, 3.5f * s); p.lineTo(15.5f * s, 6.5f * s); p.lineTo(18.5f * s, 6.5f * s);
            }
            canvas.drawPath(p, paint);
            canvas.restore();
        }

        @Override public int getIntrinsicWidth() { return size; }
        @Override public int getIntrinsicHeight() { return size; }
        @Override public void setAlpha(int a) { paint.setAlpha(a); }
        @Override public void setColorFilter(ColorFilter f) { paint.setColorFilter(f); }
        @Override public int getOpacity() { return PixelFormat.TRANSLUCENT; }
    }
}
