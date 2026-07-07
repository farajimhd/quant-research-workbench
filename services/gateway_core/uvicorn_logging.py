from __future__ import annotations

import copy
import logging
from typing import Any

from uvicorn.config import LOGGING_CONFIG


def quiet_uvicorn_log_config() -> dict[str, Any]:
    """Return Uvicorn logging config without per-request access lines.

    Rich terminal dashboards own the screen. Uvicorn's access logger writes
    normal HTTP request lines outside Rich, which corrupts the dashboard layout.
    Keep Uvicorn startup/error logging, but remove the access logger handlers.
    """

    config = copy.deepcopy(LOGGING_CONFIG)
    config.setdefault("loggers", {})["uvicorn.access"] = {
        "handlers": [],
        "level": "WARNING",
        "propagate": False,
    }
    return config


def suppress_uvicorn_access_logger() -> None:
    logger = logging.getLogger("uvicorn.access")
    logger.handlers.clear()
    logger.propagate = False
    logger.disabled = True
