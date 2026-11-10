/* endless-chao-items: when a Chao eats a small animal or a Chaos Drive in the garden, the same item
 * goes back on the stack of items the player carries and falls from the sky again, so one item can
 * be given to a Chao as often as the player likes (runtime/sa2_tweaks.h: the tweaks and their
 * rules). Players asked for it as "infinite drives and animals" for raising a Chao without farming
 * stages. The Chao's stat points are the game's own, so they are kept in the Chao save.
 *
 * TWEAK_WRAPS: sub_566df0
 *
 * Where it comes from, read from the unpacked image. 0x00566DF0 (unnamed) is the Chao's eating
 * state, called once a frame with the Chao's task as its one stack argument (cdecl). The Chao's
 * state word is the 16 bits at [[task + 0x34] + 0x1B6]. In state 1, once the Chao's animation
 * flag [[task + 0x34] + 0xAC] & 1 is set, one call does all of the eating (0x00566EE6 to
 * 0x0056751A): it moves the state to 2, adds the stat points with 0x00535F00, and releases the
 * item. The item is found through two links, each read the way the body reads them:
 *
 *   item = [task + 0x3C]            the item the Chao holds
 *   pair = [item + 0x40]            its partner; the body uses it only if [pair + 0x40] == item
 *   body = [[pair + 0x3C] + 0x34]   the item's data; its byte [body + 3] is the item's type
 *
 * Types 0x00 to 0x14 are the 21 small animals and 0x15 to 0x18 the four Chaos Drives, the rows of
 * chao-stat-x5's first table. The release at 0x005674E5 to 0x00567511 clears the item's links,
 * [item + 0x40] among them, which is how a finished meal looks from outside.
 *
 * The items fall from the stack the player carries. 0x00487260, called with ECX = 0, returns the
 * player's record P: [P + 0xA0] is the number of items carried (at most 10), and entry i is the
 * 8 bytes at P + 0x50 + 8 * i, the type and then a timer the game starts at 0x1E. The game's own
 * push, 0x004872F0, writes a type and 0x1E into the next entry, adds 1 to the count, and puts the
 * float at 0x008AF004 in [P + 0xAC]. The delivery task, created by 0x00549490 with no arguments
 * and run by 0x005492B0, takes the last entry every 16 frames, makes an animal (0x00548D30) or a
 * drive (0x00545600) above the player, and when the stack is empty leaves for 0x00548F40.
 *
 * What the wrap does. On, it reads the item's type before the call, only when the Chao is in
 * state 1 and the two links agree, the case where the body adds points. After the call it checks
 * that the state is 2 and that [item + 0x40] is 0: the Chao has eaten. Then it puts the type on
 * the stack the way 0x004872F0 does (its entry, its count, and the float in [P + 0xAC]), and
 * when the stack was empty it creates a delivery task with 0x00549490, since the earlier one has
 * left. When the stack was not empty a delivery task is still running and takes the item. A full
 * stack (10 items) is left alone. Off, the wrap only calls the lifted body.
 *
 * What it does not do. It does not touch fruit, which another function feeds to the Chao
 * (0x00545790), and it does not change what the Chao gains: with chao-stat-x5 on, an item gives
 * the larger points each time. An item that a Chao picks up and does not eat comes back to the
 * ground the way it did before.
 *
 * The risks. The stack lives in the character's record, which may be saved with the game, so an
 * item put back and not yet fallen when the player leaves can stay in the save. The meal's stat
 * points are saved as always, so a Chao raised this way keeps its stats with the tweak off. The
 * calls into 0x00487260 and 0x00549490 run on the live state in the way recap's do
 * (runtime/sa2_guest_call.h). Each failure is said once by name and leaves the item eaten, as the
 * game has it.
 *
 * The first time it gives an item back, the run says so: "sa2 tweaks: endless chao items: a Chao
 * ate item type N; it goes back on the stack of carried items to fall again".
 */
#include <stdint.h>
#include <stdio.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_call.h"
#include "sa2_trap.h"
#include "sa2_tweaks.h"

#define ENDLESS_TASK_DATA     0x34u        /* a task's data pointer */
#define ENDLESS_TASK_HELD     0x3Cu        /* the task's partner link; the Chao's held item */
#define ENDLESS_TASK_LINK     0x40u        /* the other link of a pair */
#define ENDLESS_STATE         0x1B6u       /* the Chao's state word, in its data */
#define ENDLESS_LAST_TYPE     0x18u        /* 0 to 0x14 animals, 0x15 to 0x18 Chaos Drives */
#define ENDLESS_STACK_COUNT   0xA0u        /* items carried, in the player's record */
#define ENDLESS_STACK_FIRST   0x50u        /* the first entry */
#define ENDLESS_STACK_SIZE    10u
#define ENDLESS_STACK_TIMER   0xACu        /* the float the game's push resets */
#define ENDLESS_TIMER_SOURCE  0x008AF004u  /* the float it resets it to */
#define ENDLESS_ENTRY_START   0x1Eu        /* the second dword of a pushed entry */

static int g_endless_said, g_endless_fail_said;

void *__real_sub_566df0(void *state, uint32_t pc, void *memory);
void *sub_487260(void *state, uint32_t pc, void *memory);
void *sub_549490(void *state, uint32_t pc, void *memory);

static int rd8(uint32_t a, uint8_t *v) { return sa2_peek(a, 1, v) == SA2_OK; }
static int rd16(uint32_t a, uint16_t *v) { return sa2_peek(a, 2, v) == SA2_OK; }
static int rd32(uint32_t a, uint32_t *v) { return sa2_peek(a, 4, v) == SA2_OK; }
static int wr32(uint32_t a, uint32_t v) { return sa2_poke(a, 4, &v) == SA2_OK; }

