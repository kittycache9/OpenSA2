#!/usr/bin/env python3
"""The DRM-unwrap measurement: do the bytes the runtime reads from the game executable match
between the shipped file and the unpacked one?

Subtitle: `.venv/bin/python tools/s7/drm_unwrap.py` writes
`Documentation/receipts/s7-drm-unwrap-v1.json`; `--check` computes it again and fails when the
tracked receipt, or its private companion, says anything else. `--companion` writes only the
companion (the read-set `tools/s7/text_readset.py` checks every release build against), from the
two executables alone, and refuses unless its sha256 is the one the tracked receipt holds: a fresh
checkout has none of the replay run records the full measurement reads, and the companion never
depended on them.

The question is the user's (`Documentation/decisions/2026-10-09-S5-user-rulings.md`, item 3):
"compare the bytes the runtime reads at run time (data sections, and any code bytes the emulated
instructions or jump tables read) in the shipped executable against the unpacked one. If they
match, the installer reads the shipped file as-is, verifies exactly those bytes, and no unwrap step
ships. If they don't, write up what the runtime needs and the options for me to rule on."

What it reads
-------------
* **The two images as the runtime maps them** (`runtime/guest_memory.c`, `pe_parse` and
  `sa2_guest_init`): the first SizeOfHeaders bytes at the image base, then each section's file
  bytes, at most its virtual size, at its VA; zero elsewhere. Whether the runtime would map a file
  at all (its loader's refusals) is recorded per file. Regions are keyed by VA range, never by
  name: the two files name the same bytes `.data` and `.dataa`.
* **The data sections, whole.** Every mapped byte of a section that is not executable counts as
  read: the lifted code's globals, the import descriptors `sa2_imports_bind` walks, the resource
  tree the dialog code walks and the emulated instructions' memory operands all live there, and
  nothing narrower is measured.
* **What the runtime reads of the executable sections**, from the censuses the lifted build is
  held to:
  - every jump table `tools/s4/switches.py` finds, wherever it sits. A table's dwords are the ones
    that hold its arms (`table_ranges`): the census's entries, widened to the relocated dwords near
    the displacement that hold an arm the entries miss (the C runtime's offset-key `memcpy` and
    `memmove` tables). An index table without a key bound (`layout`) runs to the next table or
    padding byte and is marked estimated. Every table's extent is labelled exact or not, and an arm
    found in no dword near its table is counted;
  - every emulated-instruction site `tools/s4/emulated.py` finds, with its instruction's bytes,
    from its address to the next one: the bytes that decide what `runtime/sa2_emulate.cpp`
    decodes. The decoder peeks up to 15 bytes; the rest are not counted. The instructions' memory
    operands are data reads, counted by the data sections where they lie in the executable.
* **The header fields the runtime reads** in `pe_parse` and the section loop, in
  `sa2_imports_bind` and in the dialog resource lookup: e_lfanew, the signature, the section
  count, the optional header's size, magic, image base, image size and header size, the import and
  resource directory entries, and each section entry.
* **Each registered replay's lifted run records**, read from the tick-replay receipt's named keys
  (`lifted.run_json`, `lifted.baseline.run_json`), checked against `run_json_sha256` where it is
  given: their `emulated.by_kind` counts. A run that executed a kind whose every census site
  differs between the files read a differing byte, from the record alone; a kind with an equal
  site decides nothing.

The verdict
-----------
`verdict` is "differs" when any byte counted above differs, and "no differing byte measured"
otherwise. `conclusive` is true only for "differs", or for a match with nothing left in
`not_measured`. `bytes_match` is true only for a conclusive match: a match over what is measured,
with paths left unmeasured, is not one the installer may act on.

`written` is the instant this verdict was first written. A rewrite that leaves the verdict, the
executables and the read-set digest unchanged carries it forward, so the order "the measurement
first" survives a receipt refreshed for an unrelated input; `rewritten` is the last write.

Nothing it writes carries a shipped byte: counts, addresses, sizes and hashes only. The address
lists go to a private companion whose sha256 the tracked receipt holds.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
S4 = ROOT / "tools" / "s4"

RECEIPT = "Documentation/receipts/s7-drm-unwrap-v1.json"
SCHEMA = "sa2-s7-drm-unwrap/v1"
GENERATED_BY = "tools/s7/drm_unwrap.py"
COMPANION = "private/generated/s7/drm-unwrap/readset.json"
SHIPPED = "private/exe/sonic2app.exe"
UNPACKED = "private/exe/sonic2app-unpacked.exe"
PINS = "tools/pins.json"
REPLAYS = "Documentation/replays.json"
TICK_RECEIPT = "Documentation/receipts/s4-tick-replay-v1.json"
TOOLS = ("tools/s7/drm_unwrap.py", "tools/s4/switches.py", "tools/s4/emulated.py",
         "tools/s4/image.py", "tools/s3/whole_image/common.py", "tools/s3/arms/cost.py",
         "Documentation/lifted-extras.json", "runtime/guest_memory.c", "runtime/sa2_emulate.cpp",
         "runtime/sa2_imports.c", "runtime/sa2_dialog.c", "runtime/guest_memory.h")
EXEC = 0x20000000
# runtime/guest_memory.c's refusals: the image base, the section table's size, the optional
# header's least size, and the image's end against the thread-stack arena.
GUEST_IMAGE_BASE = 0x00400000
GUEST_MAX_SECTIONS = 32
GUEST_MIN_OPT = 0x60
GUEST_TSTACK_BASE = 0x02800000
NEAR = 64                 # bytes either side of a table's displacement searched for arm dwords
INDEX_LIMIT = 256         # longest index table read by layout
PAD = 0xCC
NOT_MEASURED = [
    "lifted reads of the executable sections other than jump tables (data in code)",
    "guest reads of the header page other than the fields the runtime's loader, import binding "
    "and resource lookup read",
    "the bytes the emulator's decoder peeks past an emulated instruction's end",
    "guest file reads of the executable itself (no registered replay's run record opens it)",
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest(doc) -> str:
    return hashlib.sha256(json.dumps(doc, indent=1, sort_keys=True).encode()).hexdigest()


# ------------------------------------------------------------------ the images


def pe_layout(data: bytes) -> dict:
    """The fields runtime/guest_memory.c's pe_parse and section loop read, and the two data
    directory entries the import binding and resource lookup read."""
    if data[:2] != b"MZ":
        raise ValueError("no MZ header")
    lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    if data[lfanew:lfanew + 4] != b"PE\0\0":
        raise ValueError("no PE signature")
    nsec, = struct.unpack_from("<H", data, lfanew + 6)
    opt_size, = struct.unpack_from("<H", data, lfanew + 20)
    opt = lfanew + 24
    magic, = struct.unpack_from("<H", data, opt)
    base, = struct.unpack_from("<I", data, opt + 28)
    size_of_image, size_of_headers = struct.unpack_from("<II", data, opt + 56)
    dirs = opt + 96
    imports = struct.unpack_from("<II", data, dirs + 8)
    resources = struct.unpack_from("<II", data, dirs + 16)
    sec_off = opt + opt_size
    sections = []
    for i in range(nsec):
        s = sec_off + 40 * i
        name = data[s:s + 8].rstrip(b"\0").decode("ascii", "replace")
        vsize, va, raw, rawptr = struct.unpack_from("<IIII", data, s + 8)
        chars, = struct.unpack_from("<I", data, s + 36)
        sections.append({"name": name, "va": base + va, "virtual_size": vsize, "raw_size": raw,
                         "raw_pointer": rawptr, "characteristics": chars})
    return {"e_lfanew": lfanew, "sections_count": nsec, "optional_header_size": opt_size,
            "magic": magic, "image_base": base, "size_of_image": size_of_image,
            "size_of_headers": size_of_headers, "import_directory": list(imports),
            "resource_directory": list(resources), "sections": sections}


def guest_constants(root: Path) -> list[str]:
    """Where runtime/guest_memory.h's constants and this module's mirrors of them disagree."""
    text = (root / "runtime" / "guest_memory.h").read_text()
    out = []
    for name, want in (("SA2_GUEST_IMAGE_BASE", GUEST_IMAGE_BASE),
                       ("SA2_GUEST_TSTACK_BASE", GUEST_TSTACK_BASE),
                       ("SA2_MAX_SECTIONS", GUEST_MAX_SECTIONS)):
        m = re.search(rf"#define\s+{name}\s+(0x[0-9A-Fa-f]+|\d+)u?", text)
        if not m or int(m.group(1), 0) != want:
            out.append(name)
    return out


def runtime_maps(layout: dict) -> list[str]:
    """The loader's refusals this file meets; empty when the runtime would map it."""
    out = []
    if layout["magic"] != 0x10B:
        out.append("not PE32")
    if layout["image_base"] != GUEST_IMAGE_BASE:
        out.append("image base")
    if not 0 < layout["sections_count"] <= GUEST_MAX_SECTIONS:
        out.append("section count")
    if layout["optional_header_size"] < GUEST_MIN_OPT:
        out.append("optional header size")
    if layout["image_base"] + layout["size_of_image"] > GUEST_TSTACK_BASE:
        out.append("image end past the thread-stack arena")
    return out


