/* SA2 recompiled runtime v0: guest memory (design 3.4 "Guest memory", 3.5
 * "Guest memory init" and the `sa2_peek` C ABI entry).
 *
 * One host allocation covers the guest 32-bit range the image and its arenas
 * use, 0x00000000..SA2_GUEST_END. Every guest pointer is `base + uint32`: the
 * host address of guest address `a` is `sa2_guest.base + a`. Nothing is mapped
 * at a fixed host address and the host link address is irrelevant, which is the
 * difference from the S1 spike harness (tools/spike/fixtures/guest.h) that
 * mapped the image at its own VA and linked its ELF at 0x10000000.
 *
 * The PE's sections are copied to their original VAs at init from the user's
 * own `private/exe/sonic2app-unpacked.exe`. The section table is parsed here,
 * in C, from that file; no part of the executable is committed or embedded.
 * The shipped `sonic2app.exe` is loaded instead when the host says so, with the
 * code-section overlay a release build links (below, "the shipped executable",
 * and runtime/sa2_exe_overlay.h).
 *
 * Layout (SA2_* constants below; the image end is checked against the heap
 * base at init):
 *
 *   0x00000000..0x00000FFF  null guard, every access rejected
 *   0x00001000..0x003FFFFF  low free
 *     0x00010000..0x001FFFFF  d3d9's overflow objects (sa2_d3d9.h)
 *     0x00200000..          import and interface method thunks (below)
 *   0x00400000..            PE headers and sections at their VAs (SizeOfImage)
 *   0x02800000..0x02FFFFFF  free; the guest heap was here until S4 slice 7
 *   0x03000000..0x03CFFFFF  scratch arena (the fixture harness's argument area)
 *     0x03100000..          fabricated COM objects (sa2_iface.h, sa2_d3d9.h)
 *     0x03300000..0x03CFFFFF  runtime arena (sa2_guest_alloc)
 *   0x03D00000..0x03EFFFFF  stack arena, stack top 0x03F00000 (grows down)
 *   0x03F00000..0x03FFFFFF  reserved (TEB/FS for S3's FS:[0] chain)
 *   0x04000000..0x1FFFFFFF  the guest's own heap (HeapAlloc, sa2_imports.c),
 *                           whose last 64 KiB is the runtime's string arena
 *
 * The heap is above the reserved region and not below the scratch arena
 * because it had to grow and nothing below it could move: S4 slice 7 measured
 * the guest asking for a single 20 971 520-byte block during initialisation
 * and a 10 487 380-byte one after it, against an arena of 7 929 856 bytes.
 * `operator new` turned the refusal into `std::bad_alloc`, which surfaced as
 * `RaiseException(0xE06D7363)` several frames away from the allocator. The
 * scratch, stack and TEB constants are pinned by the S2 fixture corpus, so
 * the space was taken at the top instead of by moving them.
 *
 * Permissions are recorded per section but not enforced in v0; see the
 * receipt's `limits`.
 */
#ifndef SA2_RUNTIME_GUEST_MEMORY_H
#define SA2_RUNTIME_GUEST_MEMORY_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define SA2_GUEST_END          0x20000000u
#define SA2_GUEST_NULL_GUARD   0x00001000u
/* The import thunk region (runtime/sa2_imports.c): one synthetic code address
 * per IAT slot, carved out of the low free space below the image. It is here
 * rather than in `sa2_imports.h` because it is part of the address-space
 * layout and `sa2_guest_region_of` has to name it.
 *
 * Requirements, in order of what they cost if broken: distinct per import;
 * never equal to a lifted function's entry; never inside the image, so a trap
 * carrying one is not fed back to the lifter by the trap fixpoint
 * (`tools/s2/corpus_run/close_traps.py` lifts any address in the image range);
 * and inside the guest space, so a guest that stores or compares one is
 * handling a real guest address.
 *
 * Not 0x00300000, although that is equally free: `tools/s2/patch/probes.py`
 * already means something by that constant in the native-probe universe, and
 * one address with two meanings is a defect this project keeps catching. */
