"""Prime's hands on the Mac: screen, mouse, keyboard, windows and the password vault,
exposed to Claude as in-process MCP tools (mcp__jarvis__<name>; the server keeps its old name so saved
permissions and skills keep working).

Everything except looking (screenshot, list windows/secrets) and saving a
secret the user handed over goes through Prime's permission prompt first.
Screenshots are scaled down to SHOT_WIDTH; coordinates Claude passes are in
screenshot pixels and are scaled back to screen points here (a Retina screenshot
has twice as many pixels as the points pyautogui clicks in).

macOS asks once for two permissions: Accessibility (mouse and keyboard) and
Screen Recording (screenshots and window titles). See README_MAC.md.
"""
import asyncio
import base64
import io
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta

import pyautogui
from claude_agent_sdk import create_sdk_mcp_server, tool

import desktop
import emailcodes
import macos
import vault

pyautogui.FAILSAFE = True   # slam the mouse into a screen corner to abort
pyautogui.PAUSE = 0.05
SHOT_WIDTH = 1280
_scale = 1.0
_login_started = None       # when the current login began; codes older than this are ignored
SELECT_ALL = ("command", "a") if sys.platform == "darwin" else ("ctrl", "a")
SCROLL_UNIT = 3 if sys.platform == "darwin" else 120   # one "amount" = about 3 lines


def _text(msg):
    return {"content": [{"type": "text", "text": msg}]}


def _in_game():
    """A refusal for tools that touch the screen while a game is fullscreen (clicks and keys would hit the game),
    or while macOS hasn't given Prime the Accessibility permission (they would silently do nothing)."""
    if desktop.in_game():
        return _text("A game is fullscreen, so I must not click, type, switch windows or open apps: it would land in "
                     "the game. Do it another way (shell commands, web search, files, email, your memory), or if it "
                     "truly needs the screen, tell the user it will wait until they leave the game.")
    if not macos.accessibility_ok():
        return _text(macos.ACCESSIBILITY_HELP)
    return None


def _real(x, y):
    return int(x * _scale), int(y * _scale)


@tool("screenshot", "Capture the whole screen. Coordinates for click/move/scroll are in this image's pixels. "
      "Take a fresh screenshot after acting to check the result.", {})
async def screenshot(args):
    global _scale
    img = pyautogui.screenshot()
    if img.width > SHOT_WIDTH:
        img = img.resize((SHOT_WIDTH, round(img.height * SHOT_WIDTH / img.width)))
    _scale = pyautogui.size()[0] / img.width   # screenshot pixels -> screen points (Retina: physical != points)
    if not macos.screen_recording_ok():
        return _text("macOS hasn't allowed Prime to record the screen, so the screenshot only shows the desktop. Ask the "
                     "user to allow Prime in System Settings > Privacy & Security > Screen Recording, then restart Prime.")
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=70)
    return {"content": [
        {"type": "image", "data": base64.b64encode(buf.getvalue()).decode(), "mimeType": "image/jpeg"},
        {"type": "text", "text": f"Screenshot {img.width}x{img.height}."},
    ]}


@tool("click", "Click at (x, y) in screenshot pixels. button: left/right/middle. clicks: 2 for double-click.",
      {"type": "object", "properties": {
          "x": {"type": "number"}, "y": {"type": "number"},
          "button": {"type": "string", "enum": ["left", "right", "middle"]},
          "clicks": {"type": "integer"}},
       "required": ["x", "y"]})
async def click(args):
    if (blocked := _in_game()):
        return blocked
    x, y = _real(args["x"], args["y"])
    pyautogui.click(x, y, clicks=args.get("clicks", 1), interval=0.08, button=args.get("button", "left"))
    return _text(f"Clicked at {args['x']},{args['y']}.")


@tool("move_mouse", "Move the mouse to (x, y) in screenshot pixels without clicking.",
      {"type": "object", "properties": {"x": {"type": "number"}, "y": {"type": "number"}}, "required": ["x", "y"]})
async def move_mouse(args):
    if (blocked := _in_game()):
        return blocked
    pyautogui.moveTo(*_real(args["x"], args["y"]), duration=0.15)
    return _text("Moved.")


