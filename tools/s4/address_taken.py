#!/usr/bin/env python3
"""Functions the game takes the address of that the function map never listed.

Subtitle: every base relocation whose value is code outside every map function,
kept when the address is taken rather than read -- an instruction's immediate
operand, or a pointer stored in a data section -- and other code ends just before it;
written as a `lift_extra.py --from-list` table with each row's evidence.

    source tools/env.sh
    .venv/bin/python tools/s4/address_taken.py                 # measure and write the table
    .venv/bin/python tools/s4/address_taken.py --lift          # and lift what it lists

Why
---
The Metal Harbor stage walk (tools/s4/reach_extend.py --stage metalharbor)
stops once per run on a "function table miss": a callback the game registered
by address -- an object's update routine, a task, a thread start -- that the
analyzer's map never named, so nothing lifted it. Of the walk's first 32 such
stops, 30 were named by a base relocation (2026-09-23), which is the image's
own list of every absolute address it holds. A relocation cannot be a
coincidence the way a dword that happens to hold a `.text` value can.

Code is every executable section, not `.text` alone: the 2012 executable keeps
Sofdec's decoder in PSFD00, and its B-picture slice loop (0x00872650), stored
by `mov dword ptr [0x0176ABC4], offset` at 0x007F926F, was never listed while
only `.text` counted, so every story movie hung on the first B-picture.

A relocated value in code is not always a function's address, so each
relocation is classed by the field it is, from the decoder's own operand
offsets:

  imm      the 32-bit immediate of an instruction in decoded code (`push offset
           f`, `mov [esi+10h], offset f`): the address taken.
  data     a dword in a section that is not code: a stored pointer
           (object tables, vtables, callback arrays).
  disp     the 32-bit displacement of a memory operand: the address is *read*,
           so what is there is data -- a jump table, a constant -- and the
           target is refused if any relocation reads it this way.
  table    a relocated code pointer in code with another at +-4: a jump table's
           entry, whatever a linear decode made of its bytes.
  other    anything else in code: a field straddled by a desynchronised decode,
           a table's bytes decoded as code. Not evidence either way.

An invalid decode classes nothing, so it cannot claim a relocation before the
decode that holds it whole: a decode cut off at the end of its range (a map
function's size, below), or bytes nothing decodes.

A target is admitted when some relocation takes its address (imm or data), none
reads it (disp), something ends just before it -- int3 padding, which is how
this compiler aligns a function and what the S3 `.rdata` sweep requires
(tools/s3/whole_image/dispatch_surface.py), a `ret`, `ret n` or `jmp`, or a
jump table -- and its code decodes to a `ret` or `jmp` with no invalid
instruction. Code is decoded over the map's functions, the lifted extras and
every admitted target, the last over the range lift_extra.py would lift it
over, round after round until a round admits exactly what the one before did.

A map function's size is the bytes in its body, which need not be contiguous:
ALR_CameraManager (0x00538110) is 0x43 bytes of its own and the 0xE0 at
0x00538030 it tail-jumps to, 291 in all, so start + size reaches past the next
function, the Chao race camera's delete routine at 0x00538160, and into the
one after. A target inside a map function's start + size is therefore still
weighed when int3 padding ends just before it, which ends a function and
starts none. Without this, 0x00538160 was never lifted, and finishing a Chao
race ended the game with a function table miss there.
Rows this tool lifted before that the criteria now refuse are listed, and
`--lift` refuses until they are dropped.

What it is not: a claim that every listed function runs. Each costs build time
(Documentation/decisions/2026-09-23-G1-1-build-time-jobs4.md) and nothing else
if it never does.
"""

from __future__ import annotations

import argparse
import bisect
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE.parent / "s3" / "whole_image"))
sys.path.insert(0, str(HERE))
import common as C  # noqa: E402

import pefile  # noqa: E402
from iced_x86 import Code, Decoder, FlowControl  # noqa: E402

PY = str(ROOT / ".venv" / "bin" / "python")
TAG = "next+allarms"
EXTRAS = C.GEN / "s4" / "link" / f"extra_functions.{TAG}.json"
WALK = C.GEN / "s4" / "run" / "reach_extend.stage-metalharbor.json"
OUT = C.GEN / "s4" / "address_taken.json"
TABLE = C.GEN / "s4" / "address_taken.txt"
GROUP = "reloc_address_taken"


