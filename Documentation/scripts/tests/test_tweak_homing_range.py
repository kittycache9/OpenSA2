"""The homing-range tweak (runtime/tweaks/homing-range.c): the squared distance 0x0047E960 takes at
guest [ESP+4] is scaled by (100 / R) squared for the lifted body and put back after it, so the
body's compare against 10000 (100 units) lets targets up to R units into the homing list, each
stored scaled. Pairs that are no player's, targets of kind 0x11 and a player with a rival within R
are left to the game. With the tweak off (or in a replay) the lifted body runs alone.

The tweak's file is built with the pinned zig beside runtime/sa2_tweaks.c, as
test_tweak_endless_chao_items.py builds its tweak, and linked with a driver that stands in for the
lifted body and the runtime's peek, poke and register accessors. The guest's memory is two flat
buffers: one at 0x01DE4000 for the lists, their counts and MainCharObj1, one at 0x30000 for the
stack and the objects.

The stand-in `__real_sub_47e960` does what the body does: it compares the float at [ESP+4] with
10000, and below it appends {EDX, d2} to player 1's or player 2's list (or their 0x11 lists, by
EDX's kind byte), chosen by ECX against MainCharObj1[0] and [1], then pops the return address.
Each driver step prints "<list> <stored d2> <[ESP+4] after> <esp moved>", the list "p1", "p2",
"p1s" or "p2s" that grew, or "-" (and the stored d2 "-") when none did.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
ZIG = ROOT / "private" / "predecessor-tools" / "zig" / "zig-x86_64-linux-0.15.2" / "zig"
RUNTIME = ROOT / "runtime"
CC = ["-target", "x86_64-linux-musl", "-O2", "-Wall", "-Wextra", "-Werror", "-I", str(RUNTIME)]
VARS = ("SA2_TWEAKS", "SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS", "SA2_HOMING_RANGE")
ON = "sa2 tweaks: longer homing range (SA2_TWEAKS=homing-range)"
LISTED = "sa2 tweaks: homing range {}: player {} listed a target {} units away (the game's limit is 100)"

# argv: switches, then steps. A step is `call` (the wrap) or `lifted` (the stand-in alone).
#   d2=F         the squared distance at [ESP+4]
#   who=p1|p2|other   ECX: player 1's EntityData1, player 2's, or an object that is neither's
#   kind=N       the target's kind byte [EDX+2]
#   rival=K:X    MainCharObj1[1] (for player 1; [0] for player 2) has kind K at X units on the x axis
#   nostack      ESP where nothing reads
DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cpu_state.h"
#include "guest_memory.h"

#define LBASE 0x01DE4000u
#define LSIZE 0x7000u
#define OBASE 0x30000u
#define OSIZE 0x9000u
#define STACK 0x38000u
#define P1    (OBASE + 0x0000u)
#define P2    (OBASE + 0x1000u)
#define OTHER (OBASE + 0x2000u)
#define TARGET (OBASE + 0x3000u)
#define MAIN  0x01DEA6C0u
static uint8_t lmem[LSIZE], omem[OSIZE];
static uint32_t gpr[SA2_GPR_COUNT];

static void *where(uint32_t a, uint32_t n) {
    if (a >= LBASE && a + n <= LBASE + LSIZE) return lmem + (a - LBASE);
    if (a >= OBASE && a + n <= OBASE + OSIZE) return omem + (a - OBASE);
    return NULL;
}
int sa2_peek(uint32_t a, uint32_t n, void *out) {
    void *p = where(a, n);
    if (!p) return 1;
    memcpy(out, p, n);
    return SA2_OK;
}
int sa2_poke(uint32_t a, uint32_t n, const void *in) {
    void *p = where(a, n);
    if (!p) return 1;
    memcpy(p, in, n);
    return SA2_OK;
}
static uint32_t rd32(uint32_t a) { uint32_t v = 0; sa2_peek(a, 4, &v); return v; }
static uint16_t rd16(uint32_t a) { uint16_t v = 0; sa2_peek(a, 2, &v); return v; }
static float rdf(uint32_t a) { float v = 0; sa2_peek(a, 4, &v); return v; }
static void wr32(uint32_t a, uint32_t v) { sa2_poke(a, 4, &v); }
static void wr16(uint32_t a, uint16_t v) { sa2_poke(a, 2, &v); }
static void wrf(uint32_t a, float v) { sa2_poke(a, 4, &v); }
uint32_t sa2_rt_get_gpr(const void *state, int idx) { (void)state; return gpr[idx]; }
void sa2_rt_set_gpr(void *state, int idx, uint32_t v) { (void)state; gpr[idx] = v; }

/* The lists: {count, base} for player 1's, player 2's, player 1's 0x11 and player 2's 0x11. */
static const uint32_t lists[4][2] = {{0x01DE46B8u, 0x01DE6FA0u}, {0x01DE50E0u, 0x01DE5100u},
                                     {0x01DE8C28u, 0x01DE8C40u}, {0x01DE9448u, 0x01DE46C0u}};
static const char *names[4] = {"p1", "p2", "p1s", "p2s"};

/* 0x0047E960: d2 < 10000, then by ECX and the kind byte, one entry {EDX, d2} and a 0 after it. */
void *__real_sub_47e960(void *s, uint32_t pc, void *m) {
    (void)s; (void)pc;
    float d2 = 0;
    uint32_t ecx = gpr[SA2_ECX], edx = gpr[SA2_EDX];
    if (sa2_peek(gpr[SA2_ESP] + 4, 4, &d2) == SA2_OK && d2 < 10000.0f) {
        int which = ecx == rd32(MAIN) ? 0 : ecx == rd32(MAIN + 4) ? 1 : -1;
        if (which >= 0) {
            uint8_t kind = 0;
            sa2_peek(edx + 2, 1, &kind);
            if (kind == 0x11) which += 2;
            uint16_t n = rd16(lists[which][0]);
            uint32_t e = lists[which][1] + 8u * n;
            wr32(e, edx);
            wrf(e + 4, d2);
            wr32(e + 8, 0);
            wr16(lists[which][0], (uint16_t)(n + 1));
        }
    }
    gpr[SA2_EIP] = rd32(gpr[SA2_ESP]);
    gpr[SA2_ESP] += 4;
    return m;
}
void *__wrap_sub_47e960(void *state, uint32_t pc, void *memory);

int main(int argc, char **argv) {
    int token, direct = 0, rival_kind = 0;
    float d2 = 30000.0f, rival_x = 0;
    uint32_t esp = STACK, self = P1;
    uint8_t kind = 5;
    for (int i = 1; i < argc; i++) {
        const char *a = argv[i];
        if (!strncmp(a, "d2=", 3)) { d2 = strtof(a + 3, NULL); continue; }
        if (!strcmp(a, "who=p1")) { self = P1; continue; }
        if (!strcmp(a, "who=p2")) { self = P2; continue; }
        if (!strcmp(a, "who=other")) { self = OTHER; continue; }
        if (!strncmp(a, "kind=", 5)) { kind = (uint8_t)strtoul(a + 5, NULL, 0); continue; }
        if (!strncmp(a, "rival=", 6)) {
            char *end;
            rival_kind = (int)strtol(a + 6, &end, 0);
            rival_x = strtof(end + 1, NULL);
            continue;
        }
        if (!strcmp(a, "nostack")) { esp = 0x90000u; continue; }
        if (!strcmp(a, "lifted")) { direct = 1; continue; }
        memset(lmem, 0, sizeof lmem);
        memset(omem, 0, sizeof omem);
        wr32(MAIN, P1);
        omem[P1 - OBASE + 2] = 1;
        omem[TARGET - OBASE + 2] = kind;
        if (self == P2 || rival_kind) {
            /* the other player: player 2 for player 1's pair, or player 1 for player 2's */
            wr32(MAIN + 4, P2);
            omem[P2 - OBASE + 2] = (uint8_t)(rival_kind ? rival_kind : 1);
            if (self == P2) omem[P1 - OBASE + 2] = (uint8_t)(rival_kind ? rival_kind : 0);
            wrf((self == P2 ? P1 : P2) + 0x14, rival_x);
        }
        wrf(STACK + 4, d2);
        wr32(STACK, 0x0047EB1Fu);
        gpr[SA2_ESP] = esp;
        gpr[SA2_ECX] = self;
        gpr[SA2_EDX] = TARGET;
        gpr[SA2_EBX] = 0x22222222u;
        void *ret = (direct ? __real_sub_47e960 : __wrap_sub_47e960)(&token, 0x0047E960u, &token);
        if (ret != &token || gpr[SA2_EBX] != 0x22222222u || gpr[SA2_ECX] != self ||
            gpr[SA2_EDX] != TARGET)
            return 3;
        int grew = -1;
        for (int l = 0; l < 4; l++) if (rd16(lists[l][0])) grew = l;
        if (grew >= 0 && rd32(lists[grew][1]) != TARGET) return 4;
        if (grew >= 0) printf("%s %.2f", names[grew], rdf(lists[grew][1] + 4));
        else printf("- -");
        printf(" %.2f %d\n", rdf(STACK + 4), (int)(gpr[SA2_ESP] - esp));
    }
    return 0;
}
"""


