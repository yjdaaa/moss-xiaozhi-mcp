import os
import shutil
import unittest
import uuid
from unittest.mock import patch

import numpy as np

from tools import laser_grbl_tool


class FakeMcp:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorator(func):
            self.tools[func.__name__] = func
            return func

        return decorator


class LaserGrblToolImageToGcodeTests(unittest.TestCase):
    def make_temp_dir(self):
        base_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", ".tmp-test")
        )
        os.makedirs(base_dir, exist_ok=True)

        temp_dir = os.path.join(base_dir, f"case-{uuid.uuid4().hex}")
        os.makedirs(temp_dir, exist_ok=False)
        self.addCleanup(shutil.rmtree, temp_dir, ignore_errors=True)
        return temp_dir

    def test_resolve_port_prefers_explicit_port(self):
        with patch.object(laser_grbl_tool, "DEFAULT_GRBL_PORT", "COM3"):
            self.assertEqual(laser_grbl_tool._resolve_port("COM14"), "COM14")

    def test_resolve_port_falls_back_to_default_port(self):
        with patch.object(laser_grbl_tool, "DEFAULT_GRBL_PORT", "COM3"):
            self.assertEqual(laser_grbl_tool._resolve_port(""), "COM3")

    def test_connect_grbl_serial_skips_auto_detect_when_port_is_configured(self):
        class FakeSerial:
            def write(self, _):
                return None

            def reset_input_buffer(self):
                return None

        class FakeSerialModule:
            SerialException = Exception

            def Serial(self, port, baudrate, timeout=1):
                self.args = (port, baudrate, timeout)
                return FakeSerial()

        class FakeListPorts:
            def comports(self):
                return []

        serial_module = FakeSerialModule()
        with (
            patch.object(laser_grbl_tool, "DEFAULT_GRBL_PORT", "COM3"),
            patch.object(laser_grbl_tool, "_auto_detect_grbl_port") as detect_mock,
            patch.object(laser_grbl_tool.time, "sleep"),
        ):
            ser, connected_port, error = laser_grbl_tool._connect_grbl_serial(
                "", 115200, serial_module, FakeListPorts()
            )

        self.assertIsInstance(ser, FakeSerial)
        self.assertEqual(connected_port, "COM3")
        self.assertIsNone(error)
        self.assertEqual(serial_module.args, ("COM3", 115200, 1))
        detect_mock.assert_not_called()

    def test_grayscale_mode_maps_dark_pixels_to_high_power(self):
        image = np.array([[0, 255]], dtype=np.uint8)

        with patch.object(laser_grbl_tool, "_resize_image", return_value=(image, None)):
            gcode, stats, error = laser_grbl_tool.generate_gcode_from_grayscale(
                image,
                width_mm=2,
                height_mm=1,
                pixel_size_mm=1,
                laser_min_power=0,
                laser_max_power=1000,
                bidirectional=False,
            )

        self.assertIsNone(error)
        self.assertEqual(stats["image_width_px"], 2)
        self.assertIn("G92 X0 Y0 Z0", gcode)
        self.assertLess(gcode.index("G92 X0 Y0 Z0"), gcode.index("M4 S0"))
        self.assertIn("G1 X1 Y0 S1000", gcode)
        self.assertNotIn("G1 X1 Y0 S0", gcode)
        self.assertTrue(any("S0" in line for line in gcode), gcode)

    def test_threshold_mode_outputs_binary_power(self):
        image = np.array([[100, 200]], dtype=np.uint8)

        with patch.object(laser_grbl_tool, "_resize_image", return_value=(image, None)):
            gcode, _, error = laser_grbl_tool.generate_gcode_from_grayscale(
                image,
                width_mm=2,
                height_mm=1,
                pixel_size_mm=1,
                laser_min_power=0,
                laser_max_power=500,
                threshold=128,
                bidirectional=False,
            )

        self.assertIsNone(error)
        self.assertIn("G1 X1 Y0 S500", gcode)
        self.assertNotIn("G1 X1 Y0 S0", gcode)

    def test_threshold_mode_merges_adjacent_dark_pixels(self):
        image = np.array([[100, 100, 200]], dtype=np.uint8)

        with patch.object(laser_grbl_tool, "_resize_image", return_value=(image, None)):
            gcode, _, error = laser_grbl_tool.generate_gcode_from_grayscale(
                image,
                width_mm=3,
                height_mm=1,
                pixel_size_mm=1,
                laser_min_power=0,
                laser_max_power=500,
                threshold=128,
                bidirectional=False,
            )

        self.assertIsNone(error)
        self.assertIn("G0 X0 Y0", gcode)
        self.assertIn("G1 X2 Y0 S500", gcode)
        self.assertNotIn("G1 X0 Y0 S500", gcode)
        self.assertNotIn("G1 X2 Y0 S0", gcode)

    def test_raster_scanline_keeps_single_continuous_row_for_separated_runs(self):
        image = np.array([[0, 255, 0]], dtype=np.uint8)

        with patch.object(laser_grbl_tool, "_resize_image", return_value=(image, None)):
            gcode, stats, error = laser_grbl_tool.generate_gcode_from_grayscale(
                image,
                width_mm=3,
                height_mm=1,
                pixel_size_mm=1,
                laser_min_power=0,
                laser_max_power=500,
                threshold=128,
                bidirectional=False,
                overscan_mm=1,
                raster_scan_direction="horizontal",
            )

        self.assertIsNone(error)
        self.assertEqual(stats["raster_output_strategy"], "scanline")
        self.assertEqual(stats["active_scanline_count"], 1)
        self.assertEqual(gcode.count("G0 X0 Y0"), 1)
        self.assertIn("G1 X1 Y0 S500", gcode)
        self.assertIn("G1 X2 Y0 S0", gcode)
        self.assertIn("G1 X3 Y0 S500", gcode)
        self.assertIn("G1 X4 Y0 S0", gcode)

    def test_raster_y_axis_maps_top_image_row_to_larger_machine_y(self):
        image = np.array(
            [
                [0, 255],
                [255, 0],
            ],
            dtype=np.uint8,
        )

        with patch.object(laser_grbl_tool, "_resize_image", return_value=(image, None)):
            gcode, stats, error = laser_grbl_tool.generate_gcode_from_grayscale(
                image,
                width_mm=2,
                height_mm=2,
                pixel_size_mm=1,
                laser_min_power=0,
                laser_max_power=500,
                threshold=128,
                bidirectional=False,
                overscan_mm=0,
                raster_scan_direction="horizontal",
                offset_y_mm=5,
            )

        self.assertIsNone(error)
        self.assertTrue(stats["y_axis_flipped"])
        self.assertIn("G1 X1 Y6 S500", gcode)
        self.assertIn("G1 X2 Y5 S500", gcode)
        self.assertLess(gcode.index("G1 X1 Y6 S500"), gcode.index("G1 X2 Y5 S500"))

    def test_vertical_raster_y_axis_preserves_image_orientation(self):
        image = np.array(
            [
                [0, 255],
                [255, 0],
            ],
            dtype=np.uint8,
        )

        with patch.object(laser_grbl_tool, "_resize_image", return_value=(image, None)):
            gcode, stats, error = laser_grbl_tool.generate_gcode_from_grayscale(
                image,
                width_mm=2,
                height_mm=2,
                pixel_size_mm=1,
                laser_min_power=0,
                laser_max_power=500,
                threshold=128,
                bidirectional=False,
                overscan_mm=0,
                raster_scan_direction="vertical",
                offset_y_mm=5,
            )

        self.assertIsNone(error)
        self.assertTrue(stats["y_axis_flipped"])
        self.assertIn("G1 X0 Y7 S500", gcode)
        self.assertIn("G1 X1 Y6 S500", gcode)

    def test_raster_auto_chooses_vertical_for_vertical_strokes(self):
        image = np.full((3, 3), 255, dtype=np.uint8)
        image[:, 1] = 0

        with patch.object(laser_grbl_tool, "_resize_image", return_value=(image, None)):
            gcode, stats, error = laser_grbl_tool.generate_gcode_from_grayscale(
                image,
                width_mm=3,
                height_mm=3,
                pixel_size_mm=1,
                laser_min_power=0,
                laser_max_power=500,
                threshold=128,
                bidirectional=False,
                overscan_mm=1,
                raster_scan_direction="auto",
            )

        self.assertIsNone(error)
        self.assertEqual(stats["raster_scan_direction"], "vertical")
        self.assertEqual(stats["raster_scan_direction_source"], "auto")
        self.assertIn("G0 X1 Y0", gcode)
        self.assertIn("G1 X1 Y3 S500", gcode)
        self.assertIn("G1 X1 Y4 S0", gcode)

    def test_raster_default_is_unidirectional_scanline(self):
        image = np.zeros((2, 2), dtype=np.uint8)

        with patch.object(laser_grbl_tool, "_resize_image", return_value=(image, None)):
            gcode, stats, error = laser_grbl_tool.generate_gcode_from_grayscale(
                image,
                width_mm=2,
                height_mm=2,
                pixel_size_mm=1,
                laser_min_power=0,
                laser_max_power=500,
                threshold=128,
                overscan_mm=0,
                raster_scan_direction="horizontal",
            )

        self.assertIsNone(error)
        self.assertFalse(stats["snake_scan"])
        self.assertEqual(stats["active_scanline_count"], 2)
        self.assertIn("G0 X0 Y0", gcode)
        self.assertIn("G1 X2 Y0 S500", gcode)
        self.assertNotIn("G0 X2 Y0", gcode)
        self.assertNotIn("G1 X0 Y0 S500", gcode)

    def test_raster_can_still_enable_bidirectional_snake_scan(self):
        image = np.zeros((2, 2), dtype=np.uint8)

        with patch.object(laser_grbl_tool, "_resize_image", return_value=(image, None)):
            gcode, stats, error = laser_grbl_tool.generate_gcode_from_grayscale(
                image,
                width_mm=2,
                height_mm=2,
                pixel_size_mm=1,
                laser_min_power=0,
                laser_max_power=500,
                threshold=128,
                bidirectional=True,
                overscan_mm=0,
                raster_scan_direction="horizontal",
            )

        self.assertIsNone(error)
        self.assertTrue(stats["snake_scan"])
        self.assertEqual(stats["active_scanline_count"], 2)
        self.assertIn("G0 X2 Y0", gcode)
        self.assertIn("G1 X0 Y0 S500", gcode)

    def test_cut_mode_outputs_contour_path(self):
        image = np.array(
            [
                [255, 255, 255, 255],
                [255, 0, 0, 255],
                [255, 0, 0, 255],
                [255, 255, 255, 255],
            ],
            dtype=np.uint8,
        )

        with patch.object(laser_grbl_tool, "_resize_image", return_value=(image, None)):
            gcode, stats, error = laser_grbl_tool.generate_cut_gcode_from_grayscale(
                image,
                width_mm=4,
                height_mm=4,
                pixel_size_mm=1,
                feed_rate=100,
                travel_rate=3000,
                laser_max_power=1000,
                threshold=128,
            )

        self.assertIsNone(error)
        self.assertEqual(stats["contour_count"], 1)
        self.assertIn("M4 S0", gcode)
        self.assertIn("S1000", gcode)
        self.assertIn("G1 F100", gcode)
        self.assertIn("M5", gcode)

    def test_cut_mode_orders_contours_by_nearest_start_not_area(self):
        image = np.full((12, 12), 255, dtype=np.uint8)
        image[9:11, 1:3] = 0
        image[1:5, 7:11] = 0

        with patch.object(laser_grbl_tool, "_resize_image", return_value=(image, None)):
            gcode, stats, error = laser_grbl_tool.generate_cut_gcode_from_grayscale(
                image,
                width_mm=12,
                height_mm=12,
                pixel_size_mm=1,
                feed_rate=100,
                travel_rate=3000,
                laser_max_power=1000,
                threshold=128,
            )

        self.assertIsNone(error)
        self.assertEqual(stats["contour_count"], 2)
        self.assertEqual(stats["cut_path_ordering"], "nearest")
        self.assertTrue(stats["y_axis_flipped"])
        first_cut_move = next(line for line in gcode if line.startswith("G0 X"))
        self.assertEqual(first_cut_move, "G0 X1 Y1")
        self.assertLess(gcode.index("G0 X1 Y1"), gcode.index("G0 X7 Y7"))

    def test_outline_mask_preserves_internal_hole_without_morphological_close(self):
        cv2, np_module, error = laser_grbl_tool._load_image_libs()
        if error:
            self.skipTest(error)

        image = np_module.full((18, 18), 255, dtype=np_module.uint8)
        image[4:14, 4:14] = 0
        image[8:10, 8:10] = 255

        mask, threshold = laser_grbl_tool._build_outline_mask(image, 128, False, cv2, np_module)

        self.assertEqual(threshold, 128)
        self.assertEqual(mask[4, 4], 255)
        self.assertEqual(mask[8, 8], 0)
        self.assertEqual(mask[9, 9], 0)

    def test_outline_mode_resizes_binary_mask_with_nearest_neighbor(self):
        cv2, np_module, error = laser_grbl_tool._load_image_libs()
        if error:
            self.skipTest(error)

        image = np_module.full((16, 16), 255, dtype=np_module.uint8)
        image[4:12, 4:12] = 0
        image[6:10, 6:10] = 255
        resize_calls = []

        def fake_resize(source, width_mm, height_mm, pixel_size_mm, interpolation=None, cv2_module=None):
            resize_calls.append(
                {
                    "source": source.copy(),
                    "interpolation": interpolation,
                    "cv2_module": cv2_module,
                }
            )
            return source.copy(), None

        with patch.object(laser_grbl_tool, "_resize_image", side_effect=fake_resize):
            gcode, stats, error = laser_grbl_tool.generate_outline_gcode_from_grayscale(
                image,
                width_mm=8,
                height_mm=8,
                pixel_size_mm=1,
                feed_rate=100,
                travel_rate=3000,
                laser_max_power=1000,
                threshold=128,
            )

        self.assertIsNone(error)
        self.assertIsNotNone(gcode)
        self.assertGreaterEqual(stats["contour_count"], 2)
        self.assertEqual(resize_calls[0]["interpolation"], cv2.INTER_NEAREST)
        self.assertIs(resize_calls[0]["cv2_module"], cv2)
        self.assertEqual(set(np_module.unique(resize_calls[0]["source"]).tolist()), {0, 255})

    def test_outline_mode_skips_full_border_and_simplifies_points(self):
        image = np.full((32, 32), 255, dtype=np.uint8)
        image[0, :] = 0
        image[-1, :] = 0
        image[:, 0] = 0
        image[:, -1] = 0
        image[9:23, 10:24] = 0

        gcode, stats, error = laser_grbl_tool.generate_outline_gcode_from_grayscale(
            image,
            width_mm=32,
            height_mm=32,
            pixel_size_mm=1,
            feed_rate=100,
            travel_rate=3000,
            laser_max_power=1000,
            threshold=128,
        )

        self.assertIsNone(error)
        self.assertGreaterEqual(stats["skipped_border_contours"], 1)
        self.assertEqual(stats["contour_count"], 1)
        self.assertLessEqual(stats["outline_point_count"], 8)
        self.assertTrue(stats["y_axis_flipped"])
        self.assertTrue(any("sharp threshold + OpenCV vector outline" in line for line in gcode))
        self.assertNotIn("G0 X0 Y0", gcode[:-1])
        self.assertIn("M4 S1000", gcode)
        first_laser_on = gcode.index("M4 S1000")
        self.assertTrue(gcode[first_laser_on - 1].startswith("G0 X"))

    def test_outline_mode_trims_white_margin_before_default_fit(self):
        image = np.full((100, 100), 255, dtype=np.uint8)
        image[40:60, 45:55] = 0

        gcode, stats, error = laser_grbl_tool.generate_outline_gcode_from_grayscale(
            image,
            pixel_size_mm=1,
            laser_max_power=1000,
            threshold=128,
        )

        self.assertIsNone(error)
        self.assertIsNotNone(gcode)
        self.assertEqual(stats["trim_box"]["x_px"], 45)
        self.assertEqual(stats["trim_box"]["y_px"], 40)
        self.assertEqual(stats["trim_box"]["width_px"], 10)
        self.assertEqual(stats["trim_box"]["height_px"], 20)
        self.assertLessEqual(stats["actual_width_mm"], 25.0)
        self.assertEqual(stats["actual_height_mm"], 50.0)

    def test_outline_cleanup_removes_redundant_horizontal_stroke_point(self):
        points = [(0, 0), (24, 0), (24, 3), (21, 3), (0, 3)]

        cleaned = laser_grbl_tool._snap_axis_aligned_outline_points(points)

        self.assertEqual(cleaned, [(0, 0), (24, 0), (24, 3), (0, 3)])

    def test_read_grayscale_image_composites_transparent_pixels_as_white(self):
        cv2, np_module, error = laser_grbl_tool._load_image_libs()
        if error:
            self.skipTest(error)

        tmp_dir = self.make_temp_dir()
        image_file = os.path.join(tmp_dir, "transparent.png")
        image = np_module.zeros((3, 3, 4), dtype=np_module.uint8)
        image[:, :, 3] = 0
        image[1, 1] = [0, 0, 0, 255]
        encoded, data = cv2.imencode(".png", image)
        self.assertTrue(encoded)
        data.tofile(image_file)

        grayscale, error = laser_grbl_tool._read_grayscale_image(image_file)

        self.assertIsNone(error)
        self.assertEqual(int(grayscale[0, 0]), 255)
        self.assertEqual(int(grayscale[1, 1]), 0)

    def test_default_resize_fits_tall_image_in_50mm_box(self):
        target_width, target_height, error = laser_grbl_tool._resolve_resize_dimensions_px(
            source_width=100,
            source_height=300,
            width_mm=0,
            height_mm=0,
            pixel_size_mm=0.1,
        )

        self.assertIsNone(error)
        self.assertLessEqual(target_width * 0.1, 50.0)
        self.assertLessEqual(target_height * 0.1, 50.0)
        self.assertEqual(target_height, 500)
        self.assertLess(target_width, 500)

    def test_default_resize_fits_wide_image_in_50mm_box(self):
        target_width, target_height, error = laser_grbl_tool._resolve_resize_dimensions_px(
            source_width=300,
            source_height=100,
            width_mm=0,
            height_mm=0,
            pixel_size_mm=0.1,
        )

        self.assertIsNone(error)
        self.assertLessEqual(target_width * 0.1, 50.0)
        self.assertLessEqual(target_height * 0.1, 50.0)
        self.assertEqual(target_width, 500)
        self.assertLess(target_height, 500)

    def test_auto_trim_removes_white_margin_and_reports_crop(self):
        image = np.full((10, 12), 255, dtype=np.uint8)
        image[3:7, 4:9] = 0

        cropped, info = laser_grbl_tool._auto_trim_image(image, tolerance=10)

        self.assertTrue(info["trimmed"])
        self.assertEqual(cropped.shape[:2], (4, 5))
        self.assertEqual(info["crop_box"], {"x_px": 4, "y_px": 3, "width_px": 5, "height_px": 4})
        self.assertEqual(info["original_width_px"], 12)
        self.assertEqual(info["trimmed_width_px"], 5)

    def test_auto_trim_keeps_content_edge_when_other_edges_are_background(self):
        image = np.full((8, 8), 255, dtype=np.uint8)
        image[:, 0] = 0
        image[3:5, 3:5] = 0

        cropped, info = laser_grbl_tool._auto_trim_image(image, tolerance=10)

        self.assertTrue(info["trimmed"])
        self.assertEqual(info["crop_box"]["x_px"], 0)
        self.assertEqual(info["crop_box"]["width_px"], 5)

    def test_auto_trim_skips_when_edges_do_not_identify_background(self):
        image = np.full((8, 8), 255, dtype=np.uint8)
        image[0, :] = 10
        image[-1, :] = 80
        image[:, 0] = 160
        image[:, -1] = 240

        cropped, info = laser_grbl_tool._auto_trim_image(image, tolerance=10)

        self.assertFalse(info["trimmed"])
        self.assertEqual(info["skipped_reason"], "edge_background_inconsistent")
        self.assertEqual(cropped.shape[:2], image.shape[:2])

    def test_placement_uses_dpi_without_upscaling_small_image(self):
        placement, error = laser_grbl_tool._resolve_image_placement(
            "sample.png",
            source_width_px=300,
            source_height_px=150,
            auto_size=False,
            dpi=300,
        )

        self.assertIsNone(error)
        self.assertEqual(placement["natural_width_mm"], 25.4)
        self.assertEqual(placement["natural_height_mm"], 12.7)
        self.assertEqual(placement["final_width_mm"], 25.4)
        self.assertFalse(placement["scaled_to_safe_area"])

    def test_placement_scales_large_dpi_size_to_safe_area(self):
        placement, error = laser_grbl_tool._resolve_image_placement(
            "sample.png",
            source_width_px=3000,
            source_height_px=1500,
            auto_size=False,
            dpi=300,
            safe_margin_mm=5,
        )

        self.assertIsNone(error)
        self.assertTrue(placement["scaled_to_safe_area"])
        self.assertEqual(placement["final_width_mm"], 90.0)
        self.assertEqual(placement["final_height_mm"], 45.0)

    def test_placement_infers_height_from_manual_width_when_locked(self):
        placement, error = laser_grbl_tool._resolve_image_placement(
            "sample.png",
            source_width_px=400,
            source_height_px=200,
            width_mm=40,
            height_mm=0,
            lock_aspect_ratio=True,
        )

        self.assertIsNone(error)
        self.assertEqual(placement["final_width_mm"], 40.0)
        self.assertEqual(placement["final_height_mm"], 20.0)
        self.assertEqual(placement["size_source"], "manual_width")

    def test_placement_single_dimension_keeps_aspect_when_unlocked(self):
        width_placement, width_error = laser_grbl_tool._resolve_image_placement(
            "sample.png",
            source_width_px=400,
            source_height_px=200,
            width_mm=40,
            height_mm=0,
            lock_aspect_ratio=False,
        )
        height_placement, height_error = laser_grbl_tool._resolve_image_placement(
            "sample.png",
            source_width_px=400,
            source_height_px=200,
            width_mm=0,
            height_mm=20,
            lock_aspect_ratio=False,
        )

        self.assertIsNone(width_error)
        self.assertEqual(width_placement["final_width_mm"], 40.0)
        self.assertEqual(width_placement["final_height_mm"], 20.0)
        self.assertEqual(width_placement["size_source"], "manual_width")
        self.assertIsNone(height_error)
        self.assertEqual(height_placement["final_width_mm"], 40.0)
        self.assertEqual(height_placement["final_height_mm"], 20.0)
        self.assertEqual(height_placement["size_source"], "manual_height")

    def test_placement_locked_box_fits_without_stretch(self):
        placement, error = laser_grbl_tool._resolve_image_placement(
            "sample.png",
            source_width_px=400,
            source_height_px=200,
            width_mm=50,
            height_mm=30,
            lock_aspect_ratio=True,
        )
        self.assertIsNone(error)
        self.assertEqual(placement["size_source"], "manual_box")
        self.assertAlmostEqual(placement["final_width_mm"], 50.0)
        self.assertAlmostEqual(placement["final_height_mm"], 25.0)
        self.assertLessEqual(placement["final_width_mm"], 50.0 + 1e-9)
        self.assertLessEqual(placement["final_height_mm"], 30.0 + 1e-9)
        self.assertAlmostEqual(
            placement["final_width_mm"] / placement["final_height_mm"],
            400 / 200,
            places=6,
        )

    def test_placement_unlocked_box_uses_exact_dimensions(self):
        placement, error = laser_grbl_tool._resolve_image_placement(
            "sample.png",
            source_width_px=400,
            source_height_px=200,
            width_mm=50,
            height_mm=30,
            lock_aspect_ratio=False,
        )
        self.assertIsNone(error)
        self.assertEqual(placement["size_source"], "manual")
        self.assertAlmostEqual(placement["final_width_mm"], 50.0)
        self.assertAlmostEqual(placement["final_height_mm"], 30.0)

    def test_placement_rejects_offset_outside_safe_area(self):
        placement, error = laser_grbl_tool._resolve_image_placement(
            "sample.png",
            source_width_px=300,
            source_height_px=300,
            auto_size=False,
            dpi=300,
            offset_x_mm=70,
            offset_y_mm=0,
        )

        self.assertIsNone(placement)
        self.assertIn("摆放超出安全雕刻区域", error)

    def test_generate_default_dimensions_fit_tall_image_stats(self):
        image = np.zeros((300, 100), dtype=np.uint8)

        gcode, stats, error = laser_grbl_tool.generate_gcode_from_grayscale(
            image,
            pixel_size_mm=1,
            bidirectional=False,
        )

        self.assertIsNone(error)
        self.assertIsNotNone(gcode)
        self.assertLessEqual(stats["actual_width_mm"], 50.0)
        self.assertLessEqual(stats["actual_height_mm"], 50.0)
        self.assertLess(stats["actual_width_mm"], 50.0)
        self.assertEqual(stats["actual_height_mm"], 50.0)

    def test_raster_offset_enters_generated_coordinates(self):
        image = np.array([[0, 255]], dtype=np.uint8)

        with patch.object(laser_grbl_tool, "_resize_image", return_value=(image, None)):
            gcode, stats, error = laser_grbl_tool.generate_gcode_from_grayscale(
                image,
                width_mm=2,
                height_mm=1,
                pixel_size_mm=1,
                laser_max_power=1000,
                bidirectional=False,
                offset_x_mm=10,
                offset_y_mm=5,
            )

        self.assertIsNone(error)
        self.assertIn("G0 X8.5 Y5", gcode)
        self.assertIn("G1 X10 Y5 S0", gcode)
        self.assertIn("G1 X11 Y5 S1000", gcode)
        self.assertEqual(stats["offset_x_mm"], 10)

    def test_cut_offset_enters_generated_coordinates(self):
        image = np.array(
            [
                [255, 255, 255, 255],
                [255, 0, 0, 255],
                [255, 0, 0, 255],
                [255, 255, 255, 255],
            ],
            dtype=np.uint8,
        )

        with patch.object(laser_grbl_tool, "_resize_image", return_value=(image, None)):
            gcode, _, error = laser_grbl_tool.generate_cut_gcode_from_grayscale(
                image,
                width_mm=4,
                height_mm=4,
                pixel_size_mm=1,
                laser_max_power=1000,
                threshold=128,
                offset_x_mm=6,
                offset_y_mm=7,
            )

        self.assertIsNone(error)
        self.assertTrue(any(line.startswith("G0 X7 Y8") for line in gcode), gcode)

    def test_explicit_width_keeps_width_based_height_inference(self):
        target_width, target_height, error = laser_grbl_tool._resolve_resize_dimensions_px(
            source_width=100,
            source_height=300,
            width_mm=50,
            height_mm=0,
            pixel_size_mm=0.1,
        )

        self.assertIsNone(error)
        self.assertEqual(target_width, 500)
        self.assertEqual(target_height, 1500)

    def test_generate_rejects_dimensions_outside_yixiu_work_area(self):
        image = np.array([[0]], dtype=np.uint8)
        resized = np.zeros((1, 101), dtype=np.uint8)

        with patch.object(laser_grbl_tool, "_resize_image", return_value=(resized, None)):
            gcode, stats, error = laser_grbl_tool.generate_gcode_from_grayscale(
                image,
                width_mm=101,
                height_mm=1,
                pixel_size_mm=1,
            )

        self.assertIsNone(gcode)
        self.assertIsNone(stats)
        self.assertIn("行程", error)
        self.assertIn("100", error)

    def test_generate_rejects_power_above_configured_s_max(self):
        image = np.array([[0]], dtype=np.uint8)

        gcode, stats, error = laser_grbl_tool.generate_gcode_from_grayscale(
            image,
            laser_max_power=laser_grbl_tool.DEFAULT_LASER_S_MAX + 1,
        )

        self.assertIsNone(gcode)
        self.assertIsNone(stats)
        self.assertIn("laser_max_power", error)

    def test_convert_cut_mode_uses_cut_generator(self):
        image = np.array([[0]], dtype=np.uint8)
        tmp_dir = self.make_temp_dir()
        input_file = os.path.join(tmp_dir, "sample.png")
        output_file = os.path.join(tmp_dir, "sample.nc")
        with open(input_file, "wb") as file:
            file.write(b"image bytes are mocked")

        with (
            patch.object(laser_grbl_tool, "_read_grayscale_image", return_value=(image, None)),
            patch.object(
                laser_grbl_tool,
                "generate_cut_gcode_from_grayscale",
                return_value=(["M4 S0", "S1000", "M5"], {"line_count": 3}, None),
            ) as cut_mock,
            patch.object(laser_grbl_tool, "generate_gcode_from_grayscale") as engrave_mock,
        ):
            result = laser_grbl_tool.convert_image_to_gcode(
                image_file=input_file,
                output_file=output_file,
                laser_mode="cut",
            )

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"]["laser_mode"], "cut")
        self.assertEqual(result["result"]["mode"], "cut")
        cut_mock.assert_called_once()
        self.assertGreater(cut_mock.call_args.kwargs["width_mm"], 0.0)
        self.assertGreater(cut_mock.call_args.kwargs["height_mm"], 0.0)
        engrave_mock.assert_not_called()

    def test_convert_reports_auto_trim_and_placement(self):
        image = np.full((20, 20), 255, dtype=np.uint8)
        image[5:15, 4:16] = 0
        tmp_dir = self.make_temp_dir()
        input_file = os.path.join(tmp_dir, "sample.png")
        output_file = os.path.join(tmp_dir, "sample.gcode")
        with open(input_file, "wb") as file:
            file.write(b"image bytes are mocked")

        with patch.object(laser_grbl_tool, "_read_grayscale_image", return_value=(image, None)):
            result = laser_grbl_tool.convert_image_to_gcode(
                image_file=input_file,
                output_file=output_file,
                auto_size=False,
                dpi=100,
                pixel_size_mm=1,
                laser_max_power=1000,
                bidirectional=False,
            )

        self.assertTrue(result["success"], result)
        payload = result["result"]
        self.assertTrue(payload["auto_trim"]["trimmed"])
        self.assertEqual(payload["auto_trim"]["crop_box"]["width_px"], 12)
        self.assertEqual(payload["placement"]["dpi"]["source"], "parameter")
        self.assertLess(payload["placement"]["final_width_mm"], 90.0)
        self.assertEqual(payload["placement"]["offset_x_mm"], 0.0)

    def test_convert_engrave_defaults_to_raster_when_env_requests_raster(self):
        image = np.array([[0]], dtype=np.uint8)
        tmp_dir = self.make_temp_dir()
        input_file = os.path.join(tmp_dir, "sample.png")
        output_file = os.path.join(tmp_dir, "sample.nc")
        with open(input_file, "wb") as file:
            file.write(b"image bytes are mocked")

        with (
            patch.object(laser_grbl_tool, "DEFAULT_ENGRAVING_MODE", "raster"),
            patch.object(laser_grbl_tool, "_read_grayscale_image", return_value=(image, None)),
            patch.object(
                laser_grbl_tool,
                "generate_gcode_from_grayscale",
                return_value=(["M4 S0", "G1 X1 Y0 S500", "M5"], {"line_count": 3}, None),
            ) as raster_mock,
            patch.object(laser_grbl_tool, "generate_outline_gcode_from_grayscale") as outline_mock,
        ):
            result = laser_grbl_tool.convert_image_to_gcode(
                image_file=input_file,
                output_file=output_file,
                laser_mode="engrave",
            )

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"]["laser_mode"], "engrave")
        self.assertEqual(result["result"]["engraving_mode"], "raster")
        self.assertEqual(result["result"]["mode"], "raster")
        raster_mock.assert_called_once()
        self.assertEqual(raster_mock.call_args.kwargs["laser_max_power"], 800)
        outline_mock.assert_not_called()

    def test_convert_engrave_outline_uses_contour_generator(self):
        image = np.array([[0]], dtype=np.uint8)
        tmp_dir = self.make_temp_dir()
        input_file = os.path.join(tmp_dir, "sample.png")
        output_file = os.path.join(tmp_dir, "sample.nc")
        with open(input_file, "wb") as file:
            file.write(b"image bytes are mocked")

        with (
            patch.object(laser_grbl_tool, "DEFAULT_ENGRAVING_MODE", "outline"),
            patch.object(laser_grbl_tool, "_read_grayscale_image", return_value=(image, None)),
            patch.object(
                laser_grbl_tool,
                "generate_outline_gcode_from_grayscale",
                return_value=(["M4 S0", "S600", "M5"], {"line_count": 3, "contour_count": 1}, None),
            ) as outline_mock,
            patch.object(laser_grbl_tool, "generate_gcode_from_grayscale") as raster_mock,
        ):
            result = laser_grbl_tool.convert_image_to_gcode(
                image_file=input_file,
                output_file=output_file,
                laser_mode="engrave",
                engraving_mode="outline",
                laser_min_power=100,
                laser_max_power=600,
                threshold=128,
            )

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"]["laser_mode"], "engrave")
        self.assertEqual(result["result"]["engraving_mode"], "outline")
        self.assertEqual(result["result"]["mode"], "outline")
        outline_mock.assert_called_once()
        self.assertEqual(outline_mock.call_args.kwargs["laser_max_power"], 600)
        self.assertEqual(outline_mock.call_args.kwargs["threshold"], 128)
        raster_mock.assert_not_called()

    def test_convert_cut_mode_ignores_engraving_mode_env(self):
        image = np.array([[0]], dtype=np.uint8)
        tmp_dir = self.make_temp_dir()
        input_file = os.path.join(tmp_dir, "sample.png")
        output_file = os.path.join(tmp_dir, "sample.nc")
        with open(input_file, "wb") as file:
            file.write(b"image bytes are mocked")

        with (
            patch.object(laser_grbl_tool, "DEFAULT_ENGRAVING_MODE", "raster"),
            patch.object(laser_grbl_tool, "_read_grayscale_image", return_value=(image, None)),
            patch.object(
                laser_grbl_tool,
                "generate_cut_gcode_from_grayscale",
                return_value=(["M4 S0", "S1000", "M5"], {"line_count": 3}, None),
            ) as cut_mock,
            patch.object(laser_grbl_tool, "_resolve_engraving_mode") as engraving_mode_mock,
        ):
            result = laser_grbl_tool.convert_image_to_gcode(
                image_file=input_file,
                output_file=output_file,
                laser_mode="cut",
            )

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"]["laser_mode"], "cut")
        self.assertEqual(result["result"]["engraving_mode"], "cut")
        self.assertEqual(result["result"]["mode"], "cut")
        cut_mock.assert_called_once()
        engraving_mode_mock.assert_not_called()

    def test_convert_rejects_unknown_engraving_mode(self):
        image = np.array([[0]], dtype=np.uint8)
        tmp_dir = self.make_temp_dir()
        input_file = os.path.join(tmp_dir, "sample.png")
        output_file = os.path.join(tmp_dir, "sample.nc")
        with open(input_file, "wb") as file:
            file.write(b"image bytes are mocked")

        with (
            patch.object(laser_grbl_tool, "DEFAULT_ENGRAVING_MODE", "bad-mode"),
            patch.object(laser_grbl_tool, "_read_grayscale_image", return_value=(image, None)),
        ):
            result = laser_grbl_tool.convert_image_to_gcode(
                image_file=input_file,
                output_file=output_file,
                laser_mode="engrave",
            )

        self.assertFalse(result["success"])
        self.assertIn("LASER_ENGRAVING_MODE 不支持", result["result"])

    def test_convert_rejects_unknown_laser_mode(self):
        result = laser_grbl_tool.convert_image_to_gcode(laser_mode="unknown")

        self.assertFalse(result["success"])
        self.assertIn("laser_mode 不支持", result["result"])

    def test_convert_accepts_bmg_extension_when_content_decoder_succeeds(self):
        image = np.array(
            [
                [255, 255, 255, 255],
                [255, 0, 0, 255],
                [255, 0, 0, 255],
                [255, 255, 255, 255],
            ],
            dtype=np.uint8,
        )

        tmp_dir = self.make_temp_dir()
        input_file = os.path.join(tmp_dir, "sample.bmg")
        output_file = os.path.join(tmp_dir, "sample.gcode")
        with open(input_file, "wb") as file:
            file.write(b"image bytes are mocked")

        with (
            patch.object(laser_grbl_tool, "_read_grayscale_image", return_value=(image, None)),
            patch.object(laser_grbl_tool, "_resize_image", return_value=(image, None)),
        ):
            result = laser_grbl_tool.convert_image_to_gcode(
                image_file=input_file,
                output_file=output_file,
                width_mm=2,
                height_mm=1,
                pixel_size_mm=1,
                laser_max_power=1000,
                bidirectional=False,
            )

            self.assertTrue(result["success"], result)
            self.assertEqual(result["result"]["gcode_file"], output_file)
            with open(output_file, "r", encoding="utf-8") as file:
                content = file.read()
            self.assertIn("G92 X0 Y0 Z0", content)
            self.assertIn("S1000", content)
            self.assertIn("G1 X", content)
            self.assertIn("M5", content)

    def test_output_must_be_gcode_or_nc(self):
        image = np.array([[0]], dtype=np.uint8)

        tmp_dir = self.make_temp_dir()
        input_file = os.path.join(tmp_dir, "sample.bmp")
        output_file = os.path.join(tmp_dir, "sample.txt")
        with open(input_file, "wb") as file:
            file.write(b"image bytes are mocked")

        with patch.object(laser_grbl_tool, "_read_grayscale_image", return_value=(image, None)):
            result = laser_grbl_tool.convert_image_to_gcode(
                image_file=input_file,
                output_file=output_file,
            )

        self.assertFalse(result["success"])
        self.assertIn("输出文件类型不支持", result["result"])

    def test_prepare_direct_gcode_does_not_convert(self):
        tmp_dir = self.make_temp_dir()
        gcode_file = os.path.join(tmp_dir, "ready.nc")
        with open(gcode_file, "w", encoding="utf-8") as file:
            file.write("G21\nG90\nM4 S100\nG1 X10 Y20 F600\n")

        with patch.object(laser_grbl_tool, "convert_image_to_gcode") as convert_mock:
            result = laser_grbl_tool.prepare_gcode_file_for_sending(gcode_file=gcode_file)

        self.assertTrue(result["success"], result)
        self.assertFalse(result["result"]["converted"])
        self.assertEqual(result["result"]["gcode_file"], gcode_file)
        self.assertEqual(result["result"]["gcode_bounds"]["max_x_mm"], 10.0)
        self.assertEqual(result["result"]["gcode_bounds"]["max_y_mm"], 20.0)
        self.assertIn("time_estimate", result["result"])
        self.assertEqual(result["result"]["speech"], "预计雕刻 2 秒")
        convert_mock.assert_not_called()

    def test_prepare_direct_gcode_hash_failure_has_no_path_leak(self):
        tmp_dir = self.make_temp_dir()
        gcode_file = os.path.join(tmp_dir, "hash-fail.nc")
        with open(gcode_file, "w", encoding="utf-8") as file:
            file.write("G21\nG90\nM4 S100\nG1 X10 Y20 F600\n")
        leak = f"Permission denied: '{gcode_file}'"

        with patch(
            "core.laser_runtime.models.file_sha256",
            side_effect=OSError(leak),
        ):
            result = laser_grbl_tool.prepare_gcode_file_for_sending(gcode_file=gcode_file)

        self.assertFalse(result["success"], result)
        self.assertEqual(result.get("error_code"), "preview_content_mismatch")
        self.assertEqual(result["result"], "无法计算 G-code 摘要")
        self.assertNotIn(gcode_file, result["result"])
        self.assertNotIn(tmp_dir, result["result"])
        self.assertNotIn(leak, result["result"])
        if "detail" in result and result["detail"] is not None:
            detail_text = str(result["detail"])
            self.assertNotIn(gcode_file, detail_text)
            self.assertNotIn(leak, detail_text)

    def test_prepare_converted_image_hash_failure_has_no_path_leak(self):
        tmp_dir = self.make_temp_dir()
        input_file = os.path.join(tmp_dir, "source.bmp")
        output_file = os.path.join(tmp_dir, "source.nc")
        with open(input_file, "wb") as file:
            file.write(b"image bytes are mocked")
        with open(output_file, "w", encoding="utf-8") as file:
            file.write("G21\nG1 X1 Y1\n")
        leak = f"I/O error reading '{output_file}'"
        conversion = {
            "success": True,
            "result": {
                "gcode_file": output_file,
                "image_file": input_file,
            },
        }

        with (
            patch.object(
                laser_grbl_tool,
                "convert_image_to_gcode",
                return_value=conversion,
            ),
            patch(
                "core.laser_runtime.models.file_sha256",
                side_effect=OSError(leak),
            ),
        ):
            result = laser_grbl_tool.prepare_gcode_file_for_sending(gcode_file=input_file)

        self.assertFalse(result["success"], result)
        self.assertEqual(result.get("error_code"), "preview_content_mismatch")
        self.assertEqual(result["result"], "无法计算 G-code 摘要")
        self.assertNotIn(output_file, result["result"])
        self.assertNotIn(input_file, result["result"])
        self.assertNotIn(tmp_dir, result["result"])
        self.assertNotIn(leak, result["result"])
        self.assertNotIn("detail", result)

    def test_inspect_gcode_bounds_tracks_absolute_and_relative_moves(self):
        tmp_dir = self.make_temp_dir()
        gcode_file = os.path.join(tmp_dir, "relative.gcode")
        with open(gcode_file, "w", encoding="utf-8") as file:
            file.write("G21\n")
            file.write("G90\n")
            file.write("G1 X10 Y20 S100\n")
            file.write("G91\n")
            file.write("G1 X5 Y-5\n")

        bounds, error = laser_grbl_tool.inspect_gcode_file_bounds(gcode_file)

        self.assertIsNone(error)
        self.assertEqual(bounds["max_x_mm"], 15.0)
        self.assertEqual(bounds["max_y_mm"], 20.0)
        self.assertEqual(bounds["coordinate_line_count"], 2)

    def test_prepare_direct_gcode_rejects_out_of_bounds_coordinate(self):
        tmp_dir = self.make_temp_dir()
        gcode_file = os.path.join(tmp_dir, "too-wide.gcode")
        with open(gcode_file, "w", encoding="utf-8") as file:
            file.write("G21\nG90\nG1 X101 Y0 S100\n")

        result = laser_grbl_tool.prepare_gcode_file_for_sending(gcode_file=gcode_file)

        self.assertFalse(result["success"], result)
        self.assertIn("G-code 第 3 行", result["result"])
        self.assertIn("行程", result["result"])

    def test_prepare_direct_gcode_rejects_s_above_configured_max(self):
        tmp_dir = self.make_temp_dir()
        gcode_file = os.path.join(tmp_dir, "too-powerful.gcode")
        with open(gcode_file, "w", encoding="utf-8") as file:
            file.write(f"G21\nG1 X1 Y1 S{laser_grbl_tool.DEFAULT_LASER_S_MAX + 1}\n")

        result = laser_grbl_tool.prepare_gcode_file_for_sending(gcode_file=gcode_file)

        self.assertFalse(result["success"], result)
        self.assertIn("S 值超出上限", result["result"])

    def test_prepare_direct_gcode_rejects_arc_that_exceeds_work_area_between_endpoints(self):
        tmp_dir = self.make_temp_dir()
        gcode_file = os.path.join(tmp_dir, "arc-outside.gcode")
        with open(gcode_file, "w", encoding="utf-8") as file:
            file.write("G21\nG90\nG0 X50 Y50\nG2 X50 Y50 I60 J0\n")

        result = laser_grbl_tool.prepare_gcode_file_for_sending(gcode_file=gcode_file)

        self.assertFalse(result["success"], result)
        self.assertIn("G-code 第 4 行", result["result"])
        self.assertIn("行程", result["result"])

    def test_prepare_finds_named_file_only_in_engraving_directory(self):
        engraving_dir = self.make_temp_dir()
        gcode_file = os.path.join(engraving_dir, "三角形.nc")
        with open(gcode_file, "w", encoding="utf-8") as file:
            file.write("G21\n")

        with patch.object(laser_grbl_tool, "DEFAULT_ENGRAVING_DIR", engraving_dir):
            result = laser_grbl_tool.prepare_gcode_file_for_sending(gcode_file="三角形")

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"]["gcode_file"], gcode_file)
        self.assertFalse(result["result"]["converted"])

    def test_prepare_named_file_missing_reports_engraving_directory(self):
        engraving_dir = self.make_temp_dir()

        with patch.object(laser_grbl_tool, "DEFAULT_ENGRAVING_DIR", engraving_dir):
            result = laser_grbl_tool.prepare_gcode_file_for_sending(gcode_file="三角形")

        self.assertFalse(result["success"], result)
        self.assertIn("未在雕刻文件目录中找到文件", result["result"])
        self.assertIn(engraving_dir, result["result"])

    def test_prepare_uses_only_file_in_engraving_directory_when_no_file_is_named(self):
        engraving_dir = self.make_temp_dir()
        gcode_file = os.path.join(engraving_dir, "三角形.nc")
        with open(gcode_file, "w", encoding="utf-8") as file:
            file.write("G21\n")

        with (
            patch.object(laser_grbl_tool, "DEFAULT_ENGRAVING_DIR", engraving_dir),
            patch.object(laser_grbl_tool, "DEFAULT_GCODE_FILE", ""),
            patch.object(laser_grbl_tool, "DEFAULT_IMAGE_FILE", ""),
        ):
            result = laser_grbl_tool.prepare_gcode_file_for_sending()

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"]["gcode_file"], gcode_file)

    def test_prepare_requires_name_when_multiple_engraving_files_exist(self):
        engraving_dir = self.make_temp_dir()
        for filename in ("三角形.nc", "圆形.gcode"):
            with open(os.path.join(engraving_dir, filename), "w", encoding="utf-8") as file:
                file.write("G21\n")

        with (
            patch.object(laser_grbl_tool, "DEFAULT_ENGRAVING_DIR", engraving_dir),
            patch.object(laser_grbl_tool, "DEFAULT_GCODE_FILE", ""),
            patch.object(laser_grbl_tool, "DEFAULT_IMAGE_FILE", ""),
        ):
            result = laser_grbl_tool.prepare_gcode_file_for_sending()

        self.assertFalse(result["success"], result)
        self.assertIn("有多个文件", result["result"])
        self.assertIn("三角形.nc", result["result"])

    def test_prepare_converts_image_to_default_gcode_file(self):
        image = np.array(
            [
                [255, 255, 255, 255],
                [255, 0, 0, 255],
                [255, 0, 0, 255],
                [255, 255, 255, 255],
            ],
            dtype=np.uint8,
        )

        tmp_dir = self.make_temp_dir()
        input_file = os.path.join(tmp_dir, "source.bmp")
        output_file = os.path.join(tmp_dir, "source.nc")
        with open(input_file, "wb") as file:
            file.write(b"image bytes are mocked")

        with (
            patch.object(laser_grbl_tool, "DEFAULT_GCODE_FILE", output_file),
            patch.object(laser_grbl_tool, "_read_grayscale_image", return_value=(image, None)),
            patch.object(laser_grbl_tool, "_resize_image", return_value=(image, None)),
        ):
            result = laser_grbl_tool.prepare_gcode_file_for_sending(gcode_file=input_file)

        self.assertTrue(result["success"], result)
        self.assertTrue(result["result"]["converted"])
        self.assertEqual(result["result"]["gcode_file"], output_file)
        self.assertTrue(os.path.isfile(output_file))

    def test_prepare_uses_default_image_when_default_gcode_is_missing(self):
        image = np.array(
            [
                [255, 255, 255, 255],
                [255, 0, 0, 255],
                [255, 0, 0, 255],
                [255, 255, 255, 255],
            ],
            dtype=np.uint8,
        )

        tmp_dir = self.make_temp_dir()
        image_file = os.path.join(tmp_dir, "default.bmp")
        default_gcode = os.path.join(tmp_dir, "default.nc")
        with open(image_file, "wb") as file:
            file.write(b"image bytes are mocked")

        with (
            patch.object(laser_grbl_tool, "DEFAULT_GCODE_FILE", default_gcode),
            patch.object(laser_grbl_tool, "DEFAULT_IMAGE_FILE", image_file),
            patch.object(laser_grbl_tool, "_read_grayscale_image", return_value=(image, None)),
            patch.object(laser_grbl_tool, "_resize_image", return_value=(image, None)),
        ):
            result = laser_grbl_tool.prepare_gcode_file_for_sending()

        self.assertTrue(result["success"], result)
        self.assertTrue(result["result"]["converted"])
        self.assertEqual(result["result"]["source_file"], image_file)
        self.assertEqual(result["result"]["gcode_file"], default_gcode)

    def test_send_file_returns_failure_when_prepare_fails(self):
        fake_mcp = FakeMcp()
        laser_grbl_tool.register_tool(fake_mcp)

        with patch.object(
            laser_grbl_tool,
            "prepare_gcode_file_for_sending",
            return_value={"success": False, "result": "missing file"},
        ), patch.object(laser_grbl_tool.laser_execution, "send_file") as send_mock:
            result = fake_mcp.tools["laser_grbl_tool"](
                action="send_file", run_in_background=False, confirmed=True
            )

        self.assertFalse(result["success"], result)
        self.assertEqual(result["result"], "missing file")
        send_mock.assert_not_called()

    def test_send_file_without_confirmation_delegates_to_laser_execution(self):
        fake_mcp = FakeMcp()
        laser_grbl_tool.register_tool(fake_mcp)
        prepared_result = {
            "source_file": "job.gcode",
            "gcode_file": "job.gcode",
            "converted": False,
        }
        preview = {
            "success": True,
            "result": {
                "confirmation_required": True,
                "prepared": prepared_result,
            },
        }

        with (
            patch.object(
                laser_grbl_tool,
                "prepare_gcode_file_for_sending",
                return_value={"success": True, "result": prepared_result},
            ),
            patch.object(
                laser_grbl_tool.laser_execution, "send_file", return_value=preview
            ) as send_mock,
        ):
            result = fake_mcp.tools["laser_grbl_tool"](action="send_file")

        self.assertTrue(result["success"], result)
        self.assertTrue(result["result"]["confirmation_required"])
        send_mock.assert_called_once()
        self.assertEqual(send_mock.call_args.args[0], prepared_result)
        self.assertEqual(send_mock.call_args.args[1], "serial")
        self.assertFalse(send_mock.call_args.kwargs.get("confirmed", False))

    def test_send_file_confirmed_delegates_to_laser_execution(self):
        fake_mcp = FakeMcp()
        laser_grbl_tool.register_tool(fake_mcp)
        prepared_result = {
            "source_file": "job.gcode",
            "gcode_file": "job.gcode",
            "converted": False,
        }
        delegated = {
            "success": True,
            "job_id": "serial-job",
            "status": "pending",
            "result": "started",
        }

        with (
            patch.object(
                laser_grbl_tool,
                "prepare_gcode_file_for_sending",
                return_value={"success": True, "result": prepared_result},
            ),
            patch.object(
                laser_grbl_tool.laser_execution, "send_file", return_value=delegated
            ) as send_mock,
        ):
            result = fake_mcp.tools["laser_grbl_tool"](
                action="send_file",
                confirmed=True,
                port="COM3",
                baudrate=115200,
                wait_for_response=True,
                run_in_background=True,
            )

        self.assertEqual(result, delegated)
        send_mock.assert_called_once()
        self.assertEqual(send_mock.call_args.args[0], prepared_result)
        self.assertEqual(send_mock.call_args.args[1], "serial")
        self.assertTrue(send_mock.call_args.kwargs["confirmed"])
        self.assertTrue(send_mock.call_args.kwargs["run_in_background"])
        self.assertEqual(send_mock.call_args.kwargs["port"], "COM3")
        self.assertEqual(send_mock.call_args.kwargs["baudrate"], 115200)
        self.assertTrue(send_mock.call_args.kwargs["wait_for_response"])

    def test_job_status_and_cancel_delegate_to_laser_execution(self):
        fake_mcp = FakeMcp()
        laser_grbl_tool.register_tool(fake_mcp)

        with patch.object(
            laser_grbl_tool.laser_execution,
            "job_status",
            return_value={"success": True, "result": {"status": "pending", "job_id": "j1"}},
        ) as status_mock:
            status = fake_mcp.tools["laser_grbl_tool"](action="job_status", job_id="j1")
        self.assertTrue(status["success"], status)
        status_mock.assert_called_once_with("j1", "serial")

        with patch.object(
            laser_grbl_tool.laser_execution,
            "cancel_job",
            return_value={"success": True, "result": {"status": "cancelled", "job_id": "j1"}},
        ) as cancel_mock:
            cancelled = fake_mcp.tools["laser_grbl_tool"](action="cancel_job", job_id="j1")
        self.assertTrue(cancelled["success"], cancelled)
        cancel_mock.assert_called_once_with("j1", "serial")

    def test_tool_module_does_not_import_private_backend(self):
        source_path = os.path.abspath(laser_grbl_tool.__file__)
        with open(source_path, "r", encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotRegex(source, r"(?m)^\s*from core import _laser_execution_backend")
        self.assertNotRegex(source, r"(?m)^\s*import core\._laser_execution_backend")
        self.assertNotIn("--send-file-worker", source)
        self.assertNotIn("def _execute_send_file", source)
        self.assertNotIn("def _start_send_file_job", source)
        self.assertNotIn("def _send_gcode_file", source)
        self.assertNotIn("JOBS_DIR", source)

    def test_send_command_sends_one_trimmed_command_and_not_a_file(self):
        fake_mcp = FakeMcp()
        laser_grbl_tool.register_tool(fake_mcp)

        class FakeSerial:
            def close(self):
                return None

        with (
            patch.object(
                laser_grbl_tool,
                "_load_serial",
                return_value=(object(), object(), None),
            ),
            patch.object(
                laser_grbl_tool,
                "_connect_grbl_serial",
                return_value=(FakeSerial(), "COM9", None),
            ),
            patch.object(
                laser_grbl_tool,
                "_send_gcode",
                return_value=(True, None),
            ) as send_mock,
            patch.object(laser_grbl_tool.laser_execution, "send_file") as send_file_mock,
        ):
            result = fake_mcp.tools["laser_grbl_tool"](
                action="send_command",
                gcode_command="  $$  ",
                confirmed=True,
            )

        self.assertTrue(result["success"], result)
        self.assertIn("发送命令成功", result["result"])
        self.assertEqual(result["detail"]["port"], "COM9")
        self.assertEqual(result["detail"]["command"], "$$")
        send_mock.assert_called_once()
        self.assertEqual(send_mock.call_args[0][1], "$$")
        send_file_mock.assert_not_called()

    def test_laser_grbl_tool_machine_profile_action(self):
        fake_mcp = FakeMcp()
        laser_grbl_tool.register_tool(fake_mcp)

        result = fake_mcp.tools["laser_grbl_tool"]("machine_profile")

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"]["machine_name"], "翼宿 V1.0")
        self.assertEqual(result["result"]["work_area_width_mm"], 100.0)
        self.assertEqual(result["result"]["work_area_height_mm"], 100.0)
        self.assertEqual(result["result"]["laser_optical_power_w"], 5.0)

    def test_threshold_boundary_0_and_255_match_less_equal_semantics(self):
        # Align with AI core: black when gray <= threshold.
        pure_black = laser_grbl_tool._power_from_gray(0, 0, 500, threshold=0, invert=False)
        near_black = laser_grbl_tool._power_from_gray(1, 0, 500, threshold=0, invert=False)
        self.assertEqual(pure_black, 500)
        self.assertEqual(near_black, 0)

        all_black = laser_grbl_tool._power_from_gray(255, 0, 500, threshold=255, invert=False)
        mid = laser_grbl_tool._power_from_gray(128, 0, 500, threshold=255, invert=False)
        self.assertEqual(all_black, 500)
        self.assertEqual(mid, 500)

        at_cut = laser_grbl_tool._power_from_gray(128, 0, 500, threshold=128, invert=False)
        above_cut = laser_grbl_tool._power_from_gray(129, 0, 500, threshold=128, invert=False)
        self.assertEqual(at_cut, 500)
        self.assertEqual(above_cut, 0)

    def test_threshold_matrix_aligns_with_ai_core_less_equal(self):
        """Shared 0/255/auto matrix semantics with AI core (black when gray <= threshold)."""
        from core.ai_laser_gcode.raster import _dither

        # 0: only pure black
        self.assertEqual(_dither([[0.0, 1.0]], "threshold", threshold=0), [[0, 255]])
        self.assertEqual(laser_grbl_tool._power_from_gray(0, 0, 500, threshold=0, invert=False), 500)
        self.assertEqual(laser_grbl_tool._power_from_gray(1, 0, 500, threshold=0, invert=False), 0)

        # 255: all black
        self.assertEqual(_dither([[0.0, 255.0]], "threshold", threshold=255), [[0, 0]])
        self.assertEqual(laser_grbl_tool._power_from_gray(255, 0, 500, threshold=255, invert=False), 500)

        # auto/default-like 128 boundary
        self.assertEqual(_dither([[127.0, 128.0, 129.0]], "threshold", threshold=128), [[0, 0, 255]])
        self.assertEqual(laser_grbl_tool._power_from_gray(128, 0, 500, threshold=128, invert=False), 500)
        self.assertEqual(laser_grbl_tool._power_from_gray(129, 0, 500, threshold=128, invert=False), 0)

        # invalid threshold rejected before work (grbl validate)
        value, error = laser_grbl_tool._validate_int(256, "threshold", -1, 255)
        self.assertIsNotNone(error)
        value, error = laser_grbl_tool._validate_int(-2, "threshold", -1, 255)
        self.assertIsNotNone(error)


if __name__ == "__main__":
    unittest.main()
