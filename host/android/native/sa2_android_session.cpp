// sa2_android_session.cpp: the Android app's native session (com.sa2recomp.app). SDL's Java
// (SDLActivity, subclassed by host/android/java's Sa2Activity) loads libmain.so into the app's
// process and calls sa2_android_main on its SDL thread. What follows is host/launch's play path
// (sa2_launch.cpp, steps 2 to 7) for the app:
//
//  - The installation: the installer's config (host/install/sa2_config.h) under the app's private
//    files folder: HOME is that folder, $XDG_CONFIG_HOME <files>/config and $XDG_DATA_HOME
//    <files>/data, so the installer (lib/arm64-v8a/libsa2install.so, run by Sa2SetupActivity's
//    first-run screen with the same environment) and this session read and write the same files.
//    The game folder is the one the user copied into the app's external files folder; the
//    installer verified it against the install table before this session can start.
//  - The session: <files>/sessions/<stamp>, the game's working folder, with the two FIFOs (the
//    game's stream and the keyboard and pads), the logs and the records. The three newest
//    sessions are kept.
//  - The game: <native library dir>/libsa2game.so (contract D: an executable packaged as a
//    library so the package manager extracts it where the app may exec it), with the APK's
//    assets/game.env and the session's settings, exactly the launcher's (SA2_INSTALL,
//    SA2_VFS_WRITE_ROOT, SA2_EXE_KIND=shipped, SA2_REC_TRACE, SA2_REC_META, SA2_REC_COUNTS,
//    SA2_INPUT_DI_SOURCE, SA2_INPUT_DI_LOG, SA2_LAUNCH_TICKS), in a process group of its own.
//    The app's settings screen (Sa2SettingsActivity) sends its settings as --setting SA2_K=V
//    arguments: they come after the session's defaults below and replace them. A measurement may
//    add SA2_ settings in <external files>/game.env.extra (adb can push it): they come after
//    game.env, the session's defaults and the app's settings, and replace those, never the
//    session's own (the installation, the session's files and the stream's settings, set last).
//    So game.env.extra wins over the settings screen. The player's settings among them
//    (SA2_TOUCH_*, SA2_PAD_MAP, SA2_PLAYER_PICTURE_Y, SA2_PLAYER_PROF*) go to the player, in this
//    process's environment, and not to the game; SA2_TWEAKS goes to both.
//  - The relay (host/launch/sa2_relay.h) hands the stream on to the player, paced (lead 1) through
//    the pace pipe, whose read end the game gets as descriptor 43 (SA2_REC_PACE_FD).
//  - The player: host/player's main (renamed sa2_player_main by tools/s8/android_app.py) on this
//    thread, with the launcher's play argv: --interactive with in-process pipes for --video-fd,
//    --cancel-fd and --end-fd, --stage 3, --input-path, --pace and --audio. The player draws the
//    game, takes the touch overlay and the gamepads, and plays the sound.
//  - The end: the player's end request (SDL_EVENT_QUIT: Sa2Activity's Back, or the activity's
//    destruction) or either side's exit stops the game (SIGTERM, which the runtime turns into
//    WM_QUIT at its next keyboard poll; SIGKILL after kStopGrace), the relay ends with the
//    stream, and the player reads it to its END (cancelled kFeedGrace later if it has not).
//
// A debug mode plays a recorded stream file through the player with no game (--stream PATH:
// Sa2Activity's intent extra sa2.stream), the renderer's check on a device.
//
// Every outcome is written to <files>/last-session.txt, which Sa2SetupActivity shows when the
// session ends, and to logcat (tag sa2recomp).
//
// The session recorder (host/launch/sa2_session_files.h), as the launcher's:
// before the game, the saves and Config/ it will see (start-saves/, start-config/), its settings
// (game-env.txt) and session-info.json (the device's model, Android version, SoC and ABI, the
// screen, the settings screen's choices); the pads by XInput call (SA2_INPUT_PAD_LOG, pad.log),
// since the touch overlay is slot 0's pad and the keyboard log alone does not replay a phone's
// session; after it, end-saves/ and end-config/, and session-info.json again with the sha256 of
// libsa2game.so and libmain.so (hashed once the game has ended, so the boot does not wait on
// it). The settings screen's Debug section (host/android/java DebugExport) zips the folder.
//
// Speed on a phone (the phone-perf unit, 2026-10-12). The game's guest threads run one at a
// time, and on the user's phone that serialized path (the guest's main thread, its audio thread
// and the recorder) was the frame's bottleneck. So, by default:
//  - Placement. At the start the session reads each CPU's cpuinfo_max_freq (cpu_capacity when
//    there is none): the CPUs with the highest are the game's (SA2_GUEST_HOST_CPUS, which the
//    runtime applies to every guest thread, runtime/sa2_threads.c), and the others the player's
//    (SA2_PLAYER_HOST_CPUS: this thread, after the game starts, so the relay, the player's
//    render thread, its audio and the GPU driver's threads it starts inherit them). When every
//    CPU tops out alike, nothing is placed.
//  - The recorder does not watch the DirectSound stores (SA2_REC_AUDIO_WATCH=0, as the
//    launcher's play: host/sa2_present_rec.c), and hashes one store in 64 that an Unlock closes
//    (SA2_REC_STORE_HASH_EVERY=64, the ones the live player holds: player.cpp,
//    kLiveStoreCheckEvery), not each one, byte by byte over the whole store, on the guest's path.
//  - ADPF. Two APerformanceHint sessions (API 33, loaded with dlopen so API 29 still runs), each
//    with a 16.67 ms target (SA2_ADPF_TARGET_US): the game's threads, from this process (Android
//    14 accepts another process's threads of the same uid), told the game's CPU time per frame;
//    and the player's thread with the relay's, told theirs. SA2_ADPF=0 makes none.
//  - The window's sustained-performance mode stays off (Sa2Activity): it caps the clocks.
// The defaults are set before game.env.extra is read, so a line there replaces each of them
// (SA2_GUEST_HOST_CPUS= with no value places no guest thread). Every 30 s the session writes
// the frame rate, each busy thread's CPU, its allowed CPUs and its last CPU, and the current
// clock of each CPU the guest's threads and the player's may run on (scaling_cur_freq), so a
// faster frame can be told to come from the clocks or from less work, to logcat and to
// last-session.txt.
//
// A stall (the AYN Thor's at the SEGA logo, 2026-10-17). The relay paces the game through a pace
// pipe and never resizes the game's FIFO (host/launch/sa2_relay.h, "Pace"), and nudges the game
// (SIGURG) when part of a frame has waited a second for the rest; its lines go to last-session.txt.
// When no frame has been handed on for kStallSay seconds the session says so once, with each game
// thread's state, wait channel, allowed CPUs and last CPU, and the relay's FIFO size, the bytes it
// holds past the last whole frame and how long the game has written nothing; a perf line over a
// window that ends in such a stall carries the same.
//
// Full width (the user's request of 2026-10-12,
// Documentation/decisions/2026-10-12-S7-widescreen-pulled-forward.md). Sa2Activity passes the
// screen's size in pixels in the session's orientation (--screen WxH: the largest window the
// activity can have, the cutout included; landscape unless the settings screen chose portrait).
// Unless it is no wider than 4:3, the session sets SA2_PLAY_RES to it, so the
// runtime rewrites the config's Width and Height (runtime/sa2_widescreen.c) and the game draws its
// back buffer at the screen's own size and aspect: the player presents it whole, with no bars.
// The settings screen replaces it with its own SA2_PLAY_RES (WxH from its widescreen, resolution
// and aspect choices, 4:3 in portrait, or off), and SA2_PLAY_HUD43=1 with 0 when it turns the
// HUD option off. A game.env.extra line replaces either: SA2_PLAY_RES=off (the config's own size,
// pillarboxed), SA2_PLAY_RES=WxH (that size), SA2_PLAY_RES=hNNN (NNN lines at the screen's aspect,
// which the session resolves to WxH: h720 on a 2340x1080 screen is 1560x720), or
// SA2_PLAY_ASPECT=W:H alone (the config's Height at that aspect). The session says what it set,
// and then the game's own line saying what the config became ("widescreen: config 1280x720 ->
// 2340x1080 ...", from its game.log).
//
// Orientation (--orientation landscape|portrait|auto, the settings screen's; landscape when
// absent): SDL's SDL_ORIENTATIONS hint, landscape either way up, portrait either way up, or all
// four. Sa2Activity asks the system for the same orientation itself. In portrait and auto-rotate
// the settings screen also sends SA2_PLAYER_PICTURE_Y, where the player puts the picture in an
// upright window (host/player/player.cpp "The picture's place"); the session says the player's
// line for it ("player: picture: ...").
//
// Tweaks (runtime/sa2_tweaks.h): changes to the game's own behaviour, or the host's, each off by
// default. The settings screen's Tweaks card sends the ones turned on as one setting,
// SA2_TWEAKS=<names> ("no-event-bars": no black bars at the sides of in-engine cutscenes),
// which reaches the game as every other setting does (a game.env.extra line replaces it), and the
// player too, with the same value, after game.env.extra's (host/player/tweaks.h: the host tweaks
// whose home is the player), with the game's SA2_REPLAY_TICKS or SA2_REPLAY_WORDS if game.env.extra
// sets one, so the player ignores its tweaks in a replay as the game does. The report names the setting ("settings: SA2_TWEAKS=...") and then
// the game's own line saying which tweaks are on ("game: sa2 tweaks: event-bars off ...", from its
// game.log), and the player's ("player: sa2 host tweaks (player): ...", from its player.log).
#include <android/log.h>
#include <dlfcn.h>
#include <fcntl.h>
#include <pthread.h>
#include <sched.h>
#include <signal.h>
#include <sys/stat.h>
#include <sys/system_properties.h>
#include <unistd.h>

