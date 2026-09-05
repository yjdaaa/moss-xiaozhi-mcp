/**
 * Browser-only repeat-send acknowledgement for identical draw content fingerprints.
 * Does not replace backend confirmed=true / material freeze / gcode SHA gates.
 */

export const LAST_ACCEPTED_FINGERPRINT_KEY = "laserLab.repeatSend.v1.lastAcceptedFingerprint";

const HEX64 = /^[0-9a-f]{64}$/;

export function isFullContentFingerprint(value) {
  return typeof value === "string" && HEX64.test(value);
}

export function normalizeContentFingerprint(value) {
  if (value == null || value === "") return "";
  const text = String(value).trim().toLowerCase();
  return isFullContentFingerprint(text) ? text : "";
}

/**
 * After a successful preview response: set current fingerprint and clear this-session ack.
 * Does not clear last accepted fingerprint.
 */
export function applyPreviewFingerprintSuccess(state, responseFingerprint) {
  const current = normalizeContentFingerprint(responseFingerprint);
  return {
    ...state,
    currentFingerprint: current,
    movedMaterialAcknowledged: false,
  };
}

export function resetMovedMaterialAcknowledgement(state) {
  return { ...state, movedMaterialAcknowledged: false };
}

export function setMovedMaterialAcknowledged(state, acknowledged) {
  return { ...state, movedMaterialAcknowledged: Boolean(acknowledged) };
}

/**
 * Whether confirm UI must require the "moved material/origin" checkbox.
 */
export function requiresMovedMaterialAcknowledgement(state) {
  const current = normalizeContentFingerprint(state?.currentFingerprint);
  const last = normalizeContentFingerprint(state?.lastAcceptedFingerprint);
  if (!current || !last) return false;
  return current === last;
}

/**
 * Whether the user may submit confirm_send for the current fingerprint.
 */
export function canSubmitConfirmSend(state) {
  if (!requiresMovedMaterialAcknowledgement(state)) return true;
  return Boolean(state?.movedMaterialAcknowledged);
}

/**
 * Nested send acceptance: outer success alone is not enough.
 * Require send_result.success === true and workflow status sending|completed.
 */
export function isNestedSenderAccepted(confirmResponse) {
  if (!confirmResponse || confirmResponse.success !== true) return false;
  const result = confirmResponse.result || {};
  const sendResult = result.send_result || result.sendResult || {};
  if (sendResult.success !== true) return false;
  const workflow = result.workflow || result;
  const status = String(workflow.status || result.status || "").trim().toLowerCase();
  return status === "sending" || status === "completed";
}

/**
 * On confirm response: only persist current fingerprint when nested sender accepted.
 */
export function applyConfirmSendResponse(state, confirmResponse, storage = null) {
  if (!isNestedSenderAccepted(confirmResponse)) {
    return { ...state };
  }
  const current = normalizeContentFingerprint(state?.currentFingerprint);
  if (!current) {
    return { ...state };
  }
  if (storage && typeof storage.setItem === "function") {
    try {
      storage.setItem(LAST_ACCEPTED_FINGERPRINT_KEY, current);
    } catch (_err) {
      // ignore quota / private mode
    }
  }
  return {
    ...state,
    lastAcceptedFingerprint: current,
    movedMaterialAcknowledged: false,
  };
}

export function loadLastAcceptedFingerprint(storage) {
  if (!storage || typeof storage.getItem !== "function") return "";
  try {
    return normalizeContentFingerprint(storage.getItem(LAST_ACCEPTED_FINGERPRINT_KEY));
  } catch (_err) {
    return "";
  }
}

export function createRepeatSendState(storage = null) {
  return {
    currentFingerprint: "",
    lastAcceptedFingerprint: loadLastAcceptedFingerprint(storage),
    movedMaterialAcknowledged: false,
  };
}
