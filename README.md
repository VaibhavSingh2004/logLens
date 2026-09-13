# LogLens

An MCP server that lets an AI model read, search, and analyze log files.

## Project structure

```
loglens/
├── main.py                         # entry point - run this
├── requirements/
|   ├── base.txt
|   ├── local.txt
|   └── production.txt
├── config/
│   └── logger.py                   # stderr-only logger (stdout is reserved for MCP protocol)
├── core/                           # all logic here has zero MCP/FastMCP dependency -
│   │                               # it's plain, unit-testable Python.
│   ├── logReader.py                # read / stream / tail files, list & read a directory
│   ├── logOptimizer.py             # collapse duplicate lines
│   ├── timeUtils.py                # multi-format timestamp extraction + flexible parsing
│   ├── severity.py                 # severity detection, canonicalization, ranking
│   ├── logFilter.py                # composable include/exclude/regex/severity/time rules
│   ├── logSearch.py                # simple AND-of-all-conditions search (the main filter tool)
│   └── logAnalyzer.py              # optional drain3 template-clustering analyzer
└── server/
    ├── mcpServer.py                # the shared FastMCP() instance
    ├── tools/
    │   ├── readTools.py            # list_log_files, read_log_file, tail_log_file, read_log_directory
    │   ├── filterTools.py          # search_logs, get_log_stats  <- core word/time filtering
    │   ├── optimizeTools.py        # collapse_duplicate_lines
    │   └── analyzerTools.py        # ingest_logs_for_analysis, get_log_summary, ... (needs drain3)
    └── resources/
        └── logResources.py         # logs://directory, logs://available, logs://latest,
                                    # logs://today, docs://log-format
```

Why it's split this way:
- **`core/` has no knowledge of MCP.** You can import and unit-test every class directly. All
  the actual logic (parsing, filtering, clustering) lives here.
- **`server/tools/` are thin wrappers.** Each function reads files via `core.logReader`, calls
  into `core/`, and shapes the result into a JSON-friendly dict. No business logic lives here.
- **Optional dependency isolation.** `drain3` is only imported inside `core/logAnalyzer.py`,
  and only when a `LogAnalyzer` is actually constructed. If it's not installed, every other
  tool in the server still works - only the analyzer tools return a clear "install drain3"
  error.

## Setup

```bash
pip install -r requirements.txt
python main.py
```

By default this runs over stdio, so point your MCP client (Claude Desktop, Claude Code, etc.)
at `python /path/to/loglens/main.py`.

Set `LOGLENS_LOG_DIR` to point the `logs://*` resources at your actual log directory:

```bash
export LOGLENS_LOG_DIR=/var/log/myapp
```

## Tools

| Tool | Purpose |
|---|---|
| `list_log_files` | List `.log` files in a directory with size/mtime, no content read. |
| `read_log_file` | Read a file (capped at `max_lines`, default 1000). |
| `tail_log_file` | Efficiently get the last N lines of a large file. |
| `read_log_directory` | Read every `.log` file in a directory at once. |
| `search_logs` | **The main filter tool.** Keyword + regex + min-severity + time-range, all AND'd together. Auto-detects timestamps in many formats. |
| `get_log_stats` | Quick overview: line count, severity breakdown, time span. |
| `collapse_duplicate_lines` | Compress repeated lines into `... repeated N more times ...`. |
| `ingest_logs_for_analysis` | Load a file into the drain3 template-clustering analyzer (optional dep). |
| `reset_log_analysis` | Clear ingested state (keeps the learned template tree). |
| `get_log_summary` | One entry per mined template: count, max severity, first/last seen, examples. |
| `count_pattern_in_range` | Deterministic count of a keyword's occurrences, optionally in a time range. |
| `get_lines_in_range` | Verbatim ingested lines in a time range, filterable by keyword/severity. |
| `get_context_around` | Verbatim lines around a timestamp or line index. |
| `get_time_buckets` | Counts per fixed-size time window - spike/anomaly detection. |
| `get_error_context_blocks` | Merged verbatim blocks around every WARNING+/ERROR+/CRITICAL+ line. |

## Resources

- `logs://directory` - the configured default log directory
- `logs://available` - names of `.log` files in it
- `logs://latest` - contents of the most recently modified log file
- `logs://today` - contents of `app_<YYYY-MM-DD>.log` if it exists
- `docs://log-format` - documentation of the timestamp/severity formats LogLens understands

## Example: searching by word and time

```
search_logs(
    file_path="/var/log/myapp/app.log",
    keyword="timeout",
    min_severity="WARNING",
    start_time="2026-06-27 10:00",
    end_time="2026-06-27T10:15:00Z",
)
```

Timestamps embedded in log lines are matched against ISO-8601 (with or without milliseconds
or a timezone), the `YYYY-MM-DD HH:MM:SS,mmm` syslog/log4j style, and Apache/nginx access-log
style (`27/Jun/2026:10:22:01 +0000`). The `start_time`/`end_time` arguments you pass in are
parsed even more permissively (via `python-dateutil` when installed, with a format-list
fallback), so `"2026-06-27"` or `"06/27/2026 10:00:00"` both work.

## Notes / things to decide for your deployment

- There's no path sandboxing - any tool that takes a `file_path`/`directory_path` will read
  whatever the process has permission to read. Restrict this at the OS/container level if the
  MCP client isn't fully trusted.
- `drain3` clustering state is in-memory and per-process; restart the server to clear it (or
  call `reset_log_analysis`, which clears ingested lines but keeps the learned template tree).
- `logs://today`'s filename pattern (`app_%Y-%m-%d.log`) is a placeholder - change it in
  `server/resources/logResources.py` to match your actual naming convention.
