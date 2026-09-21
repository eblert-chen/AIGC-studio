import { useCallback, useEffect, useRef, useState } from "react";

import { ADVANCED_WORKBENCHES, readCreationWorkbench } from "./useCreationWorkspaceSession.js";
import { appRouteFromPath, surfacePath } from "../studioNavigation.js";

const CREATION_ENTRY_ROUTES = new Set(["shots", "create"]);

function readInitialRoute() {
  return appRouteFromPath(globalThis.location?.pathname);
}

/**
 * Owns the Studio URL, surface and workbench history contract.
 *
 * Identity and permission code may still correct the selected surface through
 * the returned setters, but browser-history parsing and mutation stay in this
 * single routing boundary instead of being repeated in the App shell.
 */
export function useStudioRouteState() {
  const initialRouteRef = useRef(null);
  if (!initialRouteRef.current) initialRouteRef.current = readInitialRoute();

  const [activeNav, setActiveNav] = useState(initialRouteRef.current.nav);
  const [creationWorkbench, setCreationWorkbench] = useState(readCreationWorkbench);
  const [surface, setSurface] = useState(initialRouteRef.current.surface);

  const navigateStudio = useCallback((nextNav, { replace = false } = {}) => {
    const nextPath = surfacePath(surface === "personal" ? "personal" : "studio", nextNav);
    setActiveNav(nextNav);
    if (CREATION_ENTRY_ROUTES.has(nextNav)) setCreationWorkbench("entry");
    if (!globalThis.history || !globalThis.location) return;
    if (globalThis.location.pathname === nextPath && !globalThis.location.search) return;
    globalThis.history[replace ? "replaceState" : "pushState"]({}, "", nextPath);
  }, [surface]);

  const creationWorkbenchPath = useCallback((nextWorkbench) => {
    const nextPath = surfacePath(surface === "personal" ? "personal" : "studio", "create");
    return nextWorkbench === "entry"
      ? nextPath
      : `${nextPath}?workbench=${encodeURIComponent(nextWorkbench)}`;
  }, [surface]);

  const navigateCreationWorkbench = useCallback((nextWorkbench) => {
    if (!["entry", ...ADVANCED_WORKBENCHES].includes(nextWorkbench)) return;
    const nextPath = creationWorkbenchPath(nextWorkbench);
    setActiveNav("create");
    setCreationWorkbench(nextWorkbench);
    if (!globalThis.history || !globalThis.location) return;
    if (`${globalThis.location.pathname}${globalThis.location.search}` === nextPath) return;
    globalThis.history.pushState(
      { ...(globalThis.history.state || {}), creationWorkbenchEntry: true },
      "",
      nextPath,
    );
    if (typeof globalThis.PopStateEvent === "function") {
      globalThis.dispatchEvent?.(new globalThis.PopStateEvent("popstate"));
    }
  }, [creationWorkbenchPath]);

  useEffect(() => {
    const handlePopState = () => {
      const route = readInitialRoute();
      setActiveNav(route.nav);
      setCreationWorkbench(readCreationWorkbench());
      setSurface(route.surface);
      if (!route.recognized) {
        globalThis.history?.replaceState?.({}, "", route.canonicalPath);
      }
    };
    globalThis.addEventListener?.("popstate", handlePopState);
    return () => globalThis.removeEventListener?.("popstate", handlePopState);
  }, []);

  useEffect(() => {
    const initialRoute = initialRouteRef.current;
    if (initialRoute.recognized) return;
    globalThis.history?.replaceState?.({}, "", initialRoute.canonicalPath);
  }, []);

  return {
    activeNav,
    setActiveNav,
    creationWorkbench,
    setCreationWorkbench,
    surface,
    setSurface,
    navigateStudio,
    navigateCreationWorkbench,
  };
}
