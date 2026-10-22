/* ninja_diff: runtime/sa2_ninja_math.c's native bodies against the lifted double-mode bodies.
 *
 * Subtitle: links the lifted objects of 0x00426CC0 (njCalcPoint), 0x00424FF0 (njInvertMatrix,
 * with 0x00425350, the scale it calls), 0x00426E40 (njMultiMatrix), 0x0042AAB0 (njSin), 0x0042AC30
 * (njCos), 0x00427470 (njTranslate_) and 0x004274E0/0x004275B0/0x00427680 (njRotateX_/Y_/Z_), built by
 * tools/s8/perf_f80.py (or for Android by tools/s8/android_game.py) in the double mode, as
 * `__real_`, the runtime's native bodies as `__wrap_`, and runs each kernel on random and
 * edge-case States and guest memories: once with the native bodies off (`sa2_ninja_mode` 0, so
 * every call is the lifted body), once on, from the same bytes and in the same host rounding
 * mode. The whole State, every guest byte the kernel can reach (and margins around them) and the
 * return value must be equal. tools/s8/native_kernels/ninja_diff.py builds and runs it;
 * Documentation/scripts/tests/test_ninja_native.py holds its answers.
 *
 *   ninja_diff layout
 *   ninja_diff calcpoint|invert|multiply|sin|cos|translate|rotx|roty|rotz CASES SEED
 *   ninja_diff bench KERNEL CALLS
 *
 * Prints one JSON object. Per kernel, CASES ordinary cases (realistic values, values with edge
 * cases mixed in, and wild finite bit patterns; every precision control under every rounding
 * control, the host's mode the control word's RC or now and then another; segment bases zero and
 * not; random bytes in every register slot and flag; the guest's own aliasing, an output that is
 * an input, and njMultiMatrix's null pointers) and, beside them, CASES/16 cases of each guard
 * the kernel has, every one of which must take the lifted body: an infinity or NaN among the
 * inputs, an overlap the native order of reads and writes does not hold for, an access outside
 * the guest space, guest memory off its fast path, the bodies switched off, a stack offset used
 * through DS while the bases differ, and (njInvertMatrix) DF set. A build without the double
 * mode's marker (`--variant nomarker` in the driver) runs the same cases and must take the lifted
 * body every time. NINJA_DIFF_MASK gives the second run's kernel mask, NINJA_DIFF_TRACE prints
 * each case, NINJA_DIFF_DUMP every differing byte of a mismatch.
 */
#define _GNU_SOURCE
#include <fenv.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#ifdef _WIN32
#include <windows.h>
#else
#include <sys/mman.h>
#endif

#define STATE_BYTES 3504u
#define GUEST_END 0x20000000u
#define NULL_GUARD 0x1000u

/* ------------------------------------------------------------------ what the objects need */

uint8_t *sa2_guest_fast_base;
static uint8_t *G;
static uint8_t scratch[0x10010];

/* The checked path, as the runtime's: the same memory, or (outside the guest space, where the
 * runtime would trap) a scratch page both runs see alike. */
static int scratch_touched;
static uint8_t *slow(uint32_t a, uint32_t len) {
    if (a >= NULL_GUARD && a <= GUEST_END - len) return G + a;
    scratch_touched = 1;
    return scratch + (a & 0xFFFFu);
}
uint8_t *sa2_guest_read_slow(uint32_t a, uint32_t len) { return slow(a, len); }
uint8_t *sa2_guest_write_slow(uint32_t a, uint32_t len) { return slow(a, len); }
void *__remill_function_return(void *s, uint32_t pc, void *m) { (void)s; (void)pc; return m; }

/* ------------------------------------------------------------------ the runtime's side */

extern int sa2_ninja_mode;
extern uint64_t sa2_ninja_calls[][2];
extern uint64_t sa2_ninja_why[][7];
#define WHY_KINDS 7
struct field { const char *field; uint32_t offset; };
extern const struct field sa2_ninja_layout[];
extern const uint32_t sa2_ninja_layout_count;
int sa2_ninja_remill_offset(const char *field);
unsigned sa2_ninja_remill_state_size(void);
void *__wrap_sub_426cc0(void *, uint32_t, void *);
void *__wrap_sub_424ff0(void *, uint32_t, void *);
void *__wrap_sub_426e40(void *, uint32_t, void *);

static uint32_t off(const char *name) {
    for (uint32_t i = 0; i < sa2_ninja_layout_count; i++)
        if (!strcmp(sa2_ninja_layout[i].field, name)) return sa2_ninja_layout[i].offset;
    fprintf(stderr, "no field %s\n", name);
    exit(2);
}
static uint32_t O_CWD, O_SS, O_ES, O_DS, O_EAX, O_EBX, O_ECX, O_EDX, O_ESP, O_DF;

/* ------------------------------------------------------------------ random values */

static uint64_t rs;
static uint64_t rnd64(void) {
    uint64_t z = (rs += 0x9E3779B97F4A7C15ull);
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ull;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBull;
    return z ^ (z >> 31);
}
static uint32_t rnd32(void) { return (uint32_t)rnd64(); }
static uint32_t below(uint32_t n) { return (uint32_t)(((uint64_t)rnd32() * n) >> 32); }
static double unit(void) { return (double)(rnd64() >> 11) * 0x1p-53; }
static float fb(uint32_t b) { float f; memcpy(&f, &b, 4); return f; }

static float f_rot(void) { return (float)(unit() * 2.0 - 1.0); }               /* a rotation's */
static float f_scale(void) { return (float)(unit() * 4.0 - 2.0); }
static float f_pos(void) { return (float)((unit() * 2.0 - 1.0) * (below(2) ? 50.0 : 8000.0)); }
static float f_finite_bits(void) {
    uint32_t b;
    do b = rnd32(); while ((b & 0x7F800000u) == 0x7F800000u);
    return fb(b);
}
static float f_edge(void) {
    static const uint32_t e[] = {
        0x00000000u, 0x80000000u, 0x00800000u, 0x80800000u, 0x00000001u, 0x80000001u,
        0x007FFFFFu, 0x807FFFFFu, 0x7F7FFFFFu, 0xFF7FFFFFu, 0x3F800000u, 0xBF800000u,
        0x3F000000u, 0x33800000u, 0x4B000000u, 0x5F000000u, 0x1F800000u, 0x0D800000u,
        0x3F7FFFFFu, 0x3F800001u, 0x34000000u, 0xB3800000u, 0x1E000000u, 0x60000000u};
    const uint32_t r = below(4);
    if (r == 0) return fb(rnd32() & 0x807FFFFFu);                        /* a denormal or zero */
    if (r == 1) return fb((rnd32() & 0x807FFFFFu) | (below(2) ? 0x7F000000u : 0x00800000u));
    return fb(e[below(sizeof e / sizeof e[0])]);
}
static float f_nonfinite(void) {
    const uint32_t s = rnd32() & 0x80000000u, r = below(4);
    if (r == 0) return fb(s | 0x7F800000u);                               /* infinity */
    if (r == 1) return fb(s | 0x7FC00000u | (rnd32() & 0x3FFFFFu));       /* quiet NaN */
    return fb(s | 0x7F800000u | (1u + below(0x3FFFFFu)));                 /* signalling NaN */
}

