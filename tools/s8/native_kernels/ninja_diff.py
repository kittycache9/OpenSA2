#!/usr/bin/env python3
"""ninja_diff: build and run the differential harness of runtime/sa2_ninja_math.c, and its game A/B.

Subtitle: compiles tools/s8/native_kernels/ninja_diff.c with the pinned zig and
runtime/sa2_ninja_math.c with tools/s4/link_s4.py's own runtime command, links them with the
double-mode lifted objects of njCalcPoint (0x00426CC0), njInvertMatrix (0x00424FF0, and
0x00425350, which it calls), njMultiMatrix (0x00426E40), njSin, njCos, njTranslate_ and
njRotateX_/Y_/Z_ (KERNELS) as `__real_`, wrapped as the
production link wraps them, and runs each kernel on CASES random and edge-case States and guest
memories plus every guard's cases; prints the harness's JSON.

    source tools/env.sh; export SDL_AUDIO_DRIVER=dummy
    .venv/bin/python tools/s8/native_kernels/ninja_diff.py [--cases 1000000] [--seed 1]
        [--kernels calcpoint,invert,multiply,sin,cos,translate,rotx,roty,rotz] [--variant double|nomarker]
        [--arch x86_64|aarch64|x86_64-windows] [--json OUT]
    .venv/bin/python tools/s8/native_kernels/ninja_diff.py bench [--arch ...] [--calls 2000000]
    .venv/bin/python tools/s8/native_kernels/ninja_diff.py ab-build --kernels on|off --name NAME
        [--variant plain|rec] [--lifted private/generated/s8/pc-double/linux-obj]
    .venv/bin/python tools/s8/native_kernels/ninja_diff.py ab-run --build NAME --label LABEL
        [--instrument] [--procmon] [--env VAR=VALUE ...]

`--variant nomarker` links without the double mode's marker (`sa2_f80_state_double`), as the
exact build is: every call must then take the lifted body. `--arch aarch64` builds the harness
for aarch64-linux-musl from the Android game's double-mode lifted objects
(private/generated/s8/android/objects-double, the phone's own code) with the Android link's
-ffp-contract=off, and runs it under the pinned qemu-aarch64; `--arch x86_64-windows` links the
Windows release's objects (private/generated/s8/pc-double/windows-obj) with tools/s7/win_link.py's
lld-link line and runs it under Proton's wine in a prefix of its own. The double mode's shims are
compiled from their source (tools/s8/f80_shims_double.c) for every arch. Builds land under
private/generated/s8/ninja-native/harness/<arch>-<variant>/.

The lifted objects must be current, or the build is refused by name: the build's summary must be
the double mode, built by this checkout's tools/s8/f80_double.py and from this checkout's
tools/s8/f80_shims_double.c (and every other input its `deps` records, when it records them),
and each kernel's record in build.jsonl must name IR whose sha256 is still the one it was
compiled from.

`ab-build` links Metal Harbor's replay as tools/s8/perf_game.py does, with sa2_ninja_math.c and
its three wraps in tools/s4/link_s4.py's lists (`--kernels on`, added when the tree's link_s4.py
does not have them yet) or left out (`--kernels off`); everything else (the CRI kernels
included) as the tree has it. `ab-run` runs one such build through perf_game's run under
tools/slot.sh, into private/generated/s8/ninja-native/runs/<LABEL>/, optionally with the S8
serial study's per-thread /proc monitor.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
for p in ("tools/s3/whole_image", "tools/spike", "tools/s4"):
    sys.path.insert(0, str(ROOT / p))
import link_s4 as L4  # noqa: E402  read-only: the runtime's compile

GEN = ROOT / "private" / "generated"
LIFTED = GEN / "s8" / "pc-double" / "linux-obj"
ARCHES = {
    # arch: (zig target, the double-mode lifted objects, extra runtime flags, the runner prefix)
    "x86_64": ("x86_64-linux-musl", LIFTED, [], []),
    "aarch64": ("aarch64-linux-musl", GEN / "s8" / "android" / "objects-double",
                ["-ffp-contract=off"],
                [str(ROOT / "private/predecessor-tools/qemu/root/usr/bin/qemu-aarch64")]),
    # The Windows release's code: tools/s7/win_link.py's triple and WIN_FLAGS, its double-mode
    # lifted objects, run under the Proton wine tools/s7/wine_run.py runs, in a prefix of its own.
    "x86_64-windows": ("x86_64-windows-gnu", GEN / "s8" / "pc-double" / "windows-obj",
                       ["-mno-ms-bitfields"],
                       [str(Path.home() / ".local/share/Steam/steamapps/common/"
                            "Proton - Experimental/files/bin/wine")]),
}
OUT = GEN / "s8" / "ninja-native"
HARNESS = OUT / "harness"
WINE_PREFIX = OUT / "wine-prefix"
RUNS = OUT / "runs"
PASS = ROOT / "tools" / "s8" / "f80_double.py"
SHIMS_SRC = ROOT / "tools" / "s8" / "f80_shims_double.c"
RUNTIME_FILE = "sa2_ninja_math.c"
# The kernels' guest functions: the harness's `__real_` objects and the -wrap the link passes.
KERNELS = {"calcpoint": 0x00426CC0, "invert": 0x00424FF0, "multiply": 0x00426E40,
           "sin": 0x0042AAB0, "cos": 0x0042AC30, "translate": 0x00427470,
           "rotx": 0x004274E0, "roty": 0x004275B0, "rotz": 0x00427680}
# What the lifted kernels call besides the runtime: njInvertMatrix's scale.
CALLEES = (0x00425350,)
WRAPS = tuple(f"sub_{va:x}" for va in KERNELS.values())
PROCMON = GEN / "s8" / "serial-study" / "procmon.py"

# remill's State offsets by the field names runtime/sa2_ninja_math.c's `sa2_ninja_layout` uses,
# from remill's own header: the harness holds the runtime's table to these.
LAYOUT_CPP = r"""
#include <cstddef>
#include <cstring>
#include "remill/Arch/X86/Runtime/State.h"
#if defined(__clang__)
#pragma clang diagnostic ignored "-Winvalid-offsetof"
#endif
#define F(name, member) {name, offsetof(State, member)}
extern "C" int sa2_ninja_remill_offset(const char *field) {
    static const struct { const char *name; size_t offset; } table[] = {
        F("aflag.cf", aflag.cf), F("aflag.pf", aflag.pf), F("aflag.af", aflag.af),
        F("aflag.zf", aflag.zf), F("aflag.sf", aflag.sf), F("aflag.df", aflag.df),
        F("aflag.of", aflag.of), F("seg.ds", seg.ds), F("seg.cs", seg.cs),
        F("addr.ss_base", addr.ss_base), F("addr.es_base", addr.es_base),
        F("addr.ds_base", addr.ds_base), F("gpr.rax", gpr.rax), F("gpr.rbx", gpr.rbx),
        F("gpr.rcx", gpr.rcx), F("gpr.rdx", gpr.rdx), F("gpr.rsi", gpr.rsi), F("gpr.rdi", gpr.rdi),
        F("gpr.rsp", gpr.rsp), F("gpr.rbp", gpr.rbp), F("gpr.rip", gpr.rip),
        F("st.elems[0].val", st.elems[0].val), F("st.elems[1].val", st.elems[1].val),
        F("sw.c0", sw.c0), F("sw.c1", sw.c1), F("sw.c2", sw.c2), F("sw.c3", sw.c3),
        F("sw.pe", sw.pe), F("sw.ue", sw.ue), F("sw.oe", sw.oe), F("sw.ze", sw.ze),
        F("sw.de", sw.de), F("sw.ie", sw.ie),
        F("x87.fxsave.cwd", x87.fxsave32.cwd), F("x87.fxsave.swd", x87.fxsave32.swd),
        F("x87.fxsave.fop", x87.fxsave32.fop), F("x87.fxsave.ip", x87.fxsave32.ip),
        F("x87.fxsave.cs", x87.fxsave32.cs), F("x87.fxsave.dp", x87.fxsave32.dp),
        F("x87.fxsave.ds", x87.fxsave32.ds),
    };
    for (const auto &e : table)
        if (!std::strcmp(e.name, field)) return static_cast<int>(e.offset);
    return -1;
}
extern "C" unsigned sa2_ninja_remill_state_size(void) { return sizeof(State); }
"""


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def lifted_objects(root: Path = LIFTED) -> tuple[list[Path], list[str]]:
    """The kernels' (and their callees') lifted objects from a double-mode build, each checked
    current, and the inputs of the build's pass that its summary does not record (built before
    perf_f80.py recorded `deps`), named so a reader knows they went unchecked."""
    if not (root / "build.jsonl").is_file() or not (root / "summary.json").is_file():
        raise SystemExit(f"no double-mode lifted build at {root} (tools/s8/perf_f80.py build "
                         f"--mode double)")
    s = json.loads((root / "summary.json").read_text())
    problems = []
    if s.get("f80_mode") != "double":
        problems.append(f"it is not a double-mode build: {s.get('f80_mode')}")
    shims = s.get("shims") or {}
    named = {"pass": (s.get("pass"), s.get("pass_sha256")),
             "shims source": (shims.get("source"), shims.get("source_sha256"))}
    for what, (path, want) in named.items():
        if path != str((PASS if what == "pass" else SHIMS_SRC).relative_to(ROOT)):
            problems.append(f"its {what} is {path}, not the double mode's")
        elif sha256(ROOT / path) != want:
            problems.append(f"its {what} {path} was {want}, this checkout's is {sha256(ROOT / path)}")
    unchecked = []
    deps = s.get("deps")
    if deps is None:
        unchecked = ["tools/s7/f80_legalize.py (imported by the pass)",
                     "tools/s7/f80_shims.c (included by the shims)"]
    else:
        for path, want in sorted(deps.items()):
            have = sha256(ROOT / path) if (ROOT / path).is_file() else None
            if have != want:
                problems.append(f"its input {path} was {want}, this checkout's is {have}")
    want = {f"0x{va:08x}" for va in (*KERNELS.values(), *CALLEES)}
    recs = {}
    for line in (root / "build.jsonl").read_text().splitlines():
        r = json.loads(line)
        if r.get("va") in want:
            recs[r["va"]] = r
    out = []
    for va in sorted(want):
        r = recs.get(va)
        if not r or not r.get("compiled"):
            problems.append(f"build.jsonl has no compiled object for {va}")
            continue
        ir = ROOT / r["ir"]
        if not ir.is_file() or sha256(ir) != r["ir_sha256"]:
            problems.append(f"{r['object']} was compiled from an older {r['ir']}")
        out.append(root / r["object"])
    if problems:
        raise SystemExit(f"the double-mode lifted build {root}: " + "; ".join(problems)
                         + "; rebuild it (tools/s8/perf_f80.py build --mode double, or "
                           "tools/s8/android_game.py compile --f80 double)")
    return out, unchecked


def run(cmd: list[str], cwd: Path | None = None) -> None:
    env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(HARNESS / "zig-global"),
               ZIG_LOCAL_CACHE_DIR=str(HARNESS / "zig-local"))
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, env=env)
    if p.returncode:
        raise SystemExit(f"failed ({p.returncode}): {' '.join(cmd)}\n{p.stderr[-4000:]}")


def build(variant: str = "double", arch: str = "x86_64") -> Path:
    """The harness binary for `variant` ("double", or "nomarker": no sa2_f80_state_double) on
    `arch` (ARCHES)."""
    if variant not in ("double", "nomarker") or arch not in ARCHES:
        raise SystemExit(f"unknown variant {variant} or arch {arch}")
    triple, root, rt_extra, _ = ARCHES[arch]
    target = ["-target", triple]
    d = HARNESS / f"{arch}-{variant}"
    d.mkdir(parents=True, exist_ok=True)
    zig = str(L4.S.ZIG)
    lifted, _ = lifted_objects(root)
    # The runtime file as link_s4.py's compile phase compiles a RUNTIME_C source (with the
    # Android link's -ffp-contract=off for aarch64).
    rt_flags = target + [L4.OPT, "-w", *rt_extra, "-c", "-I", str(L4.RUNTIME),
                         "-I", str(L4.C.REMILL_INC)]
    objs = [d / "sa2_ninja_math.o", d / "ninja_diff.o", d / "ninja_layout.o"]
    run([zig, "cc", *rt_flags, str(L4.RUNTIME / RUNTIME_FILE), "-o", str(objs[0])])
    hflags = target + rt_extra + ["-O2", "-Wall", "-Wextra", "-Werror"]
    run([zig, "cc", *hflags, "-c", str(HERE / "ninja_diff.c"), "-o", str(objs[1])])
    layout = d / "ninja_layout.cpp"
    if not layout.is_file() or layout.read_text() != LAYOUT_CPP:
        layout.write_text(LAYOUT_CPP)
    run([zig, "c++", *target, *rt_extra, "-O2", "-std=c++17", "-w", "-I", str(L4.C.REMILL_INC),
         "-c", str(layout), "-o", str(objs[2])])
    # The double mode's shims from their source; for the exact build's view, with the marker
    # renamed away: the lifted objects still reach sa2_df80_pc_round_slow, and the runtime's weak
    # reference to sa2_f80_state_double stays NULL.
    shims = d / "f80_shims.o"
    defs = ["-Dsa2_f80_state_double=sa2_f80_state_double_renamed_away"] if variant == "nomarker" else []
    run([zig, "cc", *target, "-O2", "-w", "-ffp-contract=off", *defs, "-c", str(SHIMS_SRC),
         "-o", str(shims)])
    if "windows" in triple:
        # tools/s7/win_link.py's own link: zig's lld-link line for its probe, with `-wrap:` for
        # each wrap and these objects in the probe's place (zig's -Wl,-wrap= does not reach
        # lld-link's COFF driver).
        sys.path.insert(0, str(ROOT / "tools" / "s7"))
        import win_link as WL  # noqa: PLC0415  read-only
        exe = d / "ninja_diff.exe"
        probe = WL.probe_line(d / "probe")
        line = WL.link_line(probe, d / "probe" / "probe.o", objs + lifted + [shims], exe,
                            list(WRAPS))
        rsp = d / "link.rsp"
        rsp.write_text(WL.response_file(line))
        run([zig, "lld-link", f"@{rsp}"], cwd=d)
        return exe
    exe = d / "ninja_diff"
    run([zig, "c++", *target, "-O2", "-static", *[f"-Wl,-wrap={w}" for w in WRAPS],
         *map(str, objs + lifted + [shims]), "-o", str(exe)])
    return exe


def runner(exe: Path) -> tuple[list[str], dict]:
    """The command prefix and environment that run a harness binary: qemu for an aarch64 one,
    wine (no display, no debug channels, its own prefix) for a Windows one."""
    arch = exe.parent.name.rsplit("-", 1)[0]
    env = dict(os.environ)
    if arch == "x86_64-windows":
        WINE_PREFIX.mkdir(parents=True, exist_ok=True)
        env = {k: v for k, v in env.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
        env.update(WINEDEBUG="-all", WINEDLLOVERRIDES="mscoree,mshtml=;winemenubuilder.exe=d",
                   WINEPREFIX=str(WINE_PREFIX))
    return (ARCHES[arch][3] if arch in ARCHES else []), env


def run_harness(exe: Path, *args: str, timeout: float = 1800) -> dict:
    prefix, env = runner(exe)
    p = subprocess.run([*prefix, str(exe), *args], capture_output=True, text=True,
                       timeout=timeout, env=env)
    if p.returncode:
        raise SystemExit(f"{exe} {' '.join(args)} failed ({p.returncode}): {p.stderr[-3000:]}")
    return json.loads(p.stdout)


def cmd_diff(a) -> int:
    exe = build(a.variant, a.arch)
    _, unchecked = lifted_objects(ARCHES[a.arch][1])
    res = {"variant": a.variant, "arch": a.arch, "binary": str(exe), "lifted": str(ARCHES[a.arch][1]),
           "unchecked_inputs": unchecked, "layout": run_harness(exe, "layout"), "kernels": {}}
    for k in [k for k in a.kernels.split(",") if k]:
        res["kernels"][k] = run_harness(exe, k, str(a.cases), str(a.seed))
        print(json.dumps(res["kernels"][k]), flush=True)
    ok = res["layout"]["ok"] and all(r["mismatches"] == 0 for r in res["kernels"].values())
    res["ok"] = ok
    if a.json:
        a.json.write_text(json.dumps(res, indent=1) + "\n")
    print("NINJA_DIFF_OK" if ok else "NINJA_DIFF_FAILED")
    return 0 if ok else 1


def cmd_bench(a) -> int:
    exe = build("double", a.arch)
    for k in KERNELS:
        print(json.dumps(run_harness(exe, "bench", k, str(a.calls))), flush=True)
    return 0


# ------------------------------------------------------------------ the game A/B


def load_link_with(kernels: str):
    """perf_game's link_s4 with this file and its wraps in (`on`) or out (`off`)."""
    sys.path.insert(0, str(ROOT / "tools" / "s8"))
    import perf_game as PG  # noqa: PLC0415
    load = PG.load_link

    def patched():
        link = load()
        if kernels == "off":
            link.RUNTIME_C = [s for s in link.RUNTIME_C if s != RUNTIME_FILE]
            link.PRODUCTION_WRAPS = [w for w in link.PRODUCTION_WRAPS if w not in WRAPS]
        else:
            if RUNTIME_FILE not in link.RUNTIME_C:
                link.RUNTIME_C = list(link.RUNTIME_C) + [RUNTIME_FILE]
            link.PRODUCTION_WRAPS = list(link.PRODUCTION_WRAPS) + [
                w for w in WRAPS if w not in link.PRODUCTION_WRAPS]
        return link
    return PG, patched


