---
name: 旭天 AI studio · Creation Default Entry Prototype
description: 邀请式创作入口与功能型 3D 导演台的独立视觉原型契约
colors:
  ink: "#1b1e24"
  ink-muted: "#596270"
  ink-subtle: "#7c8592"
  canvas: "#f6f6f3"
  panel: "#ffffff"
  field: "#f0f1f3"
  field-strong: "#e7e8eb"
  accent: "#5a55d2"
  accent-hover: "#4a45c2"
  accent-soft: "#eeedfb"
  focus-ring: "#6f68d8"
  success: "#19845e"
  warm-desk: "#e9e8df"
typography:
  display:
    fontFamily: "Noto Sans SC Variable, Manrope Variable, Microsoft YaHei, sans-serif"
    fontSize: "clamp(27px, 2.4vw, 32px)"
    fontWeight: 760
    lineHeight: 1.25
    letterSpacing: "-0.025em"
  body:
    fontFamily: "Noto Sans SC Variable, Manrope Variable, Microsoft YaHei, sans-serif"
    fontSize: "13.5px"
    fontWeight: 400
    lineHeight: 1.62
  label:
    fontFamily: "Noto Sans SC Variable, Manrope Variable, Microsoft YaHei, sans-serif"
    fontSize: "11.5px"
    fontWeight: 650
rounded:
  control: "10px"
  navigation: "12px"
  media: "14px"
  panel: "16px"
  shell: "20px"
  pill: "999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "24px"
  xxl: "32px"
components:
  action-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.panel}"
    rounded: "{rounded.control}"
    padding: "0 16px"
    height: "40px"
  path-card:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    rounded: "{rounded.panel}"
    padding: "17px 18px 16px"
  prompt-slate:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    rounded: "{rounded.panel}"
---

# Design System: Creation Default Entry Prototype

> **Scope boundary:** This file governs only `prototypes/creation-default-entry`. It documents a standalone visual example; it is not an approved production-frontend baseline and must not override the repository-root design system or product code.

## Overview

**Creative North Star: “邀请进入片场，再走上导演台”**

The default state is an invitation, not an empty report or a pre-opened settings form. A centered question, three truthful creation paths, and optional inspiration starters give the user one clear decision: quick generation, Director Notebook, or Lineage Canvas.

The interaction hierarchy is fixed:

1. **Default entry:** invitation and routing only; it contains no composer or duplicate quick-generation dock.
2. **直接生成:** opens the functional 3D Director Console in-flow.
3. **导演手记 / Notebook** and **谱系画布 / Canvas:** remain separate advanced workbenches with their own topology, state anatomy, and interaction metaphor. Do not flatten either into the entry or merge all three modes into one hybrid screen.

The 3D metaphor is operational: monitors answer “what am I looking at,” desk modules answer “what am I setting,” and the slate answers “what am I asking the model to make.” Every spatial object must carry real media, data, state, or an action.

## Colors

The world is near-white and warm-light: graphite copy on quiet paper, white working surfaces, a warm physical desk, and restrained violet for selection and action.

- Use `canvas` for the invitation field and `panel` for actionable surfaces.
- Use `warm-desk` only to establish the console’s physical workbench plane.
- Use `accent` sparingly for the active route, primary path, focus, and generate action; `accent-soft` is its low-emphasis selected surface.
- Use `success` only for verified readiness or availability. Do not use color as the sole status signal.
- Use `ink-muted` and `ink-subtle` for hierarchy, not ultra-low contrast or sub-10px copy.

**The No Decorative Gradient Rule.** Do not add gradient backgrounds, purple glass, neon glow, or generic AI decoration. Real media and material depth supply the visual interest.

## Typography

Chinese is primary. Use `Noto Sans SC Variable` first, with `Manrope Variable` stabilizing Latin text and numerals and `Microsoft YaHei` as the system fallback.

- The invitation question is the only display-scale statement.
- Body copy remains calm and compact; path descriptions explain use cases rather than selling unsupported outcomes.
- Manrope may be used alone for scene/take notation, counters, and technical labels, never as the default Chinese face.
- Keep every visible label and annotation at 12px or larger; body and explanatory copy should remain 13px or larger when space permits.
- Avoid all-caps except concise production notation such as `SCENE 01 · TAKE 01`.

## Layout

