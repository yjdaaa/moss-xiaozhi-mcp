import test from "node:test";
import assert from "node:assert/strict";
import {
  CUSTOM_OPTION_VALUE,
  applyMaterialSelectChange,
  extractUiMaterialOptions,
  loadUiMaterialOptions,
  normalizeMaterialOptions,
  pickThicknessForMaterial,
  restoreMaterialSelectState,
  returnToLibrary,
  thicknessesEqual,
} from "../src/materialSelectState.js";

test("extractUiMaterialOptions accepts success envelope only", () => {
  assert.deepEqual(
    extractUiMaterialOptions({
      success: true,
      result: { materials: [{ name: "椴木", thicknesses: [3, 5] }] },
    }),
    [{ name: "椴木", thicknesses: [3, 5] }],
  );
  assert.equal(extractUiMaterialOptions({ success: false, result: "x" }), null);
  assert.equal(extractUiMaterialOptions({ success: true, result: { materials: {} } }), null);
  assert.equal(extractUiMaterialOptions({ success: true, result: {} }), null);
  assert.equal(extractUiMaterialOptions(null), null);
});

test("normalize filters invalid thickness and sorts stably", () => {
  const materials = normalizeMaterialOptions([
    { name: "  亚克力 ", thicknesses: [5, "3", 3.0, 0, -1, "x", 2.5] },
    { name: "椴木", thicknesses: [3] },
    { name: "", thicknesses: [1] },
    { name: "坏材料", thicknesses: [0, "nope"] },
  ]);
  assert.deepEqual(
    materials.map((item) => item.name),
    ["亚克力", "椴木"],
  );
  assert.deepEqual(materials[0].thicknesses, [2.5, 3, 5]);
  assert.equal(thicknessesEqual(3, "3.0"), true);
});

test("restore unknown history becomes custom; known keeps equivalent thickness", () => {
  const library = [
    { name: "椴木", thicknesses: [3, 5] },
    { name: "亚克力", thicknesses: [2, 3] },
  ];
  const unknown = restoreMaterialSelectState({
    materials: library,
    savedMaterial: "外星材料",
    savedThickness: "3",
  });
  assert.equal(unknown.mode, "custom");
  assert.equal(unknown.selectValue, CUSTOM_OPTION_VALUE);
  assert.equal(unknown.material, "外星材料");

  const known = restoreMaterialSelectState({
    materials: library,
    savedMaterial: "亚克力",
    savedThickness: "3.0",
  });
  assert.equal(known.mode, "library");
  assert.equal(known.material, "亚克力");
  assert.equal(known.thickness_mm, "3");
});

test("switch material keeps equivalent thickness else first", () => {
  const library = [
    { name: "椴木", thicknesses: [3, 5] },
    { name: "亚克力", thicknesses: [2, 5] },
  ];
  const keep = applyMaterialSelectChange({
    materials: library,
    currentThickness: "5",
    selectValue: "亚克力",
  });
  assert.equal(keep.mode, "library");
  assert.equal(keep.thickness_mm, "5");

  const first = applyMaterialSelectChange({
    materials: library,
    currentThickness: "3",
    selectValue: "亚克力",
  });
  assert.equal(first.thickness_mm, "2");
  assert.equal(pickThicknessForMaterial(library[0], "9"), "3");
});

test("returnToLibrary exact name or first; empty library stays custom", () => {
  const library = [
    { name: "椴木", thicknesses: [3] },
    { name: "亚克力", thicknesses: [2] },
  ];
  const exact = returnToLibrary({
    materials: library,
    preferredMaterial: "亚克力",
    preferredThickness: "2",
  });
  assert.equal(exact.material, "亚克力");
  assert.equal(exact.mode, "library");

  const fallback = returnToLibrary({
    materials: library,
    preferredMaterial: "未知",
    preferredThickness: "9",
  });
  // Stable Unicode name sort: "亚克力" < "椴木"
  assert.equal(fallback.material, "亚克力");
  assert.equal(fallback.thickness_mm, "2");

  const empty = returnToLibrary({
    materials: [],
    preferredMaterial: "手填",
    preferredThickness: "1",
  });
  assert.equal(empty.mode, "custom");
  assert.equal(empty.material, "手填");
});

test("loadUiMaterialOptions try/catch covers HTML SPA fallback and 404-like parse errors", async () => {
  const htmlFetch = async () => ({
    ok: true,
    status: 200,
    async json() {
      throw new SyntaxError("Unexpected token < in JSON");
    },
  });
  const htmlResult = await loadUiMaterialOptions(htmlFetch);
  assert.equal(htmlResult.ok, false);
  assert.equal(htmlResult.reason, "load_error");
  assert.deepEqual(htmlResult.materials, []);

  const badEnvelope = async () => ({
    ok: false,
    status: 404,
    async json() {
      return { success: false, result: "未找到路径" };
    },
  });
  const bad = await loadUiMaterialOptions(badEnvelope);
  assert.equal(bad.ok, false);
  assert.equal(bad.reason, "invalid_payload");

  const emptyOk = async () => ({
    ok: true,
    status: 200,
    async json() {
      return { success: true, result: { materials: [] } };
    },
  });
  const empty = await loadUiMaterialOptions(emptyOk);
  assert.equal(empty.ok, true);
  assert.equal(empty.reason, "empty");
  assert.deepEqual(empty.materials, []);

  const network = async () => {
    throw new Error("network down");
  };
  const net = await loadUiMaterialOptions(network);
  assert.equal(net.ok, false);
  assert.equal(net.reason, "load_error");
});

test("loadUiMaterialOptions success path normalizes materials", async () => {
  const fetchImpl = async () => ({
    ok: true,
    status: 200,
    async json() {
      return {
        success: true,
        result: {
          materials: [
            { name: "椴木", thicknesses: [5, 3, 3.0] },
            { name: "坏", thicknesses: [0] },
          ],
        },
      };
    },
  });
  const result = await loadUiMaterialOptions(fetchImpl);
  assert.equal(result.ok, true);
  assert.equal(result.reason, "ok");
  assert.deepEqual(result.materials, [{ name: "椴木", thicknesses: [3, 5] }]);
});
