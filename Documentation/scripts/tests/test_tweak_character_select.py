"""character-select (runtime/tweaks/character-select.c): Y on a Stage Select stage opens Chao
World's character picker in that stage's sub-menu, and the character picked there starts the stage.

The tweak's file is built with the pinned zig beside runtime/sa2_tweaks.c, as test_tweaks.py builds
no-event-bars, and linked with a driver that stands in for the three lifted functions it wraps and
the runtime's guest memory. Guest memory holds CurrentSubMenu (0x01D7BB14), MenuButtons_Pressed
(0x01DEFAB0), the sub-menu's row (0x01D1BF05), the highlighted tile (0x01D1BF08), StageSelectLevels
(0x00C75078) and ChaoWorldCharacters (0x00C75368) with the image's values, the characters' open
bytes (0x01DEF829 + character), CurrentCharacter (0x01934B80), ControllerPointers[0] (0x01DEFB60)
and the PDS_PERIPHERAL it points to, whose press word is at +0x10, and each character's start
position list (LoadStartPosition's, 0x0043D931) with the image's levels in it. The panels'
sprites (0x00C68C50, 20 bytes each) have sx = 1000 + their index, and the level name table
(0x008A0560) gives City Escape sprite 8, Security Hall 0x1F and every other level 0x10.

The stand-ins model the arms the tweak's header reads:

  __real_sub_672760  Menu_StageSelect. Map (2): B leaves (4); A opens the tile's sub-menu (3, row
                     0), except Green Hill, which starts (5). Sub-menu (3): B back to the map, left
                     and right move the row (each prints "sound 8000"), A asks sub_678460 (modelled
                     on sub_676640's answer) and fades out (5) or prints "sound 800A". Fade done (5):
                     calls sub_678250.
  __real_sub_676640  type 2 for Chao World, 1 for the kart race, else 0, into the struct at ECX.
  __real_sub_678250  type 0: CurrentCharacter = the tile's Character, returns 1; type 2 returns 3.

Each argument is one frame, comma-separated key=value pairs: sub (CurrentSubMenu, kept), tile (kept),
row (kept), press (player 1's press word, 0 when left out), menu (MenuButtons_Pressed, 0 when left
out), shut=<character> (greys that character), draw (calls sub_676640 as the drawer would and prints
the type; for type 2 also the icons and the sx of the title sprite 0x28 and the box sprite 0x58 as
the drawer sees them). A frame prints "sub=<CurrentSubMenu> row=<row> menu=<MenuButtons_Pressed> char=<
CurrentCharacter>" after any "sound" or "type" lines.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from test_tweaks import CC, RUNTIME, VARS, ZIG, zig_env

ON = {"SA2_TWEAKS": "character-select"}
REPORT = "sa2 tweaks: character pick in Stage Select (SA2_TWEAKS=character-select)"
SAID = "sa2 tweaks: character select: "
A, B, LEFT, RIGHT, Y = 0x04, 0x02, 0x40, 0x80, 0x200
MAP, ITEMS, LEAVE, START = 2, 3, 4, 5
CITY_ESCAPE, GREEN_HILL, KART, CHAO_WORLD, CANNONS_CORE = 23, 17, 18, 22, 29   # tile indices
SECURITY_HALL, ETERNAL_ENGINE, RADICAL_HIGHWAY = 10, 25, 19

DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cpu_state.h"
#include "guest_memory.h"

#define SUB_MENU  0x01D7BB14u
#define MENU      0x01DEFAB0u
#define ROW       0x01D1BF05u
#define TILE      0x01D1BF08u
#define LEVELS    0x00C75078u
#define CW_CHARS  0x00C75368u
#define CW_OPEN   0x01DEF829u
#define TILE_OPEN 0x01A4A974u
#define CHARACTER 0x01934B80u
#define PADS      0x01DEFB60u
#define PAD       0x01DEF000u
#define OUT       0x00010000u   /* the struct sub_676640 fills */

static const int32_t levels[33][4] = {
    {23,6,19,1},{28,0,19,4},{25,4,19,5},{26,5,18,5},{21,7,17,1},{18,5,15,2},{12,7,15,5},{9,6,11,6},
    {16,4,15,1},{11,7,11,4},{8,5,11,5},{10,0,13,6},{6,1,13,1},{4,1,10,4},{3,0,10,5},{5,4,11,1},
    {7,4,9,2},{31,0,8,6},{70,5,6,5},{14,1,4,4},{70,6,4,6},{17,6,6,3},{90,0,4,1},{13,0,1,4},
    {22,0,29,3},{24,6,25,2},{43,7,24,3},{32,4,23,4},{27,7,25,1},{34,0,26,4},{40,1,28,6},{30,0,27,5},
    {44,5,24,6}};
static const int32_t cw_chars[6] = {0, 6, 4, 1, 7, 5};
static uint32_t sub_menu = 2, menu, character = 99, pad_ptr = PAD, pad[8], out[3];
static int8_t row, tile = 23, count = 5;
static uint8_t open[8] = {1, 0, 0, 0, 0, 0, 0, 0};   /* the save's Chao World bytes: Sonic's only */
static uint32_t tile_open[33];
static uint32_t ecx, eax;
#define SPRITES 0x00C68C50u
#define NAMES   0x008A0560u
static int16_t sprites[0x80][10];
static uint32_t name_of;
/* The start lists: {character, list address, its levels then 0x5B}, the image's. */
static const struct { int32_t c; uint32_t at; int16_t levels[24]; } starts[] = {
    {0, 0x01746F20u, {0,3,6,10,57,13,4,14,19,22,28,30,40,34,64,66,42,31,52,54,0x5B}},
    {1, 0x017472C0u, {0,3,4,6,13,10,57,14,19,22,66,42,30,40,31,52,54,0x5B}},
    {4, 0x017475D8u, {0,5,7,16,25,63,33,41,38,32,46,49,50,8,26,44,59,0x5B}},
    {5, 0x017478F0u, {0,5,8,25,26,18,16,33,41,37,32,44,46,49,50,59,0x5B}},
    {6, 0x01748008u, {0,9,12,15,17,20,23,24,29,39,36,45,51,55,47,48,56,58,53,0x5B}},
    {7, 0x01747C40u, {0,11,12,15,20,21,24,27,29,39,67,35,45,43,51,55,47,48,53,56,58,0x5B}},
};

static void *at(uint32_t a, uint32_t n) {
    if (a == SUB_MENU && n == 4) return &sub_menu;
    if (a == MENU && n == 1) return &menu;
    if (a == ROW && n == 1) return &row;
    if (a == TILE && n == 1) return &tile;
    if (a == CHARACTER && n == 4) return &character;
    if (a == PADS && n == 4) return &pad_ptr;
    if (n == 4 && a >= PAD && a < PAD + sizeof pad && (a & 3) == 0) return &pad[(a - PAD) / 4];
    if (n == 4 && a >= OUT && a < OUT + sizeof out && (a & 3) == 0) return &out[(a - OUT) / 4];
    if (n == 4 && a >= LEVELS && a < LEVELS + sizeof levels && (a & 3) == 0)
        return (void *)&((const int32_t *)levels)[(a - LEVELS) / 4];
    if (n == 4 && a >= CW_CHARS && a < CW_CHARS + 24 && (a & 3) == 0)
        return (void *)&cw_chars[(a - CW_CHARS) / 4];
    if (n == 1 && a >= CW_OPEN && a < CW_OPEN + 8) return &open[a - CW_OPEN];
    if (n == 1 && a == 0x0174AFD1u) { static uint8_t english; return &english; }
    if (a >= SPRITES && a + n <= SPRITES + sizeof sprites) return (uint8_t *)sprites + (a - SPRITES);
    if (n == 4 && a >= NAMES && a < NAMES + 4u * 91u && (a & 3) == 0) {
        const uint32_t level = (a - NAMES) / 4u;
        name_of = level == 13 ? 8u : level == 8 ? 0x1Fu : 0x10u;
        return &name_of;
    }
    for (unsigned i = 0; n == 2 && i < sizeof starts / sizeof starts[0]; i++)
        for (unsigned k = 0; k < 24; k++) {
            if (a == starts[i].at + k * 0x2Cu) return (void *)&starts[i].levels[k];
            if (starts[i].levels[k] == 0x5B) break;
        }
    if (n == 4 && a >= TILE_OPEN && a < TILE_OPEN + sizeof tile_open && (a & 3) == 0)
        return &tile_open[(a - TILE_OPEN) / 4];
    return NULL;
}
int sa2_peek(uint32_t a, uint32_t n, void *o) {
    void *w = at(a, n);
    if (!w) return 1;
    memcpy(o, w, n);
    return SA2_OK;
}
int sa2_poke(uint32_t a, uint32_t n, const void *in) {
    void *w = at(a, n);
    if (!w || (a >= LEVELS && a < LEVELS + sizeof levels) || (a >= CW_CHARS && a < CW_CHARS + 24) ||
        n == 2 || a == 0x0174AFD1u || (a >= NAMES && a < NAMES + 4u * 91u))
        return 1;
    memcpy(w, in, n);
    return SA2_OK;
}
uint32_t sa2_rt_get_gpr(const void *state, int idx) {
    (void)state;
    return idx == SA2_ECX ? ecx : idx == SA2_EAX ? eax : 0;
}
void sa2_rt_set_gpr(void *state, int idx, uint32_t v) { (void)state; (void)idx; (void)v; }

void *__wrap_sub_676640(void *state, uint32_t pc, void *memory);
void *__wrap_sub_678250(void *state, uint32_t pc, void *memory);
void *__wrap_sub_672760(void *state, uint32_t pc, void *memory);
void *__wrap_sub_676b70(void *state, uint32_t pc, void *memory);
void *__wrap_sub_678460(void *state, uint32_t pc, void *memory);
static int draw;

void *__real_sub_676640(void *state, uint32_t pc, void *memory) {
    (void)state; (void)pc;
    const int32_t level = levels[tile][0];
    out[0] = level == 90 ? 2 : level == 70 ? 1 : 0;
    if (level == 90) out[2] = 0x01DEF824u;
    else if (level != 70) out[1] = 0x01DEC638u + (uint32_t)level * 0xC4u;
    return memory;
}
static uint32_t type_of(void *state, void *memory) {
    out[0] = out[1] = out[2] = 0;
    ecx = OUT;
    __wrap_sub_676640(state, 0x00676640u, memory);
    return out[0];
}
void *__real_sub_676b70(void *state, uint32_t pc, void *memory) {
    (void)pc;
    const uint32_t type = type_of(state, memory);
    count = type == 2 ? 6 : 5;     /* the drawer's count, 0x00676CF2 and 0x00676E41 */
    if (!draw) return memory;
    printf("type %u", type);
    if (type == 2) {
        printf(" icons=");
        for (int i = 0; i < 6; i++) printf("%d", open[cw_chars[i]] != 0);
        printf(" title=%d box=%d", sprites[0x28][0], sprites[0x58][0]);
    }
    printf("\n");
    return memory;
}
void *__real_sub_678460(void *state, uint32_t pc, void *memory) {
    (void)pc;
    const uint32_t type = type_of(state, memory);
    eax = type == 2 ? (open[cw_chars[row]] ? 3 : 0) : 1;
    return memory;
}
void *__real_sub_678250(void *state, uint32_t pc, void *memory) {
    (void)pc;
    const uint32_t type = type_of(state, memory);
    eax = 0;
    if (type == 2) { if (open[cw_chars[row]]) { character = (uint32_t)cw_chars[row]; eax = 3; } }
    else { character = (uint32_t)levels[tile][1]; eax = 1; }
    return memory;
}
void *__real_sub_672760(void *state, uint32_t pc, void *memory) {
    (void)pc;
    const uint8_t m = (uint8_t)menu;
    if (sub_menu == 2) {
        if (m & 0x02) sub_menu = 4;
        else if (m & 0x0C) {
            if (levels[tile][0] == 31) sub_menu = 5;
            else { sub_menu = 3; row = 0; }
        }
    } else if (sub_menu == 3) {
        if (m & 0x02) sub_menu = 2;
        else if (m & 0x40) { if (row != 0) { row--; printf("sound 8000\n"); } }
        else if (m & 0x80) { if (row < count - 1) { row++; printf("sound 8000\n"); } }
        else if (m & 0x0C) {
            __wrap_sub_678460(state, 0x00678460u, memory);
            if (eax) sub_menu = 5; else printf("sound 800A\n");
        }
    } else if (sub_menu == 5) {
        __wrap_sub_678250(state, 0x00678250u, memory);
        sub_menu = 9;                  /* the game mode takes over */
    }
    return memory;
}

int main(int argc, char **argv) {
    int token;
    for (int t = 0; t < 33; t++) tile_open[t] = 1;
    for (int i = 0; i < 0x80; i++) sprites[i][0] = (int16_t)(1000 + i);
    for (int i = 1; i < argc; i++) {
        char buf[256];
        draw = 0;
        snprintf(buf, sizeof buf, "%s", argv[i]);
        pad[4] = 0; menu = 0;
        for (char *kv = strtok(buf, ","); kv; kv = strtok(NULL, ",")) {
            char *eq = strchr(kv, '=');
            const long v = eq ? strtol(eq + 1, NULL, 0) : 1;
            if (eq) *eq = 0;
            if (!strcmp(kv, "sub")) sub_menu = (uint32_t)v;
            else if (!strcmp(kv, "tile")) tile = (int8_t)v;
            else if (!strcmp(kv, "row")) row = (int8_t)v;
            else if (!strcmp(kv, "press")) pad[4] = (uint32_t)v;
            else if (!strcmp(kv, "menu")) menu = (uint32_t)v;
            else if (!strcmp(kv, "lock")) {
                for (int t = 0; t < 33; t++) if (levels[t][1] == v) tile_open[t] = 0;
            }
            else if (!strcmp(kv, "draw")) draw = 1;
            else if (strcmp(kv, "-")) return 2;
        }
        if (__wrap_sub_672760(&token, 0x00672760u, &token) != &token) return 3;
        if (draw || sub_menu == 3) __wrap_sub_676b70(&token, 0x00676B70u, &token);
        for (int i = 0; i < 0x80; i++) if (sprites[i][0] != 1000 + i) return 5;
        printf("sub=%u row=%d menu=0x%x char=%u save=", sub_menu, row, menu, character);
        for (int c = 0; c < 8; c++) printf("%d", open[c]);
        printf("\n");
    }
    return 0;
}
"""


