"""Serial/network full-file send orchestration and sole background worker CLI.

Dependency rule: this module must never import tools.*.
"""

from __future__ import annotations

import io
import json
import os
import signal
import socket
import subprocess
import sys
import time
import uuid

from core.laser_runtime.config import get_laser_settings
from core.laser_runtime.models import sha256_binary_handle

_SETTINGS = get_laser_settings()
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME_ROOT = os.path.join(REPO_ROOT, ".runtime")

DEFAULT_BAUDRATE = _SETTINGS.baudrate
DEFAULT_GRBL_PORT = _SETTINGS.default_serial_port
DEFAULT_NETWORK_HOST = _SETTINGS.network_host
DEFAULT_HTTP_PORT = _SETTINGS.network_http_port
DEFAULT_TELNET_PORT = _SETTINGS.network_telnet_port
DEFAULT_TIMEOUT = _SETTINGS.network_timeout
FORCED_FILE_TRANSPORT = "telnet"
ZERO_ORIGIN_COMMAND = "G92 X0 Y0 Z0"

SERIAL_JOBS_DIR = os.path.join(RUNTIME_ROOT, "lasergrbl_jobs")
NETWORK_JOBS_DIR = os.path.join(RUNTIME_ROOT, "lasergrbl_network_jobs")
TERMINAL_JOB_STATUSES = {"completed", "failed", "cancelled", "canceled"}

TRANSPORT_ALIASES = {
    "http": "http",
    "web": "http",
    "webui": "http",
    "网络": "http",
    "telnet": "telnet",
    "tcp": "telnet",
}

CONNECTION_MODE_ALIASES = {
    "serial": "serial",
    "串口": "serial",
    "com": "serial",
    "network": "network",
    "net": "network",
    "网络": "network",
}


def build_failure(message, detail=None, error_code=None):
    payload = {"success": False, "result": message}
    if detail is not None:
        payload["detail"] = detail
    if error_code is not None:
        payload["error_code"] = error_code
    return payload


def build_success(result, detail=None):
    payload = {"success": True, "result": result}
    if detail is not None:
        payload["detail"] = detail
    return payload


def require_expected_gcode_sha256(prepared_result):
    if not isinstance(prepared_result, dict):
        return None, build_failure(
            "prepared_result 必须包含 expected_gcode_sha256",
            prepared_result,
            error_code="preview_content_mismatch",
        )
    expected = str(prepared_result.get("expected_gcode_sha256") or "").strip().lower()
    if not expected or len(expected) != 64 or any(ch not in "0123456789abcdef" for ch in expected):
        return None, build_failure(
            "confirmed 完整文件发送缺少有效 expected_gcode_sha256；请重新 preview 生成摘要",
            prepared_result,
            error_code="preview_content_mismatch",
        )
    return expected, None


def open_verified_gcode_text(gcode_file, expected_gcode_sha256):
    """Open the G-code path once as binary, hash raw bytes, wrap the same handle as text.

    Closes the path TOCTOU window between hash and stream open. On every failure path
    the binary handle is closed. Success returns a TextIOWrapper over the same raw
    descriptor; callers must close the returned handle exactly once.
    """
    expected = str(expected_gcode_sha256 or "").strip().lower()
    path = str(gcode_file or "").strip()
    if not path:
        return {
            "success": False,
            "error_code": "preview_content_mismatch",
            "result": "G-code 路径缺失，无法校验摘要",
            "handle": None,
        }
    if not os.path.isfile(path):
        return {
            "success": False,
            "error_code": "preview_content_mismatch",
            "result": "G-code 文件缺失，无法校验摘要",
            "handle": None,
        }

    binary = None
    try:
        binary = open(path, "rb")
        actual = sha256_binary_handle(binary)
        if actual != expected:
            binary.close()
            binary = None
            return {
                "success": False,
                "error_code": "preview_content_mismatch",
                "result": "preview_content_mismatch: G-code 内容与预览摘要不一致",
                "handle": None,
                "detail": {
                    "expected_gcode_sha256": expected,
                    "actual_gcode_sha256": actual,
                },
            }
        # Same raw FD for hash and text iteration; TextIOWrapper.close() closes binary.
        try:
            text_handle = io.TextIOWrapper(
                binary,
                encoding="utf-8",
                errors="replace",
                newline="",
                line_buffering=False,
            )
        except Exception:
            try:
                binary.close()
            except Exception:
                pass
            binary = None
            return {
                "success": False,
                "error_code": "preview_content_mismatch",
                "result": "G-code 摘要校验失败",
                "handle": None,
            }
        binary = None  # ownership transferred to TextIOWrapper
        return {
            "success": True,
            "handle": text_handle,
            "actual_gcode_sha256": actual,
            "gcode_file": path,
        }
    except OSError:
        if binary is not None:
            try:
                binary.close()
            except Exception:
                pass
        return {
            "success": False,
            "error_code": "preview_content_mismatch",
            "result": "无法读取或打开 G-code 以校验摘要",
            "handle": None,
        }
    except Exception:
        if binary is not None:
            try:
                binary.close()
            except Exception:
                pass
        return {
            "success": False,
            "error_code": "preview_content_mismatch",
            "result": "G-code 摘要校验失败",
            "handle": None,
        }


