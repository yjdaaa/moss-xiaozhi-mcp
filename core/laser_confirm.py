"""Strict confirmation parsing for laser send gates.

Pure helper: no FastMCP, tools, Web, or device imports.
Used by core.laser_execution and tools.laser_workflow_tool so every entry
rejects bool("false")-style truthiness bugs independently of callers.
"""

from __future__ import annotations

from typing import Any

_TRUE_TOKENS = frozenset({"1", "true", "yes", "y", "on", "确认", "confirmed"})
_FALSE_TOKENS = frozenset(
    {"0", "false", "no", "n", "off", "否", "取消", "null", "none", "undefined"}
)


def is_explicitly_confirmed(value: Any) -> bool:
    """Return True only for explicit confirmation expressions.

    Python ``bool("false")`` is True — this helper must NOT accept that.
    Accepted true forms: True, 1, "true", "1", "yes", "y", "on", "confirmed", "确认".
    Everything else (False, None, "", "false", "0", "off", "no", other strings) is False.
    """
    if value is True:
        return True
    if value is False or value is None:
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value == 1
    normalized = str(value).strip().lower()
    if not normalized:
        return False
    if normalized in _FALSE_TOKENS:
        return False
    return normalized in _TRUE_TOKENS
