package controller

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"image"
	"image/color"
	"image/draw"
	"image/png"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync"
	"time"

	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/generationprofile"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/relay"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	relayservice "github.com/QuantumNous/new-api/service"
	"github.com/gin-gonic/gin"
)

const (
	platformGenerationRouteTestTimeout      = 10 * time.Minute
	platformGenerationRouteTestPollInterval = 2 * time.Second
	platformGenerationRouteTestBodyLimit    = 1 << 20
)

var errPlatformGenerationRouteSubmissionUnknown = errors.New("provider submission outcome is unknown")

// platformGenerationRouteTestError deliberately carries only a closed receipt
// code. Its Error string must remain safe to log: raw provider bodies,
// provider messages, URLs, headers and credentials are never attached.
type platformGenerationRouteTestError struct {
	receiptCode        string
	submissionUnknown  bool
	submissionRejected bool
	pollPending        bool
	providerTerminal   bool
}

func (err *platformGenerationRouteTestError) Error() string {
	return "route-bound generation acceptance failed: " + err.receiptCode
}

func (err *platformGenerationRouteTestError) Unwrap() error {
	if err != nil && err.submissionUnknown {
		return errPlatformGenerationRouteSubmissionUnknown
	}
	return nil
}

func newPlatformGenerationRouteTestError(receiptCode string, submissionUnknown bool) error {
	if !model.IsPlatformChannelControlTestErrorCode(receiptCode) {
		receiptCode = model.PlatformChannelControlErrorTestFailed
	}
	return &platformGenerationRouteTestError{receiptCode: receiptCode, submissionUnknown: submissionUnknown}
}

func newPlatformGenerationRouteTestRejectedError(receiptCode string) error {
	if !model.IsPlatformChannelControlTestErrorCode(receiptCode) {
		receiptCode = model.PlatformChannelControlErrorTestFailed
	}
	return &platformGenerationRouteTestError{receiptCode: receiptCode, submissionRejected: true}
}

func newPlatformGenerationRouteTestPollPendingError(receiptCode string) error {
	if !model.IsPlatformChannelControlTestErrorCode(receiptCode) {
		receiptCode = model.PlatformChannelControlErrorTestUnavailable
	}
	return &platformGenerationRouteTestError{receiptCode: receiptCode, pollPending: true}
}

func newPlatformGenerationRouteTestProviderTerminalError(receiptCode string) error {
	if !model.IsPlatformChannelControlTestErrorCode(receiptCode) {
		receiptCode = model.PlatformChannelControlErrorTestTerminal
	}
	return &platformGenerationRouteTestError{receiptCode: receiptCode, providerTerminal: true}
}

func platformGenerationRouteTestReceiptCode(err error) string {
	var classified *platformGenerationRouteTestError
	if errors.As(err, &classified) && model.IsPlatformChannelControlTestErrorCode(classified.receiptCode) {
		return classified.receiptCode
	}
	return model.PlatformChannelControlErrorTestFailed
}

type platformGenerationTaskContextFetcher interface {
	FetchTaskWithContext(context.Context, string, string, map[string]any, string) (*http.Response, error)
}

type platformGenerationImmediateTerminalResult interface {
	ImmediateTerminalTaskResult() *relaycommon.TaskInfo
}

type platformGenerationNoRedirectSubmitter interface {
	DoRequestNoRedirect(*gin.Context, *relaycommon.RelayInfo, io.Reader) (*http.Response, error)
}

type platformGenerationRouteTestPlan struct {
	Profile      generationprofile.Profile
	Mode         string
	Artifact     generationprofile.ArtifactContract
	Asynchronous bool
	Prompt       string
	Resolution   string
	AspectRatio  string
	Duration     int
	Images       []string
}

var (
	platformGenerationRouteTestPNGOnce sync.Once
	platformGenerationRouteTestPNG     string
	platformGenerationRouteTestPNGErr  error
)

