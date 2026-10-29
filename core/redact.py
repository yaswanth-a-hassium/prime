"""Strip likely secrets from text before it is logged or stored."""
import re

PATTERNS = [
    (re.compile(r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|apikey|auth)\b(\s*[:=]\s*|\s+is\s+)(\S+)"),
     r"\1\2[redacted]"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b"), "[redacted-key]"),
    (re.compile(r"\b[A-Fa-f0-9]{32,}\b"), "[redacted-hex]"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b"), "[redacted-jwt]"),
    (re.compile(r"\b(?:\d[ -]?){13,19}\b"), "[redacted-card]"),
]


def redact(text):
    text = str(text)
    for pattern, replacement in PATTERNS:
        text = pattern.sub(replacement, text)
    return text
