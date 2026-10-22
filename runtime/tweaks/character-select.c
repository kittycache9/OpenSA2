/* character-select: in Stage Select, Y on a stage opens the character picker that Chao World's tile
 * shows (the stage's sub-menu with the six character icons), and the character picked there plays
 * the stage in place of its own; the mission picker then comes back as usual (runtime/sa2_tweaks.h:
 * the tweaks and their rules). Ranks, emblems and scores save under the stage, as they always do.
 *
 * TWEAK_WRAPS: sub_672760, sub_676640, sub_676b70, sub_678460, sub_678250
 *
 * Where, read from the unpacked image. Menu_StageSelect (0x00672760) runs once a frame and switches
 * on CurrentSubMenu (0x01D7BB14): 2 the map, where MenuButtons_Pressed (the byte at 0x01DEFAB0: B
 * 0x02, A or Start 0x0C, the directions 0x10 to 0x80) moves over the tiles and A opens the
 * highlighted tile's sub-menu (0x00672888: CurrentSubMenu 3, row 0); 3 the sub-menu, a row of
 * icons, where B goes back to 2 (0x00672964), left (0x40) and right (0x80) move the row (the byte
 * at 0x01D1BF05, below the count at 0x01D1BF04, each with the cursor sound 0x8000 at 0x00672870),
 * and A asks 0x00678460 whether the row can be played and on a yes fades out (CurrentSubMenu 5);
 * 5, the fade done, calls 0x00678250, which starts the tile. No other function in the menus reads
 * MenuButtons_Pressed during Stage Select. The highlighted tile is the byte at 0x01D1BF08, an index
 * into StageSelectLevels (0x00C75078, 33 entries of {Level, Character, Column, Row}); Character is
 * 0 Sonic, 1 Shadow, 4 Knuckles, 5 Rouge, 6 Tails' walker and 7 Eggman's walker, the values
 * LoadCharacters (0x0043D630) dispatches on.
 *
 * 0x00676640 says what a tile's sub-menu holds, into a struct at ECX: type 2 for Chao World (level
 * 0x5A), with +8 = 0x01DEF824; type 1 for the kart race (0x46); type 0 for the rest, with +4 the
 * stage's mission data. Its four callers are the sub-menu's drawers (0x00676A60, 0x00676B70), the
 * A check 0x00678460 and the start 0x00678250, each for the highlighted tile. For type 2 the drawer
 * sets the count to 6, draws Chao World's panel (0x006768A0: the name CHAO WORLD and its nine
 * boxes) and ChaoWorldCharacters (0x00C75368: 0, 6, 4, 1, 7, 5) as icons, a character greyed when
 * its byte at 0x01DEF829 + character is 0; 0x00678460 refuses such a row (and the menu plays the
 * refusal sound 0x800A). Those bytes are save data for Chao World (only Sonic's is set in a save
 * with both stories cleared), so that picker would grey nearly everyone. The start, for type 2,
 * would load Chao World; for type 0 it calls SetCurrentLevel and stores CurrentCharacter
 * (0x01934B80) from the tile's Character (0x006783A4) and MissionNum from the row, sets the next
 * game mode to 4 and returns 1.
 *
 * What the tweak does, each part behind its bit:
 *
 *   sub_672760, before the body, on a frame where player 1's press edges (ControllerPointers[0],
 *   0x01DEFB60, +0x10) hold Y (0x200) and MenuButtons_Pressed holds nothing: on the map, at a stage
 *   tile, it hands the body an A for that call, so the body opens the sub-menu as A does (its sound,
 *   its lock check), then turns the picker on and puts the row on the tile's own character. In the
 *   sub-menu it turns the picker on (at the character already picked, or the tile's own) or off
 *   (back at mission 1). A on a character that can be picked records it as the pick and turns the
 *   picker off. A character can be picked when the stage is in its start position list and one of
 *   its own stage tiles is open (the dword per tile at 0x01A4A974, which StageMapExec_Main,
 *   0x00677350, sets each frame). Each of those row
 *   changes is made as row + 1 with a left handed to the body for that call, so the body moves the
 *   row and plays the cursor sound. MenuButtons_Pressed is put back after the call. A character
 *   that cannot be picked is left to the body, which refuses it. The picker is turned off whenever
 *   CurrentSubMenu is not 3, and the pick is dropped whenever it is neither 3 nor 5: B back to the
 *   map drops both.
 *
 *   sub_676640, after the body, while the picker is on: type 2 and +8 = 0x01DEF824, Chao World's
 *   answer, so the drawers show the character icons and 0x00678460 checks the character. The start
 *   never sees it: the picker is off in CurrentSubMenu 5.
 *
 *   sub_676b70 and sub_678460, while the picker is on: the six characters' bytes at 0x01DEF829 +
 *   character hold whether each can be picked for the call, and are put back after it, so the
 *   icons are greyed and the A check refuses by that rule. Nothing saves during either call.
 *   sub_676b70 also shows the stage's own name over the panel and leaves out Chao World's nine
 *   boxes, for its call only. Chao World's panel (0x006768A0) draws its title as sprite 0x28 (0x49
 *   in Japanese, 0x21 on) of the panels' NJS_TEXANIMs at 0x00C68C50, and each box as sprite 0x58;
 *   the missions panel draws a stage's name as sprite [0x008A0560 + 4 * Level] (+ 0x21). For the
 *   call, sprite 0x28 (+ 0x21) holds the stage name's 20 bytes and sprite 0x58 a size of 0 by 0.
 *   The sprite drawer 0x0066EF40 queues a copy of the sprite it is handed (0x0066EF71), so the
 *   bytes put back after the call change nothing already queued.
 *
 *   sub_678250, after the body, when it returned 1 for the tile the pick was made on:
 *   CurrentCharacter = the pick. Nothing between here and the character load sets it again for a
 *   stage tile: the next game mode, 4, runs StartLevel's setup 0x00439610, whose only read of
 *   CurrentCharacter (0x00439705) is two-player, and the load 0x0043C970 changes it only for Chao
 *   World and for the on-foot Tails and Eggman (2 and 3, made walkers in a stage).
 *
 * The tiles that take Y are the stages: not Green Hill (level 31, which starts without a sub-menu),
 * the kart races (70), Chao World (90), or Cannon's Core (34), which hands the next section to the
 * next character itself (GameModeHandler 0x004342FA).
 *
 * Why the start list. LoadStartPosition (0x0043D8E0) zeroes the player's position, then looks the
 * level up in the list its CharID picks (the switch at 0x0043D931; entries of 0x2C bytes, Level
 * first, ended by Level 0x5B). A level missing from the list leaves the player at (0, 0, 0). Each
 * character's list holds the stages of its own kind and nothing else: Sonic's and Shadow's the
 * speed stages, Knuckles' and Rouge's the treasure hunts, the walkers' the walker stages, and a few
 * of those not all (Shadow's has no Pyramid Cave, Knuckles' no Dry Lagoon, Rouge's no Aquatic
 * Mine). Started at the origin, Knuckles in City Escape drew half the screen from another place and
 * Rouge in Iron Gate drew nothing but the HUD; handed Sonic's start (a probe, not kept), Knuckles
 * flew off past 5,000,000 units and his position went NaN within a second, so the stage is not
 * his to play, and the picker refuses him there. Within the rule a pairing may still not be
 * finishable (a walker where the stage needs something the other walker lacks). The character's
 * upgrades are the ones the save holds for that character.
 *
 * Each start with a pick is said, the first 16: "sa2 tweaks: character select: level 13 starts as
 * Knuckles (4) in place of Sonic (0)".
 */
