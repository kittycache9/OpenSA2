package com.sa2recomp.app;

import android.app.Activity;
import android.content.Context;
import android.content.SharedPreferences;
import android.content.pm.ActivityInfo;
import android.graphics.Point;
import android.graphics.Rect;
import android.os.Build;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/**
 * The game settings the settings screen (Sa2SettingsActivity) keeps in the app's own preferences
 * ("game_settings", which an app update keeps), and what they ask of a session: the orientation
 * Sa2Activity asks the system for and passes as --orientation, the screen size it passes as
 * --screen, and the SA2_ settings it passes as --setting (host/android/native/sa2_android_session.cpp:
 * after the session's defaults, before <external files>/game.env.extra, whose lines still win).
 * Every setting at its default asks for nothing, so such a session runs as it did before there
 * were settings.
 *
 * The picture. Landscape with widescreen on draws the game at the chosen aspect (the screen's own
 * by default, never narrower than 4:3) and resolution (the largest picture of that aspect the
 * screen holds, or fewer lines); widescreen off keeps the game's own size from its config
 * (SA2_PLAY_RES=off). Portrait and auto-rotate always draw 4:3: the game's menus are laid out for
 * 4:3, and a wider picture across an upright phone would be a thin strip. Portrait sizes it across
 * the screen's width; auto-rotate sizes it for landscape, so it is scaled down when the phone is
 * upright. Upright, the picture sits where the picture position puts it (its centre a quarter of the
 * way down by default, the middle of the screen's upper half), so the touch controls below it get
 * the rest of the screen.
 *
 * The tweaks. Each is a change to the game's own behaviour, or the host's, off by default, sent as
 * one name in SA2_TWEAKS (runtime/sa2_tweaks.h, which lists them all: SA2_TWEAK_LIST the game's,
 * SA2_TWEAK_HOST_LIST the host's; the game and the player ignore the setting in a replay). TWEAKS
 * holds one line for each, a game tweak or a host tweak whose home is the player or this app, and
 * a switch is a switch either way: the settings screen's Tweaks card makes one of every line, kept
 * in the preferences as "tweak_<name>", and sessionSettings sends the names of those turned on, in
 * TWEAKS's order, to the game and the player alike. The card shows them under TWEAK_GROUPS's
 * headings, each group in TWEAK_GROUPS's own order (tweakGroups), not in TWEAKS's order. A host tweak whose home
 * is this app has its helper, Tweak<Name>.java, which asks tweaksOn. A new tweak is one line in
 * TWEAKS and its name in one group of TWEAK_GROUPS (runtime/sa2_tweaks.h, "Add a tweak", "Add a host
 * tweak").
 */
final class GameSettings {
    static final String PREFS = "game_settings";
    static final String LANDSCAPE = "landscape", PORTRAIT = "portrait", AUTO = "auto";
    /** Resolution choices in lines, 0 the largest the screen holds. */
    static final int[] RESOLUTIONS = {0, 1440, 1080, 900, 720, 540, 480};
    static final String[] ASPECTS = {"auto", "21:9", "16:9", "4:3"};
    /** The low-FPS speed fix's choices, as stored (runtime/sa2_frameskip.c): off, 30 or 20 drawn frames
     * a second in stages (2 or 3 game ticks per drawn frame), or auto (1 to 3, chosen as the game runs). */
    static final String[] FRAMESKIPS = {"off", "30", "20", "auto"};
    /** The touch overlay's on-screen buttons (host/player/touch_overlay.h SA2_TOUCH_MAP), and a pad's
     * buttons by position (host/player/player.cpp SA2_PAD_MAP), as the settings name them. */
    static final String[] TOUCH_BUTTONS = {"A", "B", "X", "Y", "START", "LT", "RT"};
    static final String[] PAD_BUTTONS = {"SOUTH", "EAST", "WEST", "NORTH", "LB", "RB", "BACK", "START", "LS", "RS",
                                         "UP", "DOWN", "LEFT", "RIGHT"};
    static final String[] PAD_DEFAULTS = {"A", "B", "X", "Y", "LB", "RB", "BACK", "START", "LS", "RS",
                                          "UP", "DOWN", "LEFT", "RIGHT"};
    /** What a button can send (host/player/input.h pad_output_named). */
    static final String[] OUTPUTS = {"A", "B", "X", "Y", "LB", "RB", "LT", "RT", "START", "BACK", "LS", "RS",
                                     "UP", "DOWN", "LEFT", "RIGHT", "NONE"};
    /** The game's own size when the session leaves its config alone (the installer's
     * Config/UserConfig.cfg: host/install/sa2_config.cpp). */
    static final int CONFIG_W = 1280, CONFIG_H = 720;

    /** One tweak the settings screen offers: its SA2_TWEAKS name (runtime/sa2_tweaks.h
     * SA2_TWEAK_LIST), the switch's label, and the note under the switch. */
    static final class Tweak {
        final String name, label, description;

        Tweak(String name, String label, String description) {
            this.name = name;
            this.label = label;
            this.description = description;
        }
    }

