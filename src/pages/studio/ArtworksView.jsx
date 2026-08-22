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
  downloadState,
  taskAuthor,
  taskCompany,
  taskCostLabel,
  taskParametersLabel,
} from "../../taskArtifacts.js";
import {
  DownloadBadge,
  LoadingRows,
  PageControls,
  ScopeControl,
} from "../../components/studio/StudioCollectionControls.jsx";
import {
  artifactKindLabel,
  formatBytes,
  shortDate,
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
  companyName = "",
  canCreateTasks = true,
  canPublish = false,
  canPromoteArtifacts = false,
  artifactAccessAvailable = true,
  supportsDownloadFilter = true,
  workspaceKind = "company",
  onCreateAgain,
  onAdjust,
  onPublish,
  onPromote,
  currentUserName = "",
}) {
  const displayedArtworks = liveMode ? artworks : demoArtworks;
  return (
    <section className="secondary-view artworks-view">
      <div className="secondary-heading">
        <div>
          <span className="view-kicker">长期归档</span>
          <h1>作品</h1>
          <p>
            {workspaceKind === "personal"
              ? "这里只显示当前个人空间已归档的作品元数据；本期尚未开放预览、下载与转存。"
              : "这里只展示已成功转存的图片和视频，临时渠道地址不会作为作品保存。"}
          </p>
        </div>
        <div className="history-toolbar artwork-toolbar">
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
        </div>
      </div>
      {loading && <LoadingRows label="正在读取作品" />}
      {!loading && error && (
        <div className="artifact-empty" role="alert">
          <WarningCircle size={26} aria-hidden="true" />
          <strong>作品读取失败</strong><span>{error}</span>
        </div>
      )}
      {!loading && !error && displayedArtworks.length === 0 && (
        <div className="artifact-empty">
          <Images size={28} aria-hidden="true" />
          <strong>当前范围还没有作品</strong>
          <span>成功生成并完成私有转存后，产物会出现在这里。</span>
        </div>
      )}
      {!loading && !error && displayedArtworks.length > 0 && (
        <div className="artwork-grid">
          {displayedArtworks.map((artwork) => {
            const key = `${artwork.task_id}:${artwork.asset_id}`;
            const leasedPreviewUrl = activePreviewUrl(previewUrls[key]);
            const previewUrl = leasedPreviewUrl || (!liveMode ? artwork.preview_url : "");
            const state = downloadState(artwork, { issuedLocally: Boolean(issuedArtifacts[key]) });
            const MediaIcon = artwork.media_type === "image" ? ImageSquare : VideoCamera;
            return (
              <article className="artwork-item" key={artwork.artifact_id || key}>
                <div className="artwork-media">
                  {previewUrl ? (
                    artwork.media_type === "image" ? (
                      <img
                        src={previewUrl}
                        alt={`由 ${taskAuthor(artwork, currentUserName)} 生成的图片作品`}
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
                      <img src={previewUrl} alt="演示视频作品画面" />
                    )
                  ) : artifactAccessAvailable ? (
                    <button type="button" onClick={() => onPreview(artwork)} disabled={Boolean(actionKey)}>
                      {actionKey === `preview:${key}` ? <SpinnerGap className="spin" size={28} /> : <MediaIcon size={36} weight="duotone" />}
                      <strong>{actionKey === `preview:${key}` ? "正在签发" : "加载安全预览"}</strong>
                      <span>预览会签发一条短时访问记录</span>
                    </button>
                  ) : (
                    <div className="artwork-access-unavailable" role="note">
                      <MediaIcon size={36} weight="duotone" aria-hidden="true" />
                      <strong>作品已归档</strong>
                      <span>个人空间暂未开放产物访问</span>
                    </div>
                  )}
                </div>
                <div className="artwork-copy">
                  <header>
                    <span><strong>{artwork.model_display_name || "模型未记录"}</strong><small>{artifactKindLabel(artwork.media_type)} {Number(artwork.output_index || 0) + 1}</small></span>
                    {artifactAccessAvailable
                      ? <DownloadBadge source={artwork} issuedLocally={Boolean(issuedArtifacts[key])} />
                      : <span className="download-state is-unknown">访问未开放</span>}
                  </header>
                  <p>{artwork.request_payload?.prompt || "制作说明未记录"}</p>
                  <dl>
                    <div><dt>发起人</dt><dd>{taskAuthor(artwork, currentUserName)}</dd></div>
                    <div>
                      <dt>{workspaceKind === "personal" ? "空间" : "公司"}</dt>
                      <dd title={artwork.company_id || artwork.workspace_id}>{taskCompany(artwork, companyName)}</dd>
                    </div>
                    <div><dt>参数</dt><dd>{taskParametersLabel(artwork.request_payload)}</dd></div>
                    <div><dt>{workspaceKind === "personal" ? "积分" : "费用"}</dt><dd>{taskCostLabel({
                      status: "succeeded",
                      actual_cost_points: artwork.actual_cost_points,
                      actual_cost_cents: artwork.actual_cost_cents,
                    })}</dd></div>
                    <div><dt>文件</dt><dd>{formatBytes(artwork.size_bytes)}，{artwork.content_type || "格式未记录"}</dd></div>
                    <div><dt>归档</dt><dd>{shortDate(artwork.created_at)}</dd></div>
                  </dl>
                  <small className={`download-detail is-${artifactAccessAvailable ? state.tone : "unknown"}`}>
                    {artifactAccessAvailable ? state.detail : "产物元数据已保留；当前个人能力合同不提供访问 URL。"}
                  </small>
                  <footer>
                    <button className="text-button" type="button" onClick={() => onOpenTask(artwork)}>查看任务</button>
                    <button
                      className="download-button"
                      type="button"
                      disabled={!artifactAccessAvailable || Boolean(actionKey)}
                      title={!artifactAccessAvailable ? "个人空间产物访问尚未开放" : undefined}
                      onClick={() => onDownload(artwork)}
                    >
                      {actionKey === `download:${key}` ? <SpinnerGap className="spin" size={17} /> : <DownloadSimple size={17} />}
                      {actionKey === `download:${key}` ? "正在签发" : artifactAccessAvailable ? "获取下载地址" : "下载未开放"}
                    </button>
                  </footer>
                  <div className="artwork-followup-actions" aria-label="作品后续操作">
                    <button
                      className="text-button"
                      type="button"
                      disabled={!canCreateTasks}
                      title={!canCreateTasks ? "缺少 tasks.create 权限" : undefined}
                      onClick={() => onCreateAgain?.(artwork)}
                    >
                      再次生成
                    </button>
                    <button
                      className="text-button"
                      type="button"
                      disabled={!canCreateTasks}
                      title={!canCreateTasks ? "缺少 tasks.create 权限" : undefined}
                      onClick={() => onAdjust?.(artwork)}
                    >
                      调整后再创作
                    </button>
                    <button
                      className="text-button"
                      type="button"
                      disabled={!canPublish}
                      title={!canPublish
                        ? workspaceKind === "personal"
                          ? "个人空间发布能力尚未开放"
                          : "需要发布账号读取、发布任务管理权限及自动发布授权"
                        : undefined}
                      onClick={() => onPublish?.(artwork)}
                    >
                      去发布
                    </button>
                    <button
                      className="text-button"
                      type="button"
                      disabled={!canPromoteArtifacts || Boolean(actionKey)}
                      title={!canPromoteArtifacts
                        ? workspaceKind === "personal"
                          ? "个人空间素材与产物访问尚未开放"
                          : "需要 assets.manage 权限"
                        : "服务端校验并转存为私有输入素材"}
                      onClick={() => onPromote?.(artwork)}
                    >
                      {actionKey === `promote:${key}` ? <SpinnerGap className="spin" size={17} /> : <Plus size={17} />}
                      {actionKey === `promote:${key}` ? "正在转存" : "存为参考素材"}
                    </button>
                  </div>
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
