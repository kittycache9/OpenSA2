"""The endless-chao-items tweak (runtime/tweaks/endless-chao-items.c): when a Chao has eaten a
small animal or a Chaos Drive, through the Chao's eating state (0x00566DF0), the item's type goes
back on the player's stack of carried items, and a delivery task starts if the stack was empty.
With the tweak off (or in a replay) the lifted body runs alone.

The tweak's file is built with the pinned zig beside runtime/sa2_tweaks.c, as
test_tweak_drives_to_minimals.py builds its tweak, and linked with a driver that stands in for the
lifted body, the two lifted functions the wrap calls, and the runtime's peek, poke and register
accessors. The guest's memory is one flat buffer at 0x30000 holding what the wrap reads: the Chao's
task and data, the item, its partner and that partner's item data, and the player's record.

The stand-in `__real_sub_566df0` does what the body does in state 1: it moves the state word to 2
and, when the item's partner points back at the item, clears [item + 0x40]. `sub_487260` returns
the player's record in EAX and `sub_549490` counts the delivery tasks started; both enter with the
sentinel at [ESP] and return the way a lifted RET does. Each driver step prints
"<state> <count> <entry 0 type> <entry 0 timer> <delivery tasks> <eax> <esp offset>".
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
FIRST = ("sa2 tweaks: endless chao items: a Chao ate item type {}; it goes back on the stack of "
         "carried items to fall again")

# argv: switches, then steps. A step is `call` (the wrap) or `lifted` (the stand-in alone).
#   type=N     the item's type byte
#   state=S    the Chao's state word
#   count=C    items already carried (each entry type 0x40 + its index, timer 5)
#   nopair     the item's partner does not point back at it
#   noplayer   0x00487260 returns 2, as with no player task
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
#define PLAYER (BASE + 0x6000u)
#define TIMER_SOURCE 0x008AF004u
static uint8_t mem[SIZE];
static uint32_t timer_source = 0x3F800000u;
static uint32_t gpr[SA2_GPR_COUNT];
static unsigned managers, player_calls;
static int noplayer;

static void *where(uint32_t a, uint32_t n) {
    if (a >= BASE && a + n <= BASE + SIZE) return mem + (a - BASE);
    if (a == TIMER_SOURCE && n == 4) return &timer_source;
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
static void wr32(uint32_t a, uint32_t v) { sa2_poke(a, 4, &v); }
uint32_t sa2_rt_get_gpr(const void *state, int idx) { (void)state; return gpr[idx]; }
void sa2_rt_set_gpr(void *state, int idx, uint32_t v) { (void)state; gpr[idx] = v; }
void sa2_trap_note(const char *fmt, ...) { fprintf(stderr, "trap note: %s\n", fmt); }
void sa2_trap_raise(uint32_t status, uint32_t exit_pc) {
    fprintf(stderr, "trap %u at %#010x\n", status, exit_pc);
    exit(7);
}
static void ret_(void) { gpr[SA2_EIP] = rd32(gpr[SA2_ESP]); gpr[SA2_ESP] += 4; }

void *__real_sub_566df0(void *s, uint32_t pc, void *m) {
    (void)s; (void)pc;
    uint32_t task = rd32(gpr[SA2_ESP] + 4), data = rd32(task + 0x34);
    uint16_t st = 0;
    sa2_peek(data + 0x1B6, 2, &st);
    if (st == 1) {
        uint32_t item = rd32(task + 0x3C), pair = rd32(item + 0x40);
        st = 2;
        sa2_poke(data + 0x1B6, 2, &st);
        if (pair && rd32(pair + 0x40) == item) wr32(item + 0x40, 0);
    }
    gpr[SA2_EAX] = 0x1234u;
    ret_();
    return m;
}
static void entered(uint32_t pc, uint32_t va) {
    if (pc != va || gpr[SA2_EIP] != va || rd32(gpr[SA2_ESP]) != SA2_CALL_SENTINEL) exit(5);
}
void *sub_487260(void *s, uint32_t pc, void *m) {
    (void)s;
    entered(pc, 0x00487260u);
    player_calls++;
    if (gpr[SA2_ECX] != 0) exit(6);
    gpr[SA2_EAX] = noplayer ? 2u : PLAYER;
    gpr[SA2_EDX] = 0xD0D0D0D0u;       /* a register the wrap must put back */
    ret_();
    return m;
}
void *sub_549490(void *s, uint32_t pc, void *m) {
    (void)s;
    entered(pc, 0x00549490u);
    managers++;
    gpr[SA2_EBX] = 0xB0B0B0B0u;
    ret_();
    return m;
}
void *__wrap_sub_566df0(void *state, uint32_t pc, void *memory);

int main(int argc, char **argv) {
    int token, direct = 0;
    uint32_t type = 0x16, count = 0, esp = STACK, pair_back = 1;
    uint16_t state = 1;
    for (int i = 1; i < argc; i++) {
        const char *a = argv[i];
        if (!strncmp(a, "type=", 5)) { type = (uint32_t)strtoul(a + 5, NULL, 0); continue; }
        if (!strncmp(a, "state=", 6)) { state = (uint16_t)strtoul(a + 6, NULL, 0); continue; }
        if (!strncmp(a, "count=", 6)) { count = (uint32_t)strtoul(a + 6, NULL, 0); continue; }
        if (!strcmp(a, "nopair")) { pair_back = 0; continue; }
        if (!strcmp(a, "noplayer")) { noplayer = 1; continue; }
        if (!strcmp(a, "notask")) { esp = 0x90000u; continue; }
        if (!strcmp(a, "lifted")) { direct = 1; continue; }
        memset(mem, 0, sizeof mem);
        wr32(TASK + 0x34, DATA);
        wr32(TASK + 0x3C, ITEM);
        wr32(ITEM + 0x40, PAIR);
        wr32(PAIR + 0x40, pair_back ? ITEM : 0);
        wr32(PAIR + 0x3C, HELD);
        wr32(HELD + 0x34, BODY);
        mem[BODY - BASE + 3] = (uint8_t)type;
        sa2_poke(DATA + 0x1B6, 2, &state);
        wr32(PLAYER + 0xA0, count);
        for (uint32_t k = 0; k < count && k < 10; k++) {
            wr32(PLAYER + 0x50 + 8 * k, 0x40 + k);
            wr32(PLAYER + 0x54 + 8 * k, 5);
        }
        wr32(STACK + 4, TASK);
        gpr[SA2_ESP] = esp;
        gpr[SA2_EDX] = 0x11111111u;
        gpr[SA2_EBX] = 0x22222222u;
        managers = 0;
        void *ret = (direct ? __real_sub_566df0 : __wrap_sub_566df0)(&token, 0x00566DF0u, &token);
        if (ret != &token || gpr[SA2_EDX] != 0x11111111u || gpr[SA2_EBX] != 0x22222222u) return 3;
        uint16_t after = 0;
        sa2_peek(DATA + 0x1B6, 2, &after);
        uint32_t n = rd32(PLAYER + 0xA0);
        uint32_t at = count < 10 ? PLAYER + 0x50 + 8 * count : 0;   /* no entry past the last */
        printf("%u %u %#x %u %u %#x %d\n", after, n, at ? rd32(at) : 0, at ? rd32(at + 4) : 0,
               managers, gpr[SA2_EAX], (int)(gpr[SA2_ESP] - esp));
        if (n > count) {                        /* what the game's push also sets */
            if (rd32(PLAYER + 0xAC) != 0x3F800000u) return 4;
        }
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
# state 2, count unchanged, nothing at entry 0, no delivery task, the body's EAX, ESP past the RET.
LIFTED = "2 0 0 0 0 0x1234 4"


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "no-event-bars"}])
def test_off_is_the_lifted_body(exe, env):
    out, err = run(exe, "call", **env)
    assert out == [LIFTED]
    assert run(exe, "lifted", "call")[0] == [LIFTED]
    assert "endless" not in err


def test_on_a_drive_goes_back_and_a_delivery_task_starts(exe):
    """The stack was empty, so the earlier delivery task has left: entry 0 is the type and the
    game's 0x1E, the count is 1, and one delivery task starts. The body's EAX and the register
    state are the body's own."""
    out, err = run(exe, "type=0x16", "call", **ENDLESS)
    assert out == ["2 1 0x16 30 1 0x1234 4"]
    assert err.splitlines() == [ON, FIRST.format(22)]


