#!/usr/bin/env python3
"""Paths, the function map and the image, shared by the S3 whole-image tools.

S3's unit of work is the whole function map (12 683 functions, all regions), not
S2's fixture-covered closure, so the S2 corpus tools are invoked and imported
read-only and never edited: `tools/s2/corpus_run/lift_set.py`'s closure rule is
the wrong rule here (design 5: *"emit every function in the function map, all
regions"*), and its log is one JSON file rewritten per function, which is
O(n^2) at this size. Everything else the corpus paid for -- the remill
invocation, the PC24 pass with **both** helpers, `internalize.rewrite`, the
reaching-instruction edge classification -- is reused rather than reinvented,
and every reuse names its source.

Output lives under `private/generated/s3/` (ignored). No lifted output is ever
written into the repository (AGENTS.md rule 9).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "spike"))
sys.path.insert(0, str(ROOT / "tools" / "spike" / "pc24pass"))
sys.path.insert(0, str(ROOT / "tools" / "s2" / "corpus_run"))

GEN = ROOT / "private" / "generated"
OUT = GEN / "s3" / "whole_image"
LIFT = OUT / "lift"
FOLD = OUT / "fold"
BUILD = OUT / "build"
EXE = ROOT / "private" / "exe" / "sonic2app-unpacked.exe"
MAP = GEN / "function-map.json"
REMILL_PREFIX = ROOT / "private" / "lifters" / "remill" / "usr" / "local"
REMILL_INC = REMILL_PREFIX / "include"

# The two helpers design 3.4 mandates. `--helper-ll` (PC24 significand rounding)
# was always wired; `--int-helper-ll` (the FIST/FISTP helper) was silently absent
# from every S2 pipeline until the G2 fold (S3_INPUTS section 3), so S3 asserts
# the resulting counter rather than assuming the flag.
HELPER_LL = GEN / "spike" / "pc24pass" / "pc_round.ll"
INT_HELPER_LL = GEN / "s2" / "corpus_run" / "helper" / "x87_int.ll"


def helpers() -> None:
    """Build both PC24 helper modules when missing or stale. Every lift splices them in,
    and a fresh checkout has neither; the pass reads them, it does not make them."""
    import spikelib  # noqa: PLC0415  tools/spike/spikelib.py, on the path above
    spikelib.helper_ir(ROOT / "tools" / "spike" / "pc24pass" / "pc_round.c", HELPER_LL)
    spikelib.helper_ir(ROOT / "tools" / "s2" / "patch" / "x87_int.c", INT_HELPER_LL)

IMAGE_BASE = 0x00400000
REGIONS = ("game", "crt", "cri", "other")


def function_map() -> dict:
    return json.loads(MAP.read_text())


def functions() -> list[dict]:
    return function_map()["functions"]


def by_address() -> dict[int, dict]:
    return {int(f["address"], 16): f for f in functions()}


def memory_image() -> bytes:
    import pefile
    return bytes(pefile.PE(str(EXE), fast_load=True).get_memory_mapped_image())


def sha256(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_jsonl(path: Path) -> dict[str, dict]:
    """The per-function log, keyed by `va`. JSONL because a 12 683-entry JSON
    document rewritten once per completed function is quadratic and loses
    everything on a kill; an append-only line log loses at most one record."""
    out: dict[str, dict] = {}
    if not path.exists():
        return out
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:      # a torn final line from a kill
                continue
            out[rec["va"]] = rec
    return out


def write_wall(path: Path, record: dict, did_work: bool) -> None:
    """Record a run's wall time, keeping the headline file honest on a resume.

    These tools resume: a second run skips every function already done, so it
    finishes in no time with `attempted` near zero. Writing that over the
    headline file destroys the only measurement of the run that did the work,
    and leaves a file that still looks like a measurement. It already
    happened: `build/map.wall.json` records 12 651 of 12 683 functions from a
    resume, and a journal quoted its seconds as the whole-image build time
    (corrected as `[c6]` in the S3 slice 1 journal).

    So: every run appends to a `.wall.jsonl` history, and the headline file is
    replaced only by a run that actually did something. A run that did nothing
    is still on the record, in the history, where it cannot be mistaken for
    the measurement.
    """
    history = path.with_suffix(".jsonl")
    with history.open("a") as fh:
        fh.write(json.dumps({**record, "did_work": did_work}, sort_keys=True) + "\n")
    if did_work or not path.exists():
        path.write_text(json.dumps(record, indent=1) + "\n")
