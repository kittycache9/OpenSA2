/* endless-chao-items: a small animal or a Chaos Drive that a Chao has eaten stays a usable item, so
 * the same one can be given to a Chao again and again (runtime/sa2_tweaks.h: the tweaks and their
 * rules). Players asked for it as "infinite drives and animals" for raising a Chao without farming
 * stages. The Chao's stat points are the game's own, so they are kept in the Chao save.
 *
 * TWEAK_WRAPS: sub_566df0
 *
 * Where it comes from, read from the unpacked image. 0x00566DF0 (unnamed) is the Chao's eating
 * state, called once a frame with the Chao's task as its one stack argument (cdecl). The Chao's
 * state word is the 16 bits at [[task + 0x34] + 0x1B6]. In state 1, once the Chao's animation
 * flag [[task + 0x34] + 0xAC] & 1 is set, one call does all of the eating (0x00566EE6 to
 * 0x0056751A): it moves the state to 2, adds the stat points with 0x00535F00, and lets go of the
 * item. The item is found through two links, each read the way the body reads them:
 *
 *   item = [task + 0x3C]            the item the Chao holds
 *   pair = [item + 0x40]            its partner; the body uses it only if [pair + 0x40] == item
 *   body = [[pair + 0x3C] + 0x34]   the item's data; its byte [body + 3] is the item's type
 *
 * Types 0x00 to 0x14 are the 21 small animals and 0x15 to 0x18 the four Chaos Drives, the rows of
 * chao-stat-x5's first table.
 *
 * What the body does to let go (0x005674E5 to 0x00567511) is what uses the item up: for the item,
 * and for its partner when the partner points back, it sets the 16-bit word at +6 to 0, the dword
 * at +0x0C to -1, and the link at +0x40 to 0. Nothing else is written, and the Chao's own link
 * [task + 0x3C] is left as it was.
 *
 * What the wrap does. On, it reads those three fields of the item and its partner before the call,
 * only when the Chao is in state 1 and the two links agree, the case where the body adds points.
 * After the call it checks that the state is 2 and that [item + 0x40] is 0: the Chao has eaten.
 * Then it writes the fields back as they were, so the item is as it was before the meal and the
 * game's own handling of a Chao that holds an item lets it go to the ground. Off, the wrap only
 * calls the lifted body.
 *
 * What it does not do. It does not touch fruit, which another function feeds to the Chao
 * (0x00545790), and it does not change what the Chao gains: with chao-stat-x5 on, an item gives
 * the larger points each time.
 *
 * The risk. Nothing here runs the code after the meal, so what the Chao and the item do next is
 * the game's own handling of a held item, not something this file decides. If a Chao keeps holding
 * the item or eats it again at once, the fields written back are the place to look.
 *
 * The first time it keeps an item, the run says so: "sa2 tweaks: endless chao items: a Chao ate
 * item type N; the item was kept usable".
 */
#include <stdint.h>
#include <stdio.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_call.h"
#include "sa2_tweaks.h"

#define ENDLESS_TASK_DATA   0x34u    /* a task's data pointer */
#define ENDLESS_TASK_HELD   0x3Cu    /* the task's partner link; the Chao's held item */
#define ENDLESS_LINK        0x40u    /* the other link of a pair */
#define ENDLESS_WORD        0x06u    /* the 16-bit word the body sets to 0 */
#define ENDLESS_FIELD       0x0Cu    /* the dword the body sets to -1 */
#define ENDLESS_STATE       0x1B6u   /* the Chao's state word, in its data */
#define ENDLESS_LAST_TYPE   0x18u    /* 0 to 0x14 animals, 0x15 to 0x18 Chaos Drives */

/* What the body changes in one of the two tasks it lets go of. */
struct endless_task {
    uint32_t addr, link, field;
    uint16_t word;
};

static int g_endless_said;

void *__real_sub_566df0(void *state, uint32_t pc, void *memory);

static int rd8(uint32_t a, uint8_t *v) { return sa2_peek(a, 1, v) == SA2_OK; }
static int rd16(uint32_t a, uint16_t *v) { return sa2_peek(a, 2, v) == SA2_OK; }
static int rd32(uint32_t a, uint32_t *v) { return sa2_peek(a, 4, v) == SA2_OK; }

static int save_task(struct endless_task *t, uint32_t addr) {
    t->addr = addr;
    return rd32(addr + ENDLESS_LINK, &t->link) && rd32(addr + ENDLESS_FIELD, &t->field) &&
           rd16(addr + ENDLESS_WORD, &t->word);
}

static int restore_task(const struct endless_task *t) {
    return sa2_poke(t->addr + ENDLESS_LINK, 4, &t->link) == SA2_OK &&
           sa2_poke(t->addr + ENDLESS_FIELD, 4, &t->field) == SA2_OK &&
           sa2_poke(t->addr + ENDLESS_WORD, 2, &t->word) == SA2_OK;
}

/* The item and its partner, as the body will let them go, and the item's type, when the body will
 * add its points this call. */
static int endless_item(uint32_t task, struct endless_task *item, struct endless_task *pair,
                        uint32_t *type) {
    uint32_t data = 0, addr = 0, back = 0, partner_item = 0, body = 0;
    uint16_t state = 0;
    uint8_t kind = 0;
    if (!rd32(task + ENDLESS_TASK_DATA, &data) || !data) return 0;
    if (!rd16(data + ENDLESS_STATE, &state) || state != 1) return 0;
    if (!rd32(task + ENDLESS_TASK_HELD, &addr) || !addr) return 0;
    if (!save_task(item, addr)) return 0;
    if (!item->link || !save_task(pair, item->link)) return 0;
    if (!rd32(pair->addr + ENDLESS_LINK, &back) || back != item->addr) return 0;
    if (!rd32(pair->addr + ENDLESS_TASK_HELD, &partner_item) || !partner_item) return 0;
    if (!rd32(partner_item + ENDLESS_TASK_DATA, &body) || !body) return 0;
    if (!rd8(body + 3u, &kind) || kind > ENDLESS_LAST_TYPE) return 0;
    *type = kind;
    return 1;
}

__attribute__((used, retain))
void *__wrap_sub_566df0(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_ENDLESS_CHAO_ITEMS)) return __real_sub_566df0(state, pc, memory);
    struct endless_task item, pair;
    uint32_t task = 0, type = 0;
    const uint32_t esp = sa2_rt_get_gpr(state, SA2_ESP);
    const int eating = sa2_peek(esp + 4u, 4, &task) == SA2_OK && endless_item(task, &item, &pair, &type);
    memory = __real_sub_566df0(state, pc, memory);
    if (!eating) return memory;
    uint32_t data = 0, link = 1;
    uint16_t after = 0;
    if (!rd32(task + ENDLESS_TASK_DATA, &data) || !rd16(data + ENDLESS_STATE, &after) || after != 2 ||
        !rd32(item.addr + ENDLESS_LINK, &link) || link != 0)
        return memory;
    if (!restore_task(&pair) || !restore_task(&item)) return memory;
    if (!g_endless_said++)
        fprintf(stderr, "sa2 tweaks: endless chao items: a Chao ate item type %u; the item was kept "
                "usable\n", type);
    return memory;
}
