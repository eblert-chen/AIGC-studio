package model

import (
	"crypto/sha256"
	"crypto/subtle"
	"errors"
	"fmt"
	"regexp"
	"sort"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/google/uuid"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

const platformProviderMonitorLeaseName = "provider-monitor-v1"

const (
	PlatformProviderRouteHealthUnknown     = "unknown"
	PlatformProviderRouteHealthHealthy     = "healthy"
	PlatformProviderRouteHealthFailed      = "failed"
	PlatformProviderRouteHealthInvalidated = "invalidated"
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

var (
	ErrPlatformProviderMonitorLeaseHeld         = errors.New("provider monitor lease is held by another worker")
	ErrPlatformProviderMonitorLeaseLost         = errors.New("provider monitor lease fencing token is no longer current")
	ErrPlatformProviderTerminalOutcomeCollision = errors.New("provider terminal outcome event collision")
	ErrPlatformProviderRetirementCollision      = errors.New("provider retirement acknowledgement collision")
	ErrPlatformProviderImmutableEvent           = errors.New("provider monitoring event is append-only")
	platformProviderSafeCodePattern             = regexp.MustCompile(`^[a-z][a-z0-9_.-]{0,63}$`)
)

type PlatformProviderMonitorLease struct {
	Name            string     `json:"name" gorm:"type:varchar(64);primaryKey"`
	OwnerID         string     `json:"owner_id" gorm:"type:varchar(128);not null"`
	TokenHash       string     `json:"-" gorm:"type:char(64);not null"`
	AcquiredAt      *time.Time `json:"acquired_at"`
	ExpiresAt       *time.Time `json:"expires_at" gorm:"index"`
	LastHeartbeatAt *time.Time `json:"last_heartbeat_at"`
	LastStartedAt   *time.Time `json:"last_started_at"`
	LastCompletedAt *time.Time `json:"last_completed_at"`
	LastErrorCode   string     `json:"last_error_code" gorm:"type:varchar(64);not null"`
	CreatedAt       time.Time  `json:"created_at"`
	UpdatedAt       time.Time  `json:"updated_at"`
}

func (PlatformProviderMonitorLease) TableName() string {
	return "platform_provider_monitor_leases"
}

type PlatformProviderMonitorLeaseClaim struct {
	OwnerID   string
	Token     string
	ExpiresAt time.Time
}

type PlatformProviderRouteHealth struct {
	RouteID               int64      `json:"route_id" gorm:"primaryKey"`
	RouteKey              string     `json:"route_key" gorm:"type:varchar(120);not null;index"`
	ProviderName          string     `json:"provider_name" gorm:"type:varchar(64);not null;index:idx_platform_provider_health_provider_status,priority:1"`
	ChannelID             int        `json:"channel_id" gorm:"not null"`
	ChannelClass          string     `json:"channel_class" gorm:"type:varchar(32);not null"`
	RouteEnabled          bool       `json:"route_enabled" gorm:"not null"`
	Status                string     `json:"status" gorm:"type:varchar(16);not null;index:idx_platform_provider_health_provider_status,priority:2"`
	FailureCode           string     `json:"failure_code" gorm:"type:varchar(64);not null"`
	FailureProviderCaused bool       `json:"failure_provider_caused" gorm:"not null"`
	ConsecutiveSuccesses  int        `json:"consecutive_successes" gorm:"not null"`
	ConsecutiveFailures   int        `json:"consecutive_failures" gorm:"not null"`
	LastProbeAt           *time.Time `json:"last_probe_at" gorm:"index"`
	LastSuccessAt         *time.Time `json:"last_success_at"`
	LastFailureAt         *time.Time `json:"last_failure_at"`
	CreatedAt             time.Time  `json:"created_at"`
	UpdatedAt             time.Time  `json:"updated_at"`
}

func (PlatformProviderRouteHealth) TableName() string {
	return "platform_provider_route_health"
}

type PlatformProviderTerminalOutcome struct {
	ID                 string    `json:"id" gorm:"type:varchar(36);primaryKey"`
	RouteID            int64     `json:"route_id" gorm:"not null;index"`
	RouteKey           string    `json:"route_key" gorm:"type:varchar(120);not null"`
	ProviderName       string    `json:"provider_name" gorm:"type:varchar(64);not null;index:idx_platform_provider_outcome_window,priority:1"`
	ChannelClass       string    `json:"channel_class" gorm:"type:varchar(32);not null"`
	RelayJobID         string    `json:"relay_job_id" gorm:"type:varchar(36);not null;index"`
	Outcome            string    `json:"outcome" gorm:"type:varchar(16);not null;index"`
	FailureOwner       string    `json:"failure_owner" gorm:"type:varchar(16);not null"`
	FailureCode        string    `json:"failure_code" gorm:"type:varchar(64);not null"`
	AccountInvalidated bool      `json:"account_invalidated" gorm:"not null"`
	OccurredAt         time.Time `json:"occurred_at" gorm:"not null;index:idx_platform_provider_outcome_window,priority:2"`
	ExternalReference  string    `json:"external_reference" gorm:"type:varchar(240);not null"`
	CreatedAt          time.Time `json:"created_at"`
}

func (PlatformProviderTerminalOutcome) TableName() string {
	return "platform_provider_terminal_outcomes"
}

func (*PlatformProviderTerminalOutcome) BeforeUpdate(*gorm.DB) error {
	return ErrPlatformProviderImmutableEvent
}

func (*PlatformProviderTerminalOutcome) BeforeDelete(*gorm.DB) error {
	return ErrPlatformProviderImmutableEvent
}

type PlatformProviderIncident struct {
	ID                     int64      `json:"id" gorm:"primaryKey"`
	ProviderName           string     `json:"provider_name" gorm:"type:varchar(64);not null;uniqueIndex:uniq_platform_provider_incident,priority:1"`
	Kind                   string     `json:"kind" gorm:"type:varchar(40);not null;uniqueIndex:uniq_platform_provider_incident,priority:2;index"`
	Active                 bool       `json:"active" gorm:"not null;index"`
	Generation             int        `json:"generation" gorm:"not null"`
	ReasonCode             string     `json:"reason_code" gorm:"type:varchar(64);not null"`
	SampleSize             int        `json:"sample_size" gorm:"not null"`
	SuccessCount           int        `json:"success_count" gorm:"not null"`
	AffectedRoutes         int        `json:"affected_routes" gorm:"not null"`
	TotalRoutes            int        `json:"total_routes" gorm:"not null"`
	SuccessRateBasisPoints int        `json:"success_rate_basis_points" gorm:"not null"`
	TriggeredAt            *time.Time `json:"triggered_at"`
	RecoveredAt            *time.Time `json:"recovered_at"`
	LastEvaluatedAt        time.Time  `json:"last_evaluated_at" gorm:"not null"`
	CreatedAt              time.Time  `json:"created_at"`
	UpdatedAt              time.Time  `json:"updated_at"`
}

func (PlatformProviderIncident) TableName() string {
	return "platform_provider_incidents"
}

type PlatformProviderAlertEvent struct {
	ID            string    `json:"id" gorm:"type:varchar(36);primaryKey"`
	TransitionKey string    `json:"transition_key" gorm:"type:char(64);not null;uniqueIndex"`
	ProviderName  string    `json:"provider_name" gorm:"type:varchar(64);not null;index"`
	IncidentKind  string    `json:"incident_kind" gorm:"type:varchar(40);not null"`
	IncidentState string    `json:"incident_state" gorm:"type:varchar(16);not null"`
	Generation    int       `json:"generation" gorm:"not null"`
	ReasonCode    string    `json:"reason_code" gorm:"type:varchar(64);not null"`
	PayloadJSON   string    `json:"-" gorm:"type:text;not null"`
	PayloadSHA256 string    `json:"payload_sha256" gorm:"type:char(64);not null"`
	OccurredAt    time.Time `json:"occurred_at" gorm:"not null"`
	CreatedAt     time.Time `json:"created_at"`
}

func (PlatformProviderAlertEvent) TableName() string {
	return "platform_provider_alert_events"
}

func (*PlatformProviderAlertEvent) BeforeUpdate(*gorm.DB) error {
	return ErrPlatformProviderImmutableEvent
}

func (*PlatformProviderAlertEvent) BeforeDelete(*gorm.DB) error {
	return ErrPlatformProviderImmutableEvent
}

type PlatformProviderRetirementAcknowledgement struct {
	ProviderName      string    `json:"provider_name" gorm:"type:varchar(64);primaryKey"`
	AcknowledgementID string    `json:"acknowledgement_id" gorm:"type:varchar(36);not null;uniqueIndex"`
	Reference         string    `json:"reference" gorm:"type:varchar(240);not null"`
	ReasonCode        string    `json:"reason_code" gorm:"type:varchar(64);not null"`
	AcknowledgedAt    time.Time `json:"acknowledged_at" gorm:"not null"`
	CreatedAt         time.Time `json:"created_at"`
}

func (PlatformProviderRetirementAcknowledgement) TableName() string {
	return "platform_provider_retirement_acknowledgements"
}

func (*PlatformProviderRetirementAcknowledgement) BeforeUpdate(*gorm.DB) error {
	return ErrPlatformProviderImmutableEvent
}

func (*PlatformProviderRetirementAcknowledgement) BeforeDelete(*gorm.DB) error {
	return ErrPlatformProviderImmutableEvent
}

type PlatformProviderRouteObservation struct {
	RouteID        int64
	Probed         bool
	Status         string
	FailureCode    string
	ProviderCaused bool
}

type PlatformProviderIncidentDecision struct {
	ProviderName           string
	Kind                   string
	DesiredActive          bool
	ReasonCode             string
	SampleSize             int
	SuccessCount           int
	AffectedRoutes         int
	TotalRoutes            int
	SuccessRateBasisPoints int
}

type PlatformProviderMonitorEvaluationSnapshot struct {
	DatabaseTime time.Time
	Health       []PlatformProviderRouteHealth
	Outcomes     []PlatformProviderTerminalOutcome
	Incidents    []PlatformProviderIncident
	Retirements  []PlatformProviderRetirementAcknowledgement
}

func ClaimPlatformProviderMonitorLease(ownerID string, lease time.Duration) (*PlatformProviderMonitorLeaseClaim, error) {
	if ownerID == "" || strings.TrimSpace(ownerID) != ownerID || len(ownerID) > 128 {
		return nil, fmt.Errorf("provider monitor owner id is invalid")
	}
	if lease < 5*time.Second {
		return nil, fmt.Errorf("provider monitor lease must be at least five seconds")
	}
	var claim *PlatformProviderMonitorLeaseClaim
	err := DB.Transaction(func(tx *gorm.DB) error {
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		seed := PlatformProviderMonitorLease{
			Name:      platformProviderMonitorLeaseName,
			OwnerID:   "",
			TokenHash: "",
			CreatedAt: now,
			UpdatedAt: now,
		}
		if err := tx.Clauses(clause.OnConflict{DoNothing: true}).Create(&seed).Error; err != nil {
			return err
		}
		var current PlatformProviderMonitorLease
		if err := lockForUpdate(tx.Where("name = ?", platformProviderMonitorLeaseName)).First(&current).Error; err != nil {
			return err
		}
		if current.TokenHash != "" && current.ExpiresAt != nil && now.Before(current.ExpiresAt.UTC()) {
			return ErrPlatformProviderMonitorLeaseHeld
		}
		token := uuid.NewString()
		expiresAt := now.Add(lease)
		result := tx.Model(&PlatformProviderMonitorLease{}).Where("name = ?", platformProviderMonitorLeaseName).Updates(map[string]any{
			"owner_id":          ownerID,
			"token_hash":        platformProviderMonitorTokenHash(token),
			"acquired_at":       now,
			"expires_at":        expiresAt,
			"last_heartbeat_at": now,
			"last_started_at":   now,
			"last_error_code":   "",
			"updated_at":        now,
		})
		if result.Error != nil {
			return result.Error
		}
		if result.RowsAffected != 1 {
			return ErrPlatformProviderMonitorLeaseLost
		}
		claim = &PlatformProviderMonitorLeaseClaim{OwnerID: ownerID, Token: token, ExpiresAt: expiresAt}
		return nil
	})
	return claim, err
}

func RenewPlatformProviderMonitorLease(token string, lease time.Duration) (time.Time, bool, error) {
	if token == "" || lease < 5*time.Second {
		return time.Time{}, false, fmt.Errorf("provider monitor lease renewal is invalid")
	}
	var expiresAt time.Time
	won := false
	err := DB.Transaction(func(tx *gorm.DB) error {
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		expiresAt = now.Add(lease)
		result := tx.Model(&PlatformProviderMonitorLease{}).Where(
			"name = ? AND token_hash = ? AND expires_at > ?",
			platformProviderMonitorLeaseName,
			platformProviderMonitorTokenHash(token),
			now,
		).Updates(map[string]any{
			"expires_at":        expiresAt,
			"last_heartbeat_at": now,
			"updated_at":        now,
		})
		won = result.RowsAffected == 1
		return result.Error
	})
	return expiresAt, won, err
}

func CompletePlatformProviderMonitorCycle(token string, errorCode string) (bool, error) {
	if token == "" || (errorCode != "" && !platformProviderSafeCodePattern.MatchString(errorCode)) {
		return false, fmt.Errorf("provider monitor completion is invalid")
	}
	won := false
	err := DB.Transaction(func(tx *gorm.DB) error {
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		updates := map[string]any{
			"last_heartbeat_at": now,
			"last_error_code":   errorCode,
			"updated_at":        now,
		}
		if errorCode == "" {
			updates["last_completed_at"] = now
		}
		result := tx.Model(&PlatformProviderMonitorLease{}).Where(
			"name = ? AND token_hash = ? AND expires_at > ?",
			platformProviderMonitorLeaseName,
			platformProviderMonitorTokenHash(token),
			now,
		).Updates(updates)
		won = result.RowsAffected == 1
		return result.Error
	})
	return won, err
}

func ReleasePlatformProviderMonitorLease(token string) (bool, error) {
	if token == "" {
		return false, nil
	}
	won := false
	err := DB.Transaction(func(tx *gorm.DB) error {
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		result := tx.Model(&PlatformProviderMonitorLease{}).Where(
			"name = ? AND token_hash = ?",
			platformProviderMonitorLeaseName,
			platformProviderMonitorTokenHash(token),
		).Updates(map[string]any{
			"owner_id":          "",
			"token_hash":        "",
			"expires_at":        nil,
			"last_heartbeat_at": now,
			"updated_at":        now,
		})
		won = result.RowsAffected == 1
		return result.Error
	})
	return won, err
}

func ListPlatformGenerationProviderRoutesForMonitoring() ([]PlatformGenerationProviderRoute, error) {
	var routes []PlatformGenerationProviderRoute
	err := DB.Where("provider_name <> ?", "").Order("provider_name ASC, route_key ASC, id ASC").Find(&routes).Error
	return routes, err
}

// ApplyPlatformProviderRouteObservations changes health only for explicit
// probes. An unprobed route is created as unknown and never treated as a
// recovery signal.
func ApplyPlatformProviderRouteObservations(token string, observations []PlatformProviderRouteObservation) error {
	if token == "" {
		return ErrPlatformProviderMonitorLeaseLost
	}
	return DB.Transaction(func(tx *gorm.DB) error {
		now, err := requirePlatformProviderMonitorFenceTx(tx, token)
		if err != nil {
			return err
		}
		seen := make(map[int64]struct{}, len(observations))
		for _, observation := range observations {
			if observation.RouteID <= 0 {
				return fmt.Errorf("provider route observation route id is invalid")
			}
			if _, duplicate := seen[observation.RouteID]; duplicate {
				return fmt.Errorf("provider route observation is duplicated")
			}
			seen[observation.RouteID] = struct{}{}

			var route PlatformGenerationProviderRoute
			if err := lockForUpdate(tx.Where("id = ?", observation.RouteID)).First(&route).Error; err != nil {
				return err
			}
			var health PlatformProviderRouteHealth
			err := lockForUpdate(tx.Where("route_id = ?", route.ID)).First(&health).Error
			if errors.Is(err, gorm.ErrRecordNotFound) {
				health = PlatformProviderRouteHealth{
					RouteID:      route.ID,
					RouteKey:     route.RouteKey,
					ProviderName: route.ProviderName,
					ChannelID:    route.ChannelID,
					ChannelClass: route.ChannelClass,
					RouteEnabled: route.Enabled,
					Status:       PlatformProviderRouteHealthUnknown,
					CreatedAt:    now,
					UpdatedAt:    now,
				}
				if err := tx.Create(&health).Error; err != nil {
					return err
				}
			} else if err != nil {
				return err
			} else if health.RouteKey != route.RouteKey || health.ProviderName != route.ProviderName {
				return fmt.Errorf("provider route %d identity changed after monitoring admission", route.ID)
			}

			updates := map[string]any{
				"route_enabled": route.Enabled,
				"updated_at":    now,
			}
			if observation.Probed {
				if err := validatePlatformProviderRouteObservation(observation); err != nil {
					return err
				}
				updates["status"] = observation.Status
				updates["failure_code"] = observation.FailureCode
				updates["failure_provider_caused"] = observation.ProviderCaused
				updates["last_probe_at"] = now
				if observation.Status == PlatformProviderRouteHealthHealthy {
					updates["consecutive_successes"] = health.ConsecutiveSuccesses + 1
					updates["consecutive_failures"] = 0
					updates["last_success_at"] = now
				} else {
					updates["consecutive_successes"] = 0
					updates["consecutive_failures"] = health.ConsecutiveFailures + 1
					updates["last_failure_at"] = now
				}
			}
			if err := tx.Model(&PlatformProviderRouteHealth{}).Where("route_id = ?", route.ID).Updates(updates).Error; err != nil {
				return err
			}
		}
		return nil
	})
}

func CreatePlatformProviderTerminalOutcome(outcome *PlatformProviderTerminalOutcome) (bool, error) {
	if err := validatePlatformProviderTerminalOutcome(outcome); err != nil {
		return false, err
	}
	created := false
	err := DB.Transaction(func(tx *gorm.DB) error {
		var err error
		created, err = createPlatformProviderTerminalOutcomeTx(tx, outcome)
		return err
	})
	return created, err
}

// createPlatformProviderTerminalOutcomeTx lets the generation terminal state,
// provider-slot release, and immutable monitoring observation commit as one
// database transaction. Callers must validate their own fencing predicate
// before invoking it.
func createPlatformProviderTerminalOutcomeTx(tx *gorm.DB, outcome *PlatformProviderTerminalOutcome) (bool, error) {
	if tx == nil {
		return false, fmt.Errorf("provider outcome transaction is required")
	}
	if err := validatePlatformProviderTerminalOutcome(outcome); err != nil {
		return false, err
	}
	var route PlatformGenerationProviderRoute
	if err := tx.Where("id = ?", outcome.RouteID).First(&route).Error; err != nil {
		return false, err
	}
	outcome.RouteKey = route.RouteKey
	outcome.ProviderName = route.ProviderName
	outcome.ChannelClass = route.ChannelClass
	outcome.OccurredAt = outcome.OccurredAt.UTC()
	now, err := GetDBTimeTx(tx)
	if err != nil {
		return false, err
	}
	outcome.CreatedAt = now
	result := tx.Clauses(clause.OnConflict{DoNothing: true}).Create(outcome)
	if result.Error != nil {
		return false, result.Error
	}
	if result.RowsAffected == 0 {
		var existing PlatformProviderTerminalOutcome
		if err := tx.Where("id = ?", outcome.ID).First(&existing).Error; err != nil {
			return false, err
		}
		if !platformProviderTerminalOutcomesEqual(existing, *outcome) {
			return false, ErrPlatformProviderTerminalOutcomeCollision
		}
		*outcome = existing
		return false, nil
	}
	if !outcome.AccountInvalidated {
		return true, nil
	}
	return true, upsertPlatformProviderInvalidatedHealthTx(tx, route, outcome.FailureCode, now)
}

func AcknowledgePlatformProviderRetirement(
	providerName string,
	acknowledgementID string,
	reference string,
	reasonCode string,
) (bool, error) {
	if !platformProviderNameValid(providerName) {
		return false, fmt.Errorf("retired provider name is invalid")
	}
	if parsed, err := uuid.Parse(acknowledgementID); err != nil || parsed.String() != acknowledgementID {
		return false, fmt.Errorf("retired provider acknowledgement id is invalid")
	}
	if reference == "" || strings.TrimSpace(reference) != reference || len(reference) > 240 || !platformProviderSafeCodePattern.MatchString(reasonCode) {
		return false, fmt.Errorf("retired provider acknowledgement is invalid")
	}
	created := false
	err := DB.Transaction(func(tx *gorm.DB) error {
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		acknowledgement := PlatformProviderRetirementAcknowledgement{
			ProviderName:      providerName,
			AcknowledgementID: acknowledgementID,
			Reference:         reference,
			ReasonCode:        reasonCode,
			AcknowledgedAt:    now,
			CreatedAt:         now,
		}
		result := tx.Clauses(clause.OnConflict{DoNothing: true}).Create(&acknowledgement)
		if result.Error != nil {
			return result.Error
		}
		if result.RowsAffected == 1 {
			created = true
			return nil
		}
		var existing PlatformProviderRetirementAcknowledgement
		if err := tx.Where("provider_name = ? OR acknowledgement_id = ?", providerName, acknowledgementID).First(&existing).Error; err != nil {
			return err
		}
		if existing.AcknowledgementID != acknowledgementID || existing.Reference != reference || existing.ReasonCode != reasonCode {
			return ErrPlatformProviderRetirementCollision
		}
		return nil
	})
	return created, err
}

func GetPlatformProviderMonitorEvaluationSnapshot(lookback time.Duration) (PlatformProviderMonitorEvaluationSnapshot, error) {
	var snapshot PlatformProviderMonitorEvaluationSnapshot
	if lookback < time.Minute || lookback > 30*24*time.Hour {
		return snapshot, fmt.Errorf("provider monitor lookback is invalid")
	}
	err := DB.Transaction(func(tx *gorm.DB) error {
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		snapshot.DatabaseTime = now
		if err := tx.Order("provider_name ASC, route_id ASC").Find(&snapshot.Health).Error; err != nil {
			return err
		}
		if err := tx.Where("occurred_at >= ?", now.Add(-lookback)).Order("occurred_at ASC, id ASC").Find(&snapshot.Outcomes).Error; err != nil {
			return err
		}
		if err := tx.Order("provider_name ASC, kind ASC").Find(&snapshot.Incidents).Error; err != nil {
			return err
		}
		return tx.Order("provider_name ASC").Find(&snapshot.Retirements).Error
	})
	return snapshot, err
}

func ApplyPlatformProviderIncidentDecisions(token string, decisions []PlatformProviderIncidentDecision) error {
	if token == "" {
		return ErrPlatformProviderMonitorLeaseLost
	}
	sort.Slice(decisions, func(i, j int) bool {
		if decisions[i].ProviderName == decisions[j].ProviderName {
			return decisions[i].Kind < decisions[j].Kind
		}
		return decisions[i].ProviderName < decisions[j].ProviderName
	})
	return DB.Transaction(func(tx *gorm.DB) error {
		now, err := requirePlatformProviderMonitorFenceTx(tx, token)
		if err != nil {
			return err
		}
		seen := make(map[string]struct{}, len(decisions))
		for _, decision := range decisions {
			if err := validatePlatformProviderIncidentDecision(decision); err != nil {
				return err
			}
			key := decision.ProviderName + "\x00" + decision.Kind
			if _, duplicate := seen[key]; duplicate {
				return fmt.Errorf("provider incident decision is duplicated")
			}
			seen[key] = struct{}{}

			var incident PlatformProviderIncident
			err := lockForUpdate(tx.Where("provider_name = ? AND kind = ?", decision.ProviderName, decision.Kind)).First(&incident).Error
			if errors.Is(err, gorm.ErrRecordNotFound) {
				if !decision.DesiredActive {
					continue
				}
				incident = PlatformProviderIncident{
					ProviderName: decision.ProviderName,
					Kind:         decision.Kind,
					CreatedAt:    now,
				}
			} else if err != nil {
				return err
			}

			transition := ""
			if decision.DesiredActive && !incident.Active {
				incident.Generation++
				transition = PlatformProviderIncidentTriggered
			} else if !decision.DesiredActive && incident.Active {
				transition = PlatformProviderIncidentRecovered
			}
			incident.Active = decision.DesiredActive
			incident.ReasonCode = decision.ReasonCode
			incident.SampleSize = decision.SampleSize
			incident.SuccessCount = decision.SuccessCount
			incident.AffectedRoutes = decision.AffectedRoutes
			incident.TotalRoutes = decision.TotalRoutes
			incident.SuccessRateBasisPoints = decision.SuccessRateBasisPoints
			incident.LastEvaluatedAt = now
			incident.UpdatedAt = now
			if transition == PlatformProviderIncidentTriggered {
				incident.TriggeredAt = &now
				incident.RecoveredAt = nil
			} else if transition == PlatformProviderIncidentRecovered {
				incident.RecoveredAt = &now
			}

			if incident.ID == 0 {
				if err := tx.Create(&incident).Error; err != nil {
					return err
				}
			} else if err := tx.Save(&incident).Error; err != nil {
				return err
			}
			if transition != "" {
				if err := createPlatformProviderAlertEventTx(tx, incident, transition, now); err != nil {
					return err
				}
			}
		}
		return nil
	})
}

func GetPlatformProviderMonitorLease() (*PlatformProviderMonitorLease, error) {
	var lease PlatformProviderMonitorLease
	err := DB.Where("name = ?", platformProviderMonitorLeaseName).First(&lease).Error
	return &lease, err
}

func GetPlatformProviderAlertEvent(eventID string) (*PlatformProviderAlertEvent, error) {
	var event PlatformProviderAlertEvent
	err := DB.Where("id = ?", eventID).First(&event).Error
	return &event, err
}

func requirePlatformProviderMonitorFenceTx(tx *gorm.DB, token string) (time.Time, error) {
	now, err := GetDBTimeTx(tx)
	if err != nil {
		return time.Time{}, err
	}
	var lease PlatformProviderMonitorLease
	err = lockForUpdate(tx.Where(
		"name = ? AND token_hash = ? AND expires_at > ?",
		platformProviderMonitorLeaseName,
		platformProviderMonitorTokenHash(token),
		now,
	)).First(&lease).Error
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return time.Time{}, ErrPlatformProviderMonitorLeaseLost
	}
	return now, err
}

func validatePlatformProviderRouteObservation(observation PlatformProviderRouteObservation) error {
	switch observation.Status {
	case PlatformProviderRouteHealthHealthy:
		if observation.FailureCode != "" || observation.ProviderCaused {
			return fmt.Errorf("healthy provider route observation cannot carry failure metadata")
		}
	case PlatformProviderRouteHealthFailed, PlatformProviderRouteHealthInvalidated:
		if !platformProviderSafeCodePattern.MatchString(observation.FailureCode) {
			return fmt.Errorf("provider route observation failure code is invalid")
		}
		if observation.Status == PlatformProviderRouteHealthInvalidated && !observation.ProviderCaused {
			return fmt.Errorf("provider route account invalidation must be provider-caused")
		}
	default:
		return fmt.Errorf("provider route observation status is invalid")
	}
	return nil
}

func validatePlatformProviderTerminalOutcome(outcome *PlatformProviderTerminalOutcome) error {
	if outcome == nil {
		return fmt.Errorf("provider terminal outcome is required")
	}
	if parsed, err := uuid.Parse(outcome.ID); err != nil || parsed.String() != outcome.ID || outcome.RouteID <= 0 {
		return fmt.Errorf("provider terminal outcome identity is invalid")
	}
	if parsed, err := uuid.Parse(outcome.RelayJobID); err != nil || parsed.String() != outcome.RelayJobID {
		return fmt.Errorf("provider terminal outcome Relay job id is invalid")
	}
	if outcome.OccurredAt.IsZero() || outcome.ExternalReference == "" || strings.TrimSpace(outcome.ExternalReference) != outcome.ExternalReference || len(outcome.ExternalReference) > 240 {
		return fmt.Errorf("provider terminal outcome occurrence is invalid")
	}
	switch outcome.Outcome {
	case PlatformProviderOutcomeSucceeded:
		if outcome.FailureOwner != PlatformProviderFailureOwnerNone || outcome.FailureCode != "" || outcome.AccountInvalidated {
			return fmt.Errorf("successful provider terminal outcome has failure metadata")
		}
	case PlatformProviderOutcomeFailed:
		if outcome.FailureOwner != PlatformProviderFailureOwnerProvider && outcome.FailureOwner != PlatformProviderFailureOwnerClient && outcome.FailureOwner != PlatformProviderFailureOwnerRelay {
			return fmt.Errorf("provider terminal outcome failure owner is invalid")
		}
		if !platformProviderSafeCodePattern.MatchString(outcome.FailureCode) {
			return fmt.Errorf("provider terminal outcome failure code is invalid")
		}
		if outcome.AccountInvalidated && outcome.FailureOwner != PlatformProviderFailureOwnerProvider {
			return fmt.Errorf("provider terminal account invalidation must be provider-caused")
		}
	default:
		return fmt.Errorf("provider terminal outcome is invalid")
	}
	return nil
}

func platformProviderTerminalOutcomesEqual(left PlatformProviderTerminalOutcome, right PlatformProviderTerminalOutcome) bool {
	return left.ID == right.ID && left.RouteID == right.RouteID && left.RouteKey == right.RouteKey &&
		left.ProviderName == right.ProviderName && left.ChannelClass == right.ChannelClass &&
		left.RelayJobID == right.RelayJobID && left.Outcome == right.Outcome &&
		left.FailureOwner == right.FailureOwner && left.FailureCode == right.FailureCode &&
		left.AccountInvalidated == right.AccountInvalidated && left.OccurredAt.UTC().Equal(right.OccurredAt.UTC()) &&
		left.ExternalReference == right.ExternalReference
}

func upsertPlatformProviderInvalidatedHealthTx(
	tx *gorm.DB,
	route PlatformGenerationProviderRoute,
	failureCode string,
	now time.Time,
) error {
	health := PlatformProviderRouteHealth{
		RouteID:               route.ID,
		RouteKey:              route.RouteKey,
		ProviderName:          route.ProviderName,
		ChannelID:             route.ChannelID,
		ChannelClass:          route.ChannelClass,
		RouteEnabled:          route.Enabled,
		Status:                PlatformProviderRouteHealthInvalidated,
		FailureCode:           failureCode,
		FailureProviderCaused: true,
		ConsecutiveFailures:   1,
		LastFailureAt:         &now,
		CreatedAt:             now,
		UpdatedAt:             now,
	}
	return tx.Clauses(clause.OnConflict{
		Columns: []clause.Column{{Name: "route_id"}},
		DoUpdates: clause.Assignments(map[string]any{
			"route_enabled":           route.Enabled,
			"status":                  PlatformProviderRouteHealthInvalidated,
			"failure_code":            failureCode,
			"failure_provider_caused": true,
			"consecutive_successes":   0,
			"consecutive_failures":    gorm.Expr("consecutive_failures + 1"),
			"last_failure_at":         now,
			"updated_at":              now,
		}),
	}).Create(&health).Error
}

func validatePlatformProviderIncidentDecision(decision PlatformProviderIncidentDecision) error {
	if !platformProviderNameValid(decision.ProviderName) || !platformProviderSafeCodePattern.MatchString(decision.ReasonCode) {
		return fmt.Errorf("provider incident decision identity is invalid")
	}
	switch decision.Kind {
	case PlatformProviderIncidentSuccessRateDrop,
		PlatformProviderIncidentWidespreadFailure,
		PlatformProviderIncidentBatchInvalidation:
	default:
		return fmt.Errorf("provider incident decision kind is invalid")
	}
	if decision.SampleSize < 0 || decision.SuccessCount < 0 || decision.SuccessCount > decision.SampleSize ||
		decision.AffectedRoutes < 0 || decision.TotalRoutes < 0 || decision.AffectedRoutes > decision.TotalRoutes ||
		decision.SuccessRateBasisPoints < 0 || decision.SuccessRateBasisPoints > 10_000 {
		return fmt.Errorf("provider incident decision metrics are invalid")
	}
	return nil
}

func createPlatformProviderAlertEventTx(
	tx *gorm.DB,
	incident PlatformProviderIncident,
	transition string,
	now time.Time,
) error {
	transitionIdentity := fmt.Sprintf("provider:%s:%s:%d:%s", incident.ProviderName, incident.Kind, incident.Generation, transition)
	transitionDigest := sha256.Sum256([]byte(transitionIdentity))
	transitionKey := fmt.Sprintf("%x", transitionDigest)
	eventID := uuid.NewSHA1(uuid.NameSpaceURL, []byte("ai-video-relay:"+transitionIdentity)).String()
	payload := struct {
		SchemaVersion int       `json:"schema_version"`
		EventID       string    `json:"event_id"`
		Type          string    `json:"type"`
		OccurredAt    time.Time `json:"occurred_at"`
		Incident      struct {
			Kind                   string `json:"kind"`
			State                  string `json:"state"`
			ProviderName           string `json:"provider_name"`
			Generation             int    `json:"generation"`
			ReasonCode             string `json:"reason_code"`
			SampleSize             int    `json:"sample_size"`
			SuccessCount           int    `json:"success_count"`
			AffectedRoutes         int    `json:"affected_routes"`
			TotalRoutes            int    `json:"total_routes"`
			SuccessRateBasisPoints int    `json:"success_rate_basis_points"`
		} `json:"incident"`
	}{
		SchemaVersion: 1,
		EventID:       eventID,
		Type:          "provider_monitor." + incident.Kind + "." + transition,
		OccurredAt:    now,
	}
	payload.Incident.Kind = incident.Kind
	payload.Incident.State = transition
	payload.Incident.ProviderName = incident.ProviderName
	payload.Incident.Generation = incident.Generation
	payload.Incident.ReasonCode = incident.ReasonCode
	payload.Incident.SampleSize = incident.SampleSize
	payload.Incident.SuccessCount = incident.SuccessCount
	payload.Incident.AffectedRoutes = incident.AffectedRoutes
	payload.Incident.TotalRoutes = incident.TotalRoutes
	payload.Incident.SuccessRateBasisPoints = incident.SuccessRateBasisPoints
	payloadJSON, err := common.Marshal(payload)
	if err != nil {
		return err
	}
	digest := sha256.Sum256(payloadJSON)
	event := PlatformProviderAlertEvent{
		ID:            eventID,
		TransitionKey: transitionKey,
		ProviderName:  incident.ProviderName,
		IncidentKind:  incident.Kind,
		IncidentState: transition,
		Generation:    incident.Generation,
		ReasonCode:    incident.ReasonCode,
		PayloadJSON:   string(payloadJSON),
		PayloadSHA256: fmt.Sprintf("%x", digest),
		OccurredAt:    now,
		CreatedAt:     now,
	}
	result := tx.Clauses(clause.OnConflict{DoNothing: true}).Create(&event)
	if result.Error != nil {
		return result.Error
	}
	if result.RowsAffected == 0 {
		var existing PlatformProviderAlertEvent
		if err := tx.Where("id = ?", event.ID).First(&existing).Error; err != nil {
			return err
		}
		if existing.TransitionKey != event.TransitionKey || existing.PayloadSHA256 != event.PayloadSHA256 || existing.PayloadJSON != event.PayloadJSON {
			return fmt.Errorf("provider alert event collision")
		}
	}
	_, err = CreatePlatformRelayExternalDeliveryTx(
		tx,
		PlatformRelayDeliveryKindProviderAlert,
		event.ID,
		"relay-provider-alert-"+event.ID,
		DefaultPlatformRelayDeliveryMaxAttempts,
	)
	return err
}

func platformProviderMonitorTokenHash(token string) string {
	digest := sha256.Sum256([]byte(token))
	return fmt.Sprintf("%x", digest)
}

func platformProviderMonitorTokenMatches(storedHash string, token string) bool {
	if len(storedHash) != 64 || token == "" {
		return false
	}
	actual := platformProviderMonitorTokenHash(token)
	return subtle.ConstantTimeCompare([]byte(storedHash), []byte(actual)) == 1
}

func platformProviderNameValid(providerName string) bool {
	return providerName != "" && strings.TrimSpace(providerName) == providerName && len(providerName) <= 64
}
