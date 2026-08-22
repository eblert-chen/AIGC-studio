import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowClockwise,
  ArrowDown,
  ArrowUp,
  CheckCircle,
  ClockCounterClockwise,
  FilmSlate,
  ImageSquare,
  Monitor,
  PencilSimple,
  Plus,
  Power,
  RocketLaunch,
  Trash,
  UploadSimple,
  VideoCamera,
  WarningCircle,
  X,
} from "@phosphor-icons/react";
import {
  COMMUNITY_CATEGORIES,
  COMMUNITY_SECTIONS,
} from "../../communityFeed.js";
import {
  showcaseDateTime,
  validateShowcaseDraft,
} from "./showcaseModel.js";

const EMPTY_FORM = {
  title: "",
  section: "视频",
  category: "风格艺术",
  altText: "",
  publicPrompt: "",
  aspectRatio: "auto",
  isHero: false,
  mediaId: "",
  sortOrder: 0,
  file: null,
  mediaSource: "upload",
  sourceTaskArtifactId: "",
};

function MediaPreview({ item, controls = true }) {
  if (!item?.mediaUrl) {
    return (
      <span className="showcase-media-placeholder" aria-label="媒体等待上传">
        <ImageSquare size={28} aria-hidden="true" />
      </span>
    );
  }
  if (item.mediaType === "video") {
    return (
      <video
        src={item.mediaUrl}
        poster={item.posterUrl || undefined}
        aria-label={item.altText || item.title}
        muted
        playsInline
        controls={controls}
        preload="metadata"
      />
    );
  }
  return <img src={item.mediaUrl} alt={item.altText || item.title} loading="lazy" />;
}

function DialogFrame({ title, description, onClose, children, className = "" }) {
  const dialogRef = useRef(null);
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return undefined;
    const previousFocus = globalThis.document?.activeElement;
    const focusable = () => Array.from(dialog.querySelectorAll(
      'button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [href], [tabindex]:not([tabindex="-1"])',
    ));
    globalThis.requestAnimationFrame?.(() => (focusable()[0] || dialog).focus());
    const onKeyDown = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab") return;
      const controls = focusable();
      if (!controls.length) {
        event.preventDefault();
        dialog.focus();
        return;
      }
      const first = controls[0];
      const last = controls[controls.length - 1];
      if (event.shiftKey && globalThis.document?.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && globalThis.document?.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    globalThis.document?.addEventListener?.("keydown", onKeyDown);
    return () => {
      globalThis.document?.removeEventListener?.("keydown", onKeyDown);
      previousFocus?.focus?.();
    };
  }, [onClose]);
  return (
    <div className="showcase-dialog-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose();
    }}>
      <section
        ref={dialogRef}
        className={`showcase-dialog ${className}`}
        role="dialog"
        tabIndex={-1}
        aria-modal="true"
        aria-labelledby="showcase-dialog-title"
        aria-describedby={description ? "showcase-dialog-description" : undefined}
      >
        <header>
          <div>
            <h2 id="showcase-dialog-title">{title}</h2>
            {description ? <p id="showcase-dialog-description">{description}</p> : null}
          </div>
          <button type="button" data-icon-only="true" onClick={onClose} aria-label="关闭窗口">
            <X size={17} aria-hidden="true" />
          </button>
        </header>
        {children}
      </section>
    </div>
  );
}