#include <stdint.h>
#include <stdio.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_tweaks.h"

#define CS_SUB_MENU     0x01D7BB14u  /* CurrentSubMenu, dword */
#define CS_MAP          2u
#define CS_ITEMS        3u
#define CS_START        5u
#define CS_MENU_PRESS   0x01DEFAB0u  /* MenuButtons_Pressed[0], its low byte */
#define CS_MENU_B       0x02u
#define CS_MENU_A       0x04u
#define CS_MENU_LEFT    0x40u
#define CS_ROW          0x01D1BF05u  /* the sub-menu's row, int8 */
#define CS_TILE         0x01D1BF08u  /* StageSelectLevelSelection, int8 */
#define CS_LEVELS       0x00C75078u  /* StageSelectLevels: {Level, Character, Column, Row} */
#define CS_NLEVELS      33
#define CS_CW_CHARS     0x00C75368u  /* ChaoWorldCharacters, 6 dwords */
#define CS_CW_DATA      0x01DEF824u  /* type 2's +8 */
#define CS_CW_OPEN      0x01DEF829u  /* + character: Chao World's save byte, nonzero: can be picked */
#define CS_TILE_OPEN    0x01A4A974u  /* a dword per tile, nonzero when the tile is open */
#define CS_CHARACTER    0x01934B80u  /* CurrentCharacter */
#define CS_START_STRIDE 0x2Cu        /* a start list entry: int16 Level first */
#define CS_START_END    0x5B         /* the Level that ends a start list */
#define CS_CONTROLLER0  0x01DEFB60u  /* ControllerPointers[0] */
#define CS_SPRITES      0x00C68C50u  /* the Stage Select panels' sprites, 20-byte NJS_TEXANIMs */
#define CS_SPRITE_SIZE  20u
#define CS_JAPANESE     0x0174AFD1u  /* nonzero: the panels use the sprites 0x21 further on */
#define CS_LANG_STEP    0x21u
#define CS_NAME_SPRITES 0x008A0560u  /* a dword per level: its name's sprite */
#define CS_CW_TITLE     0x28u        /* CHAO WORLD, 0x006769F1 */
#define CS_CW_BOX       0x58u        /* Chao World's nine boxes, 0x00676994 */
#define CS_PRESS        0x10u
#define CS_BUTTON_Y     0x200u
#define CS_SAID         16
#define CS_NONE         (-1)

