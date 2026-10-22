#!/usr/bin/env python3
"""Replay a Debug Export headlessly and check it against the session it came from.

A Debug Export (`sa2recomp --debug-export`, or the Android app's settings screen, Debug section;
host/launch/sa2_debug_export.h) holds a session's inputs and the state it started from:

  session/start-saves/, session/start-config/  the saves and Config/ the game saw at its start
                                               (host/launch/sa2_session_files.h)
  session/input.log                            the keyboard polls (SA2_INPUT_DI_LOG)
  session/pad.log                              the pads by XInput call (SA2_INPUT_PAD_LOG)
  session/game-env.txt                         the settings the game ran with
  session/ticks.tsv                            the game mode and menu at every game loop

This tool runs the Linux release's game binary on them with no player and no window:

  1. a fresh write root (WORK/overlay) seeded from start-saves/ and start-config/, with the game
     folder's own saves hidden (SA2_VFS_HIDE_INSTALL_SAVES=1), so the game sees the session's
     saves whatever this machine's game folder holds;
  2. the release's share/game.env, then the session's own settings that shape what the game does
     (CARRY: the picture size and HUD, and the guest command line "-r" that gives a pad to
     player 1, runtime/sa2_dinput.h "InputWay"), then --env overrides;
  3. the keyboard from input.log (SA2_INPUT_DI_SCRIPT) and the pads from pad.log
     (SA2_INPUT_PAD_SCRIPT), both keyed to the game's own counts, so the replay needs no clock;
     the replay logs both again (WORK/input.log, WORK/pad.log) for the comparison;
  4. the stream recorded to WORK/stream.bin (SA2_REC_TRACE; --no-trace leaves it unset, which
     records nothing: the stream is gigabytes for a long session);
  5. the game stopped (SIGTERM, then SIGKILL, to its own PID) once it has passed the session's
     last tick, or at --timeout.

Then it compares the game-mode transitions (each tick where game_mode or current_menu changes,
in the session's ticks.tsv and the replay's) up to the session's last tick, and the replay's pad
and keyboard logs with the session's, and says the first difference. Exit 0 when the transitions
match, 1 when they differ, 2 for a usage error or an export it cannot replay.

    tools/s7/replay_export.py EXPORT.zip [--work DIR] [--release DIR] [--install DIR] [--exe FILE]
                                [--no-trace] [--timeout S] [--env K=V ...] [--dry-run]

The game folder and executable default to the installer's config
($XDG_CONFIG_HOME/sa2recomp/sa2recomp.cfg: read_root, exe); the release to
private/generated/s7/release/linux-x86_64; WORK to private/replays/<session>. A session from
an Android device replays on the Linux release: the game's code is the same lifted image, and the
settings that differ by platform (thread placement, the recorder's sampling) do not shape what
the game does. Nothing outside WORK is written.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAX_ENTRIES = 20000
MAX_UNPACKED = 4 << 30
DEFAULT_RELEASE = ROOT / "private/generated/s7/release/linux-x86_64"
SAVE_DIR = "resource/gd_PC/SAVEDATA"

# The session's settings a replay takes over: the ones that change what the game computes or
# shows (runtime/sa2_widescreen.c's picture size and HUD, the guest command line). Everything
# else in game-env.txt is the session's plumbing (paths, FIFOs) or the recorder's and the
# runner's tuning, which the replay sets for itself or does not need.
CARRY_PREFIXES = ("SA2_PLAY_",)
CARRY_KEYS = {"SA2_GUEST_CMDLINE"}
# Set by this tool; a --env naming one is refused.
OWN_KEYS = {"SA2_INSTALL", "SA2_VFS_WRITE_ROOT", "SA2_EXE_KIND", "SA2_INPUT_DI_SCRIPT", "SA2_INPUT_DI_LOG",
            "SA2_INPUT_PAD_SCRIPT", "SA2_INPUT_PAD_LOG", "SA2_LAUNCH_TICKS", "SA2_REC_TRACE",
            "SA2_VFS_HIDE_INSTALL_SAVES"}


class Refused(Exception):
    pass


# ---------------------------------------------------------------- the export

def extract(zip_path: Path, dest: Path) -> Path:
    """The export's entries under dest (a name that would land outside it is refused); the
    session folder."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as z:
        infos = z.infolist()
        if len(infos) > MAX_ENTRIES or sum(i.file_size for i in infos) > MAX_UNPACKED:
            raise Refused(f"{zip_path}: more than {MAX_ENTRIES} entries or {MAX_UNPACKED >> 30} GiB unpacked")
        for info in infos:
            target = (dest / info.filename).resolve()
            if not str(target).startswith(str(dest.resolve()) + os.sep):
                raise Refused(f"{zip_path}: the entry {info.filename!r} would land outside the work folder")
            if info.is_dir():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
    session = dest / "session"
    if not (dest / "MANIFEST.txt").is_file() or not session.is_dir():
        raise Refused(f"{zip_path} is not a Debug Export (no MANIFEST.txt and session/)")
    return session


