"""vanilla-profile, the launcher's host tweak (host/launch/tweaks/vanilla-profile.cpp), and the
Android Tweaks screen's "Turn every tweak off", its form there.

On the desktop, share/game.env's SA2_TWEAKS naming vanilla-profile gives the game, the player and
the launcher's own reader SA2_TWEAKS=vanilla-profile alone: every other tweak is off and game.env
keeps its list. What each case pins:

* the tweak's own code, compiled with the host's reader (host/player/tweaks.cpp) as
  host/launch/build.sh compiles them, with a driver that calls the launcher's reading as play
  does: off (vanilla-profile not named, no value, a replay), the value is left as it was and nothing
  is said; on, the value becomes "vanilla-profile" and one line names each name dropped, once, a
  name no list holds too, or says none was;
* the same with test_tweaks' synthetic table, which has a second launcher tweak, zz-launch: named
  with vanilla-profile, it is off in the launcher too, and the launcher's line names
  vanilla-profile alone;
* the game's reader (runtime/sa2_tweaks.c, with no-event-bars) given that value: no game tweak is
  on, and its line names vanilla-profile as the launcher's;
* the real launcher, built against the synthetic table (test_installer_launch's release): play
  gives its fake game and fake player the dropped value, says so, and keeps it in the record; the
  selftest still drops the line for both;
* Android: GameSettings.tweaksOff clears every switch and returns what was on, so the session
  sends no SA2_TWEAKS; the settings screen that calls it compiles.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_installer_launch import env_for, installed, release, selftest  # noqa: E402,F401
from test_tweaks import exe, host_header, run, zig  # noqa: E402,F401
from test_android_settings import ANDROID_JAR, APP, JDK  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
PLAYER = ROOT / "host/player"
LAUNCH_TWEAKS = ROOT / "host/launch/tweaks"
HEAD = "sa2 host tweaks: vanilla: every other tweak off: "

# argv[1] the value ("-" for unset, which the launcher passes as ""); the launcher's reading of it,
# as play calls it (sa2tw::off answering from the driver's environment, which stands for the game's
# here, as launch() asks game.env's), then prints the value the game and the player get, the tweak's line, the
# launcher's own tweaks as they ask them (g_on: sa2tw::on of vanilla-profile, and whether any other
# is on), and its line, each in brackets. ZZ (test_tweaks' synthetic table) adds sa2tw::on of
# zz-launch, a second launcher tweak.
DRIVER = r"""
#include <cstdio>
#include <cstring>
#include <string>
#include "tweaks.h"
#include "vanilla-profile.h"
int main(int argc, char** argv) {
    if (argc != 2) return 2;
    std::string value = std::strcmp(argv[1], "-") ? argv[1] : "";
    std::string said;
    const sa2tw::Reading r = sa2tw::launcher_reading(value, said, sa2tw::off(nullptr));
    std::printf("[%s]\n[%s]\n[%d %d", value.c_str(), said.c_str(), sa2tw::on(sa2tw::SA2_TWEAK_VANILLA_PROFILE),
                (sa2tw::g_on & ~sa2tw::SA2_TWEAK_VANILLA_PROFILE.mask) != 0);
#ifdef ZZ
    std::printf(" %d", sa2tw::on(sa2tw::SA2_TWEAK_ZZ_LAUNCH));
#endif
    std::printf("]\n[%s]\n", r.line.c_str());
    return r.on == sa2tw::g_on ? 0 : 3;
}
"""


def build(d: Path, player: Path, *flags: str) -> Path:
    """The driver with the launcher's tweak and the host's reader in `player` (its tweaks.h reads
    the runtime/sa2_tweaks.h beside it)."""
    if shutil.which("g++") is None:
        pytest.fail("g++ is not installed, and the launcher is built with it (host/launch/build.sh)")
    (d / "driver.cpp").write_text(DRIVER)
    out = d / "vp"
    done = subprocess.run(["g++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror", *flags, f"-I{player}",
                           f"-I{LAUNCH_TWEAKS}", str(d / "driver.cpp"), str(player / "tweaks.cpp"),
                           str(LAUNCH_TWEAKS / "vanilla-profile.cpp"), "-o", str(out)],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return out


@pytest.fixture(scope="module")
def vp(tmp_path_factory) -> Path:
    return build(tmp_path_factory.mktemp("vanilla"), PLAYER)


@pytest.fixture(scope="module")
def vp_zz(tmp_path_factory) -> Path:
    """With test_tweaks' synthetic table: zz-app, zz-launch, zz-player and zz-scale after the list's
    own host tweaks."""
    d = tmp_path_factory.mktemp("vanilla-zz")
    host_header(d)
    return build(d, d / "host" / "player", "-DZZ")


def vanilla(exe: Path, value: str, **env: str) -> list[str]:
    clean = {k: v for k, v in os.environ.items() if k not in ("SA2_TWEAKS", "SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS")}
    p = subprocess.run([str(exe), value], capture_output=True, text=True, timeout=30, env={**clean, **env}, check=True)
    assert p.stderr == ""
    return p.stdout.splitlines()


LAUNCHER = "sa2 host tweaks (launcher): "
VANILLA = f"{LAUNCHER}vanilla: every other tweak off (SA2_TWEAKS=vanilla-profile)"


@pytest.mark.parametrize("value", ["-", "", "no-event-bars", "no-event-bars, vanilla-profiles", "Vanilla-Profile"])
def test_off_leaves_the_value_and_says_nothing(vp, value):
    """Off unless named exactly, as every reader matches a name: the launcher gives what game.env says."""
    given = "" if value == "-" else value
    line = f"{LAUNCHER}none on (SA2_TWEAKS={given})" if given else ""
    assert vanilla(vp, value) == [f"[{given}]", "[]", "[0 0]", f"[{line}]"]


def test_on_gives_vanilla_profile_alone_and_names_every_name_dropped(vp):
    value = " no-event-bars,vanilla-profile,,bogus ,no-event-bars,\tzz-player"
    assert vanilla(vp, value) == [
        "[vanilla-profile]",
        f"[{HEAD}the game, the player and the launcher get SA2_TWEAKS=vanilla-profile; "
        "dropped no-event-bars, bogus, zz-player]",
        "[1 0]", f"[{VANILLA}]"]


@pytest.mark.parametrize("value", ["vanilla-profile", " vanilla-profile ,vanilla-profile,"])
def test_on_with_nothing_else_named_says_none_is_dropped(vp, value):
    assert vanilla(vp, value) == [
        "[vanilla-profile]", f"[{HEAD}no other tweak is named, so none is dropped (SA2_TWEAKS={value})]",
        "[1 0]", f"[{VANILLA}]"]


@pytest.mark.parametrize("replay", ["SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"])
def test_off_in_a_replay(vp, replay):
    """The reader turns every host tweak off in a replay; the tweak does not ask again."""
    assert vanilla(vp, "vanilla-profile,no-event-bars", **{replay: "/dev/null"}) == [
        "[vanilla-profile,no-event-bars]", "[]", "[0 0]",
        f"[{LAUNCHER}SA2_TWEAKS=vanilla-profile,no-event-bars ignored in a replay (SA2_REPLAY_TICKS or "
        "SA2_REPLAY_WORDS is set)]"]


def test_a_second_launcher_tweak_is_on_alone_and_off_with_vanilla_profile(vp_zz):
    """The launcher reads the value the game and the player get: a launcher tweak named with
    vanilla-profile (zz-launch here; speedrun-timer is one) is off in the launcher as well, and the
    launcher's line names vanilla-profile alone, as the vanilla line says."""
    assert vanilla(vp_zz, "zz-launch,zz-player") == [
        "[zz-launch,zz-player]", "[]", "[0 1 1]",
        f"[{LAUNCHER}launch on (SA2_TWEAKS=zz-launch,zz-player; not the launcher's: \"zz-player\" (the player's))]"]
    assert vanilla(vp_zz, "zz-launch,vanilla-profile,zz-player") == [
        "[vanilla-profile]",
        f"[{HEAD}the game, the player and the launcher get SA2_TWEAKS=vanilla-profile; dropped zz-launch, zz-player]",
        "[1 0 0]", f"[{VANILLA}]"]
    assert vanilla(vp_zz, "zz-launch,vanilla-profile", SA2_REPLAY_TICKS="/dev/null")[:3] == [
        "[zz-launch,vanilla-profile]", "[]", "[0 0 0]"]


