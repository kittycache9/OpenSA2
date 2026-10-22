/* Fabricated interfaces. See sa2_iface.h. */
#include "sa2_iface.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cpu_state.h"
#include "function_table.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_devprofile.h"
#include "sa2_dinput.h"
#include "sa2_trap.h"

/* The interface block sits below the runtime arena, and nothing else holds it
 * there: check_handle_regions.py keeps declared regions apart, but the arena is
 * not a declared region (the rule-5 review of the second fold, 2026-09-23). */
_Static_assert(SA2_IFACE_BASE + SA2_IFACE_MAX * SA2_IFACE_STRIDE <= SA2_GUEST_RT_ARENA_BASE,
               "the synthetic COM interfaces must not run into the runtime arena");

enum { CONV_THIS = 0, CONV_STD = 1 };
enum { POLICY_REFUSE = 0, POLICY_SURVEY = 1 };
enum { ANS_REFUSED  = SA2_IFACE_ANS_REFUSED,
       ANS_IMPL     = SA2_IFACE_ANS_IMPL,
       ANS_SURVEY   = SA2_IFACE_ANS_SURVEY,
       ANS_DECLINED = SA2_IFACE_ANS_DECLINED,
       ANS_RECORDED = SA2_IFACE_ANS_RECORDED,
       ANS_NONE     = SA2_IFACE_ANS_NONE,
       ANS_GUEST    = SA2_IFACE_ANS_GUEST,
       ANS_INVENT   = SA2_IFACE_ANS_INVENT };

static sa2_iface_stats g_st;
static sa2_iface_call  g_log[SA2_IFACE_LOG_MAX];
static uint32_t        g_log_used, g_seq;
static sa2_iface_slot_stat g_tally[SA2_IFACE_TALLY_MAX];
static uint32_t            g_tally_used;
static sa2_steam_name      g_names[SA2_STEAM_NAMES_MAX];
static uint32_t            g_names_used, g_names_dropped;
static uint64_t        g_steam_id = SA2_STEAM_ID_DEFAULT;
static int32_t         g_survey_budget = -1;   /* -1 = the survey is off */
/* The same switch for the Steam interfaces, which are POLICY_REFUSE because
 * design 3.5(c)'s "no-op stubs returning success" has no oracle for a *value*
 * and this runtime refuses to invent one in a shipping run.
 *
 * It exists for one job: a long run to measure determinism on. The interleaving
 * of guest threads is what is being measured, and a fabricated Steam answer is
 * the same fabrication in both runs, so it does not flatter the measurement --
 * whereas stopping at the first Steam call gives four frames to compare, which
 * is not much of a comparison. Off by default; every run that uses it says so
 * in its own JSON. */
static int32_t         g_steam_survey_budget = -1;
int sa2_iface_steam_survey(void) { return g_steam_survey_budget; }

typedef uint32_t (*iface_method)(void *s, uint32_t self, const uint32_t *argv);

typedef struct {
    uint8_t      slot;
    uint8_t      argc;        /* stack arguments, NOT counting a stdcall `this` */
    const char  *name;
    iface_method fn;
    uint8_t      ans;         /* SA2_IFACE_ANS_*: how this slot answers, declared */
    int8_t       name_arg;    /* index of the const char* argument, or -1 */
} iface_slot;

typedef struct {
    const char       *name;
    const iface_slot *slots;
    uint8_t           conv;
    uint8_t           policy;
    uint8_t           survey_argc;  /* stack args a surveyed slot records and pops */
    uint32_t          obj;          /* guest address, 0 until built */
    uint8_t           bound;
} iface_row;

static uint32_t gpoke32(uint32_t a, uint32_t v) {
    if (sa2_poke(a, 4, &v) != SA2_OK) {
        sa2_trap_note("iface: write of 4 bytes at guest %#010x is outside the guest space", a);
        sa2_trap_raise(SA2_TRAP_FAULT, a);
    }
    return 0;
}

/* --------------------------------------------------- the Steam methods */

/* The design's "no-op stubs returning success": ISteamApps::BIsSubscribed,
 * ::BIsSubscribedApp and ISteamUser::BLoggedOn. The app id the guest asks
 * about is recorded rather than compared -- this runtime is not a licence
 * check, and pretending to be one is the only way to get it wrong. */
static uint32_t m_true(void *s, uint32_t self, const uint32_t *a) {
    (void)s; (void)self; (void)a;
    return 1;
}

/* ISteamUtils::SetOverlayNotificationPosition. There is no overlay. */
static uint32_t m_void(void *s, uint32_t self, const uint32_t *a) {
    (void)s; (void)self; (void)a;
    return 0;
}

/* ISteamUser::GetSteamID returns a CSteamID by value, which MSVC compiles to a
 * hidden first stack argument holding the caller's buffer and a return of that
 * same pointer in EAX. The guest reads the low dword out of it at 0x0040a6bb,
 * so the value has to land in guest memory and the pointer has to come back. */
static uint32_t m_GetSteamID(void *s, uint32_t self, const uint32_t *a) {
    (void)s; (void)self;
    const uint32_t out = a[0];
    if (!out) {
        sa2_trap_note("ISteamUser::GetSteamID called with a null return buffer");
        sa2_trap_raise(SA2_TRAP_FAULT, 0);
    }
    gpoke32(out, (uint32_t)(g_steam_id & 0xffffffffu));
    gpoke32(out + 4u, (uint32_t)(g_steam_id >> 32));
    return out;
}

/* ISteamUtils::GetAppID(). The tick path asks for it at 0x0040e7a7. The
 * answer is not invented: it is the app id the guest itself named at
 * 0x0040a671 when it asked BIsSubscribedApp about its own app, recorded in
 * that call and handed back here. A runtime that made up an id could hand back
 * one the guest would then compare against its own and disagree with. When the
 * guest has not named one, there is no answer and the call refuses. */
static uint32_t g_app_id;
static uint32_t m_BIsSubscribedApp(void *s, uint32_t self, const uint32_t *a) {
    (void)s; (void)self;
    g_app_id = a[0];
    return 1;
}
static uint32_t m_GetAppID(void *s, uint32_t self, const uint32_t *a) {
    (void)s; (void)self; (void)a;
    if (!g_app_id) {
        sa2_trap_note("ISteamUtils::GetAppID: the guest has not named its own app id yet, so "
                      "there is nothing to hand back that it did not supply");
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_IFACE_THUNK_BASE);
    }
    return g_app_id;
}

