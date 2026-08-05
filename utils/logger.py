"""Structured logging setup."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional


class JSONFormatter(logging.Formatter):
    """One JSON object per log line."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "module": record.module,
            "message": record.getMessage(),
        }
        for key in ("event", "build_id", "error_type", "context"):
            value = getattr(record, key, None)
            if value not in (None, "", {}):
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(
    log_dir: Path,
    level: str = "INFO",
    json_format: bool = True,
) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "getkernel.log"

    root = logging.getLogger("getkernel")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for existing in root.handlers:
        existing.close()
    root.handlers.clear()
    root.propagate = False

    handler_file = logging.FileHandler(log_file, encoding="utf-8")
    if json_format:
        handler_file.setFormatter(JSONFormatter())
    else:
        plain = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        handler_file.setFormatter(plain)
    root.addHandler(handler_file)
    return root


def log_exception(
    logger: logging.Logger,
    error: BaseException,
    context: Optional[Mapping[str, Any]] = None,
) -> None:
    logger.error(
        str(error),
        exc_info=(type(error), error, error.__traceback__)
        if error.__traceback__ is not None
        else None,
        extra={
            "error_type": type(error).__name__,
            "context": dict(context or {}),
        },
    )


def log_build_event(
    logger: logging.Logger,
    event: str,
    build_id: str,
    extra: Optional[Mapping[str, Any]] = None,
) -> None:
    """Structured build lifecycle line (JSON) for observability."""
    context = dict(extra or {})
    logger.info(
        event,
        extra={"event": event, "build_id": build_id, "context": context},
    )
