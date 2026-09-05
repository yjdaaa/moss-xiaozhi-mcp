import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from PIL import Image, ImageDraw

from core.ai_laser_gcode.ai_assistant import AiAssistResult, AiProviderConfig, parse_ai_preprocess_suggestion, run_ai_assist
from core.ai_laser_gcode.calibration import generate_calibration_matrix, save_calibration_result
from core.ai_laser_gcode.generator import generate_job
from core.ai_laser_gcode.gcode_writer import build_gcode_with_stats, build_raster_gcode
from core.ai_laser_gcode.image_preprocess import _otsu_threshold, prepare_processed_image
from core.ai_laser_gcode.image_router import analyze_image, resolve_job_mode
from core.ai_laser_gcode.image_trace import trace_image
from core.ai_laser_gcode.material_library import BUILTIN_RECORDS, load_material_records, match_material_record
from core.ai_laser_gcode.models import JobParams, TraceResult
from core.ai_laser_gcode.parameters import parse_job_params
from core.ai_laser_gcode.path_optimizer import optimize_trace_paths
from core.ai_laser_gcode.preview import write_preview_png
from core.ai_laser_gcode.raster import _dither, _resolve_dither_algorithm
from core.ai_laser_gcode.raster import rasterize_image
from core.ai_laser_gcode.safety import SafetyError, validate_job_params
from core.ai_laser_gcode.vector_fill import apply_vector_fill


