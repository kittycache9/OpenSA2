#!/usr/bin/env python3
"""Driver for the PC24 post-lift pass experiment (S1, G1 lifter decision).

Question: is remill's PC24 remainder "fixable" by a pass over its IR, without
rebuilding remill? Steps, all under private/generated/spike/pc24pass/ (ignored):

  1. helper    compile pc_round.c to LLVM IR (zig cc = clang 20, x86_64-linux-musl,
               the harness's target) and run pc_round_test.c natively: the helper
               against the C# RoundSignificand24 port and against the host x87
               under control words 0x007F / 0x027F.
  2. pass      pc24pass.py over the ten remill .ll files
               (private/generated/spike/remill/sub_*.ll) into root/remill/;
               run it a second time over its own output and require byte
               identity (idempotence); sha256 and line counts before/after.
  3. compile   every patched file with the harness's IR compile command
               (zig cc -target x86_64-linux-musl -O1 -w -c), timed with a fresh
               zig cache against the original; timeline: pass_first_compiles.
  4. harness   tools/spike/fixtures/run_fixtures.py, imported as a module with
               its GEN/OUTDIR redirected to root/ (it locates the IR as
               GEN/remill/sub_<VA>.ll and caches objects under OUTDIR/build, so a
               separate root is the only way to run it on patched IR without an
               edit); its timeline writes go to a private copy. root/retdec is a
               symlink to the original RetDec output so that column still builds.
  5. compare   before (private/generated/spike/fixtures/results.json, the run the
               existing receipts were written from) against after, per function
               and per row (control-flow changes = rows whose status or exit
               address moved); timeline: pass_first_row_exact.
  6. receipt   Documentation/receipts/s1-remill-pc24-patch-v1.json; timeline: pass_done.

Usage: .venv/bin/python tools/spike/pc24pass/run_pc24pass.py [--skip-harness] [--no-receipt]
(--skip-harness reuses root/fixtures/results.json from an earlier run.)
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "tools" / "spike"))
sys.path.insert(0, str(ROOT / "tools" / "spike" / "fixtures"))
import spikelib as S  # noqa: E402
import write_receipt as WR  # noqa: E402
import run_fixtures as RF  # noqa: E402
sys.path.insert(0, str(HERE))
import pc24pass  # noqa: E402

GEN = ROOT / "private" / "generated" / "spike"
PGEN = GEN / "pc24pass"
PROOT = PGEN / "root"
BEFORE_RESULTS = GEN / "fixtures" / "results.json"
SPIKE_RECEIPT = ROOT / "Documentation" / "receipts" / "s1-lifter-spike-v1.json"
PROBE_RECEIPT = ROOT / "Documentation" / "receipts" / "s1-pc24-check-v1.json"
RECEIPT = ROOT / "Documentation" / "receipts" / "s1-remill-pc24-patch-v1.json"
ZIG = str(S.ZIG)
TARGET = ["-target", "x86_64-linux-musl"]
IR_COMPILE = [ZIG, "cc"] + TARGET + ["-O1", "-w", "-c"]          # run_fixtures.build(): the IR compile policy
LIFTER = "remill-pc24pass"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(p: Path) -> str:
    return str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)


def sh(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def cmdline(cmd: list[str]) -> str:
    return " ".join(cmd).replace(str(ROOT) + "/", "")


def timeline_once(event: str, detail: str) -> None:
    """Append to the real timeline unless it already has this event (re-runs add nothing)."""
    for line in S.TIMELINE.read_text().splitlines():
        if line.strip() and json.loads(line)["lifter"] == LIFTER and json.loads(line)["event"] == event:
            return
    S.timeline(LIFTER, event, detail)


def fresh_cache_compile(src: Path, out: Path) -> tuple[bool, float, str]:
    """(ok, best-of-3 seconds, command) for the harness's IR compile with a fresh zig cache each time."""
    best = None
    ok = True
    err = ""
    for _ in range(3):
        with tempfile.TemporaryDirectory(dir=PGEN / "scratch") as cache:
            env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=cache, ZIG_LOCAL_CACHE_DIR=cache)
            t0 = time.perf_counter()
            p = sh(IR_COMPILE + [str(src), "-o", str(out)], cwd=PGEN / "scratch", env=env)
            t = time.perf_counter() - t0
            if p.returncode != 0:
                ok, err = False, S.error_head(p.stderr)
                break
            best = t if best is None else min(best, t)
    return ok, round(best, 3) if best is not None else None, err


# ---------------------------------------------------------------- 1. helper
def build_helper() -> dict:
    helper_ll = PGEN / "pc_round.ll"
    helper_ll.unlink(missing_ok=True)
    S.helper_ir(HERE / "pc_round.c", helper_ll)
    test_bin = PGEN / "scratch" / "pc_round_test"
    tcmd = [ZIG, "cc", "-O1", "-w", str(HERE / "pc_round_test.c"), str(HERE / "pc_round.c"), "-lm", "-o", str(test_bin)]
    p = sh(tcmd, cwd=PGEN / "scratch")
    if p.returncode != 0:
        raise SystemExit(f"self-test compile failed: {p.stderr}")
    t = sh([str(test_bin)])
    result = json.loads(t.stdout.strip().splitlines()[-1])
    result["exit_status"] = t.returncode
    return {"helper_ll": helper_ll, "helper_ll_sha256": sha(helper_ll), "compile_command": cmdline(cmd),
            "selftest_command": cmdline(tcmd) + " && " + rel(test_bin), "selftest": result}


