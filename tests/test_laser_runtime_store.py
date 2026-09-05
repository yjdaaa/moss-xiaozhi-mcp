import json
import os
import shutil
import unittest
import uuid
from pathlib import Path

from core.laser_runtime.models import PreviewSnapshot, TaskRecord
from core.laser_runtime.store import RuntimeStore


class LaserRuntimeStoreTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / ".tmp-test" / f"runtime-{uuid.uuid4().hex}"
        root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        self.root = root
        self.store = RuntimeStore(root)

    def test_task_round_trip_uses_new_runtime_directory(self):
        task = TaskRecord.create("text", request={"text": "佳佳"})

        saved = self.store.save_task(task)
        loaded = self.store.load_task(task.workflow_id)

        self.assertTrue(saved.success, saved.error)
        self.assertTrue(loaded.success, loaded.error)
        self.assertEqual(loaded.value, task)
        self.assertTrue((self.root / "tasks" / f"{task.record_id}.json").is_file())

    def test_corrupt_task_returns_state_error_without_overwrite(self):
        task = TaskRecord.create("image")
        task_path = self.root / "tasks" / f"{task.record_id}.json"
        task_path.parent.mkdir(parents=True)
        task_path.write_text("{broken", encoding="utf-8")

        result = self.store.load_task(task.task_id)

        self.assertFalse(result.success)
        self.assertEqual(result.error.code, "state_corrupt")
        self.assertEqual(task_path.read_text(encoding="utf-8"), "{broken")

    def test_task_schema_mismatch_returns_state_corrupt(self):
        task = TaskRecord.create("text")
        task_path = self.root / "tasks" / f"{task.record_id}.json"
        task_path.parent.mkdir(parents=True)
        payload = task.to_dict()
        payload["schema_version"] = 999
        task_path.write_text(json.dumps(payload), encoding="utf-8")

        result = self.store.load_task(task.task_id)

        self.assertFalse(result.success)
        self.assertEqual(result.error.code, "state_corrupt")

    def test_task_identity_mismatch_returns_state_corrupt(self):
        requested = TaskRecord.create("text")
        different = TaskRecord.create("text")
        task_path = self.root / "tasks" / f"{requested.record_id}.json"
        task_path.parent.mkdir(parents=True)
        task_path.write_text(json.dumps(different.to_dict()), encoding="utf-8")

        result = self.store.load_task(requested.task_id)

        self.assertFalse(result.success)
        self.assertEqual(result.error.code, "state_corrupt")

    def test_invalid_nested_task_shape_returns_state_corrupt(self):
        task = TaskRecord.create("image")
        task_path = self.root / "tasks" / f"{task.record_id}.json"
        task_path.parent.mkdir(parents=True)
        payload = task.to_dict()
        payload["processing"] = []
        task_path.write_text(json.dumps(payload), encoding="utf-8")

        result = self.store.load_task(task.task_id)

        self.assertFalse(result.success)
        self.assertEqual(result.error.code, "state_corrupt")

    def test_invalid_nested_task_value_returns_state_corrupt(self):
        task = TaskRecord.create("image")
        task_path = self.root / "tasks" / f"{task.record_id}.json"
        task_path.parent.mkdir(parents=True)
        payload = task.to_dict()
        payload["connection"]["baudrate"] = "not-a-number"
        task_path.write_text(json.dumps(payload), encoding="utf-8")

        result = self.store.load_task(task.task_id)

        self.assertFalse(result.success)
        self.assertEqual(result.error.code, "state_corrupt")

    def test_tampered_preview_snapshot_returns_state_corrupt(self):
        task = TaskRecord.create("prepared_gcode")
        task.preview_snapshot = PreviewSnapshot.create(
            {"gcode_file": "C:/jobs/example.gcode", "feed_rate": 1200},
            "a" * 64,
        )
        self.assertTrue(self.store.save_task(task).success)
        task_path = self.root / "tasks" / f"{task.record_id}.json"
        payload = json.loads(task_path.read_text(encoding="utf-8"))
        payload["preview_snapshot"]["locked"]["feed_rate"] = 2400
        task_path.write_text(json.dumps(payload), encoding="utf-8")

        result = self.store.load_task(task.task_id)

        self.assertFalse(result.success)
        self.assertEqual(result.error.code, "state_corrupt")

    def test_missing_task_returns_not_found(self):
        result = self.store.load_task(uuid.uuid4().hex)

        self.assertFalse(result.success)
        self.assertEqual(result.error.code, "not_found")

    def test_job_update_does_not_replace_cancelled_terminal_state(self):
        job_id = uuid.uuid4().hex
        self.assertTrue(
            self.store.save_job(
                {
                    "job_id": job_id,
                    "status": "cancelled",
                    "created_at": 1.0,
                }
            ).success
        )

        result = self.store.update_job(
            job_id,
            {"status": "completed", "finished_at": 2.0},
            expected_statuses={"running"},
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error.code, "already_terminal")
        stored = json.loads((self.root / "jobs" / f"{job_id}.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["status"], "cancelled")

    def test_atomic_write_leaves_no_temporary_file(self):
        task = TaskRecord.create("prepared_gcode")

        result = self.store.save_task(task)

        self.assertTrue(result.success, result.error)
        self.assertFalse(os.path.exists(self.root / "tasks" / f"{task.record_id}.json.tmp"))


if __name__ == "__main__":
    unittest.main()
