import time
import os
import re
import math

from core import laser_execution
from core import laser_time_estimate
from core.laser_runtime.config import get_laser_settings


_SETTINGS = get_laser_settings()
DEFAULT_MACHINE_NAME = _SETTINGS.machine_name
DEFAULT_WORK_AREA_WIDTH_MM = _SETTINGS.work_area_width_mm
DEFAULT_WORK_AREA_HEIGHT_MM = _SETTINGS.work_area_height_mm
DEFAULT_IMAGE_FIT_BOX_WIDTH_MM = _SETTINGS.image_fit_box_width_mm
DEFAULT_IMAGE_FIT_BOX_HEIGHT_MM = _SETTINGS.image_fit_box_height_mm
DEFAULT_SAFE_MARGIN_MM = _SETTINGS.safe_margin_mm
DEFAULT_LASER_OPTICAL_POWER_W = _SETTINGS.laser_optical_power_w
DEFAULT_LASER_S_MAX = _SETTINGS.laser_s_max
DEFAULT_ENGRAVING_MODE = _SETTINGS.engraving_mode
DEFAULT_RASTER_OVERSCAN_MM = _SETTINGS.raster_overscan_mm
OUTLINE_MIN_CONTOUR_AREA_RATIO = 0.00005
OUTLINE_APPROX_EPSILON_RATIO = 0.0004
OUTLINE_AXIS_SNAP_TOLERANCE_PX = 1.0
OUTLINE_AXIS_SNAP_DOMINANCE_RATIO = 4.0
DEFAULT_IMAGE_FILE = _SETTINGS.default_image_file
DEFAULT_GRBL_PORT = _SETTINGS.default_serial_port
DEFAULT_BAUDRATE = _SETTINGS.baudrate
DEFAULT_ENGRAVING_DIR = str(_SETTINGS.engraving_dir)
IMAGE_EXTENSIONS = (".bmp", ".bmg", ".jpg", ".jpeg", ".png", ".gif")
GCODE_EXTENSIONS = (".gcode", ".nc")
ZERO_ORIGIN_COMMAND = "G92 X0 Y0 Z0"
GCODE_WORD_RE = re.compile(r"([A-Za-z])\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))")
MOTION_G_CODES = {0, 1, 2, 3}
LASER_MODE_ALIASES = {
    "engrave": "engrave",
    "engraving": "engrave",
    "雕刻": "engrave",
    "cut": "cut",
    "cutting": "cut",
    "切割": "cut",
}
ENGRAVING_MODE_ALIASES = {
    "raster": "raster",
    "fill": "raster",
    "scan": "raster",
    "填充": "raster",
    "栅格": "raster",
    "outline": "outline",
    "line": "outline",
    "contour": "outline",
    "轮廓": "outline",
    "线雕": "outline",
}
RASTER_SCAN_DIRECTION_ALIASES = {
    "auto": "auto",
    "自动": "auto",
    "horizontal": "horizontal",
    "h": "horizontal",
    "x": "horizontal",
    "横向": "horizontal",
    "横扫": "horizontal",
    "水平": "horizontal",
    "vertical": "vertical",
    "v": "vertical",
    "y": "vertical",
    "纵向": "vertical",
    "竖向": "vertical",
    "竖扫": "vertical",
    "垂直": "vertical",
}


def _load_serial():
    try:
        import serial
        from serial.tools import list_ports
        return serial, list_ports, None
    except ImportError:
        return None, None, "缺少 pyserial 依赖，请先在当前 Python 环境执行: pip install pyserial"


def _load_image_libs():
    try:
        import cv2
        import numpy as np

        return cv2, np, None
    except ImportError as exc:
        return (
            None,
            None,
            f"缺少图像处理依赖，请先在当前 Python 环境执行: pip install opencv-python numpy。原始错误: {exc}",
        )


def _normalize_path(path):
    return os.path.normpath(os.path.expandvars(os.path.expanduser(path)))


def _path_has_extension(path, extensions):
    return path.lower().endswith(extensions)


def _is_plain_filename(path):
    return bool(path) and not os.path.isabs(path) and os.path.dirname(path) == ""


def _find_engraving_file(name):
    if not name:
        return None

    search_dir = _normalize_path(DEFAULT_ENGRAVING_DIR)
    if not os.path.isdir(search_dir):
        return None

    if _path_has_extension(name, GCODE_EXTENSIONS + IMAGE_EXTENSIONS):
        candidate = os.path.join(search_dir, name)
        return candidate if os.path.isfile(candidate) else None

    for extension in GCODE_EXTENSIONS + IMAGE_EXTENSIONS:
        candidate = os.path.join(search_dir, name + extension)
        if os.path.isfile(candidate):
            return candidate

    target = name.lower()
    for filename in os.listdir(search_dir):
        path = os.path.join(search_dir, filename)
        if not os.path.isfile(path):
            continue
        stem, extension = os.path.splitext(filename)
        if stem.lower() == target and extension.lower() in GCODE_EXTENSIONS + IMAGE_EXTENSIONS:
            return path

    return None


def _list_engraving_files():
    search_dir = _normalize_path(DEFAULT_ENGRAVING_DIR)
    if not os.path.isdir(search_dir):
        return []

    files = []
    for filename in os.listdir(search_dir):
        path = os.path.join(search_dir, filename)
        if os.path.isfile(path) and _path_has_extension(path, GCODE_EXTENSIONS + IMAGE_EXTENSIONS):
            files.append(path)
    return sorted(files)


def _find_single_default_engraving_file():
    files = _list_engraving_files()
    if len(files) == 1:
        return files[0], None
    if len(files) > 1:
        names = ", ".join(os.path.basename(path) for path in files)
        return None, f"雕刻文件目录中有多个文件，请指定文件名。目录: {_normalize_path(DEFAULT_ENGRAVING_DIR)}，文件: {names}"
    return None, None


def _validate_number(value, name, minimum, allow_zero=False):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None, f"{name} 必须是数字"

    if allow_zero:
        if number < minimum:
            return None, f"{name} 不能小于 {minimum}"
    elif number <= minimum:
        return None, f"{name} 必须大于 {minimum}"

    return number, None


def _validate_int(value, name, minimum, maximum=None):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None, f"{name} 必须是整数"

    if number < minimum:
        return None, f"{name} 不能小于 {minimum}"
    if maximum is not None and number > maximum:
        return None, f"{name} 不能大于 {maximum}"

    return number, None


def _validate_dimension(value, name):
    if value is None:
        value = 0
    return _validate_number(value, name, 0, allow_zero=True)


def _machine_profile():
    return {
        "machine_name": DEFAULT_MACHINE_NAME,
        "work_area_width_mm": DEFAULT_WORK_AREA_WIDTH_MM,
        "work_area_height_mm": DEFAULT_WORK_AREA_HEIGHT_MM,
        "default_image_fit_box_width_mm": DEFAULT_IMAGE_FIT_BOX_WIDTH_MM,
        "default_image_fit_box_height_mm": DEFAULT_IMAGE_FIT_BOX_HEIGHT_MM,
        "default_safe_margin_mm": DEFAULT_SAFE_MARGIN_MM,
        "laser_optical_power_w": DEFAULT_LASER_OPTICAL_POWER_W,
        "grbl_laser_s_max": DEFAULT_LASER_S_MAX,
        "default_raster_overscan_mm": DEFAULT_RASTER_OVERSCAN_MM,
    }


def _validate_work_area(width_mm, height_mm, x0=0.0, y0=0.0):
    error = _validate_coordinate_in_work_area(x0, y0)
    if error:
        return error

    return _validate_coordinate_in_work_area(x0 + width_mm, y0 + height_mm)


def _validate_coordinate_in_work_area(x_mm, y_mm):
    tolerance = 1e-9
    if x_mm < -tolerance or y_mm < -tolerance:
        return (
            f"坐标超出 {DEFAULT_MACHINE_NAME} 行程: "
            f"X={_format_mm(x_mm)}mm, Y={_format_mm(y_mm)}mm；允许 X/Y 从 0 开始"
        )

    max_width = DEFAULT_WORK_AREA_WIDTH_MM
    max_height = DEFAULT_WORK_AREA_HEIGHT_MM
    if max_width > 0 and x_mm - max_width > tolerance:
        return (
            f"坐标超出 {DEFAULT_MACHINE_NAME} 行程: X 最大 {_format_mm(x_mm)}mm，"
            f"允许 0..{_format_mm(max_width)}mm"
        )
    if max_height > 0 and y_mm - max_height > tolerance:
        return (
            f"坐标超出 {DEFAULT_MACHINE_NAME} 行程: Y 最大 {_format_mm(y_mm)}mm，"
            f"允许 0..{_format_mm(max_height)}mm"
        )
    return None


def _resolve_laser_mode(laser_mode):
    normalized = str(laser_mode or "engrave").strip().lower()
    resolved = LASER_MODE_ALIASES.get(normalized)
    if not resolved:
        supported = ", ".join(sorted(LASER_MODE_ALIASES))
        return None, f"laser_mode 不支持: {laser_mode}，支持: {supported}"
    return resolved, None


def _resolve_engraving_mode(engraving_mode=None):
    normalized = str(engraving_mode or DEFAULT_ENGRAVING_MODE or "raster").strip().lower()
    resolved = ENGRAVING_MODE_ALIASES.get(normalized)
    if not resolved:
        supported = ", ".join(sorted(ENGRAVING_MODE_ALIASES))
        return None, f"LASER_ENGRAVING_MODE 不支持: {engraving_mode or DEFAULT_ENGRAVING_MODE}，支持: {supported}"
    return resolved, None


def _resolve_raster_scan_direction(raster_scan_direction=None):
    normalized = str(raster_scan_direction or "auto").strip().lower()
    resolved = RASTER_SCAN_DIRECTION_ALIASES.get(normalized)
    if not resolved:
        supported = ", ".join(sorted(RASTER_SCAN_DIRECTION_ALIASES))
        return None, f"raster_scan_direction 不支持: {raster_scan_direction}，支持: {supported}"
    return resolved, None


def _resolve_port(port):
    return (port or DEFAULT_GRBL_PORT).strip()


def _resolve_image_file(image_file):
    normalized = _normalize_path(image_file or DEFAULT_IMAGE_FILE)
    if not normalized:
        return None, "请提供 image_file，或在 .env 中配置 LASERGRBL_DEFAULT_IMAGE"

    if not os.path.isfile(normalized):
        return normalized, f"图片文件不存在: {normalized}"

    if not normalized.lower().endswith(IMAGE_EXTENSIONS):
        return (
            normalized,
            f"图片类型不支持: {normalized}，支持: {', '.join(IMAGE_EXTENSIONS)}",
        )

    return normalized, None


def _resolve_output_gcode_file(output_file, image_file):
    if output_file:
        normalized = _normalize_path(output_file)
    elif DEFAULT_GCODE_FILE and _path_has_extension(DEFAULT_GCODE_FILE, GCODE_EXTENSIONS):
        normalized = _normalize_path(DEFAULT_GCODE_FILE)
    else:
        base, _ = os.path.splitext(image_file)
        normalized = base + ".gcode"

    if not normalized.lower().endswith(GCODE_EXTENSIONS):
        return (
            normalized,
            f"输出文件类型不支持: {normalized}，支持: {', '.join(GCODE_EXTENSIONS)}",
        )

    output_dir = os.path.dirname(normalized) or "."
    if not os.path.isdir(output_dir):
        return normalized, f"输出目录不存在: {output_dir}"

    return normalized, None