static int g_cs_picker;              /* the sub-menu shows the characters */
static int g_cs_pick = CS_NONE, g_cs_pick_tile = CS_NONE;
static unsigned g_cs_starts;
static int g_cs_read_said, g_cs_write_said, g_cs_lost_said;

void *__real_sub_672760(void *state, uint32_t pc, void *memory);
void *__real_sub_676640(void *state, uint32_t pc, void *memory);
void *__real_sub_676b70(void *state, uint32_t pc, void *memory);
void *__real_sub_678460(void *state, uint32_t pc, void *memory);
void *__real_sub_678250(void *state, uint32_t pc, void *memory);

static const char *cs_name(int c) {
    static const char *const names[8] = {"Sonic", "Shadow", "Tails", "Eggman", "Knuckles", "Rouge",
                                         "Tails' walker", "Eggman's walker"};
    return c >= 0 && c < 8 ? names[c] : "a character";
}

/* Each CharID's start position list, from LoadStartPosition's switch (0x0043D931). */
static const struct {
    int32_t character;
    uint32_t list;
} CS_STARTS[] = {
    {0, 0x01746F20u}, {1, 0x017472C0u}, {4, 0x017475D8u},
    {5, 0x017478F0u}, {6, 0x01748008u}, {7, 0x01747C40u},
};

/* Whether character `c`'s start list holds `level`. */
static int cs_starts_in(int32_t c, int16_t level) {
    for (unsigned i = 0; i < sizeof CS_STARTS / sizeof CS_STARTS[0]; i++) {
        if (CS_STARTS[i].character != c) continue;
        for (uint32_t k = 0; k < 64; k++) {
            int16_t at = CS_START_END;
            if (sa2_peek(CS_STARTS[i].list + k * CS_START_STRIDE, 2, &at) != SA2_OK ||
                at == CS_START_END)
                return 0;
            if (at == level) return 1;
        }
    }
    return 0;
}

/* The highlighted tile's index, level and character; 0 when they do not read. */
static int cs_tile(int *tile, int32_t *level, int32_t *character) {
    int8_t t = 0;
    if (sa2_peek(CS_TILE, 1, &t) != SA2_OK || t < 0 || t >= CS_NLEVELS ||
        sa2_peek(CS_LEVELS + 16u * (uint32_t)t, 4, level) != SA2_OK ||
        sa2_peek(CS_LEVELS + 16u * (uint32_t)t + 4u, 4, character) != SA2_OK)
        return 0;
    *tile = t;
    return 1;
}

