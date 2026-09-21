import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import {
  CaretLeft,
  CaretRight,
  SlidersHorizontal,
  WarningCircle,
} from "@phosphor-icons/react";
import { OperationsWorkspaceActions } from "../../src/admin/OperationsWorkspaceActions.jsx";
import { BrandLogo } from "../../src/BrandLogo.jsx";
import { DemoAccountSwitcher } from "../../src/DemoAccountSwitcher.jsx";
import { SkinSwitcher } from "../../src/SkinSwitcher.jsx";
import { surfacePath } from "../../src/studioNavigation.js";
import "../../src/design-system/index.css";

const params = new URLSearchParams(globalThis.location.search);
const fixtureRole = params.get("role") || (params.get("owner") === "true" ? "owner" : "platform_admin");
const isPlatformOwner = fixtureRole === "owner";

function OperationsWorkspaceEntryFixture() {
  const [selectedSurface, setSelectedSurface] = useState("");
  const [contextSwitchCount, setContextSwitchCount] = useState(0);
  const [skin, setSkin] = useState("paper");

  const openPersonalCreation = () => {
    setSelectedSurface("personal");
    setContextSwitchCount((count) => count + 1);
    globalThis.history.pushState({}, "", surfacePath("personal", "create"));
  };

  const returnToPlatform = () => {
    setSelectedSurface("platform");
    globalThis.history.pushState({}, "", surfacePath("platform"));
  };

  return (
    <div className="ops-console" data-theme={skin}>
      <output id="selected-surface" className="visually-hidden" aria-live="polite">
        {selectedSurface}
      </output>
      <output id="selected-skin" className="visually-hidden" aria-live="polite">
        {skin}
      </output>
      <output id="fixture-role" className="visually-hidden">
        {fixtureRole}
      </output>
      <output id="context-switch-count" className="visually-hidden" aria-live="polite">
        {contextSwitchCount}
      </output>
      {selectedSurface === "personal" ? (
        <main aria-labelledby="fixture-title" data-product-context="personal">
          <button
            type="button"
            onClick={returnToPlatform}
            aria-label="返回 Platform"
            style={{ minWidth: 44, minHeight: 44 }}
          >
            返回 Platform
          </button>
          <h1 id="fixture-title">本人个人创作空间</h1>
          <p>当前登录主体保持不变：{fixtureRole}</p>
        </main>
      ) : (
        <>
          <header className="ops-topbar">
        <div className="ops-brand" aria-label="旭天 AI studio · 平台运营">
          <BrandLogo variant="responsive" mobileBreakpoint={820} />
          <span className="ops-surface-name" aria-hidden="true">平台运营</span>
        </div>
        <div className="ops-module-navigation">
          <button className="ops-nav-scroll-button is-previous" data-icon-only="true" type="button" disabled aria-label="查看前面的平台模块"><CaretLeft size={16} /></button>
          <nav aria-label="平台管理员模块">
            <button type="button" className="is-active" aria-current="page">任务运营</button>
            <button type="button">经营洞察</button>
          </nav>
          <button className="ops-nav-scroll-button is-next" data-icon-only="true" type="button" disabled aria-label="查看更多平台模块"><CaretRight size={16} /></button>
        </div>
        <div className="ops-admin-tools">
          <OperationsWorkspaceActions
            canOpenPersonalCreation={isPlatformOwner}
            onOpenPersonalCreation={openPersonalCreation}
          />
          <SkinSwitcher value={skin} onChange={setSkin} />
          <button type="button" className="ops-basic-config-button" data-icon-only="true" aria-label="打开基础配置"><SlidersHorizontal size={16} /><span className="ops-basic-config-label">基础配置</span></button>
          <button type="button" className="ops-help-button" aria-label="打开运营控制台帮助"><WarningCircle size={16} /><span className="ops-help-label">帮助</span></button>
          <span className="ops-top-divider" />
          <DemoAccountSwitcher value="platform_admin" onChange={() => {}} />
        </div>
          </header>
          <main aria-labelledby="fixture-title" data-product-context="platform">
            <h1 id="fixture-title">平台运营入口回归</h1>
          </main>
        </>
      )}
    </div>
  );
}

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <OperationsWorkspaceEntryFixture />
  </React.StrictMode>,
);