@pytest.fixture(scope="module")
def exe(tmp_path_factory) -> Path:
    if not ZIG.is_file():
        pytest.fail(f"the pinned zig is not present: {ZIG}")
    d = tmp_path_factory.mktemp("character_select")
    (d / "driver.c").write_text(DRIVER)
    out = d / "cs"
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(RUNTIME / "tweaks" / "character-select.c"), "-o", str(out)],
                          capture_output=True, text=True, env=zig_env(d))
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, *frames: str, **env: str) -> tuple[list[str], list[str]]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS}
    p = subprocess.run([str(exe), *frames], capture_output=True, text=True, timeout=30,
                       env=dict(clean, **env))
    assert p.returncode == 0, p.stderr
    return p.stdout.splitlines(), p.stderr.splitlines()


def row(sub, r=0, char=99, menu=0) -> str:
    """A frame's last line; save= is the Chao World bytes, Sonic's only, as the frame left them."""
    return f"sub={sub} row={r} menu={menu:#x} char={char} save=10000000"


# Y on Security Hall (Rouge's, row 5), left three times to Knuckles (row 2), A, then A on mission 1
# and the fade.
KNUCKLES = [f"sub={MAP},tile={SECURITY_HALL},row=0,press={Y},draw", f"menu={LEFT}", f"menu={LEFT}",
            f"menu={LEFT}", f"menu={A}", "draw", f"menu={A}", "-"]


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}])
def test_off_y_does_nothing_and_the_stage_keeps_its_character(exe, env):
    out, err = run(exe, *KNUCKLES, **env)
    assert out == ["type 0", row(MAP), row(MAP, menu=LEFT), row(MAP, menu=LEFT), row(MAP, menu=LEFT),
                   row(ITEMS, menu=A), "type 0", row(ITEMS), row(START, menu=A), row(9, char=5)]
    assert err == []