@tool("drag", "Drag with the left button from (x1, y1) to (x2, y2), screenshot pixels.",
      {"type": "object", "properties": {k: {"type": "number"} for k in ("x1", "y1", "x2", "y2")},
       "required": ["x1", "y1", "x2", "y2"]})
async def drag(args):
    if (blocked := _in_game()):
        return blocked
    pyautogui.moveTo(*_real(args["x1"], args["y1"]))
    pyautogui.dragTo(*_real(args["x2"], args["y2"]), duration=0.4, button="left")
    return _text("Dragged.")


@tool("scroll", "Scroll the mouse wheel. amount > 0 scrolls up, < 0 down (about 3 lines per unit). "
      "Optional x, y to scroll over a specific spot.",
      {"type": "object", "properties": {"amount": {"type": "integer"}, "x": {"type": "number"}, "y": {"type": "number"}},
       "required": ["amount"]})
async def scroll(args):
    if (blocked := _in_game()):
        return blocked
    if "x" in args and "y" in args:
        pyautogui.moveTo(*_real(args["x"], args["y"]))
    pyautogui.scroll(args["amount"] * SCROLL_UNIT)
    return _text("Scrolled.")


@tool("type_text", "Type text into the focused window as keystrokes. Never use this for passwords: use type_secret.",
      {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]})
async def type_text(args):
    if (blocked := _in_game()):
        return blocked
    if args["text"].isascii():
        pyautogui.write(args["text"], interval=0.01)
    else:  # accents, emoji, Telugu: the keyboard can't type them key by key, so paste them
        macos.paste_text(args["text"])
    return _text("Typed.")


@tool("press_keys", "Press a key or shortcut. Mac names: 'command+c', 'command+tab', 'command+space' (Spotlight), "
      "'command+shift+t', 'option+left', 'ctrl+up' (Mission Control), 'enter', 'tab', 'esc'. 'cmd' and 'alt' work too.",
      {"type": "object", "properties": {"keys": {"type": "string"}}, "required": ["keys"]})
async def press_keys(args):
    if (blocked := _in_game()):
        return blocked
    keys = [macos.key(k) for k in args["keys"].split("+")]
    pyautogui.hotkey(*keys) if len(keys) > 1 else pyautogui.press(keys[0])
    return _text(f"Pressed {args['keys']}.")


@tool("list_windows", "List open windows, front to back, as 'App: window title'.", {})
async def list_windows(args):
    titles = macos.window_titles()
    return _text("\n".join(titles) or "No windows.")


@tool("focus_window", "Bring the first window whose app name or title contains `title` to the front.",
      {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]})
async def focus_window(args):
    if (blocked := _in_game()):
        return blocked
    found = await asyncio.to_thread(macos.focus, args["title"])
    if not found:
        return _text(f"No window matching '{args['title']}'. (Minimised windows don't show; open_app brings an app back.)")
    await asyncio.sleep(0.3)
    return _text(f"Focused '{found}'.")


@tool("list_secrets", "List the names of passwords/secrets in the encrypted vault (never the values).", {})
async def list_secrets(args):
    return _text(", ".join(vault.names()) or "The vault is empty.")


@tool("save_secret", "Store a password or other secret the user gave you in the encrypted vault under a short name "
      "(e.g. 'gmail', 'wifi'). After saving, never repeat the value or write it anywhere else.",
      {"type": "object", "properties": {"name": {"type": "string"}, "value": {"type": "string"}},
       "required": ["name", "value"]})
async def save_secret(args):
    vault.set_secret(args["name"], args["value"])
    return _text(f"Saved '{args['name']}' to the vault.")


@tool("type_secret", "Type a vault secret into the focused field (click the field first). "
      "The value is typed directly and is never shown to you.",
      {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]})
async def type_secret(args):
    if (blocked := _in_game()):
        return blocked
    value = vault.get_secret(args["name"])
    if value is None:
        return _text(f"No secret named '{args['name']}'. Known: {', '.join(vault.names()) or 'none'}.")
    _type_private(value)
    return _text(f"Typed secret '{args['name']}'.")


def _type_private(value, interval=0.02):
    """Type a password or code as keystrokes, never through the clipboard."""
    if value.isascii():
        pyautogui.write(value, interval=interval)
    else:
        macos.keystroke_private(value)


