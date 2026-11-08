#!/usr/bin/env python3
"""Rebuild every lifted object the link uses, after a change to the emitter.

Subtitle: the map's modules, S3's jump-table arm traces and S4's lifted extras
lifted, passed, folded and compiled again in place, the merged link directory
restaged, and how many objects actually changed reported by content hash.

    source tools/env.sh
    .venv/bin/python tools/s4/rebuild_image.py [--jobs 16]

On a fresh checkout this one command builds the whole image: it restores the extras'
state from `Documentation/lifted-extras.json` (`track_extra_functions.py --restore`) and
writes the arm work list from the map build (`tools/s3/arms/armlist.py`) when either is
missing, and builds the PC24 helpers through `lift_all.py`. Check the result against
`Documentation/receipts/lift-ir-v1.json` with `tools/s4/lift_ir_receipt.py`.

Why
---
The objects are built by three tools and live in three places:
`tools/s3/whole_image/lift_all.py` and `compile_all.py` for the map's modules
(tag `next`), `tools/s3/arms/lift_arms_image.py` for the arm traces (tag
`next`), and `tools/s4/lift_extra.py` for the extras. The link reads the map
and the arms through `build/next+allarms`, which is hard links
(`tools/s3/arms/stage_merged_build.py`), so a rebuild that replaces an object
leaves the old bytes linked unless the directory is restaged. A change to
`tools/spike/pc24pass/pc24pass.py` -- the first was FNSTCW's bit 6, on
2026-09-23 -- touches every object whose IR it rewrites, in all three places.
Doing that by hand is five commands and one easily forgotten restage, which is
AGENTS.md rule 13's case for a tool.

Every compiled extra must rebuild to the path its row records, and the
objects that changed are counted per population, so a rebuild that changed
nothing after an emitter change shows as zeros rather than passing unseen.
The restage is checked, not assumed: every object in `build/next+allarms` must
be the same file (inode) as its rebuilt source. Its first version restaged
`next+arms`, the stager's default, and the reach went on linking the old
objects with every count looking right (2026-09-23 review).

And every object the link reads (`build/next+allarms` and each extra's object)
must have been written during the rebuild: one that still has the inode and
mtime it had before the first step is an earlier build's (`not_rewritten`).
The inode check above cannot see that. A module whose lift or PC24 pass the new
code refuses kept its last good build record, so the stager linked that build's
object again, the same file as the map's own, and the stamp below named fold
code that never touched it (the review of the S5 speed fold, SHOULD-FIX 1).
`compile_all.py --force` now records such a module as not compiled, so the
stager refuses first; this check holds for the arms and the extras too.

The fold-code stamp
-------------------
A complete rebuild ends by writing `build/next+allarms.fold-code.json`: the
sha256 of the fold code it ran (`FOLD_CODE`, the PC24 pass, the stub fold and
the S5 speed passes with State localization) and of the two helper modules the pass splices into every
module (`FOLD_HELPERS`), and the command each tool that builds a linked object
compiles a folded module with (`COMPILE_STEPS`: `compile_all.py`'s,
`lift_arms.py`'s and `lift_extra.py`'s), all read before the first object is
touched and checked unchanged at the end, by a new interpreter there
(`fresh_fold_code`), since this one imports the tools once. The commands are
data, not file hashes: each is what its step hands `subprocess.run`, read by
running the step with a recorder in place of `subprocess` (`compile_commands`),
so a flag typed inline counts and an edit elsewhere in those files does not.
The stamp is deleted first, so a rebuild that stops part way leaves none.
Nothing else tied the linked objects to the code that folded them: the reach
manifest digests them by name and size, and the emitter determinism gate
re-folds seven panel functions in a scratch directory, so an edit to the fold
code followed by the gate's `--refresh` left every gate green over the previous
fold's objects (review of the S5 speed change, SHOULD-FIX 3).
`tools/s4/reach_receipt.py` records the stamp in its manifest and refuses to
measure over objects whose stamp is not the tree's fold code, and
`Documentation/scripts/check_run_reach.py` fails a push while it is not.

What the stamp cannot see
-------------------------
It vouches for the objects the last complete rebuild wrote, and only while
nothing else writes one. `tools/s4/lift_extra.py` and `compile_all.py` also
build objects between rebuilds, with whatever fold code the tree holds then.
If that was other fold code and the tree went back to the stamped code
afterwards, the stamp matches the tree again and every check passes over the
objects they built. Only another rebuild replaces them.

The lift commands are not in the stamp either: remill's arguments and the
switches the pass is called with in `lift_all.py`, `lift_arms.py` and
`lift_extra.py`. A change there moves the objects and not the stamp.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "s3" / "whole_image"))
import common as C  # noqa: E402
import lift_extra as LE  # noqa: E402

PY = str(ROOT / ".venv" / "bin" / "python")
TAG = "next"
ARMLIST = C.GEN / "s3" / "arms" / "armlist.next.json"
MAP_BUILD = C.GEN / "s3" / "whole_image" / "build" / TAG
ARM_BUILD = C.GEN / "s3" / "arms" / "build" / TAG
STATE = C.GEN / "s4" / "link" / "extra_functions.next+allarms.json"
MERGED = "next+allarms"
# Where the link reads the map's and the arms' objects, as hard links (stage_merged_build.py).
MERGED_BUILD = C.BUILD / MERGED
# The code between the lifted IR and an object, by repository-relative path: the PC24
# pass, the stub fold, and the S5 speed passes the fold ends with (State localization is
# the fourth, in a module of its own). Each is also in the reach manifest's pipeline
# (tools/s4/reach_receipt.py TOOLS).
FOLD_CODE = ("tools/spike/pc24pass/pc24pass.py", "tools/s2/corpus_run/internalize.py",
             "tools/s2/corpus_run/speed_rewrite.py", "tools/s2/corpus_run/state_local.py")
# The two modules the pass splices into every module (generated, never tracked).
FOLD_HELPERS = tuple(str(h.relative_to(C.ROOT)) for h in (C.HELPER_LL, C.INT_HELPER_LL))
# The step that compiles a folded module in each tool that builds an object the link reads
# (the map's modules, the arm traces, the extras), as file:function. The stamp holds the
# command each runs (`compile_commands`), not its file's hash: each file also holds a lift,
# a job list and a log, none of which changes an object.
COMPILE_STEPS = ("tools/s3/whole_image/compile_all.py:one",
                 "tools/s3/arms/lift_arms.py:fold_and_compile",
                 "tools/s4/lift_extra.py:fold_and_compile")
STAMP = C.BUILD / f"{MERGED}.fold-code.json"


def staged_not_rebuilt() -> list[str]:
    """Objects the link reads (build/next+allarms, hard links) that are not the
    same file as the map's or the arms' own object of that name."""
    out = []
    for p in sorted(MERGED_BUILD.glob("ir_*.o")):
        src = next((d / p.name for d in (MAP_BUILD, ARM_BUILD) if (d / p.name).is_file()), None)
        if src is None or os.stat(src).st_ino != os.stat(p).st_ino:
            out.append(p.name)
    return out


def linked_objects(rows: list[dict]) -> list[Path]:
    """Every object the link reads: build/next+allarms and each compiled extra's object
    (`rows`, the extras state's)."""
    return sorted(MERGED_BUILD.glob("ir_*.o")) + [Path(r["object"]) for r in rows]


def file_ids(paths) -> dict[str, tuple[int, int]]:
    """Each file's inode and mtime in nanoseconds, by path; a missing one is left out."""
    out = {}
    for p in paths:
        try:
            st = os.stat(p)
        except FileNotFoundError:
            continue
        out[str(p)] = (st.st_ino, st.st_mtime_ns)
    return out


def not_rewritten(before: dict[str, tuple[int, int]],
                  after: dict[str, tuple[int, int]]) -> list[str]:
    """The files in `after` that are still the file `before` recorded (the same inode and
    mtime): nothing wrote them in between. The compiler writes a new file every time, even
    when it compiles from its cache, so an object compiled again never keeps both."""
    return sorted(_shown(Path(p)) for p, ident in after.items() if before.get(p) == ident)


def _shown(p: Path) -> str:
    return str(p.relative_to(C.ROOT)) if p.is_relative_to(C.ROOT) else str(p)


class _Recorder:
    """A tool's `subprocess` module for `compile_commands`: `run` keeps each command it is
    handed, runs none, and answers success."""
    TimeoutExpired = subprocess.TimeoutExpired

    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def run(self, cmd, **kw):
        self.commands.append([str(a) for a in cmd])
        return subprocess.CompletedProcess(cmd, 0, "", "")


def compile_commands() -> dict[str, str]:
    """Each COMPILE_STEPS step's command, as data: what the step hands `subprocess.run` for
    one empty module, with that module's folded IR shown as {ir}, its object as {object},
    any other scratch path under {scratch}, and the repository's paths relative to it.
    Each step runs with its tool's `subprocess` replaced by a recorder, so nothing is
    compiled and the command is the one the code runs, a flag typed inline included.
    Raises when a step runs no command, since then nothing says what compiles its objects."""
    arms = str(HERE.parent / "s3" / "arms")
    if arms not in sys.path:
        sys.path.append(arms)
    import compile_all as CA
    import lift_arms as LA
    va, out = 0x00401000, {}
    with tempfile.TemporaryDirectory() as td:
        scratch = Path(td)
        ll, fold, obj = scratch / "ll", scratch / "fold", scratch / "obj"
        for d in (ll, fold, obj):
            d.mkdir()
        (ll / f"sub_{va:08X}.pc24.ll").write_text("")
        names = {str(fold / f"sub_{va:08X}.pc24.ll"): "{ir}",
                 str(obj / f"ir_{va:08X}.o"): "{object}"}

        def compile_all_one() -> None:
            was = (CA._LL, CA._FOLD, CA._OBJ)
            CA._init(str(ll), str(fold), str(obj))
            try:
                CA.one((va, "", ""))
            finally:
                CA._LL, CA._FOLD, CA._OBJ = was

        steps = dict(zip(COMPILE_STEPS, (
            (CA, compile_all_one),
            (LA, lambda: LA.fold_and_compile(va, ll, fold, obj)),
            (LE, lambda: LE.fold_and_compile(va, ll, fold, obj)))))
        for step, (tool, call) in steps.items():
            recorder, real = _Recorder(), tool.subprocess
            tool.subprocess = recorder
            try:
                call()
            finally:
                tool.subprocess = real
            if not recorder.commands:
                raise RuntimeError(f"{step} ran no command for a module, so nothing says what "
                                   f"compiles the objects it builds")
            out[step] = " && ".join(
                " ".join(names.get(a) or a.replace(td, "{scratch}").replace(f"{ROOT}/", "")
                         for a in cmd) for cmd in recorder.commands)
    return out


def fold_code(root: Path | None = None) -> dict[str, str]:
    """The fold code as the tree holds it now: each FOLD_CODE and FOLD_HELPERS file by
    repository-relative path, with its sha256, or "" for a file that is not there; and each
    COMPILE_STEPS step with the command it compiles a folded module with
    (`compile_commands`, which reads the tools this process imported, whatever `root`)."""
    root = root or ROOT
    files = {rel: hashlib.sha256((root / rel).read_bytes()).hexdigest()
             if (root / rel).is_file() else "" for rel in FOLD_CODE + FOLD_HELPERS}
    return {**files, **compile_commands()}


def fresh_fold_code() -> dict[str, str]:
    """fold_code() as a new interpreter reads the tree now. This process imports the tools
    whose commands it records once, so its own fold_code() at the end of a rebuild returns
    the commands it started with, whatever their files say by then."""
    p = subprocess.run([PY, "-c", "import json, sys; sys.path.insert(0, sys.argv[1]); "
                        "import rebuild_image; print(json.dumps(rebuild_image.fold_code()))",
                        str(HERE)], cwd=str(ROOT), capture_output=True, text=True, check=True)
    return json.loads(p.stdout)


def stamped_fold_code() -> dict[str, str]:
    """The fold code the last complete rebuild ran (its stamp), or {} when there is no
    readable stamp."""
    try:
        return dict(json.loads(STAMP.read_text()).get("fold_code") or {})
    except (OSError, ValueError, AttributeError):
        return {}


def fold_code_problems(stamped: dict[str, str] | None = None,
                       now: dict[str, str] | None = None) -> list[str]:
    """Each way the fold code that made the linked objects (`stamped`, default the stamp)
    is not the tree's (`now`, default `fold_code()`), as a sentence; [] when it is. A
    compile command is compared whole and shown whole."""
    stamped = stamped_fold_code() if stamped is None else stamped
    now = fold_code() if now is None else now
    if not stamped:
        return [f"the objects the link reads carry no fold-code stamp ({_shown(STAMP)}), so "
                f"nothing says which fold code made them: run tools/s4/rebuild_image.py"]

    def command(c: str | None) -> str:
        return f"`{c}`" if c else "no command"
    return [(f"the objects the link reads were compiled by {name} with "
             f"{command(stamped.get(name))} and the tree's compiles with "
             f"{command(now.get(name))}: run tools/s4/rebuild_image.py")
            if name in COMPILE_STEPS else
            (f"the objects the link reads were folded with {name} at "
             f"{(stamped.get(name) or 'none')[:12]} and the tree holds "
             f"{(now.get(name) or 'none')[:12]}: run tools/s4/rebuild_image.py")
            for name in sorted(set(stamped) | set(now)) if stamped.get(name) != now.get(name)]


def digests(d: Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.glob("*.o"))}


