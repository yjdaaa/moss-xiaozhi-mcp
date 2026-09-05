from pathlib import Path
from dataclasses import replace

from core.ai_laser_gcode.image_trace import _connected_components, _read_image_pixels
from core.ai_laser_gcode.models import JobParams, RoutingCandidate, RoutingMetrics, RoutingResult


FOREGROUND_THRESHOLD = 180
BINARY_MARGIN = 32
RICH_GRAYSCALE_LEVELS = 24
OUTLINE_GRAYSCALE_LEVELS = 8
OUTLINE_MIN_FOREGROUND_RATIO = 0.001
OUTLINE_MAX_FOREGROUND_RATIO = 0.45
OUTLINE_MAX_COMPONENTS = 20
LOW_CONFIDENCE_FOREGROUND_RATIO = 0.0008
MEDIUM_CONFIDENCE_MIN_LEVELS = 9
MEDIUM_CONFIDENCE_MAX_LEVELS = 23


def resolve_job_mode(image_path: Path, params: JobParams) -> tuple[JobParams, RoutingResult]:
    metrics = analyze_image(image_path)
    if params.mode in {"outline", "raster"}:
        warnings = _manual_mode_warnings(params.mode, metrics)
        return params, RoutingResult(
            mode=params.mode,
            mode_source="manual",
            reason="用户手动指定模式",
            metrics=metrics,
            confidence=1.0,
            warnings=warnings,
        )

    if params.task_type == "cut_contour" and not _looks_like_outline(metrics):
        return _replace_mode(params, "outline"), RoutingResult(
            mode="outline",
            mode_source="auto",
            reason="切割任务需要清晰闭合轮廓，当前图片轮廓不清晰，拒绝直接生成切割生产文件",
            metrics=metrics,
            confidence=0.3,
            recommendation_status="rejected_cut_contour_unclear",
            confirmation_required=True,
        )
    if _looks_low_confidence(metrics):
        return _replace_mode(params, "raster"), RoutingResult(
            mode="raster",
            mode_source="auto",
            reason="图片有效前景或结构信息不足，无法可靠推荐生产加工模式",
            metrics=metrics,
            confidence=0.3,
            recommendation_status="rejected_low_confidence",
            confirmation_required=True,
        )
    if params.task_type == "engrave_photo":
        mode = "raster"
        reason = "照片或灰度层次较丰富，适合光栅/抖动雕刻"
        confidence = 0.8
    elif _looks_medium_confidence(metrics):
        mode = "raster"
        reason = "图片兼具灰度和边缘特征，需要用户在光栅与轮廓候选间选择"
        confidence = 0.6
        candidates = [
            RoutingCandidate(mode="raster", reason="保留灰度和填充信息，适合光栅/抖动雕刻", confidence=0.6),
            RoutingCandidate(mode="outline", reason="边缘较清晰，可尝试轮廓雕刻但需确认细节", confidence=0.55),
        ]
        return _replace_mode(params, mode), RoutingResult(
            mode=mode,
            mode_source="auto",
            reason=reason,
            metrics=metrics,
            confidence=confidence,
            recommendation_status="candidate_selection_required",
            candidates=candidates,
            confirmation_required=True,
        )
    elif params.task_type is None and _looks_medium_confidence(metrics):
        mode = "raster"
        reason = "图片介于照片和线稿之间，需要用户在光栅与轮廓候选间选择"
        confidence = 0.6
        candidates = [
            RoutingCandidate(mode="raster", reason="保留灰度和填充信息，适合光栅/抖动雕刻", confidence=0.6),
            RoutingCandidate(mode="outline", reason="边缘较清晰，可尝试轮廓雕刻但需确认细节", confidence=0.55),
        ]
        return _replace_mode(params, mode), RoutingResult(
            mode=mode,
            mode_source="auto",
            reason=reason,
            metrics=metrics,
            confidence=confidence,
            recommendation_status="candidate_selection_required",
            candidates=candidates,
            confirmation_required=True,
        )
    elif _looks_like_outline(metrics):
        mode = "outline"
        reason = "图片黑白分明、前景占比适中，适合轮廓雕刻"
        confidence = 0.8
    elif _looks_like_raster(metrics):
        mode = "raster"
        reason = "图片灰度层次较丰富，适合光栅雕刻"
        confidence = 0.8
    else:
        mode = "raster"
        reason = "图片特征不明确，选择更保守的光栅模式"
        confidence = 0.5

    return _replace_mode(params, mode), RoutingResult(
        mode=mode,
        mode_source="auto",
        reason=reason,
        metrics=metrics,
        confidence=confidence,
    )