func platformGenerationRouteTestInlinePNG() (string, error) {
	platformGenerationRouteTestPNGOnce.Do(func() {
		canvas := image.NewRGBA(image.Rect(0, 0, 512, 512))
		draw.Draw(canvas, canvas.Bounds(), &image.Uniform{C: color.RGBA{R: 242, G: 244, B: 241, A: 255}}, image.Point{}, draw.Src)
		draw.Draw(canvas, image.Rect(96, 144, 416, 368), &image.Uniform{C: color.RGBA{R: 8, G: 123, B: 128, A: 255}}, image.Point{}, draw.Src)
		draw.Draw(canvas, image.Rect(176, 208, 336, 304), &image.Uniform{C: color.RGBA{R: 255, G: 255, B: 255, A: 255}}, image.Point{}, draw.Src)
		var encoded bytes.Buffer
		if err := png.Encode(&encoded, canvas); err != nil {
			platformGenerationRouteTestPNGErr = err
			return
		}
		platformGenerationRouteTestPNG = "data:image/png;base64," + base64.StdEncoding.EncodeToString(encoded.Bytes())
	})
	if platformGenerationRouteTestPNGErr != nil || platformGenerationRouteTestPNG == "" {
		return "", fmt.Errorf("route acceptance image fixture is unavailable")
	}
	return platformGenerationRouteTestPNG, nil
}

// resolvePlatformGenerationRouteTestPlan selects a self-contained probe only
// from the immutable adapter-profile revision pinned by the route.  Public or
// provider model strings cannot switch protocol, mode, output shape or media
// type after the exact route binding has been resolved.
func resolvePlatformGenerationRouteTestPlan(binding *relayservice.PlatformGenerationRouteTestBinding) (platformGenerationRouteTestPlan, error) {
	if binding == nil {
		return platformGenerationRouteTestPlan{}, fmt.Errorf("route adapter profile release is unavailable")
	}
	profile, ok := generationprofile.Get(binding.CapabilityProfileID)
	if !ok || profile.Revision != binding.CapabilityProfileRevision || profile.NativeChannelType != binding.NativeChannelType {
		return platformGenerationRouteTestPlan{}, fmt.Errorf("route adapter profile release is unavailable")
	}
	modeName := binding.Mode
	if modeName == "" {
		modeName, ok = profile.AcceptanceTestMode()
	}
	if modeName == "" || !containsPlatformGenerationRouteTestString(profile.AcceptanceTestModes(), modeName) {
		return platformGenerationRouteTestPlan{}, fmt.Errorf("route adapter profile has no safe acceptance mode")
	}
	mode, ok := profile.Capability.Modes[modeName]
	if !ok {
		return platformGenerationRouteTestPlan{}, fmt.Errorf("route adapter profile acceptance mode is unavailable")
	}
	artifact, ok := profile.Artifact(modeName)
	if !ok || artifact.Count != 1 || !containsPlatformGenerationRouteTestInt(mode.Limits.OutputCounts, artifact.Count) {
		return platformGenerationRouteTestPlan{}, fmt.Errorf("route adapter profile has no single-output acceptance contract")
	}

	plan := platformGenerationRouteTestPlan{
		Profile:  profile,
		Mode:     modeName,
		Artifact: artifact,
	}
	switch profile.Protocol {
	case generationprofile.VolcengineArkImageProtocolV1:
		if modeName != "text_to_image" || profile.Image == nil ||
			artifact.MediaType != "image" || artifact.ContentType != "image/png" ||
			artifact.Width <= 0 || artifact.Height <= 0 {
			return platformGenerationRouteTestPlan{}, fmt.Errorf("route image acceptance contract is unavailable")
		}
		resolution, found := preferredPlatformGenerationRouteTestString(mode.Limits.Resolutions, profile.Image.Size)
		if !found || resolution != profile.Image.Size {
			return platformGenerationRouteTestPlan{}, fmt.Errorf("route image acceptance resolution is unavailable")
		}
		aspectRatio, found := preferredPlatformGenerationRouteTestString(mode.Limits.AspectRatios, "1:1")
		if !found {
			return platformGenerationRouteTestPlan{}, fmt.Errorf("route image acceptance aspect ratio is unavailable")
		}
		plan.Prompt = "A white ceramic cup on a plain studio table in soft daylight, no people, no text."
		plan.Resolution = resolution
		plan.AspectRatio = aspectRatio
		plan.Duration = 0
		plan.Asynchronous = false
	case generationprofile.VolcengineArkVideoProtocolV1,
		generationprofile.MiniMaxH3VideoProtocolV2,
		generationprofile.GoogleGeminiInteractionsVideoProtocolV1,
		generationprofile.GoogleGeminiVeoVideoProtocolV1,
		generationprofile.GoogleVertexVeoVideoProtocolV1:
		if (modeName != "text_to_video" && modeName != "image_to_video") ||
			artifact.MediaType != "video" || artifact.ContentType != "video/mp4" {
			return platformGenerationRouteTestPlan{}, fmt.Errorf("route video acceptance contract is unavailable")
		}
		duration, found := preferredPlatformGenerationRouteTestInt(mode.Limits.DurationSeconds, 4)
		if !found {
			return platformGenerationRouteTestPlan{}, fmt.Errorf("route video acceptance duration is unavailable")
		}
		resolution, found := preferredPlatformGenerationRouteTestString(mode.Limits.Resolutions, "720p")
		if !found {
			return platformGenerationRouteTestPlan{}, fmt.Errorf("route video acceptance resolution is unavailable")
		}
		aspectRatio, found := preferredPlatformGenerationRouteTestString(mode.Limits.AspectRatios, "16:9")
		if !found {
			return platformGenerationRouteTestPlan{}, fmt.Errorf("route video acceptance aspect ratio is unavailable")
		}
		plan.Prompt = "A white ceramic cup rests on a plain studio table in soft daylight. One slow continuous camera move, no people, no text."
		plan.Resolution = resolution
		plan.AspectRatio = aspectRatio
		plan.Duration = duration
		plan.Asynchronous = true
		if modeName == "image_to_video" {
			if mode.Limits.MaxImages < 1 || !containsPlatformGenerationRouteTestString(mode.InputMediaTypes, "image") {
				return platformGenerationRouteTestPlan{}, fmt.Errorf("route image-to-video acceptance input is unavailable")
			}
			fixture, fixtureErr := platformGenerationRouteTestInlinePNG()
			if fixtureErr != nil {
				return platformGenerationRouteTestPlan{}, fixtureErr
			}
			plan.Images = []string{fixture}
		}
	default:
		return platformGenerationRouteTestPlan{}, fmt.Errorf("route adapter profile protocol is unsupported")
	}
	return plan, nil
}

