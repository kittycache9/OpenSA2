# Download and play OpenSA2

Get the builds from [GitHub Releases](https://github.com/kittycache9/OpenSA2/releases/tag/v0.1.0-alpha.5). The current release is an experimental prerelease. It has reached gameplay, but it has not completed a full-game compatibility pass. Read the [limitations](status.md) before testing.

You need your own supported, unmodified **2012 PC release of Sonic Adventure 2**. That is the version Steam sells and installs today. "2012" names the release, not an old build: do not look for an older copy. Install the game through Steam, remove any mods, and use its folder as it is. The installer checks every file it reads against the known hashes, so a modified, repacked, or downloaded copy fails the check.

The downloads include recompiled game logic and the port's runtime. They include no original game executable or assets. The installer verifies the files you supply and prepares a separate installation.

| Platform | Download | Requirements |
| --- | --- | --- |
| Windows, x86-64 | [Windows ZIP](https://github.com/kittycache9/OpenSA2/releases/download/v0.1.0-alpha.5/OpenSA2-v0.1.0-alpha.5-windows-x86_64.zip) | Windows 10 or later and a Vulkan or Direct3D 12 graphics driver. |
| Android, arm64 | [Android APK](https://github.com/kittycache9/OpenSA2/releases/download/v0.1.0-alpha.5/OpenSA2-v0.1.0-alpha.5-android-arm64.apk) | Android 10 or later, a 64-bit ARM device, and Vulkan 1.1 or later graphics. Device support is experimental. |
| Linux, x86-64 | [Linux DEB](https://github.com/kittycache9/OpenSA2/releases/download/v0.1.0-alpha.5/OpenSA2-v0.1.0-alpha.5-linux-x86_64.deb) | Ubuntu 24.04, Debian 13, or a compatible distribution, with a working Vulkan driver. |

## Windows

1. Extract the entire ZIP to a writable folder. Keep its `bin` and `share` folders together.
2. Run `OpenSA2.cmd`.
3. Let the installer find your Steam copy, or select the game folder or a ZIP made from that folder.

The Windows build is unsigned. Windows may ask you to confirm that you want to run the downloaded program. Saves, settings, and logs use `%LOCALAPPDATA%\sa2recomp`.

## Android

1. On your computer, open the game's folder. In Steam, right-click Sonic Adventure 2 and select **Manage** > **Browse local files**.
2. Put everything in that folder into a ZIP. The archive must include `sonic2app.exe` and the `resource` folder.
3. Copy the ZIP to your device.
4. Download the APK on your device and install it. Android may require permission for your browser or file manager to install apps.
5. Open OpenSA2, tap **Choose game ZIP**, and select your ZIP.

The app copies the ZIP, then shows "Checking the game ZIP" while it verifies and imports the files into its own storage. This can take a few minutes, and the screen can look stuck. Keep the app open until it says "Tap Play". You can then delete the ZIP. If a file fails the check, the setup screen names it.

The app supports touch controls and external controllers. The touch controls appear when you touch the screen.

The app no longer starts the game by itself. It opens to its own screen, and you tap **Play**. To start faster, press and hold the app icon on your home screen and tap **Play**, or turn on **Start the game when the app opens** in **Game settings**, which also starts the game when an install finishes.

### Game settings on Android

Tap **Game settings** on the setup screen. Changes apply the next time the game starts. App updates keep your settings.

In the **Tweaks** card, the **Chao** group has a **Turn on the Chao boosts** button. It turns on **Chao stats x5** and **Rings over time in Chao World** together. Both write to your save and stay there when you turn them off, so the button offers to back up your saves first. **Chao Key always held** is not part of it, because it can mark the Chao Key as found in your save.

- **Orientation** is Landscape by default. **Portrait** lets you hold the phone upright: the game is drawn at 4:3 across the screen, in the middle of its upper half, and the touch controls sit in the space below the picture. **Picture position** moves the picture anywhere from the top of the screen to its middle. **Auto-rotate** follows the phone when its own auto-rotate is on. The game is then drawn at 4:3 so that it fits either way.
- **Full-width picture** draws the game at the screen's shape. Turn it off to keep the game's own 16:9 picture, with bars at the sides of a wider screen.
- **Aspect ratio** is the screen's shape by default. You can choose 21:9, 16:9, or 4:3 instead.
- **Resolution** is the largest picture the screen holds. Fewer lines can run faster on a slower phone.
- **Low-FPS speed fix (experimental)** keeps stages at their normal speed when the phone cannot draw 60 frames per second. **30 FPS** and **20 FPS** run two or three game ticks for each drawn frame, and **Auto** chooses one, two, or three as the game runs. Menus, cutscenes, and movies still slow down on a slow phone. It is off by default. See [Low-FPS speed fix](#low-fps-speed-fix-experimental) for what to expect.
- **HUD at 4:3 positions** keeps the score, time, and rings near the middle of a wide picture. It is on by default.
- **Touch controls** sets the size and opacity of the on-screen controls. At 0% opacity the controls are hidden but still work. **Move the controls** lets you drag each control to a new place, and **Change what the touch buttons do** changes the game button each one sends.
- **Controller** changes the game button that each controller button sends. Buttons are named by position: the bottom face button is A, as on an Xbox controller.
- **Tweaks** are optional changes to the game, each off by default. Turn on **Remove cutscene black bars** to remove the black bars that in-engine cutscenes draw at the sides of a wide picture. Movies keep their bars. See [Tweaks](#tweaks) for what to expect. **Turn every tweak off** turns all of them off at once.

**Reset all settings** restores the defaults. It does not change your saves.

The **Saves** card in **Game settings** manages the game's saves. **Back up saves now** copies your current saves to a backup, unless the newest backup already holds exactly them. **Import saves** takes save files, such as `SONIC2B__S01` for the story or `SONIC2B__ALF` for Chao World, or a ZIP of them. Given a Debug Export, it asks whether to import the saves from the start or the end of that session. It replaces only the saves it brings, and it keeps a backup of your current saves first. The app keeps the last 10 backups. **Export saves** writes the saves the game uses to a ZIP in a place you choose. **Restore a backup** puts the saves back to one of the backups. The app refuses a file whose name or size is not a save's. It does not check a Chao World save's checksum, so import only saves you trust. The game reads the new saves the next time it starts.

The **Debug** section at the end of **Game settings** has **Record sessions** and **Debug Export**. **Record sessions** is off by default; turned on, each session also keeps what a replay needs (see below). **Export the latest session** makes one ZIP of the last time you played and opens the share sheet, so you can attach it to a bug report or send it to your computer. **Export another session** picks one of the earlier sessions the app keeps. See [Export a session for a bug report](#export-a-session-for-a-bug-report) for what the ZIP holds.

### Report a crash on Android

If the game stops, OpenSA2 returns to its setup screen and says so. Tap **Report on GitHub**. The app copies a crash report and opens the bug-report form with your device filled in. Paste the report into the **Crash report** box, then fill in the steps and what you expected. **Copy report** copies the whole report, **Copy summary** copies a shorter version that fits in a forum comment, and **Share report** sends it to another app. **Share OpenSA2** at the bottom of the screen sends a friend the download link. If the installer refuses your game files, the same buttons copy its output.

The report contains the app version, the device model, the GPU, the Android version, and the game's last log lines. It contains no game files or account details. Read it before you post it. The app also saves the last report as `Android/data/com.sa2recomp.app/files/crash-report.txt`, which a computer can read over USB.

This release uses a new release signing key. If you previously installed a development APK, Android may reject an update because the signing key differs. Back up that app's saves before uninstalling the development version. Public builds will keep the release key for updates.

## Linux

Download the DEB, then install it from that directory:

```bash
sudo apt install './OpenSA2-v0.1.0-alpha.5-linux-x86_64.deb'
```

Open OpenSA2 from your application menu, or run `opensa2`. The first start finds a Steam copy or offers a game-folder picker. A controller is optional.

Saves, settings, and logs use the port's per-user `sa2recomp` directories under your XDG data and configuration locations. The package does not write into your Steam installation.

## Export a session for a bug report

Each time you play, OpenSA2 keeps a session folder with the game's logs and your keyboard input. A Debug Export puts one session into a ZIP that a developer can read, with the saves as they are when you export.

For a ZIP a developer can also replay, turn on the session recorder before you play: **Record sessions** in the Debug section of **Game settings** on Android, or `--record-session` (`opensa2 --record-session`) on the desktop. To record every desktop session, add the line `SA2_RECORD_SESSION=1` to the release's `share/game.env`. A recorded session also keeps your controller input, the settings the game ran with, and copies of your saves and configuration from before and after it, about 200 KB more per session. The recorder is off by default.

On Android, use **Debug Export** in the Debug section of **Game settings**. On Linux, run:

```bash
opensa2 --debug-export
```

On Windows, run `bin\sa2recomp.exe --debug-export` from the extracted folder (tested on Linux and Android only). With no other argument the newest session is exported to `exports/sa2-debug-<session>.zip` in the port's data folder, and the command prints the ZIP's path. Name another session folder (or give its path) to export it instead, and add `--out FILE.zip` to choose where the ZIP goes.

The ZIP holds a `MANIFEST.txt` that lists every file with its size and purpose. Your home folder, game folder and data folder are written as `~`, `<game>` and `<data>` in its text files. It holds your saves and your game settings, but no game files, no pictures of the game, and no account details. Read the manifest before you share the ZIP.

## Tweaks

Tweaks are optional changes to how the game looks or plays. Each one is off until you turn it on, and replays and the launcher's self-test never use them. Tweaks arrived in v0.1.0-alpha.4.

| Name | Change |
| --- | --- |
| `2p-layout-in-1p` | Thirteen one-player stages load their leftover two-player object layout, with other rings, items, and enemies. Hard Mode, Pumpkin Hill, and Iron Gate keep their own layout. These layouts leave out most upgrades and Chao key boxes, and some switches and animals, so a mission may not be finishable. Clears, ranks, and emblems still save, but an upgrade that the layout leaves out cannot be collected or saved. |
| `action-buttons` | The light dash moves to Y: at a line of light-dash rings, Y dashes along them, so B or X there somersaults or starts a spin dash as anywhere else. You still need the Light Shoes, and the on-screen prompt still shows the usual button. Holding X starts Sonic's or Shadow's spin dash at once, with no charge-up and no somersault; B keeps the usual hold, where a tap somersaults. The X part follows the SA2 Mod Loader code "Press B to Somersault" by Hyper and MainMemory. Nothing is saved differently. |
| `all-events-skippable` | Start skips every cutscene, including the Hero story ending and the Last story finale, which the game otherwise makes you watch. |
| `all-upgrades` | Your character has every upgrade from the start of each level, as with the Have All Upgrades cheat, so upgrade-only paths are open at once. It works in one-player play only, and Amy, Metal Sonic, Tikal, Chaos, and the Chao Walkers are left as they are. In the Sonic vs Shadow fights, each hedgehog gets only his own upgrades, so neither wears the other's. The upgrades it gives are not saved, but anything you collect or clear with them is, such as upgrades you pick up, Lost Chao, times, ranks, and emblems. |
| `always-recap` | Plays the story-so-far recap before every story stage that has one, not only when you continue a story from story select. A, B, or Start skips it. |
| `analog-fix` | A gamepad stick reaches full speed at about two thirds of a push instead of before half, so half a push walks and a full push still runs. The keyboard plays as before. |
| `camera-invert` | The camera turns the other way: the right stick's left and right swap, and so do the LT and RT triggers. Menus that read the right stick or the triggers see the swap too. |
| `chao-key-always` | After every stage you clear in one-player play, you go to Chao World as if you had found the Chao Key. Your save may record the key as found. |
| `chao-stat-x5` | Chao gain up to five times the stat points from Chaos Drives, small animals, and fruit, like the Mod Loader's "Chao Stat Multiplier" code. An animal or a drive gives at most 100 points to a stat, so a stat never loses points. The extra points are saved with your Chao and stay after you turn the tweak off. |
| `deadzone` | Gives each gamepad's left stick a round deadzone, so a worn stick that rests slightly off center stays still, and full tilt comes a little before the stick's edge, the same in every direction. The right stick and the touch controls are unchanged, and the game's own deadzone still applies after this one. |
| `draw-distance` | Rings, enemies, and other stage objects appear and stay loaded at twice the usual distance, so less pops in. The scenery's own draw distance is unchanged. It costs speed on slower devices. |
| `drives-to-minimals` | Destroyed enemies leave a small animal instead of a Chaos Drive, which changes what you can collect for your Chao. |
| `event-bars-all` | Draws black bars at the sides of every in-engine cutscene on a screen wider than 4:3, so each one looks as it does on a 4:3 screen. Movies are unchanged. It cannot be combined with `no-event-bars`: with both set, neither applies. |
| `fast-stage-load` | Starts a stage as soon as its files are loaded. The game no longer waits at the end of the loading screen, so the loading screen and its tip can end sooner. Chao World loads as usual. |
| `force-hard-layout` | Every mission on a one-player stage uses that stage's Hard Mode (mission 5) layout of rings, items, and enemies. Your mission, its goal, and its rank stay the ones you picked, and a clear saves as that mission. The ten stages whose Hard Mode layout has no upgrade keep their normal layout until your save has that upgrade. Missions 2 to 4 may be harder to finish. With `2p-layout-in-1p` also on, a stage that loads its Hard Mode layout keeps it. |
| `forgiving-restarts` | Restarting from the pause menu keeps your rings and costs no life, and a miss keeps your score. Kept rings still give extra lives and count toward a 100-ring mission. Your save keeps the lives you have and the rings you finish with, and scores, ranks, and missions you finish with it on are saved as usual. |
| `fov` | The camera in stages shows a wider view, 1.25 times its usual angle. To choose the amount, add a line such as `SA2_FOV_SCALE=1.1` to `game.env`, with a value from 0.8 to 1.4. The HUD, cutscenes, and menus look the same as before. |
| `frametime-overlay` | Shows a small box at the top left of the window with the frame rate (FPS), the mean and longest frame time over the last second (MS, MAX), and the number of frames shown more than 16.7 ms late (LATE). The box is drawn over the window only, never into recordings or screenshots. On Android it stays clear of the touch controls and the screen's cutout. The default `sdl3-gpu` player draws it; the Linux launcher's `--host dxvk-native` player does not. |
| `garden-ring-trickle` | In Chao World, one ring is added to your ring total every second, up to 9,999,999. The rings are kept in your save. |
| `gold-beetle-stays` | A Gold Beetle that appears no longer flies away when its time runs out; it stays until you destroy it. This raises scores, so the scores and ranks the game saves are higher than in the original game. |
| `hide-hud` | Hides the score, time, rings, and lives counters during play, and the whole kart race display, for a clean picture. Button prompts and the radar stay, and the game plays the same. |
| `hide-radar` | Treasure hunts (Knuckles and Rouge) show no emerald radar and no "!" over a piece, and play no radar beep. You find the pieces by eye and with the hint monitors. Your save is not changed. |
| `hide-upgrade-models` | The upgrades a character has found, such as the Bounce Bracelet or the Shovel Claws, are not drawn on the character during play. Walkers keep their guns, because their shots start from them. The upgrades still work, and cutscenes still show them. |
| `hold-toggle` | Makes B a toggle on a controller or the touch controls: one press holds B down and the next press lets it go, for a mech's lock-on or the spin-dash charge without holding the button. START, switching away from the game's window, or unplugging the controller also lets it go. In menus, a held B stays held until you press B or START again. |
| `infinite-lives` | Dying costs no life, so the lives never run out and there is no game over. 1-ups still add lives, and your save keeps the count you actually have. |
| `m4-time-rank` | Mission 4 (the time-limit mission) is ranked by your clear time instead of your score. An A needs 60% of the stage's time limit or less, then B 70%, C 80%, and D 90%. A Perfect still gives an A. The results screen still counts up the score, and your save keeps the rank like any other. |
| `mech-handling` | Tails' Cyclone and Eggman's Egg Walker, and the Chao Walkers in 2-player mode, pick up speed faster once moving, lose less speed when you let go of the stick, and reach a higher top speed. These are starting values that may change after more play: ground and air acceleration 1.6 times the game's value, slowdown with the stick released 0.6 times, top speed 1.25 times, and the fastest running speed 1.8 to 2. Turning, hovering, knockback, and air steering are unchanged. With `physics-preset` also on, these values replace the preset's for the walkers. Your save is not changed. |
| `metal-bounce-fix` | Gives Metal Sonic's bounce attack a model he has, so the attack no longer crashes the game. The crash happens only when a cheat gives Metal Sonic all upgrades, so this changes nothing in normal play. |
| `model-tint-fix` | Rockets, propeller springs, fire skulls and a few other stage objects are drawn at full brightness, without the grey tint the PC version gives them. This is the model tint fix from SA2 Render Fix. Only how the objects look changes; your save is not changed. |
| `modloader-default-fixes` | Two fixes the SA2 Mod Loader turns on by default. On the victory screen after any stage, the faint white fade over your character's pose now clears completely instead of flickering slightly until the results appear; the results screen's timing is unchanged. In Chao World, models with a shiny, reflective surface compute their own reflection instead of reusing the previous model's. Your save is not changed. From the SA2 Mod Loader patches "Fix Screen Fade" by MainMemory and "Fix Environment Mapping" by Exant. |
| `mono-audio` | Both speakers play the left and right channels mixed together, for one earbud or for hearing in one ear. |
| `no-2p-specials` | In 2-player battles, neither player earns special attacks, from collecting rings or after losing a life. 1-player games and save data are unchanged. |
| `no-event-bars` | Removes the black bars that in-engine cutscenes draw at the sides of a screen wider than 4:3, so you see what the game draws behind them. The scenes are not changed. Movies keep their bars. |
| `no-lives-bonus` | Collecting 100 rings (or 200, 300, and so on) no longer gives an extra life or plays the 1-up jingle. 1-up boxes and other extra lives still count, and your save keeps the lives you earned. |
| `no-loading-tips` | Hides Omochao's tips and the animated Now Loading ring while the game loads. Most loads show a black screen instead. A load over a picture already on screen, such as a stage's title card, keeps that picture. |
| `no-music` | Linux and Android only. Turns off the stage, menu, and cutscene music. Sound effects, voices, and the short music cues (1-up, speed shoes, invincibility, goal ring, and boss cues) still play. Useful when you stream, record, or play your own music. From the SA2 Mod Loader code "Disable Music" by MainMemory. |
| `no-powerup-music` | The speed shoes and invincibility item boxes no longer play their own tune, so the stage music keeps playing. The power-ups still work as before. |
| `no-voices` | Linux and Android only. Silences the characters' voices in stages, Omochao's hints, and the voices in cutscenes. Music and sound effects still play. Based on the SA2 Mod Loader code "Disable Voices" by MainMemory. |
| `no-wj-rain` | White Jungle's rain is not drawn or animated, which saves work on slow devices such as phones. The stage, its enemies and items, and your save are unchanged. |
| `pause-no-confirm` | Restart and Exit in the pause menu act as soon as you pick them, without the Yes or No question, so a stray press restarts or leaves the stage. Once you pick one, there is no way to back out. Nothing is saved differently: Exit and Restart save what they always do. |
| `pause-unfocused` | The game and its sound pause while the game's window is out of focus, minimized, or hidden, and continue from the same moment when you come back. The stage timer does not run while paused. It needs paced play, which is the default: a session started with `--no-pace` is not paused. On Android it pauses the game while the app is visible but not in focus, such as under the notification shade. |
| `physics-preset` | Sonic and Shadow use Metal Sonic's movement values from 2-player mode in every stage and mode: a higher jump, much faster acceleration, and weaker brakes. To give a character another character's physics instead, add a line such as `SA2_PHYSICS_PRESET=shadow=amy` to `game.env`. Your save is not changed. |
| `practice-stage` | The game starts straight in one action stage, skipping the logos, title, and menus, and saves nothing in that run. The stage is City Escape unless a line `SA2_PRACTICE=stage[,character[,mission]]` in `game.env` names another, for example `SA2_PRACTICE=whitejungle,shadow,2`. On Android, put that line in a text file named `game.env.extra` in `Android/data/com.sa2recomp.app/files/` on the internal storage. Upgrades from your save are not loaded, so a stage that needs an earlier stage's upgrade may not be finishable, and mission 1 of Green Forest, White Jungle, and Security Hall keeps its story time limit. |
| `quiet-omochao` | Omochao, the hint robot, no longer appears in the stages, so it gives no hints. The Chao Garden is unchanged. |
| `radar-all-pieces` | In treasure hunts (Knuckles and Rouge), the radar shows how near every piece still to find is, not only the first one, and its beep and the "!" over your character follow the nearest piece. This makes hunts easier. Your save is not changed. |
| `rail-forgiving` | When your character loses balance on a grind rail, for example by slowing to a stop, they wobble but stay on the rail instead of falling off. Jumping off and the other ways off a rail work as before. Sonic, Shadow, Amy, and Metal Sonic all get it, in 2-player battles too. Times and ranks you earn with it on are saved as usual. |
| `reduce-shake` | Camera shake from explosions, bosses, and big impacts moves the picture a quarter as far. The shake's timing and the gameplay stay the same. |
| `ring-loss-cap` | When you get hit, you lose at most 20 rings instead of all of them. The game scatters at most 20 rings on a hit, so you can pick every lost ring back up. Getting back to 100 rings this way gives no second extra life. Scores and ranks you earn with it on are higher than normal and are saved as usual. |
| `rival-upgrades` | Shadow, Rouge, and Mech Tails get the upgrades Sonic, Knuckles, and Eggman have found: Shadow can use the Bounce Bracelet and Magic Gloves, Rouge never runs out of air underwater, and Mech Tails gets the Protective Armor's extra health. Your save is not changed. |
| `shadow-lightdash-fx` | Shadow's light dash leaves the same fading trail of after-images that Sonic's does, drawn with Shadow's own model. Only the look changes, and nothing is saved. |
| `skip-intro` | Starts the game at the title screen, skipping the company logos and the opening cutscene. Loading tips still appear. The opening still plays if you leave the title screen idle. |
| `slow-mode` | The game runs at three quarters of its normal speed. Its sound slows down with it and plays lower in pitch. |
| `speedrun-timer` | Windows and Linux only. Sends the in-game timer and the stage state to timers on the same computer: a 64-byte UDP datagram to 127.0.0.1:16838 on each change, and LiveSplit Server commands to 127.0.0.1:16834 when LiveSplit is running (start, game time, and a split at each goal). It only reads the game's memory and never changes the game. Its messages are in the session's `launcher.log`. |
| `spin-dash-fast` | Linux and Android only. The spin dash starts after you hold the button for 10 frames instead of 24, so it charges in well under half the time. A short press still does a somersault. From the SA2 Mod Loader code "Reduce Spin Dash Delay" by MainMemory. |
| `stage-select-all` | Linux and Android only. Shows every stage in Stage Select, including ones you have not reached in story mode. Stage Select itself still appears only after you finish a story. Showing the stages writes nothing to your save, but playing one saves its ranks, times, and emblems like any other stage, and they stay after you turn the tweak off. From the SA2 Mod Loader code "All Stages Appear in Stage Select" by MainMemory. |
| `start-assists` | Every level starts with a magnetic barrier that pulls in rings, invincibility, and your character's own upgrades, kept for the whole level. No shield bubble, sparkle, or invincibility music appears, the barrier and invincibility item boxes do nothing, and falling or drowning still costs a life. The upgrades it gives are not saved, but anything you collect or clear with them is, such as upgrades you pick up, Lost Chao, times, ranks, and emblems. |
| `swap-audio` | The left channel plays from the right speaker and the right channel from the left, for headphones worn the other way around. With `mono-audio` also on, there is nothing to swap. |
| `sync-load` | The game reads the files a load needs all at once, as the SA2 Mod Loader's synchronous loading does, instead of drawing a loading-screen frame for each file. The game reaches the title about half a second sooner, and a stage loads about half a second to a second sooner (measured on a fast PC; a slower device saves less). The Now Loading ring and Omochao's tip stand still while the files load. Your save is not changed. |
| `tex-filter` | Sharper textures: 16x anisotropic filtering where the GPU supports it, and a slight mip bias, on textures that have mipmaps, which covers most of the 3D world. Most of the HUD and menus look the same. It has a small GPU cost. The default `sdl3-gpu` player applies it; the Linux launcher's `--host dxvk-native` player ignores it and says so. |
| `vanilla-profile` | Windows and Linux only. Turns off every other tweak named with it, so the game plays as it was made, for example for a speedrun. Your list stays in `game.env`, and removing `vanilla-profile` turns the other tweaks back on. On Android, use **Turn every tweak off** in the **Tweaks** card instead. |
| `victory-powerups` | Linux and Android only. A barrier or invincibility stays on your character during the victory pose at the end of a stage instead of vanishing. From the SA2 Mod Loader code "Keep Powerups during Victory Screen" by Sora. |
| `vsync` | Each frame waits for the display's refresh. This stops tearing on Windows and on Linux under X11, and adds up to one frame of delay. On Wayland and Android the picture does not tear without it, so there it only adds delay. The game's speed is unchanged. |
| `walker-cannon-fix` | Fixes a crash when a Chao Walker that has the Mech's cannon upgrade fires its cannon. Only a cheat gives a Chao Walker that upgrade, so without one this tweak changes nothing. |

With `no-event-bars` on, every in-engine cutscene loses its bars, and the scenes stay as the game made them. Four cutscenes were made for 4:3: the opening one (event 361, which the game also plays when it asks for event 360) and events 21, 205, and 206. These can show an effect or picture that stops short of the sides of the screen, with black beyond it. The opening draws its title cards, flashes, backdrops, and logo glow over the middle 4:3 part of the picture only, so on a wide screen those effects end at a hard edge where a bar was. In event 21, the map of the space colony does not reach the sides of the screen, so black strips stay at the far left and right. Movies keep their bars, because the video itself is 4:3.

On Android, use the **Tweaks** card in **Game settings**, which groups the tweaks under Gameplay and assists, Story and presentation, Controls, Audio, Chao, Fixes, and Other. On Windows and Linux, add a line to the release's `share/game.env` file:

```
SA2_TWEAKS=no-event-bars
```

On Windows the file is `share\game.env` in the extracted folder. For the Linux package it is `/usr/lib/opensa2/share/game.env`, which you edit as root; a package update replaces it. Separate several tweak names with commas. Setting `SA2_TWEAKS` in a shell has no effect, because the launcher passes only `game.env`'s settings to the game.

The launcher prints the tweaks it passes, and each session's game log starts with a line that names the active tweaks. A name that is not a tweak is reported by name in that line and ignored. Some tweaks change the player or the launcher instead of the game. The game's line names them as host tweaks, and the player says which of its tweaks are on in its own log, `player.err` (on Android, the session report). The launcher says which of its own are on in `launcher.log`.

There is no tweak to remove the game's quit confirmation, the Mod Loader's DisableExitPrompt option, because OpenSA2 never shows it: closing the window, or pressing Back twice on Android, ends the game at once.

## Source and checksums

The release provides `SHA256SUMS` for the downloadable files. The project source for all three packages is at the release tag. The [developer guide](getting-started.md) explains the local build inputs and the current limits of the public build setup.

All three packages include the math fixes from 2026-10-15. Report the platform and release version with any bug. Android shows the version in the app's title.

## Low-FPS speed fix (experimental)

The game advances one tick for each frame it draws, so on a device that cannot draw 60 frames per second the whole game slows down. The low-FPS speed fix runs two or three game ticks for each drawn frame in stages, so a stage keeps its normal speed at 30 or 20 frames per second. **Auto** chooses one, two, or three ticks as the game runs, from how long the game takes and how fast frames reach the screen. When drawing, not the game, holds the frame rate down, Auto keeps the higher count and tries the lower one again after 30 seconds, then less often. The fix needs a release newer than v0.1.0-alpha.3.1.

What to expect:

- Only stages change. Menus, the pause menu, cutscenes, and movies still draw one frame per tick, so they slow down on a slow device as before. The fade into and out of a stage also counts drawn frames, so it takes longer.
- A button press counts once, even when a frame runs several ticks.
- Sound effects from the ticks of one frame can start together, up to about 33 ms early at 20 frames per second.
- The level-up message window is drawn once for each tick, over itself, so it can look more opaque.
- In the 100-ring mission, the game checks for 100 rings once per drawn frame, so the mission can end up to two ticks after the 100th ring. If you lose rings in those ticks, collect 100 again.
- Replays and tests never use the fix.

On Android, choose it under **Low-FPS speed fix (experimental)** in **Game settings**. On Windows and Linux, add one line to the release's `share/game.env` file:

```
SA2_PLAY_FRAMESKIP=2
```

Use `2` for 30 frames per second, `3` for 20 frames per second, or `auto`. On Windows the file is `share\game.env` in the extracted folder. For the Linux package it is `/usr/lib/opensa2/share/game.env`, which you edit as root; a package update replaces it. Setting `SA2_PLAY_FRAMESKIP` in a shell has no effect, because the launcher passes only `game.env`'s settings to the game.

Each session's game log starts with a line that names the setting, or says why a value was not used. During stage play it adds a line every 10 seconds: the frames drawn at each tick count, frame times, and the stage timer's progress against the wall clock, where 100% means the stage ran at its normal speed.
