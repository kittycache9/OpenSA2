/* start-in-chao-world: the first time the main menu comes up in a run, after the game's own File
 * Select has loaded the save, the game goes straight to the Chao World lobby instead of showing
 * the menu. The title and File Select play as usual; the main menu, Stage Select and its
 * character choice are skipped. It happens once a run: leaving Chao World returns to the game's
 * menus (runtime/sa2_tweaks.h: the tweaks and their rules).
 *
 * TWEAK_WRAPS: sub_664ec0
 *
 * Where it acts, read from the unpacked image. RunMenus (0x00666270) calls the menu that
 * CurrentMenu (int32 at 0x01D7BB10) names once a frame; menu 1 is the main menu, 0x00664EC0, which
 * takes no arguments and returns 0 to stay or 1 to leave the menus (0x00666299). On a 1, RunMenus
 * returns 1 (0x006663CC-0x00666405, raising the lives to 4 as for any start) and GameModeHandler's
 * case 14 copies NextGameMode (int32 at 0x0174B044) into GameMode (0x0043479A). The title opens
 * File Select while the save index ChaoSaveIndexThing (int32 at 0x0173D06C, -1 at boot) is -1
 * (0x00671C96) and the main menu once File Select has set it and loaded the save (0x006895A2,
 * then 0x00445100), so the main menu with that index not -1 comes after the save is loaded.
 *
 * Stage Select's Chao World tile (row 22 of 0x00C75078, level 90) starts the level in the type 2
 * arm of 0x00678250 (0x0067829C-0x00678407), then Menu_StageSelect (0x00672AA3) and returns 1.
 * The wrap makes the same stores, in CW_STORES and around it: the previous level (int16 at
 * 0x01934B84) = CurrentLevel; 0x01748B94 = 0 (the arm clears it when it is 1, its value in the
 * image; only Chao World's code sets it 1 again); CurrentLevel (int16 at 0x01934B70) = 90;
 * CurrentCharacter (int32 at 0x01934B80) = the character; 0x0174AFE0, TwoPlayerMode (0x0174AFDE)
 * and 0x0174AFC0 = 0; NextGameMode = 4 (StartLevel); the story type (0x01DEB320) and its two
 * indexes (0x01DEB31E, 0x01DEB31F) = 0 and its flag (0x01DEB321) = 1. The lobby is the first area
 * because the menu module's load zeroes 0x0174B017 (0x006707A3), which ChaoMain_Constructor reads
 * (0x0052ACF2). The tile opens only for a character whose byte in 0x01DEF829..0x01DEF830 (one per
 * character id, in the save block at 0x01DEC600, written by the save routine at 0x00444014 when a
 * Chao Key is held) is set (0x006782AA). The wrap takes the first character of ChaoWorldCharacters
 * (0x00C75368: Sonic 0, Mech Tails 6, Knuckles 4, Shadow 1, Mech Eggman 7, Rouge 5) whose byte is
 * set, and as the arm does makes 6 Tails (2) and 7 Eggman (3), who walk in Chao World. The arm's
 * pending-character branch (0x00678307 and on, when 0x01748B94 was 1) cannot run: the arm has
 * cleared EAX by then.
 *
 * The wrap, on the first main-menu frame with the save index not -1: when no character's byte is
 * set, it says so and the menu runs as usual; otherwise it calls __real_sub_664ec0, makes the
 * stores and returns EAX = 1. Either way the run is then DONE, and every later main-menu frame is
 * the lifted code alone. Chao World's exit (0x004343BE) then goes to the menus (GameMode 2), with
 * no Chao Key, no two-player game and no story set; 0x01A559E8 is left as the title left it (0), so
 * the menus open at the title, and the title, the save index being set, at the main menu.
 *
 * Off is identical: the wrap tests its bit first and otherwise only calls __real_sub_664ec0.
 *
 * Risks. The wrap loads, writes and skips no save: File Select loaded it, and the game's own save
 * routines run as usual, so what is played in Chao World is saved as on a visit from Stage Select
 * (the Chao save is loaded on the first visit of a run, as always, and written on the way out).
 * Stage Select's fade and its menu-work save (0x00673AE0) are skipped: the main menu runs one
 * frame of its own start and the game cuts to the load. A store that fails leaves the stores before
 * it made, says so and returns the menu's own answer.
 *
 * The lines it prints, once a run, each prefixed "sa2 tweaks: start in Chao World: ": "the save is
 * loaded: going to Chao World as character 0 instead of the main menu", "the save has not opened
 * Chao World (no byte of 0x01DEF829..0x01DEF830 set for a Chao World character), so the main menu
 * is shown", "CurrentMenu, the save index or the Chao World bytes do not read, so the main menu is
 * shown", or "0x0174B044 does not write, so the main menu answers for itself" (the address that
 * failed).
 */
