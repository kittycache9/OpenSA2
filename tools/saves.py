#!/usr/bin/env python3
"""Import, export, back up and restore the desktop game's saves.

The desktop twin of the Android settings screen's Saves card (host/android/java SaveTools.java),
with the same rules:

* The game reads resource/gd_PC/SAVEDATA, the write overlay's copy over the game folder's, and
  writes the overlay alone (runtime/sa2_files.c). Both folders come from the installer's config
  ($XDG_CONFIG_HOME/sa2recomp/sa2recomp.cfg: read_root and overlay; --config names another). An
  import writes the overlay's SAVEDATA and nothing else: the game folder is never written.
* A save is SONIC2B__S01..S99 (main game, 24,576 bytes) or SONIC2B__ALF (Chao World, 65,536
  bytes), by name without regard to case, at exactly that size. Anything else is refused. With the
  game executable at hand (the config's exe, or --exe), an ALF's checksum is also checked
  ("The Chao World checksum" below); a mismatch refuses the file unless --force.
* An import takes save files, or one ZIP: a Debug Export (sa2recomp --debug-export) with its
  session/end-saves/ and session/start-saves/ (the end by default, the start with --from start), a zipped SAVEDATA
  folder, or any ZIP whose save files have one copy each. It replaces only the saves it brings.
* Every import and restore first copies the overlay's saves to
  $XDG_DATA_HOME/sa2recomp/save-backups/<UTC time>/ (empty when there were none); the newest 10
  are kept. A restore makes the overlay's SAVEDATA exactly the backup's.

Usage:
  tools/saves.py show
  tools/saves.py import FILE... [--from end|start] [--force]
  tools/saves.py export OUT.zip
  tools/saves.py backups
  tools/saves.py restore BACKUP
  (--config FILE, --backups DIR and --exe FILE go before the command)

Do not run it while the game is running: the game would write its own saves over the import.

The Chao World checksum
-----------------------
SONIC2B__ALF holds a 0x40-byte file header (naming CHAO GARDEN DATA in the English game) and a
0x3000-byte banner, then the 0xCA6C-byte save. Four bytes of the save, scattered, store a CRC
over the whole save with those four bytes and one filler byte zeroed: the game's own table
(not the standard CRC-32 table), seed 0x6368616F, result XORed with 0x686F6765. The table is
read from the user's executable at run time (1,024 bytes at 0x8A6FF8 in the 32-bit image), so
no game data is kept here. An ALF whose header is not recognised, or an executable that has no
such table, is reported as not checked rather than refused.
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import os
import re
import shutil
import struct
import sys
import zipfile
from pathlib import Path

MAIN = re.compile(r"(?i)^SONIC2B__S\d\d$")
CHAO = re.compile(r"(?i)^SONIC2B__ALF$")
SIZES = {"main": 24576, "chao": 65536}
SAVEDATA = ("resource", "gd_PC", "SAVEDATA")
KEEP = 10


class Refused(Exception):
    pass


def kind(name: str) -> str | None:
    return "main" if MAIN.match(name) else "chao" if CHAO.match(name) else None


def not_a_save(name: str, size: int) -> str | None:
    k = kind(name)
    if k is None:
        return f"{name} is not a save's name (SONIC2B__S01..S99 or SONIC2B__ALF)"
    return None if size == SIZES[k] else f"{name} is {size} bytes, not {SIZES[k]}"


def xdg(var: str, default: str) -> Path:
    v = os.environ.get(var)
    return Path(v) if v else Path.home() / default


def read_config(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise Refused(f"no installer config at {path}: install the game first (sa2-install)")
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def folded(root: Path, parts: tuple[str, ...], make: bool) -> Path | None:
    """root/parts, each component matched without regard to case as the runtime does."""
    cur = root
    for part in parts:
        hit = next((p for p in cur.iterdir() if p.name.lower() == part.lower()), None) if cur.is_dir() else None
        if hit is None:
            if not make:
                return None
            hit = cur / part
        cur = hit
    if make:
        cur.mkdir(parents=True, exist_ok=True)
    return cur


# The Chao World save's layout and checksum (module docstring, "The Chao World checksum").
ALF_HEADER = 0x40
ALF_SAVE_OFFSET = ALF_HEADER + 0x3000
ALF_SAVE_SIZE = 0xCA6C
ALF_CRC_TABLE_VA = 0x8A6FF8
ALF_CRC_SEED = 0x6368616F
ALF_CRC_XOROUT = 0x686F6765
ALF_CRC_POS = (0xCA69, 0xCA64, 0xCA6B, 0xCA66)   # byte i of the checksum is at ALF_CRC_POS[i]
ALF_CRC_ZEROED = ALF_CRC_POS + (0xCA67,)
_tables: dict[Path, list[int] | str] = {}


def pe_read(data: bytes, va: int, n: int) -> bytes:
    """n bytes at virtual address va of a 32-bit PE image (bytes past a section's raw data read
    as zero); ValueError when the file is no such image or va is outside it."""
    if data[:2] != b"MZ":
        raise ValueError("not a PE file")
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != b"PE\0\0" or struct.unpack_from("<H", data, pe + 24)[0] != 0x10B:
        raise ValueError("not a 32-bit PE image")
    nsec, optsize = struct.unpack_from("<H", data, pe + 6)[0], struct.unpack_from("<H", data, pe + 20)[0]
    base = struct.unpack_from("<I", data, pe + 52)[0]
    off = pe + 24 + optsize
    for _ in range(nsec):
        vsize, rva, rsize, rptr = struct.unpack_from("<IIII", data, off + 8)
        start = base + rva
        if start <= va and va + n <= start + max(vsize, rsize):
            chunk = data[rptr:rptr + rsize][va - start:va - start + n]
            return chunk + b"\0" * (n - len(chunk))
        off += 40
    raise ValueError(f"{va:#x} is outside the image")


def crc_table(exe: Path) -> list[int] | str:
    """The game's checksum table from the executable, or why it cannot be had."""
    if exe not in _tables:
        try:
            table = list(struct.unpack("<256I", pe_read(exe.read_bytes(), ALF_CRC_TABLE_VA, 1024)))
            # A CRC table's entries are distinct and its first is zero; anything else is not it.
            _tables[exe] = table if table[0] == 0 and len(set(table)) == 256 else "no checksum table found"
        except (OSError, ValueError, struct.error) as e:
            _tables[exe] = str(e)
    return _tables[exe]


