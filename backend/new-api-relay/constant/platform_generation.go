package constant

const (
	HeaderPlatformGenerationInternalAdmission = "X-Relay-Internal-Admission"
	HeaderPlatformGenerationJobID             = "X-Relay-Generation-Job-ID"
	HeaderPlatformGenerationRouteID           = "X-Relay-Generation-Route-ID"
	HeaderPlatformGenerationWorkerLeaseToken  = "X-Relay-Generation-Worker-Lease-Token"
	HeaderPlatformGenerationSubmissionToken   = "X-Relay-Generation-Submission-Token"
	HeaderPlatformGenerationTransportRevision = "X-Relay-Generation-Transport-Revision"
	HeaderPlatformGenerationTransportSHA256   = "X-Relay-Generation-Transport-SHA256"
	HeaderPlatformGenerationProviderStarted   = "X-Relay-Provider-Request-Started"

	// PlatformGenerationArkSeedream50Model is the exact, non-Lite Ark model ID
	// accepted by the protected image-generation bridge. Public product aliases
	// are deliberately separate from this provider identity.
	PlatformGenerationArkSeedream50Model = "doubao-seedream-5-0-260128"

	// PlatformGenerationPublicSeedream50Model is the unambiguous canonical
	// customer-facing Relay ID. The old seedream-5-lite ID remains a deprecated
	// compatibility alias; it must never be interpreted as proof that the
	// upstream provider model is a Lite SKU.
	PlatformGenerationPublicSeedream50Model            = "seedream-5"
	PlatformGenerationLegacySeedream50LitePublicAlias  = "seedream-5-lite"
	PlatformGenerationArkSeedream50CompatibilitySize   = "2048x2048"
	PlatformGenerationArkSeedream50CompatibilityWidth  = 2048
	PlatformGenerationArkSeedream50CompatibilityHeight = 2048

	// Deprecated compatibility names. New code must use the unambiguous names
	// above; keeping these aliases avoids breaking existing route/test fixtures
	// while operators migrate the public model ID.
	PlatformGenerationArkSeedream50LiteModel  = PlatformGenerationArkSeedream50Model
	PlatformGenerationArkSeedream50LiteSize   = PlatformGenerationArkSeedream50CompatibilitySize
	PlatformGenerationArkSeedream50LiteWidth  = PlatformGenerationArkSeedream50CompatibilityWidth
	PlatformGenerationArkSeedream50LiteHeight = PlatformGenerationArkSeedream50CompatibilityHeight
)