@tool("delete_secret", "Remove a secret from the vault.",
      {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]})
async def delete_secret(args):
    return _text("Deleted." if vault.delete_secret(args["name"]) else "No such secret.")


# ---- apps -----------------------------------------------------------------------

_apps = None


def _start_apps():
    """Every app in the Applications folders (yours, the system's, utilities): [{Name, Path}]."""
    global _apps
    if _apps is None:
        _apps = macos.apps()
    return _apps


def _find_app(name):
    n = name.strip().lower()
    apps = _start_apps()
    for test in (lambda a: a["Name"].lower() == n,
                 lambda a: a["Name"].lower().startswith(n),
                 lambda a: n in a["Name"].lower()):
        hits = [a for a in apps if test(a)]
        if hits:
            return hits[0]
    return None


@tool("list_apps", "Search the apps installed on this Mac (Applications folders, system apps, utilities). "
      "Optional `filter` text; returns app names to use with open_app.",
      {"type": "object", "properties": {"filter": {"type": "string"}}})
async def list_apps(args):
    f = (args.get("filter") or "").lower()
    names = sorted({a["Name"] for a in _start_apps() if f in a["Name"].lower()})
    return _text("\n".join(names[:200]) or "No matching apps.")


@tool("open_app", "Open an installed app or game by name (e.g. 'Safari', 'Music', 'Spotify', 'Xcode', 'Steam'). "
      "Matches app names; use list_apps if unsure. Take a screenshot afterwards to see its state.",
      {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]})
async def open_app(args):
    if (blocked := _in_game()):
        return blocked
    global _apps
    app = _find_app(args["name"])
    if not app:
        _apps = None  # maybe installed since we last looked
        app = _find_app(args["name"])
    if not app:
        return _text(f"No installed app matching '{args['name']}'. Try list_apps.")
    macos.open_app(app["Path"])
    await asyncio.sleep(1)  # enough for the window to start appearing; the next screenshot shows the rest
    return _text(f"Opened {app['Name']}.")


# ---- logins ---------------------------------------------------------------------

def _click_field(args):
    """Click the field at (x, y) if given, then select its contents so typing replaces them."""
    if "x" in args and "y" in args:
        pyautogui.click(*_real(args["x"], args["y"]))
        time.sleep(0.2)
    pyautogui.hotkey(*SELECT_ALL)


@tool("list_accounts", "List saved logins (service, username, whether it's the usual account, and how its "
      "2FA codes arrive: none/email/app/phone). Passwords are never shown. Optional service filter.",
      {"type": "object", "properties": {"service": {"type": "string"}}})
async def list_accounts(args):
    accs = vault.accounts(args.get("service"))
    if not accs:
        return _text("No saved logins" + (f" for {args['service']}." if args.get("service") else "."))
    return _text("\n".join(f"{a['service']}: {a['username']}{' (usual)' if a['usual'] else ''}, 2FA: {a['twofa']}"
                           for a in accs))


@tool("fill_login", "Fill one field of a sign-in form from a saved login: clicks the field at (x, y) in screenshot "
      "pixels, replaces its contents with the username or password, and presses Enter if submit is true. "
      "Uses the service's usual account unless `account` (a username) is given. Values are never shown to you.",
      {"type": "object", "properties": {
          "service": {"type": "string"},
          "field": {"type": "string", "enum": ["username", "password"]},
          "account": {"type": "string"},
          "x": {"type": "number"}, "y": {"type": "number"},
          "submit": {"type": "boolean"}},
       "required": ["service", "field"]})
async def fill_login(args):
    if (blocked := _in_game()):
        return blocked
    global _login_started
    acc = vault.get_account(args["service"], args.get("account"))
    if not acc:
        known = ", ".join(f"{a['service']} ({a['username']})" for a in vault.accounts()) or "none"
        return _text(f"No single saved login for '{args['service']}'. Saved: {known}.")
    if args["field"] == "username" or _login_started is None:
        _login_started = emailcodes.now()
    _click_field(args)
    _type_private(acc[args["field"]], interval=0.03)
    if args.get("submit"):
        pyautogui.press("enter")
    return _text(f"Filled the {args['field']} for {acc['service']} ({acc['username']})"
                 + (" and submitted." if args.get("submit") else "."))


