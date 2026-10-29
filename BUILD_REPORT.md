# Prime build report

Built on 29 Oct 2026, on the Windows PC, from `C:\Users\ADMIN\Desktop\jarvis`. That folder was copied, never modified; the only files that changed there are `jarvis.log` and `session.txt`, which the running Jarvis app writes itself.

## Status

- **Everything in BRIEF.md is built.** The macOS-only code paths (AppKit, Quartz, the Keychain, Core Audio, the camera and Siri) could not be run here and are unverified until they run on the Mac.
- **Tests:** 274 passed and 4 skipped (`pytest tests`, run on Windows with the original Jarvis venv against a temporary data folder).
  - The skips include the two Unix-socket bridge tests; they run on the Mac.
  - New tests cover the Mac port: robot voice, Siri bridge, Prime.app and login item, logo, macOS policy rules and Core Audio.

## What was built

| Requirement | Done |
|---|---|
| Name: Prime | The app, window title, menu bar, Dock, system prompt, UI text and settings all use Prime. Prime answers to "Prime" anywhere in a sentence. The pre-trained "Hey Jarvis" wake phrase still works, because there is no pre-trained "Hey Prime" model. |
| Deep robot voice as an effect | New `robovoice.py`: phase-vocoder pitch shift (−5 semitones, length kept), a sub-octave layer, a channel vocoder on a sawtooth+noise carrier, a metallic comb filter and light ring modulation. It sits on top of `en-US-ChristopherNeural` (edge-tts, slightly slower and lower). The offline fallback is macOS `say` with the same effect. Robot voice, depth and pitch can be set in Settings. It is pure DSP on a normal TTS voice and imitates no actor. |
| Voice measured | Pitch falls from 106.7 Hz to 81.4 Hz; −5 semitones predicts 79.9 Hz. A 7.4 s reply is processed in 0.19 s. Listen to `samples/voice_plain.wav` and `samples/voice_prime.wav`. |
| Steel robotic UI | `ui.html` restyled: brushed-metal gradients with hairline texture, bevel highlights, a cool-blue accent (`#4fb3ff`), tighter corners, letter-spaced labels and SF fonts. The big display and header mark are now the animated Hassium atom; shells counter-rotate while Prime is busy and glow with the sound level. The UI calls all 43 of the same backend methods as before. |
| Hassium logo, 108 electrons in shells 2-8-18-32-32-14-2 | `make_logo.py` produces the following in `assets/` (a test checks for exactly 108 electrons and the shell counts): `prime_logo.svg`, `prime_icon.svg`, `prime_logo.png`, `prime_icon.png` (1024 px), `Prime.icns` (16 to 1024 px), `Prime.iconset/` and 6 menu-bar state icons. |
| PowerShell → zsh/osascript | System prompt rewritten for macOS (zsh, `osascript`, `open -a`, `shortcuts`, `mdfind`, Mac key names, the permissions to ask for). The policy now flags dangerous macOS commands: `sudo`, `diskutil erase`, `csrutil`, Keychain dumps, `curl … \| sh`, `launchctl bootout`, `chmod`/`chown`, `shutdown`, and writes to `/System`, `/etc`, `/usr` and similar. |
| win32 screen/keyboard → Quartz | New `macos.py`: Quartz window list, focus via NSRunningApplication and System Events, fullscreen-game detection, app discovery in the Applications folders, Accessibility and Screen Recording checks, Mac key names, and paste or private-keystroke typing for non-ASCII text. Screenshot coordinates now account for Retina scaling. |
| Windows audio → CoreAudio | `mics.py` uses Core Audio; `say` replaces SAPI. |
| Desktop integration | `desktop.py` rewritten for the Mac: Dock name and icon, menu-bar status item with the tray menu, global hotkeys ⌃⌥J and ⌃⌥Esc (NSEvent monitors), window placement, a LaunchAgent login item, and a generated `~/Applications/Prime.app` (Info.plist with microphone, camera and automation usage strings, ad-hoc signed). |
| Vault | DPAPI replaced by Fernet; the random key is kept in the macOS Keychain (`keyring`). No key or password is written to any file. |
| Siri Shortcut bridge | `siri_bridge.py` serves a private Unix socket (mode 600, so no token is needed). `prime_ask.py` is the client. README step 7 shows how to build the "Hey Siri, Prime" Shortcut. Siri requests count as guest requests (consequential actions ask first) unless `siri_trusted` is set. |
| No secrets in any file | Scanned: no passwords, keys or tokens. The only match is a fake `sk-…` string inside the redaction test. |
| README_MAC.md | Step-by-step Mac install, permissions, use, Siri, login item, memory migration and troubleshooting. |
| Installer | `install_mac.sh` (Python 3.12, venv, packages, models, config, Prime.app) and `run_prime.sh`. |

