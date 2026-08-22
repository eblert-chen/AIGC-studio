export const GENERATION_MODES = [
  { id: "text_to_video", label: "文生视频", output: "video" },
  { id: "image_to_video", label: "图生视频", output: "video", requiredMedia: "image" },
  { id: "video_to_video", label: "视频重绘", output: "video", requiredMedia: "video" },
  { id: "text_to_image", label: "文生图", output: "image" },
];

export const MODE_IDS = GENERATION_MODES.map((item) => item.id);

const MODE_BY_ID = new Map(GENERATION_MODES.map((item) => [item.id, item]));
const DEFAULT_RATIOS = ["16:9"];
const DEFAULT_RESOLUTIONS = ["720p"];
const DEFAULT_DURATIONS = [5];
const DEFAULT_OUTPUT_COUNTS = [1];

function isObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function modeId(value) {
  const normalized = String(value ?? "").trim().toLowerCase().replaceAll("-", "_");
  return MODE_BY_ID.has(normalized) ? normalized : "";
}

function uniqueStrings(value, fallback = []) {
  if (!Array.isArray(value)) return [...fallback];
  const result = value
    .map((item) => String(item ?? "").trim())
    .filter(Boolean);
  return result.length ? [...new Set(result)] : [...fallback];
}

function uniqueIntegers(value, fallback = [], { min = 1, max = 10_000 } = {}) {
  const source = Array.isArray(value) ? value : value == null ? [] : [value];
  const result = source
    .map(Number)
    .filter((item) => Number.isInteger(item) && item >= min && item <= max);
  return result.length ? [...new Set(result)].sort((left, right) => left - right) : [...fallback];
}

function nonnegativeInteger(value, fallback = 0, max = 15) {
  const number = Number(value);
  return Number.isInteger(number) && number >= 0 ? Math.min(number, max) : fallback;
}

function positiveInteger(value, fallback, max = 10_000) {
  const number = Number(value);
  return Number.isInteger(number) && number > 0 ? Math.min(number, max) : fallback;
}

function valueFrom(source, limits, names) {
  for (const name of names) {
    if (source[name] !== undefined) return source[name];
    if (limits[name] !== undefined) return limits[name];
  }
  return undefined;
}

function modeDefaults(id) {
  const definition = MODE_BY_ID.get(id) ?? MODE_BY_ID.get("text_to_video");
  return {
    inputMediaTypes: definition.requiredMedia ? [definition.requiredMedia] : [],
    supportsFace: false,
    requiredResourceKeys: [],
    limits: {
      maxPromptLength: 10_000,
      maxImages: definition.requiredMedia === "image" ? 1 : 0,
      maxVideos: definition.requiredMedia === "video" ? 1 : 0,
      maxAudio: 0,
      durations: [...DEFAULT_DURATIONS],
      aspectRatios: [...DEFAULT_RATIOS],
      resolutions: [...DEFAULT_RESOLUTIONS],
      outputCounts: [...DEFAULT_OUTPUT_COUNTS],
    },
  };
}

