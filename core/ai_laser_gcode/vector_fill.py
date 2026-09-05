from dataclasses import replace
from math import isfinite

from core.ai_laser_gcode.models import JobParams, TraceResult


POINT_TOLERANCE = 1e-9


def apply_vector_fill(trace: TraceResult, params: JobParams) -> TraceResult:
    if params.fill_strategy == "none":
        return replace(trace, fill_strategy="none", fill_spacing_mm=params.fill_spacing_mm, fill_segment_count=0, fill_warnings=[])
    if params.fill_strategy == "auto":
        return replace(trace, fill_strategy="none", fill_spacing_mm=params.fill_spacing_mm, fill_segment_count=0, fill_warnings=["fill_strategy=auto resolved to none for MVP safety."])

    fill_paths: list[list[tuple[float, float]]] = []
    warnings: list[str] = []
    for index, path in enumerate(trace.paths):
        if not _is_simple_closed_polygon(path):
            warnings.append(f"Path {index} is not a supported simple closed polygon; skipped fill.")
            continue
        segments = _horizontal_hatch_segments(path, params.fill_spacing_mm)
        if segments is None:
            warnings.append(f"Path {index} has unsupported scanline intersections; skipped fill.")
            continue
        if params.fill_strategy == "hatch":
            fill_paths.extend([[start, end] for start, end in segments])
        else:
            fill_paths.extend(_zigzag_paths(segments))

    segment_count = sum(max(0, len(path) - 1) for path in fill_paths)
    paths = [list(path) for path in trace.paths] + fill_paths
    return TraceResult(
        paths=paths,
        width_mm=trace.width_mm,
        height_mm=trace.height_mm,
        contour_count=len(paths),
        point_count=sum(len(path) for path in paths),
        raw_contour_count=trace.raw_contour_count,
        raw_point_count=trace.raw_point_count,
        trace_algorithm=trace.trace_algorithm,
        fill_strategy=params.fill_strategy,
        fill_spacing_mm=params.fill_spacing_mm,
        fill_segment_count=segment_count,
        fill_warnings=warnings,
    )


def _horizontal_hatch_segments(path: list[tuple[float, float]], spacing_mm: float) -> list[tuple[tuple[float, float], tuple[float, float]]] | None:
    polygon = path[:-1]
    min_y = min(point[1] for point in polygon)
    max_y = max(point[1] for point in polygon)
    segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
    y_coord = min_y + spacing_mm
    while y_coord < max_y - POINT_TOLERANCE:
        intersections = _scanline_intersections(polygon, y_coord)
        if len(intersections) != 2:
            return None
        start_x, end_x = intersections
        if end_x - start_x > POINT_TOLERANCE:
            segments.append(((start_x, y_coord), (end_x, y_coord)))
        y_coord += spacing_mm
    return segments


def _scanline_intersections(polygon: list[tuple[float, float]], y_coord: float) -> list[float]:
    intersections: list[float] = []
    for start, end in zip(polygon, polygon[1:] + polygon[:1]):
        start_x, start_y = start
        end_x, end_y = end
        if abs(start_y - end_y) <= POINT_TOLERANCE:
            continue
        lower_y = min(start_y, end_y)
        upper_y = max(start_y, end_y)
        if lower_y <= y_coord < upper_y:
            ratio = (y_coord - start_y) / (end_y - start_y)
            intersections.append(start_x + ratio * (end_x - start_x))
    intersections.sort()
    return intersections


def _zigzag_paths(segments: list[tuple[tuple[float, float], tuple[float, float]]]) -> list[list[tuple[float, float]]]:
    if not segments:
        return []
    path: list[tuple[float, float]] = []
    for index, (start, end) in enumerate(segments):
        if index % 2 == 0:
            path.extend([start, end])
        else:
            path.extend([end, start])
    return [path]


def _is_simple_closed_polygon(path: list[tuple[float, float]]) -> bool:
    if len(path) < 4 or _distance_squared(path[0], path[-1]) > POINT_TOLERANCE:
        return False
    if any(not isfinite(x_coord) or not isfinite(y_coord) for x_coord, y_coord in path):
        return False
    polygon = path[:-1]
    if len(set(polygon)) < 3:
        return False
    edge_count = len(polygon)
    for first_index in range(edge_count):
        first_start = polygon[first_index]
        first_end = polygon[(first_index + 1) % edge_count]
        for second_index in range(first_index + 1, edge_count):
            if second_index in {first_index, (first_index + 1) % edge_count}:
                continue
            if first_index == 0 and second_index == edge_count - 1:
                continue
            second_start = polygon[second_index]
            second_end = polygon[(second_index + 1) % edge_count]
            if _segments_intersect(first_start, first_end, second_start, second_end):
                return False
    return True


def _segments_intersect(a_start: tuple[float, float], a_end: tuple[float, float], b_start: tuple[float, float], b_end: tuple[float, float]) -> bool:
    first = _orientation(a_start, a_end, b_start)
    second = _orientation(a_start, a_end, b_end)
    third = _orientation(b_start, b_end, a_start)
    fourth = _orientation(b_start, b_end, a_end)
    return first * second < 0 and third * fourth < 0


def _orientation(first: tuple[float, float], second: tuple[float, float], third: tuple[float, float]) -> float:
    return (second[0] - first[0]) * (third[1] - first[1]) - (second[1] - first[1]) * (third[0] - first[0])


def _distance_squared(first: tuple[float, float], second: tuple[float, float]) -> float:
    return (first[0] - second[0]) ** 2 + (first[1] - second[1]) ** 2
