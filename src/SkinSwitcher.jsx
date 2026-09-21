import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import { CaretDown, Check } from "./phosphorIcons.js";

export const SKIN_STORAGE_KEY = "yingchuang-skin";

export const SKIN_OPTIONS = Object.freeze([
  { id: "paper", label: "纯白", description: "中性纸面" },
  { id: "mist", label: "雾灰", description: "冷灰低眩光" },
  { id: "warm", label: "暖米", description: "温暖纸面" },
]);

const SKIN_IDS = new Set(SKIN_OPTIONS.map(({ id }) => id));

export function normalizeSkin(value) {
  return SKIN_IDS.has(value) ? value : "paper";
}

function readStoredSkin() {
  try {
    return normalizeSkin(globalThis.localStorage?.getItem(SKIN_STORAGE_KEY));
  } catch {
    return "paper";
  }
}

export function useSkinPreference() {
  const [skin, setSkinState] = useState(readStoredSkin);

  const setSkin = useCallback((nextSkin) => {
    setSkinState(normalizeSkin(nextSkin));
  }, []);

  useEffect(() => {
    try {
      globalThis.localStorage?.setItem(SKIN_STORAGE_KEY, skin);
    } catch {
      // The selected skin still applies in memory when storage is unavailable.
    }
  }, [skin]);

  return [skin, setSkin];
}

const MENU_GAP = 8;
const MENU_GUTTER = 12;
const MENU_MIN_WIDTH = 244;
const MENU_MAX_WIDTH = 272;
const MENU_ESTIMATED_HEIGHT = 222;
const PAGE_FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

function menuPlacement(trigger, menuHeight = MENU_ESTIMATED_HEIGHT) {
  const rect = trigger?.getBoundingClientRect?.();
  if (!rect) return { top: MENU_GUTTER, left: MENU_GUTTER, width: MENU_MIN_WIDTH };

  const viewport = globalThis.visualViewport;
  const viewportLeft = viewport?.offsetLeft || 0;
  const viewportTop = viewport?.offsetTop || 0;
  const viewportWidth = viewport?.width || globalThis.innerWidth || MENU_MAX_WIDTH;
  const viewportHeight = viewport?.height || globalThis.innerHeight || menuHeight;
  const availableWidth = Math.max(0, viewportWidth - MENU_GUTTER * 2);
  const width = Math.min(
    MENU_MAX_WIDTH,
    availableWidth,
    Math.max(MENU_MIN_WIDTH, rect.width),
  );
  const minLeft = viewportLeft + MENU_GUTTER;
  const maxLeft = viewportLeft + viewportWidth - width - MENU_GUTTER;
  const left = Math.min(Math.max(rect.right - width, minLeft), Math.max(minLeft, maxLeft));
  const below = rect.bottom + MENU_GAP;
  const above = rect.top - menuHeight - MENU_GAP;
  const maxTop = viewportTop + viewportHeight - menuHeight - MENU_GUTTER;
  const top = below + menuHeight <= viewportTop + viewportHeight - MENU_GUTTER
    ? below
    : Math.max(viewportTop + MENU_GUTTER, Math.min(above, maxTop));

  return {
    top: Math.round(top),
    left: Math.round(left),
    width: Math.round(width),
  };
}

function SkinSample({ skin, large = false }) {
  return (
    <span
      className={`skin-switcher-sample${large ? " is-large" : ""}`}
      data-skin-sample={skin}
      aria-hidden="true"
    >
      <span />
    </span>
  );
}

function focusAdjacentControl(trigger, reverse = false) {
  const document = trigger?.ownerDocument;
  if (!document) return;
  const controls = Array.from(document.querySelectorAll(PAGE_FOCUSABLE_SELECTOR))
    .filter((element) => (
      !element.closest(".skin-switcher-menu")
      && !element.hasAttribute("hidden")
      && element.getClientRects().length > 0
    ));
  const triggerIndex = controls.indexOf(trigger);
  const next = controls[triggerIndex + (reverse ? -1 : 1)];
  (next || trigger).focus();
}

