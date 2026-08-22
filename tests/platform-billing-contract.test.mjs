import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const app = readFileSync(new URL("../src/App.jsx", import.meta.url), "utf8");
const management = readFileSync(
  new URL("../src/ManagementConsole.jsx", import.meta.url),
  "utf8",
);

test("LIVE generation pins the server model quote revision", () => {
  assert.match(
    app,
    /quoteRevision:\s*[\s\S]*?typeof source\.quote_revision === "string"/,
  );
  assert.match(app, /version:\s*5/);
  assert.match(app, /expectedQuoteRevision:\s*pendingCreate\.quoteRevision/);
  assert.match(app, /当前模型缺少有效的报价版本/);
});

test("legacy dashboard labels incomplete cost profit as known, not final", () => {
  assert.match(management, /known_gross_profit_cents/);
  assert.match(
    management,
    /reconciliationComplete \? "平台毛利" : "已知毛利"/,
  );
  assert.match(
    management,
    /reconciliationComplete \? "毛利率" : "已知毛利率"/,
  );
  assert.match(management, /不能视为最终经营结果/);
  assert.doesNotMatch(
    management,
    /label: "平台毛利", value: money\(dashboard\.gross_profit_cents\)/,
  );
});
