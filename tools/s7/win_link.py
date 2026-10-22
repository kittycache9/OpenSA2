#!/usr/bin/env python3
"""win_link: link the replay build for x86-64 Windows.

Subtitle: tools/s4/link_s4.py's compile and link, as tools/s4/loop_run.py drives them for
tools/s4/tick_replay.py's lifted run, for the triple x86_64-windows-gnu. The runtime, the
driver, the hook and the generated C are compiled with zig cc for the Windows triple; the
lifted objects are the ones tools/s7/cross_build.py compiled for it; and the link is
`zig lld-link` over a response file, with `-wrap:` for each wrapped symbol.

Why `zig lld-link` and not `zig c++`: the pinned zig 0.15.2 does not forward `-wrap` to the
COFF linker (`-Wl,-wrap=` links and leaves `__real_<symbol>` undefined; `--wrap`, `-wrap:`
and `/wrap:` are refused as unsupported linker arguments). lld-link itself has `-wrap:`. So
this tool asks zig for its own link line once, on a probe program that needs what the build
needs (the C++ runtime and winpthreads), and replaces the probe's objects and output with the
build's (`link_line`, `response_file`). The libraries, the CRT objects and the flags are then
zig's, as they would have been had zig forwarded the flag. A line this reader does not
recognise refuses the link.

What it links, and from where:
  runtime  link_s4.RUNTIME_C and RUNTIME_CXX, plus WINDOWS_C (runtime/sa2_win32.c, the Windows
           host's POSIX layer), from --src's runtime/ (default: this tree). Until the Windows
           runtime layer is in the tree, --src is a copy of the tree with the S7 Windows-port
           runtime patch applied (`patch -p1`; the patch is kept under private/, untracked); a
           runtime/ without sa2_win32.c refuses the link by name.
  driver   --main (harness/crt_probe.c), the generated function table, mb_edges and
           extra_functions of --tag, the runner's hooks, in loop_run.link_args' order, from
           --src. The runner's hooks and wraps are read from its own file: tick_replay.py's
           HOOK and WRAP (a tick replay, the default), or with --stage stage_run.py's HOOKS
           and WRAPS (a stage replay, Metal Harbor). --hook and --wrap replace them.
  lifted   --objects (default private/generated/s7/targets/x86_64-windows-gnu): cross_build's
           map and arms objects, which must be exactly the staged link directory's names, and
           its extras, in extra_functions.<tag>.json's order. Every one must be a compiled
           record of that build's build.jsonl whose IR still hashes to the record's
           ir_sha256, and the build's summary must name this triple.
  more     --extra-object (none by default): objects linked after the lifted ones, as given;
           a lifted image legalised by an f80 pass (tools/s8/perf_f80.py's double mode) needs
           its shims, the build's f80_shims.o. The record names each by path and sha256
           (`extra_objects`), and a link without one records nothing new.
  wraps    link_s4.PRODUCTION_WRAPS, then the runner's wraps
  manifest the application manifest (MANIFEST), compiled to a resource with `zig rc` and linked
           as RT_MANIFEST 1: the active code page UTF-8, so the narrow file calls of the
           runtime (fopen, stat, CreateFileA) and its own path conversions (runtime/sa2_win32.c)
           read every path as UTF-8, as on Linux, rather than in the machine's ANSI code page,
           which cannot spell most names. Windows 10 1903 and later honour it, and so does Wine
           (ACP 65001 with it and 1252 without, Proton's wine-11.0, 2026-10-11).
Every C and C++ source is compiled with link_s4's flags, the triple swapped, plus
-mno-ms-bitfields: mingw's default bitfield layout differs from the System V one that the
lifted IR's remill State and the runtime's structs were laid out under.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s7/win_link.py --out private/generated/s7/win/replay.next+allarms \\
      [--src <tree>] [--objects <cross_build root>] [--stage] [--exe-overlay [TABLE]] \\
      [--extra-object OBJ ...] [--map] [--jobs 4]
  .venv/bin/python tools/s7/win_link.py census [--objects <cross_build root>] \\
      [--replays <replays evidence>] [--host-build <host2_win_build --out>] [--census-out PATH]
Compiles run --jobs at a time (default 4, the machine's memory rule; the link is one process).
Outputs (under --out): obj/*.o, sa2_main.manifest, sa2_main.rc and sa2_main.res, link.rsp,
sa2_main.exe, link.record (JSON: inputs by sha256, the commands, rc, seconds, the binary's
sha256). Not named .json: the M7 census counts every *.json under private/generated as a
possible run record.

`census` writes the target's build census (Documentation/s7-green-means.md, `build`; schema
sa2-s7-build-census/v1), by default to the tracked CENSUS_TRACKED. Its binaries are the ones
the Windows replays evidence (REPLAYS_TRACKED) ran, one per registered replay, each held to its
link.record beside it, and `host`, host 2's Windows player as tools/s7/host2_win_build.py's
record names it. Each link must be reproducible from the tree as it stands: linked from the
cross build's log as it is now, every module's IR still at the log's ir_sha256 (`stale_ir`),
every compiled source at the sha256 the link recorded, the runner's hooks and wraps and this
tool's flags and manifest as they are now. Anything else refuses, naming what moved: link that
replay again, and run it again with tools/s7/wine_run.py --evidence. The lifted inputs are the
reach manifest's, as tools/s7/arm64_link.py's census takes them, since the cross build compiled
the reference's folded IR.
"""
from __future__ import annotations

