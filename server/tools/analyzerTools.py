from pathlib import Path
from typing import Optional

from server.mcpServer import mcp
from core.logAnalyzer import LogAnalyzer
from core.logFilter import LogFilter
from core.logReader import LogReader
from core.timeUtils import parse_user_time

_ANALYZER: Optional[LogAnalyzer] = None


def _get_analyzer() -> LogAnalyzer:
    global _ANALYZER
    if _ANALYZER is None:
        _ANALYZER = (
            LogAnalyzer()
        )  # raises RuntimeError with install instructions if drain3 missing
    return _ANALYZER


@mcp.tool
def ingest_logs_for_analysis(file_path: str, filter: Optional[dict] = None) -> dict:
    """
    Load a log file into the template-clustering analyzer. Optionally filter
    lines (include/exclude/regex/severity/time-range) BEFORE clustering, so
    noise never pollutes the templates. Call this once per file before using
    get_log_summary / count_pattern_in_range / get_error_context_blocks /
    get_time_buckets. Ingestion is cumulative across calls within a session -
    use reset_log_analysis to start over.

    Args:
        file_path: Path to the log file to ingest.
        filter: Optional filter spec, e.g.
            {"exclude": [{"value": "heartbeat"}], "severities": ["ERROR", "CRITICAL"]}
    """
    try:
        analyzer = _get_analyzer()
    except RuntimeError as e:
        return {"error": str(e)}

    lines = LogReader.read_file(Path(file_path))
    raw_text = "".join(lines)
    log_filter = LogFilter.from_dict(filter) if filter else None
    return analyzer.ingest(raw_text, log_filter)


@mcp.tool
def reset_log_analysis() -> dict:
    """Clear all previously-ingested lines/occurrences from the analyzer (keeps the learned template tree)."""
    try:
        analyzer = _get_analyzer()
    except RuntimeError as e:
        return {"error": str(e)}
    analyzer.reset()
    return {"status": "reset"}


@mcp.tool
def get_log_summary(
    top_n: Optional[int] = None, min_severity: Optional[str] = None
) -> dict:
    """
    Return the compressed template summary: one entry per unique log pattern
    (drain3-mined), with occurrence count, max severity seen, first/last
    timestamp, and example lines. Call this first, after ingesting, to get
    an overview before drilling down.

    Args:
        top_n: Limit to the top N most frequent templates.
        min_severity: Only include templates whose max severity is at/above this level.
    """
    try:
        analyzer = _get_analyzer()
    except RuntimeError as e:
        return {"error": str(e)}
    return {"templates": analyzer.get_summary(top_n=top_n, min_severity=min_severity)}


@mcp.tool
def count_pattern_in_range(
    keyword: str, start_time: Optional[str] = None, end_time: Optional[str] = None
) -> dict:
    """
    Deterministically count how many times a keyword/pattern (matched
    against mined templates) occurred, optionally restricted to a timestamp
    range. Use this instead of asking an LLM to count lines manually.

    Args:
        keyword: Keyword to match against template text, e.g. 'connect to db'.
        start_time: Optional lower bound timestamp.
        end_time: Optional upper bound timestamp.
    """
    try:
        analyzer = _get_analyzer()
    except RuntimeError as e:
        return {"error": str(e)}
    return analyzer.count_pattern_in_range(
        keyword, parse_user_time(start_time), parse_user_time(end_time)
    )


@mcp.tool
def get_lines_in_range(
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    keyword: Optional[str] = None,
    min_severity: Optional[str] = None,
    limit: int = 500,
) -> dict:
    """
    Return verbatim, previously-ingested log lines within a timestamp range,
    optionally filtered by keyword and/or minimum severity.

    Args:
        start_time: Optional lower bound timestamp.
        end_time: Optional upper bound timestamp.
        keyword: Optional substring the line must contain.
        min_severity: Only lines at/above this severity level.
        limit: Max lines to return (default 500).
    """
    try:
        analyzer = _get_analyzer()
    except RuntimeError as e:
        return {"error": str(e)}
    lines = analyzer.get_lines_in_range(
        parse_user_time(start_time),
        parse_user_time(end_time),
        keyword,
        min_severity,
        limit,
    )
    return {"count": len(lines), "lines": lines}


@mcp.tool
def get_context_around(
    timestamp: Optional[str] = None,
    line_index: Optional[int] = None,
    window_seconds: int = 60,
    window_lines: int = 5,
) -> dict:
    """
    Return verbatim lines surrounding a specific moment in time or a
    specific line index, for root-cause investigation. Provide exactly one
    of `timestamp` or `line_index`.

    Args:
        timestamp: A point in time to center the window on.
        line_index: A specific ingested-line index to center the window on.
        window_seconds: Half-width of the time window in seconds (used with `timestamp`).
        window_lines: Half-width of the window in lines (used with `line_index`).
    """
    try:
        analyzer = _get_analyzer()
    except RuntimeError as e:
        return {"error": str(e)}
    lines = analyzer.get_context_around(
        parse_user_time(timestamp), line_index, window_seconds, window_lines
    )
    return {"count": len(lines), "lines": lines}


@mcp.tool
def get_time_buckets(
    keyword: Optional[str] = None,
    bucket_seconds: int = 300,
    min_severity: Optional[str] = None,
) -> dict:
    """
    Bucket matching, previously-ingested log lines into fixed-size time
    windows with counts per bucket. Use this to spot spikes/anomalies over
    time without reading every line.

    Args:
        keyword: Optional substring to filter lines before bucketing.
        bucket_seconds: Width of each time bucket in seconds (default 300 = 5 min).
        min_severity: Only count lines at/above this severity level.
    """
    try:
        analyzer = _get_analyzer()
    except RuntimeError as e:
        return {"error": str(e)}
    return {"buckets": analyzer.get_time_buckets(keyword, bucket_seconds, min_severity)}


@mcp.tool
def get_error_context_blocks(
    min_severity: str = "WARNING", window_lines: int = 2
) -> dict:
    """
    Return verbatim, merged context blocks around every previously-ingested
    line at or above a severity threshold. This is the highest-signal view
    for incident analysis - it surfaces exactly the lines around each
    problem without dumping the whole file.

    Args:
        min_severity: Severity threshold: WARNING, ERROR, or CRITICAL (default WARNING).
        window_lines: How many lines of context to include on each side (default 2).
    """
    try:
        analyzer = _get_analyzer()
    except RuntimeError as e:
        return {"error": str(e)}
    return {"blocks": analyzer.get_error_context_blocks(min_severity, window_lines)}