    /** Every tweak, one line each, game and host alike (but a host tweak of the desktop launcher's), in
     * name order, the order of runtime/sa2_tweaks.h's two lists together. SA2_TWEAKS names them in
     * this order; the Tweaks card shows them by TWEAK_GROUPS instead. */
    static final Tweak[] TWEAKS = {
        new Tweak("2p-layout-in-1p", "Two-player stage layouts", "13 one-player stages use their leftover two-player object layout: other rings, items and enemies. Hard Mode, Pumpkin Hill and Iron Gate keep their own. These layouts leave out most upgrades and Chao key boxes, and some switches and animals, so a mission may not be finishable. Clears, ranks and emblems still save; an upgrade the layout leaves out cannot be collected or saved."),
        new Tweak("action-buttons", "Light dash on Y, spin dash on X", "Y does the light dash along a line of rings, so B or X there somersaults instead (the button prompt on screen still shows the usual button). Holding X starts Sonic's or Shadow's spin dash at once, with no charge-up or somersault; B keeps the usual hold, where a tap somersaults. Nothing is saved differently."),
        new Tweak("all-events-skippable", "Skip any cutscene", "Start skips every cutscene, including the Hero story ending and the Last story finale, which the game otherwise makes you watch."),
        new Tweak("all-upgrades", "All upgrades", "Your character has every upgrade from the start of each level, as with the Have All Upgrades cheat, so upgrade-only paths are open at once. One-player only; Amy, Metal Sonic, Tikal, Chaos and the Chao Walkers are left as they are, and in the Sonic vs Shadow fights each hedgehog gets only his own upgrades, so neither wears the other's. The upgrades it gives are not saved, but anything you collect or clear with them (upgrades you pick up, Lost Chao, times, ranks and emblems) is."),
        new Tweak("always-recap", "Story recap before every stage", "Plays the story-so-far recap before each story stage, not only when you continue a story from story select. A, B or Start skips it."),
        new Tweak("analog-fix", "Analog stick fix", "Your speed builds up over more of the stick's push: full speed comes at about two thirds of a push instead of before half, so half a push walks and a full push still runs."),
        new Tweak("camera-invert", "Invert camera", "Turns the camera the other way: the right stick's left and right swap, and so do the LT and RT triggers, on screens other than levels too."),
        new Tweak("chao-key-always", "Chao Key always held", "After every stage you clear in one-player play, you go to Chao World as if you had found the Chao Key. Your save may record the key as found."),
        new Tweak("chao-stat-x5", "Chao stats x5", "Chao gain up to five times the stat points from Chaos Drives, small animals and fruit. An animal or drive gives at most 100 points to a stat, so a stat never loses points. The extra points are saved with your Chao and stay when you turn this off."),
        new Tweak("character-select", "Pick the character in Stage Select", "In Stage Select, press Y on a stage to pick who plays it from the six character icons; then pick the mission as usual. Only characters of the same kind as the stage can be picked: Sonic and Shadow in speed stages, Knuckles and Rouge in treasure hunts, Tails and Eggman in walker stages, and a few stages leave out one of the pair. The game has no start points for the rest. Ranks, emblems and scores save under the stage as usual."),
        new Tweak("deadzone", "Round stick deadzone", "Gives a gamepad's left stick a round deadzone, so a worn stick rests still, and full tilt a little before its edge. The touch controls are not changed."),
        new Tweak("draw-distance", "Double draw distance", "Rings, enemies and other objects appear from twice as far away, so less pops in. Slower phones may run less smoothly."),
        new Tweak("drives-to-minimals", "Chaos Drives become animals", "Destroyed enemies leave a small animal instead of a Chaos Drive. Changes what you can collect for your Chao."),
        new Tweak("event-bars-all", "Black bars on every cutscene", "Draws black bars at the sides of every cutscene on a wide picture, so each one looks as it does on a 4:3 screen. Movies are unchanged. Not together with Remove cutscene black bars: with both on, neither applies."),
        new Tweak("fast-stage-load", "Faster stage loading", "Starts a stage as soon as it has loaded, without waiting for the loading screen to finish. Chao World loads as usual."),
        new Tweak("force-hard-layout", "Hard Mode layouts", "Every mission uses the stage's Hard Mode (mission 5) layout of rings, items and enemies. The mission and its goal stay the one you picked, and clears and ranks save as that mission. A stage whose Hard Mode layout has no upgrade keeps its own until you have that upgrade. Missions 2 to 4 may be harder to finish."),
        new Tweak("forgiving-restarts", "Forgiving restarts", "Restarting from the pause menu keeps your rings and costs no life, and a miss keeps your score. Rings kept through a restart still give extra lives, and your save keeps the lives you have and the rings you finish with. Scores, ranks and missions you finish with it on are saved as usual."),
        new Tweak("fov", "Wider field of view", "Widens the camera's view in levels by a quarter, so you see more around your character. The HUD, cutscenes and menus are unchanged."),
        new Tweak("frametime-overlay", "Frame-time overlay", "Shows a small box at the top left with the frame rate, the frame time and the frames shown late."),
        new Tweak("garden-ring-trickle", "Rings over time in Chao World", "Adds one ring to your ring total every second while you are in Chao World, up to 9,999,999. The rings are kept in your save."),
        new Tweak("gold-beetle-stays", "Gold Beetles stay", "A Gold Beetle no longer flies away after a moment; it stays until you destroy it. The extra points count in your saved scores and ranks."),
        new Tweak("hide-hud", "Hide the HUD", "Hides the score, time, rings and lives counters, and the kart race display. Button prompts and the radar stay."),
        new Tweak("hide-radar", "Hide the emerald radar", "Treasure hunts (Knuckles and Rouge) show no emerald radar and no \"!\" over a piece, and play no radar beep, so you find the pieces by eye and with the hint monitors."),
        new Tweak("hide-upgrade-models", "Hide upgrade models", "Upgrades like the Bounce Bracelet and Shovel Claws are not shown on the characters. Walkers keep their guns, because shots fire from them. Upgrades still work, and cutscenes still show them."),
        new Tweak("hold-toggle", "Tap to hold B", "A tap of B holds it down and the next tap lets go, for a mech's lock-on and the spin-dash charge. START lets go too."),
        new Tweak("infinite-lives", "Infinite lives", "Dying doesn't cost a life, so you never get a game over. 1-ups still add lives, and your save keeps the count you have."),
        new Tweak("m4-time-rank", "Rank Mission 4 by time", "Mission 4, the time-limit mission, is ranked by how fast you finish instead of by score. Your save keeps that rank like any other."),
        new Tweak("mech-handling", "Livelier walkers", "Tails' Cyclone and Eggman's Egg Walker (and the Chao Walkers in 2-player) pick up speed faster once moving, lose less speed when you let go of the stick, and reach a higher top speed. These are starting values that may change. Off in replays; nothing is saved."),
        new Tweak("metal-bounce-fix", "Fix Metal Sonic bounce crash", "Gives Metal Sonic's bounce attack his own model, so it no longer crashes the game when a cheat gives him all upgrades. Changes nothing in normal play."),
        new Tweak("model-tint-fix", "Fix grey model tint", "Rockets, propeller springs, fire skulls and a few other stage objects are drawn at full brightness, without the grey tint the PC version gives them (the model tint fix from SA2 Render Fix). Only how they look changes, and nothing is saved."),
        new Tweak("modloader-default-fixes", "Mod Loader default fixes", "Two fixes the SA2 Mod Loader turns on by default. The white fade on the victory screen after a stage clears fully instead of flickering faintly during the pose, and shiny parts of Chao World models reflect their own view instead of the one left by the model drawn before. Nothing is saved."),
        new Tweak("mono-audio", "Mono audio", "Plays the left and right channels mixed together from both speakers, for one earbud or one ear."),
        new Tweak("no-2p-specials", "No special attacks in 2P", "In 2-player battles, neither player gets special attacks, not from collecting rings and not after losing a life."),
        new Tweak("no-event-bars", "Remove cutscene black bars", "Removes the black bars at the sides of in-engine cutscenes on a wide picture. The scenes are not changed, so the opening and three other cutscenes made for 4:3 may show an effect or picture that stops short of the screen's sides, with black beyond it. Movies keep their bars, because the video is 4:3."),
        new Tweak("no-lives-bonus", "No extra life at 100 rings", "Collecting 100 rings (or 200, 300...) no longer gives an extra life. 1-up boxes still do."),
        new Tweak("no-loading-tips", "Skip loading tips", "Hides Omochao's tips and the Now Loading animation while the game loads. Most loads show a black screen instead; a load over a picture already on screen, such as a stage's title card, keeps that picture."),
        new Tweak("no-music", "Turn off music", "Turns off the stage, menu and cutscene music. Short music cues (1-up, speed-up, invincibility, goal ring, bosses), sound effects and voices still play."),
        new Tweak("no-powerup-music", "No power-up music", "The speed shoes and invincibility boxes no longer play their own tune, so the stage music keeps playing."),
        new Tweak("no-voices", "Mute character voices", "Silences the characters' spoken lines in stages, Omochao's hints and cutscenes. Music and sound effects still play."),
        new Tweak("no-wj-rain", "No White Jungle rain", "White Jungle's rain is not drawn, which can help a slow phone keep up. The stage plays exactly the same, and your save is not changed."),
        new Tweak("pause-no-confirm", "No Restart or Exit question", "Restart and Exit in the pause menu act as soon as you pick them, without asking Yes or No first, so a stray press restarts or quits the stage. Nothing is saved differently: Exit and Restart save what they always do."),
        new Tweak("pause-unfocused", "Pause when unfocused", "Holds the game and its sound while the app is visible but not in focus, such as under the notification shade."),
        new Tweak("physics-preset", "Metal Sonic physics", "Sonic and Shadow use Metal Sonic's physics from 2-player mode in every stage and mode, story and boss stages included: a higher jump, much quicker to speed up and slower to brake. Off in replays; nothing is saved."),
        new Tweak("practice-stage", "Practice a stage", "Starts the game straight in City Escape, with no title or menus, and saves nothing that run. Upgrades from your save are not loaded, so a stage that needs an earlier stage's upgrade may not be finishable. Mission 1 of Green Forest, White Jungle and Security Hall keeps its story time limit. To pick another stage, character or mission, use a computer (or adb) to put a text file named game.env.extra in Internal storage/Android/data/com.sa2recomp.app/files/, next to the game folder, with a line such as SA2_PRACTICE=whitejungle,shadow,2."),
        new Tweak("quiet-omochao", "No Omochao hints", "Omochao, the hint robot, no longer appears in the stages, so it gives no hints."),
        new Tweak("radar-all-pieces", "Radar tracks all pieces", "In treasure hunts, every piece still to find shows on the radar how near it is, not only the first. The radar beeps for the nearest piece, and the \"!\" shows over you on top of any piece. Makes hunts easier."),
        new Tweak("rail-forgiving", "Never fall off rails", "When your character loses balance on a grind rail, for example by going too slowly, they wobble but stay on instead of falling off. You can still jump off. Rails get easier, and the times and ranks you earn with it on are saved as usual."),
        new Tweak("reduce-shake", "Reduce camera shake", "Explosions, bosses and big impacts shake the picture a quarter as much. The game plays the same."),
        new Tweak("ring-loss-cap", "Lose at most 20 rings", "A hit costs at most 20 rings, the ones that scatter, so you can pick them all back up. Getting back to 100 rings that way gives no second extra life. The higher ring counts, scores and ranks it brings are saved as usual."),
        new Tweak("rival-upgrades", "Rival upgrades", "Shadow gets the Bounce Bracelet and Magic Gloves once Sonic has them, Rouge breathes underwater once Knuckles has the Air Necklace, and Mech Tails gets the Protective Armor once Eggman has it. Your save is not changed."),
        new Tweak("shadow-lightdash-fx", "Shadow light dash trail", "Shadow's light dash leaves the fading trail of after-images that Sonic's does, drawn with Shadow's model. Only the look changes; nothing is saved."),
        new Tweak("skip-intro", "Skip intro", "Starts the game at the title screen, without the company logos and the opening cutscene. The opening still plays if you leave the title screen idle."),
        new Tweak("slow-mode", "Slow mode", "Plays the game at three quarters of its speed. The sound slows with it and plays lower in pitch."),
        new Tweak("spin-dash-fast", "Faster spin dash", "The spin dash starts after holding the button for 10 frames instead of 24. A short press still somersaults."),
        new Tweak("stage-select-all", "Show all stages in Stage Select", "Every stage appears in Stage Select, even ones you have not reached in story mode. Showing them writes nothing to your save, but playing one saves its ranks, times and emblems like any other stage, and they stay after you turn this off."),
        new Tweak("start-assists", "Start with assists", "Every level starts with a ring-pulling barrier, lasting invincibility and all your character's upgrades. The upgrades it gives are not saved, but anything you collect or clear with them (upgrades you pick up, Lost Chao, times, ranks and emblems) is."),
        new Tweak("start-in-chao-world", "Start in Chao World", "After you pick your file in File Select, the game goes straight to the Chao World lobby instead of the main menu. It happens once each time the game starts, so leaving Chao World brings back the menus. If your save has not opened Chao World yet, you get the main menu. Your save loads and saves as usual."),
        new Tweak("swap-audio", "Swap left/right audio", "Plays the left channel from the right speaker and the right channel from the left, for headphones worn the other way round. With mono audio on there is nothing to swap."),
        new Tweak("sync-load", "Faster loading", "Reads the files a load needs at once instead of drawing a loading-screen frame for each, so the game starts and stages load sooner, by about half a second to a second each on a fast device. The Now Loading animation stands still while the files load. Your save is not changed."),
        new Tweak("tex-filter", "Sharper textures", "Sharpens textures that have mipmaps, most of the 3D world, so floors and walls stay sharp into the distance: 16x anisotropic filtering where the GPU supports it, and a slight mip bias. Most of the HUD and menus look the same. A small GPU cost."),
        new Tweak("victory-powerups", "Keep powerups on the victory screen", "A barrier or invincibility stays on your character during the victory pose at the end of a stage, instead of vanishing."),
        new Tweak("vsync", "Vsync", "Each frame waits for the screen's refresh (FIFO) instead of mailbox presentation. Android does not tear either way, so on a phone this only adds up to a frame of delay. Leave it off unless you are testing it."),
        new Tweak("walker-cannon-fix", "Fix the Chao Walker cannon crash", "Stops the crash when a Chao Walker has the cannon upgrade, which only a cheat can give it. Changes nothing else."),
    };

