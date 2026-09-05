import argparse
import os
import sys
from pathlib import Path

from core.ai_laser_gcode.calibration import generate_calibration_matrix, save_calibration_result
from core.ai_laser_gcode.generator import generate_job
from core.ai_laser_gcode.material_source import MaterialSourceError, MaterialSourceRequest, prepare_material_source


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] in {"calibration-matrix", "save-calibration"}:
        return _calibration_main(sys.argv[1:])
    parser = argparse.ArgumentParser(description="Generate safe GRBL outline G-code from an image and prompt.")
    parser.add_argument("image", nargs="?", type=Path, help="Input image path. For text or image-search sources, this can be omitted when --prompt is used.")
    parser.add_argument("prompt", nargs="?", help="Natural-language or structured job parameters.")
    parser.add_argument("--prompt", dest="prompt_option", help="Prompt text, useful when no positional image path is needed.")
    parser.add_argument("--source-type", choices=("file", "text", "image-search", "ai-image"), default="file", help="Material source type before conversion.")
    parser.add_argument("--text", help="Text to render when --source-type text is used.")
    parser.add_argument("--image-url", help="Direct image URL when --source-type image-search is used.")
    parser.add_argument("--assets-dir", type=Path, default=Path("generated_assets"), help="Directory for generated/downloaded material assets.")
    parser.add_argument("--keep-grayscale", action="store_true", help="Keep grayscale material instead of default black/white thresholding.")
    parser.add_argument("--text-style", choices=("filled", "outline"), default="filled", help="Text material style.")
    parser.add_argument("--out", type=Path, default=Path("out"), help="Output directory.")
    parser.add_argument("--format", choices=("gcode", "nc"), help="Output main file format. Overrides prompt format.")
    parser.add_argument("--mode", choices=("auto", "outline", "raster"), help="Generation mode. Overrides prompt mode keywords.")
    parser.add_argument("--trace-algorithm", choices=("vector",), help="Outline tracing algorithm. vector is the built-in low-dependency ordered boundary backend.")
    parser.add_argument("--vector-simplify-factor", type=float, help="Vector trace simplification multiplier. Default 1.0 keeps compatible output; larger values reduce points and detail.")
    parser.add_argument("--fill-strategy", choices=("auto", "none", "hatch", "zigzag"), help="Outline fill strategy. Default none preserves outline-only output.")
    parser.add_argument("--fill-spacing-mm", type=float, help="Spacing between hatch or zigzag fill lines in millimeters.")
    parser.add_argument("--raster-scan-direction", choices=("auto", "horizontal", "vertical"), help="Raster scan direction. 'auto' keeps horizontal output unless vertical clearly reduces scan work.")
    parser.add_argument("--raster-output-strategy", choices=("auto", "segment", "scanline"), help="Raster G-code output strategy. auto keeps sparse jobs segmented and uses continuous scanlines for fragmented rasters.")
    parser.add_argument("--dither-algorithm", choices=("auto", "threshold", "floyd_steinberg", "atkinson", "sierra_lite"), help="Raster binarization. threshold keeps rendered line art black/white without error-diffusion stippling.")
    parser.add_argument("--arc-output", action="store_true", help="Allow optional G2/G3 arc output for compatible outline paths.")
    parser.add_argument("--firmware-supports-arc", action="store_true", help="Declare that the target GRBL firmware accepts G2/G3 arc moves.")
    parser.add_argument("--arc-tolerance-mm", type=float, help="Maximum radial fitting error for optional G2/G3 arc output.")
    parser.add_argument("--material-library", type=Path, help="Material library JSON path. Overrides the default local library.")
    parser.add_argument("--thickness-mm", type=float, help="Material thickness in millimeters.")
    parser.add_argument("--task-type", choices=("engrave_photo", "engrave_logo", "cut_contour"), help="Material-library task type.")
    parser.add_argument("--inspect", action="store_true", help="Only inspect material matching and safety status; do not write G-code or preview.")
    parser.add_argument("--ai-assist", action="store_true", help="Use configured OpenAI-compatible AI assistance for image explanation and preprocessing suggestions.")
    parser.add_argument("--allow-image-upload", action="store_true", help="Allow sending the input image to the configured AI service. Requires --ai-assist.")
    args = parser.parse_args()

    prompt = args.prompt_option or args.prompt
    image = args.image
    if args.source_type != "file" and prompt is None and image is not None:
        prompt = str(image)
        image = None
    if not prompt:
        parser.exit(2, "Generation failed: Prompt is required.\n")
    try:
        asset = None
        if args.source_type == "file":
            if image is None:
                parser.exit(2, "Generation failed: Image path is required for file source.\n")
            image_path = image
        else:
            asset = prepare_material_source(MaterialSourceRequest(source_type=args.source_type, text=args.text, url=args.image_url, assets_dir=args.assets_dir, keep_grayscale=args.keep_grayscale, text_style=args.text_style, base_url=os.environ.get("AI_LASER_OPENAI_BASE_URL"), api_key=os.environ.get("AI_LASER_OPENAI_API_KEY"), model=os.environ.get("AI_LASER_OPENAI_MODEL")))
            image_path = asset.processed_path
        bundle = generate_job(image_path, prompt, args.out, output_format=args.format, mode=args.mode, trace_algorithm=args.trace_algorithm, vector_simplify_factor=args.vector_simplify_factor, fill_strategy=args.fill_strategy, fill_spacing_mm=args.fill_spacing_mm, material_library_path=args.material_library, thickness_mm=args.thickness_mm, task_type=args.task_type, inspect=args.inspect, ai_assist=args.ai_assist, allow_image_upload=args.allow_image_upload, arc_output=args.arc_output, firmware_supports_arc=args.firmware_supports_arc, arc_tolerance_mm=args.arc_tolerance_mm, dither_algorithm=args.dither_algorithm, raster_scan_direction=args.raster_scan_direction, raster_output_strategy=args.raster_output_strategy)
    except MaterialSourceError as error:
        parser.exit(2, f"Material source failed: {error}\n")
    except Exception as error:
        parser.exit(2, f"Generation failed: {error}\n")
    if asset is not None:
        print(f"Asset original: {asset.original_path}")
        print(f"Asset processed: {asset.processed_path}")
    print(f"G-code: {bundle.gcode_path if bundle.gcode_path else 'not generated'}")
    print(f"Preview: {bundle.preview_path if bundle.preview_path else 'not generated'}")
    print(f"Summary: {bundle.summary_path}")
    return 0


