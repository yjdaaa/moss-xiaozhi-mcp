from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse, urlunparse
from urllib.request import Request, urlopen
from uuid import uuid4

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

MaterialSourceType = Literal["file", "text", "image-search", "ai-image"]
TextStyle = Literal["filled", "outline"]

MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 30
DOWNLOAD_RETRY_ATTEMPTS = 2
MAX_SOURCE_PIXELS = 16_000_000
MAX_PROCESSED_EDGE_PX = 1600
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


class MaterialSourceError(ValueError):
    def __init__(self, message: str, original_path: Path | None = None):
        super().__init__(message)
        self.original_path = original_path


@dataclass(frozen=True)
class MaterialSourceRequest:
    source_type: MaterialSourceType = "file"
    text: str | None = None
    url: str | None = None
    assets_dir: Path = Path("generated_assets")
    keep_grayscale: bool = False
    text_style: TextStyle = "filled"
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None


@dataclass(frozen=True)
class MaterialSourceResult:
    original_path: Path
    processed_path: Path
    source_type: MaterialSourceType
    message: str


def prepare_material_source(request: MaterialSourceRequest) -> MaterialSourceResult:
    if request.source_type == "file":
        raise MaterialSourceError("File source does not need material preparation.")
    request.assets_dir.mkdir(parents=True, exist_ok=True)
    prefix = _safe_prefix(request.source_type)
    if request.source_type == "text":
        original_path = request.assets_dir / f"{prefix}_original.png"
        _render_text_asset(request.text, original_path, request.text_style)
    elif request.source_type == "image-search":
        original_path = _download_image_asset(request.url, request.assets_dir, prefix)
    elif request.source_type == "ai-image":
        original_path = _generate_ai_image_asset(request, request.assets_dir, prefix)
    else:
        raise MaterialSourceError("Unsupported material source type.")
    processed_path = request.assets_dir / f"{prefix}_processed.png"
    try:
        _preprocess_asset(original_path, processed_path, keep_grayscale=request.keep_grayscale)
    except MaterialSourceError as error:
        raise MaterialSourceError(str(error), original_path=original_path) from error
    return MaterialSourceResult(
        original_path=original_path,
        processed_path=processed_path,
        source_type=request.source_type,
        message="素材已生成并预处理，最终加工安全状态仍以 summary 为准。",
    )


def _safe_prefix(source_type: str) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{source_type.replace('-', '_')}_{timestamp}_{uuid4().hex[:8]}"


