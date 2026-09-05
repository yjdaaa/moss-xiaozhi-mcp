import os
import shutil
import unittest
import uuid

from core import laser_time_estimate


class LaserTimeEstimateTests(unittest.TestCase):
    def make_temp_dir(self):
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".tmp-test"))
        os.makedirs(base_dir, exist_ok=True)
        temp_dir = os.path.join(base_dir, f"case-{uuid.uuid4().hex}")
        os.makedirs(temp_dir, exist_ok=False)
        self.addCleanup(shutil.rmtree, temp_dir, ignore_errors=True)
        return temp_dir

    def test_g1_s0_blank_motion_uses_feed_rate_not_rapid_rate(self):
        estimate = laser_time_estimate.estimate_gcode_time_seconds(
            "\n".join(
                [
                    "G21",
                    "G90",
                    "G0 F3000",
                    "G0 X0 Y0",
                    "M4 S0",
                    "G1 X10 F600 S0",
                    "G1 X20 S200",
                ]
            )
        )

        self.assertEqual(estimate["method"], "lasergrbl_style_gcode_motion_estimate")
        self.assertAlmostEqual(estimate["non_laser_seconds"], 1.0)
        self.assertAlmostEqual(estimate["laser_seconds"], 1.0)
        self.assertAlmostEqual(estimate["estimated_seconds"], 2.0)

    def test_modal_coordinates_pause_and_relative_motion_are_preserved(self):
        estimate = laser_time_estimate.estimate_gcode_time_seconds(
            "\n".join(
                [
                    "G21 G90",
                    "M3 S100",
                    "G1 X10 F1200",
                    "Y10",
                    "G91",
                    "X10",
                    "G4 P2",
                ]
            )
        )

        self.assertAlmostEqual(estimate["laser_distance_mm"], 30.0)
        self.assertAlmostEqual(estimate["laser_seconds"], 1.5)
        self.assertAlmostEqual(estimate["non_laser_seconds"], 2.0)
        self.assertAlmostEqual(estimate["estimated_seconds"], 3.5)

    def test_g2_g3_ij_arc_uses_arc_length(self):
        estimate = laser_time_estimate.estimate_gcode_time_seconds(
            "\n".join(
                [
                    "G21 G90",
                    "M4 S100",
                    "G1 X10 Y0 F600",
                    "G3 X0 Y10 I-10 J0",
                ]
            )
        )

        self.assertAlmostEqual(estimate["laser_distance_mm"], 10 + 15.708, places=3)
        self.assertAlmostEqual(estimate["laser_seconds"], 2.6, places=1)

    def test_add_time_estimate_fields_adds_short_speech(self):
        temp_dir = self.make_temp_dir()
        gcode_file = os.path.join(temp_dir, "job.gcode")
        with open(gcode_file, "w", encoding="utf-8") as file:
            file.write("G21\nG90\nM4 S100\nG1 X10 F600\n")

        payload = laser_time_estimate.add_time_estimate_fields({}, gcode_file)

        self.assertEqual(payload["time_estimate"]["estimated_seconds"], 1.0)
        self.assertEqual(payload["speech"], "预计雕刻 1 秒")


if __name__ == "__main__":
    unittest.main()
