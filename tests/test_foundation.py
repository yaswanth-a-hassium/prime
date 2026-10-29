"""Tests for the MVP foundation: config loading, the SQLite store (incl. restart), redaction."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import config  # noqa: E402
from core.redact import redact  # noqa: E402
from memory.store import MIGRATIONS, Store  # noqa: E402


def test_example_config_loads_with_defaults(tmp_path):
    cfg = config.load(config.EXAMPLE)
    assert cfg.provider == "claude_code"
    assert cfg.model is None                       # "" means account default
    assert cfg.ask_permission is True
    assert cfg.vault_dir == cfg.data_dir / "vault"
    assert cfg.keep_raw_audio is False


def test_missing_values_fall_back_to_defaults(tmp_path):
    f = tmp_path / "c.toml"
    f.write_text("[voice]\nwake_threshold = 0.7\n")
    cfg = config.load(f)
    assert cfg.wake_threshold == 0.7
    assert cfg.follow_up_seconds == 5
    assert cfg.permission_timeout == 90


def test_store_migrates_to_latest_version(tmp_path):
    s = Store(tmp_path / "j.db")
    assert s.version == len(MIGRATIONS)


def test_history_survives_restart(tmp_path):
    db = tmp_path / "j.db"
    s = Store(db)
    s.add_message("user", "hello")
    s.add_message("jarvis", "hi sir")
    s.close()
    s2 = Store(db)                                 # simulated app restart
    assert [m["text"] for m in s2.recent_messages()] == ["hello", "hi sir"]
    assert s2.version == len(MIGRATIONS)           # migrations not re-run


def test_activity_log_and_export(tmp_path):
    s = Store(tmp_path / "j.db")
    s.log_activity("PowerShell", "B", "deny", "run a command")
    assert s.recent_activity()[-1]["decision"] == "deny"
    out = s.export(tmp_path / "export.json")
    data = json.loads(Path(out).read_text())
    assert data["activity"][0]["tool"] == "PowerShell" and data["schema_version"] == len(MIGRATIONS)


def test_history_can_be_cleared(tmp_path):
    s = Store(tmp_path / "j.db")
    s.add_message("user", "x")
    s.clear_history()
    assert s.recent_messages() == []


def test_redaction():
    assert "hunter2" not in redact("my password is hunter2")
    assert "hunter2" not in redact("password=hunter2")
    assert "sk-abcdefghijklmnopqrstuv" not in redact("key sk-abcdefghijklmnopqrstuv")
    assert "4111 1111 1111 1111" not in redact("card 4111 1111 1111 1111")
    assert redact("open steam please") == "open steam please"
