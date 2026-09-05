import unittest
from unittest.mock import patch

from tools import text_image_gcode_tool


class FakeMcp:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorator(func):
            self.tools[func.__name__] = func
            return func

        return decorator


class TextImageGcodeToolTests(unittest.TestCase):
    def test_create_text_image_gcode_chains_image_to_gcode_for_raster_engraving(self):
        image_result = {
            "success": True,
            "result": {
                "text": "佳佳",
                "output_file": "C:\\tmp\\name.png",
                "width": 512,
                "height": 512,
                "layout": {"mode": "manual", "line_count": 1, "lines": ["佳佳"]},
            },
        }
        gcode_result = {
            "success": True,
            "result": {
                "image_file": "C:\\tmp\\name.png",
                "gcode_file": "C:\\tmp\\name.gcode",
                "time_estimate": {"estimated_seconds": 559.1, "estimated_minutes": 9.32},
                "speech": "预计雕刻 9 分 19 秒",
            },
        }

        with (
            patch.object(
                text_image_gcode_tool.text_image_tool,
                "create_text_image",
                return_value=image_result,
            ) as image_mock,
            patch.object(
                text_image_gcode_tool.laser_grbl_tool,
                "convert_image_to_gcode",
                return_value=gcode_result,
            ) as gcode_mock,
        ):
            result = text_image_gcode_tool.create_text_image_gcode(
                text="佳佳",
                image_output_file="C:\\tmp\\name.png",
                gcode_output_file="C:\\tmp\\name.gcode",
                image_width=512,
                image_height=512,
                width_mm=30.0,
                laser_mode="engrave",
                engraving_mode="raster",
                auto_trim=False,
                dpi=200,
                offset_x_mm=3,
                offset_y_mm=4,
                overscan_mm=2,
                raster_scan_direction="vertical",
                auto_wrap=True,
                max_lines=2,
                layout_mode="auto_wrap",
            )

        self.assertTrue(result["success"], result)
        self.assertEqual(result["result"]["image_file"], "C:\\tmp\\name.png")
        self.assertEqual(result["result"]["gcode_file"], "C:\\tmp\\name.gcode")
        self.assertEqual(result["result"]["time_estimate"]["estimated_seconds"], 559.1)
        self.assertEqual(result["result"]["speech"], "预计雕刻 9 分 19 秒")
        self.assertEqual(result["result"]["text_layout"]["mode"], "manual")
        image_mock.assert_called_once_with(
            text="佳佳",
            output_file="C:\\tmp\\name.png",
            width=512,
            height=512,
            font_size=text_image_gcode_tool.text_image_tool.DEFAULT_FONT_SIZE,
            font_path="",
            auto_wrap=True,
            max_lines=2,
            layout_mode="auto_wrap",
        )
        gcode_mock.assert_called_once()
        self.assertEqual(gcode_mock.call_args.kwargs["image_file"], "C:\\tmp\\name.png")
        self.assertEqual(gcode_mock.call_args.kwargs["output_file"], "C:\\tmp\\name.gcode")
        self.assertEqual(gcode_mock.call_args.kwargs["width_mm"], 30.0)
        self.assertEqual(gcode_mock.call_args.kwargs["laser_mode"], "engrave")
        self.assertEqual(gcode_mock.call_args.kwargs["engraving_mode"], "raster")
        self.assertFalse(gcode_mock.call_args.kwargs["auto_trim"])
        self.assertEqual(gcode_mock.call_args.kwargs["dpi"], 200)
        self.assertEqual(gcode_mock.call_args.kwargs["offset_x_mm"], 3)
        self.assertEqual(gcode_mock.call_args.kwargs["offset_y_mm"], 4)
        self.assertEqual(gcode_mock.call_args.kwargs["overscan_mm"], 2)
        self.assertEqual(gcode_mock.call_args.kwargs["raster_scan_direction"], "vertical")
        self.assertFalse(gcode_mock.call_args.kwargs["bidirectional"])

    def test_create_text_image_gcode_passes_text_layout_options(self):
        image_result = {
            "success": True,
            "result": {
                "text": "AAA\nBBB\nCCC",
                "output_file": "C:\\tmp\\name.png",
                "layout": {"mode": "balanced", "line_count": 3, "lines": ["AAA", "BBB", "CCC"]},
            },
        }
        gcode_result = {"success": True, "result": {"gcode_file": "C:\\tmp\\name.gcode"}}

        with (
            patch.object(
                text_image_gcode_tool.text_image_tool,
                "create_text_image",
                return_value=image_result,
            ) as image_mock,
            patch.object(
                text_image_gcode_tool.laser_grbl_tool,
                "convert_image_to_gcode",
                return_value=gcode_result,
            ),
        ):
            result = text_image_gcode_tool.create_text_image_gcode(
                text="AAABBBCCC",
                auto_wrap=True,
                max_lines=3,
                layout_mode="balanced",
            )

        self.assertTrue(result["success"], result)
        self.assertEqual(image_mock.call_args.kwargs["auto_wrap"], True)
        self.assertEqual(image_mock.call_args.kwargs["max_lines"], 3)
        self.assertEqual(image_mock.call_args.kwargs["layout_mode"], "balanced")
        self.assertEqual(result["result"]["text_layout"]["line_count"], 3)

    def test_create_text_image_gcode_auto_wraps_long_outline_chinese_text(self):
        image_result = {
            "success": True,
            "result": {
                "text": "苏小鹏\n杨家德",
                "output_file": "C:\\tmp\\name.png",
                "width": 1024,
                "height": 1024,
            },
        }
        gcode_result = {
            "success": True,
            "result": {
                "image_file": "C:\\tmp\\name.png",
                "gcode_file": "C:\\tmp\\name.gcode",
            },
        }

        with (
            patch.object(
                text_image_gcode_tool.text_image_tool,
                "create_text_image",
                return_value=image_result,
            ) as image_mock,
            patch.object(
                text_image_gcode_tool,
                "_create_text_vector_outline_gcode",
                return_value=gcode_result,
            ) as vector_mock,
            patch.object(text_image_gcode_tool.laser_grbl_tool, "convert_image_to_gcode") as image_gcode_mock,
        ):
            result = text_image_gcode_tool.create_text_image_gcode(
                text="苏小鹏杨家德",
                engraving_mode="outline",
            )

        self.assertTrue(result["success"], result)
        self.assertEqual(image_mock.call_args.kwargs["text"], "苏小鹏\n杨家德")
        self.assertEqual(vector_mock.call_args.kwargs["height_mm"], 0.0)
        self.assertEqual(vector_mock.call_args.kwargs["width_mm"], 0.0)
        image_gcode_mock.assert_not_called()
        self.assertEqual(result["result"]["layout"]["lines"], ["苏小鹏", "杨家德"])
        self.assertEqual(result["result"]["layout"]["line_count"], 2)
        self.assertEqual(result["result"]["layout"]["physical_size_source"], "dpi")

    def test_create_text_image_gcode_honors_explicit_outline_target_char_height(self):
        image_result = {
            "success": True,
            "result": {
                "text": "张三李\n四",
                "output_file": "C:\\tmp\\name.png",
                "width": 1024,
                "height": 1024,
            },
        }
        gcode_result = {
            "success": True,
            "result": {
                "image_file": "C:\\tmp\\name.png",
                "gcode_file": "C:\\tmp\\name.gcode",
            },
        }

        with (
            patch.object(
                text_image_gcode_tool.text_image_tool,
                "create_text_image",
                return_value=image_result,
            ) as image_mock,
            patch.object(
                text_image_gcode_tool,
                "_create_text_vector_outline_gcode",
                return_value=gcode_result,
            ) as vector_mock,
        ):
            result = text_image_gcode_tool.create_text_image_gcode(
                text="张三李四",
                engraving_mode="outline",
                target_char_height_mm=22,
            )

        self.assertTrue(result["success"], result)
        self.assertEqual(image_mock.call_args.kwargs["text"], "张三李\n四")
        self.assertEqual(vector_mock.call_args.kwargs["width_mm"], 75.9)
        self.assertEqual(vector_mock.call_args.kwargs["height_mm"], 49.5)
        self.assertEqual(result["result"]["layout"]["physical_size_source"], "target_char_height_mm")

    def test_create_text_image_gcode_uses_vector_outline_for_text_outline_engraving(self):
        image_result = {
            "success": True,
            "result": {
                "text": "佳佳",
                "output_file": "C:\\tmp\\name.png",
                "width": 512,
                "height": 512,
                "font_path": "C:\\Windows\\Fonts\\simhei.ttf",
                "font_size": 320,
                "line_count": 1,
                "lines": ["佳佳"],
            },
        }
        gcode_result = {
            "success": True,
            "result": {
                "image_file": "C:\\tmp\\name.png",
                "gcode_file": "C:\\tmp\\name.gcode",
                "text_vector_outline": True,
                "mode": "outline",
            },
        }

        with (
            patch.object(
                text_image_gcode_tool.text_image_tool,
                "create_text_image",
                return_value=image_result,
            ),
            patch.object(
                text_image_gcode_tool,
                "_create_text_vector_outline_gcode",
                return_value=gcode_result,
            ) as vector_mock,
            patch.object(text_image_gcode_tool.laser_grbl_tool, "convert_image_to_gcode") as image_gcode_mock,
        ):
            result = text_image_gcode_tool.create_text_image_gcode(
                text="佳佳",
                gcode_output_file="C:\\tmp\\name.gcode",
                laser_mode="engrave",
                engraving_mode="outline",
                width_mm=30.0,
                height_mm=20.0,
                feed_rate=1500,
                travel_rate=3200,
                laser_max_power=650,
                auto_trim=False,
                auto_size=False,
                dpi=200,
                lock_aspect_ratio=False,
                offset_x_mm=3,
                offset_y_mm=4,
                safe_margin_mm=6,
            )

        self.assertTrue(result["success"], result)
        self.assertTrue(result["result"]["gcode"]["text_vector_outline"])
        image_gcode_mock.assert_not_called()
        vector_mock.assert_called_once()
        self.assertEqual(vector_mock.call_args.kwargs["image_payload"], image_result["result"])
        self.assertEqual(vector_mock.call_args.kwargs["image_file"], "C:\\tmp\\name.png")
        self.assertEqual(vector_mock.call_args.kwargs["output_file"], "C:\\tmp\\name.gcode")
        self.assertEqual(vector_mock.call_args.kwargs["width_mm"], 30.0)
        self.assertEqual(vector_mock.call_args.kwargs["height_mm"], 20.0)
        self.assertEqual(vector_mock.call_args.kwargs["feed_rate"], 1500)
        self.assertEqual(vector_mock.call_args.kwargs["travel_rate"], 3200)
        self.assertEqual(vector_mock.call_args.kwargs["laser_max_power"], 650)
        self.assertFalse(vector_mock.call_args.kwargs["auto_trim"])
        self.assertFalse(vector_mock.call_args.kwargs["auto_size"])
        self.assertEqual(vector_mock.call_args.kwargs["dpi"], 200)
        self.assertFalse(vector_mock.call_args.kwargs["lock_aspect_ratio"])
        self.assertEqual(vector_mock.call_args.kwargs["offset_x_mm"], 3)
        self.assertEqual(vector_mock.call_args.kwargs["offset_y_mm"], 4)
        self.assertEqual(vector_mock.call_args.kwargs["safe_margin_mm"], 6)
        self.assertEqual(vector_mock.call_args.kwargs["laser_mode"], "engrave")
        self.assertEqual(vector_mock.call_args.kwargs["engraving_mode"], "outline")

    def test_create_text_image_gcode_uses_vector_outline_for_text_cutting(self):
        image_result = {
            "success": True,
            "result": {
                "text": "佳佳",
                "output_file": "C:\\tmp\\cut-name.png",
                "width": 512,
                "height": 512,
                "font_path": "C:\\Windows\\Fonts\\simhei.ttf",
                "font_size": 320,
                "line_count": 1,
                "lines": ["佳佳"],
            },
        }
        gcode_result = {
            "success": True,
            "result": {
                "image_file": "C:\\tmp\\cut-name.png",
                "gcode_file": "C:\\tmp\\cut-name.gcode",
                "laser_mode": "cut",
                "engraving_mode": "cut",
                "mode": "cut",
                "text_vector_outline": True,
                "vector_backend": "fontTools",
            },
        }

        with (
            patch.object(
                text_image_gcode_tool.text_image_tool,
                "create_text_image",
                return_value=image_result,
            ),
            patch.object(
                text_image_gcode_tool,
                "_create_text_vector_outline_gcode",
                return_value=gcode_result,
            ) as vector_mock,
            patch.object(text_image_gcode_tool.laser_grbl_tool, "convert_image_to_gcode") as image_gcode_mock,
        ):
            result = text_image_gcode_tool.create_text_image_gcode(
                text="佳佳",
                gcode_output_file="C:\\tmp\\cut-name.gcode",
                laser_mode="cut",
                engraving_mode="outline",
                width_mm=30.0,
                feed_rate=600,
                laser_max_power=900,
            )

        self.assertTrue(result["success"], result)
        self.assertTrue(result["result"]["gcode"]["text_vector_outline"])
        self.assertEqual(result["result"]["gcode"]["laser_mode"], "cut")
        self.assertEqual(result["result"]["gcode"]["engraving_mode"], "cut")
        self.assertEqual(result["result"]["gcode"]["mode"], "cut")
        image_gcode_mock.assert_not_called()
        vector_mock.assert_called_once()
        self.assertEqual(vector_mock.call_args.kwargs["image_payload"], image_result["result"])
        self.assertEqual(vector_mock.call_args.kwargs["image_file"], "C:\\tmp\\cut-name.png")
        self.assertEqual(vector_mock.call_args.kwargs["output_file"], "C:\\tmp\\cut-name.gcode")
        self.assertEqual(vector_mock.call_args.kwargs["laser_mode"], "cut")
        self.assertEqual(vector_mock.call_args.kwargs["engraving_mode"], "outline")

    def test_create_text_image_gcode_falls_back_to_image_outline_for_vector_font_failure(self):
        image_result = {
            "success": True,
            "result": {
                "text": "佳佳",
                "output_file": "C:\\tmp\\name.png",
                "font_path": "C:\\Windows\\Fonts\\simhei.ttf",
                "font_size": 320,
                "lines": ["佳佳"],
            },
        }
        vector_result = {
            "success": False,
            "result": "当前字体不包含这些字符的矢量 glyph: 佳",
            "error_code": "missing_glyphs",
            "missing_glyphs": ["佳"],
        }
        fallback_result = {
            "success": True,
            "result": {
                "image_file": "C:\\tmp\\name.png",
                "gcode_file": "C:\\tmp\\name.gcode",
                "laser_mode": "engrave",
                "engraving_mode": "outline",
                "mode": "outline",
                "speech": "预计雕刻 1 分钟",
            },
        }

        with (
            patch.object(
                text_image_gcode_tool.text_image_tool,
                "create_text_image",
                return_value=image_result,
            ),
            patch.object(
                text_image_gcode_tool,
                "_create_text_vector_outline_gcode",
                return_value=vector_result,
            ),
            patch.object(
                text_image_gcode_tool.laser_grbl_tool,
                "convert_image_to_gcode",
                return_value=fallback_result,
            ) as image_gcode_mock,
        ):
            result = text_image_gcode_tool.create_text_image_gcode(
                text="佳佳",
                laser_mode="engrave",
                engraving_mode="outline",
            )

        self.assertTrue(result["success"], result)
        image_gcode_mock.assert_called_once()
        self.assertEqual(image_gcode_mock.call_args.kwargs["engraving_mode"], "outline")
        self.assertFalse(result["result"]["gcode"]["text_vector_outline"])
        self.assertTrue(result["result"]["gcode"]["text_vector_outline_fallback"])
        self.assertEqual(result["result"]["gcode"]["vector_outline_error"], vector_result["result"])
        self.assertEqual(result["result"]["gcode"]["vector_outline_error_code"], "missing_glyphs")
        self.assertEqual(result["result"]["gcode"]["missing_glyphs"], ["佳"])
        self.assertEqual(result["result"]["gcode"]["fallback_from_vector_backend"], "fontTools")
        self.assertEqual(result["result"]["gcode"]["fallback_to_backend"], "image_to_gcode_outline")
        self.assertTrue(result["result"]["speech"].startswith(text_image_gcode_tool.TEXT_VECTOR_OUTLINE_FALLBACK_SPEECH))

    def test_create_text_image_gcode_falls_back_to_image_cut_for_vector_font_failure(self):
        image_result = {
            "success": True,
            "result": {
                "text": "佳佳",
                "output_file": "C:\\tmp\\cut-name.png",
                "font_path": "C:\\Windows\\Fonts\\simhei.ttf",
                "font_size": 320,
                "lines": ["佳佳"],
            },
        }
        vector_result = {
            "success": False,
            "result": "当前字体不包含这些字符的矢量 glyph: 佳",
            "error_code": "missing_glyphs",
            "missing_glyphs": ["佳"],
        }
        fallback_result = {
            "success": True,
            "result": {
                "image_file": "C:\\tmp\\cut-name.png",
                "gcode_file": "C:\\tmp\\cut-name.gcode",
                "laser_mode": "cut",
                "engraving_mode": "cut",
                "mode": "cut",
                "speech": "预计切割 1 分钟",
            },
        }

        with (
            patch.object(
                text_image_gcode_tool.text_image_tool,
                "create_text_image",
                return_value=image_result,
            ),
            patch.object(
                text_image_gcode_tool,
                "_create_text_vector_outline_gcode",
                return_value=vector_result,
            ),
            patch.object(
                text_image_gcode_tool.laser_grbl_tool,
                "convert_image_to_gcode",
                return_value=fallback_result,
            ) as image_gcode_mock,
        ):
            result = text_image_gcode_tool.create_text_image_gcode(
                text="佳佳",
                laser_mode="cut",
                engraving_mode="outline",
            )

        self.assertTrue(result["success"], result)
        image_gcode_mock.assert_called_once()
        self.assertEqual(image_gcode_mock.call_args.kwargs["laser_mode"], "cut")
        self.assertEqual(image_gcode_mock.call_args.kwargs["engraving_mode"], "outline")
        self.assertEqual(result["result"]["gcode"]["laser_mode"], "cut")
        self.assertEqual(result["result"]["gcode"]["engraving_mode"], "cut")
        self.assertFalse(result["result"]["gcode"]["text_vector_outline"])
        self.assertTrue(result["result"]["gcode"]["text_vector_outline_fallback"])
        self.assertEqual(result["result"]["gcode"]["vector_outline_error_code"], "missing_glyphs")
        self.assertTrue(result["result"]["speech"].startswith(text_image_gcode_tool.TEXT_VECTOR_OUTLINE_FALLBACK_SPEECH))

    def test_create_text_image_gcode_keeps_non_font_vector_failure(self):
        image_result = {
            "success": True,
            "result": {
                "text": "佳佳",
                "output_file": "C:\\tmp\\name.png",
                "font_path": "C:\\Windows\\Fonts\\simhei.ttf",
                "font_size": 320,
                "lines": ["佳佳"],
            },
        }
        vector_result = {"success": False, "result": "laser_max_power 不支持"}

        with (
            patch.object(
                text_image_gcode_tool.text_image_tool,
                "create_text_image",
                return_value=image_result,
            ),
            patch.object(
                text_image_gcode_tool,
                "_create_text_vector_outline_gcode",
                return_value=vector_result,
            ),
            patch.object(text_image_gcode_tool.laser_grbl_tool, "convert_image_to_gcode") as image_gcode_mock,
        ):
            result = text_image_gcode_tool.create_text_image_gcode(
                text="佳佳",
                laser_mode="engrave",
                engraving_mode="outline",
            )

        self.assertFalse(result["success"])
        self.assertEqual(result["stage"], "text_vector_outline")
        self.assertEqual(result["image"], image_result["result"])
        self.assertEqual(result["detail"], vector_result)
        image_gcode_mock.assert_not_called()

    def test_create_text_image_gcode_stops_when_image_generation_fails(self):
        image_result = {"success": False, "result": "text 不能为空"}

        with (
            patch.object(
                text_image_gcode_tool.text_image_tool,
                "create_text_image",
                return_value=image_result,
            ),
            patch.object(text_image_gcode_tool.laser_grbl_tool, "convert_image_to_gcode") as gcode_mock,
        ):
            result = text_image_gcode_tool.create_text_image_gcode(text="")

        self.assertFalse(result["success"])
        self.assertEqual(result["stage"], "generate_text_image")
        self.assertEqual(result["result"], "text 不能为空")
        gcode_mock.assert_not_called()

    def test_create_text_image_gcode_reports_gcode_failure_with_image_detail(self):
        image_result = {
            "success": True,
            "result": {"text": "佳佳", "output_file": "C:\\tmp\\name.png"},
        }
        gcode_result = {"success": False, "result": "无法识别图片内容"}

        with (
            patch.object(
                text_image_gcode_tool.text_image_tool,
                "create_text_image",
                return_value=image_result,
            ),
            patch.object(
                text_image_gcode_tool.laser_grbl_tool,
                "convert_image_to_gcode",
                return_value=gcode_result,
            ),
        ):
            result = text_image_gcode_tool.create_text_image_gcode(text="佳佳")

        self.assertFalse(result["success"])
        self.assertEqual(result["stage"], "image_to_gcode")
        self.assertEqual(result["image"], image_result["result"])
        self.assertEqual(result["detail"], gcode_result)

    def test_create_text_image_gcode_can_use_material_params(self):
        image_result = {
            "success": True,
            "result": {"text": "佳佳", "output_file": "C:\\tmp\\name.png"},
        }
        gcode_result = {
            "success": True,
            "result": {"gcode_file": "C:\\tmp\\name.gcode"},
        }
        recommendation = {
            "success": True,
            "result": {
                "material": "椴木",
                "thickness_mm": 3.0,
                "laser_mode": "engrave",
                "engraving_mode": "raster",
                "params": {
                    "laser_min_power": 0,
                    "laser_max_power": 420,
                    "feed_rate": 1800,
                    "travel_rate": 3000,
                    "pixel_size_mm": 0.1,
                    "threshold": -1,
                    "passes": 1,
                    "power_percent": 42.0,
                    "s_max": 1000,
                },
            },
        }

        with (
            patch.object(
                text_image_gcode_tool.laser_material_calibration_tool,
                "recommend_laser_params",
                return_value=recommendation,
            ) as recommend_mock,
            patch.object(
                text_image_gcode_tool.text_image_tool,
                "create_text_image",
                return_value=image_result,
            ),
            patch.object(
                text_image_gcode_tool.laser_grbl_tool,
                "convert_image_to_gcode",
                return_value=gcode_result,
            ) as gcode_mock,
        ):
            result = text_image_gcode_tool.create_text_image_gcode(
                text="佳佳",
                material="木头",
                thickness_mm=3,
                laser_mode="engrave",
                engraving_mode="raster",
                params_file="materials.json",
            )

        self.assertTrue(result["success"], result)
        recommend_mock.assert_called_once_with(
            "木头",
            3,
            "engrave",
            "raster",
            params_file="materials.json",
        )
        self.assertEqual(gcode_mock.call_args.kwargs["feed_rate"], 1800)
        self.assertEqual(gcode_mock.call_args.kwargs["laser_max_power"], 420)
        self.assertEqual(result["result"]["material_recommendation"], recommendation["result"])

    def test_create_text_image_gcode_requires_material_when_forced_to_use_material_params(self):
        with patch.object(text_image_gcode_tool.text_image_tool, "create_text_image") as image_mock:
            result = text_image_gcode_tool.create_text_image_gcode(
                text="佳佳",
                use_material_params=True,
                thickness_mm=3,
            )

        self.assertFalse(result["success"])
        self.assertEqual(result["stage"], "recommend_laser_params")
        self.assertIn("material", result["result"])
        image_mock.assert_not_called()

    def test_create_text_image_gcode_requires_thickness_when_material_is_set(self):
        with patch.object(text_image_gcode_tool.text_image_tool, "create_text_image") as image_mock:
            result = text_image_gcode_tool.create_text_image_gcode(text="佳佳", material="椴木")

        self.assertFalse(result["success"])
        self.assertEqual(result["stage"], "recommend_laser_params")
        self.assertIn("thickness_mm", result["result"])
        image_mock.assert_not_called()

    def test_register_tool_exposes_generate_text_image_gcode_tool(self):
        fake_mcp = FakeMcp()
        text_image_gcode_tool.register_tool(fake_mcp)

        with patch.object(
            text_image_gcode_tool,
            "create_text_image_gcode",
            return_value={"success": True, "result": "ok"},
        ) as create_mock:
            result = fake_mcp.tools["generate_text_image_gcode_tool"]("佳佳", image_width=512)

        self.assertEqual(result, {"success": True, "result": "ok"})
        create_mock.assert_called_once()
        self.assertEqual(create_mock.call_args.kwargs["text"], "佳佳")
        self.assertEqual(create_mock.call_args.kwargs["image_width"], 512)
        self.assertEqual(create_mock.call_args.kwargs["engraving_mode"], "")
        self.assertEqual(create_mock.call_args.kwargs["material"], "")
        self.assertEqual(create_mock.call_args.kwargs["thickness_mm"], 0.0)
        self.assertTrue(create_mock.call_args.kwargs["auto_trim"])
        self.assertEqual(create_mock.call_args.kwargs["dpi"], 300.0)
        self.assertEqual(create_mock.call_args.kwargs["auto_wrap"], text_image_gcode_tool.text_image_tool.DEFAULT_AUTO_WRAP)
        self.assertEqual(create_mock.call_args.kwargs["max_lines"], text_image_gcode_tool.text_image_tool.DEFAULT_MAX_LINES)
        self.assertEqual(create_mock.call_args.kwargs["layout_mode"], text_image_gcode_tool.text_image_tool.DEFAULT_LAYOUT_MODE)

    def test_recording_to_image_contours_keeps_line_segments_exact(self):
        recording = [
            ("moveTo", ((0, 0),)),
            ("lineTo", ((100, 0),)),
            ("lineTo", ((100, 100),)),
            ("lineTo", ((0, 100),)),
            ("closePath", ()),
        ]

        contours = text_image_gcode_tool._recording_to_image_contours(
            recording,
            origin_x_px=10,
            baseline_y_px=200,
            scale_px=0.5,
        )

        self.assertEqual(
            contours,
            [[(10.0, 200.0), (60.0, 200.0), (60.0, 150.0), (10.0, 150.0)]],
        )

    def test_recording_to_image_contours_flattens_quadratic_curves(self):
        recording = [
            ("moveTo", ((0, 0),)),
            ("qCurveTo", ((50, 100), (100, 0))),
            ("closePath", ()),
        ]

        contours = text_image_gcode_tool._recording_to_image_contours(
            recording,
            origin_x_px=0,
            baseline_y_px=100,
            scale_px=1,
        )

        self.assertEqual(contours[0][0], (0.0, 100.0))
        self.assertEqual(contours[0][-1], (100.0, 100.0))
        self.assertGreater(len(contours[0]), 4)

    def test_create_text_vector_outline_gcode_reports_missing_fonttools(self):
        with patch.object(
            text_image_gcode_tool,
            "_load_fonttools",
            return_value=(None, None, None, "缺少 fontTools"),
        ):
            result = text_image_gcode_tool._create_text_vector_outline_gcode(
                image_payload={"font_path": "C:\\tmp\\font.ttf", "text": "佳", "lines": ["佳"]},
                image_file="C:\\tmp\\name.png",
                output_file="C:\\tmp\\name.gcode",
                width_mm=20.0,
                height_mm=20.0,
                feed_rate=1200,
                travel_rate=3000,
                laser_max_power=800,
                overwrite=True,
                auto_trim=True,
                auto_size=True,
                dpi=300.0,
                lock_aspect_ratio=True,
                offset_x_mm=0.0,
                offset_y_mm=0.0,
                safe_margin_mm=text_image_gcode_tool.laser_grbl_tool.DEFAULT_SAFE_MARGIN_MM,
            )

        self.assertFalse(result["success"])
        self.assertIn("fontTools", result["result"])
        self.assertEqual(result["error_code"], "missing_fonttools")

    def test_create_text_vector_outline_gcode_validates_laser_power(self):
        result = text_image_gcode_tool._create_text_vector_outline_gcode(
            image_payload={"font_path": "C:\\tmp\\font.ttf", "text": "佳", "lines": ["佳"]},
            image_file="C:\\tmp\\name.png",
            output_file="C:\\tmp\\name.gcode",
            width_mm=20.0,
            height_mm=20.0,
            feed_rate=1200,
            travel_rate=3000,
            laser_max_power=text_image_gcode_tool.laser_grbl_tool.DEFAULT_LASER_S_MAX + 1,
            overwrite=True,
            auto_trim=True,
            auto_size=True,
            dpi=300.0,
            lock_aspect_ratio=True,
            offset_x_mm=0.0,
            offset_y_mm=0.0,
            safe_margin_mm=text_image_gcode_tool.laser_grbl_tool.DEFAULT_SAFE_MARGIN_MM,
        )

        self.assertFalse(result["success"])
        self.assertIn("laser_max_power", result["result"])


if __name__ == "__main__":
    unittest.main()