function normalizeModeConfig(id, value, inherited = {}) {
  const source = isObject(value) ? value : {};
  const inheritedSource = isObject(inherited) ? inherited : {};
  const combined = { ...inheritedSource, ...source };
  const inheritedLimits = isObject(inheritedSource.limits) ? inheritedSource.limits : {};
  const sourceLimits = isObject(source.limits) ? source.limits : {};
  const limits = { ...inheritedLimits, ...sourceLimits };
  const defaults = modeDefaults(id);
  const definition = MODE_BY_ID.get(id);

  const maxImages = nonnegativeInteger(
    valueFrom(combined, limits, ["max_images", "maxImages", "image"]),
    defaults.limits.maxImages,
  );
  const maxVideos = nonnegativeInteger(
    valueFrom(combined, limits, ["max_videos", "maxVideos", "video"]),
    defaults.limits.maxVideos,
  );
  const maxAudio = nonnegativeInteger(
    valueFrom(combined, limits, ["max_audio", "max_audios", "maxAudio", "audio"]),
    defaults.limits.maxAudio,
  );
  const explicitMediaTypes = valueFrom(combined, limits, [
    "input_media_types",
    "inputMediaTypes",
    "media_types",
  ]);
  const derivedMediaTypes = [
    maxImages > 0 ? "image" : "",
    maxVideos > 0 ? "video" : "",
    maxAudio > 0 ? "audio" : "",
  ].filter(Boolean);
  const inputMediaTypes = uniqueStrings(explicitMediaTypes, derivedMediaTypes).filter(
    (item) => ["image", "video", "audio"].includes(item),
  );
  if (definition?.requiredMedia && !inputMediaTypes.includes(definition.requiredMedia)) {
    inputMediaTypes.push(definition.requiredMedia);
  }

  return {
    inputMediaTypes,
    supportsFace: Boolean(
      combined.supports_face ?? combined.supportsFace ?? combined.face_supported ?? false,
    ),
    requiredResourceKeys: uniqueStrings(
      combined.required_resource_keys ?? combined.requiredResourceKeys,
      [],
    ),
    limits: {
      maxPromptLength: positiveInteger(
        valueFrom(combined, limits, ["max_prompt_length", "maxPromptLength"]),
        defaults.limits.maxPromptLength,
      ),
      maxImages: definition?.requiredMedia === "image" ? Math.max(1, maxImages) : maxImages,
      maxVideos: definition?.requiredMedia === "video" ? Math.max(1, maxVideos) : maxVideos,
      maxAudio,
      durations: uniqueIntegers(
        valueFrom(combined, limits, ["duration_seconds", "durations", "duration"]),
        defaults.limits.durations,
        { min: 1, max: 3600 },
      ),
      aspectRatios: uniqueStrings(
        valueFrom(combined, limits, [
          "aspect_ratios",
          "aspectRatios",
          "ratios",
          "aspect_ratio",
        ]),
        defaults.limits.aspectRatios,
      ),
      resolutions: uniqueStrings(
        valueFrom(combined, limits, ["resolutions", "resolution"]),
        defaults.limits.resolutions,
      ),
      outputCounts: uniqueIntegers(
        valueFrom(combined, limits, ["output_counts", "outputCounts", "output_count"]),
        defaults.limits.outputCounts,
        { min: 1, max: 16 },
      ),
    },
  };
}

function strictNonnegativeInteger(value, max = 15) {
  const number = Number(value);
  return Number.isInteger(number) && number >= 0 && number <= max ? number : 0;
}

function normalizeEffectiveModeConfig(id, value) {
  if (!isObject(value) || !isObject(value.limits)) return null;

  const limits = value.limits;
  const declaredMediaTypes = Array.isArray(value.input_media_types)
    ? [...new Set(
        value.input_media_types
          .map((item) => String(item ?? "").trim())
          .filter((item) => ["image", "video", "audio"].includes(item)),
      )]
    : [];
  const declaredMaximums = {
    image: strictNonnegativeInteger(limits.max_images),
    video: strictNonnegativeInteger(limits.max_videos),
    audio: strictNonnegativeInteger(limits.max_audio),
  };
  const maximums = Object.fromEntries(
    Object.entries(declaredMaximums).map(([mediaType, maximum]) => [
      mediaType,
      declaredMediaTypes.includes(mediaType) ? maximum : 0,
    ]),
  );
  const inputMediaTypes = declaredMediaTypes.filter(
    (mediaType) => maximums[mediaType] > 0,
  );
  const definition = MODE_BY_ID.get(id);
  if (
    (definition?.requiredMedia && !inputMediaTypes.includes(definition.requiredMedia)) ||
    maximums.image + maximums.video + maximums.audio > 15
  ) {
    return null;
  }

  const maxPromptLength = positiveInteger(limits.max_prompt_length, 0);
  const durations = uniqueIntegers(limits.duration_seconds, [], {
    min: 1,
    max: 3600,
  });
  const aspectRatios = uniqueStrings(limits.aspect_ratios, []);
  const resolutions = uniqueStrings(limits.resolutions, []);
  const outputCounts = uniqueIntegers(limits.output_counts, [], {
    min: 1,
    max: 16,
  });
  if (
    !maxPromptLength ||
    !durations.length ||
    !aspectRatios.length ||
    !resolutions.length ||
    !outputCounts.length
  ) {
    return null;
  }

  return {
    inputMediaTypes,
    supportsFace: value.supports_face === true,
    requiredResourceKeys: uniqueStrings(value.required_resource_keys, []),
    limits: {
      maxPromptLength,
      maxImages: maximums.image,
      maxVideos: maximums.video,
      maxAudio: maximums.audio,
      durations,
      aspectRatios,
      resolutions,
      outputCounts,
    },
  };
}

