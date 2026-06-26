from pathlib import Path
from typing import Iterator

from config.logger import Logger

logger = Logger.get_logger(__name__)


class LogReader:
    """
    Responsible for reading log files.

    Features:
        - Read a single log file
        - Read all log files from a directory
        - Stream large files line-by-line
    """

    @staticmethod
    def read_file(file_path: str | Path) -> list[str]:
        """
        Read an entire log file into memory.

        Best suited for small and medium-sized log files.
        """

        file_path = Path(file_path)

        logger.info("Reading log file: %s", file_path)

        if not file_path.exists():
            logger.error("Log file not found: %s", file_path)
            raise FileNotFoundError(file_path)

        with file_path.open("r", encoding="utf-8") as file:
            lines = file.readlines()

        logger.info("Loaded %d log lines.", len(lines))

        return lines

    @staticmethod
    def stream_file(file_path: str | Path) -> Iterator[str]:
        """
        Stream a log file line-by-line.

        Suitable for very large production log files.
        """

        file_path = Path(file_path)

        logger.info("Streaming log file: %s", file_path)

        if not file_path.exists():
            logger.error("Log file not found: %s", file_path)
            raise FileNotFoundError(file_path)

        with file_path.open("r", encoding="utf-8") as file:
            for line in file:
                yield line.rstrip("\n")

    @staticmethod
    def read_directory(directory: str | Path) -> dict[str, list[str]]:
        """
        Read every .log file from a directory.

        Returns:
            {
                "app.log": [...],
                "payment.log": [...]
            }
        """

        directory = Path(directory)

        logger.info("Scanning directory: %s", directory)

        if not directory.exists():
            logger.error("Directory not found: %s", directory)
            raise FileNotFoundError(directory)

        logs: dict[str, list[str]] = {}

        for file in sorted(directory.glob("*.log")):
            logs[file.name] = LogReader.read_file(file)

        logger.info("Loaded %d log file(s).", len(logs))

        return logs