#include <stdint.h>
#include <stdio.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_tweaks.h"

#define CW_CURRENT_MENU    0x01D7BB10u  /* int32 */
#define CW_SAVE_INDEX      0x0173D06Cu  /* int32, -1 until File Select loads a save */
#define CW_UNLOCK          0x01DEF829u  /* 8 bytes, one per character id */
#define CW_CURRENT_LEVEL   0x01934B70u  /* int16 */
#define CW_PREVIOUS_LEVEL  0x01934B84u  /* int16 */
#define CW_CHARACTER       0x01934B80u  /* int32 */
#define CW_MAIN_MENU       1

typedef enum { CW_WAITING, CW_DONE } cw_phase;

typedef struct {
    uint32_t addr, width, value;
} cw_store;

static const cw_store CW_STORES[] = {
    {0x01748B94u, 4, 0},
    {CW_CURRENT_LEVEL, 2, 90},
    {0x0174AFE0u, 1, 0},
    {0x0174AFDEu, 1, 0},
    {0x0174AFC0u, 1, 0},
    {0x0174B044u, 4, 4},
    {0x01DEB320u, 1, 0},
    {0x01DEB31Eu, 1, 0},
    {0x01DEB31Fu, 1, 0},
    {0x01DEB321u, 1, 1},
};

static const uint8_t CW_CHARACTERS[] = {0, 6, 4, 1, 7, 5};

static cw_phase g_cw_phase = CW_WAITING;

void *__real_sub_664ec0(void *state, uint32_t pc, void *memory);

#define CW_SAY(...) fprintf(stderr, "sa2 tweaks: start in Chao World: " __VA_ARGS__)

static int cw_poke(uint32_t addr, uint32_t width, uint32_t value) {
    if (sa2_poke(addr, width, &value) == SA2_OK) return 1;
    CW_SAY("0x%08X does not write, so the main menu answers for itself\n", addr);
    return 0;
}

__attribute__((used, retain))
void *__wrap_sub_664ec0(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_START_IN_CHAO_WORLD) || g_cw_phase == CW_DONE)
        return __real_sub_664ec0(state, pc, memory);
    int32_t menu = 0, index = -1;
    uint16_t level = 0;
    uint8_t unlock[8] = {0};
    if (sa2_peek(CW_CURRENT_MENU, 4, &menu) != SA2_OK || sa2_peek(CW_SAVE_INDEX, 4, &index) != SA2_OK ||
        sa2_peek(CW_UNLOCK, sizeof unlock, unlock) != SA2_OK ||
        sa2_peek(CW_CURRENT_LEVEL, 2, &level) != SA2_OK) {
        g_cw_phase = CW_DONE;
        CW_SAY("CurrentMenu, the save index or the Chao World bytes do not read, so the main menu "
               "is shown\n");
        return __real_sub_664ec0(state, pc, memory);
    }
    if (menu != CW_MAIN_MENU || index == -1) return __real_sub_664ec0(state, pc, memory);
    g_cw_phase = CW_DONE;
    int chosen = -1;
    for (unsigned i = 0; i < sizeof CW_CHARACTERS && chosen < 0; i++)
        if (unlock[CW_CHARACTERS[i]]) chosen = CW_CHARACTERS[i];
    if (chosen < 0) {
        CW_SAY("the save has not opened Chao World (no byte of 0x01DEF829..0x01DEF830 set for a "
               "Chao World character), so the main menu is shown\n");
        return __real_sub_664ec0(state, pc, memory);
    }
    void *ret = __real_sub_664ec0(state, pc, memory);
    const uint32_t character = chosen == 6 ? 2 : chosen == 7 ? 3 : (uint32_t)chosen;
    if (!cw_poke(CW_PREVIOUS_LEVEL, 2, level) || !cw_poke(CW_CHARACTER, 4, character)) return ret;
    for (unsigned i = 0; i < sizeof CW_STORES / sizeof CW_STORES[0]; i++)
        if (!cw_poke(CW_STORES[i].addr, CW_STORES[i].width, CW_STORES[i].value)) return ret;
    sa2_rt_set_gpr(state, SA2_EAX, 1);
    CW_SAY("the save is loaded: going to Chao World as character %u instead of the main menu\n",
           character);
    return ret;
}
