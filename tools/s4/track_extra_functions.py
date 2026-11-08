#!/usr/bin/env python3
"""The blocks lift_extra.py registered, as a tracked record.

Why this is tracked and the file it comes from is not
-----------------------------------------------------
`private/generated/s4/link/extra_functions.<tag>.json` is **evidence from
running the game**. Each row is an address the whole-image lift produced no
module for, which a driven run then reached, so `lift_extra.py` lifted it as
its own module with a recorded reason. None of it is derivable from the
executable alone: it took a run to find each one.

It was gitignored, which is the same mistake the dispatch exceptions were: on
2026-09-21 a regeneration dropped a reviewed exception, the driven run fell
from 369 WinMain iterations to 1, and every gate passed. That reason had to be
recovered out of a surviving object file's string table.

**This is a projection, not a copy, and the difference is deliberate.** The
generated file carries `lift_seconds`, `compile_seconds` and absolute object
paths -- a stopwatch and a working directory. Tracking those would change the
record's hash on every regeneration for no semantic reason, so the reach
receipt would go stale constantly and the staleness gate would become noise
somebody learns to re-run past. That is exactly the failure mode this record
exists to prevent, so the timings are dropped and the decisions are kept.

What a row means: this address was lifted as its own module, for this reason,
and it did or did not compile and fold. Changing the set changes what the
guest can execute, the same as a dispatch exception.

Restoring the state from the record
-----------------------------------

`--restore` runs the projection backwards: it writes the generated state from
this record, with each row's object path where `lift_extra.py` puts it, so a
fresh checkout (or one whose `private/` was lost) can rebuild every extra with
`tools/s4/rebuild_image.py` instead of finding each block again by running the
game. The timings stay absent; nothing reads them. It refuses to overwrite a
state that exists, and checks that projecting what it wrote gives this record
back, field for field.

Usage
-----

    .venv/bin/python tools/s4/track_extra_functions.py [--tag next+allarms] [--write]
    .venv/bin/python tools/s4/track_extra_functions.py [--tag next+allarms] --restore
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "s3" / "whole_image"))
import common as C  # noqa: E402

OUT = C.ROOT / "Documentation" / "lifted-extras.json"
BACKFILL = C.ROOT / "Documentation" / "lifted-extras-backfill.json"

# Decision-bearing only. `lift_seconds`, `compile_seconds`, `object`,
# `object_bytes` and `error_head` are deliberately absent: the first two are a
# stopwatch, the next two a working directory, and the last is a compiler
# message that moves with the compiler.
KEEP = ("va", "end", "anchor", "bytes", "lifted", "compiled", "folded", "declares_sub",
        "rejected", "owner", "why")


def project(tag: str) -> dict:
    src = C.GEN / "s4" / "link" / f"extra_functions.{tag}.json"
    if not src.is_file():
        raise SystemExit(f"TRACK_EXTRAS_FAILED {src} does not exist; run "
                         "tools/s4/lift_extra.py first")
    doc = json.loads(src.read_text())
    rows = [{k: r[k] for k in KEEP if k in r} for r in doc.get("rows", [])]
    rows.sort(key=lambda r: int(r["va"], 16))
    # Rows written before lift_extra.py required a reason. Each carries where
    # its reason was recovered from, so a backfilled reason is never mistaken
    # for one that was recorded at the time.
    back = {}
    if BACKFILL.is_file():
        for b in json.loads(BACKFILL.read_text()).get("rows", []):
            back[f"0x{int(b['va'], 0):08x}"] = b
    for r in rows:
        if str(r.get("why", "")).strip():
            continue
        b = back.get(f"0x{int(r['va'], 16):08x}")
        if not b:
            raise SystemExit(
                f"TRACK_EXTRAS_FAILED {r['va']} has no reason and no entry in "
                f"{BACKFILL.name}. A block lifted for no recorded reason is the "
                "thing rule 14 forbids; lift_extra.py now refuses to create one, "
                "so this is a row from before it did and its reason has to be "
                "recovered from run evidence rather than invented.")
        r["why"] = b["why"]
        r["why_recovered"] = b.get("recovered", "")
    return {
        "schema": "sa2-recomp-lifted-extras/v1",
        "about": [
            "Blocks tools/s4/lift_extra.py registered as their own modules,",
            "because the whole-image lift produced no module for them. Each",
            "row's `why` says how it came in: an address a driven run reached",
            "(tools/s4/reach_extend.py, a probe's census, or a hand --entry",
            "naming the run); a dispatch target on a dispatch-surface list",
            "(--from-list); a code pointer in a run of them in .rdata (the",
            "sweep); or an arm of a switch anywhere in the lifted image",
            "(--seed-arms, anchored as `anchor` records). The first is evidence",
            "from running the game; the rest are read from the executable.",
            "",
            "A projection of the generated state, without its timings and",
            "absolute paths, so the record changes only when a decision does.",
            "Written by tools/s4/track_extra_functions.py, checked by",
            "Documentation/scripts/check_lifted_extras.py.",
        ],
        "tag": tag,
        "lift_tag": doc.get("lift_tag", ""),
        "arms_tag": doc.get("arms_tag", ""),
        "counts": {
            "rows": len(rows),
            "lifted": sum(1 for r in rows if r.get("lifted")),
            "compiled": sum(1 for r in rows if r.get("compiled")),
            "guest_bytes": sum(int(r.get("bytes", 0)) for r in rows),
        },
        "rows": rows,
    }


def restore(tag: str) -> Path:
    """Write the generated state from the tracked record (module docstring)."""
    dst = C.GEN / "s4" / "link" / f"extra_functions.{tag}.json"
    if dst.exists():
        raise SystemExit(f"TRACK_EXTRAS_FAILED {dst} exists; --restore only writes a missing state")
    record = json.loads(OUT.read_text())
    objdir = C.GEN / "s4" / "lift" / "obj"
    rows = []
    for r in record["rows"]:
        row = {k: r[k] for k in KEEP if k in r}
        if "why_recovered" in r:
            # A backfilled reason was empty in the state; project() fills it again.
            row["why"] = ""
        row["object"] = str(objdir / f"ir_{int(r['va'], 16):08X}.o")
        rows.append(row)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps({"tag": tag, "lift_tag": record.get("lift_tag", ""),
                               "arms_tag": record.get("arms_tag", ""), "rows": rows},
                              indent=1) + "\n")
    if project(tag) != record:
        dst.unlink()
        raise SystemExit("TRACK_EXTRAS_FAILED the restored state does not project back to "
                         f"{OUT.relative_to(C.ROOT)}; nothing was written")
    return dst


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="next+allarms")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--restore", action="store_true",
                    help="write the generated state from the tracked record")
    a = ap.parse_args()
    if a.restore:
        dst = restore(a.tag)
        print(f"TRACK_EXTRAS restored {dst.relative_to(C.ROOT)} from {OUT.relative_to(C.ROOT)}")
        return 0
    doc = project(a.tag)
    c = doc["counts"]
    print(f"TRACK_EXTRAS tag={a.tag} rows={c['rows']} lifted={c['lifted']} "
          f"compiled={c['compiled']} guest_bytes={c['guest_bytes']}")
    if a.write:
        OUT.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
        print(f"TRACK_EXTRAS wrote {OUT.relative_to(C.ROOT)} "
              f"({OUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
