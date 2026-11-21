/* endless-chao-items: a small animal or a Chaos Drive that a Chao has eaten is not used up: it
 * drops, and the player can pick it up and give it again (runtime/sa2_tweaks.h: the tweaks and
 * their rules). Players asked for it as "infinite drives and animals" for raising a Chao without
 * farming stages. The Chao's stat points are the game's own, so they are kept in the Chao save.
 *
 * TWEAK_WRAPS: sub_544f30 sub_548870
 *
 * Where it comes from, read from the unpacked image. A Chao fed by hand takes the item in
 * 0x0056CFB0 (it links the two with 0x00530640 and sets flag 2 of the item's word at [entry + 6],
 * entry = [item task + 0x3C]: held by a Chao), and eats it in its behaviour 0x00566DF0, which adds
 * the points (0x00535F00, called at 0x005674C4) and then lets go of the item (0x005674E5 to
 * 0x00567511: word [entry + 6] = 0, [entry + 0x0C] = -1, [entry + 0x40] = 0). It destroys nothing.
 * The item uses itself up, on its own next update, when 0x0056D170 (EDX = the item's task) answers
 * 0 because flag 2 is gone:
 *
 *   Chaos Drive. ALO_ChaosDriveExecutor_Main (0x005450C0) switches on the mode, byte 0 of the
 *   task's work (twp = [task + 0x34]), through the table at 0x00545138; mode 3, held by a Chao, is
 *   0x00544F30, called only at 0x005450F3 with ESI = the task, no stack arguments, and a plain RET
 *   (it keeps ESI and EDI). Let go, it clears the item's garden save record, rec = [entry + 8], at
 *   0x00544FC7 to 0x0054500D: the words at rec + 0 and + 2 to -1, at + 4 and + 6 to 0, the floats
 *   at + 8, + 0x0C and + 0x10 to 0.0 (0x008AF004), 0x14 bytes in all, then [entry + 8] = 0; and at
 *   0x00545010 sets the mode to 4 and byte 1 (the mode's set-up step) to 0. Mode 4 (0x00545020)
 *   shrinks the drive away and deletes its task. Mode 1 (0x00544B40) is the drive lying free: with
 *   byte 1 at 0 it first turns collision entries 0 and 1 back on (it clears flag 0x10 at
 *   [[[twp + 0x2C] + 0x0C] + 4] and + 0x34) and keeps entry 2 off (+ 0x64).
 *
 *   Small animal. AL_MinimalExecutor_Main (0x00548C50) switches on the same mode byte; mode 2,
 *   held, is 0x00548870, called only at 0x00548C6F with EAX = the task and no stack arguments. Let
 *   go, it sets the dword at [twp + 0xF4] to 1 (0x0054893E), clears the save record exactly as the
 *   drive does (0x00548948 to 0x0054898E), clears flags 0x240 of the word at [twp + 4], turns
 *   collision entries 0 and 2 off, and puts the mode and byte 1 to 0 (0x005489C4). Mode 0 is the
 *   animal's own behaviour, from the table at 0x01316B00, 0x0057BD40 for all 21 kinds in the image;
 *   its set-up turns collision entry 0 back on only while [twp + 0xF4] is 0 (0x0057BD69), and with
 *   it set, 0x005489D0 counts down while the animal is off camera (0x00548A06) and then deletes
 *   it. [twp + 0xF4] is "eaten".
 *
 * What the wraps do. On, each reads the mode first and acts only in the mode it is called in to
 * hold the item (drive 3, animal 2). Before the lifted body it keeps rec and its 0x14 bytes (when
 * rec is not 0; the body clears none otherwise), and for an animal the dword at + 0xF4. After it,
 * a drive that went from 3 to 4 is put in mode 1 with byte 1 at 0, so its set-up runs again and it
 * lies free where the Chao let go of it; an animal that went from 2 to 0 with + 0xF4 now 1 gets
 * its old + 0xF4 back. Before either, rec's 0x14 bytes are written back and rec goes back into
 * [entry + 8]. Everything else the body did is kept. Off, each wrap only calls the lifted body.
 *
 * What it does not do, and what is not checked. Fruit is not covered: a Chao eats it through
 * another function (0x00545790). The item is not given back to the Chao: in mode 1 a drive keeps
 * collision entry 2 off, and an animal's set-up turns entry 2 off, and entry 2 is read as the one
 * a Chao takes an item by (not checked in the collision data), so a Chao should not take it again
 * until the player has picked it up and let go of it once more. The wraps cannot tell a meal from
 * any other let-go, so any item a Chao lets go of, eaten or not, now drops instead of being used
 * up. The save record is written back as it was when the Chao held the item; whether the game
 * updates its position when the item moves again, or saves the item at the old place, is not
 * read. Nobody has played it yet: that the dropped item can be picked up and given again rests on
 * the reading above.
 *
 * Said once each: the first item kept, "sa2 tweaks: endless chao items: a Chao ate a Chaos Drive;
 * it was kept for another go" (or "a small animal"); an item's work that does not read, or a
 * write back that fails, which leaves the game's own behaviour (the item is used up).
 */
#include <stdint.h>
#include <stdio.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_tweaks.h"

