/* host/sa2_present_rec.c: the presentation build's recorder (S5, unit REC).
 *
 * What it is
 * ----------
 * The game side of the DXVK route. The lifted game is a static musl binary and can
 * load no DXVK, SDL3 or Vulkan driver (its dlopen is musl's stub), so every call the
 * guest makes on the recording device's COM objects, on the eleven d3dx9 entry
 * points and on Direct3DCreate9 is copied out at call time into a stream a separate
 * glibc player replays into DXVK Native. The stream's format is host/sa2_stream.h,
 * whose rule lines say what every call carries; tools/s5/stream_dump.py reads it
 * back and holds every record to those rules.
 *
 * How it attaches: by wrapping, in presentation builds only
 * ---------------------------------------------------------
 * Nothing under runtime/ or harness/ is edited, so no registered replay's inputs
 * move. tools/s5/present_run.py links this file into a build of its own name
 * (present-*, never reach, replay.* or stage-*) with -Wl,-wrap of each of WRAPS
 * there: sa2_ft_add (every function-table registration), sa2_d3d9_make_d3d9
 * (Direct3DCreate9) and the eleven sa2_d3d9_d3dx_* entry points.
 *
 * The recording device registers one function-table entry per (interface, slot)
 * thunk in [SA2_D3D9_THUNK_BASE, SA2_D3D9_THUNK_END) with sa2_ft_add
 * (runtime/sa2_d3d9.c, type_vtable). __wrap_sa2_ft_add keeps the recording
 * device's native for each and registers rec_thunk in its place, under the same
 * address, name and kind; every other registration passes through untouched. So a
 * guest call through a vtable reaches rec_thunk, which reads `this` and the
 * arguments off the guest stack exactly as the native is about to, calls the
 * native, reads EAX, and records. The entry points are wrapped the same way;
 * runtime/sa2_imports.c is their only caller.
 *
 * What the guest cannot see, and why
 * ----------------------------------
 * The recorder only reads the guest. Every guest read is sa2_peek (runtime/
 * guest_memory.c: a bounds check and a memcpy); it never writes or allocates guest
 * memory, calls no sa2_poke, obj_* or heap function, and never changes an answer:
 * EAX, ESP and every out-parameter are the recording device's, set before the
 * recorder looks. It does no floating point, starts no thread, installs no signal
 * handler and calls no scheduler function except two getters that only read a
 * variable, sa2_sched_current and sa2_sched_frames (runtime/sa2_threads.c), beside
 * sa2_virtual_us (runtime/sa2_imports.c) and sa2_window_stats (runtime/
 * sa2_window.c), which copy. The host resources it takes -- a file descriptor, a
 * malloc'd buffer, an atexit entry, and in paced play the pace pipe's inherited
 * descriptor, which it reads (SA2_REC_PACE_FD) -- are the process's, which the runtime never
 * hands the guest: it numbers guest handles itself (harness/replay/
 * wrap_tick_probe.c says the same of its word log). Two runtime calls write host
 * scratch that every reader rewrites before reading: sa2_d3d9_texsrc_at refreshes
 * two host-side fields of a texture row from its object (runtime/sa2_d3d9.c, every
 * reader calls it first), and sa2_d3d9_method_flags formats into a static buffer,
 * called here only at start-up (its one other caller, harness/crt_probe.c, formats
 * afresh and prints at once).
 *
 * How that is checked, and what each check covers:
 *   - statically, on the object itself (tools/s5/objcheck.py, held by
 *     test_s5_stream.py and by present_run.py on the object it linked): its
 *     undefined symbols are exactly an allowed list of reads, host libc and the
 *     __real_ wraps -- no sa2_poke, no allocator, no sa2_guest -- it defines only its
 *     __wrap_ functions, and its code holds no x87, MMX, 3DNow! or SSE/AVX
 *     floating-point instruction, nothing that loads or saves the FP state or MXCSR,
 *     no system call, no double handed to a variadic call and no float conversion
 *     in its strings; this file holds no inline asm. That proves the object calls
 *     no runtime writer by name and computes no floating point. It does not see a
 *     store through the register file rec_thunk is handed, nor how often the
 *     recorder calls the natives and __real_ functions it keeps, each of which
 *     writes guest memory by design;
 *   - dynamically, by tools/s5/present_run.py on every run against the registered
 *     boot replay: lifted.csv byte for byte, the .dataa word log by value at every
 *     tick, the reach fields, the whole run record leaf for leaf (the device's own
 *     counts and objects, which an extra call of a native or a __real_ function
 *     would move), the recorder's per-method and per-entry counts against the run
 *     record and the import table, and the device's first 8,192 calls against the
 *     stream (forwarded with the guest's arguments, answered as the device
 *     answered). That covers what those record, not every guest byte: a write
 *     outside .dataa that nothing reads back would pass it, which is why the static
 *     check exists;
 *   - without the game, by Documentation/scripts/tests/test_s5_stream.py
 *     (tools/s5/tests/rec_harness.c): the same calls with the recorder on and off
 *     leave the same guest bytes, answers, function-table registrations (address,
 *     name and kind), and register file after every call -- the harness's own
 *     stand-in for it, nine 32-bit registers and the x87 control word, 40 bytes in
 *     the harness's layout, not the runtime's State.
 * So a store through the register file rec_thunk is handed is held only where it
 * lands in those 40 bytes of the stand-in, and only for the calls the harness makes;
 * no check of a run sees one the guest never reads back.
 *
 * When a pointer argument's bytes are read. BeginScene, and a Present no BeginScene
 * came before, take the frame's wait (runtime/sa2_d3d9.c, frame_wait), the one place
 * a d3d9 or d3dx call lets another guest thread run; no other handler, and nothing
 * they call (the guest heap included), reaches the scheduler. So a call's pointed-at
 * bytes are read after the call returns and before the guest resumes, when only the
 * call itself could have written them, except Present's, which are read before it,
 * and the presentation parameters, which D3D9 lets a call rewrite.
 *
 * Per-call state lives on the host stack. BeginScene hands the guest lock to other
 * guest threads inside the call (runtime/sa2_d3d9.c, frame_wait), and their calls
 * run to completion, and are recorded, before BeginScene returns and is recorded
 * after them: the order the recording device finished them in. A record is built
 * whole after its call returned, so a call that traps (a refusal ends the run
 * inside the native) leaves no half record: the stream holds every call that
 * completed. Records, counters, the shadow table and the open locks below are
 * shared by all guest threads and are only touched by the thread holding the guest
 * lock.
 *
 * The buffer watch (Metal Harbor). A vertex or index buffer's bytes are copied at
 * its Unlock, and a player draws them for every DrawPrimitive that reads the buffer
 * afterwards. They are the guest's bytes only while the guest writes a buffer between
 * Lock and Unlock, as Direct3D says it must; the recording device cannot stop a write
 * after the Unlock, because the store is guest memory. So when a buffer's last open
 * lock closes, the bytes that Unlock copied are hashed (FNV-1a), and every
 * REC_WATCH_EVERY frames they are hashed again, until the buffer's next Lock or its
 * final Release: a change is counted, noted (class "buffer-changed", after the FRAME
 * whose check saw it) and counted once, and the counts file says how many checks ran
 * over how many bytes ("buffer_watch"). The watch reads guest memory with sa2_peek
 * like everything else here.
 *
 * Audio (SA2_REC_AUDIO=1 only)
 * ----------------------------
 * With SA2_REC_AUDIO=1 the stream is version 2 (host/sa2_stream.h, "Versions") and
 * the DirectSound and XAudio2 method thunks are wrapped the way the d3d9 ones are:
 * runtime/sa2_dsound.c and runtime/sa2_xaudio2.c register one function-table entry per
 * (interface, slot) thunk in [SA2_DS_THUNK_BASE, SA2_DS_THUNK_END) and [SA2_XA2_
 * THUNK_BASE, SA2_XA2_THUNK_END) when their first object is made, and
 * __wrap_sa2_ft_add registers au_thunk in place of each one whose method has an
 * `audio` line (AU_RULES), under the same address, name and kind; every other
 * registration passes through. au_thunk reads `this` and the arguments off the guest
 * stack (the device's own count, held at start-up to the line's), calls the native
 * once, reads EAX, and records a SA2S_AUDIO record when the call succeeded: the
 * bytes each Unlock and SubmitSourceBuffer names, and the device's own record of the
 * buffer or voice after the call (sa2_dsound_buffer_at, sa2_xaudio2_voice_at: a
 * pointer into the device's table, read, never written), copied field by field. It
 * calls no cursor helper (sa2_dsound_buffer_cursors moves g_st.cursor_wraps,
 * sa2_xaudio2_voice_now can trap), and computes no floating point: the level matrix
 * and the frequency ratio are float32 bit patterns copied as words. Unset or 0, no
 * audio thunk is wrapped and the stream, the counts file and every counter are what
 * they were before this section existed; any other value stops the stream.
 *
 * The store watch (audio). A DirectSound buffer plays its store, guest memory the
 * runtime's Lock points into and its Unlock never copies, so the stream holds a
 * store only as the PCM blobs of its Unlocks. Two checks hold that: an Unlock that
 * leaves no Lock open on its store (au_store: Locks are counted per store, so a
 * duplicate that shares one counts with its original) carries the FNV-1a of the
 * whole store after the call (store_hash, a HASH blob; a live session's play carries it
 * on one such Unlock in 64, SA2_REC_STORE_HASH_EVERY below), which a player compares
 * with the store it rebuilt from the PCM; and from that Unlock the store is
 * watched, like a vertex buffer: hashed again at every REC_WATCH_EVERY-th FRAME and
 * when the next Lock opens on it, a change counted once and noted (class "audio").
 * The counts file's "audio" "store_watch" says what the watch did. Both read guest
 * memory with sa2_peek and compute integers only.
 *
 * Failure
 * -------
 * The recorder never traps and never stops the game. A failure it can detect -- a
 * file that does not open, a write cut short, memory it cannot allocate, an
 * interface table it was not written for -- stops the stream (the counting goes on)
 * and says why in the sidecar, <SA2_REC_TRACE>.status. A blob it cannot copy whole
 * is left out of its record and a SA2S_NOTE names it; every note is counted by
 * class in the counts file. Under any SA2_D3D9_BREAK the recorder records nothing: a
 * break changes the recording device's model, and the stream would describe a
 * device nobody meant to present. For the same reason SA2_REC_AUDIO=1 under any
 * SA2_DSOUND_BREAK or SA2_XAUDIO2_BREAK stops the stream (the counting goes on).
 *
 * Environment (read at the first registration of a d3d9 thunk, the first call of a
 * wrapped entry point or, with SA2_REC_AUDIO=1 only, the first registration of an
 * audio thunk, whichever comes first; unset, the wrappers only pass through). The
 * prefix is the recorder's own: the runtime reads SA2_PRESENT_SURVEY, and a runner
 * that clears the recorder's variables must not clear the runtime's.
 *   SA2_REC_TRACE=<path>   write the stream there (host/sa2_stream.h); the sidecar is
 *                          <path>.status
 *   SA2_REC_COUNTS=<path>  write the call counts there as JSON at exit (without a
 *                          trace, the sidecar is <path>.status)
 *   SA2_REC_META=<json>    the text the stream header carries as "meta"
 *   SA2_REC_AUDIO=1        record the audio calls too (above): a version 2 stream.
 *                          Any other value but 0 is refused, with a trace or without:
 *                          the stream stops, the counting goes on without audio, and
 *                          the counts file's "why" says so
 *   SA2_REC_AUDIO_WATCH=0  the store watch (above) hashes no store again after its
 *                          Unlock; each Unlock's store_hash is made as before, and the
 *                          counts file's "store_watch" says checks 0. A live session's
 *                          play only (host/launch, host/android): nothing reads its stream
 *                          back against its counts. Unset or any other value, it watches.
 *   SA2_REC_STORE_HASH_EVERY=<n>  an Unlock that closes its store's last Lock carries the
 *                          store_hash (above) on the first such Unlock and every n-th after it,
 *                          counted over every store, and none on the others, which hash
 *                          nothing and set no watch; n is 1 to 65536. A live session's play
 *                          only (host/launch, host/android: 64, the live player's
 *                          kLiveStoreCheckEvery), since the hash is one byte at a time over the
 *                          whole 64-128 KB store at about two Unlocks a frame, on the guest's
 *                          serialized path (0.20 ms a tick on the PC, the serial study of
 *                          2026-10-12). The counts file's "audio" "store_hash" says how many
 *                          closed and how many went without. Unset, 1 or anything else, every
 *                          such Unlock carries it: replays, the selftest, every receipt's run.
 *   SA2_REC_STATE=1        after each FRAME, when what it reads of the game's state has changed
 *                          since the last (or at the first FRAME), a state note: a SA2S_NOTE
 *                          whose text is "state mode=M level=L character=C two=T alt=A",
 *                          GameMode (the int32 at 0x0174B040, which GameModeHandler 0x00434160
 *                          switches on), CurrentLevel (the int16 at 0x01934B70), player one's
 *                          CurrentCharacter (the int32 at 0x01934B80, which LoadCharacters
 *                          0x0043D630 loads by), TwoPlayerMode (the byte at 0x0174AFDE) and
 *                          player one's costume byte (0x0174B015, which each character's loader
 *                          reads in a two-player game), each -1 when it does not read. The
 *                          player's touch controls follow it (host/player/touch_overlay.h, "The
 *                          game's state"). It says nothing was left out (host/sa2_stream.h), so
 *                          it is no note class: the counts file has their number as
 *                          stream_records' "state", apart from "note". A live session's play
 *                          only (host/launch, host/android); unset or any other value, none is
 *                          written, and the stream is what it was.
 *   SA2_REC_PACE_FD=<n>    the read end of a live session's pace pipe, inherited as descriptor
 *                          n (host/launch/sa2_relay.h, "Pace"): after each frame's records are
 *                          written, the recorder reads one byte from it, so the game waits
 *                          there, after its Present's write, until the relay has handed on the
 *                          frame `lead` frames back. The guest reads only the virtual clock, so
 *                          the wait changes nothing it computes. End of file (the relay closed
 *                          it: the player has gone or the stream ended) ends the pacing for the
 *                          run, silently; an error ends it too, said in the sidecar. Neither
 *                          stops the stream. Read only while the stream runs. Paced play only
 *                          (host/launch, host/android); unset or not a decimal number, nothing
 *                          is read.
 *
 * Breaks for the tests only. Each is compiled in with -D and exists so a check in
 * test_s5_stream.py is shown to fail without the property it holds; no
 * presentation build defines one:
 *   REC_BREAK_POKE     the recorder writes a guest byte after each call
 *   REC_BREAK_REGSTORE the recorder flips a bit of the register file's EDI word after
 *                      each call, a store through the pointer it is handed that
 *                      objcheck cannot see (EDI in the harness's stand-in only)
 *   REC_BREAK_FTKIND   rec_thunk is registered with kind 0, not the device's kind
 *   REC_BREAK_FTNAME   rec_thunk is registered under a name of its own, not the
 *                      device's
 *   REC_BREAK_GLOBAL   the per-call state lives in one static, not on the host stack
 *   REC_BREAK_NOFLUSH  no flush at Present
 *   REC_BREAK_WALK     the shader walk ends at the first 0x0000FFFF, comments included
 *   REC_BREAK_NOPAD    blobs are not padded to 4 bytes
 *   REC_BREAK_ONELOCK  one open lock per object: a second level's lock replaces the
 *                      first, and a buffer's nested lock replaces the outer one
 *   REC_BREAK_BUFBASE  a buffer's Unlock copies from the store's base, not from the
 *                      address its Lock answered (OffsetToLock forgotten)
 *   REC_BREAK_NOWATCH  the buffer watch never checks
 *   REC_BREAK_AUPCM    an Unlock's segment is copied from 4 bytes past its pointer
 *   REC_BREAK_AUSTATE  an audio call's STATE is the device's record from before the
 *                      call, not after it
 *   REC_BREAK_AUCURSOR the recorder asks the DirectSound device for every buffer's cursors
 *                      (sa2_dsound_buffer_cursors), which moves the device's wrap count
 *   REC_BREAK_AUDROP   every DS_UNLOCK is counted as a call that failed and not
 *                      recorded, so the stream and the counts file still agree
 *   REC_BREAK_AUNOWATCH the store watch never checks
 */
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "cpu_state.h"
#include "function_table.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_d3d9.h"
#include "sa2_dsound.h"
#include "sa2_threads.h"
#include "sa2_window.h"
#include "sa2_xaudio2.h"

#include "sa2_stream.h"

#define REC_WRITER     "host/sa2_present_rec.c"
#define REC_THUNKS     (SA2_D3D9_MAX_TYPES * SA2_D3D9_MAX_SLOTS)
#define REC_OBJ_TAG    0x44334439u           /* 'D3D9': runtime/sa2_d3d9.c, obj_new */
#define REC_BUF_BYTES  (32u << 20)
#define REC_BLOB_CAP   (64u << 20)           /* any blob but a d3dx file */
#define REC_FILE_CAP   (256u << 20)          /* a d3dx file blob */
#define REC_MAX_BLOBS  8
#define REC_MAX_ARGS   15u                   /* the device reads at most 16 dwords, `this` too */
#define REC_ARGS       16u                   /* room for any call: the volume load takes 16 */
#define REC_ACTS       6
#define REC_RGN_RECTS  16u
#define REC_LOCKS      64u
#define REC_NOTES_PER_CLASS 16u
#define REC_CALL_NOTES 4
#define REC_NOTE_BYTES 256
#define REC_WATCH_EVERY 64u                  /* frames between checks of the buffer watch */

/* The recording device's interface indices (runtime/sa2_d3d9.c, g_types), checked
 * against its own names at start-up: a table that moved stops the stream rather
 * than mislabel every call. */
enum {
    T_D3D9 = 0, T_DEVICE, T_TEXTURE, T_CUBETEX, T_VOLTEX, T_SURFACE, T_VOLUME,
    T_VERTEXBUFFER, T_INDEXBUFFER, T_VERTEXDECL, T_VERTEXSHADER, T_PIXELSHADER,
    T_STATEBLOCK, T_SWAPCHAIN, T_QUERY, T_CONSTANTTABLE, T_COUNT
};
static const char *const TYPE_NAMES[T_COUNT] = {
    "IDirect3D9", "IDirect3DDevice9", "IDirect3DTexture9", "IDirect3DCubeTexture9",
    "IDirect3DVolumeTexture9", "IDirect3DSurface9", "IDirect3DVolume9",
    "IDirect3DVertexBuffer9", "IDirect3DIndexBuffer9", "IDirect3DVertexDeclaration9",
    "IDirect3DVertexShader9", "IDirect3DPixelShader9", "IDirect3DStateBlock9",
    "IDirect3DSwapChain9", "IDirect3DQuery9", "ID3DXConstantTable",
};

/* ---------------------------------------------------------------- the rules
 *
 * What each call carries beside its args and eax: host/sa2_stream.h's rule lines,
 * the ops there by the same names (OPS). The COM rules are keyed by the recording
 * device's own interface and method names, so a slot number transcribed wrong here
 * cannot attach a rule to the wrong method, and resolved once per thunk. Every
 * stream's header carries the table as resolved (write_header), and the tests hold
 * it to the header file's lines. */
