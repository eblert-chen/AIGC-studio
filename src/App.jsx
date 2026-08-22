import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  ArrowCounterClockwise,
  Bell,
  CaretDown,
  Check,
  CheckCircle,
  CircleNotch,
  ClockCounterClockwise,
  FilmSlate,
  FolderOpen,
  Gear,
  House,
  ImageSquare,
  Images,
  MagnifyingGlass,
  MusicNote,
  PaperPlaneTilt,
  Play,
  Plus,
  SlidersHorizontal,
  Sparkle,
  SpinnerGap,
  UploadSimple,
  UserCircle,
  VideoCamera,
  WarningCircle,
  X,
} from "@phosphor-icons/react";
import {
  createPlatformClient,
  parseArtifactDownloadUrl,
  PlatformApiError,
  readRuntimePlatformConfig,
} from "./api/platformClient.js";
import { ManagementConsole } from "./ManagementConsole.jsx";
import { CommunityHome } from "./CommunityHome.jsx";
import { CreationHub } from "./CreationHub.jsx";
import { PublishingCenter } from "./PublishingCenter.jsx";
import { AccountCenter } from "./AccountCenter.jsx";
import { ArtworksView } from "./pages/studio/ArtworksView.jsx";
import { HistoryView } from "./pages/studio/HistoryView.jsx";
import { WorkspaceCapabilityUnavailableView } from "./pages/studio/StudioStatusViews.jsx";
import { ResultDetailView } from "./pages/studio/ResultDetailView.jsx";
import { formatBytes, shortId } from "./components/studio/studioPresentation.js";
import {
  IconButton,
  Preview,
  SceneTimeline,
} from "./components/studio/StudioWorkspaceViews.jsx";
import { DemoAccountSwitcher } from "./DemoAccountSwitcher.jsx";
import { SkinSwitcher, useSkinPreference } from "./SkinSwitcher.jsx";
import { BrandLogo, BRAND_NAME } from "./BrandLogo.jsx";
import {
  isExplicitDevelopmentDemo,
  readBuildPlatformConfig,
} from "./runtimeMode.js";
import { useAuth } from "./auth/AuthGateway.jsx";
import {
  allowedSurfacesForIdentity,
  defaultSurfaceForIdentity,
  identityRoleLabel,
  resolveSurfaceForIdentity,
} from "./identitySurfaces.js";
import { demoPersona as resolveDemoPersona } from "./demoIdentitySurfaces.js";
import {
  normalizeSessionSurfaces,
  personalCapability,
  personalIdentityFromSession,
  preferredCompanyId,
} from "./personalWorkspace.js";
import {
  buildCapabilityRequestPayload,
  capabilityControlVisibility,
  capabilityForMode,
  firstSupportedMode,
  modeLabel,
  reconcileGenerationDraft,
  resolveEffectiveCapabilities,
} from "./modelCapabilities.js";
import {
  deriveArtworksFromTasks,
  normalizePage,
} from "./taskArtifacts.js";
import {
  isTaskAttentionRequired,
  resolveTaskStatus,
} from "./taskStatus.js";
import {
  readStudioPreferences,
  writeStudioPreferences,
} from "./studioPreferences.js";
import {
  createPreviewLease,
  nextPreviewCleanupDelay,
  removeExpiredPreviewLeases,
  removePreviewLease,
} from "./previewLeases.js";
import {
  appRouteFromPath,
  surfacePath,
} from "./studioNavigation.js";

const DEVELOPMENT_DEMO_ENABLED = import.meta.env.PROD
  ? false
  : isExplicitDevelopmentDemo(import.meta.env);

const DEMO_MODEL_RESPONSES = DEVELOPMENT_DEMO_ENABLED ? [
  {
    id: "cinemox",
    slug: "cinemox-v2",
    display_name: "CinemoX Pro 2.1",
    pricing_mode: "per_second",
    unit_price_cents: 300,
    rate: 3,
    effective_capabilities: {
      schema_version: 1,
      modes: {
        text_to_video: {
          input_media_types: ["image", "video", "audio"],
          supports_face: true,
          required_resource_keys: ["face.library"],
          limits: {
            max_prompt_length: 1000,
            max_images: 9,
            max_videos: 3,
            max_audio: 3,
            duration_seconds: [10, 15, 20],
            aspect_ratios: ["16:9", "9:16", "1:1"],
            resolutions: ["720p", "1080p"],
            output_counts: [1, 2, 3, 4],
          },
        },
        image_to_video: {
          input_media_types: ["image", "video", "audio"],
          supports_face: true,
          required_resource_keys: ["face.library"],
          limits: {
            max_prompt_length: 1000,
            max_images: 9,
            max_videos: 3,
            max_audio: 3,
            duration_seconds: [10, 15, 20],
            aspect_ratios: ["16:9", "9:16", "1:1"],
            resolutions: ["720p", "1080p"],
            output_counts: [1, 2, 3, 4],
          },
        },
        video_to_video: {
          input_media_types: ["image", "video", "audio"],
          supports_face: true,
          required_resource_keys: ["face.library"],
          limits: {
            max_prompt_length: 1000,
            max_images: 9,
            max_videos: 3,
            max_audio: 3,
            duration_seconds: [10, 15, 20],
            aspect_ratios: ["16:9", "9:16", "1:1"],
            resolutions: ["720p", "1080p"],
            output_counts: [1, 2, 3, 4],
          },
        },
      },
    },
  },
  {
    id: "rush",
    slug: "rush-video-1.6",
    display_name: "Rush Video 1.6",
    pricing_mode: "per_item",
    unit_price_cents: 200,
    rate: 2,
    effective_capabilities: {
      schema_version: 1,
      modes: {
        text_to_video: {
          input_media_types: ["image", "video", "audio"],
          supports_face: false,
          required_resource_keys: [],
          limits: {
            max_prompt_length: 500,
            max_images: 4,
            max_videos: 3,
            max_audio: 3,
            duration_seconds: [5, 10],
            aspect_ratios: ["16:9", "9:16"],
            resolutions: ["720p"],
            output_counts: [1, 2],
          },
        },
        image_to_video: {
          input_media_types: ["image", "video", "audio"],
          supports_face: false,
          required_resource_keys: [],
          limits: {
            max_prompt_length: 500,
            max_images: 4,
            max_videos: 3,
            max_audio: 3,
            duration_seconds: [5, 10],
            aspect_ratios: ["16:9", "9:16"],
            resolutions: ["720p"],
            output_counts: [1, 2],
          },
        },
      },
    },
  },
  {
    id: "frameflow",
    slug: "frameflow-lite",
    display_name: "FrameFlow Lite",
    pricing_mode: "per_item",
    unit_price_cents: 100,
    rate: 1,
    effective_capabilities: {
      schema_version: 1,
      modes: {
        text_to_image: {
          input_media_types: ["image"],
          supports_face: false,
          required_resource_keys: [],
          limits: {
            max_prompt_length: 500,
            max_images: 1,
            max_videos: 0,
            max_audio: 0,
            duration_seconds: [5],
            aspect_ratios: ["16:9", "1:1"],
            resolutions: ["720p", "1080p"],
            output_counts: [1, 2, 3, 4],
          },
        },
      },
    },
  },
] : [];

const buildPlatformConfig = readBuildPlatformConfig(import.meta.env);
const runtimePlatformConfig = readRuntimePlatformConfig(
  globalThis,
  buildPlatformConfig,
);
const DEMO_MODE = DEVELOPMENT_DEMO_ENABLED;
let platformClientConfigurationError = "";
let liveClient = null;
try {
  liveClient = createPlatformClient({
    ...runtimePlatformConfig,
  });
} catch (error) {
  platformClientConfigurationError = readableApiError(error);
}
const API_CONFIGURED = Boolean(runtimePlatformConfig.baseUrl) && !platformClientConfigurationError;
const LIVE_MODE = !DEMO_MODE && API_CONFIGURED;
const CONFIG_REQUIRED = !DEMO_MODE && !API_CONFIGURED;

const EMPTY_MODEL = {
  id: "",
  name: "暂无可用模型",
  tier: "未授权",
  effectiveCapabilities: { schemaVersion: 1, modes: {} },
  defaultMode: "",
  capabilityVersion: null,
  quoteRevision: null,
  pricingMode: "per_item",
  unitPriceCents: 0,
  rate: 0,
};

function normalizeLiveModel(source, { requireEffective = false } = {}) {
  const effectiveCapabilities = resolveEffectiveCapabilities(
    requireEffective
      ? { effective_capabilities: source?.effective_capabilities }
      : source,
  );
  const defaultMode = firstSupportedMode(effectiveCapabilities);
  const pricingMode = source.pricing_mode || source.billing_mode || "per_item";

  return {
    id: source.id,
    slug: source.slug,
    name: source.display_name || source.slug || source.id,
    tier: pricingMode === "per_second" ? "按秒计费" : "按条计费",
    effectiveCapabilities,
    defaultMode,
    capabilityVersion: Number.isInteger(Number(source.capability_version))
      ? Number(source.capability_version)
      : null,
    quoteRevision:
      typeof source.quote_revision === "string" ? source.quote_revision : null,
    pricingMode,
    unitPriceCents: Number(source.unit_price_cents) || 0,
    unitPricePoints: Number(source.unit_price_points) || 0,
    rate: Number(source.rate ?? source.unit_price_cents) || 0,
  };
}

const DEMO_MODELS = DEMO_MODEL_RESPONSES.map((source) => normalizeLiveModel(source));
const DEMO_INITIAL_MODE = DEMO_MODE ? DEMO_MODELS[0].defaultMode : "";
const DEMO_INITIAL_CAPABILITY = DEMO_MODE
  ? capabilityForMode(DEMO_MODELS[0].effectiveCapabilities, DEMO_INITIAL_MODE)
  : null;

function mapTaskStage(status) {
  return resolveTaskStatus(status).stage;
}

function progressForTask(status) {
  return resolveTaskStatus(status).progress;
}

function readableApiError(error) {
  if (
    error instanceof PlatformApiError &&
    ["insufficient_points", "INSUFFICIENT_POINTS"].includes(error.code)
  ) {
    return "个人可用积分不足，请充值后再试。";
  }
  if (
    error instanceof PlatformApiError &&
    ["insufficient_balance", "INSUFFICIENT_BALANCE"].includes(error.code)
  ) {
    return "公司可用余额不足，请联系管理员充值后再试。";
  }
  return error?.message || "客户平台请求失败，请稍后重试。";
}

function missingCollectionEndpoint(error) {
  return error instanceof PlatformApiError && [404, 405].includes(error.status);
}

async function fetchTaskHistoryPage(client, filters, { signal } = {}) {
  try {
    const response = await client.listTaskHistory(filters, { signal });
    return normalizePage(response, {
      page: filters.page,
      pageSize: filters.page_size,
    });
  } catch (error) {
    if (!missingCollectionEndpoint(error)) throw error;
    const tasks = await client.listTasks({ signal });
    const filtered = (Array.isArray(tasks) ? tasks : []).filter(
      (task) => !filters.status || task.status === filters.status,
    );
    return normalizePage(filtered, {
      page: 1,
      pageSize: filters.page_size,
    });
  }
}

async function fetchArtworkPage(client, filters, { signal } = {}) {
  try {
    const response = await client.listArtworks(filters, { signal });
    return normalizePage(response, {
      page: filters.page,
      pageSize: filters.page_size,
    });
  } catch (error) {
    if (!missingCollectionEndpoint(error)) throw error;
    const tasks = await client.listTasks({ signal });
    const derived = deriveArtworksFromTasks(Array.isArray(tasks) ? tasks : []);
    const filtered = derived.filter((artwork) => {
      if (filters.media_type && artwork.media_type !== filters.media_type) return false;
      if (filters.downloaded !== undefined && filters.downloaded !== "") {
        return false;
      }
      return true;
    });
    const page = Number(filters.page) || 1;
    const pageSize = Number(filters.page_size) || 24;
    const start = (page - 1) * pageSize;
    return {
      page,
      page_size: pageSize,
      total: filtered.length,
      items: filtered.slice(start, start + pageSize),
      legacy: true,
    };
  }
}

function inputAssetId(asset) {
  return String(asset?.id ?? asset?.asset_id ?? "").trim();
}

function inputAssetName(asset) {
  return (
    asset?.original_filename ||
    asset?.name ||
    (inputAssetId(asset) ? `素材 ${inputAssetId(asset).slice(0, 8)}` : "未命名素材")
  );
}

function inputAssetType(asset, fallback = "") {
  return String(asset?.media_type ?? fallback).trim();
}

function filesFromPendingRequest(requestPayload) {
  const grouped = { image: [], video: [], audio: [] };
  for (const reference of requestPayload?.assets ?? []) {
    const kind = inputAssetType(reference);
    if (!grouped[kind]) continue;
    grouped[kind].push({
      id: inputAssetId(reference),
      asset_id: inputAssetId(reference),
      media_type: kind,
      original_filename: `已上传${kind === "image" ? "图片" : kind === "video" ? "视频" : "音频"}`,
      status: "active",
    });
  }
  return grouped;
}

function artworkAsTask(artwork) {
  if (!artwork) return null;
  return {
    id: artwork.task_id,
    task_id: artwork.task_id,
    status: "succeeded",
    company_id: artwork.company_id,
    workspace_id: artwork.workspace_id,
    user_id: artwork.created_by_user_id,
    user_display_name: artwork.created_by_display_name,
    model_id: artwork.model_id,
    model_display_name: artwork.model_display_name,
    request_payload: artwork.request_payload,
    actual_cost_cents: artwork.actual_cost_cents,
    actual_cost_points: artwork.actual_cost_points,
    output_artifacts: [],
    created_at: artwork.created_at,
  };
}

function generationModeLabel(mode) {
  return modeLabel(mode);
}

function makeIdempotencyKey() {
  return (
    globalThis.crypto?.randomUUID?.() ??
    `task-${Date.now()}-${Math.random().toString(16).slice(2)}`
  );
}

const PENDING_CREATE_STORAGE_PREFIX = "ai-video.pending-create";

function pendingCreateStorageKey(workspaceKey) {
  return `${PENDING_CREATE_STORAGE_PREFIX}:${encodeURIComponent(workspaceKey)}`;
}

function taskRequestFingerprint(value) {
  let hash = 0x811c9dc5;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return `${value.length}:${(hash >>> 0).toString(16).padStart(8, "0")}`;
}

function readPendingCreate(workspaceKey) {
  if (!workspaceKey) return null;
  try {
    const value = globalThis.sessionStorage?.getItem(
      pendingCreateStorageKey(workspaceKey),
    );
    if (!value) return null;
    const parsed = JSON.parse(value);
    if (
      !parsed ||
      ![1, 2, 3, 4, 5].includes(parsed.version) ||
      String(parsed.workspaceKey || parsed.companyId || "") !== workspaceKey ||
      typeof parsed.fingerprint !== "string" ||
      typeof parsed.idempotencyKey !== "string" ||
      parsed.idempotencyKey.length < 8 ||
      parsed.idempotencyKey.length > 120 ||
      typeof parsed.modelId !== "string" ||
      !parsed.modelId ||
      (parsed.version >= 4 &&
        (!Number.isInteger(parsed.capabilityVersion) || parsed.capabilityVersion < 1)) ||
      (parsed.version >= 5 &&
        (typeof parsed.quoteRevision !== "string" ||
          !/^sha256:[0-9a-f]{64}$/.test(parsed.quoteRevision))) ||
      !parsed.requestPayload ||
      typeof parsed.requestPayload !== "object" ||
      Array.isArray(parsed.requestPayload)
    ) {
      return null;
    }
    const requestPayload = parsed.requestPayload;
    const allowedPayloadKeys = new Set([
      "mode",
      "prompt",
      "duration_seconds",
      "aspect_ratio",
      "resolution",
      "output_count",
      "face_enabled",
      "assets",
    ]);
    if (Object.keys(requestPayload).some((key) => !allowedPayloadKeys.has(key))) {
      return null;
    }
    const rawAssets = requestPayload.assets;
    if (rawAssets !== undefined && !Array.isArray(rawAssets)) return null;
    const assets = rawAssets ?? [];
    const validAssets = assets.every((asset) => (
      asset &&
      typeof asset === "object" &&
      !Array.isArray(asset) &&
      typeof asset.asset_id === "string" &&
      Boolean(asset.asset_id.trim()) &&
      ["image", "video", "audio"].includes(asset.media_type)
    ));
    if (
      !validAssets ||
      assets.length > 15 ||
      !["text_to_video", "image_to_video", "video_to_video", "text_to_image"].includes(
        requestPayload.mode,
      ) ||
      typeof requestPayload.prompt !== "string" ||
      !requestPayload.prompt.trim() ||
      requestPayload.prompt.length > 10_000 ||
      !Number.isInteger(requestPayload.duration_seconds) ||
      requestPayload.duration_seconds <= 0 ||
      requestPayload.duration_seconds > 3600 ||
      typeof requestPayload.aspect_ratio !== "string" ||
      !requestPayload.aspect_ratio ||
      (requestPayload.resolution !== undefined &&
        (typeof requestPayload.resolution !== "string" || !requestPayload.resolution)) ||
      (requestPayload.face_enabled !== undefined &&
        typeof requestPayload.face_enabled !== "boolean") ||
      !Number.isInteger(requestPayload.output_count) ||
      requestPayload.output_count < 1 ||
      requestPayload.output_count > 16 ||
      (requestPayload.mode === "image_to_video" &&
        !assets.some((asset) => asset.media_type === "image")) ||
      (requestPayload.mode === "video_to_video" &&
        !assets.some((asset) => asset.media_type === "video"))
    ) {
      return null;
    }
    const fingerprint = taskRequestFingerprint(JSON.stringify(
      parsed.version >= 5
        ? {
            modelId: parsed.modelId,
            capabilityVersion: parsed.capabilityVersion,
            quoteRevision: parsed.quoteRevision,
            requestPayload,
          }
        : parsed.version >= 4
          ? {
              modelId: parsed.modelId,
              capabilityVersion: parsed.capabilityVersion,
              requestPayload,
            }
          : { modelId: parsed.modelId, requestPayload },
    ));
    if (fingerprint !== parsed.fingerprint) return null;
    return { ...parsed, requestPayload };
  } catch {
    return null;
  }
}

function rememberPendingCreate(workspaceKey, value) {
  if (!workspaceKey) return;
  try {
    if (value) {
      globalThis.sessionStorage?.setItem(
        pendingCreateStorageKey(workspaceKey),
        JSON.stringify(value),
      );
    } else {
      globalThis.sessionStorage?.removeItem(pendingCreateStorageKey(workspaceKey));
    }
  } catch {
    // The in-memory ref still prevents duplicate clicks in restricted browsers.
  }
}

const SCENES = [
  {
    id: "water",
    number: "01",
    title: "产品节奏",
    range: "00:00 - 00:04",
    image: "/media/speaker-water-hero.png",
  },
  {
    id: "indoor",
    number: "02",
    title: "场景展示",
    range: "00:04 - 00:08",
    image: "/media/scene-indoor.png",
  },
  {
    id: "rain",
    number: "03",
    title: "防水演示",
    range: "00:08 - 00:11",
    image: "/media/scene-hand-rain.png",
  },
  {
    id: "lifestyle",
    number: "04",
    title: "使用氛围",
    range: "00:11 - 00:15",
    image: "/media/scene-lifestyle.png",
  },
];

const NAV_ITEMS = [
  { id: "shots", label: "首页", icon: House },
  { id: "create", label: "创作", icon: Sparkle },
  { id: "media", label: "素材", icon: ImageSquare },
  { id: "artworks", label: "作品", icon: Images },
  { id: "publish", label: "发布", icon: PaperPlaneTilt },
  { id: "history", label: "历史", icon: ClockCounterClockwise },
];

const COLLECTION_PAGE_SIZE = 24;

function readInitialDemoPersona() {
  try {
    const value = globalThis.sessionStorage?.getItem("ai-video.demo-persona");
    if (["operator", "owner", "platform_admin"].includes(value)) return value;
  } catch {
    // Demo account selection remains in memory when storage is unavailable.
  }
  return "operator";
}

const STATUS = {
  idle: { label: "等待提交", icon: FilmSlate },
  accepted: { label: "已接收", icon: CircleNotch },
  queued: { label: "排队中", icon: CircleNotch },
  rendering: { label: "渲染中", icon: SpinnerGap },
  complete: { label: "生成完成", icon: CheckCircle },
  failed: { label: "生成失败", icon: WarningCircle },
  cancelled: { label: "已取消", icon: X },
  "timed-out": { label: "已超时", icon: WarningCircle },
  "reconciliation-required": { label: "待人工确认", icon: WarningCircle },
  unknown: { label: "状态未知", icon: WarningCircle },
};