static const iface_slot SLOTS_APPS[] = {
    {0, 0, "BIsSubscribed",    m_true,             ANS_IMPL,  -1},
    {6, 1, "BIsSubscribedApp", m_BIsSubscribedApp, ANS_IMPL,  -1},
    {0, 0, NULL, NULL, 0, 0},
};
static const iface_slot SLOTS_UTILS[] = {
    {9,  0, "GetAppID",                      m_GetAppID, ANS_GUEST, -1},
    {10, 1, "SetOverlayNotificationPosition", m_void,    ANS_IMPL,  -1},
    {0, 0, NULL, NULL, 0, 0},
};
static const iface_slot SLOTS_USER[] = {
    {1, 0, "BLoggedOn",  m_true,      ANS_IMPL,   -1},
    /* The one Steam value this runtime invents, and it is labelled `invented`
     * in every report so no reader can mistake it for the user's. It predates
     * this slice; it is here rather than fixed because the guest reads the low
     * dword of it at 0x0040a6bb and a refusal there would stop the run. */
    {2, 1, "GetSteamID", m_GetSteamID, ANS_INVENT, -1},
    {0, 0, NULL, NULL, 0, 0},
};
static const iface_slot SLOTS_NONE[] = {
    {0, 0, NULL, NULL, 0, 0},
};

/* --------------------------------------------- ISteamUserStats
 *
 * Design 3.5(c) says "no-op stubs returning success", and the user's decision
 * of 2026-09-21 is that achievements and stats are not worth implementing:
 * there is no oracle behind them and no parity value. This table is that
 * decision written down.
 *
 * The line it holds is between *declining to implement* and *lying*. Three
 * kinds of answer live here and they are not the same:
 *
 *  * **no-op success** (`RequestCurrentStats`, `StoreStats`). The call did
 *    nothing and says it succeeded. Nothing about the game's state is asserted
 *    by that, because neither call returns a value: one asks for a download
 *    that never arrives, the other offers an upload that goes nowhere. Note
 *    that `RequestCurrentStats` returning true promises a `UserStatsReceived_t`
 *    callback, and this runtime never delivers one -- counted, not hidden.
 *
 *  * **declined** (`GetStat`, `GetAchievement`, `GetAchievementAndUnlockTime`,
 *    the user variants). The guest asks *whether* a stat or achievement exists.
 *    The answer is "there is no such thing": return false and leave the guest's
 *    out-parameter exactly as the guest left it. That is Steam's own contract
 *    for an unknown name, so it is not an invention; writing `false` into
 *    `*pbAchieved` would be, because it asserts the achievement exists and is
 *    unearned. Every declined out-parameter is counted.
 *
 *  * **recorded** (`SetStat`, `SetAchievement`, `ClearAchievement`,
 *    `UpdateAvgRateStat`, `IndicateAchievementProgress`, `ResetAllStats`). The
 *    guest tells Steam something. This runtime writes it into its own log with
 *    the caller and the name, stores it nowhere, and returns success.
 *
 * Everything else **refuses**: the slots that return a pointer (an icon
 * handle, a display string, an achievement name) or a `SteamAPICall_t`. There
 * is no answer to those that is not an object this runtime invented, and a
 * fabricated call handle is worse than a refusal because it promises a call
 * result the guest will wait for forever. A refusal is a work item with an
 * address on it. `tools/s4/steam_surface.py` lists all eleven slots the image
 * can reach, so the ones left refusing are named rather than merely absent.
 *
 * The argument counts are load-bearing: a __thiscall COM method pops its own
 * arguments, so a count one dword short leaves the caller's frame drifting.
 * `steam_surface.py --argc` checks every one of them against the pushes the
 * image makes at the call site. */

/* Read a NUL-terminated guest string. Returns 0 and an empty buffer when the
 * pointer is not readable, rather than trapping: the name is a diagnostic, and
 * a diagnostic that can fault the run is a worse diagnostic than none. */
static int gstr(uint32_t va, char *buf, uint32_t n) {
    uint32_t i = 0;
    buf[0] = 0;
    if (!va) return 0;
    for (; i + 1 < n; i++) {
        uint8_t c = 0;
        if (sa2_peek(va + i, 1, &c) != SA2_OK) { buf[i] = 0; return 0; }
        buf[i] = (char)c;
        if (!c) return 1;
    }
    buf[i] = 0;
    return 1;
}

/* One row per distinct name, with the query and the write counted apart. The
 * table is bounded and what it drops is counted, because a table that silently
 * stops recording reports an absence that is its own. */
static void note_name(uint32_t va, uint32_t caller, int is_set) {
    char nm[64];
    if (!gstr(va, nm, sizeof nm) || !nm[0]) return;
    for (uint32_t i = 0; i < g_names_used; i++) {
        if (!strcmp(g_names[i].name, nm)) {
            if (is_set) g_names[i].sets++; else g_names[i].asks++;
            return;
        }
    }
    if (g_names_used >= SA2_STEAM_NAMES_MAX) { g_names_dropped++; return; }
    sa2_steam_name *r = &g_names[g_names_used++];
    memset(r, 0, sizeof *r);
    snprintf(r->name, sizeof r->name, "%s", nm);
    r->first_caller = caller;
    if (is_set) r->sets = 1; else r->asks = 1;
}

/* SA2_STEAM_BREAK=<name>. Each one moves exactly one clause of the probe's
 * verdict, and a break that moves none is a clause that was not a check.
 *
 *   refuse     ISteamUserStats answers nothing again, so the guest stops at
 *              the wall this slice removed.
 *   fabricate  the declining queries invent an answer: they return true and
 *              write into the guest's out-parameter. This is the thing the
 *              user's instruction forbids, so it has to be *observable* that
 *              it is not happening -- hence a sentinel the probe can watch.
 *   storefail  StoreStats reports failure, so the guest's dirty flag never
 *              clears.
 *   argcshort  every ISteamUserStats slot declares one dword less than it
 *              pops. This is the d3d9 defect that went unnoticed for six
 *              slices; here it moves a clause. */
static int g_break_fabricate, g_break_storefail;
static char g_break_name[24];

/* RequestCurrentStats(): accepted. Steam would answer with a
 * UserStatsReceived_t callback; this runtime delivers none, so the guest's
 * state machine advances on the return value alone, which is what it does at
 * 0x0040e8c5. */
static uint32_t m_stats_request(void *s, uint32_t self, const uint32_t *a) {
    (void)s; (void)self; (void)a;
    g_st.callbacks_never_delivered++;
    return 1;
}

/* StoreStats(): there is nothing to store and nowhere to store it. The guest
 * clears its own dirty flag at 0x0040e90d on a true. */
