"""Offline-only hardware-sensitive G-code candidates.

All outputs are UNTESTED_ON_HARDWARE. This module must not be imported by
production tools, Web, workflow, generator, or raster_quality selection paths.
It never opens serial ports, Telnet, HTTP laser devices, cameras, or senders.
"""

from __future__ import annotations

import math
import tempfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Literal, Sequence

from core.ai_laser_gcode.gcode_writer import build_gcode_with_stats, build_raster_gcode
from core.ai_laser_gcode.models import JobParams, TraceResult
from core.ai_laser_gcode.preview import write_preview_png
from core.ai_laser_gcode.raster import MAX_RASTER_CELLS, rasterize_image
from core.ai_laser_gcode.safety import MAX_CONTOURS, MAX_POINTS, SafetyError, validate_job_params, validate_raster_result
from core.laser_time_estimate import estimate_gcode_time_seconds


UNTESTED_ON_HARDWARE = "UNTESTED_ON_HARDWARE"
TravelMode = Literal["rapid_g0", "controlled_g1_s0"]
ALLOWED_TRAVEL_MODES = frozenset({"rapid_g0", "controlled_g1_s0"})
_POINT_EPS = 1e-9
MAX_OFFLINE_COMMANDS = 1_000_000
MAX_OFFLINE_ESTIMATED_SECONDS = 24 * 60 * 60
_PROTECTED_EVIDENCE_DIRS = frozenset(
    {
        ".runtime",
        "laser_workflows",
        "lasergrbl_jobs",
        "lasergrbl_network_jobs",
        "lasergrbl_calibrations",
        "lasergrbl_text_tasks",
        "ai_laser_gcode_state",
    }
)


class OfflineHardwareCandidateError(ValueError):
    """Readable offline candidate failure (geometry, profile, or complexity)."""


@dataclass(frozen=True)
class OfflineCandidateProfile:
    simplify_tolerance_mm: float = 0.02
    contour_start_dwell_s: float = 0.15
    seam_overlap_mm: float = 0.3
    travel_mode: TravelMode = "rapid_g0"
    line_art_pixel_size_mm: float = 0.06

    def __post_init__(self) -> None:
        object.__setattr__(self, "simplify_tolerance_mm", _coerce_nonnegative_finite("simplify_tolerance_mm", self.simplify_tolerance_mm))
        object.__setattr__(self, "contour_start_dwell_s", _coerce_nonnegative_finite("contour_start_dwell_s", self.contour_start_dwell_s))
        object.__setattr__(self, "seam_overlap_mm", _coerce_nonnegative_finite("seam_overlap_mm", self.seam_overlap_mm))
        object.__setattr__(self, "line_art_pixel_size_mm", _coerce_positive_finite("line_art_pixel_size_mm", self.line_art_pixel_size_mm))
        if self.travel_mode not in ALLOWED_TRAVEL_MODES:
            raise OfflineHardwareCandidateError(
                f"travel_mode must be one of {sorted(ALLOWED_TRAVEL_MODES)}, got {self.travel_mode!r}"
            )


def simplify_closed_path(
    points: Sequence[tuple[float, float]],
    tolerance_mm: float = 0.02,
) -> list[tuple[float, float]]:
    """Ramer-Douglas-Peucker style closed-path simplification with absolute mm tolerance."""
    tolerance_mm = _coerce_nonnegative_finite("tolerance_mm", tolerance_mm)
    cleaned = _clean_points(points)
    if _distinct_point_count(cleaned) < 3:
        raise OfflineHardwareCandidateError("Closed path simplification needs at least 3 distinct points")
    closed = _ensure_closed(cleaned)
    ring = closed[:-1]
    if len(ring) < 3:
        raise OfflineHardwareCandidateError("Closed path simplification needs at least 3 valid points after close")
    if tolerance_mm == 0 or len(ring) <= 3:
        simplified_ring = ring
    else:
        # Keep endpoints by simplifying the open ring with fixed start/end, then re-close.
        # For a closed loop, rotate so first point is retained and RDP can drop collinear middles.
        simplified_ring = _rdp(ring + [ring[0]], tolerance_mm)[:-1]
        if _distinct_point_count(simplified_ring) < 3:
            simplified_ring = ring
    result = _ensure_closed(simplified_ring)
    if len(result) < 4 or _distinct_point_count(result) < 3:
        raise OfflineHardwareCandidateError("Simplified closed path must keep at least 3 valid points")
    return result


