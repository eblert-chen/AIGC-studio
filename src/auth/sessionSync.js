export const AUTH_SESSION_CHANNEL = "ai-video.auth-session";
export const AUTH_SESSION_STORAGE_KEY = "ai-video.auth-session-event";

const SAFE_EVENT_TYPES = new Set([
  "account_version_changed",
  "deactivated",
  "invalidated",
  "logout",
  "revoke_all",
  "session_revoked",
]);

function eventSource(runtime) {
  return runtime.crypto?.randomUUID?.()
    || `tab-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function validEvent(value) {
  return value
    && typeof value === "object"
    && value.version === 1
    && SAFE_EVENT_TYPES.has(value.type)
    && typeof value.source === "string"
    && Number.isFinite(value.at);
}

export function createAuthSessionSync({ runtime = globalThis, onEvent } = {}) {
  if (typeof onEvent !== "function") throw new TypeError("onEvent is required");
  const source = eventSource(runtime);
  let closed = false;
  let channel = null;

  const receive = (value) => {
    if (closed || !validEvent(value) || value.source === source) return;
    onEvent(value);
  };
  const onChannelMessage = (event) => receive(event?.data);
  const onStorage = (event) => {
    if (event?.key !== AUTH_SESSION_STORAGE_KEY || !event.newValue) return;
    try { receive(JSON.parse(event.newValue)); } catch { /* ignore malformed local events */ }
  };

  try {
    if (typeof runtime.BroadcastChannel === "function") {
      channel = new runtime.BroadcastChannel(AUTH_SESSION_CHANNEL);
      channel.addEventListener?.("message", onChannelMessage);
    }
  } catch {
    channel = null;
  }
  runtime.addEventListener?.("storage", onStorage);

  return {
    publish(type, details = {}) {
      if (closed || !SAFE_EVENT_TYPES.has(type)) return null;
      const event = {
        version: 1,
        type,
        source,
        at: Date.now(),
        preserve_invitation: details.preserveInvitation === true,
        full_clear: details.fullClear === true,
        auth_version: Number.isInteger(details.authVersion) ? details.authVersion : null,
      };
      try { channel?.postMessage?.(event); } catch { /* storage fallback remains */ }
      try {
        runtime.localStorage?.setItem(AUTH_SESSION_STORAGE_KEY, JSON.stringify(event));
        runtime.localStorage?.removeItem(AUTH_SESSION_STORAGE_KEY);
      } catch {
        // BroadcastChannel may still synchronize, and focus refresh is a final fallback.
      }
      return event;
    },
    close() {
      if (closed) return;
      closed = true;
      runtime.removeEventListener?.("storage", onStorage);
      try { channel?.removeEventListener?.("message", onChannelMessage); } catch { /* no-op */ }
      try { channel?.close?.(); } catch { /* no-op */ }
    },
  };
}
