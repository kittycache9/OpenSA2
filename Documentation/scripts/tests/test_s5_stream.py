"""The S5 Direct3D 9 call stream: its reader, its format, the recorder that writes it,
and the runner that holds a presentation run to the registered replay.

host/sa2_present_rec.c records every call the lifted game makes on the recording
device (runtime/sa2_d3d9.c) into the stream host/sa2_stream.h describes;
tools/s5/stream_dump.py reads it back; tools/s5/present_run.py runs the boot with it
and says whether the guest saw anything different; tools/s5/objcheck.py holds the
recorder's object. All are measuring instruments -- the DXVK player renders what the
stream says, and the presentation run's parity is read through them -- so every
check here has a case that must fail it, down to the comparisons inside each check:

  * the reader, on small streams made here byte by byte: a whole stream reads to
    literal counts; each framing malformation and each content malformation (a blob
    its rule does not give, a Present not followed by its FRAME, a FRAME whose clock
    goes back, each field of a LOCK that is not the lock opened, a lock that lost its
    upload, a shader walk that does not end, pad bytes that are not zero, a blob of no
    role, an object nothing introduced, an entry's argc) is a problem, and under
    SA2_STREAM_DUMP_BREAK naming that check the same stream reads clean. A header
    whose tables are not the shapes the reader indexes is not a stream; a rule or
    entry row that does not read is a problem of that row. Holding a stream to a run
    record: each word of a load's description, the load count, the FILE total, the
    import argc, and the device-log walk's order, `this` and answer.
    Object-producing out parameters are classified by method and success;
    same stream reads clean. Only an out-parameter that answers an object, of a call
    that answered 0, introduces one. The buffer watch recomputed from a stream's own
    records, and held to the counts file's.
  * the format: the rule lines of host/sa2_stream.h equal the table the writer puts
    in a stream's header, both ways, and every method the recording device flags as
    writing through a pointer has a line.
  * the recorder, on the recording device itself with no game
    (tools/s5/tests/rec_harness.c, built with the pinned zig from that file,
    runtime/sa2_d3d9.c and, but for the control, the recorder and the -wrap flags a
    presentation build links it with): the guest sees the same answers, register
    file (the harness's 40-byte stand-in for it, not the runtime's State), function
    table and memory with the recorder on as in a build without it, in every
    scenario; the stream holds each call's arguments and blobs as the harness wrote
    them, a Present's rectangle and region as they were before another thread
    rewrote them inside its wait; every loss is a note, counted. The recorder's
    compile-time breaks each fail the check that names them: REC_BREAK_REGSTORE,
    _FTKIND and _FTNAME move only the register file or the function table, which
    only the harness compares.
  * the recorder's object, compiled as tools/s4/link_s4.py compiles it (the command
    read from link_s4's own compile phase): its undefined symbols are exactly
    objcheck's list, it defines exactly the wraps, every executable section is
    PROGBITS and every branch target, symbol and relocation target in one starts an
    instruction, and it holds no floating point, FP-state or system instruction, no
    SSE instruction outside SIMD_ALLOWED, no double to a variadic call or jump and no
    float conversion in any allocated section; each rule has a compiled falsifier,
    the REC_BREAK_POKE build fails, and the source holds no inline asm.
  * the runner's checks, on small fixtures: each holds on a run equal to the
    registered one and fails when any one of its comparisons fails; the build check
    fails for each way a build is not this tree's or not the binary that ran; READY
    is written only for a stream that holds, with the run's meta, and never while
    another runner holds the trace's lock; a failed run deletes its partial stream;
    --check refuses without a receipt, fails a run whose files are not the tree's,
    fails a trace that is not a stream with a receipt, withdraws READY only from
    a stream that is its own run's, and records whether the run it holds asked for
    audio as that run's meta says, not as --audio does.
    Settings, save reset, stage outputs and input are also held, and a counts file
    with audio is held by audio_counts too (test_s5_audio.py holds the rest of audio).
  * Metal Harbor (unit MH-REC, the end of the file): the calls it makes beyond the
    boot, on the recording device with no game (tools/s5/tests/rec_harness_mh.c):
    vertex buffers locked at an offset and whole and drawn by DrawPrimitive, a
    render-target texture's level and its own depth set as targets, GetBackBuffer,
    GetRenderTarget, GetDepthStencilSurface and D3DXLoadSurfaceFromSurface; the guest
    sees the same with the recorder on; a buffer copied from its store's base
    (REC_BREAK_BUFBASE) and a buffer written after its Unlock (the buffer watch) are
    caught, and a watch that stopped checking (REC_BREAK_NOWATCH) fails the runner's
    stream check. The stream's own counts held to the run's record
    (stream_dump.hold_counts_to_run, its break runcounts), the objects it introduces
    to the ones the device built among them. The runner's stage path:
    tools/s4/stage_run.py's command for the registered observed run under present-mh,
    run_stage's guards, what --check holds of a stage run's files, every stage hook's
    object compiled afresh, the other replay's build names refused, and the buffer
    watch held in the counts check.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s5"))
import stream_dump as SD  # noqa: E402
import objcheck as OC  # noqa: E402

ZIG = ROOT / "private" / "predecessor-tools" / "zig" / "zig-x86_64-linux-0.15.2" / "zig"
HARNESS = ROOT / "tools" / "s5" / "tests" / "rec_harness.c"
RECORDER = ROOT / "host" / "sa2_present_rec.c"
STREAM_H = ROOT / "host" / "sa2_stream.h"
D3D9_C = ROOT / "runtime" / "sa2_d3d9.c"
WRAPS = OC.WRAPS


# The audio devices' table getters host/sa2_present_rec.c references (SA2_REC_AUDIO=1
# only), as inert stand-ins for the harnesses here, which have no audio device: no
# interfaces, no buffers, no voices. Documentation/scripts/tests/test_s5_audio.py
# holds the recorder's audio path on the runtime's own devices.
AUDIO_GETTERS_C = """
#include <stddef.h>
#include "sa2_dsound.h"
#include "sa2_xaudio2.h"
uint32_t sa2_dsound_type_count(void) { return 0; }
const char *sa2_dsound_type_name(uint32_t t) { (void)t; return NULL; }
const char *sa2_dsound_method_name(uint32_t t, uint32_t s) { (void)t; (void)s; return NULL; }
int sa2_dsound_method_argc(uint32_t t, uint32_t s) { (void)t; (void)s; return -1; }
uint32_t sa2_dsound_buffer_count(void) { return 0; }
const sa2_ds_buffer *sa2_dsound_buffer_at(uint32_t i) { (void)i; return NULL; }
uint32_t sa2_xaudio2_type_count(void) { return 0; }
const char *sa2_xaudio2_type_name(uint32_t t) { (void)t; return NULL; }
const char *sa2_xaudio2_method_name(uint32_t t, uint32_t s) { (void)t; (void)s; return NULL; }
int sa2_xaudio2_method_argc(uint32_t t, uint32_t s) { (void)t; (void)s; return -1; }
uint32_t sa2_xaudio2_voice_count(void) { return 0; }
const sa2_xa2_voice *sa2_xaudio2_voice_at(uint32_t i) { (void)i; return NULL; }
"""


def audio_getters(d: Path) -> Path:
    """AUDIO_GETTERS_C written into the build directory `d`."""
    src = d / "audio_getters.c"
    if not src.is_file():
        src.write_text(AUDIO_GETTERS_C)
    return src


def zig_env(local: Path) -> dict:
    """The environment a test compile runs in: zig's caches, and nothing else, moved."""
    return dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(local),
                ZIG_GLOBAL_CACHE_DIR=os.environ.get(
                    "ZIG_GLOBAL_CACHE_DIR", str(ROOT / "private" / "generated" / "zig-cache-tests")))


def need_zig() -> None:
    """The recorder and object tests need the pinned zig: without it they fail, since
    a skip would pass the gate with the instrument unheld."""
    if not ZIG.is_file():
        pytest.fail(f"the pinned zig is not present: {ZIG}")


def pad4(n: int) -> int:
    return (n + 3) & ~3


# ---------------------------------------------------------------- the reader

HEADER = {"schema": "sa2-d3d9-stream/v1", "writer": "test_s5_stream.py",
          "meta": json.dumps({"build": "test"}),
          "object_base": 0x03200000, "object_stride": 16,
          "interfaces": ["IDirect3D9", "IDirect3DDevice9", "IDirect3DTexture9",
                         "IDirect3DCubeTexture9", "IDirect3DVolumeTexture9", "IDirect3DSurface9",
                         "IDirect3DVolume9", "IDirect3DVertexBuffer9"],
          "methods": [[0, 16, "CreateDevice", 6], [1, 17, "Present", 4], [1, 41, "BeginScene", 0],
                      [1, 26, "CreateVertexBuffer", 6], [1, 84, "DrawIndexedPrimitiveUP", 8],
                      [1, 91, "CreateVertexShader", 2], [1, 92, "SetVertexShader", 1],
                      [1, 94, "SetVertexShaderConstantF", 3],
                      [2, 4, "SetPrivateData", 4], [2, 18, "GetSurfaceLevel", 2],
                      [5, 13, "LockRect", 3], [5, 14, "UnlockRect", 0],
                      [7, 11, "Lock", 4], [7, 12, "Unlock", 0]],
          "rules": [[0, 16, "pre_pp 4; out 5 4; device"],
                    [1, 17, "pre_rect 0; pre_rect 1; pre_rgn 3; frame"],
                    [1, 26, "out 4 4; new_buf 4 0"], [1, 84, "iup"],
                    [1, 91, "shader 0; out 1 4"], [1, 94, "in_vec 1 2 16"],
                    [2, 4, "in 0 16; in_vec 1 2 1"],
                    [2, 18, "out 1 4; level 1"], [5, 13, "out 0 8; in 1 16; lock_surf"],
                    [5, 14, "unlock_surf"], [7, 11, "out 2 4; lock_buf"], [7, 12, "unlock_buf"]],
          "entries": [[1, "D3DXCreateTextureFromFileInMemoryEx", 15,
                       "file 1 2; out 14 4; out 12 28; out 13 1024; file_desc 14"],
                      [2, "D3DXCreateTexture", 8, "out 7 4; new_tex 7 1 2 5"],
                      [3, "D3DXFilterTexture", 4, "in 1 1024"],
                      [12, "Direct3DCreate9", 0, "object_eax"]],
          "note_classes": ["blob-left-out"]}
D3D9, DEV, RT, DS, TEX, SURF = 0x03200000, 0x03200010, 0x03200020, 0x03200030, 0x03200040, 0x03200050
VB, LOADED = 0x03200070, 0x03200080
UNKNOWN = 0x03200990
PP = struct.pack("<14I", 64, 32, 21, 1, 0, 0, 1, 0, 1, 1, 75, 0, 0, 0)
CONSTS = bytes(range(32))
VS = struct.pack("<5I", 0xFFFE0300, 0x0200001F, 0x80000000, 0x900F0000, 0x0000FFFF)
LEVEL1 = bytes((i * 7) & 0xFF for i in range(128 * 16))     # 32x16 A8R8G8B8: pitch 128
DXT1 = 0x31545844
DXT5 = 0x35545844
NO_ARG = 0xFFFF


def make(records: list[tuple[int, bytes]], *, header: dict | None = None,
         seqs: list[int] | None = None) -> bytes:
    """A stream: the header, then each (kind, payload) numbered 0, 1, 2, ... (or `seqs`)."""
    head = json.dumps(header or HEADER).encode()
    hb = 16 + pad4(len(head))
    out = bytearray(b"SA2D3D9S" + struct.pack("<II", 1, hb) + head + b" " * (hb - 16 - len(head)))
    for i, (kind, payload) in enumerate(records):
        out += struct.pack("<III", kind, 12 + len(payload), seqs[i] if seqs else i) + payload
    return bytes(out)


def blobs(items, pad: bytes = b"\0") -> bytes:
    out = struct.pack("<I", len(items))
    for role, arg, data in items:
        out += struct.pack("<HHI", role, arg, len(data)) + data + pad * (pad4(len(data)) - len(data))
    return out


def call(t, slot, this, args, eax=0, items=(), thread=0, pad=b"\0") -> tuple[int, bytes]:
    return (1, struct.pack("<HHIII", t, slot, this, thread, len(args))
            + struct.pack(f"<{len(args)}I", *args) + struct.pack("<I", eax) + blobs(list(items), pad))


def d3dx(entry, args, eax=0, items=()) -> tuple[int, bytes]:
    return (2, struct.pack("<II", entry, len(args)) + struct.pack(f"<{len(args)}I", *args)
            + struct.pack("<I", eax) + blobs(list(items)))


def frame(us, frames, polls) -> tuple[int, bytes]:
    return (3, struct.pack("<IIII", us & 0xFFFFFFFF, us >> 32, frames, polls))


def note(text: str) -> tuple[int, bytes]:
    b = text.encode()
    return (4, struct.pack("<I", len(b)) + b + b"\0" * (pad4(len(b)) - len(b)))


END = (5, b"")
w32 = lambda v: struct.pack("<I", v)  # noqa: E731


def lock_info(bits, pitch=128, rows=16, nbytes=128 * 16, has_rect=0, rect=(0, 0, 32, 16), *,
              obj=SURF, container=TEX, level=1, fmt=21, w=32, h=16, flags=0, offset=0):
    """sa2s_lock_info, by default the whole stream's level-1 surface lock."""
    return struct.pack("<11I4i2I", obj, container, level, bits, pitch, rows, nbytes, fmt, w, h,
                       has_rect, *rect, flags, offset)


GUID = bytes(range(16))
FIVE = b"\x01\x02\x03\x04\x05"          # a blob that needs three bytes of padding


def private_data(pad: bytes = b"\0") -> tuple[int, bytes]:
    return call(2, 4, TEX, [0x03004400, 0x03004410, 5, 0], items=[(1, 0, GUID), (1, 1, FIVE)],
                pad=pad)


def device() -> tuple[int, bytes]:
    return call(0, 16, D3D9, [0, 1, 0x7e000000, 0x40, 0x03004000, 0x03004100],
                items=[(1, 4, PP), (2, 5, w32(DEV)), (5, NO_ARG, struct.pack("<3I", DEV, RT, DS))])


def whole() -> list[tuple[int, bytes]]:
    """Direct3DCreate9, CreateDevice, a shader, constants, a call on another thread,
    a texture whose level 1 is locked and unlocked, five bytes of private data, a
    Present and its FRAME, a note."""
    return [d3dx(12, [], eax=D3D9),
            device(),
            call(1, 91, DEV, [0x03004200, 0x03004104], items=[(1, 0, VS), (2, 1, w32(0x03200060))]),
            call(1, 94, DEV, [4, 0x03004300, 2], items=[(1, 1, CONSTS)]),
            call(1, 41, DEV, [], thread=1),
            d3dx(2, [DEV, 64, 32, 0, 0, 21, 1, 0x03004108], items=[(2, 7, w32(TEX))]),
            call(2, 18, TEX, [1, 0x0300410c], items=[(2, 1, w32(SURF))]),
            call(5, 13, SURF, [0x03004110, 0, 0], items=[(2, 0, struct.pack("<iI", 128, 0x04001000))]),
            call(5, 14, SURF, [], items=[(6, NO_ARG, lock_info(0x04001000)), (3, NO_ARG, LEVEL1)]),
            private_data(),
            call(1, 17, DEV, [0, 0, 0, 0]),
            frame(5_000_016_666, 7, 77),
            note("a note"),
            END]


# A vertex buffer, as Metal Harbor makes them: the header's table with the buffer
# interface and its calls, and the buffer's records.
BUF_HEADER = dict(HEADER,
                  interfaces=HEADER["interfaces"] + ["IDirect3DVolume9", "IDirect3DVertexBuffer9"],
                  methods=HEADER["methods"] + [[1, 26, "CreateVertexBuffer", 6], [7, 2, "Release", 0],
                                               [7, 11, "Lock", 4], [7, 12, "Unlock", 0]],
                  rules=HEADER["rules"] + [[1, 26, "out 4 4; new_buf 4 0"],
                                           [7, 11, "out 2 4; lock_buf"], [7, 12, "unlock_buf"]])
VB, VB_BITS = 0x03200070, 0x04100000
VBDATA = bytes((i * 5 + 3) & 0xFF for i in range(0x200))


def buf_lock_info(bits=VB_BITS + 0x100, pitch=0, rows=1, nbytes=0x200, fmt=0, w=0, h=0, has_rect=0,
                  flags=0, offset=0x100) -> bytes:
    return struct.pack("<11I4i2I", VB, 0, 0, bits, pitch, rows, nbytes, fmt, w, h, has_rect,
                       0, 0, 0, 0, flags, offset)


def buffer_calls(unlock: tuple[int, bytes] | None = None) -> list[tuple[int, bytes]]:
    """A buffer of 0x300 bytes made, locked from 0x100 to its end, and unlocked with
    the 0x200 bytes the lock covers (or `unlock`)."""
    return [call(1, 26, DEV, [0x300, 8, 0, 1, 0x03004500, 0], items=[(2, 4, w32(VB))]),
            call(7, 11, VB, [0x100, 0, 0x03004504, 0], items=[(2, 2, w32(VB_BITS + 0x100))]),
            unlock or call(7, 12, VB, [], items=[(6, 0xFFFF, buf_lock_info()), (3, 0xFFFF, VBDATA)])]


def with_buffer(unlock: tuple[int, bytes] | None = None) -> bytes:
    """whole(), with a buffer's calls before its Present."""
    w = whole()
    return make(w[:10] + buffer_calls(unlock) + w[10:], header=BUF_HEADER)


def buf_unlock(**lock) -> tuple[int, bytes]:
    """The buffer's Unlock with its LOCK changed as `lock` says, and the LOCKED blob
    as long as that LOCK claims (none for 0)."""
    n = lock.get("nbytes", 0x200)
    return call(7, 12, VB, [], items=[(6, 0xFFFF, buf_lock_info(**lock))]
                + ([(3, 0xFFFF, VBDATA[:n])] if n else []))


# The recording device's pool is two blocks (runtime/sa2_d3d9.h): indices 16384 on
# are in the overflow block, below the first block's object_base. The header says so
# in "object_blocks" (host/sa2_stream.h); OVF_VB is that block's first object.
BLOCKS = [[0x03200000, 0, 16384], [0x00010000, 16384, 126976]]
OVF_VB = 0x00010000


def overflow_stream(header: dict) -> bytes:
    """whole(), with buffer_calls()'s buffer made at OVF_VB, locked and unlocked."""
    info = struct.pack("<11I4i2I", OVF_VB, 0, 0, VB_BITS + 0x100, 0, 1, 0x200, 0, 0, 0, 0,
                       0, 0, 0, 0, 0, 0x100)
    calls = [call(1, 26, DEV, [0x300, 8, 0, 1, 0x03004500, 0], items=[(2, 4, w32(OVF_VB))]),
             call(7, 11, OVF_VB, [0x100, 0, 0x03004504, 0], items=[(2, 2, w32(VB_BITS + 0x100))]),
             call(7, 12, OVF_VB, [], items=[(6, 0xFFFF, info), (3, 0xFFFF, VBDATA)])]
    w = whole()
    return make(w[:10] + calls + w[10:], header=header)


def scan(tmp_path: Path, data: bytes, brk: str | None = None, name: str = "s.sa2s",
         **kw) -> dict:
    p = tmp_path / name
    p.write_bytes(data)
    old = os.environ.pop("SA2_STREAM_DUMP_BREAK", None)
    if brk:
        os.environ["SA2_STREAM_DUMP_BREAK"] = brk
    try:
        return SD.scan(p, want_range=(0, 100), **kw)
    finally:
        os.environ.pop("SA2_STREAM_DUMP_BREAK", None)
        if old is not None:
            os.environ["SA2_STREAM_DUMP_BREAK"] = old


def test_a_whole_stream_reads_to_its_counts(tmp_path):
    s = scan(tmp_path, make(whole()))
    assert s["problems"] == []
    assert s["end"] is True and s["records"] == 14
    # each record's bytes: its 12-byte head, fixed fields, args, eax and blob count,
    # and 8 bytes of head and the padded data per blob
    assert s["kinds"] == {"call": {"records": 9, "bytes": 156 + 84 + 88 + 36 + 56 + 64 + 2168
                                                          + 92 + 52},
                          "d3dx": {"records": 2, "bytes": 28 + 72},
                          "frame": {"records": 1, "bytes": 28}, "note": {"records": 1, "bytes": 24},
                          "end": {"records": 1, "bytes": 12}}
    assert [(m["interface"], m["method"], m["calls"]) for m in s["methods"]] == [
        ("IDirect3D9", "CreateDevice", 1), ("IDirect3DDevice9", "Present", 1),
        ("IDirect3DDevice9", "BeginScene", 1), ("IDirect3DDevice9", "CreateVertexShader", 1),
        ("IDirect3DDevice9", "SetVertexShaderConstantF", 1), ("IDirect3DTexture9", "SetPrivateData", 1),
        ("IDirect3DTexture9", "GetSurfaceLevel", 1), ("IDirect3DSurface9", "LockRect", 1),
        ("IDirect3DSurface9", "UnlockRect", 1)]
    assert s["d3dx"] == {"D3DXCreateTexture": 1, "Direct3DCreate9": 1} and s["d3dx_calls"] == 1
    assert s["blobs"]["locked"] == {"blobs": 1, "bytes": 2048}
    assert s["first_frame"] == {"seq": 11, "virtual_us": 5_000_016_666, "frames": 7, "polls": 77}
    assert s["notes"] == ["a note"] and s["note_records"] == 1
    assert s["meta"] == {"build": "test"} and s["content_checked"] == 11
    assert s["locks_open_at_end"] == 0
    # D3D9, the device and its two surfaces, the shader, the texture, its level
    assert s["objects_introduced"] == 7
    r = s["range"]
    assert r[4]["call"] == "IDirect3DDevice9::BeginScene" and r[4]["thread"] == 1
    assert r[3]["blobs"][0]["head"] == CONSTS.hex()


def test_blob_padding_is_what_keeps_the_framing(tmp_path):
    """Under the pad break the reader forgets the padding after the 5-byte blob."""
    assert scan(tmp_path, make(whole()))["problems"] == []
    assert scan(tmp_path, make(whole()), "pad")["problems"] != []


def test_a_u64_is_read_as_two_words(tmp_path):
    assert scan(tmp_path, make(whole()))["first_frame"]["virtual_us"] == 5_000_016_666
    assert scan(tmp_path, make(whole()), "u64")["first_frame"]["virtual_us"] == 5_000_016_666 & 0xFFFFFFFF


def replace(i: int, rec: tuple[int, bytes]) -> list[tuple[int, bytes]]:
    w = whole()
    w[i] = rec
    return w


def unlock(info: bytes, locked: bytes | None) -> tuple[int, bytes]:
    return call(5, 14, SURF, [], items=[(6, NO_ARG, info)] + ([(3, NO_ARG, locked)] if locked else []))


@pytest.mark.parametrize("brk,build,needle", [
    ("seq", lambda: make(whole(), seqs=[0, 1, 2, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]),
     "where 3 was due"),
    ("end", lambda: make(whole()[:-1]), "no END record"),
    ("tail", lambda: make(whole()) + struct.pack("<III", 4, 16, 14) + struct.pack("<I", 0),
     "follow the END record"),
    ("fill", lambda: make(replace(3, (1, whole()[3][1] + b"\0\0\0\0"))), "do not fill"),
    ("trunc", lambda: make(whole())[:-20], "cut short"),
    ("argc", lambda: make(replace(4, call(1, 41, DEV, [9], thread=1))),
     "the header's method table says 0"),
    # the content: each record held to its rule
    ("rules", lambda: make(replace(3, call(1, 94, DEV, [4, 0x03004300, 3], items=[(1, 1, CONSTS)]))),
     "its rule gives 48"),
    ("rules", lambda: make(replace(4, call(1, 41, DEV, [], items=[(1, NO_ARG, b"xx")], thread=1))),
     "blobs its rule does not give"),
    ("rules", lambda: make(replace(9, call(2, 4, TEX, [0x03004400, 0x03004410, 5, 0],
                                           items=[(1, 0, GUID)]))),
     "no in blob of arg 1, which its rule gives"),
    ("rules", lambda: make(whole()[:10] + [call(1, 84, DEV, [4, 2, 4, 2, 0x03004800, 102, 0x03004900, 12],
                                                items=[(1, 4, bytes(12)), (1, 6, bytes(72))])]
                           + whole()[10:]),
     "the in blob of arg 4 is 12 bytes, and its rule gives 24"),
    ("frame", lambda: make(whole()[:11] + [note("between")] + whole()[11:]), "not its FRAME"),
    ("frame", lambda: make(whole()[:4] + [frame(1, 1, 1)] + whole()[4:]),
     "does not follow a Present"),
    ("frame", lambda: make(whole()[:11]), "the stream ends after the Present at seq 10, with no FRAME"),
    ("clock", lambda: make(whole()[:12] + [call(1, 17, DEV, [0, 0, 0, 0]), frame(5_000_000_000, 8, 78)]
                           + whole()[12:]),
     "the FRAME at seq 13 goes back"),
    ("clock", lambda: make(whole()[:12] + [call(1, 17, DEV, [0, 0, 0, 0]), frame(5_000_020_000, 6, 78)]
                           + whole()[12:]),
     "the FRAME at seq 13 goes back"),
    ("lock", lambda: make(replace(8, unlock(lock_info(0x04002000), LEVEL1))), "where the Lock answered"),
    ("lock", lambda: make(replace(8, unlock(lock_info(0x04001000, rows=15, nbytes=128 * 15),
                                            LEVEL1[:128 * 15]))),
     "rows where the level and rectangle give 16"),
    # a level the stream sizes, whose LOCK claims no bytes and carries none
    ("lock", lambda: make(replace(8, call(5, 14, SURF, [], items=[
        (6, 0xFFFF, lock_info(0x04001000, nbytes=0))]))),
     "0 bytes where the level's rows at the Lock's pitch are 2048"),
    ("rules", lambda: make(replace(8, call(5, 14, SURF, [], items=[
        (6, 0xFFFF, lock_info(0x04001000, nbytes=0))]))),
     "no locked blob of the call, which its rule gives"),
    # a buffer's LOCK: its offset, its size, its shape and its flags each the Lock's
    ("lock", lambda: with_buffer(buf_unlock(offset=0x80)), "offset 128 where the Lock passed 256"),
    ("lock", lambda: with_buffer(buf_unlock(nbytes=0x100)), "256 bytes where the Lock covers 512"),
    ("lock", lambda: with_buffer(buf_unlock(nbytes=0)), "0 bytes where the Lock covers 512"),
    ("rules", lambda: with_buffer(buf_unlock(nbytes=0)),
     "no locked blob of the call, which its rule gives"),
    ("lock", lambda: with_buffer(buf_unlock(rows=2)),
     "a buffer's lock with a pitch, rows beyond one, or a shape"),
    ("lock", lambda: with_buffer(buf_unlock(pitch=0x200)),
     "a buffer's lock with a pitch, rows beyond one, or a shape"),
    ("lock", lambda: with_buffer(buf_unlock(has_rect=1)),
     "a buffer's lock with a pitch, rows beyond one, or a shape"),
    ("lock", lambda: with_buffer(buf_unlock(flags=0x2000)), "flags 0x2000 where the Lock passed 0x0"),
    ("walk", lambda: make(replace(2, call(1, 91, DEV, [0x03004200, 0x03004104],
                                          items=[(1, 0, VS[:16]), (2, 1, w32(0x03200060))]))),
     "without meeting the end token"),
    ("padzero", lambda: make(replace(9, private_data(pad=b"\x07"))), "is not zero"),
    ("blobhead", lambda: make(replace(4, call(1, 41, DEV, [], items=[(10, NO_ARG, b"")], thread=1))),
     "which no call of 0 arguments carries"),
    ("objects", lambda: make(replace(4, call(1, 41, UNKNOWN, [], thread=1))),
     "an object nothing earlier in the stream introduced"),
    ("objects", lambda: make(whole()[1:]), "0x03200000 is an object nothing"),
    ("objects", lambda: make(replace(4, call(1, 92, DEV, [UNKNOWN]))),
     "(IDirect3DDevice9::SetVertexShader): arg 0 0x03200990 is an object nothing"),
    ("objects", lambda: make(replace(5, d3dx(2, [UNKNOWN, 64, 32, 0, 0, 21, 1, 0x03004108],
                                             items=[(2, 7, w32(TEX))]))),
     "(D3DXCreateTexture): arg 0 0x03200990 is an object nothing"),
    ("objects", lambda: make(whole()[:4] + [call(1, 91, DEV, [0x03004200, 0x03004104],
                                                 items=[(1, 0, VS), (2, 1, w32(7))]),
                                            call(1, 92, DEV, [7])] + whole()[4:]),
     "arg 0 0x00000007 is an object nothing"),
    ("entryargc", lambda: make(replace(0, d3dx(12, [7], eax=D3D9))),
     "and the header's entries table says 0"),
])
def test_each_malformation_is_a_problem_and_its_break_hides_it(tmp_path, brk, build, needle):
    data = build()
    bad = scan(tmp_path, data)["problems"]
    assert any(needle in p for p in bad), bad
    assert not any(needle in p for p in scan(tmp_path, data, brk)["problems"])


