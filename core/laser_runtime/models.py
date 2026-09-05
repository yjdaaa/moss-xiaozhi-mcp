from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
TASK_KINDS = {"text", "image", "calibration", "prepared_gcode"}
TASK_STATES = {
    "collecting",
    "preview_ready",
    "sending",
    "completed",
    "failed",
    "cancelled",
    "feedback_recorded",
}
JOB_STATES = {"pending", "running", "completed", "failed", "cancelled"}
TERMINAL_JOB_STATES = {"completed", "failed", "cancelled"}


class LaserError(Exception):
    def __init__(self, code: str, message: str, detail: Any = None, retryable: bool = False):
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)
        self.detail = detail
        self.retryable = bool(retryable)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.detail is not None:
            payload["detail"] = self.detail
        return payload


@dataclass(frozen=True)
class OperationResult:
    value: Any = None
    error: LaserError | None = None

    @property
    def success(self) -> bool:
        return self.error is None

    @classmethod
    def ok(cls, value: Any = None) -> "OperationResult":
        return cls(value=value)

    @classmethod
    def failure(cls, error: LaserError) -> "OperationResult":
        if not isinstance(error, LaserError):
            raise TypeError("error must be a LaserError")
        return cls(error=error)


@dataclass(frozen=True)
class ProcessingSpec:
    material: str = ""
    thickness_mm: float | None = None
    laser_mode: str = "engrave"
    engraving_mode: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    dimensions: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "ProcessingSpec":
        payload = _object_value(payload, "processing")
        return cls(
            material=str(payload.get("material") or ""),
            thickness_mm=payload.get("thickness_mm"),
            laser_mode=str(payload.get("laser_mode") or "engrave"),
            engraving_mode=str(payload.get("engraving_mode") or ""),
            params=dict(payload.get("params") or {}),
            dimensions=dict(payload.get("dimensions") or {}),
        )


@dataclass(frozen=True)
class ConnectionSpec:
    mode: str = ""
    serial_port: str = ""
    baudrate: int = 115200
    network_host: str = ""
    network_transport: str = "telnet"
    network_http_port: int = 80
    network_telnet_port: int = 23
    network_timeout: float = 5.0
    wait_for_response: bool = True
    run_in_background: bool = True

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "ConnectionSpec":
        payload = _object_value(payload, "connection")
        return cls(
            mode=str(payload.get("mode") or ""),
            serial_port=str(payload.get("serial_port") or ""),
            baudrate=int(payload.get("baudrate") or 115200),
            network_host=str(payload.get("network_host") or ""),
            network_transport=str(payload.get("network_transport") or "telnet"),
            network_http_port=int(payload.get("network_http_port") or 80),
            network_telnet_port=int(payload.get("network_telnet_port") or 23),
            network_timeout=float(payload.get("network_timeout") or 5.0),
            wait_for_response=bool(payload.get("wait_for_response", True)),
            run_in_background=bool(payload.get("run_in_background", True)),
        )


@dataclass(frozen=True)
class PreviewSnapshot:
    locked: dict[str, Any]
    fingerprint: str
    gcode_sha256: str = ""
    created_at: float = 0.0

    @classmethod
    def create(cls, locked: dict[str, Any], gcode_sha256: str = "") -> "PreviewSnapshot":
        locked_payload = dict(locked)
        gcode_digest = str(gcode_sha256 or "")
        return cls(
            locked=locked_payload,
            fingerprint=_preview_fingerprint(locked_payload, gcode_digest),
            gcode_sha256=gcode_digest,
            created_at=time.time(),
        )

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "PreviewSnapshot | None":
        if payload is None:
            return None
        payload = _object_value(payload, "preview_snapshot")
        if not payload:
            return None
        locked = _object_value(payload.get("locked"), "preview_snapshot.locked")
        fingerprint = str(payload.get("fingerprint") or "")
        gcode_digest = str(payload.get("gcode_sha256") or "")
        if fingerprint != _preview_fingerprint(locked, gcode_digest):
            raise LaserError("state_corrupt", "preview snapshot fingerprint is invalid")
        return cls(
            locked=locked,
            fingerprint=fingerprint,
            gcode_sha256=gcode_digest,
            created_at=float(payload.get("created_at") or 0.0),
        )


@dataclass(frozen=True)
class ExecutionRef:
    job_id: str = ""
    transport: str = ""
    status: str = ""
    result: Any = None
    created_at: float | None = None
    updated_at: float | None = None
    finished_at: float | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "ExecutionRef | None":
        if payload is None:
            return None
        payload = _object_value(payload, "execution")
        if not payload:
            return None
        return cls(
            job_id=str(payload.get("job_id") or ""),
            transport=str(payload.get("transport") or ""),
            status=str(payload.get("status") or ""),
            result=payload.get("result"),
            created_at=payload.get("created_at"),
            updated_at=payload.get("updated_at"),
            finished_at=payload.get("finished_at"),
        )