/* A float at the edges of what the multiplying bodies take natively (zero, or a magnitude in
 * [2^-63, 2^63)): the ends of the range, a few ulps in, powers of two, both zeros. */
static float f_range_edge(void) {
    const uint32_t s = rnd32() & 0x80000000u, r = below(8);
    if (r == 0) return fb(s);                                               /* +-0 */
    if (r == 1) return fb(s | (64u << 23) | below(4));                      /* 2^-63, just above */
    if (r == 2) return fb(s | (189u << 23) | (0x7FFFFFu - below(4)));       /* just below 2^63 */
    if (r == 3) return fb(s | ((64u + below(126)) << 23));                  /* a power of two */
    if (r == 4) return fb(s | ((64u + below(126)) << 23) | (rnd32() & 0x7FFFFFu));
    if (r == 5) return fb(s | ((127u - 24u + below(48)) << 23) | (rnd32() & 0x7FFFFFu));
    return fb(s | (127u << 23) | (below(2) ? below(8) : 0x7FFFFFu - below(8)));   /* about 1 */
}

/* A field's value in the case's class: realistic, realistic with edges, wild finite bits, or
 * edges inside the native range. */
static int g_class;
#define N_CLASSES 4
static float f_val(float (*real)(void)) {
    const uint32_t r = below(100);
    if (g_class == 0) return real();
    if (g_class == 1) return r < 70 ? real() : r < 90 ? f_edge() : f_finite_bits();
    if (g_class == 2) return r < 30 ? real() : r < 60 ? f_edge() : f_finite_bits();
    return r < 40 ? real() : f_range_edge();
}
/* Class 3's cancellations: two products of a sum made equal and opposite, or one ulp apart. */
static void cancel(uint32_t pa, uint32_t pb, uint32_t qa, uint32_t qb) {
    if (g_class != 3 || below(3)) return;
    float a, b;
    memcpy(&a, G + pa, 4);
    memcpy(&b, G + pb, 4);
    uint32_t nb;
    memcpy(&nb, &b, 4);
    nb ^= 0x80000000u;
    if (below(2)) nb += below(2) ? 1u : 0xFFFFFFFFu;
    memcpy(G + qa, &a, 4);
    memcpy(G + qb, &nb, 4);
}

static void put32(uint8_t *p, uint32_t v) { memcpy(p, &v, 4); }
static void put16(uint8_t *p, uint16_t v) { memcpy(p, &v, 2); }
/* A guest write of the case's setup; past the guest space there is no guest byte, and the
 * lifted body reads the scratch page there instead. */
static int in_guest(uint32_t a, uint32_t len) { return a >= NULL_GUARD && a <= GUEST_END - len; }
static void putf(uint32_t a, float v) { if (in_guest(a, 4)) memcpy(G + a, &v, 4); }
static void put_word(uint32_t a, uint32_t v) { if (in_guest(a, 4)) memcpy(G + a, &v, 4); }
static void fill_random(uint32_t a, uint32_t len) {
    for (uint32_t i = 0; i < len; i += 4) {
        const uint32_t v = rnd32();
        memcpy(G + a + i, &v, len - i < 4 ? len - i : 4);
    }
}

/* ------------------------------------------------------------------ the cases' shape */

enum { G_NONE, G_NONFINITE, G_OVERLAP, G_RANGE, G_NOBASE, G_OFF, G_SEGMENT, G_DF, G_PRECISION,
       G_HOSTMODE, G_KINDS };
static const char *const GUARD_NAME[G_KINDS] = {"none", "nonfinite_input", "overlap",
                                                "outside_guest_space", "memory_off_fast_path",
                                                "switched_off", "segment_bases_differ",
                                                "direction_flag", "precision_not_24",
                                                "host_mode_not_rc"};

/* A control word. The ordinary cases: PC24 (the main thread's precision) under every rounding
 * control, the game's own words most often (0x007F the main thread's from CreateDevice, 0x0C7F
 * its truncating one), the other bits random now and then. The precision guard: 53 bits, 64 or
 * the reserved 01, under every rounding control (0x027F a new thread's). */
static int g_guard;
static uint16_t pick_cw(void) {
    uint16_t cw;
    const uint32_t r = below(100);
    if (r < 60) cw = below(2) ? 0x007F : 0x0C7F;
    else if (r < 90) cw = (uint16_t)(0x007Fu | (below(4) << 10));
    else cw = (uint16_t)(rnd32() & 0xFCFFu);
    if (g_guard == G_PRECISION) cw = (uint16_t)((cw & ~0x0300u) | ((1u + below(3)) << 8));
    return cw;
}

/* The host's rounding mode for a case: the control word's RC, as the runtime keeps it; for the
 * host-mode guard another (the lifted code and the native body read the same host mode, and a
 * native body that cannot round as the lifted code would there must hand the call over). */
static int host_mode(uint16_t cw) {
    static const int modes[4] = {FE_TONEAREST, FE_DOWNWARD, FE_UPWARD, FE_TOWARDZERO};
    const uint32_t rc = (cw >> 10) & 3u;
    return modes[g_guard == G_HOSTMODE ? (rc + 1u + below(3)) & 3u : rc];
}
static uint32_t pick_base(void) { return below(100) < 85 ? 0 : (below(0x40000) << (below(2) ? 2 : 0)); }

typedef struct { uint32_t lo, len; } win;
#define MAX_WIN 32
static win W[MAX_WIN];
static int nw;
static uint8_t *snap0[MAX_WIN], *snapA[MAX_WIN];
static uint32_t snap_cap[MAX_WIN];
static uint8_t scratchA[sizeof scratch];

static void add_win(uint32_t lo, uint32_t len) {
    if (lo < NULL_GUARD) { len = lo + len > NULL_GUARD ? lo + len - NULL_GUARD : 0; lo = NULL_GUARD; }
    if (lo >= GUEST_END || !len) return;
    if (lo + len > GUEST_END || lo + len < lo) len = GUEST_END - lo;
    W[nw].lo = lo;
    W[nw].len = len;
    if (snap_cap[nw] < len) {
        snap0[nw] = realloc(snap0[nw], len);
        snapA[nw] = realloc(snapA[nw], len);
        snap_cap[nw] = len;
    }
    nw++;
}
static void save(uint8_t **to) { for (int i = 0; i < nw; i++) memcpy(to[i], G + W[i].lo, W[i].len); }
static void restore(uint8_t **from) { for (int i = 0; i < nw; i++) memcpy(G + W[i].lo, from[i], W[i].len); }

static uint8_t S0[STATE_BYTES] __attribute__((aligned(16)));
static uint8_t SA[STATE_BYTES] __attribute__((aligned(16)));
static uint8_t SB[STATE_BYTES] __attribute__((aligned(16)));

static void state_random(uint8_t *s, uint16_t cw, uint32_t ssb, uint32_t dsb, uint32_t esb) {
    for (uint32_t i = 0; i < STATE_BYTES; i += 8) {
        const uint64_t v = rnd64();
        memcpy(s + i, &v, 8);
    }
    put16(s + O_CWD, cw);
    put32(s + O_SS, ssb);
    put32(s + O_DS, dsb);
    put32(s + O_ES, esb);
}