    /** The Tweaks card's groups, in the card's order: each a heading, then the names of its tweaks,
     * one a line, in the order the card shows them: the ones players want most first. Every TWEAKS name
     * is in exactly one group (test_tweaks.py): a new Android tweak adds its line to TWEAKS and its
     * name's line to one group (runtime/sa2_tweaks.h, "Add a tweak"). The headings follow the
     * release notes; pause-unfocused, which they list under Windows and Linux, is under Other. */
    static final String[][] TWEAK_GROUPS = {
        {"Gameplay and assists",
            "infinite-lives",
            "stage-select-all",
            "character-select",
            "all-upgrades",
            "practice-stage",
            "spin-dash-fast",
            "physics-preset",
            "mech-handling",
            "quiet-omochao",
            "start-assists",
            "rail-forgiving",
            "forgiving-restarts",
            "radar-all-pieces",
            "ring-loss-cap",
            "force-hard-layout",
            "2p-layout-in-1p",
            "m4-time-rank",
            "rival-upgrades",
            "gold-beetle-stays",
            "victory-powerups",
            "no-lives-bonus",
            "pause-no-confirm",
            "hide-radar",
            "no-2p-specials",
            "slow-mode",
        },
        {"Story and presentation",
            "skip-intro",
            "all-events-skippable",
            "no-event-bars",
            "always-recap",
            "fast-stage-load",
            "sync-load",
            "no-loading-tips",
            "fov",
            "draw-distance",
            "tex-filter",
            "reduce-shake",
            "hide-hud",
            "frametime-overlay",
            "hide-upgrade-models",
            "no-wj-rain",
            "vsync",
            "event-bars-all",
        },
        {"Controls",
            "analog-fix",
            "action-buttons",
            "deadzone",
            "camera-invert",
            "hold-toggle",
        },
        {"Audio",
            "no-music",
            "no-voices",
            "no-powerup-music",
            "mono-audio",
            "swap-audio",
        },
        {"Chao",
            "start-in-chao-world",
            "chao-stat-x5",
            "chao-key-always",
            "garden-ring-trickle",
            "drives-to-minimals",
        },
        {"Fixes",
            "model-tint-fix",
            "shadow-lightdash-fx",
            "metal-bounce-fix",
            "walker-cannon-fix",
            "modloader-default-fixes",
        },
        {"Other",
            "pause-unfocused",
        },
    };
    /** The heading a tweak in no group is shown under, at the end of the card. */
    static final String OTHER = "Other";

