from __future__ import annotations

import copy
import logging
from typing import Any

from uvicorn.config import LOGGING_CONFIG

_UVICORN_LOGGER_NAMES = ("uvicorn", "uvicorn.error", "uvicorn.access")


def quiet_uvicorn_log_config() -> dict[str, Any]:
    """Return Uvicorn logging config that does not write to the console.

    Rich terminal dashboards own the screen. Uvicorn's startup and per-request
    loggers write outside Rich, which corrupts the dashboard layout.
    """

    config = copy.deepcopy(LOGGING_CONFIG)
    loggers = config.setdefault("loggers", {})
    for name in _UVICORN_LOGGER_NAMES:
        loggers[name] = {"handlers": [], "level": "CRITICAL", "propagate": False}
    return config


def suppress_uvicorn_access_logger() -> None:
    """Silence Uvicorn console loggers for dashboard services.

    Uvicorn can reconfigure logging during server startup. Keep this function
    intentionally stronger: mutate the stable logger objects so even later
    handler changes cannot print into Rich dashboards.
    """

    def _no_handlers() -> bool:
        return False

    def _drop_log(*_args: Any, **_kwargs: Any) -> None:
        return None

    for name in _UVICORN_LOGGER_NAMES:
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = False
        logger.disabled = True
        logger.setLevel(logging.CRITICAL + 1)
        logger.hasHandlers = _no_handlers  # type: ignore[method-assign]
        logger.debug = _drop_log  # type: ignore[method-assign]
        logger.info = _drop_log  # type: ignore[method-assign]
        logger.warning = _drop_log  # type: ignore[method-assign]
        logger.error = _drop_log  # type: ignore[method-assign]
        logger.exception = _drop_log  # type: ignore[method-assign]
        logger.critical = _drop_log  # type: ignore[method-assign]