@tool("type_email_code", "When a sign-in asks for a verification code sent by email: waits for the newest code "
      "email whose sender or subject mentions `sender` (e.g. 'riot', 'crunchyroll'), clicks the code field at "
      "(x, y) and types the code, then presses Enter if submit is true. The email and the code are never shown "
      "to you. Waits up to wait_seconds (default 90) for the email to arrive.",
      {"type": "object", "properties": {
          "sender": {"type": "string"},
          "x": {"type": "number"}, "y": {"type": "number"},
          "submit": {"type": "boolean"},
          "wait_seconds": {"type": "integer"}},
       "required": ["sender"]})
async def type_email_code(args):
    if (blocked := _in_game()):
        return blocked
    started = _login_started or emailcodes.now() - timedelta(minutes=10)
    try:
        code = await asyncio.to_thread(emailcodes.wait_for_code, args["sender"],
                                       started - timedelta(minutes=1), args.get("wait_seconds", 90))
    except Exception as e:
        return _text(f"Couldn't check email: {e}")
    if not code:
        return _text(f"No code email from '{args['sender']}' arrived. Ask the user to read the code out.")
    _click_field(args)
    _type_private(code, interval=0.05)
    if args.get("submit"):
        pyautogui.press("enter")
    return _text("Entered the verification code from email" + (" and submitted." if args.get("submit") else "."))


# ---- camera ---------------------------------------------------------------------

CAMERA_INDEX = 0      # which webcam (0 = the built-in FaceTime camera)


def CAMERA_BACKEND():  # noqa: N802  AVFoundation on the Mac (asks for the Camera permission once), DirectShow on Windows
    import cv2
    return cv2.CAP_AVFOUNDATION if sys.platform == "darwin" else cv2.CAP_DSHOW
CAMERA_WIDTH = 960    # frames are scaled down to this width before Claude sees them


def _capture(frames, interval):
    """Grab `frames` webcam pictures `interval` seconds apart, as JPEG bytes. The camera is
    switched on only for this and released straight after; nothing is saved to disk."""
    import cv2
    cam = cv2.VideoCapture(CAMERA_INDEX, CAMERA_BACKEND())
    try:
        if not cam.isOpened():
            raise RuntimeError("no webcam found, or another app is using it")
        for _ in range(8):  # let auto-exposure settle; the first frames are often dark
            cam.read()
        shots = []
        for i in range(frames):
            if i:
                time.sleep(interval)
            ok, img = cam.read()
            if not ok:
                raise RuntimeError("the webcam stopped sending pictures")
            h, w = img.shape[:2]
            if w > CAMERA_WIDTH:
                img = cv2.resize(img, (CAMERA_WIDTH, round(h * CAMERA_WIDTH / w)))
            shots.append(cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 75])[1].tobytes())
        return shots
    finally:
        cam.release()


@tool("look", "See through the Mac's webcam: what's in front of the laptop, the user, what they're holding or "
      "doing. Use it only when the user asks you to look, see or watch. frames > 1 (max 6) takes a short "
      "sequence `interval` seconds apart (default 1) to watch movement, e.g. 'watch me do this'.",
      {"type": "object", "properties": {"frames": {"type": "integer"}, "interval": {"type": "number"}}})
async def look(args):
    frames = max(1, min(int(args.get("frames") or 1), 6))
    interval = args.get("interval")
    interval = max(0.3, min(float(1 if interval is None else interval), 5))
    try:
        shots = await asyncio.to_thread(lambda: _live_shots(frames, interval) or _capture(frames, interval))
    except Exception as e:
        return _text(f"Couldn't use the webcam: {e}")
    content = [{"type": "image", "data": base64.b64encode(s).decode(), "mimeType": "image/jpeg"} for s in shots]
    what = "1 frame" if frames == 1 else f"{frames} frames, {interval:g}s apart"
    content.append({"type": "text", "text": f"Webcam, {what}, taken {datetime.now():%H:%M:%S}. "
                                            "This is the camera's view, not a mirror: the user's right hand "
                                            "appears on the left of the image."})
    return {"content": content}


