"""The play tweaks: runtime/sa2_tweaks.c's reader and report, the list in runtime/sa2_tweaks.h and
the places each tweak is named, and the first tweak, no-event-bars (runtime/tweaks/no-event-bars.c),
on the events' "shown at 4:3?" answer (0x005F8EC0).

The reader and a tweak's file are built with the pinned zig, as tools/s4/link_s4.py compiles a
runtime source (-O2, x86_64-linux-musl), and linked with a driver that stands in for the lifted
answer and the runtime's peek and register accessors. The stand-in `__real_sub_5f8ec0` does what
the lifted body does: EAX = 1 when ECX is in the game's table of 4:3 events, else 0, then the RET
that pops the return address (ESP + 4). Each call puts a caller's return address at guest [ESP]
first, as the lifted CALL does, so a case reads what each caller would see in EAX.

The list's own tests hold every tweak in SA2_TWEAK_LIST to its runtime/tweaks/<name>.c, or, for a
code-patch tweak (SA2_TWEAK_PATCH_LIST), to its spec tools/s8/variants/tweaks/<name>.json, its line
in the Android settings (GameSettings.TWEAKS) and its line in the release's README, and the
reverse; each tweak file's TWEAK_WRAPS line to the wraps the compiled file defines; the variant
tool's fixtures to no tweak's name; and every link tool's lists to the files
(tools/s4/tweak_sources.py), so a tweak added in one place and not the others fails here by name.
A function the runtime wraps (a tweak's included) is in no runner's wrap list and has no `__wrap_`
in any hook file, and a hook a runtime wrap calls (`sa2_harness_*`) is defined weak and called
there, so a tweak that takes over a runner's wrap and moves the runner's rule into such a hook fails
here, not at link time, when one half is taken without the other.

The reader's code-patch side is compiled from a copy of runtime/sa2_tweaks.h with one code-patch
tweak registered (none is yet), linked with and without a stand-in for the file
tools/s8/variants/variants.py generates; test_variants.py links the generated file itself.

The host side (runtime/sa2_tweaks.h, "Add a host tweak"): the host list (SA2_TWEAK_HOST_LIST) is
held with the game's to one name order and one spelling, each host tweak to its own file in its
home and each file in the homes to its tweak, to the Android switches (but a launcher's) and to the
release's README (but an app's). Each Android switch is in exactly one of the Tweaks card's groups
(GameSettings.TWEAK_GROUPS), whose headings are in the card's order. The game's reader and the host's (host/player/tweaks.cpp, built
with the system g++ as host/player/build.sh builds it) are compiled from a copy of the header with
one host tweak of each home registered (none is yet): the game names each as the host's and turns
nothing on; the host's reader turns on its home's alone, names the others' by home, says nothing of
a game tweak's name, and is off, saying why, out of play and in a replay. Every build of the player
and the launcher compiles the reader and the homes' files.
"""
from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s4"))
sys.path.insert(0, str(ROOT / "tools" / "s3" / "whole_image"))
sys.path.insert(0, str(ROOT / "tools" / "spike"))
sys.path.insert(0, str(ROOT / "tools" / "s7"))
import link_s4 as LS  # noqa: E402
import tweak_sources as TS  # noqa: E402
import win_link as WL  # noqa: E402

sys.path.insert(0, str(ROOT / "tools" / "s8" / "variants"))
import variants as V  # noqa: E402

ZIG = ROOT / "private" / "predecessor-tools" / "zig" / "zig-x86_64-linux-0.15.2" / "zig"
RUNTIME = ROOT / "runtime"
GAME_SETTINGS = ROOT / "host/android/java/com/sa2recomp/app/GameSettings.java"
CC = ["-target", "x86_64-linux-musl", "-O2", "-Wall", "-Wextra", "-Werror", "-I", str(RUNTIME)]
VARS = ("SA2_TWEAKS", "SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS")
EXECUTOR, MOVIE = "0x005FB508", "0x00601948"     # the two callers' return addresses
TABLE = [21, 24, 25, 100, 101, 126, 128, 129, 203, 205, 206, 360, 361, 409]
IN_ENGINE = [e for e in TABLE if e != 409]       # 409, the one movie, is the movie player's

# The guest stack at 0x20000. argv: pairs of (return address, event ID); each prints
# "<event> <EAX> <ESP moved>".
DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cpu_state.h"
#include "guest_memory.h"

#define STACK 0x20000u
static uint8_t stack[0x100];
static uint32_t gpr[8];

int sa2_peek(uint32_t a, uint32_t n, void *out) {
    if (a < STACK || a + n > STACK + sizeof stack) return 1;
    memcpy(out, stack + (a - STACK), n);
    return SA2_OK;
}
uint32_t sa2_rt_get_gpr(const void *state, int idx) { (void)state; return gpr[idx]; }
void sa2_rt_set_gpr(void *state, int idx, uint32_t v) { (void)state; gpr[idx] = v; }

static const uint32_t table[14] = {21, 24, 25, 100, 101, 126, 128, 129, 203, 205, 206, 360, 361, 409};
void *__real_sub_5f8ec0(void *state, uint32_t pc, void *memory) {
    (void)state; (void)pc;
    gpr[SA2_EAX] = 0;
    for (int i = 0; i < 14; i++) if (gpr[SA2_ECX] == table[i]) gpr[SA2_EAX] = 1;
    gpr[SA2_ESP] += 4;                         /* the RET */
    return memory;
}
void *__wrap_sub_5f8ec0(void *state, uint32_t pc, void *memory);

int main(int argc, char **argv) {
    for (int i = 1; i + 1 < argc; i += 2) {
        const uint32_t ret = (uint32_t)strtoul(argv[i], NULL, 0), event = (uint32_t)strtoul(argv[i + 1], NULL, 0);
        const uint32_t esp = strcmp(argv[i], "unreadable") == 0 ? 0x90000u : STACK + 0x80u;
        if (esp == STACK + 0x80u) memcpy(stack + 0x80, &ret, 4);
        gpr[SA2_ESP] = esp;
        gpr[SA2_ECX] = event;
        gpr[SA2_EAX] = 0xDEADBEEFu;
        int token;
        if (__wrap_sub_5f8ec0(&token, 0x005F8EC0u, &token) != &token) return 3;
        printf("%u %u %u\n", event, gpr[SA2_EAX], gpr[SA2_ESP] - esp);
    }
    return 0;
}
"""


def zig_env(d: Path) -> dict:
    return dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(d / "zc"),
                ZIG_GLOBAL_CACHE_DIR=os.environ.get(
                    "ZIG_GLOBAL_CACHE_DIR", str(ROOT / "private" / "generated" / "zig-cache-tests")))


@pytest.fixture(scope="module")
def zig() -> Path:
    if not ZIG.is_file():
        pytest.fail(f"the pinned zig is not present: {ZIG}")
    return ZIG


@pytest.fixture(scope="module")
def exe(zig, tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("tweaks")
    (d / "driver.c").write_text(DRIVER)
    out = d / "tw"
    acting = [str(RUNTIME / rel) for rel, owner, _ in TS.shares(RUNTIME) if owner == "tweaks/no-event-bars.c"]
    done = subprocess.run([str(zig), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(RUNTIME / "tweaks" / "no-event-bars.c"), *acting, "-o", str(out)],
                          capture_output=True, text=True, env=zig_env(d))
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, *calls: str, **env: str) -> tuple[list[str], str]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS}
    p = subprocess.run([str(exe), *calls], capture_output=True, text=True, timeout=30,
                       env=dict(clean, **env))
    assert p.returncode == 0, p.stderr
    return p.stdout.splitlines(), p.stderr


def names() -> list[str]:
    return [r["name"] for r in TS.rows(RUNTIME)]


def patch_names() -> list[str]:
    return [r["name"] for r in TS.patches(RUNTIME)]


def plain_names() -> list[str]:
    """The tweaks with a runtime file: every game has them."""
    return [n for n in names() if n not in patch_names()]


def host_names() -> list[str]:
    return [r["name"] for r in TS.host_rows(RUNTIME)]


def the_tweaks(game: list[str], hosts: list[str]) -> str:
    """The end of the game's line when it names a name that is no tweak: the tweaks there are."""
    return f"the tweaks: {', '.join(game)}" + (f"; the host tweaks: {', '.join(hosts)}" if hosts else "")


# ------------------------------------------------------------------ the reader and the report

