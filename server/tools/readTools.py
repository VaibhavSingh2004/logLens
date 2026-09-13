from pathlib import Path
from typing import Optional

from server.mcpServer import mcp
from core.logReader import LogReader


@mcp.tool
def list_log_files(directory_path: str) -> dict:
    """
    List every .log file in a directory with size and modified time, without
    reading file contents. Use this first to see what's available before
    reading or searching a specific file.

    Args:
        directory_path: Directory to scan for .log files.
    """
    files = LogReader.list_log_files(Path(directory_path))
    return {
        "directory": str(Path(directory_path).resolve()),
        "count": len(files),
        "files": files,
    }


@mcp.tool
def read_log_file(file_path: str, max_lines: Optional[int] = 1000) -> dict:
    """
    Read a log file. For files bigger than `max_lines`, only the first
    `max_lines` lines are returned (use `tail_log_file` for the end of a
    file, or `search_logs` to jump straight to what you care about).

    Args:
        file_path: Absolute or relative path to the log file.
        max_lines: Cap on lines returned (default 1000). Pass null/None for no cap.
    """
    lines = LogReader.read_file(Path(file_path))
    truncated = max_lines is not None and len(lines) > max_lines
    if truncated:
        lines = lines[:max_lines]

    return {
        "file_name": Path(file_path).name,
        "path": str(file_path),
        "line_count": len(lines),
        "truncated": truncated,
        "logs": [line.rstrip("\n") for line in lines],
    }


@mcp.tool
def tail_log_file(file_path: str, n_lines: int = 200) -> dict:
    """
    Return the last N lines of a log file, streamed rather than loaded
    entirely into memory. Ideal for "what just happened" on large files.

    Args:
        file_path: Absolute or relative path to the log file.
        n_lines: How many trailing lines to return (default 200).
    """
    lines = LogReader.tail_file(Path(file_path), n_lines=n_lines)
    return {"file_name": Path(file_path).name, "line_count": len(lines), "logs": lines}


@mcp.tool
def read_log_directory(directory_path: str) -> dict:
    """
    Read every .log file from a directory in one call.

    Args:
        directory_path: Directory containing log files.
    """
    logs = LogReader.read_directory(Path(directory_path))

    result = {}
    for filename, lines in logs.items():
        result[filename] = {
            "line_count": len(lines),
            "logs": [line.rstrip("\n") for line in lines],
        }
    return result
