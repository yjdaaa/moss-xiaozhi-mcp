import json
import math
from pathlib import Path

from core.ai_laser_gcode.ai_assistant import AiAssistResult, ai_assist_payload
from core.ai_laser_gcode.models import GcodeOutputStats, ImagePreprocessResult, JobParams, OutputBundle, RasterResult, RoutingCandidate, RoutingMetrics, RoutingResult, TraceResult
from core.ai_laser_gcode.path_optimizer import PathOptimizationStats
from core.laser_time_estimate import estimate_gcode_time_seconds, time_estimate_payload


def write_summary(output_path: Path, params: JobParams, result: TraceResult | RasterResult | None, bundle: OutputBundle | None = None, path_optimization: PathOptimizationStats | None = None, routing: RoutingResult | None = None, source_image_path: Path | None = None, ai_assist: AiAssistResult | None = None, gcode_stats: GcodeOutputStats | None = None, image_preprocess: ImagePreprocessResult | None = None) -> None:
    payload = {
        "contract_version": 1,
        "gcode_path": str(bundle.gcode_path) if bundle and bundle.gcode_path else None,
        "preview_path": str(bundle.preview_path) if bundle and bundle.preview_path else None,
        "processed_preview_path": str(bundle.processed_preview_path) if bundle and bundle.processed_preview_path else None,
        "summary_path": str(bundle.summary_path) if bundle else str(output_path),
        "material": params.material,
        "thickness_mm": params.thickness_mm,
        "thickness_source": params.thickness_source,
        "matched_thickness_mm": params.matched_thickness_mm,
        "task_type": params.task_type,
        "task_type_source": params.task_type_source,
        "machine_profile_id": params.machine_profile_id,
        "size_mm": params.size_mm,
        "width_mm": params.width_mm,
        "height_mm": params.height_mm,
        "power": params.power,
        "feed_rate": params.feed_rate,
        "speed": params.feed_rate,
        "passes": params.passes,
        "confidence": params.confidence,
        "parameter_source": params.parameter_source,
        "match_type": params.match_type,
        "matched_material": params.matched_material,
        "material_group": params.material_group,
        "material_match_policy": params.material_match_policy,
        "send_policy": params.send_policy,
        "material_warnings": list(params.material_warnings),
        "can_send": params.can_send,
        "requires_sample_test": params.requires_sample_test,
        "next_action": params.next_action,
        "message": params.message,
        "source_image_name": source_image_path.name if source_image_path else None,
        "output_format": params.output_format,
        "laser_mode": params.laser_mode,
        "mode": params.mode,
        "confirmation_required": routing.confirmation_required if routing else True,
        "confirmation_subjects": ["processed_preview", "parameter_explanation", "safety_report"],
        "recommendation_status": routing.recommendation_status if routing else "single_recommendation",
        "candidates": _candidate_payloads(routing.candidates) if routing and routing.candidates else [],
        "safety_report": _safety_report_payload(params, routing),
        "ai_assist": ai_assist_payload(ai_assist),
        "image_preprocess": _image_preprocess_payload(image_preprocess),
        "safety_note": params.safety_note,
        "origin_note": "运行前请先把激光头移动到工件左下/起点；文件使用 G92 X0 Y0 Z0 设置当前位置为原点。",
    }
    warnings = list(params.material_warnings)
    if routing:
        payload["routing"] = _routing_payload(routing)
        if routing.warnings:
            warnings.extend(routing.warnings)
    if warnings:
        payload["warnings"] = warnings
    if result is None:
        payload["inspect_only"] = True
    elif isinstance(result, RasterResult):
        time_estimate = _estimate_time_from_gcode_bundle(bundle) or _estimate_raster_time_seconds(params, result)
        payload["raster"] = {
            "width_mm": result.width_mm,
            "height_mm": result.height_mm,
            "grid_width": result.grid_width,
            "grid_height": result.grid_height,
            "pixel_size_mm": result.pixel_size_mm,
            "overscan_mm": result.overscan_mm,
            "scaled": result.scaled,
            "scale_factor": result.scale_factor,
            "work_area_width_mm": result.work_area_width_mm,
            "work_area_height_mm": result.work_area_height_mm,
            "dither_algorithm": result.dither_algorithm,
            "threshold": result.threshold,
            "resize_strategy": result.resize_strategy,
            "scan_direction_requested": result.scan_direction_requested,
            "scan_direction": result.scan_direction,
            "scan_direction_source": result.scan_direction_source,
            "scan_direction_reason": result.scan_direction_reason,
            "horizontal_segment_count": result.horizontal_segment_count,
            "vertical_segment_count": result.vertical_segment_count,
            "whitespace_skipping": result.whitespace_skipping,
            "run_length_compression": result.run_length_compression,
            "output_strategy_requested": result.output_strategy_requested,
            "output_strategy": result.output_strategy,
            "output_strategy_source": result.output_strategy_source,
            "output_strategy_reason": result.output_strategy_reason,
            "scanline_count": result.scanline_count,
            "scanline_transition_count": result.scanline_transition_count,
            "snake_scan": result.snake_scan,
            "segment_count": len(result.segments),
        }
        if result.quality_profile:
            payload["auto_raster_profile"] = result.quality_profile
            payload["raster"]["quality_profile"] = result.quality_profile
        # Top-level mirrors so Web/speech projections do not depend on nested-only fields.
        payload["threshold"] = result.threshold
        payload["resize_strategy"] = result.resize_strategy
        payload["dither_algorithm"] = result.dither_algorithm
        payload["time_estimate"] = time_estimate
    else:
        time_estimate = _estimate_time_from_gcode_bundle(bundle) or _estimate_trace_time_seconds(params, result)
        trace_complexity = _trace_complexity_payload(result)
        payload["trace"] = {
            "width_mm": result.width_mm,
            "height_mm": result.height_mm,
            "contour_count": result.contour_count,
            "point_count": result.point_count,
            "raw_contour_count": result.raw_contour_count,
            "raw_point_count": result.raw_point_count,
            "trace_algorithm": result.trace_algorithm,
            "vector_simplify_factor": params.vector_simplify_factor,
            "fill_strategy": result.fill_strategy,
            "fill_spacing_mm": result.fill_spacing_mm,
            "fill_segment_count": result.fill_segment_count,
            "fill_warnings": result.fill_warnings or [],
            "complexity": trace_complexity,
            "gcode_output": _gcode_output_payload(params, gcode_stats),
        }
        if result.fill_warnings:
            warnings = payload.setdefault("warnings", [])
            if isinstance(warnings, list):
                warnings.extend(result.fill_warnings)
        if trace_complexity["complex"]:
            warnings = payload.setdefault("warnings", [])
            if isinstance(warnings, list):
                warnings.append(trace_complexity["message"])
        payload["time_estimate"] = time_estimate
    if path_optimization:
        payload["path_optimization"] = {
            "strategy": path_optimization.strategy,
            "travel_distance_before": path_optimization.travel_distance_before,
            "travel_distance_after": path_optimization.travel_distance_after,
            "travel_reduction_ratio": path_optimization.travel_reduction_ratio,
            "path_count": path_optimization.path_count,
        }
    if bundle:
        payload["files"] = {
            "gcode": str(bundle.gcode_path) if bundle.gcode_path else None,
            "preview": str(bundle.preview_path) if bundle.preview_path else None,
            "processed_preview": str(bundle.processed_preview_path) if bundle.processed_preview_path else None,
            "summary": str(bundle.summary_path),
        }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


