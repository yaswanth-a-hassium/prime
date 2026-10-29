#!/bin/zsh
# Run Prime from Terminal with its log on screen (handy the first time and for troubleshooting).
#   ./run_prime.sh            the app with its window
#   ./run_prime.sh --text     console text mode (no window): type requests, replies are spoken
HERE="${0:A:h}"
cd "$HERE" || exit 1
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
if [[ ! -x .venv/bin/python ]]; then echo "Run 'zsh install_mac.sh' first."; exit 1; fi
if [[ "${1:-}" == "--text" ]]; then
  exec .venv/bin/python jarvis.py --text
fi
exec .venv/bin/python prime_app.py "$@"
