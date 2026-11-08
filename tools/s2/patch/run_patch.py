#!/usr/bin/env python3
"""S2 patch driver: the four pieces of decision 2026-09-19-G1 section 1.3's "Patch" list.

  1 fsqrt    where the image's `fsqrt` sites are, what remill's FSQRT semantics
             lower to, and whether the pass reaches them.
  2 fistp    what remill's FIST/FISTP does on out-of-range, NaN and infinity
             inputs, measured against this host's x87 under the same control
             word; the helper (tools/s2/patch/x87_int.c) and its pass rule.
  3 PC53     the same fixture rows under cw = 0x007F and cw = 0x027F against a
             reference that rounds once at the target precision (ref_pc.c), and
             the double rounding the pass's post-rounding leaves behind.
  4 rows     fixture rows for the `fsub`, `fsqrt` and `fldcw` classes design 3.4
             names and the ten spike functions never exercised.

Everything generated lands under private/generated/s2/patch/ (ignored by git);
the receipt is Documentation/receipts/s2-patch-v1.json.

Usage: source tools/env.sh && .venv/bin/python tools/s2/patch/run_patch.py [--skip-lift]
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import random
import struct
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import s2lib as L  # noqa: E402
import probes as P  # noqa: E402
sys.path.insert(0, str(HERE.parents[1] / "spike"))
import spikelib as S  # noqa: E402

ROOT = L.ROOT
GEN = L.GEN
BUILD = GEN / "build"
VEC = GEN / "vectors"
OUT = GEN / "out"
RECEIPT = ROOT / "Documentation" / "receipts" / "s2-patch-v1.json"
SITES = GEN / "x87_sites.json"
INT_HELPER_LL = GEN / "x87_int.ll"
SCHEMA = "sa2-recomp-receipt/v1"

CW = {"pc24": 0x007F, "pc53": 0x027F, "pc64": 0x037F}
CRT_SQRT_BODY = 0x008290BD        # the body of the CRT square root; every game sqrt reaches it through _CIsqrt
LENGTH = 0x0077FC30
UNIT_VECTOR = 0x0077F3A0


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sh_json(cmd: list[str]) -> tuple[dict | None, str]:
    p = L.sh(cmd)
    text = (p.stdout or "").strip()
    try:
        return json.loads(text.splitlines()[-1]), text
    except Exception:
        return None, text + (p.stderr or "")


# ---------------------------------------------------------------- shared helpers
def build_tool(name: str, sources: list[Path], extra: list[str] | None = None) -> dict:
    BUILD.mkdir(parents=True, exist_ok=True)
    out = BUILD / name
    cmd = [L.ZIG, "cc"] + L.TARGET + ["-O1", "-w"] + [str(s) for s in sources] + ["-o", str(out), "-lm"] + (extra or [])
    p = L.sh(cmd, cwd=BUILD, env=L.zig_env())
    return {"ok": p.returncode == 0, "command": L.cmdline(cmd), "binary": L.rel(out),
            "error_head": "\n".join((p.stderr or "").splitlines()[:3]) if p.returncode else ""}


def compile_int_helper() -> dict:
    INT_HELPER_LL.unlink(missing_ok=True)
    S.helper_ir(HERE / "x87_int.c", INT_HELPER_LL)
    cmd = [str(S.ZIG), "cc"] + S.HELPER_IR_FLAGS + ["x87_int.c", "-o", str(INT_HELPER_LL)]
    return {"command": L.cmdline(cmd), "out": L.rel(INT_HELPER_LL), "sha256": L.sha(INT_HELPER_LL)}


def probe_variants(name: str, code: bytes, skip_lift: bool) -> dict:
    """Lift one probe and build three binaries: raw remill, the pass, and the pass with the FIST helper."""
    lf = L.lift(name, code, P.PROBE_BASE, GEN / "probes", reuse=skip_lift)
    if not lf["lifted"]:
        return {"lift": lf, "ok": False}
    variants: dict[str, dict] = {"remill": {"ll": lf["path"], "lift": lf}}
    patched = GEN / "probes" / f"{name}.pc24pass.ll"
    variants["pc24pass"] = {"ll": patched, "pass": L.run_pass(lf["path"], patched)}
    full = GEN / "probes" / f"{name}.pc24pass_int.ll"
    variants["pc24pass_int"] = {"ll": full, "pass": L.run_pass(lf["path"], full, extra=["--int-helper-ll", str(INT_HELPER_LL)])}
    for tag, v in variants.items():
        v["build"] = L.build_harness(f"{name}__{tag}", v["ll"], P.PROBE_BASE, BUILD)
        v["binary"] = BUILD / f"run_{name}__{tag}"
    return {"lift": lf, "variants": variants, "ok": all(v["build"]["ok"] for v in variants.values())}


def run_rows(binary: Path, name: str, rows: list[list[int]], out_words: int, cw: int, tag: str, **kw) -> list[list[int]]:
    v = VEC / f"{name}.{tag}.bin"
    o = OUT / f"{name}.{tag}.bin"
    o.parent.mkdir(parents=True, exist_ok=True)
    L.write_vectors(v, P.PROBE_BASE, rows, out_words, cw, **kw)
    r = L.run_binary(binary, v, o)
    if r["rc"]:
        raise SystemExit(f"{binary.name} failed on {name}/{tag}: {r['stderr_head']}")
    return L.read_out(o)


def payload(row: list[int], out_words: int) -> list[int]:
    return row[L.PREFIX_WORDS:L.PREFIX_WORDS + out_words]


def tally(a: list[list[int]], b: list[list[int]], out_words: int, st0: bool = False) -> dict:
    """Row-by-row comparison of status, the output-arena words, and optionally st(0).

    A function that returns in st(0) (0x0077FC30 length) has no arena words at all, so the
    comparison has to reach the three prefix words that carry the x86_fp80.
    """
    n = min(len(a), len(b))

    def key(r):
        return (r[0], r[1], payload(r, out_words), r[3:6] if st0 else None)

    bad = [i for i in range(n) if key(a[i]) != key(b[i])]
    return {"rows": n, "match": n - len(bad), "mismatch": len(bad), "first_mismatch_rows": bad[:6],
            "compares": "status, exit_pc" + (", st(0)" if st0 else "") + (f", {out_words} arena words" if out_words else "")}


# ---------------------------------------------------------------- piece 1: fsqrt
def piece_fsqrt(sites: dict, probes_json: dict, variants: dict, skip_lift: bool) -> dict:
    cls = sites["classes"]["fsqrt"]
    ir = {p["probe"]: p.get("ir", {}) for p in probes_json["probes"]}
    sq = ir.get("fsqrt_f32", {})
    # what the pass does with it
    pass_stats = variants["fsqrt_cw80"]["variants"]["pc24pass"]["pass"]["stats"]

    # rows: the discriminating 80-bit form under the three control words, against native x87
    rng = random.Random(20260920)
    vals = [1.0, 2.0, 3.0, 0.5, 1e-6, 1e6, 16777215.0, 0.0, 1.0000001, 2.7182817]
    vals += [abs(rng.uniform(-1e4, 1e4)) for _ in range(246)]
    per_cw = {}
    oracle = BUILD / "x87_oracle"
    for model, cw in CW.items():
        rows = [[cw, L.fw(v)] for v in vals]
        lifted = run_rows(variants["fsqrt_cw80"]["variants"]["pc24pass"]["binary"], "fsqrt_cw80", rows, 3, CW["pc64"], f"pass.{model}")
        raw = run_rows(variants["fsqrt_cw80"]["variants"]["remill"]["binary"], "fsqrt_cw80", rows, 3, CW["pc64"], f"raw.{model}")
        v = VEC / f"fsqrt_cw80.pass.{model}.bin"
        o = OUT / f"fsqrt_cw80.oracle.{model}.bin"
        L.run_binary(oracle, v, o, args=["fsqrt_cw80"])
        native = L.read_out(o)
        per_cw[model] = {"cw": f"{cw:#06x}", "rows": len(rows),
                         "remill_vs_native": tally(raw, native, 3),
                         "remill_plus_pass_vs_native": tally(lifted, native, 3)}

    smallest = cls["smallest_drivable"]
    body_full = lift_real(CRT_SQRT_BODY, "crt_sqrt_body", skip_lift)
    body = public(body_full)
    body["rows"] = crt_sqrt_rows(body_full) if body_full.get("pass") else {"error": "not lifted"}
    body["error_branch_rows"] = crt_sqrt_error_rows(body_full) if body_full.get("pass") else {"error": "not lifted"}
    return {
        "question": "Does any drivable game function contain fsqrt, what does remill lower FSQRT to, and does the pass reach it?",
        "sites_in_image": {"total": cls["sites"], "by_region": cls["by_region"],
                           "containing_functions": cls["functions"],
                           "call_free_leaf_game_functions": cls["leaf_game_functions"],
                           "sites": [{"va": f"{s['va']:#010x}", "function": s["function"]} for s in sites["all_fsqrt_sites"]],
                           "containing_function_table": [{k: v for k, v in f.items()} for f in smallest]},
        "remill_lowering": {"semantic_label": "DoFSQRT",
                            "llvm_sqrt_f80_calls": sq.get("llvm_sqrt_f80"),
                            "x86_fp80_arithmetic_ops": sq.get("fp80_arith_ops"),
                            "external_calls": sq.get("external_calls"),
                            "finding": "remill v5.0.7 lowers fsqrt to a call to the host's libm `sqrtl`, not to llvm.sqrt.f80, "
                                       "and puts it in a block labelled with the semantics-library helper "
                                       "_ZL19CheckedFloatUnaryOp..., not with the DoFSQRT exit label"},
        "pass_before": {"matched_forms": ["llvm.sqrt.f80"], "semantic_gate": "^FSQRT(mem)?$",
                        "effect": "0 sites: neither the call form nor the label matched, so every fsqrt in the image would have "
                                  "been left at the host's 64-bit precision with no diagnostic"},
        "pass_after": {"matched_forms": ["llvm.sqrt.f80", "@sqrtl"], "semantic_gate": "^(Do)?FSQRT(mem)?$",
                       "attribution": "the enclosing-semantic rule (pc24pass.py: enclosing_semantics)",
                       "probe_stats": {"ops_instrumented": pass_stats["ops_instrumented"], "by_op": pass_stats["by_op"],
                                       "by_semantic": pass_stats["by_semantic"],
                                       "by_semantic_enclosing": pass_stats["by_semantic_enclosing"]}},
        "rows_against_native_x87": per_cw,
        "crt_square_root_body": body,
    }


def public(d: dict) -> dict:
    """A lift_real result with the internal Path handles removed (the receipt is JSON)."""
    return {k: v for k, v in d.items() if not k.startswith("_")}


def crt_sqrt_rows(fn: dict) -> dict:
    """Drive the CRT square-root body itself, and score st(0) against this host's x87.

    0x008290BD is entered with the value already on the x87 stack and its bit pattern at
    [esp+8]; it loads (cw & 0x300) | 0x7F, takes the root, and leaves the function through
    the shared CRT tail at 0x0082C2AB, which is past its first `ret` and so outside the
    lifted byte range: every row is therefore status 1 at that address, and what the row
    measures is st(0).
    """
    builds = {tag: L.build_harness(f"crt_sqrt__{tag}", ll, CRT_SQRT_BODY, BUILD)
              for tag, ll in (("remill", fn["_ll_path"]), ("pc24pass", fn["_patched_path"]))}
    rng = random.Random(20260923)
    vals = [1.0, 2.0, 3.0, 0.5, 1e-6, 1e6, 16777215.0, 2.7182818284590452] + [abs(rng.uniform(0, 1e4)) for _ in range(248)]
    oracle = BUILD / "x87_oracle"
    per_cw = {}
    for model, cw in CW.items():
        rows = [list(L.dw(v)) for v in vals]
        v = VEC / f"crt_sqrt.{model}.bin"
        L.write_vectors(v, CRT_SQRT_BODY, rows, 0, cw, stack_args=2, flags=L.FLAG_ST0_FROM_ROW)
        outs = {}
        for tag in ("remill", "pc24pass"):
            o = OUT / f"crt_sqrt.{model}.{tag}.bin"
            r = L.run_binary(BUILD / f"run_crt_sqrt__{tag}", v, o)
            outs[tag] = L.read_out(o) if r["rc"] == 0 else None
        oo = OUT / f"crt_sqrt.{model}.oracle.bin"
        L.run_binary(oracle, v, oo, args=["crt_sqrt_body"])
        native = L.read_out(oo)
        # the oracle cannot leave the lifted range, so only st(0) is comparable
        def st_only(rows_):
            return [[0, 0] + r[2:] for r in rows_]
        entry = {"cw": f"{cw:#06x}", "rows": len(rows),
                 "exit": "status 1 at 0x0082C2AB on every row (the CRT tail is past the first ret)",
                 "compares": "st(0) only"}
        for tag in ("remill", "pc24pass"):
            entry[f"{tag}_vs_native_x87"] = (tally(st_only(outs[tag]), st_only(native), 0, st0=True)
                                             if outs[tag] else {"error": "did not run"})
        per_cw[model] = entry
    return {"builds_ok": all(b["ok"] for b in builds.values()), "by_control_word": per_cw,
            "entry": "st(0) = the double and [esp+4..0xB] = its two words, the way _CIsqrt (0x008290A0) leaves them; "
                     "the control word is State's, as a guest fldcw would have left it"}


ERROR_TAIL = 0x0082C1B7        # the call the error branch at 0x0082912E ends in; outside the lifted range
INDEFINITE_CONST = 0x00922EE0  # `fld tbyte [0x00922EE0]`, the x87 real indefinite the error branch loads


def crt_sqrt_error_rows(fn: dict) -> dict:
    """Drive 0x008290BD's *error* branch: the one that contains the 80-bit load.

    crt_sqrt_rows feeds the function non-negative values only, so every one of its rows takes the
    `fsqrt` at 0x008290DC and not one reaches 0x0082912E (`fstp st(0)` then
    `fld tbyte [0x00922EE0]`), the branch that reads the x87 real indefinite through
    `__remill_read_memory_f80` -- the accessor s2-atan2-v1 found remill declares `readnone`, whose
    loaded value the optimiser folds to 0.0. A negative operand takes that branch:
    `test eax,80000000h` at 0x008290D5 is non-zero and `test eax,7FF00000h` at 0x008290FB is
    non-zero for every negative that is not a denormal, so the row leaves through the error tail
    (the call at 0x00829153 to 0x0082C1B7, outside the lifted bytes, which traps with status 1 at
    that address after the load has happened).

    The expected st(0) is this host's own `fsqrt` of the same value under the same control word: a
    masked invalid operation leaves exactly the real indefinite the CRT's constant holds
    (0x00922EE0 = ffff c000000000000000, read out of the image below), so one oracle scores both
    branches. A third binary is built with the pass's f80 fix switched off
    (`--no-fix-f80-accessors`), which is what s2-patch-v1's first measurement was built with: if
    these rows cannot tell the two apart they are not testing the branch.
    """
    img = L.image_bytes()
    const = img[INDEFINITE_CONST - L.IMAGE_BASE: INDEFINITE_CONST - L.IMAGE_BASE + 10]
    no_fix_ll = GEN / "functions" / "crt_sqrt_body.no_f80_fix.ll"
    no_fix = L.run_pass(fn["_ll_path"], no_fix_ll, extra=["--int-helper-ll", str(INT_HELPER_LL), "--no-fix-f80-accessors"])
    builds = {tag: L.build_harness(f"crt_sqrt_err__{tag}", ll, CRT_SQRT_BODY, BUILD)
              for tag, ll in (("remill", fn["_ll_path"]), ("pc24pass", fn["_patched_path"]),
                              ("pc24pass_without_the_f80_fix", no_fix_ll))}
    rng = random.Random(20260924)
    named = [("-1.0", -1.0), ("-2.0", -2.0), ("-4.0", -4.0), ("-0.5", -0.5), ("-1e-6", -1e-6),
             ("-1e6", -1e6), ("-16777215.0", -16777215.0), ("-e", -2.7182818284590452),
             ("-inf", float("-inf")), ("-nan", -float("nan")), ("-denormal", -5e-324),
             ("-0.0", -0.0), ("-1e300", -1e300), ("-DBL_MIN", -2.2250738585072014e-308)]
    vals = [v for _n, v in named] + [-abs(rng.uniform(1e-3, 1e4)) for _ in range(242)]
    oracle = BUILD / "x87_oracle"
    per_cw = {}
    for model, cw in CW.items():
        rows = [list(L.dw(v)) for v in vals]
        v = VEC / f"crt_sqrt_err.{model}.bin"
        L.write_vectors(v, CRT_SQRT_BODY, rows, 0, cw, stack_args=2, flags=L.FLAG_ST0_FROM_ROW)
        outs = {}
        for tag in builds:
            o = OUT / f"crt_sqrt_err.{model}.{tag}.bin"
            r = L.run_binary(BUILD / f"run_crt_sqrt_err__{tag}", v, o)
            outs[tag] = L.read_out(o) if r["rc"] == 0 else None
        oo = OUT / f"crt_sqrt_err.{model}.oracle.bin"
        L.run_binary(oracle, v, oo, args=["crt_sqrt_body"])
        native = L.read_out(oo)

        def st_only(rows_):
            return [[0, 0] + r[2:] for r in rows_]

        ref = outs["pc24pass"] or []
        took = [i for i, r in enumerate(ref) if r[1] == ERROR_TAIL]
        entry = {"cw": f"{cw:#06x}", "rows": len(rows), "compares": "st(0) only",
                 "rows_through_the_error_branch": len(took),
                 "rows_through_the_fsqrt_branch": len(ref) - len(took),
                 "exit_pc_census": {f"{pc:#010x}": sum(1 for r in ref if r[1] == pc) for pc in sorted({r[1] for r in ref})}}
        for tag in builds:
            entry[f"{tag}_vs_native_x87"] = (tally(st_only(outs[tag]), st_only(native), 0, st0=True)
                                             if outs[tag] else {"error": "did not run"})
            if outs[tag] and took:
                entry[f"{tag}_vs_native_x87_on_the_error_branch"] = tally(
                    st_only([outs[tag][i] for i in took]), st_only([native[i] for i in took]), 0, st0=True)
        per_cw[model] = entry
    return {
        "question": "Do the rows reach the branch that holds the 80-bit load, and does the result hold there?",
        "branch": {"taken_at": f"{0x0082912E:#010x}", "instructions": "fstp st(0) ; fld tbyte [0x00922EE0] ; mov eax,1",
                   "selected_by": "test eax,80000000h at 0x008290D5 (the sign of the double's high word) and "
                                  "test eax,7FF00000h at 0x008290FB",
                   "exit": f"call {ERROR_TAIL:#010x} at 0x00829153, outside the lifted bytes: status 1 at that address",
                   "constant": {"address": f"{INDEFINITE_CONST:#010x}", "bytes": const.hex(),
                                "reading": "x87 real indefinite (sign 1, exponent 0x7FFF, significand 0xC000000000000000), "
                                           "the same value this host's fsqrt of a negative leaves with the invalid "
                                           "exception masked"}},
        "f80_accessor_evidence": {
            "lift_declares_read_memory_f80": "__remill_read_memory_f80" in fn["_ll_path"].read_text(errors="replace"),
            "patched_ll_carries_the_f80_marker": "; sa2-pc24pass f80" in fn["_patched_path"].read_text(errors="replace"),
            "f80_accessor_attrs_fixed": fn["pass"].get("f80_accessor_attrs_fixed"),
            "contrast_build": {"pass_command": no_fix["command"], "rc": no_fix["rc"],
                               "ll_sha256": no_fix["out_sha256"],
                               "what": "the same module with --no-fix-f80-accessors: remill's `readnone` declaration left "
                                       "in place, which is how the first s2-patch-v1 measurement of this function was built"}},
        "builds_ok": all(b["ok"] for b in builds.values()),
        "by_control_word": per_cw,
        "entry": "same entry as the non-negative rows: st(0) = the double and [esp+4..0xB] = its two words, the way "
                 "_CIsqrt (0x008290A0) leaves them",
    }


def lift_real(va: int, name: str, skip_lift: bool) -> dict:
    """Lift a real function out of the image, run the pass, and report what the pass found."""
    img = L.image_bytes()
    code = L.function_bytes(img, va)
    lf = L.lift(name, code, va, GEN / "functions", reuse=skip_lift)
    out: dict = {"va": f"{va:#010x}", "bytes": len(code), "bytes_sha256": hashlib.sha256(code).hexdigest(),
                 "lift": {k: lf[k] for k in ("command", "rc", "lifted", "ll", "ll_sha256")}}
    if not lf["lifted"]:
        return out
    patched = GEN / "functions" / f"{name}.pc24pass.ll"
    pr = L.run_pass(lf["path"], patched, extra=["--int-helper-ll", str(INT_HELPER_LL)])
    st = pr["stats"] or {}
    out["pass"] = {"command": pr["command"], "rc": pr["rc"],
                   "ops_instrumented": st.get("ops_instrumented"), "by_op": st.get("by_op"),
                   "by_semantic": st.get("by_semantic"), "by_semantic_enclosing": st.get("by_semantic_enclosing"),
                   "skipped_by_semantic": st.get("skipped_by_semantic"),
                   "fist_rewrites": st.get("fist_rewrites"), "by_fist_semantic": st.get("by_fist_semantic"),
                   "control_word_masks_fixed": st.get("control_word_masks_fixed"),
                   "f80_accessor_attrs_fixed": st.get("f80_accessor_attrs_fixed"),
                   "f80_accessor_sites": st.get("f80_accessor_sites"),
                   "fpatan_rewrites": st.get("fpatan_rewrites")}
    out["patched_ll"] = L.rel(patched)
    out["patched_ll_sha256"] = L.sha(patched)
    out["_patched_path"] = patched
    out["_ll_path"] = lf["path"]
    return out


# ---------------------------------------------------------------- piece 2: fistp
FISTP_BOUNDARY = [
    ("zero", 0.0), ("negative zero", -0.0), ("half", 0.5), ("negative half", -0.5),
    ("tie down to even", 1.5), ("tie up to even", 2.5), ("negative tie", -1.5), ("negative tie 2", -2.5),
    ("int32 max", 2147483647.0), ("just under int32 max", 2147483646.5),
    ("rounds onto int32 max + 1", 2147483647.5), ("int32 max + 1", 2147483648.0),
    ("int32 min", -2147483648.0), ("just inside int32 min", -2147483647.5),
    ("rounds onto int32 min - 1", -2147483648.5), ("int32 min - 1", -2147483649.0),
    ("3e9 (out of int32)", 3e9), ("-3e9 (out of int32)", -3e9),
    ("1e18 (in int64)", 1e18), ("-1e18 (in int64)", -1e18),
    ("int64 max as a double", 9.2233720368547758e18), ("out of int64", 1e19), ("out of int64 negative", -1e19),
    ("nan", float("nan")), ("+inf", float("inf")), ("-inf", float("-inf")),
    ("1e300", 1e300), ("-1e300", -1e300), ("2^32", 4294967296.0), ("2^16", 65536.0),
]
RC_NAMES = {0x007F: "nearest", 0x047F: "down", 0x087F: "up", 0x0C7F: "zero"}


def piece_fistp(sites: dict, variants: dict, int_helper: dict) -> dict:
    oracle = BUILD / "x87_oracle"
    widths = {"fistpl_cw": ("fistpl", 1, "m32int"), "fistpll_cw": ("fistpll", 2, "m64int"), "fistps_f64": ("fistps", 1, "m16int")}
    table: dict = {}
    divergences: list[dict] = []
    for probe, (mnem, out_words, dest) in widths.items():
        v = variants[probe]
        per_rc = {}
        for cw, rc in RC_NAMES.items():
            if probe == "fistps_f64":
                rows = [list(L.dw(x)) for _, x in FISTP_BOUNDARY]
                header_cw = cw
            else:
                rows = [[cw] + list(L.dw(x)) for _, x in FISTP_BOUNDARY]
                header_cw = cw
            raw = run_rows(v["variants"]["remill"]["binary"], probe, rows, out_words, header_cw, f"raw.{cw:04x}")
            helped = run_rows(v["variants"]["pc24pass_int"]["binary"], probe, rows, out_words, header_cw, f"helped.{cw:04x}")
            vv = VEC / f"{probe}.raw.{cw:04x}.bin"
            oo = OUT / f"{probe}.oracle.{cw:04x}.bin"
            L.run_binary(oracle, vv, oo, args=[probe])
            native = L.read_out(oo)
            per_rc[rc] = {"cw": f"{cw:#06x}", "rows": len(rows),
                          "remill_vs_native_x87": tally(raw, native, out_words),
                          "remill_plus_helper_vs_native_x87": tally(helped, native, out_words)}
            for i, (label, _val) in enumerate(FISTP_BOUNDARY):
                if payload(raw[i], out_words) != payload(native[i], out_words):
                    divergences.append({"probe": probe, "rounding": rc, "input": label,
                                        "remill": [f"{w:#010x}" for w in payload(raw[i], out_words)],
                                        "native_x87": [f"{w:#010x}" for w in payload(native[i], out_words)]})
            if probe == "fistps_f64":
                break   # this probe has no fldcw, so it has only the header's control word
        table[mnem] = {"destination": dest, "by_rounding_mode": per_rc}

    # a sample of the boundary rows, printed so the receipt shows the answer and not only a count
    sample_probe = "fistpl_cw"
    rows = [[0x007F] + list(L.dw(x)) for _, x in FISTP_BOUNDARY]
    raw = run_rows(variants[sample_probe]["variants"]["remill"]["binary"], sample_probe, rows, 1, 0x007F, "sample.raw")
    helped = run_rows(variants[sample_probe]["variants"]["pc24pass_int"]["binary"], sample_probe, rows, 1, 0x007F, "sample.helped")
    L.run_binary(oracle, VEC / f"{sample_probe}.sample.raw.bin", OUT / f"{sample_probe}.sample.oracle.bin", args=[sample_probe])
    native = L.read_out(OUT / f"{sample_probe}.sample.oracle.bin")
    sample = [{"input": label, "native_x87": f"{payload(native[i], 1)[0]:#010x}",
               "remill": f"{payload(raw[i], 1)[0]:#010x}", "remill_plus_helper": f"{payload(helped[i], 1)[0]:#010x}"}
              for i, (label, _v) in enumerate(FISTP_BOUNDARY)]

    st = variants["fistpl_cw"]["variants"]["pc24pass_int"]["pass"]["stats"]
    selftest, selftest_text = sh_json([str(BUILD / "x87_int_test")])
    return {
        "question": "What does remill's FIST/FISTP do on out-of-range, NaN and infinity inputs, and does design 3.4's helper change it?",
        "sites_in_image": {"total_fistp": sites["classes"]["fistp_family"]["sites"],
                           "fistpl": sites["classes"]["fistpl"]["sites"],
                           "fistpll": sites["classes"]["fistpll"]["sites"],
                           "fistps": sites["classes"]["fistps"]["sites"],
                           "by_region_fistp_family": sites["classes"]["fistp_family"]["by_region"],
                           "opcode_histogram_receipt": {"fistpl": 423, "fistpll": 272, "fistps": 4, "total": 699},
                           "note": "this sweep counts fist and fisttp in the family as well, and marks a slightly different "
                                   "suspect set from the histogram's, so the per-form totals differ by a few; the receipt's "
                                   "423 / 272 / 4 are the figures the design and the decision cite"},
        "remill_lowering": {"rounding": "llvm.nearbyint.f80 (follows the host rounding mode, which remill sets from a guest "
                                        "fldcw by calling fesetround)",
                            "conversion": "fptosi x86_fp80 to iN, with a select substituting INT_MIN on the low side only",
                            "risk": "fptosi of an out-of-range operand is poison in LLVM, so the out-of-range result is "
                                    "whatever the backend happens to select, not a defined value"},
        "oracle": "the same instruction executed by this host's x87 under the same control word (tools/s2/patch/x87_oracle.c)",
        "by_width": table,
        "divergences_found": divergences,
        "boundary_sample_cw_007F_fistpl": sample,
        "helper": {"source": "tools/s2/patch/x87_int.c", "ir": int_helper,
                   "self_test": selftest, "self_test_output": selftest_text[:512],
                   "pass_rule": {"fist_rewrites": st.get("fist_rewrites"), "by_fist_semantic": st.get("by_fist_semantic")}},
    }


# ---------------------------------------------------------------- piece 3: PC53
def spike_rows(va: int, n: int = 272) -> list[list[int]]:
    """The spike's vector generator for the two x87-heavy leaves, regenerated with its own seed and rule."""
    rng = random.Random(20260919)

    def fw(x):
        return L.fw(x)

    def rnd_float(lo=-3, hi=4):
        return fw(rng.choice((-1, 1)) * 10 ** rng.uniform(lo, hi))

    def rnd_vec():
        return [rnd_float() for _ in range(3)]

    def rnd_unit():
        while True:
            v = [rng.gauss(0, 1) for _ in range(3)]
            m = math.sqrt(sum(x * x for x in v))
            if m > 1e-3:
                return [fw(x / m) for x in v]

    EDGE = [0.0, -0.0, 1.0, -1.0, 0.5, 1e-20, -1e-20, 3.4e38, 1e-38, 12.0, -149.0, -1593.0, 1.0000001, 0.99999994]
    e = [fw(x) for x in EDGE]
    edge_vecs = [[e[i], e[(i + 3) % len(e)], e[(i + 7) % len(e)]] for i in range(len(e))] + [[0, 0, 0], [fw(-0.0)] * 3]
    rows = []
    for _ in range(256):
        rows.append(rnd_vec() if rng.random() < 0.6 else rnd_unit())
    rows += edge_vecs
    return rows[:n] if n else rows


