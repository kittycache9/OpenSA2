"""The start-in-chao-world tweak (runtime/tweaks/start-in-chao-world.c) on the main menu (0x00664EC0).

The tweak's file and runtime/sa2_tweaks.c are built with the pinned zig, as test_tweaks.py builds
no-event-bars, and linked with a driver that stands in for the lifted main menu, the runtime's peek
and poke, and its register accessors. The guest holds the bytes the wrap reads and writes:
CurrentMenu (0x01D7BB10), the save index (0x0173D06C), the Chao World bytes (0x01DEF829..30),
CurrentLevel (0x01934B70), the previous level (0x01934B84), CurrentCharacter (0x01934B80),
0x01748B94, 0x0174AFE0, TwoPlayerMode (0x0174AFDE), 0x0174AFC0, NextGameMode (0x0174B044) and the
story bytes (0x01DEB31E..21). At the start they hold what a boot leaves before the main menu, with
CurrentLevel 13, TwoPlayerMode 1, NextGameMode 0 and the story type 2, so a store shows.

The stand-in `__real_sub_664ec0` counts its calls and returns EAX = 0, the main menu staying.

Each call is one main-menu frame, "<CurrentMenu> <save index> <Chao World bytes, 16 hex digits>",
and prints "<body calls> <EAX> <CurrentLevel> <previous level> <CurrentCharacter> <NextGameMode>
<0x01748B94> <0x0174AFE0> <TwoPlayerMode> <0x0174AFC0> <story type> <index> <previous> <flag>".
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
VARS = ("SA2_TWEAKS", "SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS", "CW_FAIL")
TWEAK = "start-in-chao-world"

DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "guest_memory.h"

typedef struct { uint32_t addr, len; uint8_t b[8]; } cell;
static cell mem[] = {
    {0x01D7BB10u, 4, {0}}, {0x0173D06Cu, 4, {0}}, {0x01DEF829u, 8, {0}},
    {0x01934B70u, 2, {13}}, {0x01934B84u, 2, {0}}, {0x01934B80u, 4, {0}},
    {0x01748B94u, 4, {1}}, {0x0174AFE0u, 1, {7}}, {0x0174AFDEu, 1, {1}}, {0x0174AFC0u, 1, {7}},
    {0x0174B044u, 4, {0}}, {0x01DEB31Eu, 4, {5, 4, 2, 0}},
};
static const char *fail;   /* CW_FAIL: "peek" fails at the save index, "poke" at NextGameMode */

static uint8_t *at(uint32_t a, uint32_t n) {
    for (unsigned i = 0; i < sizeof mem / sizeof mem[0]; i++)
        if (a >= mem[i].addr && a + n <= mem[i].addr + mem[i].len) return mem[i].b + (a - mem[i].addr);
    return NULL;
}
int sa2_peek(uint32_t a, uint32_t n, void *out) {
    uint8_t *p = at(a, n);
    if (!p || (a == 0x0173D06Cu && fail && strcmp(fail, "peek") == 0)) return SA2_E_RANGE;
    memcpy(out, p, n);
    return SA2_OK;
}
int sa2_poke(uint32_t a, uint32_t n, const void *in) {
    uint8_t *p = at(a, n);
    if (!p || (a == 0x0174B044u && fail && strcmp(fail, "poke") == 0)) return SA2_E_RANGE;
    memcpy(p, in, n);
    return SA2_OK;
}
static uint32_t eax, calls;
uint32_t sa2_rt_get_gpr(const void *s, int idx) { (void)s; (void)idx; return eax; }
void sa2_rt_set_gpr(void *s, int idx, uint32_t v) { (void)s; if (idx == 0) eax = v; }

void *__real_sub_664ec0(void *s, uint32_t pc, void *memory) { (void)s; (void)pc; calls++; eax = 0; return memory; }
void *__wrap_sub_664ec0(void *s, uint32_t pc, void *memory);

static uint32_t rd(uint32_t a, uint32_t n) { uint32_t v = 0; memcpy(&v, at(a, n), n); return v; }

int main(int argc, char **argv) {
    fail = getenv("CW_FAIL");
    for (int i = 1; i + 2 < argc; i += 3) {
        uint32_t menu = (uint32_t)strtoul(argv[i], NULL, 0);
        int32_t index = (int32_t)strtol(argv[i + 1], NULL, 0);
        memcpy(at(0x01D7BB10u, 4), &menu, 4);
        memcpy(at(0x0173D06Cu, 4), &index, 4);
        for (int k = 0; k < 8; k++) {
            char hex[3] = {argv[i + 2][2 * k], argv[i + 2][2 * k + 1], 0};
            at(0x01DEF829u + k, 1)[0] = (uint8_t)strtoul(hex, NULL, 16);
        }
        int token;
        if (__wrap_sub_664ec0(&token, 0x00664EC0u, &token) != &token) return 3;
        printf("%u %u %u %u %u %u %u %u %u %u %u %u %u %u\n", calls, eax, rd(0x01934B70u, 2),
               rd(0x01934B84u, 2), rd(0x01934B80u, 4), rd(0x0174B044u, 4), rd(0x01748B94u, 4),
               rd(0x0174AFE0u, 1), rd(0x0174AFDEu, 1), rd(0x0174AFC0u, 1), rd(0x01DEB320u, 1),
               rd(0x01DEB31Eu, 1), rd(0x01DEB31Fu, 1), rd(0x01DEB321u, 1));
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
    d = tmp_path_factory.mktemp("start-in-chao-world")
    (d / "driver.c").write_text(DRIVER)
    out = d / "cw"
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(RUNTIME / "tweaks" / f"{TWEAK}.c"), "-o", str(out)],
                          capture_output=True, text=True, env=zig_env(d))
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, *calls: str, **env: str) -> tuple[list[str], list[str]]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS}
    p = subprocess.run([str(exe), *calls], capture_output=True, text=True, timeout=30,
                       env=dict(clean, **env))
    assert p.returncode == 0, p.stderr
    return p.stdout.splitlines(), p.stderr.splitlines()


REPORT = f"sa2 tweaks: start in Chao World (SA2_TWEAKS={TWEAK})"
SAY = "sa2 tweaks: start in Chao World: "
SONIC = "0100000000000000"
NONE = "0000000000000000"
LOCKED = SAY + ("the save has not opened Chao World (no byte of 0x01DEF829..0x01DEF830 set for a "
                "Chao World character), so the main menu is shown")
UNTOUCHED = "13 0 0 0 1 7 1 7 2 5 4 0"

# Before the save is loaded (index -1), on another menu (2, story select), then the first main-menu
# frame with the save loaded, then a second one.
CALLS = ("1", "-1", SONIC, "2", "0", SONIC, "1", "0", SONIC, "1", "0", SONIC)


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "skip-intro"}])
def test_off_is_the_lifted_menu_and_says_nothing(exe, env):
    out, err = run(exe, *CALLS, **env)
    assert out == [f"{n} 0 {UNTOUCHED}" for n in (1, 2, 3, 4)]
    assert not [line for line in err if line.startswith(SAY)]


def test_on_the_first_main_menu_after_the_save_goes_to_chao_world_once(exe):
    out, err = run(exe, *CALLS, SA2_TWEAKS=TWEAK)
    assert out == [f"1 0 {UNTOUCHED}",                  # save not loaded yet: the menu alone
                   f"2 0 {UNTOUCHED}",                  # not the main menu: the menu alone
                   "3 1 90 13 0 4 0 0 0 0 0 0 0 1",     # Chao World as Sonic, EAX 1
                   "4 0 90 13 0 4 0 0 0 0 0 0 0 1"]     # done: the menu's own EAX, nothing stored
    assert err == [REPORT, SAY + "the save is loaded: going to Chao World as character 0 instead "
                                 "of the main menu"]


def test_a_main_menu_with_the_save_index_at_minus_two_waits_for_the_save(exe):
    """File Select's entry and its failures, and the title's branch to the main menu without a save,
    leave the index at -2: no save is loaded, so the wrap waits for a real slot and does not finish."""
    out, err = run(exe, "1", "-2", SONIC, "1", "-2", SONIC, "1", "1", SONIC, SA2_TWEAKS=TWEAK)
    assert out == [f"1 0 {UNTOUCHED}", f"2 0 {UNTOUCHED}", "3 1 90 13 0 4 0 0 0 0 0 0 0 1"]
    assert err == [REPORT, SAY + "the save is loaded: going to Chao World as character 0 instead "
                                 "of the main menu"]


@pytest.mark.parametrize("unlock, character", [
    ("0000000001000000", 4),   # Knuckles
    ("0000000000000100", 2),   # Mech Tails, who walks as Tails
    ("0000000000000001", 3),   # Mech Eggman, who walks as Eggman
    ("0000000000010000", 5),   # Rouge
    ("0000000000010001", 3),   # Mech Eggman comes before Rouge in ChaoWorldCharacters
    ("01FF000000000000", 0)])  # Sonic first; any nonzero byte counts
def test_the_first_chao_world_character_the_save_has_opened(exe, unlock, character):
    out, err = run(exe, "1", "1", unlock, SA2_TWEAKS=TWEAK)
    assert out == [f"1 1 90 13 {character} 4 0 0 0 0 0 0 0 1"]
    assert err[1:] == [SAY + f"the save is loaded: going to Chao World as character {character} "
                             "instead of the main menu"]


@pytest.mark.parametrize("unlock", [NONE, "0000FFFF00000000"])
def test_a_save_without_chao_world_keeps_the_main_menu_and_is_said_once(exe, unlock):
    """Bytes 2 and 3 (Tails and Eggman on foot) are no Chao World character's; a later frame
    with Sonic's byte set is still left alone, since the run is done."""
    out, err = run(exe, "1", "0", unlock, "1", "0", unlock, "1", "0", SONIC, SA2_TWEAKS=TWEAK)
    assert out == [f"{n} 0 {UNTOUCHED}" for n in (1, 2, 3)]
    assert err == [REPORT, LOCKED]


