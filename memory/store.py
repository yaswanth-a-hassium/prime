"""Jarvis's local database (SQLite): conversation history, activity log, reminders, tasks, memories.

Schema changes are numbered migrations; the database records which ones it has
applied, so upgrading Jarvis never loses data. Back up by copying jarvis.db
while Jarvis is closed (or use export()).
"""
import json
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

MIGRATIONS = [
    # 1: initial schema
    """
    CREATE TABLE messages (id INTEGER PRIMARY KEY, ts TEXT NOT NULL, session TEXT, role TEXT NOT NULL, text TEXT NOT NULL);
    CREATE TABLE activity (id INTEGER PRIMARY KEY, ts TEXT NOT NULL, tool TEXT NOT NULL, category TEXT NOT NULL,
                           decision TEXT NOT NULL, summary TEXT NOT NULL);
    CREATE TABLE reminders (id INTEGER PRIMARY KEY, created TEXT NOT NULL, due TEXT NOT NULL, text TEXT NOT NULL,
                            status TEXT NOT NULL DEFAULT 'pending', fired TEXT);
    CREATE TABLE tasks (id INTEGER PRIMARY KEY, created TEXT NOT NULL, updated TEXT NOT NULL, title TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'open', notes TEXT NOT NULL DEFAULT '');
    CREATE TABLE memories (id INTEGER PRIMARY KEY, created TEXT NOT NULL, updated TEXT NOT NULL,
                           kind TEXT NOT NULL, text TEXT NOT NULL);
    CREATE INDEX idx_messages_ts ON messages(ts);
    CREATE INDEX idx_activity_ts ON activity(ts);
    CREATE INDEX idx_reminders_due ON reminders(status, due);
    """,
    # 2: messages can be hidden from the screen without being deleted
    "ALTER TABLE messages ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0;",
]


def now():
    return datetime.now().isoformat(timespec="seconds")


class Store:
    def __init__(self, path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL" if self.path != ":memory:" else "PRAGMA journal_mode=MEMORY")
        self.lock = threading.Lock()
        self.migrate()

    @property
    def version(self):
        return self.db.execute("PRAGMA user_version").fetchone()[0]

    def migrate(self):
        with self.lock:
            for number, sql in enumerate(MIGRATIONS, start=1):
                if self.version < number:
                    self.db.executescript(f"BEGIN; {sql} PRAGMA user_version = {number}; COMMIT;")

    def _exec(self, sql, args=()):
        with self.lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            return cur

    def _rows(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def close(self):
        with self.lock:
            self.db.close()

    # ---- conversation history ----

    def add_message(self, role, text, session=None):
        return self._exec("INSERT INTO messages (ts, session, role, text) VALUES (?, ?, ?, ?)",
                          (now(), session, role, text)).lastrowid

    def recent_messages(self, limit=50):
        """The latest messages that are not hidden from view."""
        return self._rows("SELECT * FROM (SELECT * FROM messages WHERE hidden = 0 ORDER BY id DESC LIMIT ?) ORDER BY id",
                          (limit,))

    def hide_message(self, mid):
        """Remove one message from view. It stays stored (and in exports); only the screen forgets it."""
        return self._exec("UPDATE messages SET hidden = 1 WHERE id = ?", (mid,)).rowcount > 0

    def hide_all(self):
        """Clear the conversation from view. Nothing is deleted. Returns how many messages were hidden."""
        return self._exec("UPDATE messages SET hidden = 1 WHERE hidden = 0").rowcount

    def prune_history(self, days):
        if days and days > 0:
            cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
            self._exec("DELETE FROM messages WHERE ts < ?", (cutoff,))

    def clear_history(self):
        self._exec("DELETE FROM messages")

    # ---- activity log ----

    def log_activity(self, tool, category, decision, summary):
        self._exec("INSERT INTO activity (ts, tool, category, decision, summary) VALUES (?, ?, ?, ?, ?)",
                   (now(), tool, category, decision, summary))

    def recent_activity(self, limit=200):
        return self._rows("SELECT * FROM (SELECT * FROM activity ORDER BY id DESC LIMIT ?) ORDER BY id", (limit,))

    # ---- reminders ----

    def add_reminder(self, due, text):
        """Save a reminder (due: naive local datetime) and return the stored row, read back to confirm."""
        rid = self._exec("INSERT INTO reminders (created, due, text) VALUES (?, ?, ?)",
                         (now(), due.isoformat(timespec="minutes"), text)).lastrowid
        return self.get_reminder(rid)

    def get_reminder(self, rid):
        rows = self._rows("SELECT * FROM reminders WHERE id = ?", (rid,))
        return rows[0] if rows else None

    def reminders(self, status="pending"):
        if status == "all":
            return self._rows("SELECT * FROM reminders ORDER BY due")
        return self._rows("SELECT * FROM reminders WHERE status = ? ORDER BY due", (status,))

    def due_reminders(self, at=None):
        at = (at or datetime.now()).isoformat(timespec="minutes")
        return self._rows("SELECT * FROM reminders WHERE status = 'pending' AND due <= ? ORDER BY due", (at,))

    def update_reminder(self, rid, due=None, text=None):
        if due is not None:
            self._exec("UPDATE reminders SET due = ?, status = 'pending', fired = NULL WHERE id = ?",
                       (due.isoformat(timespec="minutes"), rid))
        if text is not None:
            self._exec("UPDATE reminders SET text = ? WHERE id = ?", (text, rid))
        return self.get_reminder(rid)

    def set_reminder_status(self, rid, status):
        fired = now() if status == "done" else None
        return self._exec("UPDATE reminders SET status = ?, fired = ? WHERE id = ?", (status, fired, rid)).rowcount > 0

    # ---- tasks ----

    def add_task(self, title, notes=""):
        tid = self._exec("INSERT INTO tasks (created, updated, title, notes) VALUES (?, ?, ?, ?)",
                         (now(), now(), title, notes)).lastrowid
        return self._rows("SELECT * FROM tasks WHERE id = ?", (tid,))[0]

    def tasks(self, status="open"):
        if status == "all":
            return self._rows("SELECT * FROM tasks ORDER BY id")
        return self._rows("SELECT * FROM tasks WHERE status = ? ORDER BY id", (status,))

    def update_task(self, tid, status=None, title=None, notes=None):
        for col, val in (("status", status), ("title", title), ("notes", notes)):
            if val is not None:
                self._exec(f"UPDATE tasks SET {col} = ?, updated = ? WHERE id = ?", (val, now(), tid))
        rows = self._rows("SELECT * FROM tasks WHERE id = ?", (tid,))
        return rows[0] if rows else None

    # ---- export ----

    def export(self, path):
        """Write everything (except nothing secret is stored here) to a JSON file."""
        data = {t: self._rows(f"SELECT * FROM {t}") for t in ("messages", "activity", "reminders", "tasks", "memories")}
        data["schema_version"] = self.version
        Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")
        return path
