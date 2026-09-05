from __future__ import annotations

import json
import math
import os
import re
import time
import uuid
from pathlib import Path

from core import laser_execution, laser_material_safety, laser_time_estimate
from core.laser_confirm import is_explicitly_confirmed
from core.laser_runtime.models import file_sha256
from tools import (
    laser_asset_gcode_tool,
    laser_material_calibration_tool,
    laser_grbl_tool,
    laser_network_grbl_tool,
    text_laser_task_tool,
)

FROZEN_PROCESSING_KEYS = (
    "gcode_file",
    "material",
    "thickness_mm",
    "laser_mode",
    # generation/output mode (image outline/raster) — NOT a global alias of laser_mode
    "mode",
    "engraving_mode",
    "task_type",
    # text final power percent; image freezes raw S as "power" separately
    "power_percent",
    "power",
    "laser_min_power",
    "laser_max_power",
    "s_max",
    "feed_rate",
    "travel_rate",
    "passes",
    "pixel_size_mm",
    "threshold",
    "invert",
    "bidirectional",
    "overscan_mm",
    "raster_scan_direction",
    "dither_algorithm",
    "raster_output_strategy",
    "raster_quality_strategy",
)
# prepared_gcode: any concrete processing / strategy override at confirm is forbidden
PREPARED_FORBIDDEN_CONFIRM_PROCESSING_KEYS = (
    "power_percent",
    "power",
    "laser_min_power",
    "laser_max_power",
    "s_max",
    "feed_rate",
    "travel_rate",
    "passes",
    "task_type",
    "pixel_size_mm",
    "threshold",
    "invert",
    "bidirectional",
    "overscan_mm",
    "raster_scan_direction",
    "dither_algorithm",
    "raster_output_strategy",
    "raster_quality_strategy",
    "engraving_mode",
)
# Keys that change production meaning; updating them invalidates preview confirmation.
PRODUCTION_INVALIDATION_KEYS = frozenset(
    {
        *FROZEN_PROCESSING_KEYS,
        "text",
        "image_file",
        "image_url",
        "summary_path",
        "prompt",
        "source_type",
        "size_mm",
        "width_mm",
        "height_mm",
        "image_width",
        "image_height",
        "font_size",
        "font_path",
        "auto_wrap",
        "max_lines",
        "layout_mode",
        "auto_trim",
        "trim_tolerance",
        "auto_size",
        "dpi",
        "lock_aspect_ratio",
        "vector_simplify_factor",
        "offset_x_mm",
        "offset_y_mm",
        "safe_margin_mm",
        "prepared_gcode_dir",
        "reuse_prepared_gcode",
        "candidate_selection",
        "candidate_id",
        "candidate_index",
        "material_library",
        "output_dir",
        "output_format",
    }
)
# All production kwargs accepted by the public workflow API (must be compared or rejected at confirm).
API_PRODUCTION_INPUT_KEYS = frozenset(
    {
        *PRODUCTION_INVALIDATION_KEYS,
        "gcode_file",
        "power",
    }
)
CONNECTION_OVERRIDE_KEYS = (
    "connection_mode",
    "port",
    "baudrate",
    "network_host",
    "network_transport",
    "network_http_port",
    "network_telnet_port",
    "network_timeout",
    "run_in_background",
    "wait_for_response",
)
CONNECTION_KEY_SET = frozenset(CONNECTION_OVERRIDE_KEYS) | frozenset({"serial_port"})
RUNTIME_LOCKED_STATUSES = frozenset({"sending"})
TEXT_AUTHORITY_KEYS = (
    "source_type",
    "gcode_file",
    "gcode_sha256",
    "material",
    "thickness_mm",
    "matched_thickness_mm",
    "match",
    "warnings",
    "can_send",
    "laser_mode",
    "power_percent",
    "feed_rate",
    "passes",
)
# Image authority uses production JobParams semantics: laser_mode=M3/M4, mode=outline/raster,
# task_type=engrave_photo|..., power = raw S value (not percent).
IMAGE_AUTHORITY_KEYS = (
    "source_type",
    "gcode_file",
    "gcode_sha256",
    "material",
    "thickness_mm",
    "laser_mode",
    "mode",
    "task_type",
    "power",
    "feed_rate",
    "passes",
)
PREPARED_AUTHORITY_KEYS = (
    "source_type",
    "gcode_file",
    "gcode_sha256",
)
STRATEGY_SNAPSHOT_KEYS = (
    "task_type",
    "pixel_size_mm",
    "threshold",
    "invert",
    "bidirectional",
    "overscan_mm",
    "raster_scan_direction",
    "dither_algorithm",
    "raster_output_strategy",
    "raster_quality_strategy",
    "engraving_mode",
)
# MCP wrapper defaults that must stay omitted unless the caller supplies them.
MCP_OMIT_IF_NONE_KEYS = frozenset(
    {
        "thickness_mm",
        "laser_mode",
        "engraving_mode",
        "mode",
        "size_mm",
        "width_mm",
        "height_mm",
        "laser_min_power",
        "laser_max_power",
        "power_percent",
        "feed_rate",
        "travel_rate",
        "pixel_size_mm",
        "threshold",
        "passes",
        "invert",
        "bidirectional",
        "overscan_mm",
        "raster_scan_direction",
        "raster_quality_strategy",
        "auto_trim",
        "trim_tolerance",
        "auto_size",
        "dpi",
        "lock_aspect_ratio",
        "offset_x_mm",
        "offset_y_mm",
        "safe_margin_mm",
        "baudrate",
        "wait_for_response",
        "run_in_background",
        "network_transport",
        "network_http_port",
        "network_telnet_port",
        "network_timeout",
        "reuse_prepared_gcode",
        "image_width",
        "image_height",
        "font_size",
        "auto_wrap",
        "max_lines",
        "layout_mode",
    }
)


ROOT_DIR = Path(__file__).resolve().parents[1]
WORKFLOW_STATES = {
    "collecting",
    "preview_ready",
    "sending",
    "completed",
    "failed",
    "cancelled",
    "feedback_recorded",
}
RUNTIME_STATUS_ALIASES = {"queued": "pending", "canceled": "cancelled"}
TERMINAL_RUNTIME_STATUSES = {"completed", "failed", "cancelled"}
FEEDBACK_ELIGIBLE_STATES = {"completed", "failed", "cancelled", "sending", "feedback_recorded"}
DEFAULT_WORKFLOWS_DIR = os.environ.get(
    "LASER_WORKFLOWS_DIR",
    str(ROOT_DIR / ".runtime" / "laser_workflows"),
)
SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(token|password|passwd|secret|api[_-]?key|host|network_host|ip)\s*[:=]\s*[^,\s;；，。]+"
)
WINDOWS_PATH_RE = re.compile(r"[A-Za-z]:[\\/][^\s;；，。]+")
LOCAL_HOST_RE = re.compile(r"\b[A-Za-z0-9][A-Za-z0-9.-]*\.local\b")
IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def _safe_speech(text):
    speech = str(text or "")
    speech = SENSITIVE_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}=<已隐藏>", speech)
    speech = WINDOWS_PATH_RE.sub("本地文件", speech)
    speech = LOCAL_HOST_RE.sub("设备地址", speech)
    speech = IPV4_RE.sub("设备地址", speech)
    return speech


def _build_success(result, detail=None):
    payload = {"success": True, "result": result}
    if detail is not None:
        payload["detail"] = detail
    return payload


def _build_failure(message, detail=None, speech=None, error_code=None):
    payload = {"success": False, "result": message}
    if detail is not None:
        payload["detail"] = detail
    if error_code is not None:
        payload["error_code"] = error_code
    payload["speech"] = _safe_speech(speech or f"{message}；请补齐信息后再继续。")
    return payload


def _now():
    return time.time()


def _coerce_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return default
    normalized = str(value).strip().lower()
    return normalized in {"1", "true", "yes", "y", "on", "确认", "confirmed"}


def _is_explicitly_confirmed(value) -> bool:
    """Strict truth for confirmed=true gates — reuses core helper (no tools->tools)."""
    return is_explicitly_confirmed(value)


def _workflow_dir(workflows_dir=None):
    return Path(workflows_dir or DEFAULT_WORKFLOWS_DIR)


def _workflow_file_path(workflow_id, workflows_dir=None):
    return _workflow_dir(workflows_dir) / f"{workflow_id}.json"


def _normalize_workflow_id(workflow_id):
    normalized = str(workflow_id or "").strip()
    if not normalized:
        return "", "请提供 workflow_id"
    if normalized != os.path.basename(normalized):
        return "", "workflow_id 不能包含路径分隔符"
    if not normalized.endswith(".json"):
        return normalized, None
    return "", "workflow_id 不能包含文件扩展名"