def normalize_connection_mode(connection_mode):
    raw = str(connection_mode or "").strip().lower()
    if not raw:
        return None, "请指定 connection_mode（serial 或 network）；不允许猜测或扫描两套任务目录"
    resolved = CONNECTION_MODE_ALIASES.get(raw)
    if not resolved:
        return None, f"不支持的 connection_mode: {connection_mode}，支持: serial / network"
    return resolved, None


def write_job_file(job_file, data):
    os.makedirs(os.path.dirname(job_file) or ".", exist_ok=True)
    tmp_file = job_file + ".tmp"
    with open(tmp_file, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
    os.replace(tmp_file, job_file)


def read_job_file(job_file):
    with open(job_file, "r", encoding="utf-8") as file:
        return json.load(file)


def update_job_file(job_file, **changes):
    job = read_job_file(job_file)
    job.update(changes)
    write_job_file(job_file, job)
    return job


def serial_job_file_path(job_id):
    return os.path.join(SERIAL_JOBS_DIR, f"{job_id}.json")


def network_job_file_path(job_id):
    return os.path.join(NETWORK_JOBS_DIR, f"{job_id}.json")


def _popen_worker(mode, job_file):
    popen_kwargs = {
        "cwd": REPO_ROOT,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        popen_kwargs["start_new_session"] = True

    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "core._laser_execution_backend",
            "--send-file-worker",
            mode,
            job_file,
        ],
        **popen_kwargs,
    )


# ---- serial low-level ----


def load_serial():
    try:
        import serial
        from serial.tools import list_ports

        return serial, list_ports, None
    except ImportError:
        return None, None, "缺少 pyserial 依赖，请先在当前 Python 环境执行: pip install pyserial"


def resolve_serial_port(port):
    return (port or DEFAULT_GRBL_PORT or "").strip()


def list_serial_ports(list_ports):
    ports = list_ports.comports()
    return [{"port": p.device, "desc": p.description} for p in ports]


def auto_detect_grbl_port(baudrate, serial_module, list_ports):
    ports = [p.device for p in list_ports.comports()]
    for port in ports:
        try:
            ser = serial_module.Serial(port, baudrate, timeout=2)
            time.sleep(1)
            ser.reset_input_buffer()
            ser.write(b"\n")
            deadline = time.time() + 2
            while time.time() < deadline:
                resp = ser.readline().decode(errors="replace").strip()
                if "Grbl" in resp or "grbl" in resp:
                    ser.close()
                    return port
            ser.close()
        except (serial_module.SerialException, OSError):
            continue
    return None


