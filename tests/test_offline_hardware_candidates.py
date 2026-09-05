"""Offline hardware-sensitive G-code candidates — UNTESTED_ON_HARDWARE only.

These tests never open serial ports, Telnet, HTTP laser devices, cameras,
senders, or workers. All device-related call counts must remain zero.
"""

from __future__ import annotations

import ast
import math
import re
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from core.ai_laser_gcode.gcode_writer import build_gcode, build_gcode_with_stats, build_raster_gcode
from core.ai_laser_gcode.models import JobParams, TraceResult
from core.ai_laser_gcode.offline_hardware_candidates import (
    MAX_OFFLINE_COMMANDS,
    UNTESTED_ON_HARDWARE,
    OfflineCandidateProfile,
    OfflineHardwareCandidateError,
    apply_seam_overlap,
    build_offline_outline_comparison,
    build_offline_outline_candidate,
    build_offline_pixel_size_comparison,
    prepare_offline_paths,
    simplify_closed_path,
)
from core.ai_laser_gcode.raster import MAX_RASTER_CELLS
from core.ai_laser_gcode.raster_quality import _candidate_pixel_sizes


def _square(side: float = 10.0, origin: tuple[float, float] = (0.0, 0.0)) -> list[tuple[float, float]]:
    x0, y0 = origin
    return [
        (x0, y0),
        (x0 + side, y0),
        (x0 + side, y0 + side),
        (x0, y0 + side),
        (x0, y0),
    ]


def _dense_square(side: float = 10.0, steps: int = 20) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for i in range(steps + 1):
        points.append((side * i / steps, 0.0))
    for i in range(1, steps + 1):
        points.append((side, side * i / steps))
    for i in range(1, steps + 1):
        points.append((side - side * i / steps, side))
    for i in range(1, steps + 1):
        points.append((0.0, side - side * i / steps))
    return points


def _params(**overrides) -> JobParams:
    base = JobParams(material="wood", size_mm=20.0, power=100, feed_rate=1000, mode="outline")
    return replace(base, **overrides) if overrides else base


