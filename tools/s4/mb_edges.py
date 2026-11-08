#!/usr/bin/env python3
"""The missing-block reaching-instruction table, for the whole image.

Subtitle: what `tools/s2/corpus_run/build.py` emits for a 147-function corpus,
emitted for the 18 394-module whole-image build instead.

Why this exists
---------------
`__remill_missing_block` decides whether to dispatch by the *kind of edge* that
reaches the missing address (`runtime/remill_runtime.h`), and it reads that from
a generated table. Only the S2 corpus build ever emitted one. At image scale the
table is empty, so every edge is `SA2_MB_EDGE_UNKNOWN` and every missing block
traps under every policy -- including `SA2_MB_REACHED`, which is the policy a
whole program has to run under.

That is not a theoretical gap. The image's PE entry point is

    007a801c  call 007B5195h        ; __security_init_cookie
    007a8021  jmp  007A7E9Eh        ; __tmainCRTStartup

and with `--to-next-entry` the `entry` module is 20 bytes, so the `jmp` leaves
its range. Without this table the very first guest instruction after the cookie
is computed traps `no lifted block at 0x007a7e9e ... reached by unknown`. The
edge is a `jmp`, which is the one shape a tail call has, and that is exactly
what the table is for.

What it decodes, and the check that it is decoding the right thing
------------------------------------------------------------------
A module's lifted byte range is `[va, end)` from the lift log -- the arm modules
use their anchor window, which is the range `tools/s3/arms` lifted them from.
Every instruction in every range is decoded and every control-flow edge that
*leaves* that range becomes a row. Where several modules reach one address by
different kinds of edge, the strictest kind wins, so an address that any
conditional branch or fall-through can reach never dispatches. That is
`build.py`'s rule, kept identical on purpose: two tables that disagree about the
same image would be worse than one table that is missing.

The module set is checked against the objects that are actually linked, not
assumed from a tag name. `private/generated/s3/whole_image/build/<tag>/ir_*.o`
names every module in the build; if the logs and the objects disagree the tool
refuses rather than emitting a table for a build nobody links. The operating
notes record a tag whose log and object directory had drifted by 343 modules,
so this is a measured failure mode and not a precaution.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s4/mb_edges.py --tag next+allarms --lift-tag next
  # writes private/generated/s4/link/mb_edges.<tag>.c and .json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "s3" / "whole_image"))
import common as C  # noqa: E402

from iced_x86 import Decoder, FlowControl, Formatter, FormatterSyntax  # noqa: E402

OUT = C.GEN / "s4" / "link"
EDGE_UNKNOWN, EDGE_JMP, EDGE_JCC, EDGE_FALL, EDGE_CALL = 0, 1, 2, 3, 4
EDGE_NAMES = {EDGE_UNKNOWN: "unknown", EDGE_JMP: "jmp", EDGE_JCC: "jcc-taken",
              EDGE_FALL: "fall-through", EDGE_CALL: "call-return"}


def module_ranges(lift_tag: str, arms_tag: str | None) -> tuple[dict[int, tuple[int, int]], dict]:
    """va -> (start, end) for every module in the build, plus a provenance note."""
    ranges: dict[int, tuple[int, int]] = {}
    prov = {"base_log": None, "base_modules": 0, "arms_log": None, "arms_modules": 0}

    base = C.LIFT / f"{lift_tag}.jsonl"
    if not base.is_file():
        raise SystemExit(f"no lift log at {base}")
    prov["base_log"] = C.rel(base) if hasattr(C, "rel") else str(base)
    for line in base.read_text().splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        if not d.get("lifted"):
            continue
        va, end = int(d["va"], 16), int(d["end"], 16)
        if end > va:
            ranges[va] = (va, end)
            prov["base_modules"] += 1

    if arms_tag:
        arms = C.GEN / "s3" / "arms" / f"arms.{arms_tag}.jsonl"
        if not arms.is_file():
            raise SystemExit(f"no arms log at {arms}")
        prov["arms_log"] = str(arms)
        for line in arms.read_text().splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            if not d.get("lifted"):
                continue
            entry = int(d["entry"], 16)
            lo, hi = (int(x, 16) for x in d["anchor"])
            if hi > lo:
                ranges[entry] = (lo, hi)
                prov["arms_modules"] += 1
    return ranges, prov


def check_against_objects(ranges: dict[int, tuple[int, int]], tag: str) -> dict:
    objdir = C.BUILD / tag
    if not objdir.is_dir():
        raise SystemExit(f"no object directory for tag {tag}: {objdir}")
    objs = {int(p.stem[3:], 16) for p in objdir.glob("ir_*.o")}
    missing = sorted(objs - set(ranges))
    extra = sorted(set(ranges) - objs)
    note = {"objects": len(objs), "ranges": len(ranges),
            "in_objects_not_in_logs": len(missing), "in_logs_not_in_objects": len(extra),
            "first_in_objects_not_in_logs": f"0x{missing[0]:08x}" if missing else None,
            "first_in_logs_not_in_objects": f"0x{extra[0]:08x}" if extra else None}
    if missing or extra:
        raise SystemExit(
            f"the lift logs and the objects in {objdir} name different module sets: "
            f"{len(missing)} objects with no log row, {len(extra)} log rows with no object. "
            f"Refusing to emit a table for a build nobody links.\n{json.dumps(note, indent=1)}")
    return note


def decode_edges(image: bytes, ranges: dict[int, tuple[int, int]],
                 entries: set[int] | None = None) -> tuple[list[dict], list[dict]]:
    """`entries` is every address a lifted module starts at -- the map's, the
    arms', and tools/s4/lift_extra.py's blocks -- because code another module
    enters is live wherever it sits, whatever the containing range's own flow
    says. The extras are not decoded as ranges of their own: an extra inside
    a map module's range had its edges recorded only because that range's
    linear decode passed through it."""
    fmt = Formatter(FormatterSyntax.INTEL)
    entries = set(entries or ()) | set(ranges)
    found: dict[int, list[dict]] = {}

    def add(target: int, source: int, kind: int, text: str, owner: int) -> None:
        found.setdefault(target, []).append(
            {"target": target, "source": source, "kind": kind,
             "kind_name": EDGE_NAMES[kind], "insn": text, "module": f"0x{owner:08x}"})

    for va, (start, end) in sorted(ranges.items()):
        off = start - C.IMAGE_BASE
        if off < 0 or end - C.IMAGE_BASE > len(image) or end <= start:
            continue
        insns = [i for i in Decoder(32, image[off:end - C.IMAGE_BASE], ip=start)
                 if not i.is_invalid]
        # Only code a lifted trace can reach records edges. The decode is
        # linear, so the alignment filler after a `jmp` or `ret` -- `lea
        # esp,[esp]` and `add eax,0` in the CRT's hand-written math -- decoded
        # as instructions that "fell through" into the next function, and that
        # invented fall-through is stricter than the real `jmp` into the same
        # address, so the real tail call stopped dispatching. It did so three
        # times: the two reviewed exceptions in
        # Documentation/dispatch-exceptions.json and 0x0082C7D0, the CRT's
        # SSE2 `tan` entry, reached by `jmp` from the dispatcher at 0x00828FB0
        # once MXCSR stopped sending every math call down the x87 path
        # (2026-09-23). Liveness is reachability within the range, to a fixed
        # point, from the module's entry and from any address a lifted module
        # starts at (see `entries`): an instruction is live if a live
        # instruction falls into it or branches or calls to it. That is
        # remill's recursive descent confined to the range, so nothing it can
        # reach is dropped. The first cut revived code at any branch target,
        # including the targets of branches that were themselves dead, and a
        # dead `jne` after a `ret` at 0x00828F93 kept four real tail calls
        # refused (2026-09-23 review).
        by_ip = {i.ip: k for k, i in enumerate(insns)}
        stops = (FlowControl.UNCONDITIONAL_BRANCH, FlowControl.RETURN,
                 FlowControl.INDIRECT_BRANCH, FlowControl.INTERRUPT, FlowControl.EXCEPTION)
        live: set[int] = set()
        work = [by_ip[e] for e in ({va, start} | {e for e in entries if start <= e < end})
                if e in by_ip]
        while work:
            k = work.pop()
            if k in live:
                continue
            live.add(k)
            i = insns[k]
            if i.flow_control in (FlowControl.UNCONDITIONAL_BRANCH,
                                  FlowControl.CONDITIONAL_BRANCH, FlowControl.CALL):
                t = i.near_branch_target
                if start <= t < end and t in by_ip:
                    work.append(by_ip[t])
            if i.flow_control not in stops and k + 1 < len(insns) and \
                    insns[k + 1].ip == i.next_ip:
                work.append(k + 1)
        for k, ins in enumerate(insns):
            if k not in live:
                continue
            nxt, fc = ins.next_ip, ins.flow_control
            if fc == FlowControl.UNCONDITIONAL_BRANCH:
                t = ins.near_branch_target
                if t and not (start <= t < end):
                    add(t, ins.ip, EDGE_JMP, fmt.format(ins), va)
            elif fc == FlowControl.CONDITIONAL_BRANCH:
                t = ins.near_branch_target
                if t and not (start <= t < end):
                    add(t, ins.ip, EDGE_JCC, fmt.format(ins), va)
                if not (start <= nxt < end):
                    add(nxt, ins.ip, EDGE_FALL, fmt.format(ins), va)
            elif fc == FlowControl.CALL:
                if not (start <= nxt < end):
                    add(nxt, ins.ip, EDGE_CALL, fmt.format(ins), va)
            elif fc in (FlowControl.RETURN, FlowControl.INDIRECT_BRANCH,
                        FlowControl.EXCEPTION, FlowControl.INTERRUPT):
                pass
            else:
                if not (start <= nxt < end):
                    add(nxt, ins.ip, EDGE_FALL, fmt.format(ins), va)

    edges, conflicts = [], []
    for target in sorted(found):
        seen = found[target]
        kinds = {e["kind"] for e in seen}
        kind = EDGE_JMP if kinds == {EDGE_JMP} else min(k for k in kinds if k != EDGE_JMP)
        # The lowest (source, module) of that kind, not the first seen: `seen` follows the
        # order modules were read, which follows the lift logs' order, and a parallel lift
        # writes its log in the order workers finish. A re-lift that matched every module's
        # IR still linked a binary two bytes off, in the source of one row reached by two.
        pick = min((e for e in seen if e["kind"] == kind),
                   key=lambda e: (e["source"], e["module"]))
        # The runtime row is one per target, but every edge into it takes that row's verdict, so
        # each distinct (source, kind) is kept for the refused census. Collapsing them to `pick`
        # hid 0x008294d8's `jne 0082CCC5h` (an MXCSR/x87-mask test) behind 0x008294a7's `je`
        # into the same target, which the record named. A source is kept once, by the row's own
        # rule (the strictest non-`jmp` kind it reaches the target by, so a `jcc` to its own
        # fall-through is one jcc-taken edge), from the first module that records it: an arm
        # repeats its owner's rows.
        sources: dict[int, dict] = {}
        for e in sorted(seen, key=lambda e: (e["kind"] == EDGE_JMP, e["kind"], e["module"])):
            sources.setdefault(e["source"], e)
        edges.append({"target": target, "source": pick["source"], "kind": kind,
                      "kind_name": EDGE_NAMES[kind], "insn": pick["insn"],
                      "module": pick["module"], "reached_by": len(seen),
                      "sources": [sources[k] for k in sorted(sources)]})
        if len(kinds) > 1:
            conflicts.append({"target": f"0x{target:08x}",
                              "kinds": sorted(EDGE_NAMES[k] for k in kinds),
                              "resolved_to": EDGE_NAMES[kind]})
    return edges, conflicts


def apply_allow(edges: list[dict], allow: dict[int, str]) -> int:
    """Mark the targets a separately lifted block now answers for.

    A non-`jmp` edge never dispatches on its own, because the G2 interrogation
    falsified the rule that it should. It dispatches only with a *recorded
    reason*, which is the `allow`/`why` pair the runtime's edge row already
    carries. The reasons here are generated from `tools/s4/lift_extra.py`'s
    state file, so the exception list is a set of addresses a tool produced
    and not a set of constants somebody typed.

    That is weaker than it sounds and the weakness is worth writing down: the
    rule is *membership in that file*, not review of each row. Every exception
    today carries "missing_block_target in dispatch_surface...txt", which is a
    static group and names no run; a future sweep row that happened to be a
    fall-through target would become a dispatch exception with nobody looking
    at it. The predicate that would deserve the name is "an address a run
    reached", and it is not the one implemented here."""
    n = 0
    for e in edges:
        why = allow.get(e["target"])
        if why and e["kind"] != EDGE_JMP:
            e["allow"], e["why"] = 1, why
            n += 1
    return n


