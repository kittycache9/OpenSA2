/* A recording DirectInput 8 device. See sa2_dinput.h. */
#include "sa2_dinput.h"

#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#if defined(_WIN32)
#include "sa2_win32.h"     /* getline, O_NONBLOCK, fcntl, and the named-pipe source */
#endif

#include "cpu_state.h"
#include "function_table.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_devprofile.h"
#include "sa2_guest_call.h"
#include "sa2_trap.h"
#include "sa2_window.h"

/* ------------------------------------------------------------ the rows */

enum {
    DF_OUT = 1u << 0,   /* writes through a pointer the guest dereferences */
    DF_CB  = 1u << 1,   /* calls back into the guest */
};

typedef struct di_ctx di_ctx;
typedef uint32_t (*di_method)(di_ctx *c);

typedef struct {
    uint8_t     slot;
    uint8_t     argc;        /* stack dwords, NOT counting `this` */
    uint8_t     flags;
    const char *name;
    di_method   fn;          /* NULL: named, and refused by name */
} di_row;

typedef struct {
    const char *name;
    di_row     *rows;
    uint32_t    vt;          /* the vtable's guest address, 0 until built */
} di_type;

enum { T_DI8 = 0, T_DIDEV = 1 };

typedef struct {
    uint32_t addr;
    uint16_t type;
    uint16_t alive;
    uint32_t refs;
    uint32_t dev;            /* index into g_dev, for T_DIDEV; 0xFFFFFFFF otherwise */
} di_obj;

struct di_ctx {
    void           *s;
    uint32_t        pc;
    uint32_t        self;
    di_obj         *obj;
    sa2_di_device  *d;       /* the device record, when `self` is a device */
    const uint32_t *a;
    uint32_t        caller;
    uint8_t         answered;
    uint32_t        out0, out1;
};

/* --------------------------------------------------------------- state */

_Static_assert(SA2_DI_THUNK_BASE >= 0x00221000u && SA2_DI_THUNK_END <= 0x00300000u,
               "the dinput thunks must sit above sa2_dsound.h's and below 0x00300000");
_Static_assert(SA2_DI_THUNK_BASE + SA2_DI_MAX_TYPES * SA2_DI_MAX_SLOTS * SA2_DI_THUNK_STRIDE
               == SA2_DI_THUNK_END, "SA2_DI_THUNK_END is the thunk block's own product");
_Static_assert(SA2_DI_OBJ_BASE >= 0x03260400u,
               "the dinput objects must sit above sa2_dsound.h's vtables");
_Static_assert(SA2_DI_OBJ_BASE + SA2_DI_MAX_OBJECTS * SA2_DI_OBJ_STRIDE <= SA2_DI_SCRATCH_BASE,
               "the dinput objects must not run into the enumeration scratch");
_Static_assert(SA2_DI_SCRATCH_BASE + SA2_DI_SCRATCH_BYTES <= SA2_DI_SCRATCH_END,
               "the enumeration scratch must end where its reserved block does, which is where "
               "runtime/sa2_wbem.h's objects start");
_Static_assert(SA2_DI_SCRATCH_END <= SA2_DI_VT_BASE,
               "the enumeration scratch's block must not run into the dinput vtables");
_Static_assert(SA2_DI_VT_BASE + SA2_DI_MAX_TYPES * SA2_DI_VT_STRIDE <= SA2_DI_VT_END,
               "the dinput vtables must fit their declared block");
_Static_assert(SA2_DI_VT_END <= SA2_GUEST_RT_ARENA_BASE,
               "the dinput vtables must not run into the runtime arena");
_Static_assert(0x244u <= SA2_DI_SCRATCH_BYTES,
               "one DIDEVICEINSTANCEA must fit the enumeration scratch");
_Static_assert(SA2_DP_MAX_GAMECTRL <= SA2_DI_MAX_ENUM,
               "every controller a profile reader accepts must be one this file enumerates");
_Static_assert(3u + SA2_DI_MAX_ENUM <= SA2_DI_MAX_OBJECTS,
               "the object pool must hold the IDirectInput8A, the keyboard, the mouse "
               "and one device per enumerated controller");

/* Deliberate breaks, so every claim this file makes can be shown failing.
 * `SA2_DINPUT_BREAK=<name>`; harness/dinput_probe.c runs each one.
 *
 *   argc        IDirectInput8A::CreateDevice is declared with two arguments
 *               instead of three. The dispatcher pops the declared count, so
 *               the caller's frame drifts four bytes: the surface check
 *               disagrees, the probe's ESP check fails, and the keyboard init
 *               at 0x00863783 reads the wrong local.
 *   bare-ok     DirectInput8Create answers DI_OK and writes nothing. The
 *               pointer, not the HRESULT, gates the device setup (0x00864D42
 *               cmp [ebx],0 after 0x00864C75 zeroed it), so this must behave
 *               exactly as the E_FAIL refusal did. Refused under a capture's
 *               profile, as the device off is.
 *   noout       every method with an out-parameter answers DI_OK and writes
 *               nothing: CreateDevice leaves the keyboard's +0x10 at 0, and
 *               0x00863793 reads guest address 0.
 *   nodevice    CreateDevice answers DIERR_DEVICENOTREG and writes NULL for
 *               both system GUIDs, whatever the profile says: the game makes
 *               the same allocations as with devices (0x00864D50, 0x00864DA0)
 *               and polls nothing. Refused under a capture's profile, whose
 *               recorded system devices it would replace.
 *   notacquired GetDeviceState answers DIERR_NOTACQUIRED, native's answer
 *               while the window is not in the foreground: the game zero-fills
 *               its buffer and calls Acquire on every poll.
 *   inputlost   Acquire and GetDeviceState answer DIERR_INPUTLOST. The game
 *               spins on it, so after SA2_DI_INPUTLOST_SPIN answers in a row
 *               the run stops naming the spinning address.
 *   allkeys     every keyboard state byte is 0x80. The replay comparison must
 *               see it, or it cannot see the keyboard at all.
 *   mousemove   every mouse state reads lX=+5, lY=+3, the buttons still up.
 *               The mouse wrapper (vtable 0x00900068) reads its two
 *               DIMOUSESTATE buffers (+0x14 and +0x24, filled at 0x008633A1)
 *               in two ways: the buttons, rgbButtons at +0x20+16*buffer, in
 *               slots 8 to 12 (0x00863160, 0x00863110, 0x008630C0, 0x00708E00
 *               through slot 12, 0x00863090); and the axes in slots 13 to 15
 *               (0x00862FE0 lX, 0x00862FD0 lY, 0x00862FC0 lZ, the Mod Loader's
 *               GetLX, GetLY and GetLZ). Nothing in the image calls the three
 *               axis slots directly, and none of the calls through +0x34 or
 *               +0x38 in the input code (0x00420000..0x0042FFFF,
 *               0x0077D000..0x0077FFFF, 0x00860000..0x00865FFF) is on a mouse.
 *               This break tests the rest of the image: it must change nothing
 *               compared, and a difference is a caller of GetLX or GetLY that
 *               the decode missed.
 */
enum { BRK_NONE = 0, BRK_ARGC, BRK_BARE_OK, BRK_NOOUT, BRK_NODEVICE, BRK_NOTACQUIRED,
       BRK_INPUTLOST, BRK_ALLKEYS, BRK_MOUSEMOVE };
static const char *const BREAK_NAMES[] = {
    "", "argc", "bare-ok", "noout", "nodevice", "notacquired", "inputlost", "allkeys",
    "mousemove",
};
static int g_break;

static sa2_di_stats     g_st;
static sa2_di_call      g_log[SA2_DI_LOG_MAX];
static uint32_t         g_log_used, g_seq, g_depth;
static sa2_di_slot_stat g_slot[SA2_DI_MAX_TYPES][SA2_DI_MAX_SLOTS];
static di_obj           g_obj[SA2_DI_MAX_OBJECTS];
static uint32_t         g_obj_used;
static sa2_di_device    g_dev[SA2_DI_MAX_OBJECTS];
static uint32_t         g_dev_used;
static sa2_di_enum      g_enum[8];
static uint32_t         g_enum_used;
static int              g_enabled, g_types_bound;

/* ---------------------------------------------- the image's own constants
 *
 * Each is read from the image by tools/s4/dinput_surface.py --claims, which
 * fails if one moves. */
#define IMG_ENUM_CALLBACK   0x00864C90u   /* pushed at 0x00864E0E */
#define DI8_VERSION         0x00000800u   /* pushed at 0x00864D1F */
#define DI8CLASS_GAMECTRL   4u            /* pushed at 0x00864E13 */
#define DIEDFL_ATTACHEDONLY 1u            /* pushed at 0x00864E0B */
#define DISCL_NONEXCL_FG    6u            /* pushed at 0x008637AB and 0x0086322F */
#define DIENUM_STOP         0u

/* {BF798030-483A-4DA2-AA99-5D64ED369700}, the 16 bytes at 0x0087355C. */
static const uint8_t IID_DI8A[16] = {0x30,0x80,0x79,0xBF,0x3A,0x48,0xA2,0x4D,
                                     0xAA,0x99,0x5D,0x64,0xED,0x36,0x97,0x00};
/* {6F1D2B61-D5A0-11CF-BFC7-444553540000}, at 0x0087360C. */
static const uint8_t GUID_KEYBOARD[16] = {0x61,0x2B,0x1D,0x6F,0xA0,0xD5,0xCF,0x11,
                                          0xBF,0xC7,0x44,0x45,0x53,0x54,0x00,0x00};
/* {6F1D2B60-D5A0-11CF-BFC7-444553540000}, at 0x008735FC. */
static const uint8_t GUID_MOUSE[16] = {0x60,0x2B,0x1D,0x6F,0xA0,0xD5,0xCF,0x11,
                                       0xBF,0xC7,0x44,0x45,0x53,0x54,0x00,0x00};

/* The two DIDATAFORMATs the image passes: c_dfDIKeyboard at 0x00875D6C and
 * c_dfDIMouse at 0x00875F74, as dinput-trace-v1a:312 and :859 print them. */
typedef struct { uint32_t kind, data_size, objects; const char *what; } di_format;
static const di_format FORMATS[] = {
    {SA2_DI_KIND_KEYBOARD, 0x100u, 256u, "c_dfDIKeyboard (0x00875D6C)"},
    {SA2_DI_KIND_MOUSE,    0x10u,  7u,   "c_dfDIMouse (0x00875F74)"},
};

/* ------------------------------------------------------------ plumbing */

static void gfault(uint32_t a, const char *what) {
    sa2_trap_note("dinput: %s at guest %#010x is outside the guest space", what, a);
    sa2_trap_raise(SA2_TRAP_FAULT, a);
}
static void pk32(uint32_t a, uint32_t v) {
    if (!a) gfault(a, "a write through a null out-parameter");
    if (sa2_poke(a, 4, &v) != SA2_OK) gfault(a, "a write of 4 bytes");
}
static void pk16(uint32_t a, uint16_t v) {
    if (sa2_poke(a, 2, &v) != SA2_OK) gfault(a, "a write of 2 bytes");
}
static void pkn(uint32_t a, const void *p, uint32_t n) {
    if (sa2_poke(a, n, p) != SA2_OK) gfault(a, "a write");
}
static void pen(uint32_t a, void *p, uint32_t n) {
    if (sa2_peek(a, n, p) != SA2_OK) gfault(a, "a read");
}

static void guid_text(const uint8_t *g, char *out, size_t cap) {
    snprintf(out, cap, "{%02X%02X%02X%02X-%02X%02X-%02X%02X-%02X%02X-%02X%02X%02X%02X%02X%02X}",
             g[3], g[2], g[1], g[0], g[5], g[4], g[7], g[6],
             g[8], g[9], g[10], g[11], g[12], g[13], g[14], g[15]);
}

static void refuse(const di_ctx *c, const char *iface, const char *method, const char *why) {
    sa2_trap_note("%s::%s called from %#010x: %s", iface, method, c->caller, why);
    sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, c->pc);
}

/* --------------------------------------------------------- the objects */

static uint32_t type_vtable(uint32_t t);

static di_obj *obj_of(uint32_t addr) {
    if (addr < SA2_DI_OBJ_BASE) return NULL;
    const uint32_t i = (addr - SA2_DI_OBJ_BASE) / SA2_DI_OBJ_STRIDE;
    if (i >= g_obj_used || g_obj[i].addr != addr) return NULL;
    return &g_obj[i];
}

static di_obj *obj_new(uint32_t t, uint32_t pc) {
    if (g_obj_used >= SA2_DI_MAX_OBJECTS) {
        sa2_trap_note("dinput: the object pool of %u is exhausted; the image creates one "
                      "IDirectInput8A, a keyboard, a mouse and one device per enumerated "
                      "controller, and this run asked for more", SA2_DI_MAX_OBJECTS);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, pc);
    }
    di_obj *o = &g_obj[g_obj_used];
    memset(o, 0, sizeof *o);
    o->addr = SA2_DI_OBJ_BASE + g_obj_used * SA2_DI_OBJ_STRIDE;
    o->type = (uint16_t)t;
    o->alive = 1;
    o->refs = 1;
    o->dev = 0xFFFFFFFFu;
    g_obj_used++;
    g_st.objects_built++;
    /* A vtable pointer, then this runtime's tag, so a stray pointer found later
     * in guest memory is identifiable by its bytes alone. */
    pk32(o->addr, type_vtable(t));
    pk32(o->addr + 4u, 0x504E4944u);          /* 'DINP' */
    pk32(o->addr + 8u, t);
    pk32(o->addr + 12u, g_obj_used - 1u);
    return o;
}

