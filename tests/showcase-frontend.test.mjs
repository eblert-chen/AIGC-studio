import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { createPlatformClient, PlatformApiError } from "../src/api/platformClient.js";
import {
  COMMUNITY_FALLBACK_FEED,
  hasPublishedHomeShowcase,
  normalizeHomeShowcase,
  reconcileHomeShowcaseFeed,
} from "../src/communityFeed.js";
import {
  normalizeAdminShowcase,
  reorderedShowcaseItems,
  showcaseManifestsEqual,
  showcaseMutationPayload,
  validateShowcaseDraft,
} from "../src/admin/showcase/showcaseModel.js";
import {
  createShowcaseDemoSnapshot,
  SHOWCASE_DEMO_ARTWORKS,
} from "../src/admin/showcase/showcaseDemoData.js";

function jsonResponse(payload, { status = 200, headers = {} } = {}) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
}

test("public showcase normalizes API sections, aspect ratios, and video media", () => {
  const feed = normalizeHomeShowcase({
    release_id: "release-1",
    version: 4,
    hero: {
      id: "hero-1",
      title: "品牌头图",
      section: "video",
      category: "广告魔法",
      alt_text: "品牌产品在水幕中的横向动态画面",
      public_prompt: "",
      aspect_ratio: "16:9",
      media_type: "video",
      media_url: "/api/v1/showcase/media/hero/content",
    },
    items: [{
      id: "item-1",
      title: "竖向模板",
      section: "template",
      category: "电影叙事",
      alt_text: "暖色室内的竖向生活场景",
      public_prompt: "暖色室内，缓慢推进镜头。",
      aspect_ratio: "9:16",
      media_type: "image",
      media_url: "/api/v1/showcase/media/item/content",
    }],
  });

  assert.equal(feed.hero.section, "视频");
  assert.equal(feed.hero.aspect, "landscape");
  assert.equal(feed.hero.mediaType, "video");
  assert.equal(feed.items[0].section, "模板");
  assert.equal(feed.items[0].aspect, "tall");
  assert.equal(hasPublishedHomeShowcase(feed), true);
  assert.equal(hasPublishedHomeShowcase(normalizeHomeShowcase({
    release_id: null,
    hero: null,
    items: [],
  })), false);
  const emptyFeed = normalizeHomeShowcase({ release_id: null, hero: null, items: [] });
  assert.equal(
    reconcileHomeShowcaseFeed(feed, emptyFeed),
    COMMUNITY_FALLBACK_FEED,
  );
});

test("a valid hero-only release remains displayable", () => {
  const feed = normalizeHomeShowcase({
    release_id: "release-hero-only",
    version: 1,
    hero: {
      id: "hero-only",
      title: "唯一头图",
      section: "video",
      category: "风格艺术",
      alt_text: "横向品牌动态画面",
      public_prompt: "",
      aspect_ratio: "16:9",
      media_type: "video",
      media_url: "/api/v1/showcase/media/hero-only/content",
    },
    items: [],
  });
  assert.ok(feed.hero);
  assert.equal(feed.items.length, 0);
  assert.equal(hasPublishedHomeShowcase(feed), true);
});

