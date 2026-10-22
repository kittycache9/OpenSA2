"""host/android/AndroidManifest.xml and the play activity's sustained-performance setting.

The app is declared a game (android:appCategory) and holds no network permission. Sa2Activity keeps
the sustained-performance mode off: it holds the clocks to what a device can sustain, which caps them."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "AGENTS.md").is_file())
ANDROID = "{http://schemas.android.com/apk/res/android}"
MANIFEST = ET.parse(ROOT / "host/android/AndroidManifest.xml").getroot()
ACTIVITY = (ROOT / "host/android/java/com/sa2recomp/app/Sa2Activity.java").read_text()


def test_the_application_is_declared_a_game():
    app = MANIFEST.find("application")
    assert app.get(ANDROID + "appCategory") == "game"


def test_no_internet_permission_is_requested():
    perms = [p.get(ANDROID + "name") for p in MANIFEST.findall("uses-permission")]
    assert perms == ["android.permission.VIBRATE"]
    assert "INTERNET" not in ET.tostring(MANIFEST, encoding="unicode")


def test_the_play_activity_keeps_the_sustained_performance_mode_off():
    """The mode holds the clocks to what the device can keep up, which caps them, so Sa2Activity turns
    it off and nothing turns it on."""
    assert "setSustainedPerformanceMode(false)" in ACTIVITY
    assert "setSustainedPerformanceMode(true)" not in ACTIVITY