/* A field name for a State offset, for the report. */
static const char *where(uint32_t o, char *buf) {
    static const struct { const char *name; uint32_t width; } widths[] = {
        {"aflag.", 1}, {"seg.", 2}, {"addr.", 8}, {"gpr.", 8}, {"st.elems", 10}, {"sw.", 1},
        {"x87.fxsave.ip", 4}, {"x87.fxsave.dp", 4}, {"x87.fxsave.", 2}};
    const uint32_t st0 = off("st.elems[0].val");
    for (uint32_t i = 0; i < sa2_ninja_layout_count; i++) {
        uint32_t w = 1;
        for (unsigned k = 0; k < sizeof widths / sizeof widths[0]; k++)
            if (!strncmp(sa2_ninja_layout[i].field, widths[k].name, strlen(widths[k].name))) {
                w = widths[k].width;
                break;
            }
        if (o >= sa2_ninja_layout[i].offset && o < sa2_ninja_layout[i].offset + w) {
            snprintf(buf, 64, "%s+%u", sa2_ninja_layout[i].field, o - sa2_ninja_layout[i].offset);
            return buf;
        }
    }
    if (o >= st0 - 6 && o < st0 - 6 + 128) {
        snprintf(buf, 64, "st.elems[%u]+%u", (o - (st0 - 6)) / 16, (o - (st0 - 6)) % 16);
        return buf;
    }
    snprintf(buf, 64, "state+%u", o);
    return buf;
}

/* ------------------------------------------------------------------ one case */

typedef void *(*kernel_fn)(void *, uint32_t, void *);

typedef struct {
    uint64_t cases, native, mismatches;
    uint64_t native_pc24, native_pc53;
} tally;

static tally T[G_KINDS];
/* The ordinary cases by their values' class: realistic, edges mixed in, wild finite bits. */
static uint64_t by_class[N_CLASSES][2];
static const char *const CLASS_NAME[N_CLASSES] = {"realistic", "edges", "wild", "range_edges"};
/* The ordinary cases by the guest's path (the kernel's own names, from the case). */
static uint64_t by_path[4][2];
static int g_path;
static char first[512];
static char info[256];
static uint64_t case_no;
/* Why the ordinary cases the native bodies handed over went to the lifted body. */
static uint64_t why_ordinary[WHY_KINDS];
/* The kernels whose native bodies the second run turns on (NINJA_DIFF_MASK). */
static int nj_mask = 0x1FF;
/* `bench`: the case is set up and not run; the bench calls it. */
static int bench_setup_only;
static kernel_fn bench_fn;
static uint32_t bench_pc;

static void report_mismatch(const char *what) {
    if (!first[0]) snprintf(first, sizeof first, "case %llu: %s", (unsigned long long)case_no, what);
}

/* Runs the prepared case both ways and compares. `no_base`: guest memory off its fast path for
 * both runs. The scratch page is all zero when a case starts; a run that reached it (an access
 * outside the guest space, which the runtime would trap) has it compared and cleared. */
static void run_case(kernel_fn fn, int kernel, uint32_t pc, int guard) {
    if (bench_setup_only) {
        bench_fn = fn;
        bench_pc = pc;
        return;
    }
    void *const MEM = (void *)(uintptr_t)0x5A2Du;
    tally *t = &T[guard];
    t->cases++;
    save(snap0);
    sa2_guest_fast_base = guard == G_NOBASE ? NULL : G;
    uint16_t cw;
    memcpy(&cw, S0 + O_CWD, 2);
    const int mode = host_mode(cw);

    memcpy(SA, S0, STATE_BYTES);
    sa2_ninja_mode = 0;
    fesetround(mode);
    void *ra = fn(SA, pc, MEM);
    fesetround(FE_TONEAREST);
    save(snapA);
    const int touched_a = scratch_touched;
    if (touched_a) {
        memcpy(scratchA, scratch, sizeof scratch);
        memset(scratch, 0, sizeof scratch);
        scratch_touched = 0;
    }

    restore(snap0);
    memcpy(SB, S0, STATE_BYTES);
    sa2_ninja_mode = guard == G_OFF ? 0 : nj_mask;
    const uint64_t before = sa2_ninja_calls[kernel][0];
    uint64_t why_before[WHY_KINDS];
    memcpy(why_before, sa2_ninja_why[kernel], sizeof why_before);
    fesetround(mode);
    void *rb = fn(SB, pc, MEM);
    fesetround(FE_TONEAREST);
    const int native = sa2_ninja_calls[kernel][0] != before;
    if (guard == G_NONE)
        for (int w = 0; w < WHY_KINDS; w++) {
            const uint64_t d = sa2_ninja_why[kernel][w] - why_before[w];
            why_ordinary[w] += d;
            if (d && getenv("NINJA_DIFF_WHY") && g_class == 0)
                fprintf(stderr, "case %llu lifted (why %d, class %d, cw %04x): %s\n",
                        (unsigned long long)case_no, w, g_class, cw, info);
        }
    sa2_guest_fast_base = G;

    char buf[160], name[64];
    int bad = 0;
    if (ra != MEM || rb != MEM) {
        report_mismatch("the return value is not the memory pointer");
        bad = 1;
    }
    if (!bad && memcmp(SA, SB, STATE_BYTES)) {
        uint32_t o = 0;
        while (SA[o] == SB[o]) o++;
        snprintf(buf, sizeof buf, "State %s: lifted %02x native %02x (%s)", where(o, name), SA[o],
                 SB[o], info);
        report_mismatch(buf);
        bad = 1;
    }
    for (int i = 0; !bad && i < nw; i++)
        if (memcmp(snapA[i], G + W[i].lo, W[i].len)) {
            uint32_t o = 0;
            while (snapA[i][o] == G[W[i].lo + o]) o++;
            snprintf(buf, sizeof buf, "guest %#010x: lifted %02x native %02x (%s)", W[i].lo + o,
                     snapA[i][o], G[W[i].lo + o], info);
            report_mismatch(buf);
            bad = 1;
        }
    if (touched_a || scratch_touched) {
        if (!touched_a) memset(scratchA, 0, sizeof scratch);
        if (!bad && memcmp(scratchA, scratch, sizeof scratch)) {
            report_mismatch("the scratch page");
            bad = 1;
        }
        memset(scratch, 0, sizeof scratch);
        scratch_touched = 0;
    }
    if (bad && getenv("NINJA_DIFF_DUMP")) {
        fprintf(stderr, "case %llu guard %s native %d cw %04x mode %d: %s\n",
                (unsigned long long)case_no, GUARD_NAME[guard], native, cw, mode, info);
        for (uint32_t o = 0; o < STATE_BYTES; o++)
            if (SA[o] != SB[o]) fprintf(stderr, "  %s: in %02x lifted %02x native %02x\n", where(o, name), S0[o], SA[o], SB[o]);
        for (int i = 0; i < nw; i++)
            for (uint32_t o = 0; o < W[i].len; o++)
                if (snapA[i][o] != G[W[i].lo + o])
                    fprintf(stderr, "  guest %#010x: in %02x lifted %02x native %02x\n", W[i].lo + o,
                            snap0[i][o], snapA[i][o], G[W[i].lo + o]);
    }
    t->mismatches += bad;
    if (guard == G_NONE) {
        by_class[g_class][0]++;
        by_class[g_class][1] += native;
        by_path[g_path][0]++;
        by_path[g_path][1] += native;
    }
    if (native) {
        t->native++;
        if (S0[O_CWD + 1] & 0x03u) t->native_pc53++;
        else t->native_pc24++;
    }
}

