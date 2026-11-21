#!/usr/bin/env python3
"""Read a device profile exactly as the runtime does, and check a capture's.

Subtitle: parse a sa2-device-profile.txt with runtime/sa2_devprofile.c's
grammar and refusals, word for word, and print what a capture's receipt
records of it.

    .venv/bin/python tools/s4/device_profile.py <sa2-device-profile.txt>
    .venv/bin/python tools/s4/device_profile.py <sa2-device-profile.txt> --summary
    .venv/bin/python tools/s4/device_profile.py --capture <capture directory>
    .venv/bin/python tools/s4/device_profile.py --default

Why
---
The user ruled on 2026-09-23 that the host's device set is a recorded
property of each capture: the runner records it in the receipt, the lifted
replay reads the same profile back, a run with no capture uses a plain
keyboard-and-mouse default, and a capture whose profile the runtime cannot
reproduce is refused, not approximated. The runtime reads the profile at
configuration and traps on a bad one outside any sa2_call, so the run dies
before the guest starts and the only record is a line on stderr. This reads
the same file the same way first, so the oracle runner can say in the receipt
whether a capture is one the lifted build can replay, and the replay tools
refuse a profile before they link a build.

The grammar, and what the runtime can reproduce, are the comment at the top of
runtime/sa2_devprofile.h; that comment is the one definition, and this file
and runtime/sa2_devprofile.c both follow it. Every refusal below has the C
reader's words, in the C reader's order, and
Documentation/scripts/tests/test_device_profile.py builds the C reader and
holds the two to each other.

The replay tools import this for three more checks: that a capture's receipt
records the profile file that is beside it (`capture_profile`), that a
registered replay's profile is its capture's and every repeat's
(`replay_profile`), and that a lifted run read the profile it was handed
(`check_run`, from crt_probe's `device_profile` block). They also take the
environment a lifted run inherits from here (`clean_environ`), without the
profile and without the host-device overrides, so nothing in a shell decides a
run's devices.

The capture's directory travels with its profile
------------------------------------------------
The game asks the machine where it is as well as what is plugged in: its
current directory (GetCurrentDirectoryA, and every full path GetFullPathNameA
builds from it, CRI's file table among them) and its own file name
(GetModuleFileNameA, and the command line the runtime builds from it). Those
are answers of the capture's machine like the processor count, so a lifted run
that stands for a capture answers the capture's (`guest_paths`: the receipt's
`cwd` and the program its `command` launched, as Wine names them), and a run
that stands for none keeps the runtime's placeholders (`DEFAULT_GUEST`).
`capture_profile` returns them with the profile, `run_env` sets them beside it,
`clean_environ` strips them from a shell, and `check_run` refuses a run whose
own record (crt_probe's `virtual` block) shows another directory than the one
it was handed; `run_guest` is what a run record keeps of it. The tick-380
divergence is why (agents-journals/s4/2026-09-24-tick-380-xeci.md).

The locale travels in the profile too
-------------------------------------
The game's C runtime asks the host for its locale whenever an archive loader
calls setlocale(LC_ALL, ""): the user's LCID, the code pages and the locale
data its category initialisers read. Natively every category qualifies the
user's locale; a runtime that refuses the data leaves three of them in "C". The
profile's optional locale section (`Locale`; runtime/sa2_devprofile.h, "The
locale") carries the native answers, and the probe writes it for every capture,
so a capture taken with the probe in the tree has it (`capture_profile` refuses
one taken with any other). A file without it is the runtime's default locale,
Wine's data for 0x0409 (runtime/sa2_locale.h). SA2_GUEST_LCID and SA2_GUEST_ACP
move that default, so they are host overrides like the processor count:
stripped from a shell, and refused by the runtime beside a profile that records
a locale.

So do the environment block and the standard handles
----------------------------------------------------
The C runtime reads two more host answers at start-up: the environment block
(GetEnvironmentStringsW, from which _environ is built) and the standard handles
with their file types (__ioinit). Each is the capture's process's, so the probe
records them too (`Environment`, `std_handles`; runtime/sa2_devprofile.h, "The
environment" and "The standard handles"): every string of the block at Init and
how many the C runtime's _environ holds, which is the block it saw at start-up
(`Environment.block` is what the runtime answers), and GetStdHandle's three
answers with GetFileType's. A file without them is an empty block and no
standard handles, the default's. SA2_STD_HANDLES=pipe, the replay
configuration's standard handles before profiles recorded them, is a host
override like the rest: stripped from a shell, and refused by the runtime
beside a profile that records the handles.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

FILE = "sa2-device-profile.txt"
ENV = "SA2_DEVICE_PROFILE"
DEFAULT = "default"
PROBE = "harness/replay/native_device_profile.c"
HEADER = "sa2-device-profile 1"

# runtime/sa2_devprofile.h's limits, each a consumer module's capacity there.
MAX_GAMECTRL = 16                    # runtime/sa2_dinput.h SA2_DI_MAX_ENUM
MAX_WMI_PNP = 253                    # runtime/sa2_wbem.h's class-object slots
MAX_DDRAW = 6                        # runtime/sa2_ddraw.h's enumeration records
MAX_PROCESSORS = 32                  # the bits of a 32-bit process's affinity mask
NAME_MAX = 260
DEVICEID_MAX = 512
XINPUT_SLOTS = 4
WMI_BATCH = 20
NOT_CONNECTED = 1167
DEFAULT_VIDMEM = 0x10000000
DEFAULT_XAUDIO2 = (0, 2, 48000)      # flags, channels, rate: stereo at the game's 48000 Hz
GUID_CHARS = 38
# The locale section (runtime/sa2_devprofile.h, "The locale" and
# SA2_DP_LOCALE_LCTYPES): the LCTYPEs in the file's order, a value's most
# UTF-8 bytes, and the one ANSI code page the runtime implements.
LOCALE_LCTYPES = ((0x0001, 0x0003, 0x0004, 0x0005, 0x0007, 0x0008, 0x0009, 0x000A, 0x000B,
                   0x000E, 0x000F, 0x0010, 0x0014, 0x0015, 0x0016, 0x0017, 0x0018, 0x0019,
                   0x001A, 0x001F, 0x0020) + tuple(range(0x0028, 0x0058))
                  + (0x1001, 0x1002, 0x1003, 0x1004, 0x1009))
LOCALE_VALUE_MAX = 127
LOCALE_ACP = 1252
# The environment and standard-handle sections (runtime/sa2_devprofile.h, "The
# environment", "The standard handles", SA2_DP_ENV_* and SA2_DP_STD_HANDLES).
ENV_MAX_STRINGS = 256
ENV_MAX_UNITS = 16384
ENV_VALUE_MAX = 4095
STD_HANDLES = 3
STD_NAMES = ("stdin", "stdout", "stderr")
# runtime/sa2_codepage.c's 0x80..0x9F: the characters code page 1252 has
# above U+00FF, and the five unassigned bytes that map to themselves.
CP1252_HIGH = (0x20AC, 0x0081, 0x201A, 0x0192, 0x201E, 0x2026, 0x2020, 0x2021,
               0x02C6, 0x2030, 0x0160, 0x2039, 0x0152, 0x008D, 0x017D, 0x008F,
               0x0090, 0x2018, 0x2019, 0x201C, 0x201D, 0x2022, 0x2013, 0x2014,
               0x02DC, 0x2122, 0x0161, 0x203A, 0x0153, 0x009D, 0x017E, 0x0178)


def _line(fixed: str, guids: int = 0, strings: tuple[int, ...] = ()) -> int:
    """A record kind's longest line, its LF included: the fixed text, then each
    GUID and each string (of at most that many bytes) at its longest spelling,
    the quotes and \\xhh for every byte."""
    return len(fixed) + 1 + guids * GUID_CHARS + sum(2 + 4 * n for n in strings)


# The largest file a reader accepts: SA2_DP_MAX_BYTES, term for term, each
# record kind's longest line times the most of it a file can hold, with every
# dec at the most digits an accepted file holds there.
MAX_BYTES = (
    _line(HEADER)
    + _line("system processors=32 process_mask=0x00000000 system_mask=0x00000000")
    + _line("dinput8_create hr=0x00000000")
    + _line("dinput8_syskeyboard hr=0x00000000")
    + _line("dinput8_sysmouse hr=0x00000000")
    + MAX_GAMECTRL * _line("dinput8_gamectrl index=15 instance= product= ffdriver= "
                           "devtype=0x00000000 usage_page=0x00000000 usage=0x00000000 "
                           "instance_name= product_name= create=0x00000000",
                           3, (NAME_MAX - 1, NAME_MAX - 1))
    + _line("dinput8_enum hr=0x00000000 count=16")
    + XINPUT_SLOTS * _line("xinput_caps slot=3 code=0x00000000")
    + _line("wmi_coinit hr=0x00000000") + _line("wmi_locator hr=0x00000000")
    + _line("wmi_connect hr=0x00000000") + _line("wmi_blanket hr=0x00000000")
    + _line("wmi_enum hr=0x00000000")
    # every full batch, one short batch and the empty one that ends the run
    + (MAX_WMI_PNP // WMI_BATCH + 2) * _line("wmi_next hr=0x00000000 returned=20")
    + MAX_WMI_PNP * _line("wmi_pnp index=252 get=0x00000000 vt=0x00000008 deviceid=", 0,
                          (DEVICEID_MAX - 1,))
    + _line("wmi_entities count=253")
    + _line("d3d9_adapters count=1")
    + MAX_DDRAW * _line("ddraw_device index=5 guid= monitor=adapter0 description= name=", 1,
                        (NAME_MAX - 1, NAME_MAX - 1))
    + _line("ddraw_enum hr=0x00000000 count=6")
    + _line("ddraw_create guid= hr=0x00000000", 1)
    + _line("ddraw_query hr=0x00000000")
    + _line("ddraw_vidmem hr=0x00000000 total=0x00000000")
    + _line("xaudio2_master voice=1 flags=0x00000000 channels=4294967295 rate=4294967295")
    + _line("locale_user lcid=0x00000000 system=0x00000000 acp=4294967295 oemcp=4294967295 "
            "valid=1")
    + len(LOCALE_LCTYPES) * _line("locale_info lctype=0x00000000 error=0x00000000 value=", 0,
                                  (LOCALE_VALUE_MAX,))
    + _line("environment strings=256 crt=256")
    # each string's line and quotes; then each unit of the block as three \xhh
    + ENV_MAX_STRINGS * _line("env index=255 value=", 0, (0,))
    + 4 * 3 * ENV_MAX_UNITS
    + STD_HANDLES * _line("std_handle which=2 handle=0x00000000 type=3")
    + _line("end"))

# The host-device overrides the runtime honours on the default profile only
# (SA2_XINPUT_PAD, the falsifier's pad at rest; SA2_GUEST_CPUS, the processor
# count; SA2_GUEST_LCID and SA2_GUEST_ACP, the locale's placeholders, refused
# beside a profile that records a locale; SA2_STD_HANDLES, the standard handles
# as pipes, refused beside a profile that records them) and traps on beside a
# capture's profile. A lifted run's devices are its capture's, so the tools strip them
# with the profile itself.
HOST_OVERRIDES = ("SA2_XINPUT_PAD", "SA2_GUEST_CPUS", "SA2_GUEST_LCID", "SA2_GUEST_ACP",
                  "SA2_STD_HANDLES")
# The guest's current directory and executable path (runtime/sa2_imports.c
# sa2_imports_config_from_env), which the runtime also takes as the guest-side
# name of the installation (runtime/sa2_files.h, Decision 1).
GUEST_ENV = {"cwd": "SA2_GUEST_CWD", "exe_path": "SA2_GUEST_EXE_PATH"}
# runtime/sa2_imports.c's SA2_DEFAULT_CWD and SA2_DEFAULT_EXE_PATH: what a run
# that stands for no capture answers, and what its record must show.
DEFAULT_GUEST = {"cwd": "C:\\SA2", "exe_path": "C:\\SA2\\sonic2app.exe"}
# Play's low-FPS speed fix (runtime/sa2_frameskip.c: N game ticks per drawn stage frame and the
# clock moved N frames), which the Android session sets from its settings screen and
# tools/s5/play_live.py from its --frameskip, and no registered run may: a run a shell left it on
# in would record frames of N ticks as frames of one, so it is stripped with the rest.
FRAMESKIP_ENV = ("SA2_PLAY_FRAMESKIP",)
# The drive the oracle's prefix names the host's root by (its dosdevices hold
# c: -> ../drive_c and z: -> /), so every path outside drive_c, the mirror the
# runner launches from among them, is Z: and the Unix path with backslashes.
# Checked against the game's own words: CRI's file table holds the opening
# movie's full path, Z:\home\...\oracle\game\resource\gd_pc\op360us.sfd, in
# every native boot capture (the tick-380 journal), and Wine's +loaddll trace
# names the image Z:\...\oracle\game\sonic2app-unpacked.exe.
WINE_ROOT_DRIVE = "Z:"
# The token before the program in the runner's launch line
# (tools/oracle/run_oracle_probe.py: `proton runinprefix ./sonic2app-unpacked.exe`).
LAUNCHER = "runinprefix"
# Play's own resolution answer (runtime/sa2_widescreen.c: the config's Width and Height rewritten
# for the device's aspect), its HUD at the 4:3 positions (SA2_PLAY_HUD43), its tweaks
# (SA2_TWEAKS, runtime/sa2_tweaks.h) and its text language (SA2_TEXT_LANGUAGE,
# runtime/sa2_text_language.c), which the launcher and the Android session set and no replay
# may: a registered run keeps the default resolution, the port's HUD, the game's own behaviour
# and the config's language, so one left in a shell is stripped with the rest.
PLAY_ENV = ("SA2_PLAY_RES", "SA2_PLAY_ASPECT", "SA2_PLAY_HUD43", "SA2_TWEAKS", "SA2_TEXT_LANGUAGE")
# What only a tool's device_profile argument decides, and nothing in a shell.
OWNED = (ENV,) + HOST_OVERRIDES + tuple(GUEST_ENV.values()) + PLAY_ENV + FRAMESKIP_ENV
# The system devices' GUIDs (0x0087360C, 0x008735FC in the image).
GUID_SYSKEYBOARD = uuid.UUID("6F1D2B61-D5A0-11CF-BFC7-444553540000").bytes_le
GUID_SYSMOUSE = uuid.UUID("6F1D2B60-D5A0-11CF-BFC7-444553540000").bytes_le

MALFORMED, UNREPRODUCIBLE, UNMEASURED = "malformed", "unreproducible", "unmeasured"

HEX, DEC, GUID, GUIDN, STR, STRN, MON = range(7)
RECORDS: dict[str, tuple[tuple[str, int, int], ...]] = {
    "system": (("processors", DEC, 0), ("process_mask", HEX, 0), ("system_mask", HEX, 0)),
    "dinput8_create": (("hr", HEX, 0),),
    "dinput8_syskeyboard": (("hr", HEX, 0),),
    "dinput8_sysmouse": (("hr", HEX, 0),),
    "dinput8_gamectrl": (("index", DEC, 0), ("instance", GUID, 0), ("product", GUID, 0),
                         ("ffdriver", GUID, 0), ("devtype", HEX, 0), ("usage_page", HEX, 0),
                         ("usage", HEX, 0), ("instance_name", STR, NAME_MAX - 1),
                         ("product_name", STR, NAME_MAX - 1), ("create", HEX, 0)),
    "dinput8_enum": (("hr", HEX, 0), ("count", DEC, 0)),
    "xinput_caps": (("slot", DEC, 0), ("code", HEX, 0)),
    "wmi_coinit": (("hr", HEX, 0),),
    "wmi_locator": (("hr", HEX, 0),),
    "wmi_connect": (("hr", HEX, 0),),
    "wmi_blanket": (("hr", HEX, 0),),
    "wmi_enum": (("hr", HEX, 0),),
    "wmi_next": (("hr", HEX, 0), ("returned", DEC, 0)),
    "wmi_pnp": (("index", DEC, 0), ("get", HEX, 0), ("vt", HEX, 0),
                ("deviceid", STRN, DEVICEID_MAX - 1)),
    "wmi_entities": (("count", DEC, 0),),
    "d3d9_adapters": (("count", DEC, 0),),
    "ddraw_device": (("index", DEC, 0), ("guid", GUIDN, 0), ("monitor", MON, 0),
                     ("description", STR, NAME_MAX - 1), ("name", STR, NAME_MAX - 1)),
    "ddraw_enum": (("hr", HEX, 0), ("count", DEC, 0)),
    "ddraw_create": (("guid", GUID, 0), ("hr", HEX, 0)),
    "ddraw_query": (("hr", HEX, 0),),
    "ddraw_vidmem": (("hr", HEX, 0), ("total", HEX, 0)),
    "xaudio2_master": (("voice", DEC, 0), ("flags", HEX, 0), ("channels", DEC, 0),
                       ("rate", DEC, 0)),
    "locale_user": (("lcid", HEX, 0), ("system", HEX, 0), ("acp", DEC, 0), ("oemcp", DEC, 0),
                    ("valid", DEC, 0)),
    "locale_info": (("lctype", HEX, 0), ("error", HEX, 0), ("value", STRN, LOCALE_VALUE_MAX)),
    "environment": (("strings", DEC, 0), ("crt", DEC, 0)),
    "env": (("index", DEC, 0), ("value", STR, ENV_VALUE_MAX)),
    "std_handle": (("which", DEC, 0), ("handle", HEX, 0), ("type", DEC, 0)),
    "end": (),
    "error": (("step", STR, MAX_BYTES), ("code", HEX, 0)),
}

_HEX = re.compile(r"0x[0-9a-f]{8}")
_DEC = re.compile(r"0|[1-9][0-9]{0,9}")
_GUID = re.compile(r"\{[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}\}")
ZERO_GUID = bytes(16)


class Refusal(ValueError):
    """A profile, or a capture's record of one, that a lifted run must not use."""


