"""tools/normalize_unpacked.py on synthetic executables: a known variant becomes the pinned file, the
pinned file and any other file are left as they are, and a refused file is never half written."""
from __future__ import annotations

import hashlib
import importlib.util
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("normalize_unpacked", ROOT / "tools/normalize_unpacked.py")
NU = importlib.util.module_from_spec(spec)
spec.loader.exec_module(NU)


def exe(names: list[bytes], tail: bytes = b"body") -> bytes:
    """MZ header, PE header with no optional header, a section table with `names`, then `tail`."""
    pe = 0x40
    head = bytearray(pe + 24)
    head[:2] = b"MZ"
    struct.pack_into("<I", head, 0x3C, pe)
    head[pe:pe + 4] = b"PE\0\0"
    struct.pack_into("<H", head, pe + 6, len(names))
    table = b"".join(n.ljust(8, b"\0") + bytes(32) for n in names)
    return bytes(head) + table + tail


PINNED = exe([b".text", b".dataa", b".rsrca", b".reloc"])
VARIANT = exe([b".text", b".data", b".rsrc", b".reloc"])
PIN = hashlib.sha256(PINNED).hexdigest()


def setup(monkeypatch, tmp_path, data: bytes) -> Path:
    monkeypatch.setattr(NU, "VARIANTS", {hashlib.sha256(VARIANT).hexdigest(): ((b".data", b".dataa"),
                                                                              (b".rsrc", b".rsrca"))})
    monkeypatch.setattr(NU, "pinned_sha256", lambda: PIN)
    p = tmp_path / "sonic2app-unpacked.exe"
    p.write_bytes(data)
    return p


def test_the_known_variant_becomes_the_pinned_file(monkeypatch, tmp_path, capsys):
    p = setup(monkeypatch, tmp_path, VARIANT)
    assert NU.main([str(p)]) == 0
    assert p.read_bytes() == PINNED
    assert "renamed .data to .dataa, .rsrc to .rsrca; it is now the pinned file" in capsys.readouterr().out


def test_the_pinned_file_is_left_as_it_is(monkeypatch, tmp_path, capsys):
    p = setup(monkeypatch, tmp_path, PINNED)
    assert NU.main([str(p)]) == 0
    assert p.read_bytes() == PINNED
    assert "already the pinned file" in capsys.readouterr().out


def test_any_other_file_is_refused_and_unchanged(monkeypatch, tmp_path, capsys):
    other = exe([b".text", b".data", b".rsrc", b".reloc"], tail=b"other")
    p = setup(monkeypatch, tmp_path, other)
    assert NU.main([str(p)]) == 1
    assert p.read_bytes() == other
    assert "neither the pin" in capsys.readouterr().err


def test_a_variant_that_renames_to_another_file_is_refused_and_unchanged(monkeypatch, tmp_path, capsys):
    p = setup(monkeypatch, tmp_path, VARIANT)
    monkeypatch.setattr(NU, "pinned_sha256", lambda: hashlib.sha256(b"something else").hexdigest())
    assert NU.main([str(p)]) == 1
    assert p.read_bytes() == VARIANT
    assert not (tmp_path / "sonic2app-unpacked.exe.part").exists()
    assert "did not give the pinned file" in capsys.readouterr().err


def test_the_real_variant_and_pin_are_the_ones_issue_41_found():
    assert NU.pinned_sha256() == "c97b116fbd1d64108cbc8ec403b4a59ba6f815159b9a0ad896668d6c19c1bfe9"
    assert NU.VARIANTS == {"ebb53fa1c47fec4eab6e22fe9e59e876215fb3b7a5391b8eeeee86d01b00410f":
                           ((b".data", b".dataa"), (b".rsrc", b".rsrca"))}
