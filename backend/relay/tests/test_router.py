from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from relay_service.models import (
    AssetInput,
    CapabilityLimits,
    GenerationInputs,
    GenerationJob,
    GenerationMode,
    ModelCapability,
    OutputOptions,
)
from relay_service.providers.base import (
    ProviderChannelType,
    ProviderContractError,
    ProviderError,
    ProviderSubmission,
)
from relay_service.providers.mock import MockProviderAdapter
from relay_service.providers.router import ProviderRouter


async def _pin_capability_revision(
    router: ProviderRouter, job: GenerationJob
) -> GenerationJob:
    catalog = await router.model_catalog()
    model = next(item for item in catalog.data if item.id == job.model)
    return job.model_copy(
        update={"expected_capability_revision": model.capability_revision}
    )


def _submit(router: ProviderRouter, job: GenerationJob):
    async def scenario():
        pinned_job = await _pin_capability_revision(router, job)
        return await router.submit(pinned_job)

    return asyncio.run(scenario())


def test_public_model_catalog_is_deterministic_and_failover_safe() -> None:
    class RestrictedRoute(MockProviderAdapter):
        async def capabilities(self) -> list[ModelCapability]:
            return [
                ModelCapability(
                    model="mock.video.v1",
                    modes=[GenerationMode.TEXT_TO_VIDEO],
                    input_media_types=["image", "video", "audio"],
                    supports_face=False,
                    limits=CapabilityLimits(
                        max_prompt_length=2_000,
                        max_images=4,
                        max_videos=3,
                        max_audio=3,
                        duration_seconds=[5, 10],
                        aspect_ratios=["16:9", "9:16"],
                        resolutions=["720p"],
                        output_counts=[1, 2],
                    ),
                    available_providers=[self.name],
                )
            ]

    broad = MockProviderAdapter(account_id="broad", healthy=False)
    restricted = RestrictedRoute(account_id="restricted")
    first = asyncio.run(ProviderRouter([broad, restricted]).model_catalog())
    second = asyncio.run(ProviderRouter([restricted, broad]).model_catalog())

    assert first == second
    assert [item.id for item in first.data] == ["mock.video.v1"]
    resource = first.data[0]
    assert resource.capability_revision.startswith("sha256:")
    video = resource.capabilities.modes[GenerationMode.TEXT_TO_VIDEO]
    assert video.supports_face is False
    assert video.limits.max_images == 4
    assert video.limits.max_prompt_length == 2_000
    assert video.limits.resolutions == ["720p"]
    assert video.limits.output_counts == [1, 2]
    # A mode supported by only one configured route remains available.
    assert GenerationMode.TEXT_TO_IMAGE in resource.capabilities.modes

    legacy = asyncio.run(
        ProviderRouter([restricted, broad]).capabilities()
    )
    assert all(len(item.modes) == 1 for item in legacy)
    legacy_by_mode = {item.modes[0]: item for item in legacy}
    assert legacy_by_mode[
        GenerationMode.TEXT_TO_VIDEO
    ].limits.max_images == 4
    assert legacy_by_mode[
        GenerationMode.TEXT_TO_IMAGE
    ].limits.max_images == 9


def test_submission_cannot_exceed_failover_safe_public_capability() -> None:
    class CountingBroadRoute(MockProviderAdapter):
        calls = 0

        async def submit(self, job: GenerationJob) -> ProviderSubmission:
            self.calls += 1
            return await super().submit(job)

    class FiveSecondRoute(MockProviderAdapter):
        async def capabilities(self) -> list[ModelCapability]:
            capability = (await super().capabilities())[0]
            capability.limits.duration_seconds = [5]
            return [capability]

    broad = CountingBroadRoute(account_id="broad")
    restricted = FiveSecondRoute(account_id="restricted")
    router = ProviderRouter([broad, restricted])
    catalog = asyncio.run(router.model_catalog())
    resource = catalog.data[0]
    assert resource.capabilities.modes[
        GenerationMode.TEXT_TO_VIDEO
    ].limits.duration_seconds == [5]
    job = GenerationJob(
        tenant_id=uuid4(),
        model="mock.video.v1",
        mode=GenerationMode.TEXT_TO_VIDEO,
        inputs=GenerationInputs(prompt="test"),
        output=OutputOptions(duration_seconds=10),
        metadata={"relay_capability_revision": resource.capability_revision},
    )

    with pytest.raises(ProviderError) as caught:
        _submit(router, job)

    assert caught.value.code == "REQUEST_NOT_SUPPORTED_BY_MODEL"
    assert broad.calls == 0


