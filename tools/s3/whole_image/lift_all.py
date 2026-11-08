#!/usr/bin/env python3
"""S3 whole-image lift: every function in the function map, all regions.

Design 5 (S3): *"Emit every function in the function map, all regions."* S2's
`tools/s2/corpus_run/lift_set.py` lifts the fixture registry's `direct_calls`
closure; this lifts the map. The remill invocation and the PC24 pass are S2's,
flag for flag, with **both** helpers (`--helper-ll` and `--int-helper-ll`):
the second was unwired for every module S2's metrics were computed from
(`Documentation/S3_INPUTS.md` section 3), so this tool asserts `fist_rewrites`
into the log instead of assuming the flag took effect.

Two differences from `lift_set.py`, both forced by the size:

  * the pass runs **in process** (`pc24pass.instrument`, imported read-only)
    inside a process-pool worker rather than as one `python` per function:
    12 683 interpreter start-ups is a measurable fraction of the wall;
  * the log is append-only JSONL, not one JSON document rewritten per
    function.

Per function the log records: lifted or not with the error head, IR lines,
missing-block call sites, the pass's instrumented-op counts by class, and the
lift and pass wall times. It also records the FLD quieting tests the pass
rewrote and refused (`fld_quiet_fixed`, `fld_quiet_refused`, and the refused
sites); a refusal leaves that module with remill's quieting of every FLD
m32/m64 while IE is set, so the run exits nonzero, after writing the log, when
any function has one.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s3/whole_image/lift_all.py [--jobs 16] [--limit N]
                                                    [--only 0x.. ...] [--force]
                                                    [--to-next-entry] [--tag NAME]
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

_IMAGE: bytes | None = None
_HELPER: str | None = None
_INT_HELPER: str | None = None
_LLDIR: Path | None = None

MISSING_BLOCK_CALL = re.compile(r"\bcall [^\n]*@__remill_missing_block\b")
ERROR_CALL = re.compile(r"\bcall [^\n]*@__remill_error\b")
DEFINE = re.compile(r"^define [^\n]*@(sub_[0-9a-fA-F]+(?:\.\d+)?)\(", re.M)


def _init(lldir: str) -> None:
    global _IMAGE, _HELPER, _INT_HELPER, _LLDIR
    _IMAGE = C.memory_image()
    _HELPER = C.HELPER_LL.read_text()
    _INT_HELPER = C.INT_HELPER_LL.read_text()
    _LLDIR = Path(lldir)


def error_head(text: str, lines: int = 3) -> str:
    return "\n".join(text.strip().splitlines()[:lines])


def lift_one(job: tuple[int, int, int, str, str]) -> dict:
    """`(va, start, end, name, region)` -> one log record. Runs in a worker process."""
    import pc24pass  # tools/spike/pc24pass, read-only

    va, start, end, name, region = job
    raw = _IMAGE[start - C.IMAGE_BASE:end - C.IMAGE_BASE]
    ll = _LLDIR / f"sub_{va:08X}.ll"
    inner = (f"mount --bind {C.REMILL_PREFIX} /usr/local && /usr/local/bin/remill-lift-14 "
             f"--arch x86 --os windows --address {va:#x} --bytes {raw.hex()} --ir_out {ll}")
    cmd = ["unshare", "-Urm", "--propagation", "unchanged", "sh", "-c", inner]
    t0 = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        rc, err = p.returncode, p.stderr + p.stdout
    except subprocess.TimeoutExpired:
        rc, err = -1, "timeout after 900 s"
    lift_s = round(time.time() - t0, 3)

    text = ll.read_text(errors="replace") if ll.exists() else ""
    lifted = rc == 0 and re.search(rf"^define [^\n]*@sub_{va:x}\(", text, re.M) is not None
    rec = {"va": f"0x{va:08x}", "name": name, "region": region,
           "bytes": len(raw), "end": f"0x{end:08x}",
           "lift_seconds": lift_s, "rc": rc, "lifted": lifted,
           "ll_lines": text.count("\n"), "ll_bytes": len(text),
           "missing_block_calls": len(MISSING_BLOCK_CALL.findall(text)),
           "remill_error_calls": len(ERROR_CALL.findall(text)),
           "defines": len(DEFINE.findall(text)),
           "error_head": "" if lifted else error_head(err)}
    if not lifted:
        return rec

    t1 = time.time()
    try:
        out, st = pc24pass.instrument(text, _HELPER, _INT_HELPER, True, True, True)
        patched = _LLDIR / f"sub_{va:08X}.pc24.ll"
        patched.write_text(out)
        rec["pass"] = {"ok": True, "error_head": "",
                       "out_lines": out.count("\n"), "out_bytes": len(out),
                       "ops_instrumented": st.get("ops_instrumented"),
                       "fist_rewrites": st.get("fist_rewrites"),
                       "fpatan_rewrites": st.get("fpatan_rewrites"),
                       "fpatan_refused": st.get("fpatan_refused"),
                       "control_word_masks_fixed": st.get("control_word_masks_fixed"),
                       "f80_accessor_attrs_fixed": st.get("f80_accessor_attrs_fixed"),
                       "fld_quiet_fixed": st.get("fld_quiet_fixed"),
                       "fld_quiet_refused": st.get("fld_quiet_refused"),
                       **({"fld_quiet_refused_sites": st["fld_quiet_refused_sites"]}
                          if st.get("fld_quiet_refused") else {}),
                       "by_op": st.get("by_op")}
    except Exception as exc:                          # the pass is text surgery; a
        rec["pass"] = {"ok": False,                   # refusal is a finding, not a crash
                       "error_head": f"{type(exc).__name__}: {exc}"[:400]}
    rec["pass_seconds"] = round(time.time() - t1, 3)
    return rec


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 8)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--tag", default="map",
                    help="output subdirectory and log name; `map` is the default "
                         "(each function lifted to entry+size, the map's own word)")
    ap.add_argument("--to-next-entry", action="store_true",
                    help="lift each function from its entry to the next map entry "
                         "instead of to entry+size. S2's per-address workaround for the "
                         "map's size under-reports (S3_INPUTS section 1), here as a "
                         "whole-set switch so the two can be compared rather than argued.")
    args = ap.parse_args()
    C.helpers()

    lldir = C.LIFT / args.tag
    lldir.mkdir(parents=True, exist_ok=True)
    log = C.LIFT / f"{args.tag}.jsonl"
    done = {} if args.force else C.read_jsonl(log)

    fmap = C.by_address()
    starts = sorted(fmap)
    targets = [int(a, 16) for a in args.only] if args.only else starts
    if args.limit:
        targets = targets[:args.limit]

    import bisect
    jobs = []
    for va in targets:
        fn = fmap[va]
        if args.to_next_entry:
            i = bisect.bisect_right(starts, va)
            end = starts[i] if i < len(starts) else va + fn["size"]
        else:
            end = va + fn["size"]
        if not args.force and done.get(f"0x{va:08x}", {}).get("lifted"):
            continue
        jobs.append((va, va, end, fn["name"], fn["region"]))

    print(f"tag={args.tag} to_next_entry={args.to_next_entry} jobs={args.jobs} "
          f"targets={len(targets)} already_lifted={len(targets) - len(jobs)} todo={len(jobs)}",
          flush=True)
    t0 = time.time()
    n = ok = 0
    with log.open("a") as fh, cf.ProcessPoolExecutor(
            max_workers=args.jobs, initializer=_init, initargs=(str(lldir),)) as ex:
        for rec in ex.map(lift_one, jobs, chunksize=4):
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
            n += 1
            ok += bool(rec["lifted"])
            if n % 200 == 0:
                fh.flush()
                el = time.time() - t0
                print(f"  {n}/{len(jobs)} lifted={ok} {el:.0f}s "
                      f"({n / el:.1f}/s, eta {(len(jobs) - n) / max(n / el, 1e-9) / 60:.1f} min)",
                      flush=True)
    wall = time.time() - t0
    print(f"done {n} functions, {ok} lifted, {wall:.1f}s wall at {args.jobs} jobs "
          f"({n / wall:.1f} functions/s)")
    C.write_wall(
        C.LIFT / f"{args.tag}.wall.json",
        {"tag": args.tag, "jobs": args.jobs, "to_next_entry": args.to_next_entry,
         "attempted": n, "lifted": ok, "wall_seconds": round(wall, 2),
         "functions_per_second": round(n / wall, 3) if wall else None,
         "nproc": os.cpu_count()},
        did_work=n > 0)
    # Over the log's records of every target, so a resumed run that lifted nothing new still
    # fails on a refusal an earlier run logged.
    logged = C.read_jsonl(log)
    fld_refused = [va for va in (f"0x{t:08x}" for t in targets)
                   if ((logged.get(va) or {}).get("pass") or {}).get("fld_quiet_refused")]
    if fld_refused:
        raise SystemExit(fld_refusal(fld_refused, log))


def fld_refusal(vas: list[str], log: Path) -> str:
    """The failure for functions whose FLD quieting fix the PC24 pass refused: such a module
    keeps remill's quieting of every FLD m32/m64 while IE is set (pc24pass.py, "FLD"), so the
    lift fails rather than leave it in the image unseen. Its record holds the sites."""
    return (f"the PC24 pass refused the FLD quieting fix in {len(vas)} function(s), first "
            f"{vas[:5]}: each keeps remill's quieting of every FLD m32/m64 while IE is set; the "
            f"sites are in their records' pass.fld_quiet_refused_sites ({log})")


if __name__ == "__main__":
    main()
