"""Which relocated code addresses `tools/s4/address_taken.py` admits as functions to lift.

The Metal Harbor stage walk stopped once per run on a callback the function map
never listed, and a base relocation named 30 of its first 32. The tool admits a
relocated target when its address is *taken* (an instruction's imm32, or a
pointer in a data section), never *read* (a disp32), something ends just before
it, and its code decodes. These tests hold each verdict on synthetic bytes, the
fixed point -- a callback found only because another admitted callback stores
it -- and the field classing the rule-5 review showed the first version got
wrong: a jump table decoded as code, a straddled field, a target both taken and
read.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s4"))
import address_taken as AT  # noqa: E402

BASE = 0x00400000
CODE = [(0x401000, 0x1000)]       # the code sections: .text alone
DATA = [(0x402000, 0x100)]
F = 0x401000                      # the one map function
G = 0x401800                      # a lifted extra's range [G, G + 0x40)


def le(v: int) -> bytes:
    return v.to_bytes(4, "little")


def build() -> tuple[bytes, list[int]]:
    img = bytearray(b"\xcc" * 0x3000)

    def put(va: int, raw: bytes) -> None:
        img[va - BASE:va - BASE + len(raw)] = raw

    put(F, b"\x68" + le(0x401100)                    # push offset 0x401100      taken
        + b"\xa1" + le(0x401200)                     # mov eax,[0x401200]        read
        + b"\xc7\x05" + le(0x402080) + le(0x401900)  # mov [0x402080], offset 0x401900
        + b"\x68" + le(0x401a00)                     # push offset 0x401a00      taken...
        + b"\xa1" + le(0x401a00)                     # mov eax,[0x401a00]        ...and read
        # imul eax,[ecx+disp32],imm8 with disp32 bytes 10 00 1b 40 and imm8 00:
        # the dword at end-4 straddles them and reads 0x00401b00, a code address.
        + b"\x6b\x81" + bytes([0x10, 0x00, 0x1b, 0x40]) + b"\x00"
        + b"\xc3")
    put(0x401100, b"\xc3")                   # after int3: admitted
    put(0x401200, b"\xc3")                   # read as data somewhere: refused
    put(0x4012fb, b"\x90" * 5 + b"\xc3")     # 0x401300 after nops: nothing ends before it
    put(0x401400, b"\x68" + le(0x401500) + b"\xc3")   # admitted, and stores 0x401500
    put(0x401500, b"\xc3")                   # found only by decoding 0x401400
    put(0x401600, b"\xff\xff")               # does not decode
    put(0x4016ff, b"\xc3")                   # 0x401700 follows a ret, not padding
    put(0x401700, b"\xc3")
    # A jump table inside the extra's range: two code pointers, 4 apart, whose
    # bytes a linear decode reads as `add dword ptr [eax],imm32`-like code.
    put(G, b"\xc3" + b"\x81\x00" + le(0x401c00) + le(0x401c10))
    put(0x401c00, b"\xc3")
    put(0x401c10, b"\xc3")
    put(0x401900, b"\xc3")                   # taken through C7 05's imm32
    put(0x401a00, b"\xc3")                   # taken and read: refused
    put(0x401b00, b"\xc3")
    put(0x401d00 - 5, b"\xe9" + le(0))       # 0x401d00 follows a jmp rel32
    put(0x401d00, b"\xc3")
    put(0x401e00 - 3, b"\xc2\x04\x00")       # 0x401e00 follows a ret 4
    put(0x401e00, b"\xc3")
    put(0x401f00 - 8, le(0x401c00) + le(0x401c10))   # 0x401f00 follows a jump table
    put(0x401f00, b"\xc3")
    put(0x402000, le(0x401300) + le(0x401400) + le(0x401008) + le(0x401600) + le(0x401700)
        + le(0x401d00) + le(0x401e00) + le(0x401f00))
    relocs = [F + 1, F + 6, F + 12, F + 16, F + 21, F + 26, F + 33,
              0x401401, G + 3, G + 7, 0x401f00 - 8, 0x401f00 - 4,
              0x402000, 0x402004, 0x402008, 0x40200c, 0x402010, 0x402014, 0x402018, 0x40201c]
    return bytes(img), relocs


# The lifted modules' byte ranges: one per 0x100 block after F, as the
# whole-image lift's modules run from one map entry to the next. They bound how
# far an admitted target's code is decoded, as they bound what lift_extra lifts.
MODULES = [(a, a + 0x100) for a in range(0x401100, 0x402000, 0x100)]


def run(relocs_filter=None):
    img, relocs = build()
    if relocs_filter:
        relocs = [r for r in relocs if relocs_filter(r)]
    return AT.analyse(img, relocs, [(F, 0x30)], [(G, G + 0x40)], CODE, DATA, base=BASE,
                      modules=MODULES)


def test_an_immediate_after_padding_is_admitted():
    r = run()
    assert r["verdict"][0x401100] == "admitted"
    assert r["boundary"][0x401100] == "int3 padding"
    assert r["targets"][0x401100] == {"imm": [(F + 1, F)]}   # the instruction's address


def test_an_address_read_as_data_is_refused():
    assert run()["verdict"][0x401200] == "read as data"


def test_a_target_both_taken_and_read_is_refused():
    r = run()
    assert set(r["targets"][0x401a00]) == {"imm", "disp"}
    assert r["verdict"][0x401a00] == "read as data"


def test_both_fields_of_c7_05_are_classed_by_their_own_offsets():
    """`mov dword ptr [disp32], imm32`: the disp at end-8 and the imm at end-4."""
    r = run()
    assert r["targets"][0x401900] == {"imm": [(F + 16, F + 10)]}
    assert r["verdict"][0x401900] == "admitted"


def test_a_straddled_field_is_not_an_immediate():
    """imul eax,[ecx+disp32],imm8: a relocation at end-4 overlaps the disp32 and
    the imm8. The first version called it an immediate (IMMEDIATE8TO32 at
    end-4) and would have admitted 0x401b00."""
    r = run()
    assert r["targets"][0x401b00] == {"other": [(F + 33, F + 30)]}
    assert r["verdict"][0x401b00] == "no address taken"


def test_a_jump_table_decoded_as_code_is_not_an_immediate():
    r = run()
    for v in (0x401c00, 0x401c10):
        assert "imm" not in r["targets"][v], r["targets"][v]
        assert r["verdict"][v] == "no address taken"


def test_nothing_ending_before_it_is_refused():
    assert run()["verdict"][0x401300] == "nothing ends before it"


def test_a_target_inside_a_map_function_is_not_a_target():
    assert 0x401008 not in run()["targets"]


def test_bytes_that_do_not_decode_are_refused():
    assert run()["verdict"][0x401600] == "does not decode to a ret or jmp"


def test_each_kind_of_boundary():
    r = run()
    want = {0x401700: "a ret", 0x401d00: "a jmp", 0x401e00: "a ret n",
            0x401f00: "a jump table"}
    for v, b in want.items():
        assert r["verdict"][v] == "admitted", (hex(v), r["verdict"][v])
        assert r["boundary"][v] == b


def test_a_callback_stored_by_an_admitted_one_is_found_by_the_fixed_point():
    r = run()
    assert r["verdict"][0x401400] == "admitted"
    assert r["verdict"][0x401500] == "admitted"
    assert r["targets"][0x401500] == {"imm": [(0x401401, 0x401400)]}
    assert r["rounds"] >= 2


def test_without_decoding_admitted_code_the_stored_callback_is_not_evidence():
    """The control for the fixed point: 0x401401 sits in code nobody listed, so
    before 0x401400 is admitted and decoded its relocation says nothing."""
    r = run(lambda x: x != 0x402004)
    assert r["verdict"][0x401500] == "no address taken"


def test_a_function_inside_a_map_functions_size_after_padding_is_admitted():
    """The Chao race camera (0x00538110): a map function's size counts its body's
    bytes wherever they are, so start + size can cover the padded function after
    it and end inside an instruction of the one after that. That instruction,
    decoded from the first function, is cut off and invalid; it must not class
    the relocation before the second function's own decode does, and the padded
    function is weighed although it lies inside the first one's size."""
    a, t, b = 0x401000, 0x401010, 0x401020
    img = bytearray(b"\xcc" * 0x3000)
    img[a - BASE] = 0xC3
    img[t - BASE] = 0xC3
    put = b"\xc7\x40\x18" + le(t) + b"\xc3"      # mov dword ptr [eax+18h], offset t
    img[b - BASE:b - BASE + len(put)] = put
    r = AT.analyse(bytes(img), [b + 3], [(a, b + 4 - a), (b, 0x10)], [], CODE, DATA,
                   base=BASE, modules=[(a, t), (b, b + 0x10)])
    assert r["targets"][t] == {"imm": [(b + 3, b)]}
    assert r["verdict"][t] == "admitted"
    assert r["boundary"][t] == "int3 padding"
