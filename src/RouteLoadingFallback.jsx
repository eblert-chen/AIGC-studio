import { useEffect, useState } from "react";

export function RouteLoadingFallback({ label = "正在打开工作区", compact = false }) {
  const [takingLonger, setTakingLonger] = useState(false);

  useEffect(() => {
    const timer = globalThis.setTimeout?.(() => setTakingLonger(true), 8_000);
    return () => globalThis.clearTimeout?.(timer);
  }, []);

  return (
    <div
      className={`route-loading-fallback ${compact ? "is-compact" : ""}`}
      role="status"
      aria-live="polite"
      aria-busy="true"
    >
      <span className="route-loading-indicator" aria-hidden="true" />
      <strong>{label}</strong>
      {takingLonger ? (
        <div className="route-loading-recovery">
          <span>加载时间较长，可能是网络暂时不稳定。</span>
          <button type="button" onClick={() => globalThis.location?.reload?.()}>
            重新加载页面
          </button>
        </div>
      ) : null}
    </div>
  );
}
