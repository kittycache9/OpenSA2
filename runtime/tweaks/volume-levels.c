/* volume-levels: the music, the characters' voices and the sound effects each play at a percentage
 * of the game's level: SA2_MUSIC_VOLUME (60 by default), SA2_VOICE_VOLUME and SA2_SFX_VOLUME (100
 * by default), each a whole number from 0 to 100 (runtime/sa2_tweaks.h: the tweaks and their
 * rules). The PC port mixes its music loud against the Dreamcast's, and the game has no volume
 * settings. The Android app's Sound sliders set the three and turn the tweak on.
 *
 * TWEAK_WRAPS: sub_439490, sub_442ad0, sub_4425d0, sub_77ee90
 *
 * Read from the unpacked image. All three play through the CRI Sound Renderer, which the game
 * drives by three separate routes, so each has its own wrap.
 *
 * Music. The music plays through one stream player, the object at +4 of the audio manager (the
 * 0x1BC-byte object that 0x004021E0 returns, kept at [0x01AF19E0]), and 0x00439490 is its volume
 * setter (cdecl, one argument): a level in tenths of a decibel, held to -960 .. 0 (0xFFFFFC40 at
 * 0x004394A5), stored at manager+0x1B8 (0x004394CA), divided by 10.0 (the double at 0x00904F48)
 * and given to the player's vtable slot 0x38 as a float gain. 155 sites call it: each stage's init
 * and the Chao areas' set that place's own music level, and the music task (0x00442AD0) fades the
 * music out and in by calling it with the stored level minus 8 a frame, so the stored level has to
 * stay the game's own. The wrap holds the argument to -960 .. 0 as the body does, adds the music
 * level, writes the sum into the argument slot (above the return address at the guest ESP), calls
 * the lifted body, puts the game's argument back in the slot (the caller's), and, when the body
 * stored a level (the player at manager+4 is not null), writes the game's held level back to
 * manager+0x1B8. A fade reads the game's level, and the next call adds the tweak's level once.
 * The short music cues (1-up, speed shoes, invincibility, goal ring, boss cues) are left at the
 * game's level: the loader 0x004429E0 starts them with the player's volume set straight to the
 * float at 0x00C71F38 (0x00442A22-0x00442A33, the vtable slot 0x38 again), not through 0x00439490.
 *
 * Voices. Every voice, in stages, from Omochao and in cutscenes, plays on one of three ADX
 * streams (ADXT handles) in the music state at [0x01A55998]: PlayVoice (0x00443130) takes a free
 * stream for each line, and PlayEventVoice (0x00602FF0) and PlayMiniEventVoice (0x00457D20) call
 * it. ADXT_SetOutVol (0x007C2F70, cdecl: handle, level in tenths of a decibel, kept at
 * handle+0x40 and divided by 10.0 by the renderer, 0x007C6B90) sets each stream's level from the
 * dword at 0x01A55990: once when the streams are made (0x004425D0, at 0x004427E4) and each frame
 * the music task runs for each stream in use (0x00442AD0, at 0x00442C1E). The image reads that
 * dword only there (0x004427CC, 0x00442C13) and never writes it, so it is 0, full level. The two
 * wraps write the voice level into it before their lifted bodies run.
 *
 * Sound effects. The effects play on 59 channels kept in the sound driver at [0x01AF0ECC]: channel
 * i is 0x14 bytes from base+0xA001AC, with its level a float at +0xC (base+0xA001B8: the effect's
 * signed volume byte times 1/256, written by 0x00436E10 and by the driver's update at 0x004367A9)
 * and its pending changes as flag bits at +0x12 (base+0xA001BE; bit 4, the level). Once a frame
 * 0x00438750 calls 0x0077EE90, which reads each channel with bit 4 set and gives its voice
 * object's vtable slot 0x4C the gain (v + 0.5) x 1.3 (the doubles at 0x00904E98 and 0x00905D88,
 * 0x0077EF12), or v x 0.7 (0x009050C0) when the driver's mode byte at base+0xA00650 is set, held
 * at 0 or above; in that mode a channel with bit 0x20 also gets its byte at +0x10 (base+0xA001BC)
 * times 1/256 times 0.7 (0x0077F010). That function is the one reader of those levels. The wrap,
 * for each channel whose bit is set, writes the level that gives the effect gain times the tweak's
 * (g x (v + 0.5) - 0.5, or g x v in the other mode, and the byte times g, rounded down), calls the
 * lifted body, and puts every level it changed back, so the driver keeps the game's levels and no
 * change compounds.
 *
 * The levels. A percent p is the music level round(200 x log10(p / 100)) tenths of a decibel, from
 * a table so that no host's math library is in it (50 percent is -60, 60 is -44, 0 is -960, below
 * hearing), the voice level from the same table, and the effects' gain p / 100. Unset or empty, a
 * setting is its default. Text that is not a whole number from 0 to 100 is named once and the
 * default used.
 *
 * Off: each wrap's first test is sa2_tweak_on, and then it only calls the lifted body; nothing is
 * read or written, the three settings included.
 *
 * Risks. These are mix levels, not game rules: they change no tick, no random number and no save,
 * and the tweak is off in replays all the same, as every tweak is. A low level makes that sound
 * hard to hear, and the music cues, which keep their level, stand out against quiet music. A guest
 * stack, manager, player or driver that does not read or write leaves that sound at the game's
 * level and says so once. The addresses are the pinned image's: a build whose code moved would wrap
 * whatever is at them now, so this tweak is for the pinned image alone.
 *
 * The run says what it does, once: "sa2 tweaks: volume levels: music 60%, voices 100%, sound
 * effects 100%", and once for each read or write that failed.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_tweaks.h"

#define VL_ARG_SLOT          4u            /* from the guest ESP at entry: above the return address */
#define VL_MANAGER_PTR       0x01AF19E0u   /* the audio manager, or 0 before it is made */
#define VL_PLAYER_OFFSET     0x04u         /* manager+4: the stream player, 0 when there is none */
#define VL_LEVEL_OFFSET      0x1B8u        /* manager+0x1B8: the level 0x00439490 stored */
#define VL_LEVEL_MIN         (-960)
#define VL_VOICE_DWORD       0x01A55990u   /* the voice streams' ADX out volume */
#define VL_DRIVER_PTR        0x01AF0ECCu   /* the sound driver, or 0 before it is made */
#define VL_CHANNELS          59u
#define VL_CHANNEL_STRIDE    0x14u
#define VL_CHANNEL_LEVEL     0xA001B8u     /* channel 0's level, a float */
#define VL_CHANNEL_BYTE      0xA001BCu     /* channel 0's byte level (the other mode's bit 0x20) */
#define VL_CHANNEL_FLAGS     0xA001BEu     /* channel 0's pending changes */
#define VL_FLAG_LEVEL        0x04u
#define VL_FLAG_RESTART      0x20u
#define VL_DRIVER_MODE       0xA00650u     /* the mode byte: 0, gain (v + 0.5) x 1.3; else v x 0.7 */