def file_bytes(data: bytes, s: dict) -> int:
    """How many of a section's file bytes the loader copies."""
    n = s["raw_size"]
    if s["raw_pointer"] >= len(data):
        return 0
    if s["raw_pointer"] + n > len(data):
        n = len(data) - s["raw_pointer"]
    if s["virtual_size"] and n > s["virtual_size"]:
        n = s["virtual_size"]
    return n


def mapped(data: bytes, layout: dict, lo: int, hi: int) -> bytearray:
    """[lo, hi) of guest memory as the runtime's loader leaves it: headers, then each section's
    file bytes up to its virtual size, zero elsewhere."""
    view = bytearray(hi - lo)

    def put(va: int, chunk: bytes) -> None:
        a, b = max(va, lo), min(va + len(chunk), hi)
        if a < b:
            view[a - lo:b - lo] = chunk[a - va:b - va]

    put(layout["image_base"], data[:min(layout["size_of_headers"], len(data))])
    for s in layout["sections"]:
        n = file_bytes(data, s)
        put(s["va"], data[s["raw_pointer"]:s["raw_pointer"] + n])
    return view


def differing(a: bytes, b: bytes, lo: int, ranges: list[tuple[int, int]]) -> tuple[int, int]:
    """(bytes in the ranges, bytes in the ranges where a and b differ); views start at lo.
    Overlapping ranges count each byte once."""
    seen = set()
    total = diff = 0
    for x, y in ranges:
        for va in range(x, y):
            if va in seen:
                continue
            seen.add(va)
            total += 1
            diff += a[va - lo] != b[va - lo]
    return total, diff