@pytest.mark.parametrize("replay", ["SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"])
def test_ignored_in_a_replay(exe, replay):
    out, err = run(exe, *CALLS, SA2_TWEAKS=TWEAK, **{replay: "/dev/null"})
    assert out == [f"{n} 0 {UNTOUCHED}" for n in (1, 2, 3, 4)]
    assert not [line for line in err if line.startswith(SAY)]


def test_a_save_index_that_does_not_read_leaves_the_menu_and_is_said_once(exe):
    out, err = run(exe, "1", "0", SONIC, "1", "0", SONIC, SA2_TWEAKS=TWEAK, CW_FAIL="peek")
    assert out == [f"{n} 0 {UNTOUCHED}" for n in (1, 2)]
    assert err == [REPORT, SAY + "CurrentMenu, the save index, the Chao World bytes or CurrentLevel do "
                                 "not read, so the main menu is shown"]


def test_a_store_that_fails_returns_the_menus_own_answer(exe):
    out, err = run(exe, "1", "0", SONIC, "1", "0", SONIC, SA2_TWEAKS=TWEAK, CW_FAIL="poke")
    assert out == ["1 0 90 13 0 0 0 0 0 0 2 5 4 0", "2 0 90 13 0 0 0 0 0 0 2 5 4 0"]
    assert err == [REPORT, SAY + "0x0174B044 does not write, so the main menu answers for itself"]
