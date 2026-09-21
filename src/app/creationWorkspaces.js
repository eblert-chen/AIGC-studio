// Local authoring metadata only. Task IDs and asset IDs are references, never
// authorization, capability, billing, task-state, or artifact-access evidence.
const STORAGE_PREFIX = "ai-video.creation-workspace.v1:";
const KINDS = ["console", "notebook", "canvas"];
const MEDIA_TYPES = ["image", "video", "audio"];
const PANELS = [null, "recipe", "references", "specs", "model", "readiness"];
export const LINEAGE_BRANCH_KINDS = ["composition", "performance", "camera", "continuity", "style", "model", "custom"];
const LINEAGE_RELATIONS = ["", "settings", "import", "lineage"];
const DRAFT_KEYS = ["modelId", "generationMode", "prompt", "ratio", "resolution", "duration",
  "outputCount", "faceEnabled", "files"];
const MAX_ENTRIES = 500;
const MAX_SHOTS = 200;
const MAX_TAKES = 100;
const MAX_PROMPT = 10_000;
// Storage abuse ceilings, not generation capabilities or visible upload limits.
const MAX_REFERENCES = 1_000;
const MAX_SERIALIZED_LENGTH = 12_000_000;
const FORBIDDEN_KEYS = new Set(["__proto__", "prototype", "constructor"]);
const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9_.:-]*$/;
const STORAGE_BASELINES = new WeakMap();
const own = (value, key) => Object.hasOwn(value, key);

function record(value, label, keys) {
  if (!value || typeof value !== "object" || Array.isArray(value)
    || ![Object.prototype, null].includes(Object.getPrototypeOf(value))) {
    throw new TypeError(`${label}必须是普通对象。`);
  }
  for (const key of Reflect.ownKeys(value)) {
    if (typeof key !== "string" || FORBIDDEN_KEYS.has(key)
      || !own(Object.getOwnPropertyDescriptor(value, key), "value")
      || (keys && !keys.includes(key))) {
      throw new TypeError(`${label}包含不允许的字段。`);
    }
  }
  return value;
}

function list(value, label, limit) {
  if (!Array.isArray(value)) throw new TypeError(`${label}必须是列表。`);
  if (value.length > limit) throw new RangeError(`${label}最多保留 ${limit} 项，请先整理后再试。`);
  for (let index = 0; index < value.length; index += 1) {
    if (!own(value, index)
      || !own(Object.getOwnPropertyDescriptor(value, index), "value")) {
      throw new TypeError(`${label}包含无效项目。`);
    }
  }
  if (Reflect.ownKeys(value).some((key) => (
    key !== "length" && (typeof key !== "string" || !/^(0|[1-9]\d*)$/.test(key))
  ))) throw new TypeError(`${label}包含不允许的字段。`);
  return value;
}

function completeRecord(value, label, keys) {
  record(value, label, keys);
  if (keys.some((key) => !own(value, key))) throw new TypeError(`${label}缺少必要字段。`);
  return value;
}

function text(value, label, maxLength, fallback = "") {
  const result = value === undefined ? fallback : value;
  if (typeof result !== "string" || /[\u0000-\u001f\u007f]/.test(result)) {
    throw new TypeError(`${label}必须是有效文本。`);
  }
  if (result.length > maxLength) throw new RangeError(`${label}过长，未保存此次修改。`);
  return result;
}

function id(value, label, optional = false) {
  const result = text(value, label, 256);
  if (optional && result === "") return result;
  if (!IDENTIFIER.test(result) || FORBIDDEN_KEYS.has(result)) {
    throw new TypeError(`${label}无效。`);
  }
  return result;
}

function scope(value) {
  const result = text(value, "工作区标识", 1024);
  if (!result || result !== result.trim()) throw new TypeError("缺少有效的用户及工作区标识。");
  return result;
}

function kind(value) {
  if (!KINDS.includes(value)) throw new TypeError("未知的创作工作台。");
  return value;
}

function integer(value, label, { nullable = false, min = 0 } = {}) {
  if (nullable && (value === null || value === undefined)) return null;
  if (!Number.isSafeInteger(value) || value < min) throw new TypeError(`${label}必须是有效整数。`);
  return value;
}

function coordinate(value) {
  if (typeof value !== "number" || !Number.isFinite(value) || Math.abs(value) > 1_000_000) {
    throw new TypeError("画布坐标无效。");
  }
  return value;
}

function boolean(value, label, fallback = false) {
  const result = value === undefined ? fallback : value;
  if (typeof result !== "boolean") throw new TypeError(`${label}必须是布尔值。`);
  return result;
}

function lineageBranchKind(value, optional = false) {
  const result = text(value, "分支类型", 32);
  if (optional && result === "") return result;
  if (!LINEAGE_BRANCH_KINDS.includes(result)) throw new TypeError("分支类型无效。");
  return result;
}

function timestamp(value = new Date().toISOString()) {
  if (!(typeof value === "string" || typeof value === "number" || value instanceof Date)) {
    throw new TypeError("工作台时间无效。");
  }
  const result = new Date(value);
  if (!Number.isFinite(result.getTime())) throw new TypeError("工作台时间无效。");
  return result.toISOString();
}

function persistedTimestamp(value, label) {
  if (typeof value !== "string") throw new TypeError(`${label}必须是标准时间文本。`);
  const result = timestamp(value);
  if (result !== value) throw new TypeError(`${label}必须使用标准 UTC 时间。`);
  return result;
}

