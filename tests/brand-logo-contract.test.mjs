import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { inflateSync } from "node:zlib";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const readText = (relativePath) => readFile(path.join(root, relativePath), "utf8");
const readBinary = (relativePath) => readFile(path.join(root, relativePath));

// Decode the shipped transparent PNGs without adding an image-library dependency.
function decodeRgbaPng(png) {
  assert.equal(png.toString("hex", 0, 8), "89504e470d0a1a0a");
  const width = png.readUInt32BE(16);
  const height = png.readUInt32BE(20);
  assert.equal(png[24], 8, "brand PNGs must use 8-bit channels");
  assert.equal(png[25], 6, "brand PNGs must retain RGBA transparency");
  assert.equal(png[28], 0, "brand PNGs must be non-interlaced");
  const chunks = [];
  for (let offset = 8; offset < png.length;) {
    const length = png.readUInt32BE(offset);
    if (png.toString("ascii", offset + 4, offset + 8) === "IDAT") {
      chunks.push(png.subarray(offset + 8, offset + 8 + length));
    }
    offset += length + 12;
  }
  const raw = inflateSync(Buffer.concat(chunks));
  const stride = width * 4;
  assert.equal(raw.length, (stride + 1) * height);
  const pixels = Buffer.alloc(stride * height);
  for (let y = 0; y < height; y += 1) {
    const filter = raw[y * (stride + 1)];
    assert.ok(filter <= 4, "PNG row filter must be supported");
    for (let x = 0; x < stride; x += 1) {
      const index = y * stride + x;
      const left = x >= 4 ? pixels[index - 4] : 0;
      const above = y > 0 ? pixels[index - stride] : 0;
      const corner = x >= 4 && y > 0 ? pixels[index - stride - 4] : 0;
      const prediction = left + above - corner;
      const distances = [left, above, corner].map((value) => Math.abs(prediction - value));
      const paeth = distances[0] <= distances[1] && distances[0] <= distances[2]
        ? left : distances[1] <= distances[2] ? above : corner;
      const predictor = [0, left, above, Math.floor((left + above) / 2), paeth][filter];
      pixels[index] = (raw[y * (stride + 1) + x + 1] + predictor) & 255;
    }
  }
  return { width, height, pixels, alpha: (x, y) => pixels[(y * width + x) * 4 + 3] };
}