def test_retryable_provider_failure_falls_back() -> None:
    failing = MockProviderAdapter(
        fail_submit=True, priority=10
    )
    failing.name = "mock-primary"
    healthy = MockProviderAdapter(priority=20)
    healthy.name = "mock-secondary"
    router = ProviderRouter(
        [failing, healthy], failure_threshold=1, cooldown_seconds=60
    )
    job = GenerationJob(
        tenant_id=uuid4(),
        model="mock.video.v1",
        mode=GenerationMode.TEXT_TO_VIDEO,
        inputs=GenerationInputs(prompt="test"),
        output=OutputOptions(),
    )

    provider_name, submission = _submit(router, job)

    assert provider_name == "mock-secondary"
    assert submission.provider_task_id == f"mock-{job.id}"
    health = asyncio.run(router.health())
    assert health["mock-primary"] is False
    assert health["mock-secondary"] is True


def test_non_account_request_error_does_not_fail_over_or_open_circuit() -> None:
    class RejectingProvider(MockProviderAdapter):
        async def submit(self, job: GenerationJob) -> ProviderSubmission:
            raise ProviderError(
                "INVALID_USER_REQUEST",
                "The request is invalid",
                retryable=False,
                account_unavailable=False,
            )

    class CountingProvider(MockProviderAdapter):
        calls = 0

        async def submit(self, job: GenerationJob) -> ProviderSubmission:
            self.calls += 1
            return await super().submit(job)

    rejecting = RejectingProvider(priority=10)
    rejecting.name = "request-rejecting"
    fallback = CountingProvider(priority=20)
    fallback.name = "request-fallback"
    router = ProviderRouter(
        [rejecting, fallback], failure_threshold=1, cooldown_seconds=60
    )
    generation = GenerationJob(
        tenant_id=uuid4(),
        model="mock.video.v1",
        mode=GenerationMode.TEXT_TO_VIDEO,
        inputs=GenerationInputs(prompt="invalid for the upstream model"),
        output=OutputOptions(),
    )

    with pytest.raises(ProviderError) as caught:
        _submit(router, generation)

    assert caught.value.code == "INVALID_USER_REQUEST"
    assert caught.value.route_id == "request-rejecting"
    assert fallback.calls == 0
    health = asyncio.run(router.health())
    assert health["request-rejecting"] is True
    assert health["request-fallback"] is True


def test_hard_account_failure_switches_account_and_opens_failed_route() -> None:
    class AuthenticationFailure(MockProviderAdapter):
        calls = 0

        async def submit(self, job: GenerationJob) -> ProviderSubmission:
            self.calls += 1
            raise ProviderError(
                "AUTHENTICATION_FAILED",
                "Provider account authentication failed",
                retryable=False,
                account_unavailable=True,
            )

    class HealthyAccount(MockProviderAdapter):
        calls = 0

        async def submit(self, job: GenerationJob) -> ProviderSubmission:
            self.calls += 1
            return await super().submit(job)

    failed_account = AuthenticationFailure(
        account_id="account-a", priority=10
    )
    healthy_account = HealthyAccount(
        account_id="account-b", priority=20
    )
    router = ProviderRouter(
        [failed_account, healthy_account],
        failure_threshold=1,
        cooldown_seconds=60,
    )
    first_job = GenerationJob(
        tenant_id=uuid4(),
        model="mock.video.v1",
        mode=GenerationMode.TEXT_TO_VIDEO,
        inputs=GenerationInputs(prompt="first"),
        output=OutputOptions(),
    )
    second_job = first_job.model_copy(
        deep=True,
        update={
            "id": uuid4(),
            "inputs": GenerationInputs(prompt="second"),
        },
    )

    first_route, _ = _submit(router, first_job)
    second_route, _ = _submit(router, second_job)

    assert first_route == "mock-video@account-b"
    assert second_route == "mock-video@account-b"
    assert failed_account.calls == 1
    assert healthy_account.calls == 2


