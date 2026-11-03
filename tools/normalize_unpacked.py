#!/usr/bin/env python3
"""Turn a known variant of the unpacked executable into the pinned one.

    python3 tools/normalize_unpacked.py private/exe/sonic2app-unpacked.exe

Steamless 3.1.0.5 with its default options writes slightly different files depending on the .NET
runtime it runs on. Under Proton (wine-mono 11.3.0) it writes the file `tools/pins.json` pins for
`sonic2app-unpacked.exe`. On Microsoft .NET the same options write a file whose `.dataa` and `.rsrca`
sections are named `.data` and `.rsrc` instead, the names the shipped executable uses; every other
byte is the same (issue #41). The build checks the whole file's hash in many places, so this tool
rewrites those names and leaves everything else to the existing checks.

It changes a file only when the file's hash is a variant listed in VARIANTS and the renamed file then
hashes to the pin. The file is replaced in one step, so it is either the pinned file or unchanged. A
file that is already the pinned one is left as it is; any other file is refused and left as it is.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# A variant's SHA-256 and the section renames (name in the variant, name in the pinned file) that
# make it the pinned file.
VARIANTS = {
    # Steamless 3.1.0.5 on Microsoft .NET (issue #41).
    "ebb53fa1c47fec4eab6e22fe9e59e876215fb3b7a5391b8eeeee86d01b00410f": ((b".data", b".dataa"), (b".rsrc", b".rsrca")),
}


class Refused(Exception):
    pass


def pinned_sha256() -> str:
    pins = json.loads((ROOT / "tools/pins.json").read_text())
    return pins["executables"]["sonic2app-unpacked.exe"]["sha256"]


def section_names(data: bytes) -> list[tuple[int, bytes]]:
    """Each section header's offset and its name, NUL padding removed."""
    if data[:2] != b"MZ":
        raise Refused("not a Windows executable (no MZ header)")
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != b"PE\0\0":
        raise Refused("not a Windows executable (no PE header)")
    count, optional = struct.unpack_from("<H", data, pe + 6)[0], struct.unpack_from("<H", data, pe + 20)[0]
    table = pe + 24 + optional
    return [(table + 40 * i, bytes(data[table + 40 * i:table + 40 * i + 8]).rstrip(b"\0")) for i in range(count)]


def normalize(data: bytes, pin: str) -> tuple[bytes, str]:
    """The pinned file made from `data`, and what was done; raises Refused when it cannot be."""
    digest = hashlib.sha256(data).hexdigest()
    if digest == pin:
        return data, "already the pinned file"
    renames = VARIANTS.get(digest)
    if renames is None:
        raise Refused(f"its SHA-256 {digest} is neither the pin {pin} nor a known variant; make it again "
                      "as docs/getting-started.md says")
    out = bytearray(data)
    for old, new in renames:
        hits = [off for off, name in section_names(out) if name == old]
        if len(hits) != 1:
            raise Refused(f"expected one section named {old.decode()}, found {len(hits)}")
        out[hits[0]:hits[0] + 8] = new.ljust(8, b"\0")
    if hashlib.sha256(out).hexdigest() != pin:
        raise Refused("renaming its sections did not give the pinned file")
    names = ", ".join(f"{old.decode()} to {new.decode()}" for old, new in renames)
    return bytes(out), f"renamed {names}; it is now the pinned file"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("exe", type=Path, help="the unpacked executable, normalized in place")
    a = ap.parse_args(argv)
    try:
        data = a.exe.read_bytes()
        out, what = normalize(data, pinned_sha256())
    except (OSError, Refused) as e:
        print(f"normalize_unpacked: {a.exe}: refused: {e}", file=sys.stderr)
        return 1
    if out != data:
        tmp = a.exe.with_name(a.exe.name + ".part")
        tmp.write_bytes(out)
        os.replace(tmp, a.exe)
    print(f"normalize_unpacked: {a.exe}: {what}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