enum {
    A_END = 0,
    A_IN, A_OUT, A_IN_VEC, A_OUT_VEC, A_OUT_SIZED, A_SHADER, A_DECL, A_UP, A_IUP,
    A_PRE_PP, A_PRE_RECT, A_PRE_RGN, A_FRAME, A_DEVICE, A_FILE, A_FILE_DESC, A_OBJECT_EAX,
    A_NEW_TEX, A_NEW_VOL, A_NEW_CUBE, A_NEW_SURF, A_NEW_BUF, A_LEVEL,
    A_LOCK_SURF, A_UNLOCK_SURF, A_LOCK_TEX, A_UNLOCK_TEX, A_LOCK_BUF, A_UNLOCK_BUF,
    A_COUNT
};
/* Each op's name and the operands its text names, in order (a b c d: argument
 * indices; n: a byte count or a multiplier). */
static const struct { const char *name, *operands; } OPS[A_COUNT] = {
    [A_END] = {"", ""},
    [A_IN] = {"in", "an"}, [A_OUT] = {"out", "an"},
    [A_IN_VEC] = {"in_vec", "abn"}, [A_OUT_VEC] = {"out_vec", "abn"},
    [A_OUT_SIZED] = {"out_sized", "abn"},
    [A_SHADER] = {"shader", "a"}, [A_DECL] = {"decl", "a"},
    [A_UP] = {"up", ""}, [A_IUP] = {"iup", ""},
    [A_PRE_PP] = {"pre_pp", "a"}, [A_PRE_RECT] = {"pre_rect", "a"},
    [A_PRE_RGN] = {"pre_rgn", "a"}, [A_FRAME] = {"frame", ""}, [A_DEVICE] = {"device", ""},
    [A_FILE] = {"file", "ab"}, [A_FILE_DESC] = {"file_desc", "a"},
    [A_OBJECT_EAX] = {"object_eax", ""},
    [A_NEW_TEX] = {"new_tex", "abcd"}, [A_NEW_VOL] = {"new_vol", "abcd"},
    [A_NEW_CUBE] = {"new_cube", "abd"}, [A_NEW_SURF] = {"new_surf", "abcd"},
    [A_NEW_BUF] = {"new_buf", "ab"}, [A_LEVEL] = {"level", "a"},
    [A_LOCK_SURF] = {"lock_surf", ""}, [A_UNLOCK_SURF] = {"unlock_surf", ""},
    [A_LOCK_TEX] = {"lock_tex", ""}, [A_UNLOCK_TEX] = {"unlock_tex", ""},
    [A_LOCK_BUF] = {"lock_buf", ""}, [A_UNLOCK_BUF] = {"unlock_buf", ""},
};

typedef struct { uint8_t op, a, b, c, d; uint32_t n; } rec_act;
typedef struct { const char *iface, *method; rec_act act[REC_ACTS]; } rec_rule;
typedef struct { uint32_t entry; const char *name; uint32_t argc; rec_act act[REC_ACTS]; } rec_entry;

#define IN(x, n)            {A_IN, x, 0, 0, 0, n}
#define OUT(x, n)           {A_OUT, x, 0, 0, 0, n}
#define IN_VEC(x, k, n)     {A_IN_VEC, x, k, 0, 0, n}
#define OUT_VEC(x, k, n)    {A_OUT_VEC, x, k, 0, 0, n}
#define OUT_SIZED(x, k, n)  {A_OUT_SIZED, x, k, 0, 0, n}
#define OP(o)               {o, 0, 0, 0, 0, 0}
#define OPA(o, x)           {o, x, 0, 0, 0, 0}
#define NEW(o, x, w, h, f)  {o, x, w, h, f, 0}

static const rec_rule RULES[] = {
    {"IDirect3D9", "GetAdapterIdentifier", {OUT(2, 1100)}},
    {"IDirect3D9", "EnumAdapterModes", {OUT(3, 16)}},
    {"IDirect3D9", "GetAdapterDisplayMode", {OUT(1, 16)}},
    {"IDirect3D9", "CheckDeviceMultiSampleType", {OUT(5, 4)}},
    {"IDirect3D9", "GetDeviceCaps", {OUT(2, 304)}},
    {"IDirect3D9", "CreateDevice", {OPA(A_PRE_PP, 4), OUT(5, 4), OP(A_DEVICE)}},

    {"IDirect3DDevice9", "GetDirect3D", {OUT(0, 4)}},
    {"IDirect3DDevice9", "GetDeviceCaps", {OUT(0, 304)}},
    {"IDirect3DDevice9", "GetDisplayMode", {OUT(1, 16)}},
    {"IDirect3DDevice9", "GetCreationParameters", {OUT(0, 16)}},
    {"IDirect3DDevice9", "CreateAdditionalSwapChain", {OPA(A_PRE_PP, 0), OUT(1, 4)}},
    {"IDirect3DDevice9", "GetSwapChain", {OUT(1, 4)}},
    {"IDirect3DDevice9", "Reset", {OPA(A_PRE_PP, 0)}},
    {"IDirect3DDevice9", "Present",
     {OPA(A_PRE_RECT, 0), OPA(A_PRE_RECT, 1), OPA(A_PRE_RGN, 3), OP(A_FRAME)}},
    {"IDirect3DDevice9", "GetBackBuffer", {OUT(3, 4)}},
    {"IDirect3DDevice9", "GetRasterStatus", {OUT(1, 8)}},
    {"IDirect3DDevice9", "SetGammaRamp", {IN(2, 1536)}},
    {"IDirect3DDevice9", "GetGammaRamp", {OUT(1, 1536)}},
    {"IDirect3DDevice9", "CreateTexture", {OUT(6, 4), NEW(A_NEW_TEX, 6, 0, 1, 4)}},
    {"IDirect3DDevice9", "CreateVolumeTexture", {OUT(7, 4), NEW(A_NEW_VOL, 7, 0, 1, 5)}},
    {"IDirect3DDevice9", "CreateCubeTexture", {OUT(5, 4), NEW(A_NEW_CUBE, 5, 0, 0, 3)}},
    {"IDirect3DDevice9", "CreateVertexBuffer", {OUT(4, 4), NEW(A_NEW_BUF, 4, 0, 0, 0)}},
    {"IDirect3DDevice9", "CreateIndexBuffer", {OUT(4, 4), NEW(A_NEW_BUF, 4, 0, 0, 0)}},
    {"IDirect3DDevice9", "CreateRenderTarget", {OUT(6, 4), NEW(A_NEW_SURF, 6, 0, 1, 2)}},
    {"IDirect3DDevice9", "CreateDepthStencilSurface", {OUT(6, 4), NEW(A_NEW_SURF, 6, 0, 1, 2)}},
    {"IDirect3DDevice9", "UpdateSurface", {IN(1, 16), IN(3, 8)}},
    {"IDirect3DDevice9", "StretchRect", {IN(1, 16), IN(3, 16)}},
    {"IDirect3DDevice9", "ColorFill", {IN(1, 16)}},
    {"IDirect3DDevice9", "CreateOffscreenPlainSurface", {OUT(4, 4), NEW(A_NEW_SURF, 4, 0, 1, 2)}},
    {"IDirect3DDevice9", "GetRenderTarget", {OUT(1, 4)}},
    {"IDirect3DDevice9", "GetDepthStencilSurface", {OUT(0, 4)}},
    {"IDirect3DDevice9", "Clear", {IN_VEC(1, 0, 16)}},
    {"IDirect3DDevice9", "SetTransform", {IN(1, 64)}},
    {"IDirect3DDevice9", "GetTransform", {OUT(1, 64)}},
    {"IDirect3DDevice9", "MultiplyTransform", {IN(1, 64)}},
    {"IDirect3DDevice9", "SetViewport", {IN(0, 24)}},
    {"IDirect3DDevice9", "GetViewport", {OUT(0, 24)}},
    {"IDirect3DDevice9", "SetMaterial", {IN(0, 68)}},
    {"IDirect3DDevice9", "GetMaterial", {OUT(0, 68)}},
    {"IDirect3DDevice9", "SetLight", {IN(1, 104)}},
    {"IDirect3DDevice9", "GetLight", {OUT(1, 104)}},
    {"IDirect3DDevice9", "GetLightEnable", {OUT(1, 4)}},
    {"IDirect3DDevice9", "SetClipPlane", {IN(1, 16)}},
    {"IDirect3DDevice9", "GetClipPlane", {OUT(1, 16)}},
    {"IDirect3DDevice9", "GetRenderState", {OUT(1, 4)}},
    {"IDirect3DDevice9", "CreateStateBlock", {OUT(1, 4)}},
    {"IDirect3DDevice9", "EndStateBlock", {OUT(0, 4)}},
    {"IDirect3DDevice9", "SetClipStatus", {IN(0, 8)}},
    {"IDirect3DDevice9", "GetClipStatus", {OUT(0, 8)}},
    {"IDirect3DDevice9", "GetTexture", {OUT(1, 4)}},
    {"IDirect3DDevice9", "GetTextureStageState", {OUT(2, 4)}},
    {"IDirect3DDevice9", "GetSamplerState", {OUT(2, 4)}},
    {"IDirect3DDevice9", "ValidateDevice", {OUT(0, 4)}},
    {"IDirect3DDevice9", "SetPaletteEntries", {IN(1, 1024)}},
    {"IDirect3DDevice9", "GetCurrentTexturePalette", {OUT(0, 4)}},
    {"IDirect3DDevice9", "SetScissorRect", {IN(0, 16)}},
    {"IDirect3DDevice9", "GetScissorRect", {OUT(0, 16)}},
    {"IDirect3DDevice9", "DrawPrimitiveUP", {OP(A_UP)}},
    {"IDirect3DDevice9", "DrawIndexedPrimitiveUP", {OP(A_IUP)}},
    {"IDirect3DDevice9", "CreateVertexDeclaration", {OPA(A_DECL, 0), OUT(1, 4)}},
    {"IDirect3DDevice9", "GetVertexDeclaration", {OUT(0, 4)}},
    {"IDirect3DDevice9", "GetFVF", {OUT(0, 4)}},
    {"IDirect3DDevice9", "CreateVertexShader", {OPA(A_SHADER, 0), OUT(1, 4)}},
    {"IDirect3DDevice9", "GetVertexShader", {OUT(0, 4)}},
    {"IDirect3DDevice9", "SetVertexShaderConstantF", {IN_VEC(1, 2, 16)}},
    {"IDirect3DDevice9", "GetVertexShaderConstantF", {OUT_VEC(1, 2, 16)}},
    {"IDirect3DDevice9", "SetVertexShaderConstantI", {IN_VEC(1, 2, 16)}},
    {"IDirect3DDevice9", "GetVertexShaderConstantI", {OUT_VEC(1, 2, 16)}},
    {"IDirect3DDevice9", "SetVertexShaderConstantB", {IN_VEC(1, 2, 4)}},
    {"IDirect3DDevice9", "GetVertexShaderConstantB", {OUT_VEC(1, 2, 4)}},
    {"IDirect3DDevice9", "GetStreamSource", {OUT(1, 4), OUT(2, 4), OUT(3, 4)}},
    {"IDirect3DDevice9", "GetStreamSourceFreq", {OUT(1, 4)}},
    {"IDirect3DDevice9", "GetIndices", {OUT(0, 4)}},
    {"IDirect3DDevice9", "CreatePixelShader", {OPA(A_SHADER, 0), OUT(1, 4)}},
    {"IDirect3DDevice9", "GetPixelShader", {OUT(0, 4)}},
    {"IDirect3DDevice9", "SetPixelShaderConstantF", {IN_VEC(1, 2, 16)}},
    {"IDirect3DDevice9", "GetPixelShaderConstantF", {OUT_VEC(1, 2, 16)}},
    {"IDirect3DDevice9", "SetPixelShaderConstantI", {IN_VEC(1, 2, 16)}},
    {"IDirect3DDevice9", "GetPixelShaderConstantI", {OUT_VEC(1, 2, 16)}},
    {"IDirect3DDevice9", "SetPixelShaderConstantB", {IN_VEC(1, 2, 4)}},
    {"IDirect3DDevice9", "GetPixelShaderConstantB", {OUT_VEC(1, 2, 4)}},
    {"IDirect3DDevice9", "DrawRectPatch", {IN(1, 16), IN(2, 28)}},
    {"IDirect3DDevice9", "DrawTriPatch", {IN(1, 12), IN(2, 16)}},
    {"IDirect3DDevice9", "CreateQuery", {OUT(1, 4)}},
    {"IDirect3DDevice9", "GetRenderTargetData", {OP(A_END)}},
    {"IDirect3DDevice9", "GetFrontBufferData", {OP(A_END)}},

    {"IDirect3DTexture9", "GetLevelDesc", {OUT(1, 32)}},
    {"IDirect3DTexture9", "GetSurfaceLevel", {OUT(1, 4), OPA(A_LEVEL, 1)}},
    {"IDirect3DTexture9", "LockRect", {OUT(1, 8), IN(2, 16), OP(A_LOCK_TEX)}},
    {"IDirect3DTexture9", "UnlockRect", {OP(A_UNLOCK_TEX)}},
    {"IDirect3DTexture9", "AddDirtyRect", {IN(0, 16)}},
    {"IDirect3DCubeTexture9", "GetLevelDesc", {OUT(1, 32)}},
    {"IDirect3DCubeTexture9", "GetCubeMapSurface", {OUT(2, 4)}},
    {"IDirect3DCubeTexture9", "AddDirtyRect", {IN(1, 16)}},
    {"IDirect3DVolumeTexture9", "GetLevelDesc", {OUT(1, 28)}},
    {"IDirect3DVolumeTexture9", "AddDirtyBox", {IN(0, 24)}},
    {"IDirect3DSurface9", "GetDesc", {OUT(0, 32)}},
    {"IDirect3DSurface9", "LockRect", {OUT(0, 8), IN(1, 16), OP(A_LOCK_SURF)}},
    {"IDirect3DSurface9", "UnlockRect", {OP(A_UNLOCK_SURF)}},
    {"IDirect3DVertexBuffer9", "Lock", {OUT(2, 4), OP(A_LOCK_BUF)}},
    {"IDirect3DVertexBuffer9", "Unlock", {OP(A_UNLOCK_BUF)}},
    {"IDirect3DVertexBuffer9", "GetDesc", {OUT(0, 24)}},
    {"IDirect3DIndexBuffer9", "Lock", {OUT(2, 4), OP(A_LOCK_BUF)}},
    {"IDirect3DIndexBuffer9", "Unlock", {OP(A_UNLOCK_BUF)}},
    {"IDirect3DIndexBuffer9", "GetDesc", {OUT(0, 20)}},
    {"IDirect3DVertexDeclaration9", "GetDeclaration", {OUT(1, 4), OUT_SIZED(0, 1, 8)}},
    {"IDirect3DVertexShader9", "GetFunction", {OUT(1, 4), OUT_SIZED(0, 1, 1)}},
    {"IDirect3DPixelShader9", "GetFunction", {OUT(1, 4), OUT_SIZED(0, 1, 1)}},
    {"IDirect3DSwapChain9", "Present",
     {OPA(A_PRE_RECT, 0), OPA(A_PRE_RECT, 1), OPA(A_PRE_RGN, 3), OP(A_FRAME)}},
    {"IDirect3DSwapChain9", "GetFrontBufferData", {OP(A_END)}},
    {"IDirect3DSwapChain9", "GetBackBuffer", {OUT(2, 4)}},
    {"IDirect3DSwapChain9", "GetRasterStatus", {OUT(0, 8)}},
    {"IDirect3DSwapChain9", "GetDisplayMode", {OUT(0, 16)}},
    {"IDirect3DSwapChain9", "GetPresentParameters", {OUT(0, 56)}},
    {"ID3DXConstantTable", "GetConstantDesc", {OUT(1, 48), OUT(2, 4)}},

    /* Every interface's; after the specific rows, so a specific row wins. */
    {NULL, "QueryInterface", {IN(0, 16), OUT(1, 4)}},
    {NULL, "GetDevice", {OUT(0, 4)}},
    {NULL, "GetContainer", {IN(0, 16), OUT(1, 4)}},
    {NULL, "SetPrivateData", {IN(0, 16), IN_VEC(1, 2, 1)}},
    {NULL, "GetPrivateData", {IN(0, 16), OUT(2, 4), OUT_SIZED(1, 2, 1)}},
    {NULL, "FreePrivateData", {IN(0, 16)}},
};
#define RULE_COUNT ((uint32_t)(sizeof RULES / sizeof RULES[0]))

/* The SA2S_D3DX entries, indexed by entry - 1, with the arguments each takes (the
 * import's own count, runtime/sa2_d3d9.h). Direct3DCreate9's SDK version is not
 * seen where it is wrapped (sa2_d3d9_make_d3d9 takes only the caller), so it
 * carries no argument. */
#define FILE_(x, k)   {A_FILE, x, k, 0, 0, 0}
#define DX_COUNT 13u                      /* entries 1..12; 0 unused */
static const rec_entry ENTRIES[DX_COUNT - 1u] = {
    {SA2S_D3DX_TEXTURE_FROM_MEMORY, "D3DXCreateTextureFromFileInMemoryEx", 15,
     {FILE_(1, 2), OUT(14, 4), OUT(12, 28), OUT(13, 1024), OPA(A_FILE_DESC, 14)}},
    {SA2S_D3DX_CREATE_TEXTURE, "D3DXCreateTexture", 8,
     {OUT(7, 4), NEW(A_NEW_TEX, 7, 1, 2, 5)}},
    {SA2S_D3DX_FILTER_TEXTURE, "D3DXFilterTexture", 4, {IN(1, 1024)}},
    {SA2S_D3DX_LOAD_SURFACE, "D3DXLoadSurfaceFromSurface", 8,
     {IN(1, 1024), IN(2, 16), IN(4, 1024), IN(5, 16)}},
    {SA2S_D3DX_GET_CONSTANT_TABLE, "D3DXGetShaderConstantTable", 2,
     {OPA(A_SHADER, 0), OUT(1, 4)}},
    {SA2S_D3DX_CUBE_TEXTURE_FROM_MEMORY, "D3DXCreateCubeTextureFromFileInMemoryEx", 14,
     {FILE_(1, 2), OUT(13, 4), OUT(11, 28), OUT(12, 1024), OPA(A_FILE_DESC, 13)}},
    {SA2S_D3DX_VOLUME_TEXTURE_FROM_MEMORY, "D3DXCreateVolumeTextureFromFileInMemoryEx", 16,
     {FILE_(1, 2), OUT(15, 4), OUT(13, 28), OUT(14, 1024), OPA(A_FILE_DESC, 15)}},
    {SA2S_D3DX_CREATE_CUBE_TEXTURE, "D3DXCreateCubeTexture", 7,
     {OUT(6, 4), NEW(A_NEW_CUBE, 6, 1, 0, 4)}},
    {SA2S_D3DX_CREATE_VOLUME_TEXTURE, "D3DXCreateVolumeTexture", 9,
     {OUT(8, 4), NEW(A_NEW_VOL, 8, 1, 2, 6)}},
    {SA2S_D3DX_SAVE_SURFACE_TO_FILE, "D3DXSaveSurfaceToFileA", 5, {OP(A_END)}},
    {SA2S_D3DX_SAVE_TEXTURE_TO_FILE, "D3DXSaveTextureToFileA", 4, {OP(A_END)}},
    {SA2S_D3DX_DIRECT3D_CREATE9, "Direct3DCreate9", 0, {OP(A_OBJECT_EAX)}},
};

/* The audio ops (SA2_REC_AUDIO=1): host/sa2_stream.h's `audio` lines, indexed by op
 * - 1, each keyed by the device's own interface and method names and held at start-
 * up to the device's own argument count (au_tables_ok). AOPS are the line grammar's
 * words, as OPS are the rule grammar's. */
