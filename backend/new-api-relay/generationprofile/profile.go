// Package generationprofile owns immutable, code-reviewed capability profiles
// for provider generation adapters. Route configuration may select one of
// these profiles and narrow its public capability, but it cannot advertise a
// field that the adapter and artifact verifier do not preserve.
package generationprofile

import (
	"crypto/sha256"
	"fmt"
	"regexp"
	"sort"
	"strings"
	"unicode/utf8"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/dto"
)

const (
	// VolcengineArkImageProtocolV1 is the synchronous Ark image-generation
	// protocol used by reusable image adapter profiles.  Keeping the protocol
	// identity here lets route acceptance select its probe from the immutable
	// profile instead of from a caller-supplied model name.
	VolcengineArkImageProtocolV1 = "volcengine-ark-images-generations"

	// VolcengineArkImageGenerationV1 is a reusable protocol/implementation
	// contract. It intentionally contains no public or provider model ID. A
	// signed generationrelease.Release binds concrete model identities to it.
	VolcengineArkImageGenerationV1 = "volcengine.ark.image-generation.v1"

	// Seedream50TextToImageV1 is the historical profile identifier kept only
	// so already accepted Seedream route documents continue to verify during
	// migration. New releases must select VolcengineArkImageGenerationV1.
	Seedream50TextToImageV1 = "volcengine.seedream-5.0.text-to-image.v1"

	MetadataProfileID       = "relay_capability_profile"
	MetadataProfileRevision = "relay_capability_profile_revision"
	// MetadataProfileSnapshot carries the complete immutable adapter contract
	// used when a job was accepted. Keeping the code-reviewed document beside
	// its digest lets a newer worker finish old jobs after a rolling profile
	// release without consulting wider/current route capability.
	MetadataProfileSnapshot = "relay_capability_profile_snapshot"
)

// DurationSemantics states whether duration_seconds is a real provider input.
// Capability v1 used [1] as an image-count sentinel. Profiles make that legacy
// transport detail explicit so image admission and provider requests no longer
// treat it as a model capability.
type DurationSemantics string

const (
	DurationSemanticsSeconds DurationSemantics = "seconds"
	DurationSemanticsNone    DurationSemantics = "none"
)

type ImageProviderContract struct {
	Size                       string `json:"size"`
	SequentialImageGeneration  string `json:"sequential_image_generation"`
	ResponseFormat             string `json:"response_format"`
	OutputFormat               string `json:"output_format"`
	Stream                     bool   `json:"stream"`
	Watermark                  bool   `json:"watermark"`
	RequireExactResponseModel  bool   `json:"require_exact_response_model"`
	RequireGeneratedImageUsage bool   `json:"require_generated_image_usage"`
}

type ArtifactContract struct {
	MediaType   string `json:"media_type"`
	ContentType string `json:"content_type"`
	Width       int    `json:"width"`
	Height      int    `json:"height"`
	Count       int    `json:"count"`
}

// Profile is immutable after registration. Revision identifies the adapter
// implementation contract and is intentionally separate from the public
// capability revision and the signed routing/acceptance release identity.
type Profile struct {
	ID                string                             `json:"id"`
	Revision          string                             `json:"-"`
	Protocol          string                             `json:"protocol"`
	NativeChannelType int                                `json:"native_channel_type"`
	Capability        dto.PlatformGenerationCapabilities `json:"capability"`
	DurationSemantics map[string]DurationSemantics       `json:"duration_semantics"`
	Image             *ImageProviderContract             `json:"image,omitempty"`
	Artifacts         map[string]ArtifactContract        `json:"artifacts"`
}

var profiles = buildProfiles()