_LASERGRBL_RAPID_RATE_MM_PER_MINUTE = 12000.0


def _estimate_time_from_gcode_bundle(bundle: OutputBundle | None) -> dict[str, object] | None:
    if bundle is None or bundle.gcode_path is None or not bundle.gcode_path.exists():
        return None
    try:
        return _estimate_gcode_time_seconds(bundle.gcode_path.read_text(encoding="utf-8"))
    except OSError:
        return None


def _estimate_gcode_time_seconds(gcode: str, rapid_rate: float = _LASERGRBL_RAPID_RATE_MM_PER_MINUTE) -> dict[str, object]:
    return estimate_gcode_time_seconds(gcode, rapid_rate=rapid_rate)


def _routing_payload(routing: RoutingResult) -> dict[str, object]:
    return {
        "mode_source": routing.mode_source,
        "reason": routing.reason,
        "user_reason": routing.reason,
        "metrics": _routing_metrics_payload(routing.metrics),
        "confidence": routing.confidence,
        "recommendation_status": routing.recommendation_status,
        "fallback_reason": routing.reason if routing.mode_source == "auto" and routing.mode == "raster" and routing.confidence < 0.8 else None,
        "warnings": routing.warnings or [],
    }


def _routing_metrics_payload(metrics: RoutingMetrics | None) -> dict[str, int | float | None]:
    if metrics is None:
        return {
            "grayscale_levels": None,
            "foreground_ratio": None,
            "binary_ratio": None,
            "component_count": None,
        }
    return {
        "grayscale_levels": metrics.grayscale_levels,
        "foreground_ratio": metrics.foreground_ratio,
        "binary_ratio": metrics.binary_ratio,
        "component_count": metrics.component_count,
        "edge_density": metrics.edge_density,
        "largest_component_ratio": metrics.largest_component_ratio,
    }