def section_extent(s: dict) -> int:
    return max(s["virtual_size"], s["raw_size"])


def regions(la: dict, lb: dict) -> list[dict]:
    """The VA ranges of both section tables and the header page, cut at every boundary either
    file has, each with the names each file gives it and whether it is executable in each."""
    cuts = {la["image_base"], la["image_base"] + la["size_of_headers"],
            lb["image_base"] + lb["size_of_headers"]}
    for lay in (la, lb):
        for s in lay["sections"]:
            cuts |= {s["va"], s["va"] + section_extent(s)}
    cuts = sorted(cuts)

    def held(lay, lo, hi):
        return [s for s in lay["sections"] if s["va"] < hi and lo < s["va"] + section_extent(s)]

    def head(lay, lo, hi):
        return lo < lay["image_base"] + lay["size_of_headers"] and hi > lay["image_base"]

    out = []
    for lo, hi in zip(cuts, cuts[1:]):
        sa, sb = held(la, lo, hi), held(lb, lo, hi)
        ha, hb = head(la, lo, hi), head(lb, lo, hi)
        if not (sa or sb or ha or hb):
            continue
        kind = ("header" if ha or hb else
                "one file" if not (sa and sb) else
                "executable" if any(s["characteristics"] & EXEC for s in sa + sb) else "data")
        out.append({"lo": lo, "hi": hi, "kind": kind,
                    "shipped": (["(headers)"] if ha else []) + [s["name"] for s in sa],
                    "unpacked": (["(headers)"] if hb else []) + [s["name"] for s in sb]})
    return out


