import { execFileSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptPath = fileURLToPath(import.meta.url);
const workspace = resolve(dirname(scriptPath), "..");

const forbiddenPrefixes = Object.freeze([
  "deploy/secrets/",
  "infra/postgres/local/",
]);

function portable(path) {
  return String(path).replaceAll("\\", "/").replace(/^\.\//, "");
}

export function forbiddenRelaySecretPaths(paths) {
  return [...new Set(paths
    .map(portable)
    .filter((path) => forbiddenPrefixes.some((prefix) => path.startsWith(prefix))))]
    .sort((left, right) => left.localeCompare(right, "en"));
}

export function assertNoTrackedRelaySecretPaths(paths) {
  const forbidden = forbiddenRelaySecretPaths(paths);
  if (forbidden.length !== 0) {
    throw new Error(`release source contains forbidden secret path(s): ${forbidden.join(", ")}`);
  }
}

export function trackedWorkspacePaths() {
  let output;
  try {
    output = execFileSync("git", ["ls-files", "-z"], {
      cwd: workspace,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
    });
  } catch {
    throw new Error("release secret-path gate requires a Git worktree");
  }
  return output.split("\0").filter(Boolean);
}

if (process.argv[1] && resolve(process.argv[1]) === scriptPath) {
  assertNoTrackedRelaySecretPaths(trackedWorkspacePaths());
  process.stdout.write("relay-secret-path-gate=PASS\n");
}