func executePlatformGenerationRouteChannelTest(
	ctx context.Context,
	providerChannel *model.Channel,
	binding *relayservice.PlatformGenerationRouteTestBinding,
) error {
	return runPlatformGenerationRouteChannelTest(
		ctx,
		providerChannel,
		binding,
		platformGenerationRouteTestPollInterval,
		platformGenerationRouteTestTimeout,
	)
}

var verifyPlatformGenerationRouteTestArtifact = relayservice.VerifyPlatformRouteTestArtifactForContract
var verifyPlatformGoogleGenerationRouteTestArtifact = relayservice.VerifyPlatformGoogleRouteTestArtifactForContract

// runPlatformGenerationRouteChannelTest performs one exact provider submit
// followed by sticky polling on the same channel, upstream model and key.
// A transport failure after the POST is deliberately returned as unknown and
// the submit is never replayed or failed over to a different route.
func runPlatformGenerationRouteChannelTest(
	parent context.Context,
	providerChannel *model.Channel,
	binding *relayservice.PlatformGenerationRouteTestBinding,
	pollInterval time.Duration,
	timeout time.Duration,
) error {
	evidence := model.PlatformChannelTestArtifactEvidence{}
	return runPlatformGenerationRouteChannelTestDurable(
		parent, providerChannel, binding, pollInterval, timeout, "", nil, &evidence,
	)
}