def header_fields(la: dict, lb: dict) -> dict:
    """Each header field the runtime reads, equal or not, by name."""
    keys = ("e_lfanew", "sections_count", "optional_header_size", "magic", "image_base",
            "size_of_image", "size_of_headers", "import_directory", "resource_directory")
    out = {k: la[k] == lb[k] for k in keys}
    by_va = {s["va"]: s for s in lb["sections"]}
    for s in la["sections"]:
        t = by_va.get(s["va"])
        out[f"section {s['va']:#010x}"] = t is not None and all(
            s[k] == t[k] for k in ("virtual_size", "raw_size", "characteristics"))
    for s in lb["sections"]:
        out.setdefault(f"section {s['va']:#010x}", False)
    return out


# ------------------------------------------------------------------ what the runtime reads


def table_ranges(t: dict, dword, relocated: set[int], starts: list[int],
                 byte) -> tuple[list[tuple[int, int]], bool, int]:
    """A jump table's byte ranges, whether they are exact, and how many of its arms no dword near
    its displacement holds. `dword(va)`/`byte(va)` read the unpacked image; `relocated` holds the
    VAs of its relocated dwords; `starts` every table and index-table start, sorted."""
    n = t["entries"] or t["cases"] or len(t["arms"]) or 1
    ranges = [(t["table"], t["table"] + 4 * n)]
    exact = t["bound_from"] == "check"
    arms = set(t["arms"])
    held = {dword(a) for a in range(t["table"], t["table"] + 4 * n, 4) if a in relocated}
    missing = arms - held
    if missing:
        exact = False
        for a in range(t["table"] - NEAR, t["table"] + 4 * n + NEAR, 4):
            if a in relocated and dword(a) in missing:
                ranges.append((a, a + 4))
                held.add(dword(a))
        missing = arms - held
    if t["index_table"] is not None:
        if t["cases"]:
            ranges.append((t["index_table"], t["index_table"] + t["cases"]))
        else:
            exact = False
            nxt = next((s for s in starts if s > t["index_table"]), t["index_table"] + INDEX_LIMIT)
            end = t["index_table"]
            while end < min(nxt, t["index_table"] + INDEX_LIMIT) and byte(end) != PAD:
                end += 1
            ranges.append((t["index_table"], max(end, t["index_table"] + 1)))
    return ranges, exact, len(missing)


def jump_tables() -> tuple[list[dict], set[int]]:
    """Every dispatch the switches census finds, and the unpacked image's relocated dwords."""
    sys.path.insert(0, str(S4))
    sys.path.insert(0, str(ROOT / "tools" / "s3" / "whole_image"))
    import common as C  # noqa: PLC0415
    import image as IM  # noqa: PLC0415
    import switches as SW  # noqa: PLC0415
    img = IM.Image()
    extras = json.loads((ROOT / "Documentation" / "lifted-extras.json").read_text())["rows"]
    ranges = [(int(d["va"], 16), int(d["end"], 16))
              for d in C.read_jsonl(C.LIFT / "next.jsonl").values()]
    ranges += [(int(r["va"], 16), int(r["end"], 16)) for r in extras]
    found = SW.census(img, ranges, SW.recorded_tables(C.by_address()))
    out = [{"dispatch": sw.dispatch, "table": sw.table, "index_table": sw.index_table,
            "entries": sw.entries, "cases": sw.cases, "arms": list(sw.arms),
            "bound_from": sw.bound_from, "problem": sw.problem}
           for sw in sorted(found.values(), key=lambda s: s.dispatch)]
    return out, set(img.relocated)


def emulated_sites() -> list[dict]:
    """Every emulated-instruction site the census reaches, with its instruction's extent."""
    sys.path.insert(0, str(S4))
    sys.path.insert(0, str(ROOT / "tools" / "s3" / "whole_image"))
    import common as C  # noqa: PLC0415
    import emulated as EM  # noqa: PLC0415
    import image as IM  # noqa: PLC0415
    extras = json.loads((ROOT / "Documentation" / "lifted-extras.json").read_text())["rows"]
    found = EM.census(IM.Image(), C, extras)
    return [{"ip": s.ip, "next_ip": s.next_ip, "kind": s.kind}
            for s in sorted(found.values(), key=lambda s: s.ip)]


