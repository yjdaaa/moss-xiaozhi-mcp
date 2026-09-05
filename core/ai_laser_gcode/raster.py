from pathlib import Path
from core.ai_laser_gcode.dimensions import resolve_locked_box_dimensions
from core.ai_laser_gcode.models import JobParams, RasterLineSegment, RasterResult, ResolvedDitherAlgorithm, ResolvedRasterOutputStrategy, ResolvedRasterScanDirection
from core.ai_laser_gcode.safety import SafetyError


MAX_RASTER_CELLS = 1_000_000
MAX_DETAIL_WORKING_PIXELS = 4_000_000
MAX_DETAIL_UPSCALE = 4
DEFAULT_THRESHOLD = 128
BLACK_PIXEL = 0
WHITE_PIXEL = 255
LINE_ART_IMAGE_TYPES = frozenset({"text", "line_art", "logo"})


def rasterize_image(image_path: Path, params: JobParams, image_type: str | None = None) -> RasterResult:
    pixels, width, height = _read_image_pixels(image_path)
    classified = image_type or _quick_classify_pixels(pixels, width, height)
    working_params = _maybe_apply_auto_line_art_strategy(params, classified, pixels, width, height)
    dither_algorithm = _resolve_dither_algorithm(working_params.dither_algorithm)
    use_threshold_path = dither_algorithm == "threshold"
    use_detail_path = use_threshold_path and classified in LINE_ART_IMAGE_TYPES

    pixels, width, height, detail_upscaled = _maybe_upscale_line_art(
        pixels,
        width,
        height,
        working_params,
        enable=use_detail_path,
    )
    grid_width, grid_height, output_width_mm, output_height_mm, scale_factor = _fit_grid(width, height, working_params)
    if grid_width * grid_height > MAX_RASTER_CELLS:
        raise SafetyError("Raster output is too large; increase pixel size, reduce size, or crop the image")

    if use_detail_path:
        grayscale = _resize_darkest(pixels, width, height, grid_width, grid_height)
        resize_strategy = "darkest_region"
    else:
        grayscale = _resize_nearest(pixels, width, height, grid_width, grid_height)
        resize_strategy = "nearest"

    resolved_threshold = _resolve_threshold_value(working_params, grayscale) if use_threshold_path else None
    dithered = _dither(grayscale, dither_algorithm, threshold=resolved_threshold)
    horizontal_segments = _horizontal_segments_from_bitmap(dithered, working_params.pixel_size_mm)
    vertical_segments = _vertical_segments_from_bitmap(dithered, working_params.pixel_size_mm)
    scan_direction, scan_source, scan_reason = _choose_scan_direction(horizontal_segments, vertical_segments, working_params.raster_scan_direction)
    segments = vertical_segments if scan_direction == "vertical" else horizontal_segments
    output_strategy, output_source, output_reason, scanline_count, transition_count = _choose_output_strategy(segments, scan_direction, working_params)
    if detail_upscaled:
        resize_strategy = f"{resize_strategy}+detail_upscale"
    return RasterResult(
        segments=segments,
        width_mm=output_width_mm,
        height_mm=output_height_mm,
        grid_width=grid_width,
        grid_height=grid_height,
        pixel_size_mm=working_params.pixel_size_mm,
        overscan_mm=working_params.overscan_mm,
        scaled=scale_factor < 1.0,
        scale_factor=scale_factor,
        work_area_width_mm=working_params.work_area_width_mm,
        work_area_height_mm=working_params.work_area_height_mm,
        dither_algorithm=dither_algorithm,
        threshold=resolved_threshold,
        resize_strategy=resize_strategy,
        scan_direction_requested=working_params.raster_scan_direction,
        scan_direction=scan_direction,
        scan_direction_source=scan_source,
        scan_direction_reason=scan_reason,
        horizontal_segment_count=len(horizontal_segments),
        vertical_segment_count=len(vertical_segments),
        output_strategy_requested=working_params.raster_output_strategy,
        output_strategy=output_strategy,
        output_strategy_source=output_source,
        output_strategy_reason=output_reason,
        scanline_count=scanline_count,
        scanline_transition_count=transition_count,
        snake_scan=output_strategy == "scanline" and scanline_count > 1,
    )