Other changes:
- Data folder is now `~/PrimeMemory` (or `$PRIME_DATA`) instead of `D:\JarvisMemory`.
- Videos are saved to `~/Movies/Prime`.
- The camera uses AVFoundation.
- `requirements.txt` was replaced by a Mac list (PyObjC and `keyring` added; pywin32, pycaw, comtypes, pythonnet, pyttsx3 and pygetwindow dropped).
- Text files were converted to LF line endings.
- Removed:
  - `make_icons.py`, `render_icon_gpu.py` and the `.ico` frames (Windows taskbar and GPU only);
  - `ui.hud.html` and `ui.tva.html` (old looks, unused);
  - `fastpath.py`: it was already cut off mid-line in the Jarvis folder and nothing imported it.
- The internal module names `jarvis.py` and `jarvis_app.py`, and the MCP tool prefix `mcp__jarvis__`, were kept so the tests, saved permissions and skills keep working. `prime_app.py` is the entry point.

## Left to do on the Mac

1. Copy the folder over, run `xattr -dr com.apple.quarantine ~/Desktop/prime`, then `zsh install_mac.sh` (README steps 2 to 4). Install Claude Code and sign in first if it isn't installed.
2. Grant the permissions when asked: Microphone, Accessibility, Screen Recording (then reopen Prime), Camera and Automation.
3. Smoke-test what couldn't be run on Windows:
   - menu-bar atom and state colours;
   - Dock icon and name;
   - ⌃⌥J and ⌃⌥Esc from another app;
   - window placement;
   - `open_app`, `list_windows` and `focus_window`;
   - a screenshot and click on Retina;
   - the vault (the first save creates the Keychain item);
   - the camera;
   - the login item;
   - `prime_ask.py "what time is it"`.
4. Run the tests on the Mac: `.venv/bin/python -m pytest tests -q`. The two socket tests run there.
5. Build the Siri Shortcut (README step 7).
6. **Version pins:** `requirements.txt` keeps the Windows build's versions for shared packages. If pip can't find a macOS arm64 wheel for one of them (most likely `sherpa-onnx`, `ctranslate2` or `opencv-python`), loosen that pin to `>=`.
7. **Optional:**
   - copy Jarvis's memory (without `vault/`) and the Telugu model from the PC (README step 9), and re-add logins in the Vault panel;
   - train a custom "Hey Prime" openWakeWord model if you want a dedicated wake phrase;
   - turn on `siri_trusted` in `settings.json` if Siri requests should act without asking.

## Known limitations

- Waking on "Prime" uses speech recognition, so phrases like "Amazon Prime" on TV could wake it. Once your voice is enrolled, the name in an unrecognised voice is ignored, and *Wake by name* can be switched off.
- `siri_trusted` isn't on the Settings page yet; edit `~/PrimeMemory/settings.json` to change it.
- Speech recognition runs on the CPU. CTranslate2 has no Metal backend, but `small` is quick on M-series chips.
- The Dock may show "Python" if Prime is started from Terminal instead of `Prime.app`.
