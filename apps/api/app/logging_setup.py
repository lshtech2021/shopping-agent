"""Configure process-wide logging for shopping-agent API."""
from __future__ import annotations

import logging
import re
import sys

from app.config import settings

_CONFIGURED = False


class _AccessPollingFilter(logging.Filter):
    """Drop the side panel's 1.5 s session/event polling from access logs."""

    _SESSION_DETAIL = re.compile(r"^/api/v1/sessions/[^/]+$")

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, tuple) or len(args) < 3:
            return True
        method = str(args[1])
        path = str(args[2]).split("?", 1)[0]
        if method != "GET":
            return True
        if path.endswith("/events") or self._SESSION_DETAIL.match(path):
            return False
        return True


def setup_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    level_name = (settings.log_level or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s %(levelname)s [%(name)s] %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        root.addHandler(handler)
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.setLevel(logging.INFO)
    access_logger.addFilter(_AccessPollingFilter())
    logging.getLogger("httpx").setLevel(logging.WARNING)
    _CONFIGURED = True
    logging.getLogger(__name__).info(
        "logging ready level=%s mock_browser=%s",
        level_name,
        settings.mock_browser,
    )