def _candidate_payloads(candidates: list[RoutingCandidate]) -> list[dict[str, object]]:
    return [
        {
            "mode": candidate.mode,
            "reason": candidate.reason,
            "confidence": candidate.confidence,
            "preview_path": str(candidate.preview_path) if candidate.preview_path else None,
            "gcode_path": str(candidate.gcode_path) if candidate.gcode_path else None,
            "selectable": candidate.selectable,
            "safety_status": candidate.safety_status,
        }
        for candidate in candidates
    ]


def _safety_report_payload(params: JobParams, routing: RoutingResult | None) -> dict[str, object]:
    cut_contour_clear = None
    if params.task_type == "cut_contour":
        cut_contour_clear = bool(routing and routing.recommendation_status == "single_recommendation" and routing.mode == "outline")
    warnings = list(params.material_warnings)
    if routing and routing.warnings:
        warnings.extend(routing.warnings)
    return {
        "can_send": params.can_send,
        "requires_sample_test": params.requires_sample_test,
        "next_action": params.next_action,
        "parameter_confidence": params.confidence,
        "parameter_source": params.parameter_source,
        "match_type": params.match_type,
        "matched_thickness_mm": params.matched_thickness_mm,
        "material_match_policy": params.material_match_policy,
        "send_policy": params.send_policy,
        "task_type": params.task_type,
        "cut_contour_clear": cut_contour_clear,
        "recommendation_status": routing.recommendation_status if routing else "single_recommendation",
        "message": params.message,
        "warnings": warnings,
    }


def _image_preprocess_payload(result: ImagePreprocessResult | None) -> dict[str, object]:
    if result is None:
        return {
            "requested": {},
            "resolved": {"grayscale": False, "binarized": False, "dithered": False},
            "source": "default",
            "warnings": [],
        }
    return {
        "requested": {
            "invert": result.invert,
            "threshold": result.threshold,
            "brightness": result.brightness,
            "contrast": result.contrast,
            "cleanup_background": result.cleanup_background,
        },
        "resolved": {
            "processed_image_name": result.processed_path.name,
            "grayscale": result.grayscale,
            "grayscale_formula": result.grayscale_formula,
            "binarized": result.binarized,
            "dithered": result.dithered,
            "reason": result.reason,
        },
        "source": result.source,
        "warnings": result.warnings or [],
    }


def _trace_complexity_payload(trace: TraceResult) -> dict[str, object]:
    raw_contour_count = trace.raw_contour_count if trace.raw_contour_count is not None else trace.contour_count
    raw_point_count = trace.raw_point_count if trace.raw_point_count is not None else trace.point_count
    output_complex_trace = trace.contour_count > 100 or trace.point_count > 5000
    raw_complex_trace = raw_contour_count > 100 or raw_point_count > 5000
    complex_trace = output_complex_trace or raw_complex_trace
    message = "路径复杂，可能耗时长/文件大。" if complex_trace else "路径复杂度在常规范围内。"
    return {
        "complex": complex_trace,
        "output_complex": output_complex_trace,
        "raw_complex": raw_complex_trace,
        "message": message,
        "contour_count": trace.contour_count,
        "point_count": trace.point_count,
        "raw_contour_count": raw_contour_count,
        "raw_point_count": raw_point_count,
        "contour_warning_threshold": 100,
        "point_warning_threshold": 5000,
    }


