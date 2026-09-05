import os
import json
import shutil
import unittest
import uuid
from unittest.mock import patch

from core.laser_runtime.models import file_sha256
from tools import text_laser_task_tool


class FakeMcp:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorator(func):
            self.tools[func.__name__] = func
            return func

        return decorator


class TextLaserTaskToolTests(unittest.TestCase):
    def make_temp_dir(self):
        base_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", ".tmp-test")
        )
        os.makedirs(base_dir, exist_ok=True)

        temp_dir = os.path.join(base_dir, f"case-{uuid.uuid4().hex}")
        os.makedirs(temp_dir, exist_ok=False)
        self.addCleanup(shutil.rmtree, temp_dir, ignore_errors=True)
        return temp_dir

    def write_test_materials(self, temp_dir, laser_max_power=420, feed_rate=1800):
        params_file = os.path.join(temp_dir, "materials.json")
        payload = {
            "version": 1,
            "materials": {
                "椴木": {
                    "aliases": ["wood", "木头"],
                    "thicknesses": {
                        "3": {
                            "engrave": {
                                "raster": {
                                    "laser_min_power": 0,
                                    "laser_max_power": laser_max_power,
                                    "feed_rate": feed_rate,
                                    "travel_rate": 3000,
                                    "threshold": -1,
                                    "passes": 1,
                                    "pixel_size_mm": 0.1,
                                }
                            }
                        }
                    },
                }
            },
        }
        with open(params_file, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False)
        return params_file

    def fake_text_gcode(self, **kwargs):
        # Content-hash binding needs real files on disk; keep fixtures minimal.
        image_path = kwargs["image_output_file"]
        gcode_path = kwargs["gcode_output_file"]
        parent = os.path.dirname(gcode_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(image_path, "wb") as file:
            file.write(b"mock-image")
        with open(gcode_path, "w", encoding="utf-8", newline="\n") as file:
            # G4 dwell keeps the legacy fixture speech "9 分 19 秒" after real file hashing.
            file.write("G21\nG90\nG4 P559.1\nM5\n")
        return {
            "success": True,
            "result": {
                "text": kwargs["text"],
                "image_file": image_path,
                "gcode_file": gcode_path,
                "image": {"output_file": image_path},
                "gcode": {
                    "gcode_file": gcode_path,
                    "auto_trim": {
                        "enabled": kwargs.get("auto_trim", True),
                        "trimmed": True,
                        "crop_box": {"x_px": 1, "y_px": 2, "width_px": 30, "height_px": 20},
                    },
                    "placement": {
                        "final_width_mm": 30.0,
                        "final_height_mm": 20.0,
                        "offset_x_mm": kwargs.get("offset_x_mm", 0.0),
                        "offset_y_mm": kwargs.get("offset_y_mm", 0.0),
                        "safe_margin_mm": kwargs.get("safe_margin_mm", 5.0),
                    },
                },
                "time_estimate": {"estimated_seconds": 559.1, "estimated_minutes": 9.32},
                "speech": "预计雕刻 9 分 19 秒",
                "text_layout": {
                    "mode": kwargs.get("layout_mode") or "manual",
                    "auto_wrap": bool(kwargs.get("auto_wrap", False)),
                    "max_lines": int(kwargs.get("max_lines", 0) or 0),
                    "line_count": int(kwargs.get("max_lines", 0) or 1),
                    "lines": [kwargs["text"]],
                },
            },
        }

    def write_file(self, path, content="G21\n"):
        with open(path, "w", encoding="utf-8") as file:
            file.write(content)

    def test_register_tool_exposes_exact_text_laser_tools(self):
        fake = FakeMcp()

        text_laser_task_tool.register_tool(fake)

        self.assertIn("generate_text_laser_task_tool", fake.tools)
        self.assertIn("refine_laser_params_from_feedback_tool", fake.tools)
        self.assertIn("regenerate_text_laser_task_tool", fake.tools)
        self.assertNotIn("send_text_laser_task_tool", fake.tools)
        self.assertEqual(len(fake.tools), 3)

    def test_generate_text_laser_task_generates_without_sending_by_default(self):
        temp_dir = self.make_temp_dir()
        params_file = self.write_test_materials(temp_dir)

        with (
            patch.object(
                text_laser_task_tool.text_image_gcode_tool,
                "create_text_image_gcode",
                side_effect=self.fake_text_gcode,
            ) as create_mock,
            patch.object(text_laser_task_tool.laser_execution, "send_file") as send_mock,
        ):
            result = text_laser_task_tool.generate_text_laser_task(
                text="佳佳",
                material="椴木",
                thickness_mm=3,
                laser_mode="engrave",
                width_mm=30,
                offset_x_mm=2,
                offset_y_mm=3,
                tasks_dir=temp_dir,
                params_file=params_file,
            )

        self.assertTrue(result["success"], result)
        payload = result["result"]
        self.assertEqual(payload["attempt_no"], 1)
        self.assertIn("文件已生成，预计需要 9 分 19 秒", payload["speech"])
        self.assertIn("材料 椴木", payload["speech"])
        self.assertIn("功率 S420", payload["speech"])
        self.assertIn("速度 F1800", payload["speech"])
        self.assertIn("实际尺寸 30 x 20 毫米，偏移 X2 Y3 毫米", payload["speech"])
        self.assertNotIn("1 遍", payload["speech"])
        self.assertEqual(payload["time_estimate"]["estimated_seconds"], 559.1)
        self.assertEqual(payload["placement"]["offset_x_mm"], 2)
        self.assertTrue(payload["auto_trim"]["trimmed"])
        self.assertEqual(payload["text_layout"]["mode"], "manual")
        self.assertTrue(payload["confirmation_required"])
        self.assertEqual(payload["send_result"]["reason"], "not_requested")
        self.assertTrue(os.path.isfile(payload["task_file"]))
        with open(payload["task_file"], "r", encoding="utf-8") as file:
            task_payload = json.load(file)
        self.assertEqual(task_payload["dimensions"]["offset_x_mm"], 2)
        self.assertEqual(task_payload["generation_options"]["dpi"], 300.0)
        self.assertFalse(task_payload["generation_options"]["bidirectional"])
        self.assertFalse(task_payload["generation_options"]["auto_wrap"])
        self.assertEqual(task_payload["generation_options"]["max_lines"], 0)
        self.assertTrue(task_payload["attempts"][0]["auto_trim"]["trimmed"])
        self.assertEqual(task_payload["attempts"][0]["text_layout"]["mode"], "manual")
        self.assertEqual(task_payload["attempts"][0]["placement"]["final_width_mm"], 30.0)
        create_mock.assert_called_once()
        self.assertEqual(create_mock.call_args.kwargs["laser_max_power"], 420)
        self.assertEqual(create_mock.call_args.kwargs["width_mm"], 30)
        self.assertEqual(create_mock.call_args.kwargs["offset_x_mm"], 2)
        self.assertEqual(create_mock.call_args.kwargs["offset_y_mm"], 3)
        self.assertFalse(create_mock.call_args.kwargs["bidirectional"])
        self.assertTrue(create_mock.call_args.kwargs["auto_trim"])
        self.assertEqual(create_mock.call_args.kwargs["auto_wrap"], False)
        self.assertEqual(create_mock.call_args.kwargs["max_lines"], 0)
        self.assertEqual(create_mock.call_args.kwargs["layout_mode"], "")
        self.assertEqual(create_mock.call_args.kwargs["engraving_mode"], "")
        send_mock.assert_not_called()

    def test_text_task_speech_discloses_matched_thickness_and_send_block(self):
        task = {
            "material": "椴木",
            "thickness_mm": 8.0,
            "laser_mode": "cut",
            "generation_options": {},
            "recommendation": {
                "thickness_mm": 8.0,
                "matched_thickness_mm": 3.0,
                "match": "nearest_thickness",
                "warnings": ["切割不能使用其他厚度参数"],
                "can_send": False,
                "send_blocked_reason": "椴木 8mm 暂无精确切割参数",
            },
        }
        attempt = {
            "params": {"laser_max_power": 1000, "feed_rate": 100, "passes": 1},
            "placement": {},
        }

        speech = text_laser_task_tool._build_text_task_speech(
            task,
            attempt,
            {"success": True, "skipped": True, "reason": "not_requested"},
        )

        self.assertIn("请求厚度 8 毫米", speech)
        self.assertIn("参数厚度 3 毫米", speech)
        self.assertIn("当前不可发送", speech)
        self.assertIn("椴木 8mm 暂无精确切割参数", speech)

    def test_generate_cut_without_exact_thickness_never_generates_or_sends(self):
        temp_dir = self.make_temp_dir()
        create_mock = patch.object(text_laser_task_tool.text_image_gcode_tool, "create_text_image_gcode").start()
        send_mock = patch.object(text_laser_task_tool.laser_execution, "send_file").start()
        self.addCleanup(patch.stopall)

        result = text_laser_task_tool.generate_text_laser_task(
            text="111",
            material="椴木",
            thickness_mm=8,
            laser_mode="cut",
            params_file=os.path.join(temp_dir, "missing.json"),
            tasks_dir=temp_dir,
            reuse_prepared_gcode=False,
            send_after_generate=True,
            confirmed=True,
            connection_mode="network",
            network_host="laser.local",
        )

        self.assertFalse(result["success"], result)
        self.assertIn("8mm 精确切割参数", result["result"])
        create_mock.assert_not_called()
        send_mock.assert_not_called()

    def test_generate_text_laser_task_speech_mentions_vector_outline_fallback(self):
        temp_dir = self.make_temp_dir()

        def fake_fallback_text_gcode(**kwargs):
            result = self.fake_text_gcode(**kwargs)
            result["result"]["gcode"]["text_vector_outline_fallback"] = True
            result["result"]["gcode"]["vector_outline_error"] = "缺少 fontTools"
            result["result"]["speech"] = (
                text_laser_task_tool.text_image_gcode_tool.TEXT_VECTOR_OUTLINE_FALLBACK_SPEECH
                + result["result"]["speech"]
            )
            return result

        with patch.object(
            text_laser_task_tool.text_image_gcode_tool,
            "create_text_image_gcode",
            side_effect=fake_fallback_text_gcode,
        ):
            result = text_laser_task_tool.generate_text_laser_task(
                text="佳佳",
                material="椴木",
                thickness_mm=3,
                laser_mode="engrave",
                engraving_mode="outline",
                tasks_dir=temp_dir,
            )

        self.assertTrue(result["success"], result)
        self.assertTrue(
            result["result"]["speech"].startswith(
                text_laser_task_tool.text_image_gcode_tool.TEXT_VECTOR_OUTLINE_FALLBACK_SPEECH
            )
        )
        self.assertIn("文件已生成，预计需要 9 分 19 秒", result["result"]["speech"])

    def test_generate_text_laser_task_can_request_outline_engraving(self):
        temp_dir = self.make_temp_dir()
        params_file = os.path.join(temp_dir, "materials.json")

        save_result = text_laser_task_tool.laser_material_calibration_tool.material_params(
            action="save",
            material="文字轮廓材料",
            thickness_mm=3,
            laser_mode="engrave",
            engraving_mode="outline",
            laser_max_power=610,
            feed_rate=880,
            params_file=params_file,
        )
        self.assertTrue(save_result["success"], save_result)

        with patch.object(
            text_laser_task_tool.text_image_gcode_tool,
            "create_text_image_gcode",
            side_effect=self.fake_text_gcode,
        ) as create_mock:
            result = text_laser_task_tool.generate_text_laser_task(
                text="佳佳",
                material="文字轮廓材料",
                thickness_mm=3,
                engraving_mode="outline",
                params_file=params_file,
                tasks_dir=temp_dir,
            )

        self.assertTrue(result["success"], result)
        create_mock.assert_called_once()
        self.assertEqual(create_mock.call_args.kwargs["engraving_mode"], "outline")
        self.assertEqual(create_mock.call_args.kwargs["laser_max_power"], 610)
        self.assertEqual(create_mock.call_args.kwargs["feed_rate"], 880)
        self.assertEqual(result["result"]["recommendation"]["engraving_mode"], "outline")

    def test_generate_and_regenerate_preserve_text_layout_options(self):
        temp_dir = self.make_temp_dir()

        with patch.object(
            text_laser_task_tool.text_image_gcode_tool,
            "create_text_image_gcode",
            side_effect=self.fake_text_gcode,
        ) as create_mock:
            generated = text_laser_task_tool.generate_text_laser_task(
                text="AAABBBCCC",
                material="椴木",
                thickness_mm=3,
                auto_wrap=True,
                max_lines=3,
                layout_mode="balanced",
                tasks_dir=temp_dir,
            )

        self.assertTrue(generated["success"], generated)
        task_id = generated["result"]["task_id"]
        self.assertEqual(create_mock.call_args.kwargs["auto_wrap"], True)
        self.assertEqual(create_mock.call_args.kwargs["max_lines"], 3)
        self.assertEqual(create_mock.call_args.kwargs["layout_mode"], "balanced")
        self.assertEqual(generated["result"]["text_layout"]["max_lines"], 3)

        saved, error = text_laser_task_tool._load_text_task(task_id, tasks_dir=temp_dir)
        self.assertIsNone(error)
        self.assertTrue(saved["generation_options"]["auto_wrap"])
        self.assertEqual(saved["generation_options"]["max_lines"], 3)
        self.assertEqual(saved["generation_options"]["layout_mode"], "balanced")

        feedback = text_laser_task_tool.refine_laser_params_from_feedback(
            feedback_text="太焦了",
            task_id=task_id,
            tasks_dir=temp_dir,
        )
        self.assertTrue(feedback["success"], feedback)

        with patch.object(
            text_laser_task_tool.text_image_gcode_tool,
            "create_text_image_gcode",
            side_effect=self.fake_text_gcode,
        ) as regen_mock:
            regenerated = text_laser_task_tool.regenerate_text_laser_task(
                task_id=task_id,
                strategy="recommended",
                tasks_dir=temp_dir,
            )

        self.assertTrue(regenerated["success"], regenerated)
        self.assertEqual(regen_mock.call_args.kwargs["auto_wrap"], True)
        self.assertEqual(regen_mock.call_args.kwargs["max_lines"], 3)
        self.assertEqual(regen_mock.call_args.kwargs["layout_mode"], "balanced")

    def test_generate_uses_exact_prepared_gcode_before_generation(self):
        temp_dir = self.make_temp_dir()
        prepared_dir = self.make_temp_dir()
        exact_file = os.path.join(prepared_dir, "佳佳.gcode")
        contains_file = os.path.join(prepared_dir, "佳佳-椴木-3mm.nc")
        self.write_file(exact_file, "G21\nG90\nM4 S100\nG1 X10 F600\n")
        self.write_file(contains_file)

        with (
            patch.object(text_laser_task_tool.text_image_gcode_tool, "create_text_image_gcode") as create_mock,
            patch.object(text_laser_task_tool.laser_execution, "send_file") as send_mock,
        ):
            result = text_laser_task_tool.generate_text_laser_task(
                text="佳佳",
                material="椴木",
                thickness_mm=3,
                tasks_dir=temp_dir,
                prepared_gcode_dir=prepared_dir,
            )

        self.assertTrue(result["success"], result)
        payload = result["result"]
        self.assertEqual(payload["source"], "prepared_gcode_file")
        self.assertEqual(payload["match_type"], "exact")
        self.assertEqual(payload["gcode_file"], exact_file)
        self.assertEqual(payload["speech"], "已找到预制雕刻文件，预计需要 1 秒。确认开始后才发送。")
        self.assertIn("time_estimate", payload)
        self.assertEqual(payload["send_result"]["reason"], "not_requested")
        self.assertNotIn("task_id", payload)
        create_mock.assert_not_called()
        send_mock.assert_not_called()

    def test_generate_can_skip_prepared_gcode_lookup(self):
        temp_dir = self.make_temp_dir()
        prepared_dir = self.make_temp_dir()
        exact_file = os.path.join(prepared_dir, "佳佳.gcode")
        self.write_file(exact_file, "G21\nG90\nM4 S100\nG1 X10 F600\n")

        with (
            patch.object(
                text_laser_task_tool.text_image_gcode_tool,
                "create_text_image_gcode",
                side_effect=self.fake_text_gcode,
            ) as create_mock,
            patch.object(text_laser_task_tool, "_find_prepared_gcode") as lookup_mock,
        ):
            result = text_laser_task_tool.generate_text_laser_task(
                text="佳佳",
                material="椴木",
                thickness_mm=3,
                tasks_dir=temp_dir,
                prepared_gcode_dir=prepared_dir,
                reuse_prepared_gcode=False,
            )

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"]["attempt_no"], 1)
        self.assertNotEqual(result["result"].get("source"), "prepared_gcode_file")
        create_mock.assert_called_once()
        lookup_mock.assert_not_called()

    def test_generate_requires_material_before_prepared_gcode_lookup(self):
        prepared_dir = self.make_temp_dir()

        with patch.object(text_laser_task_tool, "_find_prepared_gcode") as lookup_mock:
            result = text_laser_task_tool.generate_text_laser_task(
                text="佳佳",
                material="",
                thickness_mm=0,
                prepared_gcode_dir=prepared_dir,
            )

        self.assertFalse(result["success"], result)
        self.assertIn("请指定材料", result["result"])
        lookup_mock.assert_not_called()

    def test_generate_uses_contains_prepared_gcode_when_no_exact_match(self):
        prepared_dir = self.make_temp_dir()
        prepared_file = os.path.join(prepared_dir, "佳佳-椴木-3mm.nc")
        self.write_file(prepared_file)

        with patch.object(text_laser_task_tool.text_image_gcode_tool, "create_text_image_gcode") as create_mock:
            result = text_laser_task_tool.generate_text_laser_task(
                text="佳佳",
                material="椴木",
                thickness_mm=3,
                prepared_gcode_dir=prepared_dir,
            )

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"]["source"], "prepared_gcode_file")
        self.assertEqual(result["result"]["match_type"], "contains")
        self.assertEqual(result["result"]["gcode_file"], prepared_file)
        create_mock.assert_not_called()

    def test_text_task_speech_mentions_multiple_passes_only(self):
        temp_dir = self.make_temp_dir()

        with (
            patch.object(
                text_laser_task_tool.text_image_gcode_tool,
                "create_text_image_gcode",
                side_effect=self.fake_text_gcode,
            ) as create_mock,
            patch.object(
                text_laser_task_tool.laser_material_calibration_tool,
                "_repeat_gcode_passes",
                side_effect=lambda source_file, passes: (source_file, None),
            ),
        ):
            single = text_laser_task_tool.generate_text_laser_task(
                text="佳佳",
                material="椴木",
                thickness_mm=3,
                tasks_dir=temp_dir,
            )
            multi = text_laser_task_tool.generate_text_laser_task(
                text="佳佳",
                material="椴木",
                thickness_mm=3,
                laser_mode="cut",
                passes=2,
                tasks_dir=temp_dir,
            )

        self.assertTrue(single["success"], single)
        self.assertTrue(multi["success"], multi)
        self.assertNotIn("1 遍", single["result"]["speech"])
        self.assertIn("2 遍", multi["result"]["speech"])
        self.assertIn("模式 切割", multi["result"]["speech"])
        self.assertEqual(create_mock.call_args_list[1].kwargs["laser_mode"], "cut")

    def test_generate_returns_candidates_for_ambiguous_prepared_gcode(self):
        prepared_dir = self.make_temp_dir()
        first = os.path.join(prepared_dir, "佳佳-椴木.gcode")
        second = os.path.join(prepared_dir, "佳佳-亚克力.nc")
        self.write_file(first)
        self.write_file(second)

        with patch.object(text_laser_task_tool.text_image_gcode_tool, "create_text_image_gcode") as create_mock:
            result = text_laser_task_tool.generate_text_laser_task(
                text="佳佳",
                material="椴木",
                thickness_mm=3,
                prepared_gcode_dir=prepared_dir,
            )

        self.assertTrue(result["success"], result)
        payload = result["result"]
        self.assertEqual(payload["source"], "prepared_gcode_file_lookup")
        self.assertEqual(payload["match_type"], "contains")
        self.assertEqual(set(item["path"] for item in payload["candidates"]), {first, second})
        self.assertTrue(payload["confirmation_required"])
        create_mock.assert_not_called()

    def test_generate_falls_back_when_no_prepared_gcode_matches(self):
        temp_dir = self.make_temp_dir()
        prepared_dir = self.make_temp_dir()
        self.write_file(os.path.join(prepared_dir, "其他名字.gcode"))

        with patch.object(
            text_laser_task_tool.text_image_gcode_tool,
            "create_text_image_gcode",
            side_effect=self.fake_text_gcode,
        ) as create_mock:
            result = text_laser_task_tool.generate_text_laser_task(
                text="佳佳",
                material="椴木",
                thickness_mm=3,
                tasks_dir=temp_dir,
                prepared_gcode_dir=prepared_dir,
            )

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"]["attempt_no"], 1)
        self.assertTrue(os.path.isfile(result["result"]["task_file"]))
        create_mock.assert_called_once()

    def test_prepared_gcode_confirmed_send_still_uses_confirmation_gate(self):
        prepared_dir = self.make_temp_dir()
        prepared_file = os.path.join(prepared_dir, "佳佳.gcode")
        self.write_file(prepared_file)

        with (
            patch.object(text_laser_task_tool.text_image_gcode_tool, "create_text_image_gcode") as create_mock,
            patch.object(
                text_laser_task_tool.laser_execution,
                "send_file",
                return_value={"success": True, "job_id": "prepared-serial-job"},
            ) as serial_mock,
        ):
            preview = text_laser_task_tool.generate_text_laser_task(
                text="佳佳",
                material="椴木",
                thickness_mm=3,
                send_after_generate=True,
                confirmed=False,
                connection_mode="serial",
                prepared_gcode_dir=prepared_dir,
            )
            confirmed = text_laser_task_tool.generate_text_laser_task(
                text="佳佳",
                material="椴木",
                thickness_mm=3,
                send_after_generate=True,
                confirmed=True,
                connection_mode="serial",
                prepared_gcode_dir=prepared_dir,
            )

        self.assertTrue(preview["success"], preview)
        self.assertEqual(preview["result"]["send_result"]["reason"], "confirmation_required")
        self.assertTrue(confirmed["success"], confirmed)
        self.assertEqual(confirmed["result"]["send_result"]["job_id"], "prepared-serial-job")
        self.assertEqual(serial_mock.call_args.args[0]["gcode_file"], prepared_file)
        self.assertEqual(
            serial_mock.call_args.args[0]["expected_gcode_sha256"],
            file_sha256(prepared_file),
        )
        self.assertEqual(serial_mock.call_args.args[1], "serial")
        self.assertEqual(serial_mock.call_count, 1)
        create_mock.assert_not_called()

    def test_generate_with_unconfirmed_send_returns_preview_without_sending(self):
        temp_dir = self.make_temp_dir()

        with (
            patch.object(
                text_laser_task_tool.text_image_gcode_tool,
                "create_text_image_gcode",
                side_effect=self.fake_text_gcode,
            ),
            patch.object(text_laser_task_tool.laser_execution, "send_file") as network_mock,
        ):
            result = text_laser_task_tool.generate_text_laser_task(
                text="佳佳",
                material="椴木",
                thickness_mm=3,
                send_after_generate=True,
                confirmed=False,
                network_host="laser.local",
                tasks_dir=temp_dir,
            )

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"]["send_result"]["reason"], "confirmation_required")
        network_mock.assert_not_called()

    def test_generate_confirmed_network_send_routes_to_network_sender(self):
        temp_dir = self.make_temp_dir()

        with (
            patch.object(
                text_laser_task_tool.text_image_gcode_tool,
                "create_text_image_gcode",
                side_effect=self.fake_text_gcode,
            ),
            patch.object(
                text_laser_task_tool.laser_execution,
                "send_file",
                return_value={"success": True, "job_id": "net-job"},
            ) as network_mock,
        ):
            result = text_laser_task_tool.generate_text_laser_task(
                text="佳佳",
                material="椴木",
                thickness_mm=3,
                send_after_generate=True,
                confirmed=True,
                connection_mode="network",
                network_host="laser.local",
                tasks_dir=temp_dir,
            )

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"]["send_result"]["job_id"], "net-job")
        network_mock.assert_called_once()
        prepared_result = network_mock.call_args.args[0]
        self.assertEqual(
            prepared_result["expected_gcode_sha256"],
            file_sha256(prepared_result["gcode_file"]),
        )
        self.assertEqual(network_mock.call_args.args[1], "network")

    def test_generate_default_mode_uses_network_host_from_env(self):
        temp_dir = self.make_temp_dir()

        with (
            patch.object(text_laser_task_tool.laser_execution, "DEFAULT_CONNECTION_MODE", "network"),
            patch.object(
                text_laser_task_tool.laser_execution,
                "resolve_laser_network_host",
                return_value=("laser.local", None),
            ),
            patch.object(
                text_laser_task_tool.text_image_gcode_tool,
                "create_text_image_gcode",
                side_effect=self.fake_text_gcode,
            ),
            patch.object(
                text_laser_task_tool.laser_execution,
                "send_file",
                return_value={"success": True, "job_id": "env-net-job"},
            ) as network_mock,
        ):
            result = text_laser_task_tool.generate_text_laser_task(
                text="佳佳",
                material="椴木",
                thickness_mm=3,
                send_after_generate=True,
                confirmed=True,
                tasks_dir=temp_dir,
            )

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"]["send_result"]["job_id"], "env-net-job")
        network_mock.assert_called_once()
        self.assertEqual(network_mock.call_args.args[1], "network")
        self.assertEqual(network_mock.call_args.kwargs.get("host"), "laser.local")

    def test_generate_network_send_missing_host_keeps_task_without_sending(self):
        temp_dir = self.make_temp_dir()

        with (
            patch.object(
                text_laser_task_tool.laser_execution,
                "resolve_laser_network_host",
                return_value=(None, "请指定 laser 网络主机 host"),
            ),
            patch.object(
                text_laser_task_tool.text_image_gcode_tool,
                "create_text_image_gcode",
                side_effect=self.fake_text_gcode,
            ),
            patch.object(text_laser_task_tool.laser_execution, "send_file") as network_mock,
        ):
            result = text_laser_task_tool.generate_text_laser_task(
                text="佳佳",
                material="椴木",
                thickness_mm=3,
                send_after_generate=True,
                confirmed=True,
                connection_mode="network",
                tasks_dir=temp_dir,
            )

        self.assertTrue(result["success"], result)
        payload = result["result"]
        self.assertTrue(os.path.isfile(payload["task_file"]))
        self.assertFalse(payload["send_result"]["success"])
        self.assertEqual(payload["send_result"]["reason"], "missing_network_host")
        network_mock.assert_not_called()

    def test_generate_serial_mode_delegates_empty_port_for_auto_detection(self):
        temp_dir = self.make_temp_dir()

        with (
            patch.object(
                text_laser_task_tool.text_image_gcode_tool,
                "create_text_image_gcode",
                side_effect=self.fake_text_gcode,
            ),
            patch.object(
                text_laser_task_tool.laser_execution,
                "send_file",
                return_value={"success": True, "job_id": "serial-job"},
            ) as serial_mock,
        ):
            result = text_laser_task_tool.generate_text_laser_task(
                text="佳佳",
                material="椴木",
                thickness_mm=3,
                send_after_generate=True,
                confirmed=True,
                connection_mode="serial",
                port="",
                tasks_dir=temp_dir,
            )

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"]["send_result"]["job_id"], "serial-job")
        serial_mock.assert_called_once()
        self.assertEqual(serial_mock.call_args.args[1], "serial")
        self.assertEqual(serial_mock.call_args.kwargs.get("port"), "")

    def test_generate_default_serial_mode_delegates_empty_port_for_auto_detection(self):
        temp_dir = self.make_temp_dir()

        with (
            patch.object(text_laser_task_tool.laser_execution, "DEFAULT_CONNECTION_MODE", "serial"),
            patch.object(
                text_laser_task_tool.text_image_gcode_tool,
                "create_text_image_gcode",
                side_effect=self.fake_text_gcode,
            ),
            patch.object(
                text_laser_task_tool.laser_execution,
                "send_file",
                return_value={"success": True, "job_id": "default-serial-job"},
            ) as serial_mock,
        ):
            result = text_laser_task_tool.generate_text_laser_task(
                text="佳佳",
                material="椴木",
                thickness_mm=3,
                send_after_generate=True,
                confirmed=True,
                port="",
                tasks_dir=temp_dir,
            )

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"]["send_result"]["job_id"], "default-serial-job")
        serial_mock.assert_called_once()
        self.assertEqual(serial_mock.call_args.args[1], "serial")
        self.assertEqual(serial_mock.call_args.kwargs.get("port"), "")

    def test_generate_invalid_default_connection_mode_fails_before_generation(self):
        temp_dir = self.make_temp_dir()

        with (
            patch.object(text_laser_task_tool.laser_execution, "DEFAULT_CONNECTION_MODE", "netwrok"),
            patch.object(text_laser_task_tool.text_image_gcode_tool, "create_text_image_gcode") as create_mock,
        ):
            result = text_laser_task_tool.generate_text_laser_task(
                text="佳佳",
                material="椴木",
                thickness_mm=3,
                tasks_dir=temp_dir,
            )

        self.assertFalse(result["success"], result)
        self.assertIn("LASER_DEFAULT_CONNECTION_MODE", result["result"])
        create_mock.assert_not_called()

    def test_feedback_too_burnt_returns_and_persists_bounded_suggestions(self):
        temp_dir = self.make_temp_dir()
        params_file = self.write_test_materials(temp_dir)

        with patch.object(
            text_laser_task_tool.text_image_gcode_tool,
            "create_text_image_gcode",
            side_effect=self.fake_text_gcode,
        ):
            generated = text_laser_task_tool.generate_text_laser_task(
                text="佳佳",
                material="椴木",
                thickness_mm=3,
                dpi=250,
                offset_x_mm=4,
                offset_y_mm=5,
                tasks_dir=temp_dir,
                params_file=params_file,
            )
        task_id = generated["result"]["task_id"]

        with patch.object(text_laser_task_tool.text_image_gcode_tool, "create_text_image_gcode") as create_mock:
            result = text_laser_task_tool.refine_laser_params_from_feedback(
                feedback_text="太焦了，边缘发黑",
                task_id=task_id,
                tasks_dir=temp_dir,
            )

        self.assertTrue(result["success"], result)
        payload = result["result"]
        self.assertEqual(payload["recommended_strategy"], "lower_power")
        self.assertEqual([item["strategy"] for item in payload["suggestions"]], ["lower_power", "raise_speed"])
        self.assertEqual(payload["suggestions"][0]["params"]["laser_max_power"], 336)
        create_mock.assert_not_called()

        saved, error = text_laser_task_tool._load_text_task(task_id, tasks_dir=temp_dir)
        self.assertIsNone(error)
        self.assertEqual(len(saved["feedback_events"]), 1)
        self.assertEqual(saved["feedback_events"][0]["matched_issue"], "too_burnt")

    def test_feedback_recognizes_mvp_keyword_groups(self):
        cases = [
            ("太浅了", "raise_power", "laser_max_power", 460),
            ("切不透", "increase_passes", "passes", 2),
            ("边缘毛糙", "lower_power", "laser_max_power", 360),
            ("broken lines", "lower_speed", "feed_rate", 850),
        ]

        for feedback, strategy, changed_key, changed_value in cases:
            with self.subTest(feedback=feedback):
                result = text_laser_task_tool.refine_laser_params_from_feedback(
                    feedback_text=feedback,
                    laser_max_power=400,
                    feed_rate=1000,
                )

                self.assertTrue(result["success"], result)
                self.assertEqual(result["result"]["recommended_strategy"], strategy)
                self.assertGreaterEqual(len(result["result"]["suggestions"]), 2)
                self.assertEqual(result["result"]["suggestions"][0]["params"][changed_key], changed_value)

    def test_unknown_feedback_with_task_id_is_persisted_as_analysis_event(self):
        temp_dir = self.make_temp_dir()

        with patch.object(
            text_laser_task_tool.text_image_gcode_tool,
            "create_text_image_gcode",
            side_effect=self.fake_text_gcode,
        ):
            generated = text_laser_task_tool.generate_text_laser_task(
                text="佳佳",
                material="椴木",
                thickness_mm=3,
                dpi=250,
                offset_x_mm=4,
                offset_y_mm=5,
                tasks_dir=temp_dir,
            )
        task_id = generated["result"]["task_id"]

        result = text_laser_task_tool.refine_laser_params_from_feedback(
            feedback_text="效果有点奇怪",
            task_id=task_id,
            tasks_dir=temp_dir,
        )

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"]["matched_issue"], "unknown")
        saved, error = text_laser_task_tool._load_text_task(task_id, tasks_dir=temp_dir)
        self.assertIsNone(error)
        self.assertEqual(saved["feedback_events"][0]["matched_issue"], "unknown")

    def test_regenerate_recommended_strategy_appends_attempt(self):
        temp_dir = self.make_temp_dir()
        params_file = self.write_test_materials(temp_dir)

        with patch.object(
            text_laser_task_tool.text_image_gcode_tool,
            "create_text_image_gcode",
            side_effect=self.fake_text_gcode,
        ):
            generated = text_laser_task_tool.generate_text_laser_task(
                text="佳佳",
                material="椴木",
                thickness_mm=3,
                dpi=250,
                offset_x_mm=4,
                offset_y_mm=5,
                tasks_dir=temp_dir,
                params_file=params_file,
            )
        task_id = generated["result"]["task_id"]
        feedback = text_laser_task_tool.refine_laser_params_from_feedback(
            feedback_text="太焦了",
            task_id=task_id,
            tasks_dir=temp_dir,
        )
        self.assertTrue(feedback["success"], feedback)

        with patch.object(
            text_laser_task_tool.text_image_gcode_tool,
            "create_text_image_gcode",
            side_effect=self.fake_text_gcode,
        ) as create_mock:
            result = text_laser_task_tool.regenerate_text_laser_task(
                task_id=task_id,
                strategy="recommended",
                tasks_dir=temp_dir,
            )

        self.assertTrue(result["success"], result)
        payload = result["result"]
        self.assertEqual(payload["attempt_no"], 2)
        self.assertEqual(payload["params"]["laser_max_power"], 336)
        self.assertEqual(create_mock.call_args.kwargs["laser_max_power"], 336)
        self.assertEqual(create_mock.call_args.kwargs["engraving_mode"], "raster")
        self.assertEqual(create_mock.call_args.kwargs["dpi"], 250)
        self.assertEqual(create_mock.call_args.kwargs["offset_x_mm"], 4)
        self.assertEqual(create_mock.call_args.kwargs["offset_y_mm"], 5)

        saved, error = text_laser_task_tool._load_text_task(task_id, tasks_dir=temp_dir)
        self.assertIsNone(error)
        self.assertEqual(len(saved["attempts"]), 2)
        self.assertEqual(saved["attempts"][1]["source"], "feedback_strategy")
        self.assertEqual(saved["attempts"][1]["selected_strategy"], "lower_power")
        self.assertEqual(saved["dimensions"]["offset_x_mm"], 4)
        self.assertEqual(saved["generation_options"]["dpi"], 250)

    def test_regenerate_default_no_send_does_not_touch_senders(self):
        temp_dir = self.make_temp_dir()

        with patch.object(
            text_laser_task_tool.text_image_gcode_tool,
            "create_text_image_gcode",
            side_effect=self.fake_text_gcode,
        ):
            generated = text_laser_task_tool.generate_text_laser_task(
                text="佳佳",
                material="椴木",
                thickness_mm=3,
                tasks_dir=temp_dir,
            )
        task_id = generated["result"]["task_id"]
        feedback = text_laser_task_tool.refine_laser_params_from_feedback(
            feedback_text="太焦了",
            task_id=task_id,
            tasks_dir=temp_dir,
        )
        self.assertTrue(feedback["success"], feedback)

        with (
            patch.object(
                text_laser_task_tool.text_image_gcode_tool,
                "create_text_image_gcode",
                side_effect=self.fake_text_gcode,
            ),
            patch.object(text_laser_task_tool.laser_execution, "send_file") as send_mock,
            patch.object(text_laser_task_tool.laser_grbl_tool, "_load_serial") as load_serial_mock,
            patch.object(text_laser_task_tool.laser_network_grbl_tool, "query_telnet_command") as telnet_probe_mock,
        ):
            result = text_laser_task_tool.regenerate_text_laser_task(
                task_id=task_id,
                strategy="recommended",
                tasks_dir=temp_dir,
            )

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"]["attempt_no"], 2)
        self.assertEqual(result["result"]["send_result"]["reason"], "not_requested")
        self.assertTrue(result["result"]["confirmation_required"])
        send_mock.assert_not_called()
        load_serial_mock.assert_not_called()
        telnet_probe_mock.assert_not_called()

    def test_regenerate_unconfirmed_send_request_does_not_touch_senders(self):
        temp_dir = self.make_temp_dir()

        with patch.object(
            text_laser_task_tool.text_image_gcode_tool,
            "create_text_image_gcode",
            side_effect=self.fake_text_gcode,
        ):
            generated = text_laser_task_tool.generate_text_laser_task(
                text="佳佳",
                material="椴木",
                thickness_mm=3,
                tasks_dir=temp_dir,
            )
        task_id = generated["result"]["task_id"]
        feedback = text_laser_task_tool.refine_laser_params_from_feedback(
            feedback_text="太焦了",
            task_id=task_id,
            tasks_dir=temp_dir,
        )
        self.assertTrue(feedback["success"], feedback)

        with (
            patch.object(
                text_laser_task_tool.text_image_gcode_tool,
                "create_text_image_gcode",
                side_effect=self.fake_text_gcode,
            ),
            patch.object(text_laser_task_tool.laser_execution, "send_file") as send_mock,
        ):
            result = text_laser_task_tool.regenerate_text_laser_task(
                task_id=task_id,
                strategy="recommended",
                send_after_generate=True,
                confirmed=False,
                connection_mode="network",
                network_host="laser.local",
                tasks_dir=temp_dir,
            )

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"]["send_result"]["reason"], "confirmation_required")
        self.assertTrue(result["result"]["confirmation_required"])
        send_mock.assert_not_called()

    def test_regenerate_changed_engraving_mode_reloads_strategy_params(self):
        temp_dir = self.make_temp_dir()

        with patch.object(
            text_laser_task_tool.text_image_gcode_tool,
            "create_text_image_gcode",
            side_effect=self.fake_text_gcode,
        ):
            generated = text_laser_task_tool.generate_text_laser_task(
                text="佳佳",
                material="椴木",
                thickness_mm=3,
                tasks_dir=temp_dir,
            )
        task_id = generated["result"]["task_id"]
        feedback = text_laser_task_tool.refine_laser_params_from_feedback(
            feedback_text="太焦了",
            task_id=task_id,
            tasks_dir=temp_dir,
        )
        self.assertTrue(feedback["success"], feedback)

        outline_recommendation = {
            "success": True,
            "result": {
                "material": "椴木",
                "requested_material": "椴木",
                "thickness_mm": 3.0,
                "matched_thickness_mm": 3.0,
                "match": "exact",
                "laser_mode": "engrave",
                "engraving_mode": "outline",
                "params": {
                    "laser_min_power": 0,
                    "laser_max_power": 700,
                    "feed_rate": 900,
                    "travel_rate": 3000,
                    "pixel_size_mm": 0.1,
                    "threshold": 128,
                    "passes": 1,
                },
                "warnings": [],
            },
        }
        with (
            patch.object(
                text_laser_task_tool.laser_material_calibration_tool,
                "recommend_laser_params",
                return_value=outline_recommendation,
            ) as recommend_mock,
            patch.object(
                text_laser_task_tool.text_image_gcode_tool,
                "create_text_image_gcode",
                side_effect=self.fake_text_gcode,
            ) as create_mock,
        ):
            result = text_laser_task_tool.regenerate_text_laser_task(
                task_id=task_id,
                strategy="recommended",
                engraving_mode="outline",
                tasks_dir=temp_dir,
            )

        self.assertTrue(result["success"], result)
        recommend_mock.assert_called_once_with("椴木", 3.0, "engrave", "outline")
        self.assertEqual(result["result"]["params"]["laser_max_power"], 560)
        self.assertEqual(create_mock.call_args.kwargs["laser_max_power"], 560)
        self.assertEqual(create_mock.call_args.kwargs["feed_rate"], 900)
        self.assertEqual(create_mock.call_args.kwargs["engraving_mode"], "outline")

        saved, error = text_laser_task_tool._load_text_task(task_id, tasks_dir=temp_dir)
        self.assertIsNone(error)
        self.assertEqual(saved["generation_options"]["engraving_mode"], "outline")

    def test_regenerate_manual_power_percent_and_conflict_validation(self):
        temp_dir = self.make_temp_dir()

        with patch.object(
            text_laser_task_tool.text_image_gcode_tool,
            "create_text_image_gcode",
            side_effect=self.fake_text_gcode,
        ):
            generated = text_laser_task_tool.generate_text_laser_task(
                text="佳佳",
                material="椴木",
                thickness_mm=3,
                tasks_dir=temp_dir,
            )
        task_id = generated["result"]["task_id"]

        with patch.object(
            text_laser_task_tool.text_image_gcode_tool,
            "create_text_image_gcode",
            side_effect=self.fake_text_gcode,
        ):
            manual = text_laser_task_tool.regenerate_text_laser_task(
                task_id=task_id,
                strategy="manual_override",
                power_percent=50,
                tasks_dir=temp_dir,
            )
        self.assertTrue(manual["success"], manual)
        self.assertEqual(manual["result"]["params"]["laser_max_power"], 500)
        self.assertEqual(manual["result"]["params"]["power_percent"], 50.0)

        with patch.object(text_laser_task_tool.text_image_gcode_tool, "create_text_image_gcode") as create_mock:
            conflict = text_laser_task_tool.regenerate_text_laser_task(
                task_id=task_id,
                strategy="manual_override",
                power_percent=50,
                laser_max_power=300,
                tasks_dir=temp_dir,
            )

        self.assertFalse(conflict["success"], conflict)
        self.assertIn("冲突", conflict["result"])
        create_mock.assert_not_called()

    def test_regenerate_named_strategy_uses_latest_feedback_issue_bounds(self):
        temp_dir = self.make_temp_dir()
        params_file = self.write_test_materials(temp_dir)

        with patch.object(
            text_laser_task_tool.text_image_gcode_tool,
            "create_text_image_gcode",
            side_effect=self.fake_text_gcode,
        ):
            generated = text_laser_task_tool.generate_text_laser_task(
                text="佳佳",
                material="椴木",
                thickness_mm=3,
                tasks_dir=temp_dir,
                params_file=params_file,
            )
        task_id = generated["result"]["task_id"]
        feedback = text_laser_task_tool.refine_laser_params_from_feedback(
            feedback_text="边缘毛糙",
            task_id=task_id,
            tasks_dir=temp_dir,
        )
        self.assertTrue(feedback["success"], feedback)

        with patch.object(
            text_laser_task_tool.text_image_gcode_tool,
            "create_text_image_gcode",
            side_effect=self.fake_text_gcode,
        ):
            result = text_laser_task_tool.regenerate_text_laser_task(
                task_id=task_id,
                strategy="lower_power",
                tasks_dir=temp_dir,
            )

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"]["params"]["laser_max_power"], 378)

    def test_regenerate_default_serial_mode_delegates_to_serial_sender(self):
        temp_dir = self.make_temp_dir()

        with patch.object(
            text_laser_task_tool.text_image_gcode_tool,
            "create_text_image_gcode",
            side_effect=self.fake_text_gcode,
        ):
            generated = text_laser_task_tool.generate_text_laser_task(
                text="佳佳",
                material="椴木",
                thickness_mm=3,
                tasks_dir=temp_dir,
            )
        task_id = generated["result"]["task_id"]
        feedback = text_laser_task_tool.refine_laser_params_from_feedback(
            feedback_text="太焦了",
            task_id=task_id,
            tasks_dir=temp_dir,
        )
        self.assertTrue(feedback["success"], feedback)

        with (
            patch.object(text_laser_task_tool.laser_execution, "DEFAULT_CONNECTION_MODE", "serial"),
            patch.object(
                text_laser_task_tool.text_image_gcode_tool,
                "create_text_image_gcode",
                side_effect=self.fake_text_gcode,
            ),
            patch.object(
                text_laser_task_tool.laser_execution,
                "send_file",
                return_value={"success": True, "job_id": "regen-serial-job"},
            ) as serial_mock,
        ):
            result = text_laser_task_tool.regenerate_text_laser_task(
                task_id=task_id,
                strategy="recommended",
                send_after_generate=True,
                confirmed=True,
                tasks_dir=temp_dir,
            )

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"]["send_result"]["job_id"], "regen-serial-job")
        serial_mock.assert_called_once()
        self.assertEqual(serial_mock.call_args.args[1], "serial")

    def test_send_prepared_gcode_file_delegates_full_prepared_result(self):
        prepared_dir = self.make_temp_dir()
        prepared_file = os.path.join(prepared_dir, "ready.gcode")
        self.write_file(prepared_file)

        with patch.object(
            text_laser_task_tool.laser_execution,
            "send_file",
            return_value={"success": True, "job_id": "text-prep-job"},
        ) as send_mock:
            result = text_laser_task_tool._send_prepared_gcode_file(
                prepared_file,
                send_after_generate=True,
                confirmed=True,
                connection_mode="serial",
                port="COM5",
                baudrate=57600,
                wait_for_response=False,
                run_in_background=True,
            )

        self.assertTrue(result["success"], result)
        send_mock.assert_called_once()
        prepared = send_mock.call_args.args[0]
        self.assertEqual(prepared["gcode_file"], prepared_file)
        self.assertIn("converted", prepared)
        self.assertFalse(prepared["converted"])
        self.assertEqual(prepared.get("source"), "prepared_gcode_file")
        self.assertEqual(send_mock.call_args.args[1], "serial")
        self.assertTrue(send_mock.call_args.kwargs.get("confirmed"))
        self.assertEqual(send_mock.call_args.kwargs.get("port"), "COM5")
        self.assertEqual(send_mock.call_args.kwargs.get("baudrate"), 57600)


if __name__ == "__main__":
    unittest.main()
