"""tools/s7/replay_export.py without the game: a synthetic Debug Export, a stand-in release.

The tool's run of the real game is measured on a real export (its MATCH line); here, the parts
that decide what that run is given and how it is judged: the transitions it compares, the first
difference it names, the export it refuses (an entry that would land outside the work folder, an
export from before the session recorder), the write root it seeds from start-saves/ and
start-config/, and the settings it carries from the session (the picture's and the guest command
line, never the session's paths), with the pad script given only when the pad log holds a line.
"""
from __future__ import annotations

import importlib.util
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("replay_export", ROOT / "tools/s7/replay_export.py")
RE = importlib.util.module_from_spec(spec)
spec.loader.exec_module(RE)

TICKS = ("# sa2 launch observer v1\nT\ttick\tpresents\tgame_mode\tcurrent_menu\n"
         "T\t1\t5\t2\t0\nT\t2\t6\t13\t0\nT\t3\t7\t13\t0\nK\t3\t1\t0\t0\t1\nT\t4\t8\t14\t9\nT\t5\t9\t14\t9\n")


def export(tmp_path: Path, extra: dict[str, bytes] | None = None, drop: tuple[str, ...] = ()) -> Path:
    entries = {
        "MANIFEST.txt": b"# sa2recomp Debug Export, format 1\n# session: s1\n",
        "session/ticks.tsv": TICKS.encode(),
        "session/input.log": b"# log\n2 3 0x1C\n",
        "session/pad.log": b"# pad log\nmode 1 1\n5 2 0 1 0x0010 0 0 0 0 0 0\n",
        "session/game-env.txt": b"SA2_INSTALL=<game>\nSA2_GUEST_CMDLINE=-r\nSA2_PLAY_RES=2340x1080\n"
                                b"SA2_PLAY_HUD43=1\nSA2_REC_TRACE=<session>/game.fifo\nSA2_GUEST_HOST_CPUS=4-7\n",
        "session/start-saves/SONIC2B__ALF": b"\1" * 16,
        "session/start-saves/index.tsv": b"# index\n",
        "session/start-config/UserConfig.cfg": b"<Configs />\n",
    }
    entries.update(extra or {})
    z = tmp_path / "export.zip"
    with zipfile.ZipFile(z, "w") as w:
        for name, data in entries.items():
            if name not in drop:
                w.writestr(name, data)
    return z


def release(tmp_path: Path) -> Path:
    rel = tmp_path / "release"
    (rel / "share").mkdir(parents=True, exist_ok=True)
    (rel / "share/game.env").write_text("# settings\nSA2_D3D9_DEVICE=1\nSA2_REC_AUDIO=1\n")
    return rel


def test_transitions_are_the_changes_of_mode_or_menu_up_to_the_last_tick(tmp_path):
    (tmp_path / "t.tsv").write_text(TICKS)
    rows = RE.ticks(tmp_path / "t.tsv")
    assert rows == [(1, 2, 0), (2, 13, 0), (3, 13, 0), (4, 14, 9), (5, 14, 9)]
    assert RE.transitions(rows) == [(1, 2, 0), (2, 13, 0), (4, 14, 9)]
    assert RE.transitions(rows, last=3) == [(1, 2, 0), (2, 13, 0)]
    assert RE.first_difference([(1, 2, 0)], [(1, 2, 0)]) is None
    assert "transition 1" in RE.first_difference([(1, 2, 0), (4, 14, 9)], [(1, 2, 0), (5, 14, 9)])
    assert "the replay has 1 more" in RE.first_difference([(1, 2, 0)], [(1, 2, 0), (5, 14, 9)])


def test_logs_agree_up_to_the_shorter_ones_last_line():
    assert RE.logs_agree(["1 2 0x1C", "5 9 0x1C"], ["1 2 0x1C", "5 7 0x1C"])
    assert not RE.logs_agree(["1 2 0x1C", "5 9 0x1C"], ["1 3 0x1C", "5 9 0x1C"])
    assert RE.logs_agree([], [])


def test_the_export_seeds_a_fresh_write_root_and_carries_the_games_settings(tmp_path):
    work = tmp_path / "work"
    session = RE.extract(export(tmp_path), work / "export")
    (work / "overlay/resource").mkdir(parents=True)
    (work / "overlay/resource/stale").write_text("from an earlier replay")
    seeded = RE.seed_overlay(session, work / "overlay")
    assert seeded == ["resource/gd_PC/SAVEDATA/SONIC2B__ALF", "Config/UserConfig.cfg"]
    assert not (work / "overlay/resource/stale").exists()
    env, carry = RE.build_env(release(tmp_path), session, work, "/games/sa2", True, ["SA2_PLAY_HUD43=0"])
    assert carry == {"SA2_GUEST_CMDLINE": "-r", "SA2_PLAY_RES": "2340x1080", "SA2_PLAY_HUD43": "1"}
    assert env["SA2_PLAY_HUD43"] == "0"                         # --env wins
    assert env["SA2_D3D9_DEVICE"] == "1" and "SA2_GUEST_HOST_CPUS" not in env
    assert env["SA2_INSTALL"] == "/games/sa2" and env["SA2_VFS_HIDE_INSTALL_SAVES"] == "1"
    assert env["SA2_INPUT_DI_SCRIPT"] == str(session / "input.log")
    assert env["SA2_INPUT_PAD_SCRIPT"] == str(session / "pad.log")
    assert env["SA2_REC_TRACE"] == str(work / "stream.bin")
    with pytest.raises(RE.Refused):
        RE.build_env(release(tmp_path), session, work, "/g", False, ["SA2_INPUT_DI_SCRIPT=x"])


def test_an_empty_pad_log_gives_no_pad_script(tmp_path):
    work = tmp_path / "work"
    session = RE.extract(export(tmp_path, {"session/pad.log": b"# only a header\n"}), work / "export")
    env, _ = RE.build_env(release(tmp_path), session, work, "/g", False, [])
    assert "SA2_INPUT_PAD_SCRIPT" not in env and "SA2_REC_TRACE" not in env


def test_an_export_it_cannot_replay_is_refused(tmp_path):
    with pytest.raises(RE.Refused, match="the session was not recorded"):
        RE.seed_overlay(RE.extract(export(tmp_path, drop=("session/start-saves/SONIC2B__ALF",
                                                          "session/start-saves/index.tsv")), tmp_path / "w1"),
                        tmp_path / "w1/overlay")
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as w:
        w.writestr("MANIFEST.txt", "x")
        w.writestr("session/../../escape", "x")
    with pytest.raises(RE.Refused, match="outside the work folder"):
        RE.extract(bad, tmp_path / "w2")


def test_a_dry_run_prepares_the_work_folder_and_runs_nothing(tmp_path, capsys):
    rc = RE.main([str(export(tmp_path)), "--work", str(tmp_path / "work"), "--release", str(release(tmp_path)),
                  "--install", "/games/sa2", "--exe", "/games/sa2/sonic2app.exe", "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "session s1, 5 ticks" in out and "SA2_GUEST_CMDLINE=-r" in out
    assert (tmp_path / "work/overlay/resource/gd_PC/SAVEDATA/SONIC2B__ALF").is_file()
    assert not (tmp_path / "work/ticks.tsv").exists()