def relocations(pe: pefile.PE) -> list[int]:
    pe.parse_data_directories(
        directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_BASERELOC"]])
    return sorted(C.IMAGE_BASE + e.rva for blk in pe.DIRECTORY_ENTRY_BASERELOC
                  for e in blk.entries if e.type == 3)          # IMAGE_REL_BASED_HIGHLOW


def walk_stops() -> list[int]:
    """The function-table misses the stage walk stopped on, for the recall."""
    if not WALK.is_file():
        return []
    found = set(int(m, 16) for m in re.findall(r"function table miss at (0x[0-9a-f]+)",
                                               WALK.read_text()))
    return sorted(found)


def decodes(img: bytes, v: int, base: int = C.IMAGE_BASE) -> bool:
    """Code from `v` reaches a ret or jmp with no invalid instruction first."""
    o = v - base
    for i, ins in enumerate(Decoder(32, img[o:o + 2048], ip=v)):
        if ins.is_invalid:
            return False
        if ins.flow_control in (FlowControl.RETURN, FlowControl.UNCONDITIONAL_BRANCH,
                                FlowControl.INDIRECT_BRANCH) or i > 500:
            return True
    return False


MAX_ROUNDS = 20
SCN_CODE = 0x00000020 | 0x20000000             # IMAGE_SCN_CNT_CODE | IMAGE_SCN_MEM_EXECUTE


def executable_sections(pe: pefile.PE) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """The image's sections as (start, size), split into code -- marked
    executable or as holding code, `.text` and PSFD00 alike -- and the rest."""
    base = pe.OPTIONAL_HEADER.ImageBase
    code: list[tuple[int, int]] = []
    data: list[tuple[int, int]] = []
    for s in pe.sections:
        (code if s.Characteristics & SCN_CODE else data).append(
            (base + s.VirtualAddress, s.Misc_VirtualSize))
    return code, data


def analyse(img: bytes, relocs: list[int], ents: list[tuple[int, int]],
            extras: list[tuple[int, int]], code: list[tuple[int, int]],
            data: list[tuple[int, int]], base: int = C.IMAGE_BASE,
            modules: list[tuple[int, int]] | None = None) -> dict:
    """Every relocated target in `code` outside the map's functions `ents`
    (start, size), with its relocations classed and a verdict.

    `code` are the executable sections (start, size), each bounding its own
    decodes; `extras` are lifted ranges (start, end) whose code is decoded
    like the map's; `data` the other sections (start, size); `modules` the
    lifted modules' byte ranges (start, end), which bound what lift_extra.py
    lifts from an admitted target, so an admitted target is decoded over the
    range it would be lifted over (the next map entry when none is given).
    Returns {"targets": {va: {kind: [(reloc, where), ...]}}, "verdict": {va: str},
    "boundary": {va: str}, "rounds": n, "judge": fn}."""
    relocs = sorted(relocs)
    rset = set(relocs)
    secs = sorted(code)
    sstarts = [b for b, _ in secs]
    starts = [a for a, _ in ents]
    size = dict(ents)
    mods = sorted(modules or [])
    mstarts = [a for a, _ in mods]

    def section(va: int) -> tuple[int, int] | None:
        """The executable section (start, end) holding `va`, if any."""
        i = bisect.bisect_right(sstarts, va) - 1
        if i >= 0 and va < secs[i][0] + secs[i][1]:
            return secs[i][0], secs[i][0] + secs[i][1]
        return None

    def same_code(va: int, at: int) -> bool:
        """`va` is code in the section that holds `at`: a jump table's entries
        point into the function that indexes it, so into its own section."""
        s = section(at)
        return s is not None and s[0] <= va < s[1]

    def value(r: int) -> int:
        return int.from_bytes(img[r - base:r - base + 4], "little")

    def in_map(v: int) -> bool:
        i = bisect.bisect_right(starts, v) - 1
        return i >= 0 and starts[i] <= v < starts[i] + size[starts[i]]

    def in_data(va: int) -> bool:
        return any(b <= va < b + n for b, n in data)

    def table_entry(r: int) -> bool:
        """A relocated code pointer in code with another at +-4: a jump table's
        entry, whatever a linear decode made of its bytes (the rule-5 review
        found 104 targets admitted from tables decoded as `add [eax],imm32`)."""
        return any(n in rset and same_code(value(n), r) for n in (r - 4, r + 4))

    def classify(a: int, n: int, use: dict) -> None:
        """Class each relocation in [a, a+n) by the field it is: an imm32 is
        the address taken, a disp32 the address read; anything else -- a
        straddle, an imm8, a byte of a table decoded as code -- says nothing."""
        lo, hi = bisect.bisect_left(relocs, a), bisect.bisect_left(relocs, a + n)
        if lo == hi:
            return
        want = relocs[lo:hi]
        k = 0
        dec = Decoder(32, img[a - base:a - base + n], ip=a)
        for ins in dec:
            if ins.code == Code.INVALID:
                continue
            end = ins.ip + ins.len
            while k < len(want) and want[k] < ins.ip:
                k += 1
            j = k
            while j < len(want) and want[j] < end:
                r = want[j]
                co = dec.get_constant_offsets(ins)
                off = r - ins.ip
                if table_entry(r):
                    kind = "table"
                elif co.immediate_size == 4 and off == co.immediate_offset:
                    kind = "imm"
                elif co.displacement_size == 4 and off == co.displacement_offset:
                    kind = "disp"
                else:
                    kind = "other"
                use.setdefault(r, (kind, ins.ip))
                j += 1

    def extent(v: int) -> int:
        """The range lift_extra.py would lift from `v`: to the end of the
        smallest lifted module containing it, else to the next map entry."""
        best = None
        i = bisect.bisect_right(mstarts, v)
        for s0, e0 in mods[max(0, i - 64):i]:
            if s0 <= v < e0 and (best is None or e0 - s0 < best[1] - best[0]):
                best = (s0, e0)
        if best:
            return best[1] - v
        k = bisect.bisect_right(starts, v)
        return (starts[k] if k < len(starts) else section(v)[1]) - v

    def boundary(v: int) -> str | None:
        """What ends just before `v`, if anything: int3 padding, `ret`, `ret n`,
        a `jmp`, or a jump table -- the ways one function's code stops and
        another's can start -- or the start of its section, where the bytes
        before it are another section's fill."""
        s = section(v)
        if s is not None and v == s[0]:
            return "a section start"
        o = v - base
        if img[o - 1] == 0xCC:
            return "int3 padding"
        if img[o - 1] == 0xC3:
            return "a ret"
        if img[o - 3] == 0xC2:
            return "a ret n"
        if img[o - 5] == 0xE9 or img[o - 2] == 0xEB:
            return "a jmp"
        if v - 4 in rset and same_code(value(v - 4), v):
            return "a jump table"
        return None

    base_use: dict[int, tuple[str, int]] = {}
    for a, n in ents:
        if section(a):
            classify(a, n, base_use)
    for a, e in extras:
        s = section(a)
        if s and a < e <= s[1]:
            classify(a, e - a, base_use)

    def collect(use: dict) -> dict[int, dict[str, list[tuple[int, int]]]]:
        targets: dict[int, dict[str, list[tuple[int, int]]]] = {}
        for r in relocs:
            v = value(r)
            if not section(v) or v in size or (in_map(v) and img[v - base - 1] != 0xCC):
                continue
            kind, where = ("data", r) if in_data(r) else use.get(r, ("other", r))
            targets.setdefault(v, {}).setdefault(kind, []).append((r, where))
        return targets

    def verdict_of(targets: dict, v: int) -> str:
        k = targets.get(v)
        if not k:
            return "no relocation names it"
        if "disp" in k:
            return "read as data"
        if "imm" not in k and "data" not in k:
            return "no address taken"
        if not boundary(v):
            return "nothing ends before it"
        if not decodes(img, v, base):
            return "does not decode to a ret or jmp"
        return "admitted"

    # To a fixed point in the admitted set itself: every round classifies from
    # the map and the extras again, plus every target the last round admitted,
    # over the range each would be lifted over, and stops only when a round
    # admits exactly what the one before did. A target whose admission a later
    # round revokes takes back what only its code had admitted.
    admitted: set[int] = set()
    rounds = 0
    while True:
        use = dict(base_use)
        for v in sorted(admitted):
            classify(v, extent(v), use)
        targets = collect(use)
        now = {v for v in targets if verdict_of(targets, v) == "admitted"}
        rounds += 1
        if now == admitted:
            break
        if rounds >= MAX_ROUNDS:
            raise SystemExit(f"ADDRESS_TAKEN_REFUSED no fixed point in {MAX_ROUNDS} rounds: "
                             f"{len(now ^ admitted)} targets still changing")
        admitted = now
    verdicts = {v: verdict_of(targets, v) for v in targets}
    return {"targets": targets, "verdict": verdicts, "rounds": rounds,
            "boundary": {v: boundary(v) for v, w in verdicts.items() if w == "admitted"},
            "judge": lambda v: verdict_of(targets, v)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lift", action="store_true", help="lift the table with lift_extra.py")
    ap.add_argument("--jobs", type=int, default=4)
    args = ap.parse_args()

    import lift_extra as LE                  # its module ranges bound what it lifts

    pe = pefile.PE(str(C.EXE), fast_load=True)
    img = C.memory_image()
    code, data = executable_sections(pe)
    fmap = json.loads((C.GEN / "function-map.json").read_text())["functions"]
    ents = sorted(((int(f["address"], 16) if isinstance(f["address"], str) else f["address"]),
                   max(int(f.get("size") or 0), 1)) for f in fmap)
    rows = json.loads(EXTRAS.read_text())["rows"] if EXTRAS.is_file() else []
    live = [r for r in rows if r.get("compiled") and not r.get("rejected")]
    modules = LE.module_ranges("next", "next")
    # Registered already: an extra, or a module entry of the whole-image lift
    # or S3's arms, which lift_extra.py skips.
    registered = {int(r["va"], 16) for r in live} | {va for va, _e, _n in modules}
    relocs = relocations(pe)
    res = analyse(img, relocs, ents, [(int(r["va"], 16), int(r["end"], 16)) for r in live],
                  code, data, modules=[(a, e) for a, e, _n in modules])
    targets, verdicts = res["targets"], res["verdict"]

    admitted, refused, already = [], {}, 0
    for v in sorted(targets):
        why = verdicts[v]
        if why != "admitted":
            refused[why] = refused.get(why, 0) + 1
            continue
        if v in registered:
            already += 1
            continue
        k = targets[v]
        how = "imm" if "imm" in k else "data"
        admitted.append({"va": f"0x{v:08x}", "by": how, "at": f"0x{k[how][0][1]:08x}",
                         "after": res["boundary"][v],
                         "relocations": {kk: len(vv) for kk, vv in k.items()}})

    # Rows this tool lifted earlier that the criteria now refuse: their recorded
    # reason no longer holds, and they are listed for dropping, not kept quietly.
    stale = sorted(r["va"] for r in live if GROUP in str(r.get("why", ""))
                   and verdicts.get(int(r["va"], 16)) != "admitted")

    # Recall: the criteria applied to what the walk actually stopped on, lifted
    # since or not.
    stops = walk_stops()
    stop_class = {f"0x{s:08x}": res["judge"](s) for s in stops}
    after: dict[str, int] = {}
    for a in admitted:
        after[a["after"]] = after.get(a["after"], 0) + 1
    doc = {"what": "tools/s4/address_taken.py", "tag": TAG, "fixed_point_rounds": res["rounds"],
           "relocations": len(relocs), "targets_outside_every_map_function": len(targets),
           "admitted_already_registered": already, "admitted": len(admitted),
           "admitted_after": after, "refused": refused,
           "lifted_earlier_now_refused": stale,
           "walk_stops": len(stops), "walk_stop_class": stop_class,
           "rows": admitted}
    OUT.write_text(json.dumps(doc, indent=1) + "\n")
    lines = ["# <hex address> <group> <evidence>; tools/s4/address_taken.py"]
    for a in admitted:
        what = ("the immediate operand of the instruction at" if a["by"] == "imm"
                else "a pointer stored at")
        lines.append(f"{a['va']} {GROUP} a base relocation names it, as {what} {a['at']}; "
                     f"no relocation reads it as data; it follows {a['after']} and decodes "
                     f"to a ret or jmp")
    TABLE.write_text("\n".join(lines) + "\n")
    print(f"ADDRESS_TAKEN {len(targets)} relocated targets outside every map function; "
          f"{len(admitted)} admitted and not yet registered ({already} more already are), "
          f"following {after}; refused {refused}; {res['rounds']} rounds to a fixed point")
    print(f"ADDRESS_TAKEN lifted earlier and now refused: {len(stale)} {stale[:12]}")
    print(f"ADDRESS_TAKEN recall on the stage walk's stops: "
          f"{json.dumps({c: list(stop_class.values()).count(c) for c in set(stop_class.values())})}")
    print(f"-> {OUT.relative_to(ROOT)}\n-> {TABLE.relative_to(ROOT)}")
    if not args.lift:
        return 0
    if stale:
        raise SystemExit("ADDRESS_TAKEN_REFUSED rows lifted earlier are now refused; drop them "
                         "first (lift_extra.py --drop VA --drop-why ...)")
    p = subprocess.run([PY, str(HERE / "lift_extra.py"), "--tag", TAG, "--lift-tag", "next",
                        "--arms-tag", "next", "--seed-arms", "--seed-continuations",
                        "--from-list", str(TABLE), "--group", GROUP, "--jobs", str(args.jobs)],
                       cwd=str(ROOT), text=True)
    return p.returncode


if __name__ == "__main__":
    raise SystemExit(main())