def _gcode_output_payload(params: JobParams, stats: GcodeOutputStats | None) -> dict[str, object]:
    if stats is None:
        return {
            "arc_enabled": params.arc_output,
            "arc_supported": params.firmware_supports_arc,
            "arc_tolerance_mm": params.arc_tolerance_mm,
            "arc_count": 0,
            "line_segment_count": 0,
            "fallback_segment_count": 0,
        }
    return {
        "arc_enabled": stats.arc_enabled,
        "arc_supported": stats.arc_supported,
        "arc_tolerance_mm": params.arc_tolerance_mm,
        "arc_count": stats.arc_count,
        "line_segment_count": stats.line_segment_count,
        "fallback_segment_count": stats.fallback_segment_count,
    }


def _estimate_raster_time_seconds(params: JobParams, raster: RasterResult) -> dict[str, object]:
    feed_mm_per_second = params.feed_rate / 60
    rapid_mm_per_second = 3000 / 60
    laser_distance_mm = 0.0
    non_laser_distance_mm = 0.0
    last_x = 0.0
    last_y = 0.0
    if raster.output_strategy == "scanline":
        for line_index, segments in enumerate(_raster_scanline_groups(raster)):
            if not segments:
                continue
            start_x, start_y, end_x, end_y, laser_distance = _scanline_points_and_laser_distance(raster, segments, line_index)
            non_laser_distance_mm += math.hypot(start_x - last_x, start_y - last_y)
            non_laser_distance_mm += max(0.0, math.hypot(end_x - start_x, end_y - start_y) - laser_distance)
            laser_distance_mm += laser_distance
            last_x = end_x
            last_y = end_y
        laser_seconds = laser_distance_mm / feed_mm_per_second if feed_mm_per_second else 0.0
        non_laser_seconds = non_laser_distance_mm / feed_mm_per_second if feed_mm_per_second else 0.0
        total_seconds = laser_seconds + non_laser_seconds
        return _time_estimate_payload(total_seconds, laser_seconds, non_laser_seconds, laser_distance_mm, non_laser_distance_mm)
    for segment in raster.segments:
        if segment.axis == "vertical":
            start_x = segment.start_x_mm
            start_y = segment.y_mm
            end_y = segment.end_y_mm
            non_laser_distance_mm += math.hypot(start_x - last_x, start_y - last_y)
            laser_distance_mm += abs(end_y - start_y)
            last_x = start_x
            last_y = end_y
        else:
            start_x = segment.start_x_mm + raster.overscan_mm
            end_x = segment.end_x_mm + raster.overscan_mm
            overscan_start = max(0.0, start_x - raster.overscan_mm)
            overscan_end = min(raster.work_area_width_mm, end_x + raster.overscan_mm)
            non_laser_distance_mm += math.hypot(overscan_start - last_x, segment.y_mm - last_y)
            non_laser_distance_mm += abs(start_x - overscan_start)
            laser_distance_mm += abs(end_x - start_x)
            non_laser_distance_mm += abs(overscan_end - end_x)
            last_x = overscan_end
            last_y = segment.y_mm
    laser_seconds = laser_distance_mm / feed_mm_per_second if feed_mm_per_second else 0.0
    non_laser_seconds = non_laser_distance_mm / rapid_mm_per_second if rapid_mm_per_second else 0.0
    total_seconds = laser_seconds + non_laser_seconds
    return _time_estimate_payload(total_seconds, laser_seconds, non_laser_seconds, laser_distance_mm, non_laser_distance_mm)