func runPlatformGenerationRouteChannelTestDurable(
	parent context.Context,
	providerChannel *model.Channel,
	binding *relayservice.PlatformGenerationRouteTestBinding,
	pollInterval time.Duration,
	timeout time.Duration,
	existingProviderTaskID string,
	recordSubmitted func(string) error,
	evidence *model.PlatformChannelTestArtifactEvidence,
) error {
	if parent == nil {
		parent = context.Background()
	}
	if providerChannel == nil || binding == nil || providerChannel.Id != binding.ChannelID ||
		providerChannel.Type != binding.NativeChannelType || strings.TrimSpace(providerChannel.Key) == "" {
		return fmt.Errorf("route-bound generation channel is unavailable")
	}
	if pollInterval <= 0 || timeout <= 0 {
		return fmt.Errorf("route-bound generation test timing is invalid")
	}
	plan, err := resolvePlatformGenerationRouteTestPlan(binding)
	if err != nil || plan.Profile.NativeChannelType != providerChannel.Type {
		return fmt.Errorf("route-bound generation adapter profile is unavailable")
	}

	request := relaycommon.TaskSubmitReq{
		Model:    binding.PublicModelID,
		Mode:     plan.Mode,
		Prompt:   plan.Prompt,
		Images:   append([]string(nil), plan.Images...),
		Duration: plan.Duration,
		Metadata: generationprofile.SnapshotMetadata(map[string]any{
			"platform_generation_mode": plan.Mode,
			"durationSeconds":          plan.Duration,
			"resolution":               plan.Resolution,
			"aspectRatio":              plan.AspectRatio,
			"sampleCount":              plan.Artifact.Count,
			"face_enabled":             false,
		}, plan.Profile),
	}
	if !plan.Asynchronous {
		// Capability v1 retains a duration sentinel for image requests, while
		// the immutable profile declares DurationSemanticsNone.  The provider
		// payload is driven by Size and never receives this compatibility value.
		request.Duration = 1
		request.Seconds = "1"
		request.Size = plan.Resolution
	}
	rawRequest, err := marshalPlatformGenerationRouteTestRequest(request)
	if err != nil {
		return err
	}

	ctx, cancel := context.WithTimeout(parent, timeout)
	defer cancel()
	recorder := httptest.NewRecorder()
	ginContext, _ := gin.CreateTestContext(recorder)
	ginContext.Request = httptest.NewRequestWithContext(ctx, http.MethodPost, "/v1/video/generations", bytes.NewReader(rawRequest))
	ginContext.Request.Header.Set("Content-Type", "application/json")

	info := &relaycommon.RelayInfo{
		StartTime:       time.Now(),
		OriginModelName: binding.PublicModelID,
		RequestURLPath:  "/v1/video/generations",
		IsChannelTest:   true,
		ForcePreConsume: false,
		UsePrice:        false,
		ChannelMeta: &relaycommon.ChannelMeta{
			ChannelType:          providerChannel.Type,
			ChannelId:            providerChannel.Id,
			ChannelIsMultiKey:    false,
			ChannelMultiKeyIndex: binding.KeyIndex,
			ChannelBaseUrl:       providerChannel.GetBaseURL(),
			ApiKey:               providerChannel.Key,
			ParamOverride:        providerChannel.GetParamOverride(),
			HeadersOverride:      providerChannel.GetHeaderOverride(),
			ChannelSetting:       providerChannel.GetSetting(),
			ChannelOtherSettings: providerChannel.GetOtherSettings(),
			UpstreamModelName:    binding.UpstreamModel,
			IsModelMapped:        true,
		},
		TaskRelayInfo: &relaycommon.TaskRelayInfo{
			PinnedProviderRoute: true,
			PublicTaskID:        "task_route_acceptance",
		},
	}
	adaptor := relay.GetTaskAdaptor(platformTaskForChannelType(providerChannel.Type))
	if adaptor == nil {
		return fmt.Errorf("route-bound generation adapter is unavailable")
	}
	adaptor.Init(info)
	if taskErr := adaptor.ValidateRequestAndSetAction(ginContext, info); taskErr != nil {
		return fmt.Errorf("route-bound generation request validation failed: %s", taskErr.Code)
	}
	protectedSubmitter, ok := adaptor.(platformGenerationNoRedirectSubmitter)
	if !ok {
		return fmt.Errorf("route-bound generation adapter does not support no-redirect submission")
	}
	providerTaskID := existingProviderTaskID
	var immediateResult *relaycommon.TaskInfo
	if providerTaskID != "" && strings.TrimSpace(providerTaskID) != providerTaskID {
		return fmt.Errorf("route-bound generation stored task identity is invalid")
	}
	if providerTaskID == "" {
		requestBody, err := adaptor.BuildRequestBody(ginContext, info)
		if err != nil {
			return fmt.Errorf("route-bound generation request conversion failed: %w", err)
		}

		// There is intentionally no submit retry loop around this provider POST.
		submitResponse, err := protectedSubmitter.DoRequestNoRedirect(ginContext, info, requestBody)
		if err != nil {
			return newPlatformGenerationRouteTestError(model.PlatformChannelControlErrorTestUnavailable, true)
		}
		if submitResponse == nil {
			return newPlatformGenerationRouteTestError(model.PlatformChannelControlErrorTestUnavailable, true)
		}
		if submitResponse.StatusCode < http.StatusOK || submitResponse.StatusCode >= http.StatusMultipleChoices {
			receiptCode := classifyPlatformGenerationRouteProviderHTTPFailure(submitResponse)
			if isDeterministicPlatformGenerationRouteProviderRejection(submitResponse.StatusCode) {
				return newPlatformGenerationRouteTestRejectedError(receiptCode)
			}
			// A provider or intermediary 5xx/redirect does not prove that the
			// provider failed before creating a task. Keep the durable claim in
			// submission_unknown so no retry can duplicate paid work.
			return newPlatformGenerationRouteTestError(receiptCode, true)
		}
		submittedTaskID, _, taskErr := adaptor.DoResponse(ginContext, submitResponse, info)
		if taskErr != nil || strings.TrimSpace(submittedTaskID) == "" {
			// A 2xx body without a trustworthy task id does not prove non-creation.
			return newPlatformGenerationRouteTestError(model.PlatformChannelControlErrorTestTerminal, true)
		}
		providerTaskID = submittedTaskID
		if recordSubmitted != nil {
			if err := recordSubmitted(providerTaskID); err != nil {
				return newPlatformGenerationRouteTestError(model.PlatformChannelControlErrorTestUnavailable, true)
			}
		}
		if !plan.Asynchronous {
			terminal, ok := adaptor.(platformGenerationImmediateTerminalResult)
			if !ok {
				return newPlatformGenerationRouteTestPollPendingError(model.PlatformChannelControlErrorTestTerminal)
			}
			immediateResult = terminal.ImmediateTerminalTaskResult()
			if immediateResult == nil {
				return newPlatformGenerationRouteTestPollPendingError(model.PlatformChannelControlErrorTestTerminal)
			}
		}
	}

	if !plan.Asynchronous {
		// A synchronous provider response contains the only provider artifact
		// locator. If the process lost that response after persisting the task id,
		// fail closed and never repeat the paid POST merely to reconstruct it.
		if immediateResult == nil {
			return newPlatformGenerationRouteTestPollPendingError(model.PlatformChannelControlErrorTestArtifact)
		}
		return verifyPlatformGenerationRouteTerminalArtifact(ctx, immediateResult, providerTaskID, binding, plan, providerChannel, evidence)
	}

	contextFetcher, ok := adaptor.(platformGenerationTaskContextFetcher)
	if !ok {
		return fmt.Errorf("route-bound generation adapter does not support cancellable polling")
	}
	proxy := providerChannel.GetSetting().Proxy
	for {
		if err := waitForPlatformGenerationRoutePoll(ctx, pollInterval); err != nil {
			return newPlatformGenerationRouteTestPollPendingError(model.PlatformChannelControlErrorTestUnavailable)
		}
		response, err := contextFetcher.FetchTaskWithContext(ctx, providerChannel.GetBaseURL(), providerChannel.Key, map[string]any{
			"task_id":        providerTaskID,
			"action":         info.Action,
			"provider_model": binding.UpstreamModel,
		}, proxy)
		if err != nil {
			return newPlatformGenerationRouteTestPollPendingError(model.PlatformChannelControlErrorTestUnavailable)
		}
		if response == nil || response.Body == nil {
			return newPlatformGenerationRouteTestPollPendingError(model.PlatformChannelControlErrorTestUnavailable)
		}
		if response.StatusCode < http.StatusOK || response.StatusCode >= http.StatusMultipleChoices {
			return newPlatformGenerationRouteTestPollPendingError(
				classifyPlatformGenerationRouteProviderHTTPFailure(response),
			)
		}
		body, readErr := readPlatformGenerationRouteTestBody(response)
		if readErr != nil {
			return newPlatformGenerationRouteTestPollPendingError(model.PlatformChannelControlErrorTestTerminal)
		}
		result, parseErr := adaptor.ParseTaskResult(body)
		if parseErr != nil || result == nil {
			return newPlatformGenerationRouteTestPollPendingError(model.PlatformChannelControlErrorTestTerminal)
		}
		if err := validatePlatformGenerationRoutePollIdentityProof(result, providerTaskID, binding, plan.Profile); err != nil {
			return newPlatformGenerationRouteTestPollPendingError(model.PlatformChannelControlErrorTestTerminal)
		}
		switch model.TaskStatus(result.Status) {
		case model.TaskStatusQueued, model.TaskStatusInProgress, model.TaskStatusSubmitted, model.TaskStatusNotStart:
			continue
		case model.TaskStatusFailure:
			return newPlatformGenerationRouteTestProviderTerminalError(
				classifyPlatformGenerationRouteProviderTerminalFailure(result),
			)
		case model.TaskStatusSuccess:
			return verifyPlatformGenerationRouteTerminalArtifact(ctx, result, providerTaskID, binding, plan, providerChannel, evidence)
		default:
			return newPlatformGenerationRouteTestPollPendingError(model.PlatformChannelControlErrorTestTerminal)
		}
	}
}