def test_on_an_item_goes_on_top_of_those_already_carried(exe):
    out, _ = run(exe, "type=0x03", "count=3", "call", **ENDLESS)
    assert out == ["2 4 0x3 30 0 0x1234 4"]    # a delivery task is already running


@pytest.mark.parametrize("kind", [0x00, 0x14, 0x15, 0x18])
def test_every_animal_and_drive_type_comes_back(exe, kind):
    out, _ = run(exe, f"type={kind}", "call", **ENDLESS)
    shown = hex(kind) if kind else "0"      # printf's %#x prints zero without the prefix
    assert out == [f"2 1 {shown} 30 1 0x1234 4"]


def test_a_full_stack_is_left_alone(exe):
    out, err = run(exe, "count=10", "call", **ENDLESS)
    assert out == ["2 10 0 0 0 0x1234 4"]
    assert FIRST.format(22) not in err


def test_a_type_that_is_no_animal_or_drive_is_left_alone(exe):
    out, _ = run(exe, "type=0x19", "call", "type=0xFF", "call", **ENDLESS)
    assert out == [LIFTED, LIFTED]


@pytest.mark.parametrize("state", [0, 2, 3])
def test_a_chao_not_in_state_1_gives_nothing_back(exe, state):
    out, _ = run(exe, f"state={state}", "call", **ENDLESS)
    assert out == [f"{2 if state == 1 else state} 0 0 0 0 0x1234 4"]


def test_a_partner_that_does_not_point_back_means_no_meal(exe):
    """The body moves to state 2 without adding points or clearing the link; nothing goes back."""
    out, _ = run(exe, "nopair", "call", **ENDLESS)
    assert out == [LIFTED]


def test_an_unreadable_stack_argument_runs_the_body_alone(exe):
    out, _ = run(exe, "notask", "call", **ENDLESS)
    assert out == ["1 0 0 0 0 0x1234 4"]   # the body ran alone and the state stayed 1


def test_a_player_record_that_does_not_read_is_said_once(exe):
    out, err = run(exe, "noplayer", "call", "call", **ENDLESS)
    assert out == [LIFTED, LIFTED]
    assert err.splitlines() == [
        ON, "sa2 tweaks: endless chao items: the player's record at 0x00487260 does not read, so "
            "the item stays eaten"]


@pytest.mark.parametrize("replay", ["SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"])
def test_ignored_in_a_replay(exe, replay):
    out, err = run(exe, "call", **ENDLESS, **{replay: "/dev/null"})
    assert out == [LIFTED]
    assert err == ("sa2 tweaks: SA2_TWEAKS=endless-chao-items ignored in a replay (SA2_REPLAY_TICKS "
                   "or SA2_REPLAY_WORDS is set)\n")
