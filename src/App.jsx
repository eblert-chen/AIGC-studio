import { lazy, Suspense, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowCounterClockwise,
  Bell,
  CaretDown,
  CaretRight,
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
import { formatBytes, shortId } from "./components/studio/studioPresentation.js";
import {
  IconButton,
  Preview,
  SceneTimeline,
} from "./components/studio/StudioWorkspaceViews.jsx";
import { DemoAccountSwitcher } from "./DemoAccountSwitcher.jsx";
import GenerationEditor from "./components/GenerationEditor.jsx";
import { usePersonalModelCatalog } from "./app/usePersonalModelCatalog.js";
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
import {
  demoIdentityForProductContext,
  demoPersona as resolveDemoPersona,
} from "./demoIdentitySurfaces.js";
import {
  availableProductContexts,
  isActiveCompanyContext,
  personalCapability,
} from "./personalWorkspace.js";
import {
  buildCapabilityRequestPayload,
  capabilityControlVisibility,
  capabilityForMode,
  capabilityMediaLimits,
  capabilitySpecificationFields,
  firstSupportedMode,
  generationPromptLength,
  modeLabel,
  modeUsesDuration,
  normalizeModeReadiness,
  reconcileGenerationDraft,
  resolveGenerationReadiness,
  resolveEffectiveCapabilities,
  truncateGenerationPrompt,
} from "./modelCapabilities.js";
import {
  billingAmountLabel,
  billingPresentationState,
  formatPointAmount,
  generationCostPreview,
  normalizePositivePrice,
} from "./billingPresentation.js";
import {
  taskArtifactEvidence,
} from "./taskArtifacts.js";
import {
  resolveTaskStatus,
  taskTimingLabel,
  taskUserMessage,
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
import { surfacePath } from "./studioNavigation.js";
import {
  resolveCompanyStudioAccess,
  studioRouteAvailable,
} from "./studioAccess.js";
import { RouteLoadingFallback } from "./RouteLoadingFallback.jsx";
import {
  DEVELOPMENT_DEMO_ARTWORKS,
  DEVELOPMENT_DEMO_HISTORY_TASKS,
  DEVELOPMENT_DEMO_MODEL_RESPONSES,
} from "./demo/studioDemoFixtures.js";
import {
  filesFromPendingRequest,
  makeIdempotencyKey,
  readPendingCreate,
  rememberPendingCreate,
  taskRequestFingerprint,
} from "./app/pendingGeneration.js";
import { appendGenerationInputAssets, useGenerationDraft, useGenerationInputs } from "./app/useGenerationDraft.js";
import { ADVANCED_WORKBENCHES, useCreationWorkspaceSession, useWorkbenchTaskRecords } from "./app/useCreationWorkspaceSession.js";
import { useStudioRouteState } from "./app/useStudioRouteState.js";
import { useStudioIdentity } from "./app/useStudioIdentity.js";
import {
  useStudioArtworkCollection,
  useStudioTaskCollections,
} from "./app/useStudioCollections.js";
import {
  useGenerationTaskLifecycle,
  useGenerationTaskRuntime,
} from "./app/useGenerationTaskRuntime.js";

function lazyNamed(loader, exportName) {
  return lazy(async () => {
    const module = await loader();
    return { default: module[exportName] };
  });
}

const ManagementConsole = lazyNamed(
  () => import("./ManagementConsole.jsx"),
  "ManagementConsole",
);
const CommunityHome = lazyNamed(() => import("./CommunityHome.jsx"), "CommunityHome");
const CreationHub = lazyNamed(() => import("./CreationHub.jsx"), "CreationHub");
const PublishingCenter = lazyNamed(() => import("./PublishingCenter.jsx"), "PublishingCenter");
const AccountCenter = lazyNamed(() => import("./AccountCenter.jsx"), "AccountCenter");
const ArtworksView = lazyNamed(
  () => import("./pages/studio/ArtworksView.jsx"),
  "ArtworksView",
);
const HistoryView = lazyNamed(
  () => import("./pages/studio/HistoryView.jsx"),
  "HistoryView",
);
const WorkspaceCapabilityUnavailableView = lazyNamed(
  () => import("./pages/studio/StudioStatusViews.jsx"),
  "WorkspaceCapabilityUnavailableView",
);
const ResultDetailView = lazyNamed(
  () => import("./pages/studio/ResultDetailView.jsx"),
  "ResultDetailView",
);

const DEVELOPMENT_DEMO_ENABLED = import.meta.env.PROD
  ? false
  : isExplicitDevelopmentDemo(import.meta.env);

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
  unitPriceCents: null,
  unitPricePoints: null,
  rate: null,
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
    readinessCheckedAt:
      typeof source.readiness_checked_at === "string" ? source.readiness_checked_at : null,
    modeReadiness: normalizeModeReadiness(source.mode_readiness),
    pricingMode,
    billingUnit: source.billing_unit,
    billingVersion: source.billing_version,
    billingScope: source.billing_scope,
    unitPriceCents: normalizePositivePrice(source.unit_price_cents),
    unitPricePoints: normalizePositivePrice(source.unit_price_points),
    rate: normalizePositivePrice(source.rate ?? source.unit_price_points),
  };
}

const DEMO_MODELS = DEVELOPMENT_DEMO_MODEL_RESPONSES.map((source) => normalizeLiveModel(source));
const DEMO_INITIAL_MODE = DEMO_MODE ? DEMO_MODELS[0].defaultMode : "";
const DEMO_INITIAL_CAPABILITY = DEMO_MODE
  ? capabilityForMode(DEMO_MODELS[0].effectiveCapabilities, DEMO_INITIAL_MODE)
  : null;

function validCapabilityRevision(value) {
  return Number.isInteger(value) && value >= 1;
}

function validQuoteRevision(value) {
  return typeof value === "string" && /^sha256:[0-9a-f]{64}$/.test(value);
}

function readableApiError(error) {
  if (
    error instanceof PlatformApiError &&
    ["insufficient_points", "INSUFFICIENT_POINTS"].includes(error.code)
  ) {
    return "个人可用积分不足；当前未开放自助充值，请联系平台管理员处理。";
  }
  if (
    error instanceof PlatformApiError &&
    ["insufficient_balance", "INSUFFICIENT_BALANCE"].includes(error.code)
  ) {
    return "公司可用余额不足；当前未开放在线充值，请联系企业负责人或平台管理员处理。";
  }
  if (error instanceof PlatformApiError) {
    if (error.status === 401) return "登录状态已失效，请重新登录后再试。";
    if (error.status === 403) return "当前账号不能执行此操作，请联系管理员检查权限。";
    if (error.status === 404) return "没有找到相关内容，请刷新页面后再试。";
    if (error.status === 409) return "内容已被其他操作更新，请刷新后再试。";
    if (error.status === 429) return "操作过于频繁，请稍后再试。";
    if (Number(error.status) >= 500) return "服务暂时不可用，请稍后再试。";
  }
  const message = String(error?.message || "").trim();
  const exposesImplementation = /\b(?:Platform|Relay|canonical|idempotency|resource|capability|artifact|tasks|assets|publish)\b|\b[A-Z][A-Z0-9_]{2,}\b|https?:\/\//i.test(message);
  if (/\p{Script=Han}/u.test(message) && !exposesImplementation) return message;
  return "请求暂时无法完成，请稍后再试。";
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
    billing_unit: artwork.billing_unit,
    billing_version: artwork.billing_version,
    billing_scope: artwork.billing_scope,
    output_artifacts: [],
    created_at: artwork.created_at,
  };
}

function generationModeLabel(mode) {
  return modeLabel(mode);
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
    if (["personal_creator", "operator", "owner", "platform_admin"].includes(value)) return value;
  } catch {
    // Demo account selection remains in memory when storage is unavailable.
  }
  return "operator";
}

