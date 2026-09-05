from __future__ import annotations

import ipaddress
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from core.ai_laser_gcode.ai_assistant import ai_assist_payload, load_ai_provider_config, run_ai_assist
from core.ai_laser_gcode.generator import generate_job
from core.ai_laser_gcode.material_source import (
    IMAGE_SUFFIXES,
    MaterialSourceError,
    MaterialSourceRequest,
    prepare_material_source,
)
from core.ai_laser_gcode.parameters import MATERIAL_ALIASES
from core import laser_execution
from core.laser_runtime.config import get_laser_settings

from tools import laser_grbl_tool, laser_network_grbl_tool


SUPPORTED_CONTRACT_VERSION = 1
SENDABLE_RECOMMENDATION_STATUS = "single_recommendation"
_SETTINGS = get_laser_settings()
DEFAULT_OUTPUT_DIR = str(_SETTINGS.ai_output_dir)
DEFAULT_ASSETS_DIR = str(_SETTINGS.ai_assets_dir)
DEFAULT_MATERIAL_LIBRARY_PATH = _SETTINGS.ai_material_library
DEFAULT_STATE_PATH = _SETTINGS.ai_state_path
DEFAULT_LASER_INPUT_DIR = _SETTINGS.ai_input_dir
AI_IMAGE_DISABLED_MESSAGE = "AI 生图素材首版暂未开放，请先提供本地图片、文字素材或明确图片 URL。"
RASTER_COMPLEXITY_MESSAGES = (
    "Raster output is too complex",
    "Raster output is too large",
)
RASTER_COMPLEXITY_RETRY_PIXEL_SIZES = (0.2, 0.3, 0.5, 1.0)
SUPPORTED_IMAGE_SUFFIXES = set(IMAGE_SUFFIXES) | {".bmp"}
_SECRET_KEY_RE = re.compile(r"(?:api[_-]?key|token|password|passwd|secret|authorization|bearer|credential)", re.IGNORECASE)
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


def _build_success(result, detail=None):
    payload = {"success": True, "result": result}
    if detail is not None:
        payload["detail"] = detail
    return payload


def _build_failure(message, detail=None, speech=None):
    payload = {"success": False, "result": message}
    if detail is not None:
        payload["detail"] = detail
    if speech is not None:
        payload["speech"] = speech
    return payload


def _read_summary(summary_path):
    try:
        with open(summary_path, "r", encoding="utf-8") as summary_file:
            summary = json.load(summary_file)
    except OSError as exc:
        return None, f"无法读取 ai_laser_gcode summary: {exc}"
    except json.JSONDecodeError as exc:
        return None, f"ai_laser_gcode summary 不是合法 JSON: {exc}"
    if not isinstance(summary, dict):
        return None, "ai_laser_gcode summary 格式不对：顶层必须是对象"
    return summary, None


def _summary_projection(summary):
    return {
        "contract_version": summary.get("contract_version"),
        "gcode_path": summary.get("gcode_path"),
        "preview_path": summary.get("preview_path"),
        "processed_preview_path": summary.get("processed_preview_path"),
        "summary_path": summary.get("summary_path"),
        "material": summary.get("material"),
        "thickness_mm": summary.get("thickness_mm"),
        "matched_thickness_mm": summary.get("matched_thickness_mm"),
        "task_type": summary.get("task_type"),
        "mode": summary.get("mode"),
        "laser_mode": summary.get("laser_mode"),
        "output_format": summary.get("output_format"),
        "power": summary.get("power"),
        "feed_rate": summary.get("feed_rate"),
        "speed": summary.get("speed"),
        "passes": summary.get("passes"),
        "parameter_source": summary.get("parameter_source"),
        "confidence": summary.get("confidence"),
        "match_type": summary.get("match_type"),
        "matched_material": summary.get("matched_material"),
        "material_group": summary.get("material_group"),
        "material_match_policy": summary.get("material_match_policy"),
        "send_policy": summary.get("send_policy"),
        "material_warnings": summary.get("material_warnings", []),
        "can_send": summary.get("can_send"),
        "requires_sample_test": summary.get("requires_sample_test"),
        "next_action": summary.get("next_action"),
        "message": summary.get("message"),
        "confirmation_required": summary.get("confirmation_required"),
        "recommendation_status": summary.get("recommendation_status"),
        "safety_report": summary.get("safety_report"),
        "warnings": summary.get("warnings", []),
        "time_estimate": summary.get("time_estimate"),
        "raster": summary.get("raster"),
        "auto_raster_profile": summary.get("auto_raster_profile"),
        "threshold": _projection_strategy_field(summary, "threshold"),
        "resize_strategy": _projection_strategy_field(summary, "resize_strategy"),
        "dither_algorithm": _projection_strategy_field(summary, "dither_algorithm"),
        "files": summary.get("files", {}),
        "candidates": summary.get("candidates", []),
        "inspect_only": summary.get("inspect_only"),
    }


def _projection_strategy_field(summary, key):
    raster = summary.get("raster") if isinstance(summary.get("raster"), dict) else {}
    if key in summary and summary.get(key) is not None and summary.get(key) != "":
        return summary.get(key)
    if key in raster and raster.get(key) is not None and raster.get(key) != "":
        return raster.get(key)
    return summary.get(key)


REQUIRED_RASTER_STRATEGY_FIELDS = ("threshold", "resize_strategy", "dither_algorithm")


def missing_raster_strategy_fields(summary_or_projection):
    """Return strategy fields that are missing/empty (for tests and projection gates)."""
    if not isinstance(summary_or_projection, dict):
        return list(REQUIRED_RASTER_STRATEGY_FIELDS)
    missing = []
    for key in REQUIRED_RASTER_STRATEGY_FIELDS:
        value = _projection_strategy_field(summary_or_projection, key)
        if value is None or value == "":
            missing.append(key)
    return missing