# ---------------------------------------------------------------- 2. pass
def run_pass(helper_ll: Path) -> dict:
    out_dir = PROOT / "remill"
    out_dir.mkdir(parents=True, exist_ok=True)
    per: dict = {}
    helper_text = helper_ll.read_text()
    for va, name, *_ in RF.FUNCTIONS:
        src = GEN / "remill" / f"sub_{va:08X}.ll"
        dst = out_dir / f"sub_{va:08X}.ll"
        text = src.read_text()
        out, stats = pc24pass.instrument(text, helper_text)
        dst.write_text(out)
        again, stats2 = pc24pass.instrument(out, helper_text)
        per[f"{va:#010x}"] = {
            "name": name, "input": rel(src), "output": rel(dst),
            "ir_sha256_before": hashlib.sha256(text.encode()).hexdigest(), "ir_sha256_after": hashlib.sha256(out.encode()).hexdigest(),
            "ll_lines_before": text.count("\n"), "ll_lines_after": out.count("\n"),
            "ops_instrumented": stats["ops_instrumented"], "by_op": stats["by_op"], "by_remill_semantic": stats["by_semantic"],
            "by_function": stats["by_function"], "sites": stats["sites"],
            "skipped_by_semantic": stats["skipped_by_semantic"], "skipped_sites": stats["skipped_sites"],
            "skipped_non_lifted": stats["skipped_non_lifted"], "skipped_non_lifted_defines": stats["skipped_non_lifted_defines"],
            "marker_stripped_rerun_identical": pc24pass.instrument(out.split("\n", 1)[1], helper_text)[0] == out,
            "idempotent": again == out and stats2["already_instrumented"],
            "fpext_untouched": text.count(" = fpext float "), "fptrunc_untouched": text.count(" = fptrunc x86_fp80 to float"),
            "fptosi_sites": text.count(" = fptosi "),
        }
        if not per[f"{va:#010x}"]["idempotent"]:
            raise SystemExit(f"pass is not idempotent on {name}")
        if not per[f"{va:#010x}"]["marker_stripped_rerun_identical"]:
            raise SystemExit(f"pass re-instruments its own output without the marker on {name}")
    # the untouched .ll are the ones the earlier run compiled; the harness needs RetDec's output too
    link = PROOT / "retdec"
    if not link.exists():
        link.symlink_to(GEN / "retdec")
    return per


# ---------------------------------------------------------------- 3. compile
def compile_all(per: dict) -> None:
    first = False
    for va, entry in per.items():
        src = GEN / "remill" / Path(entry["input"]).name
        dst = PROOT / "remill" / Path(entry["output"]).name
        ok_b, t_b, err_b = fresh_cache_compile(src, PGEN / "scratch" / f"orig_{va}.o")
        ok_a, t_a, err_a = fresh_cache_compile(dst, PGEN / "scratch" / f"patched_{va}.o")
        entry["compile"] = {"command": cmdline(IR_COMPILE) + " <file.ll>", "before_ok": ok_b, "after_ok": ok_a,
                            "before_seconds_best_of_3_fresh_cache": t_b, "after_seconds_best_of_3_fresh_cache": t_a,
                            "delta_seconds": round(t_a - t_b, 3) if ok_a and ok_b else None, "error_head": err_a or err_b}
        if ok_a and not first:
            first = True
            timeline_once("pass_first_compiles",
                          f"{entry['name']} {va}: pc24pass output compiles with the harness's IR policy ({cmdline(IR_COMPILE)}); "
                          f"{entry['ops_instrumented']} x86_fp80 ops wrapped in __sa2_pc_round, {entry['ll_lines_before']} -> {entry['ll_lines_after']} lines")
        print(f"compile {va} {entry['name']:14} before {ok_b} {t_b}s  after {ok_a} {t_a}s  {err_a or err_b}")


# ---------------------------------------------------------------- 4. harness
def run_harness() -> dict:
    RF.GEN = PROOT
    RF.OUTDIR = PROOT / "fixtures"
    private_timeline = PROOT / "timeline.jsonl"
    shutil.copy(S.TIMELINE, private_timeline)   # the harness's timeline_once() must not touch the real one
    S.TIMELINE = private_timeline
    RF.OUTDIR.mkdir(parents=True, exist_ok=True)
    argv = sys.argv
    sys.argv = ["run_fixtures.py"]
    t0 = time.perf_counter()
    try:
        RF.main()
    finally:
        sys.argv = argv
    wall = time.perf_counter() - t0
    S.TIMELINE = ROOT / "tools" / "spike" / "timeline.jsonl"
    return {"results": RF.OUTDIR / "results.json", "wall_seconds": round(wall, 1),
            "command": "tools/spike/pc24pass/run_pc24pass.py: import tools/spike/fixtures/run_fixtures.py, set GEN=private/generated/spike/pc24pass/root "
                       "OUTDIR=GEN/fixtures spikelib.TIMELINE=GEN/timeline.jsonl (a copy), then main() with no arguments (default --rows 256)"}