#define ENDLESS_TWP        0x34u   /* the task's work; byte 0 the mode, byte 1 its set-up step */
#define ENDLESS_ENTRY      0x3Cu   /* the task's link entry */
#define ENDLESS_REC        0x08u   /* in the entry: the item's garden save record */
#define ENDLESS_REC_SIZE   0x14u   /* the bytes the body clears from the record */
#define ENDLESS_EATEN      0xF4u   /* an animal's "eaten" dword, in its work */
#define DRIVE_HELD         3u
#define DRIVE_GONE         4u
#define DRIVE_FREE         1u
#define ANIMAL_HELD        2u
#define ANIMAL_FREE        0u

/* An item held by a Chao, as it was before the body ran. */
struct endless_item {
    uint32_t twp, entry, rec, eaten;
    uint8_t mode, bytes[ENDLESS_REC_SIZE];
};

static int g_endless_kept_said, g_endless_read_said, g_endless_write_said;

void *__real_sub_544f30(void *state, uint32_t pc, void *memory);
void *__real_sub_548870(void *state, uint32_t pc, void *memory);

static int peek(uint32_t a, uint32_t n, void *v) { return sa2_peek(a, n, v) == SA2_OK; }
static int poke(uint32_t a, uint32_t n, const void *v) { return sa2_poke(a, n, v) == SA2_OK; }

/* The item's work and mode, then, in its holding mode, its record and (an animal) its eaten
 * dword. 1: held, all read; 0: not held, or a read failed (said once). */
static int endless_read(uint32_t task, uint8_t held, int animal, struct endless_item *it,
                        const char *what) {
    it->rec = it->eaten = 0;
    if (!peek(task + ENDLESS_TWP, 4, &it->twp) || !peek(it->twp, 1, &it->mode)) goto unread;
    if (it->mode != held) return 0;
    if (!peek(task + ENDLESS_ENTRY, 4, &it->entry)) goto unread;
    if (it->entry && !peek(it->entry + ENDLESS_REC, 4, &it->rec)) goto unread;
    if (it->rec && !peek(it->rec, ENDLESS_REC_SIZE, it->bytes)) goto unread;
    if (animal && !peek(it->twp + ENDLESS_EATEN, 4, &it->eaten)) goto unread;
    return 1;
unread:
    if (!g_endless_read_said++)
        fprintf(stderr, "sa2 tweaks: endless chao items: a %s's work does not read (task 0x%08X), "
                "so it is used up as usual\n", what, task);
    return 0;
}

static void endless_unwritten(const char *what, uint32_t task) {
    if (!g_endless_write_said++)
        fprintf(stderr, "sa2 tweaks: endless chao items: a %s's work does not write (task "
                "0x%08X), so it is used up as usual\n", what, task);
}

/* The record's bytes, then the record back in the entry, as they were; first of the writes, so a
 * record that does not write leaves the item as the body left it. */
static int endless_put_back(const struct endless_item *it) {
    if (!it->rec) return 1;
    return poke(it->rec, ENDLESS_REC_SIZE, it->bytes) && poke(it->entry + ENDLESS_REC, 4, &it->rec);
}

static void endless_kept(const char *what) {
    if (!g_endless_kept_said++)
        fprintf(stderr, "sa2 tweaks: endless chao items: a Chao ate a %s; it was kept for another "
                "go\n", what);
}

__attribute__((used, retain))
void *__wrap_sub_544f30(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_ENDLESS_CHAO_ITEMS)) return __real_sub_544f30(state, pc, memory);
    static const char what[] = "Chaos Drive";
    const uint32_t task = sa2_rt_get_gpr(state, SA2_ESI);
    struct endless_item it;
    const int held = endless_read(task, DRIVE_HELD, 0, &it, what);
    memory = __real_sub_544f30(state, pc, memory);
    uint8_t mode = 0;
    if (!held || !peek(it.twp, 1, &mode) || mode != DRIVE_GONE) return memory;
    const uint8_t free_mode[2] = {DRIVE_FREE, 0};
    if (!endless_put_back(&it) || !poke(it.twp, 2, free_mode)) {
        endless_unwritten(what, task);
        return memory;
    }
    endless_kept(what);
    return memory;
}

__attribute__((used, retain))
void *__wrap_sub_548870(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_ENDLESS_CHAO_ITEMS)) return __real_sub_548870(state, pc, memory);
    static const char what[] = "small animal";
    const uint32_t task = sa2_rt_get_gpr(state, SA2_EAX);
    struct endless_item it;
    const int held = endless_read(task, ANIMAL_HELD, 1, &it, what);
    memory = __real_sub_548870(state, pc, memory);
    uint8_t mode = 0xFF;
    uint32_t eaten = 0;
    if (!held || !peek(it.twp, 1, &mode) || mode != ANIMAL_FREE ||
        !peek(it.twp + ENDLESS_EATEN, 4, &eaten) || eaten != 1)
        return memory;
    if (!endless_put_back(&it) || !poke(it.twp + ENDLESS_EATEN, 4, &it.eaten)) {
        endless_unwritten(what, task);
        return memory;
    }
    endless_kept(what);
    return memory;
}