def test_the_report_and_the_row_are_the_registry_line():
    """The line's head is "sa2 host tweaks: <report>: ", with the report of its line in
    runtime/sa2_tweaks.h, and its home is the launcher's (so it has no Android switch)."""
    sys.path.insert(0, str(ROOT / "tools" / "s4"))
    import tweak_sources as TS
    row = next(r for r in TS.host_rows(ROOT / "runtime") if r["name"] == "vanilla-profile")
    assert (row["bit"], row["home"]) == ("SA2_TWEAK_VANILLA_PROFILE", "launcher")
    assert HEAD == f"sa2 host tweaks: {row['report']}: "
    assert HEAD in (LAUNCH_TWEAKS / "vanilla-profile.cpp").read_text()


def test_the_game_given_vanilla_profile_turns_nothing_on(exe):
    """The game's reader with what the launcher passes: no-event-bars stays off (the lifted answer, 1,
    for event 24 from the executor), and the line names vanilla-profile as the launcher's."""
    out, err = run(exe, "0x005FB508", "24", SA2_TWEAKS="vanilla-profile")
    assert out == ["24 1 4"]
    assert err.startswith('sa2 tweaks: none on (SA2_TWEAKS=vanilla-profile; host tweak, not the game\'s: '
                          '"vanilla-profile" (the launcher\'s)')
    out, _ = run(exe, "0x005FB508", "24", SA2_TWEAKS="vanilla-profile,no-event-bars")
    assert out == ["24 0 4"]          # the game alone does not drop it: the launcher does


