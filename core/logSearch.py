from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional

from core.severity import extract_severity, severity_rank
from core.timeUtils import extract_timestamp


@dataclass
class MatchedLine:
    line_number: int
    timestamp: Optional[str]
    severity: str
    text: str


class LogSearch:
    """
    Direct, predictable AND-of-all-conditions search - the answer must match
    the keyword/regex AND be at/above min_severity AND fall inside the time
    range, all at once. This is deliberately simpler than LogFilter's
    OR/AND-combinable rule engine, because "find me ERROR+ lines mentioning
    'timeout' between 10:00 and 10:15" is the common case and should be one
    obvious function call, not a rule-building exercise.
    """

    @staticmethod
    def search(
        lines: list[str],
        keyword: Optional[str] = None,
        regex: Optional[str] = None,
        min_severity: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        case_sensitive: bool = False,
        limit: Optional[int] = 500,
    ) -> list[MatchedLine]:
        pattern = None
        if regex:
            flags = 0 if case_sensitive else re.IGNORECASE
            pattern = re.compile(regex, flags)

        needle = keyword if (keyword is None or case_sensitive) else keyword.lower()

        results: list[MatchedLine] = []
        for idx, raw in enumerate(lines):
            line = raw.rstrip("\n")
            if not line.strip():
                continue

            if needle:
                haystack = line if case_sensitive else line.lower()
                if needle not in haystack:
                    continue

            if pattern and not pattern.search(line):
                continue

            sev = extract_severity(line)
            if min_severity and severity_rank(sev) < severity_rank(min_severity):
                continue

            ts = extract_timestamp(line)
            if (start_time or end_time) and ts is None:
                continue
            if start_time and ts < start_time:
                continue
            if end_time and ts > end_time:
                continue

            results.append(
                MatchedLine(
                    line_number=idx + 1,
                    timestamp=ts.isoformat() if ts else None,
                    severity=sev,
                    text=line,
                )
            )
            if limit and len(results) >= limit:
                break

        return results

    @staticmethod
    def to_dicts(matches: list[MatchedLine]) -> list[dict]:
        return [asdict(m) for m in matches]

    @staticmethod
    def stats(lines: list[str]) -> dict:
        """Quick overview: line count, severity breakdown, time span covered."""
        total = 0
        severity_counts: dict[str, int] = {}
        timestamps: list[datetime] = []

        for raw in lines:
            line = raw.rstrip("\n")
            if not line.strip():
                continue
            total += 1

            sev = extract_severity(line)
            severity_counts[sev] = severity_counts.get(sev, 0) + 1

            ts = extract_timestamp(line)
            if ts:
                timestamps.append(ts)

        return {
            "total_lines": total,
            "lines_with_parsed_timestamp": len(timestamps),
            "severity_counts": severity_counts,
            "first_timestamp": min(timestamps).isoformat() if timestamps else None,
            "last_timestamp": max(timestamps).isoformat() if timestamps else None,
        }