func verifyPlatformGenerationRouteTerminalArtifact(
	ctx context.Context,
	result *relaycommon.TaskInfo,
	providerTaskID string,
	binding *relayservice.PlatformGenerationRouteTestBinding,
	plan platformGenerationRouteTestPlan,
	providerChannel *model.Channel,
	evidence *model.PlatformChannelTestArtifactEvidence,
) error {
	// Validate the provider artifact locator before the output-shape proof.
	// Adaptors intentionally omit output evidence when the locator is unsafe;
	// classify that as an artifact blocker while keeping the created task sticky.
	if result == nil || validatePlatformGenerationRouteArtifactURL(result.Url) != nil {
		return newPlatformGenerationRouteTestPollPendingError(model.PlatformChannelControlErrorTestArtifact)
	}
	if err := validatePlatformGenerationRouteSuccessProof(result, providerTaskID, binding, plan); err != nil {
		// The provider already accepted and completed this exact task. A proof
		// mismatch is an acceptance blocker, never evidence of non-creation.
		return newPlatformGenerationRouteTestPollPendingError(model.PlatformChannelControlErrorTestTerminal)
	}
	var verified model.PlatformChannelTestArtifactEvidence
	var err error
	switch plan.Profile.Protocol {
	case generationprofile.GoogleGeminiInteractionsVideoProtocolV1, generationprofile.GoogleGeminiVeoVideoProtocolV1:
		if providerChannel == nil || providerChannel.Type != constant.ChannelTypeGemini ||
			strings.TrimSpace(providerChannel.Key) == "" {
			return newPlatformGenerationRouteTestPollPendingError(model.PlatformChannelControlErrorTestArtifact)
		}
		verified, err = verifyPlatformGoogleGenerationRouteTestArtifact(
			ctx,
			result.Url,
			plan.Artifact,
			providerChannel.Key,
		)
	default:
		verified, err = verifyPlatformGenerationRouteTestArtifact(ctx, result.Url, plan.Artifact)
	}
	if err != nil || evidence == nil ||
		verified.ContentType != plan.Artifact.ContentType ||
		verified.SizeBytes <= 0 || len(verified.SHA256) != 64 {
		return newPlatformGenerationRouteTestPollPendingError(model.PlatformChannelControlErrorTestArtifact)
	}
	*evidence = verified
	return nil
}