#define SA2_GUEST_IMPORT_BASE   0x00200000u
#define SA2_GUEST_IMPORT_STRIDE 16u
/* Two 4 KiB pages at that stride. The low 256 are the static IAT (251 used);
 * the high 256 are the thunks `GetProcAddress` hands out, so a dynamically
 * resolved entry point is the same kind of object as a statically imported one
 * and `sa2_import_for_thunk` answers for both. The split is by *index*, not by
 * how many static imports there happen to be, so adding an import never
 * renumbers a dynamic thunk. */
#define SA2_GUEST_IMPORT_SLOTS  512u
#define SA2_GUEST_IMPORT_DYN_FIRST 256u
#define SA2_GUEST_IMPORT_END    (SA2_GUEST_IMPORT_BASE + SA2_GUEST_IMPORT_SLOTS * SA2_GUEST_IMPORT_STRIDE)
#define SA2_GUEST_IMAGE_BASE   0x00400000u
/* --------------------------------------------------- guest thread stacks
 *
 * G1's thread strategy: "each guest thread gets its own CPU-state struct and
 * guest stack". Thread 0 keeps the stack the fixture corpus pins
 * (SA2_GUEST_STACK_BASE..SA2_GUEST_STACK_TOP); every thread the guest creates
 * gets one slot here.
 *
 * This is the 8 MiB the guest heap vacated in S4 slice 7 -- named, not reused
 * by accident. The slot size is a *hard* bound rather than a hint: the
 * scheduler records each thread's low-water ESP and refuses one that leaves
 * its own slot, because the failure mode of a stack that quietly grows into
 * its neighbour is exactly the one slice 7 spent a day on (two writers, one
 * address, the symptom eleven thousand calls later).
 *
 * The first page of each slot is a guard the stack must never enter, so an
 * overflow is caught one page before it reaches the slot below. */
#define SA2_GUEST_TSTACK_BASE  0x02800000u
#define SA2_GUEST_TSTACK_END   0x03000000u
/* 512 KiB per guest thread, which makes sixteen slots out of the same 8 MiB.
 *
 * It was 1 MiB, the reservation `_beginthreadex` asks for, and that gave eight
 * slots and therefore eight worker threads -- **below the nine creation sites
 * G1 section 3 counted in the image**. With the CRI file system open the guest
 * asks for a ninth, `CreateThread` refuses, and the C++ thread wrapper at
 * 0x00865380 does not check: it calls `ResumeThread(NULL)` on the next line.
 *
 * Windows *reserves* 1 MiB and commits on demand, so the number that matters
 * is what a thread touches. Measured across both arms: 4 620 bytes is the
 * deepest any worker has reached, and 73 732 the main thread, which keeps its
 * own pinned stack and is not in this arena. 512 KiB is a hundred times the
 * observed high-water mark, and a thread that does exceed its slot is still
 * refused by name at the guard page rather than allowed into its neighbour --
 * so this trades reservation the guest never uses for slots it does. */
#define SA2_GUEST_TSTACK_SIZE  0x00080000u    /* 512 KiB per guest thread */
#define SA2_GUEST_TSTACK_GUARD 0x00001000u    /* the low page of a slot: never valid ESP */
#define SA2_GUEST_TSTACK_SLOTS ((SA2_GUEST_TSTACK_END - SA2_GUEST_TSTACK_BASE) \
                                / SA2_GUEST_TSTACK_SIZE)
/* The guest's own heap, which `runtime/sa2_imports.c` bases `HeapAlloc` on.
 * See the layout note above for why it is at the top of the guest space. */
