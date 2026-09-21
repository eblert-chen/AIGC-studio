import React, { useMemo, useState } from "react";
import {
  GENERATION_MODES,
  defaultEditorCapability,
  resolveEffectiveCapabilities,
} from "../../modelCapabilities.js";

export function CapabilityEditorFields({ model }) {
  const initial = useMemo(() => resolveEffectiveCapabilities(model ?? {}), [model]);
  const initialModeIds = Object.keys(initial.modes);
  const [selectedModes, setSelectedModes] = useState(
    initialModeIds.length ? initialModeIds : ["text_to_video"],
  );
  const values = useMemo(() => {
    const result = {};
    GENERATION_MODES.forEach(({ id }) => {
      result[id] = initial.modes[id] ?? defaultEditorCapability(id);
    });
    return result;
  }, [initial]);

  const toggleMode = (id, checked) => {
    setSelectedModes((current) => (
      checked
        ? [...new Set([...current, id])]
        : current.filter((item) => item !== id)
    ));
  };

  return (
    <fieldset className="capability-editor">
      <legend>生成能力</legend>
      <p className="control-form-help capability-editor-intro">
        每种模式独立声明输入数量和可选参数。保存会写入一个版本化的标准能力配置。
      </p>
      <div className="capability-mode-picker" role="group" aria-label="启用的生成模式">
        {GENERATION_MODES.map(({ id, label }) => (
          <label className="control-check is-compact" key={id}>
            <input
              name="capabilityModes"
              type="checkbox"
              value={id}
              checked={selectedModes.includes(id)}
              onChange={(event) => toggleMode(id, event.target.checked)}
            />
            <span><strong>{label}</strong><small>{id}</small></span>
          </label>
        ))}
      </div>

      <div className="capability-mode-list">
        {GENERATION_MODES.filter(({ id }) => selectedModes.includes(id)).map(({ id, label, requiredMedia }) => {
          const capability = values[id];
          const prefix = `cap.${id}`;
          return (
            <section className="capability-mode-panel" key={id} aria-labelledby={`capability-${id}`}>
              <header>
                <div>
                  <strong id={`capability-${id}`}>{label}</strong>
                  <small>{requiredMedia === "image" ? "至少 1 张图片" : requiredMedia === "video" ? "至少 1 个视频" : "可使用纯文本"}</small>
                </div>
                <code>{id}</code>
              </header>

              <div className="control-form-grid capability-limit-grid">
                <label><span>图片上限</span><input name={`${prefix}.maxImages`} type="number" min={requiredMedia === "image" ? 1 : 0} max="15" required defaultValue={capability.limits.maxImages} /></label>
                <label><span>视频上限</span><input name={`${prefix}.maxVideos`} type="number" min={requiredMedia === "video" ? 1 : 0} max="15" required defaultValue={capability.limits.maxVideos} /></label>
                <label><span>音频上限</span><input name={`${prefix}.maxAudio`} type="number" min="0" max="15" required defaultValue={capability.limits.maxAudio} /></label>
              </div>

              <div className="control-form-grid">
                <label><span>时长选项（秒，最大 3600）</span><input name={`${prefix}.durations`} required placeholder="5, 10" defaultValue={capability.limits.durations.join(", ")} /></label>
                <label><span>提示词上限</span><input name={`${prefix}.maxPromptLength`} type="number" min="1" max="10000" required defaultValue={capability.limits.maxPromptLength} /></label>
              </div>

              <div className="control-form-grid">
                <label><span>画面比例</span><input name={`${prefix}.aspectRatios`} required placeholder="16:9, 9:16, 1:1" defaultValue={capability.limits.aspectRatios.join(", ")} /></label>
                <label><span>分辨率</span><input name={`${prefix}.resolutions`} required placeholder="720p, 1080p, 4K" defaultValue={capability.limits.resolutions.join(", ")} /></label>
              </div>

              <fieldset className="capability-option-group">
                <legend>单次产物数（1-16）</legend>
                <div className="capability-option-row">
                  {Array.from({ length: 16 }, (_, index) => index + 1).map((value) => (
                    <label className="control-check is-compact" key={value}>
                      <input name={`${prefix}.outputCounts`} type="checkbox" value={value} defaultChecked={capability.limits.outputCounts.includes(value)} />
                      <span><strong>{value} 个</strong></span>
                    </label>
                  ))}
                </div>
              </fieldset>

              <label className="capability-face-toggle">
                <input name={`${prefix}.supportsFace`} type="checkbox" defaultChecked={capability.supportsFace} />
                <span><strong>支持人脸能力</strong><small>制作台会在此模式下显示人脸开关。</small></span>
              </label>
              <label>
                <span>始终需要的功能资源 Key</span>
                <input name={`${prefix}.requiredResourceKeys`} placeholder="例如：face.library" defaultValue={capability.requiredResourceKeys.join(", ")} />
              </label>
              <label>
                <span>启用人脸时需要的资源 Key</span>
                <input
                  name={`${prefix}.faceRequiredResourceKeys`}
                  placeholder="例如：face.library"
                  defaultValue={capability.conditionalRequiredResourceKeys?.faceEnabled?.join(", ") ?? ""}
                />
                <small>仅在人脸开关打开时校验；关闭人脸不会被这项授权阻止。</small>
              </label>
            </section>
          );
        })}
      </div>
      {!selectedModes.length && (
        <p className="control-drawer-error" role="alert">请至少启用一种生成模式。</p>
      )}
    </fieldset>
  );
}
