import test from "node:test";
import assert from "node:assert/strict";
import {
  COMMUNITY_FEED_ITEMS,
  communityColumnCount,
  filterCommunityItems,
} from "../src/communityFeed.js";

test("视频首页展示完整灵感流，分类筛选不泄漏其他类别", () => {
  assert.equal(
    filterCommunityItems(COMMUNITY_FEED_ITEMS, "视频", "全部").length,
    COMMUNITY_FEED_ITEMS.length,
  );
  const productItems = filterCommunityItems(COMMUNITY_FEED_ITEMS, "视频", "商品展示");
  assert.deepEqual(productItems.map((item) => item.id), ["speaker-water"]);
});

test("模板和挑战标签保留各自的内容边界", () => {
  assert.deepEqual(
    filterCommunityItems(COMMUNITY_FEED_ITEMS, "模板", "全部").map((item) => item.id),
    ["indoor-story", "rain-detail"],
  );
  assert.deepEqual(
    filterCommunityItems(COMMUNITY_FEED_ITEMS, "挑战", "全部").map((item) => item.id),
    ["lifestyle-cut", "blue-editorial"],
  );
});

test("社区瀑布流在常用断点按 1 到 5 列收敛", () => {
  assert.equal(communityColumnCount(420), 1);
  assert.equal(communityColumnCount(620), 2);
  assert.equal(communityColumnCount(900), 3);
  assert.equal(communityColumnCount(1280), 4);
  assert.equal(communityColumnCount(1680), 5);
});

test("每张灵感卡都使用真实图片、替代文本和可复用制作说明", () => {
  for (const item of COMMUNITY_FEED_ITEMS) {
    assert.match(item.image, /^\//);
    assert.ok(item.alt.length >= 8);
    assert.ok(item.prompt.length >= 24);
    assert.ok(item.category);
  }
});