static uint32_t m_stats_store(void *s, uint32_t self, const uint32_t *a) {
    (void)s; (void)self; (void)a;
    return g_break_storefail ? 0u : 1u;
}

/* The declining queries. `a[0]` is the name; the out-parameters at a[1] (and
 * a[2]) are left exactly as the guest left them, which is what Steam does for
 * a name it does not know. */
static uint32_t decline(const uint32_t *out, uint32_t n) {
    if (!g_break_fabricate) {
        g_st.out_params_unwritten += n;
        return 0;                   /* "there is no such stat", and nothing written */
    }
    /* The break, and the whole point of it: invent an answer. */
    for (uint32_t k = 0; k < n; k++)
        if (out[k]) gpoke32(out[k], 0);
    g_st.methods_invented++;
    return 1;
}
static uint32_t m_stats_decline1(void *s, uint32_t self, const uint32_t *a) {
    (void)s; (void)self;
    return decline(a + 1, 1);
}
static uint32_t m_stats_decline2(void *s, uint32_t self, const uint32_t *a) {
    (void)s; (void)self;
    return decline(a + 1, 2);
}
/* The user-scoped variants take a CSteamID first (two dwords), so the name is
 * at a[2]. */
static uint32_t m_stats_decline_user(void *s, uint32_t self, const uint32_t *a) {
    (void)s; (void)self;
    return decline(a + 3, 1);
}
static uint32_t m_stats_decline_user2(void *s, uint32_t self, const uint32_t *a) {
    (void)s; (void)self;
    return decline(a + 3, 2);
}

/* The writes: stored nowhere, success. The name is recorded by iface_native
 * from the table's `name_arg`, in one place, so a slot cannot be added with a
 * name argument the log silently ignores. */
static uint32_t m_stats_set(void *s, uint32_t self, const uint32_t *a) {
    (void)s; (void)self; (void)a;
    return 1;
}
/* ResetAllStats(bool bAchievementsToo): no name to record. */
static uint32_t m_stats_reset(void *s, uint32_t self, const uint32_t *a) {
    (void)s; (void)self; (void)a;
    return 1;
}
/* The SteamAPICall_t methods. MSVC returns a 64-bit value in EDX:EAX, so EDX
 * has to be cleared explicitly: without it EDX still holds the method thunk
 * address this runtime put there, the guest's `or ecx,edx` at 0x00411960 sees
 * a non-zero high half, and it walks on with a call handle made of a pointer
 * into the runtime's own thunk page. Zero is k_uAPICallInvalid: no call was
 * made, so there is no handle. */
static uint32_t m_stats_no_call(void *s, uint32_t self, const uint32_t *a) {
    (void)self; (void)a;
    sa2_rt_set_gpr(s, SA2_EDX, 0);
    return 0;
}

/* Not const: `SA2_STEAM_BREAK` edits this table in place, which is how the
 * argument-count break exists at all.
 *
 * **The vtable order is this build's, and it is not the one the current
 * Steamworks headers document.** The first draft of this table used the modern
 * order, in which `GetNumAchievements` and `GetAchievementName` sit at 14 and
 * 15; this build's interface does not have them, and every slot from 14 up is
 * therefore two lower than the modern header says. The error was not caught by
 * reading -- it was caught by `tools/s4/steam_surface.py --argc`, which
 * compares the counts below against the dwords the image actually pushes at
 * each call site, and which reported
 *
 *     slot 20  declared 5 vs 3 pushed at 0x0041195c   DISAGREES
 *
 * for a slot the guest had already called once in a run that looked fine. Five
 * dwords popped where three were pushed destroys eight bytes of the caller's
 * frame, which is the d3d9 defect of slice 5 happening again, silently, in new
 * code. Four call sites settle the order independently:
 *
 *   0x0041195c  slot 20, three pushes (name, enum, enum), result used as a
 *               64-bit value in EDX:EAX  -> FindOrCreateLeaderboard
 *   0x004119b9  slot 22, two pushes (a 64-bit handle), result dereferenced as
 *               a char*                  -> GetLeaderboardName
 *   0x00410e7d  slot 23, two pushes (a 64-bit handle), result compared with
 *               `test eax,eax; jle`      -> GetLeaderboardEntryCount
 *   0x00410ba8  slot 26, five pushes, 64-bit result
 *                                        -> DownloadLeaderboardEntries
 *
 * Under the modern order those four would be FindOrCreateLeaderboard(3 args)
 * at 22, FindLeaderboard(1) at 23 and GetLeaderboardSortMethod(2) at 26, and
 * all four sites contradict it.
 *
 * A slot with a name but no function still **refuses**; the name and count are
 * there so the trap says which method it was and so the cross-check can
 * compare its count. */