def _read_grayscale_image(path):
    cv2, np, error = _load_image_libs()
    if error:
        return None, error

    data = np.fromfile(path, dtype=np.uint8)
    decoded = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    if decoded is None:
        image = None
    elif len(decoded.shape) == 3 and decoded.shape[2] == 4:
        alpha = decoded[:, :, 3].astype(np.float32) / 255.0
        color = decoded[:, :, :3].astype(np.float32)
        white = np.full(color.shape, 255, dtype=np.float32)
        composited = color * alpha[:, :, None] + white * (1.0 - alpha[:, :, None])
        image = cv2.cvtColor(composited.astype(np.uint8), cv2.COLOR_BGR2GRAY)
    elif len(decoded.shape) == 3:
        image = cv2.cvtColor(decoded, cv2.COLOR_BGR2GRAY)
    else:
        image = decoded
    if image is None:
        return (
            None,
            "无法识别图片内容。如果这是厂商私有 .bmg，请先用原软件导出为 BMP/PNG；"
            "如果只是扩展名写错，文件内容需要是标准图片。",
        )

    return image, None


def _read_image_dpi(path):
    try:
        from PIL import Image
    except ImportError:
        return None, None, "pillow_unavailable"

    try:
        with Image.open(path) as image:
            dpi = image.info.get("dpi")
    except Exception:
        return None, None, "unreadable"

    if not isinstance(dpi, tuple) or len(dpi) < 2:
        return None, None, "missing"
    try:
        dpi_x = float(dpi[0])
        dpi_y = float(dpi[1])
    except (TypeError, ValueError):
        return None, None, "invalid"
    if dpi_x <= 0 or dpi_y <= 0:
        return None, None, "invalid"
    return dpi_x, dpi_y, "embedded"


def _edge_median_gray(values, np):
    return float(np.median(values.astype(np.float32)))


def _auto_trim_image(image, tolerance=20):
    cv2, np, error = _load_image_libs()
    if error:
        return image, {
            "enabled": True,
            "trimmed": False,
            "skipped_reason": error,
        }

    try:
        tolerance = float(tolerance)
    except (TypeError, ValueError):
        tolerance = 20.0
    tolerance = max(0.0, tolerance)

    source_height, source_width = image.shape[:2]
    trim_image = image
    if len(image.shape) == 3 and image.shape[2] == 4:
        alpha = image[:, :, 3].astype(np.float32) / 255.0
        color = image[:, :, :3].astype(np.float32)
        white = np.full(color.shape, 255, dtype=np.float32)
        composited = color * alpha[:, :, None] + white * (1.0 - alpha[:, :, None])
        trim_image = cv2.cvtColor(composited.astype(np.uint8), cv2.COLOR_BGR2GRAY)
    elif len(image.shape) == 3:
        trim_image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    info = {
        "enabled": True,
        "trimmed": False,
        "tolerance": tolerance,
        "original_width_px": int(source_width),
        "original_height_px": int(source_height),
        "trimmed_width_px": int(source_width),
        "trimmed_height_px": int(source_height),
        "crop_box": {
            "x_px": 0,
            "y_px": 0,
            "width_px": int(source_width),
            "height_px": int(source_height),
        },
    }
    if source_width <= 0 or source_height <= 0:
        info["skipped_reason"] = "invalid_image_size"
        return image, info

    edges = [
        _edge_median_gray(trim_image[0, :], np),
        _edge_median_gray(trim_image[source_height - 1, :], np),
        _edge_median_gray(trim_image[:, 0], np),
        _edge_median_gray(trim_image[:, source_width - 1], np),
    ]

    clusters = []
    for value in edges:
        for cluster in clusters:
            if abs(cluster["center"] - value) <= tolerance:
                cluster["values"].append(value)
                cluster["center"] = float(np.median(np.array(cluster["values"], dtype=np.float32)))
                break
        else:
            clusters.append({"center": value, "values": [value]})
    clusters.sort(key=lambda item: len(item["values"]), reverse=True)
    if not clusters or len(clusters[0]["values"]) < 3:
        info["skipped_reason"] = "edge_background_inconsistent"
        info["edge_background_gray"] = [round(value, 3) for value in edges]
        return image, info

    background = float(np.median(np.array(clusters[0]["values"], dtype=np.float32)))
    info["background_gray"] = round(background, 3)
    background_mask = np.abs(trim_image.astype(np.float32) - background) <= tolerance
    row_is_background = np.all(background_mask, axis=1)
    col_is_background = np.all(background_mask, axis=0)

    foreground_rows = np.where(~row_is_background)[0]
    foreground_cols = np.where(~col_is_background)[0]
    if len(foreground_rows) == 0 or len(foreground_cols) == 0:
        info["skipped_reason"] = "no_foreground_found"
        return image, info

    top = int(foreground_rows[0])
    bottom = int(foreground_rows[-1]) + 1
    left = int(foreground_cols[0])
    right = int(foreground_cols[-1]) + 1
    if top == 0 and left == 0 and bottom == source_height and right == source_width:
        info["skipped_reason"] = "no_background_margin"
        return image, info

    cropped = image[top:bottom, left:right]
    trim_height, trim_width = cropped.shape[:2]
    info.update(
        {
            "trimmed": True,
            "trimmed_width_px": int(trim_width),
            "trimmed_height_px": int(trim_height),
            "crop_box": {
                "x_px": left,
                "y_px": top,
                "width_px": int(trim_width),
                "height_px": int(trim_height),
            },
        }
    )
    return cropped, info


def _safe_image_area(safe_margin_mm):
    safe_margin_mm, error = _validate_number(safe_margin_mm, "safe_margin_mm", 0, allow_zero=True)
    if error:
        return None, None, None, error
    safe_width = DEFAULT_WORK_AREA_WIDTH_MM - safe_margin_mm * 2
    safe_height = DEFAULT_WORK_AREA_HEIGHT_MM - safe_margin_mm * 2
    if safe_width <= 0 or safe_height <= 0:
        return None, None, None, "safe_margin_mm 过大，安全雕刻区域必须大于 0mm"
    return safe_width, safe_height, safe_margin_mm, None


def _resolve_effective_dpi(image_file, auto_size, dpi):
    dpi, error = _validate_number(dpi, "dpi", 0)
    if error:
        return None, None, None, error

    embedded_x, embedded_y, embedded_status = _read_image_dpi(image_file)
    if auto_size and embedded_x and embedded_y:
        return embedded_x, embedded_y, {
            "dpi_x": round(embedded_x, 3),
            "dpi_y": round(embedded_y, 3),
            "source": "embedded",
            "embedded_status": embedded_status,
        }, None

    return dpi, dpi, {
        "dpi_x": round(dpi, 3),
        "dpi_y": round(dpi, 3),
        "source": "parameter",
        "embedded_status": embedded_status,
    }, None


def _scale_dimensions_to_fit(width_mm, height_mm, max_width_mm, max_height_mm):
    scale = min(max_width_mm / width_mm, max_height_mm / height_mm, 1.0)
    if scale >= 1.0:
        return width_mm, height_mm, 1.0, False
    return width_mm * scale, height_mm * scale, scale, True


def _validate_placement_safe_area(placement, actual_width_mm, actual_height_mm):
    offset_x_mm = float(placement.get("offset_x_mm", 0.0))
    offset_y_mm = float(placement.get("offset_y_mm", 0.0))
    safe_width_mm = float(placement.get("safe_width_mm", DEFAULT_WORK_AREA_WIDTH_MM))
    safe_height_mm = float(placement.get("safe_height_mm", DEFAULT_WORK_AREA_HEIGHT_MM))
    if offset_x_mm + actual_width_mm > safe_width_mm + 1e-9 or offset_y_mm + actual_height_mm > safe_height_mm + 1e-9:
        return (
            "图片像素取整后的摆放超出安全雕刻区域: "
            f"offset=({_format_mm(offset_x_mm)}, {_format_mm(offset_y_mm)})mm, "
            f"actual_size=({_format_mm(actual_width_mm)}, {_format_mm(actual_height_mm)})mm, "
            f"safe=({_format_mm(safe_width_mm)}, {_format_mm(safe_height_mm)})mm"
        )
    return None


def _finalize_placement_with_actual_size(placement, stats):
    updated = dict(placement)
    actual_width_mm = stats.get("actual_width_mm")
    actual_height_mm = stats.get("actual_height_mm")
    if actual_width_mm is not None and actual_height_mm is not None:
        updated["actual_width_mm"] = actual_width_mm
        updated["actual_height_mm"] = actual_height_mm
        updated["final_width_mm"] = actual_width_mm
        updated["final_height_mm"] = actual_height_mm
    return updated


def _resolve_image_placement(
    image_file,
    source_width_px,
    source_height_px,
    width_mm=0.0,
    height_mm=0.0,
    auto_size=True,
    dpi=300.0,
    lock_aspect_ratio=True,
    offset_x_mm=0.0,
    offset_y_mm=0.0,
    safe_margin_mm=DEFAULT_SAFE_MARGIN_MM,
):
    width_mm, error = _validate_dimension(width_mm, "width_mm")
    if error:
        return None, error
    height_mm, error = _validate_dimension(height_mm, "height_mm")
    if error:
        return None, error
    offset_x_mm, error = _validate_number(offset_x_mm, "offset_x_mm", 0, allow_zero=True)
    if error:
        return None, error
    offset_y_mm, error = _validate_number(offset_y_mm, "offset_y_mm", 0, allow_zero=True)
    if error:
        return None, error
    safe_width, safe_height, safe_margin_mm, error = _safe_image_area(safe_margin_mm)
    if error:
        return None, error
    if source_width_px <= 0 or source_height_px <= 0:
        return None, "图片尺寸无效"

    dpi_x, dpi_y, dpi_info, error = _resolve_effective_dpi(image_file, auto_size, dpi)
    if error:
        return None, error
    natural_width_mm = source_width_px / dpi_x * 25.4
    natural_height_mm = source_height_px / dpi_y * 25.4
    aspect_ratio = source_width_px / source_height_px
    manual_width = width_mm > 0
    manual_height = height_mm > 0
    size_source = "dpi"

    if manual_width and manual_height:
        if lock_aspect_ratio:
            # Fit source aspect into the requested mm bounding box.
            box_aspect = width_mm / height_mm
            if aspect_ratio > box_aspect:
                target_width_mm = width_mm
                target_height_mm = width_mm / aspect_ratio
            else:
                target_height_mm = height_mm
                target_width_mm = height_mm * aspect_ratio
            size_source = "manual_box"
        else:
            target_width_mm = width_mm
            target_height_mm = height_mm
            size_source = "manual"
    elif manual_width:
        target_width_mm = width_mm
        target_height_mm = width_mm / aspect_ratio
        size_source = "manual_width"
    elif manual_height:
        target_height_mm = height_mm
        target_width_mm = height_mm * aspect_ratio
        size_source = "manual_height"
    else:
        target_width_mm = natural_width_mm
        target_height_mm = natural_height_mm

    final_width_mm, final_height_mm, scale, scaled = _scale_dimensions_to_fit(
        target_width_mm,
        target_height_mm,
        safe_width,
        safe_height,
    )
    if offset_x_mm + final_width_mm > safe_width + 1e-9 or offset_y_mm + final_height_mm > safe_height + 1e-9:
        return None, (
            "图片摆放超出安全雕刻区域: "
            f"offset=({_format_mm(offset_x_mm)}, {_format_mm(offset_y_mm)})mm, "
            f"size=({_format_mm(final_width_mm)}, {_format_mm(final_height_mm)})mm, "
            f"safe=({_format_mm(safe_width)}, {_format_mm(safe_height)})mm"
        )

    return {
        "size_source": size_source,
        "auto_size": bool(auto_size),
        "lock_aspect_ratio": bool(lock_aspect_ratio),
        "dpi": dpi_info,
        "source_width_px": int(source_width_px),
        "source_height_px": int(source_height_px),
        "natural_width_mm": round(natural_width_mm, 3),
        "natural_height_mm": round(natural_height_mm, 3),
        "requested_width_mm": round(target_width_mm, 3),
        "requested_height_mm": round(target_height_mm, 3),
        "final_width_mm": round(final_width_mm, 3),
        "final_height_mm": round(final_height_mm, 3),
        "offset_x_mm": round(offset_x_mm, 3),
        "offset_y_mm": round(offset_y_mm, 3),
        "safe_margin_mm": round(safe_margin_mm, 3),
        "safe_width_mm": round(safe_width, 3),
        "safe_height_mm": round(safe_height, 3),
        "scaled_to_safe_area": bool(scaled),
        "safe_scale": round(scale, 6),
    }, None