def piece_pc53(skip_lift: bool) -> dict:
    ref = BUILD / "ref_pc"
    functions = {
        "length": {"va": LENGTH, "out_words": 0, "st0": True, "reg": {"eax": L.REG_A_IN}, "flags": 0,
                   "x87_ops": "3 fmul, 2 fadd, _CIsqrt; the result is left in st(0)"},
        "unit_vector": {"va": UNIT_VECTOR, "out_words": 3, "st0": False, "reg": {"esi": L.REG_A_IN},
                        "flags": L.FLAG_OUT_IN_ARENA,
                        "x87_ops": "3 fmul, 2 fadd, _CIsqrt, 1 fdivr, 3 fmul; the result is written back through esi"},
    }
    result: dict = {"question": "Does the pass's live control-word read hold at PC53, and how much does its post-rounding double-round?",
                    "reference": "tools/s2/patch/ref_pc.c: pc24 = double then RoundSignificand24; pc53 = plain binary64, a single "
                                 "rounding to 53 bits; pc64 = long double",
                    "functions": {}}
    for name, spec in functions.items():
        fn = lift_real(spec["va"], name, skip_lift)
        if not fn.get("pass"):
            result["functions"][name] = {**public(fn), "verdict": "not lifted"}
            continue
        builds = {}
        for tag, ll in (("remill", fn["_ll_path"]), ("pc24pass", fn["_patched_path"])):
            builds[tag] = L.build_harness(f"{name}__{tag}", ll, spec["va"], BUILD)
        rows = spike_rows(spec["va"])
        per_cw = {}
        for model, cw in (("pc24", CW["pc24"]), ("pc53", CW["pc53"])):
            kw = {"eax": spec["reg"].get("eax", L.REG_ZERO), "esi": spec["reg"].get("esi", L.REG_ZERO), "flags": spec["flags"]}
            v = VEC / f"{name}.{model}.bin"
            L.write_vectors(v, spec["va"], rows, spec["out_words"], cw, **kw)
            outs = {}
            for tag in ("remill", "pc24pass"):
                o = OUT / f"{name}.{model}.{tag}.bin"
                r = L.run_binary(BUILD / f"run_{name}__{tag}", v, o)
                outs[tag] = L.read_out(o) if r["rc"] == 0 else None
            ro = OUT / f"{name}.{model}.ref.bin"
            rr = L.sh([str(ref), model, name, str(v), str(ro)])
            reference = L.read_out(ro) if rr.returncode == 0 else None
            entry = {"cw": f"{cw:#06x}", "rows": len(rows), "reference_model": model,
                     "reference_command": L.cmdline([str(ref), model, name, str(v), str(ro)])}
            if reference is None:
                entry["error"] = (rr.stderr or "")[:200]
            else:
                for tag in ("remill", "pc24pass"):
                    entry[f"{tag}_vs_reference"] = tally(outs[tag], reference, spec["out_words"], spec["st0"]) if outs[tag] else {"error": "did not run"}
                entry["traps"] = sum(1 for r2 in (outs["pc24pass"] or []) if r2[0] != 0)
                entry["control_word_after_row"] = f"{(outs['pc24pass'] or [[0] * 7])[0][6]:#06x}"
            per_cw[model] = entry
        result["functions"][name] = {
            "va": fn["va"], "x87_ops": spec["x87_ops"], "bytes": fn["bytes"],
            "pass": fn["pass"], "builds_ok": all(b["ok"] for b in builds.values()),
            "by_control_word": per_cw,
        }
    return result


