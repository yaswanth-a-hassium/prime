"""Prime on the Mac desktop: its Dock identity, the menu-bar icon, global hotkeys, where the window sits, start at
login, the Prime.app bundle, and only one copy running at a time.

    Identity      - the Dock and the app menu say "Prime" with the Hassium icon, not Python and its rocket.
    Menu bar      - an atom in the menu bar shows Prime's state (colour per state) and has the tray menu.
    Hotkeys       - Ctrl+Option+J wakes Prime to listen, Ctrl+Option+Esc is the emergency stop, from any app.
                    (NSEvent monitors: they need the Accessibility permission, which the screen tools need anyway.)
    Start at login- a LaunchAgent (~/Library/LaunchAgents/com.hassium.prime.plist) that opens Prime.app hidden.
    Prime.app     - ~/Applications/Prime.app, a small bundle that runs prime_app.py with this folder's .venv. Its
                    Info.plist carries the usage descriptions macOS shows when it asks for the microphone, camera
                    and automation permissions, and it is ad-hoc signed so those permissions stick.

All AppKit work happens on the main thread (PyObjCTools.AppHelper.callAfter); pywebview runs the Cocoa event loop.
"""
import json
import os
import plistlib
import subprocess
import sys
import threading
from pathlib import Path

import macos
import paths

HERE = Path(__file__).resolve().parent
APP_NAME = "Prime"
BUNDLE_ID = "com.hassium.prime"
TITLE = "PRIME"
ICON_PNG = HERE / "assets" / "prime_icon.png"
ICNS = HERE / "assets" / "Prime.icns"
STATUS_ICONS = HERE / "assets" / "status"
VENV_PYTHON = HERE / ".venv" / "bin" / "python"
APP_BUNDLE = Path.home() / "Applications" / "Prime.app"
LAUNCH_AGENT = Path.home() / "Library" / "LaunchAgents" / f"{BUNDLE_ID}.plist"
LOG_DIR = Path.home() / "Library" / "Logs" / "Prime"
WINDOW_ON_RTX = False   # kept for settings written by the Windows version; nothing to choose on a Mac
GAME_MODE = True        # a fullscreen game or video in front keeps Prime's window and screen tools out of its way
WINDOW = None           # the pywebview window, set by prime_app.py


def _main(fn, *args):
    """Run fn on the main (Cocoa) thread; directly if this is it."""
    if not macos.IS_MAC:
        return
    try:
        from Foundation import NSThread
        from PyObjCTools import AppHelper
        if NSThread.isMainThread():
            fn(*args)
        else:
            AppHelper.callAfter(fn, *args)
    except Exception as e:
        print(f"[desktop] {getattr(fn, '__name__', fn)} failed: {e}")


# ---- identity, single instance, GPU (call before the window is created) -----------------------------

def claim_identity():
    """Call Prime "Prime" in the Dock and menu bar, with its icon (instead of Python's rocket)."""
    if not macos.IS_MAC:
        return
    try:
        from AppKit import NSApplication, NSImage
        from Foundation import NSBundle, NSProcessInfo
        info = NSBundle.mainBundle().localizedInfoDictionary() or NSBundle.mainBundle().infoDictionary()
        if info is not None:
            info["CFBundleName"] = APP_NAME
        NSProcessInfo.processInfo().setProcessName_(APP_NAME)
        app = NSApplication.sharedApplication()
        if ICON_PNG.exists():
            app.setApplicationIconImage_(NSImage.alloc().initWithContentsOfFile_(str(ICON_PNG)))
    except Exception as e:
        print(f"[desktop] identity: {e}")


def use_fast_gpu():
    """Nothing to do on Apple Silicon: one GPU, shared by everything."""


_lock = None
LOCK_FILE = paths.DATA / ".prime.lock"