static uint32_t addr_in(uint32_t lo, uint32_t span) {
    uint32_t a = lo + (below(span >> 2) << 2);
    if (below(16) == 0) a += 1 + below(3);                 /* a misaligned one now and then */
    return a;
}

/* ------------------------------------------------------------------ njCalcPoint 0x00426CC0 */

static const char *const CP_PATHS[4] = {"point", "vector", "in_place", "-"};

static void case_calcpoint(int guard) {
    g_class = (int)below(N_CLASSES);
    const uint16_t cw = pick_cw();
    const uint32_t ssb = pick_base(), dsb = pick_base();
    uint32_t ma = addr_in(0x01000000u, 0x100000u), va = addr_in(0x01200000u, 0x100000u);
    uint32_t oa = addr_in(0x01400000u, 0x100000u), e = addr_in(0x01600000u, 0x100000u);
    const uint32_t lay = below(10);
    if (lay < 2) oa = va;                                       /* in place */
    else if (lay == 2) oa = ma + 4 * below(9);                  /* onto the matrix */
    else if (lay == 3) va = ma + 4 * below(9);                  /* the input in the matrix */
    const int vector = below(2);
    if (guard == G_OVERLAP) {                                   /* a region on the frame */
        const uint32_t k = below(3), f = e - 0x0C - 0x2C + below(0x2C + 0x11);
        if (k == 0) ma = f;
        else if (k == 1) va = e - 0x0C - 0x08 + below(0x08 + 0x11);
        else oa = e - 0x0C - 0x08 + below(0x08 + 0x11);
    }
    if (guard == G_RANGE) {
        const uint32_t k = below(4);
        if (k == 0) ma = GUEST_END - 0x30 + 4 * (1 + below(11));
        else if (k == 1) va = GUEST_END - 0x0C + 4 * (1 + below(2));
        else if (k == 2) oa = GUEST_END - 0x0C + 4 * (1 + below(2));
        else e = GUEST_END - 4 * below(2);
    }
    nw = 0;
    add_win(ma - 0x10, 0x50);
    add_win(va - 0x10, 0x20);
    add_win(oa - 0x10, 0x20);
    add_win(e - 0x30, 0x50);
    for (int i = 0; i < nw; i++) fill_random(W[i].lo, W[i].len);
    for (int r = 0; r < 3; r++) {
        for (int c = 0; c < 3; c++) putf(ma + 16 * r + 4 * c, f_val(below(4) ? f_rot : f_scale));
        putf(ma + 16 * r + 12, f_val(f_pos));
    }
    for (int c = 0; c < 3; c++) putf(va + 4 * c, f_val(f_pos));
    /* m[4r+1]*v1 against m[4r]*v0: equal and opposite when the matrix's two are equal */
    if (lay >= 4 && in_guest(ma, 0x30) && in_guest(va, 0xC)) {
        const uint32_t r = below(3);
        cancel(ma + 16 * r, va, ma + 16 * r + 4, va + 4);
    }
    /* the flag: a byte at [esp+4], the rest of its dword whatever */
    put_word(e + 4, rnd32() & (vector ? 0xFFFFFFFFu : 0xFFFFFF00u));
    if (vector && in_guest(e + 4, 1) && G[e + 4] == 0) G[e + 4] = (uint8_t)(1 + below(255));
    if (guard == G_NONFINITE) {
        /* a value the guest reads: the matrix's 3x3, its translation (a point only), the input */
        const uint32_t k = below(vector ? 12 : 15);
        if (k < 9) putf(ma + 16 * (k / 3) + 4 * (k % 3), f_nonfinite());
        else if (k < 12) putf(va + 4 * (k - 9), f_nonfinite());
        else putf(ma + 16 * (k - 12) + 12, f_nonfinite());
    }
    state_random(S0, cw, ssb, dsb, pick_base());
    put32(S0 + O_EAX, ma - dsb);
    put32(S0 + O_ECX, va - dsb);
    put32(S0 + O_EDX, oa - dsb);
    put32(S0 + O_ESP, e - ssb);
    g_path = lay < 2 ? 2 : vector;
    snprintf(info, sizeof info, "m %#x v %#x o %#x e %#x ds %#x ss %#x vector %d", ma, va, oa, e,
             dsb, ssb, vector);
    run_case(__wrap_sub_426cc0, 0, 0x00426CC0u, guard);
}

/* ------------------------------------------------------------------ njMultiMatrix 0x00426E40 */

static const char *const MM_PATHS[4] = {"plain", "a_is_out", "b_is_out", "null"};

static void put_matrix34(uint32_t a) {
    for (int r = 0; r < 3; r++) {
        for (int c = 0; c < 3; c++) putf(a + 16 * r + 4 * c, f_val(below(4) ? f_rot : f_scale));
        putf(a + 16 * r + 12, f_val(f_pos));
    }
}

