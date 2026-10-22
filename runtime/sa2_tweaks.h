/* Opt-in play tweaks: the list of tweaks, and what a tweak's own file uses.
 *
 * Subtitle: SA2_TWEAKS is a comma-separated list of tweak names, read once at the process's start
 * by runtime/sa2_tweaks.c (the reader and the report). Each name turns on one change to the game's
 * own behaviour that a player may want and no replay ever sees. Unset or empty, no tweak is on,
 * nothing is printed, and every tweak's wrap only calls its `__real_`. SA2_TWEAK_LIST below names
 * every tweak; each tweak's wraps, and the reading of the image they rest on, are in a file of its
 * own, runtime/tweaks/<name>.c.
 *
 * A code-patch tweak is the other kind: a Mod Loader patch to the game's code bytes, which OpenSA2
 * never runs, so tools/s8/variants/variants.py lifts each module the patch reaches again from the
 * patched bytes (a variant) and generates the wraps that choose it. It is in SA2_TWEAK_LIST like
 * any tweak, and in SA2_TWEAK_PATCH_LIST too; its spec, tools/s8/variants/tweaks/<name>.json,
 * stands in place of a runtime file ("Add a code-patch tweak" below).
 *
 * A host tweak is the third kind: a change the host makes, in the player (host/player, and
 * host/d3d9gpu, the renderer host 2's player links), the desktop launcher (host/launch) or the
 * Android app, and never in the game. SA2_TWEAKS names it as it names the game's, and its one line
 * is in SA2_TWEAK_HOST_LIST below, in place of a line in SA2_TWEAK_LIST: the two lists are the one
 * list of tweaks a player is shown (the Android Tweaks card, the release's README.txt) and every
 * tool reads (tools/s4/tweak_sources.py). The runtime knows each host tweak by its name, so it never
 * calls one unknown, and turns nothing on for it; host/player/tweaks.h makes the host's own bits
 * from the same lines and reads SA2_TWEAKS in the player and the launcher ("Add a host tweak").
 *
 * The rules every tweak keeps:
 *
 *   - Play only. The list is ignored when SA2_REPLAY_TICKS or SA2_REPLAY_WORDS is set, with one
 *     line saying so; tools/s4/device_profile.py PLAY_ENV strips SA2_TWEAKS from every registered
 *     run's environment, and the launcher's selftest drops it, as does a launcher session whose
 *     player is given synthetic input (host/launch/sa2_launch.cpp). The player reads it in a play
 *     session alone (--interactive with --input-path, no synthetic input) and never with the game's
 *     SA2_REPLAY_TICKS or SA2_REPLAY_WORDS set (the launcher asks the game's environment, and it and
 *     the Android session give the player those two beside SA2_TWEAKS); the launcher and the
 *     Android session give it to the player in play alone (host/player/tweaks.h).
 *   - One report line. At the start, when SA2_TWEAKS is set, the run prints one line to stderr (the
 *     session's game.log): the active tweaks, "sa2 tweaks: event-bars off (SA2_TWEAKS=no-event-bars)",
 *     or why none is on. A name that is no tweak is named in that line and ignored, and so is a
 *     code-patch tweak the game was built without; a host tweak is named in it as the host's. The
 *     player's own line, "sa2 host tweaks (player): ...", says which of its tweaks are on (its
 *     stderr: the launcher's session's player.err, the Android session's player.log). The
 *     launcher's record and the Android session's report carry the setting and these lines. A
 *     tweak's own lines after it begin "sa2 tweaks: <report>: " ("sa2 host tweaks: <report>: ").
 *   - Off is the lifted code alone: each wrap tests sa2_tweak_on(its bit) first (a code-patch
 *     tweak's generated wrap, sa2_tweak_patch_on) and otherwise only calls `__real_` and returns.
 *   - By name, never silently: a case a tweak leaves alone on purpose, or cannot act on (a guest
 *     read that fails), is said once in its own line.
 *
 * Where the setting comes from: on the desktop, a line in the release's share/game.env (the
 * launcher passes none of its own environment's SA2_ variables to the game); on Android, the
 * settings screen's Tweaks card (host/android GameSettings.TWEAKS and sessionSettings), or a
 * game.env.extra line, which wins. The player is given the same value as the game.
 *
 * Add a tweak. Each step adds lines and edits none, so tweaks written at once in separate
 * worktrees merge cleanly, or with a conflict between two added lines that keeps both:
 *
 *   1. One line in SA2_TWEAK_LIST: X(SA2_TWEAK_<NAME>, "<name>", "<report>", "<summary>"). <name> is
 *      its SA2_TWEAKS spelling (lower case, words joined by hyphens), <report> what the report line
 *      says of it when on, and <summary> its line in the release's README.txt
 *      (tools/s7/release_build.py player_readme). The lines go in name order, each ending in a
 *      backslash. A tweak's bit is its place in the list, never stored or sent, so where a line
 *      goes changes nothing outside the process; there is room for SA2_TWEAK_MAX (64) in all, fixtures
 *      included.
 *   2. runtime/tweaks/<name>.c: its wraps, and a header comment that says what they do and the
 *      reading of the image it rests on. One line of that comment names the lifted functions the
 *      file wraps, for example
 *
 *          TWEAK_WRAPS: sub_5f8ec0
 *
 *      Every link that compiles the runtime picks the file up by glob and passes -wrap for each
 *      (tools/s4/tweak_sources.py: link_s4.py, and through it the Windows and Android links;
 *      tools/s7/arm64_link.py and tools/s4/build_cost.py). Each wrap is
 *      `__attribute__((used, retain))`, declares its `__real_`, and begins
 *      `if (!sa2_tweak_on(SA2_TWEAK_<NAME>)) return __real_<sub>(state, pc, memory);`. A function
 *      the runtime or another tweak already wraps cannot be wrapped twice, and the tools refuse it
 *      by name: the tweak then goes into the wrap that exists, behind its own sa2_tweak_on test.
 *      When that wrap is another tweak's, the tweak's own file still holds what it does, in a
 *      function the wrap calls when the tweak is on, and its line names the wrap in place of a
 *      list: `TWEAK_WRAPS: none (it acts in no-event-bars.c's wrap of sub_5f8ec0)`.
 *   3. One line in host/android GameSettings.TWEAKS: new Tweak("<name>", the switch's label, the
 *      note under it). The settings screen's Tweaks card makes a switch of each line, and
 *      sessionSettings sends the ones turned on. And one line, "<name>", in one group of
 *      GameSettings.TWEAK_GROUPS, at the place in that group the card shows it: the heading the
 *      card shows the switch under, in the group's own order. A name in no group shows under Other, and test_tweaks.py
 *      fails until it is in one.
 *   4. Its tests. Documentation/scripts/tests/test_tweaks.py holds the list, the files and the
 *      Android lines and groups to one another, and each file's TWEAK_WRAPS line to the wraps it
 *      defines, so a step left out fails there by name. What the tweak does goes in a test file
 *      of its own, which compiles the tweak's file beside sa2_tweaks.c with a driver that stands
 *      in for the lifted functions and the runtime's accessors, as test_tweaks.py does for
 *      no-event-bars.
 *   5. A row in the player's guide (OpenSA2's docs/downloads.md, "Tweaks").
 *
 * Add a code-patch tweak (tools/s8/variants/README.md has the tool's checks and limits). Steps 1, 3
 * and 5 as above; in place of steps 2 and 4:
 *
 *   2. tools/s8/variants/tweaks/<name>.json, its spec: the name, the report (step 1's, the same
 *      words), the functions and the patches, where the patch comes from and what the patched code
 *      does. And one line in SA2_TWEAK_PATCH_LIST below, X(SA2_TWEAK_<NAME>), in name order. No
 *      runtime file: variants.py `build` lifts the variants for a game's lifted objects and writes
 *      the C that wraps each module the patch reaches, testing sa2_tweak_patch_on, and
 *      release_build.py, android_game.py link and arm64_link.py link it with `--variants`. A game
 *      linked without it names the tweak in the report line as one it was built without, and the
 *      release's README.txt leaves it out.
 *   4. Its tests: test_tweaks.py holds the spec to the two lists and to the Android line, and
 *      test_variants.py runs variants.py's checks on every spec against the lifted image.
 *
 * A spec under tools/s8/variants/fixtures/ is the tool's test fixture, not a tweak: it is in no list
 * and no settings screen. A game linked with its variants knows it by its spec's name, after the
 * list's tweaks.
 *
 * Add a host tweak (host/player/tweaks.h has the host's reader and its rules). Each step adds lines,
 * as a tweak's do:
 *
 *   1. One line in SA2_TWEAK_HOST_LIST, and none in SA2_TWEAK_LIST (a name is in one list):
 *      X(SA2_TWEAK_<NAME>, "<name>", "<report>", "<summary>", "<home>"), in name order. <name>,
 *      <report> and <summary> as a tweak's; <home> is where its change is made: "player"
 *      (host/player, and host/d3d9gpu: the player of every platform), "launcher" (the desktop
 *      launcher, host/launch) or "app" (the Android app's Java). Its bit is its place in this list,
 *      in the host's own mask: it spends none of the game's SA2_TWEAK_MAX.
 *   2. Its own file, in its home:
 *        player    host/player/tweaks/<name>.cpp, which every build of the player compiles by glob
 *                  (host/player/build.sh, host/d3d9gpu/build.sh, tools/s7/host2_win_build.py,
 *                  tools/s8/android_app.py), with host/player/tweaks/<name>.h when player.cpp calls
 *                  into it. Its code tests sa2tw::on(sa2tw::SA2_TWEAK_<NAME>) first; the call where
 *                  it acts is the one line it adds to player.cpp. A change inside host/d3d9gpu tests
 *                  sa2tw::on there (inline: host 2's own tests link no reader and see every tweak
 *                  off), and its own code stays in its player file where it can.
 *        launcher  host/launch/tweaks/<name>.cpp, which host/launch/build.sh compiles by glob. The
 *                  launcher reads SA2_TWEAKS in play from share/game.env, the game's value.
 *        app       host/android/java/com/sa2recomp/app/Tweak<Name>.java, <Name> the name's words
 *                  capitalised and joined (no-event-bars: TweakNoEventBars), which the app's Java
 *                  build compiles with the rest. It asks GameSettings.tweaksOn, the Tweaks card's
 *                  switch (game.env.extra, which the session reads after the app is up, does not
 *                  reach it), and says in logcat (tag sa2recomp) what it did.
 *      Off is the host as it was: each change tests its tweak first and otherwise does nothing.
 *   3. One line in host/android GameSettings.TWEAKS, and its name in a TWEAK_GROUPS group, as a
 *      tweak's, when its home is "player" or "app"; a launcher tweak has neither, since it does
 *      nothing on Android.
 *   4. Its tests. test_tweaks.py holds the host list to the home files and the Android lines, and
 *      the reader to its cases; what the tweak does goes in a test file of its own.
 *   5. A row in the player's guide (OpenSA2's docs/downloads.md, "Tweaks"). The release's
 *      README.txt lists it with the game's when its home is "player" or "launcher".
 *
 * Asked for, and already so. A change players ask for as a tweak that this port makes with none is
 * recorded here, and is in no list and on no settings screen: a switch whose on and off are the
 * same would tell the player it did something.
 *
 *   no-exit-prompt (the Mod Loader's DisableExitPrompt, on by default there): the game asks before
 *   it quits from its window procedure's WM_CLOSE arm (0x0040184A, then 0x004015F0, 0x004026E0 and
 *   0x00404A00, which creates the dialog at 0x00404CBA). The guest is never sent WM_CLOSE. The
 *   player owns the window, and a close (SDL_EVENT_WINDOW_CLOSE_REQUESTED, or SDL_EVENT_QUIT, which
 *   the Android app's Back sends) is its end request (host/player/player.cpp, Player::pump_events),
 *   which the launcher or the Android session turns into the game's SIGTERM, and the runtime into
 *   WM_QUIT at its next keyboard poll (runtime/sa2_dinput.c); the guest leaves its loop and calls
 *   ExitProcess(0) itself. That WM_CLOSE never arrives is the whole guarantee. The arm itself does
 *   not end cleanly: a WM_CLOSE sent at the title (SA2_PUMP_INJECT) stops the game on MulDiv, an
 *   import the runtime does not provide, from the dialog's sizer at 0x004029FD, before the dialog
 *   is created. CreateDialogIndirectParamW would answer NULL (runtime/sa2_dialog.h), and the guest
 *   has the branch for that at 0x00404CC2. A change that sends the guest WM_CLOSE must provide
 *   MulDiv first. The Android app's "Press Back again to end the game" is the app's guard against
 *   a stray Back, not the game's prompt, and stays. Documentation/scripts/tests/
 *   test_tweak_no_exit_prompt.py holds the dialog's answer and keeps the name out of the lists; the
 *   close's own tests are named there.
 */
