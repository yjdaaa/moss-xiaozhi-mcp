import os
import shutil
import unittest
import uuid
from unittest.mock import patch

from tools import laser_network_grbl_tool


class FakeMcp:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorator(func):
            self.tools[func.__name__] = func
            return func

        return decorator


class FakeHttpResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self.body


class LaserNetworkGrblToolTests(unittest.TestCase):
    def make_temp_dir(self):
        base_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", ".tmp-test")
        )
        os.makedirs(base_dir, exist_ok=True)

        temp_dir = os.path.join(base_dir, f"case-{uuid.uuid4().hex}")
        os.makedirs(temp_dir, exist_ok=False)
        self.addCleanup(shutil.rmtree, temp_dir, ignore_errors=True)
        return temp_dir

    def test_register_tool_exposes_network_tool(self):
        fake = FakeMcp()

        laser_network_grbl_tool.register_tool(fake)

        self.assertIn("laser_network_grbl_tool", fake.tools)

    def test_classify_readonly_motion_laser_and_config_commands(self):
        self.assertEqual(laser_network_grbl_tool.classify_laser_command("$$"), "read_only")
        self.assertEqual(laser_network_grbl_tool.classify_laser_command("G1 X1"), "motion")
        self.assertEqual(laser_network_grbl_tool.classify_laser_command("M4 S100"), "laser")
        self.assertEqual(laser_network_grbl_tool.classify_laser_command("$30=1000"), "config_write")
        self.assertEqual(laser_network_grbl_tool.classify_laser_command("[ESP444]RESTART"), "destructive")

    def test_send_command_dry_run_renders_encoded_http_url(self):
        result = laser_network_grbl_tool.send_network_command(
            command="G1 X1 Y2 S300",
            host="laser.local",
            transport="http",
            confirmed=True,
            dry_run=True,
        )

        self.assertTrue(result["success"], result)
        payload = result["result"]
        self.assertEqual(payload["classification"], "motion")
        self.assertIn("G1%20X1%20Y2%20S300", payload["url"])

    def test_hazardous_command_requires_confirmation(self):
        result = laser_network_grbl_tool.send_network_command(
            command="G0 X10",
            host="laser.local",
            transport="http",
            confirmed=False,
        )

        self.assertFalse(result["success"], result)
        self.assertTrue(result["detail"]["confirmation_required"])
        self.assertEqual(result["detail"]["classification"], "motion")

    def test_readonly_command_can_send_without_confirmation(self):
        urls = []

        def fake_urlopen(url, timeout):
            urls.append((url, timeout))
            return FakeHttpResponse(b"ok")

        result = laser_network_grbl_tool.query_web_command(
            "laser.local", "$$", opener=fake_urlopen
        )

        self.assertTrue(result["success"], result)
        self.assertIn("commandText=%24%24", urls[0][0])

    def test_send_file_dry_run_prepares_without_starting_job(self):
        temp_dir = self.make_temp_dir()
        gcode_file = os.path.join(temp_dir, "job.gcode")
        with open(gcode_file, "w", encoding="utf-8") as file:
            file.write("G21\n")

        with patch.object(
            laser_network_grbl_tool.laser_execution,
            "send_file",
            return_value={
                "success": True,
                "result": {
                    "confirmation_required": True,
                    "transport": "telnet",
                    "transport_policy": "complete network laser jobs always use telnet",
                    "prepared": {"gcode_file": gcode_file, "converted": False},
                },
            },
        ) as send_mock:
            result = laser_network_grbl_tool.send_network_file(
                host="laser.local",
                gcode_file=gcode_file,
                dry_run=True,
            )

        self.assertTrue(result["success"], result)
        self.assertTrue(result["result"]["confirmation_required"])
        self.assertEqual(result["result"]["transport"], "telnet")
        self.assertEqual(
            result["result"]["transport_policy"],
            "complete network laser jobs always use telnet",
        )
        self.assertEqual(result["result"]["prepared"]["gcode_file"], gcode_file)
        send_mock.assert_called_once()
        self.assertEqual(send_mock.call_args.args[1], "network")
        self.assertFalse(send_mock.call_args.kwargs.get("confirmed", False))

    def test_send_file_passes_image_import_options_to_preparer(self):
        prepared = {"success": True, "result": {"gcode_file": "job.gcode", "converted": True}}

        with (
            patch.object(
                laser_network_grbl_tool, "_prepare_network_send_file", return_value=prepared
            ) as prepare_mock,
            patch.object(
                laser_network_grbl_tool.laser_execution,
                "send_file",
                return_value={
                    "success": True,
                    "result": {
                        "confirmation_required": True,
                        "prepared": prepared["result"],
                    },
                },
            ),
        ):
            result = laser_network_grbl_tool.send_network_file(
                host="laser.local",
                image_file="part.png",
                dry_run=True,
                auto_trim=False,
                dpi=200,
                offset_x_mm=3,
                offset_y_mm=4,
                safe_margin_mm=6,
            )

        self.assertTrue(result["success"], result)
        self.assertFalse(prepare_mock.call_args.kwargs["auto_trim"])
        self.assertEqual(prepare_mock.call_args.kwargs["dpi"], 200)
        self.assertEqual(prepare_mock.call_args.kwargs["offset_x_mm"], 3)
        self.assertEqual(prepare_mock.call_args.kwargs["offset_y_mm"], 4)
        self.assertEqual(prepare_mock.call_args.kwargs["safe_margin_mm"], 6)

    def test_send_file_confirmed_starts_background_network_job(self):
        temp_dir = self.make_temp_dir()
        gcode_file = os.path.join(temp_dir, "job.gcode")
        with open(gcode_file, "w", encoding="utf-8") as file:
            file.write("G21\n")

        with patch.object(
            laser_network_grbl_tool.laser_execution,
            "send_file",
            return_value={"success": True, "job_id": "net-job", "status": "pending"},
        ) as send_mock:
            result = laser_network_grbl_tool.send_network_file(
                host="laser.local",
                gcode_file=gcode_file,
                confirmed=True,
            )

        self.assertTrue(result["success"], result)
        self.assertFalse(result["result"]["confirmation_required"])
        self.assertEqual(result["result"]["send_result"]["job_id"], "net-job")
        send_mock.assert_called_once()
        self.assertEqual(send_mock.call_args.args[1], "network")
        self.assertTrue(send_mock.call_args.kwargs.get("confirmed"))

    def test_job_status_and_cancel_delegate_to_laser_execution(self):
        fake = FakeMcp()
        laser_network_grbl_tool.register_tool(fake)

        with patch.object(
            laser_network_grbl_tool.laser_execution,
            "job_status",
            return_value={
                "success": True,
                "result": {"status": "pending", "job_id": "net-1"},
            },
        ) as status_mock:
            result = fake.tools["laser_network_grbl_tool"](
                action="job_status", job_id="net-1"
            )
        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"]["status"], "pending")
        status_mock.assert_called_once_with("net-1", "network")

        with patch.object(
            laser_network_grbl_tool.laser_execution,
            "cancel_job",
            return_value={
                "success": True,
                "result": {"status": "cancelled", "job_id": "net-1"},
            },
        ) as cancel_mock:
            cancelled = fake.tools["laser_network_grbl_tool"](
                action="cancel_job", job_id="net-1"
            )
        self.assertTrue(cancelled["success"], cancelled)
        cancel_mock.assert_called_once_with("net-1", "network")

    def test_send_file_confirmed_surfaces_preflight_failure_from_laser_execution(self):
        temp_dir = self.make_temp_dir()
        gcode_file = os.path.join(temp_dir, "job.gcode")
        with open(gcode_file, "w", encoding="utf-8") as file:
            file.write("G21\n")

        with patch.object(
            laser_network_grbl_tool.laser_execution,
            "send_file",
            return_value={"success": False, "result": "设备在线检查失败"},
        ) as send_mock:
            result = laser_network_grbl_tool.send_network_file(
                host="laser.local",
                gcode_file=gcode_file,
                confirmed=True,
            )

        self.assertFalse(result["success"], result)
        self.assertFalse(result["detail"]["send_result"]["success"])
        self.assertIn("设备在线检查失败", result["result"])
        send_mock.assert_called_once()

    def test_tool_module_does_not_import_private_backend(self):
        source_path = os.path.abspath(laser_network_grbl_tool.__file__)
        with open(source_path, "r", encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotRegex(source, r"(?m)^\s*from core import _laser_execution_backend")
        self.assertNotRegex(source, r"(?m)^\s*import core\._laser_execution_backend")
        self.assertNotIn("--send-file-worker", source)
        self.assertNotIn("def _execute_network_send_file", source)
        self.assertNotIn("def _start_network_send_file_job", source)
        self.assertNotIn("def _send_http_gcode_file", source)
        self.assertNotIn("def _send_telnet_gcode_file", source)
        self.assertNotIn("NETWORK_JOBS_DIR", source)
        self.assertEqual(laser_network_grbl_tool.FORCED_FILE_TRANSPORT, "telnet")


if __name__ == "__main__":
    unittest.main()
