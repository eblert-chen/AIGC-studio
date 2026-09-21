# Creation Default Entry — Design QA

## Visual truth

- Invitation-entry source: `C:/Users/16691/AppData/Local/Temp/codex-clipboard-dd3c08a3-a2b4-4d18-9be4-264c3b954027.png` (1436×872).
- Latest structural source: `C:/AI-agent-project-s2/AI-video/docs/creation-3d-console.html`, captured as `.impeccable/review/source-3d-console-full.png` (1265×1867).
- User-approved hierarchy applied after those frames: the invitation launcher is the default entry; `导演手记` and `谱系画布` remain separate advanced workbenches; the launcher has no duplicate composer; `直接生成` opens the 3D director console.

## Implementation evidence

| State | Viewport | Screenshot |
| --- | ---: | --- |
| Default entry | 1440×900, 1× | `.impeccable/review/entry-desktop-final-v4.png` |
| 3D director console | 1440×900, 1× | `.impeccable/review/console-desktop-final-v4.png` |
| Default entry | 390×844, 1× | `.impeccable/review/entry-mobile-final-v3.png` |
| 3D director console | 390×844, 1× | `.impeccable/review/console-mobile-final-v3.png` |
| Default entry | 320×720, 1× | `.impeccable/review/entry-320-final-v3.png` |
| 3D director console | 320×720, 1× | `.impeccable/review/console-320-final-v3.png` |

## Comparison evidence

- Full entry comparison, source left and implementation right: `.impeccable/review/entry-comparison-final-v4.png`.
- Full console comparison, source left and implementation right: `.impeccable/review/console-comparison-final-v4.png`.
- Focused-region evidence is covered by the separate 390×844 and 320×720 frames because each mobile frame contains the complete current task surface: route choice on entry, and monitor/spec/slate/action on the console.

## Intentional differences from early concepts

- Removed the entry-page composer dock because it duplicated the `直接生成` route and was explicitly rejected.
- Added the third `谱系画布` route because the latest product hierarchy defines Notebook and Canvas as two separate advanced workbenches.
- Replaced concept-only gradients and placeholders with the approved brand, project-owned media, Phosphor icons, and self-hosted Chinese-first typography.
- Removed the unsupported “60 秒出片” and fixed-cost claims. The prototype says that no task or charge occurs and defers readiness and pricing to the server-backed product.

## Browser and interaction checks

- `直接生成` opens the in-flow 3D director console.
- Inspiration cards open the console with the matching real prompt and reference media.
- Notebook and Canvas remain honest advanced-workbench stubs and show one non-blocking status message rather than a fake screen.
- Ratio, duration, quality, and engine controls update pressed state and the monitor/spec summary.
- The preview control now starts and pauses a visibly moving, explicitly labelled demo preview; `aria-pressed`, icon, and local status update together.
- `复用设置` restores the previous demo Prompt, reference media, ratio, duration, quality, and engine as one coherent draft.
- Mobile specifications use progressive disclosure; the prompt slate and submit reason remain visible at 390px and 320px.
- Prompt tools populate/extend the field; the submit action enables only after content exists; the demo submission reports `演示任务已进入队列 · 未连接后端` without charging or pretending to call production.
- All visible actions use semantic buttons, grouped controls expose pressed/expanded state, the input has a visible label, and its verified 3px focus ring also lifts the containing slate.
- At 390px and 320px, every visible button/link is at least 44×44px, mobile navigation has fixed previous/next controls, and `设置` remains reachable in the rail.
- Browser console: 0 errors, 0 warnings after desktop/mobile interactions.

## Findings and fixes

- Fixed mobile horizontal document overflow and hid non-informative Windows scrollbars without removing touch/trackpad scrolling.
- Replaced a detector-flagged `max-height`/`margin-top` layout transition with a transform/opacity reveal. The detector ran once as required and was in degraded regex mode because its optional HTML parser modules were unavailable.
- Removed the hidden file input from the accessibility tree so the two visible reference triggers remain the only focusable upload actions.
- Reworded fixed capability, previous-result, and submission feedback as explicit local demonstration state; the prototype never claims a real queue, readiness result, price, or charge.
- No remaining visible clipping, unreachable primary action, duplicated readiness/error/price state, false promise, or P0–P2 visual issue was observed in the final comparison frames.

## Verification

- `npm run build`: passed.
- `npm run test:sites`: passed, 4/4.
- Independent Impeccable finish review: approved after functional-affordance, touch-target, focus-contrast, mobile-navigation, and demo-state corrections.

## Final result

passed