function nextRevision(state) {
  if (state.revision === Number.MAX_SAFE_INTEGER) throw new RangeError("草稿版本已达上限，未保存此次修改。");
  return state.revision + 1;
}

function nextUpdatedAt(entry, now) {
  return timestamp(Math.max(Date.parse(entry.updatedAt), Date.parse(timestamp(now))));
}

function cleanFiles(value = {}) {
  record(value, "参考素材", MEDIA_TYPES);
  let count = 0;
  const seen = new Set();
  return Object.fromEntries(MEDIA_TYPES.map((mediaType) => {
    const files = list(value[mediaType] === undefined ? [] : value[mediaType], "参考素材", MAX_REFERENCES).map((asset) => {
      record(asset, "素材");
      const assetId = id(asset.asset_id ?? asset.id, "素材标识");
      if (asset.asset_id !== undefined && asset.id !== undefined && asset.asset_id !== asset.id) {
        throw new TypeError("素材标识不一致。");
      }
      if (asset.media_type !== mediaType) throw new TypeError("素材类型与分组不一致。");
      if (seen.has(assetId)) throw new TypeError("参考素材包含重复标识。");
      seen.add(assetId);
      if (![undefined, "", "active", "disabled"].includes(asset.status)) {
        throw new TypeError("素材状态无效。");
      }
      count += 1;
      if (count > MAX_REFERENCES) throw new RangeError("参考素材过多，未保存此次修改。");
      return {
        asset_id: assetId,
        media_type: mediaType,
        original_filename: text(asset.original_filename, "素材名称", 512),
        // Missing status is unknown, never silently upgraded to active.
        status: asset.status ?? "",
      };
    });
    return [mediaType, files];
  }));
}

/** Whitelist the editable draft; never serialize asset URLs or model snapshots. */
export function sanitizeCreationDraft(value = {}) {
  record(value, "创作草稿");
  const prompt = value.prompt === undefined ? "" : value.prompt;
  if (typeof prompt !== "string" || /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(prompt)) {
    throw new TypeError("制作说明必须是有效文本。");
  }
  if (prompt.length > MAX_PROMPT * 2 || Array.from(prompt).length > MAX_PROMPT) {
    throw new RangeError(`制作说明最多保存 ${MAX_PROMPT} 字，未截断原文。`);
  }
  if (value.faceEnabled !== undefined && typeof value.faceEnabled !== "boolean") {
    throw new TypeError("人脸选项必须是布尔值。");
  }
  return {
    modelId: text(value.modelId, "模型标识", 256),
    generationMode: text(value.generationMode, "创作方式", 96),
    prompt,
    ratio: text(value.ratio, "画面比例", 64),
    resolution: text(value.resolution, "分辨率", 64),
    duration: integer(value.duration, "时长", { nullable: true, min: 1 }),
    outputCount: integer(value.outputCount, "生成数量", { nullable: true, min: 1 }),
    faceEnabled: value.faceEnabled ?? false,
    files: cleanFiles(value.files),
  };
}

export function emptyCreationWorkspace(scopeKey) {
  return {
    version: 2,
    scopeKey: scope(scopeKey),
    revision: 0,
    quick: { draft: sanitizeCreationDraft(), panel: null, mobileOpen: false },
    active: { console: "", notebook: "", canvas: "" },
    lineage: { activeShotId: "", shots: [] },
    entries: [],
  };
}