static int cs_takes_y(int32_t level) {
    return level != 31 && level != 34 && level != 70 && level != 90;
}

/* The picker's row of character `c`, 0 when it is not there. */
static int cs_row_of(int32_t c) {
    for (int i = 0; i < 6; i++) {
        int32_t v = -1;
        if (sa2_peek(CS_CW_CHARS + 4u * (uint32_t)i, 4, &v) == SA2_OK && v == c) return i;
    }
    return 0;
}

/* Whether character `c` can be picked for `level`: the level is in its start list, and a stage
 * tile of its own is open. */
static int cs_open(int32_t c, int32_t level) {
    if (!cs_starts_in(c, (int16_t)level)) return 0;
    for (uint32_t t = 0; t < CS_NLEVELS; t++) {
        int32_t its = 0, character = -1;
        uint32_t open = 0;
        if (sa2_peek(CS_LEVELS + 16u * t, 4, &its) == SA2_OK &&
            sa2_peek(CS_LEVELS + 16u * t + 4u, 4, &character) == SA2_OK &&
            sa2_peek(CS_TILE_OPEN + 4u * t, 4, &open) == SA2_OK && character == c && open &&
            cs_takes_y(its))
            return 1;
    }
    return 0;
}

/* One call of `real` with the six characters' save bytes at 0x01DEF829 + character holding
 * cs_open's answers, put back after the call. */
static void *cs_with_open(void *state, uint32_t pc, void *memory,
                          void *(*real)(void *, uint32_t, void *)) {
    uint8_t saved[6], mine[6];
    uint32_t at[6];
    int n = 0, tile = 0;
    int32_t level = 0, own = 0;
    if (!cs_tile(&tile, &level, &own)) return real(state, pc, memory);
    for (; n < 6; n++) {
        int32_t c = -1;
        if (sa2_peek(CS_CW_CHARS + 4u * (uint32_t)n, 4, &c) != SA2_OK || c < 0 || c > 7) break;
        at[n] = CS_CW_OPEN + (uint32_t)c;
        mine[n] = (uint8_t)cs_open(c, level);
        if (sa2_peek(at[n], 1, &saved[n]) != SA2_OK || sa2_poke(at[n], 1, &mine[n]) != SA2_OK) break;
    }
    if (n < 6) {
        for (int i = 0; i < n; i++) (void)sa2_poke(at[i], 1, &saved[i]);
        if (!g_cs_write_said++)
            fprintf(stderr, "sa2 tweaks: character select: the characters' bytes at 0x%08X do not "
                    "read or write, so the picker greys the characters Chao World does\n",
                    CS_CW_OPEN);
        return real(state, pc, memory);
    }
    void *ret = real(state, pc, memory);
    for (int i = 0; i < 6; i++) (void)sa2_poke(at[i], 1, &saved[i]);
    return ret;
}

/* One call of the body with MenuButtons_Pressed's low byte `menu`, put back to `was` after. */
static void *cs_with_menu(void *state, uint32_t pc, void *memory, uint8_t menu, uint8_t was) {
    if (sa2_poke(CS_MENU_PRESS, 1, &menu) != SA2_OK) {
        if (!g_cs_write_said++)
            fprintf(stderr, "sa2 tweaks: character select: MenuButtons_Pressed at 0x%08X does not "
                    "write, so Y does nothing\n", CS_MENU_PRESS);
        g_cs_picker = 0;
        return __real_sub_672760(state, pc, memory);
    }
    void *ret = __real_sub_672760(state, pc, memory);
    (void)sa2_poke(CS_MENU_PRESS, 1, &was);
    return ret;
}

/* The row put on `row` by the body: row + 1 and a left, so the cursor sound plays. */
static void *cs_move_to(void *state, uint32_t pc, void *memory, int row, uint8_t was) {
    const int8_t above = (int8_t)(row + 1);
    if (sa2_poke(CS_ROW, 1, &above) != SA2_OK) return __real_sub_672760(state, pc, memory);
    return cs_with_menu(state, pc, memory, CS_MENU_LEFT, was);
}