# The intro (361) and a stage's (21) in-engine events, both framed for 4:3, a Dark story event
# (100), an event outside the table (50), and the one movie in the table (409, at the movie
# player's call).
CALLS = (EXECUTOR, "361", EXECUTOR, "21", EXECUTOR, "100", EXECUTOR, "50", MOVIE, "409",
         EXECUTOR, "361", EXECUTOR, "100")
GAME = ["361 1 4", "21 1 4", "100 1 4", "50 0 4", "409 1 4", "361 1 4", "100 1 4"]


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}])
def test_off_is_the_lifted_answer_and_says_nothing(exe, env):
    assert run(exe, *CALLS, **env) == (GAME, "")


def test_names_are_trimmed_and_repeats_are_one(exe):
    out, err = run(exe, EXECUTOR, "100", SA2_TWEAKS=" no-event-bars ,no-event-bars,")
    assert out == ["100 0 4"]
    assert err.splitlines()[0] == "sa2 tweaks: event-bars off (SA2_TWEAKS= no-event-bars ,no-event-bars,)"


@pytest.mark.parametrize("value, on", [("event-bars", False), ("No-Event-Bars", False),
                                       ("no-event-bars,widescreen-fmv", True)])
def test_a_name_that_is_no_tweak_is_named_and_ignored(exe, value, on):
    out, err = run(exe, EXECUTOR, "100", SA2_TWEAKS=value)
    assert out == (["100 0 4"] if on else ["100 1 4"])
    unknown = value.split(",")[-1]
    assert err.splitlines()[0] == (
        f"sa2 tweaks: {'event-bars off' if on else 'none on'} (SA2_TWEAKS={value}; no tweak is named "
        f"\"{unknown}\", so it is ignored; {the_tweaks(plain_names(), host_names())})")


@pytest.mark.parametrize("replay", ["SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"])
def test_ignored_in_a_replay(exe, replay):
    out, err = run(exe, *CALLS, SA2_TWEAKS="no-event-bars", **{replay: "/dev/null"})
    assert out == GAME
    assert err == ("sa2 tweaks: SA2_TWEAKS=no-event-bars ignored in a replay (SA2_REPLAY_TICKS or "
                   "SA2_REPLAY_WORDS is set)\n")
    assert run(exe, *CALLS, **{replay: "/dev/null"}) == (GAME, "")


# ------------------------------------------------------------------ no-event-bars

def test_no_event_bars_answers_0_to_the_executor_for_every_event(exe):
    """The intro and event 21, framed for 4:3, lose their bars as event 100 does; each is named once."""
    out, err = run(exe, *CALLS, SA2_TWEAKS="no-event-bars")
    assert out == ["361 0 4", "21 0 4", "100 0 4", "50 0 4", "409 1 4", "361 0 4", "100 0 4"]
    assert err.splitlines() == [
        "sa2 tweaks: event-bars off (SA2_TWEAKS=no-event-bars)",
        "sa2 tweaks: event-bars off: event 361's side bars not drawn",
        "sa2 tweaks: event-bars off: event 21's side bars not drawn",
        "sa2 tweaks: event-bars off: event 100's side bars not drawn"]
    assert "keeps" not in err


def test_each_in_engine_event_in_the_table(exe):
    """All 13 in-engine events in the table lose their bars (the answer 1 becomes 0)."""
    assert IN_ENGINE == [21, 24, 25, 100, 101, 126, 128, 129, 203, 205, 206, 360, 361]
    calls = [x for e in IN_ENGINE for x in (EXECUTOR, str(e))]
    out, err = run(exe, *calls, SA2_TWEAKS="no-event-bars")
    assert out == [f"{e} 0 4" for e in IN_ENGINE]
    assert err.splitlines()[1:] == [f"sa2 tweaks: event-bars off: event {e}'s side bars not drawn"
                                    for e in IN_ENGINE]
    assert run(exe, *calls)[0] == [f"{e} 1 4" for e in IN_ENGINE]


def test_the_movie_keeps_its_bars(exe):
    out, err = run(exe, MOVIE, "409", MOVIE, "100", SA2_TWEAKS="no-event-bars")
    assert out == ["409 1 4", "100 1 4"]
    assert err == "sa2 tweaks: event-bars off (SA2_TWEAKS=no-event-bars)\n"


def test_an_unreadable_stack_keeps_the_bars_and_says_so(exe):
    out, err = run(exe, "unreadable", "100", EXECUTOR, "100", SA2_TWEAKS="no-event-bars")
    assert out == ["100 1 4", "100 0 4"]
    assert ("sa2 tweaks: event-bars off: the guest stack at 0x00090000 does not read, so the caller "
            "is unknown and the event keeps its bars") in err.splitlines()


# ------------------------------------------------------------------ the list and its places

def android_tweaks() -> list[str]:
    """GameSettings.TWEAKS's names, in its order."""
    text = GAME_SETTINGS.read_text()
    m = re.search(r"static final Tweak\[\] TWEAKS = \{(.*?)\n    \};", text, re.S)
    assert m, f"{GAME_SETTINGS.name} has no TWEAKS array"
    return re.findall(r'new Tweak\(\s*"([^"]+)"', m.group(1))


def test_the_list_is_in_name_order_and_each_name_is_one_spelling():
    """The game's list and the host's, each in name order, and a name in one of them only."""
    rows, hosts = TS.rows(RUNTIME), TS.host_rows(RUNTIME)
    assert rows and names() == sorted(names()) and len(set(names())) == len(rows)
    assert host_names() == sorted(host_names()) and len(set(host_names())) == len(hosts)
    assert not set(names()) & set(host_names())
    assert [r["name"] for r in TS.every_row(RUNTIME)] == sorted(names() + host_names())
    for r in rows + hosts:
        assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", r["name"]), r
        assert r["bit"] == "SA2_TWEAK_" + r["name"].upper().replace("-", "_"), r
        assert r["report"] and r["summary"], r
    assert all(r["home"] in TS.HOMES for r in hosts)


def test_the_compiled_reader_holds_the_headers_list(exe):
    _, err = run(exe, SA2_TWEAKS="?")
    assert err.rstrip("\n").endswith(f"; {the_tweaks(plain_names(), host_names())})")


def test_every_tweak_has_its_file_or_its_spec_and_each_its_tweak():
    """A tweak has a runtime file, or it is a code-patch tweak and has a spec; never both."""
    specs = sorted((ROOT / TS.SPECS).glob("*.json"))
    assert [Path(s).stem for s in TS.sources(RUNTIME)] == plain_names()
    assert [p.stem for p in specs] == patch_names()
    assert sorted(plain_names() + patch_names()) == names()


def test_each_code_patch_spec_is_its_tweak_and_each_fixture_no_tweak():
    """A spec's name is its file's and its report the list's (variants.py refuses a build
    otherwise); a fixture's name is no tweak's, so no game links two tweaks of one name."""
    rows = {r["name"]: r for r in TS.patches(RUNTIME)}
    for path in sorted((ROOT / TS.SPECS).glob("*.json")):
        spec = V.read_spec(path)
        assert spec.shipped and spec.name == path.stem and spec.report == rows[spec.name]["report"], path
    fixtures = [V.read_spec(p) for p in sorted((ROOT / TS.FIXTURES).glob("*.json"))]
    assert fixtures, "the variant tool has no fixture"
    for f in fixtures:
        assert not f.shipped and f.name not in names() and f.name.startswith("test-"), f.spec
    assert V.registrations(RUNTIME) == (frozenset(plain_names()),
                                        {r["name"]: r["report"] for r in TS.patches(RUNTIME)})


def registered_patch_runtime(d: Path) -> Path:
    """runtime/sa2_tweaks.c and a copy of runtime/sa2_tweaks.h with one more tweak, zz-patch (bit
    after the list's others), registered as a code-patch tweak, in `d`."""
    rt = d / "rt"
    rt.mkdir()
    (rt / "sa2_tweaks.c").write_text((RUNTIME / "sa2_tweaks.c").read_text())
    h = (RUNTIME / "sa2_tweaks.h").read_text()
    end = "    /* the end of the list */"
    at = h.index(end, h.index("#define SA2_TWEAK_LIST(X)"))
    h = h[:at] + '    X(SA2_TWEAK_ZZ_PATCH, "zz-patch", "zz on", "a code-patch tweak") \\\n' + h[at:]
    at = h.index(end, h.index("#define SA2_TWEAK_PATCH_LIST(X)"))
    h = h[:at] + "    X(SA2_TWEAK_ZZ_PATCH) \\\n" + h[at:]
    (rt / "sa2_tweaks.h").write_text(h)
    assert [r["name"] for r in TS.patches(rt)] == patch_names() + ["zz-patch"]
    return rt