def test_play_gives_the_game_the_player_and_the_launcher_vanilla_profile_alone(release, tmp_path):  # noqa: F811
    home, _ = installed(release, tmp_path)
    env = env_for(home, FAKE_GAME=json.dumps({"title_at": 30}), FAKE_PLAYER=json.dumps({"end_after": 1.0}))

    def play(name: str) -> tuple[str | None, str | None, str, str]:
        session = tmp_path / name
        p = subprocess.run([str(release["rel"] / "bin" / "sa2recomp"), "--no-pace", "--out", str(session)],
                           capture_output=True, text=True, env=env, timeout=300)
        assert p.returncode == 0, p.stdout + p.stderr
        game = json.loads((session / "game-args.runrec").read_text())["env"].get("SA2_TWEAKS")
        player = json.loads((session / "player-report.runrec").read_text())["sa2_tweaks"]
        return game, player, json.loads((session / "launch.runrec").read_text())["tweaks"], p.stderr

    game_env = release["rel"] / "share" / "game.env"
    text = game_env.read_text()
    try:
        game_env.write_text(text + "SA2_TWEAKS=no-event-bars,vanilla-profile\n")
        game, player, said, err = play("vanilla")
        assert (game, player) == ("vanilla-profile", "vanilla-profile")
        assert said == ("SA2_TWEAKS=no-event-bars,vanilla-profile (game.env); "
                        "given as SA2_TWEAKS=vanilla-profile (vanilla-profile)")
        # The vanilla line, then the launcher's reading of the value it was given: vanilla-profile alone.
        assert (f"sa2recomp: {HEAD}the game, the player and the launcher get SA2_TWEAKS=vanilla-profile; "
                f"dropped no-event-bars\nsa2recomp: {VANILLA}\n") in err
        assert "SA2_TWEAKS=no-event-bars,vanilla-profile)" not in err
        p, doc, session = selftest(release, home, tmp_path, name="selftest")
        assert p.returncode == 0, p.stdout + p.stderr
        assert "SA2_TWEAKS" not in json.loads((session / "game-args.runrec").read_text())["env"]
        assert doc["tweaks"] == "none: the selftest drops game.env's SA2_TWEAKS=no-event-bars,vanilla-profile"
        assert HEAD not in p.stderr
    finally:
        game_env.write_text(text)


# argv: tweak names to turn on -> what tweaksOff returned in brackets, then each --setting.
ANDROID_DRIVER = r"""
package com.sa2recomp.app;
public class VanillaDriver {
    public static void main(String[] a) {
        GameSettings s = new GameSettings();
        for (String n : a) s.tweaksOn.add(n);
        System.out.println("[" + s.tweaksOff() + "]");
        for (String e : s.sessionSettings(2340, 1080)) System.out.println(e);
    }
}
"""


def test_android_turn_every_tweak_off_clears_the_switches(tmp_path):
    missing = [str(p) for p in (JDK / "bin/javac", ANDROID_JAR) if not p.exists()]
    if missing:
        pytest.skip(f"needs the JDK and android.jar: missing {missing}")
    src = tmp_path / "java/com/sa2recomp/app"
    src.mkdir(parents=True)
    (src / "VanillaDriver.java").write_text(ANDROID_DRIVER)
    for name in ("GameSettings", "Sa2SettingsActivity", "Ui", "Sa2LayoutActivity"):
        shutil.copy(APP / f"{name}.java", src)
    done = subprocess.run([str(JDK / "bin/javac"), "--release", "11", "-nowarn", "-classpath", str(ANDROID_JAR),
                           "-d", str(tmp_path / "classes"), *map(str, src.glob("*.java"))],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr

    def java(*names: str) -> list[str]:
        return subprocess.run([str(JDK / "bin/java"), "-cp", f"{tmp_path / 'classes'}:{ANDROID_JAR}",
                               "com.sa2recomp.app.VanillaDriver", *names], capture_output=True, text=True,
                              check=True).stdout.splitlines()

    assert java() == ["[]"]
    assert java("no-event-bars") == ["[no-event-bars]"]     # and no SA2_TWEAKS after it
    screen = (APP / "Sa2TweaksActivity.java").read_text()
    assert 'mTurnOff = Ui.button(this, "Turn every tweak off",' in screen
    assert "mS.tweaksOff()" in screen and "mTurnOff.setEnabled(on > 0);" in screen
