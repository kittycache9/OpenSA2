"""tools/saves.py without the game: a scratch config, synthetic saves and a stand-in executable.

The saves here are filler of the right sizes, and the stand-in executable is a minimal 32-bit PE
whose section holds the standard CRC-32 table where the game keeps its own, so the Chao World
checksum is exercised with no game bytes. Covered: what is refused (a wrong name or size, a ZIP
holding a save twice, a ZIP with other files, a bad checksum without --force), what an import
writes (the overlay alone, the game folder untouched), a Debug Export's two save sets, export,
the backups (one per import, the newest ten kept) and a restore of the oldest backup.
"""
from __future__ import annotations

import importlib.util
import struct
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("saves", ROOT / "tools/saves.py")
SV = importlib.util.module_from_spec(spec)
spec.loader.exec_module(SV)

MAIN, CHAO = 24576, 65536


def crc32_table() -> list[int]:
    out = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = (c >> 1) ^ 0xEDB88320 if c & 1 else c >> 1
        out.append(c)
    return out


def fake_exe(path: Path) -> Path:
    """MZ, PE header, one section at the table's address holding the stand-in table."""
    base, rva = 0x400000, SV.ALF_CRC_TABLE_VA - 0x400000
    pe = 0x40
    optsize = 0xE0
    sec = pe + 24 + optsize
    raw = 0x200
    data = bytearray(raw + 1024)
    data[0:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, pe)
    data[pe:pe + 4] = b"PE\0\0"
    struct.pack_into("<HH", data, pe + 4, 0x14C, 1)
    struct.pack_into("<H", data, pe + 20, optsize)
    struct.pack_into("<H", data, pe + 24, 0x10B)
    struct.pack_into("<I", data, pe + 52, base)
    data[sec:sec + 8] = b".data\0\0\0"
    struct.pack_into("<IIII", data, sec + 8, 1024, rva, 1024, raw)
    data[raw:raw + 1024] = struct.pack("<256I", *crc32_table())
    path.write_bytes(bytes(data))
    return path


def alf(good: bool = True) -> bytes:
    d = bytearray(CHAO)
    d[0:16] = b"CHAO GARDEN DATA"
    for i in range(SV.ALF_SAVE_SIZE):
        d[SV.ALF_SAVE_OFFSET + i] = (i * 7) & 0xFF
    _, calc = SV.alf_checksums(bytes(d), crc32_table())
    if not good:
        calc ^= 1
    for i, p in enumerate(SV.ALF_CRC_POS):
        d[SV.ALF_SAVE_OFFSET + p] = (calc >> (8 * i)) & 0xFF
    return bytes(d)


@pytest.fixture
def setup(tmp_path: Path):
    game = tmp_path / "game"
    (game / "resource/gd_PC/SAVEDATA").mkdir(parents=True)
    (game / "resource/gd_PC/SAVEDATA/SONIC2B__S01").write_bytes(b"\1" * MAIN)
    overlay = tmp_path / "overlay"
    cfg = tmp_path / "sa2recomp.cfg"
    exe = fake_exe(tmp_path / "sonic2app.exe")
    cfg.write_text(f"read_root={game}\noverlay={overlay}\nexe={exe}\n")
    backups = tmp_path / "backups"

    def run(*args: str) -> int:
        return SV.main(["--config", str(cfg), "--backups", str(backups), *args])

    return tmp_path, run, overlay / "resource/gd_PC/SAVEDATA", backups, game


def tree(d: Path) -> dict[str, bytes]:
    return {str(p.relative_to(d)): p.read_bytes() for p in sorted(d.rglob("*")) if p.is_file()}


def test_the_checksum_reads_the_table_from_the_executable(tmp_path):
    exe = fake_exe(tmp_path / "x.exe")
    assert SV.crc_table(exe) == crc32_table()
    assert SV.checksum_note("SONIC2B__ALF", alf(), exe) == (None, None)
    assert "computed" in SV.checksum_note("SONIC2B__ALF", alf(False), exe)[0]
    junk = tmp_path / "junk.exe"
    junk.write_bytes(b"not a program")
    assert SV.checksum_note("SONIC2B__ALF", alf(False), junk)[1].startswith("checksum not checked")