export function SkinSwitcher({ value, onChange, className = "" }) {
  const skin = normalizeSkin(value);
  const currentIndex = Math.max(0, SKIN_OPTIONS.findIndex((option) => option.id === skin));
  const currentOption = SKIN_OPTIONS[currentIndex];
  const menuId = useId();
  const rootRef = useRef(null);
  const triggerRef = useRef(null);
  const menuRef = useRef(null);
  const optionRefs = useRef([]);
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(currentIndex);
  const [placement, setPlacement] = useState(() => ({
    top: MENU_GUTTER,
    left: MENU_GUTTER,
    width: MENU_MIN_WIDTH,
  }));
  const [announcement, setAnnouncement] = useState("");

  const positionMenu = useCallback((menuHeight) => {
    setPlacement(menuPlacement(triggerRef.current, menuHeight));
  }, []);

  const closeMenu = useCallback((restoreFocus = false) => {
    setOpen(false);
    if (restoreFocus) {
      globalThis.requestAnimationFrame?.(() => triggerRef.current?.focus());
    }
  }, []);

  const openMenu = useCallback((nextIndex = currentIndex) => {
    setActiveIndex(nextIndex);
    positionMenu();
    setOpen(true);
  }, [currentIndex, positionMenu]);

  useEffect(() => {
    if (!open) return undefined;

    const frame = globalThis.requestAnimationFrame?.(() => {
      positionMenu(menuRef.current?.offsetHeight || MENU_ESTIMATED_HEIGHT);
      optionRefs.current[activeIndex]?.focus();
    });
    const handleOutsidePointer = (event) => {
      if (
        !rootRef.current?.contains(event.target)
        && !menuRef.current?.contains(event.target)
      ) {
        setOpen(false);
      }
    };
    const handleOutsideFocus = (event) => {
      if (
        !rootRef.current?.contains(event.target)
        && !menuRef.current?.contains(event.target)
      ) {
        setOpen(false);
      }
    };
    const handleViewportChange = () => {
      positionMenu(menuRef.current?.offsetHeight || MENU_ESTIMATED_HEIGHT);
    };

    globalThis.document?.addEventListener("pointerdown", handleOutsidePointer, true);
    globalThis.document?.addEventListener("focusin", handleOutsideFocus, true);
    globalThis.addEventListener?.("resize", handleViewportChange);
    globalThis.addEventListener?.("scroll", handleViewportChange, true);
    globalThis.visualViewport?.addEventListener?.("resize", handleViewportChange);
    globalThis.visualViewport?.addEventListener?.("scroll", handleViewportChange);

    return () => {
      if (frame !== undefined) globalThis.cancelAnimationFrame?.(frame);
      globalThis.document?.removeEventListener("pointerdown", handleOutsidePointer, true);
      globalThis.document?.removeEventListener("focusin", handleOutsideFocus, true);
      globalThis.removeEventListener?.("resize", handleViewportChange);
      globalThis.removeEventListener?.("scroll", handleViewportChange, true);
      globalThis.visualViewport?.removeEventListener?.("resize", handleViewportChange);
      globalThis.visualViewport?.removeEventListener?.("scroll", handleViewportChange);
    };
  }, [activeIndex, open, positionMenu]);

  const chooseSkin = (option) => {
    onChange?.(normalizeSkin(option.id));
    setAnnouncement(`界面已切换为${option.label}`);
    closeMenu(true);
  };

  const moveActiveOption = (nextIndex) => {
    const normalizedIndex = (nextIndex + SKIN_OPTIONS.length) % SKIN_OPTIONS.length;
    setActiveIndex(normalizedIndex);
    optionRefs.current[normalizedIndex]?.focus();
  };

  const handleTriggerKeyDown = (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      if (open) closeMenu(true);
      else openMenu(currentIndex);
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      openMenu(currentIndex);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      openMenu(SKIN_OPTIONS.length - 1);
    } else if (event.key === "Escape" && open) {
      event.preventDefault();
      closeMenu(true);
    }
  };

  const handleOptionKeyDown = (event, option, index) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      moveActiveOption(index + 1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      moveActiveOption(index - 1);
    } else if (event.key === "Home") {
      event.preventDefault();
      moveActiveOption(0);
    } else if (event.key === "End") {
      event.preventDefault();
      moveActiveOption(SKIN_OPTIONS.length - 1);
    } else if (event.key === "Escape") {
      event.preventDefault();
      closeMenu(true);
    } else if (event.key === "Tab") {
      event.preventDefault();
      setOpen(false);
      globalThis.requestAnimationFrame?.(() => {
        focusAdjacentControl(triggerRef.current, event.shiftKey);
      });
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      chooseSkin(option);
    }
  };

  const menu = open && globalThis.document?.body
    ? createPortal(
      <div
        ref={menuRef}
        id={menuId}
        className="skin-switcher-menu"
        data-theme={skin}
        role="menu"
        aria-label="界面皮肤"
        aria-orientation="vertical"
        style={{
          top: `${placement.top}px`,
          left: `${placement.left}px`,
          width: `${placement.width}px`,
        }}
      >
        <div className="skin-switcher-menu-heading" role="presentation">
          <strong>界面皮肤</strong>
          <span>仅调整页面色温</span>
        </div>
        <div className="skin-switcher-options" role="presentation">
          {SKIN_OPTIONS.map((option, index) => (
            <button
              key={option.id}
              ref={(node) => { optionRefs.current[index] = node; }}
              type="button"
              className="skin-switcher-option"
              data-skin-option={option.id}
              role="menuitemradio"
              aria-checked={skin === option.id}
              tabIndex={index === activeIndex ? 0 : -1}
              onClick={() => chooseSkin(option)}
              onFocus={() => setActiveIndex(index)}
              onKeyDown={(event) => handleOptionKeyDown(event, option, index)}
            >
              <SkinSample skin={option.id} large />
              <span className="skin-switcher-option-copy">
                <strong>{option.label}</strong>
                <small>{option.description}</small>
              </span>
              <Check className="skin-switcher-option-check" size={16} weight="bold" aria-hidden="true" />
            </button>
          ))}
        </div>
      </div>,
      globalThis.document.body,
    )
    : null;

  return (
    <div
      ref={rootRef}
      className={`skin-switcher ${className}`.trim()}
      data-open={open ? "true" : "false"}
    >
      <button
        ref={triggerRef}
        type="button"
        className="skin-switcher-trigger"
        aria-label={`界面皮肤：${currentOption.label}`}
        aria-haspopup="menu"
        aria-controls={menuId}
        aria-expanded={open}
        onClick={() => (open ? closeMenu() : openMenu(currentIndex))}
        onKeyDown={handleTriggerKeyDown}
      >
        <SkinSample skin={skin} />
        <span className="skin-switcher-current">{currentOption.label}</span>
        <CaretDown className="skin-switcher-caret" size={13} weight="bold" aria-hidden="true" />
      </button>
      {menu}
      <span className="visually-hidden" role="status" aria-live="polite">{announcement}</span>
    </div>
  );
}
