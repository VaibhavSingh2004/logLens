"""
log_analyzer.py
================
Complete log analysis toolkit for an MCP log-analyzer server.

Components:
  1. Timestamp / severity extraction helpers
  2. LogFilter        - include/exclude/regex/severity/time-range filtering
  3. LogAnalyzer       - drain3 wrapper with per-cluster timestamp + severity tracking
  4. MCP tool functions - thin wrappers exposing LogAnalyzer/LogFilter as callable tools,
                          each with an explicit JSON schema for tool registration.

Design principles followed (per prior discussion):
  - Drain3 only does structural template clustering. It has NO concept of time or severity.
  - All temporal/severity bookkeeping is done in Python, keyed by cluster_id and by line index.
  - Filtering happens BEFORE drain3 ingestion, never after (don't waste drain3 cycles on noise).
  - Counting / aggregation is deterministic Python, never delegated to the LLM.
  - Each capability is its own MCP tool so the LLM can compose them agentically.
"""

from __future__ import annotations

import re
import json
import threading
from dataclasses import dataclass, field as dc_field
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Optional, Any

from drain3 import TemplateMiner
from drain3.template_miner_config import TemplateMinerConfig
from drain3.file_persistence import FilePersistence

# ============================================================================
# 1. TIMESTAMP / SEVERITY EXTRACTION
# ============================================================================

# Covers common formats:
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
                return datetime.strptime(normalized, fmt)
            except ValueError:
                continue
    return None


def extract_severity(line: str) -> str:
    """Return canonical severity level found in the line, defaulting to INFO."""
    upper = line.upper()
    for sev in _SEVERITY_SCAN_ORDER:
        if _SEVERITY_TOKEN_RE[sev].search(upper):
            return _SEVERITY_CANON.get(sev, sev)
    return "INFO"


def severity_rank(sev: str) -> int:
    return SEVERITY_ORDER.get(sev.upper(), 2)


