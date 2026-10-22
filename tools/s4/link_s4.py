#!/usr/bin/env python3
"""Link an S4 driver against a whole-image tag, and run it.

Subtitle: `tools/s3/whole_image/link_main.py`, plus the runtime sources S4
adds, plus an incremental compile so a stub iteration is seconds.

Why this exists rather than a flag on the S3 tool: `link_main.py` hard-codes
its runtime file list (`guest_memory.c`, `function_table_impl.c`, `sa2_trap.c`,
`sa2_call.c`, `remill_runtime.cpp`) and S4 adds `sa2_imports.c` to it. That
tool belongs to a stage that is closed and is not edited here. Everything else
is the same shape on purpose, so a result from either is comparable with a
result from the other: the same target triple, the same `-O2`, the same
generated function table, the same per-phase checkpoint file.

Two differences that are not cosmetic:

  * **Incremental compile.** An object is rebuilt only when it is older than
    its source or than any runtime header. `function_table_whole.c` is 2 MB of
    generated table and takes minutes; recompiling it to add a Win32 stub is
    the difference between a ten-second loop and a ten-minute one. `--rebuild`
    forces the full compile, and the checkpoint records which objects were
    reused so a run can never quietly be a mix nobody chose.
  * **The run phase takes `--env`,** because the before column of this slice's
    measurement is `SA2_IMPORT_BIND=0` against the same binary.

`--exe-overlay` links the shipped executable's code-section overlay table
(runtime/sa2_exe_overlay.h; tools/s7/text_readset.py writes it), so the build
loads the shipped `sonic2app.exe` when SA2_EXE_KIND=shipped says that is the file
it was handed. A build without it loads the unpacked file exactly as before and
refuses the shipped one by name.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s4/link_s4.py --tag next+allarms \\
      --main harness/import_probe.c --name import.next+allarms \\
      --run private/exe/sonic2app-unpacked.exe \\
      --run=--json --run private/generated/s4/run/import.bound.json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "s3" / "whole_image"))
import common as C  # noqa: E402
import link_all as L  # noqa: E402

import spikelib as S  # tools/spike/spikelib.py, read-only  # noqa: E402
import tweak_sources as TS  # tools/s4/tweak_sources.py  # noqa: E402

OUT = C.GEN / "s4" / "link"
RUNTIME = C.ROOT / "runtime"
TARGET = ["-target", "x86_64-linux-musl"]
OPT = "-O2"
RUNTIME_C = ["guest_memory.c", "function_table_impl.c", "sa2_trap.c", "sa2_call.c",
             "sa2_imports.c", "sa2_files.c", "sa2_window.c", "sa2_iface.c",
             "sa2_d3d9.c", "sa2_dsound.c", "sa2_ddraw.c", "sa2_xaudio2.c", "sa2_threads.c",
             "sa2_dialog.c", "sa2_gdi.c", "sa2_module.c", "sa2_format.c", "sa2_codepage.c",
             "sa2_devprofile.c", "sa2_locale.c", "sa2_frameskip.c",
             "sa2_guest_call.c", "sa2_dinput.c", "sa2_wbem.c", "sa2_poll_handoff.c",
             "sa2_exe_overlay.c", "sa2_cri_dsp.c", "sa2_ninja_math.c", "sa2_widescreen.c",
             "sa2_crt_x87.c", "sa2_tweaks.c", "sa2_stall.c"]
# The tweaks' own files, runtime/tweaks/*.c, join RUNTIME_C after PRODUCTION_WRAPS below.
RUNTIME_CXX = ["remill_runtime.cpp", "sa2_emulate.cpp"]
# zig compiles with -mframe-pointer=all, which gave each guest-memory intrinsic in
# remill_runtime.cpp a push/mov/pop of rbp around a dozen instructions; nothing in the runtime
# walks frame pointers (unwinding is by CFI), so the C++ runtime omits them.
RUNTIME_CXX_FLAGS = ["-fomit-frame-pointer"]
# The shipped executable's code-section overlay (runtime/sa2_exe_overlay.h): the table
# tools/s7/text_readset.py writes, never tracked, since it holds code bytes of the game. A build
# links it only when it asks to (`--exe-overlay`), and then compiles it after the runtime and
# before the extras, so no `extra<k>_` object is renumbered by it. Its object is named by the
# table's sha256 (`exe_overlay_object`), so an object compiled from one table can never be
# reused for another, whatever the files' times say: the freshness test below is by time, and a
# release table and a negative control are the same size and the same path apart. Linking it is
# what lets the runtime load the shipped executable (SA2_EXE_KIND=shipped); a build without it
# refuses that by name and loads the unpacked one exactly as before.
EXE_OVERLAY_TABLE = C.GEN / "s7" / "exe-overlay" / "sa2_exe_overlay_table.c"


def exe_overlay_object(sha256: str) -> str:
    """The compile job's name for an overlay table with this sha256; its object is the stem.
    A link that reproduces a recorded one (tools/s4/build_time_jobs.py) names the object from
    the record's `exe_overlay.sha256`."""
    return f"exe_overlay_{sha256[:16]}.c"