import argparse
import ast
import concurrent.futures as cf
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "tools" / "s4"))
sys.path.insert(0, str(ROOT / "tools" / "s3" / "whole_image"))
sys.path.insert(0, str(ROOT / "tools" / "spike"))
import common as C  # noqa: E402  tools/s3/whole_image/common.py, read-only
import link_s4 as LS  # noqa: E402  tools/s4/link_s4.py, read-only
import spikelib as S  # noqa: E402  tools/spike/spikelib.py, read-only

TRIPLE = "x86_64-windows-gnu"
TARGET = ["-target", TRIPLE]
# Beside link_s4's flags: the System V bitfield layout (module docstring).
WIN_FLAGS = ["-mno-ms-bitfields"]
WINDOWS_C = ["sa2_win32.c"]
TAG = "next+allarms"
MAIN = "harness/crt_probe.c"
# The runners whose binaries this links: the hooks and wraps each links beside the runtime's
# own wraps, read from the runner's file without importing it (`runner_links`).
RUNNERS = {"tick": (ROOT / "tools" / "s4" / "tick_replay.py", "HOOK", "WRAP"),
           "stage": (ROOT / "tools" / "s4" / "stage_run.py", "HOOKS", "WRAPS")}
OBJECTS = C.GEN / "s7" / "targets" / TRIPLE
LINKDIR = C.GEN / "s4" / "link"
STAGED = C.BUILD / TAG
EXE = "sa2_main.exe"
PROBE_SRC = """// win_link's probe: what the replay build needs from zig's link line, and nothing else.
#include <pthread.h>
#include <cstdio>
#include <string>
static void *body(void *p) { return p; }
int main(int argc, char **argv) {
    pthread_t t;
    pthread_create(&t, nullptr, body, argv);
    pthread_join(t, nullptr);
    std::string s(argv[0]);
    std::printf("%zu %d\\n", s.size(), argc);
    return 0;
}
"""
# The link libraries the probe asks for beside zig c++'s own: winpthreads, which the runtime's
# threads (runtime/sa2_threads.c) need and which zig does not link by default on this triple.
PROBE_LIBS = ["-lpthread"]
# Flags in zig's line that name the probe's own outputs; each is rewritten for the build.
OUTPUT_FLAGS = ("-OUT:", "-PDB:", "-PDBALTPATH:", "-IMPLIB:")
# The application manifest (module docstring): the active code page, and nothing else, so the
# process is otherwise what it was without one.
MANIFEST = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <application xmlns="urn:schemas-microsoft-com:asm.v3">
    <windowsSettings>
      <activeCodePage xmlns="http://schemas.microsoft.com/SMI/2019/WindowsSettings">UTF-8</activeCodePage>
    </windowsSettings>
  </application>
