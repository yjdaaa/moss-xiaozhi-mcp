import math
import os

from core import laser_time_estimate
from tools import laser_material_calibration_tool, laser_grbl_tool, text_image_tool

DEFAULT_OUTLINE_MAX_LAYOUT_WIDTH_MM = 90.0
DEFAULT_OUTLINE_MAX_LAYOUT_HEIGHT_MM = 90.0
TEXT_VECTOR_CURVE_STEP_PX = 2.5
TEXT_VECTOR_MIN_CURVE_SEGMENTS = 4
TEXT_VECTOR_MAX_CURVE_SEGMENTS = 96
TEXT_VECTOR_DUPLICATE_POINT_TOLERANCE_PX = 0.01
TEXT_VECTOR_OUTLINE_FALLBACK_CODES = {
    "missing_fonttools",
    "missing_font_path",
    "missing_font_file",
    "empty_font_collection",
    "invalid_font_units",
    "font_read_error",
    "layout_font_missing",
    "layout_font_load_error",
    "layout_metadata_invalid",
    "layout_dimensions_invalid",
    "missing_glyphs",
    "glyph_read_error",
    "empty_glyph_contours",
    "invalid_vector_dimensions",
    "empty_vector_machine_paths",
}
TEXT_VECTOR_OUTLINE_FALLBACK_SPEECH = "字体矢量轮廓不可用，已自动改用图片轮廓方式生成。"


def _should_use_material_params(material, thickness_mm, use_material_params):
    return bool(use_material_params or str(material or "").strip() or thickness_mm)


def _is_cjk_char(char):
    return "\u4e00" <= char <= "\u9fff"


def _cjk_chars(text):
    return [char for char in str(text or "") if _is_cjk_char(char)]


def _split_balanced_lines(chars, chars_per_line):
    return ["".join(chars[index : index + chars_per_line]) for index in range(0, len(chars), chars_per_line)]


def _resolve_outline_auto_layout(
    text,
    engraving_mode,
    width_mm,
    height_mm,
    image_width,
    image_height,
    auto_layout,
    target_char_height_mm,
    max_layout_width_mm,
    max_layout_height_mm,
):
    if not auto_layout:
        return {"text": text, "enabled": False, "reason": "disabled"}
    if str(engraving_mode or "").strip().lower() not in ("outline", "line", "contour", "轮廓", "线雕"):
        return {"text": text, "enabled": False, "reason": "not_outline"}
    if width_mm or height_mm:
        return {"text": text, "enabled": False, "reason": "manual_size"}
    if "\n" in str(text or ""):
        return {"text": text, "enabled": False, "reason": "manual_multiline"}

    chars = _cjk_chars(text)
    if len(chars) < 4 or len(chars) != len(str(text or "")):
        return {"text": text, "enabled": False, "reason": "not_long_cjk"}

    if len(chars) <= 6:
        chars_per_line = 3
    elif len(chars) <= 9:
        chars_per_line = 3
    else:
        chars_per_line = 4
    lines = _split_balanced_lines(chars, chars_per_line)

    try:
        target_char_height_mm = float(target_char_height_mm or 0.0)
    except (TypeError, ValueError):
        target_char_height_mm = 0.0

    if target_char_height_mm <= 0:
        return {
            "text": "\n".join(lines),
            "enabled": True,
            "reason": "outline_long_cjk",
            "original_text": text,
            "lines": lines,
            "line_count": len(lines),
            "chars_per_line": chars_per_line,
            "physical_size_source": "dpi",
        }

    target_char_height_mm = max(8.0, target_char_height_mm)

    try:
        max_layout_width_mm = float(max_layout_width_mm or DEFAULT_OUTLINE_MAX_LAYOUT_WIDTH_MM)
    except (TypeError, ValueError):
        max_layout_width_mm = DEFAULT_OUTLINE_MAX_LAYOUT_WIDTH_MM
    try:
        max_layout_height_mm = float(max_layout_height_mm or DEFAULT_OUTLINE_MAX_LAYOUT_HEIGHT_MM)
    except (TypeError, ValueError):
        max_layout_height_mm = DEFAULT_OUTLINE_MAX_LAYOUT_HEIGHT_MM

    line_count = len(lines)
    max_line_chars = max(len(line) for line in lines)
    line_gap_mm = target_char_height_mm * 0.25
    target_width_mm = max_line_chars * target_char_height_mm * 1.15
    target_height_mm = line_count * target_char_height_mm + max(0, line_count - 1) * line_gap_mm
    scale = min(max_layout_width_mm / target_width_mm, max_layout_height_mm / target_height_mm, 1.0)
    target_width_mm *= scale
    target_height_mm *= scale
    target_char_height_mm *= scale

    return {
        "text": "\n".join(lines),
        "enabled": True,
        "reason": "outline_long_cjk",
        "original_text": text,
        "lines": lines,
        "line_count": line_count,
        "chars_per_line": chars_per_line,
        "target_char_height_mm": round(target_char_height_mm, 3),
        "physical_size_source": "target_char_height_mm",
        "width_mm": round(target_width_mm, 3),
        "height_mm": round(target_height_mm, 3),
        "image_width": image_width,
        "image_height": image_height,
    }


