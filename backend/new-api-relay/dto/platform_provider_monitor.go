package dto

import (
	"fmt"
	"regexp"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/google/uuid"
)

const (
	PlatformProviderProbeHealthy     = "healthy"
	PlatformProviderProbeFailed      = "failed"
	PlatformProviderProbeInvalidated = "invalidated"
)

const (
	PlatformProviderOutcomeSucceeded = "succeeded"
	PlatformProviderOutcomeFailed    = "failed"
)

const (
	PlatformProviderFailureOwnerNone     = "none"
	PlatformProviderFailureOwnerProvider = "provider"
	PlatformProviderFailureOwnerClient   = "client"
	PlatformProviderFailureOwnerRelay    = "relay"
)

const (
	PlatformProviderIncidentSuccessRateDrop   = "success_rate_drop"
	PlatformProviderIncidentWidespreadFailure = "widespread_route_failure"
	PlatformProviderIncidentBatchInvalidation = "batch_account_invalidation"
)

const (
	PlatformProviderIncidentTriggered = "triggered"
	PlatformProviderIncidentRecovered = "recovered"
)

var platformProviderCodePattern = regexp.MustCompile(`^[a-z][a-z0-9_.-]{0,63}$`)

// PlatformProviderRouteProbeResult is returned by a provider-specific prober.
// Route identity is supplied by the Relay, rather than trusted from the prober.
type PlatformProviderRouteProbeResult struct {
	Status         string
	FailureCode    string
	ProviderCaused bool
}

func (result PlatformProviderRouteProbeResult) Validate() error {
	switch result.Status {
	case PlatformProviderProbeHealthy:
		if result.FailureCode != "" || result.ProviderCaused {
			return fmt.Errorf("healthy provider probe cannot carry failure metadata")
		}
	case PlatformProviderProbeFailed, PlatformProviderProbeInvalidated:
		if !platformProviderCodePattern.MatchString(result.FailureCode) {
			return fmt.Errorf("provider probe failure_code is invalid")
		}
		if result.Status == PlatformProviderProbeInvalidated && !result.ProviderCaused {
			return fmt.Errorf("account invalidation must be provider-caused")
		}
	default:
		return fmt.Errorf("provider probe status is invalid")
	}
	return nil
}

// PlatformProviderTerminalOutcomeInput is an immutable observation emitted by
// provider execution. It deliberately contains no credential, request body,
// response body, or customer-price field.
type PlatformProviderTerminalOutcomeInput struct {
	EventID            string
	RouteID            int64
	RelayJobID         string
	Outcome            string
	FailureOwner       string
	FailureCode        string
	AccountInvalidated bool
	OccurredAt         time.Time
	ExternalReference  string
}

func (input PlatformProviderTerminalOutcomeInput) Validate() error {
	if parsed, err := uuid.Parse(input.EventID); err != nil || parsed.String() != input.EventID {
		return fmt.Errorf("provider outcome event_id must be a canonical UUID")
	}
	if input.RouteID <= 0 {
		return fmt.Errorf("provider outcome route_id must be positive")
	}
	if parsed, err := uuid.Parse(input.RelayJobID); err != nil || parsed.String() != input.RelayJobID {
		return fmt.Errorf("provider outcome relay_job_id must be a canonical UUID")
	}
	if input.OccurredAt.IsZero() {
		return fmt.Errorf("provider outcome occurred_at is required")
	}
	if input.ExternalReference == "" || strings.TrimSpace(input.ExternalReference) != input.ExternalReference || utf8.RuneCountInString(input.ExternalReference) > 240 {
		return fmt.Errorf("provider outcome external_reference is invalid")
	}
	switch input.Outcome {
	case PlatformProviderOutcomeSucceeded:
		if input.FailureOwner != PlatformProviderFailureOwnerNone || input.FailureCode != "" || input.AccountInvalidated {
			return fmt.Errorf("successful provider outcome cannot carry failure metadata")
		}
	case PlatformProviderOutcomeFailed:
		switch input.FailureOwner {
		case PlatformProviderFailureOwnerProvider,
			PlatformProviderFailureOwnerClient,
			PlatformProviderFailureOwnerRelay:
		default:
			return fmt.Errorf("provider outcome failure_owner is invalid")
		}
		if !platformProviderCodePattern.MatchString(input.FailureCode) {
			return fmt.Errorf("provider outcome failure_code is invalid")
		}
		if input.AccountInvalidated && input.FailureOwner != PlatformProviderFailureOwnerProvider {
			return fmt.Errorf("account invalidation must be provider-caused")
		}
	default:
		return fmt.Errorf("provider outcome is invalid")
	}
	return nil
}

// PlatformProviderAlertEvent is the stable, secret-free alert webhook body.
type PlatformProviderAlertEvent struct {
	SchemaVersion int                           `json:"schema_version"`
	EventID       string                        `json:"event_id"`
	Type          string                        `json:"type"`
	OccurredAt    time.Time                     `json:"occurred_at"`
	Incident      PlatformProviderAlertIncident `json:"incident"`
}

type PlatformProviderAlertIncident struct {
	Kind             string `json:"kind"`
	State            string `json:"state"`
	ProviderName     string `json:"provider_name"`
	Generation       int    `json:"generation"`
	ReasonCode       string `json:"reason_code"`
	SampleSize       int    `json:"sample_size"`
	SuccessCount     int    `json:"success_count"`
	AffectedRoutes   int    `json:"affected_routes"`
	TotalRoutes      int    `json:"total_routes"`
	SuccessRateBasis int    `json:"success_rate_basis_points"`
}
