"""Logging with secret redaction."""
from __future__ import annotations

import logging
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_PATTERNS = [
    (re.compile(r"AIza[0-9A-Za-z_\-]{20,}"), "[REDACTED_API_KEY]"),
    (re.compile(r"AQ\.[0-9A-Za-z_\-\.]{20,}"), "[REDACTED_API_KEY]"),
    (re.compile(r"(?i)(api[_-]?key|password|passwd|secret|token|authorization)(\s*[=:]\s*)([^\s,;'\"]+)"),
     r"\1\2[REDACTED]"),
    (re.compile(r"scrypt\$[^\s]+"), "[REDACTED_HASH]"),
]


def redact(text: str) -> str:
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # malformed format args: keep the record but make it safe
            message = str(record.msg)
        record.msg = redact(message)
        record.args = ()
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True


_configured = False


def configure_logging(log_path: Path, level: str = "INFO") -> None:
    global _configured
    if _configured:
        return
    # Windows consoles often use cp1252; never let a Unicode product name crash logging.
    for stream in (sys.stderr, sys.stdout):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(errors="backslashreplace")
            except (ValueError, OSError):
                pass
    root = logging.getLogger("stocksense")
    root.setLevel(getattr(logging, level, logging.INFO))
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=3,
                                            encoding="utf-8"))
    except OSError:
        pass  # file logging is optional; console logging still works
    for handler in handlers:
        handler.setFormatter(fmt)
        handler.addFilter(RedactingFilter())
        root.addHandler(handler)
    _configured = True