func isDeterministicPlatformGenerationRouteProviderRejection(statusCode int) bool {
	// A received client-error response proves rejection, except 408: an
	// intermediary can synthesize Request Timeout after the provider has
	// accepted bytes, so creation remains ambiguous. Provider/intermediary 5xx
	// and redirects are likewise never treated as proof of non-creation.
	return statusCode >= http.StatusBadRequest && statusCode < http.StatusInternalServerError &&
		statusCode != http.StatusRequestTimeout
}

// validatePlatformGenerationRoutePollIdentityProof runs before every status
// transition. A mismatched queued response must not be allowed to consume the
// probe timeout, and a mismatched failure must not be classified as evidence
// about the selected route.
func validatePlatformGenerationRoutePollIdentityProof(
	result *relaycommon.TaskInfo,
	providerTaskID string,
	binding *relayservice.PlatformGenerationRouteTestBinding,
	profile generationprofile.Profile,
) error {
	if result == nil || binding == nil || providerTaskID == "" || result.TaskID != providerTaskID {
		return fmt.Errorf("route-bound generation poll identity is inconsistent")
	}
	proof := result.ProviderResultProof
	if proof == nil || proof.SchemaVersion != 1 || proof.Protocol != profile.Protocol ||
		proof.TaskID != providerTaskID || proof.Model != binding.UpstreamModel {
		return fmt.Errorf("route-bound generation poll proof is inconsistent")
	}
	switch model.TaskStatus(result.Status) {
	case model.TaskStatusQueued:
		if (proof.ProviderStatus != "pending" && proof.ProviderStatus != "queued") || result.Progress != "10%" {
			return fmt.Errorf("route-bound generation queued proof is inconsistent")
		}
	case model.TaskStatusInProgress:
		if (proof.ProviderStatus != "processing" && proof.ProviderStatus != "running") || result.Progress != "50%" {
			return fmt.Errorf("route-bound generation in-progress proof is inconsistent")
		}
	case model.TaskStatusFailure:
		if (proof.ProviderStatus != "failed" && proof.ProviderStatus != "cancelled" && proof.ProviderStatus != "expired") ||
			result.Progress != "100%" {
			return fmt.Errorf("route-bound generation failure proof is inconsistent")
		}
	case model.TaskStatusSuccess:
		if proof.ProviderStatus != "succeeded" || result.Progress != "100%" {
			return fmt.Errorf("route-bound generation success proof is inconsistent")
		}
	default:
		return fmt.Errorf("route-bound generation poll status is unsupported")
	}
	return nil
}

