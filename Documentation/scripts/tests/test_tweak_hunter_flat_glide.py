"""hunter-flat-glide (runtime/tweaks/hunter-flat-glide.c): after the glide's acceleration,
0x0045DF10 (EAX = the CharObj2Base, ECX = the EntityData1), Acc.y (float at CharObj2Base+0x5C) is
-Speed.y (float at +0x68) when the word at EntityData1+4 has no 0x4000 bit, so the integration's
`Speed.y += Acc.y` (0x004613DD) leaves Speed.y at 0.

The tweak's file is built with the pinned zig beside runtime/sa2_tweaks.c, as test_tweaks.py builds
no-event-bars, and linked with a driver that stands in for the lifted body (which stores a sinking
Acc.y of -0.05 unless the flag is set, as 0x0045DF24 returns early) and the runtime's peek, poke and
register accessors. argv[1] is flags ("-" for none), r (the flags do not read), s (the speed does
not read) or w (nothing writes); argv[2..] are frames, "<speed_y>,<flags hex>". The driver then
runs the integration, Speed.y += Acc.y, and prints "<acc_y> <speed_y>" after each frame, then
"pokes <n>".
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

#define BASE  0x01DE9600u
#define DATA1 0x01DEA000u
static uint8_t base[0x400], data1[0x40];
static uint32_t gpr[8];
static int unreadable_flags, unreadable_speed, locked, pokes;

static uint8_t *at(uint32_t a, uint32_t n) {
    if (a >= BASE && a + n <= BASE + sizeof base) return base + (a - BASE);
    if (a >= DATA1 && a + n <= DATA1 + sizeof data1) return data1 + (a - DATA1);
    return NULL;
}
int sa2_peek(uint32_t a, uint32_t n, void *out) {
    uint8_t *p = at(a, n);
    if (!p || (unreadable_flags && a == DATA1 + 4) || (unreadable_speed && a == BASE + 0x68)) return SA2_E_RANGE;
    memcpy(out, p, n);
    return SA2_OK;
}
int sa2_poke(uint32_t a, uint32_t n, const void *in) {
    uint8_t *p = at(a, n);
    if (!p || locked) return SA2_E_RANGE;
    memcpy(p, in, n);
    pokes++;
    return SA2_OK;
}
uint32_t sa2_rt_get_gpr(const void *state, int idx) { (void)state; return gpr[idx]; }
void sa2_rt_set_gpr(void *state, int idx, uint32_t v) { (void)state; gpr[idx] = v; }

void *__real_sub_45df10(void *state, uint32_t pc, void *memory) {
    (void)state;
    if (pc != 0x0045DF10u) return NULL;
    uint16_t flags;
    memcpy(&flags, data1 + 4, 2);
    if (flags & 0x4000) return memory;
    const float sink = -0.05f;
    memcpy(base + 0x5C, &sink, 4);
    return memory;
}
void *__wrap_sub_45df10(void *state, uint32_t pc, void *memory);

int main(int argc, char **argv) {
    if (argc < 2) return 2;
    unreadable_flags = strchr(argv[1], 'r') != NULL;
    unreadable_speed = strchr(argv[1], 's') != NULL;
    locked = strchr(argv[1], 'w') != NULL;
    for (int i = 2; i < argc; i++) {
        char *e;
        float speed = strtof(argv[i], &e);
        const uint16_t flags = (uint16_t)strtol(e + 1, NULL, 16);
        const float untouched = 7.0f;
        memcpy(base + 0x5C, &untouched, 4);
        memcpy(base + 0x68, &speed, 4);
        memcpy(data1 + 4, &flags, 2);
        gpr[SA2_EAX] = BASE;
        gpr[SA2_ECX] = DATA1;
        int token;
        if (__wrap_sub_45df10(&token, 0x0045DF10u, &token) != &token) return 3;
        float acc;
        memcpy(&acc, base + 0x5C, 4);
        speed += acc;
        printf("%g %g\n", (double)acc, (double)speed);
    }
    printf("pokes %d\n", pokes);
    return 0;
}
"""

CLEAN = {k: v for k, v in os.environ.items() if k not in TT.VARS}
ON = "sa2 tweaks: glide held level (SA2_TWEAKS=hunter-flat-glide)"


@pytest.fixture(scope="module")
def exe(tmp_path_factory) -> Path:
    if not TT.ZIG.is_file():
        pytest.fail(f"the pinned zig is not present: {TT.ZIG}")
    d = tmp_path_factory.mktemp("hunter_flat_glide")
    (d / "driver.c").write_text(DRIVER)
    out = d / "hg"
    done = subprocess.run([str(TT.ZIG), "cc", *TT.CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(RUNTIME / "tweaks" / "hunter-flat-glide.c"), "-o", str(out)],
                          capture_output=True, text=True, env=TT.zig_env(d))
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, flags: str, frames: list[str], **env: str) -> tuple[list[str], int, list[str]]:
    p = subprocess.run([str(exe), flags or "-", *frames], capture_output=True, text=True, timeout=30,
                       env=dict(CLEAN, **env))
    assert p.returncode == 0, p.stderr
    lines = p.stdout.splitlines()
    assert lines[-1].startswith("pokes ")
    return lines[:-1], int(lines[-1].split()[1]), p.stderr.splitlines()


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "no-event-bars"}])
def test_off_the_glide_sinks_and_nothing_is_written(exe, env):
    rows, pokes, _ = run(exe, "", ["-0.5,0", "-1,0", "0,0"], **env)
    assert rows == ["-0.05 -0.55", "-0.05 -1.05", "-0.05 -0.05"] and pokes == 0


def test_on_the_speed_stays_where_it_is_while_the_flag_is_clear(exe):
    rows, pokes, err = run(exe, "", ["-0.5,0", "-1,0", "0,0", "-0.25,3fff"], SA2_TWEAKS="hunter-flat-glide")
    assert rows == ["0.5 0", "1 0", "-0 0", "0.25 0"]
    assert pokes == 4
    assert err == [ON, "sa2 tweaks: hunter flat glide: glide held level (Speed.y -0.5 -> 0)"]


def test_on_with_the_flag_set_the_body_returns_early_and_nothing_is_written(exe):
    rows, pokes, _ = run(exe, "", ["-0.5,4000", "-1,c000"], SA2_TWEAKS="hunter-flat-glide")
    assert rows == ["7 6.5", "7 6"] and pokes == 0


@pytest.mark.parametrize("flag", ["r", "s", "w"])
def test_a_word_that_does_not_read_or_write_leaves_the_glide_as_the_game_has_it(exe, flag):
    rows, pokes, err = run(exe, flag, ["-0.5,0"], SA2_TWEAKS="hunter-flat-glide")
    assert rows == ["-0.05 -0.55"] and pokes == 0
    assert len(err) == 2 and err[0] == ON and "so the glide sinks as usual" in err[1]
