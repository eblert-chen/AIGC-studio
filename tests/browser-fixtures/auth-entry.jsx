import React from "react";
import { createRoot } from "react-dom/client";
import { AuthGateway } from "../../src/auth/AuthGateway.jsx";
import "../../src/design-system/index.css";

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <AuthGateway demoMode={false}>
      <main>Authenticated application</main>
    </AuthGateway>
  </React.StrictMode>,
);