static void endless_fail(const char *what) {
    if (g_endless_fail_said++) return;
    fprintf(stderr, "sa2 tweaks: endless chao items: %s, so the item stays eaten\n", what);
}

/* The type of the item the Chao is about to eat, when the body will add its points this call. */
static int endless_item(uint32_t task, uint32_t *item, uint32_t *type) {
    uint32_t data = 0, pair = 0, back = 0, held = 0, body = 0;
    uint16_t state = 0;
    uint8_t kind = 0;
    if (!rd32(task + ENDLESS_TASK_DATA, &data) || !data) return 0;
    if (!rd16(data + ENDLESS_STATE, &state) || state != 1) return 0;
    if (!rd32(task + ENDLESS_TASK_HELD, item) || !*item) return 0;
    if (!rd32(*item + ENDLESS_TASK_LINK, &pair) || !pair) return 0;
    if (!rd32(pair + ENDLESS_TASK_LINK, &back) || back != *item) return 0;
    if (!rd32(pair + ENDLESS_TASK_HELD, &held) || !held) return 0;
    if (!rd32(held + ENDLESS_TASK_DATA, &body) || !body) return 0;
    if (!rd8(body + 3u, &kind) || kind > ENDLESS_LAST_TYPE) return 0;
    *type = kind;
    return 1;
}

/* Call a lifted function with no stack arguments on the live state, with ECX set, and return its
 * EAX. The frame sits 0x40 bytes below ESP (sa2_guest_call.h's slack) with a sentinel return
 * address, and every register is put back after, as recap's call does. */
static void *endless_call(void *s, void *m, void *(*fn)(void *, uint32_t, void *), uint32_t va,
                          uint32_t ecx, uint32_t *eax) {
    uint32_t saved[SA2_GPR_COUNT];
    for (int i = 0; i < SA2_GPR_COUNT; ++i) saved[i] = sa2_rt_get_gpr(s, i);
    const uint32_t sentinel = SA2_CALL_SENTINEL, base = saved[SA2_ESP] - 0x40u;
    if (sa2_poke(base, 4, &sentinel) != SA2_OK) {
        sa2_trap_note("sa2 tweaks: endless chao items: the guest stack at %#010x does not take the "
                      "return address for %#010x", base, va);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, va);
    }
    sa2_rt_set_gpr(s, SA2_ECX, ecx);
    sa2_rt_set_gpr(s, SA2_ESP, base);
    sa2_rt_set_gpr(s, SA2_EIP, va);
    m = fn(s, va, m);
    const uint32_t eip = sa2_rt_get_gpr(s, SA2_EIP), esp = sa2_rt_get_gpr(s, SA2_ESP);
    if (eip != sentinel || esp != base + 4u) {
        sa2_trap_note("sa2 tweaks: endless chao items: %#010x returned to %#010x with esp %#010x, "
                      "not to the sentinel with esp %#010x", va, eip, esp, base + 4u);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, eip);
    }
    if (eax) *eax = sa2_rt_get_gpr(s, SA2_EAX);
    for (int i = 0; i < SA2_GPR_COUNT; ++i) sa2_rt_set_gpr(s, i, saved[i]);
    return m;
}

/* Put `type` back on the player's stack the way 0x004872F0 does, and start a delivery task when
 * the stack was empty. */
static void *endless_give_back(void *s, void *m, uint32_t type) {
    uint32_t p = 0, count = 0, timer = 0;
    m = endless_call(s, m, sub_487260, 0x00487260u, 0, &p);
    if (p < 0x1000u || !rd32(p + ENDLESS_STACK_COUNT, &count)) {
        endless_fail("the player's record at 0x00487260 does not read");
        return m;
    }
    if (count >= ENDLESS_STACK_SIZE) return m;
    const uint32_t entry = p + ENDLESS_STACK_FIRST + 8u * count;
    if (!rd32(ENDLESS_TIMER_SOURCE, &timer) || !wr32(entry, type) ||
        !wr32(entry + 4u, ENDLESS_ENTRY_START) || !wr32(p + ENDLESS_STACK_TIMER, timer) ||
        !wr32(p + ENDLESS_STACK_COUNT, count + 1u)) {
        endless_fail("the player's stack does not take the item");
        return m;
    }
    if (count == 0) m = endless_call(s, m, sub_549490, 0x00549490u, 0, 0);
    if (!g_endless_said++)
        fprintf(stderr, "sa2 tweaks: endless chao items: a Chao ate item type %u; it goes back on "
                "the stack of carried items to fall again\n", type);
    return m;
}

__attribute__((used, retain))
void *__wrap_sub_566df0(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_ENDLESS_CHAO_ITEMS)) return __real_sub_566df0(state, pc, memory);
    uint32_t task = 0, item = 0, type = 0;
    const uint32_t esp = sa2_rt_get_gpr(state, SA2_ESP);
    const int eating = sa2_peek(esp + 4u, 4, &task) == SA2_OK && endless_item(task, &item, &type);
    memory = __real_sub_566df0(state, pc, memory);
    if (!eating) return memory;
    uint32_t data = 0, link = 1;
    uint16_t after = 0;
    if (!rd32(task + ENDLESS_TASK_DATA, &data) || !rd16(data + ENDLESS_STATE, &after) ||
        after != 2 || !rd32(item + ENDLESS_TASK_LINK, &link) || link != 0)
        return memory;
    return endless_give_back(state, memory, type);
}
