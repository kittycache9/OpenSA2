"""runtime/sa2_ninja_math.c: the main thread's Ninja math kernels, bit for bit the double mode's
lifted bodies.

tools/s8/native_kernels/ninja_diff.py builds the differential harness
(tools/s8/native_kernels/ninja_diff.c): the runtime file as tools/s4/link_s4.py compiles it, the
double-mode lifted objects of 0x00426CC0 (njCalcPoint), 0x00424FF0 (njInvertMatrix, with
0x00425350, which it calls), 0x00426E40 (njMultiMatrix), 0x0042AAB0 (njSin), 0x0042AC30
(njCos), 0x00427470 (njTranslate_) and 0x004274E0/0x004275B0/0x00427680 (njRotateX_/Y_/Z_) as
`__real_`, wrapped as the
production link wraps them, and the double mode's shims compiled from tools/s8/f80_shims_double.c
(which includes tools/s7/f80_shims.c). Each kernel runs a million ordinary cases (realistic
values, edge cases mixed in, wild finite bit patterns, edges inside the range the bodies take
natively with cancellations; PC24 under every rounding control; segment bases zero and not; the
guest's own aliasing and null pointers; the register slots and flags random) twice, native bodies
off and on, and the whole State and every guest byte the kernel can reach must be equal. Every
guard's cases (an infinity or NaN among the inputs, an overlap the native order of reads and
writes does not hold for, an access outside the guest space, guest memory off its fast path, the
bodies switched off, a stack offset through DS with the bases apart, DF set, a precision other
than 24 bits, a host rounding mode other than RC) must take the lifted body, and so must every
call of a build without the double mode's marker, as the exact build is.

The lifted objects are the double mode's (private/generated/s8/pc-double/linux-obj), which the
harness refuses when its pass (tools/s8/f80_double.py, importing tools/s7/f80_legalize.py) or its
shims source has changed since they were built: a change there is a change to what these bodies
must equal, so this file names them for the fast check. The inputs are private/; without them, or
without the pinned zig, the build fails rather than skips.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s8" / "native_kernels"))
import ninja_diff as D  # noqa: E402

CASES = 1_000_000
SEED = "20261012"
KERNELS = ("calcpoint", "invert", "multiply", "sin", "cos", "translate", "rotx", "roty", "rotz")
COMMON_GUARDS = ("nonfinite_input", "overlap", "outside_guest_space", "memory_off_fast_path",
                 "switched_off", "precision_not_24", "host_mode_not_rc")
GUARDS = {"calcpoint": COMMON_GUARDS,
          "invert": COMMON_GUARDS + ("segment_bases_differ", "direction_flag"),
          "multiply": COMMON_GUARDS + ("segment_bases_differ",),
          "sin": COMMON_GUARDS, "cos": COMMON_GUARDS,
          "translate": tuple(g for g in COMMON_GUARDS if g != "overlap"),
          **{f"rot{a}": COMMON_GUARDS + ("segment_bases_differ", "direction_flag") for a in "xyz"}}
# The pass and shims inputs whose change is a change to what the bodies must equal.
DOUBLE_MODE_INPUTS = ("tools/s8/f80_double.py", "tools/s8/f80_shims_double.c",
                      "tools/s7/f80_shims.c", "tools/s7/f80_legalize.py")


@pytest.fixture(scope="module")
def double():
    return D.build("double")


@pytest.fixture(scope="module")
def nomarker():
    return D.build("nomarker")


def test_link_s4_compiles_and_wraps_the_kernels():
    assert "sa2_ninja_math.c" in D.L4.RUNTIME_C
    assert D.L4.PRODUCTION_WRAPS[0] == "sub_427ee0"
    assert [w for w in D.L4.PRODUCTION_WRAPS if w in D.WRAPS] == [
        "sub_426cc0", "sub_424ff0", "sub_426e40", "sub_42aab0", "sub_42ac30", "sub_427470",
        "sub_4274e0", "sub_4275b0", "sub_427680"]


def test_state_offsets_are_remills(double):
    r = D.run_harness(double, "layout")
    assert r["state_bytes"] == 3504
    assert r["ok"], {k: v for k, v in r["fields"].items() if v[0] != v[1]}
    assert len(r["fields"]) == 40


@pytest.mark.parametrize("kernel", KERNELS)
def test_native_body_equals_lifted_body(double, kernel):
    r = D.run_harness(double, kernel, str(CASES), SEED)
    assert r["mismatches"] == 0, r["first_mismatch"]
    ordinary = r["classes"]["none"]
    assert ordinary["cases"] == CASES
    # Realistic values never leave the native path, nor do the edges inside its range but for
    # the few a host flag or an ulp step out of the range hands over; edge and wild values may.
    real, inside = r["ordinary_by_values"]["realistic"], r["ordinary_by_values"]["range_edges"]
    assert real["cases"] > CASES // 5 and real["native"] == real["cases"]
    assert inside["cases"] > CASES // 5 and inside["native"] > 0.8 * inside["cases"]
    assert ordinary["native"] > CASES // 2
    assert ordinary["native_pc24"] == ordinary["native"]
    # Every path of the guest's runs natively.
    for path, n in r["ordinary_by_path"].items():
        assert n["native"] > n["cases"] // 3, (path, n)
    assert set(r["classes"]) == {"none", *GUARDS[kernel]}
    for g in GUARDS[kernel]:
        assert r["classes"][g] == {"cases": CASES // 16, "native": 0, "native_pc24": 0,
                                   "native_pc53_64": 0, "mismatches": 0}, g


@pytest.mark.parametrize("kernel", KERNELS)
def test_exact_build_takes_the_lifted_body(nomarker, kernel):
    r = D.run_harness(nomarker, kernel, "100000", SEED)
    assert r["mismatches"] == 0, r["first_mismatch"]
    assert all(c["native"] == 0 for c in r["classes"].values()), r["classes"]
    assert r["classes"]["none"]["cases"] == 100000


def stale_copy(tmp_path: Path, edit) -> Path:
    """The double-mode build's records in `tmp_path`, edited by `edit(summary, records)` (the
    check reads the records and the IR they name, not the objects)."""
    root = D.LIFTED
    summary = json.loads((root / "summary.json").read_text())
    recs = [json.loads(line) for line in (root / "build.jsonl").read_text().splitlines()]
    edit(summary, recs)
    (tmp_path / "summary.json").write_text(json.dumps(summary))
    (tmp_path / "build.jsonl").write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    return tmp_path


def test_the_lifted_build_is_held_to_this_checkout(tmp_path):
    objs, unchecked = D.lifted_objects(D.LIFTED)
    assert {o.name for o in objs} == {"ir_00426CC0.o", "ir_00424FF0.o", "ir_00426E40.o",
                                      "ir_00425350.o", "ir_0042AAB0.o", "ir_0042AC30.o",
                                      "ir_00427470.o", "ir_004274E0.o", "ir_004275B0.o",
                                      "ir_00427680.o"}
    named = {"tools/s8/f80_double.py", "tools/s8/f80_shims_double.c"}
    assert all(any(p in u for p in DOUBLE_MODE_INPUTS) for u in unchecked)
    assert not any(p in u for u in unchecked for p in named)

    def other_pass(s, _):
        s["pass_sha256"] = "0" * 64

    def other_shims(s, _):
        s["shims"]["source_sha256"] = "0" * 64

    def other_dep(s, _):
        s["deps"] = {"tools/s7/f80_legalize.py": "0" * 64}

    def older_ir(_, recs):
        for r in recs:
            if r.get("va") == "0x00424ff0":
                r["ir_sha256"] = "0" * 64

    def exact_mode(s, _):
        s["f80_mode"] = "exact"

    for i, edit in enumerate((other_pass, other_shims, other_dep, older_ir, exact_mode)):
        d = tmp_path / str(i)
        d.mkdir()
        with pytest.raises(SystemExit) as e:
            D.lifted_objects(stale_copy(d, edit))
        assert "rebuild it" in str(e.value), edit.__name__
