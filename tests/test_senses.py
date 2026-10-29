"""Voice profiles, reminders and the camera tool.

    .venv\\Scripts\\python -m pytest tests -q
"""
import asyncio
import base64
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import jarvis  # noqa: E402
import paths  # noqa: E402
import policy  # noqa: E402
import tools  # noqa: E402
import voiceid  # noqa: E402
from memory.store import Store  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_listening_state():
    """Each test starts with no echo window and no open follow-up window."""
    jarvis.ECHO.update(until=0.0, follow_until=0.0)
    yield
    jarvis.ECHO.update(until=0.0, follow_until=0.0)


def call(t, args):
    return asyncio.run(t.handler(args))["content"][-1]["text"]


# ---- reminders ----

@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "j.db")
    tools.use_store(s)
    yield s
    tools.use_store(None)
    s.close()


def test_reminder_set_list_cancel(store):
    assert call(tools.set_reminder, {"text": "stretch", "in_minutes": 1}).startswith("Reminder #")
    later = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d 09:30")
    assert "09:30" in call(tools.set_reminder, {"text": "dentist", "at": later})
    listed = call(tools.list_reminders, {})
    assert "stretch" in listed and "dentist" in listed
    rid = store.reminders()[0]["id"]
    assert "Cancelled" in call(tools.cancel_reminder, {"id": rid})
    assert len(store.reminders()) == 1


def test_reminder_rejects_past_and_bad_times(store):
    assert "past" in call(tools.set_reminder, {"text": "x", "at": "2001-01-01 10:00"})
    assert "Couldn't read" in call(tools.set_reminder, {"text": "x", "at": "teatime"})
    assert store.reminders() == []


def test_due_reminder_is_spoken_once(store, monkeypatch):
    said = []
    monkeypatch.setattr(jarvis, "REMINDER_CHECK_SECONDS", 0.05)
    monkeypatch.setattr(jarvis.Mouth, "say", lambda self, text, then="idle", silent=False: said.append(text))
    store.add_reminder(datetime.now().replace(second=0, microsecond=0), "take the pizza out")
    store.add_reminder(datetime.now() - timedelta(hours=2), "water the plants")
    store.add_reminder(datetime.now() + timedelta(hours=1), "not yet")
    jarvis.Jarvis(mute=True, store=store)
    time.sleep(0.4)  # several passes of the loop
    assert sorted(said) == ["Reminder, sir: take the pizza out.",
                            f"You missed a reminder from {(datetime.now() - timedelta(hours=2)):%H:%M}, sir: water the plants."]
    assert [r["text"] for r in store.reminders()] == ["not yet"]


def test_new_tools_are_routine():
    for t in ("look", "set_reminder", "list_reminders", "cancel_reminder"):
        assert policy.classify(f"mcp__jarvis__{t}", {})[0] == "A"


# ---- camera ----

def test_look_reports_a_missing_camera(monkeypatch):
    def broken(frames, interval):
        raise RuntimeError("no webcam found, or another app is using it")
    monkeypatch.setattr(tools, "_capture", broken)
    assert "Couldn't use the webcam" in call(tools.look, {})


def test_look_returns_images(monkeypatch):
    seen = {}

    def fake(frames, interval):
        seen.update(frames=frames, interval=interval)
        return [b"\xff\xd8jpeg"] * frames
    monkeypatch.setattr(tools, "_capture", fake)
    out = asyncio.run(tools.look.handler({"frames": 99, "interval": 0}))["content"]
    assert seen == {"frames": 6, "interval": 0.3}          # clamped
    assert [c["type"] for c in out] == ["image"] * 6 + ["text"]


# ---- watching ----

def fake_watcher(frames, verdicts, monkeypatch):
    """A Watcher fed `frames` (grey levels) whose judge answers from `verdicts`; returns (watcher, said, judged)."""
    import watcher
    said, judged = [], []
    w = watcher.Watcher("claude", said.append,
                        judge_fn=lambda jpeg, cond, cli: (judged.append(jpeg), verdicts.pop(0))[1])

    def frames_gen():
        for level in frames:
            if w._stop.is_set():
                return
            yield np.full((36, 64), level, dtype="float32"), bytes([level])
    monkeypatch.setattr(w, "_frames", frames_gen)
    return w, said, judged


def test_watch_only_judges_changed_frames_and_announces(monkeypatch):
    w, said, judged = fake_watcher([10, 11, 10, 200, 200, 90], [(False, "sitting"), (False, "sitting"),
                                                                (True, "you stood up")], monkeypatch)
    w.start("the person is standing", 5)
    w._thread.join(2)
    assert judged == [bytes([10]), bytes([200]), bytes([90])]   # tiny changes skipped locally
    assert said == ["Heads up, sir: you stood up."]
    assert not w.active


def test_watch_reports_on_and_off_for_the_banner(monkeypatch):
    w, said, _ = fake_watcher([10, 200], [(False, ""), (True, "you stood up")], monkeypatch)
    states = []
    w.on_state = states.append
    w.start("the person is standing", 5)
    w._thread.join(2)
    assert states == ["the person is standing", None]