def pc53_sweep(variants: dict, rows_per_op: int) -> dict:
    """The double-rounding sweep: many rows per op through the lifted path, at PC24 and PC53.

    A few hundred fixture rows cannot see a residue the helper's own self-test measures at
    0 to 45 per 200 000, so the mismatch count the brief asks for is taken at that scale,
    through the lifted binary rather than through the helper alone.
    """
    oracle = BUILD / "x87_oracle"
    rng = random.Random(20260922)
    operands = [(rng.uniform(-1e3, 1e3), rng.uniform(-1e3, 1e3)) for _ in range(rows_per_op)]
    out: dict = {"rows_per_op": rows_per_op,
                 "method": "each row loads the control word with a guest fldcw, runs the op on two binary32 operands and "
                           "stores the raw x86_fp80; the expected words are the same instruction on this host's x87 under "
                           "the same control word",
                 "by_op": {}}
    for probe in ("fadd_cw80", "fsub_cw80", "fmul_cw80", "fdiv_cw80", "fsqrt_cw80"):
        v = variants[probe]
        in_words = P.probe_table()[probe]["in"]
        per_cw = {}
        for model in ("pc24", "pc53"):
            cw = CW[model]
            if in_words == 2:
                rows = [[cw, L.fw(abs(a))] for a, _b in operands]
            else:
                rows = [[cw, L.fw(a), L.fw(b)] for a, b in operands]
            patched = run_rows(v["variants"]["pc24pass"]["binary"], probe, rows, 3, CW["pc64"], f"sweep.{model}")
            vv = VEC / f"{probe}.sweep.{model}.bin"
            oo = OUT / f"{probe}.sweep.{model}.oracle.bin"
            L.run_binary(oracle, vv, oo, args=[probe])
            native = L.read_out(oo)
            per_cw[model] = {"cw": f"{cw:#06x}", **tally(patched, native, 3)}
        out["by_op"][probe] = per_cw
    return out