def replay_runs(root: Path) -> dict[str, list[dict]]:
    """Each registered replay's lifted run records from the tick-replay receipt's named keys,
    each with the sha256 the receipt pins, if it pins one."""
    names = [r["name"] for r in json.loads((root / REPLAYS).read_text())["replays"]]
    by = {r["name"]: r for r in json.loads((root / TICK_RECEIPT).read_text())["replays"]}
    out = {}
    for n in names:
        lifted = by.get(n, {}).get("lifted") or {}
        named = [(lifted.get("run_json"), lifted.get("run_json_sha256"))]
        base = lifted.get("baseline") or {}
        named.append((base.get("run_json"), base.get("run_json_sha256")))
        out[n] = [{"path": p, "pinned": s} for p, s in named if isinstance(p, str) and p]
    return out


def emulated_chain(by_kind: dict, sites: list[dict], site_differs: dict[int, bool]) -> dict:
    """For a run's per-kind emulated counts: the kinds it executed whose every census site
    differs (each such execution read a differing byte), the kinds that decide nothing, and any
    kind it executed that the census has no site of."""
    certain, undecided, unknown = [], [], []
    for kind, n in sorted(by_kind.items()):
        if not isinstance(n, int) or n <= 0:
            continue
        of = [s for s in sites if s["kind"] == kind]
        if not of:
            unknown.append(kind)
        elif all(site_differs[s["ip"]] for s in of):
            certain.append(kind)
        else:
            undecided.append(kind)
    return {"executed_kinds_every_site_differs": certain, "executed_kinds_undecided": undecided,
            "executed_kinds_without_census_sites": unknown,
            "witness_basis": "emulated counts only", "differing_read_witnessed": bool(certain)}


# ------------------------------------------------------------------ the receipt


