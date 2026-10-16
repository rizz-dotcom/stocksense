"""CSV reading, number parsing and formula-injection protection."""
from __future__ import annotations

import io
import re
from decimal import Decimal, InvalidOperation

import pandas as pd

from stocksense.exceptions import DomainValidationError, ImportValidationError

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_THOUSANDS_RE = re.compile(r"^\d{1,3}(,\d{3})+(\.\d+)?$")
_PLAIN_NUMBER_RE = re.compile(r"^-?\d+(\.\d+)?$")


def read_csv_bytes(data: bytes, *, max_bytes: int, required: list[str]) -> pd.DataFrame:
    """Read an uploaded CSV as strings and check its header exactly."""
    if data is None or len(data) == 0:
        raise ImportValidationError("The file is empty.")
    if len(data) > max_bytes:
        raise ImportValidationError(
            f"The file is too large ({len(data):,} bytes). The limit is {max_bytes:,} bytes.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ImportValidationError("The file is not valid UTF-8 text. Save it as 'CSV UTF-8'.") from None
    if "\x00" in text:
        raise ImportValidationError("The file contains binary data and is not a CSV file.")
    if not text.strip():
        raise ImportValidationError("The file is empty.")
    try:
        frame = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False,
                            skip_blank_lines=True)
    except (pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        raise ImportValidationError("The file is not a well-formed CSV file.",
                                    detail=str(exc)) from None
    headers = [str(c).strip() for c in frame.columns]
    frame.columns = headers
    missing = [c for c in required if c not in headers]
    extra = [c for c in headers if c not in required]
    problems = []
    if missing:
        problems.append("missing column(s): " + ", ".join(missing))
    if extra:
        problems.append("unexpected column(s): " + ", ".join(extra))
    if len(set(headers)) != len(headers):
        problems.append("duplicate column names")
    if problems:
        raise ImportValidationError(
            "Incorrect header - " + "; ".join(problems)
            + ". Expected exactly: " + ", ".join(required))
    # Drop rows where every value is blank.
    frame = frame[~(frame.apply(lambda r: all(not str(v).strip() for v in r), axis=1))]
    if frame.empty:
        raise ImportValidationError("The file has a header but no data rows.")
    return frame.reset_index(drop=True)


def check_text(value: str, *, field: str, required: bool = True, max_len: int = 200) -> str | None:
    text = (value or "").strip()
    if not text:
        if required:
            raise DomainValidationError(f"{field} is required.")
        return None
    if text.startswith(FORMULA_PREFIXES):
        raise DomainValidationError(
            f"{field} starts with '{text[0]}', which is not allowed (spreadsheet formula risk).")
    if len(text) > max_len:
        raise DomainValidationError(f"{field} is longer than {max_len} characters.")
    if any(ord(ch) < 32 for ch in text):
        raise DomainValidationError(f"{field} contains control characters.")
    return text


def parse_number_text(value: str, *, field: str) -> Decimal:
    """Parse a number using '.' as the decimal separator.

    '1,250.50' (thousands separators) is accepted. '12,5' is rejected because
    a decimal comma is ambiguous.
    """
    text = (value or "").strip().replace(" ", "")
    if not text:
        raise DomainValidationError(f"{field} is required.")
    if _THOUSANDS_RE.match(text):
        text = text.replace(",", "")
    elif "," in text:
        raise DomainValidationError(
            f"{field} '{value}' uses a comma. Use '.' for decimals (e.g. 12.50).")
    if not _PLAIN_NUMBER_RE.match(text):
        raise DomainValidationError(f"{field} '{value}' is not a valid number.")
    try:
        number = Decimal(text)
    except InvalidOperation:
        raise DomainValidationError(f"{field} '{value}' is not a valid number.") from None
    if not number.is_finite():
        raise DomainValidationError(f"{field} must be finite.")
    return number


def parse_whole_quantity(value, *, field: str, allow_zero: bool = True,
                         maximum: int = 10_000_000) -> int:
    number = value if isinstance(value, Decimal) else parse_number_text(str(value), field=field)
    if number < 0:
        raise DomainValidationError(f"{field} cannot be negative.")
    if number != number.to_integral_value():
        raise DomainValidationError(f"{field} must be a whole number of units.")
    if number == 0 and not allow_zero:
        raise DomainValidationError(f"{field} must be greater than zero.")
    if number > maximum:
        raise DomainValidationError(f"{field} is unrealistically large (max {maximum:,}).")
    return int(number)


def neutralize_cell(value):
    """Prefix risky strings so spreadsheet apps treat them as text."""
    if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
        return "'" + value
    return value


def dataframe_to_safe_csv(frame: pd.DataFrame, *, banner: str | None = None) -> bytes:
    safe = frame.copy()
    safe.columns = [neutralize_cell(str(c)) for c in safe.columns]
    for column in safe.columns:
        safe[column] = safe[column].map(neutralize_cell)
    body = safe.to_csv(index=False)
    if banner:
        body = f"# {banner}\n" + body
    return body.encode("utf-8-sig")