function readInitialDemoProductContext(personaId) {
  if (personaId !== "platform_admin") return "";
  try {
    const value = globalThis.sessionStorage?.getItem("ai-video.demo-product-context");
    if (["personal", "platform"].includes(value)) return value;
  } catch {
    // The linked owner context falls back to Platform when storage is unavailable.
  }
  return "platform";
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
  "artifact-evidence-missing": { label: "作品保存未确认", icon: WarningCircle },
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
  disabledReasonId = "",
  icon: Icon,
}) {
  const inputRef = useRef(null);
  const mediaInputId = `media-input-${kind}`;
  const displayedFiles = files;
  const inputDisabled = uploading || uploadDisabled || files.length >= limit;

  return (
    <div className="media-input-group" data-kind={kind}>
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
            aria-describedby={inputDisabled && disabledReasonId ? disabledReasonId : undefined}
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
      {disabledReason && !disabledReasonId && (
        <small className="media-input-permission">{disabledReason}</small>
      )}
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
  const [mediaFilter, setMediaFilter] = useState("all");
  const filteredAssets = assets.filter((asset) => (
    inputAssetName(asset).toLowerCase().includes(query.trim().toLowerCase())
    && (mediaFilter === "all" || inputAssetType(asset) === mediaFilter)
  ));
  const demoScenes = SCENES.filter((scene) => (
    scene.title.toLowerCase().includes(query.trim().toLowerCase())
    && (mediaFilter === "all" || mediaFilter === "image")
  ));
  const requestAssetRemoval = (asset) => {
    const assetName = inputAssetName(asset);
    const confirmed = globalThis.confirm?.(
      liveMode
        ? `停用“${assetName}”？它会同时移出当前草稿，但不会影响历史任务。`
        : `从演示素材库移除“${assetName}”？它会同时移出当前草稿。`,
    );
    if (confirmed === false) return;
    onDelete(asset);
  };

  return (
    <section className="secondary-view media-view">
      <div className="secondary-heading">
        <div>
          <h1>素材</h1>
          <p>上传或选择图片、视频与音频，用于当前创作。</p>
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
        <div className="media-library-heading-actions">
          <button
            className="media-library-upload-button"
            type="button"
            onClick={() => inputRef.current?.click()}
            disabled={uploading || !canManageAssets}
          >
            {uploading ? <SpinnerGap className="spin" size={18} aria-hidden="true" /> : <UploadSimple size={18} aria-hidden="true" />}
            {uploading ? "正在上传" : "上传素材"}
          </button>
          <label className="search-field media-library-search">
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
      </div>
      <nav className="media-library-filters" aria-label="素材类型">
        {[["all", "全部"], ["image", "参考图"], ["video", "视频"], ["audio", "音频"]].map(([value, label]) => (
          <button
            key={value}
            type="button"
            className={mediaFilter === value ? "is-active" : ""}
            aria-pressed={mediaFilter === value}
            onClick={() => setMediaFilter(value)}
          >
            {label}
          </button>
        ))}
      </nav>
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
      {!loading && !error && (
        (liveMode && filteredAssets.length === 0)
        || (!liveMode && demoScenes.length === 0)
      ) && (
        <div className="artifact-empty" role="status">
          <ImageSquare size={26} aria-hidden="true" />
          <strong>{query.trim() ? "没有符合搜索的素材" : mediaFilter !== "all" ? "当前分类还没有素材" : "还没有素材"}</strong>
          <span>{query.trim() ? "清除搜索后查看全部素材。" : mediaFilter !== "all" ? "切换到全部，查看当前可用素材。" : "上传第一份素材，或直接前往创作使用无素材模式。"}</span>
          {query.trim() ? (
            <button className="text-button" type="button" onClick={() => setQuery("")}>清除搜索</button>
          ) : mediaFilter !== "all" ? (
            <button className="text-button" type="button" onClick={() => setMediaFilter("all")}>查看全部</button>
          ) : null}
        </div>
      )}
      <div className="asset-grid">
        {!liveMode && demoScenes.map((scene) => (
          <button
            className="asset-item"
            type="button"
            key={scene.id}
            onClick={() => onUse(scene.id)}
          >
            <img src={scene.image} alt="" />
            <span>
              <strong>{scene.title}</strong>
              <small>用于当前创作</small>
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
                <span>{mediaType === "video" ? "视频" : mediaType === "audio" ? "音频" : "图片"}</span>
              </div>
              <span>
                <strong title={inputAssetName(asset)}>{inputAssetName(asset)}</strong>
                <small>{liveMode ? formatBytes(asset.size_bytes) : `${formatBytes(asset.size_bytes)}，演示素材`}</small>
              </span>
              <div className="asset-actions">
                <button type="button" onClick={() => onAdd(asset)} disabled={!canCreateTasks}>用于当前创作</button>
                <button type="button" onClick={() => onPreview(asset)}>预览</button>
                <button className="is-danger" type="button" onClick={() => requestAssetRemoval(asset)} disabled={!canManageAssets}>{liveMode ? "停用素材" : "移除"}</button>
              </div>
            </article>
          );
        })}
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

const DEMO_HISTORY_TASKS = DEMO_MODE ? DEVELOPMENT_DEMO_HISTORY_TASKS : [];
const DEMO_ARTWORKS = DEMO_MODE ? DEVELOPMENT_DEMO_ARTWORKS : [];
const PHONE_ADVANCED_WORKBENCH_QUERY = "(max-width: 720px)";

function useMediaQuery(query) {
  const read = () => globalThis.matchMedia?.(query)?.matches ?? false;
  const [matches, setMatches] = useState(read);

  useEffect(() => {
    const media = globalThis.matchMedia?.(query);
    if (!media) return undefined;
    const sync = () => setMatches(media.matches);
    sync();
    if (typeof media.addEventListener === "function") {
      media.addEventListener("change", sync);
      return () => media.removeEventListener("change", sync);
    }
    media.addListener?.(sync);
    return () => media.removeListener?.(sync);
  }, [query]);

  return matches;
}

export function App() {
  const {
    session: authSession,
    logout: logoutSession,
    switchProductContext,
    handleAuthenticationError,
  } = useAuth();
  const [skin, setSkin] = useSkinPreference();
  const pendingCreateRef = useRef(null);
  const activeSubmissionRef = useRef(null);
  const initialPendingCreate = pendingCreateRef.current;
  const {
    activeNav,
    setActiveNav,
    creationWorkbench,
    setCreationWorkbench,
    surface,
    setSurface,
    navigateStudio,
    navigateCreationWorkbench,
  } = useStudioRouteState();
  const advancedWorkbenchesDesktopOnly = useMediaQuery(PHONE_ADVANCED_WORKBENCH_QUERY);
  const phoneAdvancedWorkbenchGate = Boolean(
    activeNav === "create"
      && advancedWorkbenchesDesktopOnly
      && ADVANCED_WORKBENCHES.includes(creationWorkbench),
  );
  const [demoPersonaId, setDemoPersonaId] = useState(readInitialDemoPersona);
  const activeDemoPersona = DEMO_MODE ? resolveDemoPersona(demoPersonaId) : null;
  const [demoProductContext, setDemoProductContext] = useState(() => (
    readInitialDemoProductContext(readInitialDemoPersona())
  ));
  const [activeSceneId, setActiveSceneId] = useState("water");
  const [models, setModels] = useState(DEMO_MODE ? DEMO_MODELS : []);
  const [modelsLoading, setModelsLoading] = useState(LIVE_MODE);
  const [modelsError, setModelsError] = useState("");
  const [activeCompanyId, setActiveCompanyId] = useState(
    runtimePlatformConfig.companyId || "",
  );
  const [personalWallet, setPersonalWallet] = useState(null);
  const [personalWalletError, setPersonalWalletError] = useState("");
  const {
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
  } = useGenerationDraft({
    demoMode: DEMO_MODE,
    demoModels: DEMO_MODELS,
    demoInitialMode: DEMO_INITIAL_MODE,
    demoInitialCapability: DEMO_INITIAL_CAPABILITY,
    initialPendingCreate,
  });
  const [assets, setAssets] = useState([]);
  const [assetsLoading, setAssetsLoading] = useState(LIVE_MODE);
  const [assetsError, setAssetsError] = useState("");
  const [uploadingKind, setUploadingKind] = useState("");
  const generationTaskRuntime = useGenerationTaskRuntime({
    demoMode: DEMO_MODE,
    initialPendingCreate,
  });
  const {
    stage,
    progress,
    currentTaskId,
    currentTask,
    currentTaskScope,
    detailTask,
    detailTaskScope,
    submitting,
    setSubmitting,
    cancelling,
    setCancelling,
    formError,
    setFormError,
    promptError,
    setPromptError,
    playing,
    setPlaying,
    playhead,
    setPlayhead,
  } = generationTaskRuntime;
  const [authExpired, setAuthExpired] = useState(false);
  const [artworkPreviewUrls, setArtworkPreviewUrls] = useState({});
  const [publishingReadiness, setPublishingReadiness] = useState(null);
  const [publishingReadinessResolved, setPublishingReadinessResolved] = useState(DEMO_MODE);
  const [publishingReadinessError, setPublishingReadinessError] = useState("");
  const [issuedArtifacts, setIssuedArtifacts] = useState({});
  const [artifactActionKey, setArtifactActionKey] = useState("");
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const [resultOpen, setResultOpen] = useState(false);
  const [composerPanel, setComposerPanel] = useState(null);
  const [mobileComposerOpen, setMobileComposerOpen] = useState(false);
  const composerExpanded = Boolean(composerPanel);
  const [downloadingAssetId, setDownloadingAssetId] = useState("");
  const [downloadError, setDownloadError] = useState("");
  const [publicationIntent, setPublicationIntent] = useState(null);
  const [toast, setToast] = useState("");
  const [productContextSwitch, setProductContextSwitch] = useState({
    target: "",
    error: "",
  });
  const [taskCompletionNotices, setTaskCompletionNotices] = useState(true);
  const composerRef = useRef(null);
  const composerPanelTriggerRef = useRef(null);
  const focusComposerPanelOnOpenRef = useRef(true);
  const restoreComposerPanelFocusRef = useRef(false);
  const mainCanvasRef = useRef(null);
  const studioNavTrackRef = useRef(null);
  const notificationAnchorRef = useRef(null);
  const userAnchorRef = useRef(null);
  const resultDialogRef = useRef(null);
  const resultReturnFocusRef = useRef(null);
  const studioWorkspaceEvidenceKeyRef = useRef("");

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
    generationTaskRuntime.closeTaskDetail();
    setResultOpen(false);
  };

  useLayoutEffect(() => {
    const canvas = mainCanvasRef.current;
    if (!canvas) return;
    canvas.scrollTop = 0;
    canvas.scrollLeft = 0;
  }, [activeNav]);

  useLayoutEffect(() => {
    const track = studioNavTrackRef.current;
    if (!track || !window.matchMedia("(max-width: 900px)").matches) return;
    const activeItem = track.querySelector(".is-active");
    if (!activeItem) return;
    const targetLeft = activeItem.offsetLeft - (track.clientWidth - activeItem.offsetWidth) / 2;
    track.scrollTo({ left: Math.max(0, targetLeft), behavior: "auto" });
  }, [activeNav]);

  useLayoutEffect(() => {
    const composer = composerRef.current;
    if (!composer) return;
    const scroller = composer.querySelector("#composer-parameters-panel");
    if (composerPanel) {
      const panel = composer.querySelector(`#composer-${composerPanel}-panel`);
      if (scroller && panel) {
        const panelTop =
          panel.getBoundingClientRect().top -
          scroller.getBoundingClientRect().top +
          scroller.scrollTop;
        const mobileSingleFocus = globalThis.matchMedia?.("(max-width: 620px)")?.matches;
        scroller.scrollTo({ top: mobileSingleFocus ? 0 : Math.max(0, panelTop), behavior: "auto" });
      }
      if (focusComposerPanelOnOpenRef.current) {
        composer
          .querySelector(`#composer-${composerPanel}-panel [data-composer-panel-focus]`)
          ?.focus({ preventScroll: true });
      }
      focusComposerPanelOnOpenRef.current = true;
      return;
    }
    scroller?.scrollTo?.({ top: 0, behavior: "auto" });
    if (restoreComposerPanelFocusRef.current) {
      const trigger = composerPanelTriggerRef.current;
      if (trigger?.isConnected && typeof trigger.focus === "function") {
        trigger.focus({ preventScroll: true });
      }
    }
    restoreComposerPanelFocusRef.current = false;
  }, [composerPanel]);

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

  function expireSessionIfNeeded(error) {
    if (!(error instanceof PlatformApiError)) return false;
    if (!handleAuthenticationError(error)) return false;
    if (error.code !== "STEP_UP_REQUIRED") setAuthExpired(true);
    setUserMenuOpen(false);
    setNotificationsOpen(false);
    return true;
  }

  const {
    companyIdentity,
    personalIdentity,
    platformIdentity,
    sessionSurfaceCatalog,
    identityResolved,
    identityError,
    beginCompanySwitch,
  } = useStudioIdentity({
    liveMode: LIVE_MODE,
    demoMode: DEMO_MODE,
    liveClient,
    runtimePlatformConfig,
    activeCompanyId,
    setActiveCompanyId,
    authExpired,
    onAuthenticationError: expireSessionIfNeeded,
  });

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
  const resultArtifactEvidence = taskArtifactEvidence(resultTask);
  const activeArtifactEvidence = taskArtifactEvidence(currentTask);
  const resolvedResultTaskStatus = resolveTaskStatus(resultTask?.status);
  const resultTaskStatus = LIVE_MODE
    && resultTask?.status === "succeeded"
    && !resultArtifactEvidence.complete
      ? {
        ...resolvedResultTaskStatus,
        stage: "artifact-evidence-missing",
        label: "作品保存未确认",
        tone: "warning",
        detail: resultArtifactEvidence.detail,
      }
    : resolvedResultTaskStatus;
  const ResultStatusIcon = STATUS[resultTaskStatus.stage]?.icon ?? WarningCircle;
  const taskStageIsActive = ["accepted", "queued", "rendering"].includes(stage);
  const taskStageNeedsAttention = [
    "timed-out",
    "reconciliation-required",
    "artifact-evidence-missing",
    "unknown",
  ].includes(stage);
  const resultTaskScope = detailTask ? detailTaskScope : currentTaskScope;
  const resultOutputArtifacts = LIVE_MODE && resultArtifactEvidence.complete
    ? resultArtifactEvidence.artifacts
    : [];
  const activeOutputArtifacts = LIVE_MODE && activeArtifactEvidence.complete
    ? activeArtifactEvidence.artifacts
    : [];
  const supportedModes = Object.keys(model.effectiveCapabilities?.modes ?? {});
  const activeCapability = capabilityForMode(model.effectiveCapabilities, generationMode);
  const visibleControls = capabilityControlVisibility(activeCapability);
  const mediaLimits = capabilityMediaLimits(activeCapability);
  const specificationFields = capabilitySpecificationFields(activeCapability, generationMode);
  const fixedSpecificationFields = specificationFields.filter((field) => field.values.length === 1);
  const pendingPayload = pendingCreateRef.current?.requestPayload ?? null;
  const pendingContext = pendingCreateRef.current?.creationContext;
  const pendingBelongsToDraft = !pendingContext || draftIdentity === JSON.stringify([pendingContext.scopeKey, pendingContext.entryId || "quick"]);
  const promptLimit = activeCapability?.limits.maxPromptLength ?? 0;
  const faceControlAvailable = visibleControls.face;
  const historicalFaceVisible = Boolean(
    pendingPayload && Object.hasOwn(pendingPayload, "face_enabled"),
  );
  const historicalRequestLocked = Boolean(pendingPayload);
  const visibleMediaLimits = historicalRequestLocked
    ? {
        image: files.image.length,
        video: files.video.length,
        audio: files.audio.length,
      }
    : mediaLimits;
  const hasVisibleMediaInputs = Object.values(visibleMediaLimits).some(
    (maximum) => maximum > 0,
  );
  const hasVisibleSpecifications = historicalRequestLocked || specificationFields.length > 0;
  const hasVisibleRecipe = historicalRequestLocked || supportedModes.length > 0;

  useLayoutEffect(() => {
    const unavailablePanel = (composerPanel === "references" && !hasVisibleMediaInputs)
      || (composerPanel === "specs" && !hasVisibleSpecifications)
      || (composerPanel === "recipe" && !hasVisibleRecipe);
    if (!unavailablePanel) return;
    restoreComposerPanelFocusRef.current = false;
    setComposerPanel(null);
    globalThis.requestAnimationFrame?.(() => {
      composerRef.current?.querySelector(activeCapability ? "#prompt" : "#model")?.focus({ preventScroll: true });
    });
  }, [composerPanel, hasVisibleMediaInputs, hasVisibleSpecifications, hasVisibleRecipe, activeCapability]);
  const companyClient = useMemo(() => {
    if (!liveClient || !activeCompanyId) return liveClient;
    if (activeCompanyId === runtimePlatformConfig.companyId) return liveClient;
    return createPlatformClient({
      ...runtimePlatformConfig,
      companyId: activeCompanyId,
    });
  }, [activeCompanyId]);
  const sessionIdentity = DEMO_MODE
    ? demoIdentityForProductContext(activeDemoPersona, demoProductContext)
    : platformIdentity || companyIdentity || personalIdentity;
  const authorizedProductContexts = availableProductContexts(
    DEMO_MODE ? sessionIdentity : sessionSurfaceCatalog || authSession,
  );
  const canReturnToPlatform = sessionIdentity?.workspace_kind === "personal"
    && authorizedProductContexts.includes("platform");
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
    const workspaceLabel = effectiveSurface === "personal" ? "个人空间" : "企业空间";
    if (globalThis.document) {
      globalThis.document.title = `${routeLabel} · ${workspaceLabel} · ${BRAND_NAME}`;
    }
  }, [activeNav, effectiveSurface]);
  const permissionCodes = Array.isArray(sessionIdentity?.permission_codes)
    ? sessionIdentity.permission_codes
    : [];
  const companyStudioAccess = resolveCompanyStudioAccess(permissionCodes);
  const identityReady = DEMO_MODE || (identityResolved && Boolean(sessionIdentity));
  const isPersonalWorkspace = effectiveSurface === "personal";
  const hasCompanySession = effectiveSurface !== "personal"
    && Boolean(sessionIdentity?.company_id)
    && !sessionIdentity?.is_platform_admin;
  const canCreateTasks = isPersonalWorkspace
    ? personalCapability(sessionIdentity, "generation")
      && personalCapability(sessionIdentity, "tasks")
    : hasCompanySession && companyStudioAccess.canCreateTasks;
  const canManageAssets = isPersonalWorkspace
    ? personalCapability(sessionIdentity, "assets")
    : hasCompanySession && companyStudioAccess.canManageAssets;
  const canAccessArtifacts = isPersonalWorkspace
    ? personalCapability(sessionIdentity, "artifact_access")
    : hasCompanySession && companyStudioAccess.canAccessArtifacts;
  const canCancelTasks = isPersonalWorkspace
    ? personalCapability(sessionIdentity, "task_cancel")
    : hasCompanySession;
  const hasStudioSession = isPersonalWorkspace
    ? Boolean(sessionIdentity?.workspace_id)
    : hasCompanySession;
  const canReadStudioModels = isPersonalWorkspace
    ? personalCapability(sessionIdentity, "models")
    : hasCompanySession && companyStudioAccess.canReadModels;
  const canReadStudioAssets = isPersonalWorkspace
    ? personalCapability(sessionIdentity, "assets")
    : hasCompanySession && companyStudioAccess.canReadAssets;
  const canReadStudioTasks = isPersonalWorkspace
    ? personalCapability(sessionIdentity, "tasks")
    : hasCompanySession && companyStudioAccess.canReadTasks;
  const canReadStudioArtworks = isPersonalWorkspace
    ? personalCapability(sessionIdentity, "artworks")
    : hasCompanySession && companyStudioAccess.canReadArtworks;
  const taskTrackingUnavailable = Boolean(
    LIVE_MODE
    && currentTaskId
    && !canReadStudioTasks
    && taskStageIsActive,
  );
  const canViewCompanyRecords = hasCompanySession && permissionCodes.includes("reports.read");
  const identityCanReadPublisherAccounts = hasCompanySession && permissionCodes.includes("publish.accounts.read");
  const identityCanManagePublisherAccounts = hasCompanySession && permissionCodes.includes("publish.accounts.manage");
  const identityCanReadPublicationJobs = hasCompanySession && permissionCodes.includes("publish.jobs.read");
  const identityCanManagePublicationJobs = hasCompanySession && permissionCodes.includes("publish.jobs.manage");
  const hasPublishingPermission = identityCanReadPublisherAccounts
    || identityCanManagePublisherAccounts
    || identityCanReadPublicationJobs
    || identityCanManagePublicationJobs;
  const canReadPublisherAccounts = hasCompanySession && (
    publishingReadiness
      ? publishingReadiness.can_read_accounts === true
      : identityCanReadPublisherAccounts
  );
  const canManagePublisherAccounts = hasCompanySession && (
    publishingReadiness
      ? publishingReadiness.can_manage_accounts === true
      : identityCanManagePublisherAccounts
  );
  const canReadPublicationJobs = hasCompanySession && (
    publishingReadiness
      ? publishingReadiness.can_read_jobs === true
      : identityCanReadPublicationJobs
  );
  const canManagePublicationJobs = hasCompanySession && (
    publishingReadiness
      ? publishingReadiness.can_manage_jobs === true
      : identityCanManagePublicationJobs
  );
  const hasAutoPublishEntitlement = DEMO_MODE
    || publishingReadiness?.feature_auto_publish_enabled === true;
  const accountPublishingSideEffectsEnabled = DEMO_MODE
    || (publishingReadinessResolved
      && publishingReadiness?.account_side_effects_enabled === true);
  const jobPublishingSideEffectsEnabled = DEMO_MODE
    || (publishingReadinessResolved
      && publishingReadiness?.job_side_effects_enabled === true);
  const showPublishingNavigation = isPersonalWorkspace || (
    hasCompanySession && (DEMO_MODE || hasPublishingPermission)
  );
  const studioNavigationAccess = {
    canReadModels: canReadStudioModels,
    canReadAssets: canReadStudioAssets,
    canManageAssets,
    canReadTasks: canReadStudioTasks,
    canCreateTasks,
  };
  const visibleStudioNavItems = NAV_ITEMS.filter((item) => (
    item.id === "publish"
      ? showPublishingNavigation
      : studioRouteAvailable(item.id, studioNavigationAccess)
  ));
  const canStartPublication = DEMO_MODE || (
    hasCompanySession &&
    jobPublishingSideEffectsEnabled &&
    canReadPublisherAccounts &&
    canReadPublicationJobs &&
    canManagePublicationJobs
  );
  const companyName =
    sessionIdentity?.company_name ||
    sessionIdentity?.company_display_name ||
    "";
  const activeCompanyContexts = (sessionSurfaceCatalog?.companies || []).filter(
    isActiveCompanyContext,
  );
  const canSwitchCompany = hasCompanySession && activeCompanyContexts.length > 1;
  const studioManagementSurfaces = sessionSurfaces.filter(
    (item) => item === "company" || item === "platform",
  );
  if (canReturnToPlatform && !studioManagementSurfaces.includes("platform")) {
    studioManagementSurfaces.push("platform");
  }
  const studioWorkspaceKey = isPersonalWorkspace
    ? sessionIdentity?.workspace_id
      ? `personal:${sessionIdentity.workspace_id}`
      : ""
    : activeCompanyId || sessionIdentity?.company_id;
  const studioClient = useMemo(() => {
    if (!LIVE_MODE || !liveClient) return liveClient;
    if (!isPersonalWorkspace) return companyClient;
    return {
      listModels: (...args) => liveClient.listPersonalModels(...args),
      listModelCatalog: (...args) => liveClient.listPersonalModelCatalog(...args),
      listTaskHistory: (...args) => liveClient.listPersonalTasks(...args),
      listTasks: ({ signal } = {}) => liveClient.listPersonalTasks({}, { signal }),
      listArtworks: (...args) => liveClient.listPersonalArtworks(...args),
      getTask: (...args) => liveClient.getPersonalTask(...args),
      createTask: (...args) => liveClient.createPersonalTask(...args),
      getArtifactPreview: (...args) => liveClient.getPersonalArtifactPreview(...args),
      getArtifactDownload: (...args) => liveClient.getPersonalArtifactDownload(...args),
    };
  }, [companyClient, isPersonalWorkspace]);
  const {
    historyTasks,
    setHistoryTasks,
    historyLoading,
    historyError,
    historyScope,
    setHistoryScope,
    historyStatus,
    setHistoryStatus,
    historyPage,
    setHistoryPage,
    historyTotal,
    setHistoryTotal,
    creationPage,
    setCreationPage,
    creationTotal,
    setCreationTotal,
    creationStatus,
    setCreationStatus,
    creationDays,
    setCreationDays,
    creationModelId,
    setCreationModelId,
    creationMediaType,
    setCreationMediaType,
    creationQuery,
    setCreationQuery,
    resetTaskCollections,
  } = useStudioTaskCollections({
    activeNav,
    liveMode: LIVE_MODE,
    authExpired,
    hasStudioSession,
    canReadStudioTasks,
    effectiveSurface,
    isPersonalWorkspace,
    studioClient,
    studioWorkspaceKey,
    collectionPageSize: COLLECTION_PAGE_SIZE,
    onAuthenticationError: expireSessionIfNeeded,
    formatError: readableApiError,
  });
  const {
    artworks,
    setArtworks,
    artworksLoading,
    artworksError,
    artworkScope,
    setArtworkScope,
    artworkMediaFilter,
    setArtworkMediaFilter,
    artworkDownloadFilter,
    setArtworkDownloadFilter,
    artworkPage,
    setArtworkPage,
    artworkTotal,
    setArtworkTotal,
    resetArtworkCollection,
  } = useStudioArtworkCollection({
    activeNav,
    liveMode: LIVE_MODE,
    authExpired,
    hasStudioSession,
    canReadStudioArtworks,
    effectiveSurface,
    isPersonalWorkspace,
    studioClient,
    studioWorkspaceKey,
    collectionPageSize: COLLECTION_PAGE_SIZE,
    onAuthenticationError: expireSessionIfNeeded,
    formatError: readableApiError,
  });
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
  const hasServerReadinessEvidence = !LIVE_MODE || (
    typeof model.readinessCheckedAt === "string" &&
    Number.isFinite(Date.parse(model.readinessCheckedAt))
  );
  const serverGenerationReadiness = hasServerReadinessEvidence
    ? resolveGenerationReadiness(
        model.modeReadiness,
        generationMode,
        { faceEnabled },
      )
    : { status: "unverified", ready: false, supported: true, blockers: [] };
  const localReadinessBlockers = historicalRequestLocked
    ? []
    : [
        ...(!prompt.trim()
          ? [{ code: "prompt_required", message: "请填写制作说明。" }]
          : []),
        ...(promptLimit > 0 && generationPromptLength(prompt.trim()) > promptLimit
          ? [{
              code: "prompt_too_long",
              message: `制作说明超过当前模式的 ${promptLimit} 字上限。`,
            }]
          : []),
        ...(generationMode === "image_to_video" && files.image.length === 0
          ? [{ code: "image_required", message: "图生视频至少需要 1 张参考图。" }]
          : []),
        ...(generationMode === "video_to_video" && files.video.length === 0
          ? [{ code: "video_required", message: "视频参考至少需要 1 个参考视频。" }]
          : []),
        ...(uploadingKind
          ? [{ code: "upload_in_progress", message: "素材仍在上传，请等待上传完成。" }]
          : []),
      ];
  const generationReadiness = localReadinessBlockers.length > 0
    ? {
        ...serverGenerationReadiness,
        ready: false,
        status: serverGenerationReadiness.ready
          ? "blocked"
          : serverGenerationReadiness.status,
        blockers: [
          ...serverGenerationReadiness.blockers,
          ...localReadinessBlockers,
        ],
      }
    : serverGenerationReadiness;
  const readinessBlocking = !historicalRequestLocked && !generationReadiness.ready;
  const readinessReasons = [...new Set(
    generationReadiness.blockers
      .map((blocker) => (
        blocker.message ||
        (blocker.resourceName ? `${blocker.resourceName}暂不可用` : "当前工作区缺少所需授权")
      ))
      .filter(Boolean),
  )];
  const readinessHeadline = generationReadiness.ready
    ? faceEnabled
      ? "可以生成人脸内容"
      : "可以开始生成"
    : serverGenerationReadiness.ready
      ? "补全创作内容后即可生成"
      : generationReadiness.status === "unsupported"
        ? "当前模型不支持人脸处理"
        : generationReadiness.status === "blocked"
          ? "当前设置不能生成"
          : "生成条件尚未确认";
  const readinessCheckedAtLabel = LIVE_MODE && hasServerReadinessEvidence
    ? new Intl.DateTimeFormat("zh-CN", {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      }).format(new Date(model.readinessCheckedAt))
    : "";
  const costPreview = generationCostPreview({
    historicalRequestLocked,
    liveMode: LIVE_MODE,
    model,
    duration,
    outputCount,
  });
  const costLabel = costPreview.label;
  const currentCapabilityRevisionValid = !LIVE_MODE
    || validCapabilityRevision(model.capabilityVersion);
  const currentQuoteRevisionValid = !LIVE_MODE
    || validQuoteRevision(model.quoteRevision);
  const showCostPreview = !historicalRequestLocked
    && !modelsLoading
    && costPreview.available
    && currentQuoteRevisionValid;
  const readinessPrimaryBlocker = generationReadiness.blockers[0] ?? null;
  const readinessPrimaryReason = readinessReasons[0] || (
    generationReadiness.status === "unverified"
      ? "生成条件尚未确认，请刷新模型后重试。"
      : generationReadiness.status === "unsupported"
        ? "当前模型不支持已选择的人脸处理选项，请关闭后重试。"
        : "当前组合暂不满足生成条件，请查看生成就绪依据。"
  );
  const personalRequestUnsupported = isPersonalWorkspace
    && !historicalRequestLocked
    && (
      !["text_to_video", "text_to_image"].includes(generationMode)
      || faceEnabled
      || Object.values(files).some((items) => items.length > 0)
    );
  const referenceUploadNote = historicalRequestLocked
    ? "原提交尚未确认，参考素材暂时锁定。"
    : LIVE_MODE && !canManageAssets
      ? "当前账号不能上传新素材，但仍可选用素材库中的已有内容。"
      : "";
  const composerSubmitState = (() => {
    if (submitting) {
      return { code: "submitting", label: "正在提交", message: "", disabled: true, tone: "progress" };
    }
    if (uploadingKind) {
      return { code: "uploading", label: "正在上传素材", message: "", disabled: true, tone: "progress" };
    }
    if (taskStageIsActive) {
      const label = stage === "accepted"
        ? "任务已接收"
        : stage === "queued"
          ? "正在排队"
          : "正在生成";
      return {
        code: `task_${stage}`,
        label,
        message: taskTrackingUnavailable
          ? "任务已经提交，但当前账号不能查看进度；页面已停止轮询。"
          : formError || "",
        disabled: true,
        tone: formError ? "danger" : "progress",
      };
    }
    if (LIVE_MODE && !identityReady) {
      return {
        code: "identity_pending",
        label: "暂不可生成",
        message: "正在确认当前账号的任务创建权限。",
        disabled: true,
        tone: "warning",
      };
    }
    if (LIVE_MODE && !canCreateTasks) {
      return {
        code: "permission_denied",
        label: "暂不可生成",
        message: isPersonalWorkspace
          ? "当前个人空间未开放生成能力。"
          : "当前账号不能开始生成，请联系企业负责人开通任务创建权限。",
        disabled: true,
        tone: "danger",
      };
    }
    if (modelsLoading) {
      return { code: "models_loading", label: "正在读取模型", message: "", disabled: true, tone: "progress" };
    }
    if (historicalRequestLocked) {
      return {
        code: "pending_confirmation",
        label: "确认原提交",
        message: formError || "再次提交只会确认同一次请求，并继续使用原设置。",
        disabled: false,
        tone: "warning",
      };
    }
    if (models.length === 0 || !model.id) {
      return {
        code: "model_missing",
        label: "暂不可生成",
        message: modelsError || (isPersonalWorkspace
          ? "个人空间当前没有可用模型。"
          : "公司当前没有已授权模型。"),
        disabled: true,
        tone: "danger",
      };
    }
    if (!activeCapability) {
      return {
        code: "capability_missing",
        label: "暂不可生成",
        message: "当前模型能力不可用，请切换模型或联系平台管理员。",
        disabled: true,
        tone: "danger",
      };
    }
    if (!currentCapabilityRevisionValid) {
      return {
        code: "capability_revision_invalid",
        label: "暂不可生成",
        message: "当前模型配置无效，请联系平台管理员检查模型配置。",
        disabled: true,
        tone: "danger",
      };
    }
    if (!currentQuoteRevisionValid || (LIVE_MODE && !costPreview.available)) {
      return {
        code: "price_unverified",
        label: "暂不可生成",
        message: "当前模型的价格尚未确认，请刷新模型后重试。",
        disabled: true,
        tone: "danger",
      };
    }
    if (personalRequestUnsupported) {
      return {
        code: "personal_request_unsupported",
        label: "暂不可生成",
        message: "个人空间本期仅支持无素材、无人脸输入的文生视频与文生图片。",
        disabled: true,
        tone: "warning",
      };
    }
    if (readinessBlocking) {
      return {
        code: readinessPrimaryBlocker?.code || "readiness_blocked",
        source: "readiness",
        label: "暂不可生成",
        message: readinessPrimaryReason,
        disabled: true,
        tone: generationReadiness.status === "unverified" ? "warning" : "danger",
      };
    }
    const transientError = promptError || formError;
    if (transientError) {
      return {
        code: promptError ? "prompt_error" : "form_error",
        label: "开始生成",
        message: transientError,
        disabled: false,
        tone: "danger",
      };
    }
    return { code: "ready", label: "开始生成", message: "", disabled: false, tone: "ready" };
  })();
  const readinessEvidenceReasons = composerSubmitState.source === "readiness"
    ? readinessReasons.slice(1)
    : readinessReasons;
  const composerSubmitMessageId = composerSubmitState.message
    ? "composer-submit-message"
    : undefined;
  const promptSubmissionIssue = ["prompt_required", "prompt_too_long", "prompt_error"]
    .includes(composerSubmitState.code);
  const activeTaskTimingLabel = taskTimingLabel(currentTask, {
    status: currentTask?.status || (stage === "rendering" ? "processing" : stage),
  });
  const hasStoredArtifacts = LIVE_MODE && activeArtifactEvidence.complete;
  const StoredStateIcon =
    LIVE_MODE && !hasStoredArtifacts ? ClockCounterClockwise : Check;
  const storedStateTitle = LIVE_MODE
    ? hasStoredArtifacts
      ? "作品已保存"
      : currentTask?.status === "succeeded"
        ? "作品保存未确认"
        : resultTask
          ? "任务记录已同步"
          : "还没有作品"
    : "演示预览，未写入存储";

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
  const creationScopeKey = identityReady && studioWorkspaceKey && sessionIdentity?.user_id
    ? JSON.stringify([DEMO_MODE ? "demo" : "live", runtimePlatformConfig.baseUrl, sessionIdentity.user_id, isPersonalWorkspace ? "personal" : "company", studioWorkspaceKey])
    : "";
  const {
    personalModelCatalog,
    personalModelCatalogLoading,
    personalModelCatalogError,
  } = usePersonalModelCatalog({
    liveMode: LIVE_MODE,
    authExpired,
    hasStudioSession,
    creationScopeKey,
    isPersonalWorkspace,
    effectiveSurface,
    canReadStudioModels,
    studioClient,
    onAuthenticationError: expireSessionIfNeeded,
    formatError: readableApiError,
  });
  const {
    generationUploadGate, activeCapabilityRef, generationInputContext,
    appendDraftInputs, invalidateGenerationInputContext,
  } = useGenerationInputs({
    capability: activeCapability, modelId, mode: generationMode,
    capabilityVersion: model.capabilityVersion, workspaceKey: creationScopeKey,
    filesRef, setFiles, pendingCreateRef,
    draftKey: draftIdentity,
  });

  const studioDraft = () => ({
    prompt,
    duration,
    aspectRatio: ratio,
    resolution,
    outputCount,
    faceEnabled,
    files: filesRef.current,
  });

  const applyReconciledDraft = (result, { announce = true } = {}) => {
    invalidateGenerationInputContext();
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

  const workbenchDraftFromReconciliation = (result, selectedModelId) => ({
    modelId: selectedModelId,
    generationMode: result.mode,
    prompt: result.draft.prompt ?? "",
    ratio: result.draft.aspectRatio ?? "",
    resolution: result.draft.resolution ?? "",
    duration: result.draft.duration,
    outputCount: result.draft.outputCount,
    faceEnabled: result.draft.faceEnabled,
    files: result.draft.files,
  });

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

  const generationDraftSnapshot = {
    modelId, generationMode, prompt, ratio, resolution, duration, outputCount, faceEnabled, files,
  };
  const generationDraftSnapshotRef = useRef(generationDraftSnapshot);
  generationDraftSnapshotRef.current = generationDraftSnapshot;
  const creationScopeRef = useRef(creationScopeKey);
  creationScopeRef.current = creationScopeKey;
  const creationRouteRef = useRef({ activeNav, workbench: creationWorkbench });
  creationRouteRef.current = { activeNav, workbench: creationWorkbench };
  const pendingStorageKey = creationScopeKey;

  function workbenchDraftForTask(task) {
    const payload = task?.request_payload || {};
    const taskModel = models.find((item) => item.id === task?.model_id);
    const requested = filesFromPendingRequest(payload);
    const verifiedFiles = Object.fromEntries(Object.entries(requested).map(([kind, items]) => [kind, items.flatMap((item) => {
      const asset = assets.find((candidate) => inputAssetId(candidate) === inputAssetId(item) && inputAssetType(candidate) === kind && candidate.status === "active");
      return asset ? [asset] : [];
    })]));
    return {
      modelId: taskModel?.id || "", generationMode: payload.mode || "", prompt: payload.prompt || "",
      ratio: payload.aspect_ratio || "", resolution: payload.resolution || "",
      duration: payload.duration_seconds ?? null, outputCount: payload.output_count ?? null,
      faceEnabled: Boolean(payload.face_enabled), files: verifiedFiles,
    };
  }

  const creationSession = useCreationWorkspaceSession({
    scopeKey: creationScopeKey,
    workbench: phoneAdvancedWorkbenchGate ? "entry" : creationWorkbench,
    draft: generationDraftSnapshot,
    draftIdentity,
    replaceDraft,
    panel: composerPanel,
    mobileOpen: mobileComposerOpen,
    restoreDisclosure: (panel, mobileOpen) => {
      invalidateGenerationInputContext();
      composerPanelTriggerRef.current = null;
      restoreComposerPanelFocusRef.current = false;
      setComposerPanel(panel);
      setMobileComposerOpen(mobileOpen);
      setPromptError("");
      setFormError("");
    },
    locked: submitting || Boolean(uploadingKind) || Boolean(pendingCreateRef.current),
    onNavigate: navigateCreationWorkbench,
    onNotice: setToast,
    draftForTask: workbenchDraftForTask,
  });
  const ownedCreationTaskIds = useMemo(
    () => [...new Set(creationSession.state.entries.flatMap((entry) => entry.taskIds))],
    [creationSession.state.entries],
  );
  const workbenchRecords = useWorkbenchTaskRecords({
    client: studioClient,
    scopeKey: creationScopeKey,
    enabled: LIVE_MODE && canReadStudioTasks && !authExpired && activeNav === "create" && creationWorkbench !== "entry" && !phoneAdvancedWorkbenchGate,
    taskIds: creationSession.state.entries.filter((entry) => entry.kind === creationWorkbench).flatMap((entry) => entry.taskIds),
    knownTasks: [],
    onAuthError: expireSessionIfNeeded,
  });

  useGenerationTaskLifecycle({
    runtime: generationTaskRuntime,
    demoMode: DEMO_MODE,
    liveMode: LIVE_MODE,
    authExpired,
    hasStudioSession,
    canReadStudioTasks,
    effectiveSurface,
    isPersonalWorkspace,
    studioClient,
    studioWorkspaceKey,
    creationScopeKey,
    creationScopeRef,
    creationRouteRef,
    taskBelongsToCurrentDraft: creationSession.taskBelongsToCurrentDraft,
    refreshPersonalWallet,
    taskCompletionNotices,
    previewDuration: duration,
    onAuthenticationError: expireSessionIfNeeded,
    onOpenResult: () => setResultOpen(true),
    onClearDownloadError: () => setDownloadError(""),
    onToast: setToast,
    formatError: readableApiError,
  });

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
    if (!LIVE_MODE || !identityResolved || !hasStudioSession || !studioWorkspaceKey) {
      return;
    }
    const legacyPending = readPendingCreate(studioWorkspaceKey);
    const pendingCreate = readPendingCreate(pendingStorageKey)
      || (legacyPending?.version < 6 ? legacyPending : null);
    studioWorkspaceEvidenceKeyRef.current = studioWorkspaceKey;
    pendingCreateRef.current = pendingCreate;
    activeSubmissionRef.current = null;
    setModels([]);
    if (pendingCreate) {
      creationSession.restoreContext(pendingCreate.creationContext || { scopeKey: creationScopeKey, kind: "quick", entryId: "" });
      restorePendingCreate(pendingCreate);
    }
    setAssets([]);
    resetTaskCollections();
    resetArtworkCollection();
    setArtworkPreviewUrls({});
    setIssuedArtifacts({});
    generationTaskRuntime.resetWorkspace({
      formError: pendingCreate
        ? "上次提交结果尚未确认，已恢复当前工作区的原设置。再次提交只会确认同一次请求，不会创建重复任务。"
        : "",
    });
    closeResultDialog();
  }, [
    hasStudioSession,
    identityResolved,
    resetArtworkCollection,
    resetTaskCollections,
    studioWorkspaceKey,
    pendingStorageKey,
  ]);

  useEffect(() => {
    if (
      !LIVE_MODE ||
      authExpired ||
      !hasStudioSession ||
      !creationScopeKey ||
      !["studio", "personal"].includes(effectiveSurface)
    ) return undefined;
    if (!canReadStudioModels) {
      setModels([]);
      setModelId("");
      setModelsLoading(false);
      setModelsError(isPersonalWorkspace
        ? "当前个人空间没有模型读取能力。"
        : "当前账号不能查看企业模型。请联系企业负责人调整模型访问权限。");
      return undefined;
    }
    const controller = new AbortController();
    const requestScope = creationScopeKey;
    setModelsLoading(true);

    studioClient
      .listModels({ signal: controller.signal })
      .then((items) => {
        if (controller.signal.aborted || creationScopeRef.current !== requestScope) return;
        const normalized = Array.isArray(items)
          ? items.map((source) => normalizeLiveModel(source, { requireEffective: true }))
          : [];
        setModels(normalized);
        const pendingCreate = pendingCreateRef.current;
        if (pendingCreate?.creationContext && creationSession.bindingRef.current?.key !== JSON.stringify([
          pendingCreate.creationContext.scopeKey, pendingCreate.creationContext.entryId || "quick",
        ])) { setModelsError(""); return; }
        const selectedModel =
          normalized.find((item) => item.id === (pendingCreate?.modelId || generationDraftSnapshotRef.current.modelId)) ??
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
            generationDraftSnapshotRef.current.generationMode || selectedModel?.defaultMode || "",
            { ...generationDraftSnapshotRef.current, aspectRatio: generationDraftSnapshotRef.current.ratio },
          );
          applyReconciledDraft(result, { announce: false });
          if (!result.ok && selectedModel) setFormError(result.error);
        }
        setModelsError("");
      })
      .catch((error) => {
        if (error?.name !== "AbortError") {
          if (creationScopeRef.current !== requestScope || controller.signal.aborted) return;
          expireSessionIfNeeded(error);
          setModelsError(readableApiError(error));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setModelsLoading(false);
      });

    return () => controller.abort();
  }, [authExpired, canReadStudioModels, creationScopeKey, effectiveSurface, hasStudioSession, isPersonalWorkspace, studioClient, studioWorkspaceKey]);

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
    if (
      !LIVE_MODE
      || authExpired
      || !hasCompanySession
      || effectiveSurface !== "studio"
      || !hasPublishingPermission
    ) {
      setPublishingReadiness(null);
      setPublishingReadinessResolved(false);
      setPublishingReadinessError("");
      return undefined;
    }
    const controller = new AbortController();
    setPublishingReadiness(null);
    setPublishingReadinessResolved(false);
    setPublishingReadinessError("");

    companyClient
      .getPublishingReadiness({ signal: controller.signal })
      .then((readiness) => {
        setPublishingReadiness(readiness && typeof readiness === "object" ? readiness : null);
      })
      .catch((error) => {
        if (error?.name !== "AbortError") {
          expireSessionIfNeeded(error);
          setPublishingReadiness(null);
          setPublishingReadinessError(`发布就绪状态核对失败：${readableApiError(error)}`);
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setPublishingReadinessResolved(true);
      });

    return () => controller.abort();
  }, [
    authExpired,
    companyClient,
    effectiveSurface,
    hasCompanySession,
    hasPublishingPermission,
    sessionIdentity?.user_id,
  ]);

  useEffect(() => {
    if (!LIVE_MODE || authExpired || !hasCompanySession || effectiveSurface !== "studio") return undefined;
    if (!canReadStudioAssets) {
      setAssets([]);
      setAssetsLoading(false);
      setAssetsError("当前账号不能查看企业素材。拥有素材管理权限时仍可上传新素材。");
      return undefined;
    }
    const controller = new AbortController();
    setAssetsLoading(true);

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
  }, [authExpired, canReadStudioAssets, companyClient, effectiveSurface, hasCompanySession, sessionIdentity?.user_id]);

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
    if (pendingCreateRef.current || modelsLoading) return;
    if (!model.id) {
      if (!modelId && models[0]) selectStudioModel(models[0].id);
      return;
    }
    const result = reconcileGenerationDraft(
      model.effectiveCapabilities,
      generationMode,
      studioDraft(),
    );
    applyReconciledDraft(result);
  }, [generationMode, model, modelsLoading, draftIdentity]);

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
        : "当前账号不能上传素材。请联系企业负责人开通素材管理权限。";
      setFormError(message);
      setToast(message);
      return { ok: false, addedCount: 0, message };
    }
    if (pendingCreateRef.current) {
      const message = "存在结果尚未确认的提交，不能改变素材；请先安全确认原任务。";
      restorePendingCreate(
        pendingCreateRef.current,
        message,
      );
      return { ok: false, addedCount: 0, message };
    }
    if (generationUploadGate.busy) {
      const message = "请等待当前素材上传完成后再添加";
      setToast(message);
      return { ok: false, addedCount: 0, message };
    }
    const limit = capabilityMediaLimits(activeCapabilityRef.current)[kind] ?? 0;
    const remaining = Math.max(0, limit - (filesRef.current[kind]?.length ?? 0));
    const selected = incoming.slice(0, remaining);
    if (!selected.length) {
      const message = limit > 0 ? `这类素材最多可添加 ${limit} 个，已达到模型上限` : "当前模型不支持这类素材";
      setToast(message);
      return { ok: false, addedCount: 0, message };
    }
    const omittedNote = incoming.length > selected.length
      ? `；超出模型上限的 ${incoming.length - selected.length} 个未添加`
      : "";
    const invalid = selected.find(
      (file) => file.type && !file.type.toLowerCase().startsWith(`${kind}/`),
    );
    if (invalid) {
      const message = `${invalid.name} 的文件类型与${kind === "image" ? "图片" : kind === "video" ? "视频" : "音频"}输入不匹配`;
      setFormError(message);
      setToast(message);
      return { ok: false, addedCount: 0, message };
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
      const appended = appendDraftInputs(kind, localAssets);
      setAssets((current) => [...localAssets, ...current]);
      const message = `${appended.addedCount} 个素材已加入当前任务${omittedNote}`;
      setToast(message);
      return { ok: appended.addedCount > 0, addedCount: appended.addedCount, message };
    }

    const uploadRequest = generationUploadGate.begin(generationInputContext());
    if (!uploadRequest) {
      const message = "当前素材上传尚未结束，请稍后重试。";
      return { ok: false, addedCount: 0, message };
    }
    setUploadingKind(kind);
    setFormError("");
    const uploaded = [];
    let uploadError = null;
    let outcome = { ok: false, addedCount: 0, message: "取景图未能加入当前镜头。" };
    try {
      for (const file of selected) {
        if (!generationUploadGate.canAppend(uploadRequest, generationInputContext())) break;
        const asset = await companyClient.uploadAsset(file, kind);
        uploaded.push(asset);
      }
    } catch (error) {
      uploadError = error;
      expireSessionIfNeeded(error);
    } finally {
      const context = generationInputContext();
      if (context.workspaceKey === uploadRequest.workspaceKey) {
        setAssets((current) => {
          const incomingIds = new Set(uploaded.map(inputAssetId));
          return [
            ...uploaded,
            ...current.filter((asset) => !incomingIds.has(inputAssetId(asset))),
          ];
        });
        const canAppend = generationUploadGate.canAppend(uploadRequest, context);
        const appended = canAppend ? appendDraftInputs(kind, uploaded) : { addedCount: 0 };
        const successNote = uploaded.length === 0 ? ""
          : appended.addedCount === uploaded.length
            ? `${appended.addedCount} 个素材已私有上传并加入当前任务`
            : `${uploaded.length} 个素材已保存到素材库，${appended.addedCount} 个加入当前任务${canAppend
              ? appended.invalidCount ? "（其余素材信息不符合当前输入要求）" : "（其余超出当前上限或已在草稿中）"
              : "（草稿已切换或锁定）"}`;
        if (uploadError) {
          const message = `素材上传失败：${readableApiError(uploadError)}`;
          if (canAppend) setFormError(message);
          setAssetsError(message);
          const combinedMessage = [message, successNote].filter(Boolean).join("；");
          setToast(combinedMessage);
          outcome = {
            ok: appended.addedCount > 0,
            addedCount: appended.addedCount,
            message: combinedMessage,
          };
        } else {
          setAssetsError("");
          const message = `${successNote || "草稿已切换，本次上传已停止"}${omittedNote}`;
          setToast(message);
          outcome = {
            ok: appended.addedCount > 0,
            addedCount: appended.addedCount,
            message,
          };
        }
      }
      if (generationUploadGate.finish(uploadRequest)) setUploadingKind("");
    }
    return outcome;
  };

  const addLibraryAssetToTask = (asset) => {
    if (LIVE_MODE && !canCreateTasks) {
      setToast(isPersonalWorkspace
        ? "当前个人空间未开放任务创建能力。"
        : "当前账号不能开始生成。请联系企业负责人开通任务创建权限。");
      return;
    }
    if (pendingCreateRef.current) {
      restorePendingCreate(
        pendingCreateRef.current,
        "存在结果尚未确认的提交，不能改变素材；请先安全确认原任务。",
      );
      return;
    }
    if (generationUploadGate.busy) {
      setToast("请等待当前素材上传完成后再添加");
      return;
    }
    const kind = inputAssetType(asset);
    const changed = creationSession.updateQuickDraft((quick) => {
      const selectedModel = models.find((candidate) => candidate.id === quick.modelId) || models[0];
      const result = reconcileGenerationDraft(selectedModel?.effectiveCapabilities, quick.generationMode || selectedModel?.defaultMode, {
        ...quick, aspectRatio: quick.ratio,
      });
      if (!result.ok) throw new Error(result.error || "请先为快捷创作选择可用模型。");
      const capability = capabilityForMode(selectedModel.effectiveCapabilities, result.mode);
      const limit = capabilityMediaLimits(capability)[kind] ?? 0;
      if (limit <= 0) throw new Error("快捷创作的当前模式不支持这类素材，请先切换创作方式。");
      const appended = appendGenerationInputAssets(capability, result.draft.files, kind, [asset]);
      if (appended.duplicateCount) throw new Error("该素材已经在快捷草稿中。");
      if (!appended.addedCount) throw new Error(`快捷草稿最多使用 ${limit} 个这类素材。`);
      return { ...workbenchDraftFromReconciliation(result, selectedModel.id), files: appended.files };
    }, { panel: "references" });
    if (changed) setToast(`${inputAssetName(asset)} 已加入快捷草稿`);
  };

  const uploadLibraryFiles = async (incoming) => {
    if (!incoming.length) return;
    if (generationUploadGate.busy) {
      setToast("请等待当前素材上传完成后再添加");
      return;
    }
    if (LIVE_MODE && !canManageAssets) {
      const message = isPersonalWorkspace
        ? "个人空间素材库与上传能力尚未开放。"
        : "当前账号不能上传素材。请联系企业负责人开通素材管理权限。";
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

    const uploadRequest = generationUploadGate.begin(generationInputContext());
    if (!uploadRequest) return;
    setUploadingKind("library");
    setAssetsError("");
    const uploaded = [];
    try {
      for (const item of typedFiles) {
        if (generationInputContext().workspaceKey !== uploadRequest.workspaceKey) break;
        uploaded.push(await companyClient.uploadAsset(item.file, item.kind));
      }
      if (generationInputContext().workspaceKey !== uploadRequest.workspaceKey) return;
      setAssets((current) => {
        const uploadedIds = new Set(uploaded.map(inputAssetId));
        return [
          ...uploaded,
          ...current.filter((asset) => !uploadedIds.has(inputAssetId(asset))),
        ];
      });
      setToast(`${uploaded.length} 个素材已保存到公司私有素材库`);
    } catch (error) {
      expireSessionIfNeeded(error);
      if (generationInputContext().workspaceKey !== uploadRequest.workspaceKey) return;
      if (uploaded.length) {
        setAssets((current) => [...uploaded, ...current]);
      }
      const message = `素材上传失败：${readableApiError(error)}`;
      setAssetsError(message);
      setToast(message);
    } finally {
      if (generationUploadGate.finish(uploadRequest)) setUploadingKind("");
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
    if (isPersonalWorkspace || !canReadStudioAssets) {
      setToast(isPersonalWorkspace
        ? "个人空间素材访问尚未开放，未发起任何访问请求。"
        : "当前账号不能预览企业素材。请联系企业负责人调整素材访问权限。");
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
        : "当前账号不能停用素材。请联系企业负责人开通素材管理权限。");
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
    if (pendingCreate.creationContext && !creationSession.restoreContext(pendingCreate.creationContext)) return;
    invalidateGenerationInputContext();
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
    generationTaskRuntime.returnToIdle();
    closeResultDialog();
    setPromptError("");
    const recoveryMessage =
      message ??
      "上一条提交结果尚未确认，已恢复原设置。再次提交只会确认同一次请求，不会创建重复任务。";
    setFormError(recoveryMessage);
    setToast(recoveryMessage);
  };

  const startGeneration = async () => {
    if (submitting) return;
    if (pendingCreateRef.current && !pendingBelongsToDraft) {
      restorePendingCreate(pendingCreateRef.current);
      return;
    }
    if (uploadingKind || generationUploadGate.busy) {
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
            : "当前账号不能开始生成。请联系企业负责人开通任务创建权限。"
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
      setFormError("当前模型暂不能生成。请切换模型，或联系平台管理员检查模型配置。");
      return;
    }
    if (LIVE_MODE && !storedPending && !costPreview.available) {
      setPromptError("");
      setFormError("当前模型的价格尚未确认，暂不能提交。请刷新模型后重试。");
      return;
    }
    if (!storedPending && readinessBlocking) {
      setPromptError("");
      setFormError(
        readinessReasons[0] || (
          generationReadiness.status === "unverified"
            ? "生成条件尚未确认，请刷新模型后重试。"
            : generationReadiness.status === "unsupported"
              ? "当前模型不支持已选择的人脸处理选项，请关闭后重试。"
              : "当前组合暂不满足生成条件，请查看生成就绪状态。"
        ),
      );
      openComposerPanel("readiness");
      return;
    }
    if (!storedPending && generationPromptLength(prompt.trim()) > promptLimit) {
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
        setFormError("原任务的模型配置已经失效，不能继续提交。请刷新模型后新建任务。");
        return;
      }
      if (
        LIVE_MODE &&
        (typeof targetQuoteRevision !== "string" ||
          !/^sha256:[0-9a-f]{64}$/.test(targetQuoteRevision))
      ) {
        setPromptError("");
        setFormError("原任务的价格信息已经失效，不能继续提交。请刷新模型后新建任务。");
        return;
      }
    } else {
      if (
        LIVE_MODE &&
        (!Number.isInteger(model.capabilityVersion) || model.capabilityVersion < 1)
      ) {
        setPromptError("");
        setFormError("当前模型配置无效，暂不能提交。请联系平台管理员检查模型配置。");
        return;
      }
      if (
        LIVE_MODE &&
        (typeof model.quoteRevision !== "string" ||
          !/^sha256:[0-9a-f]{64}$/.test(model.quoteRevision))
      ) {
        setPromptError("");
        setFormError("当前模型的价格信息无效，暂不能提交。请刷新后重试。");
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
    const submissionContext = storedPending?.creationContext || (storedPending
      ? { scopeKey: creationScopeKey, kind: "quick", entryId: "", draftRevision: creationSession.state.revision }
      : creationSession.captureContext());
    if (!submissionContext) { setFormError("工作区尚未就绪，请稍后再试。"); return; }
    const submissionStorageKey = submissionContext.scopeKey;
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
            ...(!storedPending || storedPending.version >= 6 ? { creationContext: submissionContext } : {}),
          },
    );
    const requestFingerprint = taskRequestFingerprint(fingerprint);
    if (storedPending && storedPending.fingerprint !== requestFingerprint) {
      restorePendingCreate(storedPending);
      return;
    }
    if (storedPending && storedPending.version < 6) {
      const upgradedPending = {
        ...storedPending,
        version: 6,
        workspaceKey: submissionStorageKey,
        creationContext: submissionContext,
        capabilityVersion: targetCapabilityVersion,
        quoteRevision: targetQuoteRevision,
        fingerprint: taskRequestFingerprint(JSON.stringify({
          modelId: targetModelId,
          capabilityVersion: targetCapabilityVersion,
          quoteRevision: targetQuoteRevision,
          requestPayload,
          creationContext: submissionContext,
        })),
      };
      pendingCreateRef.current = upgradedPending;
      rememberPendingCreate(submissionStorageKey, upgradedPending);
    }

    setPromptError("");
    setFormError("");
    setDownloadError("");
    closeResultDialog();
    if (submissionContext.kind === "quick") navigateStudio("create");
    generationTaskRuntime.queueSubmission();
    setMobileComposerOpen(false);
    if (!LIVE_MODE) {
      const task = {
        id: `demo-${makeIdempotencyKey()}`, status: "succeeded", model_id: targetModelId,
        model_display_name: model.name, request_payload: requestPayload, output_artifacts: [],
        created_at: new Date().toISOString(),
      };
      creationSession.bindTask(submissionContext, task.id);
      generationTaskRuntime.activateTask(task, { stage: "complete", progress: 100 });
      setToast("演示操作已完成，未调用模型或扣费。");
      return;
    }

    if (!pendingCreateRef.current) {
      pendingCreateRef.current = {
        version: 6,
        workspaceKey: submissionStorageKey,
        creationContext: submissionContext,
        ...(!isPersonalWorkspace ? { companyId: activeCompanyId } : {}),
        modelId: targetModelId,
        capabilityVersion: targetCapabilityVersion,
        quoteRevision: targetQuoteRevision,
        requestPayload,
        fingerprint: requestFingerprint,
        idempotencyKey: makeIdempotencyKey(),
        uncertain: false,
      };
      rememberPendingCreate(submissionStorageKey, pendingCreateRef.current);
    }
    const pendingCreate = pendingCreateRef.current;
    const pendingReceipt = rememberPendingCreate(submissionStorageKey, pendingCreate);
    if (!pendingReceipt.ok) {
      if (!storedPending) pendingCreateRef.current = null;
      generationTaskRuntime.returnToIdle();
      setMobileComposerOpen(true);
      setFormError(`本次没有发送。${pendingReceipt.notice}`);
      setToast(`本次没有发送。${pendingReceipt.notice}`);
      return;
    }
    const submissionToken = {};
    activeSubmissionRef.current = submissionToken;
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
        throw new PlatformApiError("任务响应不完整，请刷新后重试", {
          code: "INVALID_RESPONSE",
        });
      }
      let bindingSaved = true;
      try { bindingSaved = creationSession.bindTask(submissionContext, task.id)?.ok !== false; }
      catch { bindingSaved = false; }
      rememberPendingCreate(submissionStorageKey, null);
      if (storedPending?.version < 6) rememberPendingCreate(studioWorkspaceKey, null);
      if (creationScopeRef.current !== submissionContext.scopeKey) return;
      pendingCreateRef.current = null;
      generationTaskRuntime.activateTask(task, { scope: "mine" });
      if (submissionContext.kind !== "quick" && globalThis.matchMedia?.("(max-width: 620px)")?.matches) creationSession.onStopEditing();
      if (isPersonalWorkspace) refreshPersonalWallet();
      setToast(!bindingSaved
        ? "任务已接收，但本机版本关联未保存。可稍后从历史任务重新加入工作台。"
        : canReadStudioTasks
        ? `任务已提交，编号 ${shortId(task.id)}`
        : `任务已提交，编号 ${shortId(task.id)}。当前账号不能查看任务进度，页面不会继续轮询。`);
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
        rememberPendingCreate(submissionStorageKey, pendingCreate);
      } else {
        if (creationScopeRef.current === submissionContext.scopeKey) pendingCreateRef.current = null;
        rememberPendingCreate(submissionStorageKey, null);
      }
      if (creationScopeRef.current !== submissionContext.scopeKey) return;
      expireSessionIfNeeded(error);
      generationTaskRuntime.returnToIdle();
      setMobileComposerOpen(true);
      const reason = readableApiError(error);
      const message = submissionUncertain
        ? `提交结果尚未确认，原设置已保留。稍后再次提交只会确认同一次请求，不会创建重复任务。${reason}`
        : reason;
      setFormError(message);
      setToast(message);
    } finally {
      if (activeSubmissionRef.current === submissionToken) {
        activeSubmissionRef.current = null;
        setSubmitting(false);
      }
    }
  };

  const openHistoryTask = async (task, requestedScope = historyScope) => {
    const taskId = task?.id || task?.task_id;
    if (!taskId) return;
    resultReturnFocusRef.current = globalThis.document?.activeElement ?? null;
    setFormError(taskUserMessage(task.failure_reason, ""));
    setDownloadError("");
    setResultOpen(true);
    await generationTaskRuntime.loadTaskDetail({
      task,
      scope: requestedScope,
      client: studioClient,
      liveMode: LIVE_MODE,
      isPersonalWorkspace,
      onAuthenticationError: expireSessionIfNeeded,
      onError: (error) => setToast(`任务详情读取失败：${readableApiError(error)}`),
    });
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
        : "当前账号不能开始生成。请联系企业负责人开通任务创建权限。");
      return;
    }
    if (pendingCreateRef.current) {
      restorePendingCreate(pendingCreateRef.current);
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
    let restoredDraft;

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
      restoredDraft = workbenchDraftFromReconciliation(result, taskModel.id);
      if (!result.ok) {
        retryError = result.error;
      } else if (result.changes.length || unavailableAssetCount > 0) {
        const removedCount = result.removedMediaCount + unavailableAssetCount;
        retryMessage = removedCount > 0
          ? `原任务已按当前能力恢复，${removedCount} 个停用、不可见或不再支持的素材未带入`
          : "原任务参数已按当前模型能力调整；确认当前报价后再开始生成";
      }
    } else {
      restoredDraft = { prompt: typeof task.request_payload?.prompt === "string" ? task.request_payload.prompt : "" };
      retryError = "原任务使用的模型已不可用，未恢复素材和能力参数。";
      retryMessage = retryError;
    }

    if (!creationSession.updateQuickDraft(restoredDraft, { panel: expanded ? "specs" : null })) return;
    setPromptError("");
    setFormError(retryError);
    setDownloadError("");
    closeResultDialog();
    setToast(retryMessage);
    globalThis.requestAnimationFrame?.(() => {
      composerRef.current?.querySelector("#prompt")?.focus();
    });
  };

  const retryHistoryTask = (task) => prepareHistoricalTask(task, {
    expanded: true,
    actionLabel: "恢复失败任务草稿",
  });

  const createAgainFromTask = (task) => prepareHistoricalTask(task, {
    expanded: false,
    actionLabel: "用此设置新建草稿",
  });

  const adjustHistoricalTask = (task) => prepareHistoricalTask(task, {
    expanded: true,
    actionLabel: "恢复并调整",
  });

  const openPublicationForArtifact = (artifact, requestedScope = resultTaskScope) => {
    const artifactId = String(artifact?.artifact_id || "").trim();
    const publicationScope = requestedScope === "company" ? "company" : "mine";
    if (!canStartPublication) {
      setToast(isPersonalWorkspace
        ? "个人账号暂未开放发布能力；如需企业发布，请使用独立的受邀企业账号登录。"
        : "当前账号缺少发布权限，或公司尚未启用自动发布。");
      return;
    }
    if (!artifactId) {
      setToast("这份结果尚未取得可发布的作品标识。请刷新作品页后再试。");
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
    generationTaskRuntime.queueSubmission();
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
        generationTaskRuntime.markCancelled(cancelled);
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
    generationTaskRuntime.markCancelled();
    setToast("任务已取消，未产生扣费");
  };

  const logout = async () => {
    setUserMenuOpen(false);
    if (DEMO_MODE) {
      setToast("当前是演示模式，没有真实登录会话");
      return;
    }
    pendingCreateRef.current = null;
    // The authentication layer owns explicit-logout browser-state cleanup.
    // Logging out must never cancel an already accepted provider task.
    closeResultDialog();
    generationTaskRuntime.clearActiveTask();
    resetTaskCollections();
    resetArtworkCollection();
    setAssets([]);
    setArtworkPreviewUrls({});
    setIssuedArtifacts({});
    setPublicationIntent(null);
    await logoutSession();
  };

  const invalidateStudioWorkspaceEvidence = () => {
    studioWorkspaceEvidenceKeyRef.current = "";
    pendingCreateRef.current = null;
    closeResultDialog();
    setModels([]);
    setModelId("");
    setAssets([]);
    resetTaskCollections();
    resetArtworkCollection();
    setArtworkPreviewUrls({});
    setIssuedArtifacts({});
    generationTaskRuntime.clearActiveTask({ resetProgress: true });
    setPublicationIntent(null);
    setPublishingReadiness(null);
    setPublishingReadinessResolved(false);
    setPersonalWallet(null);
  };

  const changeSurface = (nextSurface) => {
    if (!["personal", "studio", "company", "platform"].includes(nextSurface)) return;
    if (!sessionSurfaces.includes(nextSurface)) {
      setToast("当前账号没有这个工作区的访问权限");
      return;
    }
    if (nextSurface !== effectiveSurface && (submitting || cancelling || uploadingKind || generationUploadGate.busy)) {
      setToast("当前操作完成前不能切换工作空间，请稍候。");
      return;
    }
    const nextWorkspaceKey = nextSurface === "personal"
      ? personalIdentity?.workspace_id ? `personal:${personalIdentity.workspace_id}` : ""
      : nextSurface === "studio"
        ? activeCompanyId || companyIdentity?.company_id || ""
        : "";
    if (
      LIVE_MODE
      && nextWorkspaceKey
      && nextWorkspaceKey !== studioWorkspaceEvidenceKeyRef.current
    ) {
      invalidateStudioWorkspaceEvidence();
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

  const changeCompanyContext = (nextCompanyId, { targetSurface = "studio" } = {}) => {
    if (submitting || cancelling || uploadingKind || generationUploadGate.busy) {
      setToast("当前操作完成前不能切换企业，请稍候。");
      return;
    }
    const company = sessionSurfaceCatalog?.companies?.find(
      (item) => item.company_id === nextCompanyId && isActiveCompanyContext(item),
    );
    if (!company) {
      setToast("该企业当前不可用，或不在本次登录的授权范围内。");
      return;
    }
    const nextSurface = targetSurface === "company" ? "company" : "studio";
    invalidateStudioWorkspaceEvidence();
    beginCompanySwitch();
    setActiveCompanyId(company.company_id);
    setSurface(nextSurface);
    const nextPath = surfacePath(nextSurface, activeNav);
    if (globalThis.location?.pathname !== nextPath) {
      globalThis.history?.pushState?.({}, "", nextPath);
    }
    try {
      globalThis.sessionStorage?.setItem("ai-video.company-id", company.company_id);
      globalThis.sessionStorage?.setItem("ai-video.surface", nextSurface);
    } catch {
      // The authorized company selection still applies in memory.
    }
  };

  const switchDemoPersona = (nextPersonaId, { targetNav = "shots" } = {}) => {
    if (!DEMO_MODE) return;
    const nextPersona = resolveDemoPersona(nextPersonaId);
    const nextSurface = defaultSurfaceForIdentity(nextPersona.identity);
    closeResultDialog();
    generationTaskRuntime.clearActiveTask({ resetProgress: true });
    resetTaskCollections();
    resetArtworkCollection();
    setArtworkPreviewUrls({});
    setIssuedArtifacts({});
    setArtifactActionKey("");
    setPublicationIntent(null);
    setDemoProductContext(nextPersona.id === "platform_admin" ? "platform" : "");
    setProductContextSwitch({ target: "", error: "" });
    setDemoPersonaId(nextPersona.id);
    setSurface(nextSurface);
    setActiveNav(targetNav);
    const nextPath = surfacePath(nextSurface, targetNav);
    if (globalThis.location?.pathname !== nextPath) {
      globalThis.history?.pushState?.({}, "", nextPath);
    }
    setNotificationsOpen(false);
    setUserMenuOpen(false);
    setToast(`已切换演示账号：${nextPersona.label}`);
    try {
      globalThis.sessionStorage?.setItem("ai-video.demo-persona", nextPersona.id);
      globalThis.sessionStorage?.setItem("ai-video.surface", nextSurface);
      if (nextPersona.id === "platform_admin") {
        globalThis.sessionStorage?.setItem("ai-video.demo-product-context", "platform");
      } else {
        globalThis.sessionStorage?.removeItem("ai-video.demo-product-context");
      }
    } catch {
      // The explicit demo account still changes for the current page.
    }
  };

  const switchActiveProductContext = async (targetContext) => {
    if (!["personal", "platform"].includes(targetContext)) return;
    if (productContextSwitch.target) return;
    if (!authorizedProductContexts.includes(targetContext)) {
      setProductContextSwitch({
        target: "",
        error: targetContext === "personal"
          ? "当前平台所有者尚未关联个人创作空间，平台会话保持不变。"
          : "当前个人创作空间未关联 Platform，创作会话保持不变。",
      });
      return;
    }
    const nextSurface = targetContext === "personal" ? "personal" : "platform";
    const nextNav = targetContext === "personal" ? "create" : "shots";
    const nextPath = surfacePath(nextSurface, nextNav);
    setToast("");
    setProductContextSwitch({ target: targetContext, error: "" });

    if (DEMO_MODE) {
      setDemoProductContext(targetContext);
      setSurface(nextSurface);
      setActiveNav(nextNav);
      setNotificationsOpen(false);
      setUserMenuOpen(false);
      if (globalThis.location?.pathname !== nextPath) {
        globalThis.history?.pushState?.({}, "", nextPath);
      }
      try {
        globalThis.sessionStorage?.setItem(
          "ai-video.demo-product-context",
          targetContext,
        );
        globalThis.sessionStorage?.setItem("ai-video.surface", nextSurface);
      } catch {
        // The explicit demo product context still changes in memory.
      }
      setProductContextSwitch({ target: "", error: "" });
      setToast(targetContext === "personal"
        ? "已使用平台所有者身份进入个人创作"
        : "已返回 Platform");
      return;
    }

    let contextCommitted = false;
    try {
      const nextSession = await switchProductContext?.({ targetContext });
      const returnedContexts = availableProductContexts(nextSession);
      if (
        nextSession?.active_product_context !== targetContext
        || !returnedContexts.includes(targetContext)
      ) {
        throw new PlatformApiError("账号服务未确认目标产品空间", {
          code: "PRODUCT_CONTEXT_NOT_CONFIRMED",
        });
      }
      contextCommitted = true;
      if (typeof globalThis.location?.assign !== "function") {
        throw new PlatformApiError("当前浏览器无法打开目标产品空间", {
          code: "PRODUCT_CONTEXT_NAVIGATION_UNAVAILABLE",
        });
      }
      globalThis.location.assign(nextPath);
    } catch (error) {
      setProductContextSwitch({
        target: "",
        error: contextCommitted
          ? `${targetContext === "personal" ? "个人创作身份" : "Platform 身份"}已切换，但页面未能自动打开。请刷新页面继续。`
          : `${targetContext === "personal" ? "进入个人创作" : "返回 Platform"}失败：${readableApiError(error)} 当前会话和页面均未切换，可以重试。`,
      });
    }
  };

  const openPersonalCreation = () => {
    switchActiveProductContext("personal");
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
      if (!LIVE_MODE) setToast("演示模式不提供真实下载");
      return;
    }
    if (!canAccessArtifacts) {
      setToast("当前工作区只能查看作品信息，暂不能预览或下载文件。");
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
          throw new PlatformApiError("预览链接有效期无效，请刷新后重试", {
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
          ? `预览已准备好，${access.expires_seconds} 秒内有效，不会记为已下载。`
          : `下载已开始，链接在 ${access.expires_seconds} 秒内有效。完成状态将在文件传输确认后更新。`,
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
      setToast("演示模式不会创建真实参考素材");
      return;
    }
    if (!canAccessArtifacts || isPersonalWorkspace) {
      setToast("个人空间暂不支持预览、下载作品或保存为素材。");
      return;
    }
    if (!canManageAssets) {
      setToast("当前账号不能存入素材库。请联系企业负责人开通素材管理权限。");
      return;
    }

    const key = `${taskId}:${artifact.asset_id}`;
    const promotionContext = generationInputContext();
    setArtifactActionKey(`promote:${key}`);
    setDownloadError("");
    try {
      const promoted = await companyClient.promoteArtifactToInputAsset(
        taskId,
        artifact.asset_id,
        { scope, idempotencyKey: `promote-${taskId}-${artifact.asset_id}` },
      );
      const currentContext = generationInputContext();
      if (currentContext.workspaceKey !== promotionContext.workspaceKey) return;
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

      let addedCount = 0;
      if (addToDraft && canCreateTasks && !currentContext.locked
        && currentContext.draftRevision === promotionContext.draftRevision
        && !generationUploadGate.busy) {
        const kind = inputAssetType(promoted, artifact.media_type);
        addedCount = appendDraftInputs(kind, [promoted]).addedCount;
      }
      setToast(
        addToDraft
          ? addedCount > 0
            ? "已存入素材库，并加入当前草稿。"
            : "已存入素材库，未改动当前草稿；可在素材页按当前模型的能力添加。"
          : "已存入素材库。",
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
          <p>正在确认当前账号类型与唯一工作入口，完成前不会开放任何数据。</p>
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
      <>
        <Suspense fallback={<RouteLoadingFallback label={effectiveSurface === "platform" ? "正在打开平台运营台" : "正在打开企业管理台"} />}>
          <ManagementConsole
            key={DEMO_MODE ? `${demoPersonaId}:${demoProductContext}` : `${effectiveSurface}:${effectiveSurface === "company" ? activeCompanyId : ""}`}
            mode={effectiveSurface}
            client={effectiveSurface === "company" ? companyClient : liveClient}
            demoMode={DEMO_MODE}
            demoIdentity={DEMO_MODE ? sessionIdentity : null}
            demoPersonaId={demoPersonaId}
            allowedSurfaces={sessionSurfaces}
            onDemoPersonaChange={switchDemoPersona}
            onSurfaceChange={changeSurface}
            onOpenPersonalCreation={authorizedProductContexts.includes("personal")
              ? openPersonalCreation
              : undefined}
            personalCreationPending={productContextSwitch.target === "personal"}
            personalCreationErrorMessageId={productContextSwitch.error
              ? "product-context-switch-error"
              : ""}
            initialPlatformIdentity={effectiveSurface === "platform" ? platformIdentity : null}
            companyContexts={activeCompanyContexts}
            activeCompanyId={activeCompanyId}
            onCompanyChange={(companyId) => changeCompanyContext(companyId, { targetSurface: "company" })}
            onLogout={logout}
            onSessionError={expireSessionIfNeeded}
            skin={skin}
            onSkinChange={setSkin}
          />
        </Suspense>
        {productContextSwitch.error && (
          <div id="product-context-switch-error" className="toast is-error" role="alert">
            <WarningCircle size={19} weight="fill" aria-hidden="true" />
            {productContextSwitch.error}
          </div>
        )}
      </>
    );
  }

  const focusComposerPrompt = () => {
    const focusPrompt = () => {
      composerRef.current?.querySelector("#prompt")?.focus({ preventScroll: true });
    };
    if (typeof globalThis.requestAnimationFrame !== "function") {
      focusPrompt();
      return;
    }
    globalThis.requestAnimationFrame(() => {
      globalThis.requestAnimationFrame(focusPrompt);
    });
  };

  const focusCommunityComposer = () => {
    restoreComposerPanelFocusRef.current = false;
    setComposerPanel(null);
    setMobileComposerOpen(true);
    focusComposerPrompt();
  };

  const useCommunityPrompt = (nextPrompt) => {
    if (historicalRequestLocked) {
      setToast("上次提交结果尚未确认，暂时不能替换制作说明");
      focusCommunityComposer();
      return;
    }
    const safeLimit = promptLimit || 1000;
    setPrompt(truncateGenerationPrompt(nextPrompt, safeLimit));
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
  const composerVideoAvailable = modelSupportsMediaKind(model, "video");
  const composerImageAvailable = modelSupportsMediaKind(model, "image");
  const composerReferenceCount = files.image.length + files.video.length + files.audio.length;
  const composerSpecSummary = [
    ratio,
    resolution,
    modeUsesDuration(generationMode) && duration ? `${duration} 秒` : "",
  ].filter(Boolean).join(" · ") || "等待模型能力";

  const composerModelPrice = (candidate) => {
    const pricingUnit = candidate?.pricingMode === "per_second" ? "秒" : "个";
    if (!LIVE_MODE) {
      const amount = normalizePositivePrice(candidate?.rate);
      return amount === null ? "" : `${formatPointAmount(amount)} / ${pricingUnit}`;
    }
    if (!validQuoteRevision(candidate?.quoteRevision)) return "";
    const billing = billingPresentationState(candidate);
    if (billing.kind === "legacy_cents" || !billing.available) return "";
    const amount = normalizePositivePrice(candidate?.unitPricePoints);
    if (amount === null) return "";
    return `${formatPointAmount(amount, {
      noun: billing.kind === "internal_test" ? "影子积分" : "积分",
    })} / ${pricingUnit}`;
  };

  const openComposerPanel = (panel, { focusPanel = true } = {}) => {
    composerPanelTriggerRef.current = composerRef.current
      ?.querySelector(`[data-composer-panel-trigger="${panel}"]`) ?? null;
    focusComposerPanelOnOpenRef.current = focusPanel;
    restoreComposerPanelFocusRef.current = false;
    setComposerPanel(panel);
  };

  const closeComposerPanel = (panel, { restoreFocus = true } = {}) => {
    if (!composerPanelTriggerRef.current) {
      composerPanelTriggerRef.current = composerRef.current
        ?.querySelector(`[data-composer-panel-trigger="${panel}"]`) ?? null;
    }
    restoreComposerPanelFocusRef.current = restoreFocus;
    setComposerPanel(null);
  };

  const toggleComposerPanel = (nextPanel, { focusPanel = true } = {}) => {
    if (composerPanel === nextPanel) {
      closeComposerPanel(nextPanel, { restoreFocus: false });
      return;
    }
    openComposerPanel(nextPanel, { focusPanel });
  };

  const toggleComposerPanelFromTrigger = (event, nextPanel) => {
    toggleComposerPanel(nextPanel, { focusPanel: event.detail === 0 });
  };

  const handleComposerEscape = (event) => {
    if (event.key !== "Escape") return;
    if (composerPanel) {
      event.preventDefault();
      closeComposerPanel(composerPanel);
      return;
    }
    if (!mobileComposerOpen) return;
    event.preventDefault();
    setMobileComposerOpen(false);
    globalThis.requestAnimationFrame?.(() => {
      composerRef.current?.querySelector("#mobile-composer-launcher")?.focus();
    });
  };

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

  function renderGenerationEditor({ variant = "quick", title = "", onClose } = {}) {
    return <GenerationEditor {...{
      variant, title, onClose,
      pendingContext, pendingBelongsToDraft, restorePendingCreate, pendingCreateRef,
      hasVisibleMediaInputs, composerExpanded, composerPanel, mobileComposerOpen,
      composerRef, handleComposerEscape, focusCommunityComposer, activeNav,
      generationMode, generationModeLabel, composerSpecSummary, setComposerPanel,
      setMobileComposerOpen, modelsLoading, model, hasVisibleRecipe,
      toggleComposerPanelFromTrigger, composerVideoAvailable, composerImageAvailable,
      handleComposerMediaKeyDown, composerMediaKind, selectComposerMediaKind,
      supportedModes, selectGenerationMode,
      quickDraftStorageNotice: creationSession.storageNotice,
      activeCapability, closeComposerPanel, prompt, historicalRequestLocked,
      generationPromptLength, promptLimit, setPrompt, truncateGenerationPrompt,
      promptError, setPromptError, formError, setFormError, promptSubmissionIssue,
      composerSubmitMessageId, composerReferenceCount, visibleMediaLimits,
      referenceUploadNote, MediaInputGroup, files, handleFiles, removeInputAsset,
      uploadingKind, LIVE_MODE, canManageAssets, hasVisibleSpecifications,
      ratio, resolution, modeUsesDuration, duration, outputCount,
      setRatio, setResolution, setDuration, setOutputCount, fixedSpecificationFields,
      isPersonalWorkspace, modelsError, models, modelId, composerModelPrice,
      personalModelCatalog, personalModelCatalogLoading, personalModelCatalogError,
      selectStudioModel, DEMO_MODE, generationReadiness, hasServerReadinessEvidence,
      serverGenerationReadiness, localReadinessBlockers, readinessCheckedAtLabel,
      faceControlAvailable, faceEnabled, setFaceEnabled, readinessEvidenceReasons,
      historicalFaceVisible, readinessHeadline, showCostPreview, costLabel,
      startGeneration, composerSubmitState, taskStageIsActive,
    }} />;
  }

  const currentView = (() => {
    if (activeNav === "create") {
      return (
        <CreationHub
          workbench={creationWorkbench}
          advancedWorkbenchesDesktopOnly={advancedWorkbenchesDesktopOnly}
          onWorkbenchChange={navigateCreationWorkbench}
          renderEditor={renderGenerationEditor}
          directorHost={{
            onCaptureFiles: (captureFiles, captureContext) => {
              const route = creationRouteRef.current;
              const binding = creationSession.bindingRef.current;
              const isCurrentShot = Boolean(
                route.activeNav === "create"
                  && route.workbench === "console"
                  && captureContext?.scopeKey === creationScopeRef.current
                  && captureContext.scopeKey === binding?.scopeKey
                  && captureContext.shotId === binding?.entryId
                  && binding?.kind === "console",
              );
              if (!isCurrentShot) {
                return Promise.resolve({
                  ok: false,
                  addedCount: 0,
                  message: "当前镜头已经切换，这张取景图没有写入新的镜头。",
                });
              }
              return handleFiles("image", captureFiles);
            },
          }}
          workbenchController={{
            ...creationSession,
            entries: creationSession.state.entries.filter((entry) => entry.kind === creationWorkbench).map((entry) => ({
              ...entry,
              draft: entry.id === creationSession.activeEntryId ? generationDraftSnapshot : entry.draft,
            })),
            locked: submitting || Boolean(uploadingKind) || Boolean(pendingCreateRef.current),
            onMoveEntry: (id, direction) => creationSession.onMoveEntry(id, direction === "up" ? -1 : 1),
            onImportTask: async (task) => {
              const origin = creationSession.captureContext();
              try {
                const verified = LIVE_MODE ? await studioClient.getTask(task.id, { scope: "mine" }) : task;
                if (origin?.scopeKey !== creationScopeRef.current || origin?.entryId !== creationSession.bindingRef.current?.entryId) return;
                creationSession.onImportTask(verified);
              } catch (error) {
                expireSessionIfNeeded(error);
                setToast("无法读取这条任务，尚未加入工作台。");
              }
            },
          }}
          ownedTaskIds={ownedCreationTaskIds}
          workbenchTasks={LIVE_MODE
            ? Object.values(workbenchRecords.items)
            : [...DEMO_HISTORY_TASKS, ...(currentTask ? [currentTask] : [])]}
          tasks={LIVE_MODE ? historyTasks : DEMO_HISTORY_TASKS}
          models={models}
          loading={LIVE_MODE && historyLoading}
          error={historyError}
          historyAccessAvailable={!LIVE_MODE || canReadStudioTasks}
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
          onUsePrompt={useCommunityPrompt}
          onContinueTask={createAgainFromTask}
          onPromoteArtifact={(task, artifact) => promoteTaskArtifactToInputAsset(
            artifact,
            {
              taskId: task?.id || task?.task_id,
              scope: "mine",
              addToDraft: false,
            },
          )}
          onDownloadArtifact={(task, artifact) => accessArtifact(artifact, {
            taskId: task?.id || task?.task_id,
            scope: "mine",
          })}
          onPublishArtifact={(task, artifact) => openPublicationForArtifact(
            { ...artifact, task_id: task?.id || task?.task_id },
            "mine",
          )}
          canPromoteArtifact={LIVE_MODE && !isPersonalWorkspace && canAccessArtifacts && canManageAssets}
          canDownloadArtifact={LIVE_MODE && canAccessArtifacts}
          canPublishArtifact={canStartPublication}
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
    if (activeNav === "media") {
      if (isPersonalWorkspace && !canManageAssets) {
        return (
          <WorkspaceCapabilityUnavailableView
            capability="素材"
            description="个人空间本期仅开放无素材的文生视频与文生图片；上传、素材库与素材引用尚未开放。"
          />
        );
      }
      if (LIVE_MODE && !canReadStudioAssets && !canManageAssets) {
        return (
          <WorkspaceCapabilityUnavailableView
            capability="素材"
            description="当前账号不能查看或管理企业素材。请联系企业负责人调整素材访问权限。"
          />
        );
      }
      return (
        <MediaLibrary
          liveMode={LIVE_MODE}
          assets={assets}
          loading={assetsLoading}
          error={assetsError}
          uploading={Boolean(uploadingKind)}
          canManageAssets={canManageAssets}
          canCreateTasks={canCreateTasks}
          onUpload={uploadLibraryFiles}
          onAdd={addLibraryAssetToTask}
          onPreview={previewLibraryAsset}
          onDelete={deleteLibraryAsset}
          onUse={(id) => {
            chooseScene(id);
            setToast("已用于当前创作，可继续选择其他素材");
          }}
        />
      );
    }
    if (activeNav === "history") {
      if (LIVE_MODE && !canReadStudioTasks) {
        return (
          <WorkspaceCapabilityUnavailableView
            capability="历史"
            description="当前账号不能查看任务记录。请联系企业负责人调整任务访问权限。"
          />
        );
      }
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
      if (LIVE_MODE && !canReadStudioArtworks) {
        return (
          <WorkspaceCapabilityUnavailableView
            capability="作品"
            description="当前账号不能查看任务与作品，预览和下载也暂不可用。请联系企业负责人调整访问权限。"
          />
        );
      }
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
            description="个人账号的发布、审批与外部平台提交尚未开放；如需企业发布，请使用独立的受邀企业账号登录。"
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
          publishingEntitlementResolved={DEMO_MODE || publishingReadinessResolved}
          accountSideEffectsEnabled={accountPublishingSideEffectsEnabled}
          jobSideEffectsEnabled={jobPublishingSideEffectsEnabled}
          publishingReadinessError={publishingReadinessError}
          publishingBlockingReasons={publishingReadiness?.blocking_reasons || []}
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
          skin={skin}
          onSkinChange={setSkin}
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
                  ? `${activeOutputArtifacts.length} 个作品文件，已保存`
                  : currentTask?.status === "succeeded"
                    ? "任务已完成，但作品保存尚未确认，后续操作已关闭"
                    : "任务和作品状态以服务端记录为准"
                : "演示内容，未生成或保存真实作品"}
            </small>
          </span>
          <button
            className="folder-button"
            type="button"
            onClick={() => {
              if (LIVE_MODE) {
                navigateStudio("history");
              } else {
                setToast("已打开当前项目作品列表");
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
  const isAdvancedWorkbench = activeNav === "create" && ADVANCED_WORKBENCHES.includes(creationWorkbench);
  const isQuickStudioView = isPrimaryStudioView && !isAdvancedWorkbench;
  const studioProjectTitle = isPersonalWorkspace
    ? "个人空间"
    : LIVE_MODE
      ? companyName || "企业工作空间"
      : "防水音箱 15 秒短片";
  const studioProjectCode = currentTask?.id
    ? `任务 ${shortId(currentTask.id)}`
    : LIVE_MODE
      ? "尚未选择任务"
      : "项目 PRJ-20250508-01";

  return (
    <div
      className={`app-shell ${isQuickStudioView ? "is-community-home" : isAdvancedWorkbench ? "is-advanced-workbench" : "is-secondary-page"} ${activeNav === "create" ? "is-creation-hub" : ""} ${isQuickStudioView && composerExpanded ? "is-composer-expanded" : ""} ${isQuickStudioView && mobileComposerOpen ? "is-mobile-composer-open" : ""}`}
      data-workbench={isAdvancedWorkbench ? creationWorkbench : undefined}
      data-theme={skin}
      data-composer-surface={activeNav === "create" ? "creation" : "home"}
    >
      <header className="topbar">
        <div className="studio-mobile-commandbar">
          <div className="studio-mobile-mark" aria-label={BRAND_NAME}>
            <BrandLogo variant="symbol" />
          </div>
          <div className="studio-mobile-context" aria-label="当前创作上下文">
            <small>
              创作
              <span aria-hidden="true"> · </span>
              {DEMO_MODE
                ? activeDemoPersona?.identity?.display_name || "演示账号"
                : sessionIdentity?.display_name || "已登录用户"}
            </small>
            <strong>{studioProjectTitle}</strong>
          </div>
          <details
            className="studio-mobile-command-menu"
            onBlur={(event) => {
              if (!event.currentTarget.contains(event.relatedTarget)) event.currentTarget.open = false;
            }}
            onKeyDown={(event) => {
              if (event.key !== "Escape") return;
              event.preventDefault();
              event.currentTarget.open = false;
              event.currentTarget.querySelector("summary")?.focus();
            }}
          >
            <summary aria-label="打开工作台命令菜单" aria-haspopup="true">
              <SlidersHorizontal size={20} aria-hidden="true" />
            </summary>
            <div className="studio-mobile-command-panel">
              {studioManagementSurfaces.length > 0 && (
                <section>
                  <span>管理入口</span>
                  <div className="studio-mobile-surface-list">
                    {sessionSurfaces.includes("company") && (
                      <button type="button" aria-pressed="false" onClick={() => changeSurface("company")}>企业管理</button>
                    )}
                    {canReturnToPlatform ? (
                      <button
                        type="button"
                        aria-pressed="false"
                        aria-label="返回 Platform"
                        aria-busy={productContextSwitch.target === "platform" ? "true" : undefined}
                        aria-describedby={productContextSwitch.error ? "product-context-switch-error" : undefined}
                        disabled={productContextSwitch.target === "platform"}
                        onClick={() => switchActiveProductContext("platform")}
                      >
                        {productContextSwitch.target === "platform" ? "正在返回" : "返回 Platform"}
                      </button>
                    ) : sessionSurfaces.includes("platform") ? (
                      <button type="button" aria-pressed="false" onClick={() => changeSurface("platform")}>平台运营</button>
                    ) : null}
                  </div>
                </section>
              )}
              {LIVE_MODE ? (
                canSwitchCompany && (
                  <section>
                    <span>当前企业</span>
                    <label className="studio-mobile-select">
                      <span className="visually-hidden">切换当前企业</span>
                      <select
                        value={activeCompanyId || sessionIdentity?.company_id || ""}
                        onChange={(event) => changeCompanyContext(event.target.value)}
                      >
                        {activeCompanyContexts.map((company) => (
                          <option key={company.company_id} value={company.company_id}>
                            {company.name || company.company_id}
                          </option>
                        ))}
                      </select>
                      <CaretDown size={14} aria-hidden="true" />
                    </label>
                  </section>
                )
              ) : (
                <section>
                  <span>当前项目</span>
                  <label className="studio-mobile-select">
                    <span className="visually-hidden">当前项目</span>
                    <select defaultValue="产品推广视频">
                      <option>产品推广视频</option>
                      <option>夏季带货系列</option>
                      <option>品牌素材测试</option>
                    </select>
                    <CaretDown size={14} aria-hidden="true" />
                  </label>
                </section>
              )}
              {DEMO_MODE && <section><span>演示身份</span><DemoAccountSwitcher value={demoPersonaId} onChange={switchDemoPersona} /></section>}
              <section><span>界面</span><SkinSwitcher value={skin} onChange={setSkin} /></section>
              <section className="studio-mobile-task-state" aria-live="polite">
                <span>任务提醒</span>
                <strong>
                  {LIVE_MODE
                    ? currentTask
                      ? `任务 ${shortId(currentTask.id)} · ${taskStatus.label}`
                      : "当前没有选中的真实任务"
                    : "演示数据不会连接真实生成渠道"}
                </strong>
                {LIVE_MODE && currentTask && (
                  <small>{hasStoredArtifacts ? `${activeOutputArtifacts.length} 个作品文件已保存` : "作品状态正在同步"}</small>
                )}
              </section>
              {LIVE_MODE && (
                <section className="studio-mobile-account-actions">
                  <span>{sessionIdentity?.display_name || "已登录用户"} · {identityRoleLabel(sessionIdentity)}</span>
                  <div>
                    <button type="button" onClick={() => navigateStudio("settings")}>账号设置</button>
                    <button type="button" onClick={logout}>退出登录</button>
                  </div>
                </section>
              )}
            </div>
          </details>
        </div>
        <div className="topbar-identity">
          <div className="brand" aria-label={BRAND_NAME}>
            <BrandLogo variant="responsive" />
          </div>
          <div className="topbar-project" aria-label="当前工作空间与任务">
            <span>当前工作空间</span>
            <strong>{studioProjectTitle}</strong>
            <small>{studioProjectCode}</small>
          </div>
        </div>
        <div className="topbar-command-cluster">
          {DEMO_MODE && <span className="mode-badge">演示模式</span>}
          {studioManagementSurfaces.length > 0 && (
            <div className="surface-switch is-studio" aria-label="管理入口">
              {sessionSurfaces.includes("company") && <button type="button" aria-pressed="false" aria-label="企业管理工作区" onClick={() => changeSurface("company")}><span className="surface-label-long">企业管理</span><span className="surface-label-short" aria-hidden="true">管理</span></button>}
              {canReturnToPlatform ? (
                <button
                  type="button"
                  aria-pressed="false"
                  aria-label="返回 Platform"
                  aria-busy={productContextSwitch.target === "platform" ? "true" : undefined}
                  aria-describedby={productContextSwitch.error ? "product-context-switch-error" : undefined}
                  disabled={productContextSwitch.target === "platform"}
                  onClick={() => switchActiveProductContext("platform")}
                >
                  {productContextSwitch.target === "platform" ? "正在返回" : "返回 Platform"}
                </button>
              ) : sessionSurfaces.includes("platform") ? (
                <button type="button" aria-pressed="false" onClick={() => changeSurface("platform")}>平台运营</button>
              ) : null}
            </div>
          )}
          {LIVE_MODE ? (
            canSwitchCompany && (
            <label className="project-select workspace-select">
              <span className="visually-hidden">切换当前企业</span>
              <FolderOpen className="project-select-mobile-icon" size={18} aria-hidden="true" />
              <select
                value={activeCompanyId || sessionIdentity?.company_id || ""}
                onChange={(event) => changeCompanyContext(event.target.value)}
              >
                {activeCompanyContexts.map((company) => (
                  <option key={company.company_id} value={company.company_id}>
                    {company.name || company.company_id}
                  </option>
                ))}
              </select>
              <CaretDown size={14} aria-hidden="true" />
            </label>
            )
          ) : (
            <label className="project-select">
              <span className="visually-hidden">当前项目</span>
              <FolderOpen className="project-select-mobile-icon" size={18} aria-hidden="true" />
              <select defaultValue="产品推广视频">
                <option>产品推广视频</option>
                <option>夏季带货系列</option>
                <option>品牌素材测试</option>
              </select>
              <CaretDown size={14} aria-hidden="true" />
            </label>
          )}
        </div>
        <div className="topbar-spacer" />
        <div className="topbar-account-cluster">
          {DEMO_MODE && (
            <DemoAccountSwitcher value={demoPersonaId} onChange={switchDemoPersona} />
          )}
          {LIVE_MODE && isPersonalWorkspace && (
            <span
              className={`personal-balance ${personalWalletError ? "is-error" : ""}`}
              title={personalWalletError || (personalWallet
                ? `预留 ${billingAmountLabel(personalWallet, {
                    pointsField: "reserved_points",
                    centsField: "reserved_cents",
                  })}`
                : "正在读取个人积分")}
            >
              {personalWalletError
                ? "积分读取失败"
                : personalWallet
                  ? billingAmountLabel(personalWallet, {
                      pointsField: "available_points",
                      centsField: "available_cents",
                    })
                  : "积分读取中"}
            </span>
          )}
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
                        ? `${activeOutputArtifacts.length} 个作品文件已保存。`
                        : "作品状态正在同步。"}
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
        </div>
      </header>

      <nav className="side-nav" aria-label="工作台导航">
        <div className="side-nav-track" ref={studioNavTrackRef}>
          <div className="side-nav-primary">
            {visibleStudioNavItems.map((item) => {
              const Icon = item.icon;
              return (
                <button
                  className={activeNav === item.id ? "is-active" : ""}
                  type="button"
                  key={item.id}
                  onClick={() => navigateStudio(item.id)}
                  onFocus={(event) => event.currentTarget.scrollIntoView({ block: "nearest", inline: "nearest" })}
                  aria-current={activeNav === item.id ? "page" : undefined}
                >
                  <Icon size={26} aria-hidden="true" />
                  <span>{item.label}</span>
                </button>
              );
            })}
          </div>
          <div className="side-nav-footer">
            <button
              className={activeNav === "settings" ? "is-active" : ""}
              type="button"
              onClick={() => navigateStudio("settings")}
              onFocus={(event) => event.currentTarget.scrollIntoView({ block: "nearest", inline: "nearest" })}
              aria-current={activeNav === "settings" ? "page" : undefined}
            >
              <Gear size={27} aria-hidden="true" />
              <span>设置</span>
            </button>
          </div>
        </div>
        <button
          className="side-nav-scroll-forward"
          type="button"
          aria-label="显示后续导航；抵达末尾后返回开头"
          onClick={() => {
            const track = studioNavTrackRef.current;
            if (!track) return;
            const atEnd = track.scrollLeft + track.clientWidth >= track.scrollWidth - 4;
            track.scrollTo({ left: atEnd ? 0 : track.scrollLeft + Math.max(152, track.clientWidth * 0.62), behavior: "smooth" });
          }}
        >
          <CaretRight size={18} aria-hidden="true" />
        </button>
      </nav>

      <main ref={mainCanvasRef} className="main-canvas">
        <Suspense fallback={(
          <RouteLoadingFallback
            label={`正在打开${activeNav === "settings" ? "设置" : NAV_ITEMS.find((item) => item.id === activeNav)?.label || "工作台"}`}
          />
        )}>
          {currentView}
        </Suspense>
      </main>

      {isQuickStudioView && renderGenerationEditor()}

      {!isPrimaryStudioView && (taskStageIsActive || taskStageNeedsAttention) && <footer
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
          {taskTrackingUnavailable
            ? "已提交，当前账号不能查看进度"
            : taskStageIsActive
              ? LIVE_MODE
                ? activeTaskTimingLabel
                : stage === "accepted"
                  ? "演示任务已接收"
                  : stage === "queued"
                    ? "演示等待调度"
                    : "演示生成中"
              : stage === "complete"
                ? LIVE_MODE ? "已转存" : "演示完成，未转存"
                : stage === "artifact-evidence-missing"
                  ? "作品保存未确认"
                : stage === "timed-out"
                  ? "已停止等待"
                  : stage === "reconciliation-required"
                    ? "不会自动重试"
                    : stage === "unknown"
                      ? "请刷新确认"
                : stage === "failed" && currentTask?.failure_reason
                  ? taskUserMessage(currentTask.failure_reason, taskStatus.detail)
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
            {LIVE_MODE ? `查看作品${activeOutputArtifacts.length ? ` (${activeOutputArtifacts.length})` : ""}` : "查看成片"}
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
            title={LIVE_MODE && !canCreateTasks ? "当前账号不能创建任务" : undefined}
          >
            <ArrowCounterClockwise size={17} aria-hidden="true" />
            重新生成
          </button>
        )}
      </footer>}

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
            <Suspense fallback={<RouteLoadingFallback label="正在读取任务详情" compact />}>
              <ResultDetailView
                resultTask={resultTask}
                liveMode={LIVE_MODE}
                resultTaskStatus={resultTaskStatus}
                ResultStatusIcon={ResultStatusIcon}
                resultArtifactEvidence={resultArtifactEvidence}
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
              />
            </Suspense>
          </section>
        </div>
      )}

      {toast && (
        <div className="toast" role="status">
          <CheckCircle size={19} weight="fill" aria-hidden="true" />
          {toast}
        </div>
      )}
      {productContextSwitch.error && (
        <div id="product-context-switch-error" className="toast is-error" role="alert">
          <WarningCircle size={19} weight="fill" aria-hidden="true" />
          {productContextSwitch.error}
        </div>
      )}
    </div>
  );
}
