"""PRIME desktop app for macOS: the assistant from jarvis.py in a steel HUD window.

Voice works as on Windows ("Prime ..." in a sentence, or "Hey Jarvis"); the window adds a live status
core, the conversation and actions taken, a text box, push-to-talk, permission
prompts (buttons, or "yes"/"no" by voice), Prime's memory and the password vault.
The menu-bar atom shows its state; Siri and Shortcuts reach it through siri_bridge.py.

    .venv/bin/python prime_app.py              (or open ~/Applications/Prime.app)
    .venv/bin/python prime_app.py --startup    (how the login item starts it: menu bar only, no window)
"""
import json
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

HERE = Path(__file__).parent
if sys.stdout is None:  # started with pythonw: no console, so log to a file
    sys.stdout = sys.stderr = open(HERE / "jarvis.log", "a", encoding="utf-8", buffering=1)

import desktop

desktop.claim_identity()   # Dock and menu bar: "Prime" with the Hassium icon, not Python

import sounddevice as sd
import webview

from datetime import datetime

import emailcodes
import jarvis
import macos
import mics
import people
import policy
import settings
import siri_bridge
import vault
from core import config
from memory.store import Store

AUTOSTART = True  # start Prime at login (a LaunchAgent; set False to remove it)
# Started at login (--startup), Prime runs in the background: no window, just the menu-bar atom, listening.
# Calling it ("Prime", claps, Ctrl+Option+J) opens the window; closing the window hides it again.
BACKGROUND = "--startup" in sys.argv