def measure(root: Path, tables: list[dict] | None = None, relocated: set[int] | None = None,
            sites: list[dict] | None = None, tool_root: Path | None = None,
            with_replays: bool = True) -> tuple[dict, dict]:
    """The receipt and its companion. `with_replays=False` skips the replay run records, which
    only the receipt reads; the companion comes out the same (`--companion`)."""
    tool_root = root if tool_root is None else tool_root
    pins = json.loads((root / PINS).read_text())["executables"]
    a_bytes, b_bytes = (root / SHIPPED).read_bytes(), (root / UNPACKED).read_bytes()
    a_sha, b_sha = hashlib.sha256(a_bytes).hexdigest(), hashlib.sha256(b_bytes).hexdigest()
    if a_sha != pins["sonic2app.exe"]["sha256"] or b_sha != pins["sonic2app-unpacked.exe"]["sha256"]:
        raise SystemExit("an executable does not match its pin in tools/pins.json")
    wrong = guest_constants(tool_root)
    if wrong:
        raise SystemExit("runtime/guest_memory.h disagrees with this module's mirrors: "
                         + ", ".join(wrong))
    la, lb = pe_layout(a_bytes), pe_layout(b_bytes)
    lo = min(la["image_base"], lb["image_base"])
    hi = max(la["image_base"] + la["size_of_image"], lb["image_base"] + lb["size_of_image"],
             *(s["va"] + section_extent(s) for s in la["sections"] + lb["sections"]))
    va, vb = mapped(a_bytes, la, lo, hi), mapped(b_bytes, lb, lo, hi)

    def dword(x: int) -> int:
        return struct.unpack_from("<I", vb, x - lo)[0] if lo <= x <= hi - 4 else -1

    def byte(x: int) -> int:
        return vb[x - lo] if lo <= x < hi else PAD

    regs = regions(la, lb)
    for g in regs:
        g["bytes"], g["differing"] = differing(va, vb, lo, [(g["lo"], g["hi"])])
    data_differing = sum(g["differing"] for g in regs if g["kind"] == "data")

    if tables is None:
        tables, relocated = jump_tables()
    relocated = relocated or set()
    sites = emulated_sites() if sites is None else sites
    starts = sorted({t["table"] for t in tables}
                    | {t["index_table"] for t in tables if t["index_table"] is not None})
    exec_regions = [(g["lo"], g["hi"]) for g in regs if g["kind"] == "executable"]

    def where(r):
        return "executable" if any(x <= r[0] and r[1] <= y for x, y in exec_regions) else "data"

    jt_ranges, per_table = [], []
    for t in tables:
        rs, exact, missing = table_ranges(t, dword, relocated, starts, byte)
        jt_ranges += rs
        per_table.append({"dispatch": f"{t['dispatch']:#010x}",
                          "ranges": [[f"{x:#010x}", f"{y:#010x}", where((x, y))] for x, y in rs],
                          "bound_from": t["bound_from"], "exact": exact,
                          "arms_in_no_near_dword": missing,
                          "differing": differing(va, vb, lo, rs)[1]})
    unheld = [t["dispatch"] for t in per_table if t["arms_in_no_near_dword"]]
    if unheld:
        raise SystemExit("jump tables with an arm no dword near the table holds: " + ", ".join(unheld))
    jt_total, jt_diff = differing(va, vb, lo, jt_ranges)
    em_ranges = [(s["ip"], s["next_ip"]) for s in sites]
    em_total, em_diff = differing(va, vb, lo, em_ranges)
    site_differs = {s["ip"]: differing(va, vb, lo, [(s["ip"], s["next_ip"])])[1] > 0
                    for s in sites}
    kinds = {}
    for s in sites:
        k = kinds.setdefault(s["kind"], {"sites": 0, "differing_sites": 0})
        k["sites"] += 1
        k["differing_sites"] += site_differs[s["ip"]]
    head = header_fields(la, lb)
    header_differs = sorted(k for k, ok in head.items() if not ok)
    union_total, union_diff = differing(va, vb, lo, jt_ranges + em_ranges)

    replays, problems = {}, []
    for name, records in (replay_runs(root) if with_replays else {}).items():
        runs, seen = [], set()
        for rec in records:
            path = root / rec["path"]
            if not path.is_file():
                problems.append(f"{name}: run record {rec['path']} is absent")
                continue
            got = sha256(path)
            if rec["pinned"] and rec["pinned"] != got:
                problems.append(f"{name}: run record {rec['path']} is not the one the tick-replay "
                                "receipt pins")
                continue
            if got in seen:
                continue
            seen.add(got)
            by_kind = (json.loads(path.read_text()).get("emulated") or {}).get("by_kind") or {}
            chain = emulated_chain(by_kind, sites, site_differs)
            if chain["executed_kinds_without_census_sites"]:
                problems.append(f"{name}: executed emulated kinds the census has no site of: "
                                + ", ".join(chain["executed_kinds_without_census_sites"]))
            runs.append({"run_record": rec["path"], "sha256": got,
                         "pinned": bool(rec["pinned"]), "emulated_by_kind": by_kind, **chain})
        if not runs:
            problems.append(f"{name}: no lifted run record")
        replays[name] = {"runs": runs, "differing_read_witnessed":
                         any(r["differing_read_witnessed"] for r in runs)}
    if problems:
        raise SystemExit("; ".join(problems))

    refusals = {"shipped": runtime_maps(la), "unpacked": runtime_maps(lb)}
    # where a difference was found, by how sure each place is: an exact table, an emulated
    # site, a header field the runtime reads, the data sections, or a run that witnessed one
    differs_in = [name for name, hit in (
        ("exact jump tables", any(t["differing"] and t["exact"] for t in per_table)),
        ("emulated sites", em_diff > 0), ("header fields", bool(header_differs)),
        ("data sections", data_differing > 0),
        ("a witnessed run", any(r["differing_read_witnessed"] for r in replays.values())),
        ("estimated jump-table extents", any(t["differing"] and not t["exact"] for t in per_table)))
        if hit]
    differs = bool(differs_in)
    sure = [d for d in differs_in if d != "estimated jump-table extents"]
    conclusive = bool(sure) if differs else not NOT_MEASURED and not any(refusals.values())
    companion = {"schema": "sa2-s7-drm-unwrap-readset/v1",
                 "regions": [{**g, "lo": f"{g['lo']:#010x}", "hi": f"{g['hi']:#010x}"}
                             for g in regs],
                 "jump_tables": per_table,
                 "emulated_sites": [{"ip": f"{s['ip']:#010x}", "length": s["next_ip"] - s["ip"],
                                     "kind": s["kind"], "differs": site_differs[s["ip"]]}
                                    for s in sites]}
    receipt = {
        "schema": SCHEMA, "id": "s7-drm-unwrap-v1", "generated_by": GENERATED_BY,
        "question": "Do the bytes the runtime reads at run time from the game executable (the data "
                    "sections, and the code bytes the emulated instructions and jump tables read) "
                    "match between the shipped executable and the unpacked one?",
        "authority": "the user's ruling of 2026-10-09, Documentation/decisions/2026-10-09-S5-user-"
                     "rulings.md, item 3",
        "command": "source tools/env.sh && .venv/bin/python tools/s7/drm_unwrap.py",
        "inputs": {"shipped": {"path": SHIPPED, "sha256": a_sha},
                   "unpacked": {"path": UNPACKED, "sha256": b_sha},
                   "pins": {"path": PINS, "sha256": sha256(root / PINS), "match": True},
                   "replays": {"path": REPLAYS, "sha256": sha256(root / REPLAYS)},
                   "tick_replay": {"path": TICK_RECEIPT, "sha256": sha256(root / TICK_RECEIPT)
                                   if with_replays else None},
                   "tools": {t: sha256(tool_root / t) for t in TOOLS}},
        "runtime_maps": {k: v or "yes" for k, v in refusals.items()},
        "regions": [{"va": f"{g['lo']:#010x}", "end": f"{g['hi']:#010x}", "kind": g["kind"],
                     "shipped": g["shipped"], "unpacked": g["unpacked"], "bytes": g["bytes"],
                     "differing": g["differing"]} for g in regs],
        "data_sections": {"read": "every mapped byte",
                          "bytes": sum(g["bytes"] for g in regs if g["kind"] == "data"),
                          "differing_bytes": data_differing},
        "header_fields": {"compared": len(head), "differing": header_differs},
        "readsets": {
            "jump_tables": {"dispatches": len(tables), "bytes": jt_total, "differing_bytes": jt_diff,
                            "dispatches_with_a_differing_byte":
                                sum(1 for t in per_table if t["differing"]),
                            "dispatches_not_exact": sum(1 for t in per_table if not t["exact"]),
                            "arms_in_no_near_dword":
                                sum(t["arms_in_no_near_dword"] for t in per_table),
                            "dispatches_outside_executable":
                                sum(1 for t in per_table
                                    if any(r[2] == "data" for r in t["ranges"]))},
            "emulated_sites": {"sites": len(sites), "bytes": em_total, "differing_bytes": em_diff,
                               "window": "the instruction's own bytes, which decide the decode",
                               "by_kind": kinds},
            "union": {"bytes": union_total, "differing_bytes": union_diff},
        },
        "replays": replays,
        "not_measured": NOT_MEASURED,
        "verdict": "differs" if differs else "no differing byte measured",
        "differs_in": differs_in,
        "conclusive": conclusive,
        "bytes_match": (not differs) and conclusive,
        "companion": {"path": COMPANION, "sha256": digest(companion)},
    }
    return receipt, companion


