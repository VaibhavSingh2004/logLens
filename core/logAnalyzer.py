"""
Stateful, drain3-backed log analyzer.

This is the "advanced" tier of LogLens: instead of just filtering raw lines,
it clusters them into structural templates (e.g. "User <NUM> logged in from
<IP>" instead of a thousand near-identical lines), then tracks per-cluster
timestamps and severities so you can ask things like "how many times did the
DB-connect-failed template fire between 10:00 and 10:06?" without an LLM
ever reading the raw lines.

`drain3` is an optional dependency (see requirements.txt). If it isn't
installed, constructing a LogAnalyzer raises a clear RuntimeError instead of
the whole MCP server failing to import.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from core.logFilter import LogFilter
from core.severity import extract_severity, severity_rank
from core.timeUtils import extract_timestamp

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
        self, config_path: Optional[str] = None, persistence_path: Optional[str] = None
    ):
        try:
            from drain3 import TemplateMiner
            from drain3.template_miner_config import TemplateMinerConfig
            from drain3.file_persistence import FilePersistence
        except ImportError as e:
            raise RuntimeError(
                "The template-clustering tools require the optional 'drain3' "
                "dependency. Install it with: pip install drain3"
            ) from e

        cfg = TemplateMinerConfig()
        if config_path:
            cfg.load(config_path)
        else:
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
                    "first_seen": min(timestamps).isoformat() if timestamps else None,
                    "last_seen": max(timestamps).isoformat() if timestamps else None,
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
