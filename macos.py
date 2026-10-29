"""The Mac-specific pieces Prime's tools and window need: windows (Quartz), apps, focus, fullscreen detection,
the Accessibility / Screen Recording permissions, key names and typing text that the keyboard can't.

Everything imports PyObjC lazily, so the rest of Prime still imports on other systems (tests run anywhere).
"""
import os
import plistlib
import subprocess
import sys
from pathlib import Path

IS_MAC = sys.platform == "darwin"

APP_DIRS = [Path("/Applications"), Path("/Applications/Utilities"), Path("/System/Applications"),
            Path("/System/Applications/Utilities"), Path("/System/Library/CoreServices/Applications"),
            Path.home() / "Applications", Path("/Applications/Setapp")]

# Names Claude (or a Windows habit) may use for a key, and what pyautogui calls it on the Mac.
KEY_NAMES = {"cmd": "command", "command": "command", "win": "command", "super": "command", "meta": "command",
             "windows": "command", "alt": "option", "opt": "option", "option": "option", "ctrl": "ctrl",
             "control": "ctrl", "return": "enter", "escape": "esc", "del": "delete", "backspace": "backspace"}


def key(name):
    n = name.strip().lower()
    return KEY_NAMES.get(n, n)


# ---- permissions ---------------------------------------------------------------------------------------

def accessibility_ok(prompt=False):
    """Is Prime (its Python, or Prime.app) allowed to control the computer? (System Settings > Privacy &
    Security > Accessibility). Without it clicks and keystrokes are silently dropped by macOS."""
    if not IS_MAC:
        return True
    try:
        from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt
        return bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: bool(prompt)}))
    except Exception:
        return True   # can't tell: let the action try


def screen_recording_ok():
    """May Prime see other apps' windows and screen contents? (Privacy & Security > Screen Recording)"""
    if not IS_MAC:
        return True
    try:
        import Quartz
        return bool(Quartz.CGPreflightScreenCaptureAccess())
    except Exception:
        return True


def request_permissions():
    """Ask macOS to show the Accessibility and Screen Recording prompts (once each; later changes are made in
    System Settings)."""
    if not IS_MAC:
        return
    accessibility_ok(prompt=True)
    try:
        import Quartz
        if not Quartz.CGPreflightScreenCaptureAccess():
            Quartz.CGRequestScreenCaptureAccess()
    except Exception:
        pass


ACCESSIBILITY_HELP = ("macOS is blocking mouse and keyboard control. Ask the user to allow Prime in System Settings > "
                      "Privacy & Security > Accessibility (add Prime.app, or the Terminal/Python running it), then "
                      "try again.")


# ---- windows -----------------------------------------------------------------------------------------

def windows():
    """Normal on-screen windows, front to back: [{owner, title, pid, x, y, w, h}] (points, top-left origin).
    Titles need the Screen Recording permission; without it only the app names are known."""
    if not IS_MAC:
        return []
    import Quartz
    opts = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
    out = []
    for w in Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID) or []:
        if w.get("kCGWindowLayer", 0) != 0:
            continue
        b = w.get("kCGWindowBounds", {})
        out.append({"owner": w.get("kCGWindowOwnerName", ""), "title": w.get("kCGWindowName", "") or "",
                    "pid": int(w.get("kCGWindowOwnerPID", 0)), "x": int(b.get("X", 0)), "y": int(b.get("Y", 0)),
                    "w": int(b.get("Width", 0)), "h": int(b.get("Height", 0))})
    return [w for w in out if w["w"] > 40 and w["h"] > 40]


def window_titles():
    return [f"{w['owner']}: {w['title']}" if w["title"] else w["owner"] for w in windows()]


def focus(text):
    """Bring the first window whose app name or title contains `text` to the front. Returns its label or None."""
    want = text.strip().lower()
    for w in windows():
        if want in w["title"].lower() or want in w["owner"].lower():
            _activate(w["pid"])
            if w["title"]:
                script = ('tell application "System Events" to tell (first process whose unix id is %d) to '
                          'perform action "AXRaise" of (first window whose name is %s)' % (w["pid"], _as_string(w["title"])))
                subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5)
            return f"{w['owner']}: {w['title']}" if w["title"] else w["owner"]
    return None


def _activate(pid):
    try:
        from AppKit import NSApplicationActivateIgnoringOtherApps, NSRunningApplication
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        if app:
            app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
    except Exception:
        pass


def _as_string(text):
    """An AppleScript string literal."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def frontmost_pid():
    if not IS_MAC:
        return None
    try:
        from AppKit import NSWorkspace
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        return int(app.processIdentifier()) if app else None
    except Exception:
        return None


def frontmost_is_fullscreen(ignore_pids=()):
    """True when the app in front covers the whole display (a fullscreen game or video), not counting Prime
    itself and Finder's desktop."""
    if not IS_MAC:
        return False
    pid = frontmost_pid()
    if not pid or pid in ignore_pids or pid == os.getpid():
        return False
    try:
        import Quartz
        from AppKit import NSRunningApplication
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        if app and str(app.bundleIdentifier() or "") in ("com.apple.finder", "com.apple.dock"):
            return False
        screen = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID())
        sw, sh = screen.size.width, screen.size.height
    except Exception:
        return False
    for w in windows():
        if w["pid"] == pid:
            return w["x"] <= 0 and w["y"] <= 0 and w["w"] >= sw and w["h"] >= sh
    return False


# ---- apps --------------------------------------------------------------------------------------------

def apps():
    """Installed apps: [{Name, Path}] from the Applications folders (two levels deep, so folders of apps count)."""
    found = {}
    for base in APP_DIRS:
        if not base.is_dir():
            continue
        for p in list(base.glob("*.app")) + list(base.glob("*/*.app")):
            name = _display_name(p)
            found.setdefault(name.lower(), {"Name": name, "Path": str(p)})
    return sorted(found.values(), key=lambda a: a["Name"].lower())


def _display_name(app):
    try:
        with open(app / "Contents" / "Info.plist", "rb") as f:
            info = plistlib.load(f)
        return info.get("CFBundleDisplayName") or info.get("CFBundleName") or app.stem
    except Exception:
        return app.stem


def open_app(path):
    subprocess.Popen(["open", "-a", path])


# ---- typing what pyautogui can't --------------------------------------------------------------------

def paste_text(text):
    """Type any text (accents, emoji, other scripts) by pasting it, then put the clipboard back."""
    import pyautogui
    old = subprocess.run(["pbpaste"], capture_output=True).stdout
    subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
    pyautogui.hotkey("command", "v")
    import time
    time.sleep(0.25)
    subprocess.run(["pbcopy"], input=old, check=False)


def keystroke_private(text):
    """Type a secret that has characters pyautogui can't type, through System Events. The script goes to osascript
    on stdin (never on the command line, where other processes could see it) and never touches the clipboard."""
    script = 'tell application "System Events" to keystroke ' + _as_string(text)
    subprocess.run(["osascript", "-"], input=script.encode("utf-8"), capture_output=True, timeout=15)