def _raster_scanline_groups(raster: RasterResult) -> list[list[object]]:
    if raster.scan_direction == "vertical":
        sorted_segments = sorted(raster.segments, key=lambda segment: (segment.start_x_mm, segment.y_mm, segment.end_y_mm))
        return _group_scanline_segments(sorted_segments, lambda segment: segment.start_x_mm)
    sorted_segments = sorted(raster.segments, key=lambda segment: (segment.y_mm, segment.start_x_mm, segment.end_x_mm))
    return _group_scanline_segments(sorted_segments, lambda segment: segment.y_mm)


def _group_scanline_segments(segments: list[object], key_fn) -> list[list[object]]:
    groups: list[list[object]] = []
    current_key: float | None = None
    for segment in segments:
        key = key_fn(segment)
        if current_key is None or abs(key - current_key) > 1e-9:
            groups.append([])
            current_key = key
        groups[-1].append(segment)
    return groups


def _scanline_points_and_laser_distance(raster: RasterResult, segments: list[object], line_index: int) -> tuple[float, float, float, float, float]:
    reverse = raster.snake_scan and line_index % 2 == 1
    laser_distance = sum(abs(segment.end_y_mm - segment.y_mm) if segment.axis == "vertical" else abs(segment.end_x_mm - segment.start_x_mm) for segment in segments)
    if segments[0].axis == "vertical":
        x_coord = segments[0].start_x_mm
        shifted_runs = [(segment.y_mm + raster.overscan_mm, segment.end_y_mm + raster.overscan_mm) for segment in segments]
        scan_start = max(0.0, min(start for start, _ in shifted_runs) - raster.overscan_mm)
        scan_end = min(raster.work_area_height_mm, max(end for _, end in shifted_runs) + raster.overscan_mm)
        return (x_coord, scan_end, x_coord, scan_start, laser_distance) if reverse else (x_coord, scan_start, x_coord, scan_end, laser_distance)
    y_coord = segments[0].y_mm
    shifted_runs = [(segment.start_x_mm + raster.overscan_mm, segment.end_x_mm + raster.overscan_mm) for segment in segments]
    scan_start = max(0.0, min(start for start, _ in shifted_runs) - raster.overscan_mm)
    scan_end = min(raster.work_area_width_mm, max(end for _, end in shifted_runs) + raster.overscan_mm)
    return (scan_end, y_coord, scan_start, y_coord, laser_distance) if reverse else (scan_start, y_coord, scan_end, y_coord, laser_distance)


def _estimate_trace_time_seconds(params: JobParams, trace: TraceResult) -> dict[str, object]:
    feed_mm_per_second = params.feed_rate / 60
    rapid_mm_per_second = 3000 / 60
    laser_distance_mm = 0.0
    non_laser_distance_mm = 0.0
    last_x = 0.0
    last_y = 0.0
    for path in trace.paths:
        if not path:
            continue
        start_x, start_y = path[0]
        non_laser_distance_mm += math.hypot(start_x - last_x, start_y - last_y)
        previous_x, previous_y = start_x, start_y
        for x_coord, y_coord in path[1:]:
            laser_distance_mm += math.hypot(x_coord - previous_x, y_coord - previous_y)
            previous_x, previous_y = x_coord, y_coord
        if len(path) > 2 and path[-1] != path[0]:
            laser_distance_mm += math.hypot(start_x - previous_x, start_y - previous_y)
            previous_x, previous_y = start_x, start_y
        last_x = previous_x
        last_y = previous_y
    laser_seconds = laser_distance_mm / feed_mm_per_second if feed_mm_per_second else 0.0
    non_laser_seconds = non_laser_distance_mm / rapid_mm_per_second if rapid_mm_per_second else 0.0
    total_seconds = laser_seconds + non_laser_seconds
    return _time_estimate_payload(total_seconds, laser_seconds, non_laser_seconds, laser_distance_mm, non_laser_distance_mm)


def _time_estimate_payload(total_seconds: float, laser_seconds: float, non_laser_seconds: float, laser_distance_mm: float, non_laser_distance_mm: float) -> dict[str, object]:
    return time_estimate_payload(total_seconds, laser_seconds, non_laser_seconds, laser_distance_mm, non_laser_distance_mm)