def test_unexpected_submit_exception_is_outcome_unknown_and_keeps_route() -> None:
    class BrokenProvider(MockProviderAdapter):
        async def submit(self, job: GenerationJob) -> ProviderSubmission:
            raise RuntimeError("unexpected adapter failure")

    class CountingProvider(MockProviderAdapter):
        calls = 0

        async def submit(self, job: GenerationJob) -> ProviderSubmission:
            self.calls += 1
            return await super().submit(job)

    broken = BrokenProvider(priority=10)
    broken.name = "broken-adapter"
    fallback = CountingProvider(priority=20)
    fallback.name = "unsafe-fallback"
    router = ProviderRouter([broken, fallback])
    generation = GenerationJob(
        tenant_id=uuid4(),
        model="mock.video.v1",
        mode=GenerationMode.TEXT_TO_VIDEO,
        inputs=GenerationInputs(prompt="test"),
        output=OutputOptions(),
    )

    with pytest.raises(ProviderError) as caught:
        _submit(router, generation)

    assert caught.value.code == "PROVIDER_ADAPTER_ERROR"
    assert caught.value.retryable is False
    assert caught.value.submission_outcome_unknown is True
    assert caught.value.account_unavailable is False
    assert caught.value.route_id == "broken-adapter"
    assert fallback.calls == 0


@pytest.mark.parametrize(
    ("retryable", "account_unavailable"),
    [(True, False), (False, True), (True, True)],
)
def test_unknown_submission_error_rejects_failover_flags(
    retryable: bool, account_unavailable: bool
) -> None:
    with pytest.raises(ValueError, match="cannot be retried or failed over"):
        ProviderError(
            "SUBMISSION_OUTCOME_UNKNOWN",
            "The upstream may have accepted the task",
            retryable=retryable,
            account_unavailable=account_unavailable,
            submission_outcome_unknown=True,
        )


def test_malformed_unknown_submission_error_still_never_fails_over() -> None:
    class AmbiguousProvider(MockProviderAdapter):
        async def submit(self, job: GenerationJob) -> ProviderSubmission:
            error = ProviderError(
                "SUBMISSION_OUTCOME_UNKNOWN",
                "The upstream may have accepted the task",
                retryable=False,
                account_unavailable=False,
                submission_outcome_unknown=True,
            )
            # Defend against a plugin mutating the otherwise validated error.
            error.retryable = True
            raise error

    class CountingProvider(MockProviderAdapter):
        calls = 0

        async def submit(self, job: GenerationJob) -> ProviderSubmission:
            self.calls += 1
            return await super().submit(job)

    ambiguous = AmbiguousProvider(priority=10)
    ambiguous.name = "ambiguous-primary"
    fallback = CountingProvider(priority=20)
    fallback.name = "must-not-submit"
    job = GenerationJob(
        tenant_id=uuid4(),
        model="mock.video.v1",
        mode=GenerationMode.TEXT_TO_VIDEO,
        inputs=GenerationInputs(prompt="test"),
        output=OutputOptions(),
    )

    with pytest.raises(ProviderError) as caught:
        _submit(ProviderRouter([ambiguous, fallback]), job)

    assert caught.value.submission_outcome_unknown is True
    assert caught.value.route_id == "ambiguous-primary"
    assert fallback.calls == 0


def test_same_provider_multiple_accounts_round_robin_and_sticky_poll() -> None:
    class AccountMock(MockProviderAdapter):
        def __init__(self, account_id: str) -> None:
            super().__init__(account_id=account_id)
            self.polled: list[str] = []

        async def submit(self, job: GenerationJob) -> ProviderSubmission:
            return ProviderSubmission(
                provider_task_id=f"{self.account_id}-{job.id}"
            )

        async def poll(self, job: GenerationJob):
            self.polled.append(job.provider_task_id or "")
            return None

    account_a = AccountMock("account-a")
    account_b = AccountMock("account-b")
    router = ProviderRouter([account_a, account_b])

    first_job = GenerationJob(
        tenant_id=uuid4(),
        model="mock.video.v1",
        mode=GenerationMode.TEXT_TO_VIDEO,
        inputs=GenerationInputs(prompt="first"),
        output=OutputOptions(),
    )
    second_job = first_job.model_copy(
        deep=True, update={"id": uuid4(), "inputs": GenerationInputs(prompt="second")}
    )

    first_route, first_submission = _submit(router, first_job)
    second_route, _ = _submit(router, second_job)

    assert first_route == "mock-video@account-a"
    assert second_route == "mock-video@account-b"
    first_job.provider = first_route
    first_job.provider_task_id = first_submission.provider_task_id
    asyncio.run(router.poll(first_job))
    assert account_a.polled == [first_submission.provider_task_id]
    assert account_b.polled == []
    assert asyncio.run(router.health()) == {"mock-video": True}