class ProfileError(Refusal):
    """The file itself: where, and the runtime's own words for what is wrong."""

    def __init__(self, source: str, line: int, message: str, kind: str = MALFORMED):
        super().__init__(f"{source}:{line}: {message}")
        self.source, self.line, self.message, self.kind = source, line, message, kind


@dataclass(frozen=True)
class GameCtrl:
    instance: bytes
    product: bytes
    ffdriver: bytes
    devtype: int
    usage_page: int
    usage: int
    instance_name: bytes
    product_name: bytes
    create: int


@dataclass(frozen=True)
class DDrawDevice:
    guid: bytes | None
    monitor: int                     # -1 for a null HMONITOR, else the Direct3D adapter
    description: bytes
    name: bytes


@dataclass(frozen=True)
class Locale:
    """The locale section: the user's LCID, the system's, the code pages, and
    for each LCTYPE of LOCALE_LCTYPES, in order, (error, value): (0, the UTF-8
    of GetLocaleInfoW's answer) or (the error it failed with, None)."""
    lcid: int
    system: int
    acp: int
    oemcp: int
    info: tuple[tuple[int, bytes | None], ...]


@dataclass(frozen=True)
class Environment:
    """The environment section: every string of the block at Init, in order,
    as UTF-8, and `crt`, how many of the first the C runtime's _environ holds
    -- the block it saw at start-up, which the runtime answers."""
    strings: tuple[bytes, ...]
    crt: int

    def block(self) -> bytes:
        """GetEnvironmentStringsW's answer, UTF-16LE: the first `crt` strings,
        each with its NUL, and the block's final NUL."""
        return b"".join(s.decode("utf-8").encode("utf-16-le") + b"\0\0"
                        for s in self.strings[:self.crt]) + b"\0\0"


