"""The macOS pieces of Prime: the robot voice, the Siri Shortcut bridge, Prime.app, the logo and the Mac helpers.
(Everything that needs a real Mac, such as AppKit, the Keychain or the menu bar, is checked on the Mac itself.)"""
import plistlib
import socket
import sys
import threading
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import desktop  # noqa: E402
import jarvis  # noqa: E402
import macos  # noqa: E402
import make_logo  # noqa: E402
import robovoice  # noqa: E402
import settings  # noqa: E402
import siri_bridge  # noqa: E402

RATE = 24000


def voice_like(seconds=1.5, f0=110.0):
    """A buzzy, vowel-ish test signal at f0 with a slow loudness wobble."""
    t = np.arange(int(RATE * seconds)) / RATE
    x = sum(np.sin(2 * np.pi * f0 * k * t) / k for k in range(1, 12)) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))
    return (x / np.abs(x).max() * 12000).astype(np.int16)


def pitch(x):
    x = x.astype(np.float64)[RATE // 4: RATE // 4 + 4096]
    x = x - x.mean()
    ac = np.correlate(x, x, "full")[len(x) - 1:]
    lo, hi = RATE // 400, RATE // 50
    return RATE / (lo + int(np.argmax(ac[lo:hi])))


# ---- robot voice -----------------------------------------------------------------------------------------

def test_robot_voice_keeps_length_and_type():
    pcm = voice_like()
    for depth in (0, 0.7, 1):
        out = robovoice.apply(pcm, RATE, depth=depth, semitones=-5)
        assert out.dtype == np.int16 and len(out) == len(pcm)
        assert 0 < np.abs(out.astype(np.int32)).max() <= 32767


def test_robot_voice_is_lower():
    pcm = voice_like(f0=120.0)
    low = robovoice.apply(pcm, RATE, depth=0, semitones=-5)
    assert pitch(pcm) == pytest.approx(120, rel=0.05)
    assert pitch(low) == pytest.approx(120 * 2 ** (-5 / 12), rel=0.06)


def test_robot_voice_handles_tiny_and_empty_clips():
    assert len(robovoice.apply(np.zeros(0, np.int16), RATE)) == 0
    assert len(robovoice.apply(np.zeros(300, np.int16), RATE)) == 300


def test_robot_effect_follows_the_settings(monkeypatch):
    keep = {k: getattr(jarvis, k) for k in ("ROBOT_VOICE", "ROBOT_DEPTH", "ROBOT_PITCH")}
    try:
        settings.apply({**settings.DEFAULTS, "robot_voice": False, "robot_depth": 40, "robot_pitch": -8}, jarvis)
        assert (jarvis.ROBOT_VOICE, jarvis.ROBOT_DEPTH, jarvis.ROBOT_PITCH) == (False, 0.4, -8)
        pcm = voice_like()
        assert jarvis.Mouth._robot(pcm) is pcm                 # off: the voice is left alone
        jarvis.ROBOT_VOICE = True
        assert not np.array_equal(jarvis.Mouth._robot(pcm), pcm)
    finally:
        for k, v in keep.items():
            setattr(jarvis, k, v)


def test_robot_settings_are_checked():
    assert settings.clean("robot_depth", 250) == 100 and settings.clean("robot_pitch", 3) == 0
    with pytest.raises(ValueError):
        settings.clean("robot_voice", "yes")


# ---- the name ---------------------------------------------------------------------------------------------

def test_prime_wakes_by_name_and_is_stripped():
    jarvis.set_assistant_name("Prime")
    assert jarvis.WAKE_NAME.search("prime, what's the time")
    assert jarvis.strip_wake_name("Prime, what's the time?") == "what's the time?"
    assert jarvis.strip_wake_name("Hey Jarvis, open Safari") == "open Safari"   # the trained phrase still works


# ---- Siri Shortcut bridge ----------------------------------------------------------------------------------

needs_unix_sockets = pytest.mark.skipif(not hasattr(socket, "AF_UNIX") or sys.platform == "win32",
                                        reason="Unix sockets (the Mac has them)")


@needs_unix_sockets
def test_bridge_round_trip_and_private_socket(tmp_path):
    import os
    import stat
    path = tmp_path / "p.sock"
    seen = []
    server = siri_bridge.Server(lambda req: seen.append(req) or {"ok": True, "reply": req["text"].upper()}, path).start()
    try:
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        assert siri_bridge.send({"cmd": "ask", "text": "hello prime"}, path=path) == {"ok": True, "reply": "HELLO PRIME"}
        assert seen[0]["text"] == "hello prime"
    finally:
        server.stop()
    assert not path.exists()


@needs_unix_sockets
def test_bridge_reports_handler_errors(tmp_path):
    def boom(req):
        raise RuntimeError("no brain")
    server = siri_bridge.Server(boom, tmp_path / "p.sock").start()
    try:
        assert siri_bridge.send({"cmd": "ask", "text": "x"}, path=tmp_path / "p.sock") == {"ok": False, "error": "no brain"}
    finally:
        server.stop()


def test_bridge_requests_reach_jarvis_as_an_unverified_guest_unless_trusted(monkeypatch):
    import jarvis_app
    calls = []

    class FakeJarvis:
        def handle(self, text, **kw):
            calls.append((text, kw))
            kw["reply_box"]["reply"] = "It's four."

    api = jarvis_app.Api.__new__(jarvis_app.Api)
    api._jarvis, api._settings = FakeJarvis(), dict(settings.DEFAULTS)
    api.show = lambda: calls.append("show")
    assert api._bridge_request({"cmd": "ask", "text": "  what   time is it "}) == {"ok": True, "reply": "It's four."}
    text, kw = calls[-1]
    assert text == "what time is it" and kw["trusted"] is False and "not verified" in kw["speaker"] and not kw["quiet"]
    api._settings["siri_trusted"] = True
    api._bridge_request({"cmd": "ask", "text": "hi", "speak": False})
    assert calls[-1][1]["trusted"] is True and calls[-1][1]["speaker"] is None and calls[-1][1]["quiet"] is True
    assert api._bridge_request({"cmd": "show"}) == {"ok": True} and "show" in calls
    assert api._bridge_request({"cmd": "ask", "text": ""})["ok"] is False


def test_the_reply_box_gets_the_answer_and_quiet_stays_silent(monkeypatch):
    said = []
    monkeypatch.setattr(jarvis.Mouth, "say", lambda self, text, then="idle", silent=False: said.append((text, silent)))
    monkeypatch.setattr(jarvis.Brain, "ask", lambda brain, text, **kw: "Four o'clock.")
    j = jarvis.Jarvis(mute=True)
    box = {}
    j.handle("what time is it", reply_box=box, quiet=True, brain="spawn", trusted=False)
    assert box["reply"] == "Four o'clock." and said[-1] == ("Four o'clock.", True)


# ---- Prime.app, start at login --------------------------------------------------------------------------

def test_prime_app_bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop, "APP_BUNDLE", tmp_path / "Prime.app")
    monkeypatch.setattr(macos, "IS_MAC", False)          # no codesign here
    desktop.make_shortcuts()
    contents = tmp_path / "Prime.app" / "Contents"
    info = plistlib.loads((contents / "Info.plist").read_bytes())
    assert info["CFBundleIdentifier"] == "com.hassium.prime" and info["CFBundleExecutable"] == "Prime"
    for key in ("NSMicrophoneUsageDescription", "NSCameraUsageDescription", "NSAppleEventsUsageDescription"):
        assert info[key]
    launcher = (contents / "MacOS" / "Prime").read_text()
    assert launcher.startswith("#!/bin/zsh") and "prime_app.py" in launcher and "exec " in launcher
    assert (contents / "Resources" / "Prime.icns").stat().st_size > 10_000


