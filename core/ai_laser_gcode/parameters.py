import re

from core.ai_laser_gcode.models import (
    DitherAlgorithm,
    FillStrategy,
    JobMode,
    JobParams,
    MaterialMatchPolicy,
    RasterOutputStrategy,
    RasterScanDirection,
    SendPolicy,
    TaskType,
    TraceAlgorithm,
)


MATERIAL_DEFAULTS = {
    "wood": {"power": 280, "feed_rate": 900},
    "paper": {"power": 80, "feed_rate": 1500},
    "cowhide_leather": {"power": 300, "feed_rate": 800},
    "test": {"power": 100, "feed_rate": 1200},
}

RASTER_DEFAULTS = {"power": 120, "feed_rate": 1500, "pixel_size_mm": 0.2, "overscan_mm": 2.0}
POWER_PATTERN = r"(?<![A-Za-z])S\s*[:=]?\s*(\d+)"
FEED_RATE_PATTERN = r"(?<![A-Za-z])F\s*[:=]?\s*(\d+)"
PIXEL_SIZE_PATTERN = r"(?:pixel|点距|像素间距)\s*(\d+(?:\.\d+)?)\s*mm"
MAX_SIZE_MM = 120.0

MATERIAL_ALIASES = {
    "wood": "wood",
    "木板": "wood",
    "木头": "wood",
    "椴木": "wood",
    "木料": "wood",
    "木牌": "wood",
    "木片": "wood",
    "木质": "wood",
    "plywood": "wood",
    "basswood": "wood",
    "balsa": "wood",
    "paper": "paper",
    "纸张": "paper",
    "纸": "paper",
    "cowhide": "cowhide_leather",
    "leather": "cowhide_leather",
    "cowhide_leather": "cowhide_leather",
    "牛皮": "cowhide_leather",
    "皮料": "cowhide_leather",
    "皮革": "cowhide_leather",
    "test": "test",
    "通用": "test",
    "测试": "test",
}


