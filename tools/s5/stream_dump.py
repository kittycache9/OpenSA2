#!/usr/bin/env python3
"""Read an S5 Direct3D 9 call stream back, and say whether it is whole and true to
its own rules.

Subtitle: the reference reader of the stream host/sa2_present_rec.c writes and
host/sa2_stream.h describes. It checks the framing record by record, holds every
record's blobs to the rule table the header carries, evaluated here by its own
code, counts records per kind, calls per (interface, slot), entry-point calls per
entry, blobs per role, and frames, and prints any range of records in full. With a
run record it also holds the stream to what the recording device itself recorded.

Framing. A stream is whole when the header reads, every record's `bytes` is a
multiple of 4 and inside the file, `seq` runs 0, 1, 2, ... without a gap, every
SA2S_CALL and SA2S_D3DX is filled exactly by its fields and blobs (each padded to
4 with zero bytes), every SA2S_CALL's argc is the one the header's method table
gives its slot and every SA2S_D3DX's the one its entries table gives, and the last
record is SA2S_END with nothing after it. The header reads when its JSON, its schema
and its tables' shapes do (read_header); a file whose header does not is not a
stream (StreamError), and a rule or entry row that does not read is a problem of
that row.

Content. Every blob's role is one of the six and its arg is one of the call's or
0xFFFF; every record carries exactly the blobs its rule gives for its arguments
and answer, each of the length the rule gives (a shader walked token by token, a
declaration to its end element, a region to its own count); a Present's FRAME is
the next record, a FRAME follows nothing else, and no FRAME's clock or frame count
is below the one before; an Unlock's LOCK describes the lock the reader saw opened
(the Lock's answer, flags and rectangle, the level's shape from the calls that made
it), its bytes are its pitch times its rows whenever those are known (a buffer's,
the size its Lock covers whenever that is known), so an upload the writer could not
copy is a problem whatever the writer noted, and its LOCKED blob is LOCK.bytes long;
every object called, and every object argument that names one, was introduced
earlier in the stream (by an out-parameter that answers an object, OBJECT_OUTS, of a call that
answered 0; CreateDevice's description, a load's description, or Direct3DCreate9's answer).
Given host/sa2_stream.h's text (--format), the header's rule and entry tables must
be its lines, both ways.

Audio (version 2). A version 2 stream is a version 1 stream that may also hold
SA2S_AUDIO records, with schema sa2-d3d9-stream/v2 and an "audio" op table in its
header, whose ds_state_bytes and xa_state_bytes are the two STATE structs' sizes; a
version 1 stream holds none. Every AUDIO record's argc is its op's in that table,
its api is its op's interface's, its blobs are exactly the ones its op's line gives
(each PCM blob the length its arguments, or its XAUDIO2_BUFFER's AudioBytes, say; the
level matrix the bytes its STATE holds; an Unlock's HASH exactly when it leaves no
Lock open on its store), its STATE describes the object the call was on or wrote (a
buffer's, a voice's) and, for a call that re-anchors, is anchored at the call's own
time, a submit's frames end at its STATE's queued_end, every buffer and voice it
calls was introduced earlier by a call that made it, and no AUDIO or FRAME record's
clock is below the timed record's before it. With --format the header's audio table
must be the format's `audio` lines, both ways; with --counts the recorder's per-op
counts must be the stream's, and its store watch's counters the ones the stream's own
Lock, Unlock and FRAME records give; with --run the records, and the counts, are held
to the audio devices' own record of the run (hold_audio_to_run).

The buffer watch. The recorder hashes each buffer's bytes after the Unlock that
closed its last lock (host/sa2_present_rec.c, watch_set and watch_check) and counts
what it did in the counts file's "buffer_watch". The reader recomputes the same
counters from the stream's own Unlock, Lock, Release and FRAME records
(summary["buffer_watch"]), and compare_counts holds the counts file to them, so a
watch that stops checking, or checks less than it says, does not pass.

Anything else is a problem, and a problem is the exit status: this reader is the
instrument the recorder is held to, so it never skips a check quietly. What it
cannot parse it stops at, and says where. A NOTE record is the writer saying it left
something out; the reader counts them and the runner refuses a stream with any. A state
note (text beginning "state ": the game's state for the player's touch controls, written
in live play alone, host/sa2_stream.h) leaves nothing out, and is counted apart as
"state_records".

Usage:
  .venv/bin/python tools/s5/stream_dump.py STREAM               # summary
  .venv/bin/python tools/s5/stream_dump.py STREAM --json OUT    # the summary as JSON
  .venv/bin/python tools/s5/stream_dump.py STREAM --range 200:240   # seq 200..239
  .venv/bin/python tools/s5/stream_dump.py STREAM --counts COUNTS.json
      # hold the stream's per-method, per-entry and Present counts, and the
      # buffer watch recomputed from the stream, to the recorder's own counts
      # file (SA2_REC_COUNTS)
  .venv/bin/python tools/s5/stream_dump.py STREAM --run RUN.json [--imports TABLE.json]
      # hold it to the run's own record: every FILE blob to the recording device's
      # hash of that texture's file, every load's DESC to the device's row, the
      # first calls to the device's call log, each entry's argc to the import
      # table's, and the stream's own counts to the device's (hold_counts_to_run:
      # the calls per method to d3d9.dynamic_surface, the methods called, FRAMEs to
      # the Presents, calls and entry calls to method_calls, each entry's records
      # to its import's calls, and the objects the stream introduces to the
      # objects the device built)
  .venv/bin/python tools/s5/stream_dump.py STREAM --format host/sa2_stream.h
      # hold the header's rule and entry tables to the format's own lines
  (a version 2 stream with --run is also held to the run's audio devices: their
  per-method calls and call logs, and with --counts the recorder's per-op counts)

Exit status: 0 whole (and, with --counts or --run, equal); 1 problems or not a
stream; 2 usage.

SA2_STREAM_DUMP_BREAK=<name> switches one check off, so
Documentation/scripts/tests/test_s5_stream.py can show each check is what fails a
bad stream: seq, end, tail, fill, pad, u64, trunc, argc (framing); rules, frame,
clock, lock, walk, padzero, blobhead, objects, entryargc, table (content); watch (the buffer watch held to counts); runcounts
(the counts held to the run record); aurules, auclock, auobjects, autable, auwatch (the store watch
held to counts), auheld (the audio held to the run record) (audio). Nothing else sets it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import mmap
import os
import re
import struct
import sys
from pathlib import Path

MAGIC = b"SA2D3D9S"
VERSION = 1
SCHEMA = "sa2-d3d9-stream/v1"
# A stream with audio: version 1 and the AUDIO kind (host/sa2_stream.h, "Versions").
VERSION_AUDIO = 2
SCHEMA_AUDIO = "sa2-d3d9-stream/v2"
SCHEMAS = {VERSION: SCHEMA, VERSION_AUDIO: SCHEMA_AUDIO}
CALL, D3DX, FRAME, NOTE, END, AUDIO = 1, 2, 3, 4, 5, 6
KIND_NAMES = {CALL: "call", D3DX: "d3dx", FRAME: "frame", NOTE: "note", END: "end", AUDIO: "audio"}
ROLE_NAMES = {1: "in", 2: "out", 3: "locked", 4: "file", 5: "desc", 6: "lock", 7: "pcm", 8: "state",
              9: "hash"}
IN, OUT, LOCKED, FILE, DESC, LOCK, PCM, STATE, HASH = 1, 2, 3, 4, 5, 6, 7, 8, 9
D3DX_NAMES = {1: "D3DXCreateTextureFromFileInMemoryEx", 2: "D3DXCreateTexture",
              3: "D3DXFilterTexture", 4: "D3DXLoadSurfaceFromSurface",
              5: "D3DXGetShaderConstantTable", 6: "D3DXCreateCubeTextureFromFileInMemoryEx",
              7: "D3DXCreateVolumeTextureFromFileInMemoryEx", 8: "D3DXCreateCubeTexture",
              9: "D3DXCreateVolumeTexture", 10: "D3DXSaveSurfaceToFileA",
              11: "D3DXSaveTextureToFileA", 12: "Direct3DCreate9"}
DIRECT3D_CREATE9 = 12
NO_ARG = 0xFFFF
MAX_ARGC = 64            # no Direct3D 9 method takes more; a larger one is a misread
BREAKS = ("seq", "end", "tail", "fill", "pad", "u64", "trunc", "argc",
          "rules", "frame", "clock", "lock", "walk", "padzero", "blobhead", "objects",
          "entryargc", "table", "watch", "runcounts", "aurules", "auclock", "auobjects",
          "autable", "auwatch", "auheld")
# The buffer watch's period in FRAMEs: host/sa2_present_rec.c's REC_WATCH_EVERY
# (test_s5_stream.py holds the two equal), and the interfaces whose final Release
# ends a buffer's watch.
WATCH_EVERY = 64
BUFFER_IFACES = ("IDirect3DVertexBuffer9", "IDirect3DIndexBuffer9")
WATCH_KEYS = ("every_frames", "set", "checks", "bytes_hashed", "watched_at_exit")

HEAD = struct.Struct("<III")
CALL_FIXED = struct.Struct("<HHIII")      # type, slot, this, thread, argc
U32 = struct.Struct("<I")
U32X2 = struct.Struct("<II")
BLOB = struct.Struct("<HHI")
FRAME_BODY = struct.Struct("<IIII")       # virtual_us low, high, frames, polls
LOCK_INFO = struct.Struct("<11I4i2I")     # sa2s_lock_info, 68 bytes
DESC_DEVICE = struct.Struct("<3I")        # sa2s_desc_device
DESC_FILE = struct.Struct("<22I")         # sa2s_desc_file_texture
PP = struct.Struct("<14I")                # D3DPRESENT_PARAMETERS, the guest's layout
AUDIO_FIXED = struct.Struct("<HHIIIII")   # api, op, this, thread, virtual_us low, high, argc
DS_STATE = struct.Struct("<21I")          # sa2s_ds_state, 84 bytes
XA_STATE = struct.Struct("<44I")          # sa2s_xa_state, 176 bytes
XBUF = struct.Struct("<9I")               # XAUDIO2_BUFFER, 36 bytes
# The fields of the two STATE structs, in order (host/sa2_stream.h).
DS_FIELDS = ("buffer", "index", "flags", "bytes", "primary", "format_tag", "channels",
             "samples_per_sec", "avg_bytes_per_sec", "block_align", "bits_per_sample", "playing",
             "looping", "halted", "released", "anchor_pos", "anchor_us_lo", "anchor_us_hi", "mem",
             "mem_len", "write_lead")
XA_FIELDS = (("voice", "index", "kind", "engine", "flags", "destroyed", "details_flags",
              "input_channels", "input_sample_rate", "format_tag", "channels", "samples_per_sec",
              "avg_bytes_per_sec", "block_align", "bits_per_sample", "valid_bits", "channel_mask",
              "max_ratio_bits", "ratio_bits", "output", "matrix_src", "matrix_dst")
             + tuple(f"matrix{k}" for k in range(12))
             + ("running", "queued", "anchor_us_lo", "anchor_us_hi", "anchor_frame_lo",
                "anchor_frame_hi", "played_lo", "played_hi", "queued_end_lo", "queued_end_hi"))
# The audio ops' grammar (host/sa2_stream.h, "What each audio call carries").
AOPERANDS = {"out": "an", "in": "abc", "pcm": "ab", "submit": "a", "ds_state": "", "ds_new": "a",
             "xa_state": "", "xa_new": "a", "store_hash": ""}
AUDIO_APIS = {1: "dsound", 2: "xaudio2"}
AUDIO_IFACES = {"IDirectSound8": 1, "IDirectSoundBuffer8": 1, "IXAudio2": 2,
                "IXAudio2MasteringVoice": 2, "IXAudio2SourceVoice": 2}
# `in A B C`: no blob past a count of this (host/sa2_stream.h; the recorder's AU_MATRIX_MAX).
MATRIX_MAX = 64
# The ops whose STATE holds the call's own virtual time as anchor_us: each re-anchors
# its buffer or voice (runtime/sa2_dsound.c buf_anchor; runtime/sa2_xaudio2.c anchor).
REANCHOR = frozenset({"DS_PLAY", "DS_STOP", "DS_SET_POSITION", "DS_SET_FREQUENCY", "DS_SET_FORMAT",
                      "XA_STOP", "XA_SET_RATIO"})
# The ops recorded whatever they answer (a Release's count, a DestroyVoice's nothing);
# every other op only when its answer is not negative (host/sa2_stream.h, SA2S_AUDIO).
ANY_ANSWER = frozenset({"DS_RELEASE", "XA_DESTROY", "XA_MASTER_DESTROY"})
# The two words each audio device logs as a call's out0 and out1 (runtime/sa2_dsound.c
# and runtime/sa2_xaudio2.c, c->out0 and c->out1), as the call's record carries them: a
# blob's (role, arg, byte offset), or a STATE field by name. Every other op logs 0 and 0,
# which says nothing of its record.
LOG_OUTS = {"DS_CREATE_BUFFER": ((OUT, 1, 0), None), "DS_DUPLICATE": ((OUT, 1, 0), None),
            "DS_LOCK": ((OUT, 2, 0), (OUT, 3, 0)), "DS_GET_POSITION": ((OUT, 0, 0), (OUT, 1, 0)),
            "DS_GET_STATUS": ((OUT, 0, 0), None),
            "XA_CREATE_MASTER": ((OUT, 0, 0), None), "XA_CREATE_SOURCE": ((OUT, 0, 0), None),
            "XA_MASTER_DETAILS": ((OUT, 0, 4), (OUT, 0, 8)), "XA_GET_STATE": ((OUT, 0, 4), (OUT, 0, 8)),
            "XA_SUBMIT": ((STATE, NO_ARG, "queued"), None)}
# The run record's audio devices, by api.
AUDIO_DEVICES = ((1, "dsound"), (2, "xaudio2"))

# Each op of host/sa2_stream.h's rule grammar, and the operands its text names.
OPERANDS = {"in": "an", "out": "an", "in_vec": "abn", "out_vec": "abn", "out_sized": "abn",
            "shader": "a", "decl": "a", "up": "", "iup": "", "pre_pp": "a", "pre_rect": "a",
            "pre_rgn": "a", "frame": "", "device": "", "file": "ab", "file_desc": "a",
            "object_eax": "", "new_tex": "abcd", "new_vol": "abcd", "new_cube": "abd",
            "new_surf": "abcd", "new_buf": "ab", "level": "a", "lock_surf": "",
            "unlock_surf": "", "lock_tex": "", "unlock_tex": "", "lock_buf": "",
            "unlock_buf": ""}
BLOCK_FORMATS = frozenset({0x31545844, 0x32545844, 0x33545844, 0x34545844, 0x35545844})
RGN_RECTS = 16
# Arguments that name an object, by method: each must be 0 or an object the stream
# introduced earlier. The entry points' device argument is its first.
OBJECT_ARGS = {("IDirect3DDevice9", "SetTexture"): (1,),
               ("IDirect3DDevice9", "SetVertexShader"): (0,),
               ("IDirect3DDevice9", "SetPixelShader"): (0,),
               ("IDirect3DDevice9", "SetVertexDeclaration"): (0,),
               ("IDirect3DDevice9", "SetIndices"): (0,),
               ("IDirect3DDevice9", "SetStreamSource"): (1,),
               ("IDirect3DDevice9", "SetRenderTarget"): (1,),
               ("IDirect3DDevice9", "SetDepthStencilSurface"): (0,),
               ("IDirect3DDevice9", "UpdateSurface"): (0, 2),
               ("IDirect3DDevice9", "UpdateTexture"): (0, 1),
               ("IDirect3DDevice9", "StretchRect"): (0, 2),
               ("IDirect3DDevice9", "ColorFill"): (0,),
               ("IDirect3DDevice9", "GetRenderTargetData"): (0, 1)}
ENTRY_OBJECT_ARGS = {"D3DXCreateTextureFromFileInMemoryEx": (0,), "D3DXCreateTexture": (0,),
                     "D3DXFilterTexture": (0,), "D3DXLoadSurfaceFromSurface": (0, 3),
                     "D3DXCreateCubeTextureFromFileInMemoryEx": (0,),
                     "D3DXCreateVolumeTextureFromFileInMemoryEx": (0,),
                     "D3DXCreateCubeTexture": (0,), "D3DXCreateVolumeTexture": (0,),
                     "D3DXSaveSurfaceToFileA": (2,), "D3DXSaveTextureToFileA": (2,)}
# The rule table's 4-byte out-parameters, by method ("*": every interface without a
# line of its own) and argument: the ones that answer an object, whose OUT word
# introduces that object when the call answered 0, and the ones that answer a value
# (a count, a size, a state, a data pointer), which introduce nothing. An OUT blob is
# read whatever the call answered, so a call that failed carries the guest's own
# variable there, not an object. test_s5_stream.py holds the two tables to the rule
# lines of host/sa2_stream.h: every `out A 4` is in exactly one of them.
OBJECT_OUTS = {("IDirect3D9", "CreateDevice"): (5,),
               ("IDirect3DDevice9", "GetDirect3D"): (0,),
               ("IDirect3DDevice9", "CreateAdditionalSwapChain"): (1,),
               ("IDirect3DDevice9", "GetSwapChain"): (1,),
               ("IDirect3DDevice9", "GetBackBuffer"): (3,),
               ("IDirect3DDevice9", "CreateTexture"): (6,),
               ("IDirect3DDevice9", "CreateVolumeTexture"): (7,),
               ("IDirect3DDevice9", "CreateCubeTexture"): (5,),
               ("IDirect3DDevice9", "CreateVertexBuffer"): (4,),
               ("IDirect3DDevice9", "CreateIndexBuffer"): (4,),
               ("IDirect3DDevice9", "CreateRenderTarget"): (6,),
               ("IDirect3DDevice9", "CreateDepthStencilSurface"): (6,),
               ("IDirect3DDevice9", "CreateOffscreenPlainSurface"): (4,),
               ("IDirect3DDevice9", "GetRenderTarget"): (1,),
               ("IDirect3DDevice9", "GetDepthStencilSurface"): (0,),
               ("IDirect3DDevice9", "CreateStateBlock"): (1,),
               ("IDirect3DDevice9", "EndStateBlock"): (0,),
               ("IDirect3DDevice9", "GetTexture"): (1,),
               ("IDirect3DDevice9", "CreateVertexDeclaration"): (1,),
               ("IDirect3DDevice9", "GetVertexDeclaration"): (0,),
               ("IDirect3DDevice9", "CreateVertexShader"): (1,),
               ("IDirect3DDevice9", "GetVertexShader"): (0,),
               ("IDirect3DDevice9", "GetStreamSource"): (1,),
               ("IDirect3DDevice9", "GetIndices"): (0,),
               ("IDirect3DDevice9", "CreatePixelShader"): (1,),
               ("IDirect3DDevice9", "GetPixelShader"): (0,),
               ("IDirect3DDevice9", "CreateQuery"): (1,),
               ("IDirect3DTexture9", "GetSurfaceLevel"): (1,),
               ("IDirect3DCubeTexture9", "GetCubeMapSurface"): (2,),
               ("IDirect3DSwapChain9", "GetBackBuffer"): (2,),
               ("*", "QueryInterface"): (1,),
               ("*", "GetDevice"): (0,),
               ("*", "GetContainer"): (1,)}
VALUE_OUTS = {("IDirect3D9", "CheckDeviceMultiSampleType"): (5,),     # quality levels
              ("IDirect3DDevice9", "GetLightEnable"): (1,),
              ("IDirect3DDevice9", "GetRenderState"): (1,),
              ("IDirect3DDevice9", "GetTextureStageState"): (2,),
              ("IDirect3DDevice9", "GetSamplerState"): (2,),
              ("IDirect3DDevice9", "ValidateDevice"): (0,),           # passes
              ("IDirect3DDevice9", "GetCurrentTexturePalette"): (0,),
              ("IDirect3DDevice9", "GetFVF"): (0,),
              ("IDirect3DDevice9", "GetStreamSource"): (2, 3),        # offset, stride
              ("IDirect3DDevice9", "GetStreamSourceFreq"): (1,),
              ("IDirect3DVertexBuffer9", "Lock"): (2,),               # the data pointer
              ("IDirect3DIndexBuffer9", "Lock"): (2,),
              ("IDirect3DVertexDeclaration9", "GetDeclaration"): (1,),   # the element count
              ("IDirect3DVertexShader9", "GetFunction"): (1,),        # the size
              ("IDirect3DPixelShader9", "GetFunction"): (1,),
              ("ID3DXConstantTable", "GetConstantDesc"): (2,),
              ("*", "GetPrivateData"): (2,)}
ENTRY_OBJECT_OUTS = {"D3DXCreateTextureFromFileInMemoryEx": (14,), "D3DXCreateTexture": (7,),
                     "D3DXGetShaderConstantTable": (1,),
                     "D3DXCreateCubeTextureFromFileInMemoryEx": (13,),
                     "D3DXCreateVolumeTextureFromFileInMemoryEx": (15,),
                     "D3DXCreateCubeTexture": (6,), "D3DXCreateVolumeTexture": (8,)}
# The recording device's synthetic slots for the d3dx entries in its call log
# (runtime/sa2_d3d9.c, DXS_*). Direct3DCreate9 is not in that log.
DEVICE_SLOT_ENTRY = {255: 2, 254: 1, 253: 8, 252: 6, 251: 9, 250: 7, 249: 3, 248: 4,
                     247: 10, 246: 11, 245: 5}
# The calls that take the frame's wait (runtime/sa2_d3d9.c, frame_wait), the one
# place another guest thread's calls can run, and finish, inside a call.
FRAME_WAIT = frozenset({("IDirect3DDevice9", "BeginScene"), ("IDirect3DDevice9", "Present"),
                        ("IDirect3DSwapChain9", "Present")})
# A load's description against the device's row (hold_to_run): the names the run
# record gives the numbers (runtime/sa2_d3d9.c, sa2_d3d9_container_name and
# sa2_d3d9_provenance_name), and the texture kind each load entry makes.
CONTAINER_NAMES = {1: "dds", 2: "png", 3: "bmp", 4: "jpeg", 5: "gif", 6: "tiff",
                   7: "radiance-hdr", 8: "ppm"}
PROVENANCE_NAMES = {1: "the file's header states it",
                    2: "derived from the header by D3DX's published rule",
                    3: "the guest passed it explicitly",
                    4: "D3DX's documented default for D3DX_DEFAULT",
                    5: "invented by SA2_D3D9_BREAK=texguess"}
LOAD_KIND = {1: 0, 6: 1, 7: 2}           # SA2_TEXK_*: 2D, cube, volume
# host/sa2_stream.h's table lines: `rule <iface>::<method> <ops>` and
# `entry <n> <name> <argc> <ops>`, after ` * ` in its comment; text after `#` is a
# comment.
RULE_LINE = re.compile(r"^\s*\*\s+rule\s+(\S+)::(\S+)\s+(.+?)\s*$")
ENTRY_LINE = re.compile(r"^\s*\*\s+entry\s+(\d+)\s+(\S+)\s+(\d+)\s+(.+?)\s*$")
AUDIO_LINE = re.compile(r"^\s*\*\s+audio\s+(\d+)\s+(\S+)\s+(\S+)::(\S+)\s+(\d+)\s+(.+?)\s*$")


def object_outs(iface: str, method: str) -> tuple[int, ...]:
    """The arguments of `iface::method` whose OUT word is an object, resolved as the
    rule table resolves a `*` line: a method with a line of its own takes its own."""
    if (iface, method) in OBJECT_OUTS or (iface, method) in VALUE_OUTS:
        return OBJECT_OUTS.get((iface, method), ())
    return OBJECT_OUTS.get(("*", method), ())


class StreamError(Exception):
    """The file is not a stream at all: no magic, a header that does not read."""


class Problems(list):
    """A stream's problems: the first KEEP in full, the rest counted, so a stream
    whose every record is wrong still reads in bounded memory."""
    KEEP = 200

    def __init__(self) -> None:
        super().__init__()
        self.more = 0

    def append(self, text: str) -> None:
        if len(self) < self.KEEP:
            super().append(text)
        else:
            self.more += 1

    def closed(self) -> list[str]:
        return list(self) + ([f"{self.more} further problems are counted, not listed"]
                             if self.more else [])


def pad4(n: int) -> int:
    return (n + 3) & ~3


def active_break() -> str:
    b = os.environ.get("SA2_STREAM_DUMP_BREAK", "")
    if b and b not in BREAKS:
        raise SystemExit(f"SA2_STREAM_DUMP_BREAK={b!r} is not one of {', '.join(BREAKS)}")
    return b


def parse_ops(text: str) -> list[tuple[str, dict[str, int]]]:
    """An op list in host/sa2_stream.h's grammar ('out 2 1100; in 0 16', or '-'), as
    [(op, {operand: value})]. ValueError for an op this reader does not know or the
    wrong number of operands; TypeError for a list that is not text."""
    if not isinstance(text, str):
        raise TypeError(f"an op list that is not text: {text!r}")
    text = text.strip()
    if text == "-":
        return []
    out = []
    for part in text.split(";"):
        words = part.split()
        if not words:
            raise ValueError(f"an empty op in {text!r}")
        name, nums = words[0], words[1:]
        want = OPERANDS.get(name)
        if want is None:
            raise ValueError(f"op {name!r} is not one this reader knows")
        if len(nums) != len(want):
            raise ValueError(f"op {name!r} takes {len(want)} operands, not {len(nums)}")
        out.append((name, {k: int(v) for k, v in zip(want, nums)}))
    return out


def ops_text(ops: list[tuple[str, dict[str, int]]]) -> str:
    """The canonical text of an op list: parse_ops's inverse."""
    if not ops:
        return "-"
    return "; ".join(" ".join([name] + [str(o[k]) for k in OPERANDS[name]]) for name, o in ops)