@pytest.mark.parametrize("field,info,locked,needle", [
    ("object", lock_info(0x04001000, obj=TEX), LEVEL1, "object 0x03200040"),
    ("flags", lock_info(0x04001000, flags=0x10), LEVEL1, "flags 0x10 where the Lock passed 0x0"),
    ("pitch", lock_info(0x04001000, pitch=256, nbytes=256 * 16), LEVEL1 * 2,
     "pitch 256 where the Lock answered 128; 4096 bytes where the level's rows at the Lock's pitch are 2048"),
    ("container and level", lock_info(0x04001000, level=0), LEVEL1,
     "container 0x03200040 level 0 where the stream says 0x03200040 level 1"),
    ("shape", lock_info(0x04001000, w=64), LEVEL1,
     "64x16 format 0x15 where the calls that made it say 32x16 format 0x15"),
    ("rectangle", lock_info(0x04001000, has_rect=1), LEVEL1,
     "rectangle (0, 0, 32, 16) where the Lock passed None"),
    ("bytes", lock_info(0x04001000, nbytes=128 * 15), LEVEL1[:128 * 15],
     "1920 bytes where the level's rows at the Lock's pitch are 2048"),
    ("no upload", lock_info(0x04001000, nbytes=0), None,
     "0 bytes where the level's rows at the Lock's pitch are 2048"),
])
def test_each_field_of_a_lock_is_held_to_the_lock_opened(tmp_path, field, info, locked, needle):
    """Hold the Unlock's LOCK and uploaded bytes independently to the LockRect."""
    assert scan(tmp_path, make(replace(8, unlock(lock_info(0x04001000), LEVEL1))))["problems"] == []
    data = make(replace(8, unlock(info, locked)))
    bad = scan(tmp_path, data)["problems"]
    prefix = "record seq 8 (IDirect3DSurface9::UnlockRect): "
    rules_problem = {
        "pitch": "the locked blob of the call is 4096 bytes, and its rule gives 2048",
        "bytes": "the locked blob of the call is 1920 bytes, and its rule gives 2048",
        "no upload": "no locked blob of the call, which its rule gives",
    }.get(field)
    rules = [prefix + rules_problem] if rules_problem else []
    lock = [prefix + "its LOCK: " + needle]
    assert bad == rules + lock, (field, bad)
    assert scan(tmp_path, data, "lock")["problems"] == rules
    assert scan(tmp_path, data, "rules")["problems"] == lock


def buffer_stream(unlock_items, lock_args=(0, 64, 0x03004124, 0)) -> bytes:
    """A 256-byte vertex buffer, locked and unlocked once."""
    return make([d3dx(12, [], eax=D3D9), device(),
                 call(1, 26, DEV, [256, 0, 0, 1, 0x03004120, 0], items=[(2, 4, w32(VB))]),
                 call(7, 11, VB, list(lock_args), items=[(2, 2, w32(0x04002000))]),
                 call(7, 12, VB, [], items=unlock_items), END])


def buffer_lock(nbytes=64, *, offset=0, flags=0, obj=VB, bits=0x04002000):
    return struct.pack("<11I4i2I", obj, 0, 0, bits, 0, 1, nbytes, 0, 0, 0, 0, 0, 0, 0, 0,
                       flags, offset)


@pytest.mark.parametrize("unlock_items,lock_args,needle", [
    ([(6, NO_ARG, buffer_lock(offset=16)), (3, NO_ARG, bytes(64))], (0, 64, 0x03004124, 0),
     "offset 16 where the Lock passed 0"),
    ([(6, NO_ARG, buffer_lock(32)), (3, NO_ARG, bytes(32))], (0, 64, 0x03004124, 0),
     "32 bytes where the Lock covers 64"),
    ([(6, NO_ARG, buffer_lock(0))], (0, 64, 0x03004124, 0), "0 bytes where the Lock covers 64"),
    ([(6, NO_ARG, buffer_lock(0))], (64, 0, 0x03004124, 0),
     "offset 0 where the Lock passed 64; 0 bytes where the Lock covers 192"),
])
def test_a_buffer_lock_carries_the_bytes_its_lock_covers(tmp_path, unlock_items, lock_args, needle):
    good = [(6, NO_ARG, buffer_lock(64)), (3, NO_ARG, bytes(64))]
    assert scan(tmp_path, buffer_stream(good))["problems"] == []
    rest = [(6, NO_ARG, buffer_lock(192, offset=64)), (3, NO_ARG, bytes(192))]
    assert scan(tmp_path, buffer_stream(rest, (64, 0, 0x03004124, 0)))["problems"] == []
    data = buffer_stream(unlock_items, lock_args)
    bad = scan(tmp_path, data)["problems"]
    prefix = "record seq 4 (IDirect3DVertexBuffer9::Unlock): "
    rules_problem = {
        "32 bytes where the Lock covers 64":
            "the locked blob of the call is 32 bytes, and its rule gives 64",
        "0 bytes where the Lock covers 64": "no locked blob of the call, which its rule gives",
        "offset 0 where the Lock passed 64; 0 bytes where the Lock covers 192":
            "no locked blob of the call, which its rule gives",
    }.get(needle)
    rules = [prefix + rules_problem] if rules_problem else []
    lock = [prefix + "its LOCK: " + needle]
    assert bad == rules + lock
    assert scan(tmp_path, data, "lock")["problems"] == rules
    assert scan(tmp_path, data, "rules")["problems"] == lock


def test_a_second_lock_of_a_surface_replaces_the_first(tmp_path):
    """As the recorder keeps it: the Unlock closes the last Lock of a surface, and a
    second Unlock has no lock to close. A buffer's Locks would nest instead."""
    again = call(5, 13, SURF, [0x03004110, 0, 0], items=[(2, 0, struct.pack("<iI", 128, 0x04001100))])
    w = whole()
    ok = w[:8] + [again, unlock(lock_info(0x04001100), LEVEL1), call(5, 14, SURF, [])] + w[9:]
    assert scan(tmp_path, make(ok))["problems"] == []
    stale = w[:8] + [again, unlock(lock_info(0x04001000), LEVEL1)] + w[9:]
    assert any("where the Lock answered" in p for p in scan(tmp_path, make(stale))["problems"])


def test_a_declaration_and_a_region_are_read_to_their_own_ends():
    elem = lambda stream: struct.pack("<HHBBBB", stream, 0, 2, 0, 0, 0)  # noqa: E731
    assert SD.decl_problem(elem(0) + elem(0xFF)) is None
    assert SD.decl_problem(elem(0) + elem(0)) == "the last element is not D3DDECL_END"
    assert SD.decl_problem(elem(0xFF) + elem(0xFF)) == "an element before the last is D3DDECL_END"
    assert SD.decl_problem(elem(0xFF)[:6]) == "6 bytes is not a whole number of vertex elements"
    assert SD.decl_problem(b"") == "0 bytes is not a whole number of vertex elements"
    rgn = lambda n, rects: struct.pack("<8I", 32, 1, n, 16 * n, 0, 0, 1, 1) + bytes(16 * rects)  # noqa: E731
    assert SD.rgn_problem(rgn(2, 2)) is None
    assert SD.rgn_problem(rgn(2, 2)[:31]) == "31 bytes is shorter than an RGNDATAHEADER"
    assert SD.rgn_problem(rgn(17, 17)) == "nCount 17 is over the 16 rectangles a region carries"
    assert SD.rgn_problem(rgn(2, 1)) == "48 bytes is not the header and its 2 rectangles"


def test_a_description_names_what_its_call_wrote(tmp_path):
    """CreateDevice's DESC names the device its out-parameter holds, and a load's DESC
    the texture it wrote."""
    wrong = call(0, 16, D3D9, [0, 1, 0x7e000000, 0x40, 0x03004000, 0x03004100],
                 items=[(1, 4, PP), (2, 5, w32(DEV)), (5, NO_ARG, struct.pack("<3I", UNKNOWN, RT, DS))])
    bad = scan(tmp_path, make(replace(1, wrong)))["problems"]
    assert any("its DESC names device 0x03200990 and its out-parameter 0x03200010" in p for p in bad), bad

    def load(desc_obj):
        dds = b"DDS " + bytes(60)
        desc = struct.pack("<22I", desc_obj, 1, 0, 64, 32, 1, 1, DXT1, 64, 32, 1, 1, DXT1,
                           1, 1, 1, 0, 0, 0, 0, 1024, 1024)
        args = [DEV, 0x03004600, len(dds)] + [0] * 9 + [0x03004700, 0, 0x03004710]
        return d3dx(1, args, items=[(4, 1, dds), (2, 14, w32(LOADED)), (2, 12, bytes(28)),
                                    (5, NO_ARG, desc)])
    good = scan(tmp_path, make(whole()[:10] + [load(LOADED)] + whole()[10:]))
    assert good["problems"] == [] and good["files"][0]["texture"] == LOADED
    assert good["files"][0]["entry"] == 1
    bad = scan(tmp_path, make(whole()[:10] + [load(UNKNOWN)] + whole()[10:]))["problems"]
    assert bad == ["record seq 10 (D3DXCreateTextureFromFileInMemoryEx): its DESC describes "
                   "0x03200990 and the call wrote 0x03200080"]


def test_the_header_names_each_entry_as_the_format_does(tmp_path):
    head = dict(HEADER, entries=[[2, "D3DXWrong", 8, "out 7 4; new_tex 7 1 2 5"]] + HEADER["entries"][2:])
    bad = scan(tmp_path, make(whole(), header=head))["problems"]
    assert "the header names entry 2 'D3DXWrong'; host/sa2_stream.h names it 'D3DXCreateTexture'" in bad


def test_an_indexed_draw_carries_its_indices_and_vertices(tmp_path):
    iup = call(1, 84, DEV, [4, 2, 4, 2, 0x03004800, 101, 0x03004900, 12],
               items=[(1, 4, bytes(12)), (1, 6, bytes(72))])
    assert scan(tmp_path, make(whole()[:10] + [iup] + whole()[10:]))["problems"] == []


def test_what_is_not_a_stream_is_refused(tmp_path):
    good = make(whole())
    for data, why in ((b"NOTASTRM" + good[8:], "magic"),
                      (good[:8] + struct.pack("<I", 3) + good[12:], "version 3; this reader"),
                      (good[:8] + struct.pack("<I", 2) + good[12:], r"not sa2-d3d9-stream/v2 \(version 2\)"),
                      (good[:12] + struct.pack("<I", 18) + good[16:], "header_bytes"),
                      (b"", "magic")):
        p = tmp_path / "x.sa2s"
        p.write_bytes(data)
        with pytest.raises(SD.StreamError, match=why):
            SD.scan(p)


# Headers whose JSON reads and whose tables do not have the shapes the reader indexes.
BAD_TABLES = [
    ("a method row of three words", {"methods": [[1, 17, "Present"]] + HEADER["methods"][1:]}),
    ("a type that is text", {"methods": [["1", 17, "Present", 4]] + HEADER["methods"][1:]}),
    ("methods that are not rows", {"methods": [5]}),
    ("an argc that is true", {"methods": [[1, 17, "Present", True]] + HEADER["methods"][1:]}),
    ("interfaces that are not names", {"interfaces": [1, 2]}),
    ("rules that are not a list", {"rules": 5}),
    ("entries that are not a list", {"entries": {"1": "x"}}),
]


@pytest.mark.parametrize("name,change", BAD_TABLES, ids=[b[0] for b in BAD_TABLES])
def test_a_header_whose_tables_do_not_read_is_not_a_stream(tmp_path, capsys, name, change):
    """The reader refuses such a header as it refuses a missing magic -- StreamError,
    and exit 1 from the command line -- and does not stop with a traceback."""
    p = tmp_path / "t.sa2s"
    p.write_bytes(make(whole(), header=dict(HEADER, **change)))
    with pytest.raises(SD.StreamError, match="the header's tables do not read"):
        SD.scan(p)
    assert SD.main([str(p)]) == 1
    assert "the header's tables do not read" in capsys.readouterr().err


@pytest.mark.parametrize("rows", [{"rules": [["1", 17, "frame"]]}, {"rules": [[1, 17, 5]]},
                                  {"entries": [[[3], "D3DXFilterTexture", 4, "in 1 1024"]]},
                                  {"entries": [[3, "D3DXFilterTexture", "4", "in 1 1024"]]}])
def test_a_rule_or_entry_row_that_does_not_read_is_a_problem(tmp_path, rows):
    """One row that does not read is a problem of that row, in the content checks and
    in the format check alike; the rest of the stream is still read."""
    head = dict(HEADER, **{k: HEADER[k] + v for k, v in rows.items()})
    s = scan(tmp_path, make(whole(), header=head), doc=SD.doc_table(STREAM_H.read_text()))
    assert sum(1 for p in s["problems"] if "does not read" in p) == 2, s["problems"]
    assert s["records"] == len(whole())


def test_a_meta_that_is_not_json_is_a_problem(tmp_path):
    head = dict(HEADER, meta="{a}")
    assert any("meta is not a JSON object" in p for p in scan(tmp_path, make(whole(), header=head))["problems"])


