#!/usr/bin/env python3
"""S2 corpus run, phase `lift`: lift every fixture-covered function and, transitively,
every direct callee the function map names, with remill + the PC24 pass.

Design 5 (S2): *"Emitter handles ... every function any `reference-project` fixture covers
(the 36 kernels' native counterparts and their callees)"*. The roots are the distinct
single-entry addresses `Documentation/receipts/fixture-registry.json` names; the set is
their transitive `direct_calls` closure out of `private/generated/function-map.json`.

Invocation and post-pass are the S1 spike's, unchanged and imported read-only:
`remill-lift-14 --arch x86 --os windows --address <va> --bytes <hex>` under
`unshare -Urm` with the semantics bind mount, then `tools/spike/pc24pass/pc24pass.py`
with **both** helpers: `--helper-ll pc_round.ll` (precision-control rounding) and
`--int-helper-ll x87_int.ll` (FIST/FISTP). The second was missing until the G2 fold, so
every module this tool produced carried remill's `llvm.nearbyint.f80` and its poison
`fptosi x86_fp80 to iN` unchanged and reported `fist_rewrites` 0.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s2/corpus_run/lift_set.py [--force] [--jobs N] [--only 0x...]
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "tools" / "spike"))
import spikelib as S  # noqa: E402  (read-only)

GEN = ROOT / "private" / "generated"
OUT = GEN / "s2" / "corpus_run"
LIFT = OUT / "lift"
EXE = ROOT / "private" / "exe" / "sonic2app-unpacked.exe"
MAP = GEN / "function-map.json"
REGISTRY = ROOT / "Documentation" / "receipts" / "fixture-registry.json"
REMILL_PREFIX = ROOT / "private" / "lifters" / "remill" / "usr" / "local"
PASS = ROOT / "tools" / "spike" / "pc24pass" / "pc24pass.py"
HELPER_LL = GEN / "spike" / "pc24pass" / "pc_round.ll"

# The FIST/FISTP integer helper (design 3.4). Without `--int-helper-ll` the pass leaves
# remill's `llvm.nearbyint.f80` (host rounding mode) and `fptosi x86_fp80 to iN` (LLVM
# poison out of range, not x87's integer indefinite) in place, and `fist_rewrites` is 0.
# The source is tools/s2/patch/x87_int.c (read-only here); the IR is compiled beside the
# corpus run's own output so this pipeline does not depend on another unit having run.
INT_HELPER_SRC = ROOT / "tools" / "s2" / "patch" / "x87_int.c"
INT_HELPER_LL = OUT / "helper" / "x87_int.ll"

# The eleven S1/S2 functions already lifted by tools/s2/wrap (re-used, not re-lifted).
WRAP_LIFT = GEN / "s2" / "wrap" / "lift"


def int_helper_ll() -> Path:
    """Compile tools/s2/patch/x87_int.c to IR if it is missing or older than its source."""
    return S.helper_ir(INT_HELPER_SRC, INT_HELPER_LL)


def function_map() -> dict[int, dict]:
    return {int(f["address"], 16): f for f in json.loads(MAP.read_text())["functions"]}


def roots() -> list[int]:
    reg = json.loads(REGISTRY.read_text())["fixtures"]
    return sorted({int(f["function"], 16) for f in reg
                   if isinstance(f.get("function"), str) and f["function"].strip()})


def closure(seeds: list[int], fmap: dict[int, dict]) -> dict[int, int]:
    """address -> shortest hop count from a root, over `direct_calls`."""
    depth = {a: 0 for a in seeds}
    frontier = list(seeds)
    while frontier:
        nxt = []
        for a in frontier:
            fn = fmap.get(a)
            if not fn:
                continue
            for c in fn.get("direct_calls", []):
                ca = int(c, 16)
                if ca not in depth:
                    depth[ca] = depth[a] + 1
                    nxt.append(ca)
        frontier = nxt
    return depth


def memory_image() -> bytes:
    import pefile
    return bytes(pefile.PE(str(EXE), fast_load=True).get_memory_mapped_image())


def lift_one(va: int, raw: bytes) -> dict:
    ll = LIFT / f"sub_{va:08X}.ll"
    inner = (f"mount --bind {REMILL_PREFIX} /usr/local && /usr/local/bin/remill-lift-14 "
             f"--arch x86 --os windows --address {va:#x} --bytes {raw.hex()} --ir_out {ll}")
    cmd = ["unshare", "-Urm", "--propagation", "unchanged", "sh", "-c", inner]
    t0 = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        rc, err = p.returncode, p.stderr + p.stdout
    except subprocess.TimeoutExpired:
        rc, err = -1, "timeout after 1800 s"
    secs = round(time.time() - t0, 3)
    text = ll.read_text(errors="replace") if ll.exists() else ""
    lifted = rc == 0 and re.search(rf"^define [^\n]*@sub_{va:x}\(", text, re.M) is not None
    entry = {"va": f"0x{va:08x}", "bytes": len(raw), "seconds": secs, "rc": rc, "lifted": lifted,
             "ll": str(ll.relative_to(ROOT)), "ll_lines": text.count("\n"),
             "missing_blocks": len(re.findall(r"__remill_missing_block", text)),
             "error_head": "" if lifted else S.error_head(err)}
    if not lifted:
        return entry
    patched = LIFT / f"sub_{va:08X}.pc24.ll"
    stats = LIFT / f"sub_{va:08X}.pc24.stats.json"
    p2 = subprocess.run([sys.executable, str(PASS), "--helper-ll", str(HELPER_LL),
                         "--int-helper-ll", str(int_helper_ll()),
                         str(ll), str(patched), "--stats", str(stats)],
                        capture_output=True, text=True)
    st = json.loads(stats.read_text()) if stats.exists() else {}
    entry["pass"] = {"rc": p2.returncode,
                     "error_head": S.error_head(p2.stderr) if p2.returncode else "",
                     "ll": str(patched.relative_to(ROOT)) if patched.exists() else None,
                     "ops_instrumented": st.get("ops_instrumented"),
                     "fist_rewrites": st.get("fist_rewrites"),
                     "fpatan_rewrites": st.get("fpatan_rewrites"),
                     "fpatan_refused": st.get("fpatan_refused"),
                     "control_word_masks_fixed": st.get("control_word_masks_fixed"),
                     "f80_accessor_attrs_fixed": st.get("f80_accessor_attrs_fixed"),
                     "by_op": st.get("by_op")}
    return entry


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--to-next-entry", nargs="*", default=None,
                    help="lift these addresses from their entry to the next entry in the "
                         "function map rather than to entry+size. The map under-reports at "
                         "least one function: 0x0047D160 is recorded as 1351 bytes, ending "
                         "0x0047D6A7, which cuts the final `jg 0x0047D550` in half and drops "
                         "the epilogue, so remill emits `__remill_error` and every call into "
                         "it traps at 0x0047D6A2. The next map entry is 0x0047D6B0, 1360 bytes "
                         "on. Off by default: widening every function would move digests the "
                         "determinism gate pins, for a defect that belongs to the map.")
    ap.add_argument("--block", nargs="*", default=None,
                    help="lift at an address the function map does not cover (a jump target "
                         "inside a gap), from there to the next mapped entry. `START:END` "
                         "lifts exactly that range instead, which is how a window *inside* a "
                         "mapped function is lifted: the default end (the next map entry, "
                         "capped at 512 bytes) is the right rule for a gap and the wrong one "
                         "for a window, whose end is the containing function's own end or the "
                         "last instruction the window needs. `START:END+ret` appends one "
                         "0xC3 byte at END instead of lifting the instruction there, for a "
                         "window every path of which leaves by jumping to a single address: "
                         "without it remill emits `__remill_missing_block` at that address and "
                         "every row traps with status 1 there. No image byte is altered -- the "
                         "appended byte is past the lifted range, and the lift log records "
                         "`synthetic_ret`.")
    args = ap.parse_args()

    LIFT.mkdir(parents=True, exist_ok=True)
    js = OUT / "lift.json"
    # `--force` re-lifts; it does not discard the log. Dropping the dict here used to be
    # how "force" was implemented, and a `--force --block ...` run then rewrote lift.json
    # with one entry and lost the other 160 -- while every .ll on disk and the linked
    # driver stayed correct, so nothing failed until the next reader of the log. The two
    # code paths below already consult `args.force` before skipping a lifted entry.
    log = json.loads(js.read_text()) if js.exists() else {"functions": {}}

    fmap = function_map()
    seeds = roots()
    # 0x0048B9F0 is already lifted but its five Ninja callees are not; seed it too so
    # the closure names them (the one blocker the corpus receipt measured).
    seeds = sorted(set(seeds) | {0x0048B9F0})
    depth = closure(seeds, fmap)
    img = memory_image()

    if args.block:
        starts = sorted(fmap)
        import bisect as _b
        spec = []
        for x in args.block:
            start, _, stop = x.partition(":")
            ret = stop.endswith("+ret")
            stop = stop[:-4] if ret else stop
            spec.append((int(start, 16), int(stop, 16) if stop else None, ret))
        for a, given_end, synthetic_ret in sorted(spec):
            key = f"0x{a:08x}"
            if not args.force and log["functions"].get(key, {}).get("lifted"):
                continue
            if given_end is not None:
                end = given_end
                kind = ("window: an address inside a mapped function, lifted from there to an "
                        "explicit end so the window's own instructions are all present")
            else:
                i = _b.bisect_right(starts, a)
                end = starts[i] if i < len(starts) else a + 256
                end = min(end, a + 512)
                kind = ("orphan block: the function map has no entry covering this address; "
                        "it is a jump target inside a gap, lifted from there to the next "
                        "mapped entry")
            raw = img[a - 0x400000:end - 0x400000]
            if synthetic_ret:
                raw += b"\xc3"
                kind = ("window + synthetic ret at the exit address: every path out of the "
                        "window jumps to that one address and the block there is not lifted, "
                        "so remill emits `__remill_missing_block` and every row traps with "
                        "status 1; the image bytes are unchanged and one 0xC3 is appended "
                        "past them")
            e = lift_one(a, raw)
            # An address the map *does* cover, lifted as a block, is the map under-reporting
            # a function's size rather than an orphan: 0x008287C0 is recorded as 28 bytes,
            # which stops before the `je 008287F6h` target that is the same routine's x87
            # body. Keep the map's own name and region so the log does not call it unmapped.
            fn = fmap.get(a)
            e["name"] = fn["name"] if fn else f"block_{a:08x}"
            e["region"] = fn["region"] if fn else "unmapped"
            e["map_size"] = fn["size"] if fn else None
            e["kind"] = kind
            e["end"] = f"0x{end:08x}"
            e["synthetic_ret"] = synthetic_ret
            e["image_bytes"] = end - a
            log["functions"][key] = e
            print(f"  block {a:#010x}..{end:#010x} lifted={e['lifted']}")
        log["lifted"] = sum(1 for x in log["functions"].values() if x.get("lifted"))
        js.write_text(json.dumps(log, indent=1, sort_keys=True) + "\n")
        return

    if args.to_next_entry:
        starts = sorted(fmap)
        import bisect as _b
        for a in sorted(int(x, 16) for x in args.to_next_entry):
            i = _b.bisect_right(starts, a)
            end = starts[i] if i < len(starts) else a + fmap[a]["size"]
            e = lift_one(a, img[a - 0x400000:end - 0x400000])
            fn = fmap.get(a, {})
            e.update(name=fn.get("name"), region=fn.get("region"), depth=depth.get(a),
                     end=f"0x{end:08x}", widened_to_next_entry=True,
                     map_size=fn.get("size"), lifted_bytes=end - a)
            log["functions"][f"0x{a:08x}"] = e
            print(f"  {a:#010x} widened to {end:#010x} ({end - a} bytes, map says "
                  f"{fn.get('size')}) lifted={e['lifted']}")
        log["lifted"] = sum(1 for x in log["functions"].values() if x.get("lifted"))
        js.write_text(json.dumps(log, indent=1, sort_keys=True) + "\n")
        return

    targets = sorted(depth)
    if args.only:
        targets = [int(a, 16) for a in args.only]
    todo = [va for va in targets
            if args.force or not log["functions"].get(f"0x{va:08x}", {}).get("lifted")]

    print(f"roots {len(seeds)}  closure {len(depth)}  to lift {len(todo)}")
    blobs = {}
    for va in todo:
        fn = fmap.get(va)
        if not fn:
            log["functions"][f"0x{va:08x}"] = {"va": f"0x{va:08x}", "lifted": False,
                                               "error_head": "address is not an entry in the function map"}
            continue
        off = va - 0x400000
        blobs[va] = img[off:off + fn["size"]]

    with cf.ThreadPoolExecutor(max_workers=args.jobs) as ex:
        futs = {ex.submit(lift_one, va, blobs[va]): va for va in blobs}
        for fut in cf.as_completed(futs):
            va = futs[fut]
            e = fut.result()
            fn = fmap[va]
            e["name"] = fn["name"]
            e["region"] = fn["region"]
            e["depth"] = depth.get(va)
            log["functions"][f"0x{va:08x}"] = e
            print(f"  {va:#010x} {fn['name'][:28]:<28} {e['bytes']:>6}B lifted={e['lifted']} {e['seconds']}s")
            js.parent.mkdir(parents=True, exist_ok=True)
            js.write_text(json.dumps(log, indent=1, sort_keys=True) + "\n")

    log["roots"] = [f"0x{a:08x}" for a in seeds]
    log["closure"] = {f"0x{a:08x}": d for a, d in sorted(depth.items())}
    log["lifted"] = sum(1 for e in log["functions"].values() if e.get("lifted"))
    log["total"] = len(depth)
    js.write_text(json.dumps(log, indent=1, sort_keys=True) + "\n")
    print(f"lifted {log['lifted']}/{log['total']}")


if __name__ == "__main__":
    main()
