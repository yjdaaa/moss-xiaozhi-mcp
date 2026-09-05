from __future__ import annotations

from dataclasses import replace

from core.laser_time_estimate import estimate_gcode_time_seconds
from core.ai_laser_gcode.gcode_writer import build_raster_gcode
from core.ai_laser_gcode.image_router import analyze_image
from core.ai_laser_gcode.models import JobParams, RasterResult, RoutingMetrics
from core.ai_laser_gcode.raster import MAX_RASTER_CELLS, rasterize_image
from core.ai_laser_gcode.safety import validate_raster_result


DEFAULT_RASTER_QUALITY_STRATEGY = "auto"
RASTER_QUALITY_STRATEGIES = {"auto", "speed", "balanced", "quality", "manual"}


def select_raster_quality_profile(
    image_path,
    params: JobParams,
    metrics: RoutingMetrics | None = None,
    strategy: str | None = DEFAULT_RASTER_QUALITY_STRATEGY,
) -> tuple[JobParams, RasterResult | None, dict[str, object] | None]:
    normalized_strategy = _normalize_strategy(strategy)
    manual_pixel = bool(params.manual_pixel_size)
    manual_direction = params.raster_scan_direction != "auto"
    if normalized_strategy == "manual" or (manual_pixel and manual_direction):
        return params, None, None

    metrics = metrics or analyze_image(image_path)
    image_type = _classify_image(metrics)
    candidates = []
    for pixel_size in _candidate_pixel_sizes(params, image_type, normalized_strategy, manual_pixel):
        for direction in _candidate_directions(params, manual_direction):
            candidate = _evaluate_candidate(image_path, params, pixel_size, direction, metrics, image_type)
            if candidate:
                candidates.append(candidate)
            if len(candidates) >= 6:
                break
        if len(candidates) >= 6:
            break
    if not candidates:
        return params, None, None

    _score_candidates(candidates, image_type, normalized_strategy)
    selected = max(candidates, key=lambda item: item["score"])
    selected_params = selected["params"]
    selected_summary = _candidate_summary(selected)
    profile = {
        "strategy": normalized_strategy,
        "image_type": image_type,
        "selected": selected_summary,
        "reason": _selection_reason(selected_summary, image_type),
        "candidates": [_candidate_summary(candidate) for candidate in candidates],
    }
    selected_raster = replace(selected["raster"], quality_profile=profile)
    return selected_params, selected_raster, profile


def _normalize_strategy(strategy: str | None) -> str:
    value = str(strategy or DEFAULT_RASTER_QUALITY_STRATEGY).strip().lower().replace("-", "_")
    aliases = {
        "fast": "speed",
        "速度优先": "speed",
        "balance": "balanced",
        "均衡": "balanced",
        "photo": "quality",
        "photo_quality": "quality",
        "照片质量优先": "quality",
        "quality_first": "quality",
        "质量优先": "quality",
        "手动": "manual",
    }
    value = aliases.get(value, value)
    return value if value in RASTER_QUALITY_STRATEGIES else DEFAULT_RASTER_QUALITY_STRATEGY


def _classify_image(metrics: RoutingMetrics) -> str:
    if metrics.grayscale_levels >= 64 or metrics.binary_ratio < 0.75:
        if metrics.edge_density >= 0.055 or metrics.component_count >= 30:
            return "complex_photo"
        return "photo"
    if metrics.binary_ratio >= 0.95 and metrics.component_count >= 8 and metrics.foreground_ratio < 0.25:
        return "text"
    if metrics.binary_ratio >= 0.95 and metrics.edge_density >= 0.035:
        return "line_art"
    return "logo"


def _candidate_pixel_sizes(params: JobParams, image_type: str, strategy: str, manual_pixel: bool) -> list[float]:
    if manual_pixel:
        return [params.pixel_size_mm]
    if strategy == "speed":
        values = [0.3, 0.4, 0.5]
    elif strategy == "quality":
        values = [0.2, 0.25, 0.3]
    elif image_type in {"photo", "complex_photo"}:
        values = [0.2, 0.25, 0.3]
    elif image_type == "text":
        values = [0.18, 0.2, 0.25]
    else:
        values = [0.2, 0.25, 0.3]
    return _unique_pixel_sizes(values)


def _candidate_directions(params: JobParams, manual_direction: bool) -> list[str]:
    if manual_direction:
        return [params.raster_scan_direction]
    return ["horizontal", "vertical"]


def _unique_pixel_sizes(values: list[float]) -> list[float]:
    seen = set()
    result = []
    for value in values:
        normalized = round(float(value), 4)
        if normalized <= 0 or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _evaluate_candidate(image_path, params: JobParams, pixel_size: float, direction: str, metrics: RoutingMetrics, image_type: str):
    candidate_params = replace(params, pixel_size_mm=pixel_size, raster_scan_direction=direction)
    try:
        raster = rasterize_image(image_path, candidate_params, image_type=image_type)
        validate_raster_result(raster)
        gcode = build_raster_gcode(candidate_params, raster)
        estimate = estimate_gcode_time_seconds(gcode)
    except Exception:
        return None
    estimated_seconds = float((estimate or {}).get("estimated_seconds") or 0.0)
    complexity = _complexity_value(raster)
    return {
        "params": candidate_params,
        "raster": raster,
        "metrics": metrics,
        "image_type": image_type,
        "pixel_size_mm": pixel_size,
        "raster_scan_direction": direction,
        "estimated_seconds": estimated_seconds,
        "segment_count": len(raster.segments),
        "scanline_transition_count": raster.scanline_transition_count,
        "grid_cells": raster.grid_width * raster.grid_height,
        "complexity": complexity,
        "time_estimate": estimate or {},
    }