# ============================================================================
# 2. LOG FILTER
# ============================================================================


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
        self,
        value,
        match_type="substring",
        field="message",
        case_sensitive=False,
    ):
        self.rules.append(
            FilterRule("include", match_type, value, field, case_sensitive)
        )
        return self

    def add_exclude(
        self,
        value,
        match_type="substring",
        field="message",
        case_sensitive=False,
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
        self,
        line: str,
        severity: Optional[str] = None,
        ts: Optional[datetime] = None,
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
            if not line.strip():
                continue
            sev = extract_severity(line)
            ts = extract_timestamp(line)
            if self.apply(line, sev, ts):
                kept.append(line)
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


# ============================================================================
# 3. LOG ANALYZER (drain3 wrapper)
# ============================================================================

DEFAULT_DRAIN_CONFIG = """
[DRAIN]
sim_th = 0.4
depth = 4
max_children = 100
max_clusters = 1024

[MASKING]
masking = [{"regex_pattern":"((?<=[^A-Za-z0-9])|^)(\\\\d{4}-\\\\d{2}-\\\\d{2}[T ]\\\\d{2}:\\\\d{2}:\\\\d{2}(\\\\.\\\\d+)?(Z|[+-]\\\\d{2}:?\\\\d{2})?)((?=[^A-Za-z0-9])|$)", "mask_with": "TIMESTAMP"}, {"regex_pattern":"((?<=[^A-Za-z0-9])|^)([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})((?=[^A-Za-z0-9])|$)", "mask_with": "UUID"}, {"regex_pattern":"((?<=[^A-Za-z0-9])|^)(\\\\d{1,3}\\\\.\\\\d{1,3}\\\\.\\\\d{1,3}\\\\.\\\\d{1,3}(:\\\\d+)?)((?=[^A-Za-z0-9])|$)", "mask_with": "IP"}, {"regex_pattern":"((?<=[^A-Za-z0-9])|^)(0x[0-9a-fA-F]+)((?=[^A-Za-z0-9])|$)", "mask_with": "HEX"}, {"regex_pattern":"((?<=[^A-Za-z0-9])|^)(\\\\d+)((?=[^A-Za-z0-9])|$)", "mask_with": "NUM"}]
mask_prefix = <:
mask_suffix = :>
"""


@dataclass
class LineRecord:
    index: int
    timestamp: Optional[datetime]
    severity: str
    raw_line: str
    cluster_id: int


class LogAnalyzer:
    """
    Stateful wrapper around drain3's TemplateMiner.

    Owns:
      - the drain3 miner (structural template clustering)
      - per-cluster occurrence list: (timestamp, severity, raw_line)
      - a flat ordered list of every kept line, for range/context queries

    Thread-safety: a lock guards ingestion since MCP tool calls may arrive
    concurrently in some server implementations.
    """

    def __init__(
        self,
        config_path: Optional[str] = None,
        persistence_path: Optional[str] = None,
    ):
        cfg = TemplateMinerConfig()
        if config_path:
            cfg.load(config_path)
        else:
            # write the default inline config to a temp file drain3 can load
            import tempfile

            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".ini", delete=False
            ) as f:
                f.write(DEFAULT_DRAIN_CONFIG)
                tmp_path = f.name
            cfg.load(tmp_path)

        persistence = FilePersistence(persistence_path) if persistence_path else None
        self.miner = TemplateMiner(persistence_handler=persistence, config=cfg)

        self.occurrences: dict[int, list[tuple]] = defaultdict(list)
        self.lines: list[LineRecord] = []
        self._lock = threading.Lock()
        self._next_index = 0

    # ---- ingestion ----

    def ingest(self, raw_log_text: str, log_filter: Optional[LogFilter] = None) -> dict:
        """
        Filter (optional) -> parse timestamp/severity -> drain3 cluster -> index.
        Returns ingestion stats.
        """
        raw_lines = raw_log_text.splitlines()
        kept_count = 0
        dropped_count = 0

        with self._lock:
            for raw_line in raw_lines:
                line = raw_line.rstrip("\n")
                if not line.strip():
                    continue

                ts = extract_timestamp(line)
                sev = extract_severity(line)

                if log_filter is not None and not log_filter.apply(line, sev, ts):
                    dropped_count += 1
                    continue

                result = self.miner.add_log_message(line)
                cid = result["cluster_id"]

                self.occurrences[cid].append((ts, sev, line))
                self.lines.append(
                    LineRecord(
                        index=self._next_index,
                        timestamp=ts,
                        severity=sev,
                        raw_line=line,
                        cluster_id=cid,
                    )
                )
                self._next_index += 1
                kept_count += 1

        return {
            "lines_seen": len(raw_lines),
            "lines_kept": kept_count,
            "lines_dropped_by_filter": dropped_count,
            "total_clusters": len(self.miner.drain.clusters),
        }

    def reset(self):
        with self._lock:
            self.occurrences.clear()
            self.lines.clear()
            self._next_index = 0
            # Note: this does NOT reset the drain3 tree itself; create a new
            # LogAnalyzer instance if you need a fully clean template tree.

    # ---- queries ----

    def get_summary(
        self, top_n: Optional[int] = None, min_severity: Optional[str] = None
    ) -> list[dict]:
        clusters = sorted(self.miner.drain.clusters, key=lambda c: -c.size)
        out = []
        for c in clusters:
            occ = self.occurrences.get(c.cluster_id, [])
            severities_seen = {s for _, s, _ in occ}
            max_sev = (
                max(severities_seen, key=severity_rank) if severities_seen else "INFO"
            )
            if min_severity and severity_rank(max_sev) < severity_rank(min_severity):
                continue
            examples = [line for _, _, line in occ[:2]]
            timestamps = [ts for ts, _, _ in occ if ts is not None]
            out.append(
                {
                    "cluster_id": c.cluster_id,
                    "template": c.get_template(),
                    "count": c.size,
                    "max_severity": max_sev,
                    "first_seen": (min(timestamps).isoformat() if timestamps else None),
                    "last_seen": (max(timestamps).isoformat() if timestamps else None),
                    "examples": examples,
                }
            )
        return out[:top_n] if top_n else out

    def find_clusters_by_keyword(self, keyword: str) -> list[int]:
        kw = keyword.lower()
        return [
            c.cluster_id
            for c in self.miner.drain.clusters
            if kw in c.get_template().lower()
        ]

    def count_pattern_in_range(
        self,
        keyword: str,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> dict:
        cluster_ids = self.find_clusters_by_keyword(keyword)
        total = 0
        per_cluster = {}
        for cid in cluster_ids:
            occ = self.occurrences.get(cid, [])
            if start is None and end is None:
                n = len(occ)
            else:
                n = sum(
                    1
                    for ts, _, _ in occ
                    if ts
                    and (start is None or ts >= start)
                    and (end is None or ts <= end)
                )
            per_cluster[cid] = n
            total += n
        return {
            "keyword": keyword,
            "matched_cluster_ids": cluster_ids,
            "total_count": total,
            "per_cluster_count": per_cluster,
            "range": {
                "start": start.isoformat() if start else None,
                "end": end.isoformat() if end else None,
            },
        }

    def get_lines_in_range(
        self,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        keyword: Optional[str] = None,
        min_severity: Optional[str] = None,
        limit: int = 500,
    ) -> list[str]:
        out = []
        for rec in self.lines:
            if start and (rec.timestamp is None or rec.timestamp < start):
                continue
            if end and (rec.timestamp is None or rec.timestamp > end):
                continue
            if keyword and keyword.lower() not in rec.raw_line.lower():
                continue
            if min_severity and severity_rank(rec.severity) < severity_rank(
                min_severity
            ):
                continue
            out.append(rec.raw_line)
            if len(out) >= limit:
                break
        return out

    def get_context_around(
        self,
        target_ts: Optional[datetime] = None,
        target_index: Optional[int] = None,
        window_seconds: int = 60,
        window_lines: int = 5,
    ) -> list[str]:
        """
        Get verbatim context around a point in time OR around a specific line index.
        Provide exactly one of target_ts / target_index.
        """
        if target_index is not None:
            lo = max(0, target_index - window_lines)
            hi = min(len(self.lines), target_index + window_lines + 1)
            return [rec.raw_line for rec in self.lines[lo:hi]]

        if target_ts is not None:
            lo_ts = target_ts - timedelta(seconds=window_seconds)
            hi_ts = target_ts + timedelta(seconds=window_seconds)
            return [
                rec.raw_line
                for rec in self.lines
                if rec.timestamp and lo_ts <= rec.timestamp <= hi_ts
            ]

        raise ValueError("Must provide target_ts or target_index")

    def get_time_buckets(
        self,
        keyword: Optional[str] = None,
        bucket_seconds: int = 300,
        min_severity: Optional[str] = None,
    ) -> list[dict]:
        """Bucket matching lines into fixed-size time windows with counts (for spike detection)."""
        records = [
            rec
            for rec in self.lines
            if rec.timestamp is not None
            and (keyword is None or keyword.lower() in rec.raw_line.lower())
            and (
                min_severity is None
                or severity_rank(rec.severity) >= severity_rank(min_severity)
            )
        ]
        if not records:
            return []

        records.sort(key=lambda r: r.timestamp)
        start = records[0].timestamp
        buckets: dict[int, int] = defaultdict(int)
        for rec in records:
            bucket_idx = int((rec.timestamp - start).total_seconds() // bucket_seconds)
            buckets[bucket_idx] += 1

        return [
            {
                "bucket_start": (
                    start + timedelta(seconds=idx * bucket_seconds)
                ).isoformat(),
                "bucket_end": (
                    start + timedelta(seconds=(idx + 1) * bucket_seconds)
                ).isoformat(),
                "count": count,
            }
            for idx, count in sorted(buckets.items())
        ]

    def get_error_context_blocks(
        self, min_severity: str = "WARNING", window_lines: int = 2
    ) -> list[dict]:
        """Verbatim blocks around every line at/above min_severity, merged if overlapping."""
        error_indices = [
            rec.index
            for rec in self.lines
            if severity_rank(rec.severity) >= severity_rank(min_severity)
        ]
        if not error_indices:
            return []

        spans = []
        for idx in error_indices:
            lo = max(0, idx - window_lines)
            hi = min(len(self.lines) - 1, idx + window_lines)
            spans.append([lo, hi])
        spans.sort()

        merged = [spans[0]]
        for lo, hi in spans[1:]:
            if lo <= merged[-1][1] + 1:
                merged[-1][1] = max(merged[-1][1], hi)
            else:
                merged.append([lo, hi])

        return [
            {
                "start_index": lo,
                "end_index": hi,
                "lines": [r.raw_line for r in self.lines[lo : hi + 1]],
            }
            for lo, hi in merged
        ]


# ============================================================================
# 4. MCP TOOL FUNCTIONS + SCHEMAS
# ============================================================================
#
# Each tool function below is a thin, stateless-signature wrapper around a
# single shared LogAnalyzer instance. In a real MCP server you would register
# these with your server's tool dispatch (name -> (schema, handler)).
#
# A single shared instance is used here because log ingestion is meant to
# accumulate across multiple tool calls within one analysis session.

_ANALYZER = LogAnalyzer()


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(ts) if ts else None


# ---- Tool: ingest_logs ----

TOOL_INGEST_LOGS_SCHEMA = {
    "name": "ingest_logs",
    "description": (
        "Load raw log text into the analyzer. Optionally filter lines (include/exclude/"
        "regex/severity/time-range) BEFORE structural clustering. Call this once per log "
        "batch before using any other log tool."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "raw_log_text": {
                "type": "string",
                "description": "Raw multi-line log text.",
            },
            "filter": {
                "type": "object",
                "description": "Optional filter spec applied before ingestion.",
                "properties": {
                    "combine_mode": {"type": "string", "enum": ["OR", "AND"]},
                    "include": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "value": {"type": "string"},
                                "match_type": {
                                    "type": "string",
                                    "enum": ["substring", "exact", "regex"],
                                },
                                "case_sensitive": {"type": "boolean"},
                            },
                            "required": ["value"],
                        },
                    },
                    "exclude": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "value": {"type": "string"},
                                "match_type": {
                                    "type": "string",
                                    "enum": ["substring", "exact", "regex"],
                                },
                                "case_sensitive": {"type": "boolean"},
                            },
                            "required": ["value"],
                        },
                    },
                    "severities": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "time_range": {
                        "type": "object",
                        "properties": {
                            "start": {"type": "string", "format": "date-time"},
                            "end": {"type": "string", "format": "date-time"},
                        },
                    },
                },
            },
        },
        "required": ["raw_log_text"],
    },
}


