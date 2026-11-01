/* music-volume: the stage, menu and cutscene music plays at a fraction of the game's level, 60
 * percent by default or SA2_MUSIC_VOLUME percent (0 to 100); sound effects, voices and the short
 * music cues keep the game's level (runtime/sa2_tweaks.h: the tweaks and their rules). The PC port
 * mixes its music loud against the Dreamcast's, and the game has no music volume setting.
 *
 * TWEAK_WRAPS: sub_439490
 *
 * Where the level comes from, read from the unpacked image. The music plays through one stream
 * player, the object at +4 of the audio manager (the 0x1BC-byte object that 0x004021E0 returns,
 * kept at [0x01AF19E0]), and 0x00439490 is its volume setter (cdecl, one argument): a level in
 * tenths of a decibel, held to -960 .. 0 (0xFFFFFC40 at 0x004394A5), stored at manager+0x1B8
 * (0x004394CA), divided by 10.0 (the double at 0x00904F48) and by the double at 0x009050B0, raised
 * to a power, and given to the player's vtable slot 0x38 as a float gain. 155 sites call it: each
 * stage's init and the Chao areas' set that place's own music level (0 to about -10 dB), and the
 * music task (0x00442AD0) fades the music out and in by calling it with the stored level minus 8 a
 * frame, so the stored level has to stay the game's own: the fade reads it back.
 *
 * The wrap acts on that one argument. On entry the guest ESP points at the return address and the
 * argument is the dword above it. It holds the level to the same -960 .. 0 range the body does, adds the
 * tweak's level (a negative number of tenths, from a table below, so that no host's math library
 * is in it: 50 percent is -60, 60 is -44, 0 is -960), writes the sum into the argument slot, calls
 * the lifted body, puts the game's argument back in the slot (it is the caller's), and, when the
 * body stored a level (the player at manager+4 is not null), writes the game's held level back to
 * manager+0x1B8. The music sounds at the game's level plus the tweak's, a fade reads the game's
 * level and not the sum, and the next call adds the tweak's level once, not again.
 *
 * Unset or empty, SA2_MUSIC_VOLUME is 60. Text that is not a whole number from 0 to 100 is named
 * once and the default used.
 *
 * Left alone: the short music cues (1-up, speed shoes, invincibility, goal ring, boss cues). The
 * loader 0x004429E0 starts them with the player's volume set straight to the float at 0x00C71F38
 * (0x00442A22-0x00442A33, the vtable slot 0x38 again), not through 0x00439490, so they play at
 * the game's level. The characters' voices play on three ADX streams of their own, whose level is
 * the dword at 0x01A55990 (0x004427E4 and 0x00442C1E), and the sound effects are mixed elsewhere.
 *
 * Off: the wrap's first test is sa2_tweak_on, and then it only calls the lifted body; nothing is
 * read or written, SA2_MUSIC_VOLUME included.
 *
 * Risks. It is a mix level, not a game rule: it changes no tick, no random number and no save, and
 * it is off in replays all the same, as every tweak is. A quiet level makes the music hard to hear
 * on a quiet device, and the cues, which keep their level, stand out against it. A guest stack,
 * manager or player that does not read or write leaves the game's level and says so once. The
 * address is the pinned image's: a build whose code moved would wrap whatever is at 0x00439490
 * now, so this tweak is for the pinned image alone.
 *
 * The run says what it does: once, the first level added ("sa2 tweaks: music volume 60%: music
 * levels are lowered by 44 tenths of a decibel, held at -960"), and once a read or write that failed.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_tweaks.h"

#define MUSIC_ARG_SLOT        4u            /* from the guest ESP at entry: above the return address */
#define MUSIC_MANAGER_PTR     0x01AF19E0u   /* the audio manager, or 0 before it is made */
#define MUSIC_PLAYER_OFFSET   0x04u         /* manager+4: the stream player, 0 when there is none */
#define MUSIC_LEVEL_OFFSET    0x1B8u        /* manager+0x1B8: the level 0x00439490 stored */
#define MUSIC_LEVEL_MIN       (-960)
#define MUSIC_VOLUME_DEFAULT  60u

