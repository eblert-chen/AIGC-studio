import React, { lazy, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { AppErrorBoundary } from "./AppErrorBoundary.jsx";
import { AuthGateway } from "./auth/AuthGateway.jsx";
import { RouteLoadingFallback } from "./RouteLoadingFallback.jsx";
import { isExplicitDevelopmentDemo } from "./runtimeMode.js";
import "./design-system/index.css";

const DEMO_MODE = import.meta.env.PROD
  ? false
  : isExplicitDevelopmentDemo(import.meta.env);

const App = lazy(async () => {
  const module = await import("./App.jsx");
  return { default: module.App };
});

const rootElement = document.getElementById("root");
const reactRoot = import.meta.hot?.data.reactRoot ?? createRoot(rootElement);

if (import.meta.hot) {
  import.meta.hot.data.reactRoot = reactRoot;
}

reactRoot.render(
  <React.StrictMode>
    <AppErrorBoundary>
      <AuthGateway demoMode={DEMO_MODE}>
        <Suspense fallback={<RouteLoadingFallback label="正在打开创作工作区" />}>
          <App />
        </Suspense>
      </AuthGateway>
    </AppErrorBoundary>
  </React.StrictMode>,
);
