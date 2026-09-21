import { useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  attachWorkbenchTask,
  createWorkbenchEntry,
  emptyCreationWorkspace,
  moveWorkbenchEntry,
  readCreationWorkspace,
  renameLineageShot,
  sanitizeCreationDraft,
  selectLineageShot,
  selectWorkbenchEntry,
  setLineageBranchArchived,
  setLineageCanonicalTake,
  updateWorkbenchEntry,
  writeCreationWorkspace,
} from "./creationWorkspaces.js";

export const ADVANCED_WORKBENCHES = ["console", "notebook", "canvas"];
export function readCreationWorkbench(search = globalThis.location?.search) {
  const kind = new URLSearchParams(search || "").get("workbench");
  return ADVANCED_WORKBENCHES.includes(kind) ? kind : "entry";
}

const draftKey = (scopeKey, entryId) => JSON.stringify([scopeKey, entryId || "quick"]);
const draftEqual = (left, right) => JSON.stringify(left) === JSON.stringify(right);

/** Local document structure, not a second task engine or an authority cache. */
export function useCreationWorkspaceSession({
  scopeKey, workbench, draft, draftIdentity, replaceDraft, panel, mobileOpen,
  restoreDisclosure, locked, onNavigate, onNotice, draftForTask,
}) {
  const [state, setState] = useState(() => emptyCreationWorkspace("unresolved"));
  const [storageNotice, setStorageNotice] = useState("");
  const [editing, setEditing] = useState(true);
  const stateRef = useRef(state);
  const bindingRef = useRef(null);
  const lastSaveRef = useRef({ ok: true, notice: "" });
  const latest = useRef({});
  latest.current = { draft, draftIdentity, panel, mobileOpen, locked, workbench, scopeKey, replaceDraft, restoreDisclosure, onNotice, draftForTask };

  function commit(next) {
    stateRef.current = next;
    setState(next);
    const saved = writeCreationWorkspace(next);
    lastSaveRef.current = saved;
    setStorageNotice(saved.notice || "");
    return next;
  }

  function saveActiveUnsafe() {
    const current = latest.current;
    const binding = bindingRef.current;
    if (!binding || current.draftIdentity !== binding.key || stateRef.current.scopeKey !== binding.scopeKey) return;
    const clean = sanitizeCreationDraft(current.draft);
    const doc = stateRef.current;
    if (binding.entryId) {
      const entry = doc.entries.find((item) => item.id === binding.entryId);
      if (entry && !draftEqual(entry.draft, clean)) commit(updateWorkbenchEntry(doc, entry.id, { draft: clean }));
    } else if (!draftEqual(doc.quick.draft, clean) || doc.quick.panel !== current.panel || doc.quick.mobileOpen !== current.mobileOpen) {
      commit({ ...doc, revision: doc.revision + 1, quick: { draft: clean, panel: current.panel, mobileOpen: current.mobileOpen } });
    }
  }

  function saveActive() {
    try { saveActiveUnsafe(); }
    catch (error) { setStorageNotice(error.message || "本机保存失败，当前修改仅在页面内保留。"); }
  }

  function bind(doc, kind, entry, { edit = true } = {}) {
    const key = draftKey(doc.scopeKey, entry?.id);
    bindingRef.current = { scopeKey: doc.scopeKey, kind, entryId: entry?.id || "", key };
    latest.current.replaceDraft(entry ? entry.draft : doc.quick.draft, key);
    latest.current.restoreDisclosure(entry ? null : doc.quick.panel, entry ? true : doc.quick.mobileOpen);
    setEditing(edit);
  }

  useLayoutEffect(() => {
    if (!scopeKey) return;
    const previous = bindingRef.current;
    const kind = ADVANCED_WORKBENCHES.includes(workbench) ? workbench : "quick";
    if (previous?.scopeKey === scopeKey && previous.kind === kind) return;
    // Never save an old identity's render into the new user's storage.
    if (previous?.scopeKey === scopeKey) saveActive();
    let doc = stateRef.current;
    if (doc.scopeKey !== scopeKey) {
      const loaded = readCreationWorkspace(scopeKey);
      doc = loaded.state;
      stateRef.current = doc;
      setState(doc);
      setStorageNotice(loaded.notice || "");
    }
    let entry = kind === "quick" ? null : doc.entries.find((item) => item.kind === kind && item.id === doc.active[kind]);
    if (kind !== "quick" && !entry) {
      try {
        const created = createWorkbenchEntry(doc, kind);
        doc = commit(created.state);
        entry = created.entry;
      } catch (error) {
        bindingRef.current = null;
        setStorageNotice(error.message || "未能建立本机草稿。");
        return;
      }
    }
    bind(doc, kind, entry, { edit: kind === "notebook" });
  }, [scopeKey, workbench]);

  const signature = JSON.stringify(draft);
  useEffect(() => {
    if (!scopeKey || bindingRef.current?.scopeKey !== scopeKey) return;
    saveActive();
  }, [scopeKey, draftIdentity, signature, panel, mobileOpen]);

  useEffect(() => {
    const save = () => { if (bindingRef.current?.scopeKey === latest.current.scopeKey) saveActive(); };
    globalThis.addEventListener?.("pagehide", save);
    return () => globalThis.removeEventListener?.("pagehide", save);
  }, []);

  function mutate(operation) {
    try { saveActive(); return operation(); }
    catch (error) { latest.current.onNotice(error.message || "工作台未能保存，请稍后再试。"); return null; }
  }
  function requireEditable() {
    if (!latest.current.locked) return true;
    latest.current.onNotice("先完成素材上传或确认当前提交，再新建、改名或复制草稿。");
    return false;
  }
  function selectEntry(id, { edit = false } = {}) {
    return mutate(() => {
      const entry = stateRef.current.entries.find((item) => item.id === id);
      if (!entry) return;
      const doc = commit(selectWorkbenchEntry(stateRef.current, entry.kind, id));
      bind(doc, entry.kind, entry, { edit });
    });
  }
  function newEntry({ parentId = "", sourceTask, sourceTake, relation = "", draft: suppliedDraft, targetKind,
    branchKind = "", branchReason = "", title, shotTitle } = {}) {
    if (!requireEditable()) return null;
    return mutate(() => {
      const kind = targetKind || latest.current.workbench;
      if (!ADVANCED_WORKBENCHES.includes(kind)) return null;
      const parent = parentId ? stateRef.current.entries.find((item) => item.id === parentId) : null;
      if (parentId && !parent) throw new TypeError("未找到分支的来源方向。");
      if (parent && kind === "canvas" && !sourceTake) throw new TypeError("谱系分支必须从一个精确 Take 发起。");
      const sourceArtifactId = sourceTake?.artifact?.artifact_id || "";
      if (sourceTake && (!sourceTake.task?.id || !sourceArtifactId)) {
        throw new TypeError("这个 Take 缺少平台任务或产物标识，不能建立谱系分支。");
      }
      const sourceDraft = sourceTake ? latest.current.draftForTask(sourceTake.task) : null;
      const created = createWorkbenchEntry(stateRef.current, kind, {
        parentId,
        relation: parent ? "lineage" : sourceTask ? "import" : relation,
        draft: sourceTake ? sourceDraft : sourceTask ? latest.current.draftForTask(sourceTask) : suppliedDraft,
        title,
        shotTitle,
        sourceTaskId: sourceTake?.task?.id || "",
        sourceArtifactId,
        branchKind: parent ? branchKind : "",
        branchReason: parent ? branchReason : "",
        sourceDraft,
      });
      let doc = created.state;
      if (sourceTask?.id) doc = attachWorkbenchTask(doc, created.entry.id, sourceTask.id);
      commit(doc);
      bind(doc, kind, doc.entries.find((item) => item.id === created.entry.id), { edit: kind !== "console" });
      if (kind !== latest.current.workbench) onNavigate(kind);
      return created.entry;
    });
  }

  return {
    state: state.scopeKey === scopeKey ? state : emptyCreationWorkspace(scopeKey || "unresolved"),
    editing, storageNotice, bindingRef,
    activeEntryId: state.scopeKey === scopeKey ? state.active[workbench] || "" : "",
    saveActive,
    onSelectEntry: selectEntry,
    onEditEntry: (id) => selectEntry(id, { edit: true }),
    onStopEditing: () => { saveActive(); setEditing(false); },
    onNewEntry: newEntry,
    onImportTask: (task) => newEntry({ sourceTask: task }),
    onBranchEntry: (id, options = {}) => newEntry({ parentId: id, sourceTake: options.sourceTake,
      branchKind: options.branchKind, branchReason: options.branchReason, title: options.title }),
    onRenameEntry: (id, title) => requireEditable() && mutate(() => commit(updateWorkbenchEntry(stateRef.current, id, { title }))),
    onMoveEntry: (id, direction) => requireEditable() && mutate(() => commit(moveWorkbenchEntry(stateRef.current, id, direction))),
    onSelectVersion: (id, taskId, artifactId = "") => mutate(() => commit(updateWorkbenchEntry(stateRef.current, id, {
      selectedTaskId: taskId, selectedArtifactId: artifactId,
    }))),
    onSelectShot: (shotId) => mutate(() => {
      const doc = commit(selectLineageShot(stateRef.current, shotId));
      const entry = doc.entries.find((item) => item.id === doc.active.canvas);
      bind(doc, "canvas", entry, { edit: false });
    }),
    onRenameShot: (shotId, title) => requireEditable() && mutate(() => commit(renameLineageShot(stateRef.current, shotId, title))),
    onSetCanonical: (entryId, taskId, artifactId) => requireEditable()
      && mutate(() => commit(setLineageCanonicalTake(stateRef.current, entryId, taskId, artifactId))),
    onArchiveEntry: (entryId, archived) => requireEditable() && mutate(() => {
      const doc = commit(setLineageBranchArchived(stateRef.current, entryId, archived));
      const entry = doc.entries.find((item) => item.id === (archived ? doc.active.canvas : entryId));
      if (entry) bind(doc, "canvas", entry, { edit: false });
      return entry;
    }),
    onMoveEntryOnCanvas: (id, position) => requireEditable() && mutate(() => commit(updateWorkbenchEntry(stateRef.current, id, {
      x: position.x, y: position.y,
    }))),
    onNewShot: (options = {}) => newEntry({ targetKind: "canvas", title: options.title, shotTitle: options.shotTitle }),
    onTransfer: (kind) => newEntry({ targetKind: kind, draft: latest.current.draft }),
    onOpenWorkbench: onNavigate,
    updateQuickDraft(update, { panel = null, mobileOpen = true } = {}) {
      if (!requireEditable()) return false;
      return Boolean(mutate(() => {
        const doc = stateRef.current;
        const nextDraft = sanitizeCreationDraft(typeof update === "function" ? update(doc.quick.draft) : update);
        const next = commit({ ...doc, revision: doc.revision + 1, quick: { draft: nextDraft, panel, mobileOpen } });
        bind(next, "quick", null);
        onNavigate("entry");
        return true;
      }));
    },
    captureContext() {
      saveActive();
      const binding = bindingRef.current;
      return binding ? { scopeKey: binding.scopeKey, kind: binding.kind, entryId: binding.entryId, draftRevision: stateRef.current.revision } : null;
    },
    taskBelongsToCurrentDraft(taskId) {
      const binding = bindingRef.current;
      if (!binding || binding.scopeKey !== latest.current.scopeKey) return false;
      const owners = stateRef.current.entries.filter((entry) => entry.taskIds.includes(taskId));
      return owners.length ? owners.some((entry) => entry.id === binding.entryId) : binding.kind === "quick";
    },
    restoreContext(context) {
      if (!context || context.scopeKey !== scopeKey) return false;
      if (context.kind === "quick") { bind(stateRef.current, "quick", null); onNavigate("entry"); return true; }
      let entry = stateRef.current.entries.find((item) => item.id === context.entryId && item.kind === context.kind);
      if (!entry) {
        // The exact pending request outlives a cleared local document. Restore
        // its pinned authoring target, never write it over the current draft.
        try {
          const restored = createWorkbenchEntry(stateRef.current, context.kind, { id: context.entryId });
          commit(restored.state);
          entry = restored.entry;
          onNotice("已重建原提交的本机草稿。确认时仍使用同一个请求编号。");
        } catch (error) {
          onNotice(error.message || "无法恢复原提交的工作台位置，当前草稿未覆盖。");
          return false;
        }
      }
      selectEntry(entry.id, { edit: true });
      onNavigate(entry.kind);
      return true;
    },
    bindTask(context, taskId) {
      if (!context?.entryId) return { ok: true, notice: "" };
      const doc = stateRef.current.scopeKey === context.scopeKey ? stateRef.current : readCreationWorkspace(context.scopeKey).state;
      const next = attachWorkbenchTask(doc, context.entryId, taskId);
      if (stateRef.current.scopeKey === context.scopeKey) { commit(next); return lastSaveRef.current; }
      return writeCreationWorkspace(next);
    },
  };
}