def comparable(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k not in ("written", "rewritten")}


def same_verdict(a: dict, b: dict) -> bool:
    """Whether two receipts give the same verdict on the same executables and read-set."""
    return all(a.get(k) == b.get(k) for k in ("verdict", "bytes_match", "conclusive", "companion")) \
        and a.get("inputs", {}).get("shipped") == b.get("inputs", {}).get("shipped") \
        and a.get("inputs", {}).get("unpacked") == b.get("inputs", {}).get("unpacked")


def instant(value: object) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def committed_receipt(root: Path) -> dict | None:
    """The receipt as the repository's HEAD holds it, if it does."""
    import subprocess  # noqa: PLC0415
    try:
        text = subprocess.run(["git", "-C", str(root), "show", f"HEAD:{RECEIPT}"],
                              capture_output=True, text=True, check=True).stdout
        return json.loads(text)
    except (OSError, subprocess.CalledProcessError, ValueError):
        return None


def check(root: Path, receipt: dict, companion: dict) -> list[str]:
    """Why the tracked receipt and companion are not this measurement's, if they are not."""
    path = root / RECEIPT
    if not path.is_file():
        return [f"{RECEIPT} is absent"]
    held = json.loads(path.read_text())
    out = []
    if comparable(held) != comparable(receipt):
        out.append(f"{RECEIPT} does not match a fresh measurement")
    written, rewritten = instant(held.get("written")), instant(held.get("rewritten"))
    now = datetime.now(timezone.utc)
    if written is None or rewritten is None:
        out.append("written or rewritten is not an instant with its UTC offset")
    elif not written <= rewritten <= now:
        out.append("written, rewritten and now are out of order")
    elif written != rewritten:
        committed = committed_receipt(root)
        if not (committed and committed.get("written") == held.get("written")
                and same_verdict(committed, held)):
            out.append("written is earlier than rewritten, and no committed receipt with the same "
                       "verdict holds that instant")
    comp = root / COMPANION
    if not comp.is_file() or digest(json.loads(comp.read_text())) != held.get("companion", {}).get("sha256"):
        out.append(f"{COMPANION} is absent or not the one the receipt pins")
    return out