def connect_grbl_serial(port, baudrate, serial_module, list_ports):
    configured_port = resolve_serial_port(port)
    available_ports = list_serial_ports(list_ports)
    tried = []
    candidates = []

    if configured_port:
        candidates.append(configured_port)
    else:
        detected_port = auto_detect_grbl_port(baudrate, serial_module, list_ports)
        if detected_port:
            candidates.append(detected_port)

    if not candidates:
        detail = {
            "configured_port": configured_port,
            "available_ports": available_ports,
        }
        return None, None, build_failure("未检测到可用的 GRBL 串口", detail)

    for candidate in candidates:
        try:
            ser = serial_module.Serial(candidate, baudrate, timeout=1)
            time.sleep(2)
            ser.write(b"\n")
            time.sleep(0.5)
            ser.reset_input_buffer()
            return ser, candidate, None
        except Exception as exc:
            tried.append({"port": candidate, "error": str(exc)})

    detail = {
        "configured_port": configured_port,
        "available_ports": available_ports,
        "tried_ports": tried,
    }
    return None, None, build_failure("无法连接激光雕刻机串口", detail)


def send_gcode_line(ser, line, timeout=5):
    ser.write((line + "\n").encode())
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = ser.readline().decode(errors="replace").strip()
        if not resp:
            continue
        if resp == "ok":
            return True, None
        if resp.startswith("error"):
            return False, resp
    return False, f"等待 GRBL 响应超时: {line}"


def send_gcode_line_without_wait(ser, line):
    ser.write((line + "\n").encode())
    return True, None


def send_gcode_file(
    ser,
    gcode_file=None,
    wait_for_response=False,
    send_zero_origin=False,
    gcode_handle=None,
):
    """Stream gcode lines. Prefer a pre-verified gcode_handle; do not reopen when provided.

    Any handle used for streaming (caller-provided verified stream or path-opened)
    is closed exactly once in the unified finally, including zero-origin failures.
    """
    total = 0
    errors = 0
    send_line = send_gcode_line if wait_for_response else send_gcode_line_without_wait
    handle = gcode_handle

    try:
        if send_zero_origin:
            success, error = send_line(ser, ZERO_ORIGIN_COMMAND)
            if not success:
                return total, 1, f"设置零点失败: {ZERO_ORIGIN_COMMAND}，原因: {error}"
            total += 1

        if handle is None:
            if not gcode_file:
                return total, 1, "缺少 gcode_file/gcode_handle"
            handle = open(gcode_file, "r", encoding="utf-8", errors="replace")
        else:
            handle.seek(0)

        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line or line.startswith(";"):
                continue
            success, error = send_line(ser, line)
            total += 1
            if not success:
                errors += 1
                return total, errors, f"发送第 {line_number} 行失败: {line}，原因: {error}"
        return total, errors, None
    finally:
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass


def probe_grbl_serial_device(ser, connected_port, baudrate, timeout=2):
    try:
        ser.write(b"?\n")
        deadline = time.time() + timeout
        while time.time() < deadline:
            response = ser.readline().decode(errors="replace").strip()
            if response:
                return None, {
                    "port": connected_port,
                    "baudrate": baudrate,
                    "probe_command": "?",
                    "probe_response": response,
                }
        return (
            build_failure(
                "设备在线检查失败：串口已连接但 GRBL 对只读状态查询 ? 无响应，请确认激光机已开机并处于可通信状态。",
                {"port": connected_port, "baudrate": baudrate, "probe_command": "?"},
            ),
            None,
        )
    except Exception as exc:
        return (
            build_failure(
                f"设备在线检查失败：串口只读状态查询 ? 异常: {exc}",
                {
                    "port": connected_port,
                    "baudrate": baudrate,
                    "probe_command": "?",
                    "error": str(exc),
                },
            ),
            None,
        )


def preflight_serial_send_file(port, baudrate):
    serial_module, list_ports, serial_error = load_serial()
    if serial_error:
        return build_failure(serial_error), None, None, None

    ser, connected_port, error_payload = connect_grbl_serial(
        port, baudrate, serial_module, list_ports
    )
    if error_payload:
        return error_payload, None, None, None

    error_payload, probe_detail = probe_grbl_serial_device(ser, connected_port, baudrate)
    if error_payload:
        try:
            ser.close()
        finally:
            return error_payload, None, None, None
    return None, ser, connected_port, probe_detail


