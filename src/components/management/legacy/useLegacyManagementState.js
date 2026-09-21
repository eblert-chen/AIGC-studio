import { useEffect, useRef, useState } from "react";

/**
 * Owns the mutable state shared by the company management console and the
 * permission-filtered platform configuration surface. Network orchestration
 * remains in the container; this hook centralizes state lifetime and request
 * cancellation so route views do not acquire their own competing copies.
 */
export function useManagementConsoleState({
  createInitialData,
  initialSection,
  initiallyLoading,
  mode,
  initialCompanyReportFilters,
  initialAdminReportFilters,
}) {
  const [section, setSection] = useState(() => initialSection);
  const [data, setData] = useState(createInitialData);
  const [loading, setLoading] = useState(initiallyLoading);
  const [busy, setBusy] = useState(false);
  const [memberAccessLoading, setMemberAccessLoading] = useState(false);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [drawer, setDrawerState] = useState(null);
  const [drawerError, setDrawerError] = useState("");
  const [entitlementBusyKey, setEntitlementBusyKey] = useState("");
  const [paginationBusyKey, setPaginationBusyKey] = useState("");
  const [accessInvalidated, setAccessInvalidated] = useState(false);
  const [ownerInvitationLinks, setOwnerInvitationLinks] = useState({});
  const [compactManagementChrome, setCompactManagementChrome] = useState(
    () => globalThis.matchMedia?.("(max-width: 720px)")?.matches ?? false,
  );
  const [mobileDemoPersonaHost, setMobileDemoPersonaHost] = useState(null);
  const [desktopDemoPersonaHost, setDesktopDemoPersonaHost] = useState(null);
  const [reportFilters, setReportFilters] = useState(initialCompanyReportFilters);
  const [appliedReportFilters, setAppliedReportFilters] = useState(initialCompanyReportFilters);
  const [adminReportFilters, setAdminReportFilters] = useState(initialAdminReportFilters);
  const [appliedAdminReportFilters, setAppliedAdminReportFilters] = useState(initialAdminReportFilters);

  const memberAccessRequestRef = useRef(0);
  const companyControlRequestRef = useRef(0);
  const personalPointGrantRequestRef = useRef(0);
  const loadRequestGenerationRef = useRef(0);
  const loadAbortControllerRef = useRef(null);
  const controlMainRef = useRef(null);
  const controlNavRef = useRef(null);
  const activeNavItemRef = useRef(null);

  useEffect(() => {
    const query = globalThis.matchMedia?.("(max-width: 720px)");
    if (!query) return undefined;
    const synchronize = (event) => setCompactManagementChrome(event.matches);
    setCompactManagementChrome(query.matches);
    if (query.addEventListener) query.addEventListener("change", synchronize);
    else query.addListener?.(synchronize);
    return () => {
      if (query.removeEventListener) query.removeEventListener("change", synchronize);
      else query.removeListener?.(synchronize);
    };
  }, []);

  useEffect(() => {
    memberAccessRequestRef.current += 1;
    companyControlRequestRef.current += 1;
    personalPointGrantRequestRef.current += 1;
    setMemberAccessLoading(false);
    setEntitlementBusyKey("");
    setPaginationBusyKey("");
  }, [mode, section]);

  useEffect(() => () => {
    memberAccessRequestRef.current += 1;
    companyControlRequestRef.current += 1;
    personalPointGrantRequestRef.current += 1;
    loadRequestGenerationRef.current += 1;
    loadAbortControllerRef.current?.abort();
    loadAbortControllerRef.current = null;
  }, []);

  return {
    section,
    setSection,
    data,
    setData,
    loading,
    setLoading,
    busy,
    setBusy,
    memberAccessLoading,
    setMemberAccessLoading,
    error,
    setError,
    toast,
    setToast,
    drawer,
    setDrawerState,
    drawerError,
    setDrawerError,
    entitlementBusyKey,
    setEntitlementBusyKey,
    paginationBusyKey,
    setPaginationBusyKey,
    accessInvalidated,
    setAccessInvalidated,
    ownerInvitationLinks,
    setOwnerInvitationLinks,
    compactManagementChrome,
    mobileDemoPersonaHost,
    setMobileDemoPersonaHost,
    desktopDemoPersonaHost,
    setDesktopDemoPersonaHost,
    reportFilters,
    setReportFilters,
    appliedReportFilters,
    setAppliedReportFilters,
    adminReportFilters,
    setAdminReportFilters,
    appliedAdminReportFilters,
    setAppliedAdminReportFilters,
    memberAccessRequestRef,
    companyControlRequestRef,
    personalPointGrantRequestRef,
    loadRequestGenerationRef,
    loadAbortControllerRef,
    controlMainRef,
    controlNavRef,
    activeNavItemRef,
  };
}