# Prints sa2_tweak_on(SA2_TWEAK_ZZ_PATCH) and sa2_tweak_patch_on(0..2).
PATCH_DRIVER = r"""
#include <stdio.h>
#include "sa2_tweaks.h"
int main(void) {
    printf("%d %d %d %d\n", sa2_tweak_on(SA2_TWEAK_ZZ_PATCH), sa2_tweak_patch_on(0), sa2_tweak_patch_on(1),
           sa2_tweak_patch_on(2));
    return 0;
}
"""
# What variants.py generates for a game with zz-patch and a fixture, test-fx (their wraps aside).
PATCH_LINKED = r"""
#include <stddef.h>
#include "sa2_tweaks.h"
static const sa2_tweak_patch g_patches[2] = {{"test-fx", "fixture on"}, {"zz-patch", "zz on"}};
const sa2_tweak_patch *sa2_tweak_patches_linked(unsigned *count) { *count = 2u; return g_patches; }
"""


@pytest.fixture(scope="module")
def patch_exes(zig, tmp_path_factory) -> dict:
    d = tmp_path_factory.mktemp("patch")
    rt = registered_patch_runtime(d)
    (d / "driver.c").write_text(PATCH_DRIVER)
    (d / "linked.c").write_text(PATCH_LINKED)
    out = {}
    for name, extra in (("without", []), ("with", [str(d / "linked.c")])):
        out[name] = d / name
        done = subprocess.run([str(zig), "cc", *CC[:-2], "-I", str(rt), str(d / "driver.c"), str(rt / "sa2_tweaks.c"),
                               *extra, "-o", str(out[name])], capture_output=True, text=True, env=zig_env(d))
        assert done.returncode == 0, done.stderr
    return out


def test_a_code_patch_tweak_the_game_was_built_without_is_named_and_left_off(patch_exes):
    assert run(patch_exes["without"]) == (["0 0 0 0"], "")
    assert run(patch_exes["without"], SA2_TWEAKS="zz-patch") == (["0 0 0 0"], (
        'sa2 tweaks: none on (SA2_TWEAKS=zz-patch; this game was built without the code-patch tweak '
        '"zz-patch", so it is ignored)\n'))
    out, err = run(patch_exes["without"], SA2_TWEAKS="no-event-bars,zz-patch,zz-patch,test-fx")
    assert err == ('sa2 tweaks: event-bars off (SA2_TWEAKS=no-event-bars,zz-patch,zz-patch,test-fx; this game '
                   'was built without the code-patch tweak "zz-patch", so it is ignored; no tweak is named '
                   f'"test-fx", so it is ignored; {the_tweaks(plain_names(), host_names())})\n')


def test_a_linked_code_patch_tweak_keeps_its_list_bit_and_a_fixture_takes_one_after(patch_exes):
    assert run(patch_exes["with"]) == (["0 0 0 0"], "")
    assert run(patch_exes["with"], SA2_TWEAKS="zz-patch") == (["1 0 1 0"], "sa2 tweaks: zz on (SA2_TWEAKS=zz-patch)\n")
    assert run(patch_exes["with"], SA2_TWEAKS="test-fx") == (["0 1 0 0"], "sa2 tweaks: fixture on (SA2_TWEAKS=test-fx)\n")
    # Reported in bit order: the list's tweaks, zz-patch last among them, then the fixture.
    assert run(patch_exes["with"], SA2_TWEAKS="test-fx,zz-patch") == (
        ["1 1 1 0"], "sa2 tweaks: zz on, fixture on (SA2_TWEAKS=test-fx,zz-patch)\n")
    _, err = run(patch_exes["with"], SA2_TWEAKS="?")
    assert err.rstrip("\n").endswith(f"; {the_tweaks(plain_names() + ['zz-patch', 'test-fx'], host_names())})")
    assert run(patch_exes["with"], SA2_TWEAKS="zz-patch,test-fx", SA2_REPLAY_TICKS="/dev/null")[0] == ["0 0 0 0"]


def test_every_tweak_has_its_android_switch_and_every_switch_its_tweak():
    """One switch for each tweak, the game's and the host's, in name order, but a host tweak of the
    desktop launcher's, which does nothing on Android."""
    assert android_tweaks() == [r["name"] for r in TS.every_row(RUNTIME) if r.get("home") != "launcher"]


# The Tweaks card's headings, in its order: the release notes' groups, and Other.
CARD_HEADINGS = ["Gameplay and assists", "Story and presentation", "Controls", "Audio", "Chao", "Fixes", "Other"]


def android_tweak_groups() -> list[tuple[str, list[str]]]:
    """GameSettings.TWEAK_GROUPS: each group's heading and its names, in its order."""
    text = GAME_SETTINGS.read_text()
    m = re.search(r"static final String\[\]\[\] TWEAK_GROUPS = \{(.*?)\n    \};", text, re.S)
    assert m, f"{GAME_SETTINGS.name} has no TWEAK_GROUPS array"
    groups = [re.findall(r'"([^"]+)"', g) for g in re.findall(r"\{(.*?)\}", m.group(1), re.S)]
    return [(g[0], g[1:]) for g in groups]


def test_every_android_tweak_is_in_one_group_of_the_tweaks_card():
    """The card shows TWEAK_GROUPS: the headings in their order, none empty, each group's names one a
    line in the card's order, and each TWEAKS name in exactly
    one group, so a new tweak whose name is in no group (or two) fails here by name."""
    groups = android_tweak_groups()
    assert [heading for heading, _ in groups] == CARD_HEADINGS
    assert all(names for _, names in groups), [heading for heading, names in groups if not names]
    grouped = [n for _, names in groups for n in names]
    twice = sorted({n for n in grouped if grouped.count(n) > 1})
    assert not twice, f"in two groups of TWEAK_GROUPS: {twice}"
    assert not set(android_tweaks()) - set(grouped), \
        f"in no group of TWEAK_GROUPS: {sorted(set(android_tweaks()) - set(grouped))}"
    assert not set(grouped) - set(android_tweaks()), \
        f"in TWEAK_GROUPS but no TWEAKS line: {sorted(set(grouped) - set(android_tweaks()))}"


def test_every_tweak_is_in_the_release_readme():
    """The game's and the host's in name order, but a host tweak of the Android app's."""
    spec = importlib.util.spec_from_file_location("release_build_tweaks_under_test",
                                                  ROOT / "tools" / "s7" / "release_build.py")
    rb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rb)
    readme = rb.player_readme("x86_64-linux", linked=tuple(patch_names()))
    listed = re.findall(r"^    SA2_TWEAKS=(\S+) +(.+)$", readme, re.M)
    assert rb.DESKTOP_HOMES == ("player", "launcher")
    assert listed == [(r["name"], r["summary"]) for r in TS.every_row(RUNTIME) if r.get("home") != "app"]


@pytest.mark.parametrize("source", TS.sources(RUNTIME))
def test_each_tweak_file_defines_the_wraps_its_line_names(zig, tmp_path, source):
    """Compiled alone with -Wall -Wextra -Werror: the `__wrap_` it defines and the `__real_` it
    calls are the TWEAK_WRAPS line's, and it asks sa2_tweak_on whether its tweak is on."""
    obj = tmp_path / "t.o"
    done = subprocess.run([str(zig), "cc", *CC, "-c", str(RUNTIME / source), "-o", str(obj)],
                          capture_output=True, text=True, env=zig_env(tmp_path))
    assert done.returncode == 0, done.stderr
    symbols = subprocess.run(["nm", str(obj)], capture_output=True, text=True, check=True).stdout
    defined = sorted(re.findall(r"^\S+ [Tt] __wrap_(\w+)$", symbols, re.M))
    called = sorted(re.findall(r"^ +U __real_(\w+)$", symbols, re.M))
    declared = sorted(dict(TS.files(RUNTIME))[source])
    assert defined == declared and called == declared
    assert re.search(r"^ +U sa2_tweak_on$", symbols, re.M)