REVIEWED = C.ROOT / "Documentation" / "dispatch-exceptions.json"


def reviewed_allow(path: Path, tag: str) -> dict[int, str]:
    """The tracked reviewed exceptions for `tag` (an entry with no tag applies to every tag):
    target -> its recorded reason."""
    rdoc = json.loads(path.read_text())
    if rdoc.get("schema") != "sa2-recomp-dispatch-exceptions/v2":
        raise SystemExit(f"{path} has schema {rdoc.get('schema')!r}, "
                         "expected 'sa2-recomp-dispatch-exceptions/v2'")
    out: dict[int, str] = {}
    for r in rdoc.get("exceptions", []):
        if r.get("tag") and r["tag"] != tag:
            continue
        why = str(r.get("why", "")).strip()
        if not why:
            raise SystemExit(f"{path}: {r.get('va')} has no reason, and an exception without a "
                             "recorded reason is the thing rule 14 forbids")
        out[int(r["va"], 0)] = why
    return out


def not_in_force(c_path: Path, tag: str, reviewed: Path = REVIEWED) -> list[str]:
    """Why the edge table at `c_path` must not be linked for `tag`: each reviewed exception
    `reviewed` tracks that the table does not allow, or a table its JSON does not describe.

    The table is generated and gitignored and the record is tracked, and until this nothing
    joined them when a game was linked. A tree that adds an exception and links a table
    generated before it builds, runs, and traps exactly where the exception says it should
    not: the 2026-09-21 failure the record exists for, the other way round (issue #16's fix
    is four such entries). A table carrying more exceptions than the tree tracks is not
    refused here; Documentation/scripts/check_dispatch_exceptions.py names those."""
    j = c_path.with_suffix(".json")
    if not j.is_file():
        return [f"{c_path} has no {j.name} beside it, so which reviewed exceptions it allows "
                "is not known; regenerate both with tools/s4/mb_edges.py"]
    doc = json.loads(j.read_text())
    if doc.get("output_c_sha256") != C.sha256(c_path):
        return [f"{j} describes another {c_path.name} (its output_c_sha256 is not this "
                "file's); regenerate both with tools/s4/mb_edges.py"]
    allowed = {f"0x{int(a, 0):08x}" for a in doc.get("named_exception_targets") or []}
    want = [f"0x{va:08x}" for va in reviewed_allow(reviewed, tag)]
    cmd = (f".venv/bin/python tools/s4/mb_edges.py --tag {tag} --lift-tag {doc.get('lift_tag')}"
           + (f" --arms-tag {doc['arms_tag']}" if doc.get("arms_tag") else "")
           + (f" --allow-from {doc['allow_from']}" if doc.get("allow_from") else ""))
    return [f"{va} is a reviewed dispatch exception in {reviewed.name} that {c_path} does "
            f"not allow: the table predates it. Regenerate it: {cmd}"
            for va in want if va not in allowed]


