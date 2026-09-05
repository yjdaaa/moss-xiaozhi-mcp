/**
 * Laser-only Excalidraw export dimensions.
 * scale = min(4, 2048 / max(width, height)); final sides never exceed 2048px.
 * Ordinary non-laser exports must not inject this helper.
 */
export const LASER_EXPORT_MAX_EDGE = 2048;
export const LASER_EXPORT_MAX_SCALE = 4;

export function getLaserExportDimensions(width, height) {
  const w = Number(width);
  const h = Number(height);
  if (!Number.isFinite(w) || !Number.isFinite(h) || w <= 0 || h <= 0) {
    throw new Error("export dimensions must be positive finite numbers");
  }
  const longest = Math.max(w, h);
  const scale = Math.min(LASER_EXPORT_MAX_SCALE, LASER_EXPORT_MAX_EDGE / longest);
  const outW = w * scale;
  const outH = h * scale;
  if (outW > LASER_EXPORT_MAX_EDGE + 1e-9 || outH > LASER_EXPORT_MAX_EDGE + 1e-9) {
    throw new Error("export dimensions exceed hard edge limit");
  }
  return {
    width: outW,
    height: outH,
    scale,
  };
}
