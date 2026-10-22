# Start working on OpenSA2

This guide sets up the source preview and runs installer and launcher checks without game files. It also identifies the build tools for developers who already have the required local inputs.

To play the experimental prebuilt version, follow the [download and setup guide](downloads.md). A complete source build needs your own supported 2012 PC installation of *Sonic Adventure 2*, an analyzed executable, translated modules, and built host dependencies. The public checkout does not yet provide a complete bootstrap for those inputs.

## Set up the checkout

Use a Linux development machine with Bash, GCC, a C++17 compiler available as `g++`, Binutils, and Bubblewrap. The sandbox cases use `bwrap` and require working unprivileged user namespaces. Python 3.13 matches the current development environment. Install your distribution's Python virtual-environment package if `python3 -m venv` reports that `ensurepip` is missing. The commands below run from the repository root.

1. Clone the repository.

   ```bash
   git clone https://github.com/kittycache9/OpenSA2.git
   cd OpenSA2
   ```

2. Create a Python environment.

   ```bash
   python3 -m venv .venv
   ```

3. Install the Python dependencies.

   ```bash
   .venv/bin/python -m pip install -r requirements.txt
   ```

4. Create the ignored workspace used by the build-tool imports.

   ```bash
   mkdir -p private/generated
   ```

5. Check the public source.

   ```bash
   python3 tools/public_check.py
   ```

6. Run the synthetic installer and launcher tests.

   ```bash
   .venv/bin/python tools/test_source.py
   ```

The tests build the installer and launcher in temporary directories. They use small synthetic files, hand-built PE images, and stand-ins for the game and player. The command runs the source-only component cases. Comparisons against a local game installation, SDL3 dialog integration, and Windows cross-compilation cases remain in the test modules but are deselected here because they require additional local inputs. A passing result does not run the game or prove gameplay compatibility.

## Find the code for your change

Use the [architecture guide](architecture.md) to choose a starting point. The main areas are:

| Area | Source |
| --- | --- |
| Installation, file verification, and Steam discovery | `host/install/` |
| First launch, session control, and the stream relay | `host/launch/` |
| Graphics, sound playback, and input | `host/player/` and `host/d3d9gpu/` |
| Android setup and native session control | `host/android/` |
| Guest memory, Windows services, and guest devices | `runtime/` |
| Translation and compilation tools | `tools/s2/`, `tools/s3/`, and `tools/spike/` |

## Add a tweak

A tweak is an optional change to the game's behavior that a player turns on with the `SA2_TWEAKS` setting. A tweak adds its own file and lines and changes no existing line, so several tweaks can be written at the same time in separate branches:

1. Add one line for the tweak to `SA2_TWEAK_LIST` in `runtime/sa2_tweaks.h`.
2. Put its wraps in `runtime/tweaks/<name>.c`. A `TWEAK_WRAPS:` line in the file's header comment names the translated functions that the file wraps. The link tools find the file and its wraps without further changes.
3. Add one line for its switch to `TWEAKS` in `host/android/java/com/sa2recomp/app/GameSettings.java`, and its name to one group of `TWEAK_GROUPS` there, which sets the heading it appears under in the Tweaks card.
4. Add a row to the [Tweaks](downloads.md#tweaks) table.

`Documentation/scripts/tests/test_tweaks.py` checks that the list, the files, the Android lines, and the groups agree. The header comment in `runtime/sa2_tweaks.h` gives the rules that every tweak follows.

A Mod Loader patch that changes the game's code bytes cannot be applied as it is, because OpenSA2 never runs the game's original code. Such a patch becomes a code-patch tweak: a spec in `tools/s8/variants/tweaks/<name>.json` takes the place of the runtime file, and `tools/s8/variants/variants.py` translates each function that the patch reaches again from the patched bytes. `tools/s8/variants/README.md` describes the tool, its checks, and its limits.

## Add a host tweak

A host tweak is a tweak whose change the player, the desktop launcher, or the Android app makes. It never changes the game, and it is turned on with the same `SA2_TWEAKS` setting. Its steps also add lines and change none:

1. Add one line for the tweak to `SA2_TWEAK_HOST_LIST` in `runtime/sa2_tweaks.h`, with its home: `player`, `launcher`, or `app`. A host tweak has no line in `SA2_TWEAK_LIST`.
2. Put its code in its home: `host/player/tweaks/<name>.cpp` for the player, `host/launch/tweaks/<name>.cpp` for the launcher, or `host/android/java/com/sa2recomp/app/Tweak<Name>.java` for the Android app. The code checks that its tweak is on first and otherwise does nothing. The builds find the file without further changes, and the player or the launcher gets one call where the tweak acts.
3. For a player or app tweak, add one line for its switch to `TWEAKS` in `host/android/java/com/sa2recomp/app/GameSettings.java`, and its name to one group of `TWEAK_GROUPS`. A launcher tweak has no Android switch.
4. Add a row to the [Tweaks](downloads.md#tweaks) table.

The player and the launcher turn their tweaks on only in play, never in a replay or a session with synthetic input. `Documentation/scripts/tests/test_tweaks.py` checks the host list against the home files and the Android lines. "Add a host tweak" in the header comment of `runtime/sa2_tweaks.h` gives the full rules.

## Understand the full build prerequisites

The game build is a developer workflow, with a Linux build host used for desktop and Android targets. The existing scripts expect a local toolchain and generated files under the ignored `private/` directory.

Before running a full build, prepare these inputs:

- A supported shipped `sonic2app.exe` and the matching unpacked executable used for translation.
- A Ghidra function map, translated LLVM modules, jump-table modules, extra blocks, and generated dispatch tables.
- The pinned compiler and translation tools, including Remill, LLVM-compatible Clang, and Zig for cross builds.
- SDL3 and the renderer dependencies, built with the expected project patches and configuration.
- The supported installation's file-verification table.
- For Android, the SDK, NDK, and Java tools expected by the Android builder.

`tools/env.sh` names the expected local layout. It adds existing tools to `PATH`; it does not install them or generate the game image. Consult each script's module documentation and `--help` where available before using it.

The current build entry points are:

| Task | Entry point | Prerequisite |
| --- | --- | --- |
| Translate mapped game functions | `tools/s3/whole_image/lift_all.py` | The analyzed executable, function map, Remill, and helper modules. |
| Rebuild the translated image after a translation change | `tools/s4/rebuild_image.py` | Existing mapped modules, jump-table modules, and extra-block inputs. |
| Build host dependencies | `tools/s5/build_host_deps.sh` | The pinned source archives, build tools, and platform headers. |
| Assemble a desktop build | `tools/s7/release_build.py` | The generated game image, host libraries, and verification inputs. |
| Compile and link the Android game | `tools/s8/android_game.py` | The translated image and Android toolchain. |
| Build the Android app | `tools/s8/android_app.py` | The Android game, renderer dependencies, and Android toolchain. |

These tools do not form a fresh-checkout build command yet. Keep generated code, binaries, and game files local. Do not commit them.

## Test a locally assembled desktop build

These commands apply only after you have assembled a complete development build. Run them from that build's root directory, which contains `bin/`, `share/`, and a build manifest.

On Linux, install your supported Steam copy and start the launcher:

```bash
bin/sa2-install --source steam --yes
bin/sa2recomp
```

On Windows, use PowerShell:

```powershell
bin\sa2-install.exe --source steam --yes
bin\sa2recomp.exe
```

If Steam discovery cannot find your copy, select the folder that contains `sonic2app.exe`:

```bash
bin/sa2-install --source folder "/path/to/Sonic Adventure 2" --yes
```

The installer also accepts `--source zip` for a ZIP made from your own game folder. It verifies the required files and prepares a separate local installation. Save and configuration writes use the port's own writable directory.

The launcher has a scripted title-to-menu check:

```bash
bin/sa2recomp --selftest
```

This check covers installation and startup. It does not check every stage or event. Read [current limitations](status.md) before a longer session.

To import, export, back up, or restore the desktop game's saves, use `tools/saves.py`. It reads the installer's configuration (`$XDG_CONFIG_HOME/sa2recomp/sa2recomp.cfg`; `--config` names another) and writes only the port's own save folder, never the game folder. Close the game first.

```bash
python3 tools/saves.py show
python3 tools/saves.py import SONIC2B__S01 SONIC2B__ALF
python3 tools/saves.py import saves.zip
python3 tools/saves.py export saves.zip
python3 tools/saves.py backups
python3 tools/saves.py restore BACKUP
```

Each import and restore first backs up the current saves to `$XDG_DATA_HOME/sa2recomp/save-backups/`, and the newest 10 backups are kept. A file whose name or size is not a save's is refused. A Chao World save (`SONIC2B__ALF`) is also refused when its checksum does not match; the check reads the checksum table from the game executable the configuration names (`--exe` names another), and `--force` imports it anyway.

Each launch keeps a session folder under the data folder's `sessions/`. With the session recorder on (`--record-session`, or `SA2_RECORD_SESSION=1` in `share/game.env`, which the launcher reads and does not pass to the game; on Android, **Record sessions** in the Debug card), it also keeps the saves and `Config/` from before and after the game, `session-info.json` and the pad log, which a replay needs. The recorder is off by default. `bin/sa2recomp --debug-export [SESSION] [--out FILE.zip]` writes one session (the newest by default) to a ZIP, and the Android app's **Game settings** > **Debug** > **Debug Export** does the same on a device. `host/launch/sa2_debug_export.h` and `host/launch/sa2_session_files.h` describe the ZIP's layout. To replay an export headlessly with the release in `private/generated/s7/release/linux-x86_64` and compare its game-mode transitions with the session's:

```bash
.venv/bin/python tools/s7/replay_export.py sa2-debug-SESSION.zip
```

To put a session's saves in place instead, `tools/saves.py import sa2-debug-SESSION.zip` imports the saves the session ended with, and `--from start` the ones it started from; the Android **Saves** card asks which.

It replays the keyboard from the session's `input.log` and the controllers from its `pad.log`. The game logs controller changes to the file `SA2_INPUT_PAD_LOG` names and replays a log given by `SA2_INPUT_PAD_SCRIPT`; with both unset, it does neither. `runtime/sa2_dinput.h` describes the format.

To try the low-FPS speed fix in a development build, add `SA2_PLAY_FRAMESKIP=2`, `3`, or `auto` to the build's `share/game.env`. The live-play tool `tools/s5/play_live.py` takes `--frameskip 2|3|auto` instead, and it removes a `SA2_PLAY_FRAMESKIP` left in your shell. Registered replays and test runs never use the setting. The game's log reports what the fix did; `runtime/sa2_frameskip.c` describes the mechanism.