@dataclass(frozen=True)
class Profile:
    """What an accepted profile says; the answers a reader accepts only one
    value of (sa2_devprofile.h) are implied, not stored."""
    processors: int = 1              # the runtime's one processor before profiles
    process_mask: int = 1
    system_mask: int = 1
    syskeyboard: int = 0
    sysmouse: int = 0
    gamectrl: tuple[GameCtrl, ...] = ()
    xinput: tuple[int, ...] = (NOT_CONNECTED,) * XINPUT_SLOTS
    coinit: int = 0
    blanket: int = 0
    pnp: tuple[bytes, ...] = ()
    ddraw: tuple[DDrawDevice, ...] = field(default_factory=lambda: (
        DDrawDevice(None, -1, b"DirectDraw HAL", b"display"),))
    vidmem_hr: int = 0
    vidmem_total: int = DEFAULT_VIDMEM
    xaudio2: tuple[int, int, int] = DEFAULT_XAUDIO2
    locale: Locale | None = None     # None: no section, the runtime's built-in answers
    environment: Environment | None = None   # None: no section, an empty block
    std_handles: tuple[tuple[int, int], ...] | None = None   # (handle, type), or no section


def default() -> Profile:
    """The built-in profile a run with no capture gets (sa2_devprofile.h)."""
    return Profile()


# --------------------------------------------------------------- the reader

@dataclass
class _Value:
    u: int = 0
    null: bool = False
    mon: int = 0                     # 0 null, 1 adapter, 2 other
    guid: bytes = ZERO_GUID
    data: bytes = b""
    raw: str = ""


def _hexd(c: str) -> int:
    return "0123456789abcdef".find(c) if c else -1


