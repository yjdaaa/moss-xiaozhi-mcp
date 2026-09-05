import base64
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Any
from urllib.error import URLError, HTTPError
from urllib.request import Request, urlopen


AI_PROVIDER_ENV_KEYS = {
    "base_url": "AI_LASER_OPENAI_BASE_URL",
    "api_key": "AI_LASER_OPENAI_API_KEY",
    "model": "AI_LASER_OPENAI_MODEL",
}

AI_AUTHORITY = "AI 仅提供图像理解、参数解释和预处理建议；本地材料库、安全校验、预览确认和用户确认仍是最终边界。"

AI_PREPROCESS_ENUMS = {
    "preprocess.invert": {"yes", "no", "auto"},
    "preprocess.cleanup_background": {"none", "white", "light", "auto"},
    "mode_recommendation": {"raster", "outline", "candidate_selection_required"},
    "raster.dithering": {"auto", "threshold", "floyd_steinberg", "atkinson", "sierra_lite"},
    "raster.scan_direction": {"auto", "horizontal", "vertical"},
    "outline.trace_algorithm": {"vector"},
    "outline.fill_strategy": {"auto", "none", "hatch", "zigzag"},
    "outline.arc_output": {"off", "on_if_supported"},
}

AI_PREPROCESS_DEFAULTS = {
    "preprocess.invert": "auto",
    "preprocess.cleanup_background": "auto",
    "mode_recommendation": "candidate_selection_required",
    "raster.dithering": "auto",
    "raster.scan_direction": "auto",
    "outline.trace_algorithm": "vector",
    "outline.fill_strategy": "none",
    "outline.arc_output": "off",
}


@dataclass(frozen=True)
class AiProviderConfig:
    base_url: str
    api_key: str
    model: str


@dataclass(frozen=True)
class AiAssistResult:
    enabled: bool
    image_upload_allowed: bool
    provider_configured: bool
    provider: str | None = None
    model: str | None = None
    status: str = "not_requested"
    recommendations: dict[str, Any] = field(default_factory=dict)
    fallback_reason: str | None = None
    authority: str = AI_AUTHORITY


@dataclass(frozen=True)
class AiPreprocessSuggestion:
    preprocess: dict[str, Any]
    mode_recommendation: str
    raster: dict[str, str]
    outline: dict[str, str]
    confidence: float | None
    reason: str | None
    valid: bool
    validation_warnings: list[str] = field(default_factory=list)


class AiAssistantError(RuntimeError):
    pass


def load_ai_provider_config(env: dict[str, str] | None = None) -> AiProviderConfig | None:
    values = env if env is not None else os.environ
    base_url = values.get(AI_PROVIDER_ENV_KEYS["base_url"], "").strip().rstrip("/")
    api_key = values.get(AI_PROVIDER_ENV_KEYS["api_key"], "").strip()
    model = values.get(AI_PROVIDER_ENV_KEYS["model"], "").strip()
    if not base_url or not api_key or not model:
        return None
    return AiProviderConfig(base_url=base_url, api_key=api_key, model=model)


def run_ai_assist(
    image_path: Path,
    prompt: str,
    enabled: bool = False,
    allow_image_upload: bool = False,
    config: AiProviderConfig | None = None,
    client: Callable[[AiProviderConfig, Path, str], dict[str, Any]] | None = None,
) -> AiAssistResult:
    if not enabled:
        return AiAssistResult(enabled=False, image_upload_allowed=False, provider_configured=False)
    loaded_config = config or load_ai_provider_config()
    if not allow_image_upload:
        return AiAssistResult(enabled=True, image_upload_allowed=False, provider_configured=loaded_config is not None, provider=_safe_provider_label(loaded_config), model=loaded_config.model if loaded_config else None, status="disabled_by_user")
    if loaded_config is None:
        return AiAssistResult(enabled=True, image_upload_allowed=True, provider_configured=False, status="not_configured", fallback_reason="AI provider environment variables are incomplete.")
    ai_client = client or request_openai_compatible_analysis
    try:
        recommendations = ai_client(loaded_config, image_path, prompt)
    except Exception as error:
        return AiAssistResult(enabled=True, image_upload_allowed=True, provider_configured=True, provider=_safe_provider_label(loaded_config), model=loaded_config.model, status="failed_fallback_local", fallback_reason=_safe_error_message(error, loaded_config.api_key))
    return AiAssistResult(enabled=True, image_upload_allowed=True, provider_configured=True, provider=_safe_provider_label(loaded_config), model=loaded_config.model, status="used", recommendations=recommendations)