func buildProfiles() map[string]Profile {
	buildArkImage := func(id string) Profile {
		profile := Profile{
			ID:                id,
			Protocol:          VolcengineArkImageProtocolV1,
			NativeChannelType: constant.ChannelTypeVolcEngine,
			Capability: dto.PlatformGenerationCapabilities{
				SchemaVersion: 1,
				Modes: map[string]dto.PlatformModeCapability{
					"text_to_image": {
						InputMediaTypes:      []string{},
						SupportsFace:         false,
						RequiredResourceKeys: []string{},
						Limits: dto.PlatformCapabilityLimits{
							MaxPromptLength: 1000,
							// Compatibility only. DurationSemanticsNone makes clear
							// that this is not a physical image-model capability.
							DurationSeconds: []int{1},
							AspectRatios:    []string{"1:1"},
							Resolutions:     []string{constant.PlatformGenerationArkSeedream50CompatibilitySize},
							OutputCounts:    []int{1},
						},
					},
				},
			},
			DurationSemantics: map[string]DurationSemantics{"text_to_image": DurationSemanticsNone},
			Image: &ImageProviderContract{
				Size:                       constant.PlatformGenerationArkSeedream50CompatibilitySize,
				SequentialImageGeneration:  "disabled",
				ResponseFormat:             "url",
				OutputFormat:               "png",
				Stream:                     false,
				Watermark:                  true,
				RequireExactResponseModel:  true,
				RequireGeneratedImageUsage: true,
			},
			Artifacts: map[string]ArtifactContract{
				"text_to_image": {
					MediaType:   "image",
					ContentType: "image/png",
					Width:       constant.PlatformGenerationArkSeedream50CompatibilityWidth,
					Height:      constant.PlatformGenerationArkSeedream50CompatibilityHeight,
					Count:       1,
				},
			},
		}
		profile.Revision = profileRevision(profile)
		return profile
	}
	genericArkImage := buildArkImage(VolcengineArkImageGenerationV1)
	legacySeedream := buildArkImage(Seedream50TextToImageV1)
	registry := map[string]Profile{
		genericArkImage.ID: genericArkImage,
		legacySeedream.ID:  legacySeedream,
	}
	for _, profile := range BuiltinSeedanceProfiles() {
		if _, duplicate := registry[profile.ID]; duplicate {
			panic(fmt.Sprintf("duplicate generation capability profile %q", profile.ID))
		}
		registry[profile.ID] = profile
	}
	for _, profile := range BuiltinMiniMaxH3Profiles() {
		if _, duplicate := registry[profile.ID]; duplicate {
			panic(fmt.Sprintf("duplicate generation capability profile %q", profile.ID))
		}
		registry[profile.ID] = profile
	}
	for _, profile := range BuiltinGoogleVideoProfiles() {
		if _, duplicate := registry[profile.ID]; duplicate {
			panic(fmt.Sprintf("duplicate generation capability profile %q", profile.ID))
		}
		registry[profile.ID] = profile
	}
	return registry
}

// AcceptanceTestModes returns the deterministic set of modes for which Relay
// owns a complete, self-contained provider acceptance probe. Google video
// profiles include both text and image modes because their adapters accept a
// locally generated inline PNG; protocols that require a provider-fetchable
// public media URL retain their historical text-only probe until a separately
// attested media-fixture service is available.
func (profile Profile) AcceptanceTestModes() []string {
	modes := make([]string, 0, 2)
	appendMode := func(mode string) {
		if _, ok := profile.Capability.Modes[mode]; !ok {
			return
		}
		if _, ok := profile.Artifact(mode); !ok {
			return
		}
		modes = append(modes, mode)
	}
	switch profile.Protocol {
	case VolcengineArkImageProtocolV1:
		appendMode("text_to_image")
	case VolcengineArkVideoProtocolV1, MiniMaxH3VideoProtocolV2:
		appendMode("text_to_video")
	case GoogleGeminiInteractionsVideoProtocolV1, GoogleGeminiVeoVideoProtocolV1, GoogleVertexVeoVideoProtocolV1:
		appendMode("text_to_video")
		appendMode("image_to_video")
	}
	return modes
}

// AcceptanceTestMode is the backward-compatible default acceptance mode.
// New callers that qualify a multi-mode release must use AcceptanceTestModes
// and persist one independently verified receipt for every returned mode.
func (profile Profile) AcceptanceTestMode() (string, bool) {
	var mode string
	modes := profile.AcceptanceTestModes()
	if len(modes) == 0 {
		return "", false
	}
	mode = modes[0]
	return mode, true
}

func profileRevision(profile Profile) string {
	profile.Revision = ""
	if err := profile.ValidateImmutableContract(); err != nil {
		panic(err)
	}
	serialized, err := common.Marshal(profile)
	if err != nil {
		panic(err)
	}
	digest := sha256.Sum256(serialized)
	return fmt.Sprintf("sha256:%x", digest)
}

func Get(id string) (Profile, bool) {
	profile, ok := profiles[strings.TrimSpace(id)]
	if !ok {
		return Profile{}, false
	}
	return cloneProfile(profile), true
}

