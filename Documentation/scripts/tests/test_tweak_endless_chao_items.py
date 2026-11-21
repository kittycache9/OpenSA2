"""The endless-chao-items tweak (runtime/tweaks/endless-chao-items.c): a Chaos Drive or a small
animal a Chao lets go of is not used up. The drive's held mode (0x00544F30, ESI = the task) is put
back in mode 1 in place of mode 4, and the animal's (0x00548870, EAX = the task) gets its "eaten"
dword at work + 0xF4 back; both get their garden save record back. With the tweak off (or in a
replay) the lifted bodies run alone.

The tweak's file is built with the pinned zig beside runtime/sa2_tweaks.c, as
test_tweak_gold_beetle_stays.py builds its tweak, and linked with a driver that stands in for the
lifted bodies and the runtime's peek, poke and register accessors. The guest's memory is one flat
buffer at 0x30000: the item's task, its work (twp), its link entry, its save record and the stack.

The stand-in bodies do what the game's do once the Chao has let go (0x0056D170 answers 0): the
drive clears the record (the words at + 0 and + 2 to -1, the rest of its 0x14 bytes to 0) and
[entry + 8], and sets mode 4, step 0; the animal sets + 0xF4 to 1, clears the record the same
way, clears flags 0x240 of the word at work + 4, and sets mode 0, step 0. With `held` the Chao
still holds the item and the bodies change nothing. Each driver step prints
"<mode> <step> <[entry+8]> <the record's 0x14 bytes> <[twp+0xF4]> <[twp+4]> <ESP offset>".
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
VARS = ("SA2_TWEAKS", "SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS")
ON = "sa2 tweaks: endless chao items (SA2_TWEAKS=endless-chao-items)"
KEPT_DRIVE = "sa2 tweaks: endless chao items: a Chao ate a Chaos Drive; it was kept for another go"
KEPT_ANIMAL = "sa2 tweaks: endless chao items: a Chao ate a small animal; it was kept for another go"

# argv: switches, then steps. A step is `drive` or `animal` (the wrap), or `lifted-drive` or
# `lifted-animal` (the stand-in alone). Switches hold for the steps after them:
#   mode=N     the item's mode byte before the call (default: the holding mode, 3 or 2)
#   eaten=N    the animal's dword at work + 0xF4 before the call (default 0)
#   held       the Chao still holds the item: the body changes nothing
#   norec      [entry + 8] is 0: the item has no save record
#   notask     the task register points at memory that does not read
#   rorec      the record does not write
DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cpu_state.h"
#include "guest_memory.h"

#define BASE   0x30000u
#define SIZE   0x8000u
#define TASK   (BASE + 0x0000u)
#define TWP    (BASE + 0x1000u)
#define ENTRY  (BASE + 0x2000u)
#define REC    (BASE + 0x3000u)
#define STACK  (BASE + 0x7000u)
static uint8_t mem[SIZE];
static uint32_t gpr[SA2_GPR_COUNT];
static int held, rorec;

static void *where(uint32_t a, uint32_t n) {
    if (a >= BASE && a + n <= BASE + SIZE) return mem + (a - BASE);
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
    if (!p || (rorec && a < REC + 0x14u && a + n > REC)) return 1;
    memcpy(p, in, n);
    return SA2_OK;
}
static uint32_t rd32(uint32_t a) { uint32_t v = 0; sa2_peek(a, 4, &v); return v; }
static uint32_t rd8(uint32_t a) { uint8_t v = 0; sa2_peek(a, 1, &v); return v; }
static void wr32(uint32_t a, uint32_t v) { uint8_t *p = where(a, 4); if (p) memcpy(p, &v, 4); }
static void wr16(uint32_t a, uint16_t v) { uint8_t *p = where(a, 2); if (p) memcpy(p, &v, 2); }
static void wr8(uint32_t a, uint8_t v) { uint8_t *p = where(a, 1); if (p) *p = v; }
uint32_t sa2_rt_get_gpr(const void *state, int idx) { (void)state; return gpr[idx]; }
void sa2_rt_set_gpr(void *state, int idx, uint32_t v) { (void)state; gpr[idx] = v; }
static void ret_(void) { gpr[SA2_EIP] = rd32(gpr[SA2_ESP]); gpr[SA2_ESP] += 4; }

/* 0x00544FC7-0x0054500D and 0x00548948-0x0054898E: the save record cleared, [entry + 8] = 0. */
static void clear_record(uint32_t task) {
    uint32_t entry = rd32(task + 0x3C), rec = entry ? rd32(entry + 8) : 0;
    if (!rec) return;
    uint8_t *p = where(rec, 0x14);
    if (!p) return;
    memset(p, 0, 0x14);
    memset(p, 0xFF, 4);
    wr32(entry + 8, 0);
}
void *__real_sub_544f30(void *s, uint32_t pc, void *m) {
    (void)s; (void)pc;
    uint32_t task = gpr[SA2_ESI], twp = rd32(task + 0x34);
    if (!held && where(twp, 2)) {
        clear_record(task);
        wr8(twp, 4);
        wr8(twp + 1, 0);
    }
    gpr[SA2_EAX] = gpr[SA2_ECX] = gpr[SA2_EDX] = 0xDEADu;
    ret_();
    return m;
}
void *__real_sub_548870(void *s, uint32_t pc, void *m) {
    (void)s; (void)pc;
    uint32_t task = gpr[SA2_EAX], twp = rd32(task + 0x34);
    if (!held && where(twp, 0xF8)) {
        wr32(twp + 0xF4, 1);
        clear_record(task);
        wr16(twp + 4, (uint16_t)(rd32(twp + 4) & 0xFDBFu));
        wr8(twp, 0);
        wr8(twp + 1, 0);
    }
    gpr[SA2_EAX] = gpr[SA2_ECX] = gpr[SA2_EDX] = 0xDEADu;
    ret_();
    return m;
}
void *__wrap_sub_544f30(void *state, uint32_t pc, void *memory);
void *__wrap_sub_548870(void *state, uint32_t pc, void *memory);

int main(int argc, char **argv) {
    int token, mode = -1, norec = 0, notask = 0;
    uint32_t eaten = 0;
    for (int i = 1; i < argc; i++) {
        const char *a = argv[i];
        if (!strncmp(a, "mode=", 5)) { mode = (int)strtol(a + 5, NULL, 0); continue; }
        if (!strncmp(a, "eaten=", 6)) { eaten = (uint32_t)strtoul(a + 6, NULL, 0); continue; }
        if (!strcmp(a, "held")) { held = 1; continue; }
        if (!strcmp(a, "norec")) { norec = 1; continue; }
        if (!strcmp(a, "notask")) { notask = 1; continue; }
        if (!strcmp(a, "rorec")) { rorec = 1; continue; }
        const int drive = strstr(a, "drive") != NULL, lifted = !strncmp(a, "lifted", 6);
        memset(mem, 0, sizeof mem);
        wr32(TASK + 0x34, TWP);
        wr32(TASK + 0x3C, ENTRY);
        wr32(ENTRY + 8, norec ? 0 : REC);
        for (uint32_t k = 0; k < 0x14; k++) wr8(REC + k, (uint8_t)(0x40 + k));
        wr8(TWP, (uint8_t)(mode >= 0 ? mode : drive ? 3 : 2));
        wr8(TWP + 1, 2);
        wr16(TWP + 4, 0x0261);
        wr32(TWP + 0xF4, eaten);
        wr32(STACK, 0x005450F8u);
        const uint32_t task = notask ? 0x90000u : TASK;
        gpr[SA2_ESP] = STACK;
        gpr[SA2_ESI] = drive ? task : 0x33333333u;
        gpr[SA2_EAX] = drive ? 0x44444444u : task;
        gpr[SA2_EBX] = 0x22222222u;
        void *ret = drive ? (lifted ? __real_sub_544f30 : __wrap_sub_544f30)(&token, 0x00544F30u, &token)
                          : (lifted ? __real_sub_548870 : __wrap_sub_548870)(&token, 0x00548870u, &token);
        if (ret != &token || gpr[SA2_EBX] != 0x22222222u || gpr[SA2_EIP] != 0x005450F8u) return 3;
        if (drive && gpr[SA2_ESI] != task) return 4;
        printf("%x %x %x ", rd8(TWP), rd8(TWP + 1), rd32(ENTRY + 8));
        for (uint32_t k = 0; k < 0x14; k++) printf("%02x", rd8(REC + k));
        printf(" %x %x %d\n", rd32(TWP + 0xF4), rd32(TWP + 4) & 0xFFFFu, (int)(gpr[SA2_ESP] - STACK));
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
    d = tmp_path_factory.mktemp("endless_chao_items")
    (d / "driver.c").write_text(DRIVER)
    out = d / "eci"
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(RUNTIME / "tweaks" / "endless-chao-items.c"), "-o", str(out)],
                          capture_output=True, text=True, env=zig_env(d))
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, *steps: str, **env: str) -> tuple[list[str], str]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS}
    p = subprocess.run([str(exe), *steps], capture_output=True, text=True, timeout=30,
                       env=dict(clean, **env))
    assert p.returncode == 0, (p.returncode, p.stderr)
    return p.stdout.splitlines(), p.stderr


ENDLESS = {"SA2_TWEAKS": "endless-chao-items"}
SAVED = "404142434445464748494a4b4c4d4e4f50515253"            # the record before the call
CLEARED = "ffffffff" + "00" * 16                                 # as the bodies leave it
# Each line: mode, step, [entry + 8], the record, [twp + 0xF4], [twp + 4], ESP past the RET.
DRIVE_USED = f"4 0 0 {CLEARED} 0 261 4"
DRIVE_KEPT = f"1 0 33000 {SAVED} 0 261 4"
ANIMAL_USED = f"0 0 0 {CLEARED} 1 21 4"
ANIMAL_KEPT = f"0 0 33000 {SAVED} 0 21 4"


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "no-event-bars"}])
def test_off_is_the_lifted_body(exe, env):
    out, err = run(exe, "drive", "animal", **env)
    assert out == [DRIVE_USED, ANIMAL_USED]
    assert run(exe, "lifted-drive", "lifted-animal")[0] == [DRIVE_USED, ANIMAL_USED]
    assert "endless" not in err


def test_on_a_drive_let_go_lies_free_with_its_record(exe):
    """Mode 1, step 0 (its set-up runs again), and the record back in the entry as it was."""
    out, err = run(exe, "drive", **ENDLESS)
    assert out == [DRIVE_KEPT]
    assert err.splitlines() == [ON, KEPT_DRIVE]


def test_on_an_animal_let_go_is_not_eaten_and_keeps_its_record(exe):
    """Mode 0 and the flags at work + 4 are the body's; + 0xF4 and the record are as before."""
    out, err = run(exe, "animal", **ENDLESS)
    assert out == [ANIMAL_KEPT]
    assert err.splitlines() == [ON, KEPT_ANIMAL]


