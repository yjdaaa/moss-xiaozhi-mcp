import unittest
from unittest.mock import patch

from tools import check_laser_connection_tool


class FakeMcp:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorator(func):
            self.tools[func.__name__] = func
            return func

        return decorator


class CheckLaserConnectionToolTests(unittest.TestCase):
    def test_register_tool_exposes_connection_tool(self):
        fake = FakeMcp()

        check_laser_connection_tool.register_tool(fake)

        self.assertIn("check_laser_connection_tool", fake.tools)

    def test_serial_connected_and_network_missing_host_returns_short_summary(self):
        with patch.object(
            check_laser_connection_tool.laser_execution,
            "probe_serial_grbl",
            return_value={
                "success": True,
                "result": {
                    "port": "COM3",
                    "baudrate": 115200,
                    "probe_command": "?",
                    "probe_response": "<Idle>",
                },
            },
        ), patch.object(
            check_laser_connection_tool.laser_network_grbl_tool,
            "DEFAULT_NETWORK_HOST",
            "",
        ), patch.object(
            check_laser_connection_tool.laser_execution,
            "resolve_laser_network_host",
            return_value=(None, "请指定 laser 网络主机 host；不要从历史对话猜设备 IP"),
        ):
            result = check_laser_connection_tool.check_laser_connection()

        self.assertEqual(
            result,
            {
                "serial_connected": True,
                "network_connected": False,
                "summary": "串口已连接，网络未连接",
            },
        )

    def test_network_connected_and_serial_failed_returns_short_summary(self):
        with patch.object(
            check_laser_connection_tool.laser_execution,
            "probe_serial_grbl",
            return_value={"success": False, "result": "未检测到可用的 GRBL 串口"},
        ), patch.object(
            check_laser_connection_tool.laser_execution,
            "resolve_laser_network_host",
            return_value=("laser.local", None),
        ), patch.object(
            check_laser_connection_tool.laser_network_grbl_tool,
            "query_web_command",
            return_value={"success": True, "result": {"response": "Grbl_ESP32"}},
        ):
            result = check_laser_connection_tool.check_laser_connection(
                host="laser.local", transport="http"
            )

        self.assertEqual(result["serial_connected"], False)
        self.assertEqual(result["network_connected"], True)
        self.assertEqual(result["summary"], "串口未连接，网络已连接")
        self.assertNotIn("detail", result)

    def test_both_connected_returns_both_connected_summary(self):
        with patch.object(
            check_laser_connection_tool.laser_execution,
            "probe_serial_grbl",
            return_value={
                "success": True,
                "result": {"port": "COM3", "baudrate": 115200, "probe_command": "?"},
            },
        ), patch.object(
            check_laser_connection_tool.laser_execution,
            "resolve_laser_network_host",
            return_value=("laser.local", None),
        ), patch.object(
            check_laser_connection_tool.laser_network_grbl_tool,
            "query_web_command",
            return_value={"success": True, "result": {"response": "ok"}},
        ):
            result = check_laser_connection_tool.check_laser_connection(
                host="laser.local", transport="http"
            )

        self.assertEqual(result["serial_connected"], True)
        self.assertEqual(result["network_connected"], True)
        self.assertEqual(result["summary"], "串口和网络都已连接")

    def test_both_failed_returns_not_detected_summary(self):
        with patch.object(
            check_laser_connection_tool.laser_execution,
            "probe_serial_grbl",
            return_value={"success": False, "result": "串口失败"},
        ), patch.object(
            check_laser_connection_tool.laser_execution,
            "resolve_laser_network_host",
            return_value=("laser.local", None),
        ), patch.object(
            check_laser_connection_tool.laser_network_grbl_tool,
            "query_telnet_command",
            return_value={"success": False, "result": "Telnet 失败"},
        ):
            result = check_laser_connection_tool.check_laser_connection(host="laser.local")

        self.assertEqual(result["serial_connected"], False)
        self.assertEqual(result["network_connected"], False)
        self.assertEqual(result["summary"], "未检测到串口或网络连接")

    def test_include_detail_keeps_details_out_of_main_fields(self):
        with patch.object(
            check_laser_connection_tool.laser_execution,
            "probe_serial_grbl",
            return_value={
                "success": False,
                "result": "串口失败",
                "detail": {"available_ports": []},
            },
        ), patch.object(
            check_laser_connection_tool.laser_execution,
            "resolve_laser_network_host",
            return_value=("laser.local", None),
        ), patch.object(
            check_laser_connection_tool.laser_network_grbl_tool,
            "query_telnet_command",
            return_value={"success": False, "result": "Telnet 失败"},
        ):
            result = check_laser_connection_tool.check_laser_connection(
                host="laser.local", include_detail=True
            )

        self.assertEqual(result["summary"], "未检测到串口或网络连接")
        self.assertIn("detail", result)
        self.assertEqual(result["detail"]["serial"]["error"], "串口失败")
        self.assertEqual(result["detail"]["network"]["error"], "Telnet 失败")

    def test_telnet_transport_uses_readonly_status_query(self):
        with patch.object(
            check_laser_connection_tool.laser_execution,
            "probe_serial_grbl",
            return_value={"success": False, "result": "串口失败"},
        ), patch.object(
            check_laser_connection_tool.laser_execution,
            "resolve_laser_network_host",
            return_value=("laser.local", None),
        ), patch.object(
            check_laser_connection_tool.laser_network_grbl_tool,
            "query_telnet_command",
            return_value={"success": True, "result": {"response": "<Idle>"}},
        ) as telnet_mock:
            result = check_laser_connection_tool.check_laser_connection(
                host="laser.local", transport="telnet", telnet_port=23, timeout=1
            )

        self.assertTrue(result["network_connected"])
        telnet_mock.assert_called_once_with("laser.local", "?", port=23, timeout=1)


if __name__ == "__main__":
    unittest.main()