function configWithSemanticValues(key, value) {
  const config = isObject(value) ? { ...value } : {};
  const normalizedKey = String(key ?? "").trim().toLowerCase().replaceAll("-", "_");
  if (!Array.isArray(config.values)) return config;
  if (["duration", "durations", "duration_seconds"].includes(normalizedKey)) {
    config.duration_seconds = config.values;
  } else if (["ratio", "ratios", "aspect_ratio", "aspect_ratios"].includes(normalizedKey)) {
    config.aspect_ratios = config.values;
  } else if (["resolution", "resolutions"].includes(normalizedKey)) {
    config.resolutions = config.values;
  } else if (["output_count", "output_counts"].includes(normalizedKey)) {
    config.output_counts = config.values;
  }
  return config;
}

function rawCapabilityModes(source) {
  const raw = isObject(source?.capabilities) ? source.capabilities : {};
  const generation = isObject(raw.generation) ? raw.generation : {};
  if (isObject(generation.modes)) {
    return Object.fromEntries(
      Object.entries(generation.modes)
        .map(([key, value]) => [modeId(key), value])
        .filter(([key]) => key),
    );
  }

  const explicitModes = {};
  const sharedLayers = [];
  for (const [key, value] of Object.entries(raw)) {
    const normalized = modeId(key);
    if (normalized) {
      explicitModes[normalized] = value;
    } else {
      sharedLayers.push(configWithSemanticValues(key, value));
    }
  }
  const shared = Object.assign({}, ...sharedLayers);
  const declaredModes = uniqueStrings(shared.modes ?? shared.mode, [])
    .map(modeId)
    .filter(Boolean);
  const ids = [...new Set([...Object.keys(explicitModes), ...declaredModes])];
  if (!ids.length && Object.keys(raw).length) ids.push("text_to_video");
  return Object.fromEntries(
    ids.map((id) => [id, { ...shared, ...(isObject(explicitModes[id]) ? explicitModes[id] : {}) }]),
  );
}

function applyLegacyOverride(modes, override) {
  if (!isObject(override) || !Object.keys(override).length) return modes;
  const overrideModeList = Array.isArray(override.modes)
    ? override.modes.map(modeId).filter(Boolean)
    : null;
  const nested = isObject(override.capabilities) ? override.capabilities : {};
  const canonicalModes = isObject(override.modes) ? override.modes : {};
  const result = {};
  for (const [id, config] of Object.entries(modes)) {
    if (overrideModeList && !overrideModeList.includes(id)) continue;
    const perMode =
      canonicalModes[id] ??
      canonicalModes[id.replaceAll("_", "-")] ??
      nested[id] ??
      nested[id.replaceAll("_", "-")] ??
      override[id] ??
      override[id.replaceAll("_", "-")] ??
      {};
    result[id] = normalizeModeConfig(id, perMode, { ...config, ...override });
  }
  return result;
}

export function resolveEffectiveCapabilities(source) {
  const effective = isObject(source?.effective_capabilities)
    ? source.effective_capabilities
    : null;
  const effectiveModes = isObject(effective?.modes) ? effective.modes : null;
  if (effectiveModes) {
    return {
      schemaVersion: Number(effective.schema_version) || 1,
      modes: Object.fromEntries(
        Object.entries(effectiveModes)
          .map(([key, value]) => {
            const id = modeId(key);
            const normalized = id ? normalizeEffectiveModeConfig(id, value) : null;
            return id && normalized ? [id, normalized] : null;
          })
          .filter(Boolean),
      ),
    };
  }

  const rawModes = rawCapabilityModes(source);
  const normalized = Object.fromEntries(
    Object.entries(rawModes).map(([id, config]) => [id, normalizeModeConfig(id, config)]),
  );
  return {
    schemaVersion: 1,
    modes: applyLegacyOverride(normalized, source?.config_override),
  };
}

export function modeLabel(id) {
  return MODE_BY_ID.get(modeId(id))?.label ?? String(id ?? "未知模式");
}

export function firstSupportedMode(capabilities, preferred = "") {
  const modes = Object.keys(capabilities?.modes ?? {});
  const normalizedPreferred = modeId(preferred);
  return normalizedPreferred && modes.includes(normalizedPreferred)
    ? normalizedPreferred
    : modes[0] ?? "";
}