#ifndef SA2_TWEAKS_H
#define SA2_TWEAKS_H

#include <stddef.h>
#include <stdint.h>

/* X(bit, name, report, summary): every tweak, one line each, in name order (step 1 above). */
#define SA2_TWEAK_LIST(X) \
    X(SA2_TWEAK_2P_LAYOUT_IN_1P, "2p-layout-in-1p", "2p layout", "one-player stages load the leftover two-player object layout where it fits (not Hard Mode, Pumpkin Hill or Iron Gate); most upgrades and Chao keys are left out") \
    X(SA2_TWEAK_ACTION_BUTTONS, "action-buttons", "Y light dash, X spin dash", "Y does the light dash, so B and X at a line of light-dash rings somersault and spin-dash; X held starts Sonic's or Shadow's spin dash at once, and B keeps the usual hold") \
    X(SA2_TWEAK_ALL_EVENTS_SKIPPABLE, "all-events-skippable", "every event skippable", "Start skips every cutscene, the Hero story ending and the Last story finale included") \
    X(SA2_TWEAK_ALL_UPGRADES, "all-upgrades", "all upgrades", "player 1 has every character's upgrades from the start of each level, as the Have All Upgrades code gives them (Amy, Metal Sonic, Tikal, Chaos and the Chao Walkers are left alone; in the Sonic vs Shadow fights each hedgehog gets only his own); the upgrades are not saved, what you collect or clear with them is") \
    X(SA2_TWEAK_ALWAYS_RECAP, "always-recap", "recap before stages", "the story-so-far recap before every story stage that has one, not only on a resume") \
    X(SA2_TWEAK_ANALOG_FIX, "analog-fix", "analog full throw", "a gamepad stick gives full speed from about two thirds of a push, not before half, so half a push walks") \
    X(SA2_TWEAK_CHAO_KEY_ALWAYS, "chao-key-always", "chao key always", "a Chao World visit after every stage cleared in one-player play, as with the Chao Key found") \
    X(SA2_TWEAK_CHAO_STAT_X5, "chao-stat-x5", "chao stats x5", "Chao gain up to five times the stat points from Chaos Drives, small animals and fruit, at most 100 a stat for each animal or drive so no stat loses points (kept in the Chao save)") \
    X(SA2_TWEAK_DRAW_DISTANCE, "draw-distance", "draw distance doubled", "stage objects appear and stay loaded at twice the usual distance") \
    X(SA2_TWEAK_DRIVES_TO_MINIMALS, "drives-to-minimals", "drives become minimals", "destroyed enemies leave a small animal instead of a Chaos Drive") \
    X(SA2_TWEAK_EVENT_BARS_ALL, "event-bars-all", "event-bars always", "black bars at the sides of every cutscene on a wide screen, as on a 4:3 one") \
    X(SA2_TWEAK_FAST_STAGE_LOAD, "fast-stage-load", "fast stage load", "a stage starts as soon as its files are loaded, without the loading screen's wait") \
    X(SA2_TWEAK_FORCE_HARD_LAYOUT, "force-hard-layout", "hard layout", "one-player stages use their Hard Mode (mission 5) object layout in every mission; ten stages wait until the save has the upgrade that layout leaves out") \
    X(SA2_TWEAK_FORGIVING_RESTARTS, "forgiving-restarts", "forgiving restarts", "a restart from the pause menu keeps your rings and lives; a miss keeps your score; the save keeps the lives and rings you have") \
    X(SA2_TWEAK_FOV, "fov", "field of view scaled", "a wider view for the gameplay camera, 1.25 times its angle (SA2_FOV_SCALE=0.8 to 1.4 sets it)") \
    X(SA2_TWEAK_GARDEN_RING_TRICKLE, "garden-ring-trickle", "garden ring trickle", "in Chao World, a ring is added to your total every second, up to 9,999,999; the rings are saved") \
    X(SA2_TWEAK_GOLD_BEETLE_STAYS, "gold-beetle-stays", "gold beetle stays", "a Gold Beetle never flies away; it stays until you destroy it (raises scores)") \
    X(SA2_TWEAK_HIDE_HUD, "hide-hud", "hud hidden", "no score, time, rings and lives counters, and no kart race HUD; button prompts and the radar stay") \
    X(SA2_TWEAK_HIDE_RADAR, "hide-radar", "radar hidden", "no emerald radar, radar beep or radar '!' in the treasure hunts") \
    X(SA2_TWEAK_HIDE_UPGRADE_MODELS, "hide-upgrade-models", "upgrade models hidden", "the upgrades a character has found are not drawn on it in play, but a walker keeps its guns; they still work") \
    X(SA2_TWEAK_INFINITE_LIVES, "infinite-lives", "lives held", "dying costs no life, so the lives never run out and there is no game over") \
    X(SA2_TWEAK_M4_TIME_RANK, "m4-time-rank", "mission 4 ranked by time", "mission 4 (the time limit) is ranked by clear time, not score; the save keeps that rank") \
    X(SA2_TWEAK_MECH_HANDLING, "mech-handling", "mech handling", "the walkers (Tails' and Eggman's, and the 2-player Chao Walkers) get ground and air acceleration 1.6 times the game's value, slow down less when you let go and have a higher top speed; starting values, still to be tuned") \
    X(SA2_TWEAK_METAL_BOUNCE_FIX, "metal-bounce-fix", "metal-bounce fixed", "Metal Sonic's bounce attack uses his own model, so it no longer crashes with an all-upgrades cheat") \
    X(SA2_TWEAK_MODEL_TINT_FIX, "model-tint-fix", "model tint fixed", "rockets, propeller springs, fire skulls and a few other stage objects drawn at full brightness, without the grey tint the PC version gives them") \
    X(SA2_TWEAK_MODLOADER_DEFAULT_FIXES, "modloader-default-fixes", "mod loader fixes", "two of the SA2 Mod Loader's default fixes: the white fade on the victory screen ends instead of flickering faintly, and Chao World models set their environment map's matrix before they are drawn") \
    X(SA2_TWEAK_NO_2P_SPECIALS, "no-2p-specials", "no 2P specials", "no special attacks in 2-player battles, from rings or after losing a life") \
    X(SA2_TWEAK_NO_EVENT_BARS, "no-event-bars", "event-bars off", "no black bars at the sides of in-engine cutscenes on a wide screen; movies keep theirs") \
    X(SA2_TWEAK_NO_LIVES_BONUS, "no-lives-bonus", "ring 1-ups off", "no extra life for every 100 rings; 1-up boxes still give one") \
    X(SA2_TWEAK_NO_LOADING_TIPS, "no-loading-tips", "loading-tips off", "no Omochao tips or Now Loading animation while loading: black, or the picture already on screen") \
    X(SA2_TWEAK_NO_MUSIC, "no-music", "music off", "no background music; jingles, sound effects and voices still play") \
    X(SA2_TWEAK_NO_POWERUP_MUSIC, "no-powerup-music", "powerup-music off", "no speed-up or invincibility jingle; the stage music keeps playing") \
    X(SA2_TWEAK_NO_VOICES, "no-voices", "voices off", "no character voices in stages, from Omochao or in cutscenes") \
    X(SA2_TWEAK_NO_WJ_RAIN, "no-wj-rain", "wj rain off", "no rain in White Jungle: the drops are made as usual but neither moved nor drawn, less work for a slow device; the stage plays the same") \
    X(SA2_TWEAK_PAUSE_NO_CONFIRM, "pause-no-confirm", "pause without confirm", "Restart and Exit in the pause menu act at once, without the Yes or No box") \
    X(SA2_TWEAK_PHYSICS_PRESET, "physics-preset", "physics preset", "Sonic and Shadow move with Metal Sonic's physics (SA2_PHYSICS_PRESET=<to>=<from>,... picks others)") \
    X(SA2_TWEAK_PRACTICE_STAGE, "practice-stage", "practice stage", "the game starts straight in one stage, City Escape or the one a line SA2_PRACTICE=level[,character[,mission]] names, and saves nothing in that run; upgrades from the save are not loaded, and mission 1 of Green Forest, White Jungle and Security Hall keeps its story time limit") \
    X(SA2_TWEAK_QUIET_OMOCHAO, "quiet-omochao", "omochao quiet", "no Omochao in the stages: the hint robot neither appears nor gives hints") \
    X(SA2_TWEAK_RADAR_ALL_PIECES, "radar-all-pieces", "radar tracks all pieces", "the treasure-hunt radar shows how near every piece is, not only the first, and its beep and the '!' over the player follow the nearest") \
    X(SA2_TWEAK_RAIL_FORGIVING, "rail-forgiving", "rails forgiving", "a character who loses balance on a grind rail wobbles and stays on instead of falling off; jumping off still works") \
    X(SA2_TWEAK_REDUCE_SHAKE, "reduce-shake", "camera shake reduced", "camera shake from explosions, bosses and impacts moves the view a quarter as far") \
    X(SA2_TWEAK_RING_LOSS_CAP, "ring-loss-cap", "ring loss capped at 20", "a hit costs at most 20 rings, the ones it scatters, not all of them; picking them back up gives no second 100-ring extra life") \
    X(SA2_TWEAK_RIVAL_UPGRADES, "rival-upgrades", "rival upgrades shared", "Shadow, Rouge and Mech Tails share the upgrades Sonic, Knuckles and Eggman have found") \
    X(SA2_TWEAK_SHADOW_LIGHTDASH_FX, "shadow-lightdash-fx", "shadow lightdash trail", "Shadow's light dash leaves the see-through after-image trail that Sonic's does, drawn with Shadow's own model") \
    X(SA2_TWEAK_SKIP_INTRO, "skip-intro", "intro skipped", "the game starts at the title screen, without the logos and the opening cutscene") \
    X(SA2_TWEAK_SPIN_DASH_FAST, "spin-dash-fast", "spin dash charges in 10 frames", "the spin dash starts after 10 frames held instead of 24; a short press still somersaults") \
    X(SA2_TWEAK_STAGE_SELECT_ALL, "stage-select-all", "all stages in Stage Select", "every stage shows in Stage Select, without clearing it in story mode first") \
    X(SA2_TWEAK_START_ASSISTS, "start-assists", "start assists", "every level starts with a magnetic barrier, invincibility and the character's upgrades (the upgrades are not saved; what you collect or clear with them is)") \
    X(SA2_TWEAK_START_IN_CHAO_WORLD, "start-in-chao-world", "start in Chao World", "the first time the main menu would show after File Select loads your save, the game goes straight to the Chao World lobby instead, once each time it starts; a save that has not opened Chao World gets the main menu") \
    X(SA2_TWEAK_SYNC_LOAD, "sync-load", "loads in place", "a load reads its files at once instead of drawing a loading-screen frame for each, so the title and stages come sooner; the Now Loading animation stands still meanwhile") \
    X(SA2_TWEAK_VICTORY_POWERUPS, "victory-powerups", "powerups kept on the victory screen", "barriers and invincibility stay on the victory pose instead of vanishing") \
    X(SA2_TWEAK_WALKER_CANNON_FIX, "walker-cannon-fix", "walker-cannon fix", "a Chao Walker given the Mech's cannon upgrade by a cheat no longer crashes the game") \
    /* the end of the list */

