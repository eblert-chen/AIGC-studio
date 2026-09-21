import { useCallback, useRef, useState } from "react";

import { filesFromPendingRequest } from "./pendingGeneration.js";
import { capabilityMediaLimits } from "../modelCapabilities.js";

const MEDIA_TYPES = ["image", "video", "audio"];
const assetId = (asset) => {
  const id = asset?.asset_id ?? asset?.id;
  return typeof id === "string" ? id.trim() : "";
};

/** One atomic append for picker, library and promoted-result inputs. */
export function appendGenerationInputAssets(capability, files, kind, incoming) {
  const limit = capabilityMediaLimits(capability)[kind] ?? 0;
  if (!MEDIA_TYPES.includes(kind) || !Array.isArray(incoming)) {
    return { files, addedCount: 0, overflowCount: 0, duplicateCount: 0, invalidCount: 0 };
  }
  const current = Array.isArray(files?.[kind]) ? files[kind].slice(0, limit) : [];
  const seen = new Set(current.map(assetId).filter(Boolean));
  const additions = [];
  let overflowCount = 0;
  let duplicateCount = 0;
  let invalidCount = 0;
  for (const asset of incoming) {
    const id = assetId(asset);
    if (!asset || typeof asset !== "object" || Array.isArray(asset) || !id || asset.media_type !== kind) {
      invalidCount += 1;
      continue;
    }
    if (seen.has(id)) {
      duplicateCount += 1;
      continue;
    }
    if (current.length + additions.length >= limit) {
      overflowCount += 1;
      continue;
    }
    seen.add(id);
    additions.push(asset);
  }
  return {
    files: { ...files, [kind]: [...current, ...additions] },
    addedCount: additions.length,
    overflowCount,
    duplicateCount,
    invalidCount,
  };
}

/** Serialize uploads; a stale completion cannot unlock or edit a newer draft. */
export function createGenerationUploadGate() {
  let active = null;
  return {
    get busy() { return active !== null; },
    begin(context) {
      if (active) return null;
      active = Object.freeze({ ...context });
      return active;
    },
    canAppend(request, context) {
      return active === request
        && request !== null
        && !context.locked
        && request.workspaceKey === context.workspaceKey
        && request.draftRevision === context.draftRevision;
    },
    finish(request) {
      if (active !== request || request === null) return false;
      active = null;
      return true;
    },
  };
}

export function useGenerationInputs({
  capability, modelId, mode, capabilityVersion, workspaceKey,
  filesRef, setFiles, pendingCreateRef, draftKey = "quick",
}) {
  const [generationUploadGate] = useState(createGenerationUploadGate);
  const activeCapabilityRef = useRef(capability);
  activeCapabilityRef.current = capability;
  const revisionRef = useRef(0);
  const contextRef = useRef({ identity: "", workspaceKey });
  const identity = JSON.stringify([workspaceKey, draftKey, modelId, mode, capabilityVersion, capability]);
  if (contextRef.current.identity !== identity) {
    revisionRef.current += 1;
    contextRef.current = { identity, workspaceKey };
  }
  const generationInputContext = () => ({
    workspaceKey: contextRef.current.workspaceKey,
    draftRevision: revisionRef.current,
    locked: Boolean(pendingCreateRef.current),
  });
  const appendDraftInputs = (kind, incoming) => {
    if (pendingCreateRef.current) return { addedCount: 0, overflowCount: 0, duplicateCount: 0, invalidCount: 0 };
    const result = appendGenerationInputAssets(activeCapabilityRef.current, filesRef.current, kind, incoming);
    setFiles(result.files);
    return result;
  };
  return {
    generationUploadGate, activeCapabilityRef, generationInputContext, appendDraftInputs,
    invalidateGenerationInputContext: () => { revisionRef.current += 1; },
  };
}

export function emptyDraftFiles() {
  return { image: [], video: [], audio: [] };
}

export function useGenerationDraft({
  demoMode,
  demoModels,
  demoInitialMode,
  demoInitialCapability,
  initialPendingCreate,
}) {
  const [draftIdentity, setDraftIdentity] = useState("");
  const [modelId, setModelId] = useState(
    demoMode ? demoModels[0].id : initialPendingCreate?.modelId ?? "",
  );
  const [generationMode, setGenerationMode] = useState(
    demoMode
      ? demoInitialMode
      : initialPendingCreate?.requestPayload.mode ?? "",
  );
  const [ratio, setRatio] = useState(
    demoMode
      ? demoInitialCapability.limits.aspectRatios[0]
      : initialPendingCreate?.requestPayload.aspect_ratio ?? "",
  );
  const [resolution, setResolution] = useState(
    demoMode
      ? demoInitialCapability.limits.resolutions[0]
      : initialPendingCreate?.requestPayload.resolution ?? "",
  );
  const [duration, setDuration] = useState(
    demoMode ? 15 : initialPendingCreate?.requestPayload.duration_seconds ?? null,
  );
  const [outputCount, setOutputCount] = useState(
    demoMode ? 1 : initialPendingCreate?.requestPayload.output_count ?? null,
  );
  const [faceEnabled, setFaceEnabled] = useState(
    demoMode ? false : Boolean(initialPendingCreate?.requestPayload.face_enabled),
  );
  const [prompt, setPrompt] = useState(
    demoMode
      ? "突出产品防水便携的特点，户外场景拍摄，光线自然干净，节奏明快，适合短视频投放。"
      : initialPendingCreate?.requestPayload.prompt ?? "",
  );
  const [files, setFilesState] = useState(() => (
    !demoMode && initialPendingCreate
      ? filesFromPendingRequest(initialPendingCreate.requestPayload)
      : emptyDraftFiles()
  ));
  const filesRef = useRef(files);
  // Upload and library events can arrive before React commits a render. Keep
  // capacity checks and file mutations on the same immediately updated draft.
  const setFiles = useCallback((update) => {
    const nextFiles = typeof update === "function" ? update(filesRef.current) : update;
    filesRef.current = nextFiles;
    setFilesState(nextFiles);
  }, []);

  // The editing surface may unmount without destroying this shared engine.
  // Identity changes atomically with its values so a late autosave/upload cannot
  // write the previous scene's content into the newly selected scene.
  const replaceDraft = useCallback((next, identity) => {
    setDraftIdentity(identity);
    setModelId(next.modelId || "");
    setGenerationMode(next.generationMode || "");
    setPrompt(next.prompt || "");
    setRatio(next.ratio || "");
    setResolution(next.resolution || "");
    setDuration(next.duration ?? null);
    setOutputCount(next.outputCount ?? null);
    setFaceEnabled(Boolean(next.faceEnabled));
    setFiles(next.files || emptyDraftFiles());
  }, [setFiles]);

  return {
    draftIdentity,
    replaceDraft,
    modelId,
    setModelId,
    generationMode,
    setGenerationMode,
    ratio,
    setRatio,
    resolution,
    setResolution,
    duration,
    setDuration,
    outputCount,
    setOutputCount,
    faceEnabled,
    setFaceEnabled,
    prompt,
    setPrompt,
    files,
    filesRef,
    setFiles,
  };
}
