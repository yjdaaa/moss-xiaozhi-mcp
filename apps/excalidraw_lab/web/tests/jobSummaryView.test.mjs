import test from "node:test";
import assert from "node:assert/strict";
import {
  isNearestThicknessMatch,
  nearestThicknessWarning,
  sendBadgeLabel,
  summaryAllowsConfirmSend,
  summaryDisplayWarnings,
  thicknessDisplayRows,
} from "../src/jobSummaryView.js";

test("exact match shows single thickness row and confirmable badge", () => {
  const summary = {
    can_send: true,
    match_type: "exact",
    thickness_mm: 3,
    matched_thickness_mm: 3,
  };
  assert.equal(isNearestThicknessMatch(summary), false);
  assert.equal(sendBadgeLabel(summary), "可确认发送");
  assert.deepEqual(thicknessDisplayRows(summary), [{ label: "厚度", valueKey: "thickness_mm" }]);
  assert.equal(nearestThicknessWarning(summary), null);
});

test("nearest match splits request/param thickness and warns", () => {
  const summary = {
    can_send: true,
    match_type: "nearest_thickness",
    thickness_mm: 3,
    matched_thickness_mm: 1.5,
  };
  assert.equal(isNearestThicknessMatch(summary), true);
  assert.equal(sendBadgeLabel(summary), "可确认发送");
  assert.deepEqual(thicknessDisplayRows(summary), [
    { label: "请求厚度", valueKey: "thickness_mm" },
    { label: "参数厚度", valueKey: "matched_thickness_mm" },
  ]);
  const warning = nearestThicknessWarning(summary, (value, unit) => `${value} ${unit}`);
  assert.match(warning, /请求厚度 3 mm/);
  assert.match(warning, /参数厚度 1\.5 mm/);
});

test("blocked or incomplete summary is explicitly not sendable", () => {
  assert.equal(sendBadgeLabel({ can_send: false, match_type: "none" }), "不可发送");
  assert.equal(sendBadgeLabel({}), "不可发送");
  assert.equal(sendBadgeLabel(null), "等待预览");
});

test("confirm send requires explicit can_send and confirm_send next action", () => {
  assert.equal(summaryAllowsConfirmSend({ can_send: true }, ["confirm_send", "feedback"]), true);
  assert.equal(summaryAllowsConfirmSend({ can_send: false }, ["confirm_send"]), false);
  assert.equal(summaryAllowsConfirmSend({}, ["confirm_send"]), false);
  assert.equal(summaryAllowsConfirmSend({ can_send: true }, ["feedback"]), false);
  assert.equal(summaryAllowsConfirmSend({ can_send: true }, null), false);
});

test("summaryDisplayWarnings dedupes nearest-thickness backend synonyms", () => {
  const format = (value, unit) => `${value} ${unit}`;
  const summary = {
    match_type: "nearest_thickness",
    thickness_mm: 3,
    matched_thickness_mm: 1.5,
    warnings: [
      "未找到请求厚度 3 mm 的精确参数，已使用同材料最近厚度 1.5 mm 参数；正式加工前请确认并建议小样测试。",
      "通风与看火仍需人工确认",
    ],
  };
  const display = summaryDisplayWarnings(summary, format);
  assert.equal(display.length, 2);
  assert.match(display[0], /参数厚度 1\.5 mm/);
  assert.equal(display[1], "通风与看火仍需人工确认");
  // Only one nearest-thickness style note.
  assert.equal(display.filter((item) => item.includes("厚度")).length, 1);
});
