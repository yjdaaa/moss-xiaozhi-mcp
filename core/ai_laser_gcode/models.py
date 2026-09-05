from dataclasses import dataclass
from pathlib import Path
from typing import Literal


ResolvedJobMode = Literal["outline", "raster"]
JobMode = Literal["auto", "outline", "raster"]
TraceAlgorithm = Literal["vector"]
ResolvedTraceAlgorithm = Literal["vector"]
FillStrategy = Literal["auto", "none", "hatch", "zigzag"]
ResolvedFillStrategy = Literal["none", "hatch", "zigzag"]
DitherAlgorithm = Literal["auto", "threshold", "floyd_steinberg", "atkinson", "sierra_lite"]
ResolvedDitherAlgorithm = Literal["threshold", "floyd_steinberg", "atkinson", "sierra_lite"]
RasterScanDirection = Literal["auto", "horizontal", "vertical"]
ResolvedRasterScanDirection = Literal["horizontal", "vertical"]
RasterOutputStrategy = Literal["auto", "segment", "scanline"]
ResolvedRasterOutputStrategy = Literal["segment", "scanline"]
TaskType = Literal["engrave_photo", "engrave_logo", "cut_contour"]
TaskTypeSource = Literal["explicit", "inferred", "missing"]
ThicknessSource = Literal["explicit", "missing"]
ParameterConfidence = Literal["verified", "library", "estimated", "experimental", "none"]
ParameterSource = Literal["verified", "library", "estimated", "experimental", "builtin_reference", "manual_override", "none"]
NextAction = Literal["ready_for_confirmation", "run_sample_test", "create_calibration_matrix", "fix_safety_violation"]
MatchType = Literal["exact", "alias", "nearest_thickness", "material_group_fallback", "none"]
MaterialMatchPolicy = Literal["exact_only", "nearest_engrave"]
SendPolicy = Literal["verified_only", "confirmed_material_record"]
RecommendationStatus = Literal["single_recommendation", "candidate_selection_required", "rejected_low_confidence", "rejected_cut_contour_unclear"]
ImagePreprocessSource = Literal["ai_suggested", "local_auto", "manual_override", "default"]


@dataclass(frozen=True)
class JobParams:
    material: str
    size_mm: float
    power: int
    feed_rate: int
    output_format: str = "gcode"
    laser_mode: str = "M4"
    safety_note: str = "起始建议，必须小功率试雕"
    mode: JobMode = "outline"
    pixel_size_mm: float = 0.2
    overscan_mm: float = 2.0
    work_area_width_mm: float = 100.0
    work_area_height_mm: float = 100.0
    trace_algorithm: TraceAlgorithm = "vector"
    vector_simplify_factor: float = 1.0
    thickness_mm: float | None = None
    thickness_source: ThicknessSource = "missing"
    task_type: TaskType | None = None
    task_type_source: TaskTypeSource = "missing"
    machine_profile_id: str = "yisu-v1-100x100"
    passes: int = 1
    confidence: ParameterConfidence = "none"
    parameter_source: ParameterSource = "none"
    match_type: MatchType = "none"
    matched_material: str | None = None
    matched_thickness_mm: float | None = None
    material_group: str | None = None
    material_match_policy: MaterialMatchPolicy = "exact_only"
    send_policy: SendPolicy = "verified_only"
    material_warnings: tuple[str, ...] = ()
    can_send: bool = False
    requires_sample_test: bool = True
    next_action: NextAction = "run_sample_test"
    message: str = "参数尚未验证，建议先做小样测试。"
    manual_power: bool = False
    manual_feed_rate: bool = False
    manual_pixel_size: bool = False
    arc_output: bool = False
    firmware_supports_arc: bool = False
    arc_tolerance_mm: float = 0.05
    dither_algorithm: DitherAlgorithm = "floyd_steinberg"
    threshold: int = -1
    manual_threshold: bool = False
    manual_dither: bool = False
    raster_scan_direction: RasterScanDirection = "auto"
    raster_output_strategy: RasterOutputStrategy = "auto"
    fill_strategy: FillStrategy = "none"
    fill_spacing_mm: float = 0.5
    width_mm: float | None = None
    height_mm: float | None = None
    lock_aspect_ratio: bool = True