def _mm_to_pixels(value_mm, pixel_size_mm, *, fit_box=False):
    if fit_box:
        return max(1, int(math.floor(value_mm / pixel_size_mm + 1e-9)))
    return max(1, int(round(value_mm / pixel_size_mm)))


def _resolve_resize_dimensions_px(source_width, source_height, width_mm, height_mm, pixel_size_mm):
    if source_width <= 0 or source_height <= 0:
        return None, None, "图片尺寸无效"

    if width_mm > 0 and height_mm > 0:
        return (
            _mm_to_pixels(width_mm, pixel_size_mm),
            _mm_to_pixels(height_mm, pixel_size_mm),
            None,
        )
    if width_mm > 0:
        target_width = _mm_to_pixels(width_mm, pixel_size_mm)
        target_height = max(1, int(round(source_height * target_width / source_width)))
        return target_width, target_height, None
    if height_mm > 0:
        target_height = _mm_to_pixels(height_mm, pixel_size_mm)
        target_width = max(1, int(round(source_width * target_height / source_height)))
        return target_width, target_height, None

    fit_width = DEFAULT_IMAGE_FIT_BOX_WIDTH_MM
    fit_height = DEFAULT_IMAGE_FIT_BOX_HEIGHT_MM
    if fit_width <= 0 or fit_height <= 0:
        return None, None, "默认图片 G-code 尺寸框必须大于 0mm"

    scale = min(fit_width / source_width, fit_height / source_height)
    target_width_mm = source_width * scale
    target_height_mm = source_height * scale
    return (
        _mm_to_pixels(target_width_mm, pixel_size_mm, fit_box=True),
        _mm_to_pixels(target_height_mm, pixel_size_mm, fit_box=True),
        None,
    )


def _resize_image(image, width_mm, height_mm, pixel_size_mm, interpolation=None, cv2_module=None):
    if cv2_module is None:
        cv2, _, error = _load_image_libs()
        if error:
            return None, error
    else:
        cv2 = cv2_module

    source_height, source_width = image.shape[:2]
    target_width, target_height, error = _resolve_resize_dimensions_px(
        source_width, source_height, width_mm, height_mm, pixel_size_mm
    )
    if error:
        return None, error

    if interpolation is None:
        interpolation = cv2.INTER_AREA
    return cv2.resize(image, (target_width, target_height), interpolation=interpolation), None


def _format_mm(value):
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return text or "0"


def _power_from_gray(gray, laser_min_power, laser_max_power, threshold, invert):
    gray = int(gray)
    if threshold >= 0:
        should_engrave = gray >= threshold if invert else gray <= threshold
        return laser_max_power if should_engrave else laser_min_power

    ratio = gray / 255 if invert else (255 - gray) / 255
    return int(round(laser_min_power + ratio * (laser_max_power - laser_min_power)))


def _strip_gcode_comments(line):
    text = line.split(";", 1)[0]
    output = []
    in_comment = False
    for char in text:
        if char == "(":
            in_comment = True
            continue
        if char == ")" and in_comment:
            in_comment = False
            continue
        if not in_comment:
            output.append(char)
    return "".join(output).strip()


def _parse_gcode_words(line):
    return [(match.group(1).upper(), float(match.group(2))) for match in GCODE_WORD_RE.finditer(line)]


def _gcode_int(value):
    return int(round(value))


def _update_bounds(bounds, x_mm, y_mm):
    bounds["min_x_mm"] = min(bounds["min_x_mm"], x_mm)
    bounds["max_x_mm"] = max(bounds["max_x_mm"], x_mm)
    bounds["min_y_mm"] = min(bounds["min_y_mm"], y_mm)
    bounds["max_y_mm"] = max(bounds["max_y_mm"], y_mm)


def _normalize_angle(angle):
    return angle % (2 * math.pi)


def _arc_sweep(start_angle, end_angle, clockwise):
    if clockwise:
        return (start_angle - end_angle) % (2 * math.pi)
    return (end_angle - start_angle) % (2 * math.pi)


def _angle_on_arc(angle, start_angle, end_angle, clockwise):
    total = _arc_sweep(start_angle, end_angle, clockwise)
    if total < 1e-9:
        return True
    partial = _arc_sweep(start_angle, angle, clockwise)
    return partial <= total + 1e-9


def _arc_bounds_from_center(start_x, start_y, end_x, end_y, center_x, center_y, clockwise):
    radius = math.hypot(start_x - center_x, start_y - center_y)
    if radius <= 0:
        return None, "圆弧半径必须大于 0"

    start_angle = _normalize_angle(math.atan2(start_y - center_y, start_x - center_x))
    end_angle = _normalize_angle(math.atan2(end_y - center_y, end_x - center_x))
    points = [(start_x, start_y), (end_x, end_y)]
    for angle in (0, math.pi / 2, math.pi, math.pi * 3 / 2):
        if _angle_on_arc(angle, start_angle, end_angle, clockwise):
            points.append((center_x + radius * math.cos(angle), center_y + radius * math.sin(angle)))

    return {
        "min_x_mm": min(point[0] for point in points),
        "max_x_mm": max(point[0] for point in points),
        "min_y_mm": min(point[1] for point in points),
        "max_y_mm": max(point[1] for point in points),
    }, None


def _arc_centers_from_r(start_x, start_y, end_x, end_y, radius_word, clockwise):
    radius = abs(radius_word)
    dx = end_x - start_x
    dy = end_y - start_y
    chord = math.hypot(dx, dy)
    if chord <= 0:
        return None, "R 圆弧不能使用相同起点和终点"
    if chord / 2 > radius + 1e-9:
        return None, "R 圆弧半径小于端点弦长的一半"

    mid_x = (start_x + end_x) / 2
    mid_y = (start_y + end_y) / 2
    height = math.sqrt(max(radius * radius - (chord / 2) * (chord / 2), 0.0))
    normal_x = -dy / chord
    normal_y = dx / chord
    centers = [
        (mid_x + normal_x * height, mid_y + normal_y * height),
        (mid_x - normal_x * height, mid_y - normal_y * height),
    ]
    want_large_arc = radius_word < 0

    selected = []
    for center_x, center_y in centers:
        start_angle = _normalize_angle(math.atan2(start_y - center_y, start_x - center_x))
        end_angle = _normalize_angle(math.atan2(end_y - center_y, end_x - center_x))
        sweep = _arc_sweep(start_angle, end_angle, clockwise)
        if (sweep > math.pi) == want_large_arc or abs(sweep - math.pi) < 1e-9:
            selected.append((center_x, center_y))

    return selected or centers, None


def _validate_arc_bounds(line_number, start_x, start_y, end_x, end_y, arc_values, clockwise, bounds):
    arc_bounds = []
    if "I" in arc_values or "J" in arc_values:
        center_x = start_x + arc_values.get("I", 0.0)
        center_y = start_y + arc_values.get("J", 0.0)
        item, error = _arc_bounds_from_center(start_x, start_y, end_x, end_y, center_x, center_y, clockwise)
        if error:
            return error
        arc_bounds.append(item)
    elif "R" in arc_values:
        centers, error = _arc_centers_from_r(start_x, start_y, end_x, end_y, arc_values["R"], clockwise)
        if error:
            return error
        for center_x, center_y in centers:
            item, error = _arc_bounds_from_center(start_x, start_y, end_x, end_y, center_x, center_y, clockwise)
            if error:
                return error
            arc_bounds.append(item)
    else:
        return f"G-code 第 {line_number} 行 G2/G3 圆弧缺少 I/J 或 R，无法证明边界安全"

    for item in arc_bounds:
        for x_mm, y_mm in (
            (item["min_x_mm"], item["min_y_mm"]),
            (item["min_x_mm"], item["max_y_mm"]),
            (item["max_x_mm"], item["min_y_mm"]),
            (item["max_x_mm"], item["max_y_mm"]),
        ):
            error = _validate_coordinate_in_work_area(x_mm, y_mm)
            if error:
                return f"G-code 第 {line_number} 行 {error}"
        bounds["min_x_mm"] = min(bounds["min_x_mm"], item["min_x_mm"])
        bounds["max_x_mm"] = max(bounds["max_x_mm"], item["max_x_mm"])
        bounds["min_y_mm"] = min(bounds["min_y_mm"], item["min_y_mm"])
        bounds["max_y_mm"] = max(bounds["max_y_mm"], item["max_y_mm"])

    return None


def inspect_gcode_file_bounds(gcode_file):
    x_mm = 0.0
    y_mm = 0.0
    unit_scale = 1.0
    absolute_mode = True
    motion_mode = None
    coordinate_line_count = 0
    max_s = 0
    bounds = {
        "min_x_mm": 0.0,
        "max_x_mm": 0.0,
        "min_y_mm": 0.0,
        "max_y_mm": 0.0,
    }

    try:
        with open(gcode_file, "r", encoding="utf-8", errors="replace") as file:
            for line_number, raw_line in enumerate(file, start=1):
                line = _strip_gcode_comments(raw_line)
                if not line or line == "%":
                    continue

                words = _parse_gcode_words(line)
                if not words:
                    continue

                axis_values = {}
                arc_values = {}
                has_g92 = False
                for letter, value in words:
                    if letter == "G":
                        code = _gcode_int(value)
                        if code == 20:
                            unit_scale = 25.4
                        elif code == 21:
                            unit_scale = 1.0
                        elif code == 90:
                            absolute_mode = True
                        elif code == 91:
                            absolute_mode = False
                        elif code == 92:
                            has_g92 = True
                        elif code in MOTION_G_CODES:
                            motion_mode = code
                    elif letter in ("X", "Y"):
                        axis_values[letter] = value * unit_scale
                    elif letter in ("I", "J", "R"):
                        arc_values[letter] = value * unit_scale
                    elif letter == "S":
                        if value < 0 or value > DEFAULT_LASER_S_MAX:
                            return None, (
                                f"G-code 第 {line_number} 行 S 值超出上限: S{_format_mm(value)}，"
                                f"允许 0..{DEFAULT_LASER_S_MAX}"
                            )
                        max_s = max(max_s, int(round(value)))

                if has_g92 and axis_values:
                    x_mm = axis_values.get("X", x_mm)
                    y_mm = axis_values.get("Y", y_mm)
                    error = _validate_coordinate_in_work_area(x_mm, y_mm)
                    if error:
                        return None, f"G-code 第 {line_number} 行 {error}"
                    _update_bounds(bounds, x_mm, y_mm)
                    continue

                has_motion_target = bool(axis_values) or (motion_mode in (2, 3) and bool(arc_values))
                if motion_mode not in MOTION_G_CODES or not has_motion_target:
                    continue

                target_x = axis_values.get("X", 0.0 if not absolute_mode else x_mm)
                target_y = axis_values.get("Y", 0.0 if not absolute_mode else y_mm)
                if absolute_mode:
                    target_x = axis_values.get("X", x_mm)
                    target_y = axis_values.get("Y", y_mm)
                else:
                    target_x = x_mm + axis_values.get("X", 0.0)
                    target_y = y_mm + axis_values.get("Y", 0.0)

                error = _validate_coordinate_in_work_area(target_x, target_y)
                if error:
                    return None, f"G-code 第 {line_number} 行 {error}"

                if motion_mode in (2, 3):
                    error = _validate_arc_bounds(
                        line_number,
                        x_mm,
                        y_mm,
                        target_x,
                        target_y,
                        arc_values,
                        clockwise=motion_mode == 2,
                        bounds=bounds,
                    )
                    if error:
                        return None, error
                else:
                    _update_bounds(bounds, target_x, target_y)

                x_mm = target_x
                y_mm = target_y
                coordinate_line_count += 1
    except OSError as exc:
        return None, f"扫描 G-code 文件失败: {exc}"

    return {
        **bounds,
        "coordinate_line_count": coordinate_line_count,
        "max_s": max_s,
        "machine_profile": _machine_profile(),
    }, None