def zig_env(d: Path) -> dict:
    return dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(d / "zc"),
                ZIG_GLOBAL_CACHE_DIR=os.environ.get(
                    "ZIG_GLOBAL_CACHE_DIR", str(ROOT / "private" / "generated" / "zig-cache-tests")))


@pytest.fixture(scope="module")
def exe(tmp_path_factory) -> Path:
    if not ZIG.is_file():
        pytest.fail(f"the pinned zig is not present: {ZIG}")
    d = tmp_path_factory.mktemp("homing_range")
    (d / "driver.c").write_text(DRIVER)
    out = d / "hr"
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(RUNTIME / "tweaks" / "homing-range.c"), "-o", str(out)],
                          capture_output=True, text=True, env=zig_env(d))
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, *steps: str, **env: str) -> tuple[list[str], str]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS}
    p = subprocess.run([str(exe), *steps], capture_output=True, text=True, timeout=30,
                       env=dict(clean, **env))
    assert p.returncode == 0, (p.returncode, p.stderr)
    return p.stdout.splitlines(), p.stderr


HOMING = {"SA2_TWEAKS": "homing-range"}
# d2 30000 (173 units) and 50000 (224 units) are beyond the game's 100; 2500 (50 units) is within.
GAME = ["- - 30000.00 4", "- - 50000.00 4", "p1 2500.00 2500.00 4"]


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "no-event-bars"}])
def test_off_is_the_lifted_body(exe, env):
    out, err = run(exe, "d2=30000", "call", "d2=50000", "call", "d2=2500", "call", **env)
    assert out == GAME
    assert run(exe, "lifted", "d2=30000", "call", "d2=50000", "call", "d2=2500", "call")[0] == GAME
    assert "homing" not in err