/* round(200 x log10(p / 100)) for p = 0 to 100; p = 0 is -960, the bottom of the game's range. */
static const int16_t k_level[101] = {
    -960, -400, -340, -305, -280, -260, -244, -231, -219, -209,
    -200, -192, -184, -177, -171, -165, -159, -154, -149, -144,
    -140, -136, -132, -128, -124, -120, -117, -114, -111, -108,
    -105, -102,  -99,  -96,  -94,  -91,  -89,  -86,  -84,  -82,
     -80,  -77,  -75,  -73,  -71,  -69,  -67,  -66,  -64,  -62,
     -60,  -58,  -57,  -55,  -54,  -52,  -50,  -49,  -47,  -46,
     -44,  -43,  -42,  -40,  -39,  -37,  -36,  -35,  -33,  -32,
     -31,  -30,  -29,  -27,  -26,  -25,  -24,  -23,  -22,  -20,
     -19,  -18,  -17,  -16,  -15,  -14,  -13,  -12,  -11,  -10,
      -9,   -8,   -7,   -6,   -5,   -4,   -4,   -3,   -2,   -1,
       0,
};

static unsigned g_music_percent = 101;   /* 101 until read */
static int g_music_said, g_music_stack_said, g_music_store_said;

/* SA2_MUSIC_VOLUME as a percent: digits only, 0 to 100; -1 when it is not that. */
static long music_parse_percent(const char *s) {
    long v = 0;
    int n = 0;
    while (*s >= '0' && *s <= '9' && n < 4) v = v * 10 + (*s++ - '0'), n++;
    if (*s || n == 0 || v > 100) return -1;
    return v;
}

static unsigned music_percent(void) {
    if (g_music_percent <= 100) return g_music_percent;
    const char *v = getenv("SA2_MUSIC_VOLUME");
    g_music_percent = MUSIC_VOLUME_DEFAULT;
    if (v && *v) {
        const long p = music_parse_percent(v);
        if (p < 0)
            fprintf(stderr, "sa2 tweaks: music volume: SA2_MUSIC_VOLUME=%s is not a whole number "
                    "from 0 to 100, so the volume is %u%%\n", v, MUSIC_VOLUME_DEFAULT);
        else g_music_percent = (unsigned)p;
    }
    return g_music_percent;
}

/* The tweak's level, in tenths of a decibel (0 or below), for a percent from 0 to 100. Exposed for
 * the unit test (Documentation/scripts/tests/test_tweak_music_volume.py). */
int32_t sa2_music_volume_level(unsigned percent) {
    return k_level[percent > 100 ? 100 : percent];
}

/* The game's argument held to the range the body holds it to. */
static int32_t music_held(int32_t level) {
    return level < MUSIC_LEVEL_MIN ? MUSIC_LEVEL_MIN : level > 0 ? 0 : level;
}

void *__real_sub_439490(void *state, uint32_t pc, void *memory);

__attribute__((used, retain))
void *__wrap_sub_439490(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_MUSIC_VOLUME)) return __real_sub_439490(state, pc, memory);
    const uint32_t slot = sa2_rt_get_gpr(state, SA2_ESP) + MUSIC_ARG_SLOT;
    int32_t arg = 0;
    if (sa2_peek(slot, 4, &arg) != SA2_OK) {
        if (!g_music_stack_said++)
            fprintf(stderr, "sa2 tweaks: music volume: the guest stack at 0x%08X does not read, so "
                    "the music keeps the game's level\n", slot);
        return __real_sub_439490(state, pc, memory);
    }
    const unsigned p = music_percent();
    const int32_t held = music_held(arg);
    const int32_t lowered = held + sa2_music_volume_level(p);
    if (sa2_poke(slot, 4, &lowered) != SA2_OK) {
        if (!g_music_stack_said++)
            fprintf(stderr, "sa2 tweaks: music volume: the guest stack at 0x%08X does not write, so "
                    "the music keeps the game's level\n", slot);
        return __real_sub_439490(state, pc, memory);
    }
    if (!g_music_said++)
        fprintf(stderr, "sa2 tweaks: music volume %u%%: music levels are lowered by %d tenths of a "
                "decibel, held at %d\n", p, (int)-sa2_music_volume_level(p), MUSIC_LEVEL_MIN);
    void *ret = __real_sub_439490(state, pc, memory);
    sa2_poke(slot, 4, &arg);
    uint32_t manager = 0, player = 0;
    if (sa2_peek(MUSIC_MANAGER_PTR, 4, &manager) == SA2_OK && manager &&
        sa2_peek(manager + MUSIC_PLAYER_OFFSET, 4, &player) == SA2_OK && player) {
        if (sa2_poke(manager + MUSIC_LEVEL_OFFSET, 4, &held) != SA2_OK && !g_music_store_said++)
            fprintf(stderr, "sa2 tweaks: music volume: the music level at 0x%08X does not write, so "
                    "a fade reads the lowered level\n", manager + MUSIC_LEVEL_OFFSET);
    }
    return ret;
}
