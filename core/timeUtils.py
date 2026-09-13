"""
Timestamp handling for LogLens.

Two distinct jobs live here, and they're kept separate on purpose:

1. extract_timestamp(line) - find/parse a timestamp embedded INSIDE a raw log
   line. This must handle whatever format the log producer chose.
2. parse_user_time(value) - parse a timestamp the *user/LLM typed in* when
   asking for a time range ("2026-06-27", "27/06/2026 10:00", full ISO,
   etc). This is deliberately more permissive.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

# Covers common log formats:
#   2026-06-27T10:22:01.445Z
#   2026-06-27 10:22:01
#   2026-06-27 10:22:01,445
#   27/Jun/2026:10:22:01 +0000   (Apache/nginx style)
_TIMESTAMP_PATTERNS = [
    re.compile(
        r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
    ),
    re.compile(r"\d{2}/[A-Za-z]{3}/\d{4}:\d{2}:\d{2}:\d{2}\s*[+-]\d{4}"),
]

_TIMESTAMP_FORMATS = [
    "%Y-%m-%dT%H:%M:%S.%fZ",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S,%f",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%d/%b/%Y:%H:%M:%S %z",
]

# Extra formats accepted for user/LLM-typed query bounds (dates without time,
# slash-separated dates, etc.) - these are NOT used against raw log lines.
_USER_INPUT_FORMATS = [
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d-%m-%Y",
    "%m/%d/%Y",
    "%Y-%m-%d %H:%M",
    "%d/%m/%Y %H:%M:%S",
]


def _to_naive_utc(dt: datetime) -> datetime:
    """
    Normalize to a naive datetime (UTC if it carried a timezone).

    Log files routinely mix formats where some carry a UTC offset (e.g. the
    Apache/nginx style or a trailing 'Z') and some don't. Comparing a
    timezone-aware datetime to a naive one raises TypeError, which would
    otherwise crash any multi-format file the moment both styles appear.
    Stripping to naive UTC keeps every extracted timestamp comparable.
    """
    if dt.tzinfo is not None:
        from datetime import timezone

        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def extract_timestamp(line: str) -> Optional[datetime]:
    """Find and parse the first timestamp in a log line. Returns None if not found/parseable."""
    for pattern in _TIMESTAMP_PATTERNS:
        match = pattern.search(line)
        if not match:
            continue
        raw = match.group(0)
        # Normalize comma-decimal (e.g. "10:22:01,445") to dot-decimal for strptime
        normalized = raw.replace(",", ".")
        for fmt in _TIMESTAMP_FORMATS:
            try:
                return _to_naive_utc(datetime.strptime(normalized, fmt))
            except ValueError:
                continue
    return None


def parse_user_time(value: Optional[str]) -> Optional[datetime]:
    """
    Parse a timestamp string a user or LLM typed into a tool call.

    Tries, in order:
      1. dateutil (if installed) - handles almost anything sensibly.
      2. The same strict formats used for log-line timestamps.
      3. A handful of common date-only / locale-ish formats.

    Raises ValueError with a clear message if nothing works, rather than
    silently returning None - a filter tool should fail loudly on a bad
    time argument instead of quietly matching everything.
    """
    if not value:
        return None

    try:
        from dateutil import parser as date_parser  # optional dependency

        return _to_naive_utc(date_parser.parse(value))
    except ImportError:
        pass
    except (ValueError, OverflowError):
        pass

    for fmt in (*_TIMESTAMP_FORMATS, *_USER_INPUT_FORMATS):
        try:
            return _to_naive_utc(datetime.strptime(value, fmt))
        except ValueError:
            continue

    raise ValueError(
        f"Could not parse time value {value!r}. Try ISO-8601 "
        f"(e.g. '2026-06-27T10:00:00') or 'YYYY-MM-DD [HH:MM[:SS]]'."
    )
