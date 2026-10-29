"""Find a sign-in verification code in the 2FA inbox (IMAP, read-only).

Runs entirely on this laptop: emails are never shown to Claude, only the
extracted code is used, and it is typed straight into the app. That also means
text inside an email can't steer Jarvis.
"""
import email
import html
import imaplib
import re
import time
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime

import vault

CODE_NEAR_WORD = re.compile(r"(?i)(?:code|verification|verify|passcode|one-time|otp|pin)\D{0,60}?\b(\d{4,8})\b")
SIX_DIGITS = re.compile(r"\b(\d{6})\b")


def _header(msg, name):
    try:
        return str(make_header(decode_header(msg.get(name, ""))))
    except Exception:
        return msg.get(name, "")


def _body(msg):
    parts = []
    for part in msg.walk():
        if part.get_content_type() in ("text/plain", "text/html"):
            payload = part.get_payload(decode=True)
            if payload:
                parts.append(payload.decode(part.get_content_charset() or "utf-8", "replace"))
    text = " ".join(parts)
    text = re.sub(r"(?is)<(style|script)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"#[0-9a-fA-F]{3,8}\b", " ", text)  # css colours look like codes
    return html.unescape(text)


def extract_code(subject, body):
    for text in (subject, body):
        m = CODE_NEAR_WORD.search(text) or SIX_DIGITS.search(text)
        if m:
            return m.group(1)
    return None


def _connect():
    cfg = vault.get_email()
    if not cfg:
        raise LookupError("No email account is set up for verification codes (Vault panel).")
    imap = imaplib.IMAP4_SSL(cfg["host"])
    imap.login(cfg["address"], cfg["password"])
    imap.select("INBOX", readonly=True)
    return imap


def test_connection():
    imap = _connect()
    imap.logout()


def _check(sender, since):
    imap = _connect()
    try:
        _, data = imap.search(None, "SINCE", (since - timedelta(days=1)).strftime("%d-%b-%Y"))
        for msg_id in reversed(data[0].split()[-30:]):
            _, head = imap.fetch(msg_id, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            msg = email.message_from_bytes(head[0][1])
            try:
                when = parsedate_to_datetime(msg["Date"])
            except (TypeError, ValueError):
                continue
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            if when < since:
                continue
            frm, subject = _header(msg, "From"), _header(msg, "Subject")
            if sender.lower() not in f"{frm} {subject}".lower():
                continue
            _, full = imap.fetch(msg_id, "(BODY.PEEK[])")
            code = extract_code(subject, _body(email.message_from_bytes(full[0][1])))
            if code:
                return code
    finally:
        imap.logout()
    return None


def wait_for_code(sender, since, timeout=90, poll=5):
    """Newest code from an email whose sender/subject mentions `sender`, received after `since`."""
    deadline = time.time() + timeout
    while True:
        code = _check(sender, since)
        if code or time.time() > deadline:
            return code
        time.sleep(poll)


def now():
    return datetime.now(timezone.utc)