function MediaInputGroup({
  kind,
  label,
  limit,
  accept,
  files,
  onFiles,
  onRemove,
  uploading = false,
  uploadDisabled = false,
  locked = false,
  disabledReason = "",
  icon: Icon,
}) {
  const inputRef = useRef(null);
  const mediaInputId = `media-input-${kind}`;
  const displayedFiles = files;
  const inputDisabled = uploading || uploadDisabled;

  return (
    <div className="media-input-group">
      <div className="field-heading">
        <label htmlFor={mediaInputId}>{label}</label>
        <span>
          {files.length} / {limit}
        </span>
      </div>
      <div className="media-slot-row">
        {displayedFiles.map((file) => (
          <button
            className="media-slot media-slot-filled"
            key={`${kind}-${inputAssetId(file) || inputAssetName(file)}`}
            type="button"
            onClick={() => onRemove(file)}
            title={`移除 ${inputAssetName(file)}`}
            aria-label={`移除 ${inputAssetName(file)}`}
            disabled={uploading || locked}
          >
            <Check size={15} weight="bold" aria-hidden="true" />
            <span>{inputAssetName(file)}</span>
            <X size={13} weight="bold" aria-hidden="true" />
          </button>
        ))}
        {files.length < limit && (
          <button
            className="media-slot media-slot-add"
            type="button"
            onClick={() => inputRef.current?.click()}
            aria-label={`继续添加${label}`}
            disabled={inputDisabled}
          >
            {uploading ? (
              <SpinnerGap className="spin" size={21} aria-hidden="true" />
            ) : (
              <Icon size={18} aria-hidden="true" />
            )}
            <span>{files.length > 0 ? "继续添加" : "添加"}</span>
          </button>
        )}
      </div>
      <input
        id={mediaInputId}
        ref={inputRef}
        className="visually-hidden"
        type="file"
        tabIndex={-1}
        aria-label={`${label}文件选择器`}
        accept={accept}
        multiple={limit > 1}
        disabled={inputDisabled}
        onChange={(event) => {
          onFiles(Array.from(event.target.files ?? []));
          event.target.value = "";
        }}
      />
      {disabledReason && <small className="media-input-permission">{disabledReason}</small>}
    </div>
  );
}

function MediaLibrary({
  onUse,
  liveMode = false,
  assets = [],
  loading = false,
  error = "",
  uploading = false,
  canManageAssets = true,
  canCreateTasks = true,
  onUpload,
  onAdd,
  onPreview,
  onDelete,
}) {
  const inputRef = useRef(null);
  const [query, setQuery] = useState("");
  const filteredAssets = assets.filter((asset) =>
    inputAssetName(asset).toLowerCase().includes(query.trim().toLowerCase()),
  );

  return (
    <section className="secondary-view media-view">
      <div className="secondary-heading">
        <div>
          <span className="view-kicker">{liveMode ? "公司私有存储" : "当前项目"}</span>
          <h1>{liveMode ? "输入素材库" : "素材库"}</h1>
          <p>
            {liveMode
              ? "素材按公司隔离保存；生成任务只引用素材编号，不暴露长期公开地址。"
              : "选择一段画面，将它设为当前镜头。"}
          </p>
          {liveMode && (!canManageAssets || !canCreateTasks) && (
            <p className="permission-note" role="note">
              {!canManageAssets && !canCreateTasks
                ? "当前账号没有素材管理和任务创建权限。"
                : !canManageAssets
                  ? "当前账号可以选用已有素材，但不能上传或停用素材。"
                  : "当前账号可以管理素材，但不能把素材加入生成任务。"}
            </p>
          )}
        </div>
        <label className="search-field">
          <MagnifyingGlass size={18} aria-hidden="true" />
          <span className="visually-hidden">搜索素材</span>
          <input
            type="search"
            placeholder="搜索素材"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
      </div>
      {loading && (
        <div className="artifact-empty" role="status">
          <SpinnerGap className="spin" size={26} aria-hidden="true" />
          <strong>正在读取素材库</strong>
        </div>
      )}
      {!loading && error && (
        <div className="artifact-empty" role="alert">
          <WarningCircle size={26} aria-hidden="true" />
          <strong>素材库读取失败</strong>
          <span>{error}</span>
        </div>
      )}
      <div className="asset-grid">
        {!liveMode && SCENES.filter((scene) =>
          scene.title.toLowerCase().includes(query.trim().toLowerCase()),
        ).map((scene) => (
          <button
            className="asset-item"
            type="button"
            key={scene.id}
            onClick={() => onUse(scene.id)}
          >
            <img src={scene.image} alt="" />
            <span>
              <strong>{scene.title}</strong>
              <small>项目素材</small>
            </span>
          </button>
        ))}
        {!loading && filteredAssets.map((asset) => {
          const mediaType = inputAssetType(asset);
          const AssetIcon =
            mediaType === "video"
              ? VideoCamera
              : mediaType === "audio"
                ? MusicNote
                : ImageSquare;
          return (
            <article className="asset-item asset-item-live" key={inputAssetId(asset)}>
              <div className="asset-live-preview" aria-hidden="true">
                <AssetIcon size={34} weight="duotone" />
                <span>{mediaType === "video" ? "VIDEO" : mediaType === "audio" ? "AUDIO" : "IMAGE"}</span>
              </div>
              <span>
                <strong title={inputAssetName(asset)}>{inputAssetName(asset)}</strong>
                <small>{formatBytes(asset.size_bytes)} · {liveMode ? "私有素材" : "本地演示素材"}</small>
              </span>
              <div className="asset-actions">
                <button type="button" onClick={() => onAdd(asset)} disabled={!canCreateTasks}>加入任务</button>
                <button type="button" onClick={() => onPreview(asset)}>预览</button>
                <button className="is-danger" type="button" onClick={() => onDelete(asset)} disabled={!canManageAssets}>{liveMode ? "停用" : "移除"}</button>
              </div>
            </article>
          );
        })}
        {(!liveMode || !loading) && (
          <button
            className="asset-upload"
            type="button"
            onClick={() => inputRef.current?.click()}
            disabled={uploading || !canManageAssets}
          >
            {uploading ? (
              <SpinnerGap className="spin" size={28} aria-hidden="true" />
            ) : (
              <UploadSimple size={28} aria-hidden="true" />
            )}
            <strong>{uploading ? "正在私有上传" : canManageAssets ? "上传素材" : "无素材管理权限"}</strong>
            <span>{canManageAssets ? "图片、视频或音频" : "请联系公司老板授权"}</span>
          </button>
        )}
      </div>
      <input
        id="asset-library-upload-input"
        ref={inputRef}
        className="visually-hidden"
        type="file"
        tabIndex={-1}
        aria-label="上传素材文件选择器"
        accept="image/*,video/*,audio/*"
        multiple
        disabled={uploading || !canManageAssets}
        onChange={(event) => {
          onUpload(Array.from(event.target.files ?? []));
          event.target.value = "";
        }}
      />
    </section>
  );
}

const DEMO_HISTORY_TASKS = DEMO_MODE ? [
  {
    id: "demo-task-complete",
    company_name: "远创电商",
    user_display_name: "陈默",
    user_email: "chenmo@example.cn",
    model_id: "cinemox",
    model_display_name: "CinemoX Pro 2.1",
    status: "succeeded",
    request_payload: {
      mode: "image_to_video",
      prompt: "产品防水演示",
      aspect_ratio: "16:9",
      resolution: "1080p",
      duration_seconds: 15,
      output_count: 1,
      assets: [{ media_type: "image" }],
    },
    actual_cost_cents: 4500,
    artifact_count: 1,
    download_issue_count: 2,
    download_completed_count: 1,
    downloaded: true,
    created_at: "2026-08-01T07:42:00Z",
  },
  {
    id: "demo-task-failed",
    company_name: "远创电商",
    user_display_name: "林瑶",
    model_id: "rush",
    model_display_name: "Rush Video 1.6",
    status: "failed",
    request_payload: {
      mode: "text_to_video",
      prompt: "户外使用氛围",
      aspect_ratio: "9:16",
      resolution: "720p",
      duration_seconds: 10,
      output_count: 1,
    },
    quote_cents: 1800,
    artifact_count: 0,
    created_at: "2026-08-01T08:18:00Z",
  },
] : [];

const DEMO_ARTWORKS = DEMO_MODE ? [
  {
    artifact_id: "demo-artwork-1",
    task_id: "demo-task-complete",
    asset_id: "demo-video-1",
    output_index: 0,
    media_type: "video",
    content_type: "video/mp4",
    size_bytes: 4372373,
    sha256: "demo-sha256-video",
    model_id: "cinemox",
    company_name: "远创电商",
    created_by_display_name: "陈默",
    model_display_name: "CinemoX Pro 2.1",
    request_payload: DEMO_HISTORY_TASKS[0].request_payload,
    actual_cost_cents: 4500,
    download_issue_count: 2,
    download_completed_count: 1,
    downloaded: true,
    created_at: "2026-08-01T07:56:00Z",
    preview_url: "/media/speaker-water-hero.png",
  },
  {
    artifact_id: "demo-artwork-2",
    task_id: "demo-task-image",
    asset_id: "demo-image-1",
    output_index: 0,
    media_type: "image",
    content_type: "image/png",
    size_bytes: 8991,
    sha256: "demo-sha256-image",
    model_id: "frameflow",
    company_name: "远创电商",
    created_by_display_name: "张帆",
    model_display_name: "FrameFlow Lite",
    request_payload: {
      mode: "text_to_image",
      prompt: "桌面场景展示",
      aspect_ratio: "1:1",
      resolution: "1080p",
      output_count: 1,
    },
    actual_cost_cents: 500,
    download_issue_count: 1,
    download_completed_count: 0,
    downloaded: false,
    created_at: "2026-07-31T12:06:00Z",
    preview_url: "/media/scene-indoor.png",
  },
] : [];

