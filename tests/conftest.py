"""Tests must never touch Jarvis's real saved state: the live conversation, settings, microphones and voiceprints
(a test calling Brain.reset() once deleted the live conversation file)."""
import pytest

import jarvis
import mics
import settings


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(jarvis, "SESSION_FILE", tmp_path / "session.txt")
    monkeypatch.setattr(mics, "PATH", tmp_path / "mics.json")
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
