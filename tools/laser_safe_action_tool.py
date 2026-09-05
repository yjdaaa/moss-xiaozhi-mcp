from core import laser_execution
from tools import laser_grbl_tool, laser_network_grbl_tool


MOVE_ACTIONS = {"absolute_move", "move_absolute", "move_abs", "绝对移动"}
RELATIVE_MOVE_ACTIONS = {"relative_move", "move_relative", "move_rel", "相对移动"}
ACTION_ALIASES = {
    "status": "status",
    "query_status": "status",
    "查状态": "status",
    "状态": "status",
    "unlock": "unlock",
    "解锁": "unlock",
    "home": "home",
    "homing": "home",
    "归零": "home",
    "回零": "home",
    "set_origin": "set_origin",
    "zero": "set_origin",
    "设置原点": "set_origin",
    "laser_off": "laser_off",
    "off": "laser_off",
    "关闭激光": "laser_off",
    "emergency_stop": "emergency_stop",
    "feed_hold": "emergency_stop",
    "急停": "emergency_stop",
    "soft_reset": "soft_reset",
    "reset": "soft_reset",
    "软复位": "soft_reset",
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


def _normalize_action(action):
    normalized = str(action or "").strip().lower()
    if normalized in MOVE_ACTIONS:
        return "absolute_move", None
    if normalized in RELATIVE_MOVE_ACTIONS:
        return "relative_move", None
    resolved = ACTION_ALIASES.get(normalized)
    if not resolved:
        supported = sorted(set(ACTION_ALIASES) | MOVE_ACTIONS | RELATIVE_MOVE_ACTIONS)
        return None, f"action 不支持，支持: {', '.join(supported)}"
    return resolved, None


def _format_number(value):
    return f"{value:g}"


def _validate_optional_number(value, name, allow_negative=False):
    if value is None or value == "":
        return None, None
    if allow_negative:
        try:
            return float(value), None
        except (TypeError, ValueError):
            return None, f"{name} 必须是数字"
    return laser_grbl_tool._validate_number(value, name, 0, allow_zero=True)


def _validate_feed_rate(feed_rate):
    if feed_rate in (None, "", 0):
        return None, None
    return laser_grbl_tool._validate_number(feed_rate, "feed_rate", 0)


def _move_command(action, x=None, y=None, z=None, feed_rate=0):
    axes = []
    allow_negative = action == "relative_move"
    for name, value in (("X", x), ("Y", y), ("Z", z)):
        number, error = _validate_optional_number(
            value, name.lower(), allow_negative=allow_negative
        )
        if error:
            return None, error
        if number is not None:
            axes.append(f"{name}{_format_number(number)}")
    if not axes:
        return None, "移动动作至少需要提供 x/y/z 中的一个坐标"

    command = ["G90" if action == "absolute_move" else "G91", "G0"] + axes
    rate, error = _validate_feed_rate(feed_rate)
    if error:
        return None, error
    if rate is not None:
        command.append(f"F{_format_number(rate)}")
    return " ".join(command), None


def _command_for_action(action, x=None, y=None, z=None, feed_rate=0, power=0):
    if action == "status":
        return "?", None
    if action == "unlock":
        return "$X", None
    if action == "home":
        return "$H", None
    if action == "set_origin":
        return laser_grbl_tool.ZERO_ORIGIN_COMMAND, None
    if action == "laser_off":
        return "M5", None
    if action == "emergency_stop":
        return "!", None
    if action == "soft_reset":
        return "\x18", None
    if action in ("absolute_move", "relative_move"):
        return _move_command(action, x=x, y=y, z=z, feed_rate=feed_rate)
    return None, f"action 不支持: {action}"


def run_laser_safe_action(
    action,
    connection_mode="",
    confirmed=False,
    x=None,
    y=None,
    z=None,
    feed_rate=0,
    power=0,
    host="",
    port="",
    baudrate=laser_grbl_tool.DEFAULT_BAUDRATE,
    http_port=laser_network_grbl_tool.DEFAULT_HTTP_PORT,
    telnet_port=laser_network_grbl_tool.DEFAULT_TELNET_PORT,
    timeout=laser_network_grbl_tool.DEFAULT_TIMEOUT,
    transport="telnet",
    dry_run=False,
):
    resolved_action, error = _normalize_action(action)
    if error:
        return _build_failure(error)
    mode, error = laser_execution.resolve_laser_connection_mode(connection_mode)
    if error:
        return _build_failure(error)
    command, error = _command_for_action(
        resolved_action, x=x, y=y, z=z, feed_rate=feed_rate, power=power
    )
    if error:
        return _build_failure(error)

    detail = {"action": resolved_action, "connection_mode": mode, "command": command}
    if mode == "network":
        result = laser_network_grbl_tool.send_network_command(
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
    else:
        result = laser_grbl_tool.send_serial_command(
            command,
            port=port,
            baudrate=baudrate,
            confirmed=confirmed,
            dry_run=dry_run,
            manual_mode=True,
        )

    if isinstance(result, dict):
        result.setdefault("detail", {})
        if isinstance(result["detail"], dict):
            result["detail"].update(detail)
    return result


def register_tool(mcp):
    @mcp.tool()
    def laser_safe_action_tool(
        action: str,
        connection_mode: str = "",
        confirmed: bool = False,
        x: float = None,
        y: float = None,
        z: float = None,
        feed_rate: float = 0,
        power: float = 0,
        host: str = "",
        port: str = "",
        baudrate: int = laser_grbl_tool.DEFAULT_BAUDRATE,
        http_port: int = laser_network_grbl_tool.DEFAULT_HTTP_PORT,
        telnet_port: int = laser_network_grbl_tool.DEFAULT_TELNET_PORT,
        timeout: float = laser_network_grbl_tool.DEFAULT_TIMEOUT,
        transport: str = "telnet",
        dry_run: bool = False,
    ) -> dict:
        """
        激光雕刻机安全语义动作工具，只封装现有串口/网络单命令安全发送路径。

        action 支持: status, unlock, home, set_origin, laser_off, absolute_move,
        relative_move, emergency_stop, soft_reset。除 status 外，改变机器状态的动作
        都必须 confirmed=true；网络模式必须提供 host 或配置 LASER_NETWORK_HOST。
        未指定 connection_mode 时按 LASER_DEFAULT_CONNECTION_MODE 路由。
        """
        try:
            return run_laser_safe_action(
                action=action,
                connection_mode=connection_mode,
                confirmed=confirmed,
                x=x,
                y=y,
                z=z,
                feed_rate=feed_rate,
                power=power,
                host=host,
                port=port,
                baudrate=baudrate,
                http_port=http_port,
                telnet_port=telnet_port,
                timeout=timeout,
                transport=transport,
                dry_run=dry_run,
            )
        except Exception as exc:
            return _build_failure(f"激光安全动作执行失败: {exc}")