#include <algorithm>
#include <atomic>
#include <cerrno>
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <filesystem>
#include <iterator>
#include <map>
#include <mutex>
#include <string>
#include <thread>
#include <utility>
#include <vector>

#include <SDL3/SDL.h>

#include "sa2_config.h"
#include "sa2_cpu_plan.h"
#include "sa2_install.h"
#include "sa2_launch_platform.h"
#include "sa2_platform.h"
#include "sa2_relay.h"
#include "sa2_session_files.h"
#include "sha256.h"

// host/player/player.cpp's main, renamed by the build (-Dmain=sa2_player_main).
int sa2_player_main(int argc, char** argv);

namespace {

constexpr const char* kTag = "sa2recomp";
constexpr const char* kGameLib = "libsa2game.so";
constexpr size_t kRelayCap = 64u << 20;                  // the launcher's
constexpr double kStopGrace = 10, kKillGrace = 5, kFeedGrace = 15;
constexpr double kStallSay = 5;                          // seconds without a frame: the stall line
constexpr int kPaceFd = 43;                              // the game's pace pipe, as the launcher's
constexpr int kKeepSessions = 3;

std::string g_files, g_report;        // <files>, <files>/last-session.txt
std::vector<std::string> g_lines;     // what last-session.txt says
std::mutex g_say;                     // the monitor thread says things too

void say(const char* fmt, ...) __attribute__((format(printf, 1, 2)));
void say(const char* fmt, ...) {
    char buf[2048];
    va_list ap;
    va_start(ap, fmt);
    std::vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);
    std::lock_guard<std::mutex> hold(g_say);
    __android_log_write(ANDROID_LOG_INFO, kTag, buf);
    g_lines.emplace_back(buf);
    if (!g_report.empty()) {
        std::string text;
        for (const std::string& l : g_lines) text += l + "\n";
        sa2p::write_file_atomic(g_report, text);
    }
}

bool exists_file(const std::string& p) {
    sa2p::Stat s;
    return sa2p::stat_path(p, s) && s.is_file;
}
bool exists_dir(const std::string& p) {
    sa2p::Stat s;
    return sa2p::stat_path(p, s) && s.is_dir;
}

std::string stamp() {
    char buf[32];
    std::time_t t = std::time(nullptr);
    std::tm tm{};
    localtime_r(&t, &tm);
    std::strftime(buf, sizeof buf, "%Y%m%d-%H%M%S", &tm);
    return std::string(buf) + "-" + std::to_string(getpid());
}

// A new session folder under <files>/sessions, keeping the kKeepSessions - 1 newest before it.
std::string new_session(const std::string& prefix) {
    namespace fs = std::filesystem;
    const std::string root = g_files + "/sessions";
    sa2p::make_dirs(root);
    std::vector<std::string> names;
    sa2p::list_dir(root, names);
    std::sort(names.begin(), names.end());
    std::error_code ec;
    for (size_t i = 0; i + (kKeepSessions - 1) < names.size(); i++) fs::remove_all(root + "/" + names[i], ec);
    const std::string s = root + "/" + stamp() + prefix;
    return sa2p::make_dirs(s) ? s : "";
}