enum { VL_MUSIC, VL_VOICE, VL_SFX, VL_KINDS };
static const char *const k_env[VL_KINDS] = {"SA2_MUSIC_VOLUME", "SA2_VOICE_VOLUME", "SA2_SFX_VOLUME"};
static const unsigned k_default[VL_KINDS] = {60u, 100u, 100u};

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

static unsigned g_percent[VL_KINDS] = {101u, 101u, 101u};   /* 101 until read */
static int g_said, g_stack_said, g_store_said, g_voice_said, g_driver_said;

/* A setting as a percent: digits only, 0 to 100; -1 when it is not that. */
static long vl_parse_percent(const char *s) {
    long v = 0;
    int n = 0;
    while (*s >= '0' && *s <= '9' && n < 4) v = v * 10 + (*s++ - '0'), n++;
    if (*s || n == 0 || v > 100) return -1;
    return v;
}

static unsigned vl_percent(int kind) {
    if (g_percent[kind] <= 100) return g_percent[kind];
    const char *v = getenv(k_env[kind]);
    g_percent[kind] = k_default[kind];
    if (v && *v) {
        const long p = vl_parse_percent(v);
        if (p < 0)
            fprintf(stderr, "sa2 tweaks: volume levels: %s=%s is not a whole number from 0 to 100, "
                    "so it is %u%%\n", k_env[kind], v, k_default[kind]);
        else g_percent[kind] = (unsigned)p;
    }
    return g_percent[kind];
}