def _resolve_dither_algorithm(algorithm: str) -> ResolvedDitherAlgorithm:
    if algorithm == "auto":
        return "floyd_steinberg"
    if algorithm in {"threshold", "floyd_steinberg", "atkinson", "sierra_lite"}:
        return algorithm
    raise ValueError(f"Unknown dither_algorithm: {algorithm}")


def _resolve_scan_direction(direction: str) -> ResolvedRasterScanDirection:
    if direction in {"horizontal", "vertical"}:
        return direction
    raise ValueError(f"Unknown raster_scan_direction: {direction}")


def _resolve_threshold_value(params: JobParams, grayscale: list[list[float]] | None = None) -> int:
    if getattr(params, "manual_threshold", False) and params.threshold is not None and int(params.threshold) >= 0:
        return int(params.threshold)
    if params.threshold is not None and int(params.threshold) >= 0:
        return int(params.threshold)
    if grayscale:
        return _detail_preserving_threshold(grayscale)
    return DEFAULT_THRESHOLD


def _detail_preserving_threshold(grayscale: list[list[float]]) -> int:
    """Prefer keeping dark strokes: Otsu-like mean between dark/light, clamped."""
    values = [int(round(value)) for row in grayscale for value in row]
    if not values:
        return DEFAULT_THRESHOLD
    histogram = [0] * 256
    for value in values:
        histogram[max(0, min(255, value))] += 1
    total = len(values)
    sum_total = sum(index * count for index, count in enumerate(histogram))
    sum_background = 0.0
    weight_background = 0
    best_threshold = DEFAULT_THRESHOLD
    max_variance = -1.0
    for threshold, count in enumerate(histogram):
        weight_background += count
        if weight_background == 0:
            continue
        weight_foreground = total - weight_background
        if weight_foreground == 0:
            break
        sum_background += threshold * count
        mean_background = sum_background / weight_background
        mean_foreground = (sum_total - sum_background) / weight_foreground
        variance = weight_background * weight_foreground * (mean_background - mean_foreground) ** 2
        if variance > max_variance:
            max_variance = variance
            best_threshold = threshold
    # Slightly raise threshold so anti-aliased gray stroke edges stay black.
    preserved = min(200, max(40, best_threshold + 8))
    return int(preserved)


def _dither(
    pixels: list[list[float]],
    algorithm: ResolvedDitherAlgorithm,
    threshold: int | None = None,
) -> list[list[int]]:
    if algorithm == "threshold":
        cut = DEFAULT_THRESHOLD if threshold is None else int(threshold)
        return [[BLACK_PIXEL if value <= cut else WHITE_PIXEL for value in row] for row in pixels]
    if algorithm == "floyd_steinberg":
        return _floyd_steinberg(pixels)
    if algorithm == "atkinson":
        return _atkinson(pixels)
    if algorithm == "sierra_lite":
        return _sierra_lite(pixels)
    raise ValueError(f"Unknown dither_algorithm: {algorithm}")


def _maybe_apply_auto_line_art_strategy(
    params: JobParams,
    image_type: str,
    pixels: list[list[int]],
    width: int,
    height: int,
) -> JobParams:
    from dataclasses import replace

    if getattr(params, "manual_dither", False) or getattr(params, "manual_threshold", False):
        return params
    if image_type not in LINE_ART_IMAGE_TYPES:
        return params
    if params.dither_algorithm not in {"auto", "floyd_steinberg"}:
        return params
    # Auto line-art: pick threshold mode with detail-preserving auto threshold.
    auto_threshold = params.threshold if params.threshold is not None and int(params.threshold) >= 0 else -1
    return replace(params, dither_algorithm="threshold", threshold=auto_threshold)


