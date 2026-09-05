import unittest

from core.laser_runtime.models import (
    ConnectionSpec,
    LaserError,
    OperationResult,
    PreviewSnapshot,
    ProcessingSpec,
    TaskRecord,
    canonical_fingerprint,
    normalize_record_id,
)


class LaserRuntimeModelsTests(unittest.TestCase):
    def test_task_kinds_round_trip_with_stable_public_ids(self):
        for kind in ("text", "image", "calibration", "prepared_gcode"):
            with self.subTest(kind=kind):
                task = TaskRecord.create(
                    kind,
                    request={"source": kind},
                    processing=ProcessingSpec(
                        material="椴木",
                        thickness_mm=3.0,
                        laser_mode="engrave",
                        engraving_mode="raster",
                        params={"feed_rate": 1200},
                    ),
                    connection=ConnectionSpec(mode="serial", baudrate=115200),
                )

                restored = TaskRecord.from_dict(task.to_dict())

                self.assertEqual(restored, task)
                self.assertEqual(restored.task_id, task.record_id)
                self.assertEqual(restored.workflow_id, f"wf_{task.record_id}")
                self.assertEqual(restored.calibration_id, task.record_id)
                self.assertEqual(normalize_record_id(restored.workflow_id), task.record_id)

    def test_canonical_fingerprint_normalizes_key_order_and_integral_floats(self):
        first = {
            "params": {"passes": 1, "feed_rate": 1200.0},
            "thickness_mm": 3,
        }
        second = {
            "thickness_mm": 3.0,
            "params": {"feed_rate": 1200, "passes": 1.0},
        }

        self.assertEqual(canonical_fingerprint(first), canonical_fingerprint(second))

    def test_preview_fingerprint_includes_gcode_content_digest(self):
        locked = {"material": "椴木", "params": {"feed_rate": 1200}}

        first = PreviewSnapshot.create(locked, "a" * 64)
        second = PreviewSnapshot.create(locked, "b" * 64)

        self.assertNotEqual(first.fingerprint, second.fingerprint)

    def test_operation_result_exposes_typed_failure(self):
        error = LaserError("config_invalid", "端口配置无效", {"field": "LASER_NETWORK_HTTP_PORT"})
        result = OperationResult.failure(error)

        self.assertFalse(result.success)
        self.assertIs(result.error, error)
        self.assertIsNone(result.value)


if __name__ == "__main__":
    unittest.main()