function normalizeLegacyWorkspace(value, expectedScope) {
  completeRecord(value, "创作工作区", ["version", "scopeKey", "revision", "quick", "active", "entries"]);
  if (value.version !== 1) throw new TypeError("不支持的创作工作区版本。");
  const scopeKey = scope(value.scopeKey);
  if (expectedScope !== undefined && scopeKey !== expectedScope) {
    throw new TypeError("草稿不属于当前用户的工作区。");
  }
  const revision = integer(value.revision, "草稿版本");
  const quick = completeRecord(value.quick, "快捷创作", ["draft", "panel", "mobileOpen"]);
  if (!PANELS.includes(quick.panel) || typeof quick.mobileOpen !== "boolean") {
    throw new TypeError("快捷创作的显示状态无效。");
  }
  completeRecord(value.active, "当前工作项", KINDS);
  const legacyEntries = list(value.entries, "工作台记录", MAX_ENTRIES).map((raw) => {
    completeRecord(raw, "工作台记录", ["id", "kind", "title", "order", "parentId", "relation", "x", "y",
      "draft", "taskIds", "selectedTaskId", "createdAt", "updatedAt"]);
    const taskIds = list(raw.taskIds, "镜头版本", MAX_TAKES).map((taskId) => id(taskId, "任务标识"));
    if (new Set(taskIds).size !== taskIds.length) throw new TypeError("镜头版本包含重复任务。");
    const selectedTaskId = id(raw.selectedTaskId, "选中任务", true);
    if (selectedTaskId && !taskIds.includes(selectedTaskId)) throw new TypeError("选中任务不属于当前工作项。");
    if (!["", "settings", "import"].includes(raw.relation)) throw new TypeError("节点关系无效。");
    if (typeof raw.createdAt !== "string" || typeof raw.updatedAt !== "string") {
      throw new TypeError("工作台时间无效。");
    }
    const createdAt = timestamp(raw.createdAt);
    const updatedAt = timestamp(raw.updatedAt);
    if (Date.parse(updatedAt) < Date.parse(createdAt)) throw new TypeError("工作台更新时间无效。");
    return {
      id: id(raw.id, "工作项标识"), kind: kind(raw.kind), title: text(raw.title, "工作项标题", 200),
      order: integer(raw.order, "工作项顺序"), parentId: id(raw.parentId, "父节点标识", true),
      relation: raw.relation, x: coordinate(raw.x), y: coordinate(raw.y),
      draft: sanitizeCreationDraft(raw.draft), taskIds, selectedTaskId, createdAt, updatedAt,
    };
  });
  const byId = new Map(legacyEntries.map((entry) => [entry.id, entry]));
  if (byId.size !== legacyEntries.length) throw new TypeError("工作项标识重复。");
  for (const entry of legacyEntries) {
    if (entry.parentId) {
      if (entry.kind !== "canvas" || entry.relation !== "settings"
        || byId.get(entry.parentId)?.kind !== "canvas") throw new TypeError("画布父节点无效。");
      const visited = new Set([entry.id]);
      let parent = byId.get(entry.parentId);
      while (parent) {
        if (visited.has(parent.id)) throw new TypeError("画布关系不能形成循环。");
        visited.add(parent.id);
        parent = byId.get(parent.parentId);
      }
    } else if (entry.relation === "settings") {
      throw new TypeError("复用设置的节点必须有明确父节点。");
    }
  }
  const active = Object.fromEntries(KINDS.map((type) => {
    const selected = id(value.active[type], "当前工作项", true);
    if (selected && byId.get(selected)?.kind !== type) throw new TypeError("当前工作项与工作台不一致。");
    const orders = legacyEntries.filter((entry) => entry.kind === type).map((entry) => entry.order).sort((a, b) => a - b);
    if (orders.some((order, index) => order !== index)) throw new TypeError("工作项顺序无效。");
    return [type, selected];
  }));

  const canvasEntries = legacyEntries.filter((entry) => entry.kind === "canvas");
  const rootFor = (entry) => {
    let current = entry;
    while (current.parentId) current = byId.get(current.parentId);
    return current;
  };
  const roots = [...new Map(canvasEntries
    .sort((left, right) => left.order - right.order)
    .map((entry) => {
      const root = rootFor(entry);
      return [root.id, root];
    })).values()];
  const shotByRoot = new Map(roots.map((root, index) => [root.id, `lineage-shot-${index + 1}`]));
  const shots = roots.map((root, index) => ({
    id: shotByRoot.get(root.id), title: `镜头 ${index + 1}`, order: index,
    canonicalEntryId: "", canonicalTaskId: "", canonicalArtifactId: "", createdAt: root.createdAt, updatedAt: root.updatedAt,
  }));
  const entries = legacyEntries.map((entry) => ({
    ...entry,
    // Version 1 did not persist an artifact ID, so its task-only selection is
    // not an exact Take. Preserve the task inventory but clear the ambiguous
    // selection during migration.
    selectedTaskId: "", selectedArtifactId: "",
    shotId: entry.kind === "canvas" ? shotByRoot.get(rootFor(entry).id) : "",
    sourceTaskId: "", sourceArtifactId: "", branchKind: "", branchReason: "", sourceDraft: null, archived: false,
  }));
  if (!active.canvas) active.canvas = entries.find((entry) => entry.kind === "canvas")?.id || "";
  const activeCanvas = entries.find((entry) => entry.id === active.canvas);
  return normalizeWorkspace({
    version: 2, scopeKey, revision,
    quick: { draft: sanitizeCreationDraft(quick.draft), panel: quick.panel, mobileOpen: quick.mobileOpen },
    active,
    lineage: { activeShotId: activeCanvas?.shotId || shots[0]?.id || "", shots },
    entries,
  }, expectedScope);
}

