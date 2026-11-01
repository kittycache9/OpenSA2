"""The music-volume tweak (runtime/tweaks/music-volume.c) on the music player's volume setter
(0x00439490): the level argument lowered for the lifted body, the game's argument and the level it
stored put back after, and nothing touched when the tweak is off.

The tweak's file is built beside runtime/sa2_tweaks.c with the pinned zig, as test_tweaks.py builds
no-event-bars, and linked with a driver that stands in for the guest's stack and audio manager and
for the lifted body. The stand-in `__real_sub_439490` does what the image's body does to the
manager: it holds the argument to -960 .. 0, and, when the manager has a player at +4, stores it at
+0x1B8; then it pops the return address (the RET). argv is a list of steps: "set <level>" calls the
setter with that argument, "noplayer" and "player" take the stream player away and give it back,
"nomanager" clears the manager pointer, "badstack" puts ESP where nothing reads, "badstore" makes
the stored level stop writing, and "table" prints the level for every percent. Each call prints the
level the lifted body saw, the argument slot afterwards, and the level stored.
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

VARS = ("SA2_TWEAKS", "SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS", "SA2_MUSIC_VOLUME")
SOURCE = RUNTIME / "tweaks" / "music-volume.c"

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
static uint8_t stack[0x100];
static uint32_t gpr[8], manager_ptr = MANAGER, player = 0x1234, stored = 0xFFFFFFFFu, seen;
static int store_writes = 1;

int sa2_peek(uint32_t a, uint32_t n, void *out) {
    if (a == MANAGER_PTR && n == 4) { memcpy(out, &manager_ptr, 4); return SA2_OK; }
    if (manager_ptr && a == manager_ptr + 4 && n == 4) { memcpy(out, &player, 4); return SA2_OK; }
    if (a < STACK || a + n > STACK + sizeof stack) return 1;
    memcpy(out, stack + (a - STACK), n);
    return SA2_OK;
}
int sa2_poke(uint32_t a, uint32_t n, const void *in) {
    if (manager_ptr && a == manager_ptr + 0x1B8 && n == 4) {
        if (!store_writes) return 1;
        memcpy(&stored, in, 4); return SA2_OK;
    }
    if (a < STACK || a + n > STACK + sizeof stack) return 1;
    memcpy(stack + (a - STACK), in, n);
    return SA2_OK;
}
uint32_t sa2_rt_get_gpr(const void *state, int idx) { (void)state; return gpr[idx]; }
void sa2_rt_set_gpr(void *state, int idx, uint32_t v) { (void)state; gpr[idx] = v; }
int32_t sa2_music_volume_level(unsigned percent);

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
void *__wrap_sub_439490(void *state, uint32_t pc, void *memory);

int main(int argc, char **argv) {
    int token;
    uint32_t esp = STACK + 0x10u;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "set") && i + 1 < argc) {
            const int32_t level = (int32_t)strtol(argv[++i], NULL, 0);
            memset(stack, 0, sizeof stack);
            memcpy(stack + 0x10 + 4, &level, 4);
            gpr[SA2_ESP] = esp;
            stored = 0xFFFFFFFFu; seen = 0xDEADBEEFu;
            if (__wrap_sub_439490(&token, 0x00439490u, &token) != &token) return 3;
            int32_t after; memcpy(&after, stack + 0x10 + 4, 4);
            printf("saw %d arg %d stored %d esp %u\n", (int)seen, (int)after,
                   stored == 0xFFFFFFFFu ? 999999 : (int)stored, gpr[SA2_ESP] - esp);
        }
        else if (!strcmp(argv[i], "noplayer")) player = 0;
        else if (!strcmp(argv[i], "player")) player = 0x1234;
        else if (!strcmp(argv[i], "nomanager")) manager_ptr = 0;
        else if (!strcmp(argv[i], "badstore")) store_writes = 0;
        else if (!strcmp(argv[i], "badstack")) {
            gpr[SA2_ESP] = 0x90000u;
            if (__wrap_sub_439490(&token, 0x00439490u, &token) != &token) return 3;
            puts("badstack called");
        }
        else if (!strcmp(argv[i], "table"))
            for (unsigned p = 0; p <= 100; p++) printf("%u %d\n", p, (int)sa2_music_volume_level(p));
    }
    return 0;
}
"""


@pytest.fixture(scope="module")
def exe(tmp_path_factory) -> Path:
    if not ZIG.is_file():
        pytest.fail(f"the pinned zig is not present: {ZIG}")
    d = tmp_path_factory.mktemp("music_volume")
    (d / "driver.c").write_text(DRIVER)
    out = d / "music_volume"
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


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "no-event-bars"},
                                 {"SA2_MUSIC_VOLUME": "0"}])
def test_off_is_the_lifted_body_and_reads_nothing(exe, env):
    out, err = run(exe, "set", "-55", "set", "0", **env)
    assert out == ["saw -55 arg -55 stored -55 esp 4", "saw 0 arg 0 stored 0 esp 4"]
    assert [line for line in err if "music volume" in line] == []


