"""somersault-momentum (runtime/tweaks/somersault-momentum.c): the wrap of 0x00723560, the moving
somersault's start, writes the speed Speed.x (float at CharObj2Base+0x64, the CharObj2Base in EDI)
had before the body back after it when that speed was above 1.875, the constant the body stores
(0x00723591, from 0x00904EC0).

The tweak's file is built with the pinned zig beside runtime/sa2_tweaks.c, as test_tweaks.py builds
no-event-bars, and linked with a driver that stands in for the lifted body (which stores 1.875 to
[EDI+0x64]) and the runtime's peek, poke and register accessors. argv[1] is flags ("-" for none),
r (the speed does not read) or w (it does not write); argv[2..] are the speeds before the call.
The driver prints the speed after each call, then "pokes <n>".
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_tweaks as TT  # noqa: E402

RUNTIME = TT.RUNTIME

DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cpu_state.h"
#include "guest_memory.h"

#define BASE 0x01DE9600u
static uint8_t base[0x400];
static uint32_t gpr[8];
static int unreadable, locked, pokes;

int sa2_peek(uint32_t a, uint32_t n, void *out) {
    if (unreadable || a < BASE || a + n > BASE + sizeof base) return SA2_E_RANGE;
    memcpy(out, base + (a - BASE), n);
    return SA2_OK;
}
int sa2_poke(uint32_t a, uint32_t n, const void *in) {
    if (locked || a < BASE || a + n > BASE + sizeof base) return SA2_E_RANGE;
    memcpy(base + (a - BASE), in, n);
    pokes++;
    return SA2_OK;
}
uint32_t sa2_rt_get_gpr(const void *state, int idx) { (void)state; return gpr[idx]; }
void sa2_rt_set_gpr(void *state, int idx, uint32_t v) { (void)state; gpr[idx] = v; }

void *__real_sub_723560(void *state, uint32_t pc, void *memory) {
    (void)state;
    if (pc != 0x00723560u) return NULL;
    const float brake = 1.875f;
    memcpy(base + 0x64, &brake, 4);
    return memory;
}
void *__wrap_sub_723560(void *state, uint32_t pc, void *memory);

int main(int argc, char **argv) {
    if (argc < 2) return 2;
    unreadable = strchr(argv[1], 'r') != NULL;
    locked = strchr(argv[1], 'w') != NULL;
    for (int i = 2; i < argc; i++) {
        const float v = strtof(argv[i], NULL);
        memcpy(base + 0x64, &v, 4);
        gpr[SA2_EDI] = BASE;
        int token;
        if (__wrap_sub_723560(&token, 0x00723560u, &token) != &token) return 3;
        float after;
        memcpy(&after, base + 0x64, 4);
        printf("%g\n", (double)after);
    }
    printf("pokes %d\n", pokes);
    return 0;
}
"""

CLEAN = {k: v for k, v in os.environ.items() if k not in TT.VARS}
ON = "sa2 tweaks: somersault keeps speed (SA2_TWEAKS=somersault-momentum)"


@pytest.fixture(scope="module")
def exe(tmp_path_factory) -> Path:
    if not TT.ZIG.is_file():
        pytest.fail(f"the pinned zig is not present: {TT.ZIG}")
    d = tmp_path_factory.mktemp("somersault_momentum")
    (d / "driver.c").write_text(DRIVER)
    out = d / "sm"
    done = subprocess.run([str(TT.ZIG), "cc", *TT.CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(RUNTIME / "tweaks" / "somersault-momentum.c"), "-o", str(out)],
                          capture_output=True, text=True, env=TT.zig_env(d))
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, flags: str, speeds: list[str], **env: str) -> tuple[list[str], int, list[str]]:
    p = subprocess.run([str(exe), flags or "-", *speeds], capture_output=True, text=True, timeout=30,
                       env=dict(CLEAN, **env))
    assert p.returncode == 0, p.stderr
    lines = p.stdout.splitlines()
    assert lines[-1].startswith("pokes ")
    return lines[:-1], int(lines[-1].split()[1]), p.stderr.splitlines()


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "no-event-bars"}])
def test_off_the_body_brakes_every_speed_and_nothing_is_written(exe, env):
    rows, pokes, _ = run(exe, "", ["3", "1.5", "0", "-2", "5.25"], **env)
    assert rows == ["1.875"] * 5 and pokes == 0


def test_on_a_speed_above_the_brake_is_kept_and_a_slower_one_is_left_to_the_game(exe):
    rows, pokes, err = run(exe, "", ["3", "1.5", "1.875", "1.9", "0", "-2", "5.25"],
                           SA2_TWEAKS="somersault-momentum")
    assert rows == ["3", "1.875", "1.875", "1.9", "1.875", "1.875", "5.25"]
    assert pokes == 3
    assert err == [ON, "sa2 tweaks: somersault momentum: speed 3 kept (the game's 1.875)"]


def test_a_speed_that_is_not_a_number_is_left_to_the_game(exe):
    rows, pokes, _ = run(exe, "", ["nan"], SA2_TWEAKS="somersault-momentum")
    assert rows == ["1.875"] and pokes == 0


def test_a_speed_that_does_not_read_or_write_leaves_the_brake_and_says_so_once(exe):
    for flag, word in (("r", "read"), ("w", "write")):
        rows, pokes, err = run(exe, flag, ["3", "4"], SA2_TWEAKS="somersault-momentum")
        assert rows == ["1.875", "1.875"] and pokes == 0
        assert err == [ON, f"sa2 tweaks: somersault momentum: the speed at 0x01DE9664 does not {word}, "
                           "so the somersault brakes as usual"]
