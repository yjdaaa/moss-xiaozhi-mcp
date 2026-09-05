import json
import os
import subprocess
import time

from core.laser_runtime.config import get_laser_settings


DEFAULT_LASERGRBL_PATH = os.environ.get(
    "LASERGRBL_EXE",
    "",  # 留空表示从 PATH 查找，或用 LASERGRBL_EXE 环境变量指定安装位置
)
DEFAULT_IMAGE_FILE = get_laser_settings().default_image_file
DEFAULT_WINDOW_TITLE = os.environ.get("LASERGRBL_WINDOW_TITLE", "LaserGRBL")
IMAGE_EXTENSIONS = (".bmp", ".jpg", ".jpeg", ".png", ".gif")
WINDOW_TITLE_EXCLUDES = ("file explorer", "文件资源管理器", "explorer.exe")
CB_SELECTSTRING = 0x014D
CB_ERR = -1
CALIBRATION_FILE = os.environ.get(
    "LASERGRBL_GUI_CALIBRATION_FILE",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), ".runtime", "lasergrbl_gui_calibration.json"),
)
CALIBRATION_TARGETS = {
    "port": "port",
    "port_dropdown": "port",
    "串口": "port",
    "串口下拉框": "port",
    "端口": "port",
    "端口下拉框": "port",
    "connect": "connect",
    "connect_button": "connect",
    "连接": "connect",
    "连接按钮": "connect",
    "连接设备": "connect",
    "start": "start",
    "start_button": "start",
    "开始": "start",
    "开始按钮": "start",
    "启动": "start",
    "启动按钮": "start",
}


def _load_pyautogui():
    try:
        import pyautogui
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.15
        return pyautogui, None
    except Exception as exc:
        return None, f"缺少或无法加载 pyautogui: {exc}"


def _normalize_path(path):
    return os.path.normpath(os.path.expandvars(os.path.expanduser(path)))


def _validate_file(path, extensions=None):
    normalized = _normalize_path(path or DEFAULT_IMAGE_FILE)
    if not normalized:
        return None, "请提供文件路径"

    if not os.path.isfile(normalized):
        return normalized, f"文件不存在: {normalized}"

    if extensions and not normalized.lower().endswith(extensions):
        return normalized, f"文件类型不支持: {normalized}，支持: {', '.join(extensions)}"

    return normalized, None


def _validate_lasergrbl_path(path):
    normalized = _normalize_path(path or DEFAULT_LASERGRBL_PATH)
    if not os.path.isfile(normalized):
        return normalized, f"LaserGRBL 程序不存在: {normalized}"
    return normalized, None


def _find_window(pyautogui, title, timeout_seconds=10):
    deadline = time.time() + max(timeout_seconds, 0)
    while True:
        windows = [
            win
            for win in pyautogui.getWindowsWithTitle(title)
            if _is_lasergrbl_window(win, title)
        ]
        if windows:
            return windows[0], None
        if time.time() >= deadline:
            return None, f"未找到窗口标题包含 {title!r} 的 LaserGRBL 窗口"
        time.sleep(0.5)


def _is_lasergrbl_window(window, title):
    window_title = getattr(window, "title", "")
    if not window_title or title.lower() not in window_title.lower():
        return False

    lowered = window_title.lower()
    return not any(excluded in lowered for excluded in WINDOW_TITLE_EXCLUDES)


def _focus_window(window):
    try:
        if window.isMinimized:
            window.restore()
        window.activate()
        time.sleep(0.5)
        return None
    except Exception as exc:
        return f"无法激活 LaserGRBL 窗口: {exc}"


def _paste_text(pyautogui, text):
    try:
        import tkinter

        root = tkinter.Tk()
        root.withdraw()
        root.clipboard_clear()
        root.clipboard_append(text)
        root.update()
        root.destroy()
        pyautogui.hotkey("ctrl", "v")
        return None
    except Exception:
        try:
            pyautogui.write(text, interval=0.01)
            return None
        except Exception as exc:
            return f"无法输入路径: {exc}"


def _press_hotkey(pyautogui, hotkey):
    keys = [key.strip().lower() for key in hotkey.split("+") if key.strip()]
    if not keys:
        return "热键不能为空"

    try:
        if len(keys) == 1:
            pyautogui.press(keys[0])
        else:
            pyautogui.hotkey(*keys)
        return None
    except Exception as exc:
        return f"执行热键 {hotkey!r} 失败: {exc}"