def exe_overlay_identity(table: Path) -> dict:
    """What a build records of its overlay table: the path and the sha256 of its bytes now."""
    return {"table": str(table), "sha256": C.sha256(table)}
# Lifted functions the runtime itself wraps, in every build this tool links:
# `-Wl,-wrap=<symbol>` is passed whatever --wrap asks for, and the runtime
# source in RUNTIME_C defines `__wrap_<symbol>`. sub_427ee0 is the guest's
# AsynDo done query, the guest-poll hand-off point (runtime/sa2_poll_handoff.c,
# runtime/sa2_threads.h sa2_sched_poll): without it a load whose wait is a pure
# spin hangs (the NEW FILE hang, 2026-10-02). The wrapper's reference to
# `__real_<symbol>` is strong and the wrapper is kept through section garbage
# collection (`retain`), so a link that compiles the runtime and omits the flag
# fails ("undefined symbol: __real_sub_427ee0") rather than linking a build
# without the rule (Documentation/scripts/tests/test_poll_handoff.py). Every
# other tool that links the runtime and the lifted image passes these too
# (tools/s4/build_cost.py; tools/s4/build_time_jobs.py replays the recorded
# `wrapped`, which includes them).
#
# sub_842500, sub_843400, sub_843460 and sub_8418b0 are the CRI Sound Renderer's biquad, damped
# comb, reverb network and mix-accumulate (runtime/sa2_cri_dsp.c): native bodies, bit for bit the
# double mode's lifted ones, which hand every call they cannot reproduce exactly, and every call
# in the exact build, to `__real_` (tools/s8/native_kernels/cri_dsp_diff.py holds them to it).
#
# sub_43ce20 and sub_441ba0 are the level loop and Control (runtime/sa2_frameskip.c): with play's
# SA2_PLAY_FRAMESKIP=2, 3 or auto a stage frame runs N game ticks, its later passes see no new
# press, and the clock moves N frames; unset, and in every replay, each wrap only calls `__real_`
# (Documentation/scripts/tests/test_frameskip.py). They go before ReadConfig so the HUD sites stay
# the list's tail (test_widescreen.py).
#
# sub_426cc0, sub_424ff0 and sub_426e40 are the main thread's Ninja njCalcPoint, njInvertMatrix
# and njMultiMatrix, and sub_42aab0, sub_42ac30, sub_427470, sub_4274e0, sub_4275b0 and sub_427680
# its njSin, njCos, njTranslate_ and njRotateX_/Y_/Z_ (runtime/sa2_ninja_math.c): native bodies at
# PC24, bit for bit the double mode's lifted ones, on the same terms
# (tools/s8/native_kernels/ninja_diff.py holds them to it).
#
# sub_859ac8, sub_859648 and sub_829008 are the CRT's x87 tan, sin and cos bodies
# (runtime/sa2_crt_x87.c): each wrap clears C2, which remill's FPTAN, FSIN and FCOS leave as the
# guest's earlier code left it, then calls `__real_`; tools/s8/x87_fallback_probe.py holds them to
# the host libm with C2 clear and set on entry.
#
# sub_405310 is ReadConfig (runtime/sa2_widescreen.c): play's SA2_PLAY_RES or SA2_PLAY_ASPECT
# rewrites the config's Width and Height after it, so the game draws at the device's aspect; with
# neither set, and in every replay, the wrap only calls `__real_`
# (Documentation/scripts/tests/test_widescreen.py).
#
# After it, the gameplay HUD's layout functions and the canvas-to-pixel, screen, projection and
# background functions they can reach (runtime/sa2_widescreen.c HUD43_SITES, in its order; one
# literal list, as tools/s7/arm64_link.py reads it with ast.literal_eval): with play's
# SA2_PLAY_HUD43=1 on a screen wider than 4:3 the HUD's layout runs with HorizontalResolution
# read as VerticalResolution x 4/3 and the others with the real one; with it unset, and in every
# replay, each wrap only calls `__real_` (test_widescreen.py holds the list to the file).
#
# Last, the play tweaks' wraps (runtime/sa2_tweaks.h): each tweak's file, runtime/tweaks/<name>.c,
# names the lifted functions it wraps in its TWEAK_WRAPS line, and tools/s4/tweak_sources.py adds
# the files to RUNTIME_C and their functions here, below, so a tweak adds no line to this tool.
# With its tweak off, and in every replay, each wrap only calls `__real_`
# (Documentation/scripts/tests/test_tweaks.py). The two lists above stay literals: arm64_link.py
# reads them with ast.literal_eval and build_cost.py with a regular expression, and both add the
# tweaks from tweak_sources.py as these lines do.
PRODUCTION_WRAPS = ["sub_427ee0", "sub_842500", "sub_843400", "sub_843460", "sub_8418b0",
                    "sub_43ce20", "sub_441ba0",
                    "sub_426cc0", "sub_424ff0", "sub_426e40",
                    "sub_42aab0", "sub_42ac30", "sub_427470", "sub_4274e0", "sub_4275b0", "sub_427680",
                    "sub_859ac8", "sub_859648", "sub_829008", "sub_405310",
                    "sub_44e9c0", "sub_7945c0", "sub_794cb0", "sub_44d360", "sub_61bba0",
                    "sub_61b6c0", "sub_4526d0", "sub_6f41b0", "sub_420210", "sub_420390",
                    "sub_4200f0", "sub_77e980", "sub_42b230", "sub_41b0c0"]