def parse_aops(text: str) -> list[tuple[str, dict[str, int]]]:
    """An audio op list in host/sa2_stream.h's grammar ('pcm 0 1; ds_state', or '-'),
    as parse_ops reads a rule's. ValueError for an op this reader does not know or the
    wrong number of operands; TypeError for a list that is not text."""
    if not isinstance(text, str):
        raise TypeError(f"an audio op list that is not text: {text!r}")
    text = text.strip()
    if text == "-":
        return []
    out = []
    for part in text.split(";"):
        words = part.split()
        if not words:
            raise ValueError(f"an empty op in {text!r}")
        name, nums = words[0], words[1:]
        want = AOPERANDS.get(name)
        if want is None:
            raise ValueError(f"audio op {name!r} is not one this reader knows")
        if len(nums) != len(want):
            raise ValueError(f"audio op {name!r} takes {len(want)} operands, not {len(nums)}")
        out.append((name, {k: int(v) for k, v in zip(want, nums)}))
    return out


def aops_text(ops: list[tuple[str, dict[str, int]]]) -> str:
    """The canonical text of an audio op list: parse_aops's inverse."""
    if not ops:
        return "-"
    return "; ".join(" ".join([name] + [str(o[k]) for k in AOPERANDS[name]]) for name, o in ops)


def object_blocks(head: dict, problems) -> list[tuple[int, int, int]]:
    """The header's "object_blocks" (host/sa2_stream.h): [(base, first index, count)],
    or [] for a stream that has none, whose object_base and object_stride are then the
    whole pool. A block that does not read, or that does not follow the one before it in
    index, is a problem, and the stream's objects are then read as having no blocks."""
    rows = head.get("object_blocks")
    if rows is None:
        return []
    out: list[tuple[int, int, int]] = []
    for row in rows if isinstance(rows, list) else [rows]:
        if (not isinstance(row, list) or len(row) != 3
                or not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in row)
                or row[1] != (out[-1][1] + out[-1][2] if out else 0) or row[2] == 0):
            problems.append(f"the header's object block {row!r} does not read as [base, first "
                            f"index, count] following the block before it")
            return []
        out.append((row[0], row[1], row[2]))
    if out and out[0][0] != head.get("object_base"):
        problems.append(f"the header's first object block starts at {out[0][0]:#x}, and its "
                        f"object_base is {head.get('object_base')!r}")
        return []
    return out


