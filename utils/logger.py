"""Structured logging setup."""

from __future__ import annotations

import json
import logging
import sys
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
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(
    log_dir: Path,
    level: str = "INFO",
    json_format: bool = True,
) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "getkernel.log"

    root = logging.getLogger("getkernel")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers.clear()

    handler_file = logging.FileHandler(log_file, encoding="utf-8")
    handler_stream = logging.StreamHandler(sys.stderr)

    if json_format:
        handler_file.setFormatter(JSONFormatter())
        handler_stream.setFormatter(JSONFormatter())
    else:
        plain = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        handler_file.setFormatter(plain)
        handler_stream.setFormatter(plain)

    root.addHandler(handler_file)
    root.addHandler(handler_stream)
    return root


def log_exception(
    logger: logging.Logger,
    error: BaseException,
    context: Optional[Mapping[str, Any]] = None,
) -> None:
    import traceback

    entry = {
        "level": "ERROR",
        "error_type": type(error).__name__,
        "message": str(error),
        "traceback": traceback.format_exc(),
        "context": dict(context or {}),
    }
    logger.error(json.dumps(entry, ensure_ascii=False))


def log_build_event(
    logger: logging.Logger,
    event: str,
    build_id: str,
    extra: Optional[Mapping[str, Any]] = None,
) -> None:
    """Structured build lifecycle line (JSON) for observability."""
    payload: dict[str, Any] = {"event": event, "build_id": build_id}
    if extra:
        payload.update(dict(extra))
    logger.info(json.dumps(payload, ensure_ascii=False))
