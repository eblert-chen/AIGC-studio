import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { PlatformApiError } from "../src/api/platformClient.js";
import { deriveArtworksFromTasks, normalizePage } from "../src/taskArtifacts.js";

const source = await readFile(new URL("../src/app/useStudioCollections.js", import.meta.url), "utf8");
// Execute the production hook bodies, replacing only React's scheduling primitives
// and timers. The real React/browser regression remains a separate integration check.
const loadHooks = new Function(
  "useCallback", "useEffect", "useRef", "useState", "PlatformApiError",
  "deriveArtworksFromTasks", "normalizePage", "globalThis",
  source.replace(/^import\s[\s\S]*?\sfrom\s"[^"]+";\s*/gm, "")
    .replace(/^export (?=(?:async )?function)/gm, "")
    + "\nreturn { useStudioTaskCollections, useStudioArtworkCollection };",
);

const variants = [
  {
    name: "task history", hook: "useStudioTaskCollections", nav: "create",
    reset: "resetTaskCollections", rows: "historyTasks", total: "creationTotal",
    loading: "historyLoading", error: "historyError", permission: "canReadStudioTasks",
    filterSetter: "setCreationStatus", filter: "status", filterValue: "succeeded", pollMs: 10_000,
  },
  {
    name: "artworks", hook: "useStudioArtworkCollection", nav: "artworks",
    reset: "resetArtworkCollection", rows: "artworks", total: "artworkTotal",
    loading: "artworksLoading", error: "artworksError", permission: "canReadStudioArtworks",
    filterSetter: "setArtworkMediaFilter", filter: "media_type", filterValue: "video", pollMs: 15_000,
  },
];

function queuedClient() {
  const requests = [];
  const read = (filters, { signal } = {}) => new Promise((resolve, reject) => {
    requests.push({ filters, signal, resolve, reject });
  });
  return {
    requests,
    client: {
      listTaskHistory: read,
      listArtworks: read,
      listTasks: () => { throw new Error("No compatibility fallback expected"); },
    },
  };
}

function resolvePage(request, ids) {
  request.resolve({
    page: request.filters.page,
    page_size: request.filters.page_size,
    total: ids.length,
    items: ids.map((id) => ({ id })),
  });
}

function collectionHarness(variant, client, { resetOnWorkspace = false, ...overrides } = {}) {
  const slots = [];
  const timers = new Map();
  let timerId = 0;
  let cursor = 0;
  let effects = [];
  let dirty = true;
  let mounted = true;
  let current;
  let props = {
    activeNav: variant.nav,
    liveMode: true,
    authExpired: false,
    hasStudioSession: true,
    [variant.permission]: true,
    effectiveSurface: "studio",
    isPersonalWorkspace: false,
    studioClient: client,
    studioWorkspaceKey: "company:first:owner",
    collectionPageSize: 24,
    onAuthenticationError: () => false,
    formatError: (error) => error.message,
    ...overrides,
  };
  const sameDependencies = (before, after) => (
    Array.isArray(before) && Array.isArray(after)
    && before.length === after.length && before.every((value, index) => Object.is(value, after[index]))
  );
  const slot = (kind, initialize) => {
    const index = cursor++;
    if (!slots[index]) slots[index] = { kind, ...initialize() };
    assert.equal(slots[index].kind, kind, "Hook order must remain stable");
    return slots[index];
  };
  const useState = (initial) => {
    const state = slot("state", () => ({ value: typeof initial === "function" ? initial() : initial }));
    state.set ??= (next) => {
      const value = typeof next === "function" ? next(state.value) : next;
      if (mounted && !Object.is(value, state.value)) {
        state.value = value;
        dirty = true;
      }
    };
    return [state.value, state.set];
  };
  const useRef = (initial) => slot("ref", () => ({ value: { current: initial } })).value;
  const useCallback = (callback, dependencies) => {
    const state = slot("callback", () => ({}));
    if (!sameDependencies(state.dependencies, dependencies)) {
      state.dependencies = dependencies;
      state.value = callback;
    }
    return state.value;
  };
  const useEffect = (callback, dependencies) => {
    const state = slot("effect", () => ({}));
    if (!sameDependencies(state.dependencies, dependencies)) {
      state.dependencies = dependencies;
      effects.push({ state, callback });
    }
  };
  const hooks = loadHooks(
    useCallback, useEffect, useRef, useState, PlatformApiError,
    deriveArtworksFromTasks, normalizePage,
    {
      setTimeout(callback, delay) { const id = ++timerId; timers.set(id, { callback, delay }); return id; },
      clearTimeout(id) { timers.delete(id); },
    },
  );
  const flush = () => {
    let renders = 0;
    while (mounted && dirty) {
      assert.ok(++renders < 30, "Reset must not cause an effect/render loop");
      cursor = 0;
      effects = [];
      dirty = false;
      const rendered = hooks[variant.hook](props);
      current = rendered;
      if (resetOnWorkspace) {
        const sessionAtRender = props.hasStudioSession;
        // App's workspace-initialization effect is registered AFTER the hook's
        // fetch effect, and resets even when the default filters have not changed.
        useEffect(() => {
          if (sessionAtRender) rendered[variant.reset]();
        }, [props.hasStudioSession, props.studioWorkspaceKey]);
      }
      const committed = effects;
      for (const effect of committed) effect.state.cleanup?.();
      for (const effect of committed) effect.state.cleanup = effect.callback();
    }
  };
  flush();
  return {
    get current() { return current; },
    get timerDelays() { return [...timers.values()].map((timer) => timer.delay); },
    flush,
    render(changes = {}) { props = { ...props, ...changes }; dirty = true; flush(); },
    async settle() {
      for (let step = 0; step < 6; step += 1) { await Promise.resolve(); flush(); }
    },
    tick() {
      const pending = [...timers.values()];
      timers.clear();
      for (const timer of pending) timer.callback();
      flush();
    },
    unmount() {
      mounted = false;
      for (const state of slots) if (state.kind === "effect") state.cleanup?.();
    },
  };
}

