"""Watching the webcam over time: "tell me when I stand up", "let me know if someone comes in".

The camera stays on (its light shows it) for at most the requested minutes. Each
second a small grey copy of the frame is compared with the last one checked, on
this laptop; only when the picture has changed (or a minute has passed) is one
frame sent to a fast Claude model to judge the condition. When it's met, Jarvis
says so and stops watching. Frames are never written to disk.
"""
import asyncio
import base64
import json
import re
import threading
import time
from pathlib import Path

JUDGE_MODEL = "haiku"
CHANGE = 12.0          # mean pixel difference (0-255) that counts as "something moved"
RECHECK_SECONDS = 60   # judge at least this often even if nothing seems to change
MAX_MINUTES = 60


def judge(jpeg, condition, cli_path, what="a webcam picture"):
    """(met, what was seen) for one frame, asked of a fast Claude model with no tools."""
    from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

    async def messages():
        yield {"type": "user", "message": {"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                         "data": base64.b64encode(jpeg).decode()}},
            {"type": "text", "text": f"This is {what}. Is this true right now: {condition}?\n"
                                     'Reply with JSON only: {"met": true or false, "seen": "<what you see, under 12 words>"}'}]}}

    async def run():
        opts = ClaudeAgentOptions(cli_path=str(cli_path), model=JUDGE_MODEL, max_turns=1,
                                  allowed_tools=[], setting_sources=[])
        result = ""
        async for m in query(prompt=messages(), options=opts):  # read to the end so the stream closes cleanly
            if isinstance(m, ResultMessage):
                result = m.result or ""
        return result

    reply = asyncio.run(run())
    found = re.search(r"\{.*\}", reply, re.S)
    try:
        data = json.loads(found.group(0)) if found else {}
    except json.JSONDecodeError:
        data = {}
    return bool(data.get("met")), str(data.get("seen", "")).strip()


class Watcher:
    """One watch at a time. on_event(text) is called with what to say aloud."""

    def __init__(self, cli_path, on_event, capture_index=0, judge_fn=judge, on_state=None):
        self.cli_path = cli_path
        self.on_event = on_event
        self.on_state = on_state or (lambda condition: None)  # condition while watching, None when stopped
        self.index = capture_index
        self.judge = judge_fn
        self.condition = None
        self.source = "camera"  # "camera" or "screen"
        self.until = 0.0
        self._stop = threading.Event()
        self._thread = None

    @property
    def active(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self, condition, minutes, source="camera"):
        self.stop()
        self.source = source
        minutes = max(0.5, min(float(minutes), MAX_MINUTES))
        self.condition, self.until = condition, time.time() + minutes * 60
        self._stop.clear()
        self.on_state(condition)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return minutes

    def stop(self):
        """Stop watching. True if a watch was running."""
        was = self.active
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._thread = None
        return was

    def _screen_frames(self):
        """Yield (small grey frame, jpeg bytes) of the whole screen about every 2 seconds until stopped."""
        import io
        import numpy as np
        import pyautogui
        while not self._stop.is_set():
            img = pyautogui.screenshot().convert("RGB")
            small = np.asarray(img.convert("L").resize((96, round(img.height * 96 / img.width))), dtype="float32")
            if img.width > 1280:
                img = img.resize((1280, round(img.height * 1280 / img.width)))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=70)
            yield small, buf.getvalue()
            self._stop.wait(2.0)

    def _frames(self):
        """Yield (small grey frame, jpeg bytes) about once a second until stopped."""
        import cv2
        cam = cv2.VideoCapture(self.index, tools_backend())
        try:
            if not cam.isOpened():
                raise RuntimeError("no webcam found, or another app is using it")
            for _ in range(8):
                cam.read()
            while not self._stop.is_set():
                ok, img = cam.read()
                if not ok:
                    raise RuntimeError("the webcam stopped sending pictures")
                h, w = img.shape[:2]
                small = cv2.cvtColor(cv2.resize(img, (64, round(h * 64 / w))), cv2.COLOR_BGR2GRAY)
                if w > 960:
                    img = cv2.resize(img, (960, round(h * 960 / w)))
                yield small.astype("float32"), cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 75])[1].tobytes()
                self._stop.wait(1.0)
        finally:
            cam.release()

    def _run(self):
        condition, last, last_time = self.condition, None, 0.0
        try:
            frames = self._screen_frames() if self.source == "screen" else self._frames()
            what = "a screenshot of the laptop screen" if self.source == "screen" else "a webcam picture"
            for small, jpeg in frames:
                if time.time() > self.until:
                    self.on_event(f"I've stopped watching, sir; I didn't see that {condition} happen.")
                    return
                changed = last is None or float(abs(small - last).mean()) > CHANGE
                if not changed and time.time() - last_time < RECHECK_SECONDS:
                    continue
                last, last_time = small, time.time()
                met, seen = self.judge(jpeg, condition, self.cli_path, what) if self.source == "screen" else self.judge(jpeg, condition, self.cli_path)
                print(f"[watch] {condition!r}: {met} ({seen})")
                if met and not self._stop.is_set():
                    self.on_event(f"Heads up, sir: {seen or condition}.")
                    return
        except Exception as e:
            self.on_event(f"I had to stop watching: {e}.")
        finally:
            if self.condition == condition:  # not replaced by a newer watch
                self.on_state(None)


def tools_backend():
    """The webcam driver: AVFoundation on the Mac, DirectShow on Windows."""
    import sys
    import cv2
    return cv2.CAP_AVFOUNDATION if sys.platform == "darwin" else cv2.CAP_DSHOW