def connect_serial_for_stream(port, baudrate):
    """Open serial for streaming only (no online probe). Used after start preflight."""
    serial_module, list_ports, serial_error = load_serial()
    if serial_error:
        return build_failure(serial_error), None, None
    ser, connected_port, error_payload = connect_grbl_serial(
        port, baudrate, serial_module, list_ports
    )
    if error_payload:
        return error_payload, None, None
    return None, ser, connected_port


def execute_serial_send_file(
    prepared_result,
    port,
    baudrate,
    wait_for_response,
    *,
    skip_online_probe=False,
    device_probe=None,
):
    """Stream a prepared serial job.

    Order: open+verify SHA-256 → one online probe → stream from verified handle.
    skip_online_probe is retained for API compatibility but ignored for safety:
    confirmed full-file sends always probe after hash verification.
    """
    del skip_online_probe, device_probe
    gcode_file = prepared_result["gcode_file"]
    send_zero_origin = bool(prepared_result.get("converted"))
    expected, hash_error = require_expected_gcode_sha256(prepared_result)
    if hash_error:
        return hash_error

    verified = open_verified_gcode_text(gcode_file, expected)
    if not verified.get("success"):
        return build_failure(
            verified.get("result") or "G-code 摘要校验失败",
            verified.get("detail") or prepared_result,
            error_code=verified.get("error_code") or "preview_content_mismatch",
        )
    gcode_handle = verified["handle"]
    ser = None

    try:
        error_payload, ser, connected_port, probe_detail = preflight_serial_send_file(
            port, baudrate
        )
        if error_payload:
            error_payload.setdefault("detail", prepared_result)
            return error_payload

        try:
            total, errors, send_error = send_gcode_file(
                ser,
                gcode_file=gcode_file,
                wait_for_response=wait_for_response,
                send_zero_origin=send_zero_origin,
                gcode_handle=gcode_handle,
            )
        finally:
            if ser is not None:
                try:
                    ser.close()
                except Exception:
                    pass

        detail = {
            **prepared_result,
            "port": connected_port,
            "baudrate": baudrate,
            "total_commands": total,
            "errors": errors,
            "wait_for_response": wait_for_response,
            "send_zero_origin": send_zero_origin,
            "device_probe": probe_detail,
            "online_probe_skipped": False,
            "actual_gcode_sha256": verified.get("actual_gcode_sha256"),
        }
        if send_error:
            detail["send_error"] = send_error
            return build_failure(send_error, detail)

        return {
            "success": True,
            "result": f"发送成功，串口: {connected_port}，雕刻文件: {gcode_file}",
            "detail": detail,
        }
    except Exception as exc:
        detail = {
            **prepared_result,
            "configured_port": resolve_serial_port(port),
            "baudrate": baudrate,
            "error": str(exc),
        }
        return build_failure(f"发送雕刻文件失败: {exc}", detail)
    finally:
        if gcode_handle is not None:
            try:
                gcode_handle.close()
            except Exception:
                pass


def start_serial_send_file_job(prepared_result, port, baudrate, wait_for_response):
    """Persist job with expected hash only; no device access. Worker probes after verify."""
    expected, hash_error = require_expected_gcode_sha256(prepared_result)
    if hash_error:
        return hash_error

    job_id = uuid.uuid4().hex
    job_file = serial_job_file_path(job_id)
    job = {
        "job_id": job_id,
        "status": "pending",
        "created_at": time.time(),
        "prepared": prepared_result,
        "port": port,
        "baudrate": baudrate,
        "wait_for_response": wait_for_response,
        "expected_gcode_sha256": expected,
        "preflight_ok": False,
        "result": None,
    }
    write_job_file(job_file, job)
    process = _popen_worker("serial", job_file)
    update_job_file(job_file, process_id=process.pid)
    return {
        "success": True,
        "result": "雕刻任务已交给独立进程后台发送，可用 job_status 查询进度",
        "job_id": job_id,
        "status": "pending",
        "detail": prepared_result,
    }


def get_serial_send_file_job(job_id):
    if not job_id:
        return build_failure("请指定 job_id")
    job_file = serial_job_file_path(job_id)
    if not os.path.isfile(job_file):
        return build_failure(f"未找到雕刻任务: {job_id}")
    try:
        return {"success": True, "result": read_job_file(job_file)}
    except (OSError, ValueError) as exc:
        return build_failure(f"读取雕刻任务状态失败: {exc}")