function normalizeWorkspace(value, expectedScope) {
  record(value, "创作工作区");
  if (value.version === 1) return normalizeLegacyWorkspace(value, expectedScope);
  completeRecord(value, "创作工作区", ["version", "scopeKey", "revision", "quick", "active", "lineage", "entries"]);
  if (value.version !== 2) throw new TypeError("不支持的创作工作区版本。");
  const scopeKey = scope(value.scopeKey);
  if (expectedScope !== undefined && scopeKey !== expectedScope) throw new TypeError("草稿不属于当前用户的工作区。");
  const revision = integer(value.revision, "草稿版本");
  const quick = completeRecord(value.quick, "快捷创作", ["draft", "panel", "mobileOpen"]);
  if (!PANELS.includes(quick.panel) || typeof quick.mobileOpen !== "boolean") throw new TypeError("快捷创作的显示状态无效。");
  completeRecord(value.active, "当前工作项", KINDS);
  const rawLineage = completeRecord(value.lineage, "谱系镜头", ["activeShotId", "shots"]);
  const shots = list(rawLineage.shots, "谱系镜头", MAX_SHOTS).map((raw) => {
    completeRecord(raw, "谱系镜头", ["id", "title", "order", "canonicalEntryId", "canonicalTaskId", "canonicalArtifactId", "createdAt", "updatedAt"]);
    const createdAt = persistedTimestamp(raw.createdAt, "谱系镜头创建时间");
    const updatedAt = persistedTimestamp(raw.updatedAt, "谱系镜头更新时间");
    if (Date.parse(updatedAt) < Date.parse(createdAt)) throw new TypeError("谱系镜头更新时间无效。");
    return {
      id: id(raw.id, "镜头标识"), title: text(raw.title, "镜头标题", 200), order: integer(raw.order, "镜头顺序"),
      canonicalEntryId: id(raw.canonicalEntryId, "主线方向", true),
      canonicalTaskId: id(raw.canonicalTaskId, "主线任务", true),
      canonicalArtifactId: id(raw.canonicalArtifactId, "主线产物", true), createdAt, updatedAt,
    };
  });
  const shotById = new Map(shots.map((shot) => [shot.id, shot]));
  if (shotById.size !== shots.length) throw new TypeError("镜头标识重复。");
  if (shots.map((shot) => shot.order).sort((a, b) => a - b).some((order, index) => order !== index)) {
    throw new TypeError("镜头顺序无效。");
  }
  const entries = list(value.entries, "工作台记录", MAX_ENTRIES).map((raw) => {
    completeRecord(raw, "工作台记录", ["id", "kind", "title", "order", "parentId", "relation", "x", "y",
      "draft", "taskIds", "selectedTaskId", "selectedArtifactId", "shotId", "sourceTaskId", "sourceArtifactId", "branchKind", "branchReason",
      "sourceDraft", "archived", "createdAt", "updatedAt"]);
    const taskIds = list(raw.taskIds, "镜头版本", MAX_TAKES).map((taskId) => id(taskId, "任务标识"));
    if (new Set(taskIds).size !== taskIds.length) throw new TypeError("镜头版本包含重复任务。");
    const selectedTaskId = id(raw.selectedTaskId, "选中任务", true);
    if (selectedTaskId && !taskIds.includes(selectedTaskId)) throw new TypeError("选中任务不属于当前工作项。");
    const selectedArtifactId = id(raw.selectedArtifactId, "选中产物", true);
    if (Boolean(selectedTaskId) !== Boolean(selectedArtifactId)) throw new TypeError("选中 Take 必须同时绑定任务和产物。");
    if (!LINEAGE_RELATIONS.includes(raw.relation)) throw new TypeError("节点关系无效。");
    const createdAt = persistedTimestamp(raw.createdAt, "工作台创建时间");
    const updatedAt = persistedTimestamp(raw.updatedAt, "工作台更新时间");
    if (Date.parse(updatedAt) < Date.parse(createdAt)) throw new TypeError("工作台更新时间无效。");
    return {
      id: id(raw.id, "工作项标识"), kind: kind(raw.kind), title: text(raw.title, "工作项标题", 200),
      order: integer(raw.order, "工作项顺序"), parentId: id(raw.parentId, "父节点标识", true),
      relation: raw.relation, x: coordinate(raw.x), y: coordinate(raw.y), draft: sanitizeCreationDraft(raw.draft),
      taskIds, selectedTaskId, selectedArtifactId, shotId: id(raw.shotId, "镜头标识", true),
      sourceTaskId: id(raw.sourceTaskId, "来源任务", true),
      sourceArtifactId: id(raw.sourceArtifactId, "来源产物", true),
      branchKind: lineageBranchKind(raw.branchKind, true), branchReason: text(raw.branchReason, "分支意图", 500),
      sourceDraft: raw.sourceDraft === null ? null : sanitizeCreationDraft(raw.sourceDraft),
      archived: boolean(raw.archived, "归档状态"), createdAt, updatedAt,
    };
  });
  const byId = new Map(entries.map((entry) => [entry.id, entry]));
  if (byId.size !== entries.length) throw new TypeError("工作项标识重复。");
  const taskOwners = new Map();
  for (const entry of entries) {
    for (const taskId of entry.taskIds) {
      const owner = taskOwners.get(taskId);
      if (owner && owner !== entry.id) throw new TypeError("同一任务不能绑定到多个工作项。");
      taskOwners.set(taskId, entry.id);
    }
  }
  for (const entry of entries) {
    if (entry.kind !== "canvas") {
      if (entry.parentId || entry.shotId || entry.sourceTaskId || entry.sourceArtifactId || entry.branchKind || entry.branchReason || entry.sourceDraft !== null || entry.archived) {
        throw new TypeError("非谱系工作项包含谱系字段。");
      }
      if (!["", "import"].includes(entry.relation)) throw new TypeError("工作台来源无效。");
      continue;
    }
    if (!shotById.has(entry.shotId)) throw new TypeError("谱系方向缺少有效镜头。");
    const parent = entry.parentId ? byId.get(entry.parentId) : null;
    if (entry.parentId) {
      if (!parent || parent.kind !== "canvas" || parent.shotId !== entry.shotId || !["settings", "lineage"].includes(entry.relation)) {
        throw new TypeError("谱系父方向无效。");
      }
      const visited = new Set([entry.id]);
      let ancestor = parent;
      while (ancestor) {
        if (visited.has(ancestor.id)) throw new TypeError("谱系关系不能形成循环。");
        visited.add(ancestor.id);
        ancestor = byId.get(ancestor.parentId);
      }
      if (entry.relation === "lineage") {
        if (!entry.sourceTaskId || !entry.sourceArtifactId || !parent.taskIds.includes(entry.sourceTaskId)
          || !entry.branchKind || !entry.branchReason.trim() || !entry.sourceDraft) {
          throw new TypeError("谱系分支必须绑定精确来源 Take 和修改意图。");
        }
      } else if (entry.sourceTaskId || entry.sourceArtifactId || entry.branchKind || entry.branchReason || entry.sourceDraft !== null) {
        throw new TypeError("旧设置分支不能伪装成谱系关系。");
      }
    } else {
      if (!["", "import"].includes(entry.relation) || entry.sourceTaskId || entry.sourceArtifactId || entry.branchKind || entry.branchReason || entry.sourceDraft !== null) {
        throw new TypeError("镜头初始方向包含无效来源。");
      }
      if (entry.archived) throw new TypeError("镜头的初始方向不能归档。");
    }
    if (!entry.archived) {
      let ancestor = entry.parentId ? byId.get(entry.parentId) : null;
      while (ancestor) {
        if (ancestor.archived) throw new TypeError("已归档方向的子方向不能单独恢复。");
        ancestor = ancestor.parentId ? byId.get(ancestor.parentId) : null;
      }
    }
  }
  for (const shot of shots) {
    const directions = entries.filter((entry) => entry.kind === "canvas" && entry.shotId === shot.id);
    if (!directions.length) throw new TypeError("谱系镜头没有创作方向。");
    if (directions.filter((entry) => !entry.parentId).length !== 1) {
      throw new TypeError("每个谱系镜头必须且只能有一个初始方向。");
    }
    if (new Set([Boolean(shot.canonicalEntryId), Boolean(shot.canonicalTaskId), Boolean(shot.canonicalArtifactId)]).size !== 1) {
      throw new TypeError("导演主线必须绑定方向、Take 和产物。");
    }
    if (shot.canonicalEntryId) {
      const direction = byId.get(shot.canonicalEntryId);
      if (direction?.shotId !== shot.id || !direction.taskIds.includes(shot.canonicalTaskId) || direction.archived) {
        throw new TypeError("导演主线不属于当前镜头。");
      }
    }
  }
  const active = Object.fromEntries(KINDS.map((type) => {
    const selected = id(value.active[type], "当前工作项", true);
    if (selected && byId.get(selected)?.kind !== type) throw new TypeError("当前工作项与工作台不一致。");
    const orders = entries.filter((entry) => entry.kind === type).map((entry) => entry.order).sort((a, b) => a - b);
    if (orders.some((order, index) => order !== index)) throw new TypeError("工作项顺序无效。");
    return [type, selected];
  }));
  const activeShotId = id(rawLineage.activeShotId, "当前镜头", true);
  if (activeShotId && !shotById.has(activeShotId)) throw new TypeError("当前谱系镜头无效。");
  if (shots.length && !activeShotId) throw new TypeError("缺少当前谱系镜头。");
  if (!shots.length && activeShotId) throw new TypeError("空谱系不能选择镜头。");
  if (shots.length && !active.canvas) throw new TypeError("当前谱系镜头缺少当前方向。");
  if (active.canvas && (byId.get(active.canvas)?.shotId !== activeShotId || byId.get(active.canvas)?.archived)) {
    throw new TypeError("当前方向与当前镜头不一致。");
  }
  return {
    version: 2, scopeKey, revision,
    quick: { draft: sanitizeCreationDraft(quick.draft), panel: quick.panel, mobileOpen: quick.mobileOpen },
    active, lineage: { activeShotId, shots }, entries,
  };
}