</assembly>
"""
RT_MANIFEST = 24
MANIFEST_ID = 1          # CREATEPROCESS_MANIFEST_RESOURCE_ID: the one the loader reads for an exe
# The census (module docstring): what it writes, what it reads, and the cross build it names.
SELF = "tools/s7/win_link.py"
TARGET_NAME = "x86_64-windows"
CENSUS_SCHEMA = "sa2-s7-build-census/v1"
CENSUS_TRACKED = "Documentation/receipts/s7-x86_64-windows-build-v1.json"
REPLAYS_TRACKED = "Documentation/receipts/s7-x86_64-windows-replays-v1.json"
REPLAYS_SCHEMA = "sa2-s7-target-replays/v1"
CENSUS_OBJECTS = C.GEN / "s7" / "wave3" / "winport" / "targets" / TRIPLE
HOST_BUILD = C.GEN / "s7" / "host2-win"
HOST_RECORD = "host2_win_build.runrec"
HOST_SCHEMA = "sa2-recomp-s7-host2-win-build/v1"
HOST_PLAYER = "bin/sa2_player_gpu.exe"
LINK_SCHEMA = "sa2-s7-win-link/v1"


class Refused(SystemExit):
    pass


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def literal(path: Path, name: str):
    """A module-level literal assignment, read without importing the module."""
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name
                                                for t in node.targets):
            return ast.literal_eval(node.value)
    raise Refused(f"win_link: {path} has no literal {name}")


def runner_links(runner: str) -> tuple[list[str], list[str]]:
    """(hooks, wraps) the runner's binary links beside the runtime: tick_replay's HOOK and
    WRAP for a tick replay, stage_run's HOOKS and WRAPS for a stage replay."""
    path, hooks, wraps = RUNNERS[runner]
    h, w = literal(path, hooks), literal(path, wraps)
    return ([h] if isinstance(h, str) else list(h)), ([w] if isinstance(w, str) else list(w))


def flags(cxx: bool, src_root: Path) -> list[str]:
    """link_s4's compile command for one source, with the Windows triple and WIN_FLAGS."""
    head = [str(S.ZIG), "c++" if cxx else "cc"] + TARGET + WIN_FLAGS + [LS.OPT, "-w"]
    return head + (["-std=c++17", *LS.RUNTIME_CXX_FLAGS] if cxx else []) + [
        "-c", "-I", str(src_root / "runtime"), "-I", str(C.REMILL_INC)]


def sources(src_root: Path, tag: str, main: str, hooks: list[str],
            overlay: Path | None) -> list[tuple[str, Path, bool]]:
    """(object stem, source, is C++) in link_s4's link order: the runtime, the Windows layer,
    the function table, the overlay table, the extra C (loop_run.link_args' order: edges,
    extra functions, hooks), then the main."""
    rt = src_root / "runtime"
    out = [(Path(s).stem, rt / s, False) for s in LS.RUNTIME_C]
    out += [(Path(s).stem, rt / s, True) for s in LS.RUNTIME_CXX]
    out += [(Path(s).stem, rt / s, False) for s in WINDOWS_C]
    out.append(("function_table_whole", C.GEN / "s3" / "link" / tag / "function_table_whole.c",
                False))
    if overlay is not None:
        out.append((Path(LS.exe_overlay_object(sha256(overlay))).stem, overlay, False))
    extra = [LINKDIR / f"mb_edges.{tag}.c"]
    if extra_objects_state(tag).get("objects"):
        extra.append(LINKDIR / f"extra_functions.{tag}.c")
    extra += [src_root / h for h in hooks]
    out += [(f"extra{k}_{x.stem}", x, False) for k, x in enumerate(extra)]
    out.append(("main", src_root / main, False))
    missing = [str(p) for _, p, _ in out if not p.is_file()]
    if not (rt / WINDOWS_C[0]).is_file():
        raise Refused(f"win_link: {rt} has no {WINDOWS_C[0]}, the Windows host's runtime layer; "
                      f"pass --src a copy of the tree with the Windows runtime patch applied")
    if missing:
        raise Refused(f"win_link: sources not on disk: {missing}")
    import mb_edges  # noqa: PLC0415  tools/s4/mb_edges.py: a stale table would ship without an exception
    stale = mb_edges.not_in_force(extra[0], tag)
    if stale:
        raise Refused("win_link: the edge table is stale:\n  " + "\n  ".join(stale))
    return out


def extra_objects_state(tag: str) -> dict:
    p = LINKDIR / f"extra_functions.{tag}.json"
    return json.loads(p.read_text()) if p.is_file() else {}


def va_of(name: str) -> int:
    return int(name.split("_")[1].split(".")[0], 16)


def stale_ir(recs: list[dict]) -> list[str]:
    """The objects whose record's IR is no longer on disk at the record's ir_sha256: the lifted
    code moved on after the cross build, so its objects are not this tree's lifted code. A
    record without an IR hash is stale too, since nothing ties its object to an input."""
    out = []
    for r in recs:
        ir, want = r.get("ir"), r.get("ir_sha256")
        p = ROOT / ir if ir else None
        if not want or p is None or not p.is_file() or sha256(p) != want:
            out.append(r["object"])
    return out


