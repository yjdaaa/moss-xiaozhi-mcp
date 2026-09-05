import json
import os
import time
import uuid

from core import laser_execution, laser_time_estimate
from core.laser_runtime.config import get_laser_settings
from core.laser_runtime.models import file_sha256
from tools import (
    laser_material_calibration_tool,
    laser_grbl_tool,
    laser_network_grbl_tool,
    text_image_gcode_tool,
    text_image_tool,
)


ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
_SETTINGS = get_laser_settings()
TEXT_LASER_TASKS_DIR = os.environ.get(
    "LASER_TEXT_TASKS_DIR",
    os.path.join(ROOT_DIR, ".runtime", "lasergrbl_text_tasks"),
)
PREPARED_GCODE_DIR = str(_SETTINGS.prepared_gcode_dir)
PREPARED_GCODE_EXTENSIONS = (".gcode", ".nc")
DEFAULT_MIN_FEED_RATE = _SETTINGS.min_feed_rate
DEFAULT_S_MAX = laser_grbl_tool.DEFAULT_LASER_S_MAX
DEFAULT_MAX_PASSES = laser_material_calibration_tool.DEFAULT_MAX_PASSES

PARAM_KEYS = (
    "laser_min_power",
    "laser_max_power",
    "feed_rate",
    "travel_rate",
    "pixel_size_mm",
    "threshold",
    "passes",
)
STRATEGY_NAMES = {
    "lower_power",
    "raise_speed",
    "raise_power",
    "lower_speed",
    "increase_passes",
}
FEEDBACK_RULES = [
    {
        "issue": "too_burnt",
        "keywords": ["太焦了", "发黑", "糊边", "烧焦", "burnt", "too dark", "charred"],
        "diagnosis": "能量密度偏高，文字区域出现过烧、发黑或糊边。",
        "recommended_strategy": "lower_power",
        "suggestions": [
            ("lower_power", "将功率降低约 20%，优先减少烧焦和发黑。"),
            ("raise_speed", "将速度提高约 25%，用更短驻留时间降低单位面积能量。"),
        ],
    },
    {
        "issue": "too_light",
        "keywords": ["太浅了", "看不清", "不明显", "too light", "faint"],
        "diagnosis": "能量密度偏低，文字痕迹偏浅或不明显。",
        "recommended_strategy": "raise_power",
        "suggestions": [
            ("raise_power", "将功率提高约 15%，让文字痕迹更清晰。"),
            ("lower_speed", "将速度降低约 15%，通过增加驻留时间加深效果。"),
        ],
    },
    {
        "issue": "cut_not_through",
        "keywords": ["切不透", "没切穿", "not cut through"],
        "diagnosis": "切割能量不足或 passes 不够，材料没有完全切穿。",
        "recommended_strategy": "increase_passes",
        "suggestions": [
            ("increase_passes", "加工次数增加 1 次，优先提高切穿概率。"),
            ("lower_speed", "将速度降低约 20%，让单次切割获得更多能量。"),
        ],
    },
    {
        "issue": "rough_edges",
        "keywords": ["边缘毛糙", "毛边", "rough edges"],
        "diagnosis": "边缘质量不稳定，可能存在局部过烧或运动/能量匹配不佳。",
        "recommended_strategy": "lower_power",
        "suggestions": [
            ("lower_power", "将功率小幅降低约 10%，减轻边缘烧蚀。"),
            ("lower_speed", "将速度小幅降低约 10%，改善线条连续性。"),
        ],
    },
    {
        "issue": "broken_lines",
        "keywords": ["断线", "线条断续", "broken lines"],
        "diagnosis": "线条能量或运动连续性不足，出现断续。",
        "recommended_strategy": "lower_speed",
        "suggestions": [
            ("lower_speed", "将速度降低约 15%，提高线条连续性。"),
            ("raise_power", "将功率提高约 10%，补偿断线区域能量不足。"),
        ],
    },
]


def _build_success(result, detail=None):
    payload = {"success": True, "result": result}
    if detail is not None:
        payload["detail"] = detail
    return payload


def _build_failure(message, detail=None):
    payload = {"success": False, "result": message}
    if detail is not None:
        payload["detail"] = detail
    return payload


def _is_set(value):
    return value is not None and value != ""


def _clamp_int(value, minimum, maximum=None):
    number = int(round(float(value)))
    if number < minimum:
        number = minimum
    if maximum is not None and number > maximum:
        number = maximum
    return number


def _normalize_task_id(task_id):
    normalized = str(task_id or "").strip()
    if not normalized:
        return None, "请指定 task_id"
    if normalized != os.path.basename(normalized):
        return None, "task_id 不能包含路径分隔符"
    return normalized, None


def _task_dir(task_id, tasks_dir=None):
    tasks_dir = tasks_dir or TEXT_LASER_TASKS_DIR
    return os.path.join(tasks_dir, task_id)


def _task_file_path(task_id, tasks_dir=None):
    return os.path.join(_task_dir(task_id, tasks_dir=tasks_dir), "task.json")


def _attempt_image_path(task_id, attempt_no, tasks_dir=None):
    return os.path.join(_task_dir(task_id, tasks_dir=tasks_dir), f"attempt_{attempt_no}.png")


def _attempt_gcode_path(task_id, attempt_no, tasks_dir=None):
    return os.path.join(_task_dir(task_id, tasks_dir=tasks_dir), f"attempt_{attempt_no}.gcode")


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


def _load_text_task(task_id, tasks_dir=None):
    task_id, error = _normalize_task_id(task_id)
    if error:
        return None, error
    path = _task_file_path(task_id, tasks_dir=tasks_dir)
    if not os.path.isfile(path):
        return None, f"未找到文字激光任务: {task_id}"
    try:
        return _read_json_file(path), None
    except (OSError, ValueError) as exc:
        return None, f"读取文字激光任务失败: {exc}"


def _save_text_task(task, tasks_dir=None):
    task["updated_at"] = time.time()
    path = _task_file_path(task["task_id"], tasks_dir=tasks_dir)
    _write_json_file(path, task)
    return path


def _params_with_power_info(params):
    result = dict(params)
    s_max = int(DEFAULT_S_MAX)
    laser_max_power = int(result.get("laser_max_power", 0))
    result["s_max"] = s_max
    result["power_percent"] = round(laser_max_power * 100.0 / s_max, 2) if s_max > 0 else 0
    return result


def _strip_power_info(params):
    return {key: params[key] for key in PARAM_KEYS if key in params}


