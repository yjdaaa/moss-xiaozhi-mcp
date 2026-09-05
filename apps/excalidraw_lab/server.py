from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import mimetypes
import os
import re
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from moss_mcp import web_server as laser_web_server
from core import laser_time_estimate
from tools import check_laser_connection_tool, laser_material_calibration_tool, laser_network_grbl_tool, laser_workflow_tool


ROOT_DIR = Path(__file__).resolve().parents[2]
STATIC_DIR = ROOT_DIR / "apps" / "excalidraw_lab" / "web" / "dist"
LAB_OUTPUT_DIR = Path(
    os.environ.get("EXCALIDRAW_LASER_LAB_OUTPUT_DIR", str(ROOT_DIR / "out" / "excalidraw_laser_lab"))
)
LAB_JOB_OUTPUT_DIR = LAB_OUTPUT_DIR / "jobs"
DEFAULT_HOST = os.environ.get("EXCALIDRAW_LASER_LAB_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("EXCALIDRAW_LASER_LAB_PORT", "8777") or 8777)
MAX_JSON_BODY_BYTES = int(os.environ.get("EXCALIDRAW_LASER_LAB_MAX_JSON_BYTES", str(10 * 1024 * 1024)))
MAX_IMAGE_BYTES = int(os.environ.get("EXCALIDRAW_LASER_LAB_MAX_IMAGE_BYTES", str(8 * 1024 * 1024)))
MAX_SCENE_JSON_BYTES = int(os.environ.get("EXCALIDRAW_LASER_LAB_MAX_SCENE_JSON_BYTES", str(4 * 1024 * 1024)))
DATA_URL_RE = re.compile(r"^data:(?P<mime>image/(?:png|jpeg|jpg|webp));base64,(?P<data>.+)$", re.IGNORECASE | re.DOTALL)
MIME_SUFFIXES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
}
LAB_MATERIAL_LIBRARY_FILE = LAB_OUTPUT_DIR / "materials_from_lasergrbl.json"
TASK_TYPE_BY_MODE = {
    ("engrave", "raster"): "engrave_photo",
    ("engrave", "outline"): "engrave_logo",
    ("cut", ""): "cut_contour",
}


def _build_success(result, status=200):
    return {"success": True, "result": result, "status": status}


def _build_failure(message, detail=None, status=400):
    payload = {"success": False, "result": message, "status": status}
    if detail is not None:
        payload["detail"] = detail
    return payload


def _optional_text(payload, key, default=""):
    value = payload.get(key, default)
    if value is None:
        return default
    return str(value).strip()


def _required_text(payload, key):
    value = _optional_text(payload, key)
    if not value:
        return None, f"{key} 不能为空"
    return value, None


def _coerce_float(payload, key, default=0.0):
    value = payload.get(key, default)
    if value is None or value == "":
        return default, None
    try:
        return float(value), None
    except (TypeError, ValueError):
        return None, f"{key} 必须是数字"


def _optional_positive_float(payload, key):
    value = payload.get(key)
    if value is None or value == "":
        return None, None
    number, error = _coerce_float(payload, key)
    if error:
        return None, error
    if number < 0:
        return None, f"{key} 不能小于 0"
    if number == 0:
        return None, None
    return number, None


def _coerce_int(payload, key, default=0):
    value = payload.get(key, default)
    if value is None or value == "":
        return default, None
    try:
        return int(float(value)), None
    except (TypeError, ValueError):
        return None, f"{key} 必须是整数"


def _coerce_bool(payload, key, default=False):
    value = payload.get(key, default)
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on", "确认", "confirmed"}


def _as_dict(value):
    return value if isinstance(value, dict) else {}


def _first_non_empty(*values):
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _first_dict(*values):
    for value in values:
        if isinstance(value, dict) and value:
            return value
    return {}