// stdout and stderr (the player's printf, SDL's own stderr) into the session's file.
void redirect_output(const std::string& path) {
    int fd = open(path.c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    if (fd < 0) return;
    std::fflush(stdout);
    std::fflush(stderr);
    dup2(fd, 1);
    dup2(fd, 2);
    close(fd);
    setvbuf(stdout, nullptr, _IOLBF, 0);
}

// A descriptor handed to the player, which owns it from then on (it closes the stream's two when
// it ends, the end pipe's after the request): closed here only while it is still the same pipe.
struct Held {
    int fd = -1;
    dev_t dev = 0;
    ino_t ino = 0;
    void hold(intptr_t f) {
        fd = int(f);
        struct stat st;
        if (fstat(fd, &st) == 0) {
            dev = st.st_dev;
            ino = st.st_ino;
        }
    }
    void close_if_ours() {
        struct stat st;
        if (fd >= 0 && fstat(fd, &st) == 0 && st.st_dev == dev && st.st_ino == ino) close(fd);
        fd = -1;
    }
};

int run_player(const std::vector<std::string>& args) {
    std::vector<std::string> copy = args;
    std::vector<char*> argv;
    for (std::string& a : copy) argv.push_back(a.data());
    argv.push_back(nullptr);
    std::string line;
    for (const std::string& a : args) line += a + " ";
    __android_log_print(ANDROID_LOG_INFO, kTag, "player: %s", line.c_str());
    return sa2_player_main(int(argv.size() - 1), argv.data());
}

void player_env(const std::string& session) {
    for (const char* k : {"SA2_DXVK_D3D9", "SA2_D3D9GPU_PRESENT", "SDL_VIDEO_DRIVER", "SDL_AUDIO_DRIVER",
                          "SA2_PLAYER_PROF", "SA2_PLAYER_PROF_OUT", "SA2_TWEAKS", "SA2_REPLAY_TICKS",
                          "SA2_REPLAY_WORDS"}) unsetenv(k);
    setenv("SA2_PLAY_OUT", (session + "/play").c_str(), 1);
    setenv("SA2_D3D9GPU_REPORT", (session + "/d3d9gpu.runrec").c_str(), 1);
    setenv("SDL_NO_SIGNAL_HANDLERS", "1", 1);
    sa2p::make_dirs(session + "/play");
}

using EnvList = std::vector<std::pair<std::string, std::string>>;

struct Args {
    std::string files, cache, external, libdir, stream, screen, orientation;
    bool audio = true, pace = true;
    std::string max_frames;
    EnvList settings;         // --setting SA2_K=V: the app's settings screen
};

// The settings that are the player's, not the game's: they go to this process's environment,
// where the player runs, and are taken out of the game's.
bool player_setting(const std::string& k) {
    return k == "SA2_PLAYER_PROF" || k == "SA2_PLAYER_PROF_OUT" || k == "SA2_PLAYER_PICTURE_Y" || k == "SA2_PAD_MAP" ||
           k.compare(0, 10, "SA2_TOUCH_") == 0;
}

// The first line of `path` that holds `needle`, said after `prefix` once it is there, while the
// player runs, for at most a minute: a line the session's report should carry from another log.
void relay_line(const std::string& path, const char* needle, const char* prefix, const std::atomic<bool>& done) {
    const double t0 = sa2p::monotonic_seconds();
    while (!done.load() && sa2p::monotonic_seconds() - t0 < 60) {
        std::string log;
        if (sa2p::read_file(path, log)) {
            const size_t at = log.find(needle);
            const size_t nl = at == std::string::npos ? at : log.find('\n', at);
            if (nl != std::string::npos) {
                say("%s%s", prefix, log.substr(at, nl - at).c_str());
                return;
            }
        }
        sa2l::sleep_ms(250);
    }
}

// ---------------------------------------------------------------- the debug mode: a stream file

int play_stream(const Args& a) {
    const std::string session = new_session("-stream");
    if (session.empty()) {
        say("FAIL cannot create a session folder under %s/sessions", g_files.c_str());
        return 5;
    }
    if (!exists_file(a.stream)) {
        say("FAIL no stream file at %s", a.stream.c_str());
        return 4;
    }
    say("session %s", session.c_str());
    say("mode stream %s", a.stream.c_str());
    redirect_output(session + "/player.log");
    player_env(session);
    for (const auto& kv : a.settings)
        if (player_setting(kv.first)) {
            setenv(kv.first.c_str(), kv.second.c_str(), 1);
            say("settings: %s=%s", kv.first.c_str(), kv.second.c_str());
        }
    std::vector<std::string> args = {"sa2_player", a.stream, "--stage", "3", "--allow-truncated",
                                     "--report", session + "/player-report.runrec"};
    if (a.pace) args.push_back("--pace");
    if (a.audio) args.push_back("--audio");
    if (!a.max_frames.empty()) {
        args.push_back("--max-frames");
        args.push_back(a.max_frames);
    }
    const int rc = run_player(args);
    say("player exit %d", rc);
    if (rc == 0) say("OK the stream played");
    else say("FAIL the player exited %d (see %s/player.log)", rc, session.c_str());
    return rc;
}

// ---------------------------------------------------------------- placement

const std::string* env_find(const EnvList& env, const std::string& k) {
    for (const auto& e : env)
        if (e.first == k) return &e.second;
    return nullptr;
}

// The guest's and the player's CPUs (sa2_cpu_plan.h), from this process's own.
sa2cpu::Plan plan_cpus() {
    cpu_set_t allowed;
    CPU_ZERO(&allowed);
    if (sched_getaffinity(0, sizeof allowed, &allowed) != 0) {
        sa2cpu::Plan p;
        p.why = std::string("sched_getaffinity: ") + std::strerror(errno) + ", so nothing is placed";
        return p;
    }
    return sa2cpu::plan("/sys/devices/system/cpu", allowed);
}

// The CPUs this thread may run on, as a list ("0-7"); empty when they do not read.
std::string own_cpu_list() {
    cpu_set_t set;
    CPU_ZERO(&set);
    if (sched_getaffinity(0, sizeof set, &set) != 0) return "";
    std::vector<int> cpus;
    for (int c = 0; c < CPU_SETSIZE; c++)
        if (CPU_ISSET(c, &set)) cpus.push_back(c);
    return sa2cpu::list_of(cpus);
}

// This thread, and every thread it starts from here on, onto `cpus`: what was done.
std::string place_this_thread(const std::string& cpus) {
    if (cpus.empty()) return "not placed";
    cpu_set_t set;
    if (!sa2cpu::parse_list(cpus, set)) return "not placed (\"" + cpus + "\" is not a CPU list)";
    if (sched_setaffinity(0, sizeof set, &set) != 0)
        return "not placed (sched_setaffinity " + cpus + ": " + std::strerror(errno) + ")";
    return "on CPUs " + cpus;
}

// ---------------------------------------------------------------- ADPF and the monitor

// The NDK's APerformanceHint (android/performance_hint.h: API 33, setThreads API 34), loaded at
// run time, since the app's minSdk is 29.
struct Hints {
    using GetManager = void* (*)();
    using Create = void* (*)(void*, const int32_t*, size_t, int64_t);
    using Rate = int64_t (*)(void*);
    using Report = int (*)(void*, int64_t);
    using Close = void (*)(void*);
    using SetThreads = int (*)(void*, const pid_t*, size_t);
    Create create = nullptr;
    Report report = nullptr;
    Close close = nullptr;
    SetThreads set_threads = nullptr;
    void* manager = nullptr;
    int64_t rate_ns = -1;
    std::string why;              // why there are none

    bool load() {
        void* h = dlopen("libandroid.so", RTLD_NOW | RTLD_LOCAL);
        if (!h) {
            const char* e = dlerror();
            why = std::string("libandroid.so does not load: ") + (e ? e : "?");
            return false;
        }
        auto get = reinterpret_cast<GetManager>(dlsym(h, "APerformanceHint_getManager"));
        auto rate = reinterpret_cast<Rate>(dlsym(h, "APerformanceHint_getPreferredUpdateRateNanos"));
        create = reinterpret_cast<Create>(dlsym(h, "APerformanceHint_createSession"));
        report = reinterpret_cast<Report>(dlsym(h, "APerformanceHint_reportActualWorkDuration"));
        close = reinterpret_cast<Close>(dlsym(h, "APerformanceHint_closeSession"));
        set_threads = reinterpret_cast<SetThreads>(dlsym(h, "APerformanceHint_setThreads"));
        if (!get || !create || !report || !close) {
            why = "this Android has no APerformanceHint (API 33 and later)";
            return false;
        }
        manager = get();
        if (!manager) {
            why = "APerformanceHint_getManager gave no manager";
            return false;
        }
        if (rate) rate_ns = rate(manager);
        return true;
    }
};

// One hint session: its threads and what it was told.
struct HintSession {
    const char* name = "";
    void* s = nullptr;
    std::vector<int32_t> tids;
    uint64_t reports = 0, over = 0, failed = 0, moves = 0;
};

int64_t clock_ns(clockid_t c) {
    timespec ts;
    if (clock_gettime(c, &ts) != 0) return -1;
    return int64_t(ts.tv_sec) * 1000000000 + ts.tv_nsec;
}

// A thread's CPU clock read so that it never goes back: once the thread has ended its clock
// cannot be read (-1), and the last good reading stands, so a sum over threads holds what each
// one spent.
struct ThreadClock {
    clockid_t clk{};
    bool have = false;
    int64_t last = 0;
    int64_t read() {
        if (have) {
            const int64_t v = clock_ns(clk);
            if (v >= 0) last = v;
        }
        return last;
    }
};

std::vector<int32_t> tasks_of(int pid) {
    std::vector<std::string> names;
    std::vector<int32_t> out;
    if (!sa2p::list_dir("/proc/" + std::to_string(pid) + "/task", names)) return out;
    for (const std::string& n : names) {
        char* end = nullptr;
        const long t = std::strtol(n.c_str(), &end, 10);
        if (end != n.c_str() && *end == 0 && t > 0) out.push_back(int32_t(t));
    }
    std::sort(out.begin(), out.end());
    return out;
}

// One of the game's threads, as /proc gives it.
struct Task {
    int32_t tid = 0;
    std::string comm, cpus;       // its name, its Cpus_allowed_list
    char state = '?';             // R running, S sleeping, D uninterruptible, T stopped, ...
    int last_cpu = -1;            // the CPU it last ran on
    int64_t ns = -1;              // its time on a CPU (schedstat)
};

Task task_of(int pid, int32_t tid) {
    Task t;
    t.tid = tid;
    const std::string base = "/proc/" + std::to_string(pid) + "/task/" + std::to_string(tid);
    std::string s;
    if (sa2p::read_file(base + "/comm", s, 4096)) {
        while (!s.empty() && (s.back() == '\n' || s.back() == ' ')) s.pop_back();
        t.comm = s;
    }
    if (sa2p::read_file(base + "/schedstat", s, 4096)) t.ns = std::strtoll(s.c_str(), nullptr, 10);
    if (sa2p::read_file(base + "/status", s, 1u << 16)) {
        const size_t at = s.find("Cpus_allowed_list:");
        if (at != std::string::npos) {
            size_t b = at + 18, e = s.find('\n', b);
            while (b < s.size() && (s[b] == ' ' || s[b] == '\t')) b++;
            t.cpus = s.substr(b, e == std::string::npos ? std::string::npos : e - b);
        }
    }
    if (sa2p::read_file(base + "/stat", s, 4096)) {
        // After the name's ")": the state is field 3, and the CPU it last ran on field 39.
        const size_t rp = s.rfind(')');
        if (rp != std::string::npos) {
            const char* p = s.c_str() + rp + 1;
            while (*p == ' ') p++;
            if (*p) t.state = *p;
            for (int field = 3; *p && field < 39; field++) {
                while (*p == ' ') p++;
                while (*p && *p != ' ') p++;
            }
            while (*p == ' ') p++;
            if (*p) t.last_cpu = int(std::strtol(p, nullptr, 10));
        }
    }
    return t;
}

// Where a sleeping thread of the game waits (/proc/<pid>/task/<tid>/wchan: a kernel function,
// pipe_write or futex_wait_queue for two; "0" or "" where the kernel does not say).
std::string wchan_of(int pid, int32_t tid) {
    std::string s;
    if (!sa2p::read_file("/proc/" + std::to_string(pid) + "/task/" + std::to_string(tid) + "/wchan", s, 256)) return "?";
    while (!s.empty() && (s.back() == '\n' || s.back() == ' ')) s.pop_back();
    return s.empty() ? "0" : s;
}

// The stall's account (the header, "A stall"): each game thread's state and wait channel, and
// what the relay holds.
std::string stall_text(int pid, const sa2l::Relay& relay, double no_frame_s) {
    char head[96];
    std::snprintf(head, sizeof head, "no frame for %.1f s; game threads:", no_frame_s);
    std::string t = head;
    for (int32_t tid : tasks_of(pid)) {
        const Task k = task_of(pid, tid);
        t += " " + std::to_string(tid) + " " + k.comm + " " + std::string(1, k.state);
        if (k.state != 'R') t += " " + wchan_of(pid, tid);
        t += " on " + k.cpus + " (last " + std::to_string(k.last_cpu) + ");";
    }
    const sa2l::RelayStats r = relay.stats();
    char rel[320];
    std::snprintf(rel, sizeof rel,
                  " relay: FIFO %llu bytes, %llu bytes past the last whole frame, no byte from the game for %.1f s%s, "
                  "%llu frames in, %llu out, %llu nudges, %llu pace bytes (%llu refused)",
                  (unsigned long long)r.fifo_bytes, (unsigned long long)r.partial_bytes, r.quiet_s,
                  r.held_now ? " (holding)" : "", (unsigned long long)r.frames_in, (unsigned long long)r.frames_out,
                  (unsigned long long)r.nudges, (unsigned long long)r.credits, (unsigned long long)r.credits_dropped);
    return t + rel;
}

// The session's watch on speed (the header, "Speed on a phone"): the ADPF sessions, told each
// frame's CPU time, and a report every 30 s.
struct Monitor {
    int game_pid = -1;
    pthread_t player_thread{};
    int32_t player_tid = 0;
    pthread_t relay_thread{};
    std::atomic<int32_t> relay_tid{0};
    // The relay's writer thread (Relay::on_writer_start), set from that thread: the clock, then
    // the tid, which tells the monitor both are there.
    std::atomic<clockid_t> writer_clk{};
    std::atomic<int32_t> writer_tid{0};
    const sa2l::Relay* relay = nullptr;
    std::atomic<bool> stop{false};
    std::atomic<bool> stopping{false};    // the session is stopping the game: its frames end
    bool adpf = true;
    int64_t target_ns = 16666667;
    std::string player_cpus;
    // The CPUs whose clocks the report reads: those the game's guest threads may run on, and the
    // player's (the session's own when it placed neither).
    std::string guest_clock_cpus, player_clock_cpus;

    // "; clocks (MHz) guest 6:4320 7:4320, player 0:3532 ..." for the perf line: the two lists,
    // once when they are the same CPUs; empty when neither reads.
    std::string clocks() const {
        const char* root = "/sys/devices/system/cpu";
        const std::string g = sa2cpu::clocks_mhz(root, guest_clock_cpus);
        if (guest_clock_cpus == player_clock_cpus) return g.empty() ? "" : "; clocks (MHz) guest and player " + g;
        const std::string p = sa2cpu::clocks_mhz(root, player_clock_cpus);
        if (g.empty() && p.empty()) return "";
        return "; clocks (MHz) guest " + (g.empty() ? std::string("?") : g) + ", player " +
               (p.empty() ? std::string("?") : p);
    }

    void run() {
        clockid_t gclk{};
        const bool have_g = clock_getcpuclockid(pid_t(game_pid), &gclk) == 0;
        ThreadClock pc, rc, wc;
        pc.have = pthread_getcpuclockid(player_thread, &pc.clk) == 0;
        rc.have = pthread_getcpuclockid(relay_thread, &rc.clk) == 0;
        auto game_ns = [&] { return have_g ? clock_ns(gclk) : -1; };
        auto player_ns = [&] { return pc.read(); };
        // The relay's two threads: its reader (Relay::run) and its writer, once it has started.
        auto relay_ns = [&] {
            if (!wc.have && writer_tid.load(std::memory_order_acquire)) {
                wc.clk = writer_clk.load();
                wc.have = true;
            }
            return rc.read() + wc.read();
        };

        Hints hints;
        bool hints_ok = false;
        if (!adpf) {
            say("ADPF: off (SA2_ADPF=0)");
        } else if (!(hints_ok = hints.load())) {
            say("ADPF: none, %s", hints.why.c_str());
        } else {
            say("ADPF: the manager's preferred update rate is %lld ns; targets %.2f ms", (long long)hints.rate_ns,
                double(target_ns) / 1e6);
        }
        HintSession gs, ps;
        gs.name = "the game's";
        ps.name = "the player's";
        auto tid_list = [](const std::vector<int32_t>& t) {
            std::string s;
            for (int32_t x : t) s += (s.empty() ? "" : ",") + std::to_string(x);
            return s;
        };
        auto retarget = [&](HintSession& h, const std::vector<int32_t>& tids) {
            static_assert(sizeof(pid_t) == sizeof(int32_t), "APerformanceHint's thread ids");
            if (h.s && hints.set_threads &&
                hints.set_threads(h.s, reinterpret_cast<const pid_t*>(tids.data()), tids.size()) == 0) {
                h.tids = tids;
                if (++h.moves <= 3)               // a short-lived thread comes and goes every scan
                    say("ADPF: %s session now holds threads %s%s", h.name, tid_list(tids).c_str(),
                        h.moves == 3 ? " (later changes are counted at the end)" : "");
                return;
            }
            if (h.s) hints.close(h.s);
            h.s = hints.create(hints.manager, tids.data(), tids.size(), target_ns);
            h.tids = tids;
            say("ADPF: %s session for threads %s %s", h.name, tid_list(tids).c_str(),
                h.s ? "made" : "refused by the system");
        };
        auto tell = [&](HintSession& h, int64_t ns) {
            if (!h.s) return;
            ns = std::max<int64_t>(ns, 1000);
            if (hints.report(h.s, ns) == 0) {
                h.reports++;
                if (ns > target_ns) h.over++;
            } else {
                h.failed++;
            }
        };

        const double t0 = sa2p::monotonic_seconds();
        double next_scan = t0 + 1, next_report = t0 + 10, t_win = t0;
        uint64_t f_last = relay->frames_out(), f_win = f_last;
        const uint64_t f0 = f_last;
        int64_t g_last = game_ns(), p_last = player_ns() + relay_ns();
        uint64_t gf_last = f_last;                // the frames counted when g_last was read
        const int64_t g0 = g_last, p0 = p_last;
        int64_t g_win = g_last, pl_win = player_ns(), r_win = relay_ns();
        std::map<int32_t, int64_t> task_win;      // a game thread -> its schedstat at the window's start
        for (int32_t t : tasks_of(game_pid)) task_win[t] = task_of(game_pid, t).ns;

        double last_frame_t = t0;                 // when frames_out last moved
        bool stall_said = false;
        while (!stop.load()) {
            sa2l::sleep_ms(8);
            const uint64_t f = relay->frames_out();
            if (f != f_last) {
                last_frame_t = sa2p::monotonic_seconds();
                stall_said = false;
                const int64_t g = game_ns(), p = player_ns() + relay_ns();
                if (g >= 0) {                 // -1 once the game has ended
                    if (g_last >= 0) tell(gs, (g - g_last) / int64_t(f - gf_last));
                    g_last = g;
                    gf_last = f;
                }
                tell(ps, (p - p_last) / int64_t(f - f_last));
                f_last = f;
                p_last = p;
            }
            const double now = sa2p::monotonic_seconds();
            if (!stall_said && f_last > 0 && !stopping.load() && now - last_frame_t >= kStallSay) {
                say("stall: %s", stall_text(game_pid, *relay, now - last_frame_t).c_str());
                stall_said = true;
            }
            if (hints_ok && now >= next_scan) {
                next_scan = now + 2;
                if (f_last > 0) {             // it has presented: its guest threads are made
                    const std::vector<int32_t> tids = tasks_of(game_pid);
                    if (!tids.empty() && tids != gs.tids) retarget(gs, tids);
                }
                std::vector<int32_t> mine = {player_tid};
                if (relay_tid.load()) mine.push_back(relay_tid.load());
                if (writer_tid.load()) mine.push_back(writer_tid.load());
                if (mine != ps.tids) retarget(ps, mine);
            }
            if (now >= next_report) {
                next_report = now + 30;
                const double secs = now - t_win;
                const uint64_t frames = f_last - f_win;
                const int64_t g = game_ns(), pl = player_ns(), r = relay_ns();
                std::vector<std::pair<int64_t, Task>> busy;
                std::map<int32_t, int64_t> next_win;
                for (int32_t t : tasks_of(game_pid)) {
                    Task k = task_of(game_pid, t);
                    next_win[t] = k.ns;
                    const auto was = task_win.find(t);
                    const int64_t d = k.ns >= 0 ? k.ns - (was != task_win.end() && was->second >= 0 ? was->second : 0) : 0;
                    if (d > int64_t(secs * 1e7)) busy.emplace_back(d, k);    // over 1% of a core
                }
                task_win.swap(next_win);
                std::sort(busy.begin(), busy.end(), [](const auto& a, const auto& b) { return a.first > b.first; });
                std::string threads;
                for (size_t i = 0; i < busy.size() && i < 5; i++)
                    threads += (i ? ", " : "") + std::to_string(busy[i].second.tid) + " " + busy[i].second.comm + " " +
                               std::to_string(int(busy[i].first / (secs * 1e7) + 0.5)) + "% on " +
                               busy[i].second.cpus + " (last " + std::to_string(busy[i].second.last_cpu) + ")";
                const double per = frames ? 1e-6 / double(frames) : 0;
                // A window that ends in a stall (no frame for 3 s, or none in the window) says why.
                const std::string stalled = (frames == 0 || now - last_frame_t >= 3) && f_last > 0 && !stopping.load()
                    ? "; stalled: " + stall_text(game_pid, *relay, now - last_frame_t) : "";
                say("perf %.0f-%.0f s: %.1f fps; game %.2f ms of CPU a frame (%.0f%% of a core): %s; player %.2f ms "
                    "a frame (%.0f%%) and relay %.0f%%, %s; ADPF reports: game %llu (%llu over), player %llu (%llu over)%s%s",
                    t_win - t0, now - t0, double(frames) / secs, g >= 0 && g_win >= 0 ? double(g - g_win) * per : -1.0,
                    g >= 0 && g_win >= 0 ? double(g - g_win) / (secs * 1e7) : -1.0, threads.c_str(),
                    double(pl - pl_win + r - r_win) * per, double(pl - pl_win) / (secs * 1e7),
                    double(r - r_win) / (secs * 1e7), player_cpus.empty() ? "not placed" : ("on " + player_cpus).c_str(),
                    (unsigned long long)gs.reports, (unsigned long long)gs.over, (unsigned long long)ps.reports,
                    (unsigned long long)ps.over, clocks().c_str(), stalled.c_str());
                t_win = now;
                f_win = f_last;
                g_win = g;
                pl_win = pl;
                r_win = r;
            }
        }
        const double secs = sa2p::monotonic_seconds() - t0;
        const uint64_t frames = f_last - f0;
        if (frames)
            say("perf over the session: %llu frames in %.0f s (%.1f fps); game %.2f ms of CPU a frame, player and "
                "relay %.2f ms", (unsigned long long)frames, secs, double(frames) / secs,
                g_last >= 0 && g0 >= 0 && gf_last > f0 ? double(g_last - g0) / 1e6 / double(gf_last - f0) : -1.0,
                double(p_last - p0) / 1e6 / double(frames));
        for (HintSession* h : {&gs, &ps})
            if (h->s) {
                say("ADPF: %s session: %llu reports, %llu over the target, %llu refused; its threads changed %llu "
                    "times", h->name, (unsigned long long)h->reports, (unsigned long long)h->over,
                    (unsigned long long)h->failed, (unsigned long long)h->moves);
                hints.close(h->s);
            }
    }
};

// ---------------------------------------------------------------- play

bool wait_proc(sa2l::Proc& p, double s) {
    const double t0 = sa2p::monotonic_seconds();
    while (!sa2l::wait_exit(p, 0.1))
        if (sa2p::monotonic_seconds() - t0 >= s) return false;
    return true;
}

std::string stop_game(sa2l::Proc& game) {
    if (!game.running) return "nothing (it had ended by itself)";
    sa2l::send_signal(game, sa2l::Signal::Terminate);
    if (wait_proc(game, kStopGrace)) return "SIGTERM";
    sa2l::send_signal(game, sa2l::Signal::Kill, true);
    sa2l::wait_exit(game, 5);
    return "SIGKILL";
}

bool read_game_env(std::vector<std::pair<std::string, std::string>>& env, std::string* error) {
    size_t n = 0;
    // SDL reads a relative path from the APK's assets on Android.
    void* data = SDL_LoadFile("game.env", &n);
    if (!data) {
        *error = std::string("no assets/game.env in the APK: ") + SDL_GetError();
        return false;
    }
    std::string text(static_cast<const char*>(data), n);
    SDL_free(data);
    size_t at = 0;
    while (at < text.size()) {
        size_t nl = text.find('\n', at);
        std::string line = text.substr(at, nl == std::string::npos ? std::string::npos : nl - at);
        at = nl == std::string::npos ? text.size() : nl + 1;
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (line.empty() || line[0] == '#') continue;
        size_t eq = line.find('=');
        if (eq == std::string::npos || line.compare(0, 4, "SA2_") != 0) {
            *error = "assets/game.env: a line that is no SA2_ setting: " + line;
            return false;
        }
        env.emplace_back(line.substr(0, eq), line.substr(eq + 1));
    }
    return true;
}

void env_set(std::vector<std::pair<std::string, std::string>>& env, const std::string& k, const std::string& v) {
    for (auto& e : env)
        if (e.first == k) {
            e.second = v;
            return;
        }
    env.emplace_back(k, v);
}

// ---------------------------------------------------------------- full width (the header)

// A whole number of 1 to 5 digits spanning `v`, or 0.
uint32_t whole(const std::string& v) {
    if (v.empty() || v.size() > 5 || v.find_first_not_of("0123456789") != std::string::npos) return 0;
    return uint32_t(std::strtoul(v.c_str(), nullptr, 10));
}

// "WxH" as the runtime takes SA2_PLAY_RES: each of W and H 320 to 16384.
bool parse_size(const std::string& v, uint32_t& w, uint32_t& h) {
    const size_t x = v.find_first_of("xX");
    if (x == std::string::npos) return false;
    w = whole(v.substr(0, x));
    h = whole(v.substr(x + 1));
    return w >= 320 && w <= 16384 && h >= 320 && h <= 16384;
}

// The full-width settings in `env` after the settings screen's and game.env.extra's: the screen's
// size unless the settings screen set SA2_PLAY_RES (`settings_res`), or a game.env.extra line set
// SA2_PLAY_RES (an "hNNN" one resolved here at the screen's aspect) or SA2_PLAY_ASPECT alone.
// What was decided, in words.
std::string full_width(EnvList& env, const std::string& screen, bool settings_res, bool extra_res,
                       bool extra_aspect) {
    auto drop = [&](const char* k) {
        env.erase(std::remove_if(env.begin(), env.end(), [k](const auto& e) { return e.first == k; }), env.end());
    };
    uint32_t sw = 0, sh = 0;
    const bool have = parse_size(screen, sw, sh);
    const std::string on = have ? "screen " + screen : "no screen size (" + (screen.empty() ? std::string("none given")
                                                                                             : "\"" + screen + "\"") + ")";
    if (extra_aspect && !extra_res) {
        drop("SA2_PLAY_RES");
        return on + "; SA2_PLAY_ASPECT=" + *env_find(env, "SA2_PLAY_ASPECT") + " (game.env.extra)";
    }
    const std::string* res = env_find(env, "SA2_PLAY_RES");
    if (extra_res && res && res->size() > 1 && (res->at(0) == 'h' || res->at(0) == 'H')) {
        const uint32_t lines = whole(res->substr(1));
        const std::string asked = *res;
        if (!have || lines < 320 || lines > 16384) {
            drop("SA2_PLAY_RES");
            return on + "; SA2_PLAY_RES=" + asked + " (game.env.extra) not applied: " +
                   (have ? "NNN must be 320 to 16384" : "no screen size to take the aspect from") +
                   ", so the config's own size";
        }
        const uint64_t w = (uint64_t(lines) * sw + sh / 2) / sh;
        if (w < 320 || w > 16384) {
            drop("SA2_PLAY_RES");
            return on + "; SA2_PLAY_RES=" + asked + " (game.env.extra) gives a width outside 320 to 16384, so the "
                   "config's own size";
        }
        env_set(env, "SA2_PLAY_RES", std::to_string(w) + "x" + std::to_string(lines));
        return on + "; SA2_PLAY_RES=" + asked + " (game.env.extra): " + *env_find(env, "SA2_PLAY_RES");
    }
    if (extra_res) return on + "; SA2_PLAY_RES=" + (res ? *res : std::string()) + " (game.env.extra)";
    if (res) return on + "; SA2_PLAY_RES=" + *res + (settings_res ? " (the settings screen)" : " (the screen)");
    return on + "; the config's own size" + (have ? " (the screen is no wider than 4:3)" : "");
}

// ------------------------------------------------------------ the session recorder (the header)

std::string prop(const char* name) {
    char buf[PROP_VALUE_MAX] = {0};
    __system_property_get(name, buf);
    return buf;
}

// The device, for session-info.json: what a bug report needs to know about the phone.
sa2s::KV device_facts() {
    sa2s::KV kv = sa2s::host_facts();
    for (const auto& [key, name] : {std::pair<const char*, const char*>{"manufacturer", "ro.product.manufacturer"},
                                    {"model", "ro.product.model"},
                                    {"device", "ro.product.device"},
                                    {"android", "ro.build.version.release"},
                                    {"sdk", "ro.build.version.sdk"},
                                    {"soc", "ro.soc.model"},
                                    {"soc_manufacturer", "ro.soc.manufacturer"},
                                    {"abi", "ro.product.cpu.abi"},
                                    {"build", "ro.build.fingerprint"}}) {
        const std::string v = prop(name);
        if (!v.empty()) kv.push_back({key, v});
    }
    return kv;
}

void write_session_info(const std::string& session, const sa2s::KV& run, const sa2s::KV& build) {
    sa2s::KV info = {{"schema", "sa2-session-info/v1"}};
    info.insert(info.end(), run.begin(), run.end());
    info.push_back({"build:", sa2s::json_object(build)});
    info.push_back({"platform:", sa2s::json_object(device_facts())});
    sa2p::write_file_atomic(session + "/session-info.json", sa2s::json_object(info) + "\n");
}

void record_start(const std::string& session, const Sa2Config& cfg, const EnvList& genv, const sa2s::KV& subs,
                  const sa2s::KV& run) {
    for (const auto& [rel, dir] : {std::pair<const char*, const char*>{sa2s::kSaveDir, "/start-saves"},
                                   {sa2s::kConfigDir, "/start-config"}}) {
        const sa2s::Snapshot s = sa2s::snapshot(cfg.read_root, cfg.overlay, rel, false, session + dir);
        if (!s.error.empty()) say("session recorder: %s", s.error.c_str());
    }
    sa2p::write_file_atomic(session + "/game-env.txt", sa2s::env_text(genv, subs));
    write_session_info(session, run, {{"install_table", sa2_install_table().id}});
}

void record_end(const std::string& session, const Sa2Config& cfg, const sa2s::KV& run, const std::string& game_lib,
                const std::string& main_lib) {
    for (const auto& [rel, dir] : {std::pair<const char*, const char*>{sa2s::kSaveDir, "/end-saves"},
                                   {sa2s::kConfigDir, "/end-config"}}) {
        const sa2s::Snapshot s = sa2s::snapshot(cfg.read_root, cfg.overlay, rel, false, session + dir);
        if (!s.error.empty()) say("session recorder: %s", s.error.c_str());
    }
    write_session_info(session, run, {{"install_table", sa2_install_table().id},
                                      {"game_sha256", sha256_file(game_lib)},
                                      {"libmain_sha256", sha256_file(main_lib)}});
}

int play_game(const Args& a) {
    // The installation.
    const std::string cfg_path = sa2_config_path();
    std::string text, why;
    Sa2Config cfg;
    if (cfg_path.empty() || !sa2p::read_file(cfg_path, text)) {
        say("FAIL no installation: %s does not exist (the first-run screen installs it)", cfg_path.c_str());
        return 4;
    }
    if (!sa2_config_parse(text, cfg, &why)) {
        say("FAIL %s: %s", cfg_path.c_str(), why.c_str());
        return 5;
    }
    const Sa2InstallTable& table = sa2_install_table();
    if (cfg.table_id != table.id || cfg.receipt_sha256 != table.receipt_sha256) {
        say("FAIL the installation was verified under another hash table (%s); install again", cfg.table_id.c_str());
        return 3;
    }
    if (!exists_dir(cfg.read_root) || !exists_file(cfg.exe) || cfg.overlay.empty() || cfg.overlay[0] != '/') {
        say("FAIL the installation names a game folder or executable that does not exist (%s, %s); install again",
            cfg.read_root.c_str(), cfg.exe.c_str());
        return 3;
    }
    if (!sa2p::make_dirs(cfg.overlay)) {
        say("FAIL cannot create the overlay %s", cfg.overlay.c_str());
        return 5;
    }
    const std::string game_bin = a.libdir + "/" + kGameLib;
    if (!exists_file(game_bin)) {
        say("FAIL this APK carries no game (%s is missing)", game_bin.c_str());
        return 5;
    }

    // The session.
    const std::string session = new_session("");
    if (session.empty()) {
        say("FAIL cannot create a session folder under %s/sessions", g_files.c_str());
        return 5;
    }
    say("session %s", session.c_str());
    say("game folder %s", cfg.read_root.c_str());
    redirect_output(session + "/player.log");
    sa2l::ignore_broken_pipes();

    const std::string game_fifo = session + "/game.fifo", input_fifo = session + "/input.fifo";
    if (!sa2l::make_fifo(game_fifo, &why) || !sa2l::make_fifo(input_fifo, &why)) {
        say("FAIL %s", why.c_str());
        return 5;
    }
    sa2l::Fd relay_in = sa2l::open_fifo_reader(game_fifo, &why);
    sa2l::Fd keys_fd = relay_in == sa2l::kNoFd ? sa2l::kNoFd : sa2l::open_fifo_holder(input_fifo, &why);
    sa2l::Fd video_r = sa2l::kNoFd, video_w = sa2l::kNoFd, cancel_r = sa2l::kNoFd, cancel_w = sa2l::kNoFd,
             end_r = sa2l::kNoFd, end_w = sa2l::kNoFd;
    if (keys_fd == sa2l::kNoFd || !sa2l::make_pipe(video_r, video_w, &why) ||
        !sa2l::make_pipe(cancel_r, cancel_w, &why) || !sa2l::make_pipe(end_r, end_w, &why)) {
        say("FAIL %s", why.c_str());
        return 5;
    }
    // The game's FIFO holds 1 MB from now on: the relay never resizes it once the game writes
    // (host/launch/sa2_relay.h, "Pace").
    sa2l::set_pipe_size(relay_in, 1u << 20);
    // The pace pipe (the same): its read end the game's, as SA2_REC_PACE_FD; its write end the relay's.
    sa2l::Fd pace_r = sa2l::kNoFd, pace_w = sa2l::kNoFd;
    if (a.pace && !sa2l::make_pipe(pace_r, pace_w, &why)) {
        say("FAIL %s", why.c_str());
        return 5;
    }

    // The game.
    std::vector<std::pair<std::string, std::string>> genv;
    for (auto& kv : sa2l::environment())
        if (kv.first.compare(0, 4, "SA2_") != 0) genv.push_back(kv);
    if (!read_game_env(genv, &why)) {
        say("FAIL %s", why.c_str());
        return 5;
    }
    // The session's defaults for speed (the header, "Speed on a phone"), which game.env.extra's
    // lines replace.
    const sa2cpu::Plan plan = plan_cpus();
    say("cpus: %s", plan.why.c_str());
    if (!plan.guest.empty()) {
        env_set(genv, "SA2_GUEST_HOST_CPUS", plan.guest);
        env_set(genv, "SA2_PLAYER_HOST_CPUS", plan.player);
    }
    env_set(genv, "SA2_REC_AUDIO_WATCH", "0");
    env_set(genv, "SA2_REC_STORE_HASH_EVERY", "64");
    env_set(genv, "SA2_STALL_DUMP_MS", "3000");   // the runtime's stall watch (runtime/sa2_stall.h)
    // Full width (the header): the game's back buffer at the screen's size, unless the screen is no
    // wider than 4:3, where the game's 4:3 layout would be cut at the sides.
    uint32_t screen_w = 0, screen_h = 0;
    if (parse_size(a.screen, screen_w, screen_h) && uint64_t(screen_w) * 3 > uint64_t(screen_h) * 4)
        env_set(genv, "SA2_PLAY_RES", a.screen);
    env_set(genv, "SA2_PLAY_HUD43", "1");   // the HUD at its 4:3 positions; SA2_PLAY_HUD43=0 turns it off
    // The settings screen's (the header): after the session's defaults, which they replace, and
    // before game.env.extra, whose lines replace them.
    bool settings_res = false;
    for (const auto& kv : a.settings) {
        env_set(genv, kv.first, kv.second);
        settings_res |= kv.first == "SA2_PLAY_RES";
        say("settings: %s=%s", kv.first.c_str(), kv.second.c_str());
    }
    bool extra_res = false, extra_aspect = false;
    // A measurement's settings for the game (the thermal unit, 2026-10-12): the SA2_ lines of
    // <external>/game.env.extra, a file adb can push without the app's own access, after the
    // APK's game.env and before this session's own settings, which the extra lines never
    // replace. Each line used is named in the session's report.
    if (!a.external.empty()) {
        std::string extra;
        if (sa2p::read_file(a.external + "/game.env.extra", extra)) {
            size_t at = 0;
            while (at < extra.size()) {
                size_t nl = extra.find('\n', at);
                std::string line = extra.substr(at, nl == std::string::npos ? std::string::npos : nl - at);
                at = nl == std::string::npos ? extra.size() : nl + 1;
                if (!line.empty() && line.back() == '\r') line.pop_back();
                const size_t eq = line.find('=');
                if (line.empty() || line[0] == '#') continue;
                if (eq == std::string::npos || line.compare(0, 4, "SA2_") != 0) {
                    say("game.env.extra: not an SA2_ setting, not used: %s", line.c_str());
                    continue;
                }
                env_set(genv, line.substr(0, eq), line.substr(eq + 1));
                extra_res |= line.compare(0, eq, "SA2_PLAY_RES") == 0;
                extra_aspect |= line.compare(0, eq, "SA2_PLAY_ASPECT") == 0;
                say("game.env.extra: %s", line.c_str());
            }
        }
    }
    say("widescreen: %s", full_width(genv, a.screen, settings_res, extra_res, extra_aspect).c_str());
    // The player's settings (player_setting: its sampler, host/player/prof.h, the touch overlay's
    // and the pads' map) leave the game's environment for this process's, where the player runs
    // (set after player_env below).
    EnvList prof_env;
    for (auto it = genv.begin(); it != genv.end();) {
        if (player_setting(it->first)) {
            prof_env.push_back(*it);
            it = genv.erase(it);
        } else {
            ++it;
        }
    }
    if (!a.audio) genv.erase(std::remove_if(genv.begin(), genv.end(), [](auto& e) { return e.first == "SA2_REC_AUDIO"; }),
                             genv.end());
    env_set(genv, "SA2_INSTALL", cfg.read_root);
    env_set(genv, "SA2_VFS_WRITE_ROOT", cfg.overlay);
    env_set(genv, "SA2_EXE_KIND", "shipped");
    env_set(genv, "SA2_REC_TRACE", game_fifo);
    env_set(genv, "SA2_REC_META", "{\"host\":\"sdl3-gpu\",\"interactive\":true,\"runner\":\"host/android\","
                                  "\"scene\":\"boot\",\"selftest\":false}");
    env_set(genv, "SA2_REC_COUNTS", session + "/counts.runrec");
    env_set(genv, "SA2_INPUT_DI_SOURCE", input_fifo);
    env_set(genv, "SA2_INPUT_DI_LOG", session + "/input.log");
    env_set(genv, "SA2_LAUNCH_TICKS", session + "/ticks.tsv");
    if (pace_r != sa2l::kNoFd) env_set(genv, "SA2_REC_PACE_FD", std::to_string(kPaceFd));
    else genv.erase(std::remove_if(genv.begin(), genv.end(), [](auto& e) { return e.first == "SA2_REC_PACE_FD"; }),
                    genv.end());
    // Controllers, as the launcher's play (host/launch/sa2_launch.cpp): the game gives player 1
    // a pad only when its command line is exactly "-r" (runtime/sa2_dinput.h, "InputWay"), and
    // makes pad objects only at its boot scan, which waits for the player's first pad record. On
    // Android the touch overlay is slot 0's pad from the start, so the pad drives player 1.
    env_set(genv, "SA2_GUEST_CMDLINE", "-r");
    env_set(genv, "SA2_INPUT_PAD_WAIT_MS", "10000");
    // The session recorder (the header): what the game will see, before it starts. Off unless the
    // settings screen's Debug card asks for it (SA2_RECORD_SESSION=1, a --setting the session reads
    // and does not hand to the game), so a session writes what it always has; on, the pads by
    // XInput call too (pad.log).
    const std::string* record_asked = env_find(genv, "SA2_RECORD_SESSION");
    const bool recording = record_asked && *record_asked == "1";
    genv.erase(std::remove_if(genv.begin(), genv.end(), [](auto& e) { return e.first == "SA2_RECORD_SESSION"; }),
               genv.end());
    say("session recorder: %s", recording ? "on (Debug > Record sessions)" : "off");
    if (recording) env_set(genv, "SA2_INPUT_PAD_LOG", session + "/pad.log");
    const sa2s::KV subs = {{session, "<session>"}, {cfg.read_root, "<game>"}, {cfg.overlay, "<overlay>"},
                           {a.libdir, "<apk>/lib"}, {a.external, "<ext>"}, {a.files, "<files>"}};
    sa2s::KV run = {{"runner", "host/android"}, {"mode", "play"}, {"host", "sdl3-gpu"}, {"started", sa2p::now_utc_iso8601()},
                    {"screen", a.screen}, {"orientation", a.orientation.empty() ? "landscape" : a.orientation},
                    {"paced", a.pace ? "true" : "false"}, {"sound", a.audio ? "true" : "false"}};
    for (const auto& kv : a.settings) run.push_back({"setting " + kv.first, kv.second});
    if (recording) record_start(session, cfg, genv, subs, run);
    sa2l::Spawn gs;
    gs.exe = game_bin;
    gs.argv = {game_bin, cfg.exe, "--json", session + "/game.runrec", "--timeout", "0"};
    gs.env = genv;
    gs.cwd = session;
    gs.stdout_path = session + "/game.out";
    gs.stderr_path = session + "/game.log";
    gs.own_group = true;
    if (pace_r != sa2l::kNoFd) gs.fds = {{pace_r, kPaceFd}};
    sa2l::Proc game;
    if (!sa2l::spawn(gs, game, &why)) {
        say("FAIL cannot start the game: %s", why.c_str());
        return 5;
    }
    sa2l::close_fd(pace_r);
    say("game pid %d", int(game.id));

    // Placement: the game's guest threads by the runtime (SA2_GUEST_HOST_CPUS); this thread, the
    // player's, here, after the game started so the game does not inherit it.
    const std::string* guest_cpus = env_find(genv, "SA2_GUEST_HOST_CPUS");
    const std::string* player_cpus = env_find(genv, "SA2_PLAYER_HOST_CPUS");
    say("game: guest threads %s", guest_cpus && !guest_cpus->empty()
                                      ? ("asked onto CPUs " + *guest_cpus + " (SA2_GUEST_HOST_CPUS)").c_str()
                                      : "not placed");
    const std::string own_cpus = own_cpu_list();      // what the game inherited
    const std::string placed = place_this_thread(player_cpus ? *player_cpus : "");
    say("player: its thread (tid %d) and the threads it starts %s", int(gettid()), placed.c_str());

    // The player's descriptors: its own from here on.
    Held held_video, held_cancel, held_end;
    held_video.hold(video_r);
    held_cancel.hold(cancel_r);
    held_end.hold(end_w);
    player_env(session);
    for (const auto& kv : prof_env) setenv(kv.first.c_str(), kv.second.c_str(), 1);
    // The tweaks (the header, "Tweaks"): the game's value, game.env.extra's when it has one, for the
    // player's host tweaks too; and the game's replay settings beside it (a game.env.extra line),
    // which turn the game's tweaks off (runtime/sa2_tweaks.c), so the player's off() says the same
    // (host/player/tweaks.h, "Play only"). player_env unset all three.
    const std::string* tweaks = env_find(genv, "SA2_TWEAKS");
    if (tweaks && !tweaks->empty()) {
        setenv("SA2_TWEAKS", tweaks->c_str(), 1);
        for (const char* k : {"SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"})
            if (const std::string* v = env_find(genv, k)) setenv(k, v->c_str(), 1);
    }
    if (const std::string* hz = env_find(prof_env, "SA2_PLAYER_PROF")) {
        const std::string* dir = env_find(prof_env, "SA2_PLAYER_PROF_OUT");
        say("player: SA2_PLAYER_PROF=%s, its render thread's sampler (host/player/prof.h), into %s", hz->c_str(),
            dir ? dir->c_str() : (session + "/play").c_str());
    }

    // The relay.
    std::atomic<bool> game_alive{true}, player_done{false};
    sa2l::Relay relay(relay_in, video_w, a.pace ? 1u : 0u, kRelayCap, pace_w);
    // The stall watch (sa2_relay.h): the nudge goes to the game by the pid it started with, only
    // while the session still counts it alive (the watch thread reaps it).
    const sa2l::Proc game_started = game;
    relay.nudge = [game_started, &game_alive] {
        return game_alive.load() && sa2l::send_signal(game_started, sa2l::Signal::Nudge);
    };
    relay.note = [](const std::string& line) { say("%s", line.c_str()); };
    Monitor mon;
    relay.on_writer_start = [&mon] {
        clockid_t c{};
        if (pthread_getcpuclockid(pthread_self(), &c) != 0) return;
        mon.writer_clk = c;
        mon.writer_tid.store(int32_t(gettid()), std::memory_order_release);
    };
    std::thread relay_thread([&] {
        mon.relay_tid = int32_t(gettid());
        relay.run(game_alive);
    });

    // The monitor: ADPF and the reports on speed.
    mon.game_pid = int(game.id);
    mon.player_thread = pthread_self();
    mon.player_tid = int32_t(gettid());
    mon.relay_thread = relay_thread.native_handle();
    mon.relay = &relay;
    const std::string* adpf = env_find(genv, "SA2_ADPF");
    mon.adpf = !(adpf && *adpf == "0");
    if (const std::string* t = env_find(genv, "SA2_ADPF_TARGET_US")) {
        const long us = std::strtol(t->c_str(), nullptr, 10);
        if (us >= 1000 && us <= 1000000) mon.target_ns = int64_t(us) * 1000;
        else say("SA2_ADPF_TARGET_US=%s: not 1000 to 1000000, so the target stays 16667 us", t->c_str());
    }
    if (placed.compare(0, 3, "on ") == 0) mon.player_cpus = *player_cpus;
    cpu_set_t asked;
    mon.guest_clock_cpus = guest_cpus && sa2cpu::parse_list(*guest_cpus, asked) ? *guest_cpus : own_cpus;
    mon.player_clock_cpus = mon.player_cpus.empty() ? own_cpus : mon.player_cpus;
    std::thread mon_thread([&] { mon.run(); });
    // The game's own full-width line (runtime/sa2_widescreen.c), said once it is in game.log:
    // ReadConfig runs once, at the guest's boot, within seconds.
    std::thread ws_thread([&] {
        if (env_find(genv, "SA2_PLAY_RES") || env_find(genv, "SA2_PLAY_ASPECT"))
            relay_line(session + "/game.log", "widescreen: ", "game: ", player_done);
    });
    // The game's tweaks line (runtime/sa2_tweaks.c), printed at its start, and the player's
    // (host/player/tweaks.cpp), printed at its own.
    std::thread tweaks_thread([&] {
        if (tweaks && !tweaks->empty()) relay_line(session + "/game.log", "sa2 tweaks: ", "game: ", player_done);
    });
    std::thread host_tweaks_thread([&] {
        if (tweaks && !tweaks->empty())
            relay_line(session + "/player.log", "sa2 host tweaks (player): ", "player: ", player_done);
    });
    // The player's line for the picture's place (its first Present after the first frame).
    std::thread picture_thread([&] {
        if (env_find(prof_env, "SA2_PLAYER_PICTURE_Y"))
            relay_line(session + "/player.log", "picture: ", "player: ", player_done);
    });

    // The watch: the game's exit or the player's end request stops the game; the player is then
    // given kFeedGrace to read the stream to its END.
    std::string ended_by, stop_how;
    std::thread watch([&] {
        while (!player_done.load()) {
            if (sa2l::wait_exit(game, 0)) {
                ended_by = "the game ended (exit " + std::to_string(game.status) + ")";
                break;
            }
            if (sa2l::wait_readable(end_r, 20) > 0) {
                char byte = 0;
                ended_by = sa2l::read_some(end_r, &byte, 1) == 1 ? "the player asked for the end" : "the player ended";
                break;
            }
        }
        if (ended_by.empty()) ended_by = "the player ended";
        relay.end_watch();
        mon.stopping = true;
        stop_how = stop_game(game);
        game_alive = false;
        // Said now, not only at the session's end: a player held by pause-unfocused (the app visible
        // but not in focus) reads no stream until the cancel below, so this is the line that names it.
        if (!player_done.load())
            say("%s; the game is stopped (by %s); the player has %.0f s to read its END", ended_by.c_str(),
                stop_how.c_str(), kFeedGrace);
        const double t0 = sa2p::monotonic_seconds();
        while (!player_done.load() && sa2p::monotonic_seconds() - t0 < kFeedGrace) sa2l::sleep_ms(50);
        if (!player_done.load()) {
            say("the player did not reach the END in %.0f s: cancelled", kFeedGrace);
            const char byte = 1;
            sa2l::write_all(cancel_w, &byte, 1);
        }
    });

    std::vector<std::string> args = {"sa2_player", "--interactive",
                                     "--video-fd", std::to_string(video_r),
                                     "--cancel-fd", std::to_string(cancel_r),
                                     "--end-fd", std::to_string(end_w),
                                     "--stage", "3", "--report", session + "/player-report.runrec",
                                     "--report-every", "600", "--input-path", input_fifo};
    if (a.pace) args.push_back("--pace");
    if (a.audio) args.push_back("--audio");
    const int rc = run_player(args);
    player_done = true;
    // A player that ended before it took its descriptors leaves them open: closed here, so the
    // relay's writes fail instead of waiting on a reader that is gone.
    held_video.close_if_ours();
    held_cancel.close_if_ours();
    held_end.close_if_ours();
    mon.stop = true;
    mon_thread.join();
    ws_thread.join();
    tweaks_thread.join();
    host_tweaks_thread.join();
    picture_thread.join();
    watch.join();
    relay_thread.join();
    const sa2l::RelayStats rs = relay.stats();
    sa2l::close_fd(cancel_w);
    sa2l::close_fd(end_r);
    sa2l::close_fd(keys_fd);
    sa2l::close_fd(relay_in);
    // A game killed by a signal (SIGTERM, the stop's own, aside) after its recorder wrote its last line (the
    // status file's "closed: N records", host/sa2_present_rec.c rec_exit, the last thing its exit does) had
    // finished everything it writes, so the signal came in the process's teardown: said on the line, for the
    // crash report. It is still a crash (CrashReport.Outcome.of).
    std::string closed;
    if (game.status < 0 && game.status != -15) {
        std::string st;
        if (sa2p::read_file(game_fifo + ".status", st, 4096) && st.compare(0, 8, "closed: ") == 0 &&
            st.find(" records, ") != std::string::npos)
            closed = "; the game had closed its records";
    }
    if (recording) record_end(session, cfg, run, game_bin, a.libdir + "/libmain.so");
    say("ended: %s; the game stopped by %s (status %d); the player exited %d%s", ended_by.c_str(), stop_how.c_str(),
        game.status, rc, closed.c_str());
    say("relay: %llu frames in, %llu out, %llu bytes%s", (unsigned long long)rs.frames_in,
        (unsigned long long)rs.frames_out, (unsigned long long)rs.bytes_in,
        rs.framing_problem.empty() ? "" : (", " + rs.framing_problem).c_str());
    // What can name a taker of the game's bytes, beside the game's own count (sa2_relay.h, "Loss").
    for (const std::string& line : sa2l::relay_report(rs, game_fifo + ".status")) say("%s", line.c_str());
    if (rs.frames_in == 0)
        say("FAIL the game presented nothing: see %s/game.log", session.c_str());
    else if (rc == 0)
        say("OK the session ended");
    else
        say("FAIL the player exited %d: see %s/player.log", rc, session.c_str());
    return rc;
}

}  // namespace

