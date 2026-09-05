import os
import tempfile
import unittest
from unittest.mock import patch

from tools import lasergrbl_gui_tool


class FakePosition:
    def __init__(self, x, y):
        self.x = x
        self.y = y


class FakePyAutoGui:
    def __init__(self, position=(0, 0)):
        self.calls = []
        self._position = FakePosition(*position)

    def click(self, x, y):
        self.calls.append(("click", x, y))

    def hotkey(self, *keys):
        self.calls.append(("hotkey", keys))

    def press(self, key):
        self.calls.append(("press", key))

    def write(self, text, interval=0):
        self.calls.append(("write", text, interval))

    def position(self):
        return self._position


class FakeWindow:
    left = 100
    top = 200
    width = 800
    height = 600
    title = "LaserGRBL"


class LaserGrblGuiToolTests(unittest.TestCase):
    def test_normalize_port_strips_and_uppercases(self):
        self.assertEqual(lasergrbl_gui_tool._normalize_port(" com7 "), "COM7")

    def test_chinese_calibration_targets_are_supported(self):
        self.assertEqual(lasergrbl_gui_tool._normalize_calibration_target("串口下拉框"), "port")
        self.assertEqual(lasergrbl_gui_tool._normalize_calibration_target("连接按钮"), "connect")
        self.assertEqual(lasergrbl_gui_tool._normalize_calibration_target("开始按钮"), "start")

    def test_remember_position_saves_relative_coordinates(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            calibration_file = os.path.join(tmp_dir, "calibration.json")
            entry, error = lasergrbl_gui_tool._remember_position(
                FakePyAutoGui(position=(145, 260)),
                FakeWindow(),
                "port",
                calibration_file,
            )

            self.assertIsNone(error)
            self.assertEqual(entry["rel_x"], 45)
            self.assertEqual(entry["rel_y"], 60)
            self.assertEqual(
                lasergrbl_gui_tool._saved_coordinates("port", calibration_file),
                (45, 60),
            )

    def test_remember_position_saves_chinese_connect_target(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            calibration_file = os.path.join(tmp_dir, "calibration.json")
            entry, error = lasergrbl_gui_tool._remember_position(
                FakePyAutoGui(position=(145, 260)),
                FakeWindow(),
                "连接按钮",
                calibration_file,
            )

            self.assertIsNone(error)
            self.assertEqual(entry["rel_x"], 45)
            self.assertEqual(
                lasergrbl_gui_tool._saved_coordinates("connect", calibration_file),
                (45, 60),
            )

    def test_remember_position_rejects_unknown_target(self):
        entry, error = lasergrbl_gui_tool._remember_position(
            FakePyAutoGui(position=(145, 260)),
            FakeWindow(),
            "unknown",
        )

        self.assertIsNone(entry)
        self.assertIn("target", error)

    def test_select_port_uses_win32_combo_when_available(self):
        pyautogui = FakePyAutoGui()

        with patch.object(lasergrbl_gui_tool, "_select_port_by_win32", return_value=(True, None)):
            error = lasergrbl_gui_tool._select_port(
                pyautogui,
                FakeWindow(),
                "com7",
                "",
                -1,
                -1,
                0,
            )

        self.assertIsNone(error)
        self.assertEqual(pyautogui.calls, [])

    def test_select_port_uses_saved_coordinates_when_win32_fails(self):
        pyautogui = FakePyAutoGui()

        with (
            patch.object(lasergrbl_gui_tool, "_select_port_by_win32", return_value=(False, "not found")),
            patch.object(lasergrbl_gui_tool, "_saved_coordinates", return_value=(15, 25)),
        ):
            error = lasergrbl_gui_tool._select_port(
                pyautogui,
                FakeWindow(),
                "com7",
                "",
                -1,
                -1,
                0,
            )

        self.assertIsNone(error)
        self.assertEqual(
            pyautogui.calls,
            [
                ("click", 115, 225),
                ("hotkey", ("ctrl", "a")),
                ("write", "COM7", 0.02),
                ("press", "enter"),
            ],
        )

    def test_select_port_falls_back_to_relative_coordinates(self):
        pyautogui = FakePyAutoGui()

        with patch.object(
            lasergrbl_gui_tool,
            "_select_port_by_win32",
            return_value=(False, "not found"),
        ):
            error = lasergrbl_gui_tool._select_port(
                pyautogui,
                FakeWindow(),
                "com7",
                "",
                15,
                25,
                0,
            )

        self.assertIsNone(error)
        self.assertEqual(
            pyautogui.calls,
            [
                ("click", 115, 225),
                ("hotkey", ("ctrl", "a")),
                ("write", "COM7", 0.02),
                ("press", "enter"),
            ],
        )

    def test_select_port_requires_port(self):
        error = lasergrbl_gui_tool._select_port(
            FakePyAutoGui(),
            FakeWindow(),
            "",
            "",
            -1,
            -1,
            0,
        )

        self.assertIn("请提供 port", error)

    def test_select_port_requires_focus_method_when_win32_fails(self):
        with (
            patch.object(lasergrbl_gui_tool, "_select_port_by_win32", return_value=(False, "not found")),
            patch.object(lasergrbl_gui_tool, "_saved_coordinates", return_value=(-1, -1)),
        ):
            error = lasergrbl_gui_tool._select_port(
                FakePyAutoGui(),
                FakeWindow(),
                "COM7",
                "",
                -1,
                -1,
                0,
            )

        self.assertIn("port_rel_x/port_rel_y", error)

    def test_connect_device_uses_saved_coordinates(self):
        pyautogui = FakePyAutoGui()

        with patch.object(lasergrbl_gui_tool, "_saved_coordinates", return_value=(15, 25)):
            error = lasergrbl_gui_tool._connect_device(
                pyautogui,
                FakeWindow(),
                "",
                -1,
                -1,
            )

        self.assertIsNone(error)
        self.assertEqual(pyautogui.calls, [("click", 115, 225)])

    def test_connect_device_uses_hotkey_when_provided(self):
        pyautogui = FakePyAutoGui()

        error = lasergrbl_gui_tool._connect_device(
            pyautogui,
            FakeWindow(),
            "ctrl+shift+c",
            -1,
            -1,
        )

        self.assertIsNone(error)
        self.assertEqual(pyautogui.calls, [("hotkey", ("ctrl", "shift", "c"))])

    def test_connect_device_clicks_relative_coordinates(self):
        pyautogui = FakePyAutoGui()

        error = lasergrbl_gui_tool._connect_device(
            pyautogui,
            FakeWindow(),
            "",
            15,
            25,
        )

        self.assertIsNone(error)
        self.assertEqual(pyautogui.calls, [("click", 115, 225)])

    def test_connect_device_requires_trigger_method(self):
        with patch.object(lasergrbl_gui_tool, "_saved_coordinates", return_value=(-1, -1)):
            error = lasergrbl_gui_tool._connect_device(
                FakePyAutoGui(),
                FakeWindow(),
                "",
                -1,
                -1,
            )

        self.assertIn("缺少连接方式", error)

    def test_start_job_still_requires_trigger_method(self):
        error = lasergrbl_gui_tool._start_job(
            FakePyAutoGui(),
            FakeWindow(),
            "",
            -1,
            -1,
        )

        self.assertIn("缺少开始方式", error)


if __name__ == "__main__":
    unittest.main()
