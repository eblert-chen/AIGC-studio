export const COMMUNITY_SECTIONS = ["视频", "模板", "挑战"];

export const COMMUNITY_CATEGORIES = [
  "全部",
  "广告魔法",
  "电影叙事",
  "风格艺术",
  "动漫剧场",
  "数字人",
  "教育学习",
  "商品展示",
];

export const COMMUNITY_FEED_ITEMS = [
  {
    id: "cyber-fashion",
    title: "冷光未来时装",
    section: "视频",
    category: "数字人",
    image: "/community/cyber-fashion-portrait.png",
    alt: "青色短发的未来时装人物肖像",
    aspect: "portrait",
    prompt: "黑色摄影棚内的未来时装人物肖像，冷青色轮廓光与银色主光，细腻皮肤质感，商业大片构图，缓慢推近镜头。",
  },
  {
    id: "anime-street",
    title: "雨夜街头双人秀",
    section: "视频",
    category: "动漫剧场",
    image: "/community/neon-anime-street.png",
    alt: "雨后霓虹街道上的两位三维动漫角色",
    aspect: "tall",
    prompt: "雨后城市街头，两位原创 3D 动漫潮流角色并肩走来，青色与暖橙电影灯光，湿地反射，低机位广角跟拍。",
  },
  {
    id: "speaker-water",
    title: "水幕中的产品力量",
    section: "视频",
    category: "商品展示",
    image: "/media/speaker-water-hero.png",
    alt: "水花环绕的黑色便携音箱",
    aspect: "landscape",
    prompt: "黑色便携音箱置于雨幕与水花中，硬朗侧光突出材质和防水性能，高速水滴慢动作，电影广告质感。",
  },
  {
    id: "indoor-story",
    title: "室内静谧叙事",
    section: "模板",
    category: "电影叙事",
    image: "/media/scene-indoor.png",
    alt: "暖色室内生活场景",
    aspect: "square",
    prompt: "安静的现代室内生活场景，午后自然光穿过窗帘，镜头缓慢横移，细微生活动作，克制的电影叙事。",
  },
  {
    id: "rain-detail",
    title: "一镜到底防水演示",
    section: "模板",
    category: "广告魔法",
    image: "/media/scene-hand-rain.png",
    alt: "手持产品的雨天细节画面",
    aspect: "portrait",
    prompt: "雨天手持产品的近景细节，一镜到底从水滴特写拉到使用动作，真实湿润质感，适合短视频商品演示。",
  },
  {
    id: "lifestyle-cut",
    title: "城市生活节奏",
    section: "挑战",
    category: "风格艺术",
    image: "/media/scene-lifestyle.png",
    alt: "城市生活方式短片场景",
    aspect: "tall",
    prompt: "城市生活方式短片，人物自然行走与停留，手持跟拍和快速匹配剪辑交替，黑青色调，节奏明快。",
  },
  {
    id: "ice-expedition",
    title: "冰原尺度感",
    section: "视频",
    category: "电影叙事",
    image: "/community/ice-landscape.png",
    alt: "巨型冰川前行进的远景小队",
    aspect: "landscape",
    prompt: "巨大冰川与雪原的史诗远景，小队缓慢向冰壁前进，冰青色侧光与风雪薄雾，镜头平稳推进，强调宏大尺度。",
  },
  {
    id: "miniature-fashion",
    title: "微缩时装片场",
    section: "视频",
    category: "广告魔法",
    image: "/community/miniature-fashion.png",
    alt: "现代室内的微缩比例时装人物",
    aspect: "portrait",
    prompt: "暖色现代室内，一位夸张微缩比例的时装人物从房间中央走向镜头，真实布料与皮肤质感，轻微广角，商业短片节奏。",
  },
  {
    id: "blue-editorial",
    title: "蓝银妆容特写",
    section: "挑战",
    category: "数字人",
    image: "/community/blue-editorial-portrait.png",
    alt: "蓝银发与闪粉妆容的棚拍人物肖像",
    aspect: "portrait",
    prompt: "浅灰摄影棚里的蓝银发人物特写，微妙蓝色闪粉妆和银饰，柔和主光配冷色轮廓光，镜头缓慢环绕，时尚商业人像。",
  },
];

const COMMUNITY_ASPECTS = new Set(["landscape", "portrait", "square", "tall"]);
const COMMUNITY_MEDIA_TYPES = new Set(["image", "video"]);
const COMMUNITY_SECTION_FROM_API = {
  video: "视频",
  template: "模板",
  challenge: "挑战",
};

function safePublicMediaUrl(value) {
  const raw = String(value || "").trim();
  if (!raw || raw.startsWith("//")) return "";
  if (raw.startsWith("/")) return raw;
  try {
    const url = new URL(raw);
    const loopback = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
    if (
      url.username
      || url.password
      || (url.protocol !== "https:" && !(loopback && url.protocol === "http:"))
    ) return "";
    return url.href;
  } catch {
    return "";
  }
}

