// tweaks.cpp: the host's reader of SA2_TWEAKS (tweaks.h has its rules; runtime/sa2_tweaks.h the
// list and the steps that add a host tweak). Nothing here names a tweak: a host tweak added to
// SA2_TWEAK_HOST_LIST is read, reported and left to its home here as every other is.
#include "tweaks.h"

#include <algorithm>
#include <cstdlib>
#include <cstring>

namespace sa2tw {

namespace {

struct Row {
    const char *name, *report, *home;
};

// SA2_TWEAK_HOST_LIST's lines in its order, the bits' order, ended by a row with no name (which
// also makes an empty list a valid array).
const Row kRows[] = {
#define SA2TW_ROW(bit, name, report, summary, home) {name, report, home},
    SA2_TWEAK_HOST_LIST(SA2TW_ROW)
#undef SA2TW_ROW
    {nullptr, nullptr, nullptr},
};

}  // namespace

const char* not_play(bool interactive, bool has_input_path, bool synthetic) {
    if (!interactive || !has_input_path) return "outside play (play is --interactive with --input-path)";
    if (synthetic) return "in a synthetic-input run (--synthetic-keys or --synthetic-input)";
    return nullptr;
}

const char* off(const char* not_play, const GameEnv& game_env) {
    if (game_env("SA2_REPLAY_TICKS") || game_env("SA2_REPLAY_WORDS"))
        return "in a replay (SA2_REPLAY_TICKS or SA2_REPLAY_WORDS is set)";
    return not_play;
}

Reading read(const char* value, const char* why_off, const char* home) {
    Reading r;
    if (!value || !*value) return r;
    const std::string head = std::string("sa2 host tweaks (") + home + "): ";
    if (why_off) {
        r.line = head + "SA2_TWEAKS=" + value + " ignored " + why_off;
        return r;
    }
    uint32_t named = 0;   // the host tweaks the value names, of every home
    for (const char* p = value; *p;) {
        const char* end = std::strchr(p, ',');
        if (!end) end = p + std::strlen(p);
        const char *a = p, *b = end;
        while (a < b && (*a == ' ' || *a == '\t')) a++;
        while (b > a && (b[-1] == ' ' || b[-1] == '\t')) b--;
        const std::string name(a, b);
        if (!name.empty() && std::find(r.names.begin(), r.names.end(), name) == r.names.end()) r.names.push_back(name);
        for (unsigned i = 0; kRows[i].name; i++)
            if (name == kRows[i].name) named |= 1u << i;
        p = *end ? end + 1 : end;
    }
    std::string active, others;
    for (unsigned i = 0; kRows[i].name; i++) {
        if (!(named & (1u << i))) continue;
        if (std::strcmp(kRows[i].home, home) == 0) {
            r.on |= 1u << i;
            active += (active.empty() ? "" : ", ") + std::string(kRows[i].report);
        } else {
            others += (others.empty() ? "\"" : ", \"") + std::string(kRows[i].name) + "\" (the " + kRows[i].home + "'s)";
        }
    }
    r.line = head + (active.empty() ? "none on" : active) + " (SA2_TWEAKS=" + value +
             (others.empty() ? "" : "; not the " + std::string(home) + "'s: " + others) + ")";
    return r;
}

}  // namespace sa2tw
