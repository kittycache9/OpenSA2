"""The Android settings screen's tweaks: what GameSettings sends the session for them.

GameSettings (host/android/java/com/sa2recomp/app/GameSettings.java) turns the settings screen's
choices into the session's --setting values (sessionSettings); the session puts them in the game's
environment before game.env.extra's lines (host/android/native/sa2_android_session.cpp). The
tweaks go as one value, SA2_TWEAKS, which runtime/sa2_tweaks.c reads: absent at the defaults, so a
session with every setting at its default runs as before there were tweaks, and the names of the
tweaks turned on, in GameSettings.TWEAKS's order, otherwise. TWEAKS is the one place a tweak is
named on Android: the Tweaks card makes a switch of each line (test_tweaks.py holds the lines to
runtime/sa2_tweaks.h's list), shown under GameSettings.TWEAK_GROUPS's headings with each group
in the table's own order (GameSettings.tweakGroups), while SA2_TWEAKS and the preferences keep the names. A small Java driver runs GameSettings here against android.jar; the
settings screen (Sa2SettingsActivity, with Ui, the layout editor it opens, SaveTools and the Debug card's
DebugExport with what it reads) is compiled beside
it, so a card that no longer builds fails here, not in the APK's build. A second driver runs
SaveTools.backupNow on folders made here.

Needs the pinned JDK (tools/pins.json java) and the Android SDK's android.jar; it is skipped
without them.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
JDK = ROOT / json.loads((ROOT / "tools/pins.json").read_text())["tools"]["java"]["path"]
ANDROID_JAR = ROOT / "private/android-sdk/platforms/android-36/android.jar"
APP = ROOT / "host/android/java/com/sa2recomp/app"

# argv: lng shrt [tweak names to turn on...] -> each --setting on a line, then the SA2_TWEAKS value
# in brackets; argv "list" -> each TWEAKS line as name|label|description; argv "groups" [heading=name,
# name...]... -> the Tweaks card's order for TWEAK_GROUPS (or for the groups given), "# heading" and
# then "name|label" for each tweak, and "! problem" for each problem tweakGroups found.
DRIVER = r"""
package com.sa2recomp.app;
public class SettingsDriver {
    public static void main(String[] a) {
        if (a[0].equals("list")) {
            for (GameSettings.Tweak t : GameSettings.TWEAKS) System.out.println(t.name + "|" + t.label + "|" + t.description);
            return;
        }
        if (a[0].equals("groups")) {
            String[][] groups = GameSettings.TWEAK_GROUPS;
            if (a.length > 1) {
                groups = new String[a.length - 1][];
                for (int i = 1; i < a.length; i++) {
                    String[] hn = a[i].split("=", 2);
                    String[] names = hn[1].isEmpty() ? new String[0] : hn[1].split(",");
                    groups[i - 1] = new String[names.length + 1];
                    groups[i - 1][0] = hn[0];
                    System.arraycopy(names, 0, groups[i - 1], 1, names.length);
                }
            }
            java.util.List<String> problems = new java.util.ArrayList<>();
            for (GameSettings.TweakGroup g : GameSettings.tweakGroups(groups, GameSettings.TWEAKS, problems)) {
                System.out.println("# " + g.heading);
                for (GameSettings.Tweak t : g.tweaks) System.out.println(t.name + "|" + t.label);
            }
            for (String p : problems) System.out.println("! " + p);
            return;
        }
        GameSettings s = new GameSettings();
        for (int i = 2; i < a.length; i++) s.tweaksOn.add(a[i]);
        for (String e : s.sessionSettings(Integer.parseInt(a[0]), Integer.parseInt(a[1]))) System.out.println(e);
        System.out.println("[" + s.tweaks() + "]");
    }
}
"""

# argv: overlay-saves-folder backup-root -> SaveTools.backupNow's message, as one tap of "Back up saves now".
BACKUP_DRIVER = r"""
package com.sa2recomp.app;
public class BackupDriver {
    public static void main(String[] a) throws Exception {
        System.out.println(SaveTools.backupNow(new java.io.File(a[0]), new java.io.File(a[1])));
    }
}
"""


# Sa2SetupActivity.readText alone (the real class extends SDL's activity, which the tree does not hold).
SETUP_STANDIN = """package com.sa2recomp.app;
final class Sa2SetupActivity {
    static String readText(java.io.File f) {
        try {
            return f.isFile() ? new String(java.nio.file.Files.readAllBytes(f.toPath()), "UTF-8") : null;
        } catch (java.io.IOException e) {
            return null;
        }
    }
}
"""


@pytest.fixture(scope="module")
def java(tmp_path_factory):
    missing = [str(p) for p in (JDK / "bin/javac", ANDROID_JAR) if not p.exists()]
    if missing:
        pytest.skip(f"needs the JDK and android.jar: missing {missing}")
    d = tmp_path_factory.mktemp("settings")
    src = d / "java/com/sa2recomp/app"
    src.mkdir(parents=True)
    (src / "SettingsDriver.java").write_text(DRIVER)
    (src / "BackupDriver.java").write_text(BACKUP_DRIVER)
    for name in ("GameSettings", "Sa2SettingsActivity", "Ui", "Sa2LayoutActivity", "SaveTools", "DebugExport",
                 "CrashReport", "Tombstone", "ExportProvider", "VulkanSupport"):
        shutil.copy(APP / f"{name}.java", src)
    # The Debug card's export reads the crash report's redaction (CrashReport), which reads one helper
    # of the setup screen; the setup screen itself needs SDL's Java, which is not in the tree. So the
    # helper alone stands in for it here, as Sa2SetupActivity.readText reads.
    (src / "Sa2SetupActivity.java").write_text(SETUP_STANDIN)
    done = subprocess.run([str(JDK / "bin/javac"), "--release", "11", "-nowarn", "-classpath", str(ANDROID_JAR),
                           "-d", str(d / "classes"), *map(str, src.glob("*.java"))], capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr

    def run(*args, driver="SettingsDriver") -> list[str]:
        return subprocess.run([str(JDK / "bin/java"), "-cp", f"{d / 'classes'}:{ANDROID_JAR}",
                               f"com.sa2recomp.app.{driver}", *map(str, args)], capture_output=True,
                              text=True, check=True).stdout.splitlines()
    return run


def test_the_defaults_send_no_tweak(java):
    assert java(2340, 1080) == ["[]"]


def test_the_remove_cutscene_bars_switch_sends_no_event_bars(java):
    assert java(2340, 1080, "no-event-bars") == ["SA2_TWEAKS=no-event-bars", "[no-event-bars]"]


def test_the_tweaks_turned_on_go_in_the_lists_order(java):
    """Every line turned on, given in reverse: SA2_TWEAKS names them all once, in TWEAKS's order;
    a name that is no line's is not sent."""
    names = [line.split("|")[0] for line in java("list")]
    assert java(2340, 1080, *reversed(names), "not-a-tweak") == [
        "SA2_TWEAKS=" + ",".join(names), "[" + ",".join(names) + "]"]


