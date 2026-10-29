"""Prime's settings: one file the Settings page edits, checked on the way in and applied to the running assistant.

Saved as settings.json in the data folder (next to the memory notes), so a copy of that folder carries them to
another Mac. Every value has a default, a type and a safe range; anything unreadable falls back to its default,
so a hand-edited or damaged file can never stop Prime starting.
"""
import json
import re
from datetime import datetime
from pathlib import Path

import paths

PATH = paths.DATA / "settings.json"

# key: (default, kind, extra).  kinds: text, choice (extra = allowed values), number (extra = (min, max)), flag, time
SCHEMA = {
    # who it is, and how it talks to you
    "name": ("Prime", "text", 24),
    "call_me": ("sir", "text", 24),
    "humour": ("normal", "choice", ("off", "normal")),
    "reply_length": ("short", "choice", ("short", "detailed")),
    # its voice
    "voice": ("en-US-ChristopherNeural", "text", 64),
    "telugu_voice": ("te-IN-MohanNeural", "text", 64),
    "speech_rate": (-8, "number", (-40, 40)),       # percent faster (+) or slower (-)
    "speech_pitch": (-10, "number", (-30, 30)),     # Hz higher (+) or lower (-)
    "volume": (100, "number", (10, 150)),           # percent
    # the robot effect on top of the voice (robovoice.py)
    "robot_voice": (True, "flag", None),
    "robot_depth": (70, "number", (0, 100)),        # percent: 0 plain voice, 100 full vocoder
    "robot_pitch": (-5, "number", (-12, 0)),        # semitones down
    # listening
    "mic_device": ("", "text", 128),                # "" = follow the Mac's default microphone
    "wake_threshold": (0.5, "number", (0.2, 0.9)),
    "wake_by_name": (True, "flag", None),
    "wake_by_clap": (True, "flag", None),
    "clap_peak": (1200, "number", (300, 8000)),
    "end_of_speech": (0.8, "number", (0.4, 2.0)),   # seconds of quiet that end your sentence
    "follow_up_seconds": (5, "number", (0, 15)),
    # behaviour
    "approvals": ("auto", "choice", ("auto", "ask")),
    "game_mode": (True, "flag", None),
    "quiet_on": (False, "flag", None),
    "quiet_from": ("23:00", "time", None),
    "quiet_to": ("07:00", "time", None),
    # brain and privacy
    "model": ("", "choice", ("", "sonnet", "haiku", "opus")),
    "history_days": (0, "number", (0, 3650)),
    "fresh_chat_minutes": (20, "number", (2, 240)),
    # start-up
    "start_at_login": (True, "flag", None),
    "start_hidden": (True, "flag", None),
    # Siri Shortcut bridge: False = requests from Siri act like a guest's (anything consequential asks first)
    "siri_trusted": (False, "flag", None),
}

DEFAULTS = {k: v[0] for k, v in SCHEMA.items()}

# English voices worth offering (Microsoft's online neural voices); Telugu has two.
VOICES = {
    "en-US-ChristopherNeural": "Christopher, American man (deep)",
    "en-GB-RyanNeural": "Ryan, British man",
    "en-GB-ThomasNeural": "Thomas, British man",
    "en-GB-SoniaNeural": "Sonia, British woman",
    "en-US-GuyNeural": "Guy, American man",
    "en-US-AndrewNeural": "Andrew, American man",
    "en-US-EricNeural": "Eric, American man",
    "en-US-JennyNeural": "Jenny, American woman",
    "en-US-AriaNeural": "Aria, American woman",
    "en-IN-PrabhatNeural": "Prabhat, Indian man",
    "en-IN-NeerjaNeural": "Neerja, Indian woman",
    "en-AU-WilliamNeural": "William, Australian man",
}
TELUGU_VOICES = {"te-IN-MohanNeural": "Mohan, man", "te-IN-ShrutiNeural": "Shruti, woman"}
TELUGU_BASE = (-6, -8)   # the Telugu voice's calm delivery: rate %, pitch Hz, before your own adjustment


def clean(key, value):
    """The value, checked and tidied for `key`. Raises ValueError for anything not allowed."""
    if key not in SCHEMA:
        raise ValueError(f"unknown setting: {key}")
    default, kind, extra = SCHEMA[key]
    if kind == "flag":
        if not isinstance(value, bool):
            raise ValueError(f"{key} must be on or off")
        return value
    if kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{key} must be a number")
        low, high = extra
        value = min(high, max(low, value))
        return round(value, 2) if isinstance(default, float) else int(round(value))
    if kind == "choice":
        if value not in extra:
            raise ValueError(f"{key} must be one of {', '.join(map(repr, extra))}")
        return value
    if kind == "time":
        if not isinstance(value, str) or not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError(f"{key} must be a time like 23:00")
        return value
    if not isinstance(value, str):
        raise ValueError(f"{key} must be text")
    value = value.strip()
    if key in ("name",) and not value:
        raise ValueError("the name can't be empty")
    return value[:extra]


