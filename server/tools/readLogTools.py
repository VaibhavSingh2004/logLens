from pathlib import Path

from server.mcpServer import mcp
from core.logReader import LogReader


@mcp.tool
def read_log_file(file_path: str) -> dict:
    """
    Read a single log file.

    Args:
        file_path: Absolute or relative path to the log file.

    Returns:
        Dictionary containing file information and log contents.
    """

    lines = LogReader.read_file(Path(file_path))

    return {
        "file_name": Path(file_path).name,
        "path": str(file_path),
        "line_count": len(lines),
        "logs": lines,
    }


@mcp.tool
def read_log_directory(directory_path: str) -> dict:
    """
    Read all .log files from a directory.

    Args:
        directory_path: Directory containing log files.

    Returns:
        Dictionary containing every log file and its contents.
    """

    logs = LogReader.read_directory(Path(directory_path))

    result = {}

    for filename, lines in logs.items():
        result[filename] = {
            "line_count": len(lines),
            "logs": lines,
        }

    return result
