import { useMemo, useRef, useState } from "react";
import { Bell } from "@phosphor-icons/react/Bell";
import { CaretDown } from "@phosphor-icons/react/CaretDown";
import { CaretLeft } from "@phosphor-icons/react/CaretLeft";
import { CaretRight } from "@phosphor-icons/react/CaretRight";
import { CheckCircle } from "@phosphor-icons/react/CheckCircle";
import { ClipboardText } from "@phosphor-icons/react/ClipboardText";
import { ClockCounterClockwise } from "@phosphor-icons/react/ClockCounterClockwise";
import { Cube } from "@phosphor-icons/react/Cube";
import { DiceFive } from "@phosphor-icons/react/DiceFive";
import { FilmSlate } from "@phosphor-icons/react/FilmSlate";
import { Gear } from "@phosphor-icons/react/Gear";
import { House } from "@phosphor-icons/react/House";
import { ImageSquare } from "@phosphor-icons/react/ImageSquare";
import { Images } from "@phosphor-icons/react/Images";
import { MagicWand } from "@phosphor-icons/react/MagicWand";
import { Paperclip } from "@phosphor-icons/react/Paperclip";
import { PaperPlaneTilt } from "@phosphor-icons/react/PaperPlaneTilt";
import { Pause } from "@phosphor-icons/react/Pause";
import { Play } from "@phosphor-icons/react/Play";
import { Sparkle } from "@phosphor-icons/react/Sparkle";
import { X } from "@phosphor-icons/react/X";

const NAV_ITEMS = [
  { label: "首页", icon: House },
  { label: "创作", icon: Sparkle },
  { label: "素材", icon: ImageSquare },
  { label: "作品", icon: Images },
  { label: "发布", icon: PaperPlaneTilt },
  { label: "历史", icon: ClockCounterClockwise },
];

const PATHS = [
  {
    id: "console",
    title: "直接生成",
    subtitle: "进入 3D 导演台",
    description: "在监视器墙上看素材与结果，用场记单写 Prompt，适合快速完成一条内容。",
    icon: FilmSlate,
    badge: "默认",
  },
  {
    id: "notebook",
    title: "导演手记",
    subtitle: "高级工作台",
    description: "按场次组织脚本、拍板与成片，适合宣传片、短剧和多段叙事。",
    icon: ClipboardText,
  },
  {
    id: "canvas",
    title: "谱系画布",
    subtitle: "高级工作台",
    description: "在节点之间探索变体、重混与分支，让每次生成都有清晰来路。",
    icon: Cube,
  },
];

const INSPIRATIONS = [
  {
    id: "fragrance",
    tag: "商品展示",
    title: "水弧蓝晶香氛",
    description: "近白影棚、水弧与钴蓝玻璃，适合产品质感短片。",
    prompt:
      "近白影棚中的钴蓝玻璃香氛瓶，清澈水弧从产品上方掠过，银色金属底座与克制橙色轮廓反光，高速摄影定格水滴，镜头缓慢横移。",
    image: "/media/cobalt-fragrance.webp",
  },
  {
    id: "observatory",
    tag: "电影叙事",
    title: "盐原蓝时观测站",
    description: "蓝色暮光、暖光建筑与独行者，强调空间尺度。",
    prompt:
      "蓝色暮光中的广阔盐原，一位独行者沿笔直小径走向亮着暖光的玻璃观测站，超宽远景固定镜头后缓慢推进，强调空间尺度与孤独感。",
    image: "/media/salt-observatory.webp",
  },
  {
    id: "courier",
    tag: "动漫剧场",
    title: "纸艺海城递送",
    description: "折纸城市逐层展开，跟随信使跃过晨光屋脊。",
    prompt:
      "原创纸艺信使穿过层叠的海滨城市屋顶，蓝色与珊瑚橙折纸建筑逐层展开，晨光投下清晰长影，镜头沿对角线跟随人物跃过屋脊。",
    image: "/media/paper-courier.webp",
  },
];