def test_account_concurrency_limit_spills_to_next_pool_member() -> None:
    class BlockingAccount(MockProviderAdapter):
        def __init__(
            self,
            account_id: str,
            *,
            entered: asyncio.Event | None = None,
            release: asyncio.Event | None = None,
        ) -> None:
            super().__init__(
                account_id=account_id,
                max_concurrency=1,
            )
            self.entered = entered
            self.release = release

        async def submit(self, job: GenerationJob) -> ProviderSubmission:
            if self.entered is not None and self.release is not None:
                self.entered.set()
                await self.release.wait()
            return ProviderSubmission(
                provider_task_id=f"{self.account_id}-{job.id}"
            )

    async def scenario() -> None:
        entered = asyncio.Event()
        release = asyncio.Event()
        account_a = BlockingAccount(
            "account-a", entered=entered, release=release
        )
        account_b = BlockingAccount("account-b")
        router = ProviderRouter([account_a, account_b])
        first_job = GenerationJob(
            tenant_id=uuid4(),
            model="mock.video.v1",
            mode=GenerationMode.TEXT_TO_VIDEO,
            inputs=GenerationInputs(prompt="first"),
            output=OutputOptions(),
        )
        second_job = first_job.model_copy(
            deep=True,
            update={
                "id": uuid4(),
                "inputs": GenerationInputs(prompt="second"),
            },
        )

        first_job = await _pin_capability_revision(router, first_job)
        second_job = second_job.model_copy(
            update={
                "expected_capability_revision": (
                    first_job.expected_capability_revision
                )
            }
        )

        first = asyncio.create_task(router.submit(first_job))
        await entered.wait()
        second_route, _ = await router.submit(second_job)
        assert second_route == "mock-video@account-b"
        release.set()
        first_route, _ = await first
        assert first_route == "mock-video@account-a"

    asyncio.run(scenario())


@pytest.mark.parametrize("value", ["", " task", "task\n", "x" * 257])
def test_provider_submission_rejects_invalid_task_identifiers(value: str) -> None:
    with pytest.raises(ValueError):
        ProviderSubmission(provider_task_id=value)


def test_contract_accepts_text_to_image_and_9_images_3_video_3_audio() -> None:
    assets = [
        AssetInput(
            url=f"https://assets.example.test/image-{index}.png",
            media_type="image",
        )
        for index in range(9)
    ]
    assets += [
        AssetInput(
            url=f"https://assets.example.test/video-{index}.mp4",
            media_type="video",
        )
        for index in range(3)
    ]
    assets += [
        AssetInput(
            url=f"https://assets.example.test/audio-{index}.mp3",
            media_type="audio",
        )
        for index in range(3)
    ]
    job = GenerationJob(
        tenant_id=uuid4(),
        model="mock.video.v1",
        mode=GenerationMode.TEXT_TO_IMAGE,
        inputs=GenerationInputs(prompt="Create a storyboard frame", assets=assets),
        output=OutputOptions(),
    )

    provider_name, _ = _submit(ProviderRouter([MockProviderAdapter()]), job)
    assert provider_name == "mock-video"