class _Reader:
    def __init__(self, data: bytes, source: str):
        self.data, self.source = data, source
        self.pos, self.line = 0, 0
        self.s, self.k = "", 0

    def fail(self, message: str, kind: str = MALFORMED):
        raise ProfileError(self.source, self.line, message, kind)

    def next_line(self) -> None:
        self.line += 1
        if self.pos >= len(self.data):
            self.fail("the file ends before `end`")
        nl = self.data.find(b"\n", self.pos)
        end = nl if nl >= 0 else len(self.data)
        raw = self.data[self.pos:end]
        for b in raw:
            if b < 0x20 or b > 0x7E:
                self.fail(f"a byte outside 0x20-0x7e (0x{b:02x})")
        if nl < 0:
            self.fail("the last line does not end in LF")
        if not raw:
            self.fail("an empty line")
        self.s, self.k, self.pos = raw.decode("ascii"), 0, end + 1

    def token(self) -> str:
        end = self.s.find(" ", self.k)
        end = len(self.s) if end < 0 else end
        t, self.k = self.s[self.k:end], end
        return t

    def string(self, key: str, mx: int, v: _Value) -> None:
        s, n = self.s, len(self.s)
        if self.k >= n or s[self.k] != '"':
            self.fail(f"field `{key}`: not a quoted string")
        start, i, out = self.k, self.k + 1, bytearray()
        while True:
            if i >= n:
                self.fail(f"field `{key}`: the string does not end")
            c = s[i]
            if c == '"':
                break
            if c == "\\":
                if i + 1 >= n:
                    self.fail(f"field `{key}`: the string does not end")
                e = s[i + 1]
                if e in "\"\\":
                    byte, i = ord(e), i + 2
                elif e == "x":
                    hi = _hexd(s[i + 2]) if i + 2 < n else -1
                    lo = _hexd(s[i + 3]) if i + 3 < n else -1
                    if hi < 0 or lo < 0:
                        self.fail(f"field `{key}`: \\x is not followed by two lower-case hex "
                                  "digits")
                    byte = hi << 4 | lo
                    if not byte:
                        self.fail(f"field `{key}`: \\x00 in a string")
                    if 0x20 <= byte <= 0x7E:
                        self.fail(f"field `{key}`: \\x{byte:02x} is not that byte's spelling")
                    i += 4
                else:
                    self.fail(f"field `{key}`: an escape other than \\\", \\\\ or \\xhh")
            else:
                byte, i = ord(c), i + 1
            out.append(byte)
        if len(out) > mx:
            self.fail(f"field `{key}`: {len(out)} bytes, more than the {mx} the runtime holds")
        i += 1
        v.data, v.raw, self.k = bytes(out), s[start:i], i
        if self.k < n and s[self.k] != " ":
            self.fail(f"field `{key}`: text after the closing quote")

    def value(self, key: str, typ: int, mx: int) -> _Value:
        v = _Value()
        if typ == HEX:
            t = self.token()
            if not _HEX.fullmatch(t):
                self.fail(f"field `{key}`: not 0x and eight lower-case hex digits")
            v.u = int(t, 16)
        elif typ == DEC:
            t = self.token()
            if not _DEC.fullmatch(t) or int(t) > 0xFFFFFFFF:
                self.fail(f"field `{key}`: not a decimal number")
            v.u = int(t)
        elif typ in (GUID, GUIDN):
            t = self.token()
            if typ == GUIDN and t == "null":
                v.null = True
            elif _GUID.fullmatch(t):
                v.guid = uuid.UUID(t[1:-1]).bytes_le
            else:
                self.fail(f"field `{key}`: not a GUID in registry form")
        elif typ == MON:
            t = self.token()
            if t == "null":
                v.null, v.mon = True, 0
            elif t == "other":
                v.mon = 2
            elif (len(t) > 7 and t.startswith("adapter") and _DEC.fullmatch(t[7:])
                  and int(t[7:]) <= 0xFFFFFFFF):
                v.mon, v.u = 1, int(t[7:])
            else:
                self.fail(f"field `{key}`: not null, other or adapter<n>")
        else:
            s, k = self.s, self.k
            if typ == STRN and s[k:k + 4] == "null" and (k + 4 == len(s) or s[k + 4] == " "):
                self.k, v.null = k + 4, True
            else:
                self.string(key, mx, v)
        return v

    def record(self, kinds: tuple[str, ...]) -> tuple[int, list[_Value]]:
        """The next record, which must be one of `kinds`; an `error` record in
        its place refuses. Returns the index of the kind found, and its values."""
        self.next_line()
        kn = self.s.find(" ")
        kn = len(self.s) if kn < 0 else kn
        kind = self.s[:kn]
        known = kind in RECORDS and kn < 40
        if kind not in kinds and not (known and kind == "error"):
            quoted = [f"`{k}`" for k in kinds]
            want = quoted[0] if len(quoted) == 1 else \
                ", ".join(quoted[:-1]) + " or " + quoted[-1]
            self.fail(f"expected {want}, found `{kind[:32]}`")
        self.k = kn
        vals = []
        for key, typ, mx in RECORDS[kind]:
            kl = len(key)
            if (self.k + 1 + kl + 1 > len(self.s) or self.s[self.k] != " "
                    or self.s[self.k + 1:self.k + 1 + kl] != key
                    or self.s[self.k + 1 + kl] != "="):
                self.fail(f"record `{kind}`: expected ` {key}=` at column {self.k + 1}")
            self.k += kl + 2
            vals.append(self.value(key, typ, mx))
        if self.k != len(self.s):
            self.fail(f"record `{kind}`: text after its last field")
        if kind not in kinds:
            self.fail(f"the probe could not measure {vals[0].raw} (code 0x{vals[1].u:08x})",
                      UNMEASURED)
        return kinds.index(kind), vals

    def one(self, kind: str) -> list[_Value]:
        return self.record((kind,))[1]


def _locale_section(r: _Reader, v: list[_Value]) -> Locale:
    """The locale section, its `locale_user` record in v."""
    u = UNREPRODUCIBLE
    if not v[0].u:
        r.fail("field `lcid`: 0 names no locale")
    if v[4].u > 1:
        r.fail(f"field `valid`: {v[4].u} is neither 0 nor 1")
    if not v[4].u:
        r.fail(f"IsValidLocale answered 0 for the user's locale 0x{v[0].u:08x} natively, and "
               "the runtime's answers 1", u)
    if v[2].u != LOCALE_ACP:
        r.fail(f"GetACP answered {v[2].u} natively, and the runtime's code-page tables are "
               f"{LOCALE_ACP}'s", u)
    lcid, system, acp, oemcp = v[0].u, v[1].u, v[2].u, v[3].u
    info = []
    for want in LOCALE_LCTYPES:
        v = r.one("locale_info")
        t = v[0].u
        if t != want:
            r.fail(f"LCTYPE 0x{t:08x} where 0x{want:08x} was next")
        if (not v[1].u) != (not v[2].null):
            r.fail(f"LCTYPE 0x{t:08x}: a value is null exactly when its error is not 0")
        if v[1].u:
            info.append((v[1].u, None))
            continue
        try:
            text = v[2].data.decode("utf-8")
        except UnicodeDecodeError:
            r.fail(f"LCTYPE 0x{t:08x}: not valid UTF-8")
        if any(ord(c) > 0xFFFF for c in text):
            r.fail(f"LCTYPE 0x{t:08x}: a character past U+FFFF natively, and the runtime's "
                   "answers hold none", u)
        for c in text:
            w = ord(c)
            if not (w < 0x80 or 0xA0 <= w < 0x100 or w in CP1252_HIGH):
                r.fail(f"LCTYPE 0x{t:08x}: U+{w:04X} has no byte of its own in code page "
                       f"{LOCALE_ACP}, and a reader takes no value GetLocaleInfoA would answer "
                       "through the best fit", u)
        if t == 0x1004 and text != str(acp):
            r.fail(f'LOCALE_IDEFAULTANSICODEPAGE is "{text}" and GetACP answered {acp}, and '
                   "the runtime's C runtime would install a code page its conversions "
                   "refuse", u)
        info.append((0, v[2].data))
    return Locale(lcid, system, acp, oemcp, tuple(info))


def _environment_section(r: _Reader, v: list[_Value]) -> Environment:
    """The environment section, its `environment` record in v."""
    u = UNREPRODUCIBLE
    strings, crt = v[0].u, v[1].u
    if not strings:
        r.fail("field `strings`: 0, and a process's block holds at least one")
    if strings > ENV_MAX_STRINGS:
        r.fail(f"{strings} strings in the environment block, more than the {ENV_MAX_STRINGS} "
               "the runtime holds", u)
    if not crt:
        r.fail("field `crt`: 0, and the C runtime keeps every string of a block it is handed")
    if crt > strings:
        r.fail(f"field `crt`: {crt} strings the C runtime kept, more than the block's {strings}")
    out, units = [], 0
    for i in range(strings):
        v = r.one("env")
        if v[0].u != i:
            r.fail(f"index {v[0].u} where {i} was next")
        if not v[1].data:
            r.fail(f"string {i}: empty, and an empty string ends the block")
        try:
            text = v[1].data.decode("utf-8")
        except UnicodeDecodeError:
            r.fail(f"string {i}: not valid UTF-8")
        if any(ord(c) > 0xFFFF for c in text):
            r.fail(f"string {i}: a character past U+FFFF natively, and the runtime's block "
                   "holds none", u)
        if units + len(text) + 2 > ENV_MAX_UNITS:
            r.fail(f"string {i}: the block passes the {ENV_MAX_UNITS} units the runtime holds", u)
        units += len(text) + 1
        out.append(v[1].data)
    return Environment(tuple(out), crt)


