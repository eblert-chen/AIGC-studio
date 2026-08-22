export const STUDIO_NAV_PATHS = Object.freeze({
  shots: "/",
  create: "/creation",
  media: "/media",
  artworks: "/artworks",
  publish: "/publishing",
  history: "/history",
  settings: "/settings",
});

export const PERSONAL_STUDIO_NAV_PATHS = Object.freeze(
  Object.fromEntries(
    Object.entries(STUDIO_NAV_PATHS).map(([nav, path]) => [
      nav,
      path === "/" ? "/personal" : `/personal${path}`,
    ]),
  ),
);

export const MANAGEMENT_SURFACE_PATHS = Object.freeze({
  company: "/company",
  platform: "/platform",
});

const STUDIO_PATH_NAV = new Map(
  Object.entries(STUDIO_NAV_PATHS).map(([nav, path]) => [path, nav]),
);

const PERSONAL_STUDIO_PATH_NAV = new Map(
  Object.entries(PERSONAL_STUDIO_NAV_PATHS).map(([nav, path]) => [path, nav]),
);

function normalizePathname(pathname) {
  const value = String(pathname || "/").split(/[?#]/, 1)[0] || "/";
  if (value === "/") return value;
  return value.replace(/\/+$/, "") || "/";
}

export function studioNavFromPath(pathname) {
  return STUDIO_PATH_NAV.get(normalizePathname(pathname)) || "shots";
}

export function studioPathForNav(nav) {
  return STUDIO_NAV_PATHS[nav] || STUDIO_NAV_PATHS.shots;
}

export function personalStudioPathForNav(nav) {
  return PERSONAL_STUDIO_NAV_PATHS[nav] || PERSONAL_STUDIO_NAV_PATHS.shots;
}

export function appRouteFromPath(pathname) {
  const path = normalizePathname(pathname);
  const managementSurface = Object.entries(MANAGEMENT_SURFACE_PATHS)
    .find(([, candidatePath]) => candidatePath === path)?.[0];
  if (managementSurface) {
    return {
      recognized: true,
      surface: managementSurface,
      nav: "shots",
      canonicalPath: MANAGEMENT_SURFACE_PATHS[managementSurface],
    };
  }
  const personalStudioNav = PERSONAL_STUDIO_PATH_NAV.get(path);
  if (personalStudioNav) {
    return {
      recognized: true,
      surface: "personal",
      nav: personalStudioNav,
      canonicalPath: PERSONAL_STUDIO_NAV_PATHS[personalStudioNav],
    };
  }
  const studioNav = STUDIO_PATH_NAV.get(path);
  if (studioNav) {
    return {
      recognized: true,
      surface: "studio",
      nav: studioNav,
      canonicalPath: STUDIO_NAV_PATHS[studioNav],
    };
  }
  return {
    recognized: false,
    surface: "studio",
    nav: "shots",
    canonicalPath: STUDIO_NAV_PATHS.shots,
  };
}

export function surfacePath(surface, studioNav = "shots") {
  if (surface === "personal") return personalStudioPathForNav(studioNav);
  return MANAGEMENT_SURFACE_PATHS[surface] || studioPathForNav(studioNav);
}