def _resolve_material_params(material, thickness_mm, laser_mode, engraving_mode, params_file, use_material_params):
    if not _should_use_material_params(material, thickness_mm, use_material_params):
        return None, None
    if not str(material or "").strip():
        return None, {
            "success": False,
            "stage": "recommend_laser_params",
            "result": "使用材料库生成 G-code 需要指定 material",
        }
    if not thickness_mm:
        return None, {
            "success": False,
            "stage": "recommend_laser_params",
            "result": "使用材料库生成 G-code 需要指定 thickness_mm",
        }

    recommendation = laser_material_calibration_tool.recommend_laser_params(
        material,
        thickness_mm,
        laser_mode,
        engraving_mode,
        params_file=params_file,
    )
    if not recommendation.get("success"):
        return None, {
            "success": False,
            "stage": "recommend_laser_params",
            "result": recommendation.get("result"),
            "detail": recommendation,
        }
    return recommendation["result"], None


def _should_use_text_vector_outline(laser_mode, engraving_mode):
    resolved_laser_mode, error = laser_grbl_tool._resolve_laser_mode(laser_mode)
    if error:
        return False
    if resolved_laser_mode == "cut":
        return True

    resolved_engraving_mode, error = laser_grbl_tool._resolve_engraving_mode(engraving_mode)
    return not error and resolved_laser_mode == "engrave" and resolved_engraving_mode == "outline"


def _resolve_text_vector_output_modes(laser_mode, engraving_mode):
    resolved_laser_mode, error = laser_grbl_tool._resolve_laser_mode(laser_mode)
    if error:
        return None, None, error
    if resolved_laser_mode == "cut":
        return "cut", "cut", None

    resolved_engraving_mode, error = laser_grbl_tool._resolve_engraving_mode(engraving_mode)
    if error:
        return resolved_laser_mode, None, error
    return resolved_laser_mode, resolved_engraving_mode, None


def _text_vector_failure(message, error_code="", **extra):
    failure = {"success": False, "result": message}
    if error_code:
        failure["error_code"] = error_code
    failure.update(extra)
    return failure


def _classify_text_vector_font_error(message):
    text = str(message or "")
    if "fontTools" in text:
        return "missing_fonttools"
    if "缺少 font_path" in text:
        return "missing_font_path"
    if "字体文件不存在" in text:
        return "missing_font_file"
    if "字体集合为空" in text:
        return "empty_font_collection"
    if "unitsPerEm" in text:
        return "invalid_font_units"
    if "读取字体矢量轮廓失败" in text:
        return "font_read_error"
    return ""


def _classify_text_vector_layout_error(message):
    text = str(message or "")
    if "缺少 font_path" in text:
        return "layout_font_missing"
    if "加载字体用于文字排版失败" in text:
        return "layout_font_load_error"
    if "排版元数据无效" in text:
        return "layout_metadata_invalid"
    if "排版尺寸无效" in text:
        return "layout_dimensions_invalid"
    return ""


def _should_fallback_text_vector_outline_failure(gcode_result):
    if not gcode_result or gcode_result.get("success"):
        return False
    error_code = gcode_result.get("error_code", "")
    if error_code in TEXT_VECTOR_OUTLINE_FALLBACK_CODES:
        return True
    return bool(gcode_result.get("missing_glyphs"))


def _annotate_text_vector_outline_fallback(gcode_result, vector_failure):
    payload = gcode_result.get("result") if isinstance(gcode_result, dict) else None
    if not isinstance(payload, dict):
        return

    payload["text_vector_outline"] = False
    payload["text_vector_outline_fallback"] = True
    payload["vector_backend"] = payload.get("vector_backend") or "image_outline"
    payload["fallback_from_vector_backend"] = "fontTools"
    payload["fallback_to_backend"] = "image_to_gcode_outline"
    payload["vector_outline_error"] = vector_failure.get("result", "")
    if vector_failure.get("error_code"):
        payload["vector_outline_error_code"] = vector_failure["error_code"]
    if vector_failure.get("missing_glyphs"):
        payload["missing_glyphs"] = vector_failure["missing_glyphs"]

    existing_speech = str(payload.get("speech") or "").strip()
    payload["speech"] = (
        f"{TEXT_VECTOR_OUTLINE_FALLBACK_SPEECH}{existing_speech}"
        if existing_speech
        else f"{TEXT_VECTOR_OUTLINE_FALLBACK_SPEECH}文件已准备好，请确认是否发送。"
    )


def _load_fonttools():
    try:
        from fontTools.pens.recordingPen import DecomposingRecordingPen
        from fontTools.ttLib import TTCollection, TTFont

        return TTFont, TTCollection, DecomposingRecordingPen, None
    except ImportError as exc:
        return (
            None,
            None,
            None,
            f"文字矢量轮廓需要 fontTools 依赖，请先执行: python -m pip install fontTools>=4.0.0。原始错误: {exc}",
        )