def _maybe_upscale_line_art(
    pixels: list[list[int]],
    width: int,
    height: int,
    params: JobParams,
    enable: bool,
) -> tuple[list[list[int]], int, int, bool]:
    if not enable:
        return pixels, width, height, False
    if width * height >= MAX_DETAIL_WORKING_PIXELS:
        return pixels, width, height, False
    requested_width_mm, requested_height_mm = _requested_dimensions(width, height, params)
    target_grid_w = max(1, int(requested_width_mm / params.pixel_size_mm))
    target_grid_h = max(1, int(requested_height_mm / params.pixel_size_mm))
    # Upscale only when the source is coarser than the target grid (physical step too coarse).
    scale_x = target_grid_w / max(1, width)
    scale_y = target_grid_h / max(1, height)
    needed = max(scale_x, scale_y)
    if needed <= 1.0:
        return pixels, width, height, False
    scale = min(MAX_DETAIL_UPSCALE, int(needed + 0.999))
    if scale <= 1:
        return pixels, width, height, False
    new_w = width * scale
    new_h = height * scale
    if new_w * new_h > MAX_DETAIL_WORKING_PIXELS:
        # Reduce scale to stay under the working-pixel cap without changing physical output size.
        max_scale = int((MAX_DETAIL_WORKING_PIXELS / max(1, width * height)) ** 0.5)
        scale = max(1, min(scale, max_scale))
        if scale <= 1:
            return pixels, width, height, False
        new_w = width * scale
        new_h = height * scale
    upscaled = _nearest_integer_upscale(pixels, width, height, scale)
    return upscaled, new_w, new_h, True