static sa2_di_device *dev_new(di_ctx *c, uint32_t kind, uint32_t profile_index,
                              const uint8_t *guid, uint32_t hr) {
    di_obj *o = obj_new(T_DIDEV, c->pc);
    sa2_di_device *d = &g_dev[g_dev_used];
    memset(d, 0, sizeof *d);
    d->addr = o->addr;
    d->kind = kind;
    d->profile_index = profile_index;
    memcpy(d->guid, guid, 16);
    d->create_caller = c->caller;
    d->create_hr = hr;
    d->refs = 1;
    o->dev = g_dev_used++;
    return d;
}

/* The enumerated device the profile's controller `i` names, by instance GUID. */
static int profile_instance(const uint8_t *g) {
    const uint32_t n = sa2_dp_gamectrl_count();
    for (uint32_t i = 0; i < n; i++) {
        const sa2_dp_didevice *p = sa2_dp_gamectrl(i);
        if (p && !memcmp(p->guid_instance, g, 16)) return (int)i;
    }
    return -1;
}

/* -------------------------------------------- IDirectInput8A's methods */

static uint32_t m_CreateDevice(di_ctx *c) {
    const uint32_t rguid = c->a[0], out = c->a[1], outer = c->a[2];
    if (outer)
        refuse(c, "IDirectInput8A", "CreateDevice",
               "pUnkOuter is not 0, and the image's three sites push 0 (0x0086375C, "
               "0x008631EC, 0x00864C9F)");
    if (!out)
        refuse(c, "IDirectInput8A", "CreateDevice",
               "the out-pointer is NULL, and every site passes a device field or the "
               "callback's own pvRef slot (0x0086376F, 0x008631F9, 0x00864CA1)");
    uint8_t g[16];
    pen(rguid, g, 16);
    uint32_t kind = 0, hr = SA2_DI_OK;
    int idx = -1;
    if (!memcmp(g, GUID_KEYBOARD, 16)) {
        kind = SA2_DI_KIND_KEYBOARD;
        hr = sa2_dp_syskeyboard_hr();
    } else if (!memcmp(g, GUID_MOUSE, 16)) {
        kind = SA2_DI_KIND_MOUSE;
        hr = sa2_dp_sysmouse_hr();
    } else if ((idx = profile_instance(g)) >= 0) {
        kind = SA2_DI_KIND_ENUMERATED;
        hr = sa2_dp_gamectrl_create_hr((uint32_t)idx);
    } else {
        char t[48], why[200];
        guid_text(g, t, sizeof t);
        snprintf(why, sizeof why, "%s is neither system GUID (0x0087360C, 0x008735FC) nor "
                 "an instance GUID of the %u controllers profile %s lists", t,
                 sa2_dp_gamectrl_count(), sa2_devprofile_source());
        refuse(c, "IDirectInput8A", "CreateDevice", why);
    }
    if (kind != SA2_DI_KIND_ENUMERATED && g_break == BRK_NODEVICE) {
        pk32(out, 0);
        c->answered = SA2_DI_ANS_BREAK;
        return SA2_DIERR_DEVICENOTREG;
    }
    /* The profile's recorded HRESULT. Native writes NULL on failure, and the
     * game then clears the device's valid byte (0x00863787 for the keyboard,
     * 0x0086320E for the mouse) or, for an enumerated controller, skips the WMI
     * query and returns 1 (0x00864CB2 jl 0x00864CF0). */
    if ((int32_t)hr < 0) {
        pk32(out, 0);
        c->answered = SA2_DI_ANS_PROFILE;
        return hr;
    }
    sa2_di_device *d = dev_new(c, kind, idx >= 0 ? (uint32_t)idx : 0xFFFFFFFFu, g, hr);
    pk32(out, d->addr);
    c->out0 = d->addr;
    c->answered = SA2_DI_ANS_OBJECT;
    return hr;
}

/* The DIDEVICEINSTANCEA native's EnumDevices hands the callback, built from the
 * profile's own fields: dwSize 0x244, guidInstance +4, guidProduct +0x14,
 * dwDevType +0x24, tszInstanceName +0x28, tszProductName +0x12C, guidFFDriver
 * +0x230, wUsagePage +0x240, wUsage +0x242. The callback reads +4 and +0x14
 * only (0x00864CA6, 0x00864CB4); every field is written anyway, because a
 * structure half-filled is a guess about which half is read. */
static void write_instance(uint32_t at, const sa2_dp_didevice *p) {
    if (sa2_guest_fill(at, 0, 0x244u) != SA2_OK) gfault(at, "a DIDEVICEINSTANCEA write");
    pk32(at + 0x000u, 0x244u);
    pkn(at + 0x004u, p->guid_instance, 16);
    pkn(at + 0x014u, p->guid_product, 16);
    pk32(at + 0x024u, p->dev_type);
    char name[260];
    memcpy(name, p->instance_name, sizeof name);
    name[259] = 0;
    pkn(at + 0x028u, name, 260);
    memcpy(name, p->product_name, sizeof name);
    name[259] = 0;
    pkn(at + 0x12Cu, name, 260);
    pkn(at + 0x230u, p->guid_ffdriver, 16);
    pk16(at + 0x240u, p->usage_page);
    pk16(at + 0x242u, p->usage);
}

static sa2_di_call      *g_cur;
static sa2_di_slot_stat *g_cur_slot;

static uint32_t m_EnumDevices(di_ctx *c) {
    const uint32_t type = c->a[0], cb = c->a[1], ref = c->a[2], flags = c->a[3];
    if (type != DI8CLASS_GAMECTRL || flags != DIEDFL_ATTACHEDONLY) {
        char why[160];
        snprintf(why, sizeof why, "class %u flags %#x; the one site (0x00864E19) asks for "
                 "DI8DEVCLASS_GAMECTRL (4) with DIEDFL_ATTACHEDONLY (1)", type, flags);
        refuse(c, "IDirectInput8A", "EnumDevices", why);
    }
    if (cb != IMG_ENUM_CALLBACK)
        refuse(c, "IDirectInput8A", "EnumDevices",
               "the callback is not 0x00864C90, the immediate the one site pushes at "
               "0x00864E0E");
    sa2_di_enum *e = g_enum_used < sizeof g_enum / sizeof g_enum[0] ? &g_enum[g_enum_used++]
                                                                       : NULL;
    if (e) {
        memset(e, 0, sizeof *e);
        e->caller = c->caller;
        e->dev_type = type;
        e->flags = flags;
        e->callback = cb;
        e->ref = ref;
    }
    g_st.enumerations++;
    const uint32_t n = sa2_dp_gamectrl_count();
    /* The callback runs lifted code that calls this file again (CreateDevice
     * at 0x00864CAE, Unacquire and Release at 0x00864CCE and 0x00864CDA), so
     * this call's own log record is saved and restored around it. */
    sa2_di_call *const my_cur = g_cur;
    sa2_di_slot_stat *const my_slot = g_cur_slot;
    uint32_t offered = 0;
    for (uint32_t i = 0; i < n; i++) {
        const sa2_dp_didevice *p = sa2_dp_gamectrl(i);
        if (!p) {
            sa2_trap_note("IDirectInput8A::EnumDevices: profile %s names %u controllers and "
                          "has no record for index %u", sa2_devprofile_source(), n, i);
            sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, c->pc);
        }
        write_instance(SA2_DI_SCRATCH_BASE, p);
        const uint32_t argv[2] = {SA2_DI_SCRATCH_BASE, ref};
        g_depth++;
        const uint32_t r = sa2_guest_stdcall(c->s, cb, 2, argv);
        g_depth--;
        g_cur = my_cur;
        g_cur_slot = my_slot;
        g_st.devices_enumerated++;
        offered++;
        if (e) { e->offered++; e->last_callback_ret = r; }
        /* Wine stops on DIENUM_STOP and on nothing else. */
        if (r == DIENUM_STOP) { if (e) e->stopped = 1; break; }
    }
    c->out0 = offered;
    c->out1 = n;
    c->answered = SA2_DI_ANS_PROFILE;
    return SA2_DI_OK;
}

/* ------------------------------------- IDirectInputDevice8A's methods */

static const char *JOYSTICK_UNREACHED =
    "only the joystick class calls this on an enumerated device, and its "
    "constructor 0x00862B50 is reached only when 0x00864980's second argument is 0; "
    "the one call, 0x00864CEB, pushes 1 at 0x00864CE6";

static const di_format *format_of(uint32_t kind) {
    for (uint32_t i = 0; i < sizeof FORMATS / sizeof FORMATS[0]; i++)
        if (FORMATS[i].kind == kind) return &FORMATS[i];
    return NULL;
}

static uint32_t m_SetDataFormat(di_ctx *c) {
    sa2_di_device *d = c->d;
    const di_format *want = format_of(d->kind);
    if (!want) refuse(c, "IDirectInputDevice8A", "SetDataFormat", JOYSTICK_UNREACHED);
    const uint32_t p = c->a[0];
    uint32_t f[6];
    pen(p, f, sizeof f);
    if (f[0] != 0x18u || f[1] != 0x10u || f[2] != 2u || f[3] != want->data_size
        || f[4] != want->objects) {
        char why[200];
        snprintf(why, sizeof why, "DIDATAFORMAT at %#010x is {dwSize %#x, dwObjSize %#x, "
                 "dwFlags %#x, dwDataSize %#x, dwNumObjs %u}, not %s", p, f[0], f[1], f[2],
                 f[3], f[4], want->what);
        refuse(c, "IDirectInputDevice8A", "SetDataFormat", why);
    }
    d->set_format_calls++;
    if (d->acquired) {
        c->answered = SA2_DI_ANS_INVALID;
        return SA2_DIERR_ACQUIRED;
    }
    d->format_ptr = p;
    d->format_size = f[0];
    d->format_obj_size = f[1];
    d->format_flags = f[2];
    d->format_data_size = f[3];
    d->format_objects = f[4];
    d->format_rgodf = f[5];
    c->answered = SA2_DI_ANS_GUEST;
    return SA2_DI_OK;
}

static uint32_t m_SetCooperativeLevel(di_ctx *c) {
    sa2_di_device *d = c->d;
    if (d->kind == SA2_DI_KIND_ENUMERATED)
        refuse(c, "IDirectInputDevice8A", "SetCooperativeLevel", JOYSTICK_UNREACHED);
    const uint32_t hwnd = c->a[0], flags = c->a[1];
    if (flags != DISCL_NONEXCL_FG) {
        char why[160];
        snprintf(why, sizeof why, "flags %#x; both sites push 6, DISCL_NONEXCLUSIVE | "
                 "DISCL_FOREGROUND (0x008637AB, 0x0086322F)", flags);
        refuse(c, "IDirectInputDevice8A", "SetCooperativeLevel", why);
    }
    d->coop_calls++;
    /* Wine's order: the window test, then the acquired test. A handle that is
     * no window this runtime made gets native's E_HANDLE, not a trap: the call
     * is the image's and the handle is the host's to vouch for. */
    if (!sa2_window_of(hwnd)) {
        c->answered = SA2_DI_ANS_INVALID;
        return SA2_DI_E_HANDLE;
    }
    if (d->acquired) {
        c->answered = SA2_DI_ANS_INVALID;
        return SA2_DIERR_ACQUIRED;
    }
    d->coop_hwnd = hwnd;
    d->coop_flags = flags;
    d->coop_hwnd_is_a_window = 1;
    c->answered = SA2_DI_ANS_GUEST;
    return SA2_DI_OK;
}

/* A DIERR_INPUTLOST answer under the `inputlost` break, counted per device so
 * the spin it causes stops the run where it happens. */
static uint32_t input_lost(di_ctx *c, const char *method) {
    sa2_di_device *d = c->d;
    if (++d->inputlost_run >= SA2_DI_INPUTLOST_SPIN) {
        sa2_trap_note("IDirectInputDevice8A::%s: %u DIERR_INPUTLOST answers in a row, the "
                      "last to %#010x. The guest spins on that code (0x008638A0..0x008638B0, "
                      "0x008633C0..0x008633D0), which is why the device never gives it",
                      method, d->inputlost_run, c->caller);
        /* The thunk, not the caller: an exit address inside the image reads as
         * code to lift to the trap fixpoint, and the caller is in the note. */
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, c->pc);
    }
    c->answered = SA2_DI_ANS_BREAK;
    return SA2_DIERR_INPUTLOST;
}