def _calibration_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Generate and record calibration matrix results.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    matrix = subparsers.add_parser("calibration-matrix", help="Generate a safe 3x3 calibration matrix.")
    matrix.add_argument("material")
    matrix.add_argument("--out", type=Path, default=Path("out"))
    matrix.add_argument("--format", choices=("gcode", "nc"), default="gcode")
    matrix.add_argument("--task-type", choices=("engrave_photo", "engrave_logo", "cut_contour"), default="engrave_photo")
    matrix.add_argument("--thickness-mm", type=float, default=1.0)
    matrix.add_argument("--power", type=int, default=120)
    matrix.add_argument("--speed", type=int, default=1200)
    matrix.add_argument("--passes", type=int, default=1)
    matrix.add_argument("--cell-size-mm", type=float, default=12.0)
    matrix.add_argument("--gap-mm", type=float, default=4.0)

    save = subparsers.add_parser("save-calibration", help="Record calibration feedback and optionally save verified parameters.")
    save.add_argument("summary", type=Path)
    save.add_argument("cell_id")
    save.add_argument("rating", choices=("good", "cut_through_cleanly", "too_light", "too_dark", "not_cut", "failed"))
    save.add_argument("--material-library", type=Path, default=Path("materials.json"))
    save.add_argument("--jobs", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "calibration-matrix":
            bundle = generate_calibration_matrix(args.material, args.out, output_format=args.format, task_type=args.task_type, thickness_mm=args.thickness_mm, power=args.power, speed=args.speed, passes=args.passes, cell_size_mm=args.cell_size_mm, gap_mm=args.gap_mm)
            print(f"G-code: {bundle.gcode_path}")
            print(f"Preview: {bundle.preview_path}")
            print(f"Summary: {bundle.summary_path}")
        else:
            saved = save_calibration_result(args.summary, args.cell_id, args.rating, args.material_library, args.jobs)
            print(f"Verified material saved: {saved}")
    except Exception as error:
        parser.exit(2, f"Calibration failed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