/** Resolve stored IDs through the current user's API; never persist task data or URLs. */
export function useWorkbenchTaskRecords({ client, scopeKey, enabled, taskIds, knownTasks, onAuthError }) {
  const [records, setRecords] = useState({ scopeKey: "", items: {}, unavailable: [] });
  const idsKey = JSON.stringify([...new Set(taskIds)].sort());
  const known = useRef(knownTasks);
  known.current = knownTasks;
  const authError = useRef(onAuthError);
  authError.current = onAuthError;
  useEffect(() => {
    setRecords({ scopeKey, items: {}, unavailable: [] });
    if (!enabled || !client || !scopeKey || !taskIds.length) return undefined;
    const controller = new AbortController();
    let timer;
    const ids = JSON.parse(idsKey);
    async function refresh() {
      const items = {};
      const unavailable = [];
      let cursor = 0;
      let needsRefresh = false;
      await Promise.all(Array.from({ length: Math.min(4, ids.length) }, async () => {
        while (!controller.signal.aborted && cursor < ids.length) {
          const id = ids[cursor++];
          const cached = known.current.find((task) => task.id === id);
          try {
            const task = cached && ["succeeded", "failed", "cancelled", "timed_out"].includes(cached.status)
              ? cached : await client.getTask(id, { scope: "mine", signal: controller.signal });
            if (task?.id !== id) { unavailable.push(id); continue; }
            items[id] = task;
            if (["accepted", "queued", "processing", "submitted", "running"].includes(task.status)) needsRefresh = true;
          } catch (error) {
            if (error?.name === "AbortError") break;
            unavailable.push(id);
            if (authError.current?.(error)) { controller.abort(); break; }
          }
        }
      }));
      if (controller.signal.aborted) return;
      setRecords({ scopeKey, items, unavailable });
      if (needsRefresh) timer = globalThis.setTimeout(refresh, 4000);
    }
    refresh();
    return () => { controller.abort(); globalThis.clearTimeout(timer); };
  }, [scopeKey, enabled, client, idsKey]);
  return records.scopeKey === scopeKey ? records : { items: {}, unavailable: [] };
}
