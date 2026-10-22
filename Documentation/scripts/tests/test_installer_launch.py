"""The release's launcher (host/launch, `sa2recomp`), each rule shown holding and shown failing;
and the two tools around it, the fresh-host runner and the release's builder.

`sa2recomp` and `sa2-install` are built against one synthetic hash table (`tools/s7/install_table.py`
over test_installer_core's synthetic game: small files and hand-built PE images, never a shipped
byte), laid out as `tools/s7/release_build.py` lays out a release (`bin/`, `share/game.env`,
`share/dxvk.conf`, `lib/`, `release.manifest`), with two stand-ins for the lifted game and the
players: `sa2-game` is a Python script that speaks the game's side of every interface the launcher
uses (the stream into the game FIFO in host/sa2_stream.h's framing, the DirectInput key records
from the input FIFO, the launch observer's table, SIGTERM as the host's quit, the probe's run
record, a save written into its write root), and the players read the video pipe to its end, count
its frames and write a final report. What each case pins:

* the config the installer writes is what the game is given: SA2_INSTALL the folder, the shipped
  executable as its first argument and SA2_EXE_KIND=shipped, the release's game.env settings, and
  none of the caller's own SA2_ variables; the selftest's write root its session's own overlay;
* the installation is verified first, and a changed file stops the launch before the game starts;
* the selftest: the title held for --frames, an Enter through the input FIFO, File Select, a second
  Enter, the main menu; the record's verdict, its paths home-relative;
* each of the selftest's refusals on its own, one case each: a file opened that the table does not
  hold (the shipped resource/gd_PC/StageSelectConfig.ini), a table path not found, the unpacked
  executable mapped, a method off the M7 list, the record's ok false, a guest that did not end
  itself, a mode other than trap, a session not automated, a path refused, a file log cut short
  (opens or resolutions), another read root, a File Select that takes no Enter, a stream of bad
  framing, a player that exits nonzero, writes no final report or presents fewer frames than the
  relay handed it; an Enter the game never read; the title never reached;
* the selftest writes no user file and repeats on one home; a save in the installation's folder
  is named when the main menu does not come;
* a Ctrl-C reaches the launcher alone, which stops the game through SIGTERM; play ends at the
  window's close through the same path, with --no-sound leaving the recorder's sound off, and
  SDL's disk sound driver is refused; play paces and plays the sound by default; --player-arg
  reaches play's player alone;
* the first play with no config installs: Steam's copy by the installer's Steam route, then plays
  (and the next play installs nothing); a changed Steam copy is refused with the installer's own
  message in an error box; with no Steam copy the message box asks, and the folder chosen (the
  game's, or the one above it) or a ZIP chosen is installed by its route, then plays; a folder that
  holds no game is refused in a box; quitting the question or the picker installs nothing and
  shows no error; with no picker to show (no zenity and no portal, no display, no SDL3) it says
  what to run, with 4. SDL3's dialogs are the release's own libSDL3.so.0 (the pinned build) and
  answered by a zenity stand-in (host/launch/first_run_play.py's);
* an error that ends play is shown in a box only when no program reads the launcher's error
  stream, and the session's launcher.log keeps what it said;
* play's full width: --play-res and --no-widescreen over game.env's own SA2_PLAY_RES or
  SA2_PLAY_ASPECT, nothing with no display, nothing in the selftest, and the record says which;
* play's tweaks: game.env's SA2_TWEAKS as written, to the game and the player alike, never the
  caller's, none in the selftest or a synthetic-input --player-arg session (to neither), off for
  the launcher too in a replay that game.env sets (and the player is given that setting), never for
  the caller's own replay setting, and the record and the launcher's line say which;
* --host picks the player; the selftest with no config, a .json record and unknown arguments are
  refused; the launcher compiles for x86_64-windows-gnu;
* the fresh-host runner binds only each route's source read only, stops on anything it must not
  see, and its write probe tells a read-only source from a writable or a missing one;
* the release's builder rewrites a DT_RPATH to the release's lib/ (refusing a DT_RUNPATH or a
  longer path), generates the observer the launcher reads, and names its sources by hash; for
  Windows it makes the observer's dinput hook a strong definition (a COFF link does not let a weak
  definition answer a weak reference) and gives the recorder O_CLOEXEC;
* the Windows routes' runner under Wine (host/launch/windows_routes.py): each route a new prefix
  with no Z: drive, only its source bound read only (and named by its Windows path), the Steam
  registry values for the Steam route alone, the release at C:/Program Files/sa2recomp and the
  records at O:, the route's two commands exactly as the receipt reads them;
* the Windows play runner (host/launch/windows_play.py): its key script (Enter pressed and let
  up on schedule, then the window's close), its two steps (the folder route's install and play
  with the player's synthetic keys through --player-arg), the PulseAudio socket bound for sound,
  and its verdict read from a session's records (title, main menu, a stage after it, keys applied,
  the window's close);
* the first-run runner (host/launch/first_run_play.py): each Linux scenario's sandbox runs the
  launcher alone (no sa2-install step) on its own source (Steam's three paths, or the game folder
  alone, or nothing), with its zenity (a stand-in first on PATH, or an empty file), and its verdict
  holds each scenario to its outcome.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import textwrap
import time
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
HOST = Path(os.environ.get("SA2_HOST_SRC", ROOT / "host"))
TABLE_TOOL = ROOT / "tools" / "s7" / "install_table.py"
GAME_NAME = "Sonic Adventure 2"

# A skip is not a pass (AGENTS.md rule 8): without the compilers these tests build with, they fail.
if shutil.which("g++") is None:
    raise RuntimeError("g++ is not installed, and the launcher's tests build the launcher with it")


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def core():
    """test_installer_core's synthetic game, imported by path."""
    spec = importlib.util.spec_from_file_location("installer_core_fixtures",
                                                  Path(__file__).with_name("test_installer_core.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# The game's stand-in. Its behaviour is chosen by FAKE_GAME (a JSON object in the environment,
# which the launcher passes on: it drops only SA2_ variables): `title_at` (the tick the title
# comes; never, if absent), `opened` (extra installation paths it reports opened), `not_found`
# (paths it reports not found), `refused` (paths it reports refused as escapes), `exe_kind` (what
# its record says the loader mapped; the default is what SA2_EXE_KIND asks), `ignore_keys` (it
# never reads the input FIFO), `unlisted` (methods off the M7 list), `ok`, `trap_note`, `mode`
# and `automated` (its run record's), `drop_opens` and `drop_resolutions` (counted but not
# logged, as the runtime's bounded logs drop them), `read_root` (the read root it reports),
# `stall` (File Select never takes an Enter) and `bad_stream` (a record of no known framing).
# Like the game it stands in for, it creates a save in its write root when NEW FILE takes it to
# the main menu, and File Select takes no Enter while a save is visible (in the write root, or
# in the read root unless SA2_VFS_HIDE_INSTALL_SAVES hides it, as runtime/sa2_files.c does), the
# stall measured on 2026-10-11 (t6, t7).
FAKE_GAME = r"""#!/usr/bin/env python3
import json, os, signal, struct, sys, time
cfg = json.loads(os.environ.get("FAKE_GAME", "{}"))
argv = sys.argv
record = argv[argv.index("--json") + 1]
env = {k: v for k, v in os.environ.items() if k.startswith("SA2_")}
with open("game-args.runrec", "w") as f:
    json.dump({"argv": argv, "env": env, "cwd": os.getcwd()}, f)
stop, sigint = [], []
signal.signal(signal.SIGTERM, lambda *a: stop.append(1))
signal.signal(signal.SIGINT, lambda *a: sigint.append(1))
read_root, write_root = env["SA2_INSTALL"], env["SA2_VFS_WRITE_ROOT"]
def saves():
    roots = (write_root,) if env.get("SA2_VFS_HIDE_INSTALL_SAVES") == "1" else (read_root, write_root)
    return [r for r in roots
            if os.path.isdir(os.path.join(r, "resource/gd_PC/SAVEDATA"))
            and os.listdir(os.path.join(r, "resource/gd_PC/SAVEDATA"))]
trace = os.open(env["SA2_REC_TRACE"], os.O_WRONLY)
head = json.dumps({"schema": "sa2-d3d9-stream/v1"}).encode()
head += b" " * (-len(head) % 4)
os.write(trace, b"SA2D3D9S" + struct.pack("<II", 1, 16 + len(head)) + head)
keys = os.open(env["SA2_INPUT_DI_SOURCE"], os.O_RDONLY | os.O_NONBLOCK)
ticks = open(env["SA2_LAUNCH_TICKS"], "w")
ticks.write("# sa2 launch observer v1\nT\ttick\tpresents\tgame_mode\tcurrent_menu\n")
mode, menu, presents, poll, pending, held = 2, 0, 0, 0, b"", False
seq = 0                     # every record numbered 0, 1, 2, ..., as the recorder numbers them
title_at = cfg.get("title_at")
for tick in range(1, 5000):
    if stop:
        break
    if title_at is not None and tick == title_at:
        mode, menu = 14, 0
    if not cfg.get("ignore_keys"):
        try:
            pending += os.read(keys, 1 << 16)
        except BlockingIOError:
            pass
        recs = []
        while len(pending) >= 264:
            recs.append(pending[:264])
            pending = pending[264:]
        if recs:
            poll += 1
            seqs = [struct.unpack_from("<I", r, 4)[0] for r in recs]
            ticks.write(f"K\t{tick}\t{poll}\t{seqs[0]}\t{seqs[-1]}\t{len(recs)}\n")
            for r in recs:
                down = r[8 + 0x1C] & 0x80
                if down and not held and mode == 14:
                    if menu == 0:
                        menu = 9
                    elif menu == 9 and not cfg.get("stall") and not saves():
                        menu = 1
                        d = os.path.join(write_root, "resource/gd_PC/SAVEDATA")
                        os.makedirs(d, exist_ok=True)
                        with open(os.path.join(d, "SONIC2B__S01"), "wb") as f:
                            f.write(b"\0" * 64)
                held = bool(down)
    presents += 1
    if cfg.get("bad_stream") and presents == 5:
        os.write(trace, struct.pack("<III", 2, 6, seq) + b"\0" * 4)
        seq += 1
    os.write(trace, struct.pack("<III", 3, 16, seq) + b"\0" * 4)
    seq += 1
    ticks.write(f"T\t{tick}\t{presents}\t{mode}\t{menu}\n")
    ticks.flush()
    time.sleep(0.004)
os.close(trace)
kind = cfg.get("exe_kind", "shipped" if env.get("SA2_EXE_KIND") == "shipped" else "unpacked")
opened = [{"rel": "sonic2app.exe"}, {"rel": "resource/gd_PC/ADX/title.adx"},
          {"rel": "Config/UserConfig.cfg"}] + [{"rel": r} for r in cfg.get("opened", [])]
res = [{"rel": o["rel"], "status": "ok"} for o in opened]
res += [{"rel": r, "status": "not-found"} for r in ["Config/StageSelectConfig.ini"] + cfg.get("not_found", [])]
res += [{"rel": r, "status": "escape"} for r in cfg.get("refused", [])]
doc = {"ok": cfg.get("ok", True),
       "trap": {"note": cfg.get("trap_note", "the guest called ExitProcess(0) -- it ended the process itself")},
       "d3d9": {"closed_world": {"mode": cfg.get("mode", "trap"),
                                 "automated_session": cfg.get("automated", True),
                                 "unlisted_methods": cfg.get("unlisted", 0),
                                 "unlisted_calls": cfg.get("unlisted", 0)}},
       "files": {"ready": True, "read_root": cfg.get("read_root", read_root), "write_root": write_root,
                 "stats": {"resolves": len(res) + cfg.get("drop_resolutions", 0),
                           "opens": len(opened) + cfg.get("drop_opens", 0)},
                 "resolutions": res, "opened": opened},
       "exe": {"path": argv[1], "kind": kind, "kind_env": env.get("SA2_EXE_KIND"),
               "overlay": {"loaded": kind == "shipped", "refusal": "",
                           "table": {"readset_sha256": "0" * 64}}},
       "sigint": bool(sigint)}
with open(record, "w") as f:
    json.dump(doc, f)
"""

# The players' stand-in: it reads the video pipe to its end, counts the frames (the stand-in game's
# 16-byte records after the stream's header) and writes a final report. FAKE_PLAYER (a JSON object)
# chooses `exit` (its exit status), `complete` (the report's), `short` (frames it leaves
# unpresented) and `end_after` (seconds after which it asks for the end through --end-fd, as a
# window's close does). Its report names the SA2_TWEAKS it was given (`sa2_tweaks`, null for none)
# and the replay settings (`sa2_replay`: SA2_REPLAY_TICKS and SA2_REPLAY_WORDS, those set).
FAKE_PLAYER = r"""#!/usr/bin/env python3
import json, os, struct, sys, threading
cfg = json.loads(os.environ.get("FAKE_PLAYER", "{}"))
a = sys.argv
fd = int(a[a.index("--video-fd") + 1])
report = a[a.index("--report") + 1]
if "end_after" in cfg:
    end = int(a[a.index("--end-fd") + 1])
    threading.Timer(cfg["end_after"], lambda: os.write(end, b"x")).start()
data = b""
while True:
    b = os.read(fd, 1 << 20)
    if not b:
        break
    data += b
head = struct.unpack_from("<I", data, 12)[0] if len(data) >= 16 else len(data)
frames = sum(1 for at in range(head, len(data) - 15, 16) if struct.unpack_from("<I", data, at)[0] == 3)
with open(report, "w") as f:
    json.dump({"complete": cfg.get("complete", True), "exit_status": cfg.get("exit", 0), "bytes": len(data),
               "presents": frames - cfg.get("short", 0), "name": os.path.basename(a[0]),
               "argv": a[1:], "sa2_tweaks": os.environ.get("SA2_TWEAKS"),
               "sa2_replay": {k: os.environ[k] for k in ("SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS") if k in os.environ}},
              f)
sys.exit(cfg.get("exit", 0))
"""


@pytest.fixture(scope="module")
def release(tmp_path_factory) -> dict:
    """The synthetic table, sa2-install and sa2recomp built against it, and a release folder."""
    c = core()
    work = tmp_path_factory.mktemp("launch")
    files = c.game_files()
    receipt = {"schema": "sa2-recomp-receipt/v1", "id": "install-hashes-synthetic", "modded": False,
               "files": [{"path": p, "bytes": len(b), "sha256": hashlib.sha256(b).hexdigest()}
                         for p, b in sorted(files.items())]}
    rpath = work / "receipt.hashes"
    rpath.write_text(json.dumps(receipt))
    (work / "shipped.exe").write_bytes(files["sonic2app.exe"])
    (work / "unpacked.exe").write_bytes(c.unpacked_exe())
    gen = subprocess.run([sys.executable, str(TABLE_TOOL), "--receipt", str(rpath),
                          "--exe", str(work / "shipped.exe"), "--reference", str(work / "unpacked.exe"),
                          "--pins", "none", "--drm-receipt", "none", "--out-dir", str(work / "table")],
                         capture_output=True, text=True)
    assert gen.returncode == 0, gen.stderr
    table = work / "table" / "sa2_install_table.cpp"
    rel = work / "release"
    for d in ("bin", "share", "lib"):
        (rel / d).mkdir(parents=True)
    b = subprocess.run(["bash", str(HOST / "install" / "build.sh"), "--src", str(HOST / "install"),
                        "--table", str(table), "--out", str(rel / "bin" / "sa2-install")],
                       capture_output=True, text=True)
    assert b.returncode == 0, b.stdout + b.stderr
    b = subprocess.run(["bash", str(HOST / "launch" / "build.sh"), "--table", str(table),
                        "--out", str(rel / "bin" / "sa2recomp")], capture_output=True, text=True)
    assert b.returncode == 0, b.stdout + b.stderr
    assert "warning" not in b.stdout + b.stderr
    for name, text in (("sa2-game", FAKE_GAME), ("sa2_player_gpu", FAKE_PLAYER),
                       ("sa2_player", FAKE_PLAYER)):
        p = rel / "bin" / name
        p.write_text(text)
        p.chmod(0o755)
    (rel / "share" / "game.env").write_text("# settings\nSA2_DEVICE_PROFILE=default\nSA2_REC_AUDIO=1\n")
    (rel / "share" / "dxvk.conf").write_text("# dxvk\n")
    (rel / "lib" / "libdxvk_d3d9.so.0").write_bytes(b"")
    (rel / "release.manifest").write_text("# synthetic release\n")
    return {"rel": rel, "files": files, "core": c, "work": work}


def env_for(home: Path, **extra: str) -> dict:
    return {"PATH": "/usr/bin:/bin", "HOME": str(home), **extra}


def installed(release: dict, tmp_path: Path, mutate=None) -> tuple[Path, Path]:
    """A home with the synthetic game installed by the folder route; (home, game folder)."""
    files = dict(release["files"])
    if mutate:
        mutate(files)
    game = release["core"].write_tree(tmp_path / "games" / GAME_NAME, files)
    home = tmp_path / "home"
    home.mkdir()
    p = subprocess.run([str(release["rel"] / "bin" / "sa2-install"), "--source", "folder", str(game),
                        "--yes"], capture_output=True, text=True, env=env_for(home))
    assert p.returncode == 0, p.stdout + p.stderr
    return home, game


def selftest(release: dict, home: Path, tmp_path: Path, *args: str, fake: dict | None = None,
             frames: int = 20, extra_env: dict | None = None, player: dict | None = None,
             name: str = "session") -> tuple[subprocess.CompletedProcess, dict, Path]:
    session = tmp_path / name
    record = tmp_path / f"{name}.runrec"
    env = env_for(home, FAKE_GAME=json.dumps({"title_at": 30} if fake is None else fake),
                  FAKE_PLAYER=json.dumps(player or {}), **(extra_env or {}))
    p = subprocess.run([str(release["rel"] / "bin" / "sa2recomp"), "--selftest", "--frames", str(frames),
                        "--record", str(record), "--out", str(session), "--timeout", "60", *args],
                       capture_output=True, text=True, env=env, timeout=300)
    doc = json.loads(record.read_text()) if record.is_file() else {}
    return p, doc, session


# ------------------------------------------------------------------ what the game is given

def test_the_game_is_given_the_installation_the_config_names(release, tmp_path):
    home, game = installed(release, tmp_path)
    p, doc, session = selftest(release, home, tmp_path,
                               extra_env={"SA2_INSTALL": "/elsewhere", "SA2_EXE_KIND": "unpacked"})
    assert p.returncode == 0, p.stdout + p.stderr
    got = json.loads((session / "game-args.runrec").read_text())
    assert got["argv"][1:] == [str(game / "sonic2app.exe"), "--json", str(session / "game.runrec"),
                               "--timeout", "0"]
    e = got["env"]
    assert e["SA2_INSTALL"] == str(game)
    # The selftest's write root is the session's own overlay, never the user's.
    assert e["SA2_VFS_WRITE_ROOT"] == str(session / "overlay")
    assert e["SA2_EXE_KIND"] == "shipped"
    assert e["SA2_DEVICE_PROFILE"] == "default" and e["SA2_REC_AUDIO"] == "1"
    assert e["SA2_REC_TRACE"] == str(session / "game.fifo")
    assert e["SA2_INPUT_DI_SOURCE"] == str(session / "input.fifo")
    assert e["SA2_LAUNCH_TICKS"] == str(session / "ticks.tsv")
    assert got["cwd"] == str(session)
    assert doc["config"]["read_root"] == str(game).replace(str(home), "~")
    assert doc["config"]["overlay"] == "~/.local/share/sa2recomp/overlay"
    assert doc["game"]["exe"]["kind"] == "shipped" and doc["game"]["exe"]["overlay_loaded"] is True
    assert doc["game"]["read_root"] == doc["config"]["read_root"] and doc["game"]["files_ready"] is True
    assert doc["game"]["opens_counted"] == doc["game"]["opened"] == 3
    assert doc["game"]["resolves_counted"] == doc["game"]["resolutions_logged"] == 4


def test_a_changed_file_stops_the_launch_before_the_game_starts(release, tmp_path):
    home, game = installed(release, tmp_path)
    changed = game / "resource/gd_PC/ADX/title.adx"
    before = changed.stat().st_mtime_ns
    changed.write_bytes(b"other " * 300)
    # The same size, so the quick check sees the change by its mtime alone; the kernel stamps
    # mtimes from a coarse clock, and an install of the small synthetic game can finish within
    # one of its ticks, so the new mtime is set past the old one rather than left to the clock.
    os.utime(changed, ns=(before + 2_000_000_000, before + 2_000_000_000))
    p, doc, session = selftest(release, home, tmp_path)
    assert p.returncode == 3, p.stdout + p.stderr
    assert "does not verify" in p.stderr
    assert not (session / "game-args.runrec").exists() and doc == {}


def test_no_installation_is_refused_with_the_command_to_run(release, tmp_path):
    """The selftest never installs: its routes install explicitly."""
    home = tmp_path / "home"
    home.mkdir()
    p, doc, _ = selftest(release, home, tmp_path)
    assert p.returncode == 4 and "sa2-install --source steam --yes" in p.stderr and doc == {}
    assert not (home / ".config").exists() and not (home / ".local").exists()


# ------------------------------------------------------------------ the first play

# zenity's stand-in, for SDL3's dialogs on Linux (its zenity message box and picker): the first-run
# runner's (host/launch/first_run_play.py FAKE_ZENITY). It logs each call's arguments
# (FAKE_ZENITY_LOG, one JSON list per line) and answers as FAKE_ZENITY says: `button` (the message
# box's button to press, by its label; the first when absent), `folder` and `file` (what the
# folder and file pickers return; a cancel when empty).
FRP = load_module(HOST / "launch" / "first_run_play.py", "first_run_play_under_test")

CHOOSE_FOLDER = "Choose your Sonic Adventure 2 folder (the one holding sonic2app.exe)"


def sdl3_library() -> Path:
    """The pinned SDL3 the release bundles (tools/s7/release_build.py BUNDLED): the launcher's
    dialogs load it at run time from the release's lib/."""
    for p in (ROOT / "private/s5-tools/install/lib/libSDL3.so.0",
              ROOT / "private/generated/s7/release/linux-x86_64/lib/libSDL3.so.0"):
        if p.is_file():
            return p.resolve()
    raise RuntimeError("no libSDL3.so.0 in private/s5-tools/install/lib (tools/s5's SDL3 build)")


@pytest.fixture(scope="module")
def release_sdl(release, tmp_path_factory) -> dict:
    """The release with its SDL3 in lib/, as a real release has it, so the dialogs can be shown."""
    rel = tmp_path_factory.mktemp("launch-sdl") / "release"
    shutil.copytree(release["rel"], rel, symlinks=True)
    shutil.copyfile(sdl3_library(), rel / "lib" / "libSDL3.so.0")
    return dict(release, rel=rel)


def steam_home(release: dict, tmp_path: Path, mutate=None) -> tuple[Path, Path]:
    """A home whose Steam (~/.local/share/Steam) holds the synthetic game; no installation."""
    files = dict(release["files"])
    if mutate:
        mutate(files)
    home = tmp_path / "home"
    root = home / ".local" / "share" / "Steam"
    apps = root / "steamapps"
    game = release["core"].write_tree(apps / "common" / GAME_NAME, files)
    (apps / "libraryfolders.vdf").write_text(f'"libraryfolders" {{ "0" {{ "path" "{root}" }} }}')
    (apps / "appmanifest_213610.acf").write_text(
        '"AppState"\n{\n\t"appid"\t\t"213610"\n\t"StateFlags"\t\t"4"\n'
        f'\t"installdir"\t\t"{GAME_NAME}"\n\t"buildid"\t\t"15280"\n}}\n')
    return home, game


def dialog_env(tmp_path: Path, home: Path, zenity: dict | None, **extra: str) -> dict:
    """A desktop as SDL3 sees one with zenity (`zenity` its answers; None: no zenity at all), no
    desktop portal (no D-Bus session) and a DISPLAY that is no X server's, so SDL's message box
    and picker go to zenity."""
    fake = tmp_path / "fakebin"
    fake.mkdir(exist_ok=True)
    if not (fake / "python3").exists():
        (fake / "python3").symlink_to(shutil.which("python3"))
    if zenity is not None:
        (fake / "zenity").write_text(FRP.FAKE_ZENITY)
        (fake / "zenity").chmod(0o755)
    return {"PATH": str(fake), "HOME": str(home), "DISPLAY": ":99",
            "DBUS_SESSION_BUS_ADDRESS": f"unix:path={tmp_path / 'no-bus'}",
            "FAKE_ZENITY": json.dumps(zenity or {}), "FAKE_ZENITY_LOG": str(tmp_path / "zenity.log"), **extra}


def zenity_calls(tmp_path: Path) -> list[list[str]]:
    log = tmp_path / "zenity.log"
    calls = [json.loads(x) for x in log.read_text().splitlines()] if log.is_file() else []
    return [c for c in calls if c[:1] != ["--version"]]


def arg(call: list[str], flag: str) -> str:
    return call[call.index(flag) + 1]


def first_play(release: dict, home: Path, tmp_path: Path, env: dict | None = None) -> tuple[subprocess.CompletedProcess, Path]:
    """Play with no installation, the player's window closed after a second; (process, session)."""
    session = tmp_path / "session"
    env = dict(env or env_for(home), FAKE_GAME=json.dumps({"title_at": 30}),
               FAKE_PLAYER=json.dumps({"end_after": 1.0}))
    p = subprocess.run([str(release["rel"] / "bin" / "sa2recomp"), "--no-sound", "--no-pace", "--out", str(session)],
                       capture_output=True, text=True, env=env, timeout=300)
    return p, session


def config_of(home: Path) -> str:
    cfg = home / ".config" / "sa2recomp" / "sa2recomp.cfg"
    return cfg.read_text() if cfg.is_file() else ""


def first_run_steps(home: Path) -> list[Path]:
    """The installer's records of the first run (<data>/sa2recomp/first-run/<stamp>-<pid>/)."""
    return sorted((home / ".local/share/sa2recomp/first-run").glob("*/*.runrec"))


def test_the_first_play_installs_steams_copy_then_plays(release, tmp_path):
    home, game = steam_home(release, tmp_path)
    p, session = first_play(release, home, tmp_path)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "looking for Sonic Adventure 2 in Steam" in p.stderr
    cfg = config_of(home)
    assert "source.kind=steam\n" in cfg and f"read_root={game}\n" in cfg
    doc = json.loads((session / "launch.runrec").read_text())
    assert doc["mode"] == "play" and doc["ended_by"] == "the window was closed"
    fr = doc["first_run"]
    assert fr["ran"] is True and fr["outcome"] == "installed from Steam"
    assert [(s["route"], s["exit"]) for s in fr["steps"]] == [("steam", 0)]
    assert fr["steps"][0]["argv"][1:4] == ["--source", "steam", "--yes"]
    rec = json.loads(next(iter(first_run_steps(home))).read_text())
    assert rec["route"] == "steam-autodiscovery" and rec["installed"] is True
    assert rec["hash_checked"] is True
    got = json.loads((session / "game-args.runrec").read_text())
    assert got["env"]["SA2_INSTALL"] == str(game)
    assert "SA2_VFS_HIDE_INSTALL_SAVES" not in got["env"]
    assert (session / "launcher.log").read_text().splitlines()[0].startswith("sa2recomp: no installation yet")
    # The next play finds the installation and installs nothing.
    p2 = subprocess.run([str(release["rel"] / "bin" / "sa2recomp"), "--no-sound", "--no-pace",
                         "--out", str(tmp_path / "again")], capture_output=True, text=True, timeout=300,
                        env=env_for(home, FAKE_GAME=json.dumps({"title_at": 30}),
                                    FAKE_PLAYER=json.dumps({"end_after": 1.0})))
    assert p2.returncode == 0, p2.stdout + p2.stderr
    assert json.loads((tmp_path / "again" / "launch.runrec").read_text())["first_run"] == {"ran": False}
    assert len(first_run_steps(home)) == 1


def test_a_changed_steam_copy_is_refused_with_the_installers_own_message(release_sdl, tmp_path):
    def change(files):
        files["resource/gd_PC/ADX/title.adx"] = b"changed " * 100
    home, _ = steam_home(release_sdl, tmp_path, change)
    p, session = first_play(release_sdl, home, tmp_path, dialog_env(tmp_path, home, {}))
    assert p.returncode == 3, p.stdout + p.stderr
    assert config_of(home) == "" and not session.exists()
    assert "did not accept Steam's copy" in p.stderr
    # One error box, the installer's verdict and the file by name in it; no picker.
    calls = zenity_calls(tmp_path)
    assert len(calls) == 1 and "--question" in calls[0] and arg(calls[0], "--icon") == "dialog-error"
    text = arg(calls[0], "--text")
    assert "SA2_INSTALL_FAIL" in text and "resource/gd_PC/ADX/title.adx" in text


@pytest.mark.parametrize("where", ["the game's folder", "the folder above it"])
def test_with_no_steam_copy_the_folder_chosen_is_installed(release_sdl, tmp_path, where):
    home = tmp_path / "home"
    home.mkdir()
    game = release_sdl["core"].write_tree(home / "Games" / GAME_NAME, release_sdl["files"])
    chosen = game if where == "the game's folder" else game.parent
    env = dialog_env(tmp_path, home, {"button": "Choose folder", "folder": str(chosen)})
    p, session = first_play(release_sdl, home, tmp_path, env)
    assert p.returncode == 0, p.stdout + p.stderr
    cfg = config_of(home)
    assert "source.kind=folder\n" in cfg and f"read_root={game}\n" in cfg
    ask, picker = zenity_calls(tmp_path)
    assert "--question" in ask and CHOOSE_FOLDER in arg(ask, "--text")
    assert "Sonic Adventure 2 was not found in Steam." in arg(ask, "--text")
    assert [ask[i + 1] for i, x in enumerate(ask) if x == "--extra-button"] == ["Choose folder", "Choose ZIP file", "Quit"]
    assert "--file-selection" in picker and "--directory" in picker and arg(picker, "--title") == CHOOSE_FOLDER
    fr = json.loads((session / "launch.runrec").read_text())["first_run"]
    assert [(s["route"], s["exit"]) for s in fr["steps"]] == [("steam", 4), ("folder", 0)]
    assert fr["asked"] == "folder" and fr["picker"] == "chosen"
    assert fr["chosen"] == str(game).replace(str(home), "~")
    assert json.loads((session / "game-args.runrec").read_text())["env"]["SA2_INSTALL"] == str(game)


def test_two_steam_copies_ask_which_folder(release_sdl, tmp_path):
    """Steam with the game in two libraries: the installer will not pick one, so the person is
    asked, and the one chosen is installed by the folder route."""
    home, game = steam_home(release_sdl, tmp_path)
    root = home / ".local" / "share" / "Steam"
    lib2 = tmp_path / "lib2"
    other = release_sdl["core"].write_tree(lib2 / "steamapps" / "common" / GAME_NAME, release_sdl["files"])
    shutil.copyfile(root / "steamapps" / "appmanifest_213610.acf", lib2 / "steamapps" / "appmanifest_213610.acf")
    (root / "steamapps" / "libraryfolders.vdf").write_text(
        f'"libraryfolders" {{ "0" {{ "path" "{root}" }} "1" {{ "path" "{lib2}" }} }}')
    env = dialog_env(tmp_path, home, {"button": "Choose folder", "folder": str(other)})
    p, session = first_play(release_sdl, home, tmp_path, env)
    assert p.returncode == 0, p.stdout + p.stderr
    ask, _ = zenity_calls(tmp_path)
    assert arg(ask, "--text").startswith("Steam has more than one installation of Sonic Adventure 2.")
    fr = json.loads((session / "launch.runrec").read_text())["first_run"]
    assert [(s["route"], s["exit"]) for s in fr["steps"]] == [("steam", 6), ("folder", 0)]
    assert f"read_root={other}\n" in config_of(home) and str(game) not in config_of(home)


def test_with_no_steam_copy_a_zip_chosen_is_installed(release_sdl, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    game = release_sdl["core"].write_tree(tmp_path / "src" / GAME_NAME, release_sdl["files"])
    archive = home / "Downloads" / f"{GAME_NAME}.zip"
    FHR.make_zip(game, archive)
    env = dialog_env(tmp_path, home, {"button": "Choose ZIP file", "file": str(archive)})
    p, session = first_play(release_sdl, home, tmp_path, env)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "source.kind=zip\n" in config_of(home)
    _, picker = zenity_calls(tmp_path)
    assert "--file-selection" in picker and "--directory" not in picker
    # SDL spells the pattern case-blind for zenity.
    assert "--file-filter=ZIP archives | *.[zZ][iI][pP]" in picker
    fr = json.loads((session / "launch.runrec").read_text())["first_run"]
    assert [(s["route"], s["exit"]) for s in fr["steps"]] == [("steam", 4), ("zip", 0)]


def test_a_folder_that_holds_no_game_is_refused_in_a_box(release_sdl, tmp_path):
    home = tmp_path / "home"
    (home / "Empty").mkdir(parents=True)
    env = dialog_env(tmp_path, home, {"button": "Choose folder", "folder": str(home / "Empty")})
    p, session = first_play(release_sdl, home, tmp_path, env)
    assert p.returncode not in (0, 2), p.stdout + p.stderr
    assert config_of(home) == "" and not session.exists()
    ask, picker, box = zenity_calls(tmp_path)
    assert arg(box, "--icon") == "dialog-error" and "SA2_INSTALL_FAIL" in arg(box, "--text")
    assert str(home / "Empty") in arg(box, "--text")


@pytest.mark.parametrize("answer", [{"button": "Quit"}, {"button": "Choose folder", "folder": ""}])
def test_quitting_the_question_or_the_picker_installs_nothing(release_sdl, tmp_path, answer):
    home = tmp_path / "home"
    home.mkdir()
    p, session = first_play(release_sdl, home, tmp_path, dialog_env(tmp_path, home, answer))
    assert p.returncode == 4, p.stdout + p.stderr
    assert config_of(home) == "" and not session.exists()
    assert "nothing was installed" in p.stderr
    # The person's own choice: no error box after it.
    calls = zenity_calls(tmp_path)
    assert all(arg(c, "--icon") != "dialog-error" for c in calls if "--question" in c)
    assert len(calls) == (1 if answer["button"] == "Quit" else 2)


@pytest.mark.parametrize("stderr", ["read by a program (a pipe)", "read by no one (/dev/null)"])
def test_an_error_that_ends_play_is_boxed_only_when_no_program_reads_the_launcher(release_sdl, tmp_path, stderr):
    """A launch from a file manager (its error stream goes nowhere a person reads) shows what
    ended it in an error box; a runner reading the error stream gets no box to wait on."""
    home, game = installed(release_sdl, tmp_path)
    changed = game / "resource/gd_PC/ADX/title.adx"
    before = changed.stat().st_mtime_ns
    changed.write_bytes(b"other " * 300)
    os.utime(changed, ns=(before + 2_000_000_000, before + 2_000_000_000))
    env = dialog_env(tmp_path, home, {}, FAKE_GAME="{}", FAKE_PLAYER="{}")
    argv = [str(release_sdl["rel"] / "bin" / "sa2recomp"), "--no-sound", "--out", str(tmp_path / "session")]
    piped = stderr.startswith("read by a program")
    p = subprocess.run(argv, env=env, timeout=300, stdout=subprocess.DEVNULL,
                       stderr=subprocess.PIPE if piped else subprocess.DEVNULL)
    assert p.returncode == 3
    calls = zenity_calls(tmp_path)
    if piped:
        assert calls == []
    else:
        assert len(calls) == 1 and arg(calls[0], "--icon") == "dialog-error"
        assert "Sonic Adventure 2 could not start." in arg(calls[0], "--text")
        assert "the installation does not verify" in arg(calls[0], "--text")
    # The session keeps what the launcher said either way.
    log = (tmp_path / "session" / "launcher.log").read_text()
    assert "the installation does not verify" in log


@pytest.mark.parametrize("case, why", [
    ("no zenity", "File dialog driver unsupported"),
    ("no display", "there is no display (DISPLAY and WAYLAND_DISPLAY are unset)"),
    ("no SDL3", "cannot load"),
])
def test_with_no_picker_to_show_it_says_what_to_run(release, release_sdl, tmp_path, case, why):
    home = tmp_path / "home"
    home.mkdir()
    rel = release if case == "no SDL3" else release_sdl
    env = dialog_env(tmp_path, home, None)
    if case == "no display":
        del env["DISPLAY"]
    p, session = first_play(rel, home, tmp_path, env)
    assert p.returncode == 4, p.stdout + p.stderr
    assert config_of(home) == "" and not session.exists()
    assert "No folder picker could be shown" in p.stderr and why in p.stderr
    assert f'"{rel["rel"]}/bin/sa2-install" --source folder' in p.stderr
    assert [r.name for r in first_run_steps(home)] == ["1-steam.runrec"]


# ------------------------------------------------------------------ the selftest

def test_the_selftest_holds_the_title_then_enters_reach_the_main_menu(release, tmp_path):
    home, _ = installed(release, tmp_path)
    p, doc, session = selftest(release, home, tmp_path, frames=20)
    assert p.returncode == 0, p.stdout + p.stderr
    assert p.stdout.startswith("SA2_SELFTEST_OK")
    assert doc["schema"] == "sa2-s7-launch-run/v1" and doc["launched"] is True
    assert doc["failures"] == [] and doc["ended_by"] == "selftest: the main menu"
    st = doc["selftest"]
    assert st["title"] is True and st["main_menu"] is True and st["frames"] == 20
    assert st["title_first_tick"] == 30
    # The first Enter comes after the title held 20 frames, and finds the title; the second
    # finds File Select (CurrentMenu 9); the main menu (1) follows.
    first, second = st["enters"]
    assert first["dik"] == 0x1C and first["current_menu"] == 0 and first["read"] is True
    assert first["presents"] - st["title_first_presents"] >= 20
    assert second["current_menu"] == 9 and second["read"] is True and second["released"] is True
    assert [t[2:] for t in st["transitions"]][-3:] == [[14, 0], [14, 9], [14, 1]]
    assert doc["game"]["opened_not_in_table"] == [] and doc["game"]["table_paths_not_found"] == []
    assert doc["game"]["stopped_by"] == "SIGTERM"
    assert doc["player"]["exit"] == 0 and doc["player"]["cancelled"] is False
    r = doc["relay"]
    assert r["bytes_in"] == r["bytes_out"] > 0 and r["bytes_dropped"] == 0
    assert r["frames_in"] == r["frames_out"] == st["last_presents"] and r["framing_problem"] == ""
    assert doc["player"]["presents"] == r["frames_out"]
    report = json.loads((session / "player-report.runrec").read_text())
    assert report["bytes"] == r["bytes_out"] and report["name"] == "sa2_player_gpu"
    assert "--input-path" not in report["argv"]
    # Every path under HOME is written with '~'.
    assert str(home) not in json.dumps({k: v for k, v in doc.items() if k != "session"})
    assert doc["verify"]["ran"] is True and doc["verify"]["exit"] == 0
    assert doc["player"]["window"] == "none (offscreen)" and doc["player"]["video_driver"] == "offscreen"


@pytest.mark.parametrize("fake, player, failure", [
    ({"opened": ["resource/gd_PC/StageSelectConfig.ini"]}, {},
     "files opened that the table does not hold: [\"resource/gd_PC/StageSelectConfig.ini\"]"),
    ({"not_found": ["resource/gd_PC/SOC/menu.pak"]}, {},
     "table paths not found: [\"resource/gd_PC/SOC/menu.pak\"]"),
    ({"exe_kind": "unpacked"}, {},
     "the loader mapped the unpacked executable, not the shipped one through the overlay"),
    ({"unlisted": 1}, {}, "the closed world counted a method off the M7 list"),
    ({"ok": False}, {}, "the run record says ok false"),
    ({"trap_note": "a trap fired at 0x401000"}, {}, "the guest did not end itself: a trap fired at 0x401000"),
    ({"mode": "audit"}, {}, "the closed world ran in mode 'audit', not 'trap'"),
    ({"automated": False}, {}, "the device did not run as an automated session"),
    ({"refused": ["../../etc/passwd"]}, {}, "paths refused: [\"../../etc/passwd (escape)\"]"),
    ({"drop_opens": 1}, {}, "the file log was truncated: 4 of 4 resolutions and 3 of 4 opens logged"),
    ({"drop_resolutions": 2}, {}, "the file log was truncated: 4 of 6 resolutions and 3 of 3 opens logged"),
    ({"read_root": "/elsewhere"}, {}, "the game's file system was not ready on the installation's folder"),
    ({"stall": True}, {}, "CurrentMenu 9 after 4 Enters; the main menu never came"),
    ({"bad_stream": True}, {}, "the stream: the record at offset"),
    ({}, {"exit": 3}, "the player exited 3"),
    ({}, {"complete": False}, "the player ended with exit status 0, its report not final"),
    ({}, {"short": 2}, "the player presented"),
], ids=["unhashed-shipped-file-opened", "table-path-not-found", "unpacked-exe", "off-the-m7-list",
        "record-not-ok", "guest-did-not-exit", "not-trap-mode", "not-automated", "path-refused",
        "opens-truncated", "resolutions-truncated", "another-read-root", "menu-stalls", "bad-stream",
        "player-exit", "player-report-not-final", "player-presented-fewer"])
def test_each_refusal_of_the_selftest(release, tmp_path, fake, player, failure):
    home, _ = installed(release, tmp_path)
    p, doc, _ = selftest(release, home, tmp_path, fake=dict({"title_at": 30}, **fake), player=player)
    assert p.returncode == 1, p.stdout + p.stderr
    assert doc["launched"] is False
    assert any(f.startswith(failure) for f in doc["failures"]), doc["failures"]
    assert p.stdout.startswith("SA2_SELFTEST_FAIL")


def test_the_selftest_writes_no_user_file_and_repeats(release, tmp_path):
    """The selftest's game writes its save into the session's overlay, seeded with copies of the
    user's Config files; the user's overlay is never written, and a second selftest on the same
    home passes as the first did."""
    home, _ = installed(release, tmp_path)
    overlay = home / ".local/share/sa2recomp/overlay"
    (overlay / "Config").mkdir(parents=True, exist_ok=True)   # the installer's default is there
    (overlay / "Config/UserConfig.cfg").write_text("<Configs FullScreen=\"0\" />\n")
    (overlay / "Config/notes.txt").write_text("not a user file\n")
    before = sorted((str(p.relative_to(overlay)), p.read_bytes()) for p in overlay.rglob("*") if p.is_file())
    for name in ("first", "second"):
        p, doc, session = selftest(release, home, tmp_path, name=name)
        assert p.returncode == 0, p.stdout + p.stderr
        assert doc["launched"] is True
        st = doc["selftest"]
        assert st["seeded_user_files"] == ["Config/Keyboard.cfg", "Config/UserConfig.cfg"]
        assert st["read_root_saves"] == []
        assert st["write_root"] == str(session / "overlay").replace(str(home), "~")
        assert (session / "overlay/Config/UserConfig.cfg").read_bytes() == \
            (overlay / "Config/UserConfig.cfg").read_bytes()
        assert (session / "overlay/resource/gd_PC/SAVEDATA/SONIC2B__S01").is_file()
    after = sorted((str(p.relative_to(overlay)), p.read_bytes()) for p in overlay.rglob("*") if p.is_file())
    assert after == before


def test_a_game_folder_with_no_user_config_runs_on_the_installers_defaults(release, tmp_path):
    """A Steam install never launched has no Config/UserConfig.cfg or Config/Keyboard.cfg. The
    installer wrote its defaults into the overlay; the selftest's overlay is seeded with them, and
    the game folder still holds neither."""
    home, game = installed(release, tmp_path)
    assert not (game / "Config").exists()
    overlay = home / ".local/share/sa2recomp/overlay"
    for name in ("UserConfig.cfg", "Keyboard.cfg"):
        assert (overlay / "Config" / name).read_bytes() == (ROOT / "private/install/Config" / name).read_bytes()
    p, doc, session = selftest(release, home, tmp_path)
    assert p.returncode == 0, p.stdout + p.stderr
    assert doc["launched"] is True, doc["failures"]
    assert doc["selftest"]["seeded_user_files"] == ["Config/Keyboard.cfg", "Config/UserConfig.cfg"]
    for name in ("UserConfig.cfg", "Keyboard.cfg"):
        assert (session / "overlay/Config" / name).read_bytes() == (overlay / "Config" / name).read_bytes()
    assert not (game / "Config").exists()


def test_a_save_in_the_installation_folder_is_hidden_from_the_selftest(release, tmp_path):
    """A folder someone has played from holds the original game's saves. The selftest's game is
    asked not to see them (SA2_VFS_HIDE_INSTALL_SAVES), so File Select runs as on a folder with
    none and NEW FILE writes the session's own save; the record names the saves hidden."""
    def played(files):
        files["resource/gd_PC/SAVEDATA/SONIC2B__S01"] = b"\1" * 64
    home, _ = installed(release, tmp_path, mutate=played)
    p, doc, session = selftest(release, home, tmp_path)
    assert p.returncode == 0, p.stdout + p.stderr
    assert doc["launched"] is True, doc["failures"]
    assert doc["selftest"]["read_root_saves"] == ["SONIC2B__S01"]
    args = json.loads((session / "game-args.runrec").read_text())
    assert args["env"]["SA2_VFS_HIDE_INSTALL_SAVES"] == "1"
    assert (session / "overlay/resource/gd_PC/SAVEDATA/SONIC2B__S01").read_bytes() == b"\0" * 64


def test_the_session_recorder_is_off_unless_asked_for(release, tmp_path):
    """Without --record-session or SA2_RECORD_SESSION=1 in share/game.env the launch writes what it
    always has: no start or end snapshots, no session-info.json, no pad log for the game; the
    launch record says so. SA2_RECORD_SESSION=1 in game.env turns it on, and is the launcher's:
    the game is not given it."""
    home, _ = installed(release, tmp_path)
    p, doc, session = selftest(release, home, tmp_path)
    assert p.returncode == 0, p.stdout + p.stderr
    args = json.loads((session / "game-args.runrec").read_text())
    assert "SA2_INPUT_PAD_LOG" not in args["env"]
    for n in ("start-saves", "start-config", "end-saves", "session-info.json", "game-env.txt"):
        assert not (session / n).exists(), n
    assert doc["session_recorder"] is False
    assert "session recorder: off" in p.stdout + p.stderr
    env_file = release["rel"] / "share" / "game.env"
    before = env_file.read_text()
    try:
        env_file.write_text(before + "SA2_RECORD_SESSION=1\n")
        p, doc, session = selftest(release, home, tmp_path, name="recorded")
    finally:
        env_file.write_text(before)
    assert p.returncode == 0, p.stdout + p.stderr
    args = json.loads((session / "game-args.runrec").read_text())
    assert args["env"]["SA2_INPUT_PAD_LOG"] == str(session / "pad.log") and "SA2_RECORD_SESSION" not in args["env"]
    assert (session / "start-saves").is_dir() and (session / "session-info.json").is_file()
    assert doc["session_recorder"] is True


def test_a_session_keeps_what_its_replay_needs(release, tmp_path):
    """The session recorder (host/launch/sa2_session_files.h), with --record-session: before the
    game, the saves and Config/ the game will see (start-saves/, start-config/, each with its
    index), its settings with the paths redacted (game-env.txt) and session-info.json naming the
    build; the game is given the pad log (SA2_INPUT_PAD_LOG); after it, the saves it left
    (end-saves/)."""
    home, _ = installed(release, tmp_path)
    p, doc, session = selftest(release, home, tmp_path, "--record-session")
    assert p.returncode == 0, p.stdout + p.stderr
    args = json.loads((session / "game-args.runrec").read_text())
    assert args["env"]["SA2_INPUT_PAD_LOG"] == str(session / "pad.log")
    assert sorted(f.name for f in (session / "start-saves").iterdir()) == ["index.tsv"]
    assert sorted(f.name for f in (session / "start-config").iterdir()) == ["Keyboard.cfg", "UserConfig.cfg",
                                                                             "index.tsv"]
    assert (session / "end-saves/SONIC2B__S01").read_bytes() == b"\0" * 64
    assert re.search(r"^SONIC2B__S01\toverlay\t64\t[0-9a-f]{64}$", (session / "end-saves/index.tsv").read_text(), re.M)
    genv = (session / "game-env.txt").read_text()
    assert "SA2_INPUT_PAD_LOG=<session>/pad.log\n" in genv and "SA2_VFS_WRITE_ROOT=<overlay>\n" in genv
    assert str(home) not in genv and str(tmp_path) not in genv
    info = json.loads((session / "session-info.json").read_text())
    assert info["schema"] == "sa2-session-info/v1" and info["runner"] == "host/launch" and info["mode"] == "selftest"
    assert info["build"]["game_sha256"] == hashlib.sha256((release["rel"] / "bin/sa2-game").read_bytes()).hexdigest()
    assert info["platform"]["os"] == "linux" and info["read_root_saves_hidden"] == "true"


def test_debug_export_zips_a_session_with_its_manifest_and_no_home_path(release, tmp_path):
    """sa2recomp --debug-export SESSION --out FILE (host/launch/sa2_debug_export.h): every regular
    file of the session under session/, the release's manifest and settings under build/, the
    installer's config under install/, and MANIFEST.txt naming each entry with its size and sha256;
    no FIFO, no selftest overlay, and no text entry holding the home folder. A session recorded
    with --record-session; one that was not still exports, with a note that it cannot be replayed."""
    home, _ = installed(release, tmp_path)
    p, doc, session = selftest(release, home, tmp_path, "--record-session")
    assert p.returncode == 0, p.stdout + p.stderr
    out = tmp_path / "export.zip"
    e = subprocess.run([str(release["rel"] / "bin" / "sa2recomp"), "--debug-export", str(session), "--out", str(out)],
                       capture_output=True, text=True, env=env_for(home))
    assert e.returncode == 0, e.stdout + e.stderr
    assert "Debug Export of session session:" in e.stdout
    z = zipfile.ZipFile(out)
    assert z.testzip() is None
    names = set(z.namelist())
    for n in ("MANIFEST.txt", "session/ticks.tsv", "session/game.runrec",
              "session/end-saves/SONIC2B__S01", "session/start-config/UserConfig.cfg", "session/session-info.json",
              "session/game-env.txt", "build/release.manifest", "build/game.env", "install/sa2recomp.cfg"):
        assert n in names, n
    assert not any(n.startswith("session/overlay/") or n.endswith(".fifo") for n in names), names
    manifest = z.read("MANIFEST.txt").decode()
    rows = [l.split("\t") for l in manifest.splitlines() if l and not l.startswith("#")]
    assert {r[0] for r in rows} == names - {"MANIFEST.txt"}
    for name, size, sha, what in rows:
        data = z.read(name)
        assert int(size) == len(data) and sha == hashlib.sha256(data).hexdigest() and what
        if not name.endswith(("SONIC2B__S01", ".cfg")):
            assert str(home).encode() not in data, name
    assert "session/ticks.tsv\t" in manifest and "SA2_LAUNCH_TICKS" in manifest
    assert "not replayable" not in manifest
    # An unrecorded session: its logs and the saves at export time, and the note.
    p, doc, plain = selftest(release, home, tmp_path, name="plain")
    assert p.returncode == 0, p.stdout + p.stderr
    out2 = tmp_path / "plain.zip"
    e = subprocess.run([str(release["rel"] / "bin" / "sa2recomp"), "--debug-export", str(plain), "--out", str(out2)],
                       capture_output=True, text=True, env=env_for(home))
    assert e.returncode == 0, e.stdout + e.stderr
    z2 = zipfile.ZipFile(out2)
    names2 = set(z2.namelist())
    assert "session/game-args.runrec" in names2 and "MANIFEST.txt" in names2
    assert not any(n.startswith("session/start-saves/") for n in names2)
    assert "# not replayable: the session recorder was off" in z2.read("MANIFEST.txt").decode()


def test_debug_export_with_no_session_says_so(release, tmp_path):
    home, _ = installed(release, tmp_path)
    e = subprocess.run([str(release["rel"] / "bin" / "sa2recomp"), "--debug-export"], capture_output=True, text=True,
                       env=env_for(home))
    assert e.returncode == 5 and "no session under <data>/sessions to export" in e.stderr, e.stderr


def test_a_ctrl_c_reaches_the_launcher_alone(release, tmp_path):
    """The game runs in a process group of its own: a Ctrl-C to the terminal's group reaches the
    launcher, which stops the game through SIGTERM, and the game never sees the SIGINT."""
    home, _ = installed(release, tmp_path)
    session, record = tmp_path / "session", tmp_path / "launch.runrec"
    env = env_for(home, FAKE_GAME=json.dumps({}), FAKE_PLAYER="{}")
    p = subprocess.Popen([str(release["rel"] / "bin" / "sa2recomp"), "--selftest", "--frames", "20",
                          "--record", str(record), "--out", str(session), "--timeout", "60"],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
                         start_new_session=True)
    deadline = time.time() + 30
    while not (session / "ticks.tsv").is_file() and time.time() < deadline:
        time.sleep(0.1)
    time.sleep(0.5)
    os.killpg(p.pid, signal.SIGINT)
    out, err = p.communicate(timeout=120)
    doc = json.loads(record.read_text())
    assert doc["ended_by"] == "interrupted", out + err
    assert doc["game"]["stopped_by"] == "SIGTERM"
    assert json.loads((session / "game.runrec").read_text())["sigint"] is False


def test_play_ends_when_the_window_closes(release, tmp_path):
    """Play (no --selftest): the user's overlay is the write root, the player is given the input
    FIFO, its end request (the window's close) ends the session through the game's SIGTERM, and
    --no-sound leaves the recorder's sound off."""
    home, _ = installed(release, tmp_path)
    session = tmp_path / "session"
    env = env_for(home, FAKE_GAME=json.dumps({"title_at": 30}), FAKE_PLAYER=json.dumps({"end_after": 1.0}))
    env["SA2_VFS_HIDE_INSTALL_SAVES"] = "1"      # exported by the user: play must strip it
    p = subprocess.run([str(release["rel"] / "bin" / "sa2recomp"), "--no-sound", "--no-pace",
                        "--out", str(session)], capture_output=True, text=True, env=env, timeout=300)
    assert p.returncode == 0, p.stdout + p.stderr
    doc = json.loads((session / "launch.runrec").read_text())
    assert doc["mode"] == "play" and doc["ended_by"] == "the window was closed"
    assert doc["game"]["stopped_by"] == "SIGTERM" and doc["launched"] is False
    # Installed already: no first run; Linux has no console to give up.
    assert doc["first_run"] == {"ran": False} and doc["console"] == ""
    got = json.loads((session / "game-args.runrec").read_text())
    assert got["env"]["SA2_VFS_WRITE_ROOT"] == str(home / ".local/share/sa2recomp/overlay")
    assert "SA2_REC_AUDIO" not in got["env"]
    # Play reads the player's saves wherever they are; only the selftest hides the folder's.
    assert "SA2_VFS_HIDE_INSTALL_SAVES" not in got["env"]
    report = json.loads((session / "player-report.runrec").read_text())
    assert report["argv"][report["argv"].index("--input-path") + 1] == str(session / "input.fifo")
    assert "--audio" not in report["argv"]


def test_play_paces_plays_sound_and_hands_the_player_its_extra_args(release, tmp_path):
    """Play's defaults: paced to real time (--pace, with its log) and the sound on SDL's default
    device (--audio, no SDL_AUDIO_DRIVER chosen for it); --player-arg is appended to the player's
    command line, in order, and the record names it."""
    home, _ = installed(release, tmp_path)
    session = tmp_path / "session"
    env = env_for(home, FAKE_GAME=json.dumps({"title_at": 30}), FAKE_PLAYER=json.dumps({"end_after": 1.0}))
    p = subprocess.run([str(release["rel"] / "bin" / "sa2recomp"), "--out", str(session),
                        "--player-arg", "--synthetic-keys", "--player-arg", "keys.tsv"],
                       capture_output=True, text=True, env=env, timeout=300)
    assert p.returncode == 0, p.stdout + p.stderr
    doc = json.loads((session / "launch.runrec").read_text())
    assert doc["paced"] is True and doc["sound"] is True
    assert doc["player"]["extra_args"] == ["--synthetic-keys", "keys.tsv"]
    report = json.loads((session / "player-report.runrec").read_text())
    argv = report["argv"]
    assert argv[-2:] == ["--synthetic-keys", "keys.tsv"]
    assert "--audio" in argv and argv[argv.index("--pace-log") + 1] == str(session / "pace.tsv")
    got = json.loads((session / "game-args.runrec").read_text())
    assert got["env"]["SA2_REC_AUDIO"] == "1"


def test_play_turns_the_audio_store_watch_off_unless_game_env_names_it(release, tmp_path):
    """Play's recorder skips the DirectSound store watch (SA2_REC_AUDIO_WATCH=0,
    host/sa2_present_rec.c) and hashes one closed store in 64 (SA2_REC_STORE_HASH_EVERY=64, the
    live player's kLiveStoreCheckEvery) unless share/game.env sets them; the selftest keeps the
    watch and every hash."""
    home, _ = installed(release, tmp_path)
    env = env_for(home, FAKE_GAME=json.dumps({"title_at": 30}), FAKE_PLAYER=json.dumps({"end_after": 1.0}))

    def play(name: str) -> dict:
        session = tmp_path / name
        p = subprocess.run([str(release["rel"] / "bin" / "sa2recomp"), "--no-pace", "--out", str(session)],
                           capture_output=True, text=True, env=env, timeout=300)
        assert p.returncode == 0, p.stdout + p.stderr
        return json.loads((session / "game-args.runrec").read_text())["env"]

    got = play("play")
    assert (got["SA2_REC_AUDIO_WATCH"], got["SA2_REC_STORE_HASH_EVERY"]) == ("0", "64")
    game_env = release["rel"] / "share" / "game.env"
    text = game_env.read_text()
    try:
        game_env.write_text(text + "SA2_REC_AUDIO_WATCH=1\nSA2_REC_STORE_HASH_EVERY=1\n")
        got = play("play-watched")
        assert (got["SA2_REC_AUDIO_WATCH"], got["SA2_REC_STORE_HASH_EVERY"]) == ("1", "1")
    finally:
        game_env.write_text(text)
    p, doc, session = selftest(release, home, tmp_path, name="selftest")
    assert p.returncode == 0, p.stdout + p.stderr
    got = json.loads((session / "game-args.runrec").read_text())["env"]
    assert "SA2_REC_AUDIO_WATCH" not in got and "SA2_REC_STORE_HASH_EVERY" not in got


def test_play_sets_the_full_width_answer_only_where_asked(release, tmp_path):
    """Play's full width (runtime/sa2_widescreen.c): --play-res WxH sets SA2_PLAY_RES over game.env's
    own; --no-widescreen sets it off and drops game.env's SA2_PLAY_ASPECT; game.env's own setting is
    kept as written; with no display (no DISPLAY or WAYLAND_DISPLAY, or SDL's offscreen driver)
    nothing is set; the selftest sets nothing. Each says which, and the record keeps it."""
    home, _ = installed(release, tmp_path)
    env = env_for(home, FAKE_GAME=json.dumps({"title_at": 30}), FAKE_PLAYER=json.dumps({"end_after": 1.0}))

    def play(name: str, *args: str, environ: dict | None = None, hud43: str = "1") -> tuple[dict, str, str]:
        session = tmp_path / name
        p = subprocess.run([str(release["rel"] / "bin" / "sa2recomp"), "--no-pace", "--out", str(session), *args],
                           capture_output=True, text=True, env=environ or env, timeout=300)
        assert p.returncode == 0, p.stdout + p.stderr
        got = json.loads((session / "game-args.runrec").read_text())["env"]
        doc = json.loads((session / "launch.runrec").read_text())
        # The HUD at 4:3 (SA2_PLAY_HUD43) is on in every play session unless game.env sets it.
        assert got.get("SA2_PLAY_HUD43") == hud43
        return ({k: v for k, v in got.items() if k.startswith("SA2_PLAY_") and k != "SA2_PLAY_HUD43"},
                doc["widescreen"], p.stderr)

    got, said, err = play("none")
    assert got == {} and said == "the config's own size: there is no display (DISPLAY and WAYLAND_DISPLAY are unset)"
    assert f"sa2recomp: widescreen: {said}" in err
    got, said, _ = play("offscreen", "--video-driver", "offscreen", environ=dict(env, DISPLAY=":99"))
    assert got == {} and said == "the config's own size: SDL's offscreen driver has no display"
    got, said, _ = play("res", "--play-res", "2340x1080")
    assert got == {"SA2_PLAY_RES": "2340x1080"} and said == "SA2_PLAY_RES=2340x1080 (--play-res)"
    got, said, _ = play("off", "--no-widescreen")
    assert got == {"SA2_PLAY_RES": "off"} and said == "off (--no-widescreen)"
    game_env = release["rel"] / "share" / "game.env"
    text = game_env.read_text()
    try:
        game_env.write_text(text + "SA2_PLAY_ASPECT=21:9\n")
        got, said, _ = play("game-env")
        assert got == {"SA2_PLAY_ASPECT": "21:9"} and said == "game.env's own SA2_PLAY_ASPECT=21:9"
        got, said, _ = play("game-env-off", "--no-widescreen")
        assert got == {"SA2_PLAY_RES": "off"}
        got, said, _ = play("game-env-res", "--play-res", "1560x720")
        assert got == {"SA2_PLAY_RES": "1560x720"}
        game_env.write_text(text + "SA2_PLAY_HUD43=0\n")
        got, said, _ = play("game-env-hud43-off", "--play-res", "2340x1080", hud43="0")
        assert got == {"SA2_PLAY_RES": "2340x1080"}
    finally:
        game_env.write_text(text)
    p, doc, session = selftest(release, home, tmp_path, name="selftest", extra_env={"DISPLAY": ":99"})
    assert p.returncode == 0, p.stdout + p.stderr
    got = json.loads((session / "game-args.runrec").read_text())["env"]
    assert not [k for k in got if k.startswith("SA2_PLAY_")] and doc["widescreen"] == ""


def test_play_passes_game_envs_tweaks_and_the_selftest_drops_them(release, tmp_path):
    """Tweaks (runtime/sa2_tweaks.c): game.env's SA2_TWEAKS line reaches play's game as written, and
    its player too (host/player/tweaks.h, the host's tweaks), and the caller's own SA2_TWEAKS
    reaches neither; with none they get none; the selftest drops game.env's for both. Each says
    which, and the record keeps it (`tweaks`)."""
    home, _ = installed(release, tmp_path)
    env = env_for(home, FAKE_GAME=json.dumps({"title_at": 30}), FAKE_PLAYER=json.dumps({"end_after": 1.0}),
                  SA2_TWEAKS="from-the-shell")

    def play(name: str, *args: str, **more: str) -> tuple[str | None, str, str]:
        session = tmp_path / name
        p = subprocess.run([str(release["rel"] / "bin" / "sa2recomp"), "--no-pace", "--out", str(session), *args],
                           capture_output=True, text=True, env=dict(env, **more), timeout=300)
        assert p.returncode == 0, p.stdout + p.stderr
        got = json.loads((session / "game-args.runrec").read_text())["env"]
        report = json.loads((session / "player-report.runrec").read_text())
        assert report["sa2_tweaks"] == got.get("SA2_TWEAKS"), (report["sa2_tweaks"], got.get("SA2_TWEAKS"))
        # The player is given the game's replay settings beside its tweaks, and else none.
        replay = {k: v for k, v in got.items() if k in ("SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS")}
        assert report["sa2_replay"] == (replay if got.get("SA2_TWEAKS") else {}), (report["sa2_replay"], replay)
        return got.get("SA2_TWEAKS"), json.loads((session / "launch.runrec").read_text())["tweaks"], p.stderr

    got, said, err = play("none")
    assert got is None and said == "none" and "sa2recomp: tweaks: none\n" in err
    assert "sa2 host tweaks" not in err
    game_env = release["rel"] / "share" / "game.env"
    text = game_env.read_text()
    try:
        game_env.write_text(text + "SA2_TWEAKS=no-event-bars\n")
        got, said, err = play("game-env")
        assert got == "no-event-bars" and said == "SA2_TWEAKS=no-event-bars (game.env)"
        assert "sa2recomp: tweaks: SA2_TWEAKS=no-event-bars (game.env)\n" in err
        # The launcher's line whenever the value is set: no launcher tweak is registered, so none on.
        assert "sa2recomp: sa2 host tweaks (launcher): none on (SA2_TWEAKS=no-event-bars)\n" in err
        # The caller's own replay setting reaches neither the game nor the player, and leaves the
        # launcher's tweaks as they are: the game, the player and the launcher keep theirs.
        got, said, err = play("shell-replay", SA2_REPLAY_TICKS="/dev/null")
        assert got == "no-event-bars" and said == "SA2_TWEAKS=no-event-bars (game.env)"
        assert "sa2recomp: sa2 host tweaks (launcher): none on (SA2_TWEAKS=no-event-bars)\n" in err
        # A synthetic-input session (host/launch/windows_play.py's) is a test run: no tweaks for any.
        for flag in ("--synthetic-keys", "--synthetic-input"):
            got, said, err = play("synthetic" + flag, "--player-arg", flag, "--player-arg", "keys.tsv")
            assert got is None and said == (f"none: a synthetic-input session (--player-arg {flag}) drops game.env's "
                                            "SA2_TWEAKS=no-event-bars"), said
            assert f"sa2recomp: tweaks: {said}\n" in err and "sa2 host tweaks" not in err
        # A replay setting in game.env: the game ignores its tweaks (runtime/sa2_tweaks.c), and so
        # does the launcher, saying so, and the player is given the setting to say the same.
        # Each of the two settings, and both; play() checks the player is given those set.
        for name, lines in (("ticks", "SA2_REPLAY_TICKS=/dev/null\n"), ("words", "SA2_REPLAY_WORDS=/dev/null\n"),
                            ("both", "SA2_REPLAY_TICKS=/dev/null\nSA2_REPLAY_WORDS=/dev/null\n")):
            game_env.write_text(text + "SA2_TWEAKS=no-event-bars\n" + lines)
            got, said, err = play("game-env-replay-" + name)
            assert got == "no-event-bars" and said == "SA2_TWEAKS=no-event-bars (game.env)"
            assert ("sa2recomp: sa2 host tweaks (launcher): SA2_TWEAKS=no-event-bars ignored in a replay "
                    "(SA2_REPLAY_TICKS or SA2_REPLAY_WORDS is set)\n") in err
        game_env.write_text(text + "SA2_TWEAKS=no-event-bars\n")
        p, doc, session = selftest(release, home, tmp_path, name="selftest")
        assert p.returncode == 0, p.stdout + p.stderr
        assert "SA2_TWEAKS" not in json.loads((session / "game-args.runrec").read_text())["env"]
        assert json.loads((session / "player-report.runrec").read_text())["sa2_tweaks"] is None
        assert doc["tweaks"] == "none: the selftest drops game.env's SA2_TWEAKS=no-event-bars"
    finally:
        game_env.write_text(text)


def test_a_launcher_tweaks_lines_are_kept_in_launcher_log(release, tmp_path):
    """A launcher tweak (host/launch/tweaks) says its lines in play's launcher.log too, for a launch
    with no terminal (a Windows double-click): speedrun-timer on, the synthetic game holds no guest
    image, so its watch fails by name, and that name is in the log."""
    home, _ = installed(release, tmp_path)
    env = env_for(home, FAKE_GAME=json.dumps({"title_at": 30}), FAKE_PLAYER=json.dumps({"end_after": 1.0}))
    game_env = release["rel"] / "share" / "game.env"
    text = game_env.read_text()
    session = tmp_path / "session"
    try:
        game_env.write_text(text + "SA2_TWEAKS=speedrun-timer\n")
        p = subprocess.run([str(release["rel"] / "bin" / "sa2recomp"), "--no-pace", "--out", str(session)],
                           capture_output=True, text=True, env=env, timeout=300)
    finally:
        game_env.write_text(text)
    assert p.returncode == 0, p.stdout + p.stderr
    head = "sa2recomp: sa2 host tweaks: speedrun timer published: "
    said = [x for x in p.stderr.splitlines() if x.startswith(head)]
    log = (session / "launcher.log").read_text().splitlines()
    assert [x for x in log if x.startswith(head)] == said and len(said) >= 4
    assert said[0].startswith(head + "UDP datagrams to 127.0.0.1:16838")
    assert said[-1].startswith(head + "the game's guest memory was never found")
    assert log[-1] == said[-1]           # the watch's end comes before the log is written


def test_sdl_disk_sound_is_refused_in_play(release, tmp_path):
    home, _ = installed(release, tmp_path)
    p = subprocess.run([str(release["rel"] / "bin" / "sa2recomp")], capture_output=True, text=True,
                       env=env_for(home, SDL_AUDIO_DRIVER="disk"))
    assert p.returncode == 2 and "SDL_AUDIO_DRIVER=disk" in p.stderr


def test_a_users_config_ini_is_a_user_file(release, tmp_path):
    home, _ = installed(release, tmp_path)
    p, doc, _ = selftest(release, home, tmp_path,
                         fake={"title_at": 30, "opened": ["Config/StageSelectConfig.ini"]})
    assert p.returncode == 0, p.stdout + p.stderr
    assert doc["launched"] is True


def test_an_enter_the_game_never_reads_fails(release, tmp_path):
    home, _ = installed(release, tmp_path)
    p, doc, _ = selftest(release, home, tmp_path, "--timeout", "15",
                         fake={"title_at": 30, "ignore_keys": True})
    assert p.returncode == 1
    assert "the game never read the Enter of seq 1" in doc["failures"]
    assert doc["ended_by"] == "selftest: timed out"


def test_the_title_never_reached_fails(release, tmp_path):
    home, _ = installed(release, tmp_path)
    p, doc, _ = selftest(release, home, tmp_path, "--timeout", "8", fake={})
    assert p.returncode == 1
    assert "the title was never held for 20 frames" in doc["failures"]


# ------------------------------------------------------------------ hosts and usage

def test_host_picks_the_player(release, tmp_path):
    home, _ = installed(release, tmp_path)
    p, doc, session = selftest(release, home, tmp_path, "--host", "dxvk-native", "--video-driver", "dummy")
    assert p.returncode == 0, p.stdout + p.stderr
    assert doc["host"] == "dxvk-native" and doc["player"]["window"] == "none (dummy)"
    report = json.loads((session / "player-report.runrec").read_text())
    assert report["name"] == "sa2_player"


@pytest.mark.parametrize("args, message", [
    (["--selftest", "--record", "x.json"], "a record is a .runrec"),
    (["--host", "metal"], "the hosts are sdl3-gpu and dxvk-native"),
    (["--frames", "5"], "--record and --frames belong to --selftest"),
    (["--bogus"], "unknown argument --bogus"),
    (["--selftest", "--host", "dxvk-native"], "DXVK Native cannot present under SDL's offscreen driver"),
    (["--selftest", "--player-arg", "--max-frames"], "--player-arg belongs to play"),
    (["--selftest", "--no-widescreen"], "--no-widescreen and --play-res belong to play"),
    (["--selftest", "--play-res", "2340x1080"], "--no-widescreen and --play-res belong to play"),
    (["--play-res", "2340"], "--play-res 2340: a size WxH"),
    (["--play-res", "100x100"], "--play-res 100x100: a size WxH"),
    (["--play-res", "2340x1080", "--no-widescreen"], "not with --no-widescreen"),
])
def test_usage_errors(release, tmp_path, args, message):
    home = tmp_path / "home"
    home.mkdir()
    p = subprocess.run([str(release["rel"] / "bin" / "sa2recomp"), *args], capture_output=True,
                       text=True, env=env_for(home))
    assert p.returncode == 2 and message in p.stderr


def test_the_launcher_compiles_for_windows(release, tmp_path):
    """The Windows leg's build: the launcher and its platform layer (the named-pipe session layer,
    sa2_launch_win32.cpp) compile for x86_64-windows-gnu with zig (clang 20)."""
    zig = shutil.which("zig") or str(next(iter(sorted((ROOT / "private" / "predecessor-tools").glob(
        "zig*/zig"))), ""))
    assert zig and Path(zig).exists(), "zig is neither on PATH nor in private/predecessor-tools (source tools/env.sh)"
    out = tmp_path / "sa2recomp.exe"
    table = release["work"] / "table" / "sa2_install_table.cpp"
    env = dict(os.environ, PATH=str(Path(zig).parent) + ":" + os.environ.get("PATH", ""),
               ZIG_GLOBAL_CACHE_DIR=str(tmp_path / "zg"), ZIG_LOCAL_CACHE_DIR=str(tmp_path / "zl"))
    b = subprocess.run(["bash", str(HOST / "launch" / "build.sh"), "--cxx", "zig", "--target",
                        "x86_64-windows-gnu", "--table", str(table), "--out", str(out)],
                       capture_output=True, text=True, env=env)
    assert b.returncode == 0, b.stdout + b.stderr
    assert out.read_bytes()[:2] == b"MZ"


# ------------------------------------------------------------------ the fresh-host runner

FHR = load_module(HOST / "launch" / "fresh_host_routes.py", "fresh_host_routes_under_test")


def binds_of(argv: list[str], flag: str) -> list[tuple[str, str]]:
    return [(argv[i + 1], argv[i + 2]) for i, a in enumerate(argv) if a == flag]


@pytest.mark.parametrize("route", FHR.ROUTES)
def test_each_routes_sandbox_binds_only_its_source_read_only(tmp_path, route):
    home, steam = Path("/home/u"), Path("/home/u/.local/share/Steam")
    game = steam / "steamapps/common/Sonic Adventure 2"
    zip_path, data, release, out = tmp_path / "g.zip", tmp_path / "data", tmp_path / "rel", tmp_path / "out"
    plan = FHR.route_plan(route, home, steam, zip_path, 300, data)
    argv = FHR.bwrap_argv(home, plan["binds"], release, out, "u", "true", plan["rw_binds"])
    assert argv[:6] == ["bwrap", "--unshare-all", "--die-with-parent", "--new-session", "--clearenv",
                        "--setenv"]
    assert ("--tmpfs", str(home)) in zip(argv, argv[1:])
    ro = binds_of(argv, "--ro-bind")
    rw = binds_of(argv, "--bind")
    assert (str(release), FHR.RELEASE_IN) in ro
    for src, dst in plan["binds"]:
        assert (str(src), str(dst)) in ro
    install = plan["steps"][0][1]
    if route == "zip":
        at = home / "Downloads" / "Sonic Adventure 2.zip"
        assert plan["binds"] == [(zip_path, at)]
        assert rw == [(str(data), str(home / ".local/share/sa2recomp")), (str(out), FHR.OUT_IN)]
        assert str(steam) in plan["hidden"] and install[1:5] == ["--source", "zip", str(at), "--yes"]
        assert plan["probe"] == str(at)
    else:
        apps = steam / "steamapps"
        assert plan["binds"] == [(p, p) for p in (apps / "libraryfolders.vdf", apps / "appmanifest_213610.acf", game)]
        assert rw == [(str(out), FHR.OUT_IN)]
        assert install[1:4] == (["--source", "steam", "--yes"] if route == "steam-autodiscovery"
                                else ["--source", "folder", str(game)])
        assert plan["probe"] == str(game / "probe")
    hidden = set(plan["hidden"])
    assert {str(FHR.CHECKOUT), str(home / ".steam"), str(steam / "config"),
            str(steam / "steamapps/compatdata"), str(steam / "steamapps/shadercache")} <= hidden
    assert plan["steps"][1][1] == [f"{FHR.RELEASE_IN}/bin/sa2recomp", "--selftest", "--frames", "300",
                                   "--record", f"/out/{route}-launch.runrec", "--out", f"/out/{route}-session"]


@pytest.mark.parametrize("route", FHR.ROUTES)
def test_hide_user_config_empties_the_steam_games_config_in_the_sandbox_alone(tmp_path, route):
    """--hide-user-config: a game folder never launched. An empty tmpfs, remounted read only, over
    the Steam game's Config/, after the game's own read-only bind; the zip route is left as it is."""
    home, steam = Path("/home/u"), Path("/home/u/.local/share/Steam")
    config = steam / "steamapps/common/Sonic Adventure 2/Config"
    plan = FHR.route_plan(route, home, steam, tmp_path / "g.zip", 300, tmp_path / "data", True)
    argv = FHR.bwrap_argv(home, plan["binds"], tmp_path / "rel", tmp_path / "out", "u", "true",
                          plan["rw_binds"], plan["empty_ro"])
    script = FHR.route_script(home, plan)
    if route == "zip":
        assert plan["empty_ro"] == [] and "--remount-ro" not in argv and "NOT_EMPTY" not in script
        return
    assert plan["empty_ro"] == [config]
    t = argv.index("--tmpfs", argv.index(str(config.parent)))
    assert argv[t:t + 4] == ["--tmpfs", str(config), "--remount-ro", str(config)]
    assert f"echo 'NOT_EMPTY {config}'; exit 94" in script and script.index("NOT_EMPTY") < script.index("SANDBOX_CLEAN")


def test_hide_user_config_reads_empty_and_read_only_and_leaves_the_folder_alone(tmp_path):
    game = tmp_path / "game"
    (game / "Config").mkdir(parents=True)
    (game / "Config/UserConfig.cfg").write_bytes(b"<Configs />")
    home, release, out = tmp_path / "home", tmp_path / "rel", tmp_path / "out"
    release.mkdir()
    out.mkdir()
    plan = {"hidden": [], "steps": [], "probe": str(game / "Config" / "probe"), "empty_ro": [game / "Config"]}
    p = subprocess.run(FHR.bwrap_argv(home, [(game, game)], release, out, "u", FHR.route_script(home, plan),
                                      [], plan["empty_ro"]), capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stdout + p.stderr
    assert f"EMPTY {game / 'Config'}" in p.stdout.splitlines() and "WRITE_PROBE refused" in p.stdout
    assert sorted(x.name for x in (game / "Config").iterdir()) == ["UserConfig.cfg"]


def sandbox(tmp_path: Path, plan: dict, binds=(), rw_binds=()) -> subprocess.CompletedProcess:
    home = tmp_path / "home"
    release, out = tmp_path / "rel", tmp_path / "out"
    release.mkdir(exist_ok=True)
    out.mkdir(exist_ok=True)
    script = FHR.route_script(home, plan)
    return subprocess.run(FHR.bwrap_argv(home, list(binds), release, out, "u", script, list(rw_binds)),
                          capture_output=True, text=True, timeout=60)


def test_the_sandbox_stops_on_anything_it_must_not_see(tmp_path):
    seen = tmp_path / "checkout"
    seen.mkdir()
    p = sandbox(tmp_path, {"hidden": [str(seen), str(tmp_path / "absent")], "steps": [],
                           "probe": str(tmp_path / "x" / "probe")}, binds=[(seen, seen)])
    assert p.returncode == 90, p.stdout + p.stderr
    assert f"VISIBLE {seen}" in p.stdout and "SANDBOX_LEAK" in p.stdout and "SANDBOX_CLEAN" not in p.stdout
    assert f"VISIBLE {tmp_path / 'absent'}" not in p.stdout


@pytest.mark.parametrize("bind, verdict", [("ro", "refused"), ("rw", "wrote"), (None, "no-such-folder")])
def test_the_write_probe_tells_a_read_only_source_from_a_missing_one(tmp_path, bind, verdict):
    game = tmp_path / "game"
    game.mkdir()
    plan = {"hidden": [], "steps": [], "probe": str(game / "probe")}
    p = sandbox(tmp_path, plan, binds=[(game, game)] if bind == "ro" else [],
                rw_binds=[(game, game)] if bind == "rw" else [])
    assert p.returncode == 0, p.stdout + p.stderr
    assert f"WRITE_PROBE {verdict}" in p.stdout.splitlines()


# ------------------------------------------------------------------ the release's builder

RB = load_module(ROOT / "tools" / "s7" / "release_build.py", "release_build_under_test")


def test_set_rpath_points_the_loader_at_the_releases_lib(tmp_path):
    src = tmp_path / "m.c"
    src.write_text("int main(void) { return 7; }\n")
    long_rpath = "/home/builder/a/long/build/machine/path/install/lib"
    for name, tags in (("rpath", "--disable-new-dtags"), ("runpath", "--enable-new-dtags")):
        b = subprocess.run(["gcc", str(src), "-o", str(tmp_path / name), f"-Wl,-rpath,{long_rpath}",
                            f"-Wl,{tags}"], capture_output=True, text=True)
        assert b.returncode == 0, b.stderr
    assert RB.set_rpath(tmp_path / "rpath") == long_rpath
    shown = subprocess.run(["readelf", "-d", str(tmp_path / "rpath")], capture_output=True, text=True).stdout
    assert f"Library rpath: [{RB.RPATH}]" in shown and long_rpath not in shown
    assert subprocess.run([str(tmp_path / "rpath")]).returncode == 7
    with pytest.raises(SystemExit, match="has a DT_RUNPATH"):
        RB.set_rpath(tmp_path / "runpath")
    with pytest.raises(SystemExit, match="is longer than"):
        RB.set_rpath(tmp_path / "rpath", "$ORIGIN/" + "x" * 80)


def test_the_observer_writes_the_rows_the_launcher_reads(tmp_path):
    text = RB.observer_source(ROOT, {"game_mode": (0x0174B040, 4), "current_menu": (0x01934BE0, 4)})
    probe = (ROOT / "harness" / "replay" / "wrap_tick_probe.c").resolve()
    assert f'#include "{probe}"' in text
    assert 'getenv("SA2_LAUNCH_TICKS")' in text
    assert "T\\ttick\\tpresents\\tgame_mode\\tcurrent_menu\\n" in text
    assert "tp_read(0x0174B040u, 4), tp_read(0x01934BE0u, 4)" in text
    assert "void sa2_dinput_host_records_read(uint32_t poll" in text and '"K\\t%lu' in text


def test_the_manifest_names_its_sources_by_path_and_hash(tmp_path):
    src = tmp_path / "src"
    for rel, body in (("host/launch/sa2_launch.cpp", "a"), ("runtime/sa2_files.c", "b"),
                      ("host/launch/notes.txt", "c"), ("harness/crt_probe.c", "d")):
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_text(body)
    got = RB.sources(src)
    assert got == {p: hashlib.sha256(b.encode()).hexdigest() for p, b in
                   (("harness/crt_probe.c", "d"), ("host/launch/sa2_launch.cpp", "a"), ("runtime/sa2_files.c", "b"))}
    assert list(got) == sorted(got)


def test_windows_gets_a_strong_observer_and_a_recorder_with_o_cloexec(tmp_path):
    words = {"game_mode": (0x0174B040, 4), "current_menu": (0x01934BE0, 4)}
    linux, windows = RB.observer_source(ROOT, words), RB.observer_source(ROOT, words, strong=True)
    assert RB.STRONG_OBSERVER_HEAD not in linux and RB.STRONG_OBSERVER_TAIL not in linux
    assert windows.startswith(RB.STRONG_OBSERVER_HEAD)
    assert windows.replace(RB.STRONG_OBSERVER_HEAD, "", 1).replace(RB.STRONG_OBSERVER_TAIL, "") == linux
    rec = RB.recorder_source(ROOT)
    assert "#define O_CLOEXEC _O_NOINHERIT" in rec
    assert f'#include "{(ROOT / "host" / "sa2_present_rec.c").resolve()}"' in rec
    # The mechanism, compiled for the Windows triple: a header's weak declaration makes the
    # definition weak (".weak" in the assembly), and the head and tail make it a strong one.
    zig = shutil.which("zig") or str(next(iter(sorted((ROOT / "private" / "predecessor-tools").glob(
        "zig*/zig"))), ""))
    assert zig and Path(zig).exists(), "zig is neither on PATH nor in private/predecessor-tools"
    (tmp_path / "sa2_dinput.h").write_text(
        "#include <stdint.h>\nvoid sa2_dinput_host_records_read(uint32_t poll, uint32_t first_seq,\n"
        "    uint32_t last_seq, uint32_t records) __attribute__((weak));\n")
    body = ("void sa2_dinput_host_records_read(uint32_t poll, uint32_t first_seq, uint32_t last_seq,\n"
            "                                  uint32_t records) { (void)poll; (void)first_seq;"
            " (void)last_seq; (void)records; }\n")
    env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(tmp_path / "zg"), ZIG_LOCAL_CACHE_DIR=str(tmp_path / "zl"))
    asm = {}
    for name, head, tail in (("weak", "", ""), ("strong", RB.STRONG_OBSERVER_HEAD, RB.STRONG_OBSERVER_TAIL)):
        (tmp_path / f"{name}.c").write_text(head + '#include "sa2_dinput.h"\n' + tail + body)
        b = subprocess.run([zig, "cc", "-target", "x86_64-windows-gnu", "-O2", "-S", "-I", str(tmp_path),
                            str(tmp_path / f"{name}.c"), "-o", str(tmp_path / f"{name}.s")],
                           capture_output=True, text=True, env=env)
        assert b.returncode == 0, b.stderr
        asm[name] = (tmp_path / f"{name}.s").read_text()
    assert ".weak\tsa2_dinput_host_records_read" in asm["weak"]
    assert ".weak" not in asm["strong"] and "sa2_dinput_host_records_read:" in asm["strong"]


WR = load_module(HOST / "launch" / "windows_routes.py", "windows_routes_under_test")


@pytest.mark.parametrize("route", WR.ROUTES)
def test_wine_hide_user_config_empties_the_steam_games_config(tmp_path, route):
    home, steam = Path("/home/u"), Path("/home/u/.local/share/Steam")
    config = steam / "steamapps/common/Sonic Adventure 2/Config"
    plan = WR.wine_plan(route, home, steam, tmp_path / "g.zip", tmp_path / "prefix", 300, True)
    script = WR.wine_script(home, plan, WR.PROTON_AT / "bin" / "wine")
    argv = WR.bwrap_argv(home, plan, tmp_path / "rel", tmp_path / "out", "u", Path("/s/Proton/files/bin"), ":0",
                         ["/tmp/.X11-unix"], script)
    if route == "zip":
        assert plan["empty_ro"] == [] and "--remount-ro" not in argv
        return
    assert plan["empty_ro"] == [config]
    t = argv.index("--tmpfs", argv.index(str(config.parent)))
    assert argv[t:t + 4] == ["--tmpfs", str(config), "--remount-ro", str(config)]
    assert f"echo 'NOT_EMPTY {config}'; exit 94" in script and script.index("NOT_EMPTY") < script.index("SETUP prefix")
    assert WR.wine_plan(route, home, steam, tmp_path / "g.zip", tmp_path / "prefix", 300)["empty_ro"] == []


@pytest.mark.parametrize("route", WR.ROUTES)
def test_each_windows_route_under_wine_binds_only_its_source_in_a_new_prefix(tmp_path, route):
    home, steam = Path("/home/u"), Path("/home/u/.local/share/Steam")
    apps = steam / "steamapps"
    zip_path, prefix, release, out = tmp_path / "g.zip", tmp_path / "prefix", tmp_path / "rel", tmp_path / "out"
    plan = WR.wine_plan(route, home, steam, zip_path, prefix, 300)
    ro = {str(at): win for _src, at, win in plan["binds"]}
    if route == "zip":
        assert ro == {"/home/u/.wine/drive_c/users/steamuser/Downloads/Sonic Adventure 2.zip":
                      "C:/users/steamuser/Downloads/Sonic Adventure 2.zip"}
        assert str(apps) in plan["hidden"] and "s:" not in plan["drives"]
    else:
        assert ro == {str(apps / "libraryfolders.vdf"): "S:/steamapps/libraryfolders.vdf",
                      str(apps / "appmanifest_213610.acf"): "S:/steamapps/appmanifest_213610.acf",
                      str(apps / "common/Sonic Adventure 2"): "S:/steamapps/common/Sonic Adventure 2"}
        assert plan["drives"]["s:"] == str(steam)
    assert plan["drives"]["o:"] == "/out"
    for h in ("compatdata", "shadercache", "workshop", "downloading", "temp"):
        assert str(apps / h) in plan["hidden"]
    assert [r[:2] for r in plan["registry"]] == ([["HKCU\\Software\\Valve\\Steam", "SteamPath"],
                                                  ["HKLM\\SOFTWARE\\Valve\\Steam", "InstallPath"]]
                                                 if route == "steam-autodiscovery" else [])
    inst, launch = (argv for _n, argv, _e in plan["steps"])
    assert inst[0] == "C:/Program Files/sa2recomp/bin/sa2-install.exe" and inst[-2:] == ["--record", f"O:/{route}-install.runrec"]
    assert launch == ["C:/Program Files/sa2recomp/bin/sa2recomp.exe", "--selftest", "--frames", "300", "--record",
                      f"O:/{route}-launch.runrec", "--out", f"O:/{route}-session"]
    script = WR.wine_script(home, plan, WR.PROTON_AT / "bin" / "wine")
    assert "unlink /home/u/.wine/dosdevices/z:" in script and "'VISIBLE the Z: drive'" in script
    assert "PREFIX_NOT_NEW" in script and "env -u DISPLAY -u WAYLAND_DISPLAY" in script.split("wineboot")[0]
    argv = WR.bwrap_argv(home, plan, release, out, "u", Path("/s/Proton/files/bin"), ":0", ["/tmp/.X11-unix"], script)
    assert "--share-net" not in argv and "--unshare-all" in argv and "--clearenv" in argv
    assert binds_of(argv, "--bind") == [(str(prefix), "/home/u/.wine"), (str(out), "/out")]
    ro_binds = dict(binds_of(argv, "--ro-bind"))
    assert ro_binds[str(release)] == "/home/u/.wine/drive_c/Program Files/sa2recomp"
    assert ro_binds["/s/Proton/files"] == str(WR.PROTON_AT)
    assert ro_binds["/tmp/.X11-unix"] == "/tmp/.X11-unix"
    assert all(dst not in ("/home/u/.steam",) and "compatdata" not in dst for dst in ro_binds.values())


# ------------------------------------------------------------------ the Windows play runner

WP = load_module(HOST / "launch" / "windows_play.py", "windows_play_under_test")


def test_the_play_runners_key_script_presses_enter_then_closes_the_window():
    rows = WP.key_script(first=8, every=2.5, hold=0.15, last=13, close=20).splitlines()
    assert rows == ["8000 40 down", "8150 40 up", "10500 40 down", "10650 40 up", "13000 40 down",
                    "13150 40 up", "20000 0 close"]


def test_the_play_runners_steps_are_the_folder_install_then_play_with_the_players_keys():
    (n1, inst, e1), (n2, play, e2) = WP.play_steps("O:", "C:/Program Files/sa2recomp",
                                                   "S:/steamapps/common/Sonic Adventure 2", 1000000)
    assert (n1, e1, n2, e2) == ("install", 0, "play", 0)
    assert inst == ["C:/Program Files/sa2recomp/bin/sa2-install.exe", "--source", "folder",
                    "S:/steamapps/common/Sonic Adventure 2", "--yes", "--record", "O:/play-install.runrec"]
    assert play[:3] == ["C:/Program Files/sa2recomp/bin/sa2recomp.exe", "--out", "O:/play-session"]
    assert "--selftest" not in play
    extra = [play[i + 1] for i, a in enumerate(play) if a == "--player-arg"]
    assert extra == ["--synthetic-keys", "O:/keys.tsv", "--key-log", "O:/key-log.tsv", "--present-log",
                     "O:/present-log.tsv", "--max-frames", "1000000"]


def test_the_play_runner_binds_the_pulseaudio_socket(tmp_path, monkeypatch):
    sock = tmp_path / "pulse" / "native"
    sock.parent.mkdir()
    sock.write_text("")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert WP.pulse_binds() == ["--ro-bind", str(sock.parent), str(sock.parent), "--setenv", "PULSE_SERVER",
                                f"unix:{sock}", "--setenv", "XDG_RUNTIME_DIR", str(tmp_path)]
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "none"))
    assert WP.pulse_binds() == []


def write_play_session(out: Path, rows: list, ended_by: str = "the window was closed") -> None:
    s = out / WP.SESSION
    s.mkdir(parents=True)
    ticks = ["# sa2 launch observer v1", "T\ttick\tpresents\tgame_mode\tcurrent_menu"]
    ticks += [f"T\t{t}\t{p}\t{m}\t{c}" for t, p, m, c in rows] + ["K\t20\t3\t1\t2\t2"]
    (s / "ticks.tsv").write_text("\n".join(ticks) + "\n")
    (s / "input.log").write_text("3 9 0x1C\n")
    (s / "launch.runrec").write_text(json.dumps({"mode": "play", "ended_by": ended_by, "player": {"exit": 0},
                                                 "game": {"stopped_by": "SIGTERM"}}))
    (s / "player-report.runrec").write_text(json.dumps({"complete": True, "exit_status": 0,
                                                        "counts": {"presents": 900}}))


def test_the_play_runners_verdict_needs_a_stage_after_the_main_menu(tmp_path):
    good = [(1, 1, 0, 0), (10, 10, 14, 0), (20, 20, 14, 9), (30, 30, 14, 1), (40, 40, 5, 1)]
    write_play_session(tmp_path / "a", good)
    v = WP.verdict(tmp_path / "a")
    assert v["pass"] and v["stage_at"] == [40, 40, 5, 1] and v["main_menu_at"] == [30, 30, 14, 1]
    assert v["transitions"] == [[1, 1, 0, 0], [10, 10, 14, 0], [20, 20, 14, 9], [30, 30, 14, 1], [40, 40, 5, 1]]
    assert v["polls_with_records"] == 1 and v["enter_runs"] == 1 and v["presented"] == 900
    # A stage before the main menu (the title's demo) is no stage reached through the menus.
    write_play_session(tmp_path / "b", [(1, 1, 5, 0), (10, 10, 14, 0), (30, 30, 14, 1)])
    v = WP.verdict(tmp_path / "b")
    assert not v["pass"] and v["stage"] is False
    write_play_session(tmp_path / "c", good, ended_by="the player ended")
    assert WP.verdict(tmp_path / "c")["pass"] is False


# ------------------------------------------------------------------ the first-run runner

@pytest.mark.parametrize("scenario", FRP.SCENARIOS["linux"])
def test_the_first_run_sandbox_runs_the_launcher_alone_on_its_scenarios_source(tmp_path, scenario):
    """host/launch/first_run_play.py --runner linux: no sa2-install step before the launcher; the
    steam scenario binds the three Steam paths and nothing else, the others no Steam at all; the
    picker's zenity stand-in comes first on PATH, the no-picker's zenity is an empty file; the
    sound is SDL's dummy driver."""
    home, steam = Path("/home/u"), Path("/home/u/.local/share/Steam")
    plan = FRP.linux_plan(scenario, home, steam, "/opt/sa2recomp", "/out", 1000, False)
    launch = plan["launch"]
    assert launch[:3] == ["/opt/sa2recomp/bin/sa2recomp", "--out", "/out/play-session"]
    assert "--selftest" not in launch and launch[launch.index("--video-driver") + 1] == "offscreen"
    assert ("--player-arg" in launch) == (scenario in ("steam", "picker"))
    game = steam / "steamapps" / "common" / GAME_NAME
    if scenario == "steam":
        assert plan["binds"] == FHR.steam_binds(steam) and str(steam) not in plan["hidden"]
    else:
        assert str(steam) in plan["hidden"]
        assert plan["binds"] == ([(game, home / "Games" / GAME_NAME)] if scenario == "picker" else [])
    assert plan["env"]["SDL_AUDIO_DRIVER"] == "dummy"
    assert ("DISPLAY" in plan["env"]) == (scenario in ("picker", "no-picker"))
    assert plan["expect_exit"] == (0 if scenario in ("steam", "picker") else 4)
    argv = FRP.linux_argv(home, plan, tmp_path / "rel", tmp_path / "out", "true", False)
    assert argv[argv.index("PATH") + 1] == ("/opt/fakebin:/usr/bin:/bin" if scenario == "picker" else "/usr/bin:/bin")
    assert (("/dev/null", "/usr/bin/zenity") in binds_of(argv, "--ro-bind")) == (scenario == "no-picker")
    assert ((str(tmp_path / "out" / "fakebin"), "/opt/fakebin") in binds_of(argv, "--ro-bind")) == (scenario == "picker")
    assert argv[-3:-1] == ["/bin/sh", "-c"]


def test_the_first_run_runners_verdict_holds_each_scenario_to_its_outcome(tmp_path):
    """No picker: exit 4, nothing installed, and the line saying what to run; a play: the config's
    source kind, the first run in the launcher's record, and windows_play.py's verdict."""
    class A:
        runner, scenario = "linux", "no-picker"
    said = ("sa2recomp: Sonic Adventure 2 was not found in Steam. No folder picker could be shown (x). "
            "Install the game from a terminal:\n  /opt/sa2recomp/bin/sa2-install --source folder \"<f>\" --yes\n")
    ran = {"log": "SANDBOX_CLEAN\nEXIT play 4\n--- stderr\n" + said}
    assert FRP.verdict(A, tmp_path, ran)["pass"] is True
    assert FRP.verdict(A, tmp_path, {"log": "SANDBOX_CLEAN\nEXIT play 4\n"})["pass"] is False
    (tmp_path / "sa2recomp.cfg").write_text("source.kind=folder\n")
    assert FRP.verdict(A, tmp_path, ran)["pass"] is False          # something was installed
    A.scenario = "steam"
    (tmp_path / "sa2recomp.cfg").write_text("source.kind=steam\n")
    s = tmp_path / "play-session"
    write_play_session(tmp_path, [[10, 10, 14, 0], [50, 50, 14, 1], [90, 90, 5, 5]])
    launch = json.loads((s / "launch.runrec").read_text())
    launch["first_run"] = {"ran": True}
    (s / "launch.runrec").write_text(json.dumps(launch))
    v = FRP.verdict(A, tmp_path, {"log": "SANDBOX_CLEAN\nEXIT play 0\n"})
    assert v["pass"] is True, v
    launch["first_run"] = {"ran": False}                           # installed beforehand: not a first run
    (s / "launch.runrec").write_text(json.dumps(launch))
    assert FRP.verdict(A, tmp_path, {"log": "SANDBOX_CLEAN\nEXIT play 0\n"})["pass"] is False
