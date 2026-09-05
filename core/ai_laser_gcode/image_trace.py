from pathlib import Path
from math import sqrt

from core.ai_laser_gcode.dimensions import resolve_locked_box_dimensions
from core.ai_laser_gcode.models import JobParams, ResolvedTraceAlgorithm, TraceResult


TRACE_OUTPUT_POINT_BUDGET = 2500
VECTOR_TRACE_ALGORITHM: ResolvedTraceAlgorithm = "vector"


def trace_image(image_path: Path, params: JobParams) -> TraceResult:
    trace_algorithm = resolve_trace_algorithm(params.trace_algorithm)
    pixels, width, height = _read_image_pixels(image_path)
    target_width_mm, target_height_mm, scale_x, scale_y, size_metric = _target_dimensions(width, height, params)
    foreground = {(x, y) for y, row in enumerate(pixels) for x, value in enumerate(row) if value < 180}
    if not foreground:
        return TraceResult(paths=[], width_mm=target_width_mm, height_mm=target_height_mm, contour_count=0, point_count=0, trace_algorithm=trace_algorithm)
    components = [component for component in _connected_components(foreground) if len(component) >= 4]
    components.sort(key=len, reverse=True)
    return _trace_vector(components, width, height, scale_x, scale_y, size_metric, trace_algorithm, params.vector_simplify_factor)


def resolve_trace_algorithm(requested_algorithm: str) -> ResolvedTraceAlgorithm:
    if requested_algorithm == VECTOR_TRACE_ALGORITHM:
        return "vector"
    raise ValueError(f"Unsupported trace_algorithm '{requested_algorithm}'; use 'vector'")