// validatePlatformGenerationRouteSuccessProof binds the provider terminal to
// the exact request used by this acceptance probe. A valid HTTPS artifact by
// itself is insufficient: accepting a result from another task, model, output
// shape or adapter protocol would incorrectly attest the selected route.
func validatePlatformGenerationRouteSuccessProof(
	result *relaycommon.TaskInfo,
	providerTaskID string,
	binding *relayservice.PlatformGenerationRouteTestBinding,
	plan platformGenerationRouteTestPlan,
) error {
	if err := validatePlatformGenerationRoutePollIdentityProof(result, providerTaskID, binding, plan.Profile); err != nil {
		return err
	}
	if result.Status != string(model.TaskStatusSuccess) {
		return fmt.Errorf("route-bound generation success status is inconsistent")
	}
	artifact, ok := plan.Profile.Artifact(plan.Mode)
	if !ok || artifact != plan.Artifact || artifact.Count != 1 ||
		(artifact.MediaType != "image" && artifact.MediaType != "video") {
		return fmt.Errorf("route-bound generation artifact contract is unavailable")
	}
	proof := result.ProviderResultProof
	if proof == nil || proof.SchemaVersion != 1 ||
		proof.Protocol != plan.Profile.Protocol || proof.TaskID != providerTaskID ||
		proof.Model != binding.UpstreamModel || proof.ProviderStatus != "succeeded" ||
		proof.Resolution != plan.Resolution || proof.DurationSeconds != plan.Duration ||
		proof.AspectRatio != plan.AspectRatio || proof.FramesPresent ||
		proof.OutputCount != artifact.Count || proof.MediaType != artifact.MediaType ||
		proof.FailureOwner != "" || proof.FailureCode != "" {
		return fmt.Errorf("route-bound generation provider proof is inconsistent")
	}
	return nil
}

func classifyPlatformGenerationRouteProviderTerminalFailure(result *relaycommon.TaskInfo) string {
	if result == nil || result.ProviderResultProof == nil {
		return model.PlatformChannelControlErrorTestTerminal
	}
	switch result.ProviderResultProof.FailureCode {
	case "provider_validation_failed", "content_policy_rejected":
		return model.PlatformChannelControlErrorTestValidation
	case "provider_authentication_failed":
		return model.PlatformChannelControlErrorTestAuth
	case "provider_quota_exhausted":
		return model.PlatformChannelControlErrorTestQuota
	default:
		return model.PlatformChannelControlErrorTestTerminal
	}
}

