import { useEffect, useRef, useState } from "react";

import {
  createPlatformClient,
  PlatformApiError,
} from "../api/platformClient.js";
import {
  normalizeSessionSurfaces,
  personalIdentityFromSession,
  preferredCompanyId,
  sessionAccountKind,
} from "../personalWorkspace.js";

function accountIdentityError(accountKind, catalog) {
  if (accountKind === "company_unavailable") {
    return "当前账号所属企业已暂停或不可用，不能回退到个人创作空间。请联系平台管理员。";
  }
  if (accountKind === "platform") {
    return "无法读取平台管理员身份，系统不会回退到企业或个人工作区。请稍后重试。";
  }
  if (accountKind === "company") {
    return "无法读取企业成员身份，系统不会回退到个人工作区。请稍后重试。";
  }
  if (accountKind === "personal") {
    return "无法读取个人用户身份，请稍后重试。";
  }
  return catalog
    ? "当前账号没有可用的个人、企业或平台工作区。"
    : "无法确认当前账号属于企业成员还是平台管理员，请稍后重试。";
}

export async function discoverStudioIdentity({
  liveClient,
  runtimePlatformConfig,
  activeCompanyId,
  signal,
  clientFactory = createPlatformClient,
}) {
  const catalog = normalizeSessionSurfaces(
    await liveClient.getSessionSurfaces({ signal }),
  );
  const accountKind = sessionAccountKind(catalog);
  const selectedCompanyId = accountKind === "company"
    ? preferredCompanyId(
        catalog,
        activeCompanyId || runtimePlatformConfig.companyId,
      )
    : "";
  const selectedCompanyClient = accountKind === "company" && selectedCompanyId
    ? clientFactory({
        ...runtimePlatformConfig,
        companyId: selectedCompanyId,
      })
    : null;
  const [companyResult, adminResult, personalResult] = await Promise.allSettled([
    accountKind === "company" && selectedCompanyClient
      ? selectedCompanyClient.getCompanyMe({ signal })
      : Promise.resolve(null),
    accountKind === "platform"
      ? liveClient.getPlatformAdminMe({ signal })
      : Promise.resolve(null),
    accountKind === "personal"
      ? liveClient.getPersonalMe({ signal })
      : Promise.resolve(null),
  ]);

  const errors = [companyResult, adminResult, personalResult]
    .filter((result) => result.status === "rejected")
    .map((result) => result.reason);
  const authenticationError = errors.find((error) => (
    error instanceof PlatformApiError &&
    (error.status === 401 || error.code === "AUTH_NOT_CONFIGURED")
  ));
  if (authenticationError) throw authenticationError;

  const companyIdentity = accountKind === "company" && companyResult.status === "fulfilled"
    ? companyResult.value
    : null;
  const platformIdentity = accountKind === "platform"
    && adminResult.status === "fulfilled"
    && adminResult.value
      ? {
          ...adminResult.value,
          company_id: null,
          permission_codes: [],
          roles: [],
          is_platform_admin: true,
        }
      : null;
  const personalIdentity = accountKind === "personal"
    && personalResult.status === "fulfilled"
    && personalResult.value
      ? {
          ...personalIdentityFromSession(catalog),
          ...personalResult.value,
          company_id: null,
          workspace_id: catalog.personal?.workspace_id,
          workspace_kind: "personal",
          workspace_label: catalog.personal?.label || "个人空间",
          personal_capabilities: catalog.personal?.capabilities || {},
          permission_codes: [],
          roles: [],
          is_personal: true,
          is_platform_admin: false,
        }
      : null;

  return {
    accountKind,
    catalog,
    selectedCompanyId,
    companyIdentity,
    platformIdentity,
    personalIdentity,
    identityError: companyIdentity || platformIdentity || personalIdentity
      ? ""
      : accountIdentityError(accountKind, catalog),
  };
}

export function useStudioIdentity({
  liveMode,
  demoMode,
  liveClient,
  runtimePlatformConfig,
  activeCompanyId,
  setActiveCompanyId,
  authExpired,
  onAuthenticationError,
}) {
  const [companyIdentity, setCompanyIdentity] = useState(null);
  const [personalIdentity, setPersonalIdentity] = useState(null);
  const [platformIdentity, setPlatformIdentity] = useState(null);
  const [sessionSurfaceCatalog, setSessionSurfaceCatalog] = useState(null);
  const [identityResolved, setIdentityResolved] = useState(demoMode);
  const [identityError, setIdentityError] = useState("");
  const authenticationErrorRef = useRef(onAuthenticationError);
  authenticationErrorRef.current = onAuthenticationError;

  const beginCompanySwitch = () => {
    setIdentityResolved(false);
    setIdentityError("");
    setCompanyIdentity(null);
  };

  useEffect(() => {
    if (!liveMode || authExpired) return undefined;
    const controller = new AbortController();
    setIdentityResolved(false);
    setIdentityError("");

    discoverStudioIdentity({
      liveClient,
      runtimePlatformConfig,
      activeCompanyId,
      signal: controller.signal,
    })
      .then((result) => {
        if (controller.signal.aborted) return;
        if (result.selectedCompanyId && result.selectedCompanyId !== activeCompanyId) {
          setActiveCompanyId(result.selectedCompanyId);
        }
        if (result.selectedCompanyId) {
          try {
            globalThis.sessionStorage?.setItem(
              "ai-video.company-id",
              result.selectedCompanyId,
            );
          } catch {
            // The selected server-authorized company still applies in memory.
          }
        }
        setSessionSurfaceCatalog(result.catalog);
        setCompanyIdentity(result.companyIdentity);
        setPlatformIdentity(result.platformIdentity);
        setPersonalIdentity(result.personalIdentity);
        setIdentityError(result.identityError);
      })
      .catch((error) => {
        if (controller.signal.aborted) return;
        if (!authenticationErrorRef.current?.(error)) {
          setIdentityError("无法读取账号工作区，请稍后重试。");
        }
        setSessionSurfaceCatalog(null);
        setCompanyIdentity(null);
        setPlatformIdentity(null);
        setPersonalIdentity(null);
      })
      .finally(() => {
        if (!controller.signal.aborted) setIdentityResolved(true);
      });

    return () => controller.abort();
  }, [activeCompanyId, authExpired, liveClient, liveMode, runtimePlatformConfig, setActiveCompanyId]);

  return {
    companyIdentity,
    personalIdentity,
    platformIdentity,
    sessionSurfaceCatalog,
    identityResolved,
    identityError,
    beginCompanySwitch,
  };
}