# ---------------------------------------------------------------- 5. compare
def compare(before: dict, after: dict, per: dict) -> dict:
    table = {}
    first_exact_event = None
    for va, name, in_words, out_words, fields, callees in RF.FUNCTIONS:
        key = f"{va:#010x}"
        b = before["functions"][key]["lifters"]["remill"]
        a = after["functions"][key]["lifters"]["remill"]
        row = {"name": name, "rows": a["rows"],
               "native_rows": (a.get("rows_by_authority") or {}).get("native", 0),
               "discriminating_rows": after["functions"][key]["pc24_vs_fp80_model_differ_rows"],
               "native_rows_reproduced_by_pc24_model": int((after["functions"][key]["sources"].get("native") or {}).get("pc24_reference_reproduces_rows", "0/0").split("/")[0]),
               "native_rows_reproduced_by_fp80_model": int((after["functions"][key]["sources"].get("native") or {}).get("fp80_model_reproduces_rows", "0/0").split("/")[0]),
               "before": {k: b.get(k) for k in ("verdict", "exact", "residue", "failed", "not_run", "observed_status_counts")},
               "after": {k: a.get(k) for k in ("verdict", "exact", "residue", "failed", "not_run", "observed_status_counts", "first_divergence", "first_fail", "coverage", "blocker")},
               "expected_status_counts": a.get("expected_status_counts") or b.get("expected_status_counts"),
               "same_vectors": before["functions"][key]["vectors_sha256"] == after["functions"][key]["vectors_sha256"],
               "ops_instrumented": per[key]["ops_instrumented"]}
        ob = GEN / "fixtures" / "out" / f"{name}.remill.bin"
        oa = PROOT / "fixtures" / "out" / f"{name}.remill.bin"
        pc24 = PROOT / "fixtures" / "out" / f"{name}.pc24.bin"
        fp80 = PROOT / "fixtures" / "out" / f"{name}.fp80.bin"
        if ob.exists() and oa.exists() and pc24.exists():
            rb, ra, exp, f80 = RF.read_out(ob), RF.read_out(oa), RF.read_out(pc24), RF.read_out(fp80)
            trans: dict[str, int] = {}
            cf_changed = []
            for i in range(len(ra)):
                vb, va_ = RF.classify(rb[i], exp[i], f80[i]), RF.classify(ra[i], exp[i], f80[i])
                trans[f"{vb}->{va_}"] = trans.get(f"{vb}->{va_}", 0) + 1
                if rb[i][:2] != ra[i][:2]:
                    cf_changed.append({"row": i, "before": [f"{w:#010x}" for w in rb[i][:2]], "after": [f"{w:#010x}" for w in ra[i][:2]],
                                       "expected": [f"{w:#010x}" for w in exp[i][:2]], "after_matches_expected": ra[i][:2] == exp[i][:2]})
            row["row_transitions_before_to_after"] = dict(sorted(trans.items()))
            row["control_flow_rows_changed"] = len(cf_changed)
            row["control_flow_rows_changed_now_expected"] = sum(1 for c in cf_changed if c["after_matches_expected"])
            row["control_flow_changes"] = cf_changed[:8]
            first_now_exact = next((i for i in range(len(ra)) if RF.classify(rb[i], exp[i], f80[i]) == "RESIDUE" and RF.classify(ra[i], exp[i], f80[i]) == "EXACT"), None)
            if first_now_exact is not None and first_exact_event is None:
                first_exact_event = (name, key, first_now_exact, a)
        table[key] = row
    if first_exact_event:
        name, key, i, a = first_exact_event
        timeline_once("pass_first_row_exact",
                      f"{name} {key}: row {i} was RESIDUE under remill's 80-bit arithmetic and is bit-identical to its PC24 expectation with the pass "
                      f"({a['exact']}/{a['rows']} rows exact, verdict {a['verdict']})")
    return table


def probe_block(after: dict) -> dict:
    out = {}
    for key, fn in after["pc24_probe"]["functions"].items():
        rows = []
        for r in fn["rows"]:
            rows.append({"why": r["why"].split(":")[0], "inputs": r["inputs"], "models_differ": r["models_differ"],
                         "expected_pc24": r["expected_pc24"], "fp80_model": r["fp80_model"],
                         "observed_remill_pc24pass": r["observed"]["remill"]["words"], "verdict": r["observed"]["remill"]["verdict"]})
        out[key] = {"name": fn["name"], "search_rows": fn["search_rows"], "search_rows_where_models_differ": fn["search_rows_where_models_differ"],
                    "summary_remill_pc24pass": fn["summary"]["remill"], "rows": rows}
    return out