export function capabilityForMode(capabilities, id) {
  const normalized = modeId(id);
  return capabilities?.modes?.[normalized] ?? null;
}

export function capabilityControlVisibility(capability) {
  const mediaTypes = Array.isArray(capability?.inputMediaTypes)
    ? capability.inputMediaTypes
    : [];
  return {
    image: mediaTypes.includes("image") && capability?.limits?.maxImages > 0,
    video: mediaTypes.includes("video") && capability?.limits?.maxVideos > 0,
    audio: mediaTypes.includes("audio") && capability?.limits?.maxAudio > 0,
    face: capability?.supportsFace === true,
  };
}

const MEDIA_TYPES = ["image", "video", "audio"];

function draftFiles(value) {
  const source = isObject(value) ? value : {};
  return Object.fromEntries(
    MEDIA_TYPES.map((mediaType) => [
      mediaType,
      Array.isArray(source[mediaType]) ? source[mediaType] : [],
    ]),
  );
}

function firstAllowed(current, allowed) {
  return allowed.includes(current) ? current : allowed[0];
}

function sameArray(left, right) {
  return left.length === right.length && left.every((item, index) => item === right[index]);
}

/**
 * Reconciles a mutable studio draft against one server-computed capability.
 * The returned draft is safe to render immediately after a model or mode switch.
 */
export function reconcileGenerationDraft(capabilities, requestedMode, draft = {}) {
  const mode = firstSupportedMode(capabilities, requestedMode);
  const capability = capabilityForMode(capabilities, mode);
  const files = draftFiles(draft.files);
  if (!mode || !capability) {
    const removedMediaCount = MEDIA_TYPES.reduce(
      (total, mediaType) => total + files[mediaType].length,
      0,
    );
    return {
      ok: false,
      mode: "",
      capability: null,
      draft: {
        ...draft,
        duration: null,
        aspectRatio: "",
        resolution: "",
        outputCount: null,
        faceEnabled: false,
        files: { image: [], video: [], audio: [] },
      },
      removedMediaCount,
      changes: ["mode", "parameters", "face", ...(removedMediaCount ? ["files"] : [])],
      error: "当前模型没有可用的生成能力声明。",
    };
  }

  const limits = capability.limits;
  const nextFiles = {
    image: capability.inputMediaTypes.includes("image")
      ? files.image.slice(0, limits.maxImages)
      : [],
    video: capability.inputMediaTypes.includes("video")
      ? files.video.slice(0, limits.maxVideos)
      : [],
    audio: capability.inputMediaTypes.includes("audio")
      ? files.audio.slice(0, limits.maxAudio)
      : [],
  };
  const removedMediaCount = MEDIA_TYPES.reduce(
    (total, mediaType) => total + files[mediaType].length - nextFiles[mediaType].length,
    0,
  );
  const prompt = String(draft.prompt ?? "");
  const nextPrompt = prompt.slice(0, limits.maxPromptLength);
  const nextDraft = {
    ...draft,
    prompt: nextPrompt,
    duration: firstAllowed(Number(draft.duration), limits.durations),
    aspectRatio: firstAllowed(String(draft.aspectRatio ?? ""), limits.aspectRatios),
    resolution: firstAllowed(String(draft.resolution ?? ""), limits.resolutions),
    outputCount: firstAllowed(Number(draft.outputCount), limits.outputCounts),
    faceEnabled: capability.supportsFace ? Boolean(draft.faceEnabled) : false,
    files: nextFiles,
  };
  const changes = [];
  if (mode !== requestedMode) changes.push("mode");
  if (nextPrompt !== prompt) changes.push("prompt");
  if (nextDraft.duration !== Number(draft.duration)) changes.push("duration");
  if (nextDraft.aspectRatio !== String(draft.aspectRatio ?? "")) changes.push("aspectRatio");
  if (nextDraft.resolution !== String(draft.resolution ?? "")) changes.push("resolution");
  if (nextDraft.outputCount !== Number(draft.outputCount)) changes.push("outputCount");
  if (nextDraft.faceEnabled !== Boolean(draft.faceEnabled)) changes.push("face");
  if (MEDIA_TYPES.some((mediaType) => !sameArray(files[mediaType], nextFiles[mediaType]))) {
    changes.push("files");
  }
  return {
    ok: true,
    mode,
    capability,
    draft: nextDraft,
    removedMediaCount,
    changes,
    error: "",
  };
}

