from pathlib import Path

from server.mcpServer import mcp
from core.logReader import LogReader
from core.logOptimizer import LogOptimizer


@mcp.tool
def collapse_duplicate_lines(file_path: str) -> dict:
    """
    Compress a noisy log file by collapsing duplicate lines into a single
    occurrence plus a "... repeated N more times ..." marker. Use this
    before reading a file that's dominated by repeated heartbeats/retries,
    to save tokens without losing information about how often each line fired.

    Args:
        file_path: Path to the log file to compress.
    """
    lines = LogReader.read_file(Path(file_path))
    stripped = [line.rstrip("\n") for line in lines]
    collapsed = LogOptimizer.collapse_duplicates(stripped)

    return {
        "file_name": Path(file_path).name,
        "original_line_count": len(stripped),
        "collapsed_line_count": len(collapsed),
        "logs": collapsed,
    }
