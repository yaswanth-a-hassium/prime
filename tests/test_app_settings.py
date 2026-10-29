"""The Settings page's calls into the app: save and apply at once, refuse bad values, manage microphones."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import jarvis  # noqa: E402
import jarvis_app  # noqa: E402
import mics  # noqa: E402
import settings  # noqa: E402
import voiceid  # noqa: E402
from memory.store import Store  # noqa: E402

USB, LAPTOP = "Microphone (USB PnP Audio Device)", "Microphone Array (Intel Smart Sound)"


class FakeEars:
    def __init__(self, key=USB):
        self.device_key, self.opened, self.job = key, [], None

    def reopen(self, device=""):
        self.opened.append(device)
        self.device_key = device or mics.default_key()
        return self.device_key

    def run_in_loop(self, job):
        self.job = job


@pytest.fixture
def api(tmp_path, monkeypatch):
    keep = {k: getattr(jarvis, k) for k in ("VOICE", "TELUGU_VOICE", "VOLUME", "WAKE_THRESHOLD", "WAKE_BY_NAME", "WAKE_BY_CLAP",
                                            "CLAP_PEAK", "END_OF_SPEECH", "FOLLOW_UP_SECONDS", "AUTO_APPROVE", "MODEL",
                                            "SESSION_IDLE_SECONDS", "SETTINGS", "VOICE_RATE", "VOICE_PITCH")}
    monkeypatch.setattr(mics, "connected", lambda: [{"key": USB, "default": True}, {"key": LAPTOP, "default": False}])
    monkeypatch.setattr(mics, "default_key", lambda: USB)
    monkeypatch.setattr(mics, "stream_index", lambda key: {USB: 1, LAPTOP: 0}.get(key))
    a = jarvis_app.Api.__new__(jarvis_app.Api)                  # no window, tray icon or microphone needed
    a._settings, a._events = settings.load(), []
    a._emit = lambda kind, data=None: a._events.append((kind, data))
    a._store = Store(tmp_path / "j.db")
    a._jarvis = jarvis.Jarvis(mute=True)
    a._ears = a._jarvis.ears = FakeEars()
    ids = voiceid.VoiceID.__new__(voiceid.VoiceID)
    ids._init_profiles(tmp_path / "v.json")
    ids.embed = lambda audio: np.array([1, 0, 0, 0], dtype=np.float32)
    a._jarvis.voices = ids
    yield a
    a._store.close()
    for k, v in keep.items():
        setattr(jarvis, k, v)
    jarvis.set_assistant_name("Jarvis")


def test_a_setting_is_saved_and_takes_effect_at_once(api):
    assert api.settings_set("volume", 70) == {"ok": True, "value": 70}
    assert jarvis.VOLUME == 0.7 and settings.load()["volume"] == 70


def test_a_bad_value_is_refused_and_nothing_changes(api):
    got = api.settings_set("humour", "sarcastic")
    assert got["ok"] is False and "humour" in got["error"]
    assert settings.load()["humour"] == "normal"


def test_renaming_tells_the_page(api):
    api.settings_set("name", "Friday")
    assert ("renamed", "Friday") in api._events and jarvis.WAKE_NAME.search("friday, hello")


def test_the_settings_page_gets_values_defaults_and_voice_lists(api):
    got = api.settings_get()
    assert got["values"]["voice"] == "en-US-ChristopherNeural" and got["defaults"] == settings.DEFAULTS
    assert "en-US-GuyNeural" in got["voices"] and "te-IN-ShrutiNeural" in got["telugu_voices"]


def test_a_clap_level_learned_for_this_mic_beats_the_general_setting(api):
    api._jarvis.mics.update(USB, clap_peak=1700)
    api.settings_set("clap_peak", 3000)
    assert jarvis.CLAP_PEAK == 1700
    api._jarvis.mics.update(USB, clap_peak=None)
    api.settings_set("clap_peak", 2500)
    assert jarvis.CLAP_PEAK == 2500


def test_choosing_a_microphone_switches_to_it_and_unplugged_falls_back(api):
    api._settings["mic_device"] = LAPTOP
    api._follow_mic_setting()
    assert api._ears.opened == [LAPTOP] and api._ears.device_key == LAPTOP
    api._settings["mic_device"] = "Headset that is not plugged in"
    api._follow_mic_setting()
    assert api._ears.opened[-1] == "" and api._ears.device_key == USB


def test_nothing_is_reopened_when_the_microphone_is_already_right(api):
    api._follow_mic_setting()
    assert api._ears.opened == []


def test_the_mic_list_shows_plugged_in_and_remembered_mics(api):
    api._jarvis.mics.update("Old headset", imprinted=True, match=0.4)
    got = api.mics_list()
    assert [m["key"] for m in got] == [USB, LAPTOP, "Old headset"]
    assert got[0]["current"] and got[0]["connected"] and not got[2]["connected"] and got[2]["match"] == 0.4


def test_mic_strictness_and_clap_level_are_kept_in_range_and_can_be_reset(api):
    api.mic_set(USB, "match", 0.99)
    assert api._jarvis.mics.match_for(USB) == 0.6
    api.mic_set(USB, "clap_peak", 50)
    assert api._jarvis.mics.clap_for(USB) == 300 and jarvis.CLAP_PEAK == 300
    api.mic_set(USB, "match", None)
    assert api._jarvis.mics.match_for(USB) is None
    assert api.mic_set(USB, "volume", 1)["ok"] is False


def test_forgetting_a_mic_removes_its_voiceprints_but_not_the_person(api):
    clip = np.zeros(20000, dtype=np.int16)
    api._jarvis.voices.enroll("Sai", [clip], device=USB)
    api._jarvis.voices.enroll("Sai", [clip], device=LAPTOP)
    api._jarvis.mics.update(USB, imprinted=True)
    got = api.mic_forget(USB)
    assert got["voiceprints"] == 1 and api._jarvis.voices.devices_of("Sai") == {LAPTOP: 1}
    assert "Sai" in api._jarvis.voices.names()


def test_guided_set_up_is_handed_to_the_listening_loop(api):
    assert api.mic_learn() == {"ok": True} and callable(api._ears.job)
    assert api.person_add_voice("Priya") == {"ok": True}
    assert api.person_add_voice("   ")["ok"] is False
    api._ears = None
    assert api.mic_learn()["ok"] is False