def single_instance():
    """True if this is the only Prime. Otherwise asks the running one to show its window and returns False."""
    global _lock
    import fcntl
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    _lock = open(LOCK_FILE, "a+")
    try:
        fcntl.flock(_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        try:
            import siri_bridge
            siri_bridge.send({"cmd": "show"}, timeout=3)
        except Exception:
            pass
        return False
    _lock.seek(0)
    _lock.truncate()
    _lock.write(str(os.getpid()))
    _lock.flush()
    return True


# ---- menu-bar icon ----------------------------------------------------------------------------------

_Target = None


def _target_class():
    """An Objective-C object for the menu items to call (defined once, on first use)."""
    global _Target
    if _Target is None:
        import objc
        from Foundation import NSObject

        class PrimeMenuTarget(NSObject):
            def initWithActions_(self, actions):
                self = objc.super(PrimeMenuTarget, self).init()
                if self is None:
                    return None
                self.actions = actions
                return self

            def menuAction_(self, sender):
                action = self.actions[sender.tag()]
                threading.Thread(target=action, daemon=True).start()

        _Target = PrimeMenuTarget
    return _Target


class TaskbarIcon:
    """Prime's atom in the menu bar, coloured by state, with a menu. (Named for the Windows version's taskbar
    button, so the app code is the same.) `menu` maps labels to actions; the first is the default."""

    STATES = ("booting", "idle", "listening", "thinking", "speaking", "asking", "muted")
    TIPS = {"booting": "starting", "idle": "standby", "listening": "listening", "thinking": "working",
            "speaking": "speaking", "asking": "needs your OK", "muted": "mic off"}

    def __init__(self, menu=None):
        self.state = "booting"
        self.menu = list((menu or {}).items())
        self._item = self._target = None
        self._images = {}
        _main(self._build)

    def _build(self):
        from AppKit import NSImage, NSMenu, NSMenuItem, NSStatusBar, NSVariableStatusItemLength
        for state in self.STATES:
            f = STATUS_ICONS / f"{'idle' if state == 'booting' else state}.png"
            if f.exists():
                img = NSImage.alloc().initWithContentsOfFile_(str(f))
                img.setSize_((18, 18))
                self._images[state] = img
        self._item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        self._target = _target_class().alloc().initWithActions_([a for _, a in self.menu])
        menu = NSMenu.alloc().init()
        self._status_line = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Prime: starting", None, "")
        self._status_line.setEnabled_(False)
        menu.addItem_(self._status_line)
        menu.addItem_(NSMenuItem.separatorItem())
        for i, (label, _) in enumerate(self.menu):
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(label, "menuAction:", "")
            item.setTarget_(self._target)
            item.setTag_(i)
            menu.addItem_(item)
        self._item.setMenu_(menu)
        self._apply()

    def set(self, state):
        state = state if state in self.STATES else "idle"
        if state != self.state:
            self.state = state
            _main(self._apply)

    def _apply(self):
        if not self._item:
            return
        button = self._item.button()
        img = self._images.get(self.state)
        if img is not None:
            button.setImage_(img)
        else:
            button.setTitle_("Hs")
        tip = f"Prime: {self.TIPS.get(self.state, self.state)}"
        button.setToolTip_(tip)
        self._status_line.setTitle_(tip)

    def close(self):
        def remove():
            if self._item:
                from AppKit import NSStatusBar
                NSStatusBar.systemStatusBar().removeStatusItem_(self._item)
                self._item = None
        _main(remove)


# ---- global hotkeys ---------------------------------------------------------------------------------

KEY_J, KEY_ESC = 38, 53   # macOS virtual key codes
_monitors = []


def install_hotkeys(talk, stop):
    """Ctrl+Option+J -> talk(), Ctrl+Option+Esc -> stop(), from any app and from Prime's own window."""
    def install():
        from AppKit import (NSEvent, NSEventMaskKeyDown, NSEventModifierFlagCommand, NSEventModifierFlagControl,
                            NSEventModifierFlagOption, NSEventModifierFlagShift)
        want = NSEventModifierFlagControl | NSEventModifierFlagOption
        mask = want | NSEventModifierFlagCommand | NSEventModifierFlagShift
        actions = {KEY_J: talk, KEY_ESC: stop}

        def check(event):
            action = actions.get(event.keyCode())
            if action and (event.modifierFlags() & mask) == want:
                threading.Thread(target=action, daemon=True).start()
                return True
            return False

        def seen_elsewhere(event):   # the global monitor's block returns nothing
            check(event)

        _monitors.append(NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(NSEventMaskKeyDown, seen_elsewhere))
        _monitors.append(NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
            NSEventMaskKeyDown, lambda e: None if check(e) else e))
        if not macos.accessibility_ok():
            print("[hotkey] Ctrl+Option+J / Ctrl+Option+Esc work in other apps only once Prime has the "
                  "Accessibility permission (System Settings > Privacy & Security > Accessibility)")
    _main(install)


# ---- fullscreen apps, the window ---------------------------------------------------------------------

def in_game():
    """True when a fullscreen app (a game or a video) is in front: the window must not pop over it."""
    if not GAME_MODE:
        return False
    return macos.frontmost_is_fullscreen()


WINDOW_FILE = paths.DATA / "window.json"
WINDOW_SIZE = (440, 760)   # points


def _work_area():
    """(left, top, right, bottom) of the main screen without the menu bar and Dock, top-left origin, in points."""
    try:
        from AppKit import NSScreen
        screen = NSScreen.mainScreen()
        full, vis = screen.frame(), screen.visibleFrame()
        top = full.size.height - (vis.origin.y + vis.size.height)
        return (int(vis.origin.x), int(top), int(vis.origin.x + vis.size.width), int(top + vis.size.height))
    except Exception:
        return (0, 25, 1512, 982 - 70)   # a 14" MacBook Pro's default resolution


