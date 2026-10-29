"""Start Prime, the macOS desktop app (the window, the menu-bar atom, the voice loop and the Siri bridge).

    .venv/bin/python prime_app.py            # with its window
    .venv/bin/python prime_app.py --startup  # menu bar only (how the login item starts it)

~/Applications/Prime.app runs this; install_mac.sh creates it.
"""
import sys

if sys.platform != "darwin":
    print("Prime is the macOS build. On Windows, run the original Jarvis.", file=sys.stderr)

from jarvis_app import main  # noqa: E402

if __name__ == "__main__":
    main()