static uint32_t m_Acquire(di_ctx *c) {
    sa2_di_device *d = c->d;
    if (d->kind == SA2_DI_KIND_ENUMERATED)
        refuse(c, "IDirectInputDevice8A", "Acquire", JOYSTICK_UNREACHED);
    d->acquire_calls++;
    if (g_break == BRK_INPUTLOST) return input_lost(c, "Acquire");
    d->inputlost_run = 0;
    c->answered = SA2_DI_ANS_NOOP;
    if (d->acquired) return SA2_DI_NOEFFECT;
    if (!d->format_data_size) {
        c->answered = SA2_DI_ANS_INVALID;
        return SA2_DIERR_INVALIDPARAM;
    }
    /* DISCL_FOREGROUND ties native acquisition to focus. The capture's window
     * had it: two Acquire calls in the whole of dinput-trace-v1a (:828, :877)
     * against 1204 GetDeviceState calls means no poll ever fell back to the
     * Acquire at 0x00863894 or 0x008633B0. `notacquired` models losing it. */
    d->acquired = 1;
    return SA2_DI_OK;
}

static uint32_t m_Unacquire(di_ctx *c) {
    sa2_di_device *d = c->d;
    d->unacquire_calls++;
    c->answered = SA2_DI_ANS_NOOP;
    if (!d->acquired) return SA2_DI_NOEFFECT;
    d->acquired = 0;
    return SA2_DI_OK;
}

/* ---------------------------------------------------- the host keyboard
 *
 * sa2_dinput.h, "The host keyboard". Nothing here runs unless
 * SA2_INPUT_DI_SCRIPT or SA2_INPUT_DI_SOURCE is set (g_in.kind): with neither,
 * m_GetDeviceState never calls in, and the configuration only reads three
 * unset variables. Integers only: the answer is bytes. */

enum { IN_NONE = 0, IN_SCRIPT = 1, IN_FIFO = 2 };

/* The FIFO's read buffer: several records of either kind, so one read takes a
 * burst; a record never fills it, so a whole one always fits behind a partial. */
#define SRC_BUF 4096u
_Static_assert(SA2_DI_KEYREC_BYTES <= SRC_BUF && SA2_DI_PADREC_BYTES <= SRC_BUF,
               "a whole record must fit the FIFO's read buffer");
_Static_assert(SA2_DI_PADREC_BYTES == 72u, "contract (A): a gamepad record is 72 bytes");

typedef struct { uint8_t bit[32]; } di_keyset;      /* one bit per DIK code */

/* A script line's keys count from its first poll (+1) to after its last (-1).
 * Sorted by poll, the events are applied as the poll index reaches them, so a
 * poll costs the lines that start or end there and not the whole script,
 * which the log of a long session makes long. */
typedef struct {
    uint64_t poll;
    uint32_t line;
    int32_t  by;
} di_keyevent;

/* One change of a slot's state ("Gamepad replay" in sa2_dinput.h): a pad log's
 * line, or one waiting for the XInput call that will answer from it. */
typedef struct {
    uint32_t   call, polls, slot;
    sa2_di_pad v;
} di_padchange;

static struct {
    int          kind;                 /* IN_* */
    char         path[512];            /* the script or the FIFO */
    di_keyset   *lines;                /* script: each key line's keys */
    di_keyevent *ev;                   /* script: two per line, by poll */
    uint32_t     nev, next_ev;
    uint32_t     held[256];            /* script: lines covering this poll, per key */
    int          fd;                   /* FIFO: -1 until the first poll or XInput call opens it */
    uint8_t      buf[SRC_BUF];         /* FIFO: bytes read and not yet a whole record */
    uint32_t     have;                 /* FIFO: how many */
    di_keyset    snap;                 /* FIFO: the latest whole keyboard record's keys */
    di_keyset    taken;                /* FIFO: every key the records read since the last poll held */
    uint32_t     taken_n;              /* FIFO: how many keyboard records that is */
    uint32_t     taken_first_seq;      /* FIFO: the first of them's seq */
    uint32_t     taken_last_seq;       /* FIFO: the last of them's seq */
    sa2_di_pad   pad[SA2_DI_PAD_SLOTS];/* FIFO: the latest gamepad record's slots */
    uint32_t     pad_wait_ms;          /* FIFO: the first XInput call's bound */
    int          pad_asked;            /* FIFO: an XInput call has come */
    int          pad_later;            /* FIFO: SA2_INPUT_PAD_HOTPLUG, 1 unless it is 0 */
    int          pad_boot;             /* FIFO: the start-up scan's calls are still coming */
    uint32_t     pad_objects;          /* FIFO: the slots that scan answered connected, a bit each */
    int          stop_seen;            /* a keyboard poll has answered the SIGTERM */
    uint32_t     stop_poll;            /* ... this one */
    FILE        *log;
    char         log_path[512];
    int          run_open;             /* log: a run of polls holding the same keys */
    uint32_t     run_first, run_last;
    di_keyset    run_keys;
    /* Gamepad replay: nothing below is touched with neither variable set. */
    FILE        *pad_log;              /* SA2_INPUT_PAD_LOG */
    char         pad_log_path[512];
    int          pad_mode_logged;      /* its mode line is written */
    di_padchange *pend;                /* changes no XInput call has answered from yet */
    uint32_t     npend, pend_room;
    int          pad_script;           /* SA2_INPUT_PAD_SCRIPT is the pads' source */
    di_padchange *pad_lines;           /* ... its change lines, in call order */
    uint32_t     npad_lines, next_pad_line;
    int          script_later, script_stand_ins;   /* ... its mode line */
    sa2_di_input_stats st;
} g_in = {.fd = -1};
static volatile sig_atomic_t g_in_stop;
static int g_in_atexit;

static int has_key(const di_keyset *k, uint32_t i) { return (k->bit[i >> 3] >> (i & 7u)) & 1; }
static void set_key(di_keyset *k, uint32_t i) { k->bit[i >> 3] |= (uint8_t)(1u << (i & 7u)); }

/* A configuration refusal: outside any call, so the trap prints the note and
 * the run never starts. The note leads with the variable, because it is cut
 * to 255 bytes and a path is long. */
static void input_refuse(const char *fmt, ...) {
    char note[SA2_TRAP_NOTE_MAX];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(note, sizeof note, fmt, ap);
    va_end(ap);
    sa2_trap_note("%s", note);
    sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_DI_THUNK_BASE);
}

static int parse_dec(const char *s, uint32_t *out) {
    uint64_t v = 0;
    if (!*s) return 0;
    for (; *s; s++) {
        if (*s < '0' || *s > '9') return 0;
        v = v * 10u + (uint64_t)(*s - '0');
        if (v > 0xFFFFFFFFull) return 0;
    }
    *out = (uint32_t)v;
    return 1;
}

/* "0x" and one or two hex digits: 0x01 to 0xFF, since no key has 0x00. */
static int parse_dik(const char *s, size_t n, uint32_t *out) {
    if (n < 3 || n > 4 || s[0] != '0' || (s[1] != 'x' && s[1] != 'X')) return 0;
    uint32_t v = 0;
    for (size_t i = 2; i < n; i++) {
        const char ch = s[i];
        const int d = ch >= '0' && ch <= '9' ? ch - '0'
                    : ch >= 'a' && ch <= 'f' ? ch - 'a' + 10
                    : ch >= 'A' && ch <= 'F' ? ch - 'A' + 10 : -1;
        if (d < 0) return 0;
        v = v * 16u + (uint32_t)d;
    }
    if (!v) return 0;
    *out = v;
    return 1;
}

/* One script line, its comment already cut: 0 when blank, 1 for a key line,
 * -1 with the reason in `why`. */
static int parse_keyline(char *s, uint32_t *first, uint32_t *last, di_keyset *k,
                         char *why, size_t cap) {
    char *f[3];
    int n = 0;
    for (char *p = s; *p;) {
        while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') p++;
        if (!*p) break;
        if (n == 3) {
            snprintf(why, cap, "more than three fields");
            return -1;
        }
        f[n++] = p;
        while (*p && *p != ' ' && *p != '\t' && *p != '\r' && *p != '\n') p++;
        if (*p) *p++ = 0;
    }
    if (!n) return 0;
    if (n != 3) {
        snprintf(why, cap, "%d field%s, not three", n, n == 1 ? "" : "s");
        return -1;
    }
    if (!parse_dec(f[0], first) || !parse_dec(f[1], last)) {
        snprintf(why, cap, "the polls \"%.12s\" and \"%.12s\" are not both decimal", f[0], f[1]);
        return -1;
    }
    if (*last < *first) {
        snprintf(why, cap, "the last poll %u is before the first, %u", *last, *first);
        return -1;
    }
    memset(k, 0, sizeof *k);
    for (const char *p = f[2];;) {
        const char *e = strchr(p, ',');
        const size_t len = e ? (size_t)(e - p) : strlen(p);
        uint32_t v;
        if (!parse_dik(p, len, &v)) {
            snprintf(why, cap, "\"%.*s\" is not a DIK code, 0x01 to 0xFF", (int)(len > 12 ? 12 : len),
                     p);
            return -1;
        }
        set_key(k, v);
        if (!e) break;
        p = e + 1;
    }
    return 1;
}

static int by_poll(const void *a, const void *b) {
    const di_keyevent *x = (const di_keyevent *)a, *y = (const di_keyevent *)b;
    return x->poll < y->poll ? -1 : x->poll > y->poll ? 1 : 0;
}

/* The whole script, at configuration: a line that does not parse refuses the
 * run before it starts, naming the line. */
static void read_script(void) {
    FILE *f = fopen(g_in.path, "r");
    if (!f) {
        input_refuse("SA2_INPUT_DI_SCRIPT does not open: %s (%s)", strerror(errno), g_in.path);
        return;
    }
    char *line = NULL;
    size_t cap = 0;
    uint32_t lineno = 0, n = 0, room = 0;
    ssize_t got;
    while ((got = getline(&line, &cap, f)) >= 0) {
        lineno++;
        uint32_t first = 0, last = 0;
        di_keyset k;
        char why[96];
        int r;
        if (strlen(line) != (size_t)got) {      /* the parse below would stop at it */
            snprintf(why, sizeof why, "it holds a NUL byte");
            r = -1;
        } else {
            char *hash = strchr(line, '#');
            if (hash) *hash = 0;
            r = parse_keyline(line, &first, &last, &k, why, sizeof why);
        }
        if (r < 0) {
            free(line);
            fclose(f);
            input_refuse("SA2_INPUT_DI_SCRIPT line %u: %s; a line is <first_poll> <last_poll> "
                         "<dik>[,<dik>...], '#' a comment (%s)", lineno, why, g_in.path);
            return;
        }
        if (!r) continue;
        if (n == room) {
            room = room ? room * 2u : 64u;
            di_keyset *l = (di_keyset *)realloc(g_in.lines, room * sizeof *l);
            if (l) g_in.lines = l;
            di_keyevent *e = l ? (di_keyevent *)realloc(g_in.ev, 2u * room * sizeof *e) : NULL;
            if (e) g_in.ev = e;
            if (!l || !e) {
                free(line);
                fclose(f);
                input_refuse("SA2_INPUT_DI_SCRIPT: no memory for %u lines (%s)", room, g_in.path);
                return;
            }
        }
        g_in.lines[n] = k;
        g_in.ev[g_in.nev++] = (di_keyevent){first, n, 1};
        if (last != 0xFFFFFFFFu) g_in.ev[g_in.nev++] = (di_keyevent){(uint64_t)last + 1u, n, -1};
        n++;
    }
    const int bad = ferror(f);
    free(line);
    fclose(f);
    if (bad) {
        input_refuse("SA2_INPUT_DI_SCRIPT could not be read to its end (%s)", g_in.path);
        return;
    }
    if (g_in.nev) qsort(g_in.ev, g_in.nev, sizeof *g_in.ev, by_poll);
    g_in.st.script_lines = n;
}

/* The keys the script holds at `poll`; polls come in order, from 0. */
static void script_at(uint32_t poll, di_keyset *out) {
    while (g_in.next_ev < g_in.nev && g_in.ev[g_in.next_ev].poll <= poll) {
        const di_keyevent *e = &g_in.ev[g_in.next_ev++];
        for (uint32_t i = 0; i < 256u; i++)
            if (has_key(&g_in.lines[e->line], i))
                g_in.held[i] = (uint32_t)((int32_t)g_in.held[i] + e->by);
    }
    memset(out, 0, sizeof *out);
    for (uint32_t i = 0; i < 256u; i++)
        if (g_in.held[i]) set_key(out, i);
}

/* Where a source read was asked for, so a refusal names the call: a keyboard
 * poll's GetDeviceState, or an XInput ordinal. */
typedef struct {
    const char *iface, *method;
    uint32_t    caller, pc;
} src_at;

static void src_refuse(const src_at *at, const char *why) {
    sa2_trap_note("%s::%s called from %#010x: %s", at->iface, at->method, at->caller, why);
    sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, at->pc);
}

