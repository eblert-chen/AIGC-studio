import test from "node:test";
import assert from "node:assert/strict";
import {
  COMMUNITY_CATEGORIES,
  COMMUNITY_FEED_ITEMS,
  COMMUNITY_SECTIONS,
  communityCategories,
  communityColumnCount,
  filterCommunityItems,
} from "../src/communityFeed.js";

function expectedIds(section, category = "全部") {
  return COMMUNITY_FEED_ITEMS
    .filter((item) => (
      (section === "视频" || item.section === section)
      && (category === "全部" || item.category === category)
    ))
    .map((item) => item.id);
}

test("视频首页展示完整灵感流，分类筛选不泄漏其他类别", () => {
  for (const section of COMMUNITY_SECTIONS) {
    for (const category of COMMUNITY_CATEGORIES) {
      assert.deepEqual(
        filterCommunityItems(COMMUNITY_FEED_ITEMS, section, category).map((item) => item.id),
        expectedIds(section, category),
        `${section} / ${category} 的筛选结果或顺序不正确`,
      );
    }
  }
});

test("模板和挑战标签保留各自的内容边界", () => {
  for (const section of ["模板", "挑战"]) {
    const items = filterCommunityItems(COMMUNITY_FEED_ITEMS, section, "全部");
    assert.ok(items.length >= 14, `${section} 至少应有 14 个方向`);
    assert.ok(items.every((item) => item.section === section));
    assert.deepEqual(items.map((item) => item.id), expectedIds(section));
  }
});

test("二级分类只展示当前内容分区确实拥有的方向", () => {
  for (const section of COMMUNITY_SECTIONS) {
    const expected = COMMUNITY_CATEGORIES.filter((category) => (
      category === "全部"
      || COMMUNITY_FEED_ITEMS.some((item) => (
        (section === "视频" || item.section === section)
        && item.category === category
      ))
    ));
    assert.deepEqual(communityCategories(COMMUNITY_FEED_ITEMS, section), expected);
  }
});

test("社区瀑布流在常用断点按 1 到 5 列收敛", () => {
  assert.equal(communityColumnCount(420), 1);
  assert.equal(communityColumnCount(620), 2);
  assert.equal(communityColumnCount(900), 3);
  assert.equal(communityColumnCount(1280), 4);
  assert.equal(communityColumnCount(1680), 5);
});

test("每张灵感卡都使用真实图片、替代文本和可复用制作说明", () => {
  const ids = new Set();
  const titles = new Set();
  const images = new Set();
  assert.ok(COMMUNITY_FEED_ITEMS.length >= 42, "首页回退素材库至少应保留 42 张卡片");
  for (const item of COMMUNITY_FEED_ITEMS) {
    assert.equal(ids.has(item.id), false, `重复的素材 id: ${item.id}`);
    assert.equal(titles.has(item.title), false, `重复的素材标题: ${item.title}`);
    assert.equal(images.has(item.image), false, `重复的素材图片: ${item.image}`);
    ids.add(item.id);
    titles.add(item.title);
    images.add(item.image);
    assert.ok(item.id.trim().length > 0);
    assert.ok(item.title.trim().length > 0);
    assert.match(item.image, /^\//);
    assert.ok(item.alt.trim().length >= 8);
    assert.ok(item.prompt.trim().length >= 24);
    assert.ok(COMMUNITY_SECTIONS.includes(item.section));
    assert.ok(COMMUNITY_CATEGORIES.includes(item.category));
    assert.notEqual(item.category, "全部");
    assert.ok(["landscape", "portrait", "square", "tall"].includes(item.aspect));
  }
  for (const section of COMMUNITY_SECTIONS) {
    assert.ok(expectedIds(section).length >= 14, `${section} 至少应覆盖 14 个方向`);
  }
  for (const category of COMMUNITY_CATEGORIES.slice(1)) {
    assert.ok(expectedIds("视频", category).length >= 6, `${category} 至少应覆盖 6 个方向`);
  }
});