def symbols(zig, tmp_path: Path, source: str) -> str:
    obj = tmp_path / (Path(source).stem + ".o")
    done = subprocess.run([str(zig), "cc", *CC, "-c", str(RUNTIME / source), "-o", str(obj)],
                          capture_output=True, text=True, env=zig_env(tmp_path))
    assert done.returncode == 0, done.stderr
    return subprocess.run(["nm", str(obj)], capture_output=True, text=True, check=True).stdout


@pytest.mark.parametrize("source, owner, sub", TS.shares(RUNTIME))
def test_a_tweak_that_acts_in_another_tweaks_wrap_is_called_from_it(zig, tmp_path, source, owner, sub):
    """A file whose TWEAK_WRAPS line names another file's wrap defines no wrap, and the wrap's file
    calls a function it defines."""
    mine, theirs = symbols(zig, tmp_path, source), symbols(zig, tmp_path, owner)
    assert not re.findall(r"^\S+ [Tt] __wrap_", mine, re.M)
    assert re.search(rf"^\S+ T __wrap_{sub}$", theirs, re.M)
    defined = set(re.findall(r"^\S+ T (\w+)$", mine, re.M))
    assert defined & set(re.findall(r"^ +U (\w+)$", theirs, re.M)), (source, owner)


# ------------------------------------------------------------------ the links

def test_every_link_compiles_the_tweaks_and_wraps_their_functions():
    assert "sa2_tweaks.c" in LS.RUNTIME_C
    tweaks = TS.sources(RUNTIME)
    assert "tweaks/no-event-bars.c" in tweaks
    assert LS.RUNTIME_C[-len(tweaks):] == tweaks
    wraps = TS.wraps(RUNTIME, [])
    assert "sub_5f8ec0" in wraps and LS.PRODUCTION_WRAPS[-len(wraps):] == wraps
    assert len(set(LS.PRODUCTION_WRAPS)) == len(LS.PRODUCTION_WRAPS)
    import device_profile as DP
    assert "SA2_TWEAKS" in DP.PLAY_ENV and "SA2_TWEAKS" in DP.OWNED


def test_the_tools_that_read_link_s4s_literals_add_the_same_tweaks():
    """arm64_link.py reads link_s4.py's literals with ast.literal_eval, build_cost.py with a
    regular expression; each then adds the tweaks as link_s4.py's import does."""
    import arm64_link as AL
    import build_cost as BC
    lists = AL.link_s4_lists(ROOT)
    assert lists["RUNTIME_C"] == LS.RUNTIME_C and lists["PRODUCTION_WRAPS"] == LS.PRODUCTION_WRAPS
    assert BC.S3_RUNTIME_C + BC.S4_ADDED_C == LS.RUNTIME_C
    assert BC.PRODUCTION_WRAPS == LS.PRODUCTION_WRAPS


def test_the_runtime_digests_and_the_release_sources_count_the_tweak_files():
    """A change to a tweak file makes every receipt that digests the runtime stale, and the
    release manifest names each tweak file it was built from."""
    import reach_receipt as RR
    import tick_replay as TR
    spec = importlib.util.spec_from_file_location("release_build_globs_under_test",
                                                  ROOT / "tools" / "s7" / "release_build.py")
    rb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rb)
    for globs in (TR.RUNTIME_GLOBS, RR.RUNTIME_GLOBS, rb.SOURCE_GLOBS):
        assert "runtime/tweaks/*.c" in globs
    assert "tools/s4/tweak_sources.py" in rb.SOURCE_GLOBS
    assert {"host/player/tweaks/*.cpp", "host/player/tweaks/*.h", "host/launch/tweaks/*.cpp",
            "host/launch/tweaks/*.h", "runtime/*.h"} <= set(rb.SOURCE_GLOBS)
    assert {f"runtime/{s}" for s in TS.sources(RUNTIME)} <= set(rb.sources(ROOT))


def tree(tmp_path: Path, files: dict[str, str]) -> Path:
    rt = tmp_path / "runtime"
    (rt / "tweaks").mkdir(parents=True)
    (rt / "sa2_call.c").write_text("")
    for name, text in files.items():
        (rt / name).write_text(text)
    return rt


def test_tweak_files_are_read_in_name_order(tmp_path):
    rt = tree(tmp_path, {"tweaks/b-two.c": "/* b\n * TWEAK_WRAPS: sub_401000, sub_402000\n */\n",
                         "tweaks/a-one.c": "/* a\n *\n * TWEAK_WRAPS: sub_403000\n */\n"})
    assert TS.sources(rt) == ["tweaks/a-one.c", "tweaks/b-two.c"]
    assert TS.wraps(rt, ["sub_427ee0"]) == ["sub_403000", "sub_401000", "sub_402000"]


@pytest.mark.parametrize("files, refusal", [
    ({"tweaks/a.c": "/* a tweak with no line */\n"}, r"tweaks/a\.c: 0 'TWEAK_WRAPS:' lines"),
    ({"tweaks/a.c": "/* TWEAK_WRAPS: sub_401000\n * TWEAK_WRAPS: sub_402000 */\n"},
     r"tweaks/a\.c: 2 'TWEAK_WRAPS:' lines"),
    ({"tweaks/a.c": "/* TWEAK_WRAPS: ReadConfig */\n"}, r"tweaks/a\.c: TWEAK_WRAPS names \['ReadConfig'\]"),
    ({"tweaks/a.c": "/* TWEAK_WRAPS: */\n"}, r"tweaks/a\.c: TWEAK_WRAPS names nothing"),
    ({"tweaks/a.c": "/* TWEAK_WRAPS: sub_401000 */\n", "tweaks/b.c": "/* TWEAK_WRAPS: sub_401000 */\n"},
     r"tweaks/b\.c and tweaks/a\.c both wrap sub_401000"),
    ({"tweaks/sa2_call.c": "/* TWEAK_WRAPS: sub_401000 */\n"}, r"its object would be sa2_call\.o"),
    ({"tweaks/a.c": "/* TWEAK_WRAPS: sub_427ee0 */\n"}, r"tweaks/a\.c wraps \['sub_427ee0'\], which the runtime"),
    ({"tweaks/a.c": "/* TWEAK_WRAPS: none (it acts in b.c's wrap of sub_401000) */\n"},
     r"tweaks/a\.c: TWEAK_WRAPS says it acts in tweaks/b\.c's wrap of sub_401000, but no tweak file wraps it"),
    ({"tweaks/a.c": "/* TWEAK_WRAPS: none (it acts in b.c's wrap of sub_401000) */\n",
      "tweaks/b.c": "/* TWEAK_WRAPS: sub_402000 */\n", "tweaks/c.c": "/* TWEAK_WRAPS: sub_401000 */\n"},
     r"tweaks/a\.c: TWEAK_WRAPS says it acts in tweaks/b\.c's wrap of sub_401000, but tweaks/c\.c wraps it"),
    ({"tweaks/a.c": "/* TWEAK_WRAPS: none */\n"}, r"tweaks/a\.c: TWEAK_WRAPS names \['none'\]"),
])
def test_a_tweak_file_the_links_cannot_take_is_refused_by_name(tmp_path, files, refusal):
    rt = tree(tmp_path, files)
    with pytest.raises(SystemExit, match=refusal):
        TS.wraps(rt, ["sub_427ee0"])


def test_a_tweak_file_may_act_in_another_tweak_files_wrap(tmp_path):
    rt = tree(tmp_path, {"tweaks/a.c": "/*\n * TWEAK_WRAPS: none (it acts in b.c's wrap of sub_401000)\n */\n",
                         "tweaks/b.c": "/* TWEAK_WRAPS: sub_401000 */\n"})
    assert TS.files(rt) == [("tweaks/a.c", []), ("tweaks/b.c", ["sub_401000"])]
    assert TS.shares(rt) == [("tweaks/a.c", "tweaks/b.c", "sub_401000")]
    assert TS.wraps(rt, []) == ["sub_401000"]



def test_no_2p_specials_and_ring_loss_cap_share_one_wrap_of_addrings():
    """Both tweaks act on AddRings (0x0044CE10): no-2p-specials.c wraps it, ring-loss-cap.c acts in
    that wrap, and the links pass -wrap for it once."""
    assert ("tweaks/ring-loss-cap.c", "tweaks/no-2p-specials.c", "sub_44ce10") in TS.shares(RUNTIME)
    assert TS.wraps(RUNTIME, []).count("sub_44ce10") == 1
    assert LS.PRODUCTION_WRAPS.count("sub_44ce10") == 1
    assert dict(TS.files(RUNTIME))["tweaks/no-2p-specials.c"] == ["sub_44ce10", "sub_46aa80"]


