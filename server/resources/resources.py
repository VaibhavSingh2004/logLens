import os
from datetime import datetime
from pathlib import Path

from server.mcpServer import mcp
from core.logReader import LogReader

# Override with the LOGLENS_LOG_DIR environment variable for your deployment.
DEFAULT_LOG_DIR = Path(os.environ.get("LOGLENS_LOG_DIR", "./logs"))


@mcp.resource("logs://directory")
def log_directory() -> str:
    """Returns the default log directory."""
    return str(DEFAULT_LOG_DIR.resolve())


@mcp.resource("logs://available")
def available_logs() -> str:
    """Returns the names of all available log files in the default directory."""
    if not DEFAULT_LOG_DIR.exists():
        return "Log directory does not exist."

    files = sorted(DEFAULT_LOG_DIR.glob("*.log"))

    if not files:
        return "No log files found."

    return "\n".join(file.name for file in files)


@mcp.resource("logs://latest")
def latest_log() -> str:
    """Returns the contents of the newest log file in the default directory."""
    if not DEFAULT_LOG_DIR.exists():
        return "Log directory does not exist."

    files = list(DEFAULT_LOG_DIR.glob("*.log"))

    if not files:
        return "No log files found."

    latest = max(files, key=lambda f: f.stat().st_mtime)

    lines = LogReader.read_file(latest)

    return "".join(lines)


@mcp.resource("logs://today")
def today_log() -> str:
    """
    Returns today's log if present.
    Example filename: app_2026-09-13.log
    """
    filename = datetime.now().strftime("app_%Y-%m-%d.log")
    path = DEFAULT_LOG_DIR / filename

    if not path.exists():
        return f"{filename} not found."

    return "".join(LogReader.read_file(path))


@mcp.resource("docs://log-format")
def log_format() -> str:
    """Documentation describing the expected log line format and supported timestamp styles."""
    return """
LogLens - Supported Log Line Formats
=====================================

Expected general shape:
    [Timestamp] [LEVEL] [MODULE] Message

Example:
    2026-06-27 10:22:01 INFO Server Started
    2026-06-27 10:22:05 ERROR Database Connection Failed

Timestamp formats recognized inside log lines:
    2026-06-27T10:22:01.445Z
    2026-06-27T10:22:01Z
    2026-06-27 10:22:01,445      (syslog/log4j comma-millis style)
    2026-06-27 10:22:01.445
    2026-06-27 10:22:01
    27/Jun/2026:10:22:01 +0000   (Apache/nginx access-log style)

Severity levels recognized (canonicalized to the left-hand form):
    TRACE
    DEBUG
    INFO      (NOTICE is treated as INFO)
    WARNING   (WARN is treated as WARNING)
    ERROR     (SEVERE is treated as ERROR)
    CRITICAL  (FATAL, ALERT, EMERGENCY are treated as CRITICAL)

Lines that don't match a recognized timestamp/severity still work fine -
they're just treated as timestamp=None / severity=INFO for filtering
purposes, so nothing is silently dropped.
"""