static void case_multiply(int guard) {
    g_class = (int)below(N_CLASSES);
    const uint16_t cw = pick_cw();
    uint32_t ssb = pick_base(), dsb = pick_base();
    uint32_t a = addr_in(0x01000000u, 0x100000u), b = addr_in(0x01200000u, 0x100000u);
    uint32_t o = addr_in(0x01400000u, 0x100000u), e = addr_in(0x01600000u, 0x100000u);
    const uint32_t lay = below(20);
    int null = 0;
    if (guard == G_NONE) {
        if (lay < 5) a = o;                                     /* A is the output */
        else if (lay < 10) b = o;                               /* B is the output */
        else if (lay < 12) a = b = o;                           /* both */
        else if (lay < 14) a = b;                               /* the same input twice */
        else if (lay == 14) null = 1 + (int)below(3);           /* a null pointer */
    }
    if (guard == G_SEGMENT) {                                   /* a copy with SS and DS apart */
        if (below(2)) a = o; else b = o;
        while (ssb == dsb) { ssb = below(0x40000) << 2; dsb = below(0x40000) << 2; }
    }
    if ((a == o || b == o) && guard != G_SEGMENT) ssb = dsb;   /* the copy needs them equal */
    if (guard == G_OVERLAP) {
        const uint32_t k = below(4);
        const uint32_t d = 4 * (1 + below(11));
        if (k == 0) a = below(2) ? o + d : o - d;               /* A partly the output */
        else if (k == 1) b = below(2) ? o + d : o - d;          /* B partly the output */
        else if (k == 2) o = e - 0x38 - 0x2C + 4 * below((0x2C + 0x3C) / 4);   /* on the frame */
        else a = e - 0x38 - 0x2C + 4 * below((0x2C + 0x3C) / 4);
    }
    if (guard == G_RANGE) {
        const uint32_t k = below(3);
        if (k == 0) a = GUEST_END - 0x30 + 4 * (1 + below(11));
        else if (k == 1) b = GUEST_END - 0x30 + 4 * (1 + below(11));
        else o = GUEST_END - 0x30 + 4 * (1 + below(11));
    }
    nw = 0;
    add_win(a - 0x10, 0x50);
    add_win(b - 0x10, 0x50);
    add_win(o - 0x10, 0x50);
    add_win(e - 0x50, 0x70);
    for (int i = 0; i < nw; i++) fill_random(W[i].lo, W[i].len);
    put_matrix34(o);
    put_matrix34(b);
    put_matrix34(a);
    if (a != b && a != o && b != o && in_guest(a, 0x30) && in_guest(b, 0x30)) {
        const uint32_t i = below(3), j = below(4);
        cancel(a + 16 * i, b + 4 * j, a + 16 * i + 4, b + 16 + 4 * j);
    }
    if (guard == G_NONFINITE) {
        const uint32_t k = below(24);
        putf((k < 12 ? a : b) + 4 * (k % 12), f_nonfinite());
    }
    state_random(S0, cw, ssb, dsb, pick_base());
    put32(S0 + O_EAX, null == 1 ? 0 : a - dsb);
    put32(S0 + O_ECX, null == 2 ? 0 : b - dsb);
    put32(S0 + O_EDX, null == 3 ? 0 : o - dsb);
    put32(S0 + O_ESP, e - ssb);
    g_path = null ? 3 : a == o ? 1 : b == o ? 2 : 0;
    snprintf(info, sizeof info, "a %#x b %#x o %#x e %#x ds %#x ss %#x null %d", a, b, o, e, dsb,
             ssb, null);
    run_case(__wrap_sub_426e40, 2, 0x00426E40u, guard);
}

/* ------------------------------------------------------------------ njInvertMatrix 0x00424FF0 */

static const char *const INV_PATHS[4] = {"inverse", "identity", "-", "-"};
#define K_ZERO 0x008AF004u
#define K_EPS 0x0089E8B4u
#define K_IDENTITY 0x01AF1A50u

/* A 4x4 matrix as the game keeps one: a rotation times a scale in the 3x3, a translation in the
 * last row, the last column (0, 0, 0, 1); or singular, or tiny (the identity path), or values. */
static int put_matrix44(uint32_t a, int kind) {
    static const float zero = 0.0f;
    const int k = kind >= 0 ? kind : (int)below(10);
    for (int r = 0; r < 4; r++)
        for (int c = 0; c < 4; c++) {
            float v;
            if (k == 5) v = r == c ? 2.0f + f_rot() * 0.25f : r == 3 ? f_pos() : f_rot() * 0.25f;
            else if (k == 0) v = zero;                                   /* singular */
            else if (k == 1) v = (float)(f_rot() * 1e-3);                /* |det| tiny */
            else if (r == 3) v = c == 3 ? 1.0f : f_val(f_pos);
            else if (c == 3) v = below(8) ? 0.0f : f_val(f_rot);
            else v = f_val(below(3) ? f_rot : f_scale);
            putf(a + 16 * r + 4 * c, v);
        }
    if (k == 2) {                                                        /* two rows the same */
        for (int c = 0; c < 4; c++) {
            float v;
            memcpy(&v, G + a + 4 * c, 4);
            if (in_guest(a + 16 + 4 * c, 4)) putf(a + 16 + 4 * c, v);
        }
    }
    return k;
}

static void case_invert(int guard) {
    g_class = (int)below(N_CLASSES);
    const uint16_t cw = pick_cw();
    uint32_t ssb = pick_base(), dsb = pick_base(), esb = below(4) ? dsb : pick_base();
    if (guard != G_SEGMENT) ssb = dsb;          /* the inverse reads its frame through DS */
    /* `dst` is the output's linear address; EBX is it less ES's base. */
    uint32_t mi = addr_in(0x01000000u, 0x100000u), dst = addr_in(0x01200000u, 0x100000u);
    uint32_t e = addr_in(0x01600000u, 0x100000u);
    const uint32_t lay = below(10);
    if (lay < 2) dst = mi;                                      /* in place */
    int kind = -1;
    if (guard == G_SEGMENT) {                                   /* the inverse with SS, DS apart */
        while (ssb == dsb) { ssb = below(0x40000) << 2; dsb = below(0x40000) << 2; }
        kind = 5;                                               /* |det| well above FLT_EPSILON */
        g_class = 0;
    }
    if (guard == G_OVERLAP) {
        const uint32_t k = below(2);
        if (k == 0) mi = e - 0x84 - 0x3C + 4 * below((0x3C + 0x88) / 4);
        else dst = e - 0x84 - 0x3C + 4 * below((0x3C + 0x88) / 4);
    }
    if (guard == G_RANGE) {
        if (below(2)) mi = GUEST_END - 0x40 + 4 * (1 + below(15));
        else dst = GUEST_END - 0x40 + 4 * (1 + below(15));
    }
    /* The image's constants and the identity, where the segment base puts them. */
    const uint32_t kz = dsb + K_ZERO, ke = dsb + K_EPS, id = dsb + K_IDENTITY;
    nw = 0;
    add_win(mi - 0x10, 0x60);
    add_win(dst - 0x10, 0x60);
    add_win(e - 0xA0, 0xB8);
    /* With SS and DS apart, the guest scales and copies whatever DS finds at the frame's
     * offsets: those bytes too. */
    if (ssb != dsb) add_win(dsb + (e - ssb) - 0xA0, 0xB8);
    add_win(kz - 8, 0x10);
    add_win(ke - 8, 0x10);
    add_win(id - 0x10, 0x60);
    for (int i = 0; i < nw; i++) fill_random(W[i].lo, W[i].len);
    put_matrix44(id, 4);
    if (below(2)) for (int i = 0; i < 16; i++) putf(id + 4 * i, i % 5 ? 0.0f : 1.0f);
    /* the game's 0.0f and FLT_EPSILON; in the edge and wild classes now and then another */
    putf(kz, g_class == 0 || below(10) ? 0.0f : below(2) ? -0.0f : f_val(f_rot));
    putf(ke, g_class == 0 || below(10) ? 0x1p-23f : f_val(f_rot));
    kind = put_matrix44(mi, kind);
    if (guard == G_NONFINITE) {
        const uint32_t k = below(18);
        if (k < 16) putf(mi + 4 * k, f_nonfinite());
        else putf(k == 16 ? kz : ke, f_nonfinite());
    }
    state_random(S0, cw, ssb, dsb, esb);
    S0[O_DF] = guard == G_DF ? (uint8_t)(1 + below(255)) : 0;
    put32(S0 + O_ECX, mi - dsb);
    put32(S0 + O_EBX, dst - esb);
    put32(S0 + O_ESP, e - ssb);
    g_path = kind <= 1 ? 1 : 0;
    snprintf(info, sizeof info, "m %#x dst %#x e %#x ds %#x ss %#x es %#x kind %d", mi, dst,
             e, dsb, ssb, esb, kind);
    run_case(__wrap_sub_424ff0, 1, 0x00424FF0u, guard);
}