function decodeWorkspace(serialized, scopeKey) {
  if (typeof serialized !== "string" || !serialized || serialized.length > MAX_SERIALIZED_LENGTH) {
    throw new TypeError("本机草稿内容无效。");
  }
  const value = JSON.parse(serialized);
  const result = normalizeWorkspace(value, scopeKey);
  // Persisted bytes must already be the minimal schema written by this module.
  // A legacy/raw task snapshot or injected URL must not masquerade as a draft.
  const persistedDrafts = [value.quick.draft, ...value.entries.map((entry) => entry.draft)];
  if (value.version === 2) persistedDrafts.push(...value.entries.map((entry) => entry.sourceDraft).filter(Boolean));
  for (const draft of persistedDrafts) {
    completeRecord(draft, "已保存草稿", DRAFT_KEYS);
    completeRecord(draft.files, "已保存素材", MEDIA_TYPES);
    for (const mediaType of MEDIA_TYPES) {
      for (const asset of draft.files[mediaType]) {
        completeRecord(asset, "已保存素材", ["asset_id", "media_type", "original_filename", "status"]);
      }
    }
  }
  return result;
}

function resolveStorage(storage) {
  // Accessing localStorage itself can throw in restricted/private environments.
  const result = storage === undefined ? globalThis.localStorage : storage;
  if (!result || typeof result.getItem !== "function" || typeof result.setItem !== "function") {
    throw new TypeError("浏览器未提供可用的本机存储。");
  }
  return result;
}

function storageBaseline(storage) {
  if (!STORAGE_BASELINES.has(storage)) STORAGE_BASELINES.set(storage, new Map());
  return STORAGE_BASELINES.get(storage);
}