def load(path=None):
    """All settings: defaults, overridden by whatever in the file is valid."""
    out = dict(DEFAULTS)
    try:
        saved = json.loads(Path(path or PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out
    for key, value in (saved.items() if isinstance(saved, dict) else ()):
        try:
            out[key] = clean(key, value)
        except ValueError:
            pass
    return out


def save(values, path=None):
    path = Path(path or PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(values, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)   # never leaves a half-written file


# ---- small helpers the assistant uses while talking ----

def address(text, call_me):
    """Swap the word "sir" in a spoken line for what you asked to be called ("" removes it)."""
    if call_me.strip().lower() == "sir":
        return text
    if not call_me.strip():
        started_capital = text[:1].isupper()
        text = re.sub(r"\s*,\s*sir\b(?=\s*[.!?]|\s*$)", "", text, flags=re.I)     # "Done, sir."  ->  "Done."
        text = re.sub(r"(\w)\s+sir\s*,", r"\1,", text, flags=re.I)                 # "Yes sir, ..."  ->  "Yes, ..."
        text = re.sub(r"\bsir\s*,\s*", "", text, flags=re.I)                      # "Sir, it's ready"  ->  "it's ready"
        text = re.sub(r"\s+\bsir\b", "", text, flags=re.I)
        text = re.sub(r"\bsir\b", "", text, flags=re.I).strip()
        return text[:1].upper() + text[1:] if started_capital else text
    return re.sub(r"\bsir\b", lambda m: call_me if m.group().islower() else call_me[:1].upper() + call_me[1:], text,
                  flags=re.I)


def in_quiet_hours(values, now=None):
    """Is it inside the hours you asked Prime not to speak?"""
    if not values.get("quiet_on"):
        return False
    now = now or datetime.now()
    here = now.hour * 60 + now.minute
    start, end = (int(v[:2]) * 60 + int(v[3:]) for v in (values["quiet_from"], values["quiet_to"]))
    return start <= here < end if start <= end else (here >= start or here < end)


def style_rules(values):
    """The lines of the system prompt that depend on your settings."""
    call = values["call_me"].strip()
    rules = {
        "assistant_name": values["name"],
        "address_rule": (f'Address the user as "{call}" now and then.' if call
                         else "Don't use a title for the user; just speak to them directly."),
        "length_rule": ("One to three short, natural spoken sentences." if values["reply_length"] == "short"
                        else "Two to five natural spoken sentences: enough to explain properly, never a lecture."),
        "humour_rule": ("" if values["humour"] == "normal"
                        else "- The user has turned humour off: no jokes, teasing or banter at all, whatever the "
                             "guidance below says. Be plain, warm and to the point.\n"),
    }
    return rules


def apply(values, jarvis, desktop=None):
    """Push the settings into the running assistant (jarvis.py's module values). Safe to call at any time."""
    jarvis.VOICE = values["voice"]
    jarvis.VOICE_RATE, jarvis.VOICE_PITCH = values["speech_rate"], values["speech_pitch"]
    jarvis.TELUGU_VOICE = {"voice": values["telugu_voice"],
                           "rate": f"{TELUGU_BASE[0] + values['speech_rate']:+d}%",
                           "pitch": f"{TELUGU_BASE[1] + values['speech_pitch']:+d}Hz"}
    jarvis.VOLUME = values["volume"] / 100
    jarvis.ROBOT_VOICE = values["robot_voice"]
    jarvis.ROBOT_DEPTH = values["robot_depth"] / 100
    jarvis.ROBOT_PITCH = values["robot_pitch"]
    jarvis.WAKE_THRESHOLD = values["wake_threshold"]
    jarvis.WAKE_BY_NAME, jarvis.WAKE_BY_CLAP = values["wake_by_name"], values["wake_by_clap"]
    jarvis.CLAP_PEAK = values["clap_peak"]
    jarvis.END_OF_SPEECH = values["end_of_speech"]
    jarvis.FOLLOW_UP_SECONDS = values["follow_up_seconds"]
    jarvis.AUTO_APPROVE = "full" if values["approvals"] == "auto" else "off"
    jarvis.MODEL = values["model"] or None
    jarvis.SESSION_IDLE_SECONDS = values["fresh_chat_minutes"] * 60
    jarvis.SETTINGS = dict(values)
    jarvis.set_assistant_name(values["name"])
    if desktop is not None:
        desktop.GAME_MODE = values["game_mode"]