function ShowcaseItemDialog({
  item,
  busy,
  demoMode,
  uploadedMedia,
  ownedArtworks,
  ownedArtworksLoading,
  ownedArtworksError,
  onReloadOwnedArtworks,
  onClose,
  onSubmit,
}) {
  const [values, setValues] = useState(() => item ? {
    ...EMPTY_FORM,
    ...item,
    altText: item.altText || "",
    publicPrompt: item.publicPrompt || "",
    file: null,
    mediaSource: "existing",
  } : EMPTY_FORM);
  const [error, setError] = useState("");
  const [localPreview, setLocalPreview] = useState("");

  useEffect(() => {
    if (!values.file) {
      setLocalPreview("");
      return undefined;
    }
    const url = URL.createObjectURL(values.file);
    setLocalPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [values.file]);

  const update = (key, value) => setValues((current) => ({ ...current, [key]: value }));
  const selectMediaSource = (mediaSource) => setValues((current) => ({
    ...current,
    mediaSource,
    file: mediaSource === "upload" ? current.file : null,
    sourceTaskArtifactId: mediaSource === "artifact"
      ? current.sourceTaskArtifactId
      : "",
    mediaId: mediaSource === "existing" ? current.mediaId : "",
  }));
  const selectedUploadedMedia = (uploadedMedia || []).find((media) => (
    media.id === values.mediaId
  ));
  const previewUrl = localPreview
    || selectedUploadedMedia?.mediaUrl
    || item?.mediaUrl
    || "";
  const mediaType = values.file?.type?.startsWith("video/")
    ? "video"
    : selectedUploadedMedia?.mediaType || item?.mediaType || "image";

  const submit = async (event) => {
    event.preventDefault();
    const validationError = validateShowcaseDraft(values, { editing: Boolean(item) });
    if (validationError) {
      setError(validationError);
      return;
    }
    setError("");
    try {
      const saved = await onSubmit(values);
      if (saved !== false) onClose();
    } catch (saveError) {
      setError(saveError?.message || "案例保存失败，请稍后重试。");
    }
  };

  return (
    <DialogFrame
      title={item ? "编辑精选案例" : "添加精选案例"}
      description={demoMode
        ? "演示操作只更新当前浏览器内存，不会写入 Platform；本地仅支持图片，视频从本人作品导入。"
        : "本地仅接收 JPEG、PNG 或 WebP 图片；视频须从本人已验证作品导入。媒体由 Platform 校验并存入专用 OBS 区域。"}
      onClose={onClose}
      className="showcase-editor-dialog"
    >
      <form onSubmit={submit}>
        <fieldset disabled={busy}>
          <div className="showcase-editor-media">
            {previewUrl ? (
              mediaType === "video" ? (
                <video
                  src={previewUrl}
                  poster={item?.posterUrl || undefined}
                  aria-label="待保存视频预览"
                  muted
                  playsInline
                  controls
                />
              ) : (
                <img src={previewUrl} alt={values.altText || "待保存图片预览"} />
              )
            ) : (
              <span><UploadSimple size={26} aria-hidden="true" />等待选择媒体</span>
            )}
            <div className="showcase-media-source" role="radiogroup" aria-label="媒体来源">
              <label>
                <input
                  type="radio"
                  name="showcase-media-source"
                  checked={values.mediaSource === "upload"}
                  onChange={() => selectMediaSource("upload")}
                />
                <span>本地图片</span>
              </label>
              <label>
                <input
                  type="radio"
                  name="showcase-media-source"
                  checked={values.mediaSource === "artifact"}
                  onChange={() => selectMediaSource("artifact")}
                />
                <span>本人作品</span>
              </label>
              <label>
                <input
                  type="radio"
                  name="showcase-media-source"
                  checked={values.mediaSource === "existing"}
                  onChange={() => selectMediaSource("existing")}
                />
                <span>已上传</span>
              </label>
            </div>
            {values.mediaSource === "upload" ? (
              <label className="showcase-file-control">
                <span>{item ? "替换为本地图片" : "选择 JPEG / PNG / WebP 图片"}</span>
                <input
                  type="file"
                  accept="image/jpeg,image/png,image/webp"
                  onChange={(event) => update("file", event.target.files?.[0] || null)}
                />
              </label>
            ) : values.mediaSource === "artifact" ? (
              <div className="showcase-artifact-control">
                <label>
                  <span>本人作品 Artifact ID</span>
                  <input
                    value={values.sourceTaskArtifactId}
                    list="showcase-owned-artifacts"
                    autoComplete="off"
                    placeholder="选择下方作品，或粘贴 Artifact ID"
                    onChange={(event) => update("sourceTaskArtifactId", event.target.value)}
                  />
                </label>
                <datalist id="showcase-owned-artifacts">
                  {(ownedArtworks || []).map((artwork) => (
                    <option
                      key={artwork.artifact_id}
                      value={artwork.artifact_id}
                      label={`${artwork.model_display_name || "模型未记录"} / ${artwork.media_type === "video" ? "视频" : "图片"}`}
                    />
                  ))}
                </datalist>
                <div className="showcase-artifact-hint">
                  <span>
                    {ownedArtworksLoading
                      ? "正在读取本人作品…"
                      : ownedArtworksError
                        ? ownedArtworksError
                        : (ownedArtworks || []).length
                          ? `可选择最近 ${(ownedArtworks || []).length} 个已验证作品。`
                          : "当前没有可列出的作品，也可以粘贴本人已验证作品的 Artifact ID。"}
                  </span>
                  <button type="button" onClick={onReloadOwnedArtworks} disabled={ownedArtworksLoading}>刷新作品</button>
                </div>
                <small>图片和视频均可导入。Platform 只接受当前 Owner 本人的个人空间成功产物，不接受任意网址，也不会跨账号读取。</small>
              </div>
            ) : (
              <div className="showcase-artifact-control showcase-existing-media-control">
                <label>
                  <span>已由 Platform 校验的媒体</span>
                  <select
                    value={values.mediaId}
                    onChange={(event) => update("mediaId", event.target.value)}
                  >
                    <option value="">选择已上传媒体</option>
                    {(uploadedMedia || []).map((media) => (
                      <option key={media.id} value={media.id}>
                        {media.filename || media.id} · {media.mediaType === "video" ? "视频" : "图片"}
                      </option>
                    ))}
                  </select>
                </label>
                <small>用于恢复上传成功但草稿保存冲突的媒体，也可以复用此前已校验素材；不会再次上传。</small>
              </div>
            )}
          </div>

          <div className="showcase-form-grid">
            <label className="is-wide">
              <span>案例标题</span>
              <input value={values.title} maxLength={80} onChange={(event) => update("title", event.target.value)} />
            </label>
            <label>
              <span>首页分区</span>
              <select value={values.section} onChange={(event) => update("section", event.target.value)}>
                {COMMUNITY_SECTIONS.map((section) => <option key={section}>{section}</option>)}
              </select>
            </label>
            <label>
              <span>内容分类</span>
              <select value={values.category} onChange={(event) => update("category", event.target.value)}>
                {COMMUNITY_CATEGORIES.filter((category) => category !== "全部")
                  .map((category) => <option key={category}>{category}</option>)}
              </select>
            </label>
            <label>
              <span>卡片比例</span>
              <select value={values.aspectRatio} onChange={(event) => update("aspectRatio", event.target.value)}>
                <option value="auto">自动识别</option>
                <option value="16:9">横向 16:9</option>
                <option value="4:3">横向 4:3</option>
                <option value="1:1">方形 1:1</option>
                <option value="3:4">竖向 3:4</option>
                <option value="9:16">竖向 9:16</option>
              </select>
            </label>
            <label className="showcase-checkbox-field">
              <input type="checkbox" checked={values.isHero} onChange={(event) => setValues((current) => ({
                ...current,
                isHero: event.target.checked,
                section: event.target.checked ? "视频" : current.section,
              }))} />
              <span>设为首页头图</span>
            </label>
            <label className="is-wide">
              <span>替代说明</span>
              <input value={values.altText} maxLength={200} onChange={(event) => update("altText", event.target.value)} />
              <small>说明画面本身，不要写“图片”或堆叠关键词。</small>
            </label>
            <label className="is-wide">
              <span>公开制作说明</span>
              <textarea value={values.publicPrompt} maxLength={2000} rows={4} onChange={(event) => update("publicPrompt", event.target.value)} />
              <small>留空时首页不会开放“做同款”，请勿包含客户、人物或供应商隐私信息。</small>
            </label>
          </div>
        </fieldset>
        {error ? <p className="showcase-form-error" role="alert">{error}</p> : null}
        <footer>
          <button type="button" className="ops-secondary-button" onClick={onClose} disabled={busy}>取消</button>
          <button type="submit" className="ops-primary-button" disabled={busy}>
            {busy ? "正在保存" : "保存到草稿"}
          </button>
        </footer>
      </form>
    </DialogFrame>
  );
}

function ConfirmDialog({ kind, release, busy, demoMode, onClose, onConfirm }) {
  const rollback = kind === "rollback";
  const unpublish = kind === "unpublish";
  const [note, setNote] = useState(
    rollback
      ? `回滚到版本 ${release?.version || ""}`
      : unpublish
        ? "紧急下线当前首页"
        : "",
  );
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState("");
  const submit = async (event) => {
    event.preventDefault();
    if (note.trim().length < 3) {
      setError(`${unpublish ? "下线" : "发布"}说明至少填写 3 个字符。`);
      return;
    }
    if (!confirmed) {
      setError(unpublish
        ? "请确认下线影响与约 30 秒的已打开页面更新窗口。"
        : "请确认公开内容检查已经完成。");
      return;
    }
    setError("");
    try {
      const result = await onConfirm(note.trim());
      if (result !== false) onClose();
    } catch (confirmError) {
      setError(confirmError?.message || "发布操作失败，请稍后重试。");
    }
  };
  return (
    <DialogFrame
      title={`${unpublish ? "下线当前首页" : rollback ? `回滚到版本 ${release?.version || ""}` : "发布首页案例"}${demoMode ? "（演示）" : ""}`}
      description={demoMode
        ? "此操作只改变浏览器内存中的演示版本，不会影响 Platform 或公开首页。"
        : unpublish
          ? "公开 feed 与已打开页面会在约 30 秒内回到内置示例；为支持视频 Range 播放，已签发的私有 OBS 媒体地址最长可能继续有效 5 分钟。草稿与历史保留，已下载内容无法收回。"
        : rollback
          ? "回滚会根据历史快照生成一个新版本，不会改写或删除旧记录。"
          : "本次会把整份草稿原子发布到首页，已打开页面将在短缓存周期内更新。"}
      onClose={onClose}
    >
      <form onSubmit={submit}>
        <label className="showcase-confirm-note">
          <span>{unpublish ? "下线说明" : "发布说明"}</span>
          <textarea rows={3} value={note} maxLength={240} onChange={(event) => setNote(event.target.value)} />
        </label>
        <label className="showcase-confirm-check">
          <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />
          <span>{unpublish
            ? "我确认下线当前精选案例；页面约 30 秒内回退，已签发媒体地址最长可能继续有效 5 分钟，已下载内容无法收回。"
            : "我已检查版权、隐私、替代说明和公开制作说明。"}</span>
        </label>
        {error ? <p className="showcase-form-error" role="alert">{error}</p> : null}
        <footer>
          <button type="button" className="ops-secondary-button" onClick={onClose} disabled={busy}>取消</button>
          <button type="submit" className={unpublish ? "showcase-danger-button" : "ops-primary-button"} disabled={busy}>
            {busy ? "正在提交" : unpublish ? "确认下线首页" : rollback ? "创建回滚版本" : "确认发布"}
          </button>
        </footer>
      </form>
    </DialogFrame>
  );
}

function RetireDialog({ item, busy, demoMode, onClose, onConfirm }) {
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState("");
  return (
    <DialogFrame
      title="从草稿撤下案例"
      description={demoMode
        ? `“${item.title}”只会从浏览器内存中的演示草稿移除。`
        : `“${item.title}”会从下一次发布中移除，当前线上版本不会立即改变。`}
      onClose={onClose}
    >
      <div className="showcase-retire-confirmation">
        <label>
          <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />
          <span>我确认要从草稿中撤下这个案例。</span>
        </label>
      </div>
      {error ? <p className="showcase-form-error" role="alert">{error}</p> : null}
      <footer>
        <button type="button" className="ops-secondary-button" onClick={onClose} disabled={busy}>取消</button>
        <button type="button" className="showcase-danger-button" disabled={busy || !confirmed} onClick={async () => {
          try {
            const result = await onConfirm();
            if (result !== false) onClose();
          } catch (retireError) {
            setError(retireError?.message || "案例撤下失败，请稍后重试。");
          }
        }}>
          {busy ? "正在撤下" : "撤下案例"}
        </button>
      </footer>
    </DialogFrame>
  );
}

function ShowcasePreview({ item, viewport, onViewport }) {
  return (
    <section className="showcase-preview-panel" aria-labelledby="showcase-preview-title">
      <header>
        <div>
          <h3 id="showcase-preview-title">首页预览</h3>
          <span>{item ? "按草稿内容预览，不代表已经上线" : "选择一条草稿查看效果"}</span>
        </div>
        <div className="showcase-viewport-switch" aria-label="预览宽度">
          <button type="button" className={viewport === "desktop" ? "is-active" : ""} onClick={() => onViewport("desktop")} aria-pressed={viewport === "desktop"}><Monitor size={15} />桌面</button>
          <button type="button" className={viewport === "390" ? "is-active" : ""} onClick={() => onViewport("390")} aria-pressed={viewport === "390"}>390</button>
          <button type="button" className={viewport === "320" ? "is-active" : ""} onClick={() => onViewport("320")} aria-pressed={viewport === "320"}>320</button>
        </div>
      </header>
      <div className={`showcase-preview-stage is-${viewport}`}>
        {item ? (
          <article className={item.isHero ? "showcase-preview-hero" : `showcase-preview-card is-${item.aspect}`}>
            <div className="showcase-preview-media"><MediaPreview item={item} /></div>
            <div className="showcase-preview-copy">
              <span>{item.category}</span>
              <strong>{item.title}</strong>
              {!item.isHero ? <button type="button" disabled={!item.publicPrompt}>做同款</button> : null}
            </div>
          </article>
        ) : (
          <div className="showcase-preview-empty"><FilmSlate size={30} /><span>草稿中还没有案例</span></div>
        )}
      </div>
    </section>
  );
}

export function ShowcaseOperationsScreen({
  snapshot,
  loading,
  error,
  notice,
  busyAction,
  demoMode,
  ownedArtworks,
  ownedArtworksLoading,
  ownedArtworksError,
  onReload,
  onReloadOwnedArtworks,
  onSave,
  onMove,
  onRetire,
  onPublish,
  onUnpublish,
  onRollback,
}) {
  const [editorItem, setEditorItem] = useState(null);
  const [editorOpen, setEditorOpen] = useState(false);
  const [retiringItem, setRetiringItem] = useState(null);
  const [publishOpen, setPublishOpen] = useState(false);
  const [unpublishOpen, setUnpublishOpen] = useState(false);
  const [rollbackRelease, setRollbackRelease] = useState(null);
  const [selectedId, setSelectedId] = useState("");
  const [viewport, setViewport] = useState("desktop");
  const items = snapshot?.draft?.items || [];
  const selectedItem = useMemo(
    () => items.find((item) => item.id === selectedId) || items[0] || null,
    [items, selectedId],
  );

  useEffect(() => {
    if (selectedId && !items.some((item) => item.id === selectedId)) setSelectedId("");
  }, [items, selectedId]);

  if (loading && !snapshot) {
    return (
      <div className="showcase-loading" role="status" aria-label="正在读取首页案例草稿">
        <span /><span /><span />
      </div>
    );
  }
  if (error && !snapshot) {
    return (
      <div className="showcase-failed" role="alert">
        <WarningCircle size={28} aria-hidden="true" />
        <strong>首页内容读取失败</strong>
        <span>{error}</span>
        <button type="button" className="ops-secondary-button" onClick={onReload}><ArrowClockwise size={15} />重新读取</button>
      </div>
    );
  }

  const liveRelease = snapshot?.liveRelease;
  const releases = snapshot?.releases || [];
  const lastUnpublishedEvent = snapshot?.lastUnpublishedEvent || null;
  const publicationEvents = snapshot?.publicationEvents?.length
    ? snapshot.publicationEvents
    : lastUnpublishedEvent
      ? [lastUnpublishedEvent]
      : [];
  const historyEntries = [
    ...releases.map((release) => ({
      kind: "release",
      at: release.publishedAt,
      release,
    })),
    ...publicationEvents.map((event) => ({
      kind: "unpublish",
      at: event.unpublishedAt,
      event,
    })),
  ].sort((left, right) => (
    new Date(right.at || 0).getTime() - new Date(left.at || 0).getTime()
  ));
  const heroCount = items.filter((item) => item.isHero).length;
  const publishBlocked = !items.length || heroCount !== 1 || !snapshot?.draft?.changed;
  return (
    <div className="showcase-operations">
      {demoMode ? (
        <p className="showcase-demo-notice" role="status">
          <WarningCircle size={16} aria-hidden="true" />
          演示数据：编辑、发布、下线、撤下和回滚只保存在当前浏览器内存，不会调用或写入 Platform。
        </p>
      ) : null}
      <section className="showcase-release-bar" aria-label="首页发布状态">
        <div>
          <span>线上版本</span>
          <strong>{liveRelease ? `版本 ${liveRelease.version}` : "尚未发布"}</strong>
          <small>{liveRelease
            ? `${showcaseDateTime(liveRelease.publishedAt)}${liveRelease.itemCount === null ? "" : `，${liveRelease.itemCount} 个案例`}`
            : releases.length
              ? lastUnpublishedEvent
                ? `${showcaseDateTime(lastUnpublishedEvent.unpublishedAt)} 由 ${lastUnpublishedEvent.actor || "平台所有者"} 下线：${lastUnpublishedEvent.note || "未填写说明"}`
                : "当前已下线，首页使用内置示例"
              : "首次发布前首页继续使用内置示例"}</small>
        </div>
        <div>
          <span>当前草稿</span>
          <strong>修订 {snapshot?.draft?.version ?? 0}</strong>
          <small>{snapshot?.draft?.changed ? "存在未发布变更" : "与线上版本一致"}</small>
        </div>
        <div className="showcase-release-actions">
          <button type="button" className="ops-secondary-button" disabled={Boolean(busyAction)} onClick={onReload}><ArrowClockwise size={15} />刷新</button>
          <button type="button" className="ops-secondary-button" disabled={Boolean(busyAction)} onClick={() => { setEditorItem(null); setEditorOpen(true); }}><Plus size={15} />添加案例</button>
          <button type="button" className="showcase-danger-button" disabled={Boolean(busyAction) || !liveRelease} onClick={() => setUnpublishOpen(true)}><Power size={15} />下线首页</button>
          <button type="button" className="ops-primary-button" disabled={Boolean(busyAction) || publishBlocked} title={heroCount !== 1 ? "发布版本必须恰好包含一个首页头图" : undefined} onClick={() => setPublishOpen(true)}><RocketLaunch size={15} />发布变更</button>
        </div>
      </section>

      {items.length && heroCount !== 1 ? <p className="showcase-inline-warning" role="status">发布前必须恰好设置一个首页头图，当前为 {heroCount} 个。</p> : null}

      {error ? <p className="showcase-inline-error" role="alert">{error}</p> : null}
      {notice ? <p className="showcase-inline-notice" role="status"><CheckCircle size={15} />{notice}</p> : null}

      <div className="showcase-workspace">
        <section className="showcase-draft-list" aria-labelledby="showcase-draft-title">
          <header>
            <div><h3 id="showcase-draft-title">草稿案例</h3><span>{items.length} 条，发布时整版生效</span></div>
          </header>
          {items.length ? (
            <div className="showcase-draft-rows">
              {items.map((item, index) => {
                const MediaIcon = item.mediaType === "video" ? VideoCamera : ImageSquare;
                return (
                  <article key={item.id} className={selectedItem?.id === item.id ? "is-selected" : ""}>
                    <button className="showcase-row-main" type="button" onClick={() => setSelectedId(item.id)} aria-pressed={selectedItem?.id === item.id}>
                      <span className="showcase-row-thumbnail"><MediaPreview item={item} controls={false} /></span>
                      <span className="showcase-row-copy">
                        <strong>{item.title}</strong>
                        <small><MediaIcon size={13} />{item.section} / {item.category}{item.isHero ? " / 首页头图" : ""}</small>
                      </span>
                    </button>
                    <div className="showcase-row-actions" aria-label={`${item.title}草稿操作`}>
                      <button type="button" data-icon-only="true" disabled={index === 0 || Boolean(busyAction)} onClick={() => onMove(item, -1).catch(() => {})} aria-label="上移"><ArrowUp size={15} /></button>
                      <button type="button" data-icon-only="true" disabled={index === items.length - 1 || Boolean(busyAction)} onClick={() => onMove(item, 1).catch(() => {})} aria-label="下移"><ArrowDown size={15} /></button>
                      <button type="button" data-icon-only="true" disabled={Boolean(busyAction)} onClick={() => { setEditorItem(item); setEditorOpen(true); }} aria-label="编辑"><PencilSimple size={15} /></button>
                      <button type="button" data-icon-only="true" disabled={Boolean(busyAction)} onClick={() => setRetiringItem(item)} aria-label="撤下"><Trash size={15} /></button>
                    </div>
                  </article>
                );
              })}
            </div>
          ) : (
            <div className="showcase-list-empty">
              <FilmSlate size={30} aria-hidden="true" />
              <strong>草稿中还没有案例</strong>
              <span>添加经过校验的图片或视频，再预览并发布。</span>
              <button type="button" className="ops-secondary-button" onClick={() => setEditorOpen(true)}><Plus size={15} />添加第一个案例</button>
            </div>
          )}
        </section>
        <ShowcasePreview item={selectedItem} viewport={viewport} onViewport={setViewport} />
      </div>

      <section className="showcase-release-history" aria-labelledby="showcase-history-title">
        <header>
          <div><h3 id="showcase-history-title">发布与下线记录</h3><span>旧版本和下线事件保持不可变；旧发布版本可据此创建新的回滚版本。</span></div>
        </header>
        {historyEntries.length ? (
          <div className="showcase-history-table" role="table" aria-label="首页案例发布与下线记录">
            <div role="row" className="showcase-history-head"><span role="columnheader">版本 / 事件</span><span role="columnheader">操作时间</span><span role="columnheader">操作人</span><span role="columnheader">案例 / 原版本</span><span role="columnheader">说明</span><span role="columnheader">操作</span></div>
            {historyEntries.map((entry) => entry.kind === "unpublish" ? (
              <div role="row" key={`unpublish-${entry.event.id || entry.event.publicationVersion}`}>
                <strong role="cell" data-label="版本 / 事件">下线</strong>
                <span role="cell" data-label="操作时间">{showcaseDateTime(entry.event.unpublishedAt)}</span>
                <span role="cell" data-label="操作人">{entry.event.actor || "平台所有者"}</span>
                <span role="cell" data-label="案例 / 原版本">{entry.event.previousReleaseVersion ? `原版本 ${entry.event.previousReleaseVersion}` : entry.event.previousReleaseId || "未记录"}</span>
                <span role="cell" data-label="说明">{entry.event.note || "未填写"}</span>
                <span role="cell" data-label="操作"><em>不可变记录</em></span>
              </div>
            ) : (
              <div role="row" key={entry.release.id}>
                <strong role="cell" data-label="版本 / 事件">{entry.release.version}</strong>
                <span role="cell" data-label="操作时间">{showcaseDateTime(entry.release.publishedAt)}</span>
                <span role="cell" data-label="操作人">{entry.release.publishedBy || "平台所有者"}</span>
                <span role="cell" data-label="案例 / 原版本">{entry.release.itemCount === null ? "未记录" : `${entry.release.itemCount} 条`}</span>
                <span role="cell" data-label="说明">{entry.release.note || "未填写"}</span>
                <span role="cell" data-label="操作">
                  {liveRelease?.id === entry.release.id ? <em>当前线上</em> : (
                    <button type="button" disabled={Boolean(busyAction)} onClick={() => setRollbackRelease(entry.release)}><ClockCounterClockwise size={14} />回滚</button>
                  )}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <div className="showcase-history-empty">完成首次发布或下线后，这里会保留版本、操作人和说明。</div>
        )}
      </section>

      {editorOpen ? <ShowcaseItemDialog key={editorItem?.id || "new"} item={editorItem} busy={busyAction === "save"} demoMode={demoMode} uploadedMedia={snapshot?.media || []} ownedArtworks={ownedArtworks} ownedArtworksLoading={ownedArtworksLoading} ownedArtworksError={ownedArtworksError} onReloadOwnedArtworks={onReloadOwnedArtworks} onClose={() => { setEditorOpen(false); setEditorItem(null); }} onSubmit={(values) => onSave(values, editorItem)} /> : null}
      {retiringItem ? <RetireDialog item={retiringItem} busy={busyAction === "retire"} demoMode={demoMode} onClose={() => setRetiringItem(null)} onConfirm={() => onRetire(retiringItem)} /> : null}
      {publishOpen ? <ConfirmDialog kind="publish" busy={busyAction === "publish"} demoMode={demoMode} onClose={() => setPublishOpen(false)} onConfirm={onPublish} /> : null}
      {unpublishOpen ? <ConfirmDialog kind="unpublish" busy={busyAction === "unpublish"} demoMode={demoMode} onClose={() => setUnpublishOpen(false)} onConfirm={onUnpublish} /> : null}
      {rollbackRelease ? <ConfirmDialog kind="rollback" release={rollbackRelease} busy={busyAction === "rollback"} demoMode={demoMode} onClose={() => setRollbackRelease(null)} onConfirm={(note) => onRollback(rollbackRelease, note)} /> : null}
    </div>
  );
}
