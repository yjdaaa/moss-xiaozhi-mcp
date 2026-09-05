import base64
import json
import os
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from apps.excalidraw_lab import server as lab_server


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADgwGosIXC9QAAAABJRU5ErkJggg=="
)


class ExcalidrawLaserLabServerTests(unittest.TestCase):
    def make_temp_dir(self):
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".tmp-test"))
        os.makedirs(base_dir, exist_ok=True)
        temp_dir = os.path.join(base_dir, f"case-{uuid.uuid4().hex}")
        os.makedirs(temp_dir, exist_ok=False)
        self.addCleanup(shutil.rmtree, temp_dir, ignore_errors=True)
        return Path(temp_dir)

    def data_url(self, payload=PNG_1X1):
        return "data:image/png;base64," + base64.b64encode(payload).decode("ascii")

    def test_draw_preview_uploads_image_and_routes_image_workflow(self):
        temp_dir = self.make_temp_dir()
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

        with patch.object(lab_server, "LAB_OUTPUT_DIR", temp_dir), patch.object(
            lab_server, "LAB_JOB_OUTPUT_DIR", temp_dir / "jobs"
        ), patch.object(
            lab_server, "LAB_MATERIAL_LIBRARY_FILE", temp_dir / "materials_from_lasergrbl.json"
        ), patch.object(
            lab_server.laser_material_calibration_tool, "MATERIAL_PARAMS_FILE", str(temp_dir / "missing-materials.json")
        ):
            result = lab_server.draw_preview_from_payload(
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
        self.assertEqual(len(fingerprint), 64)
        self.assertRegex(fingerprint, r"^[0-9a-f]{64}$")
        for banned_len in (10, 12, 16):
            self.assertNotEqual(len(fingerprint), banned_len)

    def test_draw_outline_vector_simplify_default_and_bounds(self):
        ok, error = lab_server._workflow_preview_payload(
            {
                "material": "椴木",
                "thickness_mm": "3",
                "mode": "outline",
            },
            Path("draw.png"),
        )
        self.assertIsNone(error)
        self.assertEqual(ok["vector_simplify_factor"], 3.0)
        self.assertTrue(ok["lock_aspect_ratio"])

        for value in (0.25, 8, 8.0):
            payload, err = lab_server._workflow_preview_payload(
                {
                    "material": "椴木",
                    "thickness_mm": "3",
                    "mode": "outline",
                    "vector_simplify_factor": value,
                },
                Path("draw.png"),
            )
            self.assertIsNone(err, value)
            self.assertEqual(payload["vector_simplify_factor"], float(value))

        for bad in (0.2, 0.249, 8.01, "nan", "inf", " ", "abc"):
            payload, err = lab_server._workflow_preview_payload(
                {
                    "material": "椴木",
                    "thickness_mm": "3",
                    "mode": "outline",
                    "vector_simplify_factor": bad,
                },
                Path("draw.png"),
            )
            self.assertIsNone(payload, bad)
            self.assertIn("vector_simplify_factor", err)

        raster, err = lab_server._workflow_preview_payload(
            {
                "material": "椴木",
                "thickness_mm": "3",
                "mode": "raster",
                "vector_simplify_factor": 99,
            },
            Path("draw.png"),
        )
        self.assertIsNone(err)
        self.assertNotIn("vector_simplify_factor", raster)

    def test_draw_preview_routes_line_art_raster_options(self):
        payload, error = lab_server._workflow_preview_payload(
            {
                "material": "椴木",
                "thickness_mm": "3",
                "mode": "raster",
                "pixel_size_mm": "0.1",
                "dither_algorithm": "threshold",
                "raster_scan_direction": "horizontal",
                "raster_output_strategy": "scanline",
                "raster_quality_strategy": "manual",
            },
            Path("draw.png"),
        )

        self.assertIsNone(error)
        self.assertEqual(payload["pixel_size_mm"], 0.1)
        self.assertEqual(payload["dither_algorithm"], "threshold")
        self.assertEqual(payload["raster_scan_direction"], "horizontal")
        self.assertEqual(payload["raster_output_strategy"], "scanline")
        self.assertEqual(payload["raster_quality_strategy"], "manual")
        self.assertNotIn("material_match_policy", payload)
        self.assertNotIn("send_policy", payload)

    def test_draw_preview_default_runner_is_trusted_draw_entry(self):
        temp_dir = self.make_temp_dir()
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

        with patch.object(lab_server, "LAB_OUTPUT_DIR", temp_dir), patch.object(
            lab_server, "LAB_JOB_OUTPUT_DIR", temp_dir / "jobs"
        ), patch.object(
            lab_server, "LAB_MATERIAL_LIBRARY_FILE", temp_dir / "materials_from_lasergrbl.json"
        ), patch.object(
            lab_server.laser_material_calibration_tool, "MATERIAL_PARAMS_FILE", str(temp_dir / "missing-materials.json")
        ), patch.object(
            lab_server.laser_workflow_tool, "preview_draw_lab_image", side_effect=fake_trusted_runner
        ) as trusted:
            result = lab_server.draw_preview_from_payload(
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

    def test_draw_preview_routes_optional_width_and_height(self):
        temp_dir = self.make_temp_dir()
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

        with patch.object(lab_server, "LAB_OUTPUT_DIR", temp_dir), patch.object(
            lab_server, "LAB_JOB_OUTPUT_DIR", temp_dir / "jobs"
        ), patch.object(
            lab_server, "LAB_MATERIAL_LIBRARY_FILE", temp_dir / "materials_from_lasergrbl.json"
        ), patch.object(
            lab_server.laser_material_calibration_tool, "MATERIAL_PARAMS_FILE", str(temp_dir / "missing-materials.json")
        ):
            result = lab_server.draw_preview_from_payload(
                {
                    "image_data_url": self.data_url(),
                    "material": "椴木",
                    "thickness_mm": "3",
                    "mode": "raster",
                    "width_mm": "40",
                    "height_mm": "20",
                },
                workflow_runner=fake_workflow_runner,
            )

        self.assertTrue(result["success"], result)
        self.assertEqual(calls["width_mm"], 40.0)
        self.assertEqual(calls["height_mm"], 20.0)
        self.assertNotIn("size_mm", calls)

    def test_connection_check_routes_to_existing_network_probe_only(self):
        with patch.object(
            lab_server.check_laser_connection_tool,
            "_check_network_connection",
            return_value=(True, {"host": "laser.local", "probe_command": "?"}),
        ) as checker:
            result = lab_server.connection_check_from_payload(
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

    def test_lab_links_include_frontend_job_summary(self):
        result = lab_server._add_lab_links(
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
        self.assertEqual(summary["thickness_mm"], 3)
        self.assertEqual(summary["mode"], "raster")
        self.assertEqual(summary["task_type"], "engrave_photo")
        self.assertEqual(summary["actual_width_mm"], 34.5)
        self.assertEqual(summary["actual_height_mm"], 18.2)
        self.assertEqual(summary["power"], 380)
        self.assertEqual(summary["speed"], 1500)
        self.assertEqual(summary["passes"], 1)
        self.assertEqual(summary["pixel_size_mm"], 0.05)
        self.assertEqual(summary["estimated_seconds"], 94)
        self.assertEqual(summary["estimated_display"], "1 分 34 秒")
        self.assertTrue(summary["can_send"])
        self.assertTrue(summary["scaled"])
        self.assertEqual(summary["auto_adjustment"]["pixel_size_mm"], 0.2)
        self.assertIn("自动", summary["size_note"])
        self.assertIn("缩小", summary["size_note"])
        self.assertIn("请先做小样测试", summary["warnings"])
        self.assertIn("点距调整到 0.2 mm", "；".join(summary["warnings"]))

    def test_draw_preview_rejects_non_image_data_url_before_workflow(self):
        def fake_workflow_runner(**kwargs):
            self.fail("workflow must not be called for invalid upload")

        result = lab_server.draw_preview_from_payload(
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
        temp_dir = self.make_temp_dir()

        with patch.object(lab_server, "LAB_OUTPUT_DIR", temp_dir):
            result = lab_server.draw_preview_from_payload(
                {
                    "image_data_url": self.data_url(),
                    "material": "",
                    "thickness_mm": "0",
                }
            )

        self.assertFalse(result["success"])
        self.assertEqual(list(temp_dir.glob("*")), [])

    def test_converts_lasergrbl_material_library_for_image_workflow(self):
        temp_dir = self.make_temp_dir()
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

        path, error = lab_server._write_converted_material_library(params_file=params_file, output_file=converted_file)

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


if __name__ == "__main__":
    unittest.main()
