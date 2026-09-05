import os
import unittest
from collections import Counter
from unittest.mock import Mock, patch

from moss_mcp import server as yo_mcp


class FakeMcp:
    def __init__(self):
        self.tools = []

    def tool(self):
        def decorator(func):
            self.tools.append(func.__name__)
            return func

        return decorator


class YoMcpRegistrationTests(unittest.TestCase):
    def test_priority_modules_register_before_other_tools(self):
        registered = []

        def fake_import(module_name):
            module = Mock()
            module.register_tool.side_effect = lambda _mcp, name=module_name: registered.append(name)
            return module

        files = [
            "open_app_tool.py",
            "laser_network_grbl_tool.py",
            "laser_safe_action_tool.py",
            "laser_workflow_tool.py",
            "camera_tool.py",
            "homeasstant_tool.py",
        ]
        with patch.object(yo_mcp, "tools_dir", os.getcwd()), patch.object(
            yo_mcp.os, "listdir", return_value=files
        ), patch.object(yo_mcp, "ENABLED_IP_CAMERA", "false"), patch.object(
            yo_mcp.importlib, "import_module", side_effect=fake_import
        ):
            for module_name in yo_mcp._iter_tool_modules():
                module = yo_mcp.importlib.import_module(module_name)
                if yo_mcp.ENABLED_IP_CAMERA != "true" and module_name == "tools.camera_tool":
                    continue
                if module_name in yo_mcp.DISABLED_TOOLS:
                    continue
                module.register_tool(yo_mcp.mcp)

        self.assertEqual(
            registered,
            [
                "tools.laser_safe_action_tool",
                "tools.laser_workflow_tool",
                "tools.laser_network_grbl_tool",
                "tools.open_app_tool",
            ],
        )

    def test_priority_tools_use_renamed_material_module(self):
        self.assertIn("tools.laser_material_calibration_tool", yo_mcp.PRIORITY_TOOLS)
        self.assertNotIn("tools.laser_calibration_tool", yo_mcp.PRIORITY_TOOLS)
        self.assertNotIn("tools.ai_laser_gcode_tool", yo_mcp.PRIORITY_TOOLS)

    def test_real_register_tool_names_for_renamed_modules(self):
        from tools import laser_asset_gcode_tool, laser_material_calibration_tool

        material_mcp = FakeMcp()
        laser_material_calibration_tool.register_tool(material_mcp)
        self.assertEqual(
            sorted(material_mcp.tools),
            [
                "material_params_tool",
                "recommend_laser_params_tool",
                "run_calibration_grid_tool",
                "select_calibration_cell_tool",
                "start_tuned_job_tool",
            ],
        )

        asset_mcp = FakeMcp()
        laser_asset_gcode_tool.register_tool(asset_mcp)
        self.assertEqual(asset_mcp.tools, ["ai_laser_gcode_tool"])

    def test_default_registration_matches_baseline_public_names(self):
        expected_names = [
            "ai_laser_gcode_tool",
            "check_laser_connection_tool",
            "command_execution_tool",
            "generate_text_image_gcode_tool",
            "generate_text_image_tool",
            "generate_text_laser_task_tool",
            "laser_grbl_tool",
            "laser_network_grbl_tool",
            "laser_safe_action_tool",
            "laser_workflow_tool",
            "material_params_tool",
            "open_app_tool",
            "open_jsjds_website_tool",
            "recommend_laser_params_tool",
            "refine_laser_params_from_feedback_tool",
            "regenerate_text_laser_task_tool",
            "run_calibration_grid_tool",
            "select_calibration_cell_tool",
            "shortcut_key_execution_tool",
            "start_tuned_job_tool",
        ]
        expected_modules = [
            "tools.check_laser_connection_tool",
            "tools.laser_safe_action_tool",
            "tools.laser_workflow_tool",
            "tools.laser_material_calibration_tool",
            "tools.text_laser_task_tool",
            "tools.text_image_gcode_tool",
            "tools.laser_grbl_tool",
            "tools.laser_network_grbl_tool",
            "tools.laser_asset_gcode_tool",
            "tools.command_execution_tool",
            "tools.open_app_tool",
            "tools.shortcut_key_execution_tool",
            "tools.text_image_tool",
        ]

        discovered = list(yo_mcp._iter_tool_modules())
        self.assertNotIn("tools.laser_calibration_tool", discovered)
        self.assertNotIn("tools.ai_laser_gcode_tool", discovered)
        self.assertIn("tools.laser_material_calibration_tool", discovered)
        self.assertIn("tools.laser_asset_gcode_tool", discovered)
        self.assertFalse(os.path.exists(os.path.join(yo_mcp.tools_dir, "laser_calibration_tool.py")))
        self.assertFalse(os.path.exists(os.path.join(yo_mcp.tools_dir, "ai_laser_gcode_tool.py")))

        modules = []
        names = []
        fails = []
        for module_name in discovered:
            # The recorded baseline intentionally represents default camera-disabled registration.
            if module_name == "tools.camera_tool":
                continue
            if module_name in yo_mcp.DISABLED_TOOLS:
                continue
            try:
                module = yo_mcp.importlib.import_module(module_name)
            except Exception as exc:  # pragma: no cover - baseline must load
                fails.append((module_name, str(exc)))
                continue
            if not hasattr(module, "register_tool"):
                continue
            fake = FakeMcp()
            module.register_tool(fake)
            modules.append(module_name)
            names.extend(fake.tools)

        self.assertEqual(fails, [])
        self.assertEqual(len(modules), 13)
        self.assertEqual(sorted(modules), sorted(expected_modules))
        self.assertEqual(sorted(names), expected_names)
        self.assertEqual([name for name, count in Counter(names).items() if count > 1], [])


if __name__ == "__main__":
    unittest.main()
