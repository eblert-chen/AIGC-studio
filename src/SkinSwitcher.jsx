import { useCallback, useEffect, useState } from "react";
import { Palette } from "@phosphor-icons/react";

export const SKIN_STORAGE_KEY = "yingchuang-skin";

export const SKIN_OPTIONS = Object.freeze([
  { id: "paper", label: "纯白" },
  { id: "mist", label: "雾灰" },
  { id: "warm", label: "暖米" },
]);

const SKIN_IDS = new Set(SKIN_OPTIONS.map(({ id }) => id));

export function normalizeSkin(value) {
  return SKIN_IDS.has(value) ? value : "paper";
}

function readStoredSkin() {
  try {
    return normalizeSkin(globalThis.localStorage?.getItem(SKIN_STORAGE_KEY));
  } catch {
    return "paper";
  }
}

export function useSkinPreference() {
  const [skin, setSkinState] = useState(readStoredSkin);

  const setSkin = useCallback((nextSkin) => {
    setSkinState(normalizeSkin(nextSkin));
  }, []);

  useEffect(() => {
    try {
      globalThis.localStorage?.setItem(SKIN_STORAGE_KEY, skin);
    } catch {
      // The selected skin still applies in memory when storage is unavailable.
    }
  }, [skin]);

  return [skin, setSkin];
}

export function SkinSwitcher({ value, onChange, className = "" }) {
  const skin = normalizeSkin(value);

  return (
    <label className={`skin-switcher ${className}`.trim()}>
      <Palette size={16} aria-hidden="true" />
      <span className="visually-hidden">界面皮肤</span>
      <select
        aria-label="界面皮肤"
        value={skin}
        onChange={(event) => onChange?.(normalizeSkin(event.target.value))}
      >
        {SKIN_OPTIONS.map((option) => (
          <option key={option.id} value={option.id}>{option.label}</option>
        ))}
      </select>
    </label>
  );
}