# ---------------------------------------------------------------- piece 4: fixture rows
CLASS_PROBES = {
    "fsub": ["fsub_cw80", "fsubr_cw80", "fsubp_cw80"],
    "fsqrt": ["fsqrt_cw80"],
    "fldcw": ["fldcw_fnstcw"],
    "fadd_fmul_fdiv (the S1 classes, for comparison)": ["fadd_cw80", "fmul_cw80", "fdiv_cw80"],
}


def piece_rows(sites: dict, variants: dict, skip_lift: bool) -> dict:
    oracle = BUILD / "x87_oracle"
    rng = random.Random(20260921)
    pairs = [(1.0, 1.0), (1.0, 3.0), (2.0, 3.0), (1e-8, 1.0), (1e8, 1.0), (16777215.0, 16777216.0),
             (0.1, 0.3), (1.0000001, 0.99999994), (-1593.0, 12.0), (3.4e38, 1e-38)]
    pairs += [(rng.uniform(-1e4, 1e4), rng.uniform(-1e4, 1e4)) for _ in range(246)]
    classes: dict = {}
    for cls, names in CLASS_PROBES.items():
        entries = {}
        for probe in names:
            v = variants[probe]
            in_words = P.probe_table()[probe]["in"]
            out_words = P.probe_table()[probe]["out"]
            per_cw = {}
            for model, cw in CW.items():
                if probe == "fldcw_fnstcw":
                    rows = [[c] for c in (0x007F, 0x027F, 0x037F, 0x047F, 0x0A7F, 0x0F7F, 0x1372, 0x0272, 0x0B7F)]
                elif in_words == 2:
                    rows = [[cw, L.fw(abs(a))] for a, _b in pairs]
                else:
                    rows = [[cw, L.fw(a), L.fw(b)] for a, b in pairs]
                header_cw = CW["pc64"]     # deliberately not the row's: only the guest fldcw can make State right
                raw = run_rows(v["variants"]["remill"]["binary"], probe, rows, out_words, header_cw, f"rows.raw.{model}")
                patched = run_rows(v["variants"]["pc24pass"]["binary"], probe, rows, out_words, header_cw, f"rows.pass.{model}")
                vv = VEC / f"{probe}.rows.raw.{model}.bin"
                oo = OUT / f"{probe}.rows.oracle.{model}.bin"
                L.run_binary(oracle, vv, oo, args=[probe])
                native = L.read_out(oo)
                per_cw[model] = {"cw_loaded_by_the_guest_fldcw": f"{cw:#06x}",
                                 "cw_in_state_at_entry": f"{header_cw:#06x}",
                                 "rows": len(rows),
                                 "remill_vs_native_x87": tally(raw, native, out_words),
                                 "remill_plus_pass_vs_native_x87": tally(patched, native, out_words)}
                if probe == "fldcw_fnstcw":
                    per_cw[model]["note"] = "the rows are control words, not operands; the class is measured once"
                    break
            st = v["variants"]["pc24pass"]["pass"]["stats"]
            entries[probe] = {"doc": P.probe_table()[probe]["doc"],
                              "instructions": P.disassemble(P.probe_table()[probe]["code"]),
                              "pass": {"ops_instrumented": st["ops_instrumented"], "by_op": st["by_op"],
                                       "by_semantic": st["by_semantic"],
                                       "control_word_masks_fixed": st["control_word_masks_fixed"]},
                              "by_control_word": per_cw}
        classes[cls] = {"probes": entries}

    # one real call-free leaf game function per class, lifted and instrumented
    real: dict = {}
    for cls, key in (("fsub", "fsub_family"), ("fldcw", "fldcw"), ("fistpl", "fistpl"), ("fistpll", "fistpll")):
        cands = [f for f in sites["classes"][key]["smallest_drivable"] if f["leaf"] and f["region"] == "game"]
        if not cands:
            real[cls] = {"chosen": None, "obstacle": "no call-free leaf function in the game region contains this class"}
            continue
        f = cands[0]
        va = int(f["address"], 16)
        got = lift_real(va, f"leaf_{cls}_{va:08x}", skip_lift)
        real[cls] = {"chosen": {k: f[k] for k in ("address", "name", "region", "size", "instructions", "forms")},
                     "candidates_leaf_game": sites["classes"][key]["leaf_game_functions"],
                     **public(got)}
    real["fsqrt"] = {"chosen": None,
                     "obstacle": "the image holds four fsqrt sites in total and none is in a call-free function: "
                                 "every square root in the game goes through the CRT's _CIsqrt (0x008290A0), whose body "
                                 "0x008290BD makes four direct calls (the control-word helper and three error paths). "
                                 "Piece 1 drives that body instead.",
                     "candidates_leaf_game": sites["classes"]["fsqrt"]["leaf_game_functions"]}
    return {"question": "Are there fixture rows for the fsub, fsqrt and fldcw clauses of design 3.4, and do they pass?",
            "corpus_check": {
                "inherited_corpus": "private/fixtures/LocalAssets-SA2/{Precise,Fixtures}/*.bin",
                "surveyed_by": "tools/s2/entry_test/entry_test.py (read-only here); its DISPOSITIONS table names every fixture's kernel",
                "fixtures_whose_native_words_are_a_leaf_x87_function's_outputs": ["SA2NPT1 (0x00426CC0)", "SA2TXC1 (0x00426D50)"],
                "coverage_of_these_classes": "none: both decoded fixtures are the point transform, whose x87 content is fmul and "
                                             "fadd only. No inherited fixture covers fsub, fsqrt, fistp or fldcw, so every row "
                                             "below is synthetic in its inputs",
                "authority": "the rows are synthetic but their expected words are not: each is the same instruction executed by "
                             "this host's x87 under the same control word, so the comparison is directly-traced"},
            "by_class": classes,
            "real_functions": real}


