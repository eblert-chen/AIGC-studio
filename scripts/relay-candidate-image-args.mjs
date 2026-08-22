#!/usr/bin/env node

import { pathToFileURL } from "node:url";

import {
  candidateImageBuildLabelArgs,
  relaySourceSnapshot,
} from "./relay-fault-source-snapshot.mjs";
import { CANDIDATE_UPSTREAM_GIT_REVISION } from "./relay-migration-acceptance.mjs";

export async function candidateImageArgs(environment = process.env) {
  const snapshot = await relaySourceSnapshot();
  return candidateImageBuildLabelArgs(
    snapshot,
    CANDIDATE_UPSTREAM_GIT_REVISION,
    environment.NEW_API_RELAY_ROUTE_ACCEPTANCE_KEYS_SHA256,
  );
}

async function main() {
  for (const argument of await candidateImageArgs()) {
    if (/\r|\n|\0/.test(argument)) throw new Error("candidate image build argument is unsafe");
    process.stdout.write(`${argument}\n`);
  }
}

if (import.meta.url === pathToFileURL(process.argv[1] || "").href) {
  main().catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exitCode = 1;
  });
}
