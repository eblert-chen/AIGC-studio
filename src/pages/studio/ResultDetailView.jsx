import {
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
import { DownloadStatus, IconButton } from "../../components/studio/StudioWorkspaceViews.jsx";
import {
  artifactEvidenceIssueMessage,
  artifactKindLabel,
  formatBytes,
  shortId,
  studioErrorMessage,
} from "../../components/studio/studioPresentation.js";

export function ResultDetailView({
  resultTask,
  liveMode,
  resultTaskStatus,
  ResultStatusIcon,
  resultArtifactEvidence,
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
  onAdjust,
}) {
  return (
    <>
      <header className="result-detail-header">
        <div className="result-detail-heading">
          <h2 id="result-title">
            {liveMode
              ? `任务 ${shortId(resultTask?.id || resultTask?.task_id)}`
              : resultTask?.request_payload?.prompt || "产品防水示例"}
          </h2>
        </div>
        <IconButton label="关闭任务详情" onClick={onClose}>
          <X size={20} aria-hidden="true" />
        </IconButton>
      </header>
      <div className="result-detail-body">
        {resultTask?.status !== "succeeded" || (liveMode && !resultArtifactEvidence?.complete) ? (
          <div className="artifact-empty">
            <ResultStatusIcon
              className={resultTaskStatus.active ? "spin" : ""}
              size={28}
              aria-hidden="true"
            />
            <strong>{liveMode && resultTask?.status === "succeeded" ? "作品文件未就绪" : resolveTaskStatus(resultTask?.status).label}</strong>
            <span>
              {liveMode && resultTask?.status === "succeeded"
                ? artifactEvidenceIssueMessage(resultArtifactEvidence)
                : studioErrorMessage(
                    resultTask?.failure_reason,
                    liveMode ? resultTaskStatus.detail : "这是示例任务状态，不会连接真实生成渠道。",
                  )}
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
                        {artifactKindLabel(artifact.media_type)}作品 {index + 1}
                      </strong>
                      <span>
                        {artifact.content_type || "格式未记录"}，{formatBytes(artifact.size_bytes)}
                      </span>
                      <small title={artifact.sha256}>
                        {artifact.sha256 ? "文件已校验" : "校验信息未记录"}
                      </small>
                      {canAccessArtifacts
                        ? <DownloadStatus source={evidence} issuedLocally={Boolean(issuedArtifacts[key])} />
                        : <span className="download-state is-unknown">访问未开放</span>}
                    </div>
                    <div className="artifact-result-actions">
                      <button
                        className="download-button"
                        type="button"
                        disabled={!canAccessArtifacts || Boolean(artifactActionKey)}
                        title={!canAccessArtifacts ? "当前账号不能下载作品" : undefined}
                        onClick={() => onDownloadArtifact(artifact)}
                      >
                        {downloadingAssetId === artifact.asset_id ? (
                          <SpinnerGap className="spin" size={18} aria-hidden="true" />
                        ) : (
                          <DownloadSimple size={18} aria-hidden="true" />
                        )}
                        {downloadingAssetId === artifact.asset_id
                          ? "正在准备"
                          : canAccessArtifacts ? "下载作品" : "下载未开放"}
                      </button>
                      <button
                        className="text-button"
                        type="button"
                        disabled={!canAccessArtifacts || !canManageAssets || Boolean(artifactActionKey)}
                        title={!canAccessArtifacts
                          ? "当前账号不能访问作品文件"
                          : !canManageAssets
                            ? "当前账号不能管理素材"
                            : "保存到公司素材库"}
                        onClick={() => onPromoteArtifact(artifact)}
                      >
                        {artifactActionKey === `promote:${key}` ? <SpinnerGap className="spin" size={17} /> : <Plus size={17} />}
                        {artifactActionKey === `promote:${key}` ? "正在保存" : "存入素材库"}
                      </button>
                      <button
                        className="text-button"
                        type="button"
                        disabled={!canStartPublication || !artifact.artifact_id}
                        title={!artifact.artifact_id
                          ? "作品信息不完整，请刷新后再试"
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
                <strong>作品文件尚未就绪</strong>
                <span>请稍后刷新任务；文件就绪前，下载和后续操作保持关闭。</span>
              </div>
            )}
            {downloadError && (
              <p className="artifact-download-error" role="alert">
                <WarningCircle size={17} aria-hidden="true" />
                {studioErrorMessage(downloadError, "下载暂时无法开始，请稍后重试。")}
              </p>
            )}
            <div className="artifact-security-note">
              {canAccessArtifacts
                ? "生成下载链接不代表下载完成；完成状态会在系统确认后更新。"
                : "作品文件已安全保存，当前个人空间暂不支持预览或下载。"}
            </div>
          </div>
        ) : (
          <img src="/media/speaker-water-hero.png" alt="生成成片预览" />
        )}
      </div>
      {resultTask?.status === "succeeded" && (!liveMode || resultArtifactEvidence?.complete) && (
        <footer className="result-detail-footer">
          {liveMode ? (
            <div className="result-followup-actions" aria-label="继续创作">
              <span>
                <strong>继续完善这个结果</strong>
                <small>只恢复草稿并按当前能力重新校验，不会立即创建任务或扣费。</small>
              </span>
              <button
                className="is-primary"
                type="button"
                disabled={!canCreateTasks}
                onClick={() => onAdjust(resultTask)}
              >
                <SlidersHorizontal size={17} />
                继续创作
              </button>
            </div>
          ) : (
            <div className="result-actions">
              <span>示例结果，不包含真实文件，无法下载。</span>
              <button className="download-button" type="button" disabled title="示例模式没有可下载的真实文件">
                <DownloadSimple size={18} aria-hidden="true" />
                示例不可下载
              </button>
            </div>
          )}
        </footer>
      )}
    </>
  );
}