__attribute__((used, retain))
void *__wrap_sub_672760(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_CHARACTER_SELECT)) return __real_sub_672760(state, pc, memory);
    uint32_t sub = 0, pad = 0, press = 0;
    uint8_t menu = 0;
    if (sa2_peek(CS_SUB_MENU, 4, &sub) != SA2_OK || sa2_peek(CS_MENU_PRESS, 1, &menu) != SA2_OK) {
        if (!g_cs_read_said++)
            fprintf(stderr, "sa2 tweaks: character select: CurrentSubMenu or MenuButtons_Pressed does "
                    "not read, so Y does nothing\n");
        g_cs_picker = 0;
        g_cs_pick = CS_NONE;
        return __real_sub_672760(state, pc, memory);
    }
    if (sub != CS_ITEMS) g_cs_picker = 0;
    if (sub != CS_ITEMS && sub != CS_START) g_cs_pick = g_cs_pick_tile = CS_NONE;
    if (sub != CS_MAP && sub != CS_ITEMS) return __real_sub_672760(state, pc, memory);

    int tile = 0;
    int32_t level = 0, own = 0;
    if (!cs_tile(&tile, &level, &own) || !cs_takes_y(level))
        return __real_sub_672760(state, pc, memory);

    if (sub == CS_ITEMS && g_cs_picker && (menu & 0x0C) && !(menu & CS_MENU_B)) {
        int8_t row = -1;
        int32_t c = -1;
        if (sa2_peek(CS_ROW, 1, &row) != SA2_OK || row < 0 || row >= 6 ||
            sa2_peek(CS_CW_CHARS + 4u * (uint32_t)row, 4, &c) != SA2_OK || !cs_open(c, level))
            return __real_sub_672760(state, pc, memory);   /* the body refuses it */
        g_cs_pick = c;
        g_cs_pick_tile = tile;
        g_cs_picker = 0;
        return cs_move_to(state, pc, memory, 0, menu);
    }

    if (sa2_peek(CS_CONTROLLER0, 4, &pad) != SA2_OK || pad == 0 ||
        sa2_peek(pad + CS_PRESS, 4, &press) != SA2_OK || !(press & CS_BUTTON_Y) || menu != 0)
        return __real_sub_672760(state, pc, memory);

    const int32_t current = g_cs_pick != CS_NONE && g_cs_pick_tile == tile ? g_cs_pick : own;
    if (sub == CS_MAP) {
        void *ret = cs_with_menu(state, pc, memory, CS_MENU_A, menu);
        uint32_t after = 0;
        if (sa2_peek(CS_SUB_MENU, 4, &after) == SA2_OK && after == CS_ITEMS) {
            const int8_t row = (int8_t)cs_row_of(current);
            g_cs_picker = sa2_poke(CS_ROW, 1, &row) == SA2_OK;
        }
        return ret;
    }
    g_cs_picker = !g_cs_picker;
    return cs_move_to(state, pc, memory, g_cs_picker ? cs_row_of(current) : 0, menu);
}

__attribute__((used, retain))
void *__wrap_sub_676640(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_CHARACTER_SELECT)) return __real_sub_676640(state, pc, memory);
    if (!g_cs_picker) return __real_sub_676640(state, pc, memory);
    const uint32_t out = sa2_rt_get_gpr(state, SA2_ECX);
    void *ret = __real_sub_676640(state, pc, memory);
    const uint32_t type = 2, data = CS_CW_DATA;
    if (sa2_poke(out, 4, &type) != SA2_OK || sa2_poke(out + 8u, 4, &data) != SA2_OK) {
        if (!g_cs_write_said++)
            fprintf(stderr, "sa2 tweaks: character select: the sub-menu's struct at 0x%08X does not "
                    "write, so it shows the missions\n", out);
        (void)sa2_poke(out, 4, &(uint32_t){0});
        g_cs_picker = 0;
    }
    return ret;
}

