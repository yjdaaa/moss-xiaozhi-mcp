import socket
import time
import urllib.error
import urllib.parse
import urllib.request

from core import laser_execution
from core.laser_runtime.config import get_laser_settings
from tools import laser_grbl_tool


_SETTINGS = get_laser_settings()
DEFAULT_NETWORK_HOST = _SETTINGS.network_host
DEFAULT_HTTP_PORT = _SETTINGS.network_http_port
DEFAULT_TELNET_PORT = _SETTINGS.network_telnet_port
DEFAULT_TIMEOUT = _SETTINGS.network_timeout
# Complete network full-file jobs always force Telnet; exposed for connection-check defaults.
FORCED_FILE_TRANSPORT = "telnet"

READ_ONLY_COMMANDS = {
    "?",
    "$$",
    "$+",
    "$G",
    "$#",
    "$I",
    "$N",
    "$CMD",
    "$A",
    "$E",
    "[ESP0]",
    "[ESP111]",
    "[ESP200]",
    "[ESP210]",
    "[ESP400]",
    "[ESP420]",
    "[ESP720]",
    "[ESP800]",
}
TRANSPORT_ALIASES = {
    "http": "http",
    "web": "http",
    "webui": "http",
    "网络": "http",
    "telnet": "telnet",
    "tcp": "telnet",
}


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


def _resolve_transport(transport):
    normalized = str(transport or "http").strip().lower()
    resolved = TRANSPORT_ALIASES.get(normalized)
    if not resolved:
        return None, "transport 不支持，支持: http / telnet"
    return resolved, None


def _validate_port(value, name):
    number, error = laser_grbl_tool._validate_int(value, name, 1, 65535)
    return number, error


def _validate_timeout(timeout):
    number, error = laser_grbl_tool._validate_number(timeout, "timeout", 0)
    return number, error


def _strip_inline_comment(command):
    command = str(command or "").strip()
    if not command:
        return ""
    if command.startswith(";"):
        return ""
    return command.split(";", 1)[0].strip()


def classify_laser_command(command):
    command = _strip_inline_comment(command)
    if not command:
        return "empty"

    upper = command.upper()
    first = upper.split()[0]
    if upper in READ_ONLY_COMMANDS:
        return "read_only"
    if upper in ("~", "!", "\x18"):
        return "destructive"
    if upper.startswith("[ESP710]FORMAT") or upper.startswith("[ESP444]RESTART"):
        return "destructive"
    if upper.startswith("[ESP220]") or upper.startswith("[ESP700]"):
        return "file_run"
    if upper.startswith("[ESP215]"):
        return "destructive"
    if upper.startswith("[ESP"):
        return "config_write"
    if upper.startswith("$RST") or upper.startswith("$NVX"):
        return "destructive"
    if upper.startswith("$J=") or upper in ("$H", "$X", "$C"):
        return "motion"
    if upper.startswith("$") and "=" in upper:
        return "config_write"
    if first in ("G0", "G00", "G1", "G01", "G2", "G02", "G3", "G03", "G92"):
        return "motion"
    if first in ("M3", "M03", "M4", "M04", "M5", "M05"):
        return "laser"
    return "unknown"


def _requires_confirmation(classification, manual_mode=False):
    if classification in ("empty", "read_only"):
        return False
    if classification == "unknown" and manual_mode:
        return True
    return True


def _mask_command(command):
    text = str(command or "")
    for key in ("password=", "token=", "pwd=", "pass="):
        lower = text.lower()
        index = lower.find(key)
        if index >= 0:
            end = text.find("&", index)
            if end < 0:
                end = len(text)
            text = text[: index + len(key)] + "***" + text[end:]
    return text


def _build_http_command_url(host, command, port=DEFAULT_HTTP_PORT):
    encoded = urllib.parse.quote(command, safe="")
    return f"http://{host}:{port}/command?commandText={encoded}"


