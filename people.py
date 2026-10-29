"""People Jarvis knows: one card per person, joining their voiceprints (voiceid.py) and their face (faceid.py).

Only numbers are kept (a voiceprint, a face embedding), never recordings or photos. Renaming, choosing the owner and
forgetting work on both at once, so a person can't end up half-known under two names.
"""
import json
import subprocess
import sys
from pathlib import Path

import paths

FACES = paths.DATA / "faces" / "faces.json"
FACEID = paths.DATA / "scripts" / "faceid.py"


def _load_faces(path=None):
    try:
        data = json.loads(Path(path or FACES).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_faces(db, path=None):
    path = Path(path or FACES)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(db), encoding="utf-8")
    tmp.replace(path)


def list_people(voices, faces_path=None):
    """[{name, owner, voice, voice_mics, face}] for everyone with a voiceprint or a face, owner first."""
    faces = _load_faces(faces_path)
    names = list(dict.fromkeys(list(voices.names() if voices else []) + list(faces)))
    owner = voices.owner() if voices else None
    people = []
    for name in names:
        in_voice = bool(voices) and name in voices.names()
        people.append({
            "name": name,
            "owner": name == owner or (owner is None and bool(faces.get(name, {}).get("owner"))),
            "voice": in_voice,
            "voice_mics": sorted(voices.devices_of(name)) if in_voice else [],
            "face": name in faces,
        })
    return sorted(people, key=lambda p: (not p["owner"], p["name"].lower()))


def _tidy(name):
    return " ".join((name or "").split())[:40]


def rename(voices, old, new, faces_path=None):
    """Rename a person everywhere. Returns an error message, or "" when it worked."""
    new = _tidy(new)
    if not new:
        return "The name can't be empty."
    faces = _load_faces(faces_path)
    known = set(voices.names() if voices else []) | set(faces)
    if old not in known:
        return f"I don't know anyone called {old}."
    if new != old and new in known:
        return f"There is already a {new}."
    if voices and old in voices.names():
        voices.rename(old, new)
    if old in faces:
        faces = {(new if n == old else n): v for n, v in faces.items()}
        _save_faces(faces, faces_path)
    return ""


def set_owner(voices, name, faces_path=None):
    """Make `name` the owner (the one voice that can approve actions). Returns an error message, or ""."""
    faces = _load_faces(faces_path)
    if name not in (set(voices.names() if voices else []) | set(faces)):
        return f"I don't know anyone called {name}."
    if voices and name in voices.names():
        voices.set_owner(name)
    if faces:
        for n, rec in faces.items():
            rec["owner"] = n == name
        _save_faces(faces, faces_path)
    return ""


def forget(voices, name, what="both", faces_path=None):
    """Forget a person's voice, face or both. Returns what was removed: a list of "voice" and/or "face"."""
    removed = []
    if what in ("voice", "both") and voices and voices.forget(name):
        removed.append("voice")
    if what in ("face", "both"):
        faces = _load_faces(faces_path)
        if faces.pop(name, None) is not None:
            _save_faces(faces, faces_path)
            removed.append("face")
    return removed


def enroll_face(name, owner=False, script=None, python=None, timeout=90):
    """Look at the camera for a few seconds and learn the face (runs the face tool). Returns (ok, message)."""
    name = _tidy(name)
    if not name:
        return False, "The name can't be empty."
    script = Path(script or FACEID)
    if not script.exists():
        return False, "The face tool isn't installed (faceid.py is missing from the scripts folder)."
    python = python or sys.executable
    cmd = [python, "-I", str(script), "enroll", name] + (["--owner"] if owner else [])
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "That took too long: the camera may be busy."
    out = (done.stdout or done.stderr or "").strip().splitlines()
    message = out[-1] if out else ""
    return done.returncode == 0, message or ("Learned." if done.returncode == 0 else "That didn't work.")
