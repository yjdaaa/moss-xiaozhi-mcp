from core import laser_execution
from tools import laser_grbl_tool, laser_network_grbl_tool


def _build_summary(serial_connected, network_connected):
    if serial_connected and network_connected:
        return "串口和网络都已连接"
    if serial_connected:
        return "串口已连接，网络未连接"
    if network_connected:
        return "串口未连接，网络已连接"
    return "未检测到串口或网络连接"


def _check_serial_connection(port="", baudrate=laser_grbl_tool.DEFAULT_BAUDRATE):
    probe = laser_execution.probe_serial_grbl(port=port, baudrate=baudrate)
    if not probe.get("success"):
        return False, {
            "error": probe.get("result", "串口连接检查失败"),
            "detail": probe.get("detail"),
        }

    result = probe.get("result") or {}
    return True, {
        "port": result.get("port"),
        "baudrate": result.get("baudrate", baudrate),
        "probe_command": result.get("probe_command") or "?",
    }


def _check_network_connection(
    host="",
    transport=laser_network_grbl_tool.FORCED_FILE_TRANSPORT,
    http_port=laser_network_grbl_tool.DEFAULT_HTTP_PORT,
    telnet_port=laser_network_grbl_tool.DEFAULT_TELNET_PORT,
    timeout=laser_network_grbl_tool.DEFAULT_TIMEOUT,
):
    resolved_host, error = laser_execution.resolve_laser_network_host(host)
    if error:
        return False, {"error": error}

    resolved_transport, error = laser_network_grbl_tool._resolve_transport(transport)
    if error:
        return False, {"error": error}

    if resolved_transport == "http":
        result = laser_network_grbl_tool.query_web_command(
            resolved_host, "[ESP800]", port=http_port, timeout=timeout
        )
        probe_command = "[ESP800]"
        port = http_port
    else:
        result = laser_network_grbl_tool.query_telnet_command(
            resolved_host, "?", port=telnet_port, timeout=timeout
        )
        probe_command = "?"
        port = telnet_port

    if not result.get("success"):
        return False, {
            "host": resolved_host,
            "transport": resolved_transport,
            "port": port,
            "probe_command": probe_command,
            "error": result.get("result", "网络连接检查失败"),
        }

    return True, {
        "host": resolved_host,
        "transport": resolved_transport,
        "port": port,
        "probe_command": probe_command,
    }


def check_laser_connection(
    port="",
    baudrate=laser_grbl_tool.DEFAULT_BAUDRATE,
    host="",
    transport=laser_network_grbl_tool.FORCED_FILE_TRANSPORT,
    http_port=laser_network_grbl_tool.DEFAULT_HTTP_PORT,
    telnet_port=laser_network_grbl_tool.DEFAULT_TELNET_PORT,
    timeout=laser_network_grbl_tool.DEFAULT_TIMEOUT,
    include_detail=False,
):
    serial_connected, serial_detail = _check_serial_connection(port=port, baudrate=baudrate)
    network_connected, network_detail = _check_network_connection(
        host=host,
        transport=transport,
        http_port=http_port,
        telnet_port=telnet_port,
        timeout=timeout,
    )

    payload = {
        "serial_connected": serial_connected,
        "network_connected": network_connected,
        "summary": _build_summary(serial_connected, network_connected),
    }
    if include_detail:
        payload["detail"] = {
            "serial": serial_detail,
            "network": network_detail,
        }
    return payload


def register_tool(mcp):
    @mcp.tool()
    def check_laser_connection_tool(
        port: str = "",
        baudrate: int = laser_grbl_tool.DEFAULT_BAUDRATE,
        host: str = "",
        transport: str = laser_network_grbl_tool.FORCED_FILE_TRANSPORT,
        http_port: int = laser_network_grbl_tool.DEFAULT_HTTP_PORT,
        telnet_port: int = laser_network_grbl_tool.DEFAULT_TELNET_PORT,
        timeout: float = laser_network_grbl_tool.DEFAULT_TIMEOUT,
        include_detail: bool = False,
    ) -> dict:
        """
        只检查激光雕刻机串口和网络是否连通，并返回极简摘要。

        适用于“检查激光雕刻机是否打开 / 看看连接没连接”。本工具只使用只读探测：
        串口发送 ?，网络默认使用 Telnet 发送 ?。不要用它执行雕刻、运动、开激光或配置写入。
        默认不返回 GRBL 配置、固件详情或完整设备响应；需要排障时才设置 include_detail=true。
        """
        try:
            return check_laser_connection(
                port=port,
                baudrate=baudrate,
                host=host,
                transport=transport,
                http_port=http_port,
                telnet_port=telnet_port,
                timeout=timeout,
                include_detail=include_detail,
            )
        except Exception as exc:
            return {
                "serial_connected": False,
                "network_connected": False,
                "summary": "未检测到串口或网络连接",
                "detail": {"error": f"连接检查失败: {exc}"},
            }
