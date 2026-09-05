import unittest
from unittest.mock import patch

from tools import laser_safe_action_tool


class FakeMcp:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorator(func):
            self.tools[func.__name__] = func
            return func

        return decorator


class LaserSafeActionToolTests(unittest.TestCase):
    def test_register_tool_exposes_safe_action_tool(self):
        fake = FakeMcp()

        laser_safe_action_tool.register_tool(fake)

        self.assertIn("laser_safe_action_tool", fake.tools)

    def test_status_routes_to_network_readonly_command_without_confirmation(self):
        with patch.object(
            laser_safe_action_tool.laser_network_grbl_tool,
            "send_network_command",
            return_value={"success": True, "result": {"response": "<Idle>"}},
        ) as send_mock:
            result = laser_safe_action_tool.run_laser_safe_action(
                "status", connection_mode="network", host="laser.local"
            )

        self.assertTrue(result["success"], result)
        send_mock.assert_called_once()
        self.assertEqual(send_mock.call_args.kwargs["command"], "?")
        self.assertFalse(send_mock.call_args.kwargs["confirmed"])

    def test_network_missing_host_fails_before_serial_or_network_send(self):
        result = laser_safe_action_tool.run_laser_safe_action(
            "unlock", connection_mode="network", confirmed=True
        )

        self.assertFalse(result["success"], result)
        self.assertIn("host", result["result"])

    def test_hazardous_serial_action_requires_confirmation(self):
        with patch.object(
            laser_safe_action_tool.laser_grbl_tool, "_load_serial"
        ) as load_serial_mock:
            result = laser_safe_action_tool.run_laser_safe_action(
                "unlock", connection_mode="serial", confirmed=False
            )

        self.assertFalse(result["success"], result)
        self.assertTrue(result["detail"]["confirmation_required"])
        load_serial_mock.assert_not_called()

    def test_confirmed_relative_move_routes_to_serial_helper(self):
        with patch.object(
            laser_safe_action_tool.laser_grbl_tool,
            "send_serial_command",
            return_value={"success": True, "result": "sent", "detail": {}},
        ) as send_mock:
            result = laser_safe_action_tool.run_laser_safe_action(
                "relative_move",
                connection_mode="serial",
                confirmed=True,
                x=1.5,
                y=2,
                feed_rate=600,
            )

        self.assertTrue(result["success"], result)
        send_mock.assert_called_once()
        self.assertEqual(send_mock.call_args.args[0], "G91 G0 X1.5 Y2 F600")
        self.assertTrue(send_mock.call_args.kwargs["confirmed"])

    def test_move_requires_at_least_one_axis(self):
        result = laser_safe_action_tool.run_laser_safe_action(
            "absolute_move", connection_mode="serial", confirmed=True
        )

        self.assertFalse(result["success"], result)
        self.assertIn("x/y/z", result["result"])

    def test_relative_move_allows_negative_axis(self):
        with patch.object(
            laser_safe_action_tool.laser_grbl_tool,
            "send_serial_command",
            return_value={"success": True, "result": "sent", "detail": {}},
        ) as send_mock:
            result = laser_safe_action_tool.run_laser_safe_action(
                "relative_move", connection_mode="serial", confirmed=True, x=-1
            )

        self.assertTrue(result["success"], result)
        self.assertEqual(send_mock.call_args.args[0], "G91 G0 X-1")

    def test_absolute_move_rejects_negative_axis(self):
        result = laser_safe_action_tool.run_laser_safe_action(
            "absolute_move", connection_mode="serial", confirmed=True, x=-1
        )

        self.assertFalse(result["success"], result)
        self.assertIn("不能小于", result["result"])

    def test_invalid_default_connection_mode_fails(self):
        with patch.object(
            laser_safe_action_tool.laser_execution,
            "DEFAULT_CONNECTION_MODE",
            "netwrok",
        ):
            result = laser_safe_action_tool.run_laser_safe_action("status")

        self.assertFalse(result["success"], result)
        self.assertIn("LASER_DEFAULT_CONNECTION_MODE", result["result"])


if __name__ == "__main__":
    unittest.main()
