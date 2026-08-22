import { readFile } from "node:fs/promises";

const studioSourceUrls = [
  new URL("../src/App.jsx", import.meta.url),
  new URL("../src/components/studio/StudioCollectionControls.jsx", import.meta.url),
  new URL("../src/components/studio/studioPresentation.js", import.meta.url),
  new URL("../src/components/studio/StudioWorkspaceViews.jsx", import.meta.url),
  new URL("../src/pages/studio/ArtworksView.jsx", import.meta.url),
  new URL("../src/pages/studio/HistoryView.jsx", import.meta.url),
  new URL("../src/pages/studio/ResultDetailView.jsx", import.meta.url),
  new URL("../src/pages/studio/StudioStatusViews.jsx", import.meta.url),
];

export const studioSource = (await Promise.all(
  studioSourceUrls.map((url) => readFile(url, "utf8")),
)).join("\n");