def test_force_hard_layout_and_2p_layout_in_1p_share_one_wrap_of_loadstagesetfile():
    """Both tweaks act on LoadStageSETFile (0x00488F60): force-hard-layout.c wraps it,
    2p-layout-in-1p.c acts in that wrap, and the links pass -wrap for it once."""
    assert ("tweaks/2p-layout-in-1p.c", "tweaks/force-hard-layout.c", "sub_488f60") in TS.shares(RUNTIME)
    assert TS.wraps(RUNTIME, []).count("sub_488f60") == 1
    assert LS.PRODUCTION_WRAPS.count("sub_488f60") == 1
    assert dict(TS.files(RUNTIME))["tweaks/2p-layout-in-1p.c"] == []


# ------------------------------------------------------------------ the runners' hooks

WRAP_DEF = re.compile(r"\b__wrap_(sub_[0-9a-f]+)\s*\([^;{]*\)\s*\{")
HOOK_DEF = re.compile(r"\b(sa2_harness_\w+)\s*\([^;{]*\)\s*\{")
COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)


def code(path: Path) -> str:
    return COMMENT.sub(" ", path.read_text())


def runtime_wrap_clashes(root: Path, production: list[str], runners: dict) -> list[str]:
    """What a runner's link would refuse or fail on, or silently lose, given the runtime's wraps:
    a runner wrap list (WL.RUNNERS' shape) naming one; a hook file (harness/, host/) defining
    `__wrap_` for one (a second definition, a duplicate symbol); and a hook file defining an
    `sa2_harness_*` function the runtime does not define weak and call (its rule would not run)."""
    out = []
    for runner, (path, _, name) in sorted(runners.items()):
        w = WL.literal(path, name)
        twice = [x for x in ([w] if isinstance(w, str) else w) if x in production]
        if twice:
            out.append(f"{path.relative_to(root)} {name} (the {runner} runner's) passes -wrap {twice}, "
                       f"which the runtime wraps in every build")
    rt = "\n".join(code(p) for p in sorted((root / "runtime").rglob("*.c")))
    for src in sorted(p for d in ("harness", "host") for p in (root / d).rglob("*.c")):
        text, rel = code(src), src.relative_to(root)
        twice = sorted(set(WRAP_DEF.findall(text)) & set(production))
        if twice:
            out.append(f"{rel} defines __wrap_ for {twice}, which the runtime defines in every build; "
                       f"its rule belongs in a hook the runtime's wrap calls")
        for hook in sorted(set(HOOK_DEF.findall(text))):
            weak = re.search(r"__attribute__\(\(weak\)\)[^;{]*\b" + hook + r"\s*\(", rt)
            if not weak or len(re.findall(r"\b" + hook + r"\s*\(", rt)) < 2:
                out.append(f"{rel} defines {hook}, which no runtime source defines weak and calls; "
                           f"the rule it holds would not run")
    return out


def test_no_runner_or_hook_wraps_what_the_runtime_wraps():
    assert set(WL.RUNNERS) == {"tick", "stage"}
    assert runtime_wrap_clashes(ROOT, LS.PRODUCTION_WRAPS, WL.RUNNERS) == []


def test_the_skip_intro_hook_is_defined_by_the_test_spawn_and_called_by_the_tweak():
    """The test spawn's 13-to-0x15-becomes-4 rule is sa2_harness_gamemode_after, which
    runtime/tweaks/skip-intro.c's wrap of sub_434160 calls; the stage runner wraps sub_434160 no
    longer. The two halves are one change: harness/replay/wrap_test_spawn.c, tools/s4/stage_run.py
    and the runtime's tweak file go in together."""
    assert "sub_434160" in dict(TS.files(RUNTIME))["tweaks/skip-intro.c"]
    assert "sub_434160" not in WL.runner_links("stage")[1]
    spawn = code(ROOT / "harness" / "replay" / "wrap_test_spawn.c")
    assert "sa2_harness_gamemode_after" in HOOK_DEF.findall(spawn)
    assert "sub_434160" not in WRAP_DEF.findall(spawn)


def test_the_practice_stage_hook_is_defined_by_the_test_spawn_and_called_by_the_tweak():
    """The test spawn's start (the data-DLL loader's init) and its save blocks are
    runtime/tweaks/practice-stage.c's wraps, which ask wrap_test_spawn.c's
    sa2_harness_test_spawn; the stage runner wraps none of the three. With skip-intro's hook, the
    test spawn defines two hooks and no wrap."""
    moved = {"sub_77dd10", "sub_4436a0", "sub_426760"}
    assert moved <= set(dict(TS.files(RUNTIME))["tweaks/practice-stage.c"])
    assert not moved & set(WL.runner_links("stage")[1])
    spawn = code(ROOT / "harness" / "replay" / "wrap_test_spawn.c")
    assert sorted(HOOK_DEF.findall(spawn)) == ["sa2_harness_gamemode_after", "sa2_harness_test_spawn"]
    assert WRAP_DEF.findall(spawn) == []


def hooks_tree(tmp_path: Path, stage_wraps: list[str], spawn: str, tweak: str) -> tuple[Path, dict]:
    (tmp_path / "runtime" / "tweaks").mkdir(parents=True)
    (tmp_path / "harness" / "replay").mkdir(parents=True)
    (tmp_path / "tools").mkdir()
    (tmp_path / "runtime" / "tweaks" / "skip-intro.c").write_text(tweak)
    (tmp_path / "harness" / "replay" / "wrap_test_spawn.c").write_text(spawn)
    (tmp_path / "tools" / "stage_run.py").write_text(f"HOOKS = []\nWRAPS = {stage_wraps!r}\n")
    return tmp_path, {"stage": (tmp_path / "tools" / "stage_run.py", "HOOKS", "WRAPS")}


OLD_SPAWN = "/* sub_434160 */\nvoid *__wrap_sub_434160(void *s, uint32_t pc,\n    void *m) {\n    return m;\n}\n"
NEW_SPAWN = ("/* sa2_harness_gamemode_after(before, after) */\n"
             "void sa2_harness_gamemode_after(uint32_t before, uint32_t after) {\n}\n")
TWEAK = ("__attribute__((weak)) void sa2_harness_gamemode_after(uint32_t b, uint32_t a) { }\n"
         "void *__wrap_sub_434160(void *s, uint32_t pc, void *m) {\n"
         "    sa2_harness_gamemode_after(1, 2);\n    return m;\n}\n")
NO_TWEAK = "/* sa2_harness_gamemode_after( is said here only */\n"


@pytest.mark.parametrize("stage_wraps, spawn, tweak, production, refusals", [
    # The whole change: nothing to say.
    (["sub_433ee0"], NEW_SPAWN, TWEAK, ["sub_434160"], []),
    # The tweak taken without the harness move: the runner's list and the hook file both repeat it.
    (["sub_433ee0", "sub_434160"], OLD_SPAWN, TWEAK, ["sub_434160"],
     ["tools/stage_run.py WRAPS (the stage runner's) passes -wrap ['sub_434160'], which the runtime "
      "wraps in every build",
      "harness/replay/wrap_test_spawn.c defines __wrap_ for ['sub_434160'], which the runtime defines "
      "in every build; its rule belongs in a hook the runtime's wrap calls"]),
    # The harness move taken without the tweak: the hook would never be called.
    (["sub_433ee0"], NEW_SPAWN, NO_TWEAK, [],
     ["harness/replay/wrap_test_spawn.c defines sa2_harness_gamemode_after, which no runtime source "
      "defines weak and calls; the rule it holds would not run"]),
])
def test_half_of_a_wrap_move_is_named(tmp_path, stage_wraps, spawn, tweak, production, refusals):
    root, runners = hooks_tree(tmp_path, stage_wraps, spawn, tweak)
    assert runtime_wrap_clashes(root, production, runners) == refusals


# ------------------------------------------------------------------ the host tweaks