static void vl_say(void) {
    if (g_said++) return;
    fprintf(stderr, "sa2 tweaks: volume levels: music %u%%, voices %u%%, sound effects %u%%\n",
            vl_percent(VL_MUSIC), vl_percent(VL_VOICE), vl_percent(VL_SFX));
}

/* The level, in tenths of a decibel (0 or below), for a percent from 0 to 100. Exposed for the unit
 * test (Documentation/scripts/tests/test_tweak_volume_levels.py). */
int32_t sa2_volume_level(unsigned percent) {
    return k_level[percent > 100 ? 100 : percent];
}

/* The game's music argument held to the range the body holds it to. */
static int32_t vl_held(int32_t level) {
    return level < VL_LEVEL_MIN ? VL_LEVEL_MIN : level > 0 ? 0 : level;
}

/* ------------------------------------------------------------------------------------------ music */

void *__real_sub_439490(void *state, uint32_t pc, void *memory);

__attribute__((used, retain))
void *__wrap_sub_439490(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_VOLUME_LEVELS)) return __real_sub_439490(state, pc, memory);
    vl_say();
    const uint32_t slot = sa2_rt_get_gpr(state, SA2_ESP) + VL_ARG_SLOT;
    int32_t arg = 0;
    if (sa2_peek(slot, 4, &arg) != SA2_OK) {
        if (!g_stack_said++)
            fprintf(stderr, "sa2 tweaks: volume levels: the guest stack at 0x%08X does not read, so "
                    "the music keeps the game's level\n", slot);
        return __real_sub_439490(state, pc, memory);
    }
    const int32_t held = vl_held(arg);
    const int32_t lowered = held + sa2_volume_level(vl_percent(VL_MUSIC));
    if (sa2_poke(slot, 4, &lowered) != SA2_OK) {
        if (!g_stack_said++)
            fprintf(stderr, "sa2 tweaks: volume levels: the guest stack at 0x%08X does not write, so "
                    "the music keeps the game's level\n", slot);
        return __real_sub_439490(state, pc, memory);
    }
    void *ret = __real_sub_439490(state, pc, memory);
    sa2_poke(slot, 4, &arg);
    uint32_t manager = 0, player = 0;
    if (sa2_peek(VL_MANAGER_PTR, 4, &manager) == SA2_OK && manager &&
        sa2_peek(manager + VL_PLAYER_OFFSET, 4, &player) == SA2_OK && player) {
        if (sa2_poke(manager + VL_LEVEL_OFFSET, 4, &held) != SA2_OK && !g_store_said++)
            fprintf(stderr, "sa2 tweaks: volume levels: the music level at 0x%08X does not write, so "
                    "a fade reads the lowered level\n", manager + VL_LEVEL_OFFSET);
    }
    return ret;
}

/* ----------------------------------------------------------------------------------------- voices */

static void vl_voice_write(void) {
    vl_say();
    const int32_t level = sa2_volume_level(vl_percent(VL_VOICE));
    if (sa2_poke(VL_VOICE_DWORD, 4, &level) != SA2_OK && !g_voice_said++)
        fprintf(stderr, "sa2 tweaks: volume levels: 0x%08X does not write, so the voices keep the "
                "game's level\n", VL_VOICE_DWORD);
}