def _score_candidates(candidates: list[dict[str, object]], image_type: str, strategy: str) -> None:
    positive_times = [candidate["estimated_seconds"] for candidate in candidates if candidate["estimated_seconds"]]
    min_time = min(positive_times) if positive_times else 1.0
    min_complexity = min(max(1.0, float(candidate["complexity"])) for candidate in candidates)
    weights = _score_weights(image_type, strategy)
    for candidate in candidates:
        time_score = min(1.0, min_time / max(float(candidate["estimated_seconds"] or min_time), 1.0))
        stability_score = _stability_score(candidate, min_complexity)
        detail_score = _detail_score(float(candidate["pixel_size_mm"]), image_type)
        direction_score = _direction_score(str(candidate["raster_scan_direction"]), image_type)
        visual_score = direction_score * 0.55 + detail_score * 0.45
        candidate["score_parts"] = {
            "visual": round(visual_score, 4),
            "detail": round(detail_score, 4),
            "time": round(time_score, 4),
            "stability": round(stability_score, 4),
        }
        candidate["score"] = round(
            visual_score * weights["visual"]
            + detail_score * weights["detail"]
            + time_score * weights["time"]
            + stability_score * weights["stability"],
            4,
        )


def _score_weights(image_type: str, strategy: str) -> dict[str, float]:
    if strategy == "speed":
        return {"visual": 0.18, "detail": 0.12, "time": 0.45, "stability": 0.25}
    if strategy == "quality":
        return {"visual": 0.45, "detail": 0.35, "time": 0.08, "stability": 0.12}
    if image_type in {"photo", "complex_photo"}:
        return {"visual": 0.42, "detail": 0.28, "time": 0.15, "stability": 0.15}
    return {"visual": 0.28, "detail": 0.22, "time": 0.25, "stability": 0.25}


def _detail_score(pixel_size: float, image_type: str) -> float:
    target = 0.2 if image_type in {"photo", "complex_photo", "text"} else 0.25
    if pixel_size <= target:
        return 1.0
    return max(0.25, min(1.0, target / pixel_size))


def _direction_score(direction: str, image_type: str) -> float:
    if image_type in {"photo", "complex_photo"}:
        return 1.0 if direction == "horizontal" else 0.68
    if image_type == "text":
        return 0.95 if direction == "horizontal" else 0.85
    return 0.9


def _stability_score(candidate: dict[str, object], min_complexity: float) -> float:
    complexity = max(1.0, float(candidate["complexity"]))
    complexity_score = min(1.0, min_complexity / complexity)
    grid_ratio = min(1.0, float(candidate["grid_cells"]) / MAX_RASTER_CELLS)
    grid_score = max(0.2, 1.0 - grid_ratio * 0.6)
    return complexity_score * 0.65 + grid_score * 0.35


def _complexity_value(raster: RasterResult) -> int:
    if raster.output_strategy == "scanline":
        return max(raster.scanline_transition_count, raster.scanline_count)
    return max(len(raster.segments), 1)


def _candidate_summary(candidate: dict[str, object]) -> dict[str, object]:
    raster = candidate.get("raster")
    threshold = candidate.get("threshold")
    resize_strategy = candidate.get("resize_strategy")
    dither_algorithm = candidate.get("dither_algorithm")
    if raster is not None:
        threshold = getattr(raster, "threshold", threshold)
        resize_strategy = getattr(raster, "resize_strategy", resize_strategy)
        dither_algorithm = getattr(raster, "dither_algorithm", dither_algorithm)
    return {
        "pixel_size_mm": candidate["pixel_size_mm"],
        "raster_scan_direction": candidate["raster_scan_direction"],
        "threshold": threshold,
        "resize_strategy": resize_strategy,
        "dither_algorithm": dither_algorithm,
        "score": candidate.get("score"),
        "score_parts": candidate.get("score_parts"),
        "estimated_seconds": round(float(candidate["estimated_seconds"]), 1),
        "segment_count": candidate["segment_count"],
        "scanline_transition_count": candidate["scanline_transition_count"],
        "grid_cells": candidate["grid_cells"],
    }


def _selection_reason(selected: dict[str, object], image_type: str) -> str:
    type_label = {
        "photo": "照片灰度层次较多",
        "complex_photo": "照片细节和边缘较复杂",
        "text": "文字/符号需要清晰边缘",
        "line_art": "线稿轮廓较清晰",
        "logo": "Logo/图标结构较清晰",
    }.get(image_type, "图片特征")
    return (
        f"{type_label}，综合视觉细节、预计时间和复杂度后选择 "
        f"{selected['raster_scan_direction']} + {selected['pixel_size_mm']:g}mm"
    )