def test_watch_times_out(monkeypatch):
    w, said, _ = fake_watcher([10, 10], [(False, "")], monkeypatch)
    frames = w._frames

    def expired_frames():  # the time limit has passed by the first frame (no race with the thread)
        w.until = 0
        yield from frames()
    monkeypatch.setattr(w, "_frames", expired_frames)
    w.start("a cat appears", 1)
    w._thread.join(2)
    assert "didn't see" in said[0]


def test_watch_tools_and_stop(monkeypatch):
    import watcher
    w = watcher.Watcher("claude", lambda t: None)
    started = []
    monkeypatch.setattr(w, "start", lambda c, m: started.append((c, m)) or 10)
    tools.use_watcher(w)
    assert "Watching for" in call(tools.watch_for, {"condition": "the person is standing"})
    assert started == [("the person is standing", 10)]
    assert "Nothing" in call(tools.stop_watching, {})
    for t in ("watch_for", "stop_watching"):
        assert policy.classify(f"mcp__jarvis__{t}", {})[0] == "A"


# ---- voice profiles ----

needs_model = pytest.mark.skipif(not voiceid.MODEL.exists(), reason="speaker model not downloaded")


def clip(seed, seconds=2.0):
    """A deterministic stand-in for speech (the mechanics, not real voice accuracy)."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * voiceid.RATE)) / voiceid.RATE
    tone = sum(np.sin(2 * np.pi * f * t) for f in rng.uniform(100, 900, 6))
    return (3000 * tone / 6 + rng.normal(0, 300, t.size)).astype(np.int16)


@needs_model
def test_enroll_identify_owner_forget(tmp_path):
    v = voiceid.VoiceID(profiles=tmp_path / "p.json")
    assert v.is_owner(clip(1))                    # no profiles yet: nothing to check against
    assert v.enroll("Sai", [clip(1), clip(1, 2.5)]) == 2
    assert v.owner() == "Sai"
    assert v.identify(clip(1))[0] == "Sai"
    assert v.enroll("Priya", [clip(7)]) == 1
    assert v.owner() == "Sai"                     # first enrolled stays the owner
    assert voiceid.VoiceID(profiles=tmp_path / "p.json").names() == ["Sai", "Priya"]  # persisted
    assert v.forget("Sai") and v.owner() is None
    v.enroll("Priya", [clip(7)])
    assert v.owner() is None                      # existing guests don't inherit ownership
    v.enroll("Arun", [clip(9)])
    assert v.owner() == "Arun"                    # the next new voice does


@needs_model
def test_too_short_clip_is_not_identified(tmp_path):
    v = voiceid.VoiceID(profiles=tmp_path / "p.json")
    v.enroll("Sai", [clip(1)])
    assert v.identify(clip(1, 0.3)) == (None, 0.0)
    assert not v.is_owner(clip(1, 0.3))


@pytest.mark.parametrize("text, learn, forget", [
    ("hey jarvis learn my voice i'm sai", True, False),
    ("learn a new voice this is priya", True, False),
    ("remember priya's voice", True, False),
    ("forget my voice", False, True),
    ("forget priya's voice", False, True),
    ("save this voice memo to my desktop", False, False),
    ("turn the voice volume down", False, False),
])
def test_voice_command_patterns(text, learn, forget):
    assert bool(jarvis.LEARN_VOICE.search(text)) == learn
    assert bool(jarvis.FORGET_VOICE.search(text)) == forget


# ---- background audio (TV, videos) in the follow-up window ----

def ears_with(frames):
    """An Ears without a microphone, fed these 80 ms frames."""
    import queue
    e = jarvis.Ears.__new__(jarvis.Ears)
    e.q, e.threshold = queue.Queue(), 400.0
    for f in frames:
        e.q.put(f)
    return e


LOUD = np.full(jarvis.FRAME, 3000, dtype=np.int16)
QUIET = np.zeros(jarvis.FRAME, dtype=np.int16)


def test_record_flags_audio_that_never_pauses():
    e = ears_with([LOUD] * 400)                                   # 32 s of nonstop sound
    audio = e.record(start_timeout=5, max_len=12)
    assert e.cut_off and 11 < len(audio) / jarvis.RATE < 13


def test_record_does_not_flag_a_normal_sentence():
    e = ears_with([LOUD] * 30 + [QUIET] * 30)                     # 2.4 s of speech, then silence
    e.record(start_timeout=5, max_len=12)
    assert not e.cut_off


@pytest.mark.parametrize("cut_off, speaker, ignored", [
    (True, None, True),                  # TV, no voices enrolled: caught by the no-pause rule
    (False, None, False),                # a normal follow-up before any voice is enrolled
    (False, "unrecognised", True),       # unknown voice once voices are enrolled
    (False, "Sai (owner)", False),
    (False, "Priya", False),             # a known guest may follow up
    (True, "Sai (owner)", True),         # even the owner: nonstop audio is background
])
def test_background_audio_rule(cut_off, speaker, ignored):
    j = jarvis.Jarvis(mute=True)
    assert bool(j.background_audio(cut_off, speaker)) == ignored


class Done(Exception):
    pass


class ScriptedEars:
    """Ears that play back a script: each wait_for_wake() returns the next wake result (None = beep, then
    record() gives the request). Raises Done when the script runs out."""
    def __init__(self, wakes, recorded_text="", cut_off=False):
        self.wakes, self.recorded_text, self.cut_off, self.recorded = list(wakes), recorded_text, cut_off, 0
    def wait_for_wake(self):
        if not self.wakes:
            raise Done
        w = self.wakes.pop(0)
        if isinstance(w, tuple) and w[0] == "follow":
            self.cut_off = w[3] if len(w) > 3 else False
            return w[:3]
        return w
    def record(self, **kw):
        self.recorded += 1
        return np.zeros(16000, dtype=np.int16)
    def transcribe(self, audio):
        return self.recorded_text
    def flush(self):
        pass


def voice_jarvis(monkeypatch):
    monkeypatch.setattr(jarvis, "beep", lambda: None)
    j = jarvis.Jarvis(mute=True)
    j._load_voices = lambda: None
    submitted = []
    j.submit = lambda text, voice=False, speaker=None, ack=True, **kw: submitted.append((text, ack))
    return j, submitted


def test_tv_in_the_follow_up_window_is_ignored(monkeypatch):
    """The real incident: a reply, then a TV talking through the follow-up window."""
    j, submitted = voice_jarvis(monkeypatch)
    audio = np.zeros(16000, dtype=np.int16)
    ears = ScriptedEars([None, ("follow", audio, "You know Tommy Bulk production. Thank you, Jane...", True)],
                        recorded_text="What does it help?")
    with pytest.raises(Done):
        j.run_voice(ears)
    assert submitted == [("What does it help?", True)]


def test_a_real_follow_up_is_accepted_without_the_wake_word(monkeypatch):
    j, submitted = voice_jarvis(monkeypatch)
    audio = np.zeros(16000, dtype=np.int16)
    ears = ScriptedEars([("follow", audio, "and how long will that take?", False)])
    with pytest.raises(Done):
        j.run_voice(ears)
    assert submitted == [("and how long will that take?", False)]  # no work line for follow-ups


def test_ignore_reply_keeps_jarvis_silent(tmp_path, monkeypatch):
    said = []
    monkeypatch.setattr(jarvis.Mouth, "say", lambda self, text, then="idle", silent=False: said.append(text))
    j = jarvis.Jarvis(mute=True)
    j.brain.ask = lambda text, on_work=None, **kw: "[ignore]"
    assert j.handle("Thank you, Jane. Let's cut this.", voice=True) is False
    assert said == []


def test_follow_ups_get_no_spoken_acknowledgement(monkeypatch):
    seen = {}
    j = jarvis.Jarvis(mute=True)
    j.mouth.say = lambda *a, **k: None
    j.brain.ask = lambda text, on_work=None, **kw: seen.update(on_work=on_work) or "ok"
    j.handle("hello", voice=True, ack=False)
    assert seen["on_work"] is None
    j.handle("hello", voice=True)
    assert seen["on_work"] is not None


# ---- other ways to wake: name in a sentence, double clap ----

@pytest.mark.parametrize("heard, command", [
    ("Jarvis, what's the time?", "what's the time?"),
    ("Hey Jarvis what's the weather tomorrow", "what's the weather tomorrow"),
    ("Okay Jarvis, open Spotify.", "open Spotify."),
    ("Can you check my reminders, Jarvis?", "Can you check my reminders?"),
    ("Jarvis", ""),
    ("జార్విస్, రేపు మీటింగ్ ఎప్పుడు?", "రేపు మీటింగ్ ఎప్పుడు?"),
    ("ఏయ్ జార్విస్ టైమ్ ఎంత", "టైమ్ ఎంత"),
    ("K Jarvis Open Notepad", "Open Notepad"),                  # "Hey" misheard as "K"
    ("Can you open Notepad for me?, Jarvis?", "Can you open Notepad for me?"),
])
def test_strip_wake_name(heard, command):
    assert jarvis.strip_wake_name(heard) == command


@pytest.mark.parametrize("text, wakes", [
    ("Jarvis, are you there?", True), ("ok jervis open notepad", True), ("జార్విస్ ఏం చేస్తున్నావ్", True),
    ("జర్వీస్ టైమ్ ఎంత", True), ("the service was great", False), ("Travis called earlier", False),
    ("I'm going to the jar store", False),
])
def test_name_pattern(text, wakes):
    assert bool(jarvis.WAKE_NAME.search(text)) == wakes


class NoWakeWord:
    def predict(self, frame):
        return {jarvis.WAKE_MODEL: 0.0}


def waking_ears(frames, heard=""):
    import threading
    e = ears_with(frames)
    e.wake, e.paused, e.manual = NoWakeWord(), False, threading.Event()
    e.flush = lambda: None
    e.transcribed = []
    e.transcribe = lambda audio: e.transcribed.append(len(audio)) or heard
    e.q.put(None)  # end of the test feed
    real_get = e.q.get

    def get():
        f = real_get()
        if f is None:
            raise Done
        return f
    e.q.get = get
    return e


def clap():
    f = np.zeros(jarvis.FRAME, dtype=np.int16)
    t = np.arange(240)
    f[:240] = (20000 * np.exp(-t / 40) * np.sign(np.sin(t * 2.1))).astype(np.int16)  # 15 ms sharp crack
    return f


def test_double_clap_wakes():
    assert waking_ears([QUIET] * 5 + [clap()] + [QUIET] * 4 + [clap()] + [QUIET] * 5).wait_for_wake() == ("clap", None, None)


@pytest.mark.parametrize("frames", [
    [QUIET] * 5 + [clap()] + [QUIET] * 30,                        # one clap
    [QUIET] * 5 + [clap()] + [QUIET] * 20 + [clap()] + [QUIET] * 5,  # two claps too far apart
    [QUIET] * 5 + [LOUD] * 40 + [QUIET] * 5,                      # steady speech-level sound
])
def test_no_wake_without_a_double_clap(frames, monkeypatch):
    monkeypatch.setattr(jarvis, "WAKE_BY_NAME", False)
    with pytest.raises(Done):
        waking_ears(frames).wait_for_wake()


def test_name_inside_a_sentence_wakes_with_the_audio():
    e = waking_ears([QUIET] * 3 + [LOUD] * 25 + [QUIET] * 12, heard="Jarvis, what's the time?")
    kind, audio, text = e.wait_for_wake()
    assert kind == "name" and text == "Jarvis, what's the time?" and len(audio) >= 25 * jarvis.FRAME


def test_speech_without_the_name_does_not_wake():
    e = waking_ears([QUIET] * 3 + [LOUD] * 25 + [QUIET] * 12, heard="so anyway we went to the market")
    with pytest.raises(Done):
        e.wait_for_wake()
    assert e.transcribed  # it listened, found no name, carried on


def test_jarvis_does_not_wake_on_its_own_voice(monkeypatch):
    monkeypatch.setitem(jarvis.ECHO, "until", float("inf"))  # Jarvis is speaking
    e = waking_ears([QUIET] * 3 + [LOUD] * 25 + [QUIET] * 12 + [clap()] + [QUIET] * 3 + [clap()],
                    heard="Say Jarvis when you need me")
    with pytest.raises(Done):
        e.wait_for_wake()
    assert e.transcribed == []


def test_name_with_a_command_runs_it_straight_away(monkeypatch):
    j, submitted = voice_jarvis(monkeypatch)
    ears = ScriptedEars([("name", np.zeros(16000, dtype=np.int16), "Jarvis, what's the time?")])
    with pytest.raises(Done):
        j.run_voice(ears)
    assert submitted == [("what's the time?", True)]
    assert ears.recorded == 0  # no "listening for the request" step


def test_jarvis_keeps_listening_while_a_request_runs(monkeypatch):
    """Requests are handed off as tasks: the next one can be spoken before the first is answered."""
    j, submitted = voice_jarvis(monkeypatch)
    audio = np.zeros(16000, dtype=np.int16)
    ears = ScriptedEars([("name", audio, "Jarvis, record a ten second video"),
                         ("name", audio, "Jarvis, what's the time?")])
    with pytest.raises(Done):
        j.run_voice(ears)
    assert [t for t, _ in submitted] == ["record a ten second video", "what's the time?"]


# ---- Telugu ----

def test_telugu_requests_are_not_dropped_as_empty():
    assert jarvis.norm("రేపు మీటింగ్ ఎప్పుడు?") == "రేపు మీటింగ్ ఎప్పుడు"
    assert jarvis.norm("రేపు మీటింగ్ ఎప్పుడు?") not in jarvis.JUNK


@pytest.mark.parametrize("said, answer", [
    ("అవును", "once"), ("సరే చేయండి", "once"), ("సరే అన్నీ చేయి", "task"),
    ("వద్దు", "deny"), ("కాదు ఆపండి", "deny"), ("ఏమిటి", None),
])
def test_telugu_permission_answers(said, answer):
    assert jarvis.parse_answer(said) == answer


def test_telugu_replies_use_the_telugu_voice(monkeypatch):
    used = []

    async def fake_tts(text, voice):
        used.append(voice["voice"])
        return np.zeros(10, dtype=np.int16)
    monkeypatch.setattr(jarvis.Mouth, "_edge_tts", staticmethod(fake_tts))
    monkeypatch.setattr(jarvis.sd, "play", lambda *a, **k: None)
    monkeypatch.setattr(jarvis.sd, "wait", lambda: None)
    mouth = jarvis.Mouth()
    mouth.say("సరే అండి, పెట్టాను.")
    mouth.say("Done, sir.")
    assert used == [jarvis.TELUGU_VOICE["voice"], jarvis.VOICE]


def test_request_carries_time_and_speaker(tmp_path):
    s = Store(tmp_path / "j.db")
    j = jarvis.Jarvis(mute=True, store=s)
    sent = []
    j.brain.ask = lambda text, on_work=None, **kw: sent.append(text) or "Hello."
    j.handle("who am I", voice=True, speaker="Sai (owner)")
    j.handle("what time is it")
    assert sent[0].startswith("[Time: ") and "| Voice: Sai (owner)]" in sent[0].splitlines()[0]
    assert "Voice" not in sent[1].splitlines()[0]
    assert s.recent_messages()[-4]["text"] == "who am I"   # history keeps what was said, not the tag
    s.close()


# ---- no filler: only a longer building job gets one short line ----

def ack_brain(monkeypatch, elapsed):
    said = []
    b = jarvis.Brain(approve=lambda *a: "deny")
    b.on_work, b._acked = said.append, False
    b._started = 1000.0
    monkeypatch.setattr(jarvis.time, "time", lambda: 1000.0 + elapsed)
    monkeypatch.setattr(jarvis.threading, "Thread",
                        lambda target, args, daemon: type("T", (), {"start": lambda self: target(*args)})())
    return b, said


@pytest.mark.parametrize("tool, inp", [
    ("WebSearch", {"query": "weather"}), ("Read", {"file_path": "C:/x.txt"}),
    ("mcp__jarvis__look", {}), ("mcp__jarvis__list_reminders", {}), ("mcp__jarvis__open_app", {"name": "Spotify"}),
    ("Write", {"file_path": str(paths.DATA / "user-profile.md")}),        # its own memory
    ("Bash", {"command": f"sed -i '' s/a/b/ {paths.DATA.as_posix()}/mac-howtos.md"}),
])
def test_no_line_for_chat_lookups_or_memory(monkeypatch, tool, inp):
    b, said = ack_brain(monkeypatch, elapsed=60)
    b._maybe_ack(tool, inp)
    assert said == []


def test_no_line_when_the_work_is_quick(monkeypatch):
    b, said = ack_brain(monkeypatch, elapsed=2)
    b._maybe_ack("Write", {"file_path": "C:/Users/ADMIN/Desktop/app.py"})
    assert said == []


def test_one_line_for_a_long_build_only_once(monkeypatch):
    b, said = ack_brain(monkeypatch, elapsed=20)
    b._maybe_ack("Write", {"file_path": "C:/Users/ADMIN/Desktop/app.py"})
    b._maybe_ack("PowerShell", {"command": "python app.py"})
    assert said == ["Write"]


@pytest.mark.parametrize("tool, request_text, line", [
    ("Write", "build me a sales agent", "Building it now, sir."),
    ("PowerShell", "install the tools", "Running that now."),
    ("Write", "సేల్స్ ఏజెంట్ బిల్డ్ చేయి", "బిల్డ్ చేస్తున్నాను సార్."),
])
def test_work_lines_are_specific(tool, request_text, line):
    assert jarvis.work_line(tool, request_text) == line
    assert line not in ("One moment, sir.", "On it.", "Working on it, sir.")


# ---- several requests at once ----

def concurrent_jarvis(monkeypatch, release):
    """Jarvis whose brain is quick, except for requests mentioning "video", which wait for `release`."""
    said, prompts = [], []
    monkeypatch.setattr(jarvis.Mouth, "say", lambda self, text, then="idle", silent=False: said.append(text))

    def ask(brain, text, on_work=None, **kw):
        prompts.append(text)
        if "video" in text.splitlines()[-1]:  # the request itself, not the context lines
            release.wait(5)
            return "Recorded and saved in your Videos folder." if not brain.cancelled else ""
        return "It's 4:47 PM."
    monkeypatch.setattr(jarvis.Brain, "ask", ask)
    monkeypatch.setattr(jarvis.Brain, "interrupt", lambda brain: release.set())
    return jarvis.Jarvis(mute=True), said, prompts


def test_quick_answer_first_slow_one_named_when_it_lands(monkeypatch):
    import threading
    release = threading.Event()
    j, said, prompts = concurrent_jarvis(monkeypatch, release)
    slow = j.submit("record a ten second video")
    for _ in range(100):
        if j.jobs:
            break
        time.sleep(0.01)
    j.submit("what's the time?").join(5)
    assert said == ["It's 4:47 PM."]                                  # answered at once, plainly
    assert 'Still working on: "record a ten second video"' in prompts[-1]
    release.set()
    slow.join(5)
    assert said[-1] == 'About "record a ten second video": Recorded and saved in your Videos folder.'
    assert j.jobs == []


def test_never_mind_cancels_the_running_request(monkeypatch):
    import threading
    release = threading.Event()
    j, said, _ = concurrent_jarvis(monkeypatch, release)
    slow = j.submit("record a ten second video")
    for _ in range(100):
        if j.jobs:
            break
        time.sleep(0.01)
    j.handle("never mind")
    slow.join(5)
    assert said == ['Cancelled "record a ten second video".']      # and no answer afterwards


def test_emergency_stop_interrupts_every_running_request():
    b = jarvis.Brain(approve=lambda *a: "deny")
    kids = [b.spawn(), b.spawn()]
    hit = []
    for k in kids:
        k.interrupt = lambda k=k: hit.append(k)
    b.halt()
    assert sorted(map(id, hit)) == sorted(map(id, kids))
    assert all(k.halted.is_set() for k in kids)
    b.resume()


def test_spawned_requests_fork_the_conversation():
    b = jarvis.Brain(approve=lambda *a: "deny")
    b.session = "main-session"
    k = b.spawn()
    assert k.session == "main-session" and k.fork is True and k in b.children


# ---- the screen and taskbar stay in step with what Jarvis is really doing ----

def state_jarvis():
    j = jarvis.Jarvis(mute=True)
    shown = []
    j.emit = lambda kind, data=None: shown.append(data) if kind == "state" else None
    return j, shown


def test_state_priority_speaking_then_listening_then_working():
    j, shown = state_jarvis()
    j.jobs.append({"id": 1})
    j._set()
    j._set(listening=True)
    j._set(speaking=True)
    j._set(speaking=False)
    j._set(listening=False)
    j.jobs.clear()
    j._set()
    assert shown == ["thinking", "listening", "speaking", "listening", "thinking", "idle"]


def test_a_background_answer_cannot_paint_over_the_user_talking():
    j, shown = state_jarvis()
    j._set(listening=True)              # the user is speaking to Jarvis
    j.jobs.append({"id": 1})
    j.jobs.clear()
    j._set()                            # a background request finishes meanwhile
    assert shown == ["listening"]       # still listening, no flicker to idle


def test_unchanged_state_is_not_sent_again():
    j, shown = state_jarvis()
    for _ in range(5):
        j._set(listening=True)
    assert shown == ["listening"]


def test_speaking_starts_when_the_audio_does_not_while_the_voice_is_generated(monkeypatch):
    seen = []
    m = jarvis.Mouth(on_speaking=lambda on: seen.append(("speaking", on)))

    async def slow_tts(text, voice):
        seen.append("generating")
        return np.zeros(10, dtype=np.int16)
    monkeypatch.setattr(jarvis.Mouth, "_edge_tts", staticmethod(slow_tts))
    monkeypatch.setattr(jarvis.sd, "play", lambda *a, **k: seen.append("playing"))
    monkeypatch.setattr(jarvis.sd, "wait", lambda: None)
    m.say("Hello, sir.")
    assert seen == ["generating", ("speaking", True), "playing", ("speaking", False)]


def test_hearing_a_follow_up_shows_listening_while_it_is_spoken():
    e = waking_ears([QUIET] * 3 + [LOUD] * 25 + [QUIET] * 12, heard="and how long will that take?")
    states = []
    e.on_hearing = states.append
    jarvis.ECHO["follow_until"] = time.time() + 60
    kind, _, text = e.wait_for_wake()
    assert kind == "follow" and states == [True, False]


def test_speech_outside_the_follow_up_window_does_not_flash_listening():
    e = waking_ears([QUIET] * 3 + [LOUD] * 25 + [QUIET] * 12, heard="so anyway we went to the market")
    states = []
    e.on_hearing = states.append
    with pytest.raises(Done):
        e.wait_for_wake()
    assert states == []  # TV and chatter don't make the HUD flicker


# ---- background mode, claps with the camera, follow-ups only after a question ----

@pytest.mark.parametrize("reply, opens", [
    ("Which folder should I save it in, sir?", True),
    ('Shall I send it? "Yes" or "no"?', True),
    ("Done, sir.", False),
    ("Sounds like you're talking with someone else, sir.", False),
    ("ఏ ఫోల్డర్ లో సేవ్ చేయాలి అండి?", True),
])
def test_follow_up_window_opens_only_after_a_question(reply, opens):
    assert jarvis.asks_question(reply) == opens
    j = jarvis.Jarvis(mute=True)
    j.mouth.say = lambda *a, **k: None
    j.brain.ask = lambda text, on_work=None, **kw: reply
    j.handle("save the notes", voice=True)
    assert (jarvis.ECHO["follow_until"] > time.time()) == opens


def test_follow_ups_are_marked_for_claude(monkeypatch):
    j = jarvis.Jarvis(mute=True)
    j.mouth.say = lambda *a, **k: None
    sent = []
    j.brain.ask = lambda text, on_work=None, **kw: sent.append(text) or "ok"
    j.handle("yes, the desktop", voice=True, follow_up=True)
    assert "Heard without your name" in sent[0].splitlines()[0]


def test_calling_jarvis_opens_the_window_but_a_follow_up_does_not(monkeypatch):
    j, submitted = voice_jarvis(monkeypatch)
    events = []
    j.emit = lambda kind, data=None: events.append(kind)
    audio = np.zeros(16000, dtype=np.int16)
    with pytest.raises(Done):
        j.run_voice(ScriptedEars([("name", audio, "Jarvis, what's the time?")]))
    assert events.count("wake") == 1
    events.clear()
    with pytest.raises(Done):
        j.run_voice(ScriptedEars([("follow", audio, "and tomorrow?", False)]))
    assert "wake" not in events


class FakeSession:
    """Stands in for camera_session.CameraSession (no webcam needed)."""
    made = []

    def __init__(self, on_stop=None, sound_level=400.0, fail=False):
        self.on_stop, self.fail = on_stop, fail
        self.extended, self.stopped, self.audio = [], None, 0
        self.path = "C:/Users/ADMIN/Videos/Jarvis/jarvis-test.mp4"
        self._active = False
        FakeSession.made.append(self)

    def start(self, wait=3.0):
        if self.fail:
            raise RuntimeError("no webcam found")
        self._active = True
        return True

    @property
    def active(self):
        return self._active

    def latest_jpeg(self):
        return b"LIVEFRAME"

    def extend(self, seconds):
        self.extended.append(seconds)

    def elapsed(self):
        return 12.0

    def add_audio(self, frame):
        self.audio += 1

    def stop(self, reason="stopped"):
        self.stopped, self._active = reason, False
        self.on_stop(self.path, 12.0, reason)

    def wait(self, timeout=None):
        pass


@pytest.fixture
def fake_camera(monkeypatch):
    import camera_session
    FakeSession.made = []
    monkeypatch.setattr(camera_session, "CameraSession", FakeSession)
    return FakeSession


def test_double_clap_starts_a_live_recording_and_sends_the_frame(monkeypatch, fake_camera):
    j, submitted = voice_jarvis(monkeypatch)
    said, sent = [], []
    j.mouth.say = lambda text, then=None: said.append(text)
    j.submit = lambda text, **kw: sent.append((text, kw.get("image")))
    with pytest.raises(Done):
        j.run_voice(ScriptedEars([("clap", None, None)], recorded_text="what am I holding?"))
    assert said[-1] == "Yes, sir. I'm listening, and I can see you. What do you need?"
    assert sent == [("what am I holding?", b"LIVEFRAME")]
    assert len(fake_camera.made) == 1 and j.camera.active


def test_double_clap_without_a_camera_does_not_claim_to_see(monkeypatch):
    import camera_session
    monkeypatch.setattr(camera_session, "CameraSession", lambda **kw: FakeSession(fail=True, **kw))
    j, _ = voice_jarvis(monkeypatch)
    said = []
    j.mouth.say = lambda text, then=None: said.append(text)
    j.submit = lambda text, **kw: None
    with pytest.raises(Done):
        j.run_voice(ScriptedEars([("clap", None, None)], recorded_text="hello"))
    assert said[-1] == "Yes, sir. I'm listening. What do you need?"


def test_record_for_two_minutes_extends_the_same_recording(fake_camera):
    j = jarvis.Jarvis(mute=True)
    j.camera_record()                      # claps started it
    msg = j.camera_record(120)             # "record for 2 minutes"
    assert len(fake_camera.made) == 1      # one continuous video, not a new one
    assert fake_camera.made[0].extended == [120.0]
    assert "Still recording" in msg and "at least 120 s more" in msg


def test_while_recording_every_request_carries_the_live_frame(fake_camera):
    j = jarvis.Jarvis(mute=True)
    j.mouth.say = lambda *a, **k: None
    seen = []
    j.brain.ask = lambda text, on_work=None, image=None, **kw: seen.append((text.splitlines()[0], image)) or "ok"
    j.camera_record()
    j.handle("is my hair ok?", voice=True)
    assert seen[0][1] == b"LIVEFRAME" and "Camera: live" in seen[0][0]


def test_look_reads_the_running_recording_instead_of_reopening_the_camera(monkeypatch, fake_camera):
    j = jarvis.Jarvis(mute=True)
    j.camera_record()
    monkeypatch.setattr(tools, "_capture", lambda *a: pytest.fail("the webcam is busy recording"))
    out = asyncio.run(tools.look.handler({}))["content"]
    assert base64.b64decode(out[0]["data"]) == b"LIVEFRAME"


def test_mic_sound_goes_into_the_recording_and_stop_clears_everything(fake_camera):
    j = jarvis.Jarvis(mute=True)
    events = []
    j.emit = lambda kind, data=None: events.append((kind, data))
    e = ears_with([])
    j.ears = e
    j.camera_record()
    e._on_audio(np.zeros((1280, 1), dtype=np.int16), 1280, None, None)
    assert fake_camera.made[0].audio == 1
    assert "Saved a 12-second video" in j.camera_stop()
    assert getattr(e, "tap") is None and ("watching", None) in events




# ---- talking while working: narration, instant acknowledgement, timestamps ----

def narrating_brain():
    said = []
    b = jarvis.Brain()
    b.on_say = said.append
    b.cancelled = False
    return b, said


def assistant(*blocks):
    from claude_agent_sdk import AssistantMessage
    return AssistantMessage(content=list(blocks), model="test")


def test_text_written_with_a_tool_call_is_spoken_straight_away():
    from claude_agent_sdk import TextBlock, ToolUseBlock
    b, said = narrating_brain()
    b._narrate(assistant(TextBlock(text="Opening Spotify."), ToolUseBlock(id="1", name="PowerShell", input={})))
    for _ in range(50):  # spoken from a thread
        if said:
            break
        time.sleep(0.01)
    assert said == ["Opening Spotify."] and b._narrated


def test_the_final_answer_without_a_tool_call_is_left_for_the_reply():
    from claude_agent_sdk import TextBlock
    b, said = narrating_brain()
    b._narrate(assistant(TextBlock(text="Done, sir.")))
    time.sleep(0.05)
    assert said == [] and not b._narrated


def test_a_narrated_job_gets_no_second_canned_line(monkeypatch):
    b, said = ack_brain(monkeypatch, elapsed=20)
    b._narrated = True
    b._maybe_ack("PowerShell", {"command": "python app.py"})
    assert said == []


def test_log_lines_carry_a_time(capsys):
    jarvis.print("hello")
    assert re.match(r"\d\d:\d\d:\d\d\.\d{3} hello", capsys.readouterr().out)


# ---- new chat and removing things from view (never from memory) ----

def test_hiding_messages_keeps_them_stored(tmp_path):
    from memory.store import Store
    s = Store(tmp_path / "j.db")
    a, b, c = (s.add_message("user", t) for t in ("one", "two", "three"))
    assert s.hide_message(b)
    assert [m["text"] for m in s.recent_messages()] == ["one", "three"]
    assert s.hide_all() == 2
    assert s.recent_messages() == []
    assert s.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 3   # nothing was deleted
    s.close()


def test_an_existing_database_gains_the_hidden_column_without_losing_messages(tmp_path):
    import sqlite3
    from memory.store import MIGRATIONS, Store
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript(f"BEGIN; {MIGRATIONS[0]} PRAGMA user_version = 1; COMMIT;")   # the schema you have today
    db.execute("INSERT INTO messages (ts, role, text) VALUES ('2026-10-22T10:00:00', 'user', 'kept')")
    db.commit(); db.close()
    s = Store(path)
    assert s.version == len(MIGRATIONS)
    assert [m["text"] for m in s.recent_messages()] == ["kept"]
    s.close()


def test_new_chat_clears_the_screen_and_the_conversation_but_not_the_stored_history(tmp_path):
    from memory.store import Store
    s = Store(tmp_path / "j.db")
    events = []
    j = jarvis.Jarvis(mute=True, store=s, emit=lambda kind, data=None: events.append(kind))
    j.brain.session, j.brain.requests = "abc", 5
    s.add_message("user", "hello")
    j.new_chat()
    assert j.brain.session is None and "cleared" in events
    assert s.recent_messages() == []
    assert s.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
    s.close()


def test_spoken_new_conversation_does_the_same(tmp_path):
    from memory.store import Store
    s = Store(tmp_path / "j.db")
    events = []
    j = jarvis.Jarvis(mute=True, store=s, emit=lambda kind, data=None: events.append(kind))
    j.handle("new conversation")
    assert "cleared" in events
    assert [m["role"] for m in s.recent_messages()] == ["jarvis"]   # only the confirmation is on screen
    s.close()


def test_messages_reach_the_screen_with_their_stored_id(tmp_path):
    from memory.store import Store
    s = Store(tmp_path / "j.db")
    seen = []
    j = jarvis.Jarvis(mute=True, store=s, emit=lambda kind, data=None: seen.append((kind, data)))
    j.mouth.say("Hello.")
    (kind, data), = [e for e in seen if e[0] == "message"]
    assert data["id"] == s.recent_messages()[-1]["id"]
    s.close()


# ---- the clap detector: 10 ms slices, quiet before and after ----

def _clap_signal(parts, total, noise=70, seed=1):
    rng = np.random.default_rng(seed)
    sig = rng.normal(0, noise, total).astype(np.float32)
    for at, kind in parts:
        t = np.arange(400)
        if kind == "clap":
            sig[at:at + 400] += 2500 * np.exp(-t / 25) * np.sign(np.sin(t * 1.9))
        elif kind == "plosive":   # a burst straight into a vowel: speech, not a clap
            sig[at:at + 400] += 2500 * np.exp(-t / 25) * np.sign(np.sin(t * 1.9))
            sig[at + 400:at + 2800] += 1500 * np.sin(np.arange(2400) * 0.09)
    return np.concatenate([rng.normal(0, noise, 16000), sig, rng.normal(0, noise, 16000)])


def _detections(signal, threshold=170.0):
    det, hits = jarvis.ClapDetector(), 0
    for i in range(0, len(signal) // jarvis.FRAME * jarvis.FRAME, jarvis.FRAME):
        hits += det.feed(signal[i:i + jarvis.FRAME].astype(np.int16), threshold)
    return hits


@pytest.mark.parametrize("gap", [4800, 8000, 12800])   # 0.3 s, 0.5 s, 0.8 s apart
def test_a_double_clap_is_heard_at_natural_speeds(gap):
    assert _detections(_clap_signal([(8000, "clap"), (8000 + gap, "clap")], 30000)) == 1


@pytest.mark.parametrize("parts", [
    [(8000, "clap")],                               # one clap
    [(8000, "clap"), (8000 + 25600, "clap")],       # too far apart
    [(8000, "clap"), (8000 + 800, "clap")],         # too close: one thud
    [(8000, "plosive"), (16000, "plosive")],        # consonants running into vowels
])
def test_no_double_clap_from_one_clap_a_thud_or_speech(parts):
    assert _detections(_clap_signal(parts, 48000)) == 0


def test_the_quiet_laptop_mic_claps_are_loud_enough():
    # measured on the user's microphone: claps peak around 1800-2700, so CLAP_PEAK must sit below that
    assert jarvis.CLAP_PEAK <= 1800


# ---- "Jarvis" alone is never judged by voice (too short to match, e.g. on a different microphone) ----

def test_the_bare_name_in_a_weakly_matched_voice_still_listens_for_the_request(monkeypatch):
    j, submitted = voice_jarvis(monkeypatch)
    j.identify = lambda audio: "unrecognised"          # a short clip on a new microphone scored below the match
    audio = np.zeros(16000, dtype=np.int16)
    with pytest.raises(Done):
        j.run_voice(ScriptedEars([("name", audio, "Jarvis.")], recorded_text="what time is it"))
    assert [t for t, _ in submitted] == ["what time is it"]


def test_a_whole_sentence_in_an_unrecognised_voice_is_still_ignored(monkeypatch):
    j, submitted = voice_jarvis(monkeypatch)
    j.identify = lambda audio: "unrecognised"
    audio = np.zeros(16000, dtype=np.int16)
    with pytest.raises(Done):
        j.run_voice(ScriptedEars([("name", audio, "Jarvis, what time is it")], recorded_text="x"))
    assert submitted == []