static void fifo_open(const src_at *at) {
    char why[220];
#if defined(_WIN32)
    /* The session's named pipe, opened as its client (sa2_win32.h). */
    const int fd = sa2_win32_pipe_open(g_in.path);
#else
    const int fd = open(g_in.path, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
#endif
    if (fd < 0) {
        snprintf(why, sizeof why, "SA2_INPUT_DI_SOURCE does not open: %s (%s)", strerror(errno),
                 g_in.path);
        src_refuse(at, why);
        return;
    }
#if !defined(_WIN32)
    struct stat sb;
    if (fstat(fd, &sb) || !S_ISFIFO(sb.st_mode)) {
        close(fd);
        snprintf(why, sizeof why, "SA2_INPUT_DI_SOURCE is not a FIFO (%s)", g_in.path);
        src_refuse(at, why);
        return;
    }
#endif
    g_in.fd = fd;
}

static uint32_t le32(const uint8_t *p) {
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}
static uint16_t le16(const uint8_t *p) { return (uint16_t)(p[0] | p[1] << 8); }

/* Records of either kind read whole, for a refusal's "record <n>". */
static uint32_t records_read(void) { return g_in.st.records + g_in.st.pad_records; }

/* A whole keyboard record: its keys become the snapshot, and join the keys
 * the next poll holds. Byte 0 is no key's. Its seq is on the keyboard records'
 * own counter, so a gap is one between two keyboard records. */
static void key_record(const uint8_t *r) {
    const uint32_t seq = le32(r + 4);
    if (g_in.st.records && seq != g_in.st.last_seq + 1u) g_in.st.seq_gaps++;
    g_in.st.last_seq = seq;
    g_in.st.records++;
    memset(&g_in.snap, 0, sizeof g_in.snap);
    for (uint32_t i = 1; i < 256u; i++)
        if (r[8u + i]) set_key(&g_in.snap, i);
    for (uint32_t b = 0; b < sizeof g_in.taken.bit; b++) g_in.taken.bit[b] |= g_in.snap.bit[b];
    if (!g_in.taken_n) g_in.taken_first_seq = seq;
    g_in.taken_last_seq = seq;
    g_in.taken_n++;
}

/* XInputGetState's buttons: XINPUT_GAMEPAD_* without 0x0400 and 0x0800, which
 * it never reports. */
#define PAD_BUTTONS_MASK 0xF3FFu

/* A change for SA2_INPUT_PAD_LOG, held until the XInput call that answers from
 * it (pad_log_flush). Out of memory, the log would be wrong from here on: said
 * once on stderr and the log closed with a note, never a wrong line. */
static void pad_pend(uint32_t s, const sa2_di_pad *v) {
    if (g_in.npend == g_in.pend_room) {
        const uint32_t room = g_in.pend_room ? g_in.pend_room * 2u : 16u;
        di_padchange *p = (di_padchange *)realloc(g_in.pend, room * sizeof *p);
        if (!p) {
            fprintf(stderr, "sa2 dinput: SA2_INPUT_PAD_LOG: no memory for %u changes; the log ends "
                    "here (%s)\n", room, g_in.pad_log_path);
            fprintf(g_in.pad_log, "# ended early: no memory for the changes\n");
            fclose(g_in.pad_log);
            g_in.pad_log = NULL;
            return;
        }
        g_in.pend = p;
        g_in.pend_room = room;
    }
    g_in.pend[g_in.npend++] = (di_padchange){0, 0, s, *v};
}

/* A slot's state, its packet number one more when it changed. */
static void pad_set(uint32_t s, const sa2_di_pad *v) {
    sa2_di_pad *p = &g_in.pad[s];
    if (p->connected == v->connected && p->buttons == v->buttons && p->lt == v->lt
        && p->rt == v->rt && p->lx == v->lx && p->ly == v->ly && p->rx == v->rx && p->ry == v->ry)
        return;
    const uint32_t packet = p->packet + 1u;
    *p = *v;
    p->packet = packet;
    g_in.st.pad_changes++;
    if (g_in.pad_log) pad_pend(s, p);
}

/* A whole gamepad record: every slot's state. A connected byte other than 0 or
 * 1 means the bytes are out of step, as another magic does. Its seq is on the
 * gamepad records' own counter. */
static void pad_record(const src_at *at, const uint8_t *r) {
    const uint32_t seq = le32(r + 4);
    for (uint32_t s = 0; s < SA2_DI_PAD_SLOTS; s++) {
        const uint8_t c = r[8u + s * SA2_DI_PADSLOT_BYTES];
        if (c > 1u) {
            char why[220];
            snprintf(why, sizeof why, "SA2_INPUT_DI_SOURCE record %u (seq %u, \"SA2P\") gives "
                     "slot %u the connected byte %u, not 0 or 1: records out of step (%s)",
                     records_read(), seq, s, c, g_in.path);
            src_refuse(at, why);
            return;
        }
    }
    if (g_in.st.pad_records && seq != g_in.st.pad_last_seq + 1u) g_in.st.pad_seq_gaps++;
    g_in.st.pad_records++;
    g_in.st.pad_last_seq = seq;
    for (uint32_t s = 0; s < SA2_DI_PAD_SLOTS; s++) {
        const uint8_t *q = r + 8u + s * SA2_DI_PADSLOT_BYTES;
        sa2_di_pad v;
        memset(&v, 0, sizeof v);
        if (q[0]) {
            v.connected = 1;
            v.buttons = (uint16_t)(le16(q + 2) & PAD_BUTTONS_MASK);
            v.lt = q[4];
            v.rt = q[5];
            v.lx = (int16_t)le16(q + 6);
            v.ly = (int16_t)le16(q + 8);
            v.rx = (int16_t)le16(q + 10);
            v.ry = (int16_t)le16(q + 12);
        }
        pad_set(s, &v);
    }
}

/* Every whole record in the buffer, in order; a partial one stays at its front.
 * A record is told by its magic, so another magic stops the run: every later
 * byte would be read out of step. */
static void src_parse(const src_at *at) {
    uint32_t off = 0;
    while (g_in.have - off >= 4u) {
        const uint8_t *r = g_in.buf + off;
        const uint32_t magic = le32(r);
        const uint32_t size = magic == SA2_DI_KEYREC_MAGIC ? SA2_DI_KEYREC_BYTES
                            : magic == SA2_DI_PADREC_MAGIC ? SA2_DI_PADREC_BYTES : 0u;
        if (!size) {
            char why[220];
            snprintf(why, sizeof why, "SA2_INPUT_DI_SOURCE record %u has magic %#010x, not "
                     "%#010x (\"SA2K\") or %#010x (\"SA2P\"): another writer, or records out "
                     "of step (%s)", records_read(), magic, SA2_DI_KEYREC_MAGIC,
                     SA2_DI_PADREC_MAGIC, g_in.path);
            src_refuse(at, why);
            return;
        }
        if (g_in.have - off < size) break;
        if (size == SA2_DI_KEYREC_BYTES) key_record(r);
        else pad_record(at, r);
        off += size;
    }
    if (off) {
        memmove(g_in.buf, g_in.buf + off, g_in.have - off);
        g_in.have -= off;
    }
}

/* Every byte the FIFO has now, without waiting, and every whole record in it.
 * End of file means no writer holds it open: every key up once the records
 * read are applied, every gamepad slot let go, and a record its writer never
 * finished dropped, since the next writer starts a record of its own. 1 when
 * it read end of file. */
static int fifo_drain(const src_at *at) {
    for (;;) {
#if defined(_WIN32)
        const ssize_t n = sa2_win32_pipe_read(g_in.fd, g_in.buf + g_in.have, SRC_BUF - g_in.have);
#else
        const ssize_t n = read(g_in.fd, g_in.buf + g_in.have, SRC_BUF - g_in.have);
#endif
        if (n > 0) {
            g_in.have += (uint32_t)n;
            src_parse(at);
            continue;
        }
        if (n == 0) {
            g_in.st.partial_dropped += g_in.have;
            g_in.have = 0;
            memset(&g_in.snap, 0, sizeof g_in.snap);
            static const sa2_di_pad gone;
            for (uint32_t s = 0; s < SA2_DI_PAD_SLOTS; s++) pad_set(s, &gone);
            return 1;
        }
        if (errno == EINTR) continue;
        if (errno == EAGAIN || errno == EWOULDBLOCK) return 0;
        char why[220];
        snprintf(why, sizeof why, "SA2_INPUT_DI_SOURCE read failed: %s (%s)", strerror(errno),
                 g_in.path);
        src_refuse(at, why);
        return 0;
    }
}

/* One log line, flushed; 0 when it could not be written. */
static int log_line(uint32_t first, uint32_t last, const di_keyset *k) {
    int ok = fprintf(g_in.log, "%u %u", first, last) > 0;
    char sep = ' ';
    for (uint32_t i = 0; i < 256u; i++)
        if (has_key(k, i)) {
            ok &= fprintf(g_in.log, "%c0x%02X", sep, i) > 0;
            sep = ',';
        }
    ok &= fputc('\n', g_in.log) != EOF;
    ok &= fflush(g_in.log) == 0;
    if (ok) g_in.st.log_lines++;
    return ok;
}

/* The poll's keys extend the open run, or end it (its line written) and,
 * when any key is held, open the next. */
static int log_poll(uint32_t poll, const di_keyset *k, uint32_t held) {
    int ok = 1;
    if (g_in.run_open && (!held || memcmp(k, &g_in.run_keys, sizeof *k)
                          || poll != g_in.run_last + 1u)) {
        ok = log_line(g_in.run_first, g_in.run_last, &g_in.run_keys);
        g_in.run_open = 0;
    }
    if (held) {
        if (g_in.run_open) {
            g_in.run_last = poll;
        } else {
            g_in.run_open = 1;
            g_in.run_first = g_in.run_last = poll;
            g_in.run_keys = *k;
        }
    }
    return ok;
}

/* SA2_INPUT_PAD_LOG at exit: the counters as a comment, which a script reader
 * skips. Changes still pending were never answered from, so they are counted
 * and not written: a replay would never apply them. */
static void pad_log_exit(void) {
    if (!g_in.pad_log) return;
    const sa2_di_input_stats *s = &g_in.st;
    fprintf(g_in.pad_log, "# closed at exit: %u XInput calls, %u change lines, %u changes no call "
            "answered from", s->xi_calls, s->pad_log_lines, g_in.npend);
    if (g_in.pad_script)
        fprintf(g_in.pad_log, "; pad script of %u lines, %u applied, %u at a call whose keyboard "
                "polls differed", s->pad_script_lines, s->pad_script_applied, s->pad_script_desyncs);
    fputc('\n', g_in.pad_log);
    if (fclose(g_in.pad_log))
        fprintf(stderr, "sa2 dinput: SA2_INPUT_PAD_LOG: closing failed (%s)\n", g_in.pad_log_path);
    g_in.pad_log = NULL;
}

/* At exit: the run still open, then the counters as a comment, which a
 * script reader skips. The guest is unmapped by now; nothing here reads it. */
static void input_exit(void) {
    pad_log_exit();
    if (!g_in.log) return;
    if (g_in.run_open && !log_line(g_in.run_first, g_in.run_last, &g_in.run_keys))
        fprintf(stderr, "sa2 dinput: SA2_INPUT_DI_LOG: the last line could not be written (%s)\n",
                g_in.log_path);
    g_in.run_open = 0;
    const sa2_di_input_stats *s = &g_in.st;
    fprintf(g_in.log, "# closed at exit: %u keyboard polls, %u holding keys, %u lines", s->polls,
            s->polls_held, s->log_lines);
    if (g_in.kind == IN_SCRIPT)
        fprintf(g_in.log, "; script of %u lines", s->script_lines);
    else
        fprintf(g_in.log, "; %u records, last seq %u, %u seq gaps, %u polls with no writer, %u "
                "bytes of an unfinished record dropped", s->records, s->last_seq, s->seq_gaps,
                s->polls_without_writer, s->partial_dropped);
    /* Gamepads only when there were any, so a keyboard session's line is as it was. */
    if (s->pad_records)
        fprintf(g_in.log, "; %u gamepad records, last seq %u, %u seq gaps, %u slot changes, %u "
                "XInput answers from a connected slot (%s)",
                s->pad_records, s->pad_last_seq, s->pad_seq_gaps, s->pad_changes, s->pad_answers,
                g_in.pad_log_path[0] ? "gamepads replay from SA2_INPUT_PAD_LOG"
                                     : "gamepads are not replayed from this log");
    /* Pads that connect later, only when it answered anything apart from the records. */
    if (s->pad_stand_ins || s->pad_hidden)
        fprintf(g_in.log, "; pad objects for slots 0x%x, %u XInput answers standing in for a pad "
                "not connected, %u rescan answers hiding one with no object", s->pad_objects,
                s->pad_stand_ins, s->pad_hidden);
    if (s->quit_posted) fprintf(g_in.log, "; SIGTERM, WM_QUIT posted at poll %u", s->quit_poll);
    else if (g_in.stop_seen)
        fprintf(g_in.log, "; SIGTERM at poll %u, WM_QUIT posted already", g_in.stop_poll);
    else if (g_in_stop) fprintf(g_in.log, "; SIGTERM, and no keyboard poll after it");
    fputc('\n', g_in.log);
    if (fclose(g_in.log))
        fprintf(stderr, "sa2 dinput: SA2_INPUT_DI_LOG: closing failed (%s)\n", g_in.log_path);
    g_in.log = NULL;
}

/* Created, or refused when it already holds anything: a log replays one
 * session from poll 0, and a second session appended would merge two
 * sessions' keys at the same polls. Anything but a regular file is refused
 * before it is opened, since opening a FIFO to write waits for a reader;
 * O_NONBLOCK covers a path replaced in between, where such an open answers
 * ENXIO instead of waiting. `var` names the variable in a refusal; NULL after
 * one. Shared by SA2_INPUT_DI_LOG and SA2_INPUT_PAD_LOG, whose refusals read
 * alike. */
static FILE *open_new_log(const char *var, const char *path) {
    struct stat sb;
    if (!stat(path, &sb) && !S_ISREG(sb.st_mode)) {
        input_refuse("%s is not a regular file (%s)", var, path);
        return NULL;
    }
    const int fd = open(path, O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC | O_NONBLOCK, 0644);
    if (fd < 0) {
        input_refuse("%s does not open: %s (%s)", var, strerror(errno), path);
        return NULL;
    }
    if (fstat(fd, &sb) || !S_ISREG(sb.st_mode)) {
        close(fd);
        input_refuse("%s is not a regular file (%s)", var, path);
        return NULL;
    }
    if (sb.st_size) {
        close(fd);
        input_refuse("%s is not an empty file: a log replays one session from poll "
                     "0, and two would merge (%s)", var, path);
        return NULL;
    }
    const int fl = fcntl(fd, F_GETFL);
    if (fl < 0 || fcntl(fd, F_SETFL, fl & ~O_NONBLOCK)) {
        close(fd);
        input_refuse("%s: its descriptor's flags cannot be set: %s (%s)", var,
                     strerror(errno), path);
        return NULL;
    }
    FILE *f = fdopen(fd, "a");
    if (!f) {
        close(fd);
        input_refuse("%s: no stream for it (%s)", var, path);
        return NULL;
    }
    if (!g_in_atexit) {
        if (atexit(input_exit)) {
            fclose(f);
            input_refuse("%s: no exit handler could be registered, so the last "
                         "line would be lost (%s)", var, path);
            return NULL;
        }
        g_in_atexit = 1;
    }
    return f;
}

static void open_log(const char *path) {
    snprintf(g_in.log_path, sizeof g_in.log_path, "%s", path);
    if (!(g_in.log = open_new_log("SA2_INPUT_DI_LOG", path))) return;
    fprintf(g_in.log, "# SA2_INPUT_DI_LOG of a %s source: one line per run of keyboard polls "
            "holding the same keys, <first_poll> <last_poll> <dik>[,<dik>...]; it replays as "
            "SA2_INPUT_DI_SCRIPT\n", g_in.kind == IN_SCRIPT ? "script" : "FIFO");
    if (fflush(g_in.log)) input_refuse("SA2_INPUT_DI_LOG cannot be written (%s)", path);
}

static void open_pad_log(const char *path) {
    snprintf(g_in.pad_log_path, sizeof g_in.pad_log_path, "%s", path);
    if (!(g_in.pad_log = open_new_log("SA2_INPUT_PAD_LOG", path))) return;
    fprintf(g_in.pad_log, "# SA2_INPUT_PAD_LOG of a %s source: \"mode <hotplug> <stand_ins>\" at the "
            "first XInput call, then one line per change of a slot's state, <call> <polls> <slot> "
            "<connected> <buttons> <lt> <rt> <lx> <ly> <rx> <ry>; it replays as "
            "SA2_INPUT_PAD_SCRIPT beside SA2_INPUT_DI_SCRIPT\n",
            g_in.kind == IN_FIFO ? "FIFO" : "script");
    if (fflush(g_in.pad_log)) input_refuse("SA2_INPUT_PAD_LOG cannot be written (%s)", path);
}

static const char *nonempty(const char *name);

/* A signed decimal from lo to hi. */
static int parse_int(const char *s, long lo, long hi, long *out) {
    char *end;
    if (!*s) return 0;
    errno = 0;
    const long v = strtol(s, &end, 10);
    if (errno || *end || v < lo || v > hi) return 0;
    *out = v;
    return 1;
}

/* One pad script line, its comment cut: 0 when blank, 1 for a change, 2 for the
 * mode line, -1 with the reason in `why`. */
static int parse_padline(char *s, di_padchange *c, int *later, int *stand_ins, char *why,
                         size_t cap) {
    char *f[12];
    int n = 0;
    for (char *p = s; *p;) {
        while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') p++;
        if (!*p) break;
        if (n == 12) {
            snprintf(why, cap, "more than eleven fields");
            return -1;
        }
        f[n++] = p;
        while (*p && *p != ' ' && *p != '\t' && *p != '\r' && *p != '\n') p++;
        if (*p) *p++ = 0;
    }
    if (!n) return 0;
    long v[11];
    if (!strcmp(f[0], "mode")) {
        if (n != 3 || !parse_int(f[1], 0, 1, &v[0]) || !parse_int(f[2], 0, 1, &v[1])) {
            snprintf(why, cap, "a mode line is \"mode <0|1> <0|1>\"");
            return -1;
        }
        *later = (int)v[0];
        *stand_ins = (int)v[1];
        return 2;
    }
    if (n != 11) {
        snprintf(why, cap, "%d field%s, not eleven", n, n == 1 ? "" : "s");
        return -1;
    }
    uint32_t call, polls;
    if (!parse_dec(f[0], &call) || !parse_dec(f[1], &polls) || !parse_int(f[2], 0, 3, &v[2]) ||
        !parse_int(f[3], 0, 1, &v[3])) {
        snprintf(why, cap, "the call, polls, slot (0 to 3) or connected (0 or 1) does not parse");
        return -1;
    }
    char *end;
    errno = 0;
    const unsigned long b = strtoul(f[4], &end, 16);
    if (strncmp(f[4], "0x", 2) || errno || *end || b > 0xFFFFu || (b & ~PAD_BUTTONS_MASK)) {
        snprintf(why, cap, "the buttons \"%.12s\" are not 0x and hex within 0x%04X", f[4],
                 PAD_BUTTONS_MASK);
        return -1;
    }
    for (int i = 5; i < 11; i++)
        if (!parse_int(f[i], i < 7 ? 0 : -32768, i < 7 ? 255 : 32767, &v[i])) {
            snprintf(why, cap, "field %d \"%.12s\" is out of its range", i + 1, f[i]);
            return -1;
        }
    memset(c, 0, sizeof *c);
    c->call = call;
    c->polls = polls;
    c->slot = (uint32_t)v[2];
    if (v[3]) {         /* a slot let go holds nothing else, as pad_record makes it */
        c->v.connected = 1;
        c->v.buttons = (uint16_t)b;
        c->v.lt = (uint8_t)v[5];
        c->v.rt = (uint8_t)v[6];
        c->v.lx = (int16_t)v[7];
        c->v.ly = (int16_t)v[8];
        c->v.rx = (int16_t)v[9];
        c->v.ry = (int16_t)v[10];
    }
    return 1;
}

/* The whole pad script, at configuration: a line that does not parse, or one
 * before its predecessor's call, refuses the run, naming the line. */
static void read_pad_script(const char *path) {
    FILE *f = fopen(path, "r");
    if (!f) {
        input_refuse("SA2_INPUT_PAD_SCRIPT does not open: %s (%s)", strerror(errno), path);
        return;
    }
    char *line = NULL;
    size_t cap = 0;
    uint32_t lineno = 0, room = 0;
    ssize_t got;
    while ((got = getline(&line, &cap, f)) >= 0) {
        lineno++;
        di_padchange c;
        char why[96];
        int r;
        if (strlen(line) != (size_t)got) {
            snprintf(why, sizeof why, "it holds a NUL byte");
            r = -1;
        } else {
            char *hash = strchr(line, '#');
            if (hash) *hash = 0;
            r = parse_padline(line, &c, &g_in.script_later, &g_in.script_stand_ins, why, sizeof why);
        }
        if (r == 1 && g_in.npad_lines && c.call < g_in.pad_lines[g_in.npad_lines - 1].call) {
            snprintf(why, sizeof why, "call %u comes after call %u", c.call,
                     g_in.pad_lines[g_in.npad_lines - 1].call);
            r = -1;
        }
        if (r < 0) {
            free(line);
            fclose(f);
            input_refuse("SA2_INPUT_PAD_SCRIPT line %u: %s; see sa2_dinput.h, \"Gamepad replay\" "
                         "(%s)", lineno, why, path);
            return;
        }
        if (r != 1) continue;
        if (g_in.npad_lines == room) {
            room = room ? room * 2u : 64u;
            di_padchange *l = (di_padchange *)realloc(g_in.pad_lines, room * sizeof *l);
            if (!l) {
                free(line);
                fclose(f);
                input_refuse("SA2_INPUT_PAD_SCRIPT: no memory for %u lines (%s)", room, path);
                return;
            }
            g_in.pad_lines = l;
        }
        g_in.pad_lines[g_in.npad_lines++] = c;
    }
    const int bad = ferror(f);
    free(line);
    fclose(f);
    if (bad) {
        input_refuse("SA2_INPUT_PAD_SCRIPT could not be read to its end (%s)", path);
        return;
    }
    g_in.pad_script = 1;
    g_in.st.pad_script_lines = g_in.npad_lines;
}

/* Gamepad replay's configuration, after the keyboard's: the pad log needs a
 * source of pads (the FIFO, or the pad script), and the pad script the
 * keyboard's script, since a FIFO brings its own pads. */
static void pad_config(void) {
    const char *script = nonempty("SA2_INPUT_PAD_SCRIPT"), *log = nonempty("SA2_INPUT_PAD_LOG");
    if (!script && !log) return;
    if (script && g_in.kind != IN_SCRIPT) {
        input_refuse("SA2_INPUT_PAD_SCRIPT is set without SA2_INPUT_DI_SCRIPT: a pad script "
                     "replays beside the keyboard's script, and a FIFO brings its own pads");
        return;
    }
    if (log && g_in.kind != IN_FIFO && !script) {
        input_refuse("SA2_INPUT_PAD_LOG is set with neither a FIFO source nor "
                     "SA2_INPUT_PAD_SCRIPT: the log holds what a source of pads applied");
        return;
    }
    if ((script && strlen(script) >= sizeof g_in.path) ||
        (log && strlen(log) >= sizeof g_in.pad_log_path)) {
        input_refuse("SA2_INPUT_PAD_SCRIPT or SA2_INPUT_PAD_LOG is a path of %zu bytes or more",
                     sizeof g_in.path);
        return;
    }
    if (script) read_pad_script(script);
    if (log) open_pad_log(log);
}

#if defined(_WIN32)
static void on_console_stop(void) {
    g_in_stop = 1;
}
#else
static void on_term(int sig) {
    (void)sig;
    g_in_stop = 1;
}
#endif

static const char *nonempty(const char *name) {
    const char *v = getenv(name);
    return v && *v ? v : NULL;
}

/* After the device switch and the break are read: each refusal below is a
 * source that no keyboard poll would carry. */
static void input_config(void) {
    /* An earlier call's state goes, as in the rest of the configuration. Not
     * quite idempotent: a log holds one configuration's polls, so a second
     * configuration refuses the log the first one began (its header makes it
     * non-empty). */
    free(g_in.lines);
    free(g_in.ev);
    free(g_in.pend);
    free(g_in.pad_lines);
    if (g_in.fd >= 0) close(g_in.fd);
    if (g_in.log) fclose(g_in.log);
    if (g_in.pad_log) fclose(g_in.pad_log);
    memset(&g_in, 0, sizeof g_in);
    g_in.fd = -1;
    g_in_stop = 0;
    const char *script = nonempty("SA2_INPUT_DI_SCRIPT"), *source = nonempty("SA2_INPUT_DI_SOURCE");
    const char *log = nonempty("SA2_INPUT_DI_LOG");
    if (!script && !source) {
        if (log)
            input_refuse("SA2_INPUT_DI_LOG is set and neither SA2_INPUT_DI_SCRIPT nor "
                         "SA2_INPUT_DI_SOURCE is: the log holds what a source applied");
        else if (nonempty("SA2_INPUT_PAD_SCRIPT") || nonempty("SA2_INPUT_PAD_LOG"))
            input_refuse("SA2_INPUT_PAD_SCRIPT or SA2_INPUT_PAD_LOG is set and neither "
                         "SA2_INPUT_DI_SCRIPT nor SA2_INPUT_DI_SOURCE is: gamepad replay rides "
                         "on the keyboard's source");
        return;
    }
    const char *name = script ? "SA2_INPUT_DI_SCRIPT" : "SA2_INPUT_DI_SOURCE";
    if (script && source) {
        input_refuse("SA2_INPUT_DI_SCRIPT and SA2_INPUT_DI_SOURCE are both set: the keyboard has "
                     "one host source, a script or a FIFO");
        return;
    }
    if (!g_enabled) {
        input_refuse("%s is set and SA2_DINPUT_DEVICE is off: DirectInput8Create would answer "
                     "E_FAIL and no keyboard would be polled", name);
        return;
    }
    if (g_break != BRK_NONE) {
        input_refuse("%s is set beside SA2_DINPUT_BREAK=%s: a break falsifies the device and a "
                     "source feeds it, one at a time", name, BREAK_NAMES[g_break]);
        return;
    }
    const char *path = script ? script : source;
    if (strlen(path) >= sizeof g_in.path || (log && strlen(log) >= sizeof g_in.log_path)) {
        input_refuse("%s or SA2_INPUT_DI_LOG is a path of %zu bytes or more", name,
                     sizeof g_in.path);
        return;
    }
    snprintf(g_in.path, sizeof g_in.path, "%s", path);
    if (script) {
        g_in.kind = IN_SCRIPT;
        read_script();
    } else {
#if defined(_WIN32)
        /* This host's FIFO is a named pipe the session created before the run, and
         * its SIGTERM a console control event (sa2_win32.h). Checked now, as the
         * FIFO is, and opened at the first poll (fifo_open). */
        if (!sa2_win32_is_pipe_name(path)) {
            input_refuse("SA2_INPUT_DI_SOURCE is not a named pipe (\\\\.\\pipe\\<name>), "
                         "this host's FIFO (%s)", path);
            return;
        }
        if (!sa2_win32_pipe_exists(path)) {
            input_refuse("SA2_INPUT_DI_SOURCE: no such named pipe; it must be created before "
                         "the run (%s)", path);
            return;
        }
        g_in.kind = IN_FIFO;
        if (sa2_win32_on_console_stop(on_console_stop)) {
            input_refuse("SA2_INPUT_DI_SOURCE: the console control event, which ends a live "
                         "session on this host, cannot be caught");
            return;
        }
#else
        /* Checked now, before the run starts, as well as when the first poll
         * opens it (fifo_open). */
        struct stat sb;
        if (stat(path, &sb)) {
            input_refuse("SA2_INPUT_DI_SOURCE: %s; it must be a FIFO made before the run (%s)",
                         strerror(errno), path);
            return;
        }
        if (!S_ISFIFO(sb.st_mode)) {
            input_refuse("SA2_INPUT_DI_SOURCE is not a FIFO (%s)", path);
            return;
        }
        g_in.kind = IN_FIFO;
        /* A live session ends by SIGTERM (tools/s5/play_live.py): asked once,
         * the next keyboard poll posts WM_QUIT; asked twice, the default
         * action ends the process. */
        struct sigaction sa;
        memset(&sa, 0, sizeof sa);
        sa.sa_handler = on_term;
        sa.sa_flags = SA_RESETHAND | SA_RESTART;
        sigemptyset(&sa.sa_mask);
        if (sigaction(SIGTERM, &sa, NULL)) {
            input_refuse("SA2_INPUT_DI_SOURCE: SIGTERM, which ends a live session, cannot be "
                         "caught: %s", strerror(errno));
            return;
        }
#endif
    }
    if (g_in.kind == IN_FIFO) {
        /* The first XInput call's wait for the source's gamepads ("The host
         * gamepads" in sa2_dinput.h): read now, so a bad value stops the run
         * before it starts. */
        const char *w = nonempty("SA2_INPUT_PAD_WAIT_MS");
        uint32_t ms = SA2_DI_PAD_WAIT_MS_DEFAULT;
        if (w && (!parse_dec(w, &ms) || ms > SA2_DI_PAD_WAIT_MS_MAX)) {
            input_refuse("SA2_INPUT_PAD_WAIT_MS=%.20s is not a decimal count of milliseconds from "
                         "0 to %u", w, SA2_DI_PAD_WAIT_MS_MAX);
            return;
        }
        g_in.pad_wait_ms = ms;
        /* Pads that connect later ("Pads that connect later" in sa2_dinput.h). */
        const char *h = nonempty("SA2_INPUT_PAD_HOTPLUG");
        if (h && strcmp(h, "0") && strcmp(h, "1")) {
            input_refuse("SA2_INPUT_PAD_HOTPLUG=%.20s is neither 0 nor 1", h);
            return;
        }
        g_in.pad_later = !h || !strcmp(h, "1");
    }
    if (log) open_log(log);
    pad_config();
}

/* The keyboard's answer at the next poll index: the source's keys written as
 * 0x80 over the zero fill, the log told, and a requested end posted. The
 * count of keys held. A FIFO poll that read records holds every key any of
 * them held, so a press and its release between two polls still reach one;
 * one that read none holds the latest record's keys. */
static uint32_t host_keys(di_ctx *c, uint32_t data) {
    static const uint8_t down = 0x80;
    const uint32_t poll = g_in.st.polls;
    di_keyset k;
    if (g_in.kind == IN_SCRIPT) {
        script_at(poll, &k);
    } else {
        const src_at at = {"IDirectInputDevice8A", "GetDeviceState", c->caller, c->pc};
        if (g_in.fd < 0) fifo_open(&at);
        if (fifo_drain(&at)) g_in.st.polls_without_writer++;
        k = g_in.taken_n ? g_in.taken : g_in.snap;
    }
    uint32_t held = 0;
    for (uint32_t i = 0; i < 256u; i++)
        if (has_key(&k, i)) {
            pkn(data + i, &down, 1u);
            held++;
        }
    g_in.st.polls++;
    g_in.st.keys_written += held;
    if (held) g_in.st.polls_held++;
    if (g_in.log && !log_poll(poll, &k, held)) {
        char why[220];
        snprintf(why, sizeof why, "SA2_INPUT_DI_LOG could not be written: %s (%s)", strerror(errno),
                 g_in.log_path);
        refuse(c, "IDirectInputDevice8A", "GetDeviceState", why);
    }
    if (g_in.taken_n && sa2_dinput_host_records_read)
        sa2_dinput_host_records_read(poll, g_in.taken_first_seq, g_in.taken_last_seq, g_in.taken_n);
    /* The records this poll applied are spent; XInput calls before the next
     * poll add theirs (sa2_dinput_live_pad). */
    memset(&g_in.taken, 0, sizeof g_in.taken);
    g_in.taken_n = 0;
    if (g_in_stop && !g_in.stop_seen) {
        /* Once: WM_QUIT as the host's, unless the guest or the pump posted one
         * already, on which the guest is leaving anyway. */
        sa2_wnd_stats ws;
        sa2_window_stats(&ws);
        g_in.stop_seen = 1;
        g_in.stop_poll = poll;
        if (!ws.quit_posted) {
            sa2_window_post_quit(0, 1);
            g_in.st.quit_posted = 1;
            g_in.st.quit_poll = poll;
        }
    }
    return held;
}

/* ---------------------------------------------------- the host gamepads
 *
 * sa2_dinput.h, "The host gamepads". XInput's calls read the FIFO the
 * keyboard reads; nothing here runs without a FIFO source. */

#if defined(_WIN32)
/* kernel32's, declared here rather than through windows.h, whose names the
 * runtime's own headers would meet. */
__declspec(dllimport) unsigned long long __stdcall GetTickCount64(void);
__declspec(dllimport) void __stdcall Sleep(unsigned long ms);
static uint64_t host_ms(void) { return GetTickCount64(); }
static void host_sleep_ms(uint32_t ms) { Sleep(ms); }
#else
static uint64_t host_ms(void) {
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC, &t)) return 0;
    return (uint64_t)t.tv_sec * 1000u + (uint64_t)t.tv_nsec / 1000000u;
}
static void host_sleep_ms(uint32_t ms) {
    const struct timespec t = {(time_t)(ms / 1000u), (long)(ms % 1000u) * 1000000L};
    nanosleep(&t, NULL);
}
#endif