# The runtime beside this file, not RUNTIME (common.ROOT's): tools/s7/release_build.py imports a
# snapshot's link_s4 and points RUNTIME at the snapshot's runtime/ after the import.
RUNTIME_C += TS.sources(Path(__file__).resolve().parents[2] / "runtime")
PRODUCTION_WRAPS += TS.wraps(Path(__file__).resolve().parents[2] / "runtime", PRODUCTION_WRAPS)


def newest_header() -> float:
    return max((p.stat().st_mtime for p in RUNTIME.glob("*.h")), default=0.0)


def phase_compile(state: dict, name: str, tag: str, main: Path, rebuild: bool,
                  extra: list[Path] | None = None, overlay: Path | None = None) -> None:
    bdir = OUT / "mains" / name / "obj"
    bdir.mkdir(parents=True, exist_ok=True)
    zig = str(S.ZIG)
    cflags = [zig, "cc"] + TARGET + [OPT, "-w", "-c", "-I", str(RUNTIME),
                                     "-I", str(C.REMILL_INC)]
    table_c = OUT.parent.parent / "s3" / "link" / tag / "function_table_whole.c"
    if not table_c.is_file():
        raise SystemExit(f"no generated table for tag {tag}: {table_c}\n"
                         f"run tools/s3/whole_image/link_all.py --tag {tag} --phase table first")
    hdr = newest_header()
    log, reused = {}, []
    jobs: list[tuple[str, Path, list[str]]] = []
    for src in RUNTIME_C:
        jobs.append((src, RUNTIME / src, cflags))
    for src in RUNTIME_CXX:
        jobs.append((src, RUNTIME / src, [zig, "c++"] + TARGET +
                     [OPT, "-w", "-std=c++17", *RUNTIME_CXX_FLAGS, "-c", "-I", str(RUNTIME),
                      "-I", str(C.REMILL_INC)]))
    jobs.append(("function_table_whole.c", table_c, cflags))
    ident = exe_overlay_identity(overlay) if overlay is not None else None
    if ident is not None:
        jobs.append((exe_overlay_object(ident["sha256"]), overlay, cflags))
    for k, x in enumerate(extra or []):
        jobs.append((f"extra{k}_{x.stem}.c", x, cflags))
    jobs.append(("main.c", main, cflags))
    for src, path, flags in jobs:
        o = bdir / (Path(src).stem + ".o")
        # A source outside runtime/ can include headers beside it -- the tick
        # hook includes harness/replay/tick_fields.h -- and an object older
        # than one of those is stale even though its own source is not.
        near = max((h.stat().st_mtime for h in path.parent.glob("*.h")), default=0.0)
        fresh = (not rebuild and o.is_file()
                 and o.stat().st_mtime >= path.stat().st_mtime
                 and o.stat().st_mtime >= hdr and o.stat().st_mtime >= near)
        if fresh:
            reused.append(src)
            log[src] = {"rc": 0, "reused": True}
            continue
        if o.is_file():
            o.unlink()          # a failed compile must not leave a stale object (S3 slice 3)
        log[src] = L.run(flags + [str(path), "-o", str(o)], timeout=7200)
        log[src]["reused"] = False
    for v in log.values():
        v.pop("stdout", None)
    state["compile"] = log
    state["compile_extra"] = [str(x) for x in (extra or [])]
    state["compile_exe_overlay"] = ident
    state["compile_reused"] = reused
    state["compile_ok"] = all(v["rc"] == 0 for v in log.values())
    print("compile: " + ", ".join(f"{k}{'(cached)' if v.get('reused') else ''} rc={v['rc']}"
                                  for k, v in log.items()), flush=True)
    for k, v in log.items():
        if v["rc"]:
            print(f"--- {k} ---\n{v['stderr_tail'][-4000:]}", flush=True)