def test_on_y_opens_the_characters_a_picks_and_the_stage_starts_as_knuckles(exe):
    out, err = run(exe, *KNUCKLES, **ON)
    assert out == [
        "type 2 icons=001001 title=1031 box=0", row(ITEMS, 5),        # Y: the characters, at Rouge (row 5)
        "sound 8000", row(ITEMS, 4, menu=LEFT),      # left: Eggman's walker
        "sound 8000", row(ITEMS, 3, menu=LEFT),      # left: Shadow
        "sound 8000", row(ITEMS, 2, menu=LEFT),      # left: Knuckles
        "sound 8000", row(ITEMS, 0, menu=A),         # A: picked, back to the missions at mission 1
        "type 0", row(ITEMS, 0),
        row(START, 0, menu=A),                       # A on mission 1: the fade
        row(9, 0, char=4),                           # the start: Knuckles
    ]
    assert err == [REPORT, SAID + "level 8 starts as Knuckles (4) in place of Rouge (5)"]


def test_the_picker_opens_at_the_tiles_own_character(exe):
    """Radical Highway (tile 19) is Shadow's: row 3 of 0, 6, 4, 1, 7, 5."""
    out, _ = run(exe, f"sub={MAP},tile={RADICAL_HIGHWAY},row=0,press={Y},draw", **ON)
    assert out == ["type 2 icons=100100 title=1016 box=0", row(ITEMS, 3)]