def write(root: Path, receipt: dict, companion: dict) -> dict:
    path = root / RECEIPT
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    held = json.loads(path.read_text()) if path.is_file() else None
    receipt["written"] = held["written"] if held and same_verdict(held, receipt) \
        and instant(held.get("written")) else now
    receipt["rewritten"] = now
    (root / COMPANION).parent.mkdir(parents=True, exist_ok=True)
    (root / COMPANION).write_text(json.dumps(companion, indent=1, sort_keys=True))
    if held is None or comparable(held) != comparable(receipt) or held.get("written") != receipt["written"]:
        path.write_text(json.dumps(receipt, indent=1) + "\n")
    else:
        receipt = held
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--check", action="store_true",
                        help="compute again and fail if the tracked receipt or companion differs")
    parser.add_argument("--companion", action="store_true",
                        help="write only the companion, without replay run records, and only if "
                             "it is the one the tracked receipt pins")
    args = parser.parse_args()
    if args.companion:
        _, companion = measure(ROOT, with_replays=False)
        want = json.loads((ROOT / RECEIPT).read_text())["companion"]["sha256"]
        if digest(companion) != want:
            print(f"the companion computed here is {digest(companion)[:16]}, not the "
                  f"{want[:16]} {RECEIPT} pins; nothing written")
            return 1
        (ROOT / COMPANION).parent.mkdir(parents=True, exist_ok=True)
        (ROOT / COMPANION).write_text(json.dumps(companion, indent=1, sort_keys=True))
        print(f"wrote {COMPANION}: the companion {RECEIPT} pins")
        return 0
    receipt, companion = measure(ROOT)
    if args.check:
        problems = check(ROOT, receipt, companion)
        for p in problems:
            print(p)
        if not problems:
            print(f"{RECEIPT} matches; verdict {receipt['verdict']}")
        return 1 if problems else 0
    receipt = write(ROOT, receipt, companion)
    r = receipt["readsets"]
    print(f"wrote {RECEIPT}: verdict {receipt['verdict']}, conclusive {receipt['conclusive']}; "
          f"data sections {receipt['data_sections']['differing_bytes']} bytes differ; jump tables "
          f"{r['jump_tables']['differing_bytes']}/{r['jump_tables']['bytes']}; emulated sites "
          f"{r['emulated_sites']['differing_bytes']}/{r['emulated_sites']['bytes']}; header fields "
          f"differing: {', '.join(receipt['header_fields']['differing']) or 'none'}")
    for name, rep in receipt["replays"].items():
        print(f"  {name}: differing read witnessed {rep['differing_read_witnessed']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