def test_three_channel_classes_share_one_capability_driven_router() -> None:
    class ClassifiedRoute(MockProviderAdapter):
        def __init__(
            self,
            *,
            name: str,
            channel_type: ProviderChannelType,
            mode: GenerationMode,
        ) -> None:
            super().__init__()
            self.name = name
            self.channel_type = channel_type
            self.mode = mode

        async def capabilities(self) -> list[ModelCapability]:
            image_count = (
                1 if self.mode == GenerationMode.IMAGE_TO_VIDEO else 0
            )
            return [
                ModelCapability(
                    model="unified.model.v1",
                    modes=[self.mode],
                    input_media_types=["image"] if image_count else [],
                    limits=CapabilityLimits(
                        max_prompt_length=2_000,
                        max_images=image_count,
                        max_videos=0,
                        max_audio=0,
                        duration_seconds=[5],
                        aspect_ratios=["16:9"],
                        resolutions=["720p"],
                        output_counts=[1],
                    ),
                    available_providers=[self.name],
                )
            ]

    reverse = ClassifiedRoute(
        name="company-reverse",
        channel_type=ProviderChannelType.REVERSE,
        mode=GenerationMode.TEXT_TO_IMAGE,
    )
    aggregator = ClassifiedRoute(
        name="api-aggregator",
        channel_type=ProviderChannelType.THIRD_PARTY_API,
        mode=GenerationMode.IMAGE_TO_VIDEO,
    )
    official = ClassifiedRoute(
        name="official-cloud",
        channel_type=ProviderChannelType.OFFICIAL,
        mode=GenerationMode.TEXT_TO_VIDEO,
    )
    router = ProviderRouter([reverse, aggregator, official])

    async def submit(mode: GenerationMode) -> str:
        assets = (
            [
                AssetInput(
                    url="https://assets.example.test/input.png",
                    media_type="image",
                )
            ]
            if mode == GenerationMode.IMAGE_TO_VIDEO
            else []
        )
        job = GenerationJob(
            tenant_id=uuid4(),
            model="unified.model.v1",
            mode=mode,
            inputs=GenerationInputs(prompt="test", assets=assets),
            output=OutputOptions(),
        )
        job = await _pin_capability_revision(router, job)
        route_id, _ = await router.submit(job)
        return route_id

    async def scenario() -> None:
        profiles = await router.route_profiles()
        assert {profile.manifest.channel_type for profile in profiles} == {
            ProviderChannelType.REVERSE,
            ProviderChannelType.THIRD_PARTY_API,
            ProviderChannelType.OFFICIAL,
        }
        assert await submit(GenerationMode.TEXT_TO_IMAGE) == "company-reverse"
        assert await submit(GenerationMode.IMAGE_TO_VIDEO) == "api-aggregator"
        assert await submit(GenerationMode.TEXT_TO_VIDEO) == "official-cloud"

    asyncio.run(scenario())


def test_retryable_failure_can_fail_over_across_channel_classes() -> None:
    reverse = MockProviderAdapter(fail_submit=True, priority=10)
    reverse.name = "reverse-primary"
    reverse.channel_type = ProviderChannelType.REVERSE
    aggregator = MockProviderAdapter(priority=20)
    aggregator.name = "third-party-backup"
    aggregator.channel_type = ProviderChannelType.THIRD_PARTY_API
    official = MockProviderAdapter(priority=30)
    official.name = "official-last-resort"
    official.channel_type = ProviderChannelType.OFFICIAL
    router = ProviderRouter([reverse, aggregator, official])
    job = GenerationJob(
        tenant_id=uuid4(),
        model="mock.video.v1",
        mode=GenerationMode.TEXT_TO_VIDEO,
        inputs=GenerationInputs(prompt="test"),
        output=OutputOptions(),
    )

    route_id, _ = _submit(router, job)

    assert route_id == "third-party-backup"


def test_provider_plugins_cannot_receive_caller_routing_metadata() -> None:
    class InspectingProvider(MockProviderAdapter):
        received: GenerationJob | None = None

        async def submit(self, job: GenerationJob) -> ProviderSubmission:
            self.received = job
            return await super().submit(job)

    provider = InspectingProvider()
    router = ProviderRouter([provider])
    job = GenerationJob(
        tenant_id=uuid4(),
        source_client_id="customer-platform",
        client_reference_id="platform-task-123",
        model="mock.video.v1",
        mode=GenerationMode.TEXT_TO_VIDEO,
        inputs=GenerationInputs(prompt="test"),
        output=OutputOptions(),
        metadata={
            "alibaba_wan": {"watermark": True},
            "business_label": "campaign-a",
        },
        callback_url="https://platform.example.test/callback",
    )

    _submit(router, job)

    assert provider.received is not None
    assert provider.received is not job
    assert provider.received.source_client_id is None
    assert provider.received.client_reference_id is None
    assert provider.received.metadata == {}
    assert provider.received.callback_url is None
    assert job.metadata["business_label"] == "campaign-a"


def test_adapter_contract_rejects_duplicate_model_mode_declarations() -> None:
    class DuplicateCapabilityRoute(MockProviderAdapter):
        async def capabilities(self) -> list[ModelCapability]:
            capability = (await super().capabilities())[0]
            return [capability, capability.model_copy(deep=True)]

    with pytest.raises(ProviderContractError, match="more than once"):
        asyncio.run(
            ProviderRouter(
                [DuplicateCapabilityRoute()]
            ).validate_configuration()
        )


