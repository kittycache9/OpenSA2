/* hunter-flat-glide: the glide of Knuckles and Rouge holds its height instead of sinking
 * (runtime/sa2_tweaks.h: the tweaks and their rules). Tikal and Chaos, who share the code, glide
 * level too.
 *
 * TWEAK_WRAPS: sub_45df10
 *
 * Where the sink comes from, read from the unpacked image. Knuckles_Main (0x00728D70, shared by
 * LoadKnuckles, LoadRouge, LoadTikal and LoadChaos) sends action 59, the glide, to 0x00729186. That
 * arm tests the vertical speed (`fld 0; fcomp [edi+0x68]; ... jp 0x007291BC`) and, while Speed.y
 * (CharObj2Base+0x68) is at most 0, calls 0x0045DF10 at 0x007291BC (above 0 it calls 0x0045D770,
 * the rise, which is left alone), then 0x004613C0 at 0x007291C9.
 *
 * 0x0045DF10 (the glide's acceleration, unnamed; its only caller is 0x007291BC) enters with EAX =
 * the CharObj2Base, ECX = the EntityData1 and one stack argument, the EntityData2, which the
 * caller pops. It returns at once when word [EntityData1+4] & 0x4000 is set (0x0045DF24). Otherwise
 * it builds the acceleration from Gravity (0x01DE94A0, set at run time) times Weight
 * (CharObj2Base+0x138) times 0.05 (the double at 0x009050D8), plus drag terms from AirResist
 * (+0x114), AirResistV (+0x118) times Speed.y and AirResistH (+0x11C), turns it by the pad
 * (0x004603D0) and stores it as Acc.x, Acc.y and Acc.z at +0x58, +0x5C and +0x60
 * (0x0045E03A-0x0045E040). 0x004613C0 then adds Acc to Speed, `fld [ebx+0x5C]; fadd [ebx+0x68];
 * fstp [ebx+0x68]` (0x004613DD-0x004613E3), so Acc.y, the gravity share, is the sink. Weight and
 * AirResistV are table fields that falling and jumping read too, so a table change would reach
 * more than the glide.
 *
 * The wrap reads the CharObj2Base from EAX and the EntityData1 from ECX before the lifted body
 * runs, and the flag word; after the body, when the flag was clear, it writes Acc.y = -Speed.y.
 * The add at 0x004613DD then gives Speed.y exactly 0: no sink and no rise from the glide's own
 * acceleration, whatever Speed.y was on entry (Speed.y is at most 0 there). A frame with the flag
 * set is left as the body left it. Releasing the button ends the action as the game does.
 *
 * Off is identical: the wrap tests its bit first and otherwise only calls the lifted body.
 *
 * Risks. A gameplay change: a level glide has no distance limit, which breaks the treasure-hunt
 * stages' routes and pit design. Nothing extra is saved; times and ranks earned with it are saved
 * as usual.
 *
 * The first time a run levels a glide it says so, once: "sa2 tweaks: hunter flat glide: glide
 * held level (Speed.y -0.5 -> 0)".
 */
#include <stdint.h>
#include <stdio.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_tweaks.h"

#define GLIDE_ACC_Y    0x5Cu     /* float at CharObj2Base+0x5C */
#define GLIDE_SPEED_Y  0x68u     /* float at CharObj2Base+0x68 */
#define GLIDE_FLAGS    0x04u     /* u16 at EntityData1+4 */
#define GLIDE_HELD     0x4000u   /* set: the acceleration is not computed (0x0045DF24) */

static int g_glide_read_said, g_glide_write_said, g_glide_named;

void *__real_sub_45df10(void *state, uint32_t pc, void *memory);

__attribute__((used, retain))
void *__wrap_sub_45df10(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_HUNTER_FLAT_GLIDE)) return __real_sub_45df10(state, pc, memory);
    const uint32_t co2 = sa2_rt_get_gpr(state, SA2_EAX);
    const uint32_t data1 = sa2_rt_get_gpr(state, SA2_ECX);
    uint16_t flags = 0;
    if (sa2_peek(data1 + GLIDE_FLAGS, 2, &flags) != SA2_OK) {
        if (!g_glide_read_said++)
            fprintf(stderr, "sa2 tweaks: hunter flat glide: the flags at 0x%08X do not read, so the "
                    "glide sinks as usual\n", data1 + GLIDE_FLAGS);
        return __real_sub_45df10(state, pc, memory);
    }
    void *ret = __real_sub_45df10(state, pc, memory);
    if (flags & GLIDE_HELD) return ret;
    float speed_y = 0.0f;
    if (sa2_peek(co2 + GLIDE_SPEED_Y, 4, &speed_y) != SA2_OK) {
        if (!g_glide_read_said++)
            fprintf(stderr, "sa2 tweaks: hunter flat glide: the speed at 0x%08X does not read, so "
                    "the glide sinks as usual\n", co2 + GLIDE_SPEED_Y);
        return ret;
    }
    const float acc_y = -speed_y;
    if (sa2_poke(co2 + GLIDE_ACC_Y, 4, &acc_y) != SA2_OK) {
        if (!g_glide_write_said++)
            fprintf(stderr, "sa2 tweaks: hunter flat glide: the acceleration at 0x%08X does not write, "
                    "so the glide sinks as usual\n", co2 + GLIDE_ACC_Y);
        return ret;
    }
    if (!g_glide_named++)
        fprintf(stderr, "sa2 tweaks: hunter flat glide: glide held level (Speed.y %g -> 0)\n",
                (double)speed_y);
    return ret;
}
