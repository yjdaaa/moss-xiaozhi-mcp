r'''
Author: yjdaaa 1945409496@qq.com
Date: 2026-04-15 20:33:35
LastEditors: yjdaaa 1945409496@qq.com
LastEditTime: 2026-04-16 19:10:54
FilePath: \moss-xiaozhi-mcp-main\tools\open_app_tool.py
Description: 这是默认设置,请设置`customMade`, 打开koroFileHeader查看配置 进行设置: https://github.com/OBKoro1/koro1FileHeader/wiki/%E9%85%8D%E7%BD%AE
'''
import subprocess
import platform

system = platform.system()
JSJDS_OFFICIAL_WEBSITE_URL = "https://jsjds.blcu.edu.cn/index.htm"
LEGACY_JSJDS_WEBSITE_URLS = {
    "http://www.44g.com.cn",
    "https://www.44g.com.cn",
    "http://44g.com.cn",
    "https://44g.com.cn",
}
JSJDS_WEBSITE_ALIASES = {
    "4c",
    "打开4c",
    "中国大学生计算机设计大赛",
    "中国大学生计算机设计大赛官网",
    "中国大学生计算机设计大赛官方",
    "打开中国大学生计算机设计大赛",
    "打开中国大学生计算机设计大赛官网",
    "打开中国大学生计算机设计大赛官方",
    "大学生计算机设计大赛",
    "大学生计算机设计大赛官网",
    "打开大学生计算机设计大赛",
    "打开大学生计算机设计大赛官网",
}

# Windows 应用名称到路径的映射，根据自己电脑实际安装路径修改
WINDOWS_APP_MAP = {
    'terminal': 'cmd.exe',
    'cmd': 'cmd.exe',
    'powershell': 'powershell.exe',
    '微信': r'C:\Program Files (x86)\Tencent\WeChat\WeChat.exe',
    'WeChat': r'C:\Program Files (x86)\Tencent\WeChat\WeChat.exe',
    '企业微信': r'C:\Program Files (x86)\WXWork\WXWork.exe',
    '浏览器': r'C:\Program Files\Google\Chrome\Application\chrome.exe',
    'Chrome': r'C:\Program Files\Google\Chrome\Application\chrome.exe',
    'Edge': r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
    '汽水音乐': r'C:\Program Files\汽水音乐\汽水音乐.exe',
    'VSCode': 'code',
    'LaserGRBL': 'LaserGRBL',  # 依赖 PATH 或 LASERGRBL_EXE 环境变量
}

def _resolve_windows_app(argument):
    """优先从映射表查找，找不到则当作路径直接执行"""
    return WINDOWS_APP_MAP.get(argument, argument)

def _normalize_site_alias(argument: str, payload: str):
    if payload in LEGACY_JSJDS_WEBSITE_URLS:
        return '浏览器', JSJDS_OFFICIAL_WEBSITE_URL, JSJDS_OFFICIAL_WEBSITE_URL

    if payload != '':
        return argument, payload, None

    normalized_argument = argument.replace(" ", "").lower()
    if normalized_argument in JSJDS_WEBSITE_ALIASES:
        return '浏览器', JSJDS_OFFICIAL_WEBSITE_URL, JSJDS_OFFICIAL_WEBSITE_URL

    return argument, payload, None

def _build_open_result(website_url):
    if website_url == JSJDS_OFFICIAL_WEBSITE_URL:
        return {
            "success": True,
            "result": "已打开中国大学生计算机设计大赛官网",
            "url": JSJDS_OFFICIAL_WEBSITE_URL,
        }

    return {"success": True, "result": "已启动应用"}

def _build_error_result(message, website_url):
    result = {"success": False, "result": message}
    if website_url:
        result["url"] = website_url
    return result

def _open_app(argument: str, payload='', current_system=None, run=None, popen=None) -> dict:
    if run is None:
        run = subprocess.run
    if popen is None:
        popen = subprocess.Popen
    if current_system is None:
        current_system = system

    argument = argument.strip()
    argument, payload, website_url = _normalize_site_alias(argument, payload)
    try:
        if current_system == 'Darwin':
            if payload != '':
                run(["open", "-a", argument, payload], check=True)
            else:
                run(["open", "-a", argument], check=True)
        elif current_system == 'Windows':
            exe_path = _resolve_windows_app(argument)
            if payload != '':
                popen([exe_path, payload])
            else:
                popen([exe_path])
        else:
            return _build_error_result("当前系统不支持该功能", website_url)
        return _build_open_result(website_url)
    except subprocess.CalledProcessError as e:
        return _build_error_result(str(e), website_url)
    except Exception as e:
        return _build_error_result(str(e), website_url)

def register_tool(mcp):
    @mcp.tool()
    def open_app_tool(argument: str, payload='') -> dict:
        """
        提供打开电脑应用的功能。
        argument 为应用名称或路径，payload 为可选参数（如 URL）。

        Windows 常用应用名称（可直接使用的别名）：
            终端: 'terminal' 或 'cmd'
            PowerShell: 'powershell'
            微信: '微信' 或 'WeChat'
            企业微信: '企业微信'
            浏览器/Chrome: '浏览器' 或 'Chrome'
            Edge: 'Edge'
            汽水音乐: '汽水音乐'
            VSCode: 'VSCode'
        如需打开指定网站，提供第二个参数 payload（URL），例如：
            打开B站搜索: open_app_tool('Chrome', 'https://search.bilibili.com/all?keyword=搜索内容')
            打开淘宝搜索: open_app_tool('Chrome', 'https://s.taobao.com/search?q=商品名称')
            打开Bing搜索: open_app_tool('Chrome', 'https://cn.bing.com/search?q=搜索问题')
            打开中国大学生计算机设计大赛官网: open_app_tool('浏览器', 'https://jsjds.blcu.edu.cn/index.htm')
            或直接说/传入: open_app_tool('打开4c')
            如果上游仍传入旧地址 https://www.44g.com.cn，也会自动改写为中国大学生计算机设计大赛官网
        如果应用不在列表中，可直接传入完整路径，如 'C:\\xxx\\app.exe'
        """
        return _open_app(argument, payload)

    @mcp.tool()
    def open_jsjds_website_tool() -> dict:
        """
        打开中国大学生计算机设计大赛官网。当用户想访问中国大学生计算机设计大赛官网、官方主页时使用此工具。
        """
        return open_app_tool('打开4c')
