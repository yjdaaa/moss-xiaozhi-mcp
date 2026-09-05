"""Shared locked-aspect dual-dimension resolution for AI raster/outline paths."""

from __future__ import annotations

from typing import Any


def resolve_locked_box_dimensions(
    source_width_px: float | int,
    source_height_px: float | int,
    width_mm: float | None,
    height_mm: float | None,
    *,
    lock_aspect_ratio: bool = True,
    size_mm: float | None = None,
) -> dict[str, Any]:
    """Resolve final mm size for image engines.

    When both width_mm and height_mm are provided:
      - lock_aspect_ratio=True  -> fit uniformly into the box (size_source=manual_box)
      - lock_aspect_ratio=False -> exact stretch (size_source=manual)
    Only width or only height keeps proportional derivation.
    Neither falls back to size_mm (square-ish longest-edge for outline/raster).
    """
    src_w = float(source_width_px)
    src_h = float(source_height_px)
    if src_w <= 0 or src_h <= 0:
        raise ValueError("source dimensions must be positive")

    aspect = src_w / src_h
    has_w = width_mm is not None and float(width_mm) > 0
    has_h = height_mm is not None and float(height_mm) > 0
    box_w = float(width_mm) if has_w else None
    box_h = float(height_mm) if has_h else None
    locked = bool(lock_aspect_ratio)

    if has_w and has_h:
        if locked:
            scale = min(box_w / src_w, box_h / src_h)
            final_w = src_w * scale
            final_h = src_h * scale
            size_source = "manual_box"
        else:
            final_w = box_w
            final_h = box_h
            size_source = "manual"
        return {
            "final_width_mm": final_w,
            "final_height_mm": final_h,
            "requested_width_mm": box_w,
            "requested_height_mm": box_h,
            "size_source": size_source,
            "lock_aspect_ratio": locked,
        }

    if has_w:
        final_w = box_w
        final_h = box_w / aspect
        return {
            "final_width_mm": final_w,
            "final_height_mm": final_h,
            "requested_width_mm": box_w,
            "requested_height_mm": None,
            "size_source": "manual_width",
            "lock_aspect_ratio": locked,
        }

    if has_h:
        final_h = box_h
        final_w = box_h * aspect
        return {
            "final_width_mm": final_w,
            "final_height_mm": final_h,
            "requested_width_mm": None,
            "requested_height_mm": box_h,
            "size_source": "manual_height",
            "lock_aspect_ratio": locked,
        }

    # Default: longest-edge style from size_mm (legacy AI behavior for square-ish sizing).
    base = float(size_mm) if size_mm not in (None, "") and float(size_mm) > 0 else 50.0
    # Keep prior AI convention: size_mm as width-ish for raster square fallback.
    final_w = base
    final_h = base * src_h / src_w
    return {
        "final_width_mm": final_w,
        "final_height_mm": final_h,
        "requested_width_mm": None,
        "requested_height_mm": None,
        "size_source": "size_mm",
        "lock_aspect_ratio": locked,
    }
