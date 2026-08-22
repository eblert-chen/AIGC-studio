import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowClockwise,
  ArrowSquareOut,
  CalendarBlank,
  CheckCircle,
  ClipboardText,
  Flask,
  ImageSquare,
  LinkSimple,
  MagnifyingGlass,
  PaperPlaneTilt,
  PlugsConnected,
  SpinnerGap,
  TrashSimple,
  VideoCamera,
  WarningCircle,
  X,
} from "@phosphor-icons/react";
import { PlatformApiError } from "./api/platformClient.js";
import { shortId } from "./components/studio/studioPresentation.js";
import { activePreviewUrl } from "./previewLeases.js";
import {
  collectionItems,
  defaultPublishingTimeZone,
  instantToLocalSchedule,
  localScheduleToOffsetIso,
  PUBLISHING_TIME_ZONES,
  publicationActionAvailability,
  publicationArtifactId,
  publicationJobArtifactId,
  publicationStatus,
  PUBLICATION_STATUS_FILTERS,
} from "./publishing.js";

const BUSY_LABELS = {
  approve: "正在批准",
  cancel: "正在取消",
  retry: "正在重试",
  detail: "正在读取",
};

// Mock publisher creation is a development-only backend probe. Vite replaces
// these constants at build time, allowing the production bundle to remove the
// entry point and its provider=mock submission path entirely.
const DEVELOPMENT_MOCK_PUBLISHING = import.meta.env.DEV && !import.meta.env.PROD;
const PUBLICATION_ARTWORK_PAGE_SIZE = 24;

const DIALOG_FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(",");

function useAccessibleDialog(open, onClose) {
  const dialogRef = useRef(null);
  const onCloseRef = useRef(onClose);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!open || typeof document === "undefined") return undefined;
    const dialog = dialogRef.current;
    if (!dialog) return undefined;

    const returnFocusTarget = document.activeElement;
    const previousBodyOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const focusableElements = () => Array.from(
      dialog.querySelectorAll(DIALOG_FOCUSABLE_SELECTOR),
    ).filter((element) => (
      element.getAttribute("aria-hidden") !== "true"
      && (element.offsetWidth > 0 || element.offsetHeight > 0 || element.getClientRects().length > 0)
    ));

    const initialFocus = dialog.querySelector("[data-dialog-initial-focus]")
      || focusableElements()[0]
      || dialog;
    initialFocus.focus({ preventScroll: true });

    const handleKeyDown = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current?.();
        return;
      }
      if (event.key !== "Tab") return;

      const focusable = focusableElements();
      if (focusable.length === 0) {
        event.preventDefault();
        dialog.focus({ preventScroll: true });
        return;
      }

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && (document.activeElement === first || !dialog.contains(document.activeElement))) {
        event.preventDefault();
        last.focus({ preventScroll: true });
      } else if (!event.shiftKey && (document.activeElement === last || !dialog.contains(document.activeElement))) {
        event.preventDefault();
        first.focus({ preventScroll: true });
      }
    };

    dialog.addEventListener("keydown", handleKeyDown);
    return () => {
      dialog.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = previousBodyOverflow;
      if (returnFocusTarget instanceof HTMLElement && returnFocusTarget.isConnected) {
        returnFocusTarget.focus({ preventScroll: true });
      }
    };
  }, [open]);

  return dialogRef;
}

function readablePublishingError(error) {
  if (error instanceof PlatformApiError) {
    if (error.status === 403) return "当前账号没有权限，或公司尚未开通自动发布。";
    if (error.status === 409) return "任务状态已经变化，请刷新后再操作。";
    return error.message;
  }
  return error?.message || "发布服务请求失败，请稍后重试。";
}

