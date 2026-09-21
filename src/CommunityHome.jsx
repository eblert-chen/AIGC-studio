import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowRight,
  FilmSlate,
  MagicWand,
  MagnifyingGlass,
  Play,
} from "@phosphor-icons/react";
import {
  COMMUNITY_CATEGORIES,
  COMMUNITY_FALLBACK_FEED,
  COMMUNITY_SECTIONS,
  communityCategories,
  filterCommunityItems,
  hasPublishedHomeShowcase,
  normalizeHomeShowcase,
  reconcileHomeShowcaseFeed,
} from "./communityFeed.js";

const SHOWCASE_REVALIDATE_MS = 30_000;

function ShowcaseMedia({ item, hero = false }) {
  if (item.mediaType === "video") {
    return (
      <video
        src={item.mediaUrl || item.image}
        poster={item.poster || undefined}
        aria-label={item.alt}
        muted
        playsInline
        loop
        controls
        preload={hero ? "metadata" : "none"}
      />
    );
  }
  return (
    <img
      src={item.mediaUrl || item.image}
      alt={item.alt}
      loading={hero ? "eager" : "lazy"}
      decoding="async"
      fetchPriority={hero ? "high" : "auto"}
    />
  );
}

function InspirationCard({ item, onUsePrompt, featured = false, example = false }) {
  return (
    <article className={`community-card is-${item.aspect} ${featured ? "is-featured" : ""}`}>
      <div className="community-card-media">
        <ShowcaseMedia item={item} />
      </div>
      <footer className="community-card-caption">
        <div>
          <span>{item.category}{example ? " · 示例" : ""}</span>
          <h2>{item.title}</h2>
        </div>
        <button
          type="button"
          onClick={() => onUsePrompt(item.prompt)}
          disabled={!item.prompt}
          aria-label={`使用“${item.title}”的制作说明`}
        >
          <MagicWand size={15} aria-hidden="true" />
          {item.prompt ? "使用此灵感" : "说明未公开"}
        </button>
      </footer>
    </article>
  );
}