void *__real_sub_442ad0(void *state, uint32_t pc, void *memory);
void *__real_sub_4425d0(void *state, uint32_t pc, void *memory);

__attribute__((used, retain))
void *__wrap_sub_442ad0(void *state, uint32_t pc, void *memory) {
    if (sa2_tweak_on(SA2_TWEAK_VOLUME_LEVELS)) vl_voice_write();
    return __real_sub_442ad0(state, pc, memory);
}

__attribute__((used, retain))
void *__wrap_sub_4425d0(void *state, uint32_t pc, void *memory) {
    if (sa2_tweak_on(SA2_TWEAK_VOLUME_LEVELS)) vl_voice_write();
    return __real_sub_4425d0(state, pc, memory);
}

/* ---------------------------------------------------------------------------------- sound effects */

/* The level a channel needs for the effect gain times g: in mode 0 the gain is (v + 0.5) x 1.3, so
 * g x (v + 0.5) - 0.5; in the other mode it is v x 0.7, so g x v. Exposed for the unit test. */
float sa2_volume_sfx_level(float v, float g, int mode) {
    return mode ? g * v : g * (v + 0.5f) - 0.5f;
}

void *__real_sub_77ee90(void *state, uint32_t pc, void *memory);

__attribute__((used, retain))
void *__wrap_sub_77ee90(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_VOLUME_LEVELS)) return __real_sub_77ee90(state, pc, memory);
    vl_say();
    const unsigned p = vl_percent(VL_SFX);
    uint32_t base = 0;
    uint8_t mode = 0;
    if (p == 100) return __real_sub_77ee90(state, pc, memory);
    if (sa2_peek(VL_DRIVER_PTR, 4, &base) != SA2_OK || !base ||
        sa2_peek(base + VL_DRIVER_MODE, 1, &mode) != SA2_OK) {
        if (base && !g_driver_said++)
            fprintf(stderr, "sa2 tweaks: volume levels: the sound driver at 0x%08X does not read, so "
                    "the sound effects keep the game's level\n", base);
        return __real_sub_77ee90(state, pc, memory);
    }
    const float g = (float)p / 100.0f;
    float saved[VL_CHANNELS];
    uint8_t saved_byte[VL_CHANNELS], changed[VL_CHANNELS];
    for (uint32_t i = 0; i < VL_CHANNELS; i++) {
        const uint32_t ch = base + i * VL_CHANNEL_STRIDE;
        uint8_t flags = 0;
        changed[i] = 0;
        if (sa2_peek(ch + VL_CHANNEL_FLAGS, 1, &flags) != SA2_OK) continue;
        if ((flags & VL_FLAG_LEVEL) && sa2_peek(ch + VL_CHANNEL_LEVEL, 4, &saved[i]) == SA2_OK) {
            const float v = sa2_volume_sfx_level(saved[i], g, mode);
            if (sa2_poke(ch + VL_CHANNEL_LEVEL, 4, &v) == SA2_OK) changed[i] |= 1;
        }
        if (mode && (flags & VL_FLAG_RESTART) && sa2_peek(ch + VL_CHANNEL_BYTE, 1, &saved_byte[i]) == SA2_OK) {
            const uint8_t b = (uint8_t)((unsigned)saved_byte[i] * p / 100u);
            if (sa2_poke(ch + VL_CHANNEL_BYTE, 1, &b) == SA2_OK) changed[i] |= 2;
        }
    }
    void *ret = __real_sub_77ee90(state, pc, memory);
    for (uint32_t i = 0; i < VL_CHANNELS; i++) {
        const uint32_t ch = base + i * VL_CHANNEL_STRIDE;
        if (changed[i] & 1) sa2_poke(ch + VL_CHANNEL_LEVEL, 4, &saved[i]);
        if (changed[i] & 2) sa2_poke(ch + VL_CHANNEL_BYTE, 1, &saved_byte[i]);
    }
    return ret;
}