def _power_grid_from_grayscale(resized, laser_min_power, laser_max_power, threshold, invert):
    image_height, image_width = resized.shape[:2]
    return [
        [
            _power_from_gray(
                resized[y][x],
                laser_min_power,
                laser_max_power,
                threshold,
                invert,
            )
            for x in range(image_width)
        ]
        for y in range(image_height)
    ]


def _scanline_active_bounds(values, background_power):
    active_indexes = [index for index, power in enumerate(values) if power > background_power]
    if not active_indexes:
        return None
    return active_indexes[0], active_indexes[-1] + 1


def _raster_scanline_cost(power_grid, direction, background_power):
    if not power_grid:
        return 0, 0, 0
    image_height = len(power_grid)
    image_width = len(power_grid[0]) if image_height else 0
    groups = []
    if direction == "vertical":
        for x in range(image_width):
            groups.append([power_grid[y][x] for y in range(image_height)])
    else:
        groups = power_grid

    active_lines = 0
    transitions = 0
    active_span_cells = 0
    for values in groups:
        bounds = _scanline_active_bounds(values, background_power)
        if bounds is None:
            continue
        start, end = bounds
        active_lines += 1
        active_span_cells += end - start
        previous_power = None
        for power in values[start:end]:
            normalized_power = power if power > background_power else background_power
            if previous_power is not None and normalized_power != previous_power:
                transitions += 1
            previous_power = normalized_power
    return active_lines, transitions, active_span_cells


def _choose_raster_scan_direction(power_grid, requested_direction, background_power):
    if requested_direction != "auto":
        return requested_direction, "manual", f"explicit raster_scan_direction={requested_direction}", {}

    horizontal = _raster_scanline_cost(power_grid, "horizontal", background_power)
    vertical = _raster_scanline_cost(power_grid, "vertical", background_power)
    costs = {
        "horizontal": {
            "active_lines": horizontal[0],
            "transitions": horizontal[1],
            "active_span_cells": horizontal[2],
        },
        "vertical": {
            "active_lines": vertical[0],
            "transitions": vertical[1],
            "active_span_cells": vertical[2],
        },
    }
    horizontal_score = horizontal[0] * 3 + horizontal[1] + horizontal[2]
    vertical_score = vertical[0] * 3 + vertical[1] + vertical[2]
    if vertical_score + 1e-9 < horizontal_score * 0.9:
        return "vertical", "auto", f"vertical scan reduces estimated scanline cost {horizontal_score}->{vertical_score}", costs
    return "horizontal", "auto", f"horizontal scan retained; estimated scanline cost horizontal={horizontal_score}, vertical={vertical_score}", costs


def _append_horizontal_scanline(gcode, row_powers, y_mm, pixel_size_mm, reverse, background_power, overscan_mm, offset_x_mm, max_x_mm):
    bounds = _scanline_active_bounds(row_powers, background_power)
    if bounds is None:
        return 0
    start_index, end_index = bounds
    active_start = offset_x_mm + start_index * pixel_size_mm
    active_end = offset_x_mm + end_index * pixel_size_mm
    scan_start = max(0.0, active_start - overscan_mm)
    scan_end = min(max_x_mm, active_end + overscan_mm)
    if reverse:
        gcode.append(f"G0 X{_format_mm(scan_end)} Y{y_mm}")
        gcode.append(f"G1 X{_format_mm(active_end)} Y{y_mm} S0")
        current_power = row_powers[end_index - 1] if row_powers[end_index - 1] > background_power else background_power
        for x in range(end_index - 2, start_index - 1, -1):
            power = row_powers[x] if row_powers[x] > background_power else background_power
            if power == current_power:
                continue
            end_x = offset_x_mm + (x + 1) * pixel_size_mm
            gcode.append(f"G1 X{_format_mm(end_x)} Y{y_mm} S{current_power}")
            current_power = power
        gcode.append(f"G1 X{_format_mm(active_start)} Y{y_mm} S{current_power}")
        gcode.append(f"G1 X{_format_mm(scan_start)} Y{y_mm} S0")
    else:
        gcode.append(f"G0 X{_format_mm(scan_start)} Y{y_mm}")
        gcode.append(f"G1 X{_format_mm(active_start)} Y{y_mm} S0")
        current_power = row_powers[start_index] if row_powers[start_index] > background_power else background_power
        for x in range(start_index + 1, end_index):
            power = row_powers[x] if row_powers[x] > background_power else background_power
            if power == current_power:
                continue
            end_x = offset_x_mm + x * pixel_size_mm
            gcode.append(f"G1 X{_format_mm(end_x)} Y{y_mm} S{current_power}")
            current_power = power
        gcode.append(f"G1 X{_format_mm(active_end)} Y{y_mm} S{current_power}")
        gcode.append(f"G1 X{_format_mm(scan_end)} Y{y_mm} S0")
    return 1


def _append_vertical_scanline(gcode, column_powers, x_mm, pixel_size_mm, reverse, background_power, overscan_mm, offset_y_mm, max_y_mm):
    bounds = _scanline_active_bounds(column_powers, background_power)
    if bounds is None:
        return 0
    start_index, end_index = bounds
    active_start = offset_y_mm + start_index * pixel_size_mm
    active_end = offset_y_mm + end_index * pixel_size_mm
    scan_start = max(0.0, active_start - overscan_mm)
    scan_end = min(max_y_mm, active_end + overscan_mm)
    if reverse:
        gcode.append(f"G0 X{x_mm} Y{_format_mm(scan_end)}")
        gcode.append(f"G1 X{x_mm} Y{_format_mm(active_end)} S0")
        current_power = column_powers[end_index - 1] if column_powers[end_index - 1] > background_power else background_power
        for y in range(end_index - 2, start_index - 1, -1):
            power = column_powers[y] if column_powers[y] > background_power else background_power
            if power == current_power:
                continue
            end_y = offset_y_mm + (y + 1) * pixel_size_mm
            gcode.append(f"G1 X{x_mm} Y{_format_mm(end_y)} S{current_power}")
            current_power = power
        gcode.append(f"G1 X{x_mm} Y{_format_mm(active_start)} S{current_power}")
        gcode.append(f"G1 X{x_mm} Y{_format_mm(scan_start)} S0")
    else:
        gcode.append(f"G0 X{x_mm} Y{_format_mm(scan_start)}")
        gcode.append(f"G1 X{x_mm} Y{_format_mm(active_start)} S0")
        current_power = column_powers[start_index] if column_powers[start_index] > background_power else background_power
        for y in range(start_index + 1, end_index):
            power = column_powers[y] if column_powers[y] > background_power else background_power
            if power == current_power:
                continue
            end_y = offset_y_mm + y * pixel_size_mm
            gcode.append(f"G1 X{x_mm} Y{_format_mm(end_y)} S{current_power}")
            current_power = power
        gcode.append(f"G1 X{x_mm} Y{_format_mm(active_end)} S{current_power}")
        gcode.append(f"G1 X{x_mm} Y{_format_mm(scan_end)} S0")
    return 1


def _append_scanline_raster_gcode(gcode, power_grid, pixel_size_mm, bidirectional, background_power, overscan_mm, offset_x_mm, offset_y_mm, scan_direction):
    if not power_grid:
        return 0
    image_height = len(power_grid)
    image_width = len(power_grid[0]) if image_height else 0
    active_scanlines = 0
    if scan_direction == "vertical":
        max_y_mm = DEFAULT_WORK_AREA_HEIGHT_MM if DEFAULT_WORK_AREA_HEIGHT_MM > 0 else offset_y_mm + image_height * pixel_size_mm
        for x in range(image_width):
            x_mm = _format_mm(offset_x_mm + x * pixel_size_mm)
            column_powers = [power_grid[y][x] for y in range(image_height - 1, -1, -1)]
            active_scanlines += _append_vertical_scanline(
                gcode,
                column_powers,
                x_mm,
                pixel_size_mm,
                bidirectional and active_scanlines % 2 == 1,
                background_power,
                overscan_mm,
                offset_y_mm,
                max_y_mm,
            )
        return active_scanlines

    max_x_mm = DEFAULT_WORK_AREA_WIDTH_MM if DEFAULT_WORK_AREA_WIDTH_MM > 0 else offset_x_mm + image_width * pixel_size_mm
    for y, row_powers in enumerate(power_grid):
        y_mm = _format_mm(_image_y_to_machine_mm(y, image_height, pixel_size_mm, offset_y_mm))
        active_scanlines += _append_horizontal_scanline(
            gcode,
            row_powers,
            y_mm,
            pixel_size_mm,
            bidirectional and active_scanlines % 2 == 1,
            background_power,
            overscan_mm,
            offset_x_mm,
            max_x_mm,
        )
    return active_scanlines


def generate_gcode_from_grayscale(
    image,
    width_mm=0.0,
    height_mm=0.0,
    pixel_size_mm=0.1,
    feed_rate=1200,
    travel_rate=3000,
    laser_min_power=0,
    laser_max_power=800,
    threshold=-1,
    invert=False,
    bidirectional=False,
    overscan_mm=DEFAULT_RASTER_OVERSCAN_MM,
    raster_scan_direction="auto",
    offset_x_mm=0.0,
    offset_y_mm=0.0,
):
    width_mm, error = _validate_dimension(width_mm, "width_mm")
    if error:
        return None, None, error
    height_mm, error = _validate_dimension(height_mm, "height_mm")
    if error:
        return None, None, error
    pixel_size_mm, error = _validate_number(pixel_size_mm, "pixel_size_mm", 0)
    if error:
        return None, None, error
    feed_rate, error = _validate_int(feed_rate, "feed_rate", 1)
    if error:
        return None, None, error
    travel_rate, error = _validate_int(travel_rate, "travel_rate", 1)
    if error:
        return None, None, error
    laser_min_power, error = _validate_int(laser_min_power, "laser_min_power", 0, DEFAULT_LASER_S_MAX)
    if error:
        return None, None, error
    laser_max_power, error = _validate_int(
        laser_max_power, "laser_max_power", laser_min_power, DEFAULT_LASER_S_MAX
    )
    if error:
        return None, None, error
    threshold, error = _validate_int(threshold, "threshold", -1, 255)
    if error:
        return None, None, error
    overscan_mm, error = _validate_number(overscan_mm, "overscan_mm", 0, allow_zero=True)
    if error:
        return None, None, error
    requested_scan_direction, error = _resolve_raster_scan_direction(raster_scan_direction)
    if error:
        return None, None, error
    offset_x_mm, error = _validate_number(offset_x_mm, "offset_x_mm", 0, allow_zero=True)
    if error:
        return None, None, error
    offset_y_mm, error = _validate_number(offset_y_mm, "offset_y_mm", 0, allow_zero=True)
    if error:
        return None, None, error

    resized, error = _resize_image(image, width_mm, height_mm, pixel_size_mm)
    if error:
        return None, None, error

    image_height, image_width = resized.shape[:2]
    actual_width_mm = image_width * pixel_size_mm
    actual_height_mm = image_height * pixel_size_mm
    error = _validate_work_area(actual_width_mm, actual_height_mm, offset_x_mm, offset_y_mm)
    if error:
        return None, None, error

    power_grid = _power_grid_from_grayscale(
        resized,
        laser_min_power,
        laser_max_power,
        threshold,
        invert,
    )
    resolved_scan_direction, scan_direction_source, scan_direction_reason, scanline_costs = _choose_raster_scan_direction(
        power_grid,
        requested_scan_direction,
        laser_min_power,
    )

    gcode = [
        "; Generated by laser_grbl_tool image_to_gcode raster engraving mode",
        "; Strategy: grayscale/threshold scanline raster, S0 over blank spans",
        f"; Raster scan direction: {resolved_scan_direction} ({scan_direction_source})",
        f"; Raster overscan mm: {_format_mm(overscan_mm)}",
        "; Firmware: GRBL laser mode recommended ($32=1)",
        "G21",
        "G90",
        "G94",
        ZERO_ORIGIN_COMMAND,
        "M4 S0",
        f"G0 F{travel_rate}",
        f"G1 F{feed_rate}",
    ]

    active_scanlines = _append_scanline_raster_gcode(
        gcode,
        power_grid,
        pixel_size_mm,
        bidirectional,
        laser_min_power,
        overscan_mm,
        offset_x_mm,
        offset_y_mm,
        resolved_scan_direction,
    )

    gcode.append("M5")

    stats = {
        "image_width_px": image_width,
        "image_height_px": image_height,
        "actual_width_mm": round(actual_width_mm, 3),
        "actual_height_mm": round(actual_height_mm, 3),
        "offset_x_mm": round(offset_x_mm, 3),
        "offset_y_mm": round(offset_y_mm, 3),
        "overscan_mm": round(overscan_mm, 3),
        "raster_output_strategy": "scanline",
        "raster_scan_direction_requested": requested_scan_direction,
        "raster_scan_direction": resolved_scan_direction,
        "raster_scan_direction_source": scan_direction_source,
        "raster_scan_direction_reason": scan_direction_reason,
        "raster_scanline_costs": scanline_costs,
        "active_scanline_count": active_scanlines,
        "snake_scan": bool(bidirectional and active_scanlines > 1),
        "y_axis_flipped": True,
        "line_count": len(gcode),
        "machine_profile": _machine_profile(),
    }
    return gcode, stats, None


