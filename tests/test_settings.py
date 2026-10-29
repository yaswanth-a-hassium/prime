"""Settings: checked on the way in, saved safely, applied to the running assistant."""
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import jarvis  # noqa: E402
import settings  # noqa: E402


def test_every_default_passes_its_own_check():
    for key, value in settings.DEFAULTS.items():
        assert settings.clean(key, value) == value


@pytest.mark.parametrize("key, value, expected", [
    ("volume", 500, 150), ("volume", 0, 10), ("wake_threshold", 5, 0.9), ("speech_rate", -99, -40),
    ("wake_threshold", 0.555, 0.56), ("end_of_speech", 0.1, 0.4), ("clap_peak", 1200.4, 1200),
])
def test_numbers_are_kept_inside_their_range(key, value, expected):
    assert settings.clean(key, value) == expected


@pytest.mark.parametrize("key, value", [
    ("volume", "loud"), ("volume", True), ("humour", "sarcastic"), ("wake_by_clap", "yes"), ("quiet_from", "25:00"),
    ("quiet_to", "7pm"), ("model", "gpt"), ("name", "   "), ("nonsense", 1), ("approvals", None),
])
def test_bad_values_are_refused(key, value):
    with pytest.raises(ValueError):
        settings.clean(key, value)


def test_text_is_trimmed_and_capped():
    assert settings.clean("call_me", "  boss  ") == "boss"
    assert len(settings.clean("name", "x" * 100)) == 24


def test_a_missing_or_damaged_file_gives_the_defaults(tmp_path):
    assert settings.load(tmp_path / "none.json") == settings.DEFAULTS
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert settings.load(bad) == settings.DEFAULTS
    bad.write_text("[1, 2]", encoding="utf-8")
    assert settings.load(bad) == settings.DEFAULTS


def test_one_bad_value_in_the_file_does_not_spoil_the_rest(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"volume": "very", "call_me": "boss", "mystery": 1, "humour": "off"}), encoding="utf-8")
    got = settings.load(p)
    assert got["volume"] == 100 and got["call_me"] == "boss" and got["humour"] == "off" and "mystery" not in got


def test_save_then_load_round_trips_and_leaves_no_temp_file(tmp_path):
    p = tmp_path / "sub" / "s.json"
    values = {**settings.DEFAULTS, "voice": "en-US-GuyNeural", "volume": 80, "name": "Friday"}
    settings.save(values, p)
    assert settings.load(p) == values
    assert [f.name for f in p.parent.iterdir()] == ["s.json"]


@pytest.mark.parametrize("call_me, text, expected", [
    ("sir", "Done, sir.", "Done, sir."),
    ("boss", "Done, sir.", "Done, boss."),
    ("boss", "Sir, it's ready.", "Boss, it's ready."),
    ("", "Done, sir.", "Done."),
    ("", "Sir, it's ready.", "It's ready."),
    ("", "Yes sir, right away.", "Yes, right away."),
    ("Sai", "Good evening, sir. All systems are online.", "Good evening, Sai. All systems are online."),
    ("boss", "Nothing to change here.", "Nothing to change here."),
])
def test_the_title_in_a_spoken_line_follows_your_choice(call_me, text, expected):
    assert settings.address(text, call_me) == expected


@pytest.mark.parametrize("hour, minute, inside", [(23, 0, True), (2, 30, True), (6, 59, True), (7, 0, False), (12, 0, False), (22, 59, False)])
def test_quiet_hours_across_midnight(hour, minute, inside):
    values = {**settings.DEFAULTS, "quiet_on": True, "quiet_from": "23:00", "quiet_to": "07:00"}
    assert settings.in_quiet_hours(values, datetime(2026, 10, 26, hour, minute)) is inside


def test_quiet_hours_within_one_day_and_when_off():
    values = {**settings.DEFAULTS, "quiet_on": True, "quiet_from": "13:00", "quiet_to": "15:00"}
    assert settings.in_quiet_hours(values, datetime(2026, 10, 26, 14, 0))
    assert not settings.in_quiet_hours(values, datetime(2026, 10, 26, 16, 0))
    assert not settings.in_quiet_hours({**values, "quiet_on": False}, datetime(2026, 10, 26, 14, 0))


def test_style_rules_follow_the_settings():
    plain = settings.style_rules(settings.DEFAULTS)
    assert plain["assistant_name"] == "Prime" and '"sir"' in plain["address_rule"] and plain["humour_rule"] == ""
    custom = settings.style_rules({**settings.DEFAULTS, "name": "Friday", "call_me": "", "humour": "off", "reply_length": "detailed"})
    assert custom["assistant_name"] == "Friday" and "title" in custom["address_rule"]
    assert "humour off" in custom["humour_rule"] and "Two to five" in custom["length_rule"]


