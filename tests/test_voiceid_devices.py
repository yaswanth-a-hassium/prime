"""Voiceprints per microphone: the same person sounds different through different mics."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import voiceid  # noqa: E402


def unit(*v):
    v = np.array(v, dtype=np.float32)
    return v / np.linalg.norm(v)


# what each "recording" sounds like to the speaker model (4 numbers stand in for 256)
SAI_LAPTOP = unit(1, 0, 0, 0)
SAI_USB = unit(.3, .95, 0, 0)            # the same person through a USB mic: only 0.3 like the laptop print
SAI_USB_2 = unit(.35, .93, .1, 0)
SAI_USB_3 = unit(.28, .96, -.05, 0)
PRIYA = unit(0, 0, 1, 0)
VECTORS = {1: SAI_LAPTOP, 2: SAI_USB, 3: SAI_USB_2, 4: SAI_USB_3, 5: PRIYA}


def clip(k):
    return np.array([k] + [0] * 20000, dtype=np.int16)


@pytest.fixture
def ids(tmp_path):
    v = voiceid.VoiceID.__new__(voiceid.VoiceID)       # no speaker model needed: voiceprints are supplied
    v._init_profiles(tmp_path / "p.json")
    v.embed = lambda audio: None if audio is None else VECTORS[int(audio[0])]
    v.tmp = tmp_path
    return v


def test_a_voice_learned_on_one_mic_is_missed_on_another_but_recognised_once_that_mic_is_learned(ids):
    ids.enroll("Sai", [clip(1)], device="laptop")
    assert ids.identify(clip(2), device="usb")[0] is None                 # a new mic: the general print doesn't match
    ids.imprint("Sai", "usb", [clip(2), clip(3), clip(4)])
    assert ids.identify(clip(2), device="usb")[0] == "Sai"                # imprinted: recognised
    assert ids.identify(clip(2), device="usb")[1] > .95


def test_the_other_microphone_keeps_working_after_a_new_one_is_learned(ids):
    ids.enroll("Sai", [clip(1)], device="laptop")
    ids.imprint("Sai", "usb", [clip(2), clip(3)])
    assert ids.identify(clip(1), device="laptop")[0] == "Sai"


def test_each_microphone_has_its_own_strictness(ids):
    ids.enroll("Sai", [clip(1)], device="laptop")
    ids.imprint("Sai", "usb", [clip(2), clip(3)])
    ids.enroll("Priya", [clip(5)])
    assert ids.identify(clip(3), device="usb", match=0.30)[0] == "Sai"
    assert ids.identify(clip(3), device="usb", match=0.999)[0] is None    # a stricter mic: no match
    assert ids.identify(clip(5))[0] == "Priya"


def test_a_guest_is_not_taken_for_the_owner_on_any_mic(ids):
    ids.enroll("Sai", [clip(1)], device="laptop")
    ids.imprint("Sai", "usb", [clip(2), clip(3)])
    assert ids.identify(clip(5), device="usb")[0] is None
    assert ids.identify(clip(5), device="laptop")[0] is None


def test_the_suggested_strictness_follows_how_steady_the_voice_is_on_that_mic(ids):
    used, suggested = ids.imprint("Sai", "usb", [clip(2), clip(3), clip(4)])
    assert used == 3 and voiceid.MATCH_RANGE[0] <= suggested <= voiceid.MATCH_RANGE[1]
    used, none = ids.imprint("Priya", "usb", [clip(5)])                   # one clip: nothing to compare it with
    assert used == 1 and none is None


def test_the_owner_check_uses_the_microphones_own_print(ids):
    ids.enroll("Sai", [clip(1)], device="laptop")
    ids.imprint("Sai", "usb", [clip(2), clip(3)])
    assert ids.is_owner(clip(4), device="usb")
    assert not ids.is_owner(clip(5), device="usb")


def test_microphone_prints_are_listed_renamed_and_forgotten(ids):
    ids.enroll("Sai", [clip(1)], device="laptop")
    ids.imprint("Sai", "usb", [clip(2), clip(3)])
    assert ids.devices_of("Sai") == {"laptop": 1, "usb": 2}
    assert ids.rename("Sai", "Sai K") and ids.names() == ["Sai K"] and ids.devices_of("Sai K")["usb"] == 2
    assert ids.forget_device("usb") == 1 and ids.devices_of("Sai K") == {"laptop": 1}
    assert ids.identify(clip(2), device="usb")[0] is None


def test_everything_survives_a_restart(ids):
    ids.enroll("Sai", [clip(1)], device="laptop")
    ids.imprint("Sai", "usb", [clip(2), clip(3)])
    again = voiceid.VoiceID.__new__(voiceid.VoiceID)
    again._init_profiles(ids.tmp / "p.json")
    again.embed = ids.embed
    assert again.devices_of("Sai") == {"laptop": 1, "usb": 2} and again.owner() == "Sai"
    assert again.identify(clip(2), device="usb")[0] == "Sai"


def test_rename_and_owner_changes_are_safe(ids):
    ids.enroll("Sai", [clip(1)])
    ids.enroll("Priya", [clip(5)])
    assert ids.owner() == "Sai"
    assert not ids.rename("Sai", "Priya") and not ids.rename("Nobody", "X")      # no clashes, no ghosts
    assert ids.set_owner("Priya") and ids.owner() == "Priya"
    assert not ids.set_owner("Nobody") and ids.owner() == "Priya"


def test_profiles_saved_before_microphones_existed_still_load(ids):
    import json
    ids.path.write_text(json.dumps({"Old": {"embedding": [1, 0, 0, 0], "samples": 3, "owner": True}}), encoding="utf-8")
    ids._init_profiles(ids.path)
    assert ids.names() == ["Old"] and ids.devices_of("Old") == {}
    assert ids.identify(clip(1), device="anything")[0] == "Old"          # falls back to the general print


# ---- how Jarvis uses it: the current mic's own voiceprint, strictness and clap level ----

import jarvis  # noqa: E402


class FakeEars:
    """A microphone called 'usb' that hears the given clips and claps."""
    def __init__(self, clips=(), clap=1500, key="usb"):
        self.device_key, self.clips, self.clap = key, iter(clips), clap
    def flush(self):
        pass
    def record(self, **kw):
        return next(self.clips, None)
    def measure_clap(self):
        return self.clap


@pytest.fixture
def jv(ids, monkeypatch):
    monkeypatch.setattr(jarvis, "beep", lambda: None)
    monkeypatch.setattr(jarvis, "CLAP_PEAK", 1200)
    j = jarvis.Jarvis(mute=True)
    j.voices, said = ids, []
    j.mouth.say = lambda text, then=None: said.append(text)
    j.said = said
    return j


def test_jarvis_checks_a_voice_against_the_current_mics_own_print_and_strictness(jv, ids):
    ids.enroll("Sai", [clip(1)], device="laptop")
    ids.imprint("Sai", "usb", [clip(2), clip(3)])
    jv.ears = FakeEars(key="usb")
    assert jv.identify(clip(2)) == "Sai (owner)"
    jv.mics.update("usb", match=0.999)                      # a very strict mic
    assert jv.identify(clip(2)) == "unrecognised"
    jv.ears = FakeEars(key="laptop")
    assert jv.identify(clip(1)) == "Sai (owner)"            # the other mic is untouched


def test_guided_set_up_learns_the_voice_the_strictness_and_the_clap(jv, ids):
    ids.enroll("Sai", [clip(1)], device="laptop")
    assert jv.learn_mic(FakeEars([clip(2), clip(3), clip(4)], clap=1500))
    rec = jv.mics.known()["usb"]
    assert rec["imprinted"] and rec["clap_peak"] == 1500 and voiceid.MATCH_RANGE[0] <= rec["match"] <= voiceid.MATCH_RANGE[1]
    assert jarvis.CLAP_PEAK == 1500                          # the clap level for this mic is in force now
    assert ids.devices_of("Sai")["usb"] == 3
    assert "Done" in jv.said[-1] and "claps" in jv.said[-1]


def test_set_up_without_two_clear_claps_keeps_the_standard_clap_level(jv, ids):
    ids.enroll("Sai", [clip(1)], device="laptop")
    assert jv.learn_mic(FakeEars([clip(2), clip(3)], clap=None))
    assert jv.mics.known()["usb"]["clap_peak"] is None and jarvis.CLAP_PEAK == 1200
    assert "standard level" in jv.said[-1]


def test_set_up_that_hears_nothing_changes_nothing(jv, ids):
    ids.enroll("Sai", [clip(1)], device="laptop")
    assert not jv.learn_mic(FakeEars([]))
    assert "usb" not in jv.mics.known() and "couldn't hear" in jv.said[-1]


def test_set_up_needs_a_voice_profile_first(jv):
    assert not jv.learn_mic(FakeEars([clip(2)]))
    assert "learn my voice" in jv.said[-1]


def test_a_new_microphone_is_announced_once_and_not_again_once_learned(jv):
    events = []
    jv.emit = lambda kind, data=None: events.append((kind, data))
    jv.ears = FakeEars(key="headset")
    assert jv.mic_changed() is True
    assert events[-1] == ("mic_changed", {"key": "headset", "new": True})
    assert jv.mic_changed() is False and events[-1][1]["new"] is False
    jv.mics.update("headset", imprinted=True, clap_peak=900)
    jv.mics.data.pop("other", None)
    jv.apply_mic()
    assert jarvis.CLAP_PEAK == 900


def test_learning_a_new_person_files_the_voice_under_this_mic(jv, ids):
    assert jv.learn_voice(FakeEars([clip(5), clip(5)]), "Priya") == 2
    assert ids.devices_of("Priya") == {"usb": 2} and jv.mics.imprinted("usb")
