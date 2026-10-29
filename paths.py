"""Where Prime keeps its data (memory notes, skills, settings, voiceprints, models, vault, logs).

The first of these that applies:
  1. the PRIME_DATA environment variable (JARVIS_DATA is honoured too, for a copied set-up),
  2. a data_dir.txt file next to this one (install_mac.sh can write it),
  3. PrimeMemory in your home folder (~/PrimeMemory).
"""
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _find():
    for var in ("PRIME_DATA", "JARVIS_DATA"):
        env = os.environ.get(var, "").strip()
        if env:
            return Path(env).expanduser()
    try:
        chosen = (HERE / "data_dir.txt").read_text(encoding="utf-8").strip()
        if chosen:
            return Path(chosen).expanduser()
    except OSError:
        pass
    return Path.home() / "PrimeMemory"


DATA = _find()
MODELS = DATA / "models"


def claude_rule_path(path):
    """A path as Claude Code's permission rules write it. Absolute POSIX paths start with "//" in a rule
    (/Users/me/PrimeMemory/vault -> //Users/me/PrimeMemory/vault); Windows drive paths D:/x -> //d/x."""
    s = str(path).replace("\\", "/")
    if len(s) > 1 and s[1] == ":":
        return "//" + s[0].lower() + s[2:]
    return "/" + s if s.startswith("/") else s


def lower_slash(path):
    """A path lower-cased with forward slashes, for comparing paths the way policy.py does."""
    return str(path).replace("\\", "/").lower().rstrip("/")