def _load_text_vector_font(font_path):
    TTFont, TTCollection, DecomposingRecordingPen, error = _load_fonttools()
    if error:
        return None, error

    if not font_path:
        return None, "文字矢量轮廓缺少 font_path，无法读取字体 glyph"

    normalized = os.path.abspath(os.path.expandvars(os.path.expanduser(font_path)))
    if not os.path.isfile(normalized):
        return None, f"字体文件不存在: {normalized}"

    try:
        extension = os.path.splitext(normalized)[1].lower()
        collection = None
        if extension in (".ttc", ".otc"):
            collection = TTCollection(normalized)
            if not collection.fonts:
                return None, f"字体集合为空: {normalized}"
            font = collection.fonts[0]
            font_index = 0
        else:
            font = TTFont(normalized)
            font_index = None

        cmap = font.getBestCmap() or {}
        glyph_set = font.getGlyphSet()
        units_per_em = float(font["head"].unitsPerEm)
        if units_per_em <= 0:
            return None, f"字体 unitsPerEm 无效: {normalized}"

        return {
            "font": font,
            "collection": collection,
            "font_index": font_index,
            "font_path": normalized,
            "cmap": cmap,
            "glyph_set": glyph_set,
            "units_per_em": units_per_em,
            "decomposing_pen": DecomposingRecordingPen,
        }, None
    except Exception as exc:
        return None, f"读取字体矢量轮廓失败: {exc}"


def _text_vector_lines(image_payload):
    lines = image_payload.get("lines")
    if lines:
        return [str(line) for line in lines if str(line)]

    text = str(image_payload.get("text") or "")
    return [line.strip() for line in text.splitlines() if line.strip()]


def _missing_text_vector_glyphs(lines, cmap):
    missing = []
    seen = set()
    for line in lines:
        for char in line:
            if char.isspace():
                continue
            if ord(char) in cmap:
                continue
            if char not in seen:
                missing.append(char)
                seen.add(char)
    return missing


def _resolve_text_vector_line_layout(image_payload, lines):
    image_module, image_draw_module, image_font_module, error = text_image_tool._load_pillow()
    if error:
        return None, error

    font_path = image_payload.get("font_path")
    if not font_path:
        return None, "文字矢量轮廓缺少 font_path，无法复用文字排版"

    try:
        image_width = int(image_payload.get("width") or text_image_tool.DEFAULT_IMAGE_WIDTH)
        image_height = int(image_payload.get("height") or text_image_tool.DEFAULT_IMAGE_HEIGHT)
        font_size = int(image_payload.get("font_size") or text_image_tool.DEFAULT_FONT_SIZE)
        line_spacing = float(image_payload.get("line_spacing", text_image_tool.DEFAULT_LINE_SPACING))
    except (TypeError, ValueError):
        return None, "文字矢量轮廓排版元数据无效"

    if image_width <= 0 or image_height <= 0 or font_size <= 0:
        return None, "文字矢量轮廓排版尺寸无效"

    try:
        font = image_font_module.truetype(font_path, font_size)
    except Exception as exc:
        return None, f"加载字体用于文字排版失败: {exc}"

    measure_image = image_module.new("L", (1, 1), 255)
    draw = image_draw_module.Draw(measure_image)
    line_spacing_px = int(round(font_size * line_spacing))
    boxes = [draw.textbbox((0, 0), line, font=font) for line in lines]
    line_heights = [box[3] - box[1] for box in boxes]
    total_text_height = sum(line_heights) + max(0, len(lines) - 1) * line_spacing_px
    ascent, _descent = font.getmetrics()

    current_y = (image_height - total_text_height) / 2
    layout_lines = []
    for line, box, line_height in zip(lines, boxes, line_heights):
        line_width = box[2] - box[0]
        text_x = (image_width - line_width) / 2 - box[0]
        text_y = current_y - box[1]
        layout_lines.append(
            {
                "line": line,
                "origin_x_px": float(text_x),
                "baseline_y_px": float(text_y + ascent),
                "bbox": tuple(float(value) for value in box),
                "line_height_px": float(line_height),
            }
        )
        current_y += line_height + line_spacing_px

    return {
        "image_width_px": image_width,
        "image_height_px": image_height,
        "font_path": os.path.abspath(font_path),
        "font_size_px": font_size,
        "line_spacing_px": line_spacing_px,
        "lines": layout_lines,
    }, None


def _distance(point_a, point_b):
    return math.hypot(point_a[0] - point_b[0], point_a[1] - point_b[1])


def _midpoint(point_a, point_b):
    return ((point_a[0] + point_b[0]) / 2, (point_a[1] + point_b[1]) / 2)


def _font_point_to_image(point, origin_x_px, baseline_y_px, scale_px):
    return origin_x_px + point[0] * scale_px, baseline_y_px - point[1] * scale_px


def _append_vector_point(contour, point, tolerance=TEXT_VECTOR_DUPLICATE_POINT_TOLERANCE_PX):
    point = (float(point[0]), float(point[1]))
    if contour and _distance(contour[-1], point) <= tolerance:
        return
    contour.append(point)


def _clean_vector_contour(points):
    cleaned = []
    for point in points:
        _append_vector_point(cleaned, point)

    if len(cleaned) > 1 and _distance(cleaned[0], cleaned[-1]) <= TEXT_VECTOR_DUPLICATE_POINT_TOLERANCE_PX:
        cleaned.pop()
    return cleaned if len(cleaned) >= 2 else []


def _curve_segment_count(points, scale_px):
    length_px = 0.0
    for previous, current in zip(points, points[1:]):
        length_px += _distance(previous, current) * scale_px
    return max(
        TEXT_VECTOR_MIN_CURVE_SEGMENTS,
        min(TEXT_VECTOR_MAX_CURVE_SEGMENTS, int(math.ceil(length_px / TEXT_VECTOR_CURVE_STEP_PX))),
    )