# ---- live camera recording (the session belongs to Jarvis: claps start it too) ----

_camera = None  # the Jarvis object, set by use_camera(): camera_record(), camera_stop(), .camera


def use_camera(owner):
    global _camera
    _camera = owner


def _live_shots(frames, interval):
    """Frames from the running recording, if there is one (the webcam can't be opened twice)."""
    session = getattr(_camera, "camera", None)
    if not (session and session.active):
        return None
    shots = []
    for i in range(frames):
        if i:
            time.sleep(interval)
        shots.append(session.latest_jpeg())
    return shots


@tool("record_video", "Record a video with the webcam: one continuous recording with sound, saved to Movies/Prime. "
      "seconds: record at least this long (e.g. 120 for 'record for 2 minutes'). If a recording is already "
      "running it is extended, never restarted. It stops by itself once nothing moves and nothing is said for "
      "15 seconds (after the requested time). While it runs, every request comes with the live frame.",
      {"type": "object", "properties": {"seconds": {"type": "number"}}})
async def record_video(args):
    if _camera is None:
        return _text("The camera isn't available right now.")
    return _text(await asyncio.to_thread(_camera.camera_record, args.get("seconds")))


@tool("stop_recording", "Stop the webcam recording and save the video.", {})
async def stop_recording(args):
    if _camera is None:
        return _text("Nothing is recording.")
    return _text(await asyncio.to_thread(_camera.camera_stop))


_watcher = None  # watcher.Watcher, set by use_watcher()


def use_watcher(watcher):
    global _watcher
    _watcher = watcher


@tool("watch_for", "Keep watching through the webcam and tell the user out loud when something becomes true, e.g. "
      "'tell me when I stand up', 'let me know if someone comes in'. condition must be checkable in one picture: "
      "'the person is standing', 'more than one person is visible', 'nobody is in front of the laptop'. "
      "minutes: how long to keep watching (default 10, max 60). Starting a new watch replaces the old one.",
      {"type": "object", "properties": {"condition": {"type": "string"}, "minutes": {"type": "number"}},
       "required": ["condition"]})
async def watch_for(args):
    if _watcher is None:
        return _text("Watching isn't available right now.")
    minutes = _watcher.start(args["condition"].strip(), args.get("minutes") or 10)
    return _text(f"Watching for '{args['condition']}' for up to {minutes:g} minutes; "
                 "the user will be told out loud when it happens. The webcam light stays on meanwhile.")


@tool("watch_screen", "Keep watching the Mac's screen (a screenshot every couple of seconds, checked only when it "
      "changes) and tell the user out loud when something becomes true, e.g. 'a download finishes', 'an error "
      "dialog appears', 'the build says done'. condition must be checkable in one screenshot. minutes: how long "
      "(default 10, max 60). Replaces any running watch.",
      {"type": "object", "properties": {"condition": {"type": "string"}, "minutes": {"type": "number"}},
       "required": ["condition"]})
async def watch_screen(args):
    if _watcher is None:
        return _text("Watching isn't available right now.")
    minutes = _watcher.start(args["condition"].strip(), args.get("minutes") or 10, source="screen")
    return _text(f"Watching the screen for '{args['condition']}' for up to {minutes:g} minutes; "
                 "the user will be told out loud when it happens.")


@tool("stop_watching", "Stop a watch started with watch_for or watch_screen.", {})
async def stop_watching(args):
    if _watcher is None or not _watcher.stop():
        return _text("Nothing was being watched.")
    return _text("Stopped watching; the webcam is off.")


# ---- reminders ------------------------------------------------------------------

_store = None  # memory.store.Store, set by use_store(); reminders live in its database


def use_store(store):
    global _store
    _store = store


def _when(due):
    d = datetime.fromisoformat(due)
    today = datetime.now().date()
    day = "today" if d.date() == today else "tomorrow" if d.date() == today + timedelta(days=1) \
        else f"{d:%a %d %b %Y}"
    return f"{day} at {d:%H:%M}"


@tool("set_reminder", "Set a reminder that Prime will say out loud when it's due. Give either in_minutes (from "
      "now) or at (local time, 'YYYY-MM-DD HH:MM'). text is what to remind about, phrased to be read back, "
      "e.g. 'call your mother'.",
      {"type": "object", "properties": {"text": {"type": "string"}, "in_minutes": {"type": "number"},
                                        "at": {"type": "string"}},
       "required": ["text"]})