def apply_seam_overlap(
    points: Sequence[tuple[float, float]],
    seam_overlap_mm: float = 0.3,
) -> list[tuple[float, float]]:
    """Append at most seam_overlap_mm along the first segment after closing. Never past p1.

    Consecutive duplicate points are preserved long enough to detect a zero-length
    first segment and skip the seam safely (no divide-by-zero, no illegal points).
    """
    seam_overlap_mm = _coerce_nonnegative_finite("seam_overlap_mm", seam_overlap_mm)
    validated = _validate_point_list(points)
    if _distinct_point_count(validated) < 3:
        raise OfflineHardwareCandidateError("Seam overlap needs at least 3 valid points")
    closed = _ensure_closed(validated)
    if seam_overlap_mm <= 0:
        return _dedupe_consecutive(_ensure_closed(_dedupe_consecutive(validated)))
    p0 = closed[0]
    p1 = closed[1]
    dx = p1[0] - p0[0]
    dy = p1[1] - p0[1]
    length = math.hypot(dx, dy)
    if not math.isfinite(length) or length <= _POINT_EPS:
        # Zero-length / non-finite first segment: skip seam, return closed deduped path.
        return _ensure_closed(_dedupe_consecutive(validated))
    distance = min(seam_overlap_mm, length)
    seam_point = (p0[0] + dx * (distance / length), p0[1] + dy * (distance / length))
    # Never past p1 (clamped by min above). Append once after closed start.
    body = _ensure_closed(_dedupe_consecutive(validated))
    if _points_equal(seam_point, body[0]):
        return body
    return body + [seam_point]


def prepare_offline_paths(
    paths: Sequence[Sequence[tuple[float, float]]],
    profile: OfflineCandidateProfile | None = None,
) -> list[list[tuple[float, float]]]:
    profile = profile or OfflineCandidateProfile()
    if len(paths) > MAX_CONTOURS:
        raise OfflineHardwareCandidateError(f"Offline outline has too many contours: {len(paths)} > {MAX_CONTOURS}")
    raw_point_count = sum(len(path) for path in paths)
    if raw_point_count > MAX_POINTS:
        raise OfflineHardwareCandidateError(f"Offline outline has too many input points: {raw_point_count} > {MAX_POINTS}")
    prepared: list[list[tuple[float, float]]] = []
    for path in paths:
        simplified = simplify_closed_path(path, tolerance_mm=profile.simplify_tolerance_mm)
        seamed = apply_seam_overlap(simplified, seam_overlap_mm=profile.seam_overlap_mm)
        prepared.append(seamed)
    return prepared


