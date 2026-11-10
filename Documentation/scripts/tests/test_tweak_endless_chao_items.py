"""The endless-chao-items tweak (runtime/tweaks/endless-chao-items.c): when a Chao has eaten a
small animal or a Chaos Drive, through the Chao's eating state (0x00566DF0), the item and its
partner are put back as they were before the meal (the body zeroes the word at +6, sets the dword
at +0x0C to -1 and clears the link at +0x40 to use them up), so the item stays usable. With the
tweak off (or in a replay) the lifted body runs alone.

The tweak's file is built with the pinned zig beside runtime/sa2_tweaks.c, as
test_tweak_drives_to_minimals.py builds its tweak, and linked with a driver that stands in for the
lifted body and the runtime's peek, poke and register accessors. The guest's memory is one flat
buffer at 0x30000 holding what the wrap reads: the Chao's task and data, the item, its partner and
that partner's item data.

The stand-in `__real_sub_566df0` does what the body does in state 1: it moves the state word to 2
and lets go of the item as the body does at 0x005674E5. Each driver step prints
"<state> <item+6> <item+0xC> <item+0x40> <pair+6> <pair+0xC> <pair+0x40> <eax> <esp offset>", the
fields as hex numbers.
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
FIRST = "sa2 tweaks: endless chao items: a Chao ate item type {}; the item was kept usable"

# argv: switches, then steps. A step is `call` (the wrap) or `lifted` (the stand-in alone).
#   type=N     the item's type byte
#   state=S    the Chao's state word
#   nopair     the item's partner does not point back at it
#   notask     the stack argument points at memory that does not read
DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cpu_state.h"
#include "guest_memory.h"
#include "sa2_call.h"

#define BASE   0x30000u
#define SIZE   0x9000u
#define STACK  0x38000u                 /* the guest stack, inside the buffer */
#define TASK   (BASE + 0x0000u)
#define DATA   (BASE + 0x1000u)
#define ITEM   (BASE + 0x2000u)
#define PAIR   (BASE + 0x3000u)
#define HELD   (BASE + 0x4000u)
#define BODY   (BASE + 0x5000u)
static uint8_t mem[SIZE];
static uint32_t gpr[SA2_GPR_COUNT];

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
    if (!p) return 1;
    memcpy(p, in, n);
    return SA2_OK;
}
static uint32_t rd32(uint32_t a) { uint32_t v = 0; sa2_peek(a, 4, &v); return v; }
static uint32_t rd16(uint32_t a) { uint16_t v = 0; sa2_peek(a, 2, &v); return v; }
static void wr32(uint32_t a, uint32_t v) { sa2_poke(a, 4, &v); }
static void wr16(uint32_t a, uint16_t v) { sa2_poke(a, 2, &v); }
uint32_t sa2_rt_get_gpr(const void *state, int idx) { (void)state; return gpr[idx]; }
void sa2_rt_set_gpr(void *state, int idx, uint32_t v) { (void)state; gpr[idx] = v; }
void sa2_trap_note(const char *fmt, ...) { fprintf(stderr, "trap note: %s\n", fmt); }
void sa2_trap_raise(uint32_t status, uint32_t exit_pc) {
    fprintf(stderr, "trap %u at %#010x\n", status, exit_pc);
    exit(7);
}
static void ret_(void) { gpr[SA2_EIP] = rd32(gpr[SA2_ESP]); gpr[SA2_ESP] += 4; }

/* 0x005674E5 to 0x00567511: the item, and its partner when it points back, are let go. */
static void let_go(uint32_t t, uint32_t other_back) {
    (void)other_back;
    wr16(t + 6, 0);
    wr32(t + 0x40, 0);
    wr32(t + 0x0C, 0xFFFFFFFFu);
}
void *__real_sub_566df0(void *s, uint32_t pc, void *m) {
    (void)s; (void)pc;
    uint32_t task = rd32(gpr[SA2_ESP] + 4), data = rd32(task + 0x34);
    if (rd16(data + 0x1B6) == 1) {
        uint32_t item = rd32(task + 0x3C), pair = rd32(item + 0x40);
        wr16(data + 0x1B6, 2);
        let_go(item, 0);
        if (pair && rd32(pair + 0x40) == item) let_go(pair, 0);
    }
    gpr[SA2_EAX] = 0x1234u;
    ret_();
    return m;
}
void *__wrap_sub_566df0(void *state, uint32_t pc, void *memory);

int main(int argc, char **argv) {
    int token, direct = 0;
    uint32_t type = 0x16, esp = STACK, pair_back = 1;
    uint16_t state = 1;
    for (int i = 1; i < argc; i++) {
        const char *a = argv[i];
        if (!strncmp(a, "type=", 5)) { type = (uint32_t)strtoul(a + 5, NULL, 0); continue; }
        if (!strncmp(a, "state=", 6)) { state = (uint16_t)strtoul(a + 6, NULL, 0); continue; }
        if (!strcmp(a, "nopair")) { pair_back = 0; continue; }
        if (!strcmp(a, "notask")) { esp = 0x90000u; continue; }
        if (!strcmp(a, "lifted")) { direct = 1; continue; }
        memset(mem, 0, sizeof mem);
        wr32(TASK + 0x34, DATA);
        wr32(TASK + 0x3C, ITEM);
        wr32(ITEM + 0x40, PAIR);
        wr16(ITEM + 6, 0x77);
        wr32(ITEM + 0x0C, 0x1234);
        wr32(PAIR + 0x40, pair_back ? ITEM : 0);
        wr16(PAIR + 6, 0x55);
        wr32(PAIR + 0x0C, 0x4321);
        wr32(PAIR + 0x3C, HELD);
        wr32(HELD + 0x34, BODY);
        mem[BODY - BASE + 3] = (uint8_t)type;
        wr16(DATA + 0x1B6, state);
        wr32(STACK + 4, TASK);
        gpr[SA2_ESP] = esp;
        gpr[SA2_EDX] = 0x11111111u;
        gpr[SA2_EBX] = 0x22222222u;
        void *ret = (direct ? __real_sub_566df0 : __wrap_sub_566df0)(&token, 0x00566DF0u, &token);
        if (ret != &token || gpr[SA2_EDX] != 0x11111111u || gpr[SA2_EBX] != 0x22222222u) return 3;
        printf("%x %x %x %x %x %x %x %#x %d\n", rd16(DATA + 0x1B6), rd16(ITEM + 6), rd32(ITEM + 0x0C),
               rd32(ITEM + 0x40), rd16(PAIR + 6), rd32(PAIR + 0x0C), rd32(PAIR + 0x40), gpr[SA2_EAX],
               (int)(gpr[SA2_ESP] - esp));
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
# state 2, the item's word 0, field -1, link 0, the partner's the same, the body's EAX, ESP past the RET.
LIFTED = "2 0 ffffffff 0 0 ffffffff 0 0x1234 4"
# state 2 and both tasks as they were before the meal: item word 0x77, field 0x1234, link PAIR
# (0x33000); partner word 0x55, field 0x4321, link ITEM (0x32000).
KEPT = "2 77 1234 33000 55 4321 32000 0x1234 4"


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "no-event-bars"}])
def test_off_is_the_lifted_body(exe, env):
    out, err = run(exe, "call", **env)
    assert out == [LIFTED]
    assert run(exe, "lifted", "call")[0] == [LIFTED]
    assert "endless" not in err


def test_on_the_item_and_its_partner_are_as_before_the_meal(exe):
    """The body's EAX, the state and the register state are the body's own; only the fields it
    used the item up with are put back."""
    out, err = run(exe, "type=0x16", "call", **ENDLESS)
    assert out == [KEPT]
    assert err.splitlines() == [ON, FIRST.format(22)]


@pytest.mark.parametrize("kind", [0x00, 0x14, 0x15, 0x18])
def test_every_animal_and_drive_type_is_kept(exe, kind):
    out, _ = run(exe, f"type={kind}", "call", **ENDLESS)
    assert out == [KEPT]


def test_it_is_said_once(exe):
    _, err = run(exe, "call", "call", **ENDLESS)
    assert err.splitlines() == [ON, FIRST.format(22)]


def test_a_type_that_is_no_animal_or_drive_is_left_alone(exe):
    out, _ = run(exe, "type=0x19", "call", "type=0xFF", "call", **ENDLESS)
    assert out == [LIFTED, LIFTED]


@pytest.mark.parametrize("state", [0, 2, 3])
def test_a_chao_not_in_state_1_keeps_nothing(exe, state):
    """Outside state 1 the body does not let go of the item, so there is nothing to put back."""
    out, _ = run(exe, f"state={state}", "call", **ENDLESS)
    assert out == [f"{state} 77 1234 33000 55 4321 32000 0x1234 4"]


def test_a_partner_that_does_not_point_back_means_no_meal(exe):
    """The body lets go of the item alone and the wrap sees no meal, so the item stays let go."""
    out, _ = run(exe, "nopair", "call", **ENDLESS)
    assert out == ["2 0 ffffffff 0 55 4321 0 0x1234 4"]


def test_an_unreadable_stack_argument_runs_the_body_alone(exe):
    out, _ = run(exe, "notask", "call", **ENDLESS)
    assert out == ["1 77 1234 33000 55 4321 32000 0x1234 4"]   # the body ran alone and the state stayed 1


@pytest.mark.parametrize("replay", ["SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"])
def test_ignored_in_a_replay(exe, replay):
    out, err = run(exe, "call", **ENDLESS, **{replay: "/dev/null"})
    assert out == [LIFTED]
    assert err == ("sa2 tweaks: SA2_TWEAKS=endless-chao-items ignored in a replay (SA2_REPLAY_TICKS "
                   "or SA2_REPLAY_WORDS is set)\n")