static iface_slot SLOTS_USERSTATS[] = {
    { 0, 0, "RequestCurrentStats",            m_stats_request,      ANS_IMPL,     -1},
    { 1, 2, "GetStat(int32)",                 m_stats_decline1,     ANS_DECLINED,  0},
    { 2, 2, "GetStat(float)",                 m_stats_decline1,     ANS_DECLINED,  0},
    { 3, 2, "SetStat(int32)",                 m_stats_set,          ANS_RECORDED,  0},
    { 4, 2, "SetStat(float)",                 m_stats_set,          ANS_RECORDED,  0},
    { 5, 4, "UpdateAvgRateStat",              m_stats_set,          ANS_RECORDED,  0},
    { 6, 2, "GetAchievement",                 m_stats_decline1,     ANS_DECLINED,  0},
    { 7, 1, "SetAchievement",                 m_stats_set,          ANS_RECORDED,  0},
    { 8, 1, "ClearAchievement",               m_stats_set,          ANS_RECORDED,  0},
    { 9, 3, "GetAchievementAndUnlockTime",    m_stats_decline2,     ANS_DECLINED,  0},
    {10, 0, "StoreStats",                     m_stats_store,        ANS_IMPL,     -1},
    /* An icon is a handle into Steam's image list and a display attribute is a
     * string. There is no answer to either that this runtime would not have
     * invented, and "" would assert that the achievement exists and is blank,
     * so both refuse with their name on the trap. */
    {11, 1, "GetAchievementIcon",             NULL,                 ANS_REFUSED,  -1},
    {12, 2, "GetAchievementDisplayAttribute", NULL,                 ANS_REFUSED,  -1},
    {13, 3, "IndicateAchievementProgress",    m_stats_set,          ANS_RECORDED,  0},
    {14, 2, "RequestUserStats",               m_stats_no_call,      ANS_NONE,     -1},
    {15, 4, "GetUserStat(int32)",             m_stats_decline_user, ANS_DECLINED,  2},
    {16, 4, "GetUserStat(float)",             m_stats_decline_user, ANS_DECLINED,  2},
    {17, 4, "GetUserAchievement",             m_stats_decline_user, ANS_DECLINED,  2},
    {18, 5, "GetUserAchievementAndUnlockTime", m_stats_decline_user2, ANS_DECLINED, 2},
    {19, 1, "ResetAllStats",                  m_stats_reset,        ANS_RECORDED, -1},
    /* The asynchronous half. Every one of these returns a SteamAPICall_t in
     * EDX:EAX, and every one of them here returns k_uAPICallInvalid -- which
     * is not a fabrication but the literal truth: this runtime issued no Steam
     * API call, so there is no call handle. The guest's own code reads it that
     * way: at 0x0041195e it does `mov ecx,eax; or ecx,edx; je` and skips the
     * leaderboard it was about to register. Handing back a *non-zero* handle
     * would be the fabrication, because it promises a call result that never
     * arrives and the guest would wait for it forever. */
    {20, 3, "FindOrCreateLeaderboard",        m_stats_no_call,      ANS_NONE,      0},
    {21, 1, "FindLeaderboard",                m_stats_no_call,      ANS_NONE,      0},
    /* No leaderboard exists, so no handle exists, so nothing can reach these
     * with a real one. They refuse rather than invent a name, a row count, a
     * sort method or a downloaded entry. */
    {22, 2, "GetLeaderboardName",             NULL,                 ANS_REFUSED,  -1},
    {23, 2, "GetLeaderboardEntryCount",       NULL,                 ANS_REFUSED,  -1},
    {24, 2, "GetLeaderboardSortMethod",       NULL,                 ANS_REFUSED,  -1},
    {25, 2, "GetLeaderboardDisplayType",      NULL,                 ANS_REFUSED,  -1},
    {26, 5, "DownloadLeaderboardEntries",     m_stats_no_call,      ANS_NONE,     -1},
    {27, 4, "DownloadLeaderboardEntriesForUsers", m_stats_no_call,  ANS_NONE,     -1},
    {28, 6, "GetDownloadedLeaderboardEntry",  NULL,                 ANS_REFUSED,  -1},
    {29, 6, "UploadLeaderboardScore",         m_stats_no_call,      ANS_NONE,     -1},
    {30, 4, "AttachLeaderboardUGC",           m_stats_no_call,      ANS_NONE,     -1},
    {31, 0, "GetNumberOfCurrentPlayers",      m_stats_no_call,      ANS_NONE,     -1},
    {0, 0, NULL, NULL, 0, 0},
};

/* IDirect3D9's vtable, named so the survey log reads as method names rather
 * than as slot numbers. The order is the published one; none of them is
 * implemented here, because this is the survey and not a device. The device
 * is `runtime/sa2_d3d9.c`.
 *
 * **Three of these argument counts were wrong until 2026-09-21**, and what
 * they cost is not a wrong answer but a drifting frame. A `__stdcall` COM
 * method pops `this` plus its arguments; a count one dword short leaves four
 * bytes of the caller's own arguments on the caller's stack, and every later
 * `[esp+n]` in that function then reads the wrong local. The survey called
 * `CheckDeviceMultiSampleType` -- six arguments, recorded as five -- twice
 * before reaching `CreateDevice`, so by then the frame was eight bytes out and
 * the `DeviceType` the S4 slice-5 journal records for that call, `0x02801b38`,
 * is a stack pointer read from the wrong slot rather than `D3DDEVTYPE_HAL`.
 * Nothing noticed, because a survey that answers 0 to everything walks on
 * either way. `tools/s4/d3d9_break.py --break argc` puts the short counts back
 * and shows the drift returning. */
static const iface_slot SLOTS_D3D9[] = {
    {0,  2, "QueryInterface",            NULL},
    {1,  0, "AddRef",                    NULL},
    {2,  0, "Release",                   NULL},
    {3,  1, "RegisterSoftwareDevice",    NULL},
    {4,  0, "GetAdapterCount",           NULL},
    {5,  3, "GetAdapterIdentifier",      NULL},
    {6,  2, "GetAdapterModeCount",       NULL},
    {7,  4, "EnumAdapterModes",          NULL},
    {8,  2, "GetAdapterDisplayMode",     NULL},
    {9,  5, "CheckDeviceType",           NULL},
    {10, 6, "CheckDeviceFormat",         NULL},
    {11, 6, "CheckDeviceMultiSampleType", NULL},
    {12, 5, "CheckDepthStencilMatch",    NULL},
    {13, 4, "CheckDeviceFormatConversion", NULL},
    {14, 3, "GetDeviceCaps",             NULL},
    {15, 1, "GetAdapterMonitor",         NULL},
    {16, 6, "CreateDevice",              NULL},
    {0, 0, NULL, NULL},
};

static iface_row g_ifaces[] = {
    {"ISteamApps",      SLOTS_APPS,  CONV_THIS, POLICY_REFUSE, 0, 0, 0},
    {"ISteamUser",      SLOTS_USER,  CONV_THIS, POLICY_REFUSE, 0, 0, 0},
    {"ISteamUtils",     SLOTS_UTILS, CONV_THIS, POLICY_REFUSE, 0, 0, 0},
    {"ISteamFriends",   SLOTS_NONE,  CONV_THIS, POLICY_REFUSE, 0, 0, 0},
    {"ISteamUserStats", SLOTS_USERSTATS, CONV_THIS, POLICY_REFUSE, 0, 0, 0},
    /* stdcall with `this` first, and 8 stack arguments assumed for a slot the
     * table does not name: a survey that popped too few would derail the
     * caller, and popping too many is visible as a stack that walks up. */
    {"IDirect3D9",      SLOTS_D3D9,  CONV_STD,  POLICY_SURVEY, 8, 0, 0},
};
#define IFACE_COUNT ((uint32_t)(sizeof g_ifaces / sizeof g_ifaces[0]))

static const iface_slot *slot_of(const iface_row *it, uint32_t slot) {
    for (const iface_slot *p = it->slots; p->name; p++)
        if (p->slot == slot) return p;
    return NULL;
}