export function CommunityHome({ client, liveMode, onUsePrompt, onFocusComposer }) {
  const [activeSection, setActiveSection] = useState(COMMUNITY_SECTIONS[0]);
  const [activeCategory, setActiveCategory] = useState(COMMUNITY_CATEGORIES[0]);
  const [searchQuery, setSearchQuery] = useState("");
  const [feed, setFeed] = useState(COMMUNITY_FALLBACK_FEED);
  const [feedSource, setFeedSource] = useState("fallback");
  const [feedNotice, setFeedNotice] = useState("");
  const sectionTabRefs = useRef([]);
  const requestSequence = useRef(0);
  const requestController = useRef(null);
  const feedEtag = useRef("");

  const revalidateShowcase = useCallback(async () => {
    if (!client?.getHomeShowcase) return;
    requestController.current?.abort();
    const controller = new AbortController();
    requestController.current = controller;
    const sequence = requestSequence.current + 1;
    requestSequence.current = sequence;
    try {
      const response = await client.getHomeShowcase({
        etag: feedEtag.current,
        signal: controller.signal,
      });
      if (sequence !== requestSequence.current || controller.signal.aborted) return;
      if (response?.notModified) {
        setFeedNotice("");
        return;
      }
      const nextFeed = normalizeHomeShowcase(response?.data);
      if (!nextFeed) throw new Error("首页精选案例响应结构无效");
      feedEtag.current = response?.etag || "";
      const hasPublishedContent = hasPublishedHomeShowcase(nextFeed);
      setFeed((currentFeed) => reconcileHomeShowcaseFeed(currentFeed, nextFeed));
      setFeedSource(hasPublishedContent ? "platform" : "fallback");
      setFeedNotice("");
    } catch (error) {
      if (controller.signal.aborted) return;
      setFeedNotice("精选案例暂时无法更新，当前继续显示最近一次可用内容。");
    }
  }, [client]);

  useEffect(() => {
    if (!client?.getHomeShowcase) return undefined;
    revalidateShowcase();
    const interval = globalThis.setInterval?.(revalidateShowcase, SHOWCASE_REVALIDATE_MS);
    const refreshWhenVisible = () => {
      if (!globalThis.document || globalThis.document.visibilityState === "visible") {
        revalidateShowcase();
      }
    };
    const refreshAfterHistoryRestore = (event) => {
      if (event?.persisted) revalidateShowcase();
    };
    globalThis.addEventListener?.("focus", refreshWhenVisible);
    globalThis.addEventListener?.("pageshow", refreshAfterHistoryRestore);
    globalThis.document?.addEventListener?.("visibilitychange", refreshWhenVisible);
    return () => {
      requestController.current?.abort();
      globalThis.clearInterval?.(interval);
      globalThis.removeEventListener?.("focus", refreshWhenVisible);
      globalThis.removeEventListener?.("pageshow", refreshAfterHistoryRestore);
      globalThis.document?.removeEventListener?.("visibilitychange", refreshWhenVisible);
    };
  }, [client, revalidateShowcase]);

  const availableCategories = useMemo(
    () => communityCategories(feed.items, activeSection),
    [activeSection, feed.items],
  );

  useEffect(() => {
    if (!availableCategories.includes(activeCategory)) setActiveCategory("全部");
  }, [activeCategory, availableCategories]);

  const visibleItems = useMemo(() => {
    const categoryItems = filterCommunityItems(feed.items, activeSection, activeCategory);
    const needle = searchQuery.trim().toLocaleLowerCase("zh-CN");
    if (!needle) return categoryItems;
    return categoryItems.filter((item) => [
      item.title,
      item.category,
      item.description,
      item.prompt,
      item.author,
      item.model,
    ].join(" ").toLocaleLowerCase("zh-CN").includes(needle));
  }, [activeSection, activeCategory, feed.items, searchQuery]);
  const showHero = Boolean(
    activeSection === "视频" && activeCategory === "全部" && !searchQuery.trim() && feed.hero,
  );
  const featuredItems = showHero ? visibleItems.slice(0, 2) : [];
  const galleryItems = showHero ? visibleItems.slice(2) : visibleItems;
  const categoryFiltered = activeCategory !== "全部";
  const showcaseIsExample = feedSource !== "platform";

  const chooseSection = (section) => {
    setActiveSection(section);
    setActiveCategory("全部");
  };

  const handleSectionKeyDown = (event, index) => {
    let nextIndex = null;
    if (event.key === "ArrowRight") nextIndex = (index + 1) % COMMUNITY_SECTIONS.length;
    if (event.key === "ArrowLeft") nextIndex = (index - 1 + COMMUNITY_SECTIONS.length) % COMMUNITY_SECTIONS.length;
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = COMMUNITY_SECTIONS.length - 1;
    if (nextIndex === null) return;

    event.preventDefault();
    chooseSection(COMMUNITY_SECTIONS[nextIndex]);
    const focusNextTab = () => sectionTabRefs.current[nextIndex]?.focus();
    if (globalThis.requestAnimationFrame) {
      globalThis.requestAnimationFrame(focusNextTab);
    } else {
      focusNextTab();
    }
  };

  return (
    <section className="community-home" aria-labelledby="community-title">
      <header className="community-heading">
        <div className="community-page-intro">
          <h1 id="community-title">首页</h1>
          <p>看看社区在创作什么，喜欢就直接开场。</p>
        </div>
        <div className="community-heading-copy">
          <label className="community-search">
            <MagnifyingGlass size={17} aria-hidden="true" />
            <span className="visually-hidden">搜索作品、作者或提示词</span>
            <input
              type="search"
              value={searchQuery}
              placeholder="搜索作品 / 作者 / 提示词"
              onChange={(event) => setSearchQuery(event.target.value)}
            />
          </label>
          {showcaseIsExample ? (
            <small>{liveMode ? "示例内容，仅用于创作参考。" : "示例内容，不会生成真实任务或作品。"}</small>
          ) : null}
          {feedNotice ? <small role="status">{feedNotice}</small> : null}
        </div>
      </header>

      <div className="community-toolbar">
        <div className="community-primary-tabs" role="tablist" aria-label="社区内容类型">
          {COMMUNITY_SECTIONS.map((section, index) => (
            <button
              key={section}
              ref={(element) => { sectionTabRefs.current[index] = element; }}
              id={`community-section-tab-${index}`}
              type="button"
              role="tab"
              aria-selected={activeSection === section}
              aria-controls="community-feed-panel"
              tabIndex={activeSection === section ? 0 : -1}
              className={activeSection === section ? "is-active" : ""}
              onClick={() => chooseSection(section)}
              onKeyDown={(event) => handleSectionKeyDown(event, index)}
            >
              {section}
            </button>
          ))}
        </div>
        <span className="community-toolbar-divider" aria-hidden="true" />
        <div className="community-category-tabs" role="group" aria-label="灵感分类">
          {availableCategories.map((category) => (
            <button
              key={category}
              type="button"
              aria-pressed={activeCategory === category}
              className={activeCategory === category ? "is-active" : ""}
              onClick={() => setActiveCategory(category)}
            >
              {category}
            </button>
          ))}
        </div>
      </div>

      {visibleItems.length > 0 || showHero ? (
        <div
          id="community-feed-panel"
          className="community-gallery"
          role="tabpanel"
          aria-labelledby={`community-section-tab-${COMMUNITY_SECTIONS.indexOf(activeSection)}`}
        >
          {showHero && <div className="community-featured-grid" aria-label="本期精选">
            <article className="community-hero">
              <ShowcaseMedia item={feed.hero} hero />
              <footer className="community-hero-copy">
                <div>
                  {showcaseIsExample ? <small className="community-example-label">示例</small> : null}
                  <h2>{feed.hero.title}</h2>
                  <p>{feed.hero.description || "参考画面方向，再开始自己的创作。"}</p>
                </div>
                <button type="button" onClick={onFocusComposer}>
                  <Play size={15} weight="fill" aria-hidden="true" />
                  开始创作
                </button>
              </footer>
            </article>
            {featuredItems.length > 0 && <div className="community-featured-rail">
              {featuredItems.map((item) => (
                <InspirationCard
                  key={item.id}
                  item={item}
                  onUsePrompt={onUsePrompt}
                  featured
                  example={showcaseIsExample}
                />
              ))}
            </div>}
          </div>}
          <div className="community-feed-grid">
            {galleryItems.map((item) => (
              <InspirationCard
                key={item.id}
                item={item}
                onUsePrompt={onUsePrompt}
                example={showcaseIsExample}
              />
            ))}
          </div>
        </div>
      ) : (
        <div
          id="community-feed-panel"
          className="community-empty"
          role="tabpanel"
          aria-labelledby={`community-section-tab-${COMMUNITY_SECTIONS.indexOf(activeSection)}`}
        >
          <FilmSlate size={34} aria-hidden="true" />
          <strong>{searchQuery.trim() ? "没有找到相关灵感" : categoryFiltered ? "没有符合当前筛选的灵感" : `暂无${activeSection}灵感`}</strong>
          <span>{searchQuery.trim() ? "换一个关键词，或清除搜索继续浏览。" : categoryFiltered ? "查看全部内容，或换一个方向继续浏览。" : "可以从自己的想法开始创作。"}</span>
          <button
            type="button"
            onClick={searchQuery.trim()
              ? () => setSearchQuery("")
              : categoryFiltered
                ? () => setActiveCategory("全部")
                : onFocusComposer}
          >
            {searchQuery.trim() ? "清除搜索" : categoryFiltered ? "查看全部" : "开始创作"}
            <ArrowRight size={15} aria-hidden="true" />
          </button>
        </div>
      )}
    </section>
  );
}
