import json
import os
import time
import uuid

from core.laser_runtime.config import get_laser_settings
from core.laser_runtime.models import file_sha256
from core import laser_execution
from tools import laser_grbl_tool, laser_network_grbl_tool


_SETTINGS = get_laser_settings()
MATERIAL_PARAMS_FILE = str(_SETTINGS.material_params_file)
CALIBRATION_DIR = str(_SETTINGS.calibration_dir)
DEFAULT_S_MAX = laser_grbl_tool.DEFAULT_LASER_S_MAX
DEFAULT_MAX_PASSES = _SETTINGS.max_passes
DEFAULT_TRAVEL_RATE = _SETTINGS.travel_rate
DEFAULT_NETWORK_TRANSPORT = laser_network_grbl_tool.FORCED_FILE_TRANSPORT
PARAMETER_KEYS = {
    "laser_min_power",
    "laser_max_power",
    "feed_rate",
    "travel_rate",
    "pixel_size_mm",
    "threshold",
    "passes",
}


DEFAULT_MATERIAL_PARAMS = {
    "version": 1,
    "materials": {
        "椴木": {
            "aliases": [
                "basswood",
                "wood",
                "plywood",
                "木头",
                "木板",
                "木料",
                "木牌",
                "木片",
                "木质",
                "椴木板",
            ],
            "thicknesses": {
                "3": {
                    "engrave": {
                        "laser_min_power": 0,
                        "laser_max_power": 420,
                        "feed_rate": 1800,
                        "travel_rate": 3000,
                        "pixel_size_mm": 0.1,
                        "threshold": -1,
                        "passes": 1,
                    },
                    "cut": {
                        "laser_min_power": 0,
                        "laser_max_power": 900,
                        "feed_rate": 360,
                        "travel_rate": 3000,
                        "pixel_size_mm": 0.1,
                        "threshold": 128,
                        "passes": 1,
                    },
                }
            },
        },
        "亚克力": {
            "aliases": ["acrylic", "plexiglass", "有机玻璃"],
            "thicknesses": {
                "3": {
                    "engrave": {
                        "laser_min_power": 0,
                        "laser_max_power": 340,
                        "feed_rate": 1600,
                        "travel_rate": 3000,
                        "pixel_size_mm": 0.1,
                        "threshold": -1,
                        "passes": 1,
                    },
                    "cut": {
                        "laser_min_power": 0,
                        "laser_max_power": 850,
                        "feed_rate": 260,
                        "travel_rate": 3000,
                        "pixel_size_mm": 0.1,
                        "threshold": 128,
                        "passes": 1,
                    },
                }
            },
        },
        "皮革": {
            "aliases": ["leather", "皮料"],
            "thicknesses": {
                "1.5": {
                    "engrave": {
                        "laser_min_power": 0,
                        "laser_max_power": 280,
                        "feed_rate": 1900,
                        "travel_rate": 3000,
                        "pixel_size_mm": 0.1,
                        "threshold": -1,
                        "passes": 1,
                    },
                    "cut": {
                        "laser_min_power": 0,
                        "laser_max_power": 650,
                        "feed_rate": 520,
                        "travel_rate": 3000,
                        "pixel_size_mm": 0.1,
                        "threshold": 128,
                        "passes": 1,
                    },
                }
            },
        },
        "纸板": {
            "aliases": ["cardboard", "paperboard", "瓦楞纸", "卡纸"],
            "thicknesses": {
                "1": {
                    "engrave": {
                        "laser_min_power": 0,
                        "laser_max_power": 220,
                        "feed_rate": 2500,
                        "travel_rate": 3000,
                        "pixel_size_mm": 0.1,
                        "threshold": -1,
                        "passes": 1,
                    },
                    "cut": {
                        "laser_min_power": 0,
                        "laser_max_power": 520,
                        "feed_rate": 760,
                        "travel_rate": 3000,
                        "pixel_size_mm": 0.1,
                        "threshold": 128,
                        "passes": 1,
                    },
                }
            },
        },
    },
}


def _deepcopy_json(data):
    return json.loads(json.dumps(data, ensure_ascii=False))


def _split_flat_engrave_params(params, include_outline=False):
    if not _is_flat_params_entry(params):
        return params

    split = {"raster": _deepcopy_json(params)}
    if include_outline:
        split["outline"] = _deepcopy_json(params)
    return split


def _build_success(result, detail=None):
    payload = {"success": True, "result": result}
    if detail is not None:
        payload["detail"] = detail
    return payload


def _build_failure(message, detail=None, error_code=None):
    payload = {"success": False, "result": message}
    if detail is not None:
        payload["detail"] = detail
    if error_code is not None:
        payload["error_code"] = error_code
    return payload


def _resolve_network_host_for_session(network_host):
    resolved_host, error = laser_execution.resolve_laser_network_host(network_host)
    if error:
        return ""
    return resolved_host


def _prefer_explicit(value, saved_value):
    return value if str(value or "").strip() else saved_value


def _send_prepared_job(
    prepared_result,
    confirmed=False,
    connection_mode="serial",
    port="",
    baudrate=laser_grbl_tool.DEFAULT_BAUDRATE,
    wait_for_response=True,
    run_in_background=True,
    network_host="",
    network_transport=DEFAULT_NETWORK_TRANSPORT,
    network_http_port=laser_network_grbl_tool.DEFAULT_HTTP_PORT,
    network_telnet_port=laser_network_grbl_tool.DEFAULT_TELNET_PORT,
    network_timeout=laser_network_grbl_tool.DEFAULT_TIMEOUT,
):
    mode, error = laser_execution.resolve_laser_connection_mode(connection_mode)
    if error:
        return _build_failure(error)

    if not confirmed:
        return _build_success(
            {
                "prepared": prepared_result,
                "connection_mode": mode,
                "confirmation_required": True,
                "result": "雕刻文件已准备，但未发送。确认安全后再用 confirmed=true 启动。",
            }
        )

    if mode == "network":
        resolved_host, error = laser_execution.resolve_laser_network_host(network_host)
        if error:
            return _build_failure(error, prepared_result)
        return laser_execution.send_file(
            prepared_result,
            mode,
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
        mode,
        confirmed=True,
        run_in_background=run_in_background,
        port=port,
        baudrate=baudrate,
        wait_for_response=wait_for_response,
    )


