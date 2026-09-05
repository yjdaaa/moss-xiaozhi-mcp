function parseHexColor(value) {
  if (typeof value !== "string" || !value.startsWith("#")) return null;

  let hex = value.slice(1);
  if (hex.length === 3 || hex.length === 4) {
    hex = [...hex].map((character) => character + character).join("");
  }
  if (hex.length !== 6 && hex.length !== 8) return null;

  const number = Number.parseInt(hex, 16);
  if (!Number.isFinite(number)) return null;
  const hasAlpha = hex.length === 8;
  return {
    red: hasAlpha ? (number >>> 24) & 0xff : (number >>> 16) & 0xff,
    green: hasAlpha ? (number >>> 16) & 0xff : (number >>> 8) & 0xff,
    blue: hasAlpha ? (number >>> 8) & 0xff : number & 0xff,
    alpha: hasAlpha ? number & 0xff : 0xff,
  };
}

function isChromaticColor(value) {
  const color = parseHexColor(value);
  return Boolean(color && color.alpha > 0 && (color.red !== color.green || color.green !== color.blue));
}

function isVisibleElement(element) {
  return !element.isDeleted && Number(element.opacity ?? 100) > 0;
}

function isColoredElement(element) {
  return isVisibleElement(element) && (
    isChromaticColor(element.strokeColor) || isChromaticColor(element.backgroundColor)
  );
}

function normalizeInkColor(value) {
  const color = parseHexColor(value);
  if (!color || color.alpha === 0) return value;
  if (color.red === 255 && color.green === 255 && color.blue === 255) return value;
  if (color.alpha === 255) return "#000000";
  return `#000000${color.alpha.toString(16).padStart(2, "0")}`;
}

export function prepareRasterExportElements(elements, mode) {
  if (mode !== "raster") return elements;

  const visibleElements = elements.filter(isVisibleElement);
  if (visibleElements.some((element) => element.type === "image")) return elements;
  if (!visibleElements.some(isColoredElement)) return elements;

  return elements.map((element) => ({
    ...element,
    strokeColor: normalizeInkColor(element.strokeColor),
    backgroundColor: normalizeInkColor(element.backgroundColor),
  }));
}
