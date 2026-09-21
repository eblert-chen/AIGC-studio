import { installArkPlatformFixture } from "./ark-video-platform.mjs";
import { minimaxH3DiscoveryResponses } from "../fixtures/minimax-h3-discovery.mjs";

// Reuse the same synthetic company/task transport, including exact unknown
// submission replay. No provider-specific composer or real grants are created.
export function installMiniMaxH3PlatformFixture(page, options = {}) {
  return installArkPlatformFixture(page, {
    models: minimaxH3DiscoveryResponses(),
    fixtureLabel: "MiniMax H3",
    ...options,
  });
}
