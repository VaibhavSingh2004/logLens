from config.env import init_env
from config.logger import Logger

from server.mcpServer import mcp

init_env()
logger = Logger.get_logger(__name__)


def main():
    logger.info("Starting LogLens MCP Server...")
    mcp.run()


if __name__ == "__main__":
    main()