static void note(const iface_row *it, const iface_slot *sl, uint32_t slot, uint32_t self,
                 uint32_t caller, const uint32_t *argv, uint32_t argc, uint32_t ret,
                 uint8_t answered) {
    g_seq++;
    g_st.method_calls++;
    switch (answered) {
    case ANS_IMPL:     g_st.methods_implemented++; break;
    case ANS_GUEST:    g_st.methods_implemented++; break;
    case ANS_NONE:     g_st.methods_implemented++; break;
    case ANS_SURVEY:   g_st.methods_surveyed++;    break;
    case ANS_DECLINED: g_st.methods_declined++;    break;
    case ANS_RECORDED: g_st.methods_recorded++;    break;
    case ANS_INVENT:   g_st.methods_invented++;    break;
    default:           g_st.methods_refused++;     break;
    }
    /* The tally first, because it is the thing that must not degrade when the
     * log fills. */
    {
        sa2_iface_slot_stat *t = NULL;
        for (uint32_t i = 0; i < g_tally_used; i++)
            if (g_tally[i].slot == slot && !strcmp(g_tally[i].iface, it->name)) {
                t = &g_tally[i];
                break;
            }
        if (!t && g_tally_used < SA2_IFACE_TALLY_MAX) {
            t = &g_tally[g_tally_used++];
            memset(t, 0, sizeof *t);
            snprintf(t->iface, sizeof t->iface, "%s", it->name);
            if (sl) snprintf(t->method, sizeof t->method, "%s", sl->name);
            else    snprintf(t->method, sizeof t->method, "vtable[%u]", slot);
            t->slot = slot;
            t->first_seq = g_seq;
            t->first_caller = caller;
            t->answered = answered;
            t->argc = sl ? (int8_t)sl->argc : (int8_t)-1;
        }
        if (t) { t->calls++; t->last_caller = caller; }
    }
    if (g_log_used >= SA2_IFACE_LOG_MAX) return;
    sa2_iface_call *c = &g_log[g_log_used++];
    memset(c, 0, sizeof *c);
    c->seq = g_seq;
    c->caller = caller;
    c->self = self;
    c->slot = (uint16_t)slot;
    c->answered = answered;
    c->argc = (uint8_t)(argc > 6u ? 6u : argc);
    for (uint32_t k = 0; k < c->argc; k++) c->arg[k] = argv[k];
    c->ret = ret;
    snprintf(c->iface, sizeof c->iface, "%s", it->name);
    if (sl) snprintf(c->name, sizeof c->name, "%s", sl->name);
    else    snprintf(c->name, sizeof c->name, "vtable[%u]", slot);
}

/* ------------------------------------------------------- the native */

static void *iface_native(void *s, uint32_t pc, void *m) {
    const uint32_t idx = (pc - SA2_IFACE_THUNK_BASE) / SA2_IFACE_THUNK_STRIDE;
    const uint32_t i = idx / SA2_IFACE_SLOTS, slot = idx % SA2_IFACE_SLOTS;
    if (i >= IFACE_COUNT) {
        sa2_trap_note("interface thunk %#010x is past the %u fabricated interfaces",
                      pc, IFACE_COUNT);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, pc);
    }
    iface_row *it = &g_ifaces[i];
    const uint32_t esp = sa2_rt_get_gpr(s, SA2_ESP);
    uint32_t caller = 0;
    if (sa2_peek(esp, 4, &caller) != SA2_OK) caller = 0;

    const iface_slot *sl = slot_of(it, slot);
    /* A stdcall COM method takes `this` as its first stack argument; a
     * thiscall one takes it in ECX. Read the arguments the same way either
     * way, then report `self` from whichever place holds it. */
    const uint32_t stack0 = esp + 4u;
    uint32_t argc = sl ? sl->argc : it->survey_argc;
    if (it->conv == CONV_STD) argc += 1u;            /* the `this` slot */
    if (argc > 12u) argc = 12u;
    uint32_t raw[12];
    memset(raw, 0, sizeof raw);
    for (uint32_t k = 0; k < argc; k++) {
        if (sa2_peek(stack0 + 4u * k, 4, &raw[k]) != SA2_OK) {
            sa2_trap_note("%s::%s: argument %u at %#010x is outside the guest space",
                          it->name, sl ? sl->name : "?", k, stack0 + 4u * k);
            sa2_trap_raise(SA2_TRAP_FAULT, stack0 + 4u * k);
        }
    }
    const uint32_t self = (it->conv == CONV_STD) ? raw[0] : sa2_rt_get_gpr(s, SA2_ECX);
    const uint32_t *argv = (it->conv == CONV_STD) ? raw + 1 : raw;
    const uint32_t nargs = (it->conv == CONV_STD) ? argc - 1u : argc;

    uint32_t ret = 0;
    uint8_t answered;
    if (sl && sl->fn) {
        ret = sl->fn(s, self, argv);
        /* The answer class is declared in the table, not inferred from the
         * return value: "returned 0" and "answered that there is no such stat"
         * are the same dword and different facts, and a report that could not
         * tell them apart is the fabrication this slice exists to avoid. */
        answered = sl->ans ? sl->ans : (uint8_t)ANS_IMPL;
        /* The stat, achievement or leaderboard name the guest named, read
         * through the guest's own pointer. Recorded here and not in the method
         * bodies so that every slot declaring a `name_arg` is recorded, and a
         * write is told apart from a query by the declared answer class rather
         * than by which function happened to remember to call this. */
        if (sl->name_arg >= 0 && (uint32_t)sl->name_arg < nargs)
            note_name(argv[sl->name_arg], caller, answered == ANS_RECORDED);
    } else if (it->policy == POLICY_SURVEY && g_survey_budget > 0) {
        g_survey_budget--;
        answered = ANS_SURVEY;
        ret = 0;                       /* S_OK, and no out-parameter written */
    } else {
        note(it, sl, slot, self, caller, argv, nargs, 0, ANS_REFUSED);
        sa2_trap_note("%s::%s called from %#010x on %#010x: this runtime fabricates the "
                      "interface and has no answer for that method%s",
                      it->name, sl ? sl->name : "an unnamed slot", caller, self,
                      it->policy == POLICY_SURVEY
                          ? " (the survey budget is spent; raise SA2_D3D9_SURVEY)" : "");
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, pc);
    }
    note(it, sl, slot, self, caller, argv, nargs, ret, answered);
    sa2_rt_set_gpr(s, SA2_EAX, ret);
    sa2_rt_set_gpr(s, SA2_ESP, esp + 4u + 4u * argc);   /* the callee pops, both conventions */
    return m;
}

/* --------------------------------------------------------- building */

/* Registration is per interface and happens when that interface is first
 * built, not at bind time. An interface nothing asks for should cost no table
 * rows -- and, load-bearing: `harness/crt_probe.c` asserts that the function
 * table grew by *exactly* the import count across `sa2_imports_bind`. Slipping
 * hundreds of natives into that call would have turned a working assertion
 * into a failing one, and the fix would have been to relax the assertion,
 * which is how a check stops being one. */
