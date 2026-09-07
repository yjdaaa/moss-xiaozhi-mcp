from __future__ import annotations

import json
import os
import shutil
from abc import ABC, abstractmethod
from copy import deepcopy
from pathlib import Path
from typing import Any


class MaterialRepositoryError(ValueError):
    """Raised when a material repository cannot be read or written."""


class MaterialLibraryRepository(ABC):
    """Storage contract for material libraries.

    The service layer only depends on this contract, so a future SQLite
    implementation can replace the JSON repository without changing MCP or
    Web callers.
    """

    @abstractmethod
    def load(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def save(self, payload: dict[str, Any], *, backup: bool = True) -> None:
        raise NotImplementedError

    @abstractmethod
    def export_data(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def import_data(
        self,
        payload: dict[str, Any],
        *,
        replace: bool = False,
        backup: bool = True,
    ) -> dict[str, Any]:
        raise NotImplementedError


class JsonMaterialRepository(MaterialLibraryRepository):
    """Atomic JSON-file repository used by the current local deployment."""

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise MaterialRepositoryError(f"读取材料参数失败: {exc}") from exc
        if not isinstance(payload, dict):
            raise MaterialRepositoryError("材料库内容必须是 JSON 对象")
        return payload

    def save(self, payload: dict[str, Any], *, backup: bool = True) -> None:
        if not isinstance(payload, dict):
            raise MaterialRepositoryError("材料库内容必须是 JSON 对象")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if backup and self.path.is_file():
            try:
                shutil.copy2(self.path, self.backup_path)
            except OSError as exc:
                raise MaterialRepositoryError(f"备份材料参数失败: {exc}") from exc
        temporary = self.path.with_name(f"{self.path.name}.tmp")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temporary, self.path)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise MaterialRepositoryError(f"保存材料参数失败: {exc}") from exc

    def export_data(self) -> dict[str, Any]:
        return deepcopy(self.load())

    def import_data(
        self,
        payload: dict[str, Any],
        *,
        replace: bool = False,
        backup: bool = True,
    ) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise MaterialRepositoryError("导入材料库必须是 JSON 对象")
        current = {} if replace else self.load()
        merged = _merge_payload(current, payload)
        self.save(merged, backup=backup)
        return deepcopy(merged)

    @property
    def backup_path(self) -> Path:
        return self.path.with_name(f"{self.path.name}.bak")


def _merge_payload(current: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """Merge material entries while preserving unrelated top-level metadata."""

    result = deepcopy(current)
    for key, value in incoming.items():
        if key != "materials" or not isinstance(value, dict):
            result[key] = deepcopy(value)
            continue
        materials = result.setdefault("materials", {})
        if not isinstance(materials, dict):
            materials = {}
            result["materials"] = materials
        for material, entry in value.items():
            if not isinstance(entry, dict):
                materials[material] = deepcopy(entry)
                continue
            existing = materials.get(material)
            if not isinstance(existing, dict):
                materials[material] = deepcopy(entry)
                continue
            materials[material] = _merge_mapping(existing, entry)
    return result


def _merge_mapping(current: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(current)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_mapping(result[key], value)
        elif isinstance(value, list) and isinstance(result.get(key), list) and key == "aliases":
            result[key] = list(dict.fromkeys([*result[key], *value]))
        else:
            result[key] = deepcopy(value)
    return result