def save_window_rect():
    """Remember where the window is, so it comes back to the same place."""
    w = WINDOW
    if w is None:
        return
    try:
        WINDOW_FILE.parent.mkdir(parents=True, exist_ok=True)
        WINDOW_FILE.write_text(json.dumps([int(w.x), int(w.y), int(w.width), int(w.height)]))
    except Exception as e:
        print(f"[desktop] couldn't save the window position: {e}")


def place_window():
    """Put the window where it belongs: where you left it, else at the right edge of the screen, vertically
    centred, with a margin, sized to fit between the menu bar and the Dock."""
    w = WINDOW
    if w is None:
        return
    left, top, right, bottom = _work_area()
    margin = 16
    try:
        x, y, width, height = json.loads(WINDOW_FILE.read_text())
    except (OSError, ValueError, TypeError):
        width, height = WINDOW_SIZE
        x = right - width - margin
        y = top + (bottom - top - height) // 2
    width, height = min(width, right - left), min(height, bottom - top - 2 * margin)
    x = max(left, min(x, right - width))
    y = max(top, min(y, bottom - height))
    try:
        w.resize(width, height)
        w.move(x, y)
    except Exception as e:
        print(f"[desktop] couldn't place the window: {e}")


def show_window():
    def front():
        from AppKit import NSApplication
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    if WINDOW is not None:
        try:
            WINDOW.restore()
        except Exception:
            pass
    _main(front)


# ---- start at login, Prime.app ------------------------------------------------------------------------

def is_admin():
    return hasattr(os, "geteuid") and os.geteuid() == 0


def autostart_enabled():
    return LAUNCH_AGENT.exists()


def enable_autostart(hidden=True):
    """Start Prime when you log in (a LaunchAgent that opens Prime.app). `hidden`: start in the menu bar
    without opening the window. Returns an error or ''."""
    if not APP_BUNDLE.exists():
        make_shortcuts()
    args = ["/usr/bin/open", "-g", "-a", str(APP_BUNDLE)] + (["--args", "--startup"] if hidden else [])
    plist = {"Label": BUNDLE_ID, "ProgramArguments": args, "RunAtLoad": True,
             "LimitLoadToSessionType": "Aqua", "ProcessType": "Interactive"}
    data = plistlib.dumps(plist)
    try:
        if not LAUNCH_AGENT.exists() or LAUNCH_AGENT.read_bytes() != data:
            LAUNCH_AGENT.parent.mkdir(parents=True, exist_ok=True)
            LAUNCH_AGENT.write_bytes(data)
    except OSError as e:
        return str(e)
    return ""


def disable_autostart():
    LAUNCH_AGENT.unlink(missing_ok=True)


def _launcher_script():
    return f"""#!/bin/zsh
# Prime.app launcher (written by desktop.py): runs Prime with its own Python environment.
cd {_sh(HERE)} || exit 1
mkdir -p {_sh(LOG_DIR)}
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
exec {_sh(VENV_PYTHON)} {_sh(HERE / "prime_app.py")} "$@" >> {_sh(LOG_DIR / "prime.log")} 2>&1
"""


def _sh(path):
    return "'" + str(path).replace("'", "'\\''") + "'"


def _info_plist():
    return plistlib.dumps({
        "CFBundleName": APP_NAME, "CFBundleDisplayName": APP_NAME, "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleExecutable": "Prime", "CFBundleIconFile": "Prime", "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "1.0", "CFBundleVersion": "1", "LSMinimumSystemVersion": "13.0",
        "NSHighResolutionCapable": True, "LSApplicationCategoryType": "public.app-category.productivity",
        "NSMicrophoneUsageDescription": "Prime listens for its name and your requests.",
        "NSCameraUsageDescription": "Prime looks through the camera only when you ask it to (look, watch, record).",
        "NSAppleEventsUsageDescription": "Prime controls other apps when you ask it to (music, windows, messages).",
        "NSSpeechRecognitionUsageDescription": "Prime transcribes your speech on this Mac.",
    })


def make_shortcuts():
    """Create or repair ~/Applications/Prime.app (only rewritten when something changed, so the ad-hoc signature,
    and with it the permissions macOS granted, stay valid). Returns the paths made."""
    contents = APP_BUNDLE / "Contents"
    files = {contents / "Info.plist": _info_plist(), contents / "MacOS" / "Prime": _launcher_script().encode(),
             contents / "Resources" / "Prime.icns": ICNS.read_bytes() if ICNS.exists() else b""}
    changed = False
    for path, data in files.items():
        if not data:
            continue
        if not path.exists() or path.read_bytes() != data:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            changed = True
    (contents / "MacOS" / "Prime").chmod(0o755)
    if changed and macos.IS_MAC:
        subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(APP_BUNDLE)], capture_output=True)
        subprocess.run(["touch", str(APP_BUNDLE)], capture_output=True)   # Finder and the Dock pick up the icon
    return [str(APP_BUNDLE)]