def phase_link(state: dict, name: str, tag: str, drop: list[str],
               extra: list[Path] | None = None, extra_o: list[Path] | None = None,
               wrap: list[str] | None = None, overlay: Path | None = None) -> None:
    if not state.get("compile_ok"):
        raise SystemExit("refusing to link: the compile phase failed")
    # The table as it is now, against the one the compile phase compiled: its path and its
    # bytes, so a table rewritten between the phases is refused rather than mislabelled. The
    # overlay helpers are named only when a build asks for an overlay: test_link_all_nofile.py
    # runs this function alone, without the rest of the module.
    compiled = state.get("compile_exe_overlay")
    ident = exe_overlay_identity(overlay) if overlay is not None else None
    if compiled != ident:
        raise SystemExit(f"refusing to link: the compile phase built the exe overlay {compiled}, "
                         f"this link asks for {ident}; compile again with the same --exe-overlay")
    bdir = (OUT / "mains" / name / "obj").resolve()
    objdir = (C.BUILD / tag).resolve()
    binary = (OUT / "mains" / name / "sa2_main").resolve()
    dropped = set(drop)
    objs = sorted(p.name for p in objdir.glob("ir_*.o") if p.name not in dropped)
    absent = sorted(d for d in dropped if not (objdir / d).is_file())
    if absent:
        raise SystemExit(f"--drop names objects that are not in {objdir}: {absent}")
    names = (RUNTIME_C + RUNTIME_CXX + ["function_table_whole.c"]
             + ([exe_overlay_object(ident["sha256"])] if ident is not None else [])
             + [f"extra{k}_{x.stem}.c" for k, x in enumerate(extra or [])] + ["main.c"])
    base = [str(bdir / (Path(s).stem + ".o")) for s in names] + [str(x) for x in (extra_o or [])]
    # `-wrap=sub_X` sends every reference to a lifted function -- a direct call
    # from lifted code, the function-table row, and under lld even a call made
    # inside the object that defines it (a toy link showed it, 2026-09-22) --
    # to `__wrap_sub_X`, which reaches the lifted body as `__real_sub_X`.
    # It is how a host hook runs at a guest function's exit without editing
    # lifted output. A wrapped symbol with no `__wrap_` definition fails the
    # link, so a hook cannot be silently absent.
    #
    # One dash, not two. The pinned zig 0.15.2 forwards `-wrap` to lld and
    # refuses `--wrap` ("error: unsupported linker arg: --wrap"); the refusal
    # was first read as zig having no wrap at all. Both spellings were tried on
    # a toy link on 2026-09-22 -- see
    # agents-journals/s4/2026-09-22-tick-replay-hook.md.
    #
    # PRODUCTION_WRAPS come first and always: the runtime wraps them in every
    # build. A hook asking to wrap one too would define a second `__wrap_`, so
    # it is refused here by name rather than by the linker's duplicate symbol.
    clash = sorted(set(wrap or []) & set(PRODUCTION_WRAPS))
    if clash:
        raise SystemExit(f"--wrap {clash}: the runtime wraps these in every build "
                         f"(PRODUCTION_WRAPS, runtime/sa2_poll_handoff.c); a hook cannot")
    wrapped = PRODUCTION_WRAPS + list(wrap or [])
    wraps = [f"-Wl,-wrap={w}" for w in wrapped]
    cmd = [str(S.ZIG), "c++"] + TARGET + [OPT] + wraps + base + objs + ["-o", str(binary)]
    print(f"link {name}: {len(objs)} lifted objects + {len(base)} runtime/driver objects",
          flush=True)
    t0 = time.time()
    r = L.run(cmd, cwd=objdir)
    r.pop("stdout", None)
    # `wrapped` is every -wrap the link passed, in the order it passed them, so
    # tools/s4/build_time_jobs.py reproduces this link from the record alone.
    r.update(objects_lifted=len(objs), objects_dropped=sorted(dropped),
             objects_runtime=len(base), wrapped=wrapped,
             production_wrapped=list(PRODUCTION_WRAPS),
             exe_overlay=ident,
             wall_seconds=round(time.time() - t0, 2))
    if binary.exists() and r["rc"] == 0:
        # The unresolved path: a shipping worktree links `private/` back to
        # the main tree, so the resolved binary is outside the worktree's root.
        r["binary"] = str((OUT / "mains" / name / "sa2_main").relative_to(C.ROOT))
        r["binary_bytes"] = binary.stat().st_size
        r["sha256"] = C.sha256(binary)
    state["link"] = r
    print(f"link {name}: rc={r['rc']} {r['seconds']}s peak_rss={r['peak_child_rss_mb']} MB "
          f"binary={r.get('binary_bytes', 0) / 1e6:.1f} MB", flush=True)
    if r["rc"] != 0:
        print(r["stderr_tail"][-6000:], flush=True)


