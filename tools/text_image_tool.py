import os
import platform

from core.laser_runtime.config import get_laser_settings


DEFAULT_IMAGE_WIDTH = 1024
DEFAULT_IMAGE_HEIGHT = 1024
DEFAULT_FONT_SIZE = 320
DEFAULT_LINE_SPACING = 0.18
DEFAULT_AUTO_WRAP = False
DEFAULT_MAX_LINES = 0
DEFAULT_LAYOUT_MODE = ""
DEFAULT_OUTPUT_DIR = str(get_laser_settings().text_image_output_dir)
DEFAULT_OUTPUT_FILE = "text_image.png"
LOSSLESS_EXTENSIONS = {".png", ".bmp", ".tif", ".tiff"}
WINDOWS_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simsun.ttc",
]
MACOS_FONT_CANDIDATES = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
]
LINUX_FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttf",
]


def _build_error_result(message):
    return {"success": False, "result": message}


def _load_pillow():
    try:
        from PIL import Image, ImageDraw, ImageFont

        return Image, ImageDraw, ImageFont, None
    except ImportError:
        return None, None, None, "未安装 Pillow，请先执行 pip install Pillow"


def _validate_text(text):
    normalized = (text or "").strip()
    if not normalized:
        return None, "text 不能为空"
    return normalized, None


def _validate_positive_int(name, value):
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None, f"{name} 必须是正整数"

    if normalized <= 0:
        return None, f"{name} 必须是正整数"

    return normalized, None


def _validate_non_negative_int(name, value):
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None, f"{name} 必须是非负整数"

    if normalized < 0:
        return None, f"{name} 必须是非负整数"

    return normalized, None


def _validate_non_negative_float(name, value):
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return None, f"{name} 必须是非负数字"

    if normalized < 0:
        return None, f"{name} 必须是非负数字"

    return normalized, None


def _split_text_lines(text):
    lines = [line.strip() for line in str(text).splitlines()]
    return [line for line in lines if line]


def _normalize_layout_mode(layout_mode, auto_wrap, max_lines):
    normalized = str(layout_mode or "").strip().lower().replace("-", "_")
    if not normalized:
        if max_lines > 0:
            return "balanced", None
        if auto_wrap:
            return "auto_wrap", None
        return "manual", None

    aliases = {
        "manual": "manual",
        "manual_multiline": "manual",
        "preserve": "manual",
        "default": "manual",
        "auto": "auto_wrap",
        "auto_wrap": "auto_wrap",
        "wrap": "auto_wrap",
        "balanced": "balanced",
        "target_lines": "balanced",
        "line_count": "balanced",
    }
    if normalized not in aliases:
        supported = ", ".join(sorted(aliases))
        return None, f"layout_mode 不支持: {layout_mode}，支持: {supported}"
    return aliases[normalized], None


def _split_balanced_text(text, target_lines):
    source = " ".join(_split_text_lines(text)).strip()
    if not source:
        return []

    target_lines = min(target_lines, len(source))
    if target_lines <= 1:
        return [source]

    line_length = max(1, (len(source) + target_lines - 1) // target_lines)
    lines = []
    for index in range(0, len(source), line_length):
        line = source[index : index + line_length].strip()
        if line:
            lines.append(line)
    return lines


def _measure_text_width(draw, text, font):
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0]


def _wrap_line_to_width(draw, line, font, max_width):
    line = str(line or "").strip()
    if not line:
        return []

    lines = []
    current = ""
    for char in line:
        candidate = current + char
        if current and _measure_text_width(draw, candidate, font) > max_width:
            lines.append(current.strip())
            current = char.lstrip()
        else:
            current = candidate
    if current.strip():
        lines.append(current.strip())
    return lines or [line]


def _wrap_text_to_width(draw, text, font, max_width):
    wrapped = []
    for line in _split_text_lines(text):
        wrapped.extend(_wrap_line_to_width(draw, line, font, max_width))
    return wrapped