/* ------------------------------------------------------------------ the trig leaves' guest data */

/* njSin's and njCos's quarter-wave table (401h floats at 0x008ACF88, read through DS) and the
 * interpolation's 0.0625 (a qword at 0x009057A8). */
#define K_TABLE 0x008ACF88u
#define K_TABLE_N 0x401u
#define K_SIXTEENTH 0x009057A8u
static float sine_table[K_TABLE_N];

static void sine_table_init(void) {
    for (uint32_t k = 0; k < K_TABLE_N; k++) sine_table[k] = (float)sin((double)k * 3.14159265358979323846 / 2048.0);
}

/* The table index a BAMS angle reads first, and the step to the next entry, by the quadrant's
 * symmetry: sin is +i, 800h-i, i-800h, 1000h-i; cos 400h-i, i-400h, 0C00h-i, i-0C00h. */
static void table_entry(int cosine, uint32_t angle, uint32_t *k, int *step) {
    const uint32_t i = (angle >> 4) & 0xFFFu, q = i >> 10;
    static const uint32_t off[2][4] = {{0, 0x800, 0x800, 0x1000}, {0x400, 0x400, 0xC00, 0xC00}};
    static const int back[2][4] = {{0, 1, 0, 1}, {1, 0, 1, 0}};
    *step = back[cosine][q] ? -1 : 1;
    *k = back[cosine][q] ? off[cosine][q] - i : i - off[cosine][q];
}

static float f_val_of(float real) {
    const uint32_t r = below(100);
    if (g_class == 0) return real;
    if (g_class == 1) return r < 70 ? real : r < 90 ? f_edge() : f_finite_bits();
    if (g_class == 2) return r < 30 ? real : r < 60 ? f_edge() : f_finite_bits();
    return r < 40 ? real : f_range_edge();
}

/* The table and the constant where DS puts them, realistic, with the two entries `angle` reads
 * (for njSin and njCos) in the case's value class; in the edge and wild classes the constant is
 * now and then another value. */
static void put_trig_data(uint32_t dsb, uint32_t angle) {
    const uint32_t t = dsb + K_TABLE;
    if (in_guest(t, 4 * K_TABLE_N)) memcpy(G + t, sine_table, sizeof sine_table);
    else for (uint32_t k = 0; k < K_TABLE_N; k++) putf(t + 4 * k, sine_table[k]);
    for (int c = 0; c < 2; c++) {
        uint32_t k;
        int step;
        table_entry(c, angle, &k, &step);
        putf(t + 4 * k, f_val_of(sine_table[k]));
        if (k + (uint32_t)step < K_TABLE_N) putf(t + 4 * (k + (uint32_t)step), f_val_of(sine_table[k + (uint32_t)step]));
    }
    const uint64_t sixteenth = 0x3FB0000000000000ull;
    const uint64_t v = g_class && below(20) == 0 ? rnd64() : sixteenth;
    if (in_guest(dsb + K_SIXTEENTH, 8)) memcpy(G + dsb + K_SIXTEENTH, &v, 8);
}

/* An angle: BAMS in 16 bits, the upper bits whatever now and then; a third on a table entry (the
 * low four bits zero); the quadrants' edges. */
static uint32_t pick_angle(void) {
    static const uint32_t e[] = {0, 1, 0xF, 0x10, 0x3FF0, 0x3FFF, 0x4000, 0x4001, 0x7FFF, 0x8000,
                                 0x8010, 0xBFFF, 0xC000, 0xFFF0, 0xFFFF, 0x10000, 0xFFFFFFFFu,
                                 0x80000000u, 0x7FFFFFFFu, 0xFFFF0000u};
    const uint32_t r = below(100);
    uint32_t a;
    if (r < 10) a = e[below(sizeof e / sizeof e[0])];
    else a = below(4) ? below(0x10000) : rnd32();
    if (r >= 10 && below(3) == 0) a &= ~0xFu;
    return a;
}

/* An entry of the table the case reads, or the constant, made non-finite (for an interpolating
 * angle either entry, else the first). */
static void trig_nonfinite(uint32_t dsb, uint32_t angle, int cosine) {
    uint32_t k;
    int step;
    table_entry(cosine, angle, &k, &step);
    if ((angle & 0xFu) && below(2)) k += (uint32_t)step;
    putf(dsb + K_TABLE + 4 * k, f_nonfinite());
}

/* ------------------------------------------------------------------ njSin 0x0042AAB0, njCos 0x0042AC30 */

static const char *const SC_PATHS[4] = {"table", "interpolated", "-", "-"};
void *__wrap_sub_42aab0(void *, uint32_t, void *);
void *__wrap_sub_42ac30(void *, uint32_t, void *);

static void case_trig(int guard, int cosine) {
    g_class = (int)below(N_CLASSES);
    const uint16_t cw = pick_cw();
    const uint32_t ssb = pick_base();
    uint32_t dsb = pick_base();
    uint32_t angle = pick_angle();
    if (guard == G_NONFINITE && below(2)) angle |= 1u + below(15);
    uint32_t e = addr_in(0x01600000u, 0x100000u) & ~3u;
    uint32_t k;
    int step;
    table_entry(cosine, angle, &k, &step);
    if (guard == G_OVERLAP) {                       /* an entry the body reads in its frame */
        const uint32_t kk = (angle & 0xFu) && below(2) ? k + (uint32_t)step : k;
        e = dsb + K_TABLE + 4 * kk + 4 + 4 * below(3);
    }
    if (guard == G_RANGE) {
        if (below(2)) e = GUEST_END + 4 * below(2);
        else dsb = GUEST_END + 4 * below(2) - K_TABLE - 4 * k;   /* the entry past the end */
    }
    nw = 0;
    add_win(e - 0x20, 0x30);
    for (int i = 0; i < nw; i++) fill_random(W[i].lo, W[i].len);
    put_trig_data(dsb, angle);
    if (guard == G_NONFINITE) trig_nonfinite(dsb, angle, cosine);
    state_random(S0, cw, ssb, dsb, pick_base());
    put32(S0 + O_ECX, angle);
    put32(S0 + O_ESP, e - ssb);
    g_path = (angle & 0xFu) != 0;
    snprintf(info, sizeof info, "angle %#x e %#x ds %#x ss %#x", angle, e, dsb, ssb);
    run_case(cosine ? __wrap_sub_42ac30 : __wrap_sub_42aab0, cosine ? 4 : 3,
             cosine ? 0x0042AC30u : 0x0042AAB0u, guard);
}
static void case_sin(int guard) { case_trig(guard, 0); }
static void case_cos(int guard) { case_trig(guard, 1); }

/* ------------------------------------------------------------------ njTranslate_ 0x00427470 */

static const char *const TR_PATHS[4] = {"moved", "null", "-", "-"};
void *__wrap_sub_427470(void *, uint32_t, void *);

