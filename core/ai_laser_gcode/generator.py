from pathlib import Path
from dataclasses import replace
import shutil
from typing import Any, Callable

from core.ai_laser_gcode.ai_assistant import AiProviderConfig, run_ai_assist
from core.ai_laser_gcode.gcode_writer import build_gcode_with_stats, build_raster_gcode
from core.ai_laser_gcode.image_preprocess import prepare_processed_image
from core.ai_laser_gcode.image_trace import trace_image
from core.ai_laser_gcode.image_router import resolve_job_mode
from core.ai_laser_gcode.material_library import load_material_records, match_material_record
from core.ai_laser_gcode.models import ImagePreprocessResult, OutputBundle
from core.ai_laser_gcode.parameters import parse_job_params
from core.ai_laser_gcode.path_optimizer import optimize_trace_paths
from core.ai_laser_gcode.preview import write_preview_png
from core.ai_laser_gcode.raster import rasterize_image
from core.ai_laser_gcode.raster_quality import select_raster_quality_profile
from core.ai_laser_gcode.safety import validate_job_params, validate_raster_result, validate_trace_result
from core.ai_laser_gcode.summary import write_summary
from core.ai_laser_gcode.vector_fill import apply_vector_fill


def generate_job(
    image_path: Path,
    prompt: str,
    output_dir: Path,
    output_format: str | None = None,
    size_mm: float | None = None,
    width_mm: float | None = None,
    height_mm: float | None = None,
    lock_aspect_ratio: bool | None = None,
    mode: str | None = None,
    trace_algorithm: str | None = None,
    material_library_path: Path | None = None,
    thickness_mm: float | None = None,
    task_type: str | None = None,
    inspect: bool = False,
    ai_assist: bool = False,
    allow_image_upload: bool = False,
    ai_config: AiProviderConfig | None = None,
    ai_client: Callable[[AiProviderConfig, Path, str], dict[str, Any]] | None = None,
    arc_output: bool | None = None,
    firmware_supports_arc: bool | None = None,
    arc_tolerance_mm: float | None = None,
    pixel_size_mm: float | None = None,
    vector_simplify_factor: float | None = None,
    dither_algorithm: str | None = None,
    threshold: int | None = None,
    raster_scan_direction: str | None = None,
    raster_output_strategy: str | None = None,
    raster_quality_strategy: str | None = None,
    fill_strategy: str | None = None,
    fill_spacing_mm: float | None = None,
    material_match_policy: str | None = None,
    send_policy: str | None = None,
    material: str | None = None,
) -> OutputBundle:
    params = parse_job_params(
        prompt,
        output_format=output_format,
        size_mm=size_mm,
        width_mm=width_mm,
        height_mm=height_mm,
        lock_aspect_ratio=lock_aspect_ratio,
        mode=mode,
        trace_algorithm=trace_algorithm,
        vector_simplify_factor=vector_simplify_factor,
        thickness_mm=thickness_mm,
        task_type=task_type,
        arc_output=arc_output,
        firmware_supports_arc=firmware_supports_arc,
        arc_tolerance_mm=arc_tolerance_mm,
        pixel_size_mm=pixel_size_mm,
        dither_algorithm=dither_algorithm,
        threshold=threshold,
        raster_scan_direction=raster_scan_direction,
        raster_output_strategy=raster_output_strategy,
        fill_strategy=fill_strategy,
        fill_spacing_mm=fill_spacing_mm,
        material_match_policy=material_match_policy,
        send_policy=send_policy,
        material=material,
    )
    records, used_builtin_only = load_material_records(material_library_path)
    ai_result = run_ai_assist(image_path, prompt, enabled=ai_assist, allow_image_upload=allow_image_upload, config=ai_config, client=ai_client)
    image_preprocess = prepare_processed_image(image_path, params, ai_result)
    processed_image_path = image_preprocess.processed_path
    try:
        params, routing = resolve_job_mode(processed_image_path, params)
        if params.mode == "outline" and not image_preprocess.binarized:
            processed_image_path.unlink(missing_ok=True)
            image_preprocess = prepare_processed_image(image_path, params, ai_result, force_binary=True)
            processed_image_path = image_preprocess.processed_path
        if params.mode == "outline" and image_preprocess.binarized:
            from core.ai_laser_gcode.image_router import analyze_image
            from core.ai_laser_gcode.raster import apply_line_art_detail_upscale
            from core.ai_laser_gcode.raster_quality import _classify_image

            metrics = routing.metrics if routing and routing.metrics else analyze_image(processed_image_path)
            image_type = _classify_image(metrics) if metrics is not None else None
            upscaled_path, did_upscale = apply_line_art_detail_upscale(processed_image_path, params, image_type=image_type)
            if did_upscale:
                if processed_image_path != image_path:
                    processed_image_path.unlink(missing_ok=True)
                processed_image_path = upscaled_path
                image_preprocess = replace(
                    image_preprocess,
                    processed_path=upscaled_path,
                    reason=(image_preprocess.reason or "") + ";line_art_detail_upscale",
                )
        params = _infer_task_type_after_routing(params)
        params = _resolve_material_params(params, records, used_builtin_only)
        validate_job_params(params)
        if routing.recommendation_status == "candidate_selection_required":
            return _generate_candidate_selection(image_path, processed_image_path, params, output_dir, routing, ai_result, image_preprocess)
        if routing.recommendation_status in {"rejected_low_confidence", "rejected_cut_contour_unclear"}:
            return _generate_rejection_summary(image_path, params, output_dir, routing, ai_result, image_preprocess)
        selected_raster = None
        if params.mode == "raster" and raster_quality_strategy:
            params, selected_raster, _profile = select_raster_quality_profile(
                processed_image_path,
                params,
                metrics=routing.metrics,
                strategy=raster_quality_strategy,
            )
        if inspect:
            output_dir.mkdir(parents=True, exist_ok=True)
            stem = image_path.stem or "laser_job"
            summary_path = output_dir / f"{stem}_summary.json"
            image_preprocess, processed_preview_path = _persist_processed_preview(image_preprocess, output_dir, stem)
            bundle = OutputBundle(gcode_path=None, preview_path=None, summary_path=summary_path, processed_preview_path=processed_preview_path)
            write_summary(summary_path, params, None, bundle, routing=routing, source_image_path=image_path, ai_assist=ai_result, image_preprocess=image_preprocess)
            return bundle
        trace = None
        raster = None
        optimization = None
        if params.mode == "raster":
            if selected_raster is None:
                from core.ai_laser_gcode.image_router import analyze_image
                from core.ai_laser_gcode.raster_quality import _classify_image

                metrics = routing.metrics if routing and routing.metrics else analyze_image(processed_image_path)
                image_type = _classify_image(metrics) if metrics is not None else None
                raster = rasterize_image(processed_image_path, params, image_type=image_type)
            else:
                raster = selected_raster
            validate_raster_result(raster)
        else:
            trace = trace_image(processed_image_path, params)
            validate_trace_result(trace)
            trace = apply_vector_fill(trace, params)
            optimization = optimize_trace_paths(trace, params.size_mm)
            trace = optimization.trace

        output_dir.mkdir(parents=True, exist_ok=True)
        stem = image_path.stem or "laser_job"
        gcode_path = output_dir / f"{stem}.{params.output_format}"
        preview_path = output_dir / f"{stem}_preview.png"
        summary_path = output_dir / f"{stem}_summary.json"
        image_preprocess, processed_preview_path = _persist_processed_preview(image_preprocess, output_dir, stem)
        bundle = OutputBundle(gcode_path=gcode_path, preview_path=preview_path, summary_path=summary_path, processed_preview_path=processed_preview_path)

        try:
            if raster is not None:
                gcode_path.write_text(build_raster_gcode(params, raster), encoding="utf-8")
                write_preview_png(raster, preview_path)
                write_summary(summary_path, params, raster, bundle, routing=routing, source_image_path=image_path, ai_assist=ai_result, image_preprocess=image_preprocess)
            elif trace is not None and optimization is not None:
                gcode, gcode_stats = build_gcode_with_stats(params, trace)
                gcode_path.write_text(gcode, encoding="utf-8")
                write_preview_png(trace, preview_path)
                write_summary(summary_path, params, trace, bundle, optimization.stats, routing=routing, source_image_path=image_path, ai_assist=ai_result, gcode_stats=gcode_stats, image_preprocess=image_preprocess)
        except Exception:
            for path in (gcode_path, preview_path, summary_path, processed_preview_path):
                if path is None:
                    continue
                path.unlink(missing_ok=True)
            try:
                output_dir.rmdir()
            except OSError:
                pass
            raise
        return bundle
    finally:
        processed_image_path.unlink(missing_ok=True)