def alf_checksums(data: bytes, table: list[int]) -> tuple[int, int]:
    """(stored, computed) for an ALF file's bytes."""
    save = bytearray(data[ALF_SAVE_OFFSET:ALF_SAVE_OFFSET + ALF_SAVE_SIZE])
    stored = sum(save[p] << (8 * i) for i, p in enumerate(ALF_CRC_POS))
    for p in ALF_CRC_ZEROED:
        save[p] = 0
    c = ALF_CRC_SEED
    for b in save:
        c = (c >> 8) ^ table[(c & 0xFF) ^ b]
    return stored, c ^ ALF_CRC_XOROUT


def checksum_note(name: str, data: bytes, exe: Path | None) -> tuple[str | None, str | None]:
    """(problem, note) for a save: problem when an ALF's stored checksum is not the game's own
    over its bytes; note when the check could not be made."""
    if kind(name) != "chao":
        return None, None
    if exe is None or not exe.is_file():
        return None, "checksum not checked: no game executable (--exe)"
    if b"CHAO GARDEN DATA" not in data[:ALF_HEADER]:
        return None, "checksum not checked: the header is not the English game's"
    table = crc_table(exe)
    if isinstance(table, str):
        return None, f"checksum not checked: {exe.name}: {table}"
    stored, calc = alf_checksums(data, table)
    return (None if calc == stored else f"{name}: checksum {stored:#010x} stored, {calc:#010x} computed"), None


def read_pick(paths: list[Path], frm: str) -> tuple[dict[str, bytes], list[str]]:
    """The saves the paths hold, by name, and what was skipped."""
    saves: dict[str, bytes] = {}
    skipped: list[str] = []
    zips = [p for p in paths if zipfile.is_zipfile(p)]
    if zips and len(paths) > 1:
        raise Refused("give one ZIP on its own")
    if zips:
        with zipfile.ZipFile(zips[0]) as z:
            entries = [i for i in z.infolist() if not i.is_dir() and kind(i.filename.rsplit("/", 1)[-1])]
            export = [i for i in entries if i.filename.startswith(("session/start-saves/", "session/end-saves/"))]
            if export:
                want = f"session/{frm}-saves/"
                entries = [i for i in export if i.filename.startswith(want)]
                if not entries:
                    raise Refused(f"this Debug Export has no {want}")
            for i in entries:
                name = i.filename.rsplit("/", 1)[-1]
                # The size the entry declares first: zipfile reads an entry only up to it, so a
                # save-named entry that would decompress to gigabytes is refused unread.
                why = not_a_save(name, i.file_size)
                if why:
                    skipped.append(f"{i.filename}: {why}")
                    continue
                data = z.read(i)
                why = not_a_save(name, len(data))
                if why:
                    skipped.append(f"{i.filename}: {why}")
                elif name.upper() in saves:
                    raise Refused(f"the ZIP holds {name} more than once: zip one save folder")
                else:
                    saves[name.upper()] = data
        return saves, skipped
    for p in paths:
        why = not_a_save(p.name, p.stat().st_size)   # a file of the wrong size is not read
        if why:
            skipped.append(why)
            continue
        data = p.read_bytes()
        why = not_a_save(p.name, len(data))
        if why:
            skipped.append(why)
        else:
            saves[p.name.upper()] = data
    return saves, skipped


def backup(dirpath: Path, root: Path) -> Path:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S-%f")[:-3]
    root.mkdir(parents=True, exist_ok=True)
    b, n = root / stamp, 1
    while True:   # two backups in one millisecond get distinct folders, never one shared
        try:
            b.mkdir()
            break
        except FileExistsError:
            b, n = root / f"{stamp}-{n}", n + 1
    for f in dirpath.iterdir():
        if f.is_file() and kind(f.name):
            shutil.copy2(f, b / f.name)
    for old in sorted((d for d in root.iterdir() if d.is_dir()), reverse=True)[KEEP:]:
        shutil.rmtree(old)
    return b