#define SA2_GUEST_HEAP_BASE    0x04000000u
#define SA2_GUEST_HEAP_END     SA2_GUEST_END
#define SA2_GUEST_SCRATCH_BASE 0x03000000u
#define SA2_GUEST_SCRATCH_END  0x03D00000u
/* The runtime's own guest-memory bump allocator (`sa2_guest_alloc`), which is
 * where a host stub puts bytes the guest must be able to read and write: a
 * DirectSound buffer's backing store, for example. It is 10 MiB and never
 * frees, so a caller that makes and drops blocks for as long as the game runs
 * keeps its own: runtime/sa2_dsound.c hands a released buffer's block to the
 * next buffer of the same length ("the backing stores" there), since a story-mode
 * session filled this arena with stream buffers in 29 minutes on 2026-10-04.
 * The d3d9 lock backing store lived here too until 2026-09-22, when the
 * game's first loading screen filled it; it now comes from the guest heap's
 * own allocator (`sa2_heap_alloc_owned` in `runtime/sa2_imports.c`), which is
 * one allocator on that range, not the second one described next.
 *
 * It is here and not at SA2_GUEST_HEAP_BASE, **and the distance between the
 * two is the whole point**. Until S4 slice 7 this allocator bumped from
 * SA2_GUEST_HEAP_BASE, which is also `SA2_HEAP_ARENA_BASE` in
 * `runtime/sa2_imports.c` -- the arena the guest's own `HeapAlloc`, and
 * therefore its CRT `malloc`, allocates from. Two allocators on one address,
 * neither aware of the other. The recording d3d9 device then zero-filled
 * 3 686 400 bytes of lock backing store from 0x02800000 upward and erased the
 * guest's heap: every object the guest had built, its vtable pointer included,
 * and the arena's own block headers. The visible symptom was a null vtable
 * pointer in the renderer teardown eleven thousand calls later, and the heap
 * walk had been reporting `consistent: false` for two slices.
 *
 * It sits inside the scratch region deliberately, above everything named in
 * it (`SA2_IFACE_BASE` 0x03100000, `SA2_D3D9_OBJ_BASE` 0x03200000 plus
 * SA2_D3D9_OBJ_FIRST_COUNT*SA2_D3D9_OBJ_STRIDE bytes, 256 KiB, then d3d9's
 * vtables, dsound's objects and vtables, and the DirectInput, WMI and XAudio2
 * objects and vtables from `SA2_DI_OBJ_BASE` 0x03270000, runtime/sa2_dinput.h,
 * runtime/sa2_wbem.h and runtime/sa2_xaudio2.h), so `sa2_guest_region_of` keeps
 * answering "scratch" for it and no pinned region name moves.
 *
 * What makes each separation a fact rather than a comment differs. The static
 * assertions in `runtime/guest_memory.c` hold this arena clear of the guest
 * heap and stack; `runtime/sa2_d3d9.c`'s hold the d3d9 object table and
 * vtables below it, `runtime/sa2_dsound.c`'s the dsound ones, and
 * `runtime/sa2_dinput.c`'s, `runtime/sa2_wbem.c`'s and `runtime/sa2_xaudio2.c`'s
 * their own vtables. The
 * interface block at `SA2_IFACE_BASE` has no static assertion:
 * `Documentation/scripts/check_handle_regions.py` holds it clear of every
 * region `Documentation/handle-regions.json` declares, but this arena is not
 * declared there, so only the block's own value keeps it below here. */
#define SA2_GUEST_RT_ARENA_BASE 0x03300000u
#define SA2_GUEST_RT_ARENA_END  SA2_GUEST_SCRATCH_END
#define SA2_GUEST_STACK_BASE   0x03D00000u
#define SA2_GUEST_STACK_TOP    0x03F00000u
/* The TEB region's end, and therefore SA2_GUEST_TEB_SLOTS, is its own constant
 * rather than SA2_GUEST_END: growing the guest space must not silently grow
 * the number of thread environment blocks the runtime claims to have. */
#define SA2_GUEST_RESERVED_END 0x04000000u

/* ------------------------------------------------------------------ the TEB
 *
 * The reserved region above the stack holds guest thread environment blocks, so
 * that `FS:[0]` resolves to guest memory rather than to guest address 0. Before
 * this existed `sa2_cpu_state_reset` left `fs_base` at 0 and every one of the
 * image's 1098 decoded `fs:[0]` accesses landed in the null guard: the SEH
 * prologue of the first guest function anybody drove, `FUN_00406520`, trapped
 * SA2_TRAP_FAULT at 0x00000000 on its `mov eax, fs:[0]` before reaching the
 * switch the arm-seeding experiment was testing.
 *
 * Reserving the region is not enough, because the prologue *reads* the chain
 * head before it writes one: `push fs:[0]` then `mov fs:[0], esp`. A zeroed TEB
 * therefore pushes 0 as the next frame, which is a chain that does not
 * terminate. The TEB is initialised, not merely pointed at.
 *
 * Which fields matter is a census over all decoded code, not a guess: 1098
 * `fs:[0]` sites and exactly one `fs:[0x18]`, the TEB self-pointer read at
 * 0x0086c817 in `__ValidateEH3RN`, and nothing else. StackBase and StackLimit
 * are laid down too -- no decoded site reads them, but they cost two stores and
 * a TEB whose stack fields are zero is a TEB that lies about the stack.
 *
 * **One thread.** G1's thread strategy gives each guest thread its own TEB, and
 * this lays out one, for thread 0, which is what `sa2_guest_init` creates and
 * what `sa2_cpu_state_reset` points `fs_base` at. The region holds
 * SA2_GUEST_TEB_SLOTS of them and every entry point here takes a thread index,
 * so the general case is a call with a different index and a per-thread
 * `fs_base` at thread creation -- the S4 work, with a named place to put it,
 * rather than a layout to redesign.
 */