def safe_name(name: str) -> str:
    """The manifest's session name is the exporter's, so it only names a folder when it is one
    plain component; anything else (a path, "..") is the folder "export"."""
    return name if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", name) else "export"


def session_name(export_dir: Path) -> str:
    for line in (export_dir / "MANIFEST.txt").read_text(errors="replace").splitlines():
        if line.startswith("# session: "):
            return line[len("# session: "):].strip()
    return "session"


def read_env_lines(text: str) -> dict[str, str]:
    env = {}
    for line in text.splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k] = v
    return env


def carried(session_env: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in session_env.items() if k in CARRY_KEYS or k.startswith(CARRY_PREFIXES)}


def seed_overlay(session: Path, overlay: Path) -> list[str]:
    """WORK/overlay from start-saves/ and start-config/ (each folder's index.tsv is the
    recorder's, not the game's). Returns what was seeded."""
    if overlay.exists():
        shutil.rmtree(overlay)
    seeded = []
    for src, rel in (("start-saves", SAVE_DIR), ("start-config", "Config")):
        folder = session / src
        if not folder.is_dir():
            raise Refused(f"the export has no session/{src}/: the session was not recorded (the session "
                          "recorder is off unless asked for: --record-session or SA2_RECORD_SESSION=1 in "
                          "share/game.env, or Game settings > Debug > Record sessions on Android), so the state "
                          "it started from is unknown")
        dest = overlay / rel
        dest.mkdir(parents=True, exist_ok=True)
        for f in sorted(folder.iterdir()):
            if f.is_file() and f.name != "index.tsv":
                shutil.copy2(f, dest / f.name)
                seeded.append(f"{rel}/{f.name}")
    return seeded


def pad_script_useful(pad_log: Path) -> bool:
    """A pad log with a mode line or a change line: one with neither (a session whose game never
    asked XInput) replays as no pad script at all."""
    if not pad_log.is_file():
        return False
    return any(l.strip() and not l.startswith("#") for l in pad_log.read_text(errors="replace").splitlines())


# ---------------------------------------------------------------- ticks

def ticks(path: Path) -> list[tuple[int, int, int]]:
    """(tick, game_mode, current_menu) of every T row of a launch observer table."""
    rows = []
    if not path.is_file():
        return rows
    for line in path.read_text(errors="replace").splitlines():
        f = line.split("\t")
        if len(f) >= 5 and f[0] == "T" and f[1].isdigit():
            rows.append((int(f[1]), int(f[3]), int(f[4])))
    return rows


def transitions(rows: list[tuple[int, int, int]], last: int | None = None) -> list[tuple[int, int, int]]:
    """The rows where game_mode or current_menu changes, up to tick `last`."""
    out, prev = [], None
    for tick, mode, menu in rows:
        if last is not None and tick > last:
            break
        if (mode, menu) != prev:
            out.append((tick, mode, menu))
            prev = (mode, menu)
    return out


