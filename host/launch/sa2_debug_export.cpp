// sa2_debug_export.cpp: Debug Export (sa2_debug_export.h).
#include "sa2_debug_export.h"

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <ctime>
#include <tuple>
#include <vector>

#include "sa2_platform.h"
#include "sha256.h"

#ifndef _WIN32
#include <dlfcn.h>
#endif

namespace sa2x {
namespace {

std::string sha256_hex(const std::string& d) {
    Sha256 h;
    h.update(d.data(), d.size());
    return h.hex();
}

// ------------------------------------------------------------------ what each entry is

// The descriptions MANIFEST.txt gives, by the entry's path under session/ (a prefix ending in
// '/' names a folder). host/android/java's DebugExport.java keeps the same table for the app's
// sessions; a name missing from both is still exported, described as "other".
struct Known {
    const char* name;
    const char* what;
};
const Known kKnown[] = {
    {"session-info.json", "build identity (release manifest, game, player sha256), platform, device, settings"},
    {"game-env.txt", "every SA2_ setting the game ran with, paths redacted"},
    {"start-saves/", "the saves the game saw at its start (resource/gd_PC/SAVEDATA); index.tsv says from where"},
    {"start-config/", "the Config/ files the game saw at its start"},
    {"end-saves/", "the saves after the game ended"},
    {"end-config/", "the Config/ files after the game ended"},
    {"input.log", "keyboard polls (SA2_INPUT_DI_LOG): replays as SA2_INPUT_DI_SCRIPT"},
    {"pad.log", "gamepad and touch-overlay pad changes by XInput call (SA2_INPUT_PAD_LOG): replays as "
                "SA2_INPUT_PAD_SCRIPT"},
    {"ticks.tsv", "the launch observer: tick, presents, game mode, menu at every game loop (SA2_LAUNCH_TICKS)"},
    {"game.runrec", "the game's run record: runtime counters, file log, the trap if any"},
    {"game.out", "the game's standard output (normally its run record again)"},
    {"game.log", "the game's standard error: runtime notes and traps"},
    {"counts.runrec", "the recorder's call counts"},
    {"counts.synthetic.json", "the recorder's call counts (selftest)"},
    {"game.fifo.status", "the stream's closing line: records, bytes, frames"},
    {"launch.runrec", "the launcher's record (schema sa2-s7-launch-run/v1)"},
    {"launcher.log", "everything the launcher said"},
    {"player.log", "the player's standard output: renderer, pads, picture"},
    {"player.err", "the player's standard error"},
    {"player-report.runrec", "the player's report: frames, pace, audio, pads"},
    {"d3d9gpu.runrec", "the SDL_gpu renderer's report"},
    {"d3d9gpu.runrec.keys.json", "the renderer report's pipeline and texture keys"},
    {"pace.tsv", "the player's pacing per frame"},
    {"verify.log", "the installer's verify: output"},
    {"verify.err", "the installer's verify: errors"},
    {"verify.runrec", "the installer's verify: record"},
    {"dxvk-log/", "DXVK Native's logs (dxvk-native host)"},
    {"play/", "the player's output folder (SA2_PLAY_OUT)"},
    {"last-session.txt", "the Android session's report (what the setup screen shows)"},
    {"logcat.txt", "the app's logcat at export time (Android)"},
};

std::string describe(const std::string& rel) {
    for (const Known& k : kKnown) {
        const size_t n = std::strlen(k.name);
        if (k.name[n - 1] == '/' ? rel.compare(0, n, k.name) == 0 : rel == k.name) return k.what;
    }
    return "other";
}

// Left out by name: the stream (never meant to be on disk, gigabytes when it is), the FIFOs (not
// regular files, so never listed anyway), the selftest's overlay (its saves are end-saves/) and
// DXVK's shader cache.
bool left_out_by_name(const std::string& rel, std::string& why) {
    auto has = [&](const char* s) { return rel.find(s) != std::string::npos; };
    if (rel.compare(0, 8, "overlay/") == 0) why = "the selftest's own write root (end-saves/ holds its saves)";
    else if (rel.compare(0, 11, "dxvk-cache/") == 0) why = "DXVK's shader cache";
    else if (has("stream.bin") || (rel.size() > 4 && rel.compare(rel.size() - 4, 4, ".sa2") == 0))
        why = "a D3D stream";
    return !why.empty();
}

bool is_text(const std::string& rel) {
    if (rel.find("saves/") != std::string::npos && rel.find("index.tsv") == std::string::npos) return false;
    for (const char* ext : {".log", ".txt", ".tsv", ".json", ".runrec", ".env", ".cfg", ".ini", ".err", ".out",
                            ".status", ".manifest", ".xml"}) {
        const size_t n = std::strlen(ext);
        if (rel.size() >= n && rel.compare(rel.size() - n, n, ext) == 0) return true;
    }
    return false;
}

// ------------------------------------------------------------------ the ZIP

uint32_t crc32_of(const std::string& d) {
    static uint32_t table[256];
    static bool made = false;
    if (!made) {
        for (uint32_t i = 0; i < 256; i++) {
            uint32_t c = i;
            for (int k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320u ^ (c >> 1) : c >> 1;
            table[i] = c;
        }
        made = true;
    }
    uint32_t c = 0xFFFFFFFFu;
    for (unsigned char b : d) c = table[(c ^ b) & 0xFF] ^ (c >> 8);
    return c ^ 0xFFFFFFFFu;
}

// zlib's raw deflate, loaded at run time: the z_stream layout and deflateInit2_'s contract are
// zlib's stable ABI (zlib.h), declared here so the launcher needs neither zlib's header nor its
// library to build.
struct ZStream {
    const unsigned char* next_in;
    unsigned avail_in;
    unsigned long total_in;
    unsigned char* next_out;
    unsigned avail_out;
    unsigned long total_out;
    const char* msg;
    void* state;
    void* zalloc;
    void* zfree;
    void* opaque;
    int data_type;
    unsigned long adler;
    unsigned long reserved;
};
using DeflateInit2 = int (*)(ZStream*, int, int, int, int, int, const char*, int);
using Deflate = int (*)(ZStream*, int);
using DeflateEnd = int (*)(ZStream*);

struct Zlib {
    DeflateInit2 init = nullptr;
    Deflate deflate = nullptr;
    DeflateEnd end = nullptr;
    Zlib() {
#ifndef _WIN32
        void* h = dlopen("libz.so.1", RTLD_NOW | RTLD_LOCAL);
        if (!h) h = dlopen("libz.so", RTLD_NOW | RTLD_LOCAL);
        if (!h) return;
        init = reinterpret_cast<DeflateInit2>(dlsym(h, "deflateInit2_"));
        deflate = reinterpret_cast<Deflate>(dlsym(h, "deflate"));
        end = reinterpret_cast<DeflateEnd>(dlsym(h, "deflateEnd"));
        if (!init || !deflate || !end) init = nullptr;
#endif
    }
    // Raw deflate (window bits -15, as ZIP's method 8 holds it); false when unavailable or failed.
    bool pack(const std::string& in, std::string& out) const {
        if (!init) return false;
        ZStream z;
        std::memset(&z, 0, sizeof z);
        if (init(&z, 6, 8, -15, 8, 0, "1.2.11", int(sizeof z)) != 0) return false;
        out.assign(in.size() + in.size() / 1000 + 64, '\0');
        z.next_in = reinterpret_cast<const unsigned char*>(in.data());
        z.avail_in = unsigned(in.size());
        z.next_out = reinterpret_cast<unsigned char*>(&out[0]);
        z.avail_out = unsigned(out.size());
        const int rc = deflate(&z, 4 /* Z_FINISH */);
        const bool ok = rc == 1 /* Z_STREAM_END */;
        out.resize(z.total_out);
        end(&z);
        return ok;
    }
};

struct ZipWriter {
    FILE* f = nullptr;
    std::string central;
    uint16_t count = 0;
    uint64_t offset = 0;
    uint16_t dos_time = 0, dos_date = 0;
    bool ok = true;
    const Zlib& z;

    explicit ZipWriter(const Zlib& zl) : z(zl) {
        std::time_t t = std::time(nullptr);
        std::tm tm{};
#ifdef _WIN32
        gmtime_s(&tm, &t);
#else
        gmtime_r(&t, &tm);
#endif
        dos_time = uint16_t((tm.tm_hour << 11) | (tm.tm_min << 5) | (tm.tm_sec / 2));
        dos_date = uint16_t(((tm.tm_year - 80) << 9) | ((tm.tm_mon + 1) << 5) | tm.tm_mday);
    }
    static void le(std::string& s, uint64_t v, int n) {
        for (int i = 0; i < n; i++) s += char((v >> (8 * i)) & 0xFF);
    }
    void put(const std::string& s) {
        if (ok && std::fwrite(s.data(), 1, s.size(), f) != s.size()) ok = false;
        offset += s.size();
    }
    void add(const std::string& name, const std::string& data, bool compress) {
        std::string packed;
        const bool deflated = compress && data.size() > 64 && z.pack(data, packed) && packed.size() < data.size();
        const std::string& body = deflated ? packed : data;
        const uint32_t crc = crc32_of(data);
        std::string h;
        le(h, 0x04034b50u, 4);
        le(h, 20, 2);                       // version needed
        le(h, 0x0800, 2);                   // UTF-8 names
        le(h, deflated ? 8 : 0, 2);
        le(h, dos_time, 2);
        le(h, dos_date, 2);
        le(h, crc, 4);
        le(h, body.size(), 4);
        le(h, data.size(), 4);
        le(h, name.size(), 2);
        le(h, 0, 2);
        const uint64_t at = offset;
        put(h + name);
        put(body);
        std::string c;
        le(c, 0x02014b50u, 4);
        le(c, 0x0314, 2);                   // made by: Unix, 2.0
        le(c, 20, 2);
        le(c, 0x0800, 2);
        le(c, deflated ? 8 : 0, 2);
        le(c, dos_time, 2);
        le(c, dos_date, 2);
        le(c, crc, 4);
        le(c, body.size(), 4);
        le(c, data.size(), 4);
        le(c, name.size(), 2);
        le(c, 0, 2);
        le(c, 0, 2);
        le(c, 0, 2);
        le(c, 0, 2);
        le(c, uint64_t(0100644) << 16, 4);  // external attributes: a regular file, rw-r--r--
        le(c, at, 4);
        central += c + name;
        count++;
    }
    void finish() {
        const uint64_t at = offset;
        put(central);
        std::string e;
        le(e, 0x06054b50u, 4);
        le(e, 0, 2);
        le(e, 0, 2);
        le(e, count, 2);
        le(e, count, 2);
        le(e, central.size(), 4);
        le(e, at, 4);
        le(e, 0, 2);
        put(e);
    }
};

// Every regular file under `dir`, as paths relative to it ('/'-separated), sorted.
void walk(const std::string& dir, const std::string& rel, std::vector<std::string>& out) {
    std::vector<std::string> names;
    if (!sa2p::list_dir(rel.empty() ? dir : dir + "/" + rel, names)) return;
    std::sort(names.begin(), names.end());
    for (const std::string& n : names) {
        const std::string r = rel.empty() ? n : rel + "/" + n;
        sa2p::Stat st;
        if (!sa2p::stat_path(dir + "/" + r, st) || st.is_link) continue;
        if (st.is_dir) walk(dir, r, out);
        else if (st.is_file) out.push_back(r);
    }
}

std::string newest_session(const std::string& root) {
    std::vector<std::string> names, dirs;
    if (!sa2p::list_dir(root, names)) return "";
    for (const std::string& n : names) {
        sa2p::Stat st;
        if (sa2p::stat_path(root + "/" + n, st) && st.is_dir) dirs.push_back(n);
    }
    std::sort(dirs.begin(), dirs.end());      // the names start with their UTC stamp
    return dirs.empty() ? "" : dirs.back();
}

std::string human(unsigned long long b) {
    char buf[32];
    if (b >= (1ull << 20)) std::snprintf(buf, sizeof buf, "%.1f MB", double(b) / double(1u << 20));
    else std::snprintf(buf, sizeof buf, "%.1f KB", double(b) / 1024.0);
    return buf;
}

}  // namespace

Result debug_export(const Request& r) {
    Result res;
    std::string session = r.session;
    if (session.empty()) session = newest_session(r.sessions_root);
    if (session.empty()) {
        res.message = "no session under " + sa2s::redact(r.sessions_root, r.subs) + " to export";
        return res;
    }
    std::string dir = session;
    sa2p::Stat st;
    if (!(sa2p::stat_path(dir, st) && st.is_dir)) dir = r.sessions_root + "/" + session;
    if (!(sa2p::stat_path(dir, st) && st.is_dir)) {
        res.message = "no session folder " + session + " (a name under " + sa2s::redact(r.sessions_root, r.subs) +
                      ", or a path)";
        return res;
    }
    std::string real;
    if (sa2p::real_path(dir, real)) dir = real;
    const std::string id = dir.substr(dir.find_last_of('/') + 1);
    res.session = id;
    res.zip = r.out;
    if (res.zip.empty()) {
        const std::string exports = r.sessions_root.substr(0, r.sessions_root.find_last_of('/')) + "/exports";
        if (!sa2p::make_dirs(exports)) {
            res.message = "cannot create " + sa2s::redact(exports, r.subs);
            return res;
        }
        res.zip = exports + "/sa2-debug-" + id + ".zip";
    }
    sa2s::KV subs = r.subs;
    subs.insert(subs.begin(), {dir, "<session>"});

    Zlib zlib;
    ZipWriter zip(zlib);
    const std::string tmp = res.zip + ".part";
    zip.f = std::fopen(tmp.c_str(), "wb");
    if (!zip.f) {
        res.message = "cannot write " + sa2s::redact(res.zip, r.subs);
        return res;
    }
    std::string manifest_rows;
    auto add = [&](const std::string& name, const std::string& data, const std::string& what) {
        const bool text = is_text(name);
        const std::string body = text ? sa2s::redact(data, subs) : data;
        zip.add(name, body, text);
        manifest_rows += name + "\t" + std::to_string(body.size()) + "\t" + sha256_hex(body) + "\t" + what + "\n";
        res.entries++;
        res.bytes += body.size();
    };
    std::string notes;

    // session/: every regular file of the folder.
    std::vector<std::string> files;
    walk(dir, "", files);
    bool has_end_saves = false;
    bool has_start_saves = false;
    for (const std::string& rel : files) {
        std::string why;
        sa2p::Stat fs;
        sa2p::stat_path(dir + "/" + rel, fs);
        if (!left_out_by_name(rel, why) && fs.size > kMaxEntryBytes) why = "larger than " + human(kMaxEntryBytes);
        std::string data;
        if (why.empty() && !sa2p::read_file(dir + "/" + rel, data, size_t(kMaxEntryBytes) + 1)) why = "could not be read";
        if (!why.empty()) {
            notes += "# left out: session/" + rel + " (" + human(fs.size) + "): " + why + "\n";
            res.left_out++;
            continue;
        }
        has_end_saves |= rel.compare(0, 10, "end-saves/") == 0;
        has_start_saves |= rel.compare(0, 12, "start-saves/") == 0;
        add("session/" + rel, data, describe(rel));
    }
    // A session the recorder did not record (it is off unless asked for) keeps no start state.
    if (!has_start_saves)
        notes += "# not replayable: the session recorder was off (--record-session, or SA2_RECORD_SESSION=1 in "
                 "share/game.env; Android: Game settings > Debug > Record sessions), so there is no "
                 "session/start-saves/ or pad.log\n";
    // end-saves/ at export time, for a session that has none.
    if (!has_end_saves && !r.overlay.empty()) {
        std::string index = "# resource/gd_PC/SAVEDATA as the game sees it, taken at export time: name, source, bytes, "
                            "sha256\n";
        for (const sa2s::SeenFile& f : sa2s::seen_files(r.read_root, r.overlay, sa2s::kSaveDir, false)) {
            std::string data;
            if (!sa2p::read_file(f.path, data)) continue;
            add("session/end-saves/" + f.name, data, "a save at export time (the session kept none at its end)");
            index += f.name + "\t" + f.source + "\t" + std::to_string(data.size()) + "\t" + sha256_hex(data) + "\n";
        }
        add("session/end-saves/index.tsv", index, "the saves at export time: where each came from");
        notes += "# session/end-saves/ was taken at export time: the session kept none at its end\n";
    }
    // build/ and install/.
    if (!r.release.empty()) {
        for (const auto& [from, name, what] :
             {std::tuple<std::string, std::string, std::string>{r.release + "/release.manifest", "build/release.manifest",
                                                                "the release's manifest: every file's sha256"},
              {r.release + "/share/game.env", "build/game.env", "the release's game settings"}}) {
            std::string data;
            if (sa2p::read_file(from, data)) add(name, data, what);
        }
    }
    if (!r.install_cfg.empty()) {
        std::string data;
        if (sa2p::read_file(r.install_cfg, data)) add("install/sa2recomp.cfg", data, "the installer's config");
    }

    std::string manifest = "# sa2recomp Debug Export, format 1 (host/launch/sa2_debug_export.h)\n"
                           "# session: " + id + "\n# exported: " + sa2p::now_utc_iso8601() +
                           "\n# paths in text entries: ~ the home folder, <session> the session folder, <game> the "
                           "game folder, <overlay> the write root, <data> the data folder\n" +
                           notes + "# path\tbytes\tsha256\twhat it is\n" + manifest_rows;
    zip.add("MANIFEST.txt", manifest, true);
    res.entries++;
    zip.finish();
    const bool wrote = zip.ok && std::fclose(zip.f) == 0;
    if (!wrote || zip.offset >= (1ull << 32) || std::rename(tmp.c_str(), res.zip.c_str()) != 0) {
        std::remove(tmp.c_str());
        res.message = "cannot write " + sa2s::redact(res.zip, r.subs) +
                      (zip.offset >= (1ull << 32) ? " (4 GiB or more)" : "");
        return res;
    }
    res.ok = true;
    res.message = std::to_string(res.entries) + " entries (" + human(zip.offset) + "), " +
                  std::to_string(res.left_out) + " left out" + (zlib.init ? "" : ", stored without zlib");
    return res;
}

}  // namespace sa2x