def test_an_import_writes_the_overlay_alone_and_backs_up_first(setup, capsys):
    tmp, run, saves, backups, game = setup
    before = tree(game)
    (tmp / "SONIC2B__ALF").write_bytes(alf())
    (tmp / "sonic2b__s02").write_bytes(b"\2" * MAIN)
    assert run("import", str(tmp / "SONIC2B__ALF"), str(tmp / "sonic2b__s02")) == 0
    assert sorted(p.name for p in saves.iterdir()) == ["SONIC2B__ALF", "SONIC2B__S02"]
    assert tree(game) == before
    assert len(list(backups.iterdir())) == 1


@pytest.mark.parametrize("name,data,why", [
    ("SONIC2B__S01", b"\0" * 1000, "1000 bytes"),
    ("notes.txt", b"hi", "not a save's name"),
])
def test_a_wrong_file_is_refused(setup, capsys, name, data, why):
    tmp, run, saves, backups, _ = setup
    (tmp / name).write_bytes(data)
    assert run("import", str(tmp / name)) == 2
    assert why in capsys.readouterr().out
    assert not backups.exists()


def test_a_bad_checksum_is_refused_unless_forced(setup, capsys):
    tmp, run, saves, _, _ = setup
    (tmp / "SONIC2B__ALF").write_bytes(alf(False))
    assert run("import", str(tmp / "SONIC2B__ALF")) == 2
    assert "--force" in capsys.readouterr().err
    assert run("import", str(tmp / "SONIC2B__ALF"), "--force") == 0


def test_zips_a_session_export_and_refusals(setup, capsys):
    tmp, run, saves, _, _ = setup
    z = tmp / "export.zip"
    with zipfile.ZipFile(z, "w") as w:
        w.writestr("session/start-saves/SONIC2B__S01", b"\3" * MAIN)
        w.writestr("session/end-saves/SONIC2B__S01", b"\4" * MAIN)
        w.writestr("session/end-saves/index.tsv", b"x")
    assert run("import", str(z)) == 0
    assert (saves / "SONIC2B__S01").read_bytes()[:1] == b"\4"
    assert run("import", str(z), "--from", "start") == 0
    assert (saves / "SONIC2B__S01").read_bytes()[:1] == b"\3"
    dup = tmp / "dup.zip"
    with zipfile.ZipFile(dup, "w") as w:
        w.writestr("a/SONIC2B__S01", b"\5" * MAIN)
        w.writestr("b/SONIC2B__S01", b"\5" * MAIN)
    assert run("import", str(dup)) == 2
    assert run("import", str(z), str(tmp / "export.zip")) == 2


def test_an_oversized_entry_or_file_is_refused_unread(setup, monkeypatch, capsys):
    """A save-named ZIP entry that would decompress far past a save's size (a ZIP bomb) and a
    save-named file of the wrong size are refused by their declared and on-disk sizes, unread."""
    tmp, run, saves, _, _ = setup
    bomb = tmp / "bomb.zip"
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as w:
        w.writestr("SAVEDATA/SONIC2B__ALF", bytes(32 << 20))   # 32 MB of zeros, a few KB compressed
    read = []
    real_read = zipfile.ZipFile.read
    monkeypatch.setattr(zipfile.ZipFile, "read", lambda self, name, *a: read.append(name) or real_read(self, name, *a))
    assert run("import", str(bomb)) == 2
    assert read == [] and "33554432 bytes" in capsys.readouterr().out
    big = tmp / "SONIC2B__S01"
    big.write_bytes(bytes(MAIN + 1))
    monkeypatch.setattr(type(big), "read_bytes", lambda self: pytest.fail(f"{self} was read"))
    assert run("import", str(big)) == 2
    assert not (saves / "SONIC2B__S01").exists()


def test_export_then_restore_the_oldest_backup(setup):
    tmp, run, saves, backups, game = setup
    (tmp / "SONIC2B__S02").write_bytes(b"\2" * MAIN)
    for _ in range(SV.KEEP + 2):
        assert run("import", str(tmp / "SONIC2B__S02")) == 0
    assert len(list(backups.iterdir())) == SV.KEEP
    assert run("export", str(tmp / "out.zip")) == 0
    assert sorted(zipfile.ZipFile(tmp / "out.zip").namelist()) == ["SAVEDATA/SONIC2B__S01", "SAVEDATA/SONIC2B__S02"]
    oldest = sorted(backups.iterdir())[0]
    want = tree(oldest)
    assert run("restore", oldest.name) == 0
    assert tree(saves) == want
    assert len(list(backups.iterdir())) == SV.KEEP