def _std_section(r: _Reader, v: list[_Value]) -> tuple[tuple[int, int], ...]:
    """The standard-handle section, its first `std_handle` record in v."""
    u = UNREPRODUCIBLE
    out = []
    for i in range(STD_HANDLES):
        if i:
            v = r.one("std_handle")
        if v[0].u != i:
            r.fail(f"standard handle {v[0].u} where {i} was next")
        h, t = v[1].u, v[2].u
        if h in (0, 0xFFFFFFFF):
            if t:
                r.fail(f"{STD_NAMES[i]}: type {t} for no handle, which GetFileType is not asked "
                       "for")
        elif t == 1:
            r.fail(f"{STD_NAMES[i]} was a disk file (FILE_TYPE_DISK) natively, and the runtime's "
                   "standard handles hold no file", u)
        elif t not in (0, 2, 3):
            r.fail(f"{STD_NAMES[i]}: type {t} is not FILE_TYPE_UNKNOWN, FILE_TYPE_CHAR or "
                   "FILE_TYPE_PIPE")
        out.append((h, t))
    return tuple(out)


def parse(data: bytes, source: str = "<profile>") -> Profile:
    """The profile, or ProfileError at the first problem in line order, with
    the words runtime/sa2_devprofile.c traps with."""
    r = _Reader(data, source)
    u = UNREPRODUCIBLE
    r.next_line()
    if r.s != HEADER:
        r.fail(f"the first line is not `{HEADER}`")

    v = r.one("system")
    cpus = v[0].u
    if not cpus:
        r.fail("field `processors`: 0, and a process runs on at least one processor")
    if cpus > MAX_PROCESSORS:
        r.fail(f"GetSystemInfo reported {cpus} processors natively, and the runtime's affinity "
               f"masks, a 32-bit process's, hold {MAX_PROCESSORS}", u)
    every = (1 << cpus) - 1
    for key, mask in (("process_mask", v[1].u), ("system_mask", v[2].u)):
        if not mask:
            r.fail(f"field `{key}`: 0x{mask:08x} names no processor")
        if mask & ~every:
            r.fail(f"field `{key}`: 0x{mask:08x} names a processor past the {cpus} "
                   "GetSystemInfo reported")
    if v[1].u & ~v[2].u:
        r.fail(f"field `process_mask`: 0x{v[1].u:08x} names a processor the system mask "
               f"0x{v[2].u:08x} does not, which Windows never answers")
    processors, process_mask, system_mask = cpus, v[1].u, v[2].u

    hr = r.one("dinput8_create")[0].u
    if hr:
        r.fail(f"DirectInput8Create answered 0x{hr:08x} natively, and the runtime's answers "
               "S_OK", u)
    syskeyboard = r.one("dinput8_syskeyboard")[0].u
    sysmouse = r.one("dinput8_sysmouse")[0].u
    gamectrl: list[GameCtrl] = []
    while True:
        which, v = r.record(("dinput8_gamectrl", "dinput8_enum"))
        if which == 1:
            if v[1].u != len(gamectrl):
                r.fail(f"count {v[1].u} where {len(gamectrl)} records came before it")
            if v[0].u:
                r.fail(f"EnumDevices answered 0x{v[0].u:08x} natively, and the runtime's answers "
                       "DI_OK", u)
            break
        if v[0].u != len(gamectrl):
            r.fail(f"index {v[0].u} where {len(gamectrl)} was next")
        if len(gamectrl) == MAX_GAMECTRL:
            r.fail(f"more than {MAX_GAMECTRL} `dinput8_gamectrl` records, the most the runtime "
                   "holds", u)
        if v[5].u > 0xFFFF:
            r.fail(f"field `usage_page`: 0x{v[5].u:08x} does not fit in 16 bits")
        if v[6].u > 0xFFFF:
            r.fail(f"field `usage`: 0x{v[6].u:08x} does not fit in 16 bits")
        if v[1].guid in (GUID_SYSKEYBOARD, GUID_SYSMOUSE):
            which = "GUID_SysKeyboard" if v[1].guid == GUID_SYSKEYBOARD else "GUID_SysMouse"
            r.fail(f"controller {len(gamectrl)} has {which} as its instance GUID, and the "
                   "runtime's CreateDevice tells devices apart by that GUID", u)
        for e, other in enumerate(gamectrl):
            if other.instance == v[1].guid:
                r.fail(f"controller {len(gamectrl)} has controller {e}'s instance GUID, and the "
                       "runtime's CreateDevice tells devices apart by that GUID", u)
        gamectrl.append(GameCtrl(v[1].guid, v[2].guid, v[3].guid, v[4].u, v[5].u, v[6].u,
                                 v[7].data, v[8].data, v[9].u))

    xinput = []
    for slot in range(XINPUT_SLOTS):
        v = r.one("xinput_caps")
        if v[0].u != slot:
            r.fail(f"slot {v[0].u} where {slot} was next")
        if v[1].u != NOT_CONNECTED:
            r.fail(f"XInput slot {slot} answered {v[1].u} natively, and the runtime answers only "
                   "1167 (not connected): a pad's input is part of no recording", u)
        xinput.append(v[1].u)

    # Information only, never refused: the apartment the main thread was in by
    # Init, not the game's answer (sa2_devprofile.h).
    coinit = r.one("wmi_coinit")[0].u
    hr = r.one("wmi_locator")[0].u
    if hr:
        r.fail(f"CoCreateInstance(CLSID_WbemLocator) answered 0x{hr:08x} natively, and the "
               "runtime's answers S_OK", u)
    hr = r.one("wmi_connect")[0].u
    if hr:
        r.fail(f"ConnectServer answered 0x{hr:08x} natively, and the runtime's answers S_OK", u)
    blanket = r.one("wmi_blanket")[0].u
    hr = r.one("wmi_enum")[0].u
    if hr:
        r.fail(f"CreateInstanceEnum answered 0x{hr:08x} natively, and the runtime's answers "
               "S_OK", u)
    pnp: list[bytes] = []
    tail = False
    while True:
        v = r.one("wmi_next")
        nhr, got = v[0].u, v[1].u
        if got > WMI_BATCH:
            r.fail(f"Next returned {got} objects of the {WMI_BATCH} asked for")
        if tail and got:
            r.fail(f"Next returned {got} objects after a short batch, and the runtime's returns "
                   "none", u)
        want = 0 if got == WMI_BATCH else 1
        if nhr != want:
            r.fail(f"Next answered 0x{nhr:08x} with {got} objects, and the runtime's answers "
                   f"0x{want:08x}", u)
        for _ in range(got):
            v = r.one("wmi_pnp")
            if v[0].u != len(pnp):
                r.fail(f"index {v[0].u} where {len(pnp)} was next")
            if len(pnp) == MAX_WMI_PNP:
                r.fail(f"more than {MAX_WMI_PNP} `wmi_pnp` records, the most the runtime holds",
                       u)
            if v[2].u > 0xFFFF:
                r.fail(f"field `vt`: 0x{v[2].u:08x} does not fit in 16 bits")
            if v[1].u:
                r.fail(f"Get(DeviceID) on entity {len(pnp)} answered 0x{v[1].u:08x} natively, and "
                       "the runtime's answers S_OK", u)
            if v[2].u != 8:
                r.fail(f"entity {len(pnp)}'s DeviceID has vt 0x{v[2].u:04x} natively, and the "
                       "runtime's is a BSTR (8)", u)
            if v[3].null:
                r.fail(f"entity {len(pnp)}'s DeviceID is a null BSTR natively, and the runtime's "
                       "is a string", u)
            try:
                v[3].data.decode("utf-8")        # strict: Unicode's Table 3-7, as the C reader
            except UnicodeDecodeError:
                r.fail(f"entity {len(pnp)}'s DeviceID is not valid UTF-8")
            pnp.append(v[3].data)
        if not got:
            break
        if got < WMI_BATCH:
            tail = True
    v = r.one("wmi_entities")
    if v[0].u != len(pnp):
        r.fail(f"count {v[0].u} where {len(pnp)} records came before it")

    adapters = r.one("d3d9_adapters")[0].u
    if adapters != 1:
        r.fail(f"Direct3D reported {adapters} adapters natively, and the runtime's IDirect3D9 "
               "reports 1", u)
    chosen = ZERO_GUID
    ddraw: list[DDrawDevice] = []
    while True:
        which, v = r.record(("ddraw_device", "ddraw_enum"))
        if which == 1:
            if v[1].u != len(ddraw):
                r.fail(f"count {v[1].u} where {len(ddraw)} records came before it")
            if v[0].u:
                r.fail(f"DirectDrawEnumerateExA answered 0x{v[0].u:08x} natively, and the "
                       "runtime's answers DD_OK", u)
            break
        if v[0].u != len(ddraw):
            r.fail(f"index {v[0].u} where {len(ddraw)} was next")
        if len(ddraw) == MAX_DDRAW:
            r.fail(f"more than {MAX_DDRAW} `ddraw_device` records, the most the runtime holds", u)
        if v[2].mon == 1 and v[2].u >= adapters:
            r.fail(f"monitor adapter{v[2].u}, and Direct3D reported {adapters} adapters")
        if v[2].mon == 2:
            r.fail(f"ddraw device {len(ddraw)}'s monitor is no Direct3D adapter's natively, and "
                   "the runtime's monitors are its adapters'", u)
        if v[2].mon == 1 and v[2].u == 0:
            if v[1].null:
                r.fail(f"ddraw device {len(ddraw)} is on adapter 0's monitor with no GUID, which "
                       "the game's callback 0x00865c20 would read through", u)
            chosen = v[1].guid
        ddraw.append(DDrawDevice(None if v[1].null else v[1].guid,
                                 v[2].u if v[2].mon == 1 else -1, v[3].data, v[4].data))
    v = r.one("ddraw_create")
    if v[0].guid != chosen:
        r.fail("DirectDrawCreate's GUID is not the one the game's callback 0x00865c20 keeps from "
               "the devices above")
    if v[1].u:
        r.fail(f"DirectDrawCreate answered 0x{v[1].u:08x} natively, and the runtime's answers "
               "DD_OK", u)
    hr = r.one("ddraw_query")[0].u
    if hr:
        r.fail(f"QueryInterface(IID_IDirectDraw7) answered 0x{hr:08x} natively, and the runtime's "
               "answers S_OK", u)
    v = r.one("ddraw_vidmem")
    vidmem_hr, vidmem_total = v[0].u, v[1].u

    v = r.one("xaudio2_master")
    if v[0].u > 1:
        r.fail(f"field `voice`: {v[0].u} is neither 0 nor 1")
    if not v[0].u:
        r.fail("the game had no XAudio2 mastering voice natively (0x01af0edc was null), and the "
               "runtime's engine makes one", u)
    xaudio2 = (v[1].u, v[2].u, v[3].u)

    locale, environment, std_handles = None, None, None
    tail = ("locale_user", "environment", "std_handle", "end")
    at = 0
    while True:
        which, v = r.record(tail[at:])
        which += at
        if which == 3:
            break
        if which == 0:
            locale = _locale_section(r, v)
        elif which == 1:
            environment = _environment_section(r, v)
        else:
            std_handles = _std_section(r, v)
        at = which + 1
    if r.pos != len(r.data):
        r.line += 1
        r.fail("text after `end`")
    return Profile(processors=processors, process_mask=process_mask, system_mask=system_mask,
                   syskeyboard=syskeyboard, sysmouse=sysmouse, gamectrl=tuple(gamectrl),
                   xinput=tuple(xinput), coinit=coinit, blanket=blanket, pnp=tuple(pnp),
                   ddraw=tuple(ddraw), vidmem_hr=vidmem_hr, vidmem_total=vidmem_total,
                   xaudio2=xaudio2, locale=locale, environment=environment,
                   std_handles=std_handles)