/* X(bit, bit): two tweaks that cannot both be on, one line per pair. When SA2_TWEAKS names both,
 * neither is on, and the report line names the pair (runtime/sa2_tweaks.c). */
#define SA2_TWEAK_EXCLUSIVE(X) \
    X(SA2_TWEAK_EVENT_BARS_ALL, SA2_TWEAK_NO_EVENT_BARS) \
    /* the end of the list */

enum {
#define SA2_TWEAK_INDEX(bit, name, report, summary) bit##_INDEX,
    SA2_TWEAK_LIST(SA2_TWEAK_INDEX)
#undef SA2_TWEAK_INDEX
    SA2_TWEAK_COUNT
};

/* The bits the tweak mask has: the list's tweaks and the fixtures a game links, together. */
#define SA2_TWEAK_MAX 64
#ifndef __cplusplus   /* host/player/tweaks.h includes this header in C++, which has static_assert */
_Static_assert(SA2_TWEAK_COUNT <= SA2_TWEAK_MAX, "SA2_TWEAK_LIST holds more tweaks than the 64-bit mask has bits");
#endif

/* A tweak's bit, a uint64_t: past bit 31 the enum is wider than int (a GNU C extension, which the
 * runtime's compilers, clang and zig cc, take). */
enum {
#define SA2_TWEAK_BIT(bit, name, report, summary) bit = 1ull << bit##_INDEX,
    SA2_TWEAK_LIST(SA2_TWEAK_BIT)
#undef SA2_TWEAK_BIT
};

