# Prime: Jarvis for macOS

Build a macOS port of the Jarvis voice assistant in this folder (C:\Users\ADMIN\Desktop\prime).
The Windows source is at C:\Users\ADMIN\Desktop\jarvis (read config.toml and the code there; copy, don't modify it).

Requirements:
- Name: Prime. Runs on the user's MacBook Pro (Apple Silicon, recent macOS).
- Voice: a deep, robotic voice in the spirit of Optimus Prime, made as an audio effect (pitch down, metallic/vocoder layer) on top of a normal TTS voice. Do not clone or imitate any actor's real voice.
- Theme: steel, robotic UI (brushed metal greys, cool blue accents).
- Logo: a Hassium (Hs) atom with all 108 electrons in shells 2-8-18-32-32-14-2, as SVG plus PNG/ICNS app icon.
- Replace Windows-only pieces (PowerShell, win32 screen/keyboard control, Windows audio) with macOS equivalents (zsh/osascript, pyautogui or Quartz, CoreAudio). Add a Siri Shortcut bridge.
- No passwords or secrets in any file.
- Write README_MAC.md with step-by-step Mac setup and run instructions.
- When finished, write BUILD_REPORT.md summarising what was built and anything left to do on the Mac.
