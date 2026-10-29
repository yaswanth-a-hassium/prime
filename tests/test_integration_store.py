"""Integration: the orchestrator writes conversation history and the activity log to the store."""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import jarvis  # noqa: E402
import policy  # noqa: E402
from memory.store import Store  # noqa: E402


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "j.db")
    policy.use_store(s)
    yield s
    policy.use_store(None)
    s.close()


def test_conversation_is_saved_and_redacted(store):
    j = jarvis.Jarvis(mute=True, store=store)
    j.brain.ask = lambda text, on_work=None, **kw: "Noted, sir."     # no network in tests
    j.handle("remember my password is hunter2")
    texts = [(m["role"], m["text"]) for m in store.recent_messages()]
    assert texts[-1] == ("jarvis", "Noted, sir.")
    assert texts[-2][0] == "user" and "hunter2" not in texts[-2][1]


@pytest.mark.parametrize("mode, decision", [("off", "deny"), ("full", "auto-approved")])
def test_actions_are_logged_to_the_database(store, monkeypatch, mode, decision):
    monkeypatch.setattr(jarvis, "AUTO_APPROVE", mode)
    brain = jarvis.Brain(approve=lambda *a: "deny")
    asyncio.run(brain._can_use_tool("PowerShell", {"command": "Start-Process calc"}, None))
    last = policy.recent()[-1]
    assert (last["tool"], last["category"], last["decision"]) == ("PowerShell", "B", decision)
    assert store.recent_activity()[-1]["tool"] == "PowerShell"


def test_emergency_stop_reply_is_saved_without_calling_the_brain(store):
    j = jarvis.Jarvis(mute=True, store=store)
    j.brain.ask = lambda *a, **k: pytest.fail("brain must not be called while halted")
    j.brain.halt()
    j.handle("open steam")
    assert "emergency stop" in store.recent_messages()[-1]["text"]
