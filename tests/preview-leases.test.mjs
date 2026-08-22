import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { studioSource } from "./studio-source.mjs";

import {
  activePreviewUrl,
  createPreviewLease,
  nextPreviewCleanupDelay,
  PREVIEW_EXPIRY_SAFETY_MS,
  removeExpiredPreviewLeases,
  removePreviewLease,
} from "../src/previewLeases.js";

const appSource = await readFile(new URL("../src/App.jsx", import.meta.url), "utf8");
const creationSource = await readFile(new URL("../src/CreationHub.jsx", import.meta.url), "utf8");
const publishingSource = await readFile(new URL("../src/PublishingCenter.jsx", import.meta.url), "utf8");

test("preview leases reject missing TTL and stop before the signed URL expires", () => {
  const now = 10_000;
  const lease = createPreviewLease("https://storage.example/preview", 30, now);
  assert.deepEqual(lease, {
    url: "https://storage.example/preview",
    expiresAt: 40_000,
  });
  assert.equal(activePreviewUrl(lease, 40_000 - PREVIEW_EXPIRY_SAFETY_MS - 1), lease.url);
  assert.equal(activePreviewUrl(lease, 40_000 - PREVIEW_EXPIRY_SAFETY_MS), "");
  assert.equal(createPreviewLease(lease.url, 0, now), null);
  assert.equal(createPreviewLease("", 30, now), null);
});

test("preview lease cleanup is immutable and exposes the next cleanup deadline", () => {
  const leases = {
    expired: { url: "https://storage.example/old", expiresAt: 10_500 },
    active: { url: "https://storage.example/new", expiresAt: 30_000 },
  };
  const cleaned = removeExpiredPreviewLeases(leases, 10_000);
  assert.notEqual(cleaned, leases);
  assert.deepEqual(Object.keys(cleaned), ["active"]);
  assert.equal(nextPreviewCleanupDelay(cleaned, 10_000), 19_000);
  assert.deepEqual(removePreviewLease(cleaned, "active"), {});
  assert.equal(removePreviewLease(cleaned, "missing"), cleaned);
});

test("all live preview consumers validate leases and clear failed media", () => {
  assert.match(appSource, /createPreviewLease\(url\.toString\(\), access\.expires_seconds\)/);
  assert.match(appSource, /nextPreviewCleanupDelay\(artworkPreviewUrls\)/);
  assert.match(appSource, /removeExpiredPreviewLeases\(current\)/);
  assert.match(studioSource, /onError=\{leasedPreviewUrl \? \(\) => onPreviewError\?\.\(key\) : undefined\}/);
  assert.match(creationSource, /activePreviewUrl\(previewUrls\[previewKey\]\)/);
  assert.match(creationSource, /onError=\{previewUrl \? onPreviewError : undefined\}/);
  assert.match(publishingSource, /activePreviewUrl\(previewUrls\[previewKey\]\)/);
  assert.match(publishingSource, /onError=\{previewUrl \? onPreviewError : undefined\}/);
});