def tool_ingest_logs(raw_log_text: str, filter: Optional[dict] = None) -> dict:
    log_filter = LogFilter.from_dict(filter) if filter else None
    return _ANALYZER.ingest(raw_log_text, log_filter)


# ---- Tool: get_log_summary ----

TOOL_GET_LOG_SUMMARY_SCHEMA = {
    "name": "get_log_summary",
    "description": (
        "Return the compressed template summary: one entry per unique log pattern "
        "(drain3-mined), with occurrence count, max severity seen, first/last timestamp, "
        "and example lines. Use this first to get an overview before drilling down."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "top_n": {
                "type": "integer",
                "description": "Limit to top N most frequent templates.",
            },
            "min_severity": {
                "type": "string",
                "enum": ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
                "description": "Only include templates whose max severity is at/above this level.",
            },
        },
    },
}


def tool_get_log_summary(
    top_n: Optional[int] = None, min_severity: Optional[str] = None
) -> dict:
    return {"templates": _ANALYZER.get_summary(top_n=top_n, min_severity=min_severity)}


# ---- Tool: count_pattern_in_range ----

TOOL_COUNT_PATTERN_SCHEMA = {
    "name": "count_pattern_in_range",
    "description": (
        "Deterministically count how many times a keyword/pattern (matched against mined "
        "templates) occurred, optionally restricted to a timestamp range. Use this instead "
        "of asking the LLM to count lines manually."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "keyword": {
                "type": "string",
                "description": "Keyword to match against template text, e.g. 'connect to db'.",
            },
            "start": {
                "type": "string",
                "format": "date-time",
                "description": "ISO-8601 start timestamp (optional).",
            },
            "end": {
                "type": "string",
                "format": "date-time",
                "description": "ISO-8601 end timestamp (optional).",
            },
        },
        "required": ["keyword"],
    },
}


