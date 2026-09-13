from pathlib import Path
from typing import Optional

from server.mcpServer import mcp
from core.logReader import LogReader
from core.logSearch import LogSearch
from core.timeUtils import parse_user_time


@mcp.tool
def search_logs(
    file_path: str,
    keyword: Optional[str] = None,
    regex: Optional[str] = None,
    min_severity: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    case_sensitive: bool = False,
    limit: int = 500,
) -> dict:
    """
    Filter a log file by keyword, regex, minimum severity, and/or a time
    window - all conditions apply together (AND). Timestamps in the log
    lines themselves are auto-detected across common formats (ISO-8601,
    'YYYY-MM-DD HH:MM:SS[,ms]', Apache/nginx access-log style, etc).

    `start_time`/`end_time` accept a wide range of formats too, e.g.
    '2026-06-27T10:00:00', '2026-06-27 10:00', or just '2026-06-27'.

    Args:
        file_path: Path to the log file to search.
        keyword: Plain substring to match (case-insensitive unless case_sensitive=True).
        regex: Regular expression to match against each line (used together with keyword if both given).
        min_severity: Only return lines at/above this level: DEBUG, INFO, WARNING, ERROR, CRITICAL.
        start_time: Inclusive lower bound on the line's parsed timestamp.
        end_time: Inclusive upper bound on the line's parsed timestamp.
        case_sensitive: Whether keyword/regex matching is case sensitive (default False).
        limit: Max number of matches to return (default 500), to control response size.
    """
    lines = LogReader.read_file(Path(file_path))

    matches = LogSearch.search(
        lines,
        keyword=keyword,
        regex=regex,
        min_severity=min_severity,
        start_time=parse_user_time(start_time),
        end_time=parse_user_time(end_time),
        case_sensitive=case_sensitive,
        limit=limit,
    )

    return {
        "file_name": Path(file_path).name,
        "match_count": len(matches),
        "matches": LogSearch.to_dicts(matches),
    }


@mcp.tool
def get_log_stats(file_path: str) -> dict:
    """
    Quick overview of a log file: total lines, a breakdown of counts per
    severity level, and the first/last timestamp found. Use this before
    `search_logs` to sanity-check what time range and severities exist.

    Args:
        file_path: Path to the log file.
    """
    lines = LogReader.read_file(Path(file_path))
    stats = LogSearch.stats(lines)
    return {"file_name": Path(file_path).name, **stats}