def test_on_a_target_within_200_units_is_listed_scaled_and_the_argument_is_put_back(exe):
    out, err = run(exe, "d2=30000", "call", "d2=50000", "call", "d2=2500", "call", **HOMING)
    assert out == ["p1 7500.00 30000.00 4", "- - 50000.00 4", "p1 625.00 2500.00 4"]
    assert err.splitlines() == [ON, LISTED.format(200, 1, 173)]


def test_the_edge_of_the_range(exe):
    out, _ = run(exe, "d2=39999", "call", "d2=40000", "call", **HOMING)
    assert out == ["p1 9999.75 39999.00 4", "- - 40000.00 4"]


def test_player_2_gets_the_same_range(exe):
    out, err = run(exe, "who=p2", "d2=30000", "call", **HOMING)
    assert out == ["p2 7500.00 30000.00 4"]
    assert err.splitlines() == [ON, LISTED.format(200, 2, 173)]


def test_kind_0x11_keeps_the_games_range(exe):
    out, err = run(exe, "kind=0x11", "d2=30000", "call", "d2=2500", "call", **HOMING)
    assert out == ["- - 30000.00 4", "p1s 2500.00 2500.00 4"]
    assert err.splitlines() == [ON, "sa2 tweaks: homing range: targets of kind 0x11 keep the game's "
                                    "range (their reader keeps them within 64 units)"]


