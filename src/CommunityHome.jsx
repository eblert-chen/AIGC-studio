import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowRight,
  FilmSlate,
  MagicWand,
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
    />
  );
}

function InspirationCard({ item, onUsePrompt, featured = false }) {
  return (
    <article className={`community-card is-${item.aspect} ${featured ? "is-featured" : ""}`}>
      <div className="community-card-media">
        <ShowcaseMedia item={item} />
      </div>
      <div className="community-card-caption">
        <div>
          <span>{item.category}</span>
          <h3>{item.title}</h3>
        </div>
        <button
          type="button"
          onClick={() => onUsePrompt(item.prompt)}
          disabled={!item.prompt}
          aria-label={`使用“${item.title}”的制作说明`}
        >
          <MagicWand size={15} aria-hidden="true" />
          {item.prompt ? "做同款" : "说明未公开"}
        </button>
      </div>
    </article>
  );
}

export function CommunityHome({ client, liveMode, onUsePrompt, onFocusComposer }) {
  const [activeSection, setActiveSection] = useState(COMMUNITY_SECTIONS[0]);
  const [activeCategory, setActiveCategory] = useState(COMMUNITY_CATEGORIES[0]);
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
    () => communityCategories(feed.items),
    [feed.items],
  );

  useEffect(() => {
    if (!availableCategories.includes(activeCategory)) setActiveCategory("全部");
  }, [activeCategory, availableCategories]);

  const visibleItems = useMemo(
    () => filterCommunityItems(feed.items, activeSection, activeCategory),
    [activeSection, activeCategory, feed.items],
  );
  const showHero = Boolean(
    activeSection === "视频" && activeCategory === "全部" && feed.hero,
  );
  const visibleDirectionCount = visibleItems.length + (showHero ? 1 : 0);
  const featuredItems = showHero ? visibleItems.slice(0, 2) : [];
  const galleryItems = showHero ? visibleItems.slice(2) : visibleItems;

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
          <h1 id="community-title">创作灵感</h1>
          <p>从画面、节奏与风格中选择方向，并将制作说明直接带入现有创作流程。</p>
        </div>
        <div className="community-heading-copy">
          <span className="community-kicker">灵感示例</span>
          <span aria-live="polite">{visibleDirectionCount} 个创作方向</span>
          <small>
            {feedSource === "platform"
              ? "Platform 已发布精选案例"
              : liveMode
                ? "项目示例素材 · 不代表公司真实数据"
                : "演示素材 · 不代表真实社区数据"}
          </small>
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
        <div className="community-category-tabs" aria-label="灵感分类">
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
              <div className="community-hero-copy">
                <h2>{feed.hero.title}</h2>
                <p>{feed.hero.description || "从精选案例获得方向，再进入真实创作流程。"}</p>
                <button type="button" onClick={onFocusComposer}>
                  <Play size={15} weight="fill" aria-hidden="true" />
                  开始创作
                </button>
              </div>
            </article>
            {featuredItems.length > 0 && <div className="community-featured-rail">
              {featuredItems.map((item) => (
                <InspirationCard
                  key={item.id}
                  item={item}
                  onUsePrompt={onUsePrompt}
                  featured
                />
              ))}
            </div>}
          </div>}
          <div className="community-feed-grid">
            {galleryItems.map((item) => (
              <InspirationCard key={item.id} item={item} onUsePrompt={onUsePrompt} />
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
          <strong>这个分类正在整理</strong>
          <span>先看看全部内容，或直接开始自己的创作。</span>
          <button type="button" onClick={() => setActiveCategory("全部")}>
            查看全部
            <ArrowRight size={15} aria-hidden="true" />
          </button>
        </div>
      )}
    </section>
  );
}