def first_difference(a: list, b: list) -> str | None:
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return f"transition {i}: the session has {x} (tick, game_mode, menu), the replay {y}"
    if len(a) != len(b):
        longer, which = (a, "session") if len(a) > len(b) else (b, "replay")
        return f"the {which} has {abs(len(a) - len(b))} more transitions, the first {longer[min(len(a), len(b))]}"
    return None


def logs_agree(session: list[str], replay: list[str]) -> bool:
    """Both logs end where their run was stopped (the session at its SIGTERM, the replay past the
    session's last tick), so the shorter one's last line may be cut short: every line before it
    must be the same."""
    n = min(len(session), len(replay))
    if n == 0:
        return len(session) == len(replay)
    return session[:n - 1] == replay[:n - 1]


def log_lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return [l for l in path.read_text(errors="replace").splitlines() if l.strip() and not l.startswith("#")]


# ---------------------------------------------------------------- the run

def install_config() -> dict[str, str]:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    cfg = Path(base) / "sa2recomp/sa2recomp.cfg"
    return read_env_lines(cfg.read_text()) if cfg.is_file() else {}


def build_env(release: Path, session: Path, work: Path, install: str, trace: bool,
              overrides: list[str]) -> tuple[dict[str, str], dict[str, str]]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("SA2_")}
    game_env = release / "share/game.env"
    if not game_env.is_file():
        raise Refused(f"no {game_env}: --release names no Linux release")
    env.update(read_env_lines(game_env.read_text()))
    session_env = read_env_lines((session / "game-env.txt").read_text()) if (session / "game-env.txt").is_file() else {}
    carry = carried(session_env)
    env.update(carry)
    for kv in overrides:
        if "=" not in kv or not kv.startswith("SA2_"):
            raise Refused(f"--env {kv}: an SA2_K=V setting")
        k, v = kv.split("=", 1)
        if k in OWN_KEYS:
            raise Refused(f"--env {k}: the replay sets it")
        env[k] = v
    env.update({
        "SA2_INSTALL": install,
        "SA2_VFS_WRITE_ROOT": str(work / "overlay"),
        "SA2_VFS_HIDE_INSTALL_SAVES": "1",
        "SA2_EXE_KIND": "shipped",
        "SA2_INPUT_DI_SCRIPT": str(session / "input.log"),
        "SA2_INPUT_DI_LOG": str(work / "input.log"),
        "SA2_LAUNCH_TICKS": str(work / "ticks.tsv"),
    })
    if pad_script_useful(session / "pad.log"):
        env["SA2_INPUT_PAD_SCRIPT"] = str(session / "pad.log")
        env["SA2_INPUT_PAD_LOG"] = str(work / "pad.log")
    if trace:
        env["SA2_REC_TRACE"] = str(work / "stream.bin")
    return env, carry


