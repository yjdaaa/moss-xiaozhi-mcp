import subprocess
import platform

is_windows = platform.system() == 'Windows'

if is_windows:
    # Windows: 用 PowerShell SendKeys 模拟媒体键，不依赖 pyautogui
    SHORTCUT_MAP = {
        '播放音乐': '^ {ENTER}',        # 类似 playpause
        '暂停音乐': '^ {ENTER}',
        '切换下一首': '^ {RIGHT}',
        '切换上一首': '^ {LEFT}',
        '音乐音量加': '^ {UP}',
        '音乐音量减': '^ {DOWN}',
    }

    def _exec_powershell_sendkeys(keys):
        ps_cmd = f'(New-Object -COM WScript.Shell).SendKeys("{keys}")'
        subprocess.run(["powershell", "-Command", ps_cmd], check=True)
else:
    import pyautogui
    SHORTCUT_MAP = {
        '播放音乐': ('command', 'option', 'p'),
        '暂停音乐': ('command', 'option', 'p'),
        '切换下一首': ('command', 'option', 'right'),
        '切换上一首': ('command', 'option', 'left'),
        '音乐音量加': ('command', 'option', 'up'),
        '音乐音量减': ('command', 'option', 'down'),
    }

    def _exec_powershell_sendkeys(keys):
        pass  # macOS 不使用

def register_tool(mcp):
    @mcp.tool()
    def shortcut_key_execution_tool(action: str) -> dict:
        """
        当前工具适用于电脑端快捷键执行(注意仅允许下列已经注册的快捷键控制)，支持的快捷键控制如下:
        1、音乐播放快捷键控制:
            播放音乐: action='播放音乐'
            暂停音乐: action='暂停音乐'
            切换下一首: action='切换下一首'
            切换上一首: action='切换上一首'
            音乐音量加: action='音乐音量加'
            音乐音量减: action='音乐音量减'
        """
        try:
            keys = SHORTCUT_MAP.get(action)
            if keys is None:
                return {"success": False, "result": f'不支持的快捷键操作: {action}，支持的操作: {list(SHORTCUT_MAP.keys())}'}
            if is_windows:
                _exec_powershell_sendkeys(keys)
            else:
                pyautogui.hotkey(*keys)
            return {"success": True, "result": 'ok'}
        except Exception as e:
            return {"success": False, "result": str(e)}
