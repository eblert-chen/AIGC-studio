export const DEFAULT_STUDIO_PREFERENCES = Object.freeze({
  taskCompletionNotices: true,
});

export function studioPreferenceStorageKey(subject) {
  const normalizedSubject = String(subject || "anonymous").trim() || "anonymous";
  return `ai-video.studio.preferences.${encodeURIComponent(normalizedSubject)}`;
}

export function normalizeStudioPreferences(value) {
  return {
    taskCompletionNotices:
      typeof value?.taskCompletionNotices === "boolean"
        ? value.taskCompletionNotices
        : DEFAULT_STUDIO_PREFERENCES.taskCompletionNotices,
  };
}

export function readStudioPreferences(subject, storage = globalThis.localStorage) {
  if (!storage?.getItem) return { ...DEFAULT_STUDIO_PREFERENCES };
  try {
    const raw = storage.getItem(studioPreferenceStorageKey(subject));
    if (!raw) return { ...DEFAULT_STUDIO_PREFERENCES };
    return normalizeStudioPreferences(JSON.parse(raw));
  } catch {
    return { ...DEFAULT_STUDIO_PREFERENCES };
  }
}

export function writeStudioPreferences(
  subject,
  preferences,
  storage = globalThis.localStorage,
) {
  if (!storage?.setItem) return false;
  try {
    storage.setItem(
      studioPreferenceStorageKey(subject),
      JSON.stringify(normalizeStudioPreferences(preferences)),
    );
    return true;
  } catch {
    return false;
  }
}