def request_openai_compatible_analysis(config: AiProviderConfig, image_path: Path, prompt: str) -> dict[str, Any]:
    image_bytes = image_path.read_bytes()
    image_payload = base64.b64encode(image_bytes).decode("ascii")
    payload = {
        "model": config.model,
        "messages": [
            {
                "role": "system",
                "content": "你是激光雕刻图片分析助手，只能提供分类、参数解释和预处理建议，不能绕过本地安全规则。",
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _analysis_prompt_text(prompt, image_path.name)},
                    {"type": "image_url", "image_url": {"url": f"data:{_mime_type(image_path)};base64,{image_payload}"}},
                ],
            },
        ],
        "response_format": {"type": "json_object"},
    }
    request = Request(
        f"{config.base_url}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {config.api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, OSError, json.JSONDecodeError) as error:
        raise AiAssistantError(_safe_error_message(error, config.api_key)) from error
    content = response_payload.get("choices", [{}])[0].get("message", {}).get("content", "")
    if not isinstance(content, str) or not content.strip():
        raise AiAssistantError("AI provider returned an empty response.")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        parsed = {"raw_text": content}
    if not isinstance(parsed, dict):
        return {"raw_text": content}
    return parsed


def ai_assist_payload(result: AiAssistResult | None) -> dict[str, Any]:
    current = result or AiAssistResult(enabled=False, image_upload_allowed=False, provider_configured=False)
    return {
        "enabled": current.enabled,
        "image_upload_allowed": current.image_upload_allowed,
        "provider_configured": current.provider_configured,
        "provider": current.provider,
        "model": current.model,
        "status": current.status,
        "recommendations": current.recommendations,
        "validated_preprocess_suggestion": parse_ai_preprocess_suggestion(current.recommendations).__dict__,
        "fallback_reason": current.fallback_reason,
        "authority": current.authority,
    }


def parse_ai_preprocess_suggestion(recommendations: dict[str, Any]) -> AiPreprocessSuggestion:
    warnings: list[str] = []
    preprocess_input = recommendations.get("preprocess") if isinstance(recommendations.get("preprocess"), dict) else {}
    raster_input = recommendations.get("raster") if isinstance(recommendations.get("raster"), dict) else {}
    outline_input = recommendations.get("outline") if isinstance(recommendations.get("outline"), dict) else {}
    preprocess = {
        "invert": _enum_value(preprocess_input, "invert", "preprocess.invert", warnings),
        "cleanup_background": _enum_value(preprocess_input, "cleanup_background", "preprocess.cleanup_background", warnings),
    }
    raster = {
        "dithering": _enum_value(raster_input, "dithering", "raster.dithering", warnings),
        "scan_direction": _enum_value(raster_input, "scan_direction", "raster.scan_direction", warnings),
    }
    outline = {
        "trace_algorithm": _enum_value(outline_input, "trace_algorithm", "outline.trace_algorithm", warnings),
        "fill_strategy": _enum_value(outline_input, "fill_strategy", "outline.fill_strategy", warnings),
        "arc_output": _enum_value(outline_input, "arc_output", "outline.arc_output", warnings),
    }
    confidence = _confidence_value(recommendations.get("confidence"), warnings)
    reason = recommendations.get("reason") if isinstance(recommendations.get("reason"), str) else None
    return AiPreprocessSuggestion(
        preprocess=preprocess,
        mode_recommendation=_enum_scalar(recommendations.get("mode_recommendation"), "mode_recommendation", warnings),
        raster=raster,
        outline=outline,
        confidence=confidence,
        reason=reason,
        valid=not warnings,
        validation_warnings=warnings,
    )


def _analysis_prompt_text(prompt: str, image_name: str) -> str:
    return (
        f"用户提示词：{prompt}\n图片文件名：{image_name}\n"
        "请只返回 JSON，不要返回自然语言方案。必须包含字段："
        "preprocess、mode_recommendation、raster、outline、confidence、reason。"
        "preprocess.invert 只能是 yes/no/auto；preprocess.cleanup_background 只能是 none/white/light/auto；"
        "mode_recommendation 只能是 raster/outline/candidate_selection_required；"
        "raster.dithering 只能是 auto/threshold/floyd_steinberg/atkinson/sierra_lite；"
        "raster.scan_direction 只能是 auto/horizontal/vertical；"
        "outline.trace_algorithm 只能是 vector；"
        "outline.fill_strategy 只能是 auto/none/hatch/zigzag；outline.arc_output 只能是 off/on_if_supported。"
        "confidence 必须是 0.0 到 1.0；reason 只用于展示，后端不会用 reason 推断行为。"
        "不要假设材料、功率、速度、尺寸或安全参数已验证。"
    )


def _enum_value(payload: dict[str, Any], key: str, path: str, warnings: list[str]) -> str:
    return _enum_scalar(payload.get(key), path, warnings)


def _enum_scalar(value: Any, path: str, warnings: list[str]) -> str:
    if isinstance(value, str) and value in AI_PREPROCESS_ENUMS[path]:
        return value
    if value is not None:
        warnings.append(f"{path} unsupported value {value!r}; using {AI_PREPROCESS_DEFAULTS[path]!r}.")
    return AI_PREPROCESS_DEFAULTS[path]


def _confidence_value(value: Any, warnings: list[str]) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 0.0 <= float(value) <= 1.0:
        return float(value)
    warnings.append(f"confidence unsupported value {value!r}; using None.")
    return None


def _safe_provider_label(config: AiProviderConfig | None) -> str | None:
    if config is None:
        return None
    return config.base_url


def _safe_error_message(error: Exception, api_key: str | None = None) -> str:
    message = str(error)
    secrets = {api_key or "", os.environ.get(AI_PROVIDER_ENV_KEYS["api_key"], "")}
    for secret in secrets:
        if secret:
            message = message.replace(secret, "[redacted]")
    return message


def _mime_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    return "image/png"
