// tweaks.h: the host's play tweaks, as the player and the launcher read them.
//
// Subtitle: SA2_TWEAKS names the game's tweaks and the host's alike. runtime/sa2_tweaks.h is the one
// list of both, included here as it is: SA2_TWEAK_LIST, the game's, which runtime/sa2_tweaks.c
// reads in the game, and SA2_TWEAK_HOST_LIST, the host's, whose lines this header makes into the
// host's bits (sa2tw::SA2_TWEAK_<NAME>, its place in that list) and tweaks.cpp reads. Each host tweak
// has a home, where its change is made: "player" (host/player and host/d3d9gpu), "launcher"
// (host/launch) or "app" (the Android app's Java, which asks the settings screen's switch and not
// this reader). runtime/sa2_tweaks.h, "Add a host tweak", has the steps that add one.
//
// The rules, the game's own (runtime/sa2_tweaks.h) as they apply to a host:
//
//   - Play only. A process reads the value once, at its start, and only in play: the player in a
//     play session (--interactive with --input-path, and no synthetic input: not_play below), the
//     launcher outside its selftest and a session it gives the player synthetic input in (both drop
//     the line, for the game and the player too), never either when the game's environment has
//     SA2_REPLAY_TICKS or SA2_REPLAY_WORDS set (off below). That is the game's environment, the one
//     runtime/sa2_tweaks.c asks, not the host's own: the launcher asks the one it gives the game
//     (game.env's lines), and the launcher and the Android session give the player those two
//     settings beside SA2_TWEAKS, so the game, the player and the launcher reach one verdict. Out of
//     play every host tweak is off, and a value that is set is said to be ignored, with why.
//   - One report line, on stderr, whenever the value is set (the launcher's too): the player's
//     "sa2 host tweaks (player): <the reports of its tweaks that are on, or none on> (SA2_TWEAKS=...)".
//     A host tweak of another home is named in it as that home's ("not the player's: ..."). A game
//     tweak's name is the game's, and a name in neither list is the game's to name: runtime/sa2_tweaks.c
//     says so in the game's line, which lists the tweaks there are. A tweak's own lines after it
//     begin "sa2 host tweaks: <report>: ".
//   - Off is the host as it was: each change tests sa2tw::on(its bit) first and otherwise does
//     nothing, so with no value set (and in every replay) the player and the launcher are the
//     builds they were.
//   - By name, never silently: a case a tweak leaves alone, or cannot act on, is said once in its
//     own line.
//
// The value is read as the game reads it: split at commas, each name trimmed of spaces and tabs and
// matched exactly (case counts), a name given twice is one, an empty one is skipped.
#pragma once

#include <algorithm>
#include <cstdint>
#include <cstdlib>
#include <functional>
#include <string>
#include <vector>

#include "../../runtime/sa2_tweaks.h"

namespace sa2tw {

// A host tweak's bit. A type of its own, so that a game tweak's bit (an int of runtime/sa2_tweaks.h's
// enum) cannot be asked of the host's mask by mistake.
struct Bit {
    uint32_t mask;
};

enum : unsigned {
#define SA2TW_INDEX(bit, name, report, summary, home) bit##_INDEX,
    SA2_TWEAK_HOST_LIST(SA2TW_INDEX)
#undef SA2TW_INDEX
    kHostCount
};
static_assert(kHostCount <= 32, "SA2_TWEAK_HOST_LIST holds more tweaks than a 32-bit mask has bits");

#define SA2TW_BIT(bit, name, report, summary, home) inline constexpr Bit bit{1u << bit##_INDEX};
SA2_TWEAK_HOST_LIST(SA2TW_BIT)
#undef SA2TW_BIT

// The host tweaks on in this process: set once, at its start, from read() (the player's main, the
// launcher's play). 0, every tweak off, in a process that never reads, as host/d3d9gpu's own tests.
inline uint32_t g_on = 0;

inline bool on(Bit b) { return (g_on & b.mask) != 0; }

// Every name the value gives, the game's tweaks' too, as read() splits it: set with g_on (the
// player's main), empty when the tweaks are off. A host change that follows a game tweak asks
// named() (the touch overlay: action-buttons puts Y in a stage).
inline std::vector<std::string> g_names;

inline bool named(const char* name) { return std::find(g_names.begin(), g_names.end(), name) != g_names.end(); }

// The player's flags of synthetic input, the ones its main parses: a session with either is a test
// run. The launcher looks for them among its --player-arg values (host/launch/windows_play.py).
inline constexpr const char* kSyntheticFlags[] = {"--synthetic-keys", "--synthetic-input"};

// Why a player session is no play session, from its arguments (--interactive, --input-path, and
// either of kSyntheticFlags): "outside play (...)" or "in a synthetic-input run (...)"; nullptr in
// play.
const char* not_play(bool interactive, bool has_input_path, bool synthetic);

// A lookup of the game's environment: a setting's value, or nullptr when it is unset.
using GameEnv = std::function<const char*(const char*)>;

// Why this process's tweaks are off: a replay (`game_env` has SA2_REPLAY_TICKS or
// SA2_REPLAY_WORDS, set to anything, as runtime/sa2_tweaks.c asks), else `not_play`, the caller's
// own reason when it is no play session (nullptr in play); nullptr when they may be on. `game_env`
// is this process's environment by default: the player's, which its host gives the game's two
// replay settings (the header, "Play only"); the launcher passes the one it builds for the game.
const char* off(const char* not_play, const GameEnv& game_env = std::getenv);

// A reading of SA2_TWEAKS for one home ("player" or "launcher").
struct Reading {
    uint32_t on = 0;     // the mask of that home's tweaks the value turns on
    std::string line;    // the report line; "" when the value is unset or empty
    std::vector<std::string> names;   // every name the value gives, in its order, once each; none when off
};

// The reading of `value` for `home`; `why_off` (off()'s answer) non-null turns every tweak off and
// says the value is ignored, and why.
Reading read(const char* value, const char* why_off, const char* home);

}  // namespace sa2tw