PLAYER = ROOT / "host" / "player"
CXX = ["g++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter"]
# One host tweak of each home, and a second of the player's, in name order, after the list's own:
# the driver prints the mask from zz-app's bit, so they are 1, 2, 4 and 8 there.
HOST_LINES = ('    X(SA2_TWEAK_ZZ_APP, "zz-app", "app on", "an app tweak", "app") \\\n'
              '    X(SA2_TWEAK_ZZ_LAUNCH, "zz-launch", "launch on", "a launcher tweak", "launcher") \\\n'
              '    X(SA2_TWEAK_ZZ_PLAYER, "zz-player", "player on", "a player tweak", "player") \\\n'
              '    X(SA2_TWEAK_ZZ_SCALE, "zz-scale", "scale on", "a second player tweak", "player") \\\n')
ZZ = ["zz-app", "zz-launch", "zz-player", "zz-scale"]


def test_every_host_tweak_has_its_file_in_its_home_and_every_file_its_tweak():
    """A host tweak's own file is where its home says (host/player/tweaks/<name>.cpp,
    host/launch/tweaks/<name>.cpp, host/android/.../Tweak<Name>.java), and asks for its bit there;
    every file in the homes is a host tweak's (a player's or launcher's <name>.h beside its .cpp);
    no host tweak has a runtime file or a spec."""
    hosts = TS.host_rows(RUNTIME)
    owned = set()
    for r in hosts:
        own = TS.host_file(r)
        assert (ROOT / own).is_file(), f"{r['name']} (home {r['home']}) has no {own}"
        text = (ROOT / own).read_text()
        assert (r["bit"] if r["home"] != "app" else f'"{r["name"]}"') in text, f"{own} does not name its tweak"
        owned |= {own, own[:-len(".cpp")] + ".h"} if own.endswith(".cpp") else {own}
    assert [f for f in TS.host_files(ROOT) if f not in owned] == []
    assert not {Path(s).stem for s in TS.sources(RUNTIME)} & set(host_names())
    assert not {p.stem for p in (ROOT / TS.SPECS).glob("*.json")} & set(host_names())


def test_the_homes_and_their_files(tmp_path):
    rows = [{"name": "no-event-bars", "home": h} for h in ("player", "launcher", "app")]
    assert [TS.host_file(r) for r in rows] == [
        "host/player/tweaks/no-event-bars.cpp", "host/launch/tweaks/no-event-bars.cpp",
        "host/android/java/com/sa2recomp/app/TweakNoEventBars.java"]
    assert TS.java_class("2p-layout-in-1p") == "Tweak2pLayoutIn1p"
    for rel in ("host/player/tweaks/a.cpp", "host/player/tweaks/a.h", "host/launch/tweaks/b.cpp",
                "host/android/java/com/sa2recomp/app/TweakC.java", "host/android/java/com/sa2recomp/app/Ui.java"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("")
    assert TS.host_files(tmp_path) == ["host/android/java/com/sa2recomp/app/TweakC.java", "host/launch/tweaks/b.cpp",
                                       "host/player/tweaks/a.cpp", "host/player/tweaks/a.h"]


@pytest.mark.parametrize("line, refusal", [
    ('    X(SA2_TWEAK_ZZ, "zz", "zz on", "a tweak with no home") \\\n',
     r"SA2_TWEAK_HOST_LIST has \d+ X\( lines, of which \d+ are its shape"),
    ('    X(SA2_TWEAK_ZZ, "zz", "zz on", "a tweak", "desktop") \\\n', r"gives \['zz'\] a home that is none of"),
])
def test_a_host_line_the_tools_cannot_take_is_refused_by_name(tmp_path, line, refusal):
    rt = host_header(tmp_path, line)
    with pytest.raises(SystemExit, match=refusal):
        TS.host_rows(rt)


def host_header(d: Path, lines: str = HOST_LINES) -> Path:
    """d/runtime: runtime/sa2_tweaks.c and a copy of runtime/sa2_tweaks.h with `lines` in
    SA2_TWEAK_HOST_LIST; d/host/player: the host's reader, so its relative include finds the copy."""
    rt = d / "runtime"
    rt.mkdir(parents=True)
    (rt / "sa2_tweaks.c").write_text((RUNTIME / "sa2_tweaks.c").read_text())
    h = (RUNTIME / "sa2_tweaks.h").read_text()
    end = "    /* the end of the list */"
    at = h.index(end, h.index("#define SA2_TWEAK_HOST_LIST(X)"))
    (rt / "sa2_tweaks.h").write_text(h[:at] + lines + h[at:])
    hp = d / "host" / "player"
    hp.mkdir(parents=True)
    for f in ("tweaks.h", "tweaks.cpp"):
        (hp / f).write_text((PLAYER / f).read_text())
    return rt


# The game with four host tweaks registered: sa2_tweak_on of the game's one tweak.
HOST_GAME_DRIVER = r"""
#include <stdio.h>
#include "sa2_tweaks.h"
int main(void) { printf("%d\n", sa2_tweak_on(SA2_TWEAK_NO_EVENT_BARS)); return 0; }
"""
# The host's reader: argv[1] the home; argv[2] the caller's reason it is no play session ("-" in
# play), or "play:IPS", sa2tw::not_play's answer for its three flags (each 0 or 1: --interactive,
# --input-path, synthetic input); argv[3], when given, "game": off() asks a game environment of its
# own (GAME_<setting> in the driver's), as the launcher asks the one it gives the game, and not the
# driver's. Prints the mask from zz-app's bit, sa2tw::on of three bits, and the line.
HOST_DRIVER = r"""
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include "tweaks.h"
int main(int argc, char** argv) {
    if (argc != 3 && !(argc == 4 && !std::strcmp(argv[3], "game"))) return 2;
    const char* why = std::strcmp(argv[2], "-") ? argv[2] : nullptr;
    if (!std::strncmp(argv[2], "play:", 5) && std::strlen(argv[2]) == 8)
        why = sa2tw::not_play(argv[2][5] == '1', argv[2][6] == '1', argv[2][7] == '1');
    const char* why_off = argc == 3 ? sa2tw::off(why) : sa2tw::off(why, [](const char* k) {
        return std::getenv((std::string("GAME_") + k).c_str());
    });
    const sa2tw::Reading r = sa2tw::read(std::getenv("SA2_TWEAKS"), why_off, argv[1]);
    sa2tw::g_on = r.on;
    std::printf("%u %d %d %d\n[%s]\n", r.on >> sa2tw::SA2_TWEAK_ZZ_APP_INDEX, sa2tw::on(sa2tw::SA2_TWEAK_ZZ_PLAYER),
                sa2tw::on(sa2tw::SA2_TWEAK_ZZ_SCALE),
                sa2tw::on(sa2tw::SA2_TWEAK_ZZ_LAUNCH), r.line.c_str());
    return 0;
}
"""


@pytest.fixture(scope="module")
def host_exes(zig, tmp_path_factory) -> dict:
    if shutil.which("g++") is None:
        pytest.fail("g++ is not installed, and the host's reader is built with it (host/player/build.sh)")
    d = tmp_path_factory.mktemp("host")
    rt = host_header(d)
    assert [r["name"] for r in TS.host_rows(rt)] == host_names() + ZZ
    (d / "game.c").write_text(HOST_GAME_DRIVER)
    (d / "host.cpp").write_text(HOST_DRIVER)
    out = {"game": d / "game", "host": d / "host-reader"}
    done = subprocess.run([str(zig), "cc", *CC[:-2], "-I", str(rt), str(d / "game.c"), str(rt / "sa2_tweaks.c"),
                           "-o", str(out["game"])], capture_output=True, text=True, env=zig_env(d))
    assert done.returncode == 0, done.stderr
    done = subprocess.run([*CXX, "-I", str(d / "host" / "player"), str(d / "host.cpp"),
                           str(d / "host" / "player" / "tweaks.cpp"), "-o", str(out["host"])],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    out["dir"] = d
    return out


def test_the_game_names_a_host_tweak_as_the_hosts_and_turns_nothing_on(host_exes):
    game = host_exes["game"]
    assert run(game) == (["0"], "")
    assert run(game, SA2_TWEAKS="zz-player") == (["0"], (
        'sa2 tweaks: none on (SA2_TWEAKS=zz-player; host tweak, not the game\'s: "zz-player" (the player\'s))\n'))
    value = "no-event-bars, zz-launch,zz-app,zz-launch,bogus"
    assert run(game, SA2_TWEAKS=value) == (["1"], (
        f'sa2 tweaks: event-bars off (SA2_TWEAKS={value}; host tweaks, not the game\'s: "zz-app" (the app\'s), '
        f'"zz-launch" (the launcher\'s); no tweak is named "bogus", so it is ignored; '
        f'{the_tweaks(plain_names(), host_names() + ZZ)})\n'))
    assert run(game, SA2_TWEAKS="zz-player,no-event-bars", SA2_REPLAY_WORDS="/dev/null") == (["0"], (
        "sa2 tweaks: SA2_TWEAKS=zz-player,no-event-bars ignored in a replay (SA2_REPLAY_TICKS or "
        "SA2_REPLAY_WORDS is set)\n"))


def test_the_game_has_no_bit_for_a_host_tweak(zig, host_exes):
    """A host tweak's bit is the host's alone (host/player/tweaks.h): the runtime cannot ask it."""
    d = host_exes["dir"]
    (d / "asks.c").write_text('#include "sa2_tweaks.h"\nint asks(void) { return sa2_tweak_on(SA2_TWEAK_ZZ_PLAYER); }\n')
    done = subprocess.run([str(zig), "cc", *CC[:-2], "-I", str(d / "runtime"), "-c", str(d / "asks.c"),
                           "-o", str(d / "asks.o")], capture_output=True, text=True, env=zig_env(d))
    assert done.returncode != 0 and "SA2_TWEAK_ZZ_PLAYER" in done.stderr


def host(exe: Path, home: str = "player", why: str = "-", *source: str, **env: str) -> list[str]:
    out, err = run(exe, home, why, *source, **env)
    assert err == ""
    return out


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}])
def test_the_host_reader_with_no_value_turns_nothing_on_and_says_nothing(host_exes, env):
    assert host(host_exes["host"], **env) == ["0 0 0 0", "[]"]
    assert host(host_exes["host"], "player", "outside play (a test)", **env) == ["0 0 0 0", "[]"]


