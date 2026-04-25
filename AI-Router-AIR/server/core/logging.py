import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parents[3] / "build" / "runtime"
LOG_FILE_PATH = LOG_DIR / "ai-router-air.realtime.log"
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def _has_handler(logger: logging.Logger, handler_type: type[logging.Handler], *, base_filename: str | None = None) -> bool:
    for existing_handler in logger.handlers:
        if not isinstance(existing_handler, handler_type):
            continue
        if base_filename is None:
            return True
        if getattr(existing_handler, "baseFilename", None) == base_filename:
            return True
    return False


def _build_file_handler(formatter: logging.Formatter) -> RotatingFileHandler:
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
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(LOG_FORMAT)

    logger = logging.getLogger("air")

    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    if not _has_handler(logger, logging.StreamHandler):
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    if not _has_handler(logger, RotatingFileHandler, base_filename=str(LOG_FILE_PATH)):
        logger.addHandler(_build_file_handler(formatter))

    return logger


logger = setup_logging()