/* X(bit): the code-patch tweaks in SA2_TWEAK_LIST, one line each, in name order ("Add a code-patch
 * tweak", step 2). */
#define SA2_TWEAK_PATCH_LIST(X) \
    X(SA2_TWEAK_NO_MUSIC) \
    X(SA2_TWEAK_NO_VOICES) \
    X(SA2_TWEAK_SPIN_DASH_FAST) \
    X(SA2_TWEAK_STAGE_SELECT_ALL) \
    X(SA2_TWEAK_VICTORY_POWERUPS) \
    /* the end of the list */

#define SA2_TWEAK_PATCH_OR(bit) | bit
#define SA2_TWEAK_PATCHES ((uint64_t)0 SA2_TWEAK_PATCH_LIST(SA2_TWEAK_PATCH_OR))

/* X(bit, name, report, summary, home): every host tweak, one line each, in name order ("Add a host
 * tweak", step 1). The runtime reads their names and homes (sa2_tweaks.c); their bits are the
 * host's, host/player/tweaks.h's. */
#define SA2_TWEAK_HOST_LIST(X) \
    X(SA2_TWEAK_CAMERA_INVERT, "camera-invert", "camera turn inverted", "the camera turns the other way on the right stick and the triggers", "player") \
    X(SA2_TWEAK_DEADZONE, "deadzone", "round stick deadzone", "a round deadzone for a gamepad's left stick, and full tilt before its edge", "player") \
    X(SA2_TWEAK_FRAMETIME_OVERLAY, "frametime-overlay", "frame-time overlay on", "a box in the corner with the frame rate, frame time and late frames (sdl3-gpu host)", "player") \
    X(SA2_TWEAK_HOLD_TOGGLE, "hold-toggle", "B toggles", "B held by one tap and let go by the next (a mech's lock-on, the spin-dash charge)", "player") \
    X(SA2_TWEAK_MONO_AUDIO, "mono-audio", "mono audio", "the same sound from both speakers: left and right mixed to mono", "player") \
    X(SA2_TWEAK_PAUSE_UNFOCUSED, "pause-unfocused", "held while unfocused", "the game and its sound pause while the player's window is out of focus (in a paced session, the default)", "player") \
    X(SA2_TWEAK_SLOW_MODE, "slow-mode", "game at 0.75 speed", "the game at three quarters of its speed, its sound slowed with it (and lower in pitch)", "player") \
    X(SA2_TWEAK_SPEEDRUN_TIMER, "speedrun-timer", "speedrun timer published", "the in-game timer and stage state sent to localhost for LiveSplit and other timers, read-only", "launcher") \
    X(SA2_TWEAK_SWAP_AUDIO, "swap-audio", "swap audio", "left and right speakers swapped", "player") \
    X(SA2_TWEAK_TEX_FILTER, "tex-filter", "texture filter 16x", "16x anisotropic filtering (where the device has samplerAnisotropy) and a -0.25 mip bias on mip-mapped, mip-filtered textures (the sdl3-gpu player)", "player") \
    X(SA2_TWEAK_VANILLA_PROFILE, "vanilla-profile", "vanilla: every other tweak off", "every other tweak named with it off, for a vanilla run that keeps the list", "launcher") \
    X(SA2_TWEAK_VSYNC, "vsync", "vsync on", "presents each frame on the display's refresh (vsync); stops tearing only where off would use IMMEDIATE (X11, Windows), about a frame more delay", "player") \
    /* the end of the list */

/* Whether the tweak with this bit is on in this run: SA2_TWEAKS names it and no replay is set. */
int sa2_tweak_on(uint64_t bit);

/* A code-patch tweak a game links: its SA2_TWEAKS name and what the report line says of it. */
typedef struct {
    const char *name, *report;
} sa2_tweak_patch;

/* The code-patch tweaks this game links, in the order of the file tools/s8/variants/variants.py
 * generates, which defines this; *count gets how many. A game linked without that file links
 * sa2_tweaks.c's weak definition, which names none. */
const sa2_tweak_patch *sa2_tweak_patches_linked(unsigned *count);

/* Whether row `row` of sa2_tweak_patches_linked is on in this run: the generated wraps ask it. */
int sa2_tweak_patch_on(unsigned row);

/* The tweaks `value` turns on (a mask of the bits above, then one bit for each fixture the game
 * links); `line` gets the run's one report line, or "" when the value is unset or empty. The reader
 * calls it once at the start; exposed for the unit test
 * (Documentation/scripts/tests/test_tweaks.py). */
uint64_t sa2_tweaks_parse(const char *value, int replay, char *line, size_t n);

#endif
