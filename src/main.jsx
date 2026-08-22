import React from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App.jsx";
import { AuthGateway } from "./auth/AuthGateway.jsx";
import { isExplicitDevelopmentDemo } from "./runtimeMode.js";
import "./design-system/index.css";

const DEMO_MODE = import.meta.env.PROD
  ? false
  : isExplicitDevelopmentDemo(import.meta.env);

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <AuthGateway demoMode={DEMO_MODE}>
      <App />
    </AuthGateway>
  </React.StrictMode>,
);
