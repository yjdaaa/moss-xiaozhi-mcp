import json
import os
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

from tools import laser_workflow_tool


class FakeMcp:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorator(func):
            self.tools[func.__name__] = func
            return func

        return decorator


class LaserWorkflowToolTests(unittest.TestCase):
    def make_temp_dir(self):
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".tmp-test"))
        os.makedirs(base_dir, exist_ok=True)
        temp_dir = os.path.join(base_dir, f"case-{uuid.uuid4().hex}")
        os.makedirs(temp_dir, exist_ok=False)
        self.addCleanup(shutil.rmtree, temp_dir, ignore_errors=True)
        return temp_dir

    def fake_text_preview(self, **kwargs):
        gcode_file = os.path.join(self.temp_dir, "preview.gcode")
        Path(gcode_file).write_text("G21\nG1 X1 F100\nM5\n", encoding="utf-8")
        return {
            "success": True,
            "result": {
                "task_id": "task-1",
                "task_file": os.path.join(self.temp_dir, "task.json"),
                "attempt_no": 1,
                "image_file": os.path.join(self.temp_dir, "preview.png"),
                "gcode_file": gcode_file,
                "params": {
                    "laser_max_power": 420,
                    "feed_rate": kwargs.get("feed_rate") or 1800,
                    "passes": kwargs.get("passes") or 1,
                    "power_percent": kwargs.get("power_percent") or 60,
                    "travel_rate": kwargs.get("travel_rate") or 3000,
                },
                "recommendation": {
                    "material": kwargs.get("material") or "椴木",
                    "requested_material": kwargs.get("material") or "椴木",
                    "thickness_mm": float(kwargs.get("thickness_mm") or 3),
                    "matched_thickness_mm": float(kwargs.get("thickness_mm") or 3),
                    "match": "exact",
                    "laser_mode": kwargs.get("laser_mode") or "engrave",
                    "warnings": [],
                    "can_send": True,
                    "send_blocked_reason": "",
                },
                "passes_applied": kwargs.get("passes") or 1,
                "time_estimate": {"estimated_seconds": 90},
                "speech": "文件已生成，预计需要 1 分 30 秒。",
            },
        }

    def load_workflow(self, workflow_id):
        workflow, error = laser_workflow_tool._load_workflow(workflow_id, workflows_dir=self.workflows_dir)
        self.assertIsNone(error)
        return workflow

    def make_sending_workflow(self, transport):
        workflow = laser_workflow_tool._new_workflow("text", {"text": "佳佳", "material": "椴木", "thickness_mm": 3})
        workflow["status"] = "sending"
        workflow["artifacts"] = {"task_id": "task-1", "gcode_file": os.path.join(self.temp_dir, "preview.gcode")}
        workflow["runtime_job"] = {
            "job_id": f"{transport}-job",
            "transport": transport,
            "status": "pending",
        }
        laser_workflow_tool._save_workflow(workflow, workflows_dir=self.workflows_dir)
        return workflow

    def setUp(self):
        self.temp_dir = self.make_temp_dir()
        self.workflows_dir = os.path.join(self.temp_dir, "workflows")

    def test_register_tool_exposes_laser_workflow_tool(self):
        fake = FakeMcp()

        laser_workflow_tool.register_tool(fake)

        self.assertIn("laser_workflow_tool", fake.tools)
        self.assertEqual(len(fake.tools), 1)

    def test_text_preview_creates_workflow_without_sender_access(self):
        result = laser_workflow_tool.run_laser_workflow_action(
            action="preview",
            source_type="text",
            text="佳佳",
            material="椴木",
            thickness_mm="3",
            workflows_dir=self.workflows_dir,
            text_generator=self.fake_text_preview,
        )

        self.assertTrue(result["success"], result)
        payload = result["result"]
        self.assertEqual(payload["status"], "preview_ready")
        self.assertEqual(payload["source_type"], "text")
        self.assertEqual(payload["task_id"], "task-1")
        self.assertEqual(payload["artifacts"]["task_id"], "task-1")
        self.assertIn("confirm_send", payload["next_actions"])
        workflow = self.load_workflow(payload["workflow_id"])
        self.assertEqual(
            laser_workflow_tool._norm_path(workflow["artifacts"]["gcode_file"]),
            laser_workflow_tool._norm_path(os.path.join(self.temp_dir, "preview.gcode")),
        )

    def test_text_nearest_cut_preview_is_not_sendable_and_confirm_never_calls_sender(self):
        def generator(**kwargs):
            result = self.fake_text_preview(**kwargs)
            result["result"]["recommendation"] = {
                "material": "椴木",
                "requested_material": "椴木",
                "thickness_mm": 8.0,
                "matched_thickness_mm": 3.0,
                "match": "nearest_thickness",
                "laser_mode": "cut",
                "warnings": ["没有 8mm 的精确切割参数，不能使用 3mm 参数发送。"],
                "can_send": False,
                "send_blocked_reason": "切割必须精确匹配材料厚度；椴木 8mm 暂无切割参数。",
            }
            return result

        preview = laser_workflow_tool.run_laser_workflow_action(
            action="preview",
            source_type="text",
            text="111",
            material="椴木",
            thickness_mm=8,
            laser_mode="cut",
            workflows_dir=self.workflows_dir,
            text_generator=generator,
        )

        self.assertTrue(preview["success"], preview)
        payload = preview["result"]
        self.assertEqual(payload["status"], "preview_ready")
        self.assertNotIn("confirm_send", payload["next_actions"])
        snapshot = payload["workflow"]["confirmation_snapshot"]
        self.assertEqual(snapshot["matched_thickness_mm"], 3.0)
        self.assertEqual(snapshot["match"], "nearest_thickness")
        self.assertFalse(snapshot["can_send"])
        self.assertTrue(snapshot["warnings"])

        sender = Mock()
        sent = laser_workflow_tool.run_laser_workflow_action(
            action="confirm_send",
            workflow_id=payload["workflow_id"],
            confirmed=True,
            workflows_dir=self.workflows_dir,
            execution_sender=sender,
            connection_mode="network",
            network_host="laser.local",
        )

        self.assertFalse(sent["success"], sent)
        self.assertEqual(sent.get("error_code"), "not_sendable")
        sender.assert_not_called()

    def test_text_preview_preserves_generator_speech_for_web_status(self):
        fallback_speech = "字体矢量轮廓不可用，已自动改用图片轮廓方式生成。文件已生成，预计需要 1 分 30 秒。"

        def generator(**kwargs):
            result = self.fake_text_preview(**kwargs)
            result["result"]["speech"] = fallback_speech
            return result

        result = laser_workflow_tool.run_laser_workflow_action(
            action="preview",
            source_type="text",
            text="佳佳",
            material="椴木",
            thickness_mm=3,
            workflows_dir=self.workflows_dir,
            text_generator=generator,
        )

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"]["speech"], fallback_speech)
        self.assertNotIn("请人工核对", result["result"]["speech"])
        self.assertNotIn("系统没有自动识别真实材料", result["result"]["speech"])
        workflow = self.load_workflow(result["result"]["workflow_id"])
        self.assertEqual(workflow["speech"], fallback_speech)
        self.assertNotIn("请人工核对", workflow["speech"])

    def test_text_preview_can_disable_prepared_gcode_reuse(self):
        calls = {}

        def generator(**kwargs):
            calls.update(kwargs)
            return self.fake_text_preview(**kwargs)

        result = laser_workflow_tool.run_laser_workflow_action(
            action="preview",
            source_type="text",
            text="佳佳",
            material="椴木",
            thickness_mm=3,
            workflows_dir=self.workflows_dir,
            text_generator=generator,
            reuse_prepared_gcode=False,
        )

        self.assertTrue(result["success"], result)
        self.assertFalse(calls["bidirectional"])
        self.assertFalse(calls["reuse_prepared_gcode"])
        workflow = self.load_workflow(result["result"]["workflow_id"])
        self.assertFalse(workflow["input"]["reuse_prepared_gcode"])

    def test_confirm_send_requires_confirmation_before_sender(self):
        preview = laser_workflow_tool.run_laser_workflow_action(
            action="preview",
            source_type="text",
            text="佳佳",
            material="椴木",
            thickness_mm=3,
            workflows_dir=self.workflows_dir,
            text_generator=self.fake_text_preview,
        )
        sender = Mock()

        result = laser_workflow_tool.run_laser_workflow_action(
            action="confirm_send",
            workflow_id=preview["result"]["workflow_id"],
            confirmed=False,
            workflows_dir=self.workflows_dir,
            tuned_job_sender=sender,
        )

        self.assertFalse(result["success"])
        sender.assert_not_called()
        self.assertIn("confirmed=true", result["speech"])

    def test_confirm_send_binds_sender_job_id_and_status_completes(self):
        preview = laser_workflow_tool.run_laser_workflow_action(
            action="preview",
            source_type="text",
            text="佳佳",
            material="椴木",
            thickness_mm=3,
            workflows_dir=self.workflows_dir,
            text_generator=self.fake_text_preview,
        )

        def fake_sender(prepared_result, mode, **kwargs):
            self.assertEqual(mode, "network")
            self.assertTrue(kwargs.get("confirmed"))
            self.assertIn("expected_gcode_sha256", prepared_result)
            self.assertEqual(
                laser_workflow_tool._norm_path(prepared_result["gcode_file"]),
                laser_workflow_tool._norm_path(os.path.join(self.temp_dir, "preview.gcode")),
            )
            return {"success": True, "result": "queued", "job_id": "job-1", "status": "pending"}

        sent = laser_workflow_tool.run_laser_workflow_action(
            action="confirm_send",
            workflow_id=preview["result"]["workflow_id"],
            confirmed=True,
            workflows_dir=self.workflows_dir,
            execution_sender=fake_sender,
            connection_mode="network",
            network_host="laser.local",
        )

        self.assertTrue(sent["success"], sent)
        self.assertEqual(sent["result"]["job_id"], "job-1")
        self.assertEqual(sent["result"]["runtime_job"]["transport"], "network")
        self.assertEqual(sent["result"]["status"], "sending")

        status = laser_workflow_tool.run_laser_workflow_action(
            action="status",
            workflow_id=preview["result"]["workflow_id"],
            workflows_dir=self.workflows_dir,
            status_getter=lambda job_id: {
                "success": True,
                "result": {"job_id": job_id, "status": "completed", "finished_at": 123.0},
            },
        )

        self.assertTrue(status["success"], status)
        self.assertEqual(status["result"]["status"], "completed")
        self.assertEqual(status["result"]["runtime_job"]["status"], "completed")

    def test_confirm_send_records_complete_confirmation_snapshot(self):
        preview = laser_workflow_tool.run_laser_workflow_action(
            action="preview",
            source_type="text",
            text="佳佳",
            material="椴木",
            thickness_mm=3,
            laser_mode="engrave",
            engraving_mode="raster",
            power_percent=60,
            feed_rate=1600,
            passes=2,
            manual_params_confirmed=True,
            workflows_dir=self.workflows_dir,
            text_generator=self.fake_text_preview,
        )

        sent = laser_workflow_tool.run_laser_workflow_action(
            action="confirm_send",
            workflow_id=preview["result"]["workflow_id"],
            confirmed=True,
            workflows_dir=self.workflows_dir,
            execution_sender=lambda prepared, mode, **kwargs: {
                "success": True,
                "job_id": "job-1",
                "status": "pending",
            },
            connection_mode="network",
            network_host="laser.local",
            network_transport="telnet",
            network_http_port=8080,
            network_telnet_port=2323,
            run_in_background=True,
            wait_for_response=False,
        )

        self.assertTrue(sent["success"], sent)
        preview_workflow = self.load_workflow(preview["result"]["workflow_id"])
        # Snapshot is created at preview-ready; connection fill is applied on successful confirm.
        self.assertIn("gcode_sha256", preview_workflow["confirmation_snapshot"])
        snapshot = sent["result"]["workflow"]["confirmation_snapshot"]
        self.assertEqual(snapshot["source_type"], "text")
        self.assertEqual(
            laser_workflow_tool._norm_path(snapshot["gcode_file"]),
            laser_workflow_tool._norm_path(os.path.join(self.temp_dir, "preview.gcode")),
        )
        self.assertTrue(snapshot.get("gcode_sha256"))
        self.assertEqual(snapshot["material"], "椴木")
        self.assertEqual(float(snapshot["thickness_mm"]), 3.0)
        self.assertEqual(snapshot["laser_mode"], "engrave")
        self.assertEqual(snapshot["engraving_mode"], "raster")
        self.assertEqual(float(snapshot["power_percent"]), 60.0)
        self.assertEqual(float(snapshot["laser_max_power"]), 420.0)
        self.assertEqual(float(snapshot["feed_rate"]), 1600.0)
        self.assertEqual(float(snapshot["passes"]), 2.0)
        self.assertEqual(snapshot["params"]["laser_max_power"], 420)
        self.assertEqual(snapshot["connection_mode"], "network")
        self.assertEqual(snapshot["connection"]["network_host"], "laser.local")
        self.assertEqual(snapshot["connection"]["network_transport"], "telnet")
        self.assertEqual(int(snapshot["connection"]["network_http_port"]), 8080)
        self.assertEqual(int(snapshot["connection"]["network_telnet_port"]), 2323)
        self.assertFalse(snapshot["connection"]["wait_for_response"])

    def test_cancel_uses_bound_job_and_clarifies_not_emergency_stop(self):
        preview = laser_workflow_tool.run_laser_workflow_action(
            action="preview",
            source_type="text",
            text="佳佳",
            material="椴木",
            thickness_mm=3,
            workflows_dir=self.workflows_dir,
            text_generator=self.fake_text_preview,
        )
        sent = laser_workflow_tool.run_laser_workflow_action(
            action="confirm_send",
            workflow_id=preview["result"]["workflow_id"],
            confirmed=True,
            workflows_dir=self.workflows_dir,
            tuned_job_sender=lambda prepared_result, mode, **kwargs: {
                "success": True,
                "result": "queued",
                "job_id": "job-1",
                "status": "pending",
            },
            connection_mode="network",
        )
        self.assertTrue(sent["success"], sent)
        canceler = Mock(return_value={"success": True, "result": {"status": "cancelled"}})

        cancelled = laser_workflow_tool.run_laser_workflow_action(
            action="cancel",
            workflow_id=preview["result"]["workflow_id"],
            workflows_dir=self.workflows_dir,
            canceler=canceler,
        )

        self.assertTrue(cancelled["success"], cancelled)
        canceler.assert_called_once_with("job-1", "network")
        self.assertEqual(cancelled["result"]["status"], "cancelled")
        self.assertIn("不等同于物理急停", cancelled["result"]["speech"])

    def test_feedback_without_workflow_id_routes_latest_text_task(self):
        preview = laser_workflow_tool.run_laser_workflow_action(
            action="preview",
            source_type="text",
            text="佳佳",
            material="椴木",
            thickness_mm=3,
            workflows_dir=self.workflows_dir,
            text_generator=self.fake_text_preview,
        )
        workflow = self.load_workflow(preview["result"]["workflow_id"])
        workflow["status"] = "completed"
        laser_workflow_tool._save_workflow(workflow, workflows_dir=self.workflows_dir)

        helper = Mock(
            return_value={
                "success": True,
                "result": {
                    "matched_issue": "burnt",
                    "recommended_strategy": "lower_power",
                },
            }
        )
        result = laser_workflow_tool.run_laser_workflow_action(
            action="feedback",
            feedback_text="太焦了",
            workflows_dir=self.workflows_dir,
            feedback_helper=helper,
        )

        self.assertTrue(result["success"], result)
        helper.assert_called_once_with(feedback_text="太焦了", task_id="task-1")
        self.assertEqual(result["result"]["status"], "feedback_recorded")
        self.assertEqual(result["result"]["feedback_event"]["recommended_strategy"], "lower_power")

    def test_regenerate_text_workflow_returns_to_preview_ready(self):
        preview = laser_workflow_tool.run_laser_workflow_action(
            action="preview",
            source_type="text",
            text="佳佳",
            material="椴木",
            thickness_mm=3,
            workflows_dir=self.workflows_dir,
            text_generator=self.fake_text_preview,
        )
        workflow = self.load_workflow(preview["result"]["workflow_id"])
        workflow["status"] = "completed"
        laser_workflow_tool._save_workflow(workflow, workflows_dir=self.workflows_dir)
        new_gcode = os.path.join(self.temp_dir, "attempt_2.gcode")
        Path(new_gcode).write_text("G21\nG1 X2 F200\nM5\n", encoding="utf-8")

        def regenerator(**kwargs):
            return {
                "success": True,
                "result": {
                    "task_id": "task-1",
                    "attempt_no": 2,
                    "image_file": os.path.join(self.temp_dir, "attempt_2.png"),
                    "gcode_file": new_gcode,
                    "time_estimate": {"estimated_seconds": 80},
                    "params": {
                        "power_percent": 40,
                        "feed_rate": 2000,
                        "passes": 1,
                        "travel_rate": 3000,
                    },
                    "passes_applied": 1,
                    "recommendation": {
                        "material": "椴木",
                        "requested_material": "椴木",
                        "thickness_mm": 3.0,
                        "matched_thickness_mm": 3.0,
                        "match": "exact",
                        "laser_mode": "engrave",
                        "warnings": [],
                        "can_send": True,
                        "send_blocked_reason": "",
                    },
                },
            }

        result = laser_workflow_tool.run_laser_workflow_action(
            action="regenerate",
            workflow_id=preview["result"]["workflow_id"],
            strategy="lower_power",
            workflows_dir=self.workflows_dir,
            regenerator=regenerator,
        )

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"]["status"], "preview_ready")
        self.assertEqual(result["result"]["attempt_no"], 2)
        self.assertEqual(
            laser_workflow_tool._norm_path(result["result"]["artifacts"]["gcode_file"]),
            laser_workflow_tool._norm_path(new_gcode),
        )
        self.assertTrue(result["result"]["workflow"]["confirmation_snapshot"].get("gcode_sha256"))

    def test_regenerate_with_feedback_text_records_feedback_first(self):
        preview = laser_workflow_tool.run_laser_workflow_action(
            action="preview",
            source_type="text",
            text="佳佳",
            material="椴木",
            thickness_mm=3,
            workflows_dir=self.workflows_dir,
            text_generator=self.fake_text_preview,
        )
        calls = []

        def feedback_helper(feedback_text, task_id):
            calls.append(("feedback", feedback_text, task_id))
            return {
                "success": True,
                "result": {
                    "matched_issue": "burnt",
                    "recommended_strategy": "lower_power",
                },
            }

        def regenerator(**kwargs):
            calls.append(("regenerate", kwargs.get("strategy"), kwargs.get("feedback_text")))
            new_gcode = os.path.join(self.temp_dir, "attempt_2.gcode")
            Path(new_gcode).write_text("G21\nG1 X2 F200\nM5\n", encoding="utf-8")
            return {
                "success": True,
                "result": {
                    "task_id": "task-1",
                    "attempt_no": 2,
                    "image_file": os.path.join(self.temp_dir, "attempt_2.png"),
                    "gcode_file": new_gcode,
                    "params": {
                        "power_percent": 40,
                        "feed_rate": 2000,
                        "passes": 1,
                        "travel_rate": 3000,
                    },
                    "passes_applied": 1,
                    "recommendation": {
                        "material": "椴木",
                        "requested_material": "椴木",
                        "thickness_mm": 3.0,
                        "matched_thickness_mm": 3.0,
                        "match": "exact",
                        "laser_mode": "engrave",
                        "warnings": [],
                        "can_send": True,
                        "send_blocked_reason": "",
                    },
                },
            }

        result = laser_workflow_tool.run_laser_workflow_action(
            action="regenerate",
            workflow_id=preview["result"]["workflow_id"],
            feedback_text="太焦了",
            workflows_dir=self.workflows_dir,
            feedback_helper=feedback_helper,
            regenerator=regenerator,
        )

        self.assertTrue(result["success"], result)
        self.assertEqual(calls[0], ("feedback", "太焦了", "task-1"))
        self.assertEqual(calls[1], ("regenerate", "recommended", "太焦了"))
        self.assertEqual(result["result"]["status"], "preview_ready")
        self.assertIn("feedback_result", result["result"])
        workflow = self.load_workflow(preview["result"]["workflow_id"])
        self.assertEqual(workflow["feedback_events"][0]["recommended_strategy"], "lower_power")

    def test_regenerate_with_unknown_feedback_does_not_call_regenerator(self):
        preview = laser_workflow_tool.run_laser_workflow_action(
            action="preview",
            source_type="text",
            text="佳佳",
            material="椴木",
            thickness_mm=3,
            workflows_dir=self.workflows_dir,
            text_generator=self.fake_text_preview,
        )
        regenerator = Mock()

        result = laser_workflow_tool.run_laser_workflow_action(
            action="regenerate",
            workflow_id=preview["result"]["workflow_id"],
            feedback_text="效果有点奇怪",
            workflows_dir=self.workflows_dir,
            feedback_helper=lambda feedback_text, task_id: {
                "success": True,
                "result": {
                    "matched_issue": "unknown",
                    "recommended_strategy": None,
                },
            },
            regenerator=regenerator,
        )

        self.assertTrue(result["success"], result)
        regenerator.assert_not_called()
        self.assertEqual(result["result"]["status"], "feedback_recorded")
        self.assertIsNone(result["result"]["feedback_event"]["recommended_strategy"])
        workflow = self.load_workflow(preview["result"]["workflow_id"])
        self.assertEqual(workflow["status"], "feedback_recorded")

    def test_prepared_gcode_preview_and_confirm_use_prepared_sender(self):
        gcode_file = os.path.join(self.temp_dir, "prepared.gcode")
        Path(gcode_file).write_text("G21\nG1 X1 F100\n", encoding="utf-8")
        preview = laser_workflow_tool.run_laser_workflow_action(
            action="preview",
            source_type="prepared_gcode",
            gcode_file=gcode_file,
            workflows_dir=self.workflows_dir,
        )
        self.assertTrue(preview["success"], preview)
        self.assertEqual(preview["result"]["status"], "preview_ready")
        self.assertEqual(preview["result"]["artifacts"]["prepared"]["params_source"], "prebaked_file")

        sender = Mock(return_value={"success": True, "job_id": "job-p", "status": "pending"})
        sent = laser_workflow_tool.run_laser_workflow_action(
            action="confirm_send",
            workflow_id=preview["result"]["workflow_id"],
            confirmed=True,
            workflows_dir=self.workflows_dir,
            prepared_sender=sender,
            connection_mode="serial",
        )

        self.assertTrue(sent["success"], sent)
        sender.assert_called_once()
        prepared_arg = sender.call_args.args[0]
        self.assertEqual(
            laser_workflow_tool._norm_path(prepared_arg["gcode_file"]),
            laser_workflow_tool._norm_path(gcode_file),
        )
        self.assertFalse(prepared_arg["converted"])
        self.assertEqual(prepared_arg.get("source"), "prepared_gcode_file")
        self.assertEqual(sent["result"]["job_id"], "job-p")

    def test_prepared_gcode_default_confirm_uses_laser_execution_not_text_helper(self):
        gcode_file = os.path.join(self.temp_dir, "prepared.gcode")
        Path(gcode_file).write_text("G21\nG1 X1 F100\n", encoding="utf-8")
        preview = laser_workflow_tool.run_laser_workflow_action(
            action="preview",
            source_type="prepared_gcode",
            gcode_file=gcode_file,
            workflows_dir=self.workflows_dir,
        )
        self.assertTrue(preview["success"], preview)

        with (
            patch.object(
                laser_workflow_tool.laser_execution,
                "send_file",
                return_value={"success": True, "job_id": "job-exec", "status": "pending"},
            ) as send_mock,
            patch.object(
                laser_workflow_tool.text_laser_task_tool,
                "_send_prepared_gcode_file",
            ) as text_sender_mock,
        ):
            sent = laser_workflow_tool.run_laser_workflow_action(
                action="confirm_send",
                workflow_id=preview["result"]["workflow_id"],
                confirmed=True,
                workflows_dir=self.workflows_dir,
                connection_mode="serial",
                port="COM3",
            )

        self.assertTrue(sent["success"], sent)
        text_sender_mock.assert_not_called()
        send_mock.assert_called_once()
        prepared_arg = send_mock.call_args.args[0]
        self.assertEqual(
            laser_workflow_tool._norm_path(prepared_arg["gcode_file"]),
            laser_workflow_tool._norm_path(gcode_file),
        )
        self.assertFalse(prepared_arg["converted"])
        self.assertEqual(prepared_arg.get("source"), "prepared_gcode_file")
        self.assertEqual(send_mock.call_args.args[1], "serial")
        self.assertTrue(send_mock.call_args.kwargs.get("confirmed"))
        self.assertEqual(sent["result"]["job_id"], "job-exec")

    def test_prepared_gcode_confirmation_snapshot_marks_unknown_params(self):
        gcode_file = os.path.join(self.temp_dir, "prepared.gcode")
        Path(gcode_file).write_text("G21\nG1 X1 F100\n", encoding="utf-8")
        preview = laser_workflow_tool.run_laser_workflow_action(
            action="preview",
            source_type="prepared_gcode",
            gcode_file=gcode_file,
            workflows_dir=self.workflows_dir,
        )

        sent = laser_workflow_tool.run_laser_workflow_action(
            action="confirm_send",
            workflow_id=preview["result"]["workflow_id"],
            confirmed=True,
            workflows_dir=self.workflows_dir,
            prepared_sender=lambda *args, **kwargs: {"success": True, "job_id": "job-p", "status": "pending"},
            connection_mode="serial",
            port="COM7",
            baudrate=115200,
        )

        self.assertTrue(sent["success"], sent)
        snapshot = sent["result"]["workflow"]["confirmation_snapshot"]
        self.assertEqual(snapshot["params_source"], "prebaked_file")
        self.assertEqual(snapshot["power_percent"], "unknown")
        self.assertEqual(snapshot["laser_min_power"], "unknown")
        self.assertEqual(snapshot["laser_max_power"], "unknown")
        self.assertEqual(snapshot["feed_rate"], "unknown")
        self.assertEqual(snapshot["passes"], "unknown")
        self.assertEqual(snapshot["connection_mode"], "serial")
        self.assertEqual(snapshot["connection"]["serial_port"], "COM7")
        self.assertEqual(snapshot["connection"]["baudrate"], 115200)

    def test_failure_speech_hides_paths_hosts_ips_and_secrets(self):
        workflow = laser_workflow_tool._new_workflow("text", {"text": "佳佳"})

        result = laser_workflow_tool._set_failed(
            workflow,
            r"发送失败 C:\Users\me\secret.gcode host=laser.local token=abc 192.168.1.5 laser.local",
            workflows_dir=self.workflows_dir,
        )

        speech = result["speech"]
        self.assertNotIn(r"C:\Users", speech)
        self.assertNotIn("secret.gcode", speech)
        self.assertNotIn("laser.local", speech)
        self.assertNotIn("abc", speech)
        self.assertNotIn("192.168.1.5", speech)
        self.assertIn("本地文件", speech)
        self.assertIn("设备地址", speech)

    def test_status_uses_default_network_job_getter(self):
        workflow = self.make_sending_workflow("network")

        with patch.object(
            laser_workflow_tool.laser_execution,
            "job_status",
            return_value={"success": True, "result": {"job_id": "network-job", "status": "running"}},
        ) as getter:
            result = laser_workflow_tool.run_laser_workflow_action(
                action="status",
                workflow_id=workflow["workflow_id"],
                workflows_dir=self.workflows_dir,
            )

        self.assertTrue(result["success"], result)
        getter.assert_called_once_with("network-job", "network")
        self.assertEqual(result["result"]["runtime_job"]["status"], "running")
        self.assertEqual(result["result"]["status"], "sending")

    def test_status_uses_default_serial_job_getter(self):
        workflow = self.make_sending_workflow("serial")

        with patch.object(
            laser_workflow_tool.laser_execution,
            "job_status",
            return_value={"success": True, "result": {"job_id": "serial-job", "status": "completed", "finished_at": 123.0}},
        ) as getter:
            result = laser_workflow_tool.run_laser_workflow_action(
                action="status",
                workflow_id=workflow["workflow_id"],
                workflows_dir=self.workflows_dir,
            )

        self.assertTrue(result["success"], result)
        getter.assert_called_once_with("serial-job", "serial")
        self.assertEqual(result["result"]["runtime_job"]["status"], "completed")
        self.assertEqual(result["result"]["status"], "completed")

    def test_status_maps_terminal_failed_and_cancelled_runtime_statuses(self):
        for runtime_status in ("failed", "cancelled"):
            with self.subTest(runtime_status=runtime_status):
                workflow = self.make_sending_workflow("network")

                result = laser_workflow_tool.run_laser_workflow_action(
                    action="status",
                    workflow_id=workflow["workflow_id"],
                    workflows_dir=self.workflows_dir,
                    status_getter=lambda job_id, status=runtime_status: {
                        "success": True,
                        "result": {"job_id": job_id, "status": status, "finished_at": 123.0},
                    },
                )

                self.assertTrue(result["success"], result)
                self.assertEqual(result["result"]["runtime_job"]["status"], runtime_status)
                self.assertEqual(result["result"]["status"], runtime_status)

    def test_cancel_uses_default_network_canceler(self):
        workflow = self.make_sending_workflow("network")

        with patch.object(
            laser_workflow_tool.laser_execution,
            "cancel_job",
            return_value={"success": True, "result": {"status": "cancelled"}},
        ) as canceler:
            result = laser_workflow_tool.run_laser_workflow_action(
                action="cancel",
                workflow_id=workflow["workflow_id"],
                workflows_dir=self.workflows_dir,
            )

        self.assertTrue(result["success"], result)
        canceler.assert_called_once_with("network-job", "network")
        self.assertEqual(result["result"]["runtime_job"]["status"], "cancelled")
        self.assertEqual(result["result"]["status"], "cancelled")

    def test_cancel_uses_default_serial_canceler(self):
        workflow = self.make_sending_workflow("serial")

        with patch.object(
            laser_workflow_tool.laser_execution,
            "cancel_job",
            return_value={"success": True, "result": {"status": "cancelled"}},
        ) as canceler:
            result = laser_workflow_tool.run_laser_workflow_action(
                action="cancel",
                workflow_id=workflow["workflow_id"],
                workflows_dir=self.workflows_dir,
            )

        self.assertTrue(result["success"], result)
        canceler.assert_called_once_with("serial-job", "serial")
        self.assertEqual(result["result"]["runtime_job"]["status"], "cancelled")
        self.assertEqual(result["result"]["status"], "cancelled")

    def test_image_preview_generates_summary_without_send_action(self):
        image_file = os.path.join(self.temp_dir, "input.png")
        Path(image_file).write_bytes(b"png")
        summary_path = os.path.join(self.temp_dir, "summary.json")
        gcode_file = os.path.join(self.temp_dir, "image.gcode")
        Path(gcode_file).write_text("G21\nG1 X1\n", encoding="utf-8")
        ai_runner = Mock(
            return_value={
                "success": True,
                "result": {
                    "summary_path": summary_path,
                    "summary": {
                        "contract_version": 1,
                        "recommendation_status": "single_recommendation",
                        "can_send": True,
                        "gcode_path": gcode_file,
                        "processed_preview_path": os.path.join(self.temp_dir, "image.png"),
                        "time_estimate": {"estimated_seconds": 42},
                        "material": "椴木",
                        "thickness_mm": 3,
                        "laser_mode": "M4",
                        "mode": "raster",
                        "task_type": "engrave_photo",
                        "power": 120,
                        "feed_rate": 1500,
                        "passes": 1,
                        "raster": {
                            "pixel_size_mm": 0.3,
                            "scan_direction": "vertical",
                            "dither_algorithm": "threshold",
                            "output_strategy": "scanline",
                        },
                        "auto_raster_profile": {"name": "quality"},
                    },
                },
            }
        )

        default_library = os.path.join(self.temp_dir, ".lasergrbl_materials.json")
        with patch.object(laser_workflow_tool.laser_asset_gcode_tool, "default_material_library_path", return_value=default_library):
            result = laser_workflow_tool.run_laser_workflow_action(
                action="preview",
                source_type="image",
                image_file=image_file,
                material="椴木",
                thickness_mm=3,
                width_mm=35,
                height_mm=12,
                pixel_size_mm=0.3,
                # Explicit scan-step override is a manual processing param.
                manual_params_confirmed=True,
                raster_scan_direction="vertical",
                dither_algorithm="threshold",
                raster_output_strategy="scanline",
                raster_quality_strategy="quality",
                workflows_dir=self.workflows_dir,
                ai_runner=ai_runner,
            )

        self.assertTrue(result["success"], result)
        ai_runner.assert_called_once()
        call_kwargs = ai_runner.call_args.kwargs
        self.assertEqual(call_kwargs["action"], "generate")
        self.assertEqual(call_kwargs["image_file"], image_file)
        self.assertEqual(call_kwargs["material_library"], default_library)
        self.assertEqual(call_kwargs["thickness_mm"], 3.0)
        self.assertEqual(call_kwargs["width_mm"], 35.0)
        self.assertEqual(call_kwargs["height_mm"], 12.0)
        self.assertEqual(call_kwargs["pixel_size_mm"], 0.3)
        self.assertEqual(call_kwargs["raster_scan_direction"], "vertical")
        self.assertEqual(call_kwargs["dither_algorithm"], "threshold")
        self.assertEqual(call_kwargs["raster_output_strategy"], "scanline")
        self.assertEqual(call_kwargs["raster_quality_strategy"], "quality")
        self.assertNotIn("size_mm", call_kwargs)
        self.assertEqual(result["result"]["status"], "preview_ready")
        self.assertEqual(result["result"]["source_type"], "image_summary")
        self.assertEqual(result["result"]["artifacts"]["summary_path"], summary_path)
        self.assertEqual(
            laser_workflow_tool._norm_path(result["result"]["artifacts"]["gcode_file"]),
            laser_workflow_tool._norm_path(os.path.join(self.temp_dir, "image.gcode")),
        )

    def test_image_preview_stays_visible_when_material_requires_sample_test(self):
        image_file = os.path.join(self.temp_dir, "input-review.png")
        Path(image_file).write_bytes(b"png")
        gcode_file = os.path.join(self.temp_dir, "review.gcode")
        Path(gcode_file).write_text("G21\nG1 X1\n", encoding="utf-8")
        processed_preview = os.path.join(self.temp_dir, "review.png")
        Path(processed_preview).write_bytes(b"png")
        ai_runner = Mock(
            return_value={
                "success": True,
                "result": {
                    "summary_path": os.path.join(self.temp_dir, "review-summary.json"),
                    "speech": "文件已生成，但当前材料参数需要先做小样测试。",
                    "summary": {
                        "contract_version": 1,
                        "recommendation_status": "single_recommendation",
                        "can_send": False,
                        "requires_sample_test": True,
                        "next_action": "run_sample_test",
                        "message": "使用参考参数生成文件，建议先做小样测试。",
                        "safety_report": {
                            "can_send": False,
                            "requires_sample_test": True,
                            "next_action": "run_sample_test",
                            "message": "使用参考参数生成文件，建议先做小样测试。",
                        },
                        "gcode_path": gcode_file,
                        "processed_preview_path": processed_preview,
                        "time_estimate": {"estimated_seconds": 42},
                        "material": "亚克力",
                        "thickness_mm": 3,
                        "laser_mode": "M4",
                        "mode": "raster",
                        "task_type": "engrave_photo",
                        "power": 340,
                        "feed_rate": 1600,
                        "passes": 1,
                    },
                },
            }
        )

        result = laser_workflow_tool.run_laser_workflow_action(
            action="preview",
            source_type="image",
            image_file=image_file,
            material="亚克力",
            thickness_mm=3,
            workflows_dir=self.workflows_dir,
            ai_runner=ai_runner,
        )

        self.assertTrue(result["success"], result)
        # Design A: preview succeeds as preview_ready with artifacts; send is gated separately.
        self.assertEqual(result["result"]["status"], "preview_ready")
        self.assertEqual(result["result"]["source_type"], "image_summary")
        self.assertEqual(result["result"]["artifacts"]["gcode_file"], gcode_file)
        self.assertEqual(result["result"]["artifacts"]["image_file"], processed_preview)
        snap = result["result"]["workflow"].get("confirmation_snapshot") or {}
        self.assertTrue(snap.get("gcode_sha256"), result)
        self.assertFalse(snap.get("can_send"), result)
        self.assertNotIn("confirm_send", result["result"]["next_actions"])
        self.assertIn("小样测试", result["result"]["speech"])
        self.assertIn("暂不可发送", result["result"]["speech"])

        sender = Mock()
        sent = laser_workflow_tool.run_laser_workflow_action(
            action="confirm_send",
            workflow_id=result["result"]["workflow_id"],
            confirmed=True,
            workflows_dir=self.workflows_dir,
            execution_sender=sender,
            connection_mode="serial",
            port="COM3",
        )
        self.assertFalse(sent["success"], sent)
        self.assertEqual(sent.get("error_code"), "not_sendable")
        sender.assert_not_called()

    def test_image_preview_prefers_explicit_size_mm_over_dimensions(self):
        image_file = os.path.join(self.temp_dir, "input.png")
        Path(image_file).write_bytes(b"png")
        gcode_file = os.path.join(self.temp_dir, "image.gcode")
        Path(gcode_file).write_text("G21\nG1 X1\n", encoding="utf-8")
        ai_runner = Mock(
            return_value={
                "success": True,
                "result": {
                    "summary_path": os.path.join(self.temp_dir, "summary.json"),
                    "summary": {
                        "contract_version": 1,
                        "recommendation_status": "single_recommendation",
                        "can_send": True,
                        "gcode_path": gcode_file,
                        "processed_preview_path": os.path.join(self.temp_dir, "image.png"),
                        "material": "椴木",
                        "thickness_mm": 3,
                        "laser_mode": "M4",
                        "mode": "raster",
                        "task_type": "engrave_photo",
                        "power": 120,
                        "feed_rate": 1500,
                        "passes": 1,
                        "raster": {"pixel_size_mm": 0.2, "scan_direction": "horizontal"},
                        "auto_raster_profile": {"name": "default"},
                    },
                },
            }
        )

        result = laser_workflow_tool.run_laser_workflow_action(
            action="preview",
            source_type="image",
            image_file=image_file,
            material="椴木",
            thickness_mm=3,
            size_mm=50,
            width_mm=35,
            height_mm=12,
            workflows_dir=self.workflows_dir,
            ai_runner=ai_runner,
        )

        self.assertTrue(result["success"], result)
        call_kwargs = ai_runner.call_args.kwargs
        self.assertEqual(call_kwargs["size_mm"], 50.0)

    def _successful_image_ai_runner(self, *, mode="outline"):
        gcode_file = os.path.join(self.temp_dir, "image.gcode")
        Path(gcode_file).write_text("G21\nG1 X1\n", encoding="utf-8")
        summary = {
            "contract_version": 1,
            "recommendation_status": "single_recommendation",
            "can_send": True,
            "gcode_path": gcode_file,
            "processed_preview_path": os.path.join(self.temp_dir, "image.png"),
            "material": "椴木",
            "thickness_mm": 3,
            "laser_mode": "M4",
            "mode": mode,
            "task_type": "engrave_logo" if mode == "outline" else "engrave_photo",
            "power": 120,
            "feed_rate": 1500,
            "passes": 1,
        }
        if mode == "raster":
            summary["raster"] = {"pixel_size_mm": 0.2, "scan_direction": "horizontal"}
            summary["auto_raster_profile"] = {"name": "default"}
        return Mock(
            return_value={
                "success": True,
                "result": {
                    "summary_path": os.path.join(self.temp_dir, "summary.json"),
                    "summary": summary,
                },
            }
        )

    def test_image_outline_passes_vector_simplify_and_lock_aspect_to_runner(self):
        image_file = os.path.join(self.temp_dir, "input.png")
        Path(image_file).write_bytes(b"png")
        ai_runner = self._successful_image_ai_runner(mode="outline")

        result = laser_workflow_tool.run_laser_workflow_action(
            action="preview",
            source_type="image",
            image_file=image_file,
            material="椴木",
            thickness_mm=3,
            mode="outline",
            width_mm=50,
            height_mm=30,
            lock_aspect_ratio=True,
            vector_simplify_factor=3,
            workflows_dir=self.workflows_dir,
            ai_runner=ai_runner,
        )

        self.assertTrue(result["success"], result)
        call_kwargs = ai_runner.call_args.kwargs
        self.assertEqual(call_kwargs["vector_simplify_factor"], 3.0)
        self.assertTrue(call_kwargs["lock_aspect_ratio"])
        self.assertEqual(call_kwargs["width_mm"], 50.0)
        self.assertEqual(call_kwargs["height_mm"], 30.0)

        # Boundary values must also reach the image runner.
        for value in (0.25, 8.0):
            boundary_runner = self._successful_image_ai_runner(mode="outline")
            boundary = laser_workflow_tool.run_laser_workflow_action(
                action="preview",
                source_type="image",
                image_file=image_file,
                material="椴木",
                thickness_mm=3,
                mode="outline",
                vector_simplify_factor=value,
                workflows_dir=self.workflows_dir,
                ai_runner=boundary_runner,
            )
            self.assertTrue(boundary["success"], boundary)
            self.assertEqual(boundary_runner.call_args.kwargs["vector_simplify_factor"], float(value))

    def test_image_outline_rejects_out_of_range_vector_simplify_factor(self):
        image_file = os.path.join(self.temp_dir, "input.png")
        Path(image_file).write_bytes(b"png")
        ai_runner = self._successful_image_ai_runner(mode="outline")

        for bad in (0.249, 8.01, "nan", "inf", "abc"):
            result = laser_workflow_tool.run_laser_workflow_action(
                action="preview",
                source_type="image",
                image_file=image_file,
                material="椴木",
                thickness_mm=3,
                mode="outline",
                vector_simplify_factor=bad,
                workflows_dir=self.workflows_dir,
                ai_runner=ai_runner,
            )
            self.assertFalse(result["success"], bad)
            # _set_failed / _build_failure return result as a human string, not a status envelope.
            self.assertIsInstance(result.get("result"), str)
            self.assertIn("vector_simplify_factor", result["result"])
            self.assertEqual((result.get("detail") or {}).get("field"), "vector_simplify_factor")
            ai_runner.assert_not_called()

    def test_image_raster_ignores_vector_simplify_factor(self):
        image_file = os.path.join(self.temp_dir, "input.png")
        Path(image_file).write_bytes(b"png")
        ai_runner = self._successful_image_ai_runner(mode="raster")

        result = laser_workflow_tool.run_laser_workflow_action(
            action="preview",
            source_type="image",
            image_file=image_file,
            material="椴木",
            thickness_mm=3,
            mode="raster",
            lock_aspect_ratio=False,
            vector_simplify_factor=99,
            workflows_dir=self.workflows_dir,
            ai_runner=ai_runner,
        )

        self.assertTrue(result["success"], result)
        call_kwargs = ai_runner.call_args.kwargs
        self.assertNotIn("vector_simplify_factor", call_kwargs)
        self.assertFalse(call_kwargs["lock_aspect_ratio"])

    def test_image_summary_preview_and_confirm_use_unified_execution_sender(self):
        gcode_file = os.path.join(self.temp_dir, "image.gcode")
        Path(gcode_file).write_text("G21\nG1 X1\n", encoding="utf-8")
        summary_path = os.path.join(self.temp_dir, "summary.json")
        Path(summary_path).write_text(
            json.dumps(
                {
                    "contract_version": 1,
                    "gcode_path": gcode_file,
                    "processed_preview_path": os.path.join(self.temp_dir, "image.png"),
                    "recommendation_status": "single_recommendation",
                    "can_send": True,
                    "time_estimate": {"estimated_seconds": 33},
                    "material": "椴木",
                    "thickness_mm": 3,
                    "laser_mode": "M4",
                    "mode": "raster",
                    "task_type": "engrave_photo",
                    "power": 120,
                    "feed_rate": 1500,
                    "passes": 1,
                    "raster": {
                        "pixel_size_mm": 0.2,
                        "scan_direction": "horizontal",
                        "dither_algorithm": "floyd_steinberg",
                        "output_strategy": "scanline",
                    },
                    "auto_raster_profile": {"name": "quality"},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        preview = laser_workflow_tool.run_laser_workflow_action(
            action="preview",
            source_type="image_summary",
            summary_path=summary_path,
            workflows_dir=self.workflows_dir,
        )
        self.assertTrue(preview["success"], preview)
        self.assertEqual(preview["result"]["artifacts"]["summary_path"], summary_path)
        self.assertTrue(preview["result"]["workflow"]["confirmation_snapshot"].get("gcode_sha256"))

        ai_runner = Mock(return_value={"success": True, "result": {"send_result": {"job_id": "job-i", "status": "pending"}}})
        execution_sender = Mock(return_value={"success": True, "job_id": "job-i", "status": "pending"})
        sent = laser_workflow_tool.run_laser_workflow_action(
            action="confirm_send",
            workflow_id=preview["result"]["workflow_id"],
            confirmed=True,
            workflows_dir=self.workflows_dir,
            ai_runner=ai_runner,
            execution_sender=execution_sender,
            connection_mode="network",
            network_host="laser.local",
        )

        self.assertTrue(sent["success"], sent)
        ai_runner.assert_not_called()
        execution_sender.assert_called_once()
        prepared_arg = execution_sender.call_args.args[0]
        self.assertEqual(
            laser_workflow_tool._norm_path(prepared_arg["gcode_file"]),
            laser_workflow_tool._norm_path(gcode_file),
        )
        self.assertIn("expected_gcode_sha256", prepared_arg)
        self.assertEqual(sent["result"]["job_id"], "job-i")

    def test_ambiguous_latest_feedback_returns_candidates(self):
        first = laser_workflow_tool._new_workflow("prepared_gcode", {"gcode_file": "a.gcode"})
        first["status"] = "completed"
        first["runtime_job"] = {"finished_at": 100.0}
        second = laser_workflow_tool._new_workflow("prepared_gcode", {"gcode_file": "b.gcode"})
        second["status"] = "completed"
        second["runtime_job"] = {"finished_at": 100.0}
        laser_workflow_tool._save_workflow(first, workflows_dir=self.workflows_dir)
        laser_workflow_tool._save_workflow(second, workflows_dir=self.workflows_dir)

        result = laser_workflow_tool.run_laser_workflow_action(
            action="feedback",
            feedback_text="太浅了",
            workflows_dir=self.workflows_dir,
        )

        self.assertFalse(result["success"])
        self.assertEqual(len(result["detail"]["candidates"]), 2)
        self.assertIn("指定 workflow_id", result["speech"])

    def test_non_text_feedback_without_workflow_id_records_workflow_only(self):
        workflow = laser_workflow_tool._new_workflow("prepared_gcode", {"gcode_file": "shape.gcode"})
        workflow["status"] = "failed"
        workflow["runtime_job"] = {"finished_at": 200.0}
        laser_workflow_tool._save_workflow(workflow, workflows_dir=self.workflows_dir)
        helper = Mock()

        result = laser_workflow_tool.run_laser_workflow_action(
            action="feedback",
            feedback_text="边缘有毛刺",
            workflows_dir=self.workflows_dir,
            feedback_helper=helper,
        )

        self.assertTrue(result["success"], result)
        helper.assert_not_called()
        self.assertEqual(result["result"]["status"], "feedback_recorded")
        self.assertEqual(result["result"]["feedback_event"]["routed_to"], "workflow_only")

    def test_feedback_for_sending_workflow_keeps_sending_state(self):
        workflow = self.make_sending_workflow("network")
        helper = Mock(
            return_value={
                "success": True,
                "result": {
                    "matched_issue": "light",
                    "recommended_strategy": "raise_power",
                },
            }
        )

        result = laser_workflow_tool.run_laser_workflow_action(
            action="feedback",
            feedback_text="太浅了",
            workflows_dir=self.workflows_dir,
            feedback_helper=helper,
        )

        self.assertTrue(result["success"], result)
        helper.assert_called_once_with(feedback_text="太浅了", task_id="task-1")
        self.assertEqual(result["result"]["status"], "sending")
        self.assertEqual(result["result"]["feedback_event"]["recommended_strategy"], "raise_power")

    def test_status_missing_transport_fails_without_backend_scan(self):
        workflow = self.make_sending_workflow("network")
        workflow["runtime_job"].pop("transport", None)
        laser_workflow_tool._save_workflow(workflow, workflows_dir=self.workflows_dir)

        with patch.object(laser_workflow_tool.laser_execution, "job_status") as status_mock:
            result = laser_workflow_tool.run_laser_workflow_action(
                action="status",
                workflow_id=workflow["workflow_id"],
                workflows_dir=self.workflows_dir,
            )

        self.assertFalse(result["success"], result)
        self.assertIn("transport", result["result"])
        status_mock.assert_not_called()

    def test_cancel_missing_transport_fails_without_backend_scan(self):
        workflow = self.make_sending_workflow("serial")
        workflow["runtime_job"].pop("transport", None)
        laser_workflow_tool._save_workflow(workflow, workflows_dir=self.workflows_dir)

        with patch.object(laser_workflow_tool.laser_execution, "cancel_job") as cancel_mock:
            result = laser_workflow_tool.run_laser_workflow_action(
                action="cancel",
                workflow_id=workflow["workflow_id"],
                workflows_dir=self.workflows_dir,
            )

        self.assertFalse(result["success"], result)
        self.assertIn("transport", result["result"])
        cancel_mock.assert_not_called()

    def test_ordinary_workflow_preview_ignores_client_policy_injection(self):
        calls = {}

        gcode_file = os.path.join(self.temp_dir, "job.gcode")
        Path(gcode_file).write_text("G21\nG1 X1\n", encoding="utf-8")

        def fake_ai_runner(**kwargs):
            calls.update(kwargs)
            return {
                "success": True,
                "result": {
                    "summary_path": os.path.join(self.temp_dir, "summary.json"),
                    "summary": {
                        "contract_version": 1,
                        "recommendation_status": "single_recommendation",
                        "gcode_path": gcode_file,
                        "can_send": True,
                        "material": "亚克力",
                        "thickness_mm": 3,
                        "laser_mode": "M4",
                        "mode": "raster",
                        "task_type": "engrave_photo",
                        "power": 100,
                        "feed_rate": 1200,
                        "passes": 1,
                        "raster": {"pixel_size_mm": 0.2, "scan_direction": "horizontal"},
                        "auto_raster_profile": {"name": "default"},
                    },
                    "speech": "预览已生成",
                },
            }

        result = laser_workflow_tool.run_laser_workflow_action(
            action="preview",
            source_type="image",
            image_file=os.path.join(self.temp_dir, "draw.png"),
            material="亚克力",
            thickness_mm=3,
            task_type="engrave_photo",
            mode="raster",
            material_match_policy="nearest_engrave",
            send_policy="confirmed_material_record",
            workflows_dir=self.workflows_dir,
            ai_runner=fake_ai_runner,
        )

        self.assertTrue(result["success"], result)
        self.assertEqual(calls.get("material"), "亚克力")
        self.assertNotIn("material_match_policy", calls)
        self.assertNotIn("send_policy", calls)
        workflow = self.load_workflow(result["result"]["workflow_id"])
        self.assertNotIn("material_match_policy", workflow.get("input") or {})
        self.assertNotIn("send_policy", workflow.get("input") or {})

    def test_preview_draw_lab_image_pins_trusted_policy(self):
        calls = {}
        gcode_file = os.path.join(self.temp_dir, "job.gcode")
        Path(gcode_file).write_text("G21\nG1 X1\n", encoding="utf-8")

        def fake_ai_runner(**kwargs):
            calls.update(kwargs)
            return {
                "success": True,
                "result": {
                    "summary_path": os.path.join(self.temp_dir, "summary.json"),
                    "summary": {
                        "contract_version": 1,
                        "recommendation_status": "single_recommendation",
                        "gcode_path": gcode_file,
                        "can_send": True,
                        "material": "亚克力",
                        "thickness_mm": 3,
                        "laser_mode": "M4",
                        "mode": "raster",
                        "task_type": "engrave_photo",
                        "power": 100,
                        "feed_rate": 1200,
                        "passes": 1,
                        "raster": {"pixel_size_mm": 0.2, "scan_direction": "horizontal"},
                        "auto_raster_profile": {"name": "default"},
                    },
                    "speech": "预览已生成",
                },
            }

        result = laser_workflow_tool.preview_draw_lab_image(
            source_type="image",
            image_file=os.path.join(self.temp_dir, "draw.png"),
            material="亚克力",
            thickness_mm=3,
            task_type="engrave_photo",
            mode="raster",
            material_match_policy="exact_only",
            send_policy="verified_only",
            workflows_dir=self.workflows_dir,
            ai_runner=fake_ai_runner,
        )

        self.assertTrue(result["success"], result)
        self.assertEqual(calls.get("material"), "亚克力")
        self.assertEqual(calls.get("material_match_policy"), "nearest_engrave")
        self.assertEqual(calls.get("send_policy"), "confirmed_material_record")
        workflow = self.load_workflow(result["result"]["workflow_id"])
        self.assertEqual(workflow["input"].get("material_match_policy"), "nearest_engrave")
        self.assertEqual(workflow["input"].get("send_policy"), "confirmed_material_record")

    def test_preview_http_image_workflow_pins_trusted_policy(self):
        calls = {}
        gcode_file = os.path.join(self.temp_dir, "job.gcode")
        Path(gcode_file).write_text("G21\nG1 X1\n", encoding="utf-8")

        def fake_ai_runner(**kwargs):
            calls.update(kwargs)
            return {
                "success": True,
                "result": {
                    "summary_path": os.path.join(self.temp_dir, "summary.json"),
                    "summary": {
                        "contract_version": 1,
                        "recommendation_status": "single_recommendation",
                        "gcode_path": gcode_file,
                        "can_send": True,
                        "material": "亚克力",
                        "thickness_mm": 3,
                        "laser_mode": "M4",
                        "mode": "raster",
                        "task_type": "engrave_photo",
                        "power": 100,
                        "feed_rate": 1200,
                        "passes": 1,
                        "raster": {"pixel_size_mm": 0.2, "scan_direction": "horizontal"},
                        "auto_raster_profile": {"name": "default"},
                    },
                    "speech": "预览已生成",
                },
            }

        result = laser_workflow_tool.preview_http_image_workflow(
            source_type="image",
            image_file=os.path.join(self.temp_dir, "draw.png"),
            material="亚克力",
            thickness_mm=3,
            task_type="engrave_photo",
            mode="raster",
            material_match_policy="exact_only",
            send_policy="verified_only",
            workflows_dir=self.workflows_dir,
            ai_runner=fake_ai_runner,
        )

        self.assertTrue(result["success"], result)
        self.assertEqual(calls.get("material"), "亚克力")
        self.assertEqual(calls.get("material_match_policy"), "nearest_engrave")
        self.assertEqual(calls.get("send_policy"), "confirmed_material_record")
        workflow = self.load_workflow(result["result"]["workflow_id"])
        self.assertEqual(workflow["input"].get("material_match_policy"), "nearest_engrave")
        self.assertEqual(workflow["input"].get("send_policy"), "confirmed_material_record")

    def test_high_level_modules_do_not_import_private_backend(self):
        root = Path(__file__).resolve().parents[1]
        forbidden = (
            "core._laser_execution_backend",
            "_start_send_file_job",
            "_execute_send_file",
            "_get_send_file_job",
            "_cancel_send_file_job",
            "_start_network_send_file_job",
            "_execute_network_send_file",
            "_get_network_send_file_job",
            "_cancel_network_send_file_job",
        )
        for rel in (
            "tools/laser_workflow_tool.py",
            "tools/text_laser_task_tool.py",
            "tools/laser_material_calibration_tool.py",
            "tools/laser_asset_gcode_tool.py",
        ):
            source = (root / rel).read_text(encoding="utf-8")
            self.assertNotIn("core._laser_execution_backend", source, rel)
            if rel == "tools/laser_workflow_tool.py":
                self.assertIn("laser_execution.job_status", source)
                self.assertIn("laser_execution.cancel_job", source)
                self.assertIn("laser_execution.send_file", source)
                self.assertNotIn(
                    "text_laser_task_tool._send_prepared_gcode_file(",
                    source,
                )
                self.assertNotRegex(
                    source,
                    r"text_laser_task_tool\._send_prepared_gcode_file\s*\(",
                    f"{rel} still calls text_laser_task_tool._send_prepared_gcode_file",
                )
            if rel in {
                "tools/laser_material_calibration_tool.py",
                "tools/laser_asset_gcode_tool.py",
                "tools/text_laser_task_tool.py",
            }:
                self.assertIn("laser_execution.send_file", source)
            for token in forbidden[1:]:
                # constants / comments may mention names; forbid private call sites only
                self.assertNotRegex(
                    source,
                    rf"\b{token}\s*\(",
                    f"{rel} still calls {token}",
                )

    def _preview_text(self, **extra):
        kwargs = {
            "action": "preview",
            "source_type": "text",
            "text": "佳佳",
            "material": "椴木",
            "thickness_mm": 3,
            "workflows_dir": self.workflows_dir,
            "text_generator": self.fake_text_preview,
        }
        kwargs.update(extra)
        # Explicit manual processing overrides require confirmation to reach generator.
        manual_keys = (
            "power_percent",
            "laser_min_power",
            "laser_max_power",
            "feed_rate",
            "travel_rate",
            "passes",
            "pixel_size_mm",
            "threshold",
        )
        if any(key in kwargs for key in manual_keys) and "manual_params_confirmed" not in kwargs:
            kwargs["manual_params_confirmed"] = True
        result = laser_workflow_tool.run_laser_workflow_action(**kwargs)
        self.assertTrue(result["success"], result)
        return result

    def test_text_preview_persists_gcode_sha_and_snapshot(self):
        preview = self._preview_text()
        workflow = self.load_workflow(preview["result"]["workflow_id"])
        self.assertEqual(workflow["status"], "preview_ready")
        self.assertTrue(workflow["artifacts"].get("gcode_sha256"))
        self.assertEqual(
            workflow["artifacts"]["gcode_sha256"],
            workflow["confirmation_snapshot"]["gcode_sha256"],
        )
        self.assertEqual(workflow["confirmation_snapshot"]["source_type"], "text")
        self.assertEqual(workflow["confirmation_snapshot"]["material"], "椴木")

    def test_text_confirm_hash_mismatch_keeps_preview_ready_and_zero_sender(self):
        preview = self._preview_text()
        workflow_id = preview["result"]["workflow_id"]
        gcode_file = preview["result"]["artifacts"]["gcode_file"]
        Path(gcode_file).write_text("G0 X99\nG1 X100\n", encoding="utf-8")
        sender = Mock(return_value={"success": True, "job_id": "should-not-run", "status": "pending"})

        result = laser_workflow_tool.run_laser_workflow_action(
            action="confirm_send",
            workflow_id=workflow_id,
            confirmed=True,
            workflows_dir=self.workflows_dir,
            execution_sender=sender,
            connection_mode="serial",
            port="COM3",
        )

        self.assertFalse(result["success"])
        self.assertEqual(result.get("error_code"), "preview_content_mismatch")
        sender.assert_not_called()
        workflow = self.load_workflow(workflow_id)
        self.assertEqual(workflow["status"], "preview_ready")
        self.assertNotIn(gcode_file, result["speech"])

    def test_text_confirm_material_mismatch_zero_sender(self):
        preview = self._preview_text()
        sender = Mock()
        result = laser_workflow_tool.run_laser_workflow_action(
            action="confirm_send",
            workflow_id=preview["result"]["workflow_id"],
            confirmed=True,
            workflows_dir=self.workflows_dir,
            execution_sender=sender,
            material="卡纸",
            connection_mode="serial",
            port="COM3",
        )
        self.assertFalse(result["success"])
        self.assertEqual(result.get("error_code"), "confirmation_mismatch")
        sender.assert_not_called()
        self.assertEqual(self.load_workflow(preview["result"]["workflow_id"])["status"], "preview_ready")

    def test_text_confirm_does_not_call_start_tuned_job(self):
        preview = self._preview_text()
        with (
            patch.object(
                laser_workflow_tool.laser_material_calibration_tool,
                "start_tuned_job",
            ) as tuned_mock,
            patch.object(
                laser_workflow_tool.laser_execution,
                "send_file",
                return_value={"success": True, "job_id": "job-x", "status": "pending"},
            ) as send_mock,
        ):
            sent = laser_workflow_tool.run_laser_workflow_action(
                action="confirm_send",
                workflow_id=preview["result"]["workflow_id"],
                confirmed=True,
                workflows_dir=self.workflows_dir,
                connection_mode="serial",
                port="COM3",
            )
        self.assertTrue(sent["success"], sent)
        tuned_mock.assert_not_called()
        send_mock.assert_called_once()
        prepared = send_mock.call_args.args[0]
        self.assertIn("expected_gcode_sha256", prepared)
        self.assertEqual(
            laser_workflow_tool._norm_path(prepared["gcode_file"]),
            laser_workflow_tool._norm_path(os.path.join(self.temp_dir, "preview.gcode")),
        )

    def test_text_multi_pass_confirm_uses_preview_artifact_exact_file(self):
        preview = self._preview_text(passes=3, feed_rate=1600, power_percent=55)
        workflow = self.load_workflow(preview["result"]["workflow_id"])
        self.assertEqual(float(workflow["confirmation_snapshot"]["passes"]), 3.0)
        calls = []

        def capture_sender(prepared_result, mode, **kwargs):
            calls.append((prepared_result, mode, kwargs))
            return {"success": True, "job_id": "job-pass", "status": "pending"}

        with patch.object(
            laser_workflow_tool.laser_material_calibration_tool,
            "start_tuned_job",
        ) as tuned_mock:
            sent = laser_workflow_tool.run_laser_workflow_action(
                action="confirm_send",
                workflow_id=preview["result"]["workflow_id"],
                confirmed=True,
                workflows_dir=self.workflows_dir,
                execution_sender=capture_sender,
                connection_mode="serial",
                port="COM9",
            )
        self.assertTrue(sent["success"], sent)
        tuned_mock.assert_not_called()
        self.assertEqual(len(calls), 1)
        prepared, mode, kwargs = calls[0]
        self.assertEqual(mode, "serial")
        self.assertEqual(prepared["gcode_file"], workflow["artifacts"]["gcode_file"])
        self.assertEqual(prepared["expected_gcode_sha256"], workflow["artifacts"]["gcode_sha256"])

    def test_prepared_confirm_rejects_new_material_label(self):
        gcode_file = os.path.join(self.temp_dir, "prepared.gcode")
        Path(gcode_file).write_text("G21\nG1 X1 F100\n", encoding="utf-8")
        preview = laser_workflow_tool.run_laser_workflow_action(
            action="preview",
            source_type="prepared_gcode",
            gcode_file=gcode_file,
            workflows_dir=self.workflows_dir,
        )
        self.assertTrue(preview["success"], preview)
        sender = Mock()
        result = laser_workflow_tool.run_laser_workflow_action(
            action="confirm_send",
            workflow_id=preview["result"]["workflow_id"],
            confirmed=True,
            workflows_dir=self.workflows_dir,
            execution_sender=sender,
            material="椴木",
            connection_mode="serial",
            port="COM3",
        )
        self.assertFalse(result["success"])
        self.assertEqual(result.get("error_code"), "confirmation_mismatch")
        sender.assert_not_called()
        self.assertEqual(self.load_workflow(preview["result"]["workflow_id"])["status"], "preview_ready")

    def test_connection_fill_missing_and_freeze_existing(self):
        preview = self._preview_text(connection_mode="serial", port="COM1")
        sender = Mock(return_value={"success": True, "job_id": "job-c", "status": "pending"})
        # cannot replace existing port
        denied = laser_workflow_tool.run_laser_workflow_action(
            action="confirm_send",
            workflow_id=preview["result"]["workflow_id"],
            confirmed=True,
            workflows_dir=self.workflows_dir,
            execution_sender=sender,
            port="COM9",
        )
        self.assertFalse(denied["success"])
        self.assertEqual(denied.get("error_code"), "confirmation_mismatch")
        sender.assert_not_called()

        # can fill missing baudrate
        ok = laser_workflow_tool.run_laser_workflow_action(
            action="confirm_send",
            workflow_id=preview["result"]["workflow_id"],
            confirmed=True,
            workflows_dir=self.workflows_dir,
            execution_sender=sender,
            baudrate=115200,
        )
        self.assertTrue(ok["success"], ok)
        sender.assert_called_once()

    # --- OpenCode R1 regression matrix ---

    def _write_sendable_summary(self, gcode_file, **overrides):
        # Production JobParams/summary schema (nested raster + raw S power + M3/M4).
        summary = {
            "contract_version": 1,
            "gcode_path": gcode_file,
            "processed_preview_path": os.path.join(self.temp_dir, "image.png"),
            "recommendation_status": "single_recommendation",
            "can_send": True,
            "time_estimate": {"estimated_seconds": 33},
            "material": "椴木",
            "thickness_mm": 3,
            "laser_mode": "M4",
            "mode": "raster",
            "task_type": "engrave_photo",
            "power": 120,
            "feed_rate": 1500,
            "passes": 1,
            "raster": {
                "pixel_size_mm": 0.2,
                "overscan_mm": 2.0,
                "dither_algorithm": "floyd_steinberg",
                "scan_direction": "horizontal",
                "scan_direction_requested": "auto",
                "output_strategy": "scanline",
                "output_strategy_requested": "auto",
                "snake_scan": True,
            },
            "auto_raster_profile": {"name": "quality"},
            "image_preprocess": {"invert": False, "threshold": 128},
        }
        summary.update(overrides)
        summary_path = os.path.join(self.temp_dir, f"summary-{uuid.uuid4().hex}.json")
        Path(summary_path).write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
        return summary_path, summary

    def _preview_image_summary(self, **summary_overrides):
        gcode_file = os.path.join(self.temp_dir, f"image-{uuid.uuid4().hex}.gcode")
        Path(gcode_file).write_text("G21\nG1 X1\n", encoding="utf-8")
        summary_path, summary = self._write_sendable_summary(gcode_file, **summary_overrides)
        preview = laser_workflow_tool.run_laser_workflow_action(
            action="preview",
            source_type="image_summary",
            summary_path=summary_path,
            workflows_dir=self.workflows_dir,
        )
        return preview, gcode_file, summary_path, summary

    def _preview_prepared(self, content="G21\nG1 X1 F100\n", **extra):
        gcode_file = os.path.join(self.temp_dir, f"prepared-{uuid.uuid4().hex}.gcode")
        Path(gcode_file).write_text(content, encoding="utf-8")
        kwargs = {
            "action": "preview",
            "source_type": "prepared_gcode",
            "gcode_file": gcode_file,
            "workflows_dir": self.workflows_dir,
        }
        kwargs.update(extra)
        preview = laser_workflow_tool.run_laser_workflow_action(**kwargs)
        return preview, gcode_file

    def _assert_confirm_fail_zero_side_effects(self, preview, sender, probe, enqueue, **confirm_kwargs):
        workflow_id = preview["result"]["workflow_id"]
        result = laser_workflow_tool.run_laser_workflow_action(
            action="confirm_send",
            workflow_id=workflow_id,
            confirmed=True,
            workflows_dir=self.workflows_dir,
            execution_sender=sender,
            **confirm_kwargs,
        )
        self.assertFalse(result["success"], result)
        sender.assert_not_called()
        probe.assert_not_called()
        enqueue.assert_not_called()
        workflow = self.load_workflow(workflow_id)
        self.assertEqual(workflow["status"], "preview_ready")
        return result

    def test_r1_text_integrity_failures_keep_preview_ready(self):
        cases = ("missing_file", "missing_digest", "content_replaced", "path_override")
        for case in cases:
            with self.subTest(case=case):
                preview = self._preview_text()
                workflow_id = preview["result"]["workflow_id"]
                workflow = self.load_workflow(workflow_id)
                gcode_file = workflow["artifacts"]["gcode_file"]
                sender = Mock()
                probe = Mock()
                enqueue = Mock()
                if case == "missing_file":
                    os.remove(gcode_file)
                elif case == "missing_digest":
                    workflow["confirmation_snapshot"]["gcode_sha256"] = ""
                    laser_workflow_tool._save_workflow(workflow, workflows_dir=self.workflows_dir)
                elif case == "content_replaced":
                    Path(gcode_file).write_text("G0 X99\n", encoding="utf-8")
                elif case == "path_override":
                    other = os.path.join(self.temp_dir, "other.gcode")
                    Path(other).write_text("G21\n", encoding="utf-8")
                    result = self._assert_confirm_fail_zero_side_effects(
                        preview,
                        sender,
                        probe,
                        enqueue,
                        connection_mode="serial",
                        port="COM3",
                        gcode_file=other,
                    )
                    self.assertEqual(result.get("error_code"), "confirmation_mismatch")
                    continue
                with (
                    patch.object(laser_workflow_tool.laser_execution, "send_file", sender),
                    patch.object(laser_workflow_tool.laser_grbl_tool, "probe_serial_connection", probe, create=True),
                    patch.object(laser_workflow_tool.laser_network_grbl_tool, "enqueue_job", enqueue, create=True),
                ):
                    result = laser_workflow_tool.run_laser_workflow_action(
                        action="confirm_send",
                        workflow_id=workflow_id,
                        confirmed=True,
                        workflows_dir=self.workflows_dir,
                        execution_sender=sender,
                        connection_mode="serial",
                        port="COM3",
                    )
                self.assertFalse(result["success"], result)
                self.assertEqual(result.get("error_code"), "preview_content_mismatch")
                sender.assert_not_called()
                self.assertEqual(self.load_workflow(workflow_id)["status"], "preview_ready")

    def test_r1_image_summary_integrity_failures_keep_preview_ready(self):
        for case in ("missing_file", "missing_digest", "content_replaced", "path_override"):
            with self.subTest(case=case):
                preview, gcode_file, _summary_path, _summary = self._preview_image_summary()
                self.assertTrue(preview["success"], preview)
                workflow_id = preview["result"]["workflow_id"]
                workflow = self.load_workflow(workflow_id)
                sender = Mock()
                if case == "missing_file":
                    os.remove(gcode_file)
                elif case == "missing_digest":
                    workflow["confirmation_snapshot"]["gcode_sha256"] = ""
                    laser_workflow_tool._save_workflow(workflow, workflows_dir=self.workflows_dir)
                elif case == "content_replaced":
                    Path(gcode_file).write_text("G0 X77\n", encoding="utf-8")
                elif case == "path_override":
                    other = os.path.join(self.temp_dir, "img-other.gcode")
                    Path(other).write_text("G21\n", encoding="utf-8")
                    result = laser_workflow_tool.run_laser_workflow_action(
                        action="confirm_send",
                        workflow_id=workflow_id,
                        confirmed=True,
                        workflows_dir=self.workflows_dir,
                        execution_sender=sender,
                        connection_mode="serial",
                        port="COM3",
                        gcode_file=other,
                    )
                    self.assertFalse(result["success"])
                    self.assertEqual(result.get("error_code"), "confirmation_mismatch")
                    sender.assert_not_called()
                    self.assertEqual(self.load_workflow(workflow_id)["status"], "preview_ready")
                    continue
                result = laser_workflow_tool.run_laser_workflow_action(
                    action="confirm_send",
                    workflow_id=workflow_id,
                    confirmed=True,
                    workflows_dir=self.workflows_dir,
                    execution_sender=sender,
                    connection_mode="serial",
                    port="COM3",
                )
                self.assertFalse(result["success"], result)
                self.assertEqual(result.get("error_code"), "preview_content_mismatch")
                sender.assert_not_called()
                self.assertEqual(self.load_workflow(workflow_id)["status"], "preview_ready")

    def test_r1_prepared_integrity_failures_keep_preview_ready(self):
        for case in ("missing_file", "missing_digest", "content_replaced", "path_override"):
            with self.subTest(case=case):
                preview, gcode_file = self._preview_prepared()
                self.assertTrue(preview["success"], preview)
                workflow_id = preview["result"]["workflow_id"]
                workflow = self.load_workflow(workflow_id)
                sender = Mock()
                if case == "missing_file":
                    os.remove(gcode_file)
                elif case == "missing_digest":
                    workflow["confirmation_snapshot"]["gcode_sha256"] = ""
                    laser_workflow_tool._save_workflow(workflow, workflows_dir=self.workflows_dir)
                elif case == "content_replaced":
                    Path(gcode_file).write_text("G0 X55\n", encoding="utf-8")
                elif case == "path_override":
                    other = os.path.join(self.temp_dir, "prep-other.gcode")
                    Path(other).write_text("G21\n", encoding="utf-8")
                    result = laser_workflow_tool.run_laser_workflow_action(
                        action="confirm_send",
                        workflow_id=workflow_id,
                        confirmed=True,
                        workflows_dir=self.workflows_dir,
                        execution_sender=sender,
                        connection_mode="serial",
                        port="COM3",
                        gcode_file=other,
                    )
                    self.assertFalse(result["success"])
                    self.assertEqual(result.get("error_code"), "confirmation_mismatch")
                    sender.assert_not_called()
                    continue
                result = laser_workflow_tool.run_laser_workflow_action(
                    action="confirm_send",
                    workflow_id=workflow_id,
                    confirmed=True,
                    workflows_dir=self.workflows_dir,
                    execution_sender=sender,
                    connection_mode="serial",
                    port="COM3",
                )
                self.assertFalse(result["success"], result)
                self.assertEqual(result.get("error_code"), "preview_content_mismatch")
                sender.assert_not_called()
                self.assertEqual(self.load_workflow(workflow_id)["status"], "preview_ready")

    def test_r1_image_summary_safety_gate_reject_matrix(self):
        reject_cases = [
            {"contract_version": 99},
            {"recommendation_status": "candidate_selection_required"},
            {"can_send": False},
            {"safety_report": {"can_send": False, "message": "blocked by safety report"}},
        ]
        for overrides in reject_cases:
            with self.subTest(overrides=overrides):
                gcode_file = os.path.join(self.temp_dir, f"unsafe-{uuid.uuid4().hex}.gcode")
                Path(gcode_file).write_text("G21\n", encoding="utf-8")
                summary_path, _ = self._write_sendable_summary(gcode_file, **overrides)
                sender = Mock()
                preview = laser_workflow_tool.run_laser_workflow_action(
                    action="preview",
                    source_type="image_summary",
                    summary_path=summary_path,
                    workflows_dir=self.workflows_dir,
                )
                # Preview succeeds with artifacts; only confirm_send stays blocked.
                self.assertTrue(preview.get("success"), preview)
                self.assertEqual(preview["result"].get("status"), "preview_ready")
                next_actions = preview["result"].get("next_actions") or []
                self.assertNotIn("confirm_send", next_actions, preview)
                snap = preview["result"]["workflow"].get("confirmation_snapshot") or {}
                self.assertTrue(snap.get("gcode_sha256"), preview)
                self.assertFalse(snap.get("can_send"), preview)
                artifacts = preview["result"].get("artifacts") or {}
                self.assertTrue(artifacts.get("gcode_file") or snap.get("gcode_file"), preview)
                sent = laser_workflow_tool.run_laser_workflow_action(
                    action="confirm_send",
                    workflow_id=preview["result"]["workflow_id"],
                    confirmed=True,
                    workflows_dir=self.workflows_dir,
                    execution_sender=sender,
                    connection_mode="serial",
                    port="COM3",
                )
                self.assertFalse(sent["success"])
                self.assertEqual(sent.get("error_code"), "not_sendable")
                sender.assert_not_called()

    def test_r1_regenerate_rebuilds_snapshot_and_sends_new_artifact(self):
        preview = self._preview_text()
        workflow_id = preview["result"]["workflow_id"]
        old_workflow = self.load_workflow(workflow_id)
        old_sha = old_workflow["confirmation_snapshot"]["gcode_sha256"]
        old_file = old_workflow["artifacts"]["gcode_file"]
        new_gcode = os.path.join(self.temp_dir, "regen-new.gcode")
        Path(new_gcode).write_text("G21\nG1 X9 F900\nM5\n", encoding="utf-8")

        def regenerator(**kwargs):
            return {
                "success": True,
                "result": {
                    "task_id": "task-1",
                    "attempt_no": 2,
                    "image_file": os.path.join(self.temp_dir, "regen.png"),
                    "gcode_file": new_gcode,
                    "params": {
                        "power_percent": 35,
                        "feed_rate": 2200,
                        "passes": 2,
                        "travel_rate": 3000,
                        "laser_max_power": 300,
                    },
                    "passes_applied": 2,
                    "time_estimate": {"estimated_seconds": 70},
                    "recommendation": {
                        "material": "椴木",
                        "requested_material": "椴木",
                        "thickness_mm": 3.0,
                        "matched_thickness_mm": 3.0,
                        "match": "exact",
                        "laser_mode": "engrave",
                        "warnings": [],
                        "can_send": True,
                        "send_blocked_reason": "",
                    },
                },
            }

        regenerated = laser_workflow_tool.run_laser_workflow_action(
            action="regenerate",
            workflow_id=workflow_id,
            strategy="lower_power",
            workflows_dir=self.workflows_dir,
            regenerator=regenerator,
        )
        self.assertTrue(regenerated["success"], regenerated)
        workflow = self.load_workflow(workflow_id)
        self.assertEqual(workflow["status"], "preview_ready")
        self.assertNotEqual(workflow["confirmation_snapshot"]["gcode_sha256"], old_sha)
        self.assertEqual(
            laser_workflow_tool._norm_path(workflow["confirmation_snapshot"]["gcode_file"]),
            laser_workflow_tool._norm_path(new_gcode),
        )
        self.assertEqual(float(workflow["confirmation_snapshot"]["passes"]), 2.0)
        self.assertEqual(float(workflow["confirmation_snapshot"]["power_percent"]), 35.0)

        calls = []

        def capture_sender(prepared_result, mode, **kwargs):
            calls.append((prepared_result, mode, kwargs))
            return {"success": True, "job_id": "job-regen", "status": "pending"}

        sent = laser_workflow_tool.run_laser_workflow_action(
            action="confirm_send",
            workflow_id=workflow_id,
            confirmed=True,
            workflows_dir=self.workflows_dir,
            execution_sender=capture_sender,
            connection_mode="serial",
            port="COM3",
        )
        self.assertTrue(sent["success"], sent)
        self.assertEqual(len(calls), 1)
        prepared = calls[0][0]
        self.assertEqual(laser_workflow_tool._norm_path(prepared["gcode_file"]), laser_workflow_tool._norm_path(new_gcode))
        self.assertNotEqual(laser_workflow_tool._norm_path(prepared["gcode_file"]), laser_workflow_tool._norm_path(old_file))
        self.assertEqual(prepared["expected_gcode_sha256"], workflow["confirmation_snapshot"]["gcode_sha256"])

    def test_r1_regenerate_missing_new_file_discards_old_snapshot(self):
        preview = self._preview_text()
        workflow_id = preview["result"]["workflow_id"]
        missing = os.path.join(self.temp_dir, "missing-regen.gcode")

        def regenerator(**kwargs):
            return {
                "success": True,
                "result": {
                    "task_id": "task-1",
                    "attempt_no": 2,
                    "gcode_file": missing,
                    "params": {"power_percent": 30, "feed_rate": 1000, "passes": 1},
                    "passes_applied": 1,
                },
            }

        result = laser_workflow_tool.run_laser_workflow_action(
            action="regenerate",
            workflow_id=workflow_id,
            strategy="lower_power",
            workflows_dir=self.workflows_dir,
            regenerator=regenerator,
        )
        self.assertFalse(result["success"])
        workflow = self.load_workflow(workflow_id)
        self.assertNotEqual(workflow["status"], "preview_ready")
        self.assertFalse(workflow.get("confirmation_snapshot"))

    def test_r1_mcp_register_confirm_not_polluted_by_wrapper_defaults(self):
        preview = self._preview_text()
        workflow_id = preview["result"]["workflow_id"]
        fake = FakeMcp()
        laser_workflow_tool.register_tool(fake)
        tool = fake.tools["laser_workflow_tool"]

        with patch.object(laser_workflow_tool, "run_laser_workflow_action") as runner:
            runner.return_value = {"success": True, "result": {"job_id": "job-mcp"}}
            tool(
                action="confirm_send",
                workflow_id=workflow_id,
                confirmed=True,
                connection_mode="serial",
                port="COM3",
            )
            kwargs = runner.call_args.kwargs
            self.assertNotIn("thickness_mm", kwargs)
            self.assertNotIn("laser_mode", kwargs)
            self.assertNotIn("power_percent", kwargs)
            self.assertNotIn("feed_rate", kwargs)
            self.assertNotIn("passes", kwargs)
            self.assertNotIn("baudrate", kwargs)
            self.assertNotIn("wait_for_response", kwargs)
            self.assertNotIn("run_in_background", kwargs)
            self.assertEqual(kwargs.get("connection_mode"), "serial")
            self.assertEqual(kwargs.get("port"), "COM3")
            self.assertTrue(kwargs.get("confirmed"))

        # End-to-end: only workflow_id/confirmed/connection fill — no processing defaults.
        sender = Mock(return_value={"success": True, "job_id": "job-mcp2", "status": "pending"})
        sent = laser_workflow_tool.run_laser_workflow_action(
            action="confirm_send",
            workflow_id=workflow_id,
            confirmed=True,
            workflows_dir=self.workflows_dir,
            execution_sender=sender,
            connection_mode="serial",
            port="COM8",
        )
        self.assertTrue(sent["success"], sent)
        sender.assert_called_once()

    def test_r1_sender_typeerror_is_single_call(self):
        preview = self._preview_text()
        calls = []

        def broken_sender(prepared_result, mode, **kwargs):
            calls.append((prepared_result, mode, kwargs))
            raise TypeError("unexpected signature")

        with self.assertRaises(TypeError):
            laser_workflow_tool.run_laser_workflow_action(
                action="confirm_send",
                workflow_id=preview["result"]["workflow_id"],
                confirmed=True,
                workflows_dir=self.workflows_dir,
                execution_sender=broken_sender,
                connection_mode="serial",
                port="COM3",
            )
        self.assertEqual(len(calls), 1)

    def test_r1_frozen_processing_field_mismatches_fail_before_sender(self):
        preview = self._preview_text(passes=2, feed_rate=1800, power_percent=60)
        fields = {
            "material": "卡纸",
            "thickness_mm": 1.0,
            "laser_mode": "cut",
            "power_percent": 99,
            "feed_rate": 999,
            "passes": 9,
            "travel_rate": 1,
        }
        for key, bad in fields.items():
            with self.subTest(field=key):
                sender = Mock()
                result = laser_workflow_tool.run_laser_workflow_action(
                    action="confirm_send",
                    workflow_id=preview["result"]["workflow_id"],
                    confirmed=True,
                    workflows_dir=self.workflows_dir,
                    execution_sender=sender,
                    connection_mode="serial",
                    port="COM3",
                    **{key: bad},
                )
                self.assertFalse(result["success"], result)
                self.assertEqual(result.get("error_code"), "confirmation_mismatch")
                sender.assert_not_called()
                self.assertEqual(self.load_workflow(preview["result"]["workflow_id"])["status"], "preview_ready")

    def test_r1_material_alias_and_thickness_tolerance(self):
        preview = self._preview_text()
        sender = Mock(return_value={"success": True, "job_id": "job-alias", "status": "pending"})
        ok = laser_workflow_tool.run_laser_workflow_action(
            action="confirm_send",
            workflow_id=preview["result"]["workflow_id"],
            confirmed=True,
            workflows_dir=self.workflows_dir,
            execution_sender=sender,
            material="木头",
            thickness_mm=3.001,
            connection_mode="serial",
            port="COM3",
        )
        self.assertTrue(ok["success"], ok)
        sender.assert_called_once()

        preview2 = self._preview_text()
        sender2 = Mock()
        denied = laser_workflow_tool.run_laser_workflow_action(
            action="confirm_send",
            workflow_id=preview2["result"]["workflow_id"],
            confirmed=True,
            workflows_dir=self.workflows_dir,
            execution_sender=sender2,
            material="木头",
            thickness_mm=3.002,
            connection_mode="serial",
            port="COM3",
        )
        self.assertFalse(denied["success"])
        self.assertEqual(denied.get("error_code"), "confirmation_mismatch")
        sender2.assert_not_called()

    def test_r1_text_and_image_missing_authority_fail_closed(self):
        # text: remove params authority after preview and rebind should fail
        preview = self._preview_text()
        workflow = self.load_workflow(preview["result"]["workflow_id"])
        workflow["last_preview_result"] = {"params": {}, "passes_applied": None}
        workflow["artifacts"].pop("gcode_sha256", None)
        error = laser_workflow_tool._bind_preview_artifact_integrity(workflow)
        self.assertIsNotNone(error)
        self.assertIn("权威", error)

        # image_summary missing power/feed/passes
        gcode_file = os.path.join(self.temp_dir, "no-auth.gcode")
        Path(gcode_file).write_text("G21\n", encoding="utf-8")
        summary_path, _ = self._write_sendable_summary(
            gcode_file,
            power=None,
            feed_rate=None,
            passes=None,
        )
        # strip keys entirely
        raw = json.loads(Path(summary_path).read_text(encoding="utf-8"))
        for key in ("power", "feed_rate", "passes", "power_percent"):
            raw.pop(key, None)
        Path(summary_path).write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        preview_img = laser_workflow_tool.run_laser_workflow_action(
            action="preview",
            source_type="image_summary",
            summary_path=summary_path,
            workflows_dir=self.workflows_dir,
        )
        self.assertFalse(preview_img.get("success") and preview_img["result"].get("status") == "preview_ready")

    def test_r1_prepared_confirm_cannot_add_labels_or_processing_params(self):
        preview, _gcode = self._preview_prepared()
        sender = Mock()
        for kwargs in (
            {"material": "椴木"},
            {"thickness_mm": 3},
            {"laser_mode": "engrave"},
            {"power_percent": 50},
            {"feed_rate": 1000},
            {"passes": 2},
        ):
            with self.subTest(kwargs=kwargs):
                result = laser_workflow_tool.run_laser_workflow_action(
                    action="confirm_send",
                    workflow_id=preview["result"]["workflow_id"],
                    confirmed=True,
                    workflows_dir=self.workflows_dir,
                    execution_sender=sender,
                    connection_mode="serial",
                    port="COM3",
                    **kwargs,
                )
                self.assertFalse(result["success"], result)
                self.assertEqual(result.get("error_code"), "confirmation_mismatch")
                sender.assert_not_called()

    def test_r1_mode_alias_maps_and_semantic_mismatch_rejects(self):
        preview = self._preview_text()
        sender = Mock(return_value={"success": True, "job_id": "job-mode", "status": "pending"})
        # laser_mode aliases still map (engraving -> engrave); generation mode is separate.
        ok = laser_workflow_tool.run_laser_workflow_action(
            action="confirm_send",
            workflow_id=preview["result"]["workflow_id"],
            confirmed=True,
            workflows_dir=self.workflows_dir,
            execution_sender=sender,
            laser_mode="engraving",
            connection_mode="serial",
            port="COM3",
        )
        self.assertTrue(ok["success"], ok)
        sender.assert_called_once()

        preview2 = self._preview_text()
        sender2 = Mock()
        denied = laser_workflow_tool.run_laser_workflow_action(
            action="confirm_send",
            workflow_id=preview2["result"]["workflow_id"],
            confirmed=True,
            workflows_dir=self.workflows_dir,
            execution_sender=sender2,
            laser_mode="cut",
            connection_mode="serial",
            port="COM3",
        )
        self.assertFalse(denied["success"])
        sender2.assert_not_called()

    # --- OpenCode R2 regression matrix ---

    def test_r2_update_production_fields_invalidates_preview_binding(self):
        preview = self._preview_text()
        workflow_id = preview["result"]["workflow_id"]
        workflow = self.load_workflow(workflow_id)
        self.assertEqual(workflow["status"], "preview_ready")
        self.assertTrue(workflow["confirmation_snapshot"].get("gcode_sha256"))
        self.assertEqual(workflow["confirmation_snapshot"]["material"], "椴木")

        updated = laser_workflow_tool.run_laser_workflow_action(
            action="update",
            workflow_id=workflow_id,
            material="卡纸",
            thickness_mm=1,
            workflows_dir=self.workflows_dir,
        )
        self.assertTrue(updated["success"], updated)
        workflow = self.load_workflow(workflow_id)
        self.assertEqual(workflow["input"]["material"], "卡纸")
        self.assertEqual(float(workflow["input"]["thickness_mm"]), 1.0)
        self.assertNotEqual(workflow["status"], "preview_ready")
        self.assertFalse(workflow.get("confirmation_snapshot"))

        sender = Mock()
        sent = laser_workflow_tool.run_laser_workflow_action(
            action="confirm_send",
            workflow_id=workflow_id,
            confirmed=True,
            workflows_dir=self.workflows_dir,
            execution_sender=sender,
            connection_mode="serial",
            port="COM3",
        )
        self.assertFalse(sent["success"])
        sender.assert_not_called()

    def test_r2_confirm_rejects_missing_authority_keys_on_persisted_snapshot(self):
        # text: delete power_percent from persisted snapshot
        preview = self._preview_text()
        workflow_id = preview["result"]["workflow_id"]
        workflow = self.load_workflow(workflow_id)
        del workflow["confirmation_snapshot"]["power_percent"]
        laser_workflow_tool._save_workflow(workflow, workflows_dir=self.workflows_dir)
        sender = Mock()
        denied = laser_workflow_tool.run_laser_workflow_action(
            action="confirm_send",
            workflow_id=workflow_id,
            confirmed=True,
            workflows_dir=self.workflows_dir,
            execution_sender=sender,
            connection_mode="serial",
            port="COM3",
        )
        self.assertFalse(denied["success"])
        self.assertEqual(denied.get("error_code"), "preview_content_mismatch")
        sender.assert_not_called()
        self.assertEqual(self.load_workflow(workflow_id)["status"], "preview_ready")

        # text: delete source_type
        preview2 = self._preview_text()
        workflow2 = self.load_workflow(preview2["result"]["workflow_id"])
        workflow2["confirmation_snapshot"].pop("source_type", None)
        laser_workflow_tool._save_workflow(workflow2, workflows_dir=self.workflows_dir)
        sender2 = Mock()
        denied2 = laser_workflow_tool.run_laser_workflow_action(
            action="confirm_send",
            workflow_id=preview2["result"]["workflow_id"],
            confirmed=True,
            workflows_dir=self.workflows_dir,
            execution_sender=sender2,
            connection_mode="serial",
            port="COM3",
        )
        self.assertFalse(denied2["success"])
        sender2.assert_not_called()

        # image_summary: delete task_type
        preview_img, _gcode, _path, _summary = self._preview_image_summary()
        self.assertTrue(preview_img["success"], preview_img)
        wf_img = self.load_workflow(preview_img["result"]["workflow_id"])
        wf_img["confirmation_snapshot"].pop("task_type", None)
        laser_workflow_tool._save_workflow(wf_img, workflows_dir=self.workflows_dir)
        sender3 = Mock()
        denied3 = laser_workflow_tool.run_laser_workflow_action(
            action="confirm_send",
            workflow_id=preview_img["result"]["workflow_id"],
            confirmed=True,
            workflows_dir=self.workflows_dir,
            execution_sender=sender3,
            connection_mode="serial",
            port="COM3",
        )
        self.assertFalse(denied3["success"])
        sender3.assert_not_called()

    def test_r2_extended_frozen_fields_mismatch_before_sender(self):
        preview = self._preview_text(pixel_size_mm=0.3, threshold=128, invert=False)
        extended = {
            "pixel_size_mm": 0.9,
            "threshold": 222,
            "invert": True,
            "bidirectional": True,
            "overscan_mm": 9.9,
            "raster_scan_direction": "vertical",
            "dither_algorithm": "floyd",
            "raster_output_strategy": "scanline",
            "raster_quality_strategy": "manual",
            "task_type": "cut",
        }
        for key, bad in extended.items():
            with self.subTest(field=key):
                sender = Mock()
                result = laser_workflow_tool.run_laser_workflow_action(
                    action="confirm_send",
                    workflow_id=preview["result"]["workflow_id"],
                    confirmed=True,
                    workflows_dir=self.workflows_dir,
                    execution_sender=sender,
                    connection_mode="serial",
                    port="COM3",
                    **{key: bad},
                )
                self.assertFalse(result["success"], result)
                self.assertEqual(result.get("error_code"), "confirmation_mismatch")
                sender.assert_not_called()

        # prepared: strategy/processing overrides forbidden
        prep, _ = self._preview_prepared()
        for kwargs in (
            {"pixel_size_mm": 0.9},
            {"task_type": "engrave"},
            {"threshold": 100},
            {"raster_quality_strategy": "manual"},
        ):
            with self.subTest(prepared=kwargs):
                sender = Mock()
                denied = laser_workflow_tool.run_laser_workflow_action(
                    action="confirm_send",
                    workflow_id=prep["result"]["workflow_id"],
                    confirmed=True,
                    workflows_dir=self.workflows_dir,
                    execution_sender=sender,
                    connection_mode="serial",
                    port="COM3",
                    **kwargs,
                )
                self.assertFalse(denied["success"], denied)
                sender.assert_not_called()

        # image: task_type and nested strategy mismatches against production schema
        preview_img, _g, _p, _s = self._preview_image_summary()
        for kwargs in (
            {"task_type": "cut_contour"},
            {"raster_quality_strategy": "manual"},
            {"raster_scan_direction": "vertical"},
            {"pixel_size_mm": 0.9},
        ):
            with self.subTest(image=kwargs):
                sender = Mock()
                denied = laser_workflow_tool.run_laser_workflow_action(
                    action="confirm_send",
                    workflow_id=preview_img["result"]["workflow_id"],
                    confirmed=True,
                    workflows_dir=self.workflows_dir,
                    execution_sender=sender,
                    connection_mode="serial",
                    port="COM3",
                    **kwargs,
                )
                self.assertFalse(denied["success"], denied)
                sender.assert_not_called()

    def test_r2_image_laser_mode_and_generation_mode_are_separate(self):
        # production schema: laser_mode=M4, mode=raster
        gcode_file = os.path.join(self.temp_dir, "dual-mode.gcode")
        Path(gcode_file).write_text("G21\nG1 X1\n", encoding="utf-8")
        summary_path, _ = self._write_sendable_summary(
            gcode_file,
            laser_mode="M4",
            mode="raster",
            task_type="engrave_photo",
        )
        preview = laser_workflow_tool.run_laser_workflow_action(
            action="preview",
            source_type="image_summary",
            summary_path=summary_path,
            workflows_dir=self.workflows_dir,
        )
        self.assertTrue(preview["success"], preview)
        snap = self.load_workflow(preview["result"]["workflow_id"])["confirmation_snapshot"]
        self.assertEqual(snap["laser_mode"], "M4")
        self.assertEqual(snap["mode"], "raster")
        self.assertEqual(float(snap["power"]), 120.0)
        self.assertEqual(snap["raster_scan_direction"], "horizontal")
        self.assertEqual(float(snap["pixel_size_mm"]), 0.2)

        sender = Mock(return_value={"success": True, "job_id": "job-dual", "status": "pending"})
        ok = laser_workflow_tool.run_laser_workflow_action(
            action="confirm_send",
            workflow_id=preview["result"]["workflow_id"],
            confirmed=True,
            workflows_dir=self.workflows_dir,
            execution_sender=sender,
            laser_mode="M4",
            mode="raster",
            raster_scan_direction="horizontal",
            connection_mode="serial",
            port="COM3",
        )
        self.assertTrue(ok["success"], ok)
        sender.assert_called_once()

        # mismatch generation mode only
        preview2 = laser_workflow_tool.run_laser_workflow_action(
            action="preview",
            source_type="image_summary",
            summary_path=summary_path,
            workflows_dir=self.workflows_dir,
        )
        sender2 = Mock()
        denied = laser_workflow_tool.run_laser_workflow_action(
            action="confirm_send",
            workflow_id=preview2["result"]["workflow_id"],
            confirmed=True,
            workflows_dir=self.workflows_dir,
            execution_sender=sender2,
            mode="outline",
            connection_mode="serial",
            port="COM3",
        )
        self.assertFalse(denied["success"])
        sender2.assert_not_called()

        # image generate path keeps M4 vs raster separate
        image_file = os.path.join(self.temp_dir, "input-dual.png")
        Path(image_file).write_bytes(b"png")
        gcode2 = os.path.join(self.temp_dir, "gen-dual.gcode")
        Path(gcode2).write_text("G21\nG1 X2\n", encoding="utf-8")
        ai_runner = Mock(
            return_value={
                "success": True,
                "result": {
                    "summary_path": os.path.join(self.temp_dir, "summary-dual.json"),
                    "summary": {
                        "contract_version": 1,
                        "recommendation_status": "single_recommendation",
                        "can_send": True,
                        "gcode_path": gcode2,
                        "material": "椴木",
                        "thickness_mm": 3,
                        "laser_mode": "M4",
                        "mode": "raster",
                        "task_type": "engrave_photo",
                        "power": 120,
                        "feed_rate": 1500,
                        "passes": 1,
                        "raster": {
                            "pixel_size_mm": 0.2,
                            "scan_direction": "horizontal",
                            "dither_algorithm": "floyd_steinberg",
                            "output_strategy": "scanline",
                        },
                        "auto_raster_profile": {"name": "quality"},
                    },
                },
            }
        )
        gen = laser_workflow_tool.run_laser_workflow_action(
            action="preview",
            source_type="image",
            image_file=image_file,
            material="椴木",
            thickness_mm=3,
            laser_mode="M4",
            mode="raster",
            workflows_dir=self.workflows_dir,
            ai_runner=ai_runner,
        )
        self.assertTrue(gen["success"], gen)
        gen_snap = self.load_workflow(gen["result"]["workflow_id"])["confirmation_snapshot"]
        self.assertEqual(gen_snap["laser_mode"], "M4")
        self.assertEqual(gen_snap["mode"], "raster")
        sender3 = Mock(return_value={"success": True, "job_id": "job-gen", "status": "pending"})
        ok_gen = laser_workflow_tool.run_laser_workflow_action(
            action="confirm_send",
            workflow_id=gen["result"]["workflow_id"],
            confirmed=True,
            workflows_dir=self.workflows_dir,
            execution_sender=sender3,
            mode="raster",
            connection_mode="serial",
            port="COM3",
        )
        self.assertTrue(ok_gen["success"], ok_gen)
        sender3.assert_called_once()

    # --- OpenCode R3 regression matrix ---

    def test_r3_sending_rejects_production_update_and_preview(self):
        preview = self._preview_text(connection_mode="serial", port="COM1")
        workflow_id = preview["result"]["workflow_id"]
        sender = Mock(return_value={"success": True, "job_id": "job-send", "status": "pending"})
        sent = laser_workflow_tool.run_laser_workflow_action(
            action="confirm_send",
            workflow_id=workflow_id,
            confirmed=True,
            workflows_dir=self.workflows_dir,
            execution_sender=sender,
        )
        self.assertTrue(sent["success"], sent)
        workflow = self.load_workflow(workflow_id)
        self.assertEqual(workflow["status"], "sending")
        self.assertEqual(workflow["runtime_job"]["job_id"], "job-send")

        updated = laser_workflow_tool.run_laser_workflow_action(
            action="update",
            workflow_id=workflow_id,
            material="卡纸",
            workflows_dir=self.workflows_dir,
        )
        self.assertFalse(updated["success"])
        self.assertEqual(updated.get("error_code"), "workflow_busy")
        workflow = self.load_workflow(workflow_id)
        self.assertEqual(workflow["status"], "sending")
        self.assertEqual(workflow["runtime_job"]["job_id"], "job-send")
        self.assertIn("status", workflow["next_actions"])
        self.assertIn("cancel", workflow["next_actions"])
        self.assertEqual(workflow["input"]["material"], "椴木")

        repreview = laser_workflow_tool.run_laser_workflow_action(
            action="preview",
            workflow_id=workflow_id,
            workflows_dir=self.workflows_dir,
            text_generator=self.fake_text_preview,
        )
        self.assertFalse(repreview["success"])
        self.assertEqual(repreview.get("error_code"), "workflow_busy")
        self.assertEqual(self.load_workflow(workflow_id)["status"], "sending")

    def test_r3_connection_update_cannot_replace_preview_bound_port(self):
        preview = self._preview_text(connection_mode="serial", port="COM1")
        workflow_id = preview["result"]["workflow_id"]
        updated = laser_workflow_tool.run_laser_workflow_action(
            action="update",
            workflow_id=workflow_id,
            port="COM9",
            workflows_dir=self.workflows_dir,
        )
        self.assertFalse(updated["success"])
        self.assertEqual(updated.get("error_code"), "confirmation_mismatch")
        workflow = self.load_workflow(workflow_id)
        self.assertEqual(workflow["status"], "preview_ready")
        self.assertEqual(workflow["input"].get("port"), "COM1")
        self.assertEqual(workflow["confirmation_snapshot"]["connection"]["serial_port"], "COM1")

        sender = Mock(return_value={"success": True, "job_id": "job-com1", "status": "pending"})
        sent = laser_workflow_tool.run_laser_workflow_action(
            action="confirm_send",
            workflow_id=workflow_id,
            confirmed=True,
            workflows_dir=self.workflows_dir,
            execution_sender=sender,
        )
        self.assertTrue(sent["success"], sent)
        kwargs = sender.call_args.kwargs
        self.assertEqual(kwargs.get("port"), "COM1")

    def test_r3_image_nested_raster_strategy_is_frozen(self):
        preview, _gcode, _path, _summary = self._preview_image_summary()
        self.assertTrue(preview["success"], preview)
        snap = self.load_workflow(preview["result"]["workflow_id"])["confirmation_snapshot"]
        self.assertEqual(float(snap["pixel_size_mm"]), 0.2)
        self.assertEqual(snap["raster_scan_direction"], "horizontal")
        self.assertEqual(snap["raster_quality_strategy"], "quality")
        self.assertEqual(snap["laser_mode"], "M4")
        self.assertEqual(snap["mode"], "raster")
        self.assertEqual(float(snap["power"]), 120.0)
        # power must NOT be treated as percent
        self.assertIn(snap.get("power_percent"), ("", None))

        sender = Mock(return_value={"success": True, "job_id": "job-nested", "status": "pending"})
        ok = laser_workflow_tool.run_laser_workflow_action(
            action="confirm_send",
            workflow_id=preview["result"]["workflow_id"],
            confirmed=True,
            workflows_dir=self.workflows_dir,
            execution_sender=sender,
            raster_scan_direction="horizontal",
            pixel_size_mm=0.2,
            connection_mode="serial",
            port="COM3",
        )
        self.assertTrue(ok["success"], ok)
        sender.assert_called_once()

        sender2 = Mock()
        denied = laser_workflow_tool.run_laser_workflow_action(
            action="confirm_send",
            workflow_id=preview["result"]["workflow_id"],
            confirmed=True,
            workflows_dir=self.workflows_dir,
            execution_sender=sender2,
            raster_scan_direction="vertical",
            connection_mode="serial",
            port="COM3",
        )
        # first confirm already moved state; re-preview for mismatch case
        preview2, _g2, _p2, _s2 = self._preview_image_summary()
        denied = laser_workflow_tool.run_laser_workflow_action(
            action="confirm_send",
            workflow_id=preview2["result"]["workflow_id"],
            confirmed=True,
            workflows_dir=self.workflows_dir,
            execution_sender=sender2,
            raster_scan_direction="vertical",
            connection_mode="serial",
            port="COM3",
        )
        self.assertFalse(denied["success"])
        sender2.assert_not_called()

    def test_r3_confirm_rejects_unfrozen_production_overrides(self):
        preview = self._preview_text()
        cases = (
            {"text": "另一段文字"},
            {"source_type": "prepared_gcode"},
            {"width_mm": 99},
            {"font_size": 64},
        )
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                sender = Mock()
                denied = laser_workflow_tool.run_laser_workflow_action(
                    action="confirm_send",
                    workflow_id=preview["result"]["workflow_id"],
                    confirmed=True,
                    workflows_dir=self.workflows_dir,
                    execution_sender=sender,
                    connection_mode="serial",
                    port="COM3",
                    **kwargs,
                )
                self.assertFalse(denied["success"], denied)
                self.assertEqual(denied.get("error_code"), "confirmation_mismatch")
                sender.assert_not_called()

        preview_img, _g, _p, _s = self._preview_image_summary()
        sender = Mock()
        denied = laser_workflow_tool.run_laser_workflow_action(
            action="confirm_send",
            workflow_id=preview_img["result"]["workflow_id"],
            confirmed=True,
            workflows_dir=self.workflows_dir,
            execution_sender=sender,
            summary_path=os.path.join(self.temp_dir, "other-summary.json"),
            connection_mode="serial",
            port="COM3",
        )
        self.assertFalse(denied["success"])
        sender.assert_not_called()

        prep, _ = self._preview_prepared()
        sender2 = Mock()
        denied2 = laser_workflow_tool.run_laser_workflow_action(
            action="confirm_send",
            workflow_id=prep["result"]["workflow_id"],
            confirmed=True,
            workflows_dir=self.workflows_dir,
            execution_sender=sender2,
            width_mm=99,
            connection_mode="serial",
            port="COM3",
        )
        self.assertFalse(denied2["success"])
        sender2.assert_not_called()

    def test_r4_unconfirmed_manual_params_do_not_reach_generator_or_persist(self):
        calls = {}

        def generator(**kwargs):
            calls.update(kwargs)
            return self.fake_text_preview(**kwargs)

        result = laser_workflow_tool.run_laser_workflow_action(
            action="preview",
            source_type="text",
            text="佳佳",
            material="椴木",
            thickness_mm=3,
            power_percent=88,
            feed_rate=999,
            passes=7,
            pixel_size_mm=0.99,
            laser_max_power=777,
            workflows_dir=self.workflows_dir,
            text_generator=generator,
        )
        self.assertTrue(result["success"], result)
        for key in ("power_percent", "feed_rate", "passes", "pixel_size_mm", "laser_max_power"):
            self.assertIsNone(calls.get(key), key)
        workflow = self.load_workflow(result["result"]["workflow_id"])
        for key in ("power_percent", "feed_rate", "passes", "pixel_size_mm", "laser_max_power", "manual_params_confirmed"):
            self.assertNotIn(key, workflow["input"])
        self.assertNotIn("请人工核对", result["result"]["speech"])
        self.assertNotIn("系统没有自动识别真实材料", result["result"]["speech"])
        self.assertEqual(workflow["input"].get("material"), "椴木")
        self.assertEqual(float(workflow["input"].get("thickness_mm")), 3)

    def test_r4_confirmed_manual_params_reach_generator(self):
        calls = {}

        def generator(**kwargs):
            calls.update(kwargs)
            return self.fake_text_preview(**kwargs)

        result = laser_workflow_tool.run_laser_workflow_action(
            action="preview",
            source_type="text",
            text="佳佳",
            material="椴木",
            thickness_mm=3,
            power_percent=55,
            feed_rate=1600,
            passes=2,
            manual_params_confirmed=True,
            workflows_dir=self.workflows_dir,
            text_generator=generator,
        )
        self.assertTrue(result["success"], result)
        self.assertEqual(calls.get("power_percent"), 55)
        self.assertEqual(calls.get("feed_rate"), 1600)
        self.assertEqual(calls.get("passes"), 2)
        workflow = self.load_workflow(result["result"]["workflow_id"])
        self.assertTrue(workflow["input"].get("manual_params_confirmed"))
        self.assertEqual(workflow["input"].get("power_percent"), 55)

    def test_r4_material_safety_soft_card_pvc_and_ambiguous_plastic(self):
        cases = (
            ("软卡", "material_clarify", "clarify"),
            ("白色软卡", "material_clarify", "clarify"),
            ("塑料软卡", "material_clarify", "clarify"),
            ("PVC", "material_blocked", "block"),
            ("PVC软卡", "material_blocked", "block"),
            ("聚氯乙烯软卡", "material_blocked", "block"),
            ("塑料", "material_clarify", "clarify"),
            ("聚氯乙烯", "material_blocked", "block"),
        )
        for material, error_code, decision in cases:
            with self.subTest(material=material):
                generator = Mock(side_effect=AssertionError("generator must not run for unsafe material"))
                sender = Mock()
                result = laser_workflow_tool.run_laser_workflow_action(
                    action="preview",
                    source_type="text",
                    text="佳佳",
                    material=material,
                    thickness_mm=3,
                    workflows_dir=self.workflows_dir,
                    text_generator=generator,
                )
                self.assertFalse(result["success"], result)
                self.assertEqual(result.get("error_code"), error_code)
                self.assertEqual((result.get("detail") or {}).get("decision"), decision)
                detail_workflow = (result.get("detail") or {}).get("workflow") or {}
                self.assertNotIn("confirm_send", detail_workflow.get("next_actions") or [])
                self.assertNotEqual(detail_workflow.get("status"), "preview_ready")
                generator.assert_not_called()
                sender.assert_not_called()

    def test_r4_paper_soft_card_passes_safety_gate_to_generator(self):
        """纸质软卡不得再因 soft_card_ambiguous 被 workflow 拦截；仍走材料库/生成路径。"""
        calls = {}

        def generator(**kwargs):
            calls.update(kwargs)
            return self.fake_text_preview(**kwargs)

        result = laser_workflow_tool.run_laser_workflow_action(
            action="preview",
            source_type="text",
            text="佳佳",
            material="纸质软卡",
            thickness_mm=1,
            workflows_dir=self.workflows_dir,
            text_generator=generator,
        )
        self.assertTrue(result["success"], result)
        self.assertEqual(calls.get("material"), "纸质软卡")
        workflow = self.load_workflow(result["result"]["workflow_id"])
        self.assertEqual(workflow["status"], "preview_ready")
        self.assertEqual(workflow["input"].get("material"), "纸质软卡")
        self.assertNotIn("请人工核对", result["result"]["speech"])
        self.assertNotIn("系统没有自动识别真实材料", result["result"]["speech"])

    def test_r4_update_stale_manual_params_are_stripped_without_confirmation(self):
        created = laser_workflow_tool.run_laser_workflow_action(
            action="create",
            source_type="text",
            text="佳佳",
            material="椴木",
            thickness_mm=3,
            power_percent=40,
            feed_rate=1111,
            manual_params_confirmed=True,
            workflows_dir=self.workflows_dir,
        )
        self.assertTrue(created["success"], created)
        workflow_id = created["result"]["workflow_id"]
        workflow = self.load_workflow(workflow_id)
        self.assertEqual(workflow["input"].get("power_percent"), 40)

        updated = laser_workflow_tool.run_laser_workflow_action(
            action="update",
            workflow_id=workflow_id,
            manual_params_confirmed=False,
            power_percent=90,
            feed_rate=2222,
            workflows_dir=self.workflows_dir,
        )
        self.assertTrue(updated["success"], updated)
        workflow = self.load_workflow(workflow_id)
        self.assertNotIn("power_percent", workflow["input"])
        self.assertNotIn("feed_rate", workflow["input"])
        self.assertNotIn("manual_params_confirmed", workflow["input"])

    def test_r5_confirmed_string_false_does_not_call_sender(self):
        preview = self._preview_text()
        for value in (False, "false", "FALSE", "0", "off", "no", "", None):
            with self.subTest(confirmed=value):
                sender = Mock(return_value={"success": True, "job_id": "should-not-run", "status": "pending"})
                kwargs = {
                    "action": "confirm_send",
                    "workflow_id": preview["result"]["workflow_id"],
                    "workflows_dir": self.workflows_dir,
                    "execution_sender": sender,
                    "connection_mode": "serial",
                    "port": "COM3",
                }
                if value is not None:
                    kwargs["confirmed"] = value
                result = laser_workflow_tool.run_laser_workflow_action(**kwargs)
                self.assertFalse(result["success"], result)
                self.assertIn("confirmed", str(result.get("result", "")).lower())
                sender.assert_not_called()
                workflow = self.load_workflow(preview["result"]["workflow_id"])
                self.assertEqual(workflow["status"], "preview_ready")

    def test_r2_direct_confirm_send_workflow_string_false_zero_sender(self):
        """Direct confirm_send_workflow entry must gate confirmed itself (not only MCP wrapper)."""
        preview = self._preview_text()
        workflow_id = preview["result"]["workflow_id"]
        false_values = (False, None, "", "false", "FALSE", "0", "off", "no")
        for value in false_values:
            with self.subTest(confirmed=value):
                sender = Mock(return_value={"success": True, "job_id": "should-not-run", "status": "pending"})
                result = laser_workflow_tool.confirm_send_workflow(
                    workflow_id=workflow_id,
                    confirmed=value,
                    workflows_dir=self.workflows_dir,
                    execution_sender=sender,
                    connection_mode="serial",
                    port="COM3",
                )
                self.assertFalse(result["success"], result)
                self.assertIn("confirmed", str(result.get("result", "")).lower())
                sender.assert_not_called()
                workflow = self.load_workflow(workflow_id)
                self.assertEqual(workflow["status"], "preview_ready")

        sender_ok = Mock(return_value={"success": True, "job_id": "ok-job", "status": "pending"})
        ok = laser_workflow_tool.confirm_send_workflow(
            workflow_id=workflow_id,
            confirmed=True,
            workflows_dir=self.workflows_dir,
            execution_sender=sender_ok,
            connection_mode="serial",
            port="COM3",
        )
        self.assertTrue(ok["success"], ok)
        sender_ok.assert_called_once()

    def test_r5_image_summary_pvc_blocked_after_summary_read(self):
        gcode_file = os.path.join(self.temp_dir, "pvc-summary.gcode")
        Path(gcode_file).write_text("G21\nG1 X1\n", encoding="utf-8")
        summary_path, _summary = self._write_sendable_summary(
            gcode_file,
            material="PVC",
            thickness_mm=2,
        )
        sender = Mock()
        result = laser_workflow_tool.run_laser_workflow_action(
            action="preview",
            source_type="image_summary",
            summary_path=summary_path,
            workflows_dir=self.workflows_dir,
        )
        self.assertFalse(result["success"], result)
        self.assertEqual(result.get("error_code"), "material_blocked")
        detail_workflow = (result.get("detail") or {}).get("workflow") or {}
        self.assertNotEqual(detail_workflow.get("status"), "preview_ready")
        self.assertFalse(detail_workflow.get("confirmation_snapshot"))
        self.assertIn("PVC", result.get("speech", "") + str(result.get("result", "")))
        sender.assert_not_called()

        # soft card / ambiguous plastic in summary also blocked from sendable preview
        for material, error_code in (("软卡", "material_clarify"), ("塑料", "material_clarify")):
            with self.subTest(material=material):
                path, _ = self._write_sendable_summary(
                    gcode_file,
                    material=material,
                    thickness_mm=1.5,
                )
                denied = laser_workflow_tool.run_laser_workflow_action(
                    action="preview",
                    source_type="image_summary",
                    summary_path=path,
                    workflows_dir=self.workflows_dir,
                )
                self.assertFalse(denied["success"], denied)
                self.assertEqual(denied.get("error_code"), error_code)
                wf = (denied.get("detail") or {}).get("workflow") or {}
                self.assertNotEqual(wf.get("status"), "preview_ready")

    def test_r5_image_summary_speech_uses_summary_material_thickness(self):
        gcode_file = os.path.join(self.temp_dir, "speech-summary.gcode")
        Path(gcode_file).write_text("G21\nG1 X1\n", encoding="utf-8")
        summary_path, _ = self._write_sendable_summary(
            gcode_file,
            material="椴木",
            thickness_mm=2.5,
        )
        result = laser_workflow_tool.run_laser_workflow_action(
            action="preview",
            source_type="image_summary",
            summary_path=summary_path,
            workflows_dir=self.workflows_dir,
        )
        self.assertTrue(result["success"], result)
        speech = result["result"]["speech"]
        # Status speech stays on the single preview summary; no second material-check sentence.
        self.assertIn("预览已生成", speech)
        self.assertNotIn("请人工核对", speech)
        self.assertNotIn("系统没有自动识别真实材料", speech)
        self.assertEqual(result["result"]["input"]["material"], "椴木")
        self.assertEqual(float(result["result"]["input"]["thickness_mm"]), 2.5)

    def test_r5_material_change_clears_sticky_manual_confirmation(self):
        created = laser_workflow_tool.run_laser_workflow_action(
            action="create",
            source_type="text",
            text="佳佳",
            material="椴木",
            thickness_mm=3,
            power_percent=55,
            feed_rate=1200,
            manual_params_confirmed=True,
            workflows_dir=self.workflows_dir,
        )
        self.assertTrue(created["success"], created)
        workflow_id = created["result"]["workflow_id"]
        workflow = self.load_workflow(workflow_id)
        self.assertTrue(workflow["input"].get("manual_params_confirmed"))
        self.assertEqual(workflow["input"].get("power_percent"), 55)

        # Material changes without a fresh confirmation must drop sticky manual params.
        updated = laser_workflow_tool.run_laser_workflow_action(
            action="update",
            workflow_id=workflow_id,
            material="卡纸",
            thickness_mm=1,
            workflows_dir=self.workflows_dir,
        )
        self.assertTrue(updated["success"], updated)
        workflow = self.load_workflow(workflow_id)
        self.assertEqual(workflow["input"].get("material"), "卡纸")
        self.assertEqual(float(workflow["input"].get("thickness_mm")), 1.0)
        self.assertNotIn("manual_params_confirmed", workflow["input"])
        self.assertNotIn("power_percent", workflow["input"])
        self.assertNotIn("feed_rate", workflow["input"])


if __name__ == "__main__":
    unittest.main()
