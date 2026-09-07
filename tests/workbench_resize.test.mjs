import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import test from "node:test";

const html = readFileSync(new URL("../apps/laser_web/ui/index.html", import.meta.url), "utf8");
const source = html.slice(
  html.indexOf('  const WORKBENCH_SIZE_KEY ='),
  html.indexOf('  $("#workbench-reset-size").addEventListener'),
);

function harness(width = 1124, desktop = true) {
  const listeners = new Map();
  const classes = new Set();
  const storage = new Map();
  const style = { removeProperty(key) { delete this[key]; } };
  const consoleEl = {
    style, offsetWidth: width, offsetHeight: 900,
    classList: {
      add: (key) => classes.add(key),
      remove: (key) => classes.delete(key),
      contains: (key) => classes.has(key),
    },
  };
  const context = vm.createContext({
    consoleEl, wrap: { clientWidth: width },
    clamp: (v, min, max) => Math.max(min, Math.min(max, v)),
    window: {
      innerHeight: 800,
      matchMedia: () => ({ matches: desktop }),
      addEventListener: (key, fn) => listeners.set(key, fn),
      removeEventListener: (key) => listeners.delete(key),
    },
    localStorage: {
      setItem: (key, value) => storage.set(key, value),
      getItem: (key) => storage.get(key) ?? null,
      removeItem: (key) => storage.delete(key),
    },
  });
  vm.runInContext(source, context);
  function drag(side, axis, dx, dy) {
    const handle = {
      dataset: { resizeSide: side, resizeAxis: axis },
      setPointerCapture() {}, hasPointerCapture: () => true,
      releasePointerCapture() {}, addEventListener() {}, removeEventListener() {},
    };
    context.startWorkbenchResize({
      button: 0, pointerId: 1, clientX: 0, clientY: 0, currentTarget: handle,
      preventDefault() {}, stopPropagation() {},
    });
    listeners.get("pointermove")({ pointerId: 1, clientX: dx, clientY: dy });
    listeners.get("pointerup")({ pointerId: 1 });
  }
  return { context, consoleEl, style, drag, storage, listeners };
}

test("left edge preserves opposite boundary and persists offset", () => {
  const h = harness();
  h.drag("left", "x", 80, 0);
  assert.equal(h.style.left, "80px");
  assert.equal(h.style.width, "1044px");
  const saved = h.context.readWorkbenchSize();
  assert.equal(saved.left + saved.width, 1124);
  assert.equal(h.listeners.size, 0);
});

test("right and bottom edges change only the requested dimension", () => {
  const h = harness();
  h.drag("right", "x", -100, 0);
  assert.equal(h.style.width, "1024px");
  assert.equal(h.style.height, "900px");
  const b = harness();
  b.drag("right", "y", 0, -150);
  assert.equal(b.style.height, "750px");
  assert.equal(b.style.width, "1124px");
});

test("both bottom corners resize width and height", () => {
  for (const side of ["left", "right"]) {
    const h = harness();
    h.drag(side, "xy", side === "left" ? 60 : -60, 120);
    assert.equal(h.style.width, "1064px");
    assert.equal(h.style.height, "1020px");
  }
});

test("narrow viewport clamps width and offset to the actual wrapper", () => {
  const h = harness(449, false);
  const size = h.context.clampWorkbenchSize(1000, 900, 800);
  assert.equal(size.left + size.width, 449);
  h.drag("left", "x", 50, 0);
  assert.equal(h.style.width, "399px");
  assert.equal(h.style.left, "50px");
});

test("reset removes persisted geometry and inline sizing", () => {
  const h = harness();
  h.drag("left", "xy", 40, 100);
  h.context.resetWorkbenchSize();
  assert.equal(h.storage.size, 0);
  for (const key of ["left", "width", "height"]) assert.equal(h.style[key], undefined);
});