async def set_reminder(args):
    if _store is None:
        return _text("Reminders aren't available: no database is open.")
    try:
        if args.get("in_minutes") is not None:
            due = datetime.now() + timedelta(minutes=float(args["in_minutes"]))
        elif args.get("at"):
            due = datetime.fromisoformat(args["at"].strip().replace("T", " "))
        else:
            return _text("Give in_minutes or at.")
    except ValueError:
        return _text(f"Couldn't read the time '{args.get('at')}'; use 'YYYY-MM-DD HH:MM'.")
    if due < datetime.now() - timedelta(minutes=1):
        return _text(f"That time ({due:%Y-%m-%d %H:%M}) is in the past. It's now {datetime.now():%Y-%m-%d %H:%M}.")
    r = _store.add_reminder(due.replace(second=0, microsecond=0), args["text"].strip())
    return _text(f"Reminder #{r['id']} set for {_when(r['due'])}: {r['text']}.")


@tool("list_reminders", "List upcoming reminders, with their ids and the current time.", {})
async def list_reminders(args):
    if _store is None:
        return _text("Reminders aren't available: no database is open.")
    rows = _store.reminders()
    lines = [f"#{r['id']} {_when(r['due'])}: {r['text']}" for r in rows]
    return _text(f"Now: {datetime.now():%a %Y-%m-%d %H:%M}.\n" + ("\n".join(lines) or "No upcoming reminders."))


@tool("cancel_reminder", "Cancel a reminder by its id (from list_reminders).",
      {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]})
async def cancel_reminder(args):
    if _store is None:
        return _text("Reminders aren't available: no database is open.")
    r = _store.get_reminder(int(args["id"]))
    if not r or r["status"] != "pending":
        return _text(f"No upcoming reminder #{args['id']}.")
    _store.set_reminder_status(r["id"], "cancelled")
    return _text(f"Cancelled reminder #{r['id']}: {r['text']}.")


ALL = [screenshot, click, move_mouse, drag, scroll, type_text, press_keys,
       list_windows, focus_window, list_secrets, save_secret, type_secret, delete_secret,
       list_accounts, fill_login, type_email_code, list_apps, open_app,
       look, record_video, stop_recording, watch_for, watch_screen, stop_watching, set_reminder, list_reminders, cancel_reminder]
SERVER = create_sdk_mcp_server("jarvis", tools=ALL)

# Run without asking: they only look or open an app, or store what the user explicitly handed over.
NO_ASK = ["mcp__jarvis__screenshot", "mcp__jarvis__list_windows", "mcp__jarvis__list_accounts",
          "mcp__jarvis__list_apps", "mcp__jarvis__open_app",
          "mcp__jarvis__list_secrets", "mcp__jarvis__save_secret", "mcp__jarvis__look",
          "mcp__jarvis__watch_for", "mcp__jarvis__watch_screen", "mcp__jarvis__stop_watching",
          "mcp__jarvis__record_video", "mcp__jarvis__stop_recording",
          "mcp__jarvis__set_reminder", "mcp__jarvis__list_reminders", "mcp__jarvis__cancel_reminder"]

# One "yes" to a login covers all its steps (username, password, email code) for that request.
LOGIN_TOOLS = {"mcp__jarvis__fill_login", "mcp__jarvis__type_email_code"}

# Plain-English labels for permission requests.
LABELS = {
    "click": "click the mouse", "move_mouse": "move the mouse", "drag": "drag with the mouse",
    "scroll": "scroll", "type_text": "type on the keyboard", "press_keys": "press keys",
    "focus_window": "switch windows", "type_secret": "type a saved password",
    "delete_secret": "delete a saved password", "look": "look through the webcam",
    "watch_for": "keep watching through the webcam", "watch_screen": "keep watching the screen", "stop_watching": "stop watching",
    "record_video": "record a video", "stop_recording": "stop recording",
    "set_reminder": "set a reminder", "list_reminders": "check your reminders",
    "cancel_reminder": "cancel a reminder",
}