// The app's entry point (Sa2Activity.getMainFunction). Arguments (Sa2Activity.getArguments):
//   --files DIR --cache DIR --libdir DIR [--external DIR] [--screen WxH]
//   [--orientation landscape|portrait|auto] [--setting SA2_K=V ...]
//   [--stream FILE [--max-frames N]] [--no-audio] [--no-pace]
extern "C" __attribute__((visibility("default"))) int sa2_android_main(int argc, char** argv) {
    Args a;
    for (int i = 1; i < argc; i++) {
        const std::string k = argv[i];
        auto val = [&]() -> std::string { return i + 1 < argc ? argv[++i] : ""; };
        if (k == "--files") a.files = val();
        else if (k == "--cache") a.cache = val();
        else if (k == "--external") a.external = val();
        else if (k == "--libdir") a.libdir = val();
        else if (k == "--screen") a.screen = val();
        else if (k == "--orientation") a.orientation = val();
        else if (k == "--setting") {
            const std::string kv = val();
            const size_t eq = kv.find('=');
            if (eq != std::string::npos && kv.compare(0, 4, "SA2_") == 0)
                a.settings.emplace_back(kv.substr(0, eq), kv.substr(eq + 1));
            else
                __android_log_print(ANDROID_LOG_WARN, kTag, "--setting %s: not SA2_K=V, not used", kv.c_str());
        }
        else if (k == "--stream") a.stream = val();
        else if (k == "--max-frames") a.max_frames = val();
        else if (k == "--no-audio") a.audio = false;
        else if (k == "--no-pace") a.pace = false;
        else __android_log_print(ANDROID_LOG_WARN, kTag, "unknown argument %s", k.c_str());
    }
    if (a.files.empty() || a.libdir.empty()) {
        __android_log_write(ANDROID_LOG_ERROR, kTag, "sa2_android_main needs --files and --libdir");
        return 2;
    }
    g_files = a.files;
    g_report = a.files + "/last-session.txt";
    g_lines.clear();
    // The installer's and the session's folders (host/install/sa2_platform_posix.cpp): the app's own.
    setenv("HOME", a.files.c_str(), 1);
    setenv("XDG_CONFIG_HOME", (a.files + "/config").c_str(), 1);
    setenv("XDG_DATA_HOME", (a.files + "/data").c_str(), 1);
    if (!a.cache.empty()) setenv("TMPDIR", a.cache.c_str(), 1);
    // SDL's hint for the app's orientation (the header): landscape either way up unless the
    // settings screen chose portrait or either.
    const char* orientations = a.orientation == "portrait" ? "Portrait PortraitUpsideDown"
                             : a.orientation == "auto"     ? "LandscapeLeft LandscapeRight Portrait PortraitUpsideDown"
                                                           : "LandscapeLeft LandscapeRight";
    setenv("SDL_ORIENTATIONS", orientations, 1);
    say("sa2recomp %s", a.stream.empty() ? "play" : "stream");
    if (!a.orientation.empty() && a.orientation != "landscape") say("orientation: %s", a.orientation.c_str());
    return a.stream.empty() ? play_game(a) : play_stream(a);
}