def _nearest_integer_upscale(pixels: list[list[int]], width: int, height: int, scale: int) -> list[list[int]]:
    output: list[list[int]] = []
    for y_index in range(height * scale):
        source_y = min(height - 1, y_index // scale)
        row: list[int] = []
        for x_index in range(width * scale):
            source_x = min(width - 1, x_index // scale)
            row.append(pixels[source_y][source_x])
        output.append(row)
    return output


def apply_line_art_detail_upscale(
    image_path: Path,
    params: JobParams,
    image_type: str | None = None,
) -> tuple[Path, bool]:
    """Optional detail upscale for threshold binary line-art (raster or outline force-binary).

    Returns (path, upscaled). When upscaled is True the path is a new temporary PNG that
    the caller owns; original path is returned unchanged when skipped.
    """
    pixels, width, height = _read_image_pixels(image_path)
    classified = image_type or _quick_classify_pixels(pixels, width, height)
    if classified not in LINE_ART_IMAGE_TYPES:
        return image_path, False
    upscaled_pixels, new_w, new_h, did = _maybe_upscale_line_art(
        pixels,
        width,
        height,
        params,
        enable=True,
    )
    if not did:
        return image_path, False
    try:
        from PIL import Image
    except ImportError:
        return image_path, False
    from tempfile import NamedTemporaryFile

    image = Image.new("L", (new_w, new_h))
    flat = [value for row in upscaled_pixels for value in row]
    image.putdata(flat)
    with NamedTemporaryFile(prefix="ai_laser_detail_upscale_", suffix=".png", delete=False) as handle:
        out_path = Path(handle.name)
    image.save(out_path)
    return out_path, True


def _quick_classify_pixels(pixels: list[list[int]], width: int, height: int) -> str:
    """Lightweight classification aligned with raster_quality line-art heuristics."""
    if width <= 0 or height <= 0:
        return "photo"
    total = width * height
    levels = set()
    black = 0
    for row in pixels:
        for value in row:
            levels.add(value)
            if value <= 32:
                black += 1
            if len(levels) >= 64:
                break
        if len(levels) >= 64:
            break
    if len(levels) >= 64:
        return "photo"
    binary_like = sum(1 for row in pixels for value in row if value <= 32 or value >= 224)
    binary_ratio = binary_like / total
    foreground_ratio = black / total
    if binary_ratio >= 0.95 and foreground_ratio < 0.25:
        return "text"
    if binary_ratio >= 0.95:
        return "line_art"
    if binary_ratio >= 0.75:
        return "logo"
    return "photo"


def _fit_grid(width: int, height: int, params: JobParams) -> tuple[int, int, float, float, float]:
    requested_width_mm, requested_height_mm = _requested_dimensions(width, height, params)
    usable_width_mm = params.work_area_width_mm - params.overscan_mm * 2
    usable_height_mm = params.work_area_height_mm
    if usable_width_mm <= 0 or usable_height_mm <= 0:
        raise SafetyError("Overscan leaves no usable raster work area")
    scale_factor = min(1.0, usable_width_mm / requested_width_mm, usable_height_mm / requested_height_mm)
    output_width_mm = requested_width_mm * scale_factor
    output_height_mm = requested_height_mm * scale_factor
    grid_width = max(1, int(output_width_mm / params.pixel_size_mm))
    grid_height = max(1, int(output_height_mm / params.pixel_size_mm))
    output_width_mm = grid_width * params.pixel_size_mm
    output_height_mm = grid_height * params.pixel_size_mm
    return grid_width, grid_height, output_width_mm, output_height_mm, scale_factor


def _requested_dimensions(width: int, height: int, params: JobParams) -> tuple[float, float]:
    resolved = resolve_locked_box_dimensions(
        width,
        height,
        params.width_mm,
        params.height_mm,
        lock_aspect_ratio=getattr(params, "lock_aspect_ratio", True),
        size_mm=params.size_mm,
    )
    if resolved["size_source"] == "size_mm":
        # Preserve legacy raster square-ish size_mm as width, proportional height.
        return float(params.size_mm), float(params.size_mm) * height / width
    return float(resolved["final_width_mm"]), float(resolved["final_height_mm"])


def _resize_nearest(pixels: list[list[int]], width: int, height: int, grid_width: int, grid_height: int) -> list[list[float]]:
    resized: list[list[float]] = []
    for y_index in range(grid_height):
        source_y = min(height - 1, int(y_index * height / grid_height))
        row: list[float] = []
        for x_index in range(grid_width):
            source_x = min(width - 1, int(x_index * width / grid_width))
            row.append(float(pixels[source_y][source_x]))
        resized.append(row)
    return resized


def _resize_darkest(pixels: list[list[int]], width: int, height: int, grid_width: int, grid_height: int) -> list[list[float]]:
    """Downsample by keeping the darkest sample in each source region (line-art strokes)."""
    resized: list[list[float]] = []
    for y_index in range(grid_height):
        y0 = int(y_index * height / grid_height)
        y1 = max(y0 + 1, int((y_index + 1) * height / grid_height))
        row: list[float] = []
        for x_index in range(grid_width):
            x0 = int(x_index * width / grid_width)
            x1 = max(x0 + 1, int((x_index + 1) * width / grid_width))
            darkest = WHITE_PIXEL
            for source_y in range(y0, min(y1, height)):
                source_row = pixels[source_y]
                for source_x in range(x0, min(x1, width)):
                    value = source_row[source_x]
                    if value < darkest:
                        darkest = value
            row.append(float(darkest))
        resized.append(row)
    return resized


def _floyd_steinberg(pixels: list[list[float]]) -> list[list[int]]:
    working = [row[:] for row in pixels]
    height = len(working)
    width = len(working[0]) if height else 0
    output = [[WHITE_PIXEL for _ in range(width)] for _ in range(height)]
    for y_index in range(height):
        for x_index in range(width):
            old_value = working[y_index][x_index]
            new_value = BLACK_PIXEL if old_value < 128 else WHITE_PIXEL
            output[y_index][x_index] = new_value
            error = old_value - new_value
            _add_error(working, x_index + 1, y_index, error * 7 / 16)
            _add_error(working, x_index - 1, y_index + 1, error * 3 / 16)
            _add_error(working, x_index, y_index + 1, error * 5 / 16)
            _add_error(working, x_index + 1, y_index + 1, error * 1 / 16)
    return output


def _atkinson(pixels: list[list[float]]) -> list[list[int]]:
    working = [row[:] for row in pixels]
    height = len(working)
    width = len(working[0]) if height else 0
    output = [[WHITE_PIXEL for _ in range(width)] for _ in range(height)]
    for y_index in range(height):
        for x_index in range(width):
            old_value = working[y_index][x_index]
            new_value = BLACK_PIXEL if old_value < 128 else WHITE_PIXEL
            output[y_index][x_index] = new_value
            error = (old_value - new_value) / 8
            _add_error(working, x_index + 1, y_index, error)
            _add_error(working, x_index + 2, y_index, error)
            _add_error(working, x_index - 1, y_index + 1, error)
            _add_error(working, x_index, y_index + 1, error)
            _add_error(working, x_index + 1, y_index + 1, error)
            _add_error(working, x_index, y_index + 2, error)
    return output


def _sierra_lite(pixels: list[list[float]]) -> list[list[int]]:
    working = [row[:] for row in pixels]
    height = len(working)
    width = len(working[0]) if height else 0
    output = [[WHITE_PIXEL for _ in range(width)] for _ in range(height)]
    for y_index in range(height):
        for x_index in range(width):
            old_value = working[y_index][x_index]
            new_value = BLACK_PIXEL if old_value < 128 else WHITE_PIXEL
            output[y_index][x_index] = new_value
            error = old_value - new_value
            _add_error(working, x_index + 1, y_index, error * 2 / 4)
            _add_error(working, x_index - 1, y_index + 1, error * 1 / 4)
            _add_error(working, x_index, y_index + 1, error * 1 / 4)
    return output


def _add_error(pixels: list[list[float]], x_index: int, y_index: int, value: float) -> None:
    if y_index < 0 or y_index >= len(pixels):
        return
    if x_index < 0 or x_index >= len(pixels[y_index]):
        return
    pixels[y_index][x_index] = min(255.0, max(0.0, pixels[y_index][x_index] + value))


def _choose_scan_direction(
    horizontal_segments: list[RasterLineSegment],
    vertical_segments: list[RasterLineSegment],
    requested: str,
) -> tuple[ResolvedRasterScanDirection, str, str]:
    if requested != "auto":
        direction = _resolve_scan_direction(requested)
        return direction, "manual", f"explicit raster_scan_direction={direction}"
    horizontal_travel = _estimated_scan_travel(horizontal_segments)
    vertical_travel = _estimated_scan_travel(vertical_segments)
    if vertical_segments and _vertical_is_clearly_better(horizontal_segments, vertical_segments, horizontal_travel, vertical_travel):
        return "vertical", "auto", f"vertical scan reduces segments {len(horizontal_segments)}->{len(vertical_segments)} and travel {horizontal_travel:.3f}->{vertical_travel:.3f}mm"
    return "horizontal", "auto", f"horizontal scan retained; segments horizontal={len(horizontal_segments)}, vertical={len(vertical_segments)}"


def _vertical_is_clearly_better(
    horizontal_segments: list[RasterLineSegment],
    vertical_segments: list[RasterLineSegment],
    horizontal_travel: float,
    vertical_travel: float,
) -> bool:
    if len(vertical_segments) < len(horizontal_segments):
        return True
    return vertical_travel + 1e-9 < horizontal_travel * 0.8


def _choose_output_strategy(
    segments: list[RasterLineSegment],
    scan_direction: ResolvedRasterScanDirection,
    params: JobParams,
) -> tuple[ResolvedRasterOutputStrategy, str, str, int, int]:
    scanline_count = _scanline_count(segments, scan_direction)
    transition_count = _scanline_transition_count(segments, scan_direction, params.overscan_mm)
    if params.raster_output_strategy == "segment":
        return "segment", "manual", "explicit raster_output_strategy=segment", scanline_count, transition_count
    if params.raster_output_strategy == "scanline":
        return "scanline", "manual", "explicit raster_output_strategy=scanline", scanline_count, transition_count
    if params.raster_output_strategy != "auto":
        raise ValueError(f"Unknown raster_output_strategy: {params.raster_output_strategy}")
    if not segments or not scanline_count:
        return "segment", "auto", "segment retained for empty raster output", scanline_count, transition_count
    segment_line_estimate = len(segments) * 6
    scanline_line_estimate = scanline_count * 3 + transition_count
    if len(segments) >= scanline_count * 2 and scanline_line_estimate <= segment_line_estimate * 0.9:
        return "scanline", "auto", f"scanline reduces estimated raster commands {segment_line_estimate}->{scanline_line_estimate} with snake scan", scanline_count, transition_count
    return "segment", "auto", f"segment retained; sparse raster segments={len(segments)}, scanlines={scanline_count}, estimated commands {segment_line_estimate}->{scanline_line_estimate}", scanline_count, transition_count


def _scanline_count(segments: list[RasterLineSegment], scan_direction: ResolvedRasterScanDirection) -> int:
    if scan_direction == "vertical":
        return len({segment.start_x_mm for segment in segments})
    return len({segment.y_mm for segment in segments})


def _scanline_transition_count(segments: list[RasterLineSegment], scan_direction: ResolvedRasterScanDirection, overscan_mm: float) -> int:
    count = 0
    for line_segments in _segment_groups(segments, scan_direction):
        black_runs = len(line_segments)
        if black_runs == 0:
            continue
        count += black_runs * 2 - 1
        if overscan_mm > 0:
            count += 2
    return count


def _segment_groups(segments: list[RasterLineSegment], scan_direction: ResolvedRasterScanDirection) -> list[list[RasterLineSegment]]:
    if scan_direction == "vertical":
        sorted_segments = sorted(segments, key=lambda segment: (segment.start_x_mm, segment.y_mm, segment.end_y_mm))
        return _group_sorted_segments(sorted_segments, lambda segment: segment.start_x_mm)
    sorted_segments = sorted(segments, key=lambda segment: (segment.y_mm, segment.start_x_mm, segment.end_x_mm))
    return _group_sorted_segments(sorted_segments, lambda segment: segment.y_mm)


def _group_sorted_segments(segments: list[RasterLineSegment], key_fn) -> list[list[RasterLineSegment]]:
    groups: list[list[RasterLineSegment]] = []
    current_key: float | None = None
    for segment in segments:
        key = key_fn(segment)
        if current_key is None or abs(key - current_key) > 1e-9:
            groups.append([])
            current_key = key
        groups[-1].append(segment)
    return groups


def _estimated_scan_travel(segments: list[RasterLineSegment]) -> float:
    last_x = 0.0
    last_y = 0.0
    distance = 0.0
    for segment in segments:
        start_x, start_y, end_x, end_y = _segment_points(segment)
        distance += ((start_x - last_x) ** 2 + (start_y - last_y) ** 2) ** 0.5
        distance += ((end_x - start_x) ** 2 + (end_y - start_y) ** 2) ** 0.5
        last_x = end_x
        last_y = end_y
    return distance


def _segment_points(segment: RasterLineSegment) -> tuple[float, float, float, float]:
    if segment.axis == "vertical":
        return segment.start_x_mm, segment.y_mm, segment.start_x_mm, segment.end_y_mm
    return segment.start_x_mm, segment.y_mm, segment.end_x_mm, segment.y_mm


def _horizontal_segments_from_bitmap(bitmap: list[list[int]], pixel_size_mm: float) -> list[RasterLineSegment]:
    segments: list[RasterLineSegment] = []
    for y_index, row in enumerate(bitmap):
        start_x: int | None = None
        for x_index, value in enumerate(row):
            if value == BLACK_PIXEL and start_x is None:
                start_x = x_index
            if value != BLACK_PIXEL and start_x is not None:
                segments.append(_horizontal_segment(y_index, start_x, x_index, pixel_size_mm))
                start_x = None
        if start_x is not None:
            segments.append(_horizontal_segment(y_index, start_x, len(row), pixel_size_mm))
    return segments


def _vertical_segments_from_bitmap(bitmap: list[list[int]], pixel_size_mm: float) -> list[RasterLineSegment]:
    segments: list[RasterLineSegment] = []
    if not bitmap:
        return segments
    width = len(bitmap[0])
    height = len(bitmap)
    for x_index in range(width):
        start_y: int | None = None
        for y_index in range(height):
            value = bitmap[y_index][x_index]
            if value == BLACK_PIXEL and start_y is None:
                start_y = y_index
            if value != BLACK_PIXEL and start_y is not None:
                segments.append(_vertical_segment(x_index, start_y, y_index, pixel_size_mm))
                start_y = None
        if start_y is not None:
            segments.append(_vertical_segment(x_index, start_y, height, pixel_size_mm))
    return segments


def _horizontal_segment(y_index: int, start_x: int, end_x: int, pixel_size_mm: float) -> RasterLineSegment:
    return RasterLineSegment(
        y_mm=y_index * pixel_size_mm,
        start_x_mm=start_x * pixel_size_mm,
        end_x_mm=end_x * pixel_size_mm,
    )


def _vertical_segment(x_index: int, start_y: int, end_y: int, pixel_size_mm: float) -> RasterLineSegment:
    return RasterLineSegment(
        y_mm=start_y * pixel_size_mm,
        start_x_mm=x_index * pixel_size_mm,
        end_x_mm=x_index * pixel_size_mm,
        axis="vertical",
        end_y_mm=end_y * pixel_size_mm,
    )


def _read_image_pixels(image_path: Path) -> tuple[list[list[int]], int, int]:
    try:
        from core.ai_laser_gcode.image_trace import _read_image_pixels as read_pixels
    except ImportError as error:
        raise ValueError("Image reader is unavailable") from error
    return read_pixels(image_path)
