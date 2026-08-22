# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

- Individual creators who use personal points to generate and manage only their own work.
- Company owners, team leads, and operators who create with a shared company wallet under server-enforced permissions.
- Platform administrators who operate entitlements, model economics, provider reliability, publishing exceptions, and access audit.

## Product Purpose

旭天 AI VIDEO is a production system for generating, managing, reviewing, and publishing AI media. Success means a user can move from an idea or stored artifact to a capability-valid generation, a durable result, and an auditable next action without crossing identity, permission, wallet, or tenant boundaries.

## Positioning

The product joins a creator-facing multi-model studio with company governance, separated personal and company economics, provider-neutral Relay execution, durable artifact custody, and an approval-based publishing loop in one auditable system.

## Operating Context

Creators work across inspiration, creation history, private media, generated works, publishing, and settings. Company users also manage members, model and resource entitlements, reports, and the shared wallet. Platform administrators use a dense operations canvas for task state, model profitability, company health, entitlement distribution, Relay channels, publishing and asset exceptions, and access audit.

## Capabilities and Constraints

- The browser calls only the customer Platform; provider credentials and routing stay in Relay.
- Model and generation controls come from server-computed, versioned capabilities. Unsupported fields remain unavailable and stale values are reconciled before submission.
- Generation uses stable idempotency and reserve then settle or release accounting. Failed work is not charged.
- Personal points and company minor-unit money are separate scopes with separate pricing, wallets, reporting, and audit.
- Task, artifact, preview, download, company, and administrator access fail closed at their authenticated scope.
- Publishing uses durable stored artifacts, destination accounts, human approval, scheduling, official adapters, and explicit unknown-submission reconciliation.
- Demo data is allowed only in an explicitly labeled development/demo mode and must never appear as production truth.
- Existing Platform and Relay APIs, permissions, safety states, workflows, and audit evidence are authoritative during frontend redesign.

## Brand Commitments

- Product name: 旭天 AI VIDEO.
- Use the approved X-shaped mark and wordmark from `public/brand/xutian-brand-source.png` and its derived transparent product assets.
- The customer product is light-only. `纯白` is the source of truth; `雾灰` and `暖米` may alter color tokens only. No dark theme.
- The interface should feel like a precise, media-first production instrument: authored, calm, technically credible, and free of generic AI decoration.

## Evidence on Hand

- Real product-owned community media under `public/community/`.
- Approved administrator-console visual source at `C:/Users/16691/.codex/generated_images/019fa417-190d-7540-acdb-68e41bd60346/exec-ee8a2243-4761-4933-a45d-ee434798daf1.png`.
- Current runnable frontend and backend contracts in this repository, including automated permissions, accounting, publishing, artifact, and Relay tests.
- No production customer logos, testimonials, provider-performance claims, or public social-community metrics are approved; future UI must not fabricate them.

## Product Principles

1. Show the real task and its state before decoration.
2. Preserve identity, permission, money, and tenant boundaries in every view.
3. Turn generated results into durable, auditable next actions rather than isolated downloads.
4. Keep high-frequency creation direct while progressively disclosing capability-driven detail.
5. Treat desktop and mobile as intentional compositions, not scaled copies.

## Accessibility & Inclusion

All primary flows must remain keyboard operable, use explicit names and state semantics, preserve visible focus, respect reduced motion, maintain readable Chinese typography, and provide touch targets of at least 44px on phone layouts.