@dataclass(frozen=True)
class ImagePreprocessPlan:
    source: ImagePreprocessSource
    invert: bool = False
    threshold: int | None = None
    brightness: float = 1.0
    contrast: float = 1.0
    cleanup_background: str = "none"
    grayscale_formula: str = "pillow_luminance"
    reason: str | None = None
    warnings: list[str] | None = None


@dataclass(frozen=True)
class ImagePreprocessResult:
    processed_path: Path
    source: ImagePreprocessSource
    invert: bool
    threshold: int | None
    brightness: float
    contrast: float
    cleanup_background: str
    grayscale_formula: str
    grayscale: bool
    binarized: bool
    dithered: bool
    reason: str | None = None
    warnings: list[str] | None = None


@dataclass(frozen=True)
class GcodeOutputStats:
    arc_enabled: bool
    arc_supported: bool
    arc_count: int = 0
    line_segment_count: int = 0
    fallback_segment_count: int = 0


@dataclass(frozen=True)
class RoutingMetrics:
    grayscale_levels: int
    foreground_ratio: float
    binary_ratio: float
    component_count: int
    edge_density: float = 0.0
    largest_component_ratio: float = 0.0


@dataclass(frozen=True)
class RoutingCandidate:
    mode: ResolvedJobMode
    reason: str
    confidence: float
    preview_path: Path | None = None
    gcode_path: Path | None = None
    selectable: bool = True
    safety_status: str = "preview_only"


@dataclass(frozen=True)
class RoutingResult:
    mode: ResolvedJobMode
    mode_source: Literal["auto", "manual"]
    reason: str
    metrics: RoutingMetrics | None
    confidence: float
    recommendation_status: RecommendationStatus = "single_recommendation"
    candidates: list[RoutingCandidate] | None = None
    confirmation_required: bool = True
    warnings: list[str] | None = None


@dataclass(frozen=True)
class TraceResult:
    paths: list[list[tuple[float, float]]]
    width_mm: float
    height_mm: float
    contour_count: int
    point_count: int
    raw_contour_count: int | None = None
    raw_point_count: int | None = None
    trace_algorithm: ResolvedTraceAlgorithm = "vector"
    fill_strategy: ResolvedFillStrategy = "none"
    fill_spacing_mm: float = 0.5
    fill_segment_count: int = 0
    fill_warnings: list[str] | None = None


@dataclass(frozen=True)
class RasterLineSegment:
    y_mm: float
    start_x_mm: float
    end_x_mm: float
    axis: Literal["horizontal", "vertical"] = "horizontal"
    end_y_mm: float = 0.0


@dataclass(frozen=True)
class RasterResult:
    segments: list[RasterLineSegment]
    width_mm: float
    height_mm: float
    grid_width: int
    grid_height: int
    pixel_size_mm: float
    overscan_mm: float
    scaled: bool
    scale_factor: float
    work_area_width_mm: float
    work_area_height_mm: float
    dither_algorithm: ResolvedDitherAlgorithm = "floyd_steinberg"
    threshold: int | None = None
    resize_strategy: str = "nearest"
    scan_direction_requested: RasterScanDirection = "auto"
    scan_direction: ResolvedRasterScanDirection = "horizontal"
    scan_direction_source: Literal["auto", "manual"] = "auto"
    scan_direction_reason: str = "default horizontal scan"
    horizontal_segment_count: int = 0
    vertical_segment_count: int = 0
    whitespace_skipping: bool = True
    run_length_compression: bool = True
    output_strategy_requested: RasterOutputStrategy = "auto"
    output_strategy: ResolvedRasterOutputStrategy = "segment"
    output_strategy_source: Literal["auto", "manual"] = "auto"
    output_strategy_reason: str = "default segment output"
    scanline_count: int = 0
    scanline_transition_count: int = 0
    snake_scan: bool = False
    quality_profile: dict[str, object] | None = None


@dataclass(frozen=True)
class OutputBundle:
    gcode_path: Path | None
    preview_path: Path | None
    summary_path: Path
    processed_preview_path: Path | None = None
