#!/usr/bin/env python3
"""Check the method pages under docs/method, the agent skill that routes to them, and the
method records.

`tools/public_check.py` calls `run_all`, so the one pre-commit command covers these checks.
Run alone with `python3 tools/method_check.py`.

What it holds the pages to (docs/method/principles.md owns the rules in prose):

* the README's reading order lists every page in docs/method, and nothing else;
* the general pages name no particular game or its tool tree (`CASE_TERMS`), so the
  method stays reusable; game facts belong in the README's examples page;
* the general pages and the records hold nothing shaped like an absolute path, an email
  address, or an account handle (`PERSONAL`); identity terms themselves stay in
  `public_check.py --deny-file`, so there is one list of them, kept outside the repository;
* the skill stays a router: it links the README and stays short;
* AGENTS.md routes to the README and the skill;
* when present, Documentation/method/records.json has exactly `RECORD_FIELDS`, sequential
  ids, no date-shaped values, and evidence, fixes, and detectors that exist in the tree;
* when present, every backticked path in docs/method/opensa2-examples.md exists.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

METHOD = Path("docs/method")
SKILL = Path(".agents/skills/recompile-a-game/SKILL.md")
RECORDS = Path("Documentation/method/records.json")
EXAMPLES = METHOD / "opensa2-examples.md"
NOT_GENERAL = {"README.md", "opensa2-examples.md"}
SKILL_MAX_LINES = 40

CASE_TERMS = re.compile(r"OpenSA2|\bSA2\b|Sonic|sonic2app|\bChao\b|tools/s\d|Steam")
PERSONAL = re.compile(r"(?<![\w.`])(?:/(?:home|Users|root|mnt|tmp)/|~/|[A-Za-z]:\\)"
                      r"|[\w.+-]+@[\w-]+\.[\w.]+|(?<![\w`])@[A-Za-z][\w-]{1,38}\b")
DATE = re.compile(r"\b(19|20)\d\d-\d\d-\d\d\b")
LINK = re.compile(r"\]\(([^)#\s]+)")
RECORD_FIELDS = {
    "decision": frozenset({"id", "title", "situation", "choice", "evidence", "reverse_if"}),
    "case": frozenset({"id", "class", "stage", "symptom", "root_cause", "fix_pattern", "fix",
                       "detector", "detector_gap"}),
}
CASE_CLASSES = frozenset({"instruction-semantics", "control-flow-coverage", "platform-surface",
                          "guest-state", "timing", "reproducibility", "verification", "process"})
SHORT_FIELDS = ("symptom", "root_cause", "fix_pattern")


def check_reading_order(root: Path) -> list[str]:
    readme = root / METHOD / "README.md"
    if not readme.is_file():
        return [f"{METHOD}/README.md is missing"]
    text = readme.read_text()
    order = text.split("## Reading order", 1)[-1].split("\n## ", 1)[0]
    listed = {Path(m).name for m in LINK.findall(order)}
    pages = {p.name for p in (root / METHOD).glob("*.md")} - {"README.md"}
    out = [f"{METHOD}/README.md: reading order omits {name}" for name in sorted(pages - listed)]
    out += [f"{METHOD}/README.md: reading order lists {name}, which is not a page here"
            for name in sorted(listed - pages)]
    return out


def check_general_pages(root: Path) -> list[str]:
    out = []
    for page in sorted((root / METHOD).glob("*.md")):
        if page.name in NOT_GENERAL:
            continue
        for n, line in enumerate(page.read_text().splitlines(), 1):
            if m := CASE_TERMS.search(line):
                out.append(f"{METHOD}/{page.name}:{n}: names a particular game ({m[0]}); "
                           f"put game facts in {EXAMPLES.name}")
    return out


def check_personal(root: Path) -> list[str]:
    out = []
    files = [p for p in sorted((root / METHOD).glob("*.md")) if p.name not in NOT_GENERAL]
    files += [root / SKILL, root / RECORDS]
    for path in files:
        if not path.is_file():
            continue
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if m := PERSONAL.search(line):
                out.append(f"{path.relative_to(root)}:{n}: looks like a personal path, email "
                           f"address, or handle ({m[0]})")
    return out


def check_skill_router(root: Path) -> list[str]:
    path = root / SKILL
    if not path.is_file():
        return [f"{SKILL} is missing"]
    text = path.read_text()
    out = []
    if f"{METHOD}/README.md" not in text:
        out.append(f"{SKILL}: does not route to {METHOD}/README.md")
    if len(text.splitlines()) > SKILL_MAX_LINES:
        out.append(f"{SKILL}: longer than {SKILL_MAX_LINES} lines; the method belongs in "
                   f"{METHOD}, and the skill only routes to it")
    return out


def check_agents_route(root: Path) -> list[str]:
    text = (root / "AGENTS.md").read_text()
    return [f"AGENTS.md: does not mention {want}"
            for want in (f"{METHOD}/README.md", str(SKILL)) if want not in text]


def check_records(root: Path) -> list[str]:
    path = root / RECORDS
    if not path.is_file():
        return []
    doc = json.loads(path.read_text())
    out = []
    for kind, prefix in (("decision", "D"), ("case", "C")):
        for k, rec in enumerate(doc.get(f"{kind}s", []), 1):
            where = f"{RECORDS} {kind} {rec.get('id', k)}"
            fields = set(rec)
            want = RECORD_FIELDS[kind]
            if kind == "case" and rec.get("detector"):
                want = want - {"detector_gap"}  # a gap is stated only when no detector exists
            if fields != want:
                out.append(f"{where}: fields {sorted(fields ^ want)} do not match the schema")
            if rec.get("id") != f"{prefix}{k}":
                out.append(f"{where}: ids must run {prefix}1, {prefix}2, ... in order")
            for key, value in rec.items():
                if isinstance(value, str) and DATE.search(value):
                    out.append(f"{where}: {key} holds a date; git history carries time")
            refs = rec.get("evidence", []) + [rec.get("fix"), rec.get("detector")]
            for ref in filter(None, refs):
                if not (root / ref.split()[0]).exists():
                    out.append(f"{where}: {ref} is not in the tree")
            if kind == "case":
                if rec.get("class") not in CASE_CLASSES:
                    out.append(f"{where}: class must be one of {sorted(CASE_CLASSES)}")
                out += [f"{where}: {f} is longer than 280 characters"
                        for f in SHORT_FIELDS if len(rec.get(f, "")) > 280]
    return out


def check_example_paths(root: Path) -> list[str]:
    path = root / EXAMPLES
    if not path.is_file():
        return []
    out = []
    for n, line in enumerate(path.read_text().splitlines(), 1):
        for ref in re.findall(r"`((?:tools|docs|Documentation|runtime|host|harness)/[^`\s]+)`", line):
            if not (root / ref).exists():
                out.append(f"{EXAMPLES}:{n}: {ref} is not in the tree")
    return out


def run_all(root: Path) -> list[str]:
    if not (root / METHOD).is_dir():
        return []
    return (check_reading_order(root) + check_general_pages(root) + check_personal(root)
            + check_skill_router(root) + check_agents_route(root) + check_records(root)
            + check_example_paths(root))


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    failures = run_all(root)
    for failure in failures:
        print(failure)
    print(f"Method check: {len(failures)} failures.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