def _clamp_adjusted_params(params):
    clamped = dict(params)
    laser_min_power = _clamp_int(clamped.get("laser_min_power", 0), 0, DEFAULT_S_MAX)
    laser_max_power = _clamp_int(clamped.get("laser_max_power", 800), 1, DEFAULT_S_MAX)
    if laser_max_power < laser_min_power:
        laser_max_power = laser_min_power
    clamped["laser_min_power"] = laser_min_power
    clamped["laser_max_power"] = laser_max_power
    clamped["feed_rate"] = _clamp_int(clamped.get("feed_rate", 1200), DEFAULT_MIN_FEED_RATE)
    clamped["travel_rate"] = _clamp_int(clamped.get("travel_rate", 3000), 1)
    clamped["threshold"] = _clamp_int(clamped.get("threshold", -1), -1, 255)
    clamped["passes"] = _clamp_int(clamped.get("passes", 1), 1, DEFAULT_MAX_PASSES)
    clamped["pixel_size_mm"] = float(clamped.get("pixel_size_mm", 0.1))
    return clamped


def _normalize_params(params):
    normalized, error = laser_material_calibration_tool._normalize_params(params)
    if error:
        return None, error
    if int(normalized.get("laser_max_power", 0)) <= 0:
        return None, "laser_max_power 必须大于 0"
    return _params_with_power_info(_clamp_adjusted_params(normalized)), None


def _s_from_power_percent(power_percent):
    percent, error = laser_grbl_tool._validate_number(power_percent, "power_percent", 0)
    if error:
        return None, error
    if percent > 100:
        return None, "power_percent 不能大于 100"
    return max(1, int(round(DEFAULT_S_MAX * percent / 100.0))), None


def _resolve_power_override(power_percent=None, laser_max_power=None):
    percent_set = _is_set(power_percent)
    power_set = _is_set(laser_max_power)
    if not percent_set and not power_set:
        return None, None

    percent_power = None
    if percent_set:
        percent_power, error = _s_from_power_percent(power_percent)
        if error:
            return None, error

    explicit_power = None
    if power_set:
        explicit_power, error = laser_grbl_tool._validate_int(
            laser_max_power, "laser_max_power", 1, DEFAULT_S_MAX
        )
        if error:
            return None, error

    if percent_set and power_set and abs(percent_power - explicit_power) > 1:
        return None, "power_percent 与 laser_max_power 冲突，请只保留一个或让二者对应同一 S 值"
    return explicit_power if power_set else percent_power, None


def _apply_overrides(
    base_params,
    laser_min_power=None,
    laser_max_power=None,
    power_percent=None,
    feed_rate=None,
    travel_rate=None,
    pixel_size_mm=None,
    threshold=None,
    passes=None,
):
    params = _strip_power_info(base_params)
    power, error = _resolve_power_override(power_percent, laser_max_power)
    if error:
        return None, error
    if power is not None:
        params["laser_max_power"] = power
    if _is_set(laser_min_power):
        params["laser_min_power"] = laser_min_power
    if _is_set(feed_rate):
        params["feed_rate"] = feed_rate
    if _is_set(travel_rate):
        params["travel_rate"] = travel_rate
    if _is_set(pixel_size_mm):
        params["pixel_size_mm"] = pixel_size_mm
    if _is_set(threshold):
        params["threshold"] = threshold
    if _is_set(passes):
        params["passes"] = passes
    return _normalize_params(params)


def _has_manual_overrides(
    laser_min_power=None,
    laser_max_power=None,
    power_percent=None,
    feed_rate=None,
    travel_rate=None,
    pixel_size_mm=None,
    threshold=None,
    passes=None,
):
    return any(
        _is_set(value)
        for value in (
            laser_min_power,
            laser_max_power,
            power_percent,
            feed_rate,
            travel_rate,
            pixel_size_mm,
            threshold,
            passes,
        )
    )


def _apply_strategy(base_params, strategy, issue=None):
    strategy = str(strategy or "").strip()
    if strategy not in STRATEGY_NAMES:
        return None, f"未知调参策略: {strategy}，支持: {', '.join(sorted(STRATEGY_NAMES))}"

    params = _strip_power_info(base_params)
    if strategy == "lower_power":
        factor = 0.9 if issue == "rough_edges" else 0.8
        params["laser_max_power"] = int(round(params.get("laser_max_power", 800) * factor))
    elif strategy == "raise_speed":
        params["feed_rate"] = int(round(params.get("feed_rate", 1200) * 1.25))
    elif strategy == "raise_power":
        factor = 1.10 if issue == "broken_lines" else 1.15
        params["laser_max_power"] = int(round(params.get("laser_max_power", 800) * factor))
    elif strategy == "lower_speed":
        factor = 0.8 if issue == "cut_not_through" else 0.9 if issue == "rough_edges" else 0.85
        params["feed_rate"] = int(round(params.get("feed_rate", 1200) * factor))
    elif strategy == "increase_passes":
        params["passes"] = int(params.get("passes", 1)) + 1
    return _normalize_params(params)


def _match_feedback_rule(feedback_text):
    text = str(feedback_text or "").strip().lower()
    if not text:
        return None, "feedback_text 不能为空"
    for rule in FEEDBACK_RULES:
        for keyword in rule["keywords"]:
            if keyword.lower() in text:
                return rule, None
    return None, None


def _build_feedback_suggestions(rule, base_params):
    suggestions = []
    for strategy, reason in rule["suggestions"]:
        adjusted, error = _apply_strategy(base_params, strategy, issue=rule["issue"])
        if error:
            return None, error
        suggestions.append(
            {
                "strategy": strategy,
                "params": adjusted,
                "reason": reason,
            }
        )
    return suggestions, None


def _no_send_result(reason, connection_mode="network", confirmation_required=True):
    messages = {
        "not_requested": "已生成文件，未请求发送到激光机。",
        "confirmation_required": "已生成文件，但未确认发送；确认安全后再传 confirmed=true。",
        "dry_run": "已生成文件，dry_run=true，因此未发送到激光机。",
        "missing_network_host": "已生成文件，但网络发送缺少 network_host 或 LASER_NETWORK_HOST，未发送到激光机。",
    }
    return {
        "success": True if reason != "missing_network_host" else False,
        "result": messages.get(reason, "未发送到激光机。"),
        "skipped": True,
        "reason": reason,
        "connection_mode": connection_mode,
        "confirmation_required": confirmation_required,
    }


def _format_speech_number(value, fallback="未定"):
    if value is None or value == "":
        return fallback
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:g}"


def _format_duration_from_estimate(estimate):
    if not isinstance(estimate, dict):
        return ""
    seconds = estimate.get("estimated_seconds") or estimate.get("total_seconds")
    if not isinstance(seconds, (int, float)) or seconds <= 0:
        return ""
    return laser_time_estimate.format_duration_zh(seconds)


def _format_mode_label(laser_mode, engraving_mode=""):
    if str(laser_mode or "").strip().lower() == "cut":
        return "切割"
    if str(engraving_mode or "").strip().lower() == "outline":
        return "轮廓雕刻"
    return "雕刻"


def _format_passes_suffix(passes):
    try:
        count = int(passes)
    except (TypeError, ValueError):
        return ""
    if count > 1:
        return f"，{count} 遍"
    return ""


