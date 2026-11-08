"""tools/method_check.py holds the method pages to their rules.

Each case builds a small tree under pytest's tmp_path that passes, breaks one rule, and
expects that rule's failure, so every check is seen to fire.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools"))
import method_check as M  # noqa: E402


def tree(tmp_path: Path) -> Path:
    method = tmp_path / "docs" / "method"
    method.mkdir(parents=True)
    (method / "README.md").write_text(
        "# Method\n\n## Reading order\n\n1. [principles.md](principles.md): rules.\n\n## Next\n")
    (method / "principles.md").write_text("# Principles\n\nA claim names its evidence.\n")
    skill = tmp_path / M.SKILL
    skill.parent.mkdir(parents=True)
    skill.write_text("# Recompile a game\n\nRead `docs/method/README.md`.\n")
    (tmp_path / "AGENTS.md").write_text(
        "Read `docs/method/README.md`. Agents load `.agents/skills/recompile-a-game/SKILL.md`.\n")
    return tmp_path


def test_a_complete_tree_passes(tmp_path):
    assert M.run_all(tree(tmp_path)) == []


def test_a_page_missing_from_the_reading_order_fails(tmp_path):
    root = tree(tmp_path)
    (root / "docs/method/gaps.md").write_text("# Gaps\n")
    assert M.run_all(root) == ["docs/method/README.md: reading order omits gaps.md"]


def test_a_general_page_naming_a_game_fails(tmp_path):
    root = tree(tmp_path)
    (root / "docs/method/principles.md").write_text("# Principles\n\nAs Sonic does.\n")
    assert M.run_all(root) == ["docs/method/principles.md:3: names a particular game (Sonic); "
                               "put game facts in opensa2-examples.md"]


def test_a_personal_path_email_or_handle_fails(tmp_path):
    root = tree(tmp_path)
    (root / "docs/method/principles.md").write_text(
        "# P\n\nSee /home/someone/x.\nMail a@b.org.\nAsk @someone.\nRun `tools/public_check.py`.\n")
    found = M.run_all(root)
    assert [f.split(":")[1] for f in found] == ["3", "4", "5"]


def test_a_skill_that_grows_content_fails(tmp_path):
    root = tree(tmp_path)
    (root / M.SKILL).write_text("Read `docs/method/README.md`.\n" + "more\n" * M.SKILL_MAX_LINES)
    assert M.run_all(root) == [f"{M.SKILL}: longer than {M.SKILL_MAX_LINES} lines; the method "
                               "belongs in docs/method, and the skill only routes to it"]


def test_records_are_held_to_the_schema(tmp_path):
    root = tree(tmp_path)
    (root / "tools").mkdir()
    (root / "tools/check.py").write_text("")
    records = root / M.RECORDS
    records.parent.mkdir(parents=True)
    good = {"id": "C1", "class": "reproducibility", "stage": "lift", "symptom": "s",
            "root_cause": "r", "fix_pattern": "f", "fix": "tools/check.py",
            "detector": "tools/check.py"}
    records.write_text(json.dumps({"decisions": [], "cases": [good]}))
    assert M.run_all(root) == []
    bad = dict(good, id="C2", symptom="seen on 2026-11-08", detector=None)
    records.write_text(json.dumps({"decisions": [], "cases": [bad]}))
    found = M.run_all(root)
    assert any("do not match the schema" in f for f in found)  # detector_gap missing
    assert any("ids must run C1" in f for f in found)
    assert any("holds a date" in f for f in found)