def phase_run(state: dict, name: str, argv: list[str], env: dict[str, str]) -> None:
    binary = (OUT / "mains" / name / "sa2_main").resolve()
    if not binary.exists():
        state["run"] = {"ok": False, "why": "no binary"}
        return
    t0 = time.time()
    p = subprocess.run([str(binary)] + argv, capture_output=True, text=True,
                       timeout=1800, env=dict(os.environ, **env))
    r = {"rc": p.returncode, "seconds": round(time.time() - t0, 2), "argv": argv,
         "env": env, "stderr_tail": p.stderr[-6000:]}
    try:
        r["result"] = json.loads(p.stdout)
    except json.JSONDecodeError:
        r["result"] = None
        r["stdout_tail"] = p.stdout[-20000:]
    state.setdefault("runs", []).append(r)
    state["run"] = r
    print(f"run {name}: rc={r['rc']} {r['seconds']}s env={env}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--main", type=Path, required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--drop", action="append", default=[])
    ap.add_argument("--run", action="append", default=[])
    ap.add_argument("--env", action="append", default=[])
    ap.add_argument("--extra-o", action="append", default=[], type=Path,
                    help="an already-compiled object to link into the driver, e.g. a block "
                         "lifted by tools/s4/lift_extra.py")
    ap.add_argument("--extra-c", action="append", default=[], type=Path,
                    help="an extra C source to compile into the driver, e.g. a generated "
                         "missing-block edge table from tools/s4/mb_edges.py")
    ap.add_argument("--wrap", action="append", default=[],
                    help="a lifted symbol (sub_XXXXXX) whose references go to __wrap_<symbol>, "
                         "defined by an --extra-c; repeatable")
    ap.add_argument("--exe-overlay", nargs="?", type=Path, default=None,
                    const=EXE_OVERLAY_TABLE,
                    help="link the shipped executable's code-section overlay table "
                         f"(default {EXE_OVERLAY_TABLE.relative_to(C.ROOT)}, written by "
                         "tools/s7/text_readset.py), so the build can load the shipped "
                         "executable under SA2_EXE_KIND=shipped")
    ap.add_argument("--rebuild", action="store_true",
                    help="recompile every object, including the 2 MB generated table")
    ap.add_argument("--phase", default="all", choices=["all", "compile", "link", "run"])
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"main.{args.name}.json"
    state = json.loads(path.read_text()) if path.is_file() else {}
    state.update(name=args.name, tag=args.tag, main=str(args.main),
                 runtime_sources=RUNTIME_C + RUNTIME_CXX,
                 machine={"nproc": os.cpu_count(),
                          "toolchain": f"zig cc/c++ {' '.join(TARGET)} {OPT}"})
    env = dict(kv.split("=", 1) for kv in args.env)
    extra = [(C.ROOT / x).resolve() for x in args.extra_c]
    for x in extra:
        if not x.is_file():
            raise SystemExit(f"--extra-c names a file that is not there: {x}")
    extra_o = [(C.ROOT / x).resolve() for x in args.extra_o]
    for x in extra_o:
        if not x.is_file():
            raise SystemExit(f"--extra-o names a file that is not there: {x}")
    state["extra_c"] = [str(x) for x in extra]
    state["extra_o"] = [str(x) for x in extra_o]
    overlay = (C.ROOT / args.exe_overlay).resolve() if args.exe_overlay is not None else None
    if overlay is not None and not overlay.is_file():
        raise SystemExit(f"--exe-overlay names a table that is not there: {overlay}\n"
                         "run tools/s7/text_readset.py first")
    state["exe_overlay"] = str(overlay) if overlay is not None else None
    runners = {
        "compile": lambda: phase_compile(state, args.name, args.tag,
                                         (C.ROOT / args.main).resolve(), args.rebuild, extra,
                                         overlay),
        "link": lambda: phase_link(state, args.name, args.tag, args.drop, extra, extra_o,
                                   args.wrap, overlay),
        "run": lambda: phase_run(state, args.name, args.run, env),
    }
    for ph in (["compile", "link", "run"] if args.phase == "all" else [args.phase]):
        state["phase_in_flight"] = ph
        path.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n")
        runners[ph]()
        state["phase_in_flight"] = None
        path.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n")
    print(f"-> {path}")


if __name__ == "__main__":
    main()
