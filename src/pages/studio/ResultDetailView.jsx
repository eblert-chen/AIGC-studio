import {
  ArrowCounterClockwise,
  CloudCheck,
  DownloadSimple,
  ImageSquare,
  PaperPlaneTilt,
  Plus,
  SlidersHorizontal,
  SpinnerGap,
  VideoCamera,
  WarningCircle,
  X,
} from "@phosphor-icons/react";
import { taskCostLabel } from "../../taskArtifacts.js";
import { resolveTaskStatus } from "../../taskStatus.js";
import { DownloadBadge } from "../../components/studio/StudioCollectionControls.jsx";
import {
  artifactKindLabel,
  formatBytes,
  shortId,
} from "../../components/studio/studioPresentation.js";
import { IconButton } from "../../components/studio/StudioWorkspaceViews.jsx";

export function ResultDetailView({
  resultTask,
  liveMode,
  resultTaskStatus,
  ResultStatusIcon,
  resultOutputArtifacts,
  artworks,
  canAccessArtifacts,
  issuedArtifacts,
  artifactActionKey,
  downloadingAssetId,
  canManageAssets,
  canStartPublication,
  isPersonalWorkspace,
  canCreateTasks,
  downloadError,
  onClose,
  onDownloadArtifact,
  onPromoteArtifact,
  onOpenPublication,
  onCreateAgain,
  onAdjust,
  onDemoDownload,
}) {
  return (
    <>
      <header>
        <div>
          <span className="view-kicker">
            {resultTask?.status === "succeeded" ? "生成完成" : "任务详情"}
          </span>
          <h2 id="result-title">
            {liveMode
              ? `任务 ${shortId(resultTask?.id || resultTask?.task_id)}`
              : resultTask?.request_payload?.prompt || "产品防水演示"}
          </h2>
        </div>
        <IconButton label="关闭" onClick={onClose}>
          <X size={20} aria-hidden="true" />
        </IconButton>
      </header>
      {resultTask?.status !== "succeeded" ? (
        <div className="artifact-empty">
          <ResultStatusIcon
            className={resultTaskStatus.active ? "spin" : ""}
            size={28}
            aria-hidden="true"
          />
          <strong>{resolveTaskStatus(resultTask?.status).label}</strong>
          <span>
            {resultTask?.failure_reason ||
              (liveMode
                ? resultTaskStatus.detail
                : "这是演示任务状态，不会连接真实生成渠道。")}
          </span>
          <small>{taskCostLabel(resultTask)}</small>
        </div>
      ) : liveMode ? (
        <div className="artifact-results">
          {resultOutputArtifacts.length > 0 ? (
            resultOutputArtifacts.map((artifact, index) => {
              const taskId = resultTask?.id || resultTask?.task_id;
              const key = `${taskId}:${artifact.asset_id}`;
              const evidence = artworks.find(
                (item) => item.task_id === taskId && item.asset_id === artifact.asset_id,
              ) || artifact;
              return (
                <article className="artifact-result" key={artifact.asset_id}>
                  <div className="artifact-result-icon" aria-hidden="true">
                    {artifact.media_type === "image" ? (
                      <ImageSquare size={23} />
                    ) : (
                      <VideoCamera size={23} />
                    )}
                  </div>
                  <div className="artifact-result-copy">
                    <strong>
                      {artifactKindLabel(artifact.media_type)}产物 {index + 1}
                    </strong>
                    <span>
                      {artifact.content_type} · {formatBytes(artifact.size_bytes)}
                    </span>
                    <small title={artifact.sha256}>
                      校验值 {artifact.sha256?.slice(0, 12) || "-"}…
                    </small>
                    {canAccessArtifacts
                      ? <DownloadBadge source={evidence} issuedLocally={Boolean(issuedArtifacts[key])} />
                      : <span className="download-state is-unknown">访问未开放</span>}
                  </div>
                  <div className="artifact-result-actions">
                    <button
                      className="download-button"
                      type="button"
                      disabled={!canAccessArtifacts || Boolean(artifactActionKey)}
                      title={!canAccessArtifacts ? "个人空间产物访问尚未开放" : undefined}
                      onClick={() => onDownloadArtifact(artifact)}
                    >
                      {downloadingAssetId === artifact.asset_id ? (
                        <SpinnerGap className="spin" size={18} aria-hidden="true" />
                      ) : (
                        <DownloadSimple size={18} aria-hidden="true" />
                      )}
                      {downloadingAssetId === artifact.asset_id
                        ? "正在获取"
                        : canAccessArtifacts ? "下载" : "下载未开放"}
                    </button>
                    <button
                      className="text-button"
                      type="button"
                      disabled={!canAccessArtifacts || !canManageAssets || Boolean(artifactActionKey)}
                      title={!canAccessArtifacts
                        ? "个人空间产物访问尚未开放"
                        : !canManageAssets
                          ? "需要 assets.manage 权限"
                          : "服务端校验并转存为私有输入素材"}
                      onClick={() => onPromoteArtifact(artifact)}
                    >
                      {artifactActionKey === `promote:${key}` ? <SpinnerGap className="spin" size={17} /> : <Plus size={17} />}
                      {artifactActionKey === `promote:${key}` ? "正在转存" : "存为参考"}
                    </button>
                    <button
                      className="text-button"
                      type="button"
                      disabled={!canStartPublication || !artifact.artifact_id}
                      title={!artifact.artifact_id
                        ? "缺少平台归档作品标识，请刷新后再试"
                        : !canStartPublication
                          ? isPersonalWorkspace
                            ? "个人空间发布能力尚未开放"
                            : "需要发布权限及自动发布授权"
                          : undefined}
                      onClick={() => onOpenPublication(artifact)}
                    >
                      <PaperPlaneTilt size={17} />
                      去发布
                    </button>
                  </div>
                </article>
              );
            })
          ) : (
            <div className="artifact-empty">
              <CloudCheck size={28} aria-hidden="true" />
              <strong>任务已经完成</strong>
              <span>平台暂未返回可下载的产物元数据，请稍后查看任务。</span>
            </div>
          )}
          {downloadError && (
            <p className="artifact-download-error" role="alert">
              <WarningCircle size={17} aria-hidden="true" />
              {downloadError}
            </p>
          )}
          <div className="artifact-security-note">
            {canAccessArtifacts
              ? "地址签发不等于下载完成；完成状态只接受存储侧可信回传。"
              : "产物校验与归档元数据已保留；个人空间本期不提供访问 URL。"}
          </div>
          <div className="result-followup-actions" aria-label="继续创作">
            <span>
              <strong>继续完善这个结果</strong>
              <small>只恢复草稿并按当前能力重新校验，不会立即创建任务或扣费。</small>
            </span>
            <button
              type="button"
              disabled={!canCreateTasks}
              onClick={() => onCreateAgain(resultTask)}
            >
              <ArrowCounterClockwise size={17} />
              再次生成
            </button>
            <button
              className="is-primary"
              type="button"
              disabled={!canCreateTasks}
              onClick={() => onAdjust(resultTask)}
            >
              <SlidersHorizontal size={17} />
              调整后再创作
            </button>
          </div>
        </div>
      ) : (
        <>
          <img src="/media/speaker-water-hero.png" alt="生成成片预览" />
          <div className="result-actions">
            <span>15 秒 · 16:9 · 已转存</span>
            <button
              className="download-button"
              type="button"
              onClick={onDemoDownload}
            >
              <DownloadSimple size={18} aria-hidden="true" />
              下载成片
            </button>
          </div>
        </>
      )}
    </>
  );
}
