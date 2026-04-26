"""Logging configuration for AIR console and rotating file output."""

import logging
import sys

from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parents[2] / "build" / "runtime"
LOG_FILE_PATH = LOG_DIR / "ai-router-air.realtime.log"
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def _has_console_handler(logger: logging.Logger) -> bool:
    """Return whether the logger already has a non-file stream handler."""
    return any(
        isinstance(existing_handler, logging.StreamHandler)
        and not isinstance(existing_handler, logging.FileHandler)
        for existing_handler in logger.handlers
    )


def _has_handler(logger: logging.Logger, handler_type: type[logging.Handler], *, base_filename: str | None = None) -> bool:
    """Return whether the logger already has a matching handler configured."""
    for existing_handler in logger.handlers:
        if not isinstance(existing_handler, handler_type):
            continue
        if base_filename is None:
            return True
        if getattr(existing_handler, "baseFilename", None) == base_filename:
            return True
    return False


def _build_file_handler(formatter: logging.Formatter) -> RotatingFileHandler:
    """Create the rotating file handler used for AIR runtime logs."""
    file_handler = RotatingFileHandler(
        LOG_FILE_PATH,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    return file_handler


def setup_logging() -> logging.Logger:
    """Configure and return the shared AIR application logger."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(LOG_FORMAT)
    root_logger = logging.getLogger()

    root_logger.setLevel(logging.INFO)
    if not _has_console_handler(root_logger):
        root_console_handler = logging.StreamHandler(sys.stdout)
        root_console_handler.setLevel(logging.INFO)
        root_console_handler.setFormatter(formatter)
        root_logger.addHandler(root_console_handler)

    logger = logging.getLogger("air")

    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    if not _has_console_handler(logger):
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    if not _has_handler(logger, RotatingFileHandler, base_filename=str(LOG_FILE_PATH)):
        logger.addHandler(_build_file_handler(formatter))

    return logger

logger = setup_logging()
