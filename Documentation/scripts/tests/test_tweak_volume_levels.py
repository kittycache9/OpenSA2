"""The volume-levels tweak (runtime/tweaks/volume-levels.c): the music player's volume setter
(0x00439490) sees the music level added and leaves the game's level stored, the voice streams'
dword (0x01A55990) holds the voice level before the music task (0x00442AD0) and the music start
(0x004425D0) run, and the sound driver's channel consumer (0x0077EE90) sees each pending channel
level scaled for the sound-effects gain, with the game's level put back after; and nothing is
touched when the tweak is off.

The tweak's file is built beside runtime/sa2_tweaks.c with the pinned zig, as test_tweaks.py builds
no-event-bars, and linked with a driver that stands in for the guest memory and the four lifted
bodies. The stand-in `__real_sub_439490` does what the image's body does to the manager: it holds
the argument to -960 .. 0 and, when the manager has a player at +4, stores it at +0x1B8; then it
pops the return address (the RET). The music task's and the start's stand-ins print the voice
dword. The consumer's stand-in prints, for each channel whose level bit (4) is set, the gain the
image's body gives it ((v + 0.5) x 1.3, or v x 0.7 in the other mode, held at 0 or above), and in
the other mode the byte level of each channel with bit 0x20, then clears the bits, as the body does.

argv is a list of steps: "set <level>" calls the music setter, "tick" and "init" the voice wraps,
"chan <i> <level> [byte]" makes channel i pending with that float level (and bit 0x20 with that
byte), "mode <0|1>" sets the driver's mode byte, "frame" calls the consumer and then prints every
channel level left in memory, "nodriver", "noplayer", "nomanager", "badstack" and "badstore" take
pieces away, and "table" prints the music level for every percent.
"""
from __future__ import annotations

import math
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_tweaks import CC, RUNTIME, ZIG, zig_env  # noqa: E402

VARS = ("SA2_TWEAKS", "SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS", "SA2_MUSIC_VOLUME", "SA2_VOICE_VOLUME",
        "SA2_SFX_VOLUME")
SOURCE = RUNTIME / "tweaks" / "volume-levels.c"
ON = "sa2 tweaks: volume levels (SA2_TWEAKS=volume-levels)"

DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cpu_state.h"
#include "guest_memory.h"

#define STACK 0x20000u
#define MANAGER_PTR 0x01AF19E0u
#define MANAGER 0x30000u
#define VOICE 0x01A55990u
#define DRIVER_PTR 0x01AF0ECCu
#define DRIVER 0x40000000u
#define NCH 59
static uint8_t stack[0x100];
static uint32_t gpr[8], manager_ptr = MANAGER, player = 0x1234, stored = 0xFFFFFFFFu, seen, driver_ptr = DRIVER;
static int32_t voice;
static int store_writes = 1;
static float level[NCH];
static uint8_t flags[NCH], byte_level[NCH], mode;