def _build_outline_mask(resized, threshold, invert, cv2, np):
    if threshold < 0:
        threshold = 128

    if invert:
        mask = np.where(resized >= threshold, 255, 0).astype(np.uint8)
    else:
        mask = np.where(resized <= threshold, 255, 0).astype(np.uint8)

    return mask, threshold


def _trim_outline_source_image(image, threshold, invert, cv2, np):
    mask, applied_threshold = _build_outline_mask(image, threshold, invert, cv2, np)
    points = cv2.findNonZero(mask)
    if points is None:
        return image, mask, applied_threshold, None

    x, y, w, h = cv2.boundingRect(points)
    source_height, source_width = image.shape[:2]
    if x <= 0 and y <= 0 and w >= source_width and h >= source_height:
        return image, mask, applied_threshold, None

    return image[y : y + h, x : x + w], mask[y : y + h, x : x + w], applied_threshold, {
        "x_px": int(x),
        "y_px": int(y),
        "width_px": int(w),
        "height_px": int(h),
        "source_width_px": int(source_width),
        "source_height_px": int(source_height),
    }


def _contour_depth(index, hierarchy):
    depth = 0
    parent = int(hierarchy[index][3])
    while parent >= 0:
        depth += 1
        parent = int(hierarchy[parent][3])
    return depth


def _snap_axis_aligned_outline_points(points):
    if len(points) < 3:
        return points

    snapped = [[int(point[0]), int(point[1])] for point in points]
    tolerance = OUTLINE_AXIS_SNAP_TOLERANCE_PX
    dominance = OUTLINE_AXIS_SNAP_DOMINANCE_RATIO
    for index in range(len(snapped)):
        next_index = (index + 1) % len(snapped)
        x1, y1 = snapped[index]
        x2, y2 = snapped[next_index]
        dx = abs(x2 - x1)
        dy = abs(y2 - y1)
        if dx <= 0 and dy <= 0:
            continue
        if dy <= tolerance and dx >= max(1.0, dy * dominance):
            snapped_y = int(round((y1 + y2) / 2.0))
            snapped[index][1] = snapped_y
            snapped[next_index][1] = snapped_y
        elif dx <= tolerance and dy >= max(1.0, dx * dominance):
            snapped_x = int(round((x1 + x2) / 2.0))
            snapped[index][0] = snapped_x
            snapped[next_index][0] = snapped_x

    return _remove_redundant_axis_aligned_points(snapped)


def _remove_redundant_axis_aligned_points(points):
    cleaned = []
    for point in points:
        point_tuple = (int(point[0]), int(point[1]))
        if cleaned and cleaned[-1] == point_tuple:
            continue
        cleaned.append(point_tuple)

    if len(cleaned) > 1 and cleaned[0] == cleaned[-1]:
        cleaned.pop()

    changed = True
    while changed and len(cleaned) >= 3:
        changed = False
        compacted = []
        count = len(cleaned)
        for index, point in enumerate(cleaned):
            previous_point = cleaned[index - 1]
            next_point = cleaned[(index + 1) % count]
            if (
                (previous_point[0] == point[0] == next_point[0])
                or (previous_point[1] == point[1] == next_point[1])
            ):
                changed = True
                continue
            compacted.append(point)
        cleaned = compacted

    return cleaned


def _prepare_outline_contours(contours, hierarchy, image_width, image_height, cv2, skip_border_contours=True):
    if hierarchy is None or len(contours) == 0:
        return [], 0, 0, 0

    hierarchy = hierarchy[0]
    image_area = image_width * image_height
    min_area = max(1.0, image_area * OUTLINE_MIN_CONTOUR_AREA_RATIO)
    raw_items = []
    for index, contour in enumerate(contours):
        x, y, w, h = cv2.boundingRect(contour)
        touches_full_border = x <= 1 and y <= 1 and x + w >= image_width - 2 and y + h >= image_height - 2
        covers_most_image = w * h >= image_area * 0.75
        area = abs(cv2.contourArea(contour))
        raw_items.append(
            {
                "index": index,
                "contour": contour,
                "area": area,
                "is_border": touches_full_border and covers_most_image,
            }
        )

    has_non_border_candidate = any(item["area"] >= min_area and not item["is_border"] for item in raw_items)
    prepared = []
    skipped_border_contours = 0
    skipped_small_contours = 0
    simplified_point_count = 0

    for item in raw_items:
        if skip_border_contours and item["is_border"] and has_non_border_candidate:
            skipped_border_contours += 1
            continue

        index = item["index"]
        contour = item["contour"]
        area = item["area"]
        if area < min_area:
            skipped_small_contours += 1
            continue

        perimeter = cv2.arcLength(contour, True)
        epsilon = max(0.5, perimeter * OUTLINE_APPROX_EPSILON_RATIO)
        simplified = cv2.approxPolyDP(contour, epsilon, True)
        points = _snap_axis_aligned_outline_points(simplified.reshape(-1, 2))
        if len(points) < 2:
            skipped_small_contours += 1
            continue

        simplified_point_count += len(points)
        prepared.append(
            {
                "points": points,
                "depth": _contour_depth(index, hierarchy),
                "area": area,
            }
        )

    ordered = _order_outline_contours_by_nearest(prepared, image_height)
    return ordered, skipped_border_contours, skipped_small_contours, simplified_point_count


def _rotate_points_near_current(points, current_point):
    if current_point is None or len(points) <= 1:
        return points

    current_x, current_y = current_point
    nearest_index = min(
        range(len(points)),
        key=lambda index: (int(points[index][0]) - current_x) ** 2 + (int(points[index][1]) - current_y) ** 2,
    )
    if nearest_index == 0:
        return points
    return list(points[nearest_index:]) + list(points[:nearest_index])


def _order_outline_contours_by_nearest(contours, image_height, start_point=None):
    remaining = list(contours)
    ordered = []
    current_point = start_point
    if current_point is None:
        current_point = (0, max(0, image_height - 1))

    while remaining:
        current_x, current_y = current_point

        def nearest_point_distance(item):
            points = item["points"]
            return min(
                (int(point[0]) - current_x) ** 2 + (int(point[1]) - current_y) ** 2
                for point in points
            )

        next_index = min(range(len(remaining)), key=lambda index: nearest_point_distance(remaining[index]))
        item = remaining.pop(next_index).copy()
        item["points"] = _rotate_points_near_current(item["points"], current_point)
        ordered.append(item)
        current_point = tuple(int(value) for value in item["points"][0])

    return ordered


def _prepare_cut_contours(contours, image_width, image_height, cv2, skip_border_contours=False):
    prepared = []
    skipped_border_contours = 0
    for contour in sorted(contours, key=cv2.contourArea, reverse=True):
        x, y, w, h = cv2.boundingRect(contour)
        touches_full_border = x <= 1 and y <= 1 and x + w >= image_width - 1 and y + h >= image_height - 1
        covers_most_image = w * h >= image_width * image_height * 0.8
        if skip_border_contours and touches_full_border and covers_most_image:
            skipped_border_contours += 1
            continue

        points = contour.reshape(-1, 2)
        if len(points) < 2:
            continue
        prepared.append({"points": points, "area": abs(cv2.contourArea(contour))})

    ordered = _order_outline_contours_by_nearest(prepared, image_height)
    return ordered, skipped_border_contours


def _image_y_to_machine_mm(y, image_height, pixel_size_mm, offset_y_mm=0.0):
    return offset_y_mm + (image_height - 1 - y) * pixel_size_mm


def _point_to_machine_mm(point, image_height, pixel_size_mm, offset_x_mm=0.0, offset_y_mm=0.0):
    x, y = point
    return offset_x_mm + x * pixel_size_mm, _image_y_to_machine_mm(y, image_height, pixel_size_mm, offset_y_mm)