def _resolve_layout_lines(draw, text, font, max_width, layout_mode, auto_wrap, max_lines):
    source_lines = _split_text_lines(text)
    manual_line_breaks = len(source_lines) > 1

    if layout_mode == "balanced":
        lines = _split_balanced_text(text, max_lines)
    elif layout_mode == "auto_wrap":
        lines = _wrap_text_to_width(draw, text, font, max_width)
    else:
        lines = source_lines

    return lines, {
        "mode": layout_mode,
        "auto_wrap": bool(auto_wrap),
        "max_lines": max_lines,
        "manual_line_breaks": manual_line_breaks,
        "source_line_count": len(source_lines),
        "line_count": len(lines),
        "lines": lines,
    }


def _resolve_output_file(output_file):
    normalized = (output_file or "").strip()
    if not normalized:
        normalized = os.path.join(DEFAULT_OUTPUT_DIR, DEFAULT_OUTPUT_FILE)

    normalized = os.path.abspath(normalized)
    root, extension = os.path.splitext(normalized)
    extension = extension.lower()

    if not extension:
        normalized = f"{normalized}.png"
        extension = ".png"

    if extension not in LOSSLESS_EXTENSIONS:
        return None, "输出格式必须是 PNG/BMP/TIF/TIFF 之一，JPEG 会破坏纯黑白效果"

    return normalized, None


def _get_font_candidates(current_system=None):
    if current_system is None:
        current_system = platform.system()

    if current_system == "Windows":
        return WINDOWS_FONT_CANDIDATES
    if current_system == "Darwin":
        return MACOS_FONT_CANDIDATES
    return LINUX_FONT_CANDIDATES


def _resolve_font_path(font_path="", current_system=None):
    normalized = (font_path or "").strip()
    if normalized:
        resolved = os.path.abspath(normalized)
        if os.path.exists(resolved):
            return resolved, None
        return None, f"字体文件不存在: {resolved}"

    for candidate in _get_font_candidates(current_system=current_system):
        if os.path.exists(candidate):
            return candidate, None

    return None, "未找到可用中文字体，请传入 font_path，例如 simhei.ttf 或 msyh.ttc"


def _measure_multiline_text(draw, lines, font, line_spacing_px):
    boxes = [draw.textbbox((0, 0), line, font=font) for line in lines]
    line_widths = [box[2] - box[0] for box in boxes]
    line_heights = [box[3] - box[1] for box in boxes]
    text_width = max(line_widths) if line_widths else 0
    text_height = sum(line_heights) + max(0, len(lines) - 1) * line_spacing_px
    return text_width, text_height, boxes, line_heights


def _fit_font(draw, text, image_font_module, font_path, width, height, requested_font_size, padding, line_spacing,
              layout_mode, auto_wrap, max_lines):
    max_width = max(1, width - padding * 2)
    max_height = max(1, height - padding * 2)

    for font_size in range(requested_font_size, 11, -2):
        font = image_font_module.truetype(font_path, font_size)
        line_spacing_px = int(round(font_size * line_spacing))
        lines, layout = _resolve_layout_lines(
            draw,
            text,
            font,
            max_width,
            layout_mode,
            auto_wrap,
            max_lines,
        )
        text_width, text_height, boxes, line_heights = _measure_multiline_text(
            draw,
            lines,
            font,
            line_spacing_px,
        )
        if text_width <= max_width and text_height <= max_height:
            layout["font_size"] = font_size
            return font, font_size, boxes, line_heights, line_spacing_px, lines, layout, None

    return None, None, None, None, None, None, None, "文字在当前画布内放不下，请增大 width/height 或减小 font_size"