/* The first XInput call: the game's one scan that makes pad objects runs now
 * (0x00864E29), so a pad the sender knows must be read before it answers. Waits
 * until a gamepad record has been read, the bound passes, or a SIGTERM asks for
 * the end. Said on stderr, since it holds the boot. */
static void pad_boot_wait(const src_at *at) {
    g_in.st.pad_wait_ms = g_in.pad_wait_ms;
    if (g_in.st.pad_records) {
        g_in.st.pad_wait_found = 1;
        return;
    }
    if (!g_in.pad_wait_ms) return;
    const uint64_t start = host_ms();
    uint64_t now = start;
    while (!g_in.st.pad_records && !g_in_stop && now - start < g_in.pad_wait_ms) {
        host_sleep_ms(2);
        fifo_drain(at);
        now = host_ms();
    }
    g_in.st.pad_waited_ms = (uint32_t)(now - start);
    g_in.st.pad_wait_found = g_in.st.pad_records ? 1u : 0u;
    fprintf(stderr, "sa2 dinput: the game's controller scan waited %u ms for SA2_INPUT_DI_SOURCE's "
            "first gamepad record (SA2_INPUT_PAD_WAIT_MS=%u): %s\n", g_in.st.pad_waited_ms,
            g_in.pad_wait_ms, g_in.st.pad_records ? "read" : "none came; no pad this run");
}

