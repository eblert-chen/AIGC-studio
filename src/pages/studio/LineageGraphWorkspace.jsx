import { useEffect, useMemo, useRef, useState } from "react";
import {
  Archive,
  ArrowLeft,
  ArrowRight,
  CheckCircle,
  CornersOut,
  CrosshairSimple,
  FilmSlate,
  FolderPlus,
  GitBranch,
  Minus,
  PencilSimple,
  Plus,
  Queue,
  X,
} from "@phosphor-icons/react";
import {
  Background,
  BackgroundVariant,
  Handle,
  Panel,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useNodesState,
  useReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

export const LINEAGE_BRANCH_OPTIONS = [
  { id: "composition", label: "改构图" },
  { id: "performance", label: "改表演" },
  { id: "camera", label: "改镜头" },
  { id: "continuity", label: "修连续性" },
  { id: "style", label: "改风格" },
  { id: "model", label: "换模型" },
  { id: "custom", label: "其他方向" },
];

const BRANCH_LABELS = Object.fromEntries(LINEAGE_BRANCH_OPTIONS.map((item) => [item.id, item.label]));
const DIRECTION_WIDTH = 236;
const DIRECTION_HEIGHT = 224;
const HORIZONTAL_GAP = 150;
const VERTICAL_GAP = 72;

function taskIdForTake(take) {
  return String(take?.taskId || take?.task?.id || "");
}

function artifactIdForTake(take) {
  return String(take?.artifactId || take?.artifact?.artifact_id || "");
}

function takeMatches(take, taskId, artifactId = "") {
  return taskIdForTake(take) === String(taskId)
    && (!artifactId || artifactIdForTake(take) === String(artifactId));
}

export function selectedLineageTake(entry, exactOnly = false) {
  const takes = entry?.takes || [];
  const selected = takes.find((take) => takeMatches(take, entry?.selectedTaskId, entry?.selectedArtifactId));
  return selected || (exactOnly ? null : takes.at(-1)) || null;
}

function branchSourceTake(entry, entries) {
  const parent = entries.find((candidate) => candidate.id === entry?.parentId);
  return parent?.takes?.find((take) => takeMatches(take, entry?.sourceTaskId, entry?.sourceArtifactId)) || null;
}

function canonicalTake(shot, entries) {
  const direction = entries.find((entry) => entry.id === shot?.canonicalEntryId);
  return direction?.takes?.find((take) => takeMatches(take, shot?.canonicalTaskId, shot?.canonicalArtifactId)) || null;
}

function formatDraftValue(value) {
  if (value === null || value === undefined || value === "") return "未设置";
  if (typeof value === "boolean") return value ? "开启" : "关闭";
  return String(value);
}

function referenceIds(draft) {
  return Object.values(draft?.files || {}).flat().map((asset) => asset.asset_id).sort().join(", ");
}

function formatTakeSources(sources) {
  return (sources || []).flatMap((source) => {
    if (typeof source === "string") return source.trim() ? [source.trim()] : [];
    if (!source || typeof source !== "object") return [];
    const label = String(source.label || source.type || "参考素材").trim();
    const count = Number(source.count || 0);
    return [count > 0 ? `${label} ${count}` : label];
  }).join("、");
}

export function lineageDraftDelta(entry) {
  if (!entry?.sourceDraft) return [];
  const source = entry.sourceDraft;
  const current = entry.draft || {};
  const fields = [
    ["制作说明", source.prompt, current.prompt],
    ["模型", source.modelId, current.modelId],
    ["创作方式", source.generationMode, current.generationMode],
    ["比例", source.ratio, current.ratio],
    ["画质", source.resolution, current.resolution],
    ["时长", source.duration, current.duration],
    ["生成数量", source.outputCount, current.outputCount],
    ["人脸能力", source.faceEnabled, current.faceEnabled],
    ["参考素材", referenceIds(source), referenceIds(current)],
  ];
  return fields
    .filter(([, before, after]) => JSON.stringify(before ?? "") !== JSON.stringify(after ?? ""))
    .map(([label, before, after]) => ({ label, before: formatDraftValue(before), after: formatDraftValue(after) }));
}

function compareTakeFacts(left, right) {
  const fields = [
    ["制作说明", left?.prompt, right?.prompt],
    ["模型", left?.modelName, right?.modelName],
    ["画面比例", left?.ratio, right?.ratio],
    ["画质", left?.resolution, right?.resolution],
    ["时长", left?.durationLabel, right?.durationLabel],
    ["参考素材", formatTakeSources(left?.sources), formatTakeSources(right?.sources)],
  ];
  return fields
    .filter(([, before, after]) => formatDraftValue(before) !== formatDraftValue(after))
    .map(([label, before, after]) => ({ label, before: formatDraftValue(before), after: formatDraftValue(after) }));
}

function verifiedParent(entry, byId) {
  if (entry?.relation !== "lineage" || !entry.sourceTaskId || !entry.sourceArtifactId) return null;
  const parent = byId.get(entry.parentId);
  return parent?.shotId === entry.shotId ? parent : null;
}

export function canonicalDirectionPath(entries, shot) {
  const byId = new Map(entries.map((entry) => [entry.id, entry]));
  const path = new Set();
  let current = byId.get(shot?.canonicalEntryId);
  while (current && !path.has(current.id)) {
    path.add(current.id);
    current = verifiedParent(current, byId);
  }
  return path;
}

export function layoutLineageDirections(entries, shotId) {
  const directions = entries
    .filter((entry) => entry.kind === "canvas" && entry.shotId === shotId)
    .sort((left, right) => left.order - right.order);
  const byId = new Map(directions.map((entry) => [entry.id, entry]));
  const children = new Map(directions.map((entry) => [entry.id, []]));
  const roots = [];
  for (const entry of directions) {
    const parent = verifiedParent(entry, byId);
    if (parent) children.get(parent.id).push(entry);
    else roots.push(entry);
  }
  for (const values of children.values()) values.sort((left, right) => left.order - right.order);

  const positions = new Map();
  let leaf = 0;
  const place = (entry, depth) => {
    const descendants = children.get(entry.id) || [];
    const childYs = descendants.map((child) => place(child, depth + 1));
    const automaticY = childYs.length
      ? childYs.reduce((sum, value) => sum + value, 0) / childYs.length
      : 64 + leaf++ * (DIRECTION_HEIGHT + VERTICAL_GAP);
    const manual = entry.x !== 0 || entry.y !== 0;
    const position = manual
      ? { x: Math.max(300, entry.x), y: Math.max(28, entry.y) }
      : { x: 340 + depth * (DIRECTION_WIDTH + HORIZONTAL_GAP), y: automaticY };
    positions.set(entry.id, position);
    return position.y;
  };
  roots.forEach((entry) => place(entry, 0));
  return { directions, byId, positions, roots };
}

function LineageShotNode({ data }) {
  const mainlineLabel = data.mainlineState === "available"
    ? "导演主线已选"
    : data.mainlineState === "unavailable"
      ? "主线证据待读取"
      : "等待拍板";
  return (
    <article className="lineage-shot-node">
      <span className="lineage-shot-node-type">镜头</span>
      <strong>{data.shot.title}</strong>
      <p>{data.intent || "写下这个镜头的第一个方向，让谱系从这里生长。"}</p>
      <span className={data.mainlineState !== "none" ? "is-mainline" : ""}>
        {mainlineLabel}
      </span>
      <Handle type="source" position={Position.Right} isConnectable={false} />
    </article>
  );
}

function LineageDirectionNode({ data, selected }) {
  const {
    entry,
    take,
    isCanonicalTake,
    hasCanonicalRecord,
    canonicalEvidenceAvailable,
    isOnMainline,
    renderPreview,
    onRestore,
    locked,
  } = data;
  const takeCount = entry.takes?.length || 0;
  const stateLabel = isCanonicalTake
    ? "导演主线"
    : hasCanonicalRecord && !canonicalEvidenceAvailable
      ? "主线证据待读取"
      : hasCanonicalRecord
        ? "包含导演主线"
    : entry.archived
      ? "已归档"
      : take?.statusLabel || "方向草稿";
  return (
    <article
      className={[
        "lineage-direction-node",
        selected ? "is-selected" : "",
        isCanonicalTake ? "is-canonical" : "",
        isOnMainline ? "is-on-mainline" : "",
        entry.archived ? "is-archived" : "",
      ].filter(Boolean).join(" ")}
    >
      <Handle type="target" position={Position.Left} isConnectable={false} />
      <div className={["lineage-take-stack", takeCount > 1 ? "has-stack" : ""].join(" ")}>
        {takeCount > 2 && <span className="lineage-stack-layer is-back" aria-hidden="true" />}
        {takeCount > 1 && <span className="lineage-stack-layer" aria-hidden="true" />}
        <div className="lineage-node-media">
          {take ? renderPreview(take, true) : (
            <div className="lineage-draft-media">
              <FilmSlate size={24} aria-hidden="true" />
              <span>等待首个 Take</span>
            </div>
          )}
          <span className={["lineage-node-state", isCanonicalTake ? "is-mainline" : ""].join(" ")}>
            {stateLabel}
          </span>
        </div>
      </div>
      <div className="lineage-node-copy">
        <strong>{entry.title || "未命名方向"}</strong>
        <span><Queue size={14} aria-hidden="true" />{takeCount ? takeCount + " 个 Take" : "尚未生成"}</span>
      </div>
      {entry.relation === "lineage" ? (
        <p className="lineage-node-origin">
          <GitBranch size={14} aria-hidden="true" />
          {BRANCH_LABELS[entry.branchKind] || "新方向"}：{entry.branchReason}
        </p>
      ) : entry.relation === "settings" ? (
        <p className="lineage-node-origin is-legacy">旧分支，精确来源未记录</p>
      ) : null}
      {entry.archived && (
        <button
          className="lineage-node-restore nodrag nopan"
          type="button"
          disabled={locked}
          onClick={(event) => {
            event.stopPropagation();
            onRestore?.();
          }}
        >恢复支线</button>
      )}
      <Handle type="source" position={Position.Right} isConnectable={false} />
    </article>
  );
}

const LINEAGE_NODE_TYPES = {
  lineageShot: LineageShotNode,
  lineageDirection: LineageDirectionNode,
};

function LineageCanvasTools({ canonicalNodeId }) {
  const { fitView, getNode, setCenter, zoomIn, zoomOut } = useReactFlow();
  const focusMainline = () => {
    const node = canonicalNodeId ? getNode(canonicalNodeId) : null;
    if (!node) {
      fitView({ padding: 0.24, duration: 220, maxZoom: 1 });
      return;
    }
    const width = node.measured?.width || DIRECTION_WIDTH;
    const height = node.measured?.height || DIRECTION_HEIGHT;
    setCenter(node.position.x + width / 2, node.position.y + height / 2, { zoom: 1, duration: 220 });
  };
  return (
    <Panel className="lineage-canvas-tools" position="top-left">
      <button type="button" onClick={() => zoomIn({ duration: 160 })} title="放大画布" aria-label="放大画布"><Plus size={17} /></button>
      <button type="button" onClick={() => zoomOut({ duration: 160 })} title="缩小画布" aria-label="缩小画布"><Minus size={17} /></button>
      <button type="button" onClick={() => fitView({ padding: 0.24, duration: 220, maxZoom: 1 })} title="适配全部方向" aria-label="适配全部方向"><CornersOut size={17} /></button>
      <button type="button" onClick={focusMainline} title="回到导演主线" aria-label="回到导演主线"><CrosshairSimple size={17} /></button>
    </Panel>
  );
}

function directionNumber(entry, entries) {
  const ordered = entries
    .filter((candidate) => candidate.kind === "canvas" && candidate.shotId === entry?.shotId)
    .sort((left, right) => left.order - right.order);
  return Math.max(1, ordered.findIndex((candidate) => candidate.id === entry?.id) + 1);
}

function takeNumber(take, entry) {
  return Math.max(1, (entry?.takes || []).findIndex((candidate) => candidate.id === take?.id) + 1);
}

function exactSuccessfulTake(take) {
  return Boolean(
    take
    && take.status === "succeeded"
    && take.artifact
    && taskIdForTake(take)
    && artifactIdForTake(take),
  );
}

function branchAvailability(take, locked) {
  if (locked) return "当前任务正在提交或素材仍在上传，请稍后再开方向。";
  if (!take) return "先在这个方向生成并选择一个 Take。";
  if (take.status !== "succeeded") return "只有生成成功的 Take 可以成为新方向的起点。";
  if (!take.artifact) return "这个 Take 暂无可用产物。";
  if (!taskIdForTake(take) || !artifactIdForTake(take)) return "这个旧 Take 缺少平台任务或产物标识，不能建立来源线。";
  return "";
}

function shortBranchLabel(entry) {
  const kind = BRANCH_LABELS[entry.branchKind] || "新方向";
  const reason = String(entry.branchReason || "").trim();
  return reason ? kind + " · " + (reason.length > 18 ? reason.slice(0, 18) + "…" : reason) : kind;
}

function EditableShotTitle({ shot, controller }) {
  const [value, setValue] = useState(shot?.title || "");
  useEffect(() => setValue(shot?.title || ""), [shot?.id, shot?.title]);
  if (!shot) return null;
  const commit = () => {
    const next = value.trim();
    if (!next) {
      setValue(shot.title);
      return;
    }
    if (next !== shot.title) controller?.onRenameShot?.(shot.id, next);
  };
  return (
    <label className="lineage-shot-title">
      <span className="visually-hidden">当前镜头名称</span>
      <input
        value={value}
        maxLength={200}
        disabled={controller?.locked}
        onChange={(event) => setValue(event.target.value)}
        onBlur={commit}
        onKeyDown={(event) => {
          if (event.key === "Enter") event.currentTarget.blur();
          if (event.key === "Escape") {
            event.preventDefault();
            setValue(shot.title);
            event.currentTarget.blur();
          }
        }}
      />
      <PencilSimple size={14} aria-hidden="true" />
    </label>
  );
}

function EditableDirectionTitle({ entry, controller }) {
  const [value, setValue] = useState(entry?.title || "");
  useEffect(() => setValue(entry?.title || ""), [entry?.id, entry?.title]);
  if (!entry) return null;
  const commit = () => {
    const next = value.trim();
    if (!next) {
      setValue(entry.title);
      return;
    }
    if (next !== entry.title) controller?.onRenameEntry?.(entry.id, next);
  };
  return (
    <label className="lineage-direction-title">
      <span className="visually-hidden">方向名称</span>
      <input
        value={value}
        maxLength={200}
        disabled={controller?.locked}
        onChange={(event) => setValue(event.target.value)}
        onBlur={commit}
        onKeyDown={(event) => {
          if (event.key === "Enter") event.currentTarget.blur();
          if (event.key === "Escape") {
            event.preventDefault();
            setValue(entry.title);
            event.currentTarget.blur();
          }
        }}
      />
      <PencilSimple size={14} aria-hidden="true" />
    </label>
  );
}

function TakeRail({ entry, shot, controller, renderPreview }) {
  const takes = entry?.takes || [];
  if (!takes.length) {
    return (
      <div className="lineage-no-takes">
        <FilmSlate size={19} aria-hidden="true" />
        <span>还没有 Take。打开当前方向，写下镜头意图后开始生成。</span>
      </div>
    );
  }
  return (
    <div className="lineage-take-rail" role="list" aria-label="当前方向的 Take">
      {takes.map((take, index) => {
        const taskId = taskIdForTake(take);
        const artifactId = artifactIdForTake(take);
        const exact = exactSuccessfulTake(take);
        const selected = takeMatches(take, entry.selectedTaskId, entry.selectedArtifactId);
        const mainline = shot?.canonicalEntryId === entry.id
          && shot.canonicalTaskId === taskId
          && shot.canonicalArtifactId === artifactId;
        return (
          <div role="listitem" key={take.id || taskId + ":" + artifactId + ":" + index}>
            <button
              type="button"
              className={["lineage-take-card", selected ? "is-selected" : "", mainline ? "is-mainline" : ""].filter(Boolean).join(" ")}
              disabled={!exact}
              aria-pressed={selected}
              aria-label={`选择 Take ${index + 1}，${mainline ? "当前导演主线" : take.statusLabel}`}
              title={exact ? "选中这个精确 Take" : "任务成功并返回规范产物后才可选用"}
              onClick={() => exact && controller?.onSelectVersion?.(entry.id, taskId, artifactId)}
            >
              <span className="lineage-take-thumb">{renderPreview(take, true)}</span>
              <span className="lineage-take-meta">
                <strong>Take {String(index + 1).padStart(2, "0")}</strong>
                <small>{mainline ? "导演主线" : take.statusLabel}</small>
              </span>
            </button>
          </div>
        );
      })}
    </div>
  );
}

function BranchComposer({ entry, selectedTake, controller, onCancel, onCreated }) {
  const [branchKind, setBranchKind] = useState("composition");
  const [reason, setReason] = useState("");
  const optionRefs = useRef([]);
  const unavailable = branchAvailability(selectedTake, controller?.locked);
  const submit = () => {
    const cleanReason = reason.trim();
    if (unavailable || !cleanReason) return;
    const label = BRANCH_LABELS[branchKind] || "新方向";
    const created = controller?.onBranchEntry?.(entry.id, {
      sourceTake: selectedTake,
      branchKind,
      branchReason: cleanReason,
      title: label + "：" + cleanReason.slice(0, 34),
    });
    if (created) onCreated?.();
  };
  return (
    <section className="lineage-branch-composer" aria-labelledby="lineage-branch-title">
      <div>
        <span className="lineage-eyebrow">从精确 Take 分叉</span>
        <h4 id="lineage-branch-title">这次为什么换一个方向？</h4>
      </div>
      <div className="lineage-branch-kinds" role="radiogroup" aria-label="方向变化类型">
        {LINEAGE_BRANCH_OPTIONS.map((option, index) => (
          <button
            type="button"
            role="radio"
            aria-checked={branchKind === option.id}
            className={branchKind === option.id ? "is-selected" : ""}
            key={option.id}
            ref={(node) => { optionRefs.current[index] = node; }}
            tabIndex={branchKind === option.id ? 0 : -1}
            onClick={() => setBranchKind(option.id)}
            onKeyDown={(event) => {
              const length = LINEAGE_BRANCH_OPTIONS.length;
              let next = null;
              if (["ArrowRight", "ArrowDown"].includes(event.key)) next = (index + 1) % length;
              if (["ArrowLeft", "ArrowUp"].includes(event.key)) next = (index - 1 + length) % length;
              if (event.key === "Home") next = 0;
              if (event.key === "End") next = length - 1;
              if (next === null) return;
              event.preventDefault();
              setBranchKind(LINEAGE_BRANCH_OPTIONS[next].id);
              optionRefs.current[next]?.focus();
            }}
          >{option.label}</button>
        ))}
      </div>
      <label className="lineage-branch-reason">
        <span>导演意图</span>
        <input
          value={reason}
          maxLength={500}
          placeholder="例如：保留角色动作，把机位推近到胸像景别"
          onChange={(event) => setReason(event.target.value)}
          onKeyDown={(event) => { if (event.key === "Enter") submit(); }}
          autoFocus
        />
      </label>
      {unavailable && <p className="lineage-action-reason" role="status">{unavailable}</p>}
      <div className="lineage-branch-actions">
        <button type="button" onClick={onCancel}>取消</button>
        <button className="is-primary" type="button" disabled={Boolean(unavailable) || !reason.trim()} onClick={submit}>
          <GitBranch size={16} aria-hidden="true" />建立方向
        </button>
      </div>
    </section>
  );
}

function DirectionInspector({
  entry,
  entries,
  shot,
  controller,
  renderPreview,
  renderResultActions,
  renderTransfers,
  onEdit,
  onCompare,
}) {
  const [branching, setBranching] = useState(false);
  useEffect(() => setBranching(false), [entry?.id]);
  if (!entry) return null;
  // The newest visible preview is never an implicit authoring decision. Every
  // branch, comparison and mainline action requires the persisted exact pair.
  const selectedTake = selectedLineageTake(entry, true);
  const sourceTake = branchSourceTake(entry, entries);
  const recordedCanonicalTake = canonicalTake(shot, entries);
  const delta = lineageDraftDelta(entry);
  const canCompare = exactSuccessfulTake(selectedTake)
    && exactSuccessfulTake(sourceTake)
    && (taskIdForTake(selectedTake) !== taskIdForTake(sourceTake)
      || artifactIdForTake(selectedTake) !== artifactIdForTake(sourceTake));
  const unavailable = branchAvailability(selectedTake, controller?.locked);
  const directionIndex = directionNumber(entry, entries);
  const selectedTaskId = taskIdForTake(selectedTake);
  const selectedArtifactId = artifactIdForTake(selectedTake);
  const selectedIsMainline = shot?.canonicalEntryId === entry.id
    && shot.canonicalTaskId === selectedTaskId
    && shot.canonicalArtifactId === selectedArtifactId;
  const directionOwnsRecordedMainline = shot?.canonicalEntryId === entry.id;
  if (branching) {
    return <BranchComposer entry={entry} selectedTake={selectedTake} controller={controller} onCancel={() => setBranching(false)} onCreated={() => setBranching(false)} />;
  }
  return (
    <section className="lineage-inspector" aria-labelledby="lineage-inspector-title">
      <div className="lineage-inspector-context">
        <span className="lineage-eyebrow">方向 {String(directionIndex).padStart(2, "0")}</span>
        <EditableDirectionTitle entry={entry} controller={controller} />
        {entry.relation === "lineage" ? (
          <p className="lineage-source-line">
            <GitBranch size={15} aria-hidden="true" />
            来源：方向 {String(directionNumber(entries.find((candidate) => candidate.id === entry.parentId), entries)).padStart(2, "0")}
            {sourceTake ? " / Take " + String(takeNumber(sourceTake, entries.find((candidate) => candidate.id === entry.parentId))).padStart(2, "0") : " / 来源暂不可读取"}
          </p>
        ) : entry.relation === "settings" ? (
          <p className="lineage-source-line is-warning">旧版记录未保存精确 Take，只展示方向，不绘制来源线。</p>
        ) : (
          <p className="lineage-source-line">镜头的独立创作起点</p>
        )}
        {entry.branchReason && <p className="lineage-intent"><strong>{BRANCH_LABELS[entry.branchKind] || "创作变化"}</strong>{entry.branchReason}</p>}
        {directionOwnsRecordedMainline && !recordedCanonicalTake && (
          <p className="lineage-source-line is-warning" role="status">
            导演主线已有精确记录，但当前账号暂时读不到对应任务或产物；图谱不会用其他 Take 冒充它。
          </p>
        )}
        {directionOwnsRecordedMainline && recordedCanonicalTake && !selectedIsMainline && (
          <p className="lineage-source-line">这个方向的另一个 Take 已被选为导演主线。</p>
        )}
        {delta.length > 0 && (
          <details className="lineage-delta">
            <summary>查看 {delta.length} 项设置变化</summary>
            <dl>{delta.map((item) => <div key={item.label}><dt>{item.label}</dt><dd>{item.before}<ArrowRight size={13} aria-hidden="true" />{item.after}</dd></div>)}</dl>
          </details>
        )}
      </div>
      <div className="lineage-inspector-takes">
        <div className="lineage-section-label"><span id="lineage-inspector-title">Take</span><small>同一方向内的生成结果</small></div>
        <TakeRail entry={entry} shot={shot} controller={controller} renderPreview={renderPreview} />
      </div>
      <div className="lineage-inspector-actions">
        <div>
          <button type="button" onClick={onEdit}><PencilSimple size={16} aria-hidden="true" />打开当前方向</button>
          <button type="button" disabled={!canCompare} onClick={() => canCompare && onCompare(sourceTake, selectedTake)} title={canCompare ? "对比来源 Take 与当前 Take" : "生成成功后可与来源 Take 对比"}>A/B 对比</button>
          <button type="button" disabled={Boolean(unavailable)} onClick={() => !unavailable && setBranching(true)}><GitBranch size={16} aria-hidden="true" />从此 Take 分叉</button>
          {entry.parentId && (
            <button type="button" disabled={controller?.locked} onClick={() => controller?.onArchiveEntry?.(entry.id, !entry.archived)}>
              <Archive size={16} aria-hidden="true" />{entry.archived ? "恢复支线" : "归档支线"}
            </button>
          )}
        </div>
        <div>
          {exactSuccessfulTake(selectedTake) && !selectedIsMainline && (
            <button
              type="button"
              className="is-mainline-action"
              disabled={controller?.locked || entry.archived}
              onClick={() => controller?.onSetCanonical?.(entry.id, selectedTaskId, selectedArtifactId)}
            ><CheckCircle size={17} aria-hidden="true" />设为导演主线</button>
          )}
          {selectedIsMainline && <span className="lineage-mainline-confirm"><CheckCircle size={17} weight="fill" aria-hidden="true" />这个 Take 是导演主线</span>}
        </div>
        {renderResultActions(selectedTake)}
        {renderTransfers()}
      </div>
    </section>
  );
}

function TakeCompare({
  left,
  right,
  leftEntry,
  rightEntry,
  shot,
  controller,
  renderPreview,
  onClose,
}) {
  const closeRef = useRef(null);
  useEffect(() => {
    closeRef.current?.focus();
    const closeOnEscape = (event) => { if (event.key === "Escape") onClose(); };
    globalThis.addEventListener?.("keydown", closeOnEscape);
    return () => globalThis.removeEventListener?.("keydown", closeOnEscape);
  }, [onClose]);
  const differences = compareTakeFacts(left, right);
  const sides = [
    { key: "left", label: "来源 Take", take: left, entry: leftEntry },
    { key: "right", label: "当前 Take", take: right, entry: rightEntry },
  ];
  return (
    <section className="lineage-compare" aria-labelledby="lineage-compare-title">
      <header>
        <div><span className="lineage-eyebrow">导演检视</span><h3 id="lineage-compare-title">A/B 对比</h3><p>只列出真正变化的内容，画面由你拍板。</p></div>
        <button ref={closeRef} type="button" onClick={onClose}><X size={18} aria-hidden="true" />返回图谱</button>
      </header>
      <div className="lineage-compare-grid">
        {sides.map((side) => {
          const isMainline = shot?.canonicalEntryId === side.entry?.id
            && shot?.canonicalTaskId === taskIdForTake(side.take)
            && shot?.canonicalArtifactId === artifactIdForTake(side.take);
          return (
            <article key={side.key}>
              <div className="lineage-compare-label"><span>{side.label}</span>{isMainline && <strong>导演主线</strong>}</div>
              <div className="lineage-compare-media">{renderPreview(side.take, false)}</div>
              <div className="lineage-compare-caption"><strong>{side.entry?.title}</strong><span>Take {String(takeNumber(side.take, side.entry)).padStart(2, "0")}</span></div>
              {!isMainline && exactSuccessfulTake(side.take) && !side.entry?.archived && (
                <button type="button" disabled={controller?.locked} onClick={() => controller?.onSetCanonical?.(side.entry.id, taskIdForTake(side.take), artifactIdForTake(side.take))}>
                  <CheckCircle size={17} aria-hidden="true" />选这一版为导演主线
                </button>
              )}
            </article>
          );
        })}
      </div>
      <section className="lineage-compare-delta" aria-labelledby="lineage-compare-delta-title">
        <div><span className="lineage-eyebrow">差异清单</span><h4 id="lineage-compare-delta-title">这两版哪里不同</h4></div>
        {differences.length ? <dl>{differences.map((item) => <div key={item.label}><dt>{item.label}</dt><dd><span>{item.before}</span><ArrowRight size={14} aria-hidden="true" /><span>{item.after}</span></dd></div>)}</dl> : <p>可读取的创作设置没有差异，请直接比较画面与表演。</p>}
      </section>
    </section>
  );
}

function EmptyDirectionInspector({ controller, onEdit }) {
  return (
    <section className="lineage-empty-inspector">
      <div><span className="lineage-eyebrow">第一个方向</span><h3>先定义这个镜头想拍什么</h3><p>写下画面、动作和镜头意图。第一次生成会成为这个方向的首个 Take。</p></div>
      <button type="button" disabled={controller?.locked} onClick={onEdit}><PencilSimple size={17} aria-hidden="true" />打开方向编辑</button>
    </section>
  );
}

function LineageGraphInner({
  controller,
  renderEditor,
  renderPreview,
  renderResultActions,
  renderTransfers,
  renderHistoryImport,
  onBack,
}) {
  const allEntries = useMemo(() => [...(controller?.entries || [])].sort((left, right) => left.order - right.order), [controller?.entries]);
  const shots = useMemo(() => [...(controller?.state?.lineage?.shots || [])].sort((left, right) => left.order - right.order), [controller?.state?.lineage?.shots]);
  const activeShotId = controller?.state?.lineage?.activeShotId || allEntries.find((entry) => entry.id === controller?.activeEntryId)?.shotId || shots[0]?.id || "";
  const shot = shots.find((candidate) => candidate.id === activeShotId) || shots[0] || null;
  const [showArchived, setShowArchived] = useState(false);
  const [mode, setMode] = useState("browse");
  const [compare, setCompare] = useState(null);
  const [importOpen, setImportOpen] = useState(false);
  const restoreFocusRef = useRef(null);
  const drawerRestoreFocusRef = useRef(null);
  const drawerRef = useRef(null);
  const drawerTitleRef = useRef(null);
  const activeEntry = allEntries.find((entry) => entry.id === controller?.activeEntryId && entry.shotId === shot?.id)
    || allEntries.find((entry) => entry.shotId === shot?.id && !entry.archived)
    || null;
  const graphEntries = useMemo(
    () => allEntries.filter((entry) => entry.shotId === shot?.id && (showArchived || !entry.archived)),
    [allEntries, shot?.id, showArchived],
  );
  const { directions, byId, positions, roots } = useMemo(() => layoutLineageDirections(graphEntries, shot?.id), [graphEntries, shot?.id]);
  const mainlinePath = useMemo(() => canonicalDirectionPath(allEntries, shot), [allEntries, shot]);
  const canonicalNodeId = shot?.canonicalEntryId || "";
  const recordedCanonicalTake = useMemo(() => canonicalTake(shot, allEntries), [allEntries, shot]);
  const canonicalRecordExists = Boolean(shot?.canonicalEntryId && shot?.canonicalTaskId && shot?.canonicalArtifactId);
  const canonicalEvidenceAvailable = exactSuccessfulTake(recordedCanonicalTake);

  useEffect(() => {
    if (controller?.editing) setMode("edit");
    else if (mode === "edit") setMode("browse");
  }, [controller?.editing]);

  useEffect(() => {
    if (!activeEntry && graphEntries.length) controller?.onSelectEntry?.(graphEntries[0].id, { edit: false });
  }, [activeEntry?.id, graphEntries.length, shot?.id]);

  useEffect(() => {
    if (!importOpen) return undefined;
    const frame = globalThis.requestAnimationFrame?.(() => drawerTitleRef.current?.focus());
    const keepFocusInside = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setImportOpen(false);
        globalThis.setTimeout?.(() => drawerRestoreFocusRef.current?.focus?.(), 0);
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = [...(drawerRef.current?.querySelectorAll?.(
        'button:not(:disabled), input:not(:disabled), summary, [href], [tabindex]:not([tabindex="-1"])',
      ) || [])].filter((node) => node.getClientRects?.().length);
      if (!focusable.length) {
        event.preventDefault();
        drawerTitleRef.current?.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable.at(-1);
      if (event.shiftKey && [first, drawerTitleRef.current].includes(document.activeElement)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    globalThis.addEventListener?.("keydown", keepFocusInside);
    return () => {
      if (frame !== undefined) globalThis.cancelAnimationFrame?.(frame);
      globalThis.removeEventListener?.("keydown", keepFocusInside);
    };
  }, [importOpen]);

  const graphNodes = useMemo(() => {
    if (!shot) return [];
    const rootYs = roots.map((entry) => positions.get(entry.id)?.y || 64);
    const rootY = rootYs.length ? rootYs.reduce((sum, value) => sum + value, 0) / rootYs.length + 24 : 116;
    return [
      {
        id: "shot:" + shot.id,
        type: "lineageShot",
        position: { x: 28, y: Math.max(56, rootY) },
          draggable: false,
          selectable: false,
          deletable: false,
        focusable: true,
        ariaLabel: `${shot.title}，${canonicalRecordExists ? (canonicalEvidenceAvailable ? "导演主线可读取" : "导演主线证据暂不可读") : "尚未选择导演主线"}`,
        data: {
          shot,
          mainlineState: canonicalRecordExists
            ? (canonicalEvidenceAvailable ? "available" : "unavailable")
            : "none",
          intent: roots[0]?.draft?.prompt || "",
        },
      },
      ...directions.map((entry) => {
        const take = selectedLineageTake(entry);
        const exactSelectedTake = selectedLineageTake(entry, true);
        const taskId = taskIdForTake(exactSelectedTake);
        const artifactId = artifactIdForTake(exactSelectedTake);
        const hasCanonicalRecord = shot.canonicalEntryId === entry.id;
        return {
          id: entry.id,
          type: "lineageDirection",
          position: positions.get(entry.id),
          selected: entry.id === activeEntry?.id,
          draggable: !controller?.locked && !entry.archived,
          deletable: false,
          focusable: true,
          ariaLabel: `${entry.title || "未命名方向"}，${entry.archived ? "已归档，可恢复" : `${entry.takes?.length || 0} 个 Take`}`,
          data: {
            entry,
            take,
            renderPreview,
            isOnMainline: mainlinePath.has(entry.id),
            hasCanonicalRecord,
            canonicalEvidenceAvailable: hasCanonicalRecord && canonicalEvidenceAvailable,
            isCanonicalTake: hasCanonicalRecord
              && shot.canonicalTaskId === taskId
              && shot.canonicalArtifactId === artifactId,
            locked: controller?.locked,
            onRestore: () => controller?.onArchiveEntry?.(entry.id, false),
          },
        };
      }),
    ];
  }, [shot, roots, positions, directions, activeEntry?.id, renderPreview, mainlinePath,
    canonicalRecordExists, canonicalEvidenceAvailable, controller?.locked, controller?.onArchiveEntry]);
  const [nodes, setNodes, onNodesChange] = useNodesState(graphNodes);
  useEffect(() => setNodes(graphNodes), [graphNodes, setNodes]);

  const graphEdges = useMemo(() => {
    if (!shot) return [];
    const edges = [];
    for (const entry of directions) {
      const parent = verifiedParent(entry, byId);
      const onMainline = mainlinePath.has(entry.id) && (!parent || mainlinePath.has(parent.id));
      if (parent) {
        edges.push({
          id: "lineage:" + parent.id + ":" + entry.id,
          source: parent.id,
          target: entry.id,
          label: shortBranchLabel(entry),
          className: onMainline ? "is-mainline" : "",
          type: "bezier",
          deletable: false,
          ariaLabel: `${parent.title || "来源方向"}到${entry.title || "当前方向"}：${shortBranchLabel(entry)}`,
        });
      } else {
        edges.push({
          id: "membership:" + shot.id + ":" + entry.id,
          source: "shot:" + shot.id,
          target: entry.id,
          label: entry.relation === "settings" ? "旧记录" : entry.relation === "import" ? "导入任务" : "初始方向",
          className: mainlinePath.has(entry.id) ? "is-mainline" : "",
          type: "bezier",
          deletable: false,
          ariaLabel: `${shot.title}包含${entry.title || "初始方向"}`,
        });
      }
    }
    return edges;
  }, [shot, directions, byId, mainlinePath]);

  const openCompare = (left, right) => {
    const ownerOf = (candidateTake) => allEntries.find((entry) => entry.takes?.some((take) => (
      taskIdForTake(take) === taskIdForTake(candidateTake)
      && artifactIdForTake(take) === artifactIdForTake(candidateTake)
    )));
    const leftEntry = ownerOf(left);
    const rightEntry = ownerOf(right);
    if (!leftEntry || !rightEntry) return;
    restoreFocusRef.current = document.activeElement;
    setCompare({ left, right, leftEntry, rightEntry });
    setMode("compare");
  };
  const closeCompare = () => {
    setCompare(null);
    setMode("browse");
    globalThis.setTimeout?.(() => restoreFocusRef.current?.focus?.(), 0);
  };
  const openEditor = () => {
    if (!activeEntry) return;
    controller?.onEditEntry?.(activeEntry.id);
    setMode("edit");
  };
  const closeEditor = () => {
    controller?.onStopEditing?.();
    setMode("browse");
  };
  const openImport = (trigger) => {
    drawerRestoreFocusRef.current = trigger || document.activeElement;
    setImportOpen(true);
  };
  const closeImport = () => {
    setImportOpen(false);
    globalThis.setTimeout?.(() => drawerRestoreFocusRef.current?.focus?.(), 0);
  };

  const directionCount = allEntries.filter((entry) => entry.shotId === shot?.id && !entry.archived).length;
  const takeCount = allEntries.filter((entry) => entry.shotId === shot?.id && !entry.archived).reduce((sum, entry) => sum + (entry.takes?.length || 0), 0);
  return (
    <div className="wb-surface wb-lineage" data-ui="lineage-canvas" data-mode={mode}>
      <header className="lineage-header">
        <div className="lineage-header-primary">
          <button className="lineage-back" type="button" onClick={onBack} aria-label="返回创作入口"><ArrowLeft size={18} aria-hidden="true" /></button>
          <div className="lineage-product-title"><GitBranch size={20} weight="duotone" aria-hidden="true" /><div><h2>谱系图谱</h2><span>本机结构草稿</span></div></div>
          <span className="lineage-header-divider" aria-hidden="true" />
          <EditableShotTitle shot={shot} controller={controller} />
        </div>
        <div className="lineage-header-actions">
          <span className="lineage-count"><strong>{directionCount}</strong> 个方向 <strong>{takeCount}</strong> 个 Take</span>
          <label className="lineage-archived-toggle"><input type="checkbox" checked={showArchived} onChange={(event) => setShowArchived(event.target.checked)} /><span>显示已归档</span></label>
          <button type="button" onClick={(event) => openImport(event.currentTarget)}><FolderPlus size={17} aria-hidden="true" />导入任务</button>
        </div>
      </header>
      <nav className="lineage-shot-rail" aria-label="镜头列表">
        {shots.map((candidate, index) => (
          <button
            type="button"
            key={candidate.id}
            aria-current={candidate.id === shot?.id ? "page" : undefined}
            onClick={() => { setMode("browse"); controller?.onSelectShot?.(candidate.id); }}
          ><span>镜头 {String(index + 1).padStart(2, "0")}</span><strong>{candidate.title}</strong></button>
        ))}
        <button className="lineage-new-shot" type="button" disabled={controller?.locked} onClick={() => controller?.onNewShot?.({ title: "初始方向", shotTitle: "镜头 " + String(shots.length + 1).padStart(2, "0") })}><Plus size={16} aria-hidden="true" />新镜头</button>
      </nav>
      {mode === "compare" && compare ? (
        <TakeCompare {...compare} shot={shot} controller={controller} renderPreview={renderPreview} onClose={closeCompare} />
      ) : (
        <section className="lineage-workspace" aria-label="当前镜头谱系">
          <div className="lineage-graph" role="region" aria-label="方向关系画布">
            <ReactFlow
              nodes={nodes}
              edges={graphEdges}
              nodeTypes={LINEAGE_NODE_TYPES}
              onNodesChange={onNodesChange}
              onNodeClick={(_, node) => {
                const entry = allEntries.find((candidate) => candidate.id === node.id);
                if (entry && !entry.archived) controller?.onSelectEntry?.(node.id, { edit: false });
              }}
              onNodeDoubleClick={(_, node) => {
                const entry = allEntries.find((candidate) => candidate.id === node.id);
                if (entry && !entry.archived && !controller?.locked) controller?.onEditEntry?.(node.id);
              }}
              onNodeDragStop={(_, node) => {
                const entry = allEntries.find((candidate) => candidate.id === node.id);
                if (entry && !entry.archived && !controller?.locked) controller?.onMoveEntryOnCanvas?.(node.id, node.position);
              }}
              nodesConnectable={false}
              nodesDraggable={!controller?.locked}
              elementsSelectable
              deleteKeyCode={null}
              fitView
              fitViewOptions={{ padding: 0.22, minZoom: 0.62, maxZoom: 1 }}
              minZoom={0.35}
              maxZoom={1.55}
              defaultEdgeOptions={{ focusable: true, interactionWidth: 18 }}
              ariaLabelConfig={{
                "node.a11yDescription.default": "按 Enter 或空格选择创作方向。已归档方向请使用节点内的恢复按钮。",
                "node.a11yDescription.keyboardDisabled": "按 Enter 或空格选择创作方向；未锁定的方向可用方向键调整画布位置。",
                "edge.a11yDescription.default": "这条关系线来自已保存的精确来源 Take 或镜头成员关系，不可在画布中删除。",
                "handle.ariaLabel": "只读关系端点",
              }}
              proOptions={{ hideAttribution: true }}
            >
              <Background variant={BackgroundVariant.Dots} gap={22} size={1.1} />
              <LineageCanvasTools canonicalNodeId={canonicalNodeId} />
              <Panel className="lineage-canvas-legend" position="top-right">
                <span><i className="is-mainline" />导演主线</span><span><i />探索方向</span>
              </Panel>
            </ReactFlow>
          </div>
          <div className="lineage-inspector-shell">
            {mode === "edit" && activeEntry ? (
              <section className="lineage-editor" aria-label="当前方向编辑器">
                <header><div><span className="lineage-eyebrow">方向 {String(directionNumber(activeEntry, allEntries)).padStart(2, "0")}</span><strong>{activeEntry.title}</strong></div><button type="button" onClick={closeEditor}><X size={17} aria-hidden="true" />收起编辑</button></header>
                <div className="lineage-editor-scroll">{renderEditor({ variant: "canvas", title: "当前方向", onClose: closeEditor })}</div>
              </section>
            ) : activeEntry ? (
              <DirectionInspector
                entry={activeEntry}
                entries={allEntries}
                shot={shot}
                controller={controller}
                renderPreview={renderPreview}
                renderResultActions={renderResultActions}
                renderTransfers={renderTransfers}
                onEdit={openEditor}
                onCompare={openCompare}
              />
            ) : (
              <EmptyDirectionInspector controller={controller} onEdit={openEditor} />
            )}
          </div>
        </section>
      )}
      {importOpen && (
        <div className="lineage-drawer-scrim" onMouseDown={(event) => { if (event.target === event.currentTarget) closeImport(); }}>
          <aside
            ref={drawerRef}
            className="lineage-import-drawer"
            role="dialog"
            aria-modal="true"
            aria-labelledby="lineage-import-title"
            aria-describedby="lineage-import-description"
          >
            <header><div><span className="lineage-eyebrow">真实任务记录</span><h3 id="lineage-import-title" ref={drawerTitleRef} tabIndex={-1}>导入为独立镜头</h3><p id="lineage-import-description">导入不会猜测镜头顺序，也不会自动建立谱系连线。</p></div><button type="button" onClick={closeImport} aria-label="关闭导入面板"><X size={19} /></button></header>
            {renderHistoryImport(true)}
          </aside>
        </div>
      )}
    </div>
  );
}

export default function LineageGraphWorkspace(props) {
  return <ReactFlowProvider><LineageGraphInner {...props} /></ReactFlowProvider>;
}