#define SA2_GUEST_TEB_BASE     SA2_GUEST_STACK_TOP   /* 0x03F00000 */
#define SA2_GUEST_TEB_SIZE     0x1000u
#define SA2_GUEST_TEB_SLOTS    ((SA2_GUEST_RESERVED_END - SA2_GUEST_TEB_BASE) / SA2_GUEST_TEB_SIZE)

/* TEB field offsets. Only the two the census found are read by guest code. */
#define SA2_TEB_EXCEPTION_LIST 0x00u   /* NT_TIB.ExceptionList, the SEH chain head */
#define SA2_TEB_STACK_BASE     0x04u   /* NT_TIB.StackBase, the high address */
#define SA2_TEB_STACK_LIMIT    0x08u   /* NT_TIB.StackLimit, the low address */
#define SA2_TEB_SELF           0x18u   /* NT_TIB.Self, read at 0x0086c817 */

/* The value that ends an SEH registration chain. A zero here is not "empty": it
 * is a next-record pointer to guest address 0. */
#define SA2_SEH_END_OF_CHAIN   0xFFFFFFFFu

#define SA2_MAX_SECTIONS 32

/* Errors returned by every bounds-checked entry point. */
#define SA2_OK            0
#define SA2_E_RANGE      -1  /* addr..addr+len leaves the guest space or hits the null guard */
#define SA2_E_INIT       -2  /* guest memory is not initialised */
#define SA2_E_IMAGE      -3  /* the PE could not be read or parsed */
#define SA2_E_NOMEM      -4
#define SA2_E_OVERLAY    -5  /* the shipped executable was asked for and refused (sa2_exe_overlay.h) */

typedef struct {
    char     name[9];
    uint32_t va;            /* guest virtual address */
    uint32_t virtual_size;
    uint32_t raw_size;      /* bytes copied from the file */
    uint32_t characteristics;
    int      readable, writable, executable;
} sa2_section_t;

typedef struct {
    uint8_t      *base;          /* host base; guest address a lives at base + a */
    uint32_t      size;          /* SA2_GUEST_END */
    uint32_t      image_base;    /* 0x00400000 */
    uint32_t      image_size;    /* SizeOfImage */
    uint32_t      rt_next;       /* bump pointer inside the runtime arena, never the guest heap */
    sa2_section_t sections[SA2_MAX_SECTIONS];
    unsigned      section_count;
    char          image_path[512];
    int           ready;
} sa2_guest_t;

extern sa2_guest_t sa2_guest;

/* Map the guest space and load the PE at `pe_path` (design 3.5). This also lays
 * out thread 0's TEB, so `FS:[0]` resolves for anything that runs afterwards. */
int  sa2_guest_init(const char *pe_path);

/* ------------------------------------------------ the shipped executable
 *
 * The user's DRM ruling of 2026-10-11 (Documentation/decisions/2026-10-11-S7-drm-unwrap-
 * ruling.md): the runtime loads the player's shipped `sonic2app.exe` as-is, whose `.text`
 * SteamStub has encrypted, and overlays into guest `.text` the bytes the runtime reads there,
 * which the release build carries (runtime/sa2_exe_overlay.h). Which file `pe_path` is, is
 * said, never inferred from its bytes:
 *
 *   sa2_guest_select_shipped(1)   before sa2_guest_init: `pe_path` is the shipped executable
 *   SA2_EXE_KIND=shipped          the same, for a host that does not call the API (the probe)
 *
 * Not SA2_EXE_SHIPPED, which tools/env.sh exports as the shipped executable's *path*: a switch
 * of that name would be on in every shell that sourced it.
 *
 * The API wins over the environment once called; -1 hands the choice back to it. Asked for,
 * the load goes to `sa2_guest_shipped_loader`, which runtime/sa2_exe_overlay.c sets when a
 * build links an overlay table (`sa2_exe_overlay_install`); a build that links none refuses
 * the shipped executable by name (SA2_E_OVERLAY) rather than mapping encrypted code. Not
 * asked for, the load is the one above, unchanged, whatever the build links. */
