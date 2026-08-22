import {
  COMMUNITY_CATEGORIES,
  COMMUNITY_SECTIONS,
} from "../../communityFeed.js";

const MEDIA_TYPES = new Set(["image", "video"]);
const DIRECT_UPLOAD_MEDIA_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);
const ASPECT_RATIOS = new Set(["auto", "1:1", "3:4", "4:3", "9:16", "16:9"]);
const SECTION_FROM_API = {
  video: "视频",
  template: "模板",
  challenge: "挑战",
};
const SECTION_TO_API = {
  视频: "video",
  模板: "template",
  挑战: "challenge",
};
const CANONICAL_UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/iu;
const MANIFEST_FIELDS = [
  "mediaId",
  "title",
  "section",
  "category",
  "altText",
  "publicPrompt",
  "aspectRatio",
  "isHero",
  "sortOrder",
];

function aspectClass(value) {
  if (["16:9", "4:3"].includes(value)) return "landscape";
  if (value === "3:4") return "portrait";
  if (value === "9:16") return "tall";
  return "square";
}

function text(value) {
  return String(value || "").trim();
}

function integer(value, fallback = 0) {
  const parsed = Number(value);
  return Number.isInteger(parsed) ? parsed : fallback;
}

function normalizeItem(source = {}, index = 0) {
  const media = source.media && typeof source.media === "object" ? source.media : {};
  const mediaType = text(source.media_type || media.media_type || media.type || "image").toLowerCase();
  const ratio = text(source.aspect_ratio || "auto").toLowerCase();
  const aspectRatio = ASPECT_RATIOS.has(ratio) ? ratio : "auto";
  const sourceSection = text(source.section);
  return {
    id: text(source.id || source.item_id || `draft-${index}`),
    mediaId: text(source.media_id || media.id),
    mediaType: MEDIA_TYPES.has(mediaType) ? mediaType : "image",
    mediaUrl: text(
      source.media_url
        || source.content_url
        || source.public_url
        || source.url
        || media.content_url
        || media.public_url
        || media.url,
    ),
    posterUrl: text(
      source.poster_url || source.cover_url || media.poster_url || media.cover_url,
    ),
    title: text(source.title),
    section: COMMUNITY_SECTIONS.includes(sourceSection)
      ? sourceSection
      : SECTION_FROM_API[sourceSection] || "视频",
    category: COMMUNITY_CATEGORIES.includes(source.category)
      && source.category !== "全部"
      ? source.category
      : "风格艺术",
    altText: text(source.alt_text || source.alt),
    publicPrompt: text(source.public_prompt || source.prompt),
    aspectRatio,
    aspect: aspectClass(aspectRatio),
    isHero: source.is_hero === true || source.placement === "hero",
    sortOrder: integer(source.sort_order, index),
    status: text(source.status || (source.retired_at ? "retired" : "draft")).toLowerCase(),
    sourceLabel: text(source.source_label || source.source || "后台上传"),
    updatedAt: source.updated_at || null,
  };
}

function normalizeRelease(source = {}, index = 0) {
  const items = Array.isArray(source.items)
    ? source.items.map(normalizeItem)
    : [];
  const explicitItemCount = Number(source.item_count);
  return {
    id: text(source.id || source.release_id || `release-${index}`),
    version: text(source.version || source.release_version || index + 1),
    note: text(source.release_note || source.note),
    publishedAt: source.published_at || source.created_at || null,
    publishedBy: text(
      source.published_by_name
        || source.published_by
        || source.actor_name
        || source.published_by_user_id,
    ),
    itemCount: Number.isInteger(explicitItemCount)
      ? explicitItemCount
      : items.length
        ? items.length
        : null,
    items,
  };
}

function normalizeUnpublishedEvent(source, releases) {
  if (!source || typeof source !== "object" || Array.isArray(source)) return null;
  const previousReleaseId = text(source.previous_release_id);
  const previousRelease = releases.find((release) => release.id === previousReleaseId);
  return {
    id: text(source.id),
    previousReleaseId,
    previousReleaseVersion: text(
      source.previous_release_version || previousRelease?.version,
    ),
    publicationVersion: integer(source.publication_version, 0),
    note: text(source.release_note || source.note),
    actor: text(
      source.actor_name
        || source.actor_user_name
        || source.actor_user_id
        || source.published_by_user_id,
    ),
    unpublishedAt: source.unpublished_at || source.created_at || null,
  };
}

function normalizeMedia(source = {}, index = 0) {
  const mediaType = text(source.media_type || "image").toLowerCase();
  return {
    id: text(source.id || `media-${index}`),
    sourceTaskArtifactId: text(source.source_task_artifact_id),
    filename: text(source.original_filename || source.filename || "已上传媒体"),
    mediaType: MEDIA_TYPES.has(mediaType) ? mediaType : "image",
    contentType: text(source.content_type),
    sizeBytes: Math.max(0, integer(source.size_bytes, 0)),
    sha256: text(source.sha256),
    mediaUrl: text(source.content_url || source.media_url || source.url),
    createdAt: source.created_at || null,
  };
}