def test_each_tweak_line_has_a_label_and_a_note(java):
    lines = [line.split("|") for line in java("list")]
    assert ["no-event-bars", "Remove cutscene black bars"] in [line[:2] for line in lines]
    assert all(len(line) == 3 and all(line) for line in lines)


def test_the_vsync_note_says_android_does_not_tear_either_way(java):
    """Android's swapchain offers no IMMEDIATE, so with vsync off host 2 already presents through
    MAILBOX, tear-free; the switch's note names the trade it makes there, FIFO for up to a frame of
    delay, and does not promise to stop tearing."""
    assert [line for line in java("list") if line.startswith("vsync|")] == [
        "vsync|Vsync|Each frame waits for the screen's refresh (FIFO) instead of mailbox presentation. Android "
        "does not tear either way, so on a phone this only adds up to a frame of delay. Leave it off unless "
        "you are testing it."]


def card(java, *groups) -> tuple[list[tuple[str, list[tuple[str, str]]]], list[str]]:
    """The Tweaks card's order, [(heading, [(name, label)...])...], and tweakGroups' problems."""
    order, problems = [], []
    for line in java("groups", *groups):
        if line.startswith("# "):
            order.append((line[2:], []))
        elif line.startswith("! "):
            problems.append(line[2:])
        else:
            order[-1][1].append(tuple(line.split("|", 1)))
    return order, problems