def _normalize_port(port):
    return (port or "").strip().upper()


def _normalize_calibration_target(target):
    normalized = (target or "").strip().lower()
    return CALIBRATION_TARGETS.get(normalized)


def _load_calibration(path=CALIBRATION_FILE):
    try:
        if not os.path.isfile(path):
            return {}
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_calibration(data, path=CALIBRATION_FILE):
    try:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.write("\n")
        return None
    except OSError as exc:
        return f"保存坐标失败: {exc}"


def _position_xy(position):
    if hasattr(position, "x") and hasattr(position, "y"):
        return position.x, position.y
    return position[0], position[1]


def _remember_position(pyautogui, window, target, path=CALIBRATION_FILE):
    normalized_target = _normalize_calibration_target(target)
    if not normalized_target:
        return None, "请提供 target，支持: port/connect/start，或中文：串口下拉框/连接按钮/开始按钮"

    absolute_x, absolute_y = _position_xy(pyautogui.position())
    rel_x = int(absolute_x - window.left)
    rel_y = int(absolute_y - window.top)
    calibration = _load_calibration(path)
    entry = {
        "rel_x": rel_x,
        "rel_y": rel_y,
        "absolute_x": int(absolute_x),
        "absolute_y": int(absolute_y),
        "window_title": getattr(window, "title", ""),
        "window_left": int(window.left),
        "window_top": int(window.top),
        "window_width": int(getattr(window, "width", 0)),
        "window_height": int(getattr(window, "height", 0)),
        "saved_at": int(time.time()),
    }
    calibration[normalized_target] = entry

    error = _save_calibration(calibration, path)
    if error:
        return None, error

    return entry, None


def _saved_coordinates(target, path=CALIBRATION_FILE):
    calibration = _load_calibration(path)
    entry = calibration.get(target)
    if not isinstance(entry, dict):
        return -1, -1

    try:
        rel_x = int(entry.get("rel_x", -1))
        rel_y = int(entry.get("rel_y", -1))
    except (TypeError, ValueError):
        return -1, -1

    if rel_x < 0 or rel_y < 0:
        return -1, -1
    return rel_x, rel_y


def _launch_lasergrbl(lasergrbl_path, wait_seconds):
    try:
        subprocess.Popen([lasergrbl_path])
        time.sleep(max(wait_seconds, 0))
        return None
    except Exception as exc:
        return f"启动 LaserGRBL 失败: {exc}"


def _ensure_lasergrbl_window(pyautogui, lasergrbl_path, window_title, wait_seconds):
    window, _ = _find_window(pyautogui, window_title, timeout_seconds=1)
    if window is None:
        error = _launch_lasergrbl(lasergrbl_path, wait_seconds)
        if error:
            return None, error

    window, error = _find_window(pyautogui, window_title, timeout_seconds=max(wait_seconds, 5))
    if error:
        return None, error

    error = _focus_window(window)
    if error:
        return None, error

    return window, None


def _open_image_file(pyautogui, image_file, open_hotkey, wizard_confirm_presses, wizard_delay_seconds):
    error = _press_hotkey(pyautogui, open_hotkey)
    if error:
        return error

    time.sleep(1)
    error = _paste_text(pyautogui, image_file)
    if error:
        return error

    pyautogui.press("enter")
    time.sleep(max(wizard_delay_seconds, 0))

    for _ in range(max(wizard_confirm_presses, 0)):
        pyautogui.press("enter")
        time.sleep(max(wizard_delay_seconds, 0))

    return None


def _click_relative(pyautogui, window, rel_x, rel_y):
    try:
        x = window.left + rel_x
        y = window.top + rel_y
        pyautogui.click(x, y)
        return None
    except Exception as exc:
        return f"点击 LaserGRBL 相对坐标失败: {exc}"