# --------------------------------------------------------------- the writer

def quote(b: bytes) -> str:
    """A string's one spelling (sa2_devprofile.h)."""
    out = ['"']
    for c in b:
        if not c:
            raise ValueError("a NUL byte has no spelling in a device profile")
        if c == 0x22:
            out.append('\\"')
        elif c == 0x5C:
            out.append("\\\\")
        elif 0x20 <= c <= 0x7E:
            out.append(chr(c))
        else:
            out.append(f"\\x{c:02x}")
    out.append('"')
    return "".join(out)


def guid_text(b: bytes) -> str:
    return "{" + str(uuid.UUID(bytes_le=b)).upper() + "}"


def chosen_guid(ddraw: tuple[DDrawDevice, ...]) -> bytes:
    """What the game's callback 0x00865C20 leaves in its context: the GUID of
    the last device on adapter 0's monitor, or the zeroes it started with."""
    out = ZERO_GUID
    for d in ddraw:
        if d.monitor == 0 and d.guid is not None:
            out = d.guid
    return out


def dump(p: Profile) -> bytes:
    """The file the probe writes for a host whose answers are `p`: parse(dump(p))
    is p, and the bytes are the only spelling of it."""
    h = "0x{:08x}".format
    out = [HEADER, f"system processors={p.processors} process_mask={h(p.process_mask)} "
                   f"system_mask={h(p.system_mask)}",
           f"dinput8_create hr={h(0)}", f"dinput8_syskeyboard hr={h(p.syskeyboard)}",
           f"dinput8_sysmouse hr={h(p.sysmouse)}"]
    for i, g in enumerate(p.gamectrl):
        out.append(f"dinput8_gamectrl index={i} instance={guid_text(g.instance)} "
                   f"product={guid_text(g.product)} ffdriver={guid_text(g.ffdriver)} "
                   f"devtype={h(g.devtype)} usage_page={h(g.usage_page)} usage={h(g.usage)} "
                   f"instance_name={quote(g.instance_name)} product_name={quote(g.product_name)} "
                   f"create={h(g.create)}")
    out.append(f"dinput8_enum hr={h(0)} count={len(p.gamectrl)}")
    out += [f"xinput_caps slot={s} code={h(c)}" for s, c in enumerate(p.xinput)]
    out += [f"wmi_coinit hr={h(p.coinit)}", f"wmi_locator hr={h(0)}", f"wmi_connect hr={h(0)}",
            f"wmi_blanket hr={h(p.blanket)}", f"wmi_enum hr={h(0)}"]
    rest, index = list(p.pnp), 0
    while True:
        batch, rest = rest[:WMI_BATCH], rest[WMI_BATCH:]
        out.append(f"wmi_next hr={h(0 if len(batch) == WMI_BATCH else 1)} returned={len(batch)}")
        for d in batch:
            out.append(f"wmi_pnp index={index} get={h(0)} vt={h(8)} deviceid={quote(d)}")
            index += 1
        if not batch:
            break
    out += [f"wmi_entities count={len(p.pnp)}", "d3d9_adapters count=1"]
    for i, d in enumerate(p.ddraw):
        mon = "null" if d.monitor < 0 else f"adapter{d.monitor}"
        g = guid_text(d.guid) if d.guid is not None else "null"
        out.append(f"ddraw_device index={i} guid={g} monitor={mon} "
                   f"description={quote(d.description)} name={quote(d.name)}")
    out += [f"ddraw_enum hr={h(0)} count={len(p.ddraw)}",
            f"ddraw_create guid={guid_text(chosen_guid(p.ddraw))} hr={h(0)}",
            f"ddraw_query hr={h(0)}", f"ddraw_vidmem hr={h(p.vidmem_hr)} total={h(p.vidmem_total)}",
            f"xaudio2_master voice=1 flags={h(p.xaudio2[0])} channels={p.xaudio2[1]} "
            f"rate={p.xaudio2[2]}"]
    if p.locale is not None:
        loc = p.locale
        out.append(f"locale_user lcid={h(loc.lcid)} system={h(loc.system)} acp={loc.acp} "
                   f"oemcp={loc.oemcp} valid=1")
        for t, (error, value) in zip(LOCALE_LCTYPES, loc.info):
            out.append(f"locale_info lctype={h(t)} error={h(error)} value="
                       + ("null" if value is None else quote(value)))
    if p.environment is not None:
        env = p.environment
        out.append(f"environment strings={len(env.strings)} crt={env.crt}")
        out += [f"env index={i} value={quote(s)}" for i, s in enumerate(env.strings)]
    if p.std_handles is not None:
        out += [f"std_handle which={i} handle={h(hd)} type={t}"
                for i, (hd, t) in enumerate(p.std_handles)]
    out.append("end")
    return ("\n".join(out) + "\n").encode("ascii")


