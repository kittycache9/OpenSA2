"""A fresh checkout can rebuild the lifted image and match the recorded hashes.

Two things stood between a clone and the IR every release records:

* tools/spike/spikelib.py `helper_ir` compiles the PC24 helpers on their bare file
  names, from their own folders. Given an absolute path, clang wrote it into the
  module, the pass stamped the helper's sha256 into every module it rewrote, and no
  checkout at another path could match a recorded `ir_sha256`;
* tools/s4/track_extra_functions.py `--restore` writes the extras' generated state
  back from Documentation/lifted-extras.json, which a clone has, so
  tools/s4/rebuild_image.py can rebuild every extra without finding each block again
  by running the game.

The helper case compiles with the pinned zig and is skipped where it is absent. The
restore case runs on a two-row record under pytest's tmp_path.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "spike"))
sys.path.insert(0, str(ROOT / "tools" / "s4"))
import spikelib  # noqa: E402
import track_extra_functions as T  # noqa: E402

HELPERS = (ROOT / "tools" / "spike" / "pc24pass" / "pc_round.c",
           ROOT / "tools" / "s2" / "patch" / "x87_int.c")


@pytest.mark.skipif(not spikelib.ZIG.is_file(), reason="the pinned zig is not installed")
@pytest.mark.parametrize("src", HELPERS, ids=lambda p: p.name)
def test_a_helper_is_the_same_ir_from_any_checkout_path(tmp_path, src):
    other = tmp_path / "another" / "checkout" / "somewhere"
    other.mkdir(parents=True)
    shutil.copy2(src, other / src.name)
    here = spikelib.helper_ir(src, tmp_path / "here.ll")
    there = spikelib.helper_ir(other / src.name, tmp_path / "there.ll")
    assert here.read_bytes() == there.read_bytes()
    assert here.read_text().splitlines()[0] == f"; ModuleID = '{src.name}'"


ROWS = [
    {"va": "0x00401000", "end": "0x0040100b", "bytes": 11, "lifted": True, "compiled": True,
     "folded": True, "declares_sub": [], "why": "reached by a run",
     "owner": {"start": "0x00401000", "end": "0x0040100b", "name": "(--end)"}},
    {"va": "0x00436410", "end": "0x00436480", "anchor": "0x00436400", "bytes": 112,
     "lifted": True, "compiled": True, "folded": True, "declares_sub": [], "why": "an arm",
     "owner": {"start": "0x00436400", "end": "0x00436480", "name": "sub_00436400"}},
]


@pytest.fixture
def record(tmp_path, monkeypatch):
    gen = tmp_path / "private" / "generated"
    monkeypatch.setattr(T.C, "ROOT", tmp_path)
    monkeypatch.setattr(T.C, "GEN", gen)
    monkeypatch.setattr(T, "OUT", tmp_path / "Documentation" / "lifted-extras.json")
    monkeypatch.setattr(T, "BACKFILL", tmp_path / "Documentation" / "none.json")
    state = gen / "s4" / "link" / "extra_functions.next+allarms.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"lift_tag": "next", "arms_tag": "next", "rows": [
        {**r, "lift_seconds": 0.1, "object": "/a/working/directory/ir.o"} for r in ROWS]}))
    T.OUT.parent.mkdir(parents=True)
    T.OUT.write_text(json.dumps(T.project("next+allarms")))
    state.unlink()
    return state


def test_restore_writes_a_state_that_projects_back_to_the_record(record):
    T.restore("next+allarms")
    assert T.project("next+allarms") == json.loads(T.OUT.read_text())
    rows = json.loads(record.read_text())["rows"]
    assert [r["object"] for r in rows] == [
        str(T.C.GEN / "s4" / "lift" / "obj" / "ir_00401000.o"),
        str(T.C.GEN / "s4" / "lift" / "obj" / "ir_00436410.o")]
    assert rows[1]["anchor"] == "0x00436400"


def test_restore_refuses_to_overwrite_a_state(record):
    T.restore("next+allarms")
    with pytest.raises(SystemExit, match="only writes a missing state"):
        T.restore("next+allarms")
