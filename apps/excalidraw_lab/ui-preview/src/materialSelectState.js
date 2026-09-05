/**
 * Pure helpers for material library dropdown + custom fallback.
 * Shared by main.jsx and unit tests; keep React-free.
 */

export const CUSTOM_OPTION_VALUE = "__custom__";
export const CUSTOM_OPTION_LABEL = "自定义…";

export function extractUiMaterialOptions(httpJson) {
  if (!httpJson || httpJson.success !== true || !httpJson.result) {
    return null;
  }
  const materials = httpJson.result.materials;
  if (!Array.isArray(materials)) {
    return null;
  }
  return materials;
}

export function thicknessKey(value) {
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) {
    return null;
  }
  if (Math.abs(number - Math.round(number)) < 1e-9) {
    return String(Math.round(number));
  }
  return String(number);
}

export function thicknessesEqual(a, b) {
  const keyA = thicknessKey(a);
  const keyB = thicknessKey(b);
  return keyA !== null && keyA === keyB;
}

export function normalizeMaterialOptions(rawMaterials) {
  if (!Array.isArray(rawMaterials)) {
    return [];
  }
  const projected = [];
  for (const item of rawMaterials) {
    if (!item || typeof item !== "object") continue;
    const name = String(item.name ?? "").trim();
    if (!name) continue;
    const source = Array.isArray(item.thicknesses) ? item.thicknesses : [];
    const deduped = new Map();
    for (const raw of source) {
      const number = Number(raw);
      if (!Number.isFinite(number) || number <= 0) continue;
      const key = thicknessKey(number);
      if (key == null) continue;
      if (!deduped.has(key)) {
        deduped.set(key, Math.abs(number - Math.round(number)) < 1e-9 ? Math.round(number) : number);
      }
    }
    if (deduped.size === 0) continue;
    const thicknesses = [...deduped.entries()]
      .sort((a, b) => Number(a[0]) - Number(b[0]))
      .map((entry) => entry[1]);
    projected.push({ name, thicknesses });
  }
  projected.sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
  return projected;
}

export function findMaterialOption(materials, name) {
  const needle = String(name ?? "").trim();
  if (!needle) return null;
  return materials.find((item) => item.name === needle) || null;
}

export function pickThicknessForMaterial(materialOption, preferredThickness) {
  if (!materialOption || !Array.isArray(materialOption.thicknesses) || materialOption.thicknesses.length === 0) {
    return preferredThickness == null ? "" : String(preferredThickness);
  }
  const match = materialOption.thicknesses.find((value) => thicknessesEqual(value, preferredThickness));
  if (match !== undefined) {
    return String(match);
  }
  return String(materialOption.thicknesses[0]);
}

/**
 * Restore UI state from historical material/thickness + loaded library options.
 * mode: "library" | "custom"
 */
export function restoreMaterialSelectState({ materials, savedMaterial, savedThickness }) {
  const library = normalizeMaterialOptions(materials);
  const savedName = String(savedMaterial ?? "").trim();
  const savedTh = savedThickness == null ? "" : String(savedThickness);

  if (!library.length) {
    return {
      mode: "custom",
      materials: library,
      material: savedName,
      thickness_mm: savedTh,
      selectValue: CUSTOM_OPTION_VALUE,
    };
  }

  const hit = findMaterialOption(library, savedName);
  if (!hit) {
    return {
      mode: "custom",
      materials: library,
      material: savedName,
      thickness_mm: savedTh,
      selectValue: CUSTOM_OPTION_VALUE,
    };
  }

  return {
    mode: "library",
    materials: library,
    material: hit.name,
    thickness_mm: pickThicknessForMaterial(hit, savedTh),
    selectValue: hit.name,
  };
}

export function applyMaterialSelectChange({ materials, currentThickness, selectValue, customMaterial }) {
  const library = normalizeMaterialOptions(materials);
  if (selectValue === CUSTOM_OPTION_VALUE) {
    return {
      mode: "custom",
      materials: library,
      material: customMaterial == null ? "" : String(customMaterial),
      thickness_mm: currentThickness == null ? "" : String(currentThickness),
      selectValue: CUSTOM_OPTION_VALUE,
    };
  }
  const hit = findMaterialOption(library, selectValue) || library[0] || null;
  if (!hit) {
    return {
      mode: "custom",
      materials: library,
      material: customMaterial == null ? "" : String(customMaterial),
      thickness_mm: currentThickness == null ? "" : String(currentThickness),
      selectValue: CUSTOM_OPTION_VALUE,
    };
  }
  return {
    mode: "library",
    materials: library,
    material: hit.name,
    thickness_mm: pickThicknessForMaterial(hit, currentThickness),
    selectValue: hit.name,
  };
}

export function returnToLibrary({ materials, preferredMaterial, preferredThickness }) {
  const library = normalizeMaterialOptions(materials);
  if (!library.length) {
    return {
      mode: "custom",
      materials: library,
      material: preferredMaterial == null ? "" : String(preferredMaterial),
      thickness_mm: preferredThickness == null ? "" : String(preferredThickness),
      selectValue: CUSTOM_OPTION_VALUE,
    };
  }
  const hit = findMaterialOption(library, preferredMaterial) || library[0];
  return {
    mode: "library",
    materials: library,
    material: hit.name,
    thickness_mm: pickThicknessForMaterial(hit, preferredThickness),
    selectValue: hit.name,
  };
}

/**
 * Load UI material options with unified try/catch.
 * Covers 404, network errors, success=false, non-array, empty library,
 * HTTP 200 + HTML SPA fallback, and response.json() parse errors.
 */
export async function loadUiMaterialOptions(fetchImpl = fetch) {
  try {
    const response = await fetchImpl("/api/ui/material-options", {
      method: "GET",
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    // Even HTTP 200 may be HTML (standalone 8777 SPA fallback).
    const json = await response.json();
    const extracted = extractUiMaterialOptions(json);
    if (extracted === null) {
      return { ok: false, materials: [], reason: "invalid_payload" };
    }
    const materials = normalizeMaterialOptions(extracted);
    if (!materials.length) {
      return { ok: true, materials: [], reason: "empty" };
    }
    return { ok: true, materials, reason: "ok" };
  } catch (error) {
    return {
      ok: false,
      materials: [],
      reason: "load_error",
      error: error && error.message ? error.message : String(error || "load_error"),
    };
  }
}
