"""Shared material safety decisions for Web and Xiaozhi laser workflows.

Pure helper: no FastMCP, Web server, device, or sender imports.
Returns structured decisions only; callers map to success/result/speech.
"""

from __future__ import annotations

from typing import Any

# Explicit allow decisions are for known-safe labels that match material library flow.
# This module only gates soft-card / PVC / ambiguous plastics before recommendation.

DECISION_ALLOW = "allow"
DECISION_CLARIFY = "clarify"
DECISION_BLOCK = "block"

# Manual processing fields that must not override material library unless confirmed.
MANUAL_PARAM_KEYS = (
    "power_percent",
    "laser_min_power",
    "laser_max_power",
    "feed_rate",
    "travel_rate",
    "passes",
    "pixel_size_mm",
    "threshold",
    "frequency",
    "freq_hz",
    "pwm_frequency",
    "laser_frequency",
)

_SOFT_CARD_TOKENS = (
    "软卡",
    "软卡片",
    "软性卡",
    "软质卡",
)

# Paper qualifiers that resolve soft-card ambiguity (must still go through material library).
_PAPER_SOFT_CARD_QUALIFIERS = (
    "纸质",
    "纸制",
    "纸做",
    "paper",
)

_PVC_TOKENS = (
    "pvc",
    "聚氯乙烯",
    "聚氯已烯",  # common typo tolerance
    "polyvinyl chloride",
    "polyvinylchloride",
)

# Explicit hazardous plastics (block). Keep list small per task scope.
_BLOCKED_PLASTIC_TOKENS = (
    "氯乙烯",
    "vinyl chloride",
    "聚氯乙烯塑料",
    "pvc板",
    "pvc 板",
    "pvc片",
    "pvc 片",
    "pvc膜",
    "pvc 膜",
    "pvc塑料",
    "pvc 塑料",
)

# Generic / ambiguous plastic wording → clarify (cannot generate sendable preview).
_AMBIGUOUS_PLASTIC_TOKENS = (
    "塑料",
    "塑胶",
    "plastic",
    "不明塑料",
    "成分不明",
    "未知塑料",
    "普通塑料",
    "塑料板",
    "塑料片",
    "塑料膜",
)


def _normalize_material_text(material: Any) -> str:
    text = str(material or "").strip().lower()
    if not text:
        return ""
    # Collapse whitespace and common separators for token matching.
    for ch in ("_", "-", "/", "\\", "（", "）", "(", ")", "[", "]", "{", "}", "·", "•"):
        text = text.replace(ch, " ")
    return " ".join(text.split())


def _contains_token(normalized: str, token: str) -> bool:
    token_n = _normalize_material_text(token)
    if not token_n:
        return False
    # Substring match is intentional for Chinese labels like "白色软卡" / "黑色PVC板".
    return token_n in normalized


def _has_any_token(normalized: str, tokens: tuple[str, ...]) -> bool:
    return any(_contains_token(normalized, token) for token in tokens)


def _is_soft_card_label(normalized: str) -> bool:
    return _has_any_token(normalized, _SOFT_CARD_TOKENS)


def _is_paper_qualified_soft_card(normalized: str) -> bool:
    return _is_soft_card_label(normalized) and _has_any_token(normalized, _PAPER_SOFT_CARD_QUALIFIERS)


def _is_hazardous_plastic(normalized: str) -> bool:
    return _has_any_token(normalized, _PVC_TOKENS) or _has_any_token(normalized, _BLOCKED_PLASTIC_TOKENS)


def _is_ambiguous_plastic(normalized: str) -> bool:
    return _has_any_token(normalized, _AMBIGUOUS_PLASTIC_TOKENS)


def evaluate_material_safety(material: Any) -> dict:
    """Evaluate spoken/written material labels before preview generation.

    Priority (fail closed for hazards):
      1. PVC / vinyl chloride / listed hazardous plastics → block
         (including compounds like "PVC软卡")
      2. Soft-card family:
         - with plastic wording ("塑料软卡") → clarify, never allow
         - with paper qualifier ("纸质软卡") → allow (material library still applies)
         - bare / color-only soft card → clarify paper vs plastic
      3. Bare / ambiguous plastic → clarify
      4. Otherwise allow (only means this helper does not block)

    Returns:
        {
          "decision": "allow" | "clarify" | "block",
          "reason": short machine-facing reason code,
          "message": user-facing Chinese message,
          "material": original stripped material string,
        }
    """
    original = str(material or "").strip()
    normalized = _normalize_material_text(original)
    if not normalized:
        return {
            "decision": DECISION_ALLOW,
            "reason": "empty_material",
            "message": "",
            "material": original,
        }

    # 1) Explicit PVC / vinyl chloride family always wins — even combined with 软卡.
    if _is_hazardous_plastic(normalized):
        return {
            "decision": DECISION_BLOCK,
            "reason": "pvc_or_hazardous_plastic",
            "message": "PVC/聚氯乙烯及明确危险塑料禁止激光加工，已拒绝生成可发送预览。",
            "material": original,
        }

    # 2) Soft-card family after hazardous plastics are ruled out.
    if _is_soft_card_label(normalized):
        # Plastic-qualified soft card is still not a safe sendable material.
        if _is_ambiguous_plastic(normalized):
            return {
                "decision": DECISION_CLARIFY,
                "reason": "ambiguous_plastic",
                "message": "“塑料软卡”成分不明，请提供明确且可安全加工的材料名称；在明确前不能生成可发送预览。",
                "material": original,
            }
        # User already said paper: stop asking soft-card paper-vs-plastic; library may still reject.
        if _is_paper_qualified_soft_card(normalized):
            return {
                "decision": DECISION_ALLOW,
                "reason": "allowed",
                "message": "",
                "material": original,
            }
        return {
            "decision": DECISION_CLARIFY,
            "reason": "soft_card_ambiguous",
            "message": "“软卡”可能是纸质或塑料，请先说明是纸质软卡还是塑料软卡；在明确前不能生成可发送预览。",
            "material": original,
        }

    # 3) Bare / ambiguous plastic wording without a known-safe qualifier.
    if _is_ambiguous_plastic(normalized):
        return {
            "decision": DECISION_CLARIFY,
            "reason": "ambiguous_plastic",
            "message": "材料仅写“塑料”或成分不明，请提供明确且可安全加工的材料名称；在明确前不能生成可发送预览。",
            "material": original,
        }

    return {
        "decision": DECISION_ALLOW,
        "reason": "allowed",
        "message": "",
        "material": original,
    }