test("approved XuTian camera wordmark is scalable, single-colour and project-owned", async () => {
  const [wordmark, symbol, touchIcon, legacySource, previousConcept, selectedConcept] = await Promise.all([
    readText("public/brand/xutian-ai-studio-wordmark.svg"),
    readText("public/brand/xutian-ai-studio-symbol.svg"),
    readBinary("public/brand/xutian-ai-studio-touch-icon.png"),
    readBinary("public/brand/xutian-brand-source.png"),
    readBinary("public/brand/xutian-ai-studio-concept-source.png"),
    readBinary("public/brand/xutian-ai-studio-camera-concept-source.png"),
  ]);

  assert.ok(legacySource.length > 1_000_000, "the retired supplied brand sheet must remain archived");
  assert.ok(previousConcept.length > 500_000, "the previous selected direction must remain archived");
  assert.ok(selectedConcept.length > 1_000_000, "the selected camera direction must remain archived");
  assert.equal(
    createHash("sha256").update(selectedConcept).digest("hex"),
    "cc1d7221575389e29ccc61c4908c21dfbe847b933ea59ad0245652cee28cbd8d",
    "the archived source must be the exact user-selected camera wordmark",
  );
  assert.match(wordmark, /viewBox="0 0 520 180"/);
  assert.match(symbol, /viewBox="0 0 220 220"/);
  assert.match(wordmark, /旭天 AI studio/);
  assert.match(symbol, /旭天 AI studio/);
  assert.match(wordmark, /id="xutian-xu-frame"/);
  assert.match(wordmark, /id="xutian-camera-glyph"/);
  assert.match(wordmark, /id="xutian-camera-window-cutout" fill-rule="evenodd"/);
  assert.match(wordmark, /id="xutian-tian-glyph"/);
  for (const asset of [wordmark, symbol]) {
    assert.match(asset, /id="xutian-x-left" fill-rule="evenodd"/);
    assert.match(asset, /id="xutian-x-right"/);
    assert.doesNotMatch(asset, /xutian-circuit|<circle\b|#13b8a6/i, "decorative nodes and lines are retired");
  }
  for (const piece of ["left", "right"]) {
    const geometry = new RegExp(`<path id="xutian-x-${piece}"[^>]* d="([^"]+)"`);
    assert.equal(wordmark.match(geometry)?.[1], symbol.match(geometry)?.[1], "compact X must use the same two-piece geometry");
  }
  assert.match(wordmark, /id="xutian-x-play"[^>]*transform="translate\(20 23\) scale\(\.4\)"/);
  assert.match(symbol, /id="xutian-x-play"[^>]*transform="translate\(10 48\.84\) scale\(\.44643\)"/);
  assert.match(wordmark, /<use href="#glyph-latin-i"/, "the normal dot on the letter i is not a decorative circuit node");
  assert.doesNotMatch(wordmark, /<text\b/i, "the formal wordmark must not depend on installed fonts");
  assert.doesNotMatch(symbol, /<text\b/i, "the compact symbol must be path-only");
  assert.doesNotMatch(`${wordmark}${symbol}`, /<image\b/i, "production vectors must not embed the raster concept");
  assert.doesNotMatch(`${wordmark}${symbol}`, /(?:linear|radial)Gradient/i);

  const wordmarkColours = [...wordmark.matchAll(/#[0-9a-f]{3,6}/gi)]
    .map(([colour]) => colour.toLowerCase());
  const symbolColours = [...symbol.matchAll(/#[0-9a-f]{3,6}/gi)]
    .map(([colour]) => colour.toLowerCase());
  const colours = [...wordmarkColours, ...symbolColours];
  assert.ok(
    colours.every((colour) => colour === "#087b80"),
    "both production vectors must use only the approved deep-ocean teal",
  );
  assert.ok(wordmarkColours.includes("#087b80"), "the wordmark must use the approved deep-ocean teal");
  assert.ok(symbolColours.includes("#087b80"), "the compact symbol must use the approved deep-ocean teal");

  assert.equal(touchIcon.toString("hex", 0, 8), "89504e470d0a1a0a");
  assert.equal(touchIcon.readUInt32BE(16), 512);
  assert.equal(touchIcon.readUInt32BE(20), 512);
});

test("shipped icons preserve the slender X, transparent zigzag and play aperture", async () => {
  const touch = await readBinary("public/brand/xutian-ai-studio-touch-icon.png");
  const compatibilitySymbol = await readBinary("public/brand/xutian-symbol-light.png");
  assert.deepEqual(compatibilitySymbol, touch, "all compact PNG consumers must use the same rendered mark");
  const icon = decodeRgbaPng(touch);
  const alphaAtSource = (x, y) => icon.alpha(
    Math.round((10 + x * 0.44643) * icon.width / 220),
    Math.round((48.84 + y * 0.44643) * icon.height / 220),
  );
  assert.equal(alphaAtSource(151, 137), 255, "left chevron must remain visible");
  assert.equal(alphaAtSource(190, 137), 0, "play aperture must be truly transparent");
  assert.equal(alphaAtSource(236, 137), 255, "left chevron tip must remain visible");
  assert.equal(alphaAtSource(260, 137), 0, "the X waist must retain its zigzag separation");
  assert.equal(alphaAtSource(292, 137), 255, "right chevron waist must remain visible");
  assert.equal(alphaAtSource(327, 137), 0, "the mark must not fill in its side negative space");
  let minX = icon.width;
  let minY = icon.height;
  let maxX = 0;
  let maxY = 0;
  for (let y = 0; y < icon.height; y += 1) {
    for (let x = 0; x < icon.width; x += 1) {
      if (icon.alpha(x, y) < 128) continue;
      minX = Math.min(minX, x);
      maxX = Math.max(maxX, x);
      minY = Math.min(minY, y);
      maxY = Math.max(maxY, y);
    }
  }
  const ratio = (maxX - minX + 1) / (maxY - minY + 1);
  assert.ok(ratio > 1.61 && ratio < 1.66, `compact mark must keep the reference's wide proportions, got ${ratio}`);
  const wordmark = decodeRgbaPng(await readBinary("public/brand/xutian-wordmark-light.png"));
  for (const raster of [icon, wordmark]) {
    assert.equal(raster.alpha(0, 0), 0, "legacy PNG paths must not add an opaque background");
    for (let index = 0; index < raster.pixels.length; index += 4) {
      if (raster.pixels[index + 3] !== 255) continue;
      const colour = [...raster.pixels.subarray(index, index + 3)];
      // Composited antialiased edges can round a channel by one or two levels.
      assert.ok(colour.every((value, channel) => Math.abs(value - [8, 123, 128][channel]) <= 2), "opaque raster pixels must not retain old colours or mint nodes");
    }
  }
});

test("Platform, Relay and executable prototype ship the same approved geometry", async () => {
  const [wordmark, symbol, relayWordmark, relaySymbol, prototypeWordmark, prototypeSymbol, compatibilityWordmark, compatibilitySymbol, prototypeCompatibilityWordmark, prototypeCompatibilitySymbol, prototypeApp] = await Promise.all([
    readBinary("public/brand/xutian-ai-studio-wordmark.svg"),
    readBinary("public/brand/xutian-ai-studio-symbol.svg"),
    readBinary("backend/new-api-relay/web/public/brand/xutian-ai-studio-wordmark.svg"),
    readBinary("backend/new-api-relay/web/public/brand/xutian-ai-studio-symbol.svg"),
    readBinary("prototypes/creation-default-entry/public/brand/xutian-ai-studio-wordmark.svg"),
    readBinary("prototypes/creation-default-entry/public/brand/xutian-ai-studio-symbol.svg"),
    readBinary("public/brand/xutian-wordmark-light.png"),
    readBinary("public/brand/xutian-symbol-light.png"),
    readBinary("prototypes/creation-default-entry/public/brand/xutian-wordmark-light.png"),
    readBinary("prototypes/creation-default-entry/public/brand/xutian-symbol-light.png"),
    readText("prototypes/creation-default-entry/src/App.jsx"),
  ]);

  assert.deepEqual(relayWordmark, wordmark, "Relay wordmark must not drift from Platform");
  assert.deepEqual(relaySymbol, symbol, "Relay symbol must not drift from Platform");
  assert.deepEqual(prototypeWordmark, wordmark, "the executable prototype must use the production wordmark");
  assert.deepEqual(prototypeSymbol, symbol, "the executable prototype must use the production symbol");
  assert.deepEqual(prototypeCompatibilityWordmark, compatibilityWordmark, "legacy wordmark paths must render the selected design");
  assert.deepEqual(prototypeCompatibilitySymbol, compatibilitySymbol, "legacy symbol paths must render the selected design");
  assert.equal(compatibilityWordmark.toString("hex", 0, 8), "89504e470d0a1a0a");
  assert.equal(compatibilityWordmark.readUInt32BE(16), 1024);
  assert.equal(compatibilityWordmark.readUInt32BE(20), 355);
  assert.equal(compatibilitySymbol.toString("hex", 0, 8), "89504e470d0a1a0a");
  assert.equal(compatibilitySymbol.readUInt32BE(16), 512);
  assert.equal(compatibilitySymbol.readUInt32BE(20), 512);
  assert.match(prototypeApp, /xutian-ai-studio-wordmark\.svg/);
  assert.match(prototypeApp, /xutian-ai-studio-symbol\.svg/);
  assert.doesNotMatch(prototypeApp, /旭天 AI VIDEO/);
});

test("one reusable brand component owns wordmark and compact-symbol switching", async () => {
  const component = await readText("src/BrandLogo.jsx");
  const styles = await readText("src/design-system/branding.css");
  const entry = await readText("src/design-system/index.css");

  assert.match(component, /BRAND_NAME\s*=\s*"旭天 AI studio"/);
  assert.match(component, /xutian-ai-studio-wordmark\.svg/);
  assert.match(component, /xutian-ai-studio-symbol\.svg/);
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
  assert.match(html, /rel="icon"[^>]+xutian-ai-studio-symbol\.svg/);
  assert.match(html, /rel="apple-touch-icon"[^>]+xutian-ai-studio-touch-icon\.png/);
  assert.match(html, /<title>旭天 AI studio<\/title>/);

  assert.doesNotMatch(app, /影创 Verse/);
  assert.doesNotMatch(operations, /影创 Verse/);
});