/* The image's XInput calls that "Pads that connect later" (sa2_dinput.h) answers
 * apart from the records, by the return address the import sees (the word at the
 * thunk's esp; the call is 5 bytes before it). */
#define XI_RET_BOOT_SCAN  0x00864E2Eu  /* the start-up scan's XInputGetCapabilities, 0x00864E29 */
#define XI_RET_INIT_CAPS  0x00863DF6u  /* a new pad object's first poll (vtable 0x008FFFE0 slot
                                          5, 0x00863DE0): XInputGetCapabilities, 0x00863DF1 */
#define XI_RET_INIT_STATE 0x00863E1Eu  /* ... and its XInputGetState, 0x00863E19 */
#define XI_RET_RESCAN     0x00864A2Cu  /* the rescan's XInputGetCapabilities, 0x00864A27 */

/* Once the start-up scan's calls are over: what it made, said on stderr. */
static void pad_boot_end(void) {
    g_in.pad_boot = 0;
    if (!g_in.st.pad_stand_in_slots) return;
    fprintf(stderr, "sa2 dinput: the game's controller scan made pad objects for slots 0x%x, %u "
            "of them for pads not connected yet (SA2_INPUT_PAD_HOTPLUG): a pad that connects "
            "later drives the game\n", g_in.pad_objects, g_in.st.pad_stand_in_slots);
}

/* SA2_INPUT_PAD_SCRIPT at XInput call `call`: every line of that call, in order,
 * as pad_record would have set the slot. */
static void pad_script_apply(uint32_t call) {
    while (g_in.next_pad_line < g_in.npad_lines && g_in.pad_lines[g_in.next_pad_line].call <= call) {
        const di_padchange *c = &g_in.pad_lines[g_in.next_pad_line++];
        if (c->polls != g_in.st.polls) {
            if (!g_in.st.pad_script_desyncs) {
                g_in.st.pad_script_first_desync = call;
                fprintf(stderr, "sa2 dinput: SA2_INPUT_PAD_SCRIPT: XInput call %u comes after %u "
                        "keyboard polls, the session's after %u: the replay has left its path\n",
                        call, g_in.st.polls, c->polls);
            }
            g_in.st.pad_script_desyncs++;
        }
        pad_set(c->slot, &c->v);
        g_in.st.pad_script_applied++;
    }
}

/* SA2_INPUT_PAD_LOG at XInput call `call`: the mode line at the first, then every
 * change this call is the first to answer from. */
static void pad_log_flush(const src_at *at, uint32_t call) {
    int ok = 1;
    if (!g_in.pad_mode_logged) {
        ok &= fprintf(g_in.pad_log, "mode %d %u\n", g_in.pad_later ? 1 : 0, g_in.st.pad_hotplug) > 0;
        g_in.pad_mode_logged = 1;
    }
    for (uint32_t i = 0; i < g_in.npend; i++) {
        const sa2_di_pad *v = &g_in.pend[i].v;
        ok &= fprintf(g_in.pad_log, "%u %u %u %u 0x%04X %u %u %d %d %d %d\n", call, g_in.st.polls,
                      g_in.pend[i].slot, v->connected, v->buttons, v->lt, v->rt, v->lx, v->ly, v->rx,
                      v->ry) > 0;
        g_in.st.pad_log_lines++;
    }
    const int any = g_in.npend || call == 0u;
    g_in.npend = 0;
    if (any) ok &= fflush(g_in.pad_log) == 0;
    if (!ok) {
        char why[220];
        snprintf(why, sizeof why, "SA2_INPUT_PAD_LOG could not be written: %s (%s)", strerror(errno),
                 g_in.pad_log_path);
        src_refuse(at, why);
    }
}