def parse_job_params(
    prompt: str,
    output_format: str | None = None,
    size_mm: float | None = None,
    width_mm: float | None = None,
    height_mm: float | None = None,
    lock_aspect_ratio: bool | None = None,
    mode: JobMode | None = None,
    trace_algorithm: TraceAlgorithm | None = None,
    vector_simplify_factor: float | None = None,
    thickness_mm: float | None = None,
    task_type: TaskType | None = None,
    arc_output: bool | None = None,
    firmware_supports_arc: bool | None = None,
    arc_tolerance_mm: float | None = None,
    pixel_size_mm: float | None = None,
    dither_algorithm: DitherAlgorithm | None = None,
    threshold: int | None = None,
    raster_scan_direction: RasterScanDirection | None = None,
    raster_output_strategy: RasterOutputStrategy | None = None,
    fill_strategy: FillStrategy | None = None,
    fill_spacing_mm: float | None = None,
    material_match_policy: MaterialMatchPolicy | None = None,
    send_policy: SendPolicy | None = None,
    material: str | None = None,
) -> JobParams:
    normalized = prompt.strip()
    # Structured material is authoritative for matching; known aliases only remap
    # known tokens. Unknown explicit materials keep their identity (never become test).
    resolved_material = _resolve_material(normalized, material)
    parsed_mode = mode if mode is not None else _parse_mode(normalized)
    defaults = _material_defaults(resolved_material, parsed_mode)
    resolved_width_mm, resolved_height_mm = _resolve_dimensions(width_mm, height_mm)
    resolved_size_mm = _resolve_size_mm(normalized, size_mm)
    if resolved_width_mm is not None or resolved_height_mm is not None:
        resolved_size_mm = max(value for value in (resolved_width_mm, resolved_height_mm) if value is not None)
    manual_power = re.search(POWER_PATTERN, normalized, re.IGNORECASE) is not None
    manual_feed_rate = re.search(FEED_RATE_PATTERN, normalized, re.IGNORECASE) is not None
    structured_pixel_size = _positive_float_or_none(pixel_size_mm)
    manual_pixel_size = structured_pixel_size is not None or re.search(PIXEL_SIZE_PATTERN, normalized, re.IGNORECASE) is not None
    power = int(_parse_number(normalized, POWER_PATTERN, defaults["power"], re.IGNORECASE))
    feed_rate = int(_parse_number(normalized, FEED_RATE_PATTERN, defaults["feed_rate"], re.IGNORECASE))
    resolved_pixel_size_mm = structured_pixel_size or _parse_number(normalized, PIXEL_SIZE_PATTERN, RASTER_DEFAULTS["pixel_size_mm"], re.IGNORECASE)
    overscan_mm = _parse_number(normalized, r"(?:overscan|过冲|边缘缓冲)\s*(\d+(?:\.\d+)?)\s*mm", RASTER_DEFAULTS["overscan_mm"], re.IGNORECASE)
    parsed_format = "nc" if re.search(r"\bnc\b|\.nc", normalized, re.IGNORECASE) else "gcode"
    if output_format is not None:
        parsed_format = output_format
    parsed_trace_algorithm = trace_algorithm if trace_algorithm is not None else _parse_trace_algorithm(normalized)
    parsed_vector_simplify_factor = vector_simplify_factor if vector_simplify_factor is not None else _parse_number(normalized, r"(?:vector[_ -]?simplify[_ -]?factor|矢量简化强度)\s*[:=]?\s*(\d+(?:\.\d+)?)", 1.0, re.IGNORECASE)
    parsed_task_type = _resolve_task_type(task_type, parsed_mode)
    parsed_thickness = _positive_float_or_none(thickness_mm) if thickness_mm is not None else _parse_thickness(normalized)
    parsed_arc_output = arc_output if arc_output is not None else _parse_arc_output(normalized)
    parsed_firmware_supports_arc = firmware_supports_arc if firmware_supports_arc is not None else _parse_firmware_supports_arc(normalized)
    parsed_arc_tolerance = arc_tolerance_mm if arc_tolerance_mm is not None else _parse_number(normalized, r"(?:arc[_ -]?tolerance|圆弧容差)\s*[:=]?\s*(\d+(?:\.\d+)?)\s*mm", 0.05, re.IGNORECASE)
    parsed_threshold, manual_threshold = _resolve_threshold(threshold, normalized)
    parsed_dither_algorithm, manual_dither = _resolve_dither_algorithm(dither_algorithm, normalized)
    parsed_dither_algorithm = _apply_threshold_dither_rules(
        parsed_threshold,
        manual_threshold,
        parsed_dither_algorithm,
        manual_dither,
        structured_dither=dither_algorithm,
    )
    parsed_raster_scan_direction = raster_scan_direction if raster_scan_direction is not None else _parse_raster_scan_direction(normalized)
    parsed_raster_output_strategy = raster_output_strategy if raster_output_strategy is not None else _parse_raster_output_strategy(normalized)
    parsed_fill_strategy = fill_strategy if fill_strategy is not None else _parse_fill_strategy(normalized)
    parsed_fill_spacing = fill_spacing_mm if fill_spacing_mm is not None else _parse_number(normalized, r"(?:fill[_ -]?spacing|填充间距)\s*[:=]?\s*(\d+(?:\.\d+)?)\s*mm", 0.5, re.IGNORECASE)
    return JobParams(
        material=resolved_material,
        size_mm=resolved_size_mm,
        power=power,
        feed_rate=feed_rate,
        width_mm=resolved_width_mm,
        height_mm=resolved_height_mm,
        lock_aspect_ratio=True if lock_aspect_ratio is None else bool(lock_aspect_ratio),
        output_format=parsed_format,
        mode=parsed_mode,
        pixel_size_mm=resolved_pixel_size_mm,
        overscan_mm=overscan_mm,
        trace_algorithm=parsed_trace_algorithm,
        vector_simplify_factor=parsed_vector_simplify_factor,
        thickness_mm=parsed_thickness,
        thickness_source="explicit" if parsed_thickness is not None else "missing",
        task_type=parsed_task_type,
        task_type_source="explicit" if task_type is not None else ("inferred" if parsed_task_type is not None else "missing"),
        manual_power=manual_power,
        manual_feed_rate=manual_feed_rate,
        manual_pixel_size=manual_pixel_size,
        arc_output=parsed_arc_output,
        firmware_supports_arc=parsed_firmware_supports_arc,
        arc_tolerance_mm=parsed_arc_tolerance,
        dither_algorithm=parsed_dither_algorithm,
        threshold=parsed_threshold,
        manual_threshold=manual_threshold,
        manual_dither=manual_dither,
        raster_scan_direction=parsed_raster_scan_direction,
        raster_output_strategy=parsed_raster_output_strategy,
        fill_strategy=parsed_fill_strategy,
        fill_spacing_mm=parsed_fill_spacing,
        material_match_policy=material_match_policy or "exact_only",
        send_policy=send_policy or "verified_only",
    )