static void case_translate(int guard) {
    g_class = (int)below(N_CLASSES);
    const uint16_t cw = pick_cw();
    const uint32_t ssb = pick_base(), dsb = pick_base();
    uint32_t ma = addr_in(0x01000000u, 0x100000u), e = addr_in(0x01600000u, 0x100000u);
    const int null = guard == G_NONE && below(8) == 0;
    if (guard == G_RANGE) {
        if (below(2)) ma = GUEST_END - 0x30 + 4 * (1 + below(11));
        else e = GUEST_END - 0x10 + 4 * (1 + below(3));
    }
    nw = 0;
    add_win(ma - 0x10, 0x50);
    add_win(e - 0x10, 0x30);
    for (int i = 0; i < nw; i++) fill_random(W[i].lo, W[i].len);
    put_matrix34(ma);
    for (int c = 1; c <= 3; c++) putf(e + 4 * c, f_val(f_pos));
    if (guard == G_NONFINITE) {
        const uint32_t k = below(15);
        if (k < 12) putf(ma + 4 * k, f_nonfinite());
        else putf(e + 4 * (k - 11), f_nonfinite());
    }
    state_random(S0, cw, ssb, dsb, pick_base());
    put32(S0 + O_EAX, null ? 0 : ma - dsb);
    put32(S0 + O_ESP, e - ssb);
    g_path = null;
    snprintf(info, sizeof info, "m %#x e %#x ds %#x ss %#x null %d", ma, e, dsb, ssb, null);
    run_case(__wrap_sub_427470, 5, 0x00427470u, guard);
}

/* ------------------------------------------------------------------ njRotateX_/Y_/Z_ */

static const char *const RT_PATHS[4] = {"rotated", "skipped", "-", "-"};
void *__wrap_sub_4274e0(void *, uint32_t, void *);
void *__wrap_sub_4275b0(void *, uint32_t, void *);
void *__wrap_sub_427680(void *, uint32_t, void *);

static void case_rotate(int guard, int axis) {
    static kernel_fn const fn[3] = {__wrap_sub_4274e0, __wrap_sub_4275b0, __wrap_sub_427680};
    static const uint32_t va[3] = {0x004274E0u, 0x004275B0u, 0x00427680u};
    g_class = (int)below(N_CLASSES);
    const uint16_t cw = pick_cw();
    uint32_t ssb = pick_base(), dsb = pick_base(), esb = ssb;
    uint32_t ma = addr_in(0x01000000u, 0x100000u), e = addr_in(0x01600000u, 0x100000u) & ~3u;
    uint32_t angle = pick_angle();
    int skip = 0;
    if (guard == G_NONE && below(8) == 0) {
        skip = 1;
        if (below(2)) ma = 0; else angle = 0;
    }
    if (guard != G_NONE && angle == 0) angle = 1 + below(0xFFFF);
    if (guard == G_SEGMENT) while (esb == ssb) esb = below(0x40000) << 2;
    if (guard == G_OVERLAP) ma = e - 0x54 - 0x2C + 4 * below((0x2C + 0x5C) / 4);
    if (guard == G_RANGE) {
        if (below(2)) ma = GUEST_END - 0x30 + 4 * (1 + below(11));
        else e = GUEST_END - 4 * below(2);
    }
    nw = 0;
    if (ma) add_win(ma - 0x10, 0x50);
    add_win(e - 0x70, 0x80);
    for (int i = 0; i < nw; i++) fill_random(W[i].lo, W[i].len);
    put_trig_data(dsb, angle);
    if (ma) put_matrix34(ma);
    put_word(e + 4, angle);
    if (guard == G_NONFINITE) {
        if (below(2)) putf(ma + 4 * below(12), f_nonfinite());
        else trig_nonfinite(dsb, angle, (int)below(2));
    }
    state_random(S0, cw, ssb, dsb, esb);
    S0[O_DF] = guard == G_DF ? (uint8_t)(1 + below(255)) : 0;
    put32(S0 + O_EBX, ma ? ma - dsb : 0);
    put32(S0 + O_ESP, e - ssb);
    g_path = skip;
    snprintf(info, sizeof info, "m %#x e %#x angle %#x ds %#x ss %#x es %#x", ma, e, angle, dsb, ssb, esb);
    run_case(fn[axis], 6 + axis, va[axis], guard);
}
static void case_rotx(int guard) { case_rotate(guard, 0); }
static void case_roty(int guard) { case_rotate(guard, 1); }
static void case_rotz(int guard) { case_rotate(guard, 2); }

/* ------------------------------------------------------------------ main */

static double now_ns(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec * 1e9 + t.tv_nsec;
}

/* `bench KERNEL CALLS`: one realistic case at the main thread's control word, called CALLS times
 * with the native bodies off and then on (ESP put back each time); ns per call each way, and how
 * many calls took the native body. */
static int bench(const char *k, uint64_t calls) {
    void (*one)(int);
    int ki;
    if (!strcmp(k, "calcpoint")) { one = case_calcpoint; ki = 0; }
    else if (!strcmp(k, "invert")) { one = case_invert; ki = 1; }
    else if (!strcmp(k, "multiply")) { one = case_multiply; ki = 2; }
    else if (!strcmp(k, "sin")) { one = case_sin; ki = 3; }
    else if (!strcmp(k, "cos")) { one = case_cos; ki = 4; }
    else if (!strcmp(k, "translate")) { one = case_translate; ki = 5; }
    else if (!strcmp(k, "rotx")) { one = case_rotx; ki = 6; }
    else if (!strcmp(k, "roty")) { one = case_roty; ki = 7; }
    else if (!strcmp(k, "rotz")) { one = case_rotz; ki = 8; }
    else return 2;
    double ns[2];
    uint64_t native = 0;
    for (int mode = 0; mode < 2; mode++) {
        rs = 7;
        do {
            bench_setup_only = 1;
            one(G_NONE);
            bench_setup_only = 0;
        } while (g_class != 0 || g_path != 0);
        put16(S0 + O_CWD, 0x007F);
        put32(S0 + O_SS, 0);
        put32(S0 + O_DS, 0);
        put32(S0 + O_ES, 0);
        memcpy(SA, S0, STATE_BYTES);
        uint32_t esp0;
        memcpy(&esp0, S0 + O_ESP, 4);
        uint32_t regs[4];
        memcpy(&regs[0], S0 + O_EAX, 4);
        memcpy(&regs[1], S0 + O_ECX, 4);
        memcpy(&regs[2], S0 + O_EDX, 4);
        memcpy(&regs[3], S0 + O_EBX, 4);
        sa2_ninja_mode = mode ? 0x1FF : 0;
        const uint64_t before = sa2_ninja_calls[ki][0];
        const double t0 = now_ns();
        for (uint64_t c = 0; c < calls; c++) {
            put32(SA + O_ESP, esp0);
            put32(SA + O_EAX, regs[0]);
            put32(SA + O_ECX, regs[1]);
            put32(SA + O_EDX, regs[2]);
            bench_fn(SA, bench_pc, (void *)1);
        }
        ns[mode] = (now_ns() - t0) / (double)calls;
        if (mode) native = sa2_ninja_calls[ki][0] - before;
    }
    printf("{\"kernel\": \"%s\", \"calls\": %llu, \"ns_lifted\": %.1f, \"ns_native\": %.1f, \"native_calls\": %llu}\n",
           k, (unsigned long long)calls, ns[0], ns[1], (unsigned long long)native);
    return 0;
}

