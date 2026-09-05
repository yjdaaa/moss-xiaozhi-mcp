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

/**
 * Decide how to restore selection after a library refresh.
 * currentMode: "library" | "custom"
 * loaded: result of loadUiMaterialOptions
 * previousMaterials: last successfully loaded library (for transient failure)
 */
export function applyMaterialLibraryRefresh({
  loaded,
  currentMode,
  currentMaterial,
  currentThickness,
  previousMaterials = [],
  hasLoadedOnce = false,
}) {
  const mode = currentMode === "library" ? "library" : "custom";
  const material = currentMaterial == null ? "" : String(currentMaterial);
  const thickness = currentThickness == null ? "" : String(currentThickness);
  const previous = normalizeMaterialOptions(previousMaterials);

  if (!loaded || loaded.ok !== true) {
    if (!hasLoadedOnce) {
      return {
        applied: true,
        keepPrevious: false,
        mode: "custom",
        materials: [],
        material,
        thickness_mm: thickness,
        selectValue: CUSTOM_OPTION_VALUE,
        reason: loaded && loaded.reason ? loaded.reason : "load_error",
      };
    }
    return {
      applied: false,
      keepPrevious: true,
      mode,
      materials: previous,
      material,
      thickness_mm: thickness,
      selectValue: mode === "library" && findMaterialOption(previous, material)
        ? material
        : CUSTOM_OPTION_VALUE,
      reason: loaded && loaded.reason ? loaded.reason : "load_error",
    };
  }

  const library = normalizeMaterialOptions(loaded.materials);
  if (!library.length) {
    return {
      applied: true,
      keepPrevious: false,
      mode: "custom",
      materials: [],
      material,
      thickness_mm: thickness,
      selectValue: CUSTOM_OPTION_VALUE,
      reason: "empty",
    };
  }

  if (!hasLoadedOnce) {
    const initialHit = findMaterialOption(library, material);
    if (initialHit) {
      return {
        applied: true,
        keepPrevious: false,
        mode: "library",
        materials: library,
        material: initialHit.name,
        thickness_mm: pickThicknessForMaterial(initialHit, thickness),
        selectValue: initialHit.name,
        reason: "initial_recovery",
      };
    }
  }

  if (mode === "custom") {
    return {
      applied: true,
      keepPrevious: false,
      mode: "custom",
      materials: library,
      material,
      thickness_mm: thickness,
      selectValue: CUSTOM_OPTION_VALUE,
      reason: "ok",
    };
  }

  const hit = findMaterialOption(library, material);
  if (hit) {
    return {
      applied: true,
      keepPrevious: false,
      mode: "library",
      materials: library,
      material: hit.name,
      thickness_mm: pickThicknessForMaterial(hit, thickness),
      selectValue: hit.name,
      reason: "ok",
    };
  }

  const first = library[0];
  return {
    applied: true,
    keepPrevious: false,
    mode: "library",
    materials: library,
    material: first.name,
    thickness_mm: pickThicknessForMaterial(first, thickness),
    selectValue: first.name,
    reason: "material_removed",
  };
}

export function extractUiMaterialRecommendation(httpJson) {
  if (!httpJson || httpJson.success !== true || !httpJson.result || typeof httpJson.result !== "object") {
    return null;
  }
  const result = httpJson.result;
  const projected = {
    material: result.material,
    thickness_mm: result.thickness_mm,
    matched_thickness_mm: result.matched_thickness_mm,
    match: result.match,
    laser_mode: result.laser_mode,
    laser_max_power: result.laser_max_power,
    feed_rate: result.feed_rate,
    passes: result.passes,
  };
  if (result.laser_mode === "engrave" && result.engraving_mode) {
    projected.engraving_mode = result.engraving_mode;
  }
  return projected;
}

export function formatMaterialRecommendationSummary(result) {
  if (!result || typeof result !== "object") {
    return "材料库推荐：暂无匹配参数";
  }
  const modeLabel =
    result.laser_mode === "cut" ? "cut" : result.engraving_mode || result.laser_mode || "engrave";
  let text = `材料库推荐：S${result.laser_max_power ?? "-"} / F${result.feed_rate ?? "-"} / ${
    result.passes ?? "-"
  }次 / ${modeLabel}`;
  if (result.match === "nearest_thickness") {
    text += `（请求 ${result.thickness_mm}mm，使用最接近的 ${result.matched_thickness_mm}mm 参数）`;
  }
  return text;
}

/**
 * Load read-only recommendation summary.
 * Never writes advanced power/speed/passes inputs.
 */
export async function loadUiMaterialRecommendation(
  { material, thickness_mm, laser_mode, engraving_mode = "" },
  fetchImpl = fetch,
) {
  const name = String(material ?? "").trim();
  const thickness = Number(thickness_mm);
  const mode = String(laser_mode ?? "").trim() || "engrave";
  if (!name || !Number.isFinite(thickness) || thickness <= 0) {
    return { ok: false, recommendation: null, reason: "invalid_args", summary: "材料库推荐：请填写材料与厚度" };
  }
  try {
    const query = new URLSearchParams({
      material: name,
      thickness_mm: String(thickness),
      laser_mode: mode,
    });
    if (mode === "engrave" && engraving_mode) {
      query.set("engraving_mode", String(engraving_mode));
    }
    const response = await fetchImpl(`/api/ui/material-recommendation?${query.toString()}`, {
      method: "GET",
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    const json = await response.json();
    const recommendation = extractUiMaterialRecommendation(json);
    if (!recommendation) {
      return {
        ok: false,
        recommendation: null,
        reason: "invalid_payload",
        summary: "材料库推荐：暂无匹配参数",
      };
    }
    return {
      ok: true,
      recommendation,
      reason: "ok",
      summary: formatMaterialRecommendationSummary(recommendation),
    };
  } catch (error) {
    return {
      ok: false,
      recommendation: null,
      reason: "load_error",
      summary: "材料库推荐：暂无匹配参数",
      error: error && error.message ? error.message : String(error || "load_error"),
    };
  }
}

export function mapDrawModeToRecommendationQuery(mode) {
  const normalized = String(mode || "").trim().toLowerCase();
  if (normalized === "outline") {
    return { laser_mode: "engrave", engraving_mode: "outline" };
  }
  // raster / auto / unknown -> engrave raster for summary only
  return { laser_mode: "engrave", engraving_mode: "raster" };
}