def _validate_summary_for_send(summary):
    if summary.get("contract_version") != SUPPORTED_CONTRACT_VERSION:
        return f"不支持的 ai_laser_gcode summary 契约版本: {summary.get('contract_version')}"
    if not summary.get("gcode_path"):
        return "当前结果没有可发送的 G-code/NC 文件，请先完成候选选择、重新生成或校准。"
    if summary.get("recommendation_status") != SENDABLE_RECOMMENDATION_STATUS:
        return "当前图像推荐状态还不能发送，请先处理候选选择或拒绝原因。"
    safety_report = summary.get("safety_report")
    if isinstance(safety_report, dict) and safety_report.get("can_send") is False:
        return safety_report.get("message") or summary.get("message") or "安全报告显示当前结果不可发送。"
    if not summary.get("can_send"):
        return summary.get("message") or "当前参数未达到可发送状态，请先小样测试或完成校准。"
    return None


def _send_generated_file(
    summary,
    connection_mode,
    confirmed,
    port,
    baudrate,
    host,
    transport,
    http_port,
    telnet_port,
    timeout,
    wait_for_response,
    run_in_background,
    dry_run,
):
    gcode_path = summary.get("gcode_path")
    prepared = laser_grbl_tool.prepare_gcode_file_for_sending(
        gcode_file=gcode_path,
    )
    if not prepared.get("success"):
        return prepared
    prepared_result = prepared["result"]

    # Pass connection_mode through as-is so laser_execution can reject invalid/empty
    # modes. Do not silently fall back to serial.
    return laser_execution.send_file(
        prepared_result,
        connection_mode,
        confirmed=confirmed,
        dry_run=dry_run,
        run_in_background=run_in_background,
        port=port,
        baudrate=baudrate,
        wait_for_response=wait_for_response,
        host=host,
        transport=transport,
        http_port=http_port,
        telnet_port=telnet_port,
        timeout=timeout,
    )


def _state_file_path(state_path=None):
    return Path(state_path) if state_path else DEFAULT_STATE_PATH


def _empty_voice_state():
    return {"recent_material": None, "image_search_candidates": [], "updated_at": None}


