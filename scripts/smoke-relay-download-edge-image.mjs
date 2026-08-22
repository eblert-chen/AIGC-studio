import { spawnSync } from "node:child_process";

const image = process.argv[2] ?? process.env.RELAY_DOWNLOAD_EDGE_SMOKE_IMAGE;
if (!image || !/^[a-zA-Z0-9][a-zA-Z0-9._/:@-]{0,255}$/.test(image)) {
  console.error(
    "usage: node scripts/smoke-relay-download-edge-image.mjs <candidate-image>",
  );
  process.exit(2);
}

function runDocker(arguments_, expectedStatus = 0) {
  const result = spawnSync("docker", arguments_, {
    encoding: "utf8",
    windowsHide: true,
  });
  if (result.error) {
    throw result.error;
  }
  if (result.status !== expectedStatus) {
    throw new Error(
      `docker ${arguments_[0]} returned ${result.status}; expected ${expectedStatus}\n${result.stderr}`,
    );
  }
  return result;
}

const inspection = runDocker([
  "image",
  "inspect",
  image,
  "--format",
  "{{.Id}}",
]).stdout.trim();

runDocker([
  "run",
  "--rm",
  "--entrypoint",
  "/bin/sh",
  image,
  "-ec",
  "test -x /new-api && test -x /relay-download-edge",
]);

const failClosed = runDocker(
  ["run", "--rm", "--entrypoint", "/relay-download-edge", image],
  1,
);
if (!`${failClosed.stdout}\n${failClosed.stderr}`.includes("relay download edge stopped")) {
  throw new Error("download-edge binary did not execute its fail-closed startup path");
}

console.log(
  JSON.stringify({
    image,
    image_id: inspection,
    new_api_executable: true,
    relay_download_edge_executable: true,
    missing_configuration_failed_closed: true,
  }),
);
