"""Ask the running Prime something from Siri, the Shortcuts app, Spotlight or a terminal.

    .venv/bin/python prime_ask.py "what's the weather tomorrow"     # Prime answers aloud; the reply is printed too
    .venv/bin/python prime_ask.py --silent "summarise my last email"  # printed only (let Siri read it out)
    echo "open Safari" | .venv/bin/python prime_ask.py               # the request on stdin
    .venv/bin/python prime_ask.py --show                             # just bring Prime's window up

In a Shortcut: "Dictate Text" -> "Run Shell Script" (pass input as arguments) running
    "$HOME/Desktop/prime/.venv/bin/python" "$HOME/Desktop/prime/prime_ask.py" --silent "$@"
-> "Speak Text" (or "Show Result"). README_MAC.md has the step-by-step.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import siri_bridge  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Send a request to the running Prime")
    parser.add_argument("text", nargs="*", help="the request (read from stdin if left out)")
    parser.add_argument("--silent", action="store_true", help="don't speak on the Mac; just print the reply")
    parser.add_argument("--show", action="store_true", help="bring Prime's window to the front")
    args = parser.parse_args()
    try:
        if args.show:
            siri_bridge.send({"cmd": "show"}, timeout=5)
            return 0
        text = " ".join(args.text).strip() or (sys.stdin.read().strip() if not sys.stdin.isatty() else "")
        if not text:
            print("Say or type a request for Prime.")
            return 2
        reply = siri_bridge.send({"cmd": "ask", "text": text, "speak": not args.silent})
    except (FileNotFoundError, ConnectionRefusedError):
        print("Prime isn't running. Open Prime from Applications first.")
        return 1
    except OSError as e:
        print(f"Couldn't reach Prime: {e}")
        return 1
    if not reply.get("ok"):
        print(reply.get("error") or "Prime couldn't handle that.")
        return 1
    print(reply.get("reply") or "Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
