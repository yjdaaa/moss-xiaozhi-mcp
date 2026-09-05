from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import mimetypes
import os
import re
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from core import laser_execution, laser_material_safety, laser_time_estimate
from core.laser_runtime.config import get_laser_settings
from core.ai_laser_gcode.models import TraceResult
from core.ai_laser_gcode.preview import write_preview_png
from tools import (
    check_laser_connection_tool,
    laser_grbl_tool,
    laser_material_calibration_tool,
    laser_network_grbl_tool,
    laser_workflow_tool,
    text_image_tool,
    text_laser_task_tool,
)


ROOT_DIR = Path(__file__).resolve().parents[1]
RUNTIME_DIR = ROOT_DIR / ".runtime"
DEFAULT_WEB_HOST = os.environ.get("LASER_WEB_HOST", "127.0.0.1")
DEFAULT_WEB_PORT = int(os.environ.get("LASER_WEB_PORT", "8766") or 8766)
GCODE_PREVIEW_DIR = ROOT_DIR / "out" / "web_gcode_previews"
MAX_JSON_BODY_BYTES = 64 * 1024
MAX_DRAW_JSON_BODY_BYTES = int(os.environ.get("LASER_WEB_DRAW_MAX_JSON_BYTES", str(10 * 1024 * 1024)))
MAX_DRAW_IMAGE_BYTES = int(os.environ.get("LASER_WEB_DRAW_MAX_IMAGE_BYTES", str(8 * 1024 * 1024)))
MAX_DRAW_SCENE_JSON_BYTES = int(os.environ.get("LASER_WEB_DRAW_MAX_SCENE_JSON_BYTES", str(4 * 1024 * 1024)))
MAX_UPLOAD_BODY_BYTES = 20 * 1024 * 1024
UPLOAD_DIR = RUNTIME_DIR / "laser_web_uploads"
DRAW_OUTPUT_DIR = Path(os.environ.get("LASER_WEB_DRAW_OUTPUT_DIR", str(ROOT_DIR / "out" / "web_draw_lab")))
DRAW_JOB_OUTPUT_DIR = DRAW_OUTPUT_DIR / "jobs"
DRAW_MATERIAL_LIBRARY_FILE = DRAW_OUTPUT_DIR / "materials_from_lasergrbl.json"
DRAW_STATIC_DIR = ROOT_DIR / "apps" / "excalidraw_lab" / "web" / "dist"
MAIN_UI_STATIC_DIR = ROOT_DIR / "apps" / "laser_web" / "ui"
ALLOWED_PREVIEW_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".gcode", ".nc", ".json"}
ALLOWED_UPLOAD_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".gcode", ".nc"}
UNSAFE_DOWNLOAD_FILENAME_CHARS = set('<>:"/\\|?*')
DRAW_DATA_URL_RE = re.compile(r"^data:(?P<mime>image/(?:png|jpeg|jpg|webp));base64,(?P<data>.+)$", re.IGNORECASE | re.DOTALL)
DRAW_MIME_SUFFIXES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
}


def _build_success(result, status=200):
    return {"success": True, "result": result, "status": status}


def _build_failure(message, detail=None, status=400):
    payload = {"success": False, "result": message, "status": status}
    if detail is not None:
        payload["detail"] = detail
    return payload


def _coerce_bool(payload, key, default=False):
    value = payload.get(key, default)
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return default
    normalized = str(value).strip().lower()
    return normalized in {"1", "true", "yes", "y", "on", "确认", "confirmed"}


def _coerce_float(payload, key, default=0.0):
    value = payload.get(key, default)
    if value is None or value == "":
        return default, None
    try:
        return float(value), None
    except (TypeError, ValueError):
        return None, f"{key} 必须是数字"


def _coerce_int(payload, key, default=0):
    value = payload.get(key, default)
    if value is None or value == "":
        return default, None
    try:
        return int(float(value)), None
    except (TypeError, ValueError):
        return None, f"{key} 必须是整数"


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


def _required_text(payload, key):
    value = str(payload.get(key) or "").strip()
    if not value:
        return None, f"{key} 不能为空"
    return value, None


def _optional_text(payload, key, default=""):
    value = payload.get(key, default)
    if value is None:
        return default
    return str(value).strip()


def _preview_url(path, download=False, filename=""):
    if not path:
        return ""
    url = "/preview?path=" + quote(str(path), safe="")
    if download:
        url += "&download=1"
        if filename:
            url += "&filename=" + quote(str(filename), safe="")
    return url


def _query_flag(query, key):
    value = (query.get(key) or [""])[0]
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on", "确认", "confirmed"}


def _safe_download_filename(name, fallback="download"):
    fallback_name = str(fallback or "download").replace("\\", "/").split("/")[-1].strip() or "download"
    raw_name = str(name or "").replace("\\", "/").split("/")[-1].strip() or fallback_name
    cleaned = "".join(
        "_" if character in UNSAFE_DOWNLOAD_FILENAME_CHARS or ord(character) < 32 else character
        for character in raw_name
    ).strip(" .")
    if cleaned:
        return cleaned
    return "".join(
        "_" if character in UNSAFE_DOWNLOAD_FILENAME_CHARS or ord(character) < 32 else character
        for character in fallback_name
    ).strip(" .") or "download"


def _safe_download_stem(value, fallback="text", max_chars=24):
    raw = re.sub(r"\s+", "_", str(value or "").strip())
    cleaned = "".join(
        "_" if character in UNSAFE_DOWNLOAD_FILENAME_CHARS or ord(character) < 32 else character
        for character in raw
    ).strip(" ._")
    if not cleaned:
        return fallback
    return cleaned[:max_chars]


def _download_content_disposition(path, filename=""):
    fallback = Path(path).name or "download"
    filename = _safe_download_filename(filename or fallback, fallback)
    ascii_name = filename.encode("ascii", errors="ignore").decode("ascii") or "download"
    ascii_name = ascii_name.replace("\\", "_").replace('"', "_")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename, safe='')}"