/** A failed read never deletes or repairs the original stored bytes. */
export function readCreationWorkspace(scopeKey, storage) {
  const state = emptyCreationWorkspace(scopeKey);
  let serialized;
  try {
    const target = resolveStorage(storage);
    const key = `${STORAGE_PREFIX}${encodeURIComponent(state.scopeKey)}`;
    serialized = target.getItem(key);
    storageBaseline(target).set(key, serialized);
  } catch {
    return { state, notice: "无法读取本机草稿；当前修改尚未保存到本机。" };
  }
  if (serialized === null) return { state, notice: "" };
  try {
    return { state: decodeWorkspace(serialized, state.scopeKey), notice: "" };
  } catch {
    return { state, notice: "本机草稿无法读取，原记录未改动；请勿覆盖，先保留当前编辑内容。" };
  }
}

/** Fail visibly on quota, corruption or a newer tab; never prune old records. */
export function writeCreationWorkspace(state, storage) {
  let normalized;
  let serialized;
  try {
    normalized = normalizeWorkspace(state);
    serialized = JSON.stringify(normalized);
    if (serialized.length > MAX_SERIALIZED_LENGTH) throw new RangeError("本机草稿过大。");
  } catch (error) {
    return { ok: false, notice: error instanceof RangeError ? error.message : "草稿内容无效，此次修改未保存。" };
  }
  try {
    const target = resolveStorage(storage);
    const key = `${STORAGE_PREFIX}${encodeURIComponent(normalized.scopeKey)}`;
    const existing = target.getItem(key);
    const baseline = storageBaseline(target);
    // Best-effort multi-tab protection, not cross-device/transactional storage.
    // A stale tab cannot increment its way past a conflict and overwrite bytes
    // it has not explicitly reloaded since another tab changed them.
    if (baseline.has(key) && baseline.get(key) !== existing) {
      return { ok: false, notice: "另一页面已更新本机草稿，此次修改未覆盖原记录。" };
    }
    if (existing !== null) {
      let previous;
      try {
        previous = decodeWorkspace(existing, normalized.scopeKey);
      } catch {
        return { ok: false, notice: "本机已有无法读取的草稿，未覆盖原记录。" };
      }
      const previousContent = JSON.stringify(previous);
      if (previous.revision > normalized.revision
        || (previous.revision === normalized.revision && previousContent !== serialized)) {
        return { ok: false, notice: "另一页面已更新本机草稿，此次修改未覆盖原记录。" };
      }
      if (previousContent === serialized) {
        baseline.set(key, existing);
        return { ok: true, notice: "" };
      }
    }
    target.setItem(key, serialized);
    if (target.getItem(key) !== serialized) {
      return { ok: false, notice: "无法确认本机草稿已保存，请保留当前编辑内容。" };
    }
    baseline.set(key, serialized);
    return { ok: true, notice: "" };
  } catch {
    return { ok: false, notice: "本机保存失败，可能是空间不足或浏览器限制；请勿关闭当前页面。" };
  }
}

export function createWorkbenchEntry(state, entryKind, options = {}) {
  const base = normalizeWorkspace(state);
  const type = kind(entryKind);
  record(options, "新建工作项", ["draft", "parentId", "relation", "title", "id", "now", "shotTitle",
    "sourceTaskId", "sourceArtifactId", "branchKind", "branchReason", "sourceDraft"]);
  if (base.entries.length >= MAX_ENTRIES) throw new RangeError(`工作台最多保留 ${MAX_ENTRIES} 条记录，未删除旧记录。`);
  const entryId = id(options.id === undefined
    ? `wb-${globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`}`
    : options.id, "工作项标识");
  if (base.entries.some((entry) => entry.id === entryId)) throw new TypeError("工作项标识已存在。");
  const order = base.entries.filter((entry) => entry.kind === type).length;
  const time = timestamp(options.now);
  const parentId = options.parentId === undefined ? "" : options.parentId;
  const parent = parentId ? base.entries.find((entry) => entry.id === parentId) : null;
  let lineage = base.lineage;
  let shotId = "";
  if (type === "canvas") {
    if (parent) {
      shotId = parent.shotId;
    } else {
      if (base.lineage.shots.length >= MAX_SHOTS) throw new RangeError(`谱系图谱最多保留 ${MAX_SHOTS} 个镜头，未删除旧镜头。`);
      shotId = id(`lineage-shot-${globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`}`, "镜头标识");
      const shot = {
        id: shotId,
        title: text(options.shotTitle, "镜头标题", 200, `镜头 ${base.lineage.shots.length + 1}`),
        order: base.lineage.shots.length,
        canonicalEntryId: "",
        canonicalTaskId: "",
        canonicalArtifactId: "",
        createdAt: time,
        updatedAt: time,
      };
      lineage = { activeShotId: shotId, shots: [...base.lineage.shots, shot] };
    }
  }
  const relation = options.relation === undefined ? "" : options.relation;
  const entry = {
    id: entryId, kind: type,
    title: text(options.title, "工作项标题", 200, type === "notebook" ? `场次 ${order + 1}` : type === "canvas" ? (parent ? `方向 ${base.entries.filter((item) => item.kind === "canvas" && item.shotId === shotId).length + 1}` : "初始方向") : `镜头 ${order + 1}`),
    order, parentId,
    relation, x: 0, y: 0,
    // Creating a new context never implicitly copies quick or parent drafts.
    draft: sanitizeCreationDraft(options.draft), taskIds: [], selectedTaskId: "", selectedArtifactId: "",
    shotId,
    sourceTaskId: options.sourceTaskId === undefined ? "" : options.sourceTaskId,
    sourceArtifactId: options.sourceArtifactId === undefined ? "" : options.sourceArtifactId,
    branchKind: options.branchKind === undefined ? "" : options.branchKind,
    branchReason: options.branchReason === undefined ? "" : options.branchReason,
    sourceDraft: options.sourceDraft == null ? null : sanitizeCreationDraft(options.sourceDraft),
    archived: false,
    createdAt: time, updatedAt: time,
  };
  const next = normalizeWorkspace({
    ...base, revision: nextRevision(base), active: { ...base.active, [type]: entryId }, lineage: type === "canvas"
      ? { ...lineage, activeShotId: shotId }
      : lineage,
    entries: [...base.entries, entry],
  });
  return {
    state: next,
    entry: next.entries.find((candidate) => candidate.id === entryId),
    shot: type === "canvas" ? next.lineage.shots.find((candidate) => candidate.id === shotId) : null,
  };
}