    /** One heading of the Tweaks card and its tweaks, in the card's order. */
    static final class TweakGroup {
        final String heading;
        final List<Tweak> tweaks = new ArrayList<>();

        TweakGroup(String heading) {
            this.heading = heading;
        }
    }

    /** The Tweaks card's order: `groups` in their order, each group's tweaks in the group's own
     * order, every line of `tweaks` once. A mistake in `groups` is added to
     * `problems` by name and never hides a switch: a name in no group is shown under OTHER (the
     * last group, or a new one at the end); a name in two groups is shown in the first only; a
     * name that is no tweak's is left out; a group left with no tweak is not shown. */
    static List<TweakGroup> tweakGroups(String[][] groups, Tweak[] tweaks, List<String> problems) {
        Map<String, Tweak> byName = new LinkedHashMap<>();
        for (Tweak t : tweaks) byName.put(t.name, t);
        Map<String, String> placed = new HashMap<>();   // tweak name -> its group's heading
        List<TweakGroup> out = new ArrayList<>();
        TweakGroup other = null;
        for (String[] g : groups) {
            TweakGroup group = new TweakGroup(g[0]);
            for (int i = 1; i < g.length; i++) {
                Tweak t = byName.get(g[i]);
                if (t == null) problems.add("\"" + g[i] + "\" under " + g[0] + " is no tweak");
                else if (placed.containsKey(t.name))
                    problems.add("\"" + t.name + "\" is under " + placed.get(t.name) + " and " + g[0]);
                else {
                    placed.put(t.name, g[0]);
                    group.tweaks.add(t);
                }
            }
            if (OTHER.equals(g[0])) other = group;
            out.add(group);
        }
        for (Tweak t : tweaks) {
            if (placed.containsKey(t.name)) continue;
            problems.add("\"" + t.name + "\" is in no group; shown under " + OTHER);
            if (other == null) out.add(other = new TweakGroup(OTHER));
            other.tweaks.add(t);
        }
        out.removeIf(g -> g.tweaks.isEmpty());
        return out;
    }

