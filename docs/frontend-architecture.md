# Frontend architecture boundaries

This document records the compatibility-first frontend refactor boundary. It is
an implementation contract, not a proposal to replace the Platform/Relay
architecture or the production authentication model.

## Stable public boundaries

- `App`, `ManagementConsole`, `AdminOperationsConsole`, and
  `createPlatformClient` keep their existing named exports and runtime behavior.
- `AuthGateway` remains the only browser authentication authority. No general
  state store may mirror, override, or persist its session, CSRF, invitation,
  step-up, or invalidation state.
- `platformClient.js` remains the compatibility import. It composes domain
  facades over one request core; callers do not need to change in this refactor.
- Route extraction must preserve canonical paths, History API behavior,
  permission checks, abort fencing, idempotency keys, and fail-closed errors.

## Current module direction

- `pages/studio/` owns route-level Studio views that receive data and handlers
  through props.
- `components/studio/` and `components/management/` own reusable presentation
  and pure projection helpers.
- `api/platformCore.js` owns runtime configuration, request IDs, CSRF injection,
  credentials, abort/timeout behavior, error normalization, and download URL
  validation.
- `api/*Api.js` files own endpoint methods by domain. They are facades, not
  independent transports or caches.
- `design-system/` remains the only CSS entry point. A route is considered
  migrated only when its replacement rules exist in the appropriate system
  layer and the matching legacy selectors have been deleted.

## State ownership decision

| State | Owner now | Later decision |
| --- | --- | --- |
| Login, session, CSRF, invitation handoff, step-up, cross-tab invalidation | `AuthGateway` Context | Keep. Never move to Zustand or a query cache. |
| Canonical route, active surface, company/personal context | App/History API with server session surfaces | Extract behind a small route/workspace reducer or Context only when page-container extraction no longer needs App-local coordination. Do not add a general store first. |
| Generation draft, local files, capability reconciliation, composer disclosure | Route-local state | Keep local; extract to a reducer/hook for testability. Files and unsaved drafts must not enter a server cache. |
| Page filters, drawer state, focus restoration, transient toasts | Owning page/container | Keep local or URL-backed. Zustand provides no current benefit. |
| Skin and Studio preferences | Existing focused hooks and browser storage | Keep isolated by subject; do not merge with authentication. |
| Models, tasks, history, artworks, wallet, members, permissions, reports, operations evidence | Server-authoritative requests in page containers | Candidate for React Query after query keys, freshness, cancellation, authorization scope, and invalidation contracts are frozen. |
| Guarded mutations and uncertain writes | Domain facade plus existing caller fencing/readback | A future query layer may coordinate invalidation, but mutation retry must remain disabled unless the endpoint has the existing stable idempotency/readback contract. |

## Why no Zustand in this refactor

The remaining shared state is either security-sensitive authentication state,
server-authoritative data, or route-local interaction state. Moving all three
into one client store would duplicate authorities and make tenant/context
invalidation harder to prove. Re-evaluate Zustand only if a concrete piece of
non-server, non-authentication state must be edited by multiple independently
mounted routes and cannot be represented in the URL or a narrow Context.

## React Query entry criteria

React Query is a possible later server-state layer, not part of the structural
refactor. Before adding it, each candidate domain must have:

1. canonical query keys including personal/company/admin scope;
2. explicit stale-time and refetch behavior;
3. abort propagation through the shared request core;
4. permission and logout invalidation rules;
5. mutation idempotency and ambiguous-response behavior;
6. regression coverage proving no cross-tenant or cross-surface cache reuse.

## CSS migration rule

Migrate one route slice at a time:

1. capture current computed-style and browser behavior;
2. place the complete replacement in the correct design-system layer;
3. delete the matching selectors from legacy CSS in the same change;
4. add a regression that prevents the legacy selectors from returning;
5. require the legacy byte/selector count to decrease.

The authentication fallback is the first completed live route slice under this
rule. The retired Studio preference selectors were also removed from every
legacy layer; the current `/settings` account center already lives in the
design-system authentication layer and is not represented as a second legacy
route migration.

## TypeScript

TypeScript is intentionally deferred. It should be a separately reviewed
version after file boundaries and public facade contracts stabilize. Structural
extraction, state-library adoption, CSS migration, and language migration must
not be combined into one release.