# ---------------------------------------------------------------- the file

def sha256(path: Path) -> str:
    """The whole file's sha256, read a block at a time, whatever its size."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 16), b""):
            digest.update(block)
    return digest.hexdigest()


def read(path: Path, source: str | None = None) -> bytes:
    """The file's bytes, read as the runtime reads them: at most one byte past
    MAX_BYTES, so an oversized file is refused without being held. Any I/O
    failure has one message, since the C reader's fopen takes a directory that
    Python's open refuses, and the two must say the same words."""
    src = str(path) if source is None else source
    try:
        with path.open("rb") as stream:
            data = stream.read(MAX_BYTES + 1)
    except OSError:
        raise ProfileError(src, 0, "the device profile cannot be read") from None
    if len(data) > MAX_BYTES:
        raise ProfileError(src, 0, f"the device profile is larger than {MAX_BYTES} bytes")
    return data


def load(path: Path, source: str | None = None) -> tuple[Profile, str]:
    """The profile at `path` and its sha256, read as the runtime reads the file
    SA2_DEVICE_PROFILE names; `source` is how messages name it."""
    src = str(path) if source is None else source
    data = read(path, src)
    return parse(data, src), hashlib.sha256(data).hexdigest()


def counts(p: Profile) -> dict:
    """What a capture's receipt records of a profile, beside its sha256."""
    return {"gamectrl": len(p.gamectrl), "xinput_connected": sum(1 for c in p.xinput if c == 0),
            "wmi_pnp": len(p.pnp), "ddraw": len(p.ddraw)}


def summary(path: Path) -> dict:
    """session.device_profile for a capture whose directory holds `path`."""
    p, sha = load(path)
    return {"file": path.name, "sha256": sha, "counts": counts(p)}


def rel(path: Path, root: Path = ROOT) -> str:
    """`path` relative to `root`, lexically. Never resolved: the clean checkout
    tools/s4/reach_reproduce.py builds symlinks private/, and a resolved path
    would leave the checkout and stop matching the registry's."""
    try:
        return str(Path(os.path.abspath(path)).relative_to(os.path.abspath(root)))
    except ValueError:
        return str(path)


# ------------------------------------------------------------ the captures

def wine_path(unix: str, where: str = "<receipt>") -> str:
    """An absolute Unix path as the capture's Wine showed it to the game
    (WINE_ROOT_DRIVE): Z: and the lexically normalised path with backslashes.
    A path Wine would name otherwise is refused: a relative one, one holding a
    backslash (a file-name byte on Unix, a separator to the game), and one
    inside the prefix's own drive_c, which Wine names C:."""
    if not isinstance(unix, str) or not unix.startswith("/"):
        raise Refusal(f"{where}: {unix!r} is not an absolute Unix path, so it has no Wine name")
    if "\\" in unix:
        raise Refusal(f"{where}: {unix!r} holds a backslash, which Wine would show the game as a "
                      f"separator")
    norm = os.path.normpath(unix)
    if "drive_c" in norm.split("/"):
        raise Refusal(f"{where}: {unix!r} is inside a prefix's drive_c, which Wine names C:, "
                      f"not {WINE_ROOT_DRIVE}")
    return WINE_ROOT_DRIVE + ("\\" if norm == "/" else norm.replace("/", "\\"))


def guest_paths(receipt: dict, where: str = "<receipt>") -> dict:
    """The current directory and executable path a capture's game saw: the
    directory the runner launched it in (the receipt's `cwd`) and the program
    its launch line names after LAUNCHER, resolved against that directory, both
    as Wine names them (`wine_path`). {"cwd", "exe_path"}."""
    cwd, command = receipt.get("cwd"), receipt.get("command")
    if not cwd:
        raise Refusal(f"{where}: the receipt records no cwd, so nothing says which directory the "
                      f"game ran in")
    # Wine names the current directory from $PWD when it names the same
    # directory, else from getcwd() with symlinks resolved; the runner's game
    # environment carries no PWD, so a directory reached through a symlink has a
    # name the receipt does not record (the working-directory review, nit 5, and
    # its fold review, nit 3). The executable is named as
    # launched (the game's own module name in memory is the mirror's path, whose
    # file is a symlink), so only the directory is held to this. Checked only
    # where the directory exists on this machine.
    norm = os.path.normpath(str(cwd))
    if os.path.isdir(norm) and os.path.realpath(norm) != norm:
        raise Refusal(f"{where}: the game's directory {cwd!r} runs through a symlink (it "
                      f"resolves to {os.path.realpath(norm)!r}), so the name Wine gave the game "
                      f"is not known from the receipt")
    if not isinstance(command, str) or not command:
        raise Refusal(f"{where}: the receipt records no launch command, so nothing says which "
                      f"file the game ran as")
    try:
        words = shlex.split(command)
    except ValueError as e:
        raise Refusal(f"{where}: the receipt's command does not parse ({e})") from None
    if words.count(LAUNCHER) != 1 or words.index(LAUNCHER) + 1 >= len(words):
        raise Refusal(f"{where}: the receipt's command names no program after {LAUNCHER!r} "
                      f"once, so nothing says which file the game ran as")
    program = words[words.index(LAUNCHER) + 1]
    exe = program if program.startswith("/") else os.path.join(cwd, program)
    return {"cwd": wine_path(cwd, where), "exe_path": wine_path(exe, where)}


def capture_profile(capture_dir: Path, root: Path = ROOT) -> dict:
    """A native capture's profile, as a lifted run that stands for it may use it:
    its receipt records the file beside it, the file hashes to the receipt's
    sha256 and to the counts the receipt kept, the runtime can reproduce it, and
    the profile probe that wrote it is the one in the tree. Returns
    {"path", "sha256", "counts", "guest"}, the path relative to `root`, and
    `guest` the directory and executable the capture's game saw
    (`guest_paths`)."""
    where = rel(capture_dir, root)
    receipt = capture_dir / "receipt.json"
    if not receipt.is_file():
        raise Refusal(f"{where} has no receipt.json, so nothing records its device profile")
    doc = json.loads(receipt.read_text())
    rec = (doc.get("session") or {}).get("device_profile")
    if not rec:
        raise Refusal(f"{where}'s receipt records no device profile: re-capture it with the "
                      f"profile probe (tools/oracle/run_oracle_probe.py --device-profile)")
    if rec.get("file") != FILE:
        raise Refusal(f"{where}'s receipt names the device profile {rec.get('file')!r}, not {FILE}")
    path = capture_dir / FILE
    if not path.is_file():
        raise Refusal(f"{rel(path, root)} is missing, and the receipt records it")
    sha = sha256(path)
    if sha != rec.get("sha256"):
        raise Refusal(f"{rel(path, root)} hashes to {sha[:12]} and its receipt records "
                      f"{str(rec.get('sha256'))[:12]}")
    profile, _ = load(path, rel(path, root))
    if rec.get("counts") != counts(profile):
        raise Refusal(f"{where}'s receipt counts {rec.get('counts')} and its profile holds "
                      f"{counts(profile)}")
    inputs = doc.get("inputs") or {}
    probes = inputs.get("built_probes") or []
    built = [b for b in probes if Path(b.get("source", "")).name == Path(PROBE).name]
    now = sha256(root / PROBE)
    if len(built) != 1 or built[0].get("source_sha256") != now:
        raise Refusal(f"{where} was taken with a {Path(PROBE).name} that is not the one in the "
                      f"tree ({[str(b.get('source_sha256'))[:12] for b in built]} against "
                      f"{now[:12]}); re-capture it")
    # Last, in the build list and in the loader's order: the position the
    # control run measured it in, after every other probe's Init.
    enabled = inputs.get("enabled_mods") or []
    if probes[-1] is not built[0] or not enabled or enabled[-1] != built[0].get("mod_id"):
        raise Refusal(f"{where} loaded {Path(PROBE).name} somewhere other than last, a load "
                      "order the non-perturbation control never measured; re-capture it with "
                      "--device-profile")
    return {"path": rel(path, root), "sha256": sha, "counts": counts(profile),
            "guest": guest_paths(doc, where)}