def groups_of_table() -> list[list[str]]:
    """GameSettings.TWEAK_GROUPS as written: each group's heading, then its names in order."""
    import re
    text = (ROOT / "host/android/java/com/sa2recomp/app/GameSettings.java").read_text()
    body = re.search(r"static final String\[\]\[\] TWEAK_GROUPS = \{(.*?)\n    \};", text, re.S).group(1)
    return [re.findall(r'"([^"]+)"', g) for g in re.findall(r"\{(.*?)\}", body, re.S)]


def test_the_tweaks_card_shows_the_groups_and_their_tweaks_in_the_tables_order(java):
    """The card's headings in TWEAK_GROUPS's order, each group's switches in the table's own order
    (the ones players want most first), every TWEAKS line once, and no problem. Remove cutscene
    black bars, which players could not find among switches in name order, is near the top of Story
    and presentation."""
    order, problems = card(java)
    assert problems == []
    assert [heading for heading, _ in order] == [
        "Gameplay and assists", "Story and presentation", "Controls", "Audio", "Chao", "Fixes", "Other"]
    table = {g[0]: g[1:] for g in groups_of_table()}
    for heading, tweaks in order:
        assert [name for name, _ in tweaks] == table[heading], heading
    names = [name for _, tweaks in order for name, _ in tweaks]
    assert sorted(names) == sorted(line.split("|")[0] for line in java("list"))
    assert dict(order)["Gameplay and assists"][:3] == [
        ("infinite-lives", "Infinite lives"), ("stage-select-all", "Show all stages in Stage Select"),
        ("all-upgrades", "All upgrades")]
    assert dict(order)["Story and presentation"][:3] == [
        ("skip-intro", "Skip intro"), ("all-events-skippable", "Skip any cutscene"),
        ("no-event-bars", "Remove cutscene black bars")]
    story = [name for name, _ in dict(order)["Story and presentation"]]
    assert story[story.index("fast-stage-load") + 1] == "sync-load"
    assert dict(order)["Controls"] == [("analog-fix", "Analog stick fix"),
                                       ("action-buttons", "Light dash on Y, spin dash on X"),
                                       ("deadzone", "Round stick deadzone"),
                                       ("camera-invert", "Invert camera"), ("hold-toggle", "Tap to hold B")]
    assert dict(order)["Other"] == [("pause-unfocused", "Pause when unfocused")]


def test_a_tweak_in_no_group_is_named_and_shown_under_other(java):
    """A grouping mistake never hides a switch: a tweak in no group goes under Other at the end, one
    in two groups shows in the first, a name that is no tweak's and a group left empty are dropped,
    and each is named."""
    every = [line.split("|")[0] for line in java("list")]
    rest = [n for n in every if n not in ("vsync", "fov", "mono-audio")]
    order, problems = card(java, "Look=vsync,not-a-tweak", "Sound=mono-audio,vsync", "Empty=", "All=" + ",".join(rest))
    assert [heading for heading, _ in order] == ["Look", "Sound", "All", "Other"]
    assert dict(order)["Look"] == [("vsync", "Vsync")]
    assert dict(order)["Sound"] == [("mono-audio", "Mono audio")]
    assert dict(order)["Other"] == [("fov", "Wider field of view")]
    assert problems == ['"not-a-tweak" under Look is no tweak', '"vsync" is under Look and Sound',
                        '"fov" is in no group; shown under Other']