def _write_json_file(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def _read_json_file(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _save_workflow(workflow, workflows_dir=None):
    workflow["updated_at"] = _now()
    if workflow.get("speech"):
        workflow["speech"] = _safe_speech(workflow["speech"])
    path = _workflow_file_path(workflow["workflow_id"], workflows_dir)
    _write_json_file(path, workflow)
    return str(path)


def _load_workflow(workflow_id, workflows_dir=None):
    workflow_id, error = _normalize_workflow_id(workflow_id)
    if error:
        return None, error
    path = _workflow_file_path(workflow_id, workflows_dir)
    if not path.is_file():
        return None, f"未找到激光 workflow: {workflow_id}"
    try:
        workflow = _read_json_file(path)
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"读取激光 workflow 失败: {exc}"
    if not isinstance(workflow, dict):
        return None, "激光 workflow 记录格式不对"
    return workflow, None


def _list_workflows(workflows_dir=None):
    root = _workflow_dir(workflows_dir)
    if not root.is_dir():
        return []
    workflows = []
    for path in root.glob("*.json"):
        try:
            payload = _read_json_file(path)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and payload.get("workflow_id"):
            workflows.append(payload)
    return workflows


def _new_workflow(source_type="", input_fields=None):
    timestamp = _now()
    normalized_source = _normalize_source_type(source_type)
    cleaned_input = _clean_input_fields(input_fields or {})
    if source_type and "source_type" not in cleaned_input:
        cleaned_input["source_type"] = normalized_source
    workflow = {
        "workflow_id": f"wf_{uuid.uuid4().hex}",
        "status": "collecting",
        "source_type": normalized_source,
        "input": cleaned_input,
        "artifacts": {},
        "confirmation_snapshot": {},
        "runtime_job": {},
        "feedback_events": [],
        "speech": "已创建激光工作流；补齐文字、材料、厚度或文件后可以生成预览。",
        "next_actions": ["update", "preview"],
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    workflow["next_actions"] = _next_actions_for(workflow)
    workflow["speech"] = _speech_for(workflow)
    return workflow


def _normalize_source_type(source_type):
    normalized = str(source_type or "").strip().lower().replace("-", "_")
    aliases = {
        "prepared": "prepared_gcode",
        "gcode": "prepared_gcode",
        "summary": "image_summary",
        "ai_summary": "image_summary",
        "image_url": "image",
    }
    return aliases.get(normalized, normalized)


def _clean_input_fields(fields, *, keep_explicit_false_manual_flag: bool = False):
    clean = {}
    for key, value in (fields or {}).items():
        if key.startswith("_"):
            continue
        if value is None:
            continue
        if isinstance(value, str) and value == "":
            continue
        clean[key] = value
    # Unconfirmed page/MCP manual overrides must not persist or override material library.
    return laser_material_safety.strip_unconfirmed_manual_params(
        clean,
        keep_explicit_false=keep_explicit_false_manual_flag,
    )


def _material_safety_failure(decision, workflows_dir=None, workflow=None):
    """Map shared material-safety decision to workflow failure without sendable preview."""
    decision_name = str((decision or {}).get("decision") or "")
    message = str((decision or {}).get("message") or "材料不满足安全要求")
    reason = str((decision or {}).get("reason") or "material_safety")
    error_code = "material_blocked" if decision_name == laser_material_safety.DECISION_BLOCK else "material_clarify"
    detail = {
        "material": (decision or {}).get("material"),
        "decision": decision_name,
        "reason": reason,
    }
    if workflow is not None:
        # Keep collecting; never leave a sendable preview_ready for blocked/clarify materials.
        if workflow.get("status") == "preview_ready" or workflow.get("confirmation_snapshot"):
            _invalidate_preview_binding(workflow, reason=message)
        workflow["status"] = "collecting"
        workflow["speech"] = _safe_speech(f"{message}；本次没有生成可发送预览，也没有访问激光机。")
        workflow["next_actions"] = _next_actions_for(workflow)
        _save_workflow(workflow, workflows_dir=workflows_dir)
        detail["workflow"] = _workflow_summary(workflow)
    return _build_failure(
        message,
        detail=detail,
        speech=_safe_speech(f"{message}；本次没有生成可发送预览，也没有访问激光机。"),
        error_code=error_code,
    )


def _reject_unsafe_material(workflow, workflows_dir=None, material=None, thickness_mm=None):
    """Reject blocked/clarify materials before a sendable preview is bound.

    When ``material`` is provided (e.g. summary.material), that value is authoritative
    for the safety decision and is mirrored into workflow input for speech.
    """
    if material not in (None, ""):
        data = dict(workflow.get("input") or {})
        data["material"] = material
        if thickness_mm not in (None, ""):
            data["thickness_mm"] = thickness_mm
        workflow["input"] = data
        candidate = material
    else:
        candidate = (workflow.get("input") or {}).get("material")
    if candidate in (None, ""):
        return None
    decision = laser_material_safety.evaluate_material_safety(candidate)
    if decision.get("decision") == laser_material_safety.DECISION_ALLOW:
        return None
    return _material_safety_failure(decision, workflows_dir=workflows_dir, workflow=workflow)


def _append_physical_material_check(speech, workflow):
    """Pass through generator/status speech without appending a second material-check sentence.

    Web/UI already shows material and thickness in the form; a trailing
    "请人工核对…系统没有自动识别" note was redundant and cluttered the status line.
    Keep this helper as a no-op sanitizer so call sites stay stable.
    """
    del workflow  # retained for call-site compatibility
    return _safe_speech(speech)


def _positive_float_or_none(value):
    if value in (None, ""):
        return None
    number = float(value)
    return number if number > 0 else None


def _production_keys_in(fields):
    clean = _clean_input_fields(fields or {})
    return {key for key in clean if key in PRODUCTION_INVALIDATION_KEYS and key not in CONNECTION_KEY_SET}


def _connection_keys_in(fields):
    clean = _clean_input_fields(fields or {})
    return {key for key in clean if key in CONNECTION_KEY_SET}


def _connection_snapshot_value(snapshot, key):
    connection = snapshot.get("connection") if isinstance(snapshot, dict) else {}
    connection = connection if isinstance(connection, dict) else {}
    if key == "connection_mode":
        return snapshot.get("connection_mode") or connection.get("mode") or ""
    if key == "port":
        return connection.get("serial_port") or connection.get("port") or ""
    if key == "serial_port":
        return connection.get("serial_port") or ""
    return connection.get(key, "")


def _reject_connection_update_against_snapshot(workflow, clean):
    """If preview already froze a connection field, reject updates that try to change it."""
    snapshot = workflow.get("confirmation_snapshot")
    if not isinstance(snapshot, dict) or not snapshot:
        return None, None, None
    for key in _connection_keys_in(clean):
        current = _connection_snapshot_value(snapshot, key)
        if not _connection_field_present(current):
            continue
        supplied = clean.get(key)
        compare_key = "serial_port" if key == "port" else key
        if not _values_equal(compare_key if compare_key != "connection_mode" else "connection_mode", current, supplied):
            return (
                f"预览已绑定连接字段，不得通过 update 修改: {key}",
                {"field": key, "preview": current, "requested": supplied},
                "confirmation_mismatch",
            )
    return None, None, None


def _invalidate_preview_binding(workflow, reason="生产参数已更新，请重新生成预览后再确认发送。"):
    """Clear confirmation binding only from preview_ready. Never touch sending/runtime jobs."""
    status = workflow.get("status")
    if status in RUNTIME_LOCKED_STATUSES:
        return False
    had_binding = bool(workflow.get("confirmation_snapshot")) or status == "preview_ready"
    if not had_binding:
        return False
    _discard_confirmation_snapshot(workflow)
    if status == "preview_ready":
        workflow["status"] = "collecting"
        workflow["speech"] = _safe_speech(reason)
    return True


def _merge_input(workflow, updates, *, allow_status_change=True):
    """
    Merge input fields.
    Returns (workflow, error_message, error_detail, error_code).
    error_message set means caller must not persist the merge.
    """
    # Keep explicit false long enough for merge to clear a prior confirmed sticky state.
    clean = _clean_input_fields(updates, keep_explicit_false_manual_flag=True)
    status = workflow.get("status")

    if status in RUNTIME_LOCKED_STATUSES:
        production_touched = _production_keys_in(clean)
        connection_touched = _connection_keys_in(clean)
        if production_touched or connection_touched or clean.get("source_type"):
            return (
                workflow,
                "发送中的任务不允许修改生产或连接参数；请先 status/cancel。",
                {
                    "status": status,
                    "blocked_fields": sorted(production_touched | connection_touched | ({"source_type"} if clean.get("source_type") else set())),
                },
                "workflow_busy",
            )
        # ignore no-op / unknown non-production merges while sending
        workflow["next_actions"] = _next_actions_for(workflow)
        return workflow, None, None, None

    conn_error, conn_detail, conn_code = _reject_connection_update_against_snapshot(workflow, clean)
    if conn_error:
        return workflow, conn_error, conn_detail, conn_code or "confirmation_mismatch"

    if clean.get("source_type"):
        workflow["source_type"] = _normalize_source_type(clean["source_type"])
    production_touched = _production_keys_in(clean)
    invalidated = False
    if allow_status_change and production_touched and (
        status == "preview_ready" or workflow.get("confirmation_snapshot")
    ):
        reason = f"生产字段已更新（{', '.join(sorted(production_touched))}），请重新生成预览后再确认发送。"
        invalidated = _invalidate_preview_binding(workflow, reason=reason)
    # Identity change clears sticky manual confirmation; unconfirmed manual fields strip.
    workflow["input"] = laser_material_safety.apply_manual_param_policy(
        workflow.get("input") or {},
        clean,
        updates_include_explicit_false=("manual_params_confirmed" in clean),
    )
    workflow["next_actions"] = _next_actions_for(workflow)
    if not invalidated:
        workflow["speech"] = _speech_for(workflow)
    return workflow, None, None, None


def _has_text_preview_inputs(workflow):
    data = workflow.get("input", {})
    return bool(data.get("text") and data.get("material") and float(data.get("thickness_mm") or 0) > 0)


def _has_prepared_preview_inputs(workflow):
    return bool(workflow.get("input", {}).get("gcode_file"))


def _has_summary_preview_inputs(workflow):
    return bool(workflow.get("input", {}).get("summary_path"))


def _preview_send_block_reason(workflow):
    """Return why a preview_ready workflow must not expose confirm_send, or None if sendable.

    Preview success and send permission are separate: G-code/preview images may exist
    while can_send=false (library/unverified params). Only real bind failures leave the
    snapshot incomplete; send-gate messages must not be treated as generation failure.
    """
    if not isinstance(workflow, dict):
        return "预览确认快照缺失"
    snapshot = workflow.get("confirmation_snapshot")
    if not isinstance(snapshot, dict) or not snapshot.get("gcode_sha256") or not snapshot.get("gcode_file"):
        return "预览确认快照缺失，请重新生成预览"
    source_type = workflow.get("source_type")
    if source_type not in {"text", "image_summary"}:
        return None
    blocked = str(snapshot.get("send_blocked_reason") or "").strip()
    if blocked:
        return blocked
    # Generated text/image previews fail closed: only explicit preview-time
    # send authority may expose or pass confirm_send.
    if snapshot.get("can_send") is not True:
        return "当前参数未达到可发送状态，请先小样测试或完成校准。"
    if source_type == "image_summary":
        summary = (workflow.get("artifacts") or {}).get("summary")
        if isinstance(summary, dict):
            send_error = laser_asset_gcode_tool._validate_summary_for_send(summary)
            if send_error:
                return send_error
    return None


def _is_sendable_preview(workflow):
    return (
        isinstance(workflow, dict)
        and workflow.get("status") == "preview_ready"
        and not _preview_send_block_reason(workflow)
    )


def _next_actions_for(workflow):
    status = workflow.get("status")
    source_type = workflow.get("source_type")
    if status == "collecting":
        if (
            source_type == "text"
            and _has_text_preview_inputs(workflow)
            or source_type == "prepared_gcode"
            and _has_prepared_preview_inputs(workflow)
            or source_type == "image_summary"
            and _has_summary_preview_inputs(workflow)
            or source_type == "image"
        ):
            return ["preview"]
        return ["update", "preview"]
    if status == "preview_ready":
        # Preview may succeed with artifacts while send is still blocked (sample test).
        if _is_sendable_preview(workflow):
            return ["confirm_send", "regenerate", "feedback"]
        return ["regenerate", "feedback"]
    if status == "sending":
        return ["status", "cancel", "feedback"]
    if status in {"completed", "failed", "cancelled", "feedback_recorded"}:
        return ["feedback", "regenerate", "preview"]
    return ["status"]


def _format_duration(estimate):
    if not isinstance(estimate, dict):
        return ""
    seconds = estimate.get("estimated_seconds") or estimate.get("total_seconds")
    if not seconds:
        return ""
    return laser_time_estimate.format_duration_zh(seconds)


def _speech_for(workflow, message=""):
    if message:
        return _safe_speech(message)
    status = workflow.get("status", "collecting")
    artifacts = workflow.get("artifacts", {})
    runtime = workflow.get("runtime_job", {})
    duration = _format_duration(artifacts.get("time_estimate") or runtime.get("time_estimate"))
    if status == "collecting":
        return "还缺必要信息，补齐文字、材料、厚度或文件后我再生成预览。"
    if status == "preview_ready":
        if duration:
            base = f"预览已生成，预计总耗时约 {duration}"
        else:
            base = "预览已生成"
        block_reason = _preview_send_block_reason(workflow)
        if block_reason:
            base = f"{base}；但暂不可发送：{block_reason}"
        else:
            base = f"{base}；确认材料、参数和设备后才能开始。"
        return _append_physical_material_check(base, workflow)
    if status == "sending":
        runtime_status = runtime.get("status") or "pending"
        return _safe_speech(f"任务正在发送，底层状态是 {runtime_status}，现在可以查询状态或取消后续 G-code 发送。")
    if status == "completed":
        return "任务已完成；如果效果太焦、太浅或切不透，可以直接告诉我。"
    if status == "failed":
        reason = runtime.get("error") or runtime.get("last_error") or "请检查输入、文件或设备连接"
        return _safe_speech(f"任务失败，原因是 {reason}；下一步请修正后重新生成预览。")
    if status == "cancelled":
        return "任务已取消，不会继续发送后续 G-code；这不等同于物理急停，需要急停请走安全动作。"
    if status == "feedback_recorded":
        return "我已记录反馈；文字任务可以继续重新生成预览，图片或预制文件请先选择新的重生成方式。"
    return "已读取工作流状态；请告诉我下一步要预览、发送、查询、取消还是反馈。"


def _workflow_summary(workflow):
    return {
        "workflow_id": workflow.get("workflow_id"),
        "status": workflow.get("status"),
        "source_type": workflow.get("source_type"),
        "input": workflow.get("input", {}),
        "artifacts": workflow.get("artifacts", {}),
        "runtime_job": workflow.get("runtime_job", {}),
        "speech": _safe_speech(workflow.get("speech") or _speech_for(workflow)),
        "next_actions": workflow.get("next_actions") or _next_actions_for(workflow),
        "created_at": workflow.get("created_at"),
        "updated_at": workflow.get("updated_at"),
    }


def _workflow_result(workflow, workflows_dir=None, extra=None):
    workflow["next_actions"] = _next_actions_for(workflow)
    workflow["speech"] = _safe_speech(workflow.get("speech") or _speech_for(workflow))
    workflow_file = _save_workflow(workflow, workflows_dir=workflows_dir)
    result = _workflow_summary(workflow)
    result["workflow_file"] = workflow_file
    result["workflow"] = workflow
    if extra:
        result.update(extra)
        # Keep workflow speech authoritative; generator extras must not wipe safety notes.
        result["speech"] = workflow["speech"]
        if isinstance(result.get("workflow"), dict):
            result["workflow"]["speech"] = workflow["speech"]
    return _build_success(result)


def _set_failed(workflow, message, detail=None, workflows_dir=None):
    workflow["status"] = "failed"
    workflow.setdefault("runtime_job", {})["last_error"] = message
    workflow["speech"] = _speech_for(workflow, f"任务失败，原因是 {message}；请修正后重新生成预览。")
    workflow["next_actions"] = _next_actions_for(workflow)
    _save_workflow(workflow, workflows_dir=workflows_dir)
    return _build_failure(message, detail=detail, speech=workflow["speech"])


def _resolve_or_create_workflow(workflow_id, source_type, input_fields, workflows_dir=None):
    if workflow_id:
        workflow, error = _load_workflow(workflow_id, workflows_dir=workflows_dir)
        if error:
            return None, error, None, None
        workflow, merge_error, merge_detail, merge_code = _merge_input(
            workflow, {"source_type": source_type, **(input_fields or {})}
        )
        if merge_error:
            return None, merge_error, merge_detail, merge_code
        return workflow, None, None, None
    return _new_workflow(source_type, input_fields), None, None, None


def create_or_update_workflow(
    workflow_id="",
    source_type="",
    input_fields=None,
    workflows_dir=None,
):
    input_fields = input_fields or {}
    if workflow_id:
        workflow, error = _load_workflow(workflow_id, workflows_dir=workflows_dir)
        if error:
            return _build_failure(error)
        workflow, merge_error, merge_detail, merge_code = _merge_input(
            workflow, {"source_type": source_type, **input_fields}
        )
        if merge_error:
            # Do not persist rejected merges; keep runtime status / snapshot intact.
            return _build_failure(
                merge_error,
                detail=merge_detail,
                speech=_safe_speech(f"{merge_error}；本次没有修改运行中任务，也没有发送到激光机。"),
                error_code=merge_code,
            )
    else:
        workflow = _new_workflow(source_type, input_fields)
    return _workflow_result(workflow, workflows_dir=workflows_dir)


def _preview_text_workflow(workflow, workflows_dir=None, text_generator=None):
    data = workflow.get("input", {})
    if not _has_text_preview_inputs(workflow):
        workflow["status"] = "collecting"
        workflow["speech"] = "文字预览还缺文字、材料或厚度；补齐后我再生成。"
        return _workflow_result(workflow, workflows_dir=workflows_dir)
    safety_error = _reject_unsafe_material(workflow, workflows_dir=workflows_dir)
    if safety_error:
        return safety_error
    # Re-read after safety; manual params already stripped by _clean_input_fields.
    data = workflow.get("input", {})
    generator = text_generator or text_laser_task_tool.generate_text_laser_task
    result = generator(
        text=data.get("text", ""),
        material=data.get("material", ""),
        thickness_mm=float(data.get("thickness_mm") or 0),
        laser_mode=data.get("laser_mode", "engrave"),
        engraving_mode=data.get("engraving_mode", ""),
        width_mm=float(data.get("width_mm") or 0),
        height_mm=float(data.get("height_mm") or 0),
        image_width=int(data.get("image_width") or text_laser_task_tool.text_image_tool.DEFAULT_IMAGE_WIDTH),
        image_height=int(data.get("image_height") or text_laser_task_tool.text_image_tool.DEFAULT_IMAGE_HEIGHT),
        font_size=int(data.get("font_size") or text_laser_task_tool.text_image_tool.DEFAULT_FONT_SIZE),
        font_path=data.get("font_path", ""),
        auto_wrap=bool(data.get("auto_wrap", text_laser_task_tool.text_image_tool.DEFAULT_AUTO_WRAP)),
        max_lines=int(data.get("max_lines") or text_laser_task_tool.text_image_tool.DEFAULT_MAX_LINES),
        layout_mode=data.get("layout_mode", text_laser_task_tool.text_image_tool.DEFAULT_LAYOUT_MODE),
        laser_min_power=data.get("laser_min_power"),
        laser_max_power=data.get("laser_max_power"),
        power_percent=data.get("power_percent"),
        feed_rate=data.get("feed_rate"),
        travel_rate=data.get("travel_rate"),
        pixel_size_mm=data.get("pixel_size_mm"),
        threshold=data.get("threshold"),
        passes=data.get("passes"),
        invert=bool(data.get("invert", False)),
        bidirectional=_coerce_bool(data.get("bidirectional"), False),
        overscan_mm=float(data.get("overscan_mm") or laser_grbl_tool.DEFAULT_RASTER_OVERSCAN_MM),
        raster_scan_direction=data.get("raster_scan_direction", "auto"),
        overwrite=True,
        auto_trim=bool(data.get("auto_trim", True)),
        trim_tolerance=float(data.get("trim_tolerance") or 20),
        auto_size=bool(data.get("auto_size", True)),
        dpi=float(data.get("dpi") or 300.0),
        lock_aspect_ratio=bool(data.get("lock_aspect_ratio", True)),
        offset_x_mm=float(data.get("offset_x_mm") or 0.0),
        offset_y_mm=float(data.get("offset_y_mm") or 0.0),
        safe_margin_mm=float(data.get("safe_margin_mm") or laser_grbl_tool.DEFAULT_SAFE_MARGIN_MM),
        send_after_generate=False,
        confirmed=False,
        dry_run=True,
        connection_mode=data.get("connection_mode", ""),
        network_host=data.get("network_host", ""),
        network_transport=data.get("network_transport", laser_material_calibration_tool.DEFAULT_NETWORK_TRANSPORT),
        network_http_port=int(data.get("network_http_port") or laser_network_grbl_tool.DEFAULT_HTTP_PORT),
        network_telnet_port=int(data.get("network_telnet_port") or laser_network_grbl_tool.DEFAULT_TELNET_PORT),
        network_timeout=float(data.get("network_timeout") or laser_network_grbl_tool.DEFAULT_TIMEOUT),
        prepared_gcode_dir=data.get("prepared_gcode_dir") or None,
        reuse_prepared_gcode=_coerce_bool(data.get("reuse_prepared_gcode"), True),
    )
    if not result.get("success"):
        return _set_failed(workflow, result.get("result", "文字预览生成失败"), result, workflows_dir)
    payload = result.get("result") or {}
    workflow["status"] = "preview_ready"
    workflow["source_type"] = "text"
    workflow["artifacts"] = {
        "task_id": payload.get("task_id"),
        "attempt_no": payload.get("attempt_no"),
        "image_file": payload.get("image_file"),
        "gcode_file": payload.get("gcode_file"),
        "task_file": payload.get("task_file"),
        "time_estimate": payload.get("time_estimate"),
        "auto_trim": payload.get("auto_trim"),
        "placement": payload.get("placement"),
        "text_layout": payload.get("text_layout"),
    }
    workflow["last_preview_result"] = payload
    bind_error = _bind_preview_artifact_integrity(workflow)
    if bind_error:
        return _set_failed(workflow, bind_error, workflows_dir=workflows_dir)
    workflow["speech"] = _append_physical_material_check(
        payload.get("speech") or _speech_for(workflow),
        workflow,
    )
    return _workflow_result(workflow, workflows_dir=workflows_dir, extra=payload)


def _preview_prepared_workflow(workflow, workflows_dir=None):
    data = workflow.get("input", {})
    gcode_file = str(data.get("gcode_file") or "").strip()
    if not gcode_file:
        workflow["status"] = "collecting"
        workflow["speech"] = "还缺已准备好的 G-code 文件路径，提供后才能进入预览。"
        return _workflow_result(workflow, workflows_dir=workflows_dir)
    # Label-only material on prepared files still cannot be a blocked/clarify plastic.
    safety_error = _reject_unsafe_material(workflow, workflows_dir=workflows_dir)
    if safety_error:
        return safety_error
    if not os.path.isfile(gcode_file):
        return _set_failed(workflow, "G-code 文件不存在", {"gcode_file": gcode_file}, workflows_dir)
    prepared = {
        "gcode_file": gcode_file,
        "source_file": gcode_file,
        "source": "prepared_gcode_file",
        "params_source": "prebaked_file",
    }
    laser_time_estimate.add_time_estimate_fields(prepared, gcode_file)
    workflow["status"] = "preview_ready"
    workflow["source_type"] = "prepared_gcode"
    workflow["artifacts"] = {
        "gcode_file": gcode_file,
        "time_estimate": prepared.get("time_estimate"),
        "prepared": prepared,
    }
    bind_error = _bind_preview_artifact_integrity(workflow)
    if bind_error:
        return _set_failed(workflow, bind_error, workflows_dir=workflows_dir)
    workflow["speech"] = _append_physical_material_check(_speech_for(workflow), workflow)
    return _workflow_result(workflow, workflows_dir=workflows_dir)


def _preview_summary_workflow(workflow, workflows_dir=None):
    data = workflow.get("input", {})
    summary_path = str(data.get("summary_path") or "").strip()
    if not summary_path:
        workflow["status"] = "collecting"
        workflow["speech"] = "还缺图片生成 summary_path，提供后我再读取预览状态。"
        return _workflow_result(workflow, workflows_dir=workflows_dir)
    # Input material gate (when caller supplied one).
    safety_error = _reject_unsafe_material(workflow, workflows_dir=workflows_dir)
    if safety_error:
        return safety_error
    summary, error = laser_asset_gcode_tool._read_summary(summary_path)
    if error:
        return _set_failed(workflow, error, {"summary_path": summary_path}, workflows_dir)
    projection = laser_asset_gcode_tool._summary_projection(summary)
    # Authoritative material is inside the summary; re-check after read.
    summary_material = projection.get("material") if isinstance(projection, dict) else None
    if summary_material in (None, ""):
        summary_material = summary.get("material") if isinstance(summary, dict) else None
    summary_thickness = None
    if isinstance(projection, dict):
        summary_thickness = projection.get("thickness_mm")
    if summary_thickness in (None, "") and isinstance(summary, dict):
        summary_thickness = summary.get("thickness_mm")
    safety_error = _reject_unsafe_material(
        workflow,
        workflows_dir=workflows_dir,
        material=summary_material,
        thickness_mm=summary_thickness,
    )
    if safety_error:
        return safety_error
    # Mirror summary material/thickness into input for speech and snapshot labels.
    if summary_material not in (None, ""):
        workflow.setdefault("input", {})["material"] = summary_material
    if summary_thickness not in (None, ""):
        workflow.setdefault("input", {})["thickness_mm"] = summary_thickness
    workflow["status"] = "preview_ready"
    workflow["source_type"] = "image_summary"
    workflow["artifacts"] = {
        "summary_path": summary_path,
        "gcode_file": projection.get("gcode_path"),
        "image_file": projection.get("processed_preview_path") or projection.get("preview_path"),
        "time_estimate": projection.get("time_estimate"),
        "summary": projection,
    }
    bind_error = _bind_preview_artifact_integrity(workflow)
    if bind_error:
        return _set_failed(workflow, bind_error, workflows_dir=workflows_dir)
    workflow["speech"] = _append_physical_material_check(_speech_for(workflow), workflow)
    return _workflow_result(workflow, workflows_dir=workflows_dir)


def _preview_image_workflow(workflow, workflows_dir=None, ai_runner=None):
    data = workflow.get("input", {})
    safety_error = _reject_unsafe_material(workflow, workflows_dir=workflows_dir)
    if safety_error:
        return safety_error
    data = workflow.get("input", {})
    runner = ai_runner or laser_asset_gcode_tool.generate_ai_laser_gcode_job
    runner_kwargs = {
        "action": "generate",
        "image_file": data.get("image_file", ""),
        "prompt": data.get("prompt", ""),
        "source_type": data.get("source_type", "file"),
        "text": data.get("text", ""),
        "image_url": data.get("image_url", ""),
        "candidate_selection": data.get("candidate_selection"),
        "candidate_id": data.get("candidate_id", ""),
        "candidate_index": data.get("candidate_index"),
        "output_dir": data.get("output_dir", ""),
        "output_format": data.get("output_format", ""),
        "mode": data.get("mode", ""),
        "material": data.get("material", ""),
        "material_library": data.get("material_library", "") or laser_asset_gcode_tool.default_material_library_path(),
        "thickness_mm": _positive_float_or_none(data.get("thickness_mm")),
        "task_type": data.get("task_type", ""),
    }
    # Trusted draw-lab entry may inject policies into workflow input; never accept
    # them from ordinary /api/workflow or MCP kwargs.
    material_match_policy = str(data.get("material_match_policy") or "").strip()
    send_policy = str(data.get("send_policy") or "").strip()
    if material_match_policy == "nearest_engrave" and send_policy == "confirmed_material_record":
        runner_kwargs["material_match_policy"] = material_match_policy
        runner_kwargs["send_policy"] = send_policy
    width_mm = _positive_float_or_none(data.get("width_mm"))
    height_mm = _positive_float_or_none(data.get("height_mm"))
    if width_mm is not None:
        runner_kwargs["width_mm"] = width_mm
    if height_mm is not None:
        runner_kwargs["height_mm"] = height_mm
    if "lock_aspect_ratio" in data and data.get("lock_aspect_ratio") is not None:
        runner_kwargs["lock_aspect_ratio"] = _coerce_bool(data.get("lock_aspect_ratio"), True)
    explicit_size_mm = _positive_float_or_none(data.get("size_mm"))
    if explicit_size_mm is not None:
        runner_kwargs["size_mm"] = explicit_size_mm
    pixel_size_mm = _positive_float_or_none(data.get("pixel_size_mm"))
    if pixel_size_mm is not None:
        runner_kwargs["pixel_size_mm"] = pixel_size_mm
    raster_scan_direction = str(data.get("raster_scan_direction") or "").strip()
    if raster_scan_direction:
        runner_kwargs["raster_scan_direction"] = raster_scan_direction
    dither_algorithm = str(data.get("dither_algorithm") or "").strip()
    if dither_algorithm:
        runner_kwargs["dither_algorithm"] = dither_algorithm
    if "threshold" in data and data.get("threshold") not in (None, ""):
        try:
            runner_kwargs["threshold"] = int(data.get("threshold"))
        except (TypeError, ValueError):
            return _set_failed(
                workflow,
                "threshold 必须是 0 到 255 的整数，或 -1 表示自动",
                {"field": "threshold", "value": data.get("threshold")},
            )
    raster_output_strategy = str(data.get("raster_output_strategy") or "").strip()
    if raster_output_strategy:
        runner_kwargs["raster_output_strategy"] = raster_output_strategy
    raster_quality_strategy = str(data.get("raster_quality_strategy") or "").strip()
    if raster_quality_strategy:
        runner_kwargs["raster_quality_strategy"] = raster_quality_strategy
    # Outline simplify is only meaningful for outline/logo paths; ignore residue in raster/auto.
    mode_value = str(data.get("mode") or "").strip().lower()
    if mode_value == "outline" and "vector_simplify_factor" in data:
        simplify = data.get("vector_simplify_factor")
        if simplify not in (None, ""):
            try:
                simplify_value = float(simplify)
            except (TypeError, ValueError):
                return _set_failed(
                    workflow,
                    "vector_simplify_factor 必须是 0.25 到 8 之间的有限数值",
                    {"field": "vector_simplify_factor", "value": simplify},
                    workflows_dir,
                )
            if (
                not math.isfinite(simplify_value)
                or simplify_value < 0.25
                or simplify_value > 8.0
            ):
                return _set_failed(
                    workflow,
                    "vector_simplify_factor 必须满足 0.25 <= value <= 8",
                    {"field": "vector_simplify_factor", "value": simplify_value},
                    workflows_dir,
                )
            runner_kwargs["vector_simplify_factor"] = simplify_value
    result = runner(**runner_kwargs)
    if not result.get("success"):
        return _set_failed(workflow, result.get("result", "图片预览生成失败"), result, workflows_dir)
    payload = result.get("result") or {}
    summary = payload.get("summary") or {}
    # Re-check material from generated summary before binding a sendable preview.
    safety_error = _reject_unsafe_material(
        workflow,
        workflows_dir=workflows_dir,
        material=summary.get("material"),
        thickness_mm=summary.get("thickness_mm"),
    )
    if safety_error:
        return safety_error
    if summary.get("material") not in (None, ""):
        workflow.setdefault("input", {})["material"] = summary.get("material")
    if summary.get("thickness_mm") not in (None, ""):
        workflow.setdefault("input", {})["thickness_mm"] = summary.get("thickness_mm")
    workflow["status"] = "preview_ready"
    workflow["source_type"] = "image_summary"
    workflow["artifacts"] = {
        "summary_path": payload.get("summary_path"),
        "gcode_file": summary.get("gcode_path"),
        "image_file": summary.get("processed_preview_path") or summary.get("preview_path"),
        "time_estimate": summary.get("time_estimate"),
        "summary": summary,
    }
    bind_error = _bind_preview_artifact_integrity(workflow)
    if bind_error:
        return _set_failed(workflow, bind_error, workflows_dir=workflows_dir)
    # Prefer gate-aware speech when send is blocked so UI does not claim "ready to send".
    if _preview_send_block_reason(workflow):
        speech_base = _speech_for(workflow)
    else:
        speech_base = payload.get("speech") or _speech_for(workflow)
    workflow["speech"] = _append_physical_material_check(speech_base, workflow)
    return _workflow_result(workflow, workflows_dir=workflows_dir, extra=payload)


def preview_workflow(
    workflow_id="",
    source_type="",
    input_fields=None,
    workflows_dir=None,
    text_generator=None,
    ai_runner=None,
):
    if workflow_id:
        existing, load_error = _load_workflow(workflow_id, workflows_dir=workflows_dir)
        if load_error:
            return _build_failure(load_error)
        if existing.get("status") in RUNTIME_LOCKED_STATUSES:
            return _build_failure(
                "发送中的任务不允许重新预览；请先 status/cancel。",
                detail={"status": existing.get("status"), "workflow": _workflow_summary(existing)},
                speech="任务正在发送，不能修改生产预览；请查询状态或取消后再预览。",
                error_code="workflow_busy",
            )
    workflow, error, merge_detail, merge_code = _resolve_or_create_workflow(
        workflow_id,
        source_type,
        input_fields or {},
        workflows_dir=workflows_dir,
    )
    if error:
        return _build_failure(error, detail=merge_detail, error_code=merge_code)
    source = workflow.get("source_type")
    if source == "text":
        return _preview_text_workflow(workflow, workflows_dir=workflows_dir, text_generator=text_generator)
    if source == "prepared_gcode":
        return _preview_prepared_workflow(workflow, workflows_dir=workflows_dir)
    if source == "image_summary":
        return _preview_summary_workflow(workflow, workflows_dir=workflows_dir)
    if source == "image":
        return _preview_image_workflow(workflow, workflows_dir=workflows_dir, ai_runner=ai_runner)
    return _set_failed(workflow, "source_type 必须是 text、image、image_summary 或 prepared_gcode", workflows_dir=workflows_dir)


def _first_job_id(payload):
    if isinstance(payload, dict):
        if payload.get("job_id"):
            return payload.get("job_id")
        for value in payload.values():
            found = _first_job_id(value)
            if found:
                return found
    if isinstance(payload, list):
        for item in payload:
            found = _first_job_id(item)
            if found:
                return found
    return ""


def _first_status(payload):
    if isinstance(payload, dict):
        if isinstance(payload.get("status"), str):
            return payload.get("status")
        for value in payload.values():
            found = _first_status(value)
            if found:
                return found
    if isinstance(payload, list):
        for item in payload:
            found = _first_status(item)
            if found:
                return found
    return ""


def _canonical_runtime_status(status):
    normalized = str(status or "").strip().lower()
    return RUNTIME_STATUS_ALIASES.get(normalized, normalized)


def _first_present(*values, default="unknown"):
    for value in values:
        if value is not None and value != "":
            return value
    return default


def _norm_path(path):
    """Canonical absolute path for storage (preserve case; compare via _paths_equal)."""
    text = str(path or "").strip()
    if not text:
        return ""
    return os.path.normpath(os.path.abspath(text))


def _paths_equal(left, right):
    a = _norm_path(left)
    b = _norm_path(right)
    if not a or not b:
        return a == b
    return os.path.normcase(a) == os.path.normcase(b)


def _canonical_laser_mode(value):
    """Normalize process laser_mode only.

    Text path: engrave/cut aliases.
    Image JobParams path: firmware laser modes M3/M4.
    Generation modes (outline/raster) are NOT laser_mode aliases.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    lowered = text.casefold().replace("-", "_").replace(" ", "_")
    aliases = {
        "engrave": "engrave",
        "engraving": "engrave",
        "carve": "engrave",
        "cut": "cut",
        "cutting": "cut",
        "m3": "M3",
        "m4": "M4",
        "unknown": "unknown",
    }
    return aliases.get(lowered, text if text in {"M3", "M4"} else lowered)


def _canonical_generation_mode(value):
    """Normalize image generation/output mode (outline/raster), not laser M3/M4."""
    text = str(value or "").strip()
    if not text:
        return ""
    lowered = text.casefold().replace("-", "_").replace(" ", "_")
    aliases = {
        "outline": "outline",
        "vector": "outline",
        "line": "outline",
        "raster": "raster",
        "auto": "auto",
        "unknown": "unknown",
    }
    return aliases.get(lowered, lowered)


def _canonical_material_name(material):
    raw = str(material or "").strip()
    if not raw:
        return ""
    try:
        data, error = laser_material_calibration_tool._load_material_params()
        if error:
            return raw.casefold()
        canonical, _entry, find_error = laser_material_calibration_tool._find_material(data, raw)
        if find_error or not canonical:
            return raw.casefold()
        return str(canonical).casefold()
    except Exception:
        return raw.casefold()


def _connection_from_mapping(data=None, connection_mode=""):
    """Capture only connection fields present in data; do not invent bool defaults here."""
    data = data or {}
    mode = str(connection_mode or data.get("connection_mode") or "").strip()
    connection = {
        "mode": mode,
        "network_host": str(data.get("network_host") or "").strip(),
        "network_transport": str(data.get("network_transport") or "").strip(),
        "network_http_port": data.get("network_http_port", "") if "network_http_port" in data else "",
        "network_telnet_port": data.get("network_telnet_port", "") if "network_telnet_port" in data else "",
        "serial_port": str(data.get("port") or data.get("serial_port") or "").strip(),
        "baudrate": data.get("baudrate", "") if "baudrate" in data else "",
        "network_timeout": data.get("network_timeout", "") if "network_timeout" in data else "",
    }
    if "run_in_background" in data and data.get("run_in_background") not in (None, ""):
        connection["run_in_background"] = _coerce_bool(data.get("run_in_background"), True)
    else:
        connection["run_in_background"] = ""
    if "wait_for_response" in data and data.get("wait_for_response") not in (None, ""):
        connection["wait_for_response"] = _coerce_bool(data.get("wait_for_response"), True)
    else:
        connection["wait_for_response"] = ""
    return connection


def _discard_confirmation_snapshot(workflow):
    """Invalidate any previous confirm binding when rebinding fails or regenerates."""
    workflow["confirmation_snapshot"] = {}
    artifacts = dict(workflow.get("artifacts") or {})
    artifacts.pop("gcode_sha256", None)
    workflow["artifacts"] = artifacts


def _bind_preview_artifact_integrity(workflow):
    """After preview success: require real gcode file, persist sha256 + confirmation_snapshot."""
    artifacts = dict(workflow.get("artifacts") or {})
    gcode_file = str(artifacts.get("gcode_file") or "").strip()
    if not gcode_file:
        _discard_confirmation_snapshot(workflow)
        return "预览未绑定 G-code 文件，请重新生成预览"
    if not os.path.isfile(gcode_file):
        _discard_confirmation_snapshot(workflow)
        return "预览 G-code 文件不存在或不可读，请重新生成预览"
    try:
        digest = file_sha256(gcode_file)
    except OSError:
        _discard_confirmation_snapshot(workflow)
        return "无法读取预览 G-code 以计算摘要，请重新生成预览"
    canonical_path = _norm_path(gcode_file) or gcode_file
    artifacts["gcode_file"] = canonical_path
    artifacts["gcode_sha256"] = digest
    workflow["artifacts"] = artifacts
    snapshot, error = _build_preview_confirmation_snapshot(workflow)
    if error:
        _discard_confirmation_snapshot(workflow)
        return error
    workflow["confirmation_snapshot"] = snapshot
    return None


def _missing_authority_keys(snapshot, required_keys):
    missing = []
    for key in required_keys:
        value = snapshot.get(key)
        if value in (None, "", "unknown"):
            missing.append(key)
    return missing


def _authority_keys_for_source(source_type):
    if source_type == "text":
        return TEXT_AUTHORITY_KEYS
    if source_type == "image_summary":
        return IMAGE_AUTHORITY_KEYS
    if source_type == "prepared_gcode":
        return PREPARED_AUTHORITY_KEYS
    return ()


def _validate_snapshot_authority(snapshot, workflow_source_type=None):
    """Confirm-time source-specific schema check; fail closed on missing authority keys."""
    if not isinstance(snapshot, dict):
        return "预览确认快照缺失，请重新生成预览", {"reason": "snapshot_not_dict"}, "preview_content_mismatch"
    source = str(snapshot.get("source_type") or "").strip()
    if not source:
        return "预览确认快照缺少 source_type，请重新生成预览", {"field": "source_type"}, "preview_content_mismatch"
    if workflow_source_type and source != workflow_source_type:
        return (
            "预览确认快照 source_type 不一致，请重新生成预览",
            {"snapshot_source_type": source, "workflow_source_type": workflow_source_type},
            "preview_content_mismatch",
        )
    required = _authority_keys_for_source(source)
    if not required:
        return f"不支持的确认 source_type: {source}", {"source_type": source}, "preview_content_mismatch"
    missing = _missing_authority_keys(snapshot, required)
    if missing:
        return (
            f"确认快照缺少最终权威字段: {', '.join(missing)}",
            {"missing": missing, "source_type": source},
            "preview_content_mismatch",
        )
    return None, None, None


def _quality_profile_name(profile):
    if not isinstance(profile, dict):
        return ""
    return _first_present(profile.get("name"), profile.get("strategy"), profile.get("id"), default="")


def _snapshot_strategy_fields(data, params=None, summary=None, *, source="text"):
    """Capture production strategy fields for freeze compare.

    Image path freezes *final nested* raster / auto_raster_profile values from the
    production summary schema, not request-time top-level mirrors.
    """
    params = params if isinstance(params, dict) else {}
    summary = summary if isinstance(summary, dict) else {}
    data = data if isinstance(data, dict) else {}
    fields = {key: "" for key in STRATEGY_SNAPSHOT_KEYS}

    if source == "image_summary":
        raster = summary.get("raster") if isinstance(summary.get("raster"), dict) else {}
        auto_profile = summary.get("auto_raster_profile") if isinstance(summary.get("auto_raster_profile"), dict) else {}
        image_preprocess = summary.get("image_preprocess") if isinstance(summary.get("image_preprocess"), dict) else {}
        # Final resolved raster strategy authority.
        fields["pixel_size_mm"] = _first_present(raster.get("pixel_size_mm"), default="")
        fields["overscan_mm"] = _first_present(raster.get("overscan_mm"), default="")
        fields["dither_algorithm"] = _first_present(raster.get("dither_algorithm"), default="")
        fields["raster_scan_direction"] = _first_present(
            raster.get("scan_direction"),
            raster.get("scan_direction_requested"),
            default="",
        )
        fields["raster_output_strategy"] = _first_present(
            raster.get("output_strategy"),
            raster.get("output_strategy_requested"),
            default="",
        )
        fields["raster_quality_strategy"] = _first_present(
            _quality_profile_name(auto_profile),
            _quality_profile_name(raster.get("quality_profile")),
            default="",
        )
        fields["bidirectional"] = _first_present(raster.get("snake_scan"), default="")
        fields["threshold"] = _first_present(image_preprocess.get("threshold"), default="")
        fields["invert"] = _first_present(image_preprocess.get("invert"), default="")
        fields["task_type"] = _first_present(summary.get("task_type"), data.get("task_type"), default="")
        fields["engraving_mode"] = _first_present(summary.get("engraving_mode"), data.get("engraving_mode"), default="")
        return fields

    # text / prepared: freeze request-bound strategy from params/input when present
    for key in STRATEGY_SNAPSHOT_KEYS:
        value = _first_present(
            params.get(key) if key in params else None,
            data.get(key) if key in data else None,
            default="",
        )
        fields[key] = value if value not in (None,) else ""
    for key in ("invert", "bidirectional"):
        if key in params:
            fields[key] = params.get(key)
        elif key in data:
            fields[key] = data.get(key)
    for key in ("pixel_size_mm", "threshold", "overscan_mm"):
        if key in params and params.get(key) not in (None, ""):
            fields[key] = params.get(key)
        elif key in data and data.get(key) not in (None, ""):
            fields[key] = data.get(key)
    return fields


def _build_preview_confirmation_snapshot(workflow):
    source = workflow.get("source_type")
    data = workflow.get("input") or {}
    artifacts = workflow.get("artifacts") or {}
    gcode_file = _norm_path(artifacts.get("gcode_file") or "") or str(artifacts.get("gcode_file") or "").strip()
    gcode_sha = str(artifacts.get("gcode_sha256") or "").strip().lower()
    if not gcode_file or not gcode_sha:
        return None, "预览缺少 G-code 路径或摘要"
    connection = _connection_from_mapping(data, data.get("connection_mode", ""))
    snapshot = {
        "source_type": source,
        "gcode_file": gcode_file,
        "gcode_sha256": gcode_sha,
        "material": _first_present(data.get("material"), default=""),
        "thickness_mm": _first_present(data.get("thickness_mm"), default=""),
        "connection_mode": connection.get("mode") or "",
        "connection": connection,
        "time_estimate": artifacts.get("time_estimate"),
    }
    if source == "text":
        preview = workflow.get("last_preview_result") or {}
        params = preview.get("params") if isinstance(preview.get("params"), dict) else {}
        recommendation = preview.get("recommendation") if isinstance(preview.get("recommendation"), dict) else {}
        passes = _first_present(preview.get("passes_applied"), params.get("passes"))
        # text laser_mode is process mode (engrave/cut); not image M3/M4 or generation mode.
        laser_mode_value = _first_present(
            data.get("laser_mode"),
            params.get("laser_mode"),
            default="engrave",
        )
        snapshot["laser_mode"] = _canonical_laser_mode(laser_mode_value)
        snapshot["mode"] = _first_present(data.get("mode"), params.get("mode"), default="")
        strategy = _snapshot_strategy_fields(data, params=params, source="text")
        snapshot.update(strategy)
        snapshot.update(
            {
                "power_percent": _first_present(params.get("power_percent")),
                "laser_min_power": _first_present(params.get("laser_min_power")),
                "laser_max_power": _first_present(params.get("laser_max_power")),
                "s_max": _first_present(params.get("s_max")),
                "feed_rate": _first_present(params.get("feed_rate")),
                "travel_rate": _first_present(params.get("travel_rate")),
                "passes": passes,
                "params": params,
                "matched_thickness_mm": _first_present(recommendation.get("matched_thickness_mm")),
                "match": _first_present(recommendation.get("match")),
                "warnings": recommendation.get("warnings") if isinstance(recommendation.get("warnings"), list) else [],
                "can_send": recommendation.get("can_send") is True,
                "send_blocked_reason": str(recommendation.get("send_blocked_reason") or "").strip(),
            }
        )
        missing = _missing_authority_keys(snapshot, TEXT_AUTHORITY_KEYS)
        if missing:
            return None, f"文字预览缺少最终权威字段: {', '.join(missing)}"
    elif source == "image_summary":
        summary = artifacts.get("summary") if isinstance(artifacts.get("summary"), dict) else {}
        # Send-gate validation must not fail preview binding: keep artifacts/preview visible
        # when can_send=false; block only confirm_send via next_actions + confirm path.
        send_error = laser_asset_gcode_tool._validate_summary_for_send(summary)
        raster = summary.get("raster") if isinstance(summary.get("raster"), dict) else {}
        auto_profile = summary.get("auto_raster_profile") if isinstance(summary.get("auto_raster_profile"), dict) else {}
        # Production schema:
        # - laser_mode: M3/M4 (firmware)
        # - mode: outline/raster (generation)
        # - power: raw S value (NOT power_percent)
        laser_mode_value = _first_present(
            summary.get("laser_mode"),
            data.get("laser_mode"),
            default="M4",
        )
        generation_mode = _first_present(
            summary.get("mode"),
            data.get("mode"),
            default="",
        )
        strategy = _snapshot_strategy_fields(data, summary=summary, source="image_summary")
        snapshot.update(strategy)
        can_send_value = False if send_error else bool(summary.get("can_send"))
        snapshot.update(
            {
                "material": _first_present(summary.get("material"), data.get("material"), default=""),
                "thickness_mm": _first_present(summary.get("thickness_mm"), data.get("thickness_mm"), default=""),
                "laser_mode": _canonical_laser_mode(laser_mode_value),
                "mode": _canonical_generation_mode(generation_mode),
                "task_type": _first_present(summary.get("task_type"), data.get("task_type"), default=""),
                # freeze raw S power under "power"; do not invent power_percent from S
                "power": _first_present(summary.get("power")),
                "power_percent": _first_present(summary.get("power_percent"), default=""),
                "laser_min_power": _first_present(summary.get("laser_min_power")),
                "laser_max_power": _first_present(summary.get("laser_max_power")),
                "s_max": _first_present(summary.get("s_max"), summary.get("power")),
                "feed_rate": _first_present(summary.get("feed_rate"), summary.get("speed")),
                "travel_rate": _first_present(summary.get("travel_rate")),
                "passes": _first_present(summary.get("passes")),
                "summary_path": artifacts.get("summary_path") or data.get("summary_path") or "",
                "raster": raster,
                "auto_raster_profile": auto_profile,
                "contract_version": summary.get("contract_version"),
                "recommendation_status": summary.get("recommendation_status"),
                "can_send": can_send_value,
                "send_blocked_reason": send_error or "",
                "safety_report": summary.get("safety_report"),
            }
        )
        locked_gcode = str(summary.get("gcode_path") or "").strip()
        if not locked_gcode:
            return None, "图片预览 summary 缺少 gcode_path"
        if not _paths_equal(locked_gcode, gcode_file):
            return None, "图片预览锁定的 G-code 与 artifact 不一致"
        missing = _missing_authority_keys(snapshot, IMAGE_AUTHORITY_KEYS)
        if missing:
            return None, f"图片预览缺少最终权威字段: {', '.join(missing)}"
    elif source == "prepared_gcode":
        strategy = _snapshot_strategy_fields(data, source="prepared_gcode")
        snapshot.update(strategy)
        snapshot.update(
            {
                "params_source": "prebaked_file",
                "power_percent": "unknown",
                "power": "unknown",
                "laser_min_power": "unknown",
                "laser_max_power": "unknown",
                "s_max": "unknown",
                "feed_rate": "unknown",
                "travel_rate": "unknown",
                "passes": "unknown",
                "material": _first_present(data.get("material"), default="unknown"),
                "thickness_mm": _first_present(data.get("thickness_mm"), default="unknown"),
                "laser_mode": _canonical_laser_mode(data.get("laser_mode")) if data.get("laser_mode") not in (None, "") else "unknown",
                "mode": _first_present(data.get("mode"), default="unknown"),
                "engraving_mode": _first_present(data.get("engraving_mode"), default="unknown"),
            }
        )
        missing = _missing_authority_keys(snapshot, PREPARED_AUTHORITY_KEYS)
        if missing:
            return None, f"预制 G-code 预览缺少最终权威字段: {', '.join(missing)}"
    else:
        return None, "该 source_type 暂不支持确认发送"
    return snapshot, None


def _values_equal(key, expected, supplied):
    if expected in (None, "") and supplied in (None, ""):
        return True
    if key == "material":
        return _canonical_material_name(expected) == _canonical_material_name(supplied)
    if key == "thickness_mm":
        try:
            return abs(float(expected) - float(supplied)) <= 0.001 + 1e-12
        except (TypeError, ValueError):
            return str(expected) == str(supplied)
    if key == "laser_mode":
        return _canonical_laser_mode(expected) == _canonical_laser_mode(supplied)
    if key == "mode":
        return _canonical_generation_mode(expected) == _canonical_generation_mode(supplied)
    if key == "connection_mode":
        return str(expected or "").strip().casefold() == str(supplied or "").strip().casefold()
    if key in {
        "power_percent",
        "power",
        "laser_min_power",
        "laser_max_power",
        "s_max",
        "feed_rate",
        "travel_rate",
        "passes",
        "pixel_size_mm",
        "threshold",
        "overscan_mm",
        "baudrate",
        "network_http_port",
        "network_telnet_port",
        "network_timeout",
    }:
        if expected in ("unknown", None, "") or supplied in ("unknown", None, ""):
            return str(expected) == str(supplied)
        try:
            return float(expected) == float(supplied)
        except (TypeError, ValueError):
            return str(expected).strip() == str(supplied).strip()
    if key in {"invert", "bidirectional"}:
        if expected in (None, "") and supplied in (None, ""):
            return True
        return _coerce_bool(expected, False) == _coerce_bool(supplied, False)
    if key == "gcode_file":
        return _paths_equal(expected, supplied)
    if key in {"raster_scan_direction", "dither_algorithm", "raster_output_strategy", "raster_quality_strategy", "task_type", "engraving_mode"}:
        left = str(expected or "").strip().casefold().replace("-", "_").replace(" ", "_")
        right = str(supplied or "").strip().casefold().replace("-", "_").replace(" ", "_")
        return left == right
    return str(expected).strip() == str(supplied).strip()


def _connection_field_present(value):
    return value not in (None, "")


def _sanitize_override_fields(fields):
    """Drop empty values only. Do not strip manual params — confirm must still compare them."""
    clean = {}
    for key, value in (fields or {}).items():
        if key.startswith("_"):
            continue
        if value is None:
            continue
        if isinstance(value, str) and value == "":
            continue
        clean[key] = value
    return clean


def _compare_frozen_overrides(snapshot, overrides):
    clean = _sanitize_override_fields(overrides or {})
    source = snapshot.get("source_type")

    # Reject production API fields that are not frozen for equality compare and are not
    # connection fill. Silent ignore of production overrides is forbidden
    # (text, summary_path, width_mm, source_type, ...).
    unfrozen_production = sorted(
        key
        for key in clean
        if key in API_PRODUCTION_INPUT_KEYS
        and key not in FROZEN_PROCESSING_KEYS
        and key not in CONNECTION_KEY_SET
    )
    if unfrozen_production:
        return (
            f"确认不得覆盖未冻结的生产字段: {', '.join(unfrozen_production)}",
            {"fields": unfrozen_production},
            "confirmation_mismatch",
        )

    prepared_label_keys = (
        "material",
        "thickness_mm",
        "laser_mode",
        "mode",
        "engraving_mode",
        "gcode_file",
        "task_type",
    )
    if source == "prepared_gcode":
        # Any concrete processing / strategy override is forbidden at confirm.
        for key in PREPARED_FORBIDDEN_CONFIRM_PROCESSING_KEYS:
            if key in clean:
                return (
                    f"预制 G-code 确认不得新增加工参数: {key}",
                    {"field": key, "preview": snapshot.get(key), "confirmed": clean.get(key)},
                    "confirmation_mismatch",
                )
        # Labels may only match an already-frozen non-unknown preview value.
        for key in prepared_label_keys:
            if key not in clean:
                continue
            expected = snapshot.get(key)
            supplied = clean.get(key)
            if expected in ("unknown", "", None) and supplied not in ("unknown", "", None):
                return (
                    f"预制 G-code 确认不得新增标签: {key}",
                    {"field": key, "preview": expected, "confirmed": supplied},
                    "confirmation_mismatch",
                )
            if not _values_equal(key, expected, supplied):
                return (
                    f"预制 G-code 确认标签与预览不一致: {key}",
                    {"field": key, "preview": expected, "confirmed": supplied},
                    "confirmation_mismatch",
                )
        # Fail closed on any other frozen processing key not covered above.
        for key in FROZEN_PROCESSING_KEYS:
            if key in clean and key not in prepared_label_keys and key not in PREPARED_FORBIDDEN_CONFIRM_PROCESSING_KEYS:
                return (
                    f"预制 G-code 确认不得新增加工参数: {key}",
                    {"field": key, "preview": snapshot.get(key), "confirmed": clean.get(key)},
                    "confirmation_mismatch",
                )
        return None, None, None

    for key in FROZEN_PROCESSING_KEYS:
        if key not in clean:
            continue
        expected = snapshot.get(key)
        supplied = clean.get(key)
        if key == "gcode_file":
            expected = snapshot.get("gcode_file")
        # Omitted snapshot strategy field: supplying a concrete production value is a mismatch.
        if expected in (None, "", "unknown") and supplied not in (None, ""):
            return (
                f"确认字段与预览不一致: {key}",
                {"field": key, "preview": expected, "confirmed": supplied},
                "confirmation_mismatch",
            )
        if not _values_equal(key, expected, supplied):
            return (
                f"确认字段与预览不一致: {key}",
                {
                    "field": key,
                    "preview": expected,
                    "confirmed": supplied,
                },
                "confirmation_mismatch",
            )
    return None, None, None


def _fill_connection_from_overrides(snapshot, overrides):
    clean = _clean_input_fields(overrides or {})
    connection = dict(snapshot.get("connection") or {})
    mode = str(snapshot.get("connection_mode") or connection.get("mode") or "").strip()

    def freeze_or_fill(snap_key, override_key, conn_key=None):
        nonlocal mode
        conn_key = conn_key or override_key
        current = connection.get(conn_key, "") if conn_key != "mode" else mode
        if override_key not in clean and (conn_key == "mode" and "connection_mode" not in clean):
            return None, None, None
        supplied = clean.get(override_key, clean.get("connection_mode") if override_key == "connection_mode" else None)
        if override_key == "connection_mode":
            supplied = clean.get("connection_mode")
        if _connection_field_present(current):
            if supplied is not None and not _values_equal(override_key if override_key != "port" else "serial_port", current, supplied):
                return (
                    f"确认不得更换预览已有连接字段: {override_key}",
                    {"field": override_key, "preview": current, "confirmed": supplied},
                    "confirmation_mismatch",
                )
            return None, None, None
        # missing in snapshot: fill
        if supplied is not None and supplied != "":
            if conn_key == "mode" or override_key == "connection_mode":
                mode = str(supplied).strip()
                connection["mode"] = mode
            elif override_key == "port":
                connection["serial_port"] = str(supplied).strip()
            else:
                connection[conn_key] = supplied
        return None, None, None

    for override_key, conn_key in (
        ("connection_mode", "mode"),
        ("port", "serial_port"),
        ("baudrate", "baudrate"),
        ("network_host", "network_host"),
        ("network_transport", "network_transport"),
        ("network_http_port", "network_http_port"),
        ("network_telnet_port", "network_telnet_port"),
        ("network_timeout", "network_timeout"),
        ("run_in_background", "run_in_background"),
        ("wait_for_response", "wait_for_response"),
    ):
        err, detail, code = freeze_or_fill(override_key, override_key, conn_key)
        if err:
            return None, err, detail, code

    # bool defaults if still empty
    if "run_in_background" not in connection or connection.get("run_in_background") in (None, ""):
        connection["run_in_background"] = True
    if "wait_for_response" not in connection or connection.get("wait_for_response") in (None, ""):
        connection["wait_for_response"] = True

    resolved_mode, mode_error = laser_execution.resolve_laser_connection_mode(mode)
    if mode_error:
        return None, mode_error, {"connection_mode": mode}, "confirmation_mismatch"
    connection["mode"] = resolved_mode
    return {"connection_mode": resolved_mode, "connection": connection}, None, None, None


def _verify_artifact_matches_snapshot(snapshot):
    gcode_file = str(snapshot.get("gcode_file") or "").strip()
    expected = str(snapshot.get("gcode_sha256") or "").strip().lower()
    if not gcode_file or not expected:
        return None, "预览快照缺少文件摘要，请重新生成预览", {"gcode_file": gcode_file}, "preview_content_mismatch"
    if not os.path.isfile(gcode_file):
        return None, "预览 G-code 文件缺失，请重新生成预览", None, "preview_content_mismatch"
    try:
        actual = file_sha256(gcode_file)
    except OSError:
        return None, "无法读取预览 G-code 以校验摘要", None, "preview_content_mismatch"
    if actual != expected:
        return None, "preview_content_mismatch: G-code 内容与预览摘要不一致", {"expected_gcode_sha256": expected, "actual_gcode_sha256": actual}, "preview_content_mismatch"
    return {"gcode_file": gcode_file, "expected_gcode_sha256": expected, "actual_gcode_sha256": actual}, None, None, None


def _build_exact_prepared_result(workflow, snapshot, integrity):
    artifacts = workflow.get("artifacts") or {}
    prepared = {
        "source_file": integrity["gcode_file"],
        "gcode_file": integrity["gcode_file"],
        "converted": False,
        "expected_gcode_sha256": integrity["expected_gcode_sha256"],
        "source": "workflow_confirmation_snapshot",
        "params_source": snapshot.get("params_source") or snapshot.get("source_type"),
    }
    if artifacts.get("time_estimate"):
        prepared["time_estimate"] = artifacts.get("time_estimate")
    if snapshot.get("source_type") == "prepared_gcode":
        base = artifacts.get("prepared") if isinstance(artifacts.get("prepared"), dict) else {}
        prepared = {**base, **prepared}
        prepared["source"] = base.get("source") or "prepared_gcode_file"
        prepared["params_source"] = "prebaked_file"
        prepared["converted"] = False
    return prepared


def _invoke_unified_send_file(prepared_result, mode, connection, *, execution_sender=None, prepared_sender=None, tuned_job_sender=None):
    """Call core.laser_execution.send_file once with the canonical injection shape."""
    wait_for_response = _coerce_bool(connection.get("wait_for_response"), True)
    run_in_background = _coerce_bool(connection.get("run_in_background"), True)
    port = connection.get("serial_port") or ""
    baudrate = int(connection.get("baudrate") or laser_grbl_tool.DEFAULT_BAUDRATE)
    network_host = connection.get("network_host") or ""
    network_transport = connection.get("network_transport") or laser_material_calibration_tool.DEFAULT_NETWORK_TRANSPORT
    network_http_port = int(connection.get("network_http_port") or laser_network_grbl_tool.DEFAULT_HTTP_PORT)
    network_telnet_port = int(connection.get("network_telnet_port") or laser_network_grbl_tool.DEFAULT_TELNET_PORT)
    network_timeout = float(connection.get("network_timeout") or laser_network_grbl_tool.DEFAULT_TIMEOUT)

    send_kwargs = {
        "confirmed": True,
        "run_in_background": run_in_background,
        "wait_for_response": wait_for_response,
    }
    if mode == "serial":
        send_kwargs.update({"port": port, "baudrate": baudrate})
    else:
        send_kwargs.update(
            {
                "host": network_host,
                "transport": network_transport,
                "http_port": network_http_port,
                "telnet_port": network_telnet_port,
                "timeout": network_timeout,
            }
        )

    # Select exactly one sender once; never probe signatures with TypeError retries.
    sender = execution_sender or prepared_sender or tuned_job_sender
    if sender is not None:
        return sender(prepared_result, mode, **send_kwargs)

    if mode == "network":
        resolved_host, error = laser_execution.resolve_laser_network_host(network_host)
        if error:
            return {"success": False, "result": error, "detail": prepared_result}
        return laser_execution.send_file(
            prepared_result,
            "network",
            confirmed=True,
            run_in_background=run_in_background,
            host=resolved_host,
            transport=network_transport,
            http_port=network_http_port,
            telnet_port=network_telnet_port,
            timeout=network_timeout,
            wait_for_response=wait_for_response,
        )
    return laser_execution.send_file(
        prepared_result,
        "serial",
        confirmed=True,
        run_in_background=run_in_background,
        port=port,
        baudrate=baudrate,
        wait_for_response=wait_for_response,
    )


def _reject_preview_ready(workflow, message, detail=None, error_code="confirmation_mismatch", workflows_dir=None):
    """Fail confirm without changing preview_ready and without calling senders."""
    workflow["status"] = "preview_ready"
    workflow["speech"] = _safe_speech(f"{message}；请核对后重新预览或修正确认参数。本次没有发送到激光机。")
    _save_workflow(workflow, workflows_dir=workflows_dir)
    return _build_failure(message, detail=detail, speech=workflow["speech"], error_code=error_code)


def confirm_send_workflow(
    workflow_id,
    confirmed=False,
    workflows_dir=None,
    tuned_job_sender=None,
    ai_runner=None,
    prepared_sender=None,
    execution_sender=None,
    **overrides,
):
    # ai_runner retained for API compatibility; E2 must not call send_generated.
    del ai_runner
    # Gate at this entry itself — do not rely on run_laser_workflow_action pre-normalization.
    confirmed = is_explicitly_confirmed(confirmed)
    workflow, error = _load_workflow(workflow_id, workflows_dir=workflows_dir)
    if error:
        return _build_failure(error)
    if workflow.get("status") != "preview_ready":
        return _build_failure(
            "只有 preview_ready 的 workflow 才能确认发送",
            _workflow_summary(workflow),
            "当前还没有可发送预览，请先生成预览再确认发送。",
        )
    if not confirmed:
        workflow["speech"] = "预览已准备好，但必须明确 confirmed=true 才会交给发送门禁。"
        _save_workflow(workflow, workflows_dir=workflows_dir)
        return _build_failure(
            "confirm_send 需要 confirmed=true",
            {"confirmation_required": True, "workflow": _workflow_summary(workflow)},
            workflow["speech"],
        )

    snapshot = workflow.get("confirmation_snapshot")
    if not isinstance(snapshot, dict) or not snapshot.get("gcode_sha256") or not snapshot.get("gcode_file"):
        return _reject_preview_ready(
            workflow,
            "预览确认快照缺失，请重新生成预览",
            error_code="preview_content_mismatch",
            workflows_dir=workflows_dir,
        )

    # Preview may be ready with artifacts while params are not sendable (sample test).
    send_block = _preview_send_block_reason(workflow)
    if send_block:
        return _reject_preview_ready(
            workflow,
            send_block,
            detail={"can_send": False, "reason": "send_gate"},
            error_code="not_sendable",
            workflows_dir=workflows_dir,
        )

    auth_error, auth_detail, auth_code = _validate_snapshot_authority(
        snapshot,
        workflow_source_type=workflow.get("source_type"),
    )
    if auth_error:
        return _reject_preview_ready(
            workflow,
            auth_error,
            detail=auth_detail,
            error_code=auth_code or "preview_content_mismatch",
            workflows_dir=workflows_dir,
        )

    mismatch, detail, code = _compare_frozen_overrides(snapshot, overrides)
    if mismatch:
        return _reject_preview_ready(workflow, mismatch, detail=detail, error_code=code or "confirmation_mismatch", workflows_dir=workflows_dir)

    filled, fill_error, fill_detail, fill_code = _fill_connection_from_overrides(snapshot, overrides)
    if fill_error:
        return _reject_preview_ready(workflow, fill_error, detail=fill_detail, error_code=fill_code or "confirmation_mismatch", workflows_dir=workflows_dir)

    integrity, integ_error, integ_detail, integ_code = _verify_artifact_matches_snapshot(snapshot)
    if integ_error:
        return _reject_preview_ready(workflow, integ_error, detail=integ_detail, error_code=integ_code or "preview_content_mismatch", workflows_dir=workflows_dir)

    prepared_result = _build_exact_prepared_result(workflow, snapshot, integrity)
    mode = filled["connection_mode"]
    connection = filled["connection"]
    send_result = _invoke_unified_send_file(
        prepared_result,
        mode,
        connection,
        execution_sender=execution_sender,
        prepared_sender=prepared_sender,
        tuned_job_sender=tuned_job_sender,
    )

    job_id = _first_job_id(send_result)
    runtime_status = _canonical_runtime_status(_first_status(send_result) or ("pending" if job_id else "completed"))
    # Persist connection fill into snapshot for audit after successful gate only.
    updated_snapshot = dict(snapshot)
    updated_snapshot["connection_mode"] = mode
    updated_snapshot["connection"] = connection
    workflow["confirmation_snapshot"] = updated_snapshot
    workflow["runtime_job"] = {
        "job_id": job_id,
        "transport": mode,
        "sender_tool": "core.laser_execution",
        "status": runtime_status,
        "last_result": send_result,
        "updated_at": _now(),
    }
    if send_result.get("success"):
        workflow["status"] = "sending" if job_id else "completed"
        workflow["speech"] = _speech_for(workflow)
    else:
        # Execution-layer rejection after confirm gate: mark failed.
        workflow["status"] = "failed"
        workflow["runtime_job"]["last_error"] = send_result.get("result", "发送门禁拒绝或执行失败")
        workflow["speech"] = _speech_for(workflow)
    return _workflow_result(workflow, workflows_dir=workflows_dir, extra={"send_result": send_result, "job_id": job_id})


def _query_runtime_job(runtime_job, status_getter=None):
    job_id = runtime_job.get("job_id", "")
    if not job_id:
        return None
    if status_getter:
        return status_getter(job_id)
    transport = runtime_job.get("transport")
    if not transport:
        return {
            "success": False,
            "result": "runtime_job.transport 缺失，无法查询发送状态；禁止扫描串口/网络目录猜测 connection_mode",
        }
    return laser_execution.job_status(job_id, transport)


def status_workflow(workflow_id, workflows_dir=None, status_getter=None):
    workflow, error = _load_workflow(workflow_id, workflows_dir=workflows_dir)
    if error:
        return _build_failure(error)
    runtime = workflow.get("runtime_job") or {}
    if workflow.get("status") == "sending" and runtime.get("job_id"):
        if not status_getter and not runtime.get("transport"):
            workflow["speech"] = "缺少已保存的 transport，无法查询发送状态。"
            _save_workflow(workflow, workflows_dir=workflows_dir)
            return _build_failure(
                "runtime_job.transport 缺失，无法查询发送状态；禁止扫描串口/网络目录猜测 connection_mode",
                _workflow_summary(workflow),
                workflow["speech"],
            )
        job_result = _query_runtime_job(runtime, status_getter=status_getter)
        runtime["last_result"] = job_result
        runtime["updated_at"] = _now()
        if isinstance(job_result, dict) and job_result.get("success"):
            job_payload = job_result.get("result") or {}
            runtime_status = _canonical_runtime_status(job_payload.get("status"))
            runtime["status"] = runtime_status
            if job_payload.get("finished_at"):
                runtime["finished_at"] = job_payload.get("finished_at")
            if runtime_status in TERMINAL_RUNTIME_STATUSES:
                workflow["status"] = runtime_status
        elif isinstance(job_result, dict):
            runtime["last_error"] = job_result.get("result", "读取底层任务失败")
        workflow["runtime_job"] = runtime
    workflow["speech"] = _speech_for(workflow)
    return _workflow_result(workflow, workflows_dir=workflows_dir)


def cancel_workflow(workflow_id, workflows_dir=None, canceler=None):
    workflow, error = _load_workflow(workflow_id, workflows_dir=workflows_dir)
    if error:
        return _build_failure(error)
    runtime = workflow.get("runtime_job") or {}
    job_id = runtime.get("job_id")
    if not job_id:
        workflow["speech"] = "当前 workflow 没有绑定发送中的 job_id，因此没有可取消的发送任务。"
        _save_workflow(workflow, workflows_dir=workflows_dir)
        return _build_failure("没有可取消的 sender job", _workflow_summary(workflow), workflow["speech"])
    if runtime.get("status") in TERMINAL_RUNTIME_STATUSES or workflow.get("status") in {"completed", "failed", "cancelled"}:
        workflow["speech"] = "任务已结束，不能再次取消；需要急停请走安全动作。"
        _save_workflow(workflow, workflows_dir=workflows_dir)
        return _build_failure("任务已结束，不能取消", _workflow_summary(workflow), workflow["speech"])
    if canceler:
        cancel_result = canceler(job_id, runtime.get("transport", ""))
    else:
        transport = runtime.get("transport")
        if not transport:
            workflow["speech"] = "缺少已保存的 transport，无法取消发送任务。"
            _save_workflow(workflow, workflows_dir=workflows_dir)
            return _build_failure(
                "runtime_job.transport 缺失，无法取消；禁止扫描串口/网络目录猜测 connection_mode",
                _workflow_summary(workflow),
                workflow["speech"],
            )
        cancel_result = laser_execution.cancel_job(job_id, transport)
    runtime["last_result"] = cancel_result
    runtime["updated_at"] = _now()
    if cancel_result.get("success"):
        runtime["status"] = "cancelled"
        workflow["status"] = "cancelled"
    else:
        runtime["last_error"] = cancel_result.get("result", "取消失败")
    workflow["runtime_job"] = runtime
    workflow["speech"] = _speech_for(workflow)
    return _workflow_result(workflow, workflows_dir=workflows_dir, extra={"cancel_result": cancel_result})


def _workflow_sort_timestamp(workflow):
    runtime = workflow.get("runtime_job") or {}
    return runtime.get("finished_at") or workflow.get("updated_at") or workflow.get("created_at") or 0


def _candidate_summary(workflow):
    data = workflow.get("input") or {}
    return {
        "workflow_id": workflow.get("workflow_id"),
        "source_type": workflow.get("source_type"),
        "status": workflow.get("status"),
        "updated_at": workflow.get("updated_at"),
        "timestamp": _workflow_sort_timestamp(workflow),
        "source_summary": data.get("text") or data.get("gcode_file") or data.get("summary_path") or "",
    }


def _resolve_feedback_workflow(workflow_id="", workflows_dir=None):
    if workflow_id:
        workflow, error = _load_workflow(workflow_id, workflows_dir=workflows_dir)
        if error:
            return None, error, []
        return workflow, None, []
    candidates = [
        workflow
        for workflow in _list_workflows(workflows_dir=workflows_dir)
        if workflow.get("status") in FEEDBACK_ELIGIBLE_STATES
    ]
    if not candidates:
        return None, "没有可接收反馈的激光 workflow，请提供 workflow_id 或先生成/执行任务。", []
    candidates.sort(key=_workflow_sort_timestamp, reverse=True)
    newest_time = _workflow_sort_timestamp(candidates[0])
    tied = [workflow for workflow in candidates if _workflow_sort_timestamp(workflow) == newest_time]
    if len(tied) > 1:
        return None, "最新可反馈 workflow 不唯一，请指定 workflow_id。", [_candidate_summary(item) for item in tied]
    return candidates[0], None, []


def feedback_workflow(workflow_id="", feedback_text="", workflows_dir=None, feedback_helper=None):
    feedback_text = str(feedback_text or "").strip()
    if not feedback_text:
        return _build_failure("feedback_text 不能为空")
    workflow, error, candidates = _resolve_feedback_workflow(workflow_id, workflows_dir=workflows_dir)
    if error:
        detail = {"candidates": candidates} if candidates else None
        return _build_failure(error, detail=detail, speech="我不确定你要反馈哪一个任务，请指定 workflow_id。")
    event = {"feedback_text": feedback_text, "created_at": _now()}
    if workflow.get("source_type") == "text" and workflow.get("artifacts", {}).get("task_id"):
        helper = feedback_helper or text_laser_task_tool.refine_laser_params_from_feedback
        helper_result = helper(feedback_text=feedback_text, task_id=workflow["artifacts"]["task_id"])
        event["routed_to"] = "text_laser_task"
        event["helper_result"] = helper_result
        if helper_result.get("success"):
            event["matched_issue"] = (helper_result.get("result") or {}).get("matched_issue")
            event["recommended_strategy"] = (helper_result.get("result") or {}).get("recommended_strategy")
    else:
        event["routed_to"] = "workflow_only"
        event["note"] = "非文字 workflow 已记录反馈，但不能自动调用文字任务重生成。"
    workflow.setdefault("feedback_events", []).append(event)
    if workflow.get("status") != "sending":
        workflow["status"] = "feedback_recorded"
    if event.get("recommended_strategy"):
        workflow["speech"] = f"我已记录反馈，并建议用 {event['recommended_strategy']} 重新生成预览。"
    else:
        workflow["speech"] = "我已记录反馈；文字任务可继续重生成，图片或预制文件请先选择新的处理方式。"
    return _workflow_result(workflow, workflows_dir=workflows_dir, extra={"feedback_event": event})


def regenerate_workflow(
    workflow_id="",
    strategy="recommended",
    feedback_text="",
    workflows_dir=None,
    regenerator=None,
    feedback_helper=None,
    **overrides,
):
    workflow, error, candidates = _resolve_feedback_workflow(workflow_id, workflows_dir=workflows_dir)
    if error:
        detail = {"candidates": candidates} if candidates else None
        return _build_failure(error, detail=detail, speech="我不确定你要重生成哪一个任务，请指定 workflow_id。")
    if workflow.get("source_type") != "text" or not workflow.get("artifacts", {}).get("task_id"):
        workflow["speech"] = "这个 workflow 不是文字任务，不能自动走文字反馈重生成；请先提供新的图片、summary 或 G-code。"
        _save_workflow(workflow, workflows_dir=workflows_dir)
        return _build_failure("当前 workflow 不支持自动文字重生成", _workflow_summary(workflow), workflow["speech"])
    feedback_text = str(feedback_text or "").strip()
    feedback_result = None
    if feedback_text:
        feedback_result = feedback_workflow(
            workflow_id=workflow.get("workflow_id", workflow_id),
            feedback_text=feedback_text,
            workflows_dir=workflows_dir,
            feedback_helper=feedback_helper,
        )
        if not feedback_result.get("success"):
            return feedback_result
        feedback_payload = feedback_result.get("result") or {}
        feedback_event = feedback_payload.get("feedback_event") or {}
        if str(strategy or "recommended").strip() == "recommended" and not feedback_event.get("recommended_strategy"):
            return feedback_result
        workflow, error = _load_workflow(workflow.get("workflow_id", workflow_id), workflows_dir=workflows_dir)
        if error:
            return _build_failure(error)
    helper = regenerator or text_laser_task_tool.regenerate_text_laser_task
    data = laser_material_safety.strip_unconfirmed_manual_params(
        {**workflow.get("input", {}), **_clean_input_fields(overrides)}
    )
    workflow["input"] = dict(data)
    safety_error = _reject_unsafe_material(workflow, workflows_dir=workflows_dir)
    if safety_error:
        return safety_error
    result = helper(
        task_id=workflow["artifacts"]["task_id"],
        strategy=strategy,
        feedback_text=feedback_text,
        engraving_mode=data.get("engraving_mode"),
        width_mm=data.get("width_mm"),
        height_mm=data.get("height_mm"),
        image_width=data.get("image_width"),
        image_height=data.get("image_height"),
        font_size=data.get("font_size"),
        font_path=data.get("font_path"),
        auto_wrap=data.get("auto_wrap"),
        max_lines=data.get("max_lines"),
        layout_mode=data.get("layout_mode"),
        laser_min_power=data.get("laser_min_power"),
        laser_max_power=data.get("laser_max_power"),
        power_percent=data.get("power_percent"),
        feed_rate=data.get("feed_rate"),
        travel_rate=data.get("travel_rate"),
        pixel_size_mm=data.get("pixel_size_mm"),
        threshold=data.get("threshold"),
        passes=data.get("passes"),
        invert=data.get("invert"),
        bidirectional=data.get("bidirectional"),
        overscan_mm=data.get("overscan_mm"),
        raster_scan_direction=data.get("raster_scan_direction"),
        auto_trim=data.get("auto_trim"),
        trim_tolerance=data.get("trim_tolerance"),
        auto_size=data.get("auto_size"),
        dpi=data.get("dpi"),
        lock_aspect_ratio=data.get("lock_aspect_ratio"),
        offset_x_mm=data.get("offset_x_mm"),
        offset_y_mm=data.get("offset_y_mm"),
        safe_margin_mm=data.get("safe_margin_mm"),
        send_after_generate=False,
        confirmed=False,
        dry_run=True,
        connection_mode=data.get("connection_mode", ""),
        network_host=data.get("network_host", ""),
        network_transport=data.get("network_transport", laser_material_calibration_tool.DEFAULT_NETWORK_TRANSPORT),
        network_http_port=int(data.get("network_http_port") or laser_network_grbl_tool.DEFAULT_HTTP_PORT),
        network_telnet_port=int(data.get("network_telnet_port") or laser_network_grbl_tool.DEFAULT_TELNET_PORT),
        network_timeout=float(data.get("network_timeout") or laser_network_grbl_tool.DEFAULT_TIMEOUT),
    )
    if not result.get("success"):
        return _set_failed(workflow, result.get("result", "文字任务重生成失败"), result, workflows_dir)
    payload = result.get("result") or {}
    # New artifact invalidates any previous confirm binding until rebind succeeds.
    _discard_confirmation_snapshot(workflow)
    workflow["status"] = "preview_ready"
    workflow["artifacts"].update(
        {
            "task_id": payload.get("task_id"),
            "attempt_no": payload.get("attempt_no"),
            "image_file": payload.get("image_file"),
            "gcode_file": payload.get("gcode_file"),
            "task_file": payload.get("task_file"),
            "time_estimate": payload.get("time_estimate"),
            "auto_trim": payload.get("auto_trim"),
            "placement": payload.get("placement"),
            "text_layout": payload.get("text_layout"),
        }
    )
    workflow["last_preview_result"] = payload
    bind_error = _bind_preview_artifact_integrity(workflow)
    if bind_error:
        # Fail closed: do not leave a sendable preview_ready without a new snapshot.
        workflow["status"] = "failed"
        workflow["speech"] = _safe_speech(f"{bind_error}；重生成未绑定新确认快照，本次没有发送到激光机。")
        _save_workflow(workflow, workflows_dir=workflows_dir)
        return _build_failure(bind_error, detail=payload, speech=workflow["speech"], error_code="preview_content_mismatch")
    workflow["speech"] = _append_physical_material_check(_speech_for(workflow), workflow)
    extra = dict(payload)
    if feedback_result:
        extra["feedback_result"] = feedback_result
    return _workflow_result(workflow, workflows_dir=workflows_dir, extra=extra)


def recent_workflows(limit=5, workflows_dir=None):
    workflows = _list_workflows(workflows_dir=workflows_dir)
    workflows.sort(key=lambda workflow: workflow.get("updated_at") or 0, reverse=True)
    return _build_success(
        {
            "workflows": [_workflow_summary(workflow) for workflow in workflows[: int(limit or 5)]],
            "speech": "已列出最近的激光 workflow；可以用 workflow_id 继续查询、反馈或重生成。",
        }
    )


def _input_fields_from_kwargs(kwargs, *, strip_unconfirmed_manual=True):
    keys = {
        "source_type",
        "text",
        "image_file",
        "image_url",
        "summary_path",
        "gcode_file",
        "prompt",
        "candidate_selection",
        "candidate_id",
        "candidate_index",
        "material",
        "material_library",
        "thickness_mm",
        "laser_mode",
        "engraving_mode",
        "mode",
        "task_type",
        "output_dir",
        "output_format",
        "size_mm",
        "width_mm",
        "height_mm",
        "image_width",
        "image_height",
        "font_size",
        "font_path",
        "auto_wrap",
        "max_lines",
        "layout_mode",
        "laser_min_power",
        "laser_max_power",
        "power_percent",
        "feed_rate",
        "travel_rate",
        "pixel_size_mm",
        "threshold",
        "passes",
        "invert",
        "bidirectional",
        "overscan_mm",
        "raster_scan_direction",
        "dither_algorithm",
        "raster_output_strategy",
        "raster_quality_strategy",
        "auto_trim",
        "trim_tolerance",
        "auto_size",
        "dpi",
        "lock_aspect_ratio",
        "vector_simplify_factor",
        "offset_x_mm",
        "offset_y_mm",
        "safe_margin_mm",
        "connection_mode",
        "port",
        "baudrate",
        "wait_for_response",
        "run_in_background",
        "network_host",
        "network_transport",
        "network_http_port",
        "network_telnet_port",
        "network_timeout",
        "prepared_gcode_dir",
        "reuse_prepared_gcode",
        "manual_params_confirmed",
        "frequency",
        "freq_hz",
        "pwm_frequency",
        "laser_frequency",
    }
    raw = {key: kwargs.get(key) for key in keys if key in kwargs}
    if strip_unconfirmed_manual:
        # Keep explicit false so update can clear a previously confirmed sticky state.
        return _clean_input_fields(raw, keep_explicit_false_manual_flag=True)
    # Confirm-path overrides must keep power/feed/passes so frozen mismatch checks still fire.
    clean = {}
    for key, value in raw.items():
        if key.startswith("_"):
            continue
        if value is None:
            continue
        if isinstance(value, str) and value == "":
            continue
        clean[key] = value
    return clean


def _pin_trusted_image_match_policies(input_fields):
    """Server-side only: nearest engrave + direct library record may enter confirm."""
    fields = dict(input_fields or {})
    fields["material_match_policy"] = "nearest_engrave"
    fields["send_policy"] = "confirmed_material_record"
    return fields


def _trusted_image_preview_from_kwargs(**kwargs):
    """Shared trusted image-preview path used by draw-lab and HTTP /api/workflow.

    External JSON policy fields are discarded; only this server helper pins strategy.
    """
    payload = dict(kwargs or {})
    payload.pop("material_match_policy", None)
    payload.pop("send_policy", None)
    payload.pop("action", None)
    workflow_id = payload.get("workflow_id", "")
    workflows_dir = payload.get("workflows_dir")
    input_fields = _pin_trusted_image_match_policies(_input_fields_from_kwargs(payload))
    source_type = payload.get("source_type", "") or input_fields.get("source_type", "") or "image"
    return preview_workflow(
        workflow_id=workflow_id,
        source_type=source_type,
        input_fields=input_fields,
        workflows_dir=workflows_dir,
        text_generator=payload.get("text_generator"),
        ai_runner=payload.get("ai_runner"),
    )


def preview_draw_lab_image(**kwargs):
    """Trusted Excalidraw/Web draw-lab preview entry.

    Client JSON cannot inject material_match_policy/send_policy through
    run_laser_workflow_action. Only this helper pins the draw-lab strategies.
    """
    return _trusted_image_preview_from_kwargs(**kwargs)


def preview_http_image_workflow(**kwargs):
    """Trusted main Web / Android HTTP /api/workflow image preview entry.

    Pins nearest_engrave + confirmed_material_record server-side for image
    previews only. Ordinary run_laser_workflow_action (MCP / speech / direct
    tool use) keeps stripping client policy and uses strict core defaults.
    Does not alter text or prepared_gcode paths.
    """
    return _trusted_image_preview_from_kwargs(**kwargs)


def run_laser_workflow_action(action="status", workflow_id="", workflows_dir=None, **kwargs):
    normalized_action = str(action or "status").strip().lower().replace("-", "_")
    # Drop policy fields from ordinary API/MCP kwargs even if a client sends them.
    safe_kwargs = dict(kwargs or {})
    safe_kwargs.pop("material_match_policy", None)
    safe_kwargs.pop("send_policy", None)
    # confirm_send compares frozen overrides against the snapshot; do not strip manual fields there.
    strip_manual = normalized_action not in {"confirm_send", "send"}
    input_fields = _input_fields_from_kwargs(safe_kwargs, strip_unconfirmed_manual=strip_manual)
    source_type = safe_kwargs.get("source_type", "") or input_fields.get("source_type", "")
    if normalized_action in {"create", "update"}:
        return create_or_update_workflow(
            workflow_id=workflow_id,
            source_type=source_type,
            input_fields=input_fields,
            workflows_dir=workflows_dir,
        )
    if normalized_action in {"preview", "plan"}:
        return preview_workflow(
            workflow_id=workflow_id,
            source_type=source_type,
            input_fields=input_fields,
            workflows_dir=workflows_dir,
            text_generator=safe_kwargs.get("text_generator"),
            ai_runner=safe_kwargs.get("ai_runner"),
        )
    if normalized_action in {"confirm_send", "send"}:
        return confirm_send_workflow(
            workflow_id=workflow_id,
            confirmed=_is_explicitly_confirmed(safe_kwargs.get("confirmed", False)),
            workflows_dir=workflows_dir,
            tuned_job_sender=safe_kwargs.get("tuned_job_sender"),
            ai_runner=safe_kwargs.get("ai_runner"),
            prepared_sender=safe_kwargs.get("prepared_sender"),
            execution_sender=safe_kwargs.get("execution_sender"),
            **input_fields,
        )
    if normalized_action == "status":
        if not workflow_id:
            return recent_workflows(limit=1, workflows_dir=workflows_dir)
        return status_workflow(
            workflow_id,
            workflows_dir=workflows_dir,
            status_getter=safe_kwargs.get("status_getter"),
        )
    if normalized_action == "cancel":
        return cancel_workflow(
            workflow_id,
            workflows_dir=workflows_dir,
            canceler=safe_kwargs.get("canceler"),
        )
    if normalized_action in {"feedback", "record_feedback"}:
        return feedback_workflow(
            workflow_id=workflow_id,
            feedback_text=safe_kwargs.get("feedback_text", ""),
            workflows_dir=workflows_dir,
            feedback_helper=safe_kwargs.get("feedback_helper"),
        )
    if normalized_action == "regenerate":
        return regenerate_workflow(
            workflow_id=workflow_id,
            strategy=safe_kwargs.get("strategy", "recommended"),
            feedback_text=safe_kwargs.get("feedback_text", ""),
            workflows_dir=workflows_dir,
            regenerator=safe_kwargs.get("regenerator"),
            feedback_helper=safe_kwargs.get("feedback_helper"),
            **input_fields,
        )
    if normalized_action == "recent":
        return recent_workflows(limit=safe_kwargs.get("limit", 5), workflows_dir=workflows_dir)
    return _build_failure("未知 workflow action，支持 create/update/preview/confirm_send/status/cancel/feedback/regenerate/recent")


def register_tool(mcp):
    @mcp.tool()
    def laser_workflow_tool(
        action: str = "status",
        workflow_id: str = "",
        source_type: str = "",
        text: str = "",
        image_file: str = "",
        image_url: str = "",
        summary_path: str = "",
        gcode_file: str = "",
        prompt: str = "",
        material: str = "",
        # Omission defaults: None means "not provided" so confirm cannot inherit wrapper defaults.
        thickness_mm: float = None,
        laser_mode: str = None,
        engraving_mode: str = None,
        mode: str = None,
        feedback_text: str = "",
        strategy: str = "recommended",
        size_mm: float = None,
        width_mm: float = None,
        height_mm: float = None,
        image_width: int = None,
        image_height: int = None,
        font_size: int = None,
        font_path: str = "",
        auto_wrap: bool = None,
        max_lines: int = None,
        layout_mode: str = None,
        laser_min_power: int = None,
        laser_max_power: int = None,
        power_percent: float = None,
        feed_rate: int = None,
        travel_rate: int = None,
        pixel_size_mm: float = None,
        threshold: int = None,
        passes: int = None,
        invert: bool = None,
        bidirectional: bool = None,
        overscan_mm: float = None,
        raster_scan_direction: str = None,
        raster_quality_strategy: str = None,
        auto_trim: bool = None,
        trim_tolerance: float = None,
        auto_size: bool = None,
        dpi: float = None,
        lock_aspect_ratio: bool = None,
        offset_x_mm: float = None,
        offset_y_mm: float = None,
        safe_margin_mm: float = None,
        connection_mode: str = "",
        port: str = "",
        baudrate: int = None,
        wait_for_response: bool = None,
        run_in_background: bool = None,
        network_host: str = "",
        network_transport: str = None,
        network_http_port: int = None,
        network_telnet_port: int = None,
        network_timeout: float = None,
        confirmed: bool = False,
        prepared_gcode_dir: str = "",
        reuse_prepared_gcode: bool = None,
        manual_params_confirmed: bool = None,
    ) -> dict:
        """
        统一激光 workflow 状态入口：预览、确认发送、状态、取消、反馈和重生成。
        preview/feedback/regenerate 不连接真实设备；confirm_send 只委托现有 sender 门禁。
        仅当 manual_params_confirmed=true 时，页面/请求中的功率、速度、点距、次数等才会覆盖材料库。
        """
        raw = dict(locals())
        # Drop self/mcp decorator locals and None-default processing/connection fields.
        payload = {}
        for key, value in raw.items():
            if key in {"mcp"}:
                continue
            if key in MCP_OMIT_IF_NONE_KEYS and value is None:
                continue
            if key == "manual_params_confirmed" and value is None:
                continue
            payload[key] = value
        return run_laser_workflow_action(**payload)