// Resolve selects an adapter implementation profile. Generic Ark image
// profiles keep provider identity in generationrelease. H3 profiles require
// an exact reviewed model because H3 Max must not inherit H3's reference
// semantics. The historical Seedream profile and omitted-ID inference retain
// an exact legacy binding while deployed route documents are migrated.
func Resolve(id string, nativeChannelType int, upstreamModel string) (Profile, bool, error) {
	id = strings.TrimSpace(id)
	upstreamModel = strings.TrimSpace(upstreamModel)
	if id != "" {
		profile, ok := Get(id)
		if !ok {
			return Profile{}, false, fmt.Errorf("unknown generation capability profile %q", id)
		}
		if profile.NativeChannelType != nativeChannelType {
			return Profile{}, false, fmt.Errorf("generation capability profile %q does not match native channel type %d", id, nativeChannelType)
		}
		if id == Seedream50TextToImageV1 && upstreamModel != constant.PlatformGenerationArkSeedream50Model {
			return Profile{}, false, fmt.Errorf("legacy generation capability profile %q does not match upstream model %q", id, upstreamModel)
		}
		if profile.Protocol == MiniMaxH3VideoProtocolV2 {
			if err := ValidateMiniMaxH3ModelBinding(profile, upstreamModel, profile.Capability); err != nil {
				return Profile{}, false, err
			}
		}
		if isGoogleVideoProtocol(profile.Protocol) {
			if err := ValidateGoogleVideoModelBinding(profile, upstreamModel, profile.Capability); err != nil {
				return Profile{}, false, err
			}
		}
		return profile, true, nil
	}
	// Omitted profile IDs are migration-only. Never infer a generic protocol
	// profile merely from a native channel because that would let an arbitrary
	// provider model claim the adapter's maximum capability without a release.
	if nativeChannelType != constant.ChannelTypeVolcEngine || upstreamModel != constant.PlatformGenerationArkSeedream50Model {
		return Profile{}, false, nil
	}
	profile, ok := Get(Seedream50TextToImageV1)
	return profile, ok, nil
}

func (profile Profile) DurationApplies(mode string) bool {
	semantics, ok := profile.DurationSemantics[mode]
	return !ok || semantics == DurationSemanticsSeconds
}

func (profile Profile) Artifact(mode string) (ArtifactContract, bool) {
	artifact, ok := profile.Artifacts[mode]
	return artifact, ok
}

// ValidateRequest enforces the actual implemented adapter contract. It does
// not interpret duration_seconds for image profiles whose duration semantics
// are none, even when a v1 client sends the compatibility value 1.
func (profile Profile) ValidateRequest(request dto.PlatformGenerationRequest) error {
	mode, ok := profile.Capability.Modes[request.Mode]
	if !ok {
		return fmt.Errorf("profile %q does not implement mode %q", profile.ID, request.Mode)
	}
	if strings.TrimSpace(request.Inputs.Prompt) == "" || len([]rune(request.Inputs.Prompt)) > mode.Limits.MaxPromptLength {
		return fmt.Errorf("request prompt exceeds profile %q", profile.ID)
	}
	if request.Output.FaceEnabled && !mode.SupportsFace {
		return fmt.Errorf("request inputs exceed profile %q", profile.ID)
	}
	mediaCounts := map[string]int{"image": 0, "video": 0, "audio": 0}
	allowedMedia := make(map[string]struct{}, len(mode.InputMediaTypes))
	for _, mediaType := range mode.InputMediaTypes {
		allowedMedia[mediaType] = struct{}{}
	}
	for _, asset := range request.Inputs.Assets {
		if _, ok := allowedMedia[asset.MediaType]; !ok {
			return fmt.Errorf("request media type %q exceeds profile %q", asset.MediaType, profile.ID)
		}
		mediaCounts[asset.MediaType]++
	}
	if mediaCounts["image"] > mode.Limits.MaxImages || mediaCounts["video"] > mode.Limits.MaxVideos ||
		mediaCounts["audio"] > mode.Limits.MaxAudio {
		return fmt.Errorf("request media counts exceed profile %q", profile.ID)
	}
	if request.Mode == "image_to_video" && mediaCounts["image"] == 0 {
		return fmt.Errorf("request requires an image for profile %q", profile.ID)
	}
	if request.Mode == "video_to_video" && mediaCounts["video"] == 0 {
		return fmt.Errorf("request requires a video for profile %q", profile.ID)
	}
	if !containsString(mode.Limits.AspectRatios, request.Output.AspectRatio) ||
		!containsString(mode.Limits.Resolutions, request.Output.Resolution) ||
		!containsInt(mode.Limits.OutputCounts, request.Output.Count) {
		return fmt.Errorf("request output exceeds profile %q", profile.ID)
	}
	if profile.DurationApplies(request.Mode) && !containsInt(mode.Limits.DurationSeconds, request.Output.DurationSeconds) {
		return fmt.Errorf("request duration exceeds profile %q", profile.ID)
	}
	return nil
}