test("admin showcase maps Platform response fields without widening mutations", () => {
  const snapshot = normalizeAdminShowcase({
    draft_version: 7,
    publication_version: 11,
    last_unpublished_event: {
      id: "event-1",
      actor_user_id: "00000000-0000-0000-0000-000000000099",
      previous_release_id: "release-1",
      publication_version: 10,
      release_note: "内容复核",
      unpublished_at: "2026-08-21T07:00:00Z",
    },
    current_release: {
      id: "release-1",
      version: 2,
      draft_version: 6,
      release_note: "第二版",
      published_at: "2026-08-21T08:00:00Z",
      published_by_user_id: "00000000-0000-0000-0000-000000000099",
      item_count: 1,
    },
    items: [{
      id: "item-1",
      media_id: "media-1",
      title: "案例一",
      section: "challenge",
      category: "数字人",
      alt_text: "蓝色棚拍人物竖向肖像",
      public_prompt: "",
      aspect_ratio: "3:4",
      is_hero: true,
      sort_order: 0,
      media: { id: "media-1", media_type: "video", content_url: "https://platform.example/api/v1/showcase/media/media-1/content" },
    }],
    media: [{
      id: "media-orphaned",
      original_filename: "recovered.webp",
      media_type: "image",
      content_type: "image/webp",
      size_bytes: 1024,
      sha256: "a".repeat(64),
      created_at: "2026-08-21T06:00:00Z",
      content_url: "https://platform.example/api/v1/platform-admin/showcase/media/media-orphaned/content",
    }],
    releases: [],
  });
  assert.equal(snapshot.draft.items[0].section, "挑战");
  assert.equal(snapshot.publicationVersion, 11);
  assert.equal(snapshot.draft.items[0].aspectRatio, "3:4");
  assert.equal(snapshot.draft.items[0].mediaUrl, "https://platform.example/api/v1/showcase/media/media-1/content");
  assert.equal(snapshot.draft.changed, true);
  assert.equal(snapshot.liveRelease.itemCount, 1);
  assert.equal(snapshot.liveRelease.publishedBy, "00000000-0000-0000-0000-000000000099");
  assert.equal(snapshot.lastUnpublishedEvent.actor, "00000000-0000-0000-0000-000000000099");
  assert.equal(snapshot.lastUnpublishedEvent.previousReleaseVersion, "2");
  assert.equal(snapshot.media[0].id, "media-orphaned");
  assert.equal(snapshot.media[0].mediaUrl, "https://platform.example/api/v1/platform-admin/showcase/media/media-orphaned/content");

  const payload = showcaseMutationPayload({
    mediaId: "media-1",
    title: "案例一",
    section: "挑战",
    category: "数字人",
    altText: "蓝色棚拍人物竖向肖像",
    publicPrompt: "公开说明",
    aspectRatio: "3:4",
    isHero: true,
    sortOrder: 0,
    description: "不得进入 API",
  }, null, 7);
  assert.deepEqual(payload, {
    expectedDraftVersion: 7,
    media_id: "media-1",
    title: "案例一",
    section: "challenge",
    category: "数字人",
    alt_text: "蓝色棚拍人物竖向肖像",
    public_prompt: "公开说明",
    aspect_ratio: "3:4",
    is_hero: true,
    sort_order: 0,
  });
});

test("admin showcase keeps full unpublish history and singular fallback compatible", () => {
  const snapshot = normalizeAdminShowcase({
    publication_version: 8,
    publication_events: [
      {
        id: "event-older",
        actor_user_id: "owner-1",
        previous_release_id: "release-1",
        publication_version: 6,
        release_note: "较早下线",
        unpublished_at: "2026-08-20T07:00:00Z",
      },
      {
        id: "event-newer",
        actor_user_id: "owner-2",
        previous_release_id: "release-2",
        publication_version: 8,
        release_note: "最近下线",
        unpublished_at: "2026-08-21T07:00:00Z",
      },
    ],
    items: [],
    media: [],
    releases: [],
  });
  assert.deepEqual(snapshot.publicationEvents.map((event) => event.id), [
    "event-newer",
    "event-older",
  ]);
  assert.equal(snapshot.lastUnpublishedEvent.id, "event-newer");
});

test("retired draft rows never affect active hero count or atomic order", () => {
  const snapshot = normalizeAdminShowcase({
    draft_version: 9,
    items: [
      {
        id: "active-item",
        media_id: "active-media",
        title: "仍在草稿",
        section: "video",
        category: "风格艺术",
        alt_text: "仍在草稿中的横向案例",
        aspect_ratio: "16:9",
        is_hero: false,
        sort_order: 0,
        media: { media_type: "image", content_url: "https://platform.example/active" },
      },
      {
        id: "retired-hero",
        media_id: "retired-media",
        title: "已撤下头图",
        section: "video",
        category: "风格艺术",
        alt_text: "已经撤下的头图",
        aspect_ratio: "16:9",
        is_hero: true,
        sort_order: 1,
        retired_at: "2026-08-21T09:00:00Z",
        media: { media_type: "image", content_url: "https://platform.example/retired" },
      },
    ],
  });
  assert.deepEqual(snapshot.draft.items.map((item) => item.id), ["active-item"]);
  assert.equal(snapshot.draft.items.filter((item) => item.isHero).length, 0);
  assert.deepEqual(
    reorderedShowcaseItems(snapshot.draft.items, "active-item", 1)
      .map((item) => item.id),
    ["active-item"],
  );
});

test("development showcase data is complete, explicit, and owner-operable in memory", () => {
  const snapshot = createShowcaseDemoSnapshot();
  assert.ok(snapshot.draft.items.length >= 3);
  assert.equal(snapshot.draft.items.filter((item) => item.isHero).length, 1);
  assert.ok(snapshot.liveRelease);
  assert.equal(snapshot.media.length, snapshot.draft.items.length);
  assert.ok(snapshot.releases.every((release) => release.publishedBy.includes("演示")));
  assert.ok(SHOWCASE_DEMO_ARTWORKS.every((item) => (
    /^[0-9a-f-]{36}$/u.test(item.artifact_id) && item.preview_url.startsWith("/")
  )));
});