export function App() {
  const {
    logout: logoutSession,
    handleAuthenticationError,
  } = useAuth();
  const [skin, setSkin] = useSkinPreference();
  const initialAppRoute = appRouteFromPath(globalThis.location?.pathname);
  const pendingCreateRef = useRef(null);
  const initialPendingCreate = pendingCreateRef.current;
  const [activeNav, setActiveNav] = useState(initialAppRoute.nav);
  const [surface, setSurface] = useState(initialAppRoute.surface);
  const navigateStudio = (nextNav, { replace = false } = {}) => {
    const nextPath = surfacePath(surface === "personal" ? "personal" : "studio", nextNav);
    setActiveNav(nextNav);
    if (!globalThis.history || !globalThis.location) return;
    if (globalThis.location.pathname === nextPath) return;
    globalThis.history[replace ? "replaceState" : "pushState"]({}, "", nextPath);
  };
  useEffect(() => {
    const handlePopState = () => {
      const route = appRouteFromPath(globalThis.location?.pathname);
      setActiveNav(route.nav);
      setSurface(route.surface);
      if (!route.recognized) {
        globalThis.history?.replaceState?.({}, "", route.canonicalPath);
      }
    };
    globalThis.addEventListener?.("popstate", handlePopState);
    return () => globalThis.removeEventListener?.("popstate", handlePopState);
  }, []);
  useEffect(() => {
    if (initialAppRoute.recognized) return;
    globalThis.history?.replaceState?.({}, "", initialAppRoute.canonicalPath);
  }, []);
  const [demoPersonaId, setDemoPersonaId] = useState(readInitialDemoPersona);
  const activeDemoPersona = DEMO_MODE ? resolveDemoPersona(demoPersonaId) : null;
  const [activeSceneId, setActiveSceneId] = useState("water");
  const [models, setModels] = useState(DEMO_MODE ? DEMO_MODELS : []);
  const [modelsLoading, setModelsLoading] = useState(LIVE_MODE);
  const [modelsError, setModelsError] = useState("");
  const [companyIdentity, setCompanyIdentity] = useState(null);
  const [personalIdentity, setPersonalIdentity] = useState(null);
  const [platformIdentity, setPlatformIdentity] = useState(null);
  const [sessionSurfaceCatalog, setSessionSurfaceCatalog] = useState(null);
  const [activeCompanyId, setActiveCompanyId] = useState(
    runtimePlatformConfig.companyId || "",
  );
  const [personalWallet, setPersonalWallet] = useState(null);
  const [personalWalletError, setPersonalWalletError] = useState("");
  const [modelId, setModelId] = useState(
    DEMO_MODE ? DEMO_MODELS[0].id : initialPendingCreate?.modelId ?? "",
  );
  const [generationMode, setGenerationMode] = useState(
    DEMO_MODE
      ? DEMO_INITIAL_MODE
      : initialPendingCreate?.requestPayload.mode ?? "",
  );
  const [ratio, setRatio] = useState(
    DEMO_MODE
      ? DEMO_INITIAL_CAPABILITY.limits.aspectRatios[0]
      : initialPendingCreate?.requestPayload.aspect_ratio ?? "",
  );
  const [resolution, setResolution] = useState(
    DEMO_MODE
      ? DEMO_INITIAL_CAPABILITY.limits.resolutions[0]
      : initialPendingCreate?.requestPayload.resolution ?? "",
  );
  const [duration, setDuration] = useState(
    DEMO_MODE ? 15 : initialPendingCreate?.requestPayload.duration_seconds ?? null,
  );
  const [outputCount, setOutputCount] = useState(
    DEMO_MODE ? 1 : initialPendingCreate?.requestPayload.output_count ?? null,
  );
  const [faceEnabled, setFaceEnabled] = useState(
    DEMO_MODE ? false : Boolean(initialPendingCreate?.requestPayload.face_enabled),
  );
  const [prompt, setPrompt] = useState(
    DEMO_MODE
      ? "突出产品防水便携的特点，户外场景拍摄，光线自然干净，节奏明快，适合短视频投放。"
      : initialPendingCreate?.requestPayload.prompt ?? "",
  );
  const [files, setFiles] = useState(() =>
    !DEMO_MODE && initialPendingCreate
      ? filesFromPendingRequest(initialPendingCreate.requestPayload)
      : { image: [], video: [], audio: [] },
  );
  const [assets, setAssets] = useState([]);
  const [assetsLoading, setAssetsLoading] = useState(LIVE_MODE);
  const [assetsError, setAssetsError] = useState("");
  const [uploadingKind, setUploadingKind] = useState("");
  const [stage, setStage] = useState(DEMO_MODE ? "rendering" : "idle");
  const [progress, setProgress] = useState(DEMO_MODE ? 65 : 0);
  const [currentTaskId, setCurrentTaskId] = useState("");
  const [currentTask, setCurrentTask] = useState(null);
  const [currentTaskScope, setCurrentTaskScope] = useState("mine");
  const [detailTask, setDetailTask] = useState(null);
  const [detailTaskScope, setDetailTaskScope] = useState("mine");
  const [submitting, setSubmitting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [formError, setFormError] = useState(
    initialPendingCreate ? "上次提交结果尚未确认，已恢复原参数；再次提交会复用原幂等键。" : "",
  );
  const [promptError, setPromptError] = useState("");
  const [authExpired, setAuthExpired] = useState(false);
  const [identityResolved, setIdentityResolved] = useState(DEMO_MODE);
  const [identityError, setIdentityError] = useState("");
  const [playing, setPlaying] = useState(false);
  const [playhead, setPlayhead] = useState(2);
  const [historyTasks, setHistoryTasks] = useState([]);
  const [historyLoading, setHistoryLoading] = useState(LIVE_MODE);
  const [historyError, setHistoryError] = useState("");
  const [historyScope, setHistoryScope] = useState("mine");
  const [historyStatus, setHistoryStatus] = useState("");
  const [historyPage, setHistoryPage] = useState(1);
  const [historyTotal, setHistoryTotal] = useState(0);
  const [creationPage, setCreationPage] = useState(1);
  const [creationTotal, setCreationTotal] = useState(0);
  const [creationStatus, setCreationStatus] = useState("");
  const [creationDays, setCreationDays] = useState("30");
  const [creationModelId, setCreationModelId] = useState("");
  const [creationMediaType, setCreationMediaType] = useState("video");
  const [creationQuery, setCreationQuery] = useState("");
  const [artworks, setArtworks] = useState([]);
  const [artworksLoading, setArtworksLoading] = useState(LIVE_MODE);
  const [artworksError, setArtworksError] = useState("");
  const [artworkScope, setArtworkScope] = useState("mine");
  const [artworkMediaFilter, setArtworkMediaFilter] = useState("");
  const [artworkDownloadFilter, setArtworkDownloadFilter] = useState("");
  const [artworkPage, setArtworkPage] = useState(1);
  const [artworkTotal, setArtworkTotal] = useState(0);
  const [artworkPreviewUrls, setArtworkPreviewUrls] = useState({});
  const [availableResources, setAvailableResources] = useState([]);
  const [resourcesResolved, setResourcesResolved] = useState(DEMO_MODE);
  const [issuedArtifacts, setIssuedArtifacts] = useState({});
  const [artifactActionKey, setArtifactActionKey] = useState("");
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const [resultOpen, setResultOpen] = useState(false);
  const [composerExpanded, setComposerExpanded] = useState(false);
  const [downloadingAssetId, setDownloadingAssetId] = useState("");
  const [downloadError, setDownloadError] = useState("");
  const [publicationIntent, setPublicationIntent] = useState(null);
  const [toast, setToast] = useState("");
  const [taskCompletionNotices, setTaskCompletionNotices] = useState(true);
  const composerRef = useRef(null);
  const mainCanvasRef = useRef(null);
  const notificationAnchorRef = useRef(null);
  const userAnchorRef = useRef(null);
  const resultDialogRef = useRef(null);
  const resultReturnFocusRef = useRef(null);
  const historyRequestGenerationRef = useRef(0);
  const activeTaskRequestGenerationRef = useRef(0);
  const artworkRequestGenerationRef = useRef(0);
  const taskDetailRequestGenerationRef = useRef(0);
  const taskDetailControllerRef = useRef(null);

  useEffect(() => {
    const delay = nextPreviewCleanupDelay(artworkPreviewUrls);
    if (delay === null) return undefined;
    const cleanup = () => {
      setArtworkPreviewUrls((current) => removeExpiredPreviewLeases(current));
    };
    if (delay <= 0) {
      cleanup();
      return undefined;
    }
    const timer = globalThis.setTimeout?.(
      cleanup,
      Math.min(Math.ceil(delay) + 1, 2_147_483_647),
    );
    return () => globalThis.clearTimeout?.(timer);
  }, [artworkPreviewUrls]);

  const closeResultDialog = () => {
    taskDetailRequestGenerationRef.current += 1;
    taskDetailControllerRef.current?.abort();
    taskDetailControllerRef.current = null;
    setResultOpen(false);
    setDetailTask(null);
  };

  useLayoutEffect(() => {
    const canvas = mainCanvasRef.current;
    if (!canvas) return;
    canvas.scrollTop = 0;
    canvas.scrollLeft = 0;
  }, [activeNav]);

  useEffect(() => {
    if (!resultOpen) {
      const returnTarget = resultReturnFocusRef.current;
      resultReturnFocusRef.current = null;
      if (returnTarget?.isConnected && typeof returnTarget.focus === "function") {
        globalThis.requestAnimationFrame?.(() => {
          const taskbar = returnTarget.closest?.(".taskbar");
          if (returnTarget.getClientRects?.().length === 0 && taskbar) {
            taskbar.focus({ preventScroll: true });
            globalThis.requestAnimationFrame?.(() => returnTarget.focus({ preventScroll: true }));
          } else {
            returnTarget.focus({ preventScroll: true });
          }
        });
      }
      return undefined;
    }

    const dialog = resultDialogRef.current;
    if (!dialog) return undefined;
    const body = globalThis.document?.body;
    const previousBodyOverflow = body?.style.overflow ?? "";
    if (body) body.style.overflow = "hidden";

    const activeElement = globalThis.document?.activeElement;
    if (!resultReturnFocusRef.current && !dialog.contains(activeElement)) {
      resultReturnFocusRef.current = activeElement;
    }

    const focusableElements = () => Array.from(
      dialog.querySelectorAll(
        'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ),
    ).filter((element) => (
      element.getAttribute("aria-hidden") !== "true"
      && (element.offsetWidth > 0 || element.offsetHeight > 0 || element.getClientRects().length > 0)
    ));

    const focusFrame = globalThis.requestAnimationFrame?.(() => {
      (focusableElements()[0] || dialog).focus();
    });

    const handleDialogKeyDown = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeResultDialog();
        return;
      }
      if (event.key !== "Tab") return;

      const focusable = focusableElements();
      if (focusable.length === 0) {
        event.preventDefault();
        dialog.focus();
        return;
      }

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const focused = globalThis.document?.activeElement;
      if (event.shiftKey && (focused === first || !dialog.contains(focused))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (focused === last || !dialog.contains(focused))) {
        event.preventDefault();
        first.focus();
      }
    };

    globalThis.document?.addEventListener("keydown", handleDialogKeyDown);
    return () => {
      globalThis.document?.removeEventListener("keydown", handleDialogKeyDown);
      if (body) body.style.overflow = previousBodyOverflow;
      if (focusFrame !== undefined) {
        globalThis.cancelAnimationFrame?.(focusFrame);
      }
    };
  }, [resultOpen]);

  useEffect(() => {
    if (!notificationsOpen && !userMenuOpen) return undefined;

    const closePopovers = ({ restoreFocus = false } = {}) => {
      const returnTarget = notificationsOpen
        ? notificationAnchorRef.current?.querySelector("button")
        : userAnchorRef.current?.querySelector("button");
      setNotificationsOpen(false);
      setUserMenuOpen(false);
      if (restoreFocus) {
        globalThis.requestAnimationFrame?.(() => returnTarget?.focus?.({ preventScroll: true }));
      }
    };
    const handleKeyDown = (event) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      closePopovers({ restoreFocus: true });
    };
    const handlePointerDown = (event) => {
      const target = event.target;
      if (notificationAnchorRef.current?.contains(target)) return;
      if (userAnchorRef.current?.contains(target)) return;
      closePopovers();
    };

    globalThis.document?.addEventListener("keydown", handleKeyDown);
    globalThis.document?.addEventListener("pointerdown", handlePointerDown);
    return () => {
      globalThis.document?.removeEventListener("keydown", handleKeyDown);
      globalThis.document?.removeEventListener("pointerdown", handlePointerDown);
    };
  }, [notificationsOpen, userMenuOpen]);

  const expireSessionIfNeeded = (error) => {
    if (!(error instanceof PlatformApiError)) return false;
    if (!handleAuthenticationError(error)) return false;
    if (error.code !== "STEP_UP_REQUIRED") setAuthExpired(true);
    setUserMenuOpen(false);
    setNotificationsOpen(false);
    return true;
  };

  const model = useMemo(() => {
    const selected = models.find((item) => item.id === modelId);
    if (selected) return selected;
    if (modelId && pendingCreateRef.current?.modelId === modelId) {
      return { ...EMPTY_MODEL, id: modelId, name: "历史授权模型" };
    }
    return EMPTY_MODEL;
  }, [modelId, models]);
  const activeScene =
    SCENES.find((scene) => scene.id === activeSceneId) ?? SCENES[0];
  const taskStatus = STATUS[stage] ?? STATUS.unknown;
  const TaskIcon = taskStatus.icon;
  const resultTask = detailTask || currentTask;
  const resultTaskStatus = resolveTaskStatus(resultTask?.status);
  const ResultStatusIcon = STATUS[resultTaskStatus.stage]?.icon ?? WarningCircle;
  const taskStageIsActive = ["accepted", "queued", "rendering"].includes(stage);
  const taskStageNeedsAttention = [
    "timed-out",
    "reconciliation-required",
    "unknown",
  ].includes(stage);
  const resultTaskScope = detailTask ? detailTaskScope : currentTaskScope;
  const resultOutputArtifacts =
    LIVE_MODE && Array.isArray(resultTask?.output_artifacts)
      ? resultTask.output_artifacts
      : [];
  const activeOutputArtifacts =
    LIVE_MODE && Array.isArray(currentTask?.output_artifacts)
      ? currentTask.output_artifacts
      : [];
  const supportedModes = Object.keys(model.effectiveCapabilities?.modes ?? {});
  const activeCapability =
    capabilityForMode(model.effectiveCapabilities, generationMode) ??
    capabilityForMode(model.effectiveCapabilities, model.defaultMode);
  const visibleControls = capabilityControlVisibility(activeCapability);
  const mediaLimits = {
    image: visibleControls.image ? activeCapability.limits.maxImages : 0,
    video: visibleControls.video ? activeCapability.limits.maxVideos : 0,
    audio: visibleControls.audio ? activeCapability.limits.maxAudio : 0,
  };
  const pendingPayload = pendingCreateRef.current?.requestPayload ?? null;
  const promptLimit = activeCapability?.limits.maxPromptLength ?? 0;
  const faceControlAvailable = visibleControls.face;
  const historicalFaceVisible = Boolean(
    pendingPayload && Object.hasOwn(pendingPayload, "face_enabled"),
  );
  const showFaceSummary = faceControlAvailable || historicalFaceVisible;
  const historicalRequestLocked = Boolean(pendingPayload);
  const visibleMediaLimits = historicalRequestLocked
    ? {
        image: files.image.length,
        video: files.video.length,
        audio: files.audio.length,
      }
    : mediaLimits;
  const companyClient = useMemo(() => {
    if (!liveClient || !activeCompanyId) return liveClient;
    if (activeCompanyId === runtimePlatformConfig.companyId) return liveClient;
    return createPlatformClient({
      ...runtimePlatformConfig,
      companyId: activeCompanyId,
    });
  }, [activeCompanyId]);
  const liveAvailableSurfaces = [
    personalIdentity ? "personal" : "",
    ...(companyIdentity ? allowedSurfacesForIdentity(companyIdentity) : []),
    platformIdentity ? "platform" : "",
  ].filter((item, index, values) => item && values.indexOf(item) === index);
  const selectedLiveIdentity = surface === "personal"
    ? personalIdentity
    : surface === "platform"
      ? platformIdentity
      : companyIdentity;
  const fallbackLiveIdentity = companyIdentity || personalIdentity || platformIdentity;
  const rawSessionIdentity = DEMO_MODE
    ? activeDemoPersona.identity
    : selectedLiveIdentity || fallbackLiveIdentity;
  const sessionIdentity = !DEMO_MODE && rawSessionIdentity
    ? { ...rawSessionIdentity, available_surfaces: liveAvailableSurfaces }
    : rawSessionIdentity;
  const studioPreferenceSubject = sessionIdentity?.user_id || (
    DEMO_MODE ? `demo:${demoPersonaId}` : "unresolved"
  );
  useEffect(() => {
    setTaskCompletionNotices(
      readStudioPreferences(studioPreferenceSubject).taskCompletionNotices,
    );
  }, [studioPreferenceSubject]);
  const sessionSurfaces = allowedSurfacesForIdentity(sessionIdentity);
  const sessionSurfaceKey = sessionSurfaces.join("|");
  const effectiveSurface = resolveSurfaceForIdentity(sessionIdentity, surface);
  useEffect(() => {
    if (!["studio", "personal"].includes(effectiveSurface)) return;
    const routeLabel = activeNav === "settings"
      ? "设置"
      : NAV_ITEMS.find((item) => item.id === activeNav)?.label || "工作台";
    const workspaceLabel = effectiveSurface === "personal" ? "个人空间" : "企业创作";
    if (globalThis.document) {
      globalThis.document.title = `${routeLabel} · ${workspaceLabel} · ${BRAND_NAME}`;
    }
  }, [activeNav, effectiveSurface]);
  const permissionCodes = Array.isArray(sessionIdentity?.permission_codes)
    ? sessionIdentity.permission_codes
    : [];
  const identityReady = DEMO_MODE || (identityResolved && Boolean(sessionIdentity));
  const isPersonalWorkspace = effectiveSurface === "personal";
  const hasCompanySession = effectiveSurface !== "personal"
    && Boolean(sessionIdentity?.company_id)
    && !sessionIdentity?.is_platform_admin;
  const canCreateTasks = isPersonalWorkspace
    ? personalCapability(sessionIdentity, "generation")
      && personalCapability(sessionIdentity, "tasks")
    : hasCompanySession && permissionCodes.includes("tasks.create");
  const canManageAssets = isPersonalWorkspace
    ? personalCapability(sessionIdentity, "assets")
    : hasCompanySession && permissionCodes.includes("assets.manage");
  const canAccessArtifacts = isPersonalWorkspace
    ? personalCapability(sessionIdentity, "artifact_access")
    : hasCompanySession;
  const canCancelTasks = isPersonalWorkspace
    ? personalCapability(sessionIdentity, "task_cancel")
    : hasCompanySession;
  const hasStudioSession = isPersonalWorkspace
    ? Boolean(sessionIdentity?.workspace_id)
    : hasCompanySession;
  const canReadStudioModels = isPersonalWorkspace
    ? personalCapability(sessionIdentity, "models")
    : hasCompanySession;
  const canReadStudioTasks = isPersonalWorkspace
    ? personalCapability(sessionIdentity, "tasks")
    : hasCompanySession;
  const canReadStudioArtworks = isPersonalWorkspace
    ? personalCapability(sessionIdentity, "artworks")
    : hasCompanySession;
  const canViewCompanyRecords = hasCompanySession && permissionCodes.includes("reports.read");
  const canReadPublisherAccounts = hasCompanySession && permissionCodes.includes("publish.accounts.read");
  const canManagePublisherAccounts = hasCompanySession && permissionCodes.includes("publish.accounts.manage");
  const canReadPublicationJobs = hasCompanySession && permissionCodes.includes("publish.jobs.read");
  const canManagePublicationJobs = hasCompanySession && permissionCodes.includes("publish.jobs.manage");
  const hasPublishingPermission = canReadPublisherAccounts || canReadPublicationJobs;
  const hasAutoPublishEntitlement = DEMO_MODE || availableResources.some((resource) => (
    (resource.key || resource.resource_key) === "feature.auto_publish" &&
    resource.active !== false &&
    resource.enabled !== false &&
    resource.status !== "disabled"
  ));
  const showPublishingNavigation = isPersonalWorkspace || (
    hasCompanySession && (DEMO_MODE || hasPublishingPermission)
  );
  const canStartPublication = DEMO_MODE || (
    hasCompanySession &&
    resourcesResolved &&
    hasAutoPublishEntitlement &&
    canReadPublisherAccounts &&
    canReadPublicationJobs &&
    canManagePublicationJobs
  );
  const companyName =
    sessionIdentity?.company_name ||
    sessionIdentity?.company_display_name ||
    "";
  const studioWorkspaceKey = isPersonalWorkspace
    ? sessionIdentity?.workspace_id
      ? `personal:${sessionIdentity.workspace_id}`
      : ""
    : sessionIdentity?.company_id || activeCompanyId;
  const studioClient = useMemo(() => {
    if (!LIVE_MODE || !liveClient) return liveClient;
    if (!isPersonalWorkspace) return companyClient;
    return {
      listModels: (...args) => liveClient.listPersonalModels(...args),
      listTaskHistory: (...args) => liveClient.listPersonalTasks(...args),
      listTasks: ({ signal } = {}) => liveClient.listPersonalTasks({}, { signal }),
      listArtworks: (...args) => liveClient.listPersonalArtworks(...args),
      getTask: (...args) => liveClient.getPersonalTask(...args),
      createTask: (...args) => liveClient.createPersonalTask(...args),
      getArtifactPreview: (...args) => liveClient.getPersonalArtifactPreview(...args),
      getArtifactDownload: (...args) => liveClient.getPersonalArtifactDownload(...args),
    };
  }, [companyClient, isPersonalWorkspace]);
  const refreshPersonalWallet = useCallback(async ({ signal } = {}) => {
    if (!LIVE_MODE || !isPersonalWorkspace || !hasStudioSession) return null;
    try {
      const wallet = await liveClient.getPersonalWallet({ signal });
      const normalized = wallet && typeof wallet === "object" ? wallet : null;
      setPersonalWallet(normalized);
      setPersonalWalletError("");
      return normalized;
    } catch (error) {
      if (error?.name === "AbortError") return null;
      expireSessionIfNeeded(error);
      setPersonalWallet(null);
      setPersonalWalletError(readableApiError(error));
      return null;
    }
  }, [hasStudioSession, isPersonalWorkspace]);
  const GenerationIcon = generationMode === "text_to_image" ? ImageSquare : VideoCamera;
  const cost = LIVE_MODE
    ? (isPersonalWorkspace ? model.unitPricePoints : model.unitPriceCents) *
      (model.pricingMode === "per_second" ? duration : outputCount)
    : duration * model.rate * outputCount;
  const costLabel = historicalRequestLocked
    ? "原提交待确认"
    : LIVE_MODE
      ? isPersonalWorkspace
        ? `${Math.max(0, Math.round(cost))} 积分`
        : `¥${(cost / 100).toFixed(2)}`
      : `${cost} 积分`;
  const hasStoredArtifacts =
    LIVE_MODE &&
    currentTask?.status === "succeeded" &&
    activeOutputArtifacts.length > 0;
  const StoredStateIcon =
    LIVE_MODE && !hasStoredArtifacts ? ClockCounterClockwise : Check;
  const storedStateTitle = LIVE_MODE
    ? hasStoredArtifacts
      ? "产物已安全转存"
      : resultTask
        ? "任务记录已保存"
        : "尚未生成产物"
    : "产物已保存";

  const updateTaskCompletionNotices = (enabled) => {
    const nextValue = Boolean(enabled);
    const nextPreferences = { taskCompletionNotices: nextValue };
    setTaskCompletionNotices(nextValue);
    const persisted = writeStudioPreferences(
      studioPreferenceSubject,
      nextPreferences,
    );
    setToast(
      persisted
        ? nextValue
          ? "已开启任务结果站内提示"
          : "已关闭任务结果站内提示"
        : "当前浏览器阻止了偏好存储，本次选择仅在当前页面有效",
    );
  };
  const activeCapabilityRef = useRef(activeCapability);
  activeCapabilityRef.current = activeCapability;

  const studioDraft = () => ({
    prompt,
    duration,
    aspectRatio: ratio,
    resolution,
    outputCount,
    faceEnabled,
    files,
  });

  const applyReconciledDraft = (result, { announce = true } = {}) => {
    setGenerationMode(result.mode);
    setPrompt(result.draft.prompt ?? "");
    setDuration(result.draft.duration);
    setRatio(result.draft.aspectRatio);
    setResolution(result.draft.resolution);
    setOutputCount(result.draft.outputCount);
    setFaceEnabled(result.draft.faceEnabled);
    setFiles(result.draft.files);

    if (!announce || !result.changes.length) return;
    const notices = [];
    if (result.changes.includes("mode")) notices.push("生成模式已匹配");
    if (result.changes.includes("prompt")) notices.push("制作说明已按字数上限裁剪");
    if (
      result.changes.some((item) =>
        ["duration", "aspectRatio", "resolution", "outputCount"].includes(item),
      )
    ) {
      notices.push("参数已调整");
    }
    if (result.changes.includes("face")) notices.push("人脸选项已关闭");
    if (result.removedMediaCount > 0) {
      notices.push(`${result.removedMediaCount} 个超出能力的素材已移除`);
    }
    setToast(
      result.ok
        ? `已按模型能力更新：${notices.join("，")}`
        : result.error,
    );
  };

  const selectStudioModel = (nextModelId) => {
    if (pendingCreateRef.current) return;
    const nextModel = models.find((item) => item.id === nextModelId) ?? EMPTY_MODEL;
    const result = reconcileGenerationDraft(
      nextModel.effectiveCapabilities,
      generationMode,
      studioDraft(),
    );
    setModelId(nextModelId);
    applyReconciledDraft(result);
    setPromptError("");
    setFormError(result.ok ? "" : result.error);
  };

  const selectGenerationMode = (nextMode) => {
    if (pendingCreateRef.current) return;
    const result = reconcileGenerationDraft(
      model.effectiveCapabilities,
      nextMode,
      studioDraft(),
    );
    applyReconciledDraft(result);
    setPromptError("");
    setFormError(result.ok ? "" : result.error);
  };

  useEffect(() => {
    if (!identityResolved) return;
    if (sessionSurfaces.includes(surface)) return;
    const fallback = defaultSurfaceForIdentity(sessionIdentity);
    setSurface(fallback);
    const canonicalPath = surfacePath(fallback, activeNav);
    if (globalThis.location?.pathname !== canonicalPath) {
      globalThis.history?.replaceState?.({}, "", canonicalPath);
    }
    try {
      globalThis.sessionStorage?.setItem("ai-video.surface", fallback);
    } catch {
      // The fail-closed surface correction still applies in memory.
    }
  }, [activeNav, identityResolved, sessionIdentity?.user_id, sessionSurfaceKey, surface]);

  useEffect(() => {
    if (!LIVE_MODE || authExpired) return undefined;
    const controller = new AbortController();

    setIdentityResolved(false);
    setIdentityError("");
    const legacyIdentityProbe = async () => {
      const companyProbe = activeCompanyId
        ? createPlatformClient({
            ...runtimePlatformConfig,
            companyId: activeCompanyId,
          }).getCompanyMe({ signal: controller.signal })
        : Promise.reject(new PlatformApiError("当前会话没有公司上下文", {
            code: "COMPANY_ID_NOT_CONFIGURED",
          }));
      const [companyResult, adminResult] = await Promise.allSettled([
        companyProbe,
        liveClient.getPlatformAdminMe({ signal: controller.signal }),
      ]);
      return { companyResult, adminResult, personalResult: null, catalog: null };
    };

    const discoverIdentity = async () => {
      let catalog;
      try {
        catalog = normalizeSessionSurfaces(
          await liveClient.getSessionSurfaces({ signal: controller.signal }),
        );
      } catch (error) {
        if (!missingCollectionEndpoint(error)) throw error;
        return legacyIdentityProbe();
      }

      const selectedCompanyId = preferredCompanyId(
        catalog,
        activeCompanyId || runtimePlatformConfig.companyId,
      );
      if (selectedCompanyId && selectedCompanyId !== activeCompanyId) {
        setActiveCompanyId(selectedCompanyId);
      }
      if (selectedCompanyId) {
        try {
          globalThis.sessionStorage?.setItem("ai-video.company-id", selectedCompanyId);
        } catch {
          // The selected server-authorized company still applies in memory.
        }
      }
      const selectedCompanyClient = selectedCompanyId
        ? createPlatformClient({
            ...runtimePlatformConfig,
            companyId: selectedCompanyId,
          })
        : null;
      const [companyResult, adminResult, personalResult] = await Promise.allSettled([
        selectedCompanyClient
          ? selectedCompanyClient.getCompanyMe({ signal: controller.signal })
          : Promise.resolve(null),
        catalog.platform_admin
          ? liveClient.getPlatformAdminMe({ signal: controller.signal })
          : Promise.resolve(null),
        catalog.personal
          ? liveClient.getPersonalMe({ signal: controller.signal })
          : Promise.resolve(null),
      ]);
      return { companyResult, adminResult, personalResult, catalog };
    };

    discoverIdentity()
      .then(({ companyResult, adminResult, personalResult, catalog }) => {
        if (controller.signal.aborted) return;
        const errors = [
          companyResult?.status === "rejected" ? companyResult.reason : null,
          adminResult?.status === "rejected" ? adminResult.reason : null,
          personalResult?.status === "rejected" ? personalResult.reason : null,
        ].filter(Boolean);
        const authError = errors.find((error) => (
          error instanceof PlatformApiError &&
          (error.status === 401 || error.code === "AUTH_NOT_CONFIGURED")
        ));
        if (authError) {
          expireSessionIfNeeded(authError);
          return;
        }

        const nextCompanyIdentity = companyResult?.status === "fulfilled"
          ? companyResult.value
          : null;
        const nextPlatformIdentity = adminResult?.status === "fulfilled" && adminResult.value
          ? {
              ...adminResult.value,
              company_id: null,
              permission_codes: [],
              roles: [],
              is_platform_admin: true,
            }
          : null;
        const nextPersonalIdentity = catalog
          && personalResult?.status === "fulfilled"
          && personalResult.value
          ? {
              ...personalIdentityFromSession(catalog),
              ...personalResult.value,
              company_id: null,
              workspace_id: catalog.personal?.workspace_id,
              workspace_kind: "personal",
              workspace_label: catalog.personal?.label || "个人空间",
              personal_capabilities: catalog.personal?.capabilities || {},
              permission_codes: [],
              roles: [],
              is_personal: true,
              is_platform_admin: false,
            }
          : null;

        setSessionSurfaceCatalog(catalog);
        setCompanyIdentity(nextCompanyIdentity);
        setPlatformIdentity(nextPlatformIdentity);
        setPersonalIdentity(nextPersonalIdentity);
        if (!nextCompanyIdentity && !nextPlatformIdentity && !nextPersonalIdentity) {
          setIdentityError(
            catalog
              ? "当前账号没有可用的个人、公司或平台工作区。"
              : "无法确认当前账号属于公司成员还是平台管理员，请稍后重试。",
          );
        } else {
          setIdentityError("");
        }
      })
      .catch((error) => {
        if (controller.signal.aborted) return;
        if (!expireSessionIfNeeded(error)) {
          setIdentityError("无法读取账号工作区，请稍后重试。");
        }
        setSessionSurfaceCatalog(null);
        setCompanyIdentity(null);
        setPlatformIdentity(null);
        setPersonalIdentity(null);
      })
      .finally(() => {
        if (!controller.signal.aborted) setIdentityResolved(true);
      });

    return () => controller.abort();
  }, [activeCompanyId, authExpired]);

  useEffect(() => {
    if (!LIVE_MODE || !identityResolved || !hasStudioSession || !studioWorkspaceKey) {
      return;
    }
    const pendingCreate = readPendingCreate(studioWorkspaceKey);
    pendingCreateRef.current = pendingCreate;
    setModels([]);
    setModelId(pendingCreate?.modelId || "");
    setGenerationMode(pendingCreate?.requestPayload?.mode || "");
    setPrompt(pendingCreate?.requestPayload?.prompt || "");
    setDuration(pendingCreate?.requestPayload?.duration_seconds ?? null);
    setOutputCount(pendingCreate?.requestPayload?.output_count ?? null);
    setRatio(pendingCreate?.requestPayload?.aspect_ratio || "");
    setResolution(pendingCreate?.requestPayload?.resolution || "");
    setFaceEnabled(Boolean(pendingCreate?.requestPayload?.face_enabled));
    setFiles(
      pendingCreate
        ? filesFromPendingRequest(pendingCreate.requestPayload)
        : { image: [], video: [], audio: [] },
    );
    setAssets([]);
    setHistoryTasks([]);
    setArtworks([]);
    setArtworkPreviewUrls({});
    setIssuedArtifacts({});
    setCurrentTask(null);
    setCurrentTaskId("");
    setDetailTask(null);
    setStage("idle");
    setProgress(0);
    setHistoryScope("mine");
    setHistoryStatus("");
    setArtworkScope("mine");
    setArtworkMediaFilter("");
    setArtworkDownloadFilter("");
    setCreationStatus("");
    setCreationDays("30");
    setCreationModelId("");
    setCreationMediaType("video");
    setCreationQuery("");
    setHistoryPage(1);
    setCreationPage(1);
    setArtworkPage(1);
    setFormError(
      pendingCreate
        ? "上次提交结果尚未确认，已恢复当前工作空间的原参数；再次提交会复用原幂等键。"
        : "",
    );
    setPromptError("");
    closeResultDialog();
  }, [hasStudioSession, identityResolved, studioWorkspaceKey]);

  useEffect(() => {
    if (
      !LIVE_MODE ||
      authExpired ||
      !hasStudioSession ||
      !canReadStudioModels ||
      !["studio", "personal"].includes(effectiveSurface)
    ) return undefined;
    const controller = new AbortController();
    setModelsLoading(true);

    studioClient
      .listModels({ signal: controller.signal })
      .then((items) => {
        const normalized = Array.isArray(items)
          ? items.map((source) => normalizeLiveModel(source, { requireEffective: true }))
          : [];
        setModels(normalized);
        const pendingCreate = pendingCreateRef.current;
        const selectedModel =
          normalized.find((item) => item.id === pendingCreate?.modelId) ??
          normalized[0];
        setModelId(pendingCreate?.modelId ?? selectedModel?.id ?? "");
        if (pendingCreate) {
          const payload = pendingCreate.requestPayload;
          setGenerationMode(payload.mode);
          setDuration(payload.duration_seconds);
          setRatio(payload.aspect_ratio);
          if (Object.hasOwn(payload, "resolution")) {
            setResolution(payload.resolution);
          }
          setOutputCount(payload.output_count);
          setFaceEnabled(Boolean(payload.face_enabled));
        } else {
          const result = reconcileGenerationDraft(
            selectedModel?.effectiveCapabilities,
            selectedModel?.defaultMode ?? "",
            studioDraft(),
          );
          applyReconciledDraft(result, { announce: false });
          if (!result.ok && selectedModel) setFormError(result.error);
        }
        setModelsError("");
      })
      .catch((error) => {
        if (error?.name !== "AbortError") {
          expireSessionIfNeeded(error);
          setModelsError(readableApiError(error));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setModelsLoading(false);
      });

    return () => controller.abort();
  }, [authExpired, canReadStudioModels, effectiveSurface, hasStudioSession, studioClient, studioWorkspaceKey]);

  useEffect(() => {
    if (
      !LIVE_MODE ||
      authExpired ||
      effectiveSurface !== "personal" ||
      !hasStudioSession
    ) {
      setPersonalWallet(null);
      setPersonalWalletError("");
      return undefined;
    }
    const controller = new AbortController();
    setPersonalWalletError("");
    refreshPersonalWallet({ signal: controller.signal });
    return () => controller.abort();
  }, [authExpired, effectiveSurface, hasStudioSession, refreshPersonalWallet, studioWorkspaceKey]);

  useEffect(() => {
    if (!LIVE_MODE || authExpired || !hasCompanySession || effectiveSurface !== "studio") return undefined;
    const controller = new AbortController();
    setResourcesResolved(false);

    companyClient
      .listResources({ signal: controller.signal })
      .then((response) => {
        const items = Array.isArray(response)
          ? response
          : Array.isArray(response?.items)
            ? response.items
            : [];
        setAvailableResources(items);
      })
      .catch((error) => {
        if (error?.name !== "AbortError") {
          expireSessionIfNeeded(error);
          setAvailableResources([]);
          setToast(`自动发布授权读取失败：${readableApiError(error)}`);
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setResourcesResolved(true);
      });

    return () => controller.abort();
  }, [authExpired, companyClient, effectiveSurface, hasCompanySession, sessionIdentity?.user_id]);

  useEffect(() => {
    if (!LIVE_MODE || authExpired || !hasCompanySession || effectiveSurface !== "studio") return undefined;
    const controller = new AbortController();

    companyClient
      .listAssets({ status: "active" }, { signal: controller.signal })
      .then((items) => {
        const activeAssets = Array.isArray(items) ? items : [];
        const byId = new Map(
          activeAssets.map((asset) => [inputAssetId(asset), asset]),
        );
        setAssets(activeAssets);
        setFiles((current) => ({
          image: current.image.map(
            (asset) => byId.get(inputAssetId(asset)) ?? asset,
          ),
          video: current.video.map(
            (asset) => byId.get(inputAssetId(asset)) ?? asset,
          ),
          audio: current.audio.map(
            (asset) => byId.get(inputAssetId(asset)) ?? asset,
          ),
        }));
        setAssetsError("");
      })
      .catch((error) => {
        if (error?.name !== "AbortError") {
          expireSessionIfNeeded(error);
          setAssetsError(readableApiError(error));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setAssetsLoading(false);
      });

    return () => controller.abort();
  }, [authExpired, companyClient, effectiveSurface, hasCompanySession, sessionIdentity?.user_id]);

  useEffect(() => {
    if (
      !LIVE_MODE ||
      authExpired ||
      !hasStudioSession ||
      !canReadStudioTasks ||
      !["studio", "personal"].includes(effectiveSurface)
    ) return undefined;
    const controller = new AbortController();
    const requestGeneration = ++activeTaskRequestGenerationRef.current;

    Promise.all([
      fetchTaskHistoryPage(
        studioClient,
        {
          page: 1,
          page_size: 1,
          ...(!isPersonalWorkspace ? { scope: "mine" } : {}),
          status: "accepted",
        },
        { signal: controller.signal },
      ),
      fetchTaskHistoryPage(
        studioClient,
        {
          page: 1,
          page_size: 1,
          ...(!isPersonalWorkspace ? { scope: "mine" } : {}),
          status: "queued",
        },
        { signal: controller.signal },
      ),
      fetchTaskHistoryPage(
        studioClient,
        {
          page: 1,
          page_size: 1,
          ...(!isPersonalWorkspace ? { scope: "mine" } : {}),
          status: "processing",
        },
        { signal: controller.signal },
      ),
    ])
      .then((pages) => {
        if (requestGeneration !== activeTaskRequestGenerationRef.current) return;
        const activeTask = pages
          .flatMap((pageData) => pageData.items)
          .sort((left, right) => (
            Date.parse(right.created_at || right.updated_at || 0)
            - Date.parse(left.created_at || left.updated_at || 0)
          ))[0];
        if (activeTask) {
          const activeTaskId = activeTask.id || activeTask.task_id;
          setCurrentTask(activeTask);
          setCurrentTaskId(activeTaskId);
          setCurrentTaskScope("mine");
          setStage(mapTaskStage(activeTask.status));
          setProgress(progressForTask(activeTask.status));
        }
      })
      .catch((error) => {
        if (error?.name !== "AbortError" && requestGeneration === activeTaskRequestGenerationRef.current) {
          expireSessionIfNeeded(error);
        }
      })
      .finally(() => {
        // The route-owned list request controls loading and error presentation.
      });

    return () => {
      activeTaskRequestGenerationRef.current += 1;
      controller.abort();
    };
  }, [authExpired, canReadStudioTasks, effectiveSurface, hasStudioSession, isPersonalWorkspace, studioClient, studioWorkspaceKey]);

  useEffect(() => {
    if (
      !LIVE_MODE ||
      authExpired ||
      !hasStudioSession ||
      !canReadStudioTasks ||
      !["studio", "personal"].includes(effectiveSurface) ||
      !["create", "history"].includes(activeNav)
    ) return undefined;
    let stopped = false;
    let timer;
    let controller;
    const requestGeneration = ++historyRequestGenerationRef.current;
    setHistoryLoading(true);

    const refreshHistory = async () => {
      controller = new AbortController();
      try {
        const pageData = await fetchTaskHistoryPage(
          studioClient,
          {
            page: activeNav === "create" ? creationPage : historyPage,
            page_size: COLLECTION_PAGE_SIZE,
            ...(!isPersonalWorkspace
              ? { scope: activeNav === "create" ? "mine" : historyScope }
              : {}),
            status: activeNav === "create" ? creationStatus : historyStatus,
            model_id: activeNav === "create" ? creationModelId : "",
            media_type: activeNav === "create" ? creationMediaType : "",
            query: !isPersonalWorkspace && activeNav === "create" ? creationQuery.trim() : "",
            start_time: !isPersonalWorkspace && activeNav === "create" && creationDays !== "all"
              ? new Date(Date.now() - Number(creationDays) * 86_400_000).toISOString()
              : "",
          },
          { signal: controller.signal },
        );
        if (stopped || requestGeneration !== historyRequestGenerationRef.current) return;
        setHistoryTasks(pageData.items);
        if (activeNav === "create") {
          setCreationTotal(pageData.total);
        } else {
          setHistoryTotal(pageData.total);
        }
        setHistoryError("");
      } catch (error) {
        if (!stopped && requestGeneration === historyRequestGenerationRef.current && error?.name !== "AbortError") {
          if (expireSessionIfNeeded(error)) {
            stopped = true;
            return;
          }
          setHistoryError(readableApiError(error));
        }
      } finally {
        if (!stopped && requestGeneration === historyRequestGenerationRef.current) {
          setHistoryLoading(false);
          timer = window.setTimeout(refreshHistory, 10_000);
        }
      }
    };

    refreshHistory();
    return () => {
      stopped = true;
      historyRequestGenerationRef.current += 1;
      window.clearTimeout(timer);
      controller?.abort();
    };
  }, [activeNav, authExpired, canReadStudioTasks, creationDays, creationMediaType, creationModelId, creationPage, creationQuery, creationStatus, effectiveSurface, hasStudioSession, historyPage, historyScope, historyStatus, isPersonalWorkspace, studioClient, studioWorkspaceKey]);

  useEffect(() => {
    if (
      !LIVE_MODE ||
      authExpired ||
      !hasStudioSession ||
      !canReadStudioArtworks ||
      !["studio", "personal"].includes(effectiveSurface) ||
      activeNav !== "artworks"
    ) return undefined;
    let stopped = false;
    let timer;
    let controller;
    const requestGeneration = ++artworkRequestGenerationRef.current;
    setArtworksLoading(true);

    const refreshArtworks = async () => {
      controller = new AbortController();
      try {
        const pageData = await fetchArtworkPage(
          studioClient,
          {
            page: artworkPage,
            page_size: COLLECTION_PAGE_SIZE,
            ...(!isPersonalWorkspace ? { scope: artworkScope } : {}),
            media_type: artworkMediaFilter,
            downloaded: !isPersonalWorkspace ? artworkDownloadFilter : "",
          },
          { signal: controller.signal },
        );
        if (stopped || requestGeneration !== artworkRequestGenerationRef.current) return;
        setArtworks(pageData.items);
        setArtworkTotal(pageData.total);
        setArtworksError("");
      } catch (error) {
        if (!stopped && requestGeneration === artworkRequestGenerationRef.current && error?.name !== "AbortError") {
          if (expireSessionIfNeeded(error)) {
            stopped = true;
            return;
          }
          setArtworksError(readableApiError(error));
        }
      } finally {
        if (!stopped && requestGeneration === artworkRequestGenerationRef.current) {
          setArtworksLoading(false);
          timer = window.setTimeout(refreshArtworks, 15_000);
        }
      }
    };

    refreshArtworks();
    return () => {
      stopped = true;
      artworkRequestGenerationRef.current += 1;
      window.clearTimeout(timer);
      controller?.abort();
    };
  }, [
    activeNav,
    artworkDownloadFilter,
    artworkMediaFilter,
    artworkPage,
    artworkScope,
    authExpired,
    effectiveSurface,
    canReadStudioArtworks,
    hasStudioSession,
    isPersonalWorkspace,
    studioClient,
    studioWorkspaceKey,
  ]);

  useEffect(() => {
    if (!identityReady || activeNav !== "publish" || showPublishingNavigation) return;
    navigateStudio("artworks", { replace: true });
  }, [activeNav, identityReady, showPublishingNavigation]);

  useEffect(() => {
    if (!identityReady || canViewCompanyRecords) return;
    setHistoryScope("mine");
    setArtworkScope("mine");
  }, [canViewCompanyRecords, identityReady]);

  useEffect(() => {
    if (pendingCreateRef.current || modelsLoading || !model.id) return;
    const result = reconcileGenerationDraft(
      model.effectiveCapabilities,
      generationMode,
      studioDraft(),
    );
    applyReconciledDraft(result);
  }, [generationMode, model, modelsLoading]);

  useEffect(() => {
    if (!DEMO_MODE) return undefined;
    if (stage === "queued") {
      const timer = window.setTimeout(() => {
        setStage("rendering");
        setProgress((value) => Math.max(value, 12));
      }, 850);
      return () => window.clearTimeout(timer);
    }

    if (stage === "rendering") {
      const timer = window.setInterval(() => {
        setProgress((value) => {
          if (value >= 100) {
            window.clearInterval(timer);
            setStage("complete");
            if (taskCompletionNotices) {
              setToast(isPersonalWorkspace
                ? "成片已生成，归档元数据已写入个人空间"
                : "成片已生成并转存到公司存储");
            }
            return 100;
          }
          return Math.min(100, value + 3);
        });
      }, 900);
      return () => window.clearInterval(timer);
    }
    return undefined;
  }, [isPersonalWorkspace, stage, taskCompletionNotices]);

  useEffect(() => {
    if (
      !LIVE_MODE ||
      authExpired ||
      !hasStudioSession ||
      !canReadStudioTasks ||
      !["studio", "personal"].includes(effectiveSurface) ||
      !currentTaskId
    ) return undefined;
    let stopped = false;
    let timer;
    let activeController;

    const poll = async () => {
      activeController = new AbortController();
      try {
        const task = await studioClient.getTask(currentTaskId, {
          signal: activeController.signal,
          ...(!isPersonalWorkspace ? { scope: currentTaskScope } : {}),
        });
        if (stopped) return;
        const nextStatus = resolveTaskStatus(task.status);
        const nextStage = nextStatus.stage;
        setCurrentTask(task);
        setStage(nextStage);
        setProgress(nextStatus.progress);
        if (task.status === "succeeded") {
          if (isPersonalWorkspace) refreshPersonalWallet();
          setDownloadError("");
          setResultOpen(true);
          if (taskCompletionNotices) {
            setToast(
              task.output_artifacts?.length
                ? `任务已完成，共 ${task.output_artifacts.length} 个产物可下载`
                : "任务已完成，但平台暂未返回产物元数据",
            );
          }
          return;
        }
        if (task.status === "failed") {
          if (isPersonalWorkspace) refreshPersonalWallet();
          const message = task.failure_reason || nextStatus.detail;
          setFormError(message);
          if (taskCompletionNotices) setToast(message);
          return;
        }
        if (task.status === "cancelled") {
          if (isPersonalWorkspace) refreshPersonalWallet();
          return;
        }
        if (isTaskAttentionRequired(task.status) || nextStatus.stage === "unknown") {
          if (isPersonalWorkspace && nextStatus.terminal) refreshPersonalWallet();
          const message = task.failure_reason || nextStatus.detail;
          setFormError(message);
          if (taskCompletionNotices) setToast(message);
          return;
        }
        if (nextStatus.terminal) return;
        timer = window.setTimeout(poll, 2000);
      } catch (error) {
        if (error?.name === "AbortError" || stopped) return;
        if (expireSessionIfNeeded(error)) return;
        setFormError(`任务状态同步失败：${readableApiError(error)}`);
        timer = window.setTimeout(poll, 4000);
      }
    };

    poll();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      activeController?.abort();
    };
  }, [
    authExpired,
    currentTaskId,
    currentTaskScope,
    effectiveSurface,
    canReadStudioTasks,
    hasStudioSession,
    isPersonalWorkspace,
    sessionIdentity?.user_id,
    refreshPersonalWallet,
    studioClient,
    taskCompletionNotices,
  ]);

  useEffect(() => {
    if (!playing) return undefined;
    const timer = window.setInterval(() => {
      setPlayhead((value) => {
        if (value >= duration) {
          setPlaying(false);
          return 0;
        }
        return Math.min(duration, value + 0.25);
      });
    }, 250);
    return () => window.clearInterval(timer);
  }, [playing, duration]);

  useEffect(() => {
    if (!toast) return undefined;
    const timer = window.setTimeout(() => setToast(""), 3200);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const removeInputAsset = (kind, asset) => {
    if (pendingCreateRef.current) {
      restorePendingCreate(
        pendingCreateRef.current,
        "存在结果尚未确认的提交，不能改变素材；请先安全确认原任务。",
      );
      return;
    }
    const targetId = inputAssetId(asset);
    setFiles((current) => ({
      ...current,
      [kind]: current[kind].filter((item) =>
        targetId
          ? inputAssetId(item) !== targetId
          : inputAssetName(item) !== inputAssetName(asset),
      ),
    }));
    setToast(`${inputAssetName(asset)} 已从当前任务移除`);
  };

  const handleFiles = async (kind, incoming) => {
    if (LIVE_MODE && !canManageAssets) {
      const message = isPersonalWorkspace
        ? "个人空间本期只开放无素材的文生视频与文生图片，素材输入尚未开放。"
        : "当前账号没有 assets.manage 素材管理权限，请联系公司老板授权。";
      setFormError(message);
      setToast(message);
      return;
    }
    const limit = mediaLimits[kind] ?? 0;
    const remaining = Math.max(0, limit - files[kind].length);
    const selected = incoming.slice(0, remaining);
    if (!selected.length) {
      setToast(`${kind === "image" ? "图片" : kind === "video" ? "视频" : "音频"}素材已达到模型上限`);
      return;
    }
    if (pendingCreateRef.current) {
      restorePendingCreate(
        pendingCreateRef.current,
        "存在结果尚未确认的提交，不能改变素材；请先安全确认原任务。",
      );
      return;
    }
    if (!LIVE_MODE) {
      const localAssets = selected.map((file) => ({
        id:
          globalThis.crypto?.randomUUID?.() ??
          `local-${Date.now()}-${Math.random().toString(16).slice(2)}`,
        media_type: kind,
        original_filename: file.name,
        content_type: file.type,
        size_bytes: file.size,
        source_file: file,
        status: "active",
      }));
      setFiles((current) => ({
        ...current,
        [kind]: [...current[kind], ...localAssets],
      }));
      setAssets((current) => [...localAssets, ...current]);
      setToast(`${selected.length} 个素材已加入当前任务`);
      return;
    }

    const invalid = selected.find(
      (file) => file.type && !file.type.toLowerCase().startsWith(`${kind}/`),
    );
    if (invalid) {
      const message = `${invalid.name} 的文件类型与${kind === "image" ? "图片" : kind === "video" ? "视频" : "音频"}输入不匹配`;
      setFormError(message);
      setToast(message);
      return;
    }

    setUploadingKind(kind);
    setFormError("");
    const uploaded = [];
    try {
      for (const file of selected) {
        const asset = await companyClient.uploadAsset(file, kind);
        uploaded.push(asset);
      }
      setAssets((current) => {
        const incomingIds = new Set(uploaded.map(inputAssetId));
        return [
          ...uploaded,
          ...current.filter((asset) => !incomingIds.has(inputAssetId(asset))),
        ];
      });
      setFiles((current) => ({
        ...current,
        [kind]: [...current[kind], ...uploaded].slice(
          0,
          activeCapabilityRef.current?.inputMediaTypes.includes(kind)
            ? activeCapabilityRef.current.limits[
                kind === "image" ? "maxImages" : kind === "video" ? "maxVideos" : "maxAudio"
              ]
            : 0,
        ),
      }));
      setAssetsError("");
      setToast(`${uploaded.length} 个素材已私有上传并加入当前任务`);
    } catch (error) {
      if (uploaded.length) {
        setAssets((current) => {
          const incomingIds = new Set(uploaded.map(inputAssetId));
          return [
            ...uploaded,
            ...current.filter((asset) => !incomingIds.has(inputAssetId(asset))),
          ];
        });
        setFiles((current) => ({
          ...current,
          [kind]: [...current[kind], ...uploaded].slice(
            0,
            activeCapabilityRef.current?.inputMediaTypes.includes(kind)
              ? activeCapabilityRef.current.limits[
                  kind === "image" ? "maxImages" : kind === "video" ? "maxVideos" : "maxAudio"
                ]
              : 0,
          ),
        }));
      }
      expireSessionIfNeeded(error);
      const message = `素材上传失败：${readableApiError(error)}`;
      setFormError(message);
      setAssetsError(message);
      setToast(message);
    } finally {
      setUploadingKind("");
    }
  };

  const addLibraryAssetToTask = (asset) => {
    if (LIVE_MODE && !canCreateTasks) {
      setToast(isPersonalWorkspace
        ? "当前个人空间未开放任务创建能力。"
        : "当前账号没有 tasks.create 任务创建权限，请联系公司老板授权。");
      return;
    }
    if (pendingCreateRef.current) {
      restorePendingCreate(
        pendingCreateRef.current,
        "存在结果尚未确认的提交，不能改变素材；请先安全确认原任务。",
      );
      return;
    }
    const kind = inputAssetType(asset);
    const limit = mediaLimits[kind] ?? 0;
    if (limit <= 0) {
      setToast("当前模型不支持这类素材");
      return;
    }
    if (files[kind].some((item) => inputAssetId(item) === inputAssetId(asset))) {
      setToast("该素材已经在当前任务中");
      return;
    }
    if (files[kind].length >= limit) {
      setToast(`当前模型最多使用 ${limit} 个这类素材`);
      return;
    }
    setFiles((current) => ({
      ...current,
      [kind]: [...current[kind], asset],
    }));
    navigateStudio("shots");
    setToast(`${inputAssetName(asset)} 已加入当前任务`);
  };

  const uploadLibraryFiles = async (incoming) => {
    if (!incoming.length) return;
    if (LIVE_MODE && !canManageAssets) {
      const message = isPersonalWorkspace
        ? "个人空间素材库与上传能力尚未开放。"
        : "当前账号没有 assets.manage 素材管理权限，请联系公司老板授权。";
      setAssetsError(message);
      setToast(message);
      return;
    }
    const typedFiles = incoming.map((file) => {
      const kind = ["image", "video", "audio"].find((candidate) =>
        file.type?.toLowerCase().startsWith(`${candidate}/`),
      );
      return { file, kind };
    });
    const invalid = typedFiles.find((item) => !item.kind);
    if (invalid) {
      const message = `${invalid.file.name} 不是支持的图片、视频或音频文件`;
      setAssetsError(message);
      setToast(message);
      return;
    }

    if (!LIVE_MODE) {
      const localAssets = typedFiles.map(({ file, kind }) => ({
        id:
          globalThis.crypto?.randomUUID?.() ??
          `local-${Date.now()}-${Math.random().toString(16).slice(2)}`,
        media_type: kind,
        original_filename: file.name,
        content_type: file.type,
        size_bytes: file.size,
        source_file: file,
        status: "active",
      }));
      setAssets((current) => [...localAssets, ...current]);
      setToast(`${localAssets.length} 个本地演示素材已加入素材库`);
      return;
    }

    setUploadingKind("library");
    setAssetsError("");
    const uploaded = [];
    try {
      for (const item of typedFiles) {
        uploaded.push(await companyClient.uploadAsset(item.file, item.kind));
      }
      setAssets((current) => {
        const uploadedIds = new Set(uploaded.map(inputAssetId));
        return [
          ...uploaded,
          ...current.filter((asset) => !uploadedIds.has(inputAssetId(asset))),
        ];
      });
      setToast(`${uploaded.length} 个素材已保存到公司私有素材库`);
    } catch (error) {
      if (uploaded.length) {
        setAssets((current) => [...uploaded, ...current]);
      }
      expireSessionIfNeeded(error);
      const message = `素材上传失败：${readableApiError(error)}`;
      setAssetsError(message);
      setToast(message);
    } finally {
      setUploadingKind("");
    }
  };

  const previewLibraryAsset = async (asset) => {
    if (!inputAssetId(asset)) return;
    if (!LIVE_MODE) {
      if (!asset.source_file) {
        setToast("当前演示素材没有可打开的本地文件");
        return;
      }
      const objectUrl = URL.createObjectURL(asset.source_file);
      const previewWindow = window.open(objectUrl, "_blank", "noopener,noreferrer");
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
      if (!previewWindow) setToast("浏览器阻止了预览窗口，请允许弹窗后重试");
      return;
    }
    if (isPersonalWorkspace || !canManageAssets) {
      setToast("个人空间素材访问尚未开放，未发起任何访问请求。");
      return;
    }
    const pendingWindow = window.open("about:blank", "_blank");
    if (pendingWindow) pendingWindow.opener = null;
    try {
      const preview = await companyClient.getAssetPreview(inputAssetId(asset));
      const url = parseArtifactDownloadUrl(preview.url, {
        allowLocalHttp: Boolean(import.meta.env.DEV),
      });
      if (pendingWindow && !pendingWindow.closed) {
        pendingWindow.location.replace(url.toString());
      } else {
        window.open(url.toString(), "_blank", "noopener,noreferrer");
      }
      setToast(`预览地址已生成，${preview.expires_seconds} 秒内有效`);
    } catch (error) {
      pendingWindow?.close();
      expireSessionIfNeeded(error);
      const message = `素材预览失败：${readableApiError(error)}`;
      setAssetsError(message);
      setToast(message);
    }
  };

  const deleteLibraryAsset = async (asset) => {
    if (!inputAssetId(asset)) return;
    if (LIVE_MODE && !canManageAssets) {
      setToast(isPersonalWorkspace
        ? "个人空间素材管理尚未开放。"
        : "当前账号没有 assets.manage 素材管理权限，请联系公司老板授权。");
      return;
    }
    if (!LIVE_MODE) {
      setAssets((current) =>
        current.filter((item) => inputAssetId(item) !== inputAssetId(asset)),
      );
      setFiles((current) => ({
        image: current.image.filter(
          (item) => inputAssetId(item) !== inputAssetId(asset),
        ),
        video: current.video.filter(
          (item) => inputAssetId(item) !== inputAssetId(asset),
        ),
        audio: current.audio.filter(
          (item) => inputAssetId(item) !== inputAssetId(asset),
        ),
      }));
      setToast(`${inputAssetName(asset)} 已从本地演示素材库移除`);
      return;
    }
    try {
      await companyClient.deleteAsset(inputAssetId(asset));
      setAssets((current) =>
        current.filter((item) => inputAssetId(item) !== inputAssetId(asset)),
      );
      setFiles((current) => ({
        image: current.image.filter(
          (item) => inputAssetId(item) !== inputAssetId(asset),
        ),
        video: current.video.filter(
          (item) => inputAssetId(item) !== inputAssetId(asset),
        ),
        audio: current.audio.filter(
          (item) => inputAssetId(item) !== inputAssetId(asset),
        ),
      }));
      setAssetsError("");
      setToast(`${inputAssetName(asset)} 已停用，历史任务记录不受影响`);
    } catch (error) {
      expireSessionIfNeeded(error);
      const message = `素材停用失败：${readableApiError(error)}`;
      setAssetsError(message);
      setToast(message);
    }
  };

  const restorePendingCreate = (pendingCreate, message) => {
    const payload = pendingCreate.requestPayload;
    setModelId(pendingCreate.modelId);
    setGenerationMode(payload.mode);
    setPrompt(payload.prompt);
    setDuration(payload.duration_seconds);
    setOutputCount(payload.output_count);
    setRatio(payload.aspect_ratio);
    setResolution(
      Object.hasOwn(payload, "resolution")
        ? payload.resolution
        : "",
    );
    setFaceEnabled(Boolean(payload.face_enabled));
    setFiles(filesFromPendingRequest(payload));
    setStage("idle");
    setProgress(0);
    closeResultDialog();
    setPromptError("");
    const recoveryMessage =
      message ??
      "上一条提交结果尚未确认，已恢复原参数；请先复用原幂等键确认结果。";
    setFormError(recoveryMessage);
    setToast(recoveryMessage);
  };

  const startGeneration = async () => {
    if (uploadingKind) {
      setPromptError("");
      setFormError("素材仍在上传，请完成后再提交任务。");
      return;
    }
    const storedPending = pendingCreateRef.current;
    if (LIVE_MODE && (!identityReady || !canCreateTasks)) {
      setPromptError("");
      setFormError(
        identityReady
          ? isPersonalWorkspace
            ? "当前个人空间未开放任务创建能力。"
            : "当前账号没有 tasks.create 任务创建权限，请联系公司老板授权。"
          : "正在确认当前账号的任务创建权限，请稍后再试。",
      );
      return;
    }
    if (!storedPending && !prompt.trim()) {
      setFormError("");
      setPromptError("请填写制作说明");
      return;
    }
    if (LIVE_MODE && !model.id && !storedPending) {
      setPromptError("");
      setFormError(modelsError || (isPersonalWorkspace
        ? "个人空间当前没有可用的零售模型。"
        : "公司当前没有已授权的可用模型。"));
      return;
    }
    if (!storedPending && !activeCapability) {
      setPromptError("");
      setFormError("当前模型没有可用的生成能力声明，请联系平台管理员。");
      return;
    }
    if (!storedPending && prompt.trim().length > promptLimit) {
      setFormError("");
      setPromptError(`当前模式的制作说明最多 ${promptLimit} 个字`);
      return;
    }
    if (
      isPersonalWorkspace &&
      !storedPending &&
      (
        !["text_to_video", "text_to_image"].includes(generationMode) ||
        faceEnabled ||
        Object.values(files).some((items) => items.length > 0)
      )
    ) {
      setPromptError("");
      setFormError("个人空间本期仅支持无素材、无人脸输入的文生视频与文生图片，当前请求已被阻止。");
      return;
    }

    let requestPayload;
    let targetModelId;
    let targetCapabilityVersion;
    let targetQuoteRevision;
    if (storedPending) {
      const currentPendingModel = models.find(
        (item) => item.id === storedPending.modelId,
      );
      requestPayload = storedPending.requestPayload;
      targetModelId = storedPending.modelId;
      targetCapabilityVersion =
        storedPending.capabilityVersion ?? currentPendingModel?.capabilityVersion;
      targetQuoteRevision =
        storedPending.quoteRevision ?? currentPendingModel?.quoteRevision;
      if (
        LIVE_MODE &&
        (!Number.isInteger(targetCapabilityVersion) || targetCapabilityVersion < 1)
      ) {
        setPromptError("");
        setFormError("旧提交缺少可确认的能力版本，已阻止继续提交；请刷新模型后重新发起。");
        return;
      }
      if (
        LIVE_MODE &&
        (typeof targetQuoteRevision !== "string" ||
          !/^sha256:[0-9a-f]{64}$/.test(targetQuoteRevision))
      ) {
        setPromptError("");
        setFormError("旧提交缺少可确认的报价版本，已阻止继续提交；请刷新模型后重新发起。");
        return;
      }
    } else {
      if (
        LIVE_MODE &&
        (!Number.isInteger(model.capabilityVersion) || model.capabilityVersion < 1)
      ) {
        setPromptError("");
        setFormError("当前模型缺少有效的能力版本，已阻止提交，请联系平台管理员。");
        return;
      }
      if (
        LIVE_MODE &&
        (typeof model.quoteRevision !== "string" ||
          !/^sha256:[0-9a-f]{64}$/.test(model.quoteRevision))
      ) {
        setPromptError("");
        setFormError("当前模型缺少有效的报价版本，已阻止提交，请刷新后重试。");
        return;
      }
      const built = buildCapabilityRequestPayload(
        model.effectiveCapabilities,
        generationMode,
        studioDraft(),
        { includeAssets: LIVE_MODE && !isPersonalWorkspace },
      );
      applyReconciledDraft(built.reconciled);
      if (!built.ok) {
        setPromptError("");
        setFormError(built.error);
        return;
      }
      requestPayload = built.payload;
      targetModelId = model.id;
      targetCapabilityVersion = model.capabilityVersion;
      targetQuoteRevision = model.quoteRevision;
    }
    const fingerprint = JSON.stringify(
      storedPending?.version < 4
        ? { modelId: targetModelId, requestPayload }
        : storedPending?.version === 4
          ? {
              modelId: targetModelId,
              capabilityVersion: targetCapabilityVersion,
              requestPayload,
            }
          : {
            modelId: targetModelId,
            capabilityVersion: targetCapabilityVersion,
            quoteRevision: targetQuoteRevision,
            requestPayload,
          },
    );
    const requestFingerprint = taskRequestFingerprint(fingerprint);
    if (storedPending && storedPending.fingerprint !== requestFingerprint) {
      restorePendingCreate(storedPending);
      return;
    }
    if (storedPending && storedPending.version < 5) {
      const upgradedPending = {
        ...storedPending,
        version: 5,
        capabilityVersion: targetCapabilityVersion,
        quoteRevision: targetQuoteRevision,
        fingerprint: taskRequestFingerprint(JSON.stringify({
          modelId: targetModelId,
          capabilityVersion: targetCapabilityVersion,
          quoteRevision: targetQuoteRevision,
          requestPayload,
        })),
      };
      pendingCreateRef.current = upgradedPending;
      rememberPendingCreate(studioWorkspaceKey, upgradedPending);
    }

    setPromptError("");
    setFormError("");
    setDownloadError("");
    closeResultDialog();
    setProgress(6);
    setStage("queued");
    if (!LIVE_MODE) {
      setToast("任务已提交，失败不会扣费");
      return;
    }

    if (!pendingCreateRef.current) {
      pendingCreateRef.current = {
        version: 5,
        workspaceKey: studioWorkspaceKey,
        ...(!isPersonalWorkspace ? { companyId: activeCompanyId } : {}),
        modelId: targetModelId,
        capabilityVersion: targetCapabilityVersion,
        quoteRevision: targetQuoteRevision,
        requestPayload,
        fingerprint: requestFingerprint,
        idempotencyKey: makeIdempotencyKey(),
        uncertain: false,
      };
      rememberPendingCreate(studioWorkspaceKey, pendingCreateRef.current);
    }
    const pendingCreate = pendingCreateRef.current;

    setSubmitting(true);
    try {
      const task = await studioClient.createTask(
        {
          modelId: targetModelId,
          requestPayload,
          expectedCapabilityVersion: pendingCreate.capabilityVersion,
          expectedQuoteRevision: pendingCreate.quoteRevision,
        },
        { idempotencyKey: pendingCreate.idempotencyKey },
      );
      if (
        !task ||
        typeof task.id !== "string" ||
        !task.id ||
        typeof task.status !== "string"
      ) {
        throw new PlatformApiError("客户平台返回了不完整的任务响应", {
          code: "INVALID_RESPONSE",
        });
      }
      pendingCreateRef.current = null;
      rememberPendingCreate(studioWorkspaceKey, null);
      setCurrentTask(task);
      setCurrentTaskId(task.id);
      setCurrentTaskScope("mine");
      setStage(mapTaskStage(task.status));
      setProgress(progressForTask(task.status));
      if (isPersonalWorkspace) refreshPersonalWallet();
      setToast(`真实任务已提交 · ${shortId(task.id)}`);
    } catch (error) {
      const submissionUncertain = Boolean(
        pendingCreate.uncertain ||
          error?.submissionUncertain ||
          !(error instanceof PlatformApiError) ||
          error.status === 0 ||
          error.status >= 500 ||
          ["NETWORK_ERROR", "REQUEST_TIMEOUT", "INVALID_RESPONSE"].includes(
            error.code,
          ),
      );
      if (submissionUncertain) {
        pendingCreate.uncertain = true;
        rememberPendingCreate(studioWorkspaceKey, pendingCreate);
      } else {
        pendingCreateRef.current = null;
        rememberPendingCreate(studioWorkspaceKey, null);
      }
      expireSessionIfNeeded(error);
      setStage("idle");
      setProgress(0);
      const reason = readableApiError(error);
      const message = submissionUncertain
        ? `提交结果尚未确认，原参数和幂等键已保留。稍后再次点击会安全确认同一任务。${reason}`
        : reason;
      setFormError(message);
      setToast(message);
    } finally {
      setSubmitting(false);
    }
  };

  const openHistoryTask = async (task, requestedScope = historyScope) => {
    const taskId = task?.id || task?.task_id;
    if (!taskId) return;
    resultReturnFocusRef.current = globalThis.document?.activeElement ?? null;
    setDetailTask(task);
    setDetailTaskScope(requestedScope);
    setFormError(task.failure_reason || "");
    setDownloadError("");
    setResultOpen(true);
    if (!LIVE_MODE) return;
    taskDetailControllerRef.current?.abort();
    const controller = new AbortController();
    taskDetailControllerRef.current = controller;
    const requestGeneration = ++taskDetailRequestGenerationRef.current;
    try {
      const detail = await studioClient.getTask(taskId, {
        ...(!isPersonalWorkspace ? { scope: requestedScope } : {}),
        signal: controller.signal,
      });
      if (requestGeneration !== taskDetailRequestGenerationRef.current) return;
      setDetailTask(detail);
    } catch (error) {
      if (error?.name === "AbortError" || requestGeneration !== taskDetailRequestGenerationRef.current) return;
      expireSessionIfNeeded(error);
      setToast(`任务详情读取失败：${readableApiError(error)}`);
    } finally {
      if (requestGeneration === taskDetailRequestGenerationRef.current) {
        taskDetailControllerRef.current = null;
      }
    }
  };

  const openArtworkTask = (artwork) =>
    openHistoryTask(
      artworkAsTask(artwork),
      artworkScope,
    );

  const prepareHistoricalTask = (
    task,
    { expanded = false, actionLabel = "再创作" } = {},
  ) => {
    if (LIVE_MODE && !canCreateTasks) {
      setToast(isPersonalWorkspace
        ? "当前个人空间未开放任务创建能力。"
        : "当前账号没有 tasks.create 任务创建权限，请联系公司老板授权。");
      return;
    }
    if (pendingCreateRef.current) {
      restorePendingCreate(pendingCreateRef.current);
      navigateStudio("create");
      return;
    }
    if (!task) return;
    const taskModel = models.find((item) => item.id === task.model_id);
    const nextDuration = Number(task.request_payload?.duration_seconds);
    const nextOutputCount = Number(task.request_payload?.output_count ?? 1);
    const nextAspectRatio = String(
      task.request_payload?.aspect_ratio ?? "",
    ).trim();
    const requestedFiles = filesFromPendingRequest(task.request_payload);
    const activeAssetsById = new Map(
      assets.map((asset) => [inputAssetId(asset), asset]),
    );
    let unavailableAssetCount = 0;
    const verifiedFiles = Object.fromEntries(
      Object.entries(requestedFiles).map(([kind, items]) => [
        kind,
        items.flatMap((item) => {
          const activeAsset = activeAssetsById.get(inputAssetId(item));
          if (activeAsset && inputAssetType(activeAsset) === kind) return [activeAsset];
          unavailableAssetCount += 1;
          return [];
        }),
      ]),
    );
    let retryMessage = `${actionLabel}草稿已恢复；确认当前参数和报价后再开始生成`;
    let retryError = "";

    if (taskModel) {
      const result = reconcileGenerationDraft(
        taskModel.effectiveCapabilities,
        task.request_payload?.mode,
        {
          prompt:
            typeof task.request_payload?.prompt === "string"
              ? task.request_payload.prompt
              : "",
          duration: nextDuration,
          outputCount: nextOutputCount,
          aspectRatio: nextAspectRatio,
          resolution: String(task.request_payload?.resolution ?? "").trim(),
          faceEnabled: Boolean(task.request_payload?.face_enabled),
          files: verifiedFiles,
        },
      );
      setModelId(taskModel.id);
      applyReconciledDraft(result, { announce: false });
      if (!result.ok) {
        retryError = result.error;
      } else if (result.changes.length || unavailableAssetCount > 0) {
        const removedCount = result.removedMediaCount + unavailableAssetCount;
        retryMessage = removedCount > 0
          ? `原任务已按当前能力恢复，${removedCount} 个停用、不可见或不再支持的素材未带入`
          : "原任务参数已按当前模型能力调整；确认当前报价后再开始生成";
      }
    } else {
      if (typeof task.request_payload?.prompt === "string") {
        setPrompt(task.request_payload.prompt);
      }
      setFiles({ image: [], video: [], audio: [] });
      setFaceEnabled(false);
      retryError = "原任务使用的模型已不可用，未恢复素材和能力参数。";
      retryMessage = retryError;
    }

    setPromptError("");
    setFormError(retryError);
    setDownloadError("");
    closeResultDialog();
    setComposerExpanded(expanded);
    navigateStudio("create");
    setToast(retryMessage);
    globalThis.requestAnimationFrame?.(() => {
      composerRef.current?.querySelector("#prompt")?.focus();
    });
  };

  const retryHistoryTask = (task) => prepareHistoricalTask(task, {
    expanded: true,
    actionLabel: "重试",
  });

  const createAgainFromTask = (task) => prepareHistoricalTask(task, {
    expanded: false,
    actionLabel: "再次生成",
  });

  const adjustHistoricalTask = (task) => prepareHistoricalTask(task, {
    expanded: true,
    actionLabel: "调整后再创作",
  });

  const openPublicationForArtifact = (artifact, requestedScope = resultTaskScope) => {
    const artifactId = String(artifact?.artifact_id || "").trim();
    const publicationScope = requestedScope === "company" ? "company" : "mine";
    if (!canStartPublication) {
      setToast(isPersonalWorkspace
        ? "个人空间发布能力尚未开放，请切换到已获授权的企业工作区。"
        : "当前账号缺少发布权限，或公司尚未启用自动发布。");
      return;
    }
    if (!artifactId) {
      setToast("该结果缺少平台归档作品标识，请从作品页刷新后再发布。");
      return;
    }
    closeResultDialog();
    setPublicationIntent({
      artifactId,
      artwork: {
        ...artifact,
        task_id: artifact.task_id || resultTask?.id || resultTask?.task_id || "",
      },
      scope: publicationScope,
      request: `${Date.now()}:${artifactId}:${makeIdempotencyKey()}`,
    });
    navigateStudio("publish");
  };

  const retryGeneration = async () => {
    navigateStudio("shots");
    if (LIVE_MODE) {
      await startGeneration();
      return;
    }
    setProgress(6);
    setStage("queued");
    setToast("任务已重新提交");
  };

  const cancelGeneration = async () => {
    if (LIVE_MODE) {
      if (!canCancelTasks) {
        setToast("个人任务取消接口尚未开放；系统不会展示或调用伪取消能力。");
        return;
      }
      if (!currentTask?.id || currentTask.status !== "queued") {
        setToast("任务可能已外发，不能安全取消或释放预留余额。");
        return;
      }
      try {
        setCancelling(true);
        const cancelled = await companyClient.cancelTask(currentTask.id);
        setCurrentTask(cancelled);
        setStage("cancelled");
        setProgress(0);
        setToast("任务已在外发前取消，预留余额已全额释放。");
      } catch (error) {
        expireSessionIfNeeded(error);
        const message = readableApiError(error);
        setFormError(message);
        setToast(message);
      } finally {
        setCancelling(false);
      }
      return;
    }
    setStage("cancelled");
    setProgress(0);
    setToast("任务已取消，未产生扣费");
  };

  const logout = async () => {
    setUserMenuOpen(false);
    if (DEMO_MODE) {
      setToast("当前是演示模式，没有真实登录会话");
      return;
    }
    pendingCreateRef.current = null;
    rememberPendingCreate(studioWorkspaceKey, null);
    closeResultDialog();
    setCurrentTask(null);
    setCurrentTaskId("");
    setDetailTask(null);
    setHistoryTasks([]);
    setArtworks([]);
    setAssets([]);
    setArtworkPreviewUrls({});
    setIssuedArtifacts({});
    setPublicationIntent(null);
    await logoutSession();
  };

  const changeSurface = (nextSurface) => {
    if (!["personal", "studio", "company", "platform"].includes(nextSurface)) return;
    if (!sessionSurfaces.includes(nextSurface)) {
      setToast("当前账号没有这个工作区的访问权限");
      return;
    }
    setNotificationsOpen(false);
    setUserMenuOpen(false);
    setSurface(nextSurface);
    const nextPath = surfacePath(nextSurface, activeNav);
    if (globalThis.location?.pathname !== nextPath) {
      globalThis.history?.pushState?.({}, "", nextPath);
    }
    try {
      globalThis.sessionStorage?.setItem("ai-video.surface", nextSurface);
    } catch {
      // Surface selection still works for the current page.
    }
  };

  const changeCompanyContext = (nextCompanyId) => {
    const company = sessionSurfaceCatalog?.companies?.find(
      (item) => item.company_id === nextCompanyId && item.status !== "deleted",
    );
    if (!company) {
      setToast("该企业不在当前登录会话的授权范围内。");
      return;
    }
    setActiveCompanyId(company.company_id);
    setSurface("studio");
    const nextPath = surfacePath("studio", activeNav);
    if (globalThis.location?.pathname !== nextPath) {
      globalThis.history?.pushState?.({}, "", nextPath);
    }
    try {
      globalThis.sessionStorage?.setItem("ai-video.company-id", company.company_id);
      globalThis.sessionStorage?.setItem("ai-video.surface", "studio");
    } catch {
      // The authorized company selection still applies in memory.
    }
  };

  const switchDemoPersona = (nextPersonaId) => {
    if (!DEMO_MODE) return;
    const nextPersona = resolveDemoPersona(nextPersonaId);
    const nextSurface = defaultSurfaceForIdentity(nextPersona.identity);
    closeResultDialog();
    setCurrentTask(null);
    setCurrentTaskId("");
    setCurrentTaskScope("mine");
    setStage("idle");
    setProgress(0);
    setHistoryTasks([]);
    setHistoryTotal(0);
    setCreationTotal(0);
    setArtworks([]);
    setArtworkTotal(0);
    setArtworkPreviewUrls({});
    setIssuedArtifacts({});
    setArtifactActionKey("");
    setPublicationIntent(null);
    setDemoPersonaId(nextPersona.id);
    setSurface(nextSurface);
    setActiveNav("shots");
    const nextPath = surfacePath(nextSurface, "shots");
    if (globalThis.location?.pathname !== nextPath) {
      globalThis.history?.pushState?.({}, "", nextPath);
    }
    setNotificationsOpen(false);
    setUserMenuOpen(false);
    setToast(`已切换演示账号：${nextPersona.label}`);
    try {
      globalThis.sessionStorage?.setItem("ai-video.demo-persona", nextPersona.id);
      globalThis.sessionStorage?.setItem("ai-video.surface", nextSurface);
    } catch {
      // The explicit demo account still changes for the current page.
    }
  };

  const accessArtifact = async (
    artifact,
    {
      taskId = resultTask?.id || resultTask?.task_id,
      scope = resultTaskScope,
      preview = false,
    } = {},
  ) => {
    if (!LIVE_MODE || !taskId || !artifact?.asset_id) {
      if (!LIVE_MODE) setToast("演示模式不会签发真实下载记录");
      return;
    }
    if (!canAccessArtifacts) {
      setToast("个人作品目前仅提供已归档元数据，预览与下载访问尚未开放。");
      return;
    }
    const key = `${taskId}:${artifact.asset_id}`;
    const action = `${preview ? "preview" : "download"}:${key}`;
    const pendingWindow = preview ? null : window.open("about:blank", "_blank");
    if (pendingWindow) {
      try {
        pendingWindow.opener = null;
        pendingWindow.document.title = "正在准备下载";
      } catch {
        // The blank window remains safe to navigate even if its title cannot be set.
      }
    }

    setArtifactActionKey(action);
    setDownloadingAssetId(artifact.asset_id);
    setDownloadError("");
    try {
      const access = preview
        ? await studioClient.getArtifactPreview(taskId, artifact.asset_id, {
            ...(!isPersonalWorkspace ? { scope } : {}),
          })
        : await studioClient.getArtifactDownload(taskId, artifact.asset_id, {
            ...(!isPersonalWorkspace ? { scope } : {}),
          });
      const url = parseArtifactDownloadUrl(access.url, {
        allowLocalHttp: Boolean(import.meta.env.DEV),
      });
      if (!preview) {
        setIssuedArtifacts((current) => ({
          ...current,
          [key]: access.download_record_id || true,
        }));
        setArtworks((current) => current.map((item) => {
          if (item.task_id !== taskId || item.asset_id !== artifact.asset_id) return item;
          const currentCount = Number(item.download_issue_count);
          return {
            ...item,
            download_status: item.downloaded ? "completed" : "issued",
            download_issue_count: Number.isFinite(currentCount) ? currentCount + 1 : item.download_issue_count,
            download_evidence_available: true,
          };
        }));
      }

      if (preview) {
        const lease = createPreviewLease(url.toString(), access.expires_seconds);
        if (!lease) {
          throw new PlatformApiError("平台返回的预览有效期无效", {
            code: "INVALID_PREVIEW_EXPIRY",
          });
        }
        setArtworkPreviewUrls((current) => ({ ...current, [key]: lease }));
      } else if (pendingWindow && !pendingWindow.closed) {
        pendingWindow.location.replace(url.toString());
      } else {
        const anchor = document.createElement("a");
        anchor.href = url.toString();
        anchor.target = "_blank";
        anchor.rel = "noopener noreferrer";
        document.body.append(anchor);
        anchor.click();
        anchor.remove();
      }
      setToast(
        preview
          ? `短时预览已签发，${access.expires_seconds} 秒内有效；不会计入下载记录`
          : `短时地址已签发，${access.expires_seconds} 秒内有效；完成状态以存储侧回传为准`,
      );
    } catch (error) {
      pendingWindow?.close();
      expireSessionIfNeeded(error);
      const message = `${preview ? "预览" : "下载地址"}获取失败：${readableApiError(error)}`;
      setDownloadError(message);
      setToast(message);
    } finally {
      setDownloadingAssetId("");
      setArtifactActionKey("");
    }
  };

  const downloadArtifact = (artifact) => accessArtifact(artifact);

  const clearArtworkPreview = (key) => {
    setArtworkPreviewUrls((current) => removePreviewLease(current, key));
  };

  const promoteTaskArtifactToInputAsset = async (
    artifact,
    {
      taskId = resultTask?.id || resultTask?.task_id,
      scope = resultTaskScope,
      addToDraft = true,
    } = {},
  ) => {
    if (!LIVE_MODE || !taskId || !artifact?.asset_id) {
      setToast("演示模式不会创建虚假的私有参考素材");
      return;
    }
    if (!canAccessArtifacts || isPersonalWorkspace) {
      setToast("个人空间尚未开放产物访问与转存素材能力，未发起任何请求。");
      return;
    }
    if (!canManageAssets) {
      setToast("当前账号没有 assets.manage 素材管理权限，请联系公司老板授权。");
      return;
    }

    const key = `${taskId}:${artifact.asset_id}`;
    setArtifactActionKey(`promote:${key}`);
    setDownloadError("");
    try {
      const promoted = await companyClient.promoteArtifactToInputAsset(
        taskId,
        artifact.asset_id,
        { scope, idempotencyKey: `promote-${taskId}-${artifact.asset_id}` },
      );
      if (promoted?.status && promoted.status !== "active") {
        setToast("这份参考素材此前已停用，未加入当前草稿。请在素材页确认后再继续。");
        return;
      }
      setAssets((current) => {
        const promotedId = inputAssetId(promoted);
        return [
          promoted,
          ...current.filter((item) => inputAssetId(item) !== promotedId),
        ];
      });

      if (addToDraft) {
        const kind = inputAssetType(promoted, artifact.media_type);
        const limit = mediaLimits[kind] ?? 0;
        if (limit > 0) {
          setFiles((current) => {
            const promotedId = inputAssetId(promoted);
            const withoutDuplicate = current[kind].filter(
              (item) => inputAssetId(item) !== promotedId,
            );
            return {
              ...current,
              [kind]: [promoted, ...withoutDuplicate].slice(0, limit),
            };
          });
        }
      }
      setToast(
        addToDraft
          ? "产物已由服务端校验并转存为私有参考素材；支持时已加入当前草稿"
          : "产物已由服务端校验并转存为私有参考素材",
      );
    } catch (error) {
      expireSessionIfNeeded(error);
      const message = `参考素材转存失败：${readableApiError(error)}`;
      setDownloadError(message);
      setToast(message);
    } finally {
      setArtifactActionKey("");
    }
  };

  const promoteArtworkToInputAsset = (artwork) => promoteTaskArtifactToInputAsset(
    artwork,
    { taskId: artwork.task_id, scope: artworkScope, addToDraft: false },
  );

  const addScene = () => {
    setToast("上传新镜头后会自动加入时间线");
  };

  const chooseScene = (id) => {
    setActiveSceneId(id);
    setPlayhead(0);
    setPlaying(false);
  };

  if (CONFIG_REQUIRED) {
    return (
      <main className="auth-gate" data-theme={skin} aria-labelledby="config-gate-title">
        <section className="auth-gate-card">
          <div className="auth-gate-brand" aria-label={BRAND_NAME}>
            <BrandLogo variant="responsive" mobileBreakpoint={620} />
          </div>
          <span className="auth-gate-icon" aria-hidden="true">
            <WarningCircle size={30} weight="fill" />
          </span>
          <span className="view-kicker">生产配置已锁定</span>
          <h1 id="config-gate-title">客户平台连接尚未配置</h1>
          <p>
            页面不会使用演示数据代替生产数据。请由部署环境配置有效的客户平台 API 地址，修复后再重新加载。
          </p>
          {platformClientConfigurationError ? (
            <p role="alert">{platformClientConfigurationError}</p>
          ) : null}
          <button type="button" onClick={() => globalThis.location?.reload?.()}>
            重新读取配置
          </button>
          <small>请联系部署管理员检查客户平台连接，修复后再重新加载。</small>
        </section>
      </main>
    );
  }

  if (LIVE_MODE && !identityResolved) {
    return (
      <main className="auth-gate" data-theme={skin} aria-labelledby="identity-loading-title">
        <section className="auth-gate-card" aria-live="polite">
          <div className="auth-gate-brand" aria-label={BRAND_NAME}>
            <BrandLogo variant="responsive" mobileBreakpoint={620} />
          </div>
          <span className="auth-gate-icon is-loading" aria-hidden="true">
            <SpinnerGap size={30} weight="bold" />
          </span>
          <span className="view-kicker">会话安全检查</span>
          <h1 id="identity-loading-title">正在确认账号身份</h1>
          <p>正在确认当前会话可访问的个人、企业与平台工作区，完成前不会开放任何数据。</p>
        </section>
      </main>
    );
  }

  if (LIVE_MODE && identityResolved && (!sessionIdentity || identityError)) {
    return (
      <main className="auth-gate" data-theme={skin} aria-labelledby="identity-error-title">
        <section className="auth-gate-card">
          <div className="auth-gate-brand" aria-label={BRAND_NAME}>
            <BrandLogo variant="responsive" mobileBreakpoint={620} />
          </div>
          <span className="auth-gate-icon" aria-hidden="true">
            <WarningCircle size={30} weight="fill" />
          </span>
          <span className="view-kicker">未开放工作区</span>
          <h1 id="identity-error-title">无法确认账号身份</h1>
          <p>{identityError || "当前账号没有可用的个人、企业或平台工作区。"}</p>
          <button type="button" onClick={() => globalThis.location?.reload?.()}>
            重新确认身份
          </button>
          <small>系统不会把身份异常的账号当作普通用户放入制作页。</small>
        </section>
      </main>
    );
  }

  if (effectiveSurface === "company" || effectiveSurface === "platform") {
    return (
      <ManagementConsole
        key={DEMO_MODE ? demoPersonaId : effectiveSurface}
        mode={effectiveSurface}
        client={effectiveSurface === "company" ? companyClient : liveClient}
        demoMode={DEMO_MODE}
        demoIdentity={DEMO_MODE ? sessionIdentity : null}
        demoPersonaId={demoPersonaId}
        allowedSurfaces={sessionSurfaces}
        onDemoPersonaChange={switchDemoPersona}
        onSurfaceChange={changeSurface}
        companyContexts={sessionSurfaceCatalog?.companies || []}
        activeCompanyId={activeCompanyId}
        onCompanyChange={changeCompanyContext}
        onLogout={logout}
        onSessionError={expireSessionIfNeeded}
        skin={skin}
        onSkinChange={setSkin}
      />
    );
  }

  const focusCommunityComposer = () => {
    navigateStudio(activeNav === "create" ? "create" : "shots");
    globalThis.requestAnimationFrame?.(() => {
      composerRef.current?.querySelector("#prompt")?.focus();
    });
  };

  const useCommunityPrompt = (nextPrompt) => {
    if (historicalRequestLocked) {
      setToast("上次提交结果尚未确认，暂时不能替换制作说明");
      focusCommunityComposer();
      return;
    }
    const safeLimit = promptLimit || 1000;
    setPrompt(nextPrompt.slice(0, safeLimit));
    setPromptError("");
    setToast("灵感制作说明已放入创作框");
    focusCommunityComposer();
  };

  const composerMediaKind = generationMode === "text_to_image" ? "image" : "video";
  const modeMatchesMediaKind = (mode, kind) =>
    kind === "image" ? mode === "text_to_image" : mode !== "text_to_image";
  const modelSupportsMediaKind = (candidate, kind) =>
    Object.keys(candidate?.effectiveCapabilities?.modes ?? {})
      .some((mode) => modeMatchesMediaKind(mode, kind));
  const composerVideoAvailable = models.some((item) => modelSupportsMediaKind(item, "video"));
  const composerImageAvailable = models.some((item) => modelSupportsMediaKind(item, "image"));

  const selectComposerMediaKind = (nextKind) => {
    if (pendingCreateRef.current) return false;
    const currentMode = supportedModes.find((mode) => modeMatchesMediaKind(mode, nextKind));
    if (currentMode) {
      selectGenerationMode(currentMode);
      return true;
    }
    const nextModel = models.find((item) => modelSupportsMediaKind(item, nextKind));
    if (!nextModel) {
      setToast(`当前账号没有可用的${nextKind === "image" ? "图片" : "视频"}生成模型`);
      return false;
    }
    const nextMode = Object.keys(nextModel.effectiveCapabilities?.modes ?? {})
      .find((mode) => modeMatchesMediaKind(mode, nextKind));
    const result = reconcileGenerationDraft(
      nextModel.effectiveCapabilities,
      nextMode,
      studioDraft(),
    );
    setModelId(nextModel.id);
    applyReconciledDraft(result, { announce: false });
    setPromptError("");
    setFormError(result.ok ? "" : result.error);
    setToast(
      result.ok
        ? `已切换到 ${nextModel.name} 的${nextKind === "image" ? "图片" : "视频"}创作`
        : result.error,
    );
    return result.ok;
  };

  const handleComposerMediaKeyDown = (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;

    const tabs = Array.from(
      event.currentTarget.querySelectorAll('[role="tab"]:not(:disabled)'),
    );
    if (tabs.length === 0) return;

    const focusedTab = event.target.closest?.('[role="tab"]');
    const currentIndex = Math.max(0, tabs.indexOf(focusedTab));
    let nextIndex = currentIndex;
    if (event.key === "ArrowRight") nextIndex = (currentIndex + 1) % tabs.length;
    if (event.key === "ArrowLeft") nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = tabs.length - 1;

    event.preventDefault();
    tabs[nextIndex].click();
    tabs[nextIndex].focus();
  };

  const currentView = (() => {
    if (activeNav === "create") {
      return (
        <CreationHub
          tasks={LIVE_MODE ? historyTasks : DEMO_HISTORY_TASKS}
          models={models}
          loading={LIVE_MODE && historyLoading}
          error={historyError}
          liveMode={LIVE_MODE}
          generationMediaKind={composerMediaKind}
          onGenerationMediaChange={selectComposerMediaKind}
          onMediaFilterChange={(nextMediaType) => {
            setCreationMediaType(nextMediaType);
            setCreationPage(1);
          }}
          onOpenTask={openHistoryTask}
          onOpenHistory={() => navigateStudio("history")}
          statusFilter={creationStatus}
          onStatusFilterChange={(nextStatus) => {
            setCreationStatus(nextStatus);
            setCreationPage(1);
          }}
          days={creationDays}
          onDaysChange={(nextDays) => {
            setCreationDays(nextDays);
            setCreationPage(1);
          }}
          modelId={creationModelId}
          onModelIdChange={(nextModelId) => {
            setCreationModelId(nextModelId);
            setCreationPage(1);
          }}
          onQueryChange={(nextQuery) => {
            setCreationQuery(nextQuery);
            setCreationPage(1);
          }}
          page={LIVE_MODE ? creationPage : 1}
          pageSize={COLLECTION_PAGE_SIZE}
          total={LIVE_MODE ? creationTotal : DEMO_HISTORY_TASKS.length}
          onPageChange={setCreationPage}
          previewUrls={artworkPreviewUrls}
          previewActionKey={artifactActionKey}
          onPreviewError={clearArtworkPreview}
          onRequestPreview={(task, artifact) => accessArtifact(artifact, {
            taskId: task.id || task.task_id,
            scope: "mine",
            preview: true,
          })}
          supportsSearch={!isPersonalWorkspace}
          supportsDateFilter={!isPersonalWorkspace}
          artifactAccessAvailable={canAccessArtifacts}
          onStartCreation={focusCommunityComposer}
        />
      );
    }
    if (activeNav === "shots") {
      return (
        <CommunityHome
          client={LIVE_MODE ? liveClient : null}
          liveMode={LIVE_MODE}
          onUsePrompt={useCommunityPrompt}
          onFocusComposer={focusCommunityComposer}
        />
      );
    }
    if (LIVE_MODE && activeNav === "shots") {
      return (
        <div className="live-editor-view">
          <section className="live-request-canvas" aria-labelledby="live-request-title">
            <header className="live-request-header">
              <div>
                <span className="view-kicker">真实任务输入</span>
                <h1 id="live-request-title">
                  {generationMode ? generationModeLabel(generationMode) : "等待模型能力"}
                </h1>
                <p>
                  {isPersonalWorkspace
                    ? "中央画布只展示个人 API 会接收的真实字段；当前仅开放无素材文生能力。"
                    : "中央画布只展示会提交到客户平台的真实字段；素材先进入公司私有素材库，再以受控引用参与生成。"}
                </p>
              </div>
              <span className="live-request-icon" aria-hidden="true">
                <GenerationIcon size={29} weight="fill" />
              </span>
            </header>
            {pendingCreateRef.current && (
              <div className="pending-create-notice" role="status">
                <ClockCounterClockwise size={19} aria-hidden="true" />
                <span>
                  <strong>有一条提交结果尚未确认</strong>
                  <small>原参数与幂等键已按当前工作空间保留；再次生成只会确认同一任务。</small>
                </span>
              </div>
            )}
            <dl className="live-request-summary">
              <div className="live-summary-prompt">
                <dt>制作说明</dt>
                <dd>{prompt.trim() || "尚未填写，请在右侧输入制作说明"}</dd>
              </div>
              <div>
                <dt>授权模型</dt>
                <dd>{model.id ? model.name : "尚未选择"}</dd>
              </div>
              <div>
                <dt>生成模式</dt>
                <dd>{generationMode ? generationModeLabel(generationMode) : "不可用"}</dd>
              </div>
              <div>
                <dt>画面比例</dt>
                <dd>{ratio ? ratio.split(/\s/)[0] : "不可用"}</dd>
              </div>
              <div>
                <dt>分辨率</dt>
                <dd>{resolution || "不可用"}</dd>
              </div>
              <div>
                <dt>目标时长</dt>
                <dd>{duration ? `${duration} 秒` : "不可用"}</dd>
              </div>
              <div>
                <dt>产物数量</dt>
                <dd>{outputCount ? `${outputCount} 个` : "不可用"}</dd>
              </div>
              {showFaceSummary && (
                <div>
                  <dt>人脸能力</dt>
                  <dd>
                    {faceEnabled ? "启用" : "关闭"}
                    {historicalFaceVisible && !faceControlAvailable ? "（历史提交，只读）" : ""}
                  </dd>
                </div>
              )}
            </dl>
          </section>
          <div className="save-strip">
            <span className="save-icon">
              <StoredStateIcon size={18} weight="bold" aria-hidden="true" />
            </span>
            <span>
              <strong>{storedStateTitle}</strong>
              <small>
                {hasStoredArtifacts
                  ? `${activeOutputArtifacts.length} 个产物 · 私有存储`
                  : "真实任务与产物状态以客户平台记录为准"}
              </small>
            </span>
            <button className="folder-button" type="button" onClick={() => navigateStudio("history")}>
              查看历史
              <FolderOpen size={19} aria-hidden="true" />
            </button>
          </div>
        </div>
      );
    }
    if (activeNav === "media") {
      if (isPersonalWorkspace && !canManageAssets) {
        return (
          <WorkspaceCapabilityUnavailableView
            capability="素材"
            description="个人空间本期仅开放无素材的文生视频与文生图片；上传、素材库与素材引用尚未开放。"
          />
        );
      }
      return (
        <MediaLibrary
          liveMode={LIVE_MODE}
          assets={assets}
          loading={assetsLoading}
          error={assetsError}
          uploading={uploadingKind === "library"}
          canManageAssets={canManageAssets}
          canCreateTasks={canCreateTasks}
          onUpload={uploadLibraryFiles}
          onAdd={addLibraryAssetToTask}
          onPreview={previewLibraryAsset}
          onDelete={deleteLibraryAsset}
          onUse={(id) => {
            chooseScene(id);
            setToast("已设为当前镜头，可继续在素材库中选择");
          }}
        />
      );
    }
    if (activeNav === "history") {
      return (
        <HistoryView
          onRetry={LIVE_MODE ? retryHistoryTask : retryGeneration}
          liveMode={LIVE_MODE}
          tasks={historyTasks}
          demoTasks={DEMO_HISTORY_TASKS}
          models={models}
          loading={historyLoading}
          error={historyError}
          onOpen={openHistoryTask}
          canCreateTasks={canCreateTasks}
          canViewCompany={canViewCompanyRecords}
          scope={historyScope}
          onScopeChange={(nextScope) => {
            setHistoryScope(nextScope);
            setHistoryPage(1);
          }}
          statusFilter={historyStatus}
          onStatusChange={(nextStatus) => {
            setHistoryStatus(nextStatus);
            setHistoryPage(1);
          }}
          page={historyPage}
          pageSize={COLLECTION_PAGE_SIZE}
          total={LIVE_MODE ? historyTotal : DEMO_HISTORY_TASKS.length}
          onPageChange={setHistoryPage}
          companyName={companyName}
          currentUserId={sessionIdentity?.user_id}
          currentUserName={sessionIdentity?.display_name}
          workspaceKind={isPersonalWorkspace ? "personal" : "company"}
          statusDefinitions={STATUS}
        />
      );
    }
    if (activeNav === "artworks") {
      return (
        <ArtworksView
          liveMode={LIVE_MODE}
          artworks={artworks}
          demoArtworks={DEMO_ARTWORKS}
          loading={artworksLoading}
          error={artworksError}
          canViewCompany={canViewCompanyRecords}
          scope={artworkScope}
          onScopeChange={(nextScope) => {
            setArtworkScope(nextScope);
            setArtworkPage(1);
          }}
          mediaFilter={artworkMediaFilter}
          onMediaFilterChange={(nextFilter) => {
            setArtworkMediaFilter(nextFilter);
            setArtworkPage(1);
          }}
          downloadFilter={artworkDownloadFilter}
          onDownloadFilterChange={(nextFilter) => {
            setArtworkDownloadFilter(nextFilter);
            setArtworkPage(1);
          }}
          page={artworkPage}
          pageSize={COLLECTION_PAGE_SIZE}
          total={LIVE_MODE ? artworkTotal : DEMO_ARTWORKS.length}
          onPageChange={setArtworkPage}
          previewUrls={artworkPreviewUrls}
          issuedArtifacts={issuedArtifacts}
          actionKey={artifactActionKey}
          companyName={companyName}
          canCreateTasks={canCreateTasks}
          canPublish={canStartPublication}
          canPromoteArtifacts={LIVE_MODE && canManageAssets}
          artifactAccessAvailable={canAccessArtifacts}
          supportsDownloadFilter={!isPersonalWorkspace}
          workspaceKind={isPersonalWorkspace ? "personal" : "company"}
          currentUserName={sessionIdentity?.display_name}
          onPreview={(artwork) => accessArtifact(artwork, {
            taskId: artwork.task_id,
            scope: artworkScope,
            preview: true,
          })}
          onPreviewError={clearArtworkPreview}
          onDownload={(artwork) => accessArtifact(artwork, {
            taskId: artwork.task_id,
            scope: artworkScope,
          })}
          onOpenTask={openArtworkTask}
          onCreateAgain={(artwork) => createAgainFromTask(artworkAsTask(artwork))}
          onAdjust={(artwork) => adjustHistoricalTask(artworkAsTask(artwork))}
          onPublish={(artwork) => openPublicationForArtifact(artwork, artworkScope)}
          onPromote={promoteArtworkToInputAsset}
        />
      );
    }
    if (activeNav === "publish") {
      if (isPersonalWorkspace) {
        return (
          <WorkspaceCapabilityUnavailableView
            capability="发布"
            description="个人空间的发布账号、审批与外部平台提交尚未开放；切换到具备发布权限的企业工作区后可使用完整流程。"
          />
        );
      }
      return (
        <PublishingCenter
          client={companyClient}
          demoMode={DEMO_MODE}
          artworks={DEMO_MODE ? DEMO_ARTWORKS : artworks}
          artworksLoading={LIVE_MODE && artworksLoading}
          artworksError={artworksError}
          canReadAccounts={canReadPublisherAccounts}
          canManageAccounts={canManagePublisherAccounts}
          canReadJobs={canReadPublicationJobs}
          canManageJobs={canManagePublicationJobs}
          autoPublishingEnabled={hasAutoPublishEntitlement}
          publishingEntitlementResolved={DEMO_MODE || resourcesResolved}
          onSessionError={expireSessionIfNeeded}
          initialArtifactId={publicationIntent?.artifactId || ""}
          initialArtwork={publicationIntent?.artwork ?? null}
          initialArtworkScope={publicationIntent?.scope || "mine"}
          openComposerRequest={publicationIntent?.request ?? null}
          previewUrls={artworkPreviewUrls}
          previewActionKey={artifactActionKey}
          onPreviewError={clearArtworkPreview}
          onRequestArtworkPreview={(artwork, scope = "mine") => accessArtifact(artwork, {
            taskId: artwork.task_id,
            scope,
            preview: true,
          })}
        />
      );
    }
    if (activeNav === "settings") {
      return (
        <AccountCenter
          demoMode={DEMO_MODE}
          demoIdentity={DEMO_MODE ? sessionIdentity : null}
          taskCompletionNotices={taskCompletionNotices}
          onTaskCompletionNoticesChange={updateTaskCompletionNotices}
        />
      );
    }
    return (
      <div className="editor-view">
        <Preview
          scene={activeScene}
          duration={duration}
          playing={playing}
          onTogglePlay={() => setPlaying((value) => !value)}
          playhead={playhead}
          onSeek={setPlayhead}
        />
        <SceneTimeline
          scenes={SCENES}
          activeId={activeSceneId}
          onSelect={chooseScene}
          onAdd={addScene}
        />
        <div className="save-strip">
          <span className="save-icon">
            <StoredStateIcon size={18} weight="bold" aria-hidden="true" />
          </span>
          <span>
            <strong>{storedStateTitle}</strong>
            <small>
              {LIVE_MODE
                ? hasStoredArtifacts
                  ? `${activeOutputArtifacts.length} 个产物 · 私有存储`
                  : "真实任务与产物状态以客户平台记录为准"
                : "公司存储 / 产品推广 / 当前版本"}
            </small>
          </span>
          <button
            className="folder-button"
            type="button"
            onClick={() => {
              if (LIVE_MODE) {
                navigateStudio("history");
              } else {
                setToast("已打开当前项目产物列表");
              }
            }}
          >
            {LIVE_MODE ? "查看历史" : "打开文件夹"}
            <FolderOpen size={19} aria-hidden="true" />
          </button>
        </div>
      </div>
    );
  })();

  const isPrimaryStudioView = activeNav === "shots" || activeNav === "create";

  return (
    <div
      className={`app-shell ${isPrimaryStudioView ? "is-community-home" : "is-secondary-page"} ${activeNav === "create" ? "is-creation-hub" : ""} ${composerExpanded ? "is-composer-expanded" : ""}`}
      data-theme={skin}
    >
      <header className="topbar">
        <div className="brand" aria-label={BRAND_NAME}>
          <BrandLogo variant="responsive" />
        </div>
        <span className="brand-context">
          {isPersonalWorkspace ? "个人创作空间" : "企业创作工作台"}
        </span>
        <span className={`mode-badge ${LIVE_MODE ? "is-live" : ""}`}>
          {LIVE_MODE ? (isPersonalWorkspace ? "个人 API" : "真实 API") : "演示模式"}
        </span>
        {DEMO_MODE && (
          <DemoAccountSwitcher value={demoPersonaId} onChange={switchDemoPersona} />
        )}
        {sessionSurfaces.length > 1 && (
          <div className="surface-switch is-studio" aria-label="工作区切换">
            {sessionSurfaces.includes("personal") && (
              <button
                className={effectiveSurface === "personal" ? "is-active" : ""}
                type="button"
                aria-pressed={effectiveSurface === "personal"}
                onClick={() => changeSurface("personal")}
              >个人</button>
            )}
            {sessionSurfaces.includes("studio") && (
              <button
                className={effectiveSurface === "studio" ? "is-active" : ""}
                type="button"
                aria-pressed={effectiveSurface === "studio"}
                aria-label="企业创作工作区"
                onClick={() => changeSurface("studio")}
              ><span className="surface-label-long">企业创作</span><span className="surface-label-short" aria-hidden="true">企业</span></button>
            )}
            {sessionSurfaces.includes("company") && <button type="button" aria-pressed="false" aria-label="企业管理工作区" onClick={() => changeSurface("company")}><span className="surface-label-long">企业管理</span><span className="surface-label-short" aria-hidden="true">管理</span></button>}
            {sessionSurfaces.includes("platform") && <button type="button" aria-pressed="false" onClick={() => changeSurface("platform")}>平台</button>}
          </div>
        )}
        {LIVE_MODE ? (
          <label className="project-select workspace-select">
            <span className="visually-hidden">当前工作空间</span>
            <select
              value={isPersonalWorkspace ? "personal" : activeCompanyId}
              onChange={(event) => {
                if (event.target.value === "personal") changeSurface("personal");
                else changeCompanyContext(event.target.value);
              }}
            >
              {sessionSurfaceCatalog?.personal && <option value="personal">个人空间</option>}
              {(sessionSurfaceCatalog?.companies || []).map((company) => (
                <option key={company.company_id} value={company.company_id}>
                  {company.name || company.company_id}
                </option>
              ))}
              {!sessionSurfaceCatalog && hasCompanySession && (
                <option value={activeCompanyId}>{companyName || "企业工作区"}</option>
              )}
            </select>
            <CaretDown size={14} aria-hidden="true" />
          </label>
        ) : (
          <label className="project-select">
            <span className="visually-hidden">当前项目</span>
            <select defaultValue="产品推广视频">
              <option>产品推广视频</option>
              <option>夏季带货系列</option>
              <option>品牌素材测试</option>
            </select>
            <CaretDown size={14} aria-hidden="true" />
          </label>
        )}
        {LIVE_MODE && isPersonalWorkspace && (
          <span
            className={`personal-balance ${personalWalletError ? "is-error" : ""}`}
            title={personalWalletError || `预留 ${Number(personalWallet?.reserved_points || 0)} 积分`}
          >
            {personalWalletError
              ? "积分读取失败"
              : personalWallet
                ? `${Number(personalWallet.available_points || 0)} 积分`
                : "积分读取中"}
          </span>
        )}
        <div className="topbar-spacer" />
        <SkinSwitcher value={skin} onChange={setSkin} />
        <div className="popover-anchor notification-anchor" ref={notificationAnchorRef}>
          <IconButton
            label="通知"
            onClick={() => {
              setNotificationsOpen((value) => !value);
              setUserMenuOpen(false);
            }}
            aria-expanded={notificationsOpen}
          >
            <Bell size={20} aria-hidden="true" />
          </IconButton>
          {notificationsOpen && (
            <div className="popover notification-popover" role="dialog" aria-label="任务提醒">
              <strong>任务提醒</strong>
              {LIVE_MODE ? (
                currentTask ? (
                  <>
                    <p>
                      任务 {shortId(currentTask.id)} · {taskStatus.label}
                    </p>
                    <p>
                      {hasStoredArtifacts
                        ? `${activeOutputArtifacts.length} 个产物已完成转存。`
                        : "状态来自客户平台，页面会继续同步。"}
                    </p>
                  </>
                ) : (
                  <p>当前没有选中的真实任务。</p>
                )
              ) : (
                <>
                  <p>“产品防水演示”正在渲染。</p>
                  <p>演示数据不会连接真实生成渠道。</p>
                </>
              )}
            </div>
          )}
        </div>
        {LIVE_MODE && (
        <div className="popover-anchor user-anchor" ref={userAnchorRef}>
          <button
            className="user-button"
            type="button"
            aria-label={`${sessionIdentity?.display_name || "已登录用户"} · ${identityRoleLabel(sessionIdentity)} · 账号菜单`}
            onClick={() => {
              setUserMenuOpen((value) => !value);
              setNotificationsOpen(false);
            }}
            aria-expanded={userMenuOpen}
          >
            <UserCircle size={27} weight="fill" aria-hidden="true" />
            <span>{sessionIdentity?.display_name || "已登录用户"} · {identityRoleLabel(sessionIdentity)}</span>
            <CaretDown size={13} aria-hidden="true" />
          </button>
          {userMenuOpen && (
            <div className="popover user-popover" role="menu" aria-label="账号操作">
              <button role="menuitem" type="button" onClick={() => navigateStudio("settings")}>
                账号设置
              </button>
              <button role="menuitem" type="button" onClick={logout}>
                退出登录
              </button>
            </div>
          )}
        </div>
        )}
      </header>

      <nav className="side-nav" aria-label="工作台导航">
        <div>
          {NAV_ITEMS.filter((item) => item.id !== "publish" || showPublishingNavigation).map((item) => {
            const Icon = item.icon;
            return (
              <button
                className={activeNav === item.id ? "is-active" : ""}
                type="button"
                key={item.id}
                onClick={() => navigateStudio(item.id)}
                aria-current={activeNav === item.id ? "page" : undefined}
              >
                <Icon size={26} aria-hidden="true" />
                <span>{item.label}</span>
              </button>
            );
          })}
        </div>
        <button
          className={activeNav === "settings" ? "is-active" : ""}
          type="button"
          onClick={() => navigateStudio("settings")}
          aria-current={activeNav === "settings" ? "page" : undefined}
        >
          <Gear size={27} aria-hidden="true" />
          <span>设置</span>
        </button>
      </nav>

      <main ref={mainCanvasRef} className="main-canvas">{currentView}</main>

      {isPrimaryStudioView && (
      <aside
        className={`inspector community-composer ${composerExpanded ? "is-expanded" : ""}`}
        aria-label="生成参数"
        ref={composerRef}
      >
        <header className="community-composer-header">
          <div
            className="composer-media-tabs"
            role="tablist"
            aria-label="生成内容类型"
            onKeyDown={handleComposerMediaKeyDown}
          >
            <button
              id="composer-media-tab-video"
              type="button"
              role="tab"
              aria-selected={composerMediaKind === "video"}
              aria-controls="composer-parameters-panel"
              tabIndex={composerMediaKind === "video" ? 0 : -1}
              className={composerMediaKind === "video" ? "is-active" : ""}
              disabled={!composerVideoAvailable || Boolean(pendingCreateRef.current)}
              onClick={() => selectComposerMediaKind("video")}
            >
              <VideoCamera size={16} aria-hidden="true" />
              视频
            </button>
            <button
              id="composer-media-tab-image"
              type="button"
              role="tab"
              aria-selected={composerMediaKind === "image"}
              aria-controls="composer-parameters-panel"
              tabIndex={composerMediaKind === "image" ? 0 : -1}
              className={composerMediaKind === "image" ? "is-active" : ""}
              disabled={!composerImageAvailable || Boolean(pendingCreateRef.current)}
              onClick={() => selectComposerMediaKind("image")}
            >
              <ImageSquare size={16} aria-hidden="true" />
              图片
            </button>
            <button
              id="composer-media-tab-audio"
              type="button"
              role="tab"
              aria-selected="false"
              aria-controls="composer-parameters-panel"
              tabIndex={-1}
              disabled
              title="音频生成能力尚未开放"
            >
              <MusicNote size={16} aria-hidden="true" />
              音频
            </button>
          </div>
          <span className="composer-current-mode">
            {generationMode ? generationModeLabel(generationMode) : "等待模型能力"}
          </span>
          <button
            className="composer-settings-button"
            type="button"
            aria-expanded={composerExpanded}
            onClick={() => setComposerExpanded((value) => !value)}
          >
            <SlidersHorizontal size={15} aria-hidden="true" />
            {composerExpanded ? "收起设置" : "详细设置"}
            <CaretDown size={13} aria-hidden="true" />
          </button>
        </header>
        <div
          id="composer-parameters-panel"
          className="inspector-scroll"
          role="tabpanel"
          aria-labelledby={`composer-media-tab-${composerMediaKind}`}
        >
          <section className="inspector-section model-section">
            <div className="model-heading">
              <label htmlFor="model">模型</label>
              {modelsLoading && <span>{isPersonalWorkspace ? "正在读取个人零售模型…" : "正在读取公司授权…"}</span>}
            </div>
            <div className="select-wrap">
              <select
                id="model"
                value={modelId}
                disabled={
                  modelsLoading ||
                  models.length === 0 ||
                  Boolean(pendingCreateRef.current)
                }
                onChange={(event) => selectStudioModel(event.target.value)}
              >
                {modelsLoading && <option value="">加载中…</option>}
                {!modelsLoading && models.length === 0 && (
                  <option value="">暂无已授权模型</option>
                )}
                {modelId && !models.some((item) => item.id === modelId) && (
                  <option value={modelId}>历史授权模型 · 仅用于确认原提交</option>
                )}
                {models.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name} · {item.tier}
                  </option>
                ))}
              </select>
              <CaretDown size={15} aria-hidden="true" />
            </div>
            {modelsLoading ? (
              <div className="capability-skeleton" role="status" aria-label="正在加载模型能力">
                <span />
                <span />
                <span />
              </div>
            ) : modelsError ? (
              <p className="inline-state is-error">
                <WarningCircle size={15} weight="fill" aria-hidden="true" />
                {modelsError}
              </p>
            ) : models.length === 0 && !modelsLoading ? (
              <p className="inline-state is-error">
                <WarningCircle size={15} weight="fill" aria-hidden="true" />
                {isPersonalWorkspace
                  ? "个人空间当前没有可用的零售模型。"
                  : "公司当前没有已授权模型，请联系管理员配置。"}
              </p>
            ) : historicalRequestLocked ? (
              <p className="inline-state is-locked">
                <ClockCounterClockwise size={15} weight="fill" aria-hidden="true" />
                当前仅展示上次未确认提交的原始参数，所有字段均为只读。
              </p>
            ) : !activeCapability ? (
              <p className="inline-state is-error">
                <WarningCircle size={15} weight="fill" aria-hidden="true" />
                当前模型的能力声明无效，制作台已关闭全部输入。
              </p>
            ) : (
              <p className="capability-note">
                支持 {supportedModes.map(generationModeLabel).join("、") || "未声明模式"}。
                当前模式最多使用 {mediaLimits.image} 张图片、{mediaLimits.video} 个视频、
                {mediaLimits.audio} 段音频，单次最多 {Math.max(...activeCapability.limits.outputCounts)} 个产物。
                {activeCapability.supportsFace ? " 支持人脸能力。" : ""}
              </p>
            )}
            <label className="mode-select-label" htmlFor="generation-mode">生成模式</label>
            <div className="select-wrap">
              <select
                id="generation-mode"
                value={generationMode}
                disabled={
                  modelsLoading ||
                  supportedModes.length === 0 ||
                  Boolean(pendingCreateRef.current)
                }
                onChange={(event) => selectGenerationMode(event.target.value)}
              >
                {generationMode && !supportedModes.includes(generationMode) && (
                  <option value={generationMode}>历史提交模式 · {generationModeLabel(generationMode)}</option>
                )}
                {supportedModes.map((item) => (
                  <option key={item} value={item}>{generationModeLabel(item)}</option>
                ))}
              </select>
              <CaretDown size={15} aria-hidden="true" />
            </div>
            {LIVE_MODE && (
              <p className="live-mode-note">
                {isPersonalWorkspace
                  ? "已连接个人 API。个人积分、任务与企业钱包完全隔离；本期不提交任何素材引用。"
                  : "已连接客户平台。文件会先上传到公司私有素材库，任务只提交经公司隔离校验的素材引用。"}
              </p>
            )}
          </section>

          <section className="inspector-section composer-prompt-section">
            <div
              className="composer-mode-rail"
              role="tablist"
              aria-label="生成模式"
              onKeyDown={handleComposerMediaKeyDown}
            >
              {supportedModes.map((item) => (
                <button
                  key={item}
                  type="button"
                  role="tab"
                  aria-selected={generationMode === item}
                  aria-controls="prompt"
                  tabIndex={generationMode === item ? 0 : -1}
                  className={generationMode === item ? "is-active" : ""}
                  disabled={Boolean(pendingCreateRef.current)}
                  onClick={() => selectGenerationMode(item)}
                >
                  {generationModeLabel(item)}
                </button>
              ))}
            </div>
            <div className="field-heading">
              <label htmlFor="prompt">制作说明</label>
              <span>
                {historicalRequestLocked
                  ? `${prompt.length} 字，只读`
                  : `${prompt.length} / ${promptLimit}`}
              </span>
            </div>
            <div className="composer-prompt-row">
              {!composerExpanded && (
                <button
                  className="composer-add-media"
                  type="button"
                  onClick={() => setComposerExpanded(true)}
                  disabled={historicalRequestLocked || !activeCapability}
                  aria-label="打开素材和高级设置"
                  title="打开素材和高级设置"
                >
                  <Plus size={20} aria-hidden="true" />
                  <span>素材</span>
                </button>
              )}
              <textarea
                id="prompt"
                value={prompt}
                maxLength={promptLimit || undefined}
                placeholder="描述您想要创作的内容"
                disabled={historicalRequestLocked || !activeCapability}
                onChange={(event) => {
                  setPrompt(event.target.value);
                  if (promptError) setPromptError("");
                }}
                aria-invalid={Boolean(promptError)}
                aria-describedby={promptError ? "prompt-error" : undefined}
              />
            </div>
            {promptError && (
              <p className="form-error" id="prompt-error" role="alert">
                <WarningCircle size={16} weight="fill" aria-hidden="true" />
                {promptError}
              </p>
            )}
          </section>

          {Object.values(visibleMediaLimits).some((maximum) => maximum > 0) && (
          <section className="inspector-section media-groups">
            {visibleMediaLimits.image > 0 && (
              <MediaInputGroup
                kind="image"
                label="参考图"
                limit={visibleMediaLimits.image}
                accept="image/*"
                files={files.image}
                onFiles={(incoming) => handleFiles("image", incoming)}
                onRemove={(file) => removeInputAsset("image", file)}
                uploading={uploadingKind === "image"}
                uploadDisabled={Boolean(pendingCreateRef.current) || (LIVE_MODE && !canManageAssets)}
                locked={Boolean(pendingCreateRef.current)}
                disabledReason={
                  pendingCreateRef.current
                    ? "原提交尚未确认，素材已锁定。"
                    : LIVE_MODE && !canManageAssets
                    ? "缺少 assets.manage 权限，只能选用素材库中已有素材。"
                    : ""
                }
                icon={ImageSquare}
              />
            )}
            {visibleMediaLimits.video > 0 && (
              <MediaInputGroup
                kind="video"
                label="参考视频"
                limit={visibleMediaLimits.video}
                accept="video/*"
                files={files.video}
                onFiles={(incoming) => handleFiles("video", incoming)}
                onRemove={(file) => removeInputAsset("video", file)}
                uploading={uploadingKind === "video"}
                uploadDisabled={Boolean(pendingCreateRef.current) || (LIVE_MODE && !canManageAssets)}
                locked={Boolean(pendingCreateRef.current)}
                disabledReason={
                  pendingCreateRef.current
                    ? "原提交尚未确认，素材已锁定。"
                    : LIVE_MODE && !canManageAssets
                    ? "缺少 assets.manage 权限，只能选用素材库中已有素材。"
                    : ""
                }
                icon={VideoCamera}
              />
            )}
            {visibleMediaLimits.audio > 0 && (
              <MediaInputGroup
                kind="audio"
                label="参考音频"
                limit={visibleMediaLimits.audio}
                accept="audio/*"
                files={files.audio}
                onFiles={(incoming) => handleFiles("audio", incoming)}
                onRemove={(file) => removeInputAsset("audio", file)}
                uploading={uploadingKind === "audio"}
                uploadDisabled={Boolean(pendingCreateRef.current) || (LIVE_MODE && !canManageAssets)}
                locked={Boolean(pendingCreateRef.current)}
                disabledReason={
                  pendingCreateRef.current
                    ? "原提交尚未确认，素材已锁定。"
                    : LIVE_MODE && !canManageAssets
                    ? "缺少 assets.manage 权限，只能选用素材库中已有素材。"
                    : ""
                }
                icon={MusicNote}
              />
            )}
          </section>
          )}

          {historicalRequestLocked ? (
            <section className="inspector-section historical-parameters" aria-labelledby="historical-parameters-title">
              <div className="field-heading">
                <strong id="historical-parameters-title">历史提交参数</strong>
                <span>只读</span>
              </div>
              <dl>
                <div><dt>比例</dt><dd>{ratio || "未记录"}</dd></div>
                <div><dt>分辨率</dt><dd>{resolution || "未记录"}</dd></div>
                <div><dt>时长</dt><dd>{duration ? `${duration} 秒` : "未记录"}</dd></div>
                <div><dt>产物数</dt><dd>{outputCount ? `${outputCount} 个` : "未记录"}</dd></div>
                {historicalFaceVisible && (
                  <div className={faceEnabled && !faceControlAvailable ? "is-warning" : ""}>
                    <dt>人脸能力</dt>
                    <dd>
                      {faceEnabled ? "已启用" : "已关闭"}
                      {faceEnabled && !faceControlAvailable
                        ? "，当前能力已不支持，仅按原提交确认"
                        : "，历史提交值"}
                    </dd>
                  </div>
                )}
              </dl>
            </section>
          ) : activeCapability ? (
          <section className="inspector-section compact-fields">
            <label>
              <span>比例</span>
              <div className="select-wrap">
                <select
                  value={ratio}
                  disabled={Boolean(pendingCreateRef.current)}
                  onChange={(event) => setRatio(event.target.value)}
                >
                  {!activeCapability.limits.aspectRatios.includes(ratio) && (
                    <option value={ratio}>{ratio} · 历史提交值</option>
                  )}
                  {activeCapability.limits.aspectRatios.map((item) => (
                    <option key={item}>{item}</option>
                  ))}
                </select>
                <CaretDown size={15} aria-hidden="true" />
              </div>
            </label>
            <label>
              <span>分辨率</span>
              <div className="select-wrap">
                <select
                  value={resolution}
                  disabled={Boolean(pendingCreateRef.current)}
                  onChange={(event) => setResolution(event.target.value)}
                >
                  {!activeCapability.limits.resolutions.includes(resolution) && (
                    <option value={resolution}>{resolution} · 历史提交值</option>
                  )}
                  {activeCapability.limits.resolutions.map((item) => (
                    <option key={item}>{item}</option>
                  ))}
                </select>
                <CaretDown size={15} aria-hidden="true" />
              </div>
            </label>
            <label>
              <span>时长</span>
              <div className="select-wrap">
                <select
                  value={duration}
                  disabled={Boolean(pendingCreateRef.current)}
                  onChange={(event) => setDuration(Number(event.target.value))}
                >
                  {!activeCapability.limits.durations.includes(duration) && (
                    <option value={duration}>{duration} 秒 · 历史提交值</option>
                  )}
                  {activeCapability.limits.durations.map((item) => (
                    <option key={item} value={item}>
                      {item} 秒
                    </option>
                  ))}
                </select>
                <CaretDown size={15} aria-hidden="true" />
              </div>
            </label>
            <label>
              <span>产物数</span>
              <div className="select-wrap">
                <select
                  value={outputCount}
                  disabled={Boolean(pendingCreateRef.current)}
                  onChange={(event) => setOutputCount(Number(event.target.value))}
                >
                  {!activeCapability.limits.outputCounts.includes(outputCount) && (
                    <option value={outputCount}>{outputCount} 个 · 历史提交值</option>
                  )}
                  {activeCapability.limits.outputCounts.map((item) => (
                    <option key={item} value={item}>
                      {item} 个
                    </option>
                  ))}
                </select>
                <CaretDown size={15} aria-hidden="true" />
              </div>
            </label>
          </section>
          ) : null}
          {!historicalRequestLocked && faceControlAvailable && (
            <section className="inspector-section face-control">
              <label>
                <span>
                  <strong>启用人脸能力</strong>
                  <small>仅在当前模型和模式明确支持时可用。</small>
                </span>
                <input
                  type="checkbox"
                  checked={faceEnabled}
                  onChange={(event) => setFaceEnabled(event.target.checked)}
                  disabled={Boolean(pendingCreateRef.current)}
                />
              </label>
              {activeCapability.requiredResourceKeys.length > 0 && (
                <small className="required-resource-note">
                  所需授权：{activeCapability.requiredResourceKeys.join("、")}
                </small>
              )}
            </section>
          )}
        </div>

        <div className="inspector-actions">
          {LIVE_MODE && (!identityReady || !canCreateTasks) && (
            <p className="permission-note" role="note">
              {!identityReady
                ? "正在确认当前账号权限，确认完成前不能提交任务。"
                : isPersonalWorkspace
                  ? "当前个人空间未开放生成能力。"
                  : "缺少 tasks.create 权限，不能提交生成任务，请联系公司老板授权。"}
            </p>
          )}
          {formError && (
            <p className="form-error" role="alert">
              <WarningCircle size={16} weight="fill" aria-hidden="true" />
              {formError}
            </p>
          )}
          <div className="cost-row">
            <span>
              预计消耗
              <SlidersHorizontal size={15} aria-hidden="true" />
            </span>
            <strong>{costLabel}</strong>
          </div>
          <button
            className="generate-button"
            type="button"
            onClick={startGeneration}
            disabled={
              submitting ||
              Boolean(uploadingKind) ||
              modelsLoading ||
              (models.length === 0 && !pendingCreateRef.current) ||
              (!activeCapability && !pendingCreateRef.current) ||
              (LIVE_MODE && (!identityReady || !canCreateTasks)) ||
              taskStageIsActive
            }
          >
            {submitting || uploadingKind || taskStageIsActive ? (
              <SpinnerGap className="spin" size={21} aria-hidden="true" />
            ) : (
              <Play size={20} weight="fill" aria-hidden="true" />
            )}
            {submitting
              ? "正在提交"
              : uploadingKind
                ? "正在上传素材"
              : LIVE_MODE && identityReady && !canCreateTasks
                ? "无任务创建权限"
              : LIVE_MODE && !identityReady
                ? "正在确认权限"
              : !activeCapability && !pendingCreateRef.current
                ? "模型能力不可用"
              : stage === "accepted"
              ? "任务已接收"
              : stage === "queued"
              ? "正在排队"
              : stage === "rendering"
                ? "正在生成"
                : "开始生成"}
          </button>
          <p>
            {isPersonalWorkspace
              ? "失败不扣积分；个人余额不与企业钱包混用"
              : "失败不扣费，完成后自动转存"}
          </p>
        </div>
      </aside>
      )}

      <footer
        className={`taskbar task-${stage}`}
        aria-live="polite"
        aria-label={`当前任务：${taskStatus.label}`}
        tabIndex={0}
      >
        <div className="task-summary">
          <span className="task-status-icon">
            <TaskIcon
              className={taskStageIsActive ? "spin" : ""}
              size={25}
              weight={stage === "complete" ? "fill" : "regular"}
              aria-hidden="true"
            />
          </span>
          <span>
            <strong>
              {taskStatus.label} · {LIVE_MODE ? generationModeLabel(generationMode) : "镜头 01"}
            </strong>
            <small>
              {LIVE_MODE && currentTask
                ? `任务 ${shortId(currentTask.id)}`
                : LIVE_MODE
                  ? "尚未提交真实任务"
                  : "产品特写"}
            </small>
          </span>
        </div>
        <div className="task-progress-area">
          <div className="progress-track" aria-label={`任务进度 ${progress}%`}>
            <span style={{ width: `${progress}%` }} />
          </div>
          <strong>{progress}%</strong>
        </div>
        <span className="task-eta">
          {stage === "accepted"
            ? "平台已接收"
            : stage === "queued"
            ? "等待调度"
            : stage === "rendering"
              ? `预计剩余 ${Math.max(8, Math.round((100 - progress) * 0.55))} 秒`
              : stage === "complete"
                ? "已转存"
                : stage === "timed-out"
                  ? "已停止等待"
                  : stage === "reconciliation-required"
                    ? "不会自动重试"
                    : stage === "unknown"
                      ? "请刷新确认"
                : stage === "failed" && currentTask?.failure_reason
                  ? currentTask.failure_reason
                  : "未计费"}
        </span>
        {taskStageIsActive && LIVE_MODE && !canCancelTasks ? (
          <span
            className="task-action is-readonly"
            title="个人任务取消接口尚未开放"
          >
            取消未开放
          </span>
        ) : taskStageIsActive ? (
          <button
            className="task-action"
            type="button"
            onClick={cancelGeneration}
            disabled={cancelling || (LIVE_MODE && currentTask?.status !== "queued")}
            title={LIVE_MODE && currentTask?.status !== "queued" ? "仅可取消尚未外发的排队任务" : undefined}
          >
            {cancelling ? "正在安全取消" : LIVE_MODE && currentTask?.status !== "queued" ? "已提交给渠道" : "取消生成"}
          </button>
        ) : stage === "complete" ? (
          <button
            className="task-action"
            type="button"
            onClick={(event) => {
              resultReturnFocusRef.current = event.currentTarget;
              setResultOpen(true);
            }}
          >
            {LIVE_MODE ? `查看产物${activeOutputArtifacts.length ? ` (${activeOutputArtifacts.length})` : ""}` : "查看成片"}
          </button>
        ) : taskStageNeedsAttention ? (
          <button
            className="task-action"
            type="button"
            onClick={(event) => {
              resultReturnFocusRef.current = event.currentTarget;
              setResultOpen(true);
            }}
          >
            查看详情
          </button>
        ) : (
          <button
            className="task-action"
            type="button"
            onClick={retryGeneration}
            disabled={LIVE_MODE && !canCreateTasks}
            title={LIVE_MODE && !canCreateTasks ? "缺少 tasks.create 权限" : undefined}
          >
            <ArrowCounterClockwise size={17} aria-hidden="true" />
            重新生成
          </button>
        )}
      </footer>

      {resultOpen && (
        <div
          className="modal-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) closeResultDialog();
          }}
        >
          <section
            ref={resultDialogRef}
            className="result-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="result-title"
            tabIndex={-1}
          >
            <ResultDetailView
              resultTask={resultTask}
              liveMode={LIVE_MODE}
              resultTaskStatus={resultTaskStatus}
              ResultStatusIcon={ResultStatusIcon}
              resultOutputArtifacts={resultOutputArtifacts}
              artworks={artworks}
              canAccessArtifacts={canAccessArtifacts}
              issuedArtifacts={issuedArtifacts}
              artifactActionKey={artifactActionKey}
              downloadingAssetId={downloadingAssetId}
              canManageAssets={canManageAssets}
              canStartPublication={canStartPublication}
              isPersonalWorkspace={isPersonalWorkspace}
              canCreateTasks={canCreateTasks}
              downloadError={downloadError}
              onClose={closeResultDialog}
              onDownloadArtifact={downloadArtifact}
              onPromoteArtifact={promoteTaskArtifactToInputAsset}
              onOpenPublication={openPublicationForArtifact}
              onCreateAgain={createAgainFromTask}
              onAdjust={adjustHistoricalTask}
              onDemoDownload={() => setToast("演示模式不会记录真实下载行为")}
            />
          </section>
        </div>
      )}

      {toast && (
        <div className="toast" role="status">
          <CheckCircle size={19} weight="fill" aria-hidden="true" />
          {toast}
        </div>
      )}
    </div>
  );
}