def test_an_animal_already_marked_eaten_keeps_its_old_mark(exe):
    out, _ = run(exe, "eaten=1", "animal", **ENDLESS)
    assert out == [f"0 0 33000 {SAVED} 1 21 4"]


def test_the_first_item_kept_is_said_once(exe):
    out, err = run(exe, "drive", "animal", "drive", **ENDLESS)
    assert out == [DRIVE_KEPT, ANIMAL_KEPT, DRIVE_KEPT]
    assert err.splitlines() == [ON, KEPT_DRIVE]


def test_an_item_with_no_record_is_kept_without_one(exe):
    out, _ = run(exe, "norec", "drive", "animal", **ENDLESS)
    assert out == [f"1 0 0 {SAVED} 0 261 4", f"0 0 0 {SAVED} 0 21 4"]


def test_an_item_the_chao_still_holds_is_untouched(exe):
    out, err = run(exe, "held", "drive", "animal", **ENDLESS)
    assert out == [f"3 2 33000 {SAVED} 0 261 4", f"2 2 33000 {SAVED} 0 261 4"]
    assert err.splitlines() == [ON]


@pytest.mark.parametrize("mode", [0, 1, 4])
def test_a_drive_not_in_its_held_mode_is_the_body_alone(exe, mode):
    out, err = run(exe, f"mode={mode}", "drive", **ENDLESS)
    assert out == [DRIVE_USED]
    assert err.splitlines() == [ON]