test("demo rollback manifest comparison leaves draft data intact", () => {
  const snapshot = createShowcaseDemoSnapshot();
  const draftBefore = structuredClone(snapshot.draft);
  assert.equal(showcaseManifestsEqual(draftBefore.items, snapshot.releases[0].items), false);
  assert.equal(showcaseManifestsEqual(draftBefore.items, structuredClone(draftBefore.items)), true);
});

test("direct uploads fail early for video while verified artwork imports remain available", () => {
  const values = {
    title: "视频案例",
    section: "视频",
    category: "风格艺术",
    altText: "用于测试的视频案例",
    mediaSource: "upload",
    file: new Blob(["video"], { type: "video/mp4" }),
  };
  assert.match(validateShowcaseDraft(values), /本地上传仅支持 JPEG、PNG 或 WebP 图片/);
  assert.equal(validateShowcaseDraft({
    ...values,
    mediaSource: "artifact",
    file: null,
    sourceTaskArtifactId: "00000000-0000-0000-0000-000000000071",
  }), "");
  assert.match(validateShowcaseDraft({
    ...values,
    file: new Blob(["image"], { type: "image/png" }),
    section: "挑战",
    isHero: true,
  }), /首页头图必须放在“视频”分区/);
});

test("showcase facade preserves ETag, same-origin media, strict bodies, and release idempotency", async () => {
  const calls = [];
  const fetcher = async (url, options) => {
    calls.push({ url, options });
    const path = new URL(url).pathname;
    if (path === "/api/v1/showcase/home") {
      return jsonResponse({
        release_id: "release-1",
        version: 1,
        hero: null,
        items: [{
          id: "item-1",
          title: "案例",
          section: "video",
          category: "风格艺术",
          alt_text: "用于测试的案例画面",
          public_prompt: "",
          aspect_ratio: "1:1",
          media_type: "image",
          content_type: "image/webp",
          media_url: "/api/v1/showcase/media/media-1/content",
        }],
      }, { headers: { etag: '"showcase-v1"' } });
    }
    if (path === "/api/v1/platform-admin/showcase/items") {
      return jsonResponse({ draft_version: 2, item: null }, { status: 201 });
    }
    if (path === "/api/v1/platform-admin/showcase/media") {
      return jsonResponse({
        id: "media-imported",
        source_task_artifact_id: "00000000-0000-0000-0000-000000000071",
        content_url: "/api/v1/showcase/media/media-imported/content",
      }, { status: 201 });
    }
    if (path === "/api/v1/platform-admin/showcase/order") {
      return jsonResponse({ draft_version: 3 });
    }
    if (path === "/api/v1/platform-admin/showcase/publish") {
      return jsonResponse({ id: "release-2", version: 2 });
    }
    if (path === "/api/v1/platform-admin/showcase/unpublish") {
      return jsonResponse({ current_release: null, draft_version: 3 });
    }
    if (path === "/api/v1/platform-admin/showcase/releases/release-1/rollback") {
      return jsonResponse({ id: "release-3", version: 3 });
    }
    throw new Error(`unexpected request ${path}`);
  };
  const client = createPlatformClient({
    baseUrl: "https://platform.example",
    companyId: "company-1",
    fetcher,
  });

  const home = await client.getHomeShowcase({ etag: '"showcase-v0"' });
  assert.equal(calls[0].options.headers["If-None-Match"], '"showcase-v0"');
  assert.equal(home.etag, '"showcase-v1"');
  assert.equal(home.data.items[0].media_url, "https://platform.example/api/v1/showcase/media/media-1/content");

  await client.createAdminShowcaseItem({
    expectedDraftVersion: 1,
    media_id: "00000000-0000-0000-0000-000000000001",
    title: "案例",
    section: "video",
    category: "风格艺术",
    alt_text: "用于测试的案例画面",
    public_prompt: "",
    aspect_ratio: "1:1",
    is_hero: true,
    sort_order: 0,
    ignored_extra: "must not cross facade",
  });
  assert.deepEqual(JSON.parse(calls[1].options.body), {
    media_id: "00000000-0000-0000-0000-000000000001",
    title: "案例",
    section: "video",
    category: "风格艺术",
    alt_text: "用于测试的案例画面",
    public_prompt: "",
    aspect_ratio: "1:1",
    is_hero: true,
    sort_order: 0,
    expected_draft_version: 1,
  });

  await client.reorderAdminShowcaseItems({
    expectedDraftVersion: 2,
    itemIds: ["item-2", "item-1"],
  });
  assert.deepEqual(JSON.parse(calls[2].options.body), {
    expected_draft_version: 2,
    item_ids: ["item-2", "item-1"],
  });

  await client.publishAdminShowcase({
    expectedDraftVersion: 3,
    expectedPublicationVersion: 4,
    releaseNote: "首页案例第一版",
    idempotencyKey: "publish-stable-key",
  });
  assert.equal(calls[3].options.headers["Idempotency-Key"], "publish-stable-key");
  assert.deepEqual(JSON.parse(calls[3].options.body), {
    expected_draft_version: 3,
    expected_publication_version: 4,
    release_note: "首页案例第一版",
  });

  await client.unpublishAdminShowcase({
    expectedDraftVersion: 3,
    expectedPublicationVersion: 5,
    releaseNote: "紧急下线当前首页",
    idempotencyKey: "unpublish-stable-key",
  });
  assert.deepEqual(JSON.parse(calls[4].options.body), {
    expected_draft_version: 3,
    expected_publication_version: 5,
    release_note: "紧急下线当前首页",
  });
  assert.equal(calls[4].options.headers["Idempotency-Key"], "unpublish-stable-key");

  await client.rollbackAdminShowcaseRelease("release-1", {
    expectedDraftVersion: 3,
    expectedPublicationVersion: 6,
    releaseNote: "回滚到首版",
    idempotencyKey: "rollback-stable-key",
  });
  assert.deepEqual(JSON.parse(calls[5].options.body), {
    expected_draft_version: 3,
    expected_publication_version: 6,
    release_note: "回滚到首版",
  });
  assert.equal(calls[5].options.headers["Idempotency-Key"], "rollback-stable-key");

  const imported = await client.uploadAdminShowcaseMedia(
    { sourceTaskArtifactId: "00000000-0000-0000-0000-000000000071" },
    { idempotencyKey: "artifact-import-stable-key" },
  );
  assert.equal(
    calls[6].options.body.get("source_task_artifact_id"),
    "00000000-0000-0000-0000-000000000071",
  );
  assert.equal(calls[6].options.body.get("file"), null);
  assert.equal(calls[6].options.headers["Idempotency-Key"], "artifact-import-stable-key");
  assert.equal(
    imported.content_url,
    "https://platform.example/api/v1/showcase/media/media-imported/content",
  );
  await assert.rejects(
    () => client.uploadAdminShowcaseMedia({
      sourceTaskArtifactId: "https://storage.example/not-an-artifact-id.mp4",
    }),
    (error) => error instanceof PlatformApiError
      && error.code === "INVALID_SHOWCASE_ARTIFACT_ID",
  );
  await assert.rejects(
    () => client.uploadAdminShowcaseMedia(new Blob(["video"], { type: "video/mp4" })),
    (error) => error instanceof PlatformApiError
      && error.code === "UNSUPPORTED_SHOWCASE_DIRECT_UPLOAD",
  );
});

