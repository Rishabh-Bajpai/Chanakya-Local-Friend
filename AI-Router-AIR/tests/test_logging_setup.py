from logging.handlers import RotatingFileHandler

from server.core.logging import LOG_FILE_PATH, logger, setup_logging


def _has_runtime_file_handler(logger_name: str) -> bool:
    target = str(LOG_FILE_PATH)
    current_logger = __import__("logging").getLogger(logger_name)
    return any(
        isinstance(handler, RotatingFileHandler) and getattr(handler, "baseFilename", None) == target
        for handler in current_logger.handlers
    )


def test_setup_logging_attaches_realtime_file_handler_only_to_air_logger():
    setup_logging()

    assert LOG_FILE_PATH.parent.exists()
    assert _has_runtime_file_handler("air")
    assert not _has_runtime_file_handler("")
    assert not _has_runtime_file_handler("uvicorn")
    assert not _has_runtime_file_handler("uvicorn.error")
    assert not _has_runtime_file_handler("uvicorn.access")
    assert logger.name == "air"