static int channel_field(uint32_t a, uint32_t n, void **p) {
    if (!driver_ptr || a < DRIVER) return 0;
    const uint32_t off = a - DRIVER;
    if (off == 0xA00650u && n == 1) { *p = &mode; return 1; }
    if (off < 0xA001ACu) return 0;
    const uint32_t i = (off - 0xA001ACu) / 0x14u, f = (off - 0xA001ACu) % 0x14u;
    if (i >= NCH) return 0;
    if (f == 0xC && n == 4) { *p = &level[i]; return 1; }
    if (f == 0x10 && n == 1) { *p = &byte_level[i]; return 1; }
    if (f == 0x12 && n == 1) { *p = &flags[i]; return 1; }
    return 0;
}
int sa2_peek(uint32_t a, uint32_t n, void *out) {
    void *p;
    if (a == MANAGER_PTR && n == 4) { memcpy(out, &manager_ptr, 4); return SA2_OK; }
    if (manager_ptr && a == manager_ptr + 4 && n == 4) { memcpy(out, &player, 4); return SA2_OK; }
    if (a == DRIVER_PTR && n == 4) { memcpy(out, &driver_ptr, 4); return SA2_OK; }
    if (channel_field(a, n, &p)) { memcpy(out, p, n); return SA2_OK; }
    if (a < STACK || a + n > STACK + sizeof stack) return 1;
    memcpy(out, stack + (a - STACK), n);
    return SA2_OK;
}
int sa2_poke(uint32_t a, uint32_t n, const void *in) {
    void *p;
    if (manager_ptr && a == manager_ptr + 0x1B8 && n == 4) {
        if (!store_writes) return 1;
        memcpy(&stored, in, 4); return SA2_OK;
    }
    if (a == VOICE && n == 4) { memcpy(&voice, in, 4); return SA2_OK; }
    if (channel_field(a, n, &p)) { memcpy(p, in, n); return SA2_OK; }
    if (a < STACK || a + n > STACK + sizeof stack) return 1;
    memcpy(stack + (a - STACK), in, n);
    return SA2_OK;
}
uint32_t sa2_rt_get_gpr(const void *state, int idx) { (void)state; return gpr[idx]; }
void sa2_rt_set_gpr(void *state, int idx, uint32_t v) { (void)state; gpr[idx] = v; }
int32_t sa2_volume_level(unsigned percent);

void *__real_sub_439490(void *state, uint32_t pc, void *memory) {
    (void)state; (void)pc;
    int32_t arg = 0;
    if (gpr[SA2_ESP] + 4 >= STACK && gpr[SA2_ESP] + 8 <= STACK + sizeof stack)
        memcpy(&arg, stack + (gpr[SA2_ESP] + 4 - STACK), 4);
    seen = (uint32_t)arg;
    if (arg < -960) arg = -960;
    if (arg > 0) arg = 0;
    if (manager_ptr && player) stored = (uint32_t)arg;
    gpr[SA2_ESP] += 4;                         /* the RET */
    return memory;
}
void *__real_sub_442ad0(void *state, uint32_t pc, void *memory) { (void)state; (void)pc; printf("tick %d\n", (int)voice); return memory; }
void *__real_sub_4425d0(void *state, uint32_t pc, void *memory) { (void)state; (void)pc; printf("init %d\n", (int)voice); return memory; }
void *__real_sub_77ee90(void *state, uint32_t pc, void *memory) {
    (void)state; (void)pc;
    for (int i = 0; i < NCH; i++) {
        if (flags[i] & 4) {
            float g = mode ? (float)(level[i] * 0.699999988079071) : (float)((level[i] + 0.5) * 1.2999999523162842);
            if (g < 0.0f) g = 0.0f;
            printf("ch %d gain %.4f\n", i, g);
        }
        if (mode && (flags[i] & 0x20)) printf("ch %d byte %u\n", i, byte_level[i]);
        flags[i] = 0;
    }
    return memory;
}
void *__wrap_sub_439490(void *state, uint32_t pc, void *memory);
void *__wrap_sub_442ad0(void *state, uint32_t pc, void *memory);
void *__wrap_sub_4425d0(void *state, uint32_t pc, void *memory);
void *__wrap_sub_77ee90(void *state, uint32_t pc, void *memory);