def _quadratic_point(start, control, end, t):
    one_minus_t = 1.0 - t
    return (
        one_minus_t * one_minus_t * start[0] + 2 * one_minus_t * t * control[0] + t * t * end[0],
        one_minus_t * one_minus_t * start[1] + 2 * one_minus_t * t * control[1] + t * t * end[1],
    )


def _cubic_point(start, control_1, control_2, end, t):
    one_minus_t = 1.0 - t
    return (
        one_minus_t**3 * start[0]
        + 3 * one_minus_t * one_minus_t * t * control_1[0]
        + 3 * one_minus_t * t * t * control_2[0]
        + t**3 * end[0],
        one_minus_t**3 * start[1]
        + 3 * one_minus_t * one_minus_t * t * control_1[1]
        + 3 * one_minus_t * t * t * control_2[1]
        + t**3 * end[1],
    )


def _append_quadratic_points(contour, start, control, end, origin_x_px, baseline_y_px, scale_px):
    segments = _curve_segment_count([start, control, end], scale_px)
    for index in range(1, segments + 1):
        point = _quadratic_point(start, control, end, index / segments)
        _append_vector_point(contour, _font_point_to_image(point, origin_x_px, baseline_y_px, scale_px))


def _append_cubic_points(contour, start, control_1, control_2, end, origin_x_px, baseline_y_px, scale_px):
    segments = _curve_segment_count([start, control_1, control_2, end], scale_px)
    for index in range(1, segments + 1):
        point = _cubic_point(start, control_1, control_2, end, index / segments)
        _append_vector_point(contour, _font_point_to_image(point, origin_x_px, baseline_y_px, scale_px))


def _finish_vector_contour(contours, contour):
    cleaned = _clean_vector_contour(contour)
    if cleaned:
        contours.append(cleaned)


def _recording_to_image_contours(recording, origin_x_px, baseline_y_px, scale_px):
    contours = []
    contour = []
    current_font_point = None
    start_font_point = None

    for operator, args in recording:
        if operator == "moveTo":
            if contour:
                _finish_vector_contour(contours, contour)
            current_font_point = tuple(args[0])
            start_font_point = current_font_point
            contour = [_font_point_to_image(current_font_point, origin_x_px, baseline_y_px, scale_px)]
            continue

        if operator == "lineTo":
            if current_font_point is None:
                continue
            current_font_point = tuple(args[0])
            _append_vector_point(
                contour,
                _font_point_to_image(current_font_point, origin_x_px, baseline_y_px, scale_px),
            )
            continue

        if operator == "qCurveTo":
            if current_font_point is None:
                continue
            points = list(args)
            if not points:
                continue
            if points[-1] is None:
                end_point = start_font_point
                controls = [tuple(point) for point in points[:-1]]
            else:
                end_point = tuple(points[-1])
                controls = [tuple(point) for point in points[:-1]]
            if end_point is None:
                continue
            if not controls:
                current_font_point = end_point
                _append_vector_point(
                    contour,
                    _font_point_to_image(current_font_point, origin_x_px, baseline_y_px, scale_px),
                )
                continue
            for index, control in enumerate(controls):
                segment_end = end_point if index == len(controls) - 1 else _midpoint(control, controls[index + 1])
                _append_quadratic_points(
                    contour,
                    current_font_point,
                    control,
                    segment_end,
                    origin_x_px,
                    baseline_y_px,
                    scale_px,
                )
                current_font_point = segment_end
            continue

        if operator == "curveTo":
            if current_font_point is None:
                continue
            points = [tuple(point) for point in args]
            for index in range(0, len(points), 3):
                segment = points[index : index + 3]
                if len(segment) < 3:
                    current_font_point = segment[-1]
                    _append_vector_point(
                        contour,
                        _font_point_to_image(current_font_point, origin_x_px, baseline_y_px, scale_px),
                    )
                    continue
                control_1, control_2, end_point = segment
                _append_cubic_points(
                    contour,
                    current_font_point,
                    control_1,
                    control_2,
                    end_point,
                    origin_x_px,
                    baseline_y_px,
                    scale_px,
                )
                current_font_point = end_point
            continue

        if operator in ("closePath", "endPath"):
            if contour:
                _finish_vector_contour(contours, contour)
            contour = []
            current_font_point = None
            start_font_point = None

    if contour:
        _finish_vector_contour(contours, contour)
    return contours


def _glyph_advance_units(vector_font, glyph_name, char):
    if glyph_name:
        metrics = vector_font["font"]["hmtx"].metrics
        if glyph_name in metrics:
            return float(metrics[glyph_name][0])
    if char.isspace():
        return vector_font["units_per_em"] * 0.33
    return vector_font["units_per_em"]


def _build_text_vector_contours(vector_font, layout):
    scale_px = layout["font_size_px"] / vector_font["units_per_em"]
    contours = []
    for line in layout["lines"]:
        pen_x = line["origin_x_px"]
        baseline_y = line["baseline_y_px"]
        for char in line["line"]:
            glyph_name = vector_font["cmap"].get(ord(char))
            if glyph_name and not char.isspace():
                try:
                    pen = vector_font["decomposing_pen"](vector_font["glyph_set"])
                    vector_font["glyph_set"][glyph_name].draw(pen)
                    contours.extend(_recording_to_image_contours(pen.value, pen_x, baseline_y, scale_px))
                except Exception as exc:
                    return None, f"读取字符 {char!r} 的 glyph 轮廓失败: {exc}"
            pen_x += _glyph_advance_units(vector_font, glyph_name, char) * scale_px
    return contours, None


