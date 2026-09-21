import {
  DownloadSimple,
  ImageSquare,
  Images,
  Plus,
  SpinnerGap,
  VideoCamera,
  WarningCircle,
} from "@phosphor-icons/react";
import { activePreviewUrl } from "../../previewLeases.js";
import {
  LoadingRows,
  PageControls,
  ScopeControl,
} from "../../components/studio/StudioCollectionControls.jsx";
import { DownloadStatus } from "../../components/studio/StudioWorkspaceViews.jsx";
import {
  artifactKindLabel,
  downloadStatusPresentation,
  formatBytes,
  shortDate,
  studioErrorMessage,
  studioTaskParametersLabel,
} from "../../components/studio/studioPresentation.js";

export function ArtworksView({
  liveMode = false,
  artworks = [],
  demoArtworks = [],
  loading = false,
  error = "",
  canViewCompany = false,
  scope = "mine",
  onScopeChange,
  mediaFilter = "",
  onMediaFilterChange,
  downloadFilter = "",
  onDownloadFilterChange,
  page = 1,
  pageSize = 24,
  total = 0,
  onPageChange,
  onPreview,
  onPreviewError,
  onDownload,
  onOpenTask,
  previewUrls = {},
  issuedArtifacts = {},
  actionKey = "",
  canCreateTasks = true,
  canPublish = false,
  canPromoteArtifacts = false,
  artifactAccessAvailable = true,
  supportsDownloadFilter = true,
  workspaceKind = "company",
  onAdjust,
  onPublish,
  onPromote,
}) {
  const displayedArtworks = liveMode ? artworks : demoArtworks;
  const hasActiveFilters = Boolean(mediaFilter || downloadFilter);
  return (
    <section className="secondary-view artworks-view" aria-labelledby="artworks-title" aria-busy={loading}>
      <div className="secondary-heading">
        <div>
          <h1 id="artworks-title">作品</h1>
          <p>
            {!liveMode
              ? "以下为示例作品，用于体验预览与后续操作。"
              : workspaceKind === "personal"
                ? "查看当前个人空间已保存的图片与视频。"
                : "查看已完成并保存的图片与视频，继续创作、下载或交接发布。"}
          </p>
        </div>
      </div>
      <div className="history-toolbar artwork-toolbar" aria-label="作品筛选">
        <ScopeControl value={scope} onChange={onScopeChange} canViewCompany={canViewCompany} />
        <label>
          <span>类型</span>
          <select value={mediaFilter} onChange={(event) => onMediaFilterChange(event.target.value)}>
            <option value="">全部</option><option value="video">视频</option><option value="image">图片</option>
          </select>
        </label>
        {supportsDownloadFilter && (
          <label>
            <span>下载状态</span>
            <select value={downloadFilter} onChange={(event) => onDownloadFilterChange(event.target.value)}>
              <option value="">全部</option><option value="false">未下载</option><option value="true">已确认下载</option>
            </select>
          </label>
        )}
        <span className="artwork-toolbar-count">
          {Math.max(displayedArtworks.length, liveMode ? Number(total || 0) : 0)} 件作品
        </span>
      </div>
      {loading && <LoadingRows label="正在读取作品" />}
      {!loading && error && (
        <div className="artifact-empty" role="alert">
          <WarningCircle size={26} aria-hidden="true" />
          <strong>作品读取失败</strong>
          <span>{studioErrorMessage(error, "作品暂时无法读取，请稍后重试。")}</span>
        </div>
      )}
      {!loading && !error && displayedArtworks.length === 0 && (
        <div className="artifact-empty">
          <Images size={28} aria-hidden="true" />
          <strong>{hasActiveFilters ? "没有符合当前筛选的作品" : "还没有作品"}</strong>
          <span>{hasActiveFilters ? "清除筛选即可查看全部作品。" : "完成一次创作后，保存好的作品会出现在这里。"}</span>
          {hasActiveFilters ? (
            <button
              className="text-button"
              type="button"
              onClick={() => {
                onMediaFilterChange("");
                onDownloadFilterChange?.("");
              }}
            >
              清除筛选
            </button>
          ) : null}
        </div>
      )}
      {!loading && !error && displayedArtworks.length > 0 && (
        <div className="artwork-grid">
          {displayedArtworks.map((artwork, index) => {
            const key = `${artwork.task_id}:${artwork.asset_id}`;
            const leasedPreviewUrl = activePreviewUrl(previewUrls[key]);
            const previewUrl = leasedPreviewUrl || (!liveMode ? artwork.preview_url : "");
            const state = downloadStatusPresentation(artwork, { issuedLocally: Boolean(issuedArtifacts[key]) });
            const MediaIcon = artwork.media_type === "image" ? ImageSquare : VideoCamera;
            return (
              <article
                className={`artwork-item ${index === 0 && page === 1 && !hasActiveFilters ? "is-featured" : ""}`}
                key={artwork.artifact_id || key}
              >
                <div className="artwork-media">
                  {previewUrl ? (
                    artwork.media_type === "image" ? (
                      <img
                        src={previewUrl}
                        alt={liveMode ? "图片作品预览" : "示例图片作品"}
                        loading="lazy"
                        decoding="async"
                        onError={leasedPreviewUrl ? () => onPreviewError?.(key) : undefined}
                      />
                    ) : liveMode ? (
                      <video
                        src={previewUrl}
                        controls
                        preload="metadata"
                        onError={leasedPreviewUrl ? () => onPreviewError?.(key) : undefined}
                      >当前浏览器无法播放该视频。</video>
                    ) : (
                      <img src={previewUrl} alt="示例视频作品画面" loading="lazy" decoding="async" />
                    )
                  ) : artifactAccessAvailable ? (
                    <button type="button" onClick={() => onPreview(artwork)} disabled={Boolean(actionKey)}>
                      {actionKey === `preview:${key}` ? <SpinnerGap className="spin" size={28} /> : <MediaIcon size={36} weight="duotone" />}
                      <strong>{actionKey === `preview:${key}` ? "正在加载" : "预览作品"}</strong>
                      <span>预览链接会在短时间后失效</span>
                    </button>
                  ) : (
                    <div className="artwork-access-unavailable" role="note">
                      <MediaIcon size={36} weight="duotone" aria-hidden="true" />
                      <strong>作品已保存</strong>
                      <span>{workspaceKind === "personal" ? "当前个人空间暂不支持预览和下载" : "当前账号不能预览或下载作品"}</span>
                    </div>
                  )}
                </div>
                <div className="artwork-copy">
                  <header>
                    <span><strong>{artwork.model_display_name || "模型未记录"}</strong><small>{artifactKindLabel(artwork.media_type)} {Number(artwork.output_index || 0) + 1}</small></span>
                    {artifactAccessAvailable
                      ? <DownloadStatus source={artwork} issuedLocally={Boolean(issuedArtifacts[key])} />
                      : <span className="download-state is-unknown">访问未开放</span>}
                  </header>
                  <p>{artwork.request_payload?.prompt || "制作说明未记录"}</p>
                  <dl>
                    <div><dt>规格</dt><dd>{studioTaskParametersLabel(artwork.request_payload)}</dd></div>
                    <div><dt>文件</dt><dd>{formatBytes(artwork.size_bytes)}，{artwork.content_type || "格式未记录"}</dd></div>
                    <div><dt>保存时间</dt><dd>{shortDate(artwork.created_at)}</dd></div>
                  </dl>
                  <small className={`download-detail is-${artifactAccessAvailable ? state.tone : "unknown"}`}>
                    {artifactAccessAvailable ? state.detail : "作品信息已保留，当前空间暂不支持预览或下载。"}
                  </small>
                  <footer className="artwork-action-row">
                    <button
                      className="is-primary"
                      type="button"
                      disabled={!canCreateTasks}
                      title={!canCreateTasks ? "当前账号不能创建任务" : undefined}
                      onClick={() => onAdjust?.(artwork)}
                    >
                      继续创作
                    </button>
                    <button
                      className="download-button"
                      type="button"
                      disabled={!liveMode || !artifactAccessAvailable || Boolean(actionKey)}
                      title={!liveMode
                        ? "示例作品没有可下载的真实文件"
                        : !artifactAccessAvailable
                          ? "当前账号不能下载作品"
                          : undefined}
                      onClick={() => onDownload(artwork)}
                    >
                      {actionKey === `download:${key}` ? <SpinnerGap className="spin" size={17} /> : <DownloadSimple size={17} />}
                      {actionKey === `download:${key}`
                        ? "正在准备"
                        : !liveMode
                          ? "示例不可下载"
                          : artifactAccessAvailable
                            ? "下载作品"
                            : "下载未开放"}
                    </button>
                    <button
                      className="text-button"
                      type="button"
                      disabled={!canPublish}
                      title={!canPublish
                        ? workspaceKind === "personal"
                          ? "个人空间发布能力尚未开放"
                          : "当前账号不能创建发布任务，或公司尚未开通发布功能"
                        : undefined}
                      onClick={() => onPublish?.(artwork)}
                    >
                      去发布
                    </button>
                    <details className="artwork-more-actions">
                      <summary>更多</summary>
                      <div>
                        <button className="text-button" type="button" onClick={() => onOpenTask(artwork)}>查看任务</button>
                        {liveMode ? <button
                          className="text-button"
                          type="button"
                          disabled={!canPromoteArtifacts || Boolean(actionKey)}
                          title={!canPromoteArtifacts
                            ? workspaceKind === "personal"
                              ? "个人空间暂不支持保存素材"
                              : "当前账号不能管理素材"
                            : "保存到公司素材库"}
                          onClick={() => onPromote?.(artwork)}
                        >
                          {actionKey === `promote:${key}` ? <SpinnerGap className="spin" size={17} /> : <Plus size={17} />}
                          {actionKey === `promote:${key}` ? "正在保存" : "存入素材库"}
                        </button> : null}
                      </div>
                    </details>
                  </footer>
                </div>
              </article>
            );
          })}
        </div>
      )}
      {liveMode && !loading && !error && (
        <PageControls page={page} pageSize={pageSize} total={total} onChange={onPageChange} />
      )}
    </section>
  );
}