def test_without_y_a_stage_select_start_is_the_lifted_one(exe):
    frames = [f"sub={MAP},tile={CITY_ESCAPE},row=0,menu={A},draw", f"menu={RIGHT}", f"menu={A}", "-"]
    on, err = run(exe, *frames, **ON)
    off, _ = run(exe, *frames)
    assert on == off == ["type 0", row(ITEMS, menu=A), "sound 8000", row(ITEMS, 1, menu=RIGHT),
                         row(START, 1, menu=A), row(9, 1, char=0)]
    assert err == [REPORT]


def test_b_backs_out_of_the_picker_and_drops_the_pick(exe):
    """B from the characters goes to the map; a pick made and then left by B does not reach a later
    start of the same tile."""
    out, _ = run(exe, f"sub={MAP},tile={CITY_ESCAPE},row=0,press={Y}", f"menu={B}", "draw", **ON)
    assert out == [row(ITEMS), row(MAP, menu=B), "type 0", row(MAP)]
    out, err = run(exe, *KNUCKLES[:5], f"menu={B}", "-", f"menu={A}", f"menu={A}", "-", **ON)
    assert out[-3:] == [row(ITEMS, menu=A), row(START, menu=A), row(9, char=5)]
    assert err == [REPORT]


def test_y_in_the_sub_menu_switches_between_missions_and_characters(exe):
    frames = [f"sub={MAP},tile={CITY_ESCAPE},row=0,menu={A}", f"press={Y},draw", f"press={Y},draw"]
    out, _ = run(exe, *frames, **ON)
    assert out == [row(ITEMS, menu=A), "sound 8000", "type 2 icons=100100 title=1008 box=0", row(ITEMS, 0), "sound 8000",
                   "type 0", row(ITEMS, 0)]