def cmd_ab_build(a) -> int:
    PG, patched = load_link_with(a.kernels)
    PG.load_link = patched
    ns = argparse.Namespace(variant=a.variant, scene="metalharbor", name=a.name,
                            lifted=a.lifted, rebuild=a.rebuild, shims=None)
    rc = PG.cmd_build(ns)
    info_p = PG.PG / "builds" / f"{a.name}.json"
    if rc == 0 and info_p.is_file():
        info = json.loads(info_p.read_text())
        info["ninja_kernels"] = a.kernels
        info_p.write_text(json.dumps(info, indent=1) + "\n")
    return rc


def cmd_ab_run(a) -> int:
    sys.path.insert(0, str(ROOT / "tools" / "s8"))
    import perf_game as PG  # noqa: PLC0415
    RUNS.mkdir(parents=True, exist_ok=True)
    info = json.loads((PG.PG / "builds" / f"{a.build}.json").read_text())
    procs = []
    if a.procmon:
        mon = RUNS / f"{a.label}.procmon.jsonl"
        procs.append(subprocess.Popen([sys.executable, str(PROCMON), info["binary"], str(mon),
                                       "--wait-s", "7200"]))
    rel = os.path.relpath(RUNS / a.label, PG.PG / "runs")
    cmd = [str(ROOT / "tools" / "slot.sh"), sys.executable, "-u", str(ROOT / "tools/s8/perf_game.py"),
           "run", "--build", a.build, "--label", rel]
    cmd += ["--instrument"] if a.instrument else []
    for e in a.env or []:
        cmd += ["--env", e]
    t0 = time.time()
    rc = subprocess.run(cmd, cwd=ROOT).returncode
    for p in procs:
        try:
            p.wait(timeout=30)
        except subprocess.TimeoutExpired:
            p.kill()
    res = {"label": a.label, "build": a.build, "rc": rc, "wall_seconds": round(time.time() - t0, 1),
           "kernels": info.get("ninja_kernels")}
    rj = RUNS / a.label / "result.json"
    if rj.is_file():
        res["perf_game"] = json.loads(rj.read_text())
    stderr = RUNS / a.label / "stderr.txt"
    if stderr.is_file():
        res["stats"] = [l for l in stderr.read_text(errors="replace").splitlines()
                        if l.startswith(("sa2_ninja_math:", "sa2_cri_dsp:"))]
    (RUNS / f"{a.label}.ab.json").write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps(res, indent=1))
    return rc


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("bench", "ab-build", "ab-run"):
        ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
        sub = ap.add_subparsers(dest="cmd", required=True)
        b = sub.add_parser("bench")
        b.add_argument("--arch", default="x86_64", choices=sorted(ARCHES))
        b.add_argument("--calls", type=int, default=2_000_000)
        ab = sub.add_parser("ab-build")
        ab.add_argument("--kernels", choices=("on", "off"), required=True)
        ab.add_argument("--variant", choices=("plain", "rec"), default="plain")
        ab.add_argument("--name", required=True)
        ab.add_argument("--lifted", default=str(LIFTED))
        ab.add_argument("--rebuild", action="store_true")
        r = sub.add_parser("ab-run")
        r.add_argument("--build", required=True)
        r.add_argument("--label", required=True)
        r.add_argument("--instrument", action="store_true")
        r.add_argument("--procmon", action="store_true")
        r.add_argument("--env", action="append")
        a = ap.parse_args(argv)
        return {"bench": cmd_bench, "ab-build": cmd_ab_build, "ab-run": cmd_ab_run}[a.cmd](a)
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cases", type=int, default=1_000_000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--kernels", default=",".join(KERNELS))
    ap.add_argument("--variant", default="double", choices=("double", "nomarker"))
    ap.add_argument("--arch", default="x86_64", choices=sorted(ARCHES))
    ap.add_argument("--json", type=Path)
    return cmd_diff(ap.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
