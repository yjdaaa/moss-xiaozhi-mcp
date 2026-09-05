import assert from "node:assert/strict";
import test from "node:test";

import { prepareRasterExportElements } from "../src/rasterInkElements.js";

const blackLine = {
  type: "freedraw",
  strokeColor: "#1e1e1e",
  backgroundColor: "transparent",
  opacity: 100,
  isDeleted: false,
};

test("keeps the existing black and white raster path unchanged", () => {
  const elements = [blackLine];

  assert.equal(prepareRasterExportElements(elements, "raster"), elements);
});

test("does not change outline exports", () => {
  const elements = [{ ...blackLine, strokeColor: "#ff0000" }];

  assert.equal(prepareRasterExportElements(elements, "outline"), elements);
});

test("keeps the existing raster path when an imported image is present", () => {
  const elements = [
    { ...blackLine, strokeColor: "#ff0000" },
    { type: "image", opacity: 100, isDeleted: false },
  ];

  assert.equal(prepareRasterExportElements(elements, "raster"), elements);
});

test("normalizes colored native elements while preserving white and transparent paint", () => {
  const elements = [
    { ...blackLine, strokeColor: "#ff0000", backgroundColor: "#ffff00" },
    { ...blackLine, strokeColor: "#1e1e1e", backgroundColor: "#ffffff" },
    { ...blackLine, strokeColor: "#00ff0080" },
  ];

  const prepared = prepareRasterExportElements(elements, "raster");

  assert.notEqual(prepared, elements);
  assert.equal(prepared[0].strokeColor, "#000000");
  assert.equal(prepared[0].backgroundColor, "#000000");
  assert.equal(prepared[1].strokeColor, "#000000");
  assert.equal(prepared[1].backgroundColor, "#ffffff");
  assert.equal(prepared[2].strokeColor, "#00000080");
  assert.equal(elements[0].strokeColor, "#ff0000");
});