def _resolve_size_mm(prompt: str, explicit_size_mm: float | None = None) -> float:
    if explicit_size_mm not in (None, ""):
        value = float(explicit_size_mm)
        if value > 0:
            return min(value, MAX_SIZE_MM)

    explicit = re.search(
        r"(?:size|尺寸|大小|宽度|宽|width|最大边|边长)\s*[:=]?\s*(\d+(?:\.\d+)?)\s*mm",
        prompt,
        re.IGNORECASE,
    )
    if explicit:
        return min(float(explicit.group(1)), MAX_SIZE_MM)

    excluded_prefix = re.compile(
        r"(?:thickness|厚度|pixel|点距|像素间距|overscan|过冲|边缘缓冲|fill[_ -]?spacing|填充间距|arc[_ -]?tolerance|圆弧容差)\s*[:=]?\s*$",
        re.IGNORECASE,
    )
    for match in re.finditer(r"(\d+(?:\.\d+)?)\s*mm", prompt, re.IGNORECASE):
        prefix = prompt[max(0, match.start() - 32) : match.start()]
        if excluded_prefix.search(prefix):
            continue
        return min(float(match.group(1)), MAX_SIZE_MM)
    return 50


def _resolve_dimensions(width_mm: float | None = None, height_mm: float | None = None) -> tuple[float | None, float | None]:
    width = _positive_float_or_none(width_mm)
    height = _positive_float_or_none(height_mm)
    max_dimension = max((value for value in (width, height) if value is not None), default=0.0)
    if max_dimension > MAX_SIZE_MM:
        scale = MAX_SIZE_MM / max_dimension
        width = width * scale if width is not None else None
        height = height * scale if height is not None else None
    return width, height


def _positive_float_or_none(value):
    if value in (None, ""):
        return None
    number = float(value)
    return number if number > 0 else None


def _material_defaults(material: str, mode: JobMode) -> dict[str, int]:
    if mode == "raster":
        return dict(RASTER_DEFAULTS)
    known = MATERIAL_DEFAULTS.get(material)
    if known is not None:
        return dict(known)
    # Unknown materials only need placeholder S/F before library match; do not
    # rewrite the material identity to "test".
    return {"power": MATERIAL_DEFAULTS["test"]["power"], "feed_rate": MATERIAL_DEFAULTS["test"]["feed_rate"]}


def _map_known_material_alias(token: str) -> str | None:
    clean = str(token or "").strip()
    if not clean:
        return None
    direct = MATERIAL_ALIASES.get(clean) or MATERIAL_ALIASES.get(clean.lower())
    if direct:
        return direct
    lowered = clean.lower()
    for alias, material in MATERIAL_ALIASES.items():
        if alias.lower() == lowered:
            return material
    return None


def _resolve_material(prompt: str, explicit_material: str | None = None) -> str:
    if explicit_material is not None and str(explicit_material).strip():
        token = str(explicit_material).strip()
        return _map_known_material_alias(token) or token
    return _parse_material(prompt)


def _parse_material(prompt: str) -> str:
    """Prompt-only material guess. Unknown prompt text still falls back to test."""
    lowered = prompt.lower()
    for token, material in MATERIAL_ALIASES.items():
        token_lower = token.lower()
        if token_lower.isascii() and re.search(rf"\b{re.escape(token_lower)}\b", lowered):
            return material
        if not token_lower.isascii() and token_lower in lowered:
            return material
    return "test"


def _parse_mode(prompt: str) -> JobMode:
    if re.search(r"\bauto\b|自动", prompt, re.IGNORECASE):
        return "auto"
    if re.search(r"照片|photo|光栅|raster", prompt, re.IGNORECASE):
        return "raster"
    if re.search(r"轮廓|outline", prompt, re.IGNORECASE):
        return "outline"
    return "auto"