def _select_port_by_win32(window, port):
    try:
        import win32gui
    except ImportError:
        return False, "当前环境缺少 win32gui，无法自动查找串口下拉框"

    hwnd = getattr(window, "_hWnd", None) or getattr(window, "hWnd", None)
    if not hwnd:
        return False, "无法获取 LaserGRBL 窗口句柄"

    combo_hwnds = []

    def collect_combo(child_hwnd, _):
        class_name = win32gui.GetClassName(child_hwnd).lower()
        if "combobox" in class_name:
            combo_hwnds.append(child_hwnd)
        return True

    try:
        win32gui.EnumChildWindows(hwnd, collect_combo, None)
        for combo_hwnd in combo_hwnds:
            result = win32gui.SendMessage(combo_hwnd, CB_SELECTSTRING, -1, port)
            if result != CB_ERR:
                win32gui.SetFocus(combo_hwnd)
                return True, None
    except Exception as exc:
        return False, f"自动选择串口失败: {exc}"

    return False, f"未在 LaserGRBL 窗口的串口下拉框中找到 {port}"


def _select_port(pyautogui, window, port, port_hotkey, port_rel_x, port_rel_y, port_select_delay_seconds):
    normalized_port = _normalize_port(port)
    if not normalized_port:
        return "请提供 port，例如 COM3"

    selected, win32_error = _select_port_by_win32(window, normalized_port)
    if selected:
        time.sleep(max(port_select_delay_seconds, 0))
        return None

    if port_hotkey:
        error = _press_hotkey(pyautogui, port_hotkey)
        if error:
            return error
    else:
        if port_rel_x < 0 or port_rel_y < 0:
            port_rel_x, port_rel_y = _saved_coordinates("port")

        if port_rel_x < 0 or port_rel_y < 0:
            return (
                f"{win32_error}；请先把鼠标移到串口下拉框上，调用 remember_position 并设置 target=port，"
                "或手动传 port_rel_x/port_rel_y"
            )

        error = _click_relative(pyautogui, window, port_rel_x, port_rel_y)
        if error:
            return error

    time.sleep(max(port_select_delay_seconds, 0))

    try:
        pyautogui.hotkey("ctrl", "a")
        pyautogui.write(normalized_port, interval=0.02)
        pyautogui.press("enter")
        time.sleep(max(port_select_delay_seconds, 0))
        return None
    except Exception as exc:
        return f"输入串口号 {normalized_port} 失败: {exc}"