int main(int argc, char **argv) {
    int token;
    const uint32_t esp = STACK + 0x10u;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "set") && i + 1 < argc) {
            const int32_t l = (int32_t)strtol(argv[++i], NULL, 0);
            memset(stack, 0, sizeof stack);
            memcpy(stack + 0x10 + 4, &l, 4);
            gpr[SA2_ESP] = esp;
            stored = 0xFFFFFFFFu; seen = 0xDEADBEEFu;
            if (__wrap_sub_439490(&token, 0x00439490u, &token) != &token) return 3;
            int32_t after; memcpy(&after, stack + 0x10 + 4, 4);
            printf("saw %d arg %d stored %d esp %u\n", (int)seen, (int)after,
                   stored == 0xFFFFFFFFu ? 999999 : (int)stored, gpr[SA2_ESP] - esp);
        }
        else if (!strcmp(argv[i], "tick")) __wrap_sub_442ad0(&token, 0x00442AD0u, &token);
        else if (!strcmp(argv[i], "init")) __wrap_sub_4425d0(&token, 0x004425D0u, &token);
        else if (!strcmp(argv[i], "chan") && i + 2 < argc) {
            const int c = atoi(argv[++i]);
            level[c] = strtof(argv[++i], NULL);
            flags[c] |= 4;
            if (i + 1 < argc && argv[i + 1][0] == 'b') { byte_level[c] = (uint8_t)atoi(argv[++i] + 1); flags[c] |= 0x20; }
        }
        else if (!strcmp(argv[i], "mode") && i + 1 < argc) mode = (uint8_t)atoi(argv[++i]);
        else if (!strcmp(argv[i], "frame")) {
            if (__wrap_sub_77ee90(&token, 0x0077EE90u, &token) != &token) return 3;
            for (int c = 0; c < NCH; c++) if (level[c] != 0.0f || byte_level[c]) printf("left %d %.4f %u\n", c, level[c], byte_level[c]);
        }
        else if (!strcmp(argv[i], "nodriver")) driver_ptr = 0;
        else if (!strcmp(argv[i], "noplayer")) player = 0;
        else if (!strcmp(argv[i], "nomanager")) manager_ptr = 0;
        else if (!strcmp(argv[i], "badstore")) store_writes = 0;
        else if (!strcmp(argv[i], "badstack")) {
            gpr[SA2_ESP] = 0x90000u;
            if (__wrap_sub_439490(&token, 0x00439490u, &token) != &token) return 3;
            puts("badstack called");
        }
        else if (!strcmp(argv[i], "table"))
            for (unsigned p = 0; p <= 100; p++) printf("%u %d\n", p, (int)sa2_volume_level(p));
    }
    return 0;
}
"""


@pytest.fixture(scope="module")
def exe(tmp_path_factory) -> Path:
    if not ZIG.is_file():
        pytest.fail(f"the pinned zig is not present: {ZIG}")
    d = tmp_path_factory.mktemp("volume_levels")
    (d / "driver.c").write_text(DRIVER)
    out = d / "volume_levels"
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(SOURCE), "-o", str(out)],
                          capture_output=True, text=True, env=zig_env(d))
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, *steps: str, **env: str) -> tuple[list[str], list[str]]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS}
    p = subprocess.run([str(exe), *steps], capture_output=True, text=True, timeout=30,
                       env=dict(clean, **env))
    assert p.returncode == 0, p.stderr
    return p.stdout.splitlines(), p.stderr.splitlines()


def said(**levels: str) -> str:
    m, v, s = levels.get("m", "60"), levels.get("v", "100"), levels.get("s", "100")
    return f"sa2 tweaks: volume levels: music {m}%, voices {v}%, sound effects {s}%"


# ------------------------------------------------------------------------------------------ off

@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "no-event-bars"},
                                 {"SA2_MUSIC_VOLUME": "0", "SA2_VOICE_VOLUME": "0", "SA2_SFX_VOLUME": "0"}])
def test_off_is_the_lifted_bodies_and_reads_nothing(exe, env):
    out, err = run(exe, "set", "-55", "init", "tick", "chan", "3", "0.25", "frame", **env)
    assert out == ["saw -55 arg -55 stored -55 esp 4", "init 0", "tick 0", "ch 3 gain 0.9750",
                   "left 3 0.2500 0"]
    assert [line for line in err if "volume levels" in line] == []


def test_a_replay_runs_the_games_levels(exe):
    out, _ = run(exe, "set", "-55", "tick", "chan", "3", "0.25", "frame", SA2_TWEAKS="volume-levels",
                 SA2_MUSIC_VOLUME="20", SA2_VOICE_VOLUME="20", SA2_SFX_VOLUME="20", SA2_REPLAY_TICKS="1")
    assert out == ["saw -55 arg -55 stored -55 esp 4", "tick 0", "ch 3 gain 0.9750", "left 3 0.2500 0"]


# ---------------------------------------------------------------------------------------- music

def test_on_lowers_what_the_music_setter_sees_and_puts_the_games_level_back(exe):
    out, err = run(exe, "set", "0", "set", "-55", "set", "-500", SA2_TWEAKS="volume-levels")
    # 60 percent is -44; the body sees the sum, the slot and the stored level are the game's.
    assert out == ["saw -44 arg 0 stored 0 esp 4",
                   "saw -99 arg -55 stored -55 esp 4",
                   "saw -544 arg -500 stored -500 esp 4"]
    assert err == [ON, said()]


def test_the_games_music_level_is_held_to_its_range_before_the_tweaks_is_added(exe):
    out, _ = run(exe, "set", "5", "set", "-999", "set", "-960", SA2_TWEAKS="volume-levels")
    assert out == ["saw -44 arg 5 stored 0 esp 4",
                   "saw -1004 arg -999 stored -960 esp 4",
                   "saw -1004 arg -960 stored -960 esp 4"]


def test_a_music_fade_reads_the_games_level_so_the_tweak_does_not_compound(exe):
    steps = []
    for k in range(1, 6):
        steps += ["set", str(-8 * k)]
    out, _ = run(exe, *steps, SA2_TWEAKS="volume-levels")
    assert [line.split("stored ")[1].split(" ")[0] for line in out] == ["-8", "-16", "-24", "-32", "-40"]


@pytest.mark.parametrize("value, level", [("0", -960), ("1", -400), ("50", -60), ("100", 0),
                                          ("10", -200), ("75", -25)])
def test_the_music_level_comes_from_sa2_music_volume(exe, value, level):
    out, err = run(exe, "set", "0", SA2_TWEAKS="volume-levels", SA2_MUSIC_VOLUME=value)
    assert out == [f"saw {level} arg 0 stored 0 esp 4"]
    assert err[-1] == said(m=value)


def test_no_player_means_the_body_stores_nothing_and_neither_does_the_tweak(exe):
    out, _ = run(exe, "noplayer", "set", "-55", "nomanager", "set", "-55", SA2_TWEAKS="volume-levels")
    assert out == ["saw -99 arg -55 stored 999999 esp 4", "saw -99 arg -55 stored 999999 esp 4"]


def test_a_music_store_that_does_not_write_is_said_once(exe):
    out, err = run(exe, "badstore", "set", "-55", "set", "-55", SA2_TWEAKS="volume-levels")
    # The body stored the lowered level and the tweak could not put the game's back.
    assert out == ["saw -99 arg -55 stored -99 esp 4"] * 2
    assert [line for line in err if "does not write" in line] == [
        "sa2 tweaks: volume levels: the music level at 0x000301B8 does not write, so a fade reads "
        "the lowered level"]


def test_a_stack_that_does_not_read_leaves_the_music_alone_and_says_so_once(exe):
    out, err = run(exe, "badstack", "badstack", SA2_TWEAKS="volume-levels")
    assert out == ["badstack called"] * 2
    assert [line for line in err if "stack" in line] == [
        "sa2 tweaks: volume levels: the guest stack at 0x00090004 does not read, so the music keeps "
        "the game's level"]


# --------------------------------------------------------------------------------------- voices

@pytest.mark.parametrize("value, level", [("100", 0), ("50", -60), ("20", -140), ("0", -960)])
def test_the_voice_streams_dword_holds_the_voice_level_before_each_body(exe, value, level):
    out, err = run(exe, "init", "tick", "tick", SA2_TWEAKS="volume-levels", SA2_VOICE_VOLUME=value)
    assert out == [f"init {level}", f"tick {level}", f"tick {level}"]
    assert err == [ON, said(v=value)]


# -------------------------------------------------------------------------------- sound effects

@pytest.mark.parametrize("value", ["50", "20", "0"])
def test_each_pending_channel_plays_at_the_gain_times_the_effects_level_and_is_put_back(exe, value):
    g = int(value) / 100
    out, _ = run(exe, "chan", "0", "0.25", "chan", "42", "-0.25", "chan", "58", "0.0", "frame",
                 SA2_TWEAKS="volume-levels", SA2_SFX_VOLUME=value)
    want = [round(1.3 * (v + 0.5) * g, 4) for v in (0.25, -0.25, 0.0)]
    gains = [float(line.split()[3]) for line in out if line.startswith("ch ")]
    assert gains == pytest.approx(want, abs=2e-4)
    assert [line for line in out if line.startswith("left")] == ["left 0 0.2500 0", "left 42 -0.2500 0"]


def test_in_the_other_mode_the_gain_and_the_byte_level_are_scaled(exe):
    out, _ = run(exe, "mode", "1", "chan", "5", "0.5", "b200", "frame",
                 SA2_TWEAKS="volume-levels", SA2_SFX_VOLUME="50")
    assert out[0] == "ch 5 gain 0.1750"           # 0.5 x 0.5 x 0.7
    assert out[1] == "ch 5 byte 100"              # 200 x 0.5
    assert out[2] == "left 5 0.5000 200"


def test_a_channel_without_its_level_bit_is_left_alone(exe):
    out, _ = run(exe, "chan", "1", "0.25", "frame", "frame", SA2_TWEAKS="volume-levels", SA2_SFX_VOLUME="50")
    # The first frame takes the pending level; the second has nothing pending and changes nothing.
    assert out == ["ch 1 gain 0.4875", "left 1 0.2500 0", "left 1 0.2500 0"]


def test_full_effects_level_and_no_driver_leave_the_consumer_alone(exe):
    out, _ = run(exe, "chan", "2", "0.25", "frame", SA2_TWEAKS="volume-levels")
    assert out == ["ch 2 gain 0.9750", "left 2 0.2500 0"]
    out, err = run(exe, "nodriver", "frame", SA2_TWEAKS="volume-levels", SA2_SFX_VOLUME="50")
    assert out == [] and [line for line in err if "driver" in line] == []


# ---------------------------------------------------------------------------------------- input

@pytest.mark.parametrize("value", ["loud", "101", "-5", "5.5", "50%", " 50", "12345"])
def test_a_value_that_does_not_read_is_named_and_the_default_used(exe, value):
    out, err = run(exe, "set", "0", "tick", SA2_TWEAKS="volume-levels", SA2_MUSIC_VOLUME=value,
                   SA2_VOICE_VOLUME=value)
    assert out == ["saw -44 arg 0 stored 0 esp 4", "tick 0"]
    assert (f"sa2 tweaks: volume levels: SA2_MUSIC_VOLUME={value} is not a whole number from 0 to 100, "
            "so it is 60%") in err
    assert (f"sa2 tweaks: volume levels: SA2_VOICE_VOLUME={value} is not a whole number from 0 to 100, "
            "so it is 100%") in err


def test_an_empty_value_is_the_default(exe):
    out, _ = run(exe, "set", "0", SA2_TWEAKS="volume-levels", SA2_MUSIC_VOLUME="")
    assert out == ["saw -44 arg 0 stored 0 esp 4"]


def test_the_table_is_the_decibel_formula_and_never_rises(exe):
    text = SOURCE.read_text()
    body = re.search(r"k_level\[101\] = \{(.*?)\};", text, re.S).group(1)
    table = [int(x) for x in re.findall(r"-?\d+", body)]
    assert len(table) == 101 and table[0] == -960 and table[100] == 0
    for p in range(1, 101):
        assert table[p] == round(200 * math.log10(p / 100))
    assert table == sorted(table)
    out, _ = run(exe, "table")
    assert [int(line.split()[1]) for line in out] == table
