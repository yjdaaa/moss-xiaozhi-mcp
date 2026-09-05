"""Public laser execution, connection probing, and routing helpers. tools -> core only."""

from __future__ import annotations

from core import _laser_execution_backend as _backend
from core.laser_confirm import is_explicitly_confirmed
from core.laser_runtime.config import get_laser_settings

_SETTINGS = get_laser_settings()

# High-level routing default (patchable). Distinct from _backend.normalize_connection_mode
# which rejects empty mode for send/status/cancel job-directory routing.
DEFAULT_CONNECTION_MODE = _SETTINGS.default_connection_mode
DEFAULT_BAUDRATE = _backend.DEFAULT_BAUDRATE

CONNECTION_MODE_ALIASES = {
    "serial": "serial",
    "usb": "serial",
    "com": "serial",
    "串口": "serial",
    "network": "network",
    "net": "network",
    "wifi": "network",
    "http": "network",
    "telnet": "network",
    "网络": "network",
}
def probe_serial_grbl(port="", baudrate=None):
    """Read-only serial GRBL probe; always closes the serial handle before return.

    Does not change _backend.preflight_serial_send_file semantics (success still
    returns an open serial for full-file send). This facade closes that handle.
    """
    if baudrate is None:
        baudrate = DEFAULT_BAUDRATE

    error_payload = None
    ser = None
    connected_port = None
    probe_detail = None
    close_error = None

    try:
        error_payload, ser, connected_port, probe_detail = _backend.preflight_serial_send_file(
            port, baudrate
        )
    finally:
        if ser is not None:
            try:
                ser.close()
            except Exception as exc:
                close_error = str(exc)

    if error_payload:
        if close_error:
            payload = dict(error_payload)
            original_detail = payload.get("detail")
            detail = dict(original_detail) if isinstance(original_detail, dict) else {}
            if original_detail is not None and not isinstance(original_detail, dict):
                detail["preflight_detail"] = original_detail
            detail["close_error"] = close_error
            payload["detail"] = detail
            return payload
        return error_payload

    result = {
        "port": connected_port,
        "baudrate": baudrate,
        "probe_command": (probe_detail or {}).get("probe_command") or "?",
    }
    if probe_detail and probe_detail.get("probe_response") is not None:
        result["probe_response"] = probe_detail["probe_response"]

    if close_error:
        return _backend.build_failure(
            f"串口探测成功但关闭串口失败: {close_error}",
            {"probe": result, "close_error": close_error},
        )
    return _backend.build_success(result)


def resolve_laser_network_host(host):
    """Public host resolver; delegates to backend authority."""
    return _backend.resolve_host(host)


def resolve_laser_connection_mode(connection_mode="", default_mode=None):
    """High-level connection mode resolver with empty-value default routing.

    Not a substitute for _backend.normalize_connection_mode used by
    send_file/job_status/cancel_job (those still require explicit mode).
    """
    explicit = str(connection_mode or "").strip().lower()
    if explicit:
        resolved = CONNECTION_MODE_ALIASES.get(explicit)
        if not resolved:
            return None, "connection_mode 不支持，支持: serial / network"
        return resolved, None

    # Empty explicit mode: use override or module default, then alias table.
    source = DEFAULT_CONNECTION_MODE if default_mode is None else default_mode
    normalized = str(source or "network").strip().lower()
    resolved = CONNECTION_MODE_ALIASES.get(normalized)
    if resolved:
        return resolved, None
    return None, "LASER_DEFAULT_CONNECTION_MODE 不支持，支持: network / serial"


def send_file(
    prepared_result,
    connection_mode,
    confirmed=False,
    *,
    run_in_background=True,
    dry_run=False,
    **connection_options,
):
    """Send a prepared full-file job via serial or network backend.

    prepared_result must keep gcode_file / converted and related metadata.
    connection_mode is required (serial|network).
    """
    mode, error = _backend.normalize_connection_mode(connection_mode)
    if error:
        return _backend.build_failure(error, prepared_result)

    if not isinstance(prepared_result, dict) or not prepared_result.get("gcode_file"):
        return _backend.build_failure(
            "prepared_result 必须包含 gcode_file",
            prepared_result if isinstance(prepared_result, dict) else None,
        )

    # Final execution gate: normalize here so callers cannot bypass with bool("false").
    confirmed = is_explicitly_confirmed(confirmed)

    if confirmed and not dry_run:
        _expected, hash_error = _backend.require_expected_gcode_sha256(prepared_result)
        if hash_error:
            return hash_error

    if dry_run or not confirmed:
        if mode == "serial":
            return {
                "success": True,
                "result": {
                    "prepared": prepared_result,
                    "confirmation_required": True,
                    "result": (
                        "雕刻文件已准备，但未发送。确认文件、材料/参数和设备安全后，"
                        "才可用 confirmed=true 启动。"
                    ),
                },
            }
        requested_transport = connection_options.get("transport", "http")
        host, host_error = _backend.resolve_host(connection_options.get("host", ""))
        if host_error:
            return _backend.build_failure(host_error)
        preview = {
            "transport": _backend.FORCED_FILE_TRANSPORT,
            "requested_transport": requested_transport,
            "transport_policy": "complete network laser jobs always use telnet",
            "host": host,
            "prepared": prepared_result,
            "confirmation_required": True,
            "result": "网络雕刻文件已准备，但未发送。确认安全后用 confirmed=true 启动。",
        }
        return _backend.build_success(preview)

    if mode == "serial":
        port = connection_options.get("port", "")
        baudrate = connection_options.get("baudrate", _backend.DEFAULT_BAUDRATE)
        wait_for_response = connection_options.get("wait_for_response", True)
        if run_in_background:
            return _backend.start_serial_send_file_job(
                prepared_result, port, baudrate, wait_for_response
            )
        return _backend.execute_serial_send_file(
            prepared_result, port, baudrate, wait_for_response
        )

    host = connection_options.get("host", "")
    # Complete file jobs always force Telnet regardless of requested transport.
    transport = _backend.FORCED_FILE_TRANSPORT
    http_port = connection_options.get("http_port", _backend.DEFAULT_HTTP_PORT)
    telnet_port = connection_options.get("telnet_port", _backend.DEFAULT_TELNET_PORT)
    timeout = connection_options.get("timeout", _backend.DEFAULT_TIMEOUT)
    wait_for_response = connection_options.get("wait_for_response", True)
    if run_in_background:
        return _backend.start_network_send_file_job(
            prepared_result,
            host,
            transport,
            http_port,
            telnet_port,
            timeout,
            wait_for_response,
        )
    return _backend.execute_network_send_file(
        prepared_result,
        host=host,
        transport=transport,
        http_port=http_port,
        telnet_port=telnet_port,
        timeout=timeout,
        wait_for_response=wait_for_response,
    )


def job_status(job_id, connection_mode):
    mode, error = _backend.normalize_connection_mode(connection_mode)
    if error:
        return _backend.build_failure(error)
    if mode == "serial":
        return _backend.get_serial_send_file_job(job_id)
    return _backend.get_network_send_file_job(job_id)


def cancel_job(job_id, connection_mode):
    mode, error = _backend.normalize_connection_mode(connection_mode)
    if error:
        return _backend.build_failure(error)
    if mode == "serial":
        return _backend.cancel_serial_send_file_job(job_id)
    return _backend.cancel_network_send_file_job(job_id)