int sa2_dinput_live_pad(uint32_t slot, uint32_t caller, sa2_di_pad *out) {
    if ((g_in.kind != IN_FIFO && !g_in.pad_script) || slot >= SA2_DI_PAD_SLOTS) return 0;
    const src_at at = {"XINPUT1_3", "XInput", caller, SA2_DI_THUNK_BASE};
    const uint32_t call = g_in.st.xi_calls++;
    if (g_in.kind == IN_FIFO) {
        if (g_in.fd < 0) fifo_open(&at);
        fifo_drain(&at);
        if (!g_in.pad_asked) {
            g_in.pad_asked = 1;
            pad_boot_wait(&at);
            g_in.pad_boot = 1;
            g_in.st.pad_hotplug = g_in.pad_later && g_in.st.pad_records ? 1u : 0u;
        }
    } else {
        /* The pad script: the session's mode, then its changes for this call. */
        if (!g_in.pad_asked) {
            g_in.pad_asked = 1;
            g_in.pad_boot = 1;
            g_in.pad_later = g_in.script_later;
            g_in.st.pad_hotplug = g_in.script_stand_ins ? 1u : 0u;
        }
        pad_script_apply(call);
    }
    if (g_in.pad_log) pad_log_flush(&at, call);
    const int boot_call = caller == XI_RET_BOOT_SCAN || caller == XI_RET_INIT_CAPS ||
                          caller == XI_RET_INIT_STATE;
    if (g_in.pad_boot && !boot_call) pad_boot_end();
    const uint32_t bit = 1u << slot;
    int answer = g_in.pad[slot].connected, stand_in = 0;
    if (g_in.pad_later && caller == XI_RET_RESCAN) {
        /* The rescan sees a slot connected exactly when it has a pad object, so
         * the count it compares never changes and the game's "controllers
         * changed" dialog never comes. */
        if (!(g_in.pad_objects & bit)) {
            if (answer) g_in.st.pad_hidden++;
            return 0;
        }
        stand_in = !answer;
    } else if (g_in.pad_boot && g_in.st.pad_hotplug && !answer &&
               (caller == XI_RET_BOOT_SCAN || (g_in.pad_objects & bit))) {
        stand_in = 1;           /* the scan, and the first poll of the object it made */
    }
    if (!stand_in && !answer) return 0;
    if (caller == XI_RET_BOOT_SCAN) {
        g_in.pad_objects |= bit;
        g_in.st.pad_objects = g_in.pad_objects;
    }
    if (stand_in) {
        const sa2_di_pad rest = {.connected = 1, .packet = g_in.pad[slot].packet};
        *out = rest;
        g_in.st.pad_stand_ins++;
        g_in.st.pad_stand_in_slots += caller == XI_RET_BOOT_SCAN;
        return 2;
    }
    *out = g_in.pad[slot];
    g_in.st.pad_answers++;
    return 1;
}

static uint32_t m_GetDeviceState(di_ctx *c) {
    sa2_di_device *d = c->d;
    if (d->kind == SA2_DI_KIND_ENUMERATED)
        refuse(c, "IDirectInputDevice8A", "GetDeviceState", JOYSTICK_UNREACHED);
    const uint32_t cb = c->a[0], data = c->a[1];
    d->state_calls++;
    c->answered = SA2_DI_ANS_INVALID;
    /* Wine's order: a null buffer, then the device status, then the format. */
    if (!data) return SA2_DIERR_INVALIDPARAM;
    if (g_break == BRK_INPUTLOST) return input_lost(c, "GetDeviceState");
    if (g_break == BRK_NOTACQUIRED) {
        c->answered = SA2_DI_ANS_BREAK;
        return SA2_DIERR_NOTACQUIRED;
    }
    if (!d->acquired) return SA2_DIERR_NOTACQUIRED;
    if (!d->format_data_size || cb != d->format_data_size) return SA2_DIERR_INVALIDPARAM;
    d->inputlost_run = 0;
    if (sa2_guest_fill(data, 0, cb) != SA2_OK) gfault(data, "a device-state write");
    c->answered = SA2_DI_ANS_IDLE;
    /* The host keyboard, after the zero fill and before the breaks, which
     * input_config never lets it meet. */
    if (g_in.kind != IN_NONE && d->kind == SA2_DI_KIND_KEYBOARD && host_keys(c, data))
        c->answered = SA2_DI_ANS_HOST;
    if (g_break == BRK_ALLKEYS && d->kind == SA2_DI_KIND_KEYBOARD) {
        if (sa2_guest_fill(data, 0x80, cb) != SA2_OK) gfault(data, "a device-state write");
        c->answered = SA2_DI_ANS_BREAK;
    }
    if (g_break == BRK_MOUSEMOVE && d->kind == SA2_DI_KIND_MOUSE) {
        pk32(data + 0u, 5u);        /* DIMOUSESTATE.lX */
        pk32(data + 4u, 3u);        /* DIMOUSESTATE.lY */
        c->answered = SA2_DI_ANS_BREAK;
    }
    if (c->answered == SA2_DI_ANS_BREAK) g_st.state_answers_nonidle++;
    d->state_ok++;
    d->state_bytes_written += cb;
    g_st.state_bytes_written += cb;
    c->out0 = cb;
    return SA2_DI_OK;
}

static uint32_t m_Release(di_ctx *c) {
    di_obj *o = c->obj;
    c->answered = SA2_DI_ANS_GUEST;
    if (o->refs) o->refs--;
    if (c->d) c->d->refs = o->refs;
    if (!o->refs) {
        o->alive = 0;
        if (c->d) c->d->released = 1;
    }
    return o->refs;
}

/* ------------------------------------------------------------ the tables */

/* Not `const`: `SA2_DINPUT_BREAK=argc` patches CreateDevice's count in place, so
 * the count the dispatcher pops with and the count the run publishes for
 * tools/s4/dinput_surface.py --argc are the same number. */
static di_row ROWS_DI8[] = {
    {0,  2, DF_OUT, "QueryInterface", NULL},
    {1,  0, 0, "AddRef", NULL},
    {2,  0, 0, "Release", NULL},
    {3,  3, DF_OUT, "CreateDevice", m_CreateDevice},
    {4,  4, DF_CB, "EnumDevices", m_EnumDevices},
    {5,  1, 0, "GetDeviceStatus", NULL},
    {6,  2, 0, "RunControlPanel", NULL},
    {7,  2, 0, "Initialize", NULL},
    {8,  3, DF_OUT, "FindDevice", NULL},
    {9,  5, DF_CB, "EnumDevicesBySemantics", NULL},
    {10, 4, DF_CB, "ConfigureDevices", NULL},
    {0, 0, 0, NULL, NULL},
};

/* IDirectInputDevice8A, all 32 slots, argument counts from zig's
 * any-windows-any/dinput.h. Handlers only where the image calls. */
static di_row ROWS_DIDEV[] = {
    {0,  2, DF_OUT, "QueryInterface", NULL},
    {1,  0, 0, "AddRef", NULL},
    {2,  0, 0, "Release", m_Release},
    {3,  1, DF_OUT, "GetCapabilities", NULL},
    {4,  3, DF_CB, "EnumObjects", NULL},
    {5,  2, DF_OUT, "GetProperty", NULL},
    {6,  2, 0, "SetProperty", NULL},
    {7,  0, 0, "Acquire", m_Acquire},
    {8,  0, 0, "Unacquire", m_Unacquire},
    {9,  2, DF_OUT, "GetDeviceState", m_GetDeviceState},
    {10, 4, DF_OUT, "GetDeviceData", NULL},
    {11, 1, 0, "SetDataFormat", m_SetDataFormat},
    {12, 1, 0, "SetEventNotification", NULL},
    {13, 2, 0, "SetCooperativeLevel", m_SetCooperativeLevel},
    {14, 3, DF_OUT, "GetObjectInfo", NULL},
    {15, 1, DF_OUT, "GetDeviceInfo", NULL},
    {16, 2, 0, "RunControlPanel", NULL},
    {17, 3, 0, "Initialize", NULL},
    {18, 4, DF_OUT, "CreateEffect", NULL},
    {19, 3, DF_CB, "EnumEffects", NULL},
    {20, 2, DF_OUT, "GetEffectInfo", NULL},
    {21, 1, DF_OUT, "GetForceFeedbackState", NULL},
    {22, 1, 0, "SendForceFeedbackCommand", NULL},
    {23, 3, DF_CB, "EnumCreatedEffectObjects", NULL},
    {24, 1, DF_OUT, "Escape", NULL},
    {25, 0, 0, "Poll", NULL},
    {26, 4, DF_OUT, "SendDeviceData", NULL},
    {27, 4, DF_CB, "EnumEffectsInFile", NULL},
    {28, 4, 0, "WriteEffectToFile", NULL},
    {29, 3, 0, "BuildActionMap", NULL},
    {30, 3, 0, "SetActionMap", NULL},
    {31, 1, DF_OUT, "GetImageInfo", NULL},
    {0, 0, 0, NULL, NULL},
};

static di_type g_types[] = {
    {"IDirectInput8A",       ROWS_DI8,   0},
    {"IDirectInputDevice8A", ROWS_DIDEV, 0},
};
static uint32_t type_count(void) { return (uint32_t)(sizeof g_types / sizeof g_types[0]); }
_Static_assert(sizeof g_types / sizeof g_types[0] <= SA2_DI_MAX_TYPES,
               "every dinput type needs a thunk block");

static const di_row *row_of(uint32_t t, uint32_t slot) {
    if (t >= type_count()) return NULL;
    for (const di_row *p = g_types[t].rows; p->name; p++)
        if (p->slot == slot) return p;
    return NULL;
}

/* --------------------------------------------------------- the native */

static void bump(uint8_t answered, int by) {
    uint32_t *f;
    switch (answered) {
    case SA2_DI_ANS_REFUSED: f = &g_st.refused;    break;
    case SA2_DI_ANS_NOOP:    f = &g_st.noop;       break;
    case SA2_DI_ANS_GUEST:   f = &g_st.from_guest; break;
    case SA2_DI_ANS_OBJECT:  f = &g_st.object;     break;
    case SA2_DI_ANS_IDLE:    f = &g_st.idle;       break;
    case SA2_DI_ANS_INVALID: f = &g_st.invalid;    break;
    case SA2_DI_ANS_PROFILE: f = &g_st.profile;    break;
    case SA2_DI_ANS_HOST:    f = &g_st.host;       break;
    default:                 f = &g_st.brk;        break;
    }
    *f = (uint32_t)((int)*f + by);
}

/* Reserved before the handler and settled after it: a handler that refuses
 * raises a trap and never comes back, and the call that stopped the run is the
 * one a reader most wants in the log. */
static void note_reserve(uint32_t t, uint32_t slot, uint32_t self, uint32_t caller,
                         const uint32_t *argv, uint32_t argc) {
    g_seq++;
    g_st.method_calls++;
    bump(SA2_DI_ANS_REFUSED, +1);
    g_cur_slot = NULL;
    if (t < SA2_DI_MAX_TYPES && slot < SA2_DI_MAX_SLOTS) {
        sa2_di_slot_stat *st = &g_slot[t][slot];
        if (!st->used) {
            st->used = 1;
            st->first_seq = g_seq;
            st->first_caller = caller;
            st->answered = SA2_DI_ANS_REFUSED;
            g_st.distinct_methods++;
            g_cur_slot = st;              /* only the first call owns the class */
        }
        st->calls++;
    }
    g_cur = NULL;
    if (g_log_used >= SA2_DI_LOG_MAX) { g_st.log_dropped++; return; }
    sa2_di_call *r = &g_log[g_log_used++];
    memset(r, 0, sizeof *r);
    r->seq = g_seq;
    r->caller = caller;
    r->self = self;
    r->type = (uint16_t)t;
    r->slot = (uint16_t)slot;
    r->answered = SA2_DI_ANS_REFUSED;
    r->depth = (uint8_t)(g_depth + 1u);
    r->argc = (uint8_t)(argc > 8u ? 8u : argc);
    for (uint32_t k = 0; k < r->argc; k++) r->arg[k] = argv[k];
    g_cur = r;
}

static void note_settle(uint32_t ret, const di_ctx *c) {
    bump(SA2_DI_ANS_REFUSED, -1);
    bump(c->answered, +1);
    if (g_cur) {
        g_cur->answered = c->answered;
        g_cur->ret = ret;
        g_cur->out0 = c->out0;
        g_cur->out1 = c->out1;
    }
    if (g_cur_slot) g_cur_slot->answered = c->answered;
    g_cur = NULL;
    g_cur_slot = NULL;
}