def tool_count_pattern_in_range(
    keyword: str, start: Optional[str] = None, end: Optional[str] = None
) -> dict:
    return _ANALYZER.count_pattern_in_range(keyword, _parse_iso(start), _parse_iso(end))


# ---- Tool: get_lines_in_range ----

TOOL_GET_LINES_IN_RANGE_SCHEMA = {
    "name": "get_lines_in_range",
    "description": (
        "Return verbatim log lines within a timestamp range, optionally filtered by keyword "
        "and/or minimum severity. Capped by `limit` to control token usage."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "start": {"type": "string", "format": "date-time"},
            "end": {"type": "string", "format": "date-time"},
            "keyword": {"type": "string"},
            "min_severity": {
                "type": "string",
                "enum": ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
            },
            "limit": {"type": "integer", "default": 500},
        },
    },
}


def tool_get_lines_in_range(
    start: Optional[str] = None,
    end: Optional[str] = None,
    keyword: Optional[str] = None,
    min_severity: Optional[str] = None,
    limit: int = 500,
) -> dict:
    lines = _ANALYZER.get_lines_in_range(
        _parse_iso(start), _parse_iso(end), keyword, min_severity, limit
    )
    return {"count": len(lines), "lines": lines}


# ---- Tool: get_context_around ----

TOOL_GET_CONTEXT_AROUND_SCHEMA = {
    "name": "get_context_around",
    "description": (
        "Return verbatim lines surrounding a specific moment in time or a specific line "
        "index, for root-cause investigation. Provide either `timestamp` or `line_index`."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "timestamp": {"type": "string", "format": "date-time"},
            "line_index": {"type": "integer"},
            "window_seconds": {"type": "integer", "default": 60},
            "window_lines": {"type": "integer", "default": 5},
        },
    },
}