def _vector_bbox(contours):
    xs = [point[0] for contour in contours for point in contour]
    ys = [point[1] for contour in contours for point in contour]
    return min(xs), min(ys), max(xs), max(ys)


def _vector_auto_trim_info(image_width, image_height, bbox, auto_trim):
    if not auto_trim:
        return {
            "enabled": False,
            "trimmed": False,
            "original_width_px": int(image_width),
            "original_height_px": int(image_height),
            "trimmed_width_px": int(image_width),
            "trimmed_height_px": int(image_height),
            "crop_box": {"x_px": 0, "y_px": 0, "width_px": int(image_width), "height_px": int(image_height)},
        }

    min_x, min_y, max_x, max_y = bbox
    trim_width = max_x - min_x
    trim_height = max_y - min_y
    return {
        "enabled": True,
        "trimmed": True,
        "original_width_px": int(image_width),
        "original_height_px": int(image_height),
        "trimmed_width_px": int(math.ceil(trim_width)),
        "trimmed_height_px": int(math.ceil(trim_height)),
        "crop_box": {
            "x_px": int(math.floor(min_x)),
            "y_px": int(math.floor(min_y)),
            "width_px": int(math.ceil(trim_width)),
            "height_px": int(math.ceil(trim_height)),
        },
    }


def _rotate_vector_points_near_current(points, current_point):
    if current_point is None or len(points) <= 1:
        return points

    nearest_index = min(
        range(len(points)),
        key=lambda index: _distance(points[index], current_point),
    )
    if nearest_index == 0:
        return points
    return list(points[nearest_index:]) + list(points[:nearest_index])


def _order_vector_contours_by_nearest(contours):
    remaining = list(contours)
    ordered = []
    current_point = (0.0, 0.0)
    while remaining:
        next_index = min(
            range(len(remaining)),
            key=lambda index: min(_distance(point, current_point) for point in remaining[index]),
        )
        points = _rotate_vector_points_near_current(remaining.pop(next_index), current_point)
        ordered.append(points)
        current_point = points[0]
    return ordered


def _image_contour_to_machine_points(contour, source_min_x, source_max_y, scale_x, scale_y, offset_x, offset_y):
    machine_points = []
    for x_px, y_px in contour:
        point = (
            offset_x + (x_px - source_min_x) * scale_x,
            offset_y + (source_max_y - y_px) * scale_y,
        )
        if machine_points and _distance(machine_points[-1], point) <= 0.001:
            continue
        machine_points.append(point)
    if len(machine_points) > 1 and _distance(machine_points[0], machine_points[-1]) <= 0.001:
        machine_points.pop()
    return machine_points if len(machine_points) >= 2 else []


