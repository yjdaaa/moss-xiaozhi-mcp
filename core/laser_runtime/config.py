from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Mapping

from .models import LaserError, OperationResult


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = REPO_ROOT / ".runtime"
# 默认雕刻输出目录；个人开发机习惯放在桌面，开源后默认改为仓库内 generated 目录，
# 可通过环境变量 LASERGRBL_JOB_DIR / LASER_PREPARED_GCODE_DIR 覆盖。
DEFAULT_ENGRAVING_DIR = REPO_ROOT / "generated"


@dataclass(frozen=True)
class ConfigIssue:
    field: str
    scope: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"field": self.field, "scope": self.scope, "message": self.message}


@dataclass(frozen=True)
class LaserSettings:
    machine_name: str = "翼宿 V1.0"
    work_area_width_mm: float = 100.0
    work_area_height_mm: float = 100.0
    image_fit_box_width_mm: float = 50.0
    image_fit_box_height_mm: float = 50.0
    safe_margin_mm: float = 5.0
    laser_optical_power_w: float = 5.0
    laser_s_max: int = 1000
    engraving_mode: str = "raster"
    raster_overscan_mm: float = 1.5
    default_image_file: str = ""
    default_gcode_file: str = ""
    default_serial_port: str = ""
    baudrate: int = 115200
    engraving_dir: Path = DEFAULT_ENGRAVING_DIR
    default_connection_mode: str = "network"
    network_host: str = ""
    network_http_port: int = 80
    network_telnet_port: int = 23
    network_timeout: float = 5.0
    runtime_dir: Path = RUNTIME_ROOT / "laser_runtime"
    material_params_file: Path = RUNTIME_ROOT / "lasergrbl_materials.json"
    calibration_dir: Path = RUNTIME_ROOT / "lasergrbl_calibrations"
    max_passes: int = 10
    travel_rate: int = 3000
    min_feed_rate: int = 50
    prepared_gcode_dir: Path = DEFAULT_ENGRAVING_DIR
    text_image_output_dir: Path = RUNTIME_ROOT / "generated_images"
    ai_output_dir: Path = REPO_ROOT / "out"
    ai_assets_dir: Path = REPO_ROOT / "generated_assets"
    ai_material_library: Path = RUNTIME_ROOT / "lasergrbl_materials.json"
    ai_state_path: Path = RUNTIME_ROOT / "ai_laser_gcode_state" / "state.json"
    ai_input_dir: Path = REPO_ROOT / "laser_inputs"
    ai_candidate_ttl_seconds: int = 1800
    issues: tuple[ConfigIssue, ...] = field(default_factory=tuple)

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        repo_root: Path | None = None,
    ) -> "LaserSettings":
        values = dict(os.environ if env is None else env)
        root = Path(repo_root or REPO_ROOT)
        runtime_root = RUNTIME_ROOT if root == REPO_ROOT else root / ".runtime"
        issues: list[ConfigIssue] = []

        def integer(name, default, scope, minimum=None, maximum=None):
            raw = values.get(name)
            if raw in (None, ""):
                return default
            try:
                number = int(raw)
            except (TypeError, ValueError):
                issues.append(ConfigIssue(name, scope, "must be an integer"))
                return default
            if minimum is not None and number < minimum or maximum is not None and number > maximum:
                issues.append(ConfigIssue(name, scope, "is outside the allowed range"))
                return default
            return number

        def decimal(name, default, scope, minimum=None, allow_zero=False):
            raw = values.get(name)
            if raw in (None, ""):
                return default
            try:
                number = float(raw)
            except (TypeError, ValueError):
                issues.append(ConfigIssue(name, scope, "must be a number"))
                return default
            if not math.isfinite(number):
                issues.append(ConfigIssue(name, scope, "must be a finite number"))
                return default
            if minimum is not None and (number < minimum if allow_zero else number <= minimum):
                issues.append(ConfigIssue(name, scope, "is outside the allowed range"))
                return default
            return number

        def path_value(name, default):
            raw = str(values.get(name) or "").strip()
            path = Path(raw).expanduser() if raw else Path(default)
            return path if path.is_absolute() else root / path

        connection_mode = str(values.get("LASER_DEFAULT_CONNECTION_MODE") or "network").strip().lower()
        if connection_mode not in {"serial", "network"}:
            issues.append(
                ConfigIssue(
                    "LASER_DEFAULT_CONNECTION_MODE",
                    "routing",
                    "must be serial or network",
                )
            )
            connection_mode = "network"

        engraving_mode = str(values.get("LASER_ENGRAVING_MODE") or "raster").strip().lower()
        if engraving_mode not in {"raster", "outline"}:
            issues.append(ConfigIssue("LASER_ENGRAVING_MODE", "gcode", "must be raster or outline"))
            engraving_mode = "raster"

        engraving_dir = path_value("LASERGRBL_JOB_DIR", root / "generated")
        material_params_file = path_value("LASER_MATERIAL_PARAMS_FILE", runtime_root / "lasergrbl_materials.json")
        return cls(
            machine_name=str(values.get("LASERGRBL_MACHINE_NAME") or "翼宿 V1.0"),
            work_area_width_mm=decimal("LASERGRBL_WORK_AREA_WIDTH_MM", 100.0, "machine", 0),
            work_area_height_mm=decimal("LASERGRBL_WORK_AREA_HEIGHT_MM", 100.0, "machine", 0),
            image_fit_box_width_mm=decimal("LASERGRBL_DEFAULT_IMAGE_FIT_BOX_WIDTH_MM", 50.0, "gcode", 0),
            image_fit_box_height_mm=decimal("LASERGRBL_DEFAULT_IMAGE_FIT_BOX_HEIGHT_MM", 50.0, "gcode", 0),
            safe_margin_mm=decimal("LASERGRBL_SAFE_MARGIN_MM", 5.0, "gcode", 0, allow_zero=True),
            laser_optical_power_w=decimal("LASERGRBL_LASER_OPTICAL_POWER_W", 5.0, "machine", 0),
            laser_s_max=integer("GRBL_LASER_S_MAX", 1000, "machine", 1),
            engraving_mode=engraving_mode,
            raster_overscan_mm=decimal("LASER_RASTER_OVERSCAN_MM", 1.5, "gcode", 0, allow_zero=True),
            default_image_file=str(values.get("LASERGRBL_DEFAULT_IMAGE") or ""),
            default_gcode_file=str(values.get("GRBL_DEFAULT_FILE") or ""),
            default_serial_port=str(values.get("GRBL_DEFAULT_PORT") or ""),
            baudrate=integer("GRBL_BAUDRATE", 115200, "serial", 1),
            engraving_dir=engraving_dir,
            default_connection_mode=connection_mode,
            network_host=str(values.get("LASER_NETWORK_HOST") or "").strip(),
            network_http_port=integer("LASER_NETWORK_HTTP_PORT", 80, "network", 1, 65535),
            network_telnet_port=integer("LASER_NETWORK_TELNET_PORT", 23, "network", 1, 65535),
            network_timeout=decimal("LASER_NETWORK_TIMEOUT", 5.0, "network", 0),
            runtime_dir=path_value("LASER_RUNTIME_DIR", runtime_root / "laser_runtime"),
            material_params_file=material_params_file,
            calibration_dir=path_value("LASER_CALIBRATION_DIR", runtime_root / "lasergrbl_calibrations"),
            max_passes=integer("LASER_MAX_PASSES", 10, "calibration", 1),
            travel_rate=integer("LASER_TRAVEL_RATE", 3000, "calibration", 1),
            min_feed_rate=integer("LASER_MIN_FEED_RATE", 50, "calibration", 1),
            prepared_gcode_dir=path_value("LASER_PREPARED_GCODE_DIR", engraving_dir),
            text_image_output_dir=path_value("TEXT_IMAGE_OUTPUT_DIR", runtime_root / "generated_images"),
            ai_output_dir=path_value("AI_LASER_GCODE_OUTPUT_DIR", root / "out"),
            ai_assets_dir=path_value("AI_LASER_GCODE_ASSETS_DIR", root / "generated_assets"),
            ai_material_library=path_value("AI_LASER_MATERIAL_LIBRARY", material_params_file),
            ai_state_path=path_value("AI_LASER_GCODE_STATE_PATH", runtime_root / "ai_laser_gcode_state" / "state.json"),
            ai_input_dir=path_value("AI_LASER_INPUT_DIR", root / "laser_inputs"),
            ai_candidate_ttl_seconds=integer("AI_LASER_CANDIDATE_TTL_SECONDS", 1800, "ai", 0),
            issues=tuple(issues),
        )

    def issues_for(self, scope: str) -> tuple[ConfigIssue, ...]:
        return tuple(issue for issue in self.issues if issue.scope == scope)

    def check_scopes(self, *scopes: str) -> OperationResult:
        requested = {str(scope).strip() for scope in scopes if str(scope).strip()}
        issues = [issue for issue in self.issues if issue.scope in requested]
        if not issues:
            return OperationResult.ok(self)
        return OperationResult.failure(
            LaserError(
                "config_invalid",
                "laser configuration is invalid for the requested capability",
                {"issues": [issue.to_dict() for issue in issues]},
            )
        )


@lru_cache(maxsize=1)
def get_laser_settings() -> LaserSettings:
    return LaserSettings.from_env()


def reset_laser_settings_cache() -> None:
    get_laser_settings.cache_clear()


def configuration_diagnostics(settings: LaserSettings | None = None) -> list[dict[str, str]]:
    current = settings or get_laser_settings()
    return [issue.to_dict() for issue in current.issues]