def test_a_character_with_no_open_stage_is_greyed_and_refused_by_the_body(exe):
    """Knuckles' tiles all locked: on Security Hall his icon (row 2) is greyed and A on it plays the
    refusal; the save's Chao World bytes are Sonic's alone before and after every call."""
    out, err = run(exe, f"sub={MAP},tile={SECURITY_HALL},row=0,press={Y},lock=4", f"menu={LEFT}",
                   f"menu={LEFT}", f"menu={LEFT}", f"menu={A},draw", **ON)
    assert out[-3:] == ["sound 800A", "type 2 icons=000001 title=1031 box=0", row(ITEMS, 2, menu=A)]
    assert err == [REPORT]


def test_a_character_the_stage_has_no_start_for_is_greyed_and_refused(exe):
    """City Escape is in Sonic's and Shadow's start lists only, so Knuckles (row 2) is greyed and A
    on him plays the refusal; the stage then starts as Sonic."""
    out, err = run(exe, f"sub={MAP},tile={CITY_ESCAPE},row=0,press={Y}", f"menu={RIGHT}",
                   f"menu={RIGHT}", f"menu={A},draw", f"press={Y}", f"menu={A}", f"menu={A}", "-", **ON)
    assert out[5:8] == ["sound 800A", "type 2 icons=100100 title=1008 box=0", row(ITEMS, 2, menu=A)]
    assert out[-1] == row(9, char=0)
    assert err == [REPORT]