def replay_profile(entry: dict, root: Path = ROOT) -> dict:
    """A registered replay's profile (Documentation/replays.json, native.device_profile
    {path, sha256}): its native capture's, checked by capture_profile, and every
    registered repeat's too, since a repeat taken on another device set shows
    nothing about this one."""
    name, native = entry.get("name", "?"), entry["native"]
    reg = native.get("device_profile")
    if not reg:
        raise Refusal(f"{name}: the registry records no device profile for "
                      f"{native['capture']}; re-capture it with the profile probe "
                      f"(tools/oracle/run_oracle_probe.py --device-profile) and register its "
                      f"native.device_profile")
    if not native.get("receipt"):
        raise Refusal(f"{name}: {native['capture']} has no oracle receipt")
    got = capture_profile((root / native["receipt"]).parent, root)
    if reg != {"path": got["path"], "sha256": got["sha256"]}:
        raise Refusal(f"{name}: the registry's device profile {reg} is not the one its capture's "
                      f"receipt records ({got['path']}, {got['sha256'][:12]})")
    for rep in native.get("repeats", []):
        other = capture_profile((root / rep["receipt"]).parent, root)
        if other["sha256"] != got["sha256"]:
            raise Refusal(f"{name}: repeat {rep['capture']} ran on device profile "
                          f"{other['sha256'][:12]} and the capture on {got['sha256'][:12]}; a "
                          f"repeat of another device set shows nothing about this one")
        if other["guest"] != got["guest"]:
            raise Refusal(f"{name}: repeat {rep['capture']} ran in {other['guest']['cwd']!r} as "
                          f"{other['guest']['exe_path']!r} and the capture in "
                          f"{got['guest']['cwd']!r} as {got['guest']['exe_path']!r}; a repeat "
                          f"in another directory shows nothing about this one")
    return got


# -------------------------------------------------------------- the runs

def run_env(profile: dict | str) -> dict[str, str]:
    """The environment a lifted run needs to read `profile`: nothing for the
    default, whose run keeps the runtime's placeholder directory; for a
    capture's, its path and the directory and executable its game saw
    (capture_profile's `guest`). A capture's profile without them is refused,
    never run in the placeholder directory: a run that stands for a capture
    answers the capture's directory."""
    if profile == DEFAULT:
        return {}
    path = profile["path"]
    if "|" in path or "=" in path:
        # loop_run.py's --run spec is VAR=VALUE items joined by `|`.
        raise Refusal(f"{path}: a device profile path with `|` or `=` cannot travel in a run spec")
    guest = profile.get("guest")
    if not isinstance(guest, dict) or set(guest) != set(GUEST_ENV):
        raise Refusal(f"{path}: the profile carries no guest directory and executable of its "
                      f"capture (capture_profile's `guest`), and a run that stands for a capture "
                      f"answers the capture's")
    for value in guest.values():
        if not isinstance(value, str) or not value or "|" in value or "=" in value:
            raise Refusal(f"{path}: the capture's guest path {value!r} cannot travel in a run spec")
    return {ENV: path, **{GUEST_ENV[k]: guest[k] for k in sorted(GUEST_ENV)}}


def guest_record(profile: dict | str) -> dict:
    """The directory and executable a run on `profile` must show in its own
    record: the capture's, or the runtime's placeholders on the default."""
    return dict(DEFAULT_GUEST) if profile == DEFAULT else dict(profile["guest"])


def run_guest(run: dict) -> dict:
    """The directory and executable a lifted run says it answered:
    harness/crt_probe.c's `virtual` block, from the runtime's own
    sa2_import_cwd and sa2_import_exe_path. What a run record keeps."""
    block = run.get("virtual")
    if not isinstance(block, dict) or not all(isinstance(block.get(k), str) for k in GUEST_ENV):
        raise Refusal("the run record has no virtual cwd and exe_path, so nothing says which "
                      "directory the run answered")
    return {k: block[k] for k in sorted(GUEST_ENV)}


def clean_environ() -> dict[str, str]:
    """This process's environment without SA2_DEVICE_PROFILE, SA2_XINPUT_PAD,
    SA2_GUEST_CPUS, SA2_GUEST_CWD or SA2_GUEST_EXE_PATH. loop_run.py hands the
    guest os.environ under the run's own settings, so any of them left in a
    shell would reach every run: a profile meant for another, a host-device
    override the capture never had (the 2026-09-23 review; the runtime also
    traps on an override beside a capture's profile), or a directory that is
    not the capture's."""
    return {k: v for k, v in os.environ.items() if k not in OWNED}


def recorded(profile: dict | str) -> dict | str:
    """What a run record keeps of the profile it used."""
    return DEFAULT if profile == DEFAULT else {"path": profile["path"],
                                               "sha256": profile["sha256"]}


def describe(profile) -> str:
    if profile == DEFAULT:
        return DEFAULT
    if isinstance(profile, dict):
        return f"{profile.get('path')} ({str(profile.get('sha256'))[:12]})"
    return "none recorded"


def same(a, b) -> bool:
    """Whether two recorded profiles are one device set: both the default, or
    the same file content."""
    if a == DEFAULT or b == DEFAULT:
        return a == b
    return isinstance(a, dict) and isinstance(b, dict) and bool(a.get("sha256")) \
        and a.get("sha256") == b.get("sha256")


def run_record(run: dict) -> dict | str:
    """The profile a lifted run says it read: harness/crt_probe.c's
    `device_profile` block, from the runtime's own sa2_devprofile_source and
    sa2_devprofile_sha256."""
    block = run.get("device_profile")
    if not isinstance(block, dict):
        raise Refusal("the run record has no device_profile block, so nothing says which device "
                      "profile the run read")
    if block.get("default"):
        return DEFAULT
    return {"path": block.get("source"), "sha256": block.get("sha256")}


def check_run(run: dict, profile: dict | str) -> dict | str:
    """Refuses a run that read another profile than the one it was handed, or
    answered another directory or executable than the profile's capture's
    (`guest_record`; the placeholders on the default)."""
    got, want = run_record(run), recorded(profile)
    if got != want:
        raise Refusal(f"the run read device profile {describe(got)} and was handed "
                      f"{describe(want)}")
    answered, handed = run_guest(run), guest_record(profile)
    if answered != handed:
        raise Refusal(f"the run answered the guest directory {answered['cwd']!r} and executable "
                      f"{answered['exe_path']!r}, and its device profile's capture ran in "
                      f"{handed['cwd']!r} as {handed['exe_path']!r}")
    return got


# ------------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", nargs="?", type=Path, help="a sa2-device-profile.txt")
    ap.add_argument("--summary", action="store_true",
                    help="print what a capture's receipt records: file, sha256, counts")
    ap.add_argument("--capture", type=Path, default=None,
                    help="check a capture directory's receipt against its profile")
    ap.add_argument("--default", action="store_true",
                    help="print the built-in default profile as a file would hold it")
    a = ap.parse_args(argv)
    try:
        if a.default:
            sys.stdout.write(dump(default()).decode("ascii"))
            return 0
        if a.capture is not None:
            print(json.dumps(capture_profile(a.capture), sort_keys=True))
            return 0
        if a.path is None:
            ap.error("a profile path, --capture or --default")
        if a.summary:
            print(json.dumps(summary(a.path), sort_keys=True))
            return 0
        p, sha = load(a.path)
    except Refusal as e:
        print(f"DEVICE_PROFILE_REFUSED {e}")
        return 1
    c = counts(p)
    print(f"DEVICE_PROFILE_OK {a.path} sha256={sha} "
          + " ".join(f"{k}={v}" for k, v in c.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
