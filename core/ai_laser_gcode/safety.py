import os

from core.ai_laser_gcode.models import JobParams, RasterResult, TraceResult


MAX_CONTOURS = 100
MAX_POINTS = 5000
MAX_RASTER_SEGMENTS = 20000
MAX_POWER = 1000
MAX_FEED_RATE = 3000


def _env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


MIN_FEED_RATE = max(1, _env_int("LASER_MIN_FEED_RATE", 50))


class SafetyError(ValueError):
    pass


def validate_job_params(params: JobParams) -> None:
    if params.output_format not in {"gcode", "nc"}:
        raise SafetyError("Output format must be gcode or nc")
    if params.mode not in {"outline", "raster"}:
        raise SafetyError("Mode must be outline or raster")
    if params.size_mm < 5 or params.size_mm > 120:
        raise SafetyError("Size must be between 5mm and 120mm for MVP safety")
    if params.power < 1 or params.power > MAX_POWER:
        raise SafetyError(f"Power S value must stay within configured GRBL range S1-S{MAX_POWER}")
    if params.feed_rate < MIN_FEED_RATE or params.feed_rate > MAX_FEED_RATE:
        raise SafetyError(f"Feed rate must be between F{MIN_FEED_RATE} and F{MAX_FEED_RATE}")
    if params.passes < 1 or params.passes > 5:
        raise SafetyError("Pass count must be between 1 and 5")
    if params.pixel_size_mm < 0.05 or params.pixel_size_mm > 1.0:
        raise SafetyError("Raster pixel size must be between 0.05mm and 1.0mm")
    if params.overscan_mm < 0 or params.overscan_mm > 10:
        raise SafetyError("Raster overscan must be between 0mm and 10mm")
    if params.arc_tolerance_mm <= 0 or params.arc_tolerance_mm > 1.0:
        raise SafetyError("Arc fitting tolerance must be between 0mm and 1.0mm")
    if params.vector_simplify_factor < 0.25 or params.vector_simplify_factor > 8.0:
        raise SafetyError("Vector simplify factor must be between 0.25 and 8.0")
    if params.fill_strategy not in {"auto", "none", "hatch", "zigzag"}:
        raise SafetyError("Fill strategy must be auto, none, hatch, or zigzag")
    if params.fill_spacing_mm < 0.1 or params.fill_spacing_mm > 10.0:
        raise SafetyError("Fill spacing must be between 0.1mm and 10.0mm")
    if params.raster_output_strategy not in {"auto", "segment", "scanline"}:
        raise SafetyError("Raster output strategy must be auto, segment, or scanline")
    if params.work_area_width_mm <= 0 or params.work_area_height_mm <= 0:
        raise SafetyError("Work area dimensions must be positive")
    threshold = getattr(params, "threshold", -1)
    if threshold is not None:
        try:
            threshold_value = int(threshold)
        except (TypeError, ValueError) as error:
            raise SafetyError("threshold must be an integer in 0..255 or -1 for auto") from error
        if threshold_value != -1 and not (0 <= threshold_value <= 255):
            raise SafetyError("threshold must be an integer in 0..255 or -1 for auto")
    if getattr(params, "manual_threshold", False) and getattr(params, "manual_dither", False):
        if params.dither_algorithm in {"floyd_steinberg", "atkinson", "sierra_lite"}:
            raise SafetyError(
                "threshold conflicts with error-diffusion dither_algorithm; "
                "use dither_algorithm=threshold/auto or omit dither when setting threshold 0..255"
            )


def validate_trace_result(trace: TraceResult) -> None:
    if trace.contour_count < 1 or trace.point_count < 2:
        raise SafetyError("No usable foreground contour was detected; simplify the image or increase foreground contrast")
    if trace.width_mm < 1 or trace.height_mm < 1:
        raise SafetyError("Trace dimensions are too small to engrave safely")


def validate_raster_result(raster: RasterResult) -> None:
    if raster.grid_width < 1 or raster.grid_height < 1:
        raise SafetyError("Raster grid is too small to engrave safely")
    if raster.width_mm <= 0 or raster.height_mm <= 0:
        raise SafetyError("Raster dimensions are too small to engrave safely")
    if raster.width_mm + raster.overscan_mm * 2 > raster.work_area_width_mm + 1e-9:
        raise SafetyError("Raster width plus overscan exceeds the hardware work area")
    raster_height_with_overscan = raster.height_mm + raster.overscan_mm * 2 if raster.scan_direction == "vertical" else raster.height_mm
    if raster_height_with_overscan > raster.work_area_height_mm + 1e-9:
        raise SafetyError("Raster height exceeds the hardware work area")
    raster_complexity = raster.scanline_transition_count if raster.output_strategy == "scanline" else len(raster.segments)
    if raster_complexity > MAX_RASTER_SEGMENTS:
        raise SafetyError("Raster output is too complex; increase pixel size, reduce size, or crop the image")