def run_game(release: Path, exe: str, env: dict[str, str], work: Path, last_tick: int, timeout: float) -> str:
    """The game until it passes `last_tick` (or ends, or `timeout`); how it ended."""
    for name in ("input.log", "pad.log", "ticks.tsv", "stream.bin"):
        (work / name).unlink(missing_ok=True)
    game = release / "bin/sa2-game"
    with open(work / "game.out", "wb") as out, open(work / "game.log", "wb") as err:
        p = subprocess.Popen([str(game), exe, "--json", str(work / "game.json"), "--timeout", "0"],
                             cwd=work, env=env, stdout=out, stderr=err, start_new_session=True)
        t0 = time.monotonic()
        how = None
        try:
            while p.poll() is None:
                rows = ticks(work / "ticks.tsv")
                if rows and rows[-1][0] > last_tick:
                    how = f"passed the session's last tick {last_tick}"
                    break
                if time.monotonic() - t0 > timeout:
                    how = f"timed out after {timeout:.0f} s"
                    break
                time.sleep(0.5)
            if how is None:
                return f"the game ended itself (exit {p.returncode})"
        finally:
            if p.poll() is None:
                os.kill(p.pid, signal.SIGTERM)          # its own PID, never a pattern
                try:
                    p.wait(10)
                except subprocess.TimeoutExpired:
                    os.kill(p.pid, signal.SIGKILL)
                    p.wait(10)
        return how


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("export", type=Path)
    ap.add_argument("--work", type=Path)
    ap.add_argument("--release", type=Path, default=DEFAULT_RELEASE)
    ap.add_argument("--install")
    ap.add_argument("--exe")
    ap.add_argument("--no-trace", action="store_true")
    ap.add_argument("--timeout", type=float, default=3600)
    ap.add_argument("--env", action="append", default=[])
    ap.add_argument("--dry-run", action="store_true", help="prepare WORK and print the settings; run nothing")
    a = ap.parse_args(argv)
    try:
        if not a.export.is_file():
            raise Refused(f"no export {a.export}")
        cfg = install_config()
        install = a.install or cfg.get("read_root")
        exe = a.exe or cfg.get("exe")
        if not install or not exe:
            raise Refused("no game folder and executable: pass --install and --exe, or install with sa2-install")
        with zipfile.ZipFile(a.export) as z:
            names = z.namelist()
        work = a.work
        if work is None:
            name = "export"
            if "MANIFEST.txt" in names:
                with zipfile.ZipFile(a.export) as z:
                    for line in z.read("MANIFEST.txt").decode(errors="replace").splitlines():
                        if line.startswith("# session: "):
                            name = line[len("# session: "):].strip()
            work = ROOT / "private/replays" / safe_name(name)
        work = work.resolve()
        work.mkdir(parents=True, exist_ok=True)
        session = extract(a.export, work / "export")
        seeded = seed_overlay(session, work / "overlay")
        env, carry = build_env(a.release, session, work, install, not a.no_trace, a.env)
        want = ticks(session / "ticks.tsv")
        if not want:
            raise Refused("the export's session/ticks.tsv holds no tick: nothing to compare")
        last = want[-1][0]
        print(f"replay_export: session {session_name(work / 'export')}, {len(want)} ticks, work {work}")
        print(f"replay_export: seeded {len(seeded)} files: {', '.join(seeded)}")
        print("replay_export: carried from the session: " +
              (", ".join(f"{k}={v}" for k, v in sorted(carry.items())) or "nothing"))
        print(f"replay_export: pads: {'SA2_INPUT_PAD_SCRIPT ' + env['SA2_INPUT_PAD_SCRIPT'] if 'SA2_INPUT_PAD_SCRIPT' in env else 'none (no pad log, or an empty one)'}")
        if a.dry_run:
            for k in sorted(k for k in env if k.startswith("SA2_")):
                print(f"  {k}={env[k]}")
            return 0
        how = run_game(a.release, exe, env, work, last, a.timeout)
        got = ticks(work / "ticks.tsv")
        print(f"replay_export: the replay {how}; {len(got)} ticks")
        tw, tg = transitions(want, last), transitions(got, last)
        diff = first_difference(tw, tg)
        for name in ("input.log", "pad.log"):
            if name == "pad.log" and "SA2_INPUT_PAD_SCRIPT" not in env:
                continue
            s, r = log_lines(session / name), log_lines(work / name)
            same = logs_agree(s, r)
            print(f"replay_export: {name}: {len(s)} session lines, {len(r)} replay lines, "
                  f"{'the same' if same else 'DIFFERENT'}")
        if (work / "stream.bin").is_file():
            print(f"replay_export: stream {work / 'stream.bin'} ({(work / 'stream.bin').stat().st_size} bytes)")
        print("replay_export: transitions (tick game_mode menu), session vs replay:")
        for i in range(max(len(tw), len(tg))):
            x = tw[i] if i < len(tw) else None
            y = tg[i] if i < len(tg) else None
            print(f"  {x!s:>20} {y!s:>20}{'' if x == y else '   <- differs'}")
        if diff:
            print(f"replay_export: MISMATCH: {diff}")
            return 1
        print(f"replay_export: MATCH: {len(tw)} transitions to tick {last}")
        return 0
    except Refused as e:
        print(f"replay_export: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
