/* homing-range: the homing attack locks on to targets up to 200 units away, twice the game's 100,
 * or SA2_HOMING_RANGE units (100 to 400) (runtime/sa2_tweaks.h: the tweaks and their rules).
 * Players asked for it as "Longer Homing Attack" (GitHub issue #67).
 *
 * TWEAK_WRAPS: sub_47e960
 *
 * Where the range is, read from the unpacked image. CheckCollision (0x00485850) calls 0x0047EA90
 * for every collision pair it tests, before any overlap test. 0x0047EA90 puts the pair's two
 * EntityData1 pointers in ECX (the first, a player's when the pair is a player's) and EDX (the
 * other), computes the squared distance d2 between their centres (+0x14, +0x18, +0x1C), and passes
 * it to 0x0047E960 as its one stack argument (push ecx; fstp [esp]; call; the x87 stack is empty at
 * the call, and no register carries d2). 0x0047E960 (303 bytes, to its RET at 0x0047EA8E):
 *
 *   0x0047E960  fld [0x00905664] (10000.0, 100 squared); fld [esp+4]; fcom; je skip unless C0:
 *               only d2 < 10000 goes on, and d2 stays on the x87 stack;
 *   0x0047E979  [[ECX+0x2C]] (a word) must be 0 and [[EDX+0x2C]+4] must have 0x40;
 *   0x0047E993  ECX == MainCharObj1[0] ([0x01DEA6C0]): the entry {EDX, d2} (fstp [eax+4]) goes to
 *               player 1's list 0x01DE6FA0 (count 0x01DE46B8, at most 0x390), or to 0x01DE8C40
 *               (count 0x01DE8C28, at most 0x100) when the kind byte [EDX+2] is 0x11; each entry
 *               8 bytes, and the next entry's first dword set to 0;
 *   0x0047EA0B  ECX == MainCharObj1[1] ([0x01DEA6C4]): the same for player 2, 0x01DE5100 (count
 *               0x01DE50E0) or 0x01DE46C0 (count 0x01DE9448);
 *   0x0047EA7B  neither: [esp+4] is read again, as a pointer, and the entry is written there.
 *
 * d2 is read for the compare, the stored entry and that last path, nothing else. 0x00905664 is
 * read by about 30 other places, so it is not changed.
 *
 * Who reads the lists. 0x00720F00, the homing attack's pick (from Sonic_Main, and from 0x00720E00 in
 * Sonic_ChecksForDamage), starts its best at 10000 and keeps the entry with the smallest stored d2
 * within 0x5000 of the player's facing; Amy and Metal Sonic run Sonic's code. 0x0045B330 (Sonic,
 * Knuckles, Tails and Eggman's Main) turns the player toward the nearest entry; 0x004760A0 and
 * 0x004760F0 (the four characters' action windows) pick an action by whether the list is empty;
 * 0x00721840, 0x00724170, 0x00725640, 0x00734130, 0x007404A0, 0x007528A0 and 0x004C5710 keep the
 * entry with the smallest stored d2. Each compares stored d2 with stored d2 alone, but for two
 * readers that compare it with a rival's: 0x00720F00 and 0x00721840 ask 0x0046DE60 for the first
 * other MainCharObj1 slot whose kind byte [+2] is 1 or 3, and start their best at its true d2
 * (0x00720F00 only when that is below 10000). The 0x11 lists are read only by 0x00721480, which
 * measures each entry again and keeps it within 32 or 64 units (0x008AE554, 0x00905078).
 *
 * What the wrap does. On, with R the range, it reads d2 at guest [ESP+4] and, for a pair whose ECX
 * is player 1's or player 2's, whose kind is not 0x11, and with d2 below R squared, writes
 * d2 x (100 / R) squared there (R = 200: a quarter) before it calls the lifted body, and writes the
 * game's d2 back after it returns. A target up to R away then passes the body's compare, and every
 * entry in the list is scaled by the same factor, so the pick's 10000 and its choice of the nearest
 * entry still hold. The other cases are left to the game, said once each: the path that reads d2 as
 * a pointer, the 0x11 lists (their reader's own limits are under 100 units), and a rival as
 * 0x0046DE60 finds it within R of the player, so a scaled entry never beats a rival nearer than it.
 * Off, the wrap only calls the lifted body.
 *
 * What else the longer range reaches. Every reader above sees targets up to R: Sonic, Knuckles,
 * Tails and Eggman turn toward a target, and take the action for "a target is near", from as far.
 * In a rival fight or a two-player battle, the range is the game's while the rival is within R.
 *
 * The risk. Nobody has played it yet. A larger range puts more entries in a list, and a list at its
 * limit (0x390) drops the entries after it. A target the game does not register for collision is
 * not listed at any range. The rival's distance is measured when the pair is tested, not when the
 * pick runs, so near the edge of R the two can disagree for a frame.
 *
 * SA2_HOMING_RANGE is read once, a whole number of units from 100 to 400; unset or empty, 200. A
 * value that does not read, or is out of that range, is named and 200 used. The first target
 * listed beyond 100 units is said: "sa2 tweaks: homing range 200: player 1 listed a target 173 units
 * away (the game's limit is 100)".
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_tweaks.h"

#define HR_GAME_RANGE   100u
#define HR_DEFAULT      200u
#define HR_LOW          100u
#define HR_HIGH         400u
#define HR_MAIN_CHAR    0x01DEA6C0u   /* MainCharObj1[8], each player's EntityData1 */
#define HR_SLOTS        8u
#define HR_KIND         2u            /* the kind byte the body and 0x0046DE60 read */
#define HR_KIND_SHORT   0x11u         /* the kind that goes to the 0x11 lists */
#define HR_POS          0x14u         /* the centre, three floats */