def _read_voice_state(state_path=None):
    path = _state_file_path(state_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _empty_voice_state(), None
    except (OSError, json.JSONDecodeError) as exc:
        return _empty_voice_state(), f"语音素材状态不可用，已按空状态处理: {exc}"
    if not isinstance(payload, dict):
        return _empty_voice_state(), "语音素材状态格式不对，已按空状态处理。"
    state = _empty_voice_state()
    state.update({key: payload.get(key) for key in state})
    return state, None


def _write_voice_state(state, state_path=None):
    path = _state_file_path(state_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_sanitize_state_payload(state), ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        return f"语音素材状态保存失败: {exc}"
    return None


def _save_recent_material(material, state_path=None):
    state, warning = _read_voice_state(state_path)
    state["recent_material"] = _sanitize_recent_material(material)
    state["updated_at"] = _utc_now_iso()
    error = _write_voice_state(state, state_path)
    return warning or error


def _save_image_search_candidates(candidates, state_path=None, query=""):
    normalized = _normalize_image_search_candidates(candidates)
    state, warning = _read_voice_state(state_path)
    state["image_search_candidates"] = normalized
    state["image_search_query"] = (query or "").strip()
    state["updated_at"] = _utc_now_iso()
    error = _write_voice_state(state, state_path)
    return normalized, warning or error


def _select_image_search_candidate(selection=None, candidate_id="", candidate_index=None, state_path=None, now=None):
    state, warning = _read_voice_state(state_path)
    candidates = state.get("image_search_candidates") or []
    if not candidates:
        return None, "没有可选择的图片候选，请先说“搜索图片”让工具返回候选列表。"
    if _candidate_context_expired(state, now=now):
        return None, "上次图片候选已过期，请重新搜索图片后再选择。"
    selector = candidate_id or candidate_index or selection
    index = _selection_to_index(selector)
    if index is not None:
        if 1 <= index <= len(candidates):
            return candidates[index - 1], warning
        return None, f"候选序号 {index} 超出范围，请选择 1 到 {len(candidates)}。"
    selector_text = str(selector or "").strip()
    if not selector_text:
        return None, "请说明要选择第几张图片或候选 ID。"
    for candidate in candidates:
        if str(candidate.get("id", "")) == selector_text:
            return candidate, warning
    return None, f"没有找到候选 ID: {selector_text}。"


def _resolve_voice_image_source(
    image_file="",
    image_url="",
    source_type="file",
    text="",
    candidate_selection=None,
    candidate_id="",
    candidate_index=None,
    state_path=None,
    input_dir=None,
):
    if source_type == "text" and (text or "").strip():
        return {"status": "resolved", "source_type": "text", "text": text.strip(), "origin": "explicit_text"}
    if image_file:
        path = Path(image_file).expanduser()
        if path.is_file():
            return {"status": "resolved", "source_type": "file", "path": str(path), "origin": "explicit_path"}
        return {"status": "ask", "message": f"提供的图片路径不存在: {image_file}"}
    if image_url:
        if _is_http_url(image_url):
            return {"status": "resolved", "source_type": "url", "url": image_url.strip(), "origin": "explicit_url"}
        return {"status": "ask", "message": "图片 URL 必须以 http:// 或 https:// 开头。"}
    if source_type == "image-search" and (candidate_selection or candidate_id or candidate_index is not None):
        candidate, error = _select_image_search_candidate(candidate_selection, candidate_id, candidate_index, state_path)
        if error and candidate is None:
            return {"status": "ask", "message": error}
        if candidate.get("path") and Path(candidate["path"]).is_file():
            return {"status": "resolved", "source_type": "file", "path": candidate["path"], "origin": "candidate", "candidate": candidate}
        if candidate.get("url"):
            return {"status": "resolved", "source_type": "url", "url": candidate["url"], "origin": "candidate", "candidate": candidate}
        return {"status": "ask", "message": "选中的候选没有可用图片 URL 或本地路径，请重新搜索。"}
    if source_type == "image-search":
        return {"status": "ask", "message": "图片搜索结果需要先选择候选，请说选第几张或提供候选 ID。"}
    state, warning = _read_voice_state(state_path)
    recent = state.get("recent_material") if isinstance(state.get("recent_material"), dict) else None
    if recent:
        path = recent.get("path")
        if path and Path(path).is_file():
            result = {"status": "resolved", "source_type": "file", "path": path, "origin": "recent", "recent_material": recent}
            if warning:
                result["warning"] = warning
            return result
        url = recent.get("url")
        if url and _is_http_url(url):
            result = {"status": "resolved", "source_type": "url", "url": url, "origin": "recent", "recent_material": recent}
            if warning:
                result["warning"] = warning
            return result
    latest = _latest_image_in_input_dir(input_dir)
    if latest is not None:
        return {"status": "resolved", "source_type": "file", "path": str(latest), "origin": "input_dir_latest"}
    return {"status": "ask", "message": "没有找到可用图片。请提供图片路径、图片 URL、文字素材，或先把图片放入 laser_inputs/。"}


def _sanitize_state_payload(payload):
    if isinstance(payload, dict):
        cleaned = {}
        for key, value in payload.items():
            if _SECRET_KEY_RE.search(str(key)):
                continue
            cleaned[key] = _sanitize_state_payload(value)
        return cleaned
    if isinstance(payload, list):
        return [_sanitize_state_payload(item) for item in payload]
    if isinstance(payload, str) and _is_http_url(payload):
        return _sanitize_url_for_state(payload)
    return payload


def _sanitize_recent_material(material):
    if not isinstance(material, dict):
        return None
    allowed = {"source_type", "path", "url", "title", "id", "origin", "updated_at"}
    cleaned = {key: value for key, value in material.items() if key in allowed and value not in (None, "")}
    if cleaned.get("url"):
        cleaned["url"] = _sanitize_url_for_state(str(cleaned["url"]))
    cleaned["updated_at"] = _utc_now_iso()
    return cleaned


def _sanitize_url_for_state(url):
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    host = parsed.hostname or ""
    try:
        ipaddress.ip_address(host)
        return ""
    except ValueError:
        pass
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def _normalize_image_search_candidates(candidates):
    if isinstance(candidates, str):
        try:
            candidates = json.loads(candidates)
        except json.JSONDecodeError:
            candidates = []
    if isinstance(candidates, dict):
        candidates = candidates.get("candidates", [])
    if not isinstance(candidates, list):
        return []
    normalized = []
    for index, item in enumerate(candidates, start=1):
        if not isinstance(item, dict):
            continue
        url = _sanitize_url_for_state(str(item.get("url") or item.get("image_url") or item.get("source_url") or ""))
        path = str(item.get("path") or item.get("image_file") or "").strip()
        candidate = {
            "index": index,
            "id": str(item.get("id") or item.get("candidate_id") or index),
            "title": str(item.get("title") or item.get("name") or f"候选 {index}"),
        }
        if url:
            candidate["url"] = url
        if path:
            candidate["path"] = path
        for optional_key in ("mode", "reason", "preview_url"):
            value = item.get(optional_key)
            if value not in (None, ""):
                candidate[optional_key] = _sanitize_state_payload(value)
        normalized.append(candidate)
    return normalized


def _candidate_context_expired(state, now=None):
    ttl = _candidate_ttl_seconds()
    if ttl <= 0:
        return False
    updated_at = state.get("updated_at")
    if not isinstance(updated_at, str) or not updated_at:
        return False
    try:
        updated = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    current = now or datetime.now(timezone.utc)
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return (current - updated).total_seconds() > ttl


def _candidate_ttl_seconds():
    return _SETTINGS.ai_candidate_ttl_seconds


def _selection_to_index(selection):
    if selection is None or selection == "":
        return None
    if isinstance(selection, int):
        return selection
    if isinstance(selection, float) and selection.is_integer():
        return int(selection)
    text = str(selection).strip()
    if text.isdigit():
        return int(text)
    match = re.search(r"第\s*(\d+)\s*张", text)
    if match:
        return int(match.group(1))
    return None


def _latest_image_in_input_dir(input_dir=None):
    directory = Path(input_dir) if input_dir else DEFAULT_LASER_INPUT_DIR
    if not directory.is_dir():
        return None
    images = [path for path in directory.iterdir() if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES]
    if not images:
        return None
    return max(images, key=lambda path: path.stat().st_mtime)


def _is_http_url(value):
    return isinstance(value, str) and _URL_RE.match(value.strip()) is not None


def _utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def _has_explicit_material(prompt, material=""):
    if (material or "").strip():
        return True
    lowered = (prompt or "").lower()
    for token in MATERIAL_ALIASES:
        token_lower = token.lower()
        if token_lower.isascii():
            if re.search(rf"\b{re.escape(token_lower)}\b", lowered):
                return True
        elif token_lower in lowered:
            return True
    return False


def _has_explicit_thickness(prompt, thickness_mm=None):
    if thickness_mm is not None:
        return True
    return re.search(r"(?:thickness|厚度)\s*[:=]?\s*\d+(?:\.\d+)?\s*mm", prompt or "", re.IGNORECASE) is not None


def _missing_generation_fields(prompt, material="", thickness_mm=None):
    missing = []
    if not _has_explicit_material(prompt, material):
        missing.append("material")
    if not _has_explicit_thickness(prompt, thickness_mm):
        missing.append("thickness_mm")
    return missing


def _has_explicit_size(prompt):
    return re.search(r"(?:size|尺寸|大小|宽度|宽|width|最大边|边长)\s*[:=]?\s*\d+(?:\.\d+)?\s*mm", prompt or "", re.IGNORECASE) is not None


def _format_mm_value(value):
    number = float(value)
    return f"{number:g}"


def _positive_float_or_none(value):
    if value in (None, ""):
        return None
    number = float(value)
    return number if number > 0 else None


def _prompt_with_explicit_fields(prompt, material="", thickness_mm=None, size_mm=None):
    parts = [(prompt or "").strip()]
    resolved_size = _positive_float_or_none(size_mm)
    if resolved_size is not None and not _has_explicit_size(prompt):
        parts.append(f"size {_format_mm_value(resolved_size)}mm")
    if material:
        parts.append(str(material).strip())
    if thickness_mm is not None and not _has_explicit_thickness(prompt, None):
        parts.append(f"thickness {thickness_mm}mm")
    return " ".join(part for part in parts if part).strip()


def _prepare_source_for_job(
    image_file="",
    image_url="",
    source_type="file",
    text="",
    candidate_selection=None,
    candidate_id="",
    candidate_index=None,
    assets_dir="",
    state_path=None,
    input_dir=None,
    keep_grayscale=False,
    text_style="filled",
):
    resolved = _resolve_voice_image_source(
        image_file=image_file,
        image_url=image_url,
        source_type=source_type,
        text=text,
        candidate_selection=candidate_selection,
        candidate_id=candidate_id,
        candidate_index=candidate_index,
        state_path=state_path,
        input_dir=input_dir,
    )
    if resolved.get("status") != "resolved":
        return None, resolved.get("message") or "无法确定图片来源。", resolved
    asset_dir = Path(assets_dir or DEFAULT_ASSETS_DIR)
    source_info = {"source_type": resolved.get("source_type"), "origin": resolved.get("origin")}
    try:
        if resolved["source_type"] == "text":
            asset = prepare_material_source(
                MaterialSourceRequest(
                    source_type="text",
                    text=resolved.get("text"),
                    assets_dir=asset_dir,
                    keep_grayscale=keep_grayscale,
                    text_style=text_style,
                )
            )
            image_path = asset.processed_path
            source_info.update({"path": str(image_path), "text": resolved.get("text")})
            _save_recent_material({"source_type": "text", "path": str(image_path), "origin": resolved.get("origin")}, state_path)
            return image_path, None, source_info
        if resolved["source_type"] == "url":
            asset = prepare_material_source(
                MaterialSourceRequest(
                    source_type="image-search",
                    url=resolved.get("url"),
                    assets_dir=asset_dir,
                    keep_grayscale=keep_grayscale,
                )
            )
            image_path = asset.processed_path
            source_info.update({"path": str(image_path), "url": _sanitize_url_for_state(resolved.get("url", ""))})
            if resolved.get("candidate"):
                source_info["candidate"] = resolved["candidate"]
            _save_recent_material(
                {
                    "source_type": "image-search",
                    "path": str(image_path),
                    "url": resolved.get("url"),
                    "origin": resolved.get("origin"),
                    "id": (resolved.get("candidate") or {}).get("id"),
                    "title": (resolved.get("candidate") or {}).get("title"),
                },
                state_path,
            )
            return image_path, None, source_info
        image_path = Path(resolved["path"])
        source_info["path"] = str(image_path)
        _save_recent_material({"source_type": "file", "path": str(image_path), "origin": resolved.get("origin")}, state_path)
        return image_path, None, source_info
    except MaterialSourceError as exc:
        return None, str(exc), source_info


def _format_time_estimate(summary):
    estimate = summary.get("time_estimate") if isinstance(summary, dict) else None
    if not isinstance(estimate, dict):
        return "暂未估算"
    minutes = estimate.get("estimated_minutes")
    if isinstance(minutes, (int, float)) and minutes > 0:
        return f"约 {minutes:.1f} 分钟"
    seconds = estimate.get("estimated_seconds") or estimate.get("total_seconds")
    if isinstance(seconds, (int, float)) and seconds > 0:
        if seconds >= 60:
            return f"约 {seconds / 60:.1f} 分钟"
        return f"约 {seconds:.0f} 秒"
    return "暂未估算"


def _format_speech_value(value, fallback="未定"):
    if value is None or value == "":
        return fallback
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:g}"


def _format_mode_label(mode):
    normalized = str(mode or "").strip().lower()
    labels = {
        "engrave": "雕刻",
        "raster": "雕刻",
        "outline": "轮廓雕刻",
        "cut": "切割",
        "auto": "自动",
    }
    return labels.get(normalized, str(mode or "未知模式"))


def _format_passes_suffix(passes):
    try:
        count = int(passes)
    except (TypeError, ValueError):
        return ""
    if count > 1:
        return f"，{count} 遍"
    return ""


def _build_generation_speech(summary, send_status=None, block_reason=None):
    if send_status == "blocked" and block_reason:
        return _build_blocked_send_speech(block_reason)

    mode = _format_mode_label(summary.get("mode"))
    material = summary.get("material") or "材料未明"
    thickness = summary.get("thickness_mm")
    thickness_text = f"{_format_speech_value(thickness)} 毫米" if thickness is not None else "厚度未明"
    power = _format_speech_value(summary.get("power"))
    speed = _format_speech_value(summary.get("feed_rate") or summary.get("speed"))
    duration = _format_time_estimate(summary)
    intro = "图片分析已完成" if summary.get("inspect_only") else "文件已生成"
    sentence = f"{intro}。"
    if duration != "暂未估算":
        sentence = f"{intro}，预计需要{duration}。"
    sentence += f"材料 {material}，厚度 {thickness_text}，模式 {mode}，功率 S{power}，速度 F{speed}{_format_passes_suffix(summary.get('passes'))}。"
    auto_profile_speech = _format_auto_raster_profile_speech(summary.get("auto_raster_profile"))
    if auto_profile_speech:
        sentence += auto_profile_speech
    strategy_speech = _format_raster_strategy_speech(summary)
    if strategy_speech:
        sentence += strategy_speech
    if send_status == "confirmation_required":
        sentence += "确认开始后才发送。"
    elif send_status == "delegated":
        sentence += "已交给发送门禁处理。"
    elif not summary.get("inspect_only"):
        sentence += "确认开始后才发送。"
    return _redact_speech(sentence)


def _format_auto_raster_profile_speech(profile):
    if not isinstance(profile, dict):
        return ""
    selected = profile.get("selected")
    if not isinstance(selected, dict):
        return ""
    direction = selected.get("raster_scan_direction")
    pixel_size = selected.get("pixel_size_mm")
    reason = profile.get("reason") or "综合评分最高"
    if not direction or pixel_size is None:
        return ""
    return f"已自动选择 {direction} + {float(pixel_size):g}mm，因为{reason}。"


def _format_raster_strategy_speech(summary):
    if not isinstance(summary, dict):
        return ""
    threshold = _projection_strategy_field(summary, "threshold")
    resize_strategy = _projection_strategy_field(summary, "resize_strategy")
    dither_algorithm = _projection_strategy_field(summary, "dither_algorithm")
    if threshold is None and not resize_strategy and not dither_algorithm:
        return ""
    parts = []
    if dither_algorithm:
        parts.append(f"抖动 {dither_algorithm}")
    if threshold is not None and threshold != "":
        parts.append(f"阈值 {threshold}")
    if resize_strategy:
        parts.append(f"缩放 {resize_strategy}")
    if not parts:
        return ""
    return "最终策略：" + "，".join(parts) + "。"


def _build_missing_params_speech(missing_fields):
    names = []
    if "material" in missing_fields:
        names.append("材料")
    if "thickness_mm" in missing_fields:
        names.append("厚度")
    missing = "和".join(names) if names else "必要参数"
    return f"还不能生成最终 G-code 或 NC，缺少{missing}。请补充材料和厚度，例如木板厚度 3 毫米。"


def _build_candidate_speech(candidates):
    if not candidates:
        return "没有可用图片候选，请换一个关键词或直接提供图片路径。"
    count = len(candidates)
    return f"找到 {count} 张候选图片。请说选第几张，或提供候选 ID；选择后才会生成 G-code 或 NC。"


def _build_blocked_send_speech(reason):
    return _redact_speech(f"当前文件不能发送到机器：{reason}请先处理候选、补充参数或完成小样测试。")


def _build_source_question_speech(message):
    return _redact_speech(f"还不能继续：{message}请提供图片路径、图片 URL，或说明要刻的文字。")


def _redact_speech(text):
    if not isinstance(text, str):
        return ""
    cleaned = re.sub(r"https?://[^\s，。；,;]+", "图片链接", text)
    cleaned = re.sub(r"[A-Za-z]:[\\/][^\s，。；,;]+", "本地文件", cleaned)
    cleaned = re.sub(r"/[\w./-]{12,}", "本地文件", cleaned)
    cleaned = re.sub(r"(?i)(api[_-]?key|token|password|passwd|secret)\s*[:=]\s*[^\s，。；,;]+", r"\1=[已隐藏]", cleaned)
    return cleaned


def _bundle_summary_path(bundle):
    return str(bundle.summary_path)


def _resolve_material_library_path(material_library=""):
    if material_library:
        return Path(material_library)
    if DEFAULT_MATERIAL_LIBRARY_PATH.exists():
        return DEFAULT_MATERIAL_LIBRARY_PATH
    return None


def default_material_library_path():
    path = _resolve_material_library_path()
    return str(path) if path is not None else ""


def _raster_complexity_retry_sizes(pixel_size_mm=None):
    current = _positive_float_or_none(pixel_size_mm)
    return [value for value in RASTER_COMPLEXITY_RETRY_PIXEL_SIZES if current is None or value > current + 1e-9]


def _is_raster_complexity_error(error):
    error_text = str(error)
    return any(message in error_text for message in RASTER_COMPLEXITY_MESSAGES)


def _run_generate_or_inspect(
    inspect,
    image_file="",
    prompt="",
    source_type="file",
    text="",
    image_url="",
    candidate_selection=None,
    candidate_id="",
    candidate_index=None,
    output_dir="",
    output_format="",
    size_mm=None,
    width_mm=None,
    height_mm=None,
    lock_aspect_ratio=None,
    pixel_size_mm=None,
    mode="",
    material="",
    material_library="",
    thickness_mm=None,
    task_type="",
    state_path=None,
    input_dir=None,
    assets_dir="",
    keep_grayscale=False,
    text_style="filled",
    trace_algorithm="",
    vector_simplify_factor=None,
    fill_strategy="",
    fill_spacing_mm=None,
    dither_algorithm="",
    threshold=None,
    raster_scan_direction="",
    raster_output_strategy="",
    raster_quality_strategy="auto",
    arc_output=None,
    firmware_supports_arc=None,
    arc_tolerance_mm=None,
    ai_assist=False,
    allow_image_upload=False,
    material_match_policy="",
    send_policy="",
):
    if source_type == "ai-image":
        return _build_failure(AI_IMAGE_DISABLED_MESSAGE, speech=AI_IMAGE_DISABLED_MESSAGE)
    prompt_text = _prompt_with_explicit_fields(prompt, material, thickness_mm, size_mm)
    if not inspect:
        missing = _missing_generation_fields(prompt_text, material, thickness_mm)
        if missing:
            speech = _build_missing_params_speech(missing)
            return _build_failure("生成最终 G-code/NC 前需要补充材料和厚度。", {"missing_fields": missing}, speech)
    image_path, error, source_info = _prepare_source_for_job(
        image_file=image_file,
        image_url=image_url,
        source_type=source_type,
        text=text,
        candidate_selection=candidate_selection,
        candidate_id=candidate_id,
        candidate_index=candidate_index,
        assets_dir=assets_dir,
        state_path=state_path,
        input_dir=input_dir,
        keep_grayscale=keep_grayscale,
        text_style=text_style,
    )
    if error:
        return _build_failure(error, {"source": source_info}, _build_source_question_speech(error))
    output_root = Path(output_dir or DEFAULT_OUTPUT_DIR)
    material_library_path = _resolve_material_library_path(material_library)
    auto_adjustment = None

    def run_with_pixel_size(candidate_pixel_size):
        return generate_job(
            image_path,
            prompt_text,
            output_root,
            output_format=output_format or None,
            size_mm=size_mm,
            width_mm=width_mm,
            height_mm=height_mm,
            lock_aspect_ratio=lock_aspect_ratio,
            mode=mode or None,
            trace_algorithm=trace_algorithm or None,
            vector_simplify_factor=vector_simplify_factor,
            fill_strategy=fill_strategy or None,
            fill_spacing_mm=fill_spacing_mm,
            material_library_path=material_library_path,
            material=material or None,
            thickness_mm=thickness_mm,
            task_type=task_type or None,
            inspect=inspect,
            ai_assist=ai_assist,
            allow_image_upload=allow_image_upload,
            arc_output=arc_output,
            firmware_supports_arc=firmware_supports_arc,
            arc_tolerance_mm=arc_tolerance_mm,
            pixel_size_mm=candidate_pixel_size,
            dither_algorithm=dither_algorithm or None,
            threshold=threshold if threshold not in ("", None) else None,
            raster_scan_direction=raster_scan_direction or None,
            raster_output_strategy=raster_output_strategy or None,
            raster_quality_strategy=raster_quality_strategy or None,
            material_match_policy=material_match_policy or None,
            send_policy=send_policy or None,
        )

    try:
        try:
            bundle = run_with_pixel_size(pixel_size_mm)
        except Exception as exc:
            if inspect or not _is_raster_complexity_error(exc):
                raise
            retry_error = exc
            for retry_pixel_size in _raster_complexity_retry_sizes(pixel_size_mm):
                try:
                    bundle = run_with_pixel_size(retry_pixel_size)
                    auto_adjustment = {
                        "reason": "raster_output_too_complex",
                        "pixel_size_mm": retry_pixel_size,
                    }
                    break
                except Exception as candidate_error:
                    retry_error = candidate_error
            else:
                raise retry_error
    except Exception as exc:
        message = f"ai_laser_gcode 生成失败: {exc}"
        return _build_failure(message, {"source": source_info}, _redact_speech(f"生成失败：{exc}。请检查图片、材料、厚度和模式参数。"))
    summary, error = _read_summary(_bundle_summary_path(bundle))
    if error:
        return _build_failure(error, {"summary_path": _bundle_summary_path(bundle), "source": source_info})
    projection = _summary_projection(summary)
    result = {
        "action": "inspect" if inspect else "generate",
        "summary": projection,
        "summary_path": _bundle_summary_path(bundle),
        "source": source_info,
        "send_status": "not_requested",
    }
    if auto_adjustment:
        result["auto_adjustment"] = auto_adjustment
    if projection.get("recommendation_status") == "candidate_selection_required" or projection.get("candidates"):
        result["speech"] = _build_candidate_speech(projection.get("candidates") or [])
    else:
        result["speech"] = _build_generation_speech(projection)
        if auto_adjustment:
            result["speech"] += f" 图片太复杂，已自动把像素步距调到 {auto_adjustment['pixel_size_mm']:g}mm。"
    return _build_success(result)


def _run_send_generated(
    summary_path="",
    connection_mode="serial",
    confirmed=False,
    port="",
    baudrate=laser_grbl_tool.DEFAULT_BAUDRATE,
    host="",
    transport="telnet",
    http_port=laser_network_grbl_tool.DEFAULT_HTTP_PORT,
    telnet_port=laser_network_grbl_tool.DEFAULT_TELNET_PORT,
    timeout=laser_network_grbl_tool.DEFAULT_TIMEOUT,
    wait_for_response=True,
    run_in_background=True,
    dry_run=False,
):
    if not summary_path:
        return _build_failure("请提供 summary_path 才能发送已生成文件。", speech="请先生成文件，或提供已有 summary 路径。")
    summary, error = _read_summary(summary_path)
    if error:
        return _build_failure(error, {"summary_path": summary_path}, _redact_speech(error))
    projection = _summary_projection(summary)
    result = {"action": "send_generated", "summary": projection, "summary_path": summary_path, "send_status": "not_requested"}
    send_error = _validate_summary_for_send(summary)
    if send_error:
        result["send_status"] = "blocked"
        result["send_block_reason"] = send_error
        result["speech"] = _build_blocked_send_speech(send_error)
        return _build_success(result)
    if not confirmed and not dry_run:
        result["send_status"] = "confirmation_required"
        result["send_block_reason"] = "文件已生成且参数可进入确认流程；必须 confirmed=true 才会调用 MCP 发送门禁。"
        result["speech"] = _build_generation_speech(projection, send_status="confirmation_required")
        return _build_success(result)
    send_result = _send_generated_file(
        summary,
        connection_mode,
        confirmed,
        port,
        baudrate,
        host,
        transport,
        http_port,
        telnet_port,
        timeout,
        wait_for_response,
        run_in_background,
        dry_run,
    )
    result["send_status"] = "delegated"
    result["send_result"] = send_result
    result["speech"] = _build_generation_speech(projection, send_status="delegated")
    return _build_success(result)


def _run_image_search(candidates=None, query="", image_url="", state_path=None):
    raw_candidates = candidates
    if raw_candidates in (None, "") and image_url:
        raw_candidates = [{"id": "url-1", "title": query or "图片 URL 候选", "url": image_url}]
    normalized, warning = _save_image_search_candidates(raw_candidates or [], state_path, query)
    result = {
        "action": "image_search",
        "query": query,
        "candidates": normalized,
        "candidate_count": len(normalized),
        "speech": _build_candidate_speech(normalized),
    }
    if warning:
        result["warning"] = warning
    return _build_success(result)


def _run_analyze_image(
    image_file="",
    prompt="",
    source_type="file",
    text="",
    image_url="",
    candidate_selection=None,
    candidate_id="",
    candidate_index=None,
    state_path=None,
    input_dir=None,
    assets_dir="",
    keep_grayscale=False,
    text_style="filled",
    allow_image_upload=False,
):
    if source_type == "ai-image":
        return _build_failure(AI_IMAGE_DISABLED_MESSAGE, speech=AI_IMAGE_DISABLED_MESSAGE)
    image_path, error, source_info = _prepare_source_for_job(
        image_file=image_file,
        image_url=image_url,
        source_type=source_type,
        text=text,
        candidate_selection=candidate_selection,
        candidate_id=candidate_id,
        candidate_index=candidate_index,
        assets_dir=assets_dir,
        state_path=state_path,
        input_dir=input_dir,
        keep_grayscale=keep_grayscale,
        text_style=text_style,
    )
    if error:
        return _build_failure(error, {"source": source_info}, _build_source_question_speech(error))
    analysis = run_ai_assist(
        image_path,
        prompt or "请分析这张图片适合 raster 还是 outline。",
        enabled=True,
        allow_image_upload=allow_image_upload,
        config=load_ai_provider_config(),
    )
    result = {
        "action": "analyze_image",
        "source": source_info,
        "analysis": ai_assist_payload(analysis),
        "speech": "图片分析已完成。AI 建议只用于理解图片，不能替代材料库、安全检查、预览确认和用户确认。",
    }
    return _build_success(result)


def _run_test_ai_provider():
    config = load_ai_provider_config()
    configured = config is not None
    result = {
        "action": "test_ai_provider",
        "configured": configured,
        "provider": config.base_url if config else None,
        "model": config.model if config else None,
        "speech": "AI 分析配置已填写。" if configured else "AI 分析配置不完整；不影响本地图片转 G-code 生成。",
    }
    return _build_success(result)


def run_ai_laser_gcode_action(action="generate", **kwargs):
    normalized_action = (action or "generate").strip().lower().replace("-", "_")
    if normalized_action == "generate" and kwargs.get("summary_path") and not any(
        kwargs.get(key) for key in ("image_file", "image_url", "text", "candidate_selection", "candidate_id", "candidate_index")
    ):
        normalized_action = "send_generated"
    if normalized_action == "generate":
        return _run_generate_or_inspect(False, **_generation_kwargs(kwargs))
    if normalized_action == "inspect":
        return _run_generate_or_inspect(True, **_generation_kwargs(kwargs))
    if normalized_action == "send_generated":
        return _run_send_generated(**_send_kwargs(kwargs))
    if normalized_action == "image_search":
        return _run_image_search(
            candidates=kwargs.get("candidates"),
            query=kwargs.get("query", "") or kwargs.get("prompt", ""),
            image_url=kwargs.get("image_url", ""),
            state_path=kwargs.get("state_path") or None,
        )
    if normalized_action == "analyze_image":
        return _run_analyze_image(**_analyze_kwargs(kwargs))
    if normalized_action == "test_ai_provider":
        return _run_test_ai_provider()
    return _build_failure(f"不支持的 ai_laser_gcode action: {action}", speech="不支持这个图片转 G-code 动作，请选择生成、检查、搜索、分析或发送。")


def _generation_kwargs(kwargs):
    keys = {
        "image_file",
        "prompt",
        "source_type",
        "text",
        "image_url",
        "candidate_selection",
        "candidate_id",
        "candidate_index",
        "output_dir",
        "output_format",
        "size_mm",
        "width_mm",
        "height_mm",
        "lock_aspect_ratio",
        "pixel_size_mm",
        "mode",
        "material",
        "material_library",
        "thickness_mm",
        "task_type",
        "state_path",
        "input_dir",
        "assets_dir",
        "keep_grayscale",
        "text_style",
        "trace_algorithm",
        "vector_simplify_factor",
        "fill_strategy",
        "fill_spacing_mm",
        "dither_algorithm",
        "threshold",
        "raster_scan_direction",
        "raster_output_strategy",
        "raster_quality_strategy",
        "arc_output",
        "firmware_supports_arc",
        "arc_tolerance_mm",
        "ai_assist",
        "allow_image_upload",
        "material_match_policy",
        "send_policy",
    }
    return {key: kwargs.get(key) for key in keys if key in kwargs}


def _analyze_kwargs(kwargs):
    keys = {
        "image_file",
        "prompt",
        "source_type",
        "text",
        "image_url",
        "candidate_selection",
        "candidate_id",
        "candidate_index",
        "state_path",
        "input_dir",
        "assets_dir",
        "keep_grayscale",
        "text_style",
        "allow_image_upload",
    }
    return {key: kwargs.get(key) for key in keys if key in kwargs}


def _send_kwargs(kwargs):
    keys = {
        "summary_path",
        "connection_mode",
        "confirmed",
        "port",
        "baudrate",
        "host",
        "transport",
        "http_port",
        "telnet_port",
        "timeout",
        "wait_for_response",
        "run_in_background",
        "dry_run",
    }
    return {key: kwargs.get(key) for key in keys if key in kwargs}


def generate_ai_laser_gcode_job(
    image_file="",
    prompt="",
    action="generate",
    source_type="file",
    text="",
    image_url="",
    candidate_selection=None,
    candidate_id="",
    candidate_index=None,
    candidates=None,
    query="",
    output_dir="",
    output_format="",
    size_mm=None,
    width_mm=None,
    height_mm=None,
    lock_aspect_ratio=None,
    pixel_size_mm=None,
    mode="",
    material="",
    material_library="",
    thickness_mm=None,
    task_type="",
    inspect=False,
    summary_path="",
    command=None,
    command_timeout=60,
    connection_mode="serial",
    confirmed=False,
    port="",
    baudrate=laser_grbl_tool.DEFAULT_BAUDRATE,
    host="",
    transport="telnet",
    http_port=laser_network_grbl_tool.DEFAULT_HTTP_PORT,
    telnet_port=laser_network_grbl_tool.DEFAULT_TELNET_PORT,
    timeout=laser_network_grbl_tool.DEFAULT_TIMEOUT,
    wait_for_response=True,
    run_in_background=True,
    dry_run=False,
    state_path=None,
    input_dir=None,
    assets_dir="",
    keep_grayscale=False,
    text_style="filled",
    trace_algorithm="",
    vector_simplify_factor=None,
    fill_strategy="",
    fill_spacing_mm=None,
    dither_algorithm="",
    threshold=None,
    raster_scan_direction="",
    raster_output_strategy="",
    raster_quality_strategy="auto",
    arc_output=None,
    firmware_supports_arc=None,
    arc_tolerance_mm=None,
    ai_assist=False,
    allow_image_upload=False,
    material_match_policy="",
    send_policy="",
):
    selected_action = "inspect" if inspect and action == "generate" else action
    return run_ai_laser_gcode_action(
        action=selected_action,
        image_file=image_file,
        prompt=prompt,
        source_type=source_type,
        text=text,
        image_url=image_url,
        candidate_selection=candidate_selection,
        candidate_id=candidate_id,
        candidate_index=candidate_index,
        candidates=candidates,
        query=query,
        output_dir=output_dir,
        output_format=output_format,
        size_mm=size_mm,
        width_mm=width_mm,
        height_mm=height_mm,
        lock_aspect_ratio=lock_aspect_ratio,
        pixel_size_mm=pixel_size_mm,
        mode=mode,
        material=material,
        material_library=material_library,
        thickness_mm=thickness_mm,
        task_type=task_type,
        summary_path=summary_path,
        connection_mode=connection_mode,
        confirmed=confirmed,
        port=port,
        baudrate=baudrate,
        host=host,
        transport=transport,
        http_port=http_port,
        telnet_port=telnet_port,
        timeout=timeout,
        wait_for_response=wait_for_response,
        run_in_background=run_in_background,
        dry_run=dry_run,
        state_path=state_path,
        material_match_policy=material_match_policy,
        send_policy=send_policy,
        input_dir=input_dir,
        assets_dir=assets_dir,
        keep_grayscale=keep_grayscale,
        text_style=text_style,
        trace_algorithm=trace_algorithm,
        vector_simplify_factor=vector_simplify_factor,
        fill_strategy=fill_strategy,
        fill_spacing_mm=fill_spacing_mm,
        dither_algorithm=dither_algorithm,
        threshold=threshold,
        raster_scan_direction=raster_scan_direction,
        raster_output_strategy=raster_output_strategy,
        raster_quality_strategy=raster_quality_strategy,
        arc_output=arc_output,
        firmware_supports_arc=firmware_supports_arc,
        arc_tolerance_mm=arc_tolerance_mm,
        ai_assist=ai_assist,
        allow_image_upload=allow_image_upload,
    )


def register_tool(mcp):
    @mcp.tool()
    def ai_laser_gcode_tool(
        image_file: str = "",
        prompt: str = "",
        action: str = "generate",
        source_type: str = "file",
        text: str = "",
        image_url: str = "",
        candidate_selection: str = "",
        candidate_id: str = "",
        candidate_index: int = None,
        candidates: str = "",
        query: str = "",
        output_dir: str = "",
        output_format: str = "",
        size_mm: float = None,
        width_mm: float = None,
        height_mm: float = None,
        pixel_size_mm: float = None,
        mode: str = "",
        material: str = "",
        material_library: str = "",
        thickness_mm: float = None,
        task_type: str = "",
        inspect: bool = False,
        summary_path: str = "",
        connection_mode: str = "serial",
        confirmed: bool = False,
        port: str = "",
        baudrate: int = laser_grbl_tool.DEFAULT_BAUDRATE,
        host: str = "",
        transport: str = "telnet",
        http_port: int = laser_network_grbl_tool.DEFAULT_HTTP_PORT,
        telnet_port: int = laser_network_grbl_tool.DEFAULT_TELNET_PORT,
        timeout: float = laser_network_grbl_tool.DEFAULT_TIMEOUT,
        wait_for_response: bool = True,
        run_in_background: bool = True,
        dry_run: bool = False,
        raster_quality_strategy: str = "auto",
    ) -> dict:
        """
        统一图片/文字素材转 G-code/NC 工具。生成和检查只落盘，不直接启动机器；
        发送已有 summary 时继续复用串口/网络 send_file 的确认门禁。
        """
        return generate_ai_laser_gcode_job(
            image_file=image_file,
            prompt=prompt,
            action=action,
            source_type=source_type,
            text=text,
            image_url=image_url,
            candidate_selection=candidate_selection,
            candidate_id=candidate_id,
            candidate_index=candidate_index,
            candidates=candidates,
            query=query,
            output_dir=output_dir,
            output_format=output_format,
            size_mm=size_mm,
            width_mm=width_mm,
            height_mm=height_mm,
            pixel_size_mm=pixel_size_mm,
            mode=mode,
            material=material,
            material_library=material_library,
            thickness_mm=thickness_mm,
            task_type=task_type,
            inspect=inspect,
            summary_path=summary_path,
            connection_mode=connection_mode,
            confirmed=confirmed,
            port=port,
            baudrate=baudrate,
            host=host,
            transport=transport,
            http_port=http_port,
            telnet_port=telnet_port,
            timeout=timeout,
            wait_for_response=wait_for_response,
            run_in_background=run_in_background,
            dry_run=dry_run,
            raster_quality_strategy=raster_quality_strategy,
        )