export function normalizeAdminShowcase(payload) {
  const source = payload && typeof payload === "object" && !Array.isArray(payload)
    ? payload
    : {};
  const draft = source.draft && typeof source.draft === "object"
    ? source.draft
    : source;
  const rawItems = Array.isArray(draft.items)
    ? draft.items
    : Array.isArray(source.items)
      ? source.items
      : [];
  const items = rawItems
    .map(normalizeItem)
    .filter((item) => item.status !== "retired")
    .sort((left, right) => left.sortOrder - right.sortOrder);
  const rawLive = source.live_release || source.current_release || source.published_release;
  const liveRelease = rawLive && typeof rawLive === "object"
    ? normalizeRelease(rawLive)
    : null;
  const releases = (Array.isArray(source.releases) ? source.releases : [])
    .map(normalizeRelease);
  const rawPublicationEvents = Array.isArray(source.publication_events)
    ? [...source.publication_events]
    : [];
  if (source.last_unpublished_event && !rawPublicationEvents.some((event) => (
    text(event?.id) && text(event?.id) === text(source.last_unpublished_event?.id)
  ))) {
    rawPublicationEvents.push(source.last_unpublished_event);
  }
  const publicationEvents = rawPublicationEvents
    .map((event) => normalizeUnpublishedEvent(
      event,
      liveRelease ? [liveRelease, ...releases] : releases,
    ))
    .filter(Boolean)
    .sort((left, right) => (
      new Date(right.unpublishedAt || 0).getTime()
      - new Date(left.unpublishedAt || 0).getTime()
    ));
  return {
    publicationVersion: integer(source.publication_version, 0),
    draft: {
      version: integer(draft.version ?? source.draft_version, 0),
      updatedAt: draft.updated_at || source.draft_updated_at || null,
      changed: typeof draft.changed === "boolean"
        ? draft.changed
        : typeof source.has_unpublished_changes === "boolean"
          ? source.has_unpublished_changes
          : integer(draft.version ?? source.draft_version, 0)
            !== integer(rawLive?.draft_version, -1),
      items,
    },
    media: (Array.isArray(source.media) ? source.media : []).map(normalizeMedia),
    liveRelease,
    lastUnpublishedEvent: publicationEvents[0] || null,
    publicationEvents,
    releases,
  };
}

export function validateShowcaseDraft(values, _options = {}) {
  const title = text(values.title);
  const altText = text(values.altText);
  const file = values.file || null;
  const sourceTaskArtifactId = text(values.sourceTaskArtifactId);
  if (!title) return "请填写案例标题。";
  if (title.length > 80) return "案例标题不能超过 80 个字符。";
  if (!altText) return "请填写图片或视频的替代说明。";
  if (altText.length > 200) return "替代说明不能超过 200 个字符。";
  if (values.mediaSource === "artifact" && !sourceTaskArtifactId) {
    return "请选择本人作品，或填写该作品的 Artifact ID。";
  }
  if (values.mediaSource === "existing" && !text(values.mediaId)) {
    return "请选择一条已上传媒体。";
  }
  if (sourceTaskArtifactId && !CANONICAL_UUID.test(sourceTaskArtifactId)) {
    return "Artifact ID 格式无效，请勿粘贴媒体网址。";
  }
  if (file && !DIRECT_UPLOAD_MEDIA_TYPES.has(String(file.type || "").toLowerCase())) {
    return "本地上传仅支持 JPEG、PNG 或 WebP 图片；视频请从本人已验证作品导入。";
  }
  if (!file && !sourceTaskArtifactId && !values.mediaId) {
    return "请选择本地媒体，或填写本人已验证作品的 Artifact ID。";
  }
  if (values.isHero === true && values.section !== "视频") {
    return "首页头图必须放在“视频”分区。";
  }
  if (!COMMUNITY_SECTIONS.includes(values.section)) return "请选择有效的首页分区。";
  if (!COMMUNITY_CATEGORIES.includes(values.category) || values.category === "全部") {
    return "请选择有效的案例分类。";
  }
  return "";
}

export function showcaseManifestsEqual(leftItems, rightItems) {
  const manifest = (items) => (items || []).map((item) => (
    Object.fromEntries(MANIFEST_FIELDS.map((field) => [field, item?.[field] ?? null]))
  ));
  return JSON.stringify(manifest(leftItems)) === JSON.stringify(manifest(rightItems));
}

export function showcaseMutationPayload(values, media, expectedDraftVersion) {
  return {
    expectedDraftVersion,
    media_id: text(media?.id || media?.media_id || values.mediaId),
    title: text(values.title),
    section: SECTION_TO_API[values.section] || "video",
    category: values.category,
    alt_text: text(values.altText),
    public_prompt: text(values.publicPrompt),
    aspect_ratio: ASPECT_RATIOS.has(values.aspectRatio) ? values.aspectRatio : "auto",
    is_hero: values.isHero === true,
    sort_order: integer(values.sortOrder, 0),
  };
}

export function reorderedShowcaseItems(items, itemId, direction) {
  const next = [...(items || [])];
  const currentIndex = next.findIndex((item) => item.id === itemId);
  const targetIndex = currentIndex + direction;
  if (currentIndex < 0 || targetIndex < 0 || targetIndex >= next.length) return next;
  [next[currentIndex], next[targetIndex]] = [next[targetIndex], next[currentIndex]];
  return next.map((item, index) => ({ ...item, sortOrder: index }));
}

export function showcaseDateTime(value) {
  const date = new Date(value || "");
  if (!Number.isFinite(date.getTime())) return "尚未记录";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}