def build_offline_outline_candidate(
    *,
    paths: Sequence[Sequence[tuple[float, float]]],
    params: JobParams,
    profile: OfflineCandidateProfile | None = None,
    height_mm: float | None = None,
    width_mm: float | None = None,
    output_dir: Path | str | None = None,
) -> dict:
    """Build offline outline candidate G-code with dwell / seam / travel variants."""
    profile = profile or OfflineCandidateProfile()
    evidence_dir = _validate_evidence_dir(output_dir)
    _validate_outline_params(params)
    prepared = prepare_offline_paths(paths, profile)
    if not prepared:
        raise OfflineHardwareCandidateError("No outline paths provided for offline candidate")
    width = float(width_mm if width_mm is not None else max((max(p[0] for p in path) for path in prepared), default=0.0))
    height = float(height_mm if height_mm is not None else max((max(p[1] for p in path) for path in prepared), default=0.0))
    if width <= 0 or height <= 0:
        # Fall back to work area for pure origin-based paths.
        width = width if width > 0 else float(params.work_area_width_mm)
        height = height if height > 0 else float(params.work_area_height_mm)
    _validate_outline_bounds(prepared, width, height, params)
    if len(prepared) > MAX_CONTOURS:
        raise OfflineHardwareCandidateError(f"Offline outline has too many contours: {len(prepared)} > {MAX_CONTOURS}")
    point_count = sum(len(path) for path in prepared)
    if point_count > MAX_POINTS:
        raise OfflineHardwareCandidateError(f"Offline outline has too many points: {point_count} > {MAX_POINTS}")
    trace = TraceResult(
        paths=prepared,
        width_mm=width,
        height_mm=height,
        contour_count=len(prepared),
        point_count=point_count,
    )
    # Seam already applied in geometry: disable writer auto-close to avoid double burn.
    auto_close = profile.seam_overlap_mm <= 0
    gcode, output_stats = build_gcode_with_stats(
        params,
        trace,
        auto_close_open_paths=auto_close,
        contour_start_dwell_s=profile.contour_start_dwell_s,
        travel_mode=profile.travel_mode,
    )
    gcode = f"; {UNTESTED_ON_HARDWARE}\n" + gcode
    estimate = estimate_gcode_time_seconds(gcode) or {}
    commands = [line for line in gcode.splitlines() if line and not line.startswith(";")]
    travel_length = _estimate_travel_length_mm(gcode)
    _validate_offline_limits(len(commands), estimate)
    if evidence_dir is None:
        evidence_dir = Path(tempfile.mkdtemp(prefix="ai_laser_offline_outline_"))
    evidence_dir.mkdir(parents=True, exist_ok=True)
    preview_path = evidence_dir / f"offline_outline_{UNTESTED_ON_HARDWARE}_preview.png"
    write_preview_png(trace, preview_path)
    gcode_path = evidence_dir / f"offline_outline_{UNTESTED_ON_HARDWARE}.nc"
    gcode_path.write_text(gcode, encoding="utf-8")
    return {
        "status": UNTESTED_ON_HARDWARE,
        "gcode": gcode,
        "gcode_path": str(gcode_path),
        "preview_path": str(preview_path),
        "profile": asdict(profile),
        "stats": {
            "command_count": len(commands),
            "byte_count": len(gcode.encode("utf-8")),
            "travel_length_mm": travel_length,
            "estimated_seconds": float(estimate.get("estimated_seconds") or 0.0),
            "line_segment_count": output_stats.line_segment_count,
            "arc_count": output_stats.arc_count,
            "contour_count": len(prepared),
            "point_count": point_count,
        },
        "bounds": {
            "min_x_mm": min(point[0] for path in prepared for point in path),
            "min_y_mm": min(point[1] for path in prepared for point in path),
            "max_x_mm": max(point[0] for path in prepared for point in path),
            "max_y_mm": max(point[1] for path in prepared for point in path),
            "width_mm": width,
            "height_mm": height,
        },
        "note": "Offline candidate only; do not send, store in workflow, or claim hardware quality.",
    }


