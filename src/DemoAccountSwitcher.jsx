import { UserSwitch } from "@phosphor-icons/react";
import { DEMO_PERSONA_OPTIONS } from "./demoIdentitySurfaces.js";

export function DemoAccountSwitcher({ value, onChange }) {
  return (
    <label className="demo-account-switcher">
      <UserSwitch size={16} aria-hidden="true" />
      <span>演示账号</span>
      <strong className="demo-account-origin" aria-hidden="true">演示</strong>
      <select
        aria-label="切换演示账号"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        {DEMO_PERSONA_OPTIONS.map((option) => (
          <option key={option.id} value={option.id}>{option.label}</option>
        ))}
      </select>
    </label>
  );
}