def _parse_trace_algorithm(prompt: str) -> TraceAlgorithm:
    if re.search(r"trace[_ -]?algorithm\s*[:=]?\s*vector|算法\s*[:=]?\s*vector", prompt, re.IGNORECASE):
        return "vector"
    if re.search(r"trace[_ -]?algorithm\s*[:=]?\s*(?:auto|basic|opencv|potrace)|算法\s*[:=]?\s*(?:auto|basic|opencv|potrace)", prompt, re.IGNORECASE):
        raise ValueError("Only trace_algorithm=vector is supported.")
    return "vector"


def _parse_dither_algorithm(prompt: str) -> DitherAlgorithm:
    value, _manual = _parse_dither_algorithm_with_manual(prompt)
    return value


def _parse_dither_algorithm_with_manual(prompt: str) -> tuple[DitherAlgorithm, bool]:
    if re.search(r"(?:dither(?:ing)?[_ -]?algorithm|dither(?:ing)?|抖动算法|抖动)\s*[:=]?\s*(?:threshold|none|off)|(?:no|without)\s+dither(?:ing)?|无抖动|关闭抖动|二值", prompt, re.IGNORECASE):
        return "threshold", True
    if re.search(r"(?:dither(?:ing)?[_ -]?algorithm|dither(?:ing)?|抖动算法|抖动)\s*[:=]?\s*sierra[_ -]?lite", prompt, re.IGNORECASE):
        return "sierra_lite", True
    if re.search(r"(?:dither(?:ing)?[_ -]?algorithm|dither(?:ing)?|抖动算法|抖动)\s*[:=]?\s*atkinson", prompt, re.IGNORECASE):
        return "atkinson", True
    if re.search(r"(?:dither(?:ing)?[_ -]?algorithm|dither(?:ing)?|抖动算法|抖动)\s*[:=]?\s*floyd[_ -]?steinberg", prompt, re.IGNORECASE):
        return "floyd_steinberg", True
    if re.search(r"(?:dither(?:ing)?[_ -]?algorithm|dither(?:ing)?|抖动算法|抖动)\s*[:=]?\s*auto", prompt, re.IGNORECASE):
        return "auto", False
    return "floyd_steinberg", False


def _resolve_dither_algorithm(structured: DitherAlgorithm | None, prompt: str) -> tuple[DitherAlgorithm, bool]:
    if structured is None:
        return _parse_dither_algorithm_with_manual(prompt)
    value = str(structured).strip().lower().replace("-", "_")
    if value not in {"auto", "threshold", "floyd_steinberg", "atkinson", "sierra_lite"}:
        raise ValueError("dither_algorithm must be auto, threshold, floyd_steinberg, atkinson, or sierra_lite")
    return value, value != "auto"  # type: ignore[return-value]


def _resolve_threshold(structured: int | None, prompt: str) -> tuple[int, bool]:
    if structured is not None:
        try:
            value = int(structured)
        except (TypeError, ValueError) as error:
            raise ValueError("threshold must be an integer in 0..255 or -1 for auto") from error
        if value == -1:
            return -1, False
        if 0 <= value <= 255:
            return value, True
        raise ValueError("threshold must be an integer in 0..255 or -1 for auto")
    match = re.search(r"(?:threshold|阈值)\s*[:=]?\s*(-?\d+)", prompt, re.IGNORECASE)
    if match:
        value = int(match.group(1))
        if value == -1:
            return -1, False
        if 0 <= value <= 255:
            return value, True
        raise ValueError("threshold must be an integer in 0..255 or -1 for auto")
    return -1, False


ERROR_DIFFUSION_ALGORITHMS = frozenset({"floyd_steinberg", "atkinson", "sierra_lite"})


def _apply_threshold_dither_rules(
    threshold: int,
    manual_threshold: bool,
    dither_algorithm: DitherAlgorithm,
    manual_dither: bool,
    structured_dither: DitherAlgorithm | None,
) -> DitherAlgorithm:
    """Resolve dither when an explicit threshold is present.

    Explicit threshold + omitted/auto dither => direct threshold mode.
    Explicit threshold + explicit error diffusion => conflict (fail early).
    """
    if not manual_threshold:
        return dither_algorithm
    # Structured auto, prompt auto, or default non-manual dither counts as "auto/omit".
    dither_is_auto_or_default = (not manual_dither) or dither_algorithm == "auto" or (
        structured_dither is not None and str(structured_dither).strip().lower() == "auto"
    )
    if dither_algorithm == "auto" or dither_is_auto_or_default:
        return "threshold"
    if dither_algorithm in ERROR_DIFFUSION_ALGORITHMS and manual_dither:
        raise ValueError(
            "threshold conflicts with error-diffusion dither_algorithm; "
            "use dither_algorithm=threshold/auto or omit dither when setting threshold 0..255"
        )
    return dither_algorithm