function assetReference(asset, mediaType) {
  const assetId = String(asset?.asset_id ?? asset?.id ?? "").trim();
  return assetId ? { asset_id: assetId, media_type: mediaType } : null;
}

/**
 * Builds a request only from fields allowed by the current capability.
 * Unsupported media and face values are intentionally omitted.
 */
export function buildCapabilityRequestPayload(
  capabilities,
  requestedMode,
  draft = {},
  { includeAssets = true } = {},
) {
  const reconciled = reconcileGenerationDraft(capabilities, requestedMode, draft);
  if (!reconciled.ok) {
    return { ok: false, payload: null, error: reconciled.error, reconciled };
  }
  if (reconciled.mode !== requestedMode) {
    return {
      ok: false,
      payload: null,
      error: "当前模型不支持原生成模式，已切换到第一个可用模式，请确认后重试。",
      reconciled,
    };
  }

  const normalizedDraft = reconciled.draft;
  const prompt = String(normalizedDraft.prompt ?? "").trim();
  if (!prompt) {
    return { ok: false, payload: null, error: "制作说明不能为空。", reconciled };
  }
  const assets = MEDIA_TYPES.flatMap((mediaType) =>
    normalizedDraft.files[mediaType]
      .map((asset) => assetReference(asset, mediaType))
      .filter(Boolean),
  );
  if (
    reconciled.mode === "image_to_video" &&
    !assets.some((asset) => asset.media_type === "image")
  ) {
    return { ok: false, payload: null, error: "图生视频至少需要 1 张参考图。", reconciled };
  }
  if (
    reconciled.mode === "video_to_video" &&
    !assets.some((asset) => asset.media_type === "video")
  ) {
    return { ok: false, payload: null, error: "视频重绘至少需要 1 个参考视频。", reconciled };
  }

  const payload = {
    mode: reconciled.mode,
    prompt,
    duration_seconds: normalizedDraft.duration,
    aspect_ratio: normalizedDraft.aspectRatio,
    resolution: normalizedDraft.resolution,
    output_count: normalizedDraft.outputCount,
    ...(reconciled.capability.supportsFace
      ? { face_enabled: normalizedDraft.faceEnabled }
      : {}),
    ...(includeAssets && assets.length ? { assets } : {}),
  };
  return { ok: true, payload, error: "", reconciled };
}

export function capabilitySummary(source) {
  const effective = source?.modes ? source : resolveEffectiveCapabilities(source);
  return Object.entries(effective.modes ?? {}).map(([id, capability]) => {
    const limits = capability.limits;
    const inputs = [];
    if (limits.maxImages > 0) inputs.push(`${limits.maxImages} 图`);
    if (limits.maxVideos > 0) inputs.push(`${limits.maxVideos} 视频`);
    if (limits.maxAudio > 0) inputs.push(`${limits.maxAudio} 音频`);
    if (!inputs.length) inputs.push("纯文本");
    const face = capability.supportsFace ? "支持人脸" : "不含人脸";
    const parameters = [
      limits.aspectRatios.join("/"),
      limits.resolutions.join("/"),
      `${limits.durations.join("/")} 秒`,
      `${limits.outputCounts.join("/")} 个产物`,
    ];
    return {
      id,
      label: modeLabel(id),
      detail: `${inputs.join("、")}，${face}，${parameters.join("，")}`,
    };
  });
}

export function toCanonicalGenerationConfig(modes) {
  return {
    schema_version: 1,
    modes: Object.fromEntries(
      Object.entries(modes).map(([id, capability]) => [
        id,
        {
          input_media_types: [...capability.inputMediaTypes],
          supports_face: Boolean(capability.supportsFace),
          required_resource_keys: [...capability.requiredResourceKeys],
          limits: {
            max_prompt_length: capability.limits.maxPromptLength,
            max_images: capability.limits.maxImages,
            max_videos: capability.limits.maxVideos,
            max_audio: capability.limits.maxAudio,
            duration_seconds: [...capability.limits.durations],
            aspect_ratios: [...capability.limits.aspectRatios],
            resolutions: [...capability.limits.resolutions],
            output_counts: [...capability.limits.outputCounts],
          },
        },
      ]),
    ),
  };
}

export function defaultEditorCapability(id) {
  return modeDefaults(modeId(id) || "text_to_video");
}