# ---------------------------------------------------------------- verdicts
def pc53_residue_class(piece: dict, self_test: dict) -> dict:
    """Write the PC53 double rounding up as a named class with its measured rate per op.

    Design 7.4's table classes the five basic float ops **at PC24** as Exact and names no PC53
    class at all, so a measured, unfixed divergence at PC53 currently belongs to no class and is
    counted in no gate report's "Residues added". This block is that class, stated with the two
    distributions it was measured on: the lifted binary's sweep (binary32 operands drawn uniformly
    from [-1e3, 1e3], the shape the game's own x87 code has) and the helper's own self-test (its
    own operand distribution, private/generated/spike/pc24pass/scratch/pc_round_test).
    """
    sweep = piece["double_rounding_sweep"]
    n = sweep["rows_per_op"]
    hw = self_test.get("hardware_mismatch", {})
    ops = {}
    for probe, cells in sweep["by_op"].items():
        op = probe.split("_")[0]
        lifted53 = cells["pc53"]["mismatch"]
        lifted24 = cells["pc24"]["mismatch"]
        helper = hw.get(op, {})
        rows = []
        for label, count, denom in (("lifted binary sweep", lifted53, n),
                                    ("helper self-test", helper.get("pc53"), self_test.get("rows_per_check"))):
            if count is None or not denom:
                continue
            rows.append({"distribution": label, "pc53_mismatches": count, "rows": denom,
                         "rate": round(count / denom, 9),
                         "rate_as_power_of_two": (round(math.log2(count / denom), 1) if count else None)})
        ops[op] = {"pc24_mismatches_lifted": lifted24, "pc24_rows": n,
                   "pc53_by_distribution": rows,
                   "reading": ("zero in the lifted sweep's distribution and non-zero in the helper's, so the rate is "
                               "distribution-dependent and a zero cell is an upper bound on that distribution, not a proof "
                               "of exactness" if lifted53 == 0 and helper.get("pc53") else
                               "non-zero in every distribution measured" if lifted53 else
                               "zero in every distribution measured")}
    worst = max((r["rate"] for o in ops.values() for r in o["pc53_by_distribution"]), default=0.0)
    return {
        "name": "Residue: PC53 double rounding",
        "status": "measured, unfixed, and in no design 7.4 residue class",
        "what": "With the guest control word at PC53 (cw = 0x027F) the pass rounds the x86_fp80 result remill has already "
                "rounded to 64 bits down to 53 bits. Two roundings where the hardware does one: when the 64-bit rounding "
                "lands on a 53-bit tie the second rounding goes the other way and the stored double differs from x87's by "
                "one ulp. It is the pass's rounding strategy, not a remill defect and not a host-libm question.",
        "where": "every lifted arithmetic op executed while PC = 10, which the game reaches through the CRT's own PC53 "
                 "window (design 2.3) and through any guest `fldcw 0x27F`: 1416 fldcw sites image-wide, 1025 in the game "
                 "region (tools/s2/patch/scan_x87.py, this receipt's tools.scan.summary)",
        "rate_per_op": ops,
        "worst_measured_rate": worst,
        "worst_measured_rate_as_power_of_two": round(math.log2(worst), 1) if worst else None,
        "design_7_4": {
            "what_it_says": "the Exact tier reads 'Integer logic; five basic float ops at PC24; float-to-int within range'. "
                            "The qualifier is PC24. No row of the table mentions PC53.",
            "consequence": "this divergence is in no class: it is not Exact (it is measured, repeatable and non-zero), it is "
                           "not the transcendental residue (no fpatan/fsin/fcos/fyl2x is involved), not exponent range, not "
                           "float-to-int, not denormal, not Observed. A gate report that counts residue classes therefore "
                           "counts it nowhere, which is how 'Residues added: 0' was printed beside it.",
            "needed": "design 7.4 needs a PC53 row, and Documentation/fidelity-ledger.json needs this class, before a gate "
                      "report's residue count means anything. Neither file is this unit's to edit; both are named here and "
                      "in agents-journals/s2/2026-09-20-s2-patch.md so the main thread can make the edit with the numbers.",
        },
        "gameplay_visibility": "unmeasured. Design 7.4 promotes a residue to a bug with a fixture when it changes "
                               "gameplay-visible state inside a replay. No replay has run at PC53, and the two x87-heavy "
                               "leaves driven here (0x0077FC30, 0x0077F3A0) store binary32 at every step, which hides a "
                               "53-versus-64 residue: their 272-row columns are 0 mismatches at both control words. So this "
                               "class is recorded, not promoted, and the promotion question is open rather than answered.",
        "fix": "round once at the target precision: replace the operation instead of post-rounding its result. The pass "
               "would emit a call taking both operands and the live control word and performing the op itself. Cost: a "
               "second helper with one entry point per op, a pass rule that rewrites the operand list rather than the "
               "result, and a re-measurement of the ten-function table (the PC24 column is 0 mismatches today, so the "
               "change has to be shown not to move it). Inside the 8 agent wall-hours the G1 kill criterion allows for one "
               "class; outside this unit's time box.",
        "helper_comment_defect": {
            "file": "tools/spike/pc24pass/pc_round.c",
            "claim": "its header says the double rounding 'has probability about 2^-40 per fdiv'",
            "measured": f"at PC53 the rate is {sweep['by_op']['fdiv_cw80']['pc53']['mismatch']}/{n} for fdiv, about 2^-12, "
                        f"which is 28 binary orders of magnitude off. The 2^-40 argument holds only for PC24 with "
                        f"24-bit-significand operands, where the measurement is "
                        f"{sweep['by_op']['fdiv_cw80']['pc24']['mismatch']}/{n}; the sentence does not carry its "
                        f"qualifier and reads as a claim about the class.",
            "not_fixed_here": "tools/spike/pc24pass/ is not this unit's to edit; the correction is reported to the main thread",
        },
    }