def _format_placement_speech(attempt):
    placement = ((attempt.get("gcode") or {}).get("placement") or {}) if isinstance(attempt, dict) else {}
    width = placement.get("final_width_mm")
    height = placement.get("final_height_mm")
    offset_x = placement.get("offset_x_mm")
    offset_y = placement.get("offset_y_mm")
    if width is None or height is None:
        return ""
    text = f"实际尺寸 {_format_speech_number(width)} x {_format_speech_number(height)} 毫米"
    if offset_x is not None and offset_y is not None:
        text += f"，偏移 X{_format_speech_number(offset_x)} Y{_format_speech_number(offset_y)} 毫米"
    return text + "。"


def _send_speech_tail(send_result):
    if isinstance(send_result, dict) and send_result.get("success") and not send_result.get("skipped"):
        return "已开始发送，可查询任务状态。"
    return "确认开始后才发送。"


def _text_vector_fallback_speech_prefix(attempt):
    gcode = attempt.get("gcode") if isinstance(attempt, dict) else {}
    if not isinstance(gcode, dict) or not gcode.get("text_vector_outline_fallback"):
        return ""
    return text_image_gcode_tool.TEXT_VECTOR_OUTLINE_FALLBACK_SPEECH


def _build_text_task_speech(task, attempt, send_result):
    duration = _format_duration_from_estimate(attempt.get("time_estimate"))
    sentence = "文件已生成。"
    if duration:
        sentence = f"文件已生成，预计需要 {duration}。"

    params = attempt.get("params", {}) if isinstance(attempt, dict) else {}
    generation_options = task.get("generation_options", {}) if isinstance(task, dict) else {}
    material = task.get("material") or "材料未明"
    thickness = task.get("thickness_mm")
    thickness_text = f"{_format_speech_number(thickness)} 毫米" if thickness is not None else "厚度未明"
    mode = _format_mode_label(task.get("laser_mode"), generation_options.get("engraving_mode"))
    power = _format_speech_number(params.get("laser_max_power"))
    speed = _format_speech_number(params.get("feed_rate"))
    sentence += (
        f"材料 {material}，厚度 {thickness_text}，模式 {mode}，"
        f"功率 S{power}，速度 F{speed}{_format_passes_suffix(params.get('passes'))}。"
    )
    sentence += _format_placement_speech(attempt)
    recommendation = task.get("recommendation") if isinstance(task, dict) else {}
    recommendation = recommendation if isinstance(recommendation, dict) else {}
    if recommendation.get("match") == "nearest_thickness":
        requested = recommendation.get("thickness_mm", task.get("thickness_mm"))
        matched = recommendation.get("matched_thickness_mm")
        sentence += (
            f"请求厚度 {_format_speech_number(requested)} 毫米，"
            f"参数厚度 {_format_speech_number(matched)} 毫米。"
        )
    if recommendation.get("can_send") is False:
        blocked = str(recommendation.get("send_blocked_reason") or "当前材料参数未达到可发送状态").strip()
        sentence += f"当前不可发送：{blocked}。"
    else:
        sentence += _send_speech_tail(send_result)
    return f"{_text_vector_fallback_speech_prefix(attempt)}{sentence}"


def _build_prepared_file_speech(result, send_result):
    duration = _format_duration_from_estimate(result.get("time_estimate"))
    if duration:
        sentence = f"已找到预制雕刻文件，预计需要 {duration}。"
    else:
        sentence = "已找到预制雕刻文件。"
    sentence += _send_speech_tail(send_result)
    return sentence


def _prepared_gcode_candidate(path):
    if not os.path.isfile(path):
        return None
    stem, extension = os.path.splitext(os.path.basename(path))
    if extension.lower() not in PREPARED_GCODE_EXTENSIONS:
        return None
    return {"name": os.path.basename(path), "stem": stem, "path": path}


def _find_prepared_gcode(text, prepared_gcode_dir=None):
    query = str(text or "").strip()
    if not query:
        return {"status": "not_found", "candidates": []}

    lookup_dir = prepared_gcode_dir or PREPARED_GCODE_DIR
    if not lookup_dir or not os.path.isdir(lookup_dir):
        return {"status": "not_found", "candidates": [], "lookup_dir": lookup_dir}

    candidates = []
    try:
        names = sorted(os.listdir(lookup_dir))
    except OSError as exc:
        return {
            "status": "error",
            "result": f"读取预制雕刻文件目录失败: {exc}",
            "lookup_dir": lookup_dir,
        }

    for name in names:
        candidate = _prepared_gcode_candidate(os.path.join(lookup_dir, name))
        if candidate:
            candidates.append(candidate)

    exact = [item for item in candidates if item["stem"] == query]
    matches = exact or [item for item in candidates if query in item["stem"]]
    match_type = "exact" if exact else "contains"
    if not matches:
        return {"status": "not_found", "candidates": [], "lookup_dir": lookup_dir}
    if len(matches) > 1:
        return {
            "status": "ambiguous",
            "match_type": match_type,
            "lookup_dir": lookup_dir,
            "candidates": matches,
        }
    return {
        "status": "matched",
        "match_type": match_type,
        "lookup_dir": lookup_dir,
        "file": matches[0],
    }


def _prepared_file_result(text, lookup, send_after_generate, send_result):
    prepared_file = lookup["file"]
    result = {
        "source": "prepared_gcode_file",
        "text": text,
        "lookup_dir": lookup.get("lookup_dir"),
        "match_type": lookup.get("match_type"),
        "gcode_file": prepared_file["path"],
        "prepared_file": prepared_file,
        "send_after_generate": bool(send_after_generate),
        "confirmation_required": bool(send_result.get("confirmation_required")),
        "send_result": send_result,
        "next_step": "已找到预制雕刻文件；如需启动机器，先复述文件和控制方式并等待确认。",
    }
    laser_time_estimate.add_time_estimate_fields(result, prepared_file["path"])
    result["speech"] = _build_prepared_file_speech(result, send_result)
    return result


def _ambiguous_prepared_file_result(text, lookup):
    return _build_success(
        {
            "source": "prepared_gcode_file_lookup",
            "text": text,
            "lookup_dir": lookup.get("lookup_dir"),
            "match_type": lookup.get("match_type"),
            "candidates": lookup.get("candidates", []),
            "confirmation_required": True,
            "result": "找到多个匹配的预制雕刻文件，请让用户指定要使用哪一个。",
        }
    )


def _send_via_laser_execution(
    prepared_result,
    connection_mode,
    port="",
    baudrate=laser_grbl_tool.DEFAULT_BAUDRATE,
    wait_for_response=True,
    run_in_background=True,
    network_host="",
    network_transport=laser_material_calibration_tool.DEFAULT_NETWORK_TRANSPORT,
    network_http_port=laser_network_grbl_tool.DEFAULT_HTTP_PORT,
    network_telnet_port=laser_network_grbl_tool.DEFAULT_TELNET_PORT,
    network_timeout=laser_network_grbl_tool.DEFAULT_TIMEOUT,
):
    if connection_mode == "network":
        resolved_host, error = laser_execution.resolve_laser_network_host(network_host)
        if error:
            return _build_failure(error, prepared_result)
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