enum {
    AA_END = 0, AA_OUT, AA_IN_MAT, AA_PCM, AA_SUBMIT, AA_DS_STATE, AA_DS_NEW, AA_XA_STATE,
    AA_XA_NEW, AA_STORE_HASH, AA_COUNT
};
static const struct { const char *name, *operands; } AOPS[AA_COUNT] = {
    [AA_END] = {"", ""}, [AA_OUT] = {"out", "an"}, [AA_IN_MAT] = {"in", "abc"},
    [AA_PCM] = {"pcm", "ab"}, [AA_SUBMIT] = {"submit", "a"},
    [AA_DS_STATE] = {"ds_state", ""}, [AA_DS_NEW] = {"ds_new", "a"},
    [AA_XA_STATE] = {"xa_state", ""}, [AA_XA_NEW] = {"xa_new", "a"},
    [AA_STORE_HASH] = {"store_hash", ""},
};
#define AU_MATRIX_MAX 64u                    /* `in A B C`: no blob past args[B] or args[C] of 64 */
#define AU_ACTS 6
typedef struct { uint8_t op, a, b, c; uint32_t n; } au_act;
typedef struct {
    uint32_t op;                     /* SA2S_AU_* */
    const char *name;                /* the op's NAME in host/sa2_stream.h */
    uint32_t api;                    /* SA2S_AU_DSOUND or SA2S_AU_XAUDIO2 */
    const char *iface, *method;      /* the device's own names */
    uint32_t argc;                   /* the device's own count */
    au_act act[AU_ACTS];
} au_rule;

#define AU_OUT(x, n)  {AA_OUT, x, 0, 0, n}
#define AU_OP(o)      {o, 0, 0, 0, 0}
#define AU_OPA(o, x)  {o, x, 0, 0, 0}
#define AU_DS SA2S_AU_DSOUND
#define AU_XA SA2S_AU_XAUDIO2

static const au_rule AU_RULES[SA2S_AU_OP_COUNT - 1] = {
    {SA2S_AU_DS_CREATE_BUFFER, "DS_CREATE_BUFFER", AU_DS, "IDirectSound8", "CreateSoundBuffer", 3,
     {AU_OUT(1, 4), AU_OPA(AA_DS_NEW, 1)}},
    {SA2S_AU_DS_DUPLICATE, "DS_DUPLICATE", AU_DS, "IDirectSound8", "DuplicateSoundBuffer", 2,
     {AU_OUT(1, 4), AU_OPA(AA_DS_NEW, 1)}},
    {SA2S_AU_DS_COOPERATIVE, "DS_COOPERATIVE", AU_DS, "IDirectSound8", "SetCooperativeLevel", 2,
     {AU_OP(AA_END)}},
    {SA2S_AU_DS_LOCK, "DS_LOCK", AU_DS, "IDirectSoundBuffer8", "Lock", 7,
     {AU_OUT(2, 4), AU_OUT(3, 4), AU_OUT(4, 4), AU_OUT(5, 4), AU_OP(AA_DS_STATE)}},
    {SA2S_AU_DS_UNLOCK, "DS_UNLOCK", AU_DS, "IDirectSoundBuffer8", "Unlock", 4,
     {{AA_PCM, 0, 1, 0, 0}, {AA_PCM, 2, 3, 0, 0}, AU_OP(AA_DS_STATE), AU_OP(AA_STORE_HASH)}},
    {SA2S_AU_DS_PLAY, "DS_PLAY", AU_DS, "IDirectSoundBuffer8", "Play", 3, {AU_OP(AA_DS_STATE)}},
    {SA2S_AU_DS_STOP, "DS_STOP", AU_DS, "IDirectSoundBuffer8", "Stop", 0, {AU_OP(AA_DS_STATE)}},
    {SA2S_AU_DS_SET_POSITION, "DS_SET_POSITION", AU_DS, "IDirectSoundBuffer8", "SetCurrentPosition", 1,
     {AU_OP(AA_DS_STATE)}},
    {SA2S_AU_DS_SET_FORMAT, "DS_SET_FORMAT", AU_DS, "IDirectSoundBuffer8", "SetFormat", 1,
     {AU_OP(AA_DS_STATE)}},
    {SA2S_AU_DS_SET_VOLUME, "DS_SET_VOLUME", AU_DS, "IDirectSoundBuffer8", "SetVolume", 1,
     {AU_OP(AA_DS_STATE)}},
    {SA2S_AU_DS_SET_PAN, "DS_SET_PAN", AU_DS, "IDirectSoundBuffer8", "SetPan", 1, {AU_OP(AA_DS_STATE)}},
    {SA2S_AU_DS_SET_FREQUENCY, "DS_SET_FREQUENCY", AU_DS, "IDirectSoundBuffer8", "SetFrequency", 1,
     {AU_OP(AA_DS_STATE)}},
    {SA2S_AU_DS_GET_POSITION, "DS_GET_POSITION", AU_DS, "IDirectSoundBuffer8", "GetCurrentPosition", 2,
     {AU_OUT(0, 4), AU_OUT(1, 4), AU_OP(AA_DS_STATE)}},
    {SA2S_AU_DS_GET_STATUS, "DS_GET_STATUS", AU_DS, "IDirectSoundBuffer8", "GetStatus", 1,
     {AU_OUT(0, 4), AU_OP(AA_DS_STATE)}},
    {SA2S_AU_DS_RELEASE, "DS_RELEASE", AU_DS, "IDirectSoundBuffer8", "Release", 0, {AU_OP(AA_DS_STATE)}},
    {SA2S_AU_XA_CREATE_MASTER, "XA_CREATE_MASTER", AU_XA, "IXAudio2", "CreateMasteringVoice", 6,
     {AU_OUT(0, 4), AU_OPA(AA_XA_NEW, 0)}},
    {SA2S_AU_XA_CREATE_SOURCE, "XA_CREATE_SOURCE", AU_XA, "IXAudio2", "CreateSourceVoice", 7,
     {AU_OUT(0, 4), AU_OPA(AA_XA_NEW, 0)}},
    {SA2S_AU_XA_MASTER_DETAILS, "XA_MASTER_DETAILS", AU_XA, "IXAudio2MasteringVoice", "GetVoiceDetails", 1,
     {AU_OUT(0, 12), AU_OP(AA_XA_STATE)}},
    {SA2S_AU_XA_MASTER_DESTROY, "XA_MASTER_DESTROY", AU_XA, "IXAudio2MasteringVoice", "DestroyVoice", 0,
     {AU_OP(AA_XA_STATE)}},
    {SA2S_AU_XA_SET_VOLUME, "XA_SET_VOLUME", AU_XA, "IXAudio2SourceVoice", "SetVolume", 2,
     {AU_OP(AA_XA_STATE)}},
    {SA2S_AU_XA_SET_MATRIX, "XA_SET_MATRIX", AU_XA, "IXAudio2SourceVoice", "SetOutputMatrix", 5,
     {{AA_IN_MAT, 3, 1, 2, 0}, AU_OP(AA_XA_STATE)}},
    {SA2S_AU_XA_DESTROY, "XA_DESTROY", AU_XA, "IXAudio2SourceVoice", "DestroyVoice", 0, {AU_OP(AA_XA_STATE)}},
    {SA2S_AU_XA_START, "XA_START", AU_XA, "IXAudio2SourceVoice", "Start", 2, {AU_OP(AA_XA_STATE)}},
    {SA2S_AU_XA_STOP, "XA_STOP", AU_XA, "IXAudio2SourceVoice", "Stop", 2, {AU_OP(AA_XA_STATE)}},
    {SA2S_AU_XA_SUBMIT, "XA_SUBMIT", AU_XA, "IXAudio2SourceVoice", "SubmitSourceBuffer", 2,
     {AU_OPA(AA_SUBMIT, 0), AU_OP(AA_XA_STATE)}},
    {SA2S_AU_XA_FLUSH, "XA_FLUSH", AU_XA, "IXAudio2SourceVoice", "FlushSourceBuffers", 0,
     {AU_OP(AA_XA_STATE)}},
    {SA2S_AU_XA_GET_STATE, "XA_GET_STATE", AU_XA, "IXAudio2SourceVoice", "GetState", 1,
     {AU_OUT(0, 16), AU_OP(AA_XA_STATE)}},
    {SA2S_AU_XA_SET_RATIO, "XA_SET_RATIO", AU_XA, "IXAudio2SourceVoice", "SetFrequencyRatio", 2,
     {AU_OP(AA_XA_STATE)}},
};

/* ------------------------------------------------------------ the state */

/* One per recording-device object, from the calls that made it: what a Lock of it
 * covers. Host memory, indexed like the recording device's own pool. */
typedef struct {
    uint8_t  type;           /* T_* + 1; 0 when no creating call was seen */
    uint8_t  level;          /* a level surface's level */
    uint32_t container;      /* a level surface's texture, a guest address */
    uint32_t w, h, fmt;      /* level 0, or the surface's own */
    uint32_t len;            /* a buffer's length */
    /* the buffer watch: the bytes the last Unlock copied, and their hash */
    uint8_t  w_on, w_listed;
    uint32_t w_bits, w_bytes;
    uint64_t w_hash;
} rec_obj;

/* The locks open now. A texture has one per level, a surface one, and a buffer a
 * stack (D3D9 lets its Locks nest); an Unlock closes the matching one, a buffer's
 * the last opened. Host memory. */
enum { LK_SURF = 1, LK_TEX = 2, LK_BUF = 3 };
typedef struct {
    uint8_t  kind;           /* LK_*; 0 when the slot is free */
    uint8_t  sized;          /* the lock's size is known */
    uint32_t object, level;  /* the object the Lock was called on, and a texture's level */
    uint32_t order;          /* when it was opened, for a buffer's stack */
    sa2s_lock_info info;
} rec_lock;

typedef struct {
    uint16_t role, arg;
    uint32_t len;
    uint32_t gaddr;          /* guest source when `host` is NULL */
    const void *host;
} rec_blob;

/* One call being recorded, on the host stack of the thread making it. Everything
 * before `pp` is cleared per call (call_reset); the buffers after it are written
 * before they are read, under the flags in the first part. */
typedef struct {
    uint32_t idx, t, slot, argc, self, thread, eax;
    uint32_t a[REC_ARGS];
    const rec_act *act;
    int nb, frame, nnotes, pp_ok, rect_ok[2];
    uint32_t rgn_len;
    rec_blob b[REC_MAX_BLOBS];
    /* before-the-call copies */
    uint32_t pp[14];
    uint32_t rect[2][4];
    uint32_t rgn[8 + 4 * REC_RGN_RECTS];
    /* host-made blobs */
    sa2s_desc_device ddev;
    sa2s_desc_file_texture dtex;
    sa2s_lock_info lock;
    /* notes about this call, written after its record */
    char notes[REC_CALL_NOTES][REC_NOTE_BYTES];
} rec_call;
#define call_reset(c) memset((c), 0, offsetof(rec_call, pp))

static int g_init;
static int g_count_on;       /* the wrappers count */
static int g_stream_on;      /* ... and write the stream */
static int g_types_ok;
static const char *g_trace_path, *g_counts_path;
static char g_status_path[4096];
static char g_why[512];      /* why the stream stopped; empty while it runs */

static sa2_lifted_fn g_real[REC_THUNKS];
static sa2_lifted_fn g_real_any;
static uint16_t g_rule_of[REC_THUNKS];      /* RULES index + 2; 1 none; 0 not looked up */
static int16_t  g_argc_of[REC_THUNKS];      /* argc + 2; 1 unnamed; 0 not looked up */
static uint8_t  g_out_flag[REC_THUNKS];     /* the device flags the method "out" */
static uint8_t  g_norule_noted[REC_THUNKS];
static uint64_t g_entered[REC_THUNKS], g_returned[REC_THUNKS];
static uint32_t g_thunks_wrapped, g_regs_passed;

static uint64_t g_dx_entered[DX_COUNT], g_dx_returned[DX_COUNT];

static rec_obj *g_objs;
static rec_lock g_locks[REC_LOCKS];
static uint32_t g_lock_order;

/* The stream writer: records are built whole in g_buf and written out after every
 * Present's records, when the buffer fills, and at exit. */
static int      g_fd = -1;
static int      g_pace_fd = -1;    /* SA2_REC_PACE_FD, -1 once pacing has ended */
static uint8_t *g_buf;
static uint32_t g_cap, g_len;
static uint32_t g_seq;
static uint64_t g_file_bytes, g_flushes;
static uint64_t g_kind_n[8], g_kind_bytes[8];
static uint64_t g_role_n[16], g_role_bytes[16];
static uint64_t g_frames;

/* The note classes: N_COUNT of them in every stream, and "audio" after them in a
 * stream with audio only, so a stream without audio names the classes it always did. */
enum { N_BLOB = 0, N_WALK, N_LOCK, N_SHADOW, N_ARGS, N_TEXROW, N_NORULE, N_WATCH, N_COUNT,
       N_AUDIO = N_COUNT, N_ALL };
static const char *const NOTE_CLASSES[N_ALL] = {
    "blob-left-out", "walk-without-end", "lock", "unknown-object", "args", "texture-row",
    "out-without-rule", "buffer-changed", "audio",
};
static uint64_t g_note_n[N_ALL];

/* The game's state (SA2_REC_STATE=1, above): the values last said, and how many state notes. */
#define ST_GAME_MODE   0x0174B040u   /* GameMode, int32 */
#define ST_LEVEL       0x01934B70u   /* CurrentLevel, int16 */
#define ST_CHARACTER   0x01934B80u   /* CurrentCharacter (player one), int32 */
#define ST_TWO_PLAYER  0x0174AFDEu   /* TwoPlayerMode, byte */
#define ST_COSTUME     0x0174B015u   /* player one's costume, byte (+1 is player two's) */
#define ST_FIELDS      5
#define REC_UNCOUNTED  8u            /* a kind past g_kind_n: wr_commit counts it nowhere */
static int      g_state_on, g_state_said;
static int32_t  g_state_last[ST_FIELDS];
static uint64_t g_state_n;

/* Audio (SA2_REC_AUDIO=1): each thunk's native and op (0: not recorded), and the
 * counts by op. Host memory, touched only by the thread holding the guest lock. */
#define AU_DS_THUNKS (SA2_DS_MAX_TYPES * SA2_DS_MAX_SLOTS)
#define AU_XA_THUNKS (SA2_XA2_MAX_TYPES * SA2_XA2_MAX_SLOTS)
static int g_audio_on;
static sa2_lifted_fn g_au_real_ds[AU_DS_THUNKS], g_au_real_xa[AU_XA_THUNKS];
static sa2_lifted_fn g_au_any_ds, g_au_any_xa;
static uint8_t g_au_op_ds[AU_DS_THUNKS], g_au_op_xa[AU_XA_THUNKS];
static uint64_t g_au_entered[SA2S_AU_OP_COUNT], g_au_returned[SA2S_AU_OP_COUNT];
static uint64_t g_au_recorded[SA2S_AU_OP_COUNT], g_au_failed[SA2S_AU_OP_COUNT];
static uint64_t g_au_pcm_bytes;
static uint32_t g_au_wrapped;

/* The DirectSound stores (the store watch, above): one row per backing store a
 * buffer's STATE named after a Lock or an Unlock, by guest address, with the Locks
 * open on it and its watch. A store is a runtime arena block the device hands on
 * only after its last holder's final Release with no Lock open, only to a buffer of
 * the same length, and with its bytes as they were (runtime/sa2_dsound.c, "the
 * backing stores"), so the row goes on describing the block and is never retired.
 * host/sa2_stream.h's "one backing store per buffer" predates that; the device's
 * comment is the account of it.
 *
 * The bound is AU_STORES rows (SA2_DS_MAX_OBJECTS, 512), half the device's
 * SA2_DS_MAX_STORES (1024). A store at a 513th distinct address gets no row: its
 * Locks are not watched and its Unlocks carry no store_hash, each noted here ("more
 * than 512 stores"), and tools/s5/stream_dump.py and the mixer report each such
 * Unlock as a problem, so a stream past the bound fails loudly. SA2 cannot reach it.
 * It locks buffers of two lengths, 65 536 and 131 072 bytes, and a block keeps its
 * address when it is handed on, so the rows are at most the blocks of 65 536 bytes
 * or more the runtime arena holds: 10 485 760 / 65 536 = 160. Host memory, touched
 * only by the thread holding the guest lock. */
#define AU_STORES SA2_DS_MAX_OBJECTS
typedef struct {
    uint32_t mem, len;       /* the STATE's mem and mem_len */
    uint32_t open;           /* Locks open on it */
    uint32_t on;             /* watched: its last Lock closed, and none opened since */
    uint64_t hash;           /* FNV-1a of its len bytes when the watch was set or last checked */
} au_store;
static au_store g_au_store[AU_STORES];
static uint32_t g_au_nstores;
static uint64_t g_au_sw_set, g_au_sw_checks, g_au_sw_hashed, g_au_sw_changed;
static uint32_t g_au_sw_on;              /* stores watched now */
/* SA2_REC_AUDIO_WATCH=0, a live session's (the launcher's and the Android session's play): no
 * store is hashed again after its Unlock, at its next Lock or at every REC_WATCH_EVERY-th FRAME;
 * the Unlock's store_hash, which the stream carries, is made as before. The watch only notes a
 * guest that wrote a store outside a Lock, and it held the guest lock for 3.5% of the game's
 * samples in a live stage (au_store_verify; the thermal unit's profile, 2026-10-12). */
static int g_au_watch = 1;
/* SA2_REC_STORE_HASH_EVERY: the Unlocks that closed a store's last Lock (over every store), and
 * those of them that carry no store_hash because of it. */
static uint32_t g_au_hash_every = 1;
static uint64_t g_au_closes, g_au_hash_skipped;

static uint32_t note_classes(void) { return g_audio_on ? N_ALL : N_COUNT; }

/* The buffer watch: the pool indices of the buffers whose bytes are watched. */
static uint32_t *g_watch;
static uint32_t g_nwatch;
static uint64_t g_watch_set, g_watch_checks, g_watch_hashed, g_watch_changed;

#ifdef REC_BREAK_GLOBAL
static rec_call g_break_call;
#endif

/* ------------------------------------------------------------ small things */

static uint32_t pad4(uint32_t n) { return (n + 3u) & ~3u; }

/* The guest range sa2_gp accepts (runtime/guest_memory.h), without its pointer. */
static int in_guest(uint32_t addr, uint64_t len) {
    return addr >= SA2_GUEST_NULL_GUARD && len <= SA2_GUEST_END &&
           (uint64_t)addr <= (uint64_t)SA2_GUEST_END - len;
}

static int peek32(uint32_t addr, uint32_t *v) {
    return sa2_peek(addr, 4, v) == SA2_OK;
}

/* The recording device's pool index at `addr`, in either of its blocks
 * (runtime/sa2_d3d9.h), or -1. */
static int obj_index(uint32_t addr) {
    return (int)sa2_d3d9_obj_index(addr);
}
static rec_obj *obj_at(uint32_t addr) {
    const int i = obj_index(addr);
    return (i >= 0 && g_objs) ? &g_objs[i] : NULL;
}

static uint32_t level_dim(uint32_t v, uint32_t level) {     /* runtime/sa2_d3d9.c's */
    while (level-- && v > 1u) v >>= 1;
    return v ? v : 1u;
}
static int fmt_is_block(uint32_t f) {                       /* DXT1..DXT5 */
    return f == 0x31545844u || f == 0x32545844u || f == 0x33545844u ||
           f == 0x34545844u || f == 0x35545844u;
}
static const char *call_name(const rec_call *c) {
    return c->t < T_COUNT ? TYPE_NAMES[c->t]
                          : (c->slot >= 1u && c->slot < DX_COUNT ? ENTRIES[c->slot - 1u].name : "?");
}

