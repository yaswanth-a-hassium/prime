# Prime: Jarvis for the Mac

Prime is the macOS port of Jarvis, your voice assistant with Claude Code as its brain. It runs on a MacBook Pro with Apple Silicon and a recent macOS (macOS 13 Ventura or later). It has a deep robotic voice, a brushed-steel interface and a Hassium (Hs, element 108) logo.

Everything Jarvis did on Windows works the same way here. You wake it by voice or by clapping. It answers aloud, runs commands, drives apps and windows, looks through the camera, sets reminders, recognises voices, and keeps a long-term memory, a list of skills and an encrypted vault. The Windows-only parts were replaced with Mac equivalents:

| Windows Jarvis | Prime on the Mac |
|---|---|
| PowerShell commands | zsh via the Bash tool, `osascript` (AppleScript), `open`, `shortcuts`, `defaults`, `mdfind` |
| win32 screen, keyboard and windows (pygetwindow, ctypes) | pyautogui (Quartz events) with Retina-correct scaling, Quartz window list, System Events focus |
| Start-menu app list, `explorer shell:AppsFolder` | scans `/Applications`, `/System/Applications` and `~/Applications`, then `open -a` |
| WASAPI/MME microphones | Core Audio microphones (follows System Settings › Sound › Input) |
| SAPI (pyttsx3) offline voice | macOS `say`, with the same robot effect added |
| DPAPI-encrypted vault | Fernet-encrypted vault; its random key lives in your login **Keychain** |
| Taskbar icon, tray menu | Dock icon plus a menu-bar atom whose colour shows Prime's state, with the tray menu |
| Ctrl+Alt+J / Ctrl+Alt+End hotkeys | **⌃⌥J** (talk) / **⌃⌥Esc** (emergency stop) |
| Task Scheduler start-up, `.lnk` shortcuts | a LaunchAgent login item and `~/Applications/Prime.app` |
| DirectShow camera | AVFoundation camera; videos go to `~/Movies/Prime` |
| `D:\JarvisMemory` | `~/PrimeMemory` (or `$PRIME_DATA`) |
| (none) | **Siri Shortcut bridge**: "Hey Siri, Prime" |

---

## 1. What you need

- A MacBook Pro with Apple Silicon (M1 to M4 or later) and an internet connection. The neural voice and Claude are online.
- About 2 GB of free disk space: about 600 MB for models, plus the Python packages.
- A Claude subscription (Pro or Max), used by Claude Code.
- **Python 3.11 or later**, ideally 3.12. The installer uses Homebrew's `python@3.12` if no suitable Python is found. The `python3` bundled with macOS is too old.

## 2. Copy the folder to the Mac

Copy the whole `prime` folder to the Mac, for example to `~/Desktop/prime`. AirDrop, a USB stick or iCloud Drive all work. These instructions assume `~/Desktop/prime`; if you put it somewhere else, use that path instead.

If macOS says the files were downloaded from the internet, clear the quarantine flag once:

```zsh
xattr -dr com.apple.quarantine ~/Desktop/prime
```

## 3. Install Homebrew and Claude Code (skip what you already have)

Open **Terminal** (Applications › Utilities):

```zsh
# Homebrew (optional, but the easiest way to get Python 3.12)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Claude Code, Prime's brain
curl -fsSL https://claude.ai/install.sh | bash
claude            # sign in with your Claude account once, then type /exit
```

## 4. Run the installer

```zsh
cd ~/Desktop/prime
zsh install_mac.sh
```

The installer:

1. finds or installs Python 3.12;
2. creates `.venv` and installs `requirements.txt`;
3. checks for Claude Code;
4. creates `config.toml` and `~/PrimeMemory`;
5. downloads the speech-recognition, wake-word and voice-ID models;
6. creates `~/Applications/Prime.app` with the Hassium icon.

It never uses `sudo` and stores no passwords. It's safe to run again.

## 5. First start and macOS permissions

```zsh
open ~/Applications/Prime.app        # or: ./run_prime.sh   (same app, with the log in Terminal)
```

macOS asks for these permissions the first time each is needed. Grant them to **Prime**. If you started it with `./run_prime.sh`, macOS asks for **Terminal** instead.

| Permission | Where | What for |
|---|---|---|
| Microphone | pop-up on first start | hearing "Prime", claps and your requests |
| Accessibility | System Settings › Privacy & Security › Accessibility | mouse, keyboard and ⌃⌥J / ⌃⌥Esc from any app |
| Screen Recording | System Settings › Privacy & Security › Screen Recording | screenshots and window titles. **Quit and reopen Prime afterwards.** |
| Camera | pop-up the first time you say "look at me" | look, watch and record |
| Automation | pop-ups like "Prime wants to control Music" | AppleScript control of other apps |

If a toggle seems to do nothing, remove Prime from the list with **−**, add it again with **+** (`~/Applications/Prime.app`) and reopen Prime.

## 6. Talking to Prime

- Say **"Prime, what time is it?"**, with the name anywhere in the sentence. The trained wake phrase **"Hey Jarvis"** still works too.
- **Clap twice** to wake it. Prime turns on the camera and answers like someone turning round.
- Press **⌃⌥J** (Control+Option+J) anywhere, or use the **Talk** button.
- Type in the window's text box.
- **⌃⌥Esc** is the emergency stop: it stops the current task and blocks all actions until you press Resume.
- "New conversation" starts afresh (long-term memory is kept), "never mind" cancels, and "goodbye" quits.
- "Learn my voice, I'm Sai" enrols your voice. Requests in other voices then ask before acting.

Closing the window keeps Prime listening in the menu bar. To quit, click the menu-bar atom and choose **Quit**.

### The voice

