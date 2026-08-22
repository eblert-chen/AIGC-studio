import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { inflateSync } from "node:zlib";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const readText = (relativePath) => readFile(path.join(root, relativePath), "utf8");

const paeth = (left, up, upperLeft) => {
  const estimate = left + up - upperLeft;
  const leftDistance = Math.abs(estimate - left);
  const upDistance = Math.abs(estimate - up);
  const upperLeftDistance = Math.abs(estimate - upperLeft);
  if (leftDistance <= upDistance && leftDistance <= upperLeftDistance) return left;
  return upDistance <= upperLeftDistance ? up : upperLeft;
};

function rgbaPngEvidence(buffer) {
  assert.equal(buffer.toString("hex", 0, 8), "89504e470d0a1a0a");
  const width = buffer.readUInt32BE(16);
  const height = buffer.readUInt32BE(20);
  assert.equal(buffer[24], 8, "brand PNGs must use eight-bit channels");
  assert.equal(buffer[25], 6, "brand PNGs must use RGBA colour type");
  assert.equal(buffer[28], 0, "brand PNGs must stay non-interlaced for deterministic inspection");

  const idat = [];
  for (let offset = 8; offset < buffer.length;) {
    const length = buffer.readUInt32BE(offset);
    const type = buffer.toString("ascii", offset + 4, offset + 8);
    if (type === "IDAT") idat.push(buffer.subarray(offset + 8, offset + 8 + length));
    offset += 12 + length;
  }

  const pixels = inflateSync(Buffer.concat(idat));
  const stride = width * 4;
  let sourceOffset = 0;
  let previous = Buffer.alloc(stride);
  let minAlpha = 255;
  let maxAlpha = 0;

  for (let rowIndex = 0; rowIndex < height; rowIndex += 1) {
    const filter = pixels[sourceOffset];
    sourceOffset += 1;
    const row = Buffer.allocUnsafe(stride);
    for (let index = 0; index < stride; index += 1) {
      const encoded = pixels[sourceOffset + index];
      const left = index >= 4 ? row[index - 4] : 0;
      const up = previous[index];
      const upperLeft = index >= 4 ? previous[index - 4] : 0;
      const predictor = filter === 0 ? 0
        : filter === 1 ? left
          : filter === 2 ? up
            : filter === 3 ? Math.floor((left + up) / 2)
              : filter === 4 ? paeth(left, up, upperLeft)
                : NaN;
      assert.ok(Number.isFinite(predictor), `unsupported PNG row filter ${filter}`);
      row[index] = (encoded + predictor) & 0xff;
      if (index % 4 === 3) {
        minAlpha = Math.min(minAlpha, row[index]);
        maxAlpha = Math.max(maxAlpha, row[index]);
      }
    }
    sourceOffset += stride;
    previous = row;
  }

  return { width, height, minAlpha, maxAlpha };
}

test("approved XuTian assets are project-owned transparent PNGs", async () => {
  const [wordmark, symbol, source] = await Promise.all([
    readFile(path.join(root, "public", "brand", "xutian-wordmark-light.png")),
    readFile(path.join(root, "public", "brand", "xutian-symbol-light.png")),
    readFile(path.join(root, "public", "brand", "xutian-brand-source.png")),
  ]);

  assert.ok(source.length > 1_000_000, "the supplied brand sheet must remain archived in the project");
  const wordmarkEvidence = rgbaPngEvidence(wordmark);
  const symbolEvidence = rgbaPngEvidence(symbol);
  assert.deepEqual(
    { width: wordmarkEvidence.width, height: wordmarkEvidence.height },
    { width: 1024, height: 349 },
  );
  assert.deepEqual(
    { width: symbolEvidence.width, height: symbolEvidence.height },
    { width: 512, height: 512 },
  );
  assert.equal(wordmarkEvidence.minAlpha, 0, "the light wordmark must contain transparent pixels");
  assert.equal(symbolEvidence.minAlpha, 0, "the compact symbol must contain transparent pixels");
  assert.equal(wordmarkEvidence.maxAlpha, 255, "the light wordmark must retain opaque artwork");
  assert.equal(symbolEvidence.maxAlpha, 255, "the compact symbol must retain opaque artwork");
});

test("one reusable brand component owns wordmark and compact-symbol switching", async () => {
  const component = await readText("src/BrandLogo.jsx");
  const styles = await readText("src/design-system/branding.css");
  const entry = await readText("src/design-system/index.css");

  assert.match(component, /BRAND_NAME\s*=\s*"旭天 AI VIDEO"/);
  assert.match(component, /xutian-wordmark-light\.png/);
  assert.match(component, /xutian-symbol-light\.png/);
  assert.match(component, /variant\s*===\s*"responsive"/);
  assert.match(component, /<picture className=\{classes\}>/);
  assert.match(component, /media=\{`\(max-width: \$\{mobileBreakpoint\}px\)`\}/);
  assert.match(component, /alt=\{decorative \? "" : label\}/);
  assert.match(styles, /brand-logo__image[\s\S]*?object-fit:\s*contain/);
  assert.doesNotMatch(styles, /brand-logo[^}]*object-fit:\s*cover/);
  assert.match(entry, /@import\s+"\.\/branding\.css"\s+layer\(system\.routes\)/);
});

test("Studio, authentication, Company, Operations and browser chrome use the approved brand", async () => {
  const [app, authShell, management, operations, html] = await Promise.all([
    readText("src/App.jsx"),
    readText("src/auth/AuthShell.jsx"),
    readText("src/ManagementConsole.jsx"),
    readText("src/admin/OperationsConsole.jsx"),
    readText("index.html"),
  ]);

  assert.match(app, /<BrandLogo variant="responsive" \/>/);
  assert.match(authShell, /auth-brand[\s\S]*?<BrandLogo variant="responsive"/);
  assert.match(management, /control-mobile-home[\s\S]*?<BrandLogo variant="symbol" \/>/);
  assert.match(management, /control-brand[\s\S]*?<BrandLogo variant="wordmark" \/>/);
  assert.match(operations, /className="ops-brand"[\s\S]*?<BrandLogo variant="responsive" mobileBreakpoint=\{820\} \/>/);
  assert.match(html, /rel="icon"[^>]+xutian-symbol-light\.png/);
  assert.match(html, /<title>旭天 AI VIDEO<\/title>/);

  assert.doesNotMatch(app, /影创 Verse/);
  assert.doesNotMatch(operations, /影创 Verse/);
});