function shortDate(value, timeZone) {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "时间未记录";
  try {
    return new Intl.DateTimeFormat("zh-CN", {
      timeZone: timeZone || undefined,
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(date);
  } catch {
    return new Intl.DateTimeFormat("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(date);
  }
}

function createIdempotencyKey() {
  return globalThis.crypto?.randomUUID?.() ?? `publish-${Date.now()}-${Math.random()}`;
}

function safePublishedUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" ? url.href : "";
  } catch {
    return "";
  }
}

function StatusBadge({ value }) {
  const status = publicationStatus(value);
  return <span className={`publication-status is-${status.tone}`}>{status.label}</span>;
}

function EmptyState({ icon: Icon, title, detail, error = false }) {
  return (
    <div className={`publication-empty ${error ? "is-error" : ""}`} role={error ? "alert" : undefined}>
      <Icon size={28} aria-hidden="true" />
      <strong>{title}</strong>
      <span>{detail}</span>
    </div>
  );
}

function LoadingRows({ label }) {
  return (
    <div className="publication-loading" aria-label={label} aria-busy="true">
      {[0, 1, 2].map((index) => <span key={index} />)}
      <small>{label}</small>
    </div>
  );
}

function ArtworkChoice({
  artwork,
  selected,
  onSelect,
  demoMode,
  previewUrl = "",
  previewLoading = false,
  onRequestPreview,
  onPreviewError,
}) {
  const artifactId = publicationArtifactId(artwork);
  const MediaIcon = artwork.media_type === "image" ? ImageSquare : VideoCamera;
  return (
    <div className={`publication-artwork-choice ${selected ? "is-selected" : ""}`}>
      <label className="publication-artwork-select">
        <input
          type="radio"
          name="artifactId"
          value={artifactId}
          checked={selected}
          onChange={() => onSelect(artifactId)}
        />
        <span className="publication-artwork-thumb">
          {!demoMode && artwork.media_type === "video" && previewUrl ? (
            <video
              src={previewUrl}
              muted
              playsInline
              preload="metadata"
              aria-label="可发布视频作品安全预览"
              onError={onPreviewError}
            />
          ) : previewUrl || (demoMode && artwork.preview_url) ? (
            <img
              src={previewUrl || artwork.preview_url}
              alt={demoMode ? "测试发布作品预览" : "可发布作品安全预览"}
              onError={previewUrl ? onPreviewError : undefined}
            />
          ) : (
            <MediaIcon size={28} weight="duotone" aria-hidden="true" />
          )}
        </span>
        <span>
          <strong>{artwork.request_payload?.prompt || artwork.model_display_name || "已归档作品"}</strong>
          <small>{artwork.media_type === "image" ? "图片" : "视频"}，作品 {shortId(artifactId)}</small>
        </span>
        <CheckCircle size={20} weight={selected ? "fill" : "regular"} aria-hidden="true" />
      </label>
      {!demoMode && !previewUrl && (
        <button
          type="button"
          className="publication-artwork-preview-button"
          disabled={previewLoading}
          onClick={() => onRequestPreview?.(artwork)}
        >
          {previewLoading ? <SpinnerGap className="spin" size={14} aria-hidden="true" /> : null}
          {previewLoading ? "加载中" : "预览"}
        </button>
      )}
    </div>
  );
}

function PublicationComposer({
  open,
  onClose,
  onSubmit,
  submitting,
  demoMode,
  artworks,
  artworksLoading,
  artworksError,
  artworksPage = 1,
  artworksTotal = 0,
  onArtworksPageChange,
  previewUrls = {},
  previewActionKey = "",
  onRequestPreview,
  onPreviewError,
  connections,
  initialArtifactId = "",
  initialSelectionRequest = null,
}) {
  const [artifactId, setArtifactId] = useState("");
  const [connectionId, setConnectionId] = useState("");
  const [title, setTitle] = useState("");
  const [caption, setCaption] = useState("");
  const [timezone, setTimezone] = useState(defaultPublishingTimeZone);
  const [scheduledLocal, setScheduledLocal] = useState(() => (
    instantToLocalSchedule(
      new Date(Date.now() + 60 * 60_000),
      defaultPublishingTimeZone(),
    )
  ));
  const [error, setError] = useState("");
  const dialogRef = useAccessibleDialog(open, onClose);
  const appliedInitialSelectionRef = useRef("");

  useEffect(() => {
    if (!open) {
      appliedInitialSelectionRef.current = "";
      setError("");
      return;
    }

    const hasExternalInitialSelection =
      initialSelectionRequest !== null && initialSelectionRequest !== undefined;
    const requestedArtifactId = publicationArtifactId({
      artifact_id: initialArtifactId,
    });
    if (!requestedArtifactId) {
      if (hasExternalInitialSelection) {
        setArtifactId("");
        setError("未指定要发布的归档作品，请从作品页重新发起发布。");
        return;
      }
      setArtifactId((current) => current || publicationArtifactId(artworks[0]));
      return;
    }

    const selectionKey = `${String(initialSelectionRequest ?? "external")}:${requestedArtifactId}`;
    if (appliedInitialSelectionRef.current === selectionKey || artworksLoading) return;
    if (artworksError) {
      setArtifactId("");
      setError(`无法核对指定作品：${artworksError}`);
      return;
    }

    const matchedArtwork = artworks.find(
      (artwork) => publicationArtifactId(artwork) === requestedArtifactId,
    );
    if (!matchedArtwork) {
      setArtifactId("");
      setError("指定作品未在当前可发布作品中找到，请重新选择已完成并归档的作品。");
      return;
    }

    appliedInitialSelectionRef.current = selectionKey;
    setArtifactId(publicationArtifactId(matchedArtwork));
    setError("");
  }, [
    artworks,
    artworksError,
    artworksLoading,
    initialArtifactId,
    initialSelectionRequest,
    open,
  ]);

  useEffect(() => {
    if (!open) return;
    setConnectionId((current) => current || String(connections[0]?.id || ""));
    setScheduledLocal((current) => {
      try {
        const currentInstant = Date.parse(localScheduleToOffsetIso(current, timezone));
        if (currentInstant >= Date.now() + 5 * 60_000) return current;
      } catch {
        // Reset an invalid, ambiguous, or expired wall time below.
      }
      return instantToLocalSchedule(new Date(Date.now() + 60 * 60_000), timezone);
    });
  }, [artworks, connections, open, timezone]);

  if (!open) return null;

  const submit = async (event) => {
    event.preventDefault();
    setError("");
    if (demoMode) {
      setError("这是测试发布界面，不会创建发布任务或调用外部平台。");
      return;
    }
    if (!artifactId || !connectionId || !caption.trim() || !scheduledLocal) {
      setError("请选择作品和发布账号，并填写文案与发布时间。");
      return;
    }
    try {
      const scheduledAt = localScheduleToOffsetIso(scheduledLocal, timezone);
      if (Date.parse(scheduledAt) < Date.now() + 5 * 60_000) {
        throw new Error("发布时间至少要晚于当前时间 5 分钟");
      }
      await onSubmit({
        artifactId,
        connectionId,
        title: title.trim(),
        caption: caption.trim(),
        scheduledAt,
        timezone,
      });
    } catch (submitError) {
      setError(readablePublishingError(submitError));
    }
  };

  const changeTimezone = (nextTimezone) => {
    setTimezone(nextTimezone);
    try {
      const scheduledInstant = Date.parse(localScheduleToOffsetIso(scheduledLocal, nextTimezone));
      if (scheduledInstant >= Date.now() + 5 * 60_000) {
        setError("");
        return;
      }
    } catch {
      // Reset an invalid or ambiguous wall time in the newly selected zone.
    }
    setScheduledLocal(instantToLocalSchedule(new Date(Date.now() + 60 * 60_000), nextTimezone));
    setError("时区已切换，原发布时间在新时区无效或已过期，已按新时区重置，请再次确认。");
  };

  const minimumScheduledLocal = instantToLocalSchedule(
    new Date(Date.now() + 5 * 60_000),
    timezone,
  );

  return (
    <div className="publication-dialog-backdrop" role="presentation">
      <section ref={dialogRef} className="publication-dialog" role="dialog" aria-modal="true" aria-labelledby="publication-composer-title" tabIndex={-1}>
        <header>
          <div>
            <span>{demoMode ? "测试发布" : "待审核发布"}</span>
            <h2 id="publication-composer-title">安排作品发布</h2>
            <p>提交后先进入人工审核，批准后才会进入定时队列。</p>
          </div>
          <button type="button" className="icon-button" aria-label="关闭发布编辑器" onClick={onClose} data-dialog-initial-focus><X size={20} /></button>
        </header>
        <form onSubmit={submit}>
          <fieldset className="publication-source-fieldset">
            <legend>选择已完成作品</legend>
            {artworksLoading && <LoadingRows label="正在读取已完成作品" />}
            {!artworksLoading && artworksError && <EmptyState icon={WarningCircle} title="作品读取失败" detail={artworksError} error />}
            {!artworksLoading && !artworksError && artworks.length === 0 && (
              <EmptyState icon={ImageSquare} title="还没有可发布作品" detail="生成并完成私有转存后，作品才可以进入发布流程。" />
            )}
            {!artworksLoading && !artworksError && artworks.length > 0 && (
              <div className="publication-artwork-list">
                {artworks.map((artwork) => {
                  const previewKey = `${artwork.task_id}:${artwork.asset_id}`;
                  return (
                    <ArtworkChoice
                      key={publicationArtifactId(artwork)}
                      artwork={artwork}
                      selected={publicationArtifactId(artwork) === artifactId}
                      onSelect={(nextArtifactId) => {
                        setArtifactId(nextArtifactId);
                        setError("");
                      }}
                      demoMode={demoMode}
                      previewUrl={activePreviewUrl(previewUrls[previewKey])}
                      previewLoading={previewActionKey === `preview:${previewKey}`}
                      onRequestPreview={onRequestPreview}
                      onPreviewError={() => onPreviewError?.(previewKey)}
                    />
                  );
                })}
              </div>
            )}
            {!artworksLoading && !artworksError && artworksTotal > PUBLICATION_ARTWORK_PAGE_SIZE && (
              <nav className="publication-pagination" aria-label="可发布作品分页">
                <button type="button" disabled={artworksPage <= 1} onClick={() => onArtworksPageChange?.(artworksPage - 1)}>上一页</button>
                <span>第 {artworksPage} / {Math.ceil(artworksTotal / PUBLICATION_ARTWORK_PAGE_SIZE)} 页</span>
                <button type="button" disabled={artworksPage * PUBLICATION_ARTWORK_PAGE_SIZE >= artworksTotal} onClick={() => onArtworksPageChange?.(artworksPage + 1)}>下一页</button>
              </nav>
            )}
          </fieldset>

          <div className="publication-form-grid">
            <label>
              <span>发布账号</span>
              <select value={connectionId} onChange={(event) => setConnectionId(event.target.value)} disabled={demoMode || connections.length === 0} required>
                <option value="">选择已连接账号</option>
                {connections.map((connection) => (
                  <option key={connection.id} value={connection.id}>{connection.display_name || connection.external_account_id || connection.provider}</option>
                ))}
              </select>
              <small>{demoMode ? "测试发布不使用真实账号" : "账号凭据只保存在发布服务端"}</small>
            </label>
            <label>
              <span>发布标题</span>
              <input value={title} onChange={(event) => setTitle(event.target.value)} maxLength={120} placeholder="可选，用于支持标题的平台" />
            </label>
          </div>
          <label>
            <span>发布文案</span>
            <textarea value={caption} onChange={(event) => setCaption(event.target.value)} maxLength={2000} rows={4} placeholder="填写随作品发布的文案" required />
            <small>{caption.length} / 2000</small>
          </label>
          <div className="publication-form-grid">
            <label>
              <span>发布时间</span>
              <input type="datetime-local" value={scheduledLocal} min={minimumScheduledLocal} onChange={(event) => setScheduledLocal(event.target.value)} required />
            </label>
            <label>
              <span>时区</span>
              <select value={timezone} onChange={(event) => changeTimezone(event.target.value)}>
                {[...new Set([timezone, ...PUBLISHING_TIME_ZONES])].map((zone) => <option key={zone} value={zone}>{zone}</option>)}
              </select>
            </label>
          </div>
          {error && <p className="publication-form-error" role="alert"><WarningCircle size={17} /> {error}</p>}
          <footer>
            <button type="button" onClick={onClose}>返回</button>
            <button className="is-primary" type="submit" disabled={submitting || (!demoMode && (!artifactId || !connections.length))}>
              {submitting ? <SpinnerGap className="spin" size={17} /> : <PaperPlaneTilt size={17} weight="fill" />}
              {demoMode ? "测试发布不提交" : submitting ? "正在提交" : "提交待审核"}
            </button>
          </footer>
        </form>
      </section>
    </div>
  );
}

function JobDetails({ job, loading, onClose, connectionsById }) {
  const open = Boolean(job);
  const dialogRef = useAccessibleDialog(open, onClose);
  if (!open) return null;
  const publishedUrl = safePublishedUrl(job?.external_post_url || job?.published_url);
  const connection = connectionsById.get(job?.connection_id);
  return (
    <div className="publication-dialog-backdrop" role="presentation">
      <section ref={dialogRef} className="publication-details" role="dialog" aria-modal="true" aria-labelledby="publication-details-title" tabIndex={-1}>
        <header>
          <div><span>发布任务详情</span><h2 id="publication-details-title">{job?.title || `任务 ${shortId(job?.id)}`}</h2></div>
          <button type="button" className="icon-button" aria-label="关闭任务详情" onClick={onClose} data-dialog-initial-focus><X size={20} /></button>
        </header>
        {loading ? <LoadingRows label="正在读取发布任务" /> : (
          <>
            <StatusBadge value={job.status} />
            <p className="publication-details-caption">{job.caption || "发布文案未记录"}</p>
            <dl>
              <div><dt>任务 ID</dt><dd>{job.id}</dd></div>
              <div><dt>发布账号</dt><dd>{connection?.display_name || job.connection_id}</dd></div>
              <div><dt>作品 ID</dt><dd>{publicationJobArtifactId(job)}</dd></div>
              <div><dt>计划时间</dt><dd>{job.scheduled_at ? `${shortDate(job.scheduled_at, job.timezone)} (${job.timezone})` : "审核通过后尽快发布"}</dd></div>
              <div><dt>失败原因</dt><dd>{job.error_message || job.failure_reason || job.last_error || "无"}</dd></div>
              <div><dt>外部作品号</dt><dd>{job.external_post_id || job.external_publication_id || job.provider_publication_id || "未返回"}</dd></div>
            </dl>
            {job.status === "submission_unknown" && (
              <p className="publication-unknown-warning"><WarningCircle size={18} /> 提交结果未知，系统不会自动重试。请先在目标平台核对，避免重复发布。</p>
            )}
            {publishedUrl && <a href={publishedUrl} target="_blank" rel="noreferrer">查看已发布作品 <ArrowSquareOut size={16} /></a>}
          </>
        )}
      </section>
    </div>
  );
}

function ReconcileDialog({ job, submitting, onClose, onSubmit }) {
  const [outcome, setOutcome] = useState("published");
  const [externalPostId, setExternalPostId] = useState("");
  const [externalPostUrl, setExternalPostUrl] = useState("");
  const [errorCode, setErrorCode] = useState("");
  const [errorMessage, setErrorMessage] = useState("");
  const [formError, setFormError] = useState("");
  const dialogRef = useAccessibleDialog(Boolean(job), onClose);

  useEffect(() => {
    if (!job) return;
    setOutcome("published");
    setExternalPostId("");
    setExternalPostUrl("");
    setErrorCode("");
    setErrorMessage("");
    setFormError("");
  }, [job?.id]);

  if (!job) return null;

  const submit = async (event) => {
    event.preventDefault();
    setFormError("");
    if (outcome === "published" && !externalPostId.trim()) {
      setFormError("核销为已发布时，必须填写渠道后台返回的作品号。");
      return;
    }
    try {
      await onSubmit({
        outcome,
        externalPostId,
        externalPostUrl,
        errorCode,
        errorMessage,
      });
    } catch (error) {
      setFormError(readablePublishingError(error));
    }
  };

  return (
    <div className="publication-dialog-backdrop" role="presentation">
      <section ref={dialogRef} className="publication-reconcile" role="dialog" aria-modal="true" aria-labelledby="publication-reconcile-title" tabIndex={-1}>
        <header>
          <div><span>结果未知任务</span><h2 id="publication-reconcile-title">人工核销</h2></div>
          <button type="button" className="icon-button" aria-label="关闭人工核销" onClick={onClose} data-dialog-initial-focus><X size={20} /></button>
        </header>
        <form onSubmit={submit}>
          <p className="publication-reconcile-warning"><WarningCircle size={19} /> <span><strong>必须先去渠道后台核对，避免重复发布</strong><small>只有确认渠道最终结果后才能核销。结果未知任务不允许直接重试。</small></span></p>
          <label>
            <span>渠道最终结果</span>
            <select value={outcome} onChange={(event) => setOutcome(event.target.value)}>
              <option value="published">已在渠道发布</option>
              <option value="failed">确认未发布</option>
            </select>
          </label>
          {outcome === "published" ? (
            <div className="publication-reconcile-fields">
              <label><span>渠道作品号</span><input value={externalPostId} onChange={(event) => setExternalPostId(event.target.value)} maxLength={255} required placeholder="渠道后台显示的作品 ID" /></label>
              <label><span>渠道作品地址（可选）</span><input type="url" pattern="https://.*" value={externalPostUrl} onChange={(event) => setExternalPostUrl(event.target.value)} placeholder="https://" /></label>
            </div>
          ) : (
            <div className="publication-reconcile-fields">
              <label><span>失败代码（可选）</span><input value={errorCode} onChange={(event) => setErrorCode(event.target.value)} maxLength={120} placeholder="例如 CHANNEL_CONFIRMED_MISSING" /></label>
              <label><span>核对说明（可选）</span><textarea value={errorMessage} onChange={(event) => setErrorMessage(event.target.value)} maxLength={1000} rows={3} placeholder="记录渠道后台的核对结果" /></label>
            </div>
          )}
          {formError && <p className="publication-form-error" role="alert"><WarningCircle size={17} /> {formError}</p>}
          <footer>
            <button type="button" onClick={onClose}>返回任务</button>
            <button className="is-primary" type="submit" disabled={submitting}>
              {submitting ? <SpinnerGap className="spin" size={17} /> : <CheckCircle size={17} />}
              {submitting ? "正在核销" : "确认人工核销"}
            </button>
          </footer>
        </form>
      </section>
    </div>
  );
}

export function PublishingCenter({
  client,
  demoMode: requestedDemoMode = false,
  artworks = [],
  artworksLoading = false,
  artworksError = "",
  canReadAccounts = false,
  canManageAccounts = false,
  canReadJobs = false,
  canManageJobs = false,
  autoPublishingEnabled = false,
  publishingEntitlementResolved = false,
  onSessionError,
  initialArtifactId = "",
  initialArtwork = null,
  initialArtworkScope = "mine",
  openComposerRequest = null,
  previewUrls = {},
  previewActionKey = "",
  onRequestArtworkPreview,
  onPreviewError,
}) {
  const demoMode = !import.meta.env.PROD && requestedDemoMode;
  const [connections, setConnections] = useState([]);
  const [jobs, setJobs] = useState([]);
  const [connectionsLoading, setConnectionsLoading] = useState(!demoMode && canReadAccounts);
  const [jobsLoading, setJobsLoading] = useState(!demoMode && canReadJobs);
  const [connectionsError, setConnectionsError] = useState("");
  const [oauthProviders, setOauthProviders] = useState([]);
  const [oauthProvidersLoading, setOauthProvidersLoading] = useState(false);
  const [oauthProvidersError, setOauthProvidersError] = useState("");
  const [jobsError, setJobsError] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [jobsPage, setJobsPage] = useState(1);
  const [jobsTotal, setJobsTotal] = useState(0);
  const [composerArtworks, setComposerArtworks] = useState([]);
  const [composerArtworksPage, setComposerArtworksPage] = useState(1);
  const [composerArtworksTotal, setComposerArtworksTotal] = useState(0);
  const [composerArtworksLoading, setComposerArtworksLoading] = useState(false);
  const [composerArtworksError, setComposerArtworksError] = useState("");
  const [composerOpen, setComposerOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [busyKey, setBusyKey] = useState("");
  const [notice, setNotice] = useState("");
  const [testConnectionOpen, setTestConnectionOpen] = useState(false);
  const [testConnectionName, setTestConnectionName] = useState("");
  const [pendingRemovalId, setPendingRemovalId] = useState("");
  const [detailJob, setDetailJob] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [reconcileTarget, setReconcileTarget] = useState(null);
  const [reconciling, setReconciling] = useState(false);
  const [composerInitialArtifactId, setComposerInitialArtifactId] = useState("");
  const [composerInitialArtwork, setComposerInitialArtwork] = useState(null);
  const [composerSelectionRequest, setComposerSelectionRequest] = useState(null);
  const submissionKeyRef = useRef("");
  const handledOpenComposerRequestRef = useRef("");
  const jobsRequestGenerationRef = useRef(0);
  const jobsPollControllerRef = useRef(null);
  const composerArtworksRequestGenerationRef = useRef(0);
  const detailRequestGenerationRef = useRef(0);
  const detailControllerRef = useRef(null);
  const externalPublishingEnabled = demoMode || (
    publishingEntitlementResolved && autoPublishingEnabled
  );
  const autoPublishingDisabled = !demoMode && (
    publishingEntitlementResolved && !autoPublishingEnabled
  );

  const handleError = useCallback((error, setError) => {
    if (error?.name === "AbortError") return;
    if (error instanceof PlatformApiError && error.status === 401) onSessionError?.(error);
    setError(readablePublishingError(error));
  }, [onSessionError]);

  const refreshConnections = useCallback(async ({ signal } = {}) => {
    if (demoMode || !canReadAccounts) {
      setConnections([]);
      setConnectionsLoading(false);
      return;
    }
    try {
      const response = await client.listPublisherConnections({}, { signal });
      setConnections(collectionItems(response));
      setConnectionsError("");
    } catch (error) {
      handleError(error, setConnectionsError);
    } finally {
      if (!signal?.aborted) setConnectionsLoading(false);
    }
  }, [canReadAccounts, client, demoMode, handleError]);

  const refreshJobs = useCallback(async ({ signal, quiet = false } = {}) => {
    if (demoMode || !canReadJobs) {
      setJobs([]);
      setJobsTotal(0);
      setJobsLoading(false);
      return;
    }
    const requestGeneration = ++jobsRequestGenerationRef.current;
    if (!quiet) setJobsLoading(true);
    try {
      const response = await client.listPublicationJobs(
        { status: statusFilter, page: jobsPage, page_size: 50 },
        { signal },
      );
      if (requestGeneration !== jobsRequestGenerationRef.current) return;
      setJobs(collectionItems(response));
      setJobsTotal(Number(response?.total) || 0);
      setJobsError("");
    } catch (error) {
      if (requestGeneration === jobsRequestGenerationRef.current) {
        handleError(error, setJobsError);
      }
    } finally {
      if (!signal?.aborted && requestGeneration === jobsRequestGenerationRef.current) {
        setJobsLoading(false);
      }
    }
  }, [canReadJobs, client, demoMode, handleError, jobsPage, statusFilter]);

  useEffect(() => {
    if (!composerOpen) return undefined;
    if (demoMode) {
      const visible = artworks.filter((artwork) => publicationArtifactId(artwork));
      setComposerArtworks(visible);
      setComposerArtworksTotal(visible.length);
      setComposerArtworksLoading(false);
      setComposerArtworksError("");
      return undefined;
    }

    const controller = new AbortController();
    const requestGeneration = ++composerArtworksRequestGenerationRef.current;
    setComposerArtworksLoading(true);
    client.listArtworks(
      { page: composerArtworksPage, page_size: PUBLICATION_ARTWORK_PAGE_SIZE, scope: "mine" },
      { signal: controller.signal },
    ).then((response) => {
      if (requestGeneration !== composerArtworksRequestGenerationRef.current) return;
      const pageItems = collectionItems(response).filter((artwork) => publicationArtifactId(artwork));
      const pinned = composerInitialArtifactId
        ? (
          publicationArtifactId(composerInitialArtwork) === composerInitialArtifactId
            ? composerInitialArtwork
            : artworks.find((artwork) => publicationArtifactId(artwork) === composerInitialArtifactId)
        )
        : null;
      setComposerArtworks(
        pinned && !pageItems.some((artwork) => publicationArtifactId(artwork) === composerInitialArtifactId)
          ? [pinned, ...pageItems]
          : pageItems,
      );
      setComposerArtworksTotal(Number(response?.total) || pageItems.length);
      setComposerArtworksError("");
    }).catch((error) => {
      if (requestGeneration === composerArtworksRequestGenerationRef.current) {
        handleError(error, setComposerArtworksError);
      }
    }).finally(() => {
      if (!controller.signal.aborted && requestGeneration === composerArtworksRequestGenerationRef.current) {
        setComposerArtworksLoading(false);
      }
    });

    return () => {
      composerArtworksRequestGenerationRef.current += 1;
      controller.abort();
    };
  }, [artworks, client, composerArtworksPage, composerInitialArtifactId, composerInitialArtwork, composerOpen, demoMode, handleError]);

  useEffect(() => {
    const controller = new AbortController();
    setConnectionsLoading(!demoMode && canReadAccounts);
    refreshConnections({ signal: controller.signal });
    return () => controller.abort();
  }, [canReadAccounts, demoMode, refreshConnections]);

  useEffect(() => {
    if (
      demoMode
      || !canReadAccounts
      || !canManageAccounts
      || !externalPublishingEnabled
    ) {
      setOauthProviders([]);
      setOauthProvidersLoading(false);
      setOauthProvidersError("");
      return undefined;
    }
    const controller = new AbortController();
    setOauthProvidersLoading(true);
    client.listPublisherOAuthProviders({ signal: controller.signal })
      .then((response) => {
        setOauthProviders(collectionItems(response));
        setOauthProvidersError("");
      })
      .catch((error) => handleError(error, setOauthProvidersError))
      .finally(() => {
        if (!controller.signal.aborted) setOauthProvidersLoading(false);
      });
    return () => controller.abort();
  }, [
    canManageAccounts,
    canReadAccounts,
    client,
    demoMode,
    externalPublishingEnabled,
    handleError,
  ]);

  useEffect(() => {
    if (demoMode || typeof window === "undefined") return;
    const url = new URL(window.location.href);
    const result = url.searchParams.get("publishing_oauth");
    if (!result) return;
    setNotice(
      result === "connected"
        ? "发布账号已安全连接。"
        : "发布账号连接未完成，请重新发起授权。",
    );
    url.searchParams.delete("publishing_oauth");
    url.searchParams.delete("provider");
    url.searchParams.delete("reason");
    window.history.replaceState(window.history.state, "", `${url.pathname}${url.search}${url.hash}`);
  }, [demoMode]);

  useEffect(() => {
    const controller = new AbortController();
    refreshJobs({ signal: controller.signal });
    return () => {
      jobsRequestGenerationRef.current += 1;
      controller.abort();
    };
  }, [refreshJobs]);

  useEffect(() => {
    if (demoMode || !canReadJobs) return undefined;
    let stopped = false;
    let timer;
    const poll = async () => {
      jobsPollControllerRef.current?.abort();
      const controller = new AbortController();
      jobsPollControllerRef.current = controller;
      await refreshJobs({ signal: controller.signal, quiet: true });
      if (!stopped) timer = window.setTimeout(poll, 12_000);
    };
    timer = window.setTimeout(poll, 12_000);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      jobsPollControllerRef.current?.abort();
    };
  }, [canReadJobs, demoMode, refreshJobs]);

  useEffect(() => {
    if (externalPublishingEnabled) return;
    setComposerOpen(false);
    setTestConnectionOpen(false);
  }, [externalPublishingEnabled]);

  const connectionsById = useMemo(
    () => new Map(connections.map((connection) => [connection.id, connection])),
    [connections],
  );
  const artworksById = useMemo(
    () => new Map(artworks.map((artwork) => [publicationArtifactId(artwork), artwork])),
    [artworks],
  );

  const createJob = async (payload) => {
    if (!externalPublishingEnabled) {
      throw new Error("公司已停用自动发布，仅保留历史与安全处置");
    }
    setSubmitting(true);
    submissionKeyRef.current ||= createIdempotencyKey();
    try {
      await client.createPublicationJob({
        ...payload,
        idempotencyKey: submissionKeyRef.current,
      });
      submissionKeyRef.current = "";
      setComposerOpen(false);
      setNotice("发布任务已提交，当前处于待审核状态。");
      await refreshJobs();
    } finally {
      setSubmitting(false);
    }
  };

  const mutateJob = async (job, action) => {
    if (["approve", "retry"].includes(action) && !externalPublishingEnabled) {
      setNotice("公司已停用自动发布，仅保留历史与安全处置");
      return;
    }
    const id = job.id || job.job_id;
    setBusyKey(`${action}:${id}`);
    setNotice("");
    try {
      await {
        approve: client.approvePublicationJob,
        cancel: client.cancelPublicationJob,
        retry: client.retryPublicationJob,
      }[action](id);
      setNotice({ approve: "发布任务已批准。", cancel: "发布任务已取消。", retry: "发布任务已重新进入队列。" }[action]);
      await refreshJobs();
    } catch (error) {
      handleError(error, setJobsError);
    } finally {
      setBusyKey("");
    }
  };

  const openJobDetails = async (job) => {
    if (demoMode) return;
    const id = job.id || job.job_id;
    detailControllerRef.current?.abort();
    const controller = new AbortController();
    detailControllerRef.current = controller;
    const requestGeneration = ++detailRequestGenerationRef.current;
    setDetailJob(job);
    setDetailLoading(true);
    setBusyKey(`detail:${id}`);
    try {
      const detail = await client.getPublicationJob(id, { signal: controller.signal });
      if (requestGeneration !== detailRequestGenerationRef.current) return;
      setDetailJob(detail);
    } catch (error) {
      if (error?.name !== "AbortError" && requestGeneration === detailRequestGenerationRef.current) {
        handleError(error, setJobsError);
        setDetailJob(null);
      }
    } finally {
      if (requestGeneration === detailRequestGenerationRef.current) {
        detailControllerRef.current = null;
        setDetailLoading(false);
        setBusyKey("");
      }
    }
  };

  const closeJobDetails = () => {
    detailRequestGenerationRef.current += 1;
    detailControllerRef.current?.abort();
    detailControllerRef.current = null;
    setDetailJob(null);
    setDetailLoading(false);
    setBusyKey("");
  };

  const createTestConnection = async (event) => {
    event.preventDefault();
    if (!DEVELOPMENT_MOCK_PUBLISHING) {
      setNotice("生产环境禁止创建 Mock 发布连接。");
      return;
    }
    if (!externalPublishingEnabled) {
      setNotice("公司已停用自动发布，仅保留历史与安全处置");
      return;
    }
    if (demoMode) {
      setNotice("测试发布不会创建本地假连接。请连接开发环境客户平台后再试。");
      return;
    }
    setBusyKey("connection:create");
    try {
      await client.createPublisherConnection({ provider: "mock", displayName: testConnectionName.trim() });
      setTestConnectionName("");
      setTestConnectionOpen(false);
      setNotice("测试连接已创建。它不会代表真实平台 OAuth 授权。");
      await refreshConnections();
    } catch (error) {
      handleError(error, setConnectionsError);
    } finally {
      setBusyKey("");
    }
  };

  const startOAuthConnection = async (provider) => {
    if (!externalPublishingEnabled || demoMode) return;
    const providerKey = String(provider || "").trim();
    setBusyKey(`connection:oauth:${providerKey}`);
    setOauthProvidersError("");
    try {
      const response = await client.startPublisherOAuth({ provider: providerKey });
      const authorizationUrl = new URL(response?.authorization_url || "");
      if (
        !["http:", "https:"].includes(authorizationUrl.protocol)
        || (import.meta.env.PROD && authorizationUrl.protocol !== "https:")
        || authorizationUrl.username
        || authorizationUrl.password
      ) {
        throw new Error("服务端返回了无效的发布授权地址");
      }
      window.location.assign(authorizationUrl.href);
    } catch (error) {
      handleError(error, setOauthProvidersError);
      setBusyKey("");
    }
  };

  const reconcileJob = async (payload) => {
    if (!reconcileTarget || demoMode) return;
    const id = reconcileTarget.id || reconcileTarget.job_id;
    setReconciling(true);
    try {
      await client.reconcilePublicationJob(id, payload);
      setReconcileTarget(null);
      setNotice(payload.outcome === "published" ? "任务已人工核销为发布成功。" : "任务已人工核销为发布失败。");
      await refreshJobs();
    } catch (error) {
      if (error instanceof PlatformApiError && error.status === 401) onSessionError?.(error);
      throw error;
    } finally {
      setReconciling(false);
    }
  };

  const removeConnection = async (connection) => {
    if (pendingRemovalId !== connection.id) {
      setPendingRemovalId(connection.id);
      return;
    }
    setBusyKey(`connection:delete:${connection.id}`);
    try {
      await client.deletePublisherConnection(connection.id);
      setPendingRemovalId("");
      setNotice("发布连接已移除，历史发布任务仍会保留。");
      await refreshConnections();
    } catch (error) {
      handleError(error, setConnectionsError);
    } finally {
      setBusyKey("");
    }
  };

  const openManualComposer = () => {
    setComposerArtworksPage(1);
    setComposerInitialArtifactId("");
    setComposerInitialArtwork(null);
    setComposerSelectionRequest(null);
    setComposerOpen(true);
  };

  const requestArtworkPreview = useCallback((artwork) => {
    const artworkId = publicationArtifactId(artwork);
    const pinnedId = publicationArtifactId(initialArtwork);
    const scope = artworkId && artworkId === pinnedId && initialArtworkScope === "company"
      ? "company"
      : "mine";
    onRequestArtworkPreview?.(artwork, scope);
  }, [initialArtwork, initialArtworkScope, onRequestArtworkPreview]);

  useEffect(() => {
    if (openComposerRequest === null || openComposerRequest === undefined) return;
    if (!publishingEntitlementResolved && !demoMode) return;

    const requestKey = String(openComposerRequest);
    if (handledOpenComposerRequestRef.current === requestKey) return;
    handledOpenComposerRequestRef.current = requestKey;

    if (!externalPublishingEnabled || !canManageJobs || !canReadJobs || !canReadAccounts) {
      setNotice("无法打开发布编辑器：当前账号缺少发布权限，或公司尚未启用自动发布。");
      return;
    }

    setComposerInitialArtifactId(publicationArtifactId({
      artifact_id: initialArtifactId,
    }));
    setComposerInitialArtwork(
      publicationArtifactId(initialArtwork) === publicationArtifactId({ artifact_id: initialArtifactId })
        ? initialArtwork
        : null,
    );
    setComposerArtworksPage(1);
    setComposerSelectionRequest(requestKey);
    setComposerOpen(true);
    setNotice("");
  }, [
    canManageJobs,
    canReadAccounts,
    canReadJobs,
    demoMode,
    externalPublishingEnabled,
    initialArtifactId,
    initialArtwork,
    openComposerRequest,
    publishingEntitlementResolved,
  ]);

  return (
    <section className="secondary-view publication-center">
      <div className="publication-heading">
        <div>
          <span className="view-kicker">作品分发</span>
          <h1>发布</h1>
          <p>从长期归档作品创建发布计划。任务必须经过人工批准，账号凭据不会进入浏览器。</p>
        </div>
        <div className="publication-heading-actions">
          <span className={`publication-mode ${demoMode ? "is-test" : ""}`}>
            {demoMode ? <Flask size={16} /> : <PlugsConnected size={16} />}
            {demoMode ? "测试发布" : autoPublishingDisabled ? "历史与安全处置" : publishingEntitlementResolved ? "正式发布流程" : "正在核对授权"}
          </span>
          <button className="publication-primary-button" type="button" onClick={openManualComposer} disabled={!externalPublishingEnabled || (!demoMode && (!canManageJobs || !canReadJobs || !canReadAccounts))} title={autoPublishingDisabled ? "公司已停用自动发布" : undefined}>
            <PaperPlaneTilt size={17} weight="fill" /> 新建发布
          </button>
        </div>
      </div>

      {demoMode && (
        <div className="publication-test-banner" role="note">
          <Flask size={20} aria-hidden="true" />
          <span><strong>当前是测试发布</strong><small>不会连接真实社交平台，不会创建真实账号，也不会执行外部发布。</small></span>
        </div>
      )}
      {autoPublishingDisabled && (
        <div className="publication-disabled-banner" role="note">
          <WarningCircle size={20} aria-hidden="true" />
          <span><strong>公司已停用自动发布，仅保留历史与安全处置</strong><small>你仍可查看历史；有管理权限时可取消尚未提交的任务，或核销结果未知任务。新建、批准、重试和新增连接均已停用。</small></span>
        </div>
      )}
      {notice && <p className="publication-notice" role="status"><CheckCircle size={18} weight="fill" /> {notice}</p>}

      <div className="publication-layout">
        <section className="publication-jobs" aria-labelledby="publication-jobs-title">
          <header>
            <div><h2 id="publication-jobs-title">发布任务</h2><span>{demoMode ? "测试流程无真实任务" : `${jobsTotal} 条`}</span></div>
            <label>
              <span>状态筛选</span>
              <select value={statusFilter} onChange={(event) => {
                setStatusFilter(event.target.value);
                setJobsPage(1);
              }} disabled={demoMode || !canReadJobs}>
                {PUBLICATION_STATUS_FILTERS.map(([value, label]) => <option key={value || "all"} value={value}>{label}</option>)}
              </select>
            </label>
          </header>

          {!demoMode && !canReadJobs && <EmptyState icon={WarningCircle} title="没有查看发布任务的权限" detail="请让公司老板开启 publish.jobs.read。" error />}
          {canReadJobs && jobsLoading && <LoadingRows label="正在读取发布任务" />}
          {canReadJobs && !jobsLoading && jobsError && <EmptyState icon={WarningCircle} title="发布任务读取失败" detail={jobsError} error />}
          {(demoMode || (canReadJobs && !jobsLoading && !jobsError && jobs.length === 0)) && (
            <EmptyState
              icon={PaperPlaneTilt}
              title={demoMode ? "测试发布没有伪造任务" : "当前还没有发布任务"}
              detail={demoMode ? "打开“新建发布”可以查看完整编辑流程，但不会提交。" : autoPublishingDisabled ? "公司已停用自动发布，目前只保留历史记录与安全处置。" : "选择一件已归档作品，安排发布时间并提交审核。"}
            />
          )}
          {canReadJobs && !jobsLoading && !jobsError && jobs.length > 0 && (
            <div className="publication-job-list">
              {jobs.map((job) => {
                const id = job.id || job.job_id;
                const artwork = artworksById.get(publicationJobArtifactId(job));
                const connection = connectionsById.get(job.connection_id);
                const isMockJob = connection?.provider === "mock" || job.provider === "mock";
                const available = publicationActionAvailability(job.status);
                const activeBusy = busyKey.endsWith(`:${id}`);
                return (
                  <article className="publication-job" key={id}>
                    <header>
                      <span className="publication-job-icon"><PaperPlaneTilt size={20} weight="duotone" /></span>
                      <span className="publication-job-title">
                        <strong>{job.title || artwork?.request_payload?.prompt || `发布任务 ${shortId(id)}`}</strong>
                        <small>{connection?.display_name || job.provider || `账号 ${shortId(job.connection_id)}`}，计划 {job.scheduled_at ? shortDate(job.scheduled_at, job.timezone) : "审核后尽快"}</small>
                      </span>
                      <span className="publication-job-badges">
                        {isMockJob && <span className="publication-mock-badge"><Flask size={13} /> Mock 测试</span>}
                        <StatusBadge value={job.status} />
                      </span>
                    </header>
                    <p>{job.caption || "发布文案未记录"}</p>
                    {job.status === "submission_unknown" && <small className="publication-inline-warning">必须先去渠道后台核对，避免重复发布。结果未知任务禁止自动重试。</small>}
                    <footer>
                      <button type="button" onClick={() => openJobDetails(job)} disabled={activeBusy}>
                        {busyKey === `detail:${id}` ? <SpinnerGap className="spin" size={16} /> : <ClipboardText size={16} />}
                        {busyKey === `detail:${id}` ? BUSY_LABELS.detail : "查看详情"}
                      </button>
                      {externalPublishingEnabled && canManageJobs && available.approve && <button className="is-positive" type="button" onClick={() => mutateJob(job, "approve")} disabled={activeBusy}>{busyKey === `approve:${id}` ? <SpinnerGap className="spin" size={16} /> : <CheckCircle size={16} />} {busyKey === `approve:${id}` ? BUSY_LABELS.approve : "批准发布"}</button>}
                      {externalPublishingEnabled && canManageJobs && available.retry && <button type="button" onClick={() => mutateJob(job, "retry")} disabled={activeBusy}>{busyKey === `retry:${id}` ? <SpinnerGap className="spin" size={16} /> : <ArrowClockwise size={16} />} {busyKey === `retry:${id}` ? BUSY_LABELS.retry : "重试"}</button>}
                      {canManageJobs && job.status === "submission_unknown" && <button className="is-reconcile" type="button" onClick={() => setReconcileTarget(job)} disabled={activeBusy}><MagnifyingGlass size={16} /> 人工核销</button>}
                      {canManageJobs && available.cancel && <button className="is-danger" type="button" onClick={() => mutateJob(job, "cancel")} disabled={activeBusy}>{busyKey === `cancel:${id}` ? <SpinnerGap className="spin" size={16} /> : <X size={16} />} {busyKey === `cancel:${id}` ? BUSY_LABELS.cancel : "取消"}</button>}
                    </footer>
                  </article>
                );
              })}
            </div>
          )}
          {canReadJobs && !jobsLoading && !jobsError && jobsTotal > 50 && (
            <nav className="publication-pagination" aria-label="发布任务分页">
              <button type="button" disabled={jobsPage <= 1} onClick={() => setJobsPage((value) => value - 1)}>上一页</button>
              <span>第 {jobsPage} / {Math.ceil(jobsTotal / 50)} 页</span>
              <button type="button" disabled={jobsPage * 50 >= jobsTotal} onClick={() => setJobsPage((value) => value + 1)}>下一页</button>
            </nav>
          )}
        </section>

        <aside className="publication-connections" aria-labelledby="publication-connections-title">
          <header>
            <div><h2 id="publication-connections-title">发布账号</h2><span>{demoMode ? "测试" : `${connections.length} 个`}</span></div>
            {DEVELOPMENT_MOCK_PUBLISHING && externalPublishingEnabled && canManageAccounts && (
              <button type="button" onClick={() => setTestConnectionOpen((value) => !value)} disabled={demoMode} title={demoMode ? "测试发布不创建本地假账号" : "只供开发测试后端使用"}>
                <Flask size={16} /> 添加开发 Mock 连接
              </button>
            )}
          </header>
          <p className="publication-account-note"><LinkSimple size={17} /> 正式账号通过服务端 OAuth 授权；访问令牌只保存在适配器的加密密钥存储中，不会进入浏览器。</p>
          {DEVELOPMENT_MOCK_PUBLISHING && (
            <p className="publication-account-note is-development"><Flask size={17} /> 仅限开发环境：Mock 连接不代表真实平台授权。</p>
          )}
          {!demoMode && canReadAccounts && canManageAccounts && externalPublishingEnabled && (
            <div className="publication-oauth-providers" aria-label="可连接的发布平台">
              {oauthProvidersLoading && <span><SpinnerGap className="spin" size={16} /> 正在读取可连接平台</span>}
              {!oauthProvidersLoading && oauthProvidersError && <span className="is-error"><WarningCircle size={16} /> {oauthProvidersError}</span>}
              {!oauthProvidersLoading && !oauthProvidersError && oauthProviders.length === 0 && (
                <span>服务端尚未配置支持 OAuth 的正式发布平台。</span>
              )}
              {!oauthProvidersLoading && !oauthProvidersError && oauthProviders.map((provider) => (
                <button
                  key={provider.provider}
                  type="button"
                  onClick={() => startOAuthConnection(provider.provider)}
                  disabled={Boolean(busyKey)}
                >
                  {busyKey === `connection:oauth:${provider.provider}` ? <SpinnerGap className="spin" size={16} /> : <LinkSimple size={16} />}
                  连接 {provider.display_name}
                </button>
              ))}
            </div>
          )}
          {DEVELOPMENT_MOCK_PUBLISHING && externalPublishingEnabled && testConnectionOpen && !demoMode && (
            <form className="publication-test-connection-form" onSubmit={createTestConnection}>
              <label><span>测试账号名称</span><input value={testConnectionName} onChange={(event) => setTestConnectionName(event.target.value)} maxLength={120} required placeholder="例如：抖音测试号" /></label>
              <button type="submit" disabled={busyKey === "connection:create"}>{busyKey === "connection:create" ? <SpinnerGap className="spin" size={16} /> : <Flask size={16} />} 创建 Mock 连接</button>
            </form>
          )}
          {!demoMode && !canReadAccounts && <EmptyState icon={WarningCircle} title="没有查看发布账号的权限" detail="请让公司老板开启 publish.accounts.read。" error />}
          {canReadAccounts && connectionsLoading && <LoadingRows label="正在读取发布账号" />}
          {canReadAccounts && !connectionsLoading && connectionsError && <EmptyState icon={WarningCircle} title="发布账号读取失败" detail={connectionsError} error />}
          {(demoMode || (canReadAccounts && !connectionsLoading && !connectionsError && connections.length === 0)) && (
            <EmptyState icon={PlugsConnected} title={demoMode ? "测试发布没有真实账号" : "尚未连接发布账号"} detail={demoMode ? "演示模式不会伪造 OAuth 状态。" : "生产账号接入必须走服务端 OAuth 和加密凭据存储。"} />
          )}
          {canReadAccounts && !connectionsLoading && !connectionsError && connections.length > 0 && (
            <div className="publication-connection-list">
              {connections.map((connection) => {
                const pending = pendingRemovalId === connection.id;
                return (
                  <article key={connection.id}>
                    <span><PlugsConnected size={21} weight="duotone" /></span>
                    <div><strong>{connection.display_name || connection.external_account_id || "发布账号"}</strong><small>{connection.provider}，{connection.status || "状态未记录"}</small></div>
                    {canManageAccounts && <button className={pending ? "is-confirm" : ""} type="button" onClick={() => removeConnection(connection)} disabled={busyKey === `connection:delete:${connection.id}`}>{busyKey === `connection:delete:${connection.id}` ? <SpinnerGap className="spin" size={16} /> : <TrashSimple size={16} />} {pending ? "确认移除" : "移除"}</button>}
                  </article>
                );
              })}
            </div>
          )}
        </aside>
      </div>

      <PublicationComposer
        open={composerOpen}
        onClose={() => {
          setComposerOpen(false);
          setComposerInitialArtifactId("");
          setComposerInitialArtwork(null);
          setComposerSelectionRequest(null);
          submissionKeyRef.current = "";
        }}
        onSubmit={createJob}
        submitting={submitting}
        demoMode={demoMode}
        artworks={composerArtworks}
        artworksLoading={composerArtworksLoading}
        artworksError={composerArtworksError}
        artworksPage={composerArtworksPage}
        artworksTotal={composerArtworksTotal}
        onArtworksPageChange={setComposerArtworksPage}
        previewUrls={previewUrls}
        previewActionKey={previewActionKey}
        onRequestPreview={requestArtworkPreview}
        onPreviewError={onPreviewError}
        connections={connections}
        initialArtifactId={composerInitialArtifactId}
        initialSelectionRequest={composerSelectionRequest}
      />
      <JobDetails job={detailJob} loading={detailLoading} onClose={closeJobDetails} connectionsById={connectionsById} />
      <ReconcileDialog job={reconcileTarget} submitting={reconciling} onClose={() => setReconcileTarget(null)} onSubmit={reconcileJob} />
    </section>
  );
}
