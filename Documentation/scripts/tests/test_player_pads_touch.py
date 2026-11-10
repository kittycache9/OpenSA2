"""Controllers and touch in the player, end to end without the game.

The player (host/player/player.cpp, "Gamepads") sends SDL's gamepads and its touch overlay
(host/player/touch_overlay.h) to the game as gamepad records on its live input FIFO (contract A
of Documentation/decisions/2026-10-12-S7-all-platforms-by-tuesday.md's push; input.h
send_pads). Here the second host's player, built by host/d3d9gpu/build.sh, plays the synthetic
stream (host/player/synth_stream.cpp) live, as an interactive session does, with
--synthetic-input driving SDL virtual gamepads and pushing finger events, and --input-path a
FIFO this test reads, so each record the game would read is checked byte for byte:

  * pads: a pad attached is slot 0 connected; its buttons by position (SOUTH is A) and D-pad
    are XInput bits, its sticks XInput's (Y up positive), its triggers 0..255; a second pad
    takes slot 1; a pad detached leaves its slot; a press and its release are a record each;
  * touch: the overlay's first touch connects slot 0 (no pad is in it then); a held button
    and the stick are its pad; the stick's origin stays where its finger landed (a finger pulled
    past the radius is a full push, and back where it landed is the centre), and a new touch is
    a new origin; a physical pad given slot 0 shares it with the overlay, and the pad's button
    hides the overlay until the next touch;
  * drawing: --overlay-out's pictures of what the swapchain was given show nothing over the
    frame before the first touch, and the controls after it, in the corners and not in the
    middle;
  * frames: the frames file (read back before Present) holds no overlay: a run without input
    has the same frames table, frame for frame;
  * player one: with SA2_PAD_P1 naming the second pad, by name or by vendor and product, it takes
    slot 0 and the first pad moves up to slot 1; SA2_PAD_P1=ALL sends both pads as slot 0;
  * settings: the same script with SA2_PAD_MAP (a pad's SOUTH and EAST swapped), SA2_TOUCH_MAP
    (the overlay's A sends X) and the overlay bigger with its stick and diamond swapped
    (SA2_TOUCH_SCALE, SA2_TOUCH_LAYOUT) sends the same records with only those buttons changed:
    the script's touches find the controls where the settings put them.

The window opens for a few seconds (the interactive mode refuses a hidden one). The player is
built from this tree by host/d3d9gpu/build.sh (about a minute and a half), unless SA2_PLAYER_GPU
names one already built. test_the_overlay_alone needs neither the player nor its window: it
builds host/player/test_touch_overlay.cpp (the overlay's own pad and drawing, on a Vulkan device)
against the install's SDL3 (SA2_S5_INSTALL, else private/s5-tools/install) and runs it:

  .venv/bin/python -m pytest Documentation/scripts/tests/test_player_pads_touch.py
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import threading
import time
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
GXX = shutil.which(os.environ.get("CXX", "g++"))
KEY_MAGIC, PAD_MAGIC = 0x4B324153, 0x50324153
FEED_GAP_S = 2.5            # the stream's second half comes this long after its first

# MS after the first Present, as player.cpp's "Gamepads" says. Buttons and axes are
# SDL_GamepadButton and SDL_GamepadAxis numbers: SOUTH 0, EAST 1, START 6, DPAD_UP 11; LEFTX 0,
# LEFTY 1, RIGHT_TRIGGER 5.
SCRIPT = """\
# pads
100 pad-attach 0
150 pad-button 0 0 1
200 pad-axis 0 0 32767
250 pad-axis 0 1 -32768
300 pad-axis 0 5 32767
350 pad-button 0 0 0
400 pad-button 0 11 1
450 pad-attach 1
500 pad-button 1 6 1
550 pad-detach 0
# touch
650 touch-down 1 @A
700 touch-down 2 @STICK
750 touch-move 2 @STICK 1 0
800 touch-up 1
850 pad-attach 2
900 pad-button 2 1 1
950 pad-button 2 1 0
1000 touch-down 3 @Y
1050 touch-down 4 @STICK
1100 touch-move 4 @STICK 0 -1
1150 touch-move 4 @STICK 3 0
1200 touch-move 4 @STICK 0 0
1250 touch-up 4
1300 touch-down 5 @STICK 1 0
1350 touch-move 5 @STICK 0 0
"""

A, B, X, Y, START, DPAD_UP = 0x1000, 0x2000, 0x4000, 0x8000, 0x0010, 0x0001


def slot(buttons=0, lt=0, rt=0, lx=0, ly=0, rx=0, ry=0):
    return (1, buttons, lt, rt, lx, ly, rx, ry)


OFF = (0, 0, 0, 0, 0, 0, 0, 0)
# Every gamepad record the FIFO must carry, in order (a record per change: input.h). "let go":
# SDL lets a pad's buttons and axes go as it detaches it, in its own order, before the removal:
# any records there have slot 0 still connected, no button held, and the other slots as before.
EXPECTED = [
    (OFF, OFF, OFF, OFF),                                      # at the FIFO's open: no pad
    (slot(), OFF, OFF, OFF),                                   # pad 0 attached: slot 0
    (slot(A), OFF, OFF, OFF),
    (slot(A, lx=32767), OFF, OFF, OFF),
    (slot(A, lx=32767, ly=32767), OFF, OFF, OFF),              # SDL's up is XInput's +Y
    (slot(A, rt=255, lx=32767, ly=32767), OFF, OFF, OFF),
    (slot(rt=255, lx=32767, ly=32767), OFF, OFF, OFF),
    (slot(DPAD_UP, rt=255, lx=32767, ly=32767), OFF, OFF, OFF),
    (slot(DPAD_UP, rt=255, lx=32767, ly=32767), slot(), OFF, OFF),   # pad 1: slot 1
    (slot(DPAD_UP, rt=255, lx=32767, ly=32767), slot(START), OFF, OFF),
    "let go",                                                  # SDL releases a pad as it goes
    (OFF, slot(START), OFF, OFF),                              # pad 0 detached
    (slot(A), slot(START), OFF, OFF),                          # the first touch: slot 0
    (slot(A, lx=32767), slot(START), OFF, OFF),                # the stick pushed a radius right
    (slot(lx=32767), slot(START), OFF, OFF),                   # A let go; pad 2 then shares slot 0
    (slot(B), slot(START), OFF, OFF),                          # pad 2's B: the overlay hides
    (slot(), slot(START), OFF, OFF),
    (slot(Y), slot(START), OFF, OFF),                          # a touch again: Y
    (slot(Y, ly=32767), slot(START), OFF, OFF),                # the stick pushed up
    (slot(Y, lx=32767), slot(START), OFF, OFF),                # pulled three radii right: a full push
    (slot(Y), slot(START), OFF, OFF),                          # back where it landed: its origin stayed
    (slot(Y, lx=-32767), slot(START), OFF, OFF),               # a new touch a radius right: its own origin
    (OFF, OFF, OFF, OFF),                                      # the end: every slot let go
]


# The settings run (test_the_settings_change_only_what_they_name).
SETTINGS = {"SA2_PAD_MAP": "SOUTH=B,EAST=A", "SA2_TOUCH_MAP": "A=X", "SA2_TOUCH_SCALE": "1.5",
            "SA2_TOUCH_LAYOUT": "STICK=0.8,0.65;FACE=0.2,0.6"}
# EXPECTED with pad 0's SOUTH (A) sending B, pad 2's EAST (B) sending A, and the overlay's A X.
EXPECTED_SETTINGS = [
    (OFF, OFF, OFF, OFF),
    (slot(), OFF, OFF, OFF),
    (slot(B), OFF, OFF, OFF),
    (slot(B, lx=32767), OFF, OFF, OFF),
    (slot(B, lx=32767, ly=32767), OFF, OFF, OFF),
    (slot(B, rt=255, lx=32767, ly=32767), OFF, OFF, OFF),
    (slot(rt=255, lx=32767, ly=32767), OFF, OFF, OFF),
    (slot(DPAD_UP, rt=255, lx=32767, ly=32767), OFF, OFF, OFF),
    (slot(DPAD_UP, rt=255, lx=32767, ly=32767), slot(), OFF, OFF),
    (slot(DPAD_UP, rt=255, lx=32767, ly=32767), slot(START), OFF, OFF),
    "let go",
    (OFF, slot(START), OFF, OFF),
    (slot(X), slot(START), OFF, OFF),
    (slot(X, lx=32767), slot(START), OFF, OFF),
    (slot(lx=32767), slot(START), OFF, OFF),
    (slot(A), slot(START), OFF, OFF),
    (slot(), slot(START), OFF, OFF),
    (slot(Y), slot(START), OFF, OFF),
    (slot(Y, ly=32767), slot(START), OFF, OFF),
    (slot(Y, lx=32767), slot(START), OFF, OFF),
    (slot(Y), slot(START), OFF, OFF),
    (slot(Y, lx=-32767), slot(START), OFF, OFF),
    (OFF, OFF, OFF, OFF),
]


# Two pads, the second the one the player means as player one (SA2_PAD_P1): its A, then the
# first pad's START (SDL_GamepadButton 0 and 6).
P1_SCRIPT = """\
100 pad-attach 0
150 pad-attach 1
200 pad-button 1 0 1
250 pad-button 1 0 0
300 pad-button 0 6 1
350 pad-button 0 6 0
"""
EXPECTED_AUTO = [
    (OFF, OFF, OFF, OFF),
    (slot(), OFF, OFF, OFF),
    (slot(), slot(), OFF, OFF),
    (slot(), slot(A), OFF, OFF),                 # pad 1 is slot 1: player two
    (slot(), slot(), OFF, OFF),
    (slot(START), slot(), OFF, OFF),
    (slot(), slot(), OFF, OFF),
    (OFF, OFF, OFF, OFF),
]
EXPECTED_P1 = [
    (OFF, OFF, OFF, OFF),
    (slot(), OFF, OFF, OFF),
    (slot(), slot(), OFF, OFF),                  # pad 1 comes in and takes slot 0; pad 0 moves up
    (slot(A), slot(), OFF, OFF),
    (slot(), slot(), OFF, OFF),
    (slot(), slot(START), OFF, OFF),
    (slot(), slot(), OFF, OFF),
    (OFF, OFF, OFF, OFF),
]
EXPECTED_ALL = [
    (OFF, OFF, OFF, OFF),
    (slot(), OFF, OFF, OFF),                     # both pads are slot 0: pad 1 changes nothing
    (slot(A), OFF, OFF, OFF),
    (slot(), OFF, OFF, OFF),
    (slot(START), OFF, OFF, OFF),
    (slot(), OFF, OFF, OFF),
    (OFF, OFF, OFF, OFF),
]


def read_png_rgb(path: Path) -> tuple[int, int, bytes]:
    """An 8-bit RGB PNG (texcodec.cpp write_png_rgb's) as (w, h, rgb rows)."""
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, w = 8, b"", 0
    while pos < len(data):
        n, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        if kind == b"IHDR":
            w, h, depth, colour, _, _, interlace = struct.unpack(">IIBBBBB", body)
            assert (depth, colour, interlace) == (8, 2, 0)
        elif kind == b"IDAT":
            idat += body
        pos += 12 + n
    raw, stride, out, prev = zlib.decompress(idat), w * 3, bytearray(), bytearray(w * 3)
    for y in range(h):
        f, row = raw[y * (stride + 1)], bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = row[i - 3] if i >= 3 else 0
            b, c = prev[i], prev[i - 3] if i >= 3 else 0
            if f == 1: row[i] = (row[i] + a) & 255
            elif f == 2: row[i] = (row[i] + b) & 255
            elif f == 3: row[i] = (row[i] + (a + b) // 2) & 255
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                row[i] = (row[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        out += row
        prev = row
    return w, h, bytes(out)


def build_synth(out: Path) -> Path:
    exe = out / "sa2_synth_stream"
    subprocess.run([GXX, "-std=c++17", "-O1", "-I", str(ROOT / "host"), "-o", str(exe),
                    str(ROOT / "host/player/synth_stream.cpp"), str(ROOT / "host/player/texcodec.cpp"), "-lpng"],
                   check=True, capture_output=True)
    stream = out / "synth.sa2d3d9"
    subprocess.run([str(exe), str(stream), str(out / "synth.expect.tsv")], check=True, capture_output=True)
    return stream


def build_player(out: Path) -> Path:
    """SA2_PLAYER_GPU, or the second host's player built from this tree into `out`."""
    if os.environ.get("SA2_PLAYER_GPU"):
        player = Path(os.environ["SA2_PLAYER_GPU"])
        assert player.is_file(), f"SA2_PLAYER_GPU names no file: {player}"
        return player
    r = subprocess.run(["bash", str(ROOT / "host/d3d9gpu/build.sh")], env=dict(os.environ, OUT=str(out)),
                       capture_output=True, text=True, timeout=1800)
    assert r.returncode == 0 and "D3D9GPU_BUILD_OK" in r.stdout, (r.stdout + r.stderr)[-4000:]
    return out / "sa2_player_gpu"


def play(player: Path, stream: Path, out: Path, script: str | None, settings: dict | None = None) -> dict:
    """One live interactive run of the synthetic stream, fed in two parts FEED_GAP_S apart, with
    `settings` in the player's environment: the records the input FIFO carried, the report, and
    the frames table."""
    out.mkdir(parents=True)
    fifo = out / "input.fifo"
    os.mkfifo(fifo)
    reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
    video_r, video_w = os.pipe()
    cancel_r, cancel_w = os.pipe()                       # never written: no cancel
    argv = [str(player), "--interactive", "--video-fd", str(video_r), "--cancel-fd", str(cancel_r),
            "--input-path", str(fifo), "--allow-notes", "--stage", "3",
            "--frames-out", str(out / "frames.y4m"), "--report", str(out / "report.json")]
    if script is not None:
        (out / "script.tsv").write_text(script)
        (out / "shots").mkdir()
        argv += ["--synthetic-input", str(out / "script.tsv"), "--overlay-out", str(out / "shots")]
    env = dict(os.environ, VK_LOADER_LAYERS_DISABLE="~implicit~", SDL_AUDIO_DRIVER="dummy")
    for k in [k for k in env if k.startswith("SA2_TOUCH_") or k in ("SA2_PAD_MAP", "SA2_PAD_P1")]:
        env.pop(k)
    env.update(settings or {})
    with (out / "player.out").open("wb") as so:
        proc = subprocess.Popen(argv, pass_fds=(video_r, cancel_r), stdout=so, stderr=subprocess.STDOUT,
                                env=env, cwd=out)        # the player writes its device log here
    os.close(video_r)
    os.close(cancel_r)
    data = stream.read_bytes()

    def feed():
        try:
            with os.fdopen(video_w, "wb") as f:
                f.write(data[:len(data) // 2])
                f.flush()
                time.sleep(FEED_GAP_S)
                f.write(data[len(data) // 2:])
        except BrokenPipeError:              # the player stopped early: its report says why
            pass

    feeder = threading.Thread(target=feed)
    feeder.start()
    got = bytearray()
    deadline = time.monotonic() + 60
    while proc.poll() is None and time.monotonic() < deadline:
        try:
            got += os.read(reader, 65536)
        except BlockingIOError:
            pass
        time.sleep(0.002)
    if proc.poll() is None:
        proc.kill()
    proc.wait()
    feeder.join()
    os.close(cancel_w)
    try:
        while chunk := os.read(reader, 65536):
            got += chunk
    except BlockingIOError:
        pass
    os.close(reader)
    records, pos = [], 0
    while pos + 8 <= len(got):
        magic, seq = struct.unpack_from("<II", got, pos)
        size = 72 if magic == PAD_MAGIC else 264 if magic == KEY_MAGIC else None
        assert size, f"a record of magic {magic:#x} at byte {pos}"
        if magic == PAD_MAGIC:
            slots = tuple(struct.unpack_from("<BxHBBhhhh2x", got, pos + 8 + 16 * i) for i in range(4))
            records.append((seq, slots))
        pos += size
    assert pos == len(got), f"{len(got) - pos} bytes of a record left over"
    (out / "records.json").write_text(json.dumps(records) + "\n")
    times_file = out / "frames.y4m.times.tsv"
    times = times_file.read_text().splitlines() if times_file.is_file() else []
    (out / "frames.y4m").unlink(missing_ok=True)
    return {"exit": proc.returncode, "records": records, "report": json.loads((out / "report.json").read_text()),
            "times": times, "out": out}


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
        pytest.fail("no display: the interactive mode opens a window")
    t = tmp_path_factory.mktemp("pads_touch")
    player = build_player(t / "bin")
    stream = build_synth(t)
    return {"input": play(player, stream, t / "input", SCRIPT), "plain": play(player, stream, t / "plain", None),
            "settings": play(player, stream, t / "settings", SCRIPT, SETTINGS),
            "p1_name": play(player, stream, t / "p1_name", P1_SCRIPT, {"SA2_PAD_P1": "NAME:SA2 synthetic pad 1"}),
            "p1_id": play(player, stream, t / "p1_id", P1_SCRIPT, {"SA2_PAD_P1": "ID:5341:0101"}),
            "p1_all": play(player, stream, t / "p1_all", P1_SCRIPT, {"SA2_PAD_P1": "ALL"}),
            "p1_none": play(player, stream, t / "p1_none", P1_SCRIPT, {"SA2_PAD_P1": "NAME:no such pad"})}


def test_the_overlay_alone(tmp_path):
    """The overlay without the player: its pad from finger events, with the stick's origin
    fixed where its finger landed (past the radius a full push in the finger's direction, the
    origin unmoved; a new touch re-centres), and its drawing (the base where the finger landed,
    the knob at the rim) on a Vulkan device with no window."""
    if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
        pytest.fail("no display: SDL's video subsystem, which the test's device needs, finds none")
    install = Path(os.environ.get("SA2_S5_INSTALL") or ROOT / "private/s5-tools/install").resolve()
    here, exe = ROOT / "host/player", tmp_path / "test_touch_overlay"
    subprocess.run([GXX, "-std=c++17", "-O2", "-Wall", "-Wextra", "-Wno-unused-parameter",
                    f"-I{install / 'include'}", "-o", str(exe), str(here / "test_touch_overlay.cpp"),
                    str(here / "touch_overlay.cpp"), str(here / "texcodec.cpp"), f"-L{install / 'lib'}",
                    "-Wl,--disable-new-dtags", f"-Wl,-rpath,{install / 'lib'}", "-lSDL3", "-lpng"],
                   check=True, capture_output=True)
    env = dict(os.environ, VK_LOADER_LAYERS_DISABLE="~implicit~", SDL_AUDIO_DRIVER="dummy")
    r = subprocess.run([str(exe), str(tmp_path)], capture_output=True, text=True, timeout=120, env=env)
    assert r.returncode == 0 and r.stdout.splitlines()[-1] == "TOUCH_OVERLAY_OK", r.stdout + r.stderr
    assert "pad mapping: ok" in r.stdout and (tmp_path / "overlay.png").is_file()


def test_the_run_and_its_script(runs):
    r = runs["input"]
    rep = r["report"]
    assert r["exit"] == 0, (r["out"] / "player.out").read_text()[-3000:]
    assert rep["pass"] is True and rep["complete"] is True
    lines = [x for x in SCRIPT.splitlines() if x and not x.startswith("#")]
    assert rep["synthetic_input"] == {"lines": len(lines), "done": len(lines), "failures": 0, "armed": True}
    pads = rep["host_pads"]
    assert (pads["up"], pads["gamepads"], pads["touch_platform"], pads["error"]) == (True, True, False, None)
    assert (pads["added"], pads["removed"], pads["unslotted"], pads["queue_overflows"]) == (3, 1, 0, 0)
    t = pads["touch"]
    assert (t["activations"], t["hides"], t["draw_failures"], t["gpu"], t["error"]) == (2, 1, 0, True, None)


def records_match(records, expected):
    got, i, last = [slots for _, slots in records], 0, None
    for want in expected:
        if want == "let go":
            while i < len(got) and got[i][0][:2] == (1, 0) and got[i][1:] == last[1:]:
                i += 1
            continue
        assert i < len(got) and got[i] == want, (i, got[i:i + 3], want)
        last, i = want, i + 1
    assert i == len(got), got[i:]
    seqs = [seq for seq, _ in records]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


def test_every_gamepad_record_in_order(runs):
    records_match(runs["input"]["records"], EXPECTED)


def test_the_settings_change_only_what_they_name(runs):
    r = runs["settings"]
    assert r["exit"] == 0, (r["out"] / "player.out").read_text()[-3000:]
    assert r["report"]["synthetic_input"]["failures"] == 0
    said = (r["out"] / "player.out").read_text()
    for k, v in SETTINGS.items():
        assert f"pads: {k}={v}\n" in said, said[-3000:]
    records_match(r["records"], EXPECTED_SETTINGS)


def test_the_overlay_over_the_frame_and_only_after_a_touch(runs):
    r = runs["input"]
    shots = sorted(r["out"].glob("shots/frame-*-game.png"), key=lambda p: int(p.name.split("-")[1]))
    pads = r["report"]["host_pads"]
    assert len(shots) == pads["overlay_shots"] >= 2 and pads["overlay_shot_error"] is None
    assert pads["overlay_presents"] >= 1
    before, after = [], []
    for game in shots:
        w, h, g = read_png_rgb(game)
        _, _, s = read_png_rgb(game.with_name(game.name.replace("-game", "-swapchain")))
        diff = [i for i in range(0, len(g), 3) if g[i:i + 3] != s[i:i + 3]]
        (after if diff else before).append((game.name, w, h, g, s, diff))
    assert before and after, f"shots without the overlay {len(before)}, with it {len(after)}"
    # Before the first touch nothing is drawn; the first shot is the first frame.
    assert before[0][0] == "frame-1-game.png"
    name, w, h, g, s, diff = after[-1]
    cover = len(diff) / (w * h)
    assert 0.02 < cover < 0.35, cover
    xs = [(i // 3) % w for i in diff]
    ys = [(i // 3) // w for i in diff]
    quarter = lambda x, y: (x >= w // 2, y >= h // 2)
    corners = {quarter(x, y) for x, y in zip(xs, ys)}
    assert {(False, False), (True, False), (True, True), (False, True)} <= corners
    centre = [(x, y) for x, y in zip(xs, ys) if abs(x - w / 2) < w / 8 and abs(y - h / 2) < h / 8]
    assert not centre, f"the overlay drew in the middle of the frame: {centre[:3]}"
    # Y is held: a bright yellow where the diamond's top button is (right half, lower half).
    yellow = [i for i in diff if quarter((i // 3) % w, (i // 3) // w) == (True, True)
              and s[i] > 150 and s[i + 1] > 120 and s[i + 2] < 90]
    assert len(yellow) > 0.002 * w * h, len(yellow)


def test_the_frames_file_holds_no_overlay(runs):
    with_input, plain = runs["input"], runs["plain"]
    assert plain["exit"] == 0 and plain["report"]["host_pads"]["overlay_presents"] == 0
    head = with_input["times"][0].split("\t")
    col = head.index("bgrx_hash")
    a = [row.split("\t")[col] for row in with_input["times"][1:]]
    b = [row.split("\t")[col] for row in plain["times"][1:]]
    assert len(a) >= 4 and a == b
    # The run without input sent the pads once, at the FIFO's open, and let them go at its end.
    assert [slots for _, slots in plain["records"]] == [(OFF, OFF, OFF, OFF)]


def check_p1(r, expected, said_line):
    assert r["exit"] == 0, (r["out"] / "player.out").read_text()[-3000:]
    assert r["report"]["synthetic_input"]["failures"] == 0
    said = (r["out"] / "player.out").read_text()
    assert said_line in said, said[-3000:]
    records_match(r["records"], expected)


def test_a_pad_named_player_one_takes_slot_zero(runs):
    check_p1(runs["p1_name"], EXPECTED_P1, 'pads: "SA2 synthetic pad 1" is player one (SA2_PAD_P1)\n')


def test_a_pad_named_by_vendor_and_product_takes_slot_zero(runs):
    check_p1(runs["p1_id"], EXPECTED_P1, 'pads: "SA2 synthetic pad 1" is player one (SA2_PAD_P1)\n')


def test_every_pad_together_is_player_one(runs):
    check_p1(runs["p1_all"], EXPECTED_ALL, "pads: SA2_PAD_P1=ALL\n")


def test_a_name_no_pad_has_changes_nothing(runs):
    check_p1(runs["p1_none"], EXPECTED_AUTO, "pads: SA2_PAD_P1=NAME:no such pad\n")
    assert "is player one" not in (runs["p1_none"]["out"] / "player.out").read_text()


def test_each_pad_is_said_with_its_ids_and_slot(runs):
    said = (runs["p1_name"]["out"] / "player.out").read_text()
    assert 'pads: added "SA2 synthetic pad 0" (vendor 5341 product 0100) as slot 0\n' in said
    assert 'pads: added "SA2 synthetic pad 1" (vendor 5341 product 0101) as slot 1\n' in said
