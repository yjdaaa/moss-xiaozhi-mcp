import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.ai_laser_gcode.models import JobParams, MatchType, ParameterConfidence, ParameterSource, TaskType


DEFAULT_MACHINE_PROFILE_ID = "yisu-v1-100x100"
DEFAULT_MATERIAL_LIBRARY_PATH = Path("materials.json")
SUPPORTED_VERSION = 1
ENGRAVE_TASKS = {"engrave_photo", "engrave_logo"}


class MaterialLibraryError(ValueError):
    pass


@dataclass(frozen=True)
class MaterialRecord:
    machine_profile_id: str
    material: str
    thickness_mm: float
    task_type: TaskType
    confidence: ParameterConfidence
    mode: str
    speed: int
    power: int
    passes: int
    source: str
    aliases: tuple[str, ...] = ()
    material_group: str | None = None
    safety_note: str = "参考参数，必须先小样测试"
    pixel_size_mm: float | None = None
    line_interval_mm: float | None = None
    thickness_independent: bool = False


@dataclass(frozen=True)
class MaterialMatch:
    record: MaterialRecord | None
    match_type: MatchType


BUILTIN_RECORDS = [
    MaterialRecord(DEFAULT_MACHINE_PROFILE_ID, "wood", 3.0, "engrave_photo", "verified", "raster", 350, 450, 1, "user_tested_3mm_wood", ("木板", "木头", "椴木", "木料", "木牌", "木片", "木质", "plywood", "basswood", "balsa"), "wood_like", safety_note="3mm 木板实测可用；偏深，正式雕刻前确认焦距、通风和看火", pixel_size_mm=0.2),
    MaterialRecord(DEFAULT_MACHINE_PROFILE_ID, "wood", 3.0, "engrave_logo", "library", "outline", 900, 280, 1, "builtin_reference", ("木板", "木头", "椴木", "木料", "木牌", "木片", "木质", "plywood", "basswood", "balsa"), "wood_like"),
    MaterialRecord(DEFAULT_MACHINE_PROFILE_ID, "wood", 3.0, "cut_contour", "estimated", "outline", 300, 500, 1, "builtin_reference", ("木板", "木头", "椴木", "木料", "木牌", "木片", "木质", "plywood", "basswood", "balsa"), "wood_like", safety_note="MVP-0 不允许用 estimated 切割参数生成生产文件"),
    MaterialRecord(DEFAULT_MACHINE_PROFILE_ID, "paper", 0.2, "engrave_photo", "library", "raster", 1500, 80, 1, "builtin_reference", ("纸", "纸张"), "paper_like", pixel_size_mm=0.2, thickness_independent=True),
    MaterialRecord(DEFAULT_MACHINE_PROFILE_ID, "paper", 0.2, "engrave_logo", "library", "outline", 1500, 80, 1, "builtin_reference", ("纸", "纸张"), "paper_like", thickness_independent=True),
    MaterialRecord(DEFAULT_MACHINE_PROFILE_ID, "cowhide_leather", 1.5, "engrave_photo", "verified", "raster", 800, 300, 1, "user_tested_cowhide_leather_c3", ("牛皮", "皮料", "皮革", "cowhide", "leather"), "leather_like", safety_note="1.5mm 牛皮光栅实测 C3 可用；正式雕刻前确认焦距、通风和看火", pixel_size_mm=0.24, line_interval_mm=0.24),
    MaterialRecord(DEFAULT_MACHINE_PROFILE_ID, "test", 1.0, "engrave_photo", "library", "raster", 1200, 100, 1, "builtin_reference", ("通用", "测试"), "test", pixel_size_mm=0.2, thickness_independent=True),
    MaterialRecord(DEFAULT_MACHINE_PROFILE_ID, "test", 1.0, "engrave_logo", "library", "outline", 1200, 100, 1, "builtin_reference", ("通用", "测试"), "test", thickness_independent=True),
]


def load_material_records(path: Path | None) -> tuple[list[MaterialRecord], bool]:
    if path is None:
        if not DEFAULT_MATERIAL_LIBRARY_PATH.exists():
            return BUILTIN_RECORDS.copy(), True
        path = DEFAULT_MATERIAL_LIBRARY_PATH
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:
        raise MaterialLibraryError(f"材料库读不了/格式不对，所以不能确认安全参数。请检查文件路径和 JSON 格式：{error}") from error
    user_records = _parse_payload(payload)
    return _merge_records(BUILTIN_RECORDS, user_records), False


