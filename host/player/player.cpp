// player.cpp: replays a recorded Direct3D 9 call stream (host/sa2_stream.h)
// into DXVK Native's d3d9 in an SDL3 window, so the lifted game's frames reach
// the screen without DXVK ever entering the game process (the game binary is a
// static musl executable that cannot load it; see the S5 DXVK critic's
// section 0). The player is glibc C++, dlopens DXVK by absolute path and links
// SDL3 from private/s5-tools/install.
//
//   sa2_player STREAM [options]
//     --stage N         0: Clear/BeginScene/EndScene/Present only
//                       1: + shaders, declarations, constants, render and sampler
//                          states, viewport, scissor, draws; every texture a 1x1
//                          white placeholder
//                       2: + DDS file textures, with the file's level count
//                       3: + lock uploads, PNG, D3DX mip chains (box filter),
//                          render targets (default)
//     --frames-out F.y4m  every frame read back (GetRenderTargetData before
//                       Present) as YUV4MPEG2 4:2:0, BT.709 limited range, with
//                       F.y4m.times.tsv giving each frame's virtual time; both
//                       files are truncated when the run starts
//     --snap N[,N..]    write frame N (1-based Present count) as PNG to --snap-dir
//     --snap-dir D      default: $SA2_PLAY_OUT/snaps
//     --pixel N:X:Y     print frame N's pixel (X, Y) as BGRA (repeatable)
//     --expect F.tsv    check pixels listed by sa2_synth_stream; exit 1 on a miss,
//                       or when the file selects nothing for the stage
//     --dump-draws N    print every draw of frame N with its state
//     --pace            present at the recorded virtual time (--speed S scales it);
//                       on a live stream the schedule moves after a frame later than
//                       the sound's lead (pace.h); the report gains "pace"
//     --pace-log F.tsv  with --pace: one row per Present, its virtual time, when it
//                       was ready, due, presented, how far the schedule moved, and
//                       with --audio the playback queue's depth (see "Pace" below)
//     --vsync           PresentationInterval ONE (default IMMEDIATE)
//     --hidden          create the window hidden
//     --max-frames N    stop after N Presents
//     --stop-after-draw F:N  offline stage 3 diagnostic: stop after original
//                       draw N of Present frame F; report a separate BGRX
//                       checkpoint, never a completed frame or full replay
//     --report F.json   write the run's verdict, counts, pins and environment
//     --report-every N  during the replay, rewrite the report every N Presents
//                       (default 100; 0: only at the phases below and at the end)
//     --stats-only      replay the stream with no device: every check that needs
//                       none, and no window, no DXVK (see "--stats-only" below);
//                       at stage 3, the only stage it takes
//     --allow-truncated play a stream with no END record without failing it
//     --allow-notes     play a stream with NOTE records without failing it
//     --dxvk PATH       libdxvk_d3d9.so.0 (default $SA2_DXVK_D3D9); sa2_player only
//     --input-path FIFO with --interactive: the window's keyboard goes to the
//                       game through FIFO (input.h); see "Input" below
//     --key-log F.tsv   actual sender changes, with --input-path
//     --present-log F.tsv  host Present call/return timestamps; needs a device
//     --synthetic-keys F.tsv  own-queue diagnostic with both logs and a positive
//                       live --max-frames; never playable evidence. Lines "MS SCANCODE
//                       down|up", or "MS 0 close" for the window's close request, MS
//                       after the first Present's return. The window's own key events
//                       are then counted and not sent (real_keys_ignored)
//     --end-fd N        with --interactive: a close request asks the caller to end
//                       the session (one byte on N) instead of stopping the run; see
//                       "Input" below
//     --video-pipe NAME, --cancel-pipe NAME, --end-pipe NAME  Windows: --video-fd's,
//                       --cancel-fd's and --end-fd's channels as named pipes the caller
//                       serves (sa2recomp's play), opened by name; the caller's end
//                       request is a named event in place of SIGUSR1 (watch_end_event)
//     --audio          play the stream's sound (a version 2 stream's AUDIO
//                       records, mixer.h) on SDL's default playback device; see
//                       "Sound" below. Not with --stats-only, nor with --speed
//                       other than 1
//     --audio-wav F.wav write the stream's sound as a 48 kHz stereo float WAV,
//                       and F.wav.json beside it; no device needed, so with
//                       --stats-only too
//
// Two binaries are built from this file. sa2_player (host/player/build.sh) is the
// DXVK host, as everywhere above. sa2_player_gpu (host/d3d9gpu/build.sh, with
// SA2_PLAYER_GPU=1) is the second host: host/d3d9gpu's Direct3D 9 on SDL_gpu (no
// shader route yet, so it counts every draw and draws none) linked in statically, so
// its renderer is its own file, pinned as files.player like every player's. It loads
// no DXVK library and reads no dxvk.conf: it pins neither, verifies neither, expects
// neither (both null in its report), refuses --dxvk, and leaves DXVK's preconditions
// (DXVK_WSI_DRIVER, DXVK_CONFIG_FILE, DXVK_CONFIG) out. Its report has the DXVK
// player's fields, host_identity.d3d9_host naming which binary wrote it. Its first
// argument may be --clear-matrix [--hidden]: host/d3d9gpu/test/clear_matrix.h through
// its own Direct3DCreate9, one line per case (the SDL_gpu leg of
// host/d3d9gpu/test_d3d9gpu.sh step 2 and compare_hosts.py's check i).
//
// Output paths (--report, --frames-out and its times file, --snap-dir, --audio-wav
// and its .json, --pace-log, and DXVK's log directory, DXVK_LOG_PATH or else the current directory) must lie
// outside every git working tree, or inside its private/ directory (AGENTS.md
// rule 9: game frames are captures). Anything else is refused before a byte is
// written: the report's own path first, and then the rest, whose refusal the
// report records.
//
// Exit status (the report's "exit_status"; "failures" names every reason):
//   0  pass
//   1  an --expect pixel missed, or --expect selected nothing for the stage
//   2  usage, a refused output path, or a file that could not be read or written
//   3  the stream: open refused (magic, version, schema, header tables), a
//      malformed record, a seq gap, a record after END, no END (unless
//      --allow-truncated or the run stopped early by choice), an argc that
//      disagrees with the header's tables (for a D3DX record in a stream whose
//      header has no "entries" table, with host/sa2_stream.h), a call or entry
//      the tables do not name, an entries row that names another function than
//      the one this player replays under its number, an IDirect3D9 call on an
//      object no Direct3DCreate9 record answered (when the header says the
//      writer records it), a record kind version 1 does not have (the header
//      lets a reader skip one; this player cannot vouch for what it skipped),
//      a FRAME with no Present or a Present with no FRAME, virtual time running
//      backwards (in a version 2 stream, across FRAME and AUDIO records), an
//      AUDIO record whose op or argc the header's audio table does not give, any
//      NOTE record (the writer left something out; unless --allow-notes)
//   4  the replay: device creation failed, no Present replayed, an error the
//      player counted (among them a GetRenderTarget, GetDepthStencilSurface or
//      GetBackBuffer answer the bindings the player followed do not give), a
//      call, d3dx entry or argument value not handled (among them a
//      GetBackBuffer answer other than the implicit back buffer), a host call that
//      failed where the guest was answered success (except the named scene-order
//      rules below), a readback or Present that failed, the window closed; with
//      --audio or --audio-wav, an audio problem (mixer.h: a version 1 stream,
//      which holds no sound, among them), or no playback device for --audio
// The most basic class present decides the status: 2, then 3, then 4, then 1.
//
// The report is written on every exit after the arguments parse, unless its
// own path is refused. It is also written before it is final ("complete":
// false, "phase" says where): first of all (phase "start"), so a player that
// dies at any point leaves this run's report and never an earlier run's; just
// before Direct3DCreate9, because DXVK can abort the process there (see
// create_device); before CreateDevice; when the device is made (phase
// "replay", 0 Presents); and after every --report-every'th Present, with the
// counts so far. A report that is not final therefore counts K Presents where
// the run had made at least K and at most K + N (N = --report-every; with 0,
// no bound): tools/s5/play_report.py turns a signal exit into a verdict that
// names the phase and says so.
//
// --stats-only is a replay with no device. It runs the replay's own code at
// stage 3 with every host call left out: every stream check above, each
// call's and entry's plan (call_plan, kEntries), and every check a replay
// makes of a call's arguments, blobs and objects that needs no device (a blob
// missing or short, a shader or declaration without its terminator, an object
// no earlier call made, an Unlock with no Lock, a level outside its texture, a
// lock rectangle, a DESC or LOCK blob that disagrees with the call, a file
// that does not parse, an argument value the player does not implement). Each
// object a replay would make stands as made (Tex::made, CubeVol::made, Surf::made,
// Buf::made); texture levels are decoded, converted and filtered on the CPU as
// in a replay, and nothing is uploaded. It cannot see what only a device
// shows: device creation (the window, DXVK, CreateDevice), host answers (a
// host call that fails, and the objects a failed one would leave unmade),
// readbacks and pixels. So its counts and errors equal a stage 3 replay's
// wherever no host call fails.
//
// Cube and volume textures (IDirect3DDevice9::CreateCubeTexture and the four D3DX
// cube and volume creators) are made as 2D ones are, at the same stages: a DDS cube
// map's six faces or a DDS volume's slices at stage 2, their box-filtered mips, the
// plain creators and a cube face's lock upload (the surface GetCubeMapSurface
// answers) at stage 3. Each GetLevelCount, GetLevelDesc and GetCubeMapSurface
// answer is held to the creation the player followed and, where the host object has
// the level, to the host's answer. No game shader samples a cube or a volume, so a
// draw while one is bound is not handled, by name, and draws nothing.
//
// Input: every SDL input event is discarded (the guest's input is the
// recording's) and counted by kind; events are pumped every frame, while
// --pace sleeps, and while a live stream (STREAM "-" or a path that is a pipe
// or a socket) waits for its next bytes (stream.h, set_idle). A close request
// stops the run, which then fails ("the window was closed"). With --interactive
// --end-fd N, the first close request is instead an end request: every key is let up
// and the key sender closed (no key after it reaches the game), one byte is written to
// N for the caller (tools/s5/play_live.py) to end the game, and the run reads on to the
// stream's END, so a session closed by hand can still be final ("complete"). The window
// stays until then; a close request kForceCloseAfterS or more after the end request
// stops the run as before. SDL's quit on the last window's close is turned off in this
// mode, so one click is one request. SIGUSR1 is the caller's own end request, taken the
// same way (play_live sends it at the goal and at a Ctrl-C, so that the keys are let up
// before it stops the game). The report's "end_request" says what happened.
// The frames do not depend on window focus or occlusion: each is read back before its
// Present.
// With --interactive --input-path FIFO, and only then, the window's own key
// events also go to the game, as the DirectInput keyboard state the runtime
// reads from SA2_INPUT_DI_SOURCE (input.h, runtime/sa2_dinput.h); losing focus
// or closing lets every key up. The report gains "host_input" from the run's
// first report on, and its "started" turns true once the window is up and the
// FIFO checked.
//
// Gamepads: with --interactive --input-path, and only then, SDL's gamepads and the touch
// overlay (touch_overlay.h) go to the game too, as the four XInput slots of the FIFO's gamepad
// records (input.h send_pads, runtime/sa2_dinput.h "The host gamepads"). The game makes its pad
// objects once, at boot, for the slots then connected, so the pads are sent as soon as the FIFO
// is up, before the stream is read: every pad SDL knows then, in slots 0 to 3 in the order SDL
// lists them, and later ones in the order they arrive, each keeping its slot until it goes. The
// touch overlay is slot 0, connected from the start where touch is the platform's input
// (Android; SA2_TOUCH_CONTROLS=1 anywhere, 0 nowhere) and otherwise from its first touch; a
// physical pad given slot 0 then shares it with the overlay (buttons and triggers combined, each
// stick the one pushed further), so a phone's Bluetooth pad is player one with or without
// touches. A physical pad's button hides the overlay until the next touch. A press and its
// release are a record each; stick and finger motion is sent once per event pump. SDL raises
// the events on whichever thread it reads them on (Android's touches come from its UI thread):
// an event watch copies them, and the reader's idle service handles them here (service_pads),
// so the pads are set up at the first service of the live open, before the stream's first
// byte. Pads are SDL's default: no presses while the window is not focused. The overlay is
// drawn over each frame on the swapchain texture, after the frame is read back (so no frames
// file holds it), when the second host's device calls the function the window's property
// "sa2.present_overlay" holds at Present (host/d3d9gpu/d9g_gpu_sdl.cpp), with the frame and the
// swapchain texture; nothing is drawn before the first touch. The frame's size goes to the
// overlay at each Present, which then knows where the picture is (its controls go below a
// portrait window's picture). The overlay's settings (SA2_TOUCH_SCALE, SA2_TOUCH_OPACITY,
// SA2_TOUCH_LAYOUT, SA2_TOUCH_LAYOUT_PORTRAIT, SA2_TOUCH_MAP: touch_overlay.h) and SA2_PAD_MAP are
// read from the environment when the pads are set up, each said in the log ("pads: ..."), a
// malformed one with why it is not used. SA2_PAD_MAP=BUTTON=OUTPUT,... changes what a physical
// pad's button sends: BUTTON an SDL button by position (SOUTH EAST WEST NORTH BACK GUIDE START LS
// RS LB RB UP DOWN LEFT RIGHT MISC1 PADDLE1-4 TOUCHPAD), OUTPUT a game button (input.h
// pad_output_named; LT and RT hold that trigger all the way). The report's "host_pads" counts
// the pads, the records asked for and the overlay's draws.
//
// Which pad is player one: SA2_PAD_P1=ID:vvvv:pppp (hex vendor and product) or NAME:<device name>
// gives the pad so named (its joystick name and ids, as each pad is said in the log: "pads: added
// ...") slot 0, whichever order SDL lists the pads in, the pad it displaces taking the slot the
// newcomer would have had; of several pads that match, the first keeps slot 0. SA2_PAD_P1=ALL sends
// every pad, and the overlay, as slot 0 (buttons and triggers combined, each stick the one pushed
// furthest) and leaves slots 1 to 3 empty, for a handheld whose controls SDL lists as several
// pads. Unset, or a name no pad has, the slots are as before.
//
// The picture's place (portrait play): SA2_PLAYER_PICTURE_Y=F, F 0 to 1, puts a letterboxed
// picture's centre at F of an upright window's height (0.25: the middle of its upper half), its
// top no higher than the window's safe area and no lower than centred; unset, or a window no taller
// than wide, the picture is centred as before. Before each Present the player gives the device that place and the safe area's top in
// pixels as the window's properties "sa2.picture_y" and "sa2.picture_min_top" (read beside
// host/d3d9gpu/d9g_gpu.h fit_rect), and the touch overlay the same (set_picture), so its band
// under the picture starts where the picture ends. Each new place is said in the log ("picture:
// WxH at rows T-B of the window ...").
// --synthetic-input F.tsv is the test of all this (Documentation/scripts/tests/
// test_player_pads_touch.py): lines "MS VERB ARGS", MS after the first Present: "pad-attach K",
// "pad-button K BUTTON 0|1", "pad-axis K AXIS VALUE", "pad-detach K" drive SDL virtual gamepad
// K (SDL_GamepadButton and SDL_GamepadAxis numbers), and "touch-down|touch-move|touch-up FINGER
// X Y" push finger events, X and Y normalised to the window, or "... FINGER @CONTROL [DX DY]" at
// an overlay control's centre (STICK, A, B, X, Y, START, LT, RT) moved by DX, DY stick radii;
// only virtual gamepads are then taken, and SDL's gamepad events are taken without window
// focus. --overlay-out DIR writes, for the first Presents, the frame as the device blits it to
// the swapchain and the same with the overlay over it (DIR/frame-N-game.png and
// DIR/frame-N-swapchain.png), rebuilt beside the swapchain, which cannot be read.
//
// Sound: with --audio or --audio-wav, every AUDIO record goes to the mixer
// (mixer.h) in stream order, and every Present closes the block of sound up to its
// FRAME's virtual time; what the records after the last FRAME render is the last
// block, closed at the end. Every block is written to --audio-wav at once, so the WAV
// holds the whole of the sound from the mixer's origin, the first timed record. With
// --audio the blocks also go to SDL's playback stream (float32 stereo 48 kHz), each
// queued as soon as its FRAME is read, before --pace waits for the frame's time --
// all but the first: the sound before the first Present is the WAV's alone, and the
// device gets kAudioLeadMs of silence in its place. So, while the replay keeps pace,
// the queue holds about that lead whenever a block is queued and the device never
// waits on the replay, and the sound plays kAudioLeadMs (and the device's own
// latency) after the frame it belongs to, whatever the stream. A block that finds the
// queue past kAudioQueueBound is dropped and counted, which is what an unpaced
// replay, faster than real time, does with its sound; --speed other than 1 is refused
// with --audio, since the sound plays at its own rate. The report's "audio" holds the
// mixer's counters, the device's (blocks queued and dropped, the lead, the queue's
// depth each time a block was queued, and its shortfalls: requests of the device the
// queue fell more than a frame short of while the replay was feeding it, counted in
// SDL's get callback, each a gap the listener heard) and the WAV's, and is there
// only when sound is asked for; without either option a version 1 stream plays
// exactly as it did before sound existed, and a version 2 stream's AUDIO records are
// counted and held to the header's table and nothing more. The WAV's .json names its
// first sample's virtual time and the first FRAME's, which
// tools/s5/frames_to_video.py aligns the sound to the video by.
//
// Pace: with --pace each Present waits for its frame's time (pace.h). A live stream
// (--interactive's) may pace too: a game ahead of real time is then held to it by the
// stream itself, since the player reads no further while it waits and the writer's pipe
// fills (tools/s5/play_live.py bounds what its relay holds in between). Its schedule
// moves when a frame is later than kAudioLeadMs, so a game that falls behind is never
// shown fast afterwards to make up more than that. The report's "pace" counts the
// waits, the lateness and the moves, and the virtual time over the wall time between
// the first paced Present and the last; --pace-log writes the same per frame, on
// CLOCK_MONOTONIC (the clock of "t_start_monotonic_s"), for another process's clock to
// be set beside.
//
// Host tweaks (tweaks.h; runtime/sa2_tweaks.h lists every tweak, the game's and the host's):
// SA2_TWEAKS, read once, in a play session alone (--interactive with --input-path, no synthetic
// input, no SA2_REPLAY_TICKS or SA2_REPLAY_WORDS: the game's, which the launcher and the Android
// session give the player beside SA2_TWEAKS), and one stderr line, "sa2 host tweaks (player):
// ...", whenever it is set. Each tweak whose home is the player is its own file,
// host/player/tweaks/<name>.cpp, and tests sa2tw::on first: with none named, and in every replay,
// the player is the build it was.
//
// The window, measured: at every Present the window's SDL flags are sampled
// just before the Present, and the report's "window" keeps them by frame
// ("flags_at_present": runs of [first frame, last frame, SDL_WindowFlags], with
// "flag_bits" naming the bits), with counts, the window and input events with
// their time and frame, and how long the window went unpumped.
// "t_start_monotonic_s" is the CLOCK_MONOTONIC time the times file's wall_us
// counts from, so another process's clock can be set beside each frame's
// read-back. On Wayland only two of the flags come from the compositor:
// keyboard focus (wl_keyboard.enter) and OCCLUDED, which SDL sets only from
// xdg_toplevel's suspended state; SDL sets HIDDEN and MINIMIZED only when the
// application asks, and this player never does.
#include <SDL3/SDL.h>
#include <SDL3/SDL_vulkan.h>
#include <SDL3/SDL_revision.h>
#ifdef __ANDROID__
// Android (contract D): the app's SDLActivity loads this player into its own process and calls
// SDL_main, which SDL_main.h makes of main() below; the game is a separate executable.
#include <SDL3/SDL_main.h>
#endif
#include <windows.h>
#include <d3d9.h>

#ifdef _WIN32
// The Windows build is the second host's alone (tools/s7/host2_win_build.py): DXVK Native, which
// the first host dlopens, is a Linux library. The few POSIX calls the second host makes have
// their Windows forms where they are made (SDL's module, this executable's file, the signals).
#if !SA2_PLAYER_GPU
#error "only sa2_player_gpu (SA2_PLAYER_GPU=1) is built for Windows"
#endif
#include <fcntl.h>
#else
#include <dlfcn.h>
#endif
#include <time.h>
#include <unistd.h>
#include <cerrno>
#include <csignal>
#include <climits>

#include <algorithm>
#include <atomic>
#include <cinttypes>
#include <cmath>
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <map>
#include <memory>
#include <set>
#include <string>
#include <unordered_map>
#include <vector>

#include "input.h"
#include "mixer.h"
#include "names.h"
#include "pace.h"
#include "prof.h"
#include "sdl_mark.h"
#include "sha256.h"
#include "stream.h"
#include "texcodec.h"
#include "touch_overlay.h"
#include "tweaks.h"
#include "tweaks/camera-invert.h"
#include "tweaks/deadzone.h"
#include "tweaks/frametime-overlay.h"
#include "tweaks/hold-toggle.h"
#include "tweaks/mono-audio.h"
#include "tweaks/pause-unfocused.h"
#include "tweaks/slow-mode.h"
#include "tweaks/swap-audio.h"
#include "tweaks/tex-filter.h"
#include "tweaks/vsync.h"
#include "sa2_host_expectations.h"

#ifdef _WIN32
// The C library's rename refuses an existing destination on Windows, where rename(2) replaces
// it; a report is published as a temporary file renamed over the last one. 0, or -1 with errno.
static int sa2_replace_rename(const char* from, const char* to) {
    std::error_code ec;
    std::filesystem::rename(std::filesystem::path(from), std::filesystem::path(to), ec);
    if (!ec) return 0;
    errno = ec.default_error_condition().category() == std::generic_category() ? ec.default_error_condition().value()
                                                                               : EIO;
    return -1;
}
#endif

// Which binary this is (see "Two binaries" above): 0, sa2_player on DXVK; 1, sa2_player_gpu
// with host/d3d9gpu linked in. Each build's expectations header holds only its own host's
// contract (host/player/generate_expectations.py --host), so neither build can name the
// other's: no host/d3d9gpu header is included here, and the GPU build's two entry points
// are declared below (Direct3DCreate9 by DXVK's d3d9.h).
#ifndef SA2_PLAYER_GPU
#define SA2_PLAYER_GPU 0
#endif
#if SA2_PLAYER_GPU
int sa2_clear_matrix_main(int argc, char** argv);   // host/d3d9gpu/test/clear_matrix.cpp
#endif

using namespace names;
namespace fs = std::filesystem;

namespace {

constexpr bool exact_string(const char* a, const char* b) {
    while (*a && *a == *b) { ++a; ++b; }
    return *a == *b;
}
static_assert(SDL_VERSION == sa2_host_expected::sdl_version, "SDL header version contract changed");
static_assert(exact_string(SDL_REVISION, sa2_host_expected::sdl_revision), "SDL header revision contract changed");
#if SA2_PLAYER_GPU
static_assert(sizeof(sa2_host_expected::sdl_sha256) == 65, "missing library expectations");
#else
static_assert(sizeof(sa2_host_expected::sdl_sha256) == 65 && sizeof(sa2_host_expected::dxvk_sha256) == 65 &&
              sizeof(sa2_host_expected::dxvk_config_sha256) == 65, "missing library expectations");
#endif
// The host this binary is, as its report names it (host_identity.d3d9_host), and what can
// hide a frame from it (the report's host.focus_or_occlusion).
#if SA2_PLAYER_GPU
constexpr const char* kD3d9Host = "sdlgpu";
// The basis is SDL_gpu's: no swapchain texture while the window is hidden, occluded or
// minimised, which the device's report counts; it presents in the swapchain mode the
// window offers (d3d9gpu's report, present.swapchain_mode: MAILBOX where IMMEDIATE is not).
constexpr const char* kOcclusionFrames = "cannot change a frame: read back before Present; SDL_gpu gives no swapchain "
                                         "texture while the window is hidden, occluded or minimised (counted in "
                                         "d3d9gpu's report)";
constexpr const char* kUnpacedTiming = "unpaced, in SDL_gpu's swapchain mode (d3d9gpu's report): not expected to move";
#else
constexpr const char* kD3d9Host = "dxvk";
constexpr const char* kOcclusionFrames = "cannot change a frame: read back before Present; DXVK's SDL3 WSI reports no "
                                         "occlusion";
constexpr const char* kUnpacedTiming = "IMMEDIATE and unpaced: not expected to move";
#endif

constexpr uint32_t kD3dxDefault = 0xFFFFFFFFu;
// The playback queue's bound with --audio: half a second of 48 kHz stereo float.
constexpr int kAudioQueueBound = int(Sa2Mixer::kRate) * 8 / 2;
// The device's lead with --audio: the silence it is given at the first Present, which
// the sound then plays behind its frames by (the "Sound" paragraph above).
constexpr int kAudioLeadMs = 60;
constexpr int kAudioFrameBytes = int(Sa2Mixer::kChannels * sizeof(float));
// The least time between two of a live session's service pumps (Player::on_idle).
constexpr double kLivePumpGapS = 0.002;
// A live session's reader services (Player::on_idle) between two reads of the clock while it
// reads without waiting: 240 million services in a 110 s Wine session, about 2 a microsecond, so
// one clock read in kLiveClockEvery keeps the pump within a fraction of kLivePumpGapS. A service
// after the reader waited reads it at once.
constexpr uint32_t kLiveClockEvery = 64;
// A live session's mixer hashes every kLiveStoreCheckEvery'th store an Unlock closes against
// the recorder's store_hash, not every one (mixer.h, "The stores"): each hash is one byte at a
// time over the whole store, and hashing them all was 6.4% of a live player's samples in a stage
// (the thermal unit's profile, 2026-10-12). Replays hash every one.
constexpr uint32_t kLiveStoreCheckEvery = 64;
// --end-fd: a close request this long after the end request stops the run (one sooner is
// the same click repeated while the game ends, and is counted).
constexpr double kForceCloseAfterS = 3.0;
// --end-fd: SIGUSR1 is the caller's end request (tools/s5/play_live.py's, at the goal, a
// Ctrl-C or a limit), taken at the next event pump as a close request is: the keys let up
// and the sender closed before the caller stops the game, so no key record is written that
// the game will not read.
volatile sig_atomic_t g_end_signal = 0;
void on_end_signal(int) { g_end_signal = 1; }
#ifdef _WIN32
// The Windows interactive mode (sa2recomp's play, host/launch/sa2_launch_win32.cpp). The
// session's channels are named pipes the launcher serves, given by name: --video-pipe and
// --cancel-pipe are opened as their client for reading, --end-pipe for writing, each as a C
// runtime descriptor (_open_osfhandle), so the live reader (stream.h open_live), the end
// request's write and every close are the POSIX code's. Windows has no SIGUSR1: the caller's
// end request is the named event kEndEventPrefix + this process's id, which a thread waits on
// and turns into g_end_signal, as the signal's handler does (sa2_launch_win32.cpp send_signal's
// User1 sets it).
constexpr const char* kEndEventPrefix = "Local\\sa2recomp-player-end-";
int open_session_pipe(const char* name, bool write, std::string* error) {
    HANDLE h = INVALID_HANDLE_VALUE;
    DWORD e = 0;
    for (int tries = 0; tries < 2; tries++) {
        h = CreateFileA(name, write ? GENERIC_WRITE : GENERIC_READ, 0, nullptr, OPEN_EXISTING, 0, nullptr);
        if (h != INVALID_HANDLE_VALUE) break;
        e = GetLastError();
        if (e != ERROR_PIPE_BUSY) break;
        WaitNamedPipeA(name, 5000);
    }
    if (h == INVALID_HANDLE_VALUE) {
        *error = std::string("cannot open the named pipe ") + name + " (Windows error " + std::to_string(e) + ")";
        return -1;
    }
    const int fd = _open_osfhandle(reinterpret_cast<intptr_t>(h), (write ? _O_WRONLY : _O_RDONLY) | _O_BINARY);
    if (fd < 0) {
        CloseHandle(h);
        *error = std::string("no C runtime descriptor for ") + name;
    }
    return fd;
}
DWORD WINAPI end_event_thread(void* event) {
    if (WaitForSingleObject(static_cast<HANDLE>(event), INFINITE) == WAIT_OBJECT_0) g_end_signal = 1;
    return 0;
}
void watch_end_event() {
    const std::string name = kEndEventPrefix + std::to_string(GetCurrentProcessId());
    HANDLE ev = CreateEventA(nullptr, TRUE, FALSE, name.c_str());
    if (!ev) return;                              // no event: the window's close still asks
    HANDLE t = CreateThread(nullptr, 0, end_event_thread, ev, 0, nullptr);
    if (t) CloseHandle(t);
}
#endif

// What SDL's device thread asks of the playback stream (SDL_SetAudioStreamGetCallback):
// every request while the replay feeds it, and the ones the queue fell more than one
// frame short of, which SDL fills with silence. More than one frame, because SDL's
// estimate of a request it resamples may run a frame over (SDL_audiocvt.c,
// SDL_GetAudioStreamDataAdjustGain); a device at 48 kHz asks exactly. The callback runs
// on SDL's thread, hence the atomics.
struct AudioTap {
    std::atomic<bool> feeding{false};
    std::atomic<uint64_t> requests{0}, shortfalls{0}, shortfall_bytes{0};
};
void SDLCALL on_audio_request(void* userdata, SDL_AudioStream*, int additional_amount, int) {
    AudioTap* t = static_cast<AudioTap*>(userdata);
    if (!t->feeding.load(std::memory_order_relaxed)) return;
    t->requests.fetch_add(1, std::memory_order_relaxed);
    if (additional_amount > kAudioFrameBytes) {
        t->shortfalls.fetch_add(1, std::memory_order_relaxed);
        t->shortfall_bytes.fetch_add(uint64_t(additional_amount), std::memory_order_relaxed);
    }
}
constexpr int kExitPass = 0, kExitExpect = 1, kExitUsage = 2, kExitStream = 3, kExitReplay = 4;
constexpr uint32_t kFilterBox = 5;                                // D3DX_FILTER_BOX
constexpr uint32_t kFilterDitherBits = 0x80000u | 0x100000u;      // D3DX_FILTER_DITHER, _DITHER_DIFFUSION
constexpr uint32_t kVpBits = D3DCREATE_SOFTWARE_VERTEXPROCESSING | D3DCREATE_HARDWARE_VERTEXPROCESSING |
                             D3DCREATE_MIXED_VERTEXPROCESSING;

double now_s() {
    timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return double(ts.tv_sec) + double(ts.tv_nsec) * 1e-9;
}
// Input latency clock and publication helpers.
bool checked_monotonic_s(double* seconds) {
    timespec ts{};
    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0 || ts.tv_sec < 0 || ts.tv_nsec < 0 || ts.tv_nsec >= 1000000000L)
        return false;
    const double value = double(ts.tv_sec) + double(ts.tv_nsec) * 1e-9;
    if (!std::isfinite(value) || value < 0) return false;
    *seconds = value;
    return true;
}
double key_send_clock_s() {
    double value = -1;
    return checked_monotonic_s(&value) ? value : -1;
}
enum class EventTimingStatus { Ok, MonotonicFailure, FutureTimestamp, AgeOutOfRange };
struct EventTiming {
    double handled_s;
    uint64_t age_ns;
    EventTimingStatus status;
};
EventTiming event_timing(double handled_s, bool clock_ok, uint64_t ticks_now_ns, uint64_t timestamp_ns) {
    if (!clock_ok || !std::isfinite(handled_s) || handled_s < 0)
        return {-1, 0, EventTimingStatus::MonotonicFailure};
    if (timestamp_ns > ticks_now_ns) return {handled_s, 0, EventTimingStatus::FutureTimestamp};
    const uint64_t age = ticks_now_ns - timestamp_ns;
    if (double(age) / 1e9 > handled_s) return {handled_s, 0, EventTimingStatus::AgeOutOfRange};
    return {handled_s, age, EventTimingStatus::Ok};
}
bool canonical_u64(const std::string& text, uint64_t* result) {
    if (text.empty() || (text.size() > 1 && text[0] == '0')) return false;
    uint64_t value = 0;
    for (const unsigned char c : text) {
        if (c < '0' || c > '9' || value > (UINT64_MAX - uint64_t(c - '0')) / 10) return false;
        value = value * 10 + uint64_t(c - '0');
    }
    *result = value;
    return true;
}
struct SyntheticKey {
    uint64_t offset_ns;
    SDL_Scancode scancode;
    bool down;
    bool close = false;     // "close": the window's close request, as the window manager's
};
bool publish_report_file(const std::string& path, const std::string& body, std::string* error) {
    const std::string tmp = path + ".tmp";
    FILE* f = std::fopen(tmp.c_str(), "w");
    if (!f) { *error = "cannot write " + tmp + ": " + std::strerror(errno); return false; }
    const bool wrote = std::fwrite(body.data(), 1, body.size(), f) == body.size();
    const bool clean = !std::ferror(f);
    const int closed = std::fclose(f);
    if (!wrote || !clean || closed != 0) { *error = "writing " + path + " failed"; return false; }
#ifdef _WIN32
    if (sa2_replace_rename(tmp.c_str(), path.c_str()) != 0) {
#else
    if (std::rename(tmp.c_str(), path.c_str()) != 0) {
#endif
        *error = "cannot publish " + path + ": " + std::strerror(errno);
        return false;
    }
    return true;
}
std::string close_diagnostic_file(FILE*& f, const std::string& path, bool* closed, uint64_t* errors, bool known_error) {
    if (!f) return {};
    std::string error;
    const int flushed = std::fflush(f);
    const bool dirty = std::ferror(f);
    if (flushed != 0) { ++*errors; error = "flushing " + path + " failed"; }
    else if (dirty && *errors == 0 && !known_error) { ++*errors; error = "writing " + path + " failed"; }
    const int result = std::fclose(f);
    f = nullptr;
    *closed = result == 0;
    if (result != 0) { ++*errors; if (error.empty()) error = "closing " + path + " failed"; }
    return error;
}
// End input latency helpers.

uint16_t x87_cw() {
    uint16_t cw = 0;
#if defined(__x86_64__) || defined(__i386__)
    __asm__ __volatile__("fnstcw %0" : "=m"(cw));
#endif
    return cw;
}
std::string jesc(const std::string& s) {
    std::string o;
    for (unsigned char c : s) {
        if (c == '"' || c == '\\') { o += '\\'; o += char(c); }
        else if (c == '\n') o += "\\n";
        else if (c < 0x20) { char b[8]; std::snprintf(b, sizeof b, "\\u%04x", c); o += b; }
        else o += char(c);
    }
    return o;
}
std::string jstr(const std::string& s) { return "\"" + jesc(s) + "\""; }
std::string jlist(const std::vector<std::string>& v) {
    std::string o = "[";
    for (size_t i = 0; i < v.size(); i++) o += (i ? ", " : "") + jstr(v[i]);
    return o + "]";
}
// printf into a string of any length: the report's lists of first errors,
// problems and values are longer than any fixed buffer (a 1,024-byte one cut
// them, and the report with them, mid-string).
std::string strf(const char* fmt, ...) {
    va_list ap, aq;
    va_start(ap, fmt);
    va_copy(aq, ap);
    const int n = std::vsnprintf(nullptr, 0, fmt, ap);
    va_end(ap);
    std::string s(n > 0 ? size_t(n) : 0, '\0');
    if (n > 0) std::vsnprintf(s.data(), size_t(n) + 1, fmt, aq);
    va_end(aq);
    return s;
}
// SDL3's name for a window or input event (SDL_events.h), or its number, for the
// window's event log.
std::string sdl_event_name(uint32_t t) {
    switch (t) {
    case SDL_EVENT_QUIT: return "QUIT";
    case SDL_EVENT_WINDOW_SHOWN: return "WINDOW_SHOWN";
    case SDL_EVENT_WINDOW_HIDDEN: return "WINDOW_HIDDEN";
    case SDL_EVENT_WINDOW_EXPOSED: return "WINDOW_EXPOSED";
    case SDL_EVENT_WINDOW_MOVED: return "WINDOW_MOVED";
    case SDL_EVENT_WINDOW_RESIZED: return "WINDOW_RESIZED";
    case SDL_EVENT_WINDOW_PIXEL_SIZE_CHANGED: return "WINDOW_PIXEL_SIZE_CHANGED";
    case SDL_EVENT_WINDOW_MINIMIZED: return "WINDOW_MINIMIZED";
    case SDL_EVENT_WINDOW_MAXIMIZED: return "WINDOW_MAXIMIZED";
    case SDL_EVENT_WINDOW_RESTORED: return "WINDOW_RESTORED";
    case SDL_EVENT_WINDOW_MOUSE_ENTER: return "WINDOW_MOUSE_ENTER";
    case SDL_EVENT_WINDOW_MOUSE_LEAVE: return "WINDOW_MOUSE_LEAVE";
    case SDL_EVENT_WINDOW_FOCUS_GAINED: return "WINDOW_FOCUS_GAINED";
    case SDL_EVENT_WINDOW_FOCUS_LOST: return "WINDOW_FOCUS_LOST";
    case SDL_EVENT_WINDOW_CLOSE_REQUESTED: return "WINDOW_CLOSE_REQUESTED";
    case SDL_EVENT_WINDOW_DISPLAY_CHANGED: return "WINDOW_DISPLAY_CHANGED";
    case SDL_EVENT_WINDOW_DISPLAY_SCALE_CHANGED: return "WINDOW_DISPLAY_SCALE_CHANGED";
    case SDL_EVENT_WINDOW_OCCLUDED: return "WINDOW_OCCLUDED";
    case SDL_EVENT_WINDOW_ENTER_FULLSCREEN: return "WINDOW_ENTER_FULLSCREEN";
    case SDL_EVENT_WINDOW_LEAVE_FULLSCREEN: return "WINDOW_LEAVE_FULLSCREEN";
    case SDL_EVENT_WINDOW_DESTROYED: return "WINDOW_DESTROYED";
    case SDL_EVENT_KEY_DOWN: return "KEY_DOWN";
    case SDL_EVENT_KEY_UP: return "KEY_UP";
    case SDL_EVENT_TEXT_EDITING: return "TEXT_EDITING";
    case SDL_EVENT_TEXT_INPUT: return "TEXT_INPUT";
    case SDL_EVENT_MOUSE_MOTION: return "MOUSE_MOTION";
    case SDL_EVENT_MOUSE_BUTTON_DOWN: return "MOUSE_BUTTON_DOWN";
    case SDL_EVENT_MOUSE_BUTTON_UP: return "MOUSE_BUTTON_UP";
    case SDL_EVENT_MOUSE_WHEEL: return "MOUSE_WHEEL";
    default: {
        char b[16];
        std::snprintf(b, sizeof b, "0x%x", t);
        return b;
    }
    }
}

// Whether an event is a person's input (keys, text, mouse, touch, pen, a joystick or
// gamepad, a drop): what the window swallowed, which reaches nothing else.
bool sdl_event_is_input(uint32_t t) {
    return (t >= SDL_EVENT_KEY_DOWN && t <= SDL_EVENT_TEXT_INPUT) || t == SDL_EVENT_TEXT_EDITING_CANDIDATES ||
           (t >= SDL_EVENT_MOUSE_MOTION && t <= SDL_EVENT_MOUSE_WHEEL) || (t >= 0x600 && t < 0x800) ||
           (t >= 0x1000 && t < 0x1100) || (t >= 0x1300 && t < 0x1400);
}

uint32_t rd32(const uint8_t* p) { uint32_t v; std::memcpy(&v, p, 4); return v; }
float fbits(uint32_t u) { float f; std::memcpy(&f, &u, 4); return f; }

// A fingerprint of a read-back frame's colour: FNV-1a over each pixel's B, G
// and R as one 32-bit word (the back buffer's alpha is whatever the game last
// wrote, not part of the picture), row by row so the pitch's padding is left
// out. Two plays of one stream that draw the same picture give the same value.
uint64_t frame_hash(const uint8_t* bgra, uint32_t pitch, uint32_t w, uint32_t h) {
    uint64_t hash = 0xcbf29ce484222325ull;
    for (uint32_t y = 0; y < h; y++) {
        const uint8_t* row = bgra + size_t(y) * pitch;
        for (uint32_t x = 0; x < w; x++) {
            hash ^= rd32(row + 4 * size_t(x)) & 0x00FFFFFFu;
            hash *= 0x100000001b3ull;
        }
    }
    return hash;
}
// The frames file's digest: FNV-1a over every frame's bgrx_hash, 8 bytes each,
// little-endian, in frame order. tools/s5/frames_to_video.py recomputes it from
// the times file to tie a frames file to the report of the run that wrote it.
uint64_t digest_step(uint64_t d, uint64_t frame) {
    for (int i = 0; i < 8; i++) {
        d ^= (frame >> (8 * i)) & 0xFFu;
        d *= 0x100000001b3ull;
    }
    return d;
}

// Why writing `path` is refused, or "" when it is allowed: inside a git
// working tree (the nearest ancestor holding .git), only its private/ may hold
// the player's outputs.
std::string refuse_output(const std::string& path) {
    std::error_code ec;
    const fs::path abs = fs::weakly_canonical(fs::absolute(fs::path(path), ec), ec);
    if (ec) return "cannot resolve " + path + ": " + ec.message();
    for (fs::path d = abs.parent_path(); !d.empty(); d = d.parent_path()) {
        if (fs::exists(d / ".git", ec)) {
            const fs::path rel = abs.lexically_relative(d / "private");
            if (!rel.empty() && *rel.begin() != "..") return "";
            return path + " resolves to " + abs.string() + ", inside the working tree " + d.string() +
                   " and outside its private/ (AGENTS.md rule 9: game frames are captures)";
        }
        if (d == d.root_path()) break;
    }
    return "";
}

struct Options {
    std::string stream;
    bool interactive = false;
    int video_fd = -1, cancel_fd = -1;
    int end_fd = -1;                          // --end-fd: where a close asks for the end
    bool explicit_live() const { return video_fd >= 0; }
    int stage = 3;
    bool vsync = false, pace = false, hidden = false, stats_only = false, allow_truncated = false, allow_notes = false;
    double speed = 1.0;
    std::string frames_out, report, snap_dir, expect, dxvk;
    std::string pace_log;                     // --pace-log: each paced Present's times
    std::string input_path;                   // --input-path: the game's keyboard FIFO
    std::string key_log, present_log, synthetic_keys;
    std::string synthetic_input;              // --synthetic-input: test pads and touches ("Gamepads")
    std::string overlay_out;                  // --overlay-out: what the swapchain was given ("Gamepads")
    bool audio = false;                     // --audio: SDL's playback device
    std::string audio_wav;                    // --audio-wav: the sound as a WAV
    bool sound() const { return audio || !audio_wav.empty(); }
    std::set<uint32_t> snaps, dump_draws;
    struct Pixel { uint32_t frame, x, y; };
    std::vector<Pixel> pixels;
    uint32_t max_frames = 0, report_every = 100;
    uint32_t stop_draw_frame = 0, stop_draw_ordinal = 0;
};

bool synthetic_configuration_valid(const Options& o, bool max_frames_given, const std::string& bound) {
    if (o.synthetic_keys.empty()) return true;
    uint64_t frames = 0;
    return o.interactive && o.explicit_live() && !o.stats_only && !o.hidden && !o.input_path.empty() &&
           !o.key_log.empty() && !o.present_log.empty() && max_frames_given &&
           canonical_u64(bound, &frames) && frames > 0 && frames <= UINT32_MAX && o.max_frames == frames;
}

struct Expect {
    uint32_t frame, x, y, lo, hi, b, g, r, tol;
    std::string what;
    bool checked = false, pass = false;
};

// What a call amounted to in the player.
enum Cat { C_HANDLED, C_IGNORED, C_STAGE, C_UNHANDLED, C_COUNT };
const char* const kCatName[C_COUNT] = {"handled", "ignored_by_design", "skipped_by_stage", "not_handled"};

// What the player does with a call, decided by its interface and slot (or its
// D3DX entry) before any argument is read: replay it; check what it answered
// and count it ignored by design (P_CHECK); count it ignored by design, since
// it has no effect on the picture; or count it not handled, since the player
// has no code for it. A replay can still find an argument value it does not
// implement in a replayed call; --stats-only counts by the plan alone.
enum Plan { P_REPLAY, P_CHECK, P_NO_EFFECT, P_NOT_HANDLED };

// The SA2S_D3DX entries (host/sa2_stream.h), by number: the name and argc the
// header's "entries" table must give each, the DLL that exports it, and the
// plan, with the reason for each entry that is not replayed.
struct EntryInfo {
    uint32_t entry;
    const char* name;
    uint32_t argc;
    const char* dll;
    Plan plan;
    const char* why;
};
const EntryInfo kEntries[] = {
    {SA2S_D3DX_TEXTURE_FROM_MEMORY, "D3DXCreateTextureFromFileInMemoryEx", 15, "d3dx9_42", P_REPLAY, ""},
    {SA2S_D3DX_CREATE_TEXTURE, "D3DXCreateTexture", 8, "d3dx9_42", P_REPLAY, ""},
    {SA2S_D3DX_FILTER_TEXTURE, "D3DXFilterTexture", 4, "d3dx9_42", P_REPLAY, ""},
    {SA2S_D3DX_LOAD_SURFACE, "D3DXLoadSurfaceFromSurface", 8, "d3dx9_42", P_REPLAY, ""},
    {SA2S_D3DX_GET_CONSTANT_TABLE, "D3DXGetShaderConstantTable", 2, "d3dx9_42", P_NO_EFFECT,
     "an ID3DXConstantTable: nothing to draw (its five root setters reach the picture through the device's "
     "Set*ShaderConstant records, which are replayed)"},
    {SA2S_D3DX_CUBE_TEXTURE_FROM_MEMORY, "D3DXCreateCubeTextureFromFileInMemoryEx", 14, "d3dx9_42", P_REPLAY, ""},
    {SA2S_D3DX_VOLUME_TEXTURE_FROM_MEMORY, "D3DXCreateVolumeTextureFromFileInMemoryEx", 16, "d3dx9_42", P_REPLAY,
     ""},
    {SA2S_D3DX_CREATE_CUBE_TEXTURE, "D3DXCreateCubeTexture", 7, "d3dx9_42", P_REPLAY, ""},
    {SA2S_D3DX_CREATE_VOLUME_TEXTURE, "D3DXCreateVolumeTexture", 9, "d3dx9_42", P_REPLAY, ""},
    {SA2S_D3DX_SAVE_SURFACE_TO_FILE, "D3DXSaveSurfaceToFileA", 5, "d3dx9_42", P_NO_EFFECT,
     "writes a file from a surface: nothing reaches the picture"},
    {SA2S_D3DX_SAVE_TEXTURE_TO_FILE, "D3DXSaveTextureToFileA", 4, "d3dx9_42", P_NO_EFFECT,
     "writes a file from a texture: nothing reaches the picture"},
    {SA2S_D3DX_DIRECT3D_CREATE9, "Direct3DCreate9", 0, "d3d9", P_NO_EFFECT,
     "the player makes its own IDirect3D9 at CreateDevice; the guest's is kept to hold IDirect3D9 calls to"},
};
const EntryInfo* entry_info(uint32_t entry) {
    for (const EntryInfo& e : kEntries)
        if (e.entry == entry) return &e;
    return nullptr;
}

struct MStat {
    uint64_t by[C_COUNT] = {0, 0, 0, 0};
    uint64_t failed = 0, hr_differs = 0;
    uint32_t first_fail_hr = 0, first_fail_seq = 0, first_differs_hr = 0, first_differs_eax = 0;
};

struct Lock {
    bool active = false, has_rect = false;
    uint32_t pitch = 0, flags = 0, rows = 0;   // rows: from the recorder's LOCK blob, 0 when unknown
    RECT rect{};
};

// Each object's `made`: in a replay, whether the host object exists; in a
// stats read, which makes none, whether a replay would have made it as far as
// the stream shows. Every check of an object's existence reads it, so the two
// modes run the same checks.
struct Tex {
    IDirect3DTexture9* host = nullptr;
    bool made = false;
    uint32_t addr = 0;                 // the guest object
    uint32_t w = 0, h = 0, levels = 1, fmt = 0, usage = 0, pool = 0;
    uint32_t host_fmt = 0, host_levels = 0;
    std::string source;              // file-dds, file-png, d3dx-create, device-create
    std::vector<std::vector<uint8_t>> data;   // guest-format bytes per level, packed pitch
    std::vector<Lock> locks;
};

// A cube or a volume texture: IDirect3DDevice9::CreateCubeTexture and the four D3DX cube
// and volume creators. Its own type, apart from Tex, so no 2D path (GetSurfaceLevel, a
// texture LockRect, D3DXFilterTexture, D3DXLoadSurfaceFromSurface) can take one for a 2D
// texture. `levels` is what the guest was told (cube_levels, the D3DX rules): a cube's
// GetLevelCount answer and a volume's, the count GetLevelDesc and GetCubeMapSurface are
// held to; `host_levels` what the host object has (fewer at stage 2, which makes no
// mips). `faces` holds the guest-format bytes, packed pitch: a cube's per face
// (D3DCUBEMAP_FACES order) and level, a volume's in faces[0] per level, each level its
// level_dim(depth, level) slices one after another.
struct CubeVol {
    bool volume = false;
    IDirect3DCubeTexture9* cube = nullptr;
    IDirect3DVolumeTexture9* vol = nullptr;
    bool made = false;
    uint32_t addr = 0;
    uint32_t w = 0, h = 0, depth = 1, levels = 1, fmt = 0, usage = 0, pool = 0;
    uint32_t host_fmt = 0, host_levels = 0;
    std::string source;
    std::vector<std::vector<uint8_t>> faces[6];
    const char* kind() const { return volume ? "volume" : "cube"; }
};

struct Surf {
    // FACE: a cube texture's face surface (GetCubeMapSurface): `tex` the cube, `face`, `level`
    enum Kind { LEVEL, BACKBUFFER, AUTODEPTH, OWN, FACE } kind = OWN;
    uint32_t tex = 0, level = 0, face = 0;
    IDirect3DSurface9* host = nullptr;
    bool made = false;                 // BACKBUFFER, AUTODEPTH, OWN; a LEVEL's is its texture's, a FACE's its cube's
    uint32_t w = 0, h = 0, fmt = 0;
    Lock lock;
};

struct Buf {
    IDirect3DVertexBuffer9* vb = nullptr;
    IDirect3DIndexBuffer9* ib = nullptr;
    uint16_t type = 0;                 // T_VB or T_IB: the call that made it
    bool made = false;
    uint32_t len = 0;
    bool locked = false;
    uint32_t off = 0, size = 0, flags = 0;
};

struct Pin { std::string path, real, sha256; };

uint32_t prim_vertices(uint32_t type, uint32_t n) {
    switch (type) {
    case 1: return n;           // POINTLIST
    case 2: return 2 * n;       // LINELIST
    case 3: return n + 1;       // LINESTRIP
    case 4: return 3 * n;       // TRIANGLELIST
    case 5: case 6: return n + 2;   // TRIANGLESTRIP, TRIANGLEFAN
    default: return 0;
    }
}

bool dxvk_format_ok(uint32_t fmt) {
    // D3DFORMATs DXVK 3.1.1 has no Vulkan format for (src/d3d9/d3d9_format.cpp).
    return fmt != 20 && fmt != 27 && fmt != 29 && fmt != 40 && fmt != 41 && fmt != 71 &&
           fmt != 73 && fmt != 79;
}

// A format conversion that keeps 8 bits per channel loses nothing, so a
// dithering filter changes nothing in it.
bool lossless_target(uint32_t fmt) {
    return fmt == tex::FMT_R8G8B8 || fmt == tex::FMT_A8R8G8B8 || fmt == tex::FMT_X8R8G8B8 ||
           fmt == tex::FMT_A8B8G8R8 || fmt == tex::FMT_X8B8G8R8;
}
bool filter_dithers(uint32_t filter) { return filter == kD3dxDefault || (filter & kFilterDitherBits); }
bool mip_filter_is_box(uint32_t f) { return f == kD3dxDefault || f == kFilterBox; }

// Calls with no effect on the picture, per interface and slot, each named.
// Anything a type does not list here and the player does not replay is not
// handled: there is no range rule.
bool no_picture_effect(uint16_t type, uint16_t slot) {
    switch (type) {
    case T_D3D9:
        // 4 GetAdapterCount .. 15 GetAdapterMonitor: adapter queries and capability checks
        return slot >= 4 && slot <= 15;
    case T_DEVICE:
        // Each named in names.h: queries (Get*, TestCooperativeLevel,
        // ValidateDevice), residency (EvictManagedResources), the cursor
        // (10-12: not in the back buffer), SetDialogBoxMode, and SetGammaRamp
        // (21: windowed Direct3D 9 and DXVK ignore gamma, r3 section 8).
        switch (slot) {
        case 3: case 4: case 5: case 6: case 7: case 8: case 9: case 10: case 11: case 12: case 14: case 15:
        case 19: case 20: case 21: case 22: case 45: case 48: case 50: case 52: case 54: case 56: case 58:
        case 63: case 64: case 66: case 68: case 70: case 72: case 74: case 76: case 78: case 80: case 88:
        case 90: case 93: case 95: case 97: case 99: case 101: case 103: case 105: case 108: case 110:
        case 112: case 114:
            return true;
        default:
            return false;
        }
    case T_TEXTURE:
        switch (slot) {
        case 3: case 5: case 8: case 10:       // GetDevice, GetPrivateData, GetPriority, GetType
        case 4: case 6:                        // SetPrivateData, FreePrivateData: not rendering state
        case 7: case 9:                        // SetPriority, PreLoad: residency hints
        case 12: case 13: case 15: case 17:    // GetLOD, GetLevelCount, GetAutoGenFilterType, GetLevelDesc
        case 21:                               // AddDirtyRect: the player uploads whole levels at every Unlock
            return true;
        default:
            return false;                      // 11 SetLOD is replayed; 14, 16 (autogen), 18-20 are the player's
        }
    case T_CUBETEX: case T_VOLTEX:
        // 13 GetLevelCount and 17 GetLevelDesc are the player's (both types), and a cube's
        // 18 GetCubeMapSurface; 11 SetLOD, 14 and 16 (autogen), 19-21 (LockRect, UnlockRect,
        // AddDirtyRect; LockBox, UnlockBox, AddDirtyBox) are not handled.
        switch (slot) {
        case 3: case 5: case 8: case 10: case 4: case 6: case 7: case 9:
        case 12: case 15:                      // GetLOD, GetAutoGenFilterType
            return true;
        case 18:                               // a volume's GetVolumeLevel: an object query
            return type == T_VOLTEX;
        default:
            return false;
        }
    case T_SURFACE:
        switch (slot) {
        case 3: case 5: case 8: case 10: case 4: case 6: case 7: case 9:
        case 11: case 12:                      // GetContainer, GetDesc
            return true;
        default:
            return false;                      // 13, 14 are the player's; 15 GetDC, 16 ReleaseDC draw
        }
    case T_VOLUME:
        switch (slot) {
        case 3: case 4: case 5: case 6:        // GetDevice, private data
        case 7: case 8:                        // GetContainer, GetDesc
            return true;
        default:
            return false;                      // 9 LockBox, 10 UnlockBox
        }
    case T_VB: case T_IB:
        switch (slot) {
        case 3: case 5: case 8: case 10: case 4: case 6: case 7: case 9:
        case 13:                               // GetDesc
            return true;
        default:
            return false;
        }
    case T_DECL: case T_VS: case T_PS:
        return slot == 3 || slot == 4;         // GetDevice; GetDeclaration or GetFunction
    case T_STATEBLOCK:
        return slot == 3;                      // GetDevice; 4 Capture, 5 Apply change state
    case T_SWAPCHAIN:
        // 5 GetBackBuffer, 6 GetRasterStatus, 7 GetDisplayMode, 8 GetDevice, 9 GetPresentParameters
        return slot >= 5 && slot <= 9;
    case T_QUERY:
        return slot >= 3 && slot <= 5;         // GetDevice, GetType, GetDataSize; CreateQuery is not handled
    case T_CTAB:
        // 3 GetBufferPointer .. 10 GetConstantElement are queries. 13 SetBool, 15 SetInt,
        // 17 SetFloat, 19 SetVector and 21 SetMatrix set device constants through the guest
        // device (runtime/sa2_d3d9.c table_set): the recording device makes those nested
        // Set*ShaderConstant{F,I,B} calls through the device's own vtable, so the recorder
        // writes each as a call of its own, with its payload, before the setter's record, and
        // the player replays them (device_call). The setter's own record therefore has no
        // effect on the picture: a setter the device answers with no nested call (a vector
        // set through SetBool, SetInt or SetFloat) has none in the device either.
        // tools/s5/m7_synthetic.py's ctabset streams show it on both hosts' pixels, and the
        // recorder's bypass mutant (test_s5_ctab_recorder.py) shows the nested records carry
        // it. 11 SetDefaults, 12 SetValue, the arrays and the transposes are not handled.
        return (slot >= 3 && slot <= 10) || slot == 13 || slot == 15 || slot == 17 || slot == 19 || slot == 21;
    default:
        return false;
    }
}

// The player's plan for a COM call (see Plan): the slots it has replay code
// for, first; then the named slots with no effect on the picture; the rest is
// not handled. device_call and resource_call are reached only for P_REPLAY and
// P_CHECK, so this is the one list of what the player replays.
Plan call_plan(uint16_t type, uint16_t slot) {
    if (slot <= 2) return P_NO_EFFECT;          // QueryInterface, AddRef, Release (kept: see on_call)
    switch (type) {
    case T_D3D9:
        if (slot == 16) return P_REPLAY;        // CreateDevice
        break;
    case T_DEVICE:
        switch (slot) {
        case 17: case 41: case 42: case 43:     // Present, BeginScene, EndScene, Clear
        case 44: case 47: case 49: case 51:     // SetTransform, SetViewport, SetMaterial, SetLight
        case 53: case 55: case 57:              // LightEnable, SetClipPlane, SetRenderState
        case 65: case 67: case 69:              // SetTexture, SetTextureStageState, SetSamplerState
        case 75: case 77:                       // SetScissorRect, SetSoftwareVertexProcessing
        case 81: case 82: case 83: case 84:     // DrawPrimitive, DrawIndexedPrimitive, and their UP forms
        case 86: case 87: case 89:              // CreateVertexDeclaration, SetVertexDeclaration, SetFVF
        case 91: case 92: case 106: case 107:   // CreateVertexShader, SetVertexShader, and the pixel pair
        case 94: case 96: case 98:              // SetVertexShaderConstantF, I, B
        case 109: case 111: case 113:           // SetPixelShaderConstantF, I, B
        case 100: case 102: case 104:           // SetStreamSource, SetStreamSourceFreq, SetIndices
        case 23: case 26: case 27:              // CreateTexture, CreateVertexBuffer, CreateIndexBuffer
        case 25:                                // CreateCubeTexture (cubevol_create)
        case 28: case 29: case 36:              // CreateRenderTarget, CreateDepthStencilSurface, CreateOffscreenPlainSurface
        case 37: case 39:                       // SetRenderTarget, SetDepthStencilSurface
            return P_REPLAY;
        case 18: case 38: case 40:              // GetBackBuffer, GetRenderTarget, GetDepthStencilSurface
        case 16:                                // Reset, to the creation parameters (reset_check)
            return P_CHECK;
        default:
            break;
        }
        break;
    case T_TEXTURE:
        if (slot == 11 || (slot >= 18 && slot <= 20)) return P_REPLAY;   // SetLOD, GetSurfaceLevel, LockRect, UnlockRect
        break;
    case T_CUBETEX:                                         // GetLevelCount, GetLevelDesc, GetCubeMapSurface
        if (slot == 13 || slot == 17 || slot == 18) return P_REPLAY;
        break;
    case T_VOLTEX:                                          // GetLevelCount, GetLevelDesc
        if (slot == 13 || slot == 17) return P_REPLAY;
        break;
    case T_SURFACE:
        if (slot == 13 || slot == 14) return P_REPLAY;                  // LockRect, UnlockRect
        break;
    case T_VB: case T_IB:
        if (slot == 11 || slot == 12) return P_REPLAY;                  // Lock, Unlock
        break;
    case T_SWAPCHAIN:
        if (slot == 3) return P_REPLAY;                                 // Present
        break;
    default:
        break;
    }
    return no_picture_effect(type, slot) ? P_NO_EFFECT : P_NOT_HANDLED;
}

const char* event_kind(uint32_t t) {
    if (t >= 0x200 && t < 0x300) return "window";
    if (t >= 0x300 && t < 0x400) return "keyboard";
    if (t >= 0x400 && t < 0x500) return "mouse";
    if (t >= 0x600 && t < 0x700) return "joystick_gamepad";
    if (t >= 0x700 && t < 0x800) return "touch";
    if (t >= 0x100 && t < 0x200) return "app_display";
    return "other";
}

class Player {
public:
    explicit Player(const Options& o) : o_(o), video_fd_(o.video_fd), cancel_fd_(o.cancel_fd),
                                        end_fd_(o.end_fd) {}
    ~Player() { teardown(); }
    int run();

private:
    // setup
    bool check_outputs();
    bool setup_sdl();
    void pin_host_files();
    bool validate_host_files();
    bool setup_window(uint32_t width, uint32_t height);
    bool accept_live_outcome(const Sa2ReadOutcome& outcome);
    static Sa2ServiceDecision service_thunk(void* self) {
        auto* player = static_cast<Player*>(self);
        player->on_idle();
        return player->stop_ ? Sa2ServiceDecision::Cancel : Sa2ServiceDecision::Continue;
    }
    bool open_frames_out();
    bool open_pace_log();
    bool create_device(const Sa2Record& r, Cat* cat);
    bool implicit_surfaces();
    bool device_failure(const std::string& why);
    // dispatch
    void check_entries_table();
    void check_record(const Sa2Record& r);
    void on_call(const Sa2Record& r);
    void on_d3dx(const Sa2Record& r);
    bool device_made() const { return o_.stats_only ? stats_device_ : dev_ != nullptr; }
    // Whether host calls are made: false in a stats read, which runs the same
    // code with every host call left out (see "--stats-only" above).
    bool host() const { return !o_.stats_only; }
    void do_present(const Sa2Record* frame);
    Cat device_call(const Sa2Record& r, uint32_t* hr_out);
    Cat target_query(const Sa2Record& r);
    Cat reset_check(const Sa2Record& r);
    Cat resource_call(const Sa2Record& r, uint32_t* hr_out);
    // objects
    uint32_t out_addr(const Sa2Record& r, int arg, const char* what);
    const Sa2Blob* in_blob(const Sa2Record& r, int arg, size_t need, const char* what);
    Tex* texture(uint32_t addr) { auto it = tex_.find(addr); return it == tex_.end() ? nullptr : &it->second; }
    IDirect3DBaseTexture9* bindable(uint32_t addr);
    IDirect3DSurface9* surface_for_target(uint32_t addr, bool* made);
    bool create_host_texture(Tex& t);
    bool upload_level(Tex& t, uint32_t level);
    bool store_locked(const Sa2Record& r, Tex& t, uint32_t level, const Lock& lk, const Sa2Blob& b);
    bool store_rows(const Sa2Record& r, uint32_t lw, uint32_t lh, uint32_t fmt, uint32_t level, const Lock& lk,
                    const Sa2Blob& b, std::vector<std::vector<uint8_t>>* chain, uint32_t index);
    // cube and volume textures (CubeVol)
    CubeVol* cubevol(uint32_t addr) {
        auto it = cubevol_.find(addr);
        return it == cubevol_.end() ? nullptr : &it->second;
    }
    bool fresh_object(const Sa2Record& r, uint32_t addr);
    void cubevol_create(const Sa2Record& r, uint32_t addr, bool volume, uint32_t w, uint32_t h, uint32_t depth,
                        uint32_t levels, uint32_t usage, uint32_t fmt, uint32_t pool, const char* source);
    bool create_host_cubevol(CubeVol& t);
    bool upload_cubevol_level(CubeVol& t, uint32_t face, uint32_t level);
    Cat cubevol_from_file(const Sa2Record& r, bool volume);
    Cat cubevol_call(const Sa2Record& r, uint32_t* hr_out);
    Cat cubevol_bound_draw(const Sa2Record& r);
    bool lock_of(const Sa2Record& r, Lock* lk, sa2s_lock_info* info);
    Cat unlock_upload(const Sa2Record& r, Tex& t, uint32_t level, const Lock& lk);
    Cat texture_from_file(const Sa2Record& r);
    void texture_create(uint32_t addr, uint32_t w, uint32_t h, uint32_t levels, uint32_t usage,
                        uint32_t fmt, uint32_t pool, const char* source);
    Cat filter_texture(const Sa2Record& r);
    Cat load_surface(const Sa2Record& r);
    // frames
    void readback_frame(uint64_t virtual_us, const Sa2Record* frame);
    void checkpoint_after_call(const Sa2Record& r, Cat cat, uint32_t hr);
    bool checkpoint_readback();
    void write_y4m(const uint8_t* bgra, uint32_t pitch);
    void pump_events();
    // Why the session is ending, or nullptr while it plays (pause-unfocused's hold, which does not
    // read the stream): the run stopped, the end asked for, the stream's END read, or the caller's
    // cancellation waiting in its pipe (looked at, not taken: the reader takes it).
    const char* hold_ending() const {
        if (stop_ || end_requested_ || end_records_) return "the session is ending";
        return sa2_cancel_waiting(cancel_fd_ >= 0 ? cancel_fd_ : rd_.live_cancel_fd());
    }
    void request_end(double now, bool by_close);
    std::string end_request_json() const;
    void pace_until(double target);
    // the reader's idle hook on a live stream: the window answers its events meanwhile
    void on_idle();
    static void idle_thunk(void* self) { static_cast<Player*>(self)->on_idle(); }
    // accounting
    std::string entry_label(uint32_t entry) const;
    std::string where(const Sa2Record& r) const;
    void count(const Sa2Record& r, Cat c, uint32_t hr);
    void error(const Sa2Record& r, const std::string& what);
    void note_error(const std::string& what);
    Cat unimplemented(const Sa2Record& r, const std::string& what);
    void stream_problem(const std::string& what);
    void io_failure(const std::string& what);
    void usage_failure(const std::string& what);
    int finish();
    std::string dxvk_log_path() const;
    void read_dxvk_log();
    bool write_report(bool complete);
    bool report_failure(const std::string& why);
    void diagnostic_failure(const std::string& why, bool fatal = true);
    std::string diagnostic_path_refusal() const;
    bool open_input_diagnostics();
    void finish_input_diagnostics();
    bool diagnostic_evidence_ready() const;
    bool write_present_row(uint32_t frame, uint64_t virtual_us, double call_s, double returned_s);
    bool load_synthetic_keys();
    void push_due_synthetic_keys();
    // gamepads and the touch overlay ("Gamepads" above)
    struct PadSlot;
    void setup_pads();
    void service_pads();
    void pad_event(const SDL_Event& e);
    void open_pad(SDL_JoystickID id);
    bool is_player_one_pad(SDL_JoystickID id) const;
    void read_pad(PadSlot* s);
    void derive_pad(PadSlot* s) const;
    void place_picture();
    PadSlot* slot_of(SDL_JoystickID id);
    void send_pads();
    void close_pads();
    std::string pads_json() const;
    bool load_synthetic_input();
    void push_due_synthetic_input();
    void overlay_shot(SDL_GPUDevice* dev, SDL_GPUCommandBuffer* cb, SDL_GPUTexture* frame, Uint32 frame_w,
                      Uint32 frame_h, Uint32 w, Uint32 h);
    void write_overlay_shot();
    static bool SDLCALL pad_watch(void* self, SDL_Event* e);
    static void present_overlay(void* self, SDL_GPUDevice* dev, SDL_GPUCommandBuffer* cb, SDL_GPUTexture* frame,
                                Uint32 frame_w, Uint32 frame_h, SDL_GPUTexture* target, Uint32 w, Uint32 h);
    std::string diagnostic_json() const;
    void teardown();
    bool load_expect();
    // sound (mixer.h)
    bool setup_audio();
    void audio_block();
    void finish_audio();
    std::string audio_json() const;

    const Options& o_;
    Sa2StreamReader rd_;
    bool opened_ = false, sdl_up_ = false;
    int video_fd_ = -1, cancel_fd_ = -1;
    bool finalized_ = false, sdl_identity_ok_ = false, dxvk_identity_ok_ = false, config_identity_ok_ = false;
    bool window_created_ = false, window_reused_ = false;
    uint32_t windows_created_ = 0;
    int sdl_version_ = 0;
    std::string sdl_revision_, live_status_ = "not_started", live_detail_;
    uint64_t service_calls_ = 0;
    uint64_t last_accepted_seq_ = 0;
    bool any_accepted_seq_ = false;
    bool report_ok_ = false;                  // the report's own path was not refused
    bool stream_file_ = false;                // the stream file opened (whether or not it was refused)
    bool records_to_end_ = false, hashed_after_stop_ = false;
    bool stats_device_ = false;               // --stats-only: a CreateDevice record was read
    bool create9_recorded_ = false;           // the header's entries table has Direct3DCreate9
    std::set<uint32_t> d3d9_objects_;         // what the guest's Direct3DCreate9 calls answered
    uint64_t direct3d_create9_ = 0;
    // The recording device's bindings as the guest set them (runtime/sa2_d3d9.c,
    // m_SetRenderTarget, m_SetDepthStencilSurface; CreateDevice binds the implicit
    // pair), which its GetRenderTarget, GetDepthStencilSurface and GetBackBuffer
    // answer from; target_query holds every answer to them.
    uint32_t bound_rt_[4] = {}, bound_ds_ = 0;
    uint64_t target_queries_ = 0, back_buffer_queries_ = 0;
    SDL_Window* win_ = nullptr;
    void* lib_ = nullptr;
    IDirect3D9* d3d_ = nullptr;
    IDirect3DDevice9* dev_ = nullptr;
    IDirect3DSurface9 *bb_ = nullptr, *ds_ = nullptr, *readback_ = nullptr;
    IDirect3DTexture9* white_ = nullptr;
    uint32_t bb_w_ = 0, bb_h_ = 0, bb_fmt_ = 0;
    uint32_t dev_addr_ = 0, rt_addr_ = 0, ds_addr_ = 0;
    uint32_t guest_pp_[14] = {};
    uint32_t creation_pp_[14] = {};            // CreateDevice's presentation parameters as recorded
    uint32_t guest_behavior_ = 0, host_behavior_ = 0;
    uint16_t cw_before_ = 0, cw_after_ = 0;
    std::string sdl_driver_, adapter_, adapter_driver_, stop_reason_ = "end of stream", implicit_from_ = "none";
    std::string phase_ = "start", device_failure_;
    uint64_t desc_checked_ = 0, desc_mismatch_ = 0, lock_info_used_ = 0;
    std::map<std::string, Pin> files_;
    std::string stream_sha256_;
    std::string dxvk_log_, dxvk_version_, dxvk_driver_, dxvk_config_seen_, dxvk_log_sha256_;
    uint64_t dxvk_log_bytes_ = 0;

    std::unordered_map<uint32_t, Tex> tex_;
    std::unordered_map<uint32_t, CubeVol> cubevol_;
    // The sampler stages a cube or volume texture is bound at (SetTexture), with that texture:
    // a draw while any is bound is refused by name (cubevol_bound_draw).
    std::map<uint32_t, uint32_t> cubevol_bound_;
    std::unordered_map<uint32_t, Surf> surf_;
    std::unordered_map<uint32_t, IDirect3DVertexDeclaration9*> decl_;
    std::unordered_map<uint32_t, IDirect3DVertexShader9*> vs_;
    std::unordered_map<uint32_t, IDirect3DPixelShader9*> ps_;
    std::unordered_map<uint32_t, Buf> buf_;
    uint32_t cur_vs_ = 0, cur_ps_ = 0, cur_decl_ = 0, cur_tex0_ = 0;
    uint32_t viewport_[6] = {};
    bool in_scene_ = false;
    const char* allow_ = nullptr;             // the scene-order rule the current call matched

    // frames
    bool present_pending_ = false, stop_ = false;
    uint32_t frame_ = 0, present_seq_ = 0;
    uint64_t source_draws_frame_ = 0;
    bool checkpoint_reached_ = false, checkpoint_pixels_ = false;
    uint32_t checkpoint_seq_ = 0, checkpoint_slot_ = 0, checkpoint_hr_ = 0;
    uint32_t checkpoint_guest_hr_ = 0, checkpoint_readback_hr_ = 0;
    Cat checkpoint_cat_ = C_UNHANDLED;
    uint64_t checkpoint_hash_ = 0;
    uint64_t last_vus_ = 0, frame_records_ = 0, missing_frame_records_ = 0;
    bool any_frame_time_ = false;
    std::unique_ptr<Sa2Pace> pace_;           // --pace: the schedule (pace.h), made at open
    FILE* pace_log_ = nullptr;                // --pace-log
    FILE* y4m_ = nullptr;
    FILE* times_ = nullptr;
    bool y4m_header_ = false;
    uint32_t y4m_frames_ = 0, times_rows_ = 0;
    uint64_t y4m_bytes_ = 0, bgrx_digest_ = 0xcbf29ce484222325ull;
    std::vector<uint8_t> yuv_, conv_;
    double t_start_ = 0;
    uint64_t events_discarded_ = 0, close_requests_ = 0, draws_ = 0, draws_frame_ = 0;
    // the window, measured: its state at every Present, its events, and its pumping
    uint64_t idle_pumps_ = 0, frames_occluded_ = 0, frames_hidden_ = 0, frames_minimized_ = 0;
    uint64_t frames_input_focus_ = 0, frames_mouse_focus_ = 0, input_events_ = 0;
    double last_pump_ = 0, longest_unpumped_ = 0, last_idle_pump_ = 0;
    uint64_t idle_waits_seen_ = 0;   // on_idle: the reader's idle_waits() at the last clock read
    uint32_t idle_unclocked_ = 0;    // on_idle: services since the last clock read
    std::vector<std::string> window_log_;
    struct FlagRun { uint32_t first, last; uint64_t flags; };
    std::vector<FlagRun> flag_runs_;       // the window's flags at each Present, by frame
    std::map<std::string, uint64_t> events_by_kind_;
    // --input-path: the gamepads, each in the XInput slot it was given, and the touch overlay
    // ("Gamepads" above). Kept before input_: test_s5_input_latency_player.py compiles the
    // fields from input_ on, without these.
    struct PadSlot {
        SDL_JoystickID id = 0;
        SDL_Gamepad* pad = nullptr;
        std::string name;        // SDL's name for it, read when it opens: the report makes no SDL call
        uint32_t held = 0;       // the SDL buttons held, bit SDL_GamepadButton
        sa2_input::PadState axes;  // its sticks and triggers as SDL gives them
        sa2_input::PadState state; // what the game is sent: axes, and held through pad_map_
    };
    PadSlot pad_slots_[sa2_input::kPadSlots];
    std::string pad_p1_name_;                      // SA2_PAD_P1=NAME:...: the pad that takes slot 0
    int pad_p1_vendor_ = -1, pad_p1_product_ = -1; // SA2_PAD_P1=ID:...: the same, by ids
    bool pad_p1_all_ = false;                      // SA2_PAD_P1=ALL: every pad is slot 0
    sa2_input::PadOutput pad_map_[SDL_GAMEPAD_BUTTON_COUNT];   // SA2_PAD_MAP, over kPadButtonBits
    float picture_y_ = -1;                         // SA2_PLAYER_PICTURE_Y ("The picture's place"); -1 centred
    float picture_anchor_ = -1;                    // what the last Present used: picture_y_ in an upright window
    bool picture_read_ = false;
    std::string picture_said_;                     // the last "picture:" line, said once per change
    sa2_touch::Overlay* touch_ = nullptr;
    sa2_touch::Stats touch_final_;                 // the overlay's counts, kept when it is destroyed
    bool pads_up_ = false, pads_closed_ = false, pads_dirty_ = false, touch_platform_ = false;
    bool gamepads_up_ = false;
    uint64_t pads_added_ = 0, pads_removed_ = 0, pads_unslotted_ = 0, pads_not_virtual_ = 0;
    uint64_t pad_events_ = 0, pad_sets_ = 0, touch_hides_ = 0, overlay_presents_ = 0;
    std::string pads_error_;
    // Events copied by pad_watch, on the thread SDL raised them on, for service_pads on this one.
    SDL_Mutex* pad_queue_mutex_ = nullptr;
    std::vector<SDL_Event> pad_queue_, pad_batch_;
    std::atomic<bool> pad_queue_ready_{false}, pad_queue_overflow_{false};
    uint64_t pad_queue_overflows_ = 0;
    // --synthetic-input
    struct SyntheticInput {
        uint64_t offset_ns = 0;
        std::string verb;
        int k = 0, n = 0, value = 0;     // pad K's BUTTON or AXIS n and its value; a touch's finger k
        bool at_control = false;          // touches: at a control, x and y then in stick radii
        sa2_touch::Control control = sa2_touch::Control::Stick;
        float x = 0, y = 0;
    };
    std::vector<SyntheticInput> synthetic_input_;
    size_t synthetic_input_cursor_ = 0;
    bool synthetic_input_armed_ = false;
    uint64_t synthetic_input_ticks_ = 0, synthetic_input_done_ = 0, synthetic_input_failures_ = 0;
    SDL_JoystickID synthetic_pad_id_[sa2_input::kPadSlots] = {};
    SDL_Joystick* synthetic_pad_[sa2_input::kPadSlots] = {};
    // --overlay-out: the first Presents' swapchain pictures, rebuilt (overlay_shot)
    struct OverlayShot {
        SDL_GPUDevice* dev = nullptr;
        SDL_GPUTexture* tex[2] = {};
        SDL_GPUTransferBuffer* down = nullptr;
        SDL_GPUTextureFormat format = SDL_GPU_TEXTUREFORMAT_INVALID;
        uint32_t w = 0, h = 0, frame = 0;
        bool pending = false, failed = false;
        uint64_t written = 0;
        std::string error;
    } shot_;
    std::unique_ptr<sa2_input::KeySender> input_;   // --input-path only
    // --end-fd: the close request that asked the caller to end the session.
    int end_fd_ = -1;
    bool end_requested_ = false, end_written_ = false;
    double end_request_s_ = 0;
    const char* end_by_ = nullptr;
    uint32_t end_request_frame_ = 0;
    uint64_t closes_after_end_ = 0;
    std::string end_error_;
    uint64_t real_keys_ignored_ = 0;                // --synthetic-keys: window keys not sent
    bool alt_enter_held_ = false;                   // Alt+Enter's Enter is down: its up is kept too
    // Input latency diagnostics are optional; the sender borrows key_log_file_.
    FILE* key_log_file_ = nullptr;
    FILE* present_log_file_ = nullptr;
    uint32_t input_window_id_ = 0;
    bool sender_start_attempted_ = false, sender_started_ = false;
    bool key_log_header_accepted_ = false, present_log_header_accepted_ = false;
    bool key_log_opened_ = false, key_log_closed_ = false;
    bool present_log_opened_ = false, present_log_closed_ = false;
    bool diagnostics_closed_ = false, input_log_ok_ = true;
    bool diagnostic_fatal_ = false, diagnostic_failure_registered_ = false;
    bool report_io_failed_ = false;
    uint64_t key_log_owner_io_errors_ = 0, present_log_io_errors_ = 0;
    uint64_t present_log_rows_ = 0, present_log_dropped_rows_ = 0;
    uint64_t future_key_timestamps_ = 0, event_clock_errors_ = 0, invalid_key_timestamps_ = 0;
    uint64_t present_clock_errors_ = 0, report_io_errors_ = 0;
    std::string diagnostic_error_, present_log_error_, report_io_error_, final_host_input_;
    std::vector<SyntheticKey> synthetic_keys_;
    size_t synthetic_cursor_ = 0;
    bool synthetic_armed_ = false, synthetic_schedule_failed_ = false;
    uint64_t synthetic_armed_ticks_ = 0, synthetic_pushed_ = 0, synthetic_push_failures_ = 0;
    uint64_t synthetic_closes_ = 0;
    double synthetic_armed_return_s_ = 0;
    std::string synthetic_script_sha256_;

    // accounting
    std::map<uint32_t, MStat> mstat_;          // (type << 16) | slot
    std::map<uint32_t, MStat> xstat_;          // d3dx entry
    // count()'s row of mstat_ for each (type, slot) below (T_COUNT, kStatSlots), found once: a
    // map's rows never move. A live session counts a call for most of its 6,550 records a frame
    // (game.fifo.status: 43,210,033 records in 6,593 frames; the thermal unit, 2026-10-12).
    static constexpr uint32_t kStatSlots = 256;
    MStat* mstat_hot_[T_COUNT * kStatSlots] = {};
    MStat& call_stat(uint16_t type, uint16_t slot) {
        if (type < T_COUNT && slot < kStatSlots) {
            MStat*& hot = mstat_hot_[uint32_t(type) * kStatSlots + slot];
            if (!hot) hot = &mstat_[uint32_t(type) << 16 | slot];
            return *hot;
        }
        return mstat_[uint32_t(type) << 16 | slot];
    }
    uint64_t kinds_[8] = {};
    uint64_t unknown_kinds_ = 0, notes_ = 0, end_records_ = 0, seq_gaps_ = 0, after_end_ = 0;
    uint32_t expect_seq_ = 0;
    std::vector<std::string> first_notes_;
    uint64_t errors_ = 0;
    std::vector<std::string> first_errors_;
    uint64_t unimplemented_ = 0;
    std::vector<std::string> first_unimplemented_;
    uint64_t stream_problems_ = 0;
    std::vector<std::string> first_stream_problems_;
    std::vector<std::string> basic_failures_;   // "i/o: ..." and "usage: ...": exit status 2
    uint64_t host_failures_ = 0, failed_like_guest_ = 0;
    std::vector<std::string> first_host_failures_;
    std::map<std::string, uint64_t> allowed_;
    std::vector<std::string> first_allowed_;
    uint64_t readback_failures_ = 0, present_failures_ = 0, readonly_locks_ = 0;
    std::map<std::string, uint64_t> tex_by_source_;
    uint64_t placeholders_bound_ = 0, levels_generated_ = 0, lock_uploads_ = 0, bytes_uploaded_ = 0;
    uint64_t file_textures_ = 0, png_textures_ = 0, filter_calls_ = 0, releases_ = 0;
    std::vector<Expect> expects_;
    uint64_t expect_pass_ = 0, expect_fail_ = 0;
    std::vector<std::string> failures_;
    int exit_status_ = -1;
    std::string window_end_ = "null";

    // sound: the mixer with --audio or --audio-wav, its WAV, SDL's playback stream
    std::unique_ptr<Sa2Mixer> mixer_;
    Sa2Wav wav_;
    bool wav_open_ = false, wav_started_ = false, wav_sidecar_ok_ = false;
    SDL_AudioStream* audio_stream_ = nullptr;
    std::string audio_driver_, audio_failure_;
    uint64_t audio_records_ = 0, audio_blocks_ = 0, audio_bytes_queued_ = 0;
    uint64_t audio_dropped_ = 0, audio_drain_wait_ms_ = 0, audio_tail_wait_ms_ = 0;
    int audio_queued_at_end_ = 0;
    // the device's lead, the sound before the first Present (the WAV's alone), the
    // queue's depth each time a block was queued, and SDL's requests (on_audio_request)
    bool audio_lead_queued_ = false;
    uint64_t audio_pre_first_samples_ = 0, audio_tail_samples_ = 0;
    uint64_t audio_depth_n_ = 0, audio_depth_sum_ = 0;
    int audio_depth_min_ = -1, audio_depth_max_ = 0;
    AudioTap audio_tap_;
    uint64_t last_timed_us_ = 0, first_frame_us_ = 0;
    bool any_timed_ = false, any_frame_us_ = false;
};

// ------------------------------------------------------------------ accounting

// A D3DX entry by the DLL that exports it and its name ("d3d9::Direct3DCreate9",
// "d3dx9_42::D3DXCreateTexture"); an entry this player does not know goes by
// the header's name for it, or by number.
std::string Player::entry_label(uint32_t entry) const {
    if (const EntryInfo* e = entry_info(entry)) return std::string(e->dll) + "::" + e->name;
    if (const Sa2EntryRow* row = rd_.entry_row(entry)) return strf("?::%s (entry %u)", row->name.c_str(), entry);
    return strf("?::entry %u", entry);
}

std::string Player::where(const Sa2Record& r) const {
    return r.kind == SA2S_CALL ? strf("seq %u %s::%s", r.seq, type_name(r.type), method_name(r.type, r.slot))
                               : strf("seq %u %s", r.seq, entry_label(r.entry).c_str());
}

// A handled call's host answer against the guest's. A host failure where the
// guest was answered success fails the run, unless the call matched one of the
// named scene-order rules (allow_), which the report lists call by call.
void Player::count(const Sa2Record& r, Cat c, uint32_t hr) {
    MStat& m = r.kind == SA2S_CALL ? call_stat(r.type, r.slot) : xstat_[r.entry];
    m.by[c]++;
    const char* allow = allow_;
    allow_ = nullptr;
    if (c != C_HANDLED) return;
    if (int32_t(hr) < 0) {
        if (!m.failed) { m.first_fail_hr = hr; m.first_fail_seq = r.seq; }
        m.failed++;
        if (allow) {
            allowed_[allow]++;
            if (first_allowed_.size() < 20)
                first_allowed_.push_back(strf("%s: host 0x%08x, guest 0x%08x (%s)", where(r).c_str(), hr, r.eax, allow));
        } else if (int32_t(r.eax) < 0) {
            failed_like_guest_++;
        } else {
            host_failures_++;
            if (first_host_failures_.size() < 20) {
                first_host_failures_.push_back(strf("%s: host 0x%08x, guest 0x%08x", where(r).c_str(), hr, r.eax));
                std::fprintf(stderr, "host failure: %s\n", first_host_failures_.back().c_str());
            }
        }
    }
    if (hr != r.eax) {
        if (!m.hr_differs) { m.first_differs_hr = hr; m.first_differs_eax = r.eax; }
        m.hr_differs++;
    }
}

void Player::note_error(const std::string& what) {
    errors_++;
    if (first_errors_.size() < 60) {
        first_errors_.push_back(what);
        std::fprintf(stderr, "error: %s\n", what.c_str());
    }
}

void Player::error(const Sa2Record& r, const std::string& what) { note_error(where(r) + ": " + what); }

// An argument value (or a call) the player does not implement: the call counts
// as not handled, and the value is named in the report.
Cat Player::unimplemented(const Sa2Record& r, const std::string& what) {
    unimplemented_++;
    if (first_unimplemented_.size() < 40) {
        first_unimplemented_.push_back(where(r) + ": " + what);
        std::fprintf(stderr, "not handled: %s\n", first_unimplemented_.back().c_str());
    }
    return C_UNHANDLED;
}

void Player::stream_problem(const std::string& what) {
    stream_problems_++;
    if (first_stream_problems_.size() < 40) {
        first_stream_problems_.push_back(what);
        std::fprintf(stderr, "stream: %s\n", what.c_str());
    }
}

void Player::io_failure(const std::string& what) {
    basic_failures_.push_back("i/o: " + what);
    std::fprintf(stderr, "i/o: %s\n", what.c_str());
}

void Player::usage_failure(const std::string& what) {
    basic_failures_.push_back("usage: " + what);
    std::fprintf(stderr, "usage: %s\n", what.c_str());
}

uint32_t Player::out_addr(const Sa2Record& r, int arg, const char* what) {
    const Sa2Blob* b = r.blob(SA2S_BLOB_OUT, arg);
    if (!b || b->len < 4) {
        error(r, strf("no OUT blob for argument %d (%s)", arg, what));
        return 0;
    }
    return rd32(b->data);
}

const Sa2Blob* Player::in_blob(const Sa2Record& r, int arg, size_t need, const char* what) {
    const Sa2Blob* b = r.blob(SA2S_BLOB_IN, arg);
    if (!b) { error(r, strf("no IN blob for argument %d (%s)", arg, what)); return nullptr; }
    if (b->len < need) {
        error(r, strf("IN blob for argument %d (%s) holds %u bytes, needs %zu", arg, what, b->len, need));
        return nullptr;
    }
    return b;
}

void Player::diagnostic_failure(const std::string& why, bool fatal) {
    if (diagnostic_error_.empty()) diagnostic_error_ = why;
    diagnostic_fatal_ = diagnostic_fatal_ || fatal;
}

bool Player::report_failure(const std::string& why) {
    ++report_io_errors_;
    report_io_failed_ = true;
    if (report_io_error_.empty()) { report_io_error_ = why; io_failure(why); }
    if (!stop_) stop_reason_ = "the required report could not be written";
    stop_ = true;
    return false;
}

std::string Player::diagnostic_path_refusal() const {
    if (o_.key_log.empty() && o_.present_log.empty() && o_.synthetic_keys.empty()) return {};
    if (!o_.key_log.empty() && o_.input_path.empty()) return "--key-log needs --input-path";
    if (!o_.present_log.empty() && o_.stats_only) return "--present-log needs a device";
    std::vector<std::string> logs;
    if (!o_.key_log.empty()) logs.push_back(o_.key_log);
    if (!o_.present_log.empty()) logs.push_back(o_.present_log);
    std::vector<std::string> protected_paths;
    const auto add = [&](const std::string& s) { if (!s.empty()) protected_paths.push_back(s); };
    add(o_.report); if (!o_.report.empty()) add(o_.report + ".tmp");
    if (o_.stream != "-") add(o_.stream);
    add(o_.input_path); add(o_.synthetic_keys); add(o_.expect); add(o_.pace_log);
    add(o_.frames_out); if (!o_.frames_out.empty()) add(o_.frames_out + ".times.tsv");
    add(o_.audio_wav); if (!o_.audio_wav.empty()) add(o_.audio_wav + ".json");
    if (!o_.snap_dir.empty()) {
        add(o_.snap_dir + "/frame-00000.png");
        for (const uint32_t frame : o_.snaps) add(o_.snap_dir + strf("/frame-%05u.png", frame));
    }
    if (!o_.stats_only) {
        const char* lp = getenv("DXVK_LOG_PATH");
        if (!lp || std::strcmp(lp, "none") != 0)
            add(std::string(lp && *lp ? lp : ".") + "/sa2_player_d3d9.log");
        add(dxvk_log_path());
    }
    const auto same = [](const std::string& a, const std::string& b, bool* ok) {
#ifdef _WIN32
        // A named pipe (Windows' --input-path, \\.\pipe\<name>) has no file path to compare
        // (weakly_canonical fails on it), and no file is one: it is the same only as its name.
        const auto pipe = [](const std::string& s) {
            return s.size() > 9 && (s.compare(0, 9, "\\\\.\\pipe\\") == 0 || s.compare(0, 9, "//./pipe/") == 0);
        };
        if (pipe(a) || pipe(b)) return a == b;
#endif
        std::error_code ec;
        const fs::path x = fs::weakly_canonical(fs::absolute(a, ec), ec);
        if (ec) { *ok = false; return false; }
        const fs::path y = fs::weakly_canonical(fs::absolute(b, ec), ec);
        if (ec) { *ok = false; return false; }
        if (x == y) return true;
        const bool x_exists = fs::exists(x, ec);
        if (ec) { *ok = false; return false; }
        const bool y_exists = fs::exists(y, ec);
        if (ec) { *ok = false; return false; }
        if (x_exists && y_exists) {
            const bool equivalent = fs::equivalent(x, y, ec);
            if (ec) *ok = false;
            return equivalent;
        }
        return false;
    };
    for (size_t i = 0; i < logs.size(); ++i) {
        if (const std::string why = refuse_output(logs[i]); !why.empty()) return why;
        std::error_code ec;
        if (fs::exists(logs[i], ec) && !fs::is_regular_file(logs[i], ec))
            return "diagnostic output is not a regular file: " + logs[i];
        if (ec) return "cannot inspect diagnostic output " + logs[i] + ": " + ec.message();
        for (const auto& other : protected_paths) {
            bool ok = true;
            if (same(logs[i], other, &ok)) return "diagnostic output aliases " + other;
            if (!ok) return "cannot compare diagnostic output paths";
        }
        for (size_t j = 0; j < i; ++j) {
            bool ok = true;
            if (same(logs[i], logs[j], &ok)) return "diagnostic outputs alias each other";
            if (!ok) return "cannot compare diagnostic output paths";
        }
    }
    if (!o_.report.empty()) {
        for (const auto& report : {o_.report, o_.report + ".tmp"}) {
            if (const std::string why = refuse_output(report); !why.empty()) return why;
            for (const auto& input : {o_.input_path, o_.synthetic_keys}) if (!input.empty()) {
                bool ok = true;
                if (same(report, input, &ok)) return "report output aliases " + input;
                if (!ok) return "cannot compare report and input paths";
            }
        }
    }
    return {};
}

bool Player::open_input_diagnostics() {
    if (!o_.key_log.empty()) {
        key_log_file_ = std::fopen(o_.key_log.c_str(), "w");
        if (!key_log_file_) { ++key_log_owner_io_errors_; diagnostic_failure("cannot write " + o_.key_log); return false; }
        key_log_opened_ = true;
    }
    if (!o_.present_log.empty()) {
        present_log_file_ = std::fopen(o_.present_log.c_str(), "w");
        if (!present_log_file_) { ++present_log_io_errors_; present_log_error_ = "cannot write " + o_.present_log; diagnostic_failure(present_log_error_); return false; }
        present_log_opened_ = true;
        if (std::fputs("frame\tvirtual_us\tcall_s\treturned_s\n", present_log_file_) < 0 ||
            std::fflush(present_log_file_) != 0 || std::ferror(present_log_file_)) {
            ++present_log_io_errors_; present_log_error_ = "Present log header write/flush failed"; diagnostic_failure(present_log_error_); return false;
        }
        present_log_header_accepted_ = true;
    }
    if (!o_.input_path.empty() && key_log_file_) {
        input_ = std::make_unique<sa2_input::KeySender>(o_.input_path, key_log_file_, o_.key_log, &key_send_clock_s);
        sender_start_attempted_ = sender_started_ = key_log_header_accepted_ = false;
    }
    return true;
}

void Player::finish_input_diagnostics() {
    if (diagnostics_closed_) return;
    if (input_) {
        input_->close();
        if (!input_->log_ok()) { input_log_ok_ = false; diagnostic_failure(input_->log_error()); }
        final_host_input_ = input_->report_json();
        input_.reset();
    }
    if (const std::string error = close_diagnostic_file(key_log_file_, o_.key_log, &key_log_closed_, &key_log_owner_io_errors_, !input_log_ok_);
        !error.empty()) diagnostic_failure(error);
    if (const std::string error = close_diagnostic_file(present_log_file_, o_.present_log, &present_log_closed_, &present_log_io_errors_, present_log_io_errors_ != 0);
        !error.empty()) { if (present_log_error_.empty()) present_log_error_ = error; diagnostic_failure(error); }
    diagnostics_closed_ = true;
    if (diagnostic_fatal_ && !diagnostic_failure_registered_) {
        diagnostic_failure_registered_ = true;
        io_failure(diagnostic_error_);
    }
}

bool Player::diagnostic_evidence_ready() const {
    return !o_.key_log.empty() && !o_.present_log.empty() && diagnostics_closed_ &&
           sender_started_ && key_log_header_accepted_ && present_log_header_accepted_ &&
           key_log_opened_ && key_log_closed_ && present_log_opened_ && present_log_closed_ && input_log_ok_ &&
           diagnostic_error_.empty() && !key_log_owner_io_errors_ && !present_log_io_errors_ &&
           !event_clock_errors_ && !future_key_timestamps_ && !invalid_key_timestamps_ &&
           !present_clock_errors_ && !report_io_errors_ &&
           (o_.synthetic_keys.empty() || (synthetic_armed_ && !synthetic_schedule_failed_));
}

bool Player::write_present_row(uint32_t frame, uint64_t virtual_us, double call_s, double returned_s) {
    if (!present_log_file_ || present_log_io_errors_ || present_clock_errors_) return false;
    if (!frame || !std::isfinite(call_s) || !std::isfinite(returned_s) || call_s < 0 || returned_s < call_s) {
        ++present_clock_errors_; ++present_log_dropped_rows_;
        present_log_error_ = "invalid Present diagnostic clock"; diagnostic_failure(present_log_error_); return false;
    }
    if (std::fprintf(present_log_file_, "%u\t%" PRIu64 "\t%.9f\t%.9f\n", frame, virtual_us, call_s, returned_s) < 0 ||
        std::fflush(present_log_file_) != 0 || std::ferror(present_log_file_)) {
        ++present_log_io_errors_; ++present_log_dropped_rows_;
        present_log_error_ = "Present log row write/flush failed"; diagnostic_failure(present_log_error_); return false;
    }
    ++present_log_rows_;
    return true;
}

bool Player::load_synthetic_keys() {
    if (o_.synthetic_keys.empty()) return true;
    std::ifstream f(o_.synthetic_keys);
    if (!f) { io_failure("cannot read " + o_.synthetic_keys); return false; }
    std::string line;
    uint64_t last_ms = 0;
    while (std::getline(f, line)) {
        std::istringstream row(line);
        std::string at, scan, state, extra;
        uint64_t ms = 0, scancode = 0;
        if (!(row >> at >> scan >> state) || (row >> extra) || !canonical_u64(at, &ms) ||
            !canonical_u64(scan, &scancode) || ms > UINT64_MAX / 1000000 ||
            (!synthetic_keys_.empty() && ms < last_ms) ||
            (state == "close" ? scancode != 0
                              : (scancode >= SDL_SCANCODE_COUNT || !sa2_input::dik_of(static_cast<SDL_Scancode>(scancode)) ||
                                 (state != "down" && state != "up")))) {
            usage_failure("malformed --synthetic-keys line " + std::to_string(synthetic_keys_.size() + 1)); return false;
        }
        synthetic_keys_.push_back({ms * 1000000, static_cast<SDL_Scancode>(scancode), state == "down", state == "close"});
        if (state == "close") ++synthetic_closes_;
        last_ms = ms;
    }
    if (f.bad()) { io_failure("reading " + o_.synthetic_keys + " failed"); return false; }
    synthetic_script_sha256_ = sha256_file(o_.synthetic_keys);
    if (synthetic_script_sha256_.empty()) { io_failure("cannot hash " + o_.synthetic_keys); return false; }
    return true;
}

void Player::push_due_synthetic_keys() {
    if (!synthetic_armed_ || synthetic_schedule_failed_) return;
    const uint64_t ticks = SDL_GetTicksNS();
    while (synthetic_cursor_ < synthetic_keys_.size()) {
        const SyntheticKey& key = synthetic_keys_[synthetic_cursor_];
        if (key.offset_ns > UINT64_MAX - synthetic_armed_ticks_) {
            synthetic_schedule_failed_ = true; diagnostic_failure("synthetic timestamp overflow"); return;
        }
        const uint64_t stamp = synthetic_armed_ticks_ + key.offset_ns;
        if (ticks < stamp) return;
        SDL_Event e{};
        if (key.close) {                          // a close request, as the window manager's
            e.type = SDL_EVENT_WINDOW_CLOSE_REQUESTED;
            e.window.windowID = input_window_id_;
            e.window.timestamp = stamp;
        } else {
            e.type = key.down ? SDL_EVENT_KEY_DOWN : SDL_EVENT_KEY_UP;
            e.key.windowID = input_window_id_;
            e.key.which = sa2_input::kSyntheticKeyboard;
            e.key.scancode = key.scancode;
            e.key.down = key.down;
            e.key.repeat = false;
            e.key.timestamp = stamp;
        }
        ++synthetic_cursor_;
        if (SDL_PushEvent(&e)) ++synthetic_pushed_;
        else { ++synthetic_push_failures_; diagnostic_failure("SDL_PushEvent refused a synthetic key"); }
    }
}

std::string Player::diagnostic_json() const {
    if (o_.input_path.empty() && o_.present_log.empty()) return {};
    const std::string& first_error = diagnostic_error_.empty() ? report_io_error_ : diagnostic_error_;
    std::string s = strf("  \"latency_diagnostics\": {\"method\": \"v1\", \"clock\": \"CLOCK_MONOTONIC\", "
        "\"event_age_clock\": \"SDL_GetTicksNS\", \"event_age_unit\": \"ns\", "
        "\"handled_at\": \"after successful SDL_PollEvent\", \"presented_at\": \"host Present returned\", "
        "\"requested_keys\": %s, \"requested_presents\": %s, \"requested_synthetic\": %s, "
        "\"sender_start_attempted\": %s, \"sender_started\": %s, \"key_log_header_accepted\": %s, "
        "\"present_log_header_accepted\": %s, \"key_log_opened\": %s, \"key_log_closed\": %s, "
        "\"key_log_owner_io_errors\": %" PRIu64 ", \"future_key_timestamps\": %" PRIu64 ", "
        "\"event_clock_errors\": %" PRIu64 ", \"invalid_key_timestamps\": %" PRIu64 ", "
        "\"present_clock_errors\": %" PRIu64 ", \"report_io_errors\": %" PRIu64 ", "
        "\"closed\": %s, \"evidence_ready\": %s, \"error\": %s},\n",
        o_.key_log.empty() ? "false" : "true", o_.present_log.empty() ? "false" : "true",
        o_.synthetic_keys.empty() ? "false" : "true", sender_start_attempted_ ? "true" : "false",
        sender_started_ ? "true" : "false", key_log_header_accepted_ ? "true" : "false",
        present_log_header_accepted_ ? "true" : "false", key_log_opened_ ? "true" : "false",
        key_log_closed_ ? "true" : "false", key_log_owner_io_errors_, future_key_timestamps_, event_clock_errors_,
        invalid_key_timestamps_, present_clock_errors_, report_io_errors_, diagnostics_closed_ ? "true" : "false",
        diagnostic_evidence_ready() ? "true" : "false", first_error.empty() ? "null" : jstr(first_error).c_str());
    if (!o_.present_log.empty()) s += strf("  \"present_log\": {\"path\": %s, \"rows\": %" PRIu64
        ", \"opened\": %s, \"closed\": %s, \"io_errors\": %" PRIu64 ", \"dropped_rows\": %" PRIu64 ", \"error\": %s},\n",
        jstr(o_.present_log).c_str(), present_log_rows_, present_log_opened_ ? "true" : "false",
        present_log_closed_ ? "true" : "false", present_log_io_errors_, present_log_dropped_rows_,
        present_log_error_.empty() ? "null" : jstr(present_log_error_).c_str());
    if (!o_.synthetic_keys.empty()) s += strf("  \"synthetic_keys\": {\"path\": %s, \"script_sha256\": %s, "
        "\"lines\": %zu, \"attempted\": %zu, \"pushed\": %" PRIu64 ", \"push_failures\": %" PRIu64
        ", \"not_due\": %zu, \"armed\": %s, \"armed_ticks_ns\": %" PRIu64 ", \"armed_return_s\": %.9f, "
        "\"real_keys_ignored\": %" PRIu64 ", \"closes\": %" PRIu64 ", \"diagnostic\": true},\n",
        jstr(o_.synthetic_keys).c_str(), synthetic_script_sha256_.empty() ? "null" : jstr(synthetic_script_sha256_).c_str(),
        synthetic_keys_.size(), synthetic_cursor_, synthetic_pushed_, synthetic_push_failures_,
        synthetic_keys_.size() - synthetic_cursor_, synthetic_armed_ ? "true" : "false", synthetic_armed_ticks_, synthetic_armed_return_s_,
        real_keys_ignored_, synthetic_closes_);
    return s;
}

// ---------------------------------------------------------------------- setup

// The options a run cannot honour, and every output path but the report's
// (run() checks that one first), before anything is written; then the
// snapshot directory. false, with the failure recorded, when the run must stop.
bool Player::check_outputs() {
    if (o_.stats_only) {
        std::vector<std::string> moot;
        if (!o_.present_log.empty()) moot.push_back("--present-log");
        if (!o_.frames_out.empty()) moot.push_back("--frames-out");
        if (!o_.snaps.empty()) moot.push_back("--snap");
        if (!o_.pixels.empty()) moot.push_back("--pixel");
        if (!o_.expect.empty()) moot.push_back("--expect");
        if (!o_.dump_draws.empty()) moot.push_back("--dump-draws");
        if (o_.pace) moot.push_back("--pace");
        if (!o_.pace_log.empty()) moot.push_back("--pace-log");
        if (o_.vsync) moot.push_back("--vsync");
        if (o_.audio) moot.push_back("--audio");
        if (!moot.empty()) {
            std::string list;
            for (const auto& m : moot) list += (list.empty() ? "" : ", ") + m;
            usage_failure("--stats-only reads no frame back and makes no window: " + list + " cannot apply");
            return false;
        }
        if (o_.stage != 3) {
            usage_failure(strf("--stats-only replays at stage 3, the whole replay: --stage %d cannot apply", o_.stage));
            return false;
        }
    }
    if (o_.audio && o_.speed != 1.0) {
        usage_failure(strf("--audio plays the sound at its own rate, beside frames paced at --speed 1: --speed %g "
                           "cannot apply", o_.speed));
        return false;
    }
    if (!o_.snaps.empty() && o_.snap_dir.empty()) {
        usage_failure("--snap needs --snap-dir, or SA2_PLAY_OUT for the default $SA2_PLAY_OUT/snaps");
        return false;
    }
    if (!o_.pace_log.empty() && !o_.pace) {
        usage_failure("--pace-log writes each paced Present's times: it needs --pace");
        return false;
    }
    std::vector<std::string> outs;
    if (!o_.key_log.empty()) outs.push_back(o_.key_log);
    if (!o_.present_log.empty()) outs.push_back(o_.present_log);
    if (!o_.frames_out.empty()) { outs.push_back(o_.frames_out); outs.push_back(o_.frames_out + ".times.tsv"); }
    if (!o_.pace_log.empty()) outs.push_back(o_.pace_log);
    if (!o_.snap_dir.empty()) outs.push_back(o_.snap_dir + "/frame-00000.png");
    if (!o_.audio_wav.empty()) { outs.push_back(o_.audio_wav); outs.push_back(o_.audio_wav + ".json"); }
    // DXVK's own log lands in DXVK_LOG_PATH, or in the current directory when
    // that is unset (src/util/log/log.cpp:124-139): the same rule holds for it.
    if (!o_.stats_only) {
        const char* lp = getenv("DXVK_LOG_PATH");
        if (!lp || std::strcmp(lp, "none") != 0) outs.push_back(std::string(lp && *lp ? lp : ".") + "/sa2_player_d3d9.log");
    }
    for (const auto& p : outs)
        if (const std::string why = refuse_output(p); !why.empty()) {
            std::fprintf(stderr, "refused: %s\n", why.c_str());
            io_failure("refused: " + why);
            return false;
        }
    // A DXVK log an earlier run left is removed, so the log read at the end
    // (read_dxvk_log) is this run's: DXVK writes it afresh when it starts.
    if (const std::string log = o_.stats_only ? "" : dxvk_log_path(); !log.empty()) {
        std::error_code ec;
        fs::remove(log, ec);
        if (ec) { io_failure("cannot remove an earlier run's DXVK log " + log + ": " + ec.message()); return false; }
    }
    if (!o_.snap_dir.empty()) {
        std::error_code ec;
        fs::create_directories(o_.snap_dir, ec);
        if (ec) { io_failure("cannot create " + o_.snap_dir + ": " + ec.message()); return false; }
    }
    return true;
}

bool Player::validate_host_files() {
    // SDL's dynamic API (SDL_dynapi.c): with SDL3_DYNAMIC_API naming a library, the first SDL call
    // routes every SDL function into that library, while the file checked below (the one
    // SDL_Init's address lies in), SDL_GetVersion and SDL_GetRevision still read as the verified
    // SDL. So the player refuses to start with it set at all, before any SDL call
    // (host/player/player_env.sh unsets it).
    if (getenv("SDL3_DYNAMIC_API"))
        return device_failure("SDL3_DYNAMIC_API is set: SDL would route every call into the library it names, "
                              "not the verified one");
    const auto verify = [&](const char* key, const char* expected) {
        const auto found = files_.find(key);
        return found != files_.end() && !found->second.sha256.empty() && found->second.sha256 == expected;
    };
    // No SDL API, DXVK dlopen or window call precedes file validation.
    sdl_identity_ok_ = verify("libSDL3", sa2_host_expected::sdl_sha256);
    if (!sdl_identity_ok_) return device_failure("loaded SDL library SHA-256 differs from the build expectation");
#if !SA2_PLAYER_GPU
    // (sa2_player_gpu loads no library but SDL: its renderer is its own file, files.player.)
    dxvk_identity_ok_ = verify("libdxvk_d3d9", sa2_host_expected::dxvk_sha256);
    if (!dxvk_identity_ok_) return device_failure("DXVK library SHA-256 differs from the build expectation");
    config_identity_ok_ = verify("dxvk_conf", sa2_host_expected::dxvk_config_sha256);
    if (!config_identity_ok_) return device_failure("DXVK configuration SHA-256 differs from the build expectation");
    const char* overlay = getenv("DXVK_CONFIG");
    if (overlay && *overlay) return device_failure("DXVK_CONFIG would override the verified configuration file");
#endif
    sdl_version_ = SDL_GetVersion();
    const char* revision = SDL_GetRevision();
    sdl_revision_ = revision ? revision : "";
    if (sdl_version_ != sa2_host_expected::sdl_version || sdl_revision_ != sa2_host_expected::sdl_revision)
        return device_failure("loaded SDL version/revision differs from the exact header expectation");
    // The verified SDL said in words, patched or stock (sdl_mark.h; SDL's zlib licence, clause 2).
    std::printf("%s\n", sa2_sdl_mark_line(sdl_revision_).c_str());
    return true;
}

bool Player::setup_window(uint32_t width, uint32_t height) {
    if (!width || !height || width > 16384 || height > 16384)
        return device_failure(strf("invalid presentation window dimensions %u x %u", width, height));
    if (win_) {
#if defined(__ANDROID__)
        // android_app.py arm_portable: Android's window is the screen; Present scales onto it.
        (void)SDL_SetWindowSize(win_, int(width), int(height));
#else
        if (!SDL_SetWindowSize(win_, int(width), int(height)))
            return device_failure(strf("SDL_SetWindowSize: %s", SDL_GetError()));
#endif
        window_reused_ = true;
        return true;
    }
    // The game's name alone: the host and the stream are the report's (and the launcher's record's).
    const std::string title = "Sonic Adventure 2";
    win_ = SDL_CreateWindow(title.c_str(), int(width), int(height),
                            SDL_WINDOW_VULKAN | (o_.hidden ? SDL_WINDOW_HIDDEN : 0));
    if (!win_) return device_failure(strf("SDL_CreateWindow: %s", SDL_GetError()));
    window_created_ = true;
    input_window_id_ = SDL_GetWindowID(win_);
    ++windows_created_;
    last_pump_ = now_s();
    return true;
}

bool Player::accept_live_outcome(const Sa2ReadOutcome& outcome) {
    switch (outcome.status) {
    case Sa2ReadStatus::Ready: live_status_ = "ready"; return true;
    case Sa2ReadStatus::End: live_status_ = "end"; return false;
    case Sa2ReadStatus::Cancelled:
        live_status_ = "cancelled";
        stream_problem("live reader cancelled: " + outcome.detail);
        stop_reason_ = close_requests_ ? "the window was closed" : "live reader cancelled";
        break;
    case Sa2ReadStatus::Malformed:
        live_status_ = "malformed";
        stream_problem("live reader malformed: " + outcome.detail);
        stop_reason_ = "malformed stream";
        break;
    case Sa2ReadStatus::IoFailure:
        live_status_ = "io_failure";
        io_failure("live reader I/O failure: " + outcome.detail);
        stop_reason_ = "the stream could not be read";
        break;
    }
    live_detail_ = outcome.detail;
    stop_ = true;
    return false;
}

bool Player::setup_sdl() {
    SDL_SetHint(SDL_HINT_NO_SIGNAL_HANDLERS, "1");
    // --end-fd: one click on the window's close is one request, not a close and a quit.
    if (o_.end_fd >= 0) SDL_SetHint(SDL_HINT_QUIT_ON_LAST_WINDOW_CLOSE, "0");
    if (!SDL_Init(SDL_INIT_VIDEO)) {
        device_failure(strf("SDL_Init(SDL_INIT_VIDEO): %s", SDL_GetError()));
        return false;
    }
    sdl_up_ = true;
    sdl_driver_ = SDL_GetCurrentVideoDriver() ? SDL_GetCurrentVideoDriver() : "?";
    return true;
}

// The files this run's pictures depend on, pinned by content.
void Player::pin_host_files() {
    auto pin = [&](const char* key, const std::string& path) {
        Pin p;
        p.path = path;
        std::error_code ec;
        p.real = path.empty() ? "" : fs::weakly_canonical(fs::path(path), ec).string();
        p.sha256 = p.real.empty() ? "" : sha256_file(p.real);
        files_[key] = p;
    };
#if SA2_PLAYER_GPU
    // sa2_player_gpu loads neither DXVK's library nor dxvk.conf: neither is pinned (both
    // null in the report), so the second host's run pins no DXVK file it never read.
#ifdef _WIN32
    // The module this executable imports SDL_Init from: SDL3.dll, the name its import table
    // holds, read back as the file the loader mapped (SDL's headers declare no dllimport, so
    // &SDL_Init is this executable's import thunk, not an address in SDL3.dll).
    const HMODULE sdl_module = GetModuleHandleW(L"SDL3.dll");
    wchar_t sdl_file[32768];
    const DWORD sdl_n = sdl_module ? GetModuleFileNameW(sdl_module, sdl_file, DWORD(sizeof sdl_file / sizeof sdl_file[0]))
                                   : 0;
    const bool sdl_found = sdl_n > 0 && sdl_n < sizeof sdl_file / sizeof sdl_file[0]
                           && GetProcAddress(sdl_module, "SDL_Init") != nullptr;
    pin("libSDL3", sdl_found ? fs::path(std::wstring(sdl_file, sdl_n)).string() : "");
#else
    Dl_info info{};
    void* sdl_init_symbol = dlsym(RTLD_DEFAULT, "SDL_Init");
    pin("libSDL3", sdl_init_symbol && dladdr(sdl_init_symbol, &info) && info.dli_fname ? info.dli_fname : "");
#endif
#else
    const char* lib = getenv("SA2_DXVK_D3D9");
    pin("libdxvk_d3d9", !o_.dxvk.empty() ? o_.dxvk : (lib ? lib : ""));
    Dl_info info{};
    void* sdl_init_symbol = dlsym(RTLD_DEFAULT, "SDL_Init");
    pin("libSDL3", sdl_init_symbol && dladdr(sdl_init_symbol, &info) && info.dli_fname ? info.dli_fname : "");
    const char* conf = getenv("DXVK_CONFIG_FILE");
    pin("dxvk_conf", conf ? conf : "");
#endif
}

// Both files are truncated now, so a run that never reads a frame back leaves
// no frames behind from an earlier run.
bool Player::open_frames_out() {
    y4m_ = std::fopen(o_.frames_out.c_str(), "wb");
    times_ = std::fopen((o_.frames_out + ".times.tsv").c_str(), "w");
    if (!y4m_ || !times_) {
        io_failure("cannot write " + o_.frames_out + " and its .times.tsv");
        return false;
    }
    std::fprintf(times_, "frame\tseq\tvirtual_us\tguest_frames\tpolls\twall_us\tbgrx_hash\n");
    return true;
}

// Truncated now too, for the same reason. One row per paced Present (do_present).
bool Player::open_pace_log() {
    pace_log_ = std::fopen(o_.pace_log.c_str(), "w");
    if (!pace_log_) {
        io_failure("cannot write " + o_.pace_log);
        return false;
    }
    std::fprintf(pace_log_, "frame\tvirtual_us\tready_s\tdue_s\tpresent_s\tmoved_ms\tqueue_ms\n");
    return true;
}

bool Player::device_failure(const std::string& why) {
    if (device_failure_.empty()) device_failure_ = why;
    std::fprintf(stderr, "device: %s\n", why.c_str());
    stop_ = true;
    stop_reason_ = "device creation failed";
    return false;
}

bool Player::create_device(const Sa2Record& r, Cat* cat) {
    const Sa2Blob* ppb = r.blob(SA2S_BLOB_IN, 4);
    if (ppb && ppb->len >= 56) std::memcpy(guest_pp_, ppb->data, 56);
    else error(r, "no 56-byte IN blob for the presentation parameters");
    std::memcpy(creation_pp_, guest_pp_, sizeof creation_pp_);     // before the defaults below fill it
    if (o_.explicit_live() && (!guest_pp_[0] || !guest_pp_[1] || guest_pp_[0] > 16384 || guest_pp_[1] > 16384))
        return device_failure("live presentation dimensions are zero or outside the validated window bound");
    if (!guest_pp_[0] || !guest_pp_[1]) {
        *cat = unimplemented(r, strf("BackBufferWidth x Height %u x %u (0 means the window's size, which the stream "
                                     "does not carry): the player makes 1280x720", guest_pp_[0], guest_pp_[1]));
        guest_pp_[0] = guest_pp_[0] ? guest_pp_[0] : 1280;
        guest_pp_[1] = guest_pp_[1] ? guest_pp_[1] : 720;
    }
    if (guest_pp_[4])
        *cat = unimplemented(r, strf("MultiSampleType %u: the player renders without multisampling, because "
                                     "GetRenderTargetData cannot read a multisampled back buffer", guest_pp_[4]));
    guest_behavior_ = r.arg(3);
    const uint32_t vp = guest_behavior_ & kVpBits;
    const bool one_vp = vp == D3DCREATE_SOFTWARE_VERTEXPROCESSING || vp == D3DCREATE_HARDWARE_VERTEXPROCESSING ||
                        vp == D3DCREATE_MIXED_VERTEXPROCESSING;
    if (vp != D3DCREATE_HARDWARE_VERTEXPROCESSING)
        *cat = unimplemented(r, strf("BehaviorFlags 0x%08x: vertex processing 0x%02x; only "
                                     "HARDWARE_VERTEXPROCESSING has been exercised", guest_behavior_, vp));
    // The guest's vertex-processing bits set DXVK's constant limits (r3 16.6);
    // MULTITHREADED and PUREDEVICE are the recording device's business.
    host_behavior_ = (one_vp ? vp : uint32_t(D3DCREATE_HARDWARE_VERTEXPROCESSING)) | D3DCREATE_FPU_PRESERVE;
    dev_addr_ = out_addr(r, 5, "ppReturnedDeviceInterface");
    // The implicit render target and automatic depth-stencil are guest objects
    // the recording device creates inside CreateDevice; the recorder names them
    // in the call's DESC (sa2s_desc_device). Without one, they are the next two
    // objects after the device (runtime/sa2_d3d9.c m_CreateDevice) at the
    // recording device's 16-byte stride.
    if (const Sa2Blob* d = r.blob(SA2S_BLOB_DESC, -1); d && d->len >= sizeof(sa2s_desc_device)) {
        sa2s_desc_device dd;
        std::memcpy(&dd, d->data, sizeof dd);
        if (dd.device != dev_addr_) error(r, strf("DESC names device 0x%08x, OUT says 0x%08x", dd.device, dev_addr_));
        rt_addr_ = dd.render_target;
        ds_addr_ = dd.depth_stencil;
        implicit_from_ = "desc";
    } else {
        rt_addr_ = dev_addr_ + 16;
        ds_addr_ = guest_pp_[9] ? dev_addr_ + 32 : 0;
        implicit_from_ = "inferred";
    }
    // The recording device's CreateDevice binds the implicit pair: its bindings,
    // which target_query holds the guest's answers to, start here.
    bound_rt_[0] = rt_addr_;
    bound_ds_ = ds_addr_;
    bb_w_ = guest_pp_[0];
    bb_h_ = guest_pp_[1];
    bb_fmt_ = guest_pp_[2] ? guest_pp_[2] : 22u;
    if (!tex::decodable(bb_fmt_))
        return device_failure(strf("back buffer format %u is not one the player can read back", bb_fmt_));
    // Everything above needs no device; a stats read stops here.
    if (!host()) {
        stats_device_ = true;
        return implicit_surfaces();
    }

    // Direct3DCreate9's preconditions, checked exactly, because a DxvkError
    // thrown inside it cannot be caught in this process (see the catch below):
    // the WSI named SDL3, SDL video up, a Vulkan window whose creation loaded the
    // Vulkan loader, SDL's instance extensions, and DXVK's LoadLibraryA of
    // "libSDL3.so.0" resolving to the SDL this process runs (r3 2.4, 2.6).
    // sa2_player_gpu checks the same but DXVK's own (the WSI and the library's path).
#if !SA2_PLAYER_GPU
    const char* wsi = getenv("DXVK_WSI_DRIVER");
    if (!wsi || std::strcmp(wsi, "SDL3") != 0)
        return device_failure(strf("DXVK_WSI_DRIVER is %s%s%s, not SDL3 (DXVK would abort in Direct3DCreate9)",
                                   wsi ? "\"" : "", wsi ? wsi : "unset", wsi ? "\"" : ""));
    const std::string lib = files_.count("libdxvk_d3d9") ? files_["libdxvk_d3d9"].path : "";
    if (lib.empty() || lib[0] != '/') return device_failure("no absolute libdxvk_d3d9 path (--dxvk or SA2_DXVK_D3D9)");
#endif
    if (!sdl_up_) return device_failure("SDL video is not initialised");
    if (!setup_window(bb_w_, bb_h_)) return false;
    pump_events();
    if (stop_) return false;
    Uint32 nexts = 0;
    if (!SDL_Vulkan_GetInstanceExtensions(&nexts) || !nexts)
        return device_failure(strf("SDL_Vulkan_GetInstanceExtensions: %s", SDL_GetError()));
#ifdef _WIN32
    // DXVK's WSI is not loaded on this host; the check is that SDL3.dll is the module this
    // process loaded SDL from (pin_host_files' libSDL3, which validate_host_files hashed).
    {
        const HMODULE named = GetModuleHandleW(L"SDL3.dll");
        if (!named || !GetProcAddress(named, "SDL_Init"))
            return device_failure("SDL3.dll does not resolve to the SDL this process loaded");
    }
#else
#if defined(__ANDROID__)
    // android_app.py arm_portable: the app's SDL is lib/<abi>/libSDL3.so (no versioned soname).
    if (void* h = dlopen("libSDL3.so", RTLD_NOW | RTLD_NOLOAD)) dlclose(h);
#else
    if (void* h = dlopen("libSDL3.so.0", RTLD_NOW | RTLD_NOLOAD)) dlclose(h);
#endif
    else return device_failure("libSDL3.so.0 does not resolve to the SDL this process loaded (DXVK's WSI would "
                               "load another, or none)");
#endif
    using Create9 = IDirect3D9* (*)(UINT);
#if SA2_PLAYER_GPU
    // host/d3d9gpu's factory, linked into this binary: no library to load or verify; the
    // renderer's bytes are this file's (files.player).
    const Create9 create9 = &Direct3DCreate9;
    // Whether this is a live session (--interactive), read by the device as it is made
    // (host/d3d9gpu/d9g_gpu_sdl.cpp make_sdl_gpu, GpuInfo::live_session): on a GPU without depth clip
    // and clamp a live session draws clip-only, where a replay refuses each draw by name.
    if (!SDL_SetBooleanProperty(SDL_GetGlobalProperties(), "sa2.live_session", o_.interactive))
        return device_failure(strf("SDL_SetBooleanProperty(sa2.live_session): %s", SDL_GetError()));
#else
    lib_ = dlopen(lib.c_str(), RTLD_NOW | RTLD_LOCAL);
    if (!lib_) return device_failure(strf("dlopen: %s", dlerror()));
    auto create9 = reinterpret_cast<Create9>(dlsym(lib_, "Direct3DCreate9"));
    if (!create9) return device_failure("dlsym(Direct3DCreate9) failed");
    Dl_info actual_dxvk{};
    if (!dladdr(reinterpret_cast<void*>(create9), &actual_dxvk) || !actual_dxvk.dli_fname ||
        sha256_file(actual_dxvk.dli_fname) != sa2_host_expected::dxvk_sha256)
        return device_failure("loaded DXVK factory library SHA-256 differs from the build expectation");
    // Recheck the file before the factory consumes it, preserving its exact contract.
    const char* configuration = getenv("DXVK_CONFIG_FILE");
    if (!configuration || sha256_file(configuration) != sa2_host_expected::dxvk_config_sha256)
        return device_failure("DXVK configuration changed before the factory call");
#endif

    phase_ = "Direct3DCreate9";
    if (!write_report(false)) return false;       // stop before a factory call without its required report
    // A second line only. DXVK is linked with -static-libgcc -static-libstdc++
    // (its meson.build:171-173). When its unwinder reaches this frame it hands
    // its own _Unwind_Context to this process's __gxx_personality_v0, whose calls
    // into the shared libgcc_s abort the process (measured with
    // DXVK_WSI_DRIVER=BOGUS: SIGABRT from libgcc_s.so.1 under
    // __gxx_personality_v0). So a DxvkError from Direct3DCreate9 is never caught
    // here: the checks above are the defence, and the report just written is
    // the receipt when they are not enough.
    try { d3d_ = create9(D3D_SDK_VERSION); }
    catch (...) { return device_failure("Direct3DCreate9 threw; see DXVK's log"); }
    if (!d3d_) return device_failure("Direct3DCreate9 returned NULL");
    D3DADAPTER_IDENTIFIER9 id = {};
    if (SUCCEEDED(d3d_->GetAdapterIdentifier(D3DADAPTER_DEFAULT, 0, &id))) {
        adapter_ = strf("%s (vendor %04x device %04x)", id.Description, unsigned(id.VendorId), unsigned(id.DeviceId));
        adapter_driver_ = id.Driver;
    }

    D3DPRESENT_PARAMETERS pp = {};
    pp.BackBufferWidth = bb_w_;
    pp.BackBufferHeight = bb_h_;
    pp.BackBufferFormat = D3DFORMAT(bb_fmt_);
    pp.BackBufferCount = std::min<uint32_t>(std::max<uint32_t>(guest_pp_[3], 1u), 3u);
    pp.MultiSampleType = D3DMULTISAMPLE_NONE;     // readable by GetRenderTargetData; a guest value is counted above
    pp.MultiSampleQuality = 0;
    pp.SwapEffect = D3DSWAPEFFECT_DISCARD;
    pp.hDeviceWindow = reinterpret_cast<HWND>(win_);
    pp.Windowed = TRUE;
    pp.EnableAutoDepthStencil = guest_pp_[9] ? TRUE : FALSE;
    pp.AutoDepthStencilFormat = D3DFORMAT(guest_pp_[10]);
    pp.Flags = guest_pp_[11];
    pp.FullScreen_RefreshRateInHz = 0;
    pp.PresentationInterval = o_.vsync ? D3DPRESENT_INTERVAL_ONE : D3DPRESENT_INTERVAL_IMMEDIATE;
    phase_ = "CreateDevice";
    if (!write_report(false)) return false;
    cw_before_ = x87_cw();
    HRESULT hr = d3d_->CreateDevice(D3DADAPTER_DEFAULT, D3DDEVTYPE_HAL, reinterpret_cast<HWND>(win_),
                                    host_behavior_, &pp, &dev_);
    cw_after_ = x87_cw();
    phase_ = "replay";
    std::printf("device: %ux%u fmt %u, depth %s fmt %u, interval %s, behavior 0x%x (guest 0x%x), x87 control word "
                "%04x -> %04x\n", bb_w_, bb_h_, bb_fmt_, guest_pp_[9] ? "auto" : "none", guest_pp_[10],
                o_.vsync ? "ONE" : "IMMEDIATE", host_behavior_, guest_behavior_, cw_before_, cw_after_);
    std::printf("sdl video driver: %s; adapter 0: %s\n", sdl_driver_.c_str(), adapter_.c_str());
    if (FAILED(hr) || !dev_) { dev_ = nullptr; return device_failure(strf("CreateDevice failed: 0x%08x", unsigned(hr))); }
    if (FAILED(dev_->GetBackBuffer(0, 0, D3DBACKBUFFER_TYPE_MONO, &bb_))) return device_failure("GetBackBuffer failed");
    if (guest_pp_[9] && FAILED(dev_->GetDepthStencilSurface(&ds_))) return device_failure("GetDepthStencilSurface failed");
    if (FAILED(dev_->CreateOffscreenPlainSurface(bb_w_, bb_h_, D3DFORMAT(bb_fmt_), D3DPOOL_SYSTEMMEM, &readback_,
                                                 nullptr)))
        return device_failure("the SYSTEMMEM readback surface could not be created");
    if (FAILED(dev_->CreateTexture(1, 1, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &white_, nullptr)))
        return device_failure("the placeholder texture could not be created");
    D3DLOCKED_RECT lr;
    if (FAILED(white_->LockRect(0, &lr, nullptr, 0))) return device_failure("the placeholder texture did not lock");
    const uint32_t white = 0xFFFFFFFFu;
    std::memcpy(lr.pBits, &white, 4);
    white_->UnlockRect(0);
    return implicit_surfaces();
}

// The implicit render target and automatic depth-stencil, under the guest's
// names for them (in a stats read, made as a replay makes them: the depth
// surface only with EnableAutoDepthStencil). Then the report at phase
// "replay", 0 Presents: the device is made, and a death from here on is the
// replay's.
bool Player::implicit_surfaces() {
    Surf rt;
    rt.kind = Surf::BACKBUFFER; rt.host = bb_; rt.made = true; rt.w = bb_w_; rt.h = bb_h_; rt.fmt = bb_fmt_;
    surf_[rt_addr_] = rt;
    if (ds_addr_) {
        Surf ds;
        ds.kind = Surf::AUTODEPTH; ds.host = ds_; ds.made = host() ? ds_ != nullptr : guest_pp_[9] != 0;
        ds.w = bb_w_; ds.h = bb_h_; ds.fmt = guest_pp_[10];
        surf_[ds_addr_] = ds;
    }
    phase_ = "replay";
    return write_report(false);
}

// -------------------------------------------------------------------- objects

IDirect3DBaseTexture9* Player::bindable(uint32_t addr) {
    if (!addr) return nullptr;
    Tex* t = texture(addr);
    if (t && t->host) return t->host;
    placeholders_bound_++;
    return white_;
}

// The host surface a SetRenderTarget or SetDepthStencilSurface names (a texture
// level's is fetched on first use), and in *made whether there is one: none
// for an unknown surface or one without a host object. A stats read fetches
// nothing and returns nullptr, with *made saying whether a replay would have
// one: the surface is made, or it is a level of a made texture.
IDirect3DSurface9* Player::surface_for_target(uint32_t addr, bool* made) {
    *made = false;
    if (!addr) return nullptr;
    auto it = surf_.find(addr);
    if (it == surf_.end()) return nullptr;
    Surf& s = it->second;
    if (s.kind == Surf::FACE) {                   // a cube's face: its host surface is fetched at GetCubeMapSurface
        CubeVol* c = cubevol(s.tex);
        if (!host()) { *made = c && c->made && s.level < c->host_levels; return nullptr; }
        if (!s.host && c && c->cube && s.level < c->host_levels)
            c->cube->GetCubeMapSurface(D3DCUBEMAP_FACES(s.face), s.level, &s.host);
        *made = s.host != nullptr;
        return s.host;
    }
    if (!host()) {
        const Tex* t = s.kind == Surf::LEVEL ? texture(s.tex) : nullptr;
        *made = s.kind == Surf::LEVEL ? t && t->made && s.level < t->host_levels : s.made;
        return nullptr;
    }
    if (!s.host && s.kind == Surf::LEVEL) {
        Tex* t = texture(s.tex);
        if (t && t->host && s.level < t->host_levels) t->host->GetSurfaceLevel(s.level, &s.host);
    }
    *made = s.host != nullptr;
    return s.host;
}

bool Player::create_host_texture(Tex& t) {
    t.host_fmt = t.fmt == tex::FMT_R8G8B8 ? tex::FMT_X8R8G8B8 : t.fmt;
    if (!dxvk_format_ok(t.host_fmt)) {
        note_error(strf("guest texture 0x%08x has format %u, which DXVK has no Vulkan format for", t.addr, t.fmt));
        return false;
    }
    uint32_t usage = t.usage & (D3DUSAGE_RENDERTARGET | D3DUSAGE_DEPTHSTENCIL | D3DUSAGE_DYNAMIC);
    uint32_t pool = t.pool;
    if (usage & (D3DUSAGE_RENDERTARGET | D3DUSAGE_DEPTHSTENCIL)) {
        pool = D3DPOOL_DEFAULT;
        usage &= ~uint32_t(D3DUSAGE_DYNAMIC);
    } else {
        // A texture the player fills itself must be lockable: MANAGED keeps a
        // CPU copy and uploads at draw. Nothing the guest sees depends on it.
        pool = D3DPOOL_MANAGED;
        usage = 0;
    }
    if (!t.host_levels) t.host_levels = t.levels;
    if (!host()) return t.made = true;           // a stats read: made, as far as the stream shows
    HRESULT hr = dev_->CreateTexture(t.w, t.h, t.host_levels, usage, D3DFORMAT(t.host_fmt), D3DPOOL(pool),
                                     &t.host, nullptr);
    if (FAILED(hr)) {
        t.host = nullptr;
        note_error(strf("CreateTexture %ux%u levels %u fmt %s usage 0x%x pool %u for guest 0x%08x: 0x%08x", t.w,
                        t.h, t.host_levels, tex::format_name(t.fmt), usage, pool, t.addr, unsigned(hr)));
        return false;
    }
    t.made = t.host != nullptr;
    tex_by_source_[t.source]++;
    return true;
}

// Upload one level of the player's copy to the host texture. false without an
// error for the levels the design leaves alone (no host object at this stage,
// a level the host does not have yet, a render target, no bytes); false with an
// error counted when the upload itself fails. A stats read converts and sizes
// the level as a replay does, and uploads nothing (false).
bool Player::upload_level(Tex& t, uint32_t level) {
    if (!t.made || level >= t.host_levels || level >= t.data.size() || t.data[level].empty()) return false;
    if (t.usage & (D3DUSAGE_RENDERTARGET | D3DUSAGE_DEPTHSTENCIL)) return false;
    const uint32_t lw = tex::level_dim(t.w, level), lh = tex::level_dim(t.h, level);
    const std::vector<uint8_t>* src = &t.data[level];
    std::vector<uint8_t> conv;
    if (t.host_fmt != t.fmt) {
        std::vector<uint8_t> bgra;
        if (!tex::decode_level(src->data(), lw, lh, t.fmt, 0, &bgra) ||
            !tex::encode_level(bgra.data(), lw, lh, t.host_fmt, &conv)) {
            note_error(strf("texture 0x%08x level %u: cannot convert %s to %s for DXVK", t.addr, level,
                            tex::format_name(t.fmt), tex::format_name(t.host_fmt)));
            return false;
        }
        src = &conv;
    }
    const uint32_t pitch = tex::level_pitch(lw, t.host_fmt), rows = tex::level_rows(lh, t.host_fmt);
    if (src->size() < size_t(pitch) * rows) {
        note_error(strf("texture 0x%08x level %u: %zu bytes, the level needs %zu", t.addr, level, src->size(),
                        size_t(pitch) * rows));
        return false;
    }
    if (!host()) return false;
    D3DLOCKED_RECT lr;
    const HRESULT hr = t.host->LockRect(level, &lr, nullptr, 0);
    if (FAILED(hr)) {
        note_error(strf("texture 0x%08x level %u: host LockRect failed 0x%08x", t.addr, level, unsigned(hr)));
        return false;
    }
    if (lr.Pitch < 0 || uint32_t(lr.Pitch) < pitch) {
        t.host->UnlockRect(level);
        note_error(strf("texture 0x%08x level %u: host pitch %d is narrower than %u", t.addr, level, int(lr.Pitch), pitch));
        return false;
    }
    for (uint32_t y = 0; y < rows; y++)
        std::memcpy(static_cast<uint8_t*>(lr.pBits) + size_t(y) * lr.Pitch, src->data() + size_t(y) * pitch, pitch);
    t.host->UnlockRect(level);
    bytes_uploaded_ += size_t(pitch) * rows;
    return true;
}

// The recording device hands the guest a packed store and ignores pRect, so a
// sub-rectangle lock's rows start at the level's origin with the level pitch
// (the critic's section 2, item 6), and only each row's first (right - left)
// pixels, or block columns, belong to the rectangle. They are placed at the
// rectangle here. A rectangle outside the level, or (DXT) off the 4x4 block
// grid, is an error and nothing is stored.
bool Player::store_locked(const Sa2Record& r, Tex& t, uint32_t level, const Lock& lk, const Sa2Blob& b) {
    if (level >= t.levels) { error(r, strf("level %u of a %u-level texture", level, t.levels)); return false; }
    return store_rows(r, tex::level_dim(t.w, level), tex::level_dim(t.h, level), t.fmt, level, lk, b, &t.data, level);
}

// store_locked's placing of a locked region's rows into (*chain)[index], a level of lw x lh
// in fmt (a 2D texture's level, a cube face's level): every check comes before the level is
// touched, which is allocated (zeros) on its first store.
bool Player::store_rows(const Sa2Record& r, uint32_t lw, uint32_t lh, uint32_t fmt, uint32_t level, const Lock& lk,
                        const Sa2Blob& b, std::vector<std::vector<uint8_t>>* chain, uint32_t index) {
    const uint32_t packed = tex::level_pitch(lw, fmt), rows = tex::level_rows(lh, fmt);
    if (!packed) { error(r, strf("format %s has no size the player knows", tex::format_name(fmt))); return false; }
    uint32_t row0 = 0, nrows = rows, col_bytes = 0, width_bytes = packed;
    if (lk.has_rect) {
        const int32_t L = lk.rect.left, T = lk.rect.top, R = lk.rect.right, B = lk.rect.bottom;
        const bool inside = 0 <= L && L < R && R <= int32_t(lw) && 0 <= T && T < B && B <= int32_t(lh);
        const uint32_t bb = tex::block_bytes(fmt);
        const bool on_grid = !bb || (L % 4 == 0 && T % 4 == 0 && (R % 4 == 0 || R == int32_t(lw)) &&
                                     (B % 4 == 0 || B == int32_t(lh)));
        if (!inside || !on_grid) {
            error(r, strf("lock rectangle (%d,%d)-(%d,%d) is not %s level %u (%ux%u %s); nothing is uploaded", L, T,
                          R, B, inside ? "on the 4x4 block grid of" : "inside", level, lw, lh,
                          tex::format_name(fmt)));
            return false;
        }
        if (bb) {
            row0 = uint32_t(T) / 4; nrows = (uint32_t(B) + 3) / 4 - row0;
            col_bytes = (uint32_t(L) / 4) * bb; width_bytes = ((uint32_t(R) + 3) / 4 - uint32_t(L) / 4) * bb;
        } else {
            const uint32_t bpp = tex::pixel_bytes(fmt);
            row0 = uint32_t(T); nrows = uint32_t(B - T); col_bytes = uint32_t(L) * bpp; width_bytes = uint32_t(R - L) * bpp;
        }
    }
    const uint32_t pitch = lk.pitch ? lk.pitch : packed;
    if (pitch < width_bytes) {
        error(r, strf("lock pitch %u is narrower than a row's %u bytes", pitch, width_bytes));
        return false;
    }
    if (lk.rows && lk.rows != nrows) {
        error(r, strf("the LOCK blob says %u rows; the locked region has %u", lk.rows, nrows));
        return false;
    }
    const size_t need = size_t(nrows - 1) * pitch + width_bytes;
    if (b.len < need) {
        error(r, strf("the LOCKED blob holds %u bytes; the locked region needs %zu", b.len, need));
        return false;
    }
    if (chain->size() <= index) chain->resize(index + 1);
    auto& d = (*chain)[index];
    if (d.size() != size_t(packed) * rows) d.assign(size_t(packed) * rows, 0);
    for (uint32_t y = 0; y < nrows; y++)          // row0 + nrows <= rows, col_bytes + width_bytes <= packed
        std::memcpy(d.data() + size_t(row0 + y) * packed + col_bytes, b.data + size_t(y) * pitch, width_bytes);
    return true;
}

// The lock an Unlock closes, as the recorder sized it (its LOCK blob,
// sa2s_lock_info), which replaces what the player saw at the Lock; false, and
// `lk` untouched, when the Unlock carries none.
bool Player::lock_of(const Sa2Record& r, Lock* lk, sa2s_lock_info* info) {
    const Sa2Blob* b = r.blob(SA2S_BLOB_LOCK, -1);
    if (!b || b->len < sizeof(sa2s_lock_info)) return false;
    std::memcpy(info, b->data, sizeof *info);
    lk->active = true;
    lk->pitch = info->pitch;
    lk->rows = info->rows;
    lk->flags = info->flags;
    lk->has_rect = info->has_rect != 0;
    lk->rect = RECT{info->rect[0], info->rect[1], info->rect[2], info->rect[3]};
    lock_info_used_++;
    return true;
}

// A texture level's Unlock: its LOCKED bytes go into the player's copy and up
// to the host. A READONLY lock wrote nothing, and its bytes are the recording
// device's store, which need not hold the level's pixels (a file texture's
// store is empty), so it uploads nothing.
Cat Player::unlock_upload(const Sa2Record& r, Tex& t, uint32_t level, const Lock& lk) {
    const Sa2Blob* b = r.blob(SA2S_BLOB_LOCKED, -1);
    if (!b) { error(r, "no LOCKED blob"); return C_HANDLED; }
    if (o_.stage < 3) return C_STAGE;
    if (lk.flags & D3DLOCK_READONLY) { readonly_locks_++; return C_HANDLED; }
    if (store_locked(r, t, level, lk, *b) && upload_level(t, level)) lock_uploads_++;
    return C_HANDLED;
}

void Player::texture_create(uint32_t addr, uint32_t w, uint32_t h, uint32_t levels, uint32_t usage,
                            uint32_t fmt, uint32_t pool, const char* source) {
    Tex t;
    t.addr = addr;
    t.w = w; t.h = h; t.levels = levels ? levels : 1u; t.usage = usage; t.fmt = fmt; t.pool = pool;
    t.source = source;
    t.locks.resize(t.levels);
    t.data.resize(t.levels);
    t.host_levels = t.levels;
    if (o_.stage >= 3) create_host_texture(t);
    tex_[addr] = std::move(t);
}

// ------------------------------------------------------ cube and volume textures

// The level count the recording device answers a cube's GetLevelCount with, whichever
// creator made it (runtime/sa2_d3d9.c cube_exposed_levels over cube_stored_levels; the D3DX
// creators' MipLevels of 0 or D3DX_DEFAULT is the complete chain, d3dx_levels, which this
// gives too): the complete chain for 0 or for more levels than it holds, 1 with
// D3DUSAGE_AUTOGENMIPMAP (which the player does not handle), else the count asked for.
uint32_t cube_levels(uint32_t edge, uint32_t want, uint32_t usage) {
    const uint32_t full = tex::full_chain(edge, edge);
    if (usage & D3DUSAGE_AUTOGENMIPMAP) return 1;
    return !want || want > full ? full : want;
}

// A creator's answer is a new object: the recording device never hands an address out twice
// (runtime/sa2_d3d9.c obj_new numbers its pool in order and reuses no slot), so an answer
// the player already knows as an object is not a creation it can replay. An error, by name.
bool Player::fresh_object(const Sa2Record& r, uint32_t addr) {
    const char* what = texture(addr) ? "a 2D texture" : cubevol(addr) ? "a cube or volume texture"
                       : surf_.count(addr) ? "a surface" : buf_.count(addr) ? "a buffer" : nullptr;
    if (what) error(r, strf("answered 0x%08x, which the player already knows as %s", addr, what));
    return !what;
}

void Player::cubevol_create(const Sa2Record& r, uint32_t addr, bool volume, uint32_t w, uint32_t h, uint32_t depth,
                            uint32_t levels, uint32_t usage, uint32_t fmt, uint32_t pool, const char* source) {
    fresh_object(r, addr);
    CubeVol t;
    t.volume = volume;
    t.addr = addr;
    t.w = w; t.h = h; t.depth = volume && depth ? depth : 1u; t.levels = levels ? levels : 1u;
    t.usage = usage; t.fmt = fmt; t.pool = pool;
    t.source = source;
    t.host_levels = t.levels;
    if (o_.stage >= 3) create_host_cubevol(t);
    cubevol_[addr] = std::move(t);
}

// The host object for a cube or volume, as create_host_texture makes a 2D one: the host
// format (R8G8B8 as X8R8G8B8), a pool the player can lock (MANAGED) unless it is a render
// target, and host_levels levels. A stats read makes none and counts it made.
bool Player::create_host_cubevol(CubeVol& t) {
    t.host_fmt = t.fmt == tex::FMT_R8G8B8 ? tex::FMT_X8R8G8B8 : t.fmt;
    if (!dxvk_format_ok(t.host_fmt)) {
        note_error(strf("guest %s texture 0x%08x has format %u, which DXVK has no Vulkan format for", t.kind(), t.addr,
                        t.fmt));
        return false;
    }
    uint32_t usage = t.usage & (D3DUSAGE_RENDERTARGET | D3DUSAGE_DEPTHSTENCIL | D3DUSAGE_DYNAMIC);
    uint32_t pool = t.pool;
    if (usage & (D3DUSAGE_RENDERTARGET | D3DUSAGE_DEPTHSTENCIL)) {
        pool = D3DPOOL_DEFAULT;
        usage &= ~uint32_t(D3DUSAGE_DYNAMIC);
    } else {
        pool = D3DPOOL_MANAGED;
        usage = 0;
    }
    if (!t.host_levels) t.host_levels = t.levels;
    if (!host()) return t.made = true;           // a stats read: made, as far as the stream shows
    HRESULT hr = t.volume ? dev_->CreateVolumeTexture(t.w, t.h, t.depth, t.host_levels, usage, D3DFORMAT(t.host_fmt),
                                                      D3DPOOL(pool), &t.vol, nullptr)
                          : dev_->CreateCubeTexture(t.w, t.host_levels, usage, D3DFORMAT(t.host_fmt), D3DPOOL(pool),
                                                    &t.cube, nullptr);
    if (FAILED(hr)) {
        t.cube = nullptr;
        t.vol = nullptr;
        note_error(strf("Create%sTexture %ux%ux%u levels %u fmt %s usage 0x%x pool %u for guest 0x%08x: 0x%08x",
                        t.volume ? "Volume" : "Cube", t.w, t.h, t.depth, t.host_levels, tex::format_name(t.fmt), usage,
                        pool, t.addr, unsigned(hr)));
        return false;
    }
    t.made = t.volume ? t.vol != nullptr : t.cube != nullptr;
    tex_by_source_[t.source]++;
    return true;
}

// One level of a cube's face (or of a volume, face 0) up to the host, as upload_level does a
// 2D level's: false without an error where there is nothing to upload (no host object, a
// level the host does not have, a render target, no bytes), with one where it fails. A
// volume level goes up slice by slice at the host's row and slice pitches.
bool Player::upload_cubevol_level(CubeVol& t, uint32_t face, uint32_t level) {
    const auto& chain = t.faces[t.volume ? 0 : face];
    if (!t.made || level >= t.host_levels || level >= chain.size() || chain[level].empty()) return false;
    if (t.usage & (D3DUSAGE_RENDERTARGET | D3DUSAGE_DEPTHSTENCIL)) return false;
    const uint32_t lw = tex::level_dim(t.w, level), lh = tex::level_dim(t.h, level);
    const uint32_t ld = t.volume ? tex::level_dim(t.depth, level) : 1u;
    if (chain[level].size() < size_t(tex::level_bytes(lw, lh, t.fmt)) * ld) {
        note_error(strf("%s texture 0x%08x face %u level %u: %zu bytes, the level needs %zu", t.kind(), t.addr, face,
                        level, chain[level].size(), size_t(tex::level_bytes(lw, lh, t.fmt)) * ld));
        return false;
    }
    const std::vector<uint8_t>* src = &chain[level];
    std::vector<uint8_t> conv;
    if (t.host_fmt != t.fmt) {
        if (!tex::convert_slices(src->data(), lw, lh, ld, t.fmt, t.host_fmt, &conv)) {
            note_error(strf("%s texture 0x%08x level %u: cannot convert %s to %s for DXVK", t.kind(), t.addr, level,
                            tex::format_name(t.fmt), tex::format_name(t.host_fmt)));
            return false;
        }
        src = &conv;
    }
    const uint32_t pitch = tex::level_pitch(lw, t.host_fmt), rows = tex::level_rows(lh, t.host_fmt);
    const size_t slice = size_t(pitch) * rows;
    if (!host()) return false;
    const uint8_t* s = src->data();
    if (t.volume) {
        D3DLOCKED_BOX lb;
        const HRESULT hr = t.vol->LockBox(level, &lb, nullptr, 0);
        if (FAILED(hr)) {
            note_error(strf("volume texture 0x%08x level %u: host LockBox failed 0x%08x", t.addr, level, unsigned(hr)));
            return false;
        }
        if (lb.RowPitch < 0 || uint32_t(lb.RowPitch) < pitch || lb.SlicePitch < 0 ||
            size_t(lb.SlicePitch) < size_t(lb.RowPitch) * (rows - 1) + pitch) {
            t.vol->UnlockBox(level);
            note_error(strf("volume texture 0x%08x level %u: host pitches %d, %d are narrower than %u, %zu", t.addr,
                            level, int(lb.RowPitch), int(lb.SlicePitch), pitch, slice));
            return false;
        }
        for (uint32_t z = 0; z < ld; z++)
            for (uint32_t y = 0; y < rows; y++)
                std::memcpy(static_cast<uint8_t*>(lb.pBits) + size_t(z) * lb.SlicePitch + size_t(y) * lb.RowPitch,
                            s + size_t(z) * slice + size_t(y) * pitch, pitch);
        t.vol->UnlockBox(level);
    } else {
        D3DLOCKED_RECT lr;
        const HRESULT hr = t.cube->LockRect(D3DCUBEMAP_FACES(face), level, &lr, nullptr, 0);
        if (FAILED(hr)) {
            note_error(strf("cube texture 0x%08x face %u level %u: host LockRect failed 0x%08x", t.addr, face, level,
                            unsigned(hr)));
            return false;
        }
        if (lr.Pitch < 0 || uint32_t(lr.Pitch) < pitch) {
            t.cube->UnlockRect(D3DCUBEMAP_FACES(face), level);
            note_error(strf("cube texture 0x%08x face %u level %u: host pitch %d is narrower than %u", t.addr, face,
                            level, int(lr.Pitch), pitch));
            return false;
        }
        for (uint32_t y = 0; y < rows; y++)
            std::memcpy(static_cast<uint8_t*>(lr.pBits) + size_t(y) * lr.Pitch, s + size_t(y) * pitch, pitch);
        t.cube->UnlockRect(D3DCUBEMAP_FACES(face), level);
    }
    bytes_uploaded_ += slice * ld;
    return true;
}

// D3DXCreateCubeTextureFromFileInMemoryEx(pDevice, pSrcData, SrcDataSize, Size, MipLevels,
// Usage, Format, Pool, Filter, MipFilter, ColorKey, pSrcInfo, pPalette, ppCubeTexture) and
// D3DXCreateVolumeTextureFromFileInMemoryEx(pDevice, pSrcData, SrcDataSize, Width, Height,
// Depth, MipLevels, Usage, Format, Pool, Filter, MipFilter, ColorKey, pSrcInfo, pPalette,
// ppVolumeTexture): texture_from_file's rules for a DDS, with the cube's six faces or the
// volume's slices. The recording device takes a cube or a volume from a DDS only, one whose
// dwCaps2 declares it (runtime/sa2_d3d9.c d3dx_from_memory), and so does the player.
Cat Player::cubevol_from_file(const Sa2Record& r, bool volume) {
    // The volume's Height and Depth move every argument after Size two places.
    const int k = volume ? 2 : 0;
    const uint32_t addr = out_addr(r, 13 + k, volume ? "ppVolumeTexture" : "ppCubeTexture");
    if (!addr) return C_HANDLED;
    fresh_object(r, addr);
    Cat cat = C_HANDLED;
    CubeVol t;
    t.volume = volume;
    t.addr = addr;
    t.source = volume ? "volume-file-dds" : "cube-file-dds";
    t.usage = r.arg(5 + k);
    t.pool = r.arg(7 + k);
    const uint32_t want_w = r.arg(3), want_h = r.arg(volume ? 4 : 3), want_d = volume ? r.arg(5) : 1u;
    const uint32_t want_levels = r.arg(4 + k), want_fmt = r.arg(6 + k);
    const uint32_t filter = r.arg(8 + k), mip_filter = r.arg(9 + k), colour_key = r.arg(10 + k);
    auto dflt = [](uint32_t v) { return v == kD3dxDefault || v == 0; };
    auto keep = [&]() { cubevol_[addr] = std::move(t); };
    if (colour_key) cat = unimplemented(r, strf("ColorKey 0x%08x: the player keys no colour out", colour_key));
    if (t.usage & D3DUSAGE_AUTOGENMIPMAP) cat = unimplemented(r, "Usage D3DUSAGE_AUTOGENMIPMAP");
    const Sa2Blob* file = r.blob(SA2S_BLOB_FILE, -1);
    if (!file) { error(r, "no FILE blob"); keep(); return cat; }
    tex::DdsInfo dds;
    if (file->len < 4 || std::memcmp(file->data, "DDS ", 4) != 0) {
        error(r, strf("a %u-byte file blob that is not a DDS; a %s texture comes from a DDS only", file->len,
                      t.kind()));
        keep();
        return cat;
    }
    if (!tex::parse_dds(file->data, file->len, &dds)) { error(r, "DDS: " + dds.error); keep(); return cat; }
    if (!(dds.caps2 & (volume ? tex::kDdsCaps2Volume : tex::kDdsCaps2Cubemap))) {
        error(r, strf("the DDS's dwCaps2 0x%08x does not declare a %s, which the recording device refuses", dds.caps2,
                      volume ? "volume" : "cube map"));
        keep();
        return cat;
    }
    if (!volume && (dds.caps2 & tex::kDdsCaps2AllFaces) != tex::kDdsCaps2AllFaces)
        cat = unimplemented(r, strf("a DDS cube map whose dwCaps2 0x%08x holds fewer than six faces: the player "
                                    "invents no face", dds.caps2));
    if (!volume && dds.w != dds.h)
        cat = unimplemented(r, strf("a DDS cube map of %ux%u: a cube's faces are square", dds.w, dds.h));
    const uint32_t file_d = volume ? dds.depth : 1u;
    // The recording device's rules (runtime/sa2_d3d9.c d3dx_from_memory).
    t.w = dflt(want_w) ? dds.w : want_w;
    t.h = dflt(want_h) ? dds.h : want_h;
    t.depth = volume ? (dflt(want_d) ? file_d : want_d) : 1u;
    t.fmt = dflt(want_fmt) ? dds.fmt : want_fmt;
    uint32_t levels = dflt(want_levels) ? (dds.levels > 1 ? dds.levels : tex::full_chain(t.w, t.h)) : want_levels;
    // The recorder's DESC is the recording device's row for the load, as for a 2D file
    // texture; its kind says which creator's it is. Where it and the file disagree, the
    // row wins.
    if (const Sa2Blob* d = r.blob(SA2S_BLOB_DESC, -1); d && d->len >= sizeof(sa2s_desc_file_texture)) {
        sa2s_desc_file_texture dt;
        std::memcpy(&dt, d->data, sizeof dt);
        desc_checked_++;
        const uint32_t kind = volume ? 2u : 1u;            // SA2_TEXK_VOLUME, SA2_TEXK_CUBE
        if (dt.object != addr || dt.kind != kind || dt.width != t.w || dt.height != t.h || dt.depth != t.depth ||
            dt.levels != levels || dt.format != t.fmt || dt.file_depth != dds.depth || dt.file_levels != dds.levels ||
            dt.file_format != dds.fmt) {
            desc_mismatch_++;
            error(r, strf("the recording device made 0x%08x kind %u %ux%ux%u, %u levels (file %u, depth %u), %s (file "
                          "%s); the player read kind %u %ux%ux%u, %u levels (file %u, depth %u), %s (file %s)",
                          dt.object, dt.kind, dt.width, dt.height, dt.depth, dt.levels, dt.file_levels, dt.file_depth,
                          tex::format_name(dt.format), tex::format_name(dt.file_format), kind, t.w, t.h, t.depth,
                          levels, dds.levels, dds.depth, tex::format_name(t.fmt), tex::format_name(dds.fmt)));
            t.w = dt.width; t.h = dt.height; t.depth = volume ? dt.depth : 1u; levels = dt.levels; t.fmt = dt.format;
        }
    }
    // What the guest is told (GetLevelCount): a volume's count as recorded, a cube's as
    // cube_levels gives it.
    t.levels = volume ? (levels ? levels : 1u) : cube_levels(t.w, levels, t.usage);
    // D3DX_SKIP_DDS_MIP_LEVELS rides in MipFilter's bits 26-30; the player loads every level.
    const bool skips = mip_filter != kD3dxDefault && (mip_filter >> 26) != 0;
    if ((std::min(dds.levels, t.levels) < t.levels || skips) && !mip_filter_is_box(mip_filter))
        cat = unimplemented(r, strf("MipFilter 0x%08x: the player builds mips with D3DX_FILTER_BOX only", mip_filter));
    if (dds.fmt != t.fmt && !lossless_target(t.fmt) && filter_dithers(filter))
        cat = unimplemented(r, strf("Filter 0x%08x converting %s to %s: D3DX dithers that and the player does not",
                                    filter, tex::format_name(dds.fmt), tex::format_name(t.fmt)));
    if (o_.stage < 2) { keep(); return cat == C_UNHANDLED ? cat : C_STAGE; }
    if (t.w != dds.w || t.h != dds.h || t.depth != file_d) {
        error(r, strf("the caller asked for %ux%ux%u from a %ux%ux%u file; D3DX's resampling is not done here", t.w,
                      t.h, t.depth, dds.w, dds.h, file_d));
        keep();
        return cat;
    }
    // Level bytes in the file's format: the faces' chains, or the volume's levels.
    const uint32_t have = std::min(dds.levels, t.levels);
    std::string err;
    if (!(volume ? tex::dds_volume_levels(file->data, file->len, dds, have, &t.faces[0], &err)
                 : tex::dds_cube_levels(file->data, file->len, dds, have, t.faces, &err))) {
        error(r, err);
        keep();
        return cat;
    }
    const int nfaces = volume ? 1 : 6;
    if (dds.fmt != t.fmt) {                      // the caller asked for another format
        for (int f = 0; f < nfaces; f++)
            for (uint32_t lv = 0; lv < have; lv++) {
                std::vector<uint8_t> out;
                const uint32_t lw = tex::level_dim(t.w, lv), lh = tex::level_dim(t.h, lv);
                if (!tex::convert_slices(t.faces[f][lv].data(), lw, lh, tex::level_dim(t.depth, lv), dds.fmt, t.fmt,
                                         &out)) {
                    error(r, strf("cannot convert %s to %s", tex::format_name(dds.fmt), tex::format_name(t.fmt)));
                    keep();
                    return cat;
                }
                t.faces[f][lv].swap(out);
            }
    }
    if (o_.stage >= 3 && have < t.levels) {
        for (int f = 0; f < nfaces; f++) {
            const bool built = volume ? tex::generate_volume_mips(&t.faces[0], t.w, t.h, t.depth, t.fmt, have - 1,
                                                                  t.levels)
                                      : tex::generate_mips(&t.faces[f], t.w, t.h, t.fmt, have - 1, t.levels);
            if (!built) {
                error(r, strf("mip generation failed for %s", tex::format_name(t.fmt)));
                break;
            }
            levels_generated_ += t.levels - have;
        }
    }
    t.host_levels = o_.stage >= 3 ? t.levels : have;
    for (int f = 0; f < nfaces; f++) t.faces[f].resize(t.host_levels);
    if (create_host_cubevol(t))
        for (int f = 0; f < nfaces; f++)
            for (uint32_t lv = 0; lv < t.host_levels; lv++) upload_cubevol_level(t, uint32_t(f), lv);
    keep();
    return cat;
}

// D3DXCreateTextureFromFileInMemoryEx(pDevice, pSrcData, SrcDataSize, Width,
// Height, MipLevels, Usage, Format, Pool, Filter, MipFilter, ColorKey,
// pSrcInfo, pPalette, ppTexture).
Cat Player::texture_from_file(const Sa2Record& r) {
    const uint32_t addr = out_addr(r, 14, "ppTexture");
    if (!addr) return C_HANDLED;
    Cat cat = C_HANDLED;
    const Sa2Blob* file = r.blob(SA2S_BLOB_FILE, -1);
    Tex t;
    t.addr = addr;
    t.source = "file";
    t.pool = r.arg(8);
    t.usage = r.arg(6);
    const uint32_t want_w = r.arg(3), want_h = r.arg(4), want_levels = r.arg(5), want_fmt = r.arg(7);
    const uint32_t filter = r.arg(9), mip_filter = r.arg(10), colour_key = r.arg(11);
    auto dflt = [](uint32_t v) { return v == kD3dxDefault || v == 0; };
    if (colour_key) cat = unimplemented(r, strf("ColorKey 0x%08x: the player keys no colour out", colour_key));
    if (t.usage & D3DUSAGE_AUTOGENMIPMAP) cat = unimplemented(r, "Usage D3DUSAGE_AUTOGENMIPMAP");
    if (!file) { error(r, "no FILE blob"); tex_[addr] = t; return cat; }
    uint32_t file_w = 0, file_h = 0, file_levels = 1, file_fmt = 0;
    tex::DdsInfo dds;
    tex::PngInfo png;
    const bool is_dds = file->len >= 4 && std::memcmp(file->data, "DDS ", 4) == 0;
    const bool is_png = !is_dds && tex::png_header(file->data, file->len, &png);
    if (is_dds) {
        if (!tex::parse_dds(file->data, file->len, &dds)) { error(r, "DDS: " + dds.error); tex_[addr] = t; return cat; }
        file_w = dds.w; file_h = dds.h; file_levels = dds.levels; file_fmt = dds.fmt;
        t.source = "file-dds";
    } else if (is_png) {
        file_w = png.w; file_h = png.h; file_levels = 1; file_fmt = png.fmt;
        t.source = "file-png";
    } else {
        error(r, strf("a %u-byte file blob that is neither DDS nor PNG", file->len));
        tex_[addr] = t;
        return cat;
    }
    // The recording device's rules (runtime/sa2_d3d9.c d3dx_from_memory).
    t.w = dflt(want_w) ? file_w : want_w;
    t.h = dflt(want_h) ? file_h : want_h;
    t.fmt = dflt(want_fmt) ? file_fmt : want_fmt;
    if (dflt(want_levels)) t.levels = file_levels > 1 ? file_levels : tex::full_chain(t.w, t.h);
    else t.levels = want_levels;
    // The recorder's DESC is the recording device's own row for the load, which
    // is what the guest was told (GetLevelDesc, GetLevelCount); the player's
    // reading of the file must agree with it, and where it does not, the row wins.
    if (const Sa2Blob* d = r.blob(SA2S_BLOB_DESC, -1); d && d->len >= sizeof(sa2s_desc_file_texture)) {
        sa2s_desc_file_texture dt;
        std::memcpy(&dt, d->data, sizeof dt);
        desc_checked_++;
        if (dt.object != addr || dt.width != t.w || dt.height != t.h || dt.levels != t.levels ||
            dt.format != t.fmt || dt.file_levels != file_levels || dt.file_format != file_fmt) {
            desc_mismatch_++;
            error(r, strf("the recording device made 0x%08x %ux%u, %u levels (file %u), %s (file %s); the player "
                          "read %ux%u, %u levels (file %u), %s (file %s)", dt.object, dt.width, dt.height,
                          dt.levels, dt.file_levels, tex::format_name(dt.format), tex::format_name(dt.file_format),
                          t.w, t.h, t.levels, file_levels, tex::format_name(t.fmt), tex::format_name(file_fmt)));
            t.w = dt.width; t.h = dt.height; t.levels = dt.levels; t.fmt = dt.format;
        }
    }
    t.locks.resize(t.levels);
    // D3DX_SKIP_DDS_MIP_LEVELS rides in MipFilter's bits 26-30; the player loads every level.
    const bool skips = mip_filter != kD3dxDefault && (mip_filter >> 26) != 0;
    if ((std::min(file_levels, t.levels) < t.levels || skips) && !mip_filter_is_box(mip_filter))
        cat = unimplemented(r, strf("MipFilter 0x%08x: the player builds mips with D3DX_FILTER_BOX only", mip_filter));
    const uint32_t conv_from = is_png ? uint32_t(tex::FMT_A8R8G8B8) : file_fmt;
    if (conv_from != t.fmt && !lossless_target(t.fmt) && filter_dithers(filter))
        cat = unimplemented(r, strf("Filter 0x%08x converting %s to %s: D3DX dithers that and the player does not",
                                    filter, tex::format_name(conv_from), tex::format_name(t.fmt)));
    const int need_stage = is_dds ? 2 : 3;
    if (o_.stage < need_stage) { tex_[addr] = std::move(t); return cat == C_UNHANDLED ? cat : C_STAGE; }
    if (t.w != file_w || t.h != file_h) {
        error(r, strf("the caller asked for %ux%u from a %ux%u file; D3DX's resampling is not done here",
                      t.w, t.h, file_w, file_h));
        tex_[addr] = std::move(t);
        return cat;
    }
    // Level bytes in the texture's format, from the file.
    std::vector<std::vector<uint8_t>> levels;
    if (is_dds) {
        size_t off = dds.data_offset;
        const uint32_t have = std::min(file_levels, t.levels);
        for (uint32_t lv = 0; lv < have; lv++) {
            const uint32_t lw = tex::level_dim(file_w, lv), lh = tex::level_dim(file_h, lv);
            const size_t n = tex::level_bytes(lw, lh, file_fmt);
            if (off + n > file->len) { error(r, strf("DDS level %u runs past the file", lv)); break; }
            levels.emplace_back(file->data + off, file->data + off + n);
            off += n;
        }
    } else {
        std::vector<uint8_t> bgra;
        uint32_t pw = 0, ph = 0;
        std::string err;
        if (!tex::decode_png(file->data, file->len, &bgra, &pw, &ph, &err)) { error(r, err); tex_[addr] = std::move(t); return cat; }
        levels.emplace_back(bgra);          // A8R8G8B8 and X8R8G8B8 are BGRA bytes
        file_fmt = tex::FMT_A8R8G8B8;
    }
    if (levels.empty()) { tex_[addr] = std::move(t); return cat; }
    if (file_fmt != t.fmt) {                 // the caller asked for another format
        for (uint32_t lv = 0; lv < levels.size(); lv++) {
            std::vector<uint8_t> bgra, out;
            const uint32_t lw = tex::level_dim(t.w, lv), lh = tex::level_dim(t.h, lv);
            if (!tex::decode_level(levels[lv].data(), lw, lh, file_fmt, 0, &bgra) ||
                !tex::encode_level(bgra.data(), lw, lh, t.fmt, &out)) {
                error(r, strf("cannot convert %s to %s", tex::format_name(file_fmt), tex::format_name(t.fmt)));
                tex_[addr] = std::move(t);
                return cat;
            }
            levels[lv].swap(out);
        }
    }
    const uint32_t from_file = uint32_t(levels.size());
    t.data = std::move(levels);
    if (o_.stage >= 3 && from_file < t.levels) {
        if (tex::generate_mips(&t.data, t.w, t.h, t.fmt, from_file - 1, t.levels))
            levels_generated_ += t.levels - from_file;
        else
            error(r, strf("mip generation failed for %s", tex::format_name(t.fmt)));
    }
    t.host_levels = o_.stage >= 3 ? t.levels : from_file;
    t.data.resize(t.host_levels);
    if (create_host_texture(t)) {
        for (uint32_t lv = 0; lv < t.host_levels; lv++) upload_level(t, lv);
        if (host()) (is_dds ? file_textures_ : png_textures_)++;
    }
    tex_[addr] = std::move(t);
    return cat;
}

// D3DXFilterTexture(pBaseTexture, pPalette, SrcLevel, MipFilter).
Cat Player::filter_texture(const Sa2Record& r) {
    filter_calls_++;
    Cat cat = C_HANDLED;
    if (!mip_filter_is_box(r.arg(3)))
        cat = unimplemented(r, strf("MipFilter 0x%08x: the player filters with D3DX_FILTER_BOX only", r.arg(3)));
    if (const CubeVol* c = cubevol(r.arg(0)))
        return unimplemented(r, strf("the %s texture 0x%08x: the player filters 2D textures only", c->kind(), c->addr));
    Tex* t = texture(r.arg(0));
    if (!t) { error(r, strf("unknown texture 0x%08x", r.arg(0))); return cat; }
    if (!t->made) return cat;
    const uint32_t src = r.arg(2) == kD3dxDefault ? 0u : r.arg(2);
    if (src >= t->levels) { error(r, strf("SrcLevel %u of a %u-level texture", src, t->levels)); return cat; }
    if (src + 1 >= t->levels) return cat;
    if (t->data.size() < t->levels) t->data.resize(t->levels);
    const uint32_t sw = tex::level_dim(t->w, src), sh = tex::level_dim(t->h, src);
    if (t->data[src].empty()) t->data[src].assign(tex::level_bytes(sw, sh, t->fmt), 0);
    if (!tex::generate_mips(&t->data, t->w, t->h, t->fmt, src, t->levels)) {
        error(r, strf("mip generation failed for %s", tex::format_name(t->fmt)));
        return cat;
    }
    levels_generated_ += t->levels - src - 1;
    for (uint32_t lv = src + 1; lv < t->levels; lv++) upload_level(*t, lv);
    return cat;
}

// D3DXLoadSurfaceFromSurface(pDest, pDestPalette, pDestRect, pSrc, pSrcPalette,
// pSrcRect, Filter, ColorKey): whole-surface copies only, which is what the
// replays make (the critic's section 2, item 5). A source that is the back
// buffer is read back from the GPU (in a stats read, zeros stand in for it).
Cat Player::load_surface(const Sa2Record& r) {
    Cat cat = C_HANDLED;
    if (r.arg(7)) cat = unimplemented(r, strf("ColorKey 0x%08x: the player keys no colour out", r.arg(7)));
    auto di = surf_.find(r.arg(0)), si = surf_.find(r.arg(3));
    if (di == surf_.end() || si == surf_.end()) { error(r, "unknown source or destination surface"); return cat; }
    if (r.arg(2) || r.arg(5)) return unimplemented(r, "source or destination rectangles");
    Surf& d = di->second;
    Surf& s = si->second;
    if (d.kind == Surf::FACE || s.kind == Surf::FACE)
        return unimplemented(r, "a cube face as the source or the destination: the player copies 2D surfaces only");
    Tex* dt = d.kind == Surf::LEVEL ? texture(d.tex) : nullptr;
    if (!dt) { error(r, "destination is not a texture level"); return cat; }
    const uint32_t dw = tex::level_dim(dt->w, d.level), dh = tex::level_dim(dt->h, d.level);
    std::vector<uint8_t> bgra;
    uint32_t sw = 0, sh = 0, sfmt = 0;
    if (s.kind == Surf::BACKBUFFER && !host()) {
        // A stats read has no picture: the level gets zeros in its place, so a
        // later read of the level finds bytes where a replay's would.
        sw = bb_w_; sh = bb_h_; sfmt = bb_fmt_;
        bgra.assign(size_t(sw) * sh * 4, 0);
    } else if (s.kind == Surf::BACKBUFFER) {
        if (FAILED(dev_->GetRenderTargetData(bb_, readback_))) { error(r, "GetRenderTargetData failed"); return cat; }
        D3DLOCKED_RECT lr;
        if (FAILED(readback_->LockRect(&lr, nullptr, D3DLOCK_READONLY))) { error(r, "readback lock failed"); return cat; }
        sw = bb_w_; sh = bb_h_; sfmt = bb_fmt_;
        if (!tex::decode_level(static_cast<const uint8_t*>(lr.pBits), sw, sh, bb_fmt_, uint32_t(lr.Pitch), &bgra)) {
            readback_->UnlockRect();
            error(r, "back buffer format not decodable");
            return cat;
        }
        readback_->UnlockRect();
    } else if (s.kind == Surf::LEVEL) {
        Tex* st = texture(s.tex);
        if (!st || st->data.size() <= s.level || st->data[s.level].empty()) { error(r, "source level holds no bytes here"); return cat; }
        sw = tex::level_dim(st->w, s.level); sh = tex::level_dim(st->h, s.level); sfmt = st->fmt;
        if (!tex::decode_level(st->data[s.level].data(), sw, sh, st->fmt, 0, &bgra)) { error(r, "source format not decodable"); return cat; }
    } else {
        return unimplemented(r, "a source that is neither the back buffer nor a texture level");
    }
    if (sw != dw || sh != dh) return unimplemented(r, strf("scaling %ux%u to %ux%u", sw, sh, dw, dh));
    if (sfmt != dt->fmt && !lossless_target(dt->fmt) && filter_dithers(r.arg(6)))
        cat = unimplemented(r, strf("Filter 0x%08x converting %s to %s: D3DX dithers that and the player does not",
                                    r.arg(6), tex::format_name(sfmt), tex::format_name(dt->fmt)));
    if (dt->data.size() <= d.level) dt->data.resize(d.level + 1);
    if (!tex::encode_level(bgra.data(), dw, dh, dt->fmt, &dt->data[d.level])) { error(r, "destination format not encodable"); return cat; }
    upload_level(*dt, d.level);
    return cat;
}

// ------------------------------------------------------------------- dispatch

// The header's entries table against this player's: an entry number must name
// the function the player replays under it, with the same argc, or one D3DX
// call would be replayed as another. An entry the player does not know may be
// in the table; a record of it is counted not handled (on_d3dx).
void Player::check_entries_table() {
    const Sa2Header& h = rd_.header();
    if (!h.has_entries) return;
    for (const auto& [entry, row] : h.entries) {
        const EntryInfo* e = entry_info(entry);
        if (e && (row.name != e->name || row.argc != e->argc))
            stream_problem(strf("the header's entries table gives entry %u as %s with argc %u; this player replays "
                                "entry %u as %s::%s with argc %u", entry, row.name.c_str(), row.argc, entry, e->dll,
                                e->name, e->argc));
    }
    create9_recorded_ = h.entries.count(SA2S_D3DX_DIRECT3D_CREATE9) > 0;
}

// The stream's own promises, record by record: argc as the header's tables
// say (for a D3DX record in a stream whose header has no entries table, as
// this player's copy of host/sa2_stream.h says), a call or entry the tables
// name, and only the kinds of version 1.
void Player::check_record(const Sa2Record& r) {
    if (r.kind == SA2S_CALL) {
        const int want = rd_.method_argc(r.type, r.slot);
        if (want < 0)
            stream_problem(strf("seq %u: (%u, %u) is not in the header's method table", r.seq, r.type, r.slot));
        else if (size_t(want) != r.args.size())
            stream_problem(strf("%s: argc %zu; the header's table says %d", where(r).c_str(), r.args.size(), want));
    } else if (r.kind == SA2S_D3DX) {
        if (rd_.header().has_entries) {
            const Sa2EntryRow* row = rd_.entry_row(r.entry);
            if (!row)
                stream_problem(strf("seq %u: d3dx entry %u is not in the header's entries table", r.seq, r.entry));
            else if (row->argc != r.args.size())
                stream_problem(strf("%s: argc %zu; the header's entries table says %u", where(r).c_str(),
                                    r.args.size(), row->argc));
        } else if (const EntryInfo* e = entry_info(r.entry); e && e->argc != r.args.size()) {
            stream_problem(strf("%s: argc %zu; host/sa2_stream.h says %u", where(r).c_str(), r.args.size(), e->argc));
        }
    } else if (r.kind == SA2S_AUDIO && rd_.version() == SA2S_VERSION_AUDIO) {
        const Sa2AudioRow* row = rd_.audio_row(r.op);
        if (!row)
            stream_problem(strf("seq %u: audio op %u is not in the header's audio table", r.seq, r.op));
        else if (row->argc != r.args.size())
            stream_problem(strf("seq %u %s: argc %zu; the header's audio table says %u", r.seq, row->name.c_str(),
                                r.args.size(), row->argc));
    } else if (r.kind != SA2S_FRAME && r.kind != SA2S_NOTE && r.kind != SA2S_END) {
        stream_problem(strf("seq %u: record kind %u is not in version %u", r.seq, r.kind, rd_.version()));
    }
    // A version 2 stream's clock never goes back across its timed records, FRAME and
    // AUDIO alike (host/sa2_stream.h).
    if (rd_.version() == SA2S_VERSION_AUDIO && (r.kind == SA2S_FRAME || r.kind == SA2S_AUDIO)) {
        if (any_timed_ && r.virtual_us < last_timed_us_)
            stream_problem(strf("seq %u: virtual time %" PRIu64 " is before the last timed record's %" PRIu64, r.seq,
                                r.virtual_us, last_timed_us_));
        last_timed_us_ = std::max(last_timed_us_, r.virtual_us);
        any_timed_ = true;
    }
}

void Player::on_d3dx(const Sa2Record& r) {
    const EntryInfo* e = entry_info(r.entry);
    // Direct3DCreate9: the guest's IDirect3D9. The player makes its own at
    // CreateDevice, so the call is ignored by design, before the device as the
    // guest makes it; what it answered is kept, and every IDirect3D9 call is
    // held to it (on_call). A second call is the guest's to make: each answer
    // is kept.
    if (r.entry == SA2S_D3DX_DIRECT3D_CREATE9) {
        direct3d_create9_++;
        if (r.eax) d3d9_objects_.insert(r.eax);
        count(r, C_IGNORED, 0);
        return;
    }
    if (!device_made()) { error(r, "a d3dx call before CreateDevice"); count(r, C_UNHANDLED, 0); return; }
    if (!e || e->plan == P_NOT_HANDLED) {
        count(r, unimplemented(r, e ? e->why : "a d3dx entry this player does not know"), 0);
        return;
    }
    if (e->plan == P_NO_EFFECT) { count(r, C_IGNORED, 0); return; }
    // The cube and volume creators: one the recording device refused made nothing, and the
    // player makes nothing for it, by name.
    const bool cubevol_creator =
        r.entry == SA2S_D3DX_CREATE_CUBE_TEXTURE || r.entry == SA2S_D3DX_CREATE_VOLUME_TEXTURE ||
        r.entry == SA2S_D3DX_CUBE_TEXTURE_FROM_MEMORY || r.entry == SA2S_D3DX_VOLUME_TEXTURE_FROM_MEMORY;
    if (cubevol_creator && r.eax != D3D_OK) {
        count(r, unimplemented(r, strf("a creation the recording device refused (0x%08x): nothing to make", r.eax)),
              r.eax);
        return;
    }
    switch (r.entry) {
    case SA2S_D3DX_TEXTURE_FROM_MEMORY:
        count(r, texture_from_file(r), r.eax);
        return;
    case SA2S_D3DX_CREATE_TEXTURE: {
        // (pDevice, Width, Height, MipLevels, Usage, Format, Pool, ppTexture)
        Cat cat = o_.stage >= 3 ? C_HANDLED : C_STAGE;
        if (r.arg(4) & D3DUSAGE_AUTOGENMIPMAP) cat = unimplemented(r, "Usage D3DUSAGE_AUTOGENMIPMAP");
        const uint32_t addr = out_addr(r, 7, "ppTexture");
        if (addr) {
            const uint32_t w = r.arg(1), h = r.arg(2);
            const uint32_t lv = (r.arg(3) == kD3dxDefault || !r.arg(3)) ? tex::full_chain(w, h) : r.arg(3);
            texture_create(addr, w, h, lv, r.arg(4), r.arg(5), r.arg(6), "d3dx-create");
        }
        count(r, cat, r.eax);
        return;
    }
    case SA2S_D3DX_CREATE_CUBE_TEXTURE: {
        // (pDevice, Size, MipLevels, Usage, Format, Pool, ppCubeTexture), a texture of its own as
        // D3DXCreateTexture's is: made at stage 3, filled by locks or nothing
        Cat cat = o_.stage >= 3 ? C_HANDLED : C_STAGE;
        if (r.arg(3) & D3DUSAGE_AUTOGENMIPMAP) cat = unimplemented(r, "Usage D3DUSAGE_AUTOGENMIPMAP");
        const uint32_t addr = out_addr(r, 6, "ppCubeTexture");
        if (addr)
            cubevol_create(r, addr, false, r.arg(1), r.arg(1), 1, cube_levels(r.arg(1), r.arg(2), r.arg(3)),
                           r.arg(3), r.arg(4), r.arg(5), "cube-d3dx-create");
        count(r, cat, r.eax);
        return;
    }
    case SA2S_D3DX_CREATE_VOLUME_TEXTURE: {
        // (pDevice, Width, Height, Depth, MipLevels, Usage, Format, Pool, ppVolumeTexture): the
        // recording device's rule for MipLevels is D3DX's (0 or D3DX_DEFAULT: the chain of
        // Width x Height), and GetLevelCount answers it as recorded
        Cat cat = o_.stage >= 3 ? C_HANDLED : C_STAGE;
        if (r.arg(5) & D3DUSAGE_AUTOGENMIPMAP) cat = unimplemented(r, "Usage D3DUSAGE_AUTOGENMIPMAP");
        const uint32_t addr = out_addr(r, 8, "ppVolumeTexture");
        if (addr) {
            const uint32_t w = r.arg(1), h = r.arg(2);
            const uint32_t lv = (r.arg(4) == kD3dxDefault || !r.arg(4)) ? tex::full_chain(w, h) : r.arg(4);
            cubevol_create(r, addr, true, w, h, r.arg(3), lv, r.arg(5), r.arg(6), r.arg(7), "volume-d3dx-create");
        }
        count(r, cat, r.eax);
        return;
    }
    case SA2S_D3DX_CUBE_TEXTURE_FROM_MEMORY:
        count(r, cubevol_from_file(r, false), r.eax);
        return;
    case SA2S_D3DX_VOLUME_TEXTURE_FROM_MEMORY:
        count(r, cubevol_from_file(r, true), r.eax);
        return;
    case SA2S_D3DX_FILTER_TEXTURE:
        if (o_.stage < 3) { count(r, C_STAGE, 0); return; }
        count(r, filter_texture(r), r.eax);
        return;
    case SA2S_D3DX_LOAD_SURFACE:
        if (o_.stage < 3) { count(r, C_STAGE, 0); return; }
        count(r, load_surface(r), r.eax);
        return;
    default:
        note_error(strf("internal: %s is planned as replayed and has no replay code", where(r).c_str()));
        count(r, C_UNHANDLED, 0);
        return;
    }
}

void Player::on_call(const Sa2Record& r) {
    uint32_t hr = r.eax;
    Cat c;
    // Every IDirect3D9 call is on an object a Direct3DCreate9 record answered,
    // when the header says the writer records that entry.
    if (r.type == T_D3D9 && create9_recorded_ && !d3d9_objects_.count(r.self))
        stream_problem(strf("%s: this 0x%08x is no IDirect3D9 a Direct3DCreate9 record answered", where(r).c_str(),
                            r.self));
    if (r.slot <= 1) {
        c = C_IGNORED;                              // QueryInterface, AddRef
    } else if (r.slot == 2) {
        if (r.type != T_CTAB) releases_++;          // the player keeps every host object
        c = C_IGNORED;
    } else if (r.type == T_D3D9 && r.slot == 16) {
        if (device_made() || !device_failure_.empty()) { error(r, "a second CreateDevice"); c = C_UNHANDLED; }
        else {
            c = C_HANDLED;
            if (!create_device(r, &c) && host()) hr = uint32_t(D3DERR_NOTAVAILABLE);
        }
    } else if (r.type != T_D3D9 && !device_made()) {
        error(r, "a call before CreateDevice");
        c = C_UNHANDLED;
    } else {
        const Plan plan = call_plan(r.type, r.slot);
        if (plan == P_NOT_HANDLED) {
            c = C_UNHANDLED;
        } else if (plan == P_NO_EFFECT) {
            c = C_IGNORED;
        } else if (r.type == T_DEVICE) {
            c = device_call(r, &hr);
        } else if (r.type == T_SWAPCHAIN && r.slot == 3) {
            present_pending_ = true;                // IDirect3DSwapChain9::Present: the implicit
            present_seq_ = r.seq;                   // chain is the only one the device has here
            c = C_HANDLED;
        } else {
            c = resource_call(r, &hr);
        }
    }
    count(r, c, hr);
    checkpoint_after_call(r, c, hr);
}

// A Device call that call_plan plans as replayed or checked. Every check that
// needs no device comes before the host call; a stats read (!host()) makes
// the same checks and leaves out the host call and its answer.
Cat Player::device_call(const Sa2Record& r, uint32_t* hr_out) {
    HRESULT hr = D3D_OK;
    const int st = o_.stage;
    auto dev_cat = [&](int need) { return st >= need ? C_HANDLED : C_STAGE; };
    switch (r.slot) {
    // --- stage 0
    case 17:                                                 // Present
        present_pending_ = true;
        present_seq_ = r.seq;
        return C_HANDLED;                                    // replayed at its FRAME record
    case 41: {                                               // BeginScene
        if (!host()) break;
        const bool open = in_scene_;
        hr = dev_->BeginScene();
        if (SUCCEEDED(hr)) in_scene_ = true;
        else if (hr == D3DERR_INVALIDCALL && open) allow_ = "scene order: BeginScene inside a scene";
        break;
    }
    case 42: {                                               // EndScene
        if (!host()) break;
        const bool open = in_scene_;
        hr = dev_->EndScene();
        if (SUCCEEDED(hr)) in_scene_ = false;
        else if (hr == D3DERR_INVALIDCALL && !open) allow_ = "scene order: EndScene with no scene open";
        break;
    }
    case 43: {                                               // Clear
        const uint32_t n = r.arg(0);
        const D3DRECT* rects = nullptr;
        if (n && !r.arg(1)) return unimplemented(r, strf("Count %u with pRects NULL, which Direct3D 9 refuses", n));
        if (n) {
            const Sa2Blob* b = in_blob(r, 1, size_t(n) * 16, "pRects");
            if (!b) return C_HANDLED;
            rects = reinterpret_cast<const D3DRECT*>(b->data);
        }
        // A Count of 0 with a non-NULL pRects clears nothing in DXVK
        // (d3d9_device.cpp:1902); the recorded pointer's nullness goes through.
        static const D3DRECT kNone = {0, 0, 0, 0};
        if (!n && r.arg(1)) rects = &kNone;
        if (host()) hr = dev_->Clear(n, rects, r.arg(2), r.arg(3), fbits(r.arg(4)), r.arg(5));
        break;
    }
    // --- stage 1: state, shaders, geometry
    case 44: case 49: case 51: case 55: {                    // SetTransform, SetMaterial, SetLight, SetClipPlane
        if (st < 1) return C_STAGE;
        const int arg = r.slot == 49 ? 0 : 1;
        const size_t need = r.slot == 44 ? 64 : r.slot == 49 ? 68 : r.slot == 51 ? 104 : 16;
        const Sa2Blob* b = in_blob(r, arg, need, "state block");
        if (!b) return C_HANDLED;
        if (!host()) break;
        if (r.slot == 44) hr = dev_->SetTransform(D3DTRANSFORMSTATETYPE(r.arg(0)), reinterpret_cast<const D3DMATRIX*>(b->data));
        else if (r.slot == 49) hr = dev_->SetMaterial(reinterpret_cast<const D3DMATERIAL9*>(b->data));
        else if (r.slot == 51) hr = dev_->SetLight(r.arg(0), reinterpret_cast<const D3DLIGHT9*>(b->data));
        else hr = dev_->SetClipPlane(r.arg(0), reinterpret_cast<const float*>(b->data));
        break;
    }
    case 47: {                                               // SetViewport
        if (st < 1) return C_STAGE;
        const Sa2Blob* b = in_blob(r, 0, 24, "pViewport");
        if (!b) return C_HANDLED;
        std::memcpy(viewport_, b->data, 24);
        if (host()) hr = dev_->SetViewport(reinterpret_cast<const D3DVIEWPORT9*>(b->data));
        break;
    }
    case 53: if (st < 1) return C_STAGE; if (host()) hr = dev_->LightEnable(r.arg(0), BOOL(r.arg(1))); break;
    case 57: if (st < 1) return C_STAGE; if (host()) hr = dev_->SetRenderState(D3DRENDERSTATETYPE(r.arg(0)), r.arg(1)); break;
    case 65: {                                               // SetTexture
        if (st < 1) return C_STAGE;
        if (r.arg(0) == 0) cur_tex0_ = r.arg(1);
        // A cube or volume texture is bound as the guest bound it, and no draw is made while
        // one is (cubevol_bound_draw); the host's sampler holds nothing in its place.
        if (r.arg(1) && cubevol(r.arg(1))) {
            cubevol_bound_[r.arg(0)] = r.arg(1);
            if (host()) hr = dev_->SetTexture(r.arg(0), nullptr);
            break;
        }
        cubevol_bound_.erase(r.arg(0));
        if (r.arg(1) && !texture(r.arg(1))) error(r, strf("unknown texture 0x%08x", r.arg(1)));
        if (host()) hr = dev_->SetTexture(r.arg(0), bindable(r.arg(1)));
        break;
    }
    case 67:
        if (st < 1) return C_STAGE;
        if (host()) hr = dev_->SetTextureStageState(r.arg(0), D3DTEXTURESTAGESTATETYPE(r.arg(1)), r.arg(2));
        break;
    case 69:
        if (st < 1) return C_STAGE;
        if (host()) hr = dev_->SetSamplerState(r.arg(0), D3DSAMPLERSTATETYPE(r.arg(1)), r.arg(2));
        break;
    case 75: {                                               // SetScissorRect
        if (st < 1) return C_STAGE;
        const Sa2Blob* b = in_blob(r, 0, 16, "pRect");
        if (!b) return C_HANDLED;
        if (host()) hr = dev_->SetScissorRect(reinterpret_cast<const RECT*>(b->data));
        break;
    }
    case 77: if (st < 1) return C_STAGE; if (host()) hr = dev_->SetSoftwareVertexProcessing(BOOL(r.arg(0))); break;
    case 81:                                                 // DrawPrimitive
        if (st < 1) return C_STAGE;
        if (!cubevol_bound_.empty()) return cubevol_bound_draw(r);
        if (host()) hr = dev_->DrawPrimitive(D3DPRIMITIVETYPE(r.arg(0)), r.arg(1), r.arg(2));
        draws_++, draws_frame_++;
        break;
    case 82:                                                 // DrawIndexedPrimitive
        if (st < 1) return C_STAGE;
        if (!cubevol_bound_.empty()) return cubevol_bound_draw(r);
        if (host())
            hr = dev_->DrawIndexedPrimitive(D3DPRIMITIVETYPE(r.arg(0)), INT(r.arg(1)), r.arg(2), r.arg(3), r.arg(4),
                                            r.arg(5));
        draws_++, draws_frame_++;
        break;
    case 83: {                                               // DrawPrimitiveUP
        if (st < 1) return C_STAGE;
        if (!cubevol_bound_.empty()) return cubevol_bound_draw(r);
        const uint32_t n = prim_vertices(r.arg(0), r.arg(1)), stride = r.arg(3);
        const Sa2Blob* b = in_blob(r, 2, size_t(n) * stride, "pVertexStreamZeroData");
        if (!b || !n) return C_HANDLED;
        if (o_.dump_draws.count(frame_ + 1)) {
            std::printf("draw frame %u seq %u UP type %u prims %u stride %u vs 0x%08x ps 0x%08x decl 0x%08x tex0 0x%08x"
                        " viewport %u,%u %ux%u\n", frame_ + 1, r.seq, r.arg(0), r.arg(1), stride, cur_vs_, cur_ps_,
                        cur_decl_, cur_tex0_, viewport_[0], viewport_[1], viewport_[2], viewport_[3]);
            for (uint32_t i = 0; i < n && i < 8; i++) {
                const float* p = reinterpret_cast<const float*>(b->data + size_t(i) * stride);
                std::printf("   v%u:", i);
                for (uint32_t k = 0; k < stride / 4 && k < 8; k++) std::printf(" %g", p[k]);
                std::printf("\n");
            }
        }
        if (host()) hr = dev_->DrawPrimitiveUP(D3DPRIMITIVETYPE(r.arg(0)), r.arg(1), b->data, stride);
        draws_++, draws_frame_++;
        break;
    }
    case 84: {                                               // DrawIndexedPrimitiveUP
        if (st < 1) return C_STAGE;
        if (!cubevol_bound_.empty()) return cubevol_bound_draw(r);
        const uint32_t idx = prim_vertices(r.arg(0), r.arg(3));
        const uint32_t isz = r.arg(5) == 101 /* D3DFMT_INDEX16 */ ? 2 : 4;
        const Sa2Blob* ib = in_blob(r, 4, size_t(idx) * isz, "pIndexData");
        const Sa2Blob* vb = in_blob(r, 6, size_t(r.arg(1) + r.arg(2)) * r.arg(7), "pVertexStreamZeroData");
        if (!ib || !vb) return C_HANDLED;
        if (host())
            hr = dev_->DrawIndexedPrimitiveUP(D3DPRIMITIVETYPE(r.arg(0)), r.arg(1), r.arg(2), r.arg(3), ib->data,
                                              D3DFORMAT(r.arg(5)), vb->data, r.arg(7));
        draws_++, draws_frame_++;
        break;
    }
    case 86: {                                               // CreateVertexDeclaration
        if (st < 1) return C_STAGE;
        const uint32_t addr = out_addr(r, 1, "ppDecl");
        const Sa2Blob* b = in_blob(r, 0, 8, "pVertexElements");
        if (!addr || !b) return C_HANDLED;
        bool ended = false;
        for (uint32_t off = 0; off + 8 <= b->len; off += 8)
            if (b->data[off] == 0xFF && b->data[off + 1] == 0) { ended = true; break; }
        if (!ended) { error(r, "declaration blob has no D3DDECL_END"); return C_HANDLED; }
        if (!host()) { decl_[addr] = nullptr; break; }      // made, as far as the stream shows
        IDirect3DVertexDeclaration9* d = nullptr;
        hr = dev_->CreateVertexDeclaration(reinterpret_cast<const D3DVERTEXELEMENT9*>(b->data), &d);
        if (SUCCEEDED(hr)) decl_[addr] = d;
        break;
    }
    case 87: {                                               // SetVertexDeclaration
        if (st < 1) return C_STAGE;
        cur_decl_ = r.arg(0);
        auto it = decl_.find(r.arg(0));
        if (r.arg(0) && it == decl_.end()) error(r, strf("unknown declaration 0x%08x", r.arg(0)));
        if (host()) hr = dev_->SetVertexDeclaration(it == decl_.end() ? nullptr : it->second);
        break;
    }
    case 89: if (st < 1) return C_STAGE; if (host()) hr = dev_->SetFVF(r.arg(0)); break;
    case 91: case 106: {                                     // CreateVertexShader, CreatePixelShader
        if (st < 1) return C_STAGE;
        const uint32_t addr = out_addr(r, 1, "ppShader");
        const Sa2Blob* b = in_blob(r, 0, 8, "pFunction");
        if (!addr || !b) return C_HANDLED;
        if (b->len % 4 || rd32(b->data + b->len - 4) != 0x0000FFFFu) {
            error(r, "shader blob does not end with the END token");
            return C_HANDLED;
        }
        if (!host()) {                                       // made, as far as the stream shows
            if (r.slot == 91) vs_[addr] = nullptr;
            else ps_[addr] = nullptr;
            break;
        }
        const DWORD* tok = reinterpret_cast<const DWORD*>(b->data);
        if (r.slot == 91) {
            IDirect3DVertexShader9* s = nullptr;
            hr = dev_->CreateVertexShader(tok, &s);
            if (SUCCEEDED(hr)) vs_[addr] = s;
        } else {
            IDirect3DPixelShader9* s = nullptr;
            hr = dev_->CreatePixelShader(tok, &s);
            if (SUCCEEDED(hr)) ps_[addr] = s;
        }
        break;
    }
    case 92: {
        if (st < 1) return C_STAGE;
        cur_vs_ = r.arg(0);
        auto it = vs_.find(r.arg(0));
        if (r.arg(0) && it == vs_.end()) error(r, strf("unknown vertex shader 0x%08x", r.arg(0)));
        if (host()) hr = dev_->SetVertexShader(it == vs_.end() ? nullptr : it->second);
        break;
    }
    case 107: {
        if (st < 1) return C_STAGE;
        cur_ps_ = r.arg(0);
        auto it = ps_.find(r.arg(0));
        if (r.arg(0) && it == ps_.end()) error(r, strf("unknown pixel shader 0x%08x", r.arg(0)));
        if (host()) hr = dev_->SetPixelShader(it == ps_.end() ? nullptr : it->second);
        break;
    }
    case 94: case 96: case 98: case 109: case 111: case 113: {   // Set*ShaderConstant{F,I,B}
        if (st < 1) return C_STAGE;
        const bool is_b = r.slot == 98 || r.slot == 113;
        const uint32_t n = r.arg(2);
        const Sa2Blob* b = in_blob(r, 1, size_t(n) * (is_b ? 4 : 16), "pConstantData");
        if (!b) return C_HANDLED;
        if (!host()) break;
        switch (r.slot) {
        case 94: hr = dev_->SetVertexShaderConstantF(r.arg(0), reinterpret_cast<const float*>(b->data), n); break;
        case 96: hr = dev_->SetVertexShaderConstantI(r.arg(0), reinterpret_cast<const int*>(b->data), n); break;
        case 98: hr = dev_->SetVertexShaderConstantB(r.arg(0), reinterpret_cast<const BOOL*>(b->data), n); break;
        case 109: hr = dev_->SetPixelShaderConstantF(r.arg(0), reinterpret_cast<const float*>(b->data), n); break;
        case 111: hr = dev_->SetPixelShaderConstantI(r.arg(0), reinterpret_cast<const int*>(b->data), n); break;
        default: hr = dev_->SetPixelShaderConstantB(r.arg(0), reinterpret_cast<const BOOL*>(b->data), n); break;
        }
        break;
    }
    case 100: {                                              // SetStreamSource
        if (st < 1) return C_STAGE;
        IDirect3DVertexBuffer9* vb = nullptr;
        if (r.arg(1)) {
            auto it = buf_.find(r.arg(1));
            if (it == buf_.end() || it->second.type != T_VB || !it->second.made)
                error(r, strf("unknown vertex buffer 0x%08x", r.arg(1)));
            else vb = it->second.vb;
        }
        if (host()) hr = dev_->SetStreamSource(r.arg(0), vb, r.arg(2), r.arg(3));
        break;
    }
    case 102: if (st < 1) return C_STAGE; if (host()) hr = dev_->SetStreamSourceFreq(r.arg(0), r.arg(1)); break;
    case 104: {                                              // SetIndices
        if (st < 1) return C_STAGE;
        IDirect3DIndexBuffer9* ib = nullptr;
        if (r.arg(0)) {
            auto it = buf_.find(r.arg(0));
            if (it == buf_.end() || it->second.type != T_IB || !it->second.made)
                error(r, strf("unknown index buffer 0x%08x", r.arg(0)));
            else ib = it->second.ib;
        }
        if (host()) hr = dev_->SetIndices(ib);
        break;
    }
    case 26: case 27: {                                      // CreateVertexBuffer, CreateIndexBuffer
        if (st < 1) return C_STAGE;
        const uint32_t addr = out_addr(r, 4, "ppBuffer");
        if (!addr) return C_HANDLED;
        Buf b;
        b.type = r.slot == 26 ? T_VB : T_IB;
        b.len = r.arg(0);
        uint32_t usage = r.arg(1), pool = r.arg(3);
        if (pool == D3DPOOL_MANAGED) usage &= ~uint32_t(D3DUSAGE_DYNAMIC);
        if (pool != D3DPOOL_DEFAULT && pool != D3DPOOL_MANAGED) pool = D3DPOOL_MANAGED;
        if (host()) {
            if (r.slot == 26) hr = dev_->CreateVertexBuffer(b.len, usage, r.arg(2), D3DPOOL(pool), &b.vb, nullptr);
            else hr = dev_->CreateIndexBuffer(b.len, usage, D3DFORMAT(r.arg(2)), D3DPOOL(pool), &b.ib, nullptr);
        }
        b.made = host() ? b.vb != nullptr || b.ib != nullptr : true;   // a stats read: as far as the stream shows
        buf_[addr] = b;
        break;
    }
    // --- textures and targets
    case 23: {                                               // CreateTexture
        const uint32_t addr = out_addr(r, 6, "ppTexture");
        if (addr) texture_create(addr, r.arg(0), r.arg(1), r.arg(2) ? r.arg(2) : 1u, r.arg(3), r.arg(4), r.arg(5), "device-create");
        if (r.arg(3) & D3DUSAGE_AUTOGENMIPMAP) return unimplemented(r, "Usage D3DUSAGE_AUTOGENMIPMAP");
        return dev_cat(3);
    }
    case 25: {                                               // CreateCubeTexture(EdgeLength, Levels, Usage, Format,
                                                             //   Pool, ppCubeTexture, pSharedHandle)
        if (r.eax != D3D_OK)
            return unimplemented(r, strf("a CreateCubeTexture the recording device refused (0x%08x): nothing to make",
                                         r.eax));
        const uint32_t addr = out_addr(r, 5, "ppCubeTexture");
        if (addr)
            cubevol_create(r, addr, false, r.arg(0), r.arg(0), 1, cube_levels(r.arg(0), r.arg(1), r.arg(2)), r.arg(2),
                           r.arg(3), r.arg(4), "cube-device-create");
        if (r.arg(2) & D3DUSAGE_AUTOGENMIPMAP) return unimplemented(r, "Usage D3DUSAGE_AUTOGENMIPMAP");
        return dev_cat(3);
    }
    case 28: case 29: case 36: {                             // CreateRenderTarget, CreateDepthStencilSurface, CreateOffscreenPlainSurface
        const int out_arg = r.slot == 36 ? 4 : 6;
        const uint32_t addr = out_addr(r, out_arg, "ppSurface");
        if (!addr) return C_HANDLED;
        Surf s;
        s.kind = Surf::OWN; s.w = r.arg(0); s.h = r.arg(1); s.fmt = r.arg(2);
        if (st >= 3 && host()) {
            if (r.slot == 28)
                hr = dev_->CreateRenderTarget(s.w, s.h, D3DFORMAT(s.fmt), D3DMULTISAMPLE_TYPE(r.arg(3)), r.arg(4),
                                              BOOL(r.arg(5)), &s.host, nullptr);
            else if (r.slot == 29)
                hr = dev_->CreateDepthStencilSurface(s.w, s.h, D3DFORMAT(s.fmt), D3DMULTISAMPLE_TYPE(r.arg(3)),
                                                     r.arg(4), BOOL(r.arg(5)), &s.host, nullptr);
            else
                hr = dev_->CreateOffscreenPlainSurface(s.w, s.h, D3DFORMAT(s.fmt), D3DPOOL_SYSTEMMEM, &s.host, nullptr);
        }
        s.made = host() ? s.host != nullptr : st >= 3;      // a stats read: as far as the stream shows
        surf_[addr] = s;
        if (st < 3) return C_STAGE;
        break;
    }
    case 37: {                                               // SetRenderTarget
        // The recording device's binding, at every stage: it binds any index
        // below 4 and answers success (runtime/sa2_d3d9.c m_SetRenderTarget).
        if (r.eax == D3D_OK && r.arg(0) < 4) bound_rt_[r.arg(0)] = r.arg(1);
        if (st < 3) return C_STAGE;
        bool made = false;
        IDirect3DSurface9* s = surface_for_target(r.arg(1), &made);
        if (r.arg(1) && !made) { error(r, strf("render target 0x%08x has no host surface", r.arg(1))); return C_HANDLED; }
        if (host()) hr = dev_->SetRenderTarget(r.arg(0), s);
        break;
    }
    case 39: {                                               // SetDepthStencilSurface
        if (r.eax == D3D_OK) bound_ds_ = r.arg(0);
        if (st < 3) return C_STAGE;
        bool made = false;
        IDirect3DSurface9* s = surface_for_target(r.arg(0), &made);
        if (r.arg(0) && !made) { error(r, strf("depth surface 0x%08x has no host surface", r.arg(0))); return C_HANDLED; }
        if (host()) hr = dev_->SetDepthStencilSurface(s);
        break;
    }
    case 18: case 38: case 40:                               // GetBackBuffer, GetRenderTarget, GetDepthStencilSurface
        return target_query(r);
    case 16:                                                 // Reset
        return reset_check(r);
    // The calls with no effect on the picture never reach here: call_plan
    // sends them to on_call's count as ignored by design (no_picture_effect).
    default:
        note_error(strf("internal: %s is planned as replayed and has no replay code", where(r).c_str()));
        return C_UNHANDLED;
    }
    if (host()) *hr_out = uint32_t(hr);                      // a stats read keeps the guest's answer
    return C_HANDLED;
}

// Reset makes no host call. The recording device's Reset stores the presentation
// parameters and keeps every object and every state (runtime/sa2_d3d9.c m_Reset), so a
// Reset to the parameters the device was created with changes nothing the player draws:
// once its PRE_PP blob is checked to be CreateDevice's, as recorded, it is ignored by
// design. A Reset to others (another size, format or depth buffer), or one the device
// refused, would need the host device reset to them, which the player does not do: not
// handled, by name.
Cat Player::reset_check(const Sa2Record& r) {
    const Sa2Blob* b = r.blob(SA2S_BLOB_IN, 0);
    if (!b || b->len < 56) { error(r, "no 56-byte IN blob for the presentation parameters"); return C_IGNORED; }
    if (r.eax != D3D_OK) return unimplemented(r, strf("a Reset the device answered 0x%08x", r.eax));
    if (std::memcmp(b->data, creation_pp_, 56) != 0)
        return unimplemented(r, strf("Reset to %ux%u format %u, other presentation parameters than "
                                     "CreateDevice's %ux%u format %u", rd32(b->data), rd32(b->data + 4),
                                     rd32(b->data + 8), creation_pp_[0], creation_pp_[1], creation_pp_[2]));
    return C_IGNORED;
}

// GetBackBuffer, GetRenderTarget and GetDepthStencilSurface make no host call.
// Each answer is a guest surface the guest then uses by address (to put a target
// back, or as a copy's source), and the player finds its host surface by that
// same address (surf_): the mapping is the identity on guest objects. What is
// checked is that the answer is the one the recording device gives from the
// bindings the player has followed (bound_rt_, bound_ds_): the bound surface,
// or a refusal where none is bound (runtime/sa2_d3d9.c m_GetRenderTarget,
// m_GetDepthStencilSurface), and for GetBackBuffer render target 0
// (m_GetBackBuffer). That is Direct3D 9's back buffer only while render target
// 0 is the implicit one: any other GetBackBuffer answer, or one for another
// swap chain or back buffer, is not handled, by name, because Direct3D 9 would
// have answered the back buffer and the calls that use the answer would read or
// draw another surface. An answer the bindings do not give is an error. Once
// checked, the calls have no effect on the picture: ignored by design.
Cat Player::target_query(const Sa2Record& r) {
    target_queries_++;
    uint32_t want = 0;
    int out = 0;
    const char* what = "";
    if (r.slot == 18) {
        back_buffer_queries_++;
        want = bound_rt_[0]; out = 3; what = "render target 0, which the recording device's GetBackBuffer answers";
    } else if (r.slot == 38) {
        want = r.arg(0) < 4 ? bound_rt_[r.arg(0)] : 0; out = 1; what = "the render target at that index";
    } else {
        want = bound_ds_; out = 0; what = "the depth-stencil surface";
    }
    if (r.eax != D3D_OK) {                   // nothing bound: the OUT blob is the guest's variable, unwritten
        if (want) error(r, strf("refused (0x%08x), but the bindings the player followed hold 0x%08x (%s)", r.eax,
                                want, what));
        return C_IGNORED;
    }
    const Sa2Blob* b = r.blob(SA2S_BLOB_OUT, out);
    if (!b || b->len < 4) { error(r, strf("no OUT blob for argument %d", out)); return C_IGNORED; }
    const uint32_t got = rd32(b->data);
    if (!surf_.count(got)) { error(r, strf("answered 0x%08x, a surface the player does not know", got)); return C_IGNORED; }
    if (got != want) {
        error(r, strf("answered 0x%08x; the bindings the player followed hold 0x%08x (%s)", got, want, what));
        return C_IGNORED;
    }
    if (r.slot == 18 && (r.arg(0) || r.arg(1) || r.arg(2)))
        return unimplemented(r, strf("GetBackBuffer(%u, %u, %u): the player maps swap chain 0's back buffer 0 "
                                     "(MONO) only", r.arg(0), r.arg(1), r.arg(2)));
    if (r.slot == 18 && got != rt_addr_)
        return unimplemented(r, strf("GetBackBuffer answered 0x%08x, the bound render target 0, where Direct3D 9 "
                                     "answers the back buffer 0x%08x (runtime/sa2_d3d9.c m_GetBackBuffer)", got,
                                     rt_addr_));
    return C_IGNORED;
}


// A draw while a cube or volume texture is bound at any sampler stage. No game shader
// samples one (the wave-1 survey of obj.pak and the recordings), so the player has no cube
// or volume sampling it can vouch for: the draw is refused by name and nothing is drawn.
Cat Player::cubevol_bound_draw(const Sa2Record& r) {
    const auto& [stage, addr] = *cubevol_bound_.begin();
    const CubeVol* t = cubevol(addr);
    return unimplemented(r, strf("a draw with the %s texture 0x%08x bound at sampler %u: the player draws no cube or "
                                 "volume sampling", t ? t->kind() : "?", addr, stage));
}

// The calls on a cube or volume texture that call_plan replays: its two queries, each held
// to what the recording device answers from the creation the player followed (cube_levels,
// the D3DX rules) and, where the host object has the level, to the host's own answer; and a
// cube's GetCubeMapSurface, whose answer becomes the face surface (Surf::FACE) later calls
// name. An answer the creation does not give is an error. None of them reaches the picture.
Cat Player::cubevol_call(const Sa2Record& r, uint32_t* hr_out) {
    const bool volume = r.type == T_VOLTEX;
    CubeVol* t = cubevol(r.self);
    if (!t || t->volume != volume) {
        error(r, strf("on 0x%08x, which no %s texture creation made", r.self, volume ? "volume" : "cube"));
        return C_HANDLED;
    }
    switch (r.slot) {
    case 13: {                                           // GetLevelCount(): the count is the answer, in eax
        if (r.eax != t->levels)
            error(r, strf("answered %u levels; the %s texture's creation gives %u", r.eax, t->kind(), t->levels));
        if (host() && t->made) {
            const uint32_t n = t->volume ? t->vol->GetLevelCount() : t->cube->GetLevelCount();
            if (n != t->host_levels)
                error(r, strf("the host %s texture has %u levels; the player made it with %u", t->kind(), n,
                              t->host_levels));
        }
        return C_HANDLED;                                // the guest's answer, checked above, is the one counted
    }
    case 17: {                                           // GetLevelDesc(Level, pDesc)
        const uint32_t lv = r.arg(0);
        const bool inside = lv < t->levels && r.arg(1);
        if (r.eax != D3D_OK) {                           // Direct3D 9 refuses a level past the count, or no pDesc
            if (inside)
                error(r, strf("refused (0x%08x) level %u of a %u-level %s texture, which Direct3D 9 describes",
                              r.eax, lv, t->levels, t->kind()));
            return C_IGNORED;                            // checked, and nothing written: no effect
        }
        if (!inside) {
            error(r, strf("answered level %u of a %u-level %s texture%s, which Direct3D 9 refuses", lv, t->levels,
                          t->kind(), r.arg(1) ? "" : " with no pDesc"));
            return C_HANDLED;
        }
        const uint32_t lw = tex::level_dim(t->w, lv), lh = tex::level_dim(t->h, lv), ld = tex::level_dim(t->depth, lv);
        // Direct3D 9's answer, which DXVK gives too: a volume level's D3DVOLUME_DESC, Type
        // D3DRTYPE_VOLUME (2), as the recording device fills it (runtime/sa2_d3d9.c
        // m_VolTexGetLevelDesc); a cube level's D3DSURFACE_DESC, Type D3DRTYPE_SURFACE (1), a
        // face's level being a surface (a device that answers a cube with the 2D texture's
        // m_TexGetLevelDesc, Type D3DRTYPE_TEXTURE (3), gives an answer Direct3D 9 does not).
        // Every other field is the creation's.
        static const char* const kSurfField[] = {"Format", "Type", "Usage", "Pool", "MultiSampleType",
                                                 "MultiSampleQuality", "Width", "Height"};
        static const char* const kVolField[] = {"Format", "Type", "Usage", "Pool", "Width", "Height", "Depth"};
        const uint32_t surf_want[8] = {t->fmt, 1u, t->usage, t->pool, 0u, 0u, lw, lh};
        const uint32_t vol_want[7] = {t->fmt, 2u, t->usage, t->pool, lw, lh, ld};
        const uint32_t n = volume ? 7u : 8u;
        const uint32_t* want = volume ? vol_want : surf_want;
        const Sa2Blob* b = r.blob(SA2S_BLOB_OUT, 1);
        if (!b || b->len < 4 * n) {
            error(r, strf("no %u-byte OUT blob for argument 1 (pDesc)", 4 * n));
            return C_HANDLED;
        }
        for (uint32_t i = 0; i < n; i++)
            if (rd32(b->data + 4 * i) != want[i])
                error(r, strf("answered %s %u for level %u; the %s texture's creation gives %u",
                              (volume ? kVolField : kSurfField)[i], rd32(b->data + 4 * i), lv, t->kind(), want[i]));
        if (host() && t->made && lv < t->host_levels) {
            HRESULT hr;
            uint32_t hw = 0, hh = 0, hd = 1, hf = 0, ht = 0;
            if (volume) {
                D3DVOLUME_DESC d{};
                hr = t->vol->GetLevelDesc(lv, &d);
                hw = d.Width; hh = d.Height; hd = d.Depth; hf = uint32_t(d.Format); ht = uint32_t(d.Type);
            } else {
                D3DSURFACE_DESC d{};
                hr = t->cube->GetLevelDesc(lv, &d);
                hw = d.Width; hh = d.Height; hf = uint32_t(d.Format); ht = uint32_t(d.Type);
            }
            if (SUCCEEDED(hr) && (hw != lw || hh != lh || hd != ld || hf != t->host_fmt || ht != want[1]))
                error(r, strf("the host's level %u is %ux%ux%u %s, Type %u; the player made it %ux%ux%u %s, "
                              "Type %u", lv, hw, hh, hd, tex::format_name(hf), ht, lw, lh, ld,
                              tex::format_name(t->host_fmt), want[1]));
            *hr_out = uint32_t(hr);
        }
        return C_HANDLED;
    }
    case 18: {                                           // GetCubeMapSurface(FaceType, Level, ppCubeMapSurface)
        const uint32_t face = r.arg(0), lv = r.arg(1);
        // A NULL ppCubeMapSurface is refused (D3DERR_INVALIDCALL) by Direct3D 9 and by the
        // recording device alike, so its refusal is the creation's answer too (as GetLevelDesc's).
        const bool inside = face < 6 && lv < t->levels && r.arg(2);
        if (r.eax != D3D_OK) {                           // the guest's variable is the OUT blob, unwritten
            if (inside)
                error(r, strf("refused (0x%08x) face %u level %u of a %u-level cube, which the recording device "
                              "answers", r.eax, face, lv, t->levels));
            return C_IGNORED;                            // checked, and nothing made: no effect (target_query's rule)
        }
        const uint32_t addr = out_addr(r, 2, "ppCubeMapSurface");
        if (!addr) return C_HANDLED;
        if (!inside) {
            error(r, strf("answered 0x%08x for face %u level %u, outside the %u-level cube", addr, face, lv,
                          t->levels));
            return C_HANDLED;
        }
        auto it = surf_.find(addr);
        if (it != surf_.end()) {
            const Surf& s = it->second;
            if (s.kind != Surf::FACE || s.tex != r.self || s.face != face || s.level != lv) {
                error(r, strf("answered 0x%08x, which the player knows as another surface", addr));
                return C_HANDLED;
            }
        } else {
            if (!fresh_object(r, addr)) return C_HANDLED;
            Surf s;
            s.kind = Surf::FACE; s.tex = r.self; s.face = face; s.level = lv;
            s.w = tex::level_dim(t->w, lv); s.h = tex::level_dim(t->h, lv); s.fmt = t->fmt;
            it = surf_.emplace(addr, s).first;
        }
        if (host() && t->cube && lv < t->host_levels) {
            IDirect3DSurface9* hs = nullptr;
            const HRESULT hr = t->cube->GetCubeMapSurface(D3DCUBEMAP_FACES(face), lv, &hs);
            if (SUCCEEDED(hr) && hs) {
                if (!it->second.host) it->second.host = hs;   // kept, as every host object is
                else hs->Release();                           // the same surface again: one reference is kept
            }
            *hr_out = uint32_t(hr);
        }
        return C_HANDLED;
    }
    default:
        note_error(strf("internal: %s is planned as replayed and has no replay code", where(r).c_str()));
        return C_UNHANDLED;
    }
}

Cat Player::resource_call(const Sa2Record& r, uint32_t* hr_out) {
    const int st = o_.stage;
    switch (r.type) {
    case T_CUBETEX: case T_VOLTEX:
        return cubevol_call(r, hr_out);
    case T_TEXTURE:
        switch (r.slot) {
        case 11: {                                           // SetLOD(LODNew)
            Tex* t = texture(r.self);
            if (!t) { error(r, "SetLOD on an unknown texture"); return C_HANDLED; }
            if (st < 2) return C_STAGE;
            // Direct3D 9 applies it to MANAGED textures only; the host copy is
            // MANAGED whatever the guest's pool, so the guest's pool decides.
            if (t->pool == D3DPOOL_MANAGED && t->host) t->host->SetLOD(r.arg(0));
            return C_HANDLED;
        }
        case 18: {                                           // GetSurfaceLevel(Level, ppSurface)
            const uint32_t addr = out_addr(r, 1, "ppSurfaceLevel");
            if (!addr) return C_HANDLED;
            auto it = surf_.find(addr);
            if (it == surf_.end()) {
                Surf s;
                s.kind = Surf::LEVEL; s.tex = r.self; s.level = r.arg(0);
                if (Tex* t = texture(r.self)) {
                    if (s.level >= t->levels) error(r, strf("level %u of a %u-level texture", s.level, t->levels));
                    s.w = tex::level_dim(t->w, s.level); s.h = tex::level_dim(t->h, s.level); s.fmt = t->fmt;
                } else {
                    error(r, strf("GetSurfaceLevel on unknown texture 0x%08x", r.self));
                }
                surf_[addr] = s;
            }
            return C_HANDLED;
        }
        case 19: {                                           // LockRect(Level, pLockedRect, pRect, Flags)
            Tex* t = texture(r.self);
            if (!t) { error(r, "LockRect on an unknown texture"); return C_HANDLED; }
            const uint32_t lv = r.arg(0);
            if (lv >= t->levels) { error(r, strf("LockRect level %u of a %u-level texture", lv, t->levels)); return C_HANDLED; }
            if (lv >= t->locks.size()) t->locks.resize(lv + 1);
            Lock& lk = t->locks[lv];
            lk = Lock{};
            lk.active = true;
            lk.flags = r.arg(3);
            if (const Sa2Blob* o = r.blob(SA2S_BLOB_OUT, 1); o && o->len >= 4) lk.pitch = rd32(o->data);
            if (r.arg(2)) {
                if (const Sa2Blob* rb = in_blob(r, 2, 16, "pRect")) { lk.has_rect = true; std::memcpy(&lk.rect, rb->data, 16); }
            }
            return C_HANDLED;
        }
        case 20: {                                           // UnlockRect(Level)
            Tex* t = texture(r.self);
            if (!t) { error(r, "UnlockRect on an unknown texture"); return C_HANDLED; }
            const uint32_t lv = r.arg(0);
            Lock lk = lv < t->locks.size() ? t->locks[lv] : Lock{};
            if (lv < t->locks.size()) t->locks[lv].active = false;
            sa2s_lock_info L;
            if (lock_of(r, &lk, &L) && (L.level != lv || L.object != r.self))
                error(r, strf("LOCK names 0x%08x level %u; the call is level %u", L.object, L.level, lv));
            if (!lk.active) { error(r, "UnlockRect without a LockRect"); return C_HANDLED; }
            return unlock_upload(r, *t, lv, lk);
        }
        default:
            break;
        }
        break;
    case T_SURFACE:
        switch (r.slot) {
        case 13: {                                           // LockRect(pLockedRect, pRect, Flags)
            auto it = surf_.find(r.self);
            if (it == surf_.end()) { error(r, "LockRect on an unknown surface"); return C_HANDLED; }
            Lock& lk = it->second.lock;
            lk = Lock{};
            lk.active = true;
            lk.flags = r.arg(2);
            if (const Sa2Blob* o = r.blob(SA2S_BLOB_OUT, 0); o && o->len >= 4) lk.pitch = rd32(o->data);
            else error(r, "no OUT blob with the lock pitch");
            if (r.arg(1)) {
                if (const Sa2Blob* rb = in_blob(r, 1, 16, "pRect")) { lk.has_rect = true; std::memcpy(&lk.rect, rb->data, 16); }
            }
            return C_HANDLED;
        }
        case 14: {                                           // UnlockRect()
            auto it = surf_.find(r.self);
            if (it == surf_.end()) { error(r, "UnlockRect on an unknown surface"); return C_HANDLED; }
            Surf& s = it->second;
            Lock lk = s.lock;
            s.lock.active = false;
            sa2s_lock_info L;
            const bool have_lock = lock_of(r, &lk, &L);
            if (have_lock && s.kind == Surf::LEVEL && (L.container != s.tex || L.level != s.level))
                error(r, strf("LOCK names texture 0x%08x level %u; the surface is 0x%08x level %u", L.container,
                              L.level, s.tex, s.level));
            // A face's LOCK names its cube and level when the recorder knows the surface for one
            // (a container of 0 is a surface it did not see made: unsized, so no bytes follow).
            if (have_lock && s.kind == Surf::FACE && L.container && (L.container != s.tex || L.level != s.level))
                error(r, strf("LOCK names texture 0x%08x level %u; the surface is cube 0x%08x face %u level %u",
                              L.container, L.level, s.tex, s.face, s.level));
            if (!lk.active) { error(r, "UnlockRect without a LockRect"); return C_HANDLED; }
            if (s.kind == Surf::FACE) {               // a cube face's upload (store_rows, upload_cubevol_level)
                CubeVol* c = cubevol(s.tex);
                const Sa2Blob* b = r.blob(SA2S_BLOB_LOCKED, -1);
                if (!c) { error(r, "the face's cube texture is unknown"); return C_HANDLED; }
                if (!b) { error(r, "no LOCKED blob"); return C_HANDLED; }
                if (st < 3) return C_STAGE;
                if (lk.flags & D3DLOCK_READONLY) { readonly_locks_++; return C_HANDLED; }
                if (store_rows(r, s.w, s.h, c->fmt, s.level, lk, *b, &c->faces[s.face], s.level) &&
                    upload_cubevol_level(*c, s.face, s.level))
                    lock_uploads_++;
                return C_HANDLED;
            }
            if (s.kind != Surf::LEVEL) {
                if (lk.flags & D3DLOCK_READONLY) { readonly_locks_++; return C_HANDLED; }
                return st < 3 ? C_STAGE : unimplemented(r, "an upload into a surface that is not a texture level");
            }
            Tex* t = texture(s.tex);
            if (!t) { error(r, "the level's texture is unknown"); return C_HANDLED; }
            return unlock_upload(r, *t, s.level, lk);
        }
        default:
            break;
        }
        break;
    case T_VB: case T_IB:
        if (st < 1 && (r.slot == 11 || r.slot == 12)) return C_STAGE;   // no buffers exist at stage 0
        switch (r.slot) {
        case 11: {                                           // Lock(Offset, Size, ppbData, Flags)
            auto it = buf_.find(r.self);
            if (it == buf_.end()) { error(r, "Lock on an unknown buffer"); return C_HANDLED; }
            Buf& b = it->second;
            b.locked = true; b.off = r.arg(0); b.size = r.arg(1); b.flags = r.arg(3);
            return C_HANDLED;
        }
        case 12: {                                           // Unlock()
            auto it = buf_.find(r.self);
            if (it == buf_.end()) { error(r, "Unlock on an unknown buffer"); return C_HANDLED; }
            Buf& b = it->second;
            Lock lk;
            lk.active = b.locked;
            lk.flags = b.flags;
            b.locked = false;
            sa2s_lock_info L;
            if (lock_of(r, &lk, &L)) {                   // the recorder's word for what was locked
                b.off = L.offset;
                b.size = L.bytes;
            }
            if (!lk.active) { error(r, "Unlock without a Lock"); return C_HANDLED; }
            if (lk.flags & D3DLOCK_READONLY) { readonly_locks_++; return C_HANDLED; }
            const Sa2Blob* blob = r.blob(SA2S_BLOB_LOCKED, -1);
            if (!blob) { error(r, "no LOCKED blob"); return C_HANDLED; }
            const uint32_t size = b.size ? b.size : (b.len > b.off ? b.len - b.off : 0);
            if (uint64_t(b.off) + size > b.len) {
                error(r, strf("Unlock of %u bytes at %u in a %u-byte buffer", size, b.off, b.len));
                return C_HANDLED;
            }
            if (blob->len < size) error(r, strf("the LOCKED blob holds %u of the %u bytes locked", blob->len, size));
            const uint32_t n = std::min(size, blob->len);
            if (!host()) return C_HANDLED;                // a stats read uploads nothing
            void* p = nullptr;
            HRESULT hr = b.vb ? b.vb->Lock(b.off, size, &p, 0) : b.ib ? b.ib->Lock(b.off, size, &p, 0) : E_FAIL;
            if (SUCCEEDED(hr) && p) {
                std::memcpy(p, blob->data, n);
                if (b.vb) b.vb->Unlock(); else b.ib->Unlock();
                bytes_uploaded_ += n;
                lock_uploads_++;
            }
            *hr_out = uint32_t(hr);
            return C_HANDLED;
        }
        default:
            break;
        }
        break;
    default:
        break;
    }
    note_error(strf("internal: %s is planned as replayed and has no replay code", where(r).c_str()));
    return C_UNHANDLED;
}

// --------------------------------------------------------------------- frames

void Player::write_y4m(const uint8_t* bgra, uint32_t pitch) {
    const uint32_t w = bb_w_, h = bb_h_, cw = (w + 1) / 2, ch = (h + 1) / 2;
    if (!y4m_header_) {
        const int n = std::fprintf(y4m_, "YUV4MPEG2 W%u H%u F60:1 Ip A1:1 C420jpeg XCOLORRANGE=LIMITED\n", w, h);
        if (n > 0) y4m_bytes_ += uint64_t(n);
        y4m_header_ = true;
        yuv_.resize(size_t(w) * h + 2 * size_t(cw) * ch);
    }
    tex::bgra_to_i420(bgra, pitch, w, h, yuv_.data());
    std::fwrite("FRAME\n", 1, 6, y4m_);
    std::fwrite(yuv_.data(), 1, yuv_.size(), y4m_);
    y4m_bytes_ += 6 + yuv_.size();
    y4m_frames_++;
}

void Player::checkpoint_after_call(const Sa2Record& r, Cat cat, uint32_t hr) {
    if (!o_.stop_draw_frame || r.type != T_DEVICE || r.slot < 81 || r.slot > 84) return;
    // This is a source ordinal, including empty, skipped and refused calls.
    // draws_frame_ counts a narrower set after device_call's early returns.
    ++source_draws_frame_;
    if (uint64_t(frame_) + 1 != o_.stop_draw_frame || source_draws_frame_ != o_.stop_draw_ordinal) return;
    checkpoint_reached_ = true;
    checkpoint_seq_ = r.seq;
    checkpoint_slot_ = r.slot;
    checkpoint_cat_ = cat;
    checkpoint_hr_ = hr;
    checkpoint_guest_hr_ = r.eax;
    if (host()) checkpoint_pixels_ = checkpoint_readback();
    stop_ = true;
    stop_reason_ = "draw checkpoint";
}

bool Player::checkpoint_readback() {
    if (!dev_ || !bb_ || !readback_) {
        readback_failures_++;
        note_error("draw checkpoint: no back buffer/readback surface");
        return false;
    }
    HRESULT hr = dev_->GetRenderTargetData(bb_, readback_);
    checkpoint_readback_hr_ = uint32_t(hr);
    if (FAILED(hr)) {
        readback_failures_++;
        note_error(strf("draw checkpoint: GetRenderTargetData failed (0x%08x)", unsigned(hr)));
        return false;
    }
    D3DLOCKED_RECT lr{};
    hr = readback_->LockRect(&lr, nullptr, D3DLOCK_READONLY);
    checkpoint_readback_hr_ = uint32_t(hr);
    if (FAILED(hr) || !lr.pBits || !bb_w_ || !bb_h_ || lr.Pitch < 0 ||
        uint32_t(lr.Pitch) < tex::level_pitch(bb_w_, bb_fmt_)) {
        if (SUCCEEDED(hr)) readback_->UnlockRect();
        readback_failures_++;
        note_error(strf("draw checkpoint: LockRect failed or invalid pixels (0x%08x)", unsigned(hr)));
        return false;
    }
    const uint8_t* px = static_cast<const uint8_t*>(lr.pBits);
    uint32_t pitch = uint32_t(lr.Pitch);
    if (bb_fmt_ != tex::FMT_A8R8G8B8 && bb_fmt_ != tex::FMT_X8R8G8B8) {
        if (!tex::decode_level(px, bb_w_, bb_h_, bb_fmt_, pitch, &conv_)) {
            readback_->UnlockRect();
            readback_failures_++;
            note_error("draw checkpoint: back buffer format did not decode");
            return false;
        }
        px = conv_.data();
        pitch = bb_w_ * 4;
    }
    const uint64_t hash = frame_hash(px, pitch, bb_w_, bb_h_);
    hr = readback_->UnlockRect();
    checkpoint_readback_hr_ = uint32_t(hr);
    if (FAILED(hr)) {
        readback_failures_++;
        note_error(strf("draw checkpoint: UnlockRect failed (0x%08x)", unsigned(hr)));
        return false;
    }
    checkpoint_hash_ = hash;
    return true;
}

void Player::readback_frame(uint64_t virtual_us, const Sa2Record* fr) {
    const bool snap = o_.snaps.count(frame_) > 0;
    bool probe = false;
    for (const auto& p : o_.pixels) probe |= p.frame == frame_;
    bool expect = false;
    for (const auto& e : expects_) expect |= e.frame == frame_;
    if (o_.frames_out.empty() && !snap && !probe && !expect) return;
    const HRESULT hr = dev_->GetRenderTargetData(bb_, readback_);
    D3DLOCKED_RECT lr;
    if (FAILED(hr) || FAILED(readback_->LockRect(&lr, nullptr, D3DLOCK_READONLY))) {
        readback_failures_++;
        note_error(strf("frame %u: readback failed (0x%08x)", frame_, unsigned(hr)));
        return;
    }
    const uint8_t* px = static_cast<const uint8_t*>(lr.pBits);
    uint32_t pitch = uint32_t(lr.Pitch);
    if (bb_fmt_ != tex::FMT_A8R8G8B8 && bb_fmt_ != tex::FMT_X8R8G8B8) {   // the rest reads as BGRA through the codec
        if (!tex::decode_level(px, bb_w_, bb_h_, bb_fmt_, pitch, &conv_)) {
            readback_->UnlockRect();
            readback_failures_++;
            note_error(strf("frame %u: back buffer format %u did not decode", frame_, bb_fmt_));
            return;
        }
        px = conv_.data();
        pitch = bb_w_ * 4;
    }
    auto at = [&](uint32_t x, uint32_t y) { return px + size_t(y) * pitch + 4 * size_t(x); };
    if (y4m_) {
        write_y4m(px, pitch);
        const uint64_t fh = frame_hash(px, pitch, bb_w_, bb_h_);
        bgrx_digest_ = digest_step(bgrx_digest_, fh);
        std::fprintf(times_, "%u\t%u\t%" PRIu64 "\t%u\t%u\t%" PRIu64 "\t0x%016" PRIx64 "\n", frame_, present_seq_,
                     virtual_us, fr ? fr->frames : 0, fr ? fr->polls : 0, uint64_t((now_s() - t_start_) * 1e6), fh);
        times_rows_++;
    }
    for (const auto& p : o_.pixels)
        if (p.frame == frame_ && p.x < bb_w_ && p.y < bb_h_) {
            const uint8_t* q = at(p.x, p.y);
            std::printf("pixel frame %u (%u,%u) B %u G %u R %u A %u\n", frame_, p.x, p.y, q[0], q[1], q[2], q[3]);
        }
    for (auto& e : expects_) {
        if (e.frame != frame_ || e.x >= bb_w_ || e.y >= bb_h_) continue;
        const uint8_t* q = at(e.x, e.y);
        const uint32_t d = std::max({uint32_t(std::abs(int(q[0]) - int(e.b))), uint32_t(std::abs(int(q[1]) - int(e.g))),
                                     uint32_t(std::abs(int(q[2]) - int(e.r)))});
        e.checked = true;
        e.pass = d <= e.tol;
        (e.pass ? expect_pass_ : expect_fail_)++;
        std::printf("expect frame %u (%u,%u) got BGR %u,%u,%u want %u,%u,%u tol %u: %s  %s\n", frame_, e.x, e.y, q[0],
                    q[1], q[2], e.b, e.g, e.r, e.tol, e.pass ? "PASS" : "FAIL", e.what.c_str());
    }
    if (snap) {
        uint64_t nonblack = 0, sum[3] = {0, 0, 0};
        uint32_t x0 = bb_w_, y0 = bb_h_, x1 = 0, y1 = 0;
        for (uint32_t y = 0; y < bb_h_; y++)
            for (uint32_t x = 0; x < bb_w_; x++) {
                const uint8_t* q = at(x, y);
                sum[0] += q[0]; sum[1] += q[1]; sum[2] += q[2];
                if (q[0] | q[1] | q[2]) {
                    nonblack++;
                    x0 = std::min(x0, x); y0 = std::min(y0, y); x1 = std::max(x1, x); y1 = std::max(y1, y);
                }
            }
        const std::string path = o_.snap_dir + strf("/frame-%05u.png", frame_);
        std::string err;
        const bool ok = tex::write_png_rgb(path.c_str(), px, bb_w_, bb_h_, pitch, &err);
        if (!ok) io_failure("cannot write " + path + ": " + err);
        const double n = double(bb_w_) * bb_h_;
        std::printf("snap frame %u -> %s%s; non-black pixels %" PRIu64 " (%.2f%%), bbox (%u,%u)-(%u,%u), mean BGR %.1f %.1f %.1f,"
                    " draws %" PRIu64 ", virtual_us %" PRIu64 "\n",
                    frame_, path.c_str(), ok ? "" : (" FAILED: " + err).c_str(), nonblack, 100.0 * nonblack / n,
                    nonblack ? x0 : 0, nonblack ? y0 : 0, x1, y1, sum[0] / n, sum[1] / n, sum[2] / n, draws_frame_, virtual_us);
    }
    readback_->UnlockRect();
}

// Every SDL event is discarded and counted by kind; none reaches anything but,
// with --input-path, the window's keys and focus, which reach the game (input.h).
// A close request (the window manager's, or SDL_EVENT_QUIT after the last
// window closed) stops the run, which then fails: it did not finish.
void Player::pump_events() {
    if (!sdl_up_) return;
    const double now = now_s();
    if (win_ && last_pump_ > 0) longest_unpumped_ = std::max(longest_unpumped_, now - last_pump_);
    last_pump_ = now;
    if (g_end_signal && o_.end_fd >= 0 && !end_requested_) request_end(now, false);
    push_due_synthetic_keys();
    SDL_Event e;
    for (size_t batch = 0; batch < 64 && SDL_PollEvent(&e); ++batch) {
        double handled = -1;
        const bool clock_ok = input_ && checked_monotonic_s(&handled);
        const uint64_t ticks = input_ ? SDL_GetTicksNS() : 0;
        const bool key_event = e.type == SDL_EVENT_KEY_DOWN || e.type == SDL_EVENT_KEY_UP;
        // --synthetic-keys: only the player's own queued keys reach the sender.
        const bool ignored = input_ && key_event && !o_.synthetic_keys.empty() &&
                             e.key.which != sa2_input::kSyntheticKeyboard;
        if (ignored) ++real_keys_ignored_;
        const EventTiming timing = key_event ? event_timing(handled, clock_ok, ticks, e.key.timestamp)
                                            : EventTiming{handled, 0, clock_ok ? EventTimingStatus::Ok : EventTimingStatus::MonotonicFailure};
        if (input_ && !clock_ok) {
            ++event_clock_errors_; diagnostic_failure("event CLOCK_MONOTONIC sample failed", !o_.key_log.empty());
        }
        if (input_ && !ignored && key_event && e.key.windowID == input_window_id_ && !e.key.repeat) {
            if (e.key.timestamp > ticks) {
                ++future_key_timestamps_; diagnostic_failure("future SDL key timestamp", !o_.key_log.empty());
            } else if (timing.status == EventTimingStatus::AgeOutOfRange) {
                ++invalid_key_timestamps_; diagnostic_failure("SDL key age exceeds handled clock", !o_.key_log.empty());
            }
        }
        events_discarded_++;
        events_by_kind_[event_kind(e.type)]++;
        input_events_ += sdl_event_is_input(e.type);
        if (((e.type >= 0x200 && e.type < 0x300) || sdl_event_is_input(e.type)) && window_log_.size() < 64)
            window_log_.push_back(strf("%.3f s, after frame %u: %s", now - t_start_, frame_,
                                       sdl_event_name(e.type).c_str()));
        if (e.type == SDL_EVENT_QUIT ||
            (e.type == SDL_EVENT_WINDOW_CLOSE_REQUESTED && win_ && e.window.windowID == SDL_GetWindowID(win_))) {
            close_requests_++;
            if (o_.end_fd >= 0 && !end_requested_) request_end(now, true);
            else if (end_requested_ && now - end_request_s_ < kForceCloseAfterS) ++closes_after_end_;
            else if (!stop_) { stop_ = true; stop_reason_ = "the window was closed"; }
        }
        // Alt+Enter on the desktop: the window's borderless fullscreen (SDL's fullscreen-desktop
        // mode) on or off. The player keeps that Enter, down and up, so the game never sees it.
        // The back buffer stays the game's size; Present fits it to the new swapchain (fit_rect,
        // whole when the aspects agree, as play's full width makes them: runtime/sa2_widescreen.c).
        bool consumed = false;
#ifndef __ANDROID__
        if (key_event && win_ && e.key.windowID == SDL_GetWindowID(win_) && e.key.key == SDLK_RETURN &&
            e.key.which != sa2_input::kSyntheticKeyboard) {
            // Only a fresh Enter press with Alt held is the player's; a repeat with Alt added to an
            // Enter the game already holds stays the game's, so its up still reaches the game.
            if (e.type == SDL_EVENT_KEY_DOWN && !e.key.repeat && (e.key.mod & SDL_KMOD_ALT)) {
                consumed = alt_enter_held_ = true;
                const bool on = (SDL_GetWindowFlags(win_) & SDL_WINDOW_FULLSCREEN) == 0;
                if (SDL_SetWindowFullscreen(win_, on))
                    std::printf("window: borderless fullscreen %s (Alt+Enter)\n", on ? "on" : "off");
                else
                    std::printf("window: Alt+Enter: SDL_SetWindowFullscreen failed: %s\n", SDL_GetError());
            } else if (e.type == SDL_EVENT_KEY_DOWN && e.key.repeat && alt_enter_held_) {
                consumed = true;
            } else if (e.type == SDL_EVENT_KEY_UP && alt_enter_held_) {
                consumed = true;
                alt_enter_held_ = false;
            }
        }
#endif
        if (input_ && !ignored && !consumed) input_->event(e, input_window_id_, timing.handled_s, timing.age_ns);
    }
    if (input_) input_->pump();
    sa2tw::hold_while_unfocused(win_, audio_stream_, o_.pace, [this] { return hold_ending(); },
                                [this] { pump_events(); service_pads(); });
}

// --end-fd: the first close request, or the caller's SIGUSR1. Every key is let up and the
// sender closed, so the game gets no key after it; the caller is told with one byte, and
// ends the game, whose stream this run then reads to its END. Where the byte cannot be
// written there is nobody to end the game, and the close stops the run as it does without
// --end-fd.
void Player::request_end(double now, bool by_close) {
    end_requested_ = true;
    end_by_ = by_close ? "the window's close" : "the caller's request";
    end_request_s_ = now;
    end_request_frame_ = frame_;
    if (input_) input_->close();
#ifndef _WIN32
    std::signal(SIGPIPE, SIG_IGN);                 // a caller gone is an EPIPE answer
#endif                                             // (Windows has no SIGPIPE: the write fails)
    const char byte = 1;
    ssize_t n;
    do n = ::write(end_fd_, &byte, 1); while (n < 0 && errno == EINTR);
    end_written_ = n == 1;
    if (!end_written_) {
        end_error_ = n < 0 ? std::string("write: ") + std::strerror(errno) : "write: short";
        if (!stop_) { stop_ = true; stop_reason_ = "the window was closed"; }
    }
    ::close(end_fd_);
    end_fd_ = -1;
}

std::string Player::end_request_json() const {
    return strf("  \"end_request\": {\"fd\": %s, \"requested\": %s, \"by\": %s, \"after_frame\": %s, "
                "\"at_s\": %s, \"written\": %s, \"closes_after\": %" PRIu64 ", \"error\": %s},\n",
                o_.end_fd >= 0 ? "true" : "false", end_requested_ ? "true" : "false",
                end_by_ ? jstr(end_by_).c_str() : "null",
                end_requested_ ? std::to_string(end_request_frame_).c_str() : "null",
                end_requested_ ? strf("%.3f", end_request_s_ - t_start_).c_str() : "null",
                end_written_ ? "true" : "false", closes_after_end_,
                end_error_.empty() ? "null" : jstr(end_error_).c_str());
}

// The reader's idle hook: it waits on a live stream between the game's frames.
void Player::on_idle() {
    const sa2prof::ZoneScope zone(sa2prof::kZoneIdle);
    ++service_calls_;
    if (!win_) return;
    // A live session's reader calls this for every record and every read, several times
    // a record (tens of thousands a frame in Metal Harbor); an SDL pump each time (167
    // million in a 77 s session) held the player to 0.81 of real time on Metal Harbor's
    // frames. The window is pumped at most every kLivePumpGapS instead, which keeps it
    // and the keyboard answering; and the clock that decides it is read every
    // kLiveClockEvery'th service while the reader reads, and at the first after it waited.
    if (o_.explicit_live()) {
        const uint64_t waits = rd_.idle_waits();
        if (waits == idle_waits_seen_ && ++idle_unclocked_ < kLiveClockEvery) return;
        idle_waits_seen_ = waits;
        idle_unclocked_ = 0;
        const double now = now_s();
        if (now - last_idle_pump_ < kLivePumpGapS) return;
        last_idle_pump_ = now;
    }
    idle_pumps_++;
    pump_events();
    service_pads();
}

// Sleep to `target` (monotonic seconds) in slices, pumping events between, so
// a long gap in virtual time never leaves the window unresponsive.
void Player::pace_until(double target) {
    for (;;) {
        pump_events();
        service_pads();
        if (stop_) return;
        const double wait = target - now_s();
        if (wait <= 0) return;
        const double slice = std::min(wait, 0.010);
        timespec ts{0, long(slice * 1e9)};
        nanosleep(&ts, nullptr);
    }
}

// A Present and its FRAME record: the stream's checks, in both modes; then,
// in a replay, the readback, the pacing and the host Present; then, in both,
// the report every --report-every Presents.
void Player::do_present(const Sa2Record* fr) {
    const sa2prof::ZoneScope zone(sa2prof::kZonePresent);
    present_pending_ = false;
    if (!o_.present_log.empty() && frame_ == UINT32_MAX) {
        ++present_clock_errors_; diagnostic_failure("diagnostic frame count would wrap");
        stop_ = true; stop_reason_ = "diagnostic frame count overflow"; return;
    }
    frame_++;
    sa2prof::frame(frame_);
    const uint64_t vus = fr ? fr->virtual_us : last_vus_ + 16666u;
    if (!fr) {
        missing_frame_records_++;
        stream_problem(strf("the Present at seq %u has no FRAME record; frame %u is given virtual time %" PRIu64,
                            present_seq_, frame_, vus));
    } else if (any_frame_time_ && vus < last_vus_) {
        stream_problem(strf("FRAME seq %u: virtual time %" PRIu64 " is before the last frame's %" PRIu64, fr->seq, vus,
                            last_vus_));
    }
    any_frame_time_ = true;
    last_vus_ = vus;
    if (mixer_) {
        const sa2prof::ZoneScope audio_zone(sa2prof::kZoneAudio);
        if (!any_frame_us_) { any_frame_us_ = true; first_frame_us_ = vus; }
        mixer_->on_frame(vus);
        audio_block();
    }
    if (host()) {
        readback_frame(vus, fr);
        Sa2Pace::Slot slot{};
        double ready = 0;
        if (pace_) {
            ready = now_s();
            slot = pace_->schedule(vus, ready);
            pace_until(slot.target);
        }
        pump_events();
        if (win_) {                            // the window's state as this frame is presented
            const SDL_WindowFlags fl = SDL_GetWindowFlags(win_);
            frames_occluded_ += (fl & SDL_WINDOW_OCCLUDED) != 0;
            frames_hidden_ += (fl & SDL_WINDOW_HIDDEN) != 0;
            frames_minimized_ += (fl & SDL_WINDOW_MINIMIZED) != 0;
            frames_input_focus_ += (fl & SDL_WINDOW_INPUT_FOCUS) != 0;
            frames_mouse_focus_ += (fl & SDL_WINDOW_MOUSE_FOCUS) != 0;
            if (!flag_runs_.empty() && flag_runs_.back().flags == uint64_t(fl) && flag_runs_.back().last + 1 == frame_)
                flag_runs_.back().last = frame_;
            else
                flag_runs_.push_back({frame_, frame_, uint64_t(fl)});
            place_picture();
        }
        double call_s = -1;
        const bool call_ok = o_.present_log.empty() || checked_monotonic_s(&call_s);
        const double at = o_.present_log.empty() || !call_ok ? now_s() : call_s;
        const HRESULT hr = dev_->Present(nullptr, nullptr, nullptr, nullptr);
        double returned_s = -1;
        const bool returned_ok = o_.present_log.empty() || checked_monotonic_s(&returned_s);
        if (!o_.synthetic_keys.empty() && frame_ == 1 && SUCCEEDED(hr) && call_ok && returned_ok) {
            const uint64_t anchor = SDL_GetTicksNS();
            if (!anchor || (!synthetic_keys_.empty() && synthetic_keys_.back().offset_ns > UINT64_MAX - anchor)) {
                synthetic_schedule_failed_ = true; diagnostic_failure("invalid synthetic schedule anchor");
            } else {
                synthetic_armed_ticks_ = anchor; synthetic_armed_return_s_ = returned_s; synthetic_armed_ = true;
            }
        }
        if (!o_.present_log.empty()) write_present_row(frame_, vus, call_ok ? call_s : -1, returned_ok ? returned_s : -1);
        if (pace_) {
            pace_->presented(vus, slot, at);
            if (pace_log_) {
                const int queued = audio_stream_ ? SDL_GetAudioStreamQueued(audio_stream_) : -1;
                std::fprintf(pace_log_, "%u\t%" PRIu64 "\t%.6f\t%.6f\t%.6f\t%.3f\t%.3f\n", frame_, vus, ready,
                             slot.due, at, slot.moved * 1e3,
                             queued < 0 ? -1.0 : double(queued) * 1000.0 / (double(Sa2Mixer::kRate) * kAudioFrameBytes));
                if (frame_ % 100 == 0) std::fflush(pace_log_);
            }
        }
        MStat& m = mstat_[uint32_t(T_DEVICE) << 16 | 17];
        if (FAILED(hr)) {
            if (!m.failed) { m.first_fail_hr = uint32_t(hr); m.first_fail_seq = present_seq_; }
            m.failed++;
            present_failures_++;
            note_error(strf("frame %u (seq %u): host Present failed 0x%08x", frame_, present_seq_, unsigned(hr)));
        }
        if (frame_ % 100 == 0)
            std::fprintf(stderr, "frame %u (virtual %.3f s, wall %.1f s)\n", frame_, double(vus) / 1e6, now_s() - t_start_);
    }
    draws_frame_ = 0;
    source_draws_frame_ = 0;
    // The counts so far. A player that dies before the next rewrite leaves a
    // report counting these Presents, and it made at most --report-every more.
    if (o_.report_every && frame_ % o_.report_every == 0 && !write_report(false)) return;
    if (o_.max_frames && frame_ >= o_.max_frames && !stop_) { stop_ = true; stop_reason_ = "--max-frames"; }
}

// ---------------------------------------------------------------------- gamepads and touch

namespace {

// SDL's gamepad buttons as XInput's XINPUT_GAMEPAD_* bits (input.h PadState), by position: SDL's
// SOUTH is XInput's A whatever the pad prints on it. GUIDE and the rest have no XInput bit. The
// pads' map until SA2_PAD_MAP changes it.
struct PadButtonBit { SDL_GamepadButton button; uint16_t bit; };
constexpr PadButtonBit kPadButtonBits[] = {
    {SDL_GAMEPAD_BUTTON_SOUTH, 0x1000}, {SDL_GAMEPAD_BUTTON_EAST, 0x2000},
    {SDL_GAMEPAD_BUTTON_WEST, 0x4000}, {SDL_GAMEPAD_BUTTON_NORTH, 0x8000},
    {SDL_GAMEPAD_BUTTON_BACK, 0x0020}, {SDL_GAMEPAD_BUTTON_START, 0x0010},
    {SDL_GAMEPAD_BUTTON_LEFT_STICK, 0x0040}, {SDL_GAMEPAD_BUTTON_RIGHT_STICK, 0x0080},
    {SDL_GAMEPAD_BUTTON_LEFT_SHOULDER, 0x0100}, {SDL_GAMEPAD_BUTTON_RIGHT_SHOULDER, 0x0200},
    {SDL_GAMEPAD_BUTTON_DPAD_UP, 0x0001}, {SDL_GAMEPAD_BUTTON_DPAD_DOWN, 0x0002},
    {SDL_GAMEPAD_BUTTON_DPAD_LEFT, 0x0004}, {SDL_GAMEPAD_BUTTON_DPAD_RIGHT, 0x0008},
};

static_assert(SDL_GAMEPAD_BUTTON_COUNT <= 32, "PadSlot::held");

// SDL's buttons by the names SA2_PAD_MAP gives them: by position, as SDL reports them.
struct PadButtonName { SDL_GamepadButton button; const char* name; };
constexpr PadButtonName kPadButtonNames[] = {
    {SDL_GAMEPAD_BUTTON_SOUTH, "SOUTH"}, {SDL_GAMEPAD_BUTTON_EAST, "EAST"}, {SDL_GAMEPAD_BUTTON_WEST, "WEST"},
    {SDL_GAMEPAD_BUTTON_NORTH, "NORTH"}, {SDL_GAMEPAD_BUTTON_BACK, "BACK"}, {SDL_GAMEPAD_BUTTON_GUIDE, "GUIDE"},
    {SDL_GAMEPAD_BUTTON_START, "START"}, {SDL_GAMEPAD_BUTTON_LEFT_STICK, "LS"},
    {SDL_GAMEPAD_BUTTON_RIGHT_STICK, "RS"}, {SDL_GAMEPAD_BUTTON_LEFT_SHOULDER, "LB"},
    {SDL_GAMEPAD_BUTTON_RIGHT_SHOULDER, "RB"}, {SDL_GAMEPAD_BUTTON_DPAD_UP, "UP"},
    {SDL_GAMEPAD_BUTTON_DPAD_DOWN, "DOWN"}, {SDL_GAMEPAD_BUTTON_DPAD_LEFT, "LEFT"},
    {SDL_GAMEPAD_BUTTON_DPAD_RIGHT, "RIGHT"}, {SDL_GAMEPAD_BUTTON_MISC1, "MISC1"},
    {SDL_GAMEPAD_BUTTON_RIGHT_PADDLE1, "PADDLE1"}, {SDL_GAMEPAD_BUTTON_LEFT_PADDLE1, "PADDLE2"},
    {SDL_GAMEPAD_BUTTON_RIGHT_PADDLE2, "PADDLE3"}, {SDL_GAMEPAD_BUTTON_LEFT_PADDLE2, "PADDLE4"},
    {SDL_GAMEPAD_BUTTON_TOUCHPAD, "TOUCHPAD"},
};

// SA2_PAD_MAP's "BUTTON=OUTPUT,...": each SDL button named (kPadButtonNames) sends that game button
// (input.h pad_output_named) in place of its own; `map` is unchanged unless the whole value reads.
bool parse_pad_map(const std::string& value, sa2_input::PadOutput* map, std::string* why) {
    sa2_input::PadOutput next[SDL_GAMEPAD_BUTTON_COUNT];
    std::copy(map, map + SDL_GAMEPAD_BUTTON_COUNT, next);
    size_t at = 0;
    while (at <= value.size()) {
        size_t end = value.find(',', at);
        if (end == std::string::npos) end = value.size();
        const std::string item = value.substr(at, end - at);
        const size_t eq = item.find('=');
        const std::string key = item.substr(0, eq), out = eq == std::string::npos ? "" : item.substr(eq + 1);
        const PadButtonName* b = nullptr;
        for (const PadButtonName& n : kPadButtonNames)
            if (key == n.name) b = &n;
        if (!b || !sa2_input::pad_output_named(out, &next[b->button])) {
            *why = "\"" + item + "\" is not BUTTON=OUTPUT with BUTTON one of SOUTH EAST WEST NORTH BACK GUIDE START "
                   "LS RS LB RB UP DOWN LEFT RIGHT MISC1 PADDLE1-4 TOUCHPAD and OUTPUT a game button";
            return false;
        }
        at = end + 1;
    }
    std::copy(next, next + SDL_GAMEPAD_BUTTON_COUNT, map);
    return true;
}

// An SDL axis value into an XInput slot: a trigger's 0..32767 as 0..255; a stick's value as it
// is, but Y turned over (SDL's Y is down positive, XInput's up positive).
void set_pad_axis(sa2_input::PadState* s, int axis, Sint16 v) {
    const auto trigger = [](Sint16 t) { return uint8_t(t <= 0 ? 0 : (int(t) * 255 + 16383) / 32767); };
    const auto up = [](Sint16 y) { return int16_t(y == INT16_MIN ? INT16_MAX : -y); };
    switch (axis) {
    case SDL_GAMEPAD_AXIS_LEFTX: s->lx = v; break;
    case SDL_GAMEPAD_AXIS_LEFTY: s->ly = up(v); break;
    case SDL_GAMEPAD_AXIS_RIGHTX: s->rx = v; break;
    case SDL_GAMEPAD_AXIS_RIGHTY: s->ry = up(v); break;
    case SDL_GAMEPAD_AXIS_LEFT_TRIGGER: s->lt = trigger(v); break;
    case SDL_GAMEPAD_AXIS_RIGHT_TRIGGER: s->rt = trigger(v); break;
    default: break;
    }
}

// Slot 0's physical pad and the touch overlay as one pad: buttons and triggers combined, each
// stick the one pushed further.
sa2_input::PadState shared_slot(const sa2_input::PadState& a, const sa2_input::PadState& b) {
    if (!a.connected) return b;
    if (!b.connected) return a;
    sa2_input::PadState s = a;
    s.buttons = uint16_t(a.buttons | b.buttons);
    s.lt = std::max(a.lt, b.lt);
    s.rt = std::max(a.rt, b.rt);
    const auto reach = [](int x, int y) { return int64_t(x) * x + int64_t(y) * y; };
    if (reach(b.lx, b.ly) > reach(a.lx, a.ly)) { s.lx = b.lx; s.ly = b.ly; }
    if (reach(b.rx, b.ry) > reach(a.rx, a.ry)) { s.rx = b.rx; s.ry = b.ry; }
    return s;
}

bool pad_or_finger_event(Uint32 type) {
    switch (type) {
    case SDL_EVENT_GAMEPAD_ADDED: case SDL_EVENT_GAMEPAD_REMOVED: case SDL_EVENT_GAMEPAD_REMAPPED:
    case SDL_EVENT_GAMEPAD_BUTTON_DOWN: case SDL_EVENT_GAMEPAD_BUTTON_UP: case SDL_EVENT_GAMEPAD_AXIS_MOTION:
    case SDL_EVENT_FINGER_DOWN: case SDL_EVENT_FINGER_UP: case SDL_EVENT_FINGER_MOTION: case SDL_EVENT_FINGER_CANCELED:
        return true;
    default:
        return false;
    }
}

// The function the second host's device calls at Present, and its argument: window properties
// (host/d3d9gpu/d9g_gpu_sdl.cpp reads them by these names).
constexpr const char* kPresentOverlayProperty = "sa2.present_overlay";
constexpr const char* kPresentOverlayContext = "sa2.present_overlay.context";
constexpr SDL_TouchID kSyntheticTouch = 0x53413254;   // --synthetic-input's fingers
constexpr size_t kPadQueueCap = 4096;                  // events held between two services
constexpr uint64_t kOverlayShots = 12;                 // --overlay-out's Presents

bool parse_control(const std::string& name, sa2_touch::Control* c) {
    static const std::pair<const char*, sa2_touch::Control> names[] = {
        {"STICK", sa2_touch::Control::Stick}, {"A", sa2_touch::Control::A}, {"B", sa2_touch::Control::B},
        {"X", sa2_touch::Control::X}, {"Y", sa2_touch::Control::Y}, {"START", sa2_touch::Control::Start},
        {"LT", sa2_touch::Control::LT}, {"RT", sa2_touch::Control::RT}};
    for (const auto& [n, v] : names)
        if (name == n) { *c = v; return true; }
    return false;
}

bool parse_float(const std::string& text, float* out) {
    char* end = nullptr;
    errno = 0;
    const float v = std::strtof(text.c_str(), &end);
    if (text.empty() || errno || !end || *end || !std::isfinite(v)) return false;
    *out = v;
    return true;
}

}  // namespace

// Called with every event SDL raises, on the thread it raises it on: gamepad and finger events
// are copied for service_pads. A queue full (no service for a long while) drops the event and
// has the next service read every pad's state afresh.
bool SDLCALL Player::pad_watch(void* self, SDL_Event* e) {
    if (!pad_or_finger_event(e->type)) return true;
    auto* p = static_cast<Player*>(self);
    SDL_LockMutex(p->pad_queue_mutex_);
    if (p->pad_queue_.size() < kPadQueueCap) p->pad_queue_.push_back(*e);
    else p->pad_queue_overflow_.store(true, std::memory_order_relaxed);
    p->pad_queue_ready_.store(true, std::memory_order_release);
    SDL_UnlockMutex(p->pad_queue_mutex_);
    return true;
}

// The picture's place ("The picture's place" above), before each Present: the anchor and the safe
// area's top, in pixels, to the device's window properties and to the overlay.
void Player::place_picture() {
    if (!picture_read_) {
        picture_read_ = true;
        if (const char* v = getenv("SA2_PLAYER_PICTURE_Y"); v && *v) {
            char* end = nullptr;
            const float f = std::strtof(v, &end);
            if (end && !*end && std::isfinite(f) && f >= 0 && f <= 1) picture_y_ = f;
            else std::printf("picture: SA2_PLAYER_PICTURE_Y=%s not used: not a number from 0 to 1\n", v);
        }
    }
    if (picture_y_ < 0) return;
    // Only an upright window moves the picture: a landscape one letterboxing it (auto-rotate held
    // sideways on a screen narrower than 4:3) keeps it centred.
    int ww = 0, wh = 0, pw = 0, ph = 0;
    SDL_GetWindowSizeInPixels(win_, &pw, &ph);
    picture_anchor_ = ph > pw ? picture_y_ : -1;
    SDL_Rect safe{};
    uint32_t min_top = 0;
    if (picture_anchor_ >= 0 && SDL_GetWindowSize(win_, &ww, &wh) && wh > 0 && SDL_GetWindowSafeArea(win_, &safe) &&
        safe.y > 0)
        min_top = uint32_t(int64_t(safe.y) * ph / wh);
    const SDL_PropertiesID props = SDL_GetWindowProperties(win_);
    SDL_SetFloatProperty(props, "sa2.picture_y", picture_anchor_);
    SDL_SetNumberProperty(props, "sa2.picture_min_top", min_top);
    sa2_touch::set_picture(touch_, picture_anchor_, min_top);
}

// The pads and the overlay, at the first service once the key sender has started.
void Player::setup_pads() {
    pads_up_ = true;
    for (const PadButtonBit& b : kPadButtonBits) pad_map_[b.button].bit = b.bit;
    const bool synthetic = !o_.synthetic_input.empty();
    if (synthetic && !load_synthetic_input()) return;
    if (!o_.overlay_out.empty())
        if (const std::string why = refuse_output(o_.overlay_out + "/frame.png"); !why.empty()) {
            usage_failure("--overlay-out: " + why);
            stop_ = true;
            stop_reason_ = "refused before the stream was read";
            return;
        }
#ifdef __ANDROID__
    touch_platform_ = true;                         // the phone's screen is its pad
#endif
    if (const char* t = getenv("SA2_TOUCH_CONTROLS"); t && *t) touch_platform_ = std::strcmp(t, "0") != 0;
    if (synthetic) SDL_SetHint(SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS, "1");
    touch_ = sa2_touch::create(nullptr, win_);
    // The overlay's settings and the pads' map ("Gamepads" above), each said in the log.
    sa2_touch::Settings touch_settings;
    for (const char* k : {"SA2_TOUCH_SCALE", "SA2_TOUCH_OPACITY", "SA2_TOUCH_LAYOUT", "SA2_TOUCH_LAYOUT_PORTRAIT",
                          "SA2_TOUCH_MAP", "SA2_PAD_MAP"}) {
        const char* v = getenv(k);
        if (!v || !*v) continue;
        std::string why;
        const bool ok = std::strcmp(k, "SA2_PAD_MAP") == 0 ? parse_pad_map(v, pad_map_, &why)
                                                            : sa2_touch::parse_setting(k, v, &touch_settings, &why);
        if (ok) std::printf("pads: %s=%s\n", k, v);
        else std::printf("pads: %s=%s not used: %s\n", k, v, why.c_str());
    }
    if (const char* v = getenv("SA2_PAD_P1"); v && *v) {
        if (std::strcmp(v, "ALL") == 0) pad_p1_all_ = true;
        else if (std::strncmp(v, "NAME:", 5) == 0 && v[5]) pad_p1_name_ = v + 5;
        else if (unsigned vid = 0, pid = 0; std::sscanf(v, "ID:%x:%x", &vid, &pid) == 2 && vid <= 0xffff && pid <= 0xffff) {
            pad_p1_vendor_ = int(vid);
            pad_p1_product_ = int(pid);
        }
        if (pad_p1_all_ || !pad_p1_name_.empty() || pad_p1_vendor_ >= 0) std::printf("pads: SA2_PAD_P1=%s\n", v);
        else std::printf("pads: SA2_PAD_P1=%s not used: it is ALL, ID:vvvv:pppp or NAME:<device name>\n", v);
    }
    sa2_touch::set_settings(touch_, touch_settings);
    sa2_frametime::setup(SA2_PLAYER_GPU != 0, o_.pace && host());   // host tweak frametime-overlay
    const SDL_PropertiesID props = SDL_GetWindowProperties(win_);
    if (!props || !SDL_SetPointerProperty(props, kPresentOverlayContext, this) ||
        !SDL_SetPointerProperty(props, kPresentOverlayProperty, reinterpret_cast<void*>(&Player::present_overlay)))
        pads_error_ = strf("the window's overlay property: %s", SDL_GetError());
    pad_queue_mutex_ = SDL_CreateMutex();
    if (!pad_queue_mutex_ || !SDL_AddEventWatch(&Player::pad_watch, this)) {
        if (pads_error_.empty()) pads_error_ = strf("the pads' event watch: %s", SDL_GetError());
    } else if (!SDL_InitSubSystem(SDL_INIT_GAMEPAD)) {
        if (pads_error_.empty()) pads_error_ = strf("SDL_InitSubSystem(SDL_INIT_GAMEPAD): %s", SDL_GetError());
    } else {
        gamepads_up_ = true;
        int n = 0;
        if (SDL_JoystickID* ids = SDL_GetGamepads(&n)) {
            for (int i = 0; i < n; ++i) open_pad(ids[i]);
            SDL_free(ids);
        }
    }
    send_pads();                                    // the game's boot scan waits for this
}

// The live reader's service, after each event pump: the pads set up once the key sender has
// started, the events pad_watch copied handled in order, and the four slots sent when they
// changed (input.h writes a record only for a change).
void Player::service_pads() {
    if (!pads_up_) {
        if (!input_ || !sender_started_ || !win_) return;
        setup_pads();
    }
    if (pads_closed_ || !pad_queue_mutex_) return;
    if (shot_.pending) write_overlay_shot();
    push_due_synthetic_input();
    if (pad_queue_ready_.load(std::memory_order_acquire)) {
        SDL_LockMutex(pad_queue_mutex_);
        pad_batch_.swap(pad_queue_);
        pad_queue_ready_.store(false, std::memory_order_relaxed);
        SDL_UnlockMutex(pad_queue_mutex_);
        for (const SDL_Event& e : pad_batch_) pad_event(e);
        pad_batch_.clear();
    }
    if (pad_queue_overflow_.exchange(false, std::memory_order_relaxed)) {
        ++pad_queue_overflows_;
        for (PadSlot& s : pad_slots_)
            if (s.pad) read_pad(&s);
        pads_dirty_ = true;
    }
    if (sa2tw::hold_toggle::owed(win_ && (SDL_GetWindowFlags(win_) & SDL_WINDOW_INPUT_FOCUS))) pads_dirty_ = true;
    if (pads_dirty_) send_pads();
}

Player::PadSlot* Player::slot_of(SDL_JoystickID id) {
    for (PadSlot& s : pad_slots_)
        if (s.pad && s.id == id) return &s;
    return nullptr;
}

// A slot's whole state, read from SDL.
void Player::read_pad(PadSlot* s) {
    s->held = 0;
    for (int b = 0; b < SDL_GAMEPAD_BUTTON_COUNT; ++b)
        if (SDL_GetGamepadButton(s->pad, SDL_GamepadButton(b))) s->held |= 1u << b;
    s->axes = sa2_input::PadState{};
    for (int a = 0; a < SDL_GAMEPAD_AXIS_COUNT; ++a)
        set_pad_axis(&s->axes, a, SDL_GetGamepadAxis(s->pad, SDL_GamepadAxis(a)));
    derive_pad(s);
}

// What the game is sent for a slot: its sticks and triggers, and the game buttons its held
// buttons send (pad_map_), a button sending a trigger holding it all the way.
void Player::derive_pad(PadSlot* s) const {
    sa2_input::PadState st = s->axes;
    st.connected = true;
    for (int b = 0; b < SDL_GAMEPAD_BUTTON_COUNT; ++b)
        if (s->held & (1u << b)) {
            st.buttons |= pad_map_[b].bit;
            if (pad_map_[b].trigger == 1) st.lt = 255;
            if (pad_map_[b].trigger == 2) st.rt = 255;
        }
    s->state = st;
}

// Whether SA2_PAD_P1 names this pad.
bool Player::is_player_one_pad(SDL_JoystickID id) const {
    if (pad_p1_vendor_ >= 0)
        return SDL_GetJoystickVendorForID(id) == pad_p1_vendor_ && SDL_GetJoystickProductForID(id) == pad_p1_product_;
    const char* device = SDL_GetJoystickNameForID(id);
    return !pad_p1_name_.empty() && device && pad_p1_name_ == device;
}

// A gamepad SDL announced: the lowest free slot, sent at once.
void Player::open_pad(SDL_JoystickID id) {
    if (slot_of(id)) return;                        // announced again (at the subsystem's start)
    if (!o_.synthetic_input.empty() && !SDL_IsJoystickVirtual(id)) { ++pads_not_virtual_; return; }
    PadSlot* free = nullptr;
    for (PadSlot& s : pad_slots_)
        if (!s.pad) { free = &s; break; }
    if (!free) { ++pads_unslotted_; return; }
    SDL_Gamepad* g = SDL_OpenGamepad(id);
    if (!g) {
        if (pads_error_.empty()) pads_error_ = strf("SDL_OpenGamepad: %s", SDL_GetError());
        return;
    }
    free->id = id;
    free->pad = g;
    const char* name = SDL_GetGamepadName(g);
    free->name = name ? name : "?";
    const char* device = SDL_GetJoystickNameForID(id);
    const std::string label = device ? device : free->name;
    std::printf("pads: added \"%s\" (vendor %04x product %04x) as slot %d\n", label.c_str(),
                unsigned(SDL_GetJoystickVendorForID(id)), unsigned(SDL_GetJoystickProductForID(id)),
                int(free - pad_slots_));
    if (free != &pad_slots_[0] && is_player_one_pad(id) && !(pad_slots_[0].pad && is_player_one_pad(pad_slots_[0].id))) {
        std::swap(*free, pad_slots_[0]);            // player one's pad: slot 0, the pad there moves up
        std::printf("pads: \"%s\" is player one (SA2_PAD_P1)\n", label.c_str());
        free = &pad_slots_[0];
    }
    read_pad(free);
    ++pads_added_;
    send_pads();
}

void Player::pad_event(const SDL_Event& e) {
    ++pad_events_;
    switch (e.type) {
    case SDL_EVENT_GAMEPAD_ADDED:
        open_pad(e.gdevice.which);
        return;
    case SDL_EVENT_GAMEPAD_REMOVED:
        if (PadSlot* s = slot_of(e.gdevice.which)) {
            SDL_CloseGamepad(s->pad);
            *s = PadSlot{};
            ++pads_removed_;
            send_pads();
        }
        return;
    case SDL_EVENT_GAMEPAD_REMAPPED:
        if (PadSlot* s = slot_of(e.gdevice.which)) { read_pad(s); pads_dirty_ = true; }
        return;
    case SDL_EVENT_GAMEPAD_BUTTON_DOWN:
    case SDL_EVENT_GAMEPAD_BUTTON_UP: {
        PadSlot* s = slot_of(e.gbutton.which);
        if (!s) return;
        if (e.gbutton.down && sa2_touch::active(touch_)) {   // a pad in hand: the overlay hides
            sa2_touch::deactivate(touch_);
            ++touch_hides_;
            pads_dirty_ = true;
        }
        const sa2_input::PadState was = s->state;
        const uint32_t bit = e.gbutton.button < SDL_GAMEPAD_BUTTON_COUNT ? 1u << e.gbutton.button : 0;
        s->held = e.gbutton.down ? s->held | bit : s->held & ~bit;
        derive_pad(s);
        // a press and its release: a record each
        if (s->state.buttons != was.buttons || s->state.lt != was.lt || s->state.rt != was.rt || pads_dirty_)
            send_pads();
        return;
    }
    case SDL_EVENT_GAMEPAD_AXIS_MOTION:
        if (PadSlot* s = slot_of(e.gaxis.which)) {
            set_pad_axis(&s->axes, e.gaxis.axis, e.gaxis.value);
            derive_pad(s);
            pads_dirty_ = true;
        }
        return;
    default:                                          // a finger
        if (sa2_touch::handle_event(touch_, e)) {
            if (e.type == SDL_EVENT_FINGER_MOTION) pads_dirty_ = true;
            else send_pads();
        }
        return;
    }
}

// The four slots to the game: each pad in its slot, and slot 0 shared with the overlay
// ("Gamepads" above).
void Player::send_pads() {
    pads_dirty_ = false;
    if (!input_) return;
    sa2_input::PadSet set;
    for (size_t i = 0; i < sa2_input::kPadSlots; ++i)
        if (pad_slots_[i].pad) set.slot[i] = pad_slots_[i].state;
    sa2tw::deadzone(&set);                          // host tweak "deadzone": the pads' left sticks
    if (pad_p1_all_) {                              // SA2_PAD_P1=ALL: every pad is player one
        sa2_input::PadState all;
        for (sa2_input::PadState& p : set.slot) {
            all = shared_slot(all, p);
            p = sa2_input::PadState{};
        }
        set.slot[0] = all;
    }
    if (touch_) {
        sa2_input::PadState t;
        sa2_touch::pad(touch_, &t);
        if (touch_platform_) t.connected = true;
        set.slot[0] = shared_slot(set.slot[0], t);
    }
    sa2tw::camera_invert(&set);
    sa2tw::hold_toggle::apply(&set, win_ && (SDL_GetWindowFlags(win_) & SDL_WINDOW_INPUT_FOCUS));
    ++pad_sets_;
    input_->send_pads(set);
}

// Before the device goes (teardown): nothing of the overlay may outlive it.
void Player::close_pads() {
    if (!pads_up_ || pads_closed_) return;
    pads_closed_ = true;
    if (pad_queue_mutex_) SDL_RemoveEventWatch(&Player::pad_watch, this);
    if (win_) {
        const SDL_PropertiesID props = SDL_GetWindowProperties(win_);
        SDL_ClearProperty(props, kPresentOverlayProperty);
        SDL_ClearProperty(props, kPresentOverlayContext);
    }
    for (PadSlot& s : pad_slots_)
        if (s.pad) { SDL_CloseGamepad(s.pad); s = PadSlot{}; }
    for (size_t k = 0; k < sa2_input::kPadSlots; ++k)
        if (synthetic_pad_[k]) {
            SDL_CloseJoystick(synthetic_pad_[k]);
            SDL_DetachVirtualJoystick(synthetic_pad_id_[k]);
            synthetic_pad_[k] = nullptr;
        }
    if (shot_.dev) {
        for (SDL_GPUTexture* t : shot_.tex)
            if (t) SDL_ReleaseGPUTexture(shot_.dev, t);
        if (shot_.down) SDL_ReleaseGPUTransferBuffer(shot_.dev, shot_.down);
        shot_.dev = nullptr;
    }
    if (touch_) {
        touch_final_ = sa2_touch::stats(touch_);
        sa2_touch::destroy(touch_);
        touch_ = nullptr;
    }
    if (gamepads_up_) { SDL_QuitSubSystem(SDL_INIT_GAMEPAD); gamepads_up_ = false; }
    if (pad_queue_mutex_) { SDL_DestroyMutex(pad_queue_mutex_); pad_queue_mutex_ = nullptr; }
}

// The report's "host_pads" (and "synthetic_input"), with --input-path only, ending ",\n".
std::string Player::pads_json() const {
    if (o_.input_path.empty()) return "";
    const sa2_touch::Stats t = touch_ ? sa2_touch::stats(touch_) : touch_final_;
    std::string slots;
    for (size_t i = 0; i < sa2_input::kPadSlots; ++i) {
        slots += std::string(i ? ", " : "") + (pad_slots_[i].pad ? jstr(pad_slots_[i].name) : "null");
    }
    std::string s = strf(
        "  \"host_pads\": {\"up\": %s, \"touch_platform\": %s, \"gamepads\": %s, \"added\": %" PRIu64
        ", \"removed\": %" PRIu64 ", \"not_virtual\": %" PRIu64 ", \"unslotted\": %" PRIu64 ", \"events\": %" PRIu64
        ", \"queue_overflows\": %" PRIu64 ", \"sets\": %" PRIu64 ", \"slots\": [%s], \"error\": %s, "
        "\"touch\": {\"finger_events\": %" PRIu64 ", \"ignored_fingers\": %" PRIu64 ", \"activations\": %" PRIu64
        ", \"hides\": %" PRIu64 ", \"draws\": %" PRIu64 ", \"draw_failures\": %" PRIu64 ", \"gpu\": %s, \"error\": %s}, "
        "\"overlay_presents\": %" PRIu64 ", \"overlay_shots\": %" PRIu64 ", \"overlay_shot_error\": %s},\n",
        pads_up_ ? "true" : "false", touch_platform_ ? "true" : "false", gamepads_up_ || pads_closed_ ? "true" : "false",
        pads_added_, pads_removed_, pads_not_virtual_, pads_unslotted_, pad_events_, pad_queue_overflows_, pad_sets_,
        slots.c_str(), pads_error_.empty() ? "null" : jstr(pads_error_).c_str(), t.finger_events, t.ignored_fingers,
        t.activations, touch_hides_, t.draws, t.draw_failures, t.gpu ? "true" : "false",
        t.error.empty() ? "null" : jstr(t.error).c_str(), overlay_presents_, shot_.written,
        shot_.error.empty() ? "null" : jstr(shot_.error).c_str());
    if (!o_.synthetic_input.empty())
        s += strf("  \"synthetic_input\": {\"lines\": %zu, \"done\": %" PRIu64 ", \"failures\": %" PRIu64
                  ", \"armed\": %s},\n", synthetic_input_.size(), synthetic_input_done_, synthetic_input_failures_,
                  synthetic_input_armed_ ? "true" : "false");
    return s;
}

// --synthetic-input's script ("Gamepads" above); a malformed line refuses the run.
bool Player::load_synthetic_input() {
    std::ifstream f(o_.synthetic_input);
    std::string line;
    uint64_t last_ms = 0;
    size_t number = 0;
    bool ok = bool(f);
    while (ok && std::getline(f, line)) {
        ++number;
        if (line.empty() || line[0] == '#') continue;
        std::istringstream row(line);
        std::string at, place, extra;
        SyntheticInput in;
        uint64_t ms = 0;
        ok = bool(row >> at >> in.verb) && canonical_u64(at, &ms) && ms <= UINT64_MAX / 1000000 && ms >= last_ms;
        const bool pad_k = in.verb.rfind("pad-", 0) == 0;
        if (ok && pad_k) ok = bool(row >> in.k) && in.k >= 0 && in.k < int(sa2_input::kPadSlots);
        if (!ok) {
        } else if (in.verb == "pad-attach" || in.verb == "pad-detach") {
        } else if (in.verb == "pad-button") {
            ok = bool(row >> in.n >> in.value) && in.n >= 0 && in.n < SDL_GAMEPAD_BUTTON_COUNT &&
                 (in.value == 0 || in.value == 1);
        } else if (in.verb == "pad-axis") {
            ok = bool(row >> in.n >> in.value) && in.n >= 0 && in.n < SDL_GAMEPAD_AXIS_COUNT && in.value >= INT16_MIN &&
                 in.value <= INT16_MAX;
        } else if (in.verb == "touch-down" || in.verb == "touch-move" || in.verb == "touch-up") {
            ok = bool(row >> in.k) && in.k >= 0;
            if (ok && (row >> place)) {
                std::string a, b;
                if (place[0] == '@') {
                    in.at_control = true;
                    ok = parse_control(place.substr(1), &in.control);
                    if (ok && (row >> a)) ok = (row >> b) && parse_float(a, &in.x) && parse_float(b, &in.y);
                } else {
                    ok = (row >> b) && parse_float(place, &in.x) && parse_float(b, &in.y);
                }
            } else {
                ok = ok && in.verb == "touch-up";        // a finger lifts where it is
            }
        } else {
            ok = false;
        }
        if (ok && (row >> extra)) ok = false;
        if (!ok) break;
        in.offset_ns = ms * 1000000;
        synthetic_input_.push_back(in);
        last_ms = ms;
    }
    if (!ok) {
        usage_failure(f ? strf("malformed --synthetic-input line %zu", number) : "cannot read " + o_.synthetic_input);
        stop_ = true;
        stop_reason_ = "refused before the stream was read";
    }
    return ok;
}

// The script's lines that are due, from the first service after the first Present: virtual
// gamepads (SDL raises their events at its next pump) and finger events pushed to SDL's queue.
void Player::push_due_synthetic_input() {
    if (synthetic_input_cursor_ >= synthetic_input_.size()) return;
    if (!synthetic_input_armed_) {
        if (frame_ < 1) return;
        synthetic_input_armed_ = true;
        synthetic_input_ticks_ = SDL_GetTicksNS();
    }
    const uint64_t now = SDL_GetTicksNS();
    while (synthetic_input_cursor_ < synthetic_input_.size()) {
        const SyntheticInput& in = synthetic_input_[synthetic_input_cursor_];
        if (now < synthetic_input_ticks_ + in.offset_ns) return;
        ++synthetic_input_cursor_;
        SDL_Joystick*& joy = synthetic_pad_[in.verb.rfind("pad-", 0) == 0 ? in.k : 0];
        bool ok = false;
        if (in.verb == "pad-attach") {
            if (!joy) {
                SDL_VirtualJoystickDesc d;
                SDL_INIT_INTERFACE(&d);
                d.type = SDL_JOYSTICK_TYPE_GAMEPAD;
                d.naxes = SDL_GAMEPAD_AXIS_COUNT;
                d.nbuttons = SDL_GAMEPAD_BUTTON_COUNT;  // a default mapping: button i is SDL_GamepadButton i
                const std::string name = "SA2 synthetic pad " + std::to_string(in.k);
                d.name = name.c_str();
                d.vendor_id = 0x5341;                   // told apart by SA2_PAD_P1=ID:5341:100K too
                d.product_id = Uint16(0x100 + in.k);
                const SDL_JoystickID id = SDL_AttachVirtualJoystick(&d);
                joy = id ? SDL_OpenJoystick(id) : nullptr;
                synthetic_pad_id_[in.k] = id;
                ok = joy != nullptr;
            }
        } else if (in.verb == "pad-detach") {
            if (joy) {
                SDL_CloseJoystick(joy);
                joy = nullptr;
                ok = SDL_DetachVirtualJoystick(synthetic_pad_id_[in.k]);
            }
        } else if (in.verb == "pad-button") {
            ok = joy && SDL_SetJoystickVirtualButton(joy, in.n, in.value != 0);
        } else if (in.verb == "pad-axis") {
            ok = joy && SDL_SetJoystickVirtualAxis(joy, in.n, Sint16(in.value));
        } else {
            int w = 0, h = 0;
            SDL_GetWindowSizeInPixels(win_, &w, &h);
            float x = in.x, y = in.y;
            if (in.at_control && w > 0 && h > 0) {
                float cx = 0, cy = 0;
                sa2_touch::control_centre(touch_, uint32_t(w), uint32_t(h), in.control, &cx, &cy);
                const float r = sa2_touch::stick_radius(touch_, uint32_t(w), uint32_t(h));
                x = (cx + in.x * r) / float(w);
                y = (cy + in.y * r) / float(h);
            }
            SDL_Event e{};
            e.type = in.verb == "touch-down" ? SDL_EVENT_FINGER_DOWN
                   : in.verb == "touch-move" ? SDL_EVENT_FINGER_MOTION : SDL_EVENT_FINGER_UP;
            e.tfinger.timestamp = SDL_GetTicksNS();
            e.tfinger.touchID = kSyntheticTouch;
            e.tfinger.fingerID = SDL_FingerID(in.k) + 1;
            e.tfinger.x = x;
            e.tfinger.y = y;
            e.tfinger.pressure = e.type == SDL_EVENT_FINGER_UP ? 0.0f : 1.0f;
            e.tfinger.windowID = input_window_id_;
            ok = SDL_PushEvent(&e);
        }
        if (ok) {
            ++synthetic_input_done_;
        } else {
            ++synthetic_input_failures_;
            if (pads_error_.empty())
                pads_error_ = strf("--synthetic-input line %zu (%s) failed: %s", synthetic_input_cursor_,
                                   in.verb.c_str(), SDL_GetError());
        }
    }
}

// The second host's device at Present, between its blit of the frame to the swapchain texture
// and its submission: the overlay over the frame, while it is active.
void Player::present_overlay(void* self, SDL_GPUDevice* dev, SDL_GPUCommandBuffer* cb, SDL_GPUTexture* frame,
                             Uint32 frame_w, Uint32 frame_h, SDL_GPUTexture* target, Uint32 w, Uint32 h) {
    auto* p = static_cast<Player*>(self);
    if (!p || !p->touch_ || p->pads_closed_ || !dev || !cb) return;
    sa2_touch::attach_device(p->touch_, dev);
    sa2_touch::set_frame(p->touch_, frame_w, frame_h);
    uint32_t top = 0, bottom = 0;
    if (p->picture_anchor_ >= 0 && sa2_touch::picture_rows(p->touch_, w, h, &top, &bottom)) {
        const std::string said = strf("picture: %ux%u at rows %u-%u of the window's %ux%u (SA2_PLAYER_PICTURE_Y=%.2f; "
                                      "the controls %s)", frame_w, frame_h, top, bottom, w, h, p->picture_y_,
                                      sa2_touch::below_picture(p->touch_, w, h) ? "below it" : "over it");
        if (said != p->picture_said_) {
            std::printf("%s\n", said.c_str());
            p->picture_said_ = said;
        }
    }
    if (sa2_touch::active(p->touch_)) {
        sa2_touch::draw(p->touch_, cb, target, w, h);
        ++p->overlay_presents_;
    }
    sa2_frametime::draw(dev, cb, target, w, h, p->frame_, p->pace_.get(), p->touch_, p->win_);   // host tweak frametime-overlay
    if (!p->o_.overlay_out.empty()) p->overlay_shot(dev, cb, frame, frame_w, frame_h, w, h);
}

// --overlay-out: the swapchain texture cannot be read, so what it was given is made again beside
// it in the same command buffer: the frame blitted as the device blits it, alone and with the
// overlay drawn over it (nothing while inactive), both downloaded for write_overlay_shot.
void Player::overlay_shot(SDL_GPUDevice* dev, SDL_GPUCommandBuffer* cb, SDL_GPUTexture* frame, Uint32 frame_w,
                          Uint32 frame_h, Uint32 w, Uint32 h) {
    OverlayShot& s = shot_;
    if (s.failed || s.pending || s.written >= kOverlayShots || !frame || !w || !h) return;
    const SDL_GPUTextureFormat format = SDL_GetGPUSwapchainTextureFormat(dev, win_);
    if (s.dev != dev || s.w != w || s.h != h || s.format != format) {
        if (s.dev) {
            for (SDL_GPUTexture*& t : s.tex)
                if (t) { SDL_ReleaseGPUTexture(s.dev, t); t = nullptr; }
            if (s.down) { SDL_ReleaseGPUTransferBuffer(s.dev, s.down); s.down = nullptr; }
        }
        s.dev = dev;
        s.w = w;
        s.h = h;
        s.format = format;
        if (format != SDL_GPU_TEXTUREFORMAT_B8G8R8A8_UNORM && format != SDL_GPU_TEXTUREFORMAT_B8G8R8A8_UNORM_SRGB &&
            format != SDL_GPU_TEXTUREFORMAT_R8G8B8A8_UNORM && format != SDL_GPU_TEXTUREFORMAT_R8G8B8A8_UNORM_SRGB) {
            s.error = strf("swapchain format %d is not 8-bit RGBA", int(format));
            s.failed = true;
            return;
        }
        SDL_GPUTextureCreateInfo ti{};
        ti.type = SDL_GPU_TEXTURETYPE_2D;
        ti.format = format;
        ti.usage = SDL_GPU_TEXTUREUSAGE_COLOR_TARGET;
        ti.width = w;
        ti.height = h;
        ti.layer_count_or_depth = 1;
        ti.num_levels = 1;
        ti.sample_count = SDL_GPU_SAMPLECOUNT_1;
        for (SDL_GPUTexture*& t : s.tex) t = SDL_CreateGPUTexture(dev, &ti);
        SDL_GPUTransferBufferCreateInfo bi{};
        bi.usage = SDL_GPU_TRANSFERBUFFERUSAGE_DOWNLOAD;
        bi.size = 2 * w * h * 4;
        s.down = SDL_CreateGPUTransferBuffer(dev, &bi);
        if (!s.tex[0] || !s.tex[1] || !s.down) {
            s.error = strf("the shot's textures: %s", SDL_GetError());
            s.failed = true;
            return;
        }
    }
    for (SDL_GPUTexture* t : s.tex) {
        SDL_GPUBlitInfo bi{};
        bi.source = SDL_GPUBlitRegion{frame, 0, 0, 0, 0, frame_w, frame_h};
        bi.destination = SDL_GPUBlitRegion{t, 0, 0, 0, 0, w, h};
        bi.load_op = SDL_GPU_LOADOP_DONT_CARE;
        bi.flip_mode = SDL_FLIP_NONE;
        bi.filter = (w == frame_w && h == frame_h) ? SDL_GPU_FILTER_NEAREST : SDL_GPU_FILTER_LINEAR;
        SDL_BlitGPUTexture(cb, &bi);
    }
    sa2_touch::draw(touch_, cb, s.tex[1], w, h);
    sa2_frametime::draw(dev, cb, s.tex[1], w, h, frame_, pace_.get(), touch_, win_);   // host tweak frametime-overlay
    SDL_GPUCopyPass* cp = SDL_BeginGPUCopyPass(cb);
    if (!cp) { s.error = strf("SDL_BeginGPUCopyPass: %s", SDL_GetError()); s.failed = true; return; }
    for (uint32_t i = 0; i < 2; ++i) {
        SDL_GPUTextureRegion from{};
        from.texture = s.tex[i];
        from.w = w;
        from.h = h;
        from.d = 1;
        SDL_GPUTextureTransferInfo to{};
        to.transfer_buffer = s.down;
        to.offset = i * w * h * 4;
        SDL_DownloadFromGPUTexture(cp, &from, &to);
    }
    SDL_EndGPUCopyPass(cp);
    s.pending = true;
    s.frame = frame_;
}

// The shot the last Present asked for, once the device has submitted it: DIR/frame-N-game.png
// and DIR/frame-N-swapchain.png.
void Player::write_overlay_shot() {
    OverlayShot& s = shot_;
    s.pending = false;
    const uint8_t* p = nullptr;
    if (!SDL_WaitForGPUIdle(s.dev) ||
        !(p = static_cast<const uint8_t*>(SDL_MapGPUTransferBuffer(s.dev, s.down, false)))) {
        s.error = strf("the shot's download: %s", SDL_GetError());
        s.failed = true;
        return;
    }
    const size_t bytes = size_t(s.w) * s.h * 4;
    const bool rgba = s.format == SDL_GPU_TEXTUREFORMAT_R8G8B8A8_UNORM ||
                      s.format == SDL_GPU_TEXTUREFORMAT_R8G8B8A8_UNORM_SRGB;
    std::vector<uint8_t> bgra(bytes);
    for (int i = 0; i < 2; ++i) {
        std::memcpy(bgra.data(), p + size_t(i) * bytes, bytes);
        if (rgba)
            for (size_t k = 0; k < bytes; k += 4) std::swap(bgra[k], bgra[k + 2]);
        const std::string path = strf("%s/frame-%u-%s.png", o_.overlay_out.c_str(), s.frame, i ? "swapchain" : "game");
        std::string err;
        if (!tex::write_png_rgb(path.c_str(), bgra.data(), s.w, s.h, s.w * 4, &err) && s.error.empty()) s.error = err;
    }
    SDL_UnmapGPUTransferBuffer(s.dev, s.down);
    ++s.written;
}

// ---------------------------------------------------------------------- sound

// SDL's default playback device as a float32 stereo 48 kHz stream. A failure is the
// replay's (no playback device for --audio); the WAV, when asked for, is written
// whatever the device does.
bool Player::setup_audio() {
    if (!SDL_InitSubSystem(SDL_INIT_AUDIO)) {
        audio_failure_ = strf("SDL_InitSubSystem(SDL_INIT_AUDIO): %s", SDL_GetError());
        return false;
    }
    audio_driver_ = SDL_GetCurrentAudioDriver() ? SDL_GetCurrentAudioDriver() : "?";
    const SDL_AudioSpec spec{SDL_AUDIO_F32, int(Sa2Mixer::kChannels), int(Sa2Mixer::kRate)};
    audio_stream_ = SDL_OpenAudioDeviceStream(SDL_AUDIO_DEVICE_DEFAULT_PLAYBACK, &spec, nullptr, nullptr);
    if (!audio_stream_) {
        audio_failure_ = strf("SDL_OpenAudioDeviceStream: %s", SDL_GetError());
        return false;
    }
    if (!SDL_SetAudioStreamGetCallback(audio_stream_, on_audio_request, &audio_tap_)) {
        audio_failure_ = strf("SDL_SetAudioStreamGetCallback: %s", SDL_GetError());
        return false;
    }
    if (!SDL_ResumeAudioStreamDevice(audio_stream_)) {
        audio_failure_ = strf("SDL_ResumeAudioStreamDevice: %s", SDL_GetError());
        return false;
    }
    std::fprintf(stderr, "audio: SDL %s driver, default playback device, float32 stereo %u Hz\n",
                 audio_driver_.c_str(), Sa2Mixer::kRate);
    return true;
}

// The sound up to this Present's virtual time, as the mixer rendered it (at the end,
// the sound after the last Present): written to --audio-wav, and with --audio queued
// to the device now, before --pace waits for the frame. The first Present's block,
// the sound before the first frame, is the WAV's alone: the device gets kAudioLeadMs
// of silence in its place, and from then on each block plays that lead after its
// frame shows. A block that would find the queue past kAudioQueueBound is dropped (an
// unpaced replay runs faster than the sound can play). The queue's depth is noted
// before each block goes in: its least is how close the device came to running dry.
void Player::audio_block() {
    sa2tw::mono_audio(*mixer_);
    sa2tw::swap_audio(*mixer_);
    const std::vector<float>& blk = mixer_->block();
    if (wav_open_ && !blk.empty() && !wav_.write(blk.data(), blk.size() / 2)) {
        io_failure("writing " + o_.audio_wav + " failed");
        wav_open_ = false;
    }
    if (wav_open_ && frame_ % 100 == 0) wav_.flush();
    if (audio_stream_ && !audio_lead_queued_) {
        audio_lead_queued_ = true;
        audio_pre_first_samples_ = blk.size() / 2;
        const std::vector<float> lead(size_t(Sa2Mixer::kRate) * kAudioLeadMs / 1000 * Sa2Mixer::kChannels, 0.0f);
        if (SDL_PutAudioStreamData(audio_stream_, lead.data(), int(lead.size() * sizeof(float))))
            audio_tap_.feeding.store(true, std::memory_order_relaxed);
        else if (audio_failure_.empty())
            audio_failure_ = strf("SDL_PutAudioStreamData: %s", SDL_GetError());
    } else if (audio_stream_ && !blk.empty()) {
        const int queued = SDL_GetAudioStreamQueued(audio_stream_);
        const int bytes = int(blk.size() * sizeof(float));
        if (queued >= 0) {
            audio_depth_n_++;
            audio_depth_sum_ += uint64_t(queued);
            audio_depth_min_ = audio_depth_min_ < 0 ? queued : std::min(audio_depth_min_, queued);
            audio_depth_max_ = std::max(audio_depth_max_, queued);
        }
        if (queued > kAudioQueueBound) {
            audio_dropped_++;
        } else if (SDL_PutAudioStreamData(audio_stream_, blk.data(), bytes)) {
            audio_blocks_++;
            audio_bytes_queued_ += uint64_t(bytes);
        } else if (audio_failure_.empty()) {
            audio_failure_ = strf("SDL_PutAudioStreamData: %s", SDL_GetError());
        }
    }
    mixer_->clear_block();
}

// The end of the sound: the records after the last Present closed as the last block,
// the replay no longer feeding the device, its queue played out (at most its length and
// a second), then the device's own buffer (twice its period, at most a quarter second),
// the WAV closed and its .json written beside it.
void Player::finish_audio() {
    if (!mixer_) return;
    if (!mixer_->block().empty()) {
        audio_tail_samples_ = mixer_->block().size() / 2;
        audio_block();
    }
    audio_tap_.feeding.store(false, std::memory_order_relaxed);
    if (audio_stream_) {
        sa2tw::slow_mode::finish(audio_stream_);
        audio_queued_at_end_ = SDL_GetAudioStreamQueued(audio_stream_);
        const double start = now_s();
        const double limit = double(std::max(audio_queued_at_end_, 0)) / (Sa2Mixer::kRate * 8.0) + 1.0;
        while (SDL_GetAudioStreamQueued(audio_stream_) > 0 && now_s() - start < limit) {
            timespec ts{0, 10000000L};
            nanosleep(&ts, nullptr);
            pump_events();
        }
        audio_drain_wait_ms_ = uint64_t((now_s() - start) * 1000.0);
        // The queue is empty once SDL has taken its last bytes into the device's buffer,
        // which still has to play them out before the stream is destroyed.
        SDL_AudioSpec dev{};
        int frames = 0;
        double tail = 0.05;
        if (SDL_GetAudioDeviceFormat(SDL_GetAudioStreamDevice(audio_stream_), &dev, &frames) && dev.freq > 0 &&
            frames > 0)
            tail = std::min(0.25, 2.0 * double(frames) / double(dev.freq));
        const double t0 = now_s();
        while (now_s() - t0 < tail) {
            timespec ts{0, 10000000L};
            nanosleep(&ts, nullptr);
            pump_events();
        }
        audio_tail_wait_ms_ = uint64_t((now_s() - t0) * 1000.0);
    }
    if (o_.audio_wav.empty() || !wav_started_) return;
    const bool ok = wav_open_ && wav_.close();
    if (wav_open_ && !ok) io_failure("writing " + o_.audio_wav + " failed");
    wav_open_ = false;
    const std::string side = o_.audio_wav + ".json";
    FILE* f = std::fopen(side.c_str(), "w");
    if (!f) { io_failure("cannot write " + side); return; }
    const std::string s = strf("{\"tool\": \"host/player sa2_player --audio-wav\", \"wav\": %s, \"ok\": %s, "
                               "\"format\": \"float32\", \"sample_rate\": %u, \"channels\": %u, \"frames\": %" PRIu64
                               ", \"origin_us\": %" PRIu64 ", \"first_frame_us\": %s, \"presents\": %u, "
                               "\"audio_problems\": %" PRIu64 ", \"stream\": %s, \"stream_sha256\": %s}\n",
                               jstr(o_.audio_wav).c_str(), ok ? "true" : "false", Sa2Mixer::kRate, Sa2Mixer::kChannels,
                               wav_.frames(), mixer_->origin_us(),
                               any_frame_us_ ? std::to_string(first_frame_us_).c_str() : "null", frame_,
                               mixer_->problems(), jstr(o_.stream).c_str(),
                               stream_sha256_.empty() ? "null" : jstr(stream_sha256_).c_str());
    wav_sidecar_ok_ = std::fwrite(s.data(), 1, s.size(), f) == s.size();
    if (std::fclose(f) != 0 || !wav_sidecar_ok_) { wav_sidecar_ok_ = false; io_failure("writing " + side + " failed"); }
}

// The report's "audio": what was asked for, the stream's AUDIO records, the device's
// counts, the WAV's, and the mixer's (mixer.h).
std::string Player::audio_json() const {
    std::string s = strf("{\"asked\": {\"device\": %s, \"wav\": %s}, \"stream_records\": %" PRIu64 ", ",
                         o_.audio ? "true" : "false", o_.audio_wav.empty() ? "null" : jstr(o_.audio_wav).c_str(),
                         audio_records_);
    if (o_.audio) {
        const double ms_per_byte = 1000.0 / (double(Sa2Mixer::kRate) * kAudioFrameBytes);
        const std::string depth =
            audio_depth_n_ ? strf("{\"blocks\": %" PRIu64 ", \"min_ms\": %.3f, \"mean_ms\": %.3f, \"max_ms\": %.3f}",
                                  audio_depth_n_, audio_depth_min_ * ms_per_byte,
                                  double(audio_depth_sum_) / double(audio_depth_n_) * ms_per_byte,
                                  audio_depth_max_ * ms_per_byte)
                           : std::string("null");
        s += strf("\"device\": {\"open\": %s, \"driver\": %s, \"failure\": %s, \"lead_ms\": %d, "
                  "\"pre_first_frame_samples\": %" PRIu64 ", \"tail_samples\": %" PRIu64 ", \"blocks_queued\": %" PRIu64
                  ", \"bytes_queued\": %" PRIu64 ", \"blocks_dropped\": %" PRIu64 ", \"queue_before_block\": %s, "
                  "\"requests\": %" PRIu64 ", \"shortfalls\": %" PRIu64 ", \"shortfall_bytes\": %" PRIu64
                  ", \"queue_bound_bytes\": %d, \"queued_at_end\": %d, \"drain_wait_ms\": %" PRIu64
                  ", \"tail_wait_ms\": %" PRIu64 "}, ",
                  audio_stream_ ? "true" : "false", jstr(audio_driver_).c_str(),
                  audio_failure_.empty() ? "null" : jstr(audio_failure_).c_str(), kAudioLeadMs,
                  audio_pre_first_samples_, audio_tail_samples_, audio_blocks_, audio_bytes_queued_, audio_dropped_,
                  depth.c_str(), audio_tap_.requests.load(), audio_tap_.shortfalls.load(),
                  audio_tap_.shortfall_bytes.load(), kAudioQueueBound, audio_queued_at_end_, audio_drain_wait_ms_,
                  audio_tail_wait_ms_);
    }
    else
        s += "\"device\": null, ";
    if (!o_.audio_wav.empty())
        s += strf("\"wav\": {\"path\": %s, \"sidecar\": %s, \"frames\": %" PRIu64 ", \"bytes\": %" PRIu64
                  ", \"failed\": %s, \"sidecar_written\": %s}, ",
                  jstr(o_.audio_wav).c_str(), jstr(o_.audio_wav + ".json").c_str(), wav_.frames(), wav_.bytes(),
                  wav_.failed() ? "true" : "false", wav_sidecar_ok_ ? "true" : "false");
    else
        s += "\"wav\": null, ";
    s += strf("\"first_frame_us\": %s, ", any_frame_us_ ? std::to_string(first_frame_us_).c_str() : "null");
    return s + (mixer_ ? mixer_->json_fields() : std::string("\"problems\": {\"count\": 0, \"first\": []}")) + "}";
}

// ---------------------------------------------------------------------- run

bool Player::load_expect() {
    FILE* f = std::fopen(o_.expect.c_str(), "r");
    if (!f) return false;
    char line[1024];
    if (!std::fgets(line, sizeof line, f)) { std::fclose(f); return true; }   // header
    while (std::fgets(line, sizeof line, f)) {
        Expect e{};
        char what[512] = "";
        if (std::sscanf(line, "%u\t%u\t%u\t%u\t%u\t%u\t%u\t%u\t%u\t%511[^\n]", &e.frame, &e.x, &e.y, &e.lo, &e.hi,
                        &e.b, &e.g, &e.r, &e.tol, what) >= 9) {
            e.what = what;
            if (int(e.lo) <= o_.stage && o_.stage <= int(e.hi)) expects_.push_back(e);
        }
    }
    std::fclose(f);
    return true;
}

int Player::run() {
    t_start_ = now_s();
    // The report's own path first: when it is refused there is nowhere to
    // record anything, so the run ends here and writes nothing.
    if (!o_.report.empty())
        if (const std::string why = refuse_output(o_.report); !why.empty()) {
            std::fprintf(stderr, "refused: %s (so no report is written)\n", why.c_str());
            return kExitUsage;
        }
    report_ok_ = true;
    {
        Pin p;
#ifdef _WIN32
        // No /proc on Windows: this executable's own module file, as d9g_entry.cpp reads it.
        wchar_t self[32768];
        const DWORD n = GetModuleFileNameW(nullptr, self, DWORD(sizeof self / sizeof self[0]));
        if (n > 0 && n < sizeof self / sizeof self[0]) p.path = p.real = fs::path(std::wstring(self, n)).string();
        p.sha256 = p.real.empty() ? "" : sha256_file(p.real);
#else
        p.path = "/proc/self/exe";
        std::error_code ec;
        p.real = fs::read_symlink("/proc/self/exe", ec).string();
        p.sha256 = sha256_file("/proc/self/exe");
#endif
        files_["player"] = p;
    }
    // The key sender exists from the first report, so every report of a run
    // given --input-path says so; it starts once the window is up (below).
    if (!o_.input_path.empty()) input_ = std::make_unique<sa2_input::KeySender>(o_.input_path);
    // This run's report replaces any earlier one before anything else happens,
    // so a player that dies at any point is never read by an earlier run's.
    if (const std::string why = diagnostic_path_refusal(); !why.empty()) {
        std::fprintf(stderr, "refused: %s\n", why.c_str()); return kExitUsage;
    }
    if (!write_report(false)) return finish();
    // Every other output path next, and the frames files truncated before the
    // stream is opened, so no run, refused or not, leaves an earlier run's
    // frames behind.
    if (!check_outputs()) { stop_reason_ = "refused before the stream was read"; return finish(); }
    if (!o_.frames_out.empty() && !open_frames_out()) { stop_reason_ = "the frames could not be written"; return finish(); }
    if (!o_.pace_log.empty() && !open_pace_log()) { stop_reason_ = "the pace log could not be written"; return finish(); }
    if (!open_input_diagnostics() || !load_synthetic_keys()) return finish();
    // SA2_PLAYER_PROF=<Hz>: this thread's CPU sampler (prof.h), off unless set.
    if (const std::string line = sa2prof::start_from_env(o_.report, refuse_output); !line.empty())
        std::printf("%s\n", line.c_str());
    if ((!o_.key_log.empty() || !o_.present_log.empty()) && !write_report(false)) return finish();
    std::string err;
    if (o_.explicit_live()) {
        if (!o_.stats_only) {
            pin_host_files();
            if (!validate_host_files() || !setup_sdl() || !setup_window(640, 480)) return finish();
            if (input_) {
                sender_start_attempted_ = true;
                sender_started_ = input_->start();
                if (!sender_started_) { usage_failure("--input-path: " + input_->last_error()); return finish(); }
                key_log_header_accepted_ = key_log_file_ && input_->log_ok();
                if (!input_->log_ok()) { input_log_ok_ = false; diagnostic_failure(input_->log_error()); return finish(); }
            }
        }
        phase_ = "live_open";
        if (!write_report(false)) return finish();
        // Reader ownership begins here, including any failure from open_live.
        const int video = video_fd_, cancel = cancel_fd_;
        video_fd_ = cancel_fd_ = -1;
        stream_file_ = true;
        // A live session's bytes are not hashed: no record or check reads a live session's
        // stream_sha256 (play_live.py, play_receipt.py, check_s5_exit.py's play clauses, the
        // launcher's and the selftest's records, installer_receipt.py), and hashing them was a
        // tenth of the player's time (more without the SHA extensions: Wine, arm64). Its report
        // says stream_sha256 null; a replay of a file is hashed as before.
        rd_.set_live_hashing(false);
        if (!accept_live_outcome(rd_.open_live(video, cancel, &Player::service_thunk, this, 20))) return finish();
    } else {
    const int op = rd_.open(o_.stream, &err);
    stream_file_ = op != Sa2StreamReader::OPEN_IO;
    if (op != Sa2StreamReader::OPEN_OK) {
        if (op == Sa2StreamReader::OPEN_IO) io_failure(err);
        else stream_problem("refused at open: " + err);
        stop_reason_ = op == Sa2StreamReader::OPEN_IO ? "the stream could not be read" : "the stream was refused";
        return finish();
    }
    }
    opened_ = true;
    // A live stream's schedule moves after a frame later than the sound's lead (pace.h).
    if (o_.pace && host()) pace_ = std::make_unique<Sa2Pace>(o_.speed, rd_.live() ? kAudioLeadMs / 1000.0 : -1.0);
    if (o_.sound()) {
        mixer_ = std::make_unique<Sa2Mixer>();
        if (rd_.live()) {
            mixer_->set_store_check_every(kLiveStoreCheckEvery);
            mixer_->set_limiter(true);
        }
        if (rd_.version() != SA2S_VERSION_AUDIO)
            mixer_->problem("the stream is version 1: it holds no audio (a presentation run records it with "
                            "SA2_REC_AUDIO=1)");
        if (!o_.audio_wav.empty()) {
            std::string werr;
            wav_open_ = wav_started_ = wav_.open(o_.audio_wav, &werr);
            if (!wav_open_) { io_failure(werr); stop_reason_ = "the sound could not be written"; return finish(); }
        }
    }
    rd_.set_idle(&Player::idle_thunk, this, 20);   // a live stream only; see stream.h
    const std::string& hj = rd_.header_json();     // the whole of it goes to --report
    std::printf("stream %s: version %u%s, header (%zu bytes) %s%s\n", o_.stream.c_str(), rd_.version(),
                rd_.compressed() ? " (zstd)" : "", hj.size(), hj.substr(0, 240).c_str(), hj.size() > 240 ? " ..." : "");
    check_entries_table();
    if (!o_.expect.empty() && !load_expect()) {
        io_failure("cannot read " + o_.expect);
        stop_reason_ = "the --expect file could not be read";
        return finish();
    }
    if (!o_.stats_only && !o_.explicit_live()) {
        pin_host_files();
        if (!validate_host_files() || !setup_sdl()) return finish();
    }
    if (o_.audio && sdl_up_) setup_audio();
    sa2tw::slow_mode::start(pace_, audio_stream_, o_.speed, rd_.live() ? kAudioLeadMs / 1000.0 : -1.0);
    phase_ = "replay";
    Sa2Record r;
    for (;;) {
        sa2prof::zone(sa2prof::kZoneStream);
        if (o_.explicit_live()) {
            if (!accept_live_outcome(rd_.next_live(&r))) break;
        } else {
            const int got = rd_.next(&r, &err);
            if (got < 0) { stream_problem("malformed record: " + err); stop_reason_ = "malformed stream"; break; }
            if (got == 0) break;
        }
        if (end_records_) { after_end_++; stream_problem(strf("record seq %u (kind %u) after END", r.seq, r.kind)); }
        if (r.seq != expect_seq_) {
            seq_gaps_++;
            stream_problem(strf("record seq %u where seq %u was due", r.seq, expect_seq_));
        }
        expect_seq_ = r.seq + 1;
        if (r.kind < 8) kinds_[r.kind]++; else unknown_kinds_++;
        check_record(r);
        if (r.kind == SA2S_END) end_records_++;
        // Only a call, a d3dx record or END closes a pending Present: the
        // recorder writes a call's NOTEs right after the call, so they can sit
        // between a Present and its FRAME.
        if (present_pending_ && (r.kind == SA2S_CALL || r.kind == SA2S_D3DX || r.kind == SA2S_END)) do_present(nullptr);
        if (stop_) break;
        switch (r.kind) {
        case SA2S_CALL: sa2prof::zone(sa2prof::kZoneCall); on_call(r); break;
        case SA2S_D3DX: sa2prof::zone(sa2prof::kZoneD3dx); on_d3dx(r); break;
        case SA2S_FRAME:
            frame_records_++;
            if (present_pending_) do_present(&r);
            else stream_problem(strf("FRAME seq %u follows no Present", r.seq));
            break;
        case SA2S_NOTE:
            notes_++;
            if (first_notes_.size() < 20) first_notes_.push_back(r.note);
            break;
        case SA2S_AUDIO:
            sa2prof::zone(sa2prof::kZoneAudio);
            if (rd_.version() == SA2S_VERSION_AUDIO) {
                audio_records_++;
                if (mixer_) mixer_->on_audio(r, rd_);
            }
            break;
        default:
            break;
        }
        if (r.arg_missing) error(r, "read an argument the record does not carry");
        last_accepted_seq_ = r.seq;
        any_accepted_seq_ = true;
        if (o_.explicit_live() && !report_io_failed_) on_idle();
        if (stop_) {
            if (o_.explicit_live() && close_requests_) {
                live_status_ = "cancelled";
                live_detail_ = "SDL close after the accepted record";
                stream_problem("live reader cancelled: " + live_detail_);
            }
            break;
        }
    }
    if (present_pending_ && !stop_) do_present(nullptr);
    return finish();
}

// The verdict, the summary, the final report, teardown; the exit status.
int Player::finish() {
    if (const std::string line = sa2prof::stop(); !line.empty()) std::printf("%s\n", line.c_str());
    if (opened_ && o_.stop_draw_frame && !checkpoint_reached_)
        note_error(strf("draw checkpoint %u:%u was not reached", o_.stop_draw_frame, o_.stop_draw_ordinal));
    const double wall = now_s() - t_start_;
    if (opened_ && stop_reason_ == "end of stream" && !end_records_ && !o_.allow_truncated)
        stream_problem("no END record: the stream was cut short (the writer's .status sidecar says why); "
                       "--allow-truncated plays it without failing");
    // The stream is pinned whatever stopped the run: by the bytes its records
    // were read from when they were read to the end, and otherwise by reading
    // the rest of the file into the same hash now (a stop by --max-frames, a
    // failure, a refusal at open). A live session's descriptor stream is not hashed
    // (set_live_hashing at open_live), so its sha256 is null.
    if (stream_file_) {
        records_to_end_ = rd_.whole_file_read();
        // Explicit live cancellation never drains an open producer to EOF.
        const bool live_complete_bytes = records_to_end_ && end_records_ && !stop_ && !stream_problems_;
        stream_sha256_ = o_.explicit_live() ? (live_complete_bytes ? rd_.file_sha256() : "")
                                         : records_to_end_ ? rd_.file_sha256() : rd_.hash_rest();
        hashed_after_stop_ = !records_to_end_ && !stream_sha256_.empty();
    }
    if (y4m_ && (std::fflush(y4m_) != 0 || std::ferror(y4m_))) io_failure("writing " + o_.frames_out + " failed");
    if (times_ && (std::fflush(times_) != 0 || std::ferror(times_))) io_failure("writing " + o_.frames_out + ".times.tsv failed");
    if (y4m_) { std::fclose(y4m_); y4m_ = nullptr; }
    if (times_) { std::fclose(times_); times_ = nullptr; }
    if (pace_log_) {
        if (std::fflush(pace_log_) != 0 || std::ferror(pace_log_)) io_failure("writing " + o_.pace_log + " failed");
        std::fclose(pace_log_);
        pace_log_ = nullptr;
    }
    finish_audio();
    finish_input_diagnostics();
    for (const auto& e : expects_)
        if (!e.checked) {
            expect_fail_++;
            std::printf("expect frame %u (%u,%u): FAIL, the frame never came  %s\n", e.frame, e.x, e.y, e.what.c_str());
        }

    uint64_t by[C_COUNT] = {0, 0, 0, 0};
    for (const auto& [k, m] : mstat_) for (int c = 0; c < C_COUNT; c++) by[c] += m.by[c];
    for (const auto& [k, m] : xstat_) for (int c = 0; c < C_COUNT; c++) by[c] += m.by[c];

    // The failures, most basic class first; the exit status follows the first.
    if (o_.explicit_live() && stream_file_ && (!records_to_end_ || !end_records_))
        stream_problem("live stream incomplete: clean byte EOF and a logical END are both required");
    failures_ = basic_failures_;
    if (stream_problems_)
        failures_.push_back(strf("stream: %" PRIu64 " problem(s); first: ", stream_problems_) + first_stream_problems_[0]);
    // A NOTE is the writer saying it left something out of the stream, so a
    // replay of it is a replay of less than the guest drew.
    const bool notes_fail = notes_ && !o_.allow_notes;
    if (notes_fail)
        failures_.push_back(strf("stream: %" PRIu64 " NOTE record(s), each something the writer did not record; "
                                 "first: %s (--allow-notes plays such a stream without failing)", notes_,
                                 first_notes_.empty() ? "" : first_notes_[0].c_str()));
    // The replay's failures. A stats read has those that need no device: a
    // CreateDevice it cannot honour, at least one Present, the errors, and the
    // calls, entries and argument values not handled.
    std::vector<std::string> replay;
    if (!device_failure_.empty()) replay.push_back("device creation failed: " + device_failure_);
    if (opened_) {
        if (frame_ == 0 && !checkpoint_reached_) replay.push_back(o_.stats_only ? "the stream holds no Present" : "no Present was replayed");
        if (errors_) replay.push_back(strf("%" PRIu64 " error(s); first: ", errors_) + first_errors_[0]);
        if (by[C_UNHANDLED]) {
            std::string names;
            for (const auto& [k, m] : mstat_)
                if (m.by[C_UNHANDLED] && names.size() < 300)
                    names += strf("%s%s::%s %" PRIu64, names.empty() ? "" : ", ", type_name(k >> 16),
                                  method_name(k >> 16, k & 0xFFFF), m.by[C_UNHANDLED]);
            for (const auto& [k, m] : xstat_)
                if (m.by[C_UNHANDLED] && names.size() < 300)
                    names += strf("%s%s %" PRIu64, names.empty() ? "" : ", ", entry_label(k).c_str(), m.by[C_UNHANDLED]);
            replay.push_back(strf("%" PRIu64 " call(s) not handled (%s)", by[C_UNHANDLED], names.c_str()) +
                             (first_unimplemented_.empty() ? "" : "; first value: " + first_unimplemented_[0]));
        }
        if (!o_.stats_only) {
            if (host_failures_)
                replay.push_back(strf("%" PRIu64 " host call(s) failed where the guest was answered success; first: ",
                                      host_failures_) + first_host_failures_[0]);
            if (readback_failures_) replay.push_back(strf("%" PRIu64 " readback(s) failed", readback_failures_));
            if (present_failures_) replay.push_back(strf("%" PRIu64 " host Present(s) failed", present_failures_));
            if (close_requests_ && stop_reason_ == "the window was closed")
                replay.push_back("the window was closed before the run finished");
            if (!o_.frames_out.empty() && y4m_frames_ != frame_)
                replay.push_back(strf("%u frame(s) written for %u Present(s)", y4m_frames_, frame_));
        }
    }
    // Sound, when asked for: the mixer's problems and a device that did not open.
    if (mixer_ && mixer_->problems())
        replay.push_back(strf("audio: %" PRIu64 " problem(s); first: ", mixer_->problems()) +
                         mixer_->first_problems()[0]);
    // Live play goes on silently when the machine has no playback device (a PC with no audio
    // output, a phone's audio refused): the report still names the failure (audio.failure). A
    // replay or evidence run asked for --audio still fails without one.
    if (o_.audio && !audio_failure_.empty() && !o_.interactive) replay.push_back("audio: " + audio_failure_);
    for (const auto& f : replay) failures_.push_back("replay: " + f);
    const bool expect_none = !o_.expect.empty() && expects_.empty() && opened_ && basic_failures_.empty();
    if (expect_fail_) failures_.push_back(strf("expect: %" PRIu64 " expectation(s) failed", expect_fail_));
    if (expect_none) failures_.push_back(strf("expect: %s selects no expectation for stage %d", o_.expect.c_str(), o_.stage));
    exit_status_ = !basic_failures_.empty()           ? kExitUsage
                 : (stream_problems_ || notes_fail)   ? kExitStream
                 : !replay.empty()                    ? kExitReplay
                 : (expect_fail_ || expect_none)      ? kExitExpect
                 : kExitPass;

    // Summary on stdout.
    std::printf("records: call %" PRIu64 ", d3dx %" PRIu64 ", frame %" PRIu64 ", note %" PRIu64 ", end %" PRIu64
                ", unknown %" PRIu64 "; seq gaps %" PRIu64 "; stream bytes %" PRIu64 "; stream sha256 %s%s\n",
                kinds_[SA2S_CALL], kinds_[SA2S_D3DX], kinds_[SA2S_FRAME], kinds_[SA2S_NOTE], kinds_[SA2S_END],
                unknown_kinds_, seq_gaps_, rd_.offset(), stream_sha256_.empty() ? "(not read)" : stream_sha256_.c_str(),
                hashed_after_stop_ ? " (the rest of the file hashed after the stop)" : "");
    if (rd_.version() == SA2S_VERSION_AUDIO || mixer_)
        std::printf("audio: %" PRIu64 " record(s)%s\n", audio_records_,
                    !mixer_ ? " (counted; no --audio or --audio-wav)"
                            : strf("; %" PRIu64 " samples from virtual %.6f s; %" PRIu64 " problem(s); device %s; wav %s",
                                   mixer_->samples(), double(mixer_->origin_us()) / 1e6, mixer_->problems(),
                                   !o_.audio ? "not asked" : audio_stream_ ? audio_driver_.c_str() : "failed",
                                   o_.audio_wav.empty() ? "not asked" : o_.audio_wav.c_str()).c_str());
    std::printf("entries: header table %s; Direct3DCreate9 %" PRIu64 " call(s), %zu IDirect3D9 object(s)%s\n",
                rd_.header().has_entries ? "read" : "absent (this player's copy of host/sa2_stream.h)",
                direct3d_create9_, d3d9_objects_.size(),
                create9_recorded_ ? "; every IDirect3D9 call held to them" : "");
    if (o_.stats_only)
        std::printf("stats only: presents %u (without a FRAME record %" PRIu64 "), draws %" PRIu64 ", wall %.2f s, "
                    "stop: %s\n", frame_, missing_frame_records_, draws_, wall, stop_reason_.c_str());
    else
        std::printf("stage %d: presents %u (without a FRAME record %" PRIu64 "), draws %" PRIu64 ", wall %.2f s, stop: %s\n",
                    o_.stage, frame_, missing_frame_records_, draws_, wall, stop_reason_.c_str());
    std::printf("calls: handled %" PRIu64 ", ignored by design %" PRIu64 ", skipped by stage %" PRIu64
                ", not handled %" PRIu64 "; errors %" PRIu64 "%s\n", by[C_HANDLED], by[C_IGNORED], by[C_STAGE],
                by[C_UNHANDLED], errors_, o_.stats_only ? " (a replay with no device)" : "");
    if (!o_.stats_only) {
        uint64_t allowed = 0;
        for (const auto& [rule, n] : allowed_) allowed += n;
        std::printf("host answers: failed where the guest succeeded %" PRIu64 ", failed like the guest %" PRIu64
                    ", allowed by a named rule %" PRIu64, host_failures_, failed_like_guest_, allowed);
        for (const auto& [rule, n] : allowed_) std::printf("; %s: %" PRIu64, rule.c_str(), n);
        std::printf("\n");
        std::printf("textures: dds %" PRIu64 ", png %" PRIu64 ", levels generated %" PRIu64 ", lock uploads %" PRIu64
                    ", readonly locks %" PRIu64 ", filter calls %" PRIu64 ", bytes uploaded %" PRIu64
                    ", placeholder binds %" PRIu64 ", releases kept %" PRIu64 ", SDL events discarded %" PRIu64 "\n",
                    file_textures_, png_textures_, levels_generated_, lock_uploads_, readonly_locks_, filter_calls_,
                    bytes_uploaded_, placeholders_bound_, releases_, events_discarded_);
        std::printf("window: presented while occluded %" PRIu64 ", hidden %" PRIu64 ", minimized %" PRIu64
                    ", with input focus %" PRIu64 ", with mouse focus %" PRIu64 "; input events discarded %" PRIu64
                    "; live input %s, idle waits %" PRIu64 " (%.3f s, %" PRIu64 " pumps), longest unpumped %.3f s\n",
                    frames_occluded_, frames_hidden_, frames_minimized_, frames_input_focus_, frames_mouse_focus_,
                    input_events_, rd_.live() ? "yes" : "no", rd_.idle_waits(), rd_.idle_s(), idle_pumps_,
                    longest_unpumped_);
        std::printf("recorder descriptions: implicit objects %s; file-texture DESC checked %" PRIu64 ", mismatched %" PRIu64
                    "; LOCK blobs used %" PRIu64 "\n", implicit_from_.c_str(), desc_checked_, desc_mismatch_,
                    lock_info_used_);
        std::printf("targets: %" PRIu64 " GetBackBuffer, GetRenderTarget and GetDepthStencilSurface answers held to the "
                    "bindings the player followed (GetBackBuffer %" PRIu64 ")\n", target_queries_, back_buffer_queries_);
    }
    // Per method: every category in a stats read (it is for counting), the
    // skipped and not handled ones in a replay.
    for (int c = o_.stats_only ? 0 : 2; c < C_COUNT; c++) {
        for (const auto& [k, m] : mstat_)
            if (m.by[c]) std::printf("  %s %s::%s %" PRIu64 "\n", kCatName[c], type_name(k >> 16),
                                     method_name(k >> 16, k & 0xFFFF), m.by[c]);
        for (const auto& [k, m] : xstat_)
            if (m.by[c]) std::printf("  %s %s %" PRIu64 "\n", kCatName[c], entry_label(k).c_str(), m.by[c]);
    }
    for (const auto& [k, m] : mstat_)
        if (m.failed) std::printf("  failed %s::%s %" PRIu64 " (first hr 0x%08x at seq %u)\n", type_name(k >> 16),
                                  method_name(k >> 16, k & 0xFFFF), m.failed, m.first_fail_hr, m.first_fail_seq);
    for (const auto& [k, m] : xstat_)
        if (m.failed) std::printf("  failed %s %" PRIu64 " (first hr 0x%08x at seq %u)\n", entry_label(k).c_str(),
                                  m.failed, m.first_fail_hr, m.first_fail_seq);
    for (const auto& a : first_allowed_) std::printf("  allowed %s\n", a.c_str());
    if (!expects_.empty() || !o_.expect.empty())
        std::printf("expectations: %zu selected, %" PRIu64 " pass, %" PRIu64 " fail\n", expects_.size(), expect_pass_,
                    expect_fail_);
    if (!o_.frames_out.empty())
        std::printf("frames out: %u frames, %" PRIu64 " y4m bytes, %u times rows, bgrx digest 0x%016" PRIx64 "\n",
                    y4m_frames_, y4m_bytes_, times_rows_, bgrx_digest_);
    const char* scope = o_.stats_only ? "; stats only: a stage 3 replay with no device, every check that needs none; "
                                        "device creation, host answers, readbacks and pixels are a replay's to check"
                                      : "";
    if (win_) {
        const SDL_WindowFlags f = SDL_GetWindowFlags(win_);
        window_end_ = strf("{\"hidden\": %s, \"occluded\": %s, \"minimized\": %s, \"input_focus\": %s}",
                           (f & SDL_WINDOW_HIDDEN) ? "true" : "false", (f & SDL_WINDOW_OCCLUDED) ? "true" : "false",
                           (f & SDL_WINDOW_MINIMIZED) ? "true" : "false", (f & SDL_WINDOW_INPUT_FOCUS) ? "true" : "false");
    }
    finalized_ = true;
    const bool complete = !o_.explicit_live() || (records_to_end_ && end_records_ && !stop_ && !stream_problems_);
    phase_ = complete ? "done" : "incomplete";
    if (!o_.stats_only) read_dxvk_log();
    if (!write_report(complete)) {
        const std::string why = "i/o: " + report_io_error_;
        if (std::find(failures_.begin(), failures_.end(), why) == failures_.end()) failures_.insert(failures_.begin(), why);
        exit_status_ = kExitUsage;
    }
    if (failures_.empty()) std::printf("verdict: PASS (exit 0%s)\n", scope);
    else {
        std::printf("verdict: FAIL (exit %d%s)\n", exit_status_, scope);
        for (const auto& f : failures_) std::printf("  failure: %s\n", f.c_str());
    }
    teardown();
    return exit_status_;
}

// DXVK's log for this run: DXVK_LOG_PATH/<exe>_d3d9.log (src/util/log/log.cpp:
// 124-139), "" when DXVK_LOG_PATH is unset or "none".
std::string Player::dxvk_log_path() const {
    const char* lp = getenv("DXVK_LOG_PATH");
    if (!lp || !*lp || std::strcmp(lp, "none") == 0) return "";
    auto it = files_.find("player");
    const std::string exe = it == files_.end() ? "sa2_player" : fs::path(it->second.real).filename().string();
    return std::string(lp) + "/" + exe + "_d3d9.log";
}

// DXVK's own lines about this run, from its log: its version, the driver it
// found, and the config file it read. The bytes read are pinned (count and
// sha256): the log was removed when the run started (check_outputs) and DXVK
// only appends to it, so tools/s5/play_report.py, which reads the options from
// the log on its own, holds the log it is given to this run's by them.
void Player::read_dxvk_log() {
    dxvk_log_ = dxvk_log_path();
    if (dxvk_log_.empty()) return;
    FILE* f = std::fopen(dxvk_log_.c_str(), "rb");
    if (!f) return;
    std::string text;
    char buf[1 << 16];
    for (size_t n; (n = std::fread(buf, 1, sizeof buf, f)) > 0;) text.append(buf, n);
    std::fclose(f);
    Sha256 sha;
    sha.update(text.data(), text.size());
    dxvk_log_bytes_ = text.size();
    dxvk_log_sha256_ = sha.hex();
    for (size_t pos = 0; pos < text.size();) {
        const size_t nl = text.find('\n', pos);
        std::string s = text.substr(pos, nl == std::string::npos ? std::string::npos : nl - pos);
        pos = nl == std::string::npos ? text.size() : nl + 1;
        while (!s.empty() && s.back() == '\r') s.pop_back();
        if (s.rfind("info:  DXVK: v", 0) == 0 && dxvk_version_.empty()) dxvk_version_ = s.substr(13);
        else if (s.rfind("info:  Found config file: ", 0) == 0) dxvk_config_seen_ = s.substr(26);
        else if (s.rfind("info:    Driver", 0) == 0 && dxvk_driver_.empty()) {
            const size_t c = s.find(": ", 15);          // past "info:    Driver"
            if (c != std::string::npos) dxvk_driver_ = s.substr(c + 2);
        }
    }
}

// Every host object released, then the device and the IDirect3D9, so DXVK's
// threads finish their work before the window goes. DXVK itself is never
// dlclosed (r3 section 3). Safe after a failure at any point of setup.
void Player::teardown() {
    close_pads();                                   // before the device the overlay drew with goes
    sa2_frametime::release();                       // host tweak frametime-overlay: its texture, before the device
    finish_input_diagnostics();
    if (end_fd_ >= 0) { ::close(end_fd_); end_fd_ = -1; }
    if (video_fd_ >= 0) { ::close(video_fd_); video_fd_ = -1; }
    if (cancel_fd_ >= 0) { ::close(cancel_fd_); cancel_fd_ = -1; }
    if (dev_) {
        for (auto& [a, t] : tex_) if (t.host) t.host->Release();
        for (auto& [a, t] : cubevol_) { if (t.cube) t.cube->Release(); if (t.vol) t.vol->Release(); }
        for (auto& [a, s] : surf_)
            if (s.host && s.kind != Surf::BACKBUFFER && s.kind != Surf::AUTODEPTH) s.host->Release();
        for (auto& [a, d] : decl_) d->Release();
        for (auto& [a, v] : vs_) v->Release();
        for (auto& [a, p] : ps_) p->Release();
        for (auto& [a, b] : buf_) { if (b.vb) b.vb->Release(); if (b.ib) b.ib->Release(); }
        if (white_) white_->Release();
        if (readback_) readback_->Release();
        if (ds_) ds_->Release();
        if (bb_) bb_->Release();
        const ULONG left = dev_->Release();
        if (left) std::fprintf(stderr, "note: the device still holds %lu references at teardown\n", (unsigned long)left);
        dev_ = nullptr;
    }
    if (d3d_) { d3d_->Release(); d3d_ = nullptr; }
    if (audio_stream_) { SDL_DestroyAudioStream(audio_stream_); audio_stream_ = nullptr; }
    if (win_) { SDL_DestroyWindow(win_); win_ = nullptr; }
    if (sdl_up_) { SDL_Quit(); sdl_up_ = false; }
}

// The report, written to a temporary file and renamed over the last one, so a
// process that dies mid-write leaves the previous report whole.
bool Player::write_report(bool complete) {
    if (o_.report.empty()) return true;
    if (!report_ok_) return false;
    auto env = [](const char* k) { const char* v = getenv(k); return std::string(v ? v : ""); };
    auto pin_json = [&](const char* key) {
        auto it = files_.find(key);
        if (it == files_.end()) return std::string("null");
        const Pin& p = it->second;
        return strf("{\"path\": %s, \"real_path\": %s, \"sha256\": %s}", jstr(p.path).c_str(), jstr(p.real).c_str(),
                    p.sha256.empty() ? "null" : jstr(p.sha256).c_str());
    };
    uint64_t by[C_COUNT] = {0, 0, 0, 0};
    for (const auto& [k, m] : mstat_) for (int c = 0; c < C_COUNT; c++) by[c] += m.by[c];
    for (const auto& [k, m] : xstat_) for (int c = 0; c < C_COUNT; c++) by[c] += m.by[c];
    std::string s = "{\n  \"tool\": \"host/player sa2_player\",\n";
    s += strf("  \"complete\": %s,\n  \"phase\": %s,\n", complete ? "true" : "false", jstr(phase_).c_str());
    if (complete || finalized_)
        s += strf("  \"pass\": %s,\n  \"exit_status\": %d,\n", failures_.empty() ? "true" : "false", exit_status_);
    else
        s += "  \"pass\": false,\n  \"exit_status\": null,\n";
    s += "  \"failures\": " + ((complete || finalized_) ? jlist(failures_) : jlist({"the player had not finished: phase " + phase_})) + ",\n";
    s += strf("  \"lifecycle\": {\"explicit_live\": %s, \"interactive\": %s, \"finalized\": %s, "
              "\"reader_status\": %s, \"reader_detail\": %s, \"service_calls\": %" PRIu64 ", "
              "\"last_accepted_seq\": %s, \"windows_created\": %u, \"window_reused\": %s, "
              "\"guest_shutdown_delivered\": false, \"ready_marker_emitted\": false},\n",
              o_.explicit_live() ? "true" : "false", o_.interactive ? "true" : "false", finalized_ ? "true" : "false",
              jstr(live_status_).c_str(), jstr(live_detail_).c_str(), service_calls_,
              any_accepted_seq_ ? std::to_string(last_accepted_seq_).c_str() : "null", windows_created_,
              window_reused_ ? "true" : "false");
    if (input_) s += input_->report_json();
    else s += final_host_input_;
    s += diagnostic_json();
    s += end_request_json();
    s += pads_json();
    // sa2_player_gpu expects no DXVK file: both expectations null, both verifications false.
#if SA2_PLAYER_GPU
    const std::string expected_dxvk = "null", expected_config = "null";
#else
    const std::string expected_dxvk = jstr(sa2_host_expected::dxvk_sha256);
    const std::string expected_config = jstr(sa2_host_expected::dxvk_config_sha256);
#endif
    s += strf("  \"host_identity\": {\"sdl_file_verified\": %s, \"dxvk_file_verified\": %s, "
              "\"config_file_verified\": %s, \"sdl_version\": %d, \"sdl_revision\": %s, "
              "\"expected_sdl_sha256\": %s, \"expected_dxvk_sha256\": %s, \"expected_config_sha256\": %s, "
              "\"d3d9_host\": \"%s\"},\n",
              sdl_identity_ok_ ? "true" : "false", dxvk_identity_ok_ ? "true" : "false",
              config_identity_ok_ ? "true" : "false", sdl_version_, jstr(sdl_revision_).c_str(),
              jstr(sa2_host_expected::sdl_sha256).c_str(), expected_dxvk.c_str(), expected_config.c_str(), kD3d9Host);
    s += "  \"stream\": " + jstr(o_.stream) + ",\n";
    s += strf("  \"stream_version\": %u,\n  \"stream_schema\": %s,\n  \"stream_writer\": %s,\n", rd_.version(),
              jstr(rd_.header().schema).c_str(), jstr(rd_.header().writer).c_str());
    // The stream's sha256 is set when the report is final: of the bytes the
    // records were read from, or (after_stop) with the rest of the file hashed
    // after the run stopped short of its end.
    s += "  \"stream_sha256\": " + (stream_sha256_.empty() ? std::string("null") : jstr(stream_sha256_)) + ",\n";
    s += strf("  \"stream_records_read_to_end\": %s,\n  \"stream_sha256_after_stop\": %s,\n"
              "  \"stream_file_bytes_read\": %" PRIu64 ",\n",
              records_to_end_ ? "true" : "false", hashed_after_stop_ ? "true" : "false", rd_.file_bytes_read());
    s += "  \"stream_header\": " + jstr(rd_.header_json()) + ",\n";
    s += strf("  \"stream_entries_table\": %s,\n", rd_.header().has_entries ? "true" : "false");
    s += strf("  \"stream_bytes\": %" PRIu64 ",\n  \"stage\": %d,\n", rd_.offset(), o_.stage);
    s += strf("  \"options\": {\"pace\": %s, \"speed\": %g, \"vsync\": %s, \"hidden\": %s, \"max_frames\": %u, "
              "\"allow_truncated\": %s, \"allow_notes\": %s, \"stats_only\": %s, \"report_every\": %u%s},\n",
              o_.pace ? "true" : "false", o_.speed, o_.vsync ? "true" : "false", o_.hidden ? "true" : "false",
              o_.max_frames, o_.allow_truncated ? "true" : "false", o_.allow_notes ? "true" : "false",
              o_.stats_only ? "true" : "false", o_.report_every,
              o_.sound() ? strf(", \"audio\": %s, \"audio_wav\": %s", o_.audio ? "true" : "false",
                                o_.audio_wav.empty() ? "null" : jstr(o_.audio_wav).c_str()).c_str() : "");
    if (o_.stop_draw_frame) {
        const bool usable = checkpoint_reached_ && checkpoint_pixels_ && !o_.stats_only &&
            checkpoint_cat_ == C_HANDLED && !FAILED(HRESULT(checkpoint_hr_)) &&
            !errors_ && !stream_problems_ && !unimplemented_ && !host_failures_ && exit_status_ == kExitPass;
        s += strf("  \"draw_checkpoint\": {\"scope\": \"partial-original-draw\", \"target_frame\": %u, "
                  "\"target_draw\": %u, \"reached\": %s, \"source_only\": %s, \"usable\": %s, "
                  "\"original_slot\": %s, \"original_seq\": %s, \"category\": %s, \"guest_hr\": %s, "
                  "\"dispatch_hr\": %s, \"readback\": %s, \"readback_hr\": %s, \"bgrx_hash\": %s},\n",
                  o_.stop_draw_frame, o_.stop_draw_ordinal, checkpoint_reached_ ? "true" : "false",
                  o_.stats_only ? "true" : "false", usable ? "true" : "false",
                  checkpoint_reached_ ? std::to_string(checkpoint_slot_).c_str() : "null",
                  checkpoint_reached_ ? std::to_string(checkpoint_seq_).c_str() : "null",
                  checkpoint_reached_ ? jstr(kCatName[checkpoint_cat_]).c_str() : "null",
                  checkpoint_reached_ ? strf("\"0x%08x\"", checkpoint_guest_hr_).c_str() : "null",
                  checkpoint_reached_ ? strf("\"0x%08x\"", checkpoint_hr_).c_str() : "null",
                  host() && checkpoint_reached_ ? (checkpoint_pixels_ ? "true" : "false") : "null",
                  host() && checkpoint_reached_ ? strf("\"0x%08x\"", checkpoint_readback_hr_).c_str() : "null",
                  checkpoint_pixels_ ? strf("\"0x%016" PRIx64 "\"", checkpoint_hash_).c_str() : "null");
    }
    if (o_.pace)
        s += "  \"pace\": " + (pace_ ? pace_->json() : std::string("null")) + ",\n  \"pace_log\": " +
             (o_.pace_log.empty() ? std::string("null") : jstr(o_.pace_log)) + ",\n";
    if (o_.stats_only)
        s += "  \"stats_only_leaves_out\": [\"device creation: the window, DXVK and CreateDevice\", "
             "\"host answers: a host call that fails, and the objects a failed one leaves unmade\", "
             "\"readbacks and pixels\"],\n";
    s += strf("  \"records\": {\"call\": %" PRIu64 ", \"d3dx\": %" PRIu64 ", \"frame\": %" PRIu64 ", \"note\": %" PRIu64
              ", \"end\": %" PRIu64 ", \"unknown\": %" PRIu64 ", \"seq_gaps\": %" PRIu64 ", \"after_end\": %" PRIu64 "%s},\n",
              kinds_[SA2S_CALL], kinds_[SA2S_D3DX], kinds_[SA2S_FRAME], kinds_[SA2S_NOTE], kinds_[SA2S_END],
              unknown_kinds_, seq_gaps_, after_end_,
              rd_.version() == SA2S_VERSION_AUDIO ? strf(", \"audio\": %" PRIu64, kinds_[SA2S_AUDIO]).c_str() : "");
    s += strf("  \"stream_problems\": {\"count\": %" PRIu64 ", \"first\": %s},\n", stream_problems_,
              jlist(first_stream_problems_).c_str());
    s += strf("  \"presents\": %u,\n  \"presents_without_frame_record\": %" PRIu64 ",\n  \"frame_records\": %" PRIu64
              ",\n  \"draws\": %" PRIu64 ",\n  \"wall_s\": %.3f,\n  \"stop\": %s,\n",
              frame_, missing_frame_records_, frame_records_, draws_, now_s() - t_start_, jstr(stop_reason_).c_str());
    s += strf("  \"errors\": %" PRIu64 ",\n  \"first_errors\": %s,\n", errors_, jlist(first_errors_).c_str());
    s += strf("  \"calls\": {\"handled\": %" PRIu64 ", \"ignored_by_design\": %" PRIu64 ", \"skipped_by_stage\": %" PRIu64
              ", \"not_handled\": %" PRIu64 "},\n", by[C_HANDLED], by[C_IGNORED], by[C_STAGE], by[C_UNHANDLED]);
    s += strf("  \"not_handled_values\": {\"count\": %" PRIu64 ", \"first\": %s},\n", unimplemented_,
              jlist(first_unimplemented_).c_str());
    s += strf("  \"host_failures\": {\"count\": %" PRIu64 ", \"first\": %s, \"failed_like_guest\": %" PRIu64 "},\n",
              host_failures_, jlist(first_host_failures_).c_str(), failed_like_guest_);
    s += "  \"allowed_host_failures\": {\"rules\": {";
    bool first = true;
    for (const auto& [rule, n] : allowed_) { s += strf("%s%s: %" PRIu64, first ? "" : ", ", jstr(rule).c_str(), n); first = false; }
    s += "}, \"calls\": " + jlist(first_allowed_) + "},\n";
    s += strf("  \"readback_failures\": %" PRIu64 ",\n  \"present_failures\": %" PRIu64 ",\n", readback_failures_,
              present_failures_);
    s += strf("  \"notes\": {\"count\": %" PRIu64 ", \"allowed\": %s, \"first\": ", notes_,
              o_.allow_notes ? "true" : "false") + jlist(first_notes_) + "},\n";
    {
        std::vector<std::string> objs;
        for (uint32_t a : d3d9_objects_) objs.push_back(strf("0x%08x", a));
        s += strf("  \"direct3d_create9\": {\"calls\": %" PRIu64 ", \"direct3d9_calls_held\": %s, \"objects\": ",
                  direct3d_create9_, create9_recorded_ ? "true" : "false") + jlist(objs) + "},\n";
    }
    // Every method and entry the stream called, with what the player made of
    // it; "failed" counts the host answers that failed (count(), do_present),
    // so the rows' sum is host_failures + failed_like_guest + the allowed
    // calls + present_failures, which tools/s5/play_report.py holds.
    auto stat_fields = [&](const MStat& m) {
        std::string o;
        for (int c = 0; c < C_COUNT; c++) if (m.by[c]) o += strf(", \"%s\": %" PRIu64, kCatName[c], m.by[c]);
        if (m.failed) o += strf(", \"failed\": %" PRIu64 ", \"first_fail_hr\": \"0x%08x\", \"first_fail_seq\": %u", m.failed,
                                m.first_fail_hr, m.first_fail_seq);
        if (m.hr_differs) o += strf(", \"hr_differs_from_guest\": %" PRIu64 ", \"first_host_hr\": \"0x%08x\", \"guest_eax\": \"0x%08x\"",
                                    m.hr_differs, m.first_differs_hr, m.first_differs_eax);
        return o;
    };
    s += "  \"methods\": [\n";
    first = true;
    for (const auto& [k, m] : mstat_) {
        s += strf("%s    {\"type\": \"%s\", \"method\": \"%s\", \"slot\": %u", first ? "" : ",\n", type_name(k >> 16),
                  method_name(k >> 16, k & 0xFFFF), k & 0xFFFF);
        s += stat_fields(m) + "}";
        first = false;
    }
    for (const auto& [k, m] : xstat_) {
        const EntryInfo* e = entry_info(k);
        const Sa2EntryRow* row = rd_.entry_row(k);
        const std::string name = e ? e->name : row ? row->name : strf("entry %u", k);
        s += strf("%s    {\"type\": %s, \"method\": %s, \"entry\": %u", first ? "" : ",\n", jstr(e ? e->dll : "?").c_str(),
                  jstr(name).c_str(), k);
        s += stat_fields(m) + "}";
        first = false;
    }
    s += strf("\n  ],\n  \"textures\": {\"dds\": %" PRIu64 ", \"png\": %" PRIu64 ", \"levels_generated\": %" PRIu64
              ", \"lock_uploads\": %" PRIu64 ", \"readonly_locks\": %" PRIu64 ", \"filter_calls\": %" PRIu64
              ", \"bytes_uploaded\": %" PRIu64 ", \"placeholder_binds\": %" PRIu64 ", \"host_created\": {",
              file_textures_, png_textures_, levels_generated_, lock_uploads_, readonly_locks_, filter_calls_,
              bytes_uploaded_, placeholders_bound_);
    first = true;
    for (const auto& [src, n] : tex_by_source_) { s += strf("%s%s: %" PRIu64, first ? "" : ", ", jstr(src).c_str(), n); first = false; }
    s += strf("}},\n  \"releases_kept\": %" PRIu64 ",\n", releases_);
    s += strf("  \"t_start_monotonic_s\": %.6f,\n  \"live_input\": %s,\n", t_start_,
              rd_.live() ? "true" : "false");
    s += strf("  \"window\": {\"input_events\": %" PRIu64 ", \"frames_with_flags\": {\"occluded\": %" PRIu64
              ", \"hidden\": %" PRIu64 ", \"minimized\": %" PRIu64 ", \"input_focus\": %" PRIu64
              ", \"mouse_focus\": %" PRIu64 "}, \"idle\": {\"waits\": %" PRIu64 ", \"slices\": %" PRIu64
              ", \"pumps\": %" PRIu64 ", \"wait_s\": %.6f, \"longest_unpumped_s\": %.6f}, \"log\": %s, ",
              input_events_, frames_occluded_, frames_hidden_, frames_minimized_, frames_input_focus_,
              frames_mouse_focus_, rd_.idle_waits(), rd_.idle_slices(), idle_pumps_, rd_.idle_s(), longest_unpumped_,
              jlist(window_log_).c_str());
    s += strf("\"flag_bits\": {\"occluded\": %" PRIu64 ", \"hidden\": %" PRIu64 ", \"minimized\": %" PRIu64
              ", \"input_focus\": %" PRIu64 ", \"mouse_focus\": %" PRIu64 "}, \"flags_at_present\": [",
              uint64_t(SDL_WINDOW_OCCLUDED), uint64_t(SDL_WINDOW_HIDDEN), uint64_t(SDL_WINDOW_MINIMIZED),
              uint64_t(SDL_WINDOW_INPUT_FOCUS), uint64_t(SDL_WINDOW_MOUSE_FOCUS));
    for (size_t i = 0; i < flag_runs_.size(); i++)
        s += strf("%s[%u, %u, %" PRIu64 "]", i ? ", " : "", flag_runs_[i].first, flag_runs_[i].last,
                  flag_runs_[i].flags);
    s += "]},\n";
    s += strf("  \"events\": {\"discarded\": %" PRIu64 ", \"close_requests\": %" PRIu64 ", \"by_kind\": {",
              events_discarded_, close_requests_);
    first = true;
    for (const auto& [k, n] : events_by_kind_) { s += strf("%s%s: %" PRIu64, first ? "" : ", ", jstr(k).c_str(), n); first = false; }
    s += "}},\n";
    s += strf("  \"recorder_descriptions\": {\"implicit_objects\": %s, \"file_texture_desc_checked\": %" PRIu64
              ", \"file_texture_desc_mismatch\": %" PRIu64 ", \"lock_info_used\": %" PRIu64 "},\n",
              jstr(implicit_from_).c_str(), desc_checked_, desc_mismatch_, lock_info_used_);
    s += strf("  \"targets\": {\"queries_held_to_bindings\": %" PRIu64 ", \"get_back_buffer\": %" PRIu64
              ", \"bound_render_target_0\": \"0x%08x\", \"bound_depth_stencil\": \"0x%08x\"},\n", target_queries_,
              back_buffer_queries_, bound_rt_[0], bound_ds_);
    s += strf("  \"expectations\": {\"file\": %s, \"selected\": %zu, \"pass\": %" PRIu64 ", \"fail\": %" PRIu64 "},\n",
              o_.expect.empty() ? "null" : jstr(o_.expect).c_str(), expects_.size(), expect_pass_, expect_fail_);
    if (o_.sound()) s += "  \"audio\": " + audio_json() + ",\n";
    if (o_.frames_out.empty()) s += "  \"frames_out\": null,\n";
    else
        s += strf("  \"frames_out\": {\"path\": %s, \"times\": %s, \"frames\": %u, \"y4m_bytes\": %" PRIu64
                  ", \"times_rows\": %u, \"bgrx_digest\": \"0x%016" PRIx64 "\"},\n", jstr(o_.frames_out).c_str(),
                  jstr(o_.frames_out + ".times.tsv").c_str(), y4m_frames_, y4m_bytes_, times_rows_, bgrx_digest_);
    s += strf("  \"device\": {\"created\": %s, \"failure\": %s, \"back_buffer\": [%u, %u, %u], \"auto_depth\": %u, "
              "\"interval\": \"%s\", \"behavior\": \"0x%08x\", \"guest_behavior\": \"0x%08x\", "
              "\"guest_multisample\": %u, \"x87_cw_before\": \"%04x\", \"x87_cw_after\": \"%04x\", "
              "\"guest_device\": \"0x%08x\", \"guest_back_buffer\": \"0x%08x\", \"guest_depth\": \"0x%08x\"},\n",
              dev_ ? "true" : "false", device_failure_.empty() ? "null" : jstr(device_failure_).c_str(), bb_w_, bb_h_,
              bb_fmt_, guest_pp_[10], o_.vsync ? "ONE" : "IMMEDIATE", host_behavior_, guest_behavior_, guest_pp_[4],
              cw_before_, cw_after_, dev_addr_, rt_addr_, ds_addr_);
    s += strf("  \"adapter\": {\"description\": %s, \"driver\": %s},\n", jstr(adapter_).c_str(), jstr(adapter_driver_).c_str());
    s += strf("  \"dxvk\": {\"log\": %s, \"log_bytes_read\": %" PRIu64 ", \"log_sha256\": %s, \"version\": %s, "
              "\"driver\": %s, \"config_file\": %s},\n",
              jstr(dxvk_log_).c_str(), dxvk_log_bytes_,
              dxvk_log_sha256_.empty() ? "null" : jstr(dxvk_log_sha256_).c_str(), jstr(dxvk_version_).c_str(),
              jstr(dxvk_driver_).c_str(), jstr(dxvk_config_seen_).c_str());
    s += "  \"files\": {\"player\": " + pin_json("player") + ", \"libdxvk_d3d9\": " + pin_json("libdxvk_d3d9") +
         ", \"libSDL3\": " + pin_json("libSDL3") + ", \"dxvk_conf\": " + pin_json("dxvk_conf") + "},\n";
    // Whether this ran on a live desktop session is inferred, not measured:
    // SDL chose a display server's video driver, and XDG_SESSION_TYPE names a
    // graphical login session. A nested or virtual X server started inside such
    // a session would read the same, and the basis says so. Frames are read
    // back before Present, at IMMEDIATE unless --vsync, and DXVK's SDL3 WSI
    // reports no occlusion (wsi_window_sdl3.cpp:163-165), so neither focus nor
    // occlusion can change a frame; under --vsync or --pace, FIFO presentation
    // may wait on the compositor, which moves wall time only (r3 9.1, not
    // measured here).
    const std::string session = env("XDG_SESSION_TYPE");
    const bool live = (sdl_driver_ == "wayland" || sdl_driver_ == "x11") && (session == "wayland" || session == "x11");
    s += strf("  \"host\": {\"sdl_video_driver\": %s, \"live_desktop_session\": %s, \"live_desktop_session_basis\": "
              "\"inferred, not measured: SDL's video driver is a display server's (wayland or x11) and XDG_SESSION_TYPE "
              "names a graphical session; an X server nested in such a session would read the same\", "
              "\"XDG_SESSION_TYPE\": %s, \"WAYLAND_DISPLAY\": %s, \"DISPLAY\": %s, \"window_end\": %s, "
              "\"focus_or_occlusion\": {\"frames\": "
              "\"%s\", "
              "\"wall_timing\": \"%s\"}},\n",
              jstr(sdl_driver_).c_str(), live ? "true" : "false", jstr(session).c_str(),
              jstr(env("WAYLAND_DISPLAY")).c_str(), jstr(env("DISPLAY")).c_str(), window_end_.c_str(),
              kOcclusionFrames,
              (o_.vsync || o_.pace) ? "may move with the compositor under FIFO (--vsync) or --pace; not measured"
                                    : kUnpacedTiming);
    s += "  \"env\": {";
    const char* keys[] = {"DXVK_WSI_DRIVER", "DXVK_CONFIG_FILE", "DXVK_CONFIG", "DXVK_LOG_PATH", "DXVK_LOG_LEVEL",
                          "DXVK_SHADER_CACHE", "DXVK_SHADER_CACHE_PATH", "DXVK_HUD", "DXVK_FILTER_DEVICE_NAME",
                          "DXVK_FILTER_DEVICE_UUID", "DXVK_FORCE_WINDOWED", "SDL_NO_SIGNAL_HANDLERS", "SDL_VIDEO_DRIVER",
                          "VK_LOADER_LAYERS_DISABLE", "VK_LOADER_LAYERS_ENABLE", "VK_INSTANCE_LAYERS", "VK_DRIVER_FILES",
                          "VK_ICD_FILENAMES", "SA2_DXVK_D3D9", "SA2_PLAY_OUT"};
    for (size_t i = 0; i < sizeof keys / sizeof keys[0]; i++)
        s += strf("%s\"%s\": %s", i ? ", " : "", keys[i], jstr(env(keys[i])).c_str());
    s += "}\n}\n";
    std::string error;
    if (!publish_report_file(o_.report, s, &error)) return report_failure(error);
    return true;
}

bool parse_draw_checkpoint(const char* text, uint32_t* frame, uint32_t* draw) {
    const char* p = text;
    auto positive = [&](uint32_t* out) {
        if (*p < '1' || *p > '9') return false;
        uint32_t value = 0;
        while (*p >= '0' && *p <= '9') {
            const uint32_t digit = uint32_t(*p++ - '0');
            if (value > (UINT32_MAX - digit) / 10) return false;
            value = value * 10 + digit;
        }
        *out = value;
        return true;
    };
    return positive(frame) && *p++ == ':' && positive(draw) && !*p;
}

bool parse_list(const char* s, std::set<uint32_t>* out) {
    for (const char* p = s; *p;) {
        char* end = nullptr;
        const unsigned long v = std::strtoul(p, &end, 10);
        if (end == p) return false;
        out->insert(uint32_t(v));
        p = *end == ',' ? end + 1 : end;
        if (*end && *end != ',') return false;
    }
    return true;
}

}  // namespace

int main(int argc, char** argv) {
#ifdef _WIN32
    // Every file this process writes (the times file, the reports, the logs, d3d9gpu's report)
    // has Linux's bytes: no CRLF translation in a file opened without "b".
    _set_fmode(_O_BINARY);
    // The system timer at 1 ms for this process (winmm's timeBeginPeriod, found at run time, so
    // the link gains no import): Sleep and the C library's nanosleep then wait about what they
    // are asked, where Windows' default tick is 15.6 ms, as long as --pace's slices and a live
    // stream's waits (stream.cpp).
    if (HMODULE mm = LoadLibraryA("winmm.dll"))
        if (void* begin = reinterpret_cast<void*>(GetProcAddress(mm, "timeBeginPeriod")))
            reinterpret_cast<UINT(WINAPI*)(UINT)>(begin)(1);
#endif
#if SA2_PLAYER_GPU
    // The clear matrix through this binary's own Direct3DCreate9 (the file the second
    // host's runs pin as files.player): host/d3d9gpu/test/clear_matrix.cpp.
    if (argc > 1 && std::strcmp(argv[1], "--clear-matrix") == 0) return sa2_clear_matrix_main(argc - 1, argv + 1);
#endif
    Options o;
    bool max_frames_given = false;
    std::string max_frames_text;
    for (int i = 1; i < argc; i++) {
        const std::string a = argv[i];
        auto val = [&]() -> const char* {
            if (i + 1 >= argc) { std::fprintf(stderr, "%s needs a value\n", a.c_str()); std::exit(kExitUsage); }
            return argv[++i];
        };
        auto fd_value = [&]() -> int {
            const char* text = val();
            char* end = nullptr;
            errno = 0;
            const long fd = std::strtol(text, &end, 10);
            if (errno || end == text || *end || fd < 3 || fd > INT_MAX) {
                std::fprintf(stderr, "%s needs an owned descriptor >= 3\n", a.c_str());
                std::exit(kExitUsage);
            }
            return int(fd);
        };
        if (a == "--interactive") o.interactive = true;
        else if (a == "--video-fd") o.video_fd = fd_value();
        else if (a == "--cancel-fd") o.cancel_fd = fd_value();
        else if (a == "--end-fd") o.end_fd = fd_value();
#ifdef _WIN32
        // The Windows interactive mode's channels: the session's named pipes, by name (above).
        else if (a == "--video-pipe" || a == "--cancel-pipe" || a == "--end-pipe") {
            const char* name = val();
            std::string why;
            const int fd = open_session_pipe(name, a == "--end-pipe", &why);
            if (fd < 0) { std::fprintf(stderr, "%s: %s\n", a.c_str(), why.c_str()); return kExitUsage; }
            (a == "--video-pipe" ? o.video_fd : a == "--cancel-pipe" ? o.cancel_fd : o.end_fd) = fd;
        }
#endif
        else if (a == "--stage") o.stage = std::atoi(val());
        else if (a == "--frames-out") o.frames_out = val();
        else if (a == "--snap") { if (!parse_list(val(), &o.snaps)) { std::fprintf(stderr, "bad --snap\n"); return kExitUsage; } }
        else if (a == "--snap-dir") o.snap_dir = val();
        else if (a == "--pixel") {
            Options::Pixel p{};
            if (std::sscanf(val(), "%u:%u:%u", &p.frame, &p.x, &p.y) != 3) { std::fprintf(stderr, "bad --pixel\n"); return kExitUsage; }
            o.pixels.push_back(p);
        }
        else if (a == "--expect") o.expect = val();
        else if (a == "--dump-draws") { if (!parse_list(val(), &o.dump_draws)) return kExitUsage; }
        else if (a == "--pace") o.pace = true;
        else if (a == "--pace-log") o.pace_log = val();
        else if (a == "--speed") o.speed = std::atof(val());
        else if (a == "--vsync") o.vsync = true;
        else if (a == "--hidden") o.hidden = true;
        else if (a == "--max-frames") { max_frames_given = true; max_frames_text = val(); o.max_frames = uint32_t(std::strtoul(max_frames_text.c_str(), nullptr, 10)); }
        else if (a == "--stop-after-draw") {
            if (o.stop_draw_frame || !parse_draw_checkpoint(val(), &o.stop_draw_frame, &o.stop_draw_ordinal)) {
                std::fprintf(stderr, "--stop-after-draw needs F:N, positive canonical decimal uint32 values\n");
                return kExitUsage;
            }
        }
        else if (a == "--report") o.report = val();
        else if (a == "--report-every") o.report_every = uint32_t(std::strtoul(val(), nullptr, 10));
        else if (a == "--stats-only") o.stats_only = true;
        else if (a == "--allow-truncated") o.allow_truncated = true;
        else if (a == "--allow-notes") o.allow_notes = true;
        else if (a == "--dxvk") o.dxvk = val();
        else if (a == "--input-path") o.input_path = val();
        else if (a == "--key-log") o.key_log = val();
        else if (a == "--present-log") o.present_log = val();
        else if (a == "--synthetic-keys") o.synthetic_keys = val();
        else if (a == "--synthetic-input") o.synthetic_input = val();
        else if (a == "--overlay-out") o.overlay_out = val();
        else if (a == "--audio") o.audio = true;
        else if (a == "--audio-wav") o.audio_wav = val();
        else if (!a.empty() && a[0] == '-' && a != "-") { std::fprintf(stderr, "unknown option %s\n", a.c_str()); return kExitUsage; }
        else o.stream = a;
    }
    if (o.stop_draw_frame && (o.explicit_live() || o.interactive || o.stage != 3 || max_frames_given || o.allow_truncated)) {
        std::fprintf(stderr, "--stop-after-draw is an offline stage 3 partial diagnostic; no live, max-frames or allow-truncated\n");
        return kExitUsage;
    }
    if (!synthetic_configuration_valid(o, max_frames_given, max_frames_text)) {
        std::fprintf(stderr, "--synthetic-keys needs interactive visible live input, both logs and a positive canonical uint32 --max-frames\n");
        return kExitUsage;
    }
    if (o.explicit_live() && o.stream.empty()) o.stream = "-";
    if ((o.video_fd >= 0) != (o.cancel_fd >= 0) ||
        (o.explicit_live() && (o.video_fd == o.cancel_fd || o.stream != "-" || o.allow_truncated || (o.max_frames && o.synthetic_keys.empty()))) ||
        (o.interactive && (!o.explicit_live() || o.stats_only || o.hidden))) {
        std::fprintf(stderr, "live descriptors must be distinct, transferred together with STREAM -, without truncation/early stop; "
                             "interactive needs live descriptors and visible video\n");
        return kExitUsage;
    }
#if SA2_PLAYER_GPU
    if (!o.dxvk.empty()) {
        std::fprintf(stderr, "--dxvk names DXVK's library: sa2_player_gpu loads none\n");
        return kExitUsage;
    }
#endif
    if (!o.input_path.empty() && !o.interactive) {
        std::fprintf(stderr, "--input-path sends the window's keyboard to the game: it needs --interactive\n");
        return kExitUsage;
    }
    if ((!o.synthetic_input.empty() || !o.overlay_out.empty()) && o.input_path.empty()) {
        std::fprintf(stderr, "--synthetic-input and --overlay-out drive and show the pads the game is sent: they need "
                             "--interactive --input-path\n");
        return kExitUsage;
    }
    if (o.end_fd >= 0 &&(!o.interactive || o.end_fd == o.video_fd || o.end_fd == o.cancel_fd)) {
        std::fprintf(stderr, "--end-fd asks the caller to end an interactive session: it needs --interactive and "
                             "a descriptor of its own\n");
        return kExitUsage;
    }
    if (o.stream.empty() || o.stage < 0 || o.stage > 3 || o.speed <= 0) {
        std::fprintf(stderr, "usage: %s STREAM [--stage 0..3] [--frames-out F.y4m] [--snap N,..] [--snap-dir D]"
                     " [--pixel N:X:Y] [--expect F.tsv] [--dump-draws N] [--pace [--speed S] [--pace-log F.tsv]] [--vsync] [--hidden]"
                     " [--max-frames N | --stop-after-draw F:N] [--report F.json] [--report-every N] [--stats-only] [--allow-truncated]"
                     " [--allow-notes]%s [--interactive --video-fd N --cancel-fd N [--input-path FIFO] [--end-fd N]]"
                     " [--key-log F.tsv] [--present-log F.tsv] [--synthetic-keys F.tsv] [--synthetic-input F.tsv]"
                     " [--overlay-out DIR] [--audio] [--audio-wav F.wav]\n",
                     argv[0], SA2_PLAYER_GPU ? "" : " [--dxvk PATH]");
#if SA2_PLAYER_GPU
        std::fprintf(stderr, "       %s --clear-matrix [--hidden]\n", argv[0]);
#endif
#ifdef _WIN32
        std::fprintf(stderr, "       on Windows, --interactive's channels are named pipes: --video-pipe NAME"
                             " --cancel-pipe NAME [--end-pipe NAME] in place of the three descriptors\n");
#endif
        return kExitUsage;
    }
    // The default snapshot directory. Without SA2_PLAY_OUT there is none, and
    // the run refuses --snap (check_outputs), with a report saying so.
    if (!o.snaps.empty() && o.snap_dir.empty())
        if (const char* out = getenv("SA2_PLAY_OUT"); out && *out) o.snap_dir = std::string(out) + "/snaps";
    // Host tweaks (tweaks.h): SA2_TWEAKS, read once, in a play session alone: --interactive with
    // --input-path, a person's window driving the game (the launcher's play, the Android
    // session's), with no synthetic input and no replay. A stream file's replay, the launcher's
    // selftest (no --input-path) and the synthetic tests have every host tweak off.
    const char* not_play = sa2tw::not_play(o.interactive, !o.input_path.empty(),
                                           !o.synthetic_keys.empty() || !o.synthetic_input.empty());
    const sa2tw::Reading tweaks = sa2tw::read(getenv("SA2_TWEAKS"), sa2tw::off(not_play), "player");
    sa2tw::g_on = tweaks.on;
    if (!tweaks.line.empty()) std::fprintf(stderr, "%s\n", tweaks.line.c_str());
    sa2tw::tex_filter::start();
    o.vsync = sa2tw::vsync(o.vsync);   // the vsync tweak: --vsync in play (tweaks/vsync.cpp)
#ifndef _WIN32
    if (o.end_fd >= 0) std::signal(SIGUSR1, on_end_signal);   // the caller's end request
#else
    if (o.end_fd >= 0) watch_end_event();   // the caller's end request: a named event (above)
#endif
    Player p(o);
    return p.run();
}