def _render_text_asset(text: str | None, output_path: Path, text_style: TextStyle) -> None:
    content = (text or "").strip()
    if not content:
        raise MaterialSourceError("Text source requires non-empty text.")
    if text_style not in {"filled", "outline"}:
        raise MaterialSourceError("Text style must be filled or outline.")
    width = 1200
    height = 600
    image = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(image)
    font = _fit_font(draw, content, width - 120, height - 120)
    lines = _wrap_text(draw, content, font, width - 120)
    line_boxes = [draw.textbbox((0, 0), line, font=font, stroke_width=1 if text_style == "outline" else 0) for line in lines]
    line_heights = [box[3] - box[1] for box in line_boxes]
    font_size = getattr(font, "size", 16)
    total_height = sum(line_heights) + max(0, len(lines) - 1) * max(10, font_size // 4)
    y_coord = max(40, (height - total_height) // 2)
    for line, box, line_height in zip(lines, line_boxes, line_heights):
        line_width = box[2] - box[0]
        x_coord = max(40, (width - line_width) // 2)
        if text_style == "outline":
            draw.text((x_coord, y_coord), line, fill=255, font=font, stroke_width=max(1, font.size // 18), stroke_fill=0)
        else:
            draw.text((x_coord, y_coord), line, fill=0, font=font)
        y_coord += line_height + max(10, font_size // 4)
    image.save(output_path)


def _fit_font(draw: ImageDraw.ImageDraw, text: str, max_width: int, max_height: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for size in range(180, 15, -4):
        font = _load_font(size)
        lines = _wrap_text(draw, text, font, max_width)
        boxes = [draw.textbbox((0, 0), line, font=font) for line in lines]
        width = max((box[2] - box[0] for box in boxes), default=0)
        height = sum(box[3] - box[1] for box in boxes) + max(0, len(lines) - 1) * max(10, size // 4)
        if width <= max_width and height <= max_height:
            return font
    return _load_font(16)


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in _font_candidates():
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _font_candidates() -> tuple[str, ...]:
    return (
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/simsun.ttc",
        "msyh.ttc",
        "simhei.ttf",
        "simsun.ttc",
        "arial.ttf",
        "DejaVuSans.ttf",
    )


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont, max_width: int) -> list[str]:
    paragraphs = text.splitlines() or [text]
    lines: list[str] = []
    for paragraph in paragraphs:
        current = ""
        for character in paragraph:
            candidate = current + character
            box = draw.textbbox((0, 0), candidate, font=font)
            if current and box[2] - box[0] > max_width:
                lines.append(current)
                current = character
            else:
                current = candidate
        if current:
            lines.append(current)
    return lines or [text]


def _download_image_asset(url: str | None, assets_dir: Path, prefix: str) -> Path:
    cleaned_url = _validate_url(url)
    suffix = Path(urlparse(cleaned_url).path).suffix.lower()
    output_suffix = suffix if suffix in IMAGE_SUFFIXES else ".img"
    output_path = assets_dir / f"{prefix}_original{output_suffix}"
    request = Request(cleaned_url, headers=_download_headers(cleaned_url))
    last_error: BaseException | None = None
    try:
        for _attempt in range(DOWNLOAD_RETRY_ATTEMPTS):
            try:
                with urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
                    final_url = response.geturl()
                    if urlparse(final_url).scheme not in {"http", "https"}:
                        raise MaterialSourceError("Image URL redirected to an unsupported protocol.")
                    content_type = response.headers.get_content_type()
                    final_suffix = Path(urlparse(final_url).path).suffix.lower()
                    if not content_type.startswith("image/") and final_suffix not in IMAGE_SUFFIXES:
                        raise MaterialSourceError("Image URL must return image content or use an image file suffix.")
                    data = _read_limited(response)
                break
            except HTTPError:
                raise
            except (URLError, TimeoutError, OSError) as error:
                last_error = error
        else:
            raise MaterialSourceError(f"Image download failed: {_safe_error_text(last_error)}")
    except MaterialSourceError:
        raise
    except HTTPError as error:
        raise MaterialSourceError(f"Image download failed with HTTP {error.code}.") from error
    output_path.write_bytes(data)
    return output_path


def _download_headers(url: str) -> dict[str, str]:
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36 AiLaserMaterialSource/0.1",
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
    }
    if origin:
        headers["Referer"] = origin + "/"
    return headers


def _generate_ai_image_asset(request: MaterialSourceRequest, assets_dir: Path, prefix: str) -> Path:
    prompt = _ai_image_prompt(request.text)
    base_url = (request.base_url or "").strip().rstrip("/")
    api_key = (request.api_key or "").strip()
    model = (request.model or "").strip()
    if not base_url or not api_key or not model:
        raise MaterialSourceError("AI image generation requires API address, API Key, and model.")
    body = {
        "model": model,
        "prompt": prompt,
        "n": 1,
        "size": "1024x1024",
    }
    http_request = Request(
        f"{base_url}/images/generations",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "AiLaserMaterialSource/0.1",
        },
        method="POST",
    )
    try:
        with urlopen(http_request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise MaterialSourceError(f"AI image generation failed with HTTP {error.code}: {_redact_secret(detail, api_key)}") from error
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
        raise MaterialSourceError(f"AI image generation failed: {_redact_secret(_safe_error_text(error), api_key)}") from error
    image_item = _first_image_item(payload)
    if image_item.get("b64_json"):
        return _write_ai_b64_image(image_item["b64_json"], assets_dir, prefix)
    if image_item.get("url"):
        return _download_image_asset(str(image_item["url"]), assets_dir, prefix)
    raise MaterialSourceError("AI image generation response did not include image data.")


def _ai_image_prompt(text: str | None) -> str:
    content = (text or "").strip()
    if not content:
        raise MaterialSourceError("AI image source requires a non-empty image description.")
    return f"{content}. Create a simple black and white vector-style line art icon or silhouette suitable for laser engraving or cutting. Use high contrast, clean edges, centered subject, plain white background, no text unless explicitly requested."


def _first_image_item(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise MaterialSourceError("AI image generation response is not a JSON object.")
    data = payload.get("data")
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        raise MaterialSourceError("AI image generation response did not include image data.")
    return data[0]


def _write_ai_b64_image(encoded: str, assets_dir: Path, prefix: str) -> Path:
    try:
        data = base64.b64decode(encoded, validate=True)
    except ValueError as error:
        raise MaterialSourceError("AI image generation returned invalid base64 image data.") from error
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise MaterialSourceError("AI image generation returned an image larger than 10MB.")
    output_path = assets_dir / f"{prefix}_original.png"
    output_path.write_bytes(data)
    return output_path


def _validate_url(url: str | None) -> str:
    text = (url or "").strip()
    if not text:
        raise MaterialSourceError("Image URL is required.")
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"}:
        raise MaterialSourceError("Image URL must start with http:// or https://.")
    if not parsed.netloc:
        raise MaterialSourceError("Image URL must include a host.")
    return urlunparse(parsed._replace(fragment=""))


def _read_limited(response) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_DOWNLOAD_BYTES:
            raise MaterialSourceError("Image download is larger than 10MB.")
        chunks.append(chunk)
    return b"".join(chunks)


def _preprocess_asset(input_path: Path, output_path: Path, keep_grayscale: bool) -> None:
    try:
        with Image.open(input_path) as opened:
            image = opened.convert("L")
    except (UnidentifiedImageError, OSError) as error:
        raise MaterialSourceError("Image could not be opened as a supported image.") from error
    width, height = image.size
    if width <= 0 or height <= 0 or width * height > MAX_SOURCE_PIXELS:
        raise MaterialSourceError("Image dimensions are unsupported or too large.")
    image = _crop_white_border(image)
    if image.size[0] <= 0 or image.size[1] <= 0:
        raise MaterialSourceError("Image is blank after trimming whitespace.")
    image.thumbnail((MAX_PROCESSED_EDGE_PX, MAX_PROCESSED_EDGE_PX), Image.Resampling.LANCZOS)
    if not keep_grayscale:
        image = _binarize_asset(image)
        image = _remove_edge_artifacts(image)
        image = _crop_white_border(image)
    image.save(output_path, format="PNG")


def _crop_white_border(image: Image.Image) -> Image.Image:
    mask = image.point(lambda value: 255 if value < 250 else 0, mode="L")
    box = mask.getbbox()
    if box is None:
        raise MaterialSourceError("Image is blank or contains no engravable pixels.")
    left, upper, right, lower = box
    padding = 12
    left = max(0, left - padding)
    upper = max(0, upper - padding)
    right = min(image.size[0], right + padding)
    lower = min(image.size[1], lower + padding)
    return image.crop((left, upper, right, lower))


def _binarize_asset(image: Image.Image) -> Image.Image:
    threshold = _auto_threshold(image)
    return image.point(lambda value: 0 if value < threshold else 255, mode="1").convert("L")


def _auto_threshold(image: Image.Image) -> int:
    histogram = image.histogram()
    total = sum(histogram)
    if total <= 0:
        return 180
    weighted_sum = sum(index * count for index, count in enumerate(histogram))
    background_ratio = sum(histogram[230:]) / total
    if background_ratio >= 0.55:
        dark_total = sum(histogram[:230])
        if dark_total:
            dark_weighted_sum = sum(index * count for index, count in enumerate(histogram[:230]))
            dark_mean = dark_weighted_sum / dark_total
            return _clamp_int((dark_mean + 245) / 2, 120, 220)
    mean = weighted_sum / total
    return _clamp_int(mean * 0.85, 90, 190)


def _remove_edge_artifacts(image: Image.Image) -> Image.Image:
    cleaned = image.copy()
    width, height = cleaned.size
    if width <= 4 or height <= 4:
        return cleaned
    pixels = cleaned.load()
    max_line_thickness = max(2, min(width, height) // 80)
    max_edge_scan = max(max_line_thickness + 1, min(width, height) // 20)
    for y_coord in range(min(max_edge_scan, height)):
        if _row_black_ratio(pixels, width, y_coord) >= 0.82:
            _paint_row_white(pixels, width, y_coord)
    for y_coord in range(max(0, height - max_edge_scan), height):
        if _row_black_ratio(pixels, width, y_coord) >= 0.82:
            _paint_row_white(pixels, width, y_coord)
    for x_coord in range(min(max_edge_scan, width)):
        if _column_black_ratio(pixels, height, x_coord) >= 0.82:
            _paint_column_white(pixels, height, x_coord)
    for x_coord in range(max(0, width - max_edge_scan), width):
        if _column_black_ratio(pixels, height, x_coord) >= 0.82:
            _paint_column_white(pixels, height, x_coord)
    return cleaned


def _row_black_ratio(pixels, width: int, y_coord: int) -> float:
    return sum(1 for x_coord in range(width) if pixels[x_coord, y_coord] < 128) / width


def _column_black_ratio(pixels, height: int, x_coord: int) -> float:
    return sum(1 for y_coord in range(height) if pixels[x_coord, y_coord] < 128) / height


def _paint_row_white(pixels, width: int, y_coord: int) -> None:
    for x_coord in range(width):
        pixels[x_coord, y_coord] = 255


def _paint_column_white(pixels, height: int, x_coord: int) -> None:
    for y_coord in range(height):
        pixels[x_coord, y_coord] = 255


def _clamp_int(value: float, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int(round(value))))


def _safe_error_text(error: BaseException) -> str:
    if error is None:
        return "unknown error"
    return str(error).split("?", 1)[0]


def _redact_secret(message: str, secret: str) -> str:
    if not secret:
        return message
    return message.replace(secret, "[redacted]").replace(quote(secret, safe=""), "[redacted]")
