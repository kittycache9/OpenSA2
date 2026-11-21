"""The Android layout editor shows the touch controls where the game draws them.

The editor (host/android/java/com/sa2recomp/app/Sa2LayoutActivity.java) lays the controls out
with GameSettings.touchLayout, a copy of host/player/touch_overlay.cpp's layout(): the control
area (the window, or the band under a portrait window's picture, placed as the player places it:
host/d3d9gpu/d9g_gpu.h fit_rect's anchor), the size, and each group's place (the one set for the
window's orientation, held whole inside the area). Both run here on the same cases, the overlay's through its control_centre and the editor's
through a small Java driver against android.jar, and every group's centre must agree to a
hundredth of a pixel: a change to one without the other fails here, not on a phone.

Needs the pinned JDK (tools/pins.json java), the Android SDK's android.jar and the SDL3 install
the player's tests build against; it is skipped without them.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
GXX = shutil.which(os.environ.get("CXX", "g++"))
JDK = ROOT / json.loads((ROOT / "tools/pins.json").read_text())["tools"]["java"]["path"]
ANDROID_JAR = ROOT / "private/android-sdk/platforms/android-36/android.jar"
INSTALL = Path(os.environ.get("SA2_S5_INSTALL") or ROOT / "private/s5-tools/install")

GROUPS = ("STICK", "FACE", "START", "LT", "RT", "the picture's top and bottom rows")
# window w, h; frame w, h (0: none yet); scale; the picture's anchor (-1 centred) and least top;
# SA2_TOUCH_LAYOUT; SA2_TOUCH_LAYOUT_PORTRAIT
CASES = [
    (1280, 720, 0, 0, 1.0, -1, 0, "", ""),                         # before the first frame
    (2340, 1080, 2340, 1080, 1.0, -1, 0, "", ""),                  # full width, the default
    (2340, 1080, 1440, 1080, 1.4, -1, 0, "", ""),                  # 4:3 with bars, bigger
    (1560, 720, 1280, 720, 0.6, -1, 0, "STICK=0.8,0.65;FACE=0.2,0.6", ""),   # moved, smaller
    (1080, 2340, 1080, 810, 1.0, -1, 0, "", ""),                   # portrait, centred: the band
    (1080, 2340, 1080, 810, 1.6, -1, 0, "", "START=0.5,0.5"),      # the band holds them back
    (720, 1280, 720, 540, 1.0, -1, 0, "STICK=0.9,0.9", ""),        # a short band shrinks them
    (1812, 2176, 1440, 1080, 1.0, -1, 0, "", "STICK=0.1,0.1"),     # too short a band: over the picture
    (1812, 2176, 1440, 1080, 1.0, -1, 0, "STICK=0.9,0.9", ""),     # upright: the landscape places do not apply
    (2340, 1080, 2340, 1080, 2.0, -1, 0, "FACE=0,1;STICK=1,0;START=0.5,0", ""),   # held whole inside
    (1080, 2340, 1080, 810, 2.0, -1, 0, "", "FACE=1,1;LT=0,0"),    # the same in the band
    (1440, 3120, 1440, 1080, 1.0, 0.25, 0, "", ""),                # portrait's default place: the upper half
    (1440, 3120, 1440, 1080, 1.4, 0.25, 96, "", "STICK=0.3,0.6"),  # under a safe area, bigger, moved
    (1440, 3120, 1440, 1080, 1.0, 0, 130, "", ""),                 # at the top, under the safe area
    (720, 1280, 720, 540, 1.0, 0.25, 0, "", ""),                   # a 16:9 phone: the band now holds them
    (1812, 2176, 1440, 1080, 1.0, 0.25, 0, "", ""),                # a near-square screen: a band appears
    (2340, 1080, 1440, 1080, 1.0, 0.25, 0, "", ""),                # landscape bars at the sides: no effect
    (2176, 1812, 1440, 1080, 1.0, 0.25, 0, "", ""),                # a landscape letterbox stays centred
]

NATIVE = r"""
#include "touch_overlay.h"
#include <cstdio>
#include <cstdlib>
#include <string>
using sa2_touch::Control;
int main(int argc, char** argv) {
    if (argc != 10) return 2;
    const uint32_t w = std::atoi(argv[1]), h = std::atoi(argv[2]);
    sa2_touch::Overlay* o = sa2_touch::create(nullptr, nullptr);
    sa2_touch::set_size(o, w, h);
    sa2_touch::set_frame(o, std::atoi(argv[3]), std::atoi(argv[4]));
    sa2_touch::set_picture(o, std::strtof(argv[6], nullptr), std::atoi(argv[7]));
    sa2_touch::Settings s;
    std::string why;
    if (!sa2_touch::parse_setting("SA2_TOUCH_SCALE", argv[5], &s, &why)) return 3;
    if (*argv[8] && !sa2_touch::parse_setting("SA2_TOUCH_LAYOUT", argv[8], &s, &why)) return 4;
    if (*argv[9] && !sa2_touch::parse_setting("SA2_TOUCH_LAYOUT_PORTRAIT", argv[9], &s, &why)) return 5;
    sa2_touch::set_settings(o, s);
    float x, y, ax, ay, yx, yy;
    sa2_touch::control_centre(o, w, h, Control::Stick, &x, &y);
    std::printf("%d %.3f %.3f\n", sa2_touch::below_picture(o, w, h) ? 1 : 0, x, y);
    sa2_touch::control_centre(o, w, h, Control::A, &ax, &ay);
    sa2_touch::control_centre(o, w, h, Control::Y, &yx, &yy);
    std::printf("%.3f %.3f\n", ax, (ay + yy) / 2);
    for (Control c : {Control::Start, Control::LT, Control::RT}) {
        sa2_touch::control_centre(o, w, h, c, &x, &y);
        std::printf("%.3f %.3f\n", x, y);
    }
    uint32_t top = 0, bottom = h;
    sa2_touch::picture_rows(o, w, h, &top, &bottom);
    std::printf("%u %u\n", top, bottom);
    sa2_touch::destroy(o);
    return 0;
}
"""

# GameSettings.picture() for the resolution list: the default resolution's picture (null: the
# game's own size) and the largest picture, for a screen and the stored resolution.
PICTURE_JAVA = r"""
package com.sa2recomp.app;
public class PictureDriver {
    public static void main(String[] a) {
        GameSettings s = new GameSettings();
        s.resolution = Integer.parseInt(a[2]);
        int lng = Integer.parseInt(a[0]), shrt = Integer.parseInt(a[1]);
        int[] own = s.picture(lng, shrt, 0), full = s.fullPicture(lng, shrt);
        System.out.println((own == null ? "own" : own[0] + "x" + own[1]) + " " + full[0] + "x" + full[1]);
    }
}
"""

JAVA = r"""
package com.sa2recomp.app;
public class LayoutDriver {
    public static void main(String[] a) {
        GameSettings.TouchLayout l = GameSettings.touchLayout(Float.parseFloat(a[0]), Float.parseFloat(a[1]),
                Integer.parseInt(a[2]), Integer.parseInt(a[3]), Float.parseFloat(a[4]), Float.parseFloat(a[5]),
                Float.parseFloat(a[6]), a[7], a[8]);
        System.out.printf(java.util.Locale.US, "%d %.3f %.3f%n", l.below ? 1 : 0, l.x[0], l.y[0]);
        for (int g = 1; g < 5; g++) System.out.printf(java.util.Locale.US, "%.3f %.3f%n", l.x[g], l.y[g]);
        System.out.printf(java.util.Locale.US, "%d %d%n", (int) l.pictureTop, (int) l.pictureBottom);
    }
}
"""


@pytest.fixture(scope="module")
def drivers(tmp_path_factory):
    missing = [str(p) for p in (JDK / "bin/javac", ANDROID_JAR, INSTALL / "include/SDL3/SDL.h") if not p.exists()]
    if missing or not GXX:
        pytest.skip(f"needs the JDK, android.jar and the SDL3 install: missing {missing or 'g++'}")
    d = tmp_path_factory.mktemp("touch_layout")
    (d / "native.cpp").write_text(NATIVE)
    here = ROOT / "host/player"
    subprocess.run([GXX, "-std=c++17", "-O1", f"-I{here}", f"-I{INSTALL / 'include'}", "-o", str(d / "native"),
                    str(d / "native.cpp"), str(here / "touch_overlay.cpp"), str(here / "texcodec.cpp"),
                    f"-L{INSTALL / 'lib'}", "-Wl,--disable-new-dtags", f"-Wl,-rpath,{INSTALL / 'lib'}", "-lSDL3", "-lpng"],
                   check=True, capture_output=True)
    src = d / "java/com/sa2recomp/app"
    src.mkdir(parents=True)
    (src / "LayoutDriver.java").write_text(JAVA)
    (src / "PictureDriver.java").write_text(PICTURE_JAVA)
    shutil.copy(ROOT / "host/android/java/com/sa2recomp/app/GameSettings.java", src)
    subprocess.run([str(JDK / "bin/javac"), "--release", "11", "-nowarn", "-classpath", str(ANDROID_JAR),
                    "-d", str(d / "classes"), *map(str, src.glob("*.java"))], check=True, capture_output=True)

    def native(*args):
        return subprocess.run([str(d / "native"), *map(str, args)], capture_output=True, text=True,
                              check=True).stdout

    def java(*args, main="LayoutDriver"):
        return subprocess.run([str(JDK / "bin/java"), "-cp", f"{d / 'classes'}:{ANDROID_JAR}",
                               f"com.sa2recomp.app.{main}", *map(str, args)], capture_output=True, text=True,
                              check=True).stdout
    return native, java


def centres(out: str) -> tuple[int, list[tuple[float, float]]]:
    rows = [line.split() for line in out.splitlines()]
    below = int(rows[0][0])
    rows[0] = rows[0][1:]
    return below, [(float(x), float(y)) for x, y in rows]


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"{c[0]}x{c[1]}-frame{c[2]}x{c[3]}-scale{c[4]}-at{c[5]}-{c[6]}")
def test_the_editor_places_every_group_where_the_overlay_does(drivers, case):
    native, java = drivers
    want_below, want = centres(native(*case))
    got_below, got = centres(java(*case))
    assert got_below == want_below
    for g, (n, j) in enumerate(zip(want, got)):
        assert abs(n[0] - j[0]) < 0.01 and abs(n[1] - j[1]) < 0.01, (GROUPS[g], n, j)


def test_the_cases_cover_both_areas(drivers):
    native, _ = drivers
    assert {centres(native(*c))[0] for c in CASES} == {0, 1}


@pytest.mark.parametrize("stored", [0, 720, 1080])
def test_the_default_resolution_keeps_the_sessions_rule_whatever_is_stored(drivers, stored):
    """On a 4:3 tablet with the default display settings the session keeps the game's own size at
    the default resolution, so the list's first entry is that and the full screen is offered too,
    whatever resolution was stored; on a wide phone the default is the screen."""
    _, java = drivers
    assert java(2048, 1536, stored, main="PictureDriver").split() == ["own", "2048x1536"]
    assert java(2340, 1080, stored, main="PictureDriver").split() == ["2340x1080", "2340x1080"]
