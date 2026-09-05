import test from "node:test";
import assert from "node:assert/strict";
import {
  LAST_ACCEPTED_FINGERPRINT_KEY,
  applyConfirmSendResponse,
  applyPreviewFingerprintSuccess,
  canSubmitConfirmSend,
  createRepeatSendState,
  isFullContentFingerprint,
  isNestedSenderAccepted,
  requiresMovedMaterialAcknowledgement,
  setMovedMaterialAcknowledged,
} from "../src/repeatSendGuard.js";

const FP_A = "a".repeat(64);
const FP_B = "b".repeat(64);

function memoryStorage(seed = {}) {
  const map = new Map(Object.entries(seed));
  return {
    getItem(key) {
      return map.has(key) ? map.get(key) : null;
    },
    setItem(key, value) {
      map.set(key, String(value));
    },
    _map: map,
  };
}

test("full fingerprint is exactly 64 lowercase hex; rejects truncated", () => {
  assert.equal(isFullContentFingerprint(FP_A), true);
  assert.equal(isFullContentFingerprint("a".repeat(12)), false);
  assert.equal(isFullContentFingerprint("a".repeat(10)), false);
  assert.equal(isFullContentFingerprint("a".repeat(16)), false);
  assert.equal(isFullContentFingerprint("A".repeat(64)), false);
});

test("new preview sets current fingerprint and clears acknowledgement only", () => {
  let state = createRepeatSendState(memoryStorage({ [LAST_ACCEPTED_FINGERPRINT_KEY]: FP_A }));
  assert.equal(state.lastAcceptedFingerprint, FP_A);
  state = applyPreviewFingerprintSuccess(state, FP_B);
  assert.equal(state.currentFingerprint, FP_B);
  assert.equal(state.lastAcceptedFingerprint, FP_A);
  assert.equal(state.movedMaterialAcknowledged, false);
  assert.equal(requiresMovedMaterialAcknowledgement(state), false);
  assert.equal(canSubmitConfirmSend(state), true);
});

test("same fingerprint requires moved-material acknowledgement before submit", () => {
  let state = createRepeatSendState(memoryStorage({ [LAST_ACCEPTED_FINGERPRINT_KEY]: FP_A }));
  state = applyPreviewFingerprintSuccess(state, FP_A);
  assert.equal(requiresMovedMaterialAcknowledgement(state), true);
  assert.equal(canSubmitConfirmSend(state), false);
  state = setMovedMaterialAcknowledged(state, true);
  assert.equal(canSubmitConfirmSend(state), true);
});

test("nested sender acceptance requires send_result.success and sending/completed", () => {
  assert.equal(
    isNestedSenderAccepted({
      success: true,
      result: { status: "failed", send_result: { success: false } },
    }),
    false,
  );
  assert.equal(
    isNestedSenderAccepted({
      success: true,
      result: { status: "failed", send_result: { success: true } },
    }),
    false,
  );
  assert.equal(
    isNestedSenderAccepted({
      success: true,
      result: { status: "sending", send_result: { success: true } },
    }),
    true,
  );
  assert.equal(
    isNestedSenderAccepted({
      success: true,
      result: { workflow: { status: "completed" }, send_result: { success: true } },
    }),
    true,
  );
  assert.equal(isNestedSenderAccepted({ success: true, result: { status: "sending" } }), false);
});

test("only nested acceptance persists last fingerprint; failures keep previous", () => {
  const storage = memoryStorage({ [LAST_ACCEPTED_FINGERPRINT_KEY]: FP_A });
  let state = createRepeatSendState(storage);
  state = applyPreviewFingerprintSuccess(state, FP_B);

  state = applyConfirmSendResponse(
    state,
    { success: true, result: { status: "failed", send_result: { success: false } } },
    storage,
  );
  assert.equal(state.lastAcceptedFingerprint, FP_A);
  assert.equal(storage.getItem(LAST_ACCEPTED_FINGERPRINT_KEY), FP_A);

  state = applyConfirmSendResponse(
    state,
    { success: true, result: { status: "sending", send_result: { success: true } } },
    storage,
  );
  assert.equal(state.lastAcceptedFingerprint, FP_B);
  assert.equal(storage.getItem(LAST_ACCEPTED_FINGERPRINT_KEY), FP_B);
});