/* ------------------------------------------------------------ the sidecar */

static void status_write(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void status_write(const char *fmt, ...) {
    if (!g_status_path[0]) return;
    FILE *f = fopen(g_status_path, "w");
    if (!f) return;
    va_list ap;
    va_start(ap, fmt);
    vfprintf(f, fmt, ap);
    va_end(ap);
    fputc('\n', f);
    fclose(f);
}

/* The stream stops; the counting goes on. Says why in the sidecar. */
static void rec_disable(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void rec_disable(const char *fmt, ...) {
    if (!g_why[0]) {
        va_list ap;
        va_start(ap, fmt);
        vsnprintf(g_why, sizeof g_why, fmt, ap);
        va_end(ap);
    }
    if (g_stream_on) {
        g_stream_on = 0;
        if (g_fd >= 0) close(g_fd);
        g_fd = -1;
    }
    status_write("disabled: %s", g_why);
}

/* ------------------------------------------------------------ the writer */

static int wr_flush(void) {
    uint32_t done = 0;
    while (done < g_len) {
        const ssize_t k = write(g_fd, g_buf + done, g_len - done);
        if (k < 0 && errno == EINTR) continue;
        if (k <= 0) {
            rec_disable("a write to %s failed after %llu bytes: %s", g_trace_path,
                        (unsigned long long)(g_file_bytes + done),
                        k < 0 ? strerror(errno) : "nothing written");
            g_len = 0;
            return 0;
        }
        done += (uint32_t)k;
    }
    g_file_bytes += g_len;
    g_len = 0;
    g_flushes++;
    return 1;
}

/* The pace (SA2_REC_PACE_FD): one byte after a frame's records are out (REC_BREAK_NOFLUSH
 * leaves the frame in the buffer, and so reads none). */
#ifndef REC_BREAK_NOFLUSH
static void pace_wait(void) {
    if (g_pace_fd < 0 || !g_stream_on) return;
    uint8_t b;
    for (;;) {
        const ssize_t k = read(g_pace_fd, &b, 1);
        if (k == 1) return;
        if (k < 0 && errno == EINTR) continue;
        if (k < 0)
            status_write("recording, pid %ld; frames unpaced from frame %llu on: the pace pipe "
                         "(SA2_REC_PACE_FD=%d) does not read: %s", (long)getpid(),
                         (unsigned long long)g_frames, g_pace_fd, strerror(errno));
        close(g_pace_fd);
        g_pace_fd = -1;
        return;
    }
}
#endif

/* Room for `n` more bytes in one piece, flushing or growing the buffer. */
static int wr_reserve(uint64_t n) {
    if (!g_stream_on) return 0;
    if (n > (uint64_t)REC_FILE_CAP + (1u << 20)) {
        rec_disable("a record of %llu bytes is past the writer's limit",
                    (unsigned long long)n);
        return 0;
    }
    if (g_cap - g_len >= n) return 1;
    if (!wr_flush()) return 0;
    if (g_cap >= n) return 1;
    uint8_t *p = (uint8_t *)realloc(g_buf, (size_t)n);
    if (!p) {
        rec_disable("the buffer could not grow to %llu bytes", (unsigned long long)n);
        return 0;
    }
    g_buf = p;
    g_cap = (uint32_t)n;
    return 1;
}

static void put32(uint8_t *p, uint32_t *off, uint32_t v) {
    memcpy(p + *off, &v, 4);
    *off += 4u;
}

/* The record at g_buf + g_len, `off` bytes long, is complete: number it. */
static void wr_commit(uint32_t kind, uint32_t off) {
    uint8_t *p = g_buf + g_len;
    const uint32_t seq = g_seq++;
    memcpy(p + 4, &off, 4);
    memcpy(p + 8, &seq, 4);
    g_len += off;
    if (kind < 8u) { g_kind_n[kind]++; g_kind_bytes[kind] += off; }
}

/* A SA2S_NOTE of `text`, counted as `count_as` (SA2S_NOTE, or REC_UNCOUNTED for a state
 * note, which g_state_n counts). */
static void emit_note_as(const char *text, uint32_t count_as) {
    const uint32_t len = (uint32_t)strlen(text);
    if (!wr_reserve(16u + pad4(len))) return;
    uint8_t *p = g_buf + g_len;
    uint32_t off = 0;
    put32(p, &off, SA2S_NOTE);
    put32(p, &off, 0);
    put32(p, &off, 0);
    put32(p, &off, len);
    memcpy(p + off, text, len);
    off += len;
    while (off & 3u) p[off++] = 0;
    wr_commit(count_as, off);
}

static void emit_note_text(const char *text) { emit_note_as(text, SA2S_NOTE); }

/* SA2_REC_STATE: the state note, when a value has changed since the last one said. */
static void state_check(void) {
    int32_t mode = 0, ch = 0;
    int16_t level = 0;
    uint8_t two = 0, costume = 0;
    int32_t v[ST_FIELDS];
    v[0] = sa2_peek(ST_GAME_MODE, 4, &mode) == SA2_OK ? mode : -1;
    v[1] = sa2_peek(ST_LEVEL, 2, &level) == SA2_OK ? (int32_t)level : -1;
    v[2] = sa2_peek(ST_CHARACTER, 4, &ch) == SA2_OK ? ch : -1;
    v[3] = sa2_peek(ST_TWO_PLAYER, 1, &two) == SA2_OK ? (int32_t)two : -1;
    v[4] = sa2_peek(ST_COSTUME, 1, &costume) == SA2_OK ? (int32_t)costume : -1;
    int same = g_state_said;
    for (int i = 0; i < ST_FIELDS; i++) {
        if (v[i] != g_state_last[i]) same = 0;
        g_state_last[i] = v[i];
    }
    if (same) return;
    g_state_said = 1;
    char text[128];
    snprintf(text, sizeof text, "state mode=%d level=%d character=%d two=%d alt=%d", (int)v[0], (int)v[1],
             (int)v[2], (int)v[3], (int)v[4]);
    emit_note_as(text, REC_UNCOUNTED);
    g_state_n++;
}

/* A note of class `cls`: the first REC_NOTES_PER_CLASS of each class are written,
 * the rest counted (the counts file has them all). A note about a call being
 * recorded (`c`) waits in the call and follows its record; one made with no call
 * is written at once. */
static void rec_note(rec_call *c, int cls, const char *fmt, ...)
    __attribute__((format(printf, 3, 4)));
static void rec_note(rec_call *c, int cls, const char *fmt, ...) {
    const uint64_t k = g_note_n[cls]++;
    if (!g_stream_on || k > REC_NOTES_PER_CLASS) return;
    char text[REC_NOTE_BYTES];
    int n = snprintf(text, sizeof text, "%s: ", NOTE_CLASSES[cls]);
    if (n < 0) n = 0;
    if (k == REC_NOTES_PER_CLASS) {
        snprintf(text + n, sizeof text - (size_t)n,
                 "further notes of this class are counted, not written");
    } else {
        va_list ap;
        va_start(ap, fmt);
        vsnprintf(text + n, sizeof text - (size_t)n, fmt, ap);
        va_end(ap);
    }
    if (!c) {
        emit_note_text(text);
    } else if (c->nnotes < REC_CALL_NOTES) {
        memcpy(c->notes[c->nnotes++], text, sizeof text);
    }
}

static void watch_check(void);
static void au_store_check(void);

static void emit_frame(void) {
    if (!wr_reserve(28u)) return;
    sa2_wnd_stats ws;
    memset(&ws, 0, sizeof ws);
    sa2_window_stats(&ws);
    const uint64_t us = sa2_virtual_us();
    const uint64_t frames = sa2_sched_frames();
    uint8_t *p = g_buf + g_len;
    uint32_t off = 0;
    put32(p, &off, SA2S_FRAME);
    put32(p, &off, 0);
    put32(p, &off, 0);
    put32(p, &off, (uint32_t)us);
    put32(p, &off, (uint32_t)(us >> 32));
    put32(p, &off, (uint32_t)frames);
    put32(p, &off, ws.polls);
    wr_commit(SA2S_FRAME, off);
    g_frames++;
    if (g_state_on) state_check();
#ifndef REC_BREAK_NOWATCH
    if (g_nwatch && g_frames % REC_WATCH_EVERY == 0u) watch_check();
#else
    (void)watch_check;
#endif
#ifndef REC_BREAK_AUNOWATCH
    if (g_au_watch && g_au_nstores && g_frames % REC_WATCH_EVERY == 0u) au_store_check();
#else
    (void)au_store_check;
#endif
}

/* A SA2S_CALL (entry 0) or SA2S_D3DX (entry != 0) record for `c`; then, for a
 * Present, its SA2S_FRAME; then the call's notes; then, for a Present, a flush. A
 * blob whose guest bytes do not read is left out, never cut short, and noted. */
static void emit_call(rec_call *c, uint32_t entry) {
    /* head; entry and argc, or type/slot, this, thread and argc; args; eax, nblobs */
    uint64_t planned = 12u + (entry ? 8u : 16u) + 4u * (uint64_t)c->argc + 8u;
    for (int i = 0; i < c->nb; i++) planned += 8u + pad4(c->b[i].len);
    if (!wr_reserve(planned)) return;
    uint8_t *p = g_buf + g_len;
    uint32_t off = 0, failed[REC_MAX_BLOBS], nfail = 0;
    put32(p, &off, entry ? SA2S_D3DX : SA2S_CALL);
    put32(p, &off, 0);
    put32(p, &off, 0);
    if (entry) {
        put32(p, &off, entry);
    } else {
        const uint16_t ts[2] = {(uint16_t)c->t, (uint16_t)c->slot};
        memcpy(p + off, ts, 4);
        off += 4u;
        put32(p, &off, c->self);
        put32(p, &off, c->thread);
    }
    put32(p, &off, c->argc);
    for (uint32_t k = 0; k < c->argc; k++) put32(p, &off, c->a[k]);
    put32(p, &off, c->eax);
    const uint32_t nb_at = off;
    put32(p, &off, 0);
    uint32_t nb = 0;
    for (int i = 0; i < c->nb; i++) {
        const rec_blob *b = &c->b[i];
        const uint32_t at = off;
        const uint16_t head[2] = {b->role, b->arg};
        memcpy(p + off, head, 4);
        off += 4u;
        put32(p, &off, b->len);
        if (b->host) {
            memcpy(p + off, b->host, b->len);
        } else if (sa2_peek(b->gaddr, b->len, p + off) != SA2_OK) {
            off = at;                              /* left out, not cut short */
            if (nfail < REC_MAX_BLOBS) failed[nfail++] = (uint32_t)i;
            continue;
        }
        off += b->len;
#ifndef REC_BREAK_NOPAD
        while (off & 3u) p[off++] = 0;
#endif
        nb++;
        if (b->role < 8u) { g_role_n[b->role]++; g_role_bytes[b->role] += b->len; }
    }
    memcpy(p + nb_at, &nb, 4);
    wr_commit(entry ? SA2S_D3DX : SA2S_CALL, off);
    const uint32_t seq = g_seq - 1u;
    if (c->frame) emit_frame();
    for (uint32_t i = 0; i < nfail; i++) {
        const rec_blob *b = &c->b[failed[i]];
        rec_note(c, N_BLOB, "the %u bytes at %#010x (role %u, arg %u) did not read",
                 b->len, b->gaddr, b->role, b->arg);
    }
    for (int i = 0; i < c->nnotes; i++) {
        char text[REC_NOTE_BYTES + 32];
        snprintf(text, sizeof text, "record %u: %s", seq, c->notes[i]);
        emit_note_text(text);
    }
    c->nnotes = 0;
#ifndef REC_BREAK_NOFLUSH
    if (c->frame && wr_flush()) pace_wait();
#endif
}

/* ------------------------------------------------------------ blobs */

static void blob_add(rec_call *c, uint16_t role, uint16_t arg, uint32_t gaddr,
                     const void *host, uint64_t len, uint64_t cap) {
    if (!len) return;
    if (!host && !gaddr) return;                   /* a null pointer carries nothing */
    if (len > cap) {
        rec_note(c, N_BLOB, "%s slot %u arg %u: %llu bytes is over the %llu-byte cap",
                 call_name(c), c->slot, arg, (unsigned long long)len, (unsigned long long)cap);
        return;
    }
    if (!host && !in_guest(gaddr, len)) {
        rec_note(c, N_BLOB, "%s slot %u arg %u: %llu bytes at %#010x are outside guest memory",
                 call_name(c), c->slot, arg, (unsigned long long)len, gaddr);
        return;
    }
    if (c->nb >= REC_MAX_BLOBS) {
        rec_note(c, N_BLOB, "more than %d blobs on one call", REC_MAX_BLOBS);
        return;
    }
    rec_blob *b = &c->b[c->nb++];
    b->role = role;
    b->arg = arg;
    b->len = (uint32_t)len;
    b->gaddr = gaddr;
    b->host = host;
}
static void blob_guest(rec_call *c, uint16_t role, uint32_t arg, uint64_t len) {
    if (arg >= c->argc) return;
    blob_add(c, role, (uint16_t)arg, c->a[arg], NULL, len, REC_BLOB_CAP);
}

/* The shader token stream at `p`: the version token (a vertex shader's high half
 * 0xFFFE, a pixel shader's 0xFFFF), then each instruction or comment, to the
 * 0x0000FFFF end token, both ends included. A comment is skipped by its own length.
 * From shader model 2 an instruction is skipped by its length field (bits 24-27);
 * below it there is none, so `def` is skipped by its five tokens (a destination and
 * four literals of any value) and any other instruction by its parameter tokens,
 * which have bit 31 set. So neither a comment nor a literal holding 0x0000FFFF ends
 * it. 0 when it is not a shader, no end token is found in 65536 tokens, or a token
 * does not read. */
static uint32_t shader_bytes(uint32_t p) {
    uint32_t version = 0;
    if (!p || !peek32(p, &version)) return 0;
    if ((version >> 16) != 0xFFFEu && (version >> 16) != 0xFFFFu) return 0;
    const uint32_t major = (version >> 8) & 0xFFu;
    uint32_t n = 1;
    while (n < 65536u) {
        uint32_t tok = 0;
        if (!peek32(p + 4u * n, &tok)) return 0;
        if (tok == 0x0000FFFFu) return 4u * (n + 1u);
#ifndef REC_BREAK_WALK
        if ((tok & 0xFFFFu) == 0xFFFEu) {                 /* D3DSIO_COMMENT */
            n += 1u + ((tok >> 16) & 0x7FFFu);
            continue;
        }
        if (major >= 2u) {
            n += 1u + ((tok >> 24) & 0x0Fu);
            continue;
        }
        if ((tok & 0xFFFFu) == 0x0051u) {                 /* D3DSIO_DEF */
            n += 6u;
            continue;
        }
        n++;
        for (;;) {                                        /* the parameter tokens */
            uint32_t prm = 0;
            if (n >= 65536u || !peek32(p + 4u * n, &prm)) return 0;
            if (!(prm & 0x80000000u)) break;
            n++;
        }
        continue;
#else
        (void)major;
        n++;
#endif
    }
    return 0;
}

/* D3DVERTEXELEMENT9s to D3DDECL_END (Stream 0xFF) included; 0 when none in 65. */
static uint32_t decl_bytes(uint32_t p) {
    for (uint32_t n = 0; n < 65u; n++) {
        uint16_t stream = 0;
        if (!p || sa2_peek(p + 8u * n, 2, &stream) != SA2_OK) return 0;
        if (stream == 0x00FFu) return 8u * (n + 1u);
    }
    return 0;
}

static uint64_t prim_vertices(uint32_t type, uint32_t count) {
    switch (type) {
    case 1: return count;                          /* POINTLIST */
    case 2: return 2ull * count;                   /* LINELIST */
    case 3: return (uint64_t)count + 1u;           /* LINESTRIP */
    case 4: return 3ull * count;                   /* TRIANGLELIST */
    case 5: case 6: return (uint64_t)count + 2u;   /* TRIANGLESTRIP, TRIANGLEFAN */
    default: return 0;
    }
}

/* ------------------------------------------------------------ the shadow */

static void shadow_set(uint32_t addr, uint32_t type, uint32_t w, uint32_t h, uint32_t fmt) {
    rec_obj *o = obj_at(addr);
    if (!o) return;
    o->type = (uint8_t)(type + 1u);
    o->w = w;
    o->h = h;
    o->fmt = fmt;
}

static uint32_t out_word(rec_call *c, uint32_t arg) {
    uint32_t v = 0;
    if (arg < c->argc && c->a[arg]) peek32(c->a[arg], &v);
    return v;
}

/* ------------------------------------------------------------ the locks */

static rec_lock *lock_find(uint8_t kind, uint32_t object, uint32_t level) {
    rec_lock *best = NULL;
    for (uint32_t i = 0; i < REC_LOCKS; i++) {
        rec_lock *L = &g_locks[i];
        if (L->kind != kind || L->object != object) continue;
#ifndef REC_BREAK_ONELOCK
        if (kind == LK_TEX && L->level != level) continue;
#else
        (void)level;
#endif
        if (!best || L->order > best->order) best = L;
    }
    return best;
}

/* A slot for a new lock: a surface's or a texture level's open one again (the
 * guest locked it twice, which D3D9 refuses and the recording device does not), or
 * a free one; a buffer's locks stack. NULL, and a note, when all are taken. */
static rec_lock *lock_take(rec_call *c, uint8_t kind, uint32_t object, uint32_t level) {
    rec_lock *L = NULL;
#ifdef REC_BREAK_ONELOCK
    L = lock_find(kind, object, level);
#else
    if (kind != LK_BUF) L = lock_find(kind, object, level);
#endif
    if (L) {
        rec_note(c, N_LOCK, "a Lock on %#010x (level %u) while its last lock there is still "
                            "open: the last one is dropped", object, level);
    } else {
        for (uint32_t i = 0; i < REC_LOCKS && !L; i++)
            if (!g_locks[i].kind) L = &g_locks[i];
        if (!L) {
            rec_note(c, N_LOCK, "a Lock on %#010x with %u locks open already: its bytes will "
                                "not be recorded", object, REC_LOCKS);
            return NULL;
        }
    }
    memset(L, 0, sizeof *L);
    L->kind = kind;
    L->object = object;
    L->level = level;
    L->order = ++g_lock_order;
    return L;
}

/* The lock a LockRect answered: the level it covers and the bytes the Unlock copies. */
static void lock_open_rect(rec_call *c, uint8_t kind, uint32_t key_level, uint32_t out_arg,
                           uint32_t rect_arg, uint32_t flags, uint32_t container,
                           uint32_t level, uint32_t w, uint32_t h, uint32_t fmt, int sized) {
    rec_lock *K = lock_take(c, kind, c->self, key_level);
    if (!K) return;
    sa2s_lock_info *L = &K->info;
    uint32_t lr[2] = {0, 0};
    if (out_arg < c->argc && c->a[out_arg]) sa2_peek(c->a[out_arg], 8, lr);
    L->object = c->self;
    L->container = container;
    L->level = level;
    L->pitch = lr[0];
    L->bits = lr[1];
    L->format = fmt;
    L->width = w;
    L->height = h;
    L->flags = flags;
    L->rect[0] = 0;
    L->rect[1] = 0;
    L->rect[2] = (int32_t)w;
    L->rect[3] = (int32_t)h;
    uint32_t rows_px = h;
    if (rect_arg < c->argc && c->a[rect_arg]) {
        int32_t r[4];
        if (sa2_peek(c->a[rect_arg], 16, r) == SA2_OK) {
            L->has_rect = 1;
            memcpy(L->rect, r, sizeof r);
            rows_px = (r[3] > r[1]) ? (uint32_t)(r[3] - r[1]) : 0u;
            if (rows_px > h) rows_px = h;
        } else {
            sized = 0;
            rec_note(c, N_BLOB, "a LockRect on %#010x names a rectangle at %#010x that does "
                                "not read, so the lock's rows are unknown", c->self,
                     c->a[rect_arg]);
        }
    }
    L->rows = fmt_is_block(fmt) ? (rows_px + 3u) / 4u : rows_px;
    const uint64_t bytes = (uint64_t)L->rows * L->pitch;
    K->sized = (uint8_t)(sized && bytes <= REC_BLOB_CAP);
    L->bytes = K->sized ? (uint32_t)bytes : 0u;
    if (sized && !K->sized)
        rec_note(c, N_LOCK, "LockRect on %#010x: %llu bytes is over the cap", c->self,
                 (unsigned long long)bytes);
}

/* The Unlock's LOCK and LOCKED blobs; 1 when it copies bytes (c->lock says which). */
static int lock_close(rec_call *c, uint8_t kind, uint32_t level) {
    rec_lock *K = lock_find(kind, c->self, level);
    if (!K) {
        rec_note(c, N_LOCK, "an Unlock on %#010x (level %u) with no lock open there", c->self,
                 level);
        return 0;
    }
    c->lock = K->info;
    const int sized = K->sized;
    memset(K, 0, sizeof *K);
    if (!sized) {
        c->lock.bytes = 0;
        rec_note(c, N_LOCK, "an Unlock on %#010x whose lock the recorder could not size",
                 c->self);
    }
    blob_add(c, SA2S_BLOB_LOCK, SA2S_NO_ARG, 0, &c->lock, sizeof c->lock, REC_BLOB_CAP);
    if (!c->lock.bytes) return 0;
    uint32_t from = c->lock.bits;
#ifdef REC_BREAK_BUFBASE
    if (kind == LK_BUF) from -= c->lock.offset;             /* the store's base */
#endif
    blob_add(c, SA2S_BLOB_LOCKED, SA2S_NO_ARG, from, NULL, c->lock.bytes, REC_BLOB_CAP);
    return 1;
}

/* ------------------------------------------------------------ the buffer watch */

/* FNV-1a over `len` guest bytes at `addr`; 0 when they do not all read. */
static int guest_fnv(uint32_t addr, uint32_t len, uint64_t *out) {
    uint8_t chunk[4096];
    uint64_t h = 0xcbf29ce484222325ull;
    while (len) {
        const uint32_t n = len < sizeof chunk ? len : (uint32_t)sizeof chunk;
        if (sa2_peek(addr, n, chunk) != SA2_OK) return 0;
        for (uint32_t i = 0; i < n; i++) {
            h ^= chunk[i];
            h *= 0x100000001b3ull;
        }
        addr += n;
        len -= n;
    }
    *out = h;
    return 1;
}

/* A buffer's last open lock closed, copying `bytes` at `bits`: watch them. */
static void watch_set(rec_obj *o, uint32_t bits, uint32_t bytes) {
    if (!o || !g_watch || !bytes || !guest_fnv(bits, bytes, &o->w_hash)) return;
    o->w_on = 1;
    o->w_bits = bits;
    o->w_bytes = bytes;
    g_watch_set++;
    if (!o->w_listed) {
        o->w_listed = 1;
        g_watch[g_nwatch++] = (uint32_t)(o - g_objs);   /* at most one entry per object */
    }
}

/* A Lock (the guest may write now) or a final Release (the store goes back to the
 * heap): stop watching; the entry leaves the list at the next check. */
static void watch_drop(rec_obj *o) {
    if (o) o->w_on = 0;
}

static void watch_check(void) {
    g_watch_checks++;
    uint32_t keep = 0;
    for (uint32_t k = 0; k < g_nwatch; k++) {
        rec_obj *o = &g_objs[g_watch[k]];
        if (!o->w_on) {
            o->w_listed = 0;
            continue;
        }
        uint64_t h = 0;
        const int ok = guest_fnv(o->w_bits, o->w_bytes, &h);
        g_watch_hashed += o->w_bytes;
        if (!ok || h != o->w_hash) {
            g_watch_changed++;
            rec_note(NULL, N_WATCH, "buffer %#010x: its %u bytes at %#010x %s after its Unlock, "
                                    "seen at frame %llu",
                     sa2_d3d9_obj_addr(g_watch[k]), o->w_bytes, o->w_bits,
                     ok ? "changed" : "stopped reading", (unsigned long long)g_frames);
            o->w_hash = h;                          /* each change is counted once */
        }
        g_watch[keep++] = g_watch[k];
    }
    g_nwatch = keep;
}

/* ------------------------------------------------------------ the rules at work */

static const rec_act *rule_for(uint32_t idx, uint32_t t, uint32_t slot) {
    if (!g_rule_of[idx]) {
        const char *iface = sa2_d3d9_type_name(t), *method = sa2_d3d9_method_name(t, slot);
        uint16_t r = 1;
        if (iface && method)
            for (uint32_t i = 0; i < RULE_COUNT; i++)
                if ((!RULES[i].iface || !strcmp(RULES[i].iface, iface)) &&
                    !strcmp(RULES[i].method, method)) {
                    r = (uint16_t)(i + 2u);
                    break;
                }
        g_rule_of[idx] = r;
    }
    return g_rule_of[idx] >= 2u ? RULES[g_rule_of[idx] - 2u].act : NULL;
}

static int argc_for(uint32_t idx, uint32_t t, uint32_t slot) {
    if (!g_argc_of[idx]) g_argc_of[idx] = (int16_t)(sa2_d3d9_method_argc(t, slot) + 2);
    return g_argc_of[idx] - 2;
}

/* What has to be read before the call: whatever another guest thread could change
 * while this one waits inside it (Present), and what the call itself may rewrite
 * (the presentation parameters). Each is left out, with a note, when it does not
 * read whole. */
static void rec_pre(rec_call *c) {
    int rects = 0;
    for (int i = 0; i < REC_ACTS && c->act[i].op; i++) {
        const rec_act *x = &c->act[i];
        const uint32_t p = x->a < c->argc ? c->a[x->a] : 0;
        switch (x->op) {
        case A_PRE_PP:
            c->pp_ok = p && sa2_peek(p, sizeof c->pp, c->pp) == SA2_OK;
            if (p && !c->pp_ok)
                rec_note(c, N_BLOB, "the presentation parameters at %#010x do not read", p);
            break;
        case A_PRE_RECT:
            if (rects < 2) {
                c->rect_ok[rects] = p && sa2_peek(p, 16, c->rect[rects]) == SA2_OK;
                if (p && !c->rect_ok[rects])
                    rec_note(c, N_BLOB, "a rectangle at %#010x does not read", p);
                rects++;
            }
            break;
        case A_PRE_RGN: {
            uint32_t head[8];
            c->rgn_len = 0;
            if (!p) break;
            if (sa2_peek(p, sizeof head, head) != SA2_OK) {
                rec_note(c, N_BLOB, "the RGNDATA at %#010x does not read: left out", p);
                break;
            }
            const uint32_t n = head[2];                 /* RGNDATAHEADER.nCount */
            if (n > REC_RGN_RECTS) {
                rec_note(c, N_BLOB, "the RGNDATA at %#010x has %u rectangles, more than the "
                                    "%u kept: left out", p, n, REC_RGN_RECTS);
                break;
            }
            if (n && sa2_peek(p + 32u, 16u * n, c->rgn + 8) != SA2_OK) {
                rec_note(c, N_BLOB, "the %u rectangles of the RGNDATA at %#010x do not read: "
                                    "left out", n, p);
                break;
            }
            memcpy(c->rgn, head, sizeof head);
            c->rgn_len = 32u + 16u * n;
            break;
        }
        default:
            break;
        }
    }
}

static void rec_device(rec_call *c) {
    memset(&c->ddev, 0, sizeof c->ddev);
    const uint32_t dev = out_word(c, 5);
    c->ddev.device = dev;
    const int di = obj_index(dev);
    if (di >= 0) {
        shadow_set(dev, T_DEVICE, 0, 0, 0);
        for (uint32_t k = 1; k <= 2; k++) {
            /* the next two pool indices, which may lie in the next block */
            const uint32_t at = sa2_d3d9_obj_addr((uint32_t)di + k);
            uint32_t w[4];
            if (!at || sa2_peek(at, 16, w) != SA2_OK) continue;
            if (w[1] != REC_OBJ_TAG || w[2] != T_SURFACE || w[3] != (uint32_t)di + k) continue;
            if (k == 1) c->ddev.render_target = at;
            else c->ddev.depth_stencil = at;
        }
    }
    if (c->pp_ok && c->ddev.render_target)
        shadow_set(c->ddev.render_target, T_SURFACE, c->pp[0], c->pp[1], c->pp[2]);
    if (c->pp_ok && c->ddev.depth_stencil)
        shadow_set(c->ddev.depth_stencil, T_SURFACE, c->pp[0], c->pp[1], c->pp[10]);
    blob_add(c, SA2S_BLOB_DESC, SA2S_NO_ARG, 0, &c->ddev, sizeof c->ddev, REC_BLOB_CAP);
}

/* A d3dx load's description: the recording device's own row for the texture the
 * call wrote through argument `arg` (the newest row, whose object it must be). */
static void rec_file_desc(rec_call *c, uint32_t arg) {
    if (c->eax) return;
    const uint32_t tex = out_word(c, arg);
    const uint32_t rows = sa2_d3d9_texsrc_count();
    const sa2_d3d9_texsrc *row = rows ? sa2_d3d9_texsrc_at(rows - 1u) : NULL;
    if (!tex || !row || row->obj != tex) {
        rec_note(c, N_TEXROW, "the texture %#010x has no row of its own in the recording "
                              "device's record, so its load carries no description", tex);
        return;
    }
    sa2s_desc_file_texture *d = &c->dtex;
    memset(d, 0, sizeof *d);
    d->object = row->obj;
    d->container = row->container;
    d->kind = row->kind;
    d->width = row->w;
    d->height = row->h;
    d->depth = row->d;
    d->levels = row->levels;
    d->format = row->fmt;
    d->file_width = row->file_w;
    d->file_height = row->file_h;
    d->file_depth = row->file_depth;
    d->file_levels = row->file_levels;
    d->file_format = row->file_fmt;
    d->levels_from = row->levels_from;
    d->format_from = row->fmt_from;
    d->dims_from = row->dim_from;
    d->png_bit_depth = row->png_bit_depth;
    d->png_colour_type = row->png_colour_type;
    d->png_interlace = row->png_interlace;
    d->png_trns = row->png_trns;
    d->chain_bytes = row->chain_bytes;
    d->level0_bytes = row->level0_bytes;
    blob_add(c, SA2S_BLOB_DESC, SA2S_NO_ARG, 0, d, sizeof *d, REC_BLOB_CAP);
    shadow_set(tex, row->kind == SA2_TEXK_CUBE ? T_CUBETEX
                    : row->kind == SA2_TEXK_VOLUME ? T_VOLTEX : T_TEXTURE,
               row->w, row->h, row->fmt);
}

static void rec_post(rec_call *c) {
    int rects = 0;
    for (int i = 0; c->act && i < REC_ACTS && c->act[i].op; i++) {
        const rec_act *x = &c->act[i];
        switch (x->op) {
        case A_IN:
            blob_guest(c, SA2S_BLOB_IN, x->a, x->n);
            break;
        case A_OUT:
            blob_guest(c, SA2S_BLOB_OUT, x->a, x->n);
            break;
        case A_IN_VEC:
        case A_OUT_VEC:
            if (x->b < c->argc)
                blob_guest(c, x->op == A_IN_VEC ? SA2S_BLOB_IN : SA2S_BLOB_OUT, x->a,
                           (uint64_t)c->a[x->b] * x->n);
            break;
        case A_OUT_SIZED: {
            if (c->eax || x->a >= c->argc || x->b >= c->argc || !c->a[x->a] || !c->a[x->b])
                break;
            uint32_t k = 0;
            if (!peek32(c->a[x->b], &k)) {
                rec_note(c, N_BLOB, "%s slot %u: the size at %#010x does not read, so the "
                                    "bytes at %#010x are left out", call_name(c), c->slot,
                         c->a[x->b], c->a[x->a]);
                break;
            }
            blob_guest(c, SA2S_BLOB_OUT, x->a, (uint64_t)k * x->n);
            break;
        }
        case A_SHADER:
        case A_DECL: {
            const uint32_t p = x->a < c->argc ? c->a[x->a] : 0;
            if (!p) break;
            const uint32_t n = x->op == A_SHADER ? shader_bytes(p) : decl_bytes(p);
            if (!n)
                rec_note(c, N_WALK, "%s at %#010x: not one, or no end within the walk's bound",
                         x->op == A_SHADER ? "a shader's tokens" : "a declaration", p);
            blob_guest(c, SA2S_BLOB_IN, x->a, n);
            break;
        }
        case A_UP:
            if (c->argc >= 4)
                blob_guest(c, SA2S_BLOB_IN, 2, prim_vertices(c->a[0], c->a[1]) * c->a[3]);
            break;
        case A_IUP:
            if (c->argc >= 8) {
                blob_guest(c, SA2S_BLOB_IN, 4, prim_vertices(c->a[0], c->a[3]) *
                                               (c->a[5] == 101u ? 2u : 4u));
                blob_guest(c, SA2S_BLOB_IN, 6, ((uint64_t)c->a[1] + c->a[2]) * c->a[7]);
            }
            break;
        case A_PRE_PP:
            if (c->pp_ok)
                blob_add(c, SA2S_BLOB_IN, x->a, 0, c->pp, sizeof c->pp, REC_BLOB_CAP);
            break;
        case A_PRE_RECT:
            if (rects < 2) {
                if (c->rect_ok[rects])
                    blob_add(c, SA2S_BLOB_IN, x->a, 0, c->rect[rects], 16, REC_BLOB_CAP);
                rects++;
            }
            break;
        case A_PRE_RGN:
            if (c->rgn_len)
                blob_add(c, SA2S_BLOB_IN, x->a, 0, c->rgn, c->rgn_len, REC_BLOB_CAP);
            break;
        case A_FRAME:
            c->frame = 1;
            break;
        case A_DEVICE:
            rec_device(c);
            break;
        case A_FILE:
            if (x->a < c->argc && x->b < c->argc)
                blob_add(c, SA2S_BLOB_FILE, x->a, c->a[x->a], NULL, c->a[x->b], REC_FILE_CAP);
            break;
        case A_FILE_DESC:
            rec_file_desc(c, x->a);
            break;
        case A_NEW_TEX:
        case A_NEW_VOL:
        case A_NEW_CUBE:
        case A_NEW_SURF: {
            if (c->eax || x->a >= c->argc || x->b >= c->argc || x->c >= c->argc ||
                x->d >= c->argc)
                break;
            const uint32_t type = x->op == A_NEW_TEX ? T_TEXTURE : x->op == A_NEW_VOL ? T_VOLTEX
                                  : x->op == A_NEW_CUBE ? T_CUBETEX : T_SURFACE;
            shadow_set(out_word(c, x->a), type, c->a[x->b],
                       c->a[x->op == A_NEW_CUBE ? x->b : x->c], c->a[x->d]);
            break;
        }
        case A_NEW_BUF: {
            if (c->eax || x->b >= c->argc) break;
            rec_obj *o = obj_at(out_word(c, x->a));
            if (o) {
                o->type = (uint8_t)(T_VERTEXBUFFER + 1u);
                o->len = c->a[x->b];
            }
            break;
        }
        case A_LEVEL: {
            if (c->eax) break;
            rec_obj *t = obj_at(c->self), *s = obj_at(out_word(c, x->a));
            if (!s) break;
            const uint32_t lv = c->a[0];
            s->type = T_SURFACE + 1u;
            s->container = c->self;
            s->level = (uint8_t)lv;
            if (t && t->type) {
                s->w = level_dim(t->w, lv);
                s->h = level_dim(t->h, lv);
                s->fmt = t->fmt;
            } else {
                s->w = s->h = s->fmt = 0;
                rec_note(c, N_SHADOW, "GetSurfaceLevel on %#010x, a texture no creating call "
                                      "was seen for", c->self);
            }
            break;
        }
        case A_LOCK_SURF: {
            if (c->eax) break;
            rec_obj *s = obj_at(c->self);
            const int sized = s && s->type && s->w && s->h;
            if (!sized)
                rec_note(c, N_SHADOW, "LockRect on %#010x, a surface of no size the recorder "
                                      "saw made", c->self);
            lock_open_rect(c, LK_SURF, 0, 0, 1, c->a[2], s ? s->container : 0u,
                           s && s->container ? s->level : 0u, s ? s->w : 0u, s ? s->h : 0u,
                           s ? s->fmt : 0u, sized);
            break;
        }
        case A_UNLOCK_SURF:
            lock_close(c, LK_SURF, 0);
            break;
        case A_LOCK_TEX: {
            if (c->eax) break;
            rec_obj *t = obj_at(c->self);
            const uint32_t lv = c->a[0];
            const int sized = t && t->type && t->w && t->h;
            if (!sized)
                rec_note(c, N_SHADOW, "LockRect on %#010x, a texture of no size the recorder "
                                      "saw made", c->self);
            lock_open_rect(c, LK_TEX, lv, 1, 2, c->a[3], 0, lv,
                           t ? level_dim(t->w, lv) : 0u, t ? level_dim(t->h, lv) : 0u,
                           t ? t->fmt : 0u, sized);
            break;
        }
        case A_UNLOCK_TEX:
            lock_close(c, LK_TEX, c->a[0]);
            break;
        case A_LOCK_BUF: {
            watch_drop(obj_at(c->self));            /* the guest may write it from here */
            if (c->eax) break;
            rec_obj *b = obj_at(c->self);
            rec_lock *K = lock_take(c, LK_BUF, c->self, 0);
            if (!K) break;
            sa2s_lock_info *L = &K->info;
            L->object = c->self;
            L->bits = out_word(c, 2);
            L->offset = c->a[0];
            L->flags = c->a[3];
            L->rows = 1;
            const int known = c->a[1] || (b && b->type && b->len);
            const uint32_t size = c->a[1] ? c->a[1]
                                          : (b && b->len > c->a[0] ? b->len - c->a[0] : 0u);
            if (!known)
                rec_note(c, N_SHADOW, "Lock(%u, 0) on %#010x, a buffer of no length the "
                                      "recorder saw made", c->a[0], c->self);
            K->sized = (uint8_t)(known && size <= REC_BLOB_CAP);
            L->bytes = K->sized ? size : 0u;
            break;
        }
        case A_UNLOCK_BUF:
            /* Watched once no lock of the buffer is left open: until then the guest
             * may still write it. */
            if (lock_close(c, LK_BUF, 0) && !lock_find(LK_BUF, c->self, 0))
                watch_set(obj_at(c->self), c->lock.bits, c->lock.bytes);
            break;
        default:
            break;
        }
    }
}

/* ------------------------------------------------------------ the header */

typedef struct { char *p; size_t len, cap; int bad; } sbuf;

static void sb_add(sbuf *s, const char *t, size_t n) {
    if (s->bad) return;
    if (s->len + n + 1 > s->cap) {
        size_t cap = s->cap ? s->cap : 4096;
        while (s->len + n + 1 > cap) cap *= 2;
        char *q = (char *)realloc(s->p, cap);
        if (!q) { s->bad = 1; return; }
        s->p = q;
        s->cap = cap;
    }
    memcpy(s->p + s->len, t, n);
    s->len += n;
    s->p[s->len] = 0;
}
static void sb_str(sbuf *s, const char *t) { sb_add(s, t, strlen(t)); }
static void sb_fmt(sbuf *s, const char *fmt, ...) __attribute__((format(printf, 2, 3)));
static void sb_fmt(sbuf *s, const char *fmt, ...) {
    char tmp[512];
    va_list ap;
    va_start(ap, fmt);
    const int n = vsnprintf(tmp, sizeof tmp, fmt, ap);
    va_end(ap);
    if (n > 0) sb_add(s, tmp, (size_t)n < sizeof tmp ? (size_t)n : sizeof tmp - 1);
}
/* A JSON string: every byte below 0x20 escaped, `"` and `\` too. */
static void sb_json(sbuf *s, const char *t) {
    sb_str(s, "\"");
    for (; t && *t; t++) {
        const unsigned char ch = (unsigned char)*t;
        if (ch == '"' || ch == '\\') { char e[3] = {'\\', (char)ch, 0}; sb_str(s, e); }
        else if (ch < 0x20u) sb_fmt(s, "\\u%04x", ch);
        else sb_add(s, (const char *)&ch, 1);
    }
    sb_str(s, "\"");
}
/* An op list in host/sa2_stream.h's words: "out 2 1100; in 0 16". */
static void sb_ops(sbuf *s, const rec_act *act) {
    sb_str(s, "\"");
    int first = 1;
    for (int i = 0; i < REC_ACTS && act[i].op; i++) {
        const rec_act *x = &act[i];
        if (x->op >= A_COUNT) continue;
        sb_str(s, first ? "" : "; ");
        sb_str(s, OPS[x->op].name);
        for (const char *o = OPS[x->op].operands; *o; o++)
            sb_fmt(s, " %u", *o == 'a' ? x->a : *o == 'b' ? x->b : *o == 'c' ? x->c
                              : *o == 'd' ? x->d : x->n);
        first = 0;
    }
    sb_str(s, first ? "-\"" : "\"");
}
/* An audio op list in host/sa2_stream.h's words: "pcm 0 1; pcm 2 3; ds_state". */
static void sb_aops(sbuf *s, const au_act *act) {
    sb_str(s, "\"");
    int first = 1;
    for (int i = 0; i < AU_ACTS && act[i].op; i++) {
        const au_act *x = &act[i];
        if (x->op >= AA_COUNT) continue;
        sb_str(s, first ? "" : "; ");
        sb_str(s, AOPS[x->op].name);
        for (const char *o = AOPS[x->op].operands; *o; o++)
            sb_fmt(s, " %u", *o == 'a' ? x->a : *o == 'b' ? x->b : *o == 'c' ? x->c : x->n);
        first = 0;
    }
    sb_str(s, first ? "-\"" : "\"");
}

static int write_header(void) {
    sbuf s = {0};
    sb_str(&s, "{\"schema\": ");
    sb_json(&s, g_audio_on ? SA2S_SCHEMA_AUDIO : SA2S_SCHEMA);
    sb_str(&s, ", \"writer\": ");
    sb_json(&s, REC_WRITER);
    const char *meta = getenv("SA2_REC_META");
    sb_str(&s, ", \"meta\": ");
    if (meta && strlen(meta) <= 65536u) sb_json(&s, meta);
    else sb_str(&s, "null");
    sb_fmt(&s, ", \"object_base\": %u, \"object_stride\": %u, "
               "\"object_blocks\": [[%u, 0, %u], [%u, %u, %u]], \"interfaces\": [",
           SA2_D3D9_OBJ_BASE, SA2_D3D9_OBJ_STRIDE,
           SA2_D3D9_OBJ_BASE, SA2_D3D9_OBJ_FIRST_COUNT,
           SA2_D3D9_OBJ_OVF_BASE, SA2_D3D9_OBJ_FIRST_COUNT, SA2_D3D9_OBJ_OVF_COUNT);
    const uint32_t nt = sa2_d3d9_type_count();
    for (uint32_t t = 0; t < nt; t++) {
        if (t) sb_str(&s, ", ");
        sb_json(&s, sa2_d3d9_type_name(t));
    }
    sb_str(&s, "], \"methods\": [");
    int first = 1;
    for (uint32_t t = 0; t < nt && t < SA2_D3D9_MAX_TYPES; t++)
        for (uint32_t k = 0; k < SA2_D3D9_MAX_SLOTS; k++) {
            const char *name = sa2_d3d9_method_name(t, k);
            if (!name) continue;
            sb_fmt(&s, "%s[%u, %u, ", first ? "" : ", ", t, k);
            sb_json(&s, name);
            sb_fmt(&s, ", %d]", sa2_d3d9_method_argc(t, k));
            first = 0;
        }
    sb_str(&s, "], \"rules\": [");
    first = 1;
    for (uint32_t t = 0; t < nt && t < SA2_D3D9_MAX_TYPES; t++)
        for (uint32_t k = 0; k < SA2_D3D9_MAX_SLOTS; k++) {
            if (!sa2_d3d9_method_name(t, k)) continue;
            const rec_act *act = rule_for(t * SA2_D3D9_MAX_SLOTS + k, t, k);
            if (!act) continue;
            sb_fmt(&s, "%s[%u, %u, ", first ? "" : ", ", t, k);
            sb_ops(&s, act);
            sb_str(&s, "]");
            first = 0;
        }
    sb_str(&s, "], \"entries\": [");
    for (uint32_t e = 1; e < DX_COUNT; e++) {
        const rec_entry *d = &ENTRIES[e - 1u];
        sb_fmt(&s, "%s[%u, ", e > 1u ? ", " : "", d->entry);
        sb_json(&s, d->name);
        sb_fmt(&s, ", %u, ", d->argc);
        sb_ops(&s, d->act);
        sb_str(&s, "]");
    }
    sb_str(&s, "], \"note_classes\": [");
    for (uint32_t k = 0; k < note_classes(); k++) {
        if (k) sb_str(&s, ", ");
        sb_json(&s, NOTE_CLASSES[k]);
    }
    sb_str(&s, "]");
    if (g_audio_on) {
        sb_str(&s, ", \"audio\": {\"ops\": [");
        for (uint32_t k = 0; k < SA2S_AU_OP_COUNT - 1u; k++) {
            const au_rule *u = &AU_RULES[k];
            sb_fmt(&s, "%s[%u, ", k ? ", " : "", u->op);
            sb_json(&s, u->name);
            sb_str(&s, ", ");
            sb_json(&s, u->iface);
            sb_str(&s, ", ");
            sb_json(&s, u->method);
            sb_fmt(&s, ", %u, ", u->argc);
            sb_aops(&s, u->act);
            sb_str(&s, "]");
        }
        sb_fmt(&s, "], \"ds_state_bytes\": %u, \"xa_state_bytes\": %u}",
               (uint32_t)sizeof(sa2s_ds_state), (uint32_t)sizeof(sa2s_xa_state));
    }
    sb_str(&s, "}");
    if (s.bad) {
        free(s.p);
        rec_disable("the stream header could not be built: out of memory");
        return 0;
    }
    const uint32_t hb = 16u + pad4((uint32_t)s.len);
    if (!wr_reserve(hb)) { free(s.p); return 0; }
    uint8_t *p = g_buf + g_len;
    memcpy(p, SA2S_MAGIC, 8);
    const uint32_t v[2] = {g_audio_on ? SA2S_VERSION_AUDIO : SA2S_VERSION, hb};
    memcpy(p + 8, v, 8);
    memcpy(p + 16, s.p, s.len);
    memset(p + 16 + s.len, ' ', hb - 16u - (uint32_t)s.len);
    g_len += hb;
    free(s.p);
    return wr_flush();
}

/* ------------------------------------------------------------ start and end */

static void rec_exit(void);
static int au_tables_ok(char *why, size_t n);

static void rec_init(void) {
    if (g_init) return;
    g_init = 1;
    g_trace_path = getenv("SA2_REC_TRACE");
    g_counts_path = getenv("SA2_REC_COUNTS");
    const char *aw = getenv("SA2_REC_AUDIO_WATCH");
    g_au_watch = !(aw && !strcmp(aw, "0"));
    const char *sv = getenv("SA2_REC_STATE");
    g_state_on = sv && !strcmp(sv, "1");
    const char *pf = getenv("SA2_REC_PACE_FD");
    if (pf && *pf) {                                /* decimal digits only, 0 to 65535 */
        int n = 0;
        const char *p = pf;
        while (*p >= '0' && *p <= '9' && n <= 65535) n = n * 10 + (*p++ - '0');
        if (!*p && n <= 65535) g_pace_fd = n;
    }
    const char *he = getenv("SA2_REC_STORE_HASH_EVERY");
    if (he && *he) {                                /* decimal digits only, 1 to 65536 */
        uint32_t n = 0;
        const char *p = he;
        while (*p >= '0' && *p <= '9' && n <= 65536u) n = n * 10u + (uint32_t)(*p++ - '0');
        if (!*p && n >= 1u && n <= 65536u) g_au_hash_every = n;
    }
    if (g_trace_path && !*g_trace_path) g_trace_path = NULL;
    if (g_counts_path && !*g_counts_path) g_counts_path = NULL;
    const char *side = g_trace_path ? g_trace_path : g_counts_path;
    if (side) snprintf(g_status_path, sizeof g_status_path, "%s.status", side);
    if (!side) return;                              /* a pass-through */
    const char *brk = getenv("SA2_D3D9_BREAK");
    if (brk && *brk) {
        snprintf(g_why, sizeof g_why, "SA2_D3D9_BREAK=%s is set, and the recorder records "
                 "nothing under a break of the recording device's model", brk);
        status_write("off: %s", g_why);
        return;
    }
    g_count_on = 1;
    g_types_ok = sa2_d3d9_type_count() >= T_COUNT;
    for (uint32_t t = 0; g_types_ok && t < T_COUNT; t++) {
        const char *n = sa2_d3d9_type_name(t);
        g_types_ok = n && !strcmp(n, TYPE_NAMES[t]);
    }
    for (uint32_t t = 0; t < sa2_d3d9_type_count() && t < SA2_D3D9_MAX_TYPES; t++)
        for (uint32_t k = 0; k < SA2_D3D9_MAX_SLOTS; k++) {
            const char *f = sa2_d3d9_method_flags(t, k);
            g_out_flag[t * SA2_D3D9_MAX_SLOTS + k] = (uint8_t)(f && strstr(f, "out") != NULL);
        }
    /* Audio: "1" records it, unset or "0" does not, anything else stops the stream
     * (below) rather than guess; so does an audio table that is not the device's, and
     * a break of either audio device's model (SA2_D3D9_BREAK's rule, above: the stream
     * would describe a device nobody meant to present). The table is held first, so a
     * break that moves an argument count says so by name. */
    char au_why[300] = "";
    const char *au = getenv("SA2_REC_AUDIO");
    if (au && *au && strcmp(au, "0")) {
        const char *dbrk = getenv("SA2_DSOUND_BREAK"), *xbrk = getenv("SA2_XAUDIO2_BREAK");
        if (strcmp(au, "1")) {
            snprintf(au_why, sizeof au_why, "SA2_REC_AUDIO=%.64s: 1 records audio, 0 or unset "
                     "does not", au);
        } else if (au_tables_ok(au_why, sizeof au_why)) {
            if ((dbrk && *dbrk) || (xbrk && *xbrk))
                snprintf(au_why, sizeof au_why, "SA2_%s_BREAK=%.32s is set, and the recorder "
                         "records no audio under a break of an audio device's model",
                         dbrk && *dbrk ? "DSOUND" : "XAUDIO2", dbrk && *dbrk ? dbrk : xbrk);
            else
                g_audio_on = 1;
        }
    }
    if (atexit(rec_exit)) {
        snprintf(g_why, sizeof g_why, "no exit handler could be registered");
        g_count_on = 0;
        status_write("off: %s", g_why);
        return;
    }
    if (au_why[0] && !g_trace_path) {
        /* Counting only: as with a trace, the counting goes on without audio and the
         * counts file says why. */
        snprintf(g_why, sizeof g_why, "%s", au_why);
        status_write("counting, without audio: %s", g_why);
    }
    if (g_trace_path) {
        g_stream_on = 1;
        if (!g_types_ok) {
            rec_disable("the recording device's interface table is not the one this "
                        "recorder was written for (runtime/sa2_d3d9.c, g_types)");
        } else if (au_why[0]) {
            rec_disable("%s", au_why);
        } else if (!(g_objs = (rec_obj *)calloc(SA2_D3D9_POOL_OBJECTS, sizeof *g_objs)) ||
                   !(g_watch = (uint32_t *)calloc(SA2_D3D9_POOL_OBJECTS, sizeof *g_watch)) ||
                   !(g_buf = (uint8_t *)malloc(REC_BUF_BYTES))) {
            rec_disable("the recorder's memory could not be allocated");
        } else {
            g_cap = REC_BUF_BYTES;
            g_fd = open(g_trace_path, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
            if (g_fd < 0) rec_disable("%s does not open: %s", g_trace_path, strerror(errno));
            else write_header();
        }
    }
    if (g_count_on && !g_why[0]) status_write("recording, pid %ld", (long)getpid());
}

static void write_counts(void) {
    if (!g_counts_path) return;
    FILE *f = fopen(g_counts_path, "w");
    if (!f) return;
    uint64_t calls = 0, entered = 0, presents = 0, dx = 0, dx_entered = 0;
    uint32_t distinct = 0;
    for (uint32_t i = 0; i < REC_THUNKS; i++) {
        calls += g_returned[i];
        entered += g_entered[i];
        if (g_entered[i]) distinct++;
    }
    const uint32_t dev_present = T_DEVICE * SA2_D3D9_MAX_SLOTS + 17u;
    const uint32_t swap_present = T_SWAPCHAIN * SA2_D3D9_MAX_SLOTS + 3u;
    presents = g_returned[dev_present] + g_returned[swap_present];
    /* d3dx9 only: Direct3DCreate9 is not one of the recording device's calls
     * (sa2_d3d9_make_d3d9 counts none), so it is not in method_and_d3dx_calls. */
    for (uint32_t k = 1; k < SA2S_D3DX_DIRECT3D_CREATE9; k++) {
        dx += g_dx_returned[k];
        dx_entered += g_dx_entered[k];
    }
    fprintf(f, "{\n \"schema\": \"sa2-present-rec-counts/v1\",\n \"writer\": \"%s\",\n", REC_WRITER);
    fprintf(f, " \"counting\": %s,\n \"stream\": %s,\n", g_count_on ? "true" : "false",
            g_trace_path ? (g_stream_on ? "\"closed\"" : "\"disabled\"") : "null");
    fprintf(f, " \"why\": ");
    {
        sbuf s = {0};
        sb_json(&s, g_why);
        fputs(s.p ? s.p : "\"\"", f);
        free(s.p);
    }
    fprintf(f, ",\n \"thunks_wrapped\": %u,\n \"registrations_passed\": %u,\n",
            g_thunks_wrapped, g_regs_passed);
    fprintf(f, " \"method_calls\": %llu,\n \"method_calls_entered\": %llu,\n"
               " \"distinct_methods\": %u,\n \"presents\": %llu,\n"
               " \"d3dx_calls\": %llu,\n \"d3dx_calls_entered\": %llu,\n"
               " \"direct3d_create9\": %llu,\n \"method_and_d3dx_calls\": %llu,\n",
            (unsigned long long)calls, (unsigned long long)entered, distinct,
            (unsigned long long)presents, (unsigned long long)dx,
            (unsigned long long)dx_entered,
            (unsigned long long)g_dx_returned[SA2S_D3DX_DIRECT3D_CREATE9],
            (unsigned long long)(calls + dx));
    fprintf(f, " \"entries\": [");
    for (uint32_t k = 1; k < DX_COUNT; k++)
        fprintf(f, "%s\n  {\"entry\": %u, \"name\": \"%s\", \"calls\": %llu, \"entered\": %llu}",
                k > 1 ? "," : "", k, ENTRIES[k - 1u].name, (unsigned long long)g_dx_returned[k],
                (unsigned long long)g_dx_entered[k]);
    fprintf(f, "\n ],\n \"methods\": [");
    int first = 1;
    for (uint32_t i = 0; i < REC_THUNKS; i++) {
        if (!g_entered[i]) continue;
        const uint32_t t = i / SA2_D3D9_MAX_SLOTS, k = i % SA2_D3D9_MAX_SLOTS;
        sbuf s = {0};
        sb_json(&s, sa2_d3d9_type_name(t));
        sb_str(&s, ", \"method\": ");
        sb_json(&s, sa2_d3d9_method_name(t, k));
        fprintf(f, "%s\n  {\"type\": %u, \"slot\": %u, \"interface\": %s, \"argc\": %d, "
                   "\"calls\": %llu, \"entered\": %llu}",
                first ? "" : ",", t, k, s.p ? s.p : "\"\"", sa2_d3d9_method_argc(t, k),
                (unsigned long long)g_returned[i], (unsigned long long)g_entered[i]);
        free(s.p);
        first = 0;
    }
    fprintf(f, "\n ],\n \"notes\": {");
    for (uint32_t k = 0; k < note_classes(); k++)
        fprintf(f, "%s\"%s\": %llu", k ? ", " : "", NOTE_CLASSES[k],
                (unsigned long long)g_note_n[k]);
    fprintf(f, "},\n \"stream_records\": {\"call\": %llu, \"d3dx\": %llu, \"frame\": %llu, "
               "\"note\": %llu, \"end\": %llu",
            (unsigned long long)g_kind_n[SA2S_CALL], (unsigned long long)g_kind_n[SA2S_D3DX],
            (unsigned long long)g_kind_n[SA2S_FRAME], (unsigned long long)g_kind_n[SA2S_NOTE],
            (unsigned long long)g_kind_n[SA2S_END]);
    if (g_audio_on) fprintf(f, ", \"audio\": %llu", (unsigned long long)g_kind_n[SA2S_AUDIO]);
    if (g_state_on) fprintf(f, ", \"state\": %llu", (unsigned long long)g_state_n);
    fprintf(f, "},\n");
    fprintf(f, " \"stream_blobs\": {");
    const uint32_t last_role = g_audio_on ? SA2S_BLOB_HASH : SA2S_BLOB_LOCK;
    for (uint32_t r = 1; r <= last_role; r++)
        fprintf(f, "%s\"%u\": [%llu, %llu]", r > 1 ? ", " : "", r,
                (unsigned long long)g_role_n[r], (unsigned long long)g_role_bytes[r]);
    uint32_t watched = 0;
    for (uint32_t k = 0; k < g_nwatch; k++) watched += g_objs[g_watch[k]].w_on;
    fprintf(f, "},\n \"buffer_watch\": {\"every_frames\": %u, \"set\": %llu, \"checks\": %llu, "
               "\"watched_at_exit\": %u, \"bytes_hashed\": %llu, \"changed\": %llu},\n",
            REC_WATCH_EVERY, (unsigned long long)g_watch_set, (unsigned long long)g_watch_checks,
            watched, (unsigned long long)g_watch_hashed, (unsigned long long)g_watch_changed);
    if (g_audio_on) {
        /* Per op: calls entered and returned, records written, and calls that answered
         * a failure (nothing to record). The store watch: Unlocks that set it, checks
         * and the bytes they hashed, stores watched at exit, stores that changed. Under
         * SA2_REC_STORE_HASH_EVERY=n (n > 1) only, the store_hash: the Unlocks that closed
         * a store's last Lock, and those that carry none because of it. */
        fprintf(f, " \"audio\": {\"thunks_wrapped\": %u, \"pcm_bytes\": %llu,\n"
                   "  \"store_watch\": {\"every_frames\": %u, \"set\": %llu, \"checks\": %llu, "
                   "\"bytes_hashed\": %llu, \"watched_at_exit\": %u, \"changed\": %llu},\n",
                g_au_wrapped, (unsigned long long)g_au_pcm_bytes, REC_WATCH_EVERY,
                (unsigned long long)g_au_sw_set, (unsigned long long)g_au_sw_checks,
                (unsigned long long)g_au_sw_hashed, g_au_sw_on, (unsigned long long)g_au_sw_changed);
        if (g_au_hash_every > 1u)
            fprintf(f, "  \"store_hash\": {\"every\": %u, \"closes\": %llu, \"not_hashed\": %llu},\n",
                    g_au_hash_every, (unsigned long long)g_au_closes,
                    (unsigned long long)g_au_hash_skipped);
        fprintf(f, "  \"ops\": [");
        for (uint32_t k = 1; k < SA2S_AU_OP_COUNT; k++)
            fprintf(f, "%s\n  {\"op\": %u, \"name\": \"%s\", \"entered\": %llu, \"returned\": %llu, "
                       "\"recorded\": %llu, \"failed\": %llu}",
                    k > 1 ? "," : "", k, AU_RULES[k - 1u].name, (unsigned long long)g_au_entered[k],
                    (unsigned long long)g_au_returned[k], (unsigned long long)g_au_recorded[k],
                    (unsigned long long)g_au_failed[k]);
        fprintf(f, "\n ]},\n");
    }
    fprintf(f, " \"stream_bytes\": %llu,\n \"flushes\": %llu\n}\n",
            (unsigned long long)g_file_bytes, (unsigned long long)g_flushes);
    fclose(f);
}

/* At exit: the guest is unmapped by now (harness/crt_probe.c, sa2_runtime_shutdown),
 * so nothing here reads it. */
static void rec_exit(void) {
    if (g_stream_on) {
        if (wr_reserve(12u)) {
            uint8_t *p = g_buf + g_len;
            uint32_t off = 0;
            put32(p, &off, SA2S_END);
            put32(p, &off, 0);
            put32(p, &off, 0);
            wr_commit(SA2S_END, off);
        }
        if (g_stream_on && wr_flush()) {
            close(g_fd);
            g_fd = -1;
        }
    }
    write_counts();
    if (g_trace_path && g_stream_on)
        status_write("closed: %u records, %llu bytes, %llu frames", g_seq,
                     (unsigned long long)g_file_bytes, (unsigned long long)g_frames);
    else if (g_count_on && !g_trace_path && g_why[0])
        status_write("closed: counting only, without audio: %s", g_why);
    else if (g_count_on && !g_trace_path)
        status_write("closed: counting only");
}

/* ------------------------------------------------------------ the thunk */

static void *rec_thunk(void *s, uint32_t pc, void *m) {
    const uint32_t idx = (pc - SA2_D3D9_THUNK_BASE) / SA2_D3D9_THUNK_STRIDE;
    sa2_lifted_fn real = (idx < REC_THUNKS && g_real[idx]) ? g_real[idx] : g_real_any;
    if (!g_count_on || idx >= REC_THUNKS) return real(s, pc, m);
    g_entered[idx]++;
    if (!g_stream_on) {
        void *r = real(s, pc, m);
        g_returned[idx]++;
        return r;
    }
    const uint32_t t = idx / SA2_D3D9_MAX_SLOTS, slot = idx % SA2_D3D9_MAX_SLOTS;
    const int argc = argc_for(idx, t, slot);
#ifdef REC_BREAK_GLOBAL
    rec_call *c = &g_break_call;
#else
    rec_call call;
    rec_call *c = &call;
#endif
    call_reset(c);
    c->idx = idx;
    c->t = t;
    c->slot = slot;
    uint32_t raw[REC_MAX_ARGS + 1];
    const uint32_t esp = sa2_rt_get_gpr(s, SA2_ESP);
    int ok = argc >= 0 && (uint32_t)argc <= REC_MAX_ARGS &&
             sa2_peek(esp + 4u, 4u * ((uint32_t)argc + 1u), raw) == SA2_OK;
    if (ok) {
        c->argc = (uint32_t)argc;
        c->self = raw[0];
        memcpy(c->a, raw + 1, 4u * c->argc);
        c->thread = (uint32_t)sa2_sched_current();
        c->act = rule_for(idx, t, slot);
        if (c->act) rec_pre(c);
    }
    /* An unnamed slot, or arguments outside guest memory: the recording device
     * refuses both by name and does not come back, so there is nothing to record. */
    void *r = real(s, pc, m);
    g_returned[idx]++;
    if (!ok) {
        if (argc > (int)REC_MAX_ARGS)
            rec_note(NULL, N_ARGS, "%u.%u returned with %d arguments, more than the recorder "
                                   "keeps", t, slot, argc);
        return r;
    }
    c->eax = sa2_rt_get_gpr(s, SA2_EAX);
    if (!c->act && g_out_flag[idx] && !g_norule_noted[idx]) {
        g_norule_noted[idx] = 1;
        rec_note(c, N_NORULE, "%s::%s writes through a pointer and has no rule here, so its "
                              "out-parameters are not in the stream", TYPE_NAMES[t < T_COUNT ? t : 0],
                 sa2_d3d9_method_name(t, slot));
    }
    rec_post(c);
    /* A buffer's final Release gives its store back to the guest heap. */
    if (slot == 2u && (t == T_VERTEXBUFFER || t == T_INDEXBUFFER) && c->eax == 0u)
        watch_drop(obj_at(c->self));
    emit_call(c, 0);
#ifdef REC_BREAK_POKE
    {
        const uint8_t one = 1;
        sa2_poke(c->self + 7u, 1, &one);         /* the 'D3D9' tag's last byte */
    }
#endif
#ifdef REC_BREAK_REGSTORE
    ((volatile uint32_t *)s)[SA2_EDI] ^= 0x10u;
#endif
    return r;
}

/* ------------------------------------------------------------ audio */

/* One audio call being recorded, on the host stack of the thread making it. Every
 * field before `xbuf` is cleared per call; the host-made blobs after it are written
 * before they are read. */
typedef struct {
    const au_rule *u;
    uint32_t argc, self, thread, eax;
    uint64_t us;
    uint32_t a[REC_ARGS];
    int nb, nnotes;
    int ds_ok;                       /* ds holds the buffer's record after the call */
    rec_blob b[REC_MAX_BLOBS];
    uint32_t xbuf[9];                /* the XAUDIO2_BUFFER a submit names */
    uint64_t store_hash;             /* an Unlock's store_hash */
    sa2s_ds_state ds;
    sa2s_xa_state xa;
#ifdef REC_BREAK_AUSTATE
    int pre;                         /* ds or xa holds the record from before the call */
#endif
    char notes[REC_CALL_NOTES][REC_NOTE_BYTES];
} au_call;
#define au_reset(c) memset((c), 0, offsetof(au_call, xbuf))

_Static_assert(sizeof(sa2s_ds_state) == 84u, "sa2s_ds_state is 84 bytes (host/sa2_stream.h)");
_Static_assert(sizeof(sa2s_xa_state) == 176u, "sa2s_xa_state is 176 bytes (host/sa2_stream.h)");

/* A note of class "audio", as rec_note writes one: about `c`, after its record. */
static void au_note(au_call *c, const char *fmt, ...) __attribute__((format(printf, 2, 3)));
static void au_note(au_call *c, const char *fmt, ...) {
    const uint64_t k = g_note_n[N_AUDIO]++;
    if (!g_stream_on || k > REC_NOTES_PER_CLASS) return;
    char text[REC_NOTE_BYTES];
    int n = snprintf(text, sizeof text, "%s: ", NOTE_CLASSES[N_AUDIO]);
    if (n < 0) n = 0;
    if (k == REC_NOTES_PER_CLASS) {
        snprintf(text + n, sizeof text - (size_t)n,
                 "further notes of this class are counted, not written");
    } else {
        va_list ap;
        va_start(ap, fmt);
        vsnprintf(text + n, sizeof text - (size_t)n, fmt, ap);
        va_end(ap);
    }
    if (!c) emit_note_text(text);
    else if (c->nnotes < REC_CALL_NOTES) memcpy(c->notes[c->nnotes++], text, sizeof text);
}

static const char *au_type_name(uint32_t api, uint32_t t) {
    return api == SA2S_AU_DSOUND ? sa2_dsound_type_name(t) : sa2_xaudio2_type_name(t);
}
static const char *au_method_name(uint32_t api, uint32_t t, uint32_t slot) {
    return api == SA2S_AU_DSOUND ? sa2_dsound_method_name(t, slot)
                                 : sa2_xaudio2_method_name(t, slot);
}

/* The op recorded for the device's (type, slot), by its interface and method names;
 * 0 for a method with no `audio` line. */
static uint32_t au_op_at(uint32_t api, uint32_t t, uint32_t slot) {
    const char *iface = au_type_name(api, t), *method = au_method_name(api, t, slot);
    if (!iface || !method) return 0;
    for (uint32_t k = 0; k < SA2S_AU_OP_COUNT - 1u; k++)
        if (AU_RULES[k].api == api && !strcmp(AU_RULES[k].iface, iface) &&
            !strcmp(AU_RULES[k].method, method))
            return AU_RULES[k].op;
    return 0;
}

/* Every `audio` line names a method the device has, with the device's own argument
 * count: a table that moved stops the stream rather than read the wrong dwords. */
static int au_tables_ok(char *why, size_t n) {
    for (uint32_t k = 0; k < SA2S_AU_OP_COUNT - 1u; k++) {
        const au_rule *u = &AU_RULES[k];
        if (u->op != k + 1u) {
            snprintf(why, n, "the recorder's audio row %u is op %u", k, u->op);
            return 0;
        }
        const int ds = u->api == SA2S_AU_DSOUND;
        uint32_t nt = ds ? sa2_dsound_type_count() : sa2_xaudio2_type_count();
        const uint32_t ns = ds ? SA2_DS_MAX_SLOTS : SA2_XA2_MAX_SLOTS;
        if (nt > (ds ? SA2_DS_MAX_TYPES : SA2_XA2_MAX_TYPES)) nt = ds ? SA2_DS_MAX_TYPES : SA2_XA2_MAX_TYPES;
        int found = 0;
        for (uint32_t t = 0; t < nt && !found; t++) {
            const char *iface = au_type_name(u->api, t);
            if (!iface || strcmp(iface, u->iface)) continue;
            for (uint32_t s = 0; s < ns && !found; s++) {
                const char *method = au_method_name(u->api, t, s);
                if (!method || strcmp(method, u->method)) continue;
                const int argc = ds ? sa2_dsound_method_argc(t, s) : sa2_xaudio2_method_argc(t, s);
                if (argc != (int)u->argc) {
                    snprintf(why, n, "the audio device gives %s::%s %d arguments, and this recorder "
                                     "was written for %u", u->iface, u->method, argc, u->argc);
                    return 0;
                }
                found = 1;
            }
        }
        if (!found) {
            snprintf(why, n, "the audio device has no %s::%s, which the recorder's op %s "
                             "records", u->iface, u->method, u->name);
            return 0;
        }
    }
    return 1;
}

/* The device's own record of the buffer or voice at `addr` after the call, copied
 * field by field; 0 when it has none. Reads the device's tables, never writes them.
 * Every field is one load and one store through a volatile pointer: left to itself
 * the compiler merges neighbouring fields into SSE shuffles (MOVLHPS, PUNPCKLQDQ) that
 * tools/s5/objcheck.py's SIMD_ALLOWED does not list, and the copy needs none. */
static int ds_state_of(uint32_t addr, sa2s_ds_state *out) {
    for (uint32_t i = sa2_dsound_buffer_count(); i-- > 0;) {
        const volatile sa2_ds_buffer *b = sa2_dsound_buffer_at(i);
        if (!b || b->addr != addr) continue;
        volatile sa2s_ds_state *o = out;
        const uint64_t anchor_us = b->anchor_us;
        o->buffer = b->addr;
        o->index = i;
        o->flags = b->flags;
        o->bytes = b->bytes;
        o->primary = b->primary;
        o->format_tag = b->format_tag;
        o->channels = b->channels;
        o->samples_per_sec = b->samples_per_sec;
        o->avg_bytes_per_sec = b->avg_bytes_per_sec;
        o->block_align = b->block_align;
        o->bits_per_sample = b->bits_per_sample;
        o->playing = b->playing;
        o->looping = b->looping;
        o->halted = b->halted;
        o->released = b->released;
        o->anchor_pos = b->anchor_pos;
        o->anchor_us_lo = (uint32_t)anchor_us;
        o->anchor_us_hi = (uint32_t)(anchor_us >> 32);
        o->mem = b->mem;
        o->mem_len = b->mem_len;
        o->write_lead = b->write_lead;
        return 1;
    }
    return 0;
}
static int xa_state_of(uint32_t addr, sa2s_xa_state *out) {
    for (uint32_t i = sa2_xaudio2_voice_count(); i-- > 0;) {
        const volatile sa2_xa2_voice *v = sa2_xaudio2_voice_at(i);
        if (!v || v->addr != addr) continue;
        volatile sa2s_xa_state *o = out;
        const uint64_t anchor_us = v->anchor_us, anchor_frame = v->anchor_frame;
        const uint64_t played = v->played, queued_end = v->queued_end;
        o->voice = v->addr;
        o->index = i;
        o->kind = v->kind;
        o->engine = v->engine;
        o->flags = v->flags;
        o->destroyed = v->destroyed;
        o->details_flags = v->details_flags;
        o->input_channels = v->input_channels;
        o->input_sample_rate = v->input_sample_rate;
        o->format_tag = v->format_tag;
        o->channels = v->channels;
        o->samples_per_sec = v->samples_per_sec;
        o->avg_bytes_per_sec = v->avg_bytes_per_sec;
        o->block_align = v->block_align;
        o->bits_per_sample = v->bits_per_sample;
        o->valid_bits = v->valid_bits;
        o->channel_mask = v->channel_mask;
        o->max_ratio_bits = v->max_ratio_bits;
        o->ratio_bits = v->ratio_bits;
        o->output = v->output;
        o->matrix_src = v->matrix_src;
        o->matrix_dst = v->matrix_dst;
        for (uint32_t k = 0; k < 12u; k++) o->matrix[k] = v->matrix[k];
        o->running = v->running;
        o->queued = v->queued;
        o->anchor_us_lo = (uint32_t)anchor_us;
        o->anchor_us_hi = (uint32_t)(anchor_us >> 32);
        o->anchor_frame_lo = (uint32_t)anchor_frame;
        o->anchor_frame_hi = (uint32_t)(anchor_frame >> 32);
        o->played_lo = (uint32_t)played;
        o->played_hi = (uint32_t)(played >> 32);
        o->queued_end_lo = (uint32_t)queued_end;
        o->queued_end_hi = (uint32_t)(queued_end >> 32);
        return 1;
    }
    return 0;
}

static void au_blob(au_call *c, uint16_t role, uint16_t arg, uint32_t gaddr, const void *host,
                    uint64_t len) {
    if (!len) return;
    if (!host && !gaddr) return;                   /* a null pointer carries nothing */
    if (len > REC_BLOB_CAP) {
        au_note(c, "%s: %llu bytes of arg %u is over the %u-byte cap", c->u->name,
                (unsigned long long)len, arg, REC_BLOB_CAP);
        return;
    }
    if (!host && !in_guest(gaddr, len)) {
        au_note(c, "%s: %llu bytes at %#010x (arg %u) are outside guest memory", c->u->name,
                (unsigned long long)len, gaddr, arg);
        return;
    }
    if (c->nb >= REC_MAX_BLOBS) {
        au_note(c, "%s: more than %d blobs on one call", c->u->name, REC_MAX_BLOBS);
        return;
    }
    rec_blob *b = &c->b[c->nb++];
    b->role = role;
    b->arg = arg;
    b->len = (uint32_t)len;
    b->gaddr = gaddr;
    b->host = host;
}

/* ------------------------------------------------------------ the store watch */

/* The row of the store at `mem`, made when there is none and there is room. */
static au_store *au_store_at(uint32_t mem) {
    for (uint32_t k = 0; k < g_au_nstores; k++)
        if (g_au_store[k].mem == mem) return &g_au_store[k];
    if (g_au_nstores >= AU_STORES) return NULL;
    au_store *s = &g_au_store[g_au_nstores++];
    memset(s, 0, sizeof *s);
    s->mem = mem;
    return s;
}

/* One check of a watched store: hashed again; a change counted once and noted, after
 * the record of the call `c` that saw it, or at once from a FRAME (c NULL). */
static void au_store_verify(au_store *s, au_call *c) {
    uint64_t h = 0;
    const int ok = guest_fnv(s->mem, s->len, &h);
    g_au_sw_checks++;
    g_au_sw_hashed += s->len;
    if (ok && h == s->hash) return;
    g_au_sw_changed++;
    if (c)
        au_note(c, "the store at %#010x (%u bytes) %s after its last Unlock, seen at %s on %#010x",
                s->mem, s->len, ok ? "changed" : "stopped reading", c->u->name, c->self);
    else
        au_note(NULL, "the store at %#010x (%u bytes) %s after its last Unlock, seen at frame %llu",
                s->mem, s->len, ok ? "changed" : "stopped reading", (unsigned long long)g_frames);
    s->hash = h;
}

/* Every REC_WATCH_EVERY-th FRAME: every watched store. */
static void au_store_check(void) {
    for (uint32_t k = 0; k < g_au_nstores; k++)
        if (g_au_store[k].on) au_store_verify(&g_au_store[k], NULL);
}

/* A Lock opened on the store its STATE names: a watched store is checked once more,
 * then not watched (the guest may write it now). */
static void au_store_lock(au_call *c) {
    if (!c->ds_ok || !c->ds.mem) return;
    au_store *s = au_store_at(c->ds.mem);
    if (!s) {
        au_note(c, "%s on %#010x: more than %u stores, so the one at %#010x is not watched",
                c->u->name, c->self, AU_STORES, c->ds.mem);
        return;
    }
    s->len = c->ds.mem_len;
    if (s->on) {
#ifndef REC_BREAK_AUNOWATCH
        if (g_au_watch) au_store_verify(s, c);
#endif
        s->on = 0;
        g_au_sw_on--;
    }
    s->open++;
}

/* An Unlock closed a Lock on the store its STATE names (none below 0). When it left
 * none open, the store's FNV-1a goes in the record (store_hash) and is watched; under
 * SA2_REC_STORE_HASH_EVERY=n only at the first such Unlock and every n-th after it, over
 * every store, and the others hash nothing and leave the watch as it was. */
static void au_store_unlock(au_call *c) {
    if (!c->ds_ok || !c->ds.mem) return;
    au_store *s = au_store_at(c->ds.mem);
    if (!s) {
        au_note(c, "%s on %#010x: more than %u stores, so the one at %#010x is not hashed",
                c->u->name, c->self, AU_STORES, c->ds.mem);
        return;
    }
    s->len = c->ds.mem_len;
    if (s->open) s->open--;
    if (s->open) return;                       /* the guest may still write through a Lock */
    if (g_au_closes++ % g_au_hash_every) {
        g_au_hash_skipped++;
        return;
    }
    uint64_t h = 0;
    if (!guest_fnv(s->mem, s->len, &h)) {
        au_note(c, "%s on %#010x: the %u bytes of its store at %#010x do not read, so the call "
                   "carries no store_hash", c->u->name, c->self, s->len, s->mem);
        return;
    }
    c->store_hash = h;
    const int nb = c->nb;
    au_blob(c, SA2S_BLOB_HASH, SA2S_NO_ARG, 0, &c->store_hash, sizeof c->store_hash);
    if (c->nb == nb) return;                   /* not carried (noted): a reader sets no watch */
    s->hash = h;
    if (!s->on) g_au_sw_on++;
    s->on = 1;
    g_au_sw_set++;
}

/* The blobs the call's `audio` line gives it, after the call. */
static void au_post(au_call *c) {
    for (int i = 0; i < AU_ACTS && c->u->act[i].op; i++) {
        const au_act *x = &c->u->act[i];
        switch (x->op) {
        case AA_OUT:
            if (x->a < c->argc) au_blob(c, SA2S_BLOB_OUT, x->a, c->a[x->a], NULL, x->n);
            break;
        case AA_IN_MAT:
            if (x->a >= c->argc || x->b >= c->argc || x->c >= c->argc) break;
            if (c->a[x->b] > AU_MATRIX_MAX || c->a[x->c] > AU_MATRIX_MAX) {
                au_note(c, "%s: a %u x %u matrix is past the %u x %u the recorder copies", c->u->name,
                        c->a[x->b], c->a[x->c], AU_MATRIX_MAX, AU_MATRIX_MAX);
                break;
            }
            au_blob(c, SA2S_BLOB_IN, x->a, c->a[x->a], NULL, (uint64_t)c->a[x->b] * c->a[x->c] * 4u);
            break;
        case AA_PCM:
            if (x->a < c->argc && x->b < c->argc)
#ifndef REC_BREAK_AUPCM
                au_blob(c, SA2S_BLOB_PCM, x->a, c->a[x->a], NULL, c->a[x->b]);
#else
                au_blob(c, SA2S_BLOB_PCM, x->a, c->a[x->a] ? c->a[x->a] + 4u : 0u, NULL, c->a[x->b]);
#endif
            break;
        case AA_SUBMIT:
            if (x->a >= c->argc || !c->a[x->a]) break;
            if (sa2_peek(c->a[x->a], sizeof c->xbuf, c->xbuf) != SA2_OK) {
                au_note(c, "%s: the XAUDIO2_BUFFER at %#010x does not read, so neither it nor "
                           "its audio is in the stream", c->u->name, c->a[x->a]);
                break;
            }
            au_blob(c, SA2S_BLOB_IN, x->a, 0, c->xbuf, sizeof c->xbuf);
            au_blob(c, SA2S_BLOB_PCM, x->a, c->xbuf[2], NULL, c->xbuf[1]);
            break;
        case AA_DS_STATE:
        case AA_DS_NEW: {
            uint32_t addr = c->self;
            if (x->op == AA_DS_NEW && !(x->a < c->argc && c->a[x->a] && peek32(c->a[x->a], &addr)))
                addr = 0;
#ifdef REC_BREAK_AUCURSOR
            for (uint32_t i = 0; i < sa2_dsound_buffer_count(); i++) {
                uint32_t play = 0, write = 0;
                (void)sa2_dsound_buffer_cursors(i, &play, &write);
            }
#endif
#ifdef REC_BREAK_AUSTATE
            if (x->op == AA_DS_STATE && c->pre) {
                c->ds_ok = 1;
                au_blob(c, SA2S_BLOB_STATE, SA2S_NO_ARG, 0, &c->ds, sizeof c->ds);
            } else
#endif
            if (addr && ds_state_of(addr, &c->ds)) {
                c->ds_ok = 1;
                au_blob(c, SA2S_BLOB_STATE, x->op == AA_DS_NEW ? x->a : SA2S_NO_ARG, 0, &c->ds,
                        sizeof c->ds);
            } else {
                au_note(c, "%s on %#010x: the device has no buffer %#010x, so the call carries "
                           "no state", c->u->name, c->self, addr);
            }
            break;
        }
        case AA_STORE_HASH:
            au_store_unlock(c);
            break;
        case AA_XA_STATE:
        case AA_XA_NEW: {
            uint32_t addr = c->self;
            if (x->op == AA_XA_NEW && !(x->a < c->argc && c->a[x->a] && peek32(c->a[x->a], &addr)))
                addr = 0;
#ifdef REC_BREAK_AUSTATE
            if (x->op == AA_XA_STATE && c->pre)
                au_blob(c, SA2S_BLOB_STATE, SA2S_NO_ARG, 0, &c->xa, sizeof c->xa);
            else
#endif
            if (addr && xa_state_of(addr, &c->xa))
                au_blob(c, SA2S_BLOB_STATE, x->op == AA_XA_NEW ? x->a : SA2S_NO_ARG, 0, &c->xa,
                        sizeof c->xa);
            else
                au_note(c, "%s on %#010x: the device has no voice %#010x, so the call carries "
                           "no state", c->u->name, c->self, addr);
            break;
        }
        default:
            break;
        }
    }
}

/* A SA2S_AUDIO record for `c`, then its notes. A blob whose guest bytes do not read
 * is left out, never cut short, and noted. */
static void emit_audio(au_call *c) {
    uint64_t planned = 12u + 24u + 4u * (uint64_t)c->argc + 8u;
    for (int i = 0; i < c->nb; i++) planned += 8u + pad4(c->b[i].len);
    if (!wr_reserve(planned)) return;
    uint8_t *p = g_buf + g_len;
    uint32_t off = 0, failed[REC_MAX_BLOBS], nfail = 0;
    put32(p, &off, SA2S_AUDIO);
    put32(p, &off, 0);
    put32(p, &off, 0);
    const uint16_t ao[2] = {(uint16_t)c->u->api, (uint16_t)c->u->op};
    memcpy(p + off, ao, 4);
    off += 4u;
    put32(p, &off, c->self);
    put32(p, &off, c->thread);
    put32(p, &off, (uint32_t)c->us);
    put32(p, &off, (uint32_t)(c->us >> 32));
    put32(p, &off, c->argc);
    for (uint32_t k = 0; k < c->argc; k++) put32(p, &off, c->a[k]);
    put32(p, &off, c->eax);
    const uint32_t nb_at = off;
    put32(p, &off, 0);
    uint32_t nb = 0;
    for (int i = 0; i < c->nb; i++) {
        const rec_blob *b = &c->b[i];
        const uint32_t at = off;
        const uint16_t head[2] = {b->role, b->arg};
        memcpy(p + off, head, 4);
        off += 4u;
        put32(p, &off, b->len);
        if (b->host) {
            memcpy(p + off, b->host, b->len);
        } else if (sa2_peek(b->gaddr, b->len, p + off) != SA2_OK) {
            off = at;                              /* left out, not cut short */
            if (nfail < REC_MAX_BLOBS) failed[nfail++] = (uint32_t)i;
            continue;
        }
        off += b->len;
        while (off & 3u) p[off++] = 0;
        nb++;
        if (b->role < 16u) { g_role_n[b->role]++; g_role_bytes[b->role] += b->len; }
        if (b->role == SA2S_BLOB_PCM) g_au_pcm_bytes += b->len;
    }
    memcpy(p + nb_at, &nb, 4);
    wr_commit(SA2S_AUDIO, off);
    g_au_recorded[c->u->op]++;
    const uint32_t seq = g_seq - 1u;
    for (uint32_t i = 0; i < nfail; i++) {
        const rec_blob *b = &c->b[failed[i]];
        au_note(c, "%s: the %u bytes at %#010x (role %u, arg %u) did not read", c->u->name, b->len,
                b->gaddr, b->role, b->arg);
    }
    for (int i = 0; i < c->nnotes; i++) {
        char text[REC_NOTE_BYTES + 32];
        snprintf(text, sizeof text, "record %u: %s", seq, c->notes[i]);
        emit_note_text(text);
    }
    c->nnotes = 0;
}

/* A Release answers a count and a DestroyVoice nothing; every other op an HRESULT. */
static int au_succeeded(uint32_t op, uint32_t eax) {
    return op == SA2S_AU_DS_RELEASE || op == SA2S_AU_XA_DESTROY || op == SA2S_AU_XA_MASTER_DESTROY ||
           (int32_t)eax >= 0;
}

static void *au_thunk(void *s, uint32_t pc, void *m) {
    const int ds = pc >= SA2_DS_THUNK_BASE && pc < SA2_DS_THUNK_END;
    const uint32_t idx = ds ? (pc - SA2_DS_THUNK_BASE) / SA2_DS_THUNK_STRIDE
                            : (pc - SA2_XA2_THUNK_BASE) / SA2_XA2_THUNK_STRIDE;
    sa2_lifted_fn real;
    uint32_t op = 0;
    if (ds) {
        real = idx < AU_DS_THUNKS && g_au_real_ds[idx] ? g_au_real_ds[idx] : g_au_any_ds;
        if (idx < AU_DS_THUNKS) op = g_au_op_ds[idx];
    } else {
        real = idx < AU_XA_THUNKS && g_au_real_xa[idx] ? g_au_real_xa[idx] : g_au_any_xa;
        if (idx < AU_XA_THUNKS) op = g_au_op_xa[idx];
    }
    if (!g_count_on || !op || op >= SA2S_AU_OP_COUNT) return real(s, pc, m);
    g_au_entered[op]++;
    if (!g_stream_on) {
        /* Counted as with a stream: a call returned, and failed or not by its answer. */
        void *r = real(s, pc, m);
        g_au_returned[op]++;
        if (!au_succeeded(op, sa2_rt_get_gpr(s, SA2_EAX))) g_au_failed[op]++;
        return r;
    }
    au_call call;
    au_call *c = &call;
    au_reset(c);
    c->u = &AU_RULES[op - 1u];
#ifdef REC_BREAK_AUSTATE
    c->pre = 0;
#endif
    uint32_t raw[REC_MAX_ARGS + 1];
    const uint32_t esp = sa2_rt_get_gpr(s, SA2_ESP);
    const int ok = c->u->argc <= REC_MAX_ARGS &&
                   sa2_peek(esp + 4u, 4u * (c->u->argc + 1u), raw) == SA2_OK;
    if (ok) {
        c->argc = c->u->argc;
        c->self = raw[0];
        memcpy(c->a, raw + 1, 4u * c->argc);
        c->thread = (uint32_t)sa2_sched_current();
#ifdef REC_BREAK_AUSTATE
        c->pre = c->u->api == SA2S_AU_DSOUND ? ds_state_of(c->self, &c->ds) : xa_state_of(c->self, &c->xa);
#endif
    }
    void *r = real(s, pc, m);
    g_au_returned[op]++;
    if (!ok) {
        au_note(NULL, "%s returned with arguments the recorder could not read: not recorded",
                c->u->name);
        return r;
    }
    c->eax = sa2_rt_get_gpr(s, SA2_EAX);
    if (!au_succeeded(op, c->eax)) {
        g_au_failed[op]++;
        return r;
    }
#ifdef REC_BREAK_AUDROP
    if (op == SA2S_AU_DS_UNLOCK) {
        g_au_failed[op]++;
        return r;
    }
#endif
    c->us = sa2_virtual_us();
    au_post(c);
    if (op == SA2S_AU_DS_LOCK) au_store_lock(c);
    emit_audio(c);
    return r;
}

/* SA2_REC_AUDIO asks for audio: "1". Read where the audio registrations are seen, so
 * a run that does not ask never reaches rec_init from them. */
static int au_requested(void) {
    const char *v = getenv("SA2_REC_AUDIO");
    return v && !strcmp(v, "1");
}

/* ------------------------------------------------------------ the wraps */

int __real_sa2_ft_add(uint32_t va, sa2_lifted_fn fn, const char *name, uint8_t kind);
int __wrap_sa2_ft_add(uint32_t va, sa2_lifted_fn fn, const char *name, uint8_t kind) {
    if (fn && va >= SA2_D3D9_THUNK_BASE && va < SA2_D3D9_THUNK_END &&
        (va - SA2_D3D9_THUNK_BASE) % SA2_D3D9_THUNK_STRIDE == 0) {
        rec_init();
        const uint32_t idx = (va - SA2_D3D9_THUNK_BASE) / SA2_D3D9_THUNK_STRIDE;
        g_real[idx] = fn;
        g_real_any = fn;
        g_thunks_wrapped++;
#ifdef REC_BREAK_FTKIND
        kind = 0;
#endif
#ifdef REC_BREAK_FTNAME
        name = "rec-thunk";
#endif
        return __real_sa2_ft_add(va, rec_thunk, name, kind);
    }
    /* An audio thunk, with SA2_REC_AUDIO=1: au_thunk in place of each one with an op. */
    const int ds = va >= SA2_DS_THUNK_BASE && va < SA2_DS_THUNK_END &&
                   (va - SA2_DS_THUNK_BASE) % SA2_DS_THUNK_STRIDE == 0;
    const int xa = va >= SA2_XA2_THUNK_BASE && va < SA2_XA2_THUNK_END &&
                   (va - SA2_XA2_THUNK_BASE) % SA2_XA2_THUNK_STRIDE == 0;
    if (fn && (ds || xa) && au_requested()) {
        rec_init();
        const uint32_t idx = ds ? (va - SA2_DS_THUNK_BASE) / SA2_DS_THUNK_STRIDE
                                : (va - SA2_XA2_THUNK_BASE) / SA2_XA2_THUNK_STRIDE;
        const uint32_t slots = ds ? SA2_DS_MAX_SLOTS : SA2_XA2_MAX_SLOTS;
        uint32_t op = g_count_on && g_audio_on
                          ? au_op_at(ds ? SA2S_AU_DSOUND : SA2S_AU_XAUDIO2, idx / slots, idx % slots)
                          : 0u;
        /* The device registers its thunks when it makes its first object, after its
         * own configuration, which can change a count (SA2_DSOUND_BREAK=argc): held
         * again here, so a record never reads a count the device does not pop. */
        const int argc = !op ? -1 : ds ? sa2_dsound_method_argc(idx / slots, idx % slots)
                                       : sa2_xaudio2_method_argc(idx / slots, idx % slots);
        if (op && argc != (int)AU_RULES[op - 1u].argc) {
            rec_disable("the audio device gives %s::%s %d arguments at its registration, and this "
                        "recorder was written for %u", AU_RULES[op - 1u].iface, AU_RULES[op - 1u].method,
                        argc, AU_RULES[op - 1u].argc);
            op = 0;
        }
        if (op) {
            if (ds) { g_au_real_ds[idx] = fn; g_au_op_ds[idx] = (uint8_t)op; g_au_any_ds = fn; }
            else { g_au_real_xa[idx] = fn; g_au_op_xa[idx] = (uint8_t)op; g_au_any_xa = fn; }
            g_au_wrapped++;
            return __real_sa2_ft_add(va, au_thunk, name, kind);
        }
    }
    g_regs_passed++;
    return __real_sa2_ft_add(va, fn, name, kind);
}

/* The record of entry `entry`, whose arguments were `args`, after it answered. */
static void entry_record(uint32_t entry, const uint32_t *args, uint32_t eax) {
    if (!g_stream_on || entry < 1u || entry >= DX_COUNT) return;
    const rec_entry *d = &ENTRIES[entry - 1u];
    rec_call call;
    rec_call *c = &call;
    call_reset(c);
    c->t = 0xFFFFu;
    c->slot = entry;
    c->argc = d->argc;
    if (d->argc) memcpy(c->a, args, 4u * d->argc);
    c->eax = eax;
    c->act = d->act;
    rec_post(c);
    emit_call(c, entry);
}

/* The entry points that take the import's argument array, `n` of them. */
#define ENTRY_WRAP(fn, entry, n)                                                    \
    uint32_t __real_##fn(const uint32_t *a, uint32_t caller);                       \
    uint32_t __wrap_##fn(const uint32_t *a, uint32_t caller) {                      \
        rec_init();                                                                 \
        if (!g_count_on) return __real_##fn(a, caller);                             \
        uint32_t args[n];                                                           \
        memcpy(args, a, sizeof args);                                               \
        g_dx_entered[entry]++;                                                      \
        const uint32_t eax = __real_##fn(a, caller);                                \
        g_dx_returned[entry]++;                                                     \
        entry_record(entry, args, eax);                                             \
        return eax;                                                                 \
    }

ENTRY_WRAP(sa2_d3d9_d3dx_texture_from_memory, SA2S_D3DX_TEXTURE_FROM_MEMORY, 15)
ENTRY_WRAP(sa2_d3d9_d3dx_filter_texture, SA2S_D3DX_FILTER_TEXTURE, 4)
ENTRY_WRAP(sa2_d3d9_d3dx_load_surface_from_surface, SA2S_D3DX_LOAD_SURFACE, 8)
ENTRY_WRAP(sa2_d3d9_d3dx_get_shader_constant_table, SA2S_D3DX_GET_CONSTANT_TABLE, 2)
ENTRY_WRAP(sa2_d3d9_d3dx_cube_from_memory, SA2S_D3DX_CUBE_TEXTURE_FROM_MEMORY, 14)
ENTRY_WRAP(sa2_d3d9_d3dx_volume_from_memory, SA2S_D3DX_VOLUME_TEXTURE_FROM_MEMORY, 16)
ENTRY_WRAP(sa2_d3d9_d3dx_create_cube_texture, SA2S_D3DX_CREATE_CUBE_TEXTURE, 7)
ENTRY_WRAP(sa2_d3d9_d3dx_create_volume_texture, SA2S_D3DX_CREATE_VOLUME_TEXTURE, 9)
ENTRY_WRAP(sa2_d3d9_d3dx_save_surface_to_file, SA2S_D3DX_SAVE_SURFACE_TO_FILE, 5)
ENTRY_WRAP(sa2_d3d9_d3dx_save_texture_to_file, SA2S_D3DX_SAVE_TEXTURE_TO_FILE, 4)

uint32_t __real_sa2_d3d9_d3dx_create_texture(uint32_t device, uint32_t w, uint32_t h,
                                             uint32_t levels, uint32_t usage, uint32_t format,
                                             uint32_t pool, uint32_t out_pp, uint32_t caller);
uint32_t __wrap_sa2_d3d9_d3dx_create_texture(uint32_t device, uint32_t w, uint32_t h,
                                             uint32_t levels, uint32_t usage, uint32_t format,
                                             uint32_t pool, uint32_t out_pp, uint32_t caller) {
    rec_init();
    if (!g_count_on)
        return __real_sa2_d3d9_d3dx_create_texture(device, w, h, levels, usage, format, pool,
                                                   out_pp, caller);
    g_dx_entered[SA2S_D3DX_CREATE_TEXTURE]++;
    const uint32_t eax = __real_sa2_d3d9_d3dx_create_texture(device, w, h, levels, usage,
                                                             format, pool, out_pp, caller);
    g_dx_returned[SA2S_D3DX_CREATE_TEXTURE]++;
    const uint32_t args[8] = {device, w, h, levels, usage, format, pool, out_pp};
    entry_record(SA2S_D3DX_CREATE_TEXTURE, args, eax);
    return eax;
}

uint32_t __real_sa2_d3d9_make_d3d9(uint32_t caller);
uint32_t __wrap_sa2_d3d9_make_d3d9(uint32_t caller) {
    rec_init();
    if (!g_count_on) return __real_sa2_d3d9_make_d3d9(caller);
    g_dx_entered[SA2S_D3DX_DIRECT3D_CREATE9]++;
    const uint32_t eax = __real_sa2_d3d9_make_d3d9(caller);
    g_dx_returned[SA2S_D3DX_DIRECT3D_CREATE9]++;
    entry_record(SA2S_D3DX_DIRECT3D_CREATE9, NULL, eax);
    return eax;
}