def test_a_replay_runs_the_games_level(exe):
    out, _ = run(exe, "set", "-55", SA2_TWEAKS="music-volume", SA2_REPLAY_TICKS="1")
    assert out == ["saw -55 arg -55 stored -55 esp 4"]


def test_on_lowers_what_the_body_sees_and_puts_the_games_level_back(exe):
    out, err = run(exe, "set", "0", "set", "-55", "set", "-500", SA2_TWEAKS="music-volume")
    # 60 percent is -44; the body sees the sum, the slot and the stored level are the game's.
    assert out == ["saw -44 arg 0 stored 0 esp 4",
                   "saw -99 arg -55 stored -55 esp 4",
                   "saw -544 arg -500 stored -500 esp 4"]
    assert err == ["sa2 tweaks: music volume (SA2_TWEAKS=music-volume)",
                   "sa2 tweaks: music volume 60%: music levels are lowered by 44 tenths of a "
                   "decibel, held at -960"]


def test_the_games_level_is_held_to_its_range_before_the_tweaks_is_added(exe):
    out, _ = run(exe, "set", "5", "set", "-999", "set", "-960", SA2_TWEAKS="music-volume")
    # +5 is held to 0; -999 to -960, where the body holds the sum to -960 again; the stored level is
    # the held game level in both.
    assert out == ["saw -44 arg 5 stored 0 esp 4",
                   "saw -1004 arg -999 stored -960 esp 4",
                   "saw -1004 arg -960 stored -960 esp 4"]


def test_a_fade_reads_the_games_level_so_the_tweak_does_not_compound(exe):
    # The music task calls the setter with the stored level minus 8 a frame; the stored level must
    # be the game's, so five frames of fade are -8, -16, ... -40 and never the sum.
    steps = []
    for k in range(1, 6):
        steps += ["set", str(-8 * k)]
    out, _ = run(exe, *steps, SA2_TWEAKS="music-volume")
    assert [line.split("stored ")[1].split(" ")[0] for line in out] == ["-8", "-16", "-24", "-32", "-40"]


@pytest.mark.parametrize("value, level", [("0", -960), ("1", -400), ("50", -60), ("100", 0),
                                          ("10", -200), ("75", -25)])
def test_the_level_comes_from_sa2_music_volume(exe, value, level):
    out, err = run(exe, "set", "0", SA2_TWEAKS="music-volume", SA2_MUSIC_VOLUME=value)
    assert out == [f"saw {level} arg 0 stored 0 esp 4"]
    assert err[-1] == (f"sa2 tweaks: music volume {value}%: music levels are lowered by {-level} "
                       "tenths of a decibel, held at -960")


@pytest.mark.parametrize("value", ["loud", "101", "-5", "5.5", "50%", " 50", "12345"])
def test_a_value_that_does_not_read_is_named_and_the_default_used(exe, value):
    out, err = run(exe, "set", "0", SA2_TWEAKS="music-volume", SA2_MUSIC_VOLUME=value)
    assert out == ["saw -44 arg 0 stored 0 esp 4"]
    assert (f"sa2 tweaks: music volume: SA2_MUSIC_VOLUME={value} is not a whole number from 0 to 100, "
            "so the volume is 60%") in err


def test_an_empty_value_is_the_default(exe):
    out, _ = run(exe, "set", "0", SA2_TWEAKS="music-volume", SA2_MUSIC_VOLUME="")
    assert out == ["saw -44 arg 0 stored 0 esp 4"]


def test_no_player_means_the_body_stores_nothing_and_neither_does_the_tweak(exe):
    out, _ = run(exe, "noplayer", "set", "-55", "nomanager", "set", "-55", SA2_TWEAKS="music-volume")
    assert out == ["saw -99 arg -55 stored 999999 esp 4", "saw -99 arg -55 stored 999999 esp 4"]


def test_a_store_that_does_not_write_is_said_once(exe):
    out, err = run(exe, "badstore", "set", "-55", "set", "-55", SA2_TWEAKS="music-volume")
    # The body stored the lowered level and the tweak could not put the game's back.
    assert out == ["saw -99 arg -55 stored -99 esp 4"] * 2
    assert [line for line in err if "does not write" in line] == [
        "sa2 tweaks: music volume: the music level at 0x0003" "01B8 does not write, so a fade reads "
        "the lowered level"]


def test_a_stack_that_does_not_read_leaves_the_game_alone_and_says_so_once(exe):
    out, err = run(exe, "badstack", "badstack", SA2_TWEAKS="music-volume")
    assert out == ["badstack called"] * 2
    assert [line for line in err if "stack" in line] == [
        "sa2 tweaks: music volume: the guest stack at 0x00090004 does not read, so the music keeps "
        "the game's level"]


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