void sa2_guest_select_shipped(int shipped);    /* 1 shipped, 0 not, -1 from SA2_EXE_KIND */
/* What the next sa2_guest_init will do: 1 shipped, 0 unpacked, -1 when SA2_EXE_KIND holds
 * anything but shipped, unpacked or nothing, which init refuses by name (`switch`) rather
 * than read as unpacked. */
int  sa2_guest_shipped_selected(void);
/* The shipped-executable loader: `file`/`n` is the whole file, read; `path` names it. It
 * fills sa2_guest's image fields and guest memory, or returns an SA2_E_* without having
 * written guest memory. NULL when the build links no overlay table. */
typedef int (*sa2_guest_loader_fn)(const uint8_t *file, size_t n, const char *path);
extern sa2_guest_loader_fn sa2_guest_shipped_loader;
/* The last init's refusal of the shipped executable, one line without its newline, "" when it
 * refused nothing; and whether it loaded the shipped executable. Both are here, not in
 * sa2_exe_overlay.c, because init refuses `switch` and `no-table` itself, in builds that link
 * no overlay. Each init resets both before it loads; neither is reset by shutdown, so a host
 * can report them afterwards. `sa2_guest_refuse` prints `line` to stderr and keeps it: the
 * one place a refusal of init's is written. */
#define SA2_GUEST_REFUSAL_MAX 768u
void        sa2_guest_refuse(const char *line);
const char *sa2_guest_refusal(void);
extern int  sa2_guest_shipped_loaded;
void sa2_guest_shutdown(void);

/* Guest base address of thread `index`'s TEB, or 0 when the index is out of the
 * reserved region. Thread 0 is the one `sa2_guest_init` creates. */
uint32_t sa2_guest_teb(unsigned index);

/* Zero thread `index`'s TEB and write the fields above: an SEH chain that is
 * terminated rather than empty, the stack bounds, and the self-pointer.
 * SA2_OK, or SA2_E_INIT / SA2_E_RANGE. Idempotent -- calling it again resets
 * that thread's chain head, which is what a thread restart wants. */
int sa2_guest_teb_init(unsigned index);

/* The same, with the stack bounds this thread actually runs on. `sa2_guest_teb_init`
 * is this with thread 0's stack. A TEB whose StackBase/StackLimit name another
 * thread's stack is a TEB that lies about the stack, which is the one thing the
 * single-TEB version could not avoid once there was more than one stack. */
int sa2_guest_teb_init_stack(unsigned index, uint32_t stack_limit, uint32_t stack_base);

/* Base and top of guest-thread stack slot `slot` (0-based, < SA2_GUEST_TSTACK_SLOTS).
 * `*low` is the first address the stack may use -- one guard page above the slot --
 * and `*top` is the address the initial ESP is derived from. 0 on a bad slot. */
int sa2_guest_tstack(unsigned slot, uint32_t *low, uint32_t *top);

/* design 3.5 C ABI: bounds-checked bulk access for the harness and hosts. */
int  sa2_peek(uint32_t addr, uint32_t len, void *out);
int  sa2_poke(uint32_t addr, uint32_t len, const void *in);

