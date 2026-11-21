/* Play's text language: the game's config answered with the language the player chose.
 *
 * Subtitle: one guest function, linked in every build with `-Wl,-wrap=sub_406520`
 * (tools/s4/link_s4.py PRODUCTION_WRAPS): the config's Language reader. In play,
 * SA2_TEXT_LANGUAGE replaces the number it returns, so the game shows its menus, messages and
 * subtitles in that language. Unset or empty, the wrap only calls the lifted body.
 *
 * Why (GitHub issue #61): the Steam release installs the text of all six languages (Message/,
 * MessageK/, the event subtitles), and the language is the Language attribute of
 * Config/UserConfig.cfg, which the PC launcher writes and the installer writes as English. The
 * Android app has no launcher to change it, and the desktop's game.env is easier to edit than a
 * config file the installer owns. Voices are another matter: Steam has them in Japanese and
 * English only, the save keeps the choice, and the game's Options menu sets it.
 *
 * The function, read from the unpacked image:
 *
 *   0x00406520  opens \Config\UserConfig.cfg (0x0089E264) again, finds the Language attribute
 *               (0x0089E160) and reads it with sscanf("%d") (0x007A67ED, format 0x008AFBAC) into a
 *               local it never initialised, which it returns in EAX (0x0040662B). When the file
 *               cannot be read it prints "Failure to load the User config file!" (0x0089E27C)
 *               and returns 1 (0x004065C4); when the attribute is missing it returns the local as
 *               it found it.
 *   0x00401C58  its call in WinMain, which switches on EAX (`cmp eax,5; ja`, the table at
 *               0x00401E78) and sets TextLanguage (byte 0x0174AFD1), VoiceLanguage (byte
 *               0x0174AFD2) and the byte at 0x0174B008:
 *
 *                 config   language    TextLanguage  VoiceLanguage  0x0174B008
 *                 0        English     1             1              1
 *                 1        German      4             1              2
 *                 2        Spanish     3             1              2
 *                 3        French      2             1              2
 *                 4        Italian     5             1              2
 *                 5        Japanese    0             0              0
 *                 other    English     1             1              1   (unsigned: >5 or <0)
 *
 *   0x00401636  its call in the quit prompt (0x004015F0), which picks the prompt's strings from
 *               the same number (`cmp eax,5; ja`). So the prompt follows the setting as well.
 *
 * So the wrap calls the lifted body (its file handling, its message and its return of 1 stay as
 * they are) and then writes the chosen language's config number to EAX, before either caller
 * reads it. A save may still set its own text language: 0x00446690, which unpacks a save's
 * buffer, copies its bytes at 0x01DEC609 and 0x01DEC60A to TextLanguage and VoiceLanguage
 * (0x004466F1, 0x004466FE). When that runs, and whether it wins over this setting in play, has
 * not been checked at runtime.
 *
 * The setting (read at each call, said once):
 *   SA2_TEXT_LANGUAGE=en|de|es|fr|it|ja   config 0, 1, 2, 3, 4 or 5 (the table above), any case.
 *   unset or empty                        nothing changes and nothing is printed: the switch is off.
 * Any other value changes nothing, with one line that says so. Ignored when SA2_REPLAY_TICKS or
 * SA2_REPLAY_WORDS is set (harness/replay/wrap_tick_probe.c): a registered replay keeps the
 * config's language. tools/s4/device_profile.py OWNED strips it from every replay's environment
 * besides.
 *
 * When it is set the run prints one line to stderr, at the first call: "text language: Japanese
 * (SA2_TEXT_LANGUAGE=ja): the config's Language 0 -> 5", or why nothing changed.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#include "cpu_state.h"
#include "remill_runtime.h"

void *__real_sub_406520(void *state, uint32_t pc, void *memory);

static const struct {
    char code[3];
    const char *name;
} TL_LANGUAGES[] = {   /* in config order: index i is the config's Language i */
    {"en", "English"}, {"de", "German"}, {"es", "Spanish"},
    {"fr", "French"},  {"it", "Italian"}, {"ja", "Japanese"},
};
#define TL_COUNT (int)(sizeof TL_LANGUAGES / sizeof TL_LANGUAGES[0])

static char tl_lower(char c) { return c >= 'A' && c <= 'Z' ? (char)(c - 'A' + 'a') : c; }

/* What SA2_TEXT_LANGUAGE asks of a config whose Language read as `read`: 1 with *config the number
 * to return, 0 for nothing. `line` gets the run's one line, or "" when the setting is unset or
 * empty. Exposed for the unit test (Documentation/scripts/tests/test_text_language.py). */
int sa2_text_language_answer(const char *value, int replay, int32_t read, int32_t *config,
                             char *line, size_t n) {
    line[0] = 0;
    if (!value || !*value) return 0;
    if (replay) {
        snprintf(line, n, "text language: SA2_TEXT_LANGUAGE=%s ignored in a replay "
                 "(SA2_REPLAY_TICKS or SA2_REPLAY_WORDS is set); the config's Language is left as "
                 "read", value);
        return 0;
    }
    for (int i = 0; i < TL_COUNT; i++) {
        if (tl_lower(value[0]) == TL_LANGUAGES[i].code[0] &&
            tl_lower(value[1]) == TL_LANGUAGES[i].code[1] && value[1] && !value[2]) {
            snprintf(line, n, "text language: %s (SA2_TEXT_LANGUAGE=%s): the config's Language "
                     "%d -> %d", TL_LANGUAGES[i].name, value, (int)read, i);
            *config = i;
            return 1;
        }
    }
    snprintf(line, n, "text language: SA2_TEXT_LANGUAGE=%s is not one of en, de, es, fr, it, ja; "
             "the config's Language %d is kept", value, (int)read);
    return 0;
}

__attribute__((used, retain))
void *__wrap_sub_406520(void *state, uint32_t pc, void *memory) {
    static int said;
    void *ret = __real_sub_406520(state, pc, memory);
    const char *value = getenv("SA2_TEXT_LANGUAGE");
    if (!(value && *value)) return ret;                         /* the switch off */
    const int replay = getenv("SA2_REPLAY_TICKS") != NULL || getenv("SA2_REPLAY_WORDS") != NULL;
    int32_t config = 0;
    char line[256];
    if (sa2_text_language_answer(value, replay, (int32_t)sa2_rt_get_gpr(state, SA2_EAX), &config,
                                 line, sizeof line))
        sa2_rt_set_gpr(state, SA2_EAX, (uint32_t)config);
    if (line[0] && !said++) fprintf(stderr, "%s\n", line);
    return ret;
}
