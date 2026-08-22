import test from "node:test";
import assert from "node:assert/strict";

import {
  STUDIO_NAV_PATHS,
  PERSONAL_STUDIO_NAV_PATHS,
  MANAGEMENT_SURFACE_PATHS,
  appRouteFromPath,
  personalStudioPathForNav,
  studioNavFromPath,
  studioPathForNav,
  surfacePath,
} from "../src/studioNavigation.js";
import { readFile } from "node:fs/promises";

const appSource = await readFile(new URL("../src/App.jsx", import.meta.url), "utf8");

test("studio navigation exposes stable deep links for every customer route", () => {
  assert.deepEqual(STUDIO_NAV_PATHS, {
    shots: "/",
    create: "/creation",
    media: "/media",
    artworks: "/artworks",
    publish: "/publishing",
    history: "/history",
    settings: "/settings",
  });
  for (const [nav, path] of Object.entries(STUDIO_NAV_PATHS)) {
    assert.equal(studioNavFromPath(path), nav);
    assert.equal(studioNavFromPath(`${path === "/" ? "" : path}/`), nav);
    assert.equal(studioPathForNav(nav), path);
  }
});

test("management surfaces use canonical URL namespaces", () => {
  assert.deepEqual(MANAGEMENT_SURFACE_PATHS, {
    company: "/company",
    platform: "/platform",
  });
  assert.deepEqual(appRouteFromPath("/company"), {
    recognized: true,
    surface: "company",
    nav: "shots",
    canonicalPath: "/company",
  });
  assert.deepEqual(appRouteFromPath("/platform/"), {
    recognized: true,
    surface: "platform",
    nav: "shots",
    canonicalPath: "/platform",
  });
  assert.equal(surfacePath("company", "creation"), "/company");
  assert.equal(surfacePath("studio", "create"), "/creation");
});

test("personal Studio reuses all seven routes under an explicit context path", () => {
  assert.equal(PERSONAL_STUDIO_NAV_PATHS.shots, "/personal");
  assert.equal(personalStudioPathForNav("create"), "/personal/creation");
  assert.equal(personalStudioPathForNav("publish"), "/personal/publishing");
  assert.deepEqual(appRouteFromPath("/personal/artworks/"), {
    recognized: true,
    surface: "personal",
    nav: "artworks",
    canonicalPath: "/personal/artworks",
  });
  assert.equal(surfacePath("personal", "settings"), "/personal/settings");
});

test("unknown paths fail back to the community home without inventing a route", () => {
  assert.equal(studioNavFromPath("/not-a-studio-route"), "shots");
  assert.equal(studioPathForNav("not-a-nav"), "/");
  assert.deepEqual(appRouteFromPath("/not-a-studio-route"), {
    recognized: false,
    surface: "studio",
    nav: "shots",
    canonicalPath: "/",
  });
});

test("the app binds URL history to studio navigation state", () => {
  assert.match(appSource, /appRouteFromPath\(globalThis\.location\?\.pathname\)/);
  assert.match(appSource, /globalThis\.history\[replace \? "replaceState" : "pushState"\]/);
  assert.match(appSource, /addEventListener\?\.\("popstate", handlePopState\)/);
  assert.match(appSource, /onClick=\{\(\) => navigateStudio\(item\.id\)\}/);
  assert.match(appSource, /globalThis\.history\?\.pushState\?\.\(\{\}, "", nextPath\)/);
  assert.match(appSource, /globalThis\.history\?\.replaceState\?\.\(\{\}, "", canonicalPath\)/);
});