def is_manual_params_confirmed(value: Any) -> bool:
    """True only for explicit truthy confirmation flags."""
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return False
    normalized = str(value).strip().lower()
    return normalized in {"1", "true", "yes", "y", "on", "确认", "confirmed"}


def _is_explicit_false(value: Any) -> bool:
    if value is False:
        return True
    if value is None or value == "":
        return False
    return str(value).strip().lower() in {"0", "false", "no", "n", "off"}


def strip_unconfirmed_manual_params(
    fields: dict | None,
    confirmed: Any = None,
    *,
    keep_explicit_false: bool = False,
) -> dict:
    """Remove manual processing overrides unless manually confirmed.

    When ``confirmed`` is omitted, reads ``manual_params_confirmed`` from fields.
    By default drops a false/absent flag so it cannot pretend to be authoritative.
    Set ``keep_explicit_false=True`` on partial updates so a later merge can clear
    a previously confirmed sticky state before the final strip.
    """
    clean = dict(fields or {})
    flag = confirmed if confirmed is not None else clean.get("manual_params_confirmed")
    if is_manual_params_confirmed(flag):
        clean["manual_params_confirmed"] = True
        return clean
    for key in MANUAL_PARAM_KEYS:
        clean.pop(key, None)
    if keep_explicit_false and _is_explicit_false(flag):
        clean["manual_params_confirmed"] = False
    else:
        clean.pop("manual_params_confirmed", None)
    return clean


# Identity keys that bind a manual confirmation to a specific material/process choice.
MANUAL_PARAM_IDENTITY_KEYS = (
    "material",
    "thickness_mm",
    "laser_mode",
    "engraving_mode",
)


def _identity_token(key: str, value: Any) -> str:
    if value in (None, ""):
        return ""
    if key == "thickness_mm":
        try:
            return f"{float(value):.6f}"
        except (TypeError, ValueError):
            return str(value).strip()
    return str(value).strip().casefold()


def identity_changed(previous: dict | None, updates: dict | None) -> bool:
    """True when material/thickness/mode identity differs between previous and updates."""
    prev = previous or {}
    new = updates or {}
    for key in MANUAL_PARAM_IDENTITY_KEYS:
        if key not in new:
            continue
        if _identity_token(key, prev.get(key)) != _identity_token(key, new.get(key)):
            return True
    return False


def clear_manual_params(fields: dict | None) -> dict:
    """Drop confirmation flag and all manual processing overrides."""
    clean = dict(fields or {})
    for key in MANUAL_PARAM_KEYS:
        clean.pop(key, None)
    clean.pop("manual_params_confirmed", None)
    return clean


def apply_manual_param_policy(
    previous: dict | None,
    updates: dict | None,
    *,
    updates_include_explicit_false: bool = False,
) -> dict:
    """Merge previous workflow input with updates under sticky-confirmation rules.

    - manual_params_confirmed is only for the current identity set.
    - material / thickness_mm / laser_mode / engraving_mode changes clear old
      confirmation and all MANUAL_PARAM_KEYS unless this update itself confirms.
    - Manual fields submitted without confirmation are stripped.
    """
    prev = dict(previous or {})
    clean_updates = dict(updates or {})
    merged = dict(prev)
    merged.update(clean_updates)

    update_confirms = is_manual_params_confirmed(clean_updates.get("manual_params_confirmed"))
    if identity_changed(prev, clean_updates) and not update_confirms:
        merged = clear_manual_params(merged)
        # Keep non-manual fields from updates after clearing.
        for key, value in clean_updates.items():
            if key in MANUAL_PARAM_KEYS or key == "manual_params_confirmed":
                continue
            merged[key] = value
        return merged

    if update_confirms:
        merged["manual_params_confirmed"] = True
        return merged

    # Explicit false or missing confirmation: strip manuals and never persist a
    # false confirmation flag (false is not an authoritative production input).
    del updates_include_explicit_false  # only used by callers for intermediate clean
    return strip_unconfirmed_manual_params(
        merged,
        confirmed=clean_updates.get("manual_params_confirmed", merged.get("manual_params_confirmed")),
        keep_explicit_false=False,
    )
