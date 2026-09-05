import os
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageFont

from tools import text_image_tool


class FakeMcp:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorator(func):
            self.tools[func.__name__] = func
            return func

        return decorator


class TextImageToolTests(unittest.TestCase):
    def test_resolve_output_file_defaults_to_png(self):
        output_file, error = text_image_tool._resolve_output_file("")

        self.assertIsNone(error)
        self.assertTrue(output_file.endswith("text_image.png"))

    def test_resolve_output_file_rejects_jpeg(self):
        _, error = text_image_tool._resolve_output_file("name.jpg")

        self.assertIn("JPEG", error)

    def test_resolve_font_path_prefers_first_available_candidate(self):
        with patch.object(text_image_tool.os.path, "exists", side_effect=lambda path: path == "B"):
            with patch.object(text_image_tool, "_get_font_candidates", return_value=["A", "B", "C"]):
                font_path, error = text_image_tool._resolve_font_path(current_system="Windows")

        self.assertIsNone(error)
        self.assertEqual(font_path, "B")

    def test_create_text_image_generates_binary_image(self):
        fallback_font = ImageFont.load_default()

        with tempfile.TemporaryDirectory() as temp_dir:
            output_file = os.path.join(temp_dir, "name.png")
            with patch.object(text_image_tool, "_resolve_font_path", return_value=("fake-font", None)):
                with patch("PIL.ImageFont.truetype", return_value=fallback_font):
                    result = text_image_tool.create_text_image(
                        text="AA",
                        output_file=output_file,
                        width=128,
                        height=128,
                        font_size=48,
                    )

            self.assertTrue(result["success"], result)
            image = Image.open(output_file).convert("RGB")
            colors = {
                image.getpixel((x, y))
                for x in range(image.width)
                for y in range(image.height)
            }

        self.assertLessEqual(len(colors), 2)
        self.assertIn((255, 255, 255), colors)
        self.assertIn((0, 0, 0), colors)

    def test_create_text_image_supports_multiline_text(self):
        fallback_font = ImageFont.load_default()

        with tempfile.TemporaryDirectory() as temp_dir:
            output_file = os.path.join(temp_dir, "name.png")
            with patch.object(text_image_tool, "_resolve_font_path", return_value=("fake-font", None)):
                with patch("PIL.ImageFont.truetype", return_value=fallback_font):
                    result = text_image_tool.create_text_image(
                        text="AAA\nBBB",
                        output_file=output_file,
                        width=160,
                        height=160,
                        font_size=48,
                        line_spacing=0.2,
                    )

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"]["line_count"], 2)
        self.assertEqual(result["result"]["lines"], ["AAA", "BBB"])
        self.assertEqual(result["result"]["layout"]["mode"], "manual")

    def test_create_text_image_auto_wraps_long_text(self):
        fallback_font = ImageFont.load_default()

        with tempfile.TemporaryDirectory() as temp_dir:
            output_file = os.path.join(temp_dir, "wrapped.png")
            with patch.object(text_image_tool, "_resolve_font_path", return_value=("fake-font", None)):
                with patch("PIL.ImageFont.truetype", return_value=fallback_font):
                    result = text_image_tool.create_text_image(
                        text="ABCDEFGHIJKL",
                        output_file=output_file,
                        width=55,
                        height=160,
                        font_size=18,
                        auto_wrap=True,
                    )

        self.assertTrue(result["success"], result)
        self.assertGreater(result["result"]["line_count"], 1)
        self.assertEqual("".join(result["result"]["lines"]), "ABCDEFGHIJKL")
        self.assertEqual(result["result"]["layout"]["mode"], "auto_wrap")

    def test_create_text_image_supports_target_line_count(self):
        fallback_font = ImageFont.load_default()

        with tempfile.TemporaryDirectory() as temp_dir:
            output_file = os.path.join(temp_dir, "balanced.png")
            with patch.object(text_image_tool, "_resolve_font_path", return_value=("fake-font", None)):
                with patch("PIL.ImageFont.truetype", return_value=fallback_font):
                    result = text_image_tool.create_text_image(
                        text="ABCDEFGHI",
                        output_file=output_file,
                        width=160,
                        height=160,
                        font_size=18,
                        max_lines=3,
                    )

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"]["line_count"], 3)
        self.assertEqual(result["result"]["lines"], ["ABC", "DEF", "GHI"])
        self.assertEqual(result["result"]["layout"]["mode"], "balanced")

    def test_create_text_image_reports_empty_text(self):
        result = text_image_tool.create_text_image(text="   ")

        self.assertFalse(result["success"])
        self.assertIn("不能为空", result["result"])

    def test_register_tool_exposes_generate_text_image_tool(self):
        fake_mcp = FakeMcp()
        text_image_tool.register_tool(fake_mcp)

        with patch.object(text_image_tool, "create_text_image", return_value={"success": True, "result": "ok"}) as create_mock:
            result = fake_mcp.tools["generate_text_image_tool"]("佳佳", width=512, height=512)

        self.assertEqual(result, {"success": True, "result": "ok"})
        create_mock.assert_called_once_with(
            text="佳佳",
            output_file="",
            width=512,
            height=512,
            font_size=text_image_tool.DEFAULT_FONT_SIZE,
            font_path="",
            line_spacing=text_image_tool.DEFAULT_LINE_SPACING,
            auto_wrap=text_image_tool.DEFAULT_AUTO_WRAP,
            max_lines=text_image_tool.DEFAULT_MAX_LINES,
            layout_mode=text_image_tool.DEFAULT_LAYOUT_MODE,
        )


if __name__ == "__main__":
    unittest.main()