def _parse_raster_scan_direction(prompt: str) -> RasterScanDirection:
    if re.search(r"(?:raster[_ -]?scan[_ -]?direction|scan[_ -]?direction|扫描方向)\s*[:=]?\s*vertical|竖向|纵向", prompt, re.IGNORECASE):
        return "vertical"
    if re.search(r"(?:raster[_ -]?scan[_ -]?direction|scan[_ -]?direction|扫描方向)\s*[:=]?\s*horizontal|横向", prompt, re.IGNORECASE):
        return "horizontal"
    if re.search(r"(?:raster[_ -]?scan[_ -]?direction|scan[_ -]?direction|扫描方向)\s*[:=]?\s*auto", prompt, re.IGNORECASE):
        return "auto"
    return "auto"


def _parse_raster_output_strategy(prompt: str) -> RasterOutputStrategy:
    explicit = re.search(r"(?:raster[_ -]?output[_ -]?strategy|output[_ -]?strategy|光栅输出策略)\s*[:=]?\s*([\w-]+)", prompt, re.IGNORECASE)
    if explicit:
        value = explicit.group(1).lower().replace("-", "_")
        if value in {"auto", "segment", "scanline"}:
            return value
        raise ValueError("Raster output strategy must be auto, segment, or scanline.")
    if re.search(r"连续扫描线|整行扫描", prompt, re.IGNORECASE):
        return "scanline"
    if re.search(r"黑段模式|分段输出", prompt, re.IGNORECASE):
        return "segment"
    return "auto"


def _parse_fill_strategy(prompt: str) -> FillStrategy:
    if re.search(r"(?:fill[_ -]?strategy|填充策略)\s*[:=]?\s*zigzag|折返填充|蛇形填充", prompt, re.IGNORECASE):
        return "zigzag"
    if re.search(r"(?:fill[_ -]?strategy|填充策略)\s*[:=]?\s*hatch|排线填充|横线填充", prompt, re.IGNORECASE):
        return "hatch"
    if re.search(r"(?:fill[_ -]?strategy|填充策略)\s*[:=]?\s*none|不填充|只描边", prompt, re.IGNORECASE):
        return "none"
    if re.search(r"(?:fill[_ -]?strategy|填充策略)\s*[:=]?\s*auto|自动填充", prompt, re.IGNORECASE):
        return "auto"
    return "none"


def _parse_arc_output(prompt: str) -> bool:
    return re.search(r"arc[_ -]?output\s*[:=]?\s*(?:1|true|yes|on)|圆弧输出", prompt, re.IGNORECASE) is not None


def _parse_firmware_supports_arc(prompt: str) -> bool:
    return re.search(r"firmware[_ -]?supports[_ -]?arc\s*[:=]?\s*(?:1|true|yes|on)|固件支持圆弧", prompt, re.IGNORECASE) is not None


def _infer_task_type(mode: JobMode) -> TaskType | None:
    if mode == "raster":
        return "engrave_photo"
    if mode == "outline":
        return "engrave_logo"
    return None


def _resolve_task_type(task_type: TaskType | str | None, mode: JobMode) -> TaskType | str | None:
    if task_type is None or str(task_type).strip() == "":
        return _infer_task_type(mode)
    normalized = str(task_type).strip().lower()
    if normalized in {"engrave_photo", "engrave_logo", "cut_contour"}:
        return normalized
    if normalized in {"engrave", "engraving", "雕刻"}:
        return _infer_task_type(mode)
    if normalized in {"cut", "cutting", "切割"}:
        return "cut_contour"
    return normalized


def _parse_thickness(prompt: str) -> float | None:
    match = re.search(r"(?:thickness|厚度)\s*[:=]?\s*(\d+(?:\.\d+)?)\s*mm", prompt, re.IGNORECASE)
    if not match:
        return None
    return float(match.group(1))


def _parse_number(prompt: str, pattern: str, default: float, flags: int = 0) -> float:
    match = re.search(pattern, prompt, flags)
    if not match:
        return default
    return float(match.group(1))