def test_a_pair_that_is_no_players_is_left_to_the_game(exe):
    out, err = run(exe, "who=other", "d2=2500", "call", **HOMING)
    assert out == ["- - 2500.00 4"]
    assert "a pair whose first object (0x00032000) is no player's is left to the game" in err


@pytest.mark.parametrize("kind", [1, 3])
def test_a_rival_within_the_range_keeps_the_games_range(exe, kind):
    """0x00720F00 and 0x00721840 start their best at the rival's true d2, so a scaled entry would
    beat a rival nearer than it."""
    out, err = run(exe, f"rival={kind}:150", "d2=30000", "call", "d2=2500", "call", **HOMING)
    assert out == ["- - 30000.00 4", "p1 2500.00 2500.00 4"]
    assert ("sa2 tweaks: homing range: player 1's rival is within 200 units, so the game's range "
            "is kept while it is") in err.splitlines()


def test_a_rival_beyond_the_range_or_of_another_kind_changes_nothing(exe):
    out, _ = run(exe, "rival=1:250", "d2=30000", "call", "rival=2:50", "d2=30000", "call", **HOMING)
    assert out == ["p1 7500.00 30000.00 4", "p1 7500.00 30000.00 4"]


def test_player_2s_rival_is_player_1(exe):
    out, _ = run(exe, "who=p2", "rival=1:150", "d2=30000", "call", "rival=1:250", "d2=30000", "call",
                 **HOMING)
    assert out == ["- - 30000.00 4", "p2 7500.00 30000.00 4"]


def test_the_first_listing_beyond_100_is_said_once(exe):
    _, err = run(exe, "d2=2500", "call", "d2=30000", "call", "d2=20000", "call", **HOMING)
    assert err.splitlines() == [ON, LISTED.format(200, 1, 173)]


@pytest.mark.parametrize("value, out, said", [
    ("300", ["p1 5555.56 50000.00 4", "- - 100000.00 4"], LISTED.format(300, 1, 223)),
    ("100", ["- - 50000.00 4", "- - 100000.00 4"], None),
])
def test_sa2_homing_range_sets_the_range(exe, value, out, said):
    got, err = run(exe, "d2=50000", "call", "d2=100000", "call", **HOMING, SA2_HOMING_RANGE=value)
    assert got == out
    assert err.splitlines() == [ON] + ([said] if said else [])


@pytest.mark.parametrize("value", ["abc", "50", "401", "2e2", "200.5", " 200", "-200"])
def test_a_bad_sa2_homing_range_is_named_once_and_200_used(exe, value):
    out, err = run(exe, "d2=30000", "call", "d2=30000", "call", **HOMING, SA2_HOMING_RANGE=value)
    assert out == ["p1 7500.00 30000.00 4"] * 2
    assert err.splitlines() == [
        ON, f"sa2 tweaks: homing range: SA2_HOMING_RANGE={value} is not a whole number of units from "
            "100 to 400, so the range is 200", LISTED.format(200, 1, 173)]


def test_an_empty_sa2_homing_range_is_200(exe):
    out, err = run(exe, "d2=30000", "call", **HOMING, SA2_HOMING_RANGE="")
    assert out == ["p1 7500.00 30000.00 4"]
    assert err.splitlines() == [ON, LISTED.format(200, 1, 173)]


def test_an_unreadable_stack_runs_the_body_alone(exe):
    out, err = run(exe, "nostack", "call", **HOMING)
    assert out == ["- - 30000.00 4"]   # the body's own read fails too, so nothing is listed
    assert ("sa2 tweaks: homing range: the guest stack at 0x00090004 does not read, so the pair is "
            "left to the game") in err.splitlines()


@pytest.mark.parametrize("replay", ["SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"])
def test_ignored_in_a_replay(exe, replay):
    out, err = run(exe, "d2=30000", "call", "d2=2500", "call", **HOMING, **{replay: "/dev/null"})
    assert out == ["- - 30000.00 4", "p1 2500.00 2500.00 4"]
    assert err == ("sa2 tweaks: SA2_TWEAKS=homing-range ignored in a replay (SA2_REPLAY_TICKS "
                   "or SA2_REPLAY_WORDS is set)\n")