// ValidateNarrowing proves that a route capability is a subset of the
// adapter's implemented capability. Adding required entitlements is a
// restriction; removing a profile-required entitlement is an expansion.
func (profile Profile) ValidateNarrowing(candidate dto.PlatformGenerationCapabilities) error {
	// Report contract expansion before validating the provider-specific shape.
	// Besides producing a clearer error, this prevents an unsupported public
	// mode from being interpreted using another profile's duration/media rules.
	for modeName := range candidate.Modes {
		if _, ok := profile.Capability.Modes[modeName]; !ok {
			return fmt.Errorf("mode %q is not implemented by profile %q", modeName, profile.ID)
		}
	}
	if err := ValidateCapabilityShape(candidate, profile.DurationSemantics); err != nil {
		return fmt.Errorf("capability for profile %q is invalid: %w", profile.ID, err)
	}
	if candidate.SchemaVersion != profile.Capability.SchemaVersion {
		return fmt.Errorf("capability schema version does not match profile %q", profile.ID)
	}
	for modeName, candidateMode := range candidate.Modes {
		baseMode := profile.Capability.Modes[modeName]
		if candidateMode.SupportsFace && !baseMode.SupportsFace {
			return fmt.Errorf("face controls exceed profile %q", profile.ID)
		}
		if !stringSubset(candidateMode.InputMediaTypes, baseMode.InputMediaTypes) ||
			!stringSubset(baseMode.RequiredResourceKeys, candidateMode.RequiredResourceKeys) ||
			!conditionalResourceRequirementsAreNarrower(
				baseMode.ConditionalRequiredResourceKeys,
				candidateMode.ConditionalRequiredResourceKeys,
			) {
			return fmt.Errorf("media or entitlement declaration exceeds profile %q", profile.ID)
		}
		candidateLimits := candidateMode.Limits
		baseLimits := baseMode.Limits
		if candidateLimits.MaxPromptLength > baseLimits.MaxPromptLength ||
			candidateLimits.MaxImages > baseLimits.MaxImages ||
			candidateLimits.MaxVideos > baseLimits.MaxVideos ||
			candidateLimits.MaxAudio > baseLimits.MaxAudio ||
			!stringSubset(candidateLimits.AspectRatios, baseLimits.AspectRatios) ||
			!stringSubset(candidateLimits.Resolutions, baseLimits.Resolutions) ||
			!intSubset(candidateLimits.OutputCounts, baseLimits.OutputCounts) {
			return fmt.Errorf("limits exceed profile %q", profile.ID)
		}
		if profile.DurationApplies(modeName) && !intSubset(candidateLimits.DurationSeconds, baseLimits.DurationSeconds) {
			return fmt.Errorf("durations exceed profile %q", profile.ID)
		}
		if !profile.DurationApplies(modeName) && len(candidateLimits.DurationSeconds) > 0 &&
			!intSubset(candidateLimits.DurationSeconds, baseLimits.DurationSeconds) {
			return fmt.Errorf("image duration compatibility values exceed profile %q", profile.ID)
		}
	}
	return nil
}