def _send_saved_calibration_session(
    calibration_id,
    connection_mode="",
    port="",
    baudrate=laser_grbl_tool.DEFAULT_BAUDRATE,
    wait_for_response=True,
    run_in_background=True,
    network_host="",
    network_transport="",
    network_http_port=laser_network_grbl_tool.DEFAULT_HTTP_PORT,
    network_telnet_port=laser_network_grbl_tool.DEFAULT_TELNET_PORT,
    network_timeout=laser_network_grbl_tool.DEFAULT_TIMEOUT,
):
    session, error = _read_calibration_session(calibration_id)
    if error:
        return _build_failure(error)

    gcode_file = session.get("gcode_file", "")
    if not gcode_file or not os.path.isfile(gcode_file):
        return _build_failure("调参测试矩阵文件不存在，无法继续发送", session)
    expected_gcode_sha256 = str(session.get("gcode_sha256") or "").strip().lower()
    if not expected_gcode_sha256:
        return _build_failure(
            "调参测试矩阵缺少预览摘要，请重新生成测试矩阵",
            error_code="preview_content_mismatch",
        )
    try:
        actual_gcode_sha256 = file_sha256(gcode_file)
    except OSError:
        return _build_failure(
            "无法读取调参测试矩阵以校验摘要",
            error_code="preview_content_mismatch",
        )
    if actual_gcode_sha256 != expected_gcode_sha256:
        return _build_failure(
            "preview_content_mismatch: 调参测试矩阵内容与预览摘要不一致",
            error_code="preview_content_mismatch",
        )

    saved_connection = session.get("connection", {})
    saved_mode = saved_connection.get("connection_mode") or session.get("connection_mode", "")
    saved_host = saved_connection.get("network_host") or session.get("network_host", "")
    saved_transport = saved_connection.get("network_transport") or session.get("network_transport", DEFAULT_NETWORK_TRANSPORT)
    saved_http_port = saved_connection.get("network_http_port", network_http_port)
    saved_telnet_port = saved_connection.get("network_telnet_port", network_telnet_port)
    saved_timeout = saved_connection.get("network_timeout", network_timeout)

    resolved_mode, error = laser_execution.resolve_laser_connection_mode(_prefer_explicit(connection_mode, saved_mode))
    if error:
        return _build_failure(error)

    resolved_host = _prefer_explicit(network_host, saved_host)
    if resolved_mode == "network" and not resolved_host:
        resolved_host = _resolve_network_host_for_session("")
    resolved_transport = DEFAULT_NETWORK_TRANSPORT

    prepared_result = {
        "source_file": gcode_file,
        "gcode_file": gcode_file,
        "converted": False,
        "calibration_id": calibration_id,
        "expected_gcode_sha256": expected_gcode_sha256,
    }
    send_result = _send_prepared_job(
        prepared_result,
        confirmed=True,
        connection_mode=resolved_mode,
        port=port,
        baudrate=baudrate,
        wait_for_response=wait_for_response,
        run_in_background=run_in_background,
        network_host=resolved_host,
        network_transport=resolved_transport,
        network_http_port=saved_http_port if network_http_port == laser_network_grbl_tool.DEFAULT_HTTP_PORT else network_http_port,
        network_telnet_port=saved_telnet_port if network_telnet_port == laser_network_grbl_tool.DEFAULT_TELNET_PORT else network_telnet_port,
        network_timeout=saved_timeout if network_timeout == laser_network_grbl_tool.DEFAULT_TIMEOUT else network_timeout,
    )

    session["status"] = "running" if send_result.get("success") else "failed"
    session["send_result"] = send_result
    session["connection"] = {
        "connection_mode": resolved_mode,
        "network_host": resolved_host,
        "network_transport": resolved_transport,
        "network_http_port": saved_http_port if network_http_port == laser_network_grbl_tool.DEFAULT_HTTP_PORT else network_http_port,
        "network_telnet_port": saved_telnet_port if network_telnet_port == laser_network_grbl_tool.DEFAULT_TELNET_PORT else network_telnet_port,
        "network_timeout": saved_timeout if network_timeout == laser_network_grbl_tool.DEFAULT_TIMEOUT else network_timeout,
    }
    if send_result.get("job_id"):
        session["job_id"] = send_result["job_id"]
    try:
        _write_calibration_session(session)
    except OSError:
        pass

    response = {
        **session,
        "connection_mode": resolved_mode,
        "network_host": resolved_host,
        "network_transport": resolved_transport,
        "confirmation_required": False,
        "send_result": send_result,
    }
    return _build_success(response)