    /** What the Chao boosts button turns on (Sa2SettingsActivity.chaoBoosts); both write to the save. */
    static final String[] CHAO_BOOSTS = {"chao-stat-x5", "garden-ring-trickle"};

    /** What the setup screen's Chao World button turns on for its one launch (Sa2Activity.EXTRA_TWEAKS),
     * beside the stored tweaksOn, which it leaves as they are. */
    static final String[] CHAO_LAUNCH = {"skip-intro", "start-in-chao-world"};

    String orientation = LANDSCAPE;
    boolean widescreen = true, hud43 = true;
    boolean autoPlay = false;         // start the game when the app opens or an install finishes: off, the screen stays up
    boolean recordSessions = false;   // the session recorder (Debug card): off, as without it
    int resolution = 0;                  // lines, 0 the screen's
    String aspect = "auto";
    String frameskip = "off";            // FRAMESKIPS; any other stored value is off
    int touchSize = 100, touchOpacity = 100;   // percent of the overlay's own
    int pictureY = 25;                         // upright, the picture's centre, percent of the height down (0 to 50)
    String layoutLandscape = "", layoutPortrait = "";  // SA2_TOUCH_LAYOUT, SA2_TOUCH_LAYOUT_PORTRAIT; "" its own
    String touchMap = "", padMap = "";         // SA2_TOUCH_MAP and SA2_PAD_MAP, only what differs
    final Set<String> tweaksOn = new HashSet<>();  // the TWEAKS names turned on; none by default

    static GameSettings load(Context c) {
        SharedPreferences p = c.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        GameSettings s = new GameSettings();
        s.orientation = p.getString("orientation", s.orientation);
        s.widescreen = p.getBoolean("widescreen", s.widescreen);
        s.hud43 = p.getBoolean("hud43", s.hud43);
        s.autoPlay = p.getBoolean("auto_play", s.autoPlay);
        s.recordSessions = p.getBoolean("record_sessions", s.recordSessions);
        s.resolution = p.getInt("resolution", s.resolution);
        s.aspect = p.getString("aspect", s.aspect);
        s.frameskip = p.getString("frameskip", s.frameskip);
        s.touchSize = p.getInt("touch_size", s.touchSize);
        s.touchOpacity = p.getInt("touch_opacity", s.touchOpacity);
        s.pictureY = p.getInt("picture_y", s.pictureY);
        s.layoutLandscape = p.getString("touch_layout", s.layoutLandscape);
        s.layoutPortrait = p.getString("touch_layout_portrait", s.layoutPortrait);
        s.touchMap = p.getString("touch_map", s.touchMap);
        s.padMap = p.getString("pad_map", s.padMap);
        for (Tweak t : TWEAKS) if (p.getBoolean("tweak_" + t.name, false)) s.tweaksOn.add(t.name);
        return s;
    }