- Desktop entry uses the shared top bar, a 200px route rail, and a centered invitation field. The three path cards share one row; inspiration starters sit below as secondary shortcuts.
- Inspiration does not create a second mode. Selecting one carries its real Prompt and reference media into the Director Console.
- Desktop console is one continuous scene: toolbar → three-monitor wall → physical desk → specification rack → dominant prompt slate. The slate remains the primary input and the current monitor remains the primary visual.
- At 390px and 320px, composition changes instead of compressing: navigation and cards scroll horizontally, the current monitor comes first in a snap-scrolling monitor rail, specifications collapse behind one summary, and the prompt slate becomes full width.
- On phones, the primary generate action is at least 48px high; other real touch targets should be at least 44px. The document root must not overflow horizontally.
- Mobile progressive disclosure must preserve access to every supported specification while keeping one vertical-scroll owner. Do not stack an expanded monitor wall, full parameter rack, and secondary project panels above the Prompt.

## Elevation & Depth

Depth is selective and semantic. Near-white tonal changes create the base hierarchy; soft shadows lift path choices, monitors, the prompt slate, and true overlays. Most controls remain border-light or borderless.

- `--shadow-soft` is for selectable entry surfaces and the prototype shell.
- `--shadow-lift` is reserved for the prompt slate, whose physical prominence communicates input priority.
- Monitor shadows and restrained perspective establish a functional viewing wall; phone layouts remove perspective and shadows that no longer explain hierarchy.
- Structural separation should come from spacing, type, material contrast, and selective elevation—not a 1px outline around every region.

**The Functional Depth Rule.** A shadow or transform must explain a usable layer, selection, or spatial relationship. If removing it does not reduce comprehension, remove it.

## Shapes

- Controls use purposeful 8–10px radii; navigation uses 12px; real media uses 14px; work surfaces and cards use 16px; only the outer desktop prototype shell uses 20px.
- Pills are limited to compact status, metadata, and tool actions. Do not turn all controls into pills.
- Media keeps its native 16:9 silhouette and clips to the media radius.
- Tables and structural dividers may stay precise and square, but creative surfaces must not regress to an ERP grid of hard rectangles and uniform gray borders.

## Components

### Invitation entry

- Lead with “今天想创作什么？” and one short explanation.
- Path cards are the only mode entry. `直接生成` is the default path; Notebook and Canvas are equally truthful but explicitly advanced.
- The entry must never include a Prompt field, generation parameters, readiness panel, price, or submit action.

### 3D Director Console

- The monitor wall shows real reference media, the current take/result, and a previous result. Each monitor action operates on that content.
- Desk modules expose real supported values for ratio, duration, quality, and engine. Production integration must replace prototype options with server-computed capabilities.
- The prompt slate is the dominant work surface: visible Chinese label, editable Prompt, compact tools, one primary “开机生成” action, and one truthful note.
- Spatial decoration with no product function is prohibited.

### State and truth

- Show each readiness, error, progress, disabled reason, and price fact at most once in the surface where it matters.
- Attach task progress to the current monitor or action; do not repeat it as banners, cards, and badges.
- A disabled primary action carries one clear reason. A ready action does not repeat model, readiness, and cost across multiple panels.
- This prototype does not submit or charge. Production copy and controls must defer to server-verified readiness, capabilities, grants, quota, concurrency, and pricing.
- Never promise fixed generation time, fixed price, or provider availability. In particular, do not restore “60 秒出片” or a hard-coded points/currency cost.

### Interaction and accessibility

- Use semantic buttons and fields. Option groups expose pressed state; disclosures expose expanded state; inputs retain visible labels.
- Keep the violet 3px `:focus-visible` ring, restore focus after dismissals, and respect `prefers-reduced-motion`.
- Horizontal rails remain keyboard/touch scrollable even when scrollbars are visually hidden.

## Do's and Don'ts

### Do

- **Do** make the empty/default creation state immediately inviting and actionable through mode choice.
- **Do** use project-owned or user-selected media; label prototype/demo state truthfully.
- **Do** let the Prompt dominate the console and reveal specifications progressively on small screens.
- **Do** preserve Notebook and Canvas as independent advanced workbenches.
- **Do** verify both 390px and 320px compositions, primary-action reachability, focus visibility, and absence of document overflow.

### Don't

- **Don't** place a composer on the entry or duplicate the `直接生成` path with another writing surface.
- **Don't** build ERP-like 1px-box partitions, card-on-card stacks, or a settings-panel wall.
- **Don't** use decorative 3D, gradients, purple glass, glows, or generic AI motifs.
- **Don't** substitute placeholders for real media or duplicate one state across several UI regions.
- **Don't** fabricate duration, price, readiness, task submission, provider capability, or charging claims.