def test_a_walker_pick_starts_as_the_walker(exe):
    """Eggman (row 4 of the icons) plays Tails' Eternal Engine as his walker, 7."""
    out, err = run(exe, f"sub={MAP},tile={ETERNAL_ENGINE},row=0,press={Y},draw", f"menu={RIGHT}",
                   f"menu={RIGHT}", f"menu={RIGHT}", f"menu={A}", f"menu={A}", "-", **ON)
    assert out[0] == "type 2 icons=010010 title=1016 box=0"
    assert out[-1] == row(9, char=7)
    assert err == [REPORT, SAID + "level 24 starts as Eggman's walker (7) in place of Tails' walker (6)"]


@pytest.mark.parametrize("tile", [GREEN_HILL, KART, CHAO_WORLD, CANNONS_CORE])
def test_y_is_ignored_on_tiles_that_are_not_a_stage(exe, tile):
    out, _ = run(exe, f"sub={MAP},tile={tile},row=0,press={Y},draw", **ON)
    off, _ = run(exe, f"sub={MAP},tile={tile},row=0,press={Y},draw")
    assert out == off
    assert out[-1] == row(MAP)


def test_y_with_another_menu_button_is_left_to_the_body(exe):
    out, _ = run(exe, f"sub={MAP},tile={CITY_ESCAPE},row=0,press={Y | B},menu={B}", **ON)
    assert out == [row(LEAVE, menu=B)]


def test_chao_world_is_unchanged(exe):
    """Its own picker greys by the save's bytes: Tails (row 1) refused, Sonic taken."""
    frames = [f"sub={MAP},tile={CHAO_WORLD},row=0,menu={A},draw", f"menu={RIGHT}", f"menu={A}",
              f"menu={LEFT}", f"menu={A}", "-"]
    on, _ = run(exe, *frames, **ON)
    off, _ = run(exe, *frames)
    assert on == off
    assert on[0] == "type 2 icons=100000 title=1040 box=1088"
    assert on[4] == "sound 800A"
    assert on[-1] == row(9, 0, char=0)


@pytest.mark.parametrize("replay", ["SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"])
def test_ignored_in_a_replay(exe, replay):
    out, err = run(exe, *KNUCKLES, **ON, **{replay: "/dev/null"})
    assert out[-1] == row(9, char=5)
    assert err == ["sa2 tweaks: SA2_TWEAKS=character-select ignored in a replay (SA2_REPLAY_TICKS or "
                   "SA2_REPLAY_WORDS is set)"]