static int iface_bind(uint32_t i) {
    iface_row *it = &g_ifaces[i];
    if (it->bound) return 0;
    for (uint32_t k = 0; k < SA2_IFACE_SLOTS; k++) {
        const uint32_t va = SA2_IFACE_THUNK_BASE +
                            (i * SA2_IFACE_SLOTS + k) * SA2_IFACE_THUNK_STRIDE;
        if (sa2_ft_add(va, iface_native, "iface-method", SA2_FN_NATIVE) < 0) return -1;
    }
    it->bound = 1;
    return 0;
}

static uint32_t iface_build(uint32_t i) {
    iface_row *it = &g_ifaces[i];
    if (it->obj) return it->obj;
    if (iface_bind(i) < 0) {
        sa2_trap_note("the function table refused the method thunks for %s", it->name);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_IFACE_THUNK_BASE);
    }
    const uint32_t obj = SA2_IFACE_BASE + i * SA2_IFACE_STRIDE;
    const uint32_t vt = obj + SA2_IFACE_VTABLE_OFF;
    gpoke32(obj, vt);
    for (uint32_t k = 0; k < SA2_IFACE_SLOTS; k++)
        gpoke32(vt + 4u * k, SA2_IFACE_THUNK_BASE +
                             (i * SA2_IFACE_SLOTS + k) * SA2_IFACE_THUNK_STRIDE);
    it->obj = obj;
    g_st.interfaces_built++;
    return obj;
}

uint32_t sa2_iface_get(const char *name) {
    for (uint32_t i = 0; i < IFACE_COUNT; i++)
        if (!strcmp(g_ifaces[i].name, name)) return iface_build(i);
    return 0;
}

/* ------------------------------------------------ the flat entry points */

uint32_t sa2_steam_api(const char *name, void *state, const uint32_t *argv, uint32_t caller) {
    (void)state; (void)argv; (void)caller;
    if (!strcmp(name, "SteamAPI_Init")) { g_st.init_calls++; return 1; }
    if (!strcmp(name, "SteamAPI_Shutdown"))     { g_st.shutdown_calls++; return 0; }
    if (!strcmp(name, "SteamAPI_RunCallbacks")) { g_st.runcallbacks_calls++; return 0; }
    if (!strcmp(name, "SteamAPI_RegisterCallback"))     { g_st.register_callback++;
                                                          g_st.callbacks_never_delivered++;
                                                          return 0; }
    if (!strcmp(name, "SteamAPI_UnregisterCallback"))   { g_st.unregister_callback++; return 0; }
    if (!strcmp(name, "SteamAPI_RegisterCallResult"))   { g_st.register_callresult++;
                                                          g_st.callbacks_never_delivered++;
                                                          return 0; }
    if (!strcmp(name, "SteamAPI_UnregisterCallResult")) { g_st.unregister_callresult++; return 0; }
    if (!strcmp(name, "SteamApps"))      return sa2_iface_get("ISteamApps");
    if (!strcmp(name, "SteamUser"))      return sa2_iface_get("ISteamUser");
    if (!strcmp(name, "SteamUtils"))     return sa2_iface_get("ISteamUtils");
    if (!strcmp(name, "SteamFriends"))   return sa2_iface_get("ISteamFriends");
    if (!strcmp(name, "SteamUserStats")) return sa2_iface_get("ISteamUserStats");
    return 0;
}

/* ------------------------------------------- SteamMetricsSDK!MetricsAgent
 *
 * See `sa2_iface.h` for why these are no-ops and why two of them being
 * `__thiscall` needs no new calling convention.
 *
 * **The token was 0x03180000 and that was wrong.** That address is inside
 * `SA2_GUEST_SCRATCH` (0x03000000..0x03D00000), so `sa2_gp` accepts it: a
 * guest reading a field through the returned pointer would have got zeroed
 * scratch back, silently, which is the exact outcome the header says the
 * token prevents. It is now above `SA2_GUEST_END`, in the handle space with
 * every other value this runtime invents for the guest to hold and never
 * dereference, and `Documentation/handle-regions.json` declares it so nothing
 * else can land on it. Caught by review on 2026-09-21, before it shipped. */
#define SA2_METRICS_AGENT_TOKEN  0x7c000000u

uint32_t sa2_steam_metrics_agent(void) { return SA2_METRICS_AGENT_TOKEN; }

uint32_t sa2_steam_metrics(const char *decorated, const uint32_t *argv, uint32_t caller) {
    (void)argv; (void)caller;
    /* Matched on the decorated name because that is what the import table
     * holds; undecorating here would mean carrying a demangler to compare two
     * strings. The substring is the method name between '?' and '@'. */
    if (strstr(decorated, "?GetInstance@MetricsAgent@")) {
        g_st.metrics_getinstance++;
        return SA2_METRICS_AGENT_TOKEN;
    }
    if (strstr(decorated, "?Initialize@MetricsAgent@"))    { g_st.metrics_initialize++;   return 0; }
    if (strstr(decorated, "?LogEvent@MetricsAgent@"))      { g_st.metrics_logevent++;     return 0; }
    if (strstr(decorated, "?CleanInstance@MetricsAgent@")) { g_st.metrics_cleaninstance++; return 0; }
    sa2_trap_note("SteamMetricsSDK!%s is not one of the four MetricsAgent entry "
                  "points this runtime stands in for; the import table grew a name "
                  "nobody classified", decorated);
    sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, caller);
    return 0;
}

/* ------------------------------------------------------------------ XInput */

#define XINPUT_ORD_GETSTATE  2u
#define XINPUT_ORD_SETSTATE  3u
#define XINPUT_ORD_GETCAPS   4u

/* winerror.h. 1167 is what XInput returns for a slot with no pad in it, and
 * the game has to handle it whatever this runtime does, because a player can
 * unplug a controller. */
#define SA2_ERROR_DEVICE_NOT_CONNECTED 1167u

static int g_xinput_pad;        /* SA2_XINPUT_PAD=1 fabricates a pad at rest */

int sa2_xinput_pad_present(void) { return g_xinput_pad; }