    /** Written at once (commit, not apply): the play screen runs in its own process and reads the
     * file when the session starts. */
    void save(Context c) {
        SharedPreferences.Editor e = c.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
                .putString("orientation", orientation)
                .putBoolean("widescreen", widescreen)
                .putBoolean("hud43", hud43)
                .putBoolean("auto_play", autoPlay)
                .putBoolean("record_sessions", recordSessions)
                .putInt("resolution", resolution)
                .putString("aspect", aspect)
                .putString("frameskip", frameskip)
                .putInt("touch_size", touchSize)
                .putInt("touch_opacity", touchOpacity)
                .putInt("picture_y", pictureY)
                .putString("touch_layout", layoutLandscape)
                .putString("touch_layout_portrait", layoutPortrait)
                .putString("touch_map", touchMap)
                .putString("pad_map", padMap);
        for (Tweak t : TWEAKS) e.putBoolean("tweak_" + t.name, tweaksOn.contains(t.name));
        e.commit();
    }

    // ------------------------------------------------------------ the screen and the picture

    /** The largest window an activity of this app can have, in pixels, {longer side, shorter side}:
     * the whole screen, cutout included (the play screen lays out into it,
     * LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES, and hides the system bars). API 30's maximum
     * window metrics; on API 29 the display's real size. Null when neither answers. */
    @SuppressWarnings("deprecation")
    static int[] screen(Activity a) {
        int w = 0, h = 0;
        try {
            if (Build.VERSION.SDK_INT >= 30) {
                Rect b = a.getWindowManager().getMaximumWindowMetrics().getBounds();
                w = b.width();
                h = b.height();
            } else {
                Point p = new Point();
                a.getWindowManager().getDefaultDisplay().getRealSize(p);
                w = p.x;
                h = p.y;
            }
        } catch (RuntimeException e) {
            return null;
        }
        if (w <= 0 || h <= 0) return null;
        return new int[] {Math.max(w, h), Math.min(w, h)};
    }

    boolean upright() {
        return PORTRAIT.equals(orientation);
    }

    /** The orientation Sa2Activity asks for; -1 for landscape, the manifest's own. */
    int requestedOrientation() {
        if (PORTRAIT.equals(orientation)) return ActivityInfo.SCREEN_ORIENTATION_USER_PORTRAIT;
        if (AUTO.equals(orientation)) return ActivityInfo.SCREEN_ORIENTATION_FULL_USER;
        return -1;
    }

    /** --screen: the screen in the session's orientation, "WxH". */
    String screenArgument(int lng, int shrt) {
        return upright() ? shrt + "x" + lng : lng + "x" + shrt;
    }

    /** Every display setting at its default: the session's own full-width rule decides. */
    boolean displayDefault() {
        return LANDSCAPE.equals(orientation) && widescreen && resolution == 0 && "auto".equals(aspect);
    }

    /** Portrait and auto-rotate draw 4:3, whatever widescreen and the aspect say. */
    boolean fixed43() {
        return !LANDSCAPE.equals(orientation);
    }

    static double ratio(String aspect) {
        int colon = aspect.indexOf(':');
        try {
            return Double.parseDouble(aspect.substring(0, colon)) / Double.parseDouble(aspect.substring(colon + 1));
        } catch (RuntimeException e) {
            return 16.0 / 9;
        }
    }

    /** The picture's aspect, width over height, on a lng x shrt screen. */
    double pictureRatio(int lng, int shrt) {
        if (fixed43()) return 4.0 / 3;
        if ("auto".equals(aspect)) return Math.max((double) lng / shrt, 4.0 / 3);
        return ratio(aspect);
    }

    /** The largest picture of the chosen aspect a lng x shrt screen holds in the session's
     * orientation, {w, h}; null with widescreen off in landscape (the game's own size). */
    int[] fullPicture(int lng, int shrt) {
        if (!fixed43() && !widescreen) return null;
        int bw = upright() ? shrt : lng, bh = upright() ? lng : shrt;
        double r = pictureRatio(lng, shrt);
        if ((double) bw / bh > r) return new int[] {(int) Math.round(bh * r), bh};
        return new int[] {bw, (int) Math.round(bw / r)};
    }

    /** The game's back buffer for these settings on a lng x shrt screen, {w, h}; null when the game
     * keeps its config's own size (widescreen off in landscape, or the default settings on a
     * screen no wider than 4:3, as the session decides). `lines` overrides the resolution (0 the
     * default, the largest picture except where the session keeps the config's size), for
     * listing the choices; as many lines as the largest picture's, or more, is the largest. */
    int[] picture(int lng, int shrt, int lines) {
        // At these lines the session's own rule decides, as it does when every display setting is
        // at its default (the stored resolution is not the one asked about here).
        boolean sessionRule = LANDSCAPE.equals(orientation) && widescreen && "auto".equals(aspect) && lines == 0;
        if (sessionRule) return (long) lng * 3 > (long) shrt * 4 ? new int[] {lng, shrt} : null;
        int[] full = fullPicture(lng, shrt);
        if (full == null) return null;
        int w = full[0], h = full[1];
        if (lines > 0 && lines < h) {
            w = (int) Math.round(lines * pictureRatio(lng, shrt));
            h = lines;
        }
        w = Math.max(320, Math.min(16384, w));
        h = Math.max(320, Math.min(16384, h));
        return new int[] {w, h};
    }