def _resolve_material_params(params, records, used_builtin_only: bool):
    match = match_material_record(params, records)
    record = match.record
    if record is None:
        if params.manual_power or params.manual_feed_rate:
            return replace(
                params,
                parameter_source="manual_override",
                confidence="none",
                match_type="none",
                matched_thickness_mm=None,
                material_warnings=(),
                can_send=False,
                requires_sample_test=True,
                next_action="run_sample_test",
                message="使用手动输入的 S/F 参数，建议先做小样测试，不建议直接加工正式工件。",
            )
        return replace(
            params,
            confidence="none",
            parameter_source="none",
            match_type="none",
            matched_thickness_mm=None,
            material_warnings=(),
            can_send=False,
            requires_sample_test=True,
            next_action="create_calibration_matrix",
            message="没有找到可信材料参数，不能确认安全加工参数。请提供用户材料库或先做校准。",
        )
    power = params.power if params.manual_power else record.power
    feed_rate = params.feed_rate if params.manual_feed_rate else record.speed
    parameter_source = (
        "manual_override"
        if params.manual_power or params.manual_feed_rate
        else ("builtin_reference" if used_builtin_only or record.source == "builtin_reference" else record.confidence)
    )
    confidence = "none" if parameter_source == "manual_override" else record.confidence
    explicit_inputs = params.thickness_source == "explicit" and params.task_type_source == "explicit"
    is_group_fallback = match.match_type == "material_group_fallback"
    is_nearest = match.match_type == "nearest_thickness"
    # verified_only keeps historical gate; confirmed_material_record allows direct
    # same-material records into human confirmation, never material_group_fallback.
    if params.send_policy == "confirmed_material_record":
        can_send = explicit_inputs and not is_group_fallback and parameter_source != "manual_override"
    else:
        can_send = confidence == "verified" and explicit_inputs and not is_group_fallback
    verified_exact = (
        can_send
        and confidence == "verified"
        and not is_nearest
        and not is_group_fallback
        and parameter_source != "manual_override"
    )
    requires_sample_test = not verified_exact
    material_warnings: list[str] = []
    if is_nearest:
        material_warnings.append(
            f"未找到请求厚度 {params.thickness_mm} mm 的精确参数，已使用同材料最近厚度 {record.thickness_mm} mm 参数；正式加工前请确认并建议小样测试。"
        )
    if is_group_fallback:
        material_warnings.append("使用材料组回退参数，不能视为已验证的同材料记录。")
    if confidence not in {"verified", "none"} and can_send:
        material_warnings.append(f"当前参数可信度为 {confidence}，仅可进入人工确认，建议先小样测试。")
    if confidence == "verified" and can_send and not is_nearest:
        next_action = "ready_for_confirmation"
        message = "已匹配用户验证参数，安全检查通过后仍需上层确认再发送。"
    elif can_send and is_nearest:
        next_action = "ready_for_confirmation"
        message = (
            f"已匹配同材料最近厚度参数（请求 {params.thickness_mm} mm，参数 {record.thickness_mm} mm），"
            "可进入人工确认发送；建议先小样测试。"
        )
    elif can_send:
        next_action = "ready_for_confirmation"
        message = "已匹配材料库参数，可进入人工确认发送；建议先小样测试。"
    elif confidence == "library" or parameter_source == "manual_override" or is_group_fallback or is_nearest:
        next_action = "run_sample_test"
        message = "使用参考或非精确匹配参数生成文件，建议先做小样测试，不建议直接加工正式工件。"
    else:
        next_action = "create_calibration_matrix"
        message = "当前参数可信度不足，MVP-0 不建议生成生产文件，请先做校准矩阵。"
    return replace(
        params,
        material=record.material,
        power=power,
        feed_rate=feed_rate,
        pixel_size_mm=params.pixel_size_mm if params.manual_pixel_size else (record.pixel_size_mm or params.pixel_size_mm),
        passes=record.passes,
        confidence=confidence,
        parameter_source=parameter_source,
        match_type=match.match_type,
        matched_material=record.material,
        matched_thickness_mm=record.thickness_mm,
        material_group=record.material_group,
        material_warnings=tuple(material_warnings),
        safety_note=record.safety_note,
        can_send=can_send,
        requires_sample_test=requires_sample_test,
        next_action=next_action,
        message=message,
    )