/* ------------------------------------------------------- the poison tier
 *
 * G2 finding 6: until this, every address between the null guard and SA2_GUEST_END read
 * back either zero or the PE's file-initial byte. Nothing could fault, so "a plan zeroed a
 * global to gate a branch away" and "a plan never modelled that global at all" produced
 * identical runs, and a read through a stale pointer landed in a quiet arena instead of
 * stopping.
 *
 * A page is *initialised* when the PE loader wrote it (the headers and each section's
 * file-backed bytes) or when something wrote it since: `sa2_poke`, `sa2_guest_fill`,
 * `sa2_guest_alloc` and the write intrinsics all mark. Everything else is poison: the
 * arenas no plan has touched, the free space, and -- the interesting tier -- the part of a
 * section past its raw size, which is the `.bss` a loader zeroes and where the game's
 * mutable globals live. A read from a poison page traps with the guest address.
 *
 * Off by default, so no shipping verdict changes by linking this in.
 *   SA2_GUEST_POISON=1   trap on a read from a page nothing has written
 *   SA2_GUEST_POISON=2   count such reads and let them through (the survey mode)
 */
enum { SA2_POISON_OFF = 0, SA2_POISON_TRAP = 1, SA2_POISON_COUNT = 2 };
extern int      sa2_guest_poison;        /* SA2_POISON_* */
extern uint64_t sa2_guest_poison_reads;  /* reads from an unwritten page seen so far */
extern uint32_t sa2_guest_poison_first;  /* the first such guest address, 0 if none */

#define SA2_PAGE_SHIFT 12
#define SA2_PAGE_COUNT (SA2_GUEST_END >> SA2_PAGE_SHIFT)

extern uint8_t sa2_guest_page_written[SA2_PAGE_COUNT / 8];

void sa2_guest_poison_from_env(void);
void sa2_guest_note_write(uint32_t addr, uint32_t len);
/* 1 when any page of [addr, addr+len) has never been written -- or, inside the byte-granular
 * range below, any byte. */
int  sa2_guest_is_poison(uint32_t addr, uint32_t len);

/* The poison tier's byte-granular range. A page is too coarse where written and unwritten
 * bytes share pages: the shipped executable's guest `.text`, where only the overlay's runs
 * (jump tables, emulated-instruction sites) are real bytes and every other byte is a fill
 * pattern standing in for encrypted code. Inside [lo, hi) a byte is written only when
 * something wrote that byte; the page bitmap is not consulted there, and every write still
 * marks its pages as before. One range a guest space: none until `sa2_guest_fine_begin`,
 * which the overlay loader calls and nothing else needs, and `sa2_guest_shutdown` ends it.
 * Its counts stay readable after shutdown, as `sa2_guest_poison_reads` does, and the next
 * `sa2_guest_init` zeroes them. With no range, every function here behaves as it did.
 *
 * A test that finds an unwritten byte in the range also counts it (`sa2_guest_fine_reads`,
 * the first address, and the first SA2_GUEST_FINE_KEPT distinct addresses), beside the
 * caller's own count: the overlay's question is "which reads of guest .text are not the
 * read-set", and the global count mixes them with every other unwritten page. Only the
 * memory intrinsics test (remill_runtime.cpp `checked_read`), so host reads through
 * `sa2_peek` -- the emulator's decoder among them, which reads 15 bytes ahead -- are not
 * counted, as no page read through it ever was. */
#define SA2_GUEST_FINE_KEPT 64u
typedef struct {
    uint32_t lo, hi;                  /* [lo, hi); lo == hi when there is no range */
    uint8_t *bits;                    /* one bit a byte of [lo, hi), set when written */
    uint64_t reads;                   /* tests that found an unwritten byte in the range */
    uint32_t first;                   /* the first such byte's address, 0 if none */
    uint32_t kept[SA2_GUEST_FINE_KEPT];  /* distinct unwritten bytes' addresses, first seen first */
    unsigned kept_count;              /* how many of `kept` are used */
    uint64_t unkept;                  /* tests whose address `kept`, full, does not hold */
} sa2_guest_fine_t;
extern sa2_guest_fine_t sa2_guest_fine;
/* Begin the range [lo, hi), every byte unwritten. SA2_OK, SA2_E_INIT before init, SA2_E_RANGE
 * for an empty or out-of-space range, SA2_E_NOMEM. Once per init: a second call refuses. */
int sa2_guest_fine_begin(uint32_t lo, uint32_t hi);

/* The header census: which bytes of the image's first page a test of the poison tier met, one
 * bit a byte, and how many tests met it. The header page is mapped as-is from the file, so no
 * read of it is poison; the census is what tells which header bytes the guest reads, so a host
 * can hold them to what a loader verified (sa2_exe_overlay_report, `header_unverified`). Kept
 * under every load; zeroed by init, and not by shutdown. */