class AiLaserGcodeCoreTests(unittest.TestCase):
    def make_test_image(self, directory, name="input.png"):
        image_path = Path(directory) / name
        image = Image.new("L", (80, 80), 255)
        draw = ImageDraw.Draw(image)
        draw.rectangle((20, 20, 60, 60), fill=0)
        image.save(image_path)
        return image_path

    def read_summary(self, bundle):
        return json.loads(bundle.summary_path.read_text(encoding="utf-8"))

    def test_generate_job_imports_from_core_package(self):
        self.assertEqual(generate_job.__module__, "core.ai_laser_gcode.generator")

    def test_safety_allows_configured_low_feed_rate_for_cut_materials(self):
        validate_job_params(
            JobParams(
                material="wood",
                size_mm=20,
                power=1000,
                feed_rate=100,
                mode="outline",
                task_type="cut_contour",
            )
        )

        with self.assertRaisesRegex(SafetyError, "F50 and F3000"):
            validate_job_params(
                JobParams(
                    material="wood",
                    size_mm=20,
                    power=1000,
                    feed_rate=49,
                    mode="outline",
                    task_type="cut_contour",
                )
            )

    def test_size_parser_does_not_treat_thickness_as_job_size(self):
        params = parse_job_params("椴木 thickness 3mm", mode="outline", thickness_mm=3.0, task_type="engrave_logo")
        self.assertEqual(params.size_mm, 50)
        self.assertEqual(params.thickness_mm, 3.0)
        self.assertFalse(params.manual_power)
        self.assertNotEqual(params.power, 3)

        explicit = parse_job_params("椴木 size 40mm thickness 3mm", mode="outline", thickness_mm=3.0, task_type="engrave_logo")
        self.assertEqual(explicit.size_mm, 40)

        structured = parse_job_params("椴木 thickness 3mm", size_mm=35, mode="outline", thickness_mm=3.0, task_type="engrave_logo")
        self.assertEqual(structured.size_mm, 35)

        manual = parse_job_params("椴木 size 40mm S380 F1500 thickness 3mm", mode="raster", thickness_mm=3.0, task_type="engrave_photo")
        self.assertTrue(manual.manual_power)
        self.assertTrue(manual.manual_feed_rate)
        self.assertEqual(manual.power, 380)
        self.assertEqual(manual.feed_rate, 1500)

        manual_pixel = parse_job_params(
            "椴木 thickness 3mm",
            mode="raster",
            thickness_mm=3.0,
            task_type="engrave_photo",
            pixel_size_mm="0.2",
        )
        self.assertTrue(manual_pixel.manual_pixel_size)
        self.assertEqual(manual_pixel.pixel_size_mm, 0.2)

    def test_generic_engrave_task_type_maps_to_strategy_specific_ai_task_type(self):
        outline = parse_job_params("椴木 thickness 3mm", mode="outline", thickness_mm=3.0, task_type="engrave")
        raster = parse_job_params("椴木 thickness 3mm", mode="raster", thickness_mm=3.0, task_type="engrave")
        cut = parse_job_params("椴木 thickness 3mm", mode="outline", thickness_mm=3.0, task_type="cut")
        string_thickness = parse_job_params("椴木", mode="raster", thickness_mm="3", task_type="engrave")

        self.assertEqual(outline.task_type, "engrave_logo")
        self.assertEqual(outline.task_type_source, "explicit")
        self.assertEqual(raster.task_type, "engrave_photo")
        self.assertEqual(cut.task_type, "cut_contour")
        self.assertEqual(string_thickness.thickness_mm, 3.0)

    def test_structured_width_height_override_and_scale_to_safe_size(self):
        params = parse_job_params(
            "椴木 thickness 3mm",
            width_mm=40,
            height_mm=20,
            mode="outline",
            thickness_mm=3.0,
            task_type="engrave_logo",
        )
        self.assertEqual(params.width_mm, 40)
        self.assertEqual(params.height_mm, 20)
        self.assertEqual(params.size_mm, 40)

        oversized = parse_job_params(
            "椴木 thickness 3mm",
            width_mm=200,
            height_mm=100,
            mode="outline",
            thickness_mm=3.0,
            task_type="engrave_logo",
        )
        self.assertEqual(oversized.width_mm, 120)
        self.assertEqual(oversized.height_mm, 60)
        self.assertEqual(oversized.size_mm, 120)

    def test_trace_uses_structured_width_and_height(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = self.make_test_image(directory)
            # Square source + locked dual size fits into box (20x20), not stretch to 40x20.
            locked = JobParams(
                material="wood",
                size_mm=50,
                width_mm=40,
                height_mm=20,
                power=120,
                feed_rate=900,
                mode="outline",
                lock_aspect_ratio=True,
            )
            unlocked = JobParams(
                material="wood",
                size_mm=50,
                width_mm=40,
                height_mm=20,
                power=120,
                feed_rate=900,
                mode="outline",
                lock_aspect_ratio=False,
            )
            locked_trace = trace_image(image_path, locked)
            unlocked_trace = trace_image(image_path, unlocked)

        self.assertAlmostEqual(locked_trace.width_mm, 20.0)
        self.assertAlmostEqual(locked_trace.height_mm, 20.0)
        self.assertAlmostEqual(unlocked_trace.width_mm, 40.0)
        self.assertAlmostEqual(unlocked_trace.height_mm, 20.0)

    def test_raster_locked_box_fits_source_aspect(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = self.make_test_image(directory)
            params = JobParams(
                material="wood",
                size_mm=50,
                width_mm=40,
                height_mm=20,
                power=120,
                feed_rate=900,
                mode="raster",
                pixel_size_mm=0.2,
                lock_aspect_ratio=True,
            )
            raster = rasterize_image(image_path, params)
        self.assertAlmostEqual(raster.width_mm, 20.0, places=3)
        self.assertAlmostEqual(raster.height_mm, 20.0, places=3)

    def test_single_dimension_keeps_source_aspect_when_unlocked(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = self.make_test_image(directory)
            outline = trace_image(
                image_path,
                JobParams(
                    material="wood",
                    size_mm=50,
                    width_mm=30,
                    power=120,
                    feed_rate=900,
                    mode="outline",
                    lock_aspect_ratio=False,
                ),
            )
            raster = rasterize_image(
                image_path,
                JobParams(
                    material="wood",
                    size_mm=50,
                    height_mm=30,
                    power=120,
                    feed_rate=900,
                    mode="raster",
                    pixel_size_mm=0.2,
                    lock_aspect_ratio=False,
                ),
            )

        self.assertAlmostEqual(outline.width_mm, 30.0)
        self.assertAlmostEqual(outline.height_mm, 30.0)
        self.assertAlmostEqual(raster.width_mm, 30.0, places=3)
        self.assertAlmostEqual(raster.height_mm, 30.0, places=3)

    def test_raster_keeps_requested_size_at_verified_fine_pixel_step(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = self.make_test_image(directory)
            params = JobParams(material="wood", size_mm=50, power=120, feed_rate=900, mode="raster", pixel_size_mm=0.05)

            raster = rasterize_image(image_path, params)

        self.assertEqual(raster.grid_width * raster.grid_height, 1_000_000)
        self.assertFalse(raster.scaled)
        self.assertEqual(raster.width_mm, 50)
        self.assertEqual(raster.height_mm, 50)

    def test_raster_rejects_excessive_pixel_grid_without_shrinking_dimensions(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = self.make_test_image(directory)
            params = JobParams(material="wood", size_mm=100, power=120, feed_rate=900, mode="raster", pixel_size_mm=0.05)

            with self.assertRaisesRegex(SafetyError, "Raster output is too large"):
                rasterize_image(image_path, params)

    def test_outline_generation_writes_nc_preview_and_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = self.make_test_image(directory)
            output_dir = Path(directory) / "out"

            bundle = generate_job(
                image_path,
                "test outline 20mm thickness 1mm",
                output_dir,
                output_format="nc",
                mode="outline",
                thickness_mm=1.0,
                task_type="engrave_logo",
            )
            summary = self.read_summary(bundle)

            self.assertTrue(bundle.gcode_path.exists())
            self.assertEqual(bundle.gcode_path.suffix, ".nc")
            self.assertTrue(bundle.preview_path.exists())
            self.assertTrue(bundle.processed_preview_path.exists())
            self.assertEqual(summary["contract_version"], 1)
            self.assertEqual(summary["mode"], "outline")
            self.assertEqual(summary["output_format"], "nc")
            self.assertEqual(summary["recommendation_status"], "single_recommendation")
            self.assertIn("trace", summary)
            self.assertIn("time_estimate", summary)
            self.assertEqual(summary["files"]["gcode"], str(bundle.gcode_path))

    def test_raster_generation_writes_gcode_preview_and_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = self.make_test_image(directory)
            output_dir = Path(directory) / "out"

            bundle = generate_job(
                image_path,
                "test raster 8mm thickness 1mm pixel 0.5mm",
                output_dir,
                output_format="gcode",
                mode="raster",
                thickness_mm=1.0,
                task_type="engrave_photo",
                dither_algorithm="floyd_steinberg",
                raster_output_strategy="segment",
            )
            summary = self.read_summary(bundle)

            self.assertTrue(bundle.gcode_path.exists())
            self.assertEqual(bundle.gcode_path.suffix, ".gcode")
            self.assertTrue(bundle.preview_path.exists())
            self.assertTrue(bundle.processed_preview_path.exists())
            self.assertEqual(summary["contract_version"], 1)
            self.assertEqual(summary["mode"], "raster")
            self.assertEqual(summary["output_format"], "gcode")
            self.assertIn("raster", summary)
            self.assertIn("time_estimate", summary)

    def test_raster_quality_strategy_scores_candidates_and_records_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "photo.png"
            image = Image.new("L", (40, 48), 245)
            draw = ImageDraw.Draw(image)
            for y_coord in range(48):
                shade = 40 + int(y_coord * 160 / 47)
                draw.line((8, y_coord, 31, y_coord), fill=shade)
            draw.ellipse((10, 6, 30, 28), fill=90)
            draw.rectangle((13, 30, 27, 45), fill=60)
            image.save(image_path)

            bundle = generate_job(
                image_path,
                "椴木 raster 20mm thickness 3mm",
                Path(directory) / "out-quality",
                output_format="gcode",
                mode="raster",
                thickness_mm=3.0,
                task_type="engrave_photo",
                raster_quality_strategy="quality",
            )
            summary = self.read_summary(bundle)

        profile = summary["auto_raster_profile"]
        self.assertEqual(profile["strategy"], "quality")
        self.assertIn(profile["image_type"], {"photo", "complex_photo"})
        self.assertLessEqual(len(profile["candidates"]), 6)
        self.assertEqual(profile["selected"]["raster_scan_direction"], "horizontal")
        self.assertEqual(profile["selected"]["pixel_size_mm"], 0.2)
        self.assertEqual(summary["raster"]["quality_profile"]["selected"], profile["selected"])

    def test_inspect_writes_summary_without_production_gcode(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = self.make_test_image(directory)
            output_dir = Path(directory) / "out"

            bundle = generate_job(
                image_path,
                "test outline 20mm thickness 1mm",
                output_dir,
                output_format="nc",
                mode="outline",
                thickness_mm=1.0,
                task_type="engrave_logo",
                inspect=True,
            )
            summary = self.read_summary(bundle)

            self.assertIsNone(bundle.gcode_path)
            self.assertIsNone(bundle.preview_path)
            self.assertTrue(bundle.processed_preview_path.exists())
            self.assertTrue(bundle.summary_path.exists())
            self.assertTrue(summary["inspect_only"])
            self.assertIsNone(summary["gcode_path"])

    def test_chinese_wood_alias_resolves_to_wood_material(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = self.make_test_image(directory)
            output_dir = Path(directory) / "out"

            bundle = generate_job(
                image_path,
                "椴木 outline 20mm thickness 3mm",
                output_dir,
                output_format="gcode",
                mode="outline",
                thickness_mm=3.0,
                task_type="engrave_logo",
            )
            summary = self.read_summary(bundle)

            self.assertEqual(summary["material"], "wood")
            self.assertEqual(summary["match_type"], "exact")

    def test_lasergrbl_material_library_schema_feeds_image_generator(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = self.make_test_image(directory)
            output_dir = Path(directory) / "out"
            library_path = Path(directory) / ".lasergrbl_materials.json"
            library_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "materials": {
                            "椴木": {
                                "aliases": ["wood", "basswood"],
                                "thicknesses": {
                                    "3": {
                                        "engrave": {
                                            "raster": {
                                                "laser_max_power": 380,
                                                "feed_rate": 1500,
                                                "passes": 1,
                                                "pixel_size_mm": 0.05,
                                                "source": "manual",
                                            },
                                            "outline": {
                                                "laser_max_power": 600,
                                                "feed_rate": 400,
                                                "passes": 1,
                                                "pixel_size_mm": 0.1,
                                                "source": "manual",
                                            },
                                        },
                                        "cut": {
                                            "laser_max_power": 1000,
                                            "feed_rate": 100,
                                            "passes": 1,
                                            "pixel_size_mm": 0.1,
                                            "source": "manual",
                                        },
                                    }
                                },
                            },
                            "钢": {"aliases": ["steel"], "thicknesses": {}},
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            records, used_builtin_only = load_material_records(library_path)
            match = match_material_record(
                JobParams(
                    material="wood",
                    size_mm=20,
                    power=1,
                    feed_rate=1,
                    mode="outline",
                    thickness_mm=3.0,
                    thickness_source="explicit",
                    task_type="engrave_logo",
                    task_type_source="explicit",
                ),
                records,
            )
            bundle = generate_job(
                image_path,
                "椴木 outline 20mm thickness 3mm",
                output_dir,
                output_format="gcode",
                mode="outline",
                material_library_path=library_path,
                thickness_mm=3.0,
                task_type="engrave_logo",
            )
            summary = self.read_summary(bundle)
            raster_bundle = generate_job(
                image_path,
                "椴木 raster 20mm thickness 3mm",
                Path(directory) / "out-raster",
                output_format="gcode",
                mode="raster",
                material_library_path=library_path,
                thickness_mm=3.0,
                task_type="engrave_photo",
                pixel_size_mm=0.2,
            )
            raster_summary = self.read_summary(raster_bundle)
            cut_bundle = generate_job(
                image_path,
                "椴木 cut 20mm thickness 3mm",
                Path(directory) / "out-cut",
                output_format="gcode",
                mode="outline",
                material_library_path=library_path,
                thickness_mm=3.0,
                task_type="cut_contour",
            )
            cut_summary = self.read_summary(cut_bundle)
            cut_gcode_exists = cut_bundle.gcode_path.is_file()
            cut_preview_exists = cut_bundle.preview_path.is_file()

        self.assertFalse(used_builtin_only)
        self.assertIsNotNone(match.record)
        self.assertEqual(match.record.material, "椴木")
        self.assertEqual(match.record.power, 600)
        self.assertEqual(match.record.speed, 400)
        self.assertEqual(match.record.confidence, "verified")
        self.assertEqual(summary["material"], "椴木")
        self.assertEqual(summary["power"], 600)
        self.assertEqual(summary["feed_rate"], 400)
        self.assertEqual(summary["parameter_source"], "verified")
        self.assertTrue(summary["can_send"])
        self.assertEqual(raster_summary["power"], 380)
        self.assertEqual(raster_summary["feed_rate"], 1500)
        self.assertEqual(raster_summary["raster"]["pixel_size_mm"], 0.2)
        self.assertEqual(cut_summary["feed_rate"], 100)
        self.assertTrue(cut_gcode_exists)
        self.assertTrue(cut_preview_exists)

    def test_preprocess_force_binary_outputs_thresholded_png(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "color_input.png"
            image = Image.new("RGB", (20, 20), "white")
            draw = ImageDraw.Draw(image)
            draw.rectangle((4, 4, 15, 15), fill=(25, 30, 35))
            image.save(image_path)

            result = prepare_processed_image(
                image_path,
                JobParams(material="wood", size_mm=20, power=120, feed_rate=900, mode="outline"),
                force_binary=True,
            )
            self.addCleanup(lambda: result.processed_path.unlink(missing_ok=True))

            with Image.open(result.processed_path) as processed:
                grayscale = processed.convert("L")
                values = set(
                    grayscale.get_flattened_data() if hasattr(grayscale, "get_flattened_data") else grayscale.getdata()
                )

        self.assertTrue(result.grayscale)
        self.assertTrue(result.binarized)
        self.assertIsNotNone(result.threshold)
        self.assertLessEqual(values, {0, 255})

    def test_auto_mode_medium_confidence_returns_raster_outline_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "medium_confidence.png"
            image = Image.new("L", (30, 30), 255)
            draw = ImageDraw.Draw(image)
            draw.rectangle((5, 5, 20, 20), fill=0)
            for index, value in enumerate(range(40, 52)):
                image.putpixel((21 + index % 3, 5 + index // 3), value)
            image.save(image_path)

            params, routing = resolve_job_mode(
                image_path,
                JobParams(material="wood", size_mm=30, power=120, feed_rate=900, mode="auto"),
            )

        self.assertEqual(params.mode, "raster")
        self.assertEqual(routing.recommendation_status, "candidate_selection_required")
        self.assertEqual([candidate.mode for candidate in routing.candidates], ["raster", "outline"])

    def test_vector_fill_hatch_zigzag_and_auto_behaviors(self):
        trace = TraceResult(
            paths=[[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)]],
            width_mm=10.0,
            height_mm=10.0,
            contour_count=1,
            point_count=5,
        )
        base = JobParams(material="wood", size_mm=10, power=120, feed_rate=900, fill_spacing_mm=2.0)

        hatch = apply_vector_fill(trace, replace(base, fill_strategy="hatch"))
        zigzag = apply_vector_fill(trace, replace(base, fill_strategy="zigzag"))
        automatic = apply_vector_fill(trace, replace(base, fill_strategy="auto"))

        self.assertEqual(hatch.fill_strategy, "hatch")
        self.assertGreater(hatch.fill_segment_count, 0)
        self.assertGreater(hatch.contour_count, trace.contour_count)
        self.assertEqual(zigzag.fill_strategy, "zigzag")
        self.assertGreater(zigzag.fill_segment_count, 0)
        self.assertEqual(automatic.fill_strategy, "none")
        self.assertIn("resolved to none", automatic.fill_warnings[0])

    def test_outline_gcode_flips_image_y_axis_to_machine_coordinates(self):
        trace = TraceResult(
            paths=[[(1.0, 0.0), (4.0, 0.0), (4.0, 8.0), (1.0, 8.0), (1.0, 0.0)]],
            width_mm=5.0,
            height_mm=8.0,
            contour_count=1,
            point_count=5,
        )
        params = JobParams(material="wood", size_mm=8, power=120, feed_rate=900, mode="outline")

        gcode, _stats = build_gcode_with_stats(params, trace)

        self.assertIn("G0 X1.000 Y8.000", gcode)
        self.assertIn("G1 X4.000 Y8.000 S120 F900", gcode)
        self.assertIn("G1 X4.000 Y0.000 S120 F900", gcode)

    def test_raster_dither_variants_resolve_to_binary_bitmaps(self):
        source = [[0.0, 64.0, 128.0, 192.0, 255.0], [255.0, 192.0, 128.0, 64.0, 0.0]]

        self.assertEqual(_resolve_dither_algorithm("auto"), "floyd_steinberg")
        for requested in ("threshold", "floyd_steinberg", "atkinson", "sierra_lite"):
            dithered = _dither([row[:] for row in source], _resolve_dither_algorithm(requested))
            self.assertEqual(len(dithered), len(source))
            self.assertTrue(all(value in {0, 255} for row in dithered for value in row))
        # value <= threshold (default 128): 127 and 128 are both black.
        self.assertEqual(_dither([[127.0, 128.0]], "threshold"), [[0, 0]])
        self.assertEqual(_dither([[127.0, 128.0, 129.0]], "threshold", threshold=128), [[0, 0, 255]])
        self.assertEqual(_dither([[0.0, 1.0]], "threshold", threshold=0), [[0, 255]])
        self.assertEqual(_dither([[254.0, 255.0]], "threshold", threshold=255), [[0, 0]])

    def test_no_dithering_prompt_selects_threshold(self):
        self.assertEqual(parse_job_params("raster no dithering", mode="raster").dither_algorithm, "threshold")
        self.assertEqual(parse_job_params("光栅 关闭抖动", mode="raster").dither_algorithm, "threshold")

    def test_structured_threshold_auto_and_boundaries(self):
        auto = parse_job_params("raster", mode="raster", threshold=-1)
        self.assertEqual(auto.threshold, -1)
        self.assertFalse(auto.manual_threshold)
        self.assertEqual(auto.dither_algorithm, "floyd_steinberg")

        zero = parse_job_params("raster", mode="raster", threshold=0)
        self.assertEqual(zero.threshold, 0)
        self.assertTrue(zero.manual_threshold)
        self.assertEqual(zero.dither_algorithm, "threshold")

        full = parse_job_params("raster", mode="raster", threshold=255)
        self.assertEqual(full.threshold, 255)
        self.assertTrue(full.manual_threshold)
        self.assertEqual(full.dither_algorithm, "threshold")

        with_auto_dither = parse_job_params("raster", mode="raster", threshold=100, dither_algorithm="auto")
        self.assertEqual(with_auto_dither.dither_algorithm, "threshold")
        self.assertTrue(with_auto_dither.manual_threshold)

        with self.assertRaises(ValueError):
            parse_job_params("raster", mode="raster", threshold=256)
        with self.assertRaises(ValueError):
            parse_job_params("raster", mode="raster", threshold=-2)

    def test_explicit_threshold_conflicts_with_error_diffusion(self):
        for algorithm in ("floyd_steinberg", "atkinson", "sierra_lite"):
            with self.assertRaises(ValueError):
                parse_job_params("raster", mode="raster", threshold=128, dither_algorithm=algorithm)

    def test_manual_threshold_and_dither_not_overwritten_by_auto_line_art(self):
        from core.ai_laser_gcode.raster import _maybe_apply_auto_line_art_strategy

        manual = parse_job_params("raster", mode="raster", threshold=90, dither_algorithm="threshold")
        kept = _maybe_apply_auto_line_art_strategy(manual, "line_art", [[0, 255], [255, 0]], 2, 2)
        self.assertEqual(kept.threshold, 90)
        self.assertEqual(kept.dither_algorithm, "threshold")
        self.assertTrue(kept.manual_threshold)

        dither_manual = parse_job_params("raster", mode="raster", dither_algorithm="atkinson")
        kept_dither = _maybe_apply_auto_line_art_strategy(dither_manual, "line_art", [[0, 255], [255, 0]], 2, 2)
        self.assertEqual(kept_dither.dither_algorithm, "atkinson")
        self.assertTrue(kept_dither.manual_dither)

    def test_darkest_region_resize_keeps_thin_black_stroke(self):
        from core.ai_laser_gcode.raster import _resize_darkest, _resize_nearest

        # 4x4 with a single dark column that nearest can miss when shrinking to 1x1 / 2x2 poorly.
        pixels = [
            [255, 255, 10, 255],
            [255, 255, 10, 255],
            [255, 255, 10, 255],
            [255, 255, 10, 255],
        ]
        nearest = _resize_nearest(pixels, 4, 4, 2, 2)
        darkest = _resize_darkest(pixels, 4, 4, 2, 2)
        # Darkest must still see the stroke (value 10) in the right column region.
        self.assertTrue(any(value <= 10 for row in darkest for value in row))
        self.assertLessEqual(min(value for row in darkest for value in row), min(value for row in nearest for value in row))

    def test_line_art_detail_upscale_respects_4x_and_working_pixel_cap(self):
        from core.ai_laser_gcode.raster import (
            MAX_DETAIL_UPSCALE,
            MAX_DETAIL_WORKING_PIXELS,
            _maybe_upscale_line_art,
        )

        params = parse_job_params(
            "test thickness 3mm",
            mode="raster",
            material="test",
            thickness_mm=3.0,
            size_mm=40,
            threshold=128,
            dither_algorithm="threshold",
            pixel_size_mm=0.1,
        )
        # Coarse 5x5 source vs fine target grid → needs upscale; hard-capped at 4x.
        source = [[0 if x == 2 else 255 for x in range(5)] for _ in range(5)]
        upscaled, new_w, new_h, applied = _maybe_upscale_line_art(source, 5, 5, params, enable=True)
        self.assertTrue(applied)
        self.assertEqual(new_w, 5 * MAX_DETAIL_UPSCALE)
        self.assertEqual(new_h, 5 * MAX_DETAIL_UPSCALE)
        self.assertEqual(len(upscaled), new_h)
        self.assertEqual(len(upscaled[0]), new_w)
        self.assertLessEqual(new_w * new_h, MAX_DETAIL_WORKING_PIXELS)

        disabled = _maybe_upscale_line_art(source, 5, 5, params, enable=False)
        self.assertEqual(disabled, (source, 5, 5, False))

        # Source already at/over the working-pixel cap → skip without changing physical size.
        oversized = _maybe_upscale_line_art([[0]], 2001, 2000, params, enable=True)
        self.assertEqual(oversized[1:], (2001, 2000, False))
        self.assertEqual(oversized[1] * oversized[2], 2001 * 2000)
        self.assertGreaterEqual(oversized[1] * oversized[2], MAX_DETAIL_WORKING_PIXELS)

    def test_line_art_threshold_raster_may_report_detail_upscale_strategy(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "tiny_line.png"
            image = Image.new("L", (8, 8), 255)
            draw = ImageDraw.Draw(image)
            draw.line((1, 4, 6, 4), fill=0, width=1)
            image.save(image_path)
            params = parse_job_params(
                "test thickness 3mm",
                mode="raster",
                material="test",
                thickness_mm=3.0,
                size_mm=20,
                threshold=128,
                dither_algorithm="threshold",
                pixel_size_mm=0.2,
            )
            line_art = rasterize_image(image_path, params, image_type="line_art")
            self.assertIn("darkest", line_art.resize_strategy)
            self.assertIn("detail_upscale", line_art.resize_strategy)
            # Physical output still follows resolved size/pixel step — not silently shrunk for the cap.
            self.assertGreater(line_art.width_mm, 0)
            self.assertGreater(line_art.height_mm, 0)

            photo = rasterize_image(image_path, params, image_type="photo")
            self.assertEqual(photo.resize_strategy, "nearest")
            self.assertNotIn("detail_upscale", photo.resize_strategy)

            diffusion = parse_job_params(
                "test thickness 3mm",
                mode="raster",
                material="test",
                thickness_mm=3.0,
                size_mm=20,
                dither_algorithm="floyd_steinberg",
                pixel_size_mm=0.2,
            )
            photo_dither = rasterize_image(image_path, diffusion, image_type="line_art")
            # Manual error-diffusion must not enter the threshold detail-upscale path.
            self.assertEqual(photo_dither.resize_strategy, "nearest")
            self.assertNotIn("detail_upscale", photo_dither.resize_strategy)

    def test_line_art_threshold_raster_reports_strategy_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "line.png"
            image = Image.new("L", (40, 40), 255)
            draw = ImageDraw.Draw(image)
            draw.line((5, 20, 35, 20), fill=0, width=2)
            draw.line((20, 5, 20, 35), fill=0, width=2)
            image.save(image_path)
            params = parse_job_params(
                "test thickness 3mm",
                mode="raster",
                material="test",
                thickness_mm=3.0,
                size_mm=20,
                threshold=128,
                dither_algorithm="threshold",
                pixel_size_mm=0.5,
            )
            result = rasterize_image(image_path, params, image_type="line_art")
            self.assertEqual(result.dither_algorithm, "threshold")
            self.assertEqual(result.threshold, 128)
            self.assertIn("darkest", result.resize_strategy)

    def test_error_diffusion_ignores_user_threshold_and_keeps_nearest(self):
        source = [[0.0, 64.0, 128.0, 192.0, 255.0]]
        from core.ai_laser_gcode.raster import _dither as dither_fn

        baseline = dither_fn([row[:] for row in source], "floyd_steinberg")
        with_threshold_arg = dither_fn([row[:] for row in source], "floyd_steinberg", threshold=0)
        self.assertEqual(baseline, with_threshold_arg)

    def test_generate_job_raster_summary_includes_threshold_resize_dither(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = self.make_test_image(temp_dir)
            bundle = generate_job(
                image_path,
                "test thickness 3mm",
                Path(temp_dir) / "out",
                mode="raster",
                material="test",
                thickness_mm=3.0,
                size_mm=20,
                threshold=100,
                dither_algorithm="threshold",
                pixel_size_mm=0.5,
            )
            summary = self.read_summary(bundle)
            self.assertIn("matched_thickness_mm", summary)
            self.assertIn("material_match_policy", summary)
            self.assertIn("send_policy", summary)
            self.assertEqual(summary.get("threshold"), 100)
            self.assertEqual(summary.get("dither_algorithm"), "threshold")
            self.assertIsNotNone(summary.get("resize_strategy"))
            self.assertEqual(summary["raster"].get("threshold"), 100)
            self.assertEqual(summary["raster"].get("dither_algorithm"), "threshold")
            self.assertIsNotNone(summary["raster"].get("resize_strategy"))

    def test_path_optimizer_preserves_geometry_and_reports_strategy(self):
        paths = [
            [(30.0, 30.0), (34.0, 30.0), (34.0, 34.0), (30.0, 34.0), (30.0, 30.0)],
            [(2.0, 2.0), (6.0, 2.0), (6.0, 6.0), (2.0, 6.0), (2.0, 2.0)],
        ]
        trace = TraceResult(paths=paths, width_mm=40.0, height_mm=40.0, contour_count=2, point_count=10)

        optimized = optimize_trace_paths(trace, size_mm=40.0)

        original_points = sorted(point for path in paths for point in path[:-1])
        optimized_points = sorted(point for path in optimized.trace.paths for point in path[:-1])
        self.assertEqual(optimized_points, original_points)
        self.assertIn(optimized.stats.strategy, {"direction_order", "nearest_neighbor"})
        self.assertEqual(optimized.stats.path_count, 2)

    def test_arc_output_uses_g23_when_fit_succeeds_and_g1_when_it_fails(self):
        params = JobParams(
            material="wood",
            size_mm=20,
            power=120,
            feed_rate=900,
            arc_output=True,
            firmware_supports_arc=True,
            arc_tolerance_mm=0.05,
        )
        arc_trace = TraceResult(
            paths=[[(1.0, 0.0), (0.924, 0.383), (0.707, 0.707), (0.383, 0.924), (0.0, 1.0)]],
            width_mm=1.0,
            height_mm=1.0,
            contour_count=1,
            point_count=5,
        )
        jagged_trace = TraceResult(
            paths=[[(0.0, 0.0), (1.0, 0.0), (2.0, 1.0), (3.0, 0.0)]],
            width_mm=3.0,
            height_mm=1.0,
            contour_count=1,
            point_count=4,
        )

        arc_gcode, arc_stats = build_gcode_with_stats(params, arc_trace)
        jagged_gcode, jagged_stats = build_gcode_with_stats(params, jagged_trace)

        self.assertIn("G2", arc_gcode)
        self.assertEqual(arc_stats.arc_count, 1)
        self.assertIn("G1", jagged_gcode)
        self.assertEqual(jagged_stats.arc_count, 0)
        self.assertGreater(jagged_stats.fallback_segment_count, 0)

    def test_calibration_matrix_success_rating_writes_verified_material_record(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "out"
            library_path = Path(directory) / "materials.json"
            bundle = generate_calibration_matrix(
                "wood",
                output_dir,
                output_format="gcode",
                task_type="engrave_photo",
                thickness_mm=3.0,
                power=120,
                speed=1200,
            )

            summary = self.read_summary(bundle)
            saved = save_calibration_result(bundle.summary_path, "B2", "good", library_path)
            library = json.loads(library_path.read_text(encoding="utf-8"))

        self.assertFalse(summary["can_send"])
        self.assertEqual(summary["calibration_matrix"]["rows"], 3)
        self.assertEqual(len(summary["calibration_matrix"]["cells"]), 9)
        self.assertTrue(saved)
        self.assertEqual(library["materials"][0]["confidence"], "verified")
        self.assertEqual(library["materials"][0]["power"], 120)
        self.assertEqual(library["materials"][0]["speed"], 1200)

    def test_ai_assist_validates_enums_confidence_and_redacts_failed_client_secret(self):
        suggestion = parse_ai_preprocess_suggestion(
            {
                "preprocess": {"invert": "maybe", "cleanup_background": "white"},
                "mode_recommendation": "unsafe-mode",
                "raster": {"dithering": "unknown", "scan_direction": "vertical"},
                "outline": {"trace_algorithm": "vector", "fill_strategy": "zigzag", "arc_output": "bad"},
                "confidence": 2.0,
            }
        )

        with tempfile.TemporaryDirectory() as directory:
            image_path = self.make_test_image(directory)
            config = AiProviderConfig("https://example.com", "secret-key", "vision-model")

            def failing_client(_config, _image_path, _prompt):
                raise RuntimeError("provider rejected secret-key")

            result = run_ai_assist(
                image_path,
                "analyze",
                enabled=True,
                allow_image_upload=True,
                config=config,
                client=failing_client,
            )

        self.assertFalse(suggestion.valid)
        self.assertGreaterEqual(len(suggestion.validation_warnings), 3)
        self.assertIsNone(suggestion.confidence)
        self.assertEqual(result.status, "failed_fallback_local")
        self.assertIn("[redacted]", result.fallback_reason)
        self.assertNotIn("secret-key", result.fallback_reason)

    def test_ai_assist_accepts_threshold_for_line_art_raster(self):
        suggestion = parse_ai_preprocess_suggestion(
            {
                "preprocess": {"invert": "no", "cleanup_background": "none"},
                "mode_recommendation": "raster",
                "raster": {"dithering": "threshold", "scan_direction": "horizontal"},
                "outline": {"trace_algorithm": "vector", "fill_strategy": "none", "arc_output": "off"},
                "confidence": 0.9,
            }
        )

        self.assertTrue(suggestion.valid, suggestion.validation_warnings)
        self.assertEqual(suggestion.raster["dithering"], "threshold")

    def test_ai_suggested_preprocess_applies_invert_cleanup_and_threshold(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "gray_input.png"
            image = Image.new("L", (12, 12), 255)
            draw = ImageDraw.Draw(image)
            draw.rectangle((3, 3, 8, 8), fill=60)
            image.save(image_path)
            ai_result = AiAssistResult(
                enabled=True,
                image_upload_allowed=True,
                provider_configured=True,
                status="used",
                recommendations={
                    "preprocess": {"invert": "yes", "cleanup_background": "white"},
                    "mode_recommendation": "outline",
                    "raster": {"dithering": "auto", "scan_direction": "auto"},
                    "outline": {"trace_algorithm": "vector", "fill_strategy": "none", "arc_output": "off"},
                    "confidence": 0.8,
                },
            )

            result = prepare_processed_image(
                image_path,
                JobParams(material="wood", size_mm=20, power=120, feed_rate=900, mode="outline"),
                ai_result=ai_result,
            )
            self.addCleanup(lambda: result.processed_path.unlink(missing_ok=True))

            with Image.open(result.processed_path) as processed:
                grayscale = processed.convert("L")
                values = set(
                    grayscale.get_flattened_data() if hasattr(grayscale, "get_flattened_data") else grayscale.getdata()
                )

        self.assertEqual(result.source, "ai_suggested")
        self.assertTrue(result.invert)
        self.assertEqual(result.cleanup_background, "white")
        self.assertEqual(result.threshold, 128)
        self.assertTrue(result.binarized)
        self.assertLessEqual(values, {0, 255})

    def test_otsu_threshold_separates_dark_foreground_from_light_background(self):
        image = Image.new("L", (20, 10), 230)
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 9, 9), fill=40)

        threshold = _otsu_threshold(image)

        self.assertGreaterEqual(threshold, 40)
        self.assertLess(threshold, 230)

    def test_router_metrics_report_foreground_components_and_edges(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "metrics.png"
            image = Image.new("L", (12, 12), 255)
            draw = ImageDraw.Draw(image)
            draw.rectangle((1, 1, 4, 4), fill=0)
            draw.rectangle((8, 8, 10, 10), fill=0)
            image.save(image_path)

            metrics = analyze_image(image_path)

        self.assertEqual(metrics.component_count, 2)
        self.assertGreater(metrics.foreground_ratio, 0)
        self.assertGreater(metrics.binary_ratio, 0.95)
        self.assertGreater(metrics.edge_density, 0)

    def test_trace_vector_extracts_closed_boundaries_by_component_area(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "trace.png"
            image = Image.new("L", (24, 24), 255)
            draw = ImageDraw.Draw(image)
            draw.rectangle((2, 2, 12, 12), fill=0)
            draw.rectangle((18, 18, 21, 21), fill=0)
            image.save(image_path)

            trace = trace_image(
                image_path,
                JobParams(material="wood", size_mm=24, power=120, feed_rate=900, vector_simplify_factor=0.25),
            )

        self.assertEqual(trace.contour_count, 2)
        self.assertEqual(trace.trace_algorithm, "vector")
        self.assertGreater(trace.raw_point_count, trace.point_count)
        self.assertTrue(all(path[0] == path[-1] for path in trace.paths))
        self.assertGreater(_path_area(trace.paths[0]), _path_area(trace.paths[1]))

    def test_raster_auto_uses_scanline_for_dense_checkerboard(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "checker.png"
            image = Image.new("L", (10, 4), 255)
            for y_coord in range(4):
                for x_coord in range(10):
                    if (x_coord + y_coord) % 2 == 0:
                        image.putpixel((x_coord, y_coord), 0)
            image.save(image_path)

            raster = rasterize_image(
                image_path,
                JobParams(
                    material="wood",
                    size_mm=10,
                    power=120,
                    feed_rate=900,
                    mode="raster",
                    pixel_size_mm=1.0,
                    overscan_mm=0.0,
                    raster_output_strategy="auto",
                ),
            )

        self.assertEqual(raster.scan_direction, "horizontal")
        self.assertEqual(raster.output_strategy, "scanline")
        self.assertEqual(raster.output_strategy_source, "auto")
        self.assertTrue(raster.snake_scan)
        self.assertGreater(raster.scanline_transition_count, raster.scanline_count)

    def test_raster_auto_chooses_vertical_when_it_reduces_segments(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "vertical.png"
            image = Image.new("L", (4, 4), 255)
            for y_coord in range(4):
                image.putpixel((1, y_coord), 0)
            image.save(image_path)

            raster = rasterize_image(
                image_path,
                JobParams(
                    material="wood",
                    size_mm=4,
                    power=120,
                    feed_rate=900,
                    mode="raster",
                    pixel_size_mm=1.0,
                    overscan_mm=0.0,
                ),
            )

        self.assertEqual(raster.scan_direction, "vertical")
        self.assertEqual(raster.scan_direction_source, "auto")
        self.assertLess(raster.vertical_segment_count, raster.horizontal_segment_count)

    def test_raster_scanline_gcode_switches_laser_power_inline(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "scanline.png"
            image = Image.new("L", (6, 2), 255)
            for x_coord in (0, 2, 4):
                image.putpixel((x_coord, 0), 0)
            for x_coord in (1, 3, 5):
                image.putpixel((x_coord, 1), 0)
            image.save(image_path)
            params = JobParams(
                material="wood",
                size_mm=6,
                power=123,
                feed_rate=900,
                mode="raster",
                pixel_size_mm=1.0,
                overscan_mm=0.0,
                raster_output_strategy="scanline",
            )

            raster = rasterize_image(image_path, params)
            gcode = build_raster_gcode(params, raster)

        self.assertEqual(raster.output_strategy, "scanline")
        self.assertTrue(raster.snake_scan)
        self.assertIn("S0", gcode)
        self.assertIn("S123", gcode)
        self.assertIn("G1 X", gcode)
        self.assertIn("Y1.000", gcode)
        self.assertIn("Y0.000", gcode)

    def test_preview_png_is_valid_png_and_contains_drawn_pixels(self):
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "preview.png"
            trace = TraceResult(
                paths=[[(0.0, 0.0), (10.0, 10.0)]],
                width_mm=10.0,
                height_mm=10.0,
                contour_count=1,
                point_count=2,
            )

            write_preview_png(trace, output_path, size_px=32)

            data = output_path.read_bytes()
            with Image.open(output_path) as preview:
                grayscale = preview.convert("L")
                pixels = list(
                    grayscale.get_flattened_data() if hasattr(grayscale, "get_flattened_data") else grayscale.getdata()
                )

        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertIn(0, pixels)
        self.assertIn(255, pixels)

    def test_material_group_fallback_matches_thickness_independent_engraving_record(self):
        match = match_material_record(
            JobParams(
                material="cardstock",
                size_mm=20,
                power=80,
                feed_rate=1200,
                thickness_mm=5.0,
                thickness_source="explicit",
                task_type="engrave_logo",
                task_type_source="explicit",
            ),
            BUILTIN_RECORDS,
        )

        self.assertEqual(match.match_type, "material_group_fallback")
        self.assertEqual(match.record.material, "paper")
        self.assertTrue(match.record.thickness_independent)

    def test_nearest_engrave_matches_same_material_nearest_thickness(self):
        match = match_material_record(
            JobParams(
                material="cowhide_leather",
                size_mm=20,
                power=1,
                feed_rate=1,
                thickness_mm=3.0,
                thickness_source="explicit",
                task_type="engrave_photo",
                task_type_source="explicit",
                material_match_policy="nearest_engrave",
            ),
            BUILTIN_RECORDS,
        )
        self.assertEqual(match.match_type, "nearest_thickness")
        self.assertIsNotNone(match.record)
        self.assertEqual(match.record.material, "cowhide_leather")
        self.assertEqual(match.record.thickness_mm, 1.5)

    def test_nearest_engrave_does_not_apply_to_cut_contour(self):
        match = match_material_record(
            JobParams(
                material="cowhide_leather",
                size_mm=20,
                power=1,
                feed_rate=1,
                thickness_mm=3.0,
                thickness_source="explicit",
                task_type="cut_contour",
                task_type_source="explicit",
                material_match_policy="nearest_engrave",
            ),
            BUILTIN_RECORDS,
        )
        self.assertEqual(match.match_type, "none")
        self.assertIsNone(match.record)

    def test_exact_only_policy_rejects_non_exact_leather_thickness(self):
        match = match_material_record(
            JobParams(
                material="cowhide_leather",
                size_mm=20,
                power=1,
                feed_rate=1,
                thickness_mm=3.0,
                thickness_source="explicit",
                task_type="engrave_photo",
                task_type_source="explicit",
                material_match_policy="exact_only",
            ),
            BUILTIN_RECORDS,
        )
        self.assertEqual(match.match_type, "none")
        self.assertIsNone(match.record)

    def test_draw_policy_nearest_leather_allows_confirm_send_with_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = self.make_test_image(directory)
            output_dir = Path(directory) / "out"
            bundle = generate_job(
                image_path,
                "皮革 raster 20mm thickness 3mm",
                output_dir,
                output_format="gcode",
                mode="raster",
                thickness_mm=3.0,
                task_type="engrave_photo",
                material_match_policy="nearest_engrave",
                send_policy="confirmed_material_record",
                pixel_size_mm=0.2,
            )
            summary = self.read_summary(bundle)

        self.assertEqual(summary["match_type"], "nearest_thickness")
        self.assertEqual(summary["thickness_mm"], 3.0)
        self.assertEqual(summary["matched_thickness_mm"], 1.5)
        self.assertTrue(summary["can_send"])
        self.assertTrue(summary["requires_sample_test"])
        self.assertEqual(summary["material_match_policy"], "nearest_engrave")
        self.assertEqual(summary["send_policy"], "confirmed_material_record")
        self.assertEqual(summary["power"], 300)
        self.assertEqual(summary["feed_rate"], 800)
        warnings = summary.get("warnings") or []
        self.assertTrue(any("最近厚度" in item or "1.5" in item for item in warnings))

    def test_draw_policy_cut_without_exact_thickness_cannot_send(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = self.make_test_image(directory)
            output_dir = Path(directory) / "out"
            bundle = generate_job(
                image_path,
                "皮革 cut 20mm thickness 3mm",
                output_dir,
                output_format="gcode",
                mode="outline",
                thickness_mm=3.0,
                task_type="cut_contour",
                material_match_policy="nearest_engrave",
                send_policy="confirmed_material_record",
            )
            summary = self.read_summary(bundle)

        self.assertEqual(summary["match_type"], "none")
        self.assertFalse(summary["can_send"])
        self.assertIsNone(summary.get("matched_thickness_mm"))

    def test_default_policy_keeps_strict_non_exact_block(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = self.make_test_image(directory)
            output_dir = Path(directory) / "out"
            bundle = generate_job(
                image_path,
                "皮革 raster 20mm thickness 3mm",
                output_dir,
                output_format="gcode",
                mode="raster",
                thickness_mm=3.0,
                task_type="engrave_photo",
                pixel_size_mm=0.2,
            )
            summary = self.read_summary(bundle)

        self.assertEqual(summary["match_type"], "none")
        self.assertFalse(summary["can_send"])
        self.assertEqual(summary["material_match_policy"], "exact_only")
        self.assertEqual(summary["send_policy"], "verified_only")

    def test_structured_acrylic_material_uses_local_library_params(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = self.make_test_image(directory)
            output_dir = Path(directory) / "out"
            library_path = Path(directory) / "materials.json"
            library_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "materials": {
                            "亚克力": {
                                "aliases": ["acrylic", "plexiglass", "有机玻璃"],
                                "thicknesses": {
                                    "3": {
                                        "engrave": {
                                            "raster": {
                                                "laser_max_power": 340,
                                                "feed_rate": 1600,
                                                "passes": 1,
                                                "pixel_size_mm": 0.1,
                                            }
                                        }
                                    }
                                },
                            }
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            bundle = generate_job(
                image_path,
                "hand drawn sketch laser raster engraving",
                output_dir,
                output_format="gcode",
                mode="raster",
                material="亚克力",
                material_library_path=library_path,
                thickness_mm=3.0,
                task_type="engrave_photo",
                material_match_policy="nearest_engrave",
                send_policy="confirmed_material_record",
                pixel_size_mm=0.2,
            )
            summary = self.read_summary(bundle)

        self.assertEqual(summary["material"], "亚克力")
        self.assertEqual(summary["match_type"], "exact")
        self.assertEqual(summary["power"], 340)
        self.assertEqual(summary["feed_rate"], 1600)
        self.assertTrue(summary["can_send"])
        self.assertNotEqual(summary["material"], "test")

    def test_explicit_unknown_material_does_not_become_test(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = self.make_test_image(directory)
            output_dir = Path(directory) / "out"
            library_path = Path(directory) / "materials.json"
            library_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "materials": {
                            "钢": {"aliases": ["steel", "不锈钢"], "thicknesses": {}},
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            bundle = generate_job(
                image_path,
                "hand drawn sketch laser raster engraving",
                output_dir,
                output_format="gcode",
                mode="raster",
                material="钢",
                material_library_path=library_path,
                thickness_mm=3.0,
                task_type="engrave_photo",
                material_match_policy="nearest_engrave",
                send_policy="confirmed_material_record",
                pixel_size_mm=0.2,
            )
            summary = self.read_summary(bundle)

        self.assertEqual(summary["material"], "钢")
        self.assertNotEqual(summary["material"], "test")
        self.assertEqual(summary["match_type"], "none")
        self.assertFalse(summary["can_send"])

    def test_detail_upscale_reachable_for_line_art_not_photo(self):
        from core.ai_laser_gcode.raster import (
            MAX_DETAIL_UPSCALE,
            MAX_DETAIL_WORKING_PIXELS,
            _maybe_upscale_line_art,
            apply_line_art_detail_upscale,
            rasterize_image,
        )

        # Small line-art grid should be allowed to upscale when physical step is finer.
        line_pixels = [[255] * 8 for _ in range(8)]
        for y in range(8):
            line_pixels[y][3] = 0
        params = JobParams(
            material="test",
            size_mm=20,
            power=100,
            feed_rate=900,
            mode="raster",
            pixel_size_mm=0.2,
            dither_algorithm="threshold",
            threshold=128,
            manual_threshold=True,
            manual_dither=True,
        )
        upscaled, w, h, did = _maybe_upscale_line_art(line_pixels, 8, 8, params, enable=True)
        self.assertTrue(did)
        self.assertLessEqual(w / 8, MAX_DETAIL_UPSCALE)
        self.assertLessEqual(w * h, MAX_DETAIL_WORKING_PIXELS)
        self.assertGreater(w * h, 8 * 8)

        # Source already over working-pixel cap: skip upscale (no silent physical resize).
        huge_w, huge_h = 2500, 2000
        self.assertGreater(huge_w * huge_h, MAX_DETAIL_WORKING_PIXELS)
        huge_pixels = [[255]]  # placeholder content not used when size exceeds cap
        # Call with synthetic dimensions via enable path using width*height check first.
        skipped, sw, sh, did_skip = _maybe_upscale_line_art(
            [[0, 255], [255, 0]],
            huge_w,
            huge_h,
            params,
            enable=True,
        )
        self.assertFalse(did_skip)
        self.assertEqual((sw, sh), (huge_w, huge_h))

        with tempfile.TemporaryDirectory() as temp_dir:
            # Photo-like continuous gray must not use darkest_region.
            photo_path = Path(temp_dir) / "photo.png"
            photo = Image.new("L", (32, 32))
            photo.putdata([int(i * 255 / 1023) for i in range(32 * 32)])
            photo.save(photo_path)
            photo_params = parse_job_params(
                "test thickness 3mm",
                mode="raster",
                material="test",
                thickness_mm=3.0,
                size_mm=20,
                dither_algorithm="floyd_steinberg",
                pixel_size_mm=0.5,
            )
            photo_result = rasterize_image(photo_path, photo_params, image_type="photo")
            self.assertEqual(photo_result.resize_strategy, "nearest")
            self.assertNotIn("darkest", photo_result.resize_strategy)
            self.assertIsNone(photo_result.threshold)

            # Line-art threshold path reports darkest strategy.
            line_path = Path(temp_dir) / "line.png"
            line = Image.new("L", (16, 16), 255)
            draw = ImageDraw.Draw(line)
            draw.line((2, 8, 14, 8), fill=0, width=1)
            line.save(line_path)
            line_params = parse_job_params(
                "test thickness 3mm",
                mode="raster",
                material="test",
                thickness_mm=3.0,
                size_mm=20,
                threshold=128,
                dither_algorithm="threshold",
                pixel_size_mm=0.5,
            )
            line_result = rasterize_image(line_path, line_params, image_type="line_art")
            self.assertEqual(line_result.dither_algorithm, "threshold")
            self.assertIn("darkest", line_result.resize_strategy)

            # Shared helper for outline force-binary path: photo skipped, line-art may upscale.
            photo_out, photo_up = apply_line_art_detail_upscale(photo_path, line_params, image_type="photo")
            self.assertFalse(photo_up)
            self.assertEqual(photo_out, photo_path)

    def test_generate_job_line_art_threshold_strategy_and_photo_isolation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            line_path = Path(temp_dir) / "thin_line.png"
            image = Image.new("L", (24, 24), 255)
            draw = ImageDraw.Draw(image)
            draw.line((2, 12, 22, 12), fill=0, width=1)
            draw.line((12, 2, 12, 22), fill=0, width=1)
            image.save(line_path)

            bundle = generate_job(
                line_path,
                "test thickness 3mm",
                Path(temp_dir) / "out_line",
                mode="raster",
                material="test",
                thickness_mm=3.0,
                size_mm=20,
                threshold=128,
                dither_algorithm="threshold",
                pixel_size_mm=0.5,
            )
            summary = self.read_summary(bundle)
            self.assertEqual(summary["threshold"], 128)
            self.assertEqual(summary["dither_algorithm"], "threshold")
            self.assertIsNotNone(summary.get("resize_strategy"))
            self.assertIn("darkest", summary["resize_strategy"])
            self.assertEqual(summary["raster"]["threshold"], 128)
            self.assertIn("matched_thickness_mm", summary)
            self.assertIn("material_match_policy", summary)
            self.assertIn("send_policy", summary)

            photo_path = Path(temp_dir) / "photo_grad.png"
            photo = Image.new("L", (24, 24))
            photo.putdata([(x * 10 + y * 7) % 256 for y in range(24) for x in range(24)])
            photo.save(photo_path)
            photo_bundle = generate_job(
                photo_path,
                "test thickness 3mm",
                Path(temp_dir) / "out_photo",
                mode="raster",
                material="test",
                thickness_mm=3.0,
                size_mm=20,
                dither_algorithm="floyd_steinberg",
                pixel_size_mm=0.5,
            )
            photo_summary = self.read_summary(photo_bundle)
            self.assertEqual(photo_summary["dither_algorithm"], "floyd_steinberg")
            self.assertEqual(photo_summary.get("resize_strategy"), "nearest")
            self.assertIsNone(photo_summary.get("threshold"))

    def test_auto_raster_candidate_summary_includes_strategy_fields(self):
        from core.ai_laser_gcode.raster_quality import _candidate_summary
        from core.ai_laser_gcode.models import RasterResult

        raster = RasterResult(
            segments=[],
            width_mm=10,
            height_mm=10,
            grid_width=20,
            grid_height=20,
            pixel_size_mm=0.5,
            overscan_mm=1.0,
            scaled=False,
            scale_factor=1.0,
            work_area_width_mm=100,
            work_area_height_mm=100,
            dither_algorithm="threshold",
            threshold=120,
            resize_strategy="darkest_region",
        )
        summary = _candidate_summary(
            {
                "pixel_size_mm": 0.5,
                "raster_scan_direction": "horizontal",
                "estimated_seconds": 12.3,
                "segment_count": 4,
                "scanline_transition_count": 2,
                "grid_cells": 400,
                "score": 0.9,
                "raster": raster,
            }
        )
        self.assertEqual(summary["threshold"], 120)
        self.assertEqual(summary["resize_strategy"], "darkest_region")
        self.assertEqual(summary["dither_algorithm"], "threshold")

    def test_error_diffusion_bitmap_baselines_stable_across_algorithms(self):
        source = [
            [0.0, 64.0, 128.0, 192.0, 255.0],
            [255.0, 192.0, 128.0, 64.0, 0.0],
            [40.0, 80.0, 120.0, 160.0, 200.0],
        ]
        for algorithm in ("floyd_steinberg", "atkinson", "sierra_lite"):
            baseline = _dither([row[:] for row in source], algorithm)
            again = _dither([row[:] for row in source], algorithm, threshold=0)
            self.assertEqual(baseline, again)
            self.assertTrue(all(value in {0, 255} for row in baseline for value in row))

    def test_generate_job_outline_force_binary_can_detail_upscale_line_art(self):
        from core.ai_laser_gcode.raster import apply_line_art_detail_upscale

        with tempfile.TemporaryDirectory() as temp_dir:
            line_path = Path(temp_dir) / "outline_line.png"
            image = Image.new("L", (10, 10), 255)
            draw = ImageDraw.Draw(image)
            draw.rectangle((2, 2, 7, 7), outline=0)
            image.save(line_path)

            params = parse_job_params(
                "test thickness 3mm",
                mode="outline",
                material="test",
                thickness_mm=3.0,
                size_mm=40,
                pixel_size_mm=0.2,
                threshold=128,
                dither_algorithm="threshold",
            )
            upscaled_path, did = apply_line_art_detail_upscale(line_path, params, image_type="line_art")
            self.assertTrue(did)
            self.assertNotEqual(upscaled_path, line_path)
            with Image.open(upscaled_path) as upscaled:
                self.assertGreater(upscaled.size[0] * upscaled.size[1], 10 * 10)
            upscaled_path.unlink(missing_ok=True)

            photo_path = Path(temp_dir) / "outline_photo.png"
            photo = Image.new("L", (10, 10))
            photo.putdata([((x + y) * 13) % 256 for y in range(10) for x in range(10)])
            photo.save(photo_path)
            same_path, photo_up = apply_line_art_detail_upscale(photo_path, params, image_type="photo")
            self.assertFalse(photo_up)
            self.assertEqual(same_path, photo_path)

            # End-to-end outline job still succeeds with force-binary + optional upscale hook.
            bundle = generate_job(
                line_path,
                "test thickness 3mm",
                Path(temp_dir) / "out_outline",
                mode="outline",
                material="test",
                thickness_mm=3.0,
                size_mm=20,
                threshold=128,
            )
            summary = self.read_summary(bundle)
            self.assertEqual(summary["mode"], "outline")
            self.assertTrue(bundle.gcode_path and bundle.gcode_path.exists())


def _path_area(path):
    xs = [point[0] for point in path]
    ys = [point[1] for point in path]
    return (max(xs) - min(xs)) * (max(ys) - min(ys))


if __name__ == "__main__":
    unittest.main()
