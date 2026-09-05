from __future__ import annotations

import json
import logging
import re
from typing import Any


LOGGER_NAME = "LASER_RUNTIME"
SAFE_FIELD_NAMES = {
    "event",
    "stage",
    "workflow_id",
    "task_id",
    "job_id",
    "transport",
    "error_code",
    "status",
    "duration",
    "config_field",
    "scope",
}
SENSITIVE_KEY_RE = re.compile(
    r"(?i)(token|password|passwd|secret|api[_-]?key|credential|network_host|host|url|command|path)"
)
SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(token|password|passwd|secret|api[_-]?key)\s*[:=]\s*[^,\s;]+"
)


def configure_laser_logger(force: bool = False) -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    if force:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def log_event(event: str, logger: logging.Logger | None = None, **fields: Any) -> None:
    payload = {"event": str(event)}
    payload.update(fields)
    safe_payload = {
        str(key): _redact(value, str(key)) if str(key) in SAFE_FIELD_NAMES else "<redacted>"
        for key, value in payload.items()
    }
    (logger or configure_laser_logger()).info(
        json.dumps(safe_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


def _redact(value: Any, key: str = "") -> Any:
    if key and SENSITIVE_KEY_RE.search(key):
        return "<redacted>"
    if isinstance(value, dict):
        return {str(item_key): _redact(item, str(item_key)) for item_key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return SENSITIVE_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}=<redacted>", value)
    return value