def write_atomically(target: Path, data: bytes) -> None:
    part = target.with_name(target.name + ".part")
    with open(part, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    part.replace(target)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--config", type=Path, default=xdg("XDG_CONFIG_HOME", ".config") / "sa2recomp" / "sa2recomp.cfg")
    ap.add_argument("--backups", type=Path, default=xdg("XDG_DATA_HOME", ".local/share") / "sa2recomp" / "save-backups")
    ap.add_argument("--exe", type=Path, default=None, help="the game executable for ALF checksums (default: the config's exe)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show", help="the saves the game sees, and where each comes from")
    im = sub.add_parser("import")
    im.add_argument("files", nargs="+", type=Path)
    im.add_argument("--from", dest="frm", choices=["end", "start"], default="end")
    im.add_argument("--force", action="store_true", help="import an ALF whose checksum does not match")
    ex = sub.add_parser("export")
    ex.add_argument("out", type=Path)
    sub.add_parser("backups")
    rs = sub.add_parser("restore")
    rs.add_argument("backup")
    a = ap.parse_args(argv)
    try:
        cfg = read_config(a.config)
        overlay = folded(Path(cfg["overlay"]), SAVEDATA, make=True)
        install = folded(Path(cfg["read_root"]), SAVEDATA, make=False) if cfg.get("read_root") else None
        exe = a.exe or (Path(cfg["exe"]) if cfg.get("exe") else None)
        if a.cmd == "show":
            seen: dict[str, tuple[Path, str]] = {}
            for d, where in ((install, "game folder"), (overlay, "overlay")):
                for f in sorted(d.iterdir()) if d else []:
                    if f.is_file() and kind(f.name):
                        seen[f.name.upper()] = (f, where)
            if not seen:
                print("no saves yet")
            for name, (f, where) in sorted(seen.items()):
                problem, unchecked = checksum_note(f.name, f.read_bytes(), exe)
                note = not_a_save(f.name, f.stat().st_size) or problem or unchecked or "ok"
                print(f"{name}\t{f.stat().st_size}\t{where}\t{note}")
        elif a.cmd == "import":
            saves, skipped = read_pick(a.files, a.frm)
            for s in skipped:
                print(f"skipped: {s}")
            if not saves:
                raise Refused("no save files found")
            notes = {n: checksum_note(n, d, exe) for n, d in saves.items()}
            for n, (_, unchecked) in notes.items():
                if unchecked:
                    print(f"{n}: {unchecked}")
            bad = [p for p, _ in notes.values() if p]
            if bad and not a.force:
                raise Refused("; ".join(bad) + " (--force imports it anyway)")
            b = backup(overlay, a.backups)
            for name, data in saves.items():
                existing = next((f for f in overlay.iterdir() if f.name.lower() == name.lower()), overlay / name)
                write_atomically(existing, data)
            print(f"imported {', '.join(sorted(saves))}; the overlay's saves before it are in backup {b.name}")
        elif a.cmd == "export":
            seen = {}
            for d in (install, overlay):
                for f in sorted(d.iterdir()) if d else []:
                    if f.is_file() and not_a_save(f.name, f.stat().st_size) is None:
                        seen[f.name.upper()] = f
            if not seen:
                raise Refused("there are no saves yet")
            with zipfile.ZipFile(a.out, "w", zipfile.ZIP_DEFLATED) as z:
                for name, f in sorted(seen.items()):
                    z.write(f, f"SAVEDATA/{f.name}")
            print(f"wrote {a.out} ({', '.join(sorted(seen))})")
        elif a.cmd == "backups":
            rows = sorted((d for d in a.backups.iterdir() if d.is_dir()), reverse=True) if a.backups.is_dir() else []
            if not rows:
                print("no backups yet: one is made at every import")
            for d in rows:
                print(f"{d.name}\t{', '.join(sorted(f.name for f in d.iterdir())) or '(no saves)'}")
        elif a.cmd == "restore":
            src = a.backups / a.backup
            if Path(a.backup).name != a.backup or a.backup in ("", ".", ".."):
                raise Refused(f"{a.backup} is not a backup's name (see `backups`)")
            if not src.is_dir():
                raise Refused(f"no backup {a.backup} (see `backups`)")
            # Read first: the backup made next can prune `src` when it is the oldest of KEEP.
            restored = {f.name: f.read_bytes() for f in src.iterdir() if f.is_file() and not not_a_save(f.name, f.stat().st_size)}
            b = backup(overlay, a.backups)
            for f in list(overlay.iterdir()):
                if f.is_file() and kind(f.name):
                    f.unlink()
            for name, data in restored.items():
                write_atomically(overlay / name, data)
            print(f"restored {a.backup}; the overlay's saves before it are in backup {b.name}")
    except Refused as e:
        print(f"saves: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