/* Sizes of the structures the caller supplies, from the XInput 1.3 headers:
 *
 *   XINPUT_STATE        = DWORD dwPacketNumber + XINPUT_GAMEPAD      = 4 + 12
 *   XINPUT_GAMEPAD      = WORD buttons, BYTE lt, BYTE rt, 4 SHORTs   = 12
 *   XINPUT_VIBRATION    = WORD left, WORD right                      = 4
 *   XINPUT_CAPABILITIES = BYTE type, BYTE sub, WORD flags,
 *                         XINPUT_GAMEPAD, XINPUT_VIBRATION           = 4 + 12 + 4
 *
 * They are written as byte counts rather than as structs because the caller's
 * buffer is guest memory and the adapter, not this module, owns writing it. */
#define XINPUT_STATE_BYTES  16u
#define XINPUT_CAPS_BYTES   20u

uint32_t sa2_xinput(uint32_t ordinal, const uint32_t *argv, uint32_t caller,
                    uint32_t *out_bytes, uint32_t *out_len) {
    *out_len = 0;
    switch (ordinal) {
    case XINPUT_ORD_GETSTATE: g_st.xinput_getstate++; *out_len = XINPUT_STATE_BYTES; break;
    case XINPUT_ORD_SETSTATE: g_st.xinput_setstate++; *out_len = 0;                  break;
    case XINPUT_ORD_GETCAPS:  g_st.xinput_getcaps++;  *out_len = XINPUT_CAPS_BYTES;  break;
    default:
        sa2_trap_note("XINPUT1_3!Ordinal#%u called from %#010x: this runtime stands "
                      "in for ordinals 2, 3 and 4 only", ordinal, caller);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_IFACE_THUNK_BASE);
        return 0;
    }
    /* dwUserIndex is the first argument of all three. The image asks about
     * slots 0 to 3 only. The GetCapabilities thunk (007c0cce) has five callers.
     * Two are the scans (00864e29, 00864a27), which count the slot up from 0
     * and leave at 4 (0x00864EEB, 0x00864A70). Two are pad-object methods
     * (00863d51, 00863df1; vtable 008fffe0 slots 7 and 5), which pass the
     * slot the scan stored in the object when it built it. The fifth
     * (00863d2d) is in a function nothing references.
     * tools/s4/dinput_surface.py pins all five as the claim
     * xinput-scans-slots-0-to-3. GetState and SetState are reached only
     * through the pad object a 0 answer builds. XInput would answer a slot
     * past 3 ERROR_BAD_ARGUMENTS; this refuses it by name, because a call the
     * image cannot make means the decode was wrong. */
    if (argv[0] >= 4u) {
        sa2_trap_note("XINPUT1_3!Ordinal#%u called from %#010x for slot %u: the image scans "
                      "slots 0 to 3 only (0x00864EEB, 0x00864A70)", ordinal, caller, argv[0]);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_IFACE_THUNK_BASE);
        return 0;
    }
    /* The buffer is zeroed on both paths, so no caller ever reads its own
     * stack back out of an out-parameter this runtime declined to fill. On the
     * absent path zero is not an answer, it is the absence of one, and the
     * status says so; on the present path zero *is* the answer -- a pad with
     * every control at rest -- except for the capability type and subtype,
     * which have to name a device or the pad is present and is nothing. */
    for (uint32_t i = 0; i < *out_len && i < 32u; i++) out_bytes[i] = 0;
    /* A pad the live source holds connected (runtime/sa2_dinput.h, "The host
     * gamepads"): answered as XInput 1.3 answers for a wired Xbox 360 pad.
     * In a replay only from a pad script (SA2_INPUT_PAD_SCRIPT, "Gamepad
     * replay"), which answers as the session's source did; never otherwise. */
    sa2_di_pad pad;
    const int live = sa2_dinput_live_pad(argv[0], caller, &pad);
    if (live) {
        /* 2: a pad at rest standing in for one not connected yet (runtime/sa2_dinput.h,
         * "Pads that connect later"), counted there and not here, so `pad_live`, and the
         * run record's "live-pads", still say a real pad answered. */
        if (live == 1) g_st.pad_live++;
        if (ordinal == XINPUT_ORD_GETCAPS) {
            /* XINPUT_CAPABILITIES: what the pad can report, not what it holds.
             * The image tests only the status (0x00864E2E, 0x00864A2C, 0x00863D58,
             * 0x00863DF8) and reads no byte of it. */
            static const uint8_t caps[XINPUT_CAPS_BYTES] = {
                1, 1,               /* XINPUT_DEVTYPE_GAMEPAD, XINPUT_DEVSUBTYPE_GAMEPAD */
                0x04, 0x00,         /* Flags: XINPUT_CAPS_VOICE_SUPPORTED, the headset port */
                0xFF, 0xF3,         /* Gamepad.wButtons: every button XInputGetState reports */
                0xFF, 0xFF,         /* bLeftTrigger, bRightTrigger: 8 bits each */
                0xC0, 0xFF, 0xC0, 0xFF, 0xC0, 0xFF, 0xC0, 0xFF,   /* sThumb*: 10 bits each */
                0xFF, 0x00, 0xFF, 0x00,                           /* both motors */
            };
            for (uint32_t i = 0; i < XINPUT_CAPS_BYTES; i++) out_bytes[i] = caps[i];
        } else if (ordinal == XINPUT_ORD_GETSTATE) {
            /* XINPUT_STATE: dwPacketNumber, then XINPUT_GAMEPAD. */
            const uint32_t v[XINPUT_STATE_BYTES] = {
                pad.packet & 0xFFu, (pad.packet >> 8) & 0xFFu, (pad.packet >> 16) & 0xFFu,
                pad.packet >> 24,
                pad.buttons & 0xFFu, (uint32_t)pad.buttons >> 8, pad.lt, pad.rt,
                (uint16_t)pad.lx & 0xFFu, (uint16_t)pad.lx >> 8,
                (uint16_t)pad.ly & 0xFFu, (uint16_t)pad.ly >> 8,
                (uint16_t)pad.rx & 0xFFu, (uint16_t)pad.rx >> 8,
                (uint16_t)pad.ry & 0xFFu, (uint16_t)pad.ry >> 8,
            };
            for (uint32_t i = 0; i < XINPUT_STATE_BYTES; i++) out_bytes[i] = v[i];
        }
        /* XInputSetState: the vibration is accepted and has nowhere to go. */
        return 0;           /* ERROR_SUCCESS */
    }
    if (!g_xinput_pad) {
        /* The slot's answer as the capture recorded it (runtime/sa2_devprofile.h,
         * sa2_dp_xinput_caps_hr): ERROR_DEVICE_NOT_CONNECTED, which XInput gives
         * a slot with no pad for all three calls, in every profile a reader
         * accepts -- the default's too -- because a reader refuses a profile
         * that recorded a pad. Any other answer means the reader and this stub
         * disagree. */
        const uint32_t st = sa2_dp_xinput_caps_hr(argv[0]);
        if (st != SA2_ERROR_DEVICE_NOT_CONNECTED) {
            sa2_trap_note("XINPUT1_3!Ordinal#%u from %#010x: device profile %s answers %u for "
                          "slot %u, and a profile reader accepts only 1167",
                          ordinal, caller, sa2_devprofile_source(), st, argv[0]);
            sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_IFACE_THUNK_BASE);
        }
        g_st.pad_absent++;
        return st;
    }
    g_st.pad_present++;
    if (ordinal == XINPUT_ORD_GETCAPS) {
        out_bytes[0] = 1;   /* XINPUT_DEVTYPE_GAMEPAD */
        out_bytes[1] = 1;   /* XINPUT_DEVSUBTYPE_GAMEPAD */
    }
    return 0;               /* ERROR_SUCCESS */
}