@pytest.fixture
def restore_jarvis():
    keep = {k: getattr(jarvis, k) for k in ("VOICE", "TELUGU_VOICE", "VOLUME", "WAKE_THRESHOLD", "WAKE_BY_NAME", "WAKE_BY_CLAP",
                                            "CLAP_PEAK", "END_OF_SPEECH", "FOLLOW_UP_SECONDS", "AUTO_APPROVE", "MODEL",
                                            "SESSION_IDLE_SECONDS", "SETTINGS", "VOICE_RATE", "VOICE_PITCH")}
    yield
    for k, v in keep.items():
        setattr(jarvis, k, v)
    jarvis.set_assistant_name("Jarvis")


def test_applying_settings_changes_the_running_assistant(restore_jarvis):
    class Desk:
        GAME_MODE = True
    desk = Desk()
    values = {**settings.DEFAULTS, "voice": "en-US-GuyNeural", "telugu_voice": "te-IN-ShrutiNeural", "speech_rate": 10,
              "speech_pitch": -5, "volume": 50, "approvals": "ask", "model": "haiku", "fresh_chat_minutes": 5,
              "game_mode": False, "wake_by_clap": False, "clap_peak": 2000, "end_of_speech": 1.2}
    settings.apply(values, jarvis, desk)
    assert jarvis.VOICE == "en-US-GuyNeural" and jarvis.VOLUME == 0.5 and jarvis.MODEL == "haiku"
    assert jarvis.TELUGU_VOICE == {"voice": "te-IN-ShrutiNeural", "rate": "+4%", "pitch": "-13Hz"}
    assert jarvis.english_voice_args() if hasattr(jarvis, "english_voice_args") else True
    assert jarvis.Mouth.english_voice() == {"voice": "en-US-GuyNeural", "rate": "+10%", "pitch": "-5Hz"}
    assert jarvis.AUTO_APPROVE == "off" and jarvis.SESSION_IDLE_SECONDS == 300
    assert jarvis.WAKE_BY_CLAP is False and jarvis.CLAP_PEAK == 2000 and jarvis.END_OF_SPEECH == 1.2
    assert desk.GAME_MODE is False


def test_a_new_name_is_answered_to_alongside_jarvis(restore_jarvis):
    settings.apply({**settings.DEFAULTS, "name": "Friday"}, jarvis)
    assert jarvis.WAKE_NAME.search("Friday, open Chrome") and jarvis.WAKE_NAME.search("hey jarvis")
    assert jarvis.strip_wake_name("Friday, what time is it") == "what time is it"
    assert "You are Friday" in jarvis.Brain._system_prompt()


def test_the_name_goes_back_to_jarvis(restore_jarvis):
    settings.apply({**settings.DEFAULTS, "name": "Friday"}, jarvis)
    settings.apply(settings.DEFAULTS, jarvis)
    assert not jarvis.WAKE_NAME.search("Friday, open Chrome") and jarvis.WAKE_NAME.search("Jarvis")


def test_volume_scales_speech_without_wrapping_around():
    import numpy as np
    loud = np.array([30000, -30000, 100], dtype=np.int16)
    old = jarvis.VOLUME
    try:
        jarvis.VOLUME = 1.5
        assert list(jarvis.Mouth._louder(loud)) == [32767, -32768, 150]
        jarvis.VOLUME = 0.5
        assert list(jarvis.Mouth._louder(loud)) == [15000, -15000, 50]
    finally:
        jarvis.VOLUME = old


def test_quiet_hours_keep_it_on_screen_but_silent(monkeypatch):
    shown, played = [], []
    mouth = jarvis.Mouth(emit=lambda kind, data=None: shown.append(data))
    monkeypatch.setattr(jarvis.sd, "play", lambda *a, **k: played.append(1))
    monkeypatch.setattr(jarvis, "SETTINGS", {**settings.DEFAULTS, "quiet_on": True, "quiet_from": "00:00", "quiet_to": "23:59"})
    mouth.say("Hello, sir.")
    assert shown and shown[0]["text"] == "Hello, sir." and not played


def test_the_title_is_applied_to_what_is_said_and_shown(monkeypatch):
    shown = []
    mouth = jarvis.Mouth(mute=True, emit=lambda kind, data=None: shown.append(data))
    monkeypatch.setattr(jarvis, "SETTINGS", {**settings.DEFAULTS, "call_me": "boss"})
    mouth.say("Done, sir.")
    assert shown[0]["text"] == "Done, boss."
