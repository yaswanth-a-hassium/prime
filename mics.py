"""The microphones Prime has met.

On the Mac every microphone comes through Core Audio under one stable name (on Windows the same mic was named
differently by WASAPI and MME, which cuts names at 31 characters), so each mic is known by its full name. The registry remembers every mic it has seen,
even after it is unplugged, along with what Jarvis learned about it: how strict voice matching should be on it and
how loud a clap sounds through it. (The voiceprints themselves are in voiceid.py, filed under the same name.)
"""
import json
import time
from pathlib import Path

import sys

import sounddevice as sd

import paths

# The host audio interface that names microphones in full, and the one to open them through at 16 kHz.
NAMES_API, OPEN_API = ("Core Audio", "Core Audio") if sys.platform == "darwin" else ("WASAPI", "MME")

PATH = paths.DATA / "mics.json"


def _api(name):
    """Index of the host audio interface whose name contains `name` (Core Audio; WASAPI or MME on Windows), or None."""
    for i, h in enumerate(sd.query_hostapis()):
        if name in h["name"]:
            return i
    return None


def _inputs(api_name):
    api = _api(api_name)
    return [] if api is None else [d for d in sd.query_devices() if d["max_input_channels"] > 0 and d["hostapi"] == api]


def key_for(name):
    """The full, stable name of a microphone, given the name any audio interface reports for it."""
    name = (name or "").strip()
    full = [d["name"].strip() for d in _inputs(NAMES_API)]
    return next((f for f in full if f == name), None) or next((f for f in full if name and f.startswith(name)), None) or name


def default_key():
    """The microphone the Mac is using by default right now, System Settings > Sound > Input ('' if none)."""
    try:
        return key_for(sd.query_devices(kind="input")["name"])
    except Exception:
        return ""


def connected():
    """[{key, default}] for the microphones plugged in right now, one per distinct name."""
    now, seen, out = default_key(), set(), []
    for d in _inputs(NAMES_API) or _inputs(OPEN_API):
        key = d["name"].strip()
        if key not in seen:
            seen.add(key)
            out.append({"key": key, "default": key == now})
    return out


def stream_index(key):
    """Device number to open a given microphone at 16 kHz (through the interface that allows it), or None."""
    api = _api(OPEN_API)
    if api is None or not key:
        return None
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and d["hostapi"] == api and key.startswith(d["name"].strip()):
            return i
    return None


class Registry:
    """What Prime knows about each microphone, saved in mics.json."""

    def __init__(self, path=None):
        self.path = Path(path or PATH)
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.data = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            self.data = {}

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def known(self):
        return {k: dict(v) for k, v in self.data.items()}

    def seen(self, key):
        """Note that this microphone is in use. Returns True if it is one Prime has not met before."""
        if not key:
            return False
        now, new = time.strftime("%Y-%m-%d %H:%M"), key not in self.data
        rec = self.data.setdefault(key, {"first_seen": now, "match": None, "clap_peak": None, "imprinted": False})
        rec["last_seen"] = now
        self._save()
        return new

    def update(self, key, **fields):
        rec = self.data.setdefault(key, {"first_seen": time.strftime("%Y-%m-%d %H:%M"), "match": None,
                                          "clap_peak": None, "imprinted": False})
        rec.update(fields)
        self._save()

    def forget(self, key):
        if self.data.pop(key, None) is None:
            return False
        self._save()
        return True

    def match_for(self, key):
        """How close a voice must match on this microphone, or None for the standard."""
        return self.data.get(key, {}).get("match")

    def clap_for(self, key):
        return self.data.get(key, {}).get("clap_peak")

    def imprinted(self, key):
        return bool(self.data.get(key, {}).get("imprinted"))
