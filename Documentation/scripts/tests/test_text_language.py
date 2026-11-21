"""runtime/sa2_text_language.c: play's text language (SA2_TEXT_LANGUAGE) on the config's Language
reader, 0x00406520.

The wrap is built with the pinned zig, as tools/s4/link_s4.py compiles a runtime source (-O2,
x86_64-linux-musl), and linked with a driver that stands in for the lifted reader (its
`__real_sub_406520` returns in EAX the number the case names: the config's Language as read, 1 when
the file could not be read, or a value no config holds) and the runtime's register accessors. The
driver calls the wrap twice, as WinMain (0x00401C58) and the quit prompt (0x00401636) do, and
prints EAX after each: the number both switch on.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s4"))
sys.path.insert(0, str(ROOT / "tools" / "s3" / "whole_image"))
import link_s4 as LS  # noqa: E402

ZIG = ROOT / "private" / "predecessor-tools" / "zig" / "zig-x86_64-linux-0.15.2" / "zig"
RUNTIME = ROOT / "runtime"
CC = ["-target", "x86_64-linux-musl", "-O2", "-Wall", "-Wextra", "-Werror", "-I", str(RUNTIME)]
VARS = ("SA2_TEXT_LANGUAGE", "SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS")

DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include "cpu_state.h"

static uint32_t eax, read_as;
uint32_t sa2_rt_get_gpr(const void *state, int idx) {
    (void)state;
    return idx == SA2_EAX ? eax : 0xDEADu;
}
void sa2_rt_set_gpr(void *state, int idx, uint32_t v) {
    (void)state;
    if (idx != SA2_EAX) exit(5);
    eax = v;
}
void *__real_sub_406520(void *state, uint32_t pc, void *memory) {
    (void)state; (void)pc;
    eax = read_as;
    return memory;
}
void *__wrap_sub_406520(void *state, uint32_t pc, void *memory);

int main(int argc, char **argv) {
    read_as = argc > 1 ? (uint32_t)strtol(argv[1], NULL, 0) : 0u;
    int token;
    if (__wrap_sub_406520(&token, 0x00401C58u, &token) != &token) return 3;
    printf("%d", (int)eax);
    if (__wrap_sub_406520(&token, 0x00401636u, &token) != &token) return 3;
    printf(" %d\n", (int)eax);
    return 0;
}
"""


@pytest.fixture(scope="module")
def exe(tmp_path_factory) -> Path:
    if not ZIG.is_file():
        pytest.fail(f"the pinned zig is not present: {ZIG}")
    d = tmp_path_factory.mktemp("text_language")
    (d / "driver.c").write_text(DRIVER)
    out = d / "tl"
    env = dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(d / "zc"),
               ZIG_GLOBAL_CACHE_DIR=os.environ.get(
                   "ZIG_GLOBAL_CACHE_DIR", str(ROOT / "private" / "generated" / "zig-cache-tests")))
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"),
                           str(RUNTIME / "sa2_text_language.c"), "-o", str(out)],
                          capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, read: int = 0, **env: str) -> tuple[str, str]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS}
    p = subprocess.run([str(exe), str(read)], capture_output=True, text=True, timeout=30,
                       env=dict(clean, **env))
    assert p.returncode == 0, p.stderr
    return p.stdout.strip(), p.stderr


@pytest.mark.parametrize("read", [0, 1, 5, -858993460])
def test_unset_or_empty_changes_nothing_and_says_nothing(exe, read):
    assert run(exe, read) == (f"{read} {read}", "")
    assert run(exe, read, SA2_TEXT_LANGUAGE="") == (f"{read} {read}", "")


@pytest.mark.parametrize("value, number, name", [
    ("en", 0, "English"), ("de", 1, "German"), ("es", 2, "Spanish"),
    ("fr", 3, "French"), ("it", 4, "Italian"), ("ja", 5, "Japanese"),
])
def test_each_language_returns_its_config_number_and_says_so_once(exe, value, number, name):
    out, err = run(exe, 0, SA2_TEXT_LANGUAGE=value)
    assert out == f"{number} {number}"      # WinMain's call and the quit prompt's
    assert err == f"text language: {name} (SA2_TEXT_LANGUAGE={value}): the config's Language 0 -> {number}\n"


def test_any_case_and_any_config_read(exe):
    assert run(exe, 0, SA2_TEXT_LANGUAGE="JA")[0] == "5 5"
    assert run(exe, 0, SA2_TEXT_LANGUAGE="De")[0] == "1 1"
    # The reader's 1 for an unreadable file, and a missing attribute's uninitialised local.
    assert run(exe, 1, SA2_TEXT_LANGUAGE="ja")[0] == "5 5"
    out, err = run(exe, -858993460, SA2_TEXT_LANGUAGE="fr")
    assert out == "3 3" and "the config's Language -858993460 -> 3" in err


@pytest.mark.parametrize("value", ["jp", "english", "e", "en ", " en", "5", "pt", "ja,en", "off"])
def test_malformed_changes_nothing_with_one_line(exe, value):
    out, err = run(exe, 2, SA2_TEXT_LANGUAGE=value)
    assert out == "2 2"
    assert err == (f"text language: SA2_TEXT_LANGUAGE={value} is not one of en, de, es, fr, it, ja; "
                   "the config's Language 2 is kept\n")


@pytest.mark.parametrize("replay", ["SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"])
def test_ignored_in_a_replay(exe, replay):
    out, err = run(exe, 0, SA2_TEXT_LANGUAGE="ja", **{replay: "/dev/null"})
    assert out == "0 0"
    assert err == ("text language: SA2_TEXT_LANGUAGE=ja ignored in a replay (SA2_REPLAY_TICKS or "
                   "SA2_REPLAY_WORDS is set); the config's Language is left as read\n")
    assert run(exe, 0, **{replay: "/dev/null"}) == ("0 0", "")


def test_every_link_wraps_the_language_reader():
    assert "sa2_text_language.c" in LS.RUNTIME_C and "sub_406520" in LS.PRODUCTION_WRAPS
    # Before ReadConfig, so the HUD sites stay the list's tail (test_widescreen.py).
    w = LS.PRODUCTION_WRAPS
    assert w.index("sub_406520") < w.index("sub_405310")
    assert w.count("sub_406520") == 1
    source = (RUNTIME / "sa2_text_language.c").read_text()
    assert "void *__wrap_sub_406520(void *state, uint32_t pc, void *memory)" in source
    import device_profile as DP
    assert "SA2_TEXT_LANGUAGE" in DP.OWNED