def _ensure_closed_path(path: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if path and not _points_equal(path[0], path[-1]):
        return path + [path[0]]
    return path


def _target_dimensions(width: int, height: int, params: JobParams) -> tuple[float, float, float, float, float]:
    resolved = resolve_locked_box_dimensions(
        width,
        height,
        params.width_mm,
        params.height_mm,
        lock_aspect_ratio=getattr(params, "lock_aspect_ratio", True),
        size_mm=params.size_mm,
    )
    # size_mm longest-edge path for outline when neither dim given (legacy).
    if resolved["size_source"] == "size_mm" and params.size_mm not in (None, ""):
        scale = float(params.size_mm) / max(width, height)
        target_width = width * scale
        target_height = height * scale
    else:
        target_width = float(resolved["final_width_mm"])
        target_height = float(resolved["final_height_mm"])
    fit_scale = min(1.0, params.work_area_width_mm / target_width, params.work_area_height_mm / target_height)
    target_width *= fit_scale
    target_height *= fit_scale
    return target_width, target_height, target_width / width, target_height / height, max(target_width, target_height)


def _trace_vector(components: list[set[tuple[int, int]]], width: int, height: int, scale_x: float, scale_y: float, size_mm: float, trace_algorithm: ResolvedTraceAlgorithm, simplify_factor: float) -> TraceResult:
    point_budget = max(25, TRACE_OUTPUT_POINT_BUDGET // max(len(components), 1))
    raw_paths = [path for component in components for path in _component_edge_paths(component, scale_x, scale_y)]
    raw_point_count = sum(len(path) for path in raw_paths)
    simplify_tolerance = max(max(scale_x, scale_y) * 0.35, size_mm * 0.001) * max(simplify_factor, 0.25)
    paths = [_simplify_path(path, simplify_tolerance) for path in raw_paths]
    paths = [_downsample_closed_path(path, max_points=point_budget) for path in paths]
    paths = [path for path in paths if len(path) >= 4]
    point_count = sum(len(path) for path in paths)
    return TraceResult(
        paths=paths,
        width_mm=width * scale_x,
        height_mm=height * scale_y,
        contour_count=len(paths),
        point_count=point_count,
        raw_contour_count=len(raw_paths),
        raw_point_count=raw_point_count,
        trace_algorithm=trace_algorithm,
    )


def _component_edge_paths(component: set[tuple[int, int]], scale_x: float, scale_y: float) -> list[list[tuple[float, float]]]:
    edges = _boundary_edges(component)
    if not edges:
        return []
    loops = _trace_edge_loops(edges)
    if not loops:
        return []
    loops.sort(key=_polygon_area_abs, reverse=True)
    return [[(x_coord * scale_x, y_coord * scale_y) for x_coord, y_coord in loop] for loop in loops]


def _boundary_edges(component: set[tuple[int, int]]) -> set[tuple[tuple[int, int], tuple[int, int]]]:
    edges: set[tuple[tuple[int, int], tuple[int, int]]] = set()
    for x_coord, y_coord in component:
        if (x_coord, y_coord - 1) not in component:
            edges.add(((x_coord, y_coord), (x_coord + 1, y_coord)))
        if (x_coord + 1, y_coord) not in component:
            edges.add(((x_coord + 1, y_coord), (x_coord + 1, y_coord + 1)))
        if (x_coord, y_coord + 1) not in component:
            edges.add(((x_coord + 1, y_coord + 1), (x_coord, y_coord + 1)))
        if (x_coord - 1, y_coord) not in component:
            edges.add(((x_coord, y_coord + 1), (x_coord, y_coord)))
    return edges


def _trace_edge_loops(edges: set[tuple[tuple[int, int], tuple[int, int]]]) -> list[list[tuple[int, int]]]:
    next_points: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for start, end in edges:
        next_points.setdefault(start, []).append(end)
    for points in next_points.values():
        points.sort(key=lambda point: (point[1], point[0]))
    remaining = set(edges)
    loops: list[list[tuple[int, int]]] = []
    while remaining:
        start, end = min(remaining, key=lambda edge: (edge[0][1], edge[0][0], edge[1][1], edge[1][0]))
        remaining.remove((start, end))
        loop = [start, end]
        current = end
        while current != start:
            candidates = [candidate for candidate in next_points.get(current, []) if (current, candidate) in remaining]
            if not candidates:
                loop = []
                break
            next_point = candidates[0]
            remaining.remove((current, next_point))
            loop.append(next_point)
            current = next_point
        if len(loop) >= 4 and loop[0] == loop[-1]:
            loops.append(loop)
    return loops


def _polygon_area_abs(path: list[tuple[int, int]]) -> float:
    if len(path) < 4:
        return 0.0
    area = 0.0
    for index in range(len(path) - 1):
        x_first, y_first = path[index]
        x_second, y_second = path[index + 1]
        area += x_first * y_second - x_second * y_first
    return abs(area) / 2


def _simplify_path(path: list[tuple[float, float]], tolerance: float) -> list[tuple[float, float]]:
    if len(path) <= 4:
        return path
    closed = _points_equal(path[0], path[-1])
    points = path[:-1] if closed else path
    simplified = _rdp(points, tolerance)
    if closed and simplified[0] != simplified[-1]:
        simplified.append(simplified[0])
    return simplified


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


def _perpendicular_distance(point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]) -> float:
    if _points_equal(start, end):
        return sqrt((point[0] - start[0]) ** 2 + (point[1] - start[1]) ** 2)
    numerator = abs((end[1] - start[1]) * point[0] - (end[0] - start[0]) * point[1] + end[0] * start[1] - end[1] * start[0])
    denominator = sqrt((end[1] - start[1]) ** 2 + (end[0] - start[0]) ** 2)
    return numerator / denominator


def _points_equal(first: tuple[float, float], second: tuple[float, float]) -> bool:
    return abs(first[0] - second[0]) <= 1e-9 and abs(first[1] - second[1]) <= 1e-9


def _downsample_closed_path(path: list[tuple[float, float]], max_points: int) -> list[tuple[float, float]]:
    if len(path) <= max_points:
        return path
    if len(path) >= 4 and _points_equal(path[0], path[-1]):
        sampled = _downsample_path(path[:-1], max_points=max_points - 1)
        return sampled + [sampled[0]]
    return _downsample_path(path, max_points=max_points)


def _connected_components(foreground: set[tuple[int, int]]) -> list[set[tuple[int, int]]]:
    remaining = set(foreground)
    components: list[set[tuple[int, int]]] = []
    while remaining:
        start = remaining.pop()
        component = {start}
        stack = [start]
        while stack:
            x_coord, y_coord = stack.pop()
            for neighbor in _neighbors4(x_coord, y_coord):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    component.add(neighbor)
                    stack.append(neighbor)
        components.append(component)
    return components


def _boundary_path(component: set[tuple[int, int]], foreground: set[tuple[int, int]], width: int, height: int, scale: float, max_points: int | None) -> list[tuple[float, float]]:
    boundary = [point for point in component if _is_boundary(point, foreground, width, height)]
    if not boundary:
        return []
    ordered = _order_boundary(boundary)
    path = [(x * scale, y * scale) for x, y in ordered]
    if max_points is None:
        return path
    return _downsample_path(path, max_points=max_points)


def _order_boundary(boundary: list[tuple[int, int]]) -> list[tuple[int, int]]:
    remaining = set(boundary)
    ordered = [min(remaining, key=lambda point: (point[1], point[0]))]
    remaining.remove(ordered[0])
    while remaining:
        current = ordered[-1]
        next_point = _nearest_neighbor(current, remaining)
        if _distance_squared(current, next_point) > 4:
            break
        ordered.append(next_point)
        remaining.remove(next_point)
    if remaining:
        return []
    return ordered


def _nearest_neighbor(point: tuple[int, int], candidates: set[tuple[int, int]]) -> tuple[int, int]:
    return min(candidates, key=lambda candidate: (_distance_squared(point, candidate), candidate[1], candidate[0]))


def _distance_squared(first: tuple[int, int], second: tuple[int, int]) -> int:
    return (first[0] - second[0]) ** 2 + (first[1] - second[1]) ** 2


def _downsample_path(path: list[tuple[float, float]], max_points: int) -> list[tuple[float, float]]:
    if len(path) <= max_points:
        return path
    step = len(path) / max_points
    return [path[int(index * step)] for index in range(max_points)]


def _is_boundary(point: tuple[int, int], foreground: set[tuple[int, int]], width: int, height: int) -> bool:
    x_coord, y_coord = point
    for neighbor_x, neighbor_y in _neighbors4(x_coord, y_coord):
        if neighbor_x < 0 or neighbor_y < 0 or neighbor_x >= width or neighbor_y >= height:
            return True
        if (neighbor_x, neighbor_y) not in foreground:
            return True
    return False


def _neighbors4(x_coord: int, y_coord: int) -> tuple[tuple[int, int], ...]:
    return ((x_coord - 1, y_coord), (x_coord + 1, y_coord), (x_coord, y_coord - 1), (x_coord, y_coord + 1))


def _read_pgm_ascii(image_path: Path) -> tuple[list[list[int]], int, int]:
    tokens: list[str] = []
    for line in image_path.read_text(encoding="ascii").splitlines():
        clean = line.split("#", 1)[0].strip()
        if clean:
            tokens.extend(clean.split())
    if len(tokens) < 4 or tokens[0] != "P2":
        raise ValueError("MVP currently supports ASCII PGM (P2) images for dependency-free tracing")
    width = int(tokens[1])
    height = int(tokens[2])
    max_value = int(tokens[3])
    values = [int(token) for token in tokens[4:]]
    if len(values) != width * height:
        raise ValueError("PGM pixel count does not match image dimensions")
    if max_value <= 0:
        raise ValueError("PGM max value must be positive")
    normalized = [round(value * 255 / max_value) for value in values]
    return [normalized[index : index + width] for index in range(0, len(normalized), width)], width, height


def _read_image_pixels(image_path: Path) -> tuple[list[list[int]], int, int]:
    try:
        return _read_pgm_ascii(image_path)
    except UnicodeDecodeError:
        return _read_with_pillow(image_path)
    except ValueError:
        return _read_with_pillow(image_path)


def _read_with_pillow(image_path: Path) -> tuple[list[list[int]], int, int]:
    try:
        from PIL import Image
    except ImportError as error:
        raise ValueError("PNG/JPG input requires Pillow; install pillow or use ASCII PGM P2") from error
    with Image.open(image_path) as image:
        grayscale = image.convert("L")
        width, height = grayscale.size
        if hasattr(grayscale, "get_flattened_data"):
            values = list(grayscale.get_flattened_data())
        else:
            values = list(grayscale.getdata())
    return [values[index : index + width] for index in range(0, len(values), width)], width, height
