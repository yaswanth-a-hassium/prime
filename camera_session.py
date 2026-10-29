"""A live camera session: the webcam records one continuous video (with sound) while Jarvis can look at the
current frame at any moment.

It stops by itself once nothing has moved and nothing has been said for IDLE_SECONDS, unless the user asked
for a longer recording (extend()), and never runs longer than MAX_MINUTES. The video is saved to
Movies/Prime as one MP4 (H.264 + AAC); the sound comes from Jarvis's own microphone stream.
"""
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np

FOLDER = Path.home() / "Movies" / "Prime"
IDLE_SECONDS = 15     # stop after this long with no movement and no speech
MAX_MINUTES = 30
FPS = 15
WIDTH = 960
MOVE = 6.0            # mean pixel change (0-255, on a small grey copy) that counts as movement
RATE = 16000


class CameraSession:
    def __init__(self, index=0, on_stop=None, sound_level=400.0):
        self.index = index
        self.on_stop = on_stop or (lambda path, seconds, reason: None)
        self.sound_level = sound_level   # speech louder than this keeps the recording going
        self.path = None
        self.started = None              # when the first frame was written
        self.min_until = 0.0             # keep recording at least until then (asked for "2 minutes")
        self.last_activity = time.time()
        self.reason = ""
        self._latest = None              # newest frame as JPEG
        self._audio = []
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._error = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    # ---- control ----

    def start(self, wait=3.0):
        """Open the camera and start recording. Returns True once the first frame is in."""
        self._thread.start()
        self._ready.wait(wait)
        if self._error:
            raise RuntimeError(self._error)
        return self._latest is not None

    @property
    def active(self):
        return self._thread.is_alive() and not self._stop.is_set()

    def extend(self, seconds):
        """Record for at least `seconds` more (one continuous video)."""
        self.min_until = max(self.min_until, time.time() + seconds)
        self.last_activity = time.time()

    def stop(self, reason="stopped"):
        if not self._stop.is_set():
            self.reason = reason
            self._stop.set()

    def wait(self, timeout=None):
        self._thread.join(timeout)

    def elapsed(self):
        return time.time() - self.started if self.started else 0.0

    def latest_jpeg(self):
        return self._latest

    def add_audio(self, frame):
        """A chunk of microphone audio (int16, 16 kHz); speech counts as activity."""
        if self.started and not self._stop.is_set():
            self._audio.append(frame.copy())
            if float(np.sqrt(np.mean(frame.astype(np.float32) ** 2))) > self.sound_level:
                self.last_activity = time.time()

    # ---- recording ----

    def _run(self):
        import av
        import cv2
        FOLDER.mkdir(parents=True, exist_ok=True)
        for stale in FOLDER.glob(".jarvis-*-video.mp4"):  # left by a recording cut off by a crash or shutdown
            if time.time() - stale.stat().st_mtime > 3600:
                stale.unlink(missing_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.path = FOLDER / f"jarvis-{stamp}.mp4"
        temp = FOLDER / f".jarvis-{stamp}-video.mp4"
        cam = cv2.VideoCapture(self.index, tools_backend())
        try:
            if not cam.isOpened():
                self._error = "no webcam found, or another app is using it"
                return
            for _ in range(8):  # let auto-exposure settle
                cam.read()
            ok, img = cam.read()
            if not ok:
                self._error = "the webcam isn't sending pictures"
                return
            h, w = img.shape[:2]
            if w > WIDTH:
                h, w = round(h * WIDTH / w) // 2 * 2, WIDTH
            out = av.open(str(temp), "w")
            vs = out.add_stream("libx264", rate=FPS)
            vs.width, vs.height, vs.pix_fmt = w, h, "yuv420p"
            vs.options = {"crf": "23", "preset": "veryfast"}
            self.started = self.last_activity = time.time()
            last_small, next_check = None, 0.0
            n = 0
            while not self._stop.is_set():
                ok, img = cam.read()
                if not ok:
                    self.reason = "the webcam stopped sending pictures"
                    break
                if img.shape[1] != w:
                    img = cv2.resize(img, (w, h))
                self._latest = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 75])[1].tobytes()
                self._ready.set()
                # write frames to keep real time: duplicate if the camera is slow, skip if it's fast
                due = int((time.time() - self.started) * FPS)
                while n <= due:
                    frame = av.VideoFrame.from_ndarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB), format="rgb24")
                    frame.pts = n
                    for packet in vs.encode(frame):
                        out.mux(packet)
                    n += 1
                now = time.time()
                if now >= next_check:  # once a second: did anything move?
                    small = cv2.cvtColor(cv2.resize(img, (64, round(64 * h / w))), cv2.COLOR_BGR2GRAY).astype("float32")
                    if last_small is not None and float(np.abs(small - last_small).mean()) > MOVE:
                        self.last_activity = now
                    last_small, next_check = small, now + 1.0
                    if now - self.started > MAX_MINUTES * 60:
                        self.reason = f"reached the {MAX_MINUTES}-minute limit"
                        break
                    if now >= self.min_until and now - self.last_activity >= IDLE_SECONDS:
                        self.reason = f"nothing moved or was said for {IDLE_SECONDS} seconds"
                        break
            for packet in vs.encode():
                out.mux(packet)
            out.close()
        except Exception as e:
            self._error = self._error or str(e)
            self.reason = f"recording failed: {e}"
        finally:
            cam.release()
            self._stop.set()
            self._ready.set()
        seconds = self.elapsed()
        try:
            self._finish(temp, seconds)
        except Exception as e:
            print(f"[camera] couldn't add the sound ({e}); keeping the video without it")
            if temp.exists():
                temp.replace(self.path)
        self.on_stop(str(self.path) if self.path.exists() else None, seconds, self.reason)

    def _finish(self, temp, seconds):
        """Combine the video with the recorded sound into the final MP4."""
        import av
        if not temp.exists():
            return
        if not self._audio:
            temp.replace(self.path)
            return
        pcm = np.concatenate(self._audio)[: int(seconds * RATE)]
        with av.open(str(temp)) as src, av.open(str(self.path), "w") as out:
            vin = src.streams.video[0]
            vout = out.add_stream_from_template(vin)
            aout = out.add_stream("aac", rate=RATE)
            aout.layout = "mono"
            for packet in src.demux(vin):
                if packet.dts is None:
                    continue
                packet.stream = vout
                out.mux(packet)
            for i in range(0, len(pcm), 1024):
                chunk = pcm[i:i + 1024].reshape(1, -1)
                frame = av.AudioFrame.from_ndarray(chunk, format="s16", layout="mono")
                frame.sample_rate, frame.pts = RATE, i
                for packet in aout.encode(frame):
                    out.mux(packet)
            for packet in aout.encode():
                out.mux(packet)
        temp.unlink()


def tools_backend():
    """The webcam driver: AVFoundation on the Mac, DirectShow on Windows."""
    import sys
    import cv2
    return cv2.CAP_AVFOUNDATION if sys.platform == "darwin" else cv2.CAP_DSHOW