def _as_number_or_none(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_bool_or_none(value):
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return None


def _unique_text_list(*values):
    items = []
    for value in values:
        if isinstance(value, list):
            candidates = value
        elif value in (None, ""):
            candidates = []
        else:
            candidates = [value]
        for candidate in candidates:
            text = str(candidate or "").strip()
            if text and text not in items:
                items.append(text)
    return items


def _decode_image_data_url(data_url):
    if not data_url:
        return None, None, "image_data_url 不能为空"
    match = DATA_URL_RE.match(str(data_url).strip())
    if not match:
        return None, None, "image_data_url 必须是 PNG/JPEG/WebP 的 base64 data URL"
    mime_type = match.group("mime").lower()
    suffix = MIME_SUFFIXES.get(mime_type)
    if not suffix:
        return None, None, "不支持的图片 MIME 类型"
    try:
        image_bytes = base64.b64decode(match.group("data"), validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        return None, None, f"图片 base64 解析失败: {exc}"
    if not image_bytes:
        return None, None, "图片内容为空"
    if len(image_bytes) > MAX_IMAGE_BYTES:
        return None, None, f"图片过大，最大允许 {MAX_IMAGE_BYTES} 字节"
    magic_error = _validate_image_magic(image_bytes, suffix)
    if magic_error:
        return None, None, magic_error
    return image_bytes, suffix, None


def _validate_image_magic(image_bytes, suffix):
    if suffix == ".png" and image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    if suffix == ".jpg" and image_bytes.startswith(b"\xff\xd8"):
        return None
    if suffix == ".webp" and image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
        return None
    return "图片内容和声明的格式不匹配"


def _safe_timestamp():
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _content_fingerprint_hex(image_bytes):
    return hashlib.sha256(image_bytes).hexdigest().lower()


def _save_uploaded_image(image_bytes, suffix, output_dir=None):
    output_root = Path(output_dir or LAB_OUTPUT_DIR)
    output_root.mkdir(parents=True, exist_ok=True)
    full_digest = _content_fingerprint_hex(image_bytes)
    path = output_root / f"draw_{_safe_timestamp()}_{full_digest[:12]}{suffix}"
    path.write_bytes(image_bytes)
    return path, full_digest


def _save_scene_json(scene_json, image_path):
    if scene_json in (None, ""):
        return None, None
    if isinstance(scene_json, str):
        try:
            parsed = json.loads(scene_json)
        except json.JSONDecodeError as exc:
            return None, f"scene_json 不是合法 JSON: {exc}"
    elif isinstance(scene_json, dict):
        parsed = scene_json
    else:
        return None, "scene_json 必须是对象或 JSON 字符串"
    body = json.dumps(parsed, ensure_ascii=False, indent=2).encode("utf-8")
    if len(body) > MAX_SCENE_JSON_BYTES:
        return None, f"scene_json 过大，最大允许 {MAX_SCENE_JSON_BYTES} 字节"
    path = image_path.with_suffix(".excalidraw.json")
    path.write_bytes(body)
    return path, None


def _default_task_type(mode):
    normalized = str(mode or "").strip().lower()
    if normalized == "raster":
        return "engrave_photo"
    if normalized == "outline":
        return "engrave_logo"
    return ""


def _is_lasergrbl_param_entry(value):
    return isinstance(value, dict) and any(key in value for key in laser_material_calibration_tool.PARAMETER_KEYS)


def _material_group_for_lasergrbl_material(material, aliases):
    tokens = {str(material).strip().lower(), *(str(alias).strip().lower() for alias in aliases or [])}
    if tokens & {"wood", "basswood", "plywood", "balsa", "椴木", "木头", "木板", "木料", "木牌", "木片", "木质", "椴木板"}:
        return "wood_like"
    if tokens & {"paper", "cardboard", "paperboard", "cardstock", "纸", "纸张", "纸板", "卡纸", "瓦楞纸"}:
        return "paper_like"
    if tokens & {"test", "测试", "通用"}:
        return "test"
    if tokens & {"leather", "cowhide", "cowhide_leather", "皮革", "皮料", "牛皮"}:
        return "leather_like"
    return None


def _canonical_material_for_lasergrbl_material(material, aliases):
    group = _material_group_for_lasergrbl_material(material, aliases)
    if group == "wood_like":
        return "wood"
    if group == "paper_like":
        return "paper"
    if group == "leather_like":
        return "cowhide_leather"
    if group == "test":
        return "test"
    return str(material)


def _confidence_for_lasergrbl_params(params):
    source = str((params or {}).get("source") or "").strip().lower()
    if source in {"manual", "verified", "user", "user_library", "user_tested", "calibration"}:
        return "verified"
    if source in {"estimated", "experimental"}:
        return source
    return "library"


def _lasergrbl_records_for_strategy(
    records,
    material,
    aliases,
    thickness_mm,
    task_type,
    mode,
    params,
):
    if not _is_lasergrbl_param_entry(params):
        return
    try:
        power = int(params["laser_max_power"])
        speed = int(params["feed_rate"])
    except (KeyError, TypeError, ValueError):
        return
    material_group = _material_group_for_lasergrbl_material(material, aliases)
    canonical_material = _canonical_material_for_lasergrbl_material(material, aliases)
    record_aliases = [str(alias) for alias in aliases or []]
    if str(material) != canonical_material and str(material) not in record_aliases:
        record_aliases.append(str(material))
    record = {
        "machine_profile_id": "yisu-v1-100x100",
        "material": canonical_material,
        "thickness_mm": float(thickness_mm),
        "task_type": task_type,
        "confidence": _confidence_for_lasergrbl_params(params),
        "mode": mode,
        "speed": speed,
        "power": power,
        "passes": int(params.get("passes") or 1),
        "source": str(params.get("source") or "lasergrbl_materials"),
        "aliases": record_aliases,
        "material_group": material_group,
        "safety_note": str(params.get("notes") or "来自本机 .lasergrbl_materials.json 的材料参数"),
    }
    if "pixel_size_mm" in params:
        record["pixel_size_mm"] = float(params["pixel_size_mm"])
    records.append(record)


def _converted_lasergrbl_material_records(payload):
    if not isinstance(payload, dict) or payload.get("version") != 1:
        return [], "材料库格式不对：需要 version=1"
    materials = payload.get("materials")
    if not isinstance(materials, dict):
        return [], "材料库格式不对：materials 必须是对象"

    records = []
    for material, entry in materials.items():
        if not isinstance(entry, dict):
            continue
        aliases = entry.get("aliases") if isinstance(entry.get("aliases"), list) else []
        thicknesses = entry.get("thicknesses") if isinstance(entry.get("thicknesses"), dict) else {}
        for thickness_mm, modes in thicknesses.items():
            if not isinstance(modes, dict):
                continue
            engrave = modes.get("engrave")
            if _is_lasergrbl_param_entry(engrave):
                _lasergrbl_records_for_strategy(records, material, aliases, thickness_mm, "engrave_photo", "raster", engrave)
            elif isinstance(engrave, dict):
                _lasergrbl_records_for_strategy(
                    records, material, aliases, thickness_mm, "engrave_photo", "raster", engrave.get("raster")
                )
                _lasergrbl_records_for_strategy(
                    records, material, aliases, thickness_mm, "engrave_logo", "outline", engrave.get("outline")
                )
            _lasergrbl_records_for_strategy(records, material, aliases, thickness_mm, "cut_contour", "outline", modes.get("cut"))
    return records, None


def _write_converted_material_library(params_file=None, output_file=None):
    source_path = Path(params_file or laser_material_calibration_tool.MATERIAL_PARAMS_FILE)
    if not source_path.is_file():
        return "", None
    try:
        payload = json.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return "", f"读取本机材料库失败: {exc}"
    records, error = _converted_lasergrbl_material_records(payload)
    if error:
        return "", error
    if not records:
        return "", None
    target = Path(output_file or LAB_MATERIAL_LIBRARY_FILE)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"version": 1, "materials": records}, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        return "", f"写入图片材料库适配文件失败: {exc}"
    return str(target), None


def _draw_prompt(payload):
    prompt = _optional_text(payload, "prompt")
    if prompt:
        return prompt
    mode = _optional_text(payload, "mode", "raster") or "raster"
    return f"hand drawn Excalidraw sketch, laser {mode} engraving"


def _workflow_preview_payload(payload, image_path):
    material, error = _required_text(payload, "material")
    if error:
        return None, error
    thickness_mm, error = _coerce_float(payload, "thickness_mm")
    if error:
        return None, error
    if thickness_mm <= 0:
        return None, "thickness_mm 必须大于 0"
    mode = _optional_text(payload, "mode", "raster") or "raster"
    if mode not in {"auto", "raster", "outline"}:
        return None, "mode 必须是 auto、raster 或 outline"
    width_mm, error = _optional_positive_float(payload, "width_mm")
    if error:
        return None, error
    height_mm, error = _optional_positive_float(payload, "height_mm")
    if error:
        return None, error
    pixel_size_mm, error = _optional_positive_float(payload, "pixel_size_mm")
    if error:
        return None, error
    dither_algorithm = _optional_text(payload, "dither_algorithm")
    raster_scan_direction = _optional_text(payload, "raster_scan_direction")
    raster_output_strategy = _optional_text(payload, "raster_output_strategy")
    network_http_port, error = _coerce_int(payload, "network_http_port", laser_network_grbl_tool.DEFAULT_HTTP_PORT)
    if error:
        return None, error
    network_telnet_port, error = _coerce_int(payload, "network_telnet_port", laser_network_grbl_tool.DEFAULT_TELNET_PORT)
    if error:
        return None, error
    network_timeout, error = _coerce_float(payload, "network_timeout", laser_network_grbl_tool.DEFAULT_TIMEOUT)
    if error:
        return None, error
    vector_simplify_factor = None
    if mode == "outline":
        if "vector_simplify_factor" not in payload or payload.get("vector_simplify_factor") in (None, ""):
            vector_simplify_factor = 3.0
        else:
            try:
                vector_simplify_factor = float(payload.get("vector_simplify_factor"))
            except (TypeError, ValueError):
                return None, "vector_simplify_factor 必须是 0.25 到 8 之间的有限数值"
            if (
                not math.isfinite(vector_simplify_factor)
                or vector_simplify_factor < 0.25
                or vector_simplify_factor > 8.0
            ):
                return None, "vector_simplify_factor 必须满足 0.25 <= value <= 8"
    task_type = _optional_text(payload, "task_type") or _default_task_type(mode)
    material_library, error = _write_converted_material_library()
    if error:
        return None, error
    workflow_payload = {
        "action": "preview",
        "workflow_id": _optional_text(payload, "workflow_id"),
        "source_type": "image",
        "image_file": str(image_path),
        "prompt": _draw_prompt(payload),
        "material": material,
        "thickness_mm": thickness_mm,
        "mode": mode,
        "task_type": task_type,
        "material_library": material_library,
        "output_format": _optional_text(payload, "output_format", "gcode") or "gcode",
        "output_dir": str(LAB_JOB_OUTPUT_DIR),
        "raster_quality_strategy": _optional_text(payload, "raster_quality_strategy", "auto") or "auto",
        "lock_aspect_ratio": _coerce_bool(payload, "lock_aspect_ratio", True),
        "connection_mode": "network",
        "network_host": _optional_text(payload, "network_host"),
        "network_transport": _optional_text(payload, "network_transport", laser_material_calibration_tool.DEFAULT_NETWORK_TRANSPORT)
        or laser_material_calibration_tool.DEFAULT_NETWORK_TRANSPORT,
        "network_http_port": network_http_port,
        "network_telnet_port": network_telnet_port,
        "network_timeout": network_timeout,
        "run_in_background": _coerce_bool(payload, "run_in_background", True),
        "wait_for_response": _coerce_bool(payload, "wait_for_response", True),
    }
    if width_mm is not None:
        workflow_payload["width_mm"] = width_mm
    if height_mm is not None:
        workflow_payload["height_mm"] = height_mm
    if pixel_size_mm is not None:
        workflow_payload["pixel_size_mm"] = pixel_size_mm
    if dither_algorithm:
        workflow_payload["dither_algorithm"] = dither_algorithm
    if raster_scan_direction:
        workflow_payload["raster_scan_direction"] = raster_scan_direction
    if raster_output_strategy:
        workflow_payload["raster_output_strategy"] = raster_output_strategy
    if vector_simplify_factor is not None:
        workflow_payload["vector_simplify_factor"] = vector_simplify_factor
    return workflow_payload, None


def _time_estimate_from_sources(*sources):
    for source in sources:
        source = _as_dict(source)
        estimate = source.get("time_estimate")
        if isinstance(estimate, dict):
            return estimate
        runtime_estimate = _as_dict(source.get("runtime_job")).get("time_estimate")
        if isinstance(runtime_estimate, dict):
            return runtime_estimate
    return {}


def _job_summary_sources(payload):
    payload = _as_dict(payload)
    workflow = _as_dict(payload.get("workflow"))
    artifacts = _as_dict(payload.get("artifacts"))
    workflow_artifacts = _as_dict(workflow.get("artifacts"))
    last_preview = _as_dict(workflow.get("last_preview_result"))
    summary = _first_dict(
        payload.get("summary"),
        artifacts.get("summary"),
        workflow_artifacts.get("summary"),
        last_preview.get("summary"),
    )
    input_payload = _as_dict(payload.get("input"))
    workflow_input = _as_dict(workflow.get("input"))
    return {
        "payload": payload,
        "workflow": workflow,
        "artifacts": artifacts,
        "workflow_artifacts": workflow_artifacts,
        "last_preview": last_preview,
        "summary": summary,
        "input": input_payload,
        "workflow_input": workflow_input,
        "raster": _as_dict(summary.get("raster")),
        "trace": _as_dict(summary.get("trace")),
        "placement": _first_dict(summary.get("placement"), artifacts.get("placement"), payload.get("placement")),
        "safety": _as_dict(summary.get("safety_report")),
        "routing": _as_dict(summary.get("routing")),
        "params": _first_dict(payload.get("params"), last_preview.get("params"), artifacts.get("params")),
        "auto_adjustment": _first_dict(payload.get("auto_adjustment"), last_preview.get("auto_adjustment")),
    }


def _dimension_note(requested_width, requested_height, actual_width, actual_height, scaled):
    size_text = ""
    if actual_width is not None and actual_height is not None:
        size_text = f"最终约 {actual_width:g} x {actual_height:g} mm"
    manual = requested_width is not None or requested_height is not None
    if manual and scaled:
        return f"按指定宽高生成，超过安全范围时已自动缩小；{size_text}".strip("；")
    if manual:
        return f"按指定宽高生成；{size_text}".strip("；")
    if scaled:
        return f"未指定宽高，按画板内容自动计算，并已自动缩小到安全范围；{size_text}".strip("；")
    if size_text:
        return f"未指定宽高，按画板内容自动计算；{size_text}"
    return "未指定宽高，按画板内容自动计算。"


def _auto_adjustment_note(auto_adjustment):
    auto_adjustment = _as_dict(auto_adjustment)
    reason = str(auto_adjustment.get("reason") or "")
    pixel_size = _as_number_or_none(auto_adjustment.get("pixel_size_mm"))
    if reason == "raster_output_too_complex" and pixel_size is not None:
        return f"光栅输出过密，已自动把点距调整到 {pixel_size:g} mm。"
    return ""


def _job_summary_from_payload(payload):
    sources = _job_summary_sources(payload)
    payload = sources["payload"]
    workflow = sources["workflow"]
    artifacts = sources["artifacts"]
    summary = sources["summary"]
    input_payload = sources["input"]
    workflow_input = sources["workflow_input"]
    raster = sources["raster"]
    trace = sources["trace"]
    placement = sources["placement"]
    safety = sources["safety"]
    routing = sources["routing"]
    params = sources["params"]
    auto_adjustment = sources["auto_adjustment"]

    time_estimate = _time_estimate_from_sources(summary, artifacts, payload, workflow, sources["workflow_artifacts"])
    estimated_seconds = _first_non_empty(
        time_estimate.get("estimated_seconds"),
        time_estimate.get("total_seconds"),
    )
    estimated_seconds_number = _as_number_or_none(estimated_seconds)
    estimated_display = (
        laser_time_estimate.format_duration_zh(estimated_seconds_number)
        if estimated_seconds_number is not None and estimated_seconds_number > 0
        else ""
    )
    requested_width = _as_number_or_none(
        _first_non_empty(input_payload.get("width_mm"), workflow_input.get("width_mm"), summary.get("width_mm"))
    )
    requested_height = _as_number_or_none(
        _first_non_empty(input_payload.get("height_mm"), workflow_input.get("height_mm"), summary.get("height_mm"))
    )
    actual_width = _as_number_or_none(
        _first_non_empty(
            summary.get("actual_width_mm"),
            raster.get("width_mm"),
            trace.get("width_mm"),
            placement.get("actual_width_mm"),
            placement.get("final_width_mm"),
        )
    )
    actual_height = _as_number_or_none(
        _first_non_empty(
            summary.get("actual_height_mm"),
            raster.get("height_mm"),
            trace.get("height_mm"),
            placement.get("actual_height_mm"),
            placement.get("final_height_mm"),
        )
    )
    scaled = _as_bool_or_none(
        _first_non_empty(
            placement.get("scaled_to_safe_area"),
            raster.get("scaled"),
            trace.get("scaled"),
            placement.get("scaled"),
        )
    )
    scaled = bool(scaled)
    next_actions = payload.get("next_actions") if isinstance(payload.get("next_actions"), list) else []
    can_send_value = _as_bool_or_none(
        _first_non_empty(summary.get("can_send"), safety.get("can_send"), payload.get("can_send"))
    )
    if can_send_value is None:
        can_send_value = "confirm_send" in next_actions

    return {
        "workflow_id": _first_non_empty(payload.get("workflow_id"), workflow.get("workflow_id")),
        "status": _first_non_empty(payload.get("status"), workflow.get("status")),
        "source_type": _first_non_empty(payload.get("source_type"), workflow.get("source_type")),
        "material": _first_non_empty(summary.get("material"), input_payload.get("material"), workflow_input.get("material")),
        "thickness_mm": _first_non_empty(
            summary.get("thickness_mm"),
            input_payload.get("thickness_mm"),
            workflow_input.get("thickness_mm"),
        ),
        "matched_thickness_mm": _first_non_empty(
            summary.get("matched_thickness_mm"),
            safety.get("matched_thickness_mm"),
        ),
        "match_type": _first_non_empty(summary.get("match_type"), safety.get("match_type")),
        "material_match_policy": _first_non_empty(
            summary.get("material_match_policy"),
            safety.get("material_match_policy"),
            workflow_input.get("material_match_policy"),
        ),
        "send_policy": _first_non_empty(
            summary.get("send_policy"),
            safety.get("send_policy"),
            workflow_input.get("send_policy"),
        ),
        "requires_sample_test": bool(
            _first_non_empty(summary.get("requires_sample_test"), safety.get("requires_sample_test"), True)
        ),
        "mode": _first_non_empty(
            summary.get("mode"),
            summary.get("laser_mode"),
            input_payload.get("mode"),
            workflow_input.get("mode"),
        ),
        "task_type": _first_non_empty(summary.get("task_type"), input_payload.get("task_type"), workflow_input.get("task_type")),
        "requested_width_mm": requested_width,
        "requested_height_mm": requested_height,
        "actual_width_mm": actual_width,
        "actual_height_mm": actual_height,
        "scaled": scaled,
        "scale_factor": _as_number_or_none(_first_non_empty(placement.get("scale_factor"), raster.get("scale_factor"))),
        "size_note": _dimension_note(requested_width, requested_height, actual_width, actual_height, scaled),
        "power": _first_non_empty(summary.get("power"), summary.get("laser_max_power"), params.get("laser_max_power")),
        "speed": _first_non_empty(summary.get("speed"), summary.get("feed_rate"), params.get("feed_rate")),
        "passes": _first_non_empty(summary.get("passes"), params.get("passes")),
        "pixel_size_mm": _first_non_empty(summary.get("pixel_size_mm"), raster.get("pixel_size_mm"), params.get("pixel_size_mm")),
        "estimated_seconds": estimated_seconds_number,
        "estimated_display": estimated_display,
        "time_note": time_estimate.get("note") or "按最终 G-code 的运动距离和进给速度估算，实际时间可能略有差异。",
        "can_send": bool(can_send_value),
        "recommendation_status": _first_non_empty(summary.get("recommendation_status"), safety.get("recommendation_status")),
        "warnings": _unique_text_list(
            summary.get("warnings"),
            safety.get("warnings"),
            routing.get("warnings"),
            _auto_adjustment_note(auto_adjustment),
        ),
        "auto_adjustment": auto_adjustment,
        "message": _first_non_empty(summary.get("message"), safety.get("message"), payload.get("result")),
        "gcode_file": _first_non_empty(artifacts.get("gcode_file"), summary.get("gcode_path"), payload.get("gcode_file")),
    }


def _add_lab_links(result, uploaded_image_path=None, scene_path=None, content_fingerprint=None):
    result = laser_web_server._augment_web_links(result)
    if not result.get("success"):
        return result
    payload = dict(result.get("result") or {})
    web = dict(payload.get("web") or {})
    lab = dict(payload.get("draw_lab") or {})
    lab["job_summary"] = _job_summary_from_payload(payload)
    if content_fingerprint:
        lab["content_fingerprint"] = str(content_fingerprint).lower()
    if uploaded_image_path:
        lab["uploaded_image_file"] = str(uploaded_image_path)
        web["uploaded_image_url"] = laser_web_server._preview_url(uploaded_image_path)
    if scene_path:
        lab["scene_file"] = str(scene_path)
        web["scene_download_url"] = laser_web_server._preview_url(
            scene_path,
            download=True,
            filename=scene_path.name,
        )
    if lab:
        payload["draw_lab"] = lab
    if web:
        payload["web"] = web
    return {**result, "result": payload}


def draw_preview_from_payload(payload, workflow_runner=None):
    image_bytes, suffix, error = _decode_image_data_url(payload.get("image_data_url"))
    if error:
        return _build_failure(error)
    preview_payload, error = _workflow_preview_payload(payload, "<pending>")
    if error:
        return _build_failure(error)
    try:
        image_path, content_fingerprint = _save_uploaded_image(image_bytes, suffix)
        scene_path, scene_error = _save_scene_json(payload.get("scene_json"), image_path)
        if scene_error:
            return _build_failure(scene_error, {"uploaded_image_file": str(image_path)})
        preview_payload["image_file"] = str(image_path)
        # Trusted draw-lab entry pins nearest_engrave policy server-side.
        runner = workflow_runner or laser_workflow_tool.preview_draw_lab_image
        result = runner(**preview_payload)
        return _add_lab_links(
            result,
            uploaded_image_path=image_path,
            scene_path=scene_path,
            content_fingerprint=content_fingerprint,
        )
    except OSError as exc:
        return _build_failure(f"保存画板素材失败: {exc}")


def connection_check_from_payload(payload, checker=None):
    http_port, error = _coerce_int(payload, "network_http_port", laser_network_grbl_tool.DEFAULT_HTTP_PORT)
    if error:
        return _build_failure(error)
    telnet_port, error = _coerce_int(payload, "network_telnet_port", laser_network_grbl_tool.DEFAULT_TELNET_PORT)
    if error:
        return _build_failure(error)
    timeout, error = _coerce_float(payload, "network_timeout", laser_network_grbl_tool.DEFAULT_TIMEOUT)
    if error:
        return _build_failure(error)
    host = _optional_text(payload, "network_host") or _optional_text(payload, "host")
    if not host:
        return _build_failure("设备 host/IP 不能为空")
    runner = checker or check_laser_connection_tool._check_network_connection
    try:
        network_connected, network_detail = runner(
            host=host,
            transport=_optional_text(payload, "network_transport", laser_network_grbl_tool.FORCED_FILE_TRANSPORT)
            or laser_network_grbl_tool.FORCED_FILE_TRANSPORT,
            http_port=http_port,
            telnet_port=telnet_port,
            timeout=timeout,
        )
    except Exception as exc:
        return _build_failure(f"连接检查失败: {exc}", status=500)
    result = {
        "serial_connected": False,
        "network_connected": bool(network_connected),
        "summary": "网络已连接" if network_connected else "网络未连接",
    }
    if _coerce_bool(payload, "include_detail", False):
        result["detail"] = {"network": network_detail}
    return _build_success(result)


def _is_relative_to(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _static_file_for_path(request_path):
    relative = unquote(request_path.lstrip("/")) or "index.html"
    if relative.endswith("/"):
        relative += "index.html"
    candidate = (STATIC_DIR / relative).resolve()
    static_root = STATIC_DIR.resolve()
    if not _is_relative_to(candidate, static_root):
        return None
    if candidate.is_file():
        return candidate
    fallback = static_root / "index.html"
    return fallback if fallback.is_file() else None


class ExcalidrawLaserLabRequestHandler(BaseHTTPRequestHandler):
    server_version = "ExcalidrawLaserLab/0.1"

    def _send_no_cache_headers(self):
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")

    def _send_json(self, payload, status=None):
        status_code = int(status or payload.get("status") or 200)
        body = json.dumps({key: value for key, value in payload.items() if key != "status"}, ensure_ascii=False).encode(
            "utf-8"
        )
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._send_no_cache_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path):
        try:
            body = path.read_bytes()
        except OSError as exc:
            self._send_json(_build_failure(f"读取文件失败: {exc}", status=404), status=404)
            return
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self._send_no_cache_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_build_missing(self):
        body = (
            "<!doctype html><meta charset='utf-8'>"
            "<title>Excalidraw Laser Lab</title>"
            "<body style='font-family: system-ui; padding: 32px'>"
            "<h1>实验前端还没有构建</h1>"
            "<p>请先运行：<code>cd apps/excalidraw_lab/web && npm install && npm run build</code></p>"
            "</body>"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._send_no_cache_headers()
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self, max_bytes=MAX_JSON_BODY_BYTES):
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}, None
        if length > max_bytes:
            return None, f"请求体过大，最大允许 {max_bytes} 字节"
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return None, f"JSON 解析失败: {exc}"
        if not isinstance(payload, dict):
            return None, "JSON 顶层必须是对象"
        return payload, None

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self._send_json(_build_success({"status": "ok", "static_dir": str(STATIC_DIR)}))
            return
        if parsed.path == "/preview":
            query = parse_qs(parsed.query)
            file_path = (query.get("path") or [""])[0]
            download = laser_web_server._query_flag(query, "download")
            download_filename = (query.get("filename") or [""])[0]
            resolved, error = laser_web_server.resolve_preview_file(file_path)
            if error:
                self._send_json(_build_failure(error, status=404), status=404)
                return
            content_type = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
            body = resolved.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            if download:
                self.send_header(
                    "Content-Disposition",
                    laser_web_server._download_content_disposition(resolved, download_filename),
                )
            self._send_no_cache_headers()
            self.end_headers()
            self.wfile.write(body)
            return
        static_file = _static_file_for_path(parsed.path)
        if static_file is None:
            if parsed.path in {"/", "/index.html"}:
                self._send_build_missing()
                return
            self._send_json(_build_failure("未找到路径", status=404), status=404)
            return
        self._send_file(static_file)

    def do_POST(self):
        parsed = urlparse(self.path)
        payload, error = self._read_json_body()
        if error:
            self._send_json(_build_failure(error, status=400), status=400)
            return
        if parsed.path == "/api/draw/preview":
            self._send_json(draw_preview_from_payload(payload))
            return
        if parsed.path == "/api/connection/check":
            self._send_json(connection_check_from_payload(payload))
            return
        if parsed.path == "/api/workflow":
            self._send_json(_add_lab_links(laser_web_server.workflow_action_from_payload(payload)))
            return
        self._send_json(_build_failure("未找到路径", status=404), status=404)

    def log_message(self, format, *args):
        print("%s - %s" % (self.address_string(), format % args))


def create_server(host=DEFAULT_HOST, port=DEFAULT_PORT):
    return ThreadingHTTPServer((host, int(port)), ExcalidrawLaserLabRequestHandler)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Standalone Excalidraw laser lab for previewing image G-code flow.")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Bind host. Use 0.0.0.0 for LAN testing.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Bind port. Defaults to 8777.")
    args = parser.parse_args(argv)

    server = create_server(args.host, args.port)
    print(f"Excalidraw laser lab: http://{args.host}:{args.port}/")
    print(f"Static build dir: {STATIC_DIR}")
    print("Generating previews will not start the laser. Confirmed sends still use existing laser gates.")
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print("Warning: this lab is reachable beyond localhost; keep it on a trusted LAN only.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
