from fastmcp import FastMCP

mcp = FastMCP(
    name="LogLens",
    instructions="""
    LogLens is an MCP server for reading, searching, and analyzing log files.

    Typical workflow:
    1. `list_log_files` (or the `logs://available` resource) to see what's there.
    2. `search_logs` to filter by keyword/regex/severity/time-range - supports
        many timestamp formats out of the box (ISO-8601, syslog-style,
        Apache/nginx access-log style, comma-decimal milliseconds, etc.).
    3. `get_log_stats` for a quick severity/time-span overview, or
        `collapse_duplicate_lines` to compress repetitive noise before reading.
    4. For deeper root-cause work, use the template-clustering tools
        (`ingest_logs_for_analysis`, `get_log_summary`, `get_error_context_blocks`,
        `get_time_buckets`, `count_pattern_in_range`). These group near-identical
        lines into templates so you don't have to read every occurrence. They
        require the optional `drain3` package - if it isn't installed, the tool
        will return a clear error explaining how to add it.

    All read tools work on files under any path you pass in; there is no
    sandboxing, so only point this server at directories you're comfortable
    having an LLM read.
    """,
)