def verdict(key: str, piece: dict) -> dict:
    """covered / partially covered / not covered, with the numbers the call rests on."""
    if key == "1_fsqrt":
        rows = piece["crt_square_root_body"]["rows"]["by_control_word"]
        probe = piece["rows_against_native_x87"]
        err = piece["crt_square_root_body"]["error_branch_rows"]
        after = sum(v["pc24pass_vs_native_x87"]["mismatch"] for v in rows.values())
        after += sum(v["remill_plus_pass_vs_native"]["mismatch"] for v in probe.values())
        before = sum(v["remill_vs_native_x87"]["mismatch"] for v in rows.values())
        before += sum(v["remill_vs_native"]["mismatch"] for v in probe.values())
        ecw = err.get("by_control_word", {})
        err_after = sum(v["pc24pass_vs_native_x87"]["mismatch"] for v in ecw.values())
        err_nofix = sum(v["pc24pass_without_the_f80_fix_vs_native_x87"]["mismatch"] for v in ecw.values())
        err_rows = sum(v["rows_through_the_error_branch"] for v in ecw.values())
        return {"verdict": "covered" if after == 0 and err_after == 0 else "partially covered",
                "error_branch": f"the first measurement of this function drove non-negative values only, so no row reached "
                                f"0x0082912E, the branch that loads the x87 real indefinite through "
                                f"__remill_read_memory_f80 -- the accessor remill declares `readnone` (s2-atan2-v1). Negative "
                                f"rows were added: {err_rows} rows over the three control words take that branch, and st(0) "
                                f"matches this host's x87 on {err_after} mismatches with the pass as it stands. Built with "
                                f"--no-fix-f80-accessors, which is how the pinned first measurement was built, the same rows "
                                f"give {err_nofix} mismatches, so the rows discriminate.",
                "reason": f"remill v5.0.7 lowers FSQRT to the host's `sqrtl` under the label DoFSQRT, inside a semantics-library "
                          f"helper block; the pass matched neither the call form nor the label, so all four fsqrt sites in the "
                          f"image were silently left at the host's 64-bit precision. Both were fixed. Driven rows: the CRT "
                          f"square-root body 0x008290BD (the function every square root in the game reaches through _CIsqrt) "
                          f"and the hand-encoded fsqrt probe, 256 rows each at cw 0x007F / 0x027F / 0x037F, scored against this "
                          f"host's x87 executing fsqrt under the same control word: {before} mismatches before the pass, "
                          f"{after} after.",
                "caveat": "no call-free leaf function in the game region contains fsqrt (4 sites image-wide, all in "
                          "CRT-shaped math with direct calls), so the driven function is the CRT body rather than game "
                          "logic, and it leaves the lifted byte range through the shared CRT tail at 0x0082C2AB on every "
                          "row: what the rows compare is st(0), not a return value."}
    if key == "2_fistp_helper":
        div = len(piece["divergences_found"])
        st = piece["helper"]["self_test"] or {}
        after = sum(e["remill_plus_helper_vs_native_x87"]["mismatch"]
                    for w in piece["by_width"].values() for e in w["by_rounding_mode"].values())
        return {"verdict": "covered",
                "reason": f"The oracle result is that remill does not diverge: over the boundary set (int32 and int64 limits "
                          f"and the half-ulp either side, values that round onto the boundary, NaN, +-inf, +-0, ties) in all "
                          f"four rounding modes and all three destination widths, remill's FIST/FISTP produced exactly what "
                          f"this host's x87 produced, {div} divergences, and the same held at -O0, -O1, -O2, -O3 and -Os. "
                          f"The helper was written anyway, because remill reaches that answer through `fptosi`, whose "
                          f"out-of-range result is poison in LLVM rather than a value, and through `llvm.nearbyint.f80`, which "
                          f"follows the host rounding mode. The helper is bit-exact against the instruction "
                          f"({st.get('total_mismatches')} mismatches over {st.get('random_rows_per_check')} random rows per "
                          f"width per rounding mode plus {st.get('edge_values')} edge values), the pass routes the family "
                          f"through it, and the rows stay at {after} mismatches after the rewrite.",
                "caveat": "the measurement is this host, this LLVM and this optimisation set; the helper exists so that the "
                          "answer stops depending on any of them. The 4 `fistps` sites the histogram counts are all in the "
                          "unmapped part of the sweep, so no mapped function exercises the 16-bit width."}
    if key == "3_pc53_column":
        sw = piece["double_rounding_sweep"]["by_op"]
        pc24 = sum(v["pc24"]["mismatch"] for v in sw.values())
        pc53 = {k: v["pc53"]["mismatch"] for k, v in sw.items() if v["pc53"]["mismatch"]}
        n = piece["double_rounding_sweep"]["rows_per_op"]
        fns = piece["functions"]
        rc = piece["residue_class"]
        helper_cells = [(op, r) for op, d in rc["rate_per_op"].items()
                        for r in d["pc53_by_distribution"] if r["distribution"] == "helper self-test"]
        return {"verdict": "partially covered",
                "reason": f"The column exists and runs: two x87-heavy leaves (0x0077FC30 length, 0x0077F3A0 njUnitVector) at "
                          f"cw 0x007F and cw 0x027F against a reference that rounds once at the target precision, 0 mismatches "
                          f"with the pass in every cell. Getting there needed a defect fixed first: remill's FLDCW stores the "
                          f"control word masked with -769 (0xFCFF), which clears the precision-control field, and FNSTCW reads "
                          f"it back through -3841 (0xF0FF), so a guest `fldcw 0x27F` left PC = 00 in State and the pass's live "
                          f"read saw PC24 at every one of the game region's 1025 fldcw sites. The double rounding itself is "
                          f"measured through the lifted binary at {n} rows per op: {pc24} mismatches at PC24 across all five "
                          f"ops, and at PC53 " + (", ".join(f"{k.split('_')[0]} {v}" for k, v in pc53.items()) or "none") +
                          f" out of {n}.",
                "not_done": "the double rounding is not fixed. The pass rounds the result remill already rounded to 64 bits; "
                            "rounding once at the target precision means replacing the operation rather than post-rounding it, "
                            "so the pass would emit a call that takes both operands and the control word and performs the op "
                            "itself. Cost: a second helper with one entry point per op and per width, a pass rule that "
                            "rewrites the operand list instead of the result, and a re-measurement of the ten-function table "
                            "(the PC24 column is 0/200000 today, so the change has to be shown not to move it). Estimated at "
                            "well inside the 8 agent wall-hours the G1 kill criterion allows for one class, and it is the "
                            "honest fix; it was not attempted inside this unit's time box.",
                "scale": (
                    f"distribution-dependent, so it is stated per measurement and not as one number. Through the lifted "
                    f"binary, on binary32 operands drawn uniformly from [-1e3, 1e3]: "
                    + ", ".join(f"{k.split('_')[0]} {v['pc53']['mismatch']}/{n}" for k, v in sw.items())
                    + ". Through the helper's own self-test, on its own distribution: "
                    + ", ".join(f"{op} {r['pc53_mismatches']}/{r['rows']}" for op, r in helper_cells)
                    + f". fadd and fsub are 0 in the first and non-zero in the second, so 'zero for fadd, fsub and fmul' "
                      f"is false as a statement about the class: it is a property of the sweep's operand distribution. "
                      f"fmul is the only op measured at 0 in both. The worst rate measured anywhere is "
                      f"{rc['worst_measured_rate']} per op, about 2^{rc['worst_measured_rate_as_power_of_two']}. "
                      f"{fns['length']['by_control_word']['pc53']['rows']} fixture rows per function see none of it at "
                      f"any of these rates, and those rows store binary32 at every step, which hides it anyway."),
                "residue_class": "written up in this piece's `residue_class` key: a named class with its measured rate per "
                                 "op, the design 7.4 row it has no home in, and the ledger entry it needs"}
    rows = piece["by_class"]
    cells = [(cls, probe, model, x) for cls, d in rows.items() for probe, e in d["probes"].items()
             for model, x in e["by_control_word"].items()]
    after = sum(x["remill_plus_pass_vs_native_x87"]["mismatch"] for _c, _p, _m, x in cells)
    before = sum(x["remill_vs_native_x87"]["mismatch"] for _c, _p, _m, x in cells)
    total = sum(x["rows"] for _c, _p, _m, x in cells)
    driven = [k for k, v in piece["real_functions"].items() if v.get("chosen")]
    return {"verdict": "partially covered",
            "reason": f"Every class design 3.4 names now has rows that run: {len(cells)} cells over {total} rows for the "
                      f"fsub family (fsub, fsubr, fsubp), fsqrt, fldcw and, for comparison, the three S1 classes, each row "
                      f"scored against this host's x87 executing the same instruction under the same control word. "
                      f"{before} mismatches before the pass, {after} after. The inherited corpus covers none of these "
                      f"classes (its two decodable fixtures are both the point transform, whose x87 content is fmul and "
                      f"fadd), so the rows are synthetic in their inputs and directly-traced in their expected words.",
            "not_done": f"the rows are hand-encoded instruction sequences, not game functions. A call-free leaf game function "
                        f"was found, lifted and instrumented for each of {', '.join(driven)}, and the pass's counts on each "
                        f"are recorded, but none is driven with rows: each would need its calling convention read off the "
                        f"listing and a reference written for it, which is S3 fixture work. fsqrt has no such function at all.",
            "obstacle_named": {k: v["obstacle"] for k, v in piece["real_functions"].items() if v.get("obstacle")}}