def build_offline_outline_comparison(
    *,
    paths: Sequence[Sequence[tuple[float, float]]],
    params: JobParams,
    profile: OfflineCandidateProfile | None = None,
    height_mm: float | None = None,
    width_mm: float | None = None,
    output_dir: Path | str | None = None,
) -> dict:
    """Compare rapid G0 and controlled G1 S0 travel for the same offline outline."""
    profile = profile or OfflineCandidateProfile()
    evidence_dir = _validate_evidence_dir(output_dir)
    if evidence_dir is None:
        evidence_dir = Path(tempfile.mkdtemp(prefix="ai_laser_offline_outline_comparison_"))
    rapid = build_offline_outline_candidate(
        paths=paths,
        params=params,
        profile=replace(profile, travel_mode="rapid_g0"),
        height_mm=height_mm,
        width_mm=width_mm,
        output_dir=evidence_dir / "rapid_g0",
    )
    controlled = build_offline_outline_candidate(
        paths=paths,
        params=params,
        profile=replace(profile, travel_mode="controlled_g1_s0"),
        height_mm=height_mm,
        width_mm=width_mm,
        output_dir=evidence_dir / "controlled_g1_s0",
    )
    return {
        "status": UNTESTED_ON_HARDWARE,
        "rapid_g0": rapid,
        "controlled_g1_s0": controlled,
        "delta": {
            "command_count": controlled["stats"]["command_count"] - rapid["stats"]["command_count"],
            "byte_count": controlled["stats"]["byte_count"] - rapid["stats"]["byte_count"],
            "travel_length_mm": controlled["stats"]["travel_length_mm"] - rapid["stats"]["travel_length_mm"],
            "estimated_seconds": controlled["stats"]["estimated_seconds"] - rapid["stats"]["estimated_seconds"],
        },
        "note": "Offline travel comparison only; UNTESTED_ON_HARDWARE; do not send.",
    }


def build_offline_pixel_size_comparison(
    *,
    image_path: Path | str,
    params: JobParams,
    baseline_pixel_size_mm: float = 0.1,
    candidate_pixel_size_mm: float | None = None,
    output_dir: Path | str | None = None,
    profile: OfflineCandidateProfile | None = None,
    max_command_count: int = MAX_OFFLINE_COMMANDS,
    max_estimated_seconds: float = MAX_OFFLINE_ESTIMATED_SECONDS,
) -> dict:
    """Compare explicit baseline vs offline fine pixel size. Never mutates public quality lists."""
    profile = profile or OfflineCandidateProfile()
    candidate_pixel = candidate_pixel_size_mm if candidate_pixel_size_mm is not None else profile.line_art_pixel_size_mm
    baseline_pixel_size_mm = _coerce_positive_finite("baseline_pixel_size_mm", baseline_pixel_size_mm)
    candidate_pixel = _coerce_positive_finite("candidate_pixel_size_mm", candidate_pixel)
    _validate_positive_int("max_command_count", max_command_count)
    max_estimated_seconds = _coerce_positive_finite("max_estimated_seconds", max_estimated_seconds)
    image_path = Path(image_path)
    if not image_path.exists():
        raise OfflineHardwareCandidateError(f"Image not found for offline comparison: {image_path}")
    evidence_dir = _validate_evidence_dir(output_dir)
    if evidence_dir is None:
        evidence_dir = Path(tempfile.mkdtemp(prefix="ai_laser_offline_raster_"))

    baseline = _build_offline_raster_variant(
        image_path=image_path,
        params=params,
        pixel_size_mm=baseline_pixel_size_mm,
        label="baseline",
        output_dir=evidence_dir,
        max_command_count=max_command_count,
        max_estimated_seconds=max_estimated_seconds,
    )
    candidate = _build_offline_raster_variant(
        image_path=image_path,
        params=params,
        pixel_size_mm=candidate_pixel,
        label="candidate",
        output_dir=evidence_dir,
        max_command_count=max_command_count,
        max_estimated_seconds=max_estimated_seconds,
    )
    return {
        "status": UNTESTED_ON_HARDWARE,
        "baseline": baseline,
        "candidate": candidate,
        "profile": asdict(profile),
        "note": "Offline raster comparison only; UNTESTED_ON_HARDWARE; not for confirm_send.",
    }


