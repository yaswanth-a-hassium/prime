"""People: voice and face under one name, managed together."""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import people  # noqa: E402
import voiceid  # noqa: E402


def vec(*v):
    v = np.array(v, dtype=np.float32)
    return v / np.linalg.norm(v)


@pytest.fixture
def world(tmp_path):
    ids = voiceid.VoiceID.__new__(voiceid.VoiceID)
    ids._init_profiles(tmp_path / "v.json")
    ids.embed = lambda audio: vec(1, int(audio[0]), 0, 0)
    clip = lambda k: np.array([k] + [0] * 20000, dtype=np.int16)
    ids.enroll("Sai", [clip(0)], device="laptop")
    ids.enroll("Priya", [clip(5)])
    faces = tmp_path / "faces.json"
    faces.write_text(json.dumps({"Sai": {"vec": [1, 0], "owner": True}, "Jay": {"vec": [0, 1], "owner": False}}), encoding="utf-8")
    return ids, faces


def test_voices_and_faces_are_joined_by_name_with_the_owner_first(world):
    ids, faces = world
    got = people.list_people(ids, faces)
    assert [p["name"] for p in got] == ["Sai", "Jay", "Priya"]
    assert got[0] == {"name": "Sai", "owner": True, "voice": True, "voice_mics": ["laptop"], "face": True}
    assert got[1]["voice"] is False and got[1]["face"] is True and not got[1]["owner"]
    assert got[2]["voice"] is True and got[2]["face"] is False


def test_nobody_known_gives_an_empty_list(tmp_path):
    assert people.list_people(None, tmp_path / "none.json") == []


def test_a_person_is_renamed_in_voice_and_face_together(world):
    ids, faces = world
    assert people.rename(ids, "Sai", "Sai K", faces) == ""
    assert "Sai K" in ids.names() and "Sai K" in json.loads(faces.read_text(encoding="utf-8"))
    assert ids.devices_of("Sai K") == {"laptop": 1}                      # the per-mic prints came along
    assert people.list_people(ids, faces)[0]["name"] == "Sai K"


@pytest.mark.parametrize("old, new, message", [
    ("Sai", "Priya", "already"), ("Sai", "Jay", "already"), ("Sai", "   ", "empty"), ("Nobody", "X", "don't know"),
])
def test_renaming_never_merges_or_invents_people(world, old, new, message):
    ids, faces = world
    assert message in people.rename(ids, old, new, faces)
    assert sorted(ids.names()) == ["Priya", "Sai"]


def test_the_owner_is_changed_for_voice_and_face_together(world):
    ids, faces = world
    assert people.set_owner(ids, "Priya", faces) == ""
    assert ids.owner() == "Priya"
    assert all(rec["owner"] is False for rec in json.loads(faces.read_text(encoding="utf-8")).values())
    assert "know" in people.set_owner(ids, "Nobody", faces) and ids.owner() == "Priya"


def test_forgetting_a_voice_keeps_the_face_and_the_other_way_round(world):
    ids, faces = world
    assert people.forget(ids, "Sai", "voice", faces) == ["voice"]
    assert "Sai" not in ids.names() and "Sai" in json.loads(faces.read_text(encoding="utf-8"))
    assert people.forget(ids, "Sai", "face", faces) == ["face"]
    assert people.forget(ids, "Jay", "both", faces) == ["face"]
    assert people.forget(ids, "Nobody", "both", faces) == []


def test_a_missing_or_damaged_face_file_is_just_empty(tmp_path):
    bad = tmp_path / "f.json"
    bad.write_text("{oops", encoding="utf-8")
    assert people.list_people(None, bad) == []


def test_face_enrolment_explains_what_went_wrong(tmp_path):
    ok, why = people.enroll_face("Sai", script=tmp_path / "nothing.py")
    assert not ok and "faceid.py is missing" in why
    assert people.enroll_face("   ") == (False, "The name can't be empty.")


def test_face_enrolment_runs_the_tool_and_reports_its_last_line(tmp_path):
    tool = tmp_path / "faceid.py"
    tool.write_text("import sys\nprint('looking...')\nprint('Learned', sys.argv[2], 'owner' if '--owner' in sys.argv else 'guest')\n", encoding="utf-8")
    assert people.enroll_face("Priya", owner=True, script=tool, python=sys.executable) == (True, "Learned Priya owner")
    bad = tmp_path / "bad.py"
    bad.write_text("import sys\nsys.exit('Camera unavailable (another app may be using it).')\n", encoding="utf-8")
    ok, why = people.enroll_face("Priya", script=bad, python=sys.executable)
    assert not ok and "Camera unavailable" in why