const SPEC_GROUPS = [
  { id: "ratio", label: "比例", options: ["16:9", "9:16", "1:1"] },
  { id: "duration", label: "时长", options: ["10秒", "15秒", "20秒"] },
  { id: "quality", label: "画质", options: ["720p", "1080p"] },
  { id: "engine", label: "引擎", options: ["CinemoX", "Rush"] },
];

function productWorkbenchUrl(workbench) {
  if (!globalThis.location) return "";
  const configuredBase = String(import.meta.env.VITE_PRODUCT_BASE_URL || "").trim();
  const current = new URL(globalThis.location.href);
  let base = configuredBase || current.origin;
  if (
    !configuredBase
    && ["127.0.0.1", "localhost"].includes(current.hostname)
    && current.port !== "5173"
  ) {
    base = `${current.protocol}//${current.hostname}:5173`;
  }
  const target = new URL("/creation", base);
  target.searchParams.set("workbench", workbench);
  return target.href;
}

function Brand() {
  return (
    <a className="brand" href="#creation" aria-label="旭天 AI studio 首页">
      <picture>
        <source media="(max-width: 760px)" srcSet="/brand/xutian-ai-studio-symbol.svg" />
        <img src="/brand/xutian-ai-studio-wordmark.svg" alt="旭天 AI studio" />
      </picture>
    </a>
  );
}

function Topbar() {
  return (
    <header className="topbar">
      <Brand />
      <div className="workspace-context">
        <strong>企业工作空间</strong>
        <button className="workspace-switch" type="button">
          <span className="status-dot" />
          <span className="workspace-name">安徽濉溪智能科技</span>
          <CaretDown size={14} weight="bold" />
        </button>
      </div>
      <div className="account-area">
        <button className="icon-button" type="button" aria-label="通知">
          <Bell size={19} weight="regular" />
          <span className="notification-dot" />
        </button>
        <button className="account-button" type="button" aria-label="打开账户菜单">
          <span className="avatar">孙</span>
          <span className="account-copy">
            <strong>孙旭嵩</strong>
            <small>老板</small>
          </span>
          <CaretDown size={13} />
        </button>
      </div>
    </header>
  );
}

function Navigation({ active, onNavigate }) {
  const navigationRef = useRef(null);

  const scrollNavigation = (direction) => {
    navigationRef.current?.scrollBy({ left: direction * 160, behavior: "smooth" });
  };

  return (
    <nav className="navigation" aria-label="Studio 导航">
      <button className="nav-scroll-button nav-scroll-back" type="button" aria-label="向前浏览导航" onClick={() => scrollNavigation(-1)}>
        <CaretLeft size={17} weight="bold" />
      </button>
      <div className="navigation-scroll" ref={navigationRef}>
        {NAV_ITEMS.map(({ label, icon: Icon }) => (
          <button
            key={label}
            type="button"
            className={active === label ? "nav-item is-active" : "nav-item"}
            aria-current={active === label ? "page" : undefined}
            onClick={() => onNavigate(label)}
          >
            <Icon size={18} weight={active === label ? "fill" : "regular"} />
            <span>{label}</span>
          </button>
        ))}
        <button
          className={active === "设置" ? "nav-item mobile-settings-item is-active" : "nav-item mobile-settings-item"}
          type="button"
          aria-current={active === "设置" ? "page" : undefined}
          onClick={() => onNavigate("设置")}
        >
          <Gear size={18} weight={active === "设置" ? "fill" : "regular"} />
          <span>设置</span>
        </button>
      </div>
      <button className="nav-scroll-button nav-scroll-forward" type="button" aria-label="向后浏览导航" onClick={() => scrollNavigation(1)}>
        <CaretRight size={17} weight="bold" />
      </button>
      <button className="nav-item settings-item" type="button" onClick={() => onNavigate("设置")}>
        <Gear size={18} />
        <span>设置</span>
      </button>
    </nav>
  );
}