static const uint32_t g_hr_count[2] = {0x01DE46B8u, 0x01DE50E0u};   /* the lists' counts, 16 bits */

static unsigned g_hr_range;   /* 0 until read */
static int g_hr_said, g_hr_stack_said, g_hr_read_said, g_hr_other_said, g_hr_short_said,
    g_hr_rival_said;

void *__real_sub_47e960(void *state, uint32_t pc, void *memory);

static unsigned hr_range(void) {
    if (g_hr_range) return g_hr_range;
    const char *v = getenv("SA2_HOMING_RANGE");
    g_hr_range = HR_DEFAULT;
    if (!v || !*v) return g_hr_range;
    unsigned long n = 0;
    const char *s = v;
    while (*s >= '0' && *s <= '9' && n <= HR_HIGH) n = n * 10 + (unsigned long)(*s++ - '0');
    if (*s || s == v || n < HR_LOW || n > HR_HIGH)
        fprintf(stderr, "sa2 tweaks: homing range: SA2_HOMING_RANGE=%s is not a whole number of "
                "units from %u to %u, so the range is %u\n", v, HR_LOW, HR_HIGH, HR_DEFAULT);
    else g_hr_range = (unsigned)n;
    return g_hr_range;
}

static int rd32(uint32_t a, uint32_t *v) { return sa2_peek(a, 4, v) == SA2_OK; }

static int rdf(uint32_t a, float *v) { return sa2_peek(a, 4, v) == SA2_OK; }

static void hr_unread(uint32_t at) {
    if (!g_hr_read_said++)
        fprintf(stderr, "sa2 tweaks: homing range: the guest memory at 0x%08X does not read, so the "
                "pair is left to the game\n", at);
}

/* The squared distance between two EntityData1 centres; -1 when one does not read. */
static double hr_d2(uint32_t a, uint32_t b) {
    double sum = 0;
    for (uint32_t i = 0; i < 3; i++) {
        float p = 0, q = 0;
        if (!rdf(a + HR_POS + 4 * i, &p)) { hr_unread(a + HR_POS + 4 * i); return -1; }
        if (!rdf(b + HR_POS + 4 * i, &q)) { hr_unread(b + HR_POS + 4 * i); return -1; }
        sum += ((double)p - q) * ((double)p - q);
    }
    return sum;
}

/* Whether the player in slot `own` (EntityData1 `self`) has a rival within `range`, the one
 * 0x0046DE60 gives: the first other slot whose kind is 1 or 3. -1 when it does not read. */