def _create_text_vector_outline_gcode(
    image_payload,
    image_file,
    output_file,
    width_mm,
    height_mm,
    feed_rate,
    travel_rate,
    laser_max_power,
    overwrite,
    auto_trim,
    auto_size,
    dpi,
    lock_aspect_ratio,
    offset_x_mm,
    offset_y_mm,
    safe_margin_mm,
    laser_mode="engrave",
    engraving_mode="outline",
):
    feed_rate, error = laser_grbl_tool._validate_int(feed_rate, "feed_rate", 1)
    if error:
        return {"success": False, "result": error}
    travel_rate, error = laser_grbl_tool._validate_int(travel_rate, "travel_rate", 1)
    if error:
        return {"success": False, "result": error}
    laser_max_power, error = laser_grbl_tool._validate_int(
        laser_max_power,
        "laser_max_power",
        0,
        laser_grbl_tool.DEFAULT_LASER_S_MAX,
    )
    if error:
        return {"success": False, "result": error}

    resolved_laser_mode, resolved_engraving_mode, error = _resolve_text_vector_output_modes(laser_mode, engraving_mode)
    if error:
        return {"success": False, "result": error}

    output_file, error = laser_grbl_tool._resolve_output_gcode_file(output_file, image_file)
    if error:
        return {"success": False, "result": error}
    if os.path.exists(output_file) and not overwrite:
        return {"success": False, "result": f"输出文件已存在: {output_file}"}

    vector_font, error = _load_text_vector_font(image_payload.get("font_path"))
    if error:
        return _text_vector_failure(error, _classify_text_vector_font_error(error))

    lines = _text_vector_lines(image_payload)
    if not lines:
        return _text_vector_failure("文字矢量轮廓没有可输出的文本行")

    missing_glyphs = _missing_text_vector_glyphs(lines, vector_font["cmap"])
    if missing_glyphs:
        missing_text = "".join(missing_glyphs)
        return {
            "success": False,
            "result": f"当前字体不包含这些字符的矢量 glyph: {missing_text}；请换一个支持这些字符的 font_path",
            "error_code": "missing_glyphs",
            "missing_glyphs": missing_glyphs,
            "font_path": vector_font["font_path"],
        }

    layout, error = _resolve_text_vector_line_layout(image_payload, lines)
    if error:
        return _text_vector_failure(error, _classify_text_vector_layout_error(error))

    contours, error = _build_text_vector_contours(vector_font, layout)
    if error:
        return _text_vector_failure(error, "glyph_read_error")
    if not contours:
        return _text_vector_failure("未从字体 glyph 中提取到可雕刻轮廓", "empty_glyph_contours")

    bbox = _vector_bbox(contours)
    image_width = layout["image_width_px"]
    image_height = layout["image_height_px"]
    if auto_trim:
        source_min_x, source_min_y, source_max_x, source_max_y = bbox
    else:
        source_min_x, source_min_y = 0.0, 0.0
        source_max_x, source_max_y = float(image_width), float(image_height)

    source_width = source_max_x - source_min_x
    source_height = source_max_y - source_min_y
    if source_width <= 0 or source_height <= 0:
        return _text_vector_failure("文字矢量轮廓尺寸无效", "invalid_vector_dimensions")

    placement, error = laser_grbl_tool._resolve_image_placement(
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
        return {"success": False, "result": error}

    actual_width_mm = placement["final_width_mm"]
    actual_height_mm = placement["final_height_mm"]
    resolved_offset_x = placement["offset_x_mm"]
    resolved_offset_y = placement["offset_y_mm"]
    placement_error = laser_grbl_tool._validate_placement_safe_area(
        placement,
        actual_width_mm,
        actual_height_mm,
    )
    if placement_error:
        return {"success": False, "result": placement_error, "placement": placement}

    scale_x = actual_width_mm / source_width
    scale_y = actual_height_mm / source_height
    machine_contours = []
    for contour in contours:
        points = _image_contour_to_machine_points(
            contour,
            source_min_x,
            source_max_y,
            scale_x,
            scale_y,
            resolved_offset_x,
            resolved_offset_y,
        )
        if points:
            machine_contours.append(points)
    if not machine_contours:
        return _text_vector_failure("文字矢量轮廓转换后没有可雕刻路径", "empty_vector_machine_paths")

    job_label = "cut mode" if resolved_laser_mode == "cut" else "outline engraving mode"
    gcode = [
        f"; Generated by text_image_gcode_tool text vector outline {job_label}",
        "; Strategy: font glyph outlines via fontTools, no raster contour tracing",
        "; Firmware: GRBL laser mode recommended ($32=1)",
        "G21",
        "G90",
        "G94",
        laser_grbl_tool.ZERO_ORIGIN_COMMAND,
        "M4 S0",
        f"G0 F{travel_rate}",
        f"G1 F{feed_rate}",
    ]

    ordered_contours = _order_vector_contours_by_nearest(machine_contours)
    for points in ordered_contours:
        first_x, first_y = points[0]
        gcode.append(f"G0 X{laser_grbl_tool._format_mm(first_x)} Y{laser_grbl_tool._format_mm(first_y)}")
        gcode.append(f"M4 S{laser_max_power}")
        for x_mm, y_mm in points[1:]:
            gcode.append(f"G1 X{laser_grbl_tool._format_mm(x_mm)} Y{laser_grbl_tool._format_mm(y_mm)}")
        gcode.append(f"G1 X{laser_grbl_tool._format_mm(first_x)} Y{laser_grbl_tool._format_mm(first_y)}")
        gcode.append("M5")

    try:
        with open(output_file, "w", encoding="utf-8", newline="\n") as file:
            file.write("\n".join(gcode))
            file.write("\n")
    except OSError as exc:
        return {"success": False, "result": f"写入 G-code 文件失败: {exc}"}

    auto_trim_info = _vector_auto_trim_info(image_width, image_height, bbox, auto_trim)
    stats = {
        "image_file": image_file,
        "gcode_file": output_file,
        "laser_mode": resolved_laser_mode,
        "engraving_mode": resolved_engraving_mode,
        "mode": resolved_engraving_mode,
        "text_vector_outline": True,
        "vector_backend": "fontTools",
        "font_path": vector_font["font_path"],
        "font_index": vector_font["font_index"],
        "font_size_px": layout["font_size_px"],
        "image_width_px": round(source_width, 3),
        "image_height_px": round(source_height, 3),
        "actual_width_mm": round(actual_width_mm, 3),
        "actual_height_mm": round(actual_height_mm, 3),
        "offset_x_mm": round(resolved_offset_x, 3),
        "offset_y_mm": round(resolved_offset_y, 3),
        "contour_count": len(ordered_contours),
        "outline_point_count": sum(len(points) for points in ordered_contours),
        "curve_step_px": TEXT_VECTOR_CURVE_STEP_PX,
        "auto_trim": auto_trim_info,
        "placement": laser_grbl_tool._finalize_placement_with_actual_size(
            placement,
            {"actual_width_mm": round(actual_width_mm, 3), "actual_height_mm": round(actual_height_mm, 3)},
        ),
        "vector_bbox_px": {
            "min_x": round(bbox[0], 3),
            "min_y": round(bbox[1], 3),
            "max_x": round(bbox[2], 3),
            "max_y": round(bbox[3], 3),
        },
        "y_axis_flipped": True,
        "line_count": len(gcode),
        "machine_profile": laser_grbl_tool._machine_profile(),
    }
    laser_time_estimate.add_time_estimate_fields(stats, output_file)
    return {"success": True, "result": stats}


def _convert_text_image_to_gcode(
    image_file,
    output_file,
    width_mm,
    height_mm,
    pixel_size_mm,
    feed_rate,
    travel_rate,
    laser_min_power,
    laser_max_power,
    threshold,
    invert,
    bidirectional,
    overscan_mm,
    raster_scan_direction,
    overwrite,
    laser_mode,
    engraving_mode,
    auto_trim,
    trim_tolerance,
    auto_size,
    dpi,
    lock_aspect_ratio,
    offset_x_mm,
    offset_y_mm,
    safe_margin_mm,
):
    return laser_grbl_tool.convert_image_to_gcode(
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


def create_text_image_gcode(
    text,
    image_output_file="",
    gcode_output_file="",
    image_width=text_image_tool.DEFAULT_IMAGE_WIDTH,
    image_height=text_image_tool.DEFAULT_IMAGE_HEIGHT,
    font_size=text_image_tool.DEFAULT_FONT_SIZE,
    font_path="",
    auto_wrap=text_image_tool.DEFAULT_AUTO_WRAP,
    max_lines=text_image_tool.DEFAULT_MAX_LINES,
    layout_mode=text_image_tool.DEFAULT_LAYOUT_MODE,
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
    overscan_mm=laser_grbl_tool.DEFAULT_RASTER_OVERSCAN_MM,
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
    safe_margin_mm=laser_grbl_tool.DEFAULT_SAFE_MARGIN_MM,
    material="",
    thickness_mm=0.0,
    use_material_params=False,
    params_file=laser_material_calibration_tool.MATERIAL_PARAMS_FILE,
    auto_layout=True,
    target_char_height_mm=0.0,
    max_layout_width_mm=DEFAULT_OUTLINE_MAX_LAYOUT_WIDTH_MM,
    max_layout_height_mm=DEFAULT_OUTLINE_MAX_LAYOUT_HEIGHT_MM,
):
    material_recommendation, error_result = _resolve_material_params(
        material,
        thickness_mm,
        laser_mode,
        engraving_mode,
        params_file,
        use_material_params,
    )
    if error_result:
        return error_result
    if material_recommendation:
        params = material_recommendation["params"]
        pixel_size_mm = params["pixel_size_mm"]
        feed_rate = params["feed_rate"]
        travel_rate = params["travel_rate"]
        laser_min_power = params["laser_min_power"]
        laser_max_power = params["laser_max_power"]
        threshold = params["threshold"]

    layout = _resolve_outline_auto_layout(
        text,
        engraving_mode,
        width_mm,
        height_mm,
        image_width,
        image_height,
        auto_layout,
        target_char_height_mm,
        max_layout_width_mm,
        max_layout_height_mm,
    )
    text_for_image = layout["text"]
    if layout.get("enabled") and layout.get("physical_size_source") == "target_char_height_mm":
        width_mm = layout["width_mm"]
        height_mm = layout["height_mm"]

    image_result = text_image_tool.create_text_image(
        text=text_for_image,
        output_file=image_output_file,
        width=image_width,
        height=image_height,
        font_size=font_size,
        font_path=font_path,
        auto_wrap=auto_wrap,
        max_lines=max_lines,
        layout_mode=layout_mode,
    )
    if not image_result.get("success"):
        return {
            "success": False,
            "stage": "generate_text_image",
            "result": image_result.get("result"),
            "detail": image_result,
        }

    image_file = image_result["result"]["output_file"]
    vector_outline_failure = None
    if _should_use_text_vector_outline(laser_mode, engraving_mode):
        gcode_stage = "text_vector_outline"
        gcode_result = _create_text_vector_outline_gcode(
            image_payload=image_result["result"],
            image_file=image_file,
            output_file=gcode_output_file,
            width_mm=width_mm,
            height_mm=height_mm,
            feed_rate=feed_rate,
            travel_rate=travel_rate,
            laser_max_power=laser_max_power,
            overwrite=overwrite,
            auto_trim=auto_trim,
            auto_size=auto_size,
            dpi=dpi,
            lock_aspect_ratio=lock_aspect_ratio,
            offset_x_mm=offset_x_mm,
            offset_y_mm=offset_y_mm,
            safe_margin_mm=safe_margin_mm,
            laser_mode=laser_mode,
            engraving_mode=engraving_mode,
        )
        if not gcode_result.get("success") and _should_fallback_text_vector_outline_failure(gcode_result):
            vector_outline_failure = gcode_result
            gcode_stage = "image_to_gcode"
            gcode_result = _convert_text_image_to_gcode(
                image_file=image_file,
                output_file=gcode_output_file,
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
            if gcode_result.get("success"):
                _annotate_text_vector_outline_fallback(gcode_result, vector_outline_failure)
    else:
        gcode_stage = "image_to_gcode"
        gcode_result = _convert_text_image_to_gcode(
            image_file=image_file,
            output_file=gcode_output_file,
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
    if not gcode_result.get("success"):
        failure = {
            "success": False,
            "stage": gcode_stage,
            "result": gcode_result.get("result"),
            "image": image_result["result"],
            "detail": gcode_result,
        }
        if vector_outline_failure:
            failure["vector_outline_detail"] = vector_outline_failure
        return failure

    gcode_payload = gcode_result["result"]
    result = {
        "text": image_result["result"]["text"],
        "image_file": image_file,
        "gcode_file": gcode_payload["gcode_file"],
        "image": image_result["result"],
        "gcode": gcode_payload,
    }
    if image_result["result"].get("layout"):
        result["text_layout"] = image_result["result"]["layout"]
    if gcode_payload.get("time_estimate"):
        result["time_estimate"] = gcode_payload["time_estimate"]
    if gcode_payload.get("speech"):
        result["speech"] = gcode_payload["speech"]
    if material_recommendation:
        result["material_recommendation"] = material_recommendation
    if layout.get("enabled"):
        result["layout"] = layout
    laser_time_estimate.add_time_estimate_fields(result, result["gcode_file"])

    return {"success": True, "result": result}


def register_tool(mcp):
    @mcp.tool()
    def generate_text_image_gcode_tool(
        text: str,
        image_output_file: str = "",
        gcode_output_file: str = "",
        image_width: int = text_image_tool.DEFAULT_IMAGE_WIDTH,
        image_height: int = text_image_tool.DEFAULT_IMAGE_HEIGHT,
        font_size: int = text_image_tool.DEFAULT_FONT_SIZE,
        font_path: str = "",
        auto_wrap: bool = text_image_tool.DEFAULT_AUTO_WRAP,
        max_lines: int = text_image_tool.DEFAULT_MAX_LINES,
        layout_mode: str = text_image_tool.DEFAULT_LAYOUT_MODE,
        width_mm: float = 0.0,
        height_mm: float = 0.0,
        pixel_size_mm: float = 0.1,
        feed_rate: int = 1200,
        travel_rate: int = 3000,
        laser_min_power: int = 0,
        laser_max_power: int = 800,
        threshold: int = -1,
        invert: bool = False,
        bidirectional: bool = False,
        overscan_mm: float = laser_grbl_tool.DEFAULT_RASTER_OVERSCAN_MM,
        raster_scan_direction: str = "auto",
        overwrite: bool = True,
        laser_mode: str = "engrave",
        engraving_mode: str = "",
        auto_trim: bool = True,
        trim_tolerance: float = 20,
        auto_size: bool = True,
        dpi: float = 300.0,
        lock_aspect_ratio: bool = True,
        offset_x_mm: float = 0.0,
        offset_y_mm: float = 0.0,
        safe_margin_mm: float = laser_grbl_tool.DEFAULT_SAFE_MARGIN_MM,
        material: str = "",
        thickness_mm: float = 0.0,
        use_material_params: bool = False,
        params_file: str = laser_material_calibration_tool.MATERIAL_PARAMS_FILE,
        auto_layout: bool = True,
        target_char_height_mm: float = 0.0,
        max_layout_width_mm: float = DEFAULT_OUTLINE_MAX_LAYOUT_WIDTH_MM,
        max_layout_height_mm: float = DEFAULT_OUTLINE_MAX_LAYOUT_HEIGHT_MM,
    ) -> dict:
        """
        一步生成文字图片并转换为 G-code。
        适合用户说“把某个文字直接做成图片和 G-code”时使用。
        用户说“帮我刻 xxx”且有材料/厚度时，优先用 generate_text_laser_task_tool 生成带材料参数和反馈历史的任务。
        该工具只生成图片和 G-code 文件，不连接串口、不开始雕刻。

        参数:
            text: 要写入图片的文字，例如“佳佳”
            image_output_file: 可选图片输出路径；为空时使用 generated_images/text_image.png
            gcode_output_file: 可选 G-code 输出路径；为空时与图片同名并改为 .gcode
            image_width/image_height/font_size/font_path: 文字图片生成参数；font_size 单位是 px
            auto_wrap/max_lines/layout_mode: 文字排版参数；可用于长文本自动换行或指定行数
            width_mm/height_mm/pixel_size_mm/feed_rate/travel_rate: G-code 尺寸和速度参数；默认按裁边后图片 DPI 换算自然尺寸，超过安全区才等比例缩小
            auto_trim/trim_tolerance/auto_size/dpi/lock_aspect_ratio/offset_x_mm/offset_y_mm/safe_margin_mm: 图片导入裁边、DPI 尺寸、安全限幅和摆放偏移参数
            laser_mode: 'engrave'/'雕刻' 为雕刻，'cut'/'切割' 为激光切割轮廓
            engraving_mode: 雕刻策略；默认 raster/线扫填充，用户明确要求 outline/轮廓/线雕时传 'outline'
            material/thickness_mm/use_material_params/params_file: 提供材料和厚度，或 use_material_params=true 时，先从材料库推荐功率、速度、像素步距、阈值等参数
            auto_layout/target_char_height_mm/max_layout_width_mm/max_layout_height_mm: outline 长中文自动多行排版；默认只换行并按字号 px + DPI 换算物理尺寸，明确传入 target_char_height_mm>0 才按目标字高缩放
            laser_min_power/laser_max_power/threshold/invert/bidirectional/overscan_mm/raster_scan_direction: 图像转 G-code 参数；raster 时 threshold=-1 使用灰度功率映射，并按 scanline 连续行扫输出；outline 时 threshold=-1 使用默认轮廓阈值
            overwrite: 是否覆盖已有 G-code 文件
        """
        return create_text_image_gcode(
            text=text,
            image_output_file=image_output_file,
            gcode_output_file=gcode_output_file,
            image_width=image_width,
            image_height=image_height,
            font_size=font_size,
            font_path=font_path,
            auto_wrap=auto_wrap,
            max_lines=max_lines,
            layout_mode=layout_mode,
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
            material=material,
            thickness_mm=thickness_mm,
            use_material_params=use_material_params,
            params_file=params_file,
            auto_layout=auto_layout,
            target_char_height_mm=target_char_height_mm,
            max_layout_width_mm=max_layout_width_mm,
            max_layout_height_mm=max_layout_height_mm,
        )
