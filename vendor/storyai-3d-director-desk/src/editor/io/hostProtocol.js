export const DIRECTOR_HOST_PROTOCOL = 2;

export const DIRECTOR_HOST_MESSAGES = Object.freeze({
  connect: "storyai:director-desk-connect",
  ready: "storyai:director-desk-ready",
  close: "storyai:director-desk-close",
  captures: "storyai:director-desk-captures",
  captureResult: "storyai:director-desk-capture-result",
  sceneSave: "storyai:director-desk-scene-save",
  sceneSaved: "storyai:director-desk-scene-saved",
  session: "storyai:director-desk-session",
  sessionAck: "storyai:director-desk-session-ack",
  panorama: "storyai:director-desk-panorama",
  panoramaRemoved: "storyai:director-desk-panorama-removed",
});

const HOST_TYPES = new Set([
  DIRECTOR_HOST_MESSAGES.session,
  DIRECTOR_HOST_MESSAGES.captureResult,
  DIRECTOR_HOST_MESSAGES.sceneSaved,
  DIRECTOR_HOST_MESSAGES.panorama,
]);

function requiredText(value) {
  return typeof value === "string" && value.trim() && value.length <= 4096 ? value.trim() : "";
}

export function bindingFromEnvelope(data) {
  if (!data || typeof data !== "object" || Array.isArray(data)) return null;
  const instanceId = requiredText(data.instanceId);
  const sessionNonce = requiredText(data.sessionNonce);
  const epoch = data.epoch;
  if (!instanceId || !sessionNonce || !Number.isSafeInteger(epoch) || epoch < 1) return null;
  return Object.freeze({ instanceId, sessionNonce, epoch });
}

export function sameDirectorBinding(left, right) {
  return Boolean(left && right
    && left.instanceId === right.instanceId
    && left.sessionNonce === right.sessionNonce
    && left.epoch === right.epoch);
}

export function parseDirectorConnectEnvelope(data, expectedInstanceId) {
  if (data?.type !== DIRECTOR_HOST_MESSAGES.connect || data?.protocolVersion !== DIRECTOR_HOST_PROTOCOL) return null;
  const binding = bindingFromEnvelope(data);
  return binding && binding.instanceId === expectedInstanceId ? binding : null;
}

export function parseDirectorHostEnvelope(data, { binding, lastSequence = 0, allowedTypes = HOST_TYPES } = {}) {
  if (data?.protocolVersion !== DIRECTOR_HOST_PROTOCOL || !allowedTypes.has(data?.type)) return null;
  if (!sameDirectorBinding(bindingFromEnvelope(data), binding)) return null;
  if (!Number.isSafeInteger(data.sequence) || data.sequence <= lastSequence) return null;
  return { type: data.type, payload: data.payload ?? null, sequence: data.sequence };
}

export function createDirectorChildEnvelope(binding, type, sequence, payload = null) {
  if (!Object.values(DIRECTOR_HOST_MESSAGES).includes(type)) throw new TypeError("Unsupported director host message.");
  if (!sameDirectorBinding(binding, bindingFromEnvelope(binding))) throw new TypeError("Invalid director host binding.");
  if (!Number.isSafeInteger(sequence) || sequence < 1) throw new TypeError("Invalid director host sequence.");
  return {
    type,
    protocolVersion: DIRECTOR_HOST_PROTOCOL,
    instanceId: binding.instanceId,
    sessionNonce: binding.sessionNonce,
    epoch: binding.epoch,
    sequence,
    payload,
  };
}
