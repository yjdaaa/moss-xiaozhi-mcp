import base64
import json
import os
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from moss_mcp import web_server as laser_web_server


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADgwGosIXC9QAAAABJRU5ErkJggg=="
)


class LaserWebServerTests(unittest.TestCase):
    def make_temp_dir(self):
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".tmp-test"))
        os.makedirs(base_dir, exist_ok=True)
        temp_dir = os.path.join(base_dir, f"case-{uuid.uuid4().hex}")
        os.makedirs(temp_dir, exist_ok=False)
        self.addCleanup(shutil.rmtree, temp_dir, ignore_errors=True)
        return temp_dir

    def data_url(self, payload=PNG_1X1):
        return "data:image/png;base64," + base64.b64encode(payload).decode("ascii")

    def test_draw_preview_uploads_image_and_routes_image_workflow(self):
        temp_dir = Path(self.make_temp_dir())
        calls = {}

        def fake_workflow_runner(**kwargs):
            calls.update(kwargs)
            return {
                "success": True,
                "result": {
                    "workflow_id": "wf-draw",
                    "artifacts": {"image_file": kwargs["image_file"]},
                    "speech": "预览已生成",
                },
            }

        with patch.object(laser_web_server, "DRAW_OUTPUT_DIR", temp_dir), patch.object(
            laser_web_server, "DRAW_JOB_OUTPUT_DIR", temp_dir / "jobs"
        ), patch.object(
            laser_web_server, "DRAW_MATERIAL_LIBRARY_FILE", temp_dir / "materials_from_lasergrbl.json"
        ), patch.object(
            laser_web_server.laser_material_calibration_tool, "MATERIAL_PARAMS_FILE", str(temp_dir / "missing-materials.json")
        ):
            result = laser_web_server.draw_preview_from_payload(
                {
                    "image_data_url": self.data_url(),
                    "scene_json": {"type": "excalidraw", "elements": []},
                    "material": "椴木",
                    "thickness_mm": "3",
                    "mode": "raster",
                    "network_host": "laser.local",
                    "network_telnet_port": "23",
                },
                workflow_runner=fake_workflow_runner,
            )

        self.assertTrue(result["success"], result)
        self.assertEqual(calls["action"], "preview")
        self.assertEqual(calls["source_type"], "image")
        self.assertEqual(calls["material"], "椴木")
        self.assertEqual(calls["thickness_mm"], 3.0)
        self.assertEqual(calls["prompt"], "hand drawn Excalidraw sketch, laser raster engraving")
        self.assertEqual(calls["mode"], "raster")
        self.assertEqual(calls["task_type"], "engrave_photo")
        self.assertEqual(calls["raster_quality_strategy"], "auto")
        self.assertNotIn("size_mm", calls)
        self.assertNotIn("width_mm", calls)
        self.assertNotIn("height_mm", calls)
        self.assertEqual(calls["material_library"], "")
        self.assertEqual(calls["connection_mode"], "network")
        self.assertEqual(calls["network_host"], "laser.local")
        self.assertTrue(Path(calls["image_file"]).is_file())
        self.assertTrue(Path(result["result"]["draw_lab"]["scene_file"]).is_file())
        self.assertIn("uploaded_image_url", result["result"]["web"])
        self.assertIn("scene_download_url", result["result"]["web"])
        fingerprint = result["result"]["draw_lab"]["content_fingerprint"]
        self.assertIsInstance(fingerprint, str)
        self.assertEqual(len(fingerprint), 64)
        self.assertRegex(fingerprint, r"^[0-9a-f]{64}$")
        self.assertNotEqual(len(fingerprint), 12)
        self.assertNotEqual(len(fingerprint), 10)
        self.assertNotEqual(len(fingerprint), 16)
        # Fingerprint is full digest of bytes; filename may still use 12-char prefix.
        self.assertIn(fingerprint[:12], Path(calls["image_file"]).name)

    def test_draw_preview_content_fingerprint_stable_and_not_truncated_filename(self):
        temp_dir = Path(self.make_temp_dir())
        png_a = self.data_url()
        # Valid PNG magic + different payload bytes => different full digest.
        png_b = self.data_url(PNG_1X1 + b"-different-fingerprint-payload")

        def fake_workflow_runner(**kwargs):
            return {
                "success": True,
                "result": {
                    "workflow_id": "wf-fp",
                    "artifacts": {"image_file": kwargs["image_file"]},
                },
            }

        with patch.object(laser_web_server, "DRAW_OUTPUT_DIR", temp_dir), patch.object(
            laser_web_server, "DRAW_JOB_OUTPUT_DIR", temp_dir / "jobs"
        ), patch.object(
            laser_web_server, "DRAW_MATERIAL_LIBRARY_FILE", temp_dir / "materials_from_lasergrbl.json"
        ), patch.object(
            laser_web_server.laser_material_calibration_tool,
            "MATERIAL_PARAMS_FILE",
            str(temp_dir / "missing-materials.json"),
        ):
            first = laser_web_server.draw_preview_from_payload(
                {
                    "image_data_url": png_a,
                    "material": "椴木",
                    "thickness_mm": "3",
                    "mode": "raster",
                },
                workflow_runner=fake_workflow_runner,
            )
            second = laser_web_server.draw_preview_from_payload(
                {
                    "image_data_url": png_a,
                    "material": "椴木",
                    "thickness_mm": "3",
                    "mode": "raster",
                },
                workflow_runner=fake_workflow_runner,
            )
            other = laser_web_server.draw_preview_from_payload(
                {
                    "image_data_url": png_b,
                    "material": "椴木",
                    "thickness_mm": "3",
                    "mode": "raster",
                },
                workflow_runner=fake_workflow_runner,
            )
        fp1 = first["result"]["draw_lab"]["content_fingerprint"]
        fp2 = second["result"]["draw_lab"]["content_fingerprint"]
        fp3 = other["result"]["draw_lab"]["content_fingerprint"]
        self.assertEqual(fp1, fp2)
        self.assertNotEqual(fp1, fp3)
        self.assertEqual(len(fp1), 64)

    def test_draw_outline_vector_simplify_default_and_bounds(self):
        ok, error = laser_web_server._draw_workflow_preview_payload(
            {
                "material": "椴木",
                "thickness_mm": "3",
                "mode": "outline",
            },
            "<pending>",
        )
        self.assertIsNone(error)
        self.assertEqual(ok["vector_simplify_factor"], 3.0)
        self.assertTrue(ok["lock_aspect_ratio"])

        for value in (0.25, 8, 8.0):
            payload, err = laser_web_server._draw_workflow_preview_payload(
                {
                    "material": "椴木",
                    "thickness_mm": "3",
                    "mode": "outline",
                    "vector_simplify_factor": value,
                },
                "<pending>",
            )
            self.assertIsNone(err, value)
            self.assertEqual(payload["vector_simplify_factor"], float(value))

        for bad in (0.249, 8.01, "nan", "inf", " ", "abc"):
            payload, err = laser_web_server._draw_workflow_preview_payload(
                {
                    "material": "椴木",
                    "thickness_mm": "3",
                    "mode": "outline",
                    "vector_simplify_factor": bad,
                },
                "<pending>",
            )
            self.assertIsNone(payload, bad)
            self.assertIn("vector_simplify_factor", err)

        raster, err = laser_web_server._draw_workflow_preview_payload(
            {
                "material": "椴木",
                "thickness_mm": "3",
                "mode": "raster",
                "vector_simplify_factor": 99,
            },
            "<pending>",
        )
        self.assertIsNone(err)
        self.assertNotIn("vector_simplify_factor", raster)

    def test_draw_preview_routes_optional_width_height_and_pixel_size(self):
        temp_dir = Path(self.make_temp_dir())
        calls = {}

        def fake_workflow_runner(**kwargs):
            calls.update(kwargs)
            return {
                "success": True,
                "result": {
                    "workflow_id": "wf-draw",
                    "artifacts": {"image_file": kwargs["image_file"]},
                },
            }

        with patch.object(laser_web_server, "DRAW_OUTPUT_DIR", temp_dir), patch.object(
            laser_web_server, "DRAW_JOB_OUTPUT_DIR", temp_dir / "jobs"
        ), patch.object(
            laser_web_server, "DRAW_MATERIAL_LIBRARY_FILE", temp_dir / "materials_from_lasergrbl.json"
        ), patch.object(
            laser_web_server.laser_material_calibration_tool, "MATERIAL_PARAMS_FILE", str(temp_dir / "missing-materials.json")
        ):
            result = laser_web_server.draw_preview_from_payload(
                {
                    "image_data_url": self.data_url(),
                    "material": "椴木",
                    "thickness_mm": "3",
                    "mode": "raster",
                    "width_mm": "40",
                    "height_mm": "20",
                    "pixel_size_mm": "0.2",
                },
                workflow_runner=fake_workflow_runner,
            )

        self.assertTrue(result["success"], result)
        self.assertEqual(calls["width_mm"], 40.0)
        self.assertEqual(calls["height_mm"], 20.0)
        self.assertEqual(calls["pixel_size_mm"], 0.2)
        self.assertTrue(calls.get("lock_aspect_ratio", True))
        self.assertNotIn("size_mm", calls)

    def test_draw_preview_routes_line_art_raster_options(self):
        payload, error = laser_web_server._draw_workflow_preview_payload(
            {
                "material": "椴木",
                "thickness_mm": "3",
                "mode": "raster",
                "pixel_size_mm": "0.1",
                "dither_algorithm": "threshold",
                "threshold": "200",
                "raster_scan_direction": "horizontal",
                "raster_output_strategy": "scanline",
                "raster_quality_strategy": "manual",
            },
            Path("draw.png"),
        )

        self.assertIsNone(error)
        self.assertEqual(payload["pixel_size_mm"], 0.1)
        self.assertEqual(payload["dither_algorithm"], "threshold")
        self.assertEqual(payload["threshold"], 200)
        self.assertEqual(payload["raster_scan_direction"], "horizontal")
        self.assertEqual(payload["raster_output_strategy"], "scanline")
        self.assertEqual(payload["raster_quality_strategy"], "manual")
        self.assertNotIn("material_match_policy", payload)
        self.assertNotIn("send_policy", payload)

    def test_draw_preview_threshold_boundaries_and_invalid(self):
        auto_payload, auto_error = laser_web_server._draw_workflow_preview_payload(
            {
                "material": "椴木",
                "thickness_mm": "3",
                "mode": "raster",
                "threshold": "-1",
            },
            Path("draw.png"),
        )
        self.assertIsNone(auto_error)
        self.assertEqual(auto_payload["threshold"], -1)

        zero_payload, zero_error = laser_web_server._draw_workflow_preview_payload(
            {
                "material": "椴木",
                "thickness_mm": "3",
                "mode": "raster",
                "threshold": "0",
            },
            Path("draw.png"),
        )
        self.assertIsNone(zero_error)
        self.assertEqual(zero_payload["threshold"], 0)

        omitted, omitted_error = laser_web_server._draw_workflow_preview_payload(
            {
                "material": "椴木",
                "thickness_mm": "3",
                "mode": "raster",
            },
            Path("draw.png"),
        )
        self.assertIsNone(omitted_error)
        self.assertNotIn("threshold", omitted)

        bad, bad_error = laser_web_server._draw_workflow_preview_payload(
            {
                "material": "椴木",
                "thickness_mm": "3",
                "mode": "raster",
                "threshold": "256",
            },
            Path("draw.png"),
        )
        self.assertIsNone(bad)
        self.assertIn("threshold", bad_error)

    def test_draw_preview_default_runner_is_trusted_draw_entry(self):
        temp_dir = Path(self.make_temp_dir())
        calls = {}

        def fake_trusted_runner(**kwargs):
            calls.update(kwargs)
            return {
                "success": True,
                "result": {
                    "workflow_id": "wf-draw",
                    "artifacts": {"image_file": kwargs["image_file"]},
                    "speech": "预览已生成",
                },
            }

        with patch.object(laser_web_server, "DRAW_OUTPUT_DIR", temp_dir), patch.object(
            laser_web_server, "DRAW_JOB_OUTPUT_DIR", temp_dir / "jobs"
        ), patch.object(
            laser_web_server, "DRAW_MATERIAL_LIBRARY_FILE", temp_dir / "materials_from_lasergrbl.json"
        ), patch.object(
            laser_web_server.laser_material_calibration_tool, "MATERIAL_PARAMS_FILE", str(temp_dir / "missing-materials.json")
        ), patch.object(
            laser_web_server.laser_workflow_tool, "preview_draw_lab_image", side_effect=fake_trusted_runner
        ) as trusted:
            result = laser_web_server.draw_preview_from_payload(
                {
                    "image_data_url": self.data_url(),
                    "scene_json": {"type": "excalidraw", "elements": []},
                    "material": "亚克力",
                    "thickness_mm": "3",
                    "mode": "raster",
                    "material_match_policy": "exact_only",
                    "send_policy": "verified_only",
                }
            )

        self.assertTrue(result["success"], result)
        trusted.assert_called_once()
        self.assertEqual(calls["material"], "亚克力")
        self.assertEqual(calls["thickness_mm"], 3.0)
        self.assertNotIn("material_match_policy", calls)
        self.assertNotIn("send_policy", calls)

    def test_connection_check_routes_to_existing_network_probe_only(self):
        with patch.object(
            laser_web_server.check_laser_connection_tool,
            "_check_network_connection",
            return_value=(True, {"host": "laser.local", "probe_command": "?"}),
        ) as checker:
            result = laser_web_server.connection_check_from_payload(
                {
                    "network_host": "laser.local",
                    "network_transport": "telnet",
                    "network_http_port": "8080",
                    "network_telnet_port": "2323",
                    "network_timeout": "1.5",
                    "include_detail": True,
                }
            )

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"]["summary"], "网络已连接")
        self.assertFalse(result["result"]["serial_connected"])
        self.assertTrue(result["result"]["network_connected"])
        self.assertEqual(result["result"]["detail"]["network"]["probe_command"], "?")
        checker.assert_called_once_with(
            host="laser.local",
            transport="telnet",
            http_port=8080,
            telnet_port=2323,
            timeout=1.5,
        )

    def test_draw_links_include_frontend_job_summary(self):
        result = laser_web_server._add_draw_links(
            {
                "success": True,
                "result": {
                    "workflow_id": "wf-draw",
                    "status": "preview_ready",
                    "next_actions": ["confirm_send", "status"],
                    "auto_adjustment": {
                        "reason": "raster_output_too_complex",
                        "pixel_size_mm": 0.2,
                    },
                    "input": {
                        "material": "椴木",
                        "thickness_mm": 3,
                        "mode": "raster",
                        "task_type": "engrave_photo",
                    },
                    "artifacts": {
                        "gcode_file": "C:/tmp/draw.gcode",
                        "time_estimate": {"estimated_seconds": 94},
                    },
                    "summary": {
                        "material": "椴木",
                        "thickness_mm": 3,
                        "mode": "raster",
                        "task_type": "engrave_photo",
                        "power": 380,
                        "speed": 1500,
                        "passes": 1,
                        "can_send": True,
                        "time_estimate": {"estimated_seconds": 94},
                        "raster": {
                            "width_mm": 34.5,
                            "height_mm": 18.2,
                            "pixel_size_mm": 0.05,
                            "scaled": True,
                            "scale_factor": 0.75,
                        },
                        "safety_report": {
                            "warnings": ["请先做小样测试"],
                        },
                    },
                },
            }
        )

        summary = result["result"]["draw_lab"]["job_summary"]
        self.assertEqual(summary["workflow_id"], "wf-draw")
        self.assertEqual(summary["material"], "椴木")
        self.assertEqual(summary["actual_width_mm"], 34.5)
        self.assertEqual(summary["actual_height_mm"], 18.2)
        self.assertEqual(summary["pixel_size_mm"], 0.05)
        self.assertEqual(summary["estimated_display"], "1 分 34 秒")
        self.assertTrue(summary["can_send"])
        self.assertEqual(summary["auto_adjustment"]["pixel_size_mm"], 0.2)
        self.assertIn("缩小", summary["size_note"])
        self.assertIn("请先做小样测试", summary["warnings"])
        self.assertIn("点距调整到 0.2 mm", "；".join(summary["warnings"]))

    def test_draw_preview_rejects_non_image_data_url_before_workflow(self):
        def fake_workflow_runner(**kwargs):
            self.fail("workflow must not be called for invalid upload")

        result = laser_web_server.draw_preview_from_payload(
            {
                "image_data_url": "data:text/plain;base64,aGVsbG8=",
                "material": "椴木",
                "thickness_mm": "3",
            },
            workflow_runner=fake_workflow_runner,
        )

        self.assertFalse(result["success"])
        self.assertIn("image_data_url", result["result"])

    def test_draw_preview_requires_material_and_thickness_before_saving(self):
        temp_dir = Path(self.make_temp_dir())

        with patch.object(laser_web_server, "DRAW_OUTPUT_DIR", temp_dir):
            result = laser_web_server.draw_preview_from_payload(
                {
                    "image_data_url": self.data_url(),
                    "material": "",
                    "thickness_mm": "0",
                }
            )

        self.assertFalse(result["success"])
        self.assertEqual(list(temp_dir.glob("*")), [])

    def test_converts_lasergrbl_material_library_for_draw_image_workflow(self):
        temp_dir = Path(self.make_temp_dir())
        params_file = temp_dir / ".lasergrbl_materials.json"
        converted_file = temp_dir / "materials_from_lasergrbl.json"
        params_file.write_text(
            json.dumps(
                {
                    "version": 1,
                    "materials": {
                        "椴木": {
                            "aliases": ["wood", "basswood", "木头"],
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
                                            "laser_max_power": 480,
                                            "feed_rate": 400,
                                            "passes": 1,
                                            "source": "manual",
                                        },
                                    },
                                    "cut": {
                                        "laser_max_power": 500,
                                        "feed_rate": 100,
                                        "passes": 2,
                                        "source": "manual",
                                    },
                                }
                            },
                        }
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        path, error = laser_web_server._write_draw_material_library(params_file=params_file, output_file=converted_file)

        self.assertIsNone(error)
        self.assertEqual(path, str(converted_file))
        payload = json.loads(converted_file.read_text(encoding="utf-8"))
        records = payload["materials"]
        self.assertEqual(len(records), 3)
        raster = next(record for record in records if record["task_type"] == "engrave_photo")
        outline = next(record for record in records if record["task_type"] == "engrave_logo")
        cut = next(record for record in records if record["task_type"] == "cut_contour")
        self.assertEqual(raster["power"], 380)
        self.assertEqual(raster["speed"], 1500)
        self.assertEqual(raster["material"], "wood")
        self.assertIn("椴木", raster["aliases"])
        self.assertEqual(raster["confidence"], "verified")
        self.assertEqual(raster["material_group"], "wood_like")
        self.assertEqual(outline["mode"], "outline")
        self.assertEqual(cut["passes"], 2)

    def test_generate_payload_routes_through_workflow_preview(self):
        calls = {}

        def fake_generator(**kwargs):
            self.fail("generator should be passed through, not called by web wrapper")

        def fake_workflow_runner(**kwargs):
            calls.update(kwargs)
            return {
                "success": True,
                "result": {
                    "workflow_id": "wf-1",
                    "task_id": "task-1",
                    "attempt_no": 1,
                    "image_file": "generated.png",
                    "gcode_file": "generated.gcode",
                },
            }

        result = laser_web_server.generate_text_task_from_payload(
            {
                "text": "佳佳",
                "material": "椴木",
                "thickness_mm": "3",
                "laser_mode": "engrave",
                "engraving_mode": "raster",
                "width_mm": "30",
                "height_mm": "",
                "font_size": "88",
                "laser_max_power": "380",
                "feed_rate": "1500",
                "passes": "2",
                "pixel_size_mm": "0.05",
                "threshold": "-1",
                "raster_scan_direction": "vertical",
                "auto_wrap": "true",
                "bidirectional": "true",
                "invert": "true",
                "lock_aspect_ratio": "false",
                "manual_params_confirmed": True,
                "confirmed": True,
                "network_host": "laser.local",
                "reuse_prepared_gcode": False,
            },
            generator=fake_generator,
            workflow_runner=fake_workflow_runner,
        )

        self.assertTrue(result["success"], result)
        self.assertEqual(calls["action"], "preview")
        self.assertEqual(calls["source_type"], "text")
        self.assertIs(calls["text_generator"], fake_generator)
        self.assertEqual(calls["text"], "佳佳")
        self.assertEqual(calls["material"], "椴木")
        self.assertEqual(calls["thickness_mm"], 3.0)
        self.assertEqual(calls["width_mm"], 30.0)
        self.assertEqual(calls["font_size"], 88)
        self.assertEqual(calls["laser_max_power"], 380.0)
        self.assertEqual(calls["feed_rate"], 1500.0)
        self.assertEqual(calls["passes"], 2.0)
        self.assertEqual(calls["pixel_size_mm"], 0.05)
        self.assertEqual(calls["threshold"], -1.0)
        self.assertTrue(calls["auto_wrap"])
        self.assertTrue(calls["bidirectional"])
        self.assertTrue(calls["invert"])
        self.assertFalse(calls["lock_aspect_ratio"])
        self.assertTrue(calls["manual_params_confirmed"])
        self.assertEqual(calls["connection_mode"], "network")
        self.assertFalse(calls["send_after_generate"])
        self.assertFalse(calls["confirmed"])
        self.assertTrue(calls["dry_run"])
        self.assertEqual(calls["raster_scan_direction"], "vertical")
        self.assertFalse(calls["reuse_prepared_gcode"])
        self.assertEqual(result["result"]["workflow_id"], "wf-1")
        self.assertIn("image_preview_url", result["result"]["web"])
        self.assertIn("gcode_download_url", result["result"]["web"])

    def test_generate_payload_strips_unconfirmed_manual_overrides(self):
        calls = {}

        def fake_workflow_runner(**kwargs):
            calls.update(kwargs)
            return {
                "success": True,
                "result": {
                    "workflow_id": "wf-strip",
                    "task_id": "task-1",
                    "image_file": "generated.png",
                    "gcode_file": "generated.gcode",
                },
            }

        result = laser_web_server.generate_text_task_from_payload(
            {
                "text": "佳佳",
                "material": "椴木",
                "thickness_mm": "3",
                "laser_max_power": "380",
                "feed_rate": "1500",
                "passes": "2",
                "power_percent": "66",
                "pixel_size_mm": "0.05",
            },
            workflow_runner=fake_workflow_runner,
        )
        self.assertTrue(result["success"], result)
        for key in ("laser_max_power", "feed_rate", "passes", "power_percent", "pixel_size_mm", "manual_params_confirmed"):
            self.assertNotIn(key, calls)

    def test_generate_payload_blocks_unsafe_materials_before_workflow(self):
        def fake_workflow_runner(**kwargs):
            self.fail("workflow runner must not run for blocked/clarify materials")

        for material, error_code in (("软卡", "material_clarify"), ("PVC", "material_blocked"), ("塑料", "material_clarify")):
            with self.subTest(material=material):
                result = laser_web_server.generate_text_task_from_payload(
                    {
                        "text": "佳佳",
                        "material": material,
                        "thickness_mm": "3",
                    },
                    workflow_runner=fake_workflow_runner,
                )
                self.assertFalse(result["success"], result)
                self.assertEqual(result.get("error_code"), error_code)

    def test_web_form_exposes_collapsible_advanced_generation_settings(self):
        html = laser_web_server.load_main_index_html()

        self.assertIn("const MAX_CHARS = 24", html)
        self.assertIn("id=\"char-counter\"", html)
        self.assertIn("id=\"power\"", html)
        self.assertIn("id=\"speed\"", html)
        self.assertIn("id=\"thickness\"", html)
        self.assertIn('id="mode-select"', html)
        self.assertIn("updateRecommendation", html)
        self.assertIn("paintSlider", html)
        for control_id in (
            "btn-gen",
            "btn-send",
            "op-test-conn",
            "op-save-cfg",
            "op-status",
        ):
            self.assertIn(f'id="{control_id}"', html)

    def test_web_form_exposes_source_tabs_for_text_and_auto_detected_file(self):
        html = laser_web_server.load_main_index_html()

        self.assertIn('id="file-input"', html)
        self.assertIn('id="gcode-input"', html)
        self.assertIn('id="image-drop"', html)
        self.assertIn('id="gcode-drop"', html)
        self.assertIn('data-tool="text"', html)
        self.assertIn('data-tool="image"', html)
        self.assertIn('data-tool="gcode"', html)
        self.assertIn('data-tool="draw"', html)
        self.assertIn("handleImageFile", html)
        self.assertIn("handleGcodeFile", html)

    def test_web_form_links_to_draw_page(self):
        html = laser_web_server.load_main_index_html()

        self.assertIn('id="draw-excalidraw"', html)
        self.assertIn("Excalidraw", html)
        self.assertIn('data-tool="draw"', html)
        self.assertIn("initDrawPad", html)

    def test_material_lab_snapshot_reads_lasergrbl_material_library(self):
        temp_dir = Path(self.make_temp_dir())
        params_file = temp_dir / ".lasergrbl_materials.json"
        params_file.write_text(
            json.dumps(
                {
                    "version": 1,
                    "materials": {
                        "测试板": {
                            "aliases": ["test board", "样板"],
                            "thicknesses": {
                                "2": {
                                    "engrave": {
                                        "raster": {
                                            "laser_max_power": 333,
                                            "feed_rate": 1444,
                                            "passes": 1,
                                            "pixel_size_mm": 0.12,
                                            "source": "unit-test",
                                        }
                                    },
                                    "cut": {
                                        "laser_max_power": 666,
                                        "feed_rate": 222,
                                        "passes": 2,
                                    },
                                }
                            },
                        }
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        snapshot = laser_web_server.material_lab_snapshot(params_file=params_file)

        self.assertIsNone(snapshot["error"])
        self.assertEqual(snapshot["source_path"], str(params_file))
        self.assertFalse(snapshot["prototype"])
        self.assertIn("network_telnet_port", snapshot["defaults"])
        material = snapshot["materials"]["测试板"]
        self.assertIn("样板", material["aliases"])
        raster = material["thicknesses"]["2"]["engrave"]["raster"]
        self.assertEqual(raster["laser_max_power"], 333)
        self.assertEqual(raster["feed_rate"], 1444)
        self.assertEqual(raster["source"], "unit-test")

    def test_material_lab_page_exposes_management_and_matrix_controls(self):
        temp_dir = Path(self.make_temp_dir())
        params_file = temp_dir / ".lasergrbl_materials.json"
        params_file.write_text(
            json.dumps(
                {
                    "version": 1,
                    "materials": {
                        "测试板": {
                            "aliases": ["样板"],
                            "thicknesses": {
                                "2": {
                                    "engrave": {
                                        "raster": {
                                            "laser_max_power": 300,
                                            "feed_rate": 1200,
                                            "passes": 1,
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

        html = laser_web_server.render_material_lab_html(params_file=params_file)

        self.assertIn("材料参数与测试矩阵 Lab", html)
        self.assertIn("新增材料", html)
        self.assertIn("复制一套参数", html)
        self.assertIn("删除当前参数", html)
        self.assertIn("从最近任务保存", html)
        self.assertIn("生成测试矩阵预览", html)
        self.assertIn("确认发送测试矩阵", html)
        self.assertIn("保存最佳格到材料库", html)
        self.assertIn("雕刻填充矩阵", html)
        self.assertIn("切割方框矩阵", html)
        self.assertIn("line/mm", html)
        self.assertIn("buildEngraveMatrix", html)
        self.assertIn("buildCutMatrix", html)
        self.assertIn("/api/material-lab/preview", html)
        self.assertIn("/api/material-lab/send", html)
        self.assertIn("/api/material-lab/select-cell", html)
        self.assertIn("/api/material-lab/manage", html)
        self.assertIn("/api/material-lab/recent", html)
        self.assertNotIn('data-prototype="保存参数"', html)
        self.assertIn('id="matrixHost"', html)
        self.assertIn('id="matrixLinks"', html)
        self.assertIn("测试板", html)

    def test_material_lab_manage_saves_and_deletes_current_param_entry(self):
        params_file = Path(self.make_temp_dir()) / "materials.json"

        saved = laser_web_server.material_lab_manage_from_payload(
            {
                "action": "save",
                "material": "测试软卡",
                "aliases": ["软卡片"],
                "thickness_mm": "1",
                "laser_mode": "engrave",
                "engraving_mode": "outline",
                "laser_min_power": "200",
                "laser_max_power": "300",
                "feed_rate": "400",
                "travel_rate": "3000",
                "pixel_size_mm": "0.1",
                "threshold": "-1",
                "passes": "1",
                "notes": "Web 保存",
            },
            params_file=params_file,
        )

        self.assertTrue(saved["success"], saved)
        entry = saved["result"]["materials"]["测试软卡"]
        self.assertEqual(entry["aliases"], ["软卡片"])
        params = entry["thicknesses"]["1"]["engrave"]["outline"]
        self.assertEqual(params["laser_min_power"], 200)
        self.assertEqual(params["laser_max_power"], 300)
        self.assertEqual(params["feed_rate"], 400)

        resaved = laser_web_server.material_lab_manage_from_payload(
            {
                "action": "save",
                "material": "测试软卡",
                "aliases": [],
                "thickness_mm": 1,
                "laser_mode": "engrave",
                "engraving_mode": "outline",
                "laser_min_power": 200,
                "laser_max_power": 300,
                "feed_rate": 400,
                "travel_rate": 3000,
                "pixel_size_mm": 0.1,
                "threshold": -1,
                "passes": 1,
            },
            params_file=params_file,
        )
        self.assertTrue(resaved["success"], resaved)
        self.assertEqual(resaved["result"]["materials"]["测试软卡"]["aliases"], [])

        rejected = laser_web_server.material_lab_manage_from_payload(
            {
                "action": "delete",
                "material": "测试软卡",
                "thickness_mm": 1,
                "laser_mode": "engrave",
                "engraving_mode": "outline",
            },
            params_file=params_file,
        )
        self.assertFalse(rejected["success"], rejected)
        self.assertIn("confirmed=true", rejected["result"])

        deleted = laser_web_server.material_lab_manage_from_payload(
            {
                "action": "delete",
                "confirmed": True,
                "material": "测试软卡",
                "thickness_mm": 1,
                "laser_mode": "engrave",
                "engraving_mode": "outline",
            },
            params_file=params_file,
        )
        self.assertTrue(deleted["success"], deleted)
        self.assertNotIn("测试软卡", deleted["result"]["materials"])
        recommendation = laser_web_server.laser_material_calibration_tool.recommend_laser_params(
            "测试软卡", 1, "engrave", "outline", params_file=str(params_file)
        )
        self.assertFalse(recommendation["success"], recommendation)

    def test_material_lab_recent_params_uses_latest_workflow_with_resolved_params(self):
        workflows_dir = Path(self.make_temp_dir())
        (workflows_dir / "older.json").write_text(
            json.dumps(
                {
                    "workflow_id": "wf-older",
                    "updated_at": 1,
                    "input": {"material": "旧材料", "thickness_mm": 2},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (workflows_dir / "newer.json").write_text(
            json.dumps(
                {
                    "workflow_id": "wf-newer",
                    "updated_at": 2,
                    "input": {
                        "material": "测试板",
                        "thickness_mm": 1,
                        "laser_mode": "engrave",
                        "engraving_mode": "raster",
                    },
                    "last_preview_result": {
                        "params": {
                            "laser_min_power": 100,
                            "laser_max_power": 320,
                            "feed_rate": 1100,
                            "travel_rate": 3000,
                            "pixel_size_mm": 0.1,
                            "threshold": -1,
                            "passes": 1,
                        }
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result = laser_web_server.material_lab_recent_params(workflows_dir=workflows_dir)

        self.assertTrue(result["success"], result)
        recent = result["result"]
        self.assertEqual(recent["workflow_id"], "wf-newer")
        self.assertEqual(recent["material"], "测试板")
        self.assertEqual(recent["thickness_mm"], 1)
        self.assertEqual(recent["engraving_mode"], "raster")
        self.assertEqual(recent["params"]["laser_max_power"], 320)

    def test_material_lab_recent_params_rejects_incomplete_workflow_params(self):
        workflows_dir = Path(self.make_temp_dir())
        (workflows_dir / "incomplete.json").write_text(
            json.dumps(
                {
                    "workflow_id": "wf-incomplete",
                    "updated_at": 1,
                    "input": {"material": "测试板", "thickness_mm": 1},
                    "last_preview_result": {"params": {"laser_max_power": 300, "feed_rate": 1200}},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result = laser_web_server.material_lab_recent_params(workflows_dir=workflows_dir)

        self.assertFalse(result["success"], result)
        self.assertIn("没有可保存的完整材料参数", result["result"])

    def test_material_lab_preview_maps_cut_matrix_to_calibration_tool(self):
        temp_dir = Path(self.make_temp_dir())
        gcode_file = temp_dir / "cut-grid.gcode"
        session_file = temp_dir / "cut-grid.json"
        gcode_file.write_text("G21\nG90\nM4 S100\nG1 X10 F600\n", encoding="utf-8")
        session_file.write_text("{}", encoding="utf-8")
        calls = {}

        def fake_runner(**kwargs):
            calls.update(kwargs)
            return {
                "success": True,
                "result": {
                    "calibration_id": "cal-cut",
                    "gcode_file": str(gcode_file),
                    "session_file": str(session_file),
                    "stats": {"cell_count": 20},
                },
            }

        with patch.object(laser_web_server, "GCODE_PREVIEW_DIR", temp_dir / "previews"):
            result = laser_web_server.material_lab_preview_from_payload(
                {
                    "material": "椴木",
                    "thickness_mm": "3",
                    "laser_mode": "cut",
                    "matrix_kind": "cut",
                    "rows": "4",
                    "columns": "5",
                    "power_min": "900",
                    "power_max": "900",
                    "speed_min": "120",
                    "speed_max": "360",
                    "pass_min": "1",
                    "pass_max": "4",
                    "cell_size_mm": "10",
                    "network_host": "192.0.2.50",
                    "network_telnet_port": "23",
                },
                calibration_runner=fake_runner,
                params_file=temp_dir / "materials.json",
            )

        self.assertTrue(result["success"], result)
        self.assertEqual(calls["matrix_kind"], "cut")
        self.assertEqual(calls["laser_mode"], "cut")
        self.assertEqual(calls["engraving_mode"], "")
        self.assertEqual(calls["rows"], 4)
        self.assertEqual(calls["columns"], 5)
        self.assertEqual(calls["power_min"], 900)
        self.assertEqual(calls["power_max"], 900)
        self.assertEqual(calls["pass_min"], 1)
        self.assertEqual(calls["pass_max"], 4)
        self.assertEqual(calls["connection_mode"], "network")
        self.assertEqual(calls["network_host"], "192.0.2.50")
        self.assertFalse(calls["confirmed"])
        self.assertFalse(calls["dry_run"])
        web = result["result"]["web"]
        self.assertIn("gcode_download_url", web)
        self.assertIn("gcode_preview_url", web)
        self.assertIn("calibration_session_url", web)

    def test_material_lab_send_requires_confirmation_and_network_host(self):
        with patch.object(laser_web_server.laser_network_grbl_tool, "DEFAULT_NETWORK_HOST", ""):
            missing_confirmation = laser_web_server.material_lab_send_from_payload(
                {"calibration_id": "cal-1", "network_host": "laser.local"}
            )
            missing_host = laser_web_server.material_lab_send_from_payload(
                {"calibration_id": "cal-1", "confirmed": True}
            )

        self.assertFalse(missing_confirmation["success"])
        self.assertIn("confirmed=true", missing_confirmation["result"])
        self.assertFalse(missing_host["success"])
        self.assertIn("host/IP", missing_host["result"])

        calls = {}

        def fake_runner(**kwargs):
            calls.update(kwargs)
            return {"success": True, "result": {"calibration_id": kwargs["calibration_id"], "send_result": {"job_id": "job-1"}}}

        result = laser_web_server.material_lab_send_from_payload(
            {
                "calibration_id": "cal-1",
                "confirmed": True,
                "network_host": "laser.local",
                "network_telnet_port": "23",
            },
            calibration_runner=fake_runner,
        )

        self.assertTrue(result["success"], result)
        self.assertTrue(calls["confirmed"])
        self.assertEqual(calls["connection_mode"], "network")
        self.assertEqual(calls["network_host"], "laser.local")

    def test_material_lab_select_cell_saves_through_calibration_tool(self):
        temp_dir = Path(self.make_temp_dir())
        calls = {}

        def fake_selector(**kwargs):
            calls.update(kwargs)
            return {"success": True, "result": {"params": {"laser_max_power": 500, "feed_rate": 1200, "passes": 2}}}

        result = laser_web_server.material_lab_select_cell_from_payload(
            {
                "calibration_id": "cal-1",
                "cell_number": "7",
                "material": "椴木",
                "thickness_mm": "3",
                "laser_mode": "engrave",
                "engraving_mode": "raster",
                "notes": "第 7 格最好",
            },
            selector=fake_selector,
            params_file=temp_dir / "materials.json",
        )

        self.assertTrue(result["success"], result)
        self.assertEqual(calls["calibration_id"], "cal-1")
        self.assertEqual(calls["cell_number"], 7)
        self.assertEqual(calls["material"], "椴木")
        self.assertEqual(calls["thickness_mm"], 3.0)
        self.assertEqual(calls["engraving_mode"], "raster")
        self.assertEqual(calls["params_file"], str(temp_dir / "materials.json"))

    def test_draw_static_file_serves_index_and_assets_safely(self):
        temp_dir = Path(self.make_temp_dir())
        index_file = temp_dir / "index.html"
        asset_dir = temp_dir / "assets"
        asset_file = asset_dir / "app.js"
        asset_dir.mkdir(parents=True)
        index_file.write_text("<div id='root'></div>", encoding="utf-8")
        asset_file.write_text("console.log('draw')", encoding="utf-8")

        with patch.object(laser_web_server, "DRAW_STATIC_DIR", temp_dir):
            self.assertEqual(laser_web_server._draw_static_file_for_path("/draw"), index_file.resolve())
            self.assertEqual(laser_web_server._draw_static_file_for_path("/draw/"), index_file.resolve())
            self.assertEqual(laser_web_server._draw_static_file_for_path("/draw/assets/app.js"), asset_file.resolve())
            self.assertEqual(laser_web_server._draw_static_file_for_path("/assets/app.js"), asset_file.resolve())
            self.assertIsNone(laser_web_server._draw_static_file_for_path("/draw/../secret.txt"))

    def test_v2_static_file_serves_vendor_assets_safely(self):
        temp_dir = Path(self.make_temp_dir())
        vendor_dir = temp_dir / "vendor"
        vendor_dir.mkdir(parents=True)
        bundle = vendor_dir / "lucide.min.js"
        css = vendor_dir / "excalidraw" / "index.css"
        css.parent.mkdir(parents=True)
        bundle.write_text("window.lucide = {}", encoding="utf-8")
        css.write_text(".excalidraw{}", encoding="utf-8")

        with patch.object(laser_web_server, "MAIN_UI_STATIC_DIR", temp_dir):
            self.assertEqual(
                laser_web_server._v2_static_file_for_path("/vendor/lucide.min.js"),
                bundle.resolve(),
            )
            self.assertEqual(
                laser_web_server._v2_static_file_for_path("/vendor/excalidraw/index.css"),
                css.resolve(),
            )
            self.assertIsNone(laser_web_server._v2_static_file_for_path("/vendor/../secret.txt"))
            self.assertIsNone(laser_web_server._v2_static_file_for_path("/other/path.js"))
            self.assertIsNone(laser_web_server._v2_static_file_for_path("/vendor/missing.js"))

    def test_save_uploaded_file_accepts_safe_gcode_under_upload_root(self):
        temp_dir = Path(self.make_temp_dir())
        content = b"G21\nG90\n"

        result = laser_web_server.save_uploaded_file_from_payload(
            {
                "filename": "demo.gcode",
                "content_base64": base64.b64encode(content).decode("ascii"),
            },
            upload_dir=temp_dir,
        )

        self.assertTrue(result["success"], result)
        saved = Path(result["result"]["file_path"])
        self.assertTrue(saved.is_file())
        self.assertTrue(saved.resolve().is_relative_to(temp_dir.resolve()))
        self.assertEqual(saved.read_bytes(), content)
        self.assertEqual(result["result"]["suffix"], ".gcode")

    def test_save_uploaded_file_rejects_unsupported_suffix(self):
        temp_dir = Path(self.make_temp_dir())

        result = laser_web_server.save_uploaded_file_from_payload(
            {
                "filename": "bad.exe",
                "content_base64": base64.b64encode(b"nope").decode("ascii"),
            },
            upload_dir=temp_dir,
        )

        self.assertFalse(result["success"], result)
        self.assertIn("不支持", result["result"])

    def test_generate_payload_keeps_cut_mode_independent_from_engraving_mode(self):
        calls = {}

        def fake_workflow_runner(**kwargs):
            calls.update(kwargs)
            return {
                "success": True,
                "result": {
                    "workflow_id": "wf-cut",
                    "task_id": "task-cut",
                    "attempt_no": 1,
                    "image_file": "cut.png",
                    "gcode_file": "cut.gcode",
                },
            }

        result = laser_web_server.generate_text_task_from_payload(
            {
                "text": "佳佳",
                "material": "椴木",
                "thickness_mm": "3",
                "laser_mode": "cut",
                "engraving_mode": "outline",
            },
            workflow_runner=fake_workflow_runner,
        )

        self.assertTrue(result["success"], result)
        self.assertEqual(calls["laser_mode"], "cut")
        self.assertEqual(calls["engraving_mode"], "")
        self.assertEqual(result["result"]["workflow_id"], "wf-cut")

    def test_web_form_default_font_size_matches_backend_default(self):
        html = laser_web_server.load_main_index_html()

        self.assertIn("text-input", html)
        self.assertIn("updateCounter", html)
        self.assertIn("MAX_CHARS", html)

    def test_embedded_web_pages_define_mobile_safe_responsive_layouts(self):
        task_html = laser_web_server.load_main_index_html()
        material_html = laser_web_server.MATERIAL_LAB_HTML

        for html in (task_html, material_html):
            self.assertIn("viewport-fit=cover", html)
        self.assertIn("@media (max-width: 640px)", task_html)
        self.assertIn("@media (max-width: 1020px)", task_html)
        self.assertIn("@media (prefers-reduced-motion: reduce)", task_html)
        self.assertIn(".grid2, .grid3, .grid4 { grid-template-columns: repeat(2", material_html)
        self.assertIn(".mode-tabs { grid-template-columns: repeat(3", material_html)
        self.assertIn(".matrix-wrap {\n      overflow: auto;", material_html)
        self.assertIn("overscroll-behavior-inline: contain", material_html)

    def test_render_gcode_preview_image_writes_png(self):
        temp_dir = Path(self.make_temp_dir())
        gcode_file = temp_dir / "job.nc"
        gcode_file.write_text("G21\nG90\nM4 S100\nG1 X10 F600\nG1 Y8\n", encoding="utf-8")

        preview = laser_web_server.render_gcode_preview_image(str(gcode_file), output_dir=temp_dir / "previews")

        self.assertIsNotNone(preview)
        preview_path = Path(preview)
        self.assertTrue(preview_path.is_file())
        self.assertEqual(preview_path.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")

    def test_preview_download_url_preserves_gcode_filename(self):
        url = laser_web_server._preview_url(r"C:\jobs\attempt_1.gcode", download=True)

        self.assertIn("/preview?path=", url)
        self.assertIn("attempt_1.gcode", url)
        self.assertTrue(url.endswith("&download=1"))

    def test_preview_download_url_accepts_filename_hint(self):
        url = laser_web_server._preview_url(
            r"C:\jobs\attempt_1.gcode",
            download=True,
            filename="你是_attempt_2_abcdef12.gcode",
        )

        self.assertIn("download=1", url)
        self.assertIn("filename=", url)
        self.assertIn("%E4%BD%A0%E6%98%AF_attempt_2_abcdef12.gcode", url)

    def test_download_content_disposition_uses_original_filename(self):
        header = laser_web_server._download_content_disposition(Path("中文 文件.nc"))

        self.assertIn("attachment", header)
        self.assertIn("filename=", header)
        self.assertIn("filename*=UTF-8''", header)
        self.assertIn("%E4%B8%AD%E6%96%87%20%E6%96%87%E4%BB%B6.nc", header)

    def test_download_content_disposition_uses_filename_hint(self):
        header = laser_web_server._download_content_disposition(
            Path("attempt_1.gcode"),
            "你是_attempt_2_abcdef12.gcode",
        )

        self.assertIn("attachment", header)
        self.assertIn("filename*=UTF-8''", header)
        self.assertIn("%E4%BD%A0%E6%98%AF_attempt_2_abcdef12.gcode", header)

    def test_extract_gcode_preview_paths_samples_ij_arcs(self):
        temp_dir = Path(self.make_temp_dir())
        gcode_file = temp_dir / "arc.nc"
        gcode_file.write_text("G21\nG90\nM4 S100\nG0 X10 Y0\nG3 X0 Y10 I-10 J0\n", encoding="utf-8")

        paths = laser_web_server._extract_gcode_preview_paths(gcode_file)

        self.assertEqual(len(paths), 1)
        self.assertGreater(len(paths[0]), 2)
        self.assertAlmostEqual(paths[0][0][0], 10.0)
        self.assertAlmostEqual(paths[0][0][1], 0.0)
        self.assertAlmostEqual(paths[0][-1][0], 0.0, places=6)
        self.assertAlmostEqual(paths[0][-1][1], 10.0, places=6)

    def test_extract_gcode_preview_paths_samples_radius_arcs(self):
        temp_dir = Path(self.make_temp_dir())
        gcode_file = temp_dir / "arc-r.nc"
        gcode_file.write_text("G21\nG90\nM4 S100\nG0 X0 Y0\nG2 X10 Y0 R5\n", encoding="utf-8")

        paths = laser_web_server._extract_gcode_preview_paths(gcode_file)

        self.assertEqual(len(paths), 1)
        self.assertGreater(len(paths[0]), 2)
        self.assertAlmostEqual(paths[0][0][0], 0.0)
        self.assertAlmostEqual(paths[0][-1][0], 10.0, places=6)

    def test_augment_web_links_renders_preview_for_prepared_gcode(self):
        temp_dir = Path(self.make_temp_dir())
        gcode_file = temp_dir / "job.nc"
        gcode_file.write_text("G21\nG90\nM4 S100\nG1 X10 F600\n", encoding="utf-8")

        with patch.object(laser_web_server, "GCODE_PREVIEW_DIR", temp_dir / "previews"):
            result = laser_web_server._augment_web_links(
                {"success": True, "result": {"gcode_file": str(gcode_file), "time_estimate": {"estimated_seconds": 1.0}}}
            )

        web = result["result"]["web"]
        self.assertIn("image_preview_url", web)
        self.assertIn("gcode_preview_url", web)
        self.assertIn("/preview?path=", web["image_preview_url"])
        self.assertIn("gcode_download_url", web)
        self.assertIn("&download=1", web["gcode_download_url"])
        self.assertEqual(web["gcode_download_name"], "job.nc")

    def test_augment_web_links_names_generated_text_download(self):
        temp_dir = Path(self.make_temp_dir())
        gcode_file = temp_dir / "attempt_2.gcode"
        gcode_file.write_text("G21\nG90\nM4 S100\nG1 X10 F600\n", encoding="utf-8")

        with patch.object(laser_web_server, "GCODE_PREVIEW_DIR", temp_dir / "previews"):
            result = laser_web_server._augment_web_links(
                {
                    "success": True,
                    "result": {
                        "task_id": "abcdef123456",
                        "attempt_no": 2,
                        "input": {"text": "你是"},
                        "gcode_file": str(gcode_file),
                    },
                }
            )

        web = result["result"]["web"]
        self.assertEqual(web["gcode_download_name"], "你是_attempt_2_abcdef12.gcode")
        self.assertIn("filename=", web["gcode_download_url"])
        self.assertIn("%E4%BD%A0%E6%98%AF_attempt_2_abcdef12.gcode", web["gcode_download_url"])

    def test_augment_web_links_keeps_prepared_gcode_download_name(self):
        temp_dir = Path(self.make_temp_dir())
        gcode_file = temp_dir / "1.nc"
        gcode_file.write_text("G21\nG90\nM4 S100\nG1 X10 F600\n", encoding="utf-8")

        with patch.object(laser_web_server, "GCODE_PREVIEW_DIR", temp_dir / "previews"):
            result = laser_web_server._augment_web_links(
                {
                    "success": True,
                    "result": {
                        "source": "prepared_gcode_file",
                        "task_id": "abcdef123456",
                        "attempt_no": 2,
                        "input": {"text": "你是"},
                        "gcode_file": str(gcode_file),
                    },
                }
            )

        web = result["result"]["web"]
        self.assertEqual(web["gcode_download_name"], "1.nc")
        self.assertIn("filename=1.nc", web["gcode_download_url"])

    def test_augment_web_links_splits_material_and_gcode_previews(self):
        temp_dir = Path(self.make_temp_dir())
        image_file = temp_dir / "text.png"
        image_file.write_bytes(b"png")
        gcode_file = temp_dir / "job.nc"
        gcode_file.write_text("G21\nG90\nM4 S100\nG1 X10 F600\n", encoding="utf-8")

        with patch.object(laser_web_server, "GCODE_PREVIEW_DIR", temp_dir / "previews"):
            result = laser_web_server._augment_web_links(
                {"success": True, "result": {"image_file": str(image_file), "gcode_file": str(gcode_file)}}
            )

        web = result["result"]["web"]
        self.assertIn("material_preview_url", web)
        self.assertIn("gcode_preview_url", web)
        self.assertEqual(web["image_preview_url"], web["material_preview_url"])

    def test_generate_payload_validates_required_fields(self):
        result = laser_web_server.generate_text_task_from_payload(
            {"text": "", "material": "椴木", "thickness_mm": "3"},
            generator=lambda **kwargs: self.fail("generator should not be called"),
        )

        self.assertFalse(result["success"])
        self.assertIn("text", result["result"])

    def test_send_payload_routes_to_workflow_confirm_send(self):
        calls = {}

        def fake_sender(**kwargs):
            self.fail("sender should be passed through, not called by web wrapper")

        def fake_workflow_runner(**kwargs):
            calls.update(kwargs)
            return {"success": True, "result": {"workflow_id": "wf-1", "job_id": "job-1"}}

        result = laser_web_server.send_text_task_from_payload(
            {
                "workflow_id": "wf-1",
                "network_host": "laser.local",
                "network_http_port": "80",
                "network_telnet_port": "23",
                "network_timeout": "5",
                "confirmed": True,
                "run_in_background": True,
            },
            sender=fake_sender,
            workflow_runner=fake_workflow_runner,
        )

        self.assertTrue(result["success"], result)
        self.assertEqual(calls["action"], "confirm_send")
        self.assertEqual(calls["workflow_id"], "wf-1")
        self.assertIs(calls["tuned_job_sender"], fake_sender)
        self.assertEqual(calls["connection_mode"], "network")
        self.assertEqual(calls["network_host"], "laser.local")
        self.assertEqual(calls["network_transport"], "telnet")
        self.assertEqual(calls["network_http_port"], 80)
        self.assertEqual(calls["network_telnet_port"], 23)
        self.assertEqual(calls["network_timeout"], 5.0)
        self.assertTrue(calls["confirmed"])

    def test_send_payload_without_confirmation_only_previews(self):
        calls = {}

        def fake_workflow_runner(**kwargs):
            calls.update(kwargs)
            return {"success": False, "result": "confirm_send 需要 confirmed=true"}

        result = laser_web_server.send_text_task_from_payload(
            {"workflow_id": "wf-1", "network_host": "laser.local", "confirmed": False},
            workflow_runner=fake_workflow_runner,
        )

        self.assertFalse(result["success"], result)
        self.assertFalse(calls["confirmed"])
        self.assertEqual(calls["action"], "confirm_send")

    def test_workflow_action_endpoint_helper_passes_action_and_workflow_id(self):
        calls = {}

        def fake_workflow_runner(**kwargs):
            calls.update(kwargs)
            return {"success": True, "result": {"workflow_id": "wf-1", "status": "sending"}}

        result = laser_web_server.workflow_action_from_payload(
            {"action": "status", "workflow_id": "wf-1"},
            workflow_runner=fake_workflow_runner,
        )

        self.assertTrue(result["success"], result)
        self.assertEqual(calls["action"], "status")
        self.assertEqual(calls["workflow_id"], "wf-1")

    def test_workflow_action_image_preview_uses_http_trusted_policy_helper(self):
        calls = {}

        def fake_trusted_runner(**kwargs):
            calls.update(kwargs)
            return {
                "success": True,
                "result": {
                    "workflow_id": "wf-http-image",
                    "status": "preview_ready",
                    "source_type": "image",
                    "speech": "预览已生成",
                },
            }

        with patch.object(
            laser_web_server.laser_workflow_tool,
            "preview_http_image_workflow",
            side_effect=fake_trusted_runner,
        ) as trusted:
            result = laser_web_server.workflow_action_from_payload(
                {
                    "action": "preview",
                    "workflow_id": "wf-http-image",
                    "source_type": "image",
                    "image_file": "part.png",
                    "material": "亚克力",
                    "thickness_mm": 3,
                    "task_type": "engrave_photo",
                    "mode": "raster",
                    "material_match_policy": "exact_only",
                    "send_policy": "verified_only",
                }
            )

        self.assertTrue(result["success"], result)
        trusted.assert_called_once()
        self.assertEqual(calls.get("workflow_id"), "wf-http-image")
        self.assertEqual(calls.get("source_type"), "image")
        self.assertEqual(calls.get("material"), "亚克力")
        # Client-forged policy must still reach the trusted helper kwargs only as discarded
        # payload fields; the helper itself pops and pins server-side (see workflow tool tests).
        self.assertEqual(calls.get("material_match_policy"), "exact_only")
        self.assertEqual(calls.get("send_policy"), "verified_only")

    def test_workflow_action_non_image_preview_does_not_use_http_trusted_helper(self):
        ordinary_calls = {}

        def fake_ordinary_runner(**kwargs):
            ordinary_calls.update(kwargs)
            return {"success": True, "result": {"workflow_id": "wf-text", "status": "preview_ready"}}

        with patch.object(
            laser_web_server.laser_workflow_tool,
            "preview_http_image_workflow",
        ) as trusted, patch.object(
            laser_web_server.laser_workflow_tool,
            "run_laser_workflow_action",
            side_effect=fake_ordinary_runner,
        ):
            text_result = laser_web_server.workflow_action_from_payload(
                {
                    "action": "preview",
                    "workflow_id": "wf-text",
                    "source_type": "text",
                    "text": "你好",
                    "material": "wood",
                    "thickness_mm": 3,
                }
            )
            prepared_result = laser_web_server.workflow_action_from_payload(
                {
                    "action": "preview",
                    "workflow_id": "wf-prep",
                    "source_type": "prepared_gcode",
                    "gcode_file": "job.gcode",
                }
            )
            status_result = laser_web_server.workflow_action_from_payload(
                {"action": "status", "workflow_id": "wf-1"}
            )

        self.assertTrue(text_result["success"], text_result)
        self.assertTrue(prepared_result["success"], prepared_result)
        self.assertTrue(status_result["success"], status_result)
        trusted.assert_not_called()
        self.assertEqual(ordinary_calls.get("action"), "status")

    def test_workflow_action_helper_passes_feedback_and_regenerate_payload(self):
        calls = []

        def fake_workflow_runner(**kwargs):
            calls.append(kwargs)
            return {"success": True, "result": {"workflow_id": kwargs.get("workflow_id"), "status": kwargs.get("action")}}

        feedback = laser_web_server.workflow_action_from_payload(
            {"action": "feedback", "workflow_id": "wf-1", "feedback_text": "太焦了"},
            workflow_runner=fake_workflow_runner,
        )
        regenerate = laser_web_server.workflow_action_from_payload(
            {
                "action": "regenerate",
                "workflow_id": "wf-1",
                "feedback_text": "太焦了",
                "strategy": "lower_power",
            },
            workflow_runner=fake_workflow_runner,
        )

        self.assertTrue(feedback["success"], feedback)
        self.assertTrue(regenerate["success"], regenerate)
        self.assertEqual(calls[0]["action"], "feedback")
        self.assertEqual(calls[0]["workflow_id"], "wf-1")
        self.assertEqual(calls[0]["feedback_text"], "太焦了")
        self.assertEqual(calls[1]["action"], "regenerate")
        self.assertEqual(calls[1]["workflow_id"], "wf-1")
        self.assertEqual(calls[1]["feedback_text"], "太焦了")
        self.assertEqual(calls[1]["strategy"], "lower_power")

    def test_index_html_uses_workflow_endpoint_for_send_and_controls(self):
        html = laser_web_server.load_main_index_html()

        # 发送链路未接入：按钮禁用且不再保留模拟发送逻辑。
        self.assertIn('id="btn-send" disabled', html)
        self.assertNotIn("simulateFullJob", html)
        self.assertNotIn("simulateGenerate", html)
        self.assertNotIn('id="btn-sim-send"', html)
        # 真实 API 调用点。
        self.assertIn('apiCall("/api/upload"', html)
        self.assertIn('apiCall("/api/text-task/generate"', html)
        self.assertIn('apiCall("/api/workflow", {', html)
        self.assertIn('apiCall("/api/connection/check"', html)
        self.assertIn('action: "preview"', html)
        self.assertIn('source_type: "image"', html)
        self.assertIn('source_type: "prepared_gcode"', html)
        self.assertIn('action: "status"', html)
        # mode 语义：文字路径 laser_mode=cut/engrave + engraving_mode=outline/raster；图片路径 mode 为 outline/raster。
        self.assertIn('laser_mode: mode === "cut" ? "cut" : "engrave"', html)
        self.assertIn('engraving_mode: mode === "line" ? "outline" : "raster"', html)
        self.assertIn("function imageGenerationMode()", html)
        # 失败路径必须走错误日志而不是模拟成功。
        self.assertIn("生成失败", html)
        self.assertIn("连接检查失败", html)
        self.assertIn("无法连接后端", html)
        # 发送链路已接入：真实 confirm_send / cancel 调用点，无模拟发送逻辑。
        self.assertIn('action: "confirm_send"', html)
        self.assertIn('action: "cancel"', html)
        self.assertIn('payload.network_host = state.host || ""', html)
        self.assertNotIn("发送未接入", html)

    def test_workflow_action_confirm_send_strips_production_form_residue(self):
        calls = {}

        def fake_workflow_runner(**kwargs):
            calls.update(kwargs)
            return {"success": True, "result": {"workflow_id": "wf-confirm", "status": "sending"}}

        result = laser_web_server.workflow_action_from_payload(
            {
                "action": "confirm_send",
                "workflow_id": "wf-confirm",
                "confirmed": True,
                "network_host": "laser.local",
                "network_telnet_port": "23",
                "run_in_background": True,
                "wait_for_response": True,
                # Production residue that used to trip frozen-field gate on main Web.
                "text": "forged",
                "font_size": 48,
                "auto_size": True,
                "auto_trim": True,
                "auto_wrap": True,
                "lock_aspect_ratio": True,
                "reuse_prepared_gcode": True,
                "material": "forged",
                "thickness_mm": 99,
                "power_percent": 99,
            },
            workflow_runner=fake_workflow_runner,
        )

        self.assertTrue(result["success"], result)
        self.assertEqual(calls["action"], "confirm_send")
        self.assertEqual(calls["workflow_id"], "wf-confirm")
        self.assertTrue(calls["confirmed"])
        self.assertEqual(calls["connection_mode"], "network")
        self.assertEqual(calls["network_host"], "laser.local")
        self.assertEqual(calls["network_transport"], "telnet")
        self.assertEqual(calls["network_telnet_port"], 23)
        self.assertTrue(calls["run_in_background"])
        self.assertTrue(calls["wait_for_response"])
        for banned in (
            "text",
            "font_size",
            "auto_size",
            "auto_trim",
            "auto_wrap",
            "lock_aspect_ratio",
            "reuse_prepared_gcode",
            "material",
            "thickness_mm",
            "power_percent",
            "source_type",
        ):
            self.assertNotIn(banned, calls)

    def test_workflow_action_confirm_send_strips_residue_for_all_source_types(self):
        """Text / image / prepared_gcode confirm paths share the same lean HTTP boundary."""
        banned = (
            "text",
            "auto_size",
            "auto_trim",
            "material",
            "thickness_mm",
            "image_file",
            "image_url",
            "gcode_file",
            "source_type",
            "font_size",
            "power_percent",
        )
        for source_type in ("text", "image", "prepared_gcode"):
            with self.subTest(source_type=source_type):
                calls = {}

                def fake_workflow_runner(**kwargs):
                    calls.update(kwargs)
                    return {
                        "success": True,
                        "result": {
                            "workflow_id": f"wf-{source_type}",
                            "status": "sending",
                            "source_type": source_type,
                        },
                    }

                result = laser_web_server.workflow_action_from_payload(
                    {
                        "action": "confirm_send",
                        "workflow_id": f"wf-{source_type}",
                        "confirmed": True,
                        "network_host": "laser.local",
                        "network_telnet_port": "23",
                        "run_in_background": True,
                        "wait_for_response": True,
                        # Client may still attach preview-time production fields.
                        "source_type": source_type,
                        "text": "forged",
                        "auto_size": True,
                        "auto_trim": True,
                        "material": "forged",
                        "thickness_mm": 99,
                        "image_file": "C:/forged.png",
                        "image_url": "https://example.invalid/forged.png",
                        "gcode_file": "C:/forged.gcode",
                        "font_size": 48,
                        "power_percent": 88,
                    },
                    workflow_runner=fake_workflow_runner,
                )

                self.assertTrue(result["success"], result)
                self.assertEqual(calls["action"], "confirm_send")
                self.assertEqual(calls["workflow_id"], f"wf-{source_type}")
                self.assertTrue(calls["confirmed"])
                self.assertEqual(calls["network_host"], "laser.local")
                self.assertEqual(calls["network_transport"], "telnet")
                self.assertTrue(calls["wait_for_response"])
                for key in banned:
                    self.assertNotIn(key, calls, f"{source_type}: unexpected {key} in runner kwargs")

    def test_excalidraw_draw_app_defaults_to_simplified_chinese(self):
        source = (
            laser_web_server.ROOT_DIR
            / "apps"
            / "excalidraw_lab"
            / "web"
            / "src"
            / "main.jsx"
        ).read_text(encoding="utf-8")

        self.assertIn('const EXCALIDRAW_LANG_CODE = "zh-CN";', source)
        self.assertIn("langCode={EXCALIDRAW_LANG_CODE}", source)

    def test_excalidraw_draw_app_configures_line_art_raster_without_changing_outline(self):
        source = (
            laser_web_server.ROOT_DIR
            / "apps"
            / "excalidraw_lab"
            / "web"
            / "src"
            / "main.jsx"
        ).read_text(encoding="utf-8")

        self.assertIn('prepareRasterExportElements(activeElements, form.mode)', source)
        self.assertIn('elements: exportElements', source)
        self.assertIn('exportScene({ forLaser: true })', source)
        self.assertIn('const { blob } = await exportScene();', source)
        self.assertIn('if (form.mode === "raster")', source)
        self.assertNotIn('payload.pixel_size_mm =', source)
        self.assertIn('payload.dither_algorithm = "threshold"', source)
        self.assertIn('payload.raster_scan_direction = "horizontal"', source)
        self.assertIn('payload.raster_output_strategy = "scanline"', source)
        self.assertIn('payload.raster_quality_strategy = "manual"', source)
        self.assertNotIn("withoutEnclosingGuideFrames", source)

    def test_excalidraw_draw_app_links_back_to_main_page(self):
        source = (
            laser_web_server.ROOT_DIR
            / "apps"
            / "excalidraw_lab"
            / "web"
            / "src"
            / "main.jsx"
        ).read_text(encoding="utf-8")

        self.assertIn("function goHome()", source)
        self.assertIn('window.location.href = "/";', source)
        self.assertIn("首页", source)

    def test_excalidraw_draw_app_prioritizes_canvas_on_mobile(self):
        app_dir = laser_web_server.ROOT_DIR / "apps" / "excalidraw_lab" / "web"
        index_html = (app_dir / "index.html").read_text(encoding="utf-8")
        styles = (app_dir / "src" / "styles.css").read_text(encoding="utf-8")

        self.assertIn("viewport-fit=cover", index_html)
        self.assertIn("@media (max-width: 1100px)", styles)
        self.assertIn("min-height: 100dvh", styles)
        self.assertIn("grid-template-rows: minmax(480px, 62dvh) auto auto", styles)
        self.assertIn(".canvas-panel {\n    grid-row: 1;", styles)
        self.assertIn(".control-panel {\n    grid-row: 2;", styles)
        self.assertIn("@media (pointer: coarse)", styles)
        self.assertIn("min-height: 44px", styles)
        self.assertIn(
            ".control-panel input,\n  .control-panel select,\n  .control-panel textarea {\n    font-size: 16px;\n  }",
            styles,
        )

    def test_preview_file_rejects_files_outside_generated_roots(self):
        temp_dir = self.make_temp_dir()
        outside = Path(temp_dir) / "preview.png"
        outside.write_bytes(b"not really png")

        resolved, error = laser_web_server.resolve_preview_file(str(outside))

        self.assertIsNone(resolved)
        self.assertIn("只能预览", error)

    def test_preview_file_allows_configured_generated_root(self):
        temp_dir = Path(self.make_temp_dir()).resolve()
        image = temp_dir / "preview.png"
        image.write_bytes(b"png")

        with patch.object(laser_web_server, "_allowed_file_roots", return_value=[temp_dir]):
            resolved, error = laser_web_server.resolve_preview_file(str(image))

        self.assertIsNone(error)
        self.assertEqual(resolved, image)

    def test_project_ui_material_options_filters_and_sorts(self):
        materials = {
            "椴木": {
                "aliases": ["basswood"],
                "thicknesses": {
                    "5": {"cut": {"laser_max_power": 1000}},
                    "3.0": {"engrave": {"raster": {"laser_max_power": 380}}},
                    "0": {"cut": {}},
                    "bad": {"cut": {}},
                },
            },
            "亚克力": {
                "aliases": ["acrylic"],
                "thicknesses": {
                    "2.5": {"cut": {}},
                    "3": {"cut": {}},
                },
            },
            "无厚度": {"aliases": [], "thicknesses": {"0": {}, "-1": {}}},
            "": {"thicknesses": {"1": {}}},
        }
        projected = laser_web_server.project_ui_material_options(materials)
        self.assertEqual(list(projected.keys()), ["materials"])
        names = [item["name"] for item in projected["materials"]]
        self.assertEqual(names, ["亚克力", "椴木"])
        by_name = {item["name"]: item["thicknesses"] for item in projected["materials"]}
        self.assertEqual(by_name["椴木"], [3, 5])
        self.assertEqual(by_name["亚克力"], [2.5, 3])
        blob = json.dumps(projected, ensure_ascii=False)
        for banned in (
            "source_path",
            "defaults",
            "aliases",
            "network_host",
            "laser_max_power",
            "version",
            "status",
        ):
            self.assertNotIn(banned, blob)

    def test_ui_material_options_response_success_and_empty(self):
        temp_dir = Path(self.make_temp_dir())
        params_file = temp_dir / ".lasergrbl_materials.json"
        params_file.write_text(
            json.dumps(
                {
                    "version": 9,
                    "materials": {
                        "测试板": {
                            "aliases": ["样板"],
                            "thicknesses": {
                                "2": {
                                    "engrave": {
                                        "raster": {
                                            "laser_max_power": 100,
                                            "feed_rate": 1000,
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
        response = laser_web_server.ui_material_options_response(params_file=params_file)
        self.assertTrue(response["success"])
        materials = response["result"]["materials"]
        self.assertIsInstance(materials, list)
        # _load_material_params merges defaults; custom material must appear projected.
        by_name = {item["name"]: item["thicknesses"] for item in materials}
        self.assertIn("测试板", by_name)
        self.assertEqual(by_name["测试板"], [2])
        blob = json.dumps(response["result"], ensure_ascii=False)
        for banned in ("source_path", "defaults", "aliases", "laser_max_power", "version", "status"):
            self.assertNotIn(banned, blob)
        self.assertEqual(response.get("status"), 200)
        # pure empty projection remains success + []
        empty = laser_web_server._build_success(laser_web_server.project_ui_material_options({}))
        self.assertTrue(empty["success"])
        self.assertEqual(empty["result"], {"materials": []})

    def test_ui_material_options_response_read_error(self):
        leaky = {
            "version": 1,
            "source_path": r"C:\Users\secret\.lasergrbl_materials.json",
            "materials": {},
            "error": r"读取材料参数失败: C:\Users\secret\.lasergrbl_materials.json",
            "prototype": False,
            "defaults": {"network_host": "laser.local", "network_telnet_port": 23},
        }
        with patch.object(laser_web_server, "material_lab_snapshot", return_value=leaky):
            response = laser_web_server.ui_material_options_response()
        self.assertFalse(response["success"])
        self.assertEqual(response["result"], "读取材料选项失败")
        self.assertEqual(response.get("status"), 500)
        blob = json.dumps(response, ensure_ascii=False)
        self.assertNotIn("secret", blob)
        self.assertNotIn("source_path", blob)
        self.assertNotIn("laser.local", blob)

        with patch.object(
            laser_web_server,
            "material_lab_snapshot",
            side_effect=RuntimeError(r"boom C:\Users\secret\path"),
        ):
            response = laser_web_server.ui_material_options_response()
        self.assertFalse(response["success"])
        self.assertEqual(response["result"], "读取材料选项失败")
        self.assertEqual(response.get("status"), 500)
        self.assertNotIn("secret", response["result"])
        self.assertNotIn("boom", response["result"])

    def test_ui_material_options_http_route_projection(self):
        from http.client import HTTPConnection
        from threading import Thread
        import socket

        temp_dir = Path(self.make_temp_dir())
        params_file = temp_dir / "materials.json"
        params_file.write_text(
            json.dumps(
                {
                    "version": 1,
                    "materials": {
                        "纸板专用测": {
                            "aliases": ["cardboard-ui-test"],
                            "thicknesses": {
                                "1.5": {"cut": {"laser_max_power": 200, "feed_rate": 800}},
                                "3": {"cut": {"laser_max_power": 300}},
                            },
                        }
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        host, port = sock.getsockname()
        sock.close()
        server = laser_web_server.create_server(host, port)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def cleanup_server():
            try:
                server.shutdown()
            finally:
                thread.join(timeout=2)
                server.server_close()

        self.addCleanup(cleanup_server)

        with patch.object(
            laser_web_server.laser_material_calibration_tool,
            "MATERIAL_PARAMS_FILE",
            str(params_file),
        ):
            conn = HTTPConnection(host, port, timeout=5)
            conn.request("GET", "/api/ui/material-options")
            resp = conn.getresponse()
            body = resp.read().decode("utf-8")
            conn.close()

        self.assertEqual(resp.status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["success"])
        by_name = {item["name"]: item["thicknesses"] for item in payload["result"]["materials"]}
        self.assertIn("纸板专用测", by_name)
        self.assertEqual(by_name["纸板专用测"], [1.5, 3])
        self.assertNotIn("status", payload)
        self.assertNotIn("source_path", body)
        self.assertNotIn("defaults", body)
        self.assertNotIn("aliases", body)
        self.assertNotIn("laser_max_power", body)
        self.assertNotIn('"version"', body)

    def test_index_html_exposes_material_select_controls(self):
        html = laser_web_server.load_main_index_html()
        self.assertIn('id="material-select"', html)
        self.assertIn('id="material-grid"', html)
        self.assertIn("selectMaterial", html)
        self.assertIn('id="draw-material"', html)
        # 材料库已接入后端动态下发：/api/ui/material-options + BroadcastChannel 刷新协议。
        self.assertIn("/api/ui/material-options", html)
        self.assertIn("laser-material-library-v1", html)
        self.assertIn("materials-updated", html)

    def _write_recommendation_params_file(self, path):
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "materials": {
                        "纸板": {
                            "aliases": ["cardboard-ui-rec"],
                            "thicknesses": {
                                "3": {
                                    "engrave": {
                                        "raster": {
                                            "laser_min_power": 0,
                                            "laser_max_power": 300,
                                            "feed_rate": 400,
                                            "travel_rate": 3000,
                                            "pixel_size_mm": 0.1,
                                            "threshold": -1,
                                            "passes": 1,
                                            "source": "manual",
                                            "notes": "secret-note",
                                            "updated_at": 123.0,
                                        },
                                        "outline": {
                                            "laser_min_power": 0,
                                            "laser_max_power": 280,
                                            "feed_rate": 450,
                                            "travel_rate": 3000,
                                            "pixel_size_mm": 0.1,
                                            "threshold": 128,
                                            "passes": 2,
                                            "source": "manual",
                                        },
                                    },
                                    "cut": {
                                        "laser_min_power": 0,
                                        "laser_max_power": 900,
                                        "feed_rate": 200,
                                        "travel_rate": 3000,
                                        "pixel_size_mm": 0.1,
                                        "threshold": 128,
                                        "passes": 3,
                                        "source": "manual",
                                    },
                                }
                            },
                        }
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def test_project_ui_material_recommendation_whitelist(self):
        raw = {
            "material": "纸板",
            "requested_material": "cardboard-ui-rec",
            "thickness_mm": 4.0,
            "matched_thickness_mm": 3.0,
            "match": "nearest_thickness",
            "laser_mode": "engrave",
            "engraving_mode": "outline",
            "params": {
                "laser_min_power": 0,
                "laser_max_power": 280,
                "feed_rate": 450,
                "travel_rate": 3000,
                "pixel_size_mm": 0.1,
                "threshold": 128,
                "passes": 2,
                "source": "manual",
                "notes": "secret-note",
                "updated_at": 99.0,
            },
            "warnings": ["没有 4mm 的精确参数，已使用最接近的 3mm 参数"],
            "params_file": r"C:\Users\secret\.lasergrbl_materials.json",
        }
        projected = laser_web_server.project_ui_material_recommendation(raw)
        self.assertEqual(
            projected,
            {
                "material": "纸板",
                "thickness_mm": 4.0,
                "matched_thickness_mm": 3.0,
                "match": "nearest_thickness",
                "laser_mode": "engrave",
                "engraving_mode": "outline",
                "laser_max_power": 280,
                "feed_rate": 450,
                "passes": 2,
            },
        )
        blob = json.dumps(projected, ensure_ascii=False)
        for banned in (
            "aliases",
            "notes",
            "source",
            "updated_at",
            "params_file",
            "secret",
            "warnings",
            "requested_material",
            "laser_min_power",
            "travel_rate",
            "pixel_size_mm",
            "threshold",
            "defaults",
            "network_host",
        ):
            self.assertNotIn(banned, blob)

    def test_ui_material_recommendation_response_modes_and_alias(self):
        temp_dir = Path(self.make_temp_dir())
        params_file = temp_dir / "materials.json"
        self._write_recommendation_params_file(params_file)

        exact = laser_web_server.ui_material_recommendation_response(
            material="纸板",
            thickness_mm=3,
            laser_mode="engrave",
            engraving_mode="raster",
            params_file=params_file,
        )
        self.assertTrue(exact["success"], exact)
        self.assertEqual(exact["result"]["material"], "纸板")
        self.assertEqual(exact["result"]["thickness_mm"], 3.0)
        self.assertEqual(exact["result"]["matched_thickness_mm"], 3.0)
        self.assertEqual(exact["result"]["match"], "exact")
        self.assertEqual(exact["result"]["laser_mode"], "engrave")
        self.assertEqual(exact["result"]["engraving_mode"], "raster")
        self.assertEqual(exact["result"]["laser_max_power"], 300)
        self.assertEqual(exact["result"]["feed_rate"], 400)
        self.assertEqual(exact["result"]["passes"], 1)

        outline = laser_web_server.ui_material_recommendation_response(
            material="纸板",
            thickness_mm=3,
            laser_mode="engrave",
            engraving_mode="outline",
            params_file=params_file,
        )
        self.assertTrue(outline["success"], outline)
        self.assertEqual(outline["result"]["engraving_mode"], "outline")
        self.assertEqual(outline["result"]["laser_max_power"], 280)
        self.assertEqual(outline["result"]["passes"], 2)

        cut = laser_web_server.ui_material_recommendation_response(
            material="纸板",
            thickness_mm=3,
            laser_mode="cut",
            engraving_mode="",
            params_file=params_file,
        )
        self.assertTrue(cut["success"], cut)
        self.assertEqual(cut["result"]["laser_mode"], "cut")
        self.assertNotIn("engraving_mode", cut["result"])
        self.assertEqual(cut["result"]["laser_max_power"], 900)
        self.assertEqual(cut["result"]["passes"], 3)

        alias = laser_web_server.ui_material_recommendation_response(
            material="cardboard-ui-rec",
            thickness_mm=3,
            laser_mode="engrave",
            engraving_mode="raster",
            params_file=params_file,
        )
        self.assertTrue(alias["success"], alias)
        self.assertEqual(alias["result"]["material"], "纸板")

        nearest = laser_web_server.ui_material_recommendation_response(
            material="纸板",
            thickness_mm=4,
            laser_mode="engrave",
            engraving_mode="raster",
            params_file=params_file,
        )
        self.assertTrue(nearest["success"], nearest)
        self.assertEqual(nearest["result"]["match"], "nearest_thickness")
        self.assertEqual(nearest["result"]["thickness_mm"], 4.0)
        self.assertEqual(nearest["result"]["matched_thickness_mm"], 3.0)
        self.assertEqual(nearest["result"]["laser_max_power"], 300)

        missing = laser_web_server.ui_material_recommendation_response(
            material="不存在的材料XYZ",
            thickness_mm=3,
            laser_mode="engrave",
            engraving_mode="raster",
            params_file=params_file,
        )
        self.assertFalse(missing["success"])
        self.assertEqual(missing["result"], "读取材料推荐失败")
        self.assertEqual(missing.get("status"), 400)
        self.assertNotIn("secret", str(missing))
        self.assertNotIn(str(params_file), str(missing))

        bad_args = laser_web_server.ui_material_recommendation_response(
            material="",
            thickness_mm=3,
            laser_mode="engrave",
            engraving_mode="raster",
            params_file=params_file,
        )
        self.assertFalse(bad_args["success"])
        self.assertEqual(bad_args["result"], "读取材料推荐失败")

    def test_ui_material_recommendation_http_route_projection(self):
        from http.client import HTTPConnection
        from threading import Thread
        import socket
        from urllib.parse import quote

        temp_dir = Path(self.make_temp_dir())
        params_file = temp_dir / "materials.json"
        self._write_recommendation_params_file(params_file)

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        host, port = sock.getsockname()
        sock.close()
        server = laser_web_server.create_server(host, port)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def cleanup_server():
            try:
                server.shutdown()
            finally:
                thread.join(timeout=2)
                server.server_close()

        self.addCleanup(cleanup_server)

        with patch.object(
            laser_web_server.laser_material_calibration_tool,
            "MATERIAL_PARAMS_FILE",
            str(params_file),
        ):
            conn = HTTPConnection(host, port, timeout=5)
            path = (
                "/api/ui/material-recommendation"
                f"?material={quote('纸板')}"
                "&thickness_mm=3"
                "&laser_mode=engrave"
                "&engraving_mode=outline"
            )
            conn.request("GET", path)
            resp = conn.getresponse()
            body = resp.read().decode("utf-8")
            conn.close()

        self.assertEqual(resp.status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["success"])
        result = payload["result"]
        self.assertEqual(
            set(result.keys()),
            {
                "material",
                "thickness_mm",
                "matched_thickness_mm",
                "match",
                "laser_mode",
                "engraving_mode",
                "laser_max_power",
                "feed_rate",
                "passes",
            },
        )
        self.assertEqual(result["material"], "纸板")
        self.assertEqual(result["engraving_mode"], "outline")
        self.assertEqual(result["laser_max_power"], 280)
        self.assertEqual(result["feed_rate"], 450)
        self.assertEqual(result["passes"], 2)
        self.assertNotIn("status", payload)
        for banned in (
            "source_path",
            "defaults",
            "aliases",
            "notes",
            "updated_at",
            "params_file",
            "secret-note",
            "warnings",
            "requested_material",
            "laser_min_power",
            "network_host",
        ):
            self.assertNotIn(banned, body)

    def test_index_hides_recommendation_summary_but_keeps_material_refresh(self):
        index_html = laser_web_server.load_main_index_html()
        # 旧页面遗留控件不再出现；材料推荐与 BroadcastChannel 刷新协议已由 v2 主页面接入。
        self.assertNotIn('id="refreshMaterialBtn"', index_html)
        self.assertNotIn('id="materialRecommendation"', index_html)
        self.assertIn("/api/ui/material-recommendation", index_html)
        self.assertIn("laser-material-library-v1", index_html)
        self.assertIn("materials-updated", index_html)
        self.assertIn("内置材质库的推荐参数", index_html)
        # 发送链路已接入：确认弹窗 + 真实 confirm_send，按钮初始禁用、生成后启用。
        self.assertIn('id="btn-send" disabled', index_html)
        self.assertIn('action: "confirm_send"', index_html)
        self.assertNotIn("发送雕刻链路将在后续版本接入", index_html)

        lab_html = laser_web_server.MATERIAL_LAB_HTML
        self.assertIn("laser-material-library-v1", lab_html)
        self.assertIn("materials-updated", lab_html)
        self.assertIn("notifyMaterialsUpdated", lab_html)

    def test_draw_job_summary_projects_threshold_resize_and_dither(self):
        summary = laser_web_server._draw_job_summary_from_payload(
            {
                "workflow_id": "wf-strategy",
                "status": "preview_ready",
                "summary": {
                    "material": "test",
                    "thickness_mm": 3,
                    "matched_thickness_mm": 3,
                    "material_match_policy": "exact_only",
                    "send_policy": "verified_only",
                    "match_type": "exact",
                    "mode": "raster",
                    "threshold": 100,
                    "resize_strategy": "darkest_region",
                    "dither_algorithm": "threshold",
                    "raster": {
                        "width_mm": 20,
                        "height_mm": 20,
                        "threshold": 100,
                        "resize_strategy": "darkest_region",
                        "dither_algorithm": "threshold",
                    },
                },
            }
        )
        self.assertEqual(summary["threshold"], 100)
        self.assertEqual(summary["resize_strategy"], "darkest_region")
        self.assertEqual(summary["dither_algorithm"], "threshold")
        self.assertEqual(summary["matched_thickness_mm"], 3)
        self.assertEqual(summary["material_match_policy"], "exact_only")
        self.assertEqual(summary["send_policy"], "verified_only")

    def test_draw_job_summary_missing_strategy_fields_are_none(self):
        incomplete = laser_web_server._draw_job_summary_from_payload(
            {
                "workflow_id": "wf-missing",
                "status": "preview_ready",
                "summary": {
                    "material": "test",
                    "mode": "raster",
                    "raster": {"width_mm": 10, "height_mm": 10},
                },
            }
        )
        self.assertIsNone(incomplete.get("threshold"))
        self.assertIsNone(incomplete.get("resize_strategy"))
        self.assertIsNone(incomplete.get("dither_algorithm"))
        # Explicit failure-style assertions used by AC: any missing strategy field fails the check.
        for key in ("threshold", "resize_strategy", "dither_algorithm"):
            self.assertTrue(
                incomplete.get(key) in (None, ""),
                msg=f"expected missing {key} to be empty for incomplete raster summary",
            )


    def test_ui_runtime_config_response_projects_safe_effective_defaults(self):
        from types import SimpleNamespace

        settings = SimpleNamespace(
            default_connection_mode="serial",
            default_serial_port="COM9",
            baudrate=57600,
            network_host="laser.local",
            network_http_port=8080,
            network_telnet_port=2323,
            network_timeout=1.5,
        )

        result = laser_web_server.ui_runtime_config_response(settings=settings)

        self.assertTrue(result["success"], result)
        connection = result["result"]["connection"]
        self.assertEqual(connection["default_mode"], "serial")
        self.assertEqual(connection["preferred_modes"], ["serial", "network"])
        self.assertEqual(connection["serial"], {"default_port": "COM9", "baudrate": 57600})
        self.assertEqual(
            connection["network"],
            {
                "default_host": "laser.local",
                "transport": "telnet",
                "http_port": 8080,
                "telnet_port": 2323,
                "timeout": 1.5,
            },
        )
        self.assertEqual(result["result"]["upload"]["gcode_suffixes"], [".gcode", ".nc"])
        body = json.dumps(result, ensure_ascii=False)
        for banned in ("runtime_dir", "material_params_file", "password", "token", "secret"):
            self.assertNotIn(banned, body)

    def test_ui_serial_ports_response_reuses_existing_listing_and_handles_dependency_error(self):
        from types import SimpleNamespace

        fake_ports = SimpleNamespace(
            comports=lambda: [
                SimpleNamespace(device="COM3", description="USB-SERIAL CH340"),
                SimpleNamespace(device="COM7", description="Laser Controller"),
            ]
        )
        with patch.object(
            laser_web_server.laser_grbl_tool,
            "_load_serial",
            return_value=(object(), fake_ports, None),
        ):
            result = laser_web_server.ui_serial_ports_response()

        self.assertTrue(result["success"], result)
        self.assertEqual(
            result["result"]["ports"],
            [
                {"port": "COM3", "desc": "USB-SERIAL CH340"},
                {"port": "COM7", "desc": "Laser Controller"},
            ],
        )

        with patch.object(
            laser_web_server.laser_grbl_tool,
            "_load_serial",
            return_value=(None, None, "pyserial 未安装"),
        ):
            failed = laser_web_server.ui_serial_ports_response()
        self.assertFalse(failed["success"], failed)
        self.assertIn("pyserial", failed["result"])

    def test_connection_check_prefers_env_mode_then_falls_back_before_send(self):
        calls = []

        def fake_network(**kwargs):
            calls.append(("network", kwargs))
            return False, {"host": kwargs["host"], "error": "网络不可用"}

        def fake_serial(**kwargs):
            calls.append(("serial", kwargs))
            return True, {"port": kwargs["port"], "baudrate": kwargs["baudrate"], "probe_command": "?"}

        with patch.object(laser_web_server.laser_execution, "DEFAULT_CONNECTION_MODE", "network"):
            result = laser_web_server.connection_check_from_payload(
                {
                    "network_host": "laser.local",
                    "network_transport": "telnet",
                    "network_telnet_port": "2323",
                    "network_timeout": "1.5",
                    "port": "COM9",
                    "baudrate": "57600",
                    "include_detail": True,
                },
                checker=fake_network,
                serial_checker=fake_serial,
            )

        self.assertTrue(result["success"], result)
        payload = result["result"]
        self.assertEqual([item[0] for item in calls], ["network", "serial"])
        self.assertEqual(payload["preferred_mode"], "network")
        self.assertEqual(payload["selected_mode"], "serial")
        self.assertTrue(payload["fallback_used"])
        self.assertFalse(payload["network_connected"])
        self.assertTrue(payload["serial_connected"])
        self.assertIn("回退", payload["summary"])
        self.assertEqual(payload["detail"]["serial"]["port"], "COM9")

    def test_connection_check_does_not_probe_backup_after_preferred_success(self):
        calls = []

        def fake_network(**kwargs):
            calls.append("network")
            return True, {"host": kwargs["host"], "probe_command": "?"}

        def fake_serial(**kwargs):
            calls.append("serial")
            return True, {"port": kwargs["port"], "baudrate": kwargs["baudrate"]}

        with patch.object(laser_web_server.laser_execution, "DEFAULT_CONNECTION_MODE", "network"):
            result = laser_web_server.connection_check_from_payload(
                {"network_host": "laser.local", "port": "COM3"},
                checker=fake_network,
                serial_checker=fake_serial,
            )

        self.assertTrue(result["success"], result)
        self.assertEqual(calls, ["network"])
        self.assertEqual(result["result"]["selected_mode"], "network")
        self.assertFalse(result["result"]["fallback_used"])

    def test_send_text_task_accepts_serial_connection_fields(self):
        calls = {}

        def fake_runner(**kwargs):
            calls.update(kwargs)
            return {"success": True, "result": {"workflow_id": "wf-serial", "job_id": "job-serial"}}

        result = laser_web_server.send_text_task_from_payload(
            {
                "workflow_id": "wf-serial",
                "connection_mode": "serial",
                "port": "COM9",
                "baudrate": "57600",
                "confirmed": True,
                "run_in_background": True,
                "wait_for_response": True,
            },
            workflow_runner=fake_runner,
        )

        self.assertTrue(result["success"], result)
        self.assertEqual(calls["connection_mode"], "serial")
        self.assertEqual(calls["port"], "COM9")
        self.assertEqual(calls["baudrate"], 57600)
        self.assertTrue(calls["run_in_background"])
        self.assertTrue(calls["wait_for_response"])
        self.assertNotIn("network_host", calls)

    def test_index_contract_uses_dual_connection_and_real_background_status(self):
        html = laser_web_server.load_main_index_html()
        self.assertIn("/api/ui/runtime-config", html)
        self.assertIn("/api/ui/serial-ports", html)
        self.assertIn("run_in_background", html)
        self.assertIn("job_id", html)
        self.assertNotIn('accept=".gcode,.nc,.txt"', html)
        self.assertNotIn(".gcode / .nc / .txt", html)


    def test_ui_runtime_and_serial_routes_return_json_envelopes(self):
        from http.client import HTTPConnection
        from threading import Thread
        import socket

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        host, port = sock.getsockname()
        sock.close()
        server = laser_web_server.create_server(host, port)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def cleanup_server():
            try:
                server.shutdown()
            finally:
                thread.join(timeout=2)
                server.server_close()

        self.addCleanup(cleanup_server)
        with patch.object(
            laser_web_server,
            "ui_runtime_config_response",
            return_value={"success": True, "result": {"connection": {"default_mode": "serial"}}},
        ), patch.object(
            laser_web_server,
            "ui_serial_ports_response",
            return_value={"success": True, "result": {"ports": []}},
        ):
            conn = HTTPConnection(host, port, timeout=5)
            conn.request("GET", "/api/ui/runtime-config")
            runtime_resp = conn.getresponse()
            runtime_body = json.loads(runtime_resp.read().decode("utf-8"))
            conn.close()

            conn = HTTPConnection(host, port, timeout=5)
            conn.request("GET", "/api/ui/serial-ports")
            serial_resp = conn.getresponse()
            serial_body = json.loads(serial_resp.read().decode("utf-8"))
            conn.close()

        self.assertEqual(runtime_resp.status, 200)
        self.assertEqual(runtime_body, {"success": True, "result": {"connection": {"default_mode": "serial"}}})
        self.assertEqual(serial_resp.status, 200)
        self.assertEqual(serial_body, {"success": True, "result": {"ports": []}})


if __name__ == "__main__":
    unittest.main()
