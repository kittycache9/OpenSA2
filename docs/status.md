# Current development status

OpenSA2 is an experimental port with Windows, Android, and Linux downloads. It has reached live gameplay on desktop and Android devices. It has not completed a full-game compatibility pass.

This status reflects development on 2026-10-16. The [prereleases](downloads.md) use tested development snapshots. A fresh checkout can rebuild the translated game image from your own executable and check it against recorded hashes (see the [developer guide](getting-started.md#translate-the-game-image)). A release build from that image still needs pinned tools and host dependencies that you set up yourself.

Claims on this page use the [method's claim vocabulary](method/principles.md#claim-vocabulary). "Reaches", for example, means a scripted run arrives at a point with no traps, and says nothing about whether the behavior on the way was correct. [Verification](method/verification.md) lists what each phrase proves and what it does not.

## Platforms

| Platform | What has been exercised | What the result means |
| --- | --- | --- |
| Linux, x86-64 | Native gameplay, sound, input, installation, startup, and renderer comparisons. | An established development path with incomplete game coverage. |
| Windows, x86-64 | Native installation, startup, and live gameplay. | Tested on native Windows as well as under Wine during development. Wider hardware coverage is unfinished. |
| Android, arm64 | Touch gameplay on a Galaxy S25 Edge and a Pixel 9 Pro. External-controller input is implemented. | Experimental device testing. GPU behavior, temperature, and the game path can affect results. |
| Linux, arm64 | Cross builds and registered replays under QEMU. | No general native-hardware support claim. |
| macOS | Shader-conversion and portability work remain. | Future work with no supported build. |

## What is implemented

The runtime can boot the original game logic and reach stages. Graphics, video, and decoded audio pass through the player. Desktop keyboard and controller input are implemented, including controller hot-plug. Android has touch controls, external controller input, portrait play, and a settings screen for the picture and the controls.

Metal Harbor has been played to the goal ring on the desktop renderers. City Escape has run on the tested Android devices. The Hero story's movies play from 0.1.0-alpha.2.1: Sofdec's B-picture slice loop (`0x00872650`) sits in the executable section PSFD00, which the address-taken scan did not cover, and earlier builds left every story movie black and waiting. The scan now covers every executable section. Those results do not establish compatibility for every stage, character, boss, story event, or mode.

The desktop installer supports Steam discovery, a selected game folder, and a ZIP made from that folder. It checks the supported files and prepares a separate local installation. The launcher verifies that installation, starts the game and player, and handles session shutdown.

## Known limitations

- From 0.1.0-alpha.3.1, the renderer needs Vulkan 1.1 or later. On a driver that offers only Vulkan 1.1 or 1.2, it uses a reduced Vulkan 1.1 profile: shaders are lowered to SPIR-V 1.3, render state is read as 32-bit words, and the floating-point control modes are not used. This profile has had less testing than the Vulkan 1.3 path, and its output can differ slightly from it. A device with only Vulkan 1.0, or one that lacks a required feature, stops before the first frame with a message that names the reason. The Android setup screen warns when the system reports a version below 1.1. Depth clamping is optional from 0.1.0-alpha.3: during play the renderer draws without it, while replays and tests still refuse the draws that need it.
- Broad stage, boss, event, and game-mode testing is unfinished. Additional missing blocks can exist outside the exercised paths.
- Widescreen rendering is in development. The viewport and main HUD have been adjusted, but several menus and overlays still need work, including the boss gauge, stage map, title cards, and story-select art.
- The portable renderer currently depends on the project's SDL3 patches. Equivalent behavior with stock SDL3 is unfinished.
- The faster floating-point mode used by desktop development builds needs wider gameplay comparisons. A native x87 mode remains available for fidelity work.
- Android support is experimental. The tested phones do not represent every Android GPU or device, and sustained performance depends on temperature and workload.
- The low-FPS speed fix is experimental and off by default. It keeps stages at their normal speed below 60 frames per second by running two or three game ticks for each drawn frame. Menus, cutscenes, and movies still slow down on a slow device, and the **Auto** setting has had little testing on phones.
- A complete public source-build bootstrap is unfinished. A fresh checkout can rebuild and check the translated game image, but the function map, the host libraries, and the release build still require pinned tools and local setup.

## Work still needed

The immediate game-path work is smoke tests across stages and events, including the Dark story's movies and the real-time events that embed movies. Wider testing must then cover long sessions, saves, controllers, and rendering behavior on more hardware. Public build setup and dependency documentation also need work before the checkout can offer a complete build guide.

There are no release dates or platform-support promises. Check this page when reporting an issue, then include the steps and text diagnostics needed to reproduce it. On Android, the setup screen's **Report on GitHub** button copies a crash report and opens the bug-report form.