static int layout(void) {
    int bad = 0;
    printf("{\"state_bytes\": %u, \"fields\": {", sa2_ninja_remill_state_size());
    for (uint32_t i = 0; i < sa2_ninja_layout_count; i++) {
        const int want = sa2_ninja_remill_offset(sa2_ninja_layout[i].field);
        printf("%s\"%s\": [%u, %d]", i ? ", " : "", sa2_ninja_layout[i].field,
               sa2_ninja_layout[i].offset, want);
        bad |= want < 0 || (uint32_t)want != sa2_ninja_layout[i].offset;
    }
    printf("}, \"ok\": %s}\n", !bad && sa2_ninja_remill_state_size() == STATE_BYTES ? "true" : "false");
    return 0;
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: ninja_diff layout | calcpoint|invert|multiply CASES SEED | bench KERNEL CALLS\n");
        return 2;
    }
    O_CWD = off("x87.fxsave.cwd"); O_SS = off("addr.ss_base"); O_ES = off("addr.es_base");
    O_DS = off("addr.ds_base"); O_EAX = off("gpr.rax"); O_EBX = off("gpr.rbx");
    O_ECX = off("gpr.rcx"); O_EDX = off("gpr.rdx"); O_ESP = off("gpr.rsp"); O_DF = off("aflag.df");
    if (!strcmp(argv[1], "layout")) return layout();
    if (argc < 4) return 2;
    /* The guest space and 64 KiB past it, where a case that reaches past the end has its setup
     * written (the guest's accesses there take the checked path, to the scratch page). */
#ifdef _WIN32
    G = VirtualAlloc(NULL, GUEST_END + 0x10000u, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    if (!G) { fprintf(stderr, "VirtualAlloc failed\n"); return 2; }
#else
    G = mmap(NULL, GUEST_END + 0x10000u, PROT_READ | PROT_WRITE,
             MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
    if (G == MAP_FAILED) { perror("mmap"); return 2; }
#endif
    sa2_guest_fast_base = G;
    sine_table_init();
    fesetround(FE_TONEAREST);
    if (!strcmp(argv[1], "bench")) return bench(argv[2], strtoull(argv[3], NULL, 0));
    const uint64_t ordinary = strtoull(argv[2], NULL, 0);
    rs = strtoull(argv[3], NULL, 0);
    void (*one)(int);
    const char *const *paths;
    /* The guards each kernel has. */
    static const int cp_guards[] = {G_NONFINITE, G_OVERLAP, G_RANGE, G_NOBASE, G_OFF,
                                    G_PRECISION, G_HOSTMODE};
    static const int mm_guards[] = {G_NONFINITE, G_OVERLAP, G_RANGE, G_NOBASE, G_OFF, G_SEGMENT,
                                    G_PRECISION, G_HOSTMODE};
    static const int inv_guards[] = {G_NONFINITE, G_OVERLAP, G_RANGE, G_NOBASE, G_OFF, G_SEGMENT,
                                     G_DF, G_PRECISION, G_HOSTMODE};
    static const int tr_guards[] = {G_NONFINITE, G_RANGE, G_NOBASE, G_OFF, G_PRECISION, G_HOSTMODE};
    const int *guards;
    int nguards;
    if (!strcmp(argv[1], "calcpoint")) {
        one = case_calcpoint; paths = CP_PATHS; guards = cp_guards; nguards = 7;
    } else if (!strcmp(argv[1], "multiply")) {
        one = case_multiply; paths = MM_PATHS; guards = mm_guards; nguards = 8;
    } else if (!strcmp(argv[1], "invert")) {
        one = case_invert; paths = INV_PATHS; guards = inv_guards; nguards = 9;
    } else if (!strcmp(argv[1], "sin") || !strcmp(argv[1], "cos")) {
        one = argv[1][0] == 's' ? case_sin : case_cos; paths = SC_PATHS; guards = cp_guards; nguards = 7;
    } else if (!strcmp(argv[1], "translate")) {
        one = case_translate; paths = TR_PATHS; guards = tr_guards; nguards = 6;
    } else if (!strncmp(argv[1], "rot", 3) && strchr("xyz", argv[1][3]) && argv[1][3]) {
        one = argv[1][3] == 'x' ? case_rotx : argv[1][3] == 'y' ? case_roty : case_rotz;
        paths = RT_PATHS; guards = inv_guards; nguards = 9;
    } else return 2;
    const uint64_t nguard_cases = ordinary / 16 * (uint64_t)nguards;
    const int trace = getenv("NINJA_DIFF_TRACE") != NULL;
    if (getenv("NINJA_DIFF_MASK")) nj_mask = (int)strtol(getenv("NINJA_DIFF_MASK"), NULL, 0);
    for (case_no = 0; case_no < ordinary + nguard_cases; case_no++) {
        const int guard = case_no < ordinary ? G_NONE
                        : guards[(case_no - ordinary) % (uint64_t)nguards];
        if (trace) fprintf(stderr, "case %llu %s\n", (unsigned long long)case_no, GUARD_NAME[guard]);
        g_guard = guard;
        one(guard);
    }
    uint64_t mism = 0;
    printf("{\"kernel\": \"%s\", \"seed\": %s, \"classes\": {", argv[1], argv[3]);
    int firstk = 1;
    for (int k = 0; k < G_KINDS; k++) {
        mism += T[k].mismatches;
        if (!T[k].cases) continue;
        printf("%s\"%s\": {\"cases\": %llu, \"native\": %llu, \"native_pc24\": %llu, "
               "\"native_pc53_64\": %llu, \"mismatches\": %llu}", firstk ? "" : ", ", GUARD_NAME[k],
               (unsigned long long)T[k].cases, (unsigned long long)T[k].native,
               (unsigned long long)T[k].native_pc24, (unsigned long long)T[k].native_pc53,
               (unsigned long long)T[k].mismatches);
        firstk = 0;
    }
    printf("}, \"ordinary_by_values\": {");
    for (int c = 0; c < N_CLASSES; c++)
        printf("%s\"%s\": {\"cases\": %llu, \"native\": %llu}", c ? ", " : "", CLASS_NAME[c],
               (unsigned long long)by_class[c][0], (unsigned long long)by_class[c][1]);
    printf("}, \"ordinary_by_path\": {");
    firstk = 1;
    for (int p = 0; p < 4; p++) {
        if (paths[p][0] == '-') continue;
        printf("%s\"%s\": {\"cases\": %llu, \"native\": %llu}", firstk ? "" : ", ", paths[p],
               (unsigned long long)by_path[p][0], (unsigned long long)by_path[p][1]);
        firstk = 0;
    }
    static const char *const WHY[WHY_KINDS] = {"off", "fpu", "memory", "overlap", "value",
                                               "segment", "df"};
    printf("}, \"ordinary_lifted_why\": {");
    for (int w = 0; w < WHY_KINDS; w++)
        printf("%s\"%s\": %llu", w ? ", " : "", WHY[w], (unsigned long long)why_ordinary[w]);
    printf("}, \"mismatches\": %llu, \"first_mismatch\": \"%s\"}\n", (unsigned long long)mism, first);
    return 0;
}
