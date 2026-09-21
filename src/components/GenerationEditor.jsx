import {
  ArrowLeft,
  CaretDown,
  CaretRight,
  Check,
  CheckCircle,
  ClockCounterClockwise,
  FilmSlate,
  ImageSquare,
  Images,
  MusicNote,
  Play,
  Plus,
  SlidersHorizontal,
  Sparkle,
  SpinnerGap,
  VideoCamera,
  WarningCircle,
  X,
} from "@phosphor-icons/react";

const PERSONAL_CATALOG_INTEGRATION_CODES = new Set([
  "model_unpublished",
  "relay_capability_unapproved",
  "personal_distribution_unconfigured",
  "personal_price_unavailable",
]);

/**
 * Shared generation view for quick creation and contextual workbench editors.
 * The host owns every draft, capability, price, pending request, and event.
 * MediaInputGroup is injected to keep this view independent of the App module.
 */
export default function GenerationEditor({
  variant = "quick",
  title = "",
  onClose,
  pendingContext,
  pendingBelongsToDraft,
  restorePendingCreate,
  pendingCreateRef,
  hasVisibleMediaInputs,
  composerExpanded,
  composerPanel,
  mobileComposerOpen,
  composerRef,
  handleComposerEscape,
  focusCommunityComposer,
  activeNav,
  generationMode,
  generationModeLabel,
  composerSpecSummary,
  setComposerPanel,
  setMobileComposerOpen,
  modelsLoading,
  model,
  hasVisibleRecipe,
  toggleComposerPanelFromTrigger,
  composerVideoAvailable,
  composerImageAvailable,
  handleComposerMediaKeyDown,
  composerMediaKind,
  selectComposerMediaKind,
  supportedModes,
  selectGenerationMode,
  quickDraftStorageNotice,
  activeCapability,
  closeComposerPanel,
  prompt,
  historicalRequestLocked,
  generationPromptLength,
  promptLimit,
  setPrompt,
  truncateGenerationPrompt,
  promptError,
  setPromptError,
  formError,
  setFormError,
  promptSubmissionIssue,
  composerSubmitMessageId,
  composerReferenceCount,
  visibleMediaLimits,
  referenceUploadNote,
  MediaInputGroup,
  files,
  handleFiles,
  removeInputAsset,
  uploadingKind,
  LIVE_MODE,
  canManageAssets,
  hasVisibleSpecifications,
  ratio,
  resolution,
  modeUsesDuration,
  duration,
  outputCount,
  setRatio,
  setResolution,
  setDuration,
  setOutputCount,
  fixedSpecificationFields,
  isPersonalWorkspace,
  modelsError,
  models,
  modelId,
  composerModelPrice,
  personalModelCatalog = [],
  personalModelCatalogLoading = false,
  personalModelCatalogError = "",
  selectStudioModel,
  DEMO_MODE,
  generationReadiness,
  hasServerReadinessEvidence,
  serverGenerationReadiness,
  localReadinessBlockers,
  readinessCheckedAtLabel,
  faceControlAvailable,
  faceEnabled,
  setFaceEnabled,
  readinessEvidenceReasons,
  historicalFaceVisible,
  readinessHeadline,
  showCostPreview,
  costLabel,
  startGeneration,
  composerSubmitState,
  taskStageIsActive,
}) {
  const isQuick = variant === "quick";
  if (pendingContext && !pendingBelongsToDraft) return (
    <aside className={isQuick ? "community-composer pending-draft-recovery" : "workbench-generation-editor pending-draft-recovery"} data-ui={isQuick ? "director-composer" : "workbench-editor"}>
      <strong>另一份草稿的提交仍待确认</strong>
      <p>当前草稿已保留。先返回原草稿确认同一次请求，不会新建重复任务。</p>
      <button type="button" onClick={() => restorePendingCreate(pendingCreateRef.current)}>返回待确认草稿</button>
    </aside>
  );
  const availableModelIds = new Set(models.map((item) => item.id));
  const catalogOnlyModels = isPersonalWorkspace
    ? personalModelCatalog.filter((item) => !availableModelIds.has(item.id))
    : [];
  const modelPanelError = modelsError && personalModelCatalogError
    ? "可用模型和接入进度暂时无法完整读取，请刷新重试。"
    : modelsError || (personalModelCatalogError
      ? `模型接入进度暂时无法读取：${personalModelCatalogError}`
      : "");
  const modelTriggerLabel = modelsLoading
    ? "正在读取…"
    : model.id
      ? model.name
      : isPersonalWorkspace && personalModelCatalogLoading
        ? "读取接入进度…"
        : isPersonalWorkspace && personalModelCatalog.length > 0
          ? "查看接入进度"
          : "暂无可用模型";
  return (
    <aside
      className={`${isQuick ? "inspector community-composer" : `workbench-generation-editor is-${variant}`} ${hasVisibleMediaInputs ? "has-media-inputs" : ""} ${composerExpanded ? "is-expanded" : ""}`}
      aria-label={isQuick ? "生成参数" : title || "当前草稿"}
      data-ui={isQuick ? "director-composer" : "workbench-editor"}
      data-editor-variant={variant}
      data-panel={composerPanel || "closed"}
      data-mobile-open={mobileComposerOpen}
      ref={composerRef}
      onKeyDown={(event) => {
        if (!isQuick && event.key === "Escape" && !composerPanel && onClose) {
          event.preventDefault();
          onClose();
          return;
        }
        handleComposerEscape(event);
      }}
    >
      {isQuick && <>
      <button
        id="mobile-composer-launcher"
        className="composer-mobile-launcher"
        type="button"
        aria-expanded={mobileComposerOpen}
        onClick={focusCommunityComposer}
      >
        <span><small>{activeNav === "create" ? "创作下一版" : "开始创作"}</small><strong>{generationMode ? generationModeLabel(generationMode) : "选择创作方式"}</strong></span>
        <span>{composerSpecSummary}</span>
        <CaretRight size={18} aria-hidden="true" />
      </button>
      <header className="composer-mobile-edit-header">
        <button type="button" aria-label={activeNav === "create" ? "返回当前成片" : "返回首页灵感"} onClick={() => {
          setComposerPanel(null);
          setMobileComposerOpen(false);
          globalThis.requestAnimationFrame?.(() => composerRef.current?.querySelector("#mobile-composer-launcher")?.focus());
        }}>
          <ArrowLeft size={18} aria-hidden="true" />
        </button>
        <span><small>{activeNav === "create" ? "创作下一版" : "开始创作"}</small><strong>{modelTriggerLabel}</strong></span>
        {hasVisibleRecipe && <button type="button" data-composer-panel-trigger="recipe" className={composerPanel === "recipe" ? "is-active" : ""} aria-controls="composer-recipe-panel" aria-expanded={composerPanel === "recipe"} onClick={(event) => toggleComposerPanelFromTrigger(event, "recipe")}>
          <span>{generationMode ? generationModeLabel(generationMode) : "创作方式"}</span>
          <CaretRight size={14} aria-hidden="true" />
        </button>}
      </header>
      </>}
      {!isQuick && <header className="workbench-editor-heading">
        <h3>{title || (variant === "console" ? "镜头场记" : variant === "notebook" ? "编辑这一场" : "编辑节点")}</h3>
        <div>
          {hasVisibleRecipe && <button type="button" data-composer-panel-trigger="recipe" aria-controls="composer-recipe-panel" aria-expanded={composerPanel === "recipe"} onClick={(event) => toggleComposerPanelFromTrigger(event, "recipe")}>{generationMode ? generationModeLabel(generationMode) : "创作方式"}<CaretDown size={14} aria-hidden="true" /></button>}
          {onClose && <button type="button" onClick={onClose} aria-label="收起编辑"><X size={18} aria-hidden="true" /></button>}
        </div>
      </header>}
      {isQuick && <header className="community-composer-header">
        <span className="composer-kind-label">
          <FilmSlate size={16} aria-hidden="true" />
          <span className="composer-kind-label-long">导演控制台</span>
          <span className="composer-kind-label-short" aria-hidden="true">创作</span>
        </span>
        {(composerVideoAvailable || composerImageAvailable) && <div
          className="composer-media-tabs"
          role="tablist"
          aria-label="生成内容类型"
          onKeyDown={handleComposerMediaKeyDown}
        >
          {composerVideoAvailable && <button
            id="composer-media-tab-video"
            type="button"
            role="tab"
            aria-selected={composerMediaKind === "video"}
            aria-controls="composer-parameters-panel"
            tabIndex={composerMediaKind === "video" ? 0 : -1}
            className={composerMediaKind === "video" ? "is-active" : ""}
            disabled={Boolean(pendingCreateRef.current)}
            onClick={() => selectComposerMediaKind("video")}
          >
            <VideoCamera size={16} aria-hidden="true" />
            视频
          </button>}
          {composerImageAvailable && <button
            id="composer-media-tab-image"
            type="button"
            role="tab"
            aria-selected={composerMediaKind === "image"}
            aria-controls="composer-parameters-panel"
            tabIndex={composerMediaKind === "image" ? 0 : -1}
            className={composerMediaKind === "image" ? "is-active" : ""}
            disabled={Boolean(pendingCreateRef.current)}
            onClick={() => selectComposerMediaKind("image")}
          >
            <ImageSquare size={16} aria-hidden="true" />
            图片
          </button>}
        </div>}
        {supportedModes.length > 0 && <div className="composer-mode-line">
          <span className="composer-mode-eyebrow">创作方式</span>
          <div
            id="generation-mode"
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
        </div>}
        <span className="composer-current-mode">
          {generationMode ? generationModeLabel(generationMode) : "等待模型能力"}
        </span>

      </header>}
      {isQuick && quickDraftStorageNotice && <p className="quick-draft-storage-notice" role="status">{quickDraftStorageNotice}</p>}
      <div
        id="composer-parameters-panel"
        className="inspector-scroll"
        role={isQuick ? "tabpanel" : "group"}
        aria-labelledby={isQuick && activeCapability ? `composer-media-tab-${composerMediaKind}` : undefined}
        aria-label={isQuick && activeCapability ? undefined : "创作参数"}
      >
        {composerPanel && (
          <button className="composer-mobile-draft-summary" type="button" onClick={() => closeComposerPanel(composerPanel)}>
            <span>制作说明</span>
            <strong>{prompt || "尚未填写制作说明"}</strong>
            <span>返回编辑</span>
          </button>
        )}
        <section className="inspector-section composer-prompt-section">
          <div className="field-heading">
            <label htmlFor="prompt">{isQuick ? "制作说明" : variant === "console" ? "镜头描述" : variant === "notebook" ? "本场描述" : "节点描述"}</label>
            <span>
              {historicalRequestLocked
                ? `${generationPromptLength(prompt)} 字，只读`
                : `${generationPromptLength(prompt)} / ${promptLimit}`}
            </span>
          </div>
          <div className="director-intent-well">
            <textarea
              id="prompt"
              value={prompt}
              placeholder={isQuick ? "描述您想要创作的内容" : variant === "notebook" ? "写下这一场的画面、人物动作和节奏…" : variant === "canvas" ? "写下这个方向的画面与变化…" : "写下镜头、光线和画面里的动作…"}
              disabled={historicalRequestLocked || !activeCapability}
              onChange={(event) => {
                // Native maxlength counts UTF-16 and would cut an otherwise
                // valid code-point-limited prompt before this handler runs.
                setPrompt(truncateGenerationPrompt(event.target.value, promptLimit || 10_000));
                if (promptError) setPromptError("");
                if (formError) setFormError("");
              }}
              aria-invalid={promptSubmissionIssue}
              aria-describedby={promptSubmissionIssue ? composerSubmitMessageId : undefined}
            />
            {hasVisibleMediaInputs && (
              <button
                className="director-reference-trigger"
                type="button"
                data-composer-panel-trigger="references"
                aria-controls="composer-references-panel"
                aria-expanded={composerPanel === "references"}
                onClick={(event) => toggleComposerPanelFromTrigger(event, "references")}
              >
                <Plus size={18} aria-hidden="true" />
                <span>参考素材</span>
                <strong>{composerReferenceCount} / {visibleMediaLimits.image + visibleMediaLimits.video + visibleMediaLimits.audio}</strong>
              </button>
            )}
          </div>
        </section>


        {composerPanel === "recipe" && hasVisibleRecipe && (
          <section
            id="composer-recipe-panel"
            className="inspector-section director-context-panel director-recipe-panel"
            aria-labelledby="composer-recipe-title"
          >
            <header className="director-context-header">
              <div>
                <strong id="composer-recipe-title" tabIndex={-1} data-composer-panel-focus>创作方式</strong>
                <span>只展示当前模型在此工作区可用的生成方式</span>
              </div>
              <button className="director-context-close" type="button" onClick={() => closeComposerPanel("recipe")} aria-label="关闭创作方式">
                <X size={16} aria-hidden="true" />
              </button>
            </header>
            <div className="director-recipe-groups">
              <div className="director-choice-group">
                <span>内容类型</span>
                <div>
                  {composerVideoAvailable && (
                    <button type="button" className={composerMediaKind === "video" ? "is-active" : ""} aria-pressed={composerMediaKind === "video"} disabled={Boolean(pendingCreateRef.current)} onClick={() => selectComposerMediaKind("video")}>视频</button>
                  )}
                  {composerImageAvailable && (
                    <button type="button" className={composerMediaKind === "image" ? "is-active" : ""} aria-pressed={composerMediaKind === "image"} disabled={Boolean(pendingCreateRef.current)} onClick={() => selectComposerMediaKind("image")}>图片</button>
                  )}
                </div>
              </div>
              <div className="director-choice-group">
                <span>生成模式</span>
                <div>
                  {supportedModes.map((item) => (
                    <button key={item} type="button" className={generationMode === item ? "is-active" : ""} aria-pressed={generationMode === item} disabled={Boolean(pendingCreateRef.current)} onClick={() => selectGenerationMode(item)}>{generationModeLabel(item)}</button>
                  ))}
                </div>
              </div>
            </div>
          </section>
        )}

        {composerPanel === "references" && hasVisibleMediaInputs && (
          <section
            id="composer-references-panel"
            className="inspector-section director-context-panel director-reference-panel"
            aria-labelledby="composer-references-title"
          >
            <header className="director-context-header">
              <div>
                <strong id="composer-references-title" tabIndex={-1} data-composer-panel-focus>参考素材</strong>
                <span>只添加会影响当前镜头的内容；支持多选和逐项移除</span>
              </div>
              <button className="director-context-close" type="button" onClick={() => closeComposerPanel("references")} aria-label="关闭参考素材">
                <X size={16} aria-hidden="true" />
              </button>
            </header>
            {referenceUploadNote && (
              <p id="composer-reference-upload-note" className="director-reference-note">
                {referenceUploadNote}
              </p>
            )}
            <div className="media-groups">
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
                  uploadDisabled={Boolean(pendingCreateRef.current) || Boolean(uploadingKind) || (LIVE_MODE && !canManageAssets)}
                  locked={Boolean(pendingCreateRef.current)}
                  disabledReasonId={referenceUploadNote ? "composer-reference-upload-note" : ""}
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
                  uploadDisabled={Boolean(pendingCreateRef.current) || Boolean(uploadingKind) || (LIVE_MODE && !canManageAssets)}
                  locked={Boolean(pendingCreateRef.current)}
                  disabledReasonId={referenceUploadNote ? "composer-reference-upload-note" : ""}
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
                  uploadDisabled={Boolean(pendingCreateRef.current) || Boolean(uploadingKind) || (LIVE_MODE && !canManageAssets)}
                  locked={Boolean(pendingCreateRef.current)}
                  disabledReasonId={referenceUploadNote ? "composer-reference-upload-note" : ""}
                  icon={MusicNote}
                />
              )}
            </div>
          </section>
        )}

        {composerPanel === "specs" && hasVisibleSpecifications && (
          <section
            id="composer-specs-panel"
            className="inspector-section director-context-panel director-spec-panel"
            aria-labelledby="composer-specs-title"
          >
            <header className="director-context-header">
              <div>
                <strong id="composer-specs-title" tabIndex={-1} data-composer-panel-focus>输出规格</strong>
                <span>{historicalRequestLocked ? "待确认任务的设置暂不可修改" : "仅显示当前模型支持的选项"}</span>
              </div>
              <button className="director-context-close" type="button" onClick={() => closeComposerPanel("specs")} aria-label="关闭输出规格">
                <X size={16} aria-hidden="true" />
              </button>
            </header>
            {historicalRequestLocked ? (
              <dl className="director-readonly-grid">
                <div><dt>比例</dt><dd>{ratio || "未记录"}</dd></div>
                <div><dt>分辨率</dt><dd>{resolution || "未记录"}</dd></div>
                {modeUsesDuration(generationMode) && <div><dt>时长</dt><dd>{duration ? `${duration} 秒` : "未记录"}</dd></div>}
                <div><dt>作品数量</dt><dd>{outputCount ? `${outputCount} 个` : "未记录"}</dd></div>
              </dl>
            ) : activeCapability ? (
              <div className="director-spec-groups">
                {activeCapability.limits.aspectRatios.length > 1 && <div className="director-choice-group is-ratio">
                  <span>画面比例</span>
                  <div>
                    {activeCapability.limits.aspectRatios.map((item) => (
                      <button key={item} type="button" className={ratio === item ? "is-active" : ""} aria-pressed={ratio === item} onClick={() => setRatio(item)}>{item}</button>
                    ))}
                  </div>
                </div>}
                {activeCapability.limits.resolutions.length > 1 && <div className="director-choice-group is-resolution">
                  <span>分辨率</span>
                  <div>
                    {activeCapability.limits.resolutions.map((item) => (
                      <button key={item} type="button" className={resolution === item ? "is-active" : ""} aria-pressed={resolution === item} onClick={() => setResolution(item)}>{item}</button>
                    ))}
                  </div>
                </div>}
                {modeUsesDuration(generationMode) && activeCapability.limits.durations.length > 1 && (
                  <div className="director-choice-group is-duration">
                    <span>镜头时长</span>
                    <div>
                      {activeCapability.limits.durations.map((item) => (
                        <button key={item} type="button" className={duration === item ? "is-active" : ""} aria-pressed={duration === item} onClick={() => setDuration(item)}>{item} 秒</button>
                      ))}
                    </div>
                  </div>
                )}
                {activeCapability.limits.outputCounts.length > 1 && <div className="director-choice-group is-count">
                  <span>作品数量</span>
                  <div>
                    {activeCapability.limits.outputCounts.map((item) => (
                      <button key={item} type="button" className={outputCount === item ? "is-active" : ""} aria-pressed={outputCount === item} onClick={() => setOutputCount(item)}>{item} 个</button>
                    ))}
                  </div>
                </div>}
                {fixedSpecificationFields.length > 0 && (
                  <dl className="director-fixed-specs" aria-label="模型固定规格">
                    {fixedSpecificationFields.map((field) => (
                      <div key={field.key} data-specification={field.key}>
                        <dt>{field.label}</dt><dd>{field.values[0]}{field.unit}<span>（固定）</span></dd>
                      </div>
                    ))}
                  </dl>
                )}
              </div>
            ) : (
              <p className="inline-state is-error"><WarningCircle size={15} weight="fill" aria-hidden="true" />当前模型没有可用的输出规格。</p>
            )}
          </section>
        )}

        {composerPanel === "model" && (
          <section
            id="composer-model-panel"
            className="inspector-section director-context-panel director-model-panel"
            aria-labelledby="composer-model-title"
          >
            <header className="director-context-header">
              <div>
                <strong id="composer-model-title" tabIndex={-1} data-composer-panel-focus>生成引擎</strong>
                <span>{isPersonalWorkspace ? "可用模型可选择；接入中模型仅展示状态" : "仅显示当前企业可用的模型"}</span>
              </div>
              <button className="director-context-close" type="button" onClick={() => closeComposerPanel("model")} aria-label="关闭生成引擎">
                <X size={16} aria-hidden="true" />
              </button>
            </header>
            {modelsLoading ? (
              <div className="capability-skeleton" role="status" aria-label="正在加载模型能力"><span /><span /><span /></div>
            ) : (
              <>
                {modelPanelError && (
                  <p className="inline-state is-error"><WarningCircle size={15} weight="fill" aria-hidden="true" />{modelPanelError}</p>
                )}
                {(models.length > 0 || catalogOnlyModels.length > 0) && (
                  <div className="director-model-list" role="list">
                    {modelId && !models.some((item) => item.id === modelId) && (
                      <div className="director-model-option is-active is-locked" role="listitem">
                        <span className="director-model-mark"><ClockCounterClockwise size={18} aria-hidden="true" /></span>
                        <span><strong>历史授权模型</strong><small>仅用于确认原提交</small></span>
                        <Check size={16} aria-hidden="true" />
                      </div>
                    )}
                    {models.map((item) => {
                      const candidateModes = Object.keys(item.effectiveCapabilities?.modes ?? {});
                      const candidatePrice = composerModelPrice(item);
                      return (
                        <button
                          key={item.id}
                          type="button"
                          role="listitem"
                          className={`director-model-option ${modelId === item.id ? "is-active" : ""}`}
                          aria-pressed={modelId === item.id}
                          disabled={Boolean(pendingCreateRef.current)}
                          onClick={() => {
                            selectStudioModel(item.id);
                            closeComposerPanel("model");
                          }}
                        >
                          <span className="director-model-mark"><Sparkle size={18} weight="fill" aria-hidden="true" /></span>
                          <span>
                            <strong>{item.name}</strong>
                            <small>{candidateModes.map(generationModeLabel).join("、") || "没有可用模式"}</small>
                          </span>
                          {candidatePrice && <span className="director-model-price">{candidatePrice}</span>}
                          {modelId === item.id && <Check size={16} aria-hidden="true" />}
                        </button>
                      );
                    })}
                    {catalogOnlyModels.map((item) => {
                      const reason = item.available
                        ? "可用状态正在同步，请刷新后再选择。"
                        : item.unavailableReason;
                      const status = item.available
                        ? "暂不可选"
                        : PERSONAL_CATALOG_INTEGRATION_CODES.has(item.unavailableReasonCode)
                          ? "接入中"
                          : "暂不可用";
                      return (
                        <div
                          key={item.id}
                          className="director-model-option is-unavailable"
                          role="listitem"
                          aria-disabled="true"
                        >
                          <span className="director-model-mark"><ClockCounterClockwise size={18} aria-hidden="true" /></span>
                          <span>
                            <strong>{item.name}</strong>
                            <small>{item.billingMode === "per_second" ? "按秒计费" : "按条计费"}{item.capabilityVersion ? ` · 能力 v${item.capabilityVersion}` : ""}</small>
                            <small className="director-model-unavailable-reason">{reason}</small>
                          </span>
                          <span className="director-model-status">{status}</span>
                        </div>
                      );
                    })}
                  </div>
                )}
                {isPersonalWorkspace && personalModelCatalogLoading && (
                  <p className="director-model-list-note" role="status"><SpinnerGap className="spin" size={15} aria-hidden="true" />正在读取其他模型的接入进度…</p>
                )}
                {!modelPanelError && models.length === 0 && catalogOnlyModels.length === 0 && !personalModelCatalogLoading && (
                  <p className="inline-state is-error"><WarningCircle size={15} weight="fill" aria-hidden="true" />{isPersonalWorkspace ? "个人空间当前没有可用或正在接入的模型。" : "公司当前没有已授权模型，请联系管理员配置。"}</p>
                )}
              </>
            )}
          </section>
        )}

        {composerPanel === "readiness" && (
          <section
            id="composer-readiness-panel"
            className="inspector-section director-context-panel director-readiness-panel"
            aria-labelledby="composer-readiness-title"
          >
            <header className="director-context-header">
              <div>
                <strong id="composer-readiness-title">生成就绪</strong>
                <span>{DEMO_MODE ? "演示模式只展示操作流程" : "检查模型、额度、权限和当前设置"}</span>
              </div>
              <button className="director-context-close" type="button" onClick={() => closeComposerPanel("readiness")} aria-label="关闭生成就绪状态">
                <X size={16} aria-hidden="true" />
              </button>
            </header>
            {historicalRequestLocked ? (
              <dl className="director-readiness-evidence" tabIndex={-1} data-composer-panel-focus>
                <div><dt>确认方式</dt><dd>沿用原提交</dd></div>
                <div><dt>设置状态</dt><dd>已锁定</dd></div>
                <div><dt>请求边界</dt><dd>同一幂等请求</dd></div>
              </dl>
            ) : !activeCapability ? (
              <dl className="director-readiness-evidence is-unavailable" tabIndex={-1} data-composer-panel-focus>
                <div><dt>能力记录</dt><dd>当前选择未返回可用能力</dd></div>
                <div><dt>提交状态</dt><dd>已停止</dd></div>
              </dl>
            ) : (
              <>
                <dl
                  className={`director-readiness-evidence is-${generationReadiness.status}`}
                  tabIndex={-1}
                  data-composer-panel-focus
                >
                  <div>
                    <dt>服务端核验</dt>
                    <dd>{DEMO_MODE
                      ? "演示流程"
                      : !hasServerReadinessEvidence
                        ? "尚未取得"
                        : serverGenerationReadiness.ready
                          ? "已通过"
                          : "存在限制"}</dd>
                  </div>
                  <div><dt>当前模式</dt><dd>{generationModeLabel(generationMode)}</dd></div>
                  <div><dt>创作内容</dt><dd>{localReadinessBlockers.length > 0 ? `${localReadinessBlockers.length} 项待补全` : "已补全"}</dd></div>
                  <div>
                    <dt>核验时间</dt>
                    <dd>{readinessCheckedAtLabel
                      ? <time dateTime={model.readinessCheckedAt}>{readinessCheckedAtLabel}</time>
                      : "未提供"}</dd>
                  </div>
                </dl>
                {faceControlAvailable && (
                  <label className="director-face-control">
                    <span><strong>启用人脸处理</strong><small id="face-readiness-help">{DEMO_MODE ? "演示模式只展示该选项，不会调用真实人物库。" : "开启后会检查人物库权限；关闭时不影响普通生成。"}</small></span>
                    <input
                      type="checkbox"
                      checked={faceEnabled}
                      aria-describedby="face-readiness-help"
                      onChange={(event) => setFaceEnabled(event.target.checked)}
                    />
                  </label>
                )}
                {readinessEvidenceReasons.length > 0 && (
                  <ul className="director-readiness-blockers" aria-label="其他待处理项">
                    {readinessEvidenceReasons.map((reason) => (
                      <li key={reason}><WarningCircle size={16} weight="fill" aria-hidden="true" /><span>{reason}</span></li>
                    ))}
                  </ul>
                )}
              </>
            )}
            {historicalFaceVisible && historicalRequestLocked && (
              <p className="live-mode-note">历史提交的人脸处理：{faceEnabled ? "已启用" : "已关闭"}{faceEnabled && !faceControlAvailable ? "；当前能力已不支持，仅按原提交确认。" : "。"}</p>
            )}
          </section>
        )}
      </div>

      <div className="inspector-actions director-execution-spine">
        <div className="director-execution-row">
          <div className="composer-mobile-settings-row" aria-label="创作设置摘要">
            {hasVisibleSpecifications && <button type="button" data-composer-panel-trigger="specs" className={composerPanel === "specs" ? "is-active" : ""} aria-controls="composer-specs-panel" aria-expanded={composerPanel === "specs"} onClick={(event) => toggleComposerPanelFromTrigger(event, "specs")}>
              <SlidersHorizontal size={16} aria-hidden="true" />
              <span><small>创作设置</small><strong>{composerSpecSummary}</strong></span>
              <CaretRight size={14} aria-hidden="true" />
            </button>}
            <button type="button" data-composer-panel-trigger="readiness" data-readiness={generationReadiness.status} className={composerPanel === "readiness" ? "is-active" : ""} aria-controls="composer-readiness-panel" aria-expanded={composerPanel === "readiness"} aria-label={`生成就绪：${readinessHeadline}`} onClick={(event) => toggleComposerPanelFromTrigger(event, "readiness")}>
              {generationReadiness.ready ? <CheckCircle size={16} weight="fill" aria-hidden="true" /> : <WarningCircle size={16} weight="fill" aria-hidden="true" />}
              <span><span className="readiness-label-prefix">生成</span>就绪</span>
            </button>
          </div>
          <div className="director-panel-triggers" aria-label="创作上下文">
            {hasVisibleMediaInputs && (
              <button
                type="button"
                data-composer-panel-trigger="references"
                className={composerPanel === "references" ? "is-active" : ""}
                aria-controls="composer-references-panel"
                aria-expanded={composerPanel === "references"}
                onClick={(event) => toggleComposerPanelFromTrigger(event, "references")}
              >
                <Images size={16} aria-hidden="true" />
                <span>素材</span>
                <strong>{composerReferenceCount}</strong>
              </button>
            )}
            {hasVisibleSpecifications && <button
              id="composer-specs-trigger"
              type="button"
              data-composer-panel-trigger="specs"
              className={composerPanel === "specs" ? "is-active" : ""}
              aria-controls="composer-specs-panel"
              aria-expanded={composerPanel === "specs"}
              onClick={(event) => toggleComposerPanelFromTrigger(event, "specs")}
            >
              <SlidersHorizontal size={16} aria-hidden="true" />
              <span>{composerSpecSummary}</span>
            </button>}
            <button
              id="composer-readiness-trigger"
              type="button"
              data-composer-panel-trigger="readiness"
              data-readiness={generationReadiness.status}
              className={composerPanel === "readiness" ? "is-active" : ""}
              aria-controls="composer-readiness-panel"
              aria-expanded={composerPanel === "readiness"}
              aria-label={`生成就绪：${readinessHeadline}`}
              onClick={(event) => toggleComposerPanelFromTrigger(event, "readiness")}
            >
              {generationReadiness.ready
                ? <CheckCircle size={16} weight="fill" aria-hidden="true" />
                : <WarningCircle size={16} weight="fill" aria-hidden="true" />}
              <span><span className="readiness-label-prefix">生成</span>就绪</span>
            </button>
          </div>
          <div className={`director-run-controls ${showCostPreview ? "has-cost" : "has-no-cost"}`}>
            <button
              id="model"
              className={`director-model-trigger ${composerPanel === "model" ? "is-active" : ""}`}
              type="button"
              data-composer-panel-trigger="model"
              aria-controls="composer-model-panel"
              aria-expanded={composerPanel === "model"}
              disabled={modelsLoading || (!isPersonalWorkspace && models.length === 0 && !pendingCreateRef.current)}
              onClick={(event) => toggleComposerPanelFromTrigger(event, "model")}
            >
              <Sparkle size={16} weight="fill" aria-hidden="true" />
              <span><small>模型{outputCount ? ` · ${outputCount} 个结果` : ""}</small><strong>{modelTriggerLabel}</strong></span>
              <CaretDown size={14} aria-hidden="true" />
            </button>
            {showCostPreview && (
              <div className="cost-row">
                <span>预计消耗</span>
                <strong>{costLabel}</strong>
              </div>
            )}
            <button
              className="generate-button"
              type="button"
              onClick={startGeneration}
              disabled={composerSubmitState.disabled}
              aria-describedby={composerSubmitMessageId}
            >
              {["submitting", "uploading"].includes(composerSubmitState.code) || taskStageIsActive ? (
                <SpinnerGap className="spin" size={21} aria-hidden="true" />
              ) : (
                <Play size={19} weight="fill" aria-hidden="true" />
              )}
              <span className="generate-button-label">{!isQuick && ["ready", "prompt_error", "form_error"].includes(composerSubmitState.code) ? (variant === "console" ? "生成当前镜头" : variant === "notebook" ? "生成这一场" : "生成此节点") : composerSubmitState.label}</span>
              {showCostPreview && (
                <small className="generate-button-mobile-cost" aria-hidden="true">{costLabel}</small>
              )}
            </button>
          </div>
        </div>
        {composerSubmitState.message ? (
          <p
            id="composer-submit-message"
            className={`composer-submit-status is-${composerSubmitState.tone}`}
            role={composerSubmitState.tone === "danger" ? "alert" : "status"}
          >
            <WarningCircle size={15} weight="fill" aria-hidden="true" />
            <span>{composerSubmitState.message}</span>
          </p>
        ) : (
          <p className="director-accounting-note">
            {isPersonalWorkspace
              ? "失败不扣积分；个人余额不与企业钱包混用"
              : "企业共享积分钱包独立结算；作品保存成功才扣积分，失败不扣积分"}
          </p>
        )}
      </div>
    </aside>
  );
}
