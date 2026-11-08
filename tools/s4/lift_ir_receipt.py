#!/usr/bin/env python3
"""The lifted image's IR, as a tracked receipt a fresh checkout can check its own lift against.

Subtitle: the sha256 of every folded module the link reads (the map's, the arms' and the
lifted extras'), per population and per module, so a clone that rebuilt the image with
`tools/s4/rebuild_image.py` learns whether it got the same translation, and which modules
differ when it did not.

    source tools/env.sh
    .venv/bin/python tools/s4/lift_ir_receipt.py            # check this tree's IR
    .venv/bin/python tools/s4/lift_ir_receipt.py --write    # record it

Why
---
The objects a release links are compiled from these modules, and each build record holds
its module's `ir_sha256`, but the records are generated and stay local, so nothing a clone
has said what the lift should produce. The lift is deterministic, and since the PC24 helpers
are compiled path-independently (`tools/spike/spikelib.py`, `helper_ir`) so is every
module's text, wherever the checkout lives. That makes a recorded hash a fair test.

The receipt holds hashes, never IR: translated game code stays in `private/`. Per module it
keeps the first 16 hex digits, enough to name a module that differs; per population it keeps
the module count and the full sha256 of its sorted `name sha256` lines, which is the check.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "s3" / "whole_image"))
import common as C  # noqa: E402

RECEIPT = C.ROOT / "Documentation" / "receipts" / "lift-ir-v1.json"
POPULATIONS = {
    "map": C.GEN / "s3" / "whole_image" / "fold" / "next",
    "arms": C.GEN / "s3" / "arms" / "fold" / "next",
    "extras": C.GEN / "s4" / "lift" / "fold",
}


def measure() -> dict[str, dict[str, str]]:
    """{population: {module file name: sha256}} for this tree's folded modules."""
    return {pop: {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in sorted(d.glob("sub_*.pc24.ll"))}
            for pop, d in POPULATIONS.items()}


def digest(mods: dict[str, str]) -> str:
    return hashlib.sha256("".join(f"{n} {h}\n" for n, h in sorted(mods.items())).encode()).hexdigest()


def receipt(found: dict[str, dict[str, str]]) -> dict:
    return {
        "schema": "sa2-lift-ir/v1",
        "generated_by": "tools/s4/lift_ir_receipt.py",
        "command": "source tools/env.sh && .venv/bin/python tools/s4/lift_ir_receipt.py --write",
        "populations": {pop: {"folder": str(POPULATIONS[pop].relative_to(C.ROOT)),
                              "modules": len(m), "sha256": digest(m)}
                        for pop, m in found.items()},
        "modules": {pop: {n: h[:16] for n, h in sorted(m.items())} for pop, m in found.items()},
    }


def problems(held: dict, found: dict[str, dict[str, str]]) -> list[str]:
    """Why this tree's IR is not the receipt's, per population; [] when it is."""
    out = []
    for pop, want in held["populations"].items():
        mods = found.get(pop, {})
        if digest(mods) == want["sha256"]:
            continue
        rec = held["modules"][pop]
        missing = sorted(set(rec) - set(mods))
        extra = sorted(set(mods) - set(rec))
        differ = sorted(n for n in set(rec) & set(mods) if mods[n][:16] != rec[n])
        out.append(f"{pop}: {len(mods)} modules, the receipt has {want['modules']}; "
                   f"{len(differ)} differ, {len(missing)} missing, {len(extra)} not recorded"
                   + (f"; first different {differ[0]}" if differ else "")
                   + (f"; first missing {missing[0]}" if missing else "")
                   + (f"; first not recorded {extra[0]}" if extra else ""))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--write", action="store_true", help="record this tree's IR as the receipt")
    args = ap.parse_args()
    found = measure()
    counts = ", ".join(f"{pop} {len(m)}" for pop, m in found.items())
    if args.write:
        RECEIPT.write_text(json.dumps(receipt(found), indent=1, sort_keys=True) + "\n")
        print(f"wrote {RECEIPT.relative_to(C.ROOT)}: {counts}")
        return 0
    bad = problems(json.loads(RECEIPT.read_text()), found)
    for line in bad:
        print(line)
    if not bad:
        print(f"LIFT_IR_OK {counts}: every module matches {RECEIPT.relative_to(C.ROOT)}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