@pytest.mark.parametrize("mode", [0, 1, 3])
def test_an_animal_not_in_its_held_mode_is_the_body_alone(exe, mode):
    out, err = run(exe, f"mode={mode}", "animal", **ENDLESS)
    assert out == [ANIMAL_USED]
    assert err.splitlines() == [ON]


def test_an_unreadable_task_is_said_once_and_left_to_the_body(exe):
    out, err = run(exe, "notask", "drive", "animal", **ENDLESS)
    assert out == [f"3 2 33000 {SAVED} 0 261 4", f"2 2 33000 {SAVED} 0 261 4"]
    assert err.splitlines() == [ON, "sa2 tweaks: endless chao items: a Chaos Drive's work does not "
                                "read (task 0x00090000), so it is used up as usual"]


def test_a_record_that_does_not_write_is_said_once_and_left_used_up(exe):
    """The record is the first write, so nothing is written: the item is as the body left it."""
    out, err = run(exe, "rorec", "drive", "animal", **ENDLESS)
    assert out == [DRIVE_USED, ANIMAL_USED]
    assert err.splitlines() == [ON, "sa2 tweaks: endless chao items: a Chaos Drive's work does not "
                                "write (task 0x00030000), so it is used up as usual"]


@pytest.mark.parametrize("replay", ["SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"])
def test_ignored_in_a_replay(exe, replay):
    out, err = run(exe, "drive", "animal", **ENDLESS, **{replay: "/dev/null"})
    assert out == [DRIVE_USED, ANIMAL_USED]
    assert err == ("sa2 tweaks: SA2_TWEAKS=endless-chao-items ignored in a replay (SA2_REPLAY_TICKS "
                   "or SA2_REPLAY_WORDS is set)\n")