# ---------------------------------------------------------------- 6. receipt
def before_after_table(table: dict) -> dict:
    """The ten-function before/after rows with native and discriminating row counts beside the verdicts, plus per-lifter totals."""
    rows = [
        {"function": f"{k} {v['name']}", "rows": v["rows"], "native_rows": v["native_rows"], "discriminating_rows": v["discriminating_rows"],
         "before_verdict": v["before"]["verdict"], "before_exact": v["before"]["exact"], "before_residue": v["before"]["residue"], "before_fail": v["before"]["failed"],
         "after_verdict": v["after"]["verdict"], "after_exact": v["after"]["exact"], "after_residue": v["after"]["residue"], "after_fail": v["after"]["failed"],
         "after_first_divergent": (f"{v['after']['first_divergence']['row']} / {v['after']['first_divergence']['field']}" if v["after"].get("first_divergence") else "- / -"),
         "control_flow_rows_changed": v.get("control_flow_rows_changed"), "control_flow_rows_changed_now_expected": v.get("control_flow_rows_changed_now_expected"),
         "ops_instrumented": v["ops_instrumented"]}
        for k, v in table.items()]
    totals = {k: sum(r[k] for r in rows) for k in ("rows", "native_rows", "discriminating_rows", "before_exact", "before_residue", "before_fail",
                                                     "after_exact", "after_residue", "after_fail", "ops_instrumented")}
    totals["native_rows_reproduced_by_pc24_model"] = sum(v["native_rows_reproduced_by_pc24_model"] for v in table.values())
    totals["native_rows_reproduced_by_fp80_model"] = sum(v["native_rows_reproduced_by_fp80_model"] for v in table.values())
    totals["functions_with_zero_discriminating_rows"] = [r["function"] + f" ({r['before_verdict']} -> {r['after_verdict']})" for r in rows if r["discriminating_rows"] == 0]
    return {"definition": "native_rows = rows_by_authority.native (directly traced; 0x00426CC0 only), reproduced by both reference models so they anchor the reference and not the "
                          "PC24 question; discriminating_rows = rows where the PC24 and fp80 reference models differ (run_fixtures.py pc24_vs_fp80_model_differ_rows), the only "
                          "rows the pass can change; a function with zero discriminating rows is EXACT before and after for reasons unrelated to precision control",
            "per_function": rows, "totals": totals}


