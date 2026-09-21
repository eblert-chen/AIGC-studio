from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from platform_api.director_canonical_json import director_canonical_json
from platform_api.relay_client import RelayDirectorShotInput, relay_canonical_json
from platform_api.services.director_shot_packages import (
    DirectorShotManifest,
    seedance_director_shot_prompt_v1,
)


CONTRACTS = Path(__file__).resolve().parents[3] / "contracts"
FIXTURE = json.loads(
    (CONTRACTS / "director-canonical-json-v1-fixtures.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda case: case["name"])
def test_director_json_matches_browser_fixed_bytes_and_hash(case):
    encoded = director_canonical_json(json.loads(case["json"]))
    assert encoded == case["canonical"].encode("utf-8")
    assert hashlib.sha256(encoded).hexdigest() == case["sha256"]


@pytest.mark.parametrize("case", FIXTURE["rejected"], ids=lambda case: case["name"])
def test_director_json_rejects_nonportable_values(case):
    with pytest.raises((ValueError, UnicodeError)):
        director_canonical_json(json.loads(case["json"]))


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), {1: "key"}, object()])
def test_director_json_rejects_non_json_python_values(value):
    with pytest.raises(ValueError):
        director_canonical_json(value)


@pytest.mark.parametrize("case", FIXTURE["manifests"], ids=lambda case: case["name"])
def test_director_new_seal_validates_and_compiles_exact_prompt(case):
    value = RelayDirectorShotInput(
        manifest=case["manifest"],
        manifest_sha256=case["manifest_sha256"],
        sealed_revision=case["sealed_revision"],
    )
    assert value.manifest_sha256 == "sha256:" + hashlib.sha256(
        director_canonical_json(value.manifest)
    ).hexdigest()
    prompt = seedance_director_shot_prompt_v1(
        case["prompt"],
        manifest=DirectorShotManifest.model_validate(value.manifest),
        manifest_sha256=value.manifest_sha256,
        sealed_revision=value.sealed_revision,
    )
    assert len(prompt) == case["expected_prompt_runes"]
    assert hashlib.sha256(prompt.encode("utf-8")).hexdigest() == (
        case["expected_prompt_sha256"]
    )
    tampered = json.loads(json.dumps(case["manifest"]))
    tampered["camera"]["name"] = "Changed camera"
    with pytest.raises(ValueError, match="manifest_sha256"):
        RelayDirectorShotInput(
            manifest=tampered,
            manifest_sha256=case["manifest_sha256"],
            sealed_revision=case["sealed_revision"],
        )


def test_director_correction_does_not_change_frozen_catalog_or_resign_old_manifest():
    assert relay_canonical_json({"value": 0.00001}) == b'{"value":1e-05}'
    historical = json.loads(
        (CONTRACTS / "director-shot-prompt-v1-fixtures.json").read_text(
            encoding="utf-8"
        )
    )["cases"][0]
    assert historical["manifest_sha256"] == "sha256:" + hashlib.sha256(
        relay_canonical_json(historical["manifest"])
    ).hexdigest()
    with pytest.raises(ValueError, match="manifest_sha256"):
        RelayDirectorShotInput(
            manifest=historical["manifest"],
            manifest_sha256=historical["manifest_sha256"],
            sealed_revision=historical["sealed_revision"],
        )