def create_text_image(text, output_file="", width=DEFAULT_IMAGE_WIDTH, height=DEFAULT_IMAGE_HEIGHT,
                      font_size=DEFAULT_FONT_SIZE, font_path="", line_spacing=DEFAULT_LINE_SPACING,
                      auto_wrap=DEFAULT_AUTO_WRAP, max_lines=DEFAULT_MAX_LINES,
                      layout_mode=DEFAULT_LAYOUT_MODE):
    text, error = _validate_text(text)
    if error:
        return _build_error_result(error)

    width, error = _validate_positive_int("width", width)
    if error:
        return _build_error_result(error)

    height, error = _validate_positive_int("height", height)
    if error:
        return _build_error_result(error)

    font_size, error = _validate_positive_int("font_size", font_size)
    if error:
        return _build_error_result(error)

    line_spacing, error = _validate_non_negative_float("line_spacing", line_spacing)
    if error:
        return _build_error_result(error)

    max_lines, error = _validate_non_negative_int("max_lines", max_lines)
    if error:
        return _build_error_result(error)

    resolved_layout_mode, error = _normalize_layout_mode(layout_mode, auto_wrap, max_lines)
    if error:
        return _build_error_result(error)

    output_file, error = _resolve_output_file(output_file)
    if error:
        return _build_error_result(error)

    font_path, error = _resolve_font_path(font_path)
    if error:
        return _build_error_result(error)

    image_module, image_draw_module, image_font_module, error = _load_pillow()
    if error:
        return _build_error_result(error)

    try:
        os.makedirs(os.path.dirname(output_file), exist_ok=True)

        image = image_module.new("L", (width, height), 255)
        draw = image_draw_module.Draw(image)
        padding = max(16, min(width, height) // 16)
        font, actual_font_size, boxes, line_heights, line_spacing_px, lines, layout, error = _fit_font(
            draw,
            text,
            image_font_module,
            font_path,
            width,
            height,
            font_size,
            padding,
            line_spacing,
            resolved_layout_mode,
            bool(auto_wrap),
            max_lines,
        )
        if error:
            return _build_error_result(error)

        total_text_height = sum(line_heights) + max(0, len(lines) - 1) * line_spacing_px
        current_y = (height - total_text_height) / 2
        for line, bbox, line_height in zip(lines, boxes, line_heights):
            text_width = bbox[2] - bbox[0]
            text_x = (width - text_width) / 2 - bbox[0]
            text_y = current_y - bbox[1]
            draw.text((text_x, text_y), line, fill=0, font=font)
            current_y += line_height + line_spacing_px

        # 阈值化后再保存，确保输出仍然只有纯黑和纯白。
        binary_image = image.point(lambda pixel: 0 if pixel < 128 else 255).convert("RGB")
        binary_image.save(output_file)
        return {
            "success": True,
            "result": {
                "text": text,
                "output_file": output_file,
                "width": width,
                "height": height,
                "font_path": font_path,
                "font_size": actual_font_size,
                "line_count": len(lines),
                "lines": lines,
                "layout": layout,
                "line_spacing": line_spacing,
                "auto_wrap": bool(auto_wrap),
                "max_lines": max_lines,
                "layout_mode": resolved_layout_mode,
                "background_color": "#FFFFFF",
                "text_color": "#000000",
            },
        }
    except Exception as exc:
        return _build_error_result(str(exc))


def register_tool(mcp):
    @mcp.tool()
    def generate_text_image_tool(text: str, output_file: str = "", width: int = DEFAULT_IMAGE_WIDTH,
                                  height: int = DEFAULT_IMAGE_HEIGHT, font_size: int = DEFAULT_FONT_SIZE,
                                  font_path: str = "", line_spacing: float = DEFAULT_LINE_SPACING,
                                  auto_wrap: bool = DEFAULT_AUTO_WRAP,
                                  max_lines: int = DEFAULT_MAX_LINES,
                                  layout_mode: str = DEFAULT_LAYOUT_MODE) -> dict:
        """
        生成纯白底、纯黑字的文字图片工具。
        适合用户只需要类似“佳佳”这种名字图片的场景，不调用任何 AI 文生图服务。

        参数:
            text: 要写入图片的文字，例如“佳佳”
            output_file: 输出图片路径；为空时默认保存到 generated_images/text_image.png
            width: 图片宽度，默认 1024
            height: 图片高度，默认 1024
            font_size: 初始字号，默认 320；如果文字过大，会自动缩小适配画布
            font_path: 可选字体路径；为空时自动选择系统常见中文黑体/无衬线字体
            line_spacing: 多行文字行距，按字号比例计算；默认 0.18
            auto_wrap: 是否自动把长文本按画布宽度换行；默认 false，保持旧行为
            max_lines: 指定目标行数；例如 3 表示尽量均衡分成三行，默认 0 不指定
            layout_mode: 可选 manual/auto_wrap/balanced；为空时由 auto_wrap/max_lines 决定
        """
        return create_text_image(
            text=text,
            output_file=output_file,
            width=width,
            height=height,
            font_size=font_size,
            font_path=font_path,
            line_spacing=line_spacing,
            auto_wrap=auto_wrap,
            max_lines=max_lines,
            layout_mode=layout_mode,
        )