test("showcase facade returns 304 metadata without parsing an empty body", async () => {
  const client = createPlatformClient({
    baseUrl: "https://platform.example",
    companyId: "company-1",
    fetcher: async () => new Response(null, {
      status: 304,
      headers: { etag: '"showcase-v4"' },
    }),
  });
  assert.deepEqual(await client.getHomeShowcase({ etag: '"showcase-v4"' }), {
    data: null,
    etag: '"showcase-v4"',
    notModified: true,
    status: 304,
  });
});

test("showcase facade rejects media outside the configured Platform origin", async () => {
  const client = createPlatformClient({
    baseUrl: "https://platform.example",
    companyId: "company-1",
    fetcher: async () => jsonResponse({
      version: 1,
      hero: null,
      items: [{
        id: "item-1",
        title: "案例",
        section: "video",
        category: "风格艺术",
        alt_text: "用于测试的案例画面",
        public_prompt: "",
        aspect_ratio: "1:1",
        media_type: "image",
        content_type: "image/webp",
        media_url: "https://attacker.example/pixel.png",
      }],
    }),
  });
  await assert.rejects(
    () => client.getHomeShowcase(),
    (error) => error instanceof PlatformApiError && error.code === "UNSAFE_PLATFORM_MEDIA_URL",
  );
});

