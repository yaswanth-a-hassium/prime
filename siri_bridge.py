"""The Siri Shortcut bridge: lets Siri, the Shortcuts app, Spotlight or a terminal hand Prime a request.

The running app listens on a Unix socket in Prime's data folder (~/PrimeMemory/prime.sock). The socket file is
readable and writable by your macOS account only (mode 600), so no password or token is needed or stored:
only programs running as you can talk to it.

Protocol: one JSON object per line in, one JSON object per line out.
    {"cmd": "ask", "text": "what's on my calendar", "speak": true}  -> {"ok": true, "reply": "..."}
    {"cmd": "show"}                                                 -> {"ok": true}   (bring the window up)
    {"cmd": "ping"}                                                 -> {"ok": true, "name": "Prime"}

prime_ask.py is the client the Shortcut runs ("Run Shell Script"); README_MAC.md shows how to build the Shortcut.
"""
import json
import os
import socket
import stat
import threading

import paths

SOCKET = paths.DATA / "prime.sock"
MAX_REQUEST = 64 * 1024


class Server:
    """Accepts bridge requests and passes each to handler(request_dict) -> reply_dict, one thread per request."""

    def __init__(self, handler, path=None):
        self.handler = handler
        self.path = str(path or SOCKET)
        self._sock = None

    def start(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        try:
            if stat.S_ISSOCK(os.stat(self.path).st_mode):
                os.unlink(self.path)   # left by a previous run (single_instance() already made sure it isn't live)
        except FileNotFoundError:
            pass
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old = os.umask(0o177)          # created as 600: only this user
        try:
            sock.bind(self.path)
        finally:
            os.umask(old)
        os.chmod(self.path, 0o600)
        sock.listen(8)
        self._sock = sock
        threading.Thread(target=self._accept, daemon=True).start()
        return self

    def _accept(self):
        while self._sock:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        with conn:
            try:
                raw = b""
                while b"\n" not in raw and len(raw) < MAX_REQUEST:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    raw += chunk
                request = json.loads(raw.split(b"\n", 1)[0].decode("utf-8") or "{}")
                if not isinstance(request, dict):
                    raise ValueError("a request is a JSON object")
                reply = self.handler(request)
            except Exception as e:
                reply = {"ok": False, "error": str(e)}
            try:
                conn.sendall((json.dumps(reply, ensure_ascii=False) + "\n").encode("utf-8"))
            except OSError:
                pass

    def stop(self):
        sock, self._sock = self._sock, None
        if sock:
            sock.close()
            try:
                os.unlink(self.path)
            except OSError:
                pass


def send(request, timeout=300, path=None):
    """Send one request to the running Prime and return its reply (raises OSError if Prime isn't running)."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(str(path or SOCKET))
        s.sendall((json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8"))
        raw = b""
        while not raw.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            raw += chunk
    return json.loads(raw.decode("utf-8") or "{}")