def register_tool(mcp):
    @mcp.tool()
    def lasergrbl_gui_tool(
        action: str,
        target: str = "",
        port: str = "",
        image_file: str = "",
        lasergrbl_path: str = "",
        window_title: str = DEFAULT_WINDOW_TITLE,
        wait_seconds: float = 8,
        open_hotkey: str = "ctrl+o",
        wizard_confirm_presses: int = 2,
        wizard_delay_seconds: float = 1,
        port_hotkey: str = "",
        port_rel_x: int = -1,
        port_rel_y: int = -1,
        port_select_delay_seconds: float = 0.3,
        confirm_connect: bool = False,
        connect_hotkey: str = "",
        connect_rel_x: int = -1,
        connect_rel_y: int = -1,
        confirm_start: bool = False,
        start_hotkey: str = "",
        start_rel_x: int = -1,
        start_rel_y: int = -1,
    ) -> dict:
        """
        自动操作 LaserGRBL 桌面软件的辅助工具。

        action:
            'status' - 查看 LaserGRBL 路径和窗口状态
            'open_app' - 启动并聚焦 LaserGRBL
            'remember_position' - 记录当前鼠标相对 LaserGRBL 窗口的坐标，target 支持 port/connect/start 或中文目标名
            'select_port' - 在 LaserGRBL 界面选择指定串口，不直接连接串口
            'connect_device' - 通过 LaserGRBL 界面连接设备；可先传 port 自动选串口
            'open_image' - 打开 BMP/JPG/PNG/GIF 图片，并按回车走完导入向导
            'engrave_image' - 打开图片；只有 confirm_start=true 且提供启动方式时才会点击开始
            'mouse_position' - 获取当前鼠标相对 LaserGRBL 窗口左上角的坐标
            'start_job' - 点击开始雕刻，需要 confirm_start=true，并提供 start_hotkey 或相对坐标

        target: remember_position 要保存的位置类型，支持 port/connect/start，也支持 串口下拉框/连接按钮/开始按钮
        port: 要在 LaserGRBL 界面选择的串口号，如 COM3；工具只操作界面，不直接打开串口
        image_file: 要导入 LaserGRBL 的图片路径；不传则使用 .env 中的 LASERGRBL_DEFAULT_IMAGE
        lasergrbl_path: LaserGRBL.exe 路径；不传则用 LASERGRBL_EXE 环境变量或从 PATH 查找
        open_hotkey: 打开文件热键，默认 ctrl+o
        wizard_confirm_presses: 图片导入向导中自动按回车的次数，默认 2（Next/Create）
        port_hotkey: 聚焦串口下拉框的热键；自动查找控件失败时可用
        port_rel_x/port_rel_y: 串口下拉框相对 LaserGRBL 窗口左上角的坐标
        port_select_delay_seconds: 选择串口后的等待时间
        confirm_connect: 安全确认；不为 true 时不会点击连接按钮
        connect_hotkey: 连接设备热键，如你已在 LaserGRBL 中配置，可传入对应热键
        connect_rel_x/connect_rel_y: 连接按钮相对 LaserGRBL 窗口左上角的坐标
        confirm_start: 安全确认；不为 true 时不会真正点击开始雕刻
        start_hotkey: 启动雕刻热键，如你已在 LaserGRBL 中配置，可传入 ctrl+enter
        start_rel_x/start_rel_y: 开始按钮相对 LaserGRBL 窗口左上角的坐标，用于点击开始按钮
        """
        pyautogui, load_error = _load_pyautogui()
        if load_error:
            return {"success": False, "result": load_error}

        lasergrbl_path, path_error = _validate_lasergrbl_path(lasergrbl_path)
        if path_error:
            return {"success": False, "result": path_error}

        try:
            if action == "status":
                windows = [
                    {"title": win.title, "left": win.left, "top": win.top, "width": win.width, "height": win.height}
                    for win in pyautogui.getWindowsWithTitle(window_title)
                    if _is_lasergrbl_window(win, window_title)
                ]
                return {
                    "success": True,
                    "result": {
                        "lasergrbl_path": lasergrbl_path,
                        "lasergrbl_exists": os.path.isfile(lasergrbl_path),
                        "default_image_file": _normalize_path(DEFAULT_IMAGE_FILE),
                        "default_image_exists": os.path.isfile(_normalize_path(DEFAULT_IMAGE_FILE)),
                        "calibration_file": CALIBRATION_FILE,
                        "saved_positions": _load_calibration(),
                        "window_title": window_title,
                        "windows": windows,
                    },
                }

            if action == "mouse_position":
                window, error = _find_window(pyautogui, window_title, timeout_seconds=1)
                if error:
                    return {"success": False, "result": error}

                position = pyautogui.position()
                return {
                    "success": True,
                    "result": {
                        "absolute_x": position.x,
                        "absolute_y": position.y,
                        "rel_x": position.x - window.left,
                        "rel_y": position.y - window.top,
                        "port_rel_x": position.x - window.left,
                        "port_rel_y": position.y - window.top,
                        "connect_rel_x": position.x - window.left,
                        "connect_rel_y": position.y - window.top,
                        "start_rel_x": position.x - window.left,
                        "start_rel_y": position.y - window.top,
                        "window_title": window.title,
                        "window_left": window.left,
                        "window_top": window.top,
                    },
                }

            if action in ("open_app", "focus"):
                _, error = _ensure_lasergrbl_window(
                    pyautogui,
                    lasergrbl_path,
                    window_title,
                    wait_seconds,
                )
                if error:
                    return {"success": False, "result": error}
                return {"success": True, "result": "LaserGRBL 已启动并聚焦"}

            if action == "remember_position":
                window, error = _ensure_lasergrbl_window(
                    pyautogui,
                    lasergrbl_path,
                    window_title,
                    wait_seconds,
                )
                if error:
                    return {"success": False, "result": error}

                entry, error = _remember_position(pyautogui, window, target)
                if error:
                    return {"success": False, "result": error}

                return {
                    "success": True,
                    "result": {
                        "target": _normalize_calibration_target(target),
                        "saved": entry,
                        "calibration_file": CALIBRATION_FILE,
                    },
                }

            if action == "select_port":
                window, error = _ensure_lasergrbl_window(
                    pyautogui,
                    lasergrbl_path,
                    window_title,
                    wait_seconds,
                )
                if error:
                    return {"success": False, "result": error}

                error = _select_port(
                    pyautogui,
                    window,
                    port,
                    port_hotkey,
                    port_rel_x,
                    port_rel_y,
                    port_select_delay_seconds,
                )
                if error:
                    return {"success": False, "result": error}

                return {"success": True, "result": f"已通过 LaserGRBL 界面选择串口 {_normalize_port(port)}"}

            if action == "connect_device":
                if not confirm_connect:
                    return {"success": False, "result": "安全限制：连接设备必须传 confirm_connect=true"}

                window, error = _ensure_lasergrbl_window(
                    pyautogui,
                    lasergrbl_path,
                    window_title,
                    wait_seconds,
                )
                if error:
                    return {"success": False, "result": error}

                if port:
                    error = _select_port(
                        pyautogui,
                        window,
                        port,
                        port_hotkey,
                        port_rel_x,
                        port_rel_y,
                        port_select_delay_seconds,
                    )
                    if error:
                        return {"success": False, "result": error}

                error = _connect_device(
                    pyautogui,
                    window,
                    connect_hotkey,
                    connect_rel_x,
                    connect_rel_y,
                )
                if error:
                    return {"success": False, "result": error}

                return {"success": True, "result": "已通过 LaserGRBL 界面执行连接设备操作"}

            if action in ("open_image", "engrave_image"):
                image_file, error = _validate_file(image_file, IMAGE_EXTENSIONS)
                if error:
                    return {"success": False, "result": error}

                window, error = _ensure_lasergrbl_window(
                    pyautogui,
                    lasergrbl_path,
                    window_title,
                    wait_seconds,
                )
                if error:
                    return {"success": False, "result": error}

                error = _open_image_file(
                    pyautogui,
                    image_file,
                    open_hotkey,
                    wizard_confirm_presses,
                    wizard_delay_seconds,
                )
                if error:
                    return {"success": False, "result": error}

                if action == "open_image" or not confirm_start:
                    return {
                        "success": True,
                        "result": "图片已尝试导入 LaserGRBL。为安全起见，未点击开始雕刻；如需开始，请调用 start_job 并设置 confirm_start=true。",
                    }

                start_result = _start_job(
                    pyautogui,
                    window,
                    start_hotkey,
                    start_rel_x,
                    start_rel_y,
                )
                if start_result:
                    return {"success": False, "result": start_result}

                return {"success": True, "result": "图片已导入，并已执行开始雕刻操作"}

            if action == "start_job":
                if not confirm_start:
                    return {"success": False, "result": "安全限制：开始雕刻必须传 confirm_start=true"}

                window, error = _ensure_lasergrbl_window(
                    pyautogui,
                    lasergrbl_path,
                    window_title,
                    wait_seconds,
                )
                if error:
                    return {"success": False, "result": error}

                error = _start_job(
                    pyautogui,
                    window,
                    start_hotkey,
                    start_rel_x,
                    start_rel_y,
                )
                if error:
                    return {"success": False, "result": error}

                return {"success": True, "result": "已执行开始雕刻操作"}

            return {
                "success": False,
                "result": "未知 action，支持: status / open_app / focus / remember_position / select_port / connect_device / open_image / engrave_image / mouse_position / start_job",
            }
        except pyautogui.FailSafeException:
            return {"success": False, "result": "pyautogui 安全停止：鼠标移动到屏幕角落触发了 FailSafe"}
        except Exception as exc:
            return {"success": False, "result": str(exc)}


