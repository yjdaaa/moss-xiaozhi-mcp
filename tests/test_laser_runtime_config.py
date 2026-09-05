import os
import re
import unittest
from pathlib import Path

from core.laser_runtime.config import LaserSettings, configuration_diagnostics

REPO_ROOT_FIXTURE = Path("C:/repo").resolve()


class LaserRuntimeConfigTests(unittest.TestCase):
    def test_valid_environment_builds_immutable_settings(self):
        settings = LaserSettings.from_env(
            {
                "LASER_DEFAULT_CONNECTION_MODE": "serial",
                "GRBL_BAUDRATE": "230400",
                "LASER_NETWORK_HTTP_PORT": "8080",
                "LASER_NETWORK_TELNET_PORT": "2323",
                "LASER_NETWORK_TIMEOUT": "2.5",
                "LASERGRBL_WORK_AREA_WIDTH_MM": "90",
                "LASERGRBL_WORK_AREA_HEIGHT_MM": "80",
                "LASER_RUNTIME_DIR": "runtime-test",
            },
            repo_root=REPO_ROOT_FIXTURE,
        )

        self.assertEqual(settings.default_connection_mode, "serial")
        self.assertEqual(settings.baudrate, 230400)
        self.assertEqual(settings.network_http_port, 8080)
        self.assertEqual(settings.network_telnet_port, 2323)
        self.assertEqual(settings.network_timeout, 2.5)
        self.assertEqual(settings.work_area_width_mm, 90.0)
        self.assertEqual(settings.runtime_dir, REPO_ROOT_FIXTURE / "runtime-test")
        self.assertEqual(settings.issues, ())

    def test_invalid_network_values_only_block_network_scope(self):
        settings = LaserSettings.from_env(
            {
                "LASER_NETWORK_HTTP_PORT": "invalid",
                "LASER_NETWORK_TIMEOUT": "0",
                "GRBL_BAUDRATE": "115200",
            },
            repo_root=REPO_ROOT_FIXTURE,
        )

        self.assertTrue(settings.issues_for("network"))
        self.assertFalse(settings.issues_for("serial"))
        diagnostics = configuration_diagnostics(settings)
        self.assertTrue(any(item["field"] == "LASER_NETWORK_HTTP_PORT" for item in diagnostics))
        self.assertNotIn("invalid", repr(diagnostics))

    def test_invalid_connection_mode_blocks_routing_without_hiding_other_tools(self):
        settings = LaserSettings.from_env(
            {"LASER_DEFAULT_CONNECTION_MODE": "netwrok"},
            repo_root=REPO_ROOT_FIXTURE,
        )

        self.assertTrue(settings.issues_for("routing"))
        self.assertFalse(settings.issues_for("serial"))
        self.assertFalse(settings.issues_for("network"))

    def test_non_finite_numbers_are_rejected_in_their_capability_scopes(self):
        settings = LaserSettings.from_env(
            {
                "LASER_NETWORK_TIMEOUT": "nan",
                "LASERGRBL_WORK_AREA_WIDTH_MM": "inf",
            },
            repo_root=REPO_ROOT_FIXTURE,
        )

        self.assertEqual(
            {issue.field for issue in settings.issues_for("network")},
            {"LASER_NETWORK_TIMEOUT"},
        )
        self.assertEqual(
            {issue.field for issue in settings.issues_for("machine")},
            {"LASERGRBL_WORK_AREA_WIDTH_MM"},
        )

    def test_capability_check_returns_typed_config_error_for_requested_scope(self):
        settings = LaserSettings.from_env(
            {"LASER_NETWORK_HTTP_PORT": "not-a-port"},
            repo_root=REPO_ROOT_FIXTURE,
        )

        network_check = settings.check_scopes("network")
        serial_check = settings.check_scopes("serial")

        self.assertFalse(network_check.success)
        self.assertEqual(network_check.error.code, "config_invalid")
        self.assertEqual(
            network_check.error.detail,
            {
                "issues": [
                    {
                        "field": "LASER_NETWORK_HTTP_PORT",
                        "scope": "network",
                        "message": "must be an integer",
                    }
                ]
            },
        )
        self.assertTrue(serial_check.success)

    def test_default_runtime_directory_does_not_use_legacy_directories(self):
        settings = LaserSettings.from_env(
            {
                "LASER_WORKFLOWS_DIR": "legacy-workflows",
                "LASER_TEXT_TASKS_DIR": "legacy-text-tasks",
            },
            repo_root=REPO_ROOT_FIXTURE,
        )

        self.assertEqual(settings.runtime_dir, REPO_ROOT_FIXTURE / ".runtime" / "laser_runtime")

    def test_from_env_uses_supplied_mapping_not_process_environment(self):
        previous = os.environ.get("GRBL_BAUDRATE")
        os.environ["GRBL_BAUDRATE"] = "9600"
        self.addCleanup(self._restore_env, "GRBL_BAUDRATE", previous)

        settings = LaserSettings.from_env({}, repo_root=REPO_ROOT_FIXTURE)

        self.assertEqual(settings.baudrate, 115200)

    def test_laser_tools_do_not_parse_centralized_environment_fields(self):
        tools_dir = Path(__file__).resolve().parents[1] / "tools"
        centralized_fields = {
            "AI_LASER_CANDIDATE_TTL_SECONDS",
            "AI_LASER_GCODE_ASSETS_DIR",
            "AI_LASER_GCODE_OUTPUT_DIR",
            "AI_LASER_GCODE_STATE_PATH",
            "AI_LASER_INPUT_DIR",
            "AI_LASER_MATERIAL_LIBRARY",
            "GRBL_DEFAULT_FILE",
            "GRBL_DEFAULT_PORT",
            "GRBL_BAUDRATE",
            "GRBL_LASER_S_MAX",
            "LASER_CALIBRATION_DIR",
            "LASER_DEFAULT_CONNECTION_MODE",
            "LASER_ENGRAVING_MODE",
            "LASER_MATERIAL_PARAMS_FILE",
            "LASER_MAX_PASSES",
            "LASER_MIN_FEED_RATE",
            "LASER_NETWORK_HOST",
            "LASER_NETWORK_HTTP_PORT",
            "LASER_NETWORK_TELNET_PORT",
            "LASER_NETWORK_TIMEOUT",
            "LASER_PREPARED_GCODE_DIR",
            "LASER_RASTER_OVERSCAN_MM",
            "LASER_RUNTIME_DIR",
            "LASER_TRAVEL_RATE",
            "LASERGRBL_DEFAULT_IMAGE",
            "LASERGRBL_DEFAULT_IMAGE_FIT_BOX_HEIGHT_MM",
            "LASERGRBL_DEFAULT_IMAGE_FIT_BOX_WIDTH_MM",
            "LASERGRBL_JOB_DIR",
            "LASERGRBL_LASER_OPTICAL_POWER_W",
            "LASERGRBL_MACHINE_NAME",
            "LASERGRBL_SAFE_MARGIN_MM",
            "LASERGRBL_WORK_AREA_HEIGHT_MM",
            "LASERGRBL_WORK_AREA_WIDTH_MM",
            "TEXT_IMAGE_OUTPUT_DIR",
        }
        direct_lookup = re.compile(
            r"os\.(?:getenv|environ\.get)\(\s*['\"]([^'\"]+)['\"]"
        )

        duplicate_reads = []
        for module_path in tools_dir.glob("*_tool.py"):
            source = module_path.read_text(encoding="utf-8")
            duplicate_reads.extend(
                f"{module_path.name}:{match.group(1)}"
                for match in direct_lookup.finditer(source)
                if match.group(1) in centralized_fields
            )

        self.assertEqual(duplicate_reads, [])

    @staticmethod
    def _restore_env(name, value):
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


if __name__ == "__main__":
    unittest.main()