Prime's voice is Microsoft's `en-US-ChristopherNeural` (online), slowed slightly and pitched down, with a robot effect from `robovoice.py`:

- a phase-vocoder pitch shift (−5 semitones);
- a sub-octave layer;
- a channel vocoder on a sawtooth carrier, for the synthetic machine timbre;
- a short metallic comb filter and a touch of ring modulation.

It is signal processing on an ordinary TTS voice. It does not imitate or clone any actor. Change it in **Settings › Prime's voice**:

- **Robot voice**: turns the effect on or off.
- **Robot depth**: from 0 (the plain pitched-down voice) to 100 (full vocoder).
- **Pitch down**: in semitones.

When you're offline, Prime falls back to the Mac's own `say` voice (Daniel) with the same effect. `samples/voice_plain.wav` and `samples/voice_prime.wav` let you hear the voice before and after the effect.

## 7. Siri ("Hey Siri, Prime")

The running Prime listens on a private socket, `~/PrimeMemory/prime.sock`, which only your macOS account can open. `prime_ask.py` sends it a request. Build the Shortcut once:

1. Open **Shortcuts**, then go to **Shortcuts › Settings › Advanced** and turn on **Allow Running Scripts**.
2. Click **+** to make a new shortcut and name it **Prime**. The name is the Siri phrase.
3. Add the **Dictate Text** action and set *Stop Listening* to *After Pause*.
4. Add **Run Shell Script** and set it up like this:
   - Shell: `zsh`
   - Input: *Dictated Text*
   - Pass Input: **as arguments**
   - Script:
     ```zsh
     "$HOME/Desktop/prime/.venv/bin/python" "$HOME/Desktop/prime/prime_ask.py" --silent "$@"
     ```
5. Add **Speak Text** with *Shell Script Result* as the input. Siri then reads the answer.
   - Alternative: remove `--silent` and use **Show Result** instead, so Prime answers in its own robot voice on the Mac.
6. Say **"Hey Siri, Prime"**, then your request. You can also run the shortcut from the menu bar or Spotlight, or give it a keyboard shortcut in its details.

For a typed variant, use **Ask for Input** instead of **Dictate Text**. From Terminal, run `./prime_ask.py "open Safari"`, or `./prime_ask.py --show` to bring the window up.

Siri can be spoken to by anyone in the room, so Siri requests act like a guest's by default: anything that changes the Mac asks for your OK on screen first. If you trust that, turn on `siri_trusted` in `~/PrimeMemory/settings.json`.

## 8. Start at login

Prime adds itself to login (a LaunchAgent at `~/Library/LaunchAgents/com.hassium.prime.plist`) and starts in the menu bar without a window. Turn this off in **Settings › Start at login**. To remove it by hand:

```zsh
rm ~/Library/LaunchAgents/com.hassium.prime.plist
```

## 9. Moving Jarvis's memory across (optional)

Copy `D:\JarvisMemory` from the PC into `~/PrimeMemory` on the Mac, **without the `vault` folder**. The Windows vault is locked to Windows (DPAPI) and can't be read on a Mac, so re-add those logins in Prime's Vault panel. Notes, skills, settings, voiceprints and reminders carry over.

The skills and scripts mention PowerShell, so tell Prime: "Some of your skills are from Windows; convert them to the Mac as you use them." For Telugu, also copy the `models/whisper-telugu-medium-ct2` folder; it can't be downloaded.

## 10. Files

| File | What it is |
|---|---|
| `prime_app.py` | starts the app (window, menu bar, voice, Siri bridge) |
| `jarvis.py` | the assistant: ears, mouth, brain, requests (`--text` for a console) |
| `jarvis_app.py` | the window's backend and its API for `ui.html` |
| `ui.html` | the steel interface |
| `robovoice.py` | the robot voice effect |
| `macos.py` | windows, apps, fullscreen detection, permissions, typing (Quartz/AppKit) |
| `desktop.py` | Dock identity, menu-bar atom, hotkeys, window placement, login item, Prime.app |
| `siri_bridge.py`, `prime_ask.py` | the Siri/Shortcuts bridge and its client |
| `tools.py` | Prime's hands: screen, mouse, keyboard, apps, camera, vault, reminders |
| `vault.py` | the encrypted vault (key in the Keychain) |
| `policy.py` | what needs your approval (now including macOS commands such as `sudo` and `diskutil`) |
| `make_logo.py` | draws the Hassium logo, app icon and menu-bar icons into `assets/` |
| `install_mac.sh`, `run_prime.sh` | install, and run with the log visible |

Logs: `~/Library/Logs/Prime/prime.log` when started from Prime.app; in Terminal when started with `run_prime.sh`.

Tests: `.venv/bin/python -m pytest tests -q`. They never touch your real data.

## 11. Troubleshooting

- **"Claude Code not found"**: run `which claude`. If it lives somewhere unusual, set `cli_path` in `config.toml` and make sure `~/.local/bin` exists.
- **No sound or no microphone**: check System Settings › Sound (input and output), and Privacy & Security › Microphone › Prime.
- **Clicks and keys do nothing**: Prime needs Accessibility (see step 5). Re-add it after moving the folder.
- **Screenshots show only the desktop**: Prime needs Screen Recording, and you must reopen Prime after granting it.
- **The Dock shows "Python"**: start Prime from `Prime.app`, not from Terminal.
- **It wakes on TV dialogue that says "prime"**: enrol your voice ("learn my voice"). Unrecognised voices that say the name are then ignored. You can also turn off *Wake by name* in Settings and rely on "Hey Jarvis", claps or ⌃⌥J.
- **Vault says the key is missing**: the Keychain item "Prime vault" was deleted. Restore it from a backup, or delete `~/PrimeMemory/vault/secrets.dat` and start a fresh vault.
