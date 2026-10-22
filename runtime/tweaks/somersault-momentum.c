/* somersault-momentum: a somersault begun at speed keeps that speed, where the game brakes it to
 * 1.875 (runtime/sa2_tweaks.h: the tweaks and their rules). Sonic and Shadow, who share the code.
 *
 * TWEAK_WRAPS: sub_723560
 *
 * Where the brake comes from, read from the unpacked image. Sonic_Somersault (0x00723880, the
 * somersault and spin-dash input handler) starts a somersault in one of two places, and both write
 * the constant 1.875 (float at 0x00904EC0) over the character's forward speed, Speed.x, at
 * CharObj2Base+0x64:
 *
 *   0x00723ED2-0x00723F00  inline, taken when Speed.x is at most RunSpeed (CharObj2Base+0xE8): the
 *                          standing start, action 0x3D, animation 0x5B. The store raises a slow
 *                          character to 1.875, so it is left alone.
 *   0x00723F0B             `call 0x00723560`, taken above RunSpeed, with EAX = the EntityData1,
 *                          ECX = the SonicCharObj2 and EDI = the CharObj2Base (`mov ecx,ebx; mov
 *                          edi,ebp; mov eax,edx`). It is the function's only caller.
 *
 * 0x00723560 (86 bytes, unnamed, a plain ret) sets action 0x42 (0x00723566), animation 0x60
 * (0x0072356E), flag 0x400 (0x0072357A), word [ECX+0x366] = 0 and byte [ECX+0x35D] = 3, loads the
 * 1.875 first (0x00723560) and stores it to [EDI+0x64] at 0x00723591, then plays its sound through
 * 0x00437260 and 0x00438E70. A character who ran into the somersault at full speed leaves it at
 * 1.875: that is the brake.
 *
 * The wrap reads the CharObj2Base from EDI and its Speed.x before the lifted body runs, and after
 * it, when that speed was above 1.875, writes it back. A speed of 1.875 or less, and a speed that
 * does not read as a number above it, are left to the game. The somersault's own per-frame physics
 * (Sonic_Main, actions 61 to 68) still slows the character afterwards, so the kept speed wears off
 * at the game's own rate; the tweak changes where that decay starts.
 *
 * Off is identical: the wrap tests its bit first and otherwise only calls the lifted body. 0x00723880
 * is the code-patch tweak spin-dash-fast's function and stays unwrapped; its second lifted copy
 * calls sub_723560 as an external symbol, so this wrap acts under both.
 *
 * The first time a run keeps a speed, it says so: "sa2 tweaks: somersault momentum: speed 3.5
 * kept (the game's 1.875)"; later ones are not named.
 *
 * Risks. A gameplay change: a faster somersault goes through enemies and under gaps the game's
 * slow one would not, and can skip parts of a stage. Nothing extra is saved; times and ranks
 * earned with it are saved as usual.
 */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_tweaks.h"

#define SOMERSAULT_SPEED_X  0x64u   /* float at CharObj2Base+0x64: forward speed */
#define SOMERSAULT_BRAKE    1.875f  /* float at 0x00904EC0: what the game sets it to */

static int g_somersault_read_said, g_somersault_write_said, g_somersault_named;

void *__real_sub_723560(void *state, uint32_t pc, void *memory);

__attribute__((used, retain))
void *__wrap_sub_723560(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_SOMERSAULT_MOMENTUM)) return __real_sub_723560(state, pc, memory);
    const uint32_t co2 = sa2_rt_get_gpr(state, SA2_EDI);
    float before = 0.0f;
    const int read = sa2_peek(co2 + SOMERSAULT_SPEED_X, 4, &before) == SA2_OK;
    void *ret = __real_sub_723560(state, pc, memory);
    if (!read) {
        if (!g_somersault_read_said++)
            fprintf(stderr, "sa2 tweaks: somersault momentum: the speed at 0x%08X does not read, so "
                    "the somersault brakes as usual\n", co2 + SOMERSAULT_SPEED_X);
        return ret;
    }
    if (!(before > SOMERSAULT_BRAKE)) return ret;
    if (sa2_poke(co2 + SOMERSAULT_SPEED_X, 4, &before) != SA2_OK) {
        if (!g_somersault_write_said++)
            fprintf(stderr, "sa2 tweaks: somersault momentum: the speed at 0x%08X does not write, so "
                    "the somersault brakes as usual\n", co2 + SOMERSAULT_SPEED_X);
        return ret;
    }
    if (!g_somersault_named++)
        fprintf(stderr, "sa2 tweaks: somersault momentum: speed %g kept (the game's 1.875)\n",
                (double)before);
    return ret;
}
