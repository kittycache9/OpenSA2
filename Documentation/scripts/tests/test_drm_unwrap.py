#!/usr/bin/env python3
"""Behaviour tests for tools/s7/drm_unwrap.py, the DRM-unwrap measurement.

Every image here is a small synthetic PE written into a scratch tree, so no shipped byte is
read or held. The censuses are passed in, as the tool's `measure` allows, and the scratch tree
holds its own pins, replay registry, tick-replay receipt and run records. The last test holds the
tracked receipt to a fresh measurement of the real files, where they exist.
"""

from __future__ import annotations

import hashlib
import json
import re
import struct
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s7"))

import drm_unwrap as D  # noqa: E402

BASE = 0x400000
TEXT_VA, TEXT_LEN = 0x1000, 0x400
DATA_VA, DATA_LEN = 0x2000, 0x200
T = BASE + TEXT_VA


def pe(text: bytes, data: bytes, names=(b".text", b".data"), extra: bytes | None = None,
       data_chars: int = 0xC0000040, text_rawptr: int | None = None) -> bytes:
    """A PE32 with a header page, .text, .data and optionally a third section."""
    secs = [(names[0], TEXT_VA, text, 0x60000020), (names[1], DATA_VA, data, data_chars)]
    if extra is not None:
        secs.append((b".bind", 0x3000, extra, 0x60000020))
    head = bytearray(0x400)
    head[0:2] = b"MZ"
    struct.pack_into("<I", head, 0x3C, 0x80)
    head[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HH", head, 0x84, 0x14C, len(secs))
    struct.pack_into("<H", head, 0x94, 224)
    opt = 0x98
    struct.pack_into("<H", head, opt, 0x10B)
    struct.pack_into("<I", head, opt + 28, BASE)
    struct.pack_into("<II", head, opt + 56, 0x3000 + (0x1000 if extra is not None else 0), 0x400)
    struct.pack_into("<II", head, opt + 96 + 8, DATA_VA, 0x40)
    raw = 0x400
    body = bytearray()
    for i, (name, va, blob, chars) in enumerate(secs):
        s = opt + 224 + 40 * i
        head[s:s + 8] = name.ljust(8, b"\0")
        ptr = raw + len(body)
        if i == 0 and text_rawptr is not None:
            ptr = text_rawptr
        struct.pack_into("<IIII", head, s + 8, len(blob), va, len(blob), ptr)
        struct.pack_into("<I", head, s + 36, chars)
        body += blob
    return bytes(head) + bytes(body)


def with_dwords(blob: bytes, at: int, values: list[int]) -> bytes:
    b = bytearray(blob)
    for i, v in enumerate(values):
        struct.pack_into("<I", b, at + 4 * i, v)
    return bytes(b)


TEXT = bytes((i * 7) & 0xFF for i in range(TEXT_LEN))
DATA = bytes((i * 3) & 0xFF for i in range(DATA_LEN))
ARM1, ARM2 = T + 0x300, T + 0x320
TEXT_T = with_dwords(TEXT, 0x100, [ARM1, ARM2])            # a 2-entry table at T+0x100
TABLE = {"dispatch": T + 0x10, "table": T + 0x100, "index_table": None, "entries": 2, "cases": 2,
         "arms": [ARM1, ARM2], "bound_from": "check", "problem": None}
RELOCATED = {T + 0x100, T + 0x104}
SITES = [{"ip": T + 0x200, "next_ip": T + 0x204, "kind": "cvtps2pi"}]


def tree(tmp: Path, shipped: bytes, unpacked: bytes, by_kind: dict | None = None,
         pin_shipped: str | None = None, lifted: dict | None = None) -> Path:
    (tmp / "private/exe").mkdir(parents=True, exist_ok=True)
    (tmp / D.SHIPPED).write_bytes(shipped)
    (tmp / D.UNPACKED).write_bytes(unpacked)
    (tmp / "tools").mkdir(exist_ok=True)
    (tmp / D.PINS).write_text(json.dumps({"executables": {
        "sonic2app.exe": {"sha256": pin_shipped or hashlib.sha256(shipped).hexdigest()},
        "sonic2app-unpacked.exe": {"sha256": hashlib.sha256(unpacked).hexdigest()}}}))
    (tmp / "Documentation/receipts").mkdir(parents=True, exist_ok=True)
    (tmp / D.REPLAYS).write_text(json.dumps({"replays": [{"name": "boot"}]}))
    rec = "private/generated/s4/run/replay-boot.ticks.json"
    (tmp / rec).parent.mkdir(parents=True, exist_ok=True)
    (tmp / rec).write_text(json.dumps({"emulated": {"by_kind": by_kind or {"cvtps2pi": 0}}}))
    (tmp / D.TICK_RECEIPT).write_text(json.dumps({"replays": [
        {"name": "boot", "lifted": lifted if lifted is not None else {"run_json": rec}}]}))
    return tmp


def run(tmp: Path, shipped: bytes, unpacked: bytes, tables=None, sites=None, relocated=None,
        **kw) -> dict:
    receipt, _companion = D.measure(
        tree(tmp, shipped, unpacked, **kw),
        tables=[dict(t) for t in (tables if tables is not None else [TABLE])],
        relocated=RELOCATED if relocated is None else relocated,
        sites=list(SITES if sites is None else sites), tool_root=ROOT)
    return receipt


def flip(blob: bytes, at: int) -> bytes:
    b = bytearray(blob)
    b[at] ^= 0xFF
    return bytes(b)


# ------------------------------------------------------------------ the verdict


def test_identical_files_are_no_match_while_paths_are_unmeasured(tmp_path):
    r = run(tmp_path, pe(TEXT_T, DATA), pe(TEXT_T, DATA))
    assert r["verdict"] == "no differing byte measured"
    assert r["conclusive"] is False and r["bytes_match"] is False


def test_a_match_is_conclusive_only_with_nothing_left_unmeasured(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "NOT_MEASURED", [])
    r = run(tmp_path, pe(TEXT_T, DATA), pe(TEXT_T, DATA))
    assert r["conclusive"] is True and r["bytes_match"] is True


def test_one_differing_jump_table_byte_differs(tmp_path):
    r = run(tmp_path, pe(flip(TEXT_T, 0x105), DATA), pe(TEXT_T, DATA))
    assert r["verdict"] == "differs" and r["bytes_match"] is False and r["conclusive"] is True
    assert r["readsets"]["jump_tables"]["differing_bytes"] == 1
    assert r["differs_in"] == ["exact jump tables"]


def test_a_difference_only_in_an_estimated_extent_is_not_conclusive(tmp_path):
    table = dict(TABLE, bound_from="relocations")
    r = run(tmp_path, pe(flip(TEXT_T, 0x105), DATA), pe(TEXT_T, DATA), tables=[table])
    assert r["verdict"] == "differs" and r["conclusive"] is False
    assert r["differs_in"] == ["estimated jump-table extents"]


def test_one_differing_emulated_byte_differs(tmp_path):
    r = run(tmp_path, pe(flip(TEXT_T, 0x203), DATA), pe(TEXT_T, DATA))
    assert r["verdict"] == "differs"
    assert r["readsets"]["emulated_sites"]["by_kind"]["cvtps2pi"] == {"sites": 1,
                                                                       "differing_sites": 1}


def test_one_differing_data_byte_differs(tmp_path):
    r = run(tmp_path, pe(TEXT_T, flip(DATA, 0x77)), pe(TEXT_T, DATA))
    assert r["verdict"] == "differs" and r["bytes_match"] is False
    assert r["data_sections"]["differing_bytes"] == 1


def test_a_differing_code_byte_nobody_reads_does_not_differ(tmp_path):
    r = run(tmp_path, pe(flip(TEXT_T, 0x380), DATA), pe(TEXT_T, DATA))
    assert r["verdict"] == "no differing byte measured"
    text = next(g for g in r["regions"] if ".text" in g["shipped"])
    assert text["kind"] == "executable" and text["differing"] == 1


def test_sections_are_compared_by_address_not_name(tmp_path):
    r = run(tmp_path, pe(TEXT_T, DATA, names=(b".text", b".dataa")), pe(TEXT_T, DATA))
    assert r["verdict"] == "no differing byte measured"
    data = next(g for g in r["regions"] if ".data" in g["unpacked"])
    assert data["shipped"] == [".dataa"] and data["differing"] == 0


def test_an_added_section_differs_in_the_header_fields(tmp_path):
    r = run(tmp_path, pe(TEXT_T, DATA, extra=b"\x90" * 0x100), pe(TEXT_T, DATA))
    assert r["verdict"] == "differs"
    assert {"sections_count", "size_of_image"} <= set(r["header_fields"]["differing"])


def test_a_section_entry_that_differs_alone_differs(tmp_path):
    r = run(tmp_path, pe(TEXT_T, DATA, data_chars=0x40000040), pe(TEXT_T, DATA))
    assert r["verdict"] == "differs"
    assert r["header_fields"]["differing"] == [f"section {BASE + DATA_VA:#010x}"]


def test_a_jump_table_in_data_is_counted(tmp_path):
    data_t = with_dwords(DATA, 0x40, [ARM1])
    table = dict(TABLE, table=BASE + DATA_VA + 0x40, entries=1, cases=1, arms=[ARM1])
    r = run(tmp_path, pe(TEXT_T, flip(data_t, 0x41)), pe(TEXT_T, data_t), tables=[table],
            relocated={BASE + DATA_VA + 0x40})
    assert r["verdict"] == "differs"
    jt = r["readsets"]["jump_tables"]
    assert jt["dispatches_outside_executable"] == 1
    assert jt["bytes"] == 4 and jt["differing_bytes"] == 1
    assert r["readsets"]["union"]["differing_bytes"] == 1


def test_an_import_directory_that_differs_alone_differs(tmp_path):
    shipped = bytearray(pe(TEXT_T, DATA))
    struct.pack_into("<I", shipped, 0x98 + 96 + 8, DATA_VA + 0x10)
    r = run(tmp_path, bytes(shipped), pe(TEXT_T, DATA))
    assert r["verdict"] == "differs" and "import_directory" in r["header_fields"]["differing"]


def test_a_section_virtual_size_that_differs_alone_differs(tmp_path):
    shipped = bytearray(pe(TEXT_T, DATA))
    struct.pack_into("<I", shipped, 0x98 + 224 + 40 + 8, DATA_LEN - 0x10)
    r = run(tmp_path, bytes(shipped), pe(TEXT_T, DATA))
    assert r["verdict"] == "differs"
    assert f"section {BASE + DATA_VA:#010x}" in r["header_fields"]["differing"]


# ------------------------------------------------------------------ the mapping


def test_mapping_stops_at_the_virtual_size():
    blob = pe(TEXT, DATA)
    lay = D.pe_layout(blob)
    lay["sections"][0]["virtual_size"] = 0x10
    view = D.mapped(blob, lay, BASE, BASE + 0x3000)
    assert view[TEXT_VA:TEXT_VA + 0x10] == TEXT[:0x10]
    assert view[TEXT_VA + 0x10:TEXT_VA + 0x20] == bytes(0x10)


def test_a_raw_pointer_past_the_end_maps_zeros():
    blob = pe(TEXT, DATA, text_rawptr=0x100000)
    lay = D.pe_layout(blob)
    view = D.mapped(blob, lay, BASE, BASE + 0x3000)
    assert view[TEXT_VA:TEXT_VA + TEXT_LEN] == bytes(TEXT_LEN)


def test_the_header_page_is_mapped():
    blob = pe(TEXT, DATA)
    view = D.mapped(blob, D.pe_layout(blob), BASE, BASE + 0x3000)
    assert view[:0x400] == blob[:0x400]


@pytest.mark.parametrize("field,value,why", [
    ("size_of_image", D.GUEST_TSTACK_BASE, "image end past the thread-stack arena"),
    ("image_base", 0x10000000, "image base"),
    ("sections_count", 0, "section count"),
    ("sections_count", D.GUEST_MAX_SECTIONS + 1, "section count"),
    ("optional_header_size", D.GUEST_MIN_OPT - 1, "optional header size"),
    ("magic", 0x20B, "not PE32"),
])
def test_each_loader_refusal_is_recorded(field, value, why):
    lay = D.pe_layout(pe(TEXT, DATA))
    assert D.runtime_maps(lay) == []
    lay[field] = value
    assert why in D.runtime_maps(lay)


def test_a_refused_file_makes_a_match_inconclusive(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "NOT_MEASURED", [])
    monkeypatch.setattr(D, "GUEST_TSTACK_BASE", BASE + 0x1000)
    monkeypatch.setattr(D, "guest_constants", lambda root: [])
    r = run(tmp_path, pe(TEXT_T, DATA), pe(TEXT_T, DATA))
    assert r["verdict"] == "no differing byte measured" and r["conclusive"] is False


def test_the_mirrors_match_the_runtimes_constants():
    assert D.guest_constants(ROOT) == []


def test_overlapping_ranges_count_each_byte_once():
    a, b = bytes(16), flip(bytes(16), 3)
    assert D.differing(a, b, 0, [(0, 8), (2, 6)]) == (8, 1)


# ------------------------------------------------------------------ jump-table extents


def table_ranges(t, blob, relocated):
    lay = D.pe_layout(blob)
    view = D.mapped(blob, lay, BASE, BASE + 0x3000)

    def dword(x):
        return struct.unpack_from("<I", view, x - BASE)[0]

    def byte(x):
        return view[x - BASE]
    starts = sorted({t["table"]} | ({t["index_table"]} if t["index_table"] else set()))
    return D.table_ranges(t, dword, relocated, starts, byte)


def test_a_bounded_table_is_exact():
    rs, exact, missing = table_ranges(TABLE, pe(TEXT_T, DATA), RELOCATED)
    assert rs == [(T + 0x100, T + 0x108)] and exact and missing == 0


def test_an_offset_key_table_takes_the_dwords_that_hold_its_arms():
    # the C runtime's memcpy tables: the key starts at 1, so the entries begin a dword on
    text = with_dwords(TEXT, 0x104, [ARM1, ARM2])
    t = dict(TABLE, entries=0, cases=0, bound_from="analyzer")
    rs, exact, missing = table_ranges(t, pe(text, DATA), {T + 0x104, T + 0x108})
    assert (T + 0x108, T + 0x10C) in rs and not exact and missing == 0


def test_an_offset_key_table_below_its_displacement_takes_the_lower_dwords():
    # the negative key (sub ecx,4; jb): the entries sit just below the displacement
    text = with_dwords(TEXT, 0xF0, [ARM1, ARM2])
    t = dict(TABLE, entries=0, cases=0, bound_from="analyzer")
    rs, exact, missing = table_ranges(t, pe(text, DATA), {T + 0xF0, T + 0xF4})
    assert (T + 0xF0, T + 0xF4) in rs and (T + 0xF4, T + 0xF8) in rs
    assert not exact and missing == 0


def test_a_bounded_table_with_an_index_table_takes_its_cases():
    t = dict(TABLE, index_table=T + 0x110, cases=6)
    rs, exact, _missing = table_ranges(t, pe(TEXT_T, DATA), RELOCATED)
    assert (T + 0x110, T + 0x116) in rs and exact


def test_a_relocations_bound_table_is_not_exact():
    t = dict(TABLE, bound_from="relocations")
    _rs, exact, _missing = table_ranges(t, pe(TEXT_T, DATA), RELOCATED)
    assert not exact


def test_an_unheld_arm_is_refused(tmp_path):
    table = dict(TABLE, arms=[ARM1, ARM2, T + 0x3F0])
    with pytest.raises(SystemExit):
        run(tmp_path, pe(TEXT_T, DATA), pe(TEXT_T, DATA), tables=[table])


def test_an_arm_no_near_dword_holds_is_counted():
    t = dict(TABLE, arms=[ARM1, ARM2, T + 0x3F0])
    _rs, _exact, missing = table_ranges(t, pe(TEXT_T, DATA), RELOCATED)
    assert missing == 1


def test_a_layout_index_table_runs_to_the_padding():
    text = bytearray(TEXT_T)
    text[0x110:0x116] = bytes([0, 1, 1, 0, 1, 0])
    text[0x116] = D.PAD
    t = dict(TABLE, index_table=T + 0x110, cases=0, bound_from="layout")
    rs, exact, _missing = table_ranges(t, pe(bytes(text), DATA), RELOCATED)
    assert (T + 0x110, T + 0x116) in rs and not exact


# ------------------------------------------------------------------ the run records


def test_a_run_of_a_kind_whose_every_site_differs_witnesses_a_differing_read(tmp_path):
    r = run(tmp_path, pe(flip(TEXT_T, 0x201), DATA), pe(TEXT_T, DATA), by_kind={"cvtps2pi": 5})
    run0 = r["replays"]["boot"]["runs"][0]
    assert run0["executed_kinds_every_site_differs"] == ["cvtps2pi"]
    assert r["replays"]["boot"]["differing_read_witnessed"] is True


def test_a_kind_with_one_equal_site_decides_nothing(tmp_path):
    sites = SITES + [{"ip": T + 0x240, "next_ip": T + 0x244, "kind": "cvtps2pi"}]
    r = run(tmp_path, pe(flip(TEXT_T, 0x201), DATA), pe(TEXT_T, DATA), sites=sites,
            by_kind={"cvtps2pi": 5})
    run0 = r["replays"]["boot"]["runs"][0]
    assert run0["executed_kinds_undecided"] == ["cvtps2pi"]
    assert r["replays"]["boot"]["differing_read_witnessed"] is False


def test_an_executed_kind_with_no_census_site_is_refused(tmp_path):
    with pytest.raises(SystemExit):
        run(tmp_path, pe(TEXT_T, DATA), pe(TEXT_T, DATA), by_kind={"fnsave": 1})


def test_only_the_named_lifted_records_are_read(tmp_path):
    rec = "private/generated/s4/run/replay-boot.ticks.json"
    lifted = {"run_json": rec, "native": {"run_json": "private/generated/s4/run/native.ticks.json"}}
    r = run(tmp_path, pe(TEXT_T, DATA), pe(TEXT_T, DATA), lifted=lifted)
    assert [x["run_record"] for x in r["replays"]["boot"]["runs"]] == [rec]


def write_record(root: Path, rel: str, by_kind: dict) -> str:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(json.dumps({"emulated": {"by_kind": by_kind}, "baseline": True}))
    return rel


def test_a_distinct_baseline_record_is_read_too(tmp_path):
    rec = "private/generated/s4/run/replay-boot.ticks.json"
    base = write_record(tmp_path, "private/generated/s4/run/replay-boot.baseline.json",
                        {"cvtps2pi": 0})
    r = run(tmp_path, pe(TEXT_T, DATA), pe(TEXT_T, DATA),
            lifted={"run_json": rec, "baseline": {"run_json": base}})
    assert [x["run_record"] for x in r["replays"]["boot"]["runs"]] == [rec, base]


def test_one_file_under_both_keys_is_one_run(tmp_path):
    rec = "private/generated/s4/run/replay-boot.ticks.json"
    r = run(tmp_path, pe(TEXT_T, DATA), pe(TEXT_T, DATA),
            lifted={"run_json": rec, "baseline": {"run_json": rec}})
    assert len(r["replays"]["boot"]["runs"]) == 1


def test_an_absent_baseline_beside_a_present_record_is_refused(tmp_path):
    rec = "private/generated/s4/run/replay-boot.ticks.json"
    with pytest.raises(SystemExit):
        run(tmp_path, pe(TEXT_T, DATA), pe(TEXT_T, DATA),
            lifted={"run_json": rec, "baseline": {"run_json": "private/generated/s4/run/gone.json"}})


def test_a_record_off_its_pin_is_refused(tmp_path):
    rec = "private/generated/s4/run/replay-boot.ticks.json"
    with pytest.raises(SystemExit):
        run(tmp_path, pe(TEXT_T, DATA), pe(TEXT_T, DATA),
            lifted={"run_json": rec, "run_json_sha256": "0" * 64})


def test_a_replay_with_no_lifted_record_is_refused(tmp_path):
    with pytest.raises(SystemExit):
        run(tmp_path, pe(TEXT_T, DATA), pe(TEXT_T, DATA), lifted={})


def test_an_executable_off_its_pin_is_refused(tmp_path):
    with pytest.raises(SystemExit):
        run(tmp_path, pe(TEXT_T, DATA), pe(TEXT_T, DATA), pin_shipped="0" * 64)


# ------------------------------------------------------------------ the receipt


def measured(tmp_path, shipped=None):
    root = tree(tmp_path, shipped or pe(flip(TEXT_T, 0x105), DATA), pe(TEXT_T, DATA))
    return root, D.measure(root, tables=[dict(TABLE)], relocated=RELOCATED, sites=list(SITES),
                           tool_root=ROOT)


def test_check_passes_on_its_own_write_and_fails_on_a_stale_input(tmp_path):
    root, (receipt, companion) = measured(tmp_path)
    D.write(root, receipt, companion)
    again, comp = D.measure(root, tables=[dict(TABLE)], relocated=RELOCATED, sites=list(SITES),
                            tool_root=ROOT)
    assert D.check(root, again, comp) == []
    (root / D.REPLAYS).write_text(json.dumps({"replays": [{"name": "boot"}], "x": 1}))
    again, comp = D.measure(root, tables=[dict(TABLE)], relocated=RELOCATED, sites=list(SITES),
                            tool_root=ROOT)
    assert D.check(root, again, comp)


def test_check_fails_on_a_forged_instant_or_an_edited_companion(tmp_path):
    root, (receipt, companion) = measured(tmp_path)
    D.write(root, receipt, companion)
    held = json.loads((root / D.RECEIPT).read_text())
    held["written"] = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    (root / D.RECEIPT).write_text(json.dumps(held))
    assert D.check(root, receipt, companion)
    D.write(root, receipt, companion)
    (root / D.COMPANION).write_text("{}")
    assert any("companion" in p or D.COMPANION in p for p in D.check(root, receipt, companion))


def test_a_backdated_written_fails_the_check(tmp_path):
    root, (receipt, companion) = measured(tmp_path)
    D.write(root, receipt, companion)
    held = json.loads((root / D.RECEIPT).read_text())
    held["written"] = "2020-01-01T00:00:00+00:00"
    (root / D.RECEIPT).write_text(json.dumps(held))
    assert D.check(root, receipt, companion)


def test_a_naive_instant_fails_the_check(tmp_path):
    root, (receipt, companion) = measured(tmp_path)
    D.write(root, receipt, companion)
    held = json.loads((root / D.RECEIPT).read_text())
    held["written"] = held["rewritten"] = "2026-10-11T00:00:00"
    (root / D.RECEIPT).write_text(json.dumps(held))
    assert D.check(root, receipt, companion)


def test_written_moves_when_the_companion_changes_under_the_same_verdict(tmp_path):
    root, (receipt, companion) = measured(tmp_path)
    D.write(root, receipt, companion)
    held = json.loads((root / D.RECEIPT).read_text())
    held["written"] = "2026-10-01T00:00:00+00:00"
    (root / D.RECEIPT).write_text(json.dumps(held))
    sites = SITES + [{"ip": T + 0x240, "next_ip": T + 0x244, "kind": "paddusb"}]
    receipt2, companion2 = D.measure(root, tables=[dict(TABLE)], relocated=RELOCATED,
                                     sites=sites, tool_root=ROOT)
    assert receipt2["verdict"] == receipt["verdict"]
    assert D.write(root, receipt2, companion2)["written"] != "2026-10-01T00:00:00+00:00"


def test_written_is_carried_forward_while_the_verdict_holds(tmp_path):
    root, (receipt, companion) = measured(tmp_path)
    first = D.write(root, dict(receipt), companion)["written"]
    held = json.loads((root / D.RECEIPT).read_text())
    held["written"] = "2026-10-01T00:00:00+00:00"
    (root / D.RECEIPT).write_text(json.dumps(held))
    (root / D.REPLAYS).write_text(json.dumps({"replays": [{"name": "boot"}], "x": 1}))
    receipt2, companion2 = D.measure(root, tables=[dict(TABLE)], relocated=RELOCATED,
                                     sites=list(SITES), tool_root=ROOT)
    assert D.write(root, receipt2, companion2)["written"] == "2026-10-01T00:00:00+00:00"
    assert first


def test_written_moves_when_the_verdict_changes(tmp_path):
    root, (receipt, companion) = measured(tmp_path)
    D.write(root, receipt, companion)
    held = json.loads((root / D.RECEIPT).read_text())
    held["written"] = "2026-10-01T00:00:00+00:00"
    (root / D.RECEIPT).write_text(json.dumps(held))
    (root / D.SHIPPED).write_bytes(pe(TEXT_T, DATA))
    pins = json.loads((root / D.PINS).read_text())
    pins["executables"]["sonic2app.exe"]["sha256"] = hashlib.sha256(pe(TEXT_T, DATA)).hexdigest()
    (root / D.PINS).write_text(json.dumps(pins))
    receipt2, companion2 = D.measure(root, tables=[dict(TABLE)], relocated=RELOCATED,
                                     sites=list(SITES), tool_root=ROOT)
    assert D.write(root, receipt2, companion2)["written"] != "2026-10-01T00:00:00+00:00"


HEX = re.compile(r"^[0-9a-f]+$")


def leaves(doc, key=""):
    if isinstance(doc, dict):
        for k, v in doc.items():
            yield from leaves(v, k)
    elif isinstance(doc, list):
        for v in doc:
            yield from leaves(v, key)
    else:
        yield key, doc


def test_the_receipt_carries_no_image_bytes(tmp_path):
    r = run(tmp_path, pe(flip(TEXT_T, 0x105), DATA), pe(TEXT_T, DATA))
    def lists(doc):
        if isinstance(doc, dict):
            for v in doc.values():
                yield from lists(v)
        elif isinstance(doc, list):
            yield doc
            for v in doc:
                yield from lists(v)
    for got in lists(r):
        assert not (len(got) > 1 and all(isinstance(v, int) for v in got)), got
    for key, value in leaves(r):
        if isinstance(value, str) and re.fullmatch(r"(0x)?[0-9a-fA-F]{8,}", value):
            assert len(value) == 64 or re.fullmatch(r"0x[0-9a-f]{8}", value), (key, value)
        if not isinstance(value, str):
            assert value is None or isinstance(value, (int, bool)), (key, value)
            continue
        if HEX.match(value) and len(value) > 16:
            assert len(value) == 64, (key, value)   # a sha256, never a run of image bytes
        assert not re.fullmatch(r"[A-Za-z0-9+/=]{24,}", value) or HEX.match(value), (key, value)


# ------------------------------------------------------------------ the tracked receipt


HAVE_EXES = (ROOT / D.SHIPPED).is_file() and (ROOT / D.UNPACKED).is_file()


@pytest.mark.skipif(not HAVE_EXES or not (ROOT / D.TICK_RECEIPT).is_file(),
                    reason="the executables are the user's own and live under private/, and the "
                           "full measurement reads replay run records a fresh checkout lacks")
def test_the_tracked_receipt_is_a_fresh_measurement():
    receipt, companion = D.measure(ROOT)
    assert D.check(ROOT, receipt, companion) == []
    assert receipt["verdict"] == "differs" and receipt["conclusive"] is True


@pytest.mark.skipif(not HAVE_EXES or not (ROOT / "private/generated/s3/arms/arms.next.jsonl").is_file(),
                    reason="needs the user's executables and a translated image "
                           "(tools/s4/rebuild_image.py)")
def test_the_companion_needs_no_replay_run_records(monkeypatch):
    def no_runs(root):
        raise AssertionError("the companion read a replay run record")
    monkeypatch.setattr(D, "replay_runs", no_runs)
    _, companion = D.measure(ROOT, with_replays=False)
    pinned = json.loads((ROOT / D.RECEIPT).read_text())["companion"]["sha256"]
    assert D.digest(companion) == pinned