def test_the_cli_prints_a_range_and_fails_a_bad_stream(tmp_path, capsys):
    p = tmp_path / "s.sa2s"
    p.write_bytes(make(whole()))
    assert SD.main([str(p), "--range", "10:12"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert [json.loads(x)["kind"] for x in lines] == ["call", "frame"]
    p.write_bytes(make(whole()[:-1]))
    assert SD.main([str(p)]) == 1
    p.write_bytes(b"not a stream")
    assert SD.main([str(p)]) == 1
    assert "no SA2D3D9S magic" in capsys.readouterr().err


NO_WATCH = {"every_frames": 64, "set": 0, "checks": 0, "bytes_hashed": 0, "watched_at_exit": 0}


def test_counts_are_held_to_the_recorders(tmp_path):
    s = scan(tmp_path, make(whole()))
    counts = {"methods": [{"type": t, "slot": sl, "calls": 1} for t, sl in
                          ((0, 16), (1, 91), (1, 94), (1, 41), (2, 18), (5, 13), (5, 14), (2, 4),
                           (1, 17))],
              "entries": [{"name": "D3DXCreateTexture", "calls": 1},
                          {"name": "Direct3DCreate9", "calls": 1}],
              "presents": 1, "notes": {"blob-left-out": 1},
              "buffer_watch": dict(NO_WATCH, changed=0)}
    assert SD.compare_counts(s, counts) == []
    counts["methods"][3]["calls"] = 2
    counts["presents"] = 2
    counts["notes"] = {"blob-left-out": 0}
    counts["entries"][0]["calls"] = 2
    counts["entries"].append({"name": "D3DXFilterTexture", "calls": 1})

    counts["buffer_watch"]["set"] = 1
    assert SD.compare_counts(s, counts) == [
        "buffer_watch.set: 1 in the counts file, 0 from the stream's own Unlocks and FRAMEs",
        "method 1.41: 1 records in the stream, 2 calls counted",
        "D3DXCreateTexture: 1 records in the stream, 2 calls counted",
        "D3DXFilterTexture: 0 records in the stream, 1 calls counted",
        "1 FRAME records against 2 Presents counted",
        "1 NOTE records, and the counts file's notes are {'blob-left-out': 0}"]
    del counts["buffer_watch"]
    assert "the counts file has no buffer_watch to hold to the stream's" in SD.compare_counts(s, counts)


def present_frames(n: int, start: int = 0) -> list[tuple[int, bytes]]:
    return [r for k in range(start, start + n) for r in (call(1, 17, DEV, [0, 0, 0, 0]),
                                          frame(16_666 * (k + 1), k + 1, k + 1))]


def watch_stream(release_eax: int = 0) -> bytes:
    """The buffer watch's rules, as the recorder keeps them (host/sa2_present_rec.c,
    watch_set, watch_drop, watch_check), in a stream: an Unlock that closes the
    buffer's last lock sets it; every 64th FRAME, while the recorder's list holds a
    buffer, is one check of the buffers still watched; a Lock, or a Release that
    answers 0, ends a buffer's watch, and the buffer leaves the list at the next
    check."""
    whole_lock = call(7, 11, VB, [0, 0, 0x03004504, 0], items=[(2, 2, w32(VB_BITS))])
    whole_unlock = call(7, 12, VB, [], items=[
        (6, 0xFFFF, buf_lock_info(bits=VB_BITS, nbytes=0x300, offset=0)), (3, 0xFFFF, bytes(0x300))])
    return make(whole()[:10] + buffer_calls()          # set: 0x200 bytes
                + present_frames(64)                    # check 1: the 0x200 bytes
                + [whole_lock] + present_frames(64, 64) # the Lock ends it; check 2 hashes nothing
                + [whole_unlock]                        # set again: 0x300 bytes
                + [call(7, 2, VB, [], eax=release_eax)]  # a final Release (0) ends it
                + present_frames(128, 128) + [END],     # check 3, and a fourth only if watched
                header=BUF_HEADER)


def counts_of(s: dict) -> dict:
    """A counts file that agrees with the stream `s`."""
    return {"methods": [{"type": m["type"], "slot": m["slot"], "calls": m["calls"]}
                        for m in s["methods"]],
            "entries": [{"name": k, "calls": v} for k, v in s["d3dx"].items()],
            "presents": s["frames"], "notes": {},
            "buffer_watch": dict(s["buffer_watch"], changed=0)}


def test_the_reader_recomputes_the_buffer_watch(tmp_path, monkeypatch):
    """stream_dump recomputes the recorder's buffer watch from the stream's own records,
    and compare_counts holds the counts file's to it: a watch that never checked
    (REC_BREAK_NOWATCH's counts) fails, and only the watch break hides it."""
    s = scan(tmp_path, watch_stream())
    assert s["problems"] == [] and s["frames"] == 256
    assert s["buffer_watch"] == {"every_frames": 64, "set": 2, "checks": 3,
                                 "bytes_hashed": 0x200, "watched_at_exit": 0}
    kept = scan(tmp_path, watch_stream(release_eax=1))            # not the final Release
    assert kept["buffer_watch"] == {"every_frames": 64, "set": 2, "checks": 4,
                                    "bytes_hashed": 0x200 + 2 * 0x300, "watched_at_exit": 1}
    counts = counts_of(s)
    assert SD.compare_counts(s, counts) == []
    counts["buffer_watch"].update(checks=0, bytes_hashed=0)
    wrong = ["buffer_watch.checks: 0 in the counts file, 3 from the stream's own Unlocks and FRAMEs",
             "buffer_watch.bytes_hashed: 0 in the counts file, 512 from the stream's own Unlocks "
             "and FRAMEs"]
    assert SD.compare_counts(s, counts) == wrong
    monkeypatch.setenv("SA2_STREAM_DUMP_BREAK", "watch")
    assert SD.compare_counts(s, counts) == []
    # the period is the recorder's
    assert re.search(r"#define REC_WATCH_EVERY (\d+)u", RECORDER.read_text()).group(1) == \
        str(SD.WATCH_EVERY)


def test_a_buffer_lock_reads_clean_and_introduces_only_its_object(tmp_path):
    """The buffer stream the lock malformations are made from holds whole; the Lock's
    data pointer is a value, not an object, and neither is the OUT word of a call that
    failed: a call on either is a problem."""
    s = scan(tmp_path, with_buffer())
    assert s["problems"] == []
    assert s["objects_introduced"] == 8          # D3D9, the device and its two surfaces,
    #                                              the shader, the texture, its level, the buffer
    assert s["buffer_watch"] == dict(NO_WATCH, set=1, watched_at_exit=1)
    w = whole()
    on_data = make(w[:10] + buffer_calls() + [call(7, 2, VB_BITS + 0x100, [])] + w[10:],
                   header=BUF_HEADER)
    assert any(f"this {VB_BITS + 0x100:#010x} is an object nothing earlier" in p
               for p in scan(tmp_path, on_data)["problems"])
    failed = [call(2, 18, TEX, [2, 0x0300410c], eax=0x8876086C, items=[(2, 1, w32(0x03200990))]),
              call(5, 13, 0x03200990, [0x03004110, 0, 0], eax=0x8876086C,
                   items=[(2, 0, struct.pack("<iI", 0, 0))])]
    assert any("this 0x03200990 is an object nothing earlier" in p
               for p in scan(tmp_path, make(w[:10] + failed + w[10:]))["problems"])


def test_the_reader_names_what_the_header_names():
    """stream_dump's constants are host/sa2_stream.h's: one format, one copy of each number."""
    text = STREAM_H.read_text()
    enum = dict((n, int(v)) for n, v in re.findall(r"\b(SA2S_[A-Z_0-9]+)\s*=\s*(\d+)", text))
    assert (enum["SA2S_CALL"], enum["SA2S_D3DX"], enum["SA2S_FRAME"], enum["SA2S_NOTE"],
            enum["SA2S_END"], enum["SA2S_AUDIO"]) == (SD.CALL, SD.D3DX, SD.FRAME, SD.NOTE, SD.END,
                                                      SD.AUDIO)
    assert {enum[k] for k in ("SA2S_CALL", "SA2S_D3DX", "SA2S_FRAME", "SA2S_NOTE", "SA2S_END",
                              "SA2S_AUDIO")} == set(SD.KIND_NAMES)
    roles = {k: v for k, v in enum.items() if k.startswith("SA2S_BLOB_")}
    assert {k[len("SA2S_BLOB_"):].lower(): v for k, v in roles.items()} == \
        {v: k for k, v in SD.ROLE_NAMES.items()}
    entries = {v for k, v in enum.items() if k.startswith("SA2S_D3DX_")}
    assert entries == set(SD.D3DX_NAMES) and enum["SA2S_D3DX_DIRECT3D_CREATE9"] == SD.DIRECT3D_CREATE9
    assert f'"{SD.MAGIC.decode()}"' in text and f'"{SD.SCHEMA}"' in text
    assert re.search(r"SA2S_VERSION\s+(\d+)u", text).group(1) == str(SD.VERSION)
    assert f'"{SD.SCHEMA_AUDIO}"' in text
    assert re.search(r"SA2S_VERSION_AUDIO\s+(\d+)u", text).group(1) == str(SD.VERSION_AUDIO)
    ops = {k[len("SA2S_AU_"):]: v for k, v in enum.items()
           if k.startswith(("SA2S_AU_DS_", "SA2S_AU_XA_"))}
    assert {op: name for op, (name, *_rest) in SD.doc_table(text)[2].items()} == \
        {v: k for k, v in ops.items()} and enum["SA2S_AU_OP_COUNT"] == len(ops) + 1
    assert (enum["SA2S_AU_DSOUND"], enum["SA2S_AU_XAUDIO2"]) == (1, 2) == tuple(SD.AUDIO_APIS)


def test_an_op_list_reads_and_writes_back():
    text = "out 2 1100; in_vec 1 2 16; new_cube 5 0 3; frame"
    assert SD.ops_text(SD.parse_ops(text)) == text and SD.parse_ops("-") == []
    for bad in ("out 2", "frob 1", "in 1 2 3"):
        with pytest.raises(ValueError):
            SD.parse_ops(bad)


DOC = """ *   rule IDirect3DDevice9::Present   pre_rect 0; frame   # a comment
 *   rule *::QueryInterface          in 0 16; out 1 4
 *   entry 12 Direct3DCreate9 0      object_eax
"""
TABLE_HEAD = {"interfaces": ["IDirect3D9", "IDirect3DDevice9", "IDirect3DTexture9"],
              "methods": [[1, 17, "Present", 4], [1, 0, "QueryInterface", 2],
                          [2, 0, "QueryInterface", 2], [1, 43, "Clear", 6]],
              "rules": [[1, 17, "pre_rect 0; frame"], [1, 0, "in 0 16; out 1 4"],
                        [2, 0, "in 0 16; out 1 4"]],
              "entries": [[12, "Direct3DCreate9", 0, "object_eax"]]}


@pytest.mark.parametrize("change,needle", [
    (lambda h, d: h["rules"].__setitem__(0, [1, 17, "pre_rect 1; frame"]),
     "IDirect3DDevice9::Present: the header's rule is 'pre_rect 1; frame', the format's line "
     "'pre_rect 0; frame'"),
    (lambda h, d: h["rules"].append([1, 43, "in_vec 1 0 16"]),
     "the header's rule for IDirect3DDevice9::Clear is no line of the format"),
    (lambda h, d: d.append(" *   rule IDirect3DDevice9::Clear  in_vec 1 0 16"),
     "the format's line for IDirect3DDevice9::Clear is no method's rule in the header"),
    (lambda h, d: h["entries"].__setitem__(0, [12, "Direct3DCreate9", 1, "object_eax"]),
     "entry 12: the header says ('Direct3DCreate9', 1, 'object_eax')"),
    (lambda h, d: (h["rules"].__setitem__(0, [1, 17, "pre_rect 4; frame"]),
                   d.__setitem__(0, " *   rule IDirect3DDevice9::Present  pre_rect 4; frame")),
     "IDirect3DDevice9::Present: pre_rect {'a': 4} names an argument past its 4"),
])
def test_the_header_table_is_held_to_the_format_lines(tmp_path, change, needle):
    head, doc = json.loads(json.dumps(TABLE_HEAD)), DOC.splitlines()
    assert SD.table_problems(head, SD.doc_table("\n".join(doc))) == []
    change(head, doc)
    bad = SD.table_problems(head, SD.doc_table("\n".join(doc)))
    assert any(needle in p for p in bad), bad
    # scan holds a stream's header to the format given, and the table break hides it
    stream = make([END], header=dict(HEADER, **head))
    assert any(needle in p for p in scan(tmp_path, stream, doc=SD.doc_table("\n".join(doc)))["problems"])
    assert scan(tmp_path, stream, "table", doc=SD.doc_table("\n".join(doc)))["problems"] == []
    with pytest.raises(ValueError, match="two rule lines"):
        SD.doc_table(DOC + DOC.splitlines()[0])


# The device-log walk: the device's rows in the order the calls started, against the
# stream's records (head_calls) in the order they finished.
def log_row(seq, slot, name, args=(), *, this=DEV, ret=0, iface="IDirect3DDevice9"):
    return {"seq": seq, "interface": iface, "method": name, "slot": slot, "this": f"{this:#010x}",
            "args": [f"{a:#010x}" for a in args], "ret": f"{ret:#010x}"}


def head_call(seq, slot, args=(), *, thread=0, this=DEV, eax=0, t=1):
    return ("call", seq, t, slot, this, tuple(args), eax, thread)


def walk(head_calls, log):
    return SD.walk_device_log({"interfaces": ["IDirect3D9", "IDirect3DDevice9"],
                               "head_calls": head_calls}, log)


def test_the_device_log_walk_allows_a_call_finished_inside_a_frame_wait():
    """The device logs calls as they start; the stream as they finish. A call another
    thread makes inside BeginScene is in the stream before it, and so is a D3DX call,
    which carries no thread."""
    head = [("d3dx", 0, 12, None, None, (), D3D9, None), head_call(1, 109, (5, 7, 1), thread=1),
            ("d3dx", 2, 3, None, None, (TEX, 0, 0, 0), 0, None),
            head_call(3, 41), head_call(4, 42)]
    log = [log_row(1, 41, "BeginScene"), log_row(2, 109, "SetPixelShaderConstantF", (5, 7, 1)),
           log_row(3, 249, "D3DXFilterTexture", (TEX, 0, 0, 0)), log_row(4, 42, "EndScene")]
    assert walk(head, log) == []
    log[1] = log_row(2, 109, "SetPixelShaderConstantF", (5, 8, 1))
    assert "is not the stream's next record" in walk(head, log)[0]


@pytest.mark.parametrize("name,head,log,needle", [
    ("the object called",
     [head_call(0, 65, (0, TEX))], [log_row(1, 65, "SetTexture", (0, TEX), this=UNKNOWN)],
     "is not the stream's next record (from seq 0)"),
    ("the answer",
     [head_call(0, 65, (0, TEX))], [log_row(1, 65, "SetTexture", (0, TEX), ret=0x8876086C)],
     "is not the stream's next record (from seq 0)"),
    ("a short stream",
     [head_call(0, 65, (0, TEX))], [log_row(1, 65, "SetTexture", (0, TEX))] * 2,
     "the device logged 2 calls, and the stream holds 1"),
    ("two calls of one thread in the other order",
     [head_call(0, 65, (0, SURF)), head_call(1, 65, (0, TEX))],
     [log_row(1, 65, "SetTexture", (0, TEX)), log_row(2, 65, "SetTexture", (0, SURF))],
     "SetTexture) is not the stream's next record (from seq 0)"),
    ("a frame wait passing its own thread's call",
     [head_call(0, 65, (0, TEX)), head_call(1, 41)],
     [log_row(1, 41, "BeginScene"), log_row(2, 65, "SetTexture", (0, TEX))],
     "the stream records seq 0 before seq 1 (IDirect3DDevice9::BeginScene), both on guest thread 0"),
    ("a frame wait past its window",
     [head_call(k, 65, (0, TEX), thread=1) for k in range(16)] + [head_call(16, 41)],
     [log_row(1, 41, "BeginScene")] + [log_row(2 + k, 65, "SetTexture", (0, TEX)) for k in range(16)],
     "BeginScene) is none of the stream's next 16 records (from seq 0)"),
])
def test_the_device_log_walk_holds_each_row(name, head, log, needle):
    bad = walk(head, log)
    assert len(bad) == 1 and needle in bad[0], (name, bad)


def test_the_device_log_walk_meets_a_frame_wait_inside_its_window():
    head = [head_call(k, 65, (0, TEX), thread=1) for k in range(15)] + [head_call(15, 41)]
    log = [log_row(1, 41, "BeginScene")] + [log_row(2 + k, 65, "SetTexture", (0, TEX)) for k in range(15)]
    assert walk(head, log) == []


PROV = SD.PROVENANCE_NAMES


def load_run(**over) -> tuple[dict, dict]:
    """A stream summary with one DDS load, and a run record whose device row is it."""
    desc = [LOADED, 1, 0, 64, 32, 1, 7, DXT5, 64, 32, 1, 1, DXT5, 1, 1, 1, 0, 0, 0, 0, 2048, 1024]
    data = b"a dds"
    f = {"seq": 3, "entry": 1, "texture": LOADED, "desc": desc, "bytes": len(data),
         "sha256": hashlib.sha256(data).hexdigest()}
    row = {"texture": f"{LOADED:#010x}", "bytes": len(data), "sha256": f["sha256"],
           "container": "dds", "file": {"w": 64, "h": 32, "depth": 1, "levels": 1, "format": DXT5},
           "png": {"bit_depth": 0, "colour_type": 0, "interlace": 0, "trns": 0},
           "created": {"w": 64, "h": 32, "levels": 7, "format": DXT5, "level0_bytes": 1024,
                       "chain_bytes": 2048},
           "dimensions_from": PROV[1], "format_from": PROV[1], "levels_from": PROV[1]}
    summary = {"files": [f], "head_calls": [], "interfaces": [],
               "methods": [{"interface": "IDirect3DDevice9", "slot": 41, "calls": 1}],
               "distinct_methods": 1, "method_calls": 1, "d3dx_calls": 1,
               "d3dx": {"D3DXCreateTextureFromFileInMemoryEx": 1},
               "frames": 0, "objects_introduced": 2,
               "entries_table": {1: ["D3DXCreateTextureFromFileInMemoryEx", 15]}}
    run = {"d3d9": {"calls": [],
                   "dynamic_surface": [{"interface": "IDirect3DDevice9", "slot": 41, "calls": 1}],
                   "distinct_methods": 1, "method_calls": 2, "frames": {"present": 0},
                   "objects_built": 2,
                   "textures_from_files": {"rows": [row], "source_bytes_hashed": len(data)}}}
    for k, v in over.items():
        v(f, row, run)
    return summary, run


@pytest.mark.parametrize("name,change,needle", [
    ("the container", lambda f, r, run: r.update(container="png"), "the DESC of 0x03200080"),
    ("the kind its entry makes", lambda f, r, run: f.update(entry=6), "the DESC of 0x03200080"),
    ("a 2D texture's depth", lambda f, r, run: f["desc"].__setitem__(5, 2), "the DESC of 0x03200080"),
    ("a PNG word", lambda f, r, run: r["png"].update(colour_type=2), "the DESC of 0x03200080"),
    ("a provenance", lambda f, r, run: r.update(levels_from=PROV[3]), "the DESC of 0x03200080"),
    ("the created levels", lambda f, r, run: r["created"].update(levels=6), "the DESC of 0x03200080"),
    ("the file's format", lambda f, r, run: r["file"].update(format=DXT1), "the DESC of 0x03200080"),
    ("the chain's bytes", lambda f, r, run: r["created"].update(chain_bytes=2047),
     "the DESC of 0x03200080"),
    ("the file's bytes", lambda f, r, run: r.update(sha256="0" * 64), "the device hashed 5 bytes, 000000000000"),
    ("a texture the device has no row for", lambda f, r, run: r.update(texture=f"{TEX:#010x}"),
     "texture 0x03200080 has no row"),
    ("the load count", lambda f, r, run: run["d3d9"]["textures_from_files"]["rows"].append(
        dict(r, texture=f"{TEX:#010x}")), "1 loads carry a FILE blob and a description; the device answered 2"),
    ("the FILE total", lambda f, r, run: run["d3d9"]["textures_from_files"].update(source_bytes_hashed=6),
     "the FILE blobs hold 5 bytes; the device hashed 6"),
])
def test_a_stream_is_held_to_the_run_record(name, change, needle):
    summary, run = load_run()
    assert SD.hold_to_run(summary, run, [{"name": "D3DXCreateTextureFromFileInMemoryEx", "argc": 15, "calls": 1}]) == []
    summary, run = load_run(x=change)
    bad = SD.hold_to_run(summary, run)
    assert len(bad) == 1 and needle in bad[0], (name, bad)


def test_each_entry_argc_is_the_import_tables():
    summary, run = load_run()
    assert SD.hold_to_run(summary, run, [{"name": "D3DXCreateTextureFromFileInMemoryEx", "argc": 14, "calls": 1}]) == [
        "entry 1 D3DXCreateTextureFromFileInMemoryEx: 15 arguments in the header; the import "
        "table says 14"]
    assert SD.hold_to_run(summary, run, []) == [
        "entry 1 D3DXCreateTextureFromFileInMemoryEx: 15 arguments in the header; the import "
        "table says nothing",
        "D3DXCreateTextureFromFileInMemoryEx: 1 records in the stream, 0 calls in the run's import table"]


# ---------------------------------------------------------------- the recorder

@pytest.fixture(scope="module")
def build(tmp_path_factory):
    """A function (variant) -> the harness binary for it: "control" (no recorder, no
    wraps), "rec", REC_HARNESS_OUTFLAG (the device's flags wrapped: SetRenderState
    flagged "out"), or a REC_BREAK_* name."""
    need_zig()
    d = tmp_path_factory.mktemp("rec-harness")
    env = zig_env(d / "zc")
    # A unit build: the device's closed world (runtime/sa2_d3d9.c) would trap the methods off the M7 list this probe drives, so it is compiled open (its edge scenario calls IDirect3DVertexDeclaration9::GetDeclaration).
    base = [str(ZIG), "cc", "-target", "x86_64-linux-musl", "-O1", "-Wall", "-Wextra", "-Werror",
            "-DSA2_D3D9_OPEN_WORLD_UNIT_BUILD",
            "-I", str(ROOT / "runtime"), "-I", str(ROOT / "host")]
    made: dict[str, Path] = {}

    def get(variant: str) -> Path:
        if variant in made:
            return made[variant]
        exe = d / f"rec_harness.{variant}"
        cmd = base + [str(HARNESS), str(D3D9_C), str(ROOT / "runtime/sa2_guest_call.c")]
        if variant != "control":
            cmd += [str(RECORDER), str(audio_getters(d))] + [f"-Wl,-wrap={w}" for w in WRAPS]
            if variant == "REC_HARNESS_OUTFLAG":
                cmd += [f"-D{variant}", "-Wl,-wrap=sa2_d3d9_method_flags"]
            elif variant != "rec":
                cmd.append(f"-D{variant}")
        done = subprocess.run(cmd + ["-o", str(exe)], capture_output=True, text=True, env=env)
        assert done.returncode == 0, done.stderr
        made[variant] = exe
        return exe
    return get


def harness(exe: Path, tmp_path: Path, name: str, *, trace: bool = True, scenario: str = "main",
            env_extra: dict | None = None, trace_path: Path | None = None) -> dict:
    """One run: what the guest saw, and where the stream and counts went."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("SA2_REC_", "SA2_D3D9", "SA2_D3DX", "SA2_STREAM_"))}
    tr = trace_path or tmp_path / f"{name}.sa2s"
    counts = tmp_path / f"{name}.counts.json"
    if trace:
        env.update(SA2_REC_TRACE=str(tr), SA2_REC_COUNTS=str(counts),
                   SA2_REC_META='{"build": "rec_harness"}')
    env.update(env_extra or {})
    out = tmp_path / f"{name}.json"
    done = subprocess.run([str(exe), "--out", str(out), "--scenario", scenario],
                          capture_output=True, text=True, env=env, timeout=120)
    assert done.returncode == 0, done.stderr
    seen = json.loads(out.read_text())
    seen["trace"], seen["counts"] = tr, counts
    return seen


def guest_view(seen: dict) -> tuple:
    """What the guest can see: every answer (EAX, ESP, and a digest of the harness's
    40-byte stand-in for the register file after the call), the function table's
    registrations, all of guest memory, the device's counters, its objects and its
    locks."""
    return (seen["answers"], seen["function_table"], seen["guest_fnv"], seen["stats"],
            seen["objects"], seen["locks"])


def guest_parts(seen: dict) -> dict:
    """guest_view by part, so a test can say which part a change moved."""
    return {"eax_and_esp": [a[:2] for a in seen["answers"]],
            "register_file": [a[2] for a in seen["answers"]],
            "function_table": seen["function_table"], "guest_memory": seen["guest_fnv"],
            "device": [seen["stats"], seen["objects"], seen["locks"]]}


@pytest.mark.parametrize("scenario,variant", [("main", "rec"), ("edge", "rec"), ("notes", "rec"),
                                              ("notes", "REC_HARNESS_OUTFLAG")])
def test_the_guest_sees_the_same_with_the_recorder(build, tmp_path, scenario, variant):
    control = harness(build("control"), tmp_path, "control", trace=False, scenario=scenario)
    on = harness(build(variant), tmp_path, "on", scenario=scenario)
    assert on["trace"].is_file()
    assert guest_view(on) == guest_view(control)
    assert len(control["answers"]) == {"main": 25, "edge": 32, "notes": 8}[scenario]
    assert control["function_table"][0] > 200


def test_a_recorder_that_writes_the_guest_is_caught(build, tmp_path):
    control = harness(build("control"), tmp_path, "control", trace=False)
    poked = harness(build("REC_BREAK_POKE"), tmp_path, "poke")
    assert [a[0] for a in poked["answers"]] == [a[0] for a in control["answers"]]
    assert poked["guest_fnv"] != control["guest_fnv"]


@pytest.mark.parametrize("brk,part", [("REC_BREAK_REGSTORE", "register_file"),
                                      ("REC_BREAK_FTKIND", "function_table"),
                                      ("REC_BREAK_FTNAME", "function_table")])
def test_a_recorder_that_moves_the_register_file_or_a_registration_is_caught(
        build, production_object, tmp_path, brk, part):
    """A store through the register file rec_thunk is handed, and a thunk registered
    with another kind or name: the object check passes all three, and the harness's
    comparison of that one part is what differs from the build without a recorder."""
    control = guest_parts(harness(build("control"), tmp_path, "control", trace=False))
    broken = guest_parts(harness(build(brk), tmp_path, brk))
    assert [k for k in control if broken[k] != control[k]] == [part]
    assert OC.check(production_object(brk))["ok"] is True


def records(path: Path) -> list[dict]:
    s = SD.scan(path, want_range=(0, 1 << 30))
    assert s["problems"] == [], s["problems"]
    return s["range"]


def blob(path: Path, seq: int, role: int, arg: int = NO_ARG) -> bytes:
    got = [d for r, a, d in SD.blobs_of(path, seq) if r == role and a == arg]
    assert len(got) == 1, (seq, role, arg, SD.blobs_of(path, seq))
    return got[0]


def test_the_stream_holds_every_call_as_the_guest_made_it(build, tmp_path):
    on = harness(build("rec"), tmp_path, "on")
    tr, b = on["trace"], on["bytes"]
    recs = records(tr)
    calls = [(r["kind"], r.get("call") or r.get("entry")) for r in recs]
    assert calls == [
        ("d3dx", "Direct3DCreate9"),
        ("call", "IDirect3D9::CreateDevice"), ("call", "IDirect3DDevice9::CreateVertexShader"),
        ("call", "IDirect3DDevice9::CreateVertexDeclaration"),
        ("call", "IDirect3DDevice9::SetVertexDeclaration"),
        ("call", "IDirect3DDevice9::SetVertexShader"),
        ("call", "IDirect3DDevice9::SetVertexShaderConstantF"),
        ("call", "IDirect3DDevice9::SetViewport"), ("call", "IDirect3DDevice9::SetScissorRect"),
        ("call", "IDirect3DDevice9::SetPixelShaderConstantF"),      # inside BeginScene's hand-off
        ("call", "IDirect3DDevice9::BeginScene"), ("call", "IDirect3DDevice9::Clear"),
        ("call", "IDirect3DDevice9::DrawPrimitiveUP"), ("call", "IDirect3DDevice9::EndScene"),
        ("call", "IDirect3DDevice9::Present"), ("frame", None),
        ("d3dx", "D3DXCreateTexture"), ("call", "IDirect3DTexture9::GetSurfaceLevel"),
        ("call", "IDirect3DSurface9::LockRect"), ("call", "IDirect3DSurface9::UnlockRect"),
        ("call", "IDirect3DTexture9::GetSurfaceLevel"), ("call", "IDirect3DSurface9::LockRect"),
        ("call", "IDirect3DSurface9::UnlockRect"), ("d3dx", "D3DXFilterTexture"),
        ("d3dx", "D3DXCreateTextureFromFileInMemoryEx"), ("call", "IDirect3DTexture9::Release"),
        ("call", "IDirect3DVertexShader9::Release"), ("end", None)]
    assert recs[0]["eax"] == f"{on['objects']['d3d9']:#010x}" and recs[0]["args"] == []
    # CreateDevice: the parameters, the device, and the two objects made inside it.
    assert blob(tr, 1, 1, 4).hex() == b["pp"]
    assert struct.unpack("<3I", blob(tr, 1, 5)) == (0x03200010, 0x03200020, 0x03200030)
    assert blob(tr, 1, 2, 5) == struct.pack("<I", 0x03200010)
    # The shader whole, past a comment and a literal that hold 0x0000FFFF.
    assert blob(tr, 2, 1, 0).hex() == b["shader"] and len(b["shader"]) == 2 * 56
    assert blob(tr, 3, 1, 0).hex() == b["decl"]
    assert blob(tr, 6, 1, 1).hex() == b["consts"]
    assert blob(tr, 7, 1, 0).hex() == b["viewport"] and blob(tr, 8, 1, 0).hex() == b["scissor"]
    # The call another thread made inside BeginScene comes first, whole, on its thread;
    # BeginScene after it, whole, on its own.
    assert (recs[9]["thread"], recs[9]["args"]) == (1, ["0x00000005", "0x03007000", "0x00000001"])
    assert blob(tr, 9, 1, 1).hex() == b["nested"]
    assert (recs[10]["thread"], recs[10]["args"], recs[10]["blobs"]) == (0, [], [])
    assert blob(tr, 11, 1, 1).hex() == b["rects"] and recs[11]["args"][4] == "0x3f800000"
    assert blob(tr, 12, 1, 2).hex() == b["verts"]          # a fan of 2 is 4 vertices of 24
    assert recs[15] == {"seq": 15, "kind": "frame", "offset": recs[15]["offset"], "bytes": 28,
                        "virtual_us": 5_000_016_666, "frames": 1, "polls": 77}
    # Level 0, whole: 16 block rows of 128 bytes; level 1 by rectangle rows 8..24.
    lock0 = struct.unpack("<11I4iII", blob(tr, 19, 6))
    assert lock0 == (0x03200070, 0x03200060, 0, on["locks"][0][1], 128, 16, 2048, DXT1,
                     64, 64, 0, 0, 0, 64, 64, 0, 0)
    assert blob(tr, 19, 3).hex() == b["level0"]
    lock1 = struct.unpack("<11I4iII", blob(tr, 22, 6))
    assert lock1 == (0x03200080, 0x03200060, 1, on["locks"][1][1], 64, 4, 256, DXT1,
                     32, 32, 1, 0, 8, 32, 24, 0, 0)
    assert blob(tr, 22, 3).hex() == b["level1"]
    # The DDS: the file whole, and the recording device's own account of it.
    assert blob(tr, 24, 4, 1).hex() == b["dds"]
    desc = struct.unpack("<22I", blob(tr, 24, 5))
    assert desc[:13] == (0x03200090, 1, 0, 64, 32, 1, 7, 0x35545844, 64, 32, 1, 1, 0x35545844)
    assert blob(tr, 24, 2, 14) == struct.pack("<I", 0x03200090)
    assert recs[-1]["kind"] == "end"


def test_the_paths_the_boot_does_not_take(build, tmp_path):
    """The edge scenario: shader model 1 walks, bytecode and elements handed back, two
    texture levels locked at once, a buffer's nested locks, a dirty region kept and one
    left out, the entry points the boot never calls, an indexed draw from memory, a
    swap chain's Present, and a Present whose rectangle and region another thread
    rewrites inside its wait."""
    on = harness(build("rec"), tmp_path, "edge", scenario="edge")
    tr, b = on["trace"], on["bytes"]
    s = SD.scan(tr, want_range=(0, 1 << 30))
    recs = s["range"]
    kinds = [(r["kind"], r.get("call") or r.get("entry")) for r in recs]
    at = {k: i for i, k in reversed(list(enumerate(kinds)))}         # the first of each
    # shader model 1: `def` literals that look like a comment token and like the end
    assert blob(tr, 2, 1, 0).hex() == b["ps1a"] and blob(tr, 3, 1, 0).hex() == b["ps1b"]
    # GetFunction: the size alone, then the size and the bytes; GetDeclaration likewise
    assert [r for r, _a, _d in SD.blobs_of(tr, 5)] == [2]
    assert blob(tr, 6, 2, 0).hex() == b["function"] == b["vs3"]
    assert blob(tr, 9, 2, 0).hex() == b["elements"]
    # two levels of one texture locked at once: both uploads kept
    assert blob(tr, 13, 3).hex() == b["level0"] and blob(tr, 14, 3).hex() == b["level1"]
    assert struct.unpack("<11I4iII", blob(tr, 14, 6))[2] == 1
    # a buffer's nested locks: the inner one (64, rest) closes first
    vb = bytes.fromhex(b["vb"])
    assert blob(tr, 18, 3) == vb[64:] and blob(tr, 19, 3) == vb[:64]
    # the dirty regions: two rectangles kept whole; twenty left out, and noted after
    # the Present's FRAME
    assert blob(tr, 20, 1, 3).hex() == b["rgn2"] and kinds[21] == ("frame", None)
    assert kinds[22:25] == [("call", "IDirect3DDevice9::Present"), ("frame", None), ("note", None)]
    assert "20 rectangles" in recs[24]["text"]
    assert s["problems"] == ["record seq 22 (IDirect3DDevice9::Present): no in blob of arg 3, "
                             "which its rule gives"]
    # the entry points the boot never calls
    for entry in ("D3DXCreateCubeTexture", "D3DXCreateVolumeTexture", "D3DXSaveTextureToFileA",
                  "D3DXSaveSurfaceToFileA", "Direct3DCreate9"):
        assert ("d3dx", entry) in at, entry
    cube = at[("d3dx", "D3DXCreateCubeTextureFromFileInMemoryEx")]
    vol = at[("d3dx", "D3DXCreateVolumeTextureFromFileInMemoryEx")]
    assert blob(tr, cube, 4, 1).hex() == b["dds_cube"] and blob(tr, vol, 4, 1).hex() == b["dds_volume"]
    assert struct.unpack("<22I", blob(tr, cube, 5))[:3] == (on["objects"]["cube_file"], 1, 1)
    assert struct.unpack("<22I", blob(tr, vol, 5))[:6] == (on["objects"]["volume_file"], 1, 2, 4, 4, 2)
    # DrawIndexedPrimitiveUP: 6 indices of 2 bytes, and vertices 0 .. 2 + 4 of 12
    iup = at[("call", "IDirect3DDevice9::DrawIndexedPrimitiveUP")]
    assert blob(tr, iup, 1, 4).hex() == b["iup_indices"] and len(b["iup_indices"]) == 2 * 12
    assert blob(tr, iup, 1, 6).hex() == b["iup_vertices"] and len(b["iup_vertices"]) == 2 * 72
    # a swap chain's Present has its FRAME
    swap = at[("call", "IDirect3DSwapChain9::Present")]
    assert kinds[swap + 1] == ("frame", None) and recs[swap]["this"] == f"{on['objects']['swap_chain']:#010x}"
    # the last Present: its rectangle and region as they were when it was called, not
    # as the other thread left them inside the wait
    last = max(i for i, k in enumerate(kinds) if k == ("call", "IDirect3DDevice9::Present"))
    assert blob(tr, last, 1, 0) == struct.pack("<4I", 0, 0, 64, 64)
    assert b["srcrect_after"] == struct.pack("<4I", 9, 9, 9, 9).hex()
    assert blob(tr, last, 1, 3).hex() == b["rgn2"] != b["rgn2b_after"]
    counts = json.loads(on["counts"].read_text())
    assert counts["notes"]["blob-left-out"] == 1 and sum(counts["notes"].values()) == 1
    assert counts["presents"] == 4 and s["frames"] == 4
    assert SD.compare_counts(s, counts) == []


def test_the_recorder_notes_what_it_leaves_out(build, tmp_path):
    """The notes scenario: each loss is a NOTE after its call's record and a count of
    its class, and the reader finds the loss in the records themselves."""
    for variant, norule in (("rec", 0), ("REC_HARNESS_OUTFLAG", 1)):
        on = harness(build(variant), tmp_path, variant, scenario="notes")
        s = SD.scan(on["trace"], want_range=(0, 1 << 30))
        texts = [r["text"] for r in s["range"] if r["kind"] == "note"]
        lock = on["objects"]["level0"]
        assert texts[:4] == [
            f"record 4: blob-left-out: a LockRect on {lock:#010x} names a rectangle at 0x05000000 "
            f"that does not read, so the lock's rows are unknown",
            "record 4: blob-left-out: the 16 bytes at 0x05000000 (role 1, arg 1) did not read",
            f"record 7: lock: an Unlock on {lock:#010x} whose lock the recorder could not size",
            f"record 9: lock: an Unlock on {lock:#010x} (level 0) with no lock open there"]
        assert texts[4:] == ([] if not norule else [
            "record 11: out-without-rule: IDirect3DDevice9::SetRenderState writes through a "
            "pointer and has no rule here, so its out-parameters are not in the stream"])
        counts = json.loads(on["counts"].read_text())
        assert {k: v for k, v in counts["notes"].items() if v} == dict(
            {"blob-left-out": 2, "lock": 2}, **({"out-without-rule": 1} if norule else {}))
        assert s["problems"] == [
            "record seq 4 (IDirect3DSurface9::LockRect): no in blob of arg 1, which its rule gives",
            "record seq 7 (IDirect3DSurface9::UnlockRect): no locked blob of the call, which its rule gives",
            "record seq 7 (IDirect3DSurface9::UnlockRect): its LOCK: 0 bytes where the level's rows "
            "at the Lock's pitch are 16384"]
        assert SD.compare_counts(s, counts) == []


def test_the_state_note_follows_the_first_frame_and_leaves_the_rest_alone(build, tmp_path):
    """SA2_REC_STATE=1 (live play's): the stream is the one without it with one state note
    after its first FRAME (the harness's guest holds none of the five values, so each reads -1,
    and none changes after), every record after it one seq on; the guest sees the same; the
    note is no note class and no NOTE the reader counts, and the counts file has it as
    stream_records' "state". Unset, the counts file has no "state" at all."""
    off = harness(build("rec"), tmp_path, "off")
    on = harness(build("rec"), tmp_path, "on", env_extra={"SA2_REC_STATE": "1"})
    assert guest_view(on) == guest_view(off)
    a, b = SD.scan(off["trace"], want_range=(0, 1 << 30)), SD.scan(on["trace"], want_range=(0, 1 << 30))
    first_frame = next(r["seq"] for r in a["range"] if r["kind"] == "frame")
    states = [r for r in b["range"] if r["kind"] == "note" and r["text"].startswith("state ")]
    assert [(r["seq"], r["text"]) for r in states] == [
        (first_frame + 1, "state mode=-1 level=-1 character=-1 two=-1 alt=-1")]
    strip = lambda r: {k: v for k, v in r.items() if k not in ("seq", "offset")}   # both move by the note
    assert [strip(r) for r in b["range"] if r not in states] == [strip(r) for r in a["range"]]
    assert [r["seq"] for r in b["range"]] == list(range(len(b["range"])))
    assert (b["state_records"], a["state_records"], b["note_records"]) == (1, 0, a["note_records"])
    assert b["problems"] == a["problems"]
    c_on, c_off = json.loads(on["counts"].read_text()), json.loads(off["counts"].read_text())
    assert c_on["stream_records"]["state"] == 1 and "state" not in c_off["stream_records"]
    assert c_on["notes"] == c_off["notes"]
    assert c_on["stream_records"]["note"] == c_off["stream_records"]["note"]
    assert SD.compare_counts(b, c_on) == []


def unlocked_levels(trace: Path) -> list[int]:
    """The level each texture UnlockRect's LOCK names, in order."""
    recs = SD.scan(trace, want_range=(0, 1 << 30))["range"]
    return [struct.unpack("<11I4iII", d)[2] for r in recs
            if r.get("call") == "IDirect3DTexture9::UnlockRect"
            for role, _a, d in SD.blobs_of(trace, r["seq"]) if role == 6]


def test_one_lock_per_object_loses_a_level(build, tmp_path):
    """REC_BREAK_ONELOCK keeps one open lock per object, as the delivered recorder did:
    level 0's lock is replaced by level 1's and its bytes never reach the stream."""
    assert unlocked_levels(harness(build("rec"), tmp_path, "twolock", scenario="edge")["trace"]) == [0, 1]
    broken = harness(build("REC_BREAK_ONELOCK"), tmp_path, "onelock", scenario="edge")
    assert unlocked_levels(broken["trace"]) == [1]


def test_the_file_holds_the_frame_when_present_returns(build, tmp_path):
    for variant, flushed in (("rec", True), ("REC_BREAK_NOFLUSH", False)):
        on = harness(build(variant), tmp_path, variant)
        recs = SD.scan(on["trace"], want_range=(0, 64))["range"]
        after_frame = recs[16]["offset"]                    # where the record after FRAME starts
        assert recs[15]["kind"] == "frame"
        assert (on["after_present"] == after_frame) is flushed, (variant, on["after_present"])


def test_per_call_state_survives_a_call_inside_the_call(build, tmp_path):
    broken = harness(build("REC_BREAK_GLOBAL"), tmp_path, "global")
    recs = SD.scan(broken["trace"], want_range=(0, 64))["range"]
    begin = [r for r in recs if r.get("call") == "IDirect3DDevice9::BeginScene"]
    assert begin == [] or begin[0]["thread"] != 0 or begin[0]["args"] != []


def test_a_walk_that_stops_at_the_first_end_value_is_caught(build, tmp_path):
    on = harness(build("REC_BREAK_WALK"), tmp_path, "walk")
    assert len(blob(on["trace"], 2, 1, 0)) != 56
    assert any("end token" in p for p in SD.scan(on["trace"])["problems"])
    edge = harness(build("REC_BREAK_WALK"), tmp_path, "walk-edge", scenario="edge")
    assert len(blob(edge["trace"], 3, 1, 0)) != 44


def test_unpadded_blobs_break_the_framing(build, tmp_path):
    on = harness(build("REC_BREAK_NOPAD"), tmp_path, "nopad")
    assert SD.scan(on["trace"])["problems"] != []


def test_the_counts_file_agrees_with_the_stream(build, tmp_path):
    on = harness(build("rec"), tmp_path, "on")
    counts = json.loads(on["counts"].read_text())
    s = SD.scan(on["trace"])
    assert s["problems"] == [] and SD.compare_counts(s, counts) == []
    assert (counts["method_calls"], counts["distinct_methods"], counts["presents"],
            counts["d3dx_calls"], counts["direct3d_create9"],
            counts["method_and_d3dx_calls"]) == (22, 19, 1, 3, 1, 25)
    assert {r["name"]: r["calls"] for r in counts["entries"] if r["calls"]} == {
        "D3DXCreateTextureFromFileInMemoryEx": 1, "D3DXCreateTexture": 1, "D3DXFilterTexture": 1,
        "Direct3DCreate9": 1}
    assert len(counts["entries"]) == 12
    assert all(r["calls"] == r["entered"] for r in counts["methods"] + counts["entries"])
    assert counts["why"] == "" and counts["stream"] == "closed" and not any(counts["notes"].values())
    assert Path(f"{on['trace']}.status").read_text().startswith("closed: 28 records")


def test_counting_alone_writes_no_stream(build, tmp_path):
    counts = tmp_path / "only.counts.json"
    seen = harness(build("rec"), tmp_path, "only", trace=False,
                   env_extra={"SA2_REC_COUNTS": str(counts)})
    assert not seen["trace"].exists()
    c = json.loads(counts.read_text())
    assert c["stream"] is None and c["method_calls"] == 22 and c["presents"] == 1


def test_the_recorder_stands_down_under_a_break_and_on_a_bad_path(build, tmp_path):
    control = harness(build("control"), tmp_path, "control", trace=False)
    brk = harness(build("rec"), tmp_path, "brk", env_extra={"SA2_D3D9_BREAK": "tablekeep"})
    assert not brk["trace"].exists()
    assert Path(f"{brk['trace']}.status").read_text().startswith("off: SA2_D3D9_BREAK=tablekeep")
    a_dir = tmp_path / "a_dir.sa2s"
    a_dir.mkdir()
    bad = harness(build("rec"), tmp_path, "bad", trace_path=a_dir)
    assert guest_view(bad) == guest_view(control)
    assert "does not open" in Path(f"{a_dir}.status").read_text()
    assert json.loads(bad["counts"].read_text())["method_calls"] == 22


# ---------------------------------------------------------------- the format

def test_the_header_file_is_the_table_every_stream_carries(build, tmp_path):
    """The rule lines of host/sa2_stream.h and the table the writer emits are one table:
    every emitted rule is its method's line (or the `*` line for its name), every line
    is emitted for some method, and every argument an op names is one the method has."""
    on = harness(build("rec"), tmp_path, "on")
    head, _ = SD.read_header(on["trace"].read_bytes())
    doc_rules, doc_entries, doc_audio = SD.doc_table(STREAM_H.read_text())
    assert SD.table_problems(head, (doc_rules, doc_entries, doc_audio)) == []
    assert len(head["rules"]) >= len(doc_rules) > 100
    # a stream without audio is version 1, with no audio table and no "audio" class
    assert struct.unpack_from("<I", on["trace"].read_bytes(), 8)[0] == 1 and "audio" not in head
    assert len(doc_audio) == 28
    assert {e: n for e, (n, _a, _o) in doc_entries.items()} == SD.D3DX_NAMES
    assert head["note_classes"] == ["blob-left-out", "walk-without-end", "lock", "unknown-object",
                                    "args", "texture-row", "out-without-rule", "buffer-changed"]


def test_every_word_out_is_an_object_or_a_value_and_not_both():
    """stream_dump introduces an object only through an out-parameter that answers one
    (OBJECT_OUTS); a count, a size, a state or a data pointer (VALUE_OUTS) introduces
    nothing. Every `out A 4` of the rule lines is in exactly one of the two, and each
    entry of either names such an op, so a new rule line has to be classified."""
    rules, entries, _audio = SD.doc_table(STREAM_H.read_text())
    outs = {(i, m, o["a"]) for (i, m), text in rules.items()
            for name, o in SD.parse_ops(text) if name == "out" and o["n"] == 4}
    obj = {(i, m, a) for (i, m), args in SD.OBJECT_OUTS.items() for a in args}
    val = {(i, m, a) for (i, m), args in SD.VALUE_OUTS.items() for a in args}
    assert not obj & val and obj | val == outs, (sorted(outs - obj - val), sorted((obj | val) - outs))
    entry_outs = {(n, o["a"]) for n, _argc, text in entries.values()
                  for name, o in SD.parse_ops(text) if name == "out" and o["n"] == 4}
    assert {(n, a) for n, args in SD.ENTRY_OBJECT_OUTS.items() for a in args} == entry_outs
    # a `*` line applies where an interface has no line of its own
    assert SD.object_outs("IDirect3DVolume9", "GetContainer") == (1,)
    assert SD.object_outs("IDirect3DSwapChain9", "GetBackBuffer") == (2,)
    assert SD.object_outs("IDirect3DVertexBuffer9", "Lock") == ()
    assert SD.object_outs("IDirect3DTexture9", "GetPrivateData") == ()


# An oracle for the rule table that shares nothing with it: the Direct3D 9 and D3DX9
# signatures, written from the SDK headers (d3d9.h, d3dx9tex.h, d3dx9shader.h), after
# `this`. Each parameter is a value ("v"), a width, height, depth, edge, length or
# format the rules name ("w", "h", "edge", "len", "fmt"), an object ("obj"), a string
# ("str"), an out interface pointer ("pp", "ppv"), a shared-handle out-parameter
# ("handle"), or a pointer to data: "in:T", "out:T", "inout:T", "in:T[aN]" (argument N
# elements) and "out:T[*aN]" (as many as the call writes through argument N).
SIGS = {
    "ID3DXConstantTable::GetConstantDesc": "v out:D3DXCONSTANT_DESC out:UINT",
    "IDirect3D9::GetAdapterIdentifier": "v v out:D3DADAPTER_IDENTIFIER9",
    "IDirect3D9::EnumAdapterModes": "v fmt v out:D3DDISPLAYMODE",
    "IDirect3D9::GetAdapterDisplayMode": "v out:D3DDISPLAYMODE",
    "IDirect3D9::CheckDeviceMultiSampleType": "v v fmt v v out:DWORD",
    "IDirect3D9::GetDeviceCaps": "v v out:D3DCAPS9",
    "IDirect3D9::CreateDevice": "v v v v inout:D3DPRESENT_PARAMETERS pp",
    "IDirect3DDevice9::GetDirect3D": "pp",
    "IDirect3DDevice9::GetDeviceCaps": "out:D3DCAPS9",
    "IDirect3DDevice9::GetDisplayMode": "v out:D3DDISPLAYMODE",
    "IDirect3DDevice9::GetCreationParameters": "out:D3DDEVICE_CREATION_PARAMETERS",
    "IDirect3DDevice9::CreateAdditionalSwapChain": "inout:D3DPRESENT_PARAMETERS pp",
    "IDirect3DDevice9::GetSwapChain": "v pp",
    "IDirect3DDevice9::Reset": "inout:D3DPRESENT_PARAMETERS",
    "IDirect3DDevice9::Present": "in:RECT in:RECT v in:RGNDATA",
    "IDirect3DDevice9::GetBackBuffer": "v v v pp",
    "IDirect3DDevice9::GetRasterStatus": "v out:D3DRASTER_STATUS",
    "IDirect3DDevice9::SetGammaRamp": "v v in:D3DGAMMARAMP",
    "IDirect3DDevice9::GetGammaRamp": "v out:D3DGAMMARAMP",
    "IDirect3DDevice9::CreateTexture": "w h v v fmt v pp handle",
    "IDirect3DDevice9::CreateVolumeTexture": "w h v v v fmt v pp handle",
    "IDirect3DDevice9::CreateCubeTexture": "edge v v fmt v pp handle",
    "IDirect3DDevice9::CreateVertexBuffer": "len v v v pp handle",
    "IDirect3DDevice9::CreateIndexBuffer": "len v fmt v pp handle",
    "IDirect3DDevice9::CreateRenderTarget": "w h fmt v v v pp handle",
    "IDirect3DDevice9::CreateDepthStencilSurface": "w h fmt v v v pp handle",
    "IDirect3DDevice9::UpdateSurface": "obj in:RECT obj in:POINT",
    "IDirect3DDevice9::StretchRect": "obj in:RECT obj in:RECT v",
    "IDirect3DDevice9::ColorFill": "obj in:RECT v",
    "IDirect3DDevice9::CreateOffscreenPlainSurface": "w h fmt v pp handle",
    "IDirect3DDevice9::GetRenderTarget": "v pp",
    "IDirect3DDevice9::GetDepthStencilSurface": "pp",
    "IDirect3DDevice9::Clear": "v in:D3DRECT[a0] v v v v",
    "IDirect3DDevice9::SetTransform": "v in:D3DMATRIX",
    "IDirect3DDevice9::GetTransform": "v out:D3DMATRIX",
    "IDirect3DDevice9::MultiplyTransform": "v in:D3DMATRIX",
    "IDirect3DDevice9::SetViewport": "in:D3DVIEWPORT9",
    "IDirect3DDevice9::GetViewport": "out:D3DVIEWPORT9",
    "IDirect3DDevice9::SetMaterial": "in:D3DMATERIAL9",
    "IDirect3DDevice9::GetMaterial": "out:D3DMATERIAL9",
    "IDirect3DDevice9::SetLight": "v in:D3DLIGHT9",
    "IDirect3DDevice9::GetLight": "v out:D3DLIGHT9",
    "IDirect3DDevice9::GetLightEnable": "v out:BOOL",
    "IDirect3DDevice9::SetClipPlane": "v in:PLANE",
    "IDirect3DDevice9::GetClipPlane": "v out:PLANE",
    "IDirect3DDevice9::GetRenderState": "v out:DWORD",
    "IDirect3DDevice9::CreateStateBlock": "v pp",
    "IDirect3DDevice9::EndStateBlock": "pp",
    "IDirect3DDevice9::SetClipStatus": "in:D3DCLIPSTATUS9",
    "IDirect3DDevice9::GetClipStatus": "out:D3DCLIPSTATUS9",
    "IDirect3DDevice9::GetTexture": "v pp",
    "IDirect3DDevice9::GetTextureStageState": "v v out:DWORD",
    "IDirect3DDevice9::GetSamplerState": "v v out:DWORD",
    "IDirect3DDevice9::ValidateDevice": "out:DWORD",
    "IDirect3DDevice9::SetPaletteEntries": "v in:PALETTE",
    "IDirect3DDevice9::GetCurrentTexturePalette": "out:UINT",
    "IDirect3DDevice9::SetScissorRect": "in:RECT",
    "IDirect3DDevice9::GetScissorRect": "out:RECT",
    "IDirect3DDevice9::DrawPrimitiveUP": "v v in:VERTICES v",
    "IDirect3DDevice9::DrawIndexedPrimitiveUP": "v v v v in:INDICES fmt in:VERTICES v",
    "IDirect3DDevice9::CreateVertexDeclaration": "in:DECLARATION pp",
    "IDirect3DDevice9::GetVertexDeclaration": "pp",
    "IDirect3DDevice9::GetFVF": "out:DWORD",
    "IDirect3DDevice9::CreateVertexShader": "in:FUNCTION pp",
    "IDirect3DDevice9::GetVertexShader": "pp",
    "IDirect3DDevice9::SetVertexShaderConstantF": "v in:FLOAT4[a2] v",
    "IDirect3DDevice9::GetVertexShaderConstantF": "v out:FLOAT4[a2] v",
    "IDirect3DDevice9::SetVertexShaderConstantI": "v in:INT4[a2] v",
    "IDirect3DDevice9::GetVertexShaderConstantI": "v out:INT4[a2] v",
    "IDirect3DDevice9::SetVertexShaderConstantB": "v in:BOOL[a2] v",
    "IDirect3DDevice9::GetVertexShaderConstantB": "v out:BOOL[a2] v",
    "IDirect3DDevice9::GetStreamSource": "v pp out:UINT out:UINT",
    "IDirect3DDevice9::GetStreamSourceFreq": "v out:UINT",
    "IDirect3DDevice9::GetIndices": "pp",
    "IDirect3DDevice9::CreatePixelShader": "in:FUNCTION pp",
    "IDirect3DDevice9::GetPixelShader": "pp",
    "IDirect3DDevice9::SetPixelShaderConstantF": "v in:FLOAT4[a2] v",
    "IDirect3DDevice9::GetPixelShaderConstantF": "v out:FLOAT4[a2] v",
    "IDirect3DDevice9::SetPixelShaderConstantI": "v in:INT4[a2] v",
    "IDirect3DDevice9::GetPixelShaderConstantI": "v out:INT4[a2] v",
    "IDirect3DDevice9::SetPixelShaderConstantB": "v in:BOOL[a2] v",
    "IDirect3DDevice9::GetPixelShaderConstantB": "v out:BOOL[a2] v",
    "IDirect3DDevice9::DrawRectPatch": "v in:FLOAT4 in:D3DRECTPATCH_INFO",
    "IDirect3DDevice9::DrawTriPatch": "v in:FLOAT3 in:D3DTRIPATCH_INFO",
    "IDirect3DDevice9::CreateQuery": "v pp",
    "IDirect3DDevice9::GetRenderTargetData": "obj obj",
    "IDirect3DDevice9::GetFrontBufferData": "v obj",
    "IDirect3DTexture9::GetLevelDesc": "v out:D3DSURFACE_DESC",
    "IDirect3DTexture9::GetSurfaceLevel": "v pp",
    "IDirect3DTexture9::LockRect": "v out:D3DLOCKED_RECT in:RECT v",
    "IDirect3DTexture9::UnlockRect": "v",
    "IDirect3DTexture9::AddDirtyRect": "in:RECT",
    "IDirect3DCubeTexture9::GetLevelDesc": "v out:D3DSURFACE_DESC",
    "IDirect3DCubeTexture9::GetCubeMapSurface": "v v pp",
    "IDirect3DCubeTexture9::AddDirtyRect": "v in:RECT",
    "IDirect3DVolumeTexture9::GetLevelDesc": "v out:D3DVOLUME_DESC",
    "IDirect3DVolumeTexture9::AddDirtyBox": "in:D3DBOX",
    "IDirect3DSurface9::GetDesc": "out:D3DSURFACE_DESC",
    "IDirect3DSurface9::LockRect": "out:D3DLOCKED_RECT in:RECT v",
    "IDirect3DSurface9::UnlockRect": "",
    "IDirect3DVertexBuffer9::Lock": "v v ppv v",
    "IDirect3DVertexBuffer9::Unlock": "",
    "IDirect3DVertexBuffer9::GetDesc": "out:D3DVERTEXBUFFER_DESC",
    "IDirect3DIndexBuffer9::Lock": "v v ppv v",
    "IDirect3DIndexBuffer9::Unlock": "",
    "IDirect3DIndexBuffer9::GetDesc": "out:D3DINDEXBUFFER_DESC",
    "IDirect3DVertexDeclaration9::GetDeclaration": "out:D3DVERTEXELEMENT9[*a1] out:UINT",
    "IDirect3DVertexShader9::GetFunction": "out:BYTE[*a1] out:UINT",
    "IDirect3DPixelShader9::GetFunction": "out:BYTE[*a1] out:UINT",
    "IDirect3DSwapChain9::Present": "in:RECT in:RECT v in:RGNDATA v",
    "IDirect3DSwapChain9::GetFrontBufferData": "obj",
    "IDirect3DSwapChain9::GetBackBuffer": "v v pp",
    "IDirect3DSwapChain9::GetRasterStatus": "out:D3DRASTER_STATUS",
    "IDirect3DSwapChain9::GetDisplayMode": "out:D3DDISPLAYMODE",
    "IDirect3DSwapChain9::GetPresentParameters": "out:D3DPRESENT_PARAMETERS",
    "*::QueryInterface": "in:GUID ppv",
    "*::GetDevice": "pp",
    "*::GetContainer": "in:GUID ppv",
    "*::SetPrivateData": "in:GUID in:BYTE[a2] v v",
    "*::GetPrivateData": "in:GUID out:BYTE[*a2] out:DWORD",
    "*::FreePrivateData": "in:GUID",
    "D3DXCreateTextureFromFileInMemoryEx": "obj in:FILE[a2] v v v v v fmt v v v v out:D3DXIMAGE_INFO out:PALETTE pp",
    "D3DXCreateTexture": "obj w h v v fmt v pp",
    "D3DXFilterTexture": "obj in:PALETTE v v",
    "D3DXLoadSurfaceFromSurface": "obj in:PALETTE in:RECT obj in:PALETTE in:RECT v v",
    "D3DXGetShaderConstantTable": "in:FUNCTION pp",
    "D3DXCreateCubeTextureFromFileInMemoryEx": "obj in:FILE[a2] v v v v fmt v v v v out:D3DXIMAGE_INFO out:PALETTE pp",
    "D3DXCreateVolumeTextureFromFileInMemoryEx": "obj in:FILE[a2] v v v v v v fmt v v v v out:D3DXIMAGE_INFO out:PALETTE pp",
    "D3DXCreateCubeTexture": "obj edge v v fmt v pp",
    "D3DXCreateVolumeTexture": "obj w h v v v fmt v pp",
    "D3DXSaveSurfaceToFileA": "str v obj in:PALETTE in:RECT",
    "D3DXSaveTextureToFileA": "str v obj in:PALETTE",
    "Direct3DCreate9": "",
}
# The bytes each points at: sizeof on x86, but D3DADAPTER_IDENTIFIER9's field extent,
# since its LARGE_INTEGER pads sizeof to 1104 with bytes nothing writes.
D3D9_SIZES = {"D3DADAPTER_IDENTIFIER9": 1100, "D3DDISPLAYMODE": 16, "DWORD": 4, "UINT": 4,
              "BOOL": 4, "D3DCAPS9": 304, "D3DPRESENT_PARAMETERS": 56,
              "D3DDEVICE_CREATION_PARAMETERS": 16, "RECT": 16, "POINT": 8, "D3DRECT": 16,
              "D3DRASTER_STATUS": 8, "D3DGAMMARAMP": 3 * 256 * 2, "D3DMATRIX": 16 * 4,
              "D3DVIEWPORT9": 6 * 4, "D3DMATERIAL9": 4 * 16 + 4, "D3DLIGHT9": 4 + 3 * 16 + 2 * 12 + 7 * 4,
              "PLANE": 4 * 4, "D3DCLIPSTATUS9": 8, "PALETTE": 256 * 4, "FLOAT4": 16, "INT4": 16,
              "FLOAT3": 12, "D3DRECTPATCH_INFO": 7 * 4, "D3DTRIPATCH_INFO": 4 * 4,
              "D3DSURFACE_DESC": 8 * 4, "D3DLOCKED_RECT": 8, "D3DVOLUME_DESC": 7 * 4,
              "D3DBOX": 6 * 4, "D3DVERTEXBUFFER_DESC": 6 * 4, "D3DINDEXBUFFER_DESC": 5 * 4,
              "D3DVERTEXELEMENT9": 8, "BYTE": 1, "GUID": 16, "D3DXIMAGE_INFO": 7 * 4,
              "D3DXCONSTANT_DESC": 48}
# Data the rules leave out on purpose, and why.
NOT_RECORDED = {
    ("*", "handle"): "pSharedHandle: Direct3D 9 (not 9Ex) takes NULL there",
    ("D3DXSaveSurfaceToFileA", "*"): "a Save call writes a file and draws nothing: no player needs its bytes",
    ("D3DXSaveTextureToFileA", "*"): "the same",
}


def sig_problems(what: str, sig: str, ops: list, argc: int) -> list[str]:
    """Where one rule's ops disagree with the signature: each op's arguments and byte
    counts, and every data pointer covered."""
    params = sig.split()
    out = [] if len(params) == argc else [f"{what}: the signature has {len(params)} parameters, the "
                                          f"device's table {argc}"]
    kind = lambda i: params[i] if i < len(params) else "?"  # noqa: E731

    def data(i):
        m = re.fullmatch(r"(in|out|inout):(\w+)(?:\[(\*?)a(\d+)\])?", kind(i))
        return (m[1], m[2], m[3], int(m[4])) if m and m[4] else ((m[1], m[2], None, None) if m else None)

    covered = set()
    for name, o in ops:
        a = o.get("a")
        d = data(a) if a is not None else None
        n = o.get("n")
        if name in ("in", "out"):
            ok = (d and d[2] is None and ((name == "in") == (d[0] == "in") or d[0] == "inout")
                  and D3D9_SIZES.get(d[1]) == n) or (name == "out" and kind(a) in ("pp", "ppv") and n == 4)
        elif name in ("in_vec", "out_vec"):
            ok = bool(d and d[2] == "" and d[3] == o["b"] and d[0] == name[:-4] and D3D9_SIZES.get(d[1]) == n)
        elif name == "out_sized":
            ok = bool(d and d[2] == "*" and d[3] == o["b"] and D3D9_SIZES.get(d[1]) == n
                      and kind(o["b"]) in ("out:UINT", "out:DWORD"))
        elif name == "pre_pp":
            ok = kind(a) in ("inout:D3DPRESENT_PARAMETERS", "in:D3DPRESENT_PARAMETERS")
        elif name == "pre_rect":
            ok = kind(a) == "in:RECT"
        elif name == "pre_rgn":
            ok = kind(a) == "in:RGNDATA"
        elif name == "shader":
            ok = kind(a) == "in:FUNCTION"
        elif name == "decl":
            ok = kind(a) == "in:DECLARATION"
        elif name == "file":
            ok = bool(d and d[1] == "FILE" and d[3] == o["b"])
        elif name in ("file_desc", "level"):
            ok = kind(a) == "pp"
        elif name in ("new_tex", "new_vol", "new_surf"):
            ok = (kind(a), kind(o["b"]), kind(o["c"]), kind(o["d"])) == ("pp", "w", "h", "fmt")
        elif name == "new_cube":
            ok = (kind(a), kind(o["b"]), kind(o["d"])) == ("pp", "edge", "fmt")
        elif name == "new_buf":
            ok = (kind(a), kind(o["b"])) == ("pp", "len")
        elif name == "up":
            ok, a = kind(2) == "in:VERTICES", 2
        elif name == "iup":
            ok, a = (kind(4), kind(6)) == ("in:INDICES", "in:VERTICES"), 4
            covered.add(6)
        else:
            ok = True                   # frame, device, object_eax, the locks: no operand to hold
        if not ok:
            out.append(f"{what}: {name} {o} is not what the signature {sig!r} gives")
        if a is not None:
            covered.add(a)
    if "device" in [n for n, _o in ops]:
        covered.add(5)                  # CreateDevice's DESC names what argument 5 received
    for i, p in enumerate(params):
        pointer = p in ("pp", "ppv", "handle") or ":" in p
        excused = (("*", p) in NOT_RECORDED or (what, "*") in NOT_RECORDED)
        if pointer and i not in covered and not excused:
            out.append(f"{what}: argument {i} ({p}) points at data no op records")
    return out


def test_every_rule_is_the_d3d9_signatures(build, tmp_path):
    """Each rule line against an oracle that shares nothing with it: every argument and
    byte count an op names is the signature's, and every data pointer a method takes is
    recorded or named in NOT_RECORDED with its reason."""
    doc_rules, doc_entries, _doc_audio = SD.doc_table(STREAM_H.read_text())
    on = harness(build("rec"), tmp_path, "on")
    head, _ = SD.read_header(on["trace"].read_bytes())
    argc = {}
    for t, s, n, a in head["methods"]:
        argc.setdefault(n, set()).add(a)
    problems = []
    for (iface, method), text in doc_rules.items():
        key = f"{iface}::{method}"
        assert key in SIGS, key
        for a in (argc[method] if iface == "*" else
                  {x for t, s, n, x in head["methods"] if n == method and head["interfaces"][t] == iface}):
            problems += sig_problems(key, SIGS[key], SD.parse_ops(text), a)
    for e, (name, a, text) in doc_entries.items():
        problems += sig_problems(name, SIGS[name], SD.parse_ops(text), a)
    assert problems == []
    # and the oracle can fail: a byte count, an argument, a pointer left out
    assert sig_problems("x", "v out:D3DDISPLAYMODE", SD.parse_ops("out 1 12"), 2)
    assert sig_problems("x", "v out:D3DDISPLAYMODE", SD.parse_ops("out 0 16"), 2)
    assert sig_problems("x", "v in:FLOAT4[a2] v", SD.parse_ops("in_vec 1 0 16"), 3)
    assert sig_problems("x", "in:RECT in:RECT v in:RGNDATA", SD.parse_ops("pre_rect 0; frame"), 4) == [
        "x: argument 1 (in:RECT) points at data no op records",
        "x: argument 3 (in:RGNDATA) points at data no op records"]


def test_every_method_the_device_says_writes_out_has_a_rule(build, tmp_path):
    """sa2_d3d9_method_flags says "out" of a method that writes through a pointer the
    guest reads; each one the device answers must have a line, or its bytes are not in
    the stream (the writer would note its first call, and the runner refuse the run)."""
    out = tmp_path / "methods.json"
    done = subprocess.run([str(build("control")), "--methods", str(out)], capture_output=True,
                          text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    table = json.loads(out.read_text())
    doc_rules, _, _ = SD.doc_table(STREAM_H.read_text())
    missing = [(i, n) for _t, _s, i, n, _a, flags, impl in table
               if "out" in flags.split(",") and impl and (i, n) not in doc_rules
               and ("*", n) not in doc_rules]
    assert missing == []
    # the dump is the device's whole table: the one every stream header carries
    on = harness(build("rec"), tmp_path, "on")
    head, _ = SD.read_header(on["trace"].read_bytes())
    assert sorted((t, s, n, a) for t, s, _i, n, a, _f, _m in table) == sorted(map(tuple, head["methods"]))
    assert sum(1 for *_x, flags, impl in table if "out" in flags.split(",") and impl) > 50


# ---------------------------------------------------------------- the object

def runner():
    sys.path.insert(0, str(ROOT / "tools" / "s4"))
    import present_run as PR
    return PR


@pytest.fixture(scope="module")
def production_object(tmp_path_factory):
    """A function (defines, source) -> the source compiled as tools/s4/link_s4.py
    compiles a presentation build's hook (present_run.compile_c: its flags and its
    working directory)."""
    need_zig()
    PR = runner()
    d = tmp_path_factory.mktemp("rec-object")
    env = zig_env(d / "zc")

    def get(*defines: str, source: Path = RECORDER) -> Path:
        o = d / f"{source.stem}{''.join(defines)}.o"
        done = PR.compile_c(source.resolve(), o, defines, env=env)
        assert done["rc"] == 0, done["stderr"]
        return o
    return get


def link_s4_compile(monkeypatch, tmp_path: Path, source: Path) -> tuple[list[str], dict]:
    """The command tools/s4/link_s4.py's phase_compile runs for `source` as a hook, and
    the keywords it runs it with: the phase itself run, its flags its own local list,
    with link_all.run standing in for the compiler and a link directory of the test's
    own. Nothing is compiled."""
    sys.path.insert(0, str(ROOT / "tools" / "s4"))
    import link_s4 as LS
    ran = []
    monkeypatch.setattr(LS, "OUT", tmp_path / "s4" / "link")
    table = tmp_path / "s3" / "link" / "tag" / "function_table_whole.c"
    table.parent.mkdir(parents=True)
    table.write_text("")
    monkeypatch.setattr(LS.L, "run", lambda cmd, **kw: ran.append((cmd, kw)) or {"rc": 0, "stderr_tail": ""})
    LS.phase_compile({}, "present-test", "tag", tmp_path / "main.c", True, [source])
    mine = [(cmd, kw) for cmd, kw in ran if cmd[-3:-2] == [str(source)]]
    assert len(mine) == 1 and len(ran) > 20, ran
    return mine[0]


def test_the_runner_compiles_the_recorder_as_link_s4_does(monkeypatch, tmp_path):
    """present_run.compile_c against link_s4's own compile of the recorder, read from
    phase_compile (link_s4_compile), not copied: the same command but for the output
    path, and the same bytes when both run from the checkout's root -- link_s4 names no
    working directory, and loop_run starts it there. check_build compares its compile
    with the object the build linked, and the object's debug information records the
    working directory."""
    PR = runner()
    source = RECORDER.resolve()
    theirs, how = link_s4_compile(monkeypatch, tmp_path, source)
    assert "cwd" not in how, how
    real_run = subprocess.run
    seen = []
    monkeypatch.setattr(PR.subprocess, "run", lambda cmd, **kw: seen.append((cmd, kw)) or real_run(cmd, **kw))
    mine_o, theirs_o = tmp_path / "compile_c.o", tmp_path / "link_s4.o"
    assert PR.compile_c(source, mine_o, env=zig_env(tmp_path / "zc"))["rc"] == 0
    [(cmd, kw)] = seen
    assert cmd[:-1] == theirs[:-1] and cmd[-1] == str(mine_o) and theirs[-2] == "-o"
    assert kw["cwd"] == str(ROOT)
    done = real_run(theirs[:-1] + [str(theirs_o)], capture_output=True, text=True, cwd=str(ROOT),
                    env=zig_env(tmp_path / "zc2"))
    assert done.returncode == 0, done.stderr
    assert hashlib.sha256(mine_o.read_bytes()).hexdigest() == hashlib.sha256(theirs_o.read_bytes()).hexdigest()


def test_the_recorder_object_reaches_only_its_list_and_computes_nothing(production_object):
    got = OC.check(production_object())
    assert got["ok"] is True, got
    assert got["undefined"] == len(OC.ALLOWED_UNDEFINED) == 65
    assert got["defined"] == len(OC.DEFINED) == len(WRAPS) == 13
    assert got["code_findings"] == [] and got["float_conversions"] == []


def test_a_recorder_that_writes_the_guest_fails_the_object_check(production_object):
    """REC_BREAK_POKE writes the guest after every call: the run-time checks cannot see
    a write the boot never reads back, and this one does."""
    got = OC.check(production_object("REC_BREAK_POKE"))
    assert got["ok"] is False and got["not_allowed"] == ["sa2_poke"]


# One falsifier per code rule, each a function the rule must refuse, and the reason
# the finding must give.
CODE_FALSIFIERS = [
    ("addsd", "double f(double a, double b) { return a + b; }",
     "ADDSD: SSE/AVX floating-point arithmetic or conversion"),
    ("x87", "long double f(long double a, long double b) { return a * b; }",
     "FMULP: an x87, MMX or 3DNow! instruction (FPU)"),
    ("cvttsd2si", "int f(double d) { return (int)d; }",
     "CVTTSD2SI: SSE/AVX floating-point arithmetic or conversion"),
    ("ldmxcsr", "void f(void) { __builtin_ia32_ldmxcsr(0x1f80u); }",
     "LDMXCSR: loads, saves or waits on the FP state or MXCSR"),
    ("fwait", 'void f(void) { __asm__ volatile("fwait"); }',
     "WAIT: loads, saves or waits on the FP state or MXCSR"),
    ("mmx", 'void f(void) { __asm__ volatile("pxor %%mm0, %%mm0" ::: "mm0"); }',
     "PXOR: an x87, MMX or 3DNow! instruction (MMX)"),
    ("3dnow", 'void f(void) { __asm__ volatile(".byte 0x0f, 0x0f, 0xc1, 0x9e" ::: "mm0"); }',
     "PFADD: an x87, MMX or 3DNow! instruction (D3NOW)"),
    ("an SSE form on MMX registers",
     'void f(void) { __asm__ volatile("pshufw $0, %%mm1, %%mm0" ::: "mm0"); }',
     "PSHUFW: names an MMX or x87 register (MM0, MM1)"),
    ("syscall", 'long f(void) { long r; __asm__ volatile("syscall" : "=a"(r) : "a"(39L) : "rcx", "r11", "memory"); return r; }',
     "SYSCALL: a system call or software interrupt"),
    ("int 0x80", 'void f(void) { __asm__ volatile("int $0x80" ::: "memory"); }',
     "INT: a system call or software interrupt"),
    ("sysenter", 'void f(void) { __asm__ volatile("sysenter"); }',
     "SYSENTER: a system call or software interrupt"),
    ("a double to a variadic call",
     'int snprintf(char *, unsigned long, const char *, ...);\n'
     'int f(char *b, double d) { return snprintf(b, 8, "%u", d) + 1; }',
     "CALL: reached with AL = 1"),
    ("a double to a variadic tail call",
     'int snprintf(char *, unsigned long, const char *, ...);\n'
     'int f(char *b, double d) { return snprintf(b, 8, "%u", d); }',
     "JMP: reached with AL = 1"),
    ("code in another section",
     '__attribute__((section(".text.other"))) double f(double a, double b) { return a * b; }',
     ".text.other+0x"),
    ("bytes that are no instruction", 'void f(void) { __asm__ volatile(".byte 0x06"); }',
     "does not decode: the linear sweep has lost the instruction stream"),
    # the fold-3 review's R1: a section attribute makes an SHT_NOTE section executable
    ("an executable section that is not PROGBITS",
     '__attribute__((section(".note.rec"))) double f(double a, double b) { return a * b + a / b; }',
     ".note.rec: an executable section of type NOTE (0x7), not PROGBITS"),
    ("arithmetic in an executable section that is not PROGBITS",
     '__attribute__((section(".note.rec"))) double f(double a, double b) { return a * b + a / b; }',
     "MULSD: SSE/AVX floating-point arithmetic or conversion"),
    # the fold-3 review's R3, as far as a rule does it
    ("a double to a variadic tail call through a pointer",
     'int snprintf(char *, unsigned long, const char *, ...);\n'
     'int (*volatile fmt)(char *, unsigned long, const char *, ...) = snprintf;\n'
     'int f(char *b, double d) { return fmt(b, 8, "%u", d); }',
     "JMP: reached with AL = 1"),
    ("an SSE/AVX instruction no list names",
     '#include <immintrin.h>\n__attribute__((target("avx512f"))) double f(double a, double b) '
     '{ return _mm_cvtsd_f64(_mm_scalef_sd(_mm_set_sd(a), _mm_set_sd(b))); }',
     "VSCALEFSD: an SSE/AVX instruction (AVX512F) that SIMD_ALLOWED does not list"),
    ("a jump into the middle of an instruction",
     'void f(void) { __asm__ volatile("jmp 1f\\n .byte 0xb8\\n 1: addsd %%xmm1, %%xmm0" ::: "xmm0"); }',
     "starts no instruction of the linear sweep"),
]


@pytest.mark.parametrize("name,source,finding", CODE_FALSIFIERS, ids=[c[0] for c in CODE_FALSIFIERS])
def test_each_code_rule_refuses_its_falsifier(production_object, tmp_path, name, source, finding):
    src = tmp_path / "falsifier.c"
    src.write_text(source + "\n")
    found = OC.code_findings(production_object(source=src).read_bytes())
    assert any(finding in f for f in found), (name, found)


def test_every_label_a_compiled_object_names_starts_an_instruction(production_object, tmp_path):
    """The boundary rule reads a relocation's target where the CPU would go: a call
    into another code section at a non-zero offset (PC-relative, from inside an
    instruction), a naked function whose first instruction is five bytes (its unwind
    entry, PC-relative from data), and a jump table (absolute). Read any other way,
    one of them lands inside an instruction of a compiled object that hides nothing."""
    src = tmp_path / "labels.c"
    src.write_text(
        "int g(int x);\n"
        '__attribute__((section(".text.other"), noinline)) int other1(int x) { return x * 3 + 1; }\n'
        '__attribute__((section(".text.other"), noinline)) int other2(int x) { return x ^ 0x5a5a; }\n'
        '__attribute__((naked)) int bare(void) { __asm__("movl $1, %eax\\n ret"); }\n'
        "int pick(int k, int x) {\n"
        "    switch (k) {\n"
        "    case 0: return other2(x);\n    case 1: return other1(x) + 7;\n"
        "    case 2: return x - 9;\n    case 3: return g(x) * 5;\n"
        "    case 4: return x << 3;\n    case 5: return bare() + x;\n"
        "    default: return 0;\n    }\n}\n")
    data = production_object(source=src).read_bytes()
    secs = OC.sections(data)
    other = next(s["index"] for s in secs if s["name"] == ".text.other")
    assert any(s["name"] == "other2" and s["shndx"] == other and s["value"] for s in OC.symbols(data))
    assert OC.code_findings(data) == []


def test_a_call_names_the_instruction_the_cpu_goes_to(production_object, tmp_path):
    """A call's PC-relative field names its symbol plus its addend plus the distance
    to the call's end, where the CPU adds it. The object's call into .text.other,
    retargeted to an instruction whose four bytes before are no instruction's start,
    is no finding; one byte further on, inside that instruction, it is one."""
    import iced_x86
    src = tmp_path / "call.c"
    src.write_text('__attribute__((section(".text.other"), noinline)) int other(int x) '
                   '{ return x * 3 + 1; }\nint f(int x) { return other(x) + 2; }\n')
    data = production_object(source=src).read_bytes()
    secs, syms = OC.sections(data), OC.symbols(data)
    other = next(s for s in secs if s["name"] == ".text.other")
    rela = next(s for s in secs if s["type"] == OC.SHT_RELA and secs[s["info"]]["name"] == ".text")
    sweep = list(iced_x86.Decoder(64, data[other["offset"]:other["offset"] + other["size"]]))
    starts = {i.ip for i in sweep}
    to = next(i for i in sweep if i.ip >= 4 and i.ip - 4 not in starts and i.len > 1)
    for j in range(rela["size"] // 24):
        _off, info, _addend = struct.unpack_from("<QQq", data, rela["offset"] + 24 * j)
        if syms[info >> 32]["shndx"] == other["index"] and info & 0xFFFFFFFF == 4:   # PLT32
            at = rela["offset"] + 24 * j + 16
            break
    else:
        pytest.fail("f's call into .text.other has no PLT32 relocation")
    found = []
    for past in (0, 1):
        buf = bytearray(data)
        struct.pack_into("<q", buf, at, to.ip + past - syms[info >> 32]["value"] - 4)
        found.append(OC.code_findings(bytes(buf)))
    assert found[0] == [], found[0]
    assert len(found[1]) == 1 and "starts no instruction of the linear sweep" in found[1][0], found[1]


@pytest.mark.parametrize("what", ["a symbol", "a relocation"])
def test_a_label_inside_an_instruction_is_refused_whatever_names_it(production_object, tmp_path,
                                                                     what):
    """A compiled object that passes, and the same object with one label moved into
    the second byte of an instruction of its own code: a static function's symbol,
    or a jump table's entry (nothing branches to either place directly, so each is
    the only name of it)."""
    import iced_x86
    src = tmp_path / "moved.c"
    src.write_text(
        "int g(int x);\n"
        "__attribute__((used)) static int spare(int x) { return x * 3 + 1; }\n"
        "int pick(int k, int x) {\n    switch (k) {\n"
        "    case 0: return g(x) + 11;\n    case 1: return g(x) * 7;\n"
        "    case 2: return g(x) - 9;\n    case 3: return g(x) ^ 5;\n"
        "    case 4: return g(x) << 3;\n    case 5: return g(x) | 64;\n"
        "    default: return 0;\n    }\n}\n")
    data = production_object(source=src).read_bytes()
    assert OC.code_findings(data) == []
    secs, syms = OC.sections(data), OC.symbols(data)
    text = next(s for s in secs if s["name"] == ".text")
    sweep = list(iced_x86.Decoder(64, data[text["offset"]:text["offset"] + text["size"]]))

    def inside(at: int) -> int:
        """The second byte of the first instruction of two bytes or more from `at`."""
        return next(i.ip for i in sweep if i.ip >= at and i.len > 1) + 1
    buf = bytearray(data)
    if what == "a symbol":
        symtab = next(s for s in secs if s["type"] == OC.SHT_SYMTAB)
        k = next(i for i, y in enumerate(syms) if y["name"] == "spare")
        struct.pack_into("<Q", buf, symtab["offset"] + 24 * k + 8, inside(syms[k]["value"]))
    else:
        rela = next(s for s in secs if s["name"] == ".rela.rodata")
        for j in range(rela["size"] // 24):
            _off, info, addend = struct.unpack_from("<QQq", data, rela["offset"] + 24 * j)
            target = syms[info >> 32]
            if target["shndx"] == text["index"]:
                moved = inside(target["value"] + addend) - target["value"]
                struct.pack_into("<q", buf, rela["offset"] + 24 * j + 16, moved)
                break
        else:
            pytest.fail("the switch compiled to no jump table into .text")
    found = OC.code_findings(bytes(buf))
    assert len(found) == 1 and "starts no instruction of the linear sweep" in found[0], found


def test_a_float_conversion_and_a_weak_reference_are_seen(production_object, tmp_path):
    src = tmp_path / "strings.c"
    src.write_text('int snprintf(char *, unsigned long, const char *, ...);\n'
                   'int f(char *b, int x) { return snprintf(b, 8, "%%%.3Lf", x); }\n'
                   'int g(char *b, int x) { return snprintf(b, 8, "%%f %d%%", x); }\n'
                   'extern int sa2_poke(unsigned, unsigned, const void *) __attribute__((weak));\n'
                   'int h(void) { return sa2_poke ? sa2_poke(0x1000u, 0u, 0) : 0; }\n')
    data = production_object(source=src).read_bytes()
    assert [f.split(" ", 1)[1] for f in OC.float_conversions(data)] == ["'%%%.3Lf'"]
    assert OC.code_findings(data) == []
    assert "sa2_poke" in OC.undefined_symbols(data)
    assert OC.defined_globals(data) == {"f", "g", "h"}


@pytest.mark.parametrize("addition,part,finding", [
    ("void *__sa2_dispatch_stub(void *s, uint32_t pc, void *m) { (void)pc; (void)m; return s; }",
     "defined_not_allowed", "__sa2_dispatch_stub"),
    ('__attribute__((used)) static void falsify(rec_call *c, double d) { rec_note(c, N_ARGS, "%u", d); }',
     "code_findings", ": reached with AL = 1"),
    ('__attribute__((used)) static void falsify(rec_call *c) { rec_note(c, N_ARGS, "%.2f", c->a[0]); }',
     "float_conversions", "'%.2f'"),
    ('__attribute__((section(".note.rec"), used)) static double falsify(double a, double b) '
     '{ return a * b + a / b; }',
     "code_findings", ".note.rec: an executable section of type NOTE (0x7), not PROGBITS"),
    ('__attribute__((section(".note.fmt"), used)) static const char falsify[] = "%g";',
     "float_conversions", ".note.fmt+0x0 '%g'"),
])
def test_the_recorder_with_one_thing_added_fails_that_rule_alone(production_object, tmp_path,
                                                                addition, part, finding):
    """The recorder's own source, included whole, and one addition: the object check
    fails, and on that rule only."""
    src = tmp_path / "rec_plus.c"
    src.write_text(f'#include "{RECORDER}"\n{addition}\n')
    got = OC.check(production_object(source=src))
    parts = ("not_allowed", "allowed_but_unused", "defined_not_allowed", "wraps_not_defined",
             "code_findings", "float_conversions")
    assert got["ok"] is False and [k for k in parts if got[k]] == [part], got
    assert any(finding in f for f in got[part]), got


@pytest.mark.parametrize("part,change", [
    ("not_allowed", lambda u, d, c, f: u.add("sa2_poke")),
    ("allowed_but_unused", lambda u, d, c, f: u.discard("getpid")),
    ("defined_not_allowed", lambda u, d, c, f: d.add("sa2_d3d9_make_d3d9")),
    ("wraps_not_defined", lambda u, d, c, f: d.discard("__wrap_sa2_ft_add")),
    ("code_findings", lambda u, d, c, f: c.append(".text+0x10 ADDSD: x")),
    ("float_conversions", lambda u, d, c, f: f.append(".rodata.str1.1+0x0 '%f'")),
])
def test_the_object_verdict_needs_every_part(production_object, monkeypatch, part, change):
    """check()'s verdict over what it read: exact on both symbol lists and empty of
    findings; each part alone fails it."""
    u, d, c, f = set(OC.ALLOWED_UNDEFINED), set(OC.DEFINED), [], []
    for name, value in (("undefined_symbols", u), ("defined_globals", d), ("code_findings", c),
                        ("float_conversions", f)):
        monkeypatch.setattr(OC, name, lambda data, v=value: v)
    obj = production_object()
    assert OC.check(obj)["ok"] is True
    change(u, d, c, f)
    got = OC.check(obj)
    assert got["ok"] is False and got[part], got


def test_the_recorder_source_holds_no_inline_asm():
    """Bytes placed in the code would hide instructions from objcheck's linear sweep,
    and only inline asm places them."""
    assert OC.source_problems(RECORDER.read_text()) == []
    assert OC.source_problems(STREAM_H.read_text()) == []
    assert OC.source_problems('int f(void) {\n  __asm__("nop");\n  asm volatile("");\n}\n') == [
        "line 2: inline asm (__asm__)", "line 3: inline asm (asm)"]
    assert OC.source_problems('/* asm here */ // asm\nconst char *s = "asm";\n') == []


def test_objcheck_reads_what_is_not_an_object_as_a_failure(tmp_path):
    p = tmp_path / "x.o"
    p.write_bytes(b"not an elf")
    assert OC.check(p) == {"ok": False, "why": "not a little-endian ELF64 file"}
    assert OC.main([str(p)]) == 1 and OC.main([]) == 2


# ---------------------------------------------------------------- the runner

def test_the_runner_runs_the_registered_configuration_under_its_own_name(monkeypatch):
    """tools/s5/present_run.py: the boot replay's own environment, the tick hook and its
    wrap as tick_replay links them, then the recorder and its wraps; an overlay, a
    counts file and a stream of its own, the stream to a partial file first; and never
    a gate build's name."""
    PR = runner()
    out = ROOT / "private" / "generated" / "s5" / "present" / "tree" / "boot"
    p = PR.paths(out, ROOT / "private" / "generated" / "s5" / "trace" / "boot.sa2s")
    cmd, spec = PR.command("present-boot.tree", p, {"SA2_DEVICE_PROFILE": "cap/profile.txt"},
                           {"build": "present-boot.tree"})
    items = spec.split("|")
    assert items[0] == "ticks"
    env = dict(i.split("=", 1) for i in items[1:])
    assert {k: env[k] for k in PR.RR.ENV} == PR.RR.ENV
    assert env["SA2_DEVICE_PROFILE"] == "cap/profile.txt"
    assert env["SA2_REPLAY_TICKS"] == "private/generated/s5/present/tree/boot/lifted.csv"
    assert env["SA2_REPLAY_WORDS"] == "private/generated/s5/present/tree/boot/lifted-words.bin"
    assert env["SA2_VFS_WRITE_ROOT"] == "private/generated/s5/present/tree/boot/vfsroot"
    assert env["SA2_REC_TRACE"] == f"private/generated/s5/trace/boot.sa2s.{PR.tree_tag()}.partial"
    assert env["SA2_REC_COUNTS"] == "private/generated/s5/present/tree/boot/counts.json"
    assert json.loads(env["SA2_REC_META"]) == {"build": "present-boot.tree"}
    hooks = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--hook"]
    wraps = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--wrap"]
    assert hooks == ["harness/replay/wrap_tick_probe.c", "host/sa2_present_rec.c"]
    assert wraps == ["sub_433ee0", *WRAPS]
    assert cmd[cmd.index("--name") + 1] == "present-boot.tree"
    assert cmd[cmd.index("--main") + 1] == "harness/crt_probe.c"
    assert [a for a in cmd if a.startswith("--probe-arg=")] == [
        "--probe-arg=--timeout", "--probe-arg=600", "--probe-arg=--dump-imports",
        "--probe-arg=private/generated/s5/present/tree/boot/imports.table.json"]
    _, counts_only = PR.command("present-boot", PR.paths(out, None), {}, {})
    assert "SA2_REC_TRACE" not in counts_only
    for name in ("reach", "replay.next+allarms", "stage-metalharbor", "loop.next+allarms",
                 "boot", "present-"):
        assert PR.build_name_problem(name), name
    assert PR.build_name_problem("present-boot") is None
    assert PR.build_name_problem(PR.default_build(False)) is None
    monkeypatch.setenv("SA2_REC_TRACE", "/somewhere/else")
    monkeypatch.setenv("SA2_STREAM_DUMP_BREAK", "seq")
    monkeypatch.setenv("SA2_PRESENT_SURVEY", "3")
    env = PR.clean_env()
    assert not any(k.startswith(("SA2_REC_", "SA2_STREAM_DUMP_")) for k in env)
    assert env["SA2_PRESENT_SURVEY"] == "3"      # the runtime's, left as the registered run leaves it


def test_each_checkout_and_mode_builds_under_a_name_of_its_own(monkeypatch):
    """Worktrees repeat directory names: the tag carries the absolute path's hash, and a
    counts-only run builds apart from a traced one."""
    PR = runner()
    names = {}
    for root in (Path("/one/wt"), Path("/two/wt"), Path("/two/tree")):
        monkeypatch.setattr(PR, "ROOT", root)
        names[root] = (PR.tree_name(), PR.tree_tag(), PR.default_build(False), PR.default_build(True),
                       PR.paths(root / "o", root / "t.sa2s")["partial"].name)
    a, b = names[Path("/one/wt")], names[Path("/two/wt")]
    assert a[0] == b[0] == "wt" and a[1] != b[1]
    assert a[1] == "wt-" + hashlib.sha256(b"/one/wt").hexdigest()[:8]
    assert a[2] == f"present-boot.{a[1]}" and a[3] == f"present-boot-counts.{a[1]}"
    assert a[4] == f"t.sa2s.{a[1]}.partial" != b[4]


def test_the_registered_alarm_is_the_probes_default():
    PR = runner()
    src = (ROOT / "harness" / "crt_probe.c").read_text()
    assert re.search(r"uint32_t va = DEFAULT_VA, timeout = (\d+);", src).group(1) == str(PR.REGISTERED_ALARM_S)
    assert PR.RR.PROBE_ARGS == []


def test_the_receipt_flags_a_run_past_the_registered_alarm(tmp_path):
    PR = runner()
    p = PR.paths(tmp_path, None)
    assert PR.alarm(p)["past_registered_alarm"] is False
    for secs, past in ((119.9, False), (120.0, False), (120.5, True), (599.0, True)):
        p["matrix"].write_text(json.dumps({"rows": [{"seconds": secs}]}))
        assert PR.alarm(p) == {"registered_alarm_s": 120, "run_alarm_s": 600, "row_seconds": secs,
                               "past_registered_alarm": past}


def test_the_inputs_are_held_key_for_key(monkeypatch, tmp_path):
    PR = runner()
    now = {"lifted": {"runtime_sha256": "r", "reach_receipt": "rr",
                      "pipeline": {"tools/a.py": "1", "tools/b.py": "2"}},
           "compare": {"policy": "p", "pipeline": {"tools/c.py": "3"}}}
    reg = {"entry": {}, "result_path": tmp_path / "result.json",
           "result": {"inputs": json.loads(json.dumps(now))}}
    monkeypatch.setattr(PR.TR, "inputs", lambda entry: now)
    assert PR.check_inputs(reg) == {"registered": str(tmp_path / "result.json"), "differing": [],
                                    "ok": True}
    for path, want in ((("lifted", "runtime_sha256"), ["lifted.runtime_sha256"]),
                       (("lifted", "pipeline", "tools/b.py"), ["lifted.pipeline.tools/b.py"]),
                       (("compare", "policy"), ["compare.policy"]),
                       (("compare", "pipeline", "tools/c.py"), ["compare.pipeline.tools/c.py"])):
        theirs = json.loads(json.dumps(now))
        node = theirs
        for k in path[:-1]:
            node = node[k]
        node[path[-1]] = "moved"
        reg["result"]["inputs"] = theirs
        got = PR.check_inputs(reg)
        assert got["differing"] == want and got["ok"] is False, path


def test_a_tree_whose_inputs_differ_is_not_run(monkeypatch, tmp_path, capsys):
    PR = runner()
    monkeypatch.setattr(PR, "registered", lambda name="boot": {"entry": {}, "result": {}, "result_path": tmp_path})
    monkeypatch.setattr(PR, "run_inputs", lambda reg: {"commit": "c"})
    monkeypatch.setattr(PR, "check_inputs", lambda reg: {"ok": False, "differing": ["lifted.x"]})
    monkeypatch.setattr(PR, "run", lambda *a: pytest.fail("a run was started"))
    out = tmp_path / "out"
    assert PR.main(["--out", str(out), "--trace", str(tmp_path / "t.sa2s")]) == 1
    assert "NOT RUN" in capsys.readouterr().err
    receipt = json.loads((out / "present-run.json").read_text())
    assert receipt["ok"] is False and receipt["inputs_check"]["differing"] == ["lifted.x"]


def test_a_run_starts_clean_from_the_pinned_saves(monkeypatch, tmp_path):
    """run(): the outputs of any earlier run, the partial stream included, are gone
    before the build; the saves are reset and required empty; the meta names the build,
    the replay, the files and a run id of its own."""
    PR = runner()
    p = PR.paths(tmp_path / "out", tmp_path / "trace" / "boot.sa2s")
    p["out"].mkdir(parents=True)
    p["trace"].parent.mkdir()
    for k in ("partial", "partial_status", "counts", "receipt", "run", "matrix"):
        p[k].write_text("from an earlier run")
    seen = {}
    monkeypatch.setattr(PR.TR, "native_device_profile", lambda entry: "default")
    monkeypatch.setattr(PR.RR, "profile_env", lambda profile: {})
    monkeypatch.setattr(PR, "forget_hook_objects", lambda build: [])
    monkeypatch.setattr(PR.SS, "reset_lifted", lambda cmd, root: {"cleared": ["a.sav"]})
    monkeypatch.setattr(PR.SS, "require_empty", lambda saves: seen.setdefault("required", saves))

    def loop_run(cmd, **kw):
        seen["left"] = [k for k in ("partial", "partial_status", "counts", "receipt", "run", "matrix")
                        if p[k].exists()]
        seen["spec"] = cmd[cmd.index("--run") + 1]
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(PR.subprocess, "run", loop_run)
    inputs = {"commit": "c", **{k: k for k in PR.RUN_FILES}}
    got = PR.run("present-boot.x", p, {"name": "boot", "stage": False, "entry": {}}, inputs)
    assert seen["left"] == [] and seen["required"] == {"cleared": ["a.sav"]}
    assert got["rc"] == 0 and got["meta"]["build"] == "present-boot.x" and got["meta"]["replay"] == "boot"
    assert {k: got["meta"][k] for k in inputs} == inputs and got["meta"]["run"]
    assert json.loads(dict(i.split("=", 1) for i in seen["spec"].split("|")[1:])["SA2_REC_META"]) \
        == got["meta"]
    again = PR.run("present-boot.x", p, {"name": "boot", "stage": False, "entry": {}}, inputs)
    assert again["meta"]["run"] != got["meta"]["run"]


@pytest.fixture
def linked(tmp_path, monkeypatch, production_object):
    """A build as link_s4 and loop_run leave it, under a link directory of its own: the
    link state, the tick hook's object and the recorder's (this tree's own compile),
    the binary, and the matrix that ran it."""
    PR = runner()
    monkeypatch.setattr(PR, "LINKDIR", tmp_path / "link")
    monkeypatch.setenv("ZIG_GLOBAL_CACHE_DIR", zig_env(tmp_path)["ZIG_GLOBAL_CACHE_DIR"])
    build = "present-boot.test"
    obj = tmp_path / "link" / "mains" / build / "obj"
    obj.mkdir(parents=True)
    rec_o = obj / "extra3_sa2_present_rec.o"
    rec_o.write_bytes(production_object().read_bytes())
    (obj / "extra2_wrap_tick_probe.o").write_bytes(b"the tick hook's object")
    binary = obj.parent / "sa2_main"
    binary.write_bytes(b"a binary")
    digest = hashlib.sha256(b"a binary").hexdigest()
    state = {"compile_ok": True, "compile_reused": ["guest_memory.c", "function_table_whole.c"],
             "phase_in_flight": None,
             "extra_c": [str(ROOT / "private/generated/s4/link/mb_edges.next+allarms.c"),
                         str((ROOT / PR.TR.HOOK).resolve()), str((ROOT / PR.RECORDER).resolve())],
             "link": {"rc": 0, "sha256": digest}}
    matrix = tmp_path / "matrix.json"
    matrix.write_text(json.dumps({"sha256": digest, "rows": []}))
    os.utime(rec_o, (1_000_000_000, 1_000_000_000))

    def check(**over):
        st = dict(state, **over)
        (tmp_path / "link" / f"main.{build}.json").write_text(json.dumps(st))
        return PR.check_build(build, matrix)
    return SimpleNamespace(PR=PR, build=build, check=check, rec_o=rec_o, obj=obj, binary=binary,
                           matrix=matrix, state=state)


def test_the_build_check_holds_a_build_of_this_tree(linked):
    got = linked.check()
    assert got["ok"] is True, got
    assert got["tied"] == [] and got["recompiled"]["same"] is True
    assert got["hooks_reused"] == [] and got["objcheck"]["ok"] is True
    assert got["objects"]["host/sa2_present_rec.c"]["sha256"] == hashlib.sha256(
        linked.rec_o.read_bytes()).hexdigest()


@pytest.mark.parametrize("name,change,needle", [
    ("a reused hook", lambda L, mp: {"compile_reused": ["extra3_sa2_present_rec.c"]}, "hooks_reused"),
    ("a failed link", lambda L, mp: {"link": dict(L.state["link"], rc=1)}, "link_rc"),
    ("a failed compile", lambda L, mp: {"compile_ok": False}, "compile_ok"),
    ("a phase that did not finish", lambda L, mp: {"phase_in_flight": "link"}, "did not finish"),
    ("another tree's recorder",
     lambda L, mp: {"extra_c": L.state["extra_c"][:2] + ["/elsewhere/host/sa2_present_rec.c"]},
     "compiled no host/sa2_present_rec.c of this tree's"),
    ("another tree's tick hook", lambda L, mp: {"extra_c": L.state["extra_c"][:1] + L.state["extra_c"][2:]},
     "compiled no harness/replay/wrap_tick_probe.c of this tree's"),
    ("another binary ran", lambda L, mp: (L.matrix.write_text(json.dumps({"sha256": "0" * 64})), {})[1],
     "the binary that ran"),
    ("a binary older than the object", lambda L, mp: os.utime(L.binary, (999_999_999, 999_999_999)) or {},
     "the binary is older than the recorder object"),
    ("two recorder objects", lambda L, mp: (L.obj / "extra4_sa2_present_rec.o").write_bytes(
        L.rec_o.read_bytes()) and {}, "no single object"),
    ("two tick hook objects", lambda L, mp: (L.obj / "extra9_wrap_tick_probe.o").write_bytes(b"x") and {},
     "objects"),
    ("the objcheck verdict",
     lambda L, mp: mp.setattr(L.PR.OC, "check", lambda o: {"ok": False, "why": "stood in"}) or {},
     "objcheck"),
    ("inline asm in the recorder's source",
     lambda L, mp: mp.setattr(L.PR.OC, "source_problems", lambda text: ["line 9: inline asm (asm)"]) or {},
     "host/sa2_present_rec.c line 9: inline asm (asm)"),
])
def test_the_build_check_fails_a_build_that_is_not_this_trees(linked, monkeypatch, name, change, needle):
    got = linked.check(**change(linked, monkeypatch))
    assert got["ok"] is False, (name, got)
    assert needle in json.dumps(got), (name, got)


def test_the_build_check_holds_the_object_linked_to_the_source(linked, production_object):
    """The poke build linked in place of this tree's recorder: objcheck refuses it, and
    this tree's source compiled again is not it."""
    linked.rec_o.write_bytes(production_object("REC_BREAK_POKE").read_bytes())
    os.utime(linked.rec_o, (1_000_000_000, 1_000_000_000))
    got = linked.check()
    assert got["ok"] is False and got["objcheck"]["not_allowed"] == ["sa2_poke"]
    assert got["recompiled"]["same"] is False and any("compiled again" in t for t in got["tied"])


def test_the_hook_objects_are_forgotten_before_a_build(linked):
    gone = linked.PR.forget_hook_objects(linked.build)
    assert sorted(Path(g).name for g in gone) == ["extra2_wrap_tick_probe.o", "extra3_sa2_present_rec.o"]
    assert list(linked.obj.glob("*.o")) == []


def test_the_registered_run_record_must_be_the_registered_binarys(monkeypatch, tmp_path):
    PR = runner()
    monkeypatch.setattr(PR.TR, "REGISTRY", tmp_path / "replays.json")
    monkeypatch.setattr(PR.TR, "OUT", tmp_path / "replay")
    (tmp_path / "replays.json").write_text(json.dumps({"replays": [{"name": "boot"}]}))
    (tmp_path / "replay" / "boot").mkdir(parents=True)
    run_json = tmp_path / "replay-boot.ticks.json"
    (tmp_path / "replay" / "boot" / "result.json").write_text(json.dumps(
        {"lifted": {"run_json": str(run_json), "binary_sha256": "b" * 64}}))
    got = PR.registered()
    assert got["run"] is None and "is not on disk" in got["run_why"]
    run_json.write_text(json.dumps({"pump": {}}))
    matrix = tmp_path / "replay-boot.matrix.json"
    matrix.write_text(json.dumps({"sha256": "a" * 64}))
    got = PR.registered()
    assert got["run"] is None and "names another binary" in got["run_why"]
    matrix.write_text(json.dumps({"sha256": "b" * 64}))
    assert PR.registered()["run"] == {"pump": {}}


# A run equal to the registered one, in miniature, and the registered one.
SURFACE = [("IDirect3DDevice9", 1, 17, 1335), ("IDirect3DDevice9", 1, 41, 1335),
           ("IDirect3DDevice9", 1, 94, 2131191)]
DX = {"D3DXCreateTextureFromFileInMemoryEx": 199, "D3DXCreateTexture": 131,
      "D3DXFilterTexture": 123, "D3DXLoadSurfaceFromSurface": 0, "D3DXGetShaderConstantTable": 26,
      "D3DXCreateCubeTextureFromFileInMemoryEx": 0, "D3DXCreateVolumeTextureFromFileInMemoryEx": 0,
      "D3DXCreateCubeTexture": 0, "D3DXCreateVolumeTexture": 0, "D3DXSaveSurfaceToFileA": 0,
      "D3DXSaveTextureToFileA": 0, "Direct3DCreate9": 1}


def record(write_root: str) -> dict:
    return {"pump": {"iterations": 1300}, "calls": {"total": 1273341},
            "trap": {"note": "the guest called ExitProcess(0)", "exit_pc": "0x002006a0"},
            "files": {"write_root": write_root, "stats": {"reads": 7}},
            "heap": {"peak_bytes": 4096, "allocs": 9}, "threads": {"created": 47},
            "d3d9": {"dynamic_surface": [{"interface": i, "slot": s, "calls": n}
                                         for i, _t, s, n in SURFACE],
                     "distinct_methods": 3, "frames": {"present": 1335},
                     "method_calls": sum(m[3] for m in SURFACE) + 479,
                     "textures_from_files": {"answered": 199}, "constant_tables": {"built": 26}}}


# The registered run's settings (its matrix row) and save reset, and the pin.
REG_ENV = {"SA2_PUMP_POLLS": "1300", "SA2_D3D9_DEVICE": "1", "SA2_DEVICE_PROFILE": "cap/p.txt",
           "SA2_REPLAY_TICKS": "private/generated/replay/boot/lifted.csv",
           "SA2_REPLAY_WORDS": "private/generated/replay/boot/lifted-words.bin"}
PIN = {"file": "Documentation/save-state.json", "digest": "e4" * 32,
       "state": "before the Light Dash pickup"}
REG_SAVES = {"pin": PIN, "installation": "private/install", "installation_matches_pin": True,
             "overlay": "private/generated/s4/vfsroot", "removed": {}, "left": {}}


def run_saves(PR, p) -> dict:
    """The save reset of a run equal to the registered one: the registered pin and
    installation, and its own overlay, left empty."""
    return dict(REG_SAVES, overlay=PR.rel(p["overlay"]), removed={})


@pytest.fixture
def fixture_run(tmp_path, monkeypatch):
    """The outputs of a run equal to the registered one, the registered side, and the
    word log reader standing in for the C reader."""
    PR = runner()
    p = PR.paths(tmp_path / "out", None)
    p["out"].mkdir()
    p["ticks"].write_text("tick,w\n0,1\n1,2\n")
    p["run"].write_text(json.dumps(record("private/generated/s5/present/x/vfsroot")))
    p["words"].write_bytes(b"mine")
    theirs = tmp_path / "registered-words.bin"
    theirs.write_bytes(b"theirs")
    counts = {"counting": True, "why": "", "thunks_wrapped": 2048,
              "methods": [{"interface": i, "type": t, "slot": s, "calls": n, "entered": n}
                          for i, t, s, n in SURFACE],
              "entries": [{"name": k, "calls": v, "entered": v} for k, v in DX.items()],
              "method_calls": sum(m[3] for m in SURFACE), "distinct_methods": 3,
              "presents": 1335, "d3dx_calls": 479,
              "method_and_d3dx_calls": sum(m[3] for m in SURFACE) + 479}
    p["counts"].write_text(json.dumps(counts))
    p["imports"].write_text(json.dumps({"rows": [
        {"dll": "d3d9.dll" if k == "Direct3DCreate9" else "d3dx9_42.dll", "name": k, "calls": v}
        for k, v in DX.items()]}))
    mine = dict(REG_ENV, SA2_REPLAY_TICKS=PR.rel(p["ticks"]), SA2_REPLAY_WORDS=PR.rel(p["words"]),
                SA2_REC_COUNTS=PR.rel(p["counts"]), SA2_REC_META='{"build": "present-boot.t"}')
    p["matrix"].write_text(json.dumps({"sha256": "b" * 64, "rows": [
        {"label": "ticks", "env": mine, "seconds": 84.82}]}))
    check = {"ticks": 1299, "named_cells_compared": 28578, "named_cells_differing": 0,
             "digest_cells_compared": 114312, "digest_cells_differing": 0}
    reg_run = record("private/generated/s4/r1fold/tree/vfsroot")
    reg = {"name": "boot", "stage": False, "result_path": tmp_path / "result.json",
           "run": reg_run, "run_json": tmp_path / "replay-boot.ticks.json", "run_why": None,
           "matrix": tmp_path / "replay-boot.matrix.json",
           "matrix_row": {"label": "ticks", "env": dict(REG_ENV), "seconds": 83.57},
           "result": {"lifted": {"sha256": hashlib.sha256(p["ticks"].read_bytes()).hexdigest(),
                                 "reach": PR.TR.reach_of(reg_run),
                                 "saves": json.loads(json.dumps(REG_SAVES)),
                                 "words": {"path": str(theirs), "check": dict(check, log_sha256="f4")}}}}
    wl = {"info": lambda log: {"whole": True, "ticks": 1299},
          "check_capture": lambda csv, log, native: dict(check, ok=True, problems=[]),
          "hashes": lambda log: {t: f"h{t}" for t in range(1300)},
          "diff": lambda a, b: ([], {"compared_through": 1299, "keyframe_differs": "0"})}
    for k, f in wl.items():
        monkeypatch.setattr(PR.WL, k, f)
    monkeypatch.setattr(PR.DP, "check_run", lambda run, profile: profile)
    monkeypatch.setattr(PR.RR, "switch_problems", lambda run, env: [])
    return PR, p, reg, counts


def applied_input(entry, applied, judged_to_tick):
    """tick_replay.input_record, stood in for: the applied-input log's rows as the
    applied rows, held to nothing."""
    rows = applied.read_text().splitlines()[1:]
    return {"applied": {"path": "x", "sha256": hashlib.sha256(applied.read_bytes()).hexdigest()},
            "judged_to_tick": judged_to_tick, "recorded_rows": 3, "calls": len(rows),
            "applied_rows": sum(1 for r in rows if r.endswith(",1")),
            "misses": sum(1 for r in rows if r.endswith(",0")), "problems": []}


@pytest.fixture
def stage_fixture(fixture_run, monkeypatch):
    """fixture_run as a stage replay's: the stage outputs, each at the registered
    result's pin, and the input it applied as the result accounts for it."""
    PR, p, reg, counts = fixture_run
    reg.update(name="metalharbor", stage=True,
               entry={"name": "metalharbor", "input": {"script": "cap/input.csv", "sha256": "0" * 64}})
    lifted = reg["result"]["lifted"]
    p["stage_trace"].write_text("tick,level,act\n1,10,0\n")
    p["goal_latch"].write_text("tick,goal\n1,0\n")
    p["input_applied"].write_text("tick,call,applied\n1,0,1\n2,0,1\n3,0,1\n")
    pin_stage_outputs(PR, p, lifted)                    # and the import table fixture_run wrote
    monkeypatch.setattr(PR.TR, "input_record", applied_input)
    lifted["input"] = applied_input(reg["entry"], p["input_applied"], 3)
    return PR, p, reg, counts


def pin_stage_outputs(PR, p, lifted) -> None:
    """Pin the run's stage outputs in the registered result, as they are."""
    for mine, theirs in PR.STAGE_PINS:
        lifted[theirs] = {"path": f"private/generated/replay/metalharbor/{p[mine].name}",
                          "sha256": PR.sha256(p[mine])}


def run_checks(PR, p, reg, **kw):
    return PR.checks(p, reg, trace=kw.pop("trace", None), meta_want=kw.pop("meta_want", None),
                     profile=kw.pop("profile", "default"), profile_env={},
                     saves=kw.pop("saves", run_saves(PR, p)), **kw)


def test_the_checks_hold_on_a_run_equal_to_the_registered_one(fixture_run):
    PR, p, reg, _ = fixture_run
    got = run_checks(PR, p, reg)
    assert {k: v["ok"] for k, v in got.items()} == {
        "lifted_csv": True, "reach": True, "record": True, "configuration": True,
        "words": True, "counts": True, "spec": True, "saves": True}
    assert got["record"]["allowed"] == ["files.write_root"]
    assert got["spec"]["settings"] == 7 and got["saves"]["pin"] == "e4" * 32


def test_a_stage_run_is_held_to_its_stage_outputs_too(stage_fixture):
    """checks() on a stage replay's run: every part of the boot's, and `stage`."""
    PR, p, reg, _ = stage_fixture
    got = run_checks(PR, p, reg)
    assert {k: v["ok"] for k, v in got.items()} == {
        "lifted_csv": True, "reach": True, "record": True, "configuration": True,
        "words": True, "counts": True, "spec": True, "saves": True, "stage": True}
    assert set(got["stage"]) == {"stage_trace", "goal_latch", "input_applied", "import_table",
                                 "input", "ok"}
    assert got["stage"]["input"]["got"]["applied_rows"] == 3


def perturb_json(path: Path, fn) -> None:
    d = json.loads(path.read_text())
    fn(d)
    path.write_text(json.dumps(d))


@pytest.mark.parametrize("name,check,change", [
    ("the tick capture", "lifted_csv", lambda PR, p, reg, mp: p["ticks"].write_text("tick,w\n0,1\n1,3\n")),
    ("an iteration", "reach",
     lambda PR, p, reg, mp: perturb_json(p["run"], lambda d: d["pump"].update(iterations=1299))),
    ("the heap peak", "record",
     lambda PR, p, reg, mp: perturb_json(p["run"], lambda d: d["heap"].update(peak_bytes=4112))),
    ("a thread", "record",
     lambda PR, p, reg, mp: perturb_json(p["run"], lambda d: d["threads"].update(created=48))),
    ("a file read under files", "record",
     lambda PR, p, reg, mp: perturb_json(p["run"], lambda d: d["files"]["stats"].update(reads=8))),
    ("a leaf only the run has", "record",
     lambda PR, p, reg, mp: perturb_json(p["run"], lambda d: d["heap"].update(frees=1))),
    ("a leaf only the registered run has", "record",
     lambda PR, p, reg, mp: reg["run"]["threads"].update(joined=2)),
    ("true where the registered run has 1", "record",
     lambda PR, p, reg, mp: (perturb_json(p["run"], lambda d: d["heap"].update(allocs=True)),
                             reg["run"]["heap"].update(allocs=1))),
    ("1.0 where the registered run has 1", "record",
     lambda PR, p, reg, mp: perturb_json(p["run"], lambda d: d["threads"].update(created=47.0))),
    ("no registered run record", "record",
     lambda PR, p, reg, mp: reg.update(run=None, run_why="the matrix names another binary")),
    ("the profile read", "configuration",
     lambda PR, p, reg, mp: mp.setattr(PR.DP, "check_run", lambda r, pr: (_ for _ in ()).throw(
         PR.DP.Refusal("the run read another profile")))),
    ("a device switch", "configuration",
     lambda PR, p, reg, mp: mp.setattr(PR.RR, "switch_problems", lambda r, e: ["SA2_DSOUND_DEVICE is off"])),
    ("a log not whole", "words",
     lambda PR, p, reg, mp: mp.setattr(PR.WL, "info", lambda log: {"whole": "registered" in str(log),
                                                                   "ticks": 1299})),
    ("a capture check", "words",
     lambda PR, p, reg, mp: mp.setattr(PR.WL, "check_capture", lambda c, l, native: {"ok": False, "problems": ["x"]})),
    ("a capture count", "words",
     lambda PR, p, reg, mp: mp.setattr(PR.WL, "check_capture", lambda c, l, native: {
         "ok": True, "problems": [], "ticks": 1299, "named_cells_compared": 28577,
         "named_cells_differing": 0, "digest_cells_compared": 114312, "digest_cells_differing": 0})),
    ("a tick hash", "words",
     lambda PR, p, reg, mp: mp.setattr(PR.WL, "hashes", lambda log: {
         t: f"h{t}" if t != 700 or "registered" in str(log) else "other" for t in range(1300)})),
    ("a tick one side lacks", "words",
     lambda PR, p, reg, mp: mp.setattr(PR.WL, "hashes", lambda log: {
         t: f"h{t}" for t in range(1300 if "registered" in str(log) else 1299)})),
    ("the keyframe", "words",
     lambda PR, p, reg, mp: mp.setattr(PR.WL, "diff", lambda a, b: ([], {"compared_through": 1299,
                                                                         "keyframe_differs": "1"}))),
    ("a word", "words",
     lambda PR, p, reg, mp: mp.setattr(PR.WL, "diff", lambda a, b: ([{"va": 1}], {"compared_through": 1299,
                                                                                   "keyframe_differs": "0"}))),
    ("the ticks compared", "words",
     lambda PR, p, reg, mp: mp.setattr(PR.WL, "diff", lambda a, b: ([], {"compared_through": 1200,
                                                                         "keyframe_differs": "0"}))),
    ("a call counted", "counts",
     lambda PR, p, reg, mp: perturb_json(p["counts"], lambda d: d["methods"][1].update(calls=1334, entered=1334))),
    ("an entry's calls", "counts",
     lambda PR, p, reg, mp: perturb_json(p["counts"], lambda d: d["entries"][11].update(calls=2, entered=2))),
])
def test_each_check_fails_when_its_comparison_fails(fixture_run, monkeypatch, name, check, change):
    PR, p, reg, _ = fixture_run
    change(PR, p, reg, monkeypatch)
    got = run_checks(PR, p, reg)
    assert got[check]["ok"] is False, name
    receipt = {"mode": "run", "inputs_check": {"ok": True}, "build_check": {"ok": True},
               "run": {"rc": 0}, "checks": got}
    assert PR.verdict(receipt) is False


def test_the_configuration_of_no_record_does_not_hold(fixture_run):
    PR, _p, _reg, _ = fixture_run
    assert PR.check_configuration({}, "default", {}) == {"problems": [], "ok": False}


@pytest.mark.parametrize("name,change", [
    ("counting stopped", lambda c, run, reg, rows: c.update(counting=False)),
    ("a reason to stop", lambda c, run, reg, rows: c.update(why="the table moved")),
    ("method calls against the surface's sum", lambda c, run, reg, rows: c.update(method_calls=c["method_calls"] + 1)),
    ("distinct methods", lambda c, run, reg, rows: c.update(distinct_methods=4)),
    ("Presents", lambda c, run, reg, rows: c.update(presents=1334)),
    ("method and d3dx calls against the device's", lambda c, run, reg, rows: c.update(
        method_and_d3dx_calls=c["method_and_d3dx_calls"] - 1)),
    ("loads against textures answered", lambda c, run, reg, rows: run["d3d9"]["textures_from_files"].update(
        answered=198)),
    ("constant tables against those built", lambda c, run, reg, rows: run["d3d9"]["constant_tables"].update(
        built=25)),
    ("one method against the run", lambda c, run, reg, rows: (
        run["d3d9"]["dynamic_surface"][0].update(calls=1336), run["d3d9"]["dynamic_surface"][1].update(calls=1334))),
    ("one method against the registered run", lambda c, run, reg, rows: (
        reg["run"]["d3d9"]["dynamic_surface"][0].update(calls=1336),
        reg["run"]["d3d9"]["dynamic_surface"][1].update(calls=1334))),
    ("no registered surface", lambda c, run, reg, rows: reg.update(run=None)),
    ("a call that never returned", lambda c, run, reg, rows: c["methods"][0].update(entered=1336)),
    ("an entry that never returned", lambda c, run, reg, rows: c["entries"][0].update(entered=200)),
    ("an entry against the import table", lambda c, run, reg, rows: rows[1].update(calls=130)),
    ("no import table", lambda c, run, reg, rows: rows.clear()),
])
def test_each_count_is_held_on_its_own(fixture_run, name, change):
    """check_counts, one comparison at a time: each alone fails it."""
    PR, p, reg, counts = fixture_run
    run = json.loads(p["run"].read_text())
    rows = json.loads(p["imports"].read_text())["rows"]
    assert PR.check_counts(p, run, reg)["ok"] is True
    c = json.loads(json.dumps(counts))
    change(c, run, reg, rows)
    p["counts"].write_text(json.dumps(c))
    p["imports"].write_text(json.dumps({"rows": rows}))
    assert PR.check_counts(p, run, reg)["ok"] is False, name


def test_the_counts_check_holds_the_audio_counts(fixture_run):
    """check_counts on a counts file with audio (a run with --audio): audio_counts is part
    of its verdict, so a store that changed after its Unlock, and nothing else, fails it;
    test_s5_audio.py fails audio_counts on each of its terms alone."""
    PR, p, reg, counts = fixture_run
    run = json.loads(p["run"].read_text())
    lines = len(SD.doc_table(STREAM_H.read_text())[2])
    audio = {"thunks_wrapped": lines, "ops": [{"name": "DS_LOCK", "entered": 2, "returned": 2}],
             "store_watch": {"every_frames": 64, "set": 1, "checks": 1, "bytes_hashed": 32,
                             "watched_at_exit": 0, "changed": 0}}
    p["counts"].write_text(json.dumps(dict(counts, audio=audio)))
    got = PR.check_counts(p, run, reg)
    assert got["ok"] is True and got["audio"]["ok"] is True
    audio["store_watch"]["changed"] = 1
    p["counts"].write_text(json.dumps(dict(counts, audio=audio)))
    got = PR.check_counts(p, run, reg)
    assert got["audio"] == {"thunks_wrapped": [lines, lines], "unfinished": [],
                            "store_watch": audio["store_watch"], "ok": False}
    assert got["ok"] is False


def row_of(p, fn) -> None:
    """Change this run's matrix row with `fn`."""
    perturb_json(p["matrix"], lambda d: fn(d["rows"][0]))


def saves_with(**change):
    return lambda PR, p, reg: {"saves": dict(run_saves(PR, p), **change)}


@pytest.mark.parametrize("name,check,change", [
    ("a setting", "spec", lambda PR, p, reg: row_of(p, lambda r: r["env"].update(SA2_PUMP_POLLS="1299"))),
    ("the label", "spec", lambda PR, p, reg: row_of(p, lambda r: r.update(label="a"))),
    ("an observer's output in the registered run's place", "spec",
     lambda PR, p, reg: row_of(p, lambda r: r["env"].update(SA2_REPLAY_TICKS=REG_ENV["SA2_REPLAY_TICKS"]))),
    ("no recorder counts", "spec", lambda PR, p, reg: row_of(p, lambda r: r["env"].pop("SA2_REC_COUNTS"))),
    ("no registered row", "spec", lambda PR, p, reg: reg.update(matrix_row=None, run_why="gone")),
    ("the pin", "saves", saves_with(pin=dict(PIN, digest="0" * 64))),
    ("the pin's state", "saves", saves_with(pin=dict(PIN, state="after the Light Dash pickup"))),
    ("the installation", "saves", saves_with(installation="private/install-other")),
    ("an installation off its pin", "saves", saves_with(installation_matches_pin=False)),
    ("a save left in the overlay", "saves",
     saves_with(left={"resource/gd_PC/SAVEDATA/SONIC2B__S01": "e3" * 32})),
    ("no record of what the overlay held", "saves", saves_with(left=None)),
    ("the registered overlay reset instead", "saves", saves_with(overlay=REG_SAVES["overlay"])),
    ("no record of the reset", "saves", lambda PR, p, reg: {"saves": None}),
    ("a registered run that did not start empty", "saves",
     lambda PR, p, reg: reg["result"]["lifted"]["saves"].update(left={"resource": "<dir>"})),
])
def test_each_setting_and_the_save_reset_are_held(fixture_run, name, check, change):
    """check_spec: every setting loop_run recorded and the label, the registered run's;
    check_saves: the reset's pin and installation the registered run's, the
    installation at its pin, the overlay the run's own and empty. The rest holds."""
    PR, p, reg, _ = fixture_run
    got = run_checks(PR, p, reg, **(change(PR, p, reg) or {}))
    assert got[check]["ok"] is False, name
    assert [k for k, v in got.items() if not v["ok"]] == [check], name
    assert PR.verdict({"mode": "run", "inputs_check": {"ok": True}, "build_check": {"ok": True},
                       "run": {"rc": 0}, "checks": got}) is False


def input_record_with(**change):
    def change_it(PR, p, reg, mp):
        mp.setattr(PR.TR, "input_record", lambda e, a, t: dict(applied_input(e, a, t), **change))
    return change_it


@pytest.mark.parametrize("name,change", [
    ("the stage trace", lambda PR, p, reg, mp: p["stage_trace"].write_text("tick,level,act\n1,10,1\n")),
    ("no goal-latch column", lambda PR, p, reg, mp: p["goal_latch"].unlink()),
    ("the import table", lambda PR, p, reg, mp: perturb_json(p["imports"], lambda d: d["rows"].append(
        {"dll": "kernel32.dll", "name": "Sleep", "calls": 0}))),
    ("the applied-input log", lambda PR, p, reg, mp: p["input_applied"].write_text(
        "tick,call,applied\n1,0,1\n2,0,1\n")),
    # the input, held to the recording again: only the account of it differs
    ("an input call the recording missed", input_record_with(misses=1, problems=["1 call missed"])),
    ("other rows applied", input_record_with(applied_rows=2)),
    ("another applied log", input_record_with(applied={"path": "x", "sha256": "0" * 64})),
    ("no account of the input", lambda PR, p, reg, mp: reg["result"]["lifted"].pop("input")),
])
def test_each_stage_output_and_the_input_are_held(stage_fixture, monkeypatch, name, change):
    PR, p, reg, _ = stage_fixture
    change(PR, p, reg, monkeypatch)
    got = run_checks(PR, p, reg)
    assert [k for k, v in got.items() if not v["ok"]] == ["stage"], name
    assert PR.verdict({"mode": "run", "inputs_check": {"ok": True}, "build_check": {"ok": True},
                       "run": {"rc": 0}, "checks": got}) is False


def test_the_verdict_needs_every_part(fixture_run):
    PR, p, reg, _ = fixture_run
    checks_ = run_checks(PR, p, reg)
    good = {"mode": "run", "inputs_check": {"ok": True}, "build_check": {"ok": True},
            "run": {"rc": 0}, "checks": checks_}
    assert PR.verdict(good) is True
    for part, bad in (("inputs_check", {"ok": False}), ("build_check", {"ok": False}),
                      ("run", {"rc": 1}), ("build_check", None), ("checks", {})):
        assert PR.verdict({**good, part: bad}) is False, part
    check = {**good, "mode": "check"}
    assert PR.verdict(check) is False                      # no run_matches_tree
    assert PR.verdict({**check, "run_matches_tree": {"ok": True}}) is True
    assert PR.verdict({**check, "run_matches_tree": {"ok": False}}) is False


def stream_fixture(PR, p, build, tmp_path, scenario="main"):
    """A harness stream held by check_stream against a run record that has its load."""
    on = harness(build("rec"), tmp_path, f"stream-{scenario}", scenario=scenario)
    p["counts"].write_text(on["counts"].read_text())
    dds = bytes.fromhex(on["bytes"]["dds"]) if scenario == "main" else b""
    run_rec = {"d3d9": {"calls": [], "textures_from_files": {
        "answered": 1, "source_bytes_hashed": len(dds),
        "rows": [{"texture": f"{on['objects'].get('file_texture', 0):#010x}", "bytes": len(dds),
                  "sha256": hashlib.sha256(dds).hexdigest(), "container": "dds",
                  "png": {"bit_depth": 0, "colour_type": 0, "interlace": 0, "trns": 0},
                  "created": {"w": 64, "h": 32, "levels": 7, "format": 0x35545844,
                              "chain_bytes": None, "level0_bytes": None},
                  "file": {"w": 64, "h": 32, "depth": 1, "levels": 1, "format": 0x35545844}}]}}}
    desc = struct.unpack("<22I", blob(on["trace"], 24, 5)) if scenario == "main" else None
    if desc:
        row = run_rec["d3d9"]["textures_from_files"]["rows"][0]
        row["created"].update(chain_bytes=desc[20], level0_bytes=desc[21])
        row.update(levels_from=PROV[desc[13]], format_from=PROV[desc[14]],
                   dimensions_from=PROV[desc[15]])
    # The device's counts, as a run record keeps them, equal to the stream's own
    # (stream_dump.hold_counts_to_run holds the two; its own test breaks them).
    run_rec["d3d9"].update(record_counts(SD.scan(on["trace"])))
    return on, run_rec


def record_counts(summary: dict) -> dict:
    """The d3d9 counts a run record would keep for a stream that agrees with it."""
    return {"dynamic_surface": [{"interface": m["interface"], "slot": m["slot"],
                                 "calls": m["calls"]} for m in summary["methods"]],
            "distinct_methods": summary["distinct_methods"],
            "frames": {"present": summary["frames"]},
            "method_calls": summary["method_calls"] + summary["d3dx_calls"],
            "objects_built": summary["objects_introduced"]}


def test_the_stream_check_holds_a_whole_stream_and_fails_a_lossy_or_foreign_one(
        fixture_run, build, tmp_path):
    PR, p, reg, _ = fixture_run
    on, run_rec = stream_fixture(PR, p, build, tmp_path)
    # the run's import table, as the probe dumps it: every entry's argc and calls
    s = SD.scan(on["trace"])
    table = {"rows": [{"dll": "d3d9.dll" if n == "Direct3DCreate9" else "d3dx9_42.dll",
                       "name": n, "argc": a, "calls": s["d3dx"].get(n, 0)}
                      for n, a in s["entries_table"].values()]}
    p["imports"].write_text(json.dumps(table))
    meta = {"build": "rec_harness"}
    got = PR.check_stream(p, on["trace"], run_rec, meta)
    assert got["ok"] is True, got
    assert PR.check_stream(p, on["trace"], run_rec, {"build": "another"})["ok"] is False
    wrong = json.loads(json.dumps(run_rec))
    wrong["d3d9"]["textures_from_files"]["rows"][0]["sha256"] = "0" * 64
    assert "sha256" in PR.check_stream(p, on["trace"], wrong, meta)["held_to_run"][0]
    # the import table is read: an entry's argc and an entry's calls, each held
    table["rows"][2]["argc"] += 1
    table["rows"][1]["calls"] += 1
    p["imports"].write_text(json.dumps(table))
    held = PR.check_stream(p, on["trace"], run_rec, meta)["held_to_run"]
    assert held == [
        "entry 3 D3DXFilterTexture: 4 arguments in the header; the import table says 5",
        "D3DXCreateTexture: 1 records in the stream, 2 calls in the run's import table"], held
    p["imports"].unlink()
    table["rows"][2]["argc"] -= 1
    table["rows"][1]["calls"] -= 1
    p["imports"].write_text(json.dumps(table))
    # the sidecar must say the writer closed the stream
    status = Path(f"{on['trace']}.status")
    status.write_text("disabled: a write failed")
    assert PR.check_stream(p, on["trace"], run_rec, meta)["ok"] is False
    status.unlink()
    assert PR.check_stream(p, on["trace"], run_rec, meta)["ok"] is False
    status.write_text("closed: 28 records")
    # the stream's counts must be the recorder's
    perturb_json(p["counts"], lambda d: d.update(presents=2))
    got = PR.check_stream(p, on["trace"], run_rec, meta)
    assert got["ok"] is False and got["counts_mismatch"] == ["1 FRAME records against 2 Presents counted"]
    edge, edge_run = stream_fixture(PR, p, build, tmp_path, "edge")
    lossy = PR.check_stream(p, edge["trace"], edge_run, meta)
    assert lossy["ok"] is False and lossy["notes_not_accepted"] == {"blob-left-out": 1}


def test_the_stream_check_holds_the_header_to_the_format_file(fixture_run, build, tmp_path, monkeypatch):
    """check_stream reads host/sa2_stream.h's lines and holds the stream's header table to
    them: a format file whose SetViewport line says 20 bytes fails a stream whose writer
    says 24."""
    PR, p, reg, _ = fixture_run
    p["imports"].unlink()
    on, run_rec = stream_fixture(PR, p, build, tmp_path)
    assert PR.check_stream(p, on["trace"], run_rec, {"build": "rec_harness"})["ok"] is True
    text, n = re.subn(r"(rule IDirect3DDevice9::SetViewport\s+in 0) 24", r"\1 20", STREAM_H.read_text())
    assert n == 1
    fmt = tmp_path / "sa2_stream.h"
    fmt.write_text(text)
    monkeypatch.setattr(PR, "STREAM_H", str(fmt))
    got = PR.check_stream(p, on["trace"], run_rec, {"build": "rec_harness"})
    assert got["ok"] is False
    assert got["problems"] == ["IDirect3DDevice9::SetViewport: the header's rule is 'in 0 24', the "
                               "format's line 'in 0 20'"]


def test_a_trace_that_is_not_a_stream_fails_the_stream_check(fixture_run, tmp_path):
    PR, p, _reg, _ = fixture_run
    for data in (b"", b"not a stream at all", make(whole())[:40],
                 *(make(whole(), header=dict(HEADER, **change)) for _name, change in BAD_TABLES)):
        t = tmp_path / "t.sa2s"
        t.write_bytes(data)
        got = PR.check_stream(p, t, {}, None)
        assert got["ok"] is False and "is not a stream this reader reads" in got["why"], got


def test_ready_is_written_only_for_a_stream_that_holds(tmp_path, monkeypatch):
    """settle() in run mode: a stream that holds replaces the trace, and READY -- the
    digest and the run's meta -- is written after it, never beside a stream it does
    not describe; a stream that fails leaves the last good one and READY, and is
    deleted (or kept as .failed)."""
    PR = runner()
    trace = tmp_path / "trace" / "boot.sa2s"
    trace.parent.mkdir()
    p = PR.paths(tmp_path / "o", trace)
    meta = {"build": "present-boot.x", "run": "r1"}
    trace.write_bytes(b"the last good stream")
    PR.write_ready(p["ready"], hashlib.sha256(b"the last good stream").hexdigest(), {"run": "r0"})
    p["partial"].write_bytes(b"a stream that failed")
    Path(f"{p['partial']}.status").write_text("closed: 1 records")
    receipt = {"ok": False, "run": {"meta": meta}}
    PR.settle(receipt, p, trace, p["partial"])
    assert trace.read_bytes() == b"the last good stream" and p["ready"].is_file()
    assert receipt["ready"] == ("left as it was: this run's stream did not hold; its 20 bytes "
                                "are deleted")
    assert not p["partial"].exists() and not Path(f"{p['partial']}.status").exists()
    p["partial"].write_bytes(b"a stream that failed")
    PR.settle({"ok": False, "run": {"meta": meta}}, p, trace, p["partial"], keep_failed=True)
    assert p["failed"].read_bytes() == b"a stream that failed" and not p["partial"].exists()
    p["partial"].write_bytes(b"a stream that held")
    Path(f"{p['partial']}.status").write_text("closed: 1 records")
    seen = []
    real = os.replace

    def replace_(src, dst):
        if Path(dst) == trace:
            seen.append(p["ready"].exists())
        real(src, dst)
    monkeypatch.setattr(PR.os, "replace", replace_)
    receipt = {"ok": True, "run": {"meta": meta}}
    PR.settle(receipt, p, trace, p["partial"])
    assert seen == [False]                                  # READY was gone while the trace moved
    assert trace.read_bytes() == b"a stream that held" and not p["partial"].exists()
    assert PR.read_ready(p["ready"]) == {"digest": hashlib.sha256(b"a stream that held").hexdigest(),
                                         "meta": meta}
    assert p["ready"].read_text().splitlines()[0] == hashlib.sha256(b"a stream that held").hexdigest()
    assert p["trace_status"].read_text() == "closed: 1 records"


@pytest.mark.parametrize("held", [fcntl.LOCK_EX, fcntl.LOCK_SH], ids=["exclusive", "shared"])
def test_settle_waits_for_another_runner_on_the_same_trace(tmp_path, held):
    """Every checkout's default trace is one file. While another runner holds
    <trace>.lock, settle neither replaces the stream nor writes READY; when the lock
    is let go, it finishes, so the stream and READY it leaves are one run's. Its own
    lock is exclusive: it waits for a shared holder too, so no two settles overlap."""
    PR = runner()
    trace = tmp_path / "trace" / "boot.sa2s"
    trace.parent.mkdir()
    p = PR.paths(tmp_path / "o", trace)
    trace.write_bytes(b"the other run's stream")
    p["partial"].write_bytes(b"this run's stream")
    receipt = {"ok": True, "run": {"meta": {"run": "this"}}}
    with open(f"{trace}.lock", "a") as other:
        fcntl.flock(other, held)
        t = threading.Thread(target=PR.settle, args=(receipt, p, trace, p["partial"]), daemon=True)
        t.start()
        t.join(0.5)
        assert t.is_alive() and trace.read_bytes() == b"the other run's stream"
        assert not p["ready"].exists()
        fcntl.flock(other, fcntl.LOCK_UN)
    t.join(30)
    assert not t.is_alive() and trace.read_bytes() == b"this run's stream"
    assert PR.read_ready(p["ready"]) == {"digest": hashlib.sha256(b"this run's stream").hexdigest(),
                                         "meta": {"run": "this"}}


@pytest.fixture
def checked(fixture_run, tmp_path, monkeypatch):
    """main(["--check"]) on a run the fixture made: the registered side, the tree's files
    and the after-the-run checks stood in for, a stream at the trace whose meta names
    the run, and its READY."""
    PR, p, reg, _ = fixture_run
    now = {"commit": "c0ffee", **{k: f"{k}-now" for k in PR.RUN_FILES}}
    meta = {"build": "present-boot.test", "replay": "boot", "run": "run-1", **now}
    monkeypatch.setattr(PR, "registered", lambda name="boot": {**reg, "entry": {"name": "boot"},
                                                   "result_path": tmp_path / "result.json"})
    monkeypatch.setattr(PR, "run_inputs", lambda reg: dict(now))
    monkeypatch.setattr(PR, "check_inputs", lambda reg: {"ok": True, "differing": []})
    monkeypatch.setattr(PR.TR, "native_device_profile", lambda entry: "default")
    monkeypatch.setattr(PR.RR, "profile_env", lambda profile: {})
    state = {"stream_ok": True, "held": []}

    def checks(p_, reg_, *, trace, meta_want, profile, profile_env, saves, accepted=()):
        state["held"].append((trace, meta_want))
        out = {k: {"ok": True} for k in ("lifted_csv", "reach", "record", "configuration", "words",
                                         "counts")}
        if trace is not None:
            out["stream"] = {"ok": state["stream_ok"]}
        return out
    monkeypatch.setattr(PR, "checks", checks)
    trace = tmp_path / "trace" / "boot.sa2s"
    trace.parent.mkdir()

    def put_stream(m):
        data = make(whole(), header=dict(HEADER, meta=json.dumps(m)))
        trace.write_bytes(data)
        return data
    data = put_stream(meta)
    PR.write_ready(trace.with_suffix(".READY"), hashlib.sha256(data).hexdigest(), meta)
    receipt = {"run": {"rc": 0, "inputs": dict(now), "meta": meta}, "build_check": {"ok": True}}
    (p["out"] / "present-run.json").write_text(json.dumps(receipt))

    def check():
        rc = PR.main(["--check", "--out", str(p["out"]), "--trace", str(trace)])
        return rc, json.loads((p["out"] / "present-run.json").read_text())
    return SimpleNamespace(PR=PR, p=p, trace=trace, ready=trace.with_suffix(".READY"), meta=meta,
                           now=now, receipt=receipt, state=state, check=check, put_stream=put_stream,
                           write_receipt=lambda r: (p["out"] / "present-run.json").write_text(json.dumps(r)))


def test_check_holds_its_own_stream_again(checked):
    rc, receipt = checked.check()
    assert rc == 0 and receipt["ok"] is True and receipt["mode"] == "check"
    assert checked.state["held"] == [(checked.trace, checked.meta)]
    assert receipt["stream_owner"]["ours"] is True
    assert checked.PR.read_ready(checked.ready) == {
        "digest": hashlib.sha256(checked.trace.read_bytes()).hexdigest(), "meta": checked.meta}


def test_check_withdraws_ready_when_its_own_stream_no_longer_holds(checked):
    checked.state["stream_ok"] = False
    rc, receipt = checked.check()
    assert rc == 1 and not checked.ready.exists()
    assert receipt["ready"] == "removed: the stream no longer holds"


@pytest.mark.parametrize("name,setup,why", [
    ("the files that made the run are not the tree's",
     lambda C: C.write_receipt({**C.receipt, "run": {**C.receipt["run"], "inputs": {
         **C.now, "recorder_sha256": "another recorder"}}}),
     "this tree cannot re-hold the run"),
    ("the stream is another run's",
     lambda C: C.PR.write_ready(C.ready, hashlib.sha256(C.put_stream(
         dict(C.meta, build="present-boot.other", run="run-9"))).hexdigest(),
         dict(C.meta, build="present-boot.other", run="run-9")),
     "is another run's: its meta names {'build': 'present-boot.other', 'run': 'run-9'}"),
    ("the trace is not a stream",
     lambda C: C.trace.write_bytes(b"not a stream"), "is not a stream: no SA2D3D9S magic"),
    ("the trace's meta is this run's and its tables do not read",
     lambda C: C.trace.write_bytes(make(whole(), header=dict(HEADER, meta=json.dumps(C.meta),
                                                             methods=[5]))),
     "is not a stream: the header's tables do not read"),
    ("the trace is empty", lambda C: C.trace.write_bytes(b""), "is not a stream"),
    ("READY describes another run's stream",
     lambda C: (C.PR.write_ready(C.ready, hashlib.sha256(C.trace.read_bytes()).hexdigest(),
                                 dict(C.meta, run="run-2")),
                C.state.update(stream_ok=False)),
     "READY describes another run's stream"),
    ("READY names other bytes",
     lambda C: (C.PR.write_ready(C.ready, "0" * 64, C.meta), C.state.update(stream_ok=False)),
     "READY names other bytes than the stream at the trace"),
])
def test_a_failed_check_leaves_a_ready_it_does_not_own(checked, name, setup, why):
    setup(checked)
    before = checked.ready.read_text()
    rc, receipt = checked.check()
    assert rc == 1 and receipt["ok"] is False, name
    assert checked.ready.read_text() == before, name
    assert receipt["ready"].startswith("left as it was") and why in receipt["ready"], (name, receipt["ready"])


def test_check_fails_a_run_whose_files_are_not_the_trees(checked):
    checked.write_receipt({**checked.receipt, "run": {**checked.receipt["run"], "inputs": {
        **checked.now, "recorder_sha256": "another recorder"}}})
    rc, receipt = checked.check()
    assert rc == 1 and receipt["run_matches_tree"] == {"differing": ["recorder_sha256"], "ok": False}


def test_check_fails_a_stream_another_run_made_and_says_so(checked):
    other = dict(checked.meta, build="present-boot.other", run="run-9")
    checked.put_stream(other)
    rc, receipt = checked.check()
    assert rc == 1 and receipt["stream_owner"]["ours"] is False
    assert checked.state["held"] == [(None, checked.meta)]          # the stream itself is not held
    assert "is another run's" in receipt["checks"]["stream"]["why"]


def test_check_writes_a_receipt_for_a_trace_that_is_not_a_stream(checked, capsys):
    checked.trace.write_bytes(b"not a stream")
    rc, receipt = checked.check()
    assert rc == 1 and receipt["checks"]["stream"]["ok"] is False
    assert "not a stream" in receipt["checks"]["stream"]["why"]
    assert "NOT OK" in capsys.readouterr().out


def test_check_records_the_audio_of_the_run_it_holds(checked):
    """--check's receipt says whether the run it holds asked for audio as that run's meta
    says, the meta the checks hold the stream to, whatever --audio says: the flag only
    chooses the default paths, and --out and --trace are given here."""
    asked = dict(checked.meta, audio=True)
    checked.PR.write_ready(checked.ready, hashlib.sha256(checked.put_stream(asked)).hexdigest(), asked)
    checked.write_receipt({**checked.receipt, "run": {**checked.receipt["run"], "meta": asked}})
    rc, receipt = checked.check()                                   # without --audio
    assert (rc, receipt["audio"], checked.state["held"]) == (0, True, [(checked.trace, asked)])
    checked.PR.write_ready(checked.ready, hashlib.sha256(checked.put_stream(checked.meta)).hexdigest(),
                           checked.meta)
    checked.write_receipt(checked.receipt)
    assert checked.PR.main(["--check", "--audio", "--out", str(checked.p["out"]), "--trace",
                            str(checked.trace)]) == 0
    receipt = json.loads((checked.p["out"] / "present-run.json").read_text())
    assert receipt["audio"] is False and checked.state["held"][-1] == (checked.trace, checked.meta)


def test_check_holds_the_runs_own_build_verdict(checked):
    checked.write_receipt({**checked.receipt, "build_check": {"ok": False, "why": "objcheck"}})
    rc, receipt = checked.check()
    assert rc == 1 and receipt["build_check"] == {"ok": False, "why": "objcheck"}


def test_check_refuses_without_a_readable_receipt(tmp_path, capsys):
    PR = runner()
    out = tmp_path / "nothing-here"
    assert PR.main(["--check", "--out", str(out), "--trace", str(tmp_path / "t.sa2s")]) == 2
    assert "no run to hold" in capsys.readouterr().err
    assert not out.exists()
    out.mkdir()
    for text in ("{not json", "[1, 2]"):
        (out / "present-run.json").write_text(text)
        assert PR.main(["--check", "--out", str(out), "--trace", str(tmp_path / "t.sa2s")]) == 2
        assert "does not read as a receipt" in capsys.readouterr().err



# ---------------------------------------------------------------- Metal Harbor: the recorder

HARNESS_MH = ROOT / "tools" / "s5" / "tests" / "rec_harness_mh.c"
BUFFERS = ("IDirect3DVertexBuffer9", "IDirect3DIndexBuffer9")


@pytest.fixture(scope="module")
def build_mh(tmp_path_factory):
    """As `build`, for tools/s5/tests/rec_harness_mh.c."""
    if not ZIG.is_file():
        pytest.skip(f"the pinned zig is not present: {ZIG}")
    d = tmp_path_factory.mktemp("rec-harness-mh")
    env = dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(d / "zc"),
               ZIG_GLOBAL_CACHE_DIR=os.environ.get(
                   "ZIG_GLOBAL_CACHE_DIR",
                   str(ROOT / "private" / "generated" / "zig-cache-tests")))
    base = [str(ZIG), "cc", "-target", "x86_64-linux-musl", "-O1", "-Wall", "-Wextra", "-Werror",
            "-I", str(ROOT / "runtime"), "-I", str(ROOT / "host")]
    made: dict[str, Path] = {}

    def get(variant: str) -> Path:
        if variant not in made:
            exe = d / f"rec_harness_mh.{variant}"
            cmd = base + [str(HARNESS_MH), str(D3D9_C), str(ROOT / "runtime/sa2_guest_call.c")]
            if variant != "control":
                cmd += [str(RECORDER), str(audio_getters(d))] + [f"-Wl,-wrap={w}" for w in WRAPS]
                if variant != "rec":
                    cmd.append(f"-D{variant}")
            done = subprocess.run(cmd + ["-o", str(exe)], capture_output=True, text=True, env=env)
            assert done.returncode == 0, done.stderr
            made[variant] = exe
        return made[variant]
    return get


def harness_mh(exe: Path, tmp_path: Path, name: str, *, trace: bool = True,
               stale: bool = False) -> dict:
    """One run of the Metal Harbor harness: what the guest saw, and where the stream
    and counts went."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("SA2_REC_", "SA2_D3D9", "SA2_D3DX", "SA2_STREAM_"))}
    tr, counts = tmp_path / f"{name}.sa2s", tmp_path / f"{name}.counts.json"
    if trace:
        env.update(SA2_REC_TRACE=str(tr), SA2_REC_COUNTS=str(counts),
                   SA2_REC_META='{"build": "rec_harness_mh"}')
    out = tmp_path / f"{name}.json"
    done = subprocess.run([str(exe), "--out", str(out)] + (["--stale"] if stale else []),
                          capture_output=True, text=True, env=env, timeout=120)
    assert done.returncode == 0, done.stderr
    seen = json.loads(out.read_text())
    seen["trace"], seen["counts"] = tr, counts
    return seen


def mh_view(seen: dict) -> tuple:
    return seen["answers"], seen["guest_fnv"], seen["stats"], seen["objects"], seen["locks"]


def u32(v: int) -> bytes:
    return struct.pack("<I", v)


def by_call(recs: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in recs:
        out.setdefault(r.get("call") or r.get("entry") or r["kind"], []).append(r)
    return out


def test_the_recorder_records_metal_harbors_calls(build_mh, tmp_path):
    """Each call Metal Harbor makes beyond the boot, as the guest made it: the reader
    holds every record to its rule and every LOCK to its Lock, and the bytes here are
    held to the ones the harness wrote."""
    on = harness_mh(build_mh("rec"), tmp_path, "on")
    s = SD.scan(on["trace"], want_range=(0, 1 << 20))
    assert s["problems"] == [] and s["note_records"] == 0 and s["end"] is True
    recs, ob = s["range"], on["objects"]
    by = by_call(recs)

    def blob_of(r, role, arg=0xFFFF):
        return blob(on["trace"], r["seq"], role, arg)
    # the targets the guest asked for, each with the object the device answered
    assert blob_of(by["IDirect3DDevice9::GetBackBuffer"][0], 2, 3) == u32(ob["back_buffer"])
    assert blob_of(by["IDirect3DDevice9::GetRenderTarget"][0], 2, 1) == u32(ob["render_target"])
    assert blob_of(by["IDirect3DDevice9::GetDepthStencilSurface"][0], 2, 0) == u32(ob["depth"])
    assert blob_of(by["IDirect3DDevice9::CreateVertexBuffer"][0], 2, 4) == u32(ob["buffer"])
    assert blob_of(by["IDirect3DDevice9::CreateTexture"][0], 2, 6) == u32(ob["rt_texture"])
    assert blob_of(by["IDirect3DTexture9::GetSurfaceLevel"][0], 2, 1) == u32(ob["rt_level"])
    assert blob_of(by["IDirect3DDevice9::CreateDepthStencilSurface"][0], 2, 6) == \
        u32(ob["rt_depth"])
    assert by["IDirect3DDevice9::SetRenderTarget"][0]["args"] == ["0x00000000",
                                                                  f"{ob['rt_level']:#010x}"]
    assert by["IDirect3DDevice9::SetDepthStencilSurface"][0]["args"] == [f"{ob['rt_depth']:#010x}"]
    # the four buffer locks: each Unlock's bytes are the guest's, from the address its
    # Lock answered, as many as it asked for; the nested one closes first
    part, whole, again, inner = on["locks"]
    u1, u2, u3, u4 = by["IDirect3DVertexBuffer9::Unlock"]
    assert struct.unpack("<11I4iII", blob_of(u1, 6)) == (
        ob["buffer"], 0, 0, part, 0, 1, 0x200, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x100)
    assert blob_of(u1, 3).hex() == on["bytes"]["part"]
    assert struct.unpack("<11I4iII", blob_of(u2, 6)) == (
        ob["buffer"], 0, 0, whole, 0, 1, 0x600, 0, 0, 0, 0, 0, 0, 0, 0, 0x2000, 0)
    assert blob_of(u2, 3).hex() == on["bytes"]["whole"] and part == whole + 0x100
    assert struct.unpack("<11I4iII", blob_of(u3, 6)) == (
        ob["buffer"], 0, 0, inner, 0, 1, 0x40, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x40)
    assert blob_of(u3, 3).hex() == on["inner"] and inner == whole + 0x40
    assert struct.unpack("<11I4iII", blob_of(u4, 6))[3:7] == (again, 0, 1, 0x600)
    assert blob_of(u4, 3).hex() == on["again"] and again == whole
    # the draws from the buffer, and the copy of the back buffer into the texture's
    # level, with its rectangle
    assert by["IDirect3DDevice9::SetStreamSource"][0]["args"] == [
        "0x00000000", f"{ob['buffer']:#010x}", "0x00000000", "0x00000018"]
    assert by["IDirect3DDevice9::DrawPrimitive"][0]["args"] == ["0x00000004", "0x00000000",
                                                                "0x00000002"]
    assert len(by["IDirect3DDevice9::DrawPrimitive"]) == 1 + 192
    load = by["D3DXLoadSurfaceFromSurface"][0]
    assert load["args"][0] == f"{ob['rt_level']:#010x}" and load["args"][3] == \
        f"{ob['back_buffer']:#010x}"
    assert blob_of(load, 1, 2) == struct.pack("<4I", 0, 0, 128, 128)
    assert len(by["frame"]) == 192 and recs[-1]["kind"] == "end"
    # the watch: three of the four Unlocks set it (not the nested one, whose outer lock
    # was still open), three checks ran, and only the first found the buffer unlocked
    # and alive to hash
    counts = json.loads(on["counts"].read_text())
    assert counts["buffer_watch"] == {"every_frames": 64, "set": 3, "checks": 3,
                                      "watched_at_exit": 0, "bytes_hashed": 0x600, "changed": 0}
    assert runner().buffer_watch(counts) == {"unlocks": 4, "watch": counts["buffer_watch"],
                                             "ok": True}
    # the reader recomputes the same watch from the stream's own records
    assert s["buffer_watch"] == {"every_frames": 64, "set": 3, "checks": 3, "bytes_hashed": 0x600,
                                 "watched_at_exit": 0}
    assert SD.compare_counts(s, counts) == []


def test_the_guest_sees_the_same_on_metal_harbors_calls(build_mh, tmp_path):
    control = harness_mh(build_mh("control"), tmp_path, "control", trace=False)
    on = harness_mh(build_mh("rec"), tmp_path, "on")
    assert mh_view(on) == mh_view(control)
    assert len(control["answers"]) > 200 and all(a == 0 for a, _ in control["answers"])


def test_a_buffer_copied_from_its_stores_base_is_caught(build_mh, tmp_path):
    """REC_BREAK_BUFBASE copies an offset lock's bytes from the store's base: the LOCK
    and the LOCKED length still agree, so only the bytes themselves show it."""
    broken = harness_mh(build_mh("REC_BREAK_BUFBASE"), tmp_path, "bufbase")
    s = SD.scan(broken["trace"], want_range=(0, 1 << 20))
    u1 = by_call(s["range"])["IDirect3DVertexBuffer9::Unlock"][0]
    assert blob(broken["trace"], u1["seq"], 3).hex() != broken["bytes"]["part"]


def test_a_buffer_written_after_its_unlock_is_caught(build_mh, tmp_path):
    """--stale writes one byte of the buffer after its Unlock: the watch counts it,
    notes it after the FRAME whose check saw it, and the runner refuses it. With
    REC_BREAK_NOWATCH the watch sees nothing, and says it checked nothing; the reader,
    which recomputes the watch from the stream, says three checks were due, so the
    runner's stream check refuses that stream too."""
    PR = runner()
    for variant, changed, checks in (("rec", 1, 3), ("REC_BREAK_NOWATCH", 0, 0)):
        stale = harness_mh(build_mh(variant), tmp_path, f"stale-{variant}", stale=True)
        counts = json.loads(stale["counts"].read_text())
        watch = counts["buffer_watch"]
        assert (watch["changed"], watch["checks"]) == (changed, checks), (variant, watch)
        assert counts["notes"]["buffer-changed"] == changed
        s = SD.scan(stale["trace"], want_range=(0, 1 << 20))
        assert s["problems"] == []
        noted = [r for r in s["range"] if r["kind"] == "note"]
        assert len(noted) == changed and all(
            n["text"].startswith("buffer-changed: buffer")
            and "changed after its Unlock" in n["text"] for n in noted), noted
        if changed:
            k = s["range"].index(noted[0])
            assert s["range"][k - 1]["kind"] == "frame"        # after the FRAME that saw it
        assert PR.buffer_watch(counts)["ok"] is (changed == 0)
        assert s["buffer_watch"]["checks"] == 3
        assert SD.compare_counts(s, counts) == ([] if variant == "rec" else [
            "buffer_watch.checks: 0 in the counts file, 3 from the stream's own Unlocks and FRAMEs",
            "buffer_watch.bytes_hashed: 0 in the counts file, 1536 from the stream's own Unlocks "
            "and FRAMEs"])
        # the runner's stream check, on either: a note it refuses, or a watch that lied
        run_rec = {"d3d9": dict(record_counts(s), calls=[], textures_from_files={
            "answered": 0, "source_bytes_hashed": 0, "rows": []})}
        p = PR.paths(tmp_path / f"out-{variant}", None)
        p["out"].mkdir()
        p["counts"].write_text(stale["counts"].read_text())
        got = PR.check_stream(p, stale["trace"], run_rec, {"build": "rec_harness_mh"})
        assert got["ok"] is False and got["held_to_run"] == []
        assert (got["notes_not_accepted"], len(got["counts_mismatch"])) == (
            ({"buffer-changed": 1}, 0) if variant == "rec" else ({}, 2))
        fresh = harness_mh(build_mh(variant), tmp_path, f"fresh-{variant}")    # no stale write
        p["counts"].write_text(fresh["counts"].read_text())
        fs = SD.scan(fresh["trace"])
        run_rec["d3d9"].update(record_counts(fs))
        assert PR.check_stream(p, fresh["trace"], run_rec, {"build": "rec_harness_mh"})["ok"] is (
            variant == "rec")


# ---------------------------------------------------------------- Metal Harbor: the reader

def test_the_streams_counts_are_held_to_the_runs_own_record(build_mh, tmp_path):
    """stream_dump.hold_counts_to_run: the stream's calls per method, methods, FRAMEs,
    calls with entry calls, objects introduced, and entry records against the run's
    record and import table; each disagreement named, none under the runcounts break.
    The objects are the recording device's own count: every object it built reached
    the guest through an out-parameter, a description or Direct3DCreate9's answer."""
    on = harness_mh(build_mh("rec"), tmp_path, "on")
    s = SD.scan(on["trace"])
    rc = record_counts(s)
    assert s["objects_introduced"] == on["stats"][1] == 8     # the device's objects_built
    rows = [{"dll": "d3dx9_42.dll" if n != "Direct3DCreate9" else "d3d9.dll", "name": n,
             "calls": s["d3dx"].get(n, 0)} for n in SD.D3DX_NAMES.values()]
    assert s["d3dx"] == {"Direct3DCreate9": 1, "D3DXLoadSurfaceFromSurface": 1}
    assert SD.hold_counts_to_run(s, {"d3d9": rc}, rows) == []
    fewer = json.loads(json.dumps(rc))
    next(r for r in fewer["dynamic_surface"]
         if (r["interface"], r["slot"]) == ("IDirect3DVertexBuffer9", 12))["calls"] -= 1
    fewer["frames"]["present"] += 1
    fewer["method_calls"] += 1
    fewer["objects_built"] += 1
    more = [dict(r, calls=r["calls"] + (r["name"] == "D3DXCreateCubeTexture")) for r in rows]
    got = SD.hold_counts_to_run(s, {"d3d9": fewer}, more)
    assert got == [
        "IDirect3DVertexBuffer9 slot 12: 4 records in the stream, 3 calls in the run's record",
        "192 FRAME records against the run's 193 Presents",
        f"{s['method_calls']} calls and 1 entry calls in the stream, {rc['method_calls'] + 1} "
        f"method calls in the run's record",
        "8 objects introduced in the stream, 9 built in the run's record",
        "D3DXCreateCubeTexture: 0 records in the stream, 1 calls in the run's import table"]
    assert SD.hold_counts_to_run(s, {"d3d9": {}}, rows) != []
    os.environ["SA2_STREAM_DUMP_BREAK"] = "runcounts"
    try:
        assert SD.hold_counts_to_run(s, {"d3d9": fewer}, more) == []
    finally:
        os.environ.pop("SA2_STREAM_DUMP_BREAK", None)


# ---------------------------------------------------------------- Metal Harbor: the runner

def test_the_stage_runner_runs_the_registered_observed_run_under_its_own_name(tmp_path,
                                                                                monkeypatch):
    """present_run.stage_command: tools/s4/stage_run.py's command as tick_replay calls it
    for the registered observed run -- the stage, the poll budget, the input script, the
    profile, label ticks, the stage trace and word log -- under present-mh.<tree>, into
    the run's own directory, with the recorder's settings (the stream to a partial file
    first), --dump-imports, and the recorder's hook and wraps after the stage's own; the
    only other difference is the alarm."""
    PR = runner()
    import stage_run as SR
    monkeypatch.setattr(PR, "ROOT", tmp_path)
    monkeypatch.setattr(SR, "ROOT", tmp_path)
    monkeypatch.setattr(PR.TR, "stage_poll_budget", lambda entry, root=None: 15645)
    for k in list(os.environ):
        if k in PR.RR.DEVICE_VARS:
            monkeypatch.delenv(k)
    entry = {"name": "metalharbor", "stage": {"name": "metalharbor", "level": 10},
             "input": {"script": "private/cap/sa2-input.csv", "sha256": "0" * 64}}
    profile = {"path": "private/cap/sa2-device-profile.txt", "sha256": "1" * 64,
               "guest": {"cwd": "Z:\\g", "exe_path": "Z:\\g\\sonic2app-unpacked.exe"}}
    out = tmp_path / "private" / "generated" / "s5" / "present" / "tree" / "metalharbor"
    trace = tmp_path / "private" / "generated" / "s5" / "trace" / "metalharbor.sa2s"
    p = PR.paths(out, trace)
    cmd, spec = PR.stage_command("present-mh.tree", p, entry, profile, {"build": "present-mh.tree"})
    # the registered run's own command, as tick_replay.run_stage_lifted builds it
    reg_cmd, _, _ = SR.command(
        "metalharbor", 15645, label="ticks", input_script=tmp_path / entry["input"]["script"],
        extra_env={"SA2_STAGE_TRACE": "private/generated/replay/metalharbor/stage-trace.csv",
                   "SA2_REPLAY_WORDS": "private/generated/replay/metalharbor/lifted-words.bin"},
        timeout=3129, device_profile=profile, name="replay-metalharbor",
        out_dir=tmp_path / "private" / "generated" / "replay" / "metalharbor", observe=True)
    items, theirs = spec.split("|"), reg_cmd[reg_cmd.index("--run") + 1].split("|")
    assert items[0] == theirs[0] == "ticks"
    env = dict(i.split("=", 1) for i in items[1:])
    reg_env = dict(i.split("=", 1) for i in theirs[1:])
    mine = "private/generated/s5/present/tree/metalharbor"
    assert env.pop("SA2_VFS_WRITE_ROOT") == f"{mine}/vfsroot"
    assert PR.spec_problems(env, reg_env, mine) == []
    assert env["SA2_REC_TRACE"] == f"private/generated/s5/trace/metalharbor.sa2s.{PR.tree_tag()}.partial"
    assert env["SA2_REC_COUNTS"] == f"{mine}/counts.json"
    assert json.loads(env["SA2_REC_META"]) == {"build": "present-mh.tree"}
    assert env["SA2_INPUT_SCRIPT"] == str((tmp_path / "private/cap/sa2-input.csv").resolve())
    hooks = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--hook"]
    wraps = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--wrap"]
    assert hooks == list(SR.HOOKS) + ["host/sa2_present_rec.c"]
    assert wraps == list(SR.WRAPS) + list(WRAPS)
    assert cmd[cmd.index("--name") + 1] == "present-mh.tree"
    assert [a for a in cmd if a.startswith("--probe-arg=")] == [
        "--probe-arg=--timeout", f"--probe-arg={3 * 3129}", "--probe-arg=--dump-imports",
        f"--probe-arg={mine}/imports.table.json"]
    assert [a for a in reg_cmd if a.startswith("--probe-arg=")] == [
        "--probe-arg=--timeout", "--probe-arg=3129"]
    assert PR.stage_alarm(entry) == (15645, 3129, 9387)
    assert PR.replay_hooks({"stage": True}) == tuple(SR.HOOKS)
    assert PR.replay_hooks({"stage": False}) == (PR.TR.HOOK,)
    for name, replay in (("present-boot", "metalharbor"), ("present-boot.tree", "metalharbor"),
                         ("present-mh.tree", "boot"), ("replay-metalharbor", "metalharbor"),
                         ("stage-metalharbor", None),
                         # the other replay's exploratory builds, which exist under s4/link
                         ("present-mh-probe", "boot"), ("present-mh-watch", "boot"),
                         ("present-boot-x", "metalharbor")):
        assert PR.build_name_problem(name, replay), (name, replay)
    for name in ("present-mh.tree", "present-mh-probe", f"present-mh.{PR.tree_name()}"):
        assert PR.build_name_problem(name, "metalharbor") is None, name
    for name in ("present-boot-x", "present-poke.tree"):
        assert PR.build_name_problem(name, "boot") is None, name


def test_check_holds_a_stage_runs_own_files_to_the_tree():
    """What --check holds to the current tree (run_files of run_inputs): for a stage run,
    tools/s4/stage_run.py and every hook it links too, so a stream made with another
    stage hook fails run_matches_tree."""
    PR = runner()
    import stage_run as SR
    made = PR.run_inputs({"stage": True})
    assert PR.run_files({"stage": True}) == PR.RUN_FILES + ("stage_run_sha256", "stage_hooks_sha256")
    assert made["stage_run_sha256"] == PR.sha256(ROOT / "tools" / "s4" / "stage_run.py")
    assert made["stage_hooks_sha256"] == {h: PR.sha256(ROOT / h) for h in SR.HOOKS}
    assert PR.run_files({"stage": False}) == PR.RUN_FILES
    assert not set(PR.STAGE_RUN_FILES) & set(PR.run_inputs({"stage": False}))


def test_the_spec_check_holds_every_setting_to_the_registered_runs():
    PR = runner()
    theirs = {"SA2_PUMP_POLLS": "15645", "SA2_REPLAY_TICKS": "private/r/lifted.csv",
              "SA2_STAGE_TRACE": "private/r/stage-trace.csv", "SA2_DEVICE_PROFILE": "cap/p.txt"}
    mine = {"SA2_PUMP_POLLS": "15645", "SA2_REPLAY_TICKS": "private/me/lifted.csv",
            "SA2_STAGE_TRACE": "private/me/stage-trace.csv", "SA2_DEVICE_PROFILE": "cap/p.txt",
            "SA2_REC_COUNTS": "private/me/counts.json", "SA2_REC_META": "{}"}
    assert PR.spec_problems(mine, theirs, "private/me") == []
    assert PR.spec_problems(dict(mine, SA2_PUMP_POLLS="1300"), theirs, "private/me") == [
        "SA2_PUMP_POLLS='1300' where the registered run has '15645'"]
    assert PR.spec_problems(dict(mine, SA2_STAGE_TRACE="private/r/stage-trace.csv"), theirs,
                            "private/me") != []
    assert PR.spec_problems(dict(mine, SA2_STAGE_TRACE="private/me/trace.csv"), theirs,
                            "private/me") != []
    assert PR.spec_problems({k: v for k, v in mine.items() if k != "SA2_DEVICE_PROFILE"}, theirs,
                            "private/me") == ["SA2_DEVICE_PROFILE is only in the registered run's "
                                              "settings"]
    assert PR.spec_problems(dict(mine, SA2_INPUT_SCRIPT="x.csv"), theirs, "private/me") == [
        "SA2_INPUT_SCRIPT is only in this run's settings"]
    assert PR.spec_problems({k: v for k, v in mine.items() if k != "SA2_REC_COUNTS"}, theirs,
                            "private/me") != []
    assert PR.spec_problems(mine, dict(theirs, SA2_REC_TRACE="t"), "private/me") != []


@pytest.fixture
def guarded_run(tmp_path, monkeypatch):
    """present_run.run and run_stage with the world stood in for: the run, the save
    reset and the objects' staleness stubbed, require_empty the real one, and each
    step recorded in `events` in the order it came."""
    PR = runner()
    import rebuild_image as RI
    events: list[tuple] = []
    saves = {"left": {}}
    script = tmp_path / "cap" / "input.csv"
    script.parent.mkdir()
    script.write_text("tick,call\n")
    entry = {"name": "metalharbor", "stage": {"name": "metalharbor"},
             "input": {"script": str(script), "sha256": PR.sha256(script)}}
    monkeypatch.setattr(RI, "staged_not_rebuilt", lambda: [])
    monkeypatch.setattr(PR.TR, "native_device_profile", lambda e: PR.DP.DEFAULT)
    monkeypatch.setattr(PR.TR, "stage_poll_budget", lambda e, root=None: 15645)
    monkeypatch.setattr(PR, "LINKDIR", tmp_path / "link")
    monkeypatch.setattr(PR, "RUN_DIR", tmp_path / "run")
    (tmp_path / "run").mkdir()
    monkeypatch.setattr(PR, "stage_command", lambda build, p, e, profile, meta, audio=False: (
        ["loop_run", "--name", build, "--run", "ticks|SA2_PUMP_POLLS=15645"],
        "ticks|SA2_PUMP_POLLS=15645"))

    def reset(cmd, root):
        events.append(("reset", cmd))
        return saves
    real_require = PR.SS.require_empty

    def require(rec):
        events.append(("require_empty", rec))
        real_require(rec)

    def run_it(cmd, **kw):
        events.append(("run", cmd, kw.get("env")))
        build = cmd[cmd.index("--name") + 1]              # loop_run writes these
        for kind in ("ticks", "matrix"):
            (tmp_path / "run" / f"{build}.{kind}.json").write_text(json.dumps({"kind": kind}))
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(PR.SS, "reset_lifted", reset)
    monkeypatch.setattr(PR.SS, "require_empty", require)
    monkeypatch.setattr(PR.subprocess, "run", run_it)
    monkeypatch.setenv("SA2_REC_TRACE", "/somewhere/else")        # a shell's, never the run's
    p = PR.paths(tmp_path / "out", tmp_path / "trace" / "metalharbor.sa2s")
    return PR, RI, p, {"name": "metalharbor", "stage": True, "entry": entry}, events, saves


def test_the_stage_run_takes_the_registered_runs_guards_in_order(guarded_run, monkeypatch):
    """run_stage: the input script at its pin and no stale objects, before anything;
    then the save reset, require_empty and the run, in that order and in the runner's
    clean environment; the run's record and matrix moved into its own directory. Each
    refusal stops it before the run."""
    PR, RI, p, reg, events, saves = guarded_run
    got = PR.run_stage("present-mh.t", p, reg, {"commit": "c"})
    assert [e[0] for e in events] == ["reset", "require_empty", "run"]
    assert events[2][2] == PR.clean_env() and "SA2_REC_TRACE" not in events[2][2]
    assert got["saves"] is saves and got["rc"] == 0 and got["polls"] == 15645
    assert json.loads(p["run"].read_text()) == {"kind": "ticks"}
    assert json.loads(p["matrix"].read_text()) == {"kind": "matrix"}
    events.clear()
    off_pin = dict(reg, entry=dict(reg["entry"], input=dict(reg["entry"]["input"], sha256="0" * 64)))
    with pytest.raises(SystemExit, match="does not hash to its pin"):
        PR.run_stage("present-mh.t", p, off_pin, {})
    monkeypatch.setattr(RI, "staged_not_rebuilt", lambda: ["obj/sub_401000.o"])
    with pytest.raises(SystemExit, match="are not the current map or arm objects"):
        PR.run_stage("present-mh.t", p, reg, {})
    assert events == []                                  # neither reset nor ran
    monkeypatch.setattr(RI, "staged_not_rebuilt", lambda: [])
    saves["left"] = {"resource": "<dir>"}
    with pytest.raises(SystemExit, match="still holds resource"):
        PR.run_stage("present-mh.t", p, reg, {})
    assert [e[0] for e in events] == ["reset", "require_empty"]      # and did not run


def test_the_boot_run_takes_the_same_guards_in_order(guarded_run):
    PR, _RI, p, _reg, events, saves = guarded_run
    got = PR.run("present-boot.t", p, {"name": "boot", "stage": False, "entry": {}}, {"commit": "c"})
    assert [e[0] for e in events] == ["reset", "require_empty", "run"]
    assert events[2][2] == PR.clean_env() and got["saves"] is saves and got["rc"] == 0
    assert "SA2_REC_TRACE" not in events[2][2]
    events.clear()
    saves["left"] = {"resource": "<dir>"}
    with pytest.raises(SystemExit, match="still holds resource"):
        PR.run("present-boot.t", p, {"name": "boot", "stage": False, "entry": {}}, {})
    assert [e[0] for e in events] == ["reset", "require_empty"]


def test_the_counts_check_holds_the_buffer_watch():
    PR = runner()
    methods = [{"interface": "IDirect3DVertexBuffer9", "method": "Unlock", "calls": 3},
               {"interface": "IDirect3DDevice9", "method": "Present", "calls": 1}]
    watch = {"every_frames": 64, "set": 3, "checks": 1, "watched_at_exit": 3,
             "bytes_hashed": 96, "changed": 0}
    counts = {"stream": "closed", "methods": methods, "buffer_watch": watch}
    assert PR.buffer_watch(counts)["ok"] is True
    for bad in ({"changed": 1}, {"set": 0}, {"set": 4}):
        assert PR.buffer_watch(dict(counts, buffer_watch=dict(watch, **bad)))["ok"] is False, bad
    assert PR.buffer_watch({k: v for k, v in counts.items() if k != "buffer_watch"})["ok"] is False
    assert PR.buffer_watch({"stream": None, "methods": methods})["ok"] is True   # counting only
    assert PR.buffer_watch({"stream": "closed", "methods": [], "buffer_watch": dict(
        watch, set=0)})["ok"] is True                                            # no buffers


def test_every_stage_hook_is_compiled_afresh(linked):
    """The stage uses the same build proof as the boot, extended over every hook."""
    PR = linked.PR
    import stage_run as SR
    hooks = PR.replay_hooks({"stage": True})
    for index, src in enumerate(hooks, start=4):
        if src != PR.TR.HOOK:
            (linked.obj / f"extra{index}_{Path(src).stem}.o").write_bytes(b"stage hook")
        path = str((ROOT / src).resolve())
        if path not in linked.state["extra_c"]:
            linked.state["extra_c"].append(path)
    assert linked.check()["ok"] is True
    assert PR.check_build(linked.build, linked.matrix, hooks)["ok"] is True
    for index, src in enumerate(hooks, start=4):
        reused = f"extra{index}_{Path(src).name}"
        linked.check(compile_reused=[reused])
        got = PR.check_build(linked.build, linked.matrix, hooks)
        assert got["ok"] is False and got["hooks_reused"] == [reused], src
    assert linked.check()["ok"] is True
    gone = PR.forget_hook_objects(linked.build, hooks)
    assert len(gone) == len(SR.HOOKS) + 1 and not list(linked.obj.iterdir())


# ---------------------------------------------------------------- the pool's blocks

def test_an_object_in_the_overflow_block_is_one_the_header_declares(tmp_path):
    assert scan(tmp_path, overflow_stream(dict(BUF_HEADER, object_blocks=BLOCKS)))["problems"] == []
    # without the blocks, object_base and object_stride are the whole pool, and the
    # buffer below object_base is an object nothing introduced
    got = scan(tmp_path, overflow_stream(BUF_HEADER), name="t.sa2s")["problems"]
    assert any("0x00010000 is an object nothing earlier in the stream introduced" in p for p in got)


@pytest.mark.parametrize("blocks", [
    [[0x03200000, 0, 16384], [0x00010000, 16000, 126976]],      # an index gap
    [[0x03200010, 0, 16384], [0x00010000, 16384, 126976]],      # not object_base
    [[0x03200000, 0, 16384], [0x00010000, 16384]],               # a short row
    [[0x03200000, 0, 0]],                                         # an empty block
    "0x03200000",
])
def test_object_blocks_that_do_not_read_are_a_problem(tmp_path, blocks):
    got = scan(tmp_path, overflow_stream(dict(BUF_HEADER, object_blocks=blocks)))["problems"]
    assert any("object block" in p for p in got), got
    assert any("0x00010000 is an object nothing earlier in the stream introduced" in p for p in got)