class Api:
    """Methods callable from the page as window.pywebview.api.<name>()."""

    def __init__(self):
        self._window = None
        self._ears = None
        self._started = False
        self._pending = None  # (event, box) of the permission request being shown
        self._config = config.load()
        self._settings = settings.load()
        settings.apply(self._settings, jarvis, desktop)   # the voice, name, wake sensitivity... you chose
        self._store = Store(self._config.db_path)
        self._store.prune_history(self._settings["history_days"] or self._config.history_days)
        policy.use_store(self._store)
        self._jarvis = jarvis.Jarvis(emit=self._emit, approve=self._approve, store=self._store)
        self._icon = desktop.TaskbarIcon(menu={
            "Show Prime": self.show,
            "Settings": self.open_settings,
            "Talk": self.talk,
            "Mute / unmute mic": self._tray_mute,
            "Stop all": self.emergency_stop,
            "Quit": self._quit,
        })
        self._state = "booting"
        self._quitting = False
        self._placed = False  # has the window been put in its place yet?
        self._outbox = queue.Queue()  # window updates, sent in order by one thread so nobody waits on the UI
        threading.Thread(target=self._sender, daemon=True).start()
        self._bridge = None           # the Siri Shortcut bridge (siri_bridge.Server), started in ready()

    def _emit(self, kind, data=None):
        if kind == "state":
            self._state = data
            if not self._pending:
                self._icon.set("muted" if data == "idle" and self._ears and self._ears.paused else data)
        elif kind == "approval":
            self._icon.set("asking")
        elif kind == "approval_done":
            self._icon.set(self._state)
        if kind == "quit":
            self._quit()
            return
        if kind == "wake":
            self.show()
            return
        self._outbox.put((kind, data))

    def _sender(self):
        while True:
            kind, data = self._outbox.get()
            if not self._window:
                continue
            try:
                self._window.evaluate_js(f"onEvent({json.dumps(kind)}, {json.dumps(data)})")
            except Exception as e:
                print("evaluate_js failed:", e)

    def _tray_mute(self):
        on = self.toggle_mic()
        self._emit("mic", on)

    def _quit(self):
        self._quitting = True
        if self._window:
            self._window.destroy()

    def show(self):
        """Bring the window up from the background (or to the front), unless a game is fullscreen: then Jarvis
        keeps working and answering by voice without covering it."""
        if self._window and not desktop.in_game():
            self._window.show()
            if not self._placed:  # a window created hidden (start at login) never fires "shown": place it now
                self._placed = True
                desktop.place_window()
            desktop.show_window()

    def _on_closing(self):
        """Closing the window hides it; Jarvis keeps listening in the background (tray: Quit to exit)."""
        if self._quitting:
            return True
        desktop.save_window_rect()
        self._window.hide()
        self._emit("note", "Prime is still listening in the background. Menu-bar atom > Quit to exit.")
        return False

    # ---- permission requests (called from the brain's thread) ----

    def _approve(self, summary, detail, category="B", reason=""):
        done, box = threading.Event(), {"decision": None}
        self._pending = (done, box)
        self._emit("approval", {"summary": summary, "detail": detail, "category": category, "reason": reason})
        # Category C needs a click: a voice is never proof of who is asking.
        by_voice = category == "B" and self._jarvis.voice_request and self._ears and not self._ears.paused
        if category == "C":
            self._jarvis.mouth.say(f"This one is sensitive, sir: it {reason}. Please confirm on screen.",
                                   then="thinking")
        else:
            self._jarvis.mouth.say(f"May I {summary}, sir?", then="thinking")
        if by_voice and not done.is_set():
            threading.Thread(target=self._listen_for_answer, args=(done, box), daemon=True).start()
        done.wait(jarvis.PERMISSION_TIMEOUT)
        self._pending = None
        decision = box["decision"] or "deny"
        self._emit("approval_done", decision)
        self._jarvis.refresh_state()
        return decision

    def _listen_for_answer(self, done, box):
        for _ in range(3):  # a few tries if the answer isn't a clear yes or no
            self._ears.flush()
            self._jarvis._set(listening=True)
            audio = self._ears.record(start_timeout=10, cancel=done)
            self._jarvis._set(listening=False)
            if audio is None or done.is_set():
                break
            heard = self._ears.transcribe(audio)
            answer = jarvis.parse_answer(heard)
            print(f"[permission] heard {heard!r} -> {answer}")
            if answer in ("once", "task") and not self._jarvis.owner_voice(audio):
                print("[permission] not the owner's voice; ignored")
                self._emit("note", "That \"yes\" didn't sound like the owner, so it was ignored. Click to approve.")
                continue
            if answer and not done.is_set():
                box["decision"] = answer
                done.set()
                return

    def decide(self, decision):
        if self._pending:
            done, box = self._pending
            box["decision"] = decision
            done.set()

    # ---- emergency stop ----

    def emergency_stop(self):
        self._jarvis.brain.halt()
        self._jarvis.watcher.stop()  # camera off too
        threading.Thread(target=self._jarvis.camera_stop, daemon=True).start()
        self.decide("deny")
        sd.stop()
        self._emit("halted", True)
        self._emit("error", "EMERGENCY STOP: current task interrupted; all actions blocked until you press Resume.")

    def resume(self):
        self._jarvis.brain.resume()
        self._emit("halted", False)
        self._emit("note", "Resumed.")

    def _hotkey_loop(self):
        """Hotkeys from any app: Ctrl+Option+Esc is the emergency stop, Ctrl+Option+J wakes Prime to listen,
        like the Talk button."""
        desktop.install_hotkeys(talk=self.talk, stop=self.emergency_stop)

    # ---- Siri Shortcut bridge ----

    def _bridge_request(self, request):
        """One request from siri_bridge (Siri, Shortcuts, prime_ask.py). Runs to completion and returns the answer.
        Requests arrive through a socket only this macOS account can open, but Siri can be spoken to by anyone in
        the room, so unless Settings says otherwise they act like a guest: anything consequential asks first."""
        cmd = request.get("cmd", "ask")
        if cmd == "ping":
            return {"ok": True, "name": self._settings["name"]}
        if cmd == "show":
            self.show()
            return {"ok": True}
        if cmd != "ask":
            return {"ok": False, "error": f"unknown command {cmd!r}"}
        text = " ".join(str(request.get("text") or "").split())[:2000]
        if not text:
            return {"ok": False, "error": "The request was empty."}
        trusted = bool(self._settings.get("siri_trusted"))
        box = {}
        self._jarvis.handle(text, speaker=None if trusted else "Siri Shortcut (voice not verified)", brain="spawn",
                            reply_box=box, quiet=not request.get("speak", True), trusted=trusted)
        return {"ok": True, "reply": box.get("reply", "")}

    def activity_log(self):
        return policy.recent()

    def history(self):
        """Recent conversation, so the window shows where you left off."""
        return [{"id": m["id"], "role": m["role"], "text": m["text"]} for m in self._store.recent_messages(40)]

    def hide_message(self, mid):
        """Remove one message from view. It is kept in the database; memory is untouched."""
        return self._store.hide_message(int(mid))

    def clear_view(self):
        """Clear the conversation from view. Nothing is deleted and Jarvis's memory is untouched."""
        return self._store.hide_all()

    def clear_history(self):
        self._store.clear_history()

    def export_data(self):
        """Export history, activity, reminders, tasks and memories to a JSON file in the data folder."""
        path = self._config.data_dir / f"export-{datetime.now():%Y%m%d-%H%M%S}.json"
        self._store.export(path)
        return str(path)

    # ---- senses panel: voices, reminders, camera ----

    def senses(self):
        """What the Senses panel shows: enrolled voices (never voiceprints), upcoming reminders, current watch."""
        v = self._jarvis.voices
        w = self._jarvis.watcher
        return {
            "voices": [{"name": n, "owner": n == v.owner()} for n in v.names()] if v else None,
            "reminders": [{"id": r["id"], "due": r["due"], "text": r["text"]} for r in self._store.reminders()],
            "watching": w.condition if w.active else None,
        }

    def forget_voice(self, name):
        ok = bool(self._jarvis.voices and self._jarvis.voices.forget(name))
        if ok:
            policy.log("forget_voice", "-", "ui", f"Forgot {name}'s voice")
        return ok

    def cancel_reminder(self, rid):
        return self._store.set_reminder_status(int(rid), "cancelled")

    def stop_watching(self):
        """The banner's Stop button: ends a camera watch and a camera recording."""
        self._jarvis.watcher.stop()
        threading.Thread(target=self._jarvis.camera_stop, daemon=True).start()

    def shutdown(self):
        """Clean shutdown: stop audio and the camera (saving any recording), close the database."""
        sd.stop()
        self._jarvis.watcher.stop()
        self._jarvis.camera_stop()
        self._store.close()

    # ---- voice loop ----

    def _voice_loop(self):
        self._emit("state", "booting")
        try:
            self._ears = jarvis.Ears(self._settings["mic_device"])
        except Exception as e:
            traceback.print_exc()
            self._emit("error", f"Microphone unavailable ({e}). Text only.")
            self._jarvis.refresh_state()
            return
        self._emit("mic", True)
        self._jarvis.ears = self._ears
        self._jarvis.mic_changed()                         # note which microphone this is, use what is known about it
        threading.Thread(target=self._mic_watch, daemon=True).start()
        try:
            self._jarvis.run_voice(self._ears)
        except Exception as e:
            traceback.print_exc()
            self._emit("error", f"Voice stopped: {e}")

    # ---- page API ----

    def ready(self):
        if not self._started:
            self._started = True
            threading.Thread(target=self._voice_loop, daemon=True).start()
            threading.Thread(target=self._hotkey_loop, daemon=True).start()
            threading.Thread(target=self._desktop_setup, daemon=True).start()
            try:
                self._bridge = siri_bridge.Server(self._bridge_request).start()
                print(f"[siri] bridge listening at {siri_bridge.SOCKET}")
            except Exception as e:
                print(f"[siri] bridge unavailable: {e}")
        return {"ask": jarvis.ASK_PERMISSION, "admin": desktop.is_admin(),
                "memoryDir": str(jarvis.MEMORY_DIR), "greeting": self._greeting(), "background": BACKGROUND,
                "name": self._settings["name"]}

    def _greeting(self):
        """What the start-up greeting card shows."""
        today = datetime.now().date().isoformat()
        due_today = [r for r in self._store.reminders() if r["due"].startswith(today)]
        lines = [datetime.now().strftime("%A, %d %B"), "All systems online"]
        if due_today:
            lines.append(f"{len(due_today)} reminder{'s' if len(due_today) > 1 else ''} today")
        return {"title": jarvis.greeting(jarvis.owner_name()), "lines": lines}

    def _desktop_setup(self):
        """Start at login, Prime.app with its icon, and the macOS permission prompts; quietly, every start (cheap,
        self-repairing)."""
        try:
            macos.request_permissions()   # Accessibility and Screen Recording: macOS asks once
            if AUTOSTART and self._settings["start_at_login"]:
                err = desktop.enable_autostart(hidden=self._settings["start_hidden"])
                print(f"[desktop] start at login: {'on' if not err else err}")
            elif desktop.autostart_enabled():
                desktop.disable_autostart()
                print("[desktop] start at login: off")
            print(f"[desktop] app: {', '.join(desktop.make_shortcuts())}")
        except Exception as e:
            print(f"[desktop] setup failed: {e}")

    # ---- settings: everything the Settings page shows and changes ----

    def settings_get(self):
        return {"values": dict(self._settings), "defaults": dict(settings.DEFAULTS), "voices": settings.VOICES,
                "telugu_voices": settings.TELUGU_VOICES, "admin": desktop.is_admin()}

    def settings_set(self, key, value):
        """Check, save and apply one setting at once. Returns {"ok", "value"} or {"ok": False, "error"}."""
        try:
            value = settings.clean(key, value)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        self._settings[key] = value
        settings.save(self._settings)
        settings.apply(self._settings, jarvis, desktop)
        if key == "clap_peak":
            self._jarvis.apply_mic()                          # a clap level learned for this mic still wins
        elif key == "history_days":
            self._store.prune_history(value)
        elif key in ("start_at_login", "start_hidden"):
            threading.Thread(target=self._desktop_setup, daemon=True).start()
        elif key == "name":
            self._emit("renamed", value)
        elif key == "mic_device":
            threading.Thread(target=self._follow_mic_setting, daemon=True).start()
        return {"ok": True, "value": value}

    def preview_voice(self, kind, name, rate, pitch):
        """Say a sample in a voice, with the speed and pitch being tried, before it is saved."""
        if kind == "telugu":
            voice = {"voice": name, "rate": f"{settings.TELUGU_BASE[0] + int(rate):+d}%",
                     "pitch": f"{settings.TELUGU_BASE[1] + int(pitch):+d}Hz"}
            text = "నమస్తే, ఈ రోజు ఏం చేద్దాం?"
        else:
            voice = {"voice": name, "rate": f"{int(rate):+d}%", "pitch": f"{int(pitch):+d}Hz"}
            text = f"Good evening. I'm {self._settings['name']}, and all systems are online."
        threading.Thread(target=self._jarvis.mouth.preview, args=(text, voice), daemon=True).start()
        return True

    def reset_window(self):
        """Forget where the window was left and put it back at the right edge of the screen."""
        desktop.WINDOW_FILE.unlink(missing_ok=True)
        desktop.place_window()
        return True

    # ---- microphones ----

    def _follow_mic_setting(self):
        """Use the microphone chosen in Settings ("" = the Mac's default) right away."""
        ears, want = self._ears, self._settings["mic_device"]
        if not ears:
            return
        if want and mics.stream_index(want) is None:
            want = ""                                     # the chosen mic isn't plugged in: use the default meanwhile
        if (want or mics.default_key()) != ears.device_key:
            ears.reopen(want)
            self._jarvis.mic_changed()

    def _mic_watch(self):
        """Notice a microphone being plugged in, unplugged or chosen, and switch to it without a restart."""
        while not self._quitting:
            time.sleep(2)
            try:
                self._follow_mic_setting()
            except Exception as e:
                print(f"[mic] watcher: {e}")

    def mics_list(self):
        """Every microphone met: plugged in or not, which is in use, what Jarvis learned about each, and who."""
        plugged = {m["key"]: m for m in mics.connected()}
        known = self._jarvis.mics.known()
        voices = self._jarvis.voices
        current = getattr(self._ears, "device_key", None)
        out = []
        for key in dict.fromkeys(list(plugged) + list(known)):
            rec = known.get(key, {})
            out.append({
                "key": key, "connected": key in plugged, "default": bool(plugged.get(key, {}).get("default")),
                "current": key == current, "imprinted": bool(rec.get("imprinted")), "match": rec.get("match"),
                "clap_peak": rec.get("clap_peak"), "last_seen": rec.get("last_seen", ""),
                "people": [n for n in (voices.names() if voices else []) if key in voices.devices_of(n)]})
        return sorted(out, key=lambda m: (not m["current"], not m["connected"], m["key"].lower()))

    def mic_set(self, key, field, value):
        """Change what is learned about a microphone: how strict voice matching is, or its clap level (None resets)."""
        limits = {"match": (0.25, 0.6, 2), "clap_peak": (300, 8000, 0)}
        if field not in limits:
            return {"ok": False, "error": "unknown field"}
        if value is not None:
            low, high, digits = limits[field]
            value = min(high, max(low, float(value)))
            value = round(value, digits) if digits else int(value)
        self._jarvis.mics.update(key, **{field: value})
        if field == "clap_peak" and key == getattr(self._ears, "device_key", None):
            self._jarvis.apply_mic(key)
        return {"ok": True, "mics": self.mics_list()}

    def mic_forget(self, key):
        """Forget a microphone and everyone's voiceprints made on it. (It is learned again if it is used again.)"""
        voices = self._jarvis.voices
        gone = voices.forget_device(key) if voices else 0
        self._jarvis.mics.forget(key)
        return {"ok": True, "voiceprints": gone, "mics": self.mics_list()}

    def mic_learn(self, person=None):
        """Guided set-up for the microphone in use: voice, strictness and clap level. Jarvis speaks and listens."""
        ears = self._ears
        if not ears:
            return {"ok": False, "error": "No microphone is running."}
        ears.run_in_loop(lambda: self._jarvis.learn_mic(ears, person or None))
        return {"ok": True}

    # ---- people (voice and face) ----

    def people_list(self):
        return people.list_people(self._jarvis.voices)

    def person_rename(self, old, new):
        return people.rename(self._jarvis.voices, old, new)

    def person_owner(self, name):
        return people.set_owner(self._jarvis.voices, name)

    def person_forget(self, name, what="both"):
        return people.forget(self._jarvis.voices, name, what)

    def person_add_voice(self, name):
        """Guided: the person repeats a few sentences after each beep and Jarvis learns their voice on this mic."""
        name, ears = " ".join((name or "").split())[:40], self._ears
        if not name or not ears or not self._jarvis.voices:
            return {"ok": False, "error": "Voice recognition isn't running."}
        ears.run_in_loop(lambda: self._jarvis.add_person(ears, name))
        return {"ok": True}

    def person_add_face(self, name, owner=False):
        """Look at the camera for a few seconds and learn the face. Runs in the background and reports back."""
        name = " ".join((name or "").split())[:40]
        if not name:
            return {"ok": False, "error": "The name can't be empty."}

        def run():
            self._jarvis.mouth.say("Look at the camera for a few seconds, please.")
            ok, message = people.enroll_face(name, owner=bool(owner))
            self._jarvis.mouth.say(f"Done. I'll know {name}'s face." if ok else f"That didn't work. {message}")
            self._emit("people_changed")
        threading.Thread(target=run, daemon=True).start()
        return {"ok": True}

    def send(self, text):
        self._jarvis.submit(text)

    def talk(self):
        if self._ears:
            self._ears.manual.set()

    def toggle_mic(self):
        if not self._ears:
            return False
        self._ears.paused = not self._ears.paused
        self._icon.set("muted" if self._ears.paused else self._state)
        return not self._ears.paused

    def stop(self):
        sd.stop()

    def open_settings(self):
        """Show the window with the Settings page open (from the tray icon)."""
        self.show()
        self._emit("open_settings")

    def log_error(self, message):
        """An error on the page, written to jarvis.log so it can be traced."""
        print(f"[page] {message}")
        return True

    def sound_levels(self):
        """How loud you and Jarvis are right now (0 to 1 each): the display's spheres glow with real sound."""
        ears = self._ears
        return {"mic": 0.0 if not ears or ears.paused else ears.level_norm(),
                "voice": self._jarvis.mouth.speaking_level()}

    def new_chat(self):
        sd.stop()
        self._jarvis.new_chat()

    def memory(self):
        """Jarvis's memory, skills and scripts: path and the start of each (never the vault)."""
        root = jarvis.MEMORY_DIR
        if not root.exists():
            return []
        order = {"MEMORY.md": 0, str(Path("skills") / "SKILLS.md"): 2}

        def rank(p):
            rel = str(p.relative_to(root))
            return (order.get(rel, 1 if p.parent == root else 3 if p.parent.name == "skills" else 4), rel)

        files = [p for p in root.rglob("*") if p.is_file() and "vault" not in p.relative_to(root).parts
                 and p.suffix.lower() in (".md", ".sh", ".zsh", ".applescript", ".py", ".txt")]
        return [{"name": str(p.relative_to(root)), "text": p.read_text(encoding="utf-8", errors="replace")[:1200]}
                for p in sorted(files, key=rank)]

    def open_memory(self):
        jarvis.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["open", str(jarvis.MEMORY_DIR)])

    def vault_data(self):
        """Everything the Vault panel shows; never passwords."""
        return {"accounts": vault.accounts(), "secrets": vault.names(), "email": vault.email_info()}

    def vault_add_account(self, service, username, password, usual, twofa):
        if not service.strip() or not username.strip() or not password:
            return "Service, username and password are all needed."
        vault.add_account(service, username, password, usual, twofa)
        return ""

    def vault_delete_account(self, acc_id):
        return vault.delete_account(acc_id)

    def vault_add(self, name, value):
        if not name.strip() or not value:
            return False
        vault.set_secret(name, value)
        return True

    def vault_delete(self, name):
        return vault.delete_secret(name)

    def vault_set_email(self, address, app_password, host):
        """Save the 2FA inbox and check it connects. Returns an error message, or ''."""
        try:
            vault.set_email(address, app_password, host)
            emailcodes.test_connection()
            return ""
        except Exception as e:
            return f"Saved, but couldn't connect: {e}"

    def vault_clear_email(self):
        vault.clear_email()


def main():
    if not desktop.single_instance():  # already running: it was brought to the front
        return
    api = Api()
    api._window = webview.create_window(
        desktop.TITLE, url=str(HERE / "ui.html"), js_api=api,
        width=440, height=760, min_size=(380, 600), background_color="#0e1114", hidden=BACKGROUND)
    desktop.WINDOW = api._window
    api._window.events.shown += lambda: (setattr(api, "_placed", True), desktop.place_window())  # window created visible
    api._window.events.closing += api._on_closing
    try:
        webview.start()
    finally:
        if api._bridge:
            api._bridge.stop()
        api._icon.close()
        api.shutdown()


if __name__ == "__main__":
    main()
