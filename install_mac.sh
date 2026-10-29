#!/bin/zsh
# Prime installer for macOS (Apple Silicon). Run from this folder:   zsh install_mac.sh
# Safe to run again: it only adds what is missing. Nothing here needs sudo, and no password is stored anywhere.
set -euo pipefail

HERE="${0:A:h}"
cd "$HERE"
say_step() { print -P "\n%F{39}==>%f %B$1%b"; }
warn() { print -P "%F{214}!!%f $1"; }

say_step "Checking this Mac"
if [[ "$(uname -s)" != "Darwin" ]]; then echo "This installer is for macOS."; exit 1; fi
if [[ "$(uname -m)" != "arm64" ]]; then warn "This isn't Apple Silicon ($(uname -m)). Prime should still work, just slower."; fi
sw_vers

say_step "Finding Python 3.11+ (3.12 recommended)"
PY=""
for c in python3.12 python3.13 python3.11 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.13 /usr/local/bin/python3.12; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    PY="$(command -v "$c")"; break
  fi
done
if [[ -z "$PY" ]]; then
  if command -v brew >/dev/null 2>&1; then
    echo "Installing Python 3.12 with Homebrew..."
    brew install python@3.12
    PY="$(brew --prefix)/bin/python3.12"
  else
    warn "No Python 3.11+ found and no Homebrew. Install Homebrew (https://brew.sh) or Python 3.12 from python.org, then run this again."
    exit 1
  fi
fi
echo "Using $PY ($("$PY" --version))"

say_step "Creating the virtual environment (.venv)"
if [[ ! -x .venv/bin/python ]]; then "$PY" -m venv .venv; fi
.venv/bin/python -m pip install --upgrade pip wheel >/dev/null
.venv/bin/python -m pip install -r requirements.txt

say_step "Claude Code (Prime's brain)"
CLAUDE_BIN=""
for c in "$HOME/.local/bin/claude" /opt/homebrew/bin/claude /usr/local/bin/claude "$(command -v claude 2>/dev/null || true)"; do
  if [[ -n "$c" && -x "$c" ]]; then CLAUDE_BIN="$c"; break; fi
done
if [[ -n "$CLAUDE_BIN" ]]; then
  echo "Found $CLAUDE_BIN ($("$CLAUDE_BIN" --version 2>/dev/null || echo 'version unknown'))"
else
  warn "Claude Code isn't installed. Install it, sign in once, then come back:"
  echo "     curl -fsSL https://claude.ai/install.sh | bash      (or: brew install --cask claude-code)"
  echo "     claude          # sign in with your Claude account, then type /exit"
fi

say_step "Configuration"
if [[ ! -f config.toml ]]; then cp config.example.toml config.toml; echo "Created config.toml"; else echo "config.toml already there"; fi
mkdir -p "$HOME/PrimeMemory"

say_step "Speech, voice and wake-word models (about 600 MB the first time)"
.venv/bin/python fetch_models.py || warn "Model download failed; run '.venv/bin/python fetch_models.py' again later."

say_step "App icon"
if command -v iconutil >/dev/null 2>&1 && [[ -d assets/Prime.iconset ]]; then
  iconutil -c icns assets/Prime.iconset -o assets/Prime.icns && echo "Built assets/Prime.icns with iconutil"
fi

say_step "Prime.app in ~/Applications"
.venv/bin/python -c "import desktop; print('\n'.join(desktop.make_shortcuts()))"
chmod +x prime_ask.py run_prime.sh 2>/dev/null || true

say_step "Done"
cat <<'NEXT'
Next:
  1. Open Prime:            open ~/Applications/Prime.app      (or: ./run_prime.sh to watch the log)
  2. Allow the permissions macOS asks for: Microphone, then (System Settings > Privacy & Security)
     Accessibility and Screen Recording for "Prime". Camera is asked the first time you say "look at me".
     Quit and reopen Prime after granting Screen Recording.
  3. Say "Prime, what time is it?"  (or "Hey Jarvis", or clap twice, or press Ctrl+Option+J)
  4. Optional: set up the Siri Shortcut (README_MAC.md, "Siri").
NEXT