/* One call of the sub-menu's drawer with Chao World's panel showing the stage's own name in place
 * of CHAO WORLD and no boxes (the box sprite drawn at no size), both put back after the call. The
 * drawer queues a copy of each sprite (0x0066EF71), so later draws see the sprites as they were. */
static void *cs_with_panel(void *state, uint32_t pc, void *memory) {
    int tile = 0;
    int32_t level = 0, own = 0;
    uint8_t japanese = 0;
    uint32_t name = 0;
    uint8_t title[CS_SPRITE_SIZE], box[CS_SPRITE_SIZE], stage[CS_SPRITE_SIZE];
    const uint8_t none[4] = {0, 0, 0, 0};    /* sx, sy */
    if (!cs_tile(&tile, &level, &own) || sa2_peek(CS_JAPANESE, 1, &japanese) != SA2_OK ||
        sa2_peek(CS_NAME_SPRITES + 4u * (uint32_t)level, 4, &name) != SA2_OK)
        return cs_with_open(state, pc, memory, __real_sub_676b70);
    const uint32_t lang = japanese ? CS_LANG_STEP : 0;
    const uint32_t at_title = CS_SPRITES + CS_SPRITE_SIZE * (CS_CW_TITLE + lang);
    const uint32_t at_box = CS_SPRITES + CS_SPRITE_SIZE * CS_CW_BOX;
    if (sa2_peek(at_title, CS_SPRITE_SIZE, title) != SA2_OK ||
        sa2_peek(at_box, CS_SPRITE_SIZE, box) != SA2_OK ||
        sa2_peek(CS_SPRITES + CS_SPRITE_SIZE * (name + lang), CS_SPRITE_SIZE, stage) != SA2_OK ||
        sa2_poke(at_title, CS_SPRITE_SIZE, stage) != SA2_OK)
        return cs_with_open(state, pc, memory, __real_sub_676b70);
    (void)sa2_poke(at_box, sizeof none, none);
    void *ret = cs_with_open(state, pc, memory, __real_sub_676b70);
    (void)sa2_poke(at_title, CS_SPRITE_SIZE, title);
    (void)sa2_poke(at_box, CS_SPRITE_SIZE, box);
    return ret;
}

__attribute__((used, retain))
void *__wrap_sub_676b70(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_CHARACTER_SELECT)) return __real_sub_676b70(state, pc, memory);
    if (!g_cs_picker) return __real_sub_676b70(state, pc, memory);
    return cs_with_panel(state, pc, memory);
}

__attribute__((used, retain))
void *__wrap_sub_678460(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_CHARACTER_SELECT)) return __real_sub_678460(state, pc, memory);
    if (!g_cs_picker) return __real_sub_678460(state, pc, memory);
    return cs_with_open(state, pc, memory, __real_sub_678460);
}

__attribute__((used, retain))
void *__wrap_sub_678250(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_CHARACTER_SELECT)) return __real_sub_678250(state, pc, memory);
    g_cs_picker = 0;
    const int pick = g_cs_pick, pick_tile = g_cs_pick_tile;
    g_cs_pick = g_cs_pick_tile = CS_NONE;
    void *ret = __real_sub_678250(state, pc, memory);
    if (pick == CS_NONE) return ret;
    int tile = 0;
    int32_t level = 0, own = 0, was = 0;
    if (sa2_rt_get_gpr(state, SA2_EAX) != 1 || !cs_tile(&tile, &level, &own) || tile != pick_tile ||
        sa2_peek(CS_CHARACTER, 4, &was) != SA2_OK || sa2_poke(CS_CHARACTER, 4, &pick) != SA2_OK) {
        if (!g_cs_lost_said++)
            fprintf(stderr, "sa2 tweaks: character select: the start did not take the pick (%s), so "
                    "the stage keeps its own character\n", cs_name(pick));
        return ret;
    }
    if (++g_cs_starts <= CS_SAID)
        fprintf(stderr, "sa2 tweaks: character select: level %d starts as %s (%d) in place of %s "
                "(%d)%s\n",
                (int)level, cs_name(pick), pick, cs_name(was), (int)was,
                g_cs_starts == CS_SAID ? "; later starts are not said" : "");
    return ret;
}