def _build_offline_raster_variant(
    *,
    image_path: Path,
    params: JobParams,
    pixel_size_mm: float,
    label: str,
    output_dir: Path | None,
    max_command_count: int,
    max_estimated_seconds: float,
) -> dict:
    working = replace(
        params,
        mode="raster",
        pixel_size_mm=float(pixel_size_mm),
        manual_pixel_size=True,
    )
    try:
        validate_job_params(working)
    except SafetyError as error:
        raise OfflineHardwareCandidateError(str(error)) from error

    threshold = getattr(working, "threshold", -1)
    if threshold is not None and int(threshold) >= 0 and working.dither_algorithm in {
        "floyd_steinberg",
        "atkinson",
        "sierra_lite",
    }:
        raise OfflineHardwareCandidateError(
            "Explicit threshold conflicts with error-diffusion dither_algorithm; use dither_algorithm='threshold'"
        )

    # Explicit complexity pre-check: do not silently shrink physical size.
    width_mm = float(working.width_mm or working.size_mm)
    height_mm = float(working.height_mm or working.size_mm)
    if width_mm > 0 and height_mm > 0 and pixel_size_mm > 0:
        approx_cells = math.ceil(width_mm / pixel_size_mm) * math.ceil(height_mm / pixel_size_mm)
        if approx_cells > MAX_RASTER_CELLS:
            raise OfflineHardwareCandidateError(
                f"Raster output is too large for offline {label} at pixel_size_mm={pixel_size_mm}: "
                f"approx {approx_cells} cells exceeds MAX_RASTER_CELLS={MAX_RASTER_CELLS}"
            )

    try:
        raster = rasterize_image(image_path, working)
        validate_raster_result(raster)
        if raster.scaled:
            raise OfflineHardwareCandidateError(
                f"Offline {label} would scale the requested physical size by {raster.scale_factor:g}; "
                "reduce the requested size explicitly instead of silently shrinking it"
            )
        gcode = f"; {UNTESTED_ON_HARDWARE}\n" + build_raster_gcode(working, raster)
    except OfflineHardwareCandidateError:
        raise
    except SafetyError as error:
        raise OfflineHardwareCandidateError(str(error)) from error
    except ValueError as error:
        raise OfflineHardwareCandidateError(str(error)) from error

    estimate = estimate_gcode_time_seconds(gcode) or {}
    commands = [line for line in gcode.splitlines() if line and not line.startswith(";")]
    _validate_offline_limits(len(commands), estimate, max_command_count, max_estimated_seconds)
    gcode_path = None
    preview_path = None
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        gcode_path = output_dir / f"offline_{label}_px{pixel_size_mm:.3f}_{UNTESTED_ON_HARDWARE}.nc"
        gcode_path.write_text(
            f"; offline_label={label}\n; pixel_size_mm={pixel_size_mm}\n" + gcode,
            encoding="utf-8",
        )
        preview_path = output_dir / f"offline_{label}_px{pixel_size_mm:.3f}_{UNTESTED_ON_HARDWARE}_preview.png"
        write_preview_png(raster, preview_path)

    return {
        "label": label,
        "status": UNTESTED_ON_HARDWARE,
        "pixel_size_mm": float(pixel_size_mm),
        "gcode": gcode,
        "gcode_path": str(gcode_path) if gcode_path is not None else None,
        "preview_path": str(preview_path) if preview_path is not None else None,
        "grid_width": raster.grid_width,
        "grid_height": raster.grid_height,
        "grid_cells": raster.grid_width * raster.grid_height,
        "width_mm": raster.width_mm,
        "height_mm": raster.height_mm,
        "bounds": {
            "min_x_mm": 0.0,
            "min_y_mm": 0.0,
            "max_x_mm": float(raster.width_mm),
            "max_y_mm": float(raster.height_mm),
        },
        "complexity": (
            max(raster.scanline_transition_count, raster.scanline_count)
            if raster.output_strategy == "scanline"
            else len(raster.segments)
        ),
        "segment_count": len(raster.segments),
        "command_count": len(commands),
        "byte_count": len(gcode.encode("utf-8")),
        "estimated_seconds": float(estimate.get("estimated_seconds") or 0.0),
        "time_estimate": estimate,
        "scaled": raster.scaled,
        "scale_factor": raster.scale_factor,
    }