#define SA2_GUEST_HEAD_CENSUS (1u << SA2_PAGE_SHIFT)
extern uint8_t  sa2_guest_head_read[SA2_GUEST_HEAD_CENSUS / 8];
extern uint64_t sa2_guest_head_reads;

/* ------------------------------------------------ the inlined fast path
 *
 * A lifted function that State localization rewrites reads and writes guest memory inline
 * rather than through the intrinsics in remill_runtime.cpp
 * (tools/s2/corpus_run/state_local.py, "The guest-memory fast path"). It loads this base
 * once, on entry, and makes an access through it when the base is not NULL and the access
 * lies in [SA2_GUEST_NULL_GUARD, SA2_GUEST_END - len], the range `sa2_gp` accepts. Every
 * other access asks `sa2_guest_read_slow` or `sa2_guest_write_slow` (remill_runtime.cpp)
 * for its pointer: the intrinsics' own checked path, so the fault, its note and the poison
 * tier stay the runtime's.
 *
 * It is `sa2_guest.base` while guest memory is mapped and the poison tier is off, and NULL
 * otherwise: before `sa2_guest_init` succeeds, after `sa2_guest_shutdown`, and while
 * `sa2_guest_poison` is not SA2_POISON_OFF, since the fast path neither tests a page nor
 * marks one. Three functions change what it follows, and each sets it again before it
 * returns: `sa2_guest_init`, `sa2_guest_shutdown` and `sa2_guest_poison_from_env`.
 * `sa2_guest_poison` is therefore set only through `sa2_guest_poison_from_env` and never
 * assigned directly, although it is a public variable. Nothing changes the base while
 * lifted code runs, which is what lets a lifted function keep the value it loaded on entry.
 *
 * An inline access is the intrinsic's only while the intrinsics' checked path
 * (`checked_read` and `checked_write` in remill_runtime.cpp) tests nothing but `sa2_gp`'s
 * range and the poison tier. A check added there -- enforcing the section permissions
 * recorded above, say -- must either leave this base NULL whenever it can refuse an access,
 * or be made in state_local.py's `_fast_helper` too; otherwise every rewritten function
 * skips it. Documentation/scripts/tests/test_state_local.py runs both paths over this
 * runtime at the edges of the guest space, under each poison mode, and fails on any
 * difference (harness/guest_fast_probe.c).
 *
 * Hidden on ELF: the Android game is -fPIC, and a default-visibility global there is reached through
 * the GOT, one more dependent load in every out-of-line intrinsic. */
#if defined(__ELF__)
__attribute__((visibility("hidden")))
#endif
extern uint8_t *sa2_guest_fast_base;

/* Helpers the intrinsics, the call entry and the harness need. */
int      sa2_guest_fill(uint32_t addr, int byte, uint32_t len);
int      sa2_guest_valid(uint32_t addr, uint32_t len);
/* Bump-allocate from the *runtime* arena (SA2_GUEST_RT_ARENA_BASE), never from
 * the guest's own heap. 0 when the arena is exhausted, which the caller must
 * treat as a refusal: handing back an address outside the arena is how the
 * guest's heap got erased once already. */
uint32_t sa2_guest_alloc(uint32_t len, uint32_t align);
void     sa2_guest_rt_reset(void);
uint32_t sa2_guest_rt_used(void);                          /* bytes handed out so far */
const sa2_section_t *sa2_guest_section_of(uint32_t addr);
const char *sa2_guest_region_of(uint32_t addr);           /* "image", "heap", "stack", ... */

/* Hot path: host pointer for a guest range, NULL when out of range. Inline so
 * the memory intrinsics cost one compare and one add. */
static inline uint8_t *sa2_gp(uint32_t addr, uint32_t len) {
    /* `addr + len` cannot wrap: both are bounded by the checks below. */
    if (addr < SA2_GUEST_NULL_GUARD) return NULL;
    if (len > SA2_GUEST_END || addr > SA2_GUEST_END - len) return NULL;
    return sa2_guest.base + addr;
}

#ifdef __cplusplus
}
#endif
#endif
