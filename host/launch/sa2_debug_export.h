// sa2_debug_export.h: Debug Export: one ZIP of a session, everything needed
// to read what happened and to replay it (tools/s7/replay_export.py), and nothing the person
// would not want to send: no stream (the game's D3D stream goes through a FIFO and is never on
// disk; a stream file found in the folder is left out by name), and every text file's home
// folder, game folder, session folder and data folder written as ~, <game>, <session> and <data>.
//
//   sa2recomp --debug-export [SESSION] [--out FILE]
//
// SESSION is a session folder's name under <data>/sessions or a path to one; the newest session
// when absent. FILE defaults to <data>/exports/sa2-debug-<session>.zip.
//
// The ZIP's layout (MANIFEST.txt inside lists every entry, its size, sha256 and what it is, so a
// reader can open only the logs that matter):
//
//   MANIFEST.txt               the list; its first lines name the export's format and session
//   session/...                every regular file of the session folder, under its own name,
//                              text redacted (sa2_session_files.h for start-saves/, end-saves/,
//                              start-config/, end-config/, session-info.json and game-env.txt;
//                              input.log, pad.log, ticks.tsv, the records and the logs), except
//                              the FIFOs, the selftest's own overlay/ (end-saves/ holds its saves),
//                              stream files and any file over kMaxEntryBytes (each named in the
//                              manifest as left out, with why)
//   session/end-saves/...      when the session has none (a session from before the recorder, or
//                              one whose launcher was killed): the overlay's saves at export time,
//                              said so in the manifest
//   build/release.manifest     the release's manifest and settings (desktop)
//   build/game.env
//   install/sa2recomp.cfg      the installer's config, redacted
//
// Text entries are stored deflated through the host's zlib, loaded at run time (as the release
// leaves zlib the host's, release.manifest "host_libraries"); without it (Windows, or no libz.so.1)
// every entry is stored as is. No entry or archive reaches 4 GiB, so no ZIP64.
#pragma once

#include <string>

#include "sa2_session_files.h"

namespace sa2x {

constexpr unsigned long long kMaxEntryBytes = 256ull << 20;

struct Request {
    std::string sessions_root;     // <data>/sessions
    std::string session;           // a name under it, a path, or "" for the newest
    std::string out;               // the ZIP; "" for <data>/exports/sa2-debug-<session>.zip
    std::string read_root, overlay;        // the installation's, for end-saves/ when absent
    std::string release;           // the release folder (build/), "" for none
    std::string install_cfg;       // the installer's config (install/), "" for none
    sa2s::KV subs;                 // redactions: {path, name}
};

struct Result {
    bool ok = false;
    std::string zip, session, message;
    size_t entries = 0, left_out = 0;
    unsigned long long bytes = 0;
};

Result debug_export(const Request& r);

}  // namespace sa2x