    int[] picture(int lng, int shrt) {
        return picture(lng, shrt, resolution);
    }

    /** Where the player puts a letterboxed picture in an upright window (SA2_PLAYER_PICTURE_Y), or -1
     * (centred) when the orientation is landscape. */
    float pictureAnchor() {
        return LANDSCAPE.equals(orientation) ? -1 : pictureY / 100f;
    }

    /** The frame the touch overlay will see: the picture, or the config's own size. */
    int[] frame(int lng, int shrt) {
        int[] p = picture(lng, shrt);
        return p != null ? p : new int[] {CONFIG_W, CONFIG_H};
    }

    // ------------------------------------------------------------ the session's settings

    /** The --setting values for these settings on a lng x shrt screen, with `launchTweaks` (TWEAKS
     * names) on for this session alone: none at the defaults. */
    List<String> sessionSettings(int lng, int shrt, String... launchTweaks) {
        List<String> e = new ArrayList<>();
        if (!displayDefault()) {
            int[] p = picture(lng, shrt);
            e.add("SA2_PLAY_RES=" + (p == null ? "off" : p[0] + "x" + p[1]));
        }
        if (!hud43) e.add("SA2_PLAY_HUD43=0");
        // The low-FPS speed fix: 2 or 3 game ticks per drawn stage frame, with ADPF's target at that
        // frame time (host/android/native/sa2_android_session.cpp reads SA2_ADPF_TARGET_US), or auto
        // with the 16.67 ms target, since a late game should ask for more clock.
        if ("30".equals(frameskip)) e.addAll(Arrays.asList("SA2_PLAY_FRAMESKIP=2", "SA2_ADPF_TARGET_US=33333"));
        else if ("20".equals(frameskip)) e.addAll(Arrays.asList("SA2_PLAY_FRAMESKIP=3", "SA2_ADPF_TARGET_US=50000"));
        else if ("auto".equals(frameskip)) e.add("SA2_PLAY_FRAMESKIP=auto");
        if (!LANDSCAPE.equals(orientation)) e.add("SA2_PLAYER_PICTURE_Y=" + fraction(pictureY));
        if (touchSize != 100) e.add("SA2_TOUCH_SCALE=" + fraction(touchSize));
        if (touchOpacity != 100) e.add("SA2_TOUCH_OPACITY=" + fraction(touchOpacity));
        if (!layoutLandscape.isEmpty()) e.add("SA2_TOUCH_LAYOUT=" + layoutLandscape);
        if (!layoutPortrait.isEmpty()) e.add("SA2_TOUCH_LAYOUT_PORTRAIT=" + layoutPortrait);
        if (!touchMap.isEmpty()) e.add("SA2_TOUCH_MAP=" + touchMap);
        if (!padMap.isEmpty()) e.add("SA2_PAD_MAP=" + padMap);
        String tweaks = tweaks(launchTweaks);
        if (!tweaks.isEmpty()) e.add("SA2_TWEAKS=" + tweaks);
        if (recordSessions) e.add("SA2_RECORD_SESSION=1");   // the native session's, not the game's
        return e;
    }

    /** SA2_TWEAKS's value: the names of the tweaks turned on, and of `also`, comma-separated in TWEAKS's
     * order, each once; "" for none. */
    String tweaks(String... also) {
        List<String> extra = Arrays.asList(also);
        List<String> on = new ArrayList<>();
        for (Tweak t : TWEAKS) if (tweaksOn.contains(t.name) || extra.contains(t.name)) on.add(t.name);
        return String.join(",", on);
    }

    /** Turns every tweak off (the Tweaks card's "Turn every tweak off", the Android form of the
     * desktop's vanilla-profile) and returns the SA2_TWEAKS value that was on, "" for none. */
    String tweaksOff() {
        String was = tweaks();
        tweaksOn.clear();
        return was;
    }

    private static String fraction(int percent) {
        return String.format(Locale.US, "%.2f", percent / 100.0);
    }

    // ------------------------------------------------------------ button maps

    /** What `button` sends in `map` ("A=B,LT=START"), or `own` when the map leaves it. */
    static String sends(String map, String button, String own) {
        for (String item : map.isEmpty() ? new String[0] : map.split(",")) {
            int eq = item.indexOf('=');
            if (eq > 0 && item.substring(0, eq).equals(button)) return item.substring(eq + 1);
        }
        return own;
    }

    /** `map` with `button` sending `out`: an entry only when it differs from `own`, in `order`. */
    static String withSend(String map, String[] order, String[] owns, String button, String out) {
        StringBuilder b = new StringBuilder();
        for (int i = 0; i < order.length; i++) {
            String v = order[i].equals(button) ? out : sends(map, order[i], owns[i]);
            if (v.equals(owns[i])) continue;
            if (b.length() > 0) b.append(',');
            b.append(order[i]).append('=').append(v);
        }
        return b.toString();
    }

    // ------------------------------------------------------------ the touch layout

    /** The overlay's groups, in SA2_TOUCH_LAYOUT's names and order. */
    static final String[] GROUPS = {"STICK", "FACE", "START", "LT", "RT"};