static int hr_rival_near(uint32_t own, uint32_t self, unsigned range) {
    for (uint32_t i = 0; i < HR_SLOTS; i++) {
        uint32_t p = 0;
        uint8_t kind = 0;
        if (i == own) continue;
        if (!rd32(HR_MAIN_CHAR + 4 * i, &p)) { hr_unread(HR_MAIN_CHAR + 4 * i); return -1; }
        if (!p) continue;
        if (sa2_peek(p + HR_KIND, 1, &kind) != SA2_OK) { hr_unread(p + HR_KIND); return -1; }
        if (kind != 1 && kind != 3) continue;
        const double d2 = hr_d2(self, p);
        if (d2 < 0) return -1;
        return d2 < (double)range * range;
    }
    return 0;
}

static unsigned hr_units(float d2) {
    unsigned u = 0;
    while ((double)(u + 1) * (u + 1) <= d2) u++;
    return u;
}

__attribute__((used, retain))
void *__wrap_sub_47e960(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_HOMING_RANGE)) return __real_sub_47e960(state, pc, memory);
    const unsigned range = hr_range();
    const uint32_t arg = sa2_rt_get_gpr(state, SA2_ESP) + 4u;
    const uint32_t self = sa2_rt_get_gpr(state, SA2_ECX), other = sa2_rt_get_gpr(state, SA2_EDX);
    uint32_t bits = 0, players[2] = {0, 0};
    uint16_t before = 0, after = 0;
    if (!rd32(arg, &bits)) {
        if (!g_hr_stack_said++)
            fprintf(stderr, "sa2 tweaks: homing range: the guest stack at 0x%08X does not read, so "
                    "the pair is left to the game\n", arg);
        return __real_sub_47e960(state, pc, memory);
    }
    float d2;
    memcpy(&d2, &bits, 4);
    if (range == HR_GAME_RANGE || !((double)d2 < (double)range * range))
        return __real_sub_47e960(state, pc, memory);
    if (!rd32(HR_MAIN_CHAR, &players[0]) || !rd32(HR_MAIN_CHAR + 4, &players[1])) {
        hr_unread(HR_MAIN_CHAR);
        return __real_sub_47e960(state, pc, memory);
    }
    const uint32_t own = self && self == players[0] ? 0 : self && self == players[1] ? 1 : 2;
    if (own == 2) {
        if (!g_hr_other_said++)
            fprintf(stderr, "sa2 tweaks: homing range: a pair whose first object (0x%08X) is no "
                    "player's is left to the game\n", self);
        return __real_sub_47e960(state, pc, memory);
    }
    uint8_t kind = 0;
    if (sa2_peek(other + HR_KIND, 1, &kind) != SA2_OK) {
        hr_unread(other + HR_KIND);
        return __real_sub_47e960(state, pc, memory);
    }
    if (kind == HR_KIND_SHORT) {
        if (!g_hr_short_said++)
            fprintf(stderr, "sa2 tweaks: homing range: targets of kind 0x11 keep the game's range "
                    "(their reader keeps them within 64 units)\n");
        return __real_sub_47e960(state, pc, memory);
    }
    const int rival = hr_rival_near(own, self, range);
    if (rival) {
        if (rival > 0 && !g_hr_rival_said++)
            fprintf(stderr, "sa2 tweaks: homing range: player %u's rival is within %u units, so the "
                    "game's range is kept while it is\n", own + 1, range);
        return __real_sub_47e960(state, pc, memory);
    }
    const float scaled = (float)((double)d2 * ((double)HR_GAME_RANGE * HR_GAME_RANGE) /
                                 ((double)range * range));
    const int counted = sa2_peek(g_hr_count[own], 2, &before) == SA2_OK;
    if (sa2_poke(arg, 4, &scaled) != SA2_OK) {
        hr_unread(arg);
        return __real_sub_47e960(state, pc, memory);
    }
    memory = __real_sub_47e960(state, pc, memory);
    sa2_poke(arg, 4, &bits);
    if (!g_hr_said && counted && sa2_peek(g_hr_count[own], 2, &after) == SA2_OK &&
        after == (uint16_t)(before + 1) && (double)d2 >= (double)HR_GAME_RANGE * HR_GAME_RANGE) {
        g_hr_said = 1;
        fprintf(stderr, "sa2 tweaks: homing range %u: player %u listed a target %u units away (the "
                "game's limit is %u)\n", range, own + 1, hr_units(d2), HR_GAME_RANGE);
    }
    return memory;
}