def test_login_item(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop, "APP_BUNDLE", tmp_path / "Prime.app")
    monkeypatch.setattr(desktop, "LAUNCH_AGENT", tmp_path / "agent.plist")
    monkeypatch.setattr(macos, "IS_MAC", False)
    assert desktop.enable_autostart(hidden=True) == "" and desktop.autostart_enabled()
    agent = plistlib.loads((tmp_path / "agent.plist").read_bytes())
    assert agent["RunAtLoad"] and agent["ProgramArguments"][-1] == "--startup"
    desktop.disable_autostart()
    assert not desktop.autostart_enabled()


# ---- the logo ---------------------------------------------------------------------------------------------

def test_hassium_has_108_electrons_in_the_right_shells():
    assert make_logo.SHELLS == (2, 8, 18, 32, 32, 14, 2) and len(make_logo.electrons()) == 108
    svg = (Path(make_logo.ASSETS) / "prime_logo.svg").read_text(encoding="utf-8")
    assert svg.count('fill="url(#electron)"') == 108 and ">Hs<" in svg


# ---- Mac helpers ------------------------------------------------------------------------------------------

def test_key_names_become_mac_keys():
    assert [macos.key(k) for k in ("cmd", "Win", "alt", "ctrl", "return", "a")] == \
        ["command", "command", "option", "ctrl", "enter", "a"]


def test_apps_are_found_with_their_display_names(tmp_path, monkeypatch):
    app = tmp_path / "Visual Studio Code.app" / "Contents"
    app.mkdir(parents=True)
    (app / "Info.plist").write_bytes(plistlib.dumps({"CFBundleName": "Code", "CFBundleDisplayName": "Visual Studio Code"}))
    (tmp_path / "Utilities" / "Terminal.app").mkdir(parents=True)
    monkeypatch.setattr(macos, "APP_DIRS", [tmp_path])
    names = [a["Name"] for a in macos.apps()]
    assert names == ["Terminal", "Visual Studio Code"]


def test_applescript_strings_are_escaped():
    assert macos._as_string('say "hi" \\ bye') == '"say \\"hi\\" \\\\ bye"'


def test_off_the_mac_nothing_is_fullscreen_and_permissions_pass(monkeypatch):
    monkeypatch.setattr(macos, "IS_MAC", False)
    assert macos.frontmost_is_fullscreen() is False and macos.accessibility_ok() and macos.windows() == []