def generate_outline_gcode_from_grayscale(
    image,
    width_mm=0.0,
    height_mm=0.0,
    pixel_size_mm=0.1,
    feed_rate=1200,
    travel_rate=3000,
    laser_max_power=800,
    threshold=-1,
    invert=False,
    offset_x_mm=0.0,
    offset_y_mm=0.0,
):
    width_mm, error = _validate_dimension(width_mm, "width_mm")
    if error:
        return None, None, error
    height_mm, error = _validate_dimension(height_mm, "height_mm")
    if error:
        return None, None, error
    pixel_size_mm, error = _validate_number(pixel_size_mm, "pixel_size_mm", 0)
    if error:
        return None, None, error
    feed_rate, error = _validate_int(feed_rate, "feed_rate", 1)
    if error:
        return None, None, error
    travel_rate, error = _validate_int(travel_rate, "travel_rate", 1)
    if error:
        return None, None, error
    laser_max_power, error = _validate_int(laser_max_power, "laser_max_power", 0, DEFAULT_LASER_S_MAX)
    if error:
        return None, None, error
    threshold, error = _validate_int(threshold, "threshold", -1, 255)
    if error:
        return None, None, error
    offset_x_mm, error = _validate_number(offset_x_mm, "offset_x_mm", 0, allow_zero=True)
    if error:
        return None, None, error
    offset_y_mm, error = _validate_number(offset_y_mm, "offset_y_mm", 0, allow_zero=True)
    if error:
        return None, None, error

    cv2, np, error = _load_image_libs()
    if error:
        return None, None, error

    _source_image, source_mask, applied_threshold, trim_box = _trim_outline_source_image(
        image, threshold, invert, cv2, np
    )

    mask, error = _resize_image(
        source_mask,
        width_mm,
        height_mm,
        pixel_size_mm,
        interpolation=cv2.INTER_NEAREST,
        cv2_module=cv2,
    )
    if error:
        return None, None, error

    image_height, image_width = mask.shape[:2]
    actual_width_mm = image_width * pixel_size_mm
    actual_height_mm = image_height * pixel_size_mm
    error = _validate_work_area(actual_width_mm, actual_height_mm, offset_x_mm, offset_y_mm)
    if error:
        return None, None, error

    contours, hierarchy = cv2.findContours(mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_TC89_KCOS)
    outline_contours, skipped_border_contours, skipped_small_contours, point_count = _prepare_outline_contours(
        contours, hierarchy, image_width, image_height, cv2, skip_border_contours=trim_box is None
    )
    if not outline_contours:
        contours, hierarchy = cv2.findContours(mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        outline_contours, skipped_border_contours, skipped_small_contours, point_count = _prepare_outline_contours(
            contours, hierarchy, image_width, image_height, cv2, skip_border_contours=trim_box is None
        )
    if not outline_contours:
        return None, None, "未从图片中提取到可雕刻轮廓，请检查图片或 threshold"

    gcode = [
        "; Generated by laser_grbl_tool image_to_gcode outline engraving mode",
        "; Strategy: sharp threshold + OpenCV vector outline, no raster line-to-line scan",
        "; Firmware: GRBL laser mode recommended ($32=1)",
        "G21",
        "G90",
        "G94",
        ZERO_ORIGIN_COMMAND,
        "M4 S0",
        f"G0 F{travel_rate}",
        f"G1 F{feed_rate}",
    ]

    current_point = None
    engraved_contours = 0
    laser_is_off = True
    for item in outline_contours:
        points = _rotate_points_near_current(item["points"], current_point)
        first = points[0]
        first_x_mm, first_y_mm = _point_to_machine_mm(
            first, image_height, pixel_size_mm, offset_x_mm, offset_y_mm
        )
        if not laser_is_off:
            gcode.append("M5")
            laser_is_off = True
        gcode.append(f"G0 X{_format_mm(first_x_mm)} Y{_format_mm(first_y_mm)}")
        gcode.append(f"M4 S{laser_max_power}")
        laser_is_off = False
        for point in points[1:]:
            x_mm, y_mm = _point_to_machine_mm(point, image_height, pixel_size_mm, offset_x_mm, offset_y_mm)
            gcode.append(f"G1 X{_format_mm(x_mm)} Y{_format_mm(y_mm)}")
        gcode.append(f"G1 X{_format_mm(first_x_mm)} Y{_format_mm(first_y_mm)}")
        gcode.append("M5")
        laser_is_off = True
        current_point = (int(first[0]), int(first[1]))
        engraved_contours += 1

    if not laser_is_off:
        gcode.append("M5")
    stats = {
        "image_width_px": image_width,
        "image_height_px": image_height,
        "actual_width_mm": round(actual_width_mm, 3),
        "actual_height_mm": round(actual_height_mm, 3),
        "offset_x_mm": round(offset_x_mm, 3),
        "offset_y_mm": round(offset_y_mm, 3),
        "contour_count": engraved_contours,
        "skipped_border_contours": skipped_border_contours,
        "skipped_small_contours": skipped_small_contours,
        "outline_point_count": point_count,
        "threshold": applied_threshold,
        "trim_box": trim_box,
        "y_axis_flipped": True,
        "line_count": len(gcode),
        "machine_profile": _machine_profile(),
    }
    return gcode, stats, None


def generate_cut_gcode_from_grayscale(
    image,
    width_mm=0.0,
    height_mm=0.0,
    pixel_size_mm=0.1,
    feed_rate=100,
    travel_rate=3000,
    laser_max_power=1000,
    threshold=128,
    invert=False,
    include_inner_contours=False,
    skip_border_contours=False,
    offset_x_mm=0.0,
    offset_y_mm=0.0,
):
    width_mm, error = _validate_dimension(width_mm, "width_mm")
    if error:
        return None, None, error
    height_mm, error = _validate_dimension(height_mm, "height_mm")
    if error:
        return None, None, error
    pixel_size_mm, error = _validate_number(pixel_size_mm, "pixel_size_mm", 0)
    if error:
        return None, None, error
    feed_rate, error = _validate_int(feed_rate, "feed_rate", 1)
    if error:
        return None, None, error
    travel_rate, error = _validate_int(travel_rate, "travel_rate", 1)
    if error:
        return None, None, error
    laser_max_power, error = _validate_int(laser_max_power, "laser_max_power", 0, DEFAULT_LASER_S_MAX)
    if error:
        return None, None, error
    threshold, error = _validate_int(threshold, "threshold", -1, 255)
    if error:
        return None, None, error
    if threshold < 0:
        threshold = 128
    offset_x_mm, error = _validate_number(offset_x_mm, "offset_x_mm", 0, allow_zero=True)
    if error:
        return None, None, error
    offset_y_mm, error = _validate_number(offset_y_mm, "offset_y_mm", 0, allow_zero=True)
    if error:
        return None, None, error

    cv2, np, error = _load_image_libs()
    if error:
        return None, None, error

    resized, error = _resize_image(image, width_mm, height_mm, pixel_size_mm)
    if error:
        return None, None, error

    if invert:
        mask = np.where(resized >= threshold, 255, 0).astype(np.uint8)
    else:
        mask = np.where(resized <= threshold, 255, 0).astype(np.uint8)

    retrieval_mode = cv2.RETR_LIST if include_inner_contours else cv2.RETR_EXTERNAL
    contours, _ = cv2.findContours(mask, retrieval_mode, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None, None, "未从图片中提取到可切割轮廓，请检查图片或 threshold"

    image_height, image_width = resized.shape[:2]
    actual_width_mm = image_width * pixel_size_mm
    actual_height_mm = image_height * pixel_size_mm
    error = _validate_work_area(actual_width_mm, actual_height_mm, offset_x_mm, offset_y_mm)
    if error:
        return None, None, error

    gcode = [
        "; Generated by laser_grbl_tool image_to_gcode cut mode",
        "; Firmware: GRBL laser mode recommended ($32=1)",
        "G21",
        "G90",
        "G94",
        ZERO_ORIGIN_COMMAND,
        "M4 S0",
        f"G0 F{travel_rate}",
        f"G1 F{feed_rate}",
    ]

    cut_items, skipped_border_contours = _prepare_cut_contours(
        contours,
        image_width,
        image_height,
        cv2,
        skip_border_contours=skip_border_contours,
    )
    cut_contours = 0
    current_point = (0, max(0, image_height - 1))
    for item in cut_items:
        points = _rotate_points_near_current(item["points"], current_point)

        first_x, first_y = points[0]
        first_x_mm, first_y_mm = _point_to_machine_mm(
            (first_x, first_y), image_height, pixel_size_mm, offset_x_mm, offset_y_mm
        )
        gcode.append(f"G0 X{_format_mm(first_x_mm)} Y{_format_mm(first_y_mm)}")
        gcode.append(f"S{laser_max_power}")
        for x, y in points[1:]:
            x_mm, y_mm = _point_to_machine_mm((x, y), image_height, pixel_size_mm, offset_x_mm, offset_y_mm)
            gcode.append(f"G1 X{_format_mm(x_mm)} Y{_format_mm(y_mm)}")
        gcode.append(f"G1 X{_format_mm(first_x_mm)} Y{_format_mm(first_y_mm)}")
        gcode.append("S0")
        current_point = (int(first_x), int(first_y))
        cut_contours += 1

    if cut_contours == 0:
        return None, None, "提取到的轮廓点不足，无法生成切割路径"

    gcode.extend(["M5", "G0 X0 Y0"])
    stats = {
        "image_width_px": image_width,
        "image_height_px": image_height,
        "actual_width_mm": round(actual_width_mm, 3),
        "actual_height_mm": round(actual_height_mm, 3),
        "offset_x_mm": round(offset_x_mm, 3),
        "offset_y_mm": round(offset_y_mm, 3),
        "contour_count": cut_contours,
        "cut_path_ordering": "nearest",
        "include_inner_contours": include_inner_contours,
        "skipped_border_contours": skipped_border_contours,
        "y_axis_flipped": True,
        "line_count": len(gcode),
        "machine_profile": _machine_profile(),
    }
    return gcode, stats, None


def convert_image_to_gcode(
    image_file="",
    output_file="",
    width_mm=0.0,
    height_mm=0.0,
    pixel_size_mm=0.1,
    feed_rate=1200,
    travel_rate=3000,
    laser_min_power=0,
    laser_max_power=800,
    threshold=-1,
    invert=False,
    bidirectional=False,
    overscan_mm=DEFAULT_RASTER_OVERSCAN_MM,
    raster_scan_direction="auto",
    overwrite=True,
    laser_mode="engrave",
    engraving_mode="",
    auto_trim=True,
    trim_tolerance=20,
    auto_size=True,
    dpi=300.0,
    lock_aspect_ratio=True,
    offset_x_mm=0.0,
    offset_y_mm=0.0,
    safe_margin_mm=DEFAULT_SAFE_MARGIN_MM,
):
    laser_mode, error = _resolve_laser_mode(laser_mode)
    if error:
        return {"success": False, "result": error}

    image_file, error = _resolve_image_file(image_file)
    if error:
        return {"success": False, "result": error}

    output_file, error = _resolve_output_gcode_file(output_file, image_file)
    if error:
        return {"success": False, "result": error}

    if os.path.exists(output_file) and not overwrite:
        return {"success": False, "result": f"输出文件已存在: {output_file}"}

    image, error = _read_grayscale_image(image_file)
    if error:
        return {"success": False, "result": error}

    if auto_trim:
        image, auto_trim_info = _auto_trim_image(image, trim_tolerance)
    else:
        image_height, image_width = image.shape[:2]
        auto_trim_info = {
            "enabled": False,
            "trimmed": False,
            "original_width_px": int(image_width),
            "original_height_px": int(image_height),
            "trimmed_width_px": int(image_width),
            "trimmed_height_px": int(image_height),
            "crop_box": {
                "x_px": 0,
                "y_px": 0,
                "width_px": int(image_width),
                "height_px": int(image_height),
            },
        }

    source_height, source_width = image.shape[:2]
    placement, error = _resolve_image_placement(
        image_file,
        source_width,
        source_height,
        width_mm=width_mm,
        height_mm=height_mm,
        auto_size=auto_size,
        dpi=dpi,
        lock_aspect_ratio=lock_aspect_ratio,
        offset_x_mm=offset_x_mm,
        offset_y_mm=offset_y_mm,
        safe_margin_mm=safe_margin_mm,
    )
    if error:
        return {"success": False, "result": error, "auto_trim": auto_trim_info}
    resolved_width_mm = placement["final_width_mm"]
    resolved_height_mm = placement["final_height_mm"]
    resolved_offset_x_mm = placement["offset_x_mm"]
    resolved_offset_y_mm = placement["offset_y_mm"]

    if laser_mode == "cut":
        resolved_engraving_mode = "cut"
        gcode, stats, error = generate_cut_gcode_from_grayscale(
            image,
            width_mm=resolved_width_mm,
            height_mm=resolved_height_mm,
            pixel_size_mm=pixel_size_mm,
            feed_rate=feed_rate,
            travel_rate=travel_rate,
            laser_max_power=laser_max_power,
            threshold=threshold,
            invert=invert,
            offset_x_mm=resolved_offset_x_mm,
            offset_y_mm=resolved_offset_y_mm,
        )
    else:
        resolved_engraving_mode, error = _resolve_engraving_mode(engraving_mode)
        if error:
            return {"success": False, "result": error}
        if resolved_engraving_mode == "outline":
            gcode, stats, error = generate_outline_gcode_from_grayscale(
                image,
                width_mm=resolved_width_mm,
                height_mm=resolved_height_mm,
                pixel_size_mm=pixel_size_mm,
                feed_rate=feed_rate,
                travel_rate=travel_rate,
                laser_max_power=laser_max_power,
                threshold=threshold,
                invert=invert,
                offset_x_mm=resolved_offset_x_mm,
                offset_y_mm=resolved_offset_y_mm,
            )
        else:
            gcode, stats, error = generate_gcode_from_grayscale(
                image,
                width_mm=resolved_width_mm,
                height_mm=resolved_height_mm,
                pixel_size_mm=pixel_size_mm,
                feed_rate=feed_rate,
                travel_rate=travel_rate,
                laser_min_power=laser_min_power,
                laser_max_power=laser_max_power,
                threshold=threshold,
                invert=invert,
                bidirectional=bidirectional,
                overscan_mm=overscan_mm,
                raster_scan_direction=raster_scan_direction,
                offset_x_mm=resolved_offset_x_mm,
                offset_y_mm=resolved_offset_y_mm,
            )
    if error:
        return {"success": False, "result": error}
    placement_error = _validate_placement_safe_area(
        placement,
        stats.get("actual_width_mm", placement["final_width_mm"]),
        stats.get("actual_height_mm", placement["final_height_mm"]),
    )
    if placement_error:
        return {"success": False, "result": placement_error, "auto_trim": auto_trim_info, "placement": placement}
    placement = _finalize_placement_with_actual_size(placement, stats)

    try:
        with open(output_file, "w", encoding="utf-8", newline="\n") as file:
            file.write("\n".join(gcode))
            file.write("\n")
    except OSError as exc:
        return {"success": False, "result": f"写入 G-code 文件失败: {exc}"}

    stats.update(
        {
            "image_file": image_file,
            "gcode_file": output_file,
            "laser_mode": laser_mode,
            "engraving_mode": resolved_engraving_mode,
            "mode": resolved_engraving_mode,
            "auto_trim": auto_trim_info,
            "placement": placement,
        }
    )
    laser_time_estimate.add_time_estimate_fields(stats, output_file)
    return {"success": True, "result": stats}


def _resolve_send_source_file(gcode_file, image_file):
    # 用户只说文件名时，只到默认雕刻目录查找，避免误用桌面或当前目录同名文件。
    if gcode_file:
        if _is_plain_filename(gcode_file):
            found = _find_engraving_file(gcode_file)
            if found:
                return found, None
            return None, f"未在雕刻文件目录中找到文件: {gcode_file}，目录: {_normalize_path(DEFAULT_ENGRAVING_DIR)}"

        normalized = _normalize_path(gcode_file)
        if os.path.isfile(normalized):
            return normalized, None
        # 用户提供的文件不存在，回退到默认

    if image_file:
        if _is_plain_filename(image_file):
            found = _find_engraving_file(image_file)
            if found:
                return found, None
            return None, f"未在雕刻文件目录中找到文件: {image_file}，目录: {_normalize_path(DEFAULT_ENGRAVING_DIR)}"

        normalized = _normalize_path(image_file)
        if os.path.isfile(normalized):
            return normalized, None

    # 回退到默认 G-code 文件
    if DEFAULT_GCODE_FILE:
        normalized = _normalize_path(DEFAULT_GCODE_FILE)
        if os.path.isfile(normalized):
            return normalized, None

    # 回退到默认图片文件
    if DEFAULT_IMAGE_FILE:
        normalized = _normalize_path(DEFAULT_IMAGE_FILE)
        if os.path.isfile(normalized):
            return normalized, None

    single_file, single_error = _find_single_default_engraving_file()
    if single_file:
        return single_file, None
    if single_error:
        return None, single_error

    return None, f"未找到可用的雕刻文件。请在雕刻文件目录放入文件或指定文件名，目录: {_normalize_path(DEFAULT_ENGRAVING_DIR)}"


def prepare_gcode_file_for_sending(
    gcode_file="",
    image_file="",
    output_file="",
    width_mm=0.0,
    height_mm=0.0,
    pixel_size_mm=0.1,
    feed_rate=1200,
    travel_rate=3000,
    laser_min_power=0,
    laser_max_power=800,
    threshold=-1,
    invert=False,
    bidirectional=False,
    overscan_mm=DEFAULT_RASTER_OVERSCAN_MM,
    raster_scan_direction="auto",
    overwrite=True,
    laser_mode="engrave",
    engraving_mode="",
    auto_trim=True,
    trim_tolerance=20,
    auto_size=True,
    dpi=300.0,
    lock_aspect_ratio=True,
    offset_x_mm=0.0,
    offset_y_mm=0.0,
    safe_margin_mm=DEFAULT_SAFE_MARGIN_MM,
):
    source_file, error = _resolve_send_source_file(gcode_file, image_file)
    if error:
        return {"success": False, "result": error}

    if _path_has_extension(source_file, GCODE_EXTENSIONS):
        bounds, error = inspect_gcode_file_bounds(source_file)
        if error:
            return {"success": False, "result": error}
        try:
            from core.laser_runtime.models import file_sha256
            digest = file_sha256(source_file)
        except OSError:
            return {
                "success": False,
                "error_code": "preview_content_mismatch",
                "result": "无法计算 G-code 摘要",
            }
        prepared_result = {
            "source_file": source_file,
            "gcode_file": source_file,
            "converted": False,
            "gcode_bounds": bounds,
            "expected_gcode_sha256": digest,
        }
        laser_time_estimate.add_time_estimate_fields(prepared_result, source_file)
        return {
            "success": True,
            "result": prepared_result,
        }

    if _path_has_extension(source_file, IMAGE_EXTENSIONS):
        conversion = convert_image_to_gcode(
            image_file=source_file,
            output_file=output_file,
            width_mm=width_mm,
            height_mm=height_mm,
            pixel_size_mm=pixel_size_mm,
            feed_rate=feed_rate,
            travel_rate=travel_rate,
            laser_min_power=laser_min_power,
            laser_max_power=laser_max_power,
            threshold=threshold,
            invert=invert,
            bidirectional=bidirectional,
            overscan_mm=overscan_mm,
            raster_scan_direction=raster_scan_direction,
            overwrite=overwrite,
            laser_mode=laser_mode,
            engraving_mode=engraving_mode,
            auto_trim=auto_trim,
            trim_tolerance=trim_tolerance,
            auto_size=auto_size,
            dpi=dpi,
            lock_aspect_ratio=lock_aspect_ratio,
            offset_x_mm=offset_x_mm,
            offset_y_mm=offset_y_mm,
            safe_margin_mm=safe_margin_mm,
        )
        if not conversion["success"]:
            return conversion

        result = conversion["result"]
        bounds, error = inspect_gcode_file_bounds(result["gcode_file"])
        if error:
            return {"success": False, "result": error, "detail": conversion}
        try:
            from core.laser_runtime.models import file_sha256
            digest = file_sha256(result["gcode_file"])
        except OSError:
            return {
                "success": False,
                "error_code": "preview_content_mismatch",
                "result": "无法计算 G-code 摘要",
            }
        prepared_result = {
            "source_file": source_file,
            "gcode_file": result["gcode_file"],
            "converted": True,
            "conversion": result,
            "gcode_bounds": bounds,
            "expected_gcode_sha256": digest,
        }
        laser_time_estimate.add_time_estimate_fields(prepared_result, result["gcode_file"])
        return {
            "success": True,
            "result": prepared_result,
        }

    supported = ", ".join(GCODE_EXTENSIONS + IMAGE_EXTENSIONS)
    return {"success": False, "result": f"文件类型不支持: {source_file}，支持: {supported}"}


def _list_serial_ports(list_ports):
    """列出可用的串口"""
    ports = list_ports.comports()
    return [{'port': p.device, 'desc': p.description} for p in ports]


def _send_gcode(ser, line, timeout=5):
    """发送一行 G-code 并等待 ok 响应"""
    ser.write((line + '\n').encode())
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = ser.readline().decode(errors='replace').strip()
        if not resp:
            continue
        if resp == 'ok':
            return True, None
        if resp.startswith('error'):
            return False, resp
    return False, f"等待 GRBL 响应超时: {line}"


def _auto_detect_grbl_port(baudrate, serial_module, list_ports):
    """遍历所有串口，发送唤醒命令，找到响应 GRBL 版本号的端口。

    仅服务 send_command / list_ports 等非完整文件能力；完整文件发送由 core.laser_execution 负责。
    """
    ports = [p.device for p in list_ports.comports()]
    for port in ports:
        try:
            ser = serial_module.Serial(port, baudrate, timeout=2)
            time.sleep(1)
            ser.reset_input_buffer()
            ser.write(b"\n")
            deadline = time.time() + 2
            while time.time() < deadline:
                resp = ser.readline().decode(errors='replace').strip()
                if 'Grbl' in resp or 'grbl' in resp:
                    ser.close()
                    return port
            ser.close()
        except (serial_module.SerialException, OSError):
            continue
    return None


def _connect_grbl_serial(port, baudrate, serial_module, list_ports):
    """打开串口连接，供 send_command / 连接检查等非完整文件路径使用。"""
    configured_port = _resolve_port(port)
    available_ports = _list_serial_ports(list_ports)
    tried = []
    candidates = []

    if configured_port:
        candidates.append(configured_port)
    else:
        detected_port = _auto_detect_grbl_port(baudrate, serial_module, list_ports)
        if detected_port:
            candidates.append(detected_port)

    if not candidates:
        detail = {
            "configured_port": configured_port,
            "available_ports": available_ports,
        }
        return None, None, _build_failure("未检测到可用的 GRBL 串口", detail)

    for candidate in candidates:
        try:
            ser = serial_module.Serial(candidate, baudrate, timeout=1)
            time.sleep(2)
            ser.write(b"\n")
            time.sleep(0.5)
            ser.reset_input_buffer()
            return ser, candidate, None
        except Exception as exc:
            tried.append({"port": candidate, "error": str(exc)})

    detail = {
        "configured_port": configured_port,
        "available_ports": available_ports,
        "tried_ports": tried,
    }
    return None, None, _build_failure("无法连接激光雕刻机串口", detail)


def _build_failure(message, detail=None):
    payload = {"success": False, "result": message}
    if detail is not None:
        payload["detail"] = detail
    return payload


READ_ONLY_COMMANDS = {"?", "$$", "$+", "$G", "$#", "$I", "$N", "$CMD", "$A", "$E"}


def _strip_inline_comment(command):
    command = str(command or "").strip()
    if not command or command.startswith(";"):
        return ""
    return command.split(";", 1)[0].strip()


def classify_manual_grbl_command(command):
    command = _strip_inline_comment(command)
    if not command:
        return "empty"

    upper = command.upper()
    first = upper.split()[0]
    if upper in READ_ONLY_COMMANDS:
        return "read_only"
    if upper in ("~", "!", "\x18"):
        return "destructive"
    if upper.startswith("$RST") or upper.startswith("$NVX"):
        return "destructive"
    if upper.startswith("$J=") or upper in ("$H", "$X", "$C"):
        return "motion"
    if upper.startswith("$") and "=" in upper:
        return "config_write"
    if first in ("G0", "G00", "G1", "G01", "G2", "G02", "G3", "G03", "G92"):
        return "motion"
    if first in ("G90", "G91") and any(code in upper.split() for code in ("G0", "G00", "G1", "G01")):
        return "motion"
    if first in ("M3", "M03", "M4", "M04", "M5", "M05"):
        return "laser"
    return "unknown"


def _manual_command_requires_confirmation(classification, manual_mode=True):
    if classification in ("empty", "read_only"):
        return False
    if classification == "unknown" and not manual_mode:
        return True
    return True


def send_serial_command(
    gcode_command,
    port="",
    baudrate=DEFAULT_BAUDRATE,
    confirmed=False,
    dry_run=False,
    manual_mode=True,
):
    command = _strip_inline_comment(gcode_command)
    if not command:
        return _build_failure("请指定 G-code 命令（gcode_command）")

    classification = classify_manual_grbl_command(command)
    detail = {
        "command": command,
        "classification": classification,
        "confirmation_required": _manual_command_requires_confirmation(
            classification, manual_mode=manual_mode
        ),
    }
    if detail["confirmation_required"] and not confirmed:
        return _build_failure("该串口命令会影响机器状态，必须 confirmed=true 后才能发送", detail)
    if dry_run:
        return {"success": True, "result": detail}

    serial_module, list_ports, serial_error = _load_serial()
    if serial_error:
        return _build_failure(serial_error)

    try:
        ser, connected_port, error_payload = _connect_grbl_serial(
            port, baudrate, serial_module, list_ports
        )
        if error_payload:
            return error_payload

        try:
            success, error = _send_gcode(ser, command)
        finally:
            ser.close()

        detail.update({"port": connected_port, "baudrate": baudrate})
        if not success:
            detail["send_error"] = error
            return _build_failure(f"发送命令失败: {error}", detail)

        return {
            "success": True,
            "result": f"发送命令成功，串口: {connected_port}，命令: {command}",
            "detail": detail,
        }
    except Exception as exc:
        detail.update(
            {
                "configured_port": _resolve_port(port),
                "baudrate": baudrate,
                "error": str(exc),
            }
        )
        return _build_failure(f"发送命令失败: {exc}", detail)


# 默认 G-code 文件路径；未配置时优先使用默认雕刻目录中的文件。
DEFAULT_GCODE_FILE = _SETTINGS.default_gcode_file

def register_tool(mcp):
    @mcp.tool()
    def laser_grbl_tool(action: str, port: str = '', baudrate: int = DEFAULT_BAUDRATE,
                        gcode_file: str = '', gcode_command: str = '',
                        image_file: str = '', output_file: str = '',
                        width_mm: float = 0.0, height_mm: float = 0.0,
                        pixel_size_mm: float = 0.1, feed_rate: int = 1200,
                        travel_rate: int = 3000, laser_min_power: int = 0,
                        laser_max_power: int = 800, threshold: int = -1,
                        invert: bool = False, bidirectional: bool = False,
                        overscan_mm: float = DEFAULT_RASTER_OVERSCAN_MM,
                        raster_scan_direction: str = 'auto',
                        overwrite: bool = True, laser_mode: str = 'engrave',
                        engraving_mode: str = '',
                        auto_trim: bool = True, trim_tolerance: float = 20,
                        auto_size: bool = True, dpi: float = 300.0,
                        lock_aspect_ratio: bool = True,
                        offset_x_mm: float = 0.0, offset_y_mm: float = 0.0,
                        safe_margin_mm: float = DEFAULT_SAFE_MARGIN_MM,
                        wait_for_response: bool = True,
                        run_in_background: bool = True, confirmed: bool = False,
                        job_id: str = '') -> dict:
        """
        GRBL 串口激光雕刻机工具。不要因为用户说"帮我刻 xxx"调用本工具 send_file；文字内容必须先用 generate_text_laser_task_tool 生成任务草稿。
        关键语义：send_file 是完整雕刻任务入口；它会在需要时先把图片转为 G-code，再先发送零点命令，然后逐行发送整份 G-code 文件。
        send_command 只发送一条手动 G-code 命令，不能替代 send_file 来执行整份雕刻任务。
        重要：send_file 只用于用户明确要求发送已有文件或明确要求使用默认雕刻文件；必须 confirmed=true，且发送前会先用只读 ? 检查设备在线。
        完整文件发送/状态/取消强制委托 core.laser_execution；后台 worker 仅由 core._laser_execution_backend 提供。

        action 操作类型:
            'machine_profile' - 查看当前软件采用的激光雕刻机硬件基线、行程范围和 GRBL S 值上限
            'send_file' - 发送完整雕刻任务到雕刻机。只用于用户明确发送已有 G-code/图片文件或明确使用默认雕刻文件；必须 confirmed=true。默认创建后台任务并等待每行 GRBL ok；如需同步等待完成，传 run_in_background=false。
            'job_status' - 查询后台雕刻任务状态，需要 job_id
            'cancel_job' - 仅当用户明确说"中断/停止/取消雕刻任务"时调用，需要 job_id；会终止独立发送进程并停止发送后续 G-code
            'list_ports' - 列出可用串口（无需其他参数）
            'image_to_gcode' - 仅将图片转换为 G-code 文件保存到本地（不连接串口，需要 image_file 为真实存在的绝对路径）。laser_mode='engrave' 默认使用 raster/线扫填充雕刻；用户明确要求 outline/轮廓时传 engraving_mode='outline'；laser_mode='cut' 为激光切割轮廓
            'send_command' - 连接串口并发送单条手动 G-code 命令（需要 gcode_command，port 可选自动检测）；它不会发送整份文件，也不会执行图片雕刻任务

        使用示例:
            用户说"帮我刻 佳佳" -> 不要调用本工具，改用 generate_text_laser_task_tool 生成文字任务草稿
            用户说"打印三角形文件" -> action='send_file'，gcode_file='三角形'；系统只会在 LASERGRBL_JOB_DIR 对应目录查找三角形.gcode/.nc/图片文件
            用户说"发送 xxx.png 文件" -> action='send_file'，image_file 填该图片的绝对路径或默认雕刻目录内的文件名
            用户说"发送 $$ 查看配置" -> action='send_command'，gcode_command='$$'
            用户说"查看串口" -> action='list_ports'

        port: 串口号，如 'COM3'，不指定则使用 GRBL_DEFAULT_PORT 或自动检测 GRBL 设备
        baudrate: 波特率，默认读取 GRBL_BAUDRATE，未配置则 115200
        gcode_file: 待发送 G-code 文件路径（.nc/.gcode）或默认雕刻目录内的文件名；不指定则使用默认雕刻文件
        gcode_command: 单条 G-code 命令，如 'G0 X10 Y10' 或 '$$'（查看设置）；仅用于手动控制，不用于发送整份雕刻任务
        image_file: 图片绝对路径，仅在用户明确提供了图片时使用
        output_file: 输出 .gcode/.nc 路径，仅在 image_to_gcode 时使用
        width_mm/height_mm/pixel_size_mm: 输出尺寸与像素步距。默认先自动裁边并按图片 DPI 或 dpi=300 换算自然尺寸；超过安全区时等比例缩小。
            默认硬件基线是翼宿 V1.0，行程 100mm x 100mm；safe_margin_mm=5 时安全雕刻区为 90mm x 90mm。
        auto_trim/trim_tolerance: 是否自动裁掉白边、透明边或纯色背景边，以及背景容差。
        auto_size/dpi/lock_aspect_ratio: 是否优先用图片内置 DPI；无内置 DPI 或 auto_size=false 时使用 dpi；手动只给宽或高时 lock_aspect_ratio=true 会按图片比例补齐另一边。
        offset_x_mm/offset_y_mm: 图片相对当前原点的摆放偏移；偏移加最终尺寸超过安全区会返回错误。
        feed_rate/travel_rate: 雕刻速度和空移速度，单位 mm/min
        laser_min_power/laser_max_power: GRBL S 功率范围，默认最大值受 GRBL_LASER_S_MAX=1000 限制
        laser_mode: 'engrave'/'雕刻' 生成雕刻 G-code；'cut'/'切割' 生成轮廓切割 G-code。
        engraving_mode: 雕刻策略，默认 raster/线扫填充；用户明确要求 outline/轮廓/线雕时传 'outline'。
        threshold: -1 使用灰度功率映射；0-255 表示指定黑白阈值；outline 模式下 -1 会使用默认 128 轮廓阈值；invert: 是否反相；overwrite: 是否覆盖输出文件
        bidirectional: raster 行扫是否蛇形往返；overscan_mm: raster 每条有效扫描线开光前后的空跑缓冲；raster_scan_direction: auto/horizontal/vertical
        wait_for_response: 仅 send_file 使用；true 表示每行等待 ok/error，false 表示逐行直接写入串口不等待响应
        run_in_background: 仅 send_file 使用；true 表示立即返回 job_id，false 表示兼容旧行为同步等待发送完成
        confirmed: 仅 send_file 使用；必须 true 才会检查设备并发送，默认 false 只返回确认预览
        job_id: job_status/cancel_job 使用；send_file 后台模式返回的任务 ID
        """
        serial_module = None
        try:
            if action in ('machine_profile', 'profile'):
                return {"success": True, "result": _machine_profile()}

            if action == 'job_status':
                return laser_execution.job_status(job_id, "serial")

            if action in ('cancel_job', 'cancel', 'stop_job'):
                return laser_execution.cancel_job(job_id, "serial")

            if action in ('image_to_gcode', 'convert_image'):
                return convert_image_to_gcode(
                    image_file=image_file,
                    output_file=output_file,
                    width_mm=width_mm,
                    height_mm=height_mm,
                    pixel_size_mm=pixel_size_mm,
                    feed_rate=feed_rate,
                    travel_rate=travel_rate,
                    laser_min_power=laser_min_power,
                    laser_max_power=laser_max_power,
                    threshold=threshold,
                    invert=invert,
                    bidirectional=bidirectional,
                    overscan_mm=overscan_mm,
                    raster_scan_direction=raster_scan_direction,
                    overwrite=overwrite,
                    laser_mode=laser_mode,
                    engraving_mode=engraving_mode,
                    auto_trim=auto_trim,
                    trim_tolerance=trim_tolerance,
                    auto_size=auto_size,
                    dpi=dpi,
                    lock_aspect_ratio=lock_aspect_ratio,
                    offset_x_mm=offset_x_mm,
                    offset_y_mm=offset_y_mm,
                    safe_margin_mm=safe_margin_mm,
                )

            if action == 'list_ports':
                serial_module, list_ports, serial_error = _load_serial()
                if serial_error:
                    return {"success": False, "result": serial_error}

                ports = _list_serial_ports(list_ports)
                if not ports:
                    return {"success": False, "result": "未发现可用串口"}
                return {"success": True, "result": ports}

            elif action == 'send_file':
                prepared = prepare_gcode_file_for_sending(
                    gcode_file=gcode_file,
                    image_file=image_file,
                    output_file=output_file,
                    width_mm=width_mm,
                    height_mm=height_mm,
                    pixel_size_mm=pixel_size_mm,
                    feed_rate=feed_rate,
                    travel_rate=travel_rate,
                    laser_min_power=laser_min_power,
                    laser_max_power=laser_max_power,
                    threshold=threshold,
                    invert=invert,
                    bidirectional=bidirectional,
                    overscan_mm=overscan_mm,
                    raster_scan_direction=raster_scan_direction,
                    overwrite=overwrite,
                    laser_mode=laser_mode,
                    engraving_mode=engraving_mode,
                    auto_trim=auto_trim,
                    trim_tolerance=trim_tolerance,
                    auto_size=auto_size,
                    dpi=dpi,
                    lock_aspect_ratio=lock_aspect_ratio,
                    offset_x_mm=offset_x_mm,
                    offset_y_mm=offset_y_mm,
                    safe_margin_mm=safe_margin_mm,
                )
                if not prepared["success"]:
                    return prepared

                prepared_result = prepared["result"]
                return laser_execution.send_file(
                    prepared_result,
                    "serial",
                    confirmed=confirmed,
                    run_in_background=run_in_background,
                    port=port,
                    baudrate=baudrate,
                    wait_for_response=wait_for_response,
                )

            elif action == 'send_command':
                return send_serial_command(
                    gcode_command,
                    port=port,
                    baudrate=baudrate,
                    confirmed=confirmed,
                    manual_mode=True,
                )

            else:
                return {"success": False, "result": f"未知操作: {action}，支持: machine_profile / image_to_gcode / list_ports / send_file / job_status / cancel_job / send_command"}

        except Exception as e:
            if serial_module is not None and isinstance(e, serial_module.SerialException):
                return _build_failure(f"串口异常: {e}")
            return _build_failure(f"执行失败: {e}")
