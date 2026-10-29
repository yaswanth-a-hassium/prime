"""Who is speaking: offline speaker recognition, so Jarvis knows your voice from a guest's.

Each person says a few sentences once ("Hey Jarvis, learn my voice, I'm Sai");
Jarvis keeps a 256-number voiceprint per person (no audio is ever stored) and
compares every request against them. The first person enrolled is the owner:
only the owner's voice can approve actions by saying "yes".

The same voice sounds different through different microphones, so a person can also have a voiceprint *per
microphone* ("imprinting" a mic). When the current mic has one, it is used instead of the general voiceprint, and
each mic can have its own strictness (how close a match must be).

Runs locally on a WeSpeaker model through sherpa-onnx; nothing leaves the laptop.
"""
import json
import threading
from pathlib import Path

import numpy as np

import paths

MODEL = paths.MODELS / "wespeaker_en_voxceleb_resnet34.onnx"
PROFILES = paths.DATA / "voices" / "profiles.json"
RATE = 16000
MATCH = 0.45          # similarity needed to name a speaker; raise if it mixes people up, lower if it misses you
APPROVE_MATCH = 0.35  # a spoken "yes" is short, so approvals accept a slightly weaker match to the owner
MIN_SECONDS = 0.8     # shorter clips are too little to tell voices apart
MATCH_RANGE = (0.25, 0.60)


def _unit(vec):
    vec = np.asarray(vec, dtype=np.float32)
    return vec / (np.linalg.norm(vec) or 1.0)


