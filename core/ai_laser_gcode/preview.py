import struct
import zlib
from pathlib import Path

from core.ai_laser_gcode.models import RasterResult, TraceResult


def write_preview_png(trace: TraceResult | RasterResult, output_path: Path, size_px: int = 256) -> None:
    canvas = [[255 for _ in range(size_px)] for _ in range(size_px)]
    max_dim = max(trace.width_mm, trace.height_mm, 1)
    scale = (size_px - 20) / max_dim
    if isinstance(trace, RasterResult):
        for segment in trace.segments:
            start = _preview_point(segment.start_x_mm, segment.y_mm, scale, size_px)
            end_y = segment.end_y_mm if segment.axis == "vertical" else segment.y_mm
            end = _preview_point(segment.end_x_mm, end_y, scale, size_px)
            _draw_preview_line(canvas, start[0], start[1], end[0], end[1])
    else:
        for path in trace.paths:
            preview_points = [_preview_point(x_coord, y_coord, scale, size_px) for x_coord, y_coord in path]
            if len(preview_points) == 1:
                _draw_preview_pixel(canvas, preview_points[0][0], preview_points[0][1])
                continue
            for start, end in zip(preview_points, preview_points[1:]):
                _draw_preview_line(canvas, start[0], start[1], end[0], end[1])
    raw = b"".join(b"\x00" + bytes(row) for row in canvas)
    png = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", size_px, size_px, 8, 0, 0, 0, 0)) + _chunk(b"IDAT", zlib.compress(raw)) + _chunk(b"IEND", b"")
    output_path.write_bytes(png)


def _chunk(kind: bytes, data: bytes) -> bytes:
    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def _preview_point(x_coord: float, y_coord: float, scale: float, size_px: int) -> tuple[int, int]:
    x_px = min(size_px - 1, max(0, int(10 + x_coord * scale)))
    y_px = min(size_px - 1, max(0, int(10 + y_coord * scale)))
    return x_px, y_px


def _draw_preview_line(canvas: list[list[int]], start_x: int, start_y: int, end_x: int, end_y: int) -> None:
    delta_x = abs(end_x - start_x)
    delta_y = -abs(end_y - start_y)
    step_x = 1 if start_x < end_x else -1
    step_y = 1 if start_y < end_y else -1
    error = delta_x + delta_y
    x_px = start_x
    y_px = start_y
    while True:
        _draw_preview_pixel(canvas, x_px, y_px)
        if x_px == end_x and y_px == end_y:
            break
        double_error = 2 * error
        if double_error >= delta_y:
            error += delta_y
            x_px += step_x
        if double_error <= delta_x:
            error += delta_x
            y_px += step_y


def _draw_preview_pixel(canvas: list[list[int]], x_px: int, y_px: int) -> None:
    canvas[y_px][x_px] = 0