def _infer_task_type_after_routing(params):
    if params.task_type is not None:
        return params
    if params.mode == "raster":
        return replace(params, task_type="engrave_photo", task_type_source="inferred")
    if params.mode == "outline":
        return replace(params, task_type="engrave_logo", task_type_source="inferred")
    return params


def _persist_processed_preview(image_preprocess: ImagePreprocessResult, output_dir: Path, stem: str) -> tuple[ImagePreprocessResult, Path | None]:
    processed_preview_path = output_dir / f"{stem}_processed.png"
    try:
        shutil.copyfile(image_preprocess.processed_path, processed_preview_path)
    except OSError:
        return image_preprocess, None
    return replace(image_preprocess, processed_path=processed_preview_path), processed_preview_path


def _generate_candidate_selection(image_path: Path, processed_image_path: Path, params, output_dir: Path, routing, ai_result=None, image_preprocess=None) -> OutputBundle:
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = image_path.stem or "laser_job"
    summary_path = output_dir / f"{stem}_summary.json"
    image_preprocess, processed_preview_path = _persist_processed_preview(image_preprocess, output_dir, stem) if image_preprocess else (image_preprocess, None)
    updated_candidates = []
    try:
        for candidate in routing.candidates or []:
            preview_path = output_dir / f"{stem}_candidate_{candidate.mode}_preview.png"
            candidate_params = replace(params, mode=candidate.mode)
            if candidate.mode == "raster":
                raster = rasterize_image(processed_image_path, candidate_params)
                validate_raster_result(raster)
                write_preview_png(raster, preview_path)
            else:
                trace = trace_image(processed_image_path, candidate_params)
                validate_trace_result(trace)
                optimization = optimize_trace_paths(trace, candidate_params.size_mm)
                write_preview_png(optimization.trace, preview_path)
            updated_candidates.append(replace(candidate, preview_path=preview_path, gcode_path=None, selectable=True, safety_status="preview_ready"))
        routing = replace(routing, candidates=updated_candidates)
        bundle = OutputBundle(gcode_path=None, preview_path=None, summary_path=summary_path, processed_preview_path=processed_preview_path)
        write_summary(summary_path, params, None, bundle, routing=routing, source_image_path=image_path, ai_assist=ai_result, image_preprocess=image_preprocess)
    except Exception:
        for path in output_dir.glob(f"{stem}_candidate_*_preview.png"):
            path.unlink(missing_ok=True)
        if processed_preview_path is not None:
            processed_preview_path.unlink(missing_ok=True)
        summary_path.unlink(missing_ok=True)
        try:
            output_dir.rmdir()
        except OSError:
            pass
        raise
    return bundle


def _generate_rejection_summary(image_path: Path, params, output_dir: Path, routing, ai_result=None, image_preprocess=None) -> OutputBundle:
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = image_path.stem or "laser_job"
    summary_path = output_dir / f"{stem}_summary.json"
    image_preprocess, processed_preview_path = _persist_processed_preview(image_preprocess, output_dir, stem) if image_preprocess else (image_preprocess, None)
    bundle = OutputBundle(gcode_path=None, preview_path=None, summary_path=summary_path, processed_preview_path=processed_preview_path)
    write_summary(summary_path, params, None, bundle, routing=routing, source_image_path=image_path, ai_assist=ai_result, image_preprocess=image_preprocess)
    return bundle