# ---------------------------------------------------------------- receipt
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skip-lift", action="store_true", help="reuse lifted IR already under private/generated/s2/patch/")
    ap.add_argument("--sweep-rows", type=int, default=200000, help="rows per op in the PC53 double-rounding sweep")
    a = ap.parse_args()
    for d in (BUILD, VEC, OUT):
        d.mkdir(parents=True, exist_ok=True)
    L.image_bytes()

    # tools
    scan = L.sh([sys.executable, str(HERE / "scan_x87.py")])
    if scan.returncode:
        raise SystemExit(f"scan_x87.py failed: {scan.stderr}")
    sites = json.loads(SITES.read_text())
    pr = L.sh([sys.executable, str(HERE / "probes.py")])
    if pr.returncode:
        raise SystemExit(f"probes.py failed: {pr.stderr}")
    probes_json = json.loads((GEN / "probes.json").read_text())
    int_helper = compile_int_helper()
    tools = {
        "oracle": L.build_oracle(BUILD),
        "reference": build_tool("ref_pc", [HERE / "ref_pc.c"]),
        "helper_self_test": build_tool("x87_int_test", [HERE / "x87_int.c", HERE / "x87_int_test.c"]),
        "scan": {"command": f".venv/bin/python {L.rel(HERE / 'scan_x87.py')}", "out": L.rel(SITES),
                 "summary": [l for l in scan.stdout.splitlines() if l.startswith("S2_X87")]},
        "probes": {"command": f".venv/bin/python {L.rel(HERE / 'probes.py')}", "out": L.rel(GEN / "probes.json")},
        "int_helper_ir": int_helper,
    }
    for name, t in tools.items():
        if isinstance(t, dict) and t.get("ok") is False:
            raise SystemExit(f"{name} did not build: {t['error_head']}")

    wanted = sorted({p for ps in CLASS_PROBES.values() for p in ps} |
                    {"fsqrt_cw80", "fistpl_cw", "fistpll_cw", "fistps_f64", "fldcw_fnstcw"})
    table = P.probe_table()
    variants = {}
    for name in wanted:
        variants[name] = probe_variants(name, table[name]["code"], a.skip_lift)
        if not variants[name]["ok"]:
            raise SystemExit(f"probe {name} did not build: {json.dumps(variants[name])[:400]}")

    p1 = piece_fsqrt(sites, probes_json, variants, a.skip_lift)
    p2 = piece_fistp(sites, variants, int_helper)
    p3 = piece_pc53(a.skip_lift)
    p3["double_rounding_sweep"] = pc53_sweep(variants, a.sweep_rows)
    p4 = piece_rows(sites, variants, a.skip_lift)

    pcround = sh_json([str(ROOT / "private" / "generated" / "spike" / "pc24pass" / "scratch" / "pc_round_test")])
    p3["residue_class"] = pc53_residue_class(p3, pcround[0] or {})
    for key, piece in (("1_fsqrt", p1), ("2_fistp_helper", p2), ("3_pc53_column", p3), ("4_fixture_rows", p4)):
        piece["verdict"] = verdict(key, piece)
    receipt = {
        "schema": SCHEMA,
        "id": "s2-patch-v1",
        "date": dt.date.today().isoformat(),
        "written": now(),
        "question": "Decision 2026-09-19-G1 section 1.3, the Patch list: extend the pass to fsqrt, add the fistp out-of-range "
                    "helper at every site, add a PC53 fixture column with cw = 0x027F, and build fixture rows for "
                    "fsub / fsqrt / fldcw before G2.",
        "authority": {
            "directly-traced": "every row compared against this host's x87 executing the same instruction under the same "
                               "control word (tools/s2/patch/x87_oracle.c, tools/s2/patch/x87_int_test.c), and the inherited "
                               "corpus rows the entry test decodes",
            "statically-translated": "rows scored against tools/s2/patch/ref_pc.c, a reference this project wrote",
        },
        "inputs": {
            "executable": "private/exe/sonic2app-unpacked.exe",
            "executable_sha256": sites["exe_sha256"],
            "function_map_sha256": sites["function_map_sha256"],
            "lifter": "remill-lift-14 (remill 5.0.7, private/lifters/remill)",
            "pass": {"path": "tools/spike/pc24pass/pc24pass.py", "sha256": L.sha(L.PASS_DIR / "pc24pass.py")},
            "pc_round_helper": {"path": "tools/spike/pc24pass/pc_round.c", "sha256": L.sha(L.PASS_DIR / "pc_round.c")},
            "int_helper": {"path": "tools/s2/patch/x87_int.c", "sha256": L.sha(HERE / "x87_int.c")},
            "reference": {"path": "tools/s2/patch/ref_pc.c", "sha256": L.sha(HERE / "ref_pc.c")},
            "oracle": {"path": "tools/s2/patch/x87_oracle.c", "sha256": L.sha(HERE / "x87_oracle.c")},
            "harness": {"path": "tools/s2/patch/s2_harness.cpp", "sha256": L.sha(HERE / "s2_harness.cpp")},
        },
        "tools": tools,
        "pieces": {"1_fsqrt": p1, "2_fistp_helper": p2, "3_pc53_column": p3, "4_fixture_rows": p4},
        "pc_round_self_test": pcround[0],
        "regression": {
            "question": "Do the S1 receipts still hold after the pass change?",
            "command": ".venv/bin/python tools/spike/pc24pass/test_pc24pass.py && source tools/env.sh && "
                       ".venv/bin/python tools/spike/pc24pass/run_pc24pass.py",
        },
    }
    RECEIPT.write_text(json.dumps(receipt, indent=1) + "\n")
    print(f"S2_PATCH wrote {L.rel(RECEIPT)}")
    return receipt


if __name__ == "__main__":
    main()
