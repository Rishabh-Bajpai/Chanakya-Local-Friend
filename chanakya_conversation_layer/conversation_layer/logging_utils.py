from __future__ import annotations

import json
import logging
from typing import Any


LOGGER_NAME = "chanakya.wrapper"


def get_logger() -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = True
    return logger


def log_event(stage: str, **payload: Any) -> None:
    event = {"stage": stage, **payload}
    get_logger().info(json.dumps(event, default=str, sort_keys=True))