test("owner-only Operations module and responsive route stay explicit", async () => {
  const [consoleSource, containerSource, showcaseContainerSource, showcaseScreenSource, sharedSource, styles, entry, homeSource, identitySource] = await Promise.all([
    readFile(new URL("../src/admin/OperationsConsole.jsx", import.meta.url), "utf8"),
    readFile(new URL("../src/admin/AdminOperationsContainer.jsx", import.meta.url), "utf8"),
    readFile(new URL("../src/admin/showcase/ShowcaseOperationsContainer.jsx", import.meta.url), "utf8"),
    readFile(new URL("../src/admin/showcase/ShowcaseOperations.jsx", import.meta.url), "utf8"),
    readFile(new URL("../src/admin/operations/operationsShared.jsx", import.meta.url), "utf8"),
    readFile(new URL("../src/design-system/showcase-route.css", import.meta.url), "utf8"),
    readFile(new URL("../src/design-system/index.css", import.meta.url), "utf8"),
    readFile(new URL("../src/CommunityHome.jsx", import.meta.url), "utf8"),
    readFile(new URL("../src/demoIdentitySurfaces.js", import.meta.url), "utf8"),
  ]);
  assert.match(sharedSource, /id: "showcase", label: "首页内容"/);
  assert.match(consoleSource, /item\.id !== "showcase" \|\| isPlatformOwner === true/);
  assert.match(containerSource, /identity\?\.is_platform_owner === true/);
  assert.match(containerSource, /<ShowcaseOperationsContainer/);
  assert.match(showcaseContainerSource, /sourceTaskArtifactId: artifactId/);
  assert.match(showcaseContainerSource, /client\.unpublishAdminShowcase/);
  assert.match(showcaseContainerSource, /expectedPublicationVersion: snapshot\.publicationVersion/);
  assert.match(showcaseContainerSource, /showcase-unpublish/);
  assert.match(showcaseContainerSource, /performDemo\("unpublish"[\s\S]*?liveRelease: null/);
  assert.match(showcaseScreenSource, /kind="unpublish"/);
  assert.match(showcaseScreenSource, /最长可能继续有效 5 分钟/);
  assert.match(showcaseScreenSource, /发布与下线记录/);
  assert.match(showcaseScreenSource, /不可变记录/);
  assert.match(showcaseScreenSource, /已上传媒体/);
  assert.match(showcaseScreenSource, /uploadedMedia=\{snapshot\?\.media \|\| \[\]\}/);
  assert.match(showcaseScreenSource, /accept="image\/jpeg,image\/png,image\/webp"/);
  assert.doesNotMatch(showcaseScreenSource, /accept="[^"]*video\//);
  assert.doesNotMatch(showcaseContainerSource, /otherHero/);
  assert.match(containerSource, /demoMode=\{demoMode\}/);
  assert.match(identitySource, /label: "平台所有者 · 周宁"/);
  assert.match(identitySource, /is_platform_owner: true/);
  const demoSaveBranch = showcaseContainerSource.match(
    /const artifactId = [\s\S]*?if \(demoMode\) \{([\s\S]*?)\n    \}\n    const fileSignature/,
  );
  assert.ok(demoSaveBranch, "demo save branch must stay explicit");
  assert.doesNotMatch(demoSaveBranch[1], /client\./);
  assert.match(showcaseContainerSource, /actionError\?\.status === 409[\s\S]*?load\(\{ silent: true \}\)/);
  assert.match(showcaseContainerSource, /changed: !showcaseManifestsEqual\(current\.draft\.items, items\)/);
  assert.match(entry, /@import "\.\/showcase-route\.css" layer\(system\.routes\)/);
  assert.match(styles, /@media \(max-width: 520px\)/);
  assert.match(styles, /@media \(max-width: 340px\)/);
  assert.match(styles, /\.showcase-operations button,[\s\S]*?min-height: 44px/);
  assert.match(styles, /button\[data-icon-only="true"\][\s\S]*?min-width: 44px/);
  assert.match(styles, /\.showcase-file-control:focus-within[\s\S]*?outline: 2px solid/);
  assert.match(homeSource, /setInterval\?\.\(revalidateShowcase, SHOWCASE_REVALIDATE_MS\)/);
  assert.match(homeSource, /hasPublishedHomeShowcase\(nextFeed\)/);
  assert.match(homeSource, /visibleItems\.length > 0 \|\| showHero/);
  assert.match(homeSource, /addEventListener\?\.\("pageshow", refreshAfterHistoryRestore\)/);
  assert.match(homeSource, /removeEventListener\?\.\("pageshow", refreshAfterHistoryRestore\)/);
  assert.match(homeSource, /muted[\s\S]*playsInline/);
});

test("reorder helper always emits a complete active order", () => {
  const items = [
    { id: "a", sortOrder: 0 },
    { id: "b", sortOrder: 1 },
    { id: "c", sortOrder: 2 },
  ];
  assert.deepEqual(
    reorderedShowcaseItems(items, "b", -1).map((item) => item.id),
    ["b", "a", "c"],
  );
});
