import test from "node:test";
import assert from "node:assert/strict";
import {
  CUSTOM_OPTION_VALUE,
  applyMaterialLibraryRefresh,
  applyMaterialSelectChange,
  extractUiMaterialOptions,
  formatMaterialRecommendationSummary,
  loadUiMaterialOptions,
  loadUiMaterialRecommendation,
  mapDrawModeToRecommendationQuery,
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

test("refresh keeps library material and equivalent thickness", () => {
  const library = [
    { name: "亚克力", thicknesses: [2, 3] },
    { name: "椴木", thicknesses: [3, 5] },
  ];
  const kept = applyMaterialLibraryRefresh({
    loaded: { ok: true, materials: library, reason: "ok" },
    currentMode: "library",
    currentMaterial: "椴木",
    currentThickness: "3.0",
    hasLoadedOnce: true,
  });
  assert.equal(kept.applied, true);
  assert.equal(kept.mode, "library");
  assert.equal(kept.material, "椴木");
  assert.equal(kept.thickness_mm, "3");
  assert.equal(kept.selectValue, "椴木");
});

test("refresh falls back when library material or thickness removed", () => {
  const library = [
    { name: "亚克力", thicknesses: [2] },
    { name: "椴木", thicknesses: [5] },
  ];
  const materialRemoved = applyMaterialLibraryRefresh({
    loaded: { ok: true, materials: library, reason: "ok" },
    currentMode: "library",
    currentMaterial: "纸板",
    currentThickness: "3",
    hasLoadedOnce: true,
  });
  assert.equal(materialRemoved.mode, "library");
  assert.equal(materialRemoved.material, "亚克力");
  assert.equal(materialRemoved.thickness_mm, "2");
  assert.equal(materialRemoved.reason, "material_removed");

  const thicknessRemoved = applyMaterialLibraryRefresh({
    loaded: { ok: true, materials: library, reason: "ok" },
    currentMode: "library",
    currentMaterial: "椴木",
    currentThickness: "3",
    hasLoadedOnce: true,
  });
  assert.equal(thicknessRemoved.material, "椴木");
  assert.equal(thicknessRemoved.thickness_mm, "5");
});

test("refresh never hijacks custom values; empty becomes custom", () => {
  const library = [{ name: "椴木", thicknesses: [3] }];
  const custom = applyMaterialLibraryRefresh({
    loaded: { ok: true, materials: library, reason: "ok" },
    currentMode: "custom",
    currentMaterial: "手填纸板",
    currentThickness: "1.2",
    hasLoadedOnce: true,
  });
  assert.equal(custom.mode, "custom");
  assert.equal(custom.material, "手填纸板");
  assert.equal(custom.thickness_mm, "1.2");
  assert.equal(custom.selectValue, CUSTOM_OPTION_VALUE);
  assert.equal(custom.materials.length, 1);

  const empty = applyMaterialLibraryRefresh({
    loaded: { ok: true, materials: [], reason: "empty" },
    currentMode: "library",
    currentMaterial: "椴木",
    currentThickness: "3",
    hasLoadedOnce: true,
  });
  assert.equal(empty.mode, "custom");
  assert.equal(empty.material, "椴木");
  assert.equal(empty.thickness_mm, "3");
});

test("transient refresh failure keeps previous successful library", () => {
  const previous = [{ name: "椴木", thicknesses: [3, 5] }];
  const transient = applyMaterialLibraryRefresh({
    loaded: { ok: false, materials: [], reason: "load_error" },
    currentMode: "library",
    currentMaterial: "椴木",
    currentThickness: "5",
    previousMaterials: previous,
    hasLoadedOnce: true,
  });
  assert.equal(transient.applied, false);
  assert.equal(transient.keepPrevious, true);
  assert.equal(transient.mode, "library");
  assert.equal(transient.material, "椴木");
  assert.equal(transient.thickness_mm, "5");
  assert.deepEqual(transient.materials, previous);

  const firstFail = applyMaterialLibraryRefresh({
    loaded: { ok: false, materials: [], reason: "invalid_payload" },
    currentMode: "custom",
    currentMaterial: "手填",
    currentThickness: "1",
    previousMaterials: [],
    hasLoadedOnce: false,
  });
  assert.equal(firstFail.applied, true);
  assert.equal(firstFail.mode, "custom");
  assert.deepEqual(firstFail.materials, []);
});

test("successful retry after initial failure restores a known saved material", () => {
  const recovered = applyMaterialLibraryRefresh({
    loaded: {
      ok: true,
      materials: [
        { name: "亚克力", thicknesses: [2] },
        { name: "椴木", thicknesses: [3, 5] },
      ],
      reason: "ok",
    },
    currentMode: "custom",
    currentMaterial: "椴木",
    currentThickness: "3.0",
    previousMaterials: [],
    hasLoadedOnce: false,
  });
  assert.equal(recovered.mode, "library");
  assert.equal(recovered.material, "椴木");
  assert.equal(recovered.thickness_mm, "3");
  assert.equal(recovered.selectValue, "椴木");
  assert.equal(recovered.reason, "initial_recovery");

  const unknown = applyMaterialLibraryRefresh({
    loaded: { ok: true, materials: [{ name: "椴木", thicknesses: [3] }], reason: "ok" },
    currentMode: "custom",
    currentMaterial: "手填材料",
    currentThickness: "1.2",
    previousMaterials: [],
    hasLoadedOnce: false,
  });
  assert.equal(unknown.mode, "custom");
  assert.equal(unknown.material, "手填材料");
  assert.equal(unknown.thickness_mm, "1.2");
});

test("loadUiMaterialRecommendation covers success and 8777-style failures", async () => {
  const successFetch = async (url) => {
    assert.match(String(url), /material-recommendation/);
    assert.match(String(url), /engraving_mode=outline/);
    return {
      ok: true,
      status: 200,
      async json() {
        return {
          success: true,
          result: {
            material: "纸板",
            thickness_mm: 4,
            matched_thickness_mm: 3,
            match: "nearest_thickness",
            laser_mode: "engrave",
            engraving_mode: "outline",
            laser_max_power: 280,
            feed_rate: 450,
            passes: 2,
          },
        };
      },
    };
  };
  const success = await loadUiMaterialRecommendation(
    { material: "纸板", thickness_mm: 4, laser_mode: "engrave", engraving_mode: "outline" },
    successFetch,
  );
  assert.equal(success.ok, true);
  assert.equal(success.recommendation.laser_max_power, 280);
  assert.match(success.summary, /S280/);
  assert.match(success.summary, /最接近的 3mm/);

  const htmlFetch = async () => ({
    ok: true,
    status: 200,
    async json() {
      throw new SyntaxError("Unexpected token < in JSON");
    },
  });
  const html = await loadUiMaterialRecommendation(
    { material: "纸板", thickness_mm: 3, laser_mode: "engrave", engraving_mode: "raster" },
    htmlFetch,
  );
  assert.equal(html.ok, false);
  assert.equal(html.reason, "load_error");
  assert.match(html.summary, /暂无匹配参数/);

  const badEnvelope = async () => ({
    ok: false,
    status: 404,
    async json() {
      return { success: false, result: "未找到路径" };
    },
  });
  const bad = await loadUiMaterialRecommendation(
    { material: "纸板", thickness_mm: 3, laser_mode: "cut" },
    badEnvelope,
  );
  assert.equal(bad.ok, false);
  assert.equal(bad.reason, "invalid_payload");

  const network = async () => {
    throw new Error("network down");
  };
  const net = await loadUiMaterialRecommendation(
    { material: "纸板", thickness_mm: 3, laser_mode: "engrave", engraving_mode: "raster" },
    network,
  );
  assert.equal(net.ok, false);
  assert.equal(net.reason, "load_error");

  assert.deepEqual(mapDrawModeToRecommendationQuery("outline"), {
    laser_mode: "engrave",
    engraving_mode: "outline",
  });
  assert.deepEqual(mapDrawModeToRecommendationQuery("raster"), {
    laser_mode: "engrave",
    engraving_mode: "raster",
  });
  assert.equal(
    formatMaterialRecommendationSummary({
      laser_mode: "cut",
      laser_max_power: 900,
      feed_rate: 200,
      passes: 3,
    }),
    "材料库推荐：S900 / F200 / 3次 / cut",
  );
});