function aspectName(value) {
  const normalized = String(value || "").trim().toLowerCase();
  if (COMMUNITY_ASPECTS.has(normalized)) return normalized;
  if (["16:9", "3:2", "4:3"].includes(normalized)) return "landscape";
  if (["9:16", "2:3"].includes(normalized)) return "tall";
  if (["4:5", "3:4"].includes(normalized)) return "portrait";
  return "square";
}

function normalizeCommunityItem(source, index = 0) {
  if (!source || typeof source !== "object" || Array.isArray(source)) return null;
  const media = source.media && typeof source.media === "object" ? source.media : {};
  const mediaType = String(
    source.media_type || media.media_type || media.type || "image",
  ).trim().toLowerCase();
  if (!COMMUNITY_MEDIA_TYPES.has(mediaType)) return null;
  const mediaUrl = safePublicMediaUrl(
    source.media_url
      || source.public_url
      || source.url
      || media.public_url
      || media.url
      || source.image,
  );
  if (!mediaUrl) return null;
  const title = String(source.title || "").trim();
  const alt = String(source.alt_text || source.alt || title).trim();
  if (!title || !alt) return null;
  const section = COMMUNITY_SECTIONS.includes(source.section)
    ? source.section
    : COMMUNITY_SECTION_FROM_API[source.section] || "视频";
  const category = COMMUNITY_CATEGORIES.includes(source.category)
    && source.category !== "全部"
    ? source.category
    : "风格艺术";
  const id = String(source.id || source.item_id || `showcase-${index}`).trim();
  return {
    id,
    title,
    section,
    category,
    image: mediaUrl,
    mediaUrl,
    mediaType,
    poster: safePublicMediaUrl(
      source.poster_url
        || source.cover_url
        || media.poster_url
        || media.cover_url,
    ),
    alt,
    aspect: aspectName(source.aspect || source.aspect_ratio || media.aspect_ratio),
    prompt: String(source.public_prompt || source.prompt || "").trim(),
    description: String(source.description || "").trim(),
  };
}

export const COMMUNITY_FALLBACK_HERO = {
  id: "community-hero",
  title: "把声音，变成画面",
  description: "从一个制作说明开始，让模型能力自动适配你的素材、时长与画幅。",
  mediaType: "image",
  mediaUrl: "/community/community-hero.png",
  image: "/community/community-hero.png",
  poster: "",
  alt: "深黑空间中的银蓝色声波艺术装置",
  aspect: "landscape",
  prompt: "",
};

export const COMMUNITY_FALLBACK_FEED = {
  releaseId: "bundled-fallback",
  version: "bundled",
  publishedAt: null,
  hero: COMMUNITY_FALLBACK_HERO,
  items: COMMUNITY_FEED_ITEMS.map((item) => ({
    ...item,
    mediaType: "image",
    mediaUrl: item.image,
    poster: "",
  })),
};

export function normalizeHomeShowcase(payload) {
  const source = payload?.home && typeof payload.home === "object"
    ? payload.home
    : payload;
  if (!source || typeof source !== "object" || Array.isArray(source)) return null;
  const rawItems = Array.isArray(source.items)
    ? source.items
    : Array.isArray(source.release?.items)
      ? source.release.items
      : [];
  const items = rawItems
    .map((item, index) => normalizeCommunityItem(item, index))
    .filter(Boolean);
  const rawHero = source.hero
    || rawItems.find((item) => item?.is_hero === true || item?.placement === "hero");
  const hero = rawHero ? normalizeCommunityItem(rawHero, -1) : null;
  return {
    releaseId: String(
      source.release_id || source.id || source.release?.id || "",
    ).trim(),
    version: String(
      source.version || source.release_version || source.release?.version || "",
    ).trim(),
    publishedAt: source.published_at || source.release?.published_at || null,
    hero,
    items: hero
      ? items.filter((item) => item.id !== hero.id)
      : items,
  };
}

export function hasPublishedHomeShowcase(feed) {
  return Boolean(feed?.releaseId || feed?.hero || feed?.items?.length);
}

export function reconcileHomeShowcaseFeed(currentFeed, candidateFeed) {
  if (hasPublishedHomeShowcase(candidateFeed)) return candidateFeed;
  return COMMUNITY_FALLBACK_FEED;
}

export function communityCategories(items) {
  const available = new Set((items || []).map((item) => item.category));
  return COMMUNITY_CATEGORIES.filter((category) => (
    category === "全部" || available.has(category)
  ));
}

export function filterCommunityItems(items, section, category) {
  return items.filter((item) => (
    (section === "视频" || item.section === section) &&
    (category === "全部" || item.category === category)
  ));
}

export function communityColumnCount(viewportWidth) {
  if (viewportWidth >= 1640) return 5;
  if (viewportWidth >= 1180) return 4;
  if (viewportWidth >= 820) return 3;
  if (viewportWidth >= 560) return 2;
  return 1;
}