def tool_get_context_around(
    timestamp: Optional[str] = None,
    line_index: Optional[int] = None,
    window_seconds: int = 60,
    window_lines: int = 5,
) -> dict:
    lines = _ANALYZER.get_context_around(
        _parse_iso(timestamp), line_index, window_seconds, window_lines
    )
    return {"count": len(lines), "lines": lines}


# ---- Tool: get_time_buckets ----

TOOL_GET_TIME_BUCKETS_SCHEMA = {
    "name": "get_time_buckets",
    "description": (
        "Bucket matching log lines into fixed-size time windows with counts per bucket. "
        "Use this to detect spikes/anomalies over time without reading every line."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "keyword": {"type": "string"},
            "bucket_seconds": {"type": "integer", "default": 300},
            "min_severity": {
                "type": "string",
                "enum": ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
            },
        },
    },
}


def tool_get_time_buckets(
    keyword: Optional[str] = None,
    bucket_seconds: int = 300,
    min_severity: Optional[str] = None,
) -> dict:
    return {
        "buckets": _ANALYZER.get_time_buckets(keyword, bucket_seconds, min_severity)
    }


# ---- Tool: get_error_context_blocks ----

TOOL_GET_ERROR_CONTEXT_SCHEMA = {
    "name": "get_error_context_blocks",
    "description": (
        "Return verbatim, merged context blocks around every line at or above a severity "
        "threshold. This is the highest-signal view for incident analysis."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "min_severity": {
                "type": "string",
                "enum": ["WARNING", "ERROR", "CRITICAL"],
                "default": "WARNING",
            },
            "window_lines": {"type": "integer", "default": 2},
        },
    },
}


def tool_get_error_context_blocks(
    min_severity: str = "WARNING", window_lines: int = 2
) -> dict:
    return {"blocks": _ANALYZER.get_error_context_blocks(min_severity, window_lines)}


# ---- Tool: filter_logs (standalone, returns filtered text without ingesting) ----

TOOL_FILTER_LOGS_SCHEMA = {
    "name": "filter_logs",
    "description": (
        "Apply include/exclude/regex/severity filtering to raw log text WITHOUT ingesting "
        "it into the analyzer. Useful for previewing what a filter would keep before "
        "committing to ingest_logs."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "raw_log_text": {"type": "string"},
            "filter": TOOL_INGEST_LOGS_SCHEMA["input_schema"]["properties"]["filter"],
        },
        "required": ["raw_log_text", "filter"],
    },
}


