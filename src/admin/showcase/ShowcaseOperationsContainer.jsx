import { useCallback, useEffect, useRef, useState } from "react";
import { ShowcaseOperationsScreen } from "./ShowcaseOperations.jsx";
import {
  normalizeAdminShowcase,
  reorderedShowcaseItems,
  showcaseManifestsEqual,
  showcaseMutationPayload,
} from "./showcaseModel.js";
import {
  createShowcaseDemoSnapshot,
  SHOWCASE_DEMO_ARTWORKS,
} from "./showcaseDemoData.js";

function actionMessage(error) {
  if (error?.status === 409) return "草稿或线上发布状态已被其他页面更新，请刷新后重新操作。";
  if (error?.status === 403) return "只有平台所有者可以管理首页精选案例。";
  if (error?.status === 422) return "提交内容没有通过校验，请检查媒体、文字和排序。";
  return error?.message || "首页内容操作失败，请稍后重试。";
}

function requestKey(prefix) {
  const suffix = globalThis.crypto?.randomUUID?.()
    || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}-${suffix}`.slice(0, 120);
}

function stableOperationKey(reference, prefix, signature) {
  if (reference.current?.signature !== signature) {
    reference.current = { signature, key: requestKey(prefix) };
  }
  return reference.current.key;
}

function demoAspect(value) {
  if (["16:9", "4:3"].includes(value)) return "landscape";
  if (value === "3:4") return "portrait";
  if (value === "9:16") return "tall";
  return "square";
}

function demoId() {
  return globalThis.crypto?.randomUUID?.()
    || `demo-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function copyItems(items) {
  return (items || []).map((item) => ({ ...item }));
}