def match_material_record(params: JobParams, records: list[MaterialRecord]) -> MaterialMatch:
    if params.task_type is None or params.thickness_mm is None:
        return MaterialMatch(None, "none")
    candidates = [record for record in records if record.machine_profile_id == params.machine_profile_id and record.task_type == params.task_type]
    for record in candidates:
        if _name_matches(params.material, record) and _thickness_matches(params.thickness_mm, params.task_type, record):
            return MaterialMatch(record, "exact" if params.material == record.material else "alias")
    # nearest_engrave: same material + same task_type only; never for cut_contour
    if params.material_match_policy == "nearest_engrave" and params.task_type in ENGRAVE_TASKS:
        nearest = _nearest_named_record(params.material, params.thickness_mm, candidates)
        if nearest is not None:
            return MaterialMatch(nearest, "nearest_thickness")
    if params.task_type in ENGRAVE_TASKS:
        requested_group = _requested_group(params.material, records)
        if requested_group in {"wood_like", "paper_like", "test"}:
            for record in candidates:
                if record.material_group == requested_group and _thickness_matches(params.thickness_mm, params.task_type, record):
                    return MaterialMatch(record, "material_group_fallback")
    return MaterialMatch(None, "none")


def material_record_key(record: MaterialRecord) -> tuple[str, str, float, str]:
    return (record.machine_profile_id, record.material, record.thickness_mm, record.task_type)


def _parse_payload(payload: Any) -> list[MaterialRecord]:
    if not isinstance(payload, dict) or payload.get("version") != SUPPORTED_VERSION:
        raise MaterialLibraryError("材料库版本不支持，所以不能确认安全参数。请使用 version: 1 的材料库 JSON。")
    materials = payload.get("materials")
    if isinstance(materials, list):
        return [_parse_record(record) for record in materials]
    if isinstance(materials, dict):
        return _parse_lasergrbl_materials(materials)
    raise MaterialLibraryError("材料库格式不对：materials 必须是数组或材料映射对象。")


def _parse_record(payload: Any) -> MaterialRecord:
    if not isinstance(payload, dict):
        raise MaterialLibraryError("材料库格式不对：每条材料记录必须是对象。")
    try:
        task_type = payload["task_type"]
        confidence = payload["confidence"]
        mode = payload["mode"]
        if task_type not in {"engrave_photo", "engrave_logo", "cut_contour"}:
            raise ValueError("task_type 不支持")
        if confidence not in {"verified", "library", "estimated", "experimental"}:
            raise ValueError("confidence 不支持")
        if mode not in {"outline", "raster"}:
            raise ValueError("mode 不支持")
        aliases = payload.get("aliases", [])
        if not isinstance(aliases, list):
            raise ValueError("aliases 必须是数组")
        return MaterialRecord(
            machine_profile_id=str(payload["machine_profile_id"]),
            material=str(payload["material"]),
            thickness_mm=float(payload["thickness_mm"]),
            task_type=task_type,
            confidence=confidence,
            mode=mode,
            speed=int(payload["speed"]),
            power=int(payload["power"]),
            passes=int(payload["passes"]),
            source=str(payload.get("source", "user_library")),
            aliases=tuple(str(alias) for alias in aliases),
            material_group=payload.get("material_group"),
            safety_note=str(payload.get("safety_note", "用户材料库参数")),
            pixel_size_mm=float(payload["pixel_size_mm"]) if "pixel_size_mm" in payload else None,
            line_interval_mm=float(payload["line_interval_mm"]) if "line_interval_mm" in payload else None,
            thickness_independent=bool(payload.get("thickness_independent", False)),
        )
    except KeyError as error:
        raise MaterialLibraryError(f"材料库格式不对：缺少字段 {error.args[0]}，所以不能确认安全参数。") from error
    except (TypeError, ValueError) as error:
        raise MaterialLibraryError(f"材料库格式不对：{error}，所以不能确认安全参数。") from error


def _parse_lasergrbl_materials(materials: dict[str, Any]) -> list[MaterialRecord]:
    records: list[MaterialRecord] = []
    for material, payload in materials.items():
        if not isinstance(payload, dict):
            continue
        aliases = _lasergrbl_aliases(material, payload.get("aliases", []))
        material_group = _infer_material_group(material, aliases)
        thicknesses = payload.get("thicknesses", {})
        if not isinstance(thicknesses, dict):
            continue
        for thickness_key, modes in thicknesses.items():
            try:
                thickness_mm = float(thickness_key)
            except (TypeError, ValueError):
                continue
            if not isinstance(modes, dict):
                continue
            records.extend(_lasergrbl_engrave_records(material, aliases, material_group, thickness_mm, modes.get("engrave")))
            cut_record = _lasergrbl_record(material, aliases, material_group, thickness_mm, "cut_contour", "outline", modes.get("cut"))
            if cut_record is not None:
                records.append(cut_record)
    return records