export function updateWorkbenchEntry(state, entryId, patch) {
  const base = normalizeWorkspace(state);
  id(entryId, "工作项标识");
  record(patch, "工作项修改", ["title", "draft", "x", "y", "selectedTaskId", "selectedArtifactId", "updatedAt"]);
  const entry = base.entries.find((candidate) => candidate.id === entryId);
  if (!entry) throw new TypeError("未找到要修改的工作项。");
  if (own(patch, "draft")) record(patch.draft, "创作草稿修改");
  let draft = entry.draft;
  if (own(patch, "draft")) {
    const merged = { ...entry.draft, ...patch.draft };
    if (own(patch.draft, "files")) {
      record(patch.draft.files, "参考素材修改", MEDIA_TYPES);
      merged.files = { ...entry.draft.files, ...patch.draft.files };
    }
    draft = sanitizeCreationDraft(merged);
  }
  const next = {
    ...entry, ...patch,
    draft,
  };
  if (JSON.stringify(next) === JSON.stringify(entry)) return state;
  next.updatedAt = nextUpdatedAt(entry, patch.updatedAt);
  return normalizeWorkspace({
    ...base, revision: nextRevision(base),
    entries: base.entries.map((candidate) => candidate.id === entryId ? next : candidate),
  });
}

/** Retry/poll completion may attach the same real task repeatedly, without a new take. */
export function attachWorkbenchTask(state, entryId, taskId) {
  const base = normalizeWorkspace(state);
  id(entryId, "工作项标识");
  id(taskId, "任务标识");
  const entry = base.entries.find((candidate) => candidate.id === entryId);
  if (!entry) throw new TypeError("未找到任务所属工作项。");
  if (entry.taskIds.includes(taskId)) return state;
  if (base.entries.some((candidate) => candidate.id !== entryId && candidate.taskIds.includes(taskId))) {
    throw new TypeError("同一任务不能绑定到多个工作项。");
  }
  if (entry.taskIds.length >= MAX_TAKES) throw new RangeError(`每个工作项最多保留 ${MAX_TAKES} 个版本，未移除旧任务。`);
  return {
    ...base, revision: nextRevision(base),
    entries: base.entries.map((candidate) => candidate.id === entryId ? {
      ...candidate, taskIds: [...candidate.taskIds, taskId],
      updatedAt: nextUpdatedAt(candidate),
    } : candidate),
  };
}

export function selectWorkbenchEntry(state, entryKind, entryId) {
  const base = normalizeWorkspace(state);
  const type = kind(entryKind);
  id(entryId, "工作项标识");
  const entry = base.entries.find((candidate) => candidate.id === entryId && candidate.kind === type);
  if (!entry) {
    throw new TypeError("工作项不属于当前工作台。");
  }
  if (base.active[type] === entryId && (type !== "canvas" || base.lineage.activeShotId === entry.shotId)) return state;
  return normalizeWorkspace({
    ...base,
    revision: nextRevision(base),
    active: { ...base.active, [type]: entryId },
    lineage: type === "canvas" ? { ...base.lineage, activeShotId: entry.shotId } : base.lineage,
  });
}

export function selectLineageShot(state, shotId) {
  const base = normalizeWorkspace(state);
  id(shotId, "镜头标识");
  const shot = base.lineage.shots.find((candidate) => candidate.id === shotId);
  if (!shot) throw new TypeError("未找到谱系镜头。");
  const current = base.entries.find((entry) => entry.id === base.active.canvas && entry.shotId === shotId && !entry.archived);
  const nextEntry = current
    || base.entries.find((entry) => entry.id === shot.canonicalEntryId)
    || base.entries.find((entry) => entry.kind === "canvas" && entry.shotId === shotId && !entry.archived);
  if (!nextEntry) throw new TypeError("谱系镜头没有可用方向。");
  if (base.lineage.activeShotId === shotId && base.active.canvas === nextEntry.id) return state;
  return normalizeWorkspace({
    ...base,
    revision: nextRevision(base),
    active: { ...base.active, canvas: nextEntry.id },
    lineage: { ...base.lineage, activeShotId: shotId },
  });
}

