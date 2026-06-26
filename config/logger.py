import logging
import os
import sys
from logging.handlers import RotatingFileHandler


class ColoredFormatter(logging.Formatter):
    """
    Custom formatter that adds ANSI colors to console logs.
    Colors are only applied to the StreamHandler.
    """

    COLORS = {
        "DEBUG": "\033[36m",  # Cyan
        "INFO": "\033[32m",  # Green
        "WARNING": "\033[33m",  # Yellow
        "ERROR": "\033[31m",  # Red
        "CRITICAL": "\033[1;31m",  # Bold Red
    }

    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, self.RESET)
        record.levelname = f"{color}{record.levelname:<8}{self.RESET}"
        return super().format(record)


# Singleton class responsible for configuring logging
class Logger:
    _configured = False

    @classmethod
    def configure(cls) -> None:
        """
        Configure the root logger once.
        """

        if cls._configured:
            return

        log_level = os.getenv("LOG_LEVEL", "INFO").upper()
        log_output = os.getenv("LOG_OUTPUT", "stdout").lower()
        log_file = os.getenv("LOG_FILE", "logs/logLens.log")
        rotation_mb = int(os.getenv("LOG_ROTATION_MB", "10"))
        backup_count = int(os.getenv("LOG_BACKUP_COUNT", "10"))

        valid_outputs = {"stdout", "file"}

        if log_output not in valid_outputs:
            raise ValueError(
                f"Invalid LOG_OUTPUT='{log_output}'. "
                f"Expected one of {valid_outputs}."
            )

        log_format = (
            "%(asctime)s | "
            "%(levelname)-8s | "
            "%(name)s | "
            "%(funcName)s:%(lineno)d | "
            "%(message)s"
        )

        date_format = "%Y-%m-%d %H:%M:%S"

        formatter = logging.Formatter(
            fmt=log_format,
            datefmt=date_format,
        )

        colored_formatter = ColoredFormatter(
            fmt=log_format,
            datefmt=date_format,
        )

        root_logger = logging.getLogger()
        root_logger.setLevel(log_level)

        # Prevent duplicate handlers
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)
            handler.close()

        # Console Handler
        if log_output in ("stdout"):
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setFormatter(colored_formatter)
            root_logger.addHandler(console_handler)

        # File Handler
        elif log_output in ("file"):
            os.makedirs(os.path.dirname(log_file), exist_ok=True)

            # Logs will rotate once the file reaches the configured size.
            # Only the configured number of backup log files will be retained.
            file_handler = RotatingFileHandler(
                filename=log_file,
                maxBytes=rotation_mb * 1024 * 1024,
                backupCount=backup_count,
                encoding="utf-8",
            )

            file_handler.setFormatter(formatter)
            root_logger.addHandler(file_handler)

        cls._configured = True

    @staticmethod
    def get_logger(name: str) -> logging.Logger:
        """
        Returns a logger instance for the given module.
        """

        Logger.configure()
        return logging.getLogger(name)