def analyze_image(image_path: Path) -> RoutingMetrics:
    pixels, _width, _height = _read_image_pixels(image_path)
    values = [value for row in pixels for value in row]
    total = len(values) or 1
    foreground = {(x, y) for y, row in enumerate(pixels) for x, value in enumerate(row) if value < FOREGROUND_THRESHOLD}
    binary_count = sum(1 for value in values if value <= BINARY_MARGIN or value >= 255 - BINARY_MARGIN)
    components = [component for component in _connected_components(foreground) if len(component) >= 4]
    largest_component = max((len(component) for component in components), default=0)
    return RoutingMetrics(
        grayscale_levels=len(set(values)),
        foreground_ratio=len(foreground) / total,
        binary_ratio=binary_count / total,
        component_count=len(components),
        edge_density=_edge_density(pixels),
        largest_component_ratio=largest_component / total,
    )


def _looks_like_outline(metrics: RoutingMetrics) -> bool:
    return (
        metrics.grayscale_levels <= OUTLINE_GRAYSCALE_LEVELS
        and metrics.binary_ratio >= 0.95
        and OUTLINE_MIN_FOREGROUND_RATIO <= metrics.foreground_ratio <= OUTLINE_MAX_FOREGROUND_RATIO
        and metrics.component_count >= 1
        and (metrics.component_count <= OUTLINE_MAX_COMPONENTS or metrics.binary_ratio >= 0.99)
    )


def _looks_like_raster(metrics: RoutingMetrics) -> bool:
    return metrics.grayscale_levels >= RICH_GRAYSCALE_LEVELS or metrics.binary_ratio < 0.85


def _looks_medium_confidence(metrics: RoutingMetrics) -> bool:
    return (
        MEDIUM_CONFIDENCE_MIN_LEVELS <= metrics.grayscale_levels <= MEDIUM_CONFIDENCE_MAX_LEVELS
        and metrics.binary_ratio >= 0.75
        and metrics.component_count >= 1
        and metrics.foreground_ratio >= OUTLINE_MIN_FOREGROUND_RATIO
    )


def _looks_low_confidence(metrics: RoutingMetrics) -> bool:
    return metrics.foreground_ratio < LOW_CONFIDENCE_FOREGROUND_RATIO


def _manual_mode_warnings(mode: str, metrics: RoutingMetrics) -> list[str] | None:
    if mode == "outline" and _looks_like_raster(metrics):
        return ["照片或丰富灰度图直接使用 outline 可能只得到稀疏外轮廓或碎线，建议改用 raster，或先把照片处理成黑白线稿后再使用 outline。"]
    if mode == "raster" and _looks_like_outline(metrics):
        return ["当前图片黑白分明且像 Logo/线稿，手动 raster 会按光栅填充雕刻；如果只想刻边线，建议改用 auto 或 outline。"]
    return None


def _edge_density(pixels: list[list[int]]) -> float:
    edge_count = 0
    comparisons = 0
    for y_coord, row in enumerate(pixels):
        for x_coord, value in enumerate(row):
            if x_coord + 1 < len(row):
                comparisons += 1
                if abs(value - row[x_coord + 1]) > BINARY_MARGIN:
                    edge_count += 1
            if y_coord + 1 < len(pixels):
                comparisons += 1
                if abs(value - pixels[y_coord + 1][x_coord]) > BINARY_MARGIN:
                    edge_count += 1
    return edge_count / comparisons if comparisons else 0.0


def _replace_mode(params: JobParams, mode: str) -> JobParams:
    return replace(params, mode=mode)
