"""Date helpers. Timestamps are stored in UTC (naive datetimes = UTC)."""
from __future__ import annotations

import re
from datetime import date, datetime, timezone

from stocksense.exceptions import DomainValidationError


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def today() -> date:
    return utcnow().date()


_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def parse_date(value, *, field: str = "date") -> date:
    """Parse unambiguous date formats only.

    Accepted: YYYY-MM-DD, YYYY/MM/DD, and DD-Mon-YYYY (e.g. 05-Oct-2026).
    Ambiguous numeric formats such as 05/10/2026 are rejected on purpose.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None or not str(value).strip():
        raise DomainValidationError(f"{field} is required.")
    text = str(value).strip()
    m = re.fullmatch(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
    try:
        if m:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        m = re.fullmatch(r"(\d{1,2})[- ]([A-Za-z]{3})[- ](\d{4})", text)
        if m and m.group(2).lower() in _MONTHS:
            return date(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1)))
    except ValueError:
        raise DomainValidationError(f"{field} '{text}' is not a real calendar date.") from None
    raise DomainValidationError(
        f"{field} '{text}' is not in a supported format. Use YYYY-MM-DD (e.g. 2026-10-16).")


def parse_datetime(value, *, field: str = "timestamp") -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo is None else \
            value.astimezone(timezone.utc).replace(tzinfo=None)
    text = str(value or "").strip()
    if not text:
        raise DomainValidationError(f"{field} is required.")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return datetime.combine(parse_date(text, field=field), datetime.min.time())
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def fmt_dt(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M UTC") if value else "-"
