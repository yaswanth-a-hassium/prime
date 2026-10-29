"""Load config.toml (created from config.example.toml on first run) into a typed Config."""
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "config.example.toml"
CONFIG_FILE = ROOT / "config.toml"


def _path(p):
    return Path(p).expanduser()


@dataclass
class Config:
    raw: dict = field(default_factory=dict)

    def get(self, section, key, default=None):
        return self.raw.get(section, {}).get(key, default)

    # brain
    @property
    def provider(self): return self.get("brain", "provider", "claude_code")
    @property
    def cli_path(self): return _path(self.get("brain", "cli_path", "~/.local/bin/claude"))
    @property
    def model(self): return self.get("brain", "model", "") or None
    @property
    def workdir(self): return _path(self.get("brain", "workdir", "~"))
    @property
    def slow_ack_seconds(self): return self.get("brain", "slow_ack_seconds", 4)

    # permissions
    @property
    def ask_permission(self): return self.get("permissions", "ask", True)
    @property
    def permission_timeout(self): return self.get("permissions", "timeout_seconds", 90)

    # storage
    @property
    def data_dir(self):
        import paths
        return _path(self.get("storage", "data_dir", "") or str(paths.DATA))
    @property
    def vault_dir(self): return self.data_dir / "vault"
    @property
    def db_path(self): return self.data_dir / "jarvis.db"

    # voice
    @property
    def voice_enabled(self): return self.get("voice", "enabled", True)
    @property
    def wake_model(self): return self.get("voice", "wake_model", "hey_jarvis")
    @property
    def wake_threshold(self): return self.get("voice", "wake_threshold", 0.5)
    @property
    def whisper_model(self): return self.get("voice", "whisper_model", "base.en")
    @property
    def whisper_device(self): return self.get("voice", "whisper_device", "cpu")
    @property
    def tts(self): return self.get("voice", "tts", "edge")
    @property
    def edge_voice(self): return self.get("voice", "edge_voice", "en-GB-RyanNeural")
    @property
    def follow_up_seconds(self): return self.get("voice", "follow_up_seconds", 5)

    # privacy
    @property
    def keep_raw_audio(self): return self.get("privacy", "keep_raw_audio", False)
    @property
    def history_days(self): return self.get("privacy", "history_days", 0)


def load(path=None):
    path = Path(path) if path else CONFIG_FILE
    if not path.exists() and path == CONFIG_FILE:
        shutil.copy(EXAMPLE, CONFIG_FILE)
    with open(path, "rb") as f:
        return Config(tomllib.load(f))