function EntryScreen({ onOpenConsole, onAdvanced, toast, setToast }) {
  const [activeNav, setActiveNav] = useState("创作");

  const handleNavigate = (label) => {
    setActiveNav(label);
    if (label !== "创作") {
      setToast(`${label}保留为产品导航；本示例聚焦创作入口。`);
    }
  };

  return (
    <div className="entry-shell">
      <Topbar />
      <div className="entry-body">
        <Navigation active={activeNav} onNavigate={handleNavigate} />
        <main className="entry-main">
          <section className="entry-hero" aria-labelledby="entry-title">
            <div className="hero-copy">
              <h1 id="entry-title">今天想创作什么？</h1>
              <p>直接完成一条内容，或进入更深入的创作工作台。</p>
            </div>

            <div className="path-grid" aria-label="选择创作路径">
              {PATHS.map(({ id, title, subtitle, description, icon: Icon, badge }) => (
                <button
                  className={id === "console" ? "path-card is-primary" : "path-card"}
                  type="button"
                  key={id}
                  onClick={() => (id === "console" ? onOpenConsole("") : onAdvanced(id))}
                >
                  <span className="path-card-topline">
                    <span className="path-icon"><Icon size={21} weight="duotone" /></span>
                    {badge ? <span className="path-badge">{badge}</span> : null}
                  </span>
                  <span className="path-heading">
                    <strong>{title}</strong>
                    <small>{subtitle}</small>
                  </span>
                  <span className="path-description">{description}</span>
                  <span className="path-action">
                    {id === "console" ? "进入导演台" : "打开生产工作台"} <CaretRight size={15} weight="bold" />
                  </span>
                </button>
              ))}
            </div>

            <div className="inspiration-block">
              <div className="inspiration-heading">
                <span>从灵感开始</span>
                <small>点击后带着 Prompt 进入导演台</small>
              </div>
              <div className="inspiration-grid">
                {INSPIRATIONS.map((item) => (
                  <button
                    className="inspiration-card"
                    type="button"
                    key={item.id}
                    onClick={() => onOpenConsole(item.prompt, item)}
                  >
                    <span className="inspiration-tag">{item.tag}</span>
                    <strong>{item.title}</strong>
                    <span>{item.description}</span>
                    <CaretRight className="inspiration-arrow" size={16} weight="bold" />
                  </button>
                ))}
              </div>
            </div>
          </section>
        </main>
      </div>
      {toast ? (
        <div className="toast" role="status">
          <span>{toast}</span>
          <button type="button" aria-label="关闭提示" onClick={() => setToast("")}><X size={15} /></button>
        </div>
      ) : null}
    </div>
  );
}

function SpecModule({ group, value, onChange }) {
  return (
    <fieldset className="spec-module">
      <legend>{group.label}</legend>
      <div className="spec-options">
        {group.options.map((option) => (
          <button
            key={option}
            type="button"
            className={value === option ? "spec-option is-selected" : "spec-option"}
            aria-pressed={value === option}
            onClick={() => onChange(option)}
          >
            {option}
          </button>
        ))}
      </div>
    </fieldset>
  );
}