def write_receipt(helper: dict, per: dict, harness: dict, table: dict, after: dict, before_receipt: dict, probe_before: dict) -> dict:
    rows_for_rule = [{"va": k, "fixture": {"verdict": v["after"]["verdict"]}} for k, v in table.items()]
    verdict = WR.fixture_verdict(rows_for_rule)
    before_rows = [{"va": k, "fixture": {"verdict": v["before"]["verdict"]}} for k, v in table.items()]
    verdict_before = WR.fixture_verdict(before_rows)
    exact_after = verdict["rule_on_exact"]["passing"]
    head = sh(["git", "rev-parse", "HEAD"], cwd=ROOT).stdout.strip()
    total_ops = sum(v["ops_instrumented"] for v in per.values())
    growth = {k: v["ll_lines_after"] - v["ll_lines_before"] for k, v in per.items()}
    ct = {k: v["compile"] for k, v in per.items()}
    pass_loc = sum(1 for l in (HERE / "pc24pass.py").read_text().splitlines() if l.strip() and not l.strip().startswith("#"))
    helper_loc = sum(1 for l in (HERE / "pc_round.c").read_text().splitlines() if l.strip())
    fptosi = sum(v["fptosi_sites"] for v in per.values())
    ir_text = "\n".join((GEN / "remill" / f"sub_{va:08X}.ll").read_text() for va, *_ in RF.FUNCTIONS)
    transcendental_labels = sum(ir_text.count(f"_ZN12_GLOBAL__N_1{len(n)}{n}") for n in ("FSIN", "FCOS", "FSINCOS", "FPTAN", "FPATAN", "FYL2X", "F2XM1", "FYL2XP1"))
    fldcw_labels = ir_text.count("FLDCW")
    fsqrt_labels = ir_text.count("FSQRT")
    sqrt_sites = ir_text.count("@llvm.sqrt.f80(")
    harness_src = (ROOT / "tools" / "spike" / "fixtures" / "remill_harness.cpp").read_text()
    cw_sites = harness_src.count("cwd.flat = 0x007F")
    fp80_ops_by_va = {f"{va:#010x}": len(re.findall(r"\bf(?:add|sub|mul|div) x86_fp80\b", (GEN / "remill" / f"sub_{va:08X}.ll").read_text())) for va, *_ in RF.FUNCTIONS}
    zero_op_functions = [f"{k} {per[k]['name']}" for k, n in fp80_ops_by_va.items() if n == 0]
    skipped_total = sum(sum(v["skipped_by_semantic"].values()) for v in per.values())
    st = helper["selftest"]
    receipt = {
        "schema": "sa2-recomp-receipt/v1",
        "id": "s1-remill-pc24-patch-v1",
        "date": dt.date.today().isoformat(),
        "stage": "S1",
        "question": "Is remill's PC24 remainder fixable by a post-lift pass, i.e. can x87 precision-control rounding be added to remill's lifted IR "
                    "without rebuilding remill, and does the ten-function fixture table then meet the design's adopt rule (design 5 S1: >= 8 of 10 with a fixable remainder) on EXACT?",
        "inputs": {
            "exe": before_receipt["inputs"].get("exe", "private/exe/sonic2app-unpacked.exe"),
            "exe_sha256": before_receipt["inputs"].get("exe_sha256"),
            "remill": {"package": before_receipt["lifters"]["remill"]["install"].get("package"),
                       "commit": "c0f90b9e4a251615288ed4b65ead81cea617da8c",
                       "download_sha256": (before_receipt["lifters"]["remill"]["install"].get("download") or {}).get("sha256")},
            "harness_commit": head,
            "harness_files_sha256": {rel(p): sha(p) for p in (ROOT / "tools" / "spike" / "fixtures" / "run_fixtures.py", ROOT / "tools" / "spike" / "fixtures" / "remill_harness.cpp",
                                                              ROOT / "tools" / "spike" / "fixtures" / "pc24_reference.c")},
            "pass_sha256": {rel(HERE / "pc24pass.py"): sha(HERE / "pc24pass.py"), rel(HERE / "run_pc24pass.py"): sha(HERE / "run_pc24pass.py")},
            "helper_sha256": {rel(HERE / "pc_round.c"): sha(HERE / "pc_round.c"), rel(HERE / "pc_round_test.c"): sha(HERE / "pc_round_test.c"),
                              "private/generated/spike/pc24pass/pc_round.ll (clang 20 -O1 -g0 -S -emit-llvm, x86_64-linux-musl)": helper["helper_ll_sha256"]},
            "compiler": WR.tool_version([ZIG, "cc", "--version"]) if hasattr(WR, "tool_version") else "zig cc",
            "ir_per_function": {k: {kk: v[kk] for kk in ("name", "input", "output", "ir_sha256_before", "ir_sha256_after", "ll_lines_before", "ll_lines_after",
                                                          "ops_instrumented", "by_op", "by_remill_semantic", "skipped_by_semantic", "skipped_non_lifted", "idempotent",
                                                          "marker_stripped_rerun_identical", "fpext_untouched", "fptrunc_untouched", "fptosi_sites")}
                                for k, v in per.items()},
            "before_results": rel(BEFORE_RESULTS),
            "before_receipts": [rel(SPIKE_RECEIPT), rel(PROBE_RECEIPT)],
            "after_results": rel(harness["results"]),
            "same_vectors_every_function": all(v["same_vectors"] for v in table.values()),
        },
        "pattern_instrumented": {
            "match": "an instruction `%r = fadd|fsub|fmul|fdiv x86_fp80 ...` (or `call x86_fp80 @llvm.sqrt.f80`) inside a `define` whose first parameter is the State pointer, "
                     "and only in a block whose nearest preceding remill label names a FADD/FSUB/FMUL/FDIV/FSQRT semantic (reverse, pop, integer-operand and memory forms included: "
                     "F[I](ADD|SUB|MUL|DIV)[R][P][mem], FSQRT); an op under any other semantic (FSIN, FCOS, FPTAN, FPATAN, FYL2X, F2XM1, FSCALE, FRNDINT, FPREM, FPREM1, ...) "
                     "or under no remill label is left untouched and counted in skipped_by_semantic; a define whose first parameter is not a pointer is skipped and counted in skipped_non_lifted",
            "attribution": "each of the ten files' ops sits in the `.exit` block of its own semantic (the block remill's inlined C++ leaves its arithmetic in); the pass records the label per site",
            "rewrite": ["%sa2pre.r = <the op unchanged>",
                        "%sa2cwp.r = getelementptr inbounds %struct.State, ptr %state, i64 0, i32 0, i32 11, i32 0, i32 0, i32 0, i32 0   ; State.X86State.FPU.fxsave.cwd.flat",
                        "%sa2cw.r = load i16, ptr %sa2cwp.r, align 2",
                        "%r = call x86_fp80 @__sa2_pc_round(x86_fp80 %sa2pre.r, i16 %sa2cw.r)"],
            "helper": "define internal x86_fp80 @__sa2_pc_round(x86_fp80, i16): PC field (bits 8-9) 00 -> round the 64-bit significand to 24 bits, 10 -> 53, 11 -> identity, 01 (reserved) -> identity; RC field (bits 10-11) nearest-even / down / up / zero; zero, inf, nan identity; denormal (exponent 0) rounded at the fixed position of the smallest normal; carry renormalises, at exponent 0x7FFE it becomes infinity",
            "idempotence": "the output starts with `; sa2-pc24pass v1`; a second run returns the input byte for byte (checked per function, column idempotent), and a run over the output "
                           "with the marker line stripped also returns it byte for byte (column marker_stripped_rerun_identical: sa2pre.* ops are never wrapped twice, the spliced helper's "
                           "define is skipped as non-lifted, the helper is not spliced again); tools/spike/pc24pass/test_pc24pass.py covers the three guards on synthetic modules",
            "ops_instrumented_total": total_ops,
            "by_op_total": {op: sum(v["by_op"].get(op, 0) for v in per.values()) for op in ("fadd", "fsub", "fmul", "fdiv", "sqrt")},
            "by_remill_semantic_total": {sem: sum(v["by_remill_semantic"].get(sem, 0) for v in per.values()) for sem in sorted({k for v in per.values() for k in v["by_remill_semantic"]})},
            "skipped_by_semantic_total": {sem: sum(v["skipped_by_semantic"].get(sem, 0) for v in per.values()) for sem in sorted({k for v in per.values() for k in v["skipped_by_semantic"]})},
            "skipped_non_lifted_total": sum(v["skipped_non_lifted"] for v in per.values()),
        },
        "commands": [
            helper["compile_command"], helper["selftest_command"],
            ".venv/bin/python tools/spike/pc24pass/pc24pass.py --helper-ll private/generated/spike/pc24pass/pc_round.ll private/generated/spike/remill/sub_<VA>.ll private/generated/spike/pc24pass/root/remill/sub_<VA>.ll",
            cmdline(IR_COMPILE) + " <file.ll> (3 runs each, ZIG_GLOBAL_CACHE_DIR/ZIG_LOCAL_CACHE_DIR fresh per run, best kept)",
            harness["command"],
            ".venv/bin/python tools/spike/pc24pass/run_pc24pass.py (the whole sequence)",
        ],
        "helper_selftest": {"rows_per_check": st["rows_per_check"], "reference_mismatch_vs_RoundSignificand24": st["reference_mismatch"],
                            "hardware_mismatch_op_at_pc64_then_helper_vs_op_at_pc": st["hardware_mismatch"], "edge_mismatch": st["edge_mismatch"], "exit_status": st["exit_status"],
                            "reading": "the helper reproduces the host x87's PC24 result for fadd/fsub/fmul/fdiv/fsqrt on binary32 operands on every row; at PC53 remill's 64-bit "
                                       "result then a 53-bit rounding is a double rounding and differs from x87's single rounding on the rows counted (measured residue, "
                                       "irrelevant to the game's PC24 arithmetic, relevant to the CRT's 0x27F window around fsin/fcos)"},
        "before_after": table,
        "before_after_table": before_after_table(table),
        "pc24_probe_after": probe_block(after),
        "pc24_probe_before": probe_before,
        "design_rule": {
            "before": {k: v for k, v in verdict_before.items() if k != "definition"},
            "after": {k: v for k, v in verdict.items() if k != "definition"},
            "definition": verdict["definition"],
            "evaluated_on": "EXACT after the patch (EXACT-PARTIAL, the head-only 0x0048B9F0, is not counted as passing; RESIDUE-ONLY is not counted)",
            "decision": ("adopt: remill plus this pass as the \"patch\"" if exact_after >= 8 else ("base for pivot" if exact_after >= 6 else "custom emitter")),
            "passing_on_exact_after": exact_after,
        },
        "cost": {
            "pass_lines_of_code": {"pc24pass.py (non-blank, non-comment)": pass_loc, "pc_round.c (non-blank, comments included)": helper_loc, "authority": "measured"},
            "ops_instrumented_per_function": {k: v["ops_instrumented"] for k, v in per.items()},
            "ir_line_growth_per_function": growth,
            "ir_line_growth_total": {"before": sum(v["ll_lines_before"] for v in per.values()), "after": sum(v["ll_lines_after"] for v in per.values()), "authority": "measured"},
            "compile_time": {"per_function": ct,
                             "total_before_seconds": round(sum(c["before_seconds_best_of_3_fresh_cache"] or 0 for c in ct.values()), 3),
                             "total_after_seconds": round(sum(c["after_seconds_best_of_3_fresh_cache"] or 0 for c in ct.values()), 3),
                             "authority": "measured (best of 3 with a fresh zig cache per run; the harness's own build caches objects, so its numbers are not comparable)"},
            "harness_wall_seconds_after": harness["wall_seconds"],
            "not_covered": [
                {"item": "fistp out-of-range (0x80000000 instead of the host's saturation)", "status": "not covered by the pass; fptosi is remill's, untouched",
                 "evidence": f"{fptosi} fptosi sites across the ten files (measured); behaviour reasoned from design 3.4 and 7.4, no fixture row exercises an out-of-range store"},
                {"item": "transcendentals (fsin, fcos, fpatan, fptan, fyl2x, f2xm1)", "status": "not covered: remill computes them in x86_fp80 via its own semantics; PC does not apply to them on x87 either, but the CRT's 0x27F window is only honoured for the basic ops inside it",
                 "evidence": f"{transcendental_labels} transcendental semantic labels in the ten files (measured: none); residue class 'transcendental' in design 7.4 (reasoned)"},
                {"item": "denormals", "status": "handled in the helper at the x87 fixed position for 80-bit denormals (< 2^-16382), which binary32 data cannot reach; binary32 denormals are normal x86_fp80 values and get the ordinary rounding; the FTZ policy of design 7.4 is not modelled",
                 "evidence": "reasoned; no fixture row is denormal (edge rows use tiny normals)"},
                {"item": "fld of a 32-bit memory operand", "status": "exact already (fpext float -> x86_fp80 loses nothing) and not instrumented",
                 "evidence": f"{sum(v['fpext_untouched'] for v in per.values())} fpext sites left untouched (measured)"},
                {"item": "fnstsw / C0-C3 flags", "status": "not touched: remill's fcom compares the (now rounded) stack values and writes the status word itself; fnstsw copies it",
                 "evidence": "reasoned; the fcom/fnstsw/test/jp pattern was already exact before the pass (before table) and the 0x004469e0 branch rows are in the after table (measured)"},
                {"item": "fldcw inside lifted code", "status": "covered by construction: the control word is loaded from State at every op site, so a store by remill's FLDCW semantics is seen by the next op",
                 "evidence": f"{fldcw_labels} FLDCW semantic labels in the ten files (measured: none in the ten; the harness sets 0x007F per row); reasoned for the CRT's 0x27F reload"},
                {"item": "PC53 double rounding", "status": "the helper rounds remill's already-64-bit-rounded result; at 53 bits this differs from x87's single rounding on a few rows in 200000",
                 "evidence": "measured (helper_selftest.hardware_mismatch.*.pc53)"},
                {"item": "rounding-control modes other than nearest-even", "status": "implemented in the helper (down, up, toward zero), not exercised",
                 "evidence": "reasoned; 0x007F and 0x27F both select nearest-even"},
                {"item": "x87 stack faults, tag word, unnormal/pseudo-denormal operands", "status": "remill's model, untouched", "evidence": "reasoned"},
                {"item": "control word other than 0x007F through the pass",
                 "status": "structurally verified, untested at PC53/PC64: the live-cw read is a getelementptr into State index 11 (%union.FPU), FpuFXSAVE field 0 (cwd), "
                           "the same path remill's own fop/ip/dp stores use, checked against the type lines per file; but no fixture varies the control word",
                 "evidence": f"remill_harness.cpp sets state.x87.fxsave.cwd.flat = 0x007F per row ({cw_sites} assignment site, measured); no vector carries a control word; "
                             f"{fldcw_labels} FLDCW labels in the ten (measured); the helper alone is tested at PC53 against the host x87 (helper_selftest, measured), the pass is not"},
                {"item": "fsqrt through remill's FSQRT semantics",
                 "status": "never ran: the game's sqrt is a call to the CRT's _CIsqrt (0x8290a0), which the harness serves natively as a host fsqrt under the guest control word, "
                           "so the pass's FSQRT/llvm.sqrt.f80 branch is exercised only by tools/spike/pc24pass/test_pc24pass.py on a synthetic module",
                 "evidence": f"{fsqrt_labels} FSQRT semantic labels and {sqrt_sites} llvm.sqrt.f80 sites in the ten files (measured); remill_harness.cpp sub_8290a0 (measured)"},
                {"item": "0x00468FA0 transform_dir: the 0x459260 callee is an oracle stub",
                 "status": "the harness answers 0x459260 (direction -> two angles) by writing the fixture row's own expected angles into guest memory, so this function's float "
                           "words are the stub's output plus the identity path, not lifted x87 evidence; its EXACT verdict before and after says nothing about the pass",
                 "evidence": f"remill_harness.cpp sub_459260 (measured); sub_00468FA0.ll has {fp80_ops_by_va.get('0x00468fa0', 0)} x86_fp80 arithmetic ops (measured); "
                             f"{table['0x00468fa0']['discriminating_rows']} discriminating rows (measured)"},
                {"item": "EXACT functions with no x87 arithmetic",
                 "status": "four of the nine EXACT-after functions were EXACT before the pass because they have no x86_fp80 arithmetic to round; their verdicts test control flow, "
                           "integer logic, memory and the matrix-stack globals, not precision control",
                 "evidence": f"functions with zero x86_fp80 arithmetic ops in their lifted IR (measured): {', '.join(zero_op_functions)}; "
                             f"EXACT before and after with zero discriminating rows: {', '.join(before_after_table(table)['totals']['functions_with_zero_discriminating_rows'])}"},
                {"item": "semantic attribution by label",
                 "status": "the pass attributes an op to the nearest preceding remill-labelled block; on the ten every site is in the `.exit` block of its own semantic, but a semantic "
                           "whose arithmetic sits in an unlabelled or differently labelled block would be skipped (counted, visible as RESIDUE), never wrongly rounded",
                 "evidence": f"{total_ops} sites all under F(ADD|MUL|DIV)[P|mem] labels, {skipped_total} skipped (measured); S3 must re-check on the full image (reasoned)"},
            ],
        },
        "outcome": {
            "functions_exact_before": verdict_before["functions_exact"], "functions_exact_after": verdict["functions_exact"],
            "functions_residue_only_before": verdict_before["functions_residue_only"], "functions_residue_only_after": verdict["functions_residue_only"],
            "functions_fail_after": verdict["functions_fail"],
            "probe_after": {k: v["summary_remill_pc24pass"] for k, v in probe_block(after).items()},
            "probe_before": probe_before,
        },
        "authority": "observed for every measured row (fixture runs of the patched IR, the helper self-test against the host x87, compile timings); the fixture expectations are the "
                     "statically-translated PC24 reference for every function except the 53 native rows of 0x00426CC0 (directly traced); the fp80 model is the diagnosis, not an authority",
        "written_by": "tools/spike/pc24pass/run_pc24pass.py",
    }
    RECEIPT.write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skip-harness", action="store_true")
    ap.add_argument("--no-receipt", action="store_true")
    a = ap.parse_args()
    (PGEN / "scratch").mkdir(parents=True, exist_ok=True)
    helper = build_helper()
    print("helper self-test:", json.dumps(helper["selftest"]))
    if helper["selftest"]["exit_status"] != 0:
        raise SystemExit("helper self-test failed")
    per = run_pass(helper["helper_ll"])
    for k, v in per.items():
        print(f"pass {k} {v['name']:14} ops={v['ops_instrumented']:2} {v['by_op']} skipped_by_semantic={v['skipped_by_semantic']} skipped_non_lifted={v['skipped_non_lifted']} "
              f"lines {v['ll_lines_before']} -> {v['ll_lines_after']} idempotent={v['idempotent']} marker_stripped_identical={v['marker_stripped_rerun_identical']}")
    skipped: dict[str, int] = {}
    for v in per.values():
        for sem, n in v["skipped_by_semantic"].items():
            skipped[sem] = skipped.get(sem, 0) + n
    by_sem: dict[str, int] = {}
    for v in per.values():
        for sem, n in v["by_remill_semantic"].items():
            by_sem[sem] = by_sem.get(sem, 0) + n
    print(f"pass stats: instrumented {sum(v['ops_instrumented'] for v in per.values())} {json.dumps(by_sem)} / skipped_by_semantic {sum(skipped.values())} {json.dumps(skipped)} "
          f"/ skipped_non_lifted {sum(v['skipped_non_lifted'] for v in per.values())}")
    compile_all(per)
    if a.skip_harness and (PROOT / "fixtures" / "results.json").exists():
        harness = {"results": PROOT / "fixtures" / "results.json", "wall_seconds": None, "command": "(reused earlier run)"}
    else:
        harness = run_harness()
    before = json.loads(BEFORE_RESULTS.read_text())
    after = json.loads(harness["results"].read_text())
    table = compare(before, after, per)
    print(f"{'function':26} {'rows':>5} {'native':>6} {'discrim':>7}  {'before':14} exact/res/fail   {'after':14} exact/res/fail  cf-changed  ops")
    for k, v in table.items():
        b, af = v["before"], v["after"]
        print(f"{k} {v['name']:14} {v['rows']:>5} {v['native_rows']:>6} {v['discriminating_rows']:>7}  {b['verdict']:14} {b['exact']:4}/{b['residue']:3}/{b['failed']:3}   "
              f"{af['verdict']:14} {af['exact']:4}/{af['residue']:3}/{af['failed']:3}  {v.get('control_flow_rows_changed')}  {v['ops_instrumented']}")
    tot = before_after_table(table)["totals"]
    print(f"{'totals':26} {tot['rows']:>5} {tot['native_rows']:>6} {tot['discriminating_rows']:>7}  {'':14} {tot['before_exact']:4}/{tot['before_residue']:3}/{tot['before_fail']:3}   "
          f"{'':14} {tot['after_exact']:4}/{tot['after_residue']:3}/{tot['after_fail']:3}  native rows reproduced by pc24 model {tot['native_rows_reproduced_by_pc24_model']}, "
          f"by fp80 model {tot['native_rows_reproduced_by_fp80_model']}; zero discriminating rows: {len(tot['functions_with_zero_discriminating_rows'])} functions")
    for k, fn in after["pc24_probe"]["functions"].items():
        print(f"probe {k} {fn['name']}: {json.dumps(fn['summary']['remill'])}")
    (PGEN / "summary.json").write_text(json.dumps({"helper": {k: v for k, v in helper.items() if k != "helper_ll"}, "per_function": per, "table": table,
                                                   "probe": probe_block(after), "harness": {k: (str(v) if isinstance(v, Path) else v) for k, v in harness.items()}}, indent=2) + "\n")
    if not a.no_receipt:
        before_receipt = json.loads(SPIKE_RECEIPT.read_text())
        probe_before = json.loads(PROBE_RECEIPT.read_text())["outcome"]["remill"]
        r = write_receipt(helper, per, harness, table, after, before_receipt, probe_before)
        print("wrote", rel(RECEIPT), "design rule on EXACT after:", r["design_rule"]["after"]["rule_on_exact"], "->", r["design_rule"]["decision"])
        v = r["design_rule"]["after"]
        timeline_once("pass_done",
                      f"ten functions rerun on pc24pass IR: EXACT {v['functions_exact']} (was {r['design_rule']['before']['functions_exact']}), "
                      f"RESIDUE-ONLY {v['functions_residue_only']}, FAIL {v['functions_fail']}, EXACT-PARTIAL {v['functions_exact_partial_head_only']}; "
                      f"probe {json.dumps(r['outcome']['probe_after'])}; receipt {rel(RECEIPT)}")


if __name__ == "__main__":
    main()