def test_the_host_reader_turns_on_its_homes_tweaks_in_list_order(host_exes):
    exe = host_exes["host"]
    assert host(exe, SA2_TWEAKS="zz-player") == ["4 1 0 0", "[sa2 host tweaks (player): player on (SA2_TWEAKS=zz-player)]"]
    assert host(exe, SA2_TWEAKS=" zz-scale ,zz-player,zz-scale,") == [
        "12 1 1 0", "[sa2 host tweaks (player): player on, scale on (SA2_TWEAKS= zz-scale ,zz-player,zz-scale,)]"]


def test_the_host_reader_names_another_homes_tweak_and_leaves_the_games_names_alone(host_exes):
    """A game tweak's name, and a name in neither list, are the game's to report (its line names
    them): the host's line says nothing of them, never calling a game tweak unknown."""
    exe = host_exes["host"]
    assert host(exe, SA2_TWEAKS="no-event-bars,Zz-Player,bogus") == [
        "0 0 0 0", "[sa2 host tweaks (player): none on (SA2_TWEAKS=no-event-bars,Zz-Player,bogus)]"]
    assert host(exe, SA2_TWEAKS="zz-launch,zz-player,zz-app") == [
        "4 1 0 0", '[sa2 host tweaks (player): player on (SA2_TWEAKS=zz-launch,zz-player,zz-app; not the player\'s: '
        '"zz-app" (the app\'s), "zz-launch" (the launcher\'s))]']
    assert host(exe, "launcher", SA2_TWEAKS="zz-player,zz-launch") == [
        "2 0 0 1", '[sa2 host tweaks (launcher): launch on (SA2_TWEAKS=zz-player,zz-launch; not the launcher\'s: '
        '"zz-player" (the player\'s))]']


NAMES_DRIVER = r"""
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include "tweaks.h"
int main(int argc, char** argv) {
    const sa2tw::Reading r = sa2tw::read(std::getenv("SA2_TWEAKS"), argc > 1 ? argv[1] : nullptr, "player");
    sa2tw::g_names = r.names;
    for (const std::string& n : r.names) std::printf("%s|", n.c_str());
    std::printf("\n%d\n", sa2tw::named("action-buttons"));
    return 0;
}
"""


