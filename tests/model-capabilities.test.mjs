import assert from "node:assert/strict";
import test from "node:test";

import {
  buildCapabilityRequestPayload,
  capabilityControlVisibility,
  reconcileGenerationDraft,
  resolveEffectiveCapabilities,
  toCanonicalGenerationConfig,
} from "../src/modelCapabilities.js";

function canonicalMode({
  maxImages = 9,
  maxVideos = 3,
  maxAudio = 3,
  durations = [5, 10],
  outputCounts = [1, 2, 3],
  supportsFace = true,
} = {}) {
  return {
    input_media_types: ["audio", "image", "video"],
    supports_face: supportsFace,
    required_resource_keys: [],
    limits: {
      max_prompt_length: 2_000,
      max_images: maxImages,
      max_videos: maxVideos,
      max_audio: maxAudio,
      duration_seconds: durations,
      aspect_ratios: ["16:9", "9:16"],
      resolutions: ["1080p", "720p"],
      output_counts: outputCounts,
    },
  };
}

function canonicalDocument(modes) {
  return { schema_version: 1, modes };
}

test("keeps canonical 9+3+3 and 4+3+3 media limits without truncation", async (t) => {
  for (const [label, maxImages] of [
    ["9+3+3", 9],
    ["4+3+3", 4],
  ]) {
    await t.test(label, () => {
      const effective = resolveEffectiveCapabilities({
        capabilities: {
          generation: canonicalDocument({
            image_to_video: canonicalMode({ maxImages }),
          }),
        },
      });

      assert.deepEqual(
        effective.modes.image_to_video.limits,
        {
          maxPromptLength: 2_000,
          maxImages,
          maxVideos: 3,
          maxAudio: 3,
          durations: [5, 10],
          aspectRatios: ["16:9", "9:16"],
          resolutions: ["1080p", "720p"],
          outputCounts: [1, 2, 3],
        },
      );
    });
  }
});

test("preserves the duration 3600 and output-count 16 contract limits", () => {
  const effective = resolveEffectiveCapabilities({
    capabilities: {
      generation: canonicalDocument({
        text_to_video: canonicalMode({
          durations: [3600],
          outputCounts: [16],
        }),
      }),
    },
  });

  assert.deepEqual(effective.modes.text_to_video.limits.durations, [3600]);
  assert.deepEqual(effective.modes.text_to_video.limits.outputCounts, [16]);
});

test("prefers effective capabilities over legacy raw capabilities and overrides", () => {
  const effective = resolveEffectiveCapabilities({
    effective_capabilities: canonicalDocument({
      text_to_video: canonicalMode({
        maxImages: 4,
        maxVideos: 3,
        maxAudio: 2,
        durations: [10],
        outputCounts: [2],
      }),
    }),
    capabilities: {
      generation: canonicalDocument({
        image_to_video: canonicalMode({ maxImages: 9 }),
      }),
    },
    config_override: canonicalDocument({
      image_to_video: canonicalMode({ maxImages: 1 }),
    }),
  });

  assert.deepEqual(Object.keys(effective.modes), ["text_to_video"]);
  assert.equal(effective.modes.text_to_video.limits.maxImages, 4);
  assert.equal(effective.modes.text_to_video.limits.maxAudio, 2);
  assert.deepEqual(effective.modes.text_to_video.limits.durations, [10]);
  assert.deepEqual(effective.modes.text_to_video.limits.outputCounts, [2]);
});

test("round-trips a canonical generation document exactly", () => {
  const canonical = canonicalDocument({
    image_to_video: canonicalMode({ maxImages: 9 }),
    text_to_video: canonicalMode({
      maxImages: 4,
      maxVideos: 3,
      maxAudio: 3,
      durations: [5, 3600],
      outputCounts: [1, 16],
      supportsFace: false,
    }),
  });

  const normalized = resolveEffectiveCapabilities({
    capabilities: { generation: canonical },
  });

  assert.deepEqual(toCanonicalGenerationConfig(normalized.modes), canonical);
});

test("accepts max_audio in a legacy per-mode capability", () => {
  const effective = resolveEffectiveCapabilities({
    capabilities: {
      "text-to-video": {
        max_audio: 3,
        durations: [5],
        resolutions: ["720p"],
        output_counts: [1],
      },
    },
  });

  assert.equal(effective.modes.text_to_video.limits.maxAudio, 3);
  assert.deepEqual(effective.modes.text_to_video.inputMediaTypes, ["audio"]);
});

function asset(mediaType, index) {
  return { id: `${mediaType}-${index}`, media_type: mediaType };
}

function assets(mediaType, count) {
  return Array.from({ length: count }, (_, index) => asset(mediaType, index + 1));
}