def test_adapter_contract_revalidates_mutated_capability_objects() -> None:
    class ContradictoryCapabilityRoute(MockProviderAdapter):
        async def capabilities(self) -> list[ModelCapability]:
            capability = (await super().capabilities())[0]
            capability.input_media_types = []
            return [capability]

    with pytest.raises(ProviderContractError, match="invalid model capability"):
        asyncio.run(
            ProviderRouter(
                [ContradictoryCapabilityRoute()]
            ).validate_configuration()
        )


def test_healthcheck_exception_isolated_to_failing_route() -> None:
    class BrokenHealthRoute(MockProviderAdapter):
        async def healthcheck(self) -> bool:
            raise RuntimeError("secret-bearing transport failure")

    broken = BrokenHealthRoute(priority=10)
    broken.name = "broken-health"
    fallback = MockProviderAdapter(priority=20)
    fallback.name = "healthy-fallback"
    router = ProviderRouter([broken, fallback], failure_threshold=1)
    job = GenerationJob(
        tenant_id=uuid4(),
        model="mock.video.v1",
        mode=GenerationMode.TEXT_TO_VIDEO,
        inputs=GenerationInputs(prompt="test"),
        output=OutputOptions(),
    )

    route_id, _ = _submit(router, job)

    assert route_id == "healthy-fallback"


def test_hung_healthcheck_is_bounded_and_falls_back() -> None:
    class HungHealthRoute(MockProviderAdapter):
        async def healthcheck(self) -> bool:
            await asyncio.Event().wait()
            return True

    hung = HungHealthRoute(priority=10)
    hung.name = "hung-health"
    fallback = MockProviderAdapter(priority=20)
    fallback.name = "bounded-fallback"
    router = ProviderRouter(
        [hung, fallback],
        failure_threshold=1,
        healthcheck_timeout_seconds=0.01,
    )
    job = GenerationJob(
        tenant_id=uuid4(),
        model="mock.video.v1",
        mode=GenerationMode.TEXT_TO_VIDEO,
        inputs=GenerationInputs(prompt="test"),
        output=OutputOptions(),
    )

    route_id, _ = _submit(router, job)

    assert route_id == "bounded-fallback"


@pytest.mark.parametrize(
    ("mutation", "expected_message"),
    [
        ("prompt", "Prompt"),
        ("type", "Input type"),
        ("image_count", "Image input count"),
        ("duration", "Duration"),
        ("aspect_ratio", "Aspect ratio"),
        ("resolution", "Resolution"),
        ("count", "Output count"),
    ],
)
def test_router_rejects_requests_outside_model_capability(
    mutation: str, expected_message: str
) -> None:
    class LimitedMock(MockProviderAdapter):
        async def capabilities(self):
            capabilities = await super().capabilities()
            capability = capabilities[0]
            capability.modes = [GenerationMode.TEXT_TO_VIDEO]
            capability.input_media_types = ["image"]
            capability.limits.max_prompt_length = 4
            capability.limits.max_images = 1
            capability.limits.max_videos = 0
            capability.limits.max_audio = 0
            capability.limits.duration_seconds = [5]
            capability.limits.aspect_ratios = ["16:9"]
            capability.limits.resolutions = ["720p"]
            capability.limits.output_counts = [1]
            return capabilities

    prompt = "okay"
    assets = []
    output = OutputOptions()
    if mutation == "prompt":
        prompt = "too long"
    elif mutation == "type":
        assets = [
            AssetInput(
                url="https://assets.example.test/input.mp3",
                media_type="audio",
            )
        ]
    elif mutation == "image_count":
        assets = [
            AssetInput(
                url=f"https://assets.example.test/{index}.png",
                media_type="image",
            )
            for index in range(2)
        ]
    elif mutation == "duration":
        output.duration_seconds = 6
    elif mutation == "aspect_ratio":
        output.aspect_ratio = "1:1"
    elif mutation == "resolution":
        output.resolution = "1080p"
    elif mutation == "count":
        output.count = 2
    job = GenerationJob(
        tenant_id=uuid4(),
        model="mock.video.v1",
        mode=GenerationMode.TEXT_TO_VIDEO,
        inputs=GenerationInputs(prompt=prompt, assets=assets),
        output=output,
    )

    with pytest.raises(ProviderError) as error:
        _submit(ProviderRouter([LimitedMock()]), job)
    assert error.value.code == "REQUEST_NOT_SUPPORTED_BY_MODEL"
    assert expected_message in error.value.message