def test_the_tweaks_card_is_made_from_the_list():
    """One Tweaks card, made from tweakGroups' order: a heading for each group and a switch and its
    note for each tweak, each switch found by its tweak's name, and the switches kept in the
    preferences like every other setting: no tweak is named on the screen itself."""
    screen = (APP / "Sa2SettingsActivity.java").read_text()
    settings = (APP / "GameSettings.java").read_text()
    assert screen.count('card(col, "Tweaks")') == 1
    assert "GameSettings.tweakGroups(GameSettings.TWEAK_GROUPS, GameSettings.TWEAKS, problems)" in screen
    assert "for (GameSettings.TweakGroup group : groups) {" in screen
    assert "tw.addView(heading(group.heading)," in screen
    assert "for (GameSettings.Tweak tweak : group.tweaks) {" in screen
    assert "Switch w = toggle(tw, tweak.label," in screen and "note(tw, tweak.description);" in screen
    assert "mTweaks.put(tweak.name, w);" in screen
    assert "e.getValue().setChecked(mS.tweaksOn.contains(e.getKey()));" in screen
    assert "GameSettings.TWEAKS[" not in screen and "TWEAKS.length" not in screen
    assert "if (Build.VERSION.SDK_INT >= 28) head.setAccessibilityHeading(true);" in screen
    assert "no-event-bars" not in screen and "eventBars" not in screen + settings
    assert 'p.getBoolean("tweak_" + t.name, false)' in settings
    assert 'e.putBoolean("tweak_" + t.name, tweaksOn.contains(t.name));' in settings


def backups(root: Path) -> list[tuple[str, dict[str, bytes]]]:
    """Each backup folder in `root`, newest first, with the files it holds."""
    return [(b.name, {f.name: f.read_bytes() for f in b.iterdir()}) for b in sorted(root.iterdir(), reverse=True)]


def test_back_up_now_copies_the_saves_once_and_again_after_a_change(java, tmp_path):
    overlay, root = tmp_path / "SAVEDATA", tmp_path / "save-backups"
    overlay.mkdir()
    story, chao = bytes(24576), bytes([7]) * 65536
    (overlay / "SONIC2B__S01").write_bytes(story)
    (overlay / "SONIC2B__ALF").write_bytes(chao)
    (overlay / "notes.txt").write_text("not a save")

    def tap():
        return java(overlay, root, driver="BackupDriver")

    first = tap()
    [(name, files)] = backups(root)
    assert files == {"SONIC2B__S01": story, "SONIC2B__ALF": chao}
    assert first == [f"Backed up 2 saves in backup {name}."]

    (overlay / "notes.txt").write_text("changed, still not a save")
    assert tap() == [f"These saves are already in backup {name}."]
    assert [n for n, _ in backups(root)] == [name]

    changed = story[:100] + b"\x01" + story[101:]
    (overlay / "SONIC2B__S01").write_bytes(changed)
    third = tap()
    [(newest, files), (older, _)] = backups(root)
    assert older == name
    assert files == {"SONIC2B__S01": changed, "SONIC2B__ALF": chao}
    assert third == [f"Backed up 2 saves in backup {newest}."]


def test_back_up_now_with_no_saves_in_the_overlay_makes_one_empty_backup(java, tmp_path):
    """The game folder is never written, so with none of the overlay's own saves an empty backup is
    the undo point: restoring it takes the game back to the game folder's saves."""
    overlay, root = tmp_path / "SAVEDATA", tmp_path / "save-backups"
    overlay.mkdir()
    (overlay / "notes.txt").write_text("not a save")
    first = java(overlay, root, driver="BackupDriver")
    [(name, files)] = backups(root)
    assert files == {}
    assert first == [f"The game's saves are all in the game folder, which the app never changes, so backup {name} "
                     "is empty. Restoring it returns to those saves."]
    assert java(overlay, root, driver="BackupDriver") == [
        f"The game's saves are all in the game folder, which the app never changes, so backup {name} was already "
        "empty. Restoring it returns to those saves."]
    assert [n for n, _ in backups(root)] == [name]


def test_back_up_now_keeps_the_last_ten_backups(java, tmp_path):
    overlay, root = tmp_path / "SAVEDATA", tmp_path / "save-backups"
    overlay.mkdir()
    for n in range(1, 13):
        (overlay / "SONIC2B__S01").write_bytes(bytes([n]) * 24576)
        java(overlay, root, driver="BackupDriver")
    kept = backups(root)
    assert [files["SONIC2B__S01"][0] for _, files in kept] == [12, 11, 10, 9, 8, 7, 6, 5, 4, 3]
