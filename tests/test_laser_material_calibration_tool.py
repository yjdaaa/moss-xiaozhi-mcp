import json
import os
import shutil
import unittest
import uuid
from unittest.mock import patch

from core.laser_runtime.models import file_sha256
from tools import laser_material_calibration_tool


class FakeMcp:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorator(func):
            self.tools[func.__name__] = func
            return func

        return decorator


class LaserCalibrationToolTests(unittest.TestCase):
    def make_temp_dir(self):
        base_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", ".tmp-test")
        )
        os.makedirs(base_dir, exist_ok=True)

        temp_dir = os.path.join(base_dir, f"case-{uuid.uuid4().hex}")
        os.makedirs(temp_dir, exist_ok=False)
        self.addCleanup(shutil.rmtree, temp_dir, ignore_errors=True)
        return temp_dir

    def test_register_tool_exposes_calibration_actions(self):
        fake = FakeMcp()

        laser_material_calibration_tool.register_tool(fake)

        self.assertIn("material_params_tool", fake.tools)
        self.assertIn("recommend_laser_params_tool", fake.tools)
        self.assertIn("run_calibration_grid_tool", fake.tools)
        self.assertIn("select_calibration_cell_tool", fake.tools)
        self.assertIn("start_tuned_job_tool", fake.tools)

    def test_recommend_cut_requires_exact_thickness_even_for_alias(self):
        result = laser_material_calibration_tool.recommend_laser_params(
            material="basswood",
            thickness_mm=4,
            laser_mode="cut",
            params_file=os.path.join(self.make_temp_dir(), "missing.json"),
        )

        self.assertFalse(result["success"], result)
        self.assertIn("切割", result["result"])
        self.assertIn("4mm", result["result"])
        self.assertIn("精确", result["result"])

    def test_basswood_engraving_uses_3mm_params_for_missing_4mm_and_5mm(self):
        params_file = os.path.join(self.make_temp_dir(), "missing.json")
        expected_params = {
            "laser_min_power": 0,
            "laser_max_power": 420,
            "feed_rate": 1800,
            "travel_rate": 3000,
            "pixel_size_mm": 0.1,
            "threshold": -1,
            "passes": 1,
        }

        for engraving_mode in ["raster", "outline"]:
            with self.subTest(engraving_mode=engraving_mode, thickness_mm=3):
                exact = laser_material_calibration_tool.recommend_laser_params(
                    "椴木", 3, "engrave", engraving_mode, params_file=params_file
                )

                self.assertTrue(exact["success"], exact)
                self.assertEqual(exact["result"]["match"], "exact")
                self.assertEqual(exact["result"]["matched_thickness_mm"], 3.0)
                self.assertEqual(exact["result"]["engraving_mode"], engraving_mode)
                self.assertEqual(exact["result"]["params"], expected_params)

            for thickness_mm in [4, 5]:
                with self.subTest(engraving_mode=engraving_mode, thickness_mm=thickness_mm):
                    fallback = laser_material_calibration_tool.recommend_laser_params(
                        "椴木",
                        thickness_mm,
                        "engrave",
                        engraving_mode,
                        params_file=params_file,
                    )

                    self.assertTrue(fallback["success"], fallback)
                    self.assertEqual(fallback["result"]["match"], "nearest_thickness")
                    self.assertEqual(fallback["result"]["matched_thickness_mm"], 3.0)
                    self.assertEqual(fallback["result"]["engraving_mode"], engraving_mode)
                    self.assertEqual(fallback["result"]["params"], expected_params)

    def test_recommend_maps_generic_wood_words_to_basswood(self):
        for material in ["木头", "木料", "木牌", "木片", "木质"]:
            result = laser_material_calibration_tool.recommend_laser_params(
                material=material,
                thickness_mm=3,
                laser_mode="engrave",
                engraving_mode="raster",
                params_file=os.path.join(self.make_temp_dir(), "missing.json"),
            )

            self.assertTrue(result["success"], result)
            self.assertEqual(result["result"]["material"], "椴木")
            self.assertEqual(result["result"]["requested_material"], material)
            self.assertEqual(result["result"]["engraving_mode"], "raster")

    def test_recommend_separates_raster_and_outline_under_same_material_alias(self):
        params_file = os.path.join(self.make_temp_dir(), "materials.json")

        raster = laser_material_calibration_tool.material_params(
            action="save",
            material="测试木材",
            aliases_json='["测试木头"]',
            thickness_mm=3,
            laser_mode="engrave",
            engraving_mode="raster",
            laser_max_power=300,
            feed_rate=1800,
            params_file=params_file,
        )
        outline = laser_material_calibration_tool.material_params(
            action="save",
            material="测试木材",
            thickness_mm=3,
            laser_mode="engrave",
            engraving_mode="outline",
            laser_max_power=520,
            feed_rate=900,
            params_file=params_file,
        )

        self.assertTrue(raster["success"], raster)
        self.assertTrue(outline["success"], outline)

        raster_rec = laser_material_calibration_tool.recommend_laser_params(
            "测试木头", 3, "engrave", "raster", params_file=params_file
        )
        outline_rec = laser_material_calibration_tool.recommend_laser_params(
            "测试木头", 3, "engrave", "outline", params_file=params_file
        )

        self.assertTrue(raster_rec["success"], raster_rec)
        self.assertTrue(outline_rec["success"], outline_rec)
        self.assertEqual(raster_rec["result"]["material"], "测试木材")
        self.assertEqual(outline_rec["result"]["material"], "测试木材")
        self.assertEqual(raster_rec["result"]["engraving_mode"], "raster")
        self.assertEqual(outline_rec["result"]["engraving_mode"], "outline")
        self.assertEqual(raster_rec["result"]["params"]["laser_max_power"], 300)
        self.assertEqual(outline_rec["result"]["params"]["laser_max_power"], 520)

    def test_delete_default_cut_params_stays_deleted_after_reload(self):
        params_file = os.path.join(self.make_temp_dir(), "materials.json")

        deleted = laser_material_calibration_tool.material_params(
            action="delete",
            material="椴木",
            thickness_mm=3,
            laser_mode="cut",
            params_file=params_file,
        )
        reloaded = laser_material_calibration_tool.recommend_laser_params(
            "椴木", 3, "cut", params_file=params_file
        )

        self.assertTrue(deleted["success"], deleted)
        self.assertFalse(reloaded["success"], reloaded)
        self.assertIn("暂无 3mm 精确切割参数", reloaded["result"])

    def test_outline_does_not_fall_back_to_raster_params(self):
        params_file = os.path.join(self.make_temp_dir(), "materials.json")

        save_result = laser_material_calibration_tool.material_params(
            action="save",
            material="只存光栅材料",
            thickness_mm=2,
            laser_mode="engrave",
            engraving_mode="raster",
            laser_max_power=333,
            feed_rate=1234,
            params_file=params_file,
        )
        self.assertTrue(save_result["success"], save_result)

        result = laser_material_calibration_tool.recommend_laser_params(
            "只存光栅材料", 2, "engrave", "outline", params_file=params_file
        )

        self.assertFalse(result["success"], result)
        self.assertIn("engrave/outline", result["result"])

    def test_material_params_save_and_get_custom_material(self):
        params_file = os.path.join(self.make_temp_dir(), "materials.json")

        save_result = laser_material_calibration_tool.material_params(
            action="save",
            material="玻璃测试片",
            thickness_mm=2,
            laser_mode="engrave",
            engraving_mode="outline",
            laser_max_power=123,
            feed_rate=456,
            travel_rate=3000,
            pixel_size_mm=0.2,
            threshold=100,
            passes=3,
            params_file=params_file,
        )
        self.assertTrue(save_result["success"], save_result)

        get_result = laser_material_calibration_tool.recommend_laser_params(
            "玻璃测试片", 2, "engrave", "outline", params_file=params_file
        )

        self.assertTrue(get_result["success"], get_result)
        params = get_result["result"]["params"]
        self.assertEqual(get_result["result"]["engraving_mode"], "outline")
        self.assertEqual(params["laser_max_power"], 123)
        self.assertEqual(params["feed_rate"], 456)
        self.assertEqual(params["passes"], 3)

    def test_material_params_new_version_preserves_previous_operation_and_evidence(self):
        params_file = os.path.join(self.make_temp_dir(), "materials.json")

        first = laser_material_calibration_tool.material_params(
            action="save",
            material="版本测试木材",
            thickness_mm=3,
            laser_mode="engrave",
            engraving_mode="raster",
            laser_max_power=300,
            feed_rate=1200,
            machine_profile_id="machine-a",
            confidence="experimental",
            evidence_json=json.dumps({"material_brand": "品牌 A", "tested_at": "2026-09-01"}),
            params_file=params_file,
        )
        second = laser_material_calibration_tool.material_params(
            action="save",
            material="版本测试木材",
            thickness_mm=3,
            laser_mode="engrave",
            engraving_mode="raster",
            laser_max_power=360,
            feed_rate=1000,
            machine_profile_id="machine-a",
            confidence="verified",
            evidence_json=json.dumps({"verified_by_user": True}),
            version_mode="new_version",
            params_file=params_file,
        )

        self.assertTrue(first["success"], first)
        self.assertTrue(second["success"], second)
        self.assertEqual(second["result"]["revision"], 2)
        recommendation = laser_material_calibration_tool.recommend_laser_params(
            "版本测试木材",
            3,
            "engrave",
            "raster",
            params_file=params_file,
        )
        self.assertTrue(recommendation["success"], recommendation)
        result = recommendation["result"]
        self.assertEqual(result["params"]["laser_max_power"], 360)
        self.assertEqual(result["revision"], 2)
        self.assertEqual(result["confidence"], "verified")
        self.assertEqual(result["evidence"]["material_brand"], "品牌 A")
        self.assertTrue(result["evidence"]["verified_by_user"])
        self.assertEqual(result["history_count"], 1)

    def test_material_params_export_and_import_merge(self):
        source_file = os.path.join(self.make_temp_dir(), "source.json")
        target_file = os.path.join(self.make_temp_dir(), "target.json")
        saved = laser_material_calibration_tool.material_params(
            action="save",
            material="导出木材",
            thickness_mm=3,
            laser_mode="engrave",
            engraving_mode="raster",
            laser_max_power=333,
            feed_rate=1111,
            params_file=source_file,
        )
        exported = laser_material_calibration_tool.material_params(
            action="export",
            params_file=source_file,
        )
        imported = laser_material_calibration_tool.material_params(
            action="import",
            library_json=json.dumps(exported["result"]["library"], ensure_ascii=False),
            import_mode="merge",
            params_file=target_file,
        )

        self.assertTrue(saved["success"], saved)
        self.assertTrue(exported["success"], exported)
        self.assertTrue(imported["success"], imported)
        recommendation = laser_material_calibration_tool.recommend_laser_params(
            "导出木材", 3, "engrave", "raster", params_file=target_file
        )
        self.assertTrue(recommendation["success"], recommendation)
        self.assertEqual(recommendation["result"]["params"]["laser_max_power"], 333)

    def test_run_calibration_grid_generates_file_without_confirmation(self):
        temp_dir = self.make_temp_dir()
        output_file = os.path.join(temp_dir, "grid.gcode")

        with (
            patch.object(laser_material_calibration_tool, "CALIBRATION_DIR", temp_dir),
            patch.object(laser_material_calibration_tool.laser_execution, "send_file") as send_mock,
            patch.object(laser_material_calibration_tool.laser_grbl_tool, "_load_serial") as load_serial_mock,
            patch.object(laser_material_calibration_tool.laser_network_grbl_tool, "query_telnet_command") as telnet_probe_mock,
        ):
            result = laser_material_calibration_tool.run_calibration_grid(
                material="椴木",
                thickness_mm=3,
                laser_mode="engrave",
                rows=5,
                columns=5,
                power_min=100,
                power_max=500,
                speed_min=1000,
                speed_max=2000,
                output_file=output_file,
                confirmed=False,
            )

        self.assertTrue(result["success"], result)
        payload = result["result"]
        self.assertTrue(payload["confirmation_required"])
        self.assertEqual(payload["matrix_kind"], "engrave")
        self.assertEqual(payload["stats"]["matrix_kind"], "engrave")
        self.assertEqual(payload["stats"]["cell_count"], 25)
        self.assertEqual(payload["cells"][7]["cell_number"], 8)
        self.assertEqual(payload["cells"][7]["row"], 2)
        self.assertEqual(payload["cells"][7]["column"], 3)
        self.assertTrue(os.path.isfile(output_file))
        send_mock.assert_not_called()

        with open(output_file, "r", encoding="utf-8") as file:
            content = file.read()
        self.assertIn("Cell 1", content)
        self.assertIn("Cell 1 fill pass 1", content)
        self.assertIn("S100", content)
        self.assertIn("S500", content)
        self.assertIn("G1 X5 Y0", content)
        self.assertIn("G1 X0 Y0.8", content)
        self.assertIn("G1 X5 Y1.6", content)
        self.assertNotIn("G1 X5 Y0 S100", content)
        load_serial_mock.assert_not_called()
        telnet_probe_mock.assert_not_called()

    def test_run_calibration_grid_cut_matrix_uses_speed_columns_and_pass_rows(self):
        temp_dir = self.make_temp_dir()
        output_file = os.path.join(temp_dir, "cut-grid.gcode")

        with patch.object(laser_material_calibration_tool, "CALIBRATION_DIR", temp_dir):
            result = laser_material_calibration_tool.run_calibration_grid(
                material="椴木",
                thickness_mm=3,
                laser_mode="cut",
                matrix_kind="cut",
                rows=4,
                columns=5,
                power_min=900,
                power_max=900,
                speed_min=120,
                speed_max=360,
                pass_min=1,
                pass_max=4,
                output_file=output_file,
                confirmed=False,
            )

        self.assertTrue(result["success"], result)
        payload = result["result"]
        self.assertEqual(payload["matrix_kind"], "cut")
        self.assertEqual(payload["stats"]["matrix_kind"], "cut")
        self.assertEqual(payload["stats"]["cell_count"], 20)
        self.assertEqual(payload["cells"][0]["laser_max_power"], 900)
        self.assertEqual(payload["cells"][0]["feed_rate"], 120)
        self.assertEqual(payload["cells"][0]["passes"], 1)
        self.assertEqual(payload["cells"][-1]["laser_max_power"], 900)
        self.assertEqual(payload["cells"][-1]["feed_rate"], 360)
        self.assertEqual(payload["cells"][-1]["passes"], 4)

        with open(output_file, "r", encoding="utf-8") as file:
            content = file.read()
        self.assertIn("; Matrix kind: cut", content)
        self.assertIn("; Cell 16 row 4 col 1 S900 F120 passes 4", content)
        self.assertIn("; Cell 16 pass 4", content)
        self.assertIn("G1 X5 Y21", content)

    def test_run_calibration_grid_rejects_matrix_outside_yixiu_work_area(self):
        temp_dir = self.make_temp_dir()
        output_file = os.path.join(temp_dir, "oversized-grid.gcode")

        with patch.object(laser_material_calibration_tool, "CALIBRATION_DIR", temp_dir):
            result = laser_material_calibration_tool.run_calibration_grid(
                material="椴木",
                thickness_mm=3,
                laser_mode="engrave",
                rows=10,
                columns=10,
                cell_size_mm=11,
                gap_mm=0,
                output_file=output_file,
                confirmed=False,
            )

        self.assertFalse(result["success"], result)
        self.assertIn("行程", result["result"])
        self.assertFalse(os.path.exists(output_file))

    def test_run_calibration_grid_confirmed_starts_background_job(self):
        temp_dir = self.make_temp_dir()
        output_file = os.path.join(temp_dir, "grid.gcode")

        with (
            patch.object(laser_material_calibration_tool, "CALIBRATION_DIR", temp_dir),
            patch.object(
                laser_material_calibration_tool.laser_execution,
                "send_file",
                return_value={"success": True, "job_id": "job-1"},
            ) as send_mock,
        ):
            result = laser_material_calibration_tool.run_calibration_grid(
                material="椴木",
                thickness_mm=3,
                laser_mode="engrave",
                rows=2,
                columns=2,
                output_file=output_file,
                confirmed=True,
                connection_mode="serial",
            )

        self.assertTrue(result["success"], result)
        self.assertFalse(result["result"]["confirmation_required"])
        self.assertEqual(result["result"]["send_result"]["job_id"], "job-1")
        send_mock.assert_called_once()
        self.assertEqual(
            send_mock.call_args.args[0]["expected_gcode_sha256"],
            file_sha256(output_file),
        )

    def test_run_calibration_grid_failed_send_preflight_does_not_report_job_started(self):
        temp_dir = self.make_temp_dir()
        output_file = os.path.join(temp_dir, "grid.gcode")

        with (
            patch.object(laser_material_calibration_tool, "CALIBRATION_DIR", temp_dir),
            patch.object(
                laser_material_calibration_tool.laser_execution,
                "send_file",
                return_value={"success": False, "result": "设备在线检查失败"},
            ) as send_mock,
        ):
            result = laser_material_calibration_tool.run_calibration_grid(
                material="椴木",
                thickness_mm=3,
                laser_mode="engrave",
                rows=2,
                columns=2,
                output_file=output_file,
                confirmed=True,
                connection_mode="serial",
            )

        self.assertTrue(result["success"], result)
        self.assertFalse(result["result"]["send_result"]["success"])
        self.assertIn("设备在线检查失败", result["result"]["send_result"]["result"])
        self.assertFalse(result["result"]["confirmation_required"])
        send_mock.assert_called_once()

    def test_run_calibration_grid_default_mode_uses_network_host_from_env(self):
        temp_dir = self.make_temp_dir()
        output_file = os.path.join(temp_dir, "grid.gcode")

        with (
            patch.object(laser_material_calibration_tool, "CALIBRATION_DIR", temp_dir),
            patch.object(laser_material_calibration_tool.laser_execution, "DEFAULT_CONNECTION_MODE", "network"),
            patch.object(
                laser_material_calibration_tool.laser_execution,
                "resolve_laser_network_host",
                return_value=("laser.local", None),
            ),
            patch.object(
                laser_material_calibration_tool.laser_execution,
                "send_file",
                return_value={"success": True, "job_id": "env-net-grid"},
            ) as network_send_mock,
        ):
            result = laser_material_calibration_tool.run_calibration_grid(
                material="椴木",
                thickness_mm=3,
                laser_mode="engrave",
                rows=2,
                columns=2,
                output_file=output_file,
                confirmed=True,
            )

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"]["connection_mode"], "network")
        self.assertEqual(result["result"]["send_result"]["job_id"], "env-net-grid")
        network_send_mock.assert_called_once()
        self.assertEqual(network_send_mock.call_args.args[1], "network")
        self.assertEqual(network_send_mock.call_args.kwargs.get("host"), "laser.local")

    def test_run_calibration_grid_saves_resolved_network_host_in_session(self):
        temp_dir = self.make_temp_dir()
        output_file = os.path.join(temp_dir, "grid.gcode")

        with (
            patch.object(laser_material_calibration_tool, "CALIBRATION_DIR", temp_dir),
            patch.object(laser_material_calibration_tool.laser_execution, "DEFAULT_CONNECTION_MODE", "network"),
            patch.object(
                laser_material_calibration_tool.laser_execution,
                "resolve_laser_network_host",
                return_value=("laser.local", None),
            ),
        ):
            result = laser_material_calibration_tool.run_calibration_grid(
                material="椴木",
                thickness_mm=3,
                rows=2,
                columns=2,
                output_file=output_file,
                confirmed=False,
            )
            session, error = laser_material_calibration_tool._read_calibration_session(
                result["result"]["calibration_id"]
            )

        self.assertTrue(result["success"], result)
        payload = result["result"]
        self.assertEqual(payload["network_host"], "laser.local")
        self.assertIsNone(error)
        self.assertEqual(session["connection"]["network_host"], "laser.local")

    def test_run_calibration_grid_confirmed_can_resume_by_calibration_id(self):
        temp_dir = self.make_temp_dir()
        output_file = os.path.join(temp_dir, "grid.gcode")

        with (
            patch.object(laser_material_calibration_tool, "CALIBRATION_DIR", temp_dir),
            patch.object(laser_material_calibration_tool.laser_execution, "DEFAULT_CONNECTION_MODE", "network"),
            patch.object(
                laser_material_calibration_tool.laser_execution,
                "resolve_laser_network_host",
                return_value=("laser.local", None),
            ),
        ):
            prepared = laser_material_calibration_tool.run_calibration_grid(
                material="椴木",
                thickness_mm=3,
                rows=2,
                columns=2,
                output_file=output_file,
                confirmed=False,
            )

            with patch.object(
                laser_material_calibration_tool.laser_execution,
                "send_file",
                return_value={"success": True, "job_id": "resumed-grid"},
            ) as network_send_mock:
                result = laser_material_calibration_tool.run_calibration_grid(
                    calibration_id=prepared["result"]["calibration_id"],
                    confirmed=True,
                )

        self.assertTrue(result["success"], result)
        self.assertFalse(result["result"]["confirmation_required"])
        self.assertEqual(result["result"]["material"], "椴木")
        self.assertEqual(
            network_send_mock.call_args.args[0]["expected_gcode_sha256"],
            file_sha256(output_file),
        )
        self.assertEqual(result["result"]["thickness_mm"], 3)
        self.assertEqual(result["result"]["send_result"]["job_id"], "resumed-grid")
        network_send_mock.assert_called_once()
        self.assertEqual(network_send_mock.call_args.args[1], "network")
        self.assertEqual(network_send_mock.call_args.kwargs.get("host"), "laser.local")

    def test_run_calibration_grid_resume_rejects_replaced_file_before_sender(self):
        temp_dir = self.make_temp_dir()
        output_file = os.path.join(temp_dir, "grid.gcode")

        with patch.object(laser_material_calibration_tool, "CALIBRATION_DIR", temp_dir):
            prepared = laser_material_calibration_tool.run_calibration_grid(
                material="椴木",
                thickness_mm=3,
                rows=2,
                columns=2,
                output_file=output_file,
                confirmed=False,
                connection_mode="serial",
            )
            with open(output_file, "a", encoding="utf-8") as file:
                file.write("M3 S1000\n")
            with patch.object(
                laser_material_calibration_tool.laser_execution,
                "send_file",
            ) as send_mock:
                result = laser_material_calibration_tool.run_calibration_grid(
                    calibration_id=prepared["result"]["calibration_id"],
                    confirmed=True,
                    connection_mode="serial",
                )

        self.assertFalse(result["success"], result)
        self.assertEqual(result.get("error_code"), "preview_content_mismatch")
        send_mock.assert_not_called()

    def test_invalid_default_connection_mode_rejects_before_generating_grid(self):
        temp_dir = self.make_temp_dir()
        output_file = os.path.join(temp_dir, "grid.gcode")

        with (
            patch.object(laser_material_calibration_tool, "CALIBRATION_DIR", temp_dir),
            patch.object(laser_material_calibration_tool.laser_execution, "DEFAULT_CONNECTION_MODE", "netwrok"),
        ):
            result = laser_material_calibration_tool.run_calibration_grid(
                material="椴木",
                thickness_mm=3,
                output_file=output_file,
                confirmed=True,
            )

        self.assertFalse(result["success"], result)
        self.assertIn("LASER_DEFAULT_CONNECTION_MODE", result["result"])
        self.assertFalse(os.path.exists(output_file))

    def test_select_calibration_cell_saves_selected_params(self):
        temp_dir = self.make_temp_dir()
        params_file = os.path.join(temp_dir, "materials.json")

        with patch.object(laser_material_calibration_tool, "CALIBRATION_DIR", temp_dir):
            grid = laser_material_calibration_tool.run_calibration_grid(
                material="椴木",
                thickness_mm=3,
                laser_mode="cut",
                rows=2,
                columns=2,
                power_min=100,
                power_max=400,
                speed_min=300,
                speed_max=600,
                confirmed=False,
                params_file=params_file,
            )
            self.assertTrue(grid["success"], grid)
            calibration_id = grid["result"]["calibration_id"]

            selected = laser_material_calibration_tool.select_calibration_cell(
                calibration_id=calibration_id,
                cell_number=3,
                params_file=params_file,
            )

        self.assertTrue(selected["success"], selected)
        saved_params = selected["result"]["params"]
        self.assertEqual(saved_params["laser_max_power"], 400)
        self.assertEqual(saved_params["feed_rate"], 300)
        self.assertEqual(saved_params["passes"], 2)
        self.assertEqual(saved_params["source"], "calibration")

        recommended = laser_material_calibration_tool.recommend_laser_params(
            "椴木", 3, "cut", params_file=params_file
        )
        self.assertTrue(recommended["success"], recommended)
        self.assertEqual(recommended["result"]["params"]["laser_max_power"], 400)
        self.assertEqual(recommended["result"]["params"]["feed_rate"], 300)
        self.assertEqual(recommended["result"]["params"]["passes"], 2)

    def test_start_tuned_job_requires_confirmation_before_prepare(self):
        with patch.object(
            laser_material_calibration_tool.laser_grbl_tool,
            "prepare_gcode_file_for_sending",
        ) as prepare_mock, patch.object(
            laser_material_calibration_tool.laser_execution,
            "send_file",
        ) as send_mock:
            result = laser_material_calibration_tool.start_tuned_job(
                material="椴木",
                thickness_mm=3,
                laser_mode="engrave",
                confirmed=False,
            )

        self.assertTrue(result["success"], result)
        self.assertTrue(result["result"]["confirmation_required"])
        prepare_mock.assert_not_called()
        send_mock.assert_not_called()

    def test_start_tuned_job_confirmed_prepares_and_sends(self):
        temp_dir = self.make_temp_dir()
        gcode_file = os.path.join(temp_dir, "job.gcode")
        with open(gcode_file, "w", encoding="utf-8") as file:
            file.write("G21\n")

        prepared = {
            "source_file": gcode_file,
            "gcode_file": gcode_file,
            "converted": False,
        }
        with (
            patch.object(
                laser_material_calibration_tool.laser_grbl_tool,
                "prepare_gcode_file_for_sending",
                return_value={"success": True, "result": prepared},
            ) as prepare_mock,
            patch.object(
                laser_material_calibration_tool.laser_execution,
                "send_file",
                return_value={"success": True, "job_id": "job-2"},
            ) as send_mock,
        ):
            result = laser_material_calibration_tool.start_tuned_job(
                material="椴木",
                thickness_mm=3,
                laser_mode="engrave",
                gcode_file=gcode_file,
                confirmed=True,
                connection_mode="serial",
            )

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"]["send_result"]["job_id"], "job-2")
        prepare_mock.assert_called_once()
        send_mock.assert_called_once()

    def test_start_tuned_job_failed_send_preflight_preserves_blocked_send_result(self):
        temp_dir = self.make_temp_dir()
        gcode_file = os.path.join(temp_dir, "job.gcode")
        with open(gcode_file, "w", encoding="utf-8") as file:
            file.write("G21\n")

        prepared = {
            "source_file": gcode_file,
            "gcode_file": gcode_file,
            "converted": False,
        }
        with (
            patch.object(
                laser_material_calibration_tool.laser_grbl_tool,
                "prepare_gcode_file_for_sending",
                return_value={"success": True, "result": prepared},
            ),
            patch.object(
                laser_material_calibration_tool.laser_execution,
                "send_file",
                return_value={"success": False, "result": "设备在线检查失败"},
            ) as send_mock,
        ):
            result = laser_material_calibration_tool.start_tuned_job(
                material="椴木",
                thickness_mm=3,
                laser_mode="engrave",
                gcode_file=gcode_file,
                confirmed=True,
                connection_mode="serial",
            )

        self.assertTrue(result["success"], result)
        self.assertFalse(result["result"]["send_result"]["success"])
        self.assertIn("设备在线检查失败", result["result"]["send_result"]["result"])
        self.assertFalse(result["result"]["confirmation_required"])
        send_mock.assert_called_once()

    def test_start_tuned_job_passes_image_import_options_to_preparer(self):
        temp_dir = self.make_temp_dir()
        image_file = os.path.join(temp_dir, "source.png")
        gcode_file = os.path.join(temp_dir, "job.gcode")
        with open(image_file, "wb") as file:
            file.write(b"mock image")
        with open(gcode_file, "w", encoding="utf-8", newline="\n") as file:
            file.write("G21\nG90\nM5\n")

        prepared = {
            "source_file": image_file,
            "gcode_file": gcode_file,
            "converted": True,
        }
        with (
            patch.object(
                laser_material_calibration_tool.laser_grbl_tool,
                "prepare_gcode_file_for_sending",
                return_value={"success": True, "result": prepared},
            ) as prepare_mock,
            patch.object(
                laser_material_calibration_tool.laser_execution,
                "send_file",
                return_value={"success": True, "job_id": "job-image"},
            ),
        ):
            result = laser_material_calibration_tool.start_tuned_job(
                material="椴木",
                thickness_mm=3,
                laser_mode="engrave",
                image_file=image_file,
                auto_trim=False,
                dpi=200,
                offset_x_mm=2,
                offset_y_mm=3,
                safe_margin_mm=6,
                confirmed=True,
                connection_mode="serial",
            )

        self.assertTrue(result["success"], result)
        self.assertFalse(prepare_mock.call_args.kwargs["auto_trim"])
        self.assertEqual(prepare_mock.call_args.kwargs["dpi"], 200)
        self.assertEqual(prepare_mock.call_args.kwargs["offset_x_mm"], 2)
        self.assertEqual(prepare_mock.call_args.kwargs["offset_y_mm"], 3)
        self.assertEqual(prepare_mock.call_args.kwargs["safe_margin_mm"], 6)

    def test_start_tuned_job_resolves_text_task_id_to_latest_gcode(self):
        temp_dir = self.make_temp_dir()
        task_id = "task-123"
        task_dir = os.path.join(temp_dir, task_id)
        os.makedirs(task_dir)
        old_gcode = os.path.join(task_dir, "attempt_1.gcode")
        latest_gcode = os.path.join(task_dir, "attempt_2.gcode")
        with open(old_gcode, "w", encoding="utf-8") as file:
            file.write("G21\n")
        with open(latest_gcode, "w", encoding="utf-8") as file:
            file.write("G21\n")
        with open(os.path.join(task_dir, "task.json"), "w", encoding="utf-8") as file:
            json.dump(
                {
                    "task_id": task_id,
                    "material": "椴木",
                    "requested_material": "木板",
                    "thickness_mm": 3,
                    "laser_mode": "engrave",
                    "attempts": [
                        {"attempt_no": 1, "gcode_file": old_gcode},
                        {"attempt_no": 2, "gcode_file": latest_gcode},
                    ],
                },
                file,
                ensure_ascii=False,
            )

        prepared = {
            "source_file": latest_gcode,
            "gcode_file": latest_gcode,
            "converted": False,
        }
        with (
            patch.object(
                laser_material_calibration_tool.laser_grbl_tool,
                "prepare_gcode_file_for_sending",
                return_value={"success": True, "result": prepared},
            ) as prepare_mock,
            patch.object(
                laser_material_calibration_tool.laser_execution,
                "send_file",
                return_value={"success": True, "job_id": "job-task"},
            ) as send_mock,
        ):
            result = laser_material_calibration_tool.start_tuned_job(
                task_id=task_id,
                confirmed=True,
                connection_mode="serial",
                tasks_dir=temp_dir,
            )

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"]["gcode_file"], latest_gcode)
        self.assertEqual(result["result"]["params_source"]["type"], "text_laser_task")
        self.assertEqual(result["result"]["params_source"]["attempt_no"], 2)
        prepare_mock.assert_called_once()
        self.assertEqual(prepare_mock.call_args.kwargs["gcode_file"], latest_gcode)
        send_mock.assert_called_once()

    def test_start_tuned_job_rejects_confirmed_text_task_material_conflict(self):
        temp_dir = self.make_temp_dir()
        task_id = "task-wood"
        task_dir = os.path.join(temp_dir, task_id)
        os.makedirs(task_dir)
        gcode_file = os.path.join(task_dir, "attempt_1.gcode")
        with open(gcode_file, "w", encoding="utf-8") as file:
            file.write("G21\n")
        with open(os.path.join(task_dir, "task.json"), "w", encoding="utf-8") as file:
            json.dump(
                {
                    "task_id": task_id,
                    "material": "椴木",
                    "thickness_mm": 3,
                    "laser_mode": "engrave",
                    "attempts": [{"attempt_no": 1, "gcode_file": gcode_file}],
                },
                file,
                ensure_ascii=False,
            )

        with patch.object(
            laser_material_calibration_tool.laser_grbl_tool,
            "prepare_gcode_file_for_sending",
        ) as prepare_mock:
            result = laser_material_calibration_tool.start_tuned_job(
                task_id=task_id,
                material="亚克力",
                thickness_mm=3,
                confirmed=True,
                connection_mode="serial",
                tasks_dir=temp_dir,
            )

        self.assertFalse(result["success"], result)
        self.assertIn("材料不一致", result["result"])
        self.assertEqual(result["detail"]["task_material"], "椴木")
        self.assertEqual(result["detail"]["resolved_material"], "亚克力")
        prepare_mock.assert_not_called()

    def test_start_tuned_job_applies_multiple_passes(self):
        temp_dir = self.make_temp_dir()
        params_file = os.path.join(temp_dir, "materials.json")
        gcode_file = os.path.join(temp_dir, "job.gcode")
        with open(gcode_file, "w", encoding="utf-8") as file:
            file.write("G21\nM5\n")

        save_result = laser_material_calibration_tool.material_params(
            action="save",
            material="多次切割材料",
            thickness_mm=3,
            laser_mode="cut",
            laser_max_power=700,
            feed_rate=300,
            passes=2,
            params_file=params_file,
        )
        self.assertTrue(save_result["success"], save_result)

        prepared = {
            "source_file": gcode_file,
            "gcode_file": gcode_file,
            "converted": False,
        }
        with (
            patch.object(
                laser_material_calibration_tool.laser_grbl_tool,
                "prepare_gcode_file_for_sending",
                return_value={"success": True, "result": prepared},
            ),
            patch.object(
                laser_material_calibration_tool.laser_execution,
                "send_file",
                return_value={"success": True, "job_id": "job-3"},
            ),
        ):
            result = laser_material_calibration_tool.start_tuned_job(
                material="多次切割材料",
                thickness_mm=3,
                laser_mode="cut",
                gcode_file=gcode_file,
                confirmed=True,
                connection_mode="serial",
                params_file=params_file,
            )

        self.assertTrue(result["success"], result)
        repeated_file = result["result"]["prepared"]["gcode_file"]
        self.assertTrue(repeated_file.endswith("_passes2.gcode"))
        self.assertTrue(os.path.isfile(repeated_file))
        self.assertEqual(result["result"]["prepared"]["passes_applied"], 2)
        self.assertEqual(
            result["result"]["prepared"]["expected_gcode_sha256"],
            file_sha256(repeated_file),
        )

    def test_run_calibration_grid_network_mode_starts_network_job(self):
        temp_dir = self.make_temp_dir()
        output_file = os.path.join(temp_dir, "grid.gcode")

        with (
            patch.object(laser_material_calibration_tool, "CALIBRATION_DIR", temp_dir),
            patch.object(
                laser_material_calibration_tool.laser_execution,
                "send_file",
                return_value={"success": True, "job_id": "net-grid"},
            ) as network_send_mock,
        ):
            result = laser_material_calibration_tool.run_calibration_grid(
                material="椴木",
                thickness_mm=3,
                laser_mode="engrave",
                rows=2,
                columns=2,
                output_file=output_file,
                confirmed=True,
                connection_mode="network",
                network_host="laser.local",
            )

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"]["connection_mode"], "network")
        self.assertEqual(result["result"]["send_result"]["job_id"], "net-grid")
        network_send_mock.assert_called_once()
        self.assertEqual(network_send_mock.call_args.args[1], "network")
        self.assertEqual(network_send_mock.call_args.kwargs.get("host"), "laser.local")

    def test_start_tuned_job_network_mode_starts_network_job(self):
        temp_dir = self.make_temp_dir()
        gcode_file = os.path.join(temp_dir, "job.gcode")
        with open(gcode_file, "w", encoding="utf-8") as file:
            file.write("G21\n")

        prepared = {
            "source_file": gcode_file,
            "gcode_file": gcode_file,
            "converted": False,
        }
        with (
            patch.object(
                laser_material_calibration_tool.laser_grbl_tool,
                "prepare_gcode_file_for_sending",
                return_value={"success": True, "result": prepared},
            ),
            patch.object(
                laser_material_calibration_tool.laser_execution,
                "send_file",
                return_value={"success": True, "job_id": "net-job"},
            ) as network_send_mock,
        ):
            result = laser_material_calibration_tool.start_tuned_job(
                material="椴木",
                thickness_mm=3,
                laser_mode="engrave",
                gcode_file=gcode_file,
                confirmed=True,
                connection_mode="network",
                network_host="laser.local",
            )

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"]["connection_mode"], "network")
        self.assertEqual(result["result"]["send_result"]["job_id"], "net-job")
        network_send_mock.assert_called_once()
        self.assertEqual(network_send_mock.call_args.args[1], "network")
        self.assertEqual(network_send_mock.call_args.kwargs.get("host"), "laser.local")

    def test_start_tuned_job_default_mode_uses_network_host_from_env(self):
        temp_dir = self.make_temp_dir()
        gcode_file = os.path.join(temp_dir, "job.gcode")
        with open(gcode_file, "w", encoding="utf-8") as file:
            file.write("G21\n")

        prepared = {
            "source_file": gcode_file,
            "gcode_file": gcode_file,
            "converted": False,
        }
        with (
            patch.object(laser_material_calibration_tool.laser_execution, "DEFAULT_CONNECTION_MODE", "network"),
            patch.object(
                laser_material_calibration_tool.laser_execution,
                "resolve_laser_network_host",
                return_value=("laser.local", None),
            ),
            patch.object(
                laser_material_calibration_tool.laser_grbl_tool,
                "prepare_gcode_file_for_sending",
                return_value={"success": True, "result": prepared},
            ),
            patch.object(
                laser_material_calibration_tool.laser_execution,
                "send_file",
                return_value={"success": True, "job_id": "env-net-job"},
            ) as network_send_mock,
        ):
            result = laser_material_calibration_tool.start_tuned_job(
                material="椴木",
                thickness_mm=3,
                laser_mode="engrave",
                gcode_file=gcode_file,
                confirmed=True,
            )

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"]["connection_mode"], "network")
        self.assertEqual(result["result"]["send_result"]["job_id"], "env-net-job")
        network_send_mock.assert_called_once()
        self.assertEqual(network_send_mock.call_args.args[1], "network")
        self.assertEqual(network_send_mock.call_args.kwargs.get("host"), "laser.local")

    def test_send_prepared_job_passes_full_prepared_result_and_converted(self):
        prepared = {
            "source_file": "a.gcode",
            "gcode_file": "a.gcode",
            "converted": True,
            "calibration_id": "cal-1",
            "detail": {"source": "calibration"},
        }
        with patch.object(
            laser_material_calibration_tool.laser_execution,
            "send_file",
            return_value={"success": True, "job_id": "cutover-job"},
        ) as send_mock:
            result = laser_material_calibration_tool._send_prepared_job(
                prepared,
                confirmed=True,
                connection_mode="serial",
                port="COM7",
                baudrate=115200,
                wait_for_response=False,
                run_in_background=True,
            )

        self.assertTrue(result["success"], result)
        send_mock.assert_called_once()
        self.assertIs(send_mock.call_args.args[0], prepared)
        self.assertTrue(send_mock.call_args.args[0]["converted"])
        self.assertEqual(send_mock.call_args.args[1], "serial")
        self.assertTrue(send_mock.call_args.kwargs.get("confirmed"))
        self.assertEqual(send_mock.call_args.kwargs.get("port"), "COM7")
        self.assertEqual(send_mock.call_args.kwargs.get("baudrate"), 115200)
        self.assertFalse(send_mock.call_args.kwargs.get("wait_for_response"))
        self.assertTrue(send_mock.call_args.kwargs.get("run_in_background"))

    def test_send_prepared_job_maps_network_options(self):
        prepared = {
            "source_file": "b.gcode",
            "gcode_file": "b.gcode",
            "converted": False,
        }
        with (
            patch.object(
                laser_material_calibration_tool.laser_execution,
                "resolve_laser_network_host",
                return_value=("laser.local", None),
            ),
            patch.object(
                laser_material_calibration_tool.laser_execution,
                "send_file",
                return_value={"success": True, "job_id": "net-cutover"},
            ) as send_mock,
        ):
            result = laser_material_calibration_tool._send_prepared_job(
                prepared,
                confirmed=True,
                connection_mode="network",
                network_host="laser.local",
                network_transport="http",
                network_http_port=81,
                network_telnet_port=24,
                network_timeout=7.5,
                wait_for_response=True,
                run_in_background=False,
            )

        self.assertTrue(result["success"], result)
        send_mock.assert_called_once()
        self.assertEqual(send_mock.call_args.args[0], prepared)
        self.assertEqual(send_mock.call_args.args[1], "network")
        self.assertEqual(send_mock.call_args.kwargs.get("host"), "laser.local")
        self.assertEqual(send_mock.call_args.kwargs.get("transport"), "http")
        self.assertEqual(send_mock.call_args.kwargs.get("http_port"), 81)
        self.assertEqual(send_mock.call_args.kwargs.get("telnet_port"), 24)
        self.assertEqual(send_mock.call_args.kwargs.get("timeout"), 7.5)
        self.assertFalse(send_mock.call_args.kwargs.get("run_in_background"))


if __name__ == "__main__":
    unittest.main()