def lifted_objects(root: Path, tag: str, staged: Path) -> tuple[list[Path], list[Path], dict]:
    """The cross-built objects the link reads: (extras in extra_functions.<tag>.json's order,
    map and arm objects sorted by name as link_s4 globs them, the build's identity). Refuses
    a build for another triple, a record that did not compile, a record whose IR changed since,
    an object no record names, and a set of map and arm names that is not the staged link
    directory's."""
    summary_p, log_p = root / "summary.json", root / "build.jsonl"
    if not summary_p.is_file() or not log_p.is_file():
        raise Refused(f"win_link: {root} holds no cross build (summary.json, build.jsonl); run "
                      f"tools/s7/cross_build.py --target {TRIPLE} first")
    summary = json.loads(summary_p.read_text())
    if summary.get("target") != TRIPLE:
        raise Refused(f"win_link: {root} is a cross build for {summary.get('target')!r}, not {TRIPLE}")
    recs = [json.loads(line) for line in log_p.read_text().splitlines() if line.strip()]
    bad = [r["object"] for r in recs if not r.get("compiled")]
    if bad:
        raise Refused(f"win_link: {len(bad)} modules of {root} did not compile: {bad[:3]}")
    stale = stale_ir(recs)
    if stale:
        raise Refused(f"win_link: {len(stale)} modules of {root} were compiled from IR that has "
                      f"changed or gone since (the record's ir_sha256): {stale[:3]}; run "
                      f"tools/s7/cross_build.py --target {TRIPLE} again")
    named = {r["object"] for r in recs}
    pops: dict[str, list[Path]] = {}
    for pop in ("map", "arms", "extra"):
        objs = sorted((root / pop).glob("ir_*.o"))
        stray = [o.name for o in objs if f"{pop}/{o.name}" not in named]
        if stray:
            raise Refused(f"win_link: {root / pop} holds objects its build.jsonl does not name: "
                          f"{stray[:3]}")
        pops[pop] = objs
    lifted = sorted(pops["map"] + pops["arms"], key=lambda p: p.name)
    names = [p.name for p in lifted]
    if len(set(names)) != len(names):
        raise Refused("win_link: a lifted object name is in both map/ and arms/")
    want = {p.name for p in staged.glob("ir_*.o")}
    if set(names) != want:
        raise Refused(f"win_link: the cross build's map and arm objects ({len(names)}) are not "
                      f"the staged link directory's ({len(want)}, {staged}); they differ by "
                      f"{sorted(set(names) ^ want)[:5]}")
    by_va = {va_of(p.name): p for p in pops["extra"]}
    extras = []
    for o in extra_objects_state(tag).get("objects", []):
        va = va_of(Path(o).name)
        if va not in by_va:
            raise Refused(f"win_link: extra function {Path(o).name} has no cross-built object "
                          f"in {root / 'extra'}")
        extras.append(by_va.pop(va))
    if by_va:
        raise Refused(f"win_link: {root / 'extra'} holds extras the link does not list: "
                      f"{sorted(p.name for p in by_va.values())[:3]}")
    ident = {"root": str(root), "summary_sha256": sha256(summary_p),
             "build_jsonl_sha256": sha256(log_p), "target": summary.get("target"),
             "modules": summary.get("modules"), "compiled": summary.get("compiled"),
             "ir_checked": len(recs)}
    return extras, lifted, ident