def _send_generated_attempt(
    task_id,
    attempt,
    send_after_generate=False,
    confirmed=False,
    dry_run=False,
    connection_mode="",
    port="",
    baudrate=laser_grbl_tool.DEFAULT_BAUDRATE,
    wait_for_response=True,
    run_in_background=True,
    network_host="",
    network_transport=laser_material_calibration_tool.DEFAULT_NETWORK_TRANSPORT,
    network_http_port=laser_network_grbl_tool.DEFAULT_HTTP_PORT,
    network_telnet_port=laser_network_grbl_tool.DEFAULT_TELNET_PORT,
    network_timeout=laser_network_grbl_tool.DEFAULT_TIMEOUT,
):
    resolved_mode, error = laser_execution.resolve_laser_connection_mode(connection_mode)
    if error:
        return _build_failure(error)

    if not send_after_generate:
        return _no_send_result("not_requested", resolved_mode, confirmation_required=True)
    if dry_run:
        return _no_send_result("dry_run", resolved_mode, confirmation_required=True)
    if not confirmed:
        return _no_send_result("confirmation_required", resolved_mode, confirmation_required=True)
    if resolved_mode == "network":
        _, host_error = laser_execution.resolve_laser_network_host(network_host)
        if host_error:
            return _no_send_result("missing_network_host", resolved_mode, confirmation_required=False)

    prepared_result = {
        "source_file": attempt["gcode_file"],
        "gcode_file": attempt["gcode_file"],
        "converted": False,
        "task_id": task_id,
        "attempt_no": attempt["attempt_no"],
    }
    try:
        prepared_result["expected_gcode_sha256"] = file_sha256(attempt["gcode_file"])
    except OSError:
        return _build_failure("无法读取文字任务 G-code 以计算发送摘要")
    if attempt.get("time_estimate"):
        prepared_result["time_estimate"] = attempt["time_estimate"]
    if attempt.get("speech"):
        prepared_result["speech"] = attempt["speech"]
    return _send_via_laser_execution(
        prepared_result,
        connection_mode=resolved_mode,
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


def _send_prepared_gcode_file(
    gcode_file,
    send_after_generate=False,
    confirmed=False,
    dry_run=False,
    connection_mode="",
    port="",
    baudrate=laser_grbl_tool.DEFAULT_BAUDRATE,
    wait_for_response=True,
    run_in_background=True,
    network_host="",
    network_transport=laser_material_calibration_tool.DEFAULT_NETWORK_TRANSPORT,
    network_http_port=laser_network_grbl_tool.DEFAULT_HTTP_PORT,
    network_telnet_port=laser_network_grbl_tool.DEFAULT_TELNET_PORT,
    network_timeout=laser_network_grbl_tool.DEFAULT_TIMEOUT,
):
    resolved_mode, error = laser_execution.resolve_laser_connection_mode(connection_mode)
    if error:
        return _build_failure(error)

    if not send_after_generate:
        return _no_send_result("not_requested", resolved_mode, confirmation_required=True)
    if dry_run:
        return _no_send_result("dry_run", resolved_mode, confirmation_required=True)
    if not confirmed:
        return _no_send_result("confirmation_required", resolved_mode, confirmation_required=True)
    if resolved_mode == "network":
        _, host_error = laser_execution.resolve_laser_network_host(network_host)
        if host_error:
            return _no_send_result("missing_network_host", resolved_mode, confirmation_required=False)

    prepared_result = {
        "source_file": gcode_file,
        "gcode_file": gcode_file,
        "converted": False,
        "source": "prepared_gcode_file",
    }
    try:
        prepared_result["expected_gcode_sha256"] = file_sha256(gcode_file)
    except OSError:
        return _build_failure("无法读取预制 G-code 以计算发送摘要")
    laser_time_estimate.add_time_estimate_fields(prepared_result, gcode_file)
    return _send_via_laser_execution(
        prepared_result,
        connection_mode=resolved_mode,
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


def _generate_attempt_files(
    task_id,
    attempt_no,
    text,
    params,
    laser_mode,
    engraving_mode,
    width_mm,
    height_mm,
    image_width,
    image_height,
    font_size,
    font_path,
    auto_wrap,
    max_lines,
    layout_mode,
    invert,
    bidirectional,
    overwrite,
    overscan_mm=laser_grbl_tool.DEFAULT_RASTER_OVERSCAN_MM,
    raster_scan_direction="auto",
    auto_trim=True,
    trim_tolerance=20,
    auto_size=True,
    dpi=300.0,
    lock_aspect_ratio=True,
    offset_x_mm=0.0,
    offset_y_mm=0.0,
    safe_margin_mm=laser_grbl_tool.DEFAULT_SAFE_MARGIN_MM,
    image_output_file="",
    gcode_output_file="",
    tasks_dir=None,
):
    os.makedirs(_task_dir(task_id, tasks_dir=tasks_dir), exist_ok=True)
    image_file = image_output_file or _attempt_image_path(task_id, attempt_no, tasks_dir=tasks_dir)
    gcode_file = gcode_output_file or _attempt_gcode_path(task_id, attempt_no, tasks_dir=tasks_dir)
    gcode_params = _strip_power_info(params)

    generated = text_image_gcode_tool.create_text_image_gcode(
        text=text,
        image_output_file=image_file,
        gcode_output_file=gcode_file,
        image_width=image_width,
        image_height=image_height,
        font_size=font_size,
        font_path=font_path,
        auto_wrap=auto_wrap,
        max_lines=max_lines,
        layout_mode=layout_mode,
        width_mm=width_mm,
        height_mm=height_mm,
        pixel_size_mm=gcode_params["pixel_size_mm"],
        feed_rate=gcode_params["feed_rate"],
        travel_rate=gcode_params["travel_rate"],
        laser_min_power=gcode_params["laser_min_power"],
        laser_max_power=gcode_params["laser_max_power"],
        threshold=gcode_params["threshold"],
        invert=invert,
        bidirectional=bidirectional,
        overscan_mm=overscan_mm,
        raster_scan_direction=raster_scan_direction,
        overwrite=overwrite,
        laser_mode=laser_mode,
        engraving_mode=engraving_mode,
        auto_trim=auto_trim,
        trim_tolerance=trim_tolerance,
        auto_size=auto_size,
        dpi=dpi,
        lock_aspect_ratio=lock_aspect_ratio,
        offset_x_mm=offset_x_mm,
        offset_y_mm=offset_y_mm,
        safe_margin_mm=safe_margin_mm,
    )
    if not generated.get("success"):
        return None, generated

    result = dict(generated["result"])
    final_gcode_file = result["gcode_file"]
    passes = int(gcode_params.get("passes", 1))
    if passes > 1:
        repeated_file, error = laser_material_calibration_tool._repeat_gcode_passes(final_gcode_file, passes)
        if error:
            return None, _build_failure(error, result)
        result["single_pass_gcode_file"] = final_gcode_file
        result["gcode_file"] = repeated_file
        result["passes_applied"] = passes
        laser_time_estimate.add_time_estimate_fields(result, repeated_file)
    else:
        laser_time_estimate.add_time_estimate_fields(result, final_gcode_file)

    return result, None


def _build_attempt(
    attempt_no,
    params,
    generated,
    source,
    selected_strategy="",
    feedback_text="",
):
    attempt = {
        "attempt_no": attempt_no,
        "created_at": time.time(),
        "source": source,
        "selected_strategy": selected_strategy,
        "feedback_text": feedback_text,
        "params": params,
        "image_file": generated["image_file"],
        "gcode_file": generated["gcode_file"],
        "image": generated.get("image"),
        "gcode": generated.get("gcode"),
        "auto_trim": (generated.get("gcode") or {}).get("auto_trim"),
        "placement": (generated.get("gcode") or {}).get("placement"),
        "text_layout": generated.get("text_layout"),
        "single_pass_gcode_file": generated.get("single_pass_gcode_file"),
        "passes_applied": generated.get("passes_applied", int(params.get("passes", 1))),
    }
    if generated.get("time_estimate"):
        attempt["time_estimate"] = generated["time_estimate"]
    if generated.get("speech"):
        attempt["speech"] = generated["speech"]
    return attempt


def _task_result(task, attempt, task_file, send_after_generate, send_result):
    result = {
        "task_id": task["task_id"],
        "task_file": task_file,
        "attempt_no": attempt["attempt_no"],
        "image_file": attempt["image_file"],
        "gcode_file": attempt["gcode_file"],
        "params": attempt["params"],
        "recommendation": task.get("recommendation"),
        "send_after_generate": bool(send_after_generate),
        "confirmation_required": bool(send_result.get("confirmation_required")),
        "send_result": send_result,
        "next_step": "观察实物效果后，可用 refine_laser_params_from_feedback_tool 描述太焦、太浅、切不透等问题。",
    }
    if attempt.get("auto_trim"):
        result["auto_trim"] = attempt["auto_trim"]
    if attempt.get("placement"):
        result["placement"] = attempt["placement"]
    if attempt.get("text_layout"):
        result["text_layout"] = attempt["text_layout"]
    if attempt.get("time_estimate"):
        result["time_estimate"] = attempt["time_estimate"]
    result["speech"] = _build_text_task_speech(task, attempt, send_result)
    return result


def generate_text_laser_task(
    text,
    material,
    thickness_mm,
    laser_mode="engrave",
    engraving_mode="",
    width_mm=0.0,
    height_mm=0.0,
    image_width=text_image_tool.DEFAULT_IMAGE_WIDTH,
    image_height=text_image_tool.DEFAULT_IMAGE_HEIGHT,
    font_size=text_image_tool.DEFAULT_FONT_SIZE,
    font_path="",
    auto_wrap=text_image_tool.DEFAULT_AUTO_WRAP,
    max_lines=text_image_tool.DEFAULT_MAX_LINES,
    layout_mode=text_image_tool.DEFAULT_LAYOUT_MODE,
    image_output_file="",
    gcode_output_file="",
    laser_min_power=None,
    laser_max_power=None,
    power_percent=None,
    feed_rate=None,
    travel_rate=None,
    pixel_size_mm=None,
    threshold=None,
    passes=None,
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
    send_after_generate=False,
    confirmed=False,
    dry_run=False,
    connection_mode="",
    port="",
    baudrate=laser_grbl_tool.DEFAULT_BAUDRATE,
    wait_for_response=True,
    run_in_background=True,
    network_host="",
    network_transport=laser_material_calibration_tool.DEFAULT_NETWORK_TRANSPORT,
    network_http_port=laser_network_grbl_tool.DEFAULT_HTTP_PORT,
    network_telnet_port=laser_network_grbl_tool.DEFAULT_TELNET_PORT,
    network_timeout=laser_network_grbl_tool.DEFAULT_TIMEOUT,
    params_file=laser_material_calibration_tool.MATERIAL_PARAMS_FILE,
    tasks_dir=None,
    prepared_gcode_dir=None,
    reuse_prepared_gcode=True,
):
    text = str(text or "").strip()
    if not text:
        return _build_failure("text 不能为空")

    resolved_connection_mode, error = laser_execution.resolve_laser_connection_mode(connection_mode)
    if error:
        return _build_failure(error)

    recommendation = laser_material_calibration_tool.recommend_laser_params(
        material, thickness_mm, laser_mode, engraving_mode, params_file=params_file
    )
    if not recommendation.get("success"):
        return recommendation

    if reuse_prepared_gcode:
        prepared_lookup = _find_prepared_gcode(text, prepared_gcode_dir=prepared_gcode_dir)
        if prepared_lookup.get("status") == "error":
            return _build_failure(prepared_lookup["result"], prepared_lookup)
        if prepared_lookup.get("status") == "ambiguous":
            return _ambiguous_prepared_file_result(text, prepared_lookup)
        if prepared_lookup.get("status") == "matched":
            send_result = _send_prepared_gcode_file(
                prepared_lookup["file"]["path"],
                send_after_generate=send_after_generate,
                confirmed=confirmed,
                dry_run=dry_run,
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
                _prepared_file_result(text, prepared_lookup, send_after_generate, send_result)
            )

    rec = dict(recommendation["result"])
    rec_match = str(rec.get("match") or "").strip().lower()
    rec_mode = str(rec.get("laser_mode") or laser_mode or "").strip().lower()
    rec_warnings = rec.get("warnings") if isinstance(rec.get("warnings"), list) else []
    rec["warnings"] = [str(item) for item in rec_warnings if str(item).strip()]
    if rec.get("can_send") is None:
        rec["can_send"] = bool(
            rec_match in {"exact", "nearest_thickness"}
            and not (rec_mode == "cut" and rec_match != "exact")
        )
    if rec.get("can_send"):
        rec["send_blocked_reason"] = ""
    else:
        rec["send_blocked_reason"] = str(rec.get("send_blocked_reason") or "").strip() or (
            "切割必须精确匹配材料厚度，当前推荐参数不可发送。"
            if rec_mode == "cut"
            else "当前材料参数未达到可发送状态，请先完成校准。"
        )
    params, error = _apply_overrides(
        rec["params"],
        laser_min_power=laser_min_power,
        laser_max_power=laser_max_power,
        power_percent=power_percent,
        feed_rate=feed_rate,
        travel_rate=travel_rate,
        pixel_size_mm=pixel_size_mm,
        threshold=threshold,
        passes=passes,
    )
    if error:
        return _build_failure(error)

    task_id = uuid.uuid4().hex
    generated, error_result = _generate_attempt_files(
        task_id,
        1,
        text,
        params,
        rec["laser_mode"],
        engraving_mode,
        width_mm,
        height_mm,
        image_width,
        image_height,
        font_size,
        font_path,
        auto_wrap,
        max_lines,
        layout_mode,
        invert,
        bidirectional,
        overwrite,
        overscan_mm=overscan_mm,
        raster_scan_direction=raster_scan_direction,
        auto_trim=auto_trim,
        trim_tolerance=trim_tolerance,
        auto_size=auto_size,
        dpi=dpi,
        lock_aspect_ratio=lock_aspect_ratio,
        offset_x_mm=offset_x_mm,
        offset_y_mm=offset_y_mm,
        safe_margin_mm=safe_margin_mm,
        image_output_file=image_output_file,
        gcode_output_file=gcode_output_file,
        tasks_dir=tasks_dir,
    )
    if error_result:
        return error_result

    source = "material_recommendation"
    if _has_manual_overrides(
        laser_min_power=laser_min_power,
        laser_max_power=laser_max_power,
        power_percent=power_percent,
        feed_rate=feed_rate,
        travel_rate=travel_rate,
        pixel_size_mm=pixel_size_mm,
        threshold=threshold,
        passes=passes,
    ):
        source = "material_recommendation_with_override"

    attempt = _build_attempt(1, params, generated, source=source)
    actual_engraving_mode = (generated.get("gcode") or {}).get("engraving_mode") or engraving_mode or "raster"
    task = {
        "task_id": task_id,
        "created_at": time.time(),
        "updated_at": time.time(),
        "text": text,
        "material": rec["material"],
        "requested_material": rec["requested_material"],
        "thickness_mm": rec["thickness_mm"],
        "matched_thickness_mm": rec["matched_thickness_mm"],
        "laser_mode": rec["laser_mode"],
        "dimensions": {
            "width_mm": width_mm,
            "height_mm": height_mm,
            "image_width": image_width,
            "image_height": image_height,
            "offset_x_mm": offset_x_mm,
            "offset_y_mm": offset_y_mm,
            "safe_margin_mm": safe_margin_mm,
        },
        "font": {"font_size": font_size, "font_path": font_path},
        "generation_options": {
            "invert": invert,
            "bidirectional": bidirectional,
            "overscan_mm": overscan_mm,
            "raster_scan_direction": raster_scan_direction,
            "overwrite": overwrite,
            "engraving_mode": actual_engraving_mode,
            "auto_wrap": bool(auto_wrap),
            "max_lines": int(max_lines or 0),
            "layout_mode": layout_mode,
            "auto_trim": auto_trim,
            "trim_tolerance": trim_tolerance,
            "auto_size": auto_size,
            "dpi": dpi,
            "lock_aspect_ratio": lock_aspect_ratio,
        },
        "recommendation": rec,
        "attempts": [attempt],
        "feedback_events": [],
    }
    send_result = _send_generated_attempt(
        task_id,
        attempt,
        send_after_generate=send_after_generate,
        confirmed=confirmed,
        dry_run=dry_run,
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
    attempt["send_result"] = send_result
    task_file = _save_text_task(task, tasks_dir=tasks_dir)
    return _build_success(_task_result(task, attempt, task_file, send_after_generate, send_result))


def refine_laser_params_from_feedback(
    feedback_text,
    task_id="",
    laser_min_power=None,
    laser_max_power=None,
    power_percent=None,
    feed_rate=None,
    travel_rate=None,
    pixel_size_mm=None,
    threshold=None,
    passes=None,
    tasks_dir=None,
):
    rule, error = _match_feedback_rule(feedback_text)
    if error:
        return _build_failure(error)

    task = None
    if task_id:
        task, error = _load_text_task(task_id, tasks_dir=tasks_dir)
        if error:
            return _build_failure(error)
        attempts = task.get("attempts", [])
        if not attempts:
            return _build_failure(f"文字激光任务没有可用 attempt: {task_id}")
        base_params = attempts[-1].get("params", {})
    else:
        base_params = {}

    base_params, error = _apply_overrides(
        base_params,
        laser_min_power=laser_min_power,
        laser_max_power=laser_max_power,
        power_percent=power_percent,
        feed_rate=feed_rate,
        travel_rate=travel_rate,
        pixel_size_mm=pixel_size_mm,
        threshold=threshold,
        passes=passes,
    )
    if error:
        return _build_failure(error)

    if not rule:
        result = {
            "task_id": task_id,
            "feedback_text": feedback_text,
            "diagnosis": "未匹配到内置反馈规则，请补充描述太焦、太浅、切不透、毛边或断线。",
            "matched_issue": "unknown",
            "recommended_strategy": None,
            "suggestions": [],
        }
        if task:
            event = {
                **result,
                "created_at": time.time(),
            }
            task.setdefault("feedback_events", []).append(event)
            result["feedback_event_index"] = len(task["feedback_events"]) - 1
            result["task_file"] = _save_text_task(task, tasks_dir=tasks_dir)
        return _build_success(result)

    suggestions, error = _build_feedback_suggestions(rule, base_params)
    if error:
        return _build_failure(error)

    result = {
        "task_id": task_id,
        "feedback_text": feedback_text,
        "diagnosis": rule["diagnosis"],
        "matched_issue": rule["issue"],
        "recommended_strategy": rule["recommended_strategy"],
        "suggestions": suggestions,
    }
    if task:
        event = {
            **result,
            "created_at": time.time(),
        }
        task.setdefault("feedback_events", []).append(event)
        result["feedback_event_index"] = len(task["feedback_events"]) - 1
        result["task_file"] = _save_text_task(task, tasks_dir=tasks_dir)
    return _build_success(result)


def _latest_recommended_strategy(task):
    for event in reversed(task.get("feedback_events", [])):
        strategy = event.get("recommended_strategy")
        if strategy:
            return strategy, event
    return None, None


def _latest_feedback_event_for_strategy(task, strategy):
    for event in reversed(task.get("feedback_events", [])):
        for suggestion in event.get("suggestions", []):
            if suggestion.get("strategy") == strategy:
                return event
    return None


def _coalesce(value, fallback):
    return fallback if value is None else value


def regenerate_text_laser_task(
    task_id,
    strategy="recommended",
    feedback_text="",
    engraving_mode=None,
    width_mm=None,
    height_mm=None,
    image_width=None,
    image_height=None,
    font_size=None,
    font_path=None,
    auto_wrap=None,
    max_lines=None,
    layout_mode=None,
    image_output_file="",
    gcode_output_file="",
    laser_min_power=None,
    laser_max_power=None,
    power_percent=None,
    feed_rate=None,
    travel_rate=None,
    pixel_size_mm=None,
    threshold=None,
    passes=None,
    invert=None,
    bidirectional=None,
    overscan_mm=None,
    raster_scan_direction=None,
    overwrite=True,
    auto_trim=None,
    trim_tolerance=None,
    auto_size=None,
    dpi=None,
    lock_aspect_ratio=None,
    offset_x_mm=None,
    offset_y_mm=None,
    safe_margin_mm=None,
    send_after_generate=False,
    confirmed=False,
    dry_run=False,
    connection_mode="",
    port="",
    baudrate=laser_grbl_tool.DEFAULT_BAUDRATE,
    wait_for_response=True,
    run_in_background=True,
    network_host="",
    network_transport=laser_material_calibration_tool.DEFAULT_NETWORK_TRANSPORT,
    network_http_port=laser_network_grbl_tool.DEFAULT_HTTP_PORT,
    network_telnet_port=laser_network_grbl_tool.DEFAULT_TELNET_PORT,
    network_timeout=laser_network_grbl_tool.DEFAULT_TIMEOUT,
    tasks_dir=None,
):
    resolved_connection_mode, error = laser_execution.resolve_laser_connection_mode(connection_mode)
    if error:
        return _build_failure(error)

    task, error = _load_text_task(task_id, tasks_dir=tasks_dir)
    if error:
        return _build_failure(error)
    attempts = task.get("attempts", [])
    if not attempts:
        return _build_failure(f"文字激光任务没有可用 attempt: {task_id}")

    selected_strategy = str(strategy or "").strip()
    manual = _has_manual_overrides(
        laser_min_power=laser_min_power,
        laser_max_power=laser_max_power,
        power_percent=power_percent,
        feed_rate=feed_rate,
        travel_rate=travel_rate,
        pixel_size_mm=pixel_size_mm,
        threshold=threshold,
        passes=passes,
    )
    generation_options = task.get("generation_options", {})
    resolved_engraving_mode = _coalesce(engraving_mode, generation_options.get("engraving_mode", ""))
    previous_engraving_mode = generation_options.get("engraving_mode", "")
    params = attempts[-1].get("params", {})
    source = "feedback_strategy"
    if (
        resolved_engraving_mode
        and resolved_engraving_mode != previous_engraving_mode
        and not manual
    ):
        recommendation = laser_material_calibration_tool.recommend_laser_params(
            task.get("material", ""),
            task.get("thickness_mm", 0.0),
            task.get("laser_mode", "engrave"),
            resolved_engraving_mode,
        )
        if not recommendation.get("success"):
            return recommendation
        params = recommendation["result"]["params"]
    latest_event = None

    if selected_strategy == "recommended":
        selected_strategy, latest_event = _latest_recommended_strategy(task)
        if not selected_strategy:
            return _build_failure("没有可用的反馈建议，strategy='recommended' 需要先调用 refine_laser_params_from_feedback_tool")
    elif selected_strategy:
        latest_event = _latest_feedback_event_for_strategy(task, selected_strategy)

    if selected_strategy and selected_strategy != "manual_override":
        issue = (latest_event or {}).get("matched_issue")
        params, error = _apply_strategy(params, selected_strategy, issue=issue)
        if error:
            return _build_failure(error)
    elif not manual:
        return _build_failure("请指定 strategy 或手动覆盖参数")

    if manual:
        params, error = _apply_overrides(
            params,
            laser_min_power=laser_min_power,
            laser_max_power=laser_max_power,
            power_percent=power_percent,
            feed_rate=feed_rate,
            travel_rate=travel_rate,
            pixel_size_mm=pixel_size_mm,
            threshold=threshold,
            passes=passes,
        )
        if error:
            return _build_failure(error)
        source = "manual_override"

    attempt_no = len(attempts) + 1
    dimensions = task.get("dimensions", {})
    font = task.get("font", {})
    generated, error_result = _generate_attempt_files(
        task["task_id"],
        attempt_no,
        task["text"],
        params,
        task.get("laser_mode", "engrave"),
        resolved_engraving_mode,
        _coalesce(width_mm, dimensions.get("width_mm", 0.0)),
        _coalesce(height_mm, dimensions.get("height_mm", 0.0)),
        _coalesce(image_width, dimensions.get("image_width", text_image_tool.DEFAULT_IMAGE_WIDTH)),
        _coalesce(image_height, dimensions.get("image_height", text_image_tool.DEFAULT_IMAGE_HEIGHT)),
        _coalesce(font_size, font.get("font_size", text_image_tool.DEFAULT_FONT_SIZE)),
        _coalesce(font_path, font.get("font_path", "")),
        _coalesce(auto_wrap, generation_options.get("auto_wrap", text_image_tool.DEFAULT_AUTO_WRAP)),
        _coalesce(max_lines, generation_options.get("max_lines", text_image_tool.DEFAULT_MAX_LINES)),
        _coalesce(layout_mode, generation_options.get("layout_mode", text_image_tool.DEFAULT_LAYOUT_MODE)),
        _coalesce(invert, generation_options.get("invert", False)),
        _coalesce(bidirectional, generation_options.get("bidirectional", False)),
        overwrite,
        overscan_mm=_coalesce(overscan_mm, generation_options.get("overscan_mm", laser_grbl_tool.DEFAULT_RASTER_OVERSCAN_MM)),
        raster_scan_direction=_coalesce(raster_scan_direction, generation_options.get("raster_scan_direction", "auto")),
        auto_trim=_coalesce(auto_trim, generation_options.get("auto_trim", True)),
        trim_tolerance=_coalesce(trim_tolerance, generation_options.get("trim_tolerance", 20)),
        auto_size=_coalesce(auto_size, generation_options.get("auto_size", True)),
        dpi=_coalesce(dpi, generation_options.get("dpi", 300.0)),
        lock_aspect_ratio=_coalesce(lock_aspect_ratio, generation_options.get("lock_aspect_ratio", True)),
        offset_x_mm=_coalesce(offset_x_mm, dimensions.get("offset_x_mm", 0.0)),
        offset_y_mm=_coalesce(offset_y_mm, dimensions.get("offset_y_mm", 0.0)),
        safe_margin_mm=_coalesce(safe_margin_mm, dimensions.get("safe_margin_mm", laser_grbl_tool.DEFAULT_SAFE_MARGIN_MM)),
        image_output_file=image_output_file,
        gcode_output_file=gcode_output_file,
        tasks_dir=tasks_dir,
    )
    if error_result:
        return error_result

    attempt = _build_attempt(
        attempt_no,
        params,
        generated,
        source=source,
        selected_strategy=selected_strategy,
        feedback_text=feedback_text or (latest_event or {}).get("feedback_text", ""),
    )
    send_result = _send_generated_attempt(
        task["task_id"],
        attempt,
        send_after_generate=send_after_generate,
        confirmed=confirmed,
        dry_run=dry_run,
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
    attempt["send_result"] = send_result
    task.setdefault("attempts", []).append(attempt)
    task.setdefault("generation_options", {})["engraving_mode"] = resolved_engraving_mode
    task["generation_options"].update(
        {
            "auto_trim": _coalesce(auto_trim, generation_options.get("auto_trim", True)),
            "trim_tolerance": _coalesce(trim_tolerance, generation_options.get("trim_tolerance", 20)),
            "overscan_mm": _coalesce(overscan_mm, generation_options.get("overscan_mm", laser_grbl_tool.DEFAULT_RASTER_OVERSCAN_MM)),
            "raster_scan_direction": _coalesce(raster_scan_direction, generation_options.get("raster_scan_direction", "auto")),
            "auto_wrap": bool(_coalesce(auto_wrap, generation_options.get("auto_wrap", text_image_tool.DEFAULT_AUTO_WRAP))),
            "max_lines": int(_coalesce(max_lines, generation_options.get("max_lines", text_image_tool.DEFAULT_MAX_LINES)) or 0),
            "layout_mode": _coalesce(layout_mode, generation_options.get("layout_mode", text_image_tool.DEFAULT_LAYOUT_MODE)),
            "auto_size": _coalesce(auto_size, generation_options.get("auto_size", True)),
            "dpi": _coalesce(dpi, generation_options.get("dpi", 300.0)),
            "lock_aspect_ratio": _coalesce(lock_aspect_ratio, generation_options.get("lock_aspect_ratio", True)),
        }
    )
    task.setdefault("dimensions", {}).update(
        {
            "width_mm": _coalesce(width_mm, dimensions.get("width_mm", 0.0)),
            "height_mm": _coalesce(height_mm, dimensions.get("height_mm", 0.0)),
            "image_width": _coalesce(image_width, dimensions.get("image_width", text_image_tool.DEFAULT_IMAGE_WIDTH)),
            "image_height": _coalesce(image_height, dimensions.get("image_height", text_image_tool.DEFAULT_IMAGE_HEIGHT)),
            "offset_x_mm": _coalesce(offset_x_mm, dimensions.get("offset_x_mm", 0.0)),
            "offset_y_mm": _coalesce(offset_y_mm, dimensions.get("offset_y_mm", 0.0)),
            "safe_margin_mm": _coalesce(safe_margin_mm, dimensions.get("safe_margin_mm", laser_grbl_tool.DEFAULT_SAFE_MARGIN_MM)),
        }
    )
    task_file = _save_text_task(task, tasks_dir=tasks_dir)
    return _build_success(_task_result(task, attempt, task_file, send_after_generate, send_result))


def register_tool(mcp):
    @mcp.tool()
    def generate_text_laser_task_tool(
        text: str,
        material: str,
        thickness_mm: float,
        laser_mode: str = "engrave",
        engraving_mode: str = "",
        width_mm: float = 0.0,
        height_mm: float = 0.0,
        image_width: int = text_image_tool.DEFAULT_IMAGE_WIDTH,
        image_height: int = text_image_tool.DEFAULT_IMAGE_HEIGHT,
        font_size: int = text_image_tool.DEFAULT_FONT_SIZE,
        font_path: str = "",
        auto_wrap: bool = text_image_tool.DEFAULT_AUTO_WRAP,
        max_lines: int = text_image_tool.DEFAULT_MAX_LINES,
        layout_mode: str = text_image_tool.DEFAULT_LAYOUT_MODE,
        image_output_file: str = "",
        gcode_output_file: str = "",
        laser_min_power: int = None,
        laser_max_power: int = None,
        power_percent: float = None,
        feed_rate: int = None,
        travel_rate: int = None,
        pixel_size_mm: float = None,
        threshold: int = None,
        passes: int = None,
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
        send_after_generate: bool = False,
        confirmed: bool = False,
        dry_run: bool = False,
        connection_mode: str = "",
        port: str = "",
        baudrate: int = laser_grbl_tool.DEFAULT_BAUDRATE,
        wait_for_response: bool = True,
        run_in_background: bool = True,
        network_host: str = "",
        network_transport: str = laser_material_calibration_tool.DEFAULT_NETWORK_TRANSPORT,
        network_http_port: int = laser_network_grbl_tool.DEFAULT_HTTP_PORT,
        network_telnet_port: int = laser_network_grbl_tool.DEFAULT_TELNET_PORT,
        network_timeout: float = laser_network_grbl_tool.DEFAULT_TIMEOUT,
        prepared_gcode_dir: str = "",
        reuse_prepared_gcode: bool = True,
    ) -> dict:
        """生成文字激光任务：可传 auto_wrap/max_lines 控制文字排版；默认不发送到机器。"""
        return generate_text_laser_task(**locals())

    @mcp.tool()
    def refine_laser_params_from_feedback_tool(
        feedback_text: str,
        task_id: str = "",
        laser_min_power: int = None,
        laser_max_power: int = None,
        power_percent: float = None,
        feed_rate: int = None,
        travel_rate: int = None,
        pixel_size_mm: float = None,
        threshold: int = None,
        passes: int = None,
    ) -> dict:
        """根据“太焦、太浅、切不透、毛边、断线”等反馈给出有界调参建议，不生成文件、不发送硬件。"""
        return refine_laser_params_from_feedback(**locals())

    @mcp.tool()
    def regenerate_text_laser_task_tool(
        task_id: str,
        strategy: str = "recommended",
        feedback_text: str = "",
        engraving_mode: str = None,
        width_mm: float = None,
        height_mm: float = None,
        image_width: int = None,
        image_height: int = None,
        font_size: int = None,
        font_path: str = None,
        auto_wrap: bool = None,
        max_lines: int = None,
        layout_mode: str = None,
        image_output_file: str = "",
        gcode_output_file: str = "",
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
        overwrite: bool = True,
        auto_trim: bool = None,
        trim_tolerance: float = None,
        auto_size: bool = None,
        dpi: float = None,
        lock_aspect_ratio: bool = None,
        offset_x_mm: float = None,
        offset_y_mm: float = None,
        safe_margin_mm: float = None,
        send_after_generate: bool = False,
        confirmed: bool = False,
        dry_run: bool = False,
        connection_mode: str = "",
        port: str = "",
        baudrate: int = laser_grbl_tool.DEFAULT_BAUDRATE,
        wait_for_response: bool = True,
        run_in_background: bool = True,
        network_host: str = "",
        network_transport: str = laser_material_calibration_tool.DEFAULT_NETWORK_TRANSPORT,
        network_http_port: int = laser_network_grbl_tool.DEFAULT_HTTP_PORT,
        network_telnet_port: int = laser_network_grbl_tool.DEFAULT_TELNET_PORT,
        network_timeout: float = laser_network_grbl_tool.DEFAULT_TIMEOUT,
    ) -> dict:
        """基于已保存 task_id 和反馈策略/手动覆盖参数重新生成一次文字激光 G-code，默认不发送。"""
        return regenerate_text_laser_task(**locals())
