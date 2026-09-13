from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field
from datetime import datetime
from typing import Any, Optional

from core.severity import extract_severity
from core.timeUtils import extract_timestamp


@dataclass
class FilterRule:
    mode: str  # "include" | "exclude"
    match_type: str  # "substring" | "exact" | "regex" | "severity" | "time_range"
    value: Any  # str, compiled Pattern, or (start, end) tuple for time_range
    field: str = "message"  # "message" | "severity"
    case_sensitive: bool = False
    compiled: Optional[re.Pattern] = dc_field(default=None, repr=False)

    def __post_init__(self):
        if self.match_type == "regex" and not isinstance(self.value, re.Pattern):
            flags = 0 if self.case_sensitive else re.IGNORECASE
            try:
                self.compiled = re.compile(self.value, flags)
            except re.error as e:
                raise ValueError(f"Invalid regex pattern '{self.value}': {e}") from e
        elif isinstance(self.value, re.Pattern):
            self.compiled = self.value


class LogFilter:
    """
    Composable include/exclude/regex/severity/time-range filter.

    Matching semantics:
      - EXCLUDE rules are checked first. If a line matches ANY exclude rule -> dropped.
      - INCLUDE rules are OR'd by default (combine_mode="OR"): line must match at least one.
        Set combine_mode="AND" to require ALL include rules to match.
      - If there are no include rules at all, every non-excluded line is kept.
    """

    def __init__(self, combine_mode: str = "OR"):
        if combine_mode not in ("OR", "AND"):
            raise ValueError("combine_mode must be 'OR' or 'AND'")
        self.combine_mode = combine_mode
        self.rules: list[FilterRule] = []

    # ---- builder methods (chainable) ----

    def add_include(
        self, value, match_type="substring", field="message", case_sensitive=False
    ):
        self.rules.append(
            FilterRule("include", match_type, value, field, case_sensitive)
        )
        return self

    def add_exclude(
        self, value, match_type="substring", field="message", case_sensitive=False
    ):
        self.rules.append(
            FilterRule("exclude", match_type, value, field, case_sensitive)
        )
        return self

    def add_time_range_include(self, start: datetime, end: datetime):
        self.rules.append(FilterRule("include", "time_range", (start, end)))
        return self

    def add_time_range_exclude(self, start: datetime, end: datetime):
        self.rules.append(FilterRule("exclude", "time_range", (start, end)))
        return self

    def clear(self):
        self.rules = []
        return self

    # ---- matching ----

    def _matches(
        self, rule: FilterRule, line: str, severity: str, ts: Optional[datetime]
    ) -> bool:
        if rule.match_type == "time_range":
            if ts is None:
                return False
            start, end = rule.value
            return start <= ts <= end

        if rule.match_type == "severity":
            return severity.upper() == str(rule.value).upper()

        target = severity if rule.field == "severity" else line
        cmp_target = target if rule.case_sensitive else target.lower()
        cmp_value = rule.value if rule.case_sensitive else str(rule.value).lower()

        if rule.match_type == "substring":
            return cmp_value in cmp_target
        if rule.match_type == "exact":
            return cmp_target.strip() == cmp_value.strip()
        if rule.match_type == "regex":
            return bool(rule.compiled.search(target))

        raise ValueError(f"Unknown match_type: {rule.match_type}")

    def apply(
        self, line: str, severity: Optional[str] = None, ts: Optional[datetime] = None
    ) -> bool:
        """Return True if the line should be KEPT."""
        severity = severity if severity is not None else extract_severity(line)

        exclude_rules = [r for r in self.rules if r.mode == "exclude"]
        include_rules = [r for r in self.rules if r.mode == "include"]

        for rule in exclude_rules:
            if self._matches(rule, line, severity, ts):
                return False

        if not include_rules:
            return True

        if self.combine_mode == "OR":
            return any(self._matches(r, line, severity, ts) for r in include_rules)
        else:  # AND
            return all(self._matches(r, line, severity, ts) for r in include_rules)

    def filter_lines(self, lines: list[str]) -> list[str]:
        kept = []
        for line in lines:
            stripped = line.rstrip("\n")
            if not stripped.strip():
                continue
            sev = extract_severity(stripped)
            ts = extract_timestamp(stripped)
            if self.apply(stripped, sev, ts):
                kept.append(stripped)
        return kept

    @classmethod
    def from_dict(cls, spec: dict) -> "LogFilter":
        """
        Build a LogFilter from a plain dict (e.g. coming straight from an MCP tool call):
        {
          "combine_mode": "OR",
          "include": [{"value": "db", "match_type": "substring"}, ...],
          "exclude": [{"value": "heartbeat", "match_type": "substring"}, ...],
          "severities": ["ERROR", "CRITICAL"],
          "time_range": {"start": "2026-06-27T10:00:00", "end": "2026-06-27T10:15:00"}
        }
        """
        f = cls(combine_mode=spec.get("combine_mode", "OR"))
        for rule in spec.get("include", []):
            f.add_include(
                rule["value"],
                rule.get("match_type", "substring"),
                rule.get("field", "message"),
                rule.get("case_sensitive", False),
            )
        for rule in spec.get("exclude", []):
            f.add_exclude(
                rule["value"],
                rule.get("match_type", "substring"),
                rule.get("field", "message"),
                rule.get("case_sensitive", False),
            )
        for sev in spec.get("severities", []):
            f.add_include(sev, match_type="severity")
        tr = spec.get("time_range")
        if tr:
            start = datetime.fromisoformat(tr["start"])
            end = datetime.fromisoformat(tr["end"])
            f.add_time_range_include(start, end)
        return f