def doc_table(text: str) -> tuple[dict, dict, dict]:
    """host/sa2_stream.h's rule, entry and audio lines: ({(iface, method): ops}, {entry:
    (name, argc, ops)}, {op: (NAME, iface, method, argc, aops)}), each op list in its
    canonical text. ValueError for a line whose ops do not read, a method with two rule
    lines, or an audio op with two lines."""
    rules, entries, audio = {}, {}, {}
    for line in text.splitlines():
        line = line.split("#")[0]
        if m := RULE_LINE.match(line):
            if (m[1], m[2]) in rules:
                raise ValueError(f"{m[1]}::{m[2]} has two rule lines")
            rules[(m[1], m[2])] = ops_text(parse_ops(m[3]))
        elif m := ENTRY_LINE.match(line):
            entries[int(m[1])] = (m[2], int(m[3]), ops_text(parse_ops(m[4])))
        elif m := AUDIO_LINE.match(line):
            if int(m[1]) in audio:
                raise ValueError(f"audio op {m[1]} has two lines")
            audio[int(m[1])] = (m[2], m[3], m[4], int(m[5]), aops_text(parse_aops(m[6])))
    return rules, entries, audio


def audio_table(head: dict) -> dict[int, tuple[str, str, str, int, list]]:
    """The header's audio op table: {op: (NAME, iface, method, argc, aops parsed)}.
    ValueError or TypeError for a row that does not read."""
    out = {}
    for row in ((head.get("audio") or {}).get("ops") or []):
        op, name, iface, method, argc, text = row
        if not (whole(op) and whole(argc) and all(isinstance(x, str) for x in (name, iface, method))):
            raise TypeError(f"the audio row {row!r} is not [op, NAME, interface, method, argc, aops]")
        if op in out:
            raise ValueError(f"the header names audio op {op} twice")
        out[op] = (name, iface, method, argc, parse_aops(text))
    return out


def table_problems(head: dict, doc: tuple) -> list[str]:
    """Where a header's "rules" and "entries" are not the format's lines (doc_table),
    both ways: every rule is its method's line (or the `*` line for its name), every
    line is some method's rule, every argument an op names is one its method or entry
    has, and the entries are the entry lines. A header with an "audio" table (version
    2) holds it to the format's `audio` lines the same way, both ways."""
    doc_rules, doc_entries = doc[0], doc[1]
    doc_audio = doc[2] if len(doc) > 2 else {}
    ifaces = head.get("interfaces") or []
    names = {}
    for t, s, name, argc in head.get("methods") or []:
        names[(t, s)] = (ifaces[t] if t < len(ifaces) else f"type{t}", name, argc)
    out, used = [], set()

    def past_argc(what: str, ops, argc) -> None:
        for name, o in ops:
            if any(v >= argc for k, v in o.items() if k != "n"):
                out.append(f"{what}: {name} {o} names an argument past its {argc}")

    for row in head.get("rules") or []:
        try:
            t, s, text = row
            if not (whole(t) and whole(s)):
                raise TypeError("its type and slot are not whole numbers")
            ops = parse_ops(text)
        except (ValueError, TypeError) as e:
            out.append(f"the header's rule {row!r} does not read: {e}")
            continue
        if (t, s) not in names:
            out.append(f"the header has a rule for {t}.{s}, which its method table does not name")
            continue
        iface, method, argc = names[(t, s)]
        key = (iface, method) if (iface, method) in doc_rules else ("*", method)
        if key not in doc_rules:
            out.append(f"the header's rule for {iface}::{method} is no line of the format")
            continue
        used.add(key)
        if ops_text(ops) != doc_rules[key]:
            out.append(f"{iface}::{method}: the header's rule is {ops_text(ops)!r}, the format's "
                       f"line {doc_rules[key]!r}")
        past_argc(f"{iface}::{method}", ops, argc)
    out += [f"the format's line for {i}::{m} is no method's rule in the header"
            for i, m in sorted(set(doc_rules) - used)]
    got = {}
    for row in head.get("entries") or []:
        try:
            e, name, argc, text = row
            if not (whole(e) and whole(argc) and isinstance(name, str)):
                raise TypeError("its entry and argc are not whole numbers and a name")
            ops = parse_ops(text)
        except (ValueError, TypeError) as err:
            out.append(f"the header's entry {row!r} does not read: {err}")
            continue
        got[e] = (name, argc, ops_text(ops))
        past_argc(f"entry {e} {name}", ops, argc)
    for e in sorted(set(got) | set(doc_entries)):
        if got.get(e) != doc_entries.get(e):
            out.append(f"entry {e}: the header says {got.get(e)!r}, the format's line "
                       f"{doc_entries.get(e)!r}")
    if "audio" in head and active_break() != "autable":
        try:
            table = audio_table(head)
        except (ValueError, TypeError) as err:
            return out + [f"the header's audio table does not read: {err}"]
        mine = {op: (n, i, m, a, aops_text(ops)) for op, (n, i, m, a, ops) in table.items()}
        for op in sorted(set(mine) | set(doc_audio)):
            if mine.get(op) != doc_audio.get(op):
                out.append(f"audio op {op}: the header says {mine.get(op)!r}, the format's line "
                           f"{doc_audio.get(op)!r}")
        for op, (n, _i, _m, argc, ops) in table.items():
            for name, o in ops:
                if any(v >= argc for k, v in o.items() if k != "n"):
                    out.append(f"audio op {op} {n}: {name} {o} names an argument past its {argc}")
    return out


def whole(v) -> bool:
    """A whole number as JSON gives one: an int, not a bool, not below 0."""
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def table_shape_problem(head: dict) -> str | None:
    """Why the header's tables are not the shapes every reader of them indexes, or
    None: "interfaces" a list of names; "methods" a list of [type, slot, name, argc]
    rows, the three numbers whole; "rules" and "entries" lists. (A rule or entry row
    that does not read is a problem the scan reports, row by row.)"""
    ifaces = head.get("interfaces")
    if ifaces is not None and not (isinstance(ifaces, list)
                                   and all(isinstance(n, str) for n in ifaces)):
        return f"its interfaces are not a list of names: {str(ifaces)[:80]}"
    for key in ("methods", "rules", "entries"):
        if head.get(key) is not None and not isinstance(head[key], list):
            return f"its {key} are not a list of rows: {str(head[key])[:80]}"
    au = head.get("audio")
    if au is not None:
        if not isinstance(au, dict) or not isinstance(au.get("ops"), list):
            return f"its audio table is not an object with a list of ops: {str(au)[:80]}"
        for row in au["ops"]:
            if not (isinstance(row, list) and len(row) == 6 and whole(row[0]) and whole(row[4])
                    and all(isinstance(x, str) for x in (row[1], row[2], row[3], row[5]))):
                return f"its audio row {str(row)[:80]} is not [op, NAME, interface, method, argc, aops]"
    for row in head.get("methods") or []:
        if not (isinstance(row, list) and len(row) == 4 and whole(row[0]) and whole(row[1])
                and isinstance(row[2], str) and whole(row[3])):
            return f"its method row {str(row)[:80]} is not [type, slot, name, argc]"
    return None


