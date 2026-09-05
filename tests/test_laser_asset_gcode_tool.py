import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.ai_laser_gcode.ai_assistant import AiAssistResult
from tools import laser_asset_gcode_tool


class FakeMcp:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorator(func):
            self.tools[func.__name__] = func
            return func

        return decorator


class AiLaserGcodeToolTests(unittest.TestCase):
    def write_summary(self, overrides=None):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        summary_path = Path(directory.name) / "job_summary.json"
        payload = {
            "contract_version": 1,
            "gcode_path": str(Path(directory.name) / "job.nc"),
            "preview_path": str(Path(directory.name) / "job_preview.png"),
            "processed_preview_path": str(Path(directory.name) / "job_processed.png"),
            "summary_path": str(summary_path),
            "material": "wood",
            "thickness_mm": 3,
            "task_type": "engrave_logo",
            "mode": "outline",
            "output_format": "nc",
            "power": 280,
            "feed_rate": 900,
            "parameter_source": "verified",
            "confidence": "verified",
            "can_send": True,
            "requires_sample_test": False,
            "next_action": "ready_for_confirmation",
            "message": "已匹配用户验证参数，安全检查通过后仍需上层确认再发送。",
            "confirmation_required": True,
            "recommendation_status": "single_recommendation",
            "safety_report": {"can_send": True},
            "time_estimate": {"estimated_minutes": 2.5, "estimated_seconds": 150},
            "files": {
                "gcode": str(Path(directory.name) / "job.nc"),
                "preview": str(Path(directory.name) / "job_preview.png"),
                "processed_preview": str(Path(directory.name) / "job_processed.png"),
                "summary": str(summary_path),
            },
            "candidates": [],
        }
        if overrides:
            payload.update(overrides)
        summary_path.write_text(json.dumps(payload), encoding="utf-8")
        return summary_path, payload

    def make_image(self, directory, name="image.png"):
        path = Path(directory) / name
        path.write_bytes(b"not a real image for mocked tests")
        return path

    def test_summary_without_gcode_blocks_send(self):
        summary_path, _payload = self.write_summary(
            {
                "gcode_path": None,
                "can_send": False,
                "recommendation_status": "candidate_selection_required",
                "candidates": [{"preview_path": "candidate.png", "gcode_path": None}],
            }
        )

        with patch.object(laser_asset_gcode_tool.laser_grbl_tool, "prepare_gcode_file_for_sending") as send_mock:
            result = laser_asset_gcode_tool.generate_ai_laser_gcode_job(summary_path=str(summary_path), confirmed=True)

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"]["send_status"], "blocked")
        self.assertIsNone(result["result"]["summary"]["gcode_path"])
        self.assertIn("speech", result["result"])
        send_mock.assert_not_called()

    def test_can_send_true_still_requires_confirmed_before_delegating(self):
        summary_path, _payload = self.write_summary()

        with patch.object(laser_asset_gcode_tool.laser_grbl_tool, "prepare_gcode_file_for_sending") as send_mock:
            result = laser_asset_gcode_tool.generate_ai_laser_gcode_job(summary_path=str(summary_path), confirmed=False)

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"]["send_status"], "confirmation_required")
        self.assertIn("确认开始", result["result"]["speech"])
        send_mock.assert_not_called()

    def test_safety_report_can_send_false_blocks_send(self):
        summary_path, _payload = self.write_summary(
            {"can_send": True, "safety_report": {"can_send": False, "message": "需要先做小样测试"}}
        )

        with patch.object(laser_asset_gcode_tool.laser_grbl_tool, "prepare_gcode_file_for_sending") as send_mock:
            result = laser_asset_gcode_tool.generate_ai_laser_gcode_job(summary_path=str(summary_path), confirmed=True)

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"]["send_status"], "blocked")
        self.assertEqual(result["result"]["send_block_reason"], "需要先做小样测试")
        self.assertIn("需要先做小样测试", result["result"]["speech"])
        send_mock.assert_not_called()

    def test_confirmed_serial_send_delegates_to_existing_send_file_gate(self):
        summary_path, payload = self.write_summary()
        prepared = {"success": True, "result": {"gcode_file": payload["gcode_path"]}}
        send_result = {"success": True, "result": "sent"}

        with (
            patch.object(laser_asset_gcode_tool.laser_grbl_tool, "prepare_gcode_file_for_sending", return_value=prepared) as prepare_mock,
            patch.object(laser_asset_gcode_tool.laser_execution, "send_file", return_value=send_result) as send_mock,
        ):
            result = laser_asset_gcode_tool.generate_ai_laser_gcode_job(
                summary_path=str(summary_path),
                confirmed=True,
                port="COM3",
                run_in_background=False,
            )

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"]["send_status"], "delegated")
        self.assertEqual(result["result"]["send_result"], send_result)
        prepare_mock.assert_called_once_with(gcode_file=payload["gcode_path"])
        send_mock.assert_called_once()
        self.assertEqual(send_mock.call_args.args[0], prepared["result"])
        self.assertEqual(send_mock.call_args.args[1], "serial")
        self.assertTrue(send_mock.call_args.kwargs.get("confirmed"))
        self.assertEqual(send_mock.call_args.kwargs.get("port"), "COM3")
        self.assertFalse(send_mock.call_args.kwargs.get("run_in_background"))

    def test_network_send_delegates_to_existing_network_gate(self):
        summary_path, payload = self.write_summary()
        send_result = {"success": True, "result": "network preview or job"}

        prepared = {"success": True, "result": {"gcode_file": payload["gcode_path"], "converted": False}}
        with (
            patch.object(laser_asset_gcode_tool.laser_grbl_tool, "prepare_gcode_file_for_sending", return_value=prepared) as prepare_mock,
            patch.object(laser_asset_gcode_tool.laser_execution, "send_file", return_value=send_result) as send_mock,
        ):
            result = laser_asset_gcode_tool.generate_ai_laser_gcode_job(
                summary_path=str(summary_path),
                connection_mode="network",
                confirmed=True,
                host="laser.local",
                dry_run=True,
            )

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"]["send_status"], "delegated")
        prepare_mock.assert_called_once_with(gcode_file=payload["gcode_path"])
        send_mock.assert_called_once()
        self.assertEqual(send_mock.call_args.args[0]["gcode_file"], payload["gcode_path"])
        self.assertEqual(send_mock.call_args.args[1], "network")
        self.assertEqual(send_mock.call_args.kwargs["host"], "laser.local")
        self.assertTrue(send_mock.call_args.kwargs["confirmed"])
        self.assertTrue(send_mock.call_args.kwargs["dry_run"])

    def test_generate_calls_builtin_api_not_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = self.make_image(directory)
            summary_path, _payload = self.write_summary()
            bundle = SimpleNamespace(summary_path=summary_path)

            with patch.object(laser_asset_gcode_tool, "generate_job", return_value=bundle) as generate_mock:
                result = laser_asset_gcode_tool.generate_ai_laser_gcode_job(
                    image_file=str(image_path),
                    prompt="wood logo",
                    output_dir=str(Path(directory) / "out"),
                    output_format="nc",
                    mode="auto",
                    material_library="materials.json",
                    thickness_mm=3,
                    task_type="engrave_logo",
                    command="ignored-old-cli",
                )

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"]["summary_path"], str(summary_path))
        self.assertEqual(result["result"]["send_status"], "not_requested")
        generate_mock.assert_called_once()
        self.assertEqual(generate_mock.call_args.args[0], image_path)
        self.assertIn("wood logo", generate_mock.call_args.args[1])
        self.assertEqual(generate_mock.call_args.kwargs["output_format"], "nc")

    def test_generate_passes_structured_size_before_thickness_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = self.make_image(directory)
            summary_path, _payload = self.write_summary()
            bundle = SimpleNamespace(summary_path=summary_path)

            with patch.object(laser_asset_gcode_tool, "generate_job", return_value=bundle) as generate_mock:
                result = laser_asset_gcode_tool.generate_ai_laser_gcode_job(
                    image_file=str(image_path),
                    prompt="",
                    material="椴木",
                    thickness_mm=3,
                    size_mm=35,
                    mode="outline",
                    task_type="engrave_logo",
                )

        self.assertTrue(result["success"], result)
        generate_mock.assert_called_once()
        self.assertTrue(generate_mock.call_args.args[1].startswith("size 35mm"))
        self.assertEqual(generate_mock.call_args.kwargs["size_mm"], 35)

    def test_generate_passes_structured_width_and_height(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = self.make_image(directory)
            summary_path, _payload = self.write_summary()
            bundle = SimpleNamespace(summary_path=summary_path)

            with patch.object(laser_asset_gcode_tool, "generate_job", return_value=bundle) as generate_mock:
                result = laser_asset_gcode_tool.generate_ai_laser_gcode_job(
                    image_file=str(image_path),
                    material="椴木",
                    thickness_mm=3,
                    width_mm=40,
                    height_mm=20,
                    mode="outline",
                    task_type="engrave_logo",
                )

        self.assertTrue(result["success"], result)
        generate_mock.assert_called_once()
        self.assertEqual(generate_mock.call_args.kwargs["width_mm"], 40)
        self.assertEqual(generate_mock.call_args.kwargs["height_mm"], 20)

    def test_raster_generation_auto_retries_with_coarser_pixel_size_when_complex(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = self.make_image(directory)
            summary_path, _payload = self.write_summary({"mode": "raster", "task_type": "engrave_photo"})
            bundle = SimpleNamespace(summary_path=summary_path)

            with patch.object(
                laser_asset_gcode_tool,
                "generate_job",
                side_effect=[
                    ValueError("Raster output is too complex; increase pixel size, reduce size, or crop the image"),
                    bundle,
                ],
            ) as generate_mock:
                result = laser_asset_gcode_tool.generate_ai_laser_gcode_job(
                    image_file=str(image_path),
                    material="椴木",
                    thickness_mm=3,
                    mode="raster",
                    task_type="engrave_photo",
                )

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"]["auto_adjustment"]["reason"], "raster_output_too_complex")
        self.assertEqual(result["result"]["auto_adjustment"]["pixel_size_mm"], 0.2)
        self.assertIn("0.2mm", result["result"]["speech"])
        self.assertEqual(generate_mock.call_count, 2)
        self.assertIsNone(generate_mock.call_args_list[0].kwargs["pixel_size_mm"])
        self.assertEqual(generate_mock.call_args_list[1].kwargs["pixel_size_mm"], 0.2)

    def test_raster_generation_auto_retries_when_output_is_too_large(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = self.make_image(directory)
            summary_path, _payload = self.write_summary({"mode": "raster", "task_type": "engrave_photo"})
            bundle = SimpleNamespace(summary_path=summary_path)

            with patch.object(
                laser_asset_gcode_tool,
                "generate_job",
                side_effect=[
                    ValueError("Raster output is too large; increase pixel size, reduce size, or crop the image"),
                    bundle,
                ],
            ) as generate_mock:
                result = laser_asset_gcode_tool.generate_ai_laser_gcode_job(
                    image_file=str(image_path),
                    material="椴木",
                    thickness_mm=3,
                    mode="raster",
                    task_type="engrave_photo",
                )

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"]["auto_adjustment"]["reason"], "raster_output_too_complex")
        self.assertEqual(result["result"]["auto_adjustment"]["pixel_size_mm"], 0.2)
        self.assertIn("0.2mm", result["result"]["speech"])
        self.assertEqual(generate_mock.call_count, 2)
        self.assertIsNone(generate_mock.call_args_list[0].kwargs["pixel_size_mm"])
        self.assertEqual(generate_mock.call_args_list[1].kwargs["pixel_size_mm"], 0.2)

    def test_generation_speech_mentions_auto_raster_profile(self):
        profile = {
            "strategy": "auto",
            "image_type": "photo",
            "reason": "照片灰度层次较多，综合视觉细节、预计时间和复杂度后选择 horizontal + 0.2mm",
            "selected": {"raster_scan_direction": "horizontal", "pixel_size_mm": 0.2},
            "candidates": [],
        }
        _summary_path, payload = self.write_summary(
            {
                "mode": "raster",
                "task_type": "engrave_photo",
                "auto_raster_profile": profile,
                "raster": {"quality_profile": profile},
            }
        )

        speech = laser_asset_gcode_tool._build_generation_speech(payload)

        self.assertIn("已自动选择 horizontal + 0.2mm", speech)
        self.assertIn("照片灰度层次较多", speech)

    def test_generate_defaults_to_local_lasergrbl_material_library(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = self.make_image(directory)
            library_path = Path(directory) / ".lasergrbl_materials.json"
            library_path.write_text('{"version": 1, "materials": {}}', encoding="utf-8")
            summary_path, _payload = self.write_summary()
            bundle = SimpleNamespace(summary_path=summary_path)

            with (
                patch.object(laser_asset_gcode_tool, "DEFAULT_MATERIAL_LIBRARY_PATH", library_path),
                patch.object(laser_asset_gcode_tool, "generate_job", return_value=bundle) as generate_mock,
            ):
                result = laser_asset_gcode_tool.generate_ai_laser_gcode_job(
                    image_file=str(image_path),
                    material="椴木",
                    thickness_mm=3,
                    mode="outline",
                    task_type="engrave_logo",
                )

        self.assertTrue(result["success"], result)
        self.assertEqual(generate_mock.call_args.kwargs["material_library_path"], library_path)

    def test_inspect_returns_speech_without_requiring_material_or_thickness(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = self.make_image(directory)
            summary_path, _payload = self.write_summary({"gcode_path": None, "preview_path": None, "inspect_only": True})
            bundle = SimpleNamespace(summary_path=summary_path)

            with patch.object(laser_asset_gcode_tool, "generate_job", return_value=bundle) as generate_mock:
                result = laser_asset_gcode_tool.generate_ai_laser_gcode_job(
                    action="inspect",
                    image_file=str(image_path),
                    prompt="只检查这张图",
                )

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"]["action"], "inspect")
        self.assertIn("speech", result["result"])
        generate_mock.assert_called_once()
        self.assertTrue(generate_mock.call_args.kwargs["inspect"])

    def test_missing_material_or_thickness_does_not_generate_final_file(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = self.make_image(directory)
            with patch.object(laser_asset_gcode_tool, "generate_job") as generate_mock:
                result = laser_asset_gcode_tool.generate_ai_laser_gcode_job(image_file=str(image_path), prompt="做一个轮廓图")

        self.assertFalse(result["success"])
        self.assertEqual(result["detail"]["missing_fields"], ["material", "thickness_mm"])
        self.assertIn("材料", result["speech"])
        self.assertIn("厚度", result["speech"])
        generate_mock.assert_not_called()

    def test_ai_image_source_is_disabled_without_network_call(self):
        with patch.object(laser_asset_gcode_tool, "prepare_material_source") as prepare_mock:
            result = laser_asset_gcode_tool.generate_ai_laser_gcode_job(
                source_type="ai-image",
                prompt="wood thickness 3mm",
                text="一只猫",
                thickness_mm=3,
            )

        self.assertFalse(result["success"])
        self.assertIn("暂未开放", result["result"])
        prepare_mock.assert_not_called()

    def test_explicit_path_wins_over_recent_and_input_dir(self):
        with tempfile.TemporaryDirectory() as directory:
            explicit = self.make_image(directory, "explicit.png")
            recent = self.make_image(directory, "recent.png")
            input_dir = Path(directory) / "laser_inputs"
            input_dir.mkdir()
            self.make_image(input_dir, "latest.png")
            state_path = Path(directory) / "state.json"
            laser_asset_gcode_tool._save_recent_material({"source_type": "file", "path": str(recent)}, state_path)

            result = laser_asset_gcode_tool._resolve_voice_image_source(
                image_file=str(explicit),
                state_path=state_path,
                input_dir=input_dir,
            )

        self.assertEqual(result["origin"], "explicit_path")
        self.assertEqual(result["path"], str(explicit))

    def test_recent_material_wins_over_input_dir_when_no_explicit_path(self):
        with tempfile.TemporaryDirectory() as directory:
            recent = self.make_image(directory, "recent.png")
            input_dir = Path(directory) / "laser_inputs"
            input_dir.mkdir()
            self.make_image(input_dir, "latest.png")
            state_path = Path(directory) / "state.json"
            laser_asset_gcode_tool._save_recent_material({"source_type": "file", "path": str(recent)}, state_path)

            result = laser_asset_gcode_tool._resolve_voice_image_source(state_path=state_path, input_dir=input_dir)

        self.assertEqual(result["origin"], "recent")
        self.assertEqual(result["path"], str(recent))

    def test_input_dir_latest_image_used_when_no_recent_material(self):
        with tempfile.TemporaryDirectory() as directory:
            input_dir = Path(directory) / "laser_inputs"
            input_dir.mkdir()
            old_image = self.make_image(input_dir, "old.png")
            new_image = self.make_image(input_dir, "new.png")
            os.utime(old_image, (1, 1))
            os.utime(new_image, (2, 2))

            result = laser_asset_gcode_tool._resolve_voice_image_source(
                state_path=Path(directory) / "missing_state.json",
                input_dir=input_dir,
            )

        self.assertEqual(result["origin"], "input_dir_latest")
        self.assertEqual(result["path"], str(new_image))

    def test_missing_source_returns_question_not_fabricated_path(self):
        with tempfile.TemporaryDirectory() as directory:
            result = laser_asset_gcode_tool._resolve_voice_image_source(
                state_path=Path(directory) / "missing_state.json",
                input_dir=Path(directory) / "empty_inputs",
            )

        self.assertEqual(result["status"], "ask")
        self.assertIn("没有找到可用图片", result["message"])

    def test_image_search_saves_candidates_without_auto_selecting_first(self):
        with tempfile.TemporaryDirectory() as directory:
            candidate = self.make_image(directory, "candidate.png")
            state_path = Path(directory) / "state.json"
            search = laser_asset_gcode_tool.generate_ai_laser_gcode_job(
                action="image_search",
                candidates=[{"id": "first", "title": "第一张", "path": str(candidate)}],
                state_path=state_path,
            )

            with patch.object(laser_asset_gcode_tool, "generate_job") as generate_mock:
                result = laser_asset_gcode_tool.generate_ai_laser_gcode_job(
                    source_type="image-search",
                    prompt="wood logo",
                    thickness_mm=3,
                    state_path=state_path,
                )

        self.assertTrue(search["success"], search)
        self.assertEqual(search["result"]["candidate_count"], 1)
        self.assertFalse(result["success"], result)
        self.assertIn("需要先选择候选", result["result"])
        generate_mock.assert_not_called()

    def test_candidate_index_out_of_range_does_not_generate(self):
        with tempfile.TemporaryDirectory() as directory:
            candidate = self.make_image(directory, "candidate.png")
            state_path = Path(directory) / "state.json"
            laser_asset_gcode_tool._save_image_search_candidates([{"id": "first", "path": str(candidate)}], state_path)

            with patch.object(laser_asset_gcode_tool, "generate_job") as generate_mock:
                result = laser_asset_gcode_tool.generate_ai_laser_gcode_job(
                    source_type="image-search",
                    candidate_index=2,
                    prompt="wood logo",
                    thickness_mm=3,
                    state_path=state_path,
                )

        self.assertFalse(result["success"], result)
        self.assertIn("超出范围", result["result"])
        generate_mock.assert_not_called()

    def test_candidate_index_selection_generates_with_selected_path(self):
        with tempfile.TemporaryDirectory() as directory:
            candidate = self.make_image(directory, "candidate.png")
            state_path = Path(directory) / "state.json"
            summary_path, _payload = self.write_summary()
            bundle = SimpleNamespace(summary_path=summary_path)
            laser_asset_gcode_tool._save_image_search_candidates([{"id": "first", "path": str(candidate)}], state_path)

            with patch.object(laser_asset_gcode_tool, "generate_job", return_value=bundle) as generate_mock:
                result = laser_asset_gcode_tool.generate_ai_laser_gcode_job(
                    source_type="image-search",
                    candidate_index=1,
                    prompt="wood logo",
                    thickness_mm=3,
                    state_path=state_path,
                )

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"]["source"]["origin"], "candidate")
        generate_mock.assert_called_once()
        self.assertEqual(generate_mock.call_args.args[0], candidate)

    def test_candidate_id_selection_generates_with_selected_path(self):
        with tempfile.TemporaryDirectory() as directory:
            first = self.make_image(directory, "first.png")
            second = self.make_image(directory, "second.png")
            state_path = Path(directory) / "state.json"
            summary_path, _payload = self.write_summary()
            bundle = SimpleNamespace(summary_path=summary_path)
            laser_asset_gcode_tool._save_image_search_candidates(
                [
                    {"id": "first", "path": str(first)},
                    {"id": "second", "path": str(second)},
                ],
                state_path,
            )

            with patch.object(laser_asset_gcode_tool, "generate_job", return_value=bundle) as generate_mock:
                result = laser_asset_gcode_tool.generate_ai_laser_gcode_job(
                    source_type="image-search",
                    candidate_id="second",
                    prompt="wood logo",
                    thickness_mm=3,
                    state_path=state_path,
                )

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"]["source"]["origin"], "candidate")
        self.assertEqual(result["result"]["source"]["path"], str(second))
        generate_mock.assert_called_once()
        self.assertEqual(generate_mock.call_args.args[0], second)

    def test_text_source_prepares_asset_updates_recent_and_generates(self):
        with tempfile.TemporaryDirectory() as directory:
            processed = self.make_image(directory, "text_processed.png")
            state_path = Path(directory) / "state.json"
            assets_dir = Path(directory) / "assets"
            summary_path, _payload = self.write_summary()
            bundle = SimpleNamespace(summary_path=summary_path)
            asset = SimpleNamespace(processed_path=processed)

            with (
                patch.object(laser_asset_gcode_tool, "prepare_material_source", return_value=asset) as source_mock,
                patch.object(laser_asset_gcode_tool, "generate_job", return_value=bundle) as generate_mock,
            ):
                result = laser_asset_gcode_tool.generate_ai_laser_gcode_job(
                    source_type="text",
                    text="MOSS",
                    prompt="wood logo",
                    material="wood",
                    thickness_mm=3,
                    state_path=state_path,
                    assets_dir=str(assets_dir),
                )

            state = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"]["source"]["source_type"], "text")
        self.assertEqual(result["result"]["source"]["path"], str(processed))
        source_mock.assert_called_once()
        request = source_mock.call_args.args[0]
        self.assertEqual(request.source_type, "text")
        self.assertEqual(request.text, "MOSS")
        self.assertEqual(request.assets_dir, assets_dir)
        generate_mock.assert_called_once()
        self.assertEqual(generate_mock.call_args.args[0], processed)
        self.assertEqual(state["recent_material"]["source_type"], "text")
        self.assertEqual(state["recent_material"]["path"], str(processed))

    def test_state_persistence_strips_secret_fields_and_url_queries(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            laser_asset_gcode_tool._save_image_search_candidates(
                [
                    {
                        "id": "first",
                        "title": "公开图片",
                        "url": "https://example.com/image.png?token=secret",
                        "api_key": "secret-value",
                    }
                ],
                state_path,
            )
            payload = json.loads(state_path.read_text(encoding="utf-8"))

        text = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("api_key", text)
        self.assertNotIn("secret", text)
        self.assertIn("https://example.com/image.png", text)

    def test_generation_speech_mentions_time_and_excludes_paths(self):
        _summary_path, payload = self.write_summary(
            {
                "preview_path": r"C:\Users\private\job_preview.png",
                "processed_preview_path": r"C:\Users\private\job_processed.png",
            }
        )

        speech = laser_asset_gcode_tool._build_generation_speech(payload)

        self.assertIn("预计需要约 2.5 分钟", speech)
        self.assertIn("材料 wood", speech)
        self.assertIn("功率 S280", speech)
        self.assertNotIn("处理后预览", speech)
        self.assertNotIn("加工路径预览", speech)
        self.assertNotIn(r"C:\Users", speech)

    def test_generation_speech_omits_single_pass_but_mentions_multiple_passes(self):
        _summary_path, payload = self.write_summary({"passes": 1})

        single_pass_speech = laser_asset_gcode_tool._build_generation_speech(payload)
        multi_pass_speech = laser_asset_gcode_tool._build_generation_speech({**payload, "passes": 2})

        self.assertNotIn("1 遍", single_pass_speech)
        self.assertIn("2 遍", multi_pass_speech)

    def test_speech_redacts_urls_secrets_and_paths(self):
        speech = laser_asset_gcode_tool._redact_speech(
            r"文件 C:\Users\private\job.nc，图片 https://example.com/image.png?token=secret，password=secret-value"
        )

        self.assertNotIn(r"C:\Users", speech)
        self.assertNotIn("https://example.com", speech)
        self.assertNotIn("secret-value", speech)
        self.assertIn("本地文件", speech)
        self.assertIn("图片链接", speech)

    def test_analyze_ai_image_source_is_disabled_without_network_call(self):
        with (
            patch.object(laser_asset_gcode_tool, "prepare_material_source") as source_mock,
            patch.object(laser_asset_gcode_tool, "run_ai_assist") as assist_mock,
        ):
            result = laser_asset_gcode_tool.generate_ai_laser_gcode_job(
                action="analyze_image",
                source_type="ai-image",
                text="生成一张猫图",
            )

        self.assertFalse(result["success"])
        self.assertIn("暂未开放", result["result"])
        source_mock.assert_not_called()
        assist_mock.assert_not_called()

    def test_test_ai_provider_reports_unconfigured_without_network_call(self):
        with (
            patch.object(laser_asset_gcode_tool, "load_ai_provider_config", return_value=None) as config_mock,
            patch.object(laser_asset_gcode_tool, "run_ai_assist") as assist_mock,
        ):
            result = laser_asset_gcode_tool.generate_ai_laser_gcode_job(action="test_ai_provider")

        self.assertTrue(result["success"], result)
        self.assertFalse(result["result"]["configured"])
        self.assertIn("不完整", result["result"]["speech"])
        config_mock.assert_called_once()
        assist_mock.assert_not_called()

    def test_analyze_image_uses_ai_assist_with_local_source_and_safe_speech(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = self.make_image(directory)
            state_path = Path(directory) / "state.json"
            analysis = AiAssistResult(
                enabled=True,
                image_upload_allowed=False,
                provider_configured=False,
                status="disabled_by_user",
                recommendations={"mode_recommendation": "outline", "confidence": 0.7},
            )

            with (
                patch.object(laser_asset_gcode_tool, "load_ai_provider_config", return_value=None) as config_mock,
                patch.object(laser_asset_gcode_tool, "run_ai_assist", return_value=analysis) as assist_mock,
            ):
                result = laser_asset_gcode_tool.generate_ai_laser_gcode_job(
                    action="analyze_image",
                    image_file=str(image_path),
                    prompt="判断适合轮廓还是光栅",
                    state_path=state_path,
                )

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"]["action"], "analyze_image")
        self.assertEqual(result["result"]["source"]["origin"], "explicit_path")
        self.assertIn("不能替代材料库", result["result"]["speech"])
        config_mock.assert_called_once()
        assist_mock.assert_called_once()
        self.assertEqual(assist_mock.call_args.args[0], image_path)
        self.assertTrue(assist_mock.call_args.kwargs["enabled"])

    def test_candidate_url_selection_prepares_downloaded_asset_with_mocked_network(self):
        with tempfile.TemporaryDirectory() as directory:
            processed = self.make_image(directory, "downloaded_processed.png")
            state_path = Path(directory) / "state.json"
            assets_dir = Path(directory) / "assets"
            summary_path, _payload = self.write_summary()
            bundle = SimpleNamespace(summary_path=summary_path)
            asset = SimpleNamespace(processed_path=processed)
            laser_asset_gcode_tool._save_image_search_candidates(
                [{"id": "web", "title": "网页图片", "url": "https://example.com/logo.png?token=secret"}],
                state_path,
            )

            with (
                patch.object(laser_asset_gcode_tool, "prepare_material_source", return_value=asset) as source_mock,
                patch.object(laser_asset_gcode_tool, "generate_job", return_value=bundle) as generate_mock,
            ):
                result = laser_asset_gcode_tool.generate_ai_laser_gcode_job(
                    source_type="image-search",
                    candidate_id="web",
                    prompt="wood logo",
                    material="wood",
                    thickness_mm=3,
                    state_path=state_path,
                    assets_dir=str(assets_dir),
                )

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"]["source"]["source_type"], "url")
        self.assertEqual(result["result"]["source"]["url"], "https://example.com/logo.png")
        source_mock.assert_called_once()
        request = source_mock.call_args.args[0]
        self.assertEqual(request.source_type, "image-search")
        self.assertEqual(request.url, "https://example.com/logo.png")
        self.assertEqual(request.assets_dir, assets_dir)
        generate_mock.assert_called_once()
        self.assertEqual(generate_mock.call_args.args[0], processed)

    def test_candidate_speech_prompts_for_number(self):
        speech = laser_asset_gcode_tool._build_candidate_speech([{"id": "a"}, {"id": "b"}])

        self.assertIn("2 张候选图片", speech)
        self.assertIn("选第几张", speech)

    def test_register_tool_exposes_ai_laser_gcode_tool(self):
        fake = FakeMcp()
        laser_asset_gcode_tool.register_tool(fake)

        with patch.object(
            laser_asset_gcode_tool,
            "generate_ai_laser_gcode_job",
            return_value={"success": True, "result": "ok"},
        ) as generate_mock:
            result = fake.tools["ai_laser_gcode_tool"]("input.png", "wood")

        self.assertEqual(result, {"success": True, "result": "ok"})
        generate_mock.assert_called_once()
        self.assertEqual(generate_mock.call_args.kwargs["image_file"], "input.png")
        self.assertEqual(generate_mock.call_args.kwargs["prompt"], "wood")

    def test_send_generated_passes_full_prepared_result_converted(self):
        summary_path, payload = self.write_summary()
        prepared = {
            "success": True,
            "result": {
                "source_file": payload["gcode_path"],
                "gcode_file": payload["gcode_path"],
                "converted": True,
                "source": "ai_summary",
            },
        }
        send_result = {"success": True, "job_id": "ai-job", "status": "pending"}

        with (
            patch.object(
                laser_asset_gcode_tool.laser_grbl_tool,
                "prepare_gcode_file_for_sending",
                return_value=prepared,
            ),
            patch.object(
                laser_asset_gcode_tool.laser_execution,
                "send_file",
                return_value=send_result,
            ) as send_mock,
        ):
            result = laser_asset_gcode_tool.generate_ai_laser_gcode_job(
                summary_path=str(summary_path),
                confirmed=True,
                connection_mode="serial",
                port="COM4",
                run_in_background=True,
            )

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"]["send_status"], "delegated")
        self.assertEqual(result["result"]["send_result"], send_result)
        send_mock.assert_called_once()
        self.assertEqual(send_mock.call_args.args[0], prepared["result"])
        self.assertTrue(send_mock.call_args.args[0]["converted"])
        self.assertEqual(send_mock.call_args.args[1], "serial")
        self.assertTrue(send_mock.call_args.kwargs.get("confirmed"))
        self.assertEqual(send_mock.call_args.kwargs.get("port"), "COM4")

    def test_invalid_connection_mode_is_rejected_without_sender_backend(self):
        summary_path, payload = self.write_summary()
        Path(payload["gcode_path"]).write_text("G21\n", encoding="utf-8")
        prepared = {
            "success": True,
            "result": {
                "source_file": payload["gcode_path"],
                "gcode_file": payload["gcode_path"],
                "converted": False,
            },
        }

        with (
            patch.object(
                laser_asset_gcode_tool.laser_grbl_tool,
                "prepare_gcode_file_for_sending",
                return_value=prepared,
            ),
            patch.object(
                laser_asset_gcode_tool.laser_execution,
                "send_file",
                wraps=laser_asset_gcode_tool.laser_execution.send_file,
            ) as send_mock,
            patch(
                "core._laser_execution_backend.execute_serial_send_file"
            ) as serial_exec,
            patch(
                "core._laser_execution_backend.start_serial_send_file_job"
            ) as serial_start,
            patch(
                "core._laser_execution_backend.execute_network_send_file"
            ) as network_exec,
            patch(
                "core._laser_execution_backend.start_network_send_file_job"
            ) as network_start,
        ):
            result = laser_asset_gcode_tool.generate_ai_laser_gcode_job(
                summary_path=str(summary_path),
                confirmed=True,
                connection_mode="wifi",
            )

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"]["send_status"], "delegated")
        send_result = result["result"]["send_result"]
        self.assertFalse(send_result["success"], send_result)
        self.assertIn("connection_mode", send_result["result"])
        send_mock.assert_called_once()
        self.assertEqual(send_mock.call_args.args[1], "wifi")
        serial_exec.assert_not_called()
        serial_start.assert_not_called()
        network_exec.assert_not_called()
        network_start.assert_not_called()

    def test_empty_connection_mode_is_rejected_without_falling_back_to_serial(self):
        summary_path, payload = self.write_summary()
        prepared = {
            "success": True,
            "result": {
                "source_file": payload["gcode_path"],
                "gcode_file": payload["gcode_path"],
                "converted": False,
            },
        }

        with (
            patch.object(
                laser_asset_gcode_tool.laser_grbl_tool,
                "prepare_gcode_file_for_sending",
                return_value=prepared,
            ),
            patch.object(
                laser_asset_gcode_tool.laser_execution,
                "send_file",
                wraps=laser_asset_gcode_tool.laser_execution.send_file,
            ) as send_mock,
            patch(
                "core._laser_execution_backend.execute_serial_send_file"
            ) as serial_exec,
            patch(
                "core._laser_execution_backend.start_serial_send_file_job"
            ) as serial_start,
        ):
            result = laser_asset_gcode_tool.generate_ai_laser_gcode_job(
                summary_path=str(summary_path),
                confirmed=True,
                connection_mode="",
            )

        self.assertTrue(result["success"], result)
        send_result = result["result"]["send_result"]
        self.assertFalse(send_result["success"], send_result)
        self.assertIn("connection_mode", send_result["result"])
        self.assertEqual(send_mock.call_args.args[1], "")
        serial_exec.assert_not_called()
        serial_start.assert_not_called()

    def test_summary_projection_requires_threshold_resize_and_dither(self):
        summary = {
            "matched_thickness_mm": 3.0,
            "material_match_policy": "exact_only",
            "send_policy": "verified_only",
            "match_type": "exact",
            "auto_raster_profile": {"strategy": "auto"},
            "threshold": 100,
            "resize_strategy": "darkest_region",
            "dither_algorithm": "threshold",
            "raster": {
                "threshold": 100,
                "resize_strategy": "darkest_region",
                "dither_algorithm": "threshold",
            },
        }
        projection = laser_asset_gcode_tool._summary_projection(summary)
        for key in ("threshold", "resize_strategy", "dither_algorithm"):
            self.assertIn(key, projection)
            self.assertIsNotNone(projection[key])
            self.assertNotEqual(projection[key], "")
        for key in ("matched_thickness_mm", "material_match_policy", "send_policy", "match_type", "auto_raster_profile"):
            self.assertIn(key, projection)
            self.assertIsNotNone(projection[key])

        nested_only = laser_asset_gcode_tool._summary_projection(
            {
                "raster": {
                    "threshold": 90,
                    "resize_strategy": "nearest",
                    "dither_algorithm": "floyd_steinberg",
                },
                "matched_thickness_mm": 3.0,
                "material_match_policy": "exact_only",
                "send_policy": "verified_only",
            }
        )
        self.assertEqual(nested_only["threshold"], 90)
        self.assertEqual(nested_only["resize_strategy"], "nearest")
        self.assertEqual(nested_only["dither_algorithm"], "floyd_steinberg")

    def test_speech_includes_final_raster_strategy_facts(self):
        speech = laser_asset_gcode_tool._build_generation_speech(
            {
                "mode": "raster",
                "material": "test",
                "thickness_mm": 3,
                "power": 100,
                "feed_rate": 900,
                "threshold": 100,
                "resize_strategy": "darkest_region",
                "dither_algorithm": "threshold",
            }
        )
        self.assertIn("阈值 100", speech)
        self.assertIn("缩放 darkest_region", speech)
        self.assertIn("抖动 threshold", speech)

    def test_missing_raster_strategy_fields_are_detectable(self):
        complete = {
            "threshold": 100,
            "resize_strategy": "darkest_region",
            "dither_algorithm": "threshold",
        }
        self.assertEqual(laser_asset_gcode_tool.missing_raster_strategy_fields(complete), [])

        for key in ("threshold", "resize_strategy", "dither_algorithm"):
            incomplete = dict(complete)
            incomplete.pop(key)
            missing = laser_asset_gcode_tool.missing_raster_strategy_fields(incomplete)
            self.assertIn(key, missing)

        # Nested-only still counts as present for projection.
        nested = {
            "raster": {
                "threshold": 90,
                "resize_strategy": "nearest",
                "dither_algorithm": "floyd_steinberg",
            }
        }
        self.assertEqual(laser_asset_gcode_tool.missing_raster_strategy_fields(nested), [])

        empty_speech = laser_asset_gcode_tool._format_raster_strategy_speech({})
        self.assertEqual(empty_speech, "")
        speech = laser_asset_gcode_tool._format_raster_strategy_speech(complete)
        for token in ("阈值 100", "缩放 darkest_region", "抖动 threshold"):
            self.assertIn(token, speech)


if __name__ == "__main__":
    unittest.main()