def _write_binary_png(path: Path, width: int = 8, height: int = 8) -> Path:
    try:
        from PIL import Image
    except ImportError as error:  # pragma: no cover - pillow required in project env
        raise unittest.SkipTest("Pillow required for raster offline comparison tests") from error
    image = Image.new("L", (width, height), 255)
    for y in range(height // 4, 3 * height // 4):
        for x in range(width // 4, 3 * width // 4):
            image.putpixel((x, y), 0)
    image.save(path)
    return path


def _distance_to_polyline(point, path) -> float:
    px, py = point
    best = float("inf")
    for start, end in zip(path, path[1:]):
        sx, sy = start
        ex, ey = end
        dx, dy = ex - sx, ey - sy
        if abs(dx) <= 1e-12 and abs(dy) <= 1e-12:
            best = min(best, math.hypot(px - sx, py - sy))
            continue
        t = max(0.0, min(1.0, ((px - sx) * dx + (py - sy) * dy) / (dx * dx + dy * dy)))
        best = min(best, math.hypot(px - (sx + t * dx), py - (sy + t * dy)))
    return best


class OfflineProfileTests(unittest.TestCase):
    def test_profile_defaults(self):
        profile = OfflineCandidateProfile()
        self.assertEqual(profile.simplify_tolerance_mm, 0.02)
        self.assertEqual(profile.contour_start_dwell_s, 0.15)
        self.assertEqual(profile.seam_overlap_mm, 0.3)
        self.assertEqual(profile.travel_mode, "rapid_g0")
        self.assertEqual(profile.line_art_pixel_size_mm, 0.06)

    def test_profile_rejects_invalid_travel_mode(self):
        with self.assertRaises(OfflineHardwareCandidateError):
            OfflineCandidateProfile(travel_mode="warp_drive")

    def test_profile_rejects_negative_and_nonfinite(self):
        with self.assertRaises(OfflineHardwareCandidateError):
            OfflineCandidateProfile(simplify_tolerance_mm=-0.01)
        with self.assertRaises(OfflineHardwareCandidateError):
            OfflineCandidateProfile(seam_overlap_mm=float("nan"))
        with self.assertRaises(OfflineHardwareCandidateError):
            OfflineCandidateProfile(contour_start_dwell_s=float("inf"))

    def test_profile_normalizes_numeric_strings(self):
        profile = OfflineCandidateProfile(simplify_tolerance_mm="0.02", line_art_pixel_size_mm="0.06")
        self.assertIsInstance(profile.simplify_tolerance_mm, float)
        self.assertIsInstance(profile.line_art_pixel_size_mm, float)


class SimplifyClosedPathTests(unittest.TestCase):
    def test_reduces_redundant_collinear_points(self):
        dense = _dense_square(side=10.0, steps=25)
        simplified = simplify_closed_path(dense, tolerance_mm=0.02)
        self.assertLess(len(simplified), len(dense))
        self.assertGreaterEqual(len(simplified), 4)
        self.assertAlmostEqual(simplified[0][0], simplified[-1][0], places=9)
        self.assertAlmostEqual(simplified[0][1], simplified[-1][1], places=9)

    def test_normalizes_open_input_to_closed(self):
        open_square = _square()[:-1]
        simplified = simplify_closed_path(open_square, tolerance_mm=0.02)
        self.assertAlmostEqual(simplified[0][0], simplified[-1][0], places=9)
        self.assertAlmostEqual(simplified[0][1], simplified[-1][1], places=9)
        self.assertGreaterEqual(len(simplified), 4)

    def test_shape_error_within_tolerance_and_deterministic(self):
        dense = _dense_square(side=8.0, steps=30)
        first = simplify_closed_path(dense, tolerance_mm=0.02)
        second = simplify_closed_path(dense, tolerance_mm=0.02)
        self.assertEqual(first, second)
        # Major corners should remain near the original square corners.
        xs = [p[0] for p in first]
        ys = [p[1] for p in first]
        self.assertAlmostEqual(min(xs), 0.0, delta=0.02)
        self.assertAlmostEqual(max(xs), 8.0, delta=0.02)
        self.assertAlmostEqual(min(ys), 0.0, delta=0.02)
        self.assertAlmostEqual(max(ys), 8.0, delta=0.02)
        self.assertLessEqual(max(_distance_to_polyline(point, first) for point in dense), 0.02 + 1e-9)

    def test_degenerate_inputs_fail_clearly(self):
        with self.assertRaises(OfflineHardwareCandidateError):
            simplify_closed_path([], tolerance_mm=0.02)
        with self.assertRaises(OfflineHardwareCandidateError):
            simplify_closed_path([(0.0, 0.0), (1.0, 0.0)], tolerance_mm=0.02)
        with self.assertRaises(OfflineHardwareCandidateError):
            simplify_closed_path([(float("nan"), 0.0), (1.0, 0.0), (1.0, 1.0)], tolerance_mm=0.02)


class SeamOverlapTests(unittest.TestCase):
    def test_seam_clamped_to_first_segment(self):
        path = _square(side=10.0)
        seamed = apply_seam_overlap(path, seam_overlap_mm=0.3)
        self.assertGreater(len(seamed), len(path))
        # After closed path, one extra point along p0->p1 by 0.3mm.
        self.assertAlmostEqual(seamed[-1][0], 0.3, places=6)
        self.assertAlmostEqual(seamed[-1][1], 0.0, places=6)
        # Never past p1.
        self.assertLessEqual(seamed[-1][0], path[1][0] + 1e-9)

    def test_zero_length_first_segment_skips_seam(self):
        path = [(0.0, 0.0), (0.0, 0.0), (5.0, 0.0), (5.0, 5.0), (0.0, 5.0), (0.0, 0.0)]
        seamed = apply_seam_overlap(path, seam_overlap_mm=0.3)
        # Zero-length first segment is skipped: no seam append, path stays closed.
        self.assertEqual(seamed[-1], seamed[0])
        self.assertFalse(any(abs(p[0] - 0.3) < 1e-9 and abs(p[1]) < 1e-9 for p in seamed[1:]))
        # Duplicate first vertex may be collapsed; never longer than a single close.
        self.assertLessEqual(len(seamed), len(path))
        self.assertGreaterEqual(len(seamed), 4)

    def test_overlap_never_exceeds_first_segment_length(self):
        path = [(0.0, 0.0), (0.2, 0.0), (0.2, 2.0), (0.0, 2.0), (0.0, 0.0)]
        seamed = apply_seam_overlap(path, seam_overlap_mm=0.3)
        self.assertAlmostEqual(seamed[-1][0], 0.2, places=6)
        self.assertAlmostEqual(seamed[-1][1], 0.0, places=6)


class OutlineCandidateGcodeTests(unittest.TestCase):
    def test_dwell_order_before_first_powered_cut(self):
        profile = OfflineCandidateProfile(
            simplify_tolerance_mm=0.02,
            contour_start_dwell_s=0.15,
            seam_overlap_mm=0.0,
            travel_mode="rapid_g0",
        )
        result = build_offline_outline_candidate(
            paths=[_square()],
            params=_params(),
            profile=profile,
            height_mm=20.0,
        )
        gcode = result["gcode"]
        self.assertIn(UNTESTED_ON_HARDWARE, result["status"])
        self.assertIn(f"; {UNTESTED_ON_HARDWARE}", gcode)
        self.assertIn("G4 P0.15", gcode)
        start_travel = gcode.index("G0 X")
        dwell = gcode.index("G4 P0.15")
        first_cut = gcode.index("G1 X")
        self.assertLess(start_travel, dwell)
        self.assertLess(dwell, first_cut)
        self.assertIn("M5", gcode[:dwell])
        self.assertIn("S0", gcode[:dwell])

    def test_production_default_has_no_contour_dwell(self):
        params = _params()
        trace = TraceResult(paths=[_square()], width_mm=20.0, height_mm=20.0, contour_count=1, point_count=5)
        production = build_gcode(params, trace)
        self.assertNotIn("G4", production)

    def test_outline_rejects_points_outside_work_area_before_writing(self):
        with self.assertRaises(OfflineHardwareCandidateError):
            build_offline_outline_candidate(
                paths=[_square(side=10.0, origin=(95.0, 0.0))],
                params=_params(),
                profile=OfflineCandidateProfile(seam_overlap_mm=0.0),
                width_mm=105.0,
                height_mm=20.0,
            )

    def test_build_gcode_rejects_non_numeric_dwell(self):
        params = _params()
        trace = TraceResult(paths=[_square()], width_mm=20.0, height_mm=20.0, contour_count=1, point_count=5)
        with self.assertRaises(ValueError):
            build_gcode_with_stats(params, trace, contour_start_dwell_s="not-a-number")

    def test_single_seam_overlap_not_double_closed(self):
        profile = OfflineCandidateProfile(seam_overlap_mm=0.3, contour_start_dwell_s=0.0)
        result = build_offline_outline_candidate(
            paths=[_square(side=10.0)],
            params=_params(),
            profile=profile,
            height_mm=20.0,
        )
        gcode = result["gcode"]
        # Machine Y is flipped by height: start machine (0, 20), seam along +X by 0.3.
        # Expect one powered move that lands on seam X=0.300 after returning near start,
        # and no second full-side reburn of the first segment beyond that seam.
        powered = [line for line in gcode.splitlines() if line.startswith("G1 ") and f"S{_params().power}" in line]
        seam_hits = [line for line in powered if "X0.300" in line]
        self.assertEqual(len(seam_hits), 1, powered)
        # Must not contain both a pure close-to-start after seam and another full 10mm first segment burn.
        full_first_segment = [line for line in powered if "X10.000" in line and "Y20.000" in line]
        self.assertEqual(len(full_first_segment), 1, powered)

    def test_controlled_g1_s0_travel_keeps_laser_off(self):
        profile = OfflineCandidateProfile(travel_mode="controlled_g1_s0", contour_start_dwell_s=0.15)
        paths = [_square(origin=(0.0, 0.0)), _square(origin=(20.0, 0.0))]
        result = build_offline_outline_candidate(
            paths=paths,
            params=_params(),
            profile=profile,
            height_mm=40.0,
        )
        gcode = result["gcode"]
        self.assertIn("G1 ", gcode)
        travel_lines = [
            line
            for line in gcode.splitlines()
            if line.startswith("G1 ") and "S0" in line and f"S{_params().power}" not in line
        ]
        self.assertGreaterEqual(len(travel_lines), 1)
        self.assertNotIn("G0 X", "\n".join(line for line in gcode.splitlines() if line.startswith("G0 X")))
        self.assertIn("M5", gcode)
        self.assertEqual(result["stats"]["command_count"], len([ln for ln in gcode.splitlines() if ln and not ln.startswith(";")]))

    def test_rapid_g0_travel_keeps_current_semantic(self):
        profile = OfflineCandidateProfile(travel_mode="rapid_g0", contour_start_dwell_s=0.0, seam_overlap_mm=0.0)
        result = build_offline_outline_candidate(
            paths=[_square()],
            params=_params(),
            profile=profile,
            height_mm=20.0,
        )
        self.assertIn("G0 X", result["gcode"])
        self.assertEqual(result["profile"]["travel_mode"], "rapid_g0")

    def test_outline_comparison_records_g0_vs_g1_s0_deltas(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = build_offline_outline_comparison(
                paths=[_square(), _square(origin=(20.0, 0.0))],
                params=_params(),
                profile=OfflineCandidateProfile(contour_start_dwell_s=0.15),
                height_mm=40.0,
                width_mm=40.0,
                output_dir=Path(tmp) / "comparison",
            )
            self.assertEqual(result["status"], UNTESTED_ON_HARDWARE)
            self.assertIn("G0 X", result["rapid_g0"]["gcode"])
            self.assertNotIn("G0 X", result["controlled_g1_s0"]["gcode"])
            self.assertIn("command_count", result["delta"])
            self.assertTrue(Path(result["controlled_g1_s0"]["preview_path"]).exists())

    def test_writer_default_hooks_preserve_production_sequence(self):
        params = _params()
        trace = TraceResult(paths=[_square()[:-1]], width_mm=20.0, height_mm=20.0, contour_count=1, point_count=4)
        default_gcode, _ = build_gcode_with_stats(params, trace)
        explicit_gcode, _ = build_gcode_with_stats(
            params,
            trace,
            auto_close_open_paths=True,
            contour_start_dwell_s=0.0,
            travel_mode="rapid_g0",
        )
        self.assertEqual(default_gcode, explicit_gcode)
        self.assertNotIn("G4", default_gcode)

    def test_writer_default_snapshot_keeps_original_outline_sequence(self):
        expected = "\n".join(
            [
                "; Generated by ai-laser-gcode-nc",
                "; GRBL laser mode expected: $30=1000, $32=1",
                "; Material: wood; note: 起始建议，必须小功率试雕",
                "G21",
                "G90",
                "G94",
                "G17",
                "G92 X0 Y0 Z0",
                "M4 S0",
                "G0 F3000",
                "M5",
                "G0 X0.000 Y20.000",
                "M4 S0",
                "G1 X10.000 Y20.000 S100 F1000",
                "G1 X10.000 Y10.000 S100 F1000",
                "G1 X0.000 Y10.000 S100 F1000",
                "G1 X0.000 Y20.000 S100 F1000",
                "S0",
                "M5",
                "M2",
                "",
            ]
        )
        trace = TraceResult(paths=[_square()], width_mm=20.0, height_mm=20.0, contour_count=1, point_count=5)
        self.assertEqual(build_gcode(_params(), trace), expected)


class RasterOfflineComparisonTests(unittest.TestCase):
    def test_compares_baseline_and_0_06_without_device_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            image_path = _write_binary_png(Path(tmp) / "line.png", width=16, height=16)
            params = _params(
                mode="raster",
                size_mm=10.0,
                pixel_size_mm=0.1,
                manual_pixel_size=True,
                dither_algorithm="threshold",
                threshold=128,
                manual_threshold=True,
                overscan_mm=0.0,
            )
            with mock.patch("core.laser_execution.send_file") as send_mock, mock.patch(
                "core.laser_execution.probe_serial_grbl"
            ) as probe_mock, mock.patch(
                "core.laser_execution.cancel_job"
            ) as cancel_mock:
                result = build_offline_pixel_size_comparison(
                    image_path=image_path,
                    params=params,
                    baseline_pixel_size_mm=0.1,
                    candidate_pixel_size_mm=0.06,
                    output_dir=Path(tmp) / "evidence",
                )
                self.assertEqual(send_mock.call_count, 0)
                self.assertEqual(probe_mock.call_count, 0)
                self.assertEqual(cancel_mock.call_count, 0)
            self.assertEqual(result["status"], UNTESTED_ON_HARDWARE)
            self.assertEqual(result["baseline"]["pixel_size_mm"], 0.1)
            self.assertEqual(result["candidate"]["pixel_size_mm"], 0.06)
            self.assertTrue(Path(result["candidate"]["gcode_path"]).exists())
            self.assertTrue(Path(result["candidate"]["preview_path"]).exists())
            self.assertGreaterEqual(result["candidate"]["complexity"], 1)
            self.assertNotIn(".runtime", str(result["candidate"]["gcode_path"]))
            self.assertEqual(result["candidate"]["bounds"]["max_x_mm"], result["candidate"]["width_mm"])

    def test_default_comparison_creates_temp_gcode_and_preview_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            image_path = _write_binary_png(Path(tmp) / "line.png")
            result = build_offline_pixel_size_comparison(
                image_path=image_path,
                params=_params(mode="raster", dither_algorithm="threshold", threshold=128, manual_threshold=True, overscan_mm=0.0),
            )
            self.assertTrue(Path(result["candidate"]["gcode_path"]).is_file())
            self.assertTrue(Path(result["candidate"]["preview_path"]).is_file())
            self.assertIn(UNTESTED_ON_HARDWARE, Path(result["candidate"]["gcode_path"]).read_text(encoding="utf-8"))

    def test_evidence_path_is_rejected_before_any_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            image_path = _write_binary_png(Path(tmp) / "line.png")
            evidence_dir = Path(tmp) / ".runtime" / "laser_workflows" / "offline"
            with self.assertRaises(OfflineHardwareCandidateError):
                build_offline_pixel_size_comparison(
                    image_path=image_path,
                    params=_params(mode="raster", dither_algorithm="threshold", threshold=128, manual_threshold=True, overscan_mm=0.0),
                    output_dir=evidence_dir,
                )
            self.assertFalse(evidence_dir.exists())

    def test_arbitrary_workspace_evidence_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            image_path = _write_binary_png(Path(tmp) / "line.png")
            with self.assertRaises(OfflineHardwareCandidateError):
                build_offline_pixel_size_comparison(
                    image_path=image_path,
                    params=_params(mode="raster", dither_algorithm="threshold", threshold=128, manual_threshold=True, overscan_mm=0.0),
                    output_dir=Path.cwd() / "offline-evidence-not-allowed",
                )

    def test_explicit_threshold_with_error_diffusion_fails_before_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            image_path = _write_binary_png(Path(tmp) / "line.png")
            evidence_dir = Path(tmp) / "evidence"
            with self.assertRaises(OfflineHardwareCandidateError):
                build_offline_pixel_size_comparison(
                    image_path=image_path,
                    params=_params(
                        mode="raster",
                        threshold=128,
                        dither_algorithm="floyd_steinberg",
                        overscan_mm=0.0,
                    ),
                    output_dir=evidence_dir,
                )
            self.assertFalse(evidence_dir.exists())

    def test_production_runtime_evidence_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            image_path = _write_binary_png(Path(tmp) / "line.png")
            for runtime_dir in (".runtime",):
                evidence_dir = Path(tmp) / runtime_dir / "offline"
                with self.subTest(runtime_dir=runtime_dir), self.assertRaises(OfflineHardwareCandidateError):
                    build_offline_pixel_size_comparison(
                        image_path=image_path,
                        params=_params(mode="raster", dither_algorithm="threshold", threshold=128, manual_threshold=True, overscan_mm=0.0),
                        output_dir=evidence_dir,
                    )
                self.assertFalse(evidence_dir.exists())

    def test_explicit_command_limit_fails_before_evidence_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            image_path = _write_binary_png(Path(tmp) / "line.png")
            evidence_dir = Path(tmp) / "evidence"
            with self.assertRaises(OfflineHardwareCandidateError):
                build_offline_pixel_size_comparison(
                    image_path=image_path,
                    params=_params(mode="raster", dither_algorithm="threshold", threshold=128, manual_threshold=True, overscan_mm=0.0),
                    output_dir=evidence_dir,
                    max_command_count=MAX_OFFLINE_COMMANDS,
                    max_estimated_seconds=0.000001,
                )
            self.assertFalse(evidence_dir.exists())

    def test_complexity_overflow_fails_clearly_without_silent_resize(self):
        with tempfile.TemporaryDirectory() as tmp:
            image_path = _write_binary_png(Path(tmp) / "big.png", width=32, height=32)
            # Force tiny pixel size at large physical size to exceed MAX_RASTER_CELLS.
            params = _params(
                mode="raster",
                size_mm=100.0,
                width_mm=100.0,
                height_mm=100.0,
                pixel_size_mm=0.05,
                manual_pixel_size=True,
                dither_algorithm="threshold",
                threshold=128,
                manual_threshold=True,
                overscan_mm=0.0,
                work_area_width_mm=100.0,
                work_area_height_mm=100.0,
            )
            with self.assertRaises(OfflineHardwareCandidateError) as ctx:
                build_offline_pixel_size_comparison(
                    image_path=image_path,
                    params=params,
                    baseline_pixel_size_mm=0.05,
                    candidate_pixel_size_mm=0.05,
                    output_dir=Path(tmp) / "evidence",
                )
            message = str(ctx.exception).lower()
            self.assertTrue("too large" in message or "complex" in message or "raster" in message)
            self.assertNotIn("silent", message)

    def test_work_area_scaling_fails_instead_of_changing_physical_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            image_path = _write_binary_png(Path(tmp) / "line.png", width=16, height=16)
            with self.assertRaises(OfflineHardwareCandidateError) as ctx:
                build_offline_pixel_size_comparison(
                    image_path=image_path,
                    params=_params(
                        mode="raster",
                        size_mm=100.0,
                        work_area_width_mm=80.0,
                        work_area_height_mm=80.0,
                        dither_algorithm="threshold",
                        threshold=128,
                        manual_threshold=True,
                        overscan_mm=0.0,
                    ),
                    baseline_pixel_size_mm=0.2,
                    candidate_pixel_size_mm=0.2,
                    output_dir=Path(tmp) / "evidence",
                )
            self.assertIn("silently shrinking", str(ctx.exception))

    def test_public_candidate_pixel_sizes_exclude_0_06(self):
        params = _params(mode="raster", pixel_size_mm=0.2)
        for strategy in ("auto", "speed", "balanced", "quality"):
            sizes = _candidate_pixel_sizes(params, "line_art", strategy, manual_pixel=False)
            self.assertNotIn(0.06, sizes)
            self.assertNotIn(0.06, [round(v, 4) for v in sizes])

    def test_0_06_occurs_only_in_offline_module_and_offline_tests(self):
        root = Path(__file__).resolve().parents[1]
        allowed = {
            root / "core/ai_laser_gcode/offline_hardware_candidates.py",
            Path(__file__).resolve(),
        }
        offenders = []
        pattern = re.compile(r"(?<!\d)0\.06(?:0+)?(?!\d)")
        for path in [*root.glob("core/**/*.py"), *root.glob("tools/*.py"), root / "moss_mcp" / "web_server.py", root / "apps" / "excalidraw_lab" / "server.py", root / "moss_mcp" / "server.py"]:
            if path in allowed or not path.exists():
                continue
            if pattern.search(path.read_text(encoding="utf-8")):
                offenders.append(str(path.relative_to(root)))
        self.assertEqual(offenders, [])


class IsolationAndRegressionTests(unittest.TestCase):
    def test_production_modules_do_not_import_offline_module(self):
        root = Path(__file__).resolve().parents[1]
        banned = "offline_hardware_candidates"
        production_paths = [
            *root.glob("tools/*.py"),
            root / "moss_mcp" / "web_server.py",
            root / "apps" / "excalidraw_lab" / "server.py",
            root / "moss_mcp" / "server.py",
            root / "core/ai_laser_gcode/generator.py",
            root / "core/ai_laser_gcode/raster_quality.py",
            root / "core/ai_laser_gcode/gcode_writer.py",
        ]
        for path in production_paths:
            if not path.exists():
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn(banned, alias.name, msg=f"{path} imports {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    self.assertNotIn(banned, module, msg=f"{path} imports from {module}")
                    for alias in node.names:
                        self.assertNotIn(banned, alias.name, msg=f"{path} imports name {alias.name}")

    def test_jobparams_has_no_offline_public_fields(self):
        fields = set(JobParams.__dataclass_fields__)
        for banned in ("dwell_s", "seam_overlap_mm", "travel_mode", "offline_0_06", "contour_start_dwell_s"):
            self.assertNotIn(banned, fields)

    def test_prepare_paths_pipeline_is_pure(self):
        dense = _dense_square(side=6.0, steps=12)
        profile = OfflineCandidateProfile(seam_overlap_mm=0.3)
        prepared = prepare_offline_paths([dense], profile)
        self.assertEqual(len(prepared), 1)
        self.assertGreaterEqual(len(prepared[0]), 4)
        self.assertTrue(math.isfinite(prepared[0][0][0]))

    def test_raster_writer_line_travel_semantics_untouched_by_offline_import(self):
        # Importing offline must not mutate raster G0 / row-internal G1 S0 behavior.
        from core.ai_laser_gcode.models import RasterLineSegment, RasterResult

        params = _params(mode="raster", power=120, feed_rate=800)
        raster = RasterResult(
            segments=[RasterLineSegment(y_mm=1.0, start_x_mm=1.0, end_x_mm=3.0)],
            width_mm=10.0,
            height_mm=10.0,
            grid_width=10,
            grid_height=10,
            pixel_size_mm=0.2,
            overscan_mm=1.0,
            scaled=False,
            scale_factor=1.0,
            work_area_width_mm=100.0,
            work_area_height_mm=100.0,
        )
        gcode = build_raster_gcode(params, raster)
        self.assertIn("G0 X", gcode)
        self.assertIn("S0", gcode)
        self.assertNotIn("G4", gcode)


if __name__ == "__main__":
    unittest.main()