for (const variant of variants) {
  const rows = (harness) => harness.current[variant.rows].map((row) => row.id);

  test(`${variant.name}: mount followed by the App workspace reset schedules a fresh request`, async (t) => {
    const queue = queuedClient();
    const harness = collectionHarness(variant, queue.client, { resetOnWorkspace: true });
    t.after(() => harness.unmount());
    assert.equal(queue.requests.length, 2, "The reset must restart unchanged default filters");
    assert.equal(queue.requests[0].signal.aborted, true);
    assert.equal(queue.requests[1].signal.aborted, false);
    assert.equal(harness.current[variant.loading], true);
    if (variant.nav === "create") {
      assert.equal(harness.current.creationDays, "30", "Do not mask the race by changing the date default");
      const cutoff = new Date(queue.requests[1].filters.start_time).getTime();
      assert.ok(Math.abs(Date.now() - cutoff - 30 * 86_400_000) < 2_000);
    }
    resolvePage(queue.requests[0], ["discarded-mount-response"]);
    resolvePage(queue.requests[1], ["fresh-1", "fresh-2"]);
    await harness.settle();
    assert.deepEqual(rows(harness), ["fresh-1", "fresh-2"]);
    assert.equal(harness.current[variant.total], 2);
    assert.deepEqual(harness.timerDelays, [variant.pollMs]);
  });

  test(`${variant.name}: an already loaded collection can reset and continue its normal polling`, async (t) => {
    const queue = queuedClient();
    const harness = collectionHarness(variant, queue.client);
    t.after(() => harness.unmount());
    resolvePage(queue.requests[0], ["before-reset"]);
    await harness.settle();
    assert.deepEqual(harness.timerDelays, [variant.pollMs]);
    harness.current[variant.reset]();
    harness.flush();
    assert.deepEqual(rows(harness), []);
    assert.equal(harness.current[variant.total], 0);
    assert.equal(queue.requests.length, 2);
    assert.deepEqual(harness.timerDelays, [], "The old polling timer must be cancelled");
    resolvePage(queue.requests[1], ["after-reset"]);
    await harness.settle();
    assert.deepEqual(rows(harness), ["after-reset"]);
    assert.deepEqual(harness.timerDelays, [variant.pollMs]);
    harness.tick();
    assert.equal(queue.requests.length, 3);
    resolvePage(queue.requests[2], ["after-reset", "poll-update"]);
    await harness.settle();
    assert.deepEqual(rows(harness), ["after-reset", "poll-update"]);
    assert.deepEqual(harness.timerDelays, [variant.pollMs], "Exactly one polling loop survives");
  });

  test(`${variant.name}: a workspace switch excludes late responses from both abandoned requests`, async (t) => {
    const first = queuedClient();
    const second = queuedClient();
    const harness = collectionHarness(variant, first.client, { resetOnWorkspace: true });
    t.after(() => harness.unmount());
    harness.render({ studioWorkspaceKey: "company:second:owner", studioClient: second.client });
    assert.equal(second.requests.length, 2, "The new workspace initialization needs its own fresh request");
    assert.ok(first.requests.every((request) => request.signal.aborted));
    assert.equal(second.requests[0].signal.aborted, true);
    for (const request of first.requests) resolvePage(request, ["private-first-workspace"]);
    resolvePage(second.requests[0], ["abandoned-second-request"]);
    await harness.settle();
    assert.deepEqual(rows(harness), []);
    assert.equal(harness.current[variant.total], 0);
    assert.equal(harness.current[variant.loading], true);
    resolvePage(second.requests[1], ["authorized-second-workspace"]);
    await harness.settle();
    assert.deepEqual(rows(harness), ["authorized-second-workspace"]);
    assert.deepEqual(harness.timerDelays, [variant.pollMs]);
  });

  for (const outcome of ["success", "failure"]) {
    test(`${variant.name}: reset fences a late ${outcome} before the fresh effect commits`, async (t) => {
      const queue = queuedClient();
      let authenticationErrors = 0;
      const harness = collectionHarness(variant, queue.client, {
        onAuthenticationError: () => { authenticationErrors += 1; return true; },
      });
      t.after(() => harness.unmount());
      harness.current[variant.reset]();
      if (outcome === "success") resolvePage(queue.requests[0], ["stale-private-record"]);
      else queue.requests[0].reject(new PlatformApiError("stale session", { status: 401 }));
      // Permit the promise to finish before rendering: the synchronous generation
      // fence must protect this gap as well as the subsequent abort cleanup.
      for (let step = 0; step < 6; step += 1) await Promise.resolve();
      harness.flush();
      assert.equal(queue.requests.length, 2);
      assert.deepEqual(rows(harness), []);
      assert.equal(harness.current[variant.total], 0);
      assert.equal(harness.current[variant.error], "");
      assert.equal(harness.current[variant.loading], true);
      assert.equal(authenticationErrors, 0, "An obsolete 401 cannot expire the new workspace session");
      assert.deepEqual(harness.timerDelays, []);
      resolvePage(queue.requests[1], ["fresh"]);
      await harness.settle();
      assert.deepEqual(rows(harness), ["fresh"]);
    });
  }

  test(`${variant.name}: withdrawing read permission clears records without restarting a request`, async (t) => {
    const queue = queuedClient();
    const harness = collectionHarness(variant, queue.client);
    t.after(() => harness.unmount());
    resolvePage(queue.requests[0], ["formerly-authorized"]);
    await harness.settle();
    harness.tick();
    assert.equal(queue.requests.length, 2);
    harness.render({ [variant.permission]: false });
    assert.equal(queue.requests[1].signal.aborted, true);
    resolvePage(queue.requests[1], ["must-stay-hidden"]);
    await harness.settle();
    harness.current[variant.reset]();
    harness.flush();
    assert.equal(queue.requests.length, 2);
    assert.deepEqual(rows(harness), []);
    assert.deepEqual(harness.timerDelays, []);
    harness.render({ [variant.permission]: true });
    assert.equal(queue.requests.length, 3);
    resolvePage(queue.requests[2], ["authorized-again"]);
    await harness.settle();
    assert.deepEqual(rows(harness), ["authorized-again"]);
  });

  for (const [guard, value] of [
    ["liveMode", false], ["authExpired", true], ["hasStudioSession", false],
    ["effectiveSurface", "company"], ["activeNav", "shots"],
  ]) {
    test(`${variant.name}: reset respects the ${guard} admission guard`, async (t) => {
      const queue = queuedClient();
      const harness = collectionHarness(variant, queue.client, { [guard]: value });
      t.after(() => harness.unmount());
      harness.current[variant.reset]();
      await harness.settle();
      assert.equal(queue.requests.length, 0);
      assert.deepEqual(rows(harness), []);
      assert.deepEqual(harness.timerDelays, []);
    });
  }

  test(`${variant.name}: resetFilters false preserves filters while resetting data and requests`, async (t) => {
    const queue = queuedClient();
    const harness = collectionHarness(variant, queue.client);
    t.after(() => harness.unmount());
    harness.current[variant.filterSetter](variant.filterValue);
    harness.flush();
    assert.equal(queue.requests.length, 2);
    harness.current[variant.reset]({ resetFilters: false });
    harness.flush();
    assert.equal(queue.requests.length, 3);
    assert.equal(queue.requests[2].filters[variant.filter], variant.filterValue);
    harness.current[variant.reset]();
    harness.flush();
    assert.equal(queue.requests.length, 4);
    assert.equal(queue.requests[3].filters[variant.filter], "");
    resolvePage(queue.requests[3], ["default-filter-result"]);
    await harness.settle();
    assert.deepEqual(rows(harness), ["default-filter-result"]);
  });

  test(`${variant.name}: unmount cancels polling and aborts an in-flight refresh`, async () => {
    const queue = queuedClient();
    const harness = collectionHarness(variant, queue.client);
    resolvePage(queue.requests[0], ["visible-before-unmount"]);
    await harness.settle();
    assert.deepEqual(harness.timerDelays, [variant.pollMs]);
    harness.tick();
    assert.equal(queue.requests.length, 2);
    harness.unmount();
    assert.equal(queue.requests[1].signal.aborted, true);
    resolvePage(queue.requests[1], ["late-after-unmount"]);
    await harness.settle();
    assert.deepEqual(rows(harness), ["visible-before-unmount"]);
    assert.deepEqual(harness.timerDelays, []);
  });
}