def cancel_serial_send_file_job(job_id):
    if not job_id:
        return build_failure("请指定 job_id")
    job_file = serial_job_file_path(job_id)
    if not os.path.isfile(job_file):
        return build_failure(f"未找到雕刻任务: {job_id}")
    try:
        job = read_job_file(job_file)
    except (OSError, ValueError) as exc:
        return build_failure(f"读取雕刻任务状态失败: {exc}")

    if job.get("status") in TERMINAL_JOB_STATUSES:
        return build_failure(f"任务已结束，不能中断: {job.get('status')}", job)

    process_id = job.get("process_id")
    kill_error = None
    if process_id:
        try:
            os.kill(int(process_id), signal.SIGTERM)
        except OSError as exc:
            kill_error = str(exc)

    result = {
        "success": True,
        "result": "雕刻任务已中断，不会继续发送后续 G-code",
        "job_id": job_id,
        "process_id": process_id,
    }
    if kill_error:
        result["kill_error"] = kill_error

    updated = update_job_file(
        job_file,
        status="cancelled",
        finished_at=time.time(),
        result=result,
    )
    return {"success": True, "result": "雕刻任务已中断", "detail": updated}


def run_serial_send_file_worker(job_file):
    try:
        job = update_job_file(job_file, status="running", started_at=time.time())
        prepared = dict(job.get("prepared") or {})
        expected = str(job.get("expected_gcode_sha256") or prepared.get("expected_gcode_sha256") or "").strip()
        if expected:
            prepared["expected_gcode_sha256"] = expected
        # Worker owns the sole online probe, after open+hash verification.
        result = execute_serial_send_file(
            prepared,
            job.get("port", ""),
            job.get("baudrate", DEFAULT_BAUDRATE),
            job.get("wait_for_response", True),
            skip_online_probe=False,
        )
        update_job_file(
            job_file,
            status="completed" if result.get("success") else "failed",
            finished_at=time.time(),
            result=result,
        )
        return 0 if result.get("success") else 1
    except Exception as exc:
        try:
            update_job_file(
                job_file,
                status="failed",
                finished_at=time.time(),
                result=build_failure(f"后台雕刻任务异常: {exc}"),
            )
        except Exception:
            pass
        return 1


# ---- network low-level ----


def resolve_host(host):
    resolved = (host or DEFAULT_NETWORK_HOST or "").strip()
    if not resolved:
        return None, "请指定 laser 网络主机 host；不要从历史对话猜设备 IP"
    if "://" in resolved:
        return None, "host 只填写 IP 或主机名，不要包含 http:// 或路径"
    return resolved, None


def resolve_transport(transport):
    normalized = str(transport or "http").strip().lower()
    resolved = TRANSPORT_ALIASES.get(normalized)
    if not resolved:
        return None, "transport 不支持，支持: http / telnet"
    return resolved, None


def validate_port(value, name):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None, f"{name} 必须是整数"
    if number < 1 or number > 65535:
        return None, f"{name} 必须在 1-65535 之间"
    return number, None


def validate_timeout(timeout):
    try:
        number = float(timeout)
    except (TypeError, ValueError):
        return None, "timeout 必须是数字"
    if number <= 0:
        return None, "timeout 必须大于 0"
    return number, None


def mask_command(command):
    text = str(command or "")
    lowered = text.lower()
    if "password" in lowered or "token" in lowered or "pwd" in lowered:
        return "[redacted]"
    return text


