from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from core.ai_laser_gcode.ai_assistant import AiAssistResult, parse_ai_preprocess_suggestion
from core.ai_laser_gcode.models import ImagePreprocessPlan, ImagePreprocessResult, JobParams


def prepare_processed_image(image_path: Path, params: JobParams, ai_result: AiAssistResult | None = None, force_binary: bool = False) -> ImagePreprocessResult:
    plan = _resolve_preprocess_plan(ai_result)
    try:
        from PIL import Image, ImageEnhance, ImageOps
    except ImportError as error:
        raise ValueError("Image preprocessing requires Pillow; install pillow or use ASCII PGM P2") from error
    with Image.open(image_path) as source_image:
        image = source_image.convert("L")
    if plan.brightness != 1.0:
        image = ImageEnhance.Brightness(image).enhance(plan.brightness)
    if plan.contrast != 1.0:
        image = ImageEnhance.Contrast(image).enhance(plan.contrast)
    if plan.invert:
        image = ImageOps.invert(image)
    threshold = plan.threshold
    cleanup_background = plan.cleanup_background
    # Job-level manual threshold wins for force-binary / outline paths.
    job_threshold = getattr(params, "threshold", -1)
    manual_threshold = bool(getattr(params, "manual_threshold", False))
    if manual_threshold and job_threshold is not None and int(job_threshold) >= 0:
        threshold = int(job_threshold)
    if cleanup_background in {"white", "light", "auto"}:
        threshold = threshold if threshold is not None else _otsu_threshold(image)
        image = image.point(lambda value: 255 if value >= threshold else value)
    if threshold is not None:
        image = image.point(lambda value: 0 if value <= threshold else 255)
    if force_binary and threshold is None:
        if manual_threshold and job_threshold is not None and int(job_threshold) >= 0:
            threshold = int(job_threshold)
        else:
            threshold = _otsu_threshold(image)
        image = image.point(lambda value: 0 if value <= threshold else 255)
    with NamedTemporaryFile(prefix="ai_laser_preprocess_", suffix=".png", delete=False) as processed_file:
        processed_path = Path(processed_file.name)
    image.save(processed_path)
    return ImagePreprocessResult(
        processed_path=processed_path,
        source=plan.source,
        invert=plan.invert,
        threshold=threshold,
        brightness=plan.brightness,
        contrast=plan.contrast,
        cleanup_background=cleanup_background,
        grayscale_formula=plan.grayscale_formula,
        grayscale=True,
        binarized=threshold is not None,
        dithered=False,
        reason=plan.reason,
        warnings=plan.warnings or [],
    )


def _resolve_preprocess_plan(ai_result: AiAssistResult | None) -> ImagePreprocessPlan:
    if ai_result is not None and ai_result.status == "used":
        suggestion = parse_ai_preprocess_suggestion(ai_result.recommendations)
        if suggestion.valid:
            invert = suggestion.preprocess["invert"] == "yes"
            cleanup_background = suggestion.preprocess["cleanup_background"]
            threshold = 128 if cleanup_background in {"white", "light"} else None
            return ImagePreprocessPlan(source="ai_suggested", invert=invert, threshold=threshold, cleanup_background=cleanup_background, reason=suggestion.reason, warnings=[])
        return ImagePreprocessPlan(source="local_auto", cleanup_background="auto", warnings=suggestion.validation_warnings)
    return ImagePreprocessPlan(source="default")


def _otsu_threshold(image: Any) -> int:
    histogram = image.histogram()
    total = sum(histogram)
    sum_total = sum(index * count for index, count in enumerate(histogram))
    sum_background = 0.0
    weight_background = 0
    best_threshold = 128
    max_variance = -1.0
    for threshold, count in enumerate(histogram):
        weight_background += count
        if weight_background == 0:
            continue
        weight_foreground = total - weight_background
        if weight_foreground == 0:
            break
        sum_background += threshold * count
        mean_background = sum_background / weight_background
        mean_foreground = (sum_total - sum_background) / weight_foreground
        variance = weight_background * weight_foreground * (mean_background - mean_foreground) ** 2
        if variance > max_variance:
            max_variance = variance
            best_threshold = threshold
    return best_threshold
