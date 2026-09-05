/**
 * Pure view helpers for draw-lab job summary UI.
 * Keep React-free so Node can unit-test nearest-thickness labeling.
 */

export function isNearestThicknessMatch(summary) {
  return Boolean(summary && summary.match_type === "nearest_thickness");
}

export function sendBadgeLabel(summary) {
  if (!summary) return "需确认";
  return summary.can_send ? "可确认发送" : "需确认";
}

export function thicknessDisplayRows(summary) {
  if (!summary) return [];
  if (isNearestThicknessMatch(summary)) {
    return [
      { label: "请求厚度", valueKey: "thickness_mm" },
      { label: "参数厚度", valueKey: "matched_thickness_mm" },
    ];
  }
  return [{ label: "厚度", valueKey: "thickness_mm" }];
}

export function nearestThicknessWarning(summary, formatWithUnit) {
  if (!isNearestThicknessMatch(summary)) return null;
  const format =
    typeof formatWithUnit === "function"
      ? formatWithUnit
      : (value, unit) => (value == null || value === "" ? "" : `${value} ${unit}`);
  return (
    `未找到请求厚度 ${format(summary.thickness_mm, "mm")} 的精确参数，已使用参数厚度 ` +
    `${format(summary.matched_thickness_mm, "mm")}；正式加工前请确认并建议小样测试。`
  );
}

/** Backend may emit the same nearest-thickness note; drop those synonyms once UI shows nearestWarning. */
export function isNearestThicknessSynonymWarning(text) {
  const value = String(text || "");
  if (!value) return false;
  return (
    value.includes("最近厚度") ||
    (value.includes("请求") && value.includes("参数") && value.includes("厚度")) ||
    (value.includes("未找到请求厚度") && value.includes("参数厚度"))
  );
}

/**
 * Single list for JobSummaryCard: at most one nearest-thickness note, then other warnings.
 * Prefer frontend nearestThicknessWarning wording when match_type is nearest_thickness.
 */
export function summaryDisplayWarnings(summary, formatWithUnit) {
  const backend = Array.isArray(summary?.warnings) ? summary.warnings.filter(Boolean) : [];
  const nearest = nearestThicknessWarning(summary, formatWithUnit);
  if (nearest) {
    return [nearest, ...backend.filter((item) => !isNearestThicknessSynonymWarning(item))];
  }
  return backend;
}