def query_web_command(host, command, port=DEFAULT_HTTP_PORT, timeout=DEFAULT_TIMEOUT, opener=None):
    host, error = laser_execution.resolve_laser_network_host(host)
    if error:
        return _build_failure(error)
    port, error = _validate_port(port, "http_port")
    if error:
        return _build_failure(error)
    timeout, error = _validate_timeout(timeout)
    if error:
        return _build_failure(error)

    url = _build_http_command_url(host, command, port)
    try:
        urlopen = opener or urllib.request.urlopen
        with urlopen(url, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return _build_failure(
            f"HTTP 命令失败: {exc.code}",
            {"transport": "http", "command": _mask_command(command), "url": url},
        )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return _build_failure(
            f"HTTP 命令异常: {exc}",
            {"transport": "http", "command": _mask_command(command), "url": url},
        )

    detail = {
        "transport": "http",
        "command": _mask_command(command),
        "url": url,
        "response": body,
    }
    if body.strip().lower().startswith("error"):
        return _build_failure(f"设备返回错误: {body.strip()}", detail)
    return _build_success(detail)


def _read_telnet_response(sock, timeout):
    deadline = time.time() + timeout
    chunks = []
    while time.time() < deadline:
        try:
            data = sock.recv(4096)
        except socket.timeout:
            continue
        if not data:
            break
        text = data.decode("utf-8", errors="replace")
        chunks.append(text)
        lowered = text.lower()
        if "ok" in lowered or "error" in lowered:
            break
    return "".join(chunks).strip()


def query_telnet_command(
    host,
    command,
    port=DEFAULT_TELNET_PORT,
    timeout=DEFAULT_TIMEOUT,
    socket_factory=socket.create_connection,
):
    host, error = laser_execution.resolve_laser_network_host(host)
    if error:
        return _build_failure(error)
    port, error = _validate_port(port, "telnet_port")
    if error:
        return _build_failure(error)
    timeout, error = _validate_timeout(timeout)
    if error:
        return _build_failure(error)

    try:
        with socket_factory((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall((command.strip() + "\n").encode("utf-8"))
            response = _read_telnet_response(sock, timeout)
    except (TimeoutError, OSError, socket.timeout) as exc:
        return _build_failure(
            f"Telnet 命令异常: {exc}",
            {"transport": "telnet", "command": _mask_command(command), "host": host, "port": port},
        )

    detail = {
        "transport": "telnet",
        "command": _mask_command(command),
        "host": host,
        "port": port,
        "response": response,
    }
    if response.lower().startswith("error"):
        return _build_failure(f"设备返回错误: {response}", detail)
    return _build_success(detail)


def send_network_command(
    command,
    host="",
    transport="http",
    http_port=DEFAULT_HTTP_PORT,
    telnet_port=DEFAULT_TELNET_PORT,
    timeout=DEFAULT_TIMEOUT,
    confirmed=False,
    dry_run=False,
    manual_mode=True,
):
    command = _strip_inline_comment(command)
    if not command:
        return _build_failure("请指定要发送的 GRBL/G-code 命令")
    host, error = laser_execution.resolve_laser_network_host(host)
    if error:
        return _build_failure(error)
    transport, error = _resolve_transport(transport)
    if error:
        return _build_failure(error)

    classification = classify_laser_command(command)
    detail = {
        "transport": transport,
        "host": host,
        "command": _mask_command(command),
        "classification": classification,
        "confirmation_required": _requires_confirmation(classification, manual_mode=manual_mode),
    }
    if detail["confirmation_required"] and not confirmed:
        return _build_failure("该网络命令会影响机器状态，必须 confirmed=true 后才能发送", detail)
    if dry_run:
        if transport == "http":
            detail["url"] = _build_http_command_url(host, command, http_port)
        return _build_success(detail)

    if transport == "http":
        result = query_web_command(host, command, port=http_port, timeout=timeout)
    else:
        result = query_telnet_command(host, command, port=telnet_port, timeout=timeout)
    if isinstance(result.get("result"), dict):
        result["result"]["classification"] = classification
        result["result"]["confirmation_required"] = detail["confirmation_required"]
    return result


def _prepare_network_send_file(
    gcode_file="",
    image_file="",
    output_file="",
    width_mm=0.0,
    height_mm=0.0,
    pixel_size_mm=0.1,
    feed_rate=1200,
    travel_rate=3000,
    laser_min_power=0,
    laser_max_power=800,
    threshold=-1,
    invert=False,
    bidirectional=False,
    overscan_mm=laser_grbl_tool.DEFAULT_RASTER_OVERSCAN_MM,
    raster_scan_direction="auto",
    overwrite=True,
    laser_mode="engrave",
    engraving_mode="",
    auto_trim=True,
    trim_tolerance=20,
    auto_size=True,
    dpi=300.0,
    lock_aspect_ratio=True,
    offset_x_mm=0.0,
    offset_y_mm=0.0,
    safe_margin_mm=laser_grbl_tool.DEFAULT_SAFE_MARGIN_MM,
):
    return laser_grbl_tool.prepare_gcode_file_for_sending(
        gcode_file=gcode_file,
        image_file=image_file,
        output_file=output_file,
        width_mm=width_mm,
        height_mm=height_mm,
        pixel_size_mm=pixel_size_mm,
        feed_rate=feed_rate,
        travel_rate=travel_rate,
        laser_min_power=laser_min_power,
        laser_max_power=laser_max_power,
        threshold=threshold,
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


def send_network_file(
    host="",
    transport="http",
    http_port=DEFAULT_HTTP_PORT,
    telnet_port=DEFAULT_TELNET_PORT,
    timeout=DEFAULT_TIMEOUT,
    gcode_file="",
    image_file="",
    output_file="",
    width_mm=0.0,
    height_mm=0.0,
    pixel_size_mm=0.1,
    feed_rate=1200,
    travel_rate=3000,
    laser_min_power=0,
    laser_max_power=800,
    threshold=-1,
    invert=False,
    bidirectional=False,
    overscan_mm=laser_grbl_tool.DEFAULT_RASTER_OVERSCAN_MM,
    raster_scan_direction="auto",
    overwrite=True,
    laser_mode="engrave",
    engraving_mode="",
    auto_trim=True,
    trim_tolerance=20,
    auto_size=True,
    dpi=300.0,
    lock_aspect_ratio=True,
    offset_x_mm=0.0,
    offset_y_mm=0.0,
    safe_margin_mm=laser_grbl_tool.DEFAULT_SAFE_MARGIN_MM,
    wait_for_response=True,
    run_in_background=True,
    confirmed=False,
    dry_run=False,
):
    host, error = laser_execution.resolve_laser_network_host(host)
    if error:
        return _build_failure(error)
    requested_transport = transport

    prepared = _prepare_network_send_file(
        gcode_file=gcode_file,
        image_file=image_file,
        output_file=output_file,
        width_mm=width_mm,
        height_mm=height_mm,
        pixel_size_mm=pixel_size_mm,
        feed_rate=feed_rate,
        travel_rate=travel_rate,
        laser_min_power=laser_min_power,
        laser_max_power=laser_max_power,
        threshold=threshold,
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
    if not prepared.get("success"):
        return prepared
    prepared_result = prepared["result"]

    if dry_run or not confirmed:
        preview_result = laser_execution.send_file(
            prepared_result,
            "network",
            confirmed=False,
            dry_run=dry_run,
            host=host,
            transport=requested_transport,
        )
        return preview_result

    send_result = laser_execution.send_file(
        prepared_result,
        "network",
        confirmed=True,
        run_in_background=run_in_background,
        host=host,
        transport=requested_transport,
        http_port=http_port,
        telnet_port=telnet_port,
        timeout=timeout,
        wait_for_response=wait_for_response,
    )
    preview = {
        "transport": FORCED_FILE_TRANSPORT,
        "requested_transport": requested_transport,
        "transport_policy": "complete network laser jobs always use telnet",
        "host": host,
        "prepared": prepared_result,
        "confirmation_required": False,
        "send_result": send_result,
    }
    if not send_result.get("success"):
        return _build_failure(send_result.get("result") or "网络雕刻文件发送失败", preview)
    return _build_success(preview)


def register_tool(mcp):
    @mcp.tool()
    def laser_network_grbl_tool(
        action: str,
        host: str = "",
        transport: str = "http",
        http_port: int = DEFAULT_HTTP_PORT,
        telnet_port: int = DEFAULT_TELNET_PORT,
        timeout: float = DEFAULT_TIMEOUT,
        command: str = "",
        gcode_file: str = "",
        image_file: str = "",
        output_file: str = "",
        width_mm: float = 0.0,
        height_mm: float = 0.0,
        pixel_size_mm: float = 0.1,
        feed_rate: int = 1200,
        travel_rate: int = 3000,
        laser_min_power: int = 0,
        laser_max_power: int = 800,
        threshold: int = -1,
        invert: bool = False,
        bidirectional: bool = False,
        overscan_mm: float = laser_grbl_tool.DEFAULT_RASTER_OVERSCAN_MM,
        raster_scan_direction: str = "auto",
        overwrite: bool = True,
        laser_mode: str = "engrave",
        engraving_mode: str = "",
        auto_trim: bool = True,
        trim_tolerance: float = 20,
        auto_size: bool = True,
        dpi: float = 300.0,
        lock_aspect_ratio: bool = True,
        offset_x_mm: float = 0.0,
        offset_y_mm: float = 0.0,
        safe_margin_mm: float = laser_grbl_tool.DEFAULT_SAFE_MARGIN_MM,
        wait_for_response: bool = True,
        run_in_background: bool = True,
        confirmed: bool = False,
        dry_run: bool = False,
        job_id: str = "",
    ) -> dict:
        """
        通过 Grbl_ESP32/ESP3D 网络通道控制激光雕刻机。
        不要因为用户说"帮我刻 xxx"调用本工具 send_file；文字内容必须先用 generate_text_laser_task_tool 生成任务草稿。
        完整文件发送/状态/取消强制委托 core.laser_execution；后台 worker 仅由 core._laser_execution_backend 提供。

        action:
            send_command - 通过 HTTP /command 或 Telnet 发送单条命令。只读命令可直接发送；运动、激光、配置、文件运行等必须 confirmed=true。
            send_file - 只用于用户明确发送已有 G-code/图片文件或明确使用默认雕刻文件；必须 confirmed=true，且发送前会先用只读命令检查设备在线。
            job_status - 查询后台网络雕刻任务，需要 job_id。
            cancel_job - 取消后台网络雕刻任务，需要 job_id。

        transport: http 使用 /command?commandText=...；telnet 使用 TCP/Telnet GRBL 通道。
        host 必须显式提供或通过 LASER_NETWORK_HOST 配置，工具不会猜测真实设备地址。
        用户说"帮我刻 佳佳"时，佳佳是文字内容，不是文件名；必须先生成文字任务草稿，不要直接查找默认文件或发送旧文件。
        图片雕刻默认 raster/线扫填充；用户明确要求 outline/轮廓时传 engraving_mode='outline'。
        图片文件会默认自动裁边，按 DPI 或 dpi=300 换算实际雕刻尺寸，并限制在默认 90mm x 90mm 安全区内；offset_x_mm/offset_y_mm 控制摆放偏移。
        """
        try:
            normalized_action = str(action or "").strip().lower()
            if normalized_action in ("job_status", "status_job"):
                return laser_execution.job_status(job_id, "network")
            if normalized_action in ("cancel_job", "cancel", "stop_job"):
                return laser_execution.cancel_job(job_id, "network")
            if normalized_action in ("send_command", "command"):
                return send_network_command(
                    command=command,
                    host=host,
                    transport=transport,
                    http_port=http_port,
                    telnet_port=telnet_port,
                    timeout=timeout,
                    confirmed=confirmed,
                    dry_run=dry_run,
                    manual_mode=True,
                )
            if normalized_action == "send_file":
                return send_network_file(
                    host=host,
                    transport=transport,
                    http_port=http_port,
                    telnet_port=telnet_port,
                    timeout=timeout,
                    gcode_file=gcode_file,
                    image_file=image_file,
                    output_file=output_file,
                    width_mm=width_mm,
                    height_mm=height_mm,
                    pixel_size_mm=pixel_size_mm,
                    feed_rate=feed_rate,
                    travel_rate=travel_rate,
                    laser_min_power=laser_min_power,
                    laser_max_power=laser_max_power,
                    threshold=threshold,
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
                    wait_for_response=wait_for_response,
                    run_in_background=run_in_background,
                    confirmed=confirmed,
                    dry_run=dry_run,
                )
            return _build_failure(
                "未知 action，支持: send_command / send_file / job_status / cancel_job"
            )
        except Exception as exc:
            return _build_failure(f"网络 GRBL 工具执行失败: {exc}")