def _is_relative_to(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _allowed_file_roots():
    roots = [
        Path(text_laser_task_tool.TEXT_LASER_TASKS_DIR),
        RUNTIME_DIR / "lasergrbl_text_tasks",
        RUNTIME_DIR / "generated_images",
        ROOT_DIR / "out",
        ROOT_DIR / "generated_assets",
        UPLOAD_DIR,
        DRAW_OUTPUT_DIR,
        Path(laser_material_calibration_tool.CALIBRATION_DIR),
        Path(laser_workflow_tool.DEFAULT_WORKFLOWS_DIR),
    ]
    resolved = []
    for root in roots:
        root_path = root if root.is_absolute() else ROOT_DIR / root
        try:
            resolved.append(root_path.resolve())
        except OSError:
            continue
    return resolved


def resolve_preview_file(path):
    if not path:
        return None, "缺少 path"
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = ROOT_DIR / candidate
    try:
        resolved = candidate.resolve()
    except OSError as exc:
        return None, f"文件路径不可用: {exc}"
    if resolved.suffix.lower() not in ALLOWED_PREVIEW_SUFFIXES:
        return None, "不支持预览该文件类型"
    allowed = any(_is_relative_to(resolved, root) for root in _allowed_file_roots())
    if not allowed:
        return None, "只能预览本项目生成的激光任务文件"
    if not resolved.is_file():
        return None, "文件不存在"
    return resolved, None


def _draw_static_file_for_path(request_path):
    request_path = unquote(str(request_path or ""))
    if request_path in {"/draw", "/draw/"}:
        relative = "index.html"
    elif request_path.startswith("/draw/"):
        relative = request_path[len("/draw/") :] or "index.html"
    elif request_path.startswith("/assets/"):
        relative = request_path.lstrip("/")
    else:
        return None
    static_root = Path(DRAW_STATIC_DIR).resolve()
    try:
        candidate = (static_root / relative).resolve()
    except OSError:
        return None
    if not _is_relative_to(candidate, static_root):
        return None
    if candidate.is_file():
        return candidate
    index_file = static_root / "index.html"
    if request_path.startswith("/draw/") and index_file.is_file():
        return index_file
    return None


def _safe_upload_filename(name):
    return _safe_download_filename(name, "upload")


def save_uploaded_file_from_payload(payload, upload_dir=UPLOAD_DIR):
    filename = _safe_upload_filename(payload.get("filename") or "")
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_UPLOAD_SUFFIXES:
        return _build_failure("不支持上传该文件类型", {"filename": filename, "allowed": sorted(ALLOWED_UPLOAD_SUFFIXES)})
    raw_base64 = str(payload.get("content_base64") or "")
    if not raw_base64:
        return _build_failure("上传内容为空")
    if "," in raw_base64 and raw_base64.split(",", 1)[0].lower().startswith("data:"):
        raw_base64 = raw_base64.split(",", 1)[1]
    try:
        content = base64.b64decode(raw_base64, validate=True)
    except (ValueError, TypeError) as exc:
        return _build_failure(f"上传内容不是合法 base64: {exc}")
    if not content:
        return _build_failure("上传文件为空")
    upload_dir = Path(upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(filename).stem or "upload"
    digest = hashlib.sha1(content).hexdigest()[:10]
    saved_name = f"{int(time.time())}_{digest}_{stem[:32]}{suffix}"
    target = (upload_dir / saved_name).resolve()
    upload_root = upload_dir.resolve()
    if not _is_relative_to(target, upload_root):
        return _build_failure("上传文件名不安全")
    target.write_bytes(content)
    return _build_success(
        {
            "file_path": str(target),
            "filename": filename,
            "size": len(content),
            "suffix": suffix,
            "preview_url": _preview_url(str(target)),
        }
    )


def render_gcode_preview_image(gcode_file, output_dir=None):
    if not gcode_file:
        return None
    candidate = Path(gcode_file).expanduser()
    if not candidate.is_absolute():
        candidate = ROOT_DIR / candidate
    try:
        resolved = candidate.resolve()
        stat = resolved.stat()
    except OSError:
        return None
    if resolved.suffix.lower() not in {".gcode", ".nc"}:
        return None

    preview_dir = Path(output_dir or GCODE_PREVIEW_DIR)
    key = f"v3-centered-yflip-arcs|{resolved}|{stat.st_size}|{stat.st_mtime_ns}".encode("utf-8", errors="replace")
    output_path = preview_dir / f"gcode-{hashlib.sha256(key).hexdigest()[:16]}.png"
    if output_path.is_file():
        return str(output_path)

    paths = _extract_gcode_preview_paths(resolved)
    if not paths:
        return None
    min_x = min(x for path in paths for x, _ in path)
    max_x = max(x for path in paths for x, _ in path)
    min_y = min(y for path in paths for _, y in path)
    max_y = max(y for path in paths for _, y in path)
    width_mm = max(max_x - min_x, 1.0)
    height_mm = max(max_y - min_y, 1.0)
    canvas_mm = max(width_mm, height_mm)
    x_offset_mm = (canvas_mm - width_mm) / 2
    y_offset_mm = (canvas_mm - height_mm) / 2
    shifted_paths = [
        [(x - min_x + x_offset_mm, max_y - y + y_offset_mm) for x, y in path]
        for path in paths
    ]
    trace = TraceResult(
        paths=shifted_paths,
        width_mm=width_mm,
        height_mm=height_mm,
        contour_count=len(shifted_paths),
        point_count=sum(len(path) for path in shifted_paths),
    )
    try:
        preview_dir.mkdir(parents=True, exist_ok=True)
        write_preview_png(trace, output_path, size_px=512)
    except OSError:
        return None
    return str(output_path)


def _extract_gcode_preview_paths(gcode_file):
    x_coord = 0.0
    y_coord = 0.0
    wco_x = 0.0
    wco_y = 0.0
    unit_scale = 1.0
    absolute = True
    motion_mode = 0
    spindle_mode = 5
    spindle_power = 0.0
    burning_segments = []
    motion_segments = []

    try:
        with open(gcode_file, "r", encoding="utf-8", errors="replace") as file:
            lines = file.readlines()
    except OSError:
        return []

    for raw_line in lines:
        words = laser_time_estimate._parse_gcode_words(raw_line)
        if not words:
            continue
        previous_x, previous_y = x_coord, y_coord
        arc_values = {}
        for letter, value in words:
            if letter == "G":
                code = int(value)
                if code == 20:
                    unit_scale = 25.4
                elif code == 21:
                    unit_scale = 1.0
                elif code in {0, 1, 2, 3}:
                    motion_mode = code
                elif code == 90:
                    absolute = True
                elif code == 91:
                    absolute = False
            elif letter == "M":
                code = int(value)
                if code in {3, 4, 5}:
                    spindle_mode = code
            elif letter == "S":
                spindle_power = value
            elif letter in {"I", "J", "R"}:
                arc_values[letter] = value * unit_scale

        if laser_time_estimate._has_word(words, "G", 92):
            if laser_time_estimate._has_letter(words, "X"):
                wco_x = x_coord - laser_time_estimate._word_value(words, "X") * unit_scale
            if laser_time_estimate._has_letter(words, "Y"):
                wco_y = y_coord - laser_time_estimate._word_value(words, "Y") * unit_scale
            continue

        has_motion_target = (
            laser_time_estimate._has_letter(words, "X")
            or laser_time_estimate._has_letter(words, "Y")
            or (motion_mode in {2, 3} and bool(arc_values))
        )
        if not has_motion_target:
            continue
        x_coord = _gcode_preview_axis(words, "X", x_coord, absolute, unit_scale, wco_x)
        y_coord = _gcode_preview_axis(words, "Y", y_coord, absolute, unit_scale, wco_y)
        if motion_mode not in {1, 2, 3}:
            continue
        if x_coord == previous_x and y_coord == previous_y and motion_mode == 1:
            continue
        path = _gcode_preview_motion_path(previous_x, previous_y, x_coord, y_coord, motion_mode, arc_values)
        if len(path) < 2:
            continue
        motion_segments.append(path)
        if laser_time_estimate._laser_burning(spindle_mode, motion_mode, spindle_power):
            burning_segments.append(path)

    return burning_segments or motion_segments


def _gcode_preview_axis(words, letter, current, absolute, unit_scale, offset):
    if not laser_time_estimate._has_letter(words, letter):
        return current
    value = laser_time_estimate._word_value(words, letter) * unit_scale
    return value + offset if absolute else current + value


def _gcode_preview_motion_path(start_x, start_y, end_x, end_y, motion_mode, arc_values):
    if motion_mode in {2, 3}:
        arc_path = _gcode_preview_arc_path(start_x, start_y, end_x, end_y, motion_mode, arc_values)
        if arc_path:
            return arc_path
    return [(start_x, start_y), (end_x, end_y)]


def _gcode_preview_arc_path(start_x, start_y, end_x, end_y, motion_mode, arc_values):
    center = _gcode_preview_arc_center(start_x, start_y, end_x, end_y, motion_mode, arc_values)
    if not center:
        return None
    center_x, center_y = center
    radius = math.hypot(start_x - center_x, start_y - center_y)
    end_radius = math.hypot(end_x - center_x, end_y - center_y)
    if radius <= 0 or not math.isfinite(radius) or abs(end_radius - radius) > max(0.01, radius * 0.02):
        return None
    start_angle = math.atan2(start_y - center_y, start_x - center_x)
    end_angle = math.atan2(end_y - center_y, end_x - center_x)
    sweep = _gcode_preview_arc_sweep(start_angle, end_angle, motion_mode)
    if abs(sweep) < 1e-9:
        sweep = -2 * math.pi if motion_mode == 2 else 2 * math.pi
    arc_length = abs(radius * sweep)
    steps = max(8, min(720, int(math.ceil(arc_length / 1.0))))
    return [
        (
            center_x + math.cos(start_angle + sweep * index / steps) * radius,
            center_y + math.sin(start_angle + sweep * index / steps) * radius,
        )
        for index in range(steps + 1)
    ]


def _gcode_preview_arc_center(start_x, start_y, end_x, end_y, motion_mode, arc_values):
    if "I" in arc_values or "J" in arc_values:
        return start_x + arc_values.get("I", 0.0), start_y + arc_values.get("J", 0.0)
    if "R" in arc_values:
        return _gcode_preview_arc_center_from_radius(start_x, start_y, end_x, end_y, motion_mode, arc_values["R"])
    return None


def _gcode_preview_arc_center_from_radius(start_x, start_y, end_x, end_y, motion_mode, radius_value):
    radius = abs(radius_value)
    chord_x = end_x - start_x
    chord_y = end_y - start_y
    chord = math.hypot(chord_x, chord_y)
    if radius <= 0 or chord <= 0 or chord > radius * 2 + 1e-9:
        return None
    mid_x = (start_x + end_x) / 2
    mid_y = (start_y + end_y) / 2
    half_chord = chord / 2
    height_sq = max(radius * radius - half_chord * half_chord, 0.0)
    height = math.sqrt(height_sq)
    perp_x = -chord_y / chord
    perp_y = chord_x / chord
    candidates = [
        (mid_x + perp_x * height, mid_y + perp_y * height),
        (mid_x - perp_x * height, mid_y - perp_y * height),
    ]
    wants_large_arc = radius_value < 0

    def score(center):
        center_x, center_y = center
        start_angle = math.atan2(start_y - center_y, start_x - center_x)
        end_angle = math.atan2(end_y - center_y, end_x - center_x)
        sweep = abs(_gcode_preview_arc_sweep(start_angle, end_angle, motion_mode))
        is_large_arc = sweep > math.pi + 1e-9
        return 0 if is_large_arc == wants_large_arc else 1

    return min(candidates, key=score)


def _gcode_preview_arc_sweep(start_angle, end_angle, motion_mode):
    if motion_mode == 2:
        return -((start_angle - end_angle) % (2 * math.pi))
    return (end_angle - start_angle) % (2 * math.pi)


def _nested_dict(mapping, key):
    value = mapping.get(key) if isinstance(mapping, dict) else None
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


def _decode_draw_image_data_url(data_url):
    if not data_url:
        return None, None, "image_data_url 不能为空"
    match = DRAW_DATA_URL_RE.match(str(data_url).strip())
    if not match:
        return None, None, "image_data_url 必须是 PNG/JPEG/WebP 的 base64 data URL"
    mime_type = match.group("mime").lower()
    suffix = DRAW_MIME_SUFFIXES.get(mime_type)
    if not suffix:
        return None, None, "不支持的图片 MIME 类型"
    try:
        image_bytes = base64.b64decode(match.group("data"), validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        return None, None, f"图片 base64 解析失败: {exc}"
    if not image_bytes:
        return None, None, "图片内容为空"
    if len(image_bytes) > MAX_DRAW_IMAGE_BYTES:
        return None, None, f"图片过大，最大允许 {MAX_DRAW_IMAGE_BYTES} 字节"
    magic_error = _validate_draw_image_magic(image_bytes, suffix)
    if magic_error:
        return None, None, magic_error
    return image_bytes, suffix, None


def _validate_draw_image_magic(image_bytes, suffix):
    if suffix == ".png" and image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    if suffix == ".jpg" and image_bytes.startswith(b"\xff\xd8"):
        return None
    if suffix == ".webp" and image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
        return None
    return "图片内容和声明的格式不匹配"


def _safe_draw_timestamp():
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _content_fingerprint_hex(image_bytes):
    """Full 64-char lowercase SHA-256 of actual received image bytes."""
    return hashlib.sha256(image_bytes).hexdigest().lower()


def _save_draw_image(image_bytes, suffix, output_dir=None):
    output_root = Path(output_dir or DRAW_OUTPUT_DIR)
    output_root.mkdir(parents=True, exist_ok=True)
    full_digest = _content_fingerprint_hex(image_bytes)
    path = output_root / f"draw_{_safe_draw_timestamp()}_{full_digest[:12]}{suffix}"
    path.write_bytes(image_bytes)
    return path, full_digest


def _save_draw_scene_json(scene_json, image_path):
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
    if len(body) > MAX_DRAW_SCENE_JSON_BYTES:
        return None, f"scene_json 过大，最大允许 {MAX_DRAW_SCENE_JSON_BYTES} 字节"
    path = Path(image_path).with_suffix(".excalidraw.json")
    path.write_bytes(body)
    return path, None


def _draw_default_task_type(mode):
    normalized = str(mode or "").strip().lower()
    if normalized == "raster":
        return "engrave_photo"
    if normalized == "outline":
        return "engrave_logo"
    return ""


def _is_lasergrbl_param_entry(value):
    return isinstance(value, dict) and any(key in value for key in laser_material_calibration_tool.PARAMETER_KEYS)


def _draw_material_group(material, aliases):
    tokens = {str(material).strip().lower(), *(str(alias).strip().lower() for alias in aliases or [])}
    if tokens & {"wood", "basswood", "plywood", "balsa", "椴木", "木头", "木板", "木料", "木牌", "木片", "木质", "椴木板"}:
        return "wood_like"
    if tokens & {"paper", "cardboard", "paperboard", "cardstock", "纸", "纸张", "纸板", "卡纸", "瓦楞纸"}:
        return "paper_like"
    if tokens & {"leather", "cowhide", "cowhide_leather", "皮革", "皮料", "牛皮"}:
        return "leather_like"
    if tokens & {"test", "测试", "通用"}:
        return "test"
    return None


def _canonical_draw_material(material, aliases):
    group = _draw_material_group(material, aliases)
    if group == "wood_like":
        return "wood"
    if group == "paper_like":
        return "paper"
    if group == "leather_like":
        return "cowhide_leather"
    if group == "test":
        return "test"
    return str(material)


def _draw_param_confidence(params):
    source = str((params or {}).get("source") or "").strip().lower()
    if source in {"manual", "verified", "user", "user_library", "user_tested", "calibration"}:
        return "verified"
    if source in {"estimated", "experimental"}:
        return source
    return "library"


def _append_draw_material_record(records, material, aliases, thickness_mm, task_type, mode, params):
    if not _is_lasergrbl_param_entry(params):
        return
    try:
        power = int(params["laser_max_power"])
        speed = int(params["feed_rate"])
    except (KeyError, TypeError, ValueError):
        return
    canonical_material = _canonical_draw_material(material, aliases)
    record_aliases = [str(alias) for alias in aliases or []]
    if str(material) != canonical_material and str(material) not in record_aliases:
        record_aliases.append(str(material))
    record = {
        "machine_profile_id": "yisu-v1-100x100",
        "material": canonical_material,
        "thickness_mm": float(thickness_mm),
        "task_type": task_type,
        "confidence": _draw_param_confidence(params),
        "mode": mode,
        "speed": speed,
        "power": power,
        "passes": int(params.get("passes") or 1),
        "source": str(params.get("source") or "lasergrbl_materials"),
        "aliases": record_aliases,
        "material_group": _draw_material_group(material, aliases),
        "safety_note": str(params.get("notes") or "来自本机 .lasergrbl_materials.json 的材料参数"),
    }
    if "pixel_size_mm" in params:
        record["pixel_size_mm"] = float(params["pixel_size_mm"])
    records.append(record)


def _converted_draw_material_records(payload):
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
                _append_draw_material_record(records, material, aliases, thickness_mm, "engrave_photo", "raster", engrave)
            elif isinstance(engrave, dict):
                _append_draw_material_record(
                    records, material, aliases, thickness_mm, "engrave_photo", "raster", engrave.get("raster")
                )
                _append_draw_material_record(
                    records, material, aliases, thickness_mm, "engrave_logo", "outline", engrave.get("outline")
                )
            _append_draw_material_record(records, material, aliases, thickness_mm, "cut_contour", "outline", modes.get("cut"))
    return records, None


def _write_draw_material_library(params_file=None, output_file=None):
    source_path = Path(params_file or laser_material_calibration_tool.MATERIAL_PARAMS_FILE)
    if not source_path.is_file():
        return "", None
    try:
        payload = json.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return "", f"读取本机材料库失败: {exc}"
    records, error = _converted_draw_material_records(payload)
    if error:
        return "", error
    if not records:
        return "", None
    target = Path(output_file or DRAW_MATERIAL_LIBRARY_FILE)
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


def _draw_workflow_preview_payload(payload, image_path):
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
    # Structured threshold: 0..255 explicit, -1 auto. Omitted keeps workflow default.
    threshold = None
    if "threshold" in payload and payload.get("threshold") not in (None, ""):
        threshold, error = _coerce_int(payload, "threshold", -1)
        if error:
            return None, error
        if threshold != -1 and not (0 <= threshold <= 255):
            return None, "threshold 必须是 0 到 255 的整数，或 -1 表示自动"
    network_http_port, error = _coerce_int(payload, "network_http_port", laser_network_grbl_tool.DEFAULT_HTTP_PORT)
    if error:
        return None, error
    network_telnet_port, error = _coerce_int(payload, "network_telnet_port", laser_network_grbl_tool.DEFAULT_TELNET_PORT)
    if error:
        return None, error
    network_timeout, error = _coerce_float(payload, "network_timeout", laser_network_grbl_tool.DEFAULT_TIMEOUT)
    if error:
        return None, error
    material_library, error = _write_draw_material_library()
    if error:
        return None, error

    # outline-only: default 3; reject non-finite / out of 0.25..8 before save/generate.
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

    task_type = _optional_text(payload, "task_type") or _draw_default_task_type(mode)
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
        "output_dir": str(DRAW_JOB_OUTPUT_DIR),
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
    if threshold is not None:
        workflow_payload["threshold"] = threshold
    if raster_scan_direction:
        workflow_payload["raster_scan_direction"] = raster_scan_direction
    if raster_output_strategy:
        workflow_payload["raster_output_strategy"] = raster_output_strategy
    if vector_simplify_factor is not None:
        workflow_payload["vector_simplify_factor"] = vector_simplify_factor
    return workflow_payload, None


def _time_estimate_from_sources(*sources):
    for source in sources:
        source = source if isinstance(source, dict) else {}
        estimate = source.get("time_estimate")
        if isinstance(estimate, dict):
            return estimate
        runtime_estimate = _nested_dict(source, "runtime_job").get("time_estimate")
        if isinstance(runtime_estimate, dict):
            return runtime_estimate
    return {}


def _draw_job_summary_sources(payload):
    payload = payload if isinstance(payload, dict) else {}
    workflow = _nested_dict(payload, "workflow")
    artifacts = _nested_dict(payload, "artifacts")
    workflow_artifacts = _nested_dict(workflow, "artifacts")
    last_preview = _nested_dict(workflow, "last_preview_result")
    summary = _first_dict(
        payload.get("summary"),
        artifacts.get("summary"),
        workflow_artifacts.get("summary"),
        last_preview.get("summary"),
    )
    return {
        "payload": payload,
        "workflow": workflow,
        "artifacts": artifacts,
        "workflow_artifacts": workflow_artifacts,
        "last_preview": last_preview,
        "summary": summary,
        "input": _nested_dict(payload, "input"),
        "workflow_input": _nested_dict(workflow, "input"),
        "raster": _nested_dict(summary, "raster"),
        "trace": _nested_dict(summary, "trace"),
        "placement": _first_dict(summary.get("placement"), artifacts.get("placement"), payload.get("placement")),
        "safety": _nested_dict(summary, "safety_report"),
        "routing": _nested_dict(summary, "routing"),
        "params": _first_dict(payload.get("params"), last_preview.get("params"), artifacts.get("params")),
        "auto_adjustment": _first_dict(payload.get("auto_adjustment"), last_preview.get("auto_adjustment")),
    }


def _draw_dimension_note(requested_width, requested_height, actual_width, actual_height, scaled):
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


def _draw_auto_adjustment_note(auto_adjustment):
    reason = str((auto_adjustment or {}).get("reason") or "")
    pixel_size = _as_number_or_none((auto_adjustment or {}).get("pixel_size_mm"))
    if reason == "raster_output_too_complex" and pixel_size is not None:
        return f"光栅输出过密，已自动把点距调整到 {pixel_size:g} mm。"
    return ""


def _draw_job_summary_from_payload(payload):
    sources = _draw_job_summary_sources(payload)
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
    estimated_seconds_number = _as_number_or_none(
        _first_non_empty(time_estimate.get("estimated_seconds"), time_estimate.get("total_seconds"))
    )
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
    scaled = bool(
        _as_bool_or_none(
            _first_non_empty(
                placement.get("scaled_to_safe_area"),
                raster.get("scaled"),
                trace.get("scaled"),
                placement.get("scaled"),
            )
        )
    )
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
        "size_note": _draw_dimension_note(requested_width, requested_height, actual_width, actual_height, scaled),
        "power": _first_non_empty(summary.get("power"), summary.get("laser_max_power"), params.get("laser_max_power")),
        "speed": _first_non_empty(summary.get("speed"), summary.get("feed_rate"), params.get("feed_rate")),
        "passes": _first_non_empty(summary.get("passes"), params.get("passes")),
        "pixel_size_mm": _first_non_empty(summary.get("pixel_size_mm"), raster.get("pixel_size_mm"), params.get("pixel_size_mm")),
        "threshold": _first_non_empty(summary.get("threshold"), raster.get("threshold"), params.get("threshold")),
        "resize_strategy": _first_non_empty(summary.get("resize_strategy"), raster.get("resize_strategy")),
        "dither_algorithm": _first_non_empty(
            summary.get("dither_algorithm"),
            raster.get("dither_algorithm"),
            params.get("dither_algorithm"),
            input_payload.get("dither_algorithm"),
            workflow_input.get("dither_algorithm"),
        ),
        "estimated_seconds": estimated_seconds_number,
        "estimated_display": estimated_display,
        "time_note": time_estimate.get("note") or "按最终 G-code 的运动距离和进给速度估算，实际时间可能略有差异。",
        "can_send": bool(can_send_value),
        "recommendation_status": _first_non_empty(summary.get("recommendation_status"), safety.get("recommendation_status")),
        "warnings": _unique_text_list(
            summary.get("warnings"),
            safety.get("warnings"),
            routing.get("warnings"),
            _draw_auto_adjustment_note(auto_adjustment),
        ),
        "auto_adjustment": auto_adjustment,
        "message": _first_non_empty(summary.get("message"), safety.get("message"), payload.get("result")),
        "gcode_file": _first_non_empty(artifacts.get("gcode_file"), summary.get("gcode_path"), payload.get("gcode_file")),
    }


def _add_draw_links(result, uploaded_image_path=None, scene_path=None, content_fingerprint=None):
    result = _augment_web_links(result)
    if not result.get("success"):
        return result
    payload = dict(result.get("result") or {})
    web = dict(payload.get("web") or {})
    draw_lab = dict(payload.get("draw_lab") or {})
    draw_lab["job_summary"] = _draw_job_summary_from_payload(payload)
    if content_fingerprint:
        draw_lab["content_fingerprint"] = str(content_fingerprint).lower()
    if uploaded_image_path:
        draw_lab["uploaded_image_file"] = str(uploaded_image_path)
        web["uploaded_image_url"] = _preview_url(uploaded_image_path)
    if scene_path:
        draw_lab["scene_file"] = str(scene_path)
        web["scene_download_url"] = _preview_url(scene_path, download=True, filename=Path(scene_path).name)
    payload["draw_lab"] = draw_lab
    if web:
        payload["web"] = web
    return {**result, "result": payload}


def draw_preview_from_payload(payload, workflow_runner=None):
    image_bytes, suffix, error = _decode_draw_image_data_url(payload.get("image_data_url"))
    if error:
        return _build_failure(error)
    preview_payload, error = _draw_workflow_preview_payload(payload, "<pending>")
    if error:
        return _build_failure(error)
    try:
        image_path, content_fingerprint = _save_draw_image(image_bytes, suffix)
        scene_path, scene_error = _save_draw_scene_json(payload.get("scene_json"), image_path)
        if scene_error:
            return _build_failure(scene_error, {"uploaded_image_file": str(image_path)})
        preview_payload["image_file"] = str(image_path)
        # Trusted draw-lab entry pins nearest_engrave policy server-side.
        runner = workflow_runner or laser_workflow_tool.preview_draw_lab_image
        result = runner(**preview_payload)
        return _add_draw_links(
            result,
            uploaded_image_path=image_path,
            scene_path=scene_path,
            content_fingerprint=content_fingerprint,
        )
    except OSError as exc:
        return _build_failure(f"保存画板素材失败: {exc}")


def ui_runtime_config_response(settings=None):
    """Project a safe, non-secret subset of the effective laser settings for the Web UI."""
    try:
        effective = settings or get_laser_settings()
        configured_mode = getattr(effective, "default_connection_mode", None)
        default_mode, mode_error = laser_execution.resolve_laser_connection_mode(
            "", default_mode=configured_mode
        )
        if mode_error:
            return _build_failure("读取激光运行配置失败", status=500)
        other_mode = "serial" if default_mode == "network" else "network"
        serial_port = str(
            getattr(effective, "default_serial_port", laser_grbl_tool.DEFAULT_GRBL_PORT) or ""
        ).strip()
        baudrate = int(getattr(effective, "baudrate", laser_grbl_tool.DEFAULT_BAUDRATE))
        network_host = str(
            getattr(effective, "network_host", laser_network_grbl_tool.DEFAULT_NETWORK_HOST) or ""
        ).strip()
        network_http_port = int(
            getattr(effective, "network_http_port", laser_network_grbl_tool.DEFAULT_HTTP_PORT)
        )
        network_telnet_port = int(
            getattr(effective, "network_telnet_port", laser_network_grbl_tool.DEFAULT_TELNET_PORT)
        )
        network_timeout = float(
            getattr(effective, "network_timeout", laser_network_grbl_tool.DEFAULT_TIMEOUT)
        )
        return _build_success(
            {
                "connection": {
                    "default_mode": default_mode,
                    "preferred_modes": [default_mode, other_mode],
                    "serial": {"default_port": serial_port, "baudrate": baudrate},
                    "network": {
                        "default_host": network_host,
                        # Complete-file jobs are forced through Telnet by the execution layer.
                        "transport": laser_network_grbl_tool.FORCED_FILE_TRANSPORT,
                        "http_port": network_http_port,
                        "telnet_port": network_telnet_port,
                        "timeout": network_timeout,
                    },
                },
                "upload": {"gcode_suffixes": [".gcode", ".nc"]},
            }
        )
    except (TypeError, ValueError):
        return _build_failure("读取激光运行配置失败", status=500)
    except Exception:
        return _build_failure("读取激光运行配置失败", status=500)


def ui_serial_ports_response(settings=None):
    """Return the currently enumerated serial ports without opening a send session."""
    try:
        _serial_module, list_ports, serial_error = laser_grbl_tool._load_serial()
        if serial_error:
            return _build_failure(serial_error)
        effective = settings or get_laser_settings()
        ports = laser_grbl_tool._list_serial_ports(list_ports)
        return _build_success(
            {
                "ports": ports,
                "default_port": str(
                    getattr(effective, "default_serial_port", laser_grbl_tool.DEFAULT_GRBL_PORT) or ""
                ).strip(),
                "baudrate": int(getattr(effective, "baudrate", laser_grbl_tool.DEFAULT_BAUDRATE)),
            }
        )
    except (TypeError, ValueError):
        return _build_failure("读取串口列表失败", status=500)
    except Exception:
        return _build_failure("读取串口列表失败", status=500)


def _connection_mode_label(mode):
    return {"serial": "串口", "network": "网络"}.get(mode, mode)


def connection_check_from_payload(payload, checker=None, serial_checker=None):
    """Run an ordered, read-only serial/network preflight for the local Web UI.

    ``checker`` and ``serial_checker`` are injectable for tests and retain the
    existing network checker signature.  A successful preferred probe stops the
    sequence; the second mode is tried only when the preferred probe fails.
    """
    payload = payload if isinstance(payload, dict) else {}
    http_port, error = _coerce_int(payload, "network_http_port", laser_network_grbl_tool.DEFAULT_HTTP_PORT)
    if error:
        return _build_failure(error)
    telnet_port, error = _coerce_int(payload, "network_telnet_port", laser_network_grbl_tool.DEFAULT_TELNET_PORT)
    if error:
        return _build_failure(error)
    timeout, error = _coerce_float(payload, "network_timeout", laser_network_grbl_tool.DEFAULT_TIMEOUT)
    if error:
        return _build_failure(error)
    baudrate, error = _coerce_int(payload, "baudrate", laser_grbl_tool.DEFAULT_BAUDRATE)
    if error:
        return _build_failure(error)

    requested_mode = _optional_text(payload, "connection_mode") or _optional_text(payload, "preferred_mode")
    if requested_mode.strip().lower() in {"auto", "default", "按配置"}:
        requested_mode = ""
    preferred_mode, mode_error = laser_execution.resolve_laser_connection_mode(requested_mode)
    if mode_error:
        return _build_failure(mode_error)
    other_mode = "serial" if preferred_mode == "network" else "network"
    modes = [preferred_mode, other_mode] if _coerce_bool(payload, "auto_fallback", True) else [preferred_mode]

    serial_port = (
        _optional_text(payload, "port")
        or _optional_text(payload, "serial_port")
        or str(laser_grbl_tool.DEFAULT_GRBL_PORT or "").strip()
    )
    network_host = (
        _optional_text(payload, "network_host")
        or _optional_text(payload, "host")
        or str(laser_network_grbl_tool.DEFAULT_NETWORK_HOST or "").strip()
    )
    network_transport = (
        _optional_text(payload, "network_transport", laser_network_grbl_tool.FORCED_FILE_TRANSPORT)
        or laser_network_grbl_tool.FORCED_FILE_TRANSPORT
    )
    include_detail = _coerce_bool(payload, "include_detail", False)
    serial_connected = False
    network_connected = False
    serial_detail = None
    network_detail = None
    attempts = []
    selected_mode = None

    for mode in modes:
        connected = False
        detail = None
        try:
            if mode == "serial":
                runner = serial_checker or check_laser_connection_tool._check_serial_connection
                connected, detail = runner(port=serial_port, baudrate=baudrate)
                serial_connected = bool(connected)
                serial_detail = detail
            else:
                if not network_host:
                    connected = False
                    detail = {"error": "设备 host/IP 不能为空"}
                else:
                    runner = checker or check_laser_connection_tool._check_network_connection
                    connected, detail = runner(
                        host=network_host,
                        transport=network_transport,
                        http_port=http_port,
                        telnet_port=telnet_port,
                        timeout=timeout,
                    )
                network_connected = bool(connected)
                network_detail = detail
        except Exception:
            # A probe failure is a failed attempt, not a server crash; do not leak
            # exception text or create a send side effect.
            connected = False
            detail = {"error": f"{_connection_mode_label(mode)}连接检查失败"}
            if mode == "serial":
                serial_detail = detail
            else:
                network_detail = detail

        attempt = {"mode": mode, "connected": bool(connected)}
        if connected:
            attempt["summary"] = f"{_connection_mode_label(mode)}已连接"
        else:
            attempt["summary"] = f"{_connection_mode_label(mode)}未连接"
        if include_detail and detail is not None:
            attempt["detail"] = detail
        attempts.append(attempt)
        if connected:
            selected_mode = mode
            break

    fallback_used = bool(selected_mode and selected_mode != preferred_mode)
    if selected_mode:
        if fallback_used:
            summary = (
                f"{_connection_mode_label(preferred_mode)}不可用，"
                f"已回退到{_connection_mode_label(selected_mode)}"
            )
        else:
            summary = f"{_connection_mode_label(selected_mode)}已连接"
    else:
        summary = "未检测到串口或网络连接"
    result = {
        "preferred_mode": preferred_mode,
        "selected_mode": selected_mode,
        "fallback_used": fallback_used,
        "attempts": attempts,
        "serial_connected": serial_connected,
        "network_connected": network_connected,
        "summary": summary,
    }
    if include_detail:
        result["detail"] = {"serial": serial_detail, "network": network_detail}
    return _build_success(result)


def _suggest_gcode_download_name(payload, gcode_file):
    original_name = _safe_download_filename(Path(gcode_file).name, "download.gcode")
    if not isinstance(payload, dict):
        return original_name

    artifacts = _nested_dict(payload, "artifacts")
    workflow = _nested_dict(payload, "workflow")
    workflow_artifacts = _nested_dict(workflow, "artifacts")
    input_payload = _nested_dict(payload, "input")
    workflow_input = _nested_dict(workflow, "input")

    source = str(_first_non_empty(payload.get("source"), artifacts.get("source")) or "").strip()
    if source == "prepared_gcode_file":
        return original_name

    task_id = _first_non_empty(
        payload.get("task_id"),
        artifacts.get("task_id"),
        workflow.get("task_id"),
        workflow_artifacts.get("task_id"),
    )
    if not task_id:
        return original_name

    attempt_no = _first_non_empty(
        payload.get("attempt_no"),
        artifacts.get("attempt_no"),
        workflow.get("attempt_no"),
        workflow_artifacts.get("attempt_no"),
        1,
    )
    try:
        attempt_no = int(float(attempt_no))
    except (TypeError, ValueError):
        attempt_no = 1

    text = _first_non_empty(payload.get("text"), input_payload.get("text"), workflow_input.get("text"))
    text_stem = _safe_download_stem(text, fallback="文字")
    task_stem = _safe_download_stem(str(task_id), fallback="task", max_chars=8)
    extension = Path(original_name).suffix or ".gcode"
    return _safe_download_filename(f"{text_stem}_attempt_{attempt_no}_{task_stem}{extension}", original_name)


def _augment_web_links(result):
    if not result.get("success"):
        return result
    payload = dict(result.get("result") or {})
    web = {}
    artifacts = payload.get("artifacts") or {}
    image_file = payload.get("image_file") or artifacts.get("image_file")
    gcode_file = payload.get("gcode_file") or artifacts.get("gcode_file")
    task_file = payload.get("task_file") or artifacts.get("task_file")
    workflow_file = payload.get("workflow_file")
    if image_file:
        web["material_preview_url"] = _preview_url(image_file)
    if gcode_file:
        gcode_preview = render_gcode_preview_image(gcode_file)
        if gcode_preview:
            web["gcode_preview_url"] = _preview_url(gcode_preview)
    if gcode_file:
        download_name = _suggest_gcode_download_name(payload, gcode_file)
        web["gcode_download_name"] = download_name
        web["gcode_download_url"] = _preview_url(gcode_file, download=True, filename=download_name)
    if task_file:
        web["task_json_url"] = _preview_url(task_file)
    if workflow_file:
        web["workflow_json_url"] = _preview_url(workflow_file)
    if web.get("material_preview_url") or web.get("gcode_preview_url"):
        web["image_preview_url"] = web.get("material_preview_url") or web.get("gcode_preview_url")
    if web:
        payload["web"] = web
    return {**result, "result": payload}


def _text_generation_kwargs(payload):
    text, error = _required_text(payload, "text")
    if error:
        return None, error
    material, error = _required_text(payload, "material")
    if error:
        return None, error
    thickness_mm, error = _coerce_float(payload, "thickness_mm")
    if error:
        return None, error
    if thickness_mm <= 0:
        return None, "thickness_mm 必须大于 0"

    manual_confirmed = laser_material_safety.is_manual_params_confirmed(
        payload.get("manual_params_confirmed")
    )
    manual_keys = set(laser_material_safety.MANUAL_PARAM_KEYS)

    numeric_fields = {}
    for key in (
        "width_mm",
        "height_mm",
        "power_percent",
        "laser_min_power",
        "laser_max_power",
        "feed_rate",
        "travel_rate",
        "pixel_size_mm",
        "threshold",
        "passes",
        "dpi",
        "offset_x_mm",
        "offset_y_mm",
        "safe_margin_mm",
        "trim_tolerance",
        "overscan_mm",
    ):
        if key in manual_keys and not manual_confirmed:
            # Page residue must not override material library without explicit confirmation.
            continue
        default = {
            "dpi": 300.0,
            "safe_margin_mm": text_laser_task_tool.laser_grbl_tool.DEFAULT_SAFE_MARGIN_MM,
            "trim_tolerance": 20,
            "overscan_mm": text_laser_task_tool.laser_grbl_tool.DEFAULT_RASTER_OVERSCAN_MM,
        }.get(key)
        if key in payload or default is not None:
            value, error = _coerce_float(payload, key, default if default is not None else None)
            if error:
                return None, error
            numeric_fields[key] = value

    for key in ("image_width", "image_height", "font_size", "max_lines"):
        default = {
            "image_width": text_image_tool.DEFAULT_IMAGE_WIDTH,
            "image_height": text_image_tool.DEFAULT_IMAGE_HEIGHT,
            "font_size": text_image_tool.DEFAULT_FONT_SIZE,
            "max_lines": text_image_tool.DEFAULT_MAX_LINES,
        }[key]
        value, error = _coerce_int(payload, key, default)
        if error:
            return None, error
        numeric_fields[key] = value

    laser_mode = _optional_text(payload, "laser_mode", "engrave") or "engrave"
    engraving_mode = _optional_text(payload, "engraving_mode", "raster") or "raster"
    if laser_mode == "cut":
        engraving_mode = ""

    kwargs = {
        "text": text,
        "material": material,
        "thickness_mm": thickness_mm,
        "laser_mode": laser_mode,
        "engraving_mode": engraving_mode,
        "font_path": _optional_text(payload, "font_path", ""),
        "layout_mode": _optional_text(payload, "layout_mode", ""),
        "auto_wrap": _coerce_bool(payload, "auto_wrap", text_image_tool.DEFAULT_AUTO_WRAP),
        "invert": _coerce_bool(payload, "invert", False),
        "bidirectional": _coerce_bool(payload, "bidirectional", False),
        "overwrite": True,
        "auto_trim": _coerce_bool(payload, "auto_trim", True),
        "auto_size": _coerce_bool(payload, "auto_size", True),
        "lock_aspect_ratio": _coerce_bool(payload, "lock_aspect_ratio", True),
        "raster_scan_direction": _optional_text(payload, "raster_scan_direction", "auto") or "auto",
        "send_after_generate": False,
        "confirmed": False,
        "dry_run": True,
        "connection_mode": "network",
        "reuse_prepared_gcode": _coerce_bool(payload, "reuse_prepared_gcode", True),
    }
    if manual_confirmed:
        kwargs["manual_params_confirmed"] = True
    kwargs.update({key: value for key, value in numeric_fields.items() if value is not None})
    return kwargs, None


def generate_text_task_from_payload(payload, generator=None, workflow_runner=None):
    material = _optional_text(payload, "material", "")
    if material:
        decision = laser_material_safety.evaluate_material_safety(material)
        if decision.get("decision") != laser_material_safety.DECISION_ALLOW:
            message = decision.get("message") or "材料不满足安全要求"
            error_code = (
                "material_blocked"
                if decision.get("decision") == laser_material_safety.DECISION_BLOCK
                else "material_clarify"
            )
            failure = _build_failure(message, detail=decision)
            failure["error_code"] = error_code
            failure["speech"] = f"{message}；本次没有生成可发送预览，也没有访问激光机。"
            return failure
    kwargs, error = _text_generation_kwargs(payload)
    if error:
        return _build_failure(error)
    runner = workflow_runner or laser_workflow_tool.run_laser_workflow_action
    result = runner(
        action="preview",
        workflow_id=_optional_text(payload, "workflow_id", ""),
        source_type="text",
        text_generator=generator,
        **kwargs,
    )
    return _augment_web_links(result)


def _send_kwargs(payload):
    workflow_id, error = _required_text(payload, "workflow_id")
    if error:
        return None, error
    confirmed = _coerce_bool(payload, "confirmed", False)
    requested_mode = _optional_text(payload, "connection_mode")
    if requested_mode.strip().lower() in {"auto", "default", "按配置"}:
        requested_mode = ""
    connection_mode, mode_error = laser_execution.resolve_laser_connection_mode(requested_mode)
    if mode_error:
        return None, mode_error

    result = {
        "workflow_id": workflow_id,
        "confirmed": confirmed,
        "connection_mode": connection_mode,
        # Web sends always use the background worker and wait for controller ACKs.
        "run_in_background": _coerce_bool(payload, "run_in_background", True),
        "wait_for_response": _coerce_bool(payload, "wait_for_response", True),
    }
    if connection_mode == "serial":
        port = _optional_text(payload, "port") or _optional_text(payload, "serial_port")
        if not port:
            port = str(laser_grbl_tool.DEFAULT_GRBL_PORT or "").strip()
        baudrate, error = _coerce_int(payload, "baudrate", laser_grbl_tool.DEFAULT_BAUDRATE)
        if error:
            return None, error
        result.update({"port": port, "baudrate": baudrate})
        return result, None

    network_host = _optional_text(payload, "network_host", "")
    http_port, error = _coerce_int(payload, "network_http_port", laser_network_grbl_tool.DEFAULT_HTTP_PORT)
    if error:
        return None, error
    telnet_port, error = _coerce_int(payload, "network_telnet_port", laser_network_grbl_tool.DEFAULT_TELNET_PORT)
    if error:
        return None, error
    timeout, error = _coerce_float(payload, "network_timeout", laser_network_grbl_tool.DEFAULT_TIMEOUT)
    if error:
        return None, error
    result.update(
        {
            "network_host": network_host,
            # Complete-file network sends are forced to Telnet by core.laser_execution.
            "network_transport": laser_network_grbl_tool.FORCED_FILE_TRANSPORT,
            "network_http_port": http_port,
            "network_telnet_port": telnet_port,
            "network_timeout": timeout,
        }
    )
    return result, None


def send_text_task_from_payload(payload, sender=None, workflow_runner=None):
    kwargs, error = _send_kwargs(payload)
    if error:
        return _build_failure(error)
    runner = workflow_runner or laser_workflow_tool.run_laser_workflow_action
    workflow_id = kwargs.pop("workflow_id")
    result = runner(
        action="confirm_send",
        workflow_id=workflow_id,
        tuned_job_sender=sender,
        **kwargs,
    )
    return _augment_web_links(result)


def workflow_action_from_payload(payload, workflow_runner=None):
    action = _optional_text(payload, "action", "status") or "status"
    workflow_id = _optional_text(payload, "workflow_id", "")
    payload = dict(payload)
    payload.pop("action", None)
    payload.pop("workflow_id", None)
    # Trusted HTTP boundary: main Web / Android image previews pin match/send
    # policy server-side. External JSON cannot inject those fields; MCP and
    # direct run_laser_workflow_action keep strict core defaults.
    normalized_action = str(action or "status").strip().lower().replace("-", "_")
    source_type = str(payload.get("source_type") or "").strip().lower()
    if (
        workflow_runner is None
        and normalized_action in {"preview", "plan"}
        and source_type == "image"
    ):
        result = laser_workflow_tool.preview_http_image_workflow(
            workflow_id=workflow_id,
            **payload,
        )
        return _augment_web_links(result)
    # confirm_send lean contract: same whitelist as /api/text-task/send and draw lab.
    # Strip production form residue (text/auto_*/font_size/...) so frozen-snapshot
    # gate is not tripped by main-page full-form resubmit.
    if normalized_action in {"confirm_send", "send"}:
        send_payload = dict(payload)
        send_payload["workflow_id"] = workflow_id
        kwargs, error = _send_kwargs(send_payload)
        if error:
            return _build_failure(error)
        workflow_id = kwargs.pop("workflow_id")
        runner = workflow_runner or laser_workflow_tool.run_laser_workflow_action
        result = runner(
            action="confirm_send",
            workflow_id=workflow_id,
            **kwargs,
        )
        return _augment_web_links(result)
    runner = workflow_runner or laser_workflow_tool.run_laser_workflow_action
    result = runner(action=action, workflow_id=workflow_id, **payload)
    return _augment_web_links(result)


def load_main_index_html():
    """Read the main Web page from the apps/laser_web/ui static directory."""
    return (MAIN_UI_STATIC_DIR / "index.html").read_text(encoding="utf-8")


def _v2_static_file_for_path(request_path):
    request_path = unquote(str(request_path or ""))
    if not request_path.startswith("/vendor/"):
        return None
    static_root = Path(MAIN_UI_STATIC_DIR).resolve()
    relative = request_path.lstrip("/")
    try:
        candidate = (static_root / relative).resolve()
    except OSError:
        return None
    if not _is_relative_to(candidate, static_root):
        return None
    if candidate.is_file():
        return candidate
    return None




MATERIAL_LAB_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>材料参数与测试矩阵 Lab</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f5f6f8;
      --panel: #ffffff;
      --ink: #182230;
      --muted: #667085;
      --line: #d0d5dd;
      --soft: #f9fafb;
      --accent: #0f766e;
      --accent-weak: #ccfbf1;
      --warning: #b54708;
      --danger: #b42318;
      --shadow: 0 1px 2px rgba(16, 24, 40, 0.08);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font: 14px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    header {
      min-height: 62px;
      padding: calc(12px + env(safe-area-inset-top, 0px)) calc(18px + env(safe-area-inset-right, 0px)) 12px calc(18px + env(safe-area-inset-left, 0px));
      border-bottom: 1px solid var(--line);
      background: var(--panel);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
    }
    h1, h2, h3 { margin: 0; }
    h1 { font-size: 18px; }
    h2 { font-size: 15px; }
    h3 { font-size: 13px; color: var(--muted); }
    a, button {
      min-height: 34px;
      border-radius: 6px;
      font: inherit;
      font-weight: 650;
    }
    a {
      display: inline-flex;
      align-items: center;
      padding: 7px 10px;
      border: 1px solid var(--line);
      color: var(--accent);
      text-decoration: none;
      background: #fff;
    }
    button {
      width: 100%;
      border: 1px solid var(--accent);
      padding: 8px 10px;
      cursor: pointer;
      color: #fff;
      background: var(--accent);
    }
    button:disabled {
      cursor: not-allowed;
      opacity: 0.55;
    }
    button.secondary { color: var(--accent); background: #fff; }
    button.warning { color: #7a2e0e; border-color: #f79009; background: #fffaeb; }
    button.danger { border-color: var(--danger); background: var(--danger); }
    input, select, textarea {
      width: 100%;
      min-height: 36px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px 9px;
      color: var(--ink);
      background: #fff;
      font: inherit;
    }
    textarea { min-height: 74px; resize: vertical; }
    label {
      display: grid;
      gap: 5px;
      color: var(--muted);
      font-size: 12px;
    }
    main {
      display: grid;
      grid-template-columns: 280px minmax(340px, 1fr) minmax(380px, 1.12fr);
      gap: 12px;
      padding: 12px calc(12px + env(safe-area-inset-right, 0px)) calc(12px + env(safe-area-inset-bottom, 0px)) calc(12px + env(safe-area-inset-left, 0px));
      align-items: start;
    }
    section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      padding: 12px;
      display: grid;
      gap: 12px;
      min-width: 0;
    }
    .header-actions, .row, .button-row, .chips, .mini-grid, .toolbar {
      display: flex;
      gap: 8px;
      align-items: center;
    }
    .header-actions, .button-row, .chips, .toolbar { flex-wrap: wrap; }
    .subtle { color: var(--muted); }
    .source {
      color: var(--muted);
      font-size: 12px;
      word-break: break-all;
    }
    .notice {
      border: 1px solid #fedf89;
      border-radius: 6px;
      background: #fffaeb;
      color: #7a2e0e;
      padding: 9px 10px;
    }
    .status {
      min-height: 38px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--soft);
      color: var(--muted);
      padding: 9px 10px;
    }
    [hidden] { display: none !important; }
    .status.good { border-color: #a6f4c5; background: #ecfdf3; color: #067647; }
    .status.warn { border-color: #fedf89; background: #fffaeb; color: #93370d; }
    .material-list {
      display: grid;
      gap: 8px;
      max-height: calc(100vh - 232px);
      overflow: auto;
      padding-right: 2px;
    }
    .material-row {
      width: 100%;
      min-height: 54px;
      text-align: left;
      color: var(--ink);
      border-color: var(--line);
      background: #fff;
      display: grid;
      gap: 4px;
    }
    .material-row.active {
      border-color: var(--accent);
      background: #f0fdfa;
    }
    .material-row small {
      color: var(--muted);
      font-weight: 500;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .chip {
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      max-width: 100%;
      padding: 3px 8px;
      border: 1px solid var(--line);
      border-radius: 999px;
      color: var(--muted);
      background: #fff;
      font-size: 12px;
    }
    .chip.active {
      border-color: var(--accent);
      color: #0b4f49;
      background: var(--accent-weak);
    }
    .grid2, .grid3, .grid4 {
      display: grid;
      gap: 10px;
    }
    .grid2 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .grid3 { grid-template-columns: repeat(3, minmax(0, 1fr)); }
    .grid4 { grid-template-columns: repeat(4, minmax(0, 1fr)); }
    .mode-tabs {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
    }
    .mode-tab {
      color: var(--ink);
      border-color: var(--line);
      background: #fff;
      min-height: 42px;
    }
    .mode-tab.active {
      color: #fff;
      border-color: var(--accent);
      background: var(--accent);
    }
    .panel-line {
      border-top: 1px solid var(--line);
      padding-top: 12px;
      display: grid;
      gap: 10px;
    }
    .matrix-wrap {
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--soft);
      padding: 10px;
    }
    .matrix {
      display: grid;
      gap: 8px;
      min-width: 360px;
    }
    .matrix-cell {
      min-height: 70px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--ink);
      display: grid;
      place-items: center;
      text-align: center;
      padding: 7px;
      cursor: pointer;
      font-weight: 650;
    }
    .matrix-cell span {
      display: block;
      color: var(--muted);
      font-size: 12px;
      font-weight: 500;
    }
    .matrix-cell strong { font-size: 14px; }
    .fill-swatch, .cut-outline {
      width: 100%;
      min-height: 22px;
      border-radius: 4px;
      border: 1px solid rgba(16, 24, 40, 0.16);
    }
    .fill-swatch {
      background:
        repeating-linear-gradient(
          0deg,
          rgba(24, 34, 48, 0.92) 0 2px,
          rgba(24, 34, 48, 0.08) 2px 4px
        );
    }
    .cut-outline {
      min-height: 28px;
      background:
        linear-gradient(#fff, #fff) padding-box,
        linear-gradient(90deg, var(--accent), #f79009) border-box;
      border: 2px solid transparent;
    }
    .matrix-axis {
      display: grid;
      grid-template-columns: 90px 1fr;
      gap: 8px;
      align-items: center;
      color: var(--muted);
      font-size: 12px;
    }
    .matrix-axis b { color: var(--ink); }
    .matrix-cell.selected {
      border-color: var(--accent);
      background: #ecfdf3;
      box-shadow: inset 0 0 0 2px rgba(15, 118, 110, 0.18);
    }
    .best-summary {
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      padding: 10px;
      min-height: 54px;
      display: grid;
      gap: 4px;
    }
    .matrix-links {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    .flow {
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 8px;
    }
    .flow div {
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--soft);
      padding: 8px;
      min-height: 48px;
      color: var(--muted);
      font-size: 12px;
    }
    @media (max-width: 1180px) {
      main { grid-template-columns: 260px 1fr; }
      .matrix-panel { grid-column: 1 / -1; }
    }
    @media (max-width: 760px) {
      header { align-items: flex-start; flex-direction: column; }
      main, .flow { grid-template-columns: 1fr; }
      .grid2, .grid3, .grid4 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .material-list { max-height: 280px; }
      .mode-tabs { grid-template-columns: repeat(3, minmax(0, 1fr)); }
      .matrix-wrap {
        overscroll-behavior-inline: contain;
        -webkit-overflow-scrolling: touch;
      }
      input, select, textarea { font-size: 16px; }
    }
    @media (pointer: coarse) {
      a,
      button,
      input,
      select { min-height: 44px; }
      input, select, textarea { font-size: 16px; }
      .mode-tab { min-height: 44px; }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>材料参数与测试矩阵 Lab</h1>
      <div class="subtle">Web 端材料调参：预览会生成本地测试矩阵 G-code；发送和保存材料库都需要手动确认。</div>
    </div>
    <div class="header-actions">
      <a href="/">返回任务页</a>
      <a href="/draw">手绘画板</a>
    </div>
  </header>
  <main>
    <section>
      <div>
        <h2>材料库</h2>
        <div class="source" id="sourcePath"></div>
      </div>
      <label>搜索材料或别名
        <input id="materialSearch" placeholder="例如 椴木、木头、acrylic">
      </label>
      <div class="button-row">
        <button class="secondary" type="button" id="newMaterial">新增材料</button>
        <button class="secondary" type="button" id="saveRecent">从最近任务保存</button>
      </div>
      <div class="material-list" id="materialList"></div>
    </section>

    <section>
      <div>
        <h2>参数草稿</h2>
        <div class="subtle">选择材料、厚度和模式后，这里显示将来可保存的一套参数。</div>
      </div>
      <div class="grid2">
        <label>材料
          <input id="editorMaterial" placeholder="材料名">
        </label>
        <label>别名
          <input id="editorAliases" placeholder="木头、木板、basswood">
        </label>
      </div>
      <div class="grid2">
        <label>厚度 mm
          <select id="thicknessSelect"></select>
        </label>
        <label>参数来源
          <input id="editorSource" placeholder="manual / calibration / task">
        </label>
      </div>
      <div class="mode-tabs" id="modeTabs"></div>
      <div class="grid3">
        <label>最小功率 S
          <input id="laser_min_power" type="number" step="1">
        </label>
        <label>最大功率 S
          <input id="laser_max_power" type="number" step="1">
        </label>
        <label>速度 mm/min
          <input id="feed_rate" type="number" step="1">
        </label>
      </div>
      <div class="grid4">
        <label>空移速度
          <input id="travel_rate" type="number" step="1">
        </label>
        <label>次数
          <input id="passes" type="number" step="1" min="1">
        </label>
        <label>像素步距
          <input id="pixel_size_mm" type="number" step="0.01" min="0.01">
        </label>
        <label>阈值
          <input id="threshold" type="number" step="1">
        </label>
      </div>
      <label>备注
        <textarea id="notes" placeholder="例如：3mm 椴木 raster，小样第 12 格效果最好"></textarea>
      </label>
      <div class="button-row">
        <button type="button" id="saveParams">保存参数</button>
        <button class="secondary" type="button" id="copyParams">复制一套参数</button>
        <button class="warning" type="button" id="deleteParams">删除当前参数</button>
      </div>
      <div class="status" id="editorStatus">先选择左侧材料，再调整参数。</div>
    </section>

    <section class="matrix-panel">
      <div>
        <h2>测试矩阵</h2>
        <div class="subtle">用于快速试功率和速度。生成预览后，实物加工完成可选择“第 N 格最好”。</div>
      </div>
      <div class="notice">生成预览只写入本地 G-code 和调参会话；只有点击“确认发送测试矩阵”并二次确认后才会通过网络发送到设备。</div>
      <div class="status good" id="matrixModeSummary">根据当前加工模式自动选择测试矩阵类型。</div>
      <div class="grid3">
        <label id="matrixPowerMinWrap"><span id="matrixPowerMinLabel">功率起点 S</span>
          <input id="matrixPowerMin" type="number" step="1">
        </label>
        <label><span id="matrixPowerMaxLabel">功率终点 S</span>
          <input id="matrixPowerMax" type="number" step="1">
        </label>
        <label><span id="matrixColsLabel">功率列数</span>
          <input id="matrixCols" type="number" min="2" max="9" step="1" value="5">
        </label>
      </div>
      <div class="grid3">
        <label><span id="matrixSpeedMinLabel">速度起点</span>
          <input id="matrixSpeedMin" type="number" step="1">
        </label>
        <label><span id="matrixSpeedMaxLabel">速度终点</span>
          <input id="matrixSpeedMax" type="number" step="1">
        </label>
        <label><span id="matrixRowsLabel">速度行数</span>
          <input id="matrixRows" type="number" min="2" max="9" step="1" value="5">
        </label>
      </div>
      <div class="grid2">
        <label>单格尺寸 mm
          <input id="matrixCellSize" type="number" min="4" step="1" value="10">
        </label>
        <label><span id="matrixPassesLabel">填充线密度 line/mm</span>
          <input id="matrixPasses" type="number" min="1" step="1" value="1">
        </label>
      </div>
      <div class="grid2">
        <label>设备 host/IP
          <input id="matrixHost" placeholder="例如 192.0.2.50">
        </label>
        <label>Telnet 端口
          <input id="matrixTelnetPort" type="number" min="1" max="65535" step="1" value="23">
        </label>
      </div>
      <div class="button-row">
        <button type="button" id="generateMatrix">生成测试矩阵预览</button>
        <button class="secondary" type="button" id="sendMatrix">确认发送测试矩阵</button>
        <button class="secondary" type="button" id="saveBest">保存最佳格到材料库</button>
      </div>
      <div class="matrix-links" id="matrixLinks"></div>
      <div class="matrix-wrap">
        <div class="matrix-axis" id="matrixAxis"></div>
        <div class="matrix" id="matrixPreview"></div>
      </div>
      <div class="best-summary" id="bestSummary">尚未选择最佳格。</div>
      <div class="panel-line">
        <h3>闭环流程</h3>
        <div class="flow">
          <div>1. 选择材料和厚度</div>
          <div>2. 生成测试矩阵</div>
          <div>3. 实物比较效果</div>
          <div>4. 选择最佳格</div>
          <div>5. 保存为推荐参数</div>
        </div>
      </div>
      <div class="status" id="matrixStatus">等待生成矩阵。</div>
    </section>
  </main>

  <script id="materialData" type="application/json">__MATERIAL_LAB_DATA__</script>
  <script>
    const payload = JSON.parse(document.getElementById("materialData").textContent || "{}");
    const materials = payload.materials || {};
    const defaults = payload.defaults || {};
    const parameterKeys = ["laser_min_power", "laser_max_power", "feed_rate", "travel_rate", "pixel_size_mm", "threshold", "passes"];
    const state = { material: "", thickness: "", modeKey: "", selectedCell: null, cells: [], backendCalibrationId: "" };

    const els = {
      sourcePath: document.getElementById("sourcePath"),
      materialSearch: document.getElementById("materialSearch"),
      materialList: document.getElementById("materialList"),
      editorMaterial: document.getElementById("editorMaterial"),
      editorAliases: document.getElementById("editorAliases"),
      thicknessSelect: document.getElementById("thicknessSelect"),
      editorSource: document.getElementById("editorSource"),
      modeTabs: document.getElementById("modeTabs"),
      editorStatus: document.getElementById("editorStatus"),
      matrixModeSummary: document.getElementById("matrixModeSummary"),
      matrixPowerMinWrap: document.getElementById("matrixPowerMinWrap"),
      matrixPowerMinLabel: document.getElementById("matrixPowerMinLabel"),
      matrixPowerMaxLabel: document.getElementById("matrixPowerMaxLabel"),
      matrixColsLabel: document.getElementById("matrixColsLabel"),
      matrixSpeedMinLabel: document.getElementById("matrixSpeedMinLabel"),
      matrixSpeedMaxLabel: document.getElementById("matrixSpeedMaxLabel"),
      matrixRowsLabel: document.getElementById("matrixRowsLabel"),
      matrixPassesLabel: document.getElementById("matrixPassesLabel"),
      matrixPowerMin: document.getElementById("matrixPowerMin"),
      matrixPowerMax: document.getElementById("matrixPowerMax"),
      matrixSpeedMin: document.getElementById("matrixSpeedMin"),
      matrixSpeedMax: document.getElementById("matrixSpeedMax"),
      matrixCols: document.getElementById("matrixCols"),
      matrixRows: document.getElementById("matrixRows"),
      matrixCellSize: document.getElementById("matrixCellSize"),
      matrixPasses: document.getElementById("matrixPasses"),
      matrixHost: document.getElementById("matrixHost"),
      matrixTelnetPort: document.getElementById("matrixTelnetPort"),
      matrixAxis: document.getElementById("matrixAxis"),
      matrixPreview: document.getElementById("matrixPreview"),
      matrixLinks: document.getElementById("matrixLinks"),
      matrixStatus: document.getElementById("matrixStatus"),
      bestSummary: document.getElementById("bestSummary"),
      newMaterial: document.getElementById("newMaterial"),
      saveRecent: document.getElementById("saveRecent"),
      saveParams: document.getElementById("saveParams"),
      copyParams: document.getElementById("copyParams"),
      deleteParams: document.getElementById("deleteParams"),
      generateMatrix: document.getElementById("generateMatrix"),
      sendMatrix: document.getElementById("sendMatrix"),
      saveBest: document.getElementById("saveBest"),
    };

    function materialNames() {
      return Object.keys(materials).sort((a, b) => a.localeCompare(b, "zh-Hans-CN"));
    }

    function aliasesFor(name) {
      const aliases = materials[name]?.aliases;
      return Array.isArray(aliases) ? aliases : [];
    }

    function thicknessesFor(name) {
      const thicknesses = materials[name]?.thicknesses || {};
      return Object.keys(thicknesses).sort((a, b) => Number(a) - Number(b));
    }

    function hasParamEntry(value) {
      return value && typeof value === "object" && parameterKeys.some((key) => key in value);
    }

    function modesFor(name, thickness) {
      const modes = materials[name]?.thicknesses?.[thickness] || {};
      const result = [];
      const engrave = modes.engrave;
      if (hasParamEntry(engrave)) {
        result.push({ key: "engrave:raster", label: "雕刻 raster", laserMode: "engrave", strategy: "raster", params: engrave });
      } else if (engrave && typeof engrave === "object") {
        if (hasParamEntry(engrave.raster)) {
          result.push({ key: "engrave:raster", label: "雕刻 raster", laserMode: "engrave", strategy: "raster", params: engrave.raster });
        }
        if (hasParamEntry(engrave.outline)) {
          result.push({ key: "engrave:outline", label: "雕刻 outline", laserMode: "engrave", strategy: "outline", params: engrave.outline });
        }
      }
      if (hasParamEntry(modes.cut)) {
        result.push({ key: "cut:outline", label: "切割 cut", laserMode: "cut", strategy: "outline", params: modes.cut });
      }
      return result;
    }

    function modeDescriptor(modeKey = state.modeKey) {
      if (modeKey === "cut:outline") return { laserMode: "cut", strategy: "", label: "切割 cut" };
      if (modeKey === "engrave:outline") return { laserMode: "engrave", strategy: "outline", label: "雕刻 outline" };
      return { laserMode: "engrave", strategy: "raster", label: "雕刻 raster" };
    }

    function replaceMaterials(nextMaterials = {}) {
      for (const name of Object.keys(materials)) delete materials[name];
      Object.assign(materials, nextMaterials);
    }

    function writeLocalParams(material, thickness, modeKey, params, aliases = null) {
      const entry = materials[material] ||= { aliases: [], thicknesses: {} };
      if (aliases) entry.aliases = aliases;
      const modes = entry.thicknesses[thickness] ||= {};
      if (modeKey === "cut:outline") {
        modes.cut = { ...params };
      } else {
        const strategy = modeKey === "engrave:outline" ? "outline" : "raster";
        if (!modes.engrave || hasParamEntry(modes.engrave)) {
          modes.engrave = hasParamEntry(modes.engrave) ? { raster: modes.engrave } : {};
        }
        modes.engrave[strategy] = { ...params };
      }
    }

    function editorAliases() {
      return els.editorAliases.value.split(/[、，,]/).map((item) => item.trim()).filter(Boolean);
    }

    function editorParams() {
      const params = {};
      for (const key of parameterKeys) params[key] = numberValue(key, NaN);
      return params;
    }

    function editorPayload(overrides = {}) {
      const mode = overrides.mode || currentMode() || modeDescriptor();
      return {
        action: "save",
        material: overrides.material || els.editorMaterial.value.trim(),
        aliases: overrides.aliases ?? editorAliases(),
        thickness_mm: Number(overrides.thickness ?? state.thickness ?? els.thicknessSelect.value),
        laser_mode: mode.laserMode,
        engraving_mode: mode.laserMode === "cut" ? "" : mode.strategy,
        ...editorParams(),
        notes: document.getElementById("notes").value.trim(),
      };
    }

    function setStatus(el, text, tone = "") {
      el.className = tone ? `status ${tone}` : "status";
      el.textContent = text;
    }

    async function postJson(url, data) {
      const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      });
      const result = await response.json().catch(() => ({ success: false, result: "响应不是 JSON" }));
      if (!response.ok && result.success !== false) {
        result.success = false;
        result.result = `HTTP ${response.status}`;
      }
      return result;
    }

    function numberValue(id, fallback = 0) {
      const value = Number(document.getElementById(id).value);
      return Number.isFinite(value) ? value : fallback;
    }

    function writeParamInputs(params = {}) {
      for (const key of parameterKeys) {
        const input = document.getElementById(key);
        input.value = params[key] ?? "";
      }
      document.getElementById("notes").value = params.notes || "";
      els.editorSource.value = params.source || "";
    }

    function currentMode() {
      return modesFor(state.material, state.thickness).find((mode) => mode.key === state.modeKey);
    }

    function currentMatrixKind() {
      return currentMode()?.laserMode === "cut" ? "cut" : "engrave";
    }

    function renderMaterialList() {
      const query = els.materialSearch.value.trim().toLowerCase();
      els.materialList.innerHTML = "";
      const names = materialNames().filter((name) => {
        const haystack = [name, ...aliasesFor(name)].join(" ").toLowerCase();
        return !query || haystack.includes(query);
      });
      if (!names.length) {
        els.materialList.innerHTML = '<div class="status warn">没有匹配的材料。</div>';
        return;
      }
      for (const name of names) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = `material-row${name === state.material ? " active" : ""}`;
        const thicknessText = thicknessesFor(name).map((item) => `${item}mm`).join("、") || "无厚度";
        const aliasText = aliasesFor(name).slice(0, 5).join("、") || "无别名";
        const title = document.createElement("strong");
        title.textContent = name;
        const thicknessNode = document.createElement("small");
        thicknessNode.textContent = thicknessText;
        const aliasNode = document.createElement("small");
        aliasNode.textContent = aliasText;
        button.append(title, thicknessNode, aliasNode);
        button.addEventListener("click", () => selectMaterial(name));
        els.materialList.appendChild(button);
      }
    }

    function selectMaterial(name) {
      state.material = name;
      const thicknesses = thicknessesFor(name);
      state.thickness = thicknesses.includes(state.thickness) ? state.thickness : (thicknesses[0] || "");
      const modes = modesFor(name, state.thickness);
      state.modeKey = modes.some((mode) => mode.key === state.modeKey) ? state.modeKey : (modes[0]?.key || "");
      renderMaterialList();
      renderEditor();
      seedMatrixFromParams();
      buildMatrix();
    }

    function renderThicknessSelect() {
      els.thicknessSelect.innerHTML = "";
      for (const thickness of thicknessesFor(state.material)) {
        const option = document.createElement("option");
        option.value = thickness;
        option.textContent = `${thickness} mm`;
        option.selected = thickness === state.thickness;
        els.thicknessSelect.appendChild(option);
      }
    }

    function renderModeTabs() {
      els.modeTabs.innerHTML = "";
      const modes = modesFor(state.material, state.thickness);
      if (!modes.length) {
        els.modeTabs.innerHTML = '<div class="status warn">这个厚度还没有参数。</div>';
        return;
      }
      for (const mode of modes) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = `mode-tab${mode.key === state.modeKey ? " active" : ""}`;
        button.textContent = mode.label;
        button.addEventListener("click", () => {
          state.modeKey = mode.key;
          renderEditor();
          seedMatrixFromParams();
          buildMatrix();
        });
        els.modeTabs.appendChild(button);
      }
    }

    function renderEditor() {
      const entry = materials[state.material] || {};
      els.editorMaterial.value = state.material || "";
      els.editorAliases.value = aliasesFor(state.material).join("、");
      renderThicknessSelect();
      renderModeTabs();
      const mode = currentMode();
      writeParamInputs(mode?.params || {});
      const aliases = aliasesFor(state.material).slice(0, 6);
      const modeLabel = mode ? mode.label : "暂无模式";
      setStatus(
        els.editorStatus,
        `${entry.disabled ? "材料已禁用；" : ""}当前查看 ${state.material || "未选择"} / ${state.thickness || "-"}mm / ${modeLabel}。编辑后点击“保存参数”写入材料库。`,
        "good"
      );
      if (aliases.length) {
        const chipWrap = document.createElement("div");
        chipWrap.className = "chips";
        chipWrap.style.marginTop = "8px";
        for (const alias of aliases) {
          const chip = document.createElement("span");
          chip.className = "chip";
          chip.textContent = alias;
          chipWrap.appendChild(chip);
        }
        els.editorStatus.appendChild(chipWrap);
      }
    }

    function seedMatrixFromParams() {
      const params = currentMode()?.params || {};
      const kind = currentMatrixKind();
      const maxPower = Number(params.laser_max_power || 400);
      const speed = Number(params.feed_rate || 1200);
      const basePasses = Math.max(1, Math.round(Number(params.passes || 1)));
      if (kind === "cut") {
        els.matrixPowerMinWrap.hidden = true;
        els.matrixPowerMaxLabel.textContent = "固定功率 S";
        els.matrixColsLabel.textContent = "速度列数";
        els.matrixRowsLabel.textContent = "最大 passes";
        els.matrixPassesLabel.textContent = "起始 passes";
        els.matrixCols.max = 9;
        els.matrixRows.min = 1;
        els.matrixRows.max = 10;
        els.matrixPowerMax.value = maxPower;
        els.matrixSpeedMin.value = Math.max(30, Math.round(speed * 0.5));
        els.matrixSpeedMax.value = Math.max(31, Math.round(speed * 1.5));
        els.matrixCols.value = 5;
        els.matrixRows.value = Math.max(basePasses + 3, 4);
        els.matrixCellSize.value = 10;
        els.matrixPasses.value = 1;
        setStatus(
          els.matrixModeSummary,
          "切割方框矩阵：参考 LaserGRBL Cutting Test，固定功率，横轴试速度，纵轴试 passes。",
          "good"
        );
      } else {
        els.matrixPowerMinWrap.hidden = false;
        els.matrixPowerMinLabel.textContent = "功率起点 S";
        els.matrixPowerMaxLabel.textContent = "功率终点 S";
        els.matrixColsLabel.textContent = "功率列数";
        els.matrixRowsLabel.textContent = "速度行数";
        els.matrixPassesLabel.textContent = "填充线密度 line/mm";
        els.matrixCols.max = 10;
        els.matrixRows.min = 2;
        els.matrixRows.max = 10;
        const minPower = Math.max(0, Math.round(maxPower * 0.72));
        const highPower = Math.round(maxPower * 1.18);
        const slowSpeed = Math.max(60, Math.round(speed * 0.72));
        const fastSpeed = Math.round(speed * 1.28);
        els.matrixPowerMin.value = minPower;
        els.matrixPowerMax.value = highPower;
        els.matrixSpeedMin.value = slowSpeed;
        els.matrixSpeedMax.value = fastSpeed;
        els.matrixCols.value = 10;
        els.matrixRows.value = 7;
        els.matrixCellSize.value = 10;
        els.matrixPasses.value = 8;
        setStatus(
          els.matrixModeSummary,
          "雕刻填充矩阵：参考 LaserGRBL Power vs Speed Test，横轴功率，纵轴速度，格内显示扫描填充密度。",
          "good"
        );
      }
    }

    function lerp(start, end, index, count) {
      if (count <= 1) return start;
      return start + (end - start) * (index / (count - 1));
    }

    function clampInt(id, fallback, min, max) {
      const value = Math.round(numberValue(id, fallback));
      return Math.max(min, Math.min(max, value));
    }

    function clearMatrix(columns) {
      state.cells = [];
      state.selectedCell = null;
      state.backendCalibrationId = "";
      els.matrixPreview.innerHTML = "";
      els.matrixLinks.replaceChildren();
      els.matrixPreview.style.gridTemplateColumns = `repeat(${columns}, minmax(96px, 1fr))`;
      els.bestSummary.textContent = "尚未选择最佳格。";
    }

    function renderAxis(horizontal, vertical) {
      els.matrixAxis.replaceChildren();
      const h = document.createElement("div");
      h.innerHTML = "<b>横轴</b>";
      const hText = document.createElement("div");
      hText.textContent = horizontal;
      const v = document.createElement("div");
      v.innerHTML = "<b>纵轴</b>";
      const vText = document.createElement("div");
      vText.textContent = vertical;
      els.matrixAxis.append(h, hText, v, vText);
    }

    function buildMatrix() {
      if (currentMatrixKind() === "cut") {
        buildCutMatrix();
      } else {
        buildEngraveMatrix();
      }
    }

    function buildEngraveMatrix() {
      const cols = clampInt("matrixCols", 10, 2, 10);
      const rows = clampInt("matrixRows", 7, 2, 10);
      const powerMin = numberValue("matrixPowerMin", 280);
      const powerMax = numberValue("matrixPowerMax", 480);
      const speedMin = numberValue("matrixSpeedMin", 800);
      const speedMax = numberValue("matrixSpeedMax", 1800);
      const cellSize = numberValue("matrixCellSize", 10);
      const lineDensity = Math.max(1, Math.round(numberValue("matrixPasses", 8)));
      clearMatrix(cols);
      renderAxis("功率 S 从左到右递增", "速度 F 从上到下递增");
      let cellNo = 1;
      for (let row = 0; row < rows; row += 1) {
        const speed = Math.round(lerp(speedMin, speedMax, row, rows));
        for (let col = 0; col < cols; col += 1) {
          const power = Math.round(lerp(powerMin, powerMax, col, cols));
          const cell = { kind: "engrave", no: cellNo, power, speed, lineDensity, cellSize };
          state.cells.push(cell);
          const node = document.createElement("button");
          node.type = "button";
          node.className = "matrix-cell engrave-cell";
          const title = document.createElement("strong");
          title.textContent = `#${cellNo}`;
          const swatch = document.createElement("div");
          swatch.className = "fill-swatch";
          const denominator = Math.max(1, powerMax - powerMin);
          swatch.style.opacity = String(Math.max(0.22, Math.min(1, 0.25 + ((power - powerMin) / denominator) * 0.75)));
          const powerNode = document.createElement("span");
          powerNode.textContent = `S${power}`;
          const speedNode = document.createElement("span");
          speedNode.textContent = `F${speed}`;
          const densityNode = document.createElement("span");
          densityNode.textContent = `${lineDensity} line/mm`;
          node.append(title, swatch, powerNode, speedNode, densityNode);
          node.addEventListener("click", () => selectCell(cell, node));
          els.matrixPreview.appendChild(node);
          cellNo += 1;
        }
      }
      const width = cols * cellSize;
      const height = rows * cellSize;
      setStatus(
        els.matrixStatus,
        `已生成雕刻填充矩阵 ${rows} x ${cols}，约 ${width}mm x ${height}mm，填充密度 ${lineDensity} line/mm。`,
        "good"
      );
    }

    function buildCutMatrix() {
      const cols = clampInt("matrixCols", 5, 2, 9);
      const fixedPower = numberValue("matrixPowerMax", 800);
      const speedMin = numberValue("matrixSpeedMin", 100);
      const speedMax = numberValue("matrixSpeedMax", 600);
      const cellSize = numberValue("matrixCellSize", 10);
      const passStart = clampInt("matrixPasses", 1, 1, 10);
      const passEnd = Math.max(passStart, clampInt("matrixRows", 4, 1, 10));
      const rows = passEnd - passStart + 1;
      clearMatrix(cols);
      renderAxis("速度 F 从左到右递增", "passes 从上到下递增");
      let cellNo = 1;
      for (let row = 0; row < rows; row += 1) {
        const passes = passStart + row;
        for (let col = 0; col < cols; col += 1) {
          const speed = Math.round(lerp(speedMin, speedMax, col, cols));
          const cell = { kind: "cut", no: cellNo, power: fixedPower, speed, passes, cellSize };
          state.cells.push(cell);
          const node = document.createElement("button");
          node.type = "button";
          node.className = "matrix-cell cut-cell";
          const title = document.createElement("strong");
          title.textContent = `#${cellNo}`;
          const outline = document.createElement("div");
          outline.className = "cut-outline";
          outline.style.borderWidth = `${Math.min(4, 1 + passes * 0.45)}px`;
          const speedNode = document.createElement("span");
          speedNode.textContent = `F${speed}`;
          const passesNode = document.createElement("span");
          passesNode.textContent = `${passes} pass`;
          const powerNode = document.createElement("span");
          powerNode.textContent = `S${fixedPower}`;
          node.append(title, outline, speedNode, passesNode, powerNode);
          node.addEventListener("click", () => selectCell(cell, node));
          els.matrixPreview.appendChild(node);
          cellNo += 1;
        }
      }
      const width = cols * cellSize;
      const height = rows * cellSize;
      setStatus(
        els.matrixStatus,
        `已生成切割方框矩阵 ${rows} x ${cols}，约 ${width}mm x ${height}mm，固定功率 S${fixedPower}。`,
        "good"
      );
    }

    function currentMatrixPayload() {
      const mode = currentMode();
      const kind = currentMatrixKind();
      const material = (state.material || els.editorMaterial.value || "").trim();
      const thickness = Number(state.thickness || els.thicknessSelect.value || 0);
      const cols = kind === "cut" ? clampInt("matrixCols", 5, 2, 9) : clampInt("matrixCols", 10, 2, 10);
      const speedMin = Math.round(numberValue("matrixSpeedMin", kind === "cut" ? 100 : 800));
      const speedMax = Math.round(numberValue("matrixSpeedMax", kind === "cut" ? 600 : 1800));
      const cellSize = numberValue("matrixCellSize", 10);
      const base = {
        material,
        thickness_mm: thickness,
        laser_mode: mode?.laserMode || (kind === "cut" ? "cut" : "engrave"),
        engraving_mode: kind === "cut" ? "" : (mode?.strategy || "raster"),
        matrix_kind: kind,
        columns: cols,
        speed_min: speedMin,
        speed_max: speedMax,
        cell_size_mm: cellSize,
        gap_mm: 0,
        x0: 0,
        y0: 0,
        network_host: els.matrixHost.value.trim(),
        network_telnet_port: Math.round(numberValue("matrixTelnetPort", 23)),
        network_transport: "telnet",
      };
      if (kind === "cut") {
        const passStart = clampInt("matrixPasses", 1, 1, 10);
        const passEnd = Math.max(passStart, clampInt("matrixRows", 4, 1, 10));
        return {
          ...base,
          rows: passEnd - passStart + 1,
          power_min: Math.round(numberValue("matrixPowerMax", 800)),
          power_max: Math.round(numberValue("matrixPowerMax", 800)),
          pass_min: passStart,
          pass_max: passEnd,
          passes: passStart,
          fill_step_mm: 0.8,
        };
      }
      const lineDensity = Math.max(1, Math.round(numberValue("matrixPasses", 8)));
      return {
        ...base,
        rows: clampInt("matrixRows", 7, 2, 10),
        power_min: Math.round(numberValue("matrixPowerMin", 280)),
        power_max: Math.round(numberValue("matrixPowerMax", 480)),
        passes: 1,
        fill_step_mm: 1 / lineDensity,
      };
    }

    function renderMatrixLinks(web = {}) {
      els.matrixLinks.replaceChildren();
      const links = [
        ["路径预览", web.gcode_preview_url],
        ["下载 G-code", web.gcode_download_url],
        ["调参会话", web.calibration_session_url],
      ].filter(([, url]) => Boolean(url));
      for (const [label, url] of links) {
        const link = document.createElement("a");
        link.href = url;
        link.textContent = label;
        els.matrixLinks.appendChild(link);
      }
    }

    async function generateMatrixPreview() {
      buildMatrix();
      const data = currentMatrixPayload();
      if (!data.material || !data.thickness_mm) {
        setStatus(els.matrixStatus, "请先选择材料和厚度。", "warn");
        return;
      }
      els.generateMatrix.disabled = true;
      setStatus(els.matrixStatus, "正在生成后端测试矩阵 G-code...", "warn");
      try {
        const result = await postJson("/api/material-lab/preview", data);
        if (!result.success) {
          setStatus(els.matrixStatus, result.result || "生成测试矩阵失败。", "warn");
          return;
        }
        const body = result.result || {};
        state.backendCalibrationId = body.calibration_id || "";
        renderMatrixLinks(body.web || {});
        const stats = body.stats || {};
        const sizeText = stats.width_mm && stats.height_mm ? `，尺寸约 ${stats.width_mm} x ${stats.height_mm}mm` : "";
        setStatus(
          els.matrixStatus,
          `已生成后端测试矩阵会话 ${state.backendCalibrationId || "-"}，共 ${stats.cell_count || state.cells.length} 格${sizeText}。`,
          "good"
        );
      } catch (error) {
        setStatus(els.matrixStatus, `生成测试矩阵失败：${error.message || error}`, "warn");
      } finally {
        els.generateMatrix.disabled = false;
      }
    }

    async function sendMatrix() {
      if (!state.backendCalibrationId) {
        setStatus(els.matrixStatus, "请先生成测试矩阵预览，拿到后端调参会话。", "warn");
        return;
      }
      const host = els.matrixHost.value.trim();
      if (!host) {
        setStatus(els.matrixStatus, "请先填写设备 host/IP。", "warn");
        return;
      }
      const ok = window.confirm(`确认通过网络发送测试矩阵到 ${host} 吗？请确认机器、材料和防护都已准备好。`);
      if (!ok) return;
      els.sendMatrix.disabled = true;
      setStatus(els.matrixStatus, "正在发送测试矩阵到设备...", "warn");
      try {
        const result = await postJson("/api/material-lab/send", {
          calibration_id: state.backendCalibrationId,
          confirmed: true,
          network_host: host,
          network_telnet_port: Math.round(numberValue("matrixTelnetPort", 23)),
          network_transport: "telnet",
          run_in_background: true,
          wait_for_response: true,
        });
        if (!result.success) {
          setStatus(els.matrixStatus, result.result || "发送测试矩阵失败。", "warn");
          return;
        }
        const sendResult = result.result?.send_result || {};
        setStatus(els.matrixStatus, sendResult.job_id ? `测试矩阵已开始发送，任务 ${sendResult.job_id}。` : "测试矩阵发送请求已提交。", "good");
      } catch (error) {
        setStatus(els.matrixStatus, `发送失败：${error.message || error}`, "warn");
      } finally {
        els.sendMatrix.disabled = false;
      }
    }

    function selectCell(cell, node) {
      state.selectedCell = cell;
      for (const item of els.matrixPreview.querySelectorAll(".matrix-cell")) {
        item.classList.remove("selected");
      }
      node.classList.add("selected");
      els.bestSummary.replaceChildren();
      const title = document.createElement("strong");
      title.textContent = `最佳格：第 ${cell.no} 格`;
      const params = document.createElement("span");
      if (cell.kind === "cut") {
        params.textContent = `切割：S${cell.power} / F${cell.speed} / ${cell.passes} pass。`;
      } else {
        params.textContent = `雕刻：S${cell.power} / F${cell.speed} / ${cell.lineDensity} line/mm。`;
      }
      const saveHint = document.createElement("span");
      saveHint.textContent = `保存后会成为 ${state.material} ${state.thickness}mm 当前模式的推荐参数。`;
      els.bestSummary.append(title, params, saveHint);
      setStatus(els.matrixStatus, `已选择第 ${cell.no} 格。生成过后端会话后即可保存为材料推荐参数。`, "good");
    }

    async function saveBestCell() {
      if (!state.selectedCell) {
        setStatus(els.matrixStatus, "请先点选效果最好的格子。", "warn");
        return;
      }
      if (!state.backendCalibrationId) {
        setStatus(els.matrixStatus, "请先生成测试矩阵预览，保存需要后端调参会话。", "warn");
        return;
      }
      const mode = currentMode();
      const ok = window.confirm(`确认把第 ${state.selectedCell.no} 格保存为 ${state.material} ${state.thickness}mm 当前模式推荐参数吗？`);
      if (!ok) return;
      els.saveBest.disabled = true;
      setStatus(els.matrixStatus, "正在保存最佳格到材料库...", "warn");
      try {
        const result = await postJson("/api/material-lab/select-cell", {
          calibration_id: state.backendCalibrationId,
          cell_number: state.selectedCell.no,
          material: state.material,
          thickness_mm: Number(state.thickness || 0),
          laser_mode: mode?.laserMode || "",
          engraving_mode: mode?.laserMode === "cut" ? "" : (mode?.strategy || ""),
          notes: document.getElementById("notes").value || `Web 测试矩阵第 ${state.selectedCell.no} 格`,
        });
        if (!result.success) {
          setStatus(els.matrixStatus, result.result || "保存最佳格失败。", "warn");
          return;
        }
        const params = result.result?.params || {};
        writeLocalParams(state.material, state.thickness, state.modeKey, params);
        writeParamInputs(params);
        setStatus(els.matrixStatus, `已保存第 ${state.selectedCell.no} 格到材料库。`, "good");
        setStatus(els.editorStatus, `材料库已更新：S${params.laser_max_power ?? "-"} / F${params.feed_rate ?? "-"} / ${params.passes ?? "-"} pass。`, "good");
        notifyMaterialsUpdated();
      } catch (error) {
        setStatus(els.matrixStatus, `保存失败：${error.message || error}`, "warn");
      } finally {
        els.saveBest.disabled = false;
      }
    }

    function notifyMaterialsUpdated() {
      try {
        if (typeof BroadcastChannel !== "function") return;
        const channel = new BroadcastChannel("laser-material-library-v1");
        channel.postMessage({ type: "materials-updated" });
        channel.close();
      } catch (error) {
        // BroadcastChannel unavailable: keep manual/focus refresh fallbacks.
      }
    }

    async function saveMaterialPayload(data, successMessage) {
      if (!data.material || !data.thickness_mm) {
        setStatus(els.editorStatus, "材料名和大于 0 的厚度不能为空。", "warn");
        return false;
      }
      const invalidKey = parameterKeys.find((key) => !Number.isFinite(data[key]));
      if (invalidKey) {
        setStatus(els.editorStatus, `${invalidKey} 必须填写有效数字。`, "warn");
        return false;
      }
      setStatus(els.editorStatus, "正在写入材料库...", "warn");
      const result = await postJson("/api/material-lab/manage", data);
      if (!result.success) {
        setStatus(els.editorStatus, result.result || "保存参数失败。", "warn");
        return false;
      }
      replaceMaterials(result.result?.materials || {});
      state.material = result.result?.material || data.material;
      state.thickness = String(result.result?.thickness_mm ?? data.thickness_mm);
      state.modeKey = data.laser_mode === "cut" ? "cut:outline" : `engrave:${data.engraving_mode || "raster"}`;
      selectMaterial(state.material);
      setStatus(els.editorStatus, successMessage, "good");
      notifyMaterialsUpdated();
      return true;
    }

    async function saveCurrentParams() {
      await saveMaterialPayload(editorPayload(), "参数已保存到材料库。左侧列表和当前页面数据已同步更新。");
    }

    function newMaterialDraft() {
      const material = window.prompt("新材料名称：", "");
      if (!material?.trim()) return;
      const thickness = Number(window.prompt("材料厚度 mm：", "1"));
      if (!Number.isFinite(thickness) || thickness <= 0) {
        setStatus(els.editorStatus, "材料厚度必须大于 0。", "warn");
        return;
      }
      const strategy = (window.prompt("模式：raster、outline 或 cut", "raster") || "").trim().toLowerCase();
      if (!["raster", "outline", "cut"].includes(strategy)) {
        setStatus(els.editorStatus, "模式必须是 raster、outline 或 cut。", "warn");
        return;
      }
      const modeKey = strategy === "cut" ? "cut:outline" : `engrave:${strategy}`;
      const params = {
        laser_min_power: 0,
        laser_max_power: 800,
        feed_rate: 1200,
        travel_rate: 3000,
        pixel_size_mm: 0.1,
        threshold: strategy === "cut" ? 128 : -1,
        passes: 1,
        source: "manual",
      };
      const name = material.trim();
      const thicknessKey = String(thickness);
      writeLocalParams(name, thicknessKey, modeKey, params, []);
      state.material = name;
      state.thickness = thicknessKey;
      state.modeKey = modeKey;
      selectMaterial(name);
      setStatus(els.editorStatus, "新材料草稿已建立。请核对参数后点击“保存参数”写入材料库。", "warn");
    }

    async function copyCurrentParams() {
      const mode = currentMode();
      if (!mode) {
        setStatus(els.editorStatus, "请先选择一套可复制的参数。", "warn");
        return;
      }
      const material = window.prompt("复制到材料：", `${state.material} 副本`);
      if (!material?.trim()) return;
      const thickness = Number(window.prompt("复制到厚度 mm：", state.thickness));
      if (!Number.isFinite(thickness) || thickness <= 0) {
        setStatus(els.editorStatus, "目标厚度必须大于 0。", "warn");
        return;
      }
      await saveMaterialPayload(
        editorPayload({ material: material.trim(), thickness, aliases: [], mode }),
        `参数已复制到 ${material.trim()} ${thickness}mm。`
      );
    }

    async function deleteCurrentParams() {
      const mode = currentMode();
      if (!mode) {
        setStatus(els.editorStatus, "当前没有可删除的参数。", "warn");
        return;
      }
      const ok = window.confirm(`确认删除 ${state.material} ${state.thickness}mm / ${mode.label} 这一套参数吗？其他厚度和模式不会删除。`);
      if (!ok) return;
      const result = await postJson("/api/material-lab/manage", {
        action: "delete",
        confirmed: true,
        material: state.material,
        thickness_mm: Number(state.thickness),
        laser_mode: mode.laserMode,
        engraving_mode: mode.laserMode === "cut" ? "" : mode.strategy,
      });
      if (!result.success) {
        setStatus(els.editorStatus, result.result || "删除参数失败。", "warn");
        return;
      }
      replaceMaterials(result.result?.materials || {});
      notifyMaterialsUpdated();
      const nextMaterial = materials[state.material] ? state.material : (materialNames()[0] || "");
      if (nextMaterial) {
        state.thickness = "";
        state.modeKey = "";
        selectMaterial(nextMaterial);
        setStatus(els.editorStatus, "当前参数已删除；其他材料、厚度和模式未受影响。", "good");
      } else {
        state.material = "";
        renderMaterialList();
        renderEditor();
        setStatus(els.editorStatus, "当前参数已删除，材料库中没有可显示参数。", "good");
      }
    }

    async function saveFromRecentTask() {
      setStatus(els.editorStatus, "正在读取最近 workflow 参数...", "warn");
      const recent = await postJson("/api/material-lab/recent", {});
      if (!recent.success) {
        setStatus(els.editorStatus, recent.result || "最近任务没有可保存参数。", "warn");
        return;
      }
      const data = recent.result;
      const modeText = data.laser_mode === "cut" ? "cut" : data.engraving_mode;
      const ok = window.confirm(
        `确认保存最近 workflow ${data.workflow_id} 的参数吗？\n${data.material} ${data.thickness_mm}mm / ${modeText}\nS${data.params.laser_max_power} F${data.params.feed_rate}`
      );
      if (!ok) {
        setStatus(els.editorStatus, "已取消从最近任务保存。", "warn");
        return;
      }
      await saveMaterialPayload(
        {
          action: "save",
          material: data.material,
          aliases: aliasesFor(data.material),
          thickness_mm: data.thickness_mm,
          laser_mode: data.laser_mode,
          engraving_mode: data.engraving_mode,
          ...data.params,
          notes: `Saved from Web recent workflow ${data.workflow_id}.`,
        },
        `最近 workflow ${data.workflow_id} 的参数已保存。`
      );
    }

    function markMatrixDirty() {
      if (!state.backendCalibrationId) return;
      state.backendCalibrationId = "";
      els.matrixLinks.replaceChildren();
      setStatus(els.matrixStatus, "矩阵参数已调整，请重新生成测试矩阵预览后再发送或保存。", "warn");
    }

    els.materialSearch.addEventListener("input", renderMaterialList);
    els.thicknessSelect.addEventListener("change", () => {
      state.thickness = els.thicknessSelect.value;
      state.modeKey = modesFor(state.material, state.thickness)[0]?.key || "";
      renderEditor();
      seedMatrixFromParams();
      buildMatrix();
    });
    els.generateMatrix.addEventListener("click", generateMatrixPreview);
    els.sendMatrix.addEventListener("click", sendMatrix);
    els.saveBest.addEventListener("click", saveBestCell);
    els.newMaterial.addEventListener("click", newMaterialDraft);
    els.saveRecent.addEventListener("click", saveFromRecentTask);
    els.saveParams.addEventListener("click", saveCurrentParams);
    els.copyParams.addEventListener("click", copyCurrentParams);
    els.deleteParams.addEventListener("click", deleteCurrentParams);
    for (const id of [
      "matrixPowerMin",
      "matrixPowerMax",
      "matrixSpeedMin",
      "matrixSpeedMax",
      "matrixCols",
      "matrixRows",
      "matrixCellSize",
      "matrixPasses",
    ]) {
      document.getElementById(id).addEventListener("input", markMatrixDirty);
    }
    els.matrixHost.value = localStorage.getItem("materialLabNetworkHost") || defaults.network_host || "";
    els.matrixTelnetPort.value = localStorage.getItem("materialLabTelnetPort") || defaults.network_telnet_port || "23";
    els.matrixHost.addEventListener("change", () => localStorage.setItem("materialLabNetworkHost", els.matrixHost.value.trim()));
    els.matrixTelnetPort.addEventListener("change", () => localStorage.setItem("materialLabTelnetPort", els.matrixTelnetPort.value.trim()));

    els.sourcePath.textContent = payload.error ? `材料库读取提示：${payload.error}` : `来源：${payload.source_path || "默认材料库"}`;
    const firstMaterial = materialNames()[0] || "";
    if (firstMaterial) {
      selectMaterial(firstMaterial);
    } else {
      setStatus(els.editorStatus, "没有可用材料。", "warn");
      setStatus(els.matrixStatus, "没有可用材料，无法生成矩阵。", "warn");
    }
  </script>
</body>
</html>
"""


def material_lab_snapshot(params_file=None):
    source_path = Path(params_file or laser_material_calibration_tool.MATERIAL_PARAMS_FILE)
    data, error = laser_material_calibration_tool._load_material_params(str(source_path))
    if error:
        data = {"version": 1, "materials": {}}
    materials = data.get("materials") if isinstance(data, dict) else {}
    if not isinstance(materials, dict):
        materials = {}
    return {
        "version": data.get("version", 1) if isinstance(data, dict) else 1,
        "source_path": str(source_path),
        "materials": materials,
        "error": error,
        "prototype": False,
        "defaults": {
            "network_host": laser_network_grbl_tool.DEFAULT_NETWORK_HOST,
            "network_telnet_port": laser_network_grbl_tool.DEFAULT_TELNET_PORT,
        },
    }


def _ui_thickness_value(raw):
    """Return a display thickness number or None if invalid."""
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    if abs(number - round(number)) < 1e-9:
        return int(round(number))
    return number


def project_ui_material_options(materials):
    """Safe projection for Web dropdown: [{name, thicknesses}] only."""
    projected = []
    if not isinstance(materials, dict):
        return {"materials": projected}
    for raw_name, entry in materials.items():
        name = str(raw_name).strip() if raw_name is not None else ""
        if not name:
            continue
        thicknesses_src = entry.get("thicknesses") if isinstance(entry, dict) else None
        if not isinstance(thicknesses_src, dict):
            continue
        deduped = {}
        for thickness_key in thicknesses_src.keys():
            value = _ui_thickness_value(thickness_key)
            if value is None:
                continue
            deduped[float(value)] = value
        if not deduped:
            continue
        ordered = [deduped[key] for key in sorted(deduped.keys())]
        projected.append({"name": name, "thicknesses": ordered})
    projected.sort(key=lambda item: item["name"])
    return {"materials": projected}


def ui_material_options_response(params_file=None):
    """Build success/failure envelope for GET /api/ui/material-options."""
    try:
        snapshot = material_lab_snapshot(params_file=params_file)
        if not isinstance(snapshot, dict) or snapshot.get("error"):
            # Fixed short copy only — never echo raw loader errors/paths.
            return _build_failure("读取材料选项失败", status=500)
        materials = snapshot.get("materials") or {}
        return _build_success(project_ui_material_options(materials))
    except Exception:
        return _build_failure("读取材料选项失败", status=500)


def project_ui_material_recommendation(recommendation):
    """Whitelist projection for Web read-only recommendation summary."""
    if not isinstance(recommendation, dict):
        return None
    params = recommendation.get("params") if isinstance(recommendation.get("params"), dict) else {}
    projected = {
        "material": recommendation.get("material"),
        "thickness_mm": recommendation.get("thickness_mm"),
        "matched_thickness_mm": recommendation.get("matched_thickness_mm"),
        "match": recommendation.get("match"),
        "laser_mode": recommendation.get("laser_mode"),
        "laser_max_power": params.get("laser_max_power"),
        "feed_rate": params.get("feed_rate"),
        "passes": params.get("passes"),
    }
    if projected.get("laser_mode") == "engrave" and recommendation.get("engraving_mode"):
        projected["engraving_mode"] = recommendation.get("engraving_mode")
    return projected


def ui_material_recommendation_response(
    material,
    thickness_mm,
    laser_mode,
    engraving_mode="",
    params_file=None,
):
    """Build success/failure envelope for GET /api/ui/material-recommendation."""
    material_name = str(material or "").strip()
    mode = str(laser_mode or "").strip().lower()
    strategy = str(engraving_mode or "").strip().lower()
    if not material_name or not mode:
        return _build_failure("读取材料推荐失败", status=400)
    try:
        thickness_value = float(thickness_mm)
    except (TypeError, ValueError):
        return _build_failure("读取材料推荐失败", status=400)
    if not math.isfinite(thickness_value) or thickness_value <= 0:
        return _build_failure("读取材料推荐失败", status=400)
    try:
        # Resolve path at call time so tests can patch MATERIAL_PARAMS_FILE.
        resolved_params_file = (
            params_file
            if params_file is not None
            else laser_material_calibration_tool.MATERIAL_PARAMS_FILE
        )
        raw = laser_material_calibration_tool.recommend_laser_params(
            material=material_name,
            thickness_mm=thickness_value,
            laser_mode=mode,
            engraving_mode=strategy,
            params_file=resolved_params_file,
        )
        if not isinstance(raw, dict) or not raw.get("success"):
            return _build_failure("读取材料推荐失败", status=400)
        projected = project_ui_material_recommendation(raw.get("result") or {})
        if not projected:
            return _build_failure("读取材料推荐失败", status=400)
        return _build_success(projected)
    except Exception:
        return _build_failure("读取材料推荐失败", status=500)


def _json_for_html_script(payload):
    return json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")


def render_material_lab_html(params_file=None):
    payload = _json_for_html_script(material_lab_snapshot(params_file=params_file))
    return MATERIAL_LAB_HTML.replace("__MATERIAL_LAB_DATA__", payload)


def _material_lab_aliases(payload):
    aliases = payload.get("aliases", [])
    if isinstance(aliases, str):
        aliases = aliases.replace("，", ",").replace("、", ",").split(",")
    if not isinstance(aliases, list):
        return None, "aliases 必须是数组或逗号分隔文本"
    return [str(alias).strip() for alias in aliases if str(alias).strip()], None


def material_lab_manage_from_payload(payload, params_file=None):
    action = _optional_text(payload, "action").lower()
    if action not in {"save", "delete"}:
        return _build_failure("action 必须是 save 或 delete")

    material, error = _required_text(payload, "material")
    if error:
        return _build_failure(error)
    thickness_mm, error = _coerce_float(payload, "thickness_mm")
    if error:
        return _build_failure(error)
    laser_mode = _optional_text(payload, "laser_mode", "engrave") or "engrave"
    engraving_mode = _optional_text(payload, "engraving_mode", "raster") or "raster"
    target_file = str(params_file or laser_material_calibration_tool.MATERIAL_PARAMS_FILE)

    if action == "delete":
        if not _coerce_bool(payload, "confirmed", False):
            return _build_failure("删除当前参数需要 confirmed=true")
        result = laser_material_calibration_tool.material_params(
            action="delete",
            material=material,
            thickness_mm=thickness_mm,
            laser_mode=laser_mode,
            engraving_mode=engraving_mode,
            params_file=target_file,
        )
    else:
        aliases, error = _material_lab_aliases(payload)
        if error:
            return _build_failure(error)
        result = laser_material_calibration_tool.material_params(
            action="save",
            material=material,
            thickness_mm=thickness_mm,
            laser_mode=laser_mode,
            engraving_mode=engraving_mode,
            laser_min_power=payload.get("laser_min_power", 0),
            laser_max_power=payload.get("laser_max_power", 800),
            feed_rate=payload.get("feed_rate", 1200),
            travel_rate=payload.get("travel_rate", laser_material_calibration_tool.DEFAULT_TRAVEL_RATE),
            pixel_size_mm=payload.get("pixel_size_mm", 0.1),
            threshold=payload.get("threshold", -1),
            passes=payload.get("passes", 1),
            aliases_json=json.dumps(aliases, ensure_ascii=False),
            notes=_optional_text(payload, "notes"),
            params_file=target_file,
        )
    if not result.get("success"):
        return result

    if action == "save":
        saved_material = str((result.get("result") or {}).get("material") or material)
        data, error = laser_material_calibration_tool._load_material_params(target_file)
        if error:
            return _build_failure(error)
        saved_entry = data.get("materials", {}).get(saved_material)
        if isinstance(saved_entry, dict):
            saved_entry["aliases"] = aliases
            error = laser_material_calibration_tool._save_material_params(data, target_file)
            if error:
                return _build_failure(error)

    snapshot = material_lab_snapshot(params_file=target_file)
    return _build_success({**(result.get("result") or {}), "materials": snapshot["materials"]})


def material_lab_recent_params(workflows_dir=None):
    workflows = laser_workflow_tool._list_workflows(workflows_dir=workflows_dir)
    workflows.sort(key=lambda workflow: workflow.get("updated_at") or 0, reverse=True)
    for workflow in workflows:
        input_fields = _nested_dict(workflow, "input")
        preview = _nested_dict(workflow, "last_preview_result")
        recommendation = _nested_dict(preview, "recommendation")
        params = _first_dict(preview.get("params"), recommendation.get("params"), _nested_dict(workflow, "artifacts").get("params"))
        material = str(input_fields.get("material") or recommendation.get("material") or "").strip()
        try:
            thickness_mm = float(input_fields.get("thickness_mm") or recommendation.get("thickness_mm") or 0)
        except (TypeError, ValueError):
            continue
        if not material or thickness_mm <= 0 or not params:
            continue
        if not laser_material_calibration_tool.PARAMETER_KEYS.issubset(params):
            continue
        normalized, error = laser_material_calibration_tool._normalize_params(params, require_all=False)
        if error:
            continue
        laser_mode = str(input_fields.get("laser_mode") or recommendation.get("laser_mode") or "engrave")
        engraving_mode = str(input_fields.get("engraving_mode") or recommendation.get("engraving_mode") or "raster")
        if laser_mode == "cut":
            engraving_mode = ""
        return _build_success(
            {
                "workflow_id": workflow.get("workflow_id"),
                "material": material,
                "thickness_mm": thickness_mm,
                "laser_mode": laser_mode,
                "engraving_mode": engraving_mode,
                "params": normalized,
            }
        )
    return _build_failure("最近的激光 workflow 中没有可保存的完整材料参数")


def _matrix_kind_from_payload(payload, laser_mode):
    requested = _optional_text(payload, "matrix_kind", "auto").lower()
    if requested in {"", "auto"}:
        return "cut" if laser_mode == "cut" else "engrave", None
    if requested in {"raster", "outline", "engraving"}:
        return "engrave", None
    if requested in {"engrave", "cut"}:
        return requested, None
    return None, "matrix_kind 必须是 engrave 或 cut"


def _material_lab_matrix_kwargs(payload, params_file=None):
    material, error = _required_text(payload, "material")
    if error:
        return None, error
    thickness_mm, error = _coerce_float(payload, "thickness_mm")
    if error:
        return None, error
    if thickness_mm <= 0:
        return None, "thickness_mm 必须大于 0"

    laser_mode = _optional_text(payload, "laser_mode", "engrave") or "engrave"
    if laser_mode not in {"engrave", "cut"}:
        return None, "laser_mode 必须是 engrave 或 cut"
    engraving_mode = _optional_text(payload, "engraving_mode", "raster") or "raster"
    if laser_mode == "cut":
        engraving_mode = ""
    matrix_kind, error = _matrix_kind_from_payload(payload, laser_mode)
    if error:
        return None, error

    numeric_ints = {}
    for key, default in (
        ("rows", 5),
        ("columns", 5),
        ("power_min", -1),
        ("power_max", -1),
        ("speed_min", -1),
        ("speed_max", -1),
        ("passes", 0),
        ("pass_min", 1),
        ("pass_max", 0),
        ("network_http_port", laser_network_grbl_tool.DEFAULT_HTTP_PORT),
        ("network_telnet_port", laser_network_grbl_tool.DEFAULT_TELNET_PORT),
    ):
        value, error = _coerce_int(payload, key, default)
        if error:
            return None, error
        numeric_ints[key] = value

    numeric_floats = {}
    for key, default in (
        ("cell_size_mm", 5.0),
        ("gap_mm", 2.0),
        ("x0", 0.0),
        ("y0", 0.0),
        ("fill_step_mm", 0.8),
        ("network_timeout", laser_network_grbl_tool.DEFAULT_TIMEOUT),
    ):
        value, error = _coerce_float(payload, key, default)
        if error:
            return None, error
        numeric_floats[key] = value

    return {
        "material": material,
        "thickness_mm": thickness_mm,
        "laser_mode": laser_mode,
        "engraving_mode": engraving_mode,
        "matrix_kind": matrix_kind,
        "output_file": "",
        "confirmed": False,
        "dry_run": False,
        "connection_mode": "network",
        "network_host": _optional_text(payload, "network_host", ""),
        "network_transport": _optional_text(payload, "network_transport", laser_material_calibration_tool.DEFAULT_NETWORK_TRANSPORT)
        or laser_material_calibration_tool.DEFAULT_NETWORK_TRANSPORT,
        "wait_for_response": _coerce_bool(payload, "wait_for_response", True),
        "run_in_background": _coerce_bool(payload, "run_in_background", True),
        "params_file": str(params_file or laser_material_calibration_tool.MATERIAL_PARAMS_FILE),
        **numeric_ints,
        **numeric_floats,
    }, None


def _augment_material_lab_links(result):
    result = _augment_web_links(result)
    if not result.get("success"):
        return result
    payload = dict(result.get("result") or {})
    web = dict(payload.get("web") or {})
    session_file = payload.get("session_file")
    if session_file:
        web["calibration_session_url"] = _preview_url(session_file)
    if web:
        payload["web"] = web
    return {**result, "result": payload}


def material_lab_preview_from_payload(payload, calibration_runner=None, params_file=None):
    kwargs, error = _material_lab_matrix_kwargs(payload, params_file=params_file)
    if error:
        return _build_failure(error)
    runner = calibration_runner or laser_material_calibration_tool.run_calibration_grid
    result = runner(**kwargs)
    return _augment_material_lab_links(result)


def _material_lab_network_kwargs(payload):
    calibration_id, error = _required_text(payload, "calibration_id")
    if error:
        return None, error
    if not _coerce_bool(payload, "confirmed", False):
        return None, "发送测试矩阵需要 confirmed=true"
    network_host = _optional_text(payload, "network_host", "")
    if not network_host and not laser_network_grbl_tool.DEFAULT_NETWORK_HOST:
        return None, "请填写设备 host/IP"
    network_http_port, error = _coerce_int(payload, "network_http_port", laser_network_grbl_tool.DEFAULT_HTTP_PORT)
    if error:
        return None, error
    network_telnet_port, error = _coerce_int(payload, "network_telnet_port", laser_network_grbl_tool.DEFAULT_TELNET_PORT)
    if error:
        return None, error
    network_timeout, error = _coerce_float(payload, "network_timeout", laser_network_grbl_tool.DEFAULT_TIMEOUT)
    if error:
        return None, error
    return {
        "calibration_id": calibration_id,
        "confirmed": True,
        "connection_mode": "network",
        "network_host": network_host,
        "network_transport": _optional_text(payload, "network_transport", laser_material_calibration_tool.DEFAULT_NETWORK_TRANSPORT)
        or laser_material_calibration_tool.DEFAULT_NETWORK_TRANSPORT,
        "network_http_port": network_http_port,
        "network_telnet_port": network_telnet_port,
        "network_timeout": network_timeout,
        "wait_for_response": _coerce_bool(payload, "wait_for_response", True),
        "run_in_background": _coerce_bool(payload, "run_in_background", True),
    }, None


def material_lab_send_from_payload(payload, calibration_runner=None):
    kwargs, error = _material_lab_network_kwargs(payload)
    if error:
        return _build_failure(error)
    runner = calibration_runner or laser_material_calibration_tool.run_calibration_grid
    result = runner(**kwargs)
    return _augment_material_lab_links(result)


def material_lab_select_cell_from_payload(payload, selector=None, params_file=None):
    calibration_id, error = _required_text(payload, "calibration_id")
    if error:
        return _build_failure(error)
    cell_number, error = _coerce_int(payload, "cell_number", 0)
    if error:
        return _build_failure(error)
    thickness_mm, error = _coerce_float(payload, "thickness_mm", 0.0)
    if error:
        return _build_failure(error)
    select = selector or laser_material_calibration_tool.select_calibration_cell
    return select(
        calibration_id=calibration_id,
        cell_number=cell_number,
        material=_optional_text(payload, "material", ""),
        thickness_mm=thickness_mm,
        laser_mode=_optional_text(payload, "laser_mode", ""),
        engraving_mode=_optional_text(payload, "engraving_mode", ""),
        notes=_optional_text(payload, "notes", ""),
        params_file=str(params_file or laser_material_calibration_tool.MATERIAL_PARAMS_FILE),
    )


class LaserWebRequestHandler(BaseHTTPRequestHandler):
    server_version = "LaserWebText/1.0"

    def _send_no_cache_headers(self):
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")

    def _send_json(self, payload, status=None):
        status_code = int(status or payload.get("status") or 200)
        body = json.dumps({k: v for k, v in payload.items() if k != "status"}, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._send_no_cache_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html_body=None):
        body = (html_body if html_body is not None else load_main_index_html()).encode("utf-8")
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
            return None, "请求体过大"
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
        if parsed.path == "/":
            self._send_html()
            return
        v2_static = _v2_static_file_for_path(parsed.path)
        if v2_static is not None:
            content_type = mimetypes.guess_type(str(v2_static))[0] or "application/octet-stream"
            body = v2_static.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self._send_no_cache_headers()
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/material-lab":
            self._send_html(render_material_lab_html())
            return
        if parsed.path == "/api/health":
            self._send_json(_build_success({"status": "ok"}))
            return
        if parsed.path == "/api/ui/runtime-config":
            self._send_json(ui_runtime_config_response())
            return
        if parsed.path == "/api/ui/serial-ports":
            self._send_json(ui_serial_ports_response())
            return
        if parsed.path == "/api/ui/material-options":
            self._send_json(ui_material_options_response())
            return
        if parsed.path == "/api/ui/material-recommendation":
            query = parse_qs(parsed.query)
            material = (query.get("material") or [""])[0]
            thickness_raw = (query.get("thickness_mm") or [""])[0]
            laser_mode = (query.get("laser_mode") or [""])[0]
            engraving_mode = (query.get("engraving_mode") or [""])[0]
            self._send_json(
                ui_material_recommendation_response(
                    material=material,
                    thickness_mm=thickness_raw,
                    laser_mode=laser_mode,
                    engraving_mode=engraving_mode,
                )
            )
            return
        draw_static = _draw_static_file_for_path(parsed.path)
        if draw_static is not None:
            content_type = mimetypes.guess_type(str(draw_static))[0] or "application/octet-stream"
            body = draw_static.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self._send_no_cache_headers()
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/preview":
            query = parse_qs(parsed.query)
            file_path = (query.get("path") or [""])[0]
            download = _query_flag(query, "download")
            download_filename = (query.get("filename") or [""])[0]
            resolved, error = resolve_preview_file(file_path)
            if error:
                self._send_json(_build_failure(error, status=404), status=404)
                return
            content_type = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
            body = resolved.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            if download:
                self.send_header("Content-Disposition", _download_content_disposition(resolved, download_filename))
            self._send_no_cache_headers()
            self.end_headers()
            self.wfile.write(body)
            return
        self._send_json(_build_failure("未找到路径", status=404), status=404)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/upload":
            max_bytes = MAX_UPLOAD_BODY_BYTES
        elif parsed.path == "/api/draw/preview":
            max_bytes = MAX_DRAW_JSON_BODY_BYTES
        else:
            max_bytes = MAX_JSON_BODY_BYTES
        payload, error = self._read_json_body(max_bytes=max_bytes)
        if error:
            self._send_json(_build_failure(error, status=400), status=400)
            return
        if parsed.path == "/api/upload":
            self._send_json(save_uploaded_file_from_payload(payload))
            return
        if parsed.path == "/api/draw/preview":
            self._send_json(draw_preview_from_payload(payload))
            return
        if parsed.path == "/api/material-lab/preview":
            self._send_json(material_lab_preview_from_payload(payload))
            return
        if parsed.path == "/api/material-lab/manage":
            self._send_json(material_lab_manage_from_payload(payload))
            return
        if parsed.path == "/api/material-lab/recent":
            self._send_json(material_lab_recent_params())
            return
        if parsed.path == "/api/material-lab/send":
            self._send_json(material_lab_send_from_payload(payload))
            return
        if parsed.path == "/api/material-lab/select-cell":
            self._send_json(material_lab_select_cell_from_payload(payload))
            return
        if parsed.path == "/api/connection/check":
            self._send_json(connection_check_from_payload(payload))
            return
        if parsed.path == "/api/text-task/generate":
            self._send_json(generate_text_task_from_payload(payload))
            return
        if parsed.path == "/api/text-task/send":
            self._send_json(send_text_task_from_payload(payload))
            return
        if parsed.path == "/api/workflow":
            self._send_json(workflow_action_from_payload(payload))
            return
        self._send_json(_build_failure("未找到路径", status=404), status=404)

    def log_message(self, format, *args):
        print("%s - %s" % (self.address_string(), format % args))


def create_server(host=DEFAULT_WEB_HOST, port=DEFAULT_WEB_PORT):
    return ThreadingHTTPServer((host, int(port)), LaserWebRequestHandler)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Local web UI for typed laser text tasks.")
    parser.add_argument("--host", default=DEFAULT_WEB_HOST, help="Bind host. Defaults to 127.0.0.1.")
    parser.add_argument("--port", type=int, default=DEFAULT_WEB_PORT, help="Bind port. Defaults to 8766.")
    args = parser.parse_args(argv)

    server = create_server(args.host, args.port)
    print(f"Laser text web UI: http://{args.host}:{args.port}/")
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print("Warning: this web control surface is reachable beyond localhost; do not expose it to the public internet.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping laser text web UI...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