def _ensure_parent_dir(path):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def _write_json_file(path, data):
    _ensure_parent_dir(path)
    tmp_file = path + ".tmp"
    with open(tmp_file, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
    os.replace(tmp_file, path)


def _read_json_file(path):
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def _merge_material_params(defaults, custom):
    merged = _deepcopy_json(defaults)
    _normalize_material_param_schema(merged, include_outline_for_flat_defaults=True)
    if not isinstance(custom, dict):
        return merged

    custom_materials = custom.get("materials", {})
    if not isinstance(custom_materials, dict):
        return merged

    materials = merged.setdefault("materials", {})
    for material, entry in custom_materials.items():
        if not isinstance(entry, dict):
            continue
        base_entry = materials.setdefault(material, {"aliases": [], "thicknesses": {}})
        aliases = entry.get("aliases")
        if isinstance(aliases, list):
            existing = base_entry.setdefault("aliases", [])
            for alias in aliases:
                if alias not in existing:
                    existing.append(alias)
        thicknesses = entry.get("thicknesses")
        if isinstance(thicknesses, dict):
            base_thicknesses = base_entry.setdefault("thicknesses", {})
            for thickness_key, modes in thicknesses.items():
                if isinstance(modes, dict):
                    target_modes = base_thicknesses.setdefault(str(thickness_key), {})
                    for mode_key, params in modes.items():
                        if mode_key == "engrave" and _is_flat_params_entry(params):
                            existing = target_modes.get("engrave")
                            if not isinstance(existing, dict) or _is_flat_params_entry(existing):
                                existing = _split_flat_engrave_params(existing or {}, include_outline=False)
                            existing["raster"] = params
                            target_modes["engrave"] = existing
                        else:
                            target_modes[mode_key] = params
    return merged


def _normalize_material_param_schema(data, include_outline_for_flat_defaults=False):
    materials = data.get("materials", {}) if isinstance(data, dict) else {}
    for entry in materials.values():
        if not isinstance(entry, dict):
            continue
        thicknesses = entry.get("thicknesses", {})
        if not isinstance(thicknesses, dict):
            continue
        for modes in thicknesses.values():
            if not isinstance(modes, dict):
                continue
            if _is_flat_params_entry(modes.get("engrave")):
                modes["engrave"] = _split_flat_engrave_params(
                    modes["engrave"], include_outline=include_outline_for_flat_defaults
                )
    return data


def _load_material_params(path=MATERIAL_PARAMS_FILE):
    defaults = _deepcopy_json(DEFAULT_MATERIAL_PARAMS)
    if not os.path.isfile(path):
        _normalize_material_param_schema(defaults, include_outline_for_flat_defaults=True)
        return defaults, None

    try:
        custom = _read_json_file(path)
    except (OSError, ValueError) as exc:
        return None, f"读取材料参数失败: {exc}"

    return _merge_material_params(defaults, custom), None


def _save_material_params(data, path=MATERIAL_PARAMS_FILE):
    try:
        _write_json_file(path, data)
        return None
    except OSError as exc:
        return f"保存材料参数失败: {exc}"


def _format_thickness_key(thickness_mm):
    number, error = laser_grbl_tool._validate_number(thickness_mm, "thickness_mm", 0)
    if error:
        return None, error
    return laser_grbl_tool._format_mm(number), None


def _parse_float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _supported_materials(data):
    return sorted(data.get("materials", {}).keys())


def _find_material(data, material, allow_create=False):
    material = str(material or "").strip()
    if not material:
        return None, None, "请指定材料，例如 椴木 / 亚克力 / 皮革 / 纸板"

    target = material.lower()
    materials = data.get("materials", {})
    for canonical, entry in materials.items():
        if target == canonical.lower():
            return canonical, entry, None
        aliases = entry.get("aliases", []) if isinstance(entry, dict) else []
        for alias in aliases:
            if target == str(alias).lower():
                return canonical, entry, None

    if allow_create:
        entry = {"aliases": [], "thicknesses": {}}
        materials[material] = entry
        return material, entry, None

    supported = ", ".join(_supported_materials(data))
    return None, None, f"未找到材料: {material}。已支持: {supported}"


def _resolve_mode(laser_mode):
    return laser_grbl_tool._resolve_laser_mode(laser_mode)


def _resolve_material_engraving_mode(laser_mode, engraving_mode=""):
    mode, error = _resolve_mode(laser_mode)
    if error:
        return None, None, error
    if mode == "cut":
        return mode, "", None

    resolved_engraving_mode, error = laser_grbl_tool._resolve_engraving_mode(engraving_mode)
    if error:
        return None, None, error
    return mode, resolved_engraving_mode, None


def _is_flat_params_entry(value):
    return isinstance(value, dict) and any(key in value for key in PARAMETER_KEYS)


def _params_for_mode(modes, mode, engraving_mode=""):
    if not isinstance(modes, dict) or mode not in modes:
        return None

    entry = modes[mode]
    if mode != "engrave":
        return entry

    if isinstance(entry, dict) and engraving_mode in entry and isinstance(entry[engraving_mode], dict):
        return entry[engraving_mode]

    if engraving_mode == "raster" and _is_flat_params_entry(entry):
        return entry

    return None


def _normalize_params(params, require_all=True):
    normalized = {}
    errors = []

    defaults = {
        "laser_min_power": 0,
        "laser_max_power": 800,
        "feed_rate": 1200,
        "travel_rate": DEFAULT_TRAVEL_RATE,
        "pixel_size_mm": 0.1,
        "threshold": -1,
        "passes": 1,
    }
    values = {**defaults, **(params or {})} if require_all else dict(params or {})

    int_specs = {
        "laser_min_power": (0, DEFAULT_S_MAX),
        "laser_max_power": (0, DEFAULT_S_MAX),
        "feed_rate": (1, None),
        "travel_rate": (1, None),
        "threshold": (-1, 255),
        "passes": (1, DEFAULT_MAX_PASSES),
    }
    for key, (minimum, maximum) in int_specs.items():
        if key not in values:
            continue
        number, error = laser_grbl_tool._validate_int(values[key], key, minimum, maximum)
        if error:
            errors.append(error)
        else:
            normalized[key] = number

    if "pixel_size_mm" in values:
        number, error = laser_grbl_tool._validate_number(values["pixel_size_mm"], "pixel_size_mm", 0)
        if error:
            errors.append(error)
        else:
            normalized["pixel_size_mm"] = number

    if (
        "laser_min_power" in normalized
        and "laser_max_power" in normalized
        and normalized["laser_max_power"] < normalized["laser_min_power"]
    ):
        errors.append("laser_max_power 不能小于 laser_min_power")

    if errors:
        return None, "；".join(errors)
    return normalized, None


def _find_params(data, material, thickness_mm, laser_mode, engraving_mode=""):
    mode, resolved_engraving_mode, error = _resolve_material_engraving_mode(laser_mode, engraving_mode)
    if error:
        return None, error

    canonical, entry, error = _find_material(data, material)
    if error:
        return None, error


    requested_key, error = _format_thickness_key(thickness_mm)
    if error:
        return None, error


    thicknesses = entry.get("thicknesses", {})
    exact_modes = thicknesses.get(requested_key, {})
    exact_params = _params_for_mode(exact_modes, mode, resolved_engraving_mode)
    if exact_params is not None:
        params, error = _normalize_params(exact_params)
        if error:
            return None, error
        result = {
            "material": canonical,
            "requested_material": material,
            "thickness_mm": _parse_float(requested_key),
            "matched_thickness_mm": _parse_float(requested_key),
            "match": "exact",
            "laser_mode": mode,
            "params": params,
            "warnings": [],
            "can_send": True,
            "send_blocked_reason": "",
        }
        if mode == "engrave":
            result["engraving_mode"] = resolved_engraving_mode
        return result, None

    if mode == "cut":
        return None, (
            f"材料 {canonical} 暂无 {requested_key}mm 精确切割参数；"
            "切割禁止使用其他厚度参数，请先保存或校准该厚度的切割参数"
        )

    candidates = []
    for thickness_key, modes in thicknesses.items():
        params_entry = _params_for_mode(modes, mode, resolved_engraving_mode)
        if params_entry is None:
            continue
        thickness = _parse_float(thickness_key)
        if thickness is None:
            continue
        candidates.append((abs(thickness - _parse_float(requested_key)), thickness_key, params_entry))

    if not candidates:
        label = f"{mode}/{resolved_engraving_mode}" if mode == "engrave" else mode
        return None, f"材料 {canonical} 暂无 {label} 参数，请先用 material_params 保存参数或跑测试矩阵"

    _, matched_key, matched_params = sorted(candidates, key=lambda item: item[0])[0]
    params, error = _normalize_params(matched_params)
    if error:
        return None, error

    result = {
        "material": canonical,
        "requested_material": material,
        "thickness_mm": _parse_float(requested_key),
        "matched_thickness_mm": _parse_float(matched_key),
        "match": "nearest_thickness",
        "laser_mode": mode,
        "params": params,
        "warnings": [
            f"没有 {requested_key}mm 的精确参数，已使用最接近的 {matched_key}mm 参数"
        ],
        "can_send": True,
        "send_blocked_reason": "",
    }
    if mode == "engrave":
        result["engraving_mode"] = resolved_engraving_mode
    return result, None


def recommend_laser_params(material, thickness_mm, laser_mode="engrave", engraving_mode="", params_file=MATERIAL_PARAMS_FILE):
    data, error = _load_material_params(params_file)
    if error:
        return _build_failure(error)

    recommendation, error = _find_params(data, material, thickness_mm, laser_mode, engraving_mode)
    if error:
        return _build_failure(error)
    return _build_success(recommendation)


def _parse_params_json(params_json):
    if not params_json:
        return {}, None
    try:
        parsed = json.loads(params_json)
    except ValueError as exc:
        return None, f"params_json 不是合法 JSON: {exc}"
    if not isinstance(parsed, dict):
        return None, "params_json 必须是对象"
    return parsed, None


def _save_params_entry(
    material,
    thickness_mm,
    laser_mode,
    engraving_mode,
    params,
    aliases=None,
    source="manual",
    notes="",
    params_file=MATERIAL_PARAMS_FILE,
):
    data, error = _load_material_params(params_file)
    if error:
        return None, error

    canonical, entry, error = _find_material(data, material, allow_create=True)
    if error:
        return None, error


    mode, resolved_engraving_mode, error = _resolve_material_engraving_mode(laser_mode, engraving_mode)
    if error:
        return None, error
    thickness_key, error = _format_thickness_key(thickness_mm)
    if error:
        return None, error

    normalized, error = _normalize_params(params)
    if error:
        return None, error
    normalized.update(
        {
            "source": source,
            "updated_at": time.time(),
        }
    )
    if notes:
        normalized["notes"] = notes

    if aliases:
        saved_aliases = entry.setdefault("aliases", [])
        for alias in aliases:
            alias = str(alias).strip()
            if alias and alias not in saved_aliases:
                saved_aliases.append(alias)

    thickness_entry = entry.setdefault("thicknesses", {}).setdefault(thickness_key, {})
    if mode == "engrave":
        existing = thickness_entry.get(mode)
        if _is_flat_params_entry(existing):
            existing = {"raster": existing}
        elif not isinstance(existing, dict):
            existing = {}
        existing[resolved_engraving_mode] = normalized
        thickness_entry[mode] = existing
    else:
        thickness_entry[mode] = normalized

    error = _save_material_params(data, params_file)
    if error:
        return None, error

    result = {
        "material": canonical,
        "thickness_mm": _parse_float(thickness_key),
        "laser_mode": mode,
        "params": normalized,
        "params_file": params_file,
    }
    if mode == "engrave":
        result["engraving_mode"] = resolved_engraving_mode
    return result, None


def _load_text_laser_task(task_id, tasks_dir=None):
    from tools import text_laser_task_tool

    return text_laser_task_tool._load_text_task(task_id, tasks_dir=tasks_dir)


def _latest_text_task_attempt(task):
    attempts = task.get("attempts", []) if isinstance(task, dict) else []
    if not attempts:
        return None, "文字激光任务没有可用 attempt"
    attempt = attempts[-1]
    gcode_file = str(attempt.get("gcode_file") or "").strip()
    if not gcode_file:
        return None, "文字激光任务最新 attempt 没有 gcode_file"
    return attempt, None


def _resolve_text_task_source(
    task_id,
    material="",
    thickness_mm=0.0,
    laser_mode="engrave",
    gcode_file="",
    tasks_dir=None,
):
    task, error = _load_text_laser_task(task_id, tasks_dir=tasks_dir)
    if error:
        return None, error

    attempt, error = _latest_text_task_attempt(task)
    if error:
        return None, f"{error}: {task_id}"

    task_gcode_file = attempt["gcode_file"]
    if gcode_file and os.path.abspath(gcode_file) != os.path.abspath(task_gcode_file):
        return None, "task_id 与 gcode_file 不一致，请使用文字任务最新 attempt 的 gcode_file"

    task_material = str(task.get("material") or "").strip()
    resolved_material = str(material or "").strip() or task_material
    resolved_thickness = thickness_mm or task.get("thickness_mm", 0.0)
    task_mode = str(task.get("laser_mode") or "").strip()
    resolved_mode = str(laser_mode or "").strip() or task_mode
    generation_options = task.get("generation_options", {}) if isinstance(task, dict) else {}
    task_engraving_mode = str(generation_options.get("engraving_mode") or task.get("engraving_mode") or "").strip()

    return {
        "task": task,
        "attempt": attempt,
        "task_id": task_id,
        "material": resolved_material,
        "thickness_mm": resolved_thickness,
        "laser_mode": resolved_mode,
        "gcode_file": task_gcode_file,
        "task_material": task_material,
        "task_laser_mode": task_mode,
        "task_engraving_mode": task_engraving_mode,
    }, None


def material_params(
    action="list",
    material="",
    thickness_mm=0.0,
    laser_mode="engrave",
    engraving_mode="",
    params_json="",
    laser_min_power=0,
    laser_max_power=800,
    feed_rate=1200,
    travel_rate=DEFAULT_TRAVEL_RATE,
    pixel_size_mm=0.1,
    threshold=-1,
    passes=1,
    aliases_json="",
    notes="",
    params_file=MATERIAL_PARAMS_FILE,
):
    action = str(action or "list").strip().lower()
    data, error = _load_material_params(params_file)
    if error:
        return _build_failure(error)

    if action in ("list", "all"):
        return _build_success({"params_file": params_file, "materials": data.get("materials", {})})

    if action in ("get", "recommend"):
        recommendation, error = _find_params(data, material, thickness_mm, laser_mode, engraving_mode)
        if error:
            return _build_failure(error)
        recommendation["params_file"] = params_file
        return _build_success(recommendation)

    if action in ("save", "upsert", "set"):
        json_params, error = _parse_params_json(params_json)
        if error:
            return _build_failure(error)
        raw_params = {
            "laser_min_power": laser_min_power,
            "laser_max_power": laser_max_power,
            "feed_rate": feed_rate,
            "travel_rate": travel_rate,
            "pixel_size_mm": pixel_size_mm,
            "threshold": threshold,
            "passes": passes,
        }
        raw_params.update(json_params)

        aliases = []
        if aliases_json:
            try:
                parsed_aliases = json.loads(aliases_json)
            except ValueError as exc:
                return _build_failure(f"aliases_json 不是合法 JSON: {exc}")
            if not isinstance(parsed_aliases, list):
                return _build_failure("aliases_json 必须是数组")
            aliases = parsed_aliases

        saved, error = _save_params_entry(
            material,
            thickness_mm,
            laser_mode,
            engraving_mode,
            raw_params,
            aliases=aliases,
            source="manual",
            notes=notes,
            params_file=params_file,
        )
        if error:
            return _build_failure(error)
        return _build_success(saved)

    if action == "delete":
        canonical, entry, error = _find_material(data, material)
        if error:
            return _build_failure(error)
        mode, resolved_engraving_mode, error = _resolve_material_engraving_mode(laser_mode, engraving_mode)
        if error:
            return _build_failure(error)
        thickness_key, error = _format_thickness_key(thickness_mm)
        if error:
            return _build_failure(error)

        modes = entry.get("thicknesses", {}).get(thickness_key)
        if mode == "engrave":
            if not modes or mode not in modes:
                return _build_failure(f"未找到可删除参数: {canonical} {thickness_key}mm {mode}/{resolved_engraving_mode}")
            entry_for_mode = modes[mode]
            if _is_flat_params_entry(entry_for_mode):
                if resolved_engraving_mode != "raster":
                    return _build_failure(f"未找到可删除参数: {canonical} {thickness_key}mm {mode}/{resolved_engraving_mode}")
                del modes[mode]
            elif isinstance(entry_for_mode, dict) and resolved_engraving_mode in entry_for_mode:
                del entry_for_mode[resolved_engraving_mode]
                if not entry_for_mode:
                    modes[mode] = {}
            else:
                return _build_failure(f"未找到可删除参数: {canonical} {thickness_key}mm {mode}/{resolved_engraving_mode}")
        else:
            if not modes or mode not in modes:
                return _build_failure(f"未找到可删除参数: {canonical} {thickness_key}mm {mode}")
            modes[mode] = None

        if canonical not in DEFAULT_MATERIAL_PARAMS.get("materials", {}):
            if modes.get(mode) in ({}, None):
                modes.pop(mode, None)
            if not modes:
                entry.get("thicknesses", {}).pop(thickness_key, None)
            if not entry.get("thicknesses"):
                data.get("materials", {}).pop(canonical, None)
        error = _save_material_params(data, params_file)
        if error:
            return _build_failure(error)
        result = {
            "material": canonical,
            "thickness_mm": _parse_float(thickness_key),
            "laser_mode": mode,
            "params_file": params_file,
        }
        if mode == "engrave":
            result["engraving_mode"] = resolved_engraving_mode
        return _build_success(result)

    return _build_failure("未知 action，支持: list / get / save / delete")


def _linspace_int(start, end, count):
    if count <= 1:
        return [int(round(start))]
    return [int(round(start + (end - start) * index / (count - 1))) for index in range(count)]


def _derive_range(base_value, minimum_value, maximum_value, spread_ratio):
    low = max(minimum_value, int(round(base_value * (1 - spread_ratio))))
    high = min(maximum_value, int(round(base_value * (1 + spread_ratio))))
    if low == high:
        high = min(maximum_value, low + 1)
    return low, high


def _build_calibration_cells(
    base_params,
    rows,
    columns,
    power_min=-1,
    power_max=-1,
    speed_min=-1,
    speed_max=-1,
    passes=0,
    matrix_kind="engrave",
    pass_min=1,
    pass_max=0,
):
    matrix_kind = str(matrix_kind or "engrave").strip().lower()
    if matrix_kind in {"raster", "outline", "engraving"}:
        matrix_kind = "engrave"
    if matrix_kind not in {"engrave", "cut"}:
        return None, "matrix_kind 必须是 engrave 或 cut"

    min_rows = 1 if matrix_kind == "cut" else 2
    rows, error = laser_grbl_tool._validate_int(rows, "rows", min_rows, 10)
    if error:
        return None, error
    columns, error = laser_grbl_tool._validate_int(columns, "columns", 2, 10)
    if error:
        return None, error

    base_power = int(base_params["laser_max_power"])
    base_speed = int(base_params["feed_rate"])
    if int(speed_min) < 0 or int(speed_max) < 0:
        speed_min, speed_max = _derive_range(base_speed, 1, max(base_speed * 2, base_speed + 1), 0.35)

    speed_min, error = laser_grbl_tool._validate_int(speed_min, "speed_min", 1)
    if error:
        return None, error
    speed_max, error = laser_grbl_tool._validate_int(speed_max, "speed_max", speed_min)
    if error:
        return None, error

    if matrix_kind == "cut":
        fixed_power = power_max if int(power_max) >= 0 else power_min
        if int(fixed_power) < 0:
            fixed_power = base_power
        fixed_power, error = laser_grbl_tool._validate_int(fixed_power, "power_max", 0, DEFAULT_S_MAX)
        if error:
            return None, error
        pass_start = pass_min if int(pass_min) > 0 else passes
        if int(pass_start) <= 0:
            pass_start = base_params.get("passes", 1)
        pass_start, error = laser_grbl_tool._validate_int(pass_start, "pass_min", 1, DEFAULT_MAX_PASSES)
        if error:
            return None, error
        if int(pass_max) > 0:
            pass_end, error = laser_grbl_tool._validate_int(pass_max, "pass_max", pass_start, DEFAULT_MAX_PASSES)
            if error:
                return None, error
            pass_values = _linspace_int(pass_start, pass_end, rows)
        else:
            pass_end = pass_start + rows - 1
            if pass_end > DEFAULT_MAX_PASSES:
                return None, f"pass_min + rows - 1 不能大于 {DEFAULT_MAX_PASSES}"
            pass_values = list(range(pass_start, pass_end + 1))
        speed_values = _linspace_int(speed_min, speed_max, columns)
        cells = []
        cell_number = 1
        for row_index, pass_count in enumerate(pass_values, start=1):
            for column_index, speed in enumerate(speed_values, start=1):
                cells.append(
                    {
                        "cell_number": cell_number,
                        "row": row_index,
                        "column": column_index,
                        "laser_max_power": fixed_power,
                        "feed_rate": speed,
                        "passes": pass_count,
                    }
                )
                cell_number += 1
        return cells, None

    if int(power_min) < 0 or int(power_max) < 0:
        power_min, power_max = _derive_range(base_power, 1, DEFAULT_S_MAX, 0.25)

    power_min, error = laser_grbl_tool._validate_int(power_min, "power_min", 0, DEFAULT_S_MAX)
    if error:
        return None, error
    power_max, error = laser_grbl_tool._validate_int(power_max, "power_max", power_min, DEFAULT_S_MAX)
    if error:
        return None, error
    if passes <= 0:
        passes = base_params.get("passes", 1)
    passes, error = laser_grbl_tool._validate_int(passes, "passes", 1, DEFAULT_MAX_PASSES)
    if error:
        return None, error

    power_values = _linspace_int(power_min, power_max, columns)
    speed_values = _linspace_int(speed_min, speed_max, rows)
    cells = []
    cell_number = 1
    for row_index, speed in enumerate(speed_values, start=1):
        for column_index, power in enumerate(power_values, start=1):
            cells.append(
                {
                    "cell_number": cell_number,
                    "row": row_index,
                    "column": column_index,
                    "laser_max_power": power,
                    "feed_rate": speed,
                    "passes": passes,
                }
            )
            cell_number += 1
    return cells, None


def _generate_cell_gcode(gcode, cell, laser_mode, x, y, cell_size_mm, fill_step_mm, matrix_kind="engrave"):
    x0 = laser_grbl_tool._format_mm(x)
    y0 = laser_grbl_tool._format_mm(y)
    x1 = laser_grbl_tool._format_mm(x + cell_size_mm)
    y1 = laser_grbl_tool._format_mm(y + cell_size_mm)
    power = int(cell["laser_max_power"])
    feed_rate = int(cell["feed_rate"])
    passes = int(cell["passes"])
    gcode.append(
        f"; Cell {cell['cell_number']} row {cell['row']} col {cell['column']} S{power} F{feed_rate} passes {passes}"
    )
    gcode.append(f"G1 F{feed_rate}")

    if matrix_kind == "engrave":
        line_count = max(2, int(round(cell_size_mm / fill_step_mm)) + 1)
        for pass_index in range(passes):
            gcode.append(f"; Cell {cell['cell_number']} fill pass {pass_index + 1}")
            for line_index in range(line_count):
                line_y = min(y + line_index * fill_step_mm, y + cell_size_mm)
                y_line = laser_grbl_tool._format_mm(line_y)
                if line_index % 2 == 0:
                    start_x, end_x = x0, x1
                else:
                    start_x, end_x = x1, x0
                gcode.append(f"G0 X{start_x} Y{y_line}")
                gcode.append(f"S{power}")
                gcode.append(f"G1 X{end_x} Y{y_line}")
                gcode.append("S0")
        return

    for pass_index in range(passes):
        gcode.append(f"; Cell {cell['cell_number']} pass {pass_index + 1}")
        gcode.append(f"G0 X{x0} Y{y0}")
        gcode.append(f"S{power}")
        gcode.append(f"G1 X{x1} Y{y0}")
        gcode.append(f"G1 X{x1} Y{y1}")
        gcode.append(f"G1 X{x0} Y{y1}")
        gcode.append(f"G1 X{x0} Y{y0}")
        gcode.append("S0")


def _generate_calibration_gcode(
    cells,
    laser_mode,
    rows,
    columns,
    cell_size_mm=5.0,
    gap_mm=2.0,
    x0=0.0,
    y0=0.0,
    fill_step_mm=0.8,
    travel_rate=DEFAULT_TRAVEL_RATE,
    matrix_kind="engrave",
):
    cell_size_mm, error = laser_grbl_tool._validate_number(cell_size_mm, "cell_size_mm", 0)
    if error:
        return None, None, error
    gap_mm, error = laser_grbl_tool._validate_number(gap_mm, "gap_mm", 0, allow_zero=True)
    if error:
        return None, None, error
    x0, error = laser_grbl_tool._validate_number(x0, "x0", -999999, allow_zero=True)
    if error:
        return None, None, error
    y0, error = laser_grbl_tool._validate_number(y0, "y0", -999999, allow_zero=True)
    if error:
        return None, None, error
    fill_step_mm, error = laser_grbl_tool._validate_number(fill_step_mm, "fill_step_mm", 0)
    if error:
        return None, None, error
    travel_rate, error = laser_grbl_tool._validate_int(travel_rate, "travel_rate", 1)
    if error:
        return None, None, error
    matrix_kind = str(matrix_kind or "engrave").strip().lower()
    if matrix_kind in {"raster", "outline", "engraving"}:
        matrix_kind = "engrave"
    if matrix_kind not in {"engrave", "cut"}:
        return None, None, "matrix_kind 必须是 engrave 或 cut"

    width_mm = columns * cell_size_mm + (columns - 1) * gap_mm
    height_mm = rows * cell_size_mm + (rows - 1) * gap_mm
    error = laser_grbl_tool._validate_work_area(width_mm, height_mm, x0=x0, y0=y0)
    if error:
        return None, None, error

    gcode = [
        "; Generated by laser_material_calibration_tool calibration grid",
        "; Count cells from left to right, top to bottom.",
        "; Firmware: GRBL laser mode recommended ($32=1)",
        "G21",
        "G90",
        "G94",
        laser_grbl_tool.ZERO_ORIGIN_COMMAND,
        "M4 S0",
        f"G0 F{travel_rate}",
        f"; Matrix kind: {matrix_kind}",
    ]

    step = cell_size_mm + gap_mm
    for cell in cells:
        x = x0 + (cell["column"] - 1) * step
        y = y0 + (cell["row"] - 1) * step
        _generate_cell_gcode(gcode, cell, laser_mode, x, y, cell_size_mm, fill_step_mm, matrix_kind=matrix_kind)

    gcode.extend(["M5", "G0 X0 Y0"])
    stats = {
        "matrix_kind": matrix_kind,
        "rows": rows,
        "columns": columns,
        "cell_count": len(cells),
        "width_mm": round(width_mm, 3),
        "height_mm": round(height_mm, 3),
        "line_count": len(gcode),
        "machine_profile": laser_grbl_tool._machine_profile(),
    }
    return gcode, stats, None


def _calibration_file_path(calibration_id):
    return os.path.join(CALIBRATION_DIR, f"{calibration_id}.json")


def _calibration_gcode_path(calibration_id):
    return os.path.join(CALIBRATION_DIR, f"{calibration_id}.gcode")


def _write_calibration_session(session):
    path = _calibration_file_path(session["calibration_id"])
    _write_json_file(path, session)
    return path


def _read_calibration_session(calibration_id):
    if not calibration_id:
        return None, "请指定 calibration_id"
    path = _calibration_file_path(calibration_id)
    if not os.path.isfile(path):
        return None, f"未找到调参会话: {calibration_id}"
    try:
        return _read_json_file(path), None
    except (OSError, ValueError) as exc:
        return None, f"读取调参会话失败: {exc}"


def run_calibration_grid(
    material="",
    thickness_mm=0.0,
    laser_mode="engrave",
    engraving_mode="",
    rows=5,
    columns=5,
    power_min=-1,
    power_max=-1,
    speed_min=-1,
    speed_max=-1,
    passes=0,
    matrix_kind="",
    pass_min=1,
    pass_max=0,
    cell_size_mm=5.0,
    gap_mm=2.0,
    x0=0.0,
    y0=0.0,
    fill_step_mm=0.8,
    output_file="",
    confirmed=False,
    dry_run=False,
    connection_mode="",
    port="",
    baudrate=laser_grbl_tool.DEFAULT_BAUDRATE,
    wait_for_response=True,
    run_in_background=True,
    network_host="",
    network_transport=DEFAULT_NETWORK_TRANSPORT,
    network_http_port=laser_network_grbl_tool.DEFAULT_HTTP_PORT,
    network_telnet_port=laser_network_grbl_tool.DEFAULT_TELNET_PORT,
    network_timeout=laser_network_grbl_tool.DEFAULT_TIMEOUT,
    params_file=MATERIAL_PARAMS_FILE,
    calibration_id="",
):
    if calibration_id and confirmed:
        return _send_saved_calibration_session(
            calibration_id=calibration_id,
            connection_mode=connection_mode,
            port=port,
            baudrate=baudrate,
            wait_for_response=wait_for_response,
            run_in_background=run_in_background,
            network_host=network_host,
            network_transport=network_transport,
            network_http_port=network_http_port,
            network_telnet_port=network_telnet_port,
            network_timeout=network_timeout,
        )

    resolved_connection_mode, error = laser_execution.resolve_laser_connection_mode(connection_mode)
    if error:
        return _build_failure(error)

    recommendation = recommend_laser_params(material, thickness_mm, laser_mode, engraving_mode, params_file=params_file)
    if not recommendation.get("success"):
        return recommendation

    result = recommendation["result"]
    mode = result["laser_mode"]
    resolved_matrix_kind = str(matrix_kind or "").strip().lower()
    if not resolved_matrix_kind or resolved_matrix_kind == "auto":
        resolved_matrix_kind = "cut" if mode == "cut" else "engrave"
    if resolved_matrix_kind in {"raster", "outline", "engraving"}:
        resolved_matrix_kind = "engrave"
    if resolved_matrix_kind not in {"engrave", "cut"}:
        return _build_failure("matrix_kind 必须是 engrave 或 cut")
    cells, error = _build_calibration_cells(
        result["params"],
        rows,
        columns,
        power_min=power_min,
        power_max=power_max,
        speed_min=speed_min,
        speed_max=speed_max,
        passes=passes,
        matrix_kind=resolved_matrix_kind,
        pass_min=pass_min,
        pass_max=pass_max,
    )
    if error:
        return _build_failure(error)

    gcode, stats, error = _generate_calibration_gcode(
        cells,
        mode,
        rows,
        columns,
        cell_size_mm=cell_size_mm,
        gap_mm=gap_mm,
        x0=x0,
        y0=y0,
        fill_step_mm=fill_step_mm,
        travel_rate=result["params"].get("travel_rate", DEFAULT_TRAVEL_RATE),
        matrix_kind=resolved_matrix_kind,
    )
    if error:
        return _build_failure(error)

    calibration_id = uuid.uuid4().hex
    gcode_file = output_file or _calibration_gcode_path(calibration_id)
    resolved_network_host = ""
    if resolved_connection_mode == "network":
        resolved_network_host = _resolve_network_host_for_session(network_host)
    session = {
        "calibration_id": calibration_id,
        "created_at": time.time(),
        "status": "dry_run" if dry_run else "prepared",
        "material": result["material"],
        "requested_material": result["requested_material"],
        "thickness_mm": result["thickness_mm"],
        "matched_thickness_mm": result["matched_thickness_mm"],
        "laser_mode": mode,
        "engraving_mode": result.get("engraving_mode", ""),
        "matrix_kind": resolved_matrix_kind,
        "base_params": result["params"],
        "cells": cells,
        "stats": stats,
        "gcode_file": gcode_file,
        "warnings": result.get("warnings", []),
        "connection": {
            "connection_mode": resolved_connection_mode,
            "network_host": resolved_network_host,
            "network_transport": DEFAULT_NETWORK_TRANSPORT,
            "network_http_port": network_http_port,
            "network_telnet_port": network_telnet_port,
            "network_timeout": network_timeout,
        },
    }

    if dry_run:
        return _build_success(
            {
                **session,
                "gcode_preview": gcode[:40],
                "confirmation_required": True,
            }
        )

    try:
        _ensure_parent_dir(gcode_file)
        with open(gcode_file, "w", encoding="utf-8", newline="\n") as file:
            file.write("\n".join(gcode))
            file.write("\n")
        session["gcode_sha256"] = file_sha256(gcode_file)
        session["session_file"] = _write_calibration_session(session)
    except OSError as exc:
        return _build_failure(f"写入测试矩阵失败: {exc}", session)

    response = {
        **session,
        "connection_mode": resolved_connection_mode,
        "network_transport": DEFAULT_NETWORK_TRANSPORT,
        "network_host": resolved_network_host,
        "confirmation_required": not confirmed,
        "instructions": "格子编号按从左到右、从上到下计数；例如 5x5 中第 8 格是第 2 行第 3 列。",
    }
    if not confirmed:
        response["result"] = "测试矩阵 G-code 已生成，但尚未发送。确认安全后再用 confirmed=true 运行。"
        return _build_success(response)

    prepared_result = {
        "source_file": gcode_file,
        "gcode_file": gcode_file,
        "converted": False,
        "calibration_id": calibration_id,
        "expected_gcode_sha256": session["gcode_sha256"],
    }
    send_result = _send_prepared_job(
        prepared_result,
        confirmed=True,
        connection_mode=resolved_connection_mode,
        port=port,
        baudrate=baudrate,
        wait_for_response=wait_for_response,
        run_in_background=run_in_background,
        network_host=resolved_network_host,
        network_transport=DEFAULT_NETWORK_TRANSPORT,
        network_http_port=network_http_port,
        network_telnet_port=network_telnet_port,
        network_timeout=network_timeout,
    )

    session["status"] = "running" if send_result.get("success") else "failed"
    session["send_result"] = send_result
    if send_result.get("job_id"):
        session["job_id"] = send_result["job_id"]
    try:
        _write_calibration_session(session)
    except OSError:
        pass

    response["confirmation_required"] = False
    response["send_result"] = send_result
    return _build_success(response)


def select_calibration_cell(
    calibration_id,
    cell_number,
    material="",
    thickness_mm=0.0,
    laser_mode="",
    engraving_mode="",
    notes="",
    params_file=MATERIAL_PARAMS_FILE,
):
    session, error = _read_calibration_session(calibration_id)
    if error:
        return _build_failure(error)

    cell_number, error = laser_grbl_tool._validate_int(cell_number, "cell_number", 1)
    if error:
        return _build_failure(error)
    selected = None
    for cell in session.get("cells", []):
        if int(cell.get("cell_number", 0)) == cell_number:
            selected = cell
            break
    if not selected:
        return _build_failure(f"调参会话中没有第 {cell_number} 格", session)

    base_params = dict(session.get("base_params", {}))
    params = {
        **base_params,
        "laser_max_power": selected["laser_max_power"],
        "feed_rate": selected["feed_rate"],
        "passes": selected["passes"],
    }
    target_material = material or session.get("material")
    target_thickness = thickness_mm or session.get("thickness_mm")
    target_mode = laser_mode or session.get("laser_mode")
    target_engraving_mode = engraving_mode or session.get("engraving_mode", "")
    saved, error = _save_params_entry(
        target_material,
        target_thickness,
        target_mode,
        target_engraving_mode,
        params,
        source="calibration",
        notes=notes,
        params_file=params_file,
    )
    if error:
        return _build_failure(error)

    session["status"] = "selected"
    session["selected_cell"] = selected
    session["selected_params"] = saved
    session["selected_at"] = time.time()
    try:
        _write_calibration_session(session)
    except OSError as exc:
        return _build_failure(f"保存调参会话失败: {exc}", saved)

    return _build_success(saved, detail={"calibration": session})


def _load_selected_params_from_calibration(calibration_id):
    session, error = _read_calibration_session(calibration_id)
    if error:
        return None, error
    selected = session.get("selected_params")
    if not selected:
        return None, f"调参会话 {calibration_id} 还没有选择最佳格"
    return selected, None


def _repeat_gcode_passes(source_file, passes):
    passes, error = laser_grbl_tool._validate_int(passes, "passes", 1, DEFAULT_MAX_PASSES)
    if error:
        return None, error
    if passes <= 1:
        return source_file, None

    base, extension = os.path.splitext(source_file)
    output_file = f"{base}_passes{passes}{extension or '.gcode'}"
    try:
        with open(source_file, "r", encoding="utf-8", errors="replace") as file:
            lines = [line.rstrip("\n") for line in file]
        with open(output_file, "w", encoding="utf-8", newline="\n") as file:
            file.write(f"; Repeated {passes} passes by laser_material_calibration_tool\n")
            for pass_index in range(passes):
                file.write(f"; Pass {pass_index + 1}/{passes}\n")
                for line in lines:
                    file.write(line + "\n")
                file.write("M5\n")
        return output_file, None
    except OSError as exc:
        return None, f"生成多次加工 G-code 失败: {exc}"


def start_tuned_job(
    material="",
    thickness_mm=0.0,
    laser_mode="engrave",
    calibration_id="",
    task_id="",
    gcode_file="",
    image_file="",
    output_file="",
    width_mm=0.0,
    height_mm=0.0,
    engraving_mode="",
    invert=False,
    bidirectional=False,
    overscan_mm=laser_grbl_tool.DEFAULT_RASTER_OVERSCAN_MM,
    raster_scan_direction="auto",
    overwrite=True,
    auto_trim=True,
    trim_tolerance=20,
    auto_size=True,
    dpi=300.0,
    lock_aspect_ratio=True,
    offset_x_mm=0.0,
    offset_y_mm=0.0,
    safe_margin_mm=laser_grbl_tool.DEFAULT_SAFE_MARGIN_MM,
    confirmed=False,
    dry_run=False,
    connection_mode="",
    port="",
    baudrate=laser_grbl_tool.DEFAULT_BAUDRATE,
    wait_for_response=True,
    run_in_background=True,
    network_host="",
    network_transport=DEFAULT_NETWORK_TRANSPORT,
    network_http_port=laser_network_grbl_tool.DEFAULT_HTTP_PORT,
    network_telnet_port=laser_network_grbl_tool.DEFAULT_TELNET_PORT,
    network_timeout=laser_network_grbl_tool.DEFAULT_TIMEOUT,
    params_file=MATERIAL_PARAMS_FILE,
    tasks_dir=None,
):
    resolved_connection_mode, error = laser_execution.resolve_laser_connection_mode(connection_mode)
    if error:
        return _build_failure(error)

    text_task_source = None
    if task_id:
        text_task_source, error = _resolve_text_task_source(
            task_id,
            material=material,
            thickness_mm=thickness_mm,
            laser_mode=laser_mode,
            gcode_file=gcode_file,
            tasks_dir=tasks_dir,
        )
        if error:
            return _build_failure(error)
        material = text_task_source["material"]
        thickness_mm = text_task_source["thickness_mm"]
        laser_mode = text_task_source["laser_mode"]
        if not str(engraving_mode or "").strip():
            engraving_mode = text_task_source.get("task_engraving_mode", "")
        gcode_file = text_task_source["gcode_file"]

    if calibration_id:
        selected, error = _load_selected_params_from_calibration(calibration_id)
        if error:
            return _build_failure(error)
        params = selected["params"]
        mode = selected["laser_mode"]
        source = {
            "type": "calibration",
            "calibration_id": calibration_id,
            "material": selected["material"],
            "thickness_mm": selected["thickness_mm"],
        }
        if selected.get("engraving_mode"):
            source["engraving_mode"] = selected["engraving_mode"]
    else:
        recommendation = recommend_laser_params(material, thickness_mm, laser_mode, engraving_mode, params_file=params_file)
        if not recommendation.get("success"):
            return recommendation
        rec = recommendation["result"]
        if text_task_source and confirmed:
            task_material = text_task_source["task_material"]
            if task_material and rec["material"] != task_material:
                return _build_failure(
                    "启动材料与文字激光任务材料不一致，请使用任务原材料或重新生成任务",
                    {
                        "task_id": task_id,
                        "task_material": task_material,
                        "requested_material": material,
                        "resolved_material": rec["material"],
                    },
                )
            task_mode = text_task_source["task_laser_mode"]
            if task_mode and rec["laser_mode"] != task_mode:
                return _build_failure(
                    "启动模式与文字激光任务模式不一致，请使用任务原模式或重新生成任务",
                    {
                        "task_id": task_id,
                        "task_laser_mode": task_mode,
                        "requested_laser_mode": laser_mode,
                        "resolved_laser_mode": rec["laser_mode"],
                    },
                )
            task_engraving_mode = text_task_source.get("task_engraving_mode", "")
            if task_engraving_mode and rec.get("engraving_mode") != task_engraving_mode:
                return _build_failure(
                    "启动雕刻策略与文字激光任务策略不一致，请使用任务原策略或重新生成任务",
                    {
                        "task_id": task_id,
                        "task_engraving_mode": task_engraving_mode,
                        "requested_engraving_mode": engraving_mode,
                        "resolved_engraving_mode": rec.get("engraving_mode", ""),
                    },
                )
        params = rec["params"]
        mode = rec["laser_mode"]
        resolved_engraving_mode = rec.get("engraving_mode", "")
        source = {
            "type": "material_params",
            "material": rec["material"],
            "thickness_mm": rec["thickness_mm"],
            "matched_thickness_mm": rec["matched_thickness_mm"],
            "match": rec["match"],
            "warnings": rec.get("warnings", []),
        }
        if resolved_engraving_mode:
            source["engraving_mode"] = resolved_engraving_mode
        if text_task_source:
            source.update(
                {
                    "type": "text_laser_task",
                    "task_id": task_id,
                    "attempt_no": text_task_source["attempt"].get("attempt_no"),
                    "task_material": text_task_source["task_material"],
                    "task_laser_mode": text_task_source["task_laser_mode"],
                    "task_engraving_mode": text_task_source.get("task_engraving_mode", ""),
                    "material_params_source": "material_params",
                }
            )

    preview = {
        "params_source": source,
        "task_id": task_id,
        "connection_mode": resolved_connection_mode,
        "network_transport": network_transport,
        "network_host": network_host,
        "laser_mode": mode,
        "engraving_mode": source.get("engraving_mode", ""),
        "params": params,
        "gcode_file": gcode_file,
        "image_file": image_file,
        "output_file": output_file,
            "image_import": {
                "auto_trim": auto_trim,
                "trim_tolerance": trim_tolerance,
                "auto_size": auto_size,
                "dpi": dpi,
                "lock_aspect_ratio": lock_aspect_ratio,
                "offset_x_mm": offset_x_mm,
                "offset_y_mm": offset_y_mm,
                "safe_margin_mm": safe_margin_mm,
                "overscan_mm": overscan_mm,
                "raster_scan_direction": raster_scan_direction,
            },
        "confirmation_required": not confirmed,
    }
    if dry_run or not confirmed:
        preview["result"] = "已选定调参参数，但未发送到激光机。确认安全后用 confirmed=true 启动。"
        return _build_success(preview)

    prepared = laser_grbl_tool.prepare_gcode_file_for_sending(
        gcode_file=gcode_file,
        image_file=image_file,
        output_file=output_file,
        width_mm=width_mm,
        height_mm=height_mm,
        pixel_size_mm=params["pixel_size_mm"],
        feed_rate=params["feed_rate"],
        travel_rate=params["travel_rate"],
        laser_min_power=params["laser_min_power"],
        laser_max_power=params["laser_max_power"],
        threshold=params["threshold"],
        engraving_mode=source.get("engraving_mode", engraving_mode),
        invert=invert,
        bidirectional=bidirectional,
        overscan_mm=overscan_mm,
        raster_scan_direction=raster_scan_direction,
        overwrite=overwrite,
        laser_mode=mode,
        auto_trim=auto_trim,
        trim_tolerance=trim_tolerance,
        auto_size=auto_size,
        dpi=dpi,
        lock_aspect_ratio=lock_aspect_ratio,
        offset_x_mm=offset_x_mm,
        offset_y_mm=offset_y_mm,
        safe_margin_mm=safe_margin_mm,
    )
    if not prepared.get("success"):
        return prepared

    prepared_result = prepared["result"]
    final_gcode = prepared_result["gcode_file"]
    if int(params.get("passes", 1)) > 1:
        repeated_file, error = _repeat_gcode_passes(final_gcode, params["passes"])
        if error:
            return _build_failure(error, prepared_result)
        prepared_result = {
            **prepared_result,
            "gcode_file": repeated_file,
            "single_pass_gcode_file": final_gcode,
            "passes_applied": params["passes"],
        }
    try:
        prepared_result["expected_gcode_sha256"] = file_sha256(
            prepared_result["gcode_file"]
        )
    except OSError:
        return _build_failure(
            "无法读取最终调参 G-code 以计算发送摘要",
            error_code="preview_content_mismatch",
        )

    send_result = _send_prepared_job(
        prepared_result,
        confirmed=True,
        connection_mode=resolved_connection_mode,
        port=port,
        baudrate=baudrate,
        wait_for_response=wait_for_response,
        run_in_background=run_in_background,
        network_host=network_host,
        network_transport=network_transport,
        network_http_port=network_http_port,
        network_telnet_port=network_telnet_port,
        network_timeout=network_timeout,
    )

    return _build_success(
        {
            **preview,
            "confirmation_required": False,
            "prepared": prepared_result,
            "send_result": send_result,
        }
    )


def register_tool(mcp):
    @mcp.tool()
    def material_params_tool(
        action: str = "list",
        material: str = "",
        thickness_mm: float = 0.0,
        laser_mode: str = "engrave",
        engraving_mode: str = "",
        params_json: str = "",
        laser_min_power: int = 0,
        laser_max_power: int = 800,
        feed_rate: int = 1200,
        travel_rate: int = DEFAULT_TRAVEL_RATE,
        pixel_size_mm: float = 0.1,
        threshold: int = -1,
        passes: int = 1,
        aliases_json: str = "",
        notes: str = "",
    ) -> dict:
        """
        管理激光材料参数库。用于按材料、厚度、雕刻/切割模式保存或读取推荐参数。
        laser_mode='engrave' 时，engraving_mode='raster' 和 'outline' 是分开的参数集。

        action:
            list - 列出材料库
            get/recommend - 读取指定 material + thickness_mm + laser_mode + engraving_mode 的推荐参数
            save/upsert/set - 保存参数
            delete - 删除指定参数
        params_json 可一次传入完整参数对象；单独字段会作为默认值并被 params_json 覆盖。
        """
        return material_params(
            action=action,
            material=material,
            thickness_mm=thickness_mm,
            laser_mode=laser_mode,
            engraving_mode=engraving_mode,
            params_json=params_json,
            laser_min_power=laser_min_power,
            laser_max_power=laser_max_power,
            feed_rate=feed_rate,
            travel_rate=travel_rate,
            pixel_size_mm=pixel_size_mm,
            threshold=threshold,
            passes=passes,
            aliases_json=aliases_json,
            notes=notes,
        )

    @mcp.tool()
    def recommend_laser_params_tool(
        material: str,
        thickness_mm: float,
        laser_mode: str = "engrave",
        engraving_mode: str = "",
    ) -> dict:
        """根据材料、厚度、雕刻/切割模式和雕刻策略推荐功率、速度、次数等参数。"""
        return recommend_laser_params(material, thickness_mm, laser_mode, engraving_mode)

    @mcp.tool()
    def run_calibration_grid_tool(
        material: str = "",
        thickness_mm: float = 0.0,
        laser_mode: str = "engrave",
        engraving_mode: str = "",
        rows: int = 5,
        columns: int = 5,
        power_min: int = -1,
        power_max: int = -1,
        speed_min: int = -1,
        speed_max: int = -1,
        passes: int = 0,
        matrix_kind: str = "",
        pass_min: int = 1,
        pass_max: int = 0,
        cell_size_mm: float = 5.0,
        gap_mm: float = 2.0,
        x0: float = 0.0,
        y0: float = 0.0,
        fill_step_mm: float = 0.8,
        output_file: str = "",
        confirmed: bool = False,
        dry_run: bool = False,
        connection_mode: str = "",
        port: str = "",
        baudrate: int = laser_grbl_tool.DEFAULT_BAUDRATE,
        wait_for_response: bool = True,
        run_in_background: bool = True,
        network_host: str = "",
        network_transport: str = DEFAULT_NETWORK_TRANSPORT,
        network_http_port: int = laser_network_grbl_tool.DEFAULT_HTTP_PORT,
        network_telnet_port: int = laser_network_grbl_tool.DEFAULT_TELNET_PORT,
        network_timeout: float = laser_network_grbl_tool.DEFAULT_TIMEOUT,
        calibration_id: str = "",
    ) -> dict:
        """
        生成 5x5 等测试矩阵；confirmed=false 时只生成 G-code 和调参会话，不发送到机器。
        第一次返回 calibration_id 后，用户确认开始时可只传 calibration_id 和 confirmed=true 继续发送上次生成的测试矩阵。
        格子编号按从左到右、从上到下计数，select_calibration_cell_tool 使用该编号保存最佳参数。
        """
        return run_calibration_grid(
            material=material,
            thickness_mm=thickness_mm,
            laser_mode=laser_mode,
            engraving_mode=engraving_mode,
            rows=rows,
            columns=columns,
            power_min=power_min,
            power_max=power_max,
            speed_min=speed_min,
            speed_max=speed_max,
            passes=passes,
            matrix_kind=matrix_kind,
            pass_min=pass_min,
            pass_max=pass_max,
            cell_size_mm=cell_size_mm,
            gap_mm=gap_mm,
            x0=x0,
            y0=y0,
            fill_step_mm=fill_step_mm,
            output_file=output_file,
            confirmed=confirmed,
            dry_run=dry_run,
            connection_mode=connection_mode,
            port=port,
            baudrate=baudrate,
            wait_for_response=wait_for_response,
            run_in_background=run_in_background,
            network_host=network_host,
            network_transport=network_transport,
            network_http_port=network_http_port,
            network_telnet_port=network_telnet_port,
            network_timeout=network_timeout,
            calibration_id=calibration_id,
        )

    @mcp.tool()
    def select_calibration_cell_tool(
        calibration_id: str,
        cell_number: int,
        material: str = "",
        thickness_mm: float = 0.0,
        laser_mode: str = "",
        engraving_mode: str = "",
        notes: str = "",
    ) -> dict:
        """选择测试矩阵中效果最好的格子，并保存为该材料/厚度/模式的推荐参数。"""
        return select_calibration_cell(
            calibration_id=calibration_id,
            cell_number=cell_number,
            material=material,
            thickness_mm=thickness_mm,
            laser_mode=laser_mode,
            engraving_mode=engraving_mode,
            notes=notes,
        )

    @mcp.tool()
    def start_tuned_job_tool(
        material: str = "",
        thickness_mm: float = 0.0,
        laser_mode: str = "engrave",
        calibration_id: str = "",
        task_id: str = "",
        gcode_file: str = "",
        image_file: str = "",
        output_file: str = "",
        width_mm: float = 0.0,
        height_mm: float = 0.0,
        engraving_mode: str = "",
        invert: bool = False,
        bidirectional: bool = False,
        overscan_mm: float = laser_grbl_tool.DEFAULT_RASTER_OVERSCAN_MM,
        raster_scan_direction: str = "auto",
        overwrite: bool = True,
        auto_trim: bool = True,
        trim_tolerance: float = 20,
        auto_size: bool = True,
        dpi: float = 300.0,
        lock_aspect_ratio: bool = True,
        offset_x_mm: float = 0.0,
        offset_y_mm: float = 0.0,
        safe_margin_mm: float = laser_grbl_tool.DEFAULT_SAFE_MARGIN_MM,
        confirmed: bool = False,
        dry_run: bool = False,
        connection_mode: str = "",
        port: str = "",
        baudrate: int = laser_grbl_tool.DEFAULT_BAUDRATE,
        wait_for_response: bool = True,
        run_in_background: bool = True,
        network_host: str = "",
        network_transport: str = DEFAULT_NETWORK_TRANSPORT,
        network_http_port: int = laser_network_grbl_tool.DEFAULT_HTTP_PORT,
        network_telnet_port: int = laser_network_grbl_tool.DEFAULT_TELNET_PORT,
        network_timeout: float = laser_network_grbl_tool.DEFAULT_TIMEOUT,
    ) -> dict:
        """
        使用材料推荐参数或已选择的调参结果启动正式任务。
        task_id 可引用 generate_text_laser_task_tool 保存的文字激光任务，工具会使用最新 attempt 的 gcode_file。
        confirmed=false/dry_run=true 时只返回将使用的参数，不发送到 GRBL 设备。
        """
        return start_tuned_job(
            material=material,
            thickness_mm=thickness_mm,
            laser_mode=laser_mode,
            calibration_id=calibration_id,
            task_id=task_id,
            gcode_file=gcode_file,
            image_file=image_file,
            output_file=output_file,
            width_mm=width_mm,
            height_mm=height_mm,
            engraving_mode=engraving_mode,
            invert=invert,
            bidirectional=bidirectional,
            overscan_mm=overscan_mm,
            raster_scan_direction=raster_scan_direction,
            overwrite=overwrite,
            auto_trim=auto_trim,
            trim_tolerance=trim_tolerance,
            auto_size=auto_size,
            dpi=dpi,
            lock_aspect_ratio=lock_aspect_ratio,
            offset_x_mm=offset_x_mm,
            offset_y_mm=offset_y_mm,
            safe_margin_mm=safe_margin_mm,
            confirmed=confirmed,
            dry_run=dry_run,
            connection_mode=connection_mode,
            port=port,
            baudrate=baudrate,
            wait_for_response=wait_for_response,
            run_in_background=run_in_background,
            network_host=network_host,
            network_transport=network_transport,
            network_http_port=network_http_port,
            network_telnet_port=network_telnet_port,
            network_timeout=network_timeout,
        )