static void *dinput_native(void *s, uint32_t pc, void *m) {
    const uint32_t idx = (pc - SA2_DI_THUNK_BASE) / SA2_DI_THUNK_STRIDE;
    const uint32_t t = idx / SA2_DI_MAX_SLOTS, slot = idx % SA2_DI_MAX_SLOTS;
    const uint32_t esp = sa2_rt_get_gpr(s, SA2_ESP);
    uint32_t caller = 0;
    if (sa2_peek(esp, 4, &caller) != SA2_OK) caller = 0;

    if (t >= type_count()) {
        sa2_trap_note("dinput method thunk %#010x is past the %u fabricated interfaces",
                      pc, type_count());
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, pc);
    }
    const di_row *row = row_of(t, slot);
    uint32_t raw[16];
    memset(raw, 0, sizeof raw);
    /* An unnamed slot is never popped with a guessed argument count; `this` is
     * still read, because naming the object in the trap is the value of
     * getting here. */
    const uint32_t want = row ? (uint32_t)row->argc + 1u : 1u;
    for (uint32_t k = 0; k < want && k < 16u; k++) {
        if (sa2_peek(esp + 4u + 4u * k, 4, &raw[k]) != SA2_OK) {
            sa2_trap_note("%s::%s: argument %u at %#010x is outside the guest space",
                          g_types[t].name, row ? row->name : "an unnamed slot", k,
                          esp + 4u + 4u * k);
            sa2_trap_raise(SA2_TRAP_FAULT, esp + 4u + 4u * k);
        }
    }
    const uint32_t self = raw[0];

    if (!row) {
        note_reserve(t, slot, self, caller, raw + 1, 0);
        sa2_trap_note("%s vtable slot %u called from %#010x on %#010x: this runtime does not "
                      "name that slot, and popping a guessed argument count would corrupt "
                      "the caller's frame rather than answer wrongly",
                      g_types[t].name, slot, caller, self);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, pc);
    }
    if (!row->fn) {
        note_reserve(t, slot, self, caller, raw + 1, row->argc);
        sa2_trap_note("%s::%s called from %#010x on %#010x: %s", g_types[t].name, row->name,
                      caller, self,
                      "no site in the image calls it (tools/s4/dinput_surface.py), so this "
                      "runtime has no answer for it and native's is unmeasured");
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, pc);
    }

    di_ctx c;
    memset(&c, 0, sizeof c);
    c.s = s;
    c.pc = pc;
    c.self = self;
    c.obj = obj_of(self);
    c.a = raw + 1;
    c.caller = caller;
    c.answered = SA2_DI_ANS_NOOP;
    note_reserve(t, slot, self, caller, raw + 1, row->argc);
    if (!c.obj || c.obj->type != t || !c.obj->alive) {
        g_st.unknown_this++;
        sa2_trap_note("%s::%s called from %#010x on %#010x, which is no live %s this runtime "
                      "built: the caller's frame is out of step, or the object was released",
                      g_types[t].name, row->name, caller, self, g_types[t].name);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, pc);
    }
    c.d = (t == T_DIDEV && c.obj->dev < g_dev_used) ? &g_dev[c.obj->dev] : NULL;
    uint32_t ret;
    if (g_break == BRK_NOOUT && (row->flags & DF_OUT)) {
        ret = SA2_DI_OK;
        c.answered = SA2_DI_ANS_BREAK;
    } else {
        ret = row->fn(&c);
    }
    note_settle(ret, &c);
    sa2_rt_set_gpr(s, SA2_EAX, ret);
    /* The callee pops: the return address plus `this` plus the arguments. */
    sa2_rt_set_gpr(s, SA2_ESP, esp + 4u + 4u * want);
    return m;
}

/* --------------------------------------------------------- the build */

static uint32_t type_vtable(uint32_t t) {
    if (g_types[t].vt) return g_types[t].vt;
    if (!g_types_bound) {
        /* Registered on the first object, not at configuration: crt_probe
         * requires rows_after == rows_before + bound across
         * sa2_imports_config_from_env() (harness/crt_probe.c). Every slot of
         * every type gets a row, so an unnamed slot lands here and refuses by
         * name rather than jumping to a value nothing registered. */
        for (uint32_t i = 0; i < type_count(); i++)
            for (uint32_t k = 0; k < SA2_DI_MAX_SLOTS; k++) {
                const uint32_t va = SA2_DI_THUNK_BASE +
                                    (i * SA2_DI_MAX_SLOTS + k) * SA2_DI_THUNK_STRIDE;
                if (sa2_ft_add(va, dinput_native, "dinput-method", SA2_FN_NATIVE) < 0) {
                    sa2_trap_note("the function table refused the dinput method thunks");
                    sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_DI_THUNK_BASE);
                }
            }
        g_types_bound = 1;
    }
    const uint32_t vt = SA2_DI_VT_BASE + t * SA2_DI_VT_STRIDE;
    for (uint32_t k = 0; k < SA2_DI_MAX_SLOTS; k++)
        pk32(vt + 4u * k, SA2_DI_THUNK_BASE + (t * SA2_DI_MAX_SLOTS + k) * SA2_DI_THUNK_STRIDE);
    g_types[t].vt = vt;
    return vt;
}

uint32_t sa2_dinput_create8(const uint32_t *a, uint32_t caller) {
    g_st.create_calls++;
    g_st.create_last_hinst = a[0];
    g_st.create_last_version = a[1];
    g_st.create_last_caller = caller;
    di_ctx c;
    memset(&c, 0, sizeof c);
    c.pc = SA2_DI_THUNK_BASE;
    c.caller = caller;
    if (a[1] != DI8_VERSION)
        refuse(&c, "DINPUT8", "DirectInput8Create",
               "dwVersion is not 0x800, the value the one site pushes at 0x00864D1F");
    uint8_t iid[16];
    pen(a[2], iid, 16);
    if (memcmp(iid, IID_DI8A, 16))
        refuse(&c, "DINPUT8", "DirectInput8Create",
               "riidltf is not IID_IDirectInput8A, the 16 bytes at 0x0087355C the one site "
               "pushes at 0x00864D1A");
    if (a[4])
        refuse(&c, "DINPUT8", "DirectInput8Create",
               "punkOuter is not 0, and the one site pushes 0 at 0x00864D14");
    if (!a[3])
        refuse(&c, "DINPUT8", "DirectInput8Create",
               "ppvOut is NULL, and the one site passes InputImpl+4 (0x00864D16..0x00864D19)");
    if (g_break == BRK_BARE_OK) {
        g_st.create_last_ret = SA2_DI_OK;
        return SA2_DI_OK;                   /* writes nothing: see BRK_BARE_OK */
    }
    di_obj *o = obj_new(T_DI8, SA2_DI_THUNK_BASE);
    pk32(a[3], o->addr);
    g_st.created++;
    g_st.create_last_ret = SA2_DI_OK;
    return SA2_DI_OK;
}

int sa2_dinput_device_enabled(void) { return g_enabled; }
const char *sa2_dinput_break_name(void) { return BREAK_NAMES[g_break]; }
const char *sa2_dinput_input_source(void) {
    return g_in.kind == IN_SCRIPT ? "script" : g_in.kind == IN_FIFO ? "fifo" : "";
}
void sa2_dinput_input_stats_get(sa2_di_input_stats *out) {
    if (!out) return;
    *out = g_in.st;
    out->stop_requested = g_in_stop ? 1u : 0u;
}

/* CreateDevice tells the devices apart by the GUID it is given: a system GUID
 * is the keyboard or the mouse, anything else the profile's controller with
 * that instance GUID. Both profile readers refuse a controller whose instance
 * GUID is a system device's or an earlier controller's
 * (runtime/sa2_devprofile.h), so no profile a run can load makes that
 * ambiguous, and the refusal lives there, once. This walk is the cheap check
 * that the reader and this file still agree: at most SA2_DP_MAX_GAMECTRL
 * controllers, once per run. */
static void check_profile(void) {
    const uint32_t n = sa2_dp_gamectrl_count();
    g_st.profile_gamectrl = n;
    for (uint32_t i = 0; i < n; i++) {
        const sa2_dp_didevice *p = sa2_dp_gamectrl(i);
        const char *clash = NULL;
        if (!p) clash = "has no record";
        else if (!memcmp(p->guid_instance, GUID_KEYBOARD, 16)
                 || !memcmp(p->guid_instance, GUID_MOUSE, 16))
            clash = "has a system device's GUID as its instance GUID";
        else
            for (uint32_t k = 0; k < i; k++) {
                const sa2_dp_didevice *q = sa2_dp_gamectrl(k);
                if (q && !memcmp(q->guid_instance, p->guid_instance, 16))
                    clash = "shares its instance GUID with an earlier controller";
            }
        if (clash) {
            sa2_trap_note("dinput: profile %s controller %u %s, which the profile reader "
                          "refuses: the reader and runtime/sa2_dinput.c disagree",
                          sa2_devprofile_source(), i, clash);
            sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_DI_THUNK_BASE);
        }
    }
}

void sa2_dinput_config_from_env(void) {
    const char *v = getenv("SA2_DINPUT_DEVICE");
    g_enabled = v && *v && *v != '0';
    /* Restore the published count first, so this is idempotent. */
    for (di_row *r = ROWS_DI8; r->name; r++) if (r->slot == 3u) r->argc = 3u;
    g_break = BRK_NONE;
    v = getenv("SA2_DINPUT_BREAK");
    if (v && *v) {
        for (int i = 1; i < (int)(sizeof BREAK_NAMES / sizeof BREAK_NAMES[0]); i++)
            if (!strcmp(v, BREAK_NAMES[i])) g_break = i;
        if (g_break == BRK_NONE) {
            sa2_trap_note("SA2_DINPUT_BREAK=%s is not a break this runtime has; the names are "
                          "argc, bare-ok, noout, nodevice, notacquired, inputlost, allkeys "
                          "and mousemove", v);
            sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_DI_THUNK_BASE);
        }
    }
    /* A capture's profile records what the native game's DirectInput answered.
     * With the device off, DirectInput8Create answers E_FAIL where that game
     * got a device (dinput-trace-v1a:108), so the lifted game skips the
     * enumeration (0x00864D45), the WMI query and the XInput scan (0x00864E29)
     * the profile recorded, and the run would carry the profile's sha256
     * without having reproduced it: refused, not approximated. Two breaks
     * approximate the same way with the device on: bare-ok writes no pointer,
     * which the game treats as the E_FAIL (the guard is the pointer), and
     * nodevice answers DIERR_DEVICENOTREG for the keyboard and mouse whatever
     * the profile recorded. The default profile stays legal with all three;
     * with the device off it is the before measurement (the DirectInput
     * design's section 9, configuration A). The notes lead with the switch,
     * because a trap note is cut to 255 bytes and a capture's path is long. */
    if (!sa2_devprofile_is_default()) {
        if (!g_enabled) {
            sa2_trap_note("dinput: SA2_DINPUT_DEVICE is off and device profile %s is a "
                          "capture's; DirectInput8Create would answer E_FAIL where that "
                          "capture's game got a device. Set SA2_DINPUT_DEVICE=1, or run the "
                          "default profile", sa2_devprofile_source());
            sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_DI_THUNK_BASE);
        }
        if (g_break == BRK_BARE_OK || g_break == BRK_NODEVICE) {
            sa2_trap_note("dinput: SA2_DINPUT_BREAK=%s is refused under device profile %s, a "
                          "capture's: %s", BREAK_NAMES[g_break], sa2_devprofile_source(),
                          g_break == BRK_BARE_OK ? "DirectInput8Create would write no device"
                                                 : "CreateDevice would refuse its system devices");
            sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_DI_THUNK_BASE);
        }
    }
    if (g_break == BRK_ARGC)
        for (di_row *r = ROWS_DI8; r->name; r++) if (r->slot == 3u) r->argc = 2u;
    check_profile();
    input_config();
}

/* ------------------------------------------------------------ readers */

void sa2_dinput_stats_get(sa2_di_stats *out) { if (out) *out = g_st; }
uint32_t           sa2_dinput_log_count(void) { return g_log_used; }
const sa2_di_call *sa2_dinput_log_at(uint32_t i) { return i < g_log_used ? &g_log[i] : NULL; }

uint32_t    sa2_dinput_type_count(void) { return type_count(); }
const char *sa2_dinput_type_name(uint32_t t) { return t < type_count() ? g_types[t].name : NULL; }
const char *sa2_dinput_method_name(uint32_t t, uint32_t slot) {
    const di_row *r = row_of(t, slot);
    return r ? r->name : NULL;
}
int sa2_dinput_method_argc(uint32_t t, uint32_t slot) {
    const di_row *r = row_of(t, slot);
    return r ? (int)r->argc : -1;
}
int sa2_dinput_method_implemented(uint32_t t, uint32_t slot) {
    const di_row *r = row_of(t, slot);
    return r && r->fn ? 1 : 0;
}
const char *sa2_dinput_method_flags(uint32_t t, uint32_t slot) {
    static char buf[16];
    const di_row *r = row_of(t, slot);
    buf[0] = 0;
    if (!r) return buf;
    if (r->flags & DF_OUT) strcat(buf, "out");
    if (r->flags & DF_CB) { if (buf[0]) strcat(buf, ","); strcat(buf, "callback"); }
    return buf;
}
const sa2_di_slot_stat *sa2_dinput_slot_stat_at(uint32_t t, uint32_t slot) {
    if (t >= SA2_DI_MAX_TYPES || slot >= SA2_DI_MAX_SLOTS) return NULL;
    return &g_slot[t][slot];
}

uint32_t             sa2_dinput_device_count(void) { return g_dev_used; }
const sa2_di_device *sa2_dinput_device_at(uint32_t i) { return i < g_dev_used ? &g_dev[i] : NULL; }
uint32_t             sa2_dinput_enum_count(void) { return g_enum_used; }
const sa2_di_enum   *sa2_dinput_enum_at(uint32_t i) { return i < g_enum_used ? &g_enum[i] : NULL; }