def read_telnet_response(sock, timeout):
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
    host, error = resolve_host(host)
    if error:
        return build_failure(error)
    port, error = validate_port(port, "telnet_port")
    if error:
        return build_failure(error)
    timeout, error = validate_timeout(timeout)
    if error:
        return build_failure(error)

    try:
        with socket_factory((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall((command.strip() + "\n").encode("utf-8"))
            response = read_telnet_response(sock, timeout)
    except (TimeoutError, OSError, socket.timeout) as exc:
        return build_failure(
            f"Telnet 命令异常: {exc}",
            {
                "transport": "telnet",
                "command": mask_command(command),
                "host": host,
                "port": port,
            },
        )

    detail = {
        "transport": "telnet",
        "command": mask_command(command),
        "host": host,
        "port": port,
        "response": response,
    }
    if response.lower().startswith("error"):
        return build_failure(f"设备返回错误: {response}", detail)
    return build_success(detail)


def strip_inline_comment(command):
    command = str(command or "").strip()
    if not command or command.startswith(";"):
        return ""
    return command.split(";", 1)[0].strip()


def iter_gcode_lines(gcode_file=None, gcode_handle=None):
    """Yield (line_number, command). Prefer verified gcode_handle; no reopen when provided."""
    owns_handle = gcode_handle is None
    if gcode_handle is None:
        if not gcode_file:
            return
        gcode_handle = open(gcode_file, "r", encoding="utf-8", errors="replace")
        owns_handle = True
    else:
        gcode_handle.seek(0)
    try:
        for line_number, line in enumerate(gcode_handle, start=1):
            command = strip_inline_comment(line)
            if command:
                yield line_number, command
    finally:
        if owns_handle and gcode_handle is not None:
            gcode_handle.close()


def send_telnet_line(sock, command, timeout, wait_for_response=True):
    sock.sendall((command.strip() + "\n").encode("utf-8"))
    if not wait_for_response:
        return True, ""
    response = read_telnet_response(sock, timeout)
    if response.lower().startswith("error"):
        return False, response
    return True, response


def send_telnet_gcode_file(
    host,
    gcode_file=None,
    telnet_port=None,
    timeout=None,
    wait_for_response=True,
    send_zero_origin=False,
    socket_factory=socket.create_connection,
    gcode_handle=None,
):
    """Stream over Telnet. Caller-provided verified handles are closed here once."""
    total = 0
    handle = gcode_handle
    try:
        with socket_factory((host, telnet_port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            if send_zero_origin:
                success, response = send_telnet_line(
                    sock, ZERO_ORIGIN_COMMAND, timeout, wait_for_response
                )
                total += 1
                if not success:
                    return total, 1, f"设置零点失败: {response}"

            if handle is not None:
                handle.seek(0)
                for line_number, line in enumerate(handle, start=1):
                    command = strip_inline_comment(line)
                    if not command:
                        continue
                    success, response = send_telnet_line(
                        sock, command, timeout, wait_for_response
                    )
                    total += 1
                    if not success:
                        return (
                            total,
                            1,
                            f"发送第 {line_number} 行失败: {command}，原因: {response}",
                        )
            else:
                for line_number, command in iter_gcode_lines(gcode_file=gcode_file):
                    success, response = send_telnet_line(
                        sock, command, timeout, wait_for_response
                    )
                    total += 1
                    if not success:
                        return (
                            total,
                            1,
                            f"发送第 {line_number} 行失败: {command}，原因: {response}",
                        )
        return total, 0, None
    except (TimeoutError, OSError, socket.timeout) as exc:
        return total, 1, f"Telnet 发送异常: {exc}"
    finally:
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass


def preflight_network_send_file(host, transport, http_port, telnet_port, timeout):
    """Read-only online probe for complete network file jobs.

    Full-file network jobs always use Telnet. ``http_port`` is kept in the
    probe detail for payload compatibility but is not used for probing.
    """
    del transport  # caller may pass requested transport; full-file path forces Telnet
    result = query_telnet_command(host, "?", port=telnet_port, timeout=timeout)
    detail = {
        "host": host,
        "transport": FORCED_FILE_TRANSPORT,
        "http_port": http_port,
        "telnet_port": telnet_port,
        "probe_command": "?",
        "probe_result": result,
    }
    if not result.get("success"):
        return (
            build_failure(
                "设备在线检查失败：Telnet 只读状态查询 ? 未成功，请确认激光机已开机、IP/端口正确且 Telnet 可访问。",
                detail,
            ),
            None,
        )
    return None, detail


def execute_network_send_file(
    prepared_result,
    host="",
    transport="http",
    http_port=DEFAULT_HTTP_PORT,
    telnet_port=DEFAULT_TELNET_PORT,
    timeout=DEFAULT_TIMEOUT,
    wait_for_response=True,
    *,
    skip_online_probe=False,
    device_probe=None,
):
    """Stream a prepared network job.

    Order: open+verify SHA-256 → one Telnet online probe → stream from verified handle.
    skip_online_probe is retained for API compatibility but ignored for safety.
    """
    del skip_online_probe, device_probe
    host, error = resolve_host(host)
    if error:
        return build_failure(error, prepared_result)
    transport = FORCED_FILE_TRANSPORT
    transport, error = resolve_transport(transport)
    if error:
        return build_failure(error, prepared_result)
    http_port, error = validate_port(http_port, "http_port")
    if error:
        return build_failure(error, prepared_result)
    telnet_port, error = validate_port(telnet_port, "telnet_port")
    if error:
        return build_failure(error, prepared_result)
    timeout, error = validate_timeout(timeout)
    if error:
        return build_failure(error, prepared_result)

    expected, hash_error = require_expected_gcode_sha256(prepared_result)
    if hash_error:
        return hash_error

    gcode_file = prepared_result["gcode_file"]
    send_zero_origin = bool(prepared_result.get("converted"))
    verified = open_verified_gcode_text(gcode_file, expected)
    if not verified.get("success"):
        return build_failure(
            verified.get("result") or "G-code 摘要校验失败",
            verified.get("detail") or prepared_result,
            error_code=verified.get("error_code") or "preview_content_mismatch",
        )
    gcode_handle = verified["handle"]
    total = 0
    errors = 0
    send_error = None
    probe_detail = None

    try:
        error_payload, probe_detail = preflight_network_send_file(
            host, transport, http_port, telnet_port, timeout
        )
        if error_payload:
            return error_payload

        try:
            total, errors, send_error = send_telnet_gcode_file(
                host,
                gcode_file=gcode_file,
                telnet_port=telnet_port,
                timeout=timeout,
                wait_for_response=wait_for_response,
                send_zero_origin=send_zero_origin,
                gcode_handle=gcode_handle,
            )
        except Exception as exc:
            return build_failure(
                f"网络发送雕刻文件失败: {exc}",
                {**prepared_result, "transport": transport, "host": host, "error": str(exc)},
            )

        detail = {
            **prepared_result,
            "transport": transport,
            "host": host,
            "http_port": http_port,
            "telnet_port": telnet_port,
            "timeout": timeout,
            "total_commands": total,
            "errors": errors,
            "wait_for_response": wait_for_response,
            "send_zero_origin": send_zero_origin,
            "device_probe": probe_detail,
            "online_probe_skipped": False,
            "actual_gcode_sha256": verified.get("actual_gcode_sha256"),
        }
        if send_error:
            detail["send_error"] = send_error
            return build_failure(send_error, detail)
        return build_success(
            f"网络发送成功，传输: {transport}，主机: {host}，雕刻文件: {gcode_file}",
            detail,
        )
    finally:
        if gcode_handle is not None:
            try:
                gcode_handle.close()
            except Exception:
                pass


def start_network_send_file_job(
    prepared_result,
    host,
    transport,
    http_port,
    telnet_port,
    timeout,
    wait_for_response,
):
    """Persist job with expected hash only; no device access. Worker probes after verify.

    resolve_host is local parameter validation (not a device probe). Empty host fails
    closed without writing a job or Popen.
    """
    expected, hash_error = require_expected_gcode_sha256(prepared_result)
    if hash_error:
        return hash_error

    host, error = resolve_host(host)
    if error:
        return build_failure(error, prepared_result)

    transport = FORCED_FILE_TRANSPORT
    transport, error = resolve_transport(transport)
    if error:
        return build_failure(error, prepared_result)
    http_port, error = validate_port(http_port, "http_port")
    if error:
        return build_failure(error, prepared_result)
    telnet_port, error = validate_port(telnet_port, "telnet_port")
    if error:
        return build_failure(error, prepared_result)
    timeout, error = validate_timeout(timeout)
    if error:
        return build_failure(error, prepared_result)

    job_id = uuid.uuid4().hex
    job_file = network_job_file_path(job_id)
    job = {
        "job_id": job_id,
        "status": "pending",
        "created_at": time.time(),
        "prepared": prepared_result,
        "host": host,
        "transport": transport,
        "http_port": http_port,
        "telnet_port": telnet_port,
        "timeout": timeout,
        "wait_for_response": wait_for_response,
        "expected_gcode_sha256": expected,
        "preflight_ok": False,
        "result": None,
    }
    write_job_file(job_file, job)
    process = _popen_worker("network", job_file)
    update_job_file(job_file, process_id=process.pid)
    return {
        "success": True,
        "result": "网络雕刻任务已交给独立进程后台发送，可用 job_status 查询进度",
        "job_id": job_id,
        "status": "pending",
        "detail": prepared_result,
    }


def get_network_send_file_job(job_id):
    if not job_id:
        return build_failure("请指定 job_id")
    job_file = network_job_file_path(job_id)
    if not os.path.isfile(job_file):
        return build_failure(f"未找到网络雕刻任务: {job_id}")
    try:
        return build_success(read_job_file(job_file))
    except (OSError, ValueError) as exc:
        return build_failure(f"读取网络雕刻任务状态失败: {exc}")


def cancel_network_send_file_job(job_id):
    if not job_id:
        return build_failure("请指定 job_id")
    job_file = network_job_file_path(job_id)
    if not os.path.isfile(job_file):
        return build_failure(f"未找到网络雕刻任务: {job_id}")
    try:
        job = read_job_file(job_file)
    except (OSError, ValueError) as exc:
        return build_failure(f"读取网络雕刻任务状态失败: {exc}")
    if job.get("status") in TERMINAL_JOB_STATUSES:
        return build_failure(f"任务已结束，不能中断: {job.get('status')}", job)

    process_id = job.get("process_id")
    kill_error = None
    if process_id:
        try:
            os.kill(int(process_id), signal.SIGTERM)
        except OSError as exc:
            kill_error = str(exc)
    result = {
        "success": True,
        "result": "网络雕刻任务已中断，不会继续发送后续 G-code",
        "job_id": job_id,
        "process_id": process_id,
    }
    if kill_error:
        result["kill_error"] = kill_error
    updated = update_job_file(
        job_file,
        status="cancelled",
        finished_at=time.time(),
        result=result,
    )
    return build_success("网络雕刻任务已中断", updated)


def run_network_send_file_worker(job_file):
    try:
        job = update_job_file(job_file, status="running", started_at=time.time())
        prepared = dict(job.get("prepared") or {})
        expected = str(job.get("expected_gcode_sha256") or prepared.get("expected_gcode_sha256") or "").strip()
        if expected:
            prepared["expected_gcode_sha256"] = expected
        # Worker owns the sole online probe after open+hash verification.
        result = execute_network_send_file(
            prepared,
            host=job.get("host", ""),
            transport=job.get("transport", "http"),
            http_port=job.get("http_port", DEFAULT_HTTP_PORT),
            telnet_port=job.get("telnet_port", DEFAULT_TELNET_PORT),
            timeout=job.get("timeout", DEFAULT_TIMEOUT),
            wait_for_response=job.get("wait_for_response", True),
            skip_online_probe=False,
        )
        update_job_file(
            job_file,
            status="completed" if result.get("success") else "failed",
            finished_at=time.time(),
            result=result,
        )
        return 0 if result.get("success") else 1
    except Exception as exc:
        try:
            update_job_file(
                job_file,
                status="failed",
                finished_at=time.time(),
                result=build_failure(f"后台网络雕刻任务异常: {exc}"),
            )
        except Exception:
            pass
        return 1


def run_send_file_worker(mode, job_file):
    mode, error = normalize_connection_mode(mode)
    if error:
        return 2
    if not job_file:
        return 2
    if mode == "serial":
        return run_serial_send_file_worker(job_file)
    return run_network_send_file_worker(job_file)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] != "--send-file-worker":
        return 2
    if len(args) != 3:
        return 2
    return run_send_file_worker(args[1], args[2])


if __name__ == "__main__":
    sys.exit(main())