def emit(edges: list[dict], path: Path, tag: str) -> None:
    out = [
        "/* GENERATED by tools/s4/mb_edges.py -- do not edit, never committed.",
        " *",
        " * The missing-block reaching-instruction table for the whole-image build.",
        " * One row per guest address a lifted module can reach with no block for it,",
        " * carrying the instruction that reaches it. SA2_MB_REACHED dispatches only a",
        f" * `jmp` edge. Tag: {tag}.",
        " */",
        "#include <stddef.h>",
        '#include "remill_runtime.h"',
        "",
        f"static const sa2_mb_edge kEdges[] = {{  /* {len(edges)} edges */",
    ]
    for e in edges:
        why = json.dumps(e["why"]) if e.get("why") else "NULL"
        out.append(f"    {{0x{e['target']:08x}u, 0x{e['source']:08x}u, {e['kind']}, "
                   f"{1 if e.get('allow') else 0}, {why}}},"
                   f"  /* {e['kind_name']}: {e['insn']} in {e['module']} */")
    out += ["};", "",
            "void sa2_mb_register_generated(void) {",
            f"    sa2_mb_set_edges(kEdges, {len(edges)}u);",
            "}", ""]
    path.write_text("\n".join(out))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True, help="the build tag whose objects are linked")
    ap.add_argument("--lift-tag", required=True, help="the base lift log to read ranges from")
    ap.add_argument("--arms-tag", default=None, help="the arms lift log, when the tag has arms")
    ap.add_argument("--allow-from", type=Path, default=None,
                    help="tools/s4/lift_extra.py's state JSON: every block it registered "
                         "becomes a named dispatch exception with its recorded reason. "
                         "Defaults to the tag's own state when it exists")
    ap.add_argument("--reviewed", type=Path, default=REVIEWED,
                    help="the tracked reviewed-exception record; pass an empty path to "
                         "skip it, which is how the falsification in "
                         "Documentation/scripts/check_dispatch_exceptions.py works")
    ap.add_argument("--allow-target", action="append", default=[],
                    help="VA=reason: one named dispatch exception, reviewed individually. "
                         "This exists for the case --allow-from cannot cover, because it is "
                         "not about a separately lifted block: a *data* region inside a "
                         "module's lifted byte range decodes as instructions under a linear "
                         "decode, and the fall-through that invents produces a second edge "
                         "into an address that a real `jmp` also reaches. The strictest kind "
                         "then wins and downgrades the real tail call. Each of these is one "
                         "address with one written reason, not a group.")
    ap.add_argument("--no-object-check", action="store_true",
                    help="emit even when the logs and the objects name different module sets")
    args = ap.parse_args()
    if args.allow_from is None:
        # Left out, the extras were silently not modules here: a table with no extra's
        # edges and none of their exceptions, which the link then refuses only by luck of
        # a reviewed exception the table lacks. The tag's own state is the right default.
        own = C.GEN / "s4" / "link" / f"extra_functions.{args.tag}.json"
        if own.is_file():
            args.allow_from = own
            print(f"--allow-from defaults to {own.relative_to(C.ROOT)}")

    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    ranges, prov = module_ranges(args.lift_tag, args.arms_tag)
    check = None if args.no_object_check else check_against_objects(ranges, args.tag)
    image = C.EXE.read_bytes()
    pe_off = int.from_bytes(image[0x3C:0x40], "little")
    # section headers -> a flat image view the ranges index into
    view = bytearray(0x02375000)
    nsec = int.from_bytes(image[pe_off + 6:pe_off + 8], "little")
    opt = int.from_bytes(image[pe_off + 20:pe_off + 22], "little")
    sh = pe_off + 24 + opt
    for i in range(nsec):
        h = sh + i * 40
        va = int.from_bytes(image[h + 12:h + 16], "little")
        raw = int.from_bytes(image[h + 16:h + 20], "little")
        ptr = int.from_bytes(image[h + 20:h + 24], "little")
        if va + raw <= len(view):
            view[va:va + raw] = image[ptr:ptr + raw]

    extra_entries: set[int] = set()
    extra_ranges = 0
    if args.allow_from:
        extra_rows = [r for r in json.loads(args.allow_from.read_text()).get("rows", [])
                      if r.get("compiled") and not r.get("rejected")]
        extra_entries = {int(r["va"], 16) for r in extra_rows}
        # The extras are modules too, and until 2026-09-23 their edges were
        # decoded only where some map module's range happened to cover their
        # bytes. Each is now decoded over the range it was lifted from -- its
        # anchor window for an arm -- with its own entry live. Measured against
        # the previous table by the rule-5 review: seven rows more, all
        # jcc-taken loop back-edges in extras that start mid-function, no new
        # `jmp` row and no edge changing kind. An arm's window also seeds its
        # anchor, so it repeats its owner module's rows and only lengthens their
        # `reached_by`. (The Metal Harbor stop on 0x0084AFD0 that prompted this
        # was the table not being regenerated after a lift; reach_extend.py now
        # regenerates it.)
        for r in extra_rows:
            va = int(r["va"], 16)
            if va in ranges:
                continue
            lo = int(r["anchor"], 16) if r.get("anchor") else va
            ranges[va] = (min(lo, va), int(r["end"], 16))
            extra_ranges += 1
    edges, conflicts = decode_edges(bytes(view), ranges, extra_entries)
    allow: dict[int, str] = {}
    if args.allow_from:
        doc = json.loads(args.allow_from.read_text())
        for r in doc.get("rows", []):
            if r.get("compiled"):
                allow[int(r["va"], 16)] = (
                    f"lifted as its own module by tools/s4/lift_extra.py because it is in no "
                    f"module the whole-image lift produced: {r.get('why', '')}")
    reviewed: dict[str, str] = {}
    # The tracked reviewed exceptions, read before any --allow-target so a
    # command-line argument can still override one by address.
    #
    # **This file exists because the arguments did not survive.** Every reviewed
    # exception used to be a `--allow-target VA=reason` on somebody's command
    # line, recorded only in the generated `.c` this script overwrites. On
    # 2026-09-21 a routine regeneration dropped 0x00826f60 -- its row went from
    # `allow=1` with a reason to `allow=0` with none -- and the driven run fell
    # from 369 WinMain iterations to 1 with every gate still passing. A reason
    # that lives only in the output is a reason one regeneration away from
    # being gone.
    if args.reviewed and args.reviewed.is_file():
        for va, why in reviewed_allow(args.reviewed, args.tag).items():
            allow[va] = why
            reviewed[f"0x{va:08x}"] = why
    elif args.reviewed:
        raise SystemExit(f"{args.reviewed} does not exist. It is a tracked record, not an "
                         "optional one: without it every reviewed dispatch exception is "
                         "silently absent and the build still succeeds.")
    for spec in args.allow_target:
        if "=" not in spec:
            raise SystemExit(f"--allow-target {spec!r} is not VA=reason")
        va, why = spec.split("=", 1)
        if not why.strip():
            raise SystemExit(f"--allow-target {spec!r} has no reason, and an exception "
                             f"without a recorded reason is the thing rule 14 forbids")
        allow[int(va, 0)] = why
        reviewed[f"0x{int(va, 0):08x}"] = why
    allowed = apply_allow(edges, allow)
    c_path = OUT / f"mb_edges.{args.tag}.c"
    emit(edges, c_path, args.tag)

    by_kind = {}
    for e in edges:
        by_kind[e["kind_name"]] = by_kind.get(e["kind_name"], 0) + 1
    # Every edge that traps if taken, one row per (target, source), so the record of why each
    # one is left that way (Documentation/dispatch-exceptions.json `refused`) can be held to the
    # table edge by edge. Issue #16 was one of these: a `jne` from FUN_007059a0 into
    # FUN_007059af, its own body, that no record named and that stopped the game at Pyramid
    # Cave's title card. A target refused by kind refuses every edge into it, a `jmp` included,
    # because the runtime looks the row up by target.
    refused = [{"target": f"0x{e['target']:08x}", "source": f"0x{s['source']:08x}",
                "kind": s["kind_name"], "insn": s["insn"], "module": s["module"]}
               for e in edges if e["kind"] != EDGE_JMP and not e.get("allow")
               for s in e["sources"]]
    doc = {"schema": "sa2-recomp-measurement/v1", "id": "s4-mb-edges",
           "question": "which guest addresses can a lifted module reach with no block, "
                       "and by what kind of edge?",
           "tag": args.tag, "lift_tag": args.lift_tag, "arms_tag": args.arms_tag,
           "provenance": prov, "object_check": check,
           "modules": len(ranges) - extra_ranges, "extra_modules_decoded": extra_ranges, "edges": len(edges), "by_kind": by_kind,
           "dispatchable_under_reached": by_kind.get("jmp", 0),
           "conflicts": len(conflicts), "conflicts_head": conflicts[:20],
           # Lexically relative to the checkout, so the record names no working directory.
           "allow_from": (str(Path(os.path.abspath(args.allow_from)).relative_to(C.ROOT))
                          if Path(os.path.abspath(args.allow_from)).is_relative_to(C.ROOT)
                          else str(args.allow_from)) if args.allow_from else None,
           "named_exceptions": allowed, "reviewed_exceptions": reviewed,
           "named_exception_targets": [f"0x{e['target']:08x}" for e in edges if e.get("allow")],
           "refused": refused,
           "exe_sha256": C.sha256(C.EXE),
           "output_c": str(c_path), "output_c_sha256": C.sha256(c_path),
           "wall_seconds": round(time.time() - t0, 2)}
    j_path = OUT / f"mb_edges.{args.tag}.json"
    j_path.write_text(json.dumps(doc, indent=1) + "\n")
    print(f"{len(ranges) - extra_ranges} modules and {extra_ranges} extras -> {len(edges)} edges "
          f"({by_kind.get('jmp', 0)} dispatchable as tail calls), "
          f"{allowed} named exceptions, {len(refused)} refused edges into "
          f"{len({r['target'] for r in refused})} targets, "
          f"{len(conflicts)} targets reached by more than one kind, "
          f"{doc['wall_seconds']}s")
    print(f"-> {c_path}\n-> {j_path}")


if __name__ == "__main__":
    main()