test("normalizes malformed effective media declarations fail-closed", async (t) => {
  await t.test("a positive maximum cannot enable an undeclared media type", () => {
    const mode = canonicalMode({ maxImages: 4, maxVideos: 3, maxAudio: 3 });
    mode.input_media_types = ["image"];
    const effective = resolveEffectiveCapabilities({
      effective_capabilities: canonicalDocument({ text_to_video: mode }),
    });

    assert.deepEqual(effective.modes.text_to_video.inputMediaTypes, ["image"]);
    assert.equal(effective.modes.text_to_video.limits.maxImages, 4);
    assert.equal(effective.modes.text_to_video.limits.maxVideos, 0);
    assert.equal(effective.modes.text_to_video.limits.maxAudio, 0);
  });

  await t.test("a declared media type with a zero maximum is removed", () => {
    const mode = canonicalMode({ maxImages: 0, maxVideos: 0, maxAudio: 0 });
    mode.input_media_types = ["audio"];
    const effective = resolveEffectiveCapabilities({
      effective_capabilities: canonicalDocument({ text_to_video: mode }),
    });

    assert.deepEqual(effective.modes.text_to_video.inputMediaTypes, []);
    assert.equal(effective.modes.text_to_video.limits.maxAudio, 0);
  });

  for (const [label, mutate] of [
    ["missing required image", (mode) => {
      mode.input_media_types = ["video"];
    }],
    ["zero required image maximum", (mode) => {
      mode.limits.max_images = 0;
    }],
    ["empty duration enum", (mode) => {
      mode.limits.duration_seconds = [];
    }],
  ]) {
    await t.test(label, () => {
      const mode = canonicalMode({ maxImages: 4 });
      mutate(mode);
      const effective = resolveEffectiveCapabilities({
        effective_capabilities: canonicalDocument({ image_to_video: mode }),
      });
      assert.deepEqual(effective.modes, {});
    });
  }
});

test("empty capabilities clear the draft and cannot build a request", () => {
  const capabilities = resolveEffectiveCapabilities({
    effective_capabilities: canonicalDocument({}),
  });
  const draft = {
    prompt: "stale prompt",
    duration: 10,
    aspectRatio: "16:9",
    resolution: "1080p",
    outputCount: 3,
    faceEnabled: true,
    files: {
      image: assets("image", 2),
      video: assets("video", 1),
      audio: assets("audio", 1),
    },
  };

  const reconciled = reconcileGenerationDraft(capabilities, "text_to_video", draft);
  assert.equal(reconciled.ok, false);
  assert.equal(reconciled.mode, "");
  assert.equal(reconciled.capability, null);
  assert.equal(reconciled.removedMediaCount, 4);
  assert.deepEqual(reconciled.draft.files, { image: [], video: [], audio: [] });
  assert.equal(reconciled.draft.faceEnabled, false);
  assert.equal(reconciled.draft.duration, null);
  assert.equal(reconciled.draft.aspectRatio, "");
  assert.equal(reconciled.draft.resolution, "");
  assert.equal(reconciled.draft.outputCount, null);

  const built = buildCapabilityRequestPayload(
    capabilities,
    "text_to_video",
    draft,
  );
  assert.equal(built.ok, false);
  assert.equal(built.payload, null);
});

test("each media type is clipped from max plus one to the exact model maximum", async (t) => {
  for (const [label, maximums] of [
    ["9+3+3", { image: 9, video: 3, audio: 3 }],
    ["4+3+3", { image: 4, video: 3, audio: 3 }],
  ]) {
    await t.test(label, () => {
      const capabilities = resolveEffectiveCapabilities({
        effective_capabilities: canonicalDocument({
          text_to_video: canonicalMode({
            maxImages: maximums.image,
            maxVideos: maximums.video,
            maxAudio: maximums.audio,
            supportsFace: false,
          }),
        }),
      });
      const reconciled = reconcileGenerationDraft(capabilities, "text_to_video", {
        prompt: "media boundary",
        duration: 5,
        aspectRatio: "16:9",
        resolution: "720p",
        outputCount: 1,
        faceEnabled: false,
        files: {
          image: assets("image", maximums.image + 1),
          video: assets("video", maximums.video + 1),
          audio: assets("audio", maximums.audio + 1),
        },
      });

      assert.equal(reconciled.ok, true);
      assert.equal(reconciled.removedMediaCount, 3);
      assert.equal(reconciled.draft.files.image.length, maximums.image);
      assert.equal(reconciled.draft.files.video.length, maximums.video);
      assert.equal(reconciled.draft.files.audio.length, maximums.audio);
    });
  }
});