def test_the_host_reader_keeps_every_name_for_a_host_change_that_follows_a_game_tweak(host_exes):
    """Reading.names and sa2tw::named (host/player/tweaks.h): every name the value gives, the
    game's too, trimmed, once each, in order; none when the tweaks are off (the touch overlay asks
    named("action-buttons") to show Y in a stage)."""
    d = host_exes["dir"]
    (d / "names.cpp").write_text(NAMES_DRIVER)
    done = subprocess.run([*CXX, "-I", str(d / "host" / "player"), str(d / "names.cpp"),
                           str(d / "host" / "player" / "tweaks.cpp"), "-o", str(d / "names")],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    names = lambda *a, **env: subprocess.run([str(d / "names"), *a], capture_output=True, text=True, check=True,
                                             env={**os.environ, **env}).stdout.splitlines()
    assert names(SA2_TWEAKS=" action-buttons ,zz-player,,action-buttons,bogus") == [
        "action-buttons|zz-player|bogus|", "1"]
    assert names(SA2_TWEAKS="no-event-bars") == ["no-event-bars|", "0"]
    assert names("outside play (a test)", SA2_TWEAKS="action-buttons") == ["", "0"]


@pytest.mark.parametrize("why, env, said", [
    ("outside play (play is --interactive with --input-path)", {},
     "ignored outside play (play is --interactive with --input-path)"),
    ("-", {"SA2_REPLAY_TICKS": "/dev/null"}, "ignored in a replay (SA2_REPLAY_TICKS or SA2_REPLAY_WORDS is set)"),
    ("outside play (x)", {"SA2_REPLAY_WORDS": "/dev/null"},
     "ignored in a replay (SA2_REPLAY_TICKS or SA2_REPLAY_WORDS is set)"),
])
def test_the_host_reader_is_off_out_of_play_and_in_a_replay_and_says_why(host_exes, why, env, said):
    assert host(host_exes["host"], "player", why, SA2_TWEAKS="zz-player,zz-scale", **env) == [
        "0 0 0 0", f"[sa2 host tweaks (player): SA2_TWEAKS=zz-player,zz-scale {said}]"]


OUTSIDE = "ignored outside play (play is --interactive with --input-path)"
SYNTHETIC = "ignored in a synthetic-input run (--synthetic-keys or --synthetic-input)"


@pytest.mark.parametrize("flags, said", [
    ("000", OUTSIDE),        # no --interactive: a stream file's replay
    ("001", OUTSIDE),
    ("010", OUTSIDE),
    ("100", OUTSIDE),        # --interactive with no --input-path: the launcher's selftest
    ("101", OUTSIDE),
    ("111", SYNTHETIC),      # --interactive --input-path with --synthetic-keys or --synthetic-input
    ("110", None),           # play: the launcher's, the Android session's
])
def test_the_players_play_check_is_play_alone(host_exes, flags, said):
    """sa2tw::not_play, the player's main's check (player.cpp): play is --interactive with
    --input-path and no synthetic input, and every other session says why its tweaks are off."""
    out = host(host_exes["host"], "player", f"play:{flags}", SA2_TWEAKS="zz-player")
    assert out == (["4 1 0 0", "[sa2 host tweaks (player): player on (SA2_TWEAKS=zz-player)]"] if said is None else
                   ["0 0 0 0", f"[sa2 host tweaks (player): SA2_TWEAKS=zz-player {said}]"])


REPLAY = "ignored in a replay (SA2_REPLAY_TICKS or SA2_REPLAY_WORDS is set)"


@pytest.mark.parametrize("env, said", [
    ({}, None),
    ({"GAME_SA2_REPLAY_TICKS": "/dev/null"}, REPLAY),
    ({"GAME_SA2_REPLAY_WORDS": ""}, REPLAY),                    # set, if empty, as the game asks it
    ({"SA2_REPLAY_TICKS": "/dev/null"}, None),                  # the launcher's own, which the game never sees
    ({"SA2_REPLAY_WORDS": "/dev/null", "GAME_SA2_REPLAY_TICKS": "x"}, REPLAY),
])
def test_the_launchers_replay_check_asks_the_games_environment_alone(host_exes, env, said):
    """The launcher asks off() of the environment it gives the game (game.env's lines), never its
    own: the game ignores its tweaks exactly when the launcher does."""
    out = host(host_exes["host"], "launcher", "-", "game", SA2_TWEAKS="zz-launch", **env)
    assert out == (["2 0 0 1", "[sa2 host tweaks (launcher): launch on (SA2_TWEAKS=zz-launch)]"] if said is None else
                   ["0 0 0 0", f"[sa2 host tweaks (launcher): SA2_TWEAKS=zz-launch {said}]"])


def test_a_game_tweaks_bit_cannot_be_asked_of_the_hosts_mask(host_exes):
    """sa2tw::on takes a host tweak's sa2tw::Bit, never the game's enum: the mistake does not compile."""
    d = host_exes["dir"]
    (d / "asks.cpp").write_text('#include "tweaks.h"\nbool asks() { return sa2tw::on(SA2_TWEAK_NO_EVENT_BARS); }\n')
    done = subprocess.run([*CXX, "-I", str(d / "host" / "player"), "-c", str(d / "asks.cpp"), "-o", str(d / "asks.o")],
                          capture_output=True, text=True)
    assert done.returncode != 0 and "sa2tw::Bit" in done.stderr


def test_the_real_host_reader_compiles_with_the_real_list(tmp_path):
    done = subprocess.run([*CXX, "-c", str(PLAYER / "tweaks.cpp"), "-o", str(tmp_path / "tweaks.o")],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_every_build_of_the_player_and_the_launcher_compiles_the_host_tweaks():
    """The reader (host/player/tweaks.cpp) and each home's files, by glob, in the four builds of the
    player and the launcher's, with runtime/sa2_tweaks.h among each build record's sources; and
    the player, the launcher and the Android session give the player SA2_TWEAKS in play."""
    for script, var in (("host/player/build.sh", "HERE"), ("host/d3d9gpu/build.sh", "P")):
        text = (ROOT / script).read_text()
        assert re.search(r"^for tu in player input mixer stream sha256 texcodec touch_overlay tweaks; do$", text, re.M), script
        assert f'for src in "${var}"/tweaks/*.cpp; do' in text and 'tu=tweak_$(basename "$src" .cpp)' in text, script
        assert '"$REPO/runtime/sa2_tweaks.h"' in text, script
    launch = (ROOT / "host/launch/build.sh").read_text()
    assert '"$HOSTDIR/player/tweaks.cpp"' in launch and 'for src in "$HERE"/tweaks/*.cpp; do' in launch
    sys.path.insert(0, str(ROOT / "tools" / "s7"))
    import host2_win_build as HW
    assert HW.PLAYER_UNITS[-1] == "tweaks"
    win = (ROOT / "tools/s7/host2_win_build.py").read_text()
    assert 'o / f"tweak_{s.stem}.o", None) for s in sorted((P / "tweaks").glob("*.cpp"))]' in win
    android = (ROOT / "tools/s8/android_app.py").read_text()
    assert 'o / f"tweak_{s.stem}.o") for s in sorted((P / "tweaks").glob("*.cpp"))]' in android
    spec = importlib.util.spec_from_file_location("android_app_tweaks_under_test", ROOT / "tools/s8/android_app.py")
    aa = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(aa)
    assert "tweaks" in aa.Builder.player_units(None)
    player = (ROOT / "host/player/player.cpp").read_text()
    assert ("const char* not_play = sa2tw::not_play(o.interactive, !o.input_path.empty(),\n"
            "                                           !o.synthetic_keys.empty() || !o.synthetic_input.empty());\n"
            '    const sa2tw::Reading tweaks = sa2tw::read(getenv("SA2_TWEAKS"), sa2tw::off(not_play), "player");') in player
    launcher = (ROOT / "host/launch/sa2_launch.cpp").read_text()
    assert ('    if (!tweak_value.empty()) {\n        penv.set("SA2_TWEAKS", tweak_value);\n'
            '        for (const char* k : {"SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"})\n'
            '            if (const std::string* v = genv.find(k)) penv.set(k, *v);\n') in launcher
    session = (ROOT / "host/android/native/sa2_android_session.cpp").read_text()
    assert ('        setenv("SA2_TWEAKS", tweaks->c_str(), 1);\n'
            '        for (const char* k : {"SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"})\n'
            '            if (const std::string* v = env_find(genv, k)) setenv(k, v->c_str(), 1);\n') in session
    assert ('"SA2_PLAYER_PROF", "SA2_PLAYER_PROF_OUT", "SA2_TWEAKS", "SA2_REPLAY_TICKS",\n'
            '                          "SA2_REPLAY_WORDS"}) unsetenv(k);') in session


def test_the_launcher_and_the_player_take_the_same_synthetic_input_flags():
    """The launcher drops SA2_TWEAKS for a --player-arg that is one of sa2tw::kSyntheticFlags; the
    player's main parses each of them and no other synthetic-input flag, so the launcher calls a
    session a test run exactly when the player does (test_installer_launch runs the launcher)."""
    flags = re.findall(r'"(--[a-z-]+)"', re.search(r"kSyntheticFlags\[\] = \{([^}]*)\}",
                                                   (PLAYER / "tweaks.h").read_text()).group(1))
    assert flags == ["--synthetic-keys", "--synthetic-input"]
    player = (ROOT / "host/player/player.cpp").read_text()
    assert sorted(set(re.findall(r'a == "(--synthetic-[a-z-]+)"', player))) == sorted(flags)
    launcher = (ROOT / "host/launch/sa2_launch.cpp").read_text()
    assert ("    for (const std::string& a : o.player_args)\n        for (const char* f : sa2tw::kSyntheticFlags)\n"
            "            if (!synthetic && a == f) synthetic = f;\n") in launcher


def d3d9gpu_tools_uncovered(build_sh: str) -> tuple[list[str], set[str]]:
    """The checkout's files that host/d3d9gpu's sources include, followed through each header they
    include (searched as its compiles search: the including file's directory, then -I$HERE,
    -I$HOST/player and -I$STREAM_H_DIR), that build_sh's SOURCES, the start set of the
    d3d9gpu_tools.json record, leaves out; and every such file reached."""
    block = re.search(r"^SOURCES=\(.*?\)$", build_sh, re.M | re.S)
    assert block, "host/d3d9gpu/build.sh: no SOURCES=(...)"
    here, host = ROOT / "host" / "d3d9gpu", ROOT / "host"
    script = (f'HERE={here}; HOST={host}; P={host}/player; STREAM_H_DIR={host}; REPO={ROOT}\n'
              f'{block.group(0)}\nprintf "%s\\0" "${{SOURCES[@]}}"')
    sources = [Path(s).resolve() for s in subprocess.run(["bash", "-c", script], capture_output=True,
                                                         check=True).stdout.decode().split("\0") if s]
    search = [here, host / "player", host]
    todo = [f for f in here.rglob("*") if f.suffix in (".cpp", ".h", ".inc") and f.is_file()]
    reached: set[Path] = set()
    while todo:
        f = todo.pop()
        for name in re.findall(r'^\s*#\s*include\s+"([^"]+)"', f.read_text(errors="replace"), re.M):
            hit = next((c.resolve() for c in [f.parent / name, *(d / name for d in search)] if c.is_file()), None)
            if hit and hit.is_relative_to(ROOT) and hit not in reached:
                reached.add(hit)
                todo.append(hit)
    uncovered = sorted(str(h.relative_to(ROOT)) for h in reached
                       if not any(h == s or h.is_relative_to(s) for s in sources))
    return uncovered, {str(h.relative_to(ROOT)) for h in reached}


def test_the_d3d9gpu_tools_record_names_every_header_d9g_includes_a_host_tweaks_among_them():
    """test_d3d9gpu, the census and the matrices compile d9g, which includes the player's host tweak
    headers (tweaks/tex-filter.h, its kLodBias and kAnisotropy): the tools' group record must start
    from them, or a record would still match a tree whose tweak values changed."""
    text = (ROOT / "host/d3d9gpu/build.sh").read_text()
    uncovered, reached = d3d9gpu_tools_uncovered(text)
    assert {"host/player/tweaks.h", "host/player/tweaks/tex-filter.h", "runtime/sa2_tweaks.h"} <= reached
    assert uncovered == []
    block = re.search(r"^SOURCES=\(.*?\)$", text, re.M | re.S).group(0)
    assert block.count(' "$P/tweaks" ') == 1
    without = text.replace(block, block.replace(' "$P/tweaks" ', ' '))
    assert d3d9gpu_tools_uncovered(without)[0] == ["host/player/tweaks/tex-filter.h"]