def manifest_resource(work: Path, stem: str) -> dict:
    """MANIFEST as <stem>.manifest, a one-line <stem>.rc naming it as RT_MANIFEST 1, and that
    compiled by `zig rc` (not preprocessed: the script has no directive) to <stem>.res, which
    lld-link links as the binary's resources. Refuses when zig rc fails."""
    work.mkdir(parents=True, exist_ok=True)
    man, rc, res = (work / f"{stem}.{x}" for x in ("manifest", "rc", "res"))
    if '"' in str(man):
        raise Refused(f"win_link: the manifest's path holds a double quote: {man}")
    man.write_text(MANIFEST)
    rc.write_text(f'{MANIFEST_ID} {RT_MANIFEST} "{man}"\n')
    if res.exists():
        res.unlink()
    cmd = [str(S.ZIG), "rc", "/:no-preprocess", "/fo", str(res), "--", str(rc)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode or not res.is_file():
        raise Refused(f"win_link: zig rc did not compile the manifest:\n{(p.stderr + p.stdout)[-2000:]}")
    return {"manifest": str(man), "manifest_sha256": sha256(man), "rc": str(rc),
            "resource": str(res), "resource_sha256": sha256(res), "command": cmd}


def compile_all(srcs: list[tuple[str, Path, bool]], src_root: Path, obj: Path,
                jobs: int) -> dict:
    obj.mkdir(parents=True, exist_ok=True)

    def one(item: tuple[str, Path, bool]) -> tuple[str, dict]:
        stem, src, cxx = item
        o = obj / f"{stem}.o"
        if o.exists():
            o.unlink()              # a failed compile must not leave a stale object
        cmd = flags(cxx, src_root) + [str(src), "-o", str(o)]
        t0 = time.time()
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
        return stem, {"source": str(src), "source_sha256": sha256(src), "rc": p.returncode,
                      "seconds": round(time.time() - t0, 2),
                      "stderr_tail": (p.stderr or "")[-3000:] if p.returncode else ""}

    with cf.ThreadPoolExecutor(max(1, jobs)) as ex:
        return dict(ex.map(one, srcs))


def probe_line(work: Path) -> list[str]:
    """zig's own lld-link argument list for PROBE_SRC, from `zig c++ -v`. The probe's objects
    and outputs are in `work`; link_line replaces them."""
    work.mkdir(parents=True, exist_ok=True)
    src, o, exe = work / "probe.cpp", work / "probe.o", work / "probe.exe"
    src.write_text(PROBE_SRC)
    cc = subprocess.run([str(S.ZIG), "c++"] + TARGET + [LS.OPT, "-c", str(src), "-o", str(o)],
                        capture_output=True, text=True)
    if cc.returncode:
        raise Refused(f"win_link: the probe does not compile:\n{cc.stderr[-2000:]}")
    p = subprocess.run([str(S.ZIG), "c++"] + TARGET + [LS.OPT, str(o), "-o", str(exe), "-v"]
                       + PROBE_LIBS, capture_output=True, text=True, cwd=str(work))
    if p.returncode or not exe.is_file():
        raise Refused(f"win_link: the probe does not link:\n{p.stderr[-2000:]}")
    lines = [ln for ln in p.stderr.splitlines() if ln.startswith("lld-link ")]
    if len(lines) != 1:
        raise Refused(f"win_link: zig printed {len(lines)} lld-link lines for the probe, not one")
    return parse_line(lines[0], o)


def parse_line(line: str, probe_object: Path) -> list[str]:
    """The arguments of zig's printed `lld-link ...` line. zig prints them space-separated and
    unquoted, so an argument holding a space or a quote cannot be read back and refuses. The
    probe's object must appear exactly once."""
    if any(c in line for c in "\"'\\"):
        raise Refused(f"win_link: zig's link line holds a quote or a backslash, which this "
                      f"reader cannot split: {line[:300]}")
    args = line.split()
    if args[:1] != ["lld-link"]:
        raise Refused(f"win_link: not an lld-link line: {line[:200]}")
    args = args[1:]
    for flag in ("-lldmingw", "-MACHINE:X64"):
        if flag not in args:
            raise Refused(f"win_link: zig's link line has no {flag}: {line[:300]}")
    hits = [a for a in args if Path(a).name == probe_object.name]
    if hits != [str(probe_object)] and hits != [probe_object.name]:
        raise Refused(f"win_link: the probe object appears {len(hits)} times in zig's link line")
    for f in OUTPUT_FLAGS[:1]:
        if sum(a.startswith(f) for a in args) != 1:
            raise Refused(f"win_link: zig's link line has {f} other than once")
    return args


def link_line(probe_args: list[str], probe_object: Path, objects: list[Path], out_exe: Path,
              wraps: list[str]) -> list[str]:
    """zig's link line with the probe's object replaced by `objects`, its outputs by the
    build's, and `-wrap:<symbol>` for each wrap before the objects."""
    out: list[str] = []
    for a in probe_args:
        if a in (str(probe_object), probe_object.name):
            out += [f"-wrap:{w}" for w in wraps]
            out += [str(o) for o in objects]
        elif a.startswith("-OUT:"):
            out.append(f"-OUT:{out_exe}")
        elif a.startswith("-PDB:"):
            out.append(f"-PDB:{out_exe.with_suffix('.pdb')}")
        elif a.startswith("-PDBALTPATH:"):
            out.append(f"-PDBALTPATH:{out_exe.with_suffix('.pdb').name}")
        elif a.startswith("-IMPLIB:"):
            out.append(f"-IMPLIB:{out_exe.with_suffix('.lib')}")
        else:
            out.append(a)
    return out


def response_file(args: list[str]) -> str:
    """One argument per line; an argument with a space is quoted, and one with a quote, which
    lld's response-file reader would take apart, refuses."""
    lines = []
    for a in args:
        if '"' in a:
            raise Refused(f"win_link: an argument holds a double quote: {a}")
        lines.append(f'"{a}"' if any(c.isspace() for c in a) else a)
    return "\n".join(lines) + "\n"


def rel(path: Path) -> str:
    path = Path(path).resolve()
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def link_moved(rec: dict, runner: str, log: Path, objects: Path) -> list[str]:
    """What moved since a link.record's link: each way it is not what this tree links now.
    The lifted objects and their IR are held by the caller, once for every link."""
    moved: list[str] = []
    link = rec.get("link") if isinstance(rec.get("link"), dict) else {}
    binary = Path(str(link.get("binary") or ""))
    if rec.get("schema") != LINK_SCHEMA or rec.get("triple") != TRIPLE:
        moved.append(f"not a {LINK_SCHEMA} link for {TRIPLE}")
    if link.get("rc") != 0 or not binary.is_file() or sha256(binary) != link.get("sha256"):
        moved.append("the link did not return zero, or its binary is gone or changed")
    if rec.get("src") != str(ROOT):
        moved.append(f"compiled from {rec.get('src')!r}, not this tree")
    ident = rec.get("objects") if isinstance(rec.get("objects"), dict) else {}
    if ident.get("root") != str(objects) or ident.get("build_jsonl_sha256") != sha256(log):
        moved.append(f"linked from another cross build than {rel(log)} as it is now")
    hooks, wraps = runner_links(runner)
    if rec.get("hooks") != hooks or rec.get("wrapped") != list(LS.PRODUCTION_WRAPS) + wraps:
        moved.append(f"the {runner} runner's hooks or wraps have changed")
    if rec.get("flags") != {"c": flags(False, ROOT)[2:], "cxx": flags(True, ROOT)[2:]}:
        moved.append("the compile flags have changed")
    if (rec.get("manifest") or {}).get("manifest_sha256") != \
            hashlib.sha256(MANIFEST.encode()).hexdigest():
        moved.append("the application manifest has changed")
    compiled = rec.get("compile") if isinstance(rec.get("compile"), dict) else {}
    if not compiled:
        moved.append("it records no compile")
    for stem, c in sorted(compiled.items()):
        src = Path(str(c.get("source") or ""))
        if c.get("rc") != 0 or not src.is_file() or sha256(src) != c.get("source_sha256"):
            moved.append(f"{stem}: {rel(src)} is not the source it compiled")
    return moved


def census(objects: Path, replays: Path, host_build: Path) -> dict:
    """The build census (module docstring): refuses, naming what moved, unless every binary it
    names is reproducible from the tree as it stands."""
    sys.path.insert(0, str(HERE))
    import target_receipt as TR  # noqa: PLC0415  tools/s7/target_receipt.py
    import tick_replay as T  # noqa: PLC0415  tools/s4/tick_replay.py
    objects = objects.resolve()
    log, summary_p = objects / "build.jsonl", objects / "summary.json"
    if not log.is_file() or not summary_p.is_file():
        raise Refused(f"win_link census: {objects} holds no cross build (summary.json, build.jsonl)")
    if json.loads(summary_p.read_text()).get("target") != TRIPLE:
        raise Refused(f"win_link census: {objects} is not a cross build for {TRIPLE}")
    recs = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
    failed = [r.get("object") for r in recs if r.get("compiled") is not True]
    if not recs or failed:
        raise Refused(f"win_link census: {len(failed)} of the {len(recs)} modules of {rel(log)} "
                      f"did not compile: {failed[:3]}")
    stale = stale_ir(recs)
    if stale:
        raise Refused(f"win_link census: {len(stale)} modules of {rel(log)} were compiled from IR "
                      f"that has changed or gone since: {stale[:3]}; run tools/s7/cross_build.py "
                      f"--target {TRIPLE} again, then link and run each replay again")
    evidence = json.loads(replays.read_text())
    if evidence.get("schema") != REPLAYS_SCHEMA or evidence.get("target") != TARGET_NAME:
        raise Refused(f"win_link census: {rel(replays)} is not {TARGET_NAME}'s {REPLAYS_SCHEMA}")
    entries = json.loads(T.REGISTRY.read_text())["replays"]
    binaries, records = {}, {}
    for entry in entries:
        name = entry["name"]
        run = (evidence.get("replays") or {}).get(name)
        if not isinstance(run, dict) or not (run.get("run") or {}).get("binary"):
            raise Refused(f"win_link census: {rel(replays)} names no binary for {name}")
        binary = ROOT / run["run"]["binary"]
        recp = binary.parent / "link.record"
        if not recp.is_file():
            raise Refused(f"win_link census: {rel(recp)} is absent ({name}'s link)")
        rec = json.loads(recp.read_text())
        moved = link_moved(rec, "stage" if T.is_stage(entry) else "tick", log, objects)
        if (rec.get("link") or {}).get("binary") != str(binary.resolve()) \
                or (rec.get("link") or {}).get("sha256") != run.get("binary_sha256"):
            moved.append(f"its binary is not the one {rel(replays)} ran")
        if moved:
            raise Refused(f"win_link census: {name}'s binary is not reproducible from the tree "
                          f"({rel(recp)}): {'; '.join(moved[:5])}; link it again and run it "
                          f"again with tools/s7/wine_run.py --evidence")
        binaries[name] = run["binary_sha256"]
        records[name] = {"path": rel(recp), "sha256": sha256(recp)}
    host_rec = host_build.resolve() / HOST_RECORD
    if not host_rec.is_file():
        raise Refused(f"win_link census: {rel(host_rec)} is absent: run tools/s7/host2_win_build.py")
    host = json.loads(host_rec.read_text())
    player = host_build.resolve() / HOST_PLAYER
    want = (host.get("outputs") or {}).get(HOST_PLAYER)
    if host.get("schema") != HOST_SCHEMA or not player.is_file() or sha256(player) != want:
        raise Refused(f"win_link census: {rel(player)} is not the player {rel(host_rec)} built")
    binaries["host"] = want
    records["host"] = {"path": rel(host_rec), "sha256": sha256(host_rec)}
    reach = json.loads((ROOT / TR.REACH).read_text())
    return {"schema": CENSUS_SCHEMA, "generated_by": SELF, "target": TARGET_NAME,
            "triple": TRIPLE, "lifted_inputs": TR.lifted_inputs(reach.get("manifest", reach)),
            "target_passes": {},
            "log": {"path": rel(log), "sha256": sha256(log), "modules": len(recs),
                    "ir_checked": len(recs)},
            "link": {"returncode": 0, "binaries": binaries, "records": records},
            "binaries_from": {"path": rel(replays), "sha256": sha256(replays)},
            "written": time.strftime("%Y-%m-%dT%H:%M:%S%z")}


def census_main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="win_link.py census",
                                 description="write the x86_64-windows build census")
    ap.add_argument("--objects", type=Path, default=CENSUS_OBJECTS,
                    help="tools/s7/cross_build.py's output root for this triple")
    ap.add_argument("--replays", type=Path, default=ROOT / REPLAYS_TRACKED,
                    help="the Windows replays evidence whose binaries the census names")
    ap.add_argument("--host-build", type=Path, default=HOST_BUILD,
                    help="tools/s7/host2_win_build.py's --out")
    ap.add_argument("--census-out", type=Path, default=ROOT / CENSUS_TRACKED)
    args = ap.parse_args(argv)
    doc = census(args.objects, args.replays, args.host_build)
    args.census_out.parent.mkdir(parents=True, exist_ok=True)
    args.census_out.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    print(f"win_link census: {', '.join(f'{k} {v[:12]}' for k, v in doc['link']['binaries'].items())}"
          f"; {doc['log']['modules']} modules, IR checked -> {args.census_out}")
    return 0


