# Studio Creation Workbenches — Design QA

- Review completed: 2026-08-29
- Production route: `http://127.0.0.1:4178/creation`
- Reviewed surfaces: invitation entry, 3D 导演台, 导演手记, 谱系画布, persistent 极速档, Company navigation
- Reference set:
  - invitation launcher: `C:\Users\16691\AppData\Local\Temp\codex-clipboard-dd3c08a3-a2b4-4d18-9be4-264c3b954027.png`
  - 导演手记: `C:\Users\16691\AppData\Local\Temp\codex-clipboard-36991892-994c-42a7-a89e-3594dfaa23cc.png`
  - 谱系画布: `C:\Users\16691\AppData\Local\Temp\codex-clipboard-c044615d-e221-47dd-b499-ae5e4192f7a1.png`

## Combined comparison evidence

- Entry: `.impeccable/review/entry-comparison.png`
- Notebook: `.impeccable/review/notebook-comparison.png`
- Canvas: `.impeccable/review/canvas-comparison.png`
- 3D console: `.impeccable/review/console-desktop.png` and `.impeccable/review/console-desk-desktop.png`
- Responsive: `.impeccable/review/mobile-390.png`, `.impeccable/review/mobile-320.png`, and `.impeccable/review/notebook-mobile-fixed.png`
- Management selected state: `.impeccable/review/management-nav-desktop.png`

The implementation was judged from the side-by-side comparison inputs, not from isolated screenshots.

## Findings and fixes

- [fixed] The production route no longer opens on an empty-report pattern. Its empty/default state is a usable invitation with three distinct workbench paths and the in-flow quick composer.
- [fixed] 3D 导演台, 导演手记, and 谱系画布 are URL-addressable production states with real task, artifact, continuation, and publishing callbacks. They do not manufacture local success or accounting state.
- [fixed] The desktop invitation heading was wrapping after five Chinese characters. It now remains one line at desktop widths and balances naturally on phones.
- [fixed] Notebook task media occupied only half of its preview at 320px because the grid item resolved to an intrinsic track. Media is now pinned to the full preview bounds.
- [fixed] Company navigation and the nested enterprise-detail tabs each have exactly one selected affordance. Computed browser evidence shows a soft-violet fill, 8px radius, no selected border, and no before/after indicator.
- [fixed] Shared command controls and management actions now use the purposeful 8–10px control scale; desktop drawers use a 16px leading edge and phone drawers use 16px top corners. Tables and structural dividers remain square.
- [fixed] Static task imagery no longer carries a fake play glyph. A successful video exposes native playback controls only after its real preview URL is available.
- [fixed] The composer has one prioritized submit message, one valid price surface, and one disabled-action reason. Readiness details remain progressive disclosure.
- [fixed] Active tasks show an exact remaining time only when the server supplies a valid numeric ETA or timezone-qualified completion timestamp.
- [P3, intentional] The entry emphasizes the equipped 3D workbench instead of copying the reference's three equal inspiration cards. This follows the later approved hierarchy while retaining the reference's invitation rhythm and light material.
- [P3, intentional] Canvas renders verified task nodes and a selected-node action inspector rather than decorative mock branches. The dotted spatial field, variable node placement, selection treatment, and continuation actions preserve the approved canvas metaphor without inventing lineage data.

## Responsive and accessibility evidence

- Browser viewports: 1440×1000, 390×844, and 320×760.
- The 390px and 320px entry keep one horizontal route rail, a readable invitation, all three workbench paths, and the compact in-flow quick-creation launcher.
- Notebook uses a single scroll owner and preserves 44px actions on narrow phones.
- 3D monitor cards become a horizontal viewing sequence on phones; its control desk and handoff remain reachable.
- The canvas and record filters remain reachable through progressive disclosure.
- Runtime browser console warnings/errors: none.
- Impeccable detector: no findings.

## Verification

- Targeted creation, composer, permissions, preview lease, mobile, management, and accessibility contracts: passed.
- Frontend release-closure guard: 2 / 2 passed.
- Production build: passed; 888 modules transformed.
- Full root suite: all frontend failures resolved; one Relay provenance failure remains outside this frontend candidate and was not changed.
- Prototype build: passed.

final result: passed
