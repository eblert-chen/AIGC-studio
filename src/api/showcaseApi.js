function requireDraftVersion(value) {
  const version = Number(value);
  if (!Number.isInteger(version) || version < 0) {
    throw new TypeError("expectedDraftVersion must be a non-negative integer");
  }
  return version;
}

function releaseReason(value) {
  return String(value || "").trim();
}

const CANONICAL_UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/iu;
const DIRECT_UPLOAD_MEDIA_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);

function showcaseItemBody(source, expectedDraftVersion) {
  return {
    media_id: source.media_id,
    title: source.title,
    section: source.section,
    category: source.category,
    alt_text: source.alt_text,
    public_prompt: source.public_prompt || "",
    aspect_ratio: source.aspect_ratio || "auto",
    is_hero: source.is_hero === true,
    sort_order: source.sort_order,
    expected_draft_version: requireDraftVersion(expectedDraftVersion),
  };
}

function resolveMedia(media, resolvePlatformUrl) {
  if (!media || typeof media !== "object" || Array.isArray(media)) return media;
  const next = { ...media };
  if (media.content_url) next.content_url = resolvePlatformUrl(media.content_url);
  if (media.media_url) next.media_url = resolvePlatformUrl(media.media_url);
  return next;
}

function resolveItem(item, resolvePlatformUrl) {
  if (!item || typeof item !== "object" || Array.isArray(item)) return item;
  const next = resolveMedia(item, resolvePlatformUrl);
  if (item.media) next.media = resolveMedia(item.media, resolvePlatformUrl);
  return next;
}

function resolveAdminPayload(payload, resolvePlatformUrl) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return payload;
  return {
    ...payload,
    items: Array.isArray(payload.items)
      ? payload.items.map((item) => resolveItem(item, resolvePlatformUrl))
      : payload.items,
    media: Array.isArray(payload.media)
      ? payload.media.map((media) => resolveMedia(media, resolvePlatformUrl))
      : payload.media,
  };
}

function resolveHomePayload(payload, resolvePlatformUrl) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return payload;
  return {
    ...payload,
    hero: resolveItem(payload.hero, resolvePlatformUrl),
    items: Array.isArray(payload.items)
      ? payload.items.map((item) => resolveItem(item, resolvePlatformUrl))
      : payload.items,
  };
}