// classifyPlatformGenerationRouteProviderHTTPFailure reads at most the probe
// body limit, extracts only an error code, and immediately discards the body.
// The provider code itself is never returned or persisted.
func classifyPlatformGenerationRouteProviderHTTPFailure(response *http.Response) string {
	if response == nil {
		return model.PlatformChannelControlErrorTestUnavailable
	}
	statusCode := response.StatusCode
	providerCode := ""
	if response.Body != nil {
		body, _ := io.ReadAll(io.LimitReader(response.Body, platformGenerationRouteTestBodyLimit+1))
		_ = response.Body.Close()
		if len(body) <= platformGenerationRouteTestBodyLimit {
			var envelope struct {
				Code  string `json:"code"`
				Error struct {
					Code string `json:"code"`
				} `json:"error"`
			}
			if json.Unmarshal(body, &envelope) == nil {
				providerCode = envelope.Error.Code
				if strings.TrimSpace(providerCode) == "" {
					providerCode = envelope.Code
				}
			}
		}
	}

	normalized := normalizePlatformGenerationRouteProviderCode(providerCode)
	switch {
	case containsPlatformGenerationRouteProviderCode(normalized,
		"unauthorized", "authentication", "invalidapikey", "invalidaccesskey", "accessdenied", "permissiondenied", "forbidden"):
		return model.PlatformChannelControlErrorTestAuth
	case containsPlatformGenerationRouteProviderCode(normalized,
		"quota", "setlimitexceeded", "insufficientbalance", "insufficientcredit", "ratelimit", "toomanyrequests", "resourceexhausted"):
		return model.PlatformChannelControlErrorTestQuota
	case containsPlatformGenerationRouteProviderCode(normalized,
		"invalidparameter", "invalidrequest", "invalidmodel", "modelnotfound", "badrequest", "validation", "contentpolicy", "invalidprompt"):
		return model.PlatformChannelControlErrorTestValidation
	case statusCode == http.StatusUnauthorized || statusCode == http.StatusForbidden:
		return model.PlatformChannelControlErrorTestAuth
	case statusCode == http.StatusPaymentRequired || statusCode == http.StatusTooManyRequests:
		return model.PlatformChannelControlErrorTestQuota
	case statusCode == http.StatusBadRequest || statusCode == http.StatusNotFound ||
		statusCode == http.StatusConflict || statusCode == http.StatusUnprocessableEntity:
		return model.PlatformChannelControlErrorTestValidation
	default:
		return model.PlatformChannelControlErrorTestTerminal
	}
}

func normalizePlatformGenerationRouteProviderCode(value string) string {
	var normalized strings.Builder
	for _, character := range strings.ToLower(strings.TrimSpace(value)) {
		if character >= 'a' && character <= 'z' || character >= '0' && character <= '9' {
			normalized.WriteRune(character)
		}
	}
	return normalized.String()
}

func containsPlatformGenerationRouteProviderCode(value string, candidates ...string) bool {
	for _, candidate := range candidates {
		if strings.Contains(value, candidate) {
			return true
		}
	}
	return false
}

func platformTaskForChannelType(channelType int) constant.TaskPlatform {
	return constant.TaskPlatform(fmt.Sprintf("%d", channelType))
}

func marshalPlatformGenerationRouteTestRequest(request relaycommon.TaskSubmitReq) ([]byte, error) {
	return json.Marshal(request)
}

func preferredPlatformGenerationRouteTestInt(values []int, preferred int) (int, bool) {
	for _, value := range values {
		if value == preferred {
			return value, true
		}
	}
	if len(values) == 0 {
		return 0, false
	}
	return values[0], true
}

func preferredPlatformGenerationRouteTestString(values []string, preferred string) (string, bool) {
	for _, value := range values {
		if value == preferred {
			return value, true
		}
	}
	if len(values) == 0 {
		return "", false
	}
	return values[0], true
}

func containsPlatformGenerationRouteTestInt(values []int, expected int) bool {
	for _, value := range values {
		if value == expected {
			return true
		}
	}
	return false
}

func containsPlatformGenerationRouteTestString(values []string, expected string) bool {
	for _, value := range values {
		if value == expected {
			return true
		}
	}
	return false
}

func waitForPlatformGenerationRoutePoll(ctx context.Context, interval time.Duration) error {
	timer := time.NewTimer(interval)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}

func readPlatformGenerationRouteTestBody(response *http.Response) ([]byte, error) {
	if response == nil || response.Body == nil {
		return nil, fmt.Errorf("route-bound generation poll returned an empty response")
	}
	defer response.Body.Close()
	if response.StatusCode < http.StatusOK || response.StatusCode >= http.StatusMultipleChoices {
		return nil, fmt.Errorf("route-bound generation poll was rejected")
	}
	body, err := io.ReadAll(io.LimitReader(response.Body, platformGenerationRouteTestBodyLimit+1))
	if err != nil {
		return nil, fmt.Errorf("route-bound generation poll could not be read: %w", err)
	}
	if len(body) > platformGenerationRouteTestBodyLimit {
		return nil, fmt.Errorf("route-bound generation poll response exceeds the limit")
	}
	return body, nil
}

func validatePlatformGenerationRouteArtifactURL(raw string) error {
	parsed, err := url.Parse(strings.TrimSpace(raw))
	if err != nil || parsed.Scheme != "https" || parsed.Host == "" || parsed.User != nil || parsed.Fragment != "" {
		return fmt.Errorf("route-bound generation success did not provide an absolute HTTPS artifact URL")
	}
	return nil
}