def _coerce_nonnegative_finite(name: str, value: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise OfflineHardwareCandidateError(f"{name} must be a finite number") from error
    if not math.isfinite(number) or number < 0:
        raise OfflineHardwareCandidateError(f"{name} must be a finite non-negative number, got {value!r}")
    return number


def _coerce_positive_finite(name: str, value: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise OfflineHardwareCandidateError(f"{name} must be a finite number") from error
    if not math.isfinite(number) or number <= 0:
        raise OfflineHardwareCandidateError(f"{name} must be a finite positive number, got {value!r}")
    return number


def _validate_positive_finite(name: str, value: float) -> None:
    _coerce_positive_finite(name, value)


def _validate_positive_int(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise OfflineHardwareCandidateError(f"{name} must be a positive integer, got {value!r}")


def _validate_outline_params(params: JobParams) -> None:
    try:
        validate_job_params(params)
    except SafetyError as error:
        raise OfflineHardwareCandidateError(str(error)) from error
    if params.mode != "outline":
        raise OfflineHardwareCandidateError("Offline outline candidates require params.mode='outline'")


def _validate_outline_bounds(
    paths: Sequence[Sequence[tuple[float, float]]],
    width: float,
    height: float,
    params: JobParams,
) -> None:
    if not math.isfinite(width) or not math.isfinite(height) or width <= 0 or height <= 0:
        raise OfflineHardwareCandidateError("Offline outline bounds must be finite and positive")
    if width > params.work_area_width_mm or height > params.work_area_height_mm:
        raise OfflineHardwareCandidateError("Offline outline bounds exceed the configured hardware work area")
    for path in paths:
        for x_coord, y_coord in path:
            if x_coord < -_POINT_EPS or y_coord < -_POINT_EPS or x_coord > width + _POINT_EPS or y_coord > height + _POINT_EPS:
                raise OfflineHardwareCandidateError("Offline outline point falls outside the declared bounds")


def _validate_evidence_dir(output_dir: Path | str | None) -> Path | None:
    if output_dir is None:
        return None
    resolved = Path(output_dir).expanduser().resolve()
    if any(part.lower() in _PROTECTED_EVIDENCE_DIRS for part in resolved.parts):
        raise OfflineHardwareCandidateError("Offline evidence must not write into a production runtime directory")
    temp_root = Path(tempfile.gettempdir()).resolve()
    task_evidence_root = (
        Path(__file__).resolve().parents[2]
        / ".trellis"
        / "tasks"
        / "07-19-laser-gcode-hardware-sensitive"
        / "research"
        / "offline-evidence"
    ).resolve()
    if not (resolved == temp_root or resolved.is_relative_to(temp_root) or resolved.is_relative_to(task_evidence_root)):
        raise OfflineHardwareCandidateError(
            "Offline evidence must be written under a temporary directory or this task's research/offline-evidence directory"
        )
    return resolved


def _validate_offline_limits(
    command_count: int,
    estimate: dict,
    max_command_count: int = MAX_OFFLINE_COMMANDS,
    max_estimated_seconds: float = MAX_OFFLINE_ESTIMATED_SECONDS,
) -> None:
    estimated_seconds = float(estimate.get("estimated_seconds") or 0.0)
    if command_count > max_command_count:
        raise OfflineHardwareCandidateError(
            f"Offline candidate has too many commands: {command_count} > {max_command_count}"
        )
    if estimated_seconds > max_estimated_seconds:
        raise OfflineHardwareCandidateError(
            f"Offline candidate estimate is too long: {estimated_seconds:g}s > {max_estimated_seconds:g}s"
        )


def _validate_point_list(points: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    if not points:
        raise OfflineHardwareCandidateError("Path is empty")
    validated: list[tuple[float, float]] = []
    for point in points:
        if len(point) != 2:
            raise OfflineHardwareCandidateError("Each point must be an (x, y) pair")
        x_coord, y_coord = float(point[0]), float(point[1])
        if not math.isfinite(x_coord) or not math.isfinite(y_coord):
            raise OfflineHardwareCandidateError("Path contains non-finite coordinates")
        validated.append((x_coord, y_coord))
    return validated


def _dedupe_consecutive(points: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    cleaned: list[tuple[float, float]] = []
    for candidate in points:
        if cleaned and _points_equal(cleaned[-1], candidate):
            continue
        cleaned.append(candidate)
    return cleaned


def _clean_points(points: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    return _dedupe_consecutive(_validate_point_list(points))


def _distinct_point_count(points: Sequence[tuple[float, float]]) -> int:
    distinct: list[tuple[float, float]] = []
    for point in points:
        if not any(_points_equal(point, existing) for existing in distinct):
            distinct.append(point)
    return len(distinct)


def _ensure_closed(points: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    path = list(points)
    if not path:
        raise OfflineHardwareCandidateError("Path is empty")
    if not _points_equal(path[0], path[-1]):
        path = path + [path[0]]
    return path


def _points_equal(first: tuple[float, float], second: tuple[float, float]) -> bool:
    return abs(first[0] - second[0]) <= _POINT_EPS and abs(first[1] - second[1]) <= _POINT_EPS


def _rdp(points: list[tuple[float, float]], tolerance: float) -> list[tuple[float, float]]:
    if len(points) <= 2:
        return points
    start = points[0]
    end = points[-1]
    max_distance = -1.0
    split_index = 0
    for index, point in enumerate(points[1:-1], start=1):
        distance = _perpendicular_distance(point, start, end)
        if distance > max_distance:
            max_distance = distance
            split_index = index
    if max_distance <= tolerance:
        return [start, end]
    first = _rdp(points[: split_index + 1], tolerance)
    second = _rdp(points[split_index:], tolerance)
    return first[:-1] + second


def _perpendicular_distance(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    if _points_equal(start, end):
        return math.hypot(point[0] - start[0], point[1] - start[1])
    numerator = abs(
        (end[1] - start[1]) * point[0]
        - (end[0] - start[0]) * point[1]
        + end[0] * start[1]
        - end[1] * start[0]
    )
    denominator = math.hypot(end[1] - start[1], end[0] - start[0])
    return numerator / denominator if denominator else 0.0


def _estimate_travel_length_mm(gcode: str) -> float:
    x_coord = 0.0
    y_coord = 0.0
    travel = 0.0
    for raw in gcode.splitlines():
        line = raw.strip().upper()
        if not (line.startswith("G0") or (line.startswith("G1") and "S0" in line and "S0" == _extract_s_token(line))):
            # Only count rapid or explicitly zero-power moves as travel for offline stats.
            if line.startswith("G0"):
                pass
            else:
                # Update position for powered moves without counting as travel.
                if line.startswith("G1") or line.startswith("G0"):
                    x_coord, y_coord = _update_xy(line, x_coord, y_coord)
                continue
        if line.startswith("G0") or (line.startswith("G1") and _extract_s_token(line) == "S0"):
            next_x, next_y = _update_xy(line, x_coord, y_coord)
            travel += math.hypot(next_x - x_coord, next_y - y_coord)
            x_coord, next_y_hold = next_x, next_y
            y_coord = next_y_hold
    return travel


def _extract_s_token(line: str) -> str | None:
    for token in line.split():
        if token.startswith("S") and len(token) > 1:
            return token
    return None


def _update_xy(line: str, x_coord: float, y_coord: float) -> tuple[float, float]:
    next_x, next_y = x_coord, y_coord
    for token in line.split():
        if token.startswith("X"):
            try:
                next_x = float(token[1:])
            except ValueError:
                pass
        elif token.startswith("Y"):
            try:
                next_y = float(token[1:])
            except ValueError:
                pass
    return next_x, next_y