function DirectorConsole({ initialPrompt, inspiration, onBack, onAdvanced }) {
  const [prompt, setPrompt] = useState(initialPrompt);
  const [reference, setReference] = useState(inspiration ?? INSPIRATIONS[0]);
  const [ratio, setRatio] = useState("16:9");
  const [duration, setDuration] = useState("15秒");
  const [quality, setQuality] = useState("1080p");
  const [engine, setEngine] = useState("CinemoX");
  const [isStarting, setIsStarting] = useState(false);
  const [isPreviewPlaying, setIsPreviewPlaying] = useState(false);
  const [status, setStatus] = useState("等待演示开机");
  const [specsOpen, setSpecsOpen] = useState(false);
  const fileInput = useRef(null);

  const specValues = { ratio, duration, quality, engine };
  const specSetters = { ratio: setRatio, duration: setDuration, quality: setQuality, engine: setEngine };
  const promptLength = Array.from(prompt).length;
  const canStart = prompt.trim().length > 0 && !isStarting;

  const selectedImage = useMemo(
    () => reference?.image ?? INSPIRATIONS[1].image,
    [reference],
  );

  const handleUpload = (event) => {
    const [file] = event.target.files ?? [];
    if (!file) return;
    setReference({ title: file.name, image: URL.createObjectURL(file) });
  };

  const expandPrompt = () => {
    if (!prompt.trim()) {
      setPrompt(INSPIRATIONS[1].prompt);
      setReference(INSPIRATIONS[1]);
      return;
    }
    if (!prompt.includes("镜头")) {
      setPrompt(`${prompt.trim()}，镜头从中景缓慢推进，保留真实材质与克制的电影光线。`);
    }
  };

  const randomInspiration = () => {
    const currentIndex = INSPIRATIONS.findIndex((item) => item.prompt === prompt);
    const next = INSPIRATIONS[(currentIndex + 1 + INSPIRATIONS.length) % INSPIRATIONS.length];
    setPrompt(next.prompt);
    setReference(next);
  };

  const startDemo = () => {
    if (!canStart) return;
    setIsPreviewPlaying(false);
    setIsStarting(true);
    setStatus("正在演示提交流程…");
    window.setTimeout(() => {
      setIsStarting(false);
      setStatus("演示完成 · 未向后端提交");
    }, 1100);
  };

  const togglePreview = () => {
    setIsPreviewPlaying((playing) => {
      const next = !playing;
      setStatus(next ? "正在播放演示预览" : "演示预览已暂停");
      return next;
    });
  };

  const reusePreviousSettings = () => {
    setPrompt(INSPIRATIONS[2].prompt);
    setReference(INSPIRATIONS[2]);
    setRatio("9:16");
    setDuration("10秒");
    setQuality("1080p");
    setEngine("CinemoX");
    setIsPreviewPlaying(false);
    setStatus("已复用演示设置 · 未提交");
  };

  return (
    <main className="director-console">
      <div className="console-toolbar">
        <button className="back-button" type="button" onClick={onBack}>
          <CaretLeft size={16} weight="bold" /> 返回创作入口
        </button>
        <div className="console-title">
          <FilmSlate size={20} weight="duotone" />
          <strong>3D 导演台</strong>
          <span>视觉原型</span>
        </div>
        <div className="readiness">
          <CheckCircle size={16} weight="fill" />
          <span>交互演示可用 · 不连接服务</span>
        </div>
      </div>

      <section className="monitor-wall" aria-label="导演监视器墙">
        <article className="monitor monitor-reference">
          <div className="monitor-screen">
            <img src={selectedImage} alt={reference?.title ?? "当前参考画面"} />
            <button className="monitor-action" type="button" onClick={() => fileInput.current?.click()}>
              <ImageSquare size={18} /> 更换参考
            </button>
          </div>
          <p><span>参考画面</span>{reference?.title ?? "水弧蓝晶香氛"}</p>
        </article>

        <article className="monitor monitor-current">
          <div className="monitor-screen current-screen">
            <img className={isPreviewPlaying ? "is-preview-playing" : ""} src="/media/salt-observatory.webp" alt="盐原蓝时观测站演示预览" />
            <span className="screen-veil" />
            <button className="play-button" type="button" aria-label={isPreviewPlaying ? "暂停演示预览" : "播放演示预览"} aria-pressed={isPreviewPlaying} onClick={togglePreview}>
              {isPreviewPlaying ? <Pause size={21} weight="fill" /> : <Play size={21} weight="fill" />}
            </button>
            <span className="current-status" role="status">{status}</span>
          </div>
          <p><span>本场预览</span>{ratio} · {duration} · {quality}</p>
        </article>

        <article className="monitor monitor-previous">
          <div className="monitor-screen">
            <img src="/media/paper-courier.webp" alt="上一条纸艺海城递送结果" />
            <button className="monitor-action" type="button" onClick={reusePreviousSettings}>
              <MagicWand size={18} /> 复用设置
            </button>
          </div>
          <p><span>演示样例</span>纸艺海城递送 · 已完成</p>
        </article>
      </section>

      <section className="director-desk" aria-label="导演控制台">
        <button className="mobile-spec-summary" type="button" onClick={() => setSpecsOpen(!specsOpen)} aria-expanded={specsOpen}>
          <span><strong>输出规格</strong>{ratio} · {duration} · {quality} · {engine}</span>
          <CaretDown size={15} className={specsOpen ? "is-open" : ""} />
        </button>

        <div className={specsOpen ? "spec-rack is-open" : "spec-rack"}>
          {SPEC_GROUPS.map((group) => (
            <SpecModule key={group.id} group={group} value={specValues[group.id]} onChange={specSetters[group.id]} />
          ))}
        </div>

        <div className="prompt-slate">
          <div className="slate-header">
            <span><FilmSlate size={17} weight="fill" /> 场记单</span>
            <span>SCENE 01 · TAKE 01</span>
          </div>
          <div className="slate-body">
            <label htmlFor="director-prompt">描述画面、动作、镜头与氛围</label>
            <textarea
              id="director-prompt"
              value={prompt}
              maxLength={2000}
              onChange={(event) => setPrompt(event.target.value)}
              placeholder="例如：蓝色暮光中的盐原，一位独行者走向亮着暖光的观测站……"
            />
            <div className="prompt-meta"><span>{promptLength}/2000</span></div>
            <div className="slate-actions">
              <div className="prompt-tools">
                <button type="button" onClick={randomInspiration}><DiceFive size={17} />灵感</button>
                <button type="button" onClick={() => fileInput.current?.click()}><Paperclip size={17} />参考图</button>
                <button type="button" onClick={expandPrompt}><MagicWand size={17} />扩写</button>
              </div>
              <button className="start-button" type="button" disabled={!canStart} onClick={startDemo}>
                {isStarting ? "正在开机…" : prompt.trim() ? "开机生成" : "先写下场景"}
                <FilmSlate size={18} weight="fill" />
              </button>
            </div>
            <p className="truth-note">原型演示不会提交任务或扣费；正式产品以服务端就绪与报价为准。</p>
          </div>
        </div>

        <aside className="project-handoff">
          <span className="handoff-icon"><ClipboardText size={19} weight="duotone" /></span>
          <div><strong>这条要进入长项目？</strong><p>把当前场景交给导演手记继续拆场。</p></div>
          <button type="button" onClick={() => onAdvanced("notebook")}>并入手记 <CaretRight size={14} /></button>
        </aside>
      </section>

      <input ref={fileInput} hidden type="file" accept="image/*" onChange={handleUpload} />
    </main>
  );
}

export function App() {
  const [view, setView] = useState("entry");
  const [initialPrompt, setInitialPrompt] = useState("");
  const [selectedInspiration, setSelectedInspiration] = useState(null);
  const [toast, setToast] = useState("");

  const openConsole = (prompt = "", inspiration = null) => {
    setInitialPrompt(prompt);
    setSelectedInspiration(inspiration);
    setToast("");
    setView("console");
  };

  const openAdvanced = (id) => {
    const target = productWorkbenchUrl(id);
    if (target) {
      globalThis.location.assign(target);
      return;
    }
    setToast("生产创作页暂时无法打开，请从主应用的创作入口重试。");
  };

  return (
    <div className="prototype-stage">
      <div className={view === "console" ? "app-frame is-console" : "app-frame"}>
        {view === "entry" ? (
          <EntryScreen onOpenConsole={openConsole} onAdvanced={openAdvanced} toast={toast} setToast={setToast} />
        ) : (
          <DirectorConsole
            initialPrompt={initialPrompt}
            inspiration={selectedInspiration}
            onBack={() => setView("entry")}
            onAdvanced={openAdvanced}
          />
        )}
      </div>
    </div>
  );
}