test("model switching clips media and replaces every stale enum before submission", () => {
  const capabilities = resolveEffectiveCapabilities({
    effective_capabilities: canonicalDocument({
      text_to_video: {
        ...canonicalMode({
          maxImages: 4,
          maxVideos: 3,
          maxAudio: 3,
          durations: [5, 10],
          outputCounts: [1, 2],
          supportsFace: false,
        }),
        limits: {
          ...canonicalMode({
            maxImages: 4,
            maxVideos: 3,
            maxAudio: 3,
            durations: [5, 10],
            outputCounts: [1, 2],
            supportsFace: false,
          }).limits,
          aspect_ratios: ["9:16"],
          resolutions: ["720p"],
        },
      },
    }),
  });
  const staleDraft = {
    prompt: "  submit against the newly selected model  ",
    duration: 20,
    aspectRatio: "1:1",
    resolution: "1080p",
    outputCount: 4,
    faceEnabled: true,
    files: {
      image: assets("image", 9),
      video: assets("video", 3),
      audio: assets("audio", 3),
    },
  };

  const built = buildCapabilityRequestPayload(
    capabilities,
    "text_to_video",
    staleDraft,
  );

  assert.equal(built.ok, true);
  assert.equal(built.reconciled.removedMediaCount, 5);
  assert.deepEqual(
    {
      duration: built.payload.duration_seconds,
      aspectRatio: built.payload.aspect_ratio,
      resolution: built.payload.resolution,
      outputCount: built.payload.output_count,
    },
    { duration: 5, aspectRatio: "9:16", resolution: "720p", outputCount: 1 },
  );
  assert.equal(Object.hasOwn(built.payload, "face_enabled"), false);
  assert.equal(built.payload.assets.length, 10);
  assert.deepEqual(
    built.payload.assets.reduce(
      (counts, item) => ({ ...counts, [item.media_type]: counts[item.media_type] + 1 }),
      { image: 0, video: 0, audio: 0 },
    ),
    { image: 4, video: 3, audio: 3 },
  );
});

test("unsupported inputs and face are omitted while supported face is preserved", () => {
  const imageOnly = resolveEffectiveCapabilities({
    effective_capabilities: canonicalDocument({
      text_to_video: {
        ...canonicalMode({
          maxImages: 2,
          maxVideos: 0,
          maxAudio: 0,
          supportsFace: false,
        }),
        input_media_types: ["image"],
      },
    }),
  });
  const draft = {
    prompt: "only allowed inputs belong in the request",
    duration: 5,
    aspectRatio: "16:9",
    resolution: "720p",
    outputCount: 1,
    faceEnabled: true,
    files: {
      image: assets("image", 2),
      video: assets("video", 2),
      audio: assets("audio", 2),
    },
  };
  const imageRequest = buildCapabilityRequestPayload(imageOnly, "text_to_video", draft);
  assert.equal(imageRequest.ok, true);
  assert.deepEqual(
    imageRequest.payload.assets,
    assets("image", 2).map((item) => ({
      asset_id: item.id,
      media_type: "image",
    })),
  );
  assert.equal(Object.hasOwn(imageRequest.payload, "face_enabled"), false);

  const faceCapable = resolveEffectiveCapabilities({
    effective_capabilities: canonicalDocument({
      text_to_video: canonicalMode({
        maxImages: 0,
        maxVideos: 0,
        maxAudio: 0,
        supportsFace: true,
      }),
    }),
  });
  faceCapable.modes.text_to_video.inputMediaTypes = [];
  const faceRequest = buildCapabilityRequestPayload(faceCapable, "text_to_video", {
    ...draft,
    files: { image: [], video: [], audio: [] },
  });
  assert.equal(faceRequest.ok, true);
  assert.equal(faceRequest.payload.face_enabled, true);
});

test("studio control visibility comes from the same effective capability", () => {
  assert.deepEqual(capabilityControlVisibility(null), {
    image: false,
    video: false,
    audio: false,
    face: false,
  });

  const capability = {
    inputMediaTypes: ["image", "video"],
    supportsFace: false,
    limits: {
      maxImages: 4,
      maxVideos: 0,
      maxAudio: 3,
    },
  };
  assert.deepEqual(capabilityControlVisibility(capability), {
    image: true,
    video: false,
    audio: false,
    face: false,
  });

  assert.deepEqual(
    capabilityControlVisibility({
      ...capability,
      inputMediaTypes: ["audio"],
      supportsFace: true,
    }),
    { image: false, video: false, audio: true, face: true },
  );
});

test("submission does not silently pass an old unsupported mode", () => {
  const capabilities = resolveEffectiveCapabilities({
    effective_capabilities: canonicalDocument({
      text_to_video: canonicalMode({ supportsFace: false }),
    }),
  });
  const result = buildCapabilityRequestPayload(capabilities, "video_to_video", {
    prompt: "old model mode must not cross the submission boundary",
    duration: 5,
    aspectRatio: "16:9",
    resolution: "720p",
    outputCount: 1,
    faceEnabled: false,
    files: { image: [], video: [], audio: [] },
  });

  assert.equal(result.ok, false);
  assert.equal(result.payload, null);
  assert.equal(result.reconciled.mode, "text_to_video");
});