def _lasergrbl_engrave_records(
    material: str,
    aliases: tuple[str, ...],
    material_group: str | None,
    thickness_mm: float,
    engrave_entry: Any,
) -> list[MaterialRecord]:
    if not isinstance(engrave_entry, dict):
        return []
    if _is_lasergrbl_params_entry(engrave_entry):
        record = _lasergrbl_record(material, aliases, material_group, thickness_mm, "engrave_photo", "raster", engrave_entry)
        return [record] if record is not None else []
    records = []
    raster_record = _lasergrbl_record(material, aliases, material_group, thickness_mm, "engrave_photo", "raster", engrave_entry.get("raster"))
    outline_record = _lasergrbl_record(material, aliases, material_group, thickness_mm, "engrave_logo", "outline", engrave_entry.get("outline"))
    if raster_record is not None:
        records.append(raster_record)
    if outline_record is not None:
        records.append(outline_record)
    return records


def _lasergrbl_record(
    material: str,
    aliases: tuple[str, ...],
    material_group: str | None,
    thickness_mm: float,
    task_type: TaskType,
    mode: str,
    params: Any,
) -> MaterialRecord | None:
    if not _is_lasergrbl_params_entry(params):
        return None
    try:
        return MaterialRecord(
            machine_profile_id=DEFAULT_MACHINE_PROFILE_ID,
            material=material,
            thickness_mm=thickness_mm,
            task_type=task_type,
            confidence=_lasergrbl_confidence(params),
            mode=mode,
            speed=int(params["feed_rate"]),
            power=int(params["laser_max_power"]),
            passes=int(params.get("passes", 1)),
            source=f"lasergrbl_materials:{params.get('source', 'library')}",
            aliases=aliases,
            material_group=material_group,
            safety_note=str(params.get("notes", "本地材料库参数，发送前仍需确认焦距、通风和看火。")),
            pixel_size_mm=float(params["pixel_size_mm"]) if "pixel_size_mm" in params else None,
        )
    except (TypeError, ValueError):
        return None


def _is_lasergrbl_params_entry(value: Any) -> bool:
    return isinstance(value, dict) and "laser_max_power" in value and "feed_rate" in value


def _lasergrbl_confidence(params: dict[str, Any]) -> ParameterConfidence:
    source = str(params.get("source", "")).lower()
    if source in {"manual", "verified", "user_tested", "user_library"}:
        return "verified"
    if source in {"estimated", "experimental"}:
        return source
    return "library"


def _lasergrbl_aliases(material: str, aliases: Any) -> tuple[str, ...]:
    values = [material]
    if isinstance(aliases, list):
        values.extend(str(alias) for alias in aliases)
    deduped = []
    seen = set()
    for value in values:
        clean = str(value).strip()
        key = clean.lower()
        if clean and key not in seen:
            deduped.append(clean)
            seen.add(key)
    return tuple(deduped)


def _infer_material_group(material: str, aliases: tuple[str, ...]) -> str | None:
    names = {material.lower(), *(alias.lower() for alias in aliases)}
    if names & {"椴木", "椴木板", "木头", "木板", "木料", "木牌", "木片", "木质", "wood", "plywood", "basswood", "balsa"}:
        return "wood_like"
    if names & {"纸", "纸张", "纸板", "卡纸", "paper", "cardboard", "paperboard"}:
        return "paper_like"
    if names & {"通用", "测试", "test"}:
        return "test"
    if names & {"牛皮", "皮料", "皮革", "cowhide", "leather"}:
        return "leather_like"
    return None


def _merge_records(builtin_records: list[MaterialRecord], user_records: list[MaterialRecord]) -> list[MaterialRecord]:
    merged = {material_record_key(record): record for record in user_records}
    for record in builtin_records:
        merged.setdefault(material_record_key(record), record)
    return list(merged.values())


def _name_matches(material: str, record: MaterialRecord) -> bool:
    return material == record.material or material.lower() in {alias.lower() for alias in record.aliases}


def _thickness_matches(thickness_mm: float, task_type: str, record: MaterialRecord) -> bool:
    if task_type in ENGRAVE_TASKS and record.thickness_independent:
        return True
    return abs(thickness_mm - record.thickness_mm) < 1e-9


def _nearest_named_record(material: str, thickness_mm: float, candidates: list[MaterialRecord]) -> MaterialRecord | None:
    named = [record for record in candidates if _name_matches(material, record)]
    if not named:
        return None
    # Stable tie-break: smaller thickness first when distance equal.
    named.sort(key=lambda record: (abs(record.thickness_mm - thickness_mm), record.thickness_mm))
    return named[0]


def _requested_group(material: str, records: list[MaterialRecord]) -> str | None:
    for record in records:
        if _name_matches(material, record):
            return record.material_group
    if material in {"plywood", "basswood", "balsa"}:
        return "wood_like"
    if material in {"cardstock"}:
        return "paper_like"
    return None
