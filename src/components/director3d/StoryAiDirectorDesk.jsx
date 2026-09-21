import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowClockwise, Cube, WarningCircle } from "@phosphor-icons/react";
import {
  createDirectorDeskBinding,
  createDirectorDeskConnectMessage,
  createDirectorDeskPortMessage,
  createDirectorDeskSessionId,
  createDirectorDeskSessionMessage,
  DIRECTOR_DESK_MESSAGES,
  DIRECTOR_DESK_SANDBOX_ORIGIN,
  directorCapturePayloadToFiles,
  parseDirectorDeskReadyMessage,
  parseDirectorDeskPortMessage,
  requireDirectorCaptureCommit,
  supportsDirectorDeskWebGl,
} from "./directorBridge.js";
import { createDirectorScenePersistenceSession } from "./directorPersistence.js";

const READY_TIMEOUT_MS = 15_000;

function publicErrorMessage(error, fallback) {
  if (error?.name === "AbortError") return "导演场景保存已取消。";
  return typeof error?.message === "string" && error.message.trim() ? error.message.trim() : fallback;
}

export default function StoryAiDirectorDesk({
  scopeKey,
  shotId,
  shotTitle,
  onCaptureFiles,
  onCaptureStatus,
  onPersistenceStatus,
  onClose,
}) {
  const iframeRef = useRef(null);
  const portRef = useRef(null);
  const persistenceRef = useRef(null);
  const handlersRef = useRef({ onCaptureFiles, onCaptureStatus, onPersistenceStatus, onClose });
  handlersRef.current = { onCaptureFiles, onCaptureStatus, onPersistenceStatus, onClose };
  const [editorState, setEditorState] = useState("checking");
  const [reloadKey, setReloadKey] = useState(0);
  const [recoveryToken, setRecoveryToken] = useState("");
  const [recoveryBusy, setRecoveryBusy] = useState(false);
  const instanceId = useMemo(() => createDirectorDeskSessionId(scopeKey, shotId), [scopeKey, shotId]);
  const origin = globalThis.location?.origin || "";
  const source = `/director-desk/index.html?embedded=1&theme=light&instanceId=${encodeURIComponent(instanceId)}&parentOrigin=${encodeURIComponent(origin)}&v=${reloadKey}`;

  useEffect(() => {
    setEditorState(supportsDirectorDeskWebGl() ? "loading" : "unavailable");
  }, [reloadKey]);

  useEffect(() => {
    if (!["loading", "hydrating"].includes(editorState)) return undefined;
    const timeout = globalThis.setTimeout(() => setEditorState("error"), READY_TIMEOUT_MS);
    return () => globalThis.clearTimeout(timeout);
  }, [editorState, reloadKey]);

  useEffect(() => {
    let active = true;
    let connected = false;
    let inboundSequence = 0;
    let outboundSequence = 0;
    let binding = null;
    let persistence = null;
    let captureBusy = false;
    setRecoveryToken("");
    setRecoveryBusy(false);

    const postPort = (type, payload) => {
      if (!active || !binding || !portRef.current) return false;
      outboundSequence += 1;
      portRef.current.postMessage(createDirectorDeskPortMessage(binding, type, outboundSequence, payload));
      return true;
    };

    const reportPersistence = (state, message) => {
      if (active) handlersRef.current.onPersistenceStatus?.({ state, message });
    };

    const handleCapture = async (message) => {
      const requestId = message.payload.requestId;
      try {
        const files = directorCapturePayloadToFiles(message.payload);
        handlersRef.current.onCaptureStatus?.("正在按当前模型能力校验这张取景图…");
        const commit = requireDirectorCaptureCommit(await handlersRef.current.onCaptureFiles?.(files));
        const status = commit.message || `${commit.addedCount} 张取景图已加入当前镜头；生成仍需你明确提交。`;
        handlersRef.current.onCaptureStatus?.(status);
        postPort(DIRECTOR_DESK_MESSAGES.captureResult, {
          requestId,
          ok: true,
          addedCount: commit.addedCount,
          message: status,
        });
      } catch (error) {
        const messageText = publicErrorMessage(error, "取景图未能加入当前镜头。");
        handlersRef.current.onCaptureStatus?.(messageText);
        postPort(DIRECTOR_DESK_MESSAGES.captureResult, {
          requestId,
          ok: false,
          addedCount: 0,
          message: messageText,
        });
      }
    };

    const handleSceneSave = async (message) => {
      if (!persistence) return;
      reportPersistence("saving", "正在保存当前场景…");
      try {
        const result = await persistence.save(message.payload.snapshot);
        if (!active || persistenceRef.current !== persistence) return;
        if (!result.superseded) reportPersistence("saved", "场景已保存于当前设备");
        postPort(DIRECTOR_DESK_MESSAGES.sceneSaved, {
          replyToSequence: message.sequence,
          ok: true,
          revision: result.revision,
          superseded: Boolean(result.superseded),
        });
      } catch (error) {
        if (!active || error?.name === "AbortError") return;
        const messageText = publicErrorMessage(error, "本机保存失败，刷新后可能丢失当前场景。");
        reportPersistence("error", messageText);
        postPort(DIRECTOR_DESK_MESSAGES.sceneSaved, {
          replyToSequence: message.sequence,
          ok: false,
          revision: persistence.getRevision(),
          message: messageText,
        });
      }
    };

    const handlePortMessage = (event) => {
      const message = parseDirectorDeskPortMessage(event.data, { binding, lastSequence: inboundSequence });
      if (!message) return;
      inboundSequence = message.sequence;
      if (message.type === DIRECTOR_DESK_MESSAGES.sessionAck) {
        if (!message.payload.accepted) {
          reportPersistence("error", "3D 导演台拒绝了本机场景，原记录未被覆盖。");
          setEditorState("error");
          return;
        }
        setEditorState("ready");
        return;
      }
      if (message.type === DIRECTOR_DESK_MESSAGES.close) {
        handlersRef.current.onClose?.();
        return;
      }
      if (message.type === DIRECTOR_DESK_MESSAGES.captures) {
        if (captureBusy) {
          const messageText = "上一批取景图仍在写入当前镜头，请完成后再试。";
          handlersRef.current.onCaptureStatus?.(messageText);
          postPort(DIRECTOR_DESK_MESSAGES.captureResult, {
            requestId: message.payload.requestId,
            ok: false,
            addedCount: 0,
            message: messageText,
          });
          return;
        }
        captureBusy = true;
        void handleCapture(message).finally(() => { captureBusy = false; });
        return;
      }
      if (message.type === DIRECTOR_DESK_MESSAGES.sceneSave) void handleSceneSave(message);
    };

    const connectDirector = async (frameWindow) => {
      if (!globalThis.MessageChannel) {
        setEditorState("error");
        reportPersistence("error", "当前浏览器不支持隔离的导演台通信。");
        return;
      }
      connected = true;
      binding = createDirectorDeskBinding(instanceId, reloadKey + 1);
      persistence = createDirectorScenePersistenceSession(instanceId);
      persistenceRef.current = persistence;
      const channel = new MessageChannel();
      const port = channel.port1;
      portRef.current?.close?.();
      portRef.current = port;
      port.onmessage = handlePortMessage;
      port.onmessageerror = () => {
        if (active) {
          reportPersistence("error", "导演台通信内容无效，连接已停止。");
          setEditorState("error");
        }
      };
      port.start?.();
      // Opaque-origin sandbox documents can only be targeted with "*". The
      // child separately verifies parent source+origin and the transferred
      // port is bound to a random nonce, instance and epoch before use.
      frameWindow.postMessage(createDirectorDeskConnectMessage(binding), "*", [channel.port2]);
      let loaded;
      try {
        loaded = await persistence.load();
        if (active) reportPersistence(
          loaded.snapshot ? "saved" : "ready",
          loaded.snapshot ? "已恢复当前设备上的场景" : "场景将在当前设备自动保存",
        );
      } catch (error) {
        if (!active || error?.name === "AbortError") return;
        setRecoveryToken(typeof error?.recoveryToken === "string" ? error.recoveryToken : "");
        reportPersistence("error", publicErrorMessage(error, "无法读取本机导演场景；原记录未被覆盖。"));
        setEditorState("storage-error");
        return;
      }
      if (!active || portRef.current !== port || iframeRef.current?.contentWindow !== frameWindow) return;
      outboundSequence += 1;
      port.postMessage(createDirectorDeskSessionMessage(binding, outboundSequence, loaded));
    };

    const handleWindowMessage = (event) => {
      const ready = parseDirectorDeskReadyMessage(event, {
        source: iframeRef.current?.contentWindow,
        origin: DIRECTOR_DESK_SANDBOX_ORIGIN,
        instanceId,
      });
      if (!ready || connected) return;
      setEditorState("hydrating");
      void connectDirector(event.source);
    };
    globalThis.addEventListener?.("message", handleWindowMessage);
    return () => {
      active = false;
      globalThis.removeEventListener?.("message", handleWindowMessage);
      portRef.current?.close?.();
      portRef.current = null;
      persistence?.close?.();
      if (persistenceRef.current === persistence) persistenceRef.current = null;
    };
  }, [instanceId, reloadKey]);

  const retry = () => {
    setEditorState("checking");
    setReloadKey((value) => value + 1);
  };

  const recoverFromQuarantine = async () => {
    const persistence = persistenceRef.current;
    const token = recoveryToken;
    if (!persistence || !token || recoveryBusy) return;
    setRecoveryBusy(true);
    handlersRef.current.onPersistenceStatus?.({
      state: "recovering",
      message: "正在确认隔离副本并重建空场景…",
    });
    try {
      await persistence.recover(token);
      setRecoveryToken("");
      handlersRef.current.onPersistenceStatus?.({
        state: "recovered",
        message: "原记录仍保留在隔离区，已从空场景重新开始",
      });
      retry();
    } catch (error) {
      const message = publicErrorMessage(error, "当前记录已经变化，未执行重建。请重新载入确认。 ");
      handlersRef.current.onPersistenceStatus?.({ state: "error", message });
      setEditorState("storage-error");
      setRecoveryBusy(false);
    }
  };

  if (editorState === "unavailable") return (
    <div className="director-embed-unavailable" data-ui="director-3d-unavailable" role="status">
      <WarningCircle size={28} weight="duotone" aria-hidden="true" />
      <div><strong>这台设备当前无法启动 3D 视口</strong><p>请开启浏览器硬件加速或换用支持 WebGL 的设备。镜头草稿与生成引擎没有被改动。</p></div>
    </div>
  );

  const failed = editorState === "error" || editorState === "storage-error";
  return (
    <div className="director-embed" data-ui="storyai-director-desk" data-editor-state={editorState} aria-label={`${shotTitle || "当前镜头"}的 3D 场景`}>
      {editorState !== "ready" && (
        <div className={`director-embed-state is-${editorState}`} role={failed ? "alert" : "status"}>
          {failed ? <WarningCircle size={25} aria-hidden="true" /> : <Cube className="director-embed-cube" size={25} weight="duotone" aria-hidden="true" />}
          <strong>{editorState === "storage-error" ? "本机场景未能安全恢复" : editorState === "error" ? "3D 导演台没有完成载入" : "正在装载真实 3D 场景…"}</strong>
          {editorState === "storage-error" && (
            <p>原记录已完整保留在隔离区，当前场景没有被空内容覆盖。只有你明确选择后，才会从空场景重新开始。</p>
          )}
          {editorState === "storage-error" && recoveryToken && (
            <button
              type="button"
              data-ui="director-rebuild-from-quarantine"
              disabled={recoveryBusy}
              onClick={recoverFromQuarantine}
            >
              <ArrowClockwise size={17} aria-hidden="true" />
              {recoveryBusy ? "正在保留并重建…" : "保留隔离副本并从空场景重建"}
            </button>
          )}
          {failed && !recoveryBusy && <button type="button" onClick={retry}><ArrowClockwise size={17} aria-hidden="true" />重新载入</button>}
        </div>
      )}
      <iframe
        key={reloadKey}
        ref={iframeRef}
        className="director-embed-frame"
        src={source}
        title="StoryAI 3D 导演台"
        loading="eager"
        allow="fullscreen"
        sandbox="allow-downloads allow-scripts"
        onLoad={() => editorState === "checking" && setEditorState("loading")}
      />
    </div>
  );
}