export function ShowcaseOperationsContainer({
  active,
  client,
  demoMode = false,
  onAuthenticationError,
}) {
  const [snapshot, setSnapshot] = useState(() => (
    demoMode ? createShowcaseDemoSnapshot() : null
  ));
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busyAction, setBusyAction] = useState("");
  const [ownedArtworks, setOwnedArtworks] = useState([]);
  const [ownedArtworksLoading, setOwnedArtworksLoading] = useState(false);
  const [ownedArtworksError, setOwnedArtworksError] = useState("");
  const requestSequence = useRef(0);
  const activeController = useRef(null);
  const artworksController = useRef(null);
  const mediaOperation = useRef(null);
  const publishOperation = useRef(null);
  const unpublishOperation = useRef(null);
  const rollbackOperation = useRef(null);
  const demoObjectUrls = useRef(new Set());

  const load = useCallback(async ({ silent = false, signal } = {}) => {
    if (demoMode || !client?.getAdminShowcase) return null;
    const sequence = requestSequence.current + 1;
    requestSequence.current = sequence;
    if (!silent) setLoading(true);
    setError("");
    try {
      const payload = await client.getAdminShowcase({ signal });
      if (sequence !== requestSequence.current || signal?.aborted) return null;
      const next = normalizeAdminShowcase(payload);
      setSnapshot(next);
      return next;
    } catch (loadError) {
      if (signal?.aborted) return null;
      if (onAuthenticationError?.(loadError)) return null;
      if (sequence === requestSequence.current) setError(actionMessage(loadError));
      throw loadError;
    } finally {
      if (!silent && sequence === requestSequence.current && !signal?.aborted) {
        setLoading(false);
      }
    }
  }, [client, demoMode, onAuthenticationError]);

  const loadOwnedArtworks = useCallback(async ({ signal } = {}) => {
    if (demoMode) {
      setOwnedArtworks(SHOWCASE_DEMO_ARTWORKS);
      setOwnedArtworksError("");
      return SHOWCASE_DEMO_ARTWORKS;
    }
    if (!client?.listPersonalArtworks) {
      setOwnedArtworksError("作品清单暂不可用，可直接粘贴本人作品的 Artifact ID。");
      return [];
    }
    setOwnedArtworksLoading(true);
    setOwnedArtworksError("");
    try {
      const payload = await client.listPersonalArtworks(
        { page: 1, page_size: 50 },
        { signal },
      );
      if (signal?.aborted) return [];
      const items = (Array.isArray(payload) ? payload : payload?.items || [])
        .filter((item) => (
          typeof item?.artifact_id === "string"
          && ["image", "video"].includes(item?.media_type)
        ));
      setOwnedArtworks(items);
      return items;
    } catch (artworkError) {
      if (signal?.aborted) return [];
      if (onAuthenticationError?.(artworkError)) return [];
      setOwnedArtworksError("本人作品清单读取失败，可直接粘贴已验证作品的 Artifact ID。");
      return [];
    } finally {
      if (!signal?.aborted) setOwnedArtworksLoading(false);
    }
  }, [client, demoMode, onAuthenticationError]);

  const reloadOwnedArtworks = useCallback(() => {
    if (demoMode) {
      setOwnedArtworks(SHOWCASE_DEMO_ARTWORKS);
      setOwnedArtworksError("");
      return Promise.resolve(SHOWCASE_DEMO_ARTWORKS);
    }
    artworksController.current?.abort();
    const controller = new AbortController();
    artworksController.current = controller;
    return loadOwnedArtworks({ signal: controller.signal });
  }, [demoMode, loadOwnedArtworks]);

  useEffect(() => () => {
    for (const url of demoObjectUrls.current) URL.revokeObjectURL?.(url);
    demoObjectUrls.current.clear();
  }, []);

  useEffect(() => {
    if (!active) return undefined;
    if (demoMode) {
      setSnapshot((current) => current || createShowcaseDemoSnapshot());
      setOwnedArtworks(SHOWCASE_DEMO_ARTWORKS);
      setOwnedArtworksError("");
      return undefined;
    }
    if (!client?.getAdminShowcase) return undefined;
    activeController.current?.abort();
    const controller = new AbortController();
    activeController.current = controller;
    load({ signal: controller.signal }).catch(() => {});
    const ownedController = new AbortController();
    artworksController.current = ownedController;
    loadOwnedArtworks({ signal: ownedController.signal });
    return () => {
      controller.abort();
      ownedController.abort();
    };
  }, [active, client, demoMode, load, loadOwnedArtworks]);

  const reload = useCallback(() => {
    if (demoMode) {
      setNotice("演示草稿已重新读取；没有向 Platform 发起请求。");
      return Promise.resolve(snapshot);
    }
    activeController.current?.abort();
    const controller = new AbortController();
    activeController.current = controller;
    setNotice("");
    return load({ signal: controller.signal }).catch(() => null);
  }, [demoMode, load, snapshot]);

  const performDemo = useCallback(async (name, update, successMessage) => {
    setBusyAction(name);
    setError("");
    setNotice("");
    try {
      await Promise.resolve();
      setSnapshot((current) => update(current || createShowcaseDemoSnapshot()));
      setNotice(successMessage);
      return true;
    } finally {
      setBusyAction("");
    }
  }, []);

  const perform = useCallback(async (name, work, successMessage) => {
    setBusyAction(name);
    setError("");
    setNotice("");
    try {
      await work();
      await load({ silent: true });
      setNotice(successMessage);
      return true;
    } catch (actionError) {
      if (onAuthenticationError?.(actionError)) return false;
      if (actionError?.status === 409) {
        await load({ silent: true }).catch(() => null);
      }
      const message = actionMessage(actionError);
      setError(message);
      const surfaced = new Error(message);
      surfaced.cause = actionError;
      throw surfaced;
    } finally {
      setBusyAction("");
    }
  }, [load, onAuthenticationError]);

  const save = useCallback(async (values, editingItem) => {
    const artifactId = String(values.sourceTaskArtifactId || "").trim();
    if (demoMode) {
      const artwork = SHOWCASE_DEMO_ARTWORKS.find((entry) => (
        entry.artifact_id === artifactId
      ));
      const existingMedia = snapshot?.media?.find((entry) => (
        entry.id === values.mediaId
      ));
      const localUrl = values.file && URL.createObjectURL
        ? URL.createObjectURL(values.file)
        : "";
      if (localUrl) demoObjectUrls.current.add(localUrl);
      const mediaUrl = localUrl
        || artwork?.preview_url
        || existingMedia?.mediaUrl
        || editingItem?.mediaUrl
        || "/community/miniature-fashion.png";
      const mediaType = values.file?.type?.startsWith("video/")
        ? "video"
        : artwork?.media_type || existingMedia?.mediaType || editingItem?.mediaType || "image";
      const mediaId = values.file || artifactId
        ? demoId()
        : values.mediaId || editingItem?.mediaId || demoId();
      return performDemo("save", (current) => {
        const currentItems = current.draft.items;
        const nextItem = {
          ...(editingItem || {}),
          id: editingItem?.id || demoId(),
          mediaId,
          mediaType,
          mediaUrl,
          posterUrl: "",
          title: String(values.title || "").trim(),
          section: values.section,
          category: values.category,
          altText: String(values.altText || "").trim(),
          publicPrompt: String(values.publicPrompt || "").trim(),
          aspectRatio: values.aspectRatio,
          aspect: demoAspect(values.aspectRatio),
          isHero: values.isHero === true,
          sortOrder: editingItem?.sortOrder ?? currentItems.length,
          status: "draft",
          sourceLabel: artifactId
            ? "本人作品导入（演示）"
            : values.mediaSource === "existing"
              ? "已上传媒体复用（演示）"
              : "本地上传（演示）",
          updatedAt: new Date().toISOString(),
        };
        const replaced = editingItem
          ? currentItems.map((entry) => entry.id === editingItem.id ? nextItem : entry)
          : [...currentItems, nextItem];
        const items = replaced.map((entry, index) => ({
          ...entry,
          isHero: nextItem.isHero && entry.id !== nextItem.id ? false : entry.isHero,
          sortOrder: index,
        }));
        const media = current.media?.some((entry) => entry.id === mediaId)
          ? current.media
          : [{
              id: mediaId,
              sourceTaskArtifactId: artifactId,
              filename: values.file?.name || artwork?.model_display_name || `${nextItem.title}.${mediaType === "video" ? "mp4" : "png"}`,
              mediaType,
              contentType: values.file?.type || (mediaType === "video" ? "video/mp4" : "image/png"),
              sizeBytes: values.file?.size || 0,
              sha256: "",
              mediaUrl,
              createdAt: new Date().toISOString(),
            }, ...(current.media || [])];
        return {
          ...current,
          media,
          draft: {
            ...current.draft,
            version: current.draft.version + 1,
            updatedAt: new Date().toISOString(),
            changed: true,
            items,
          },
        };
      }, editingItem ? "演示案例草稿已更新，未写入 Platform" : "演示案例已加入草稿，未写入 Platform");
    }
    const fileSignature = values.file
      ? `${values.file.name}:${values.file.size}:${values.file.type}:${values.file.lastModified}`
      : "";
    const sourceSignature = artifactId ? `artifact:${artifactId}` : `file:${fileSignature}`;
    const mediaKey = values.file || artifactId
      ? stableOperationKey(mediaOperation, "showcase-media", sourceSignature)
      : "";
    const saved = await perform("save", async () => {
      const media = values.file
        ? await client.uploadAdminShowcaseMedia(
            { file: values.file },
            { idempotencyKey: mediaKey },
          )
        : artifactId
          ? await client.uploadAdminShowcaseMedia(
              { sourceTaskArtifactId: artifactId },
              { idempotencyKey: mediaKey },
            )
          : { id: values.mediaId || editingItem?.mediaId };
      const payload = showcaseMutationPayload(
        { ...values, sortOrder: editingItem?.sortOrder ?? snapshot.draft.items.length },
        media,
        snapshot.draft.version,
      );
      if (editingItem) {
        await client.updateAdminShowcaseItem(editingItem.id, payload);
      } else {
        await client.createAdminShowcaseItem(payload);
      }
    }, editingItem ? "案例草稿已更新" : "案例已加入草稿");
    if (saved) mediaOperation.current = null;
    return saved;
  }, [client, demoMode, perform, performDemo, snapshot]);

  const move = useCallback(async (item, direction) => {
    const reordered = reorderedShowcaseItems(snapshot?.draft?.items, item.id, direction);
    if (demoMode) {
      return performDemo("reorder", (current) => ({
        ...current,
        draft: {
          ...current.draft,
          version: current.draft.version + 1,
          updatedAt: new Date().toISOString(),
          changed: true,
          items: reordered,
        },
      }), "演示草稿顺序已更新，未写入 Platform");
    }
    return perform("reorder", () => client.reorderAdminShowcaseItems({
      expectedDraftVersion: snapshot.draft.version,
      itemIds: reordered.map((entry) => entry.id),
    }), "草稿顺序已更新");
  }, [client, demoMode, perform, performDemo, snapshot]);

  const retire = useCallback((item) => {
    if (demoMode) {
      return performDemo("retire", (current) => ({
        ...current,
        draft: {
          ...current.draft,
          version: current.draft.version + 1,
          updatedAt: new Date().toISOString(),
          changed: true,
          items: current.draft.items
            .filter((entry) => entry.id !== item.id)
            .map((entry, index) => ({ ...entry, sortOrder: index })),
        },
      }), "演示案例已从草稿撤下，未写入 Platform");
    }
    return perform(
      "retire",
      () => client.retireAdminShowcaseItem(item.id, {
        expectedDraftVersion: snapshot.draft.version,
      }),
      "案例已从草稿撤下，线上版本尚未改变",
    );
  }, [client, demoMode, perform, performDemo, snapshot]);

  const publish = useCallback(async (releaseNote) => {
    if (demoMode) {
      return performDemo("publish", (current) => {
        const version = Math.max(
          0,
          ...current.releases.map((release) => Number(release.version) || 0),
        ) + 1;
        const items = copyItems(current.draft.items);
        const release = {
          id: demoId(),
          version: String(version),
          note: releaseNote,
          publishedAt: new Date().toISOString(),
          publishedBy: "周宁（演示）",
          itemCount: items.length,
          items,
        };
        return {
          ...current,
          publicationVersion: current.publicationVersion + 1,
          draft: { ...current.draft, changed: false },
          liveRelease: release,
          releases: [release, ...current.releases],
        };
      }, "演示版本已发布到内存，未写入 Platform");
    }
    const signature = `${snapshot.draft.version}:${snapshot.publicationVersion}:${releaseNote}`;
    const idempotencyKey = stableOperationKey(
      publishOperation,
      "showcase-publish",
      signature,
    );
    const published = await perform(
      "publish",
      () => client.publishAdminShowcase({
        expectedDraftVersion: snapshot.draft.version,
        expectedPublicationVersion: snapshot.publicationVersion,
        releaseNote,
        idempotencyKey,
      }),
      "首页精选案例已经发布",
    );
    if (published) publishOperation.current = null;
    return published;
  }, [client, demoMode, perform, performDemo, snapshot]);

  const unpublish = useCallback(async (releaseNote) => {
    if (demoMode) {
      return performDemo("unpublish", (current) => {
        const nextPublicationVersion = current.publicationVersion + 1;
        const unpublishedAt = new Date().toISOString();
        const event = {
          id: demoId(),
          previousReleaseId: current.liveRelease?.id || "",
          previousReleaseVersion: current.liveRelease?.version || "",
          publicationVersion: nextPublicationVersion,
          note: releaseNote,
          actor: "周宁（演示）",
          unpublishedAt,
        };
        return {
          ...current,
          publicationVersion: nextPublicationVersion,
          draft: { ...current.draft, changed: true },
          liveRelease: null,
          lastUnpublishedEvent: event,
          publicationEvents: [event, ...(current.publicationEvents || [])],
        };
      }, "演示首页已在内存中下线，草稿与历史均已保留");
    }
    const signature = `${snapshot.draft.version}:${snapshot.publicationVersion}:${snapshot.liveRelease?.id || ""}:${releaseNote}`;
    const idempotencyKey = stableOperationKey(
      unpublishOperation,
      "showcase-unpublish",
      signature,
    );
    const unpublished = await perform(
      "unpublish",
      () => client.unpublishAdminShowcase({
        expectedDraftVersion: snapshot.draft.version,
        expectedPublicationVersion: snapshot.publicationVersion,
        releaseNote,
        idempotencyKey,
      }),
      "当前首页已下线；公开页面约 30 秒内回到内置示例，已签发媒体地址最长可能继续有效 5 分钟，已下载内容无法收回",
    );
    if (unpublished) unpublishOperation.current = null;
    return unpublished;
  }, [client, demoMode, perform, performDemo, snapshot]);

  const rollback = useCallback(async (release, releaseNote) => {
    if (demoMode) {
      return performDemo("rollback", (current) => {
        const items = copyItems(release.items);
        const version = Math.max(
          0,
          ...current.releases.map((entry) => Number(entry.version) || 0),
        ) + 1;
        const rollbackRelease = {
          id: demoId(),
          version: String(version),
          note: releaseNote,
          publishedAt: new Date().toISOString(),
          publishedBy: "周宁（演示）",
          itemCount: items.length,
          items,
        };
        return {
          ...current,
          publicationVersion: current.publicationVersion + 1,
          draft: {
            ...current.draft,
            changed: !showcaseManifestsEqual(current.draft.items, items),
          },
          liveRelease: rollbackRelease,
          releases: [rollbackRelease, ...current.releases],
        };
      }, `演示环境已根据版本 ${release.version} 创建内存回滚版本`);
    }
    const signature = `${release.id}:${snapshot.draft.version}:${snapshot.publicationVersion}:${releaseNote}`;
    const idempotencyKey = stableOperationKey(
      rollbackOperation,
      "showcase-rollback",
      signature,
    );
    const rolledBack = await perform(
      "rollback",
      () => client.rollbackAdminShowcaseRelease(release.id, {
        expectedDraftVersion: snapshot.draft.version,
        expectedPublicationVersion: snapshot.publicationVersion,
        releaseNote,
        idempotencyKey,
      }),
      `已根据版本 ${release.version} 创建新的线上版本`,
    );
    if (rolledBack) rollbackOperation.current = null;
    return rolledBack;
  }, [client, demoMode, perform, performDemo, snapshot]);

  return (
    <ShowcaseOperationsScreen
      snapshot={snapshot}
      loading={loading}
      error={error}
      notice={notice}
      busyAction={busyAction}
      demoMode={demoMode}
      ownedArtworks={ownedArtworks}
      ownedArtworksLoading={ownedArtworksLoading}
      ownedArtworksError={ownedArtworksError}
      onReload={reload}
      onReloadOwnedArtworks={reloadOwnedArtworks}
      onSave={save}
      onMove={move}
      onRetire={retire}
      onPublish={publish}
      onUnpublish={unpublish}
      onRollback={rollback}
    />
  );
}