class VoiceID:
    def __init__(self, model=MODEL, profiles=PROFILES):
        import sherpa_onnx
        if not Path(model).exists():
            raise FileNotFoundError(f"speaker model missing: {model}")
        config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(model), num_threads=2)
        self.extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)
        self._init_profiles(profiles)

    def _init_profiles(self, profiles):
        self.path = Path(profiles)
        self.lock = threading.Lock()
        # name -> {"embedding": unit vector, "samples": clips enrolled, "owner": bool,
        #          "devices": {microphone: {"embedding": unit vector, "samples": n}}}
        self.profiles = {}
        if self.path.exists():
            for name, p in json.loads(self.path.read_text(encoding="utf-8")).items():
                devices = {d: {**v, "embedding": np.array(v["embedding"], dtype=np.float32)}
                           for d, v in p.get("devices", {}).items()}
                self.profiles[name] = {**p, "embedding": np.array(p["embedding"], dtype=np.float32), "devices": devices}

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        for n, p in self.profiles.items():
            data[n] = {**p, "embedding": [round(float(x), 6) for x in p["embedding"]],
                       "devices": {d: {**v, "embedding": [round(float(x), 6) for x in v["embedding"]]}
                                   for d, v in p.get("devices", {}).items()}}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(self.path)

    def embed(self, audio):
        """Voiceprint (unit vector) of an int16 16 kHz clip, or None if it's too short."""
        if audio is None or len(audio) < MIN_SECONDS * RATE:
            return None
        stream = self.extractor.create_stream()
        stream.accept_waveform(RATE, audio.astype(np.float32) / 32768)
        stream.input_finished()
        if not self.extractor.is_ready(stream):
            return None
        return _unit(self.extractor.compute(stream))

    def _print_for(self, profile, device):
        """The voiceprint to compare against: the one made on this microphone if there is one."""
        return profile.get("devices", {}).get(device, profile)["embedding"] if device else profile["embedding"]

    def scores(self, audio, device=None):
        """{name: similarity} for every enrolled person, best first; {} if the clip is unusable."""
        vec = self.embed(audio)
        if vec is None:
            return {}
        with self.lock:
            s = {n: float(vec @ self._print_for(p, device)) for n, p in self.profiles.items()}
        return dict(sorted(s.items(), key=lambda kv: -kv[1]))

    def identify(self, audio, device=None, match=None):
        """(name, similarity) of the speaker; name is None for an unknown voice. `device` is the microphone,
        `match` how close a match must be (this mic's own strictness; the default otherwise)."""
        s = self.scores(audio, device)
        if not s:
            return None, 0.0
        name, score = next(iter(s.items()))
        return (name if score >= (MATCH if match is None else match) else None), score

    def is_owner(self, audio, device=None, match=None):
        """Is this the owner's voice? True when no owner is enrolled yet (nothing to check against)."""
        owner = self.owner()
        if owner is None:
            return True
        s = self.scores(audio, device)
        if not s:
            return False
        best = next(iter(s))
        # a spoken "yes" is short: accept a slightly weaker match than for a whole sentence
        floor = APPROVE_MATCH if match is None else max(MATCH_RANGE[0], match - (MATCH - APPROVE_MATCH))
        return best == owner and s[owner] >= floor

    def enroll(self, name, clips, device=None):
        """Add clips to `name`'s voiceprint (creating it); with `device`, also to the voiceprint for that microphone.
        Returns how many clips were usable."""
        vecs = [v for v in (self.embed(c) for c in clips) if v is not None]
        if not vecs:
            return 0
        with self.lock:
            old = self.profiles.get(name)
            self.profiles[name] = {
                "embedding": self._blend(old, vecs), "samples": len(vecs) + (old["samples"] if old else 0),
                "owner": old["owner"] if old else self.owner() is None, "devices": dict(old["devices"]) if old else {}}
            if device:
                self._add_device(self.profiles[name], device, vecs)
            self._save()
        return len(vecs)

    @staticmethod
    def _blend(old, vecs):
        total = np.sum(vecs, axis=0)
        if old:  # running average with what was enrolled before
            total = total + old["embedding"] * old["samples"]
        return _unit(total)

    @staticmethod
    def _add_device(profile, device, vecs):
        old = profile["devices"].get(device)
        total = np.sum(vecs, axis=0) + (old["embedding"] * old["samples"] if old else 0)
        profile["devices"][device] = {"embedding": _unit(total), "samples": len(vecs) + (old["samples"] if old else 0)}

    def imprint(self, name, device, clips):
        """Learn `name`'s voice on one microphone. Returns (clips used, a suggested strictness for that mic).
        The strictness is how well the clips match each other on this mic, less a safety margin."""
        vecs = [v for v in (self.embed(c) for c in clips) if v is not None]
        if not vecs:
            return 0, None
        with self.lock:
            old = self.profiles.get(name)
            if old is None:
                self.profiles[name] = old = {"embedding": _unit(np.sum(vecs, axis=0)), "samples": len(vecs),
                                             "owner": self.owner() is None, "devices": {}}
            self._add_device(old, device, vecs)
            self._save()
        if len(vecs) < 2:
            return len(vecs), None
        likeness = [float(v @ _unit(np.sum([w for j, w in enumerate(vecs) if j != i], axis=0))) for i, v in enumerate(vecs)]
        low, high = MATCH_RANGE
        return len(vecs), round(min(high, max(low, float(np.mean(likeness)) - 0.2)), 2)

    def rename(self, old, new):
        with self.lock:
            if old not in self.profiles or new in self.profiles:
                return False
            self.profiles = {(new if n == old else n): p for n, p in self.profiles.items()}
            self._save()
            return True

    def set_owner(self, name):
        with self.lock:
            if name not in self.profiles:
                return False
            for n, p in self.profiles.items():
                p["owner"] = n == name
            self._save()
            return True

    def forget(self, name):
        with self.lock:
            if self.profiles.pop(name, None) is None:
                return False
            self._save()  # if that was the owner, the next voice enrolled becomes the owner
            return True

    def forget_device(self, device):
        """Drop everyone's voiceprint for one microphone. Returns how many were removed."""
        with self.lock:
            gone = sum(1 for p in self.profiles.values() if p.get("devices", {}).pop(device, None) is not None)
            if gone:
                self._save()
            return gone

    def devices_of(self, name):
        """{microphone: clips} the person has been learned on."""
        with self.lock:
            return {d: v["samples"] for d, v in self.profiles.get(name, {}).get("devices", {}).items()}

    def names(self):
        return list(self.profiles)

    def owner(self):
        return next((n for n, p in self.profiles.items() if p["owner"]), None)
