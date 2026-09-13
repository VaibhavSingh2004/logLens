"""
Severity-level detection for log lines, independent of log format.
"""

from __future__ import annotations

import re

SEVERITY_ORDER = {
    "TRACE": 0,
    "DEBUG": 1,
    "INFO": 2,
    "NOTICE": 2,
    "WARNING": 3,
    "WARN": 3,
    "ERROR": 4,
    "SEVERE": 4,
    "CRITICAL": 5,
    "FATAL": 5,
    "ALERT": 5,
    "EMERGENCY": 5,
}

_SEVERITY_CANON = {
    "WARN": "WARNING",
    "SEVERE": "ERROR",
    "FATAL": "CRITICAL",
    "ALERT": "CRITICAL",
    "EMERGENCY": "CRITICAL",
    "NOTICE": "INFO",
}

# Ordered so longer / more specific tokens are checked before shorter ones
_SEVERITY_SCAN_ORDER = [
    "CRITICAL",
    "EMERGENCY",
    "ALERT",
    "FATAL",
    "SEVERE",
    "ERROR",
    "WARNING",
    "WARN",
    "NOTICE",
    "INFO",
    "DEBUG",
    "TRACE",
]

_SEVERITY_TOKEN_RE = {
    sev: re.compile(rf"(?<![A-Za-z]){re.escape(sev)}(?![A-Za-z])")
    for sev in _SEVERITY_SCAN_ORDER
}


def extract_severity(line: str) -> str:
    """Return the canonical severity level found in the line, defaulting to INFO."""
    upper = line.upper()
    for sev in _SEVERITY_SCAN_ORDER:
        if _SEVERITY_TOKEN_RE[sev].search(upper):
            return _SEVERITY_CANON.get(sev, sev)
    return "INFO"


def severity_rank(sev: str) -> int:
    return SEVERITY_ORDER.get(sev.upper(), 2)
