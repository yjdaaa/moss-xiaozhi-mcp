from dataclasses import dataclass
from math import hypot, isfinite

from core.ai_laser_gcode.models import TraceResult
from core.ai_laser_gcode.safety import SafetyError


MINIMUM_TRAVEL_GAIN = 0.05


@dataclass(frozen=True)
class PathOptimizationStats:
    strategy: str
    travel_distance_before: float
    travel_distance_after: float
    travel_reduction_ratio: float
    path_count: int


@dataclass(frozen=True)
class PathOptimizationResult:
    trace: TraceResult
    stats: PathOptimizationStats


def optimize_trace_paths(trace: TraceResult, size_mm: float) -> PathOptimizationResult:
    if not trace.paths:
        raise SafetyError("Path optimization requires at least one usable path")
    _validate_paths(trace.paths)
    direction_paths = _direction_order(trace.paths, size_mm)
    nearest_paths = _nearest_neighbor_order(trace.paths)
    travel_before = calculate_travel_distance(trace.paths)
    direction_travel = calculate_travel_distance(direction_paths)
    nearest_travel = calculate_travel_distance(nearest_paths)
    if direction_travel <= 0:
        strategy = "direction_order"
        optimized_paths = direction_paths
        travel_after = direction_travel
    else:
        reduction_against_direction = (direction_travel - nearest_travel) / direction_travel
        if reduction_against_direction >= MINIMUM_TRAVEL_GAIN:
            strategy = "nearest_neighbor"
            optimized_paths = nearest_paths
            travel_after = nearest_travel
        else:
            strategy = "direction_order"
            optimized_paths = direction_paths
            travel_after = direction_travel
    _ensure_same_points(trace.paths, optimized_paths)
    reduction_ratio = 0.0 if travel_before <= 0 else (travel_before - travel_after) / travel_before
    optimized_trace = TraceResult(
        paths=optimized_paths,
        width_mm=trace.width_mm,
        height_mm=trace.height_mm,
        contour_count=trace.contour_count,
        point_count=trace.point_count,
        raw_contour_count=trace.raw_contour_count,
        raw_point_count=trace.raw_point_count,
        trace_algorithm=trace.trace_algorithm,
        fill_strategy=trace.fill_strategy,
        fill_spacing_mm=trace.fill_spacing_mm,
        fill_segment_count=trace.fill_segment_count,
        fill_warnings=trace.fill_warnings,
    )
    return PathOptimizationResult(
        trace=optimized_trace,
        stats=PathOptimizationStats(
            strategy=strategy,
            travel_distance_before=travel_before,
            travel_distance_after=travel_after,
            travel_reduction_ratio=reduction_ratio,
            path_count=len(optimized_paths),
        ),
    )


def calculate_travel_distance(paths: list[list[tuple[float, float]]]) -> float:
    distance = 0.0
    current = (0.0, 0.0)
    for path in paths:
        if not path:
            continue
        start = path[0]
        distance += _distance(current, start)
        current = _writer_end_point(path)
    if not isfinite(distance):
        raise SafetyError("Path optimization produced an invalid travel distance")
    return distance


def _direction_order(paths: list[list[tuple[float, float]]], size_mm: float) -> list[list[tuple[float, float]]]:
    tolerance = _row_tolerance(paths, size_mm)
    indexed = [(_bounds(path), path) for path in paths]
    indexed.sort(key=lambda item: (_center_y(item[0]), _center_x(item[0])))
    rows: list[list[tuple[tuple[float, float, float, float], list[tuple[float, float]]]]] = []
    previous_center_y: float | None = None
    for bounds, path in indexed:
        center_y = _center_y(bounds)
        if previous_center_y is None or abs(center_y - previous_center_y) > tolerance:
            rows.append([])
        rows[-1].append((bounds, path))
        previous_center_y = center_y
    ordered: list[list[tuple[float, float]]] = []
    for row in rows:
        row.sort(key=lambda item: _center_x(item[0]))
        ordered.extend(list(path) for _, path in row)
    return ordered


def _nearest_neighbor_order(paths: list[list[tuple[float, float]]]) -> list[list[tuple[float, float]]]:
    remaining = [list(path) for path in paths]
    ordered: list[list[tuple[float, float]]] = []
    current = (0.0, 0.0)
    while remaining:
        best_index = 0
        best_path = remaining[0]
        best_distance = float("inf")
        for index, path in enumerate(remaining):
            for candidate in _path_orientations(path):
                candidate_distance = _distance(current, candidate[0])
                if candidate_distance < best_distance:
                    best_distance = candidate_distance
                    best_index = index
                    best_path = candidate
        ordered.append(best_path)
        current = _writer_end_point(best_path)
        remaining.pop(best_index)
    return ordered


def _path_orientations(path: list[tuple[float, float]]) -> tuple[list[tuple[float, float]], ...]:
    orientations: list[list[tuple[float, float]]] = []
    for candidate in (path, list(reversed(path))):
        if _is_closed_path(candidate):
            unique_points = candidate[:-1]
            for index in range(len(unique_points)):
                rotated = unique_points[index:] + unique_points[:index]
                orientations.append(rotated + [rotated[0]])
        else:
            orientations.append(candidate)
    return tuple(orientations)


def _is_closed_path(path: list[tuple[float, float]]) -> bool:
    if len(path) < 3:
        return False
    return _distance(path[0], path[-1]) <= 1e-9


def _row_tolerance(paths: list[list[tuple[float, float]]], size_mm: float) -> float:
    heights = [_bounds(path)[3] - _bounds(path)[1] for path in paths]
    average_height = sum(heights) / len(heights) if heights else 0.0
    return max(size_mm * 0.05, average_height, 0.001)


def _bounds(path: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    x_values = [point[0] for point in path]
    y_values = [point[1] for point in path]
    return min(x_values), min(y_values), max(x_values), max(y_values)


def _center_x(bounds: tuple[float, float, float, float]) -> float:
    return (bounds[0] + bounds[2]) / 2


def _center_y(bounds: tuple[float, float, float, float]) -> float:
    return (bounds[1] + bounds[3]) / 2


def _distance(first: tuple[float, float], second: tuple[float, float]) -> float:
    return hypot(first[0] - second[0], first[1] - second[1])


def _writer_end_point(path: list[tuple[float, float]]) -> tuple[float, float]:
    if len(path) > 2:
        return path[0]
    return path[-1]


def _validate_paths(paths: list[list[tuple[float, float]]]) -> None:
    if any(not path for path in paths):
        raise SafetyError("Path optimization cannot process empty paths")
    for path in paths:
        for x_coord, y_coord in path:
            if not isfinite(x_coord) or not isfinite(y_coord):
                raise SafetyError("Path optimization cannot process non-finite coordinates")


def _ensure_same_points(original_paths: list[list[tuple[float, float]]], optimized_paths: list[list[tuple[float, float]]]) -> None:
    original_points = sorted(point for path in original_paths for point in _geometry_points(path))
    optimized_points = sorted(point for path in optimized_paths for point in _geometry_points(path))
    if original_points != optimized_points:
        raise SafetyError("Path optimization changed path geometry; refusing to generate G-code")


def _geometry_points(path: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if _is_closed_path(path):
        return path[:-1]
    return path