def read_header(buf) -> tuple[dict, int]:
    """The header's JSON and its length in bytes. StreamError when the file is not a
    stream: no magic, another version, a header that does not read as JSON, another
    schema, or tables that are not the shapes table_shape_problem names."""
    if len(buf) < 16 or bytes(buf[:8]) != MAGIC:
        raise StreamError("no SA2D3D9S magic: not a stream")
    version, hb = U32X2.unpack_from(buf, 8)
    if version not in SCHEMAS:
        raise StreamError(f"version {version}; this reader reads {VERSION} and {VERSION_AUDIO}")
    if hb < 16 or hb % 4 or hb > len(buf):
        raise StreamError(f"header_bytes {hb} is not a multiple of 4 inside the file")
    try:
        head = json.loads(bytes(buf[16:hb]).decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise StreamError(f"the header's JSON does not read: {e}") from None
    if not isinstance(head, dict) or head.get("schema") != SCHEMAS[version]:
        raise StreamError(f"the header's schema is {head.get('schema') if isinstance(head, dict) else head!r}, "
                          f"not {SCHEMAS[version]} (version {version})")
    if (version == VERSION_AUDIO) != ("audio" in head):
        raise StreamError(f"a version {version} header {'without' if version == VERSION_AUDIO else 'with'} "
                          f"an audio table")
    why = table_shape_problem(head)
    if why:
        raise StreamError(f"the header's tables do not read: {why}")
    return head, hb


def header_meta(head: dict):
    """The header's meta, a JSON string, read: a dict, None when absent, or the
    string itself when it is not a JSON object (a problem scan() reports)."""
    meta = head.get("meta")
    if meta is None or isinstance(meta, dict):
        return meta
    try:
        got = json.loads(meta)
    except (TypeError, ValueError):
        return meta
    return got if isinstance(got, dict) else meta


def method_table(head: dict) -> dict[tuple[int, int], tuple[str, str, int]]:
    """(type, slot) -> (interface, method, argc), from the header."""
    ifaces = head.get("interfaces") or []
    out = {}
    for row in head.get("methods") or []:
        t, slot, name, argc = row
        out[(t, slot)] = (ifaces[t] if t < len(ifaces) else f"type{t}", name, argc)
    return out


def _blobs(buf, p: int, n: int, brk: str) -> tuple[list[tuple[int, int, int, int]], int]:
    """n blobs from p: [(role, arg, offset of the data, len)], and the offset after."""
    out = []
    for _ in range(n):
        if p + 8 > len(buf):
            raise IndexError(p)
        role, arg, blen = BLOB.unpack_from(buf, p)
        out.append((role, arg, p + 8, blen))
        p += 8 + (blen if brk == "pad" else pad4(blen))
    return out, p


def decode(buf, off: int, kind: int, nbytes: int, brk: str = "") -> dict:
    """One record's payload, decoded; blob data as (role, arg, offset, len)."""
    end = off + nbytes
    if kind == CALL:
        t, slot, this, thread, argc = CALL_FIXED.unpack_from(buf, off + 12)
        if argc > MAX_ARGC:
            raise ValueError(f"argc {argc}")
        p = off + 28
        args = struct.unpack_from(f"<{argc}I", buf, p)
        p += 4 * argc
        eax, nb = U32X2.unpack_from(buf, p)
        blobs, p = _blobs(buf, p + 8, nb, brk)
        return {"type": t, "slot": slot, "this": this, "thread": thread, "args": args,
                "eax": eax, "blobs": blobs, "end": p, "fills": p == end}
    if kind == D3DX:
        entry, argc = U32X2.unpack_from(buf, off + 12)
        if argc > MAX_ARGC:
            raise ValueError(f"argc {argc}")
        p = off + 20
        args = struct.unpack_from(f"<{argc}I", buf, p)
        p += 4 * argc
        eax, nb = U32X2.unpack_from(buf, p)
        blobs, p = _blobs(buf, p + 8, nb, brk)
        return {"entry": entry, "args": args, "eax": eax, "blobs": blobs, "end": p,
                "fills": p == end}
    if kind == FRAME:
        lo, hi, frames, polls = FRAME_BODY.unpack_from(buf, off + 12)
        us = lo if brk == "u64" else lo | (hi << 32)
        return {"virtual_us": us, "frames": frames, "polls": polls, "fills": nbytes == 28}
    if kind == NOTE:
        (n,) = U32.unpack_from(buf, off + 12)
        text = bytes(buf[off + 16:off + 16 + n]).decode("utf-8", "replace")
        return {"text": text, "fills": 16 + pad4(n) == nbytes}
    if kind == END:
        return {"fills": nbytes == 12}
    if kind == AUDIO:
        api, op, this, thread, lo, hi, argc = AUDIO_FIXED.unpack_from(buf, off + 12)
        if argc > MAX_ARGC:
            raise ValueError(f"argc {argc}")
        p = off + 12 + AUDIO_FIXED.size
        args = struct.unpack_from(f"<{argc}I", buf, p)
        p += 4 * argc
        eax, nb = U32X2.unpack_from(buf, p)
        blobs, p = _blobs(buf, p + 8, nb, brk)
        return {"api": api, "op": op, "this": this, "thread": thread,
                "virtual_us": lo if brk == "u64" else lo | (hi << 32), "args": args, "eax": eax,
                "blobs": blobs, "end": p, "fills": p == end}
    return {"fills": True}


# ---------------------------------------------------------------- the content

def prim_vertices(ptype: int, count: int) -> int | None:
    return {1: count, 2: 2 * count, 3: count + 1, 4: 3 * count, 5: count + 2,
            6: count + 2}.get(ptype)


def level_dim(v: int, level: int) -> int:
    return max(1, v >> level) if v else 1


def lock_bytes(info: bytes | None) -> int:
    """The bytes a LOCK blob says its LOCKED blob holds; 0 for no LOCK blob."""
    return LOCK_INFO.unpack(info)[6] if info and len(info) == LOCK_INFO.size else 0


def shader_problem(data: bytes) -> str | None:
    """Why `data` is not one whole shader token stream (host/sa2_stream.h's walk),
    or None."""
    if len(data) < 8 or len(data) % 4:
        return f"{len(data)} bytes is not a token stream"
    toks = struct.unpack_from(f"<{len(data) // 4}I", data)
    if toks[0] >> 16 not in (0xFFFE, 0xFFFF):
        return f"the version token {toks[0]:#010x} is not a shader's"
    major = (toks[0] >> 8) & 0xFF
    i, n = 1, len(toks)
    while i < n:
        tok = toks[i]
        if tok == 0x0000FFFF:
            return None if i == n - 1 else f"the end token is at token {i} of {n}"
        if tok & 0xFFFF == 0xFFFE:
            i += 1 + ((tok >> 16) & 0x7FFF)
        elif major >= 2:
            i += 1 + ((tok >> 24) & 0xF)
        elif tok & 0xFFFF == 0x51:
            i += 6
        else:
            i += 1
            while i < n and toks[i] >> 31:
                i += 1
    return f"the walk leaves the {n} tokens without meeting the end token"


def decl_problem(data: bytes) -> str | None:
    if not data or len(data) % 8 or len(data) > 65 * 8:
        return f"{len(data)} bytes is not a whole number of vertex elements"
    streams = [struct.unpack_from("<H", data, k)[0] for k in range(0, len(data), 8)]
    if streams[-1] != 0xFF:
        return "the last element is not D3DDECL_END"
    if 0xFF in streams[:-1]:
        return "an element before the last is D3DDECL_END"
    return None


def rgn_problem(data: bytes) -> str | None:
    if len(data) < 32:
        return f"{len(data)} bytes is shorter than an RGNDATAHEADER"
    n = U32.unpack_from(data, 8)[0]
    if n > RGN_RECTS:
        return f"nCount {n} is over the {RGN_RECTS} rectangles a region carries"
    if len(data) != 32 + 16 * n:
        return f"{len(data)} bytes is not the header and its {n} rectangles"
    return None


class Content:
    """The content checks, fed one record at a time in order (host/sa2_stream.h's
    rules, evaluated here). Problems go to `problems`."""

    def __init__(self, head: dict, brk: str, problems: Problems) -> None:
        self.brk = brk
        self.p = problems
        ifaces = head.get("interfaces") or []
        self.names = {}
        for t, s, name, argc in head.get("methods") or []:
            self.names[(t, s)] = (ifaces[t] if t < len(ifaces) else f"type{t}", name)
        self.rules: dict[tuple[int, int], list] = {}
        for row in head.get("rules") or []:
            try:
                t, s, text = row
                if not (whole(t) and whole(s)):
                    raise TypeError("its type and slot are not whole numbers")
                self.rules[(t, s)] = parse_ops(text)
            except (ValueError, TypeError) as e:
                self.p.append(f"the header's rule {row!r} does not read: {e}")
        self.entries: dict[int, tuple[str, int, list]] = {}
        for row in head.get("entries") or []:
            try:
                e, name, argc, text = row
                if not (whole(e) and whole(argc) and isinstance(name, str)):
                    raise TypeError("its entry and argc are not whole numbers and a name")
                self.entries[e] = (name, argc, parse_ops(text))
            except (ValueError, TypeError) as err:
                self.p.append(f"the header's entry {row!r} does not read: {err}")
                continue
            if D3DX_NAMES.get(e) != name:
                self.p.append(f"the header names entry {e} {name!r}; host/sa2_stream.h names it "
                              f"{D3DX_NAMES.get(e)!r}")
        self.frame_methods = {k for k, ops in self.rules.items() if any(n == "frame" for n, _ in ops)}
        self.object_args = {k: OBJECT_ARGS[v] for k, v in self.names.items() if v in OBJECT_ARGS}
        base, stride = head.get("object_base"), head.get("object_stride")
        self.object_base = base if isinstance(base, int) else None
        self.object_stride = stride if isinstance(stride, int) and stride > 0 else None
        self.object_blocks = object_blocks(head, self.p)
        self.cur_entry = 0
        self.object_outs = {k: outs for k, v in self.names.items() if (outs := object_outs(*v))}
        self.buffer_releases = {k for k, (i, m) in self.names.items()
                                if i in BUFFER_IFACES and m == "Release"}
        self.known: set[int] = set()
        self.unknown_seen: set[int] = set()
        self.shapes: dict[int, tuple[int, int, int]] = {}      # object -> (w, h, format)
        self.levels: dict[int, tuple[int, int]] = {}          # level surface -> (texture, level)
        self.buffers: dict[int, int] = {}                     # buffer -> length
        self.locks: dict[tuple, list[dict]] = {}              # open locks, a stack per key
        self.pending_frame: int | None = None
        self.files: list[dict] = []
        self.checked = 0
        # the recorder's buffer watch, as the stream shows it: each buffer's bytes
        # while watched, the recorder's list (a buffer joins it at its first watch
        # and leaves it at the first check after its watch ended), and the counters
        self.watch_on: dict[int, int] = {}
        self.watch_list: list[int] = []
        self.watch_listed: set[int] = set()
        self.watch = {"every_frames": WATCH_EVERY, "set": 0, "checks": 0, "bytes_hashed": 0}
        self.frames = 0

    def object_shaped(self, value: int) -> bool:
        if self.object_base is None or self.object_stride is None:
            return True
        if self.object_blocks:
            return any(base <= value < base + count * self.object_stride
                       and (value - base) % self.object_stride == 0
                       for base, _first, count in self.object_blocks)
        return value >= self.object_base and (value - self.object_base) % self.object_stride == 0

    # --- ordering
    def next_record(self, kind: int, seq: int) -> None:
        if self.brk == "frame":
            return
        if self.pending_frame is not None:
            if kind != FRAME:
                self.p.append(f"record seq {self.pending_frame} is a Present, and the record after "
                              f"it is a {KIND_NAMES.get(kind, kind)}, not its FRAME")
            self.pending_frame = None
        elif kind == FRAME:
            self.p.append(f"the FRAME at seq {seq} does not follow a Present")

    def finish(self) -> None:
        if self.pending_frame is not None and self.brk != "frame":
            self.p.append(f"the stream ends after the Present at seq {self.pending_frame}, "
                          f"with no FRAME")
        self.watch["watched_at_exit"] = sum(1 for b in self.watch_list if b in self.watch_on)

    # --- the buffer watch (host/sa2_present_rec.c: watch_set, watch_drop, watch_check)
    def _watch_set(self, buf: int, nbytes: int) -> None:
        """An Unlock closed the buffer's last open lock and copied `nbytes`."""
        self.watch_on[buf] = nbytes
        self.watch["set"] += 1
        if buf not in self.watch_listed:
            self.watch_listed.add(buf)
            self.watch_list.append(buf)

    def _watch_drop(self, buf: int) -> None:
        """A Lock of the buffer, or its final Release: its watch ends."""
        self.watch_on.pop(buf, None)

    def frame(self) -> None:
        """A FRAME: every WATCH_EVERY-th, while the recorder's list holds anything, is
        a check of every buffer still watched; the others leave the list."""
        self.frames += 1
        if not self.watch_list or self.frames % WATCH_EVERY:
            return
        self.watch["checks"] += 1
        keep = []
        for b in self.watch_list:
            if b in self.watch_on:
                self.watch["bytes_hashed"] += self.watch_on[b]
                keep.append(b)
            else:
                self.watch_listed.discard(b)
        self.watch_list = keep

    # --- per record
    def _heads(self, seq: int, what: str, argc: int, blobs, buf) -> None:
        for role, arg, off, n in blobs:
            if self.brk != "blobhead" and (role not in ROLE_NAMES or (arg != NO_ARG and arg >= argc)):
                self.p.append(f"record seq {seq} ({what}): a blob of role {role}, arg {arg}, "
                              f"which no call of {argc} arguments carries")
            if n % 4 and self.brk != "padzero" and any(bytes(buf[off + n:off + pad4(n)])):
                self.p.append(f"record seq {seq} ({what}): the padding after a {n}-byte blob is "
                              f"not zero")

    def _known(self, seq: int, what: str, obj: int, where: str) -> None:
        if self.brk == "objects" or not obj or obj in self.known:
            return
        if obj not in self.unknown_seen:
            self.unknown_seen.add(obj)
            self.p.append(f"record seq {seq} ({what}): {where} {obj:#010x} is an object nothing "
                          f"earlier in the stream introduced")

    def _expect(self, seq: int, what: str, ops, args, eax, this, blobs, buf,
                obj_outs: tuple[int, ...]) -> None:
        """Hold the record's blobs to its ops; then update what the ops say about
        objects, shapes and locks. `obj_outs` are the arguments whose OUT word is an
        object (OBJECT_OUTS)."""
        argc = len(args)

        def arg(i):
            return args[i] if i < argc else 0

        def word(role, i):
            for r, a, off, n in blobs:
                if r == role and a == i and n >= 4:
                    return U32.unpack_from(buf, off)[0]
            return None

        def data(role, i):
            for r, a, off, n in blobs:
                if r == role and a == i:
                    return bytes(buf[off:off + n])
            return None

        want = []                  # (role, arg, length or None, check or None)
        after = []                 # (op, operands) to apply once the blobs held
        for name, o in ops:
            a = o.get("a", 0)
            if name in ("in", "out"):
                if arg(a) and o["n"]:
                    want.append((IN if name == "in" else OUT, a, o["n"], None))
            elif name in ("in_vec", "out_vec"):
                n = arg(o["b"]) * o["n"]
                if arg(a) and n:
                    want.append((IN if name == "in_vec" else OUT, a, n, None))
            elif name == "out_sized":
                if not eax and arg(a) and arg(o["b"]):
                    k = word(OUT, o["b"])
                    if k is None:
                        self.p.append(f"record seq {seq} ({what}): out_sized {a} needs the "
                                      f"OUT blob of arg {o['b']}, and there is none")
                    elif k * o["n"]:
                        want.append((OUT, a, k * o["n"], None))
            elif name == "shader":
                if arg(a):
                    want.append((IN, a, None, None if self.brk == "walk" else shader_problem))
            elif name == "decl":
                if arg(a):
                    want.append((IN, a, None, None if self.brk == "walk" else decl_problem))
            elif name == "up":
                v = prim_vertices(arg(0), arg(1)) if argc >= 4 else None
                if argc >= 4 and arg(2) and v and v * arg(3):
                    want.append((IN, 2, v * arg(3), None))
            elif name == "iup":
                if argc >= 8:
                    v = prim_vertices(arg(0), arg(3))
                    n = (v or 0) * (2 if arg(5) == 101 else 4)
                    if arg(4) and n:
                        want.append((IN, 4, n, None))
                    n = (arg(1) + arg(2)) * arg(7)
                    if arg(6) and n:
                        want.append((IN, 6, n, None))
            elif name == "pre_pp":
                if arg(a):
                    want.append((IN, a, PP.size, None))
            elif name == "pre_rect":
                if arg(a):
                    want.append((IN, a, 16, None))
            elif name == "pre_rgn":
                if arg(a):
                    want.append((IN, a, None, None if self.brk == "walk" else rgn_problem))
            elif name == "device":
                want.append((DESC, NO_ARG, DESC_DEVICE.size, None))
                after.append((name, o))
            elif name == "file":
                if arg(a) and arg(o["b"]):
                    want.append((FILE, a, arg(o["b"]), None))
            elif name == "file_desc":
                if not eax and word(OUT, a):
                    want.append((DESC, NO_ARG, DESC_FILE.size, None))
                    after.append((name, o))
            elif name in ("unlock_surf", "unlock_tex", "unlock_buf"):
                key = self._lock_key(name, this, arg(0))
                stack = self.locks.get(key)
                if stack:
                    lk = stack.pop()
                    if not stack:
                        del self.locks[key]
                    want.append((LOCK, NO_ARG, LOCK_INFO.size, None))
                    # LOCKED is the size the stream gives the lock; where it gives
                    # none, the size the LOCK claims
                    size = self._lock_size(lk, this)
                    if size is None:
                        size = lock_bytes(data(LOCK, NO_ARG))
                    if size:
                        want.append((LOCKED, NO_ARG, size, None))
                    after.append(("unlock", {"lock": lk, "last": key not in self.locks}))
            else:
                after.append((name, o))
        if self.brk != "rules":
            self._match(seq, what, want, blobs, buf)
        # the objects the record introduces: an out-parameter that answers one, of a
        # call that answered 0, within the header's object layout (the DESC and
        # object_eax ones are applied below).
        if not eax:
            for i in obj_outs:
                v = word(OUT, i)
                if v and self.object_shaped(v):
                    self.known.add(v)
        for name, o in after:
            self._apply(seq, what, name, o, args, eax, this, blobs, buf, word, data)

    def _match(self, seq: int, what: str, want, blobs, buf) -> None:
        if not want and not blobs:
            return
        have = list(blobs)
        for role, a, n, check in want:
            hit = next((b for b in have if b[0] == role and b[1] == a), None)
            label = f"{ROLE_NAMES.get(role, role)} blob of {'arg ' + str(a) if a != NO_ARG else 'the call'}"
            if hit is None:
                self.p.append(f"record seq {seq} ({what}): no {label}, which its rule gives")
                continue
            have.remove(hit)
            if n is not None and hit[3] != n:
                self.p.append(f"record seq {seq} ({what}): the {label} is {hit[3]} bytes, and its "
                              f"rule gives {n}")
            if check is not None:
                why = check(bytes(buf[hit[2]:hit[2] + hit[3]]))
                if why:
                    self.p.append(f"record seq {seq} ({what}): the {label}: {why}")
        if have:
            self.p.append(f"record seq {seq} ({what}): blobs its rule does not give: "
                          f"{[(ROLE_NAMES.get(r, r), a, n) for r, a, _o, n in have]}")

    @staticmethod
    def _lock_key(name: str, this: int, level: int) -> tuple:
        if name in ("lock_tex", "unlock_tex"):
            return ("tex", this, level)
        if name in ("lock_surf", "unlock_surf"):
            return ("surf", this)
        return ("buf", this)

    def _level_shape(self, obj: int, level: int):
        s = self.shapes.get(obj)
        if s is None:
            return None
        return (level_dim(s[0], level), level_dim(s[1], level), s[2])

    def _lock_shape(self, lk: dict, this: int):
        """The (w, h, format) of what a texture or surface lock covers, as the calls
        that made it say, or None: a texture's level; a level surface's level of its
        texture; any other surface's own shape."""
        if lk["kind"] == "tex":
            return self._level_shape(this, lk["level"])
        tex_level = self.levels.get(this)
        return self._level_shape(*tex_level) if tex_level else self.shapes.get(this)

    def _lock_size(self, lk: dict, this: int) -> int | None:
        """The bytes an Unlock copies for the lock `lk`, sized from the stream alone
        as the recorder sizes it (host/sa2_present_rec.c, lock_open_rect and the
        buffer Lock): a buffer's SizeToLock, or the rest of it from OffsetToLock when
        the stream made it; a level's rows (the rectangle's, else the level's, in
        4x4 block rows for DXT) times the pitch its Lock answered, when the calls
        that made it give its shape. None when the stream does not say; the recorder
        cannot size such a lock either, and notes that it could not."""
        if lk["kind"] == "buf":
            if lk["size"]:
                return lk["size"]
            if this in self.buffers:
                return max(0, self.buffers[this] - lk["offset"])
            return None
        shape = self._lock_shape(lk, this)
        if shape is None:
            return None
        rect = lk["rect"]
        rows_px = min(max(0, rect[3] - rect[1]), shape[1]) if rect else shape[1]
        return lk["pitch"] * ((rows_px + 3) // 4 if shape[2] in BLOCK_FORMATS else rows_px)

    def _apply(self, seq, what, name, o, args, eax, this, blobs, buf, word, data) -> None:
        argc = len(args)

        def arg(i):
            return args[i] if i < argc else 0

        if name == "device":
            d = data(DESC, NO_ARG)
            if d is None or len(d) != DESC_DEVICE.size:
                return
            dev, rt, ds = DESC_DEVICE.unpack(d)
            out = word(OUT, 5)
            if out is not None and out != dev:
                self.p.append(f"record seq {seq} ({what}): its DESC names device {dev:#010x} and "
                              f"its out-parameter {out:#010x}")
            self.known.update(x for x in (dev, rt, ds) if x)
            pp = data(IN, 4)
            if pp is not None and len(pp) == PP.size:
                v = PP.unpack(pp)
                if rt:
                    self.shapes[rt] = (v[0], v[1], v[2])
                if ds:
                    self.shapes[ds] = (v[0], v[1], v[10])
        elif name == "file_desc":
            d = data(DESC, NO_ARG)
            if d is None or len(d) != DESC_FILE.size:
                return
            f = DESC_FILE.unpack(d)
            tex = word(OUT, o["a"])
            if f[0] != tex:
                self.p.append(f"record seq {seq} ({what}): its DESC describes {f[0]:#010x} and "
                              f"the call wrote {tex:#010x}")
            self.known.add(f[0])
            self.shapes[f[0]] = (f[3], f[4], f[7])
            fb = next(((off, n) for r, a, off, n in blobs if r == FILE), None)
            self.files.append({"seq": seq, "entry": self.cur_entry, "texture": tex, "desc": list(f),
                               "bytes": fb[1] if fb else None,
                               "sha256": hashlib.sha256(bytes(buf[fb[0]:fb[0] + fb[1]])).hexdigest()
                               if fb else None})
        elif name == "object_eax":
            if eax:
                self.known.add(eax)
        elif name in ("new_tex", "new_vol", "new_surf", "new_cube"):
            if eax:
                return
            obj = word(OUT, o["a"])
            if obj:
                h = arg(o["b"]) if name == "new_cube" else arg(o["c"])
                self.shapes[obj] = (arg(o["b"]), h, arg(o["d"]))
        elif name == "new_buf":
            obj = word(OUT, o["a"])
            if not eax and obj:
                self.buffers[obj] = arg(o["b"])
        elif name == "level":
            obj = word(OUT, o["a"])
            if not eax and obj:
                self.levels[obj] = (this, arg(0))
        elif name in ("lock_surf", "lock_tex", "lock_buf"):
            if name == "lock_buf":
                self._watch_drop(this)          # the recorder drops it at any Lock
            if eax:
                return
            if name == "lock_buf":
                bits = word(OUT, 2)
                lk = {"kind": "buf", "bits": bits or 0, "pitch": 0, "offset": arg(0),
                      "size": arg(1), "flags": arg(3), "rect": None}
            else:
                out_i, rect_i, flags_i = (0, 1, 2) if name == "lock_surf" else (1, 2, 3)
                lr = data(OUT, out_i)
                pitch, bits = struct.unpack("<iI", lr) if lr and len(lr) == 8 else (0, 0)
                r = data(IN, rect_i)
                lk = {"kind": name[5:], "bits": bits, "pitch": pitch & 0xFFFFFFFF,
                      "flags": arg(flags_i), "rect": struct.unpack("<4i", r) if r and len(r) == 16
                      else None, "level": arg(0) if name == "lock_tex" else 0, "offset": 0}
            key = self._lock_key(name, this, arg(0))
            if name == "lock_buf":
                self.locks.setdefault(key, []).append(lk)      # a buffer's Locks nest
            else:
                self.locks[key] = [lk]     # a surface's or level's second Lock replaces its first
        elif name == "unlock":
            if self.brk != "lock":
                self._check_lock(seq, what, o["lock"], this, blobs, buf, data)
            if o["lock"]["kind"] == "buf" and o["last"]:
                # the buffer's last open lock closed: the recorder watches the bytes
                # this Unlock copied (LOCK.bytes, when a LOCKED blob holds them)
                nbytes = lock_bytes(data(LOCK, NO_ARG))
                if nbytes and data(LOCKED, NO_ARG) is not None:
                    self._watch_set(this, nbytes)

    def _check_lock(self, seq, what, lk, this, blobs, buf, data) -> None:
        info = data(LOCK, NO_ARG)
        if info is None or len(info) != LOCK_INFO.size:
            return                                   # _match said so
        (obj, container, level, bits, pitch, rows, nbytes, fmt, w, h, has_rect,
         r0, r1, r2, r3, flags, offset) = LOCK_INFO.unpack(info)
        bad = []
        if obj != this:
            bad.append(f"object {obj:#010x}")
        if bits != lk["bits"]:
            bad.append(f"bits {bits:#010x} where the Lock answered {lk['bits']:#010x}")
        if flags != lk["flags"]:
            bad.append(f"flags {flags:#x} where the Lock passed {lk['flags']:#x}")
        # Where the stream sizes the lock, LOCK.bytes is that size: a LOCK that
        # claims fewer bytes, or none, for a lock the stream sizes is data lost.
        size = self._lock_size(lk, this)
        if lk["kind"] == "buf":
            if (pitch, rows, fmt, w, h, has_rect) != (0, 1, 0, 0, 0, 0):
                bad.append("a buffer's lock with a pitch, rows beyond one, or a shape")
            if offset != lk["offset"]:
                bad.append(f"offset {offset} where the Lock passed {lk['offset']}")
            if size is not None and nbytes != size:
                bad.append(f"{nbytes} bytes where the Lock covers {size}")
        else:
            if pitch != lk["pitch"]:
                bad.append(f"pitch {pitch} where the Lock answered {lk['pitch']}")
            shape = self._lock_shape(lk, this)
            want_where = (0, lk["level"]) if lk["kind"] == "tex" else (self.levels.get(this)
                                                                       or (0, 0))
            if (container, level) != want_where:
                bad.append(f"container {container:#010x} level {level} where the stream says "
                           f"{want_where[0]:#010x} level {want_where[1]}")
            if shape is not None and (w, h, fmt) != shape:
                bad.append(f"{w}x{h} format {fmt:#x} where the calls that made it say "
                           f"{shape[0]}x{shape[1]} format {shape[2]:#x}")
            rect = lk["rect"]
            if bool(has_rect) != (rect is not None) or (rect and (r0, r1, r2, r3) != rect):
                bad.append(f"rectangle {(r0, r1, r2, r3) if has_rect else None} where the Lock "
                           f"passed {rect}")
            rows_px = min(max(0, rect[3] - rect[1]), h) if rect else h
            want_rows = (rows_px + 3) // 4 if fmt in BLOCK_FORMATS else rows_px
            if rows != want_rows:
                bad.append(f"{rows} rows where the level and rectangle give {want_rows}")
            if size is not None and nbytes != size:
                bad.append(f"{nbytes} bytes where the level's rows at the Lock's pitch are {size}")
            elif size is None and (nbytes or (pitch and rows)) and nbytes != pitch * rows:
                bad.append(f"{nbytes} bytes where pitch x rows is {pitch * rows}")
        if bad:
            self.p.append(f"record seq {seq} ({what}): its LOCK: {'; '.join(bad)}")

    def call(self, seq: int, rec: dict, buf) -> None:
        key = (rec["type"], rec["slot"])
        iface, method = self.names.get(key, (f"type{key[0]}", f"slot{key[1]}"))
        what = f"{iface}::{method}"
        args, blobs = rec["args"], rec["blobs"]
        self.checked += 1
        self._heads(seq, what, len(args), blobs, buf)
        self._known(seq, what, rec["this"], "this")
        for i in self.object_args.get(key, ()):
            if i < len(args):
                self._known(seq, what, args[i], f"arg {i}")
        ops = self.rules.get(key, ())
        if ops or blobs:
            self._expect(seq, what, ops, args, rec["eax"], rec["this"], blobs, buf,
                         self.object_outs.get(key, ()))
        if key in self.buffer_releases and rec["eax"] == 0:
            self._watch_drop(rec["this"])        # the final Release: the store is the heap's
        if key in self.frame_methods:
            self.pending_frame = seq

    def entry(self, seq: int, rec: dict, buf) -> None:
        e = rec["entry"]
        ent = self.entries.get(e)
        if ent is None:
            self.p.append(f"record seq {seq}: entry {e}, which the header's entries table does "
                          f"not name")
            return
        name, argc, ops = ent
        args, blobs = rec["args"], rec["blobs"]
        self.checked += 1
        if len(args) != argc and self.brk != "entryargc":
            self.p.append(f"record seq {seq} ({name}): {len(args)} arguments, and the header's "
                          f"entries table says {argc}")
        self._heads(seq, name, len(args), blobs, buf)
        for i in ENTRY_OBJECT_ARGS.get(name, ()):
            if i < len(args):
                self._known(seq, name, args[i], f"arg {i}")
        self.cur_entry = e
        self._expect(seq, name, ops, args, rec["eax"], 0, blobs, buf,
                     ENTRY_OBJECT_OUTS.get(name, ()))


class AudioContent:
    """The audio checks (version 2), fed every AUDIO record in order: its argc its
    op's in the header's audio table, its api its op's interface's, its blobs exactly
    the ones its op's line gives with the lengths they give (an Unlock's HASH exactly
    when it leaves no Lock open on an existing store), its STATE the object the call
    was on or wrote, an Unlock's bytes inside its buffer's store, the level matrix its
    STATE's, a re-anchoring call's STATE anchored at the call's own time (REANCHOR), a
    submit's frames ending at its STATE's queued_end, and every buffer and voice it
    calls introduced earlier by a call that made it (ds_new, xa_new). It also follows
    the recorder's store watch from the records (host/sa2_stream.h: a HASH sets a
    store's watch, a Lock on a watched store is one check and ends it, every
    WATCH_EVERY-th FRAME is one check of every watched store), for compare_counts, and
    keeps each api's first `keep` records' heads for hold_audio_to_run. Problems go to
    `problems`."""

    def __init__(self, head: dict, brk: str, problems: Problems, keep: int = 0) -> None:
        self.brk = brk
        self.p = problems
        self.table: dict = {}
        if "audio" in head:
            try:
                self.table = audio_table(head)
            except (ValueError, TypeError) as e:
                problems.append(f"the header's audio table does not read: {e}")
            au = head.get("audio") or {}
            if brk != "autable":
                for key, size in (("ds_state_bytes", DS_STATE.size), ("xa_state_bytes", XA_STATE.size)):
                    if au.get(key) != size:
                        problems.append(f"the header's audio {key} is {au.get(key)!r}, and "
                                        f"host/sa2_stream.h's struct is {size} bytes")
        self.buffers: set[int] = set()
        self.voices: set[int] = set()
        self.unknown_seen: set[int] = set()
        self.ops: dict[str, int] = {}
        self.pcm_bytes = 0
        self.checked = 0
        # the stores (by guest address): {"len": mem_len, "open": Locks open, "on": watched}
        self.stores: dict[int, dict] = {}
        self.watch = {"every_frames": WATCH_EVERY, "set": 0, "checks": 0, "bytes_hashed": 0}
        self.frames = 0
        self.queued_end: dict[int, int] = {}       # voice -> its last STATE's queued_end
        self.keep = keep
        self.heads: dict[int, list[tuple]] = {1: [], 2: []}
        self.cut: dict[int, bool] = {1: False, 2: False}

    def _known(self, seq: int, what: str, obj: int, kind: str) -> None:
        if self.brk == "auobjects" or obj in (self.buffers if kind == "ds" else self.voices):
            return
        if obj not in self.unknown_seen:
            self.unknown_seen.add(obj)
            self.p.append(f"record seq {seq} ({what}): {'buffer' if kind == 'ds' else 'voice'} "
                          f"{obj:#010x} is one no earlier call made")

    def frame(self) -> None:
        """A FRAME: every WATCH_EVERY-th is one check of every watched store."""
        self.frames += 1
        if self.frames % WATCH_EVERY:
            return
        for s in self.stores.values():
            if s["on"]:
                self.watch["checks"] += 1
                self.watch["bytes_hashed"] += s["len"]

    def finish(self) -> None:
        self.watch["watched_at_exit"] = sum(1 for s in self.stores.values() if s["on"])

    def _store(self, words: tuple) -> dict | None:
        mem, mem_len = words[DS_FIELDS.index("mem")], words[DS_FIELDS.index("mem_len")]
        if not mem:
            return None
        s = self.stores.setdefault(mem, {"len": 0, "open": 0, "on": False})
        s["len"] = mem_len
        return s

    def record(self, seq: int, rec: dict, buf) -> None:
        op = rec["op"]
        row = self.table.get(op)
        if row is None:
            self.p.append(f"record seq {seq}: audio op {op}, which the header's audio table does not name")
            return
        name, iface, method, argc, aops = row
        self.ops[name] = self.ops.get(name, 0) + 1
        what = f"{name}, {iface}::{method}"
        args, blobs, eax, this = rec["args"], rec["blobs"], rec["eax"], rec["this"]
        self.checked += 1
        if len(args) != argc and self.brk != "argc":
            self.p.append(f"record seq {seq} ({what}): {len(args)} arguments, and the header's audio "
                          f"table says {argc}")
        if AUDIO_IFACES.get(iface) != rec["api"]:
            self.p.append(f"record seq {seq} ({what}): api {rec['api']}, and {iface} is "
                          f"{AUDIO_APIS.get(AUDIO_IFACES.get(iface))}'s")
        for role, a, off, n in blobs:
            if self.brk != "blobhead" and (role not in ROLE_NAMES or (a != NO_ARG and a >= len(args))):
                self.p.append(f"record seq {seq} ({what}): a blob of role {role}, arg {a}, which no "
                              f"call of {len(args)} arguments carries")
            if role == PCM:
                self.pcm_bytes += n
            if n % 4 and self.brk != "padzero" and any(bytes(buf[off + n:off + pad4(n)])):
                self.p.append(f"record seq {seq} ({what}): the padding after a {n}-byte blob is not zero")

        def arg(i):
            return args[i] if i < len(args) else 0

        def data(role, i):
            for r, a, off, n in blobs:
                if r == role and a == i:
                    return bytes(buf[off:off + n])
            return None

        def state_words(st):
            """The STATE `st` names ((kind, object, arg)), unpacked; None when absent."""
            if st is None:
                return None
            got = data(STATE, st[2])
            size = DS_STATE.size if st[0] == "ds" else XA_STATE.size
            return (DS_STATE if st[0] == "ds" else XA_STATE).unpack(got) if got is not None and \
                len(got) == size else None

        want, state, pcm = [], None, []
        for aop, o in aops:
            if aop == "out":
                if arg(o["a"]) and o["n"]:
                    want.append((OUT, o["a"], o["n"]))
            elif aop == "in":
                n = arg(o["b"]) * arg(o["c"]) * 4
                if arg(o["a"]) and n and arg(o["b"]) <= MATRIX_MAX and arg(o["c"]) <= MATRIX_MAX:
                    want.append((IN, o["a"], n))
            elif aop == "pcm":
                if arg(o["a"]) and arg(o["b"]):
                    want.append((PCM, o["a"], arg(o["b"])))
                    pcm.append((arg(o["a"]), arg(o["b"])))
            elif aop == "submit":
                if arg(o["a"]):
                    want.append((IN, o["a"], XBUF.size))
                    x = data(IN, o["a"])
                    if x is not None and len(x) == XBUF.size:
                        words = XBUF.unpack(x)
                        if words[1] and words[2]:
                            want.append((PCM, o["a"], words[1]))
            elif aop in ("ds_state", "xa_state"):
                want.append((STATE, NO_ARG, DS_STATE.size if aop == "ds_state" else XA_STATE.size))
                state = (aop[:2], this, NO_ARG)
                self._known(seq, what, this, aop[:2])
            elif aop in ("ds_new", "xa_new"):
                out = data(OUT, o["a"])
                obj = U32.unpack_from(out)[0] if out is not None and len(out) >= 4 else 0
                if obj and not eax:
                    want.append((STATE, o["a"], DS_STATE.size if aop == "ds_new" else XA_STATE.size))
                    state = (aop[:2], obj, o["a"])
                    (self.buffers if aop == "ds_new" else self.voices).add(obj)
            elif aop == "store_hash":
                # an Unlock: it closes a Lock on its STATE's store (none below 0), and when
                # none is left open carries the store's hash, which sets its watch
                words = state_words(state) if state is not None and state[0] == "ds" else None
                s = self._store(words) if words is not None else None
                if s is not None:
                    s["open"] = max(0, s["open"] - 1)
                    if not s["open"]:
                        want.append((HASH, NO_ARG, 8))
                        h = data(HASH, NO_ARG)
                        if h is not None and len(h) == 8:
                            self.watch["set"] += 1
                            s["on"] = True
        if name == "DS_LOCK":
            # a Lock opens one on its STATE's store: a watched store is checked once more
            words = state_words(state) if state is not None and state[0] == "ds" else None
            s = self._store(words) if words is not None else None
            if s is not None:
                if s["on"]:
                    self.watch["checks"] += 1
                    self.watch["bytes_hashed"] += s["len"]
                    s["on"] = False
                s["open"] += 1
        words = state_words(state)
        if self.brk != "aurules":
            have = list(blobs)
            for role, a, n in want:
                hit = next((b for b in have if b[0] == role and b[1] == a), None)
                label = f"{ROLE_NAMES.get(role, role)} blob of {'arg ' + str(a) if a != NO_ARG else 'the call'}"
                if hit is None:
                    self.p.append(f"record seq {seq} ({what}): no {label}, which its line gives")
                    continue
                have.remove(hit)
                if hit[3] != n:
                    self.p.append(f"record seq {seq} ({what}): the {label} is {hit[3]} bytes, and its "
                                  f"line gives {n}")
            if have:
                self.p.append(f"record seq {seq} ({what}): blobs its line does not give: "
                              f"{[(ROLE_NAMES.get(r, r), a, n) for r, a, _o, n in have]}")
            if words is not None:
                if words[0] != state[1]:
                    self.p.append(f"record seq {seq} ({what}): its STATE describes {words[0]:#010x}, "
                                  f"and the call names {state[1]:#010x}")
                fields = DS_FIELDS if state[0] == "ds" else XA_FIELDS
                f = dict(zip(fields, words))
                anchor = f["anchor_us_lo"] | f["anchor_us_hi"] << 32
                if name in REANCHOR and anchor != rec["virtual_us"]:
                    self.p.append(f"record seq {seq} ({what}): the call re-anchors, and its STATE's "
                                  f"anchor_us {anchor} is not its virtual_us {rec['virtual_us']}")
                if state[0] == "ds" and pcm:
                    mem, mem_len = f["mem"], f["mem_len"]
                    for at, n in pcm:
                        if not (mem <= at and at + n <= mem + mem_len):
                            self.p.append(f"record seq {seq} ({what}): {n} bytes at {at:#010x} are not "
                                          f"inside the buffer's store {mem:#010x}+{mem_len}")
                if name == "XA_SET_MATRIX" and state[0] == "xa":
                    m = data(IN, 3)
                    k = arg(1) * arg(2)
                    if m is not None and len(m) == 4 * k and k <= 12:
                        got = struct.unpack(f"<{k}I", m)
                        mine = words[XA_FIELDS.index("matrix0"):XA_FIELDS.index("matrix0") + k]
                        if tuple(got) != tuple(mine):
                            self.p.append(f"record seq {seq} ({what}): the level matrix the call "
                                          f"passed is not the one its STATE holds")
                if name == "XA_SUBMIT" and state[0] == "xa":
                    x = data(IN, 0)
                    before = self.queued_end.get(state[1])
                    end = f["queued_end_lo"] | f["queued_end_hi"] << 32
                    if x is not None and len(x) == XBUF.size and before is not None and f["block_align"]:
                        frames = XBUF.unpack(x)[1] // f["block_align"]
                        if end != before + frames:
                            self.p.append(f"record seq {seq} ({what}): its STATE's queued_end is {end}, and "
                                          f"the {frames} frames it submits end at {before + frames}")
        if words is not None and state[0] == "xa":
            f = dict(zip(XA_FIELDS, words))
            self.queued_end[state[1]] = f["queued_end_lo"] | f["queued_end_hi"] << 32
        api = rec["api"]
        if api in self.heads:
            if len(self.heads[api]) < self.keep:
                self.heads[api].append((seq, name, this, tuple(args), eax, rec["thread"], rec["virtual_us"],
                                        self._outs(name, data, words)))
            else:
                self.cut[api] = True

    @staticmethod
    def _outs(name: str, data, words) -> tuple:
        """The record's two words the device logs as out0 and out1 (LOG_OUTS), None for one
        it does not set or the record does not carry."""
        got = []
        for spec in LOG_OUTS.get(name, (None, None)):
            v = None
            if spec is not None and spec[0] == STATE:
                if words is not None and len(words) == len(XA_FIELDS):
                    v = words[XA_FIELDS.index(spec[2])]
            elif spec is not None:
                d = data(spec[0], spec[1])
                if d is not None and len(d) >= spec[2] + 4:
                    v = U32.unpack_from(d, spec[2])[0]
            got.append(v)
        return tuple(got)


def scan(path: Path, *, want_range: tuple[int, int] | None = None,
         keep_notes: int = 40, keep_calls: int = 0, doc: tuple[dict, dict] | None = None,
         keep_audio: int = 0) -> dict:
    """The stream at `path`, read whole: its summary, its problems, and the records
    of `want_range` (seq from, to) decoded in full. With `keep_calls`, the first
    that many CALL and D3DX records (`head_calls`), for hold_to_run; with
    `keep_audio`, the first that many AUDIO records of each api (`audio_heads`), for
    hold_audio_to_run. With `doc` (doc_table of host/sa2_stream.h), the header's
    tables held to its lines. StreamError when the file is not a stream at all."""
    brk = active_break()
    size = path.stat().st_size
    problems = Problems()
    with open(path, "rb") as f:
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) if size else b""
        buf = memoryview(mm)
        try:
            head, off = read_header(buf)
            version = U32.unpack_from(buf, 8)[0]
            table = method_table(head)
            content = Content(head, brk, problems)
            audio = AudioContent(head, brk, problems, keep_audio)
            last_us = None              # the clock of the timed record before (version 2)
            meta = header_meta(head)
            if meta is not None and not isinstance(meta, dict):
                problems.append(f"the header's meta is not a JSON object: {str(meta)[:80]!r}")
            if doc is not None and brk != "table":
                for p in table_problems(head, doc):
                    problems.append(p)
            kinds: dict[int, list[int]] = {}
            calls: dict[tuple[int, int], int] = {}
            d3dx: dict[int, int] = {}
            roles: dict[int, list[int]] = {}
            method_blobs: dict[tuple[int, int], int] = {}
            frames: list[dict] = []
            notes: list[str] = []
            picked: list[dict] = []
            head_calls: list[tuple] = []
            n_notes = 0
            n_state = 0
            seq_expect = 0
            records = 0
            ended_at = None
            while off < size:
                if ended_at is not None:
                    if brk != "tail":
                        problems.append(f"{size - off} bytes follow the END record "
                                        f"(seq {ended_at})")
                    break
                if size - off < 12:
                    if brk != "trunc":
                        problems.append(f"{size - off} bytes at offset {off} are too few for "
                                        f"a record head: the stream is cut short")
                    break
                kind, nbytes, seq = HEAD.unpack_from(buf, off)
                if nbytes < 12 or nbytes % 4:
                    problems.append(f"record at offset {off} (seq {seq}) says it is {nbytes} "
                                    f"bytes, not a multiple of 4 of at least 12: the framing "
                                    f"is lost here")
                    break
                if off + nbytes > size:
                    if brk != "trunc":
                        problems.append(f"record seq {seq} at offset {off} runs {off + nbytes - size} "
                                        f"bytes past the end of the file: the stream is cut short")
                    break
                if seq != seq_expect and brk != "seq":
                    problems.append(f"record at offset {off} has seq {seq} where {seq_expect} "
                                    f"was due")
                seq_expect = seq + 1
                records += 1
                k = kinds.setdefault(kind, [0, 0])
                k[0] += 1
                k[1] += nbytes
                content.next_record(kind, seq)
                try:
                    rec = decode(buf, off, kind, nbytes, brk)
                except (struct.error, IndexError, ValueError) as e:
                    problems.append(f"record seq {seq} ({KIND_NAMES.get(kind, kind)}) at offset "
                                    f"{off} does not decode inside its {nbytes} bytes ({e})")
                    off += nbytes
                    continue
                fills = rec["fills"]
                if not fills and brk != "fill":
                    problems.append(f"record seq {seq} ({KIND_NAMES.get(kind, kind)}) at offset "
                                    f"{off}: its fields and blobs do not fill its {nbytes} bytes")
                if "end" in rec and rec["end"] > off + nbytes:
                    if brk != "fill":
                        problems.append(f"record seq {seq}: its blobs run past its own end")
                    fills = False
                if kind == CALL:
                    key = (rec["type"], rec["slot"])
                    calls[key] = calls.get(key, 0) + 1
                    want = table.get(key)
                    if brk != "argc" and table and (want is None or want[2] != len(rec["args"])):
                        problems.append(f"record seq {seq}: call {key[0]}.{key[1]} carries "
                                        f"{len(rec['args'])} arguments, and the header's method "
                                        f"table says {want[2] if want else 'no such method'}")
                    if rec["blobs"]:
                        method_blobs[key] = method_blobs.get(key, 0) + sum(b[3] for b in rec["blobs"])
                    if fills:
                        content.call(seq, rec, buf)
                    if len(head_calls) < keep_calls:
                        head_calls.append(("call", seq, rec["type"], rec["slot"], rec["this"],
                                           rec["args"], rec["eax"], rec["thread"]))
                elif kind == D3DX:
                    d3dx[rec["entry"]] = d3dx.get(rec["entry"], 0) + 1
                    if fills:
                        content.entry(seq, rec, buf)
                    if len(head_calls) < keep_calls:
                        head_calls.append(("d3dx", seq, rec["entry"], None, None, rec["args"],
                                           rec["eax"], None))
                elif kind == FRAME:
                    if frames and brk != "clock" and (
                            rec["virtual_us"] < frames[-1]["virtual_us"]
                            or rec["frames"] < frames[-1]["frames"]):
                        problems.append(f"the FRAME at seq {seq} goes back: virtual_us "
                                        f"{rec['virtual_us']} and frames {rec['frames']} after "
                                        f"{frames[-1]['virtual_us']} and {frames[-1]['frames']}")
                    frames.append({"seq": seq, "virtual_us": rec["virtual_us"],
                                   "frames": rec["frames"], "polls": rec["polls"]})
                    content.frame()
                    audio.frame()
                elif kind == NOTE and rec["text"].startswith("state "):
                    n_state += 1
                elif kind == NOTE:
                    n_notes += 1
                    if len(notes) < keep_notes:
                        notes.append(rec["text"])
                elif kind == END:
                    ended_at = seq
                elif kind == AUDIO and version == VERSION_AUDIO and fills:
                    audio.record(seq, rec, buf)
                if version == VERSION_AUDIO and kind in (FRAME, AUDIO):
                    if last_us is not None and rec["virtual_us"] < last_us and brk != "auclock":
                        problems.append(f"record seq {seq} ({KIND_NAMES[kind]}) goes back: virtual_us "
                                        f"{rec['virtual_us']} after {last_us}")
                    last_us = rec["virtual_us"] if last_us is None else max(last_us, rec["virtual_us"])
                for role, _arg, _o, blen in rec.get("blobs", ()):
                    r = roles.setdefault(role, [0, 0])
                    r[0] += 1
                    r[1] += blen
                if want_range and want_range[0] <= seq < want_range[1]:
                    picked.append(printable(buf, off, kind, nbytes, seq, rec, table))
                off += nbytes
            content.finish()
            audio.finish()
            if ended_at is None and brk != "end":
                problems.append("the stream has no END record: the writer did not close it "
                                "(its .status sidecar says why)")
            unknown = sorted(k for k in kinds if k not in KIND_NAMES
                             or (k == AUDIO and version != VERSION_AUDIO))
            if unknown:
                problems.append(f"records of kinds this reader does not know in a version "
                                f"{version} stream: {unknown}")
        finally:
            buf.release()
            if size:
                mm.close()
    method_rows = []
    for (t, slot), n in sorted(calls.items()):
        iface, name, argc = table.get((t, slot), (f"type{t}", f"slot{slot}", None))
        method_rows.append({"type": t, "slot": slot, "interface": iface, "method": name,
                            "argc": argc, "calls": n, "blob_bytes": method_blobs.get((t, slot), 0)})
    return {
        "path": str(path),
        "bytes": size,
        "version": version,
        "header": {k: v for k, v in head.items() if k not in ("methods", "interfaces", "rules",
                                                             "entries")},
        "meta": meta,
        "interfaces": head.get("interfaces") or [],
        "rules": len(content.rules),
        "entries_table": {e: [n, a] for e, (n, a, _o) in content.entries.items()},
        "records": records,
        "kinds": {KIND_NAMES.get(k, str(k)): {"records": v[0], "bytes": v[1]}
                  for k, v in sorted(kinds.items())},
        "method_calls": sum(calls.values()),
        "distinct_methods": len(calls),
        "methods": method_rows,
        "d3dx": {D3DX_NAMES.get(e, str(e)): n for e, n in sorted(d3dx.items())},
        "d3dx_calls": sum(n for e, n in d3dx.items() if e != DIRECT3D_CREATE9),
        "blobs": {ROLE_NAMES.get(r, str(r)): {"blobs": v[0], "bytes": v[1]}
                  for r, v in sorted(roles.items())},
        "frames": len(frames),
        "first_frame": frames[0] if frames else None,
        "last_frame": frames[-1] if frames else None,
        "notes": notes,
        "note_records": n_notes,
        "state_records": n_state,
        "content_checked": content.checked,
        "objects_introduced": len(content.known),
        "locks_open_at_end": sum(len(v) for v in content.locks.values()),
        "buffer_watch": dict(content.watch),
        "audio": ({"records": sum(audio.ops.values()), "ops": dict(sorted(audio.ops.items())),
                   "pcm_bytes": audio.pcm_bytes, "checked": audio.checked,
                   "buffers": len(audio.buffers), "voices": len(audio.voices),
                   "stores": len(audio.stores), "store_watch": dict(audio.watch)}
                  if version == VERSION_AUDIO else None),
        "audio_rows": [[op, n, i, m, a] for op, (n, i, m, a, _o) in sorted(audio.table.items())],
        "audio_heads": audio.heads,
        "audio_heads_cut": audio.cut,
        "files": content.files,
        "head_calls": head_calls,
        "end": ended_at is not None,
        "problems": problems.closed(),
        "range": picked,
    }


def printable(buf, off: int, kind: int, nbytes: int, seq: int, rec: dict,
              table: dict) -> dict:
    """A record as a reader wants to see it: names, hex words, blob heads."""
    out = {"seq": seq, "kind": KIND_NAMES.get(kind, kind), "offset": off, "bytes": nbytes}
    if kind == CALL:
        iface, name, _ = table.get((rec["type"], rec["slot"]), ("?", "?", None))
        out.update(call=f"{iface}::{name}", type=rec["type"], slot=rec["slot"],
                   this=f"{rec['this']:#010x}", thread=rec["thread"])
    elif kind == D3DX:
        out.update(entry=D3DX_NAMES.get(rec["entry"], rec["entry"]))
    elif kind == AUDIO:
        out.update(api=AUDIO_APIS.get(rec["api"], rec["api"]), op=rec["op"],
                   this=f"{rec['this']:#010x}", thread=rec["thread"], virtual_us=rec["virtual_us"])
    if kind in (CALL, D3DX, AUDIO):
        out["args"] = [f"{a:#010x}" for a in rec["args"]]
        out["eax"] = f"{rec['eax']:#010x}"
        out["blobs"] = [{"role": ROLE_NAMES.get(r, r), "arg": None if a == NO_ARG else a,
                         "len": n, "head": bytes(buf[o:o + min(n, 32)]).hex()}
                        for r, a, o, n in rec["blobs"]]
    elif kind == FRAME:
        out.update(virtual_us=rec["virtual_us"], frames=rec["frames"], polls=rec["polls"])
    elif kind == NOTE:
        out["text"] = rec["text"]
    return out


def blobs_of(path: Path, seq: int) -> list[tuple[int, int, bytes]]:
    """The blobs of record `seq`, whole: [(role, arg, data)]."""
    brk = active_break()
    with open(path, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
        buf = memoryview(mm)
        try:
            _, off = read_header(buf)
            while off + 12 <= len(buf):
                kind, nbytes, s = HEAD.unpack_from(buf, off)
                if nbytes < 12:
                    break
                if s == seq:
                    rec = decode(buf, off, kind, nbytes, brk)
                    return [(r, a, bytes(buf[o:o + n])) for r, a, o, n in rec.get("blobs", ())]
                off += nbytes
        finally:
            buf.release()
    raise KeyError(seq)


def compare_counts(summary: dict, counts: dict) -> list[str]:
    """Where the stream's counts differ from the recorder's own (SA2_REC_COUNTS): per
    (type, slot), per entry point, Presents against SA2S_FRAME records, NOTE records
    against the notes counted (each class's first sixteen and one more are written;
    the counts file has them all), and the buffer watch's counters against the ones
    the stream's own records give (scan's buffer_watch): how many Unlocks set it,
    how many checks ran over how many bytes, and how many buffers were watched at
    exit. Only `changed` is the recorder's alone: the stream holds no bytes after an
    Unlock to compare, and the runner holds it to 0."""
    out = []
    if active_break() != "watch":
        seen, theirs = summary["buffer_watch"], counts.get("buffer_watch")
        if not isinstance(theirs, dict):
            out.append("the counts file has no buffer_watch to hold to the stream's")
        else:
            out += [f"buffer_watch.{k}: {theirs.get(k)} in the counts file, {seen[k]} from the "
                    f"stream's own Unlocks and FRAMEs" for k in WATCH_KEYS
                    if theirs.get(k) != seen[k]]
    mine = {(m["type"], m["slot"]): m["calls"] for m in summary["methods"]}
    theirs = {(m["type"], m["slot"]): m["calls"] for m in counts.get("methods", [])}
    for key in sorted(set(mine) | set(theirs)):
        if mine.get(key, 0) != theirs.get(key, 0):
            out.append(f"method {key[0]}.{key[1]}: {mine.get(key, 0)} records in the stream, "
                       f"{theirs.get(key, 0)} calls counted")
    dx_theirs = {r["name"]: r["calls"] for r in counts.get("entries", [])}
    for name in sorted(set(summary["d3dx"]) | {n for n, c in dx_theirs.items() if c}):
        if summary["d3dx"].get(name, 0) != dx_theirs.get(name, 0):
            out.append(f"{name}: {summary['d3dx'].get(name, 0)} records in the stream, "
                       f"{dx_theirs.get(name, 0)} calls counted")
    if summary["frames"] != counts.get("presents"):
        out.append(f"{summary['frames']} FRAME records against {counts.get('presents')} "
                   f"Presents counted")
    if bool(summary.get("note_records")) != any((counts.get("notes") or {}).values()):
        out.append(f"{summary.get('note_records')} NOTE records, and the counts file's notes "
                   f"are {counts.get('notes')}")
    au, theirs_au = summary.get("audio"), counts.get("audio")
    if (au is None) != (theirs_au is None):
        out.append(f"the stream {'has' if au is not None else 'has no'} audio and the counts file "
                   f"{'has' if theirs_au is not None else 'has no'} audio counts")
    elif au is not None:
        recorded = {r.get("name"): r.get("recorded", 0) for r in theirs_au.get("ops", [])}
        for name in sorted(set(recorded) | set(au["ops"])):
            if au["ops"].get(name, 0) != recorded.get(name, 0):
                out.append(f"audio {name}: {au['ops'].get(name, 0)} records in the stream, "
                           f"{recorded.get(name, 0)} recorded in the counts file")
        if au["pcm_bytes"] != theirs_au.get("pcm_bytes"):
            out.append(f"audio: {au['pcm_bytes']} PCM bytes in the stream, {theirs_au.get('pcm_bytes')} "
                       f"in the counts file")
        if active_break() != "auwatch":
            sw, theirs_sw = au["store_watch"], theirs_au.get("store_watch")
            if not isinstance(theirs_sw, dict):
                out.append("the counts file has no audio store_watch to hold to the stream's")
            else:
                out += [f"audio store_watch.{k}: {theirs_sw.get(k)} in the counts file, {sw[k]} from the "
                        f"stream's own Locks, Unlocks and FRAMEs" for k in WATCH_KEYS
                        if theirs_sw.get(k) != sw[k]]
    return out


def desc_words(f: dict, r: dict) -> tuple[tuple, tuple]:
    """A load's DESC (sa2s_desc_file_texture) and the device's row for it, word for
    word as the row names them: (the stream's, the row's). The row has no kind and no
    created depth: the kind is the load's entry's (LOAD_KIND), and a 2D or cube
    texture's depth is 1 (runtime/sa2_d3d9.h, sa2_d3d9_texsrc)."""
    d, cr, fi, png = f["desc"], r.get("created", {}), r.get("file", {}), r.get("png", {})
    kind = LOAD_KIND.get(f.get("entry"))
    prov = lambda v: PROVENANCE_NAMES.get(v, "none")  # noqa: E731
    # object, container, kind, width, height, depth, levels, format, file_width ..
    # file_format (8..12), levels_from, format_from, dims_from, the four png words,
    # chain_bytes (20), level0_bytes (21)
    mine = (d[0], CONTAINER_NAMES.get(d[1], "unnamed"), d[2], d[3], d[4], d[5], d[6], d[7],
            d[8], d[9], d[10], d[11], d[12], prov(d[13]), prov(d[14]), prov(d[15]),
            d[16], d[17], d[18], d[19], d[20], d[21])
    theirs = (f["texture"], r.get("container"), kind, cr.get("w"), cr.get("h"),
              d[5] if kind == 2 else 1, cr.get("levels"), cr.get("format"),
              fi.get("w"), fi.get("h"), fi.get("depth"), fi.get("levels"), fi.get("format"),
              r.get("levels_from"), r.get("format_from"), r.get("dimensions_from"),
              png.get("bit_depth"), png.get("colour_type"), png.get("interlace"), png.get("trns"),
              cr.get("chain_bytes"), cr.get("level0_bytes"))
    return mine, theirs


def hold_to_run(summary: dict, run: dict, import_rows: list[dict] | None = None) -> list[str]:
    """Where the stream disagrees with the run's own record (run.json): each load's
    FILE blob with the recording device's sha256 and length of that texture's file
    and their total with source_bytes_hashed, each load's DESC with the device's row
    in all 22 words (desc_words), the first calls with the device's call log
    (walk_device_log), and, given the run's import table, each entry's argc with the
    import's."""
    out = []
    d3d9 = run.get("d3d9") or {}
    tff = d3d9.get("textures_from_files") or {}
    rows = {int(r["texture"], 16): r for r in tff.get("rows", []) if not r.get("refused")}
    files = summary.get("files", [])
    for f in files:
        r = rows.get(f["texture"])
        if r is None:
            out.append(f"record seq {f['seq']}: texture {f['texture']:#010x} has no row in the "
                       f"device's record")
            continue
        if f["sha256"] != r["sha256"] or f["bytes"] != r["bytes"]:
            out.append(f"record seq {f['seq']}: the FILE blob of {f['texture']:#010x} is "
                       f"{f['bytes']} bytes, sha256 {str(f['sha256'])[:12]}; the device hashed "
                       f"{r['bytes']} bytes, {r['sha256'][:12]}")
        mine, theirs = desc_words(f, r)
        if mine != theirs:
            out.append(f"record seq {f['seq']}: the DESC of {f['texture']:#010x} is {mine}; the "
                       f"device's row says {theirs}")
    if len(files) != len(rows):
        out.append(f"{len(files)} loads carry a FILE blob and a description; the device "
                   f"answered {len(rows)}")
    total = sum(f["bytes"] or 0 for f in files)
    if total != tff.get("source_bytes_hashed"):
        out.append(f"the FILE blobs hold {total} bytes; the device hashed "
                   f"{tff.get('source_bytes_hashed')}")
    out += walk_device_log(summary, d3d9.get("calls") or [])
    if import_rows is not None:
        table = {r["name"]: r for r in import_rows}
        for e, (name, argc) in summary.get("entries_table", {}).items():
            if int(e) == DIRECT3D_CREATE9:
                continue                  # its SDK version is not seen where it is recorded
            r = table.get(name)
            if r is None or r.get("argc") != argc:
                out.append(f"entry {e} {name}: {argc} arguments in the header; the import "
                           f"table says {r.get('argc') if r else 'nothing'}")
    out += hold_counts_to_run(summary, run, import_rows)
    return out


def hold_counts_to_run(summary: dict, run: dict, import_rows: list[dict] | None = None) -> list[str]:
    """Where the stream's own counts differ from the run's record, which the recording
    device keeps and the recorder never writes: the calls per (interface, slot)
    against d3d9.dynamic_surface, the methods called against distinct_methods, the
    FRAME records against frames.present, the calls with the entry calls against
    method_calls (every thunk call and every d3dx entry the device answers,
    runtime/sa2_d3d9.c note_reserve; Direct3DCreate9 is not one), and the objects
    the stream introduces against objects_built (every object the device made,
    runtime/sa2_d3d9.c obj_new, reached the guest through a call the stream holds).
    With the import table, each entry's records against its import's calls.
    compare_counts holds the stream to the recorder's counts file, which the same
    trampoline counts; this holds it to the device, so the stream and the device
    agree directly and not only through the counts file."""
    if active_break() == "runcounts":
        return []
    d3d9 = run.get("d3d9") if isinstance(run, dict) else None
    if not isinstance(d3d9, dict) or not d3d9.get("dynamic_surface"):
        return ["the run's record has no d3d9 dynamic surface to hold the stream's counts to"]
    out = []
    mine = {(m["interface"], m["slot"]): m["calls"] for m in summary["methods"]}
    theirs = {(r["interface"], r["slot"]): r["calls"] for r in d3d9["dynamic_surface"]}
    for key in sorted(set(mine) | set(theirs)):
        if mine.get(key, 0) != theirs.get(key, 0):
            out.append(f"{key[0]} slot {key[1]}: {mine.get(key, 0)} records in the stream, "
                       f"{theirs.get(key, 0)} calls in the run's record")
    if summary["distinct_methods"] != d3d9.get("distinct_methods"):
        out.append(f"{summary['distinct_methods']} methods in the stream, "
                   f"{d3d9.get('distinct_methods')} in the run's record")
    present = (d3d9.get("frames") or {}).get("present")
    if summary["frames"] != present:
        out.append(f"{summary['frames']} FRAME records against the run's {present} Presents")
    total = summary["method_calls"] + summary["d3dx_calls"]
    if total != d3d9.get("method_calls"):
        out.append(f"{summary['method_calls']} calls and {summary['d3dx_calls']} entry calls "
                   f"in the stream, {d3d9.get('method_calls')} method calls in the run's record")
    if summary["objects_introduced"] != d3d9.get("objects_built"):
        out.append(f"{summary['objects_introduced']} objects introduced in the stream, "
                   f"{d3d9.get('objects_built')} built in the run's record")
    if import_rows is not None:
        names = set(D3DX_NAMES.values())
        calls = {r["name"]: r.get("calls", 0) for r in import_rows if r.get("name") in names}
        for name in sorted(names):
            if summary["d3dx"].get(name, 0) != calls.get(name, 0):
                out.append(f"{name}: {summary['d3dx'].get(name, 0)} records in the stream, "
                           f"{calls.get(name, 0)} calls in the run's import table")
    return out


def audio_keep(run: dict) -> int:
    """How many AUDIO records of each api scan must keep for hold_audio_to_run to walk
    the run's audio device logs: the longer log's length, and some."""
    logs = [len(((run or {}).get(k) or {}).get("calls") or []) for _api, k in AUDIO_DEVICES]
    return max(logs) + 64


def hold_audio_to_run(summary: dict, run: dict, counts: dict | None = None) -> list[str]:
    """Where a version 2 stream's AUDIO records disagree with the audio devices' own record
    of the run (run.json's "dsound" and "xaudio2", which the devices keep and the recorder
    never writes). The recorder's counts file is counted by the same trampoline that writes
    the records, so the two agreeing says nothing of a record the trampoline dropped or
    misjudged; this holds the records, and the counts, to the device. For each device:

      * per op, no more records than the device's calls of its method ("methods"), and,
        with the counts file, every call entered the recorder (entered equal to the
        device's calls), came back (returned equal to entered), and was recorded or
        answered a failure (recorded + failed equal to returned);
      * the device's call log (the first 4,096 calls it reserved, in order) walked
        against its own records in stream order (scan's audio_heads): a logged call of a
        recorded op that answered a failure (a negative HRESULT; ANY_ANSWER's never do)
        has no record, one the device logged as refused (it trapped, ending the run) has
        none, and every other is the next record, with its op, object, arguments, answer
        and virtual time, XAudio2's guest thread (the log keeps its low 8 bits), and the
        out words the device logged (LOG_OUTS);
      * when the log holds every call the device answered, every record was met in the
        walk, each op's records are the log's calls that succeeded and, with the counts
        file, its failures are the log's.

    The walk needs the heads scan kept (its keep_audio, which audio_keep(run) gives).
    A summary that kept fewer of a device's records than its log has calls, or none
    (a summary read back from --json), is a problem for that device and its log is not
    walked: past a log cut short the walk is the only check of the records, so a walk
    that ran out of heads would otherwise pass what it never read.

    A version 1 stream holds no audio: nothing to hold. Empty when they agree."""
    if active_break() == "auheld":
        return []
    au = summary.get("audio")
    if au is None:
        return []
    out = []
    rows = summary.get("audio_rows") or []
    heads, cut = summary.get("audio_heads") or {}, summary.get("audio_heads_cut") or {}
    cops = None
    if counts is not None:
        theirs = counts.get("audio") if isinstance(counts, dict) else None
        if not isinstance(theirs, dict):
            out.append("the counts file has no audio counts to hold to the run's record")
        else:
            cops = {r.get("name"): r for r in theirs.get("ops") or []}
    for api, key in AUDIO_DEVICES:
        mine = [(n, i, m) for _op, n, i, m, _a in rows if AUDIO_IFACES.get(i) == api]
        by_method = {(i, m): n for n, i, m in mine}
        recs = sum(au["ops"].get(n, 0) for n, _i, _m in mine)
        dev = run.get(key) if isinstance(run, dict) else None
        if not isinstance(dev, dict):
            if recs:
                out.append(f"the stream holds {recs} {key} records, and the run's record has no {key} device")
            continue
        calls = {(r.get("interface"), r.get("name")): r.get("calls", 0) for r in dev.get("methods") or []}
        for n, i, m in mine:
            n_dev, n_rec = calls.get((i, m), 0), au["ops"].get(n, 0)
            if n_rec > n_dev:
                out.append(f"audio {n}: {n_rec} records in the stream, and the {key} device answered "
                           f"{n_dev} calls of {i}::{m}")
            if cops is not None:
                c = cops.get(n) or {}
                ent, ret, done, failed = (c.get(k, 0) for k in ("entered", "returned", "recorded", "failed"))
                if ent != n_dev:
                    out.append(f"audio {n}: {ent} calls entered the recorder, and the {key} device "
                               f"answered {n_dev} calls of {i}::{m}")
                if ret != ent:
                    out.append(f"audio {n}: {ent} calls entered the recorder and {ret} came back")
                if done + failed != ret:
                    out.append(f"audio {n}: {done} recorded and {failed} failed of the {ret} that came back")
        log = dev.get("calls") or []
        pending = heads.get(api) or []
        if log and ("audio_heads" not in summary or (cut.get(api) and len(pending) < len(log))):
            out.append(f"the {key} device's log holds {len(log)} calls, and the summary kept the heads of "
                       f"{len(pending)} of the stream's {recs} {key} records to walk it against (scan's "
                       f"keep_audio, which audio_keep(run) gives): the log is not walked")
            continue
        ok_calls = {n: 0 for n, _i, _m in mine}
        failed_calls = {n: 0 for n, _i, _m in mine}
        at, stopped = 0, None
        for row in log:
            n = by_method.get((row.get("interface"), row.get("method")))
            if n is None or row.get("answered") == "refused":
                continue
            ret = int(row["ret"], 16)
            this = int(row["this"], 16)
            args = tuple(int(a, 16) for a in row.get("args", []))
            h = pending[at] if at < len(pending) else None
            if n not in ANY_ANSWER and ret >= 0x80000000:
                failed_calls[n] += 1
                if h is not None and (h[1], h[2], tuple(h[3]), h[4]) == (n, this, args, ret):
                    stopped = (f"record seq {h[0]} ({n}) is the {key} device's call {row['seq']}, which "
                               f"answered {ret:#010x}: a call that failed has no record")
                    break
                continue
            ok_calls[n] += 1
            where = f"the {key} device's call {row['seq']} ({row['interface']}::{row['method']} on {row['this']})"
            if h is None:                           # every record was kept (the check above)
                stopped = f"{where} answered {ret:#010x}, and the stream holds no {key} record for it"
                break
            differ = []
            if h[1] != n:
                differ.append(f"the record is {h[1]}")
            if h[2] != this:
                differ.append(f"its object is {h[2]:#010x}")
            if tuple(h[3]) != args:
                differ.append(f"its arguments are {[f'{a:#x}' for a in h[3]]}, the device's "
                              f"{[f'{a:#x}' for a in args]}")
            if h[4] != ret:
                differ.append(f"its answer is {h[4]:#010x}, the device's {ret:#010x}")
            if h[6] != row.get("virtual_us"):
                differ.append(f"its virtual_us is {h[6]}, the device's {row.get('virtual_us')}")
            if api == 2 and (h[5] & 0xFF) != row.get("thread"):
                differ.append(f"its guest thread is {h[5]}, the device's {row.get('thread')}")
            for k, word in enumerate(h[7]):
                if word is not None and h[1] == n and word != int((row.get("out") or ["0x0", "0x0"])[k], 16):
                    differ.append(f"its out{k} word is {word:#010x}, the device's {row['out'][k]}")
            if differ:
                stopped = f"{where} is not the stream's next {key} record, seq {h[0]}: {'; '.join(differ)}"
                break
            at += 1
        if stopped:
            out.append(stopped)
            continue
        if len(log) == dev.get("method_calls"):
            if at != recs:
                out.append(f"the {key} device's log holds every call it answered, and {recs - at} of the "
                           f"stream's {recs} {key} records are none of them")
            for n, _i, _m in mine:
                if au["ops"].get(n, 0) != ok_calls[n]:
                    out.append(f"audio {n}: {au['ops'].get(n, 0)} records, and the {key} device's log "
                               f"holds {ok_calls[n]} calls of it that succeeded")
                if cops is not None and (cops.get(n) or {}).get("failed", 0) != failed_calls[n]:
                    out.append(f"audio {n}: {(cops.get(n) or {}).get('failed', 0)} failed in the counts "
                               f"file, and the {key} device's log holds {failed_calls[n]} that failed")
    return out


DEVICE_LOG_WINDOW = 16


def walk_device_log(summary: dict, log: list[dict]) -> list[str]:
    """The recording device's call log (the first 8,192 calls it reserved, in that
    order) against the stream's first records (in the order the calls finished).
    The two orders differ only where a call finishes inside another, and the one
    place that happens is the frame's wait (FRAME_WAIT): another guest thread's
    calls run to completion inside it, and are recorded before the call that waits.
    So each row must be the stream's next unmatched record, except a frame-wait
    call's: it may be met up to DEVICE_LOG_WINDOW records on, and every record it
    passes must be another thread's -- a CALL on another guest thread, or a D3DX
    record, which carries no thread (sa2_stream.h). Direct3DCreate9 is not in the
    device's log."""
    ifaces = summary.get("interfaces") or []
    pending = [h for h in summary.get("head_calls", [])
               if not (h[0] == "d3dx" and h[2] == DIRECT3D_CREATE9)]
    if len(pending) < len(log):
        return [f"the device logged {len(log)} calls, and the stream holds {len(pending)} "
                f"to walk them against"]

    def same(row: dict, h: tuple) -> bool:
        slot = row["slot"]
        args = [int(a, 16) for a in row.get("args", [])]
        if slot >= 128:
            return (h[0] == "d3dx" and h[2] == DEVICE_SLOT_ENTRY.get(slot)
                    and list(h[5][:len(args)]) == args[:len(h[5])]
                    and h[6] == int(row["ret"], 16))
        return (h[0] == "call" and h[2] < len(ifaces) and ifaces[h[2]] == row["interface"]
                and h[3] == slot and h[4] == int(row["this"], 16)
                and list(h[5][:len(args)]) == args and h[6] == int(row["ret"], 16))

    at = 0
    for row in log:
        waits = (row["interface"], row["method"]) in FRAME_WAIT
        reach = DEVICE_LOG_WINDOW if waits else 1
        hit = next((k for k in range(at, min(at + reach, len(pending)))
                    if pending[k] is not None and same(row, pending[k])), None)
        if hit is None:
            h = pending[at] if at < len(pending) else None
            where = (f"none of the stream's next {DEVICE_LOG_WINDOW} records" if waits
                     else "not the stream's next record")
            return [f"the device's call {row['seq']} ({row['interface']}::{row['method']}) is "
                    f"{where} (from seq {h[1] if h else 'the end'})"]
        passed = [pending[k] for k in range(at, hit) if pending[k] is not None
                  and pending[k][0] == "call" and pending[k][7] == pending[hit][7]]
        if passed:
            return [f"the stream records seq {passed[0][1]} before seq {pending[hit][1]} "
                    f"({row['interface']}::{row['method']}), both on guest thread "
                    f"{pending[hit][7]}, where the device logged it after: a call finishes "
                    f"inside another only on another thread"]
        pending[hit] = None
        while at < len(pending) and pending[at] is None:
            at += 1
    return []


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("stream", type=Path)
    ap.add_argument("--json", type=Path, help="write the summary here as JSON")
    ap.add_argument("--range", help="FROM:TO, the records of seq FROM..TO-1 in full")
    ap.add_argument("--counts", type=Path, help="the recorder's counts file to hold the "
                                                "stream to")
    ap.add_argument("--run", type=Path, help="the run's record (run.json) to hold the stream to")
    ap.add_argument("--imports", type=Path, help="the run's import table, with --run")
    ap.add_argument("--format", type=Path, help="host/sa2_stream.h: hold the header's rule and "
                                                "entry tables to its lines")
    args = ap.parse_args(argv)
    rng = None
    if args.range:
        try:
            a, b = args.range.split(":")
            rng = (int(a, 0), int(b, 0))
        except ValueError:
            print(f"--range {args.range!r} is not FROM:TO", file=sys.stderr)
            return 2
    run = json.loads(args.run.read_text()) if args.run else None
    keep = len(((run or {}).get("d3d9") or {}).get("calls") or []) + 16 if run else 0
    doc = doc_table(args.format.read_text()) if args.format else None
    try:
        s = scan(args.stream, want_range=rng, keep_calls=keep, doc=doc,
                 keep_audio=audio_keep(run) if run else 0)
    except StreamError as e:
        print(f"{args.stream}: {e}", file=sys.stderr)
        return 1
    counts = json.loads(args.counts.read_text()) if args.counts else None
    if counts is not None:
        s["counts_problems"] = compare_counts(s, counts)
    if run is not None:
        rows = json.loads(args.imports.read_text())["rows"] if args.imports else None
        s["run_problems"] = hold_to_run(s, run, rows) + hold_audio_to_run(s, run, counts)
    if args.json:
        args.json.write_text(json.dumps({k: v for k, v in s.items()
                                         if k not in ("head_calls", "audio_heads")}, indent=1) + "\n")
    if rng:
        for r in s["range"]:
            print(json.dumps(r))
    else:
        print(f"{s['path']}: {s['bytes']} bytes, {s['records']} records, end {s['end']}")
        for k, v in s["kinds"].items():
            print(f"  {k:6} {v['records']:>10} records {v['bytes']:>14} bytes")
        print(f"  method calls {s['method_calls']} over {s['distinct_methods']} methods; "
              f"d3dx calls {s['d3dx_calls']}; frames {s['frames']}; records content-checked "
              f"{s['content_checked']}; objects introduced {s['objects_introduced']}")
        for m in s["methods"]:
            print(f"    {m['type']:>2}.{m['slot']:<3} {m['interface']}::{m['method']:<28} "
                  f"{m['calls']:>10}  blob bytes {m['blob_bytes']}")
        for name, n in s["d3dx"].items():
            print(f"    entry {name:<41} {n:>10}")
        for role, v in s["blobs"].items():
            print(f"  blobs {role:7} {v['blobs']:>10} {v['bytes']:>14} bytes")
        if s["first_frame"]:
            print(f"  first frame {s['first_frame']}, last {s['last_frame']}")
        if s["audio"] is not None:
            au = s["audio"]
            print(f"  audio (version {s['version']}): {au['records']} records, {au['pcm_bytes']} PCM "
                  f"bytes, {au['buffers']} buffers, {au['voices']} voices")
            for name, n in au["ops"].items():
                print(f"    {name:<20} {n:>10}")
        for n in s["notes"][:10]:
            print(f"  note: {n}")
    bad = s["problems"] + s.get("counts_problems", []) + s.get("run_problems", [])
    for p in bad:
        print(f"PROBLEM {p}", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
