import unittest
from unittest.mock import Mock, patch

from tools import open_app_tool


class FakeMcp:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorator(func):
            self.tools[func.__name__] = func
            return func

        return decorator


class OpenAppToolTests(unittest.TestCase):
    def test_open_app_uses_windows_browser_alias_with_payload(self):
        popen = Mock()

        result = open_app_tool._open_app(
            "浏览器",
            open_app_tool.JSJDS_OFFICIAL_WEBSITE_URL,
            current_system="Windows",
            popen=popen,
        )

        self.assertTrue(result["success"], result)
        popen.assert_called_once_with([
            open_app_tool.WINDOWS_APP_MAP["浏览器"],
            open_app_tool.JSJDS_OFFICIAL_WEBSITE_URL,
        ])

    def test_open_app_opens_jsjds_website_from_natural_phrase(self):
        popen = Mock()

        result = open_app_tool._open_app(
            "打开4c",
            current_system="Windows",
            popen=popen,
        )

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"], "已打开中国大学生计算机设计大赛官网")
        self.assertEqual(result["url"], open_app_tool.JSJDS_OFFICIAL_WEBSITE_URL)
        popen.assert_called_once_with([
            open_app_tool.WINDOWS_APP_MAP["浏览器"],
            open_app_tool.JSJDS_OFFICIAL_WEBSITE_URL,
        ])

    def test_open_app_rewrites_legacy_44g_url(self):
        popen = Mock()

        result = open_app_tool._open_app(
            "浏览器",
            "https://www.44g.com.cn",
            current_system="Windows",
            popen=popen,
        )

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"], "已打开中国大学生计算机设计大赛官网")
        self.assertEqual(result["url"], open_app_tool.JSJDS_OFFICIAL_WEBSITE_URL)
        popen.assert_called_once_with([
            open_app_tool.WINDOWS_APP_MAP["浏览器"],
            open_app_tool.JSJDS_OFFICIAL_WEBSITE_URL,
        ])

    def test_open_app_reports_unsupported_system(self):
        result = open_app_tool._open_app("浏览器", current_system="Linux")

        self.assertFalse(result["success"])
        self.assertIn("当前系统不支持", result["result"])

    def test_open_app_reports_popen_exception(self):
        def failing_popen(args):
            raise FileNotFoundError("missing browser")

        result = open_app_tool._open_app(
            "浏览器",
            open_app_tool.JSJDS_OFFICIAL_WEBSITE_URL,
            current_system="Windows",
            popen=failing_popen,
        )

        self.assertFalse(result["success"])
        self.assertIn("missing browser", result["result"])

    def test_register_tool_adds_jsjds_website_tool_via_open_app_tool(self):
        fake_mcp = FakeMcp()
        popen = Mock()

        open_app_tool.register_tool(fake_mcp)
        with patch.object(open_app_tool.subprocess, "Popen", popen):
            result = fake_mcp.tools["open_jsjds_website_tool"]()

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"], "已打开中国大学生计算机设计大赛官网")
        self.assertEqual(result["url"], open_app_tool.JSJDS_OFFICIAL_WEBSITE_URL)
        popen.assert_called_once_with([
            open_app_tool.WINDOWS_APP_MAP["浏览器"],
            open_app_tool.JSJDS_OFFICIAL_WEBSITE_URL,
        ])


if __name__ == "__main__":
    unittest.main()