var (
	profileIDPattern          = regexp.MustCompile(`^[a-z][a-z0-9._-]{2,159}$`)
	profileAspectRatioPattern = regexp.MustCompile(`^[1-9][0-9]{0,3}:[1-9][0-9]{0,3}$`)
	profileResolutionPattern  = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$`)
	profileResourceKeyPattern = regexp.MustCompile(`^[a-z][a-z0-9._-]{0,127}$`)
)

// ValidateImmutableContract validates the complete adapter contract rather
// than only its public capability. A stored snapshot is executable code-path
// input: protocol, native channel, duration semantics, image provider options,
// and terminal artifact declarations must therefore form one closed contract.
func (profile Profile) ValidateImmutableContract() error {
	if !profileIDPattern.MatchString(profile.ID) {
		return fmt.Errorf("profile id is invalid")
	}
	switch profile.Protocol {
	case "volcengine-ark-images-generations":
		if profile.NativeChannelType != constant.ChannelTypeVolcEngine || profile.Image == nil {
			return fmt.Errorf("image profile protocol or native channel is inconsistent")
		}
	case "volcengine-ark-contents-generations-tasks-v1":
		if profile.NativeChannelType != constant.ChannelTypeVolcEngine || profile.Image != nil {
			return fmt.Errorf("video profile protocol or native channel is inconsistent")
		}
	case MiniMaxH3VideoProtocolV2:
		if profile.NativeChannelType != constant.ChannelTypeMiniMax || profile.Image != nil {
			return fmt.Errorf("MiniMax H3 profile protocol or native channel is inconsistent")
		}
	case GoogleGeminiInteractionsVideoProtocolV1, GoogleGeminiVeoVideoProtocolV1:
		if profile.NativeChannelType != constant.ChannelTypeGemini || profile.Image != nil {
			return fmt.Errorf("Google Gemini video profile protocol or native channel is inconsistent")
		}
	case GoogleVertexVeoVideoProtocolV1:
		if profile.NativeChannelType != constant.ChannelTypeVertexAi || profile.Image != nil {
			return fmt.Errorf("Google Vertex video profile protocol or native channel is inconsistent")
		}
	default:
		return fmt.Errorf("profile protocol is unsupported")
	}
	if err := ValidateCapabilityShape(profile.Capability, profile.DurationSemantics); err != nil {
		return err
	}
	if len(profile.DurationSemantics) != len(profile.Capability.Modes) || len(profile.Artifacts) != len(profile.Capability.Modes) {
		return fmt.Errorf("profile mode contracts are incomplete")
	}
	for modeName, mode := range profile.Capability.Modes {
		semantics, hasSemantics := profile.DurationSemantics[modeName]
		artifact, hasArtifact := profile.Artifacts[modeName]
		if !hasSemantics || !hasArtifact || artifact.Count < 1 || !containsInt(mode.Limits.OutputCounts, artifact.Count) {
			return fmt.Errorf("profile mode %q has no closed artifact contract", modeName)
		}
		switch modeName {
		case "text_to_image":
			if profile.Protocol != "volcengine-ark-images-generations" || semantics != DurationSemanticsNone ||
				artifact.MediaType != "image" || artifact.ContentType != "image/png" || artifact.Width < 1 || artifact.Height < 1 {
				return fmt.Errorf("profile image mode contract is inconsistent")
			}
		case "text_to_video", "image_to_video", "video_to_video":
			if (profile.Protocol != VolcengineArkVideoProtocolV1 && profile.Protocol != MiniMaxH3VideoProtocolV2 && !isGoogleVideoProtocol(profile.Protocol)) || semantics != DurationSemanticsSeconds ||
				artifact.MediaType != "video" || artifact.ContentType != "video/mp4" || artifact.Width != 0 || artifact.Height != 0 {
				return fmt.Errorf("profile video mode contract is inconsistent")
			}
		default:
			return fmt.Errorf("profile mode %q is unsupported", modeName)
		}
	}
	for modeName, semantics := range profile.DurationSemantics {
		if _, ok := profile.Capability.Modes[modeName]; !ok || (semantics != DurationSemanticsNone && semantics != DurationSemanticsSeconds) {
			return fmt.Errorf("profile duration semantics are inconsistent")
		}
	}
	for modeName := range profile.Artifacts {
		if _, ok := profile.Capability.Modes[modeName]; !ok {
			return fmt.Errorf("profile artifact contract has an unknown mode")
		}
	}
	if profile.Image != nil {
		imageMode, ok := profile.Capability.Modes["text_to_image"]
		image := profile.Image
		if !ok || !containsString(imageMode.Limits.Resolutions, image.Size) ||
			image.SequentialImageGeneration != "disabled" || image.ResponseFormat != "url" || image.OutputFormat != "png" ||
			image.Stream || !image.RequireExactResponseModel || !image.RequireGeneratedImageUsage {
			return fmt.Errorf("profile image provider contract is inconsistent")
		}
	}
	if profile.Protocol == MiniMaxH3VideoProtocolV2 {
		return validateMiniMaxH3ProfileContract(profile)
	}
	if isGoogleVideoProtocol(profile.Protocol) {
		return validateGoogleVideoProfileContract(profile)
	}
	return nil
}

// ValidateCapabilityShape validates a release/route capability independently
// from the adapter ceiling. This prevents an empty or contradictory document
// from passing subset checks simply because every empty set is a subset.
func ValidateCapabilityShape(capability dto.PlatformGenerationCapabilities, durationSemantics map[string]DurationSemantics) error {
	if capability.SchemaVersion != 1 && capability.SchemaVersion != 2 {
		return fmt.Errorf("schema_version must be 1 or 2")
	}
	if len(capability.Modes) == 0 {
		return fmt.Errorf("at least one mode is required")
	}
	for modeName, mode := range capability.Modes {
		switch modeName {
		case "text_to_image", "text_to_video", "image_to_video", "video_to_video":
		default:
			return fmt.Errorf("mode %q is unknown", modeName)
		}
		limits := mode.Limits
		if limits.MaxPromptLength < 1 || limits.MaxPromptLength > 10_000 ||
			limits.MaxImages < 0 || limits.MaxImages > 15 ||
			limits.MaxVideos < 0 || limits.MaxVideos > 15 ||
			limits.MaxAudio < 0 || limits.MaxAudio > 15 ||
			limits.MaxImages+limits.MaxVideos+limits.MaxAudio > 15 {
			return fmt.Errorf("mode %q numeric limits are invalid", modeName)
		}
		semantics, declared := durationSemantics[modeName]
		durationApplies := !declared || semantics == DurationSemanticsSeconds
		if durationApplies && len(limits.DurationSeconds) == 0 {
			return fmt.Errorf("mode %q requires duration_seconds", modeName)
		}
		if len(limits.AspectRatios) == 0 || len(limits.Resolutions) == 0 || len(limits.OutputCounts) == 0 {
			return fmt.Errorf("mode %q requires output enumerations", modeName)
		}

		media := make(map[string]struct{}, len(mode.InputMediaTypes))
		for _, value := range mode.InputMediaTypes {
			switch value {
			case "image", "video", "audio":
			default:
				return fmt.Errorf("mode %q input media type %q is unknown", modeName, value)
			}
			if _, duplicate := media[value]; duplicate {
				return fmt.Errorf("mode %q input media types contain duplicates", modeName)
			}
			media[value] = struct{}{}
		}
		if (limits.MaxImages > 0) != containsString(mode.InputMediaTypes, "image") ||
			(limits.MaxVideos > 0) != containsString(mode.InputMediaTypes, "video") ||
			(limits.MaxAudio > 0) != containsString(mode.InputMediaTypes, "audio") {
			return fmt.Errorf("mode %q media limits are inconsistent", modeName)
		}
		if modeName == "image_to_video" && limits.MaxImages < 1 {
			return fmt.Errorf("image_to_video requires image input")
		}
		if modeName == "video_to_video" && limits.MaxVideos < 1 {
			return fmt.Errorf("video_to_video requires video input")
		}
		if err := validateUniqueInts(limits.DurationSeconds, 1, 3600, "duration_seconds"); err != nil {
			return fmt.Errorf("mode %q: %w", modeName, err)
		}
		if err := validateUniqueInts(limits.OutputCounts, 1, 16, "output_counts"); err != nil {
			return fmt.Errorf("mode %q: %w", modeName, err)
		}
		if err := validateUniqueStrings(limits.AspectRatios, profileAspectRatioPattern, "aspect_ratios"); err != nil {
			return fmt.Errorf("mode %q: %w", modeName, err)
		}
		if err := validateUniqueStrings(limits.Resolutions, profileResolutionPattern, "resolutions"); err != nil {
			return fmt.Errorf("mode %q: %w", modeName, err)
		}
		if err := validateResourceKeys(capability.SchemaVersion, mode); err != nil {
			return fmt.Errorf("mode %q: %w", modeName, err)
		}
	}
	return nil
}

// NormalizeCapability returns a defensive, deterministically ordered copy for
// hashing and signing. It does not silently repair invalid documents.
func NormalizeCapability(capability dto.PlatformGenerationCapabilities) dto.PlatformGenerationCapabilities {
	normalized := dto.PlatformGenerationCapabilities{
		SchemaVersion: capability.SchemaVersion,
		Modes:         make(map[string]dto.PlatformModeCapability, len(capability.Modes)),
	}
	for modeName, mode := range capability.Modes {
		mode.InputMediaTypes = sortedUniqueStrings(mode.InputMediaTypes)
		mode.RequiredResourceKeys = sortedUniqueStrings(mode.RequiredResourceKeys)
		mode.Limits.DurationSeconds = sortedUniqueInts(mode.Limits.DurationSeconds)
		mode.Limits.AspectRatios = sortedUniqueStrings(mode.Limits.AspectRatios)
		mode.Limits.Resolutions = sortedUniqueStrings(mode.Limits.Resolutions)
		mode.Limits.OutputCounts = sortedUniqueInts(mode.Limits.OutputCounts)
		if mode.ConditionalRequiredResourceKeys != nil {
			conditions := make(map[string][]string, len(mode.ConditionalRequiredResourceKeys))
			for condition, keys := range mode.ConditionalRequiredResourceKeys {
				conditions[condition] = sortedUniqueStrings(keys)
			}
			mode.ConditionalRequiredResourceKeys = conditions
		}
		normalized.Modes[modeName] = mode
	}
	return normalized
}

func validateUniqueInts(values []int, minimum int, maximum int, field string) error {
	seen := make(map[int]struct{}, len(values))
	for _, value := range values {
		if value < minimum || value > maximum {
			return fmt.Errorf("%s contains an out-of-range value", field)
		}
		if _, duplicate := seen[value]; duplicate {
			return fmt.Errorf("%s contains duplicates", field)
		}
		seen[value] = struct{}{}
	}
	return nil
}

func validateUniqueStrings(values []string, pattern *regexp.Regexp, field string) error {
	seen := make(map[string]struct{}, len(values))
	for _, value := range values {
		if !pattern.MatchString(value) || !utf8.ValidString(value) {
			return fmt.Errorf("%s contains an invalid value", field)
		}
		if _, duplicate := seen[value]; duplicate {
			return fmt.Errorf("%s contains duplicates", field)
		}
		seen[value] = struct{}{}
	}
	return nil
}

func validateResourceKeys(schemaVersion int, mode dto.PlatformModeCapability) error {
	unconditional := make(map[string]struct{}, len(mode.RequiredResourceKeys))
	for _, key := range mode.RequiredResourceKeys {
		if !profileResourceKeyPattern.MatchString(key) {
			return fmt.Errorf("required_resource_keys contains an invalid key")
		}
		if _, duplicate := unconditional[key]; duplicate {
			return fmt.Errorf("required_resource_keys contains duplicates")
		}
		unconditional[key] = struct{}{}
	}
	if schemaVersion == 1 && mode.ConditionalRequiredResourceKeys != nil {
		return fmt.Errorf("schema v1 cannot declare conditional resource keys")
	}
	for condition, keys := range mode.ConditionalRequiredResourceKeys {
		if condition != "face_enabled" || !mode.SupportsFace || len(keys) == 0 {
			return fmt.Errorf("conditional_required_resource_keys is invalid")
		}
		seen := make(map[string]struct{}, len(keys))
		for _, key := range keys {
			if !profileResourceKeyPattern.MatchString(key) {
				return fmt.Errorf("conditional_required_resource_keys contains an invalid key")
			}
			if _, duplicate := seen[key]; duplicate {
				return fmt.Errorf("conditional_required_resource_keys contains duplicates")
			}
			if _, duplicate := unconditional[key]; duplicate {
				return fmt.Errorf("resource key cannot be both unconditional and conditional")
			}
			seen[key] = struct{}{}
		}
	}
	return nil
}

func conditionalResourceRequirementsAreNarrower(base map[string][]string, candidate map[string][]string) bool {
	for condition, baseKeys := range base {
		if !stringSubset(baseKeys, candidate[condition]) {
			return false
		}
	}
	return true
}

func SnapshotMetadata(metadata map[string]any, profile Profile) map[string]any {
	result := make(map[string]any, len(metadata)+3)
	for key, value := range metadata {
		if key == MetadataProfileID || key == MetadataProfileRevision || key == MetadataProfileSnapshot {
			continue
		}
		result[key] = value
	}
	serialized, err := common.Marshal(profile)
	if err != nil {
		// Registered profiles contain only JSON-safe contract values. Treat a
		// future violation as a programmer error rather than accepting a job
		// whose immutable execution contract cannot be recovered.
		panic(err)
	}
	result[MetadataProfileID] = profile.ID
	result[MetadataProfileRevision] = profile.Revision
	result[MetadataProfileSnapshot] = string(serialized)
	return result
}

// ResolveSnapshot resolves the exact code-reviewed contract captured at job
// acceptance. Legacy jobs that predate the complete snapshot retain the
// previous current-registry fallback, while new jobs remain executable across
// rolling releases and fail closed if the persisted contract is tampered.
func ResolveSnapshot(metadata map[string]any) (Profile, bool, error) {
	id, _ := metadata[MetadataProfileID].(string)
	revision, _ := metadata[MetadataProfileRevision].(string)
	snapshot, _ := metadata[MetadataProfileSnapshot].(string)
	if strings.TrimSpace(id) == "" && strings.TrimSpace(revision) == "" && strings.TrimSpace(snapshot) == "" {
		return Profile{}, false, nil
	}
	if strings.TrimSpace(id) == "" || strings.TrimSpace(revision) == "" {
		return Profile{}, false, fmt.Errorf("generation capability profile snapshot is incomplete")
	}
	if strings.TrimSpace(snapshot) != "" {
		raw := []byte(snapshot)
		if err := common.RejectDuplicateJSONKeys(raw); err != nil {
			return Profile{}, false, fmt.Errorf("generation capability profile snapshot is invalid: %w", err)
		}
		var profile Profile
		if err := common.DecodeJsonDisallowUnknownFields(strings.NewReader(snapshot), &profile); err != nil {
			return Profile{}, false, fmt.Errorf("generation capability profile snapshot is invalid: %w", err)
		}
		if err := profile.ValidateImmutableContract(); err != nil {
			return Profile{}, false, fmt.Errorf("generation capability profile snapshot is invalid: %w", err)
		}
		if profile.ID != id || profileRevision(profile) != revision {
			return Profile{}, false, fmt.Errorf("generation capability profile snapshot is unavailable")
		}
		profile.Revision = revision
		return cloneProfile(profile), true, nil
	}
	profile, ok := Get(id)
	if !ok || profile.Revision != revision {
		return Profile{}, false, fmt.Errorf("generation capability profile snapshot is unavailable")
	}
	return profile, true, nil
}

func PublicMetadata(metadata map[string]any) map[string]any {
	result := make(map[string]any, len(metadata))
	for key, value := range metadata {
		if key == MetadataProfileID || key == MetadataProfileRevision || key == MetadataProfileSnapshot {
			continue
		}
		result[key] = value
	}
	return result
}

func cloneProfile(profile Profile) Profile {
	serialized, _ := common.Marshal(profile)
	var cloned Profile
	_ = common.Unmarshal(serialized, &cloned)
	cloned.Revision = profile.Revision
	return cloned
}

func stringSubset(candidate []string, base []string) bool {
	baseSet := make(map[string]struct{}, len(base))
	for _, value := range base {
		baseSet[value] = struct{}{}
	}
	for _, value := range candidate {
		if _, ok := baseSet[value]; !ok {
			return false
		}
	}
	return true
}

func intSubset(candidate []int, base []int) bool {
	baseSet := make(map[int]struct{}, len(base))
	for _, value := range base {
		baseSet[value] = struct{}{}
	}
	for _, value := range candidate {
		if _, ok := baseSet[value]; !ok {
			return false
		}
	}
	return true
}

func containsString(values []string, expected string) bool {
	index := sort.SearchStrings(values, expected)
	if index < len(values) && values[index] == expected {
		return true
	}
	for _, value := range values {
		if value == expected {
			return true
		}
	}
	return false
}

func containsInt(values []int, expected int) bool {
	for _, value := range values {
		if value == expected {
			return true
		}
	}
	return false
}

func sortedUniqueStrings(values []string) []string {
	set := make(map[string]struct{}, len(values))
	for _, value := range values {
		set[value] = struct{}{}
	}
	result := make([]string, 0, len(set))
	for value := range set {
		result = append(result, value)
	}
	sort.Strings(result)
	return result
}

func sortedUniqueInts(values []int) []int {
	set := make(map[int]struct{}, len(values))
	for _, value := range values {
		set[value] = struct{}{}
	}
	result := make([]int, 0, len(set))
	for value := range set {
		result = append(result, value)
	}
	sort.Ints(result)
	return result
}