int sa2_d3d9_survey_enabled(void) { return g_survey_budget >= 0; }

uint32_t sa2_d3d9_create(void *state, uint32_t sdk_version, uint32_t caller) {
    (void)state;
    g_st.d3d9_creates++;
    if (!sa2_d3d9_survey_enabled()) {
        sa2_trap_note("d3d9.dll!Direct3DCreate9(SDKVersion %u) called from %#010x: building a "
                      "device is S5's work. Set SA2_D3D9_SURVEY=<n> to fabricate a recording "
                      "IDirect3D9 that answers nothing and logs the first n methods asked of it.",
                      sdk_version, caller);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_IFACE_THUNK_BASE);
    }
    return sa2_iface_get("IDirect3D9");
}

void sa2_iface_config_from_env(void) {
    const char *v = getenv("SA2_STEAM_ID");
    if (v && *v) g_steam_id = strtoull(v, NULL, 0);
    v = getenv("SA2_D3D9_SURVEY");
    if (v && *v) g_survey_budget = (int32_t)strtol(v, NULL, 0);
    v = getenv("SA2_STEAM_SURVEY");
    if (v && *v) {
        g_steam_survey_budget = (int32_t)strtol(v, NULL, 0);
        for (uint32_t i = 0; i < IFACE_COUNT; i++) {
            if (g_ifaces[i].name[0] != 'I' || g_ifaces[i].name[1] != 'S') continue;
            g_ifaces[i].policy = POLICY_SURVEY;
            g_ifaces[i].survey_argc = 0;   /* thiscall: `mov ecx,eax; call eax` pushes nothing */
        }
        if (g_survey_budget < g_steam_survey_budget) g_survey_budget = g_steam_survey_budget;
    }
    v = getenv("SA2_XINPUT_PAD");
    /* The fabricated pad is the falsifier for runs on the default profile. A
     * capture's replay answers every slot from that capture's profile, and a
     * pad beside it would be a device the capture never had: refused, whatever
     * the value, since the replay tools strip the variable
     * (tools/s4/device_profile.py, clean_environ) and one that arrives anyway
     * came from a shell. sa2_imports_config_from_env reads the profile before
     * this runs. */
    if (v && *v && !sa2_devprofile_is_default()) {
        sa2_trap_note("SA2_XINPUT_PAD is set and device profile %s is a capture's: the "
                      "fabricated pad is for runs on the default profile, and a capture's "
                      "replay answers every slot from its profile", sa2_devprofile_source());
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_IFACE_THUNK_BASE);
    }
    if (v && *v) g_xinput_pad = (int)strtol(v, NULL, 0);
    v = getenv("SA2_STEAM_BREAK");
    if (v && *v) {
        if (!strcmp(v, "refuse")) {
            for (uint32_t i = 0; i < IFACE_COUNT; i++)
                if (!strcmp(g_ifaces[i].name, "ISteamUserStats"))
                    g_ifaces[i].slots = SLOTS_NONE;
        } else if (!strcmp(v, "fabricate")) {
            g_break_fabricate = 1;
        } else if (!strcmp(v, "storefail")) {
            g_break_storefail = 1;
        } else if (!strcmp(v, "argcshort")) {
            for (iface_slot *p = SLOTS_USERSTATS; p->name; p++)
                if (p->argc) p->argc--;
        } else {
            sa2_trap_note("SA2_STEAM_BREAK=%s is not a break this runtime knows; the "
                          "breaks are refuse, fabricate, storefail, argcshort", v);
            sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_IFACE_THUNK_BASE);
        }
        snprintf(g_break_name, sizeof g_break_name, "%s", v);
    }
    g_st.steam_id_lo = (uint32_t)(g_steam_id & 0xffffffffu);
    g_st.steam_id_hi = (uint32_t)(g_steam_id >> 32);
    g_st.d3d9_survey_depth = g_survey_budget > 0 ? (uint32_t)g_survey_budget : 0u;
}

const char *sa2_steam_break_name(void) { return g_break_name[0] ? g_break_name : ""; }
uint32_t sa2_steam_names_dropped(void) { return g_names_dropped; }

void sa2_iface_stats_get(sa2_iface_stats *out) {
    if (!out) return;
    g_st.steam_id_lo = (uint32_t)(g_steam_id & 0xffffffffu);
    g_st.steam_id_hi = (uint32_t)(g_steam_id >> 32);
    *out = g_st;
}
uint32_t              sa2_iface_log_count(void) { return g_log_used; }
const sa2_iface_call *sa2_iface_log_at(uint32_t i) { return i < g_log_used ? &g_log[i] : NULL; }

uint32_t sa2_iface_slot_count(void) { return g_tally_used; }
const sa2_iface_slot_stat *sa2_iface_slot_at(uint32_t i) {
    return i < g_tally_used ? &g_tally[i] : NULL;
}
uint32_t sa2_steam_name_count(void) { return g_names_used; }
const sa2_steam_name *sa2_steam_name_at(uint32_t i) {
    return i < g_names_used ? &g_names[i] : NULL;
}

static const iface_slot *declared(const char *iface, uint32_t slot) {
    for (uint32_t i = 0; i < IFACE_COUNT; i++) {
        if (strcmp(g_ifaces[i].name, iface)) continue;
        return slot_of(&g_ifaces[i], slot);
    }
    return NULL;
}
int sa2_iface_declared_argc(const char *iface, uint32_t slot) {
    const iface_slot *sl = declared(iface, slot);
    return (sl && sl->name) ? (int)sl->argc : -1;
}
const char *sa2_iface_declared_name(const char *iface, uint32_t slot) {
    const iface_slot *sl = declared(iface, slot);
    return (sl && sl->name) ? sl->name : NULL;
}