def _start_job(pyautogui, window, start_hotkey, start_rel_x, start_rel_y):
    return _trigger_window_action(
        pyautogui,
        window,
        start_hotkey,
        start_rel_x,
        start_rel_y,
        "缺少开始方式：请提供 start_hotkey，或提供 start_rel_x/start_rel_y（相对 LaserGRBL 窗口左上角的开始按钮坐标）",
        "start",
    )


def _connect_device(pyautogui, window, connect_hotkey, connect_rel_x, connect_rel_y):
    return _trigger_window_action(
        pyautogui,
        window,
        connect_hotkey,
        connect_rel_x,
        connect_rel_y,
        "缺少连接方式：请先把鼠标移到连接按钮上，调用 remember_position 并设置 target=connect，或手动传 connect_hotkey/connect_rel_x/connect_rel_y",
        "connect",
    )


def _trigger_window_action(pyautogui, window, hotkey, rel_x, rel_y, missing_message, calibration_target=None):
    if hotkey:
        return _press_hotkey(pyautogui, hotkey)

    if (rel_x < 0 or rel_y < 0) and calibration_target:
        rel_x, rel_y = _saved_coordinates(calibration_target)

    if rel_x >= 0 and rel_y >= 0:
        return _click_relative(pyautogui, window, rel_x, rel_y)

    return missing_message
