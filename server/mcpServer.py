from fastmcp import FastMCP

# Register all tools
import server.tools.readLogTools  # noqa: F401

mcp = FastMCP(
    name="LogLens",
    instructions="""
    LogLens is an MCP server for analyzing production logs.

    Capabilities:
    - Read log files
    - Read directories
    - Search logs
    - Filter logs
    - Generate reports
    - Analyze incidents
    """,
)