export function renameLineageShot(state, shotId, title, now) {
  const base = normalizeWorkspace(state);
  id(shotId, "镜头标识");
  const nextTitle = text(title, "镜头标题", 200);
  if (!nextTitle.trim()) throw new TypeError("镜头标题不能为空。");
  const shot = base.lineage.shots.find((candidate) => candidate.id === shotId);
  if (!shot) throw new TypeError("未找到谱系镜头。");
  if (shot.title === nextTitle) return state;
  return normalizeWorkspace({
    ...base,
    revision: nextRevision(base),
    lineage: {
      ...base.lineage,
      shots: base.lineage.shots.map((candidate) => candidate.id === shotId
        ? { ...candidate, title: nextTitle, updatedAt: nextUpdatedAt(candidate, now) }
        : candidate),
    },
  });
}

export function setLineageCanonicalTake(state, entryId, taskId, artifactId, now) {
  const base = normalizeWorkspace(state);
  id(entryId, "工作项标识");
  id(taskId, "任务标识");
  id(artifactId, "产物标识");
  const entry = base.entries.find((candidate) => candidate.id === entryId && candidate.kind === "canvas");
  if (!entry || entry.archived || !entry.taskIds.includes(taskId)) throw new TypeError("只能从当前镜头的可用 Take 设定导演主线。");
  const shot = base.lineage.shots.find((candidate) => candidate.id === entry.shotId);
  if (!shot) throw new TypeError("未找到方向所属镜头。");
  if (shot.canonicalEntryId === entryId && shot.canonicalTaskId === taskId && shot.canonicalArtifactId === artifactId) return state;
  const updatedAt = nextUpdatedAt(shot, now);
  return normalizeWorkspace({
    ...base,
    revision: nextRevision(base),
    entries: base.entries.map((candidate) => candidate.id === entryId
      ? { ...candidate, selectedTaskId: taskId, selectedArtifactId: artifactId, updatedAt: nextUpdatedAt(candidate, now) }
      : candidate),
    lineage: {
      ...base.lineage,
      shots: base.lineage.shots.map((candidate) => candidate.id === shot.id
        ? { ...candidate, canonicalEntryId: entryId, canonicalTaskId: taskId, canonicalArtifactId: artifactId, updatedAt }
        : candidate),
    },
  });
}

export function setLineageBranchArchived(state, entryId, archived, now) {
  const base = normalizeWorkspace(state);
  id(entryId, "工作项标识");
  boolean(archived, "归档状态");
  const entry = base.entries.find((candidate) => candidate.id === entryId && candidate.kind === "canvas");
  if (!entry) throw new TypeError("未找到要归档的创作方向。");
  if (!entry.parentId) throw new TypeError("镜头的初始方向不能归档。");
  const affected = new Set([entry.id]);
  if (!archived) {
    // A visible archived descendant must be directly recoverable. Restore its
    // ancestor chain as well so the normalized document never exposes a live
    // child beneath an archived parent.
    let ancestor = entry.parentId ? base.entries.find((candidate) => candidate.id === entry.parentId) : null;
    while (ancestor) {
      affected.add(ancestor.id);
      ancestor = ancestor.parentId
        ? base.entries.find((candidate) => candidate.id === ancestor.parentId)
        : null;
    }
  }
  let changed = true;
  while (changed) {
    changed = false;
    for (const candidate of base.entries) {
      if (candidate.kind === "canvas" && affected.has(candidate.parentId) && !affected.has(candidate.id)) {
        affected.add(candidate.id);
        changed = true;
      }
    }
  }
  const shot = base.lineage.shots.find((candidate) => candidate.id === entry.shotId);
  if (archived && shot?.canonicalEntryId && affected.has(shot.canonicalEntryId)) {
    throw new TypeError("导演主线所在支线不能归档，请先选择另一条主线。");
  }
  if ([...affected].every((idValue) => base.entries.find((candidate) => candidate.id === idValue)?.archived === archived)) return state;
  let activeCanvas = base.active.canvas;
  if (archived && affected.has(activeCanvas)) activeCanvas = entry.parentId;
  return normalizeWorkspace({
    ...base,
    revision: nextRevision(base),
    active: { ...base.active, canvas: activeCanvas },
    entries: base.entries.map((candidate) => affected.has(candidate.id)
      ? { ...candidate, archived, updatedAt: nextUpdatedAt(candidate, now) }
      : candidate),
    lineage: {
      ...base.lineage,
      shots: base.lineage.shots.map((candidate) => candidate.id === entry.shotId
        ? { ...candidate, updatedAt: nextUpdatedAt(candidate, now) }
        : candidate),
    },
  });
}

export function moveWorkbenchEntry(state, entryId, direction) {
  const base = normalizeWorkspace(state);
  id(entryId, "工作项标识");
  if (direction !== -1 && direction !== 1) throw new TypeError("移动方向必须为 -1 或 1。");
  const entry = base.entries.find((candidate) => candidate.id === entryId);
  if (!entry) throw new TypeError("未找到要移动的工作项。");
  const ordered = base.entries.filter((candidate) => candidate.kind === entry.kind).sort((a, b) => a.order - b.order);
  const destination = entry.order + direction;
  if (destination < 0 || destination >= ordered.length) return state;
  [ordered[entry.order], ordered[destination]] = [ordered[destination], ordered[entry.order]];
  let index = 0;
  return {
    ...base, revision: nextRevision(base),
    entries: base.entries.map((candidate) => candidate.kind === entry.kind
      ? { ...ordered[index], order: index++ } : candidate),
  };
}