export function createShowcaseApi(core) {
  const {
    request,
    makeRequestId,
    resolvePlatformUrl,
    PlatformApiError,
  } = core;

  return {
    getHomeShowcase: async ({ etag = "", signal } = {}) => {
      const response = await request("/api/v1/showcase/home", {
        signal,
        companyContext: false,
        etag,
        acceptNotModified: true,
        includeResponseMetadata: true,
      });
      return response?.notModified
        ? response
        : {
            ...response,
            data: resolveHomePayload(response?.data, resolvePlatformUrl),
          };
    },
    getAdminShowcase: async ({ signal } = {}) => {
      const payload = await request("/api/v1/platform-admin/showcase", {
        signal,
        companyContext: false,
      });
      return resolveAdminPayload(payload, resolvePlatformUrl);
    },
    uploadAdminShowcaseMedia: async (
      source,
      { signal, idempotencyKey } = {},
    ) => {
      if (typeof FormData === "undefined") {
        throw new PlatformApiError("当前浏览器不支持文件上传", {
          code: "FORM_DATA_UNAVAILABLE",
        });
      }
      const structured = source && typeof source === "object"
        && !(typeof Blob !== "undefined" && source instanceof Blob)
        ? source
        : { file: source };
      const file = structured.file || null;
      const sourceTaskArtifactId = String(
        structured.sourceTaskArtifactId || structured.source_task_artifact_id || "",
      ).trim();
      if (Boolean(file) === Boolean(sourceTaskArtifactId)) {
        throw new PlatformApiError("请选择一个本地文件或一个本人作品 Artifact ID", {
          code: "SHOWCASE_MEDIA_REQUIRED",
        });
      }
      if (sourceTaskArtifactId && !CANONICAL_UUID.test(sourceTaskArtifactId)) {
        throw new PlatformApiError("本人作品 Artifact ID 格式无效，不能使用媒体网址", {
          code: "INVALID_SHOWCASE_ARTIFACT_ID",
        });
      }
      if (file && !DIRECT_UPLOAD_MEDIA_TYPES.has(String(file.type || "").toLowerCase())) {
        throw new PlatformApiError("本地上传仅支持 JPEG、PNG 或 WebP 图片；视频请从本人已验证作品导入", {
          code: "UNSUPPORTED_SHOWCASE_DIRECT_UPLOAD",
        });
      }
      const form = new FormData();
      if (file) {
        form.append("file", file, file?.name || "showcase-upload.bin");
      } else {
        form.append("source_task_artifact_id", sourceTaskArtifactId);
      }
      const stableIdempotencyKey = idempotencyKey || makeRequestId();
      const media = await request("/api/v1/platform-admin/showcase/media", {
        method: "POST",
        body: form,
        idempotencyKey: stableIdempotencyKey,
        signal,
        timeoutMs: 5 * 60_000,
        companyContext: false,
      });
      return resolveMedia(media, resolvePlatformUrl);
    },
    createAdminShowcaseItem: (
      { expectedDraftVersion, ...fields },
      { signal } = {},
    ) => request("/api/v1/platform-admin/showcase/items", {
      method: "POST",
      body: showcaseItemBody(fields, expectedDraftVersion),
      signal,
      companyContext: false,
    }).then((payload) => ({
      ...payload,
      item: resolveItem(payload?.item, resolvePlatformUrl),
    })),
    updateAdminShowcaseItem: (
      itemId,
      { expectedDraftVersion, ...fields },
      { signal } = {},
    ) => request(
      `/api/v1/platform-admin/showcase/items/${encodeURIComponent(itemId)}`,
      {
        method: "PUT",
        body: showcaseItemBody(fields, expectedDraftVersion),
        signal,
        companyContext: false,
      },
    ).then((payload) => ({
      ...payload,
      item: resolveItem(payload?.item, resolvePlatformUrl),
    })),
    reorderAdminShowcaseItems: (
      { expectedDraftVersion, itemIds },
      { signal } = {},
    ) => request("/api/v1/platform-admin/showcase/order", {
      method: "PUT",
      body: {
        expected_draft_version: requireDraftVersion(expectedDraftVersion),
        item_ids: Array.isArray(itemIds) ? itemIds.map(String) : [],
      },
      signal,
      companyContext: false,
    }),
    retireAdminShowcaseItem: (
      itemId,
      { expectedDraftVersion },
      { signal } = {},
    ) => request(
      `/api/v1/platform-admin/showcase/items/${encodeURIComponent(itemId)}/retire`,
      {
        method: "POST",
        body: {
          expected_draft_version: requireDraftVersion(expectedDraftVersion),
        },
        signal,
        companyContext: false,
      },
    ),
    publishAdminShowcase: (
      {
        expectedDraftVersion,
        expectedPublicationVersion,
        releaseNote,
        idempotencyKey,
      },
      { signal } = {},
    ) => {
      const stableIdempotencyKey = idempotencyKey || makeRequestId();
      return request("/api/v1/platform-admin/showcase/publish", {
        method: "POST",
        body: {
          expected_draft_version: requireDraftVersion(expectedDraftVersion),
          expected_publication_version: requireDraftVersion(expectedPublicationVersion),
          release_note: releaseReason(releaseNote),
        },
        idempotencyKey: stableIdempotencyKey,
        signal,
        companyContext: false,
      });
    },
    unpublishAdminShowcase: (
      {
        expectedDraftVersion,
        expectedPublicationVersion,
        releaseNote,
        idempotencyKey,
      },
      { signal } = {},
    ) => {
      const stableIdempotencyKey = idempotencyKey || makeRequestId();
      return request("/api/v1/platform-admin/showcase/unpublish", {
        method: "POST",
        body: {
          expected_draft_version: requireDraftVersion(expectedDraftVersion),
          expected_publication_version: requireDraftVersion(expectedPublicationVersion),
          release_note: releaseReason(releaseNote),
        },
        idempotencyKey: stableIdempotencyKey,
        signal,
        companyContext: false,
      });
    },
    rollbackAdminShowcaseRelease: (
      releaseId,
      {
        expectedDraftVersion,
        expectedPublicationVersion,
        releaseNote,
        idempotencyKey,
      },
      { signal } = {},
    ) => {
      const stableIdempotencyKey = idempotencyKey || makeRequestId();
      return request(
        `/api/v1/platform-admin/showcase/releases/${encodeURIComponent(releaseId)}/rollback`,
        {
          method: "POST",
          body: {
            expected_draft_version: requireDraftVersion(expectedDraftVersion),
            expected_publication_version: requireDraftVersion(expectedPublicationVersion),
            release_note: releaseReason(releaseNote),
          },
          idempotencyKey: stableIdempotencyKey,
          signal,
          companyContext: false,
        },
      );
    },
  };
}