    /**
     * The touch overlay's layout on a w x h window showing a fw x fh frame, for the layout editor:
     * host/player/touch_overlay.cpp layout(), the same arithmetic, so the editor shows the controls
     * where the game will. The control area is the window, or the band under the picture when that
     * band is at least 30% of the window's short side; s sizes everything (0.12 s the stick's
     * radius); x and y are each group's centre in pixels. The places set for the window's
     * orientation (portrait when it is taller than wide) apply, each held whole inside the area.
     */
    static final class TouchLayout {
        boolean below, upright;
        float ax, ay, aw, ah, s;
        float pictureTop, pictureBottom;   // the picture's rows (the whole height when it fills it)
        final float[] x = new float[5], y = new float[5];
        final float[] dx = new float[5], dy = new float[5];   // the overlay's own places

        /** Half the group's width and height, for the editor's dragging and drawing. */
        float halfW(int g) {
            return g == 0 ? 0.12f * s : g == 1 ? 0.21f * s : g == 2 ? 0.075f * s : 0.09f * s;
        }

        float halfH(int g) {
            return g == 0 ? 0.12f * s : g == 1 ? 0.21f * s : g == 2 ? 0.035f * s : 0.04f * s;
        }

        /** The places as SA2_TOUCH_LAYOUT's value, "" when every group is at its own place. */
        String value() {
            boolean moved = false;
            for (int g = 0; g < 5; g++) moved |= Math.abs(x[g] - dx[g]) > 0.5f || Math.abs(y[g] - dy[g]) > 0.5f;
            if (!moved) return "";
            StringBuilder b = new StringBuilder();
            for (int g = 0; g < 5; g++) {
                if (b.length() > 0) b.append(';');
                b.append(GROUPS[g]).append('=')
                 .append(String.format(Locale.US, "%.4f,%.4f", clamp01((x[g] - ax) / aw), clamp01((y[g] - ay) / ah)));
            }
            return b.toString();
        }
    }

    static float clamp01(float v) {
        return Math.max(0f, Math.min(1f, v));
    }

    /** `anchor` and `minTop`: the picture's place in a window taller than wide, as the player sets it
     * (host/d3d9gpu/d9g_gpu.h fit_rect), its centre at anchor of the height (negative: centred), its
     * top no higher than minTop; a landscape window keeps it centred. */
    static TouchLayout touchLayout(float w, float h, int fw, int fh, float scale, float anchor, float minTop,
                                   String landscapeValue, String portraitValue) {
        TouchLayout l = new TouchLayout();
        l.upright = h > w;
        float shortSide = Math.min(w, h);
        float s = shortSide * scale;
        float top = 0, bottom = h;
        if (fw > 0 && fh > 0 && (double) w * fh <= (double) h * fw) {
            float ph = Math.min(h, Math.round(w * (float) fh / fw));
            if (h - ph > 2f) {
                top = (float) Math.floor((h - ph) / 2);
                if (anchor >= 0 && h > w)
                    top = Math.min(top, Math.max(minTop, (float) Math.round((double) anchor * h - ph / 2.0)));
                bottom = top + ph;
            }
        }
        l.pictureTop = top;
        l.pictureBottom = bottom;
        l.below = h - bottom >= 0.30f * shortSide;
        l.ax = 0;
        l.ay = 0;
        l.aw = w;
        l.ah = h;
        if (l.below) {
            l.ay = bottom;
            l.ah = h - bottom;
            s = Math.min(s, l.ah / 0.60f);
        }
        l.s = s;
        float m = 0.04f * s, R = 0.12f * s, r = 0.075f * s, d = 0.135f * s, ph = 0.035f * s, th = 0.04f * s,
              tw = 0.09f * s;
        l.dx[0] = l.ax + m + 1.6f * R;
        l.dy[0] = l.ay + l.ah - m - 1.6f * R;
        l.dx[1] = l.ax + l.aw - m - d - r;
        l.dy[1] = l.ay + l.ah - m - d - r;
        l.dx[2] = l.ax + l.aw * 0.5f;
        l.dy[2] = l.ay + m + ph;
        l.dx[3] = l.ax + m + tw;
        l.dy[3] = l.ay + m + th;
        l.dx[4] = l.ax + l.aw - m - tw;
        l.dy[4] = l.ay + m + th;
        for (int g = 0; g < 5; g++) {
            l.x[g] = l.dx[g];
            l.y[g] = l.dy[g];
        }
        String value = l.upright ? portraitValue : landscapeValue;
        for (String item : value.isEmpty() ? new String[0] : value.split(";")) {
            int eq = item.indexOf('='), comma = item.indexOf(',');
            int g = eq < 0 ? -1 : Arrays.asList(GROUPS).indexOf(item.substring(0, eq));
            if (g < 0 || comma < eq) continue;
            float px, py;
            try {
                px = l.ax + clamp01(Float.parseFloat(item.substring(eq + 1, comma))) * l.aw;
                py = l.ay + clamp01(Float.parseFloat(item.substring(comma + 1))) * l.ah;
            } catch (NumberFormatException e) {
                continue;   // a place that does not read is the group's own, as the overlay refuses it
            }
            float hw = Math.min(l.halfW(g), l.aw / 2), hh = Math.min(l.halfH(g), l.ah / 2);
            l.x[g] = Math.max(l.ax + hw, Math.min(l.ax + l.aw - hw, px));
            l.y[g] = Math.max(l.ay + hh, Math.min(l.ay + l.ah - hh, py));
        }
        return l;
    }
}