@dataclass
class TaskRecord:
    record_id: str
    kind: str
    status: str = "collecting"
    request: dict[str, Any] = field(default_factory=dict)
    processing: ProcessingSpec = field(default_factory=ProcessingSpec)
    artifacts: dict[str, Any] = field(default_factory=dict)
    attempts: list[dict[str, Any]] = field(default_factory=list)
    preview_snapshot: PreviewSnapshot | None = None
    connection: ConnectionSpec = field(default_factory=ConnectionSpec)
    execution: ExecutionRef | None = None
    feedback_events: list[dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    schema_version: int = SCHEMA_VERSION

    @classmethod
    def create(
        cls,
        kind: str,
        request: dict[str, Any] | None = None,
        processing: ProcessingSpec | None = None,
        connection: ConnectionSpec | None = None,
    ) -> "TaskRecord":
        if kind not in TASK_KINDS:
            raise LaserError("invalid_argument", f"unsupported task kind: {kind}")
        return cls(
            record_id=uuid.uuid4().hex,
            kind=kind,
            request=dict(request or {}),
            processing=processing or ProcessingSpec(),
            connection=connection or ConnectionSpec(),
        )

    @property
    def task_id(self) -> str:
        return self.record_id

    @property
    def workflow_id(self) -> str:
        return f"wf_{self.record_id}"

    @property
    def calibration_id(self) -> str:
        return self.record_id

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["record_type"] = "task"
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TaskRecord":
        if not isinstance(payload, dict):
            raise LaserError("state_corrupt", "task record must be an object")
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise LaserError("state_corrupt", "unsupported task schema version")
        if payload.get("record_type", "task") != "task":
            raise LaserError("state_corrupt", "record is not a task")

        try:
            record_id = normalize_record_id(payload.get("record_id"))
        except LaserError as exc:
            raise LaserError("state_corrupt", "task record identifier is invalid") from exc
        kind = str(payload.get("kind") or "")
        status = str(payload.get("status") or "")
        if kind not in TASK_KINDS or status not in TASK_STATES:
            raise LaserError("state_corrupt", "task kind or status is invalid")

        return cls(
            record_id=record_id,
            kind=kind,
            status=status,
            request=_object_value(payload.get("request"), "request"),
            processing=ProcessingSpec.from_dict(payload.get("processing")),
            artifacts=_object_value(payload.get("artifacts"), "artifacts"),
            attempts=_object_list(payload.get("attempts"), "attempts"),
            preview_snapshot=PreviewSnapshot.from_dict(payload.get("preview_snapshot")),
            connection=ConnectionSpec.from_dict(payload.get("connection")),
            execution=ExecutionRef.from_dict(payload.get("execution")),
            feedback_events=_object_list(payload.get("feedback_events"), "feedback_events"),
            created_at=float(payload.get("created_at") or 0.0),
            updated_at=float(payload.get("updated_at") or 0.0),
            schema_version=SCHEMA_VERSION,
        )


def normalize_record_id(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized.startswith("wf_"):
        normalized = normalized[3:]
    if len(normalized) != 32 or any(char not in "0123456789abcdef" for char in normalized):
        raise LaserError("invalid_argument", "task identifier is invalid")
    return normalized


def canonical_fingerprint(payload: Any) -> str:
    encoded = json.dumps(
        _canonical_value(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def sha256_binary_handle(handle) -> str:
    """Hash raw bytes from an already-open binary handle; seek(0) after."""
    digest = hashlib.sha256()
    handle.seek(0)
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
    handle.seek(0)
    return digest.hexdigest()


def file_sha256(path: str | Path) -> str:
    with open(path, "rb") as file:
        return sha256_binary_handle(file)


def _preview_fingerprint(locked: dict[str, Any], gcode_sha256: str) -> str:
    return canonical_fingerprint(
        {
            "locked": locked,
            "gcode_sha256": gcode_sha256,
        }
    )


def _object_value(value: Any, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise LaserError("state_corrupt", f"task {field_name} must be an object")
    return dict(value)


def _object_list(value: Any, field_name: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise LaserError("state_corrupt", f"task {field_name} must be a list of objects")
    return [dict(item) for item in value]


def _canonical_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _canonical_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise LaserError("invalid_argument", "fingerprint values must be finite")
        if value.is_integer():
            return int(value)
    return value
