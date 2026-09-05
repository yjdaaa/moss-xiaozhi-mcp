import test from "node:test";
import assert from "node:assert/strict";
import {
  getLaserExportDimensions,
  LASER_EXPORT_MAX_EDGE,
  LASER_EXPORT_MAX_SCALE,
} from "../src/laserExportDimensions.js";

test("small canvas scales up to 4x", () => {
  const result = getLaserExportDimensions(100, 50);
  assert.equal(result.scale, 4);
  assert.equal(result.width, 400);
  assert.equal(result.height, 200);
});

test("4x cap is respected when longest edge * 4 is still under 2048", () => {
  const result = getLaserExportDimensions(400, 300);
  assert.equal(result.scale, LASER_EXPORT_MAX_SCALE);
  assert.equal(result.width, 1600);
  assert.equal(result.height, 1200);
});

test("hard 2048 edge limit reduces scale below 4", () => {
  const result = getLaserExportDimensions(800, 600);
  assert.ok(result.scale < 4);
  assert.equal(result.scale, LASER_EXPORT_MAX_EDGE / 800);
  assert.equal(result.width, 2048);
  assert.equal(result.height, 1536);
  assert.ok(result.width <= 2048);
  assert.ok(result.height <= 2048);
});

test("oversized original is scaled down (scale < 1)", () => {
  const result = getLaserExportDimensions(4096, 2048);
  assert.ok(result.scale < 1);
  assert.equal(result.scale, LASER_EXPORT_MAX_EDGE / 4096);
  assert.equal(result.width, 2048);
  assert.equal(result.height, 1024);
});

test("rejects non-positive or non-finite dimensions", () => {
  for (const args of [
    [0, 10],
    [-1, 10],
    [10, 0],
    [Number.NaN, 10],
    [10, Number.POSITIVE_INFINITY],
  ]) {
    assert.throws(() => getLaserExportDimensions(...args), /positive finite/);
  }
});