def tool_filter_logs(raw_log_text: str, filter: dict) -> dict:
    log_filter = LogFilter.from_dict(filter)
    lines = raw_log_text.splitlines()
    kept = log_filter.filter_lines(lines)
    return {
        "original_count": len(lines),
        "filtered_count": len(kept),
        "filtered_lines": kept,
    }


# ---- Registry: name -> (schema, handler) ----

TOOL_REGISTRY: dict[str, dict] = {
    "ingest_logs": {
        "schema": TOOL_INGEST_LOGS_SCHEMA,
        "handler": tool_ingest_logs,
    },
    "filter_logs": {
        "schema": TOOL_FILTER_LOGS_SCHEMA,
        "handler": tool_filter_logs,
    },
    "get_log_summary": {
        "schema": TOOL_GET_LOG_SUMMARY_SCHEMA,
        "handler": tool_get_log_summary,
    },
    "count_pattern_in_range": {
        "schema": TOOL_COUNT_PATTERN_SCHEMA,
        "handler": tool_count_pattern_in_range,
    },
    "get_lines_in_range": {
        "schema": TOOL_GET_LINES_IN_RANGE_SCHEMA,
        "handler": tool_get_lines_in_range,
    },
    "get_context_around": {
        "schema": TOOL_GET_CONTEXT_AROUND_SCHEMA,
        "handler": tool_get_context_around,
    },
    "get_time_buckets": {
        "schema": TOOL_GET_TIME_BUCKETS_SCHEMA,
        "handler": tool_get_time_buckets,
    },
    "get_error_context_blocks": {
        "schema": TOOL_GET_ERROR_CONTEXT_SCHEMA,
        "handler": tool_get_error_context_blocks,
    },
}


# ============================================================================
# 5. SELF-TEST / DEMO
# ============================================================================

if __name__ == "__main__":
    sample_logs = """\
2026-06-27T10:00:01Z INFO User 123 logged in from 192.168.1.5
2026-06-27T10:00:05Z INFO User 456 logged in from 10.0.0.9
2026-06-27T10:00:10Z DEBUG cache hit for key abc
2026-06-27T10:05:00Z ERROR Failed to connect to db at 10.0.0.2:5432
2026-06-27T10:05:02Z ERROR Failed to connect to db at 10.0.0.2:5432
2026-06-27T10:05:04Z WARNING retrying db connection, attempt 1
2026-06-27T10:05:06Z ERROR Failed to connect to db at 10.0.0.2:5432
2026-06-27T10:05:08Z WARNING retrying db connection, attempt 2
2026-06-27T10:10:00Z INFO User 789 logged in from 192.168.1.7
2026-06-27T10:10:01Z DEBUG heartbeat OK
2026-06-27T10:10:02Z DEBUG heartbeat OK
"""

    print("=== ingest_logs (excluding heartbeat/cache noise) ===")
    res = tool_ingest_logs(
        sample_logs,
        filter={"exclude": [{"value": "heartbeat"}, {"value": "cache hit"}]},
    )
    print(json.dumps(res, indent=2))

    print("\n=== get_log_summary ===")
    print(json.dumps(tool_get_log_summary(), indent=2))

    print("\n=== count_pattern_in_range('connect to db', 10:00-10:06) ===")
    print(
        json.dumps(
            tool_count_pattern_in_range(
                "connect to db", "2026-06-27T10:00:00", "2026-06-27T10:06:00"
            ),
            indent=2,
        )
    )

    print("\n=== get_error_context_blocks (WARNING+) ===")
    print(
        json.dumps(
            tool_get_error_context_blocks(min_severity="WARNING", window_lines=1),
            indent=2,
        )
    )

    print("\n=== get_time_buckets('login', 300s buckets) ===")
    print(
        json.dumps(
            tool_get_time_buckets(keyword="logged in", bucket_seconds=300),
            indent=2,
        )
    )