def main() -> None:
    if sys.argv[1:2] == ["census"]:
        raise SystemExit(census_main(sys.argv[2:]))
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, type=Path, help="the build directory (scratch)")
    ap.add_argument("--src", type=Path, default=ROOT,
                    help="the tree whose runtime/ and harness/ are compiled (default: this one)")
    ap.add_argument("--objects", type=Path, default=OBJECTS,
                    help="tools/s7/cross_build.py's output root for this triple")
    ap.add_argument("--tag", default=TAG)
    ap.add_argument("--main", default=MAIN)
    ap.add_argument("--stage", action="store_true",
                    help="link a stage replay's binary: stage_run.py's HOOKS and WRAPS in place "
                         "of tick_replay.py's HOOK and WRAP")
    ap.add_argument("--hook", action="append", default=None,
                    help="in place of the runner's hooks; repeatable")
    ap.add_argument("--wrap", action="append", default=None,
                    help="in place of the runner's wraps; repeatable")
    ap.add_argument("--exe-overlay", nargs="?", type=Path, default=None,
                    const=LS.EXE_OVERLAY_TABLE)
    ap.add_argument("--jobs", type=int, default=4,
                    help="compiles at a time (default 4: at most four build jobs on this machine)")
    ap.add_argument("--map", action="store_true",
                    help="also write the linker's map (sa2_main.map): host addresses to symbols, "
                         "for a trap note's rip")
    ap.add_argument("--extra-object", action="append", type=Path, default=None,
                    help="an object linked after the lifted ones (an f80 build's f80_shims.o); "
                         "repeatable")
    args = ap.parse_args()

    run_hooks, run_wraps = runner_links("stage" if args.stage else "tick")
    hooks = args.hook if args.hook is not None else run_hooks
    wraps = args.wrap if args.wrap is not None else run_wraps
    clash = sorted(set(wraps) & set(LS.PRODUCTION_WRAPS))
    if clash:
        raise Refused(f"win_link: --wrap {clash}: the runtime wraps these in every build")
    wrapped = list(LS.PRODUCTION_WRAPS) + wraps
    out = args.out.resolve()
    src = args.src.resolve()
    overlay = args.exe_overlay.resolve() if args.exe_overlay is not None else None
    if overlay is not None and not overlay.is_file():
        raise Refused(f"win_link: --exe-overlay names a table that is not there: {overlay}")
    out.mkdir(parents=True, exist_ok=True)
    record: dict = {"schema": "sa2-s7-win-link/v1", "triple": TRIPLE, "tag": args.tag,
                    "src": str(src), "main": args.main, "hooks": hooks, "wrapped": wrapped,
                    "flags": {"c": flags(False, src)[2:], "cxx": flags(True, src)[2:]},
                    "exe_overlay": LS.exe_overlay_identity(overlay) if overlay else None}
    extras, lifted, ident = lifted_objects(args.objects.resolve(), args.tag, STAGED)
    record["objects"] = dict(ident, extras=len(extras), lifted=len(lifted))
    more = [p.resolve() for p in (args.extra_object or [])]
    absent = [str(p) for p in more if not p.is_file()]
    if absent:
        raise Refused(f"win_link: --extra-object names objects that are not there: {absent}")
    if more:
        record["extra_objects"] = [{"path": str(p), "sha256": sha256(p)} for p in more]
    srcs = sources(src, args.tag, args.main, hooks, overlay)
    t0 = time.time()
    record["compile"] = compile_all(srcs, src, out / "obj", args.jobs)
    record["compile_seconds"] = round(time.time() - t0, 2)
    failed = {k: v for k, v in record["compile"].items() if v["rc"]}
    rec_path = out / "link.record"
    if failed:
        record["link"] = {"rc": None, "why": f"compile failed: {sorted(failed)}"}
        rec_path.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
        for k, v in failed.items():
            print(f"--- {k} ---\n{v['stderr_tail']}", file=sys.stderr)
        raise Refused(f"win_link: {len(failed)} sources did not compile: {sorted(failed)}")
    probe_o = out / "probe" / "probe.o"
    probe = probe_line(out / "probe")
    base = [out / "obj" / f"{stem}.o" for stem, _, _ in srcs]
    exe = out / EXE
    if exe.exists():
        exe.unlink()
    record["manifest"] = manifest_resource(out, exe.stem)
    line = link_line(probe, probe_o,
                     base + extras + lifted + more + [Path(record["manifest"]["resource"])],
                     exe, wrapped)
    if args.map:
        line.append(f"-MAP:{exe.with_suffix('.map')}")
    rsp = out / "link.rsp"
    rsp.write_text(response_file(line))
    cmd = [str(S.ZIG), "lld-link", f"@{rsp}"]
    print(f"link: {len(lifted)} lifted + {len(extras)} extra + {len(base)} runtime/driver "
          f"objects, wraps {wrapped}", flush=True)
    t1 = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=str(out), timeout=3600)
    link = {"rc": p.returncode, "seconds": round(time.time() - t1, 2), "command": cmd,
            "probe_line_head": probe[:12], "response_sha256": sha256(rsp),
            "stderr_tail": (p.stderr or "")[-6000:]}
    if p.returncode == 0 and exe.is_file():
        link.update(binary=str(exe), binary_bytes=exe.stat().st_size, sha256=sha256(exe))
    record["link"] = link
    rec_path.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    print(f"link: rc={p.returncode} {link['seconds']} s "
          f"{link.get('binary_bytes', 0) / 1e6:.1f} MB -> {rec_path}", flush=True)
    if p.returncode or not exe.is_file():
        print(link["stderr_tail"], file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
