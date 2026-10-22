// sa2_session_files.h: what a session folder keeps so the session can be replayed and its Debug
// Export read without the machine it ran on (the session recorder). Shared by
// the launcher (host/launch/sa2_launch.cpp) and the Android app's native session
// (host/android/native/sa2_android_session.cpp); both write the same files under the session:
//
//   session-info.json  the build (release manifest, game, player and launcher sha256 where the
//                      runner can name them), the platform and device, the runner, the start time
//                      and the settings that shape the run; schema sa2-session-info/v1.
//   game-env.txt       every SA2_ setting the game was started with, one KEY=VALUE a line, with
//                      the session folder written <session>, the game folder <game>, the write
//                      root <overlay> and the home folder ~ (redact below).
//   start-saves/       the save folder the game sees at its start (resource/gd_PC/SAVEDATA), taken
//   start-config/      before the game runs, and Config/ the same way; each with index.tsv naming
//                      every file's source (overlay or game folder), size and sha256.
//   end-saves/         the same two after the game has ended.
//   end-config/
//
// "The folder the game sees" is the runtime's view (runtime/sa2_files.c): the write root's files
// over the read root's, names matched case-blind, and the read root's saves left out when the run
// hides them (SA2_VFS_HIDE_INSTALL_SAVES, the selftest's). A replay seeds a fresh write root with
// these files and hides the read root's saves, so it sees the same bytes under the same names
// whatever saves its own game folder holds (tools/s7/replay_export.py).
//
// Nothing here is needed for the game to run: a snapshot that fails is said in its index and in
// the runner's log, never a reason to refuse the session. The copies are small (a save folder is
// at most a few hundred KB) and made outside the game's process, before it starts and after it
// has ended, so they never race a save being written.
#pragma once

#include <string>
#include <utility>
#include <vector>

namespace sa2s {

using KV = std::vector<std::pair<std::string, std::string>>;

// The save folder and the config folder, as the game names them (case-blind).
constexpr const char* kSaveDir = "resource/gd_PC/SAVEDATA";
constexpr const char* kConfigDir = "Config";

struct Snapshot {
    size_t files = 0;
    std::string error;                  // "" when every file was copied
};

// One file the game sees: its name, where it is, and from which root ("overlay" or "game-folder").
struct SeenFile {
    std::string name, path, source;
};

// The files the game sees in `rel` (the write root's over the read root's, names matched
// case-blind; the read root's skipped when `hide_read_root`), sorted by name.
std::vector<SeenFile> seen_files(const std::string& read_root, const std::string& write_root, const std::string& rel,
                                 bool hide_read_root);

// seen_files copied into `dest`, with dest/index.tsv. `dest` is created.
Snapshot snapshot(const std::string& read_root, const std::string& write_root, const std::string& rel,
                  bool hide_read_root, const std::string& dest);

// `text` with each `from` replaced by its `to`, the longest `from` first, so a session folder
// inside the home folder becomes <session> and not ~/... .
std::string redact(const std::string& text, KV subs);

// The SA2_ entries of `env`, KEY=VALUE a line in the order given, redacted.
std::string env_text(const KV& env, const KV& subs);

// Facts about the machine for session-info.json: os, arch, kernel, cpu model and count, memory,
// the distribution's or the device's name. Read from /proc and uname; empty values are left out.
KV host_facts();

// A flat JSON object of strings, in order (`raw` values whose key ends in ':' are written as
// they are, the ':' dropped: a nested object the caller built).
std::string json_object(const KV& kv);
std::string json_quote(const std::string& s);

}  // namespace sa2s
