from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Iterable

from .models import (
    JOB_STATES,
    SCHEMA_VERSION,
    TERMINAL_JOB_STATES,
    LaserError,
    OperationResult,
    TaskRecord,
    normalize_record_id,
)


JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class RuntimeStore:
    def __init__(self, runtime_dir: str | Path):
        self.runtime_dir = Path(runtime_dir)
        self.tasks_dir = self.runtime_dir / "tasks"
        self.jobs_dir = self.runtime_dir / "jobs"

    def save_task(self, task: TaskRecord) -> OperationResult:
        if not isinstance(task, TaskRecord):
            return OperationResult.failure(LaserError("invalid_argument", "task must be a TaskRecord"))
        path = self.tasks_dir / f"{task.record_id}.json"
        return self._write(path, task.to_dict(), task)

    def load_task(self, identifier: str) -> OperationResult:
        try:
            record_id = normalize_record_id(identifier)
        except LaserError as error:
            return OperationResult.failure(error)
        path = self.tasks_dir / f"{record_id}.json"
        loaded = self._read(path, "task")
        if not loaded.success:
            return loaded
        try:
            task = TaskRecord.from_dict(loaded.value)
        except LaserError as error:
            return OperationResult.failure(error)
        except (TypeError, ValueError, OverflowError) as exc:
            return OperationResult.failure(
                LaserError("state_corrupt", "task record contains invalid values", {"reason": str(exc)})
            )
        if task.record_id != record_id:
            return OperationResult.failure(
                LaserError("state_corrupt", "task record identifier does not match its path")
            )
        return OperationResult.ok(task)

    def update_task(
        self,
        identifier: str,
        changes: dict[str, Any],
        expected_statuses: Iterable[str] | None = None,
    ) -> OperationResult:
        loaded = self.load_task(identifier)
        if not loaded.success:
            return loaded
        task = loaded.value
        expected = set(expected_statuses or ())
        if expected and task.status not in expected:
            return OperationResult.failure(self._state_error(task.status, expected))

        payload = task.to_dict()
        payload.update(changes)
        payload["record_id"] = task.record_id
        payload["updated_at"] = time.time()
        try:
            updated = TaskRecord.from_dict(payload)
        except LaserError as error:
            return OperationResult.failure(error)
        return self.save_task(updated)

    def save_job(self, job: dict[str, Any]) -> OperationResult:
        if not isinstance(job, dict):
            return OperationResult.failure(LaserError("invalid_argument", "job must be an object"))
        job_id = str(job.get("job_id") or "")
        error = self._job_id_error(job_id)
        if error:
            return OperationResult.failure(error)
        status = str(job.get("status") or "")
        if status not in JOB_STATES:
            return OperationResult.failure(LaserError("invalid_argument", "job status is invalid"))
        payload = dict(job)
        payload.update({"schema_version": SCHEMA_VERSION, "record_type": "job", "job_id": job_id})
        path = self.jobs_dir / f"{job_id}.json"
        return self._write(path, payload, payload)

    def load_job(self, job_id: str) -> OperationResult:
        error = self._job_id_error(job_id)
        if error:
            return OperationResult.failure(error)
        loaded = self._read(self.jobs_dir / f"{job_id}.json", "job")
        if not loaded.success:
            return loaded
        payload = loaded.value
        if payload.get("schema_version") != SCHEMA_VERSION or payload.get("record_type") != "job":
            return OperationResult.failure(LaserError("state_corrupt", "job schema is invalid"))
        if payload.get("job_id") != job_id or payload.get("status") not in JOB_STATES:
            return OperationResult.failure(LaserError("state_corrupt", "job identity or status is invalid"))
        return OperationResult.ok(payload)

    def update_job(
        self,
        job_id: str,
        changes: dict[str, Any],
        expected_statuses: Iterable[str] | None = None,
    ) -> OperationResult:
        loaded = self.load_job(job_id)
        if not loaded.success:
            return loaded
        job = loaded.value
        expected = set(expected_statuses or ())
        if expected and job["status"] not in expected:
            return OperationResult.failure(self._state_error(job["status"], expected))
        updated = dict(job)
        updated.update(changes)
        updated["job_id"] = job_id
        return self.save_job(updated)

    def _read(self, path: Path, label: str) -> OperationResult:
        if not path.is_file():
            return OperationResult.failure(LaserError("not_found", f"{label} record was not found"))
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return OperationResult.failure(
                LaserError("state_corrupt", f"{label} record cannot be read", {"reason": str(exc)})
            )
        if not isinstance(payload, dict):
            return OperationResult.failure(LaserError("state_corrupt", f"{label} record must be an object"))
        return OperationResult.ok(payload)

    def _write(self, path: Path, payload: dict[str, Any], value: Any) -> OperationResult:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = Path(f"{path}.tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temporary, path)
        except OSError as exc:
            return OperationResult.failure(
                LaserError("transport_failed", "runtime state could not be written", {"reason": str(exc)})
            )
        return OperationResult.ok(value)

    @staticmethod
    def _job_id_error(job_id: str) -> LaserError | None:
        if not JOB_ID_RE.fullmatch(str(job_id or "")):
            return LaserError("invalid_argument", "job identifier is invalid")
        return None

    @staticmethod
    def _state_error(status: str, expected: set[str]) -> LaserError:
        code = "already_terminal" if status in TERMINAL_JOB_STATES else "invalid_argument"
        return LaserError(code, "record status does not allow this update", {"status": status, "expected": sorted(expected)})