def run(cmd: list[str]) -> float:
    t0 = time.time()
    subprocess.run(cmd, cwd=str(ROOT), check=True)
    return round(time.time() - t0, 1)


_IMAGE = None


def _extra(r: dict) -> dict:
    va, end = int(r["va"], 16), int(r["end"], 16)
    anchor = int(r["anchor"], 16) if r.get("anchor") else None
    rec = LE.lift_one(va, end, _IMAGE, LE.OUT / "ll", anchor)
    if rec.get("lifted"):
        rec.update(LE.fold_and_compile(va, LE.OUT / "ll", LE.OUT / "fold", LE.OUT / "obj"))
    return {"va": r["va"], "compiled": bool(rec.get("compiled")),
            "same_path": str(Path(rec.get("object", ""))) == str(Path(r["object"]))}


def main() -> int:
    global _IMAGE
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=16)
    args = ap.parse_args()
    j = str(args.jobs)
    # Read before the first object is touched, and the old stamp gone, so a rebuild that
    # stops part way leaves no stamp (the module docstring, "The fold-code stamp").
    # The helpers are part of the fold code the stamp names; on a fresh checkout they do not
    # exist until the first lift builds them, so build them before reading it.
    C.helpers()
    code = fold_code()
    STAMP.unlink(missing_ok=True)
    if not STATE.is_file():
        # A fresh checkout: the extras' state comes back from the tracked record.
        run([PY, "tools/s4/track_extra_functions.py", "--tag", MERGED, "--restore"])
    rows = [r for r in json.loads(STATE.read_text())["rows"]
            if r.get("compiled") and not r.get("rejected")]
    # Every object the link reads, as it is before the first step: each must be written
    # again before the stamp may name the code that made it (the docstring, after the
    # restage check).
    old = file_ids(linked_objects(rows))
    before = {"map": digests(MAP_BUILD), "arms": digests(ARM_BUILD),
              "extras": {r["va"]: hashlib.sha256(Path(r["object"]).read_bytes()).hexdigest()
                         for r in rows if Path(r["object"]).is_file()}}
    walls = {
        "map_lift": run([PY, "tools/s3/whole_image/lift_all.py", "--jobs", j, "--tag", TAG,
                         "--to-next-entry", "--force"]),
        "map_compile": run([PY, "tools/s3/whole_image/compile_all.py", "--jobs", j, "--tag", TAG,
                            "--force"]),
    }
    if not ARMLIST.is_file():
        # A fresh checkout: the arm work list is read from the map build just made.
        walls["armlist"] = run([PY, "tools/s3/arms/armlist.py", "--tag", TAG, "--jobs", j])
    walls["arms"] = run([PY, "tools/s3/arms/lift_arms_image.py", "--jobs", j, "--tag", TAG,
                         "--armlist", str(ARMLIST), "--force"])
    _IMAGE = C.memory_image()
    # lift_extra.py's main makes these; on a fresh checkout (or after --restore of
    # the state) nothing has run it yet, and remill cannot write into a missing folder.
    for d in (LE.OUT / "ll", LE.OUT / "fold", LE.OUT / "obj"):
        d.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    with multiprocessing.get_context("fork").Pool(args.jobs) as pool:
        out = pool.map(_extra, rows, chunksize=4)
    walls["extras"] = round(time.time() - t0, 1)
    bad = [o["va"] for o in out if not o["compiled"] or not o["same_path"]]
    if bad:
        raise SystemExit(f"{len(bad)} extra(s) did not rebuild to their recorded object, "
                         f"first {bad[:5]}")
    walls["restage"] = run([PY, "tools/s3/arms/stage_merged_build.py", "--tag", TAG,
                            "--merged-tag", MERGED, "--force"])
    stale = staged_not_rebuilt()
    if stale:
        raise SystemExit(f"{len(stale)} object(s) in build/{MERGED} are not the rebuilt files, "
                         f"first {stale[:5]}")
    kept = not_rewritten(old, file_ids(linked_objects(rows)))
    if kept:
        raise SystemExit(f"{len(kept)} object(s) the link reads were not written by this "
                         f"rebuild (the same inode and mtime as before it), so an earlier "
                         f"build's fold code made them and no stamp is written: first "
                         f"{kept[:5]}. A module the lift or the pass now refuses keeps its old "
                         f"object; its record in the build log says why")
    after = {"map": digests(MAP_BUILD), "arms": digests(ARM_BUILD),
             "extras": {r["va"]: hashlib.sha256(Path(r["object"]).read_bytes()).hexdigest()
                        for r in rows}}
    changed = {k: sum(1 for n, h in after[k].items() if before[k].get(n) != h) for k in after}
    if fresh_fold_code() != code:
        raise SystemExit("the fold code changed during the rebuild, so the objects are a mix of "
                         "two versions and no stamp is written: run tools/s4/rebuild_image.py again")
    STAMP.write_text(json.dumps({"generated_by": "tools/s4/rebuild_image.py", "fold_code": code},
                                indent=1, sort_keys=True) + "\n")
    print(json.dumps({"walls_seconds": walls, "objects": {k: len(v) for k, v in after.items()},
                      "changed": changed, "stamp": _shown(STAMP)}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
