package model

import (
	"crypto/subtle"
	"encoding/hex"
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/google/uuid"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

const (
	PlatformGenerationChannelClassReverseEngineered = "reverse"
	PlatformGenerationChannelClassThirdPartyAPI     = "third_party_api"
	PlatformGenerationChannelClassOfficialProvider  = "official"
)

const (
	PlatformGenerationRouteAdmissionSelecting = "selecting"
	PlatformGenerationRouteAdmissionHeld      = "held"
	PlatformGenerationRouteAdmissionPosting   = "posting"
	PlatformGenerationRouteAdmissionUnknown   = "unknown"
	PlatformGenerationRouteAdmissionReleased  = "released"
	PlatformGenerationRouteAdmissionFinished  = "finished"
)

var (
	ErrPlatformGenerationProviderRouteUnavailable = errors.New("no generation provider route is available")
	ErrPlatformGenerationProviderRouteBusy        = errors.New("all generation provider routes are at active-task capacity")
	ErrPlatformGenerationProviderRouteRateLimited = errors.New("all generation provider routes with capacity are RPM limited")
	ErrPlatformGenerationRouteAdmissionHeld       = errors.New("generation job already holds a provider route")
	ErrPlatformGenerationRouteAdmissionUnknown    = errors.New("generation job has an unknown provider submission outcome")
	ErrPlatformGenerationRouteAdmissionFinished   = errors.New("generation job provider route admission is already finished")
)

// PlatformGenerationProviderRoute is one stable provider account route. A
// multi-key new-api channel must materialize one row per key index so a task
// can remain pinned to the same real account after submission.
//
// KeyFingerprint is a lowercase SHA-256 digest used to detect credential
// rotation. It is part of the stable identity: rotation creates a new route
// and disables the old route instead of mutating a route held by existing
// tasks. Provider credentials are deliberately never stored in this table.
type PlatformGenerationProviderRoute struct {
	ID           int64  `json:"id" gorm:"primaryKey"`
	RouteKey     string `json:"route_key" gorm:"type:varchar(120);not null;uniqueIndex:uniq_platform_generation_route_key,priority:1"`
	Model        string `json:"model" gorm:"type:varchar(128);not null;index:idx_platform_generation_route_lookup,priority:1;uniqueIndex:uniq_platform_generation_route,priority:1"`
	Mode         string `json:"mode" gorm:"type:varchar(32);not null;index:idx_platform_generation_route_lookup,priority:2;uniqueIndex:uniq_platform_generation_route,priority:2;uniqueIndex:uniq_platform_generation_route_key,priority:2"`
	ProviderName string `json:"provider_name" gorm:"type:varchar(64);not null;index"`
	AccountID    string `json:"account_id" gorm:"type:varchar(128);not null"`
	ChannelID    int    `json:"channel_id" gorm:"not null;index;uniqueIndex:uniq_platform_generation_route,priority:3"`
	// AcceptedChannelType is the exact native adapter type covered by the
	// route's offline acceptance evidence. Admission compares it with the
	// locked Channel row every time; it is not inferred from mutable runtime
	// configuration. Zero is retained only as a legacy-migration sentinel and
	// is never eligible for new work.
	AcceptedChannelType int        `json:"accepted_channel_type" gorm:"not null;default:0"`
	KeyIndex            int        `json:"key_index" gorm:"not null;uniqueIndex:uniq_platform_generation_route,priority:4"`
	KeyFingerprint      string     `json:"key_fingerprint" gorm:"type:char(64);not null;uniqueIndex:uniq_platform_generation_route,priority:5"`
	AccountStateID      int64      `json:"account_state_id" gorm:"not null;default:0;index;index:idx_platform_generation_route_lookup,priority:4"`
	ChannelClass        string     `json:"channel_class" gorm:"type:varchar(32);not null"`
	UpstreamModel       string     `json:"upstream_model" gorm:"type:varchar(128);not null"`
	StagingReady        bool       `json:"staging_ready" gorm:"not null;default:false"`
	ProductionReady     bool       `json:"production_ready" gorm:"not null"`
	Enabled             bool       `json:"enabled" gorm:"not null;index:idx_platform_generation_route_lookup,priority:3"`
	CoolingUntil        *time.Time `json:"cooling_until,omitempty" gorm:"index"`
	ConsecutiveFailures int        `json:"consecutive_failures" gorm:"not null"`
	LastFailureAt       *time.Time `json:"last_failure_at,omitempty"`
	LastSuccessAt       *time.Time `json:"last_success_at,omitempty"`
	LastErrorCode       string     `json:"last_error_code" gorm:"type:varchar(64);not null"`

	RPMWindowSeconds   int        `json:"rpm_window_seconds" gorm:"not null"`
	RPMLimit           int        `json:"rpm_limit" gorm:"not null"`
	RPMWindowStartedAt *time.Time `json:"rpm_window_started_at,omitempty"`
	RPMWindowCount     int        `json:"rpm_window_count" gorm:"not null"`
	ActiveCount        int        `json:"active_count" gorm:"not null"`
	ActiveLimit        int        `json:"active_limit" gorm:"not null"`

	CreatedAt time.Time `json:"created_at"`
	UpdatedAt time.Time `json:"updated_at"`
}

// PlatformGenerationProviderAccountState owns admission state for one real
// provider account. A channel/key can be exposed by several model/mode route
// rows, but RPM, active capacity, cooldowns, and failure history must be shared
// by that physical identity rather than multiplied by the number of routes.
//
// The credential itself is never persisted here. KeyFingerprint makes a key
// rotation a new physical identity while existing admissions remain pinned to
// the old route and credential snapshot.
type PlatformGenerationProviderAccountState struct {
	ID             int64  `json:"id" gorm:"primaryKey"`
	ChannelID      int    `json:"channel_id" gorm:"not null;uniqueIndex:uniq_platform_generation_provider_account,priority:1"`
	KeyIndex       int    `json:"key_index" gorm:"not null;uniqueIndex:uniq_platform_generation_provider_account,priority:2"`
	KeyFingerprint string `json:"key_fingerprint" gorm:"type:char(64);not null;uniqueIndex:uniq_platform_generation_provider_account,priority:3"`

	RPMWindowSeconds   int        `json:"rpm_window_seconds" gorm:"not null"`
	RPMLimit           int        `json:"rpm_limit" gorm:"not null"`
	RPMWindowStartedAt *time.Time `json:"rpm_window_started_at,omitempty"`
	RPMWindowCount     int        `json:"rpm_window_count" gorm:"not null"`
	ActiveCount        int        `json:"active_count" gorm:"not null"`
	ActiveLimit        int        `json:"active_limit" gorm:"not null"`

	CoolingUntil        *time.Time `json:"cooling_until,omitempty" gorm:"index"`
	ConsecutiveFailures int        `json:"consecutive_failures" gorm:"not null"`
	LastFailureAt       *time.Time `json:"last_failure_at,omitempty"`
	LastSuccessAt       *time.Time `json:"last_success_at,omitempty"`
	LastErrorCode       string     `json:"last_error_code" gorm:"type:varchar(64);not null"`

	CreatedAt time.Time `json:"created_at"`
	UpdatedAt time.Time `json:"updated_at"`
}

// PlatformGenerationRouteAdmission is the durable route assignment for one
// generation job. JobID is unique so concurrent workers cannot occupy two
// provider slots for the same job. A released (proven non-created) admission
// may be reused for a later failover attempt with a new fencing token.
type PlatformGenerationRouteAdmission struct {
	ID                  int64      `json:"id" gorm:"primaryKey"`
	JobID               string     `json:"job_id" gorm:"type:varchar(36);not null;uniqueIndex"`
	RouteID             int64      `json:"route_id" gorm:"not null;index"`
	SubmissionTokenHash string     `json:"-" gorm:"type:char(64);not null"`
	State               string     `json:"state" gorm:"type:varchar(16);not null;index"`
	SlotHeld            bool       `json:"slot_held" gorm:"not null;index"`
	Attempt             int        `json:"attempt" gorm:"not null"`
	UnknownAt           *time.Time `json:"unknown_at,omitempty"`
	ClosedAt            *time.Time `json:"closed_at,omitempty"`
	CreatedAt           time.Time  `json:"created_at"`
	UpdatedAt           time.Time  `json:"updated_at"`
}

type PlatformGenerationRouteClaim struct {
	AdmissionID     int64                           `json:"admission_id"`
	JobID           string                          `json:"job_id"`
	Route           PlatformGenerationProviderRoute `json:"route"`
	Attempt         int                             `json:"attempt"`
	SubmissionToken string                          `json:"-"`
}

func (route *PlatformGenerationProviderRoute) Validate() error {
	if route == nil {
		return errors.New("generation provider route is required")
	}
	if route.RouteKey == "" || strings.TrimSpace(route.RouteKey) != route.RouteKey || len(route.RouteKey) > 120 {
		return errors.New("generation provider route key is invalid")
	}
	if route.Model == "" || strings.TrimSpace(route.Model) != route.Model || len(route.Model) > 128 {
		return errors.New("generation provider route model is invalid")
	}
	if route.Mode == "" || strings.TrimSpace(route.Mode) != route.Mode || len(route.Mode) > 32 {
		return errors.New("generation provider route mode is invalid")
	}
	if route.ChannelID <= 0 {
		return errors.New("generation provider route channel_id must be positive")
	}
	if route.AcceptedChannelType < constant.ChannelTypeUnknown || route.AcceptedChannelType >= constant.ChannelTypeDummy {
		return errors.New("generation provider route accepted_channel_type is invalid")
	}
	if route.ProviderName == "" || strings.TrimSpace(route.ProviderName) != route.ProviderName || len(route.ProviderName) > 64 ||
		route.AccountID == "" || strings.TrimSpace(route.AccountID) != route.AccountID || len(route.AccountID) > 128 ||
		route.UpstreamModel == "" || strings.TrimSpace(route.UpstreamModel) != route.UpstreamModel || len(route.UpstreamModel) > 128 {
		return errors.New("generation provider route identity is invalid")
	}
	if route.KeyIndex < 0 {
		return errors.New("generation provider route key_index must not be negative")
	}
	decodedFingerprint, err := hex.DecodeString(route.KeyFingerprint)
	if err != nil || len(decodedFingerprint) != 32 || strings.ToLower(route.KeyFingerprint) != route.KeyFingerprint {
		return errors.New("generation provider route key_fingerprint must be a lowercase SHA-256 digest")
	}
	switch route.ChannelClass {
	case PlatformGenerationChannelClassReverseEngineered,
		PlatformGenerationChannelClassThirdPartyAPI,
		PlatformGenerationChannelClassOfficialProvider:
	default:
		return errors.New("generation provider route channel_class is invalid")
	}
	if route.RPMWindowSeconds <= 0 {
		return errors.New("generation provider route rpm_window_seconds must be positive")
	}
	if route.RPMLimit <= 0 {
		return errors.New("generation provider route rpm_limit must be positive")
	}
	if route.ActiveLimit <= 0 {
		return errors.New("generation provider route active_limit must be positive")
	}
	if route.RPMWindowCount < 0 || route.ActiveCount < 0 || route.ConsecutiveFailures < 0 {
		return errors.New("generation provider route counters must not be negative")
	}
	return nil
}

func (state *PlatformGenerationProviderAccountState) Validate() error {
	if state == nil {
		return errors.New("generation provider account state is required")
	}
	if state.ChannelID <= 0 || state.KeyIndex < 0 {
		return errors.New("generation provider account identity is invalid")
	}
	decodedFingerprint, err := hex.DecodeString(state.KeyFingerprint)
	if err != nil || len(decodedFingerprint) != 32 || strings.ToLower(state.KeyFingerprint) != state.KeyFingerprint {
		return errors.New("generation provider account key_fingerprint must be a lowercase SHA-256 digest")
	}
	if state.RPMWindowSeconds <= 0 || state.RPMLimit <= 0 || state.ActiveLimit <= 0 {
		return errors.New("generation provider account limits must be positive")
	}
	if state.RPMWindowCount < 0 || state.ActiveCount < 0 || state.ConsecutiveFailures < 0 {
		return errors.New("generation provider account counters must not be negative")
	}
	return nil
}

func platformGenerationAccountIdentity(route PlatformGenerationProviderRoute) string {
	return fmt.Sprintf("%d\x00%d\x00%s", route.ChannelID, route.KeyIndex, route.KeyFingerprint)
}

func platformGenerationAccountStateMatchesRoute(state PlatformGenerationProviderAccountState, route PlatformGenerationProviderRoute) bool {
	return state.ID == route.AccountStateID &&
		state.ChannelID == route.ChannelID &&
		state.KeyIndex == route.KeyIndex &&
		state.KeyFingerprint == route.KeyFingerprint
}

func platformGenerationAccountLimitsMatchRoute(state PlatformGenerationProviderAccountState, route PlatformGenerationProviderRoute) bool {
	return state.RPMWindowSeconds == route.RPMWindowSeconds &&
		state.RPMLimit == route.RPMLimit &&
		state.ActiveLimit == route.ActiveLimit
}

func applyPlatformGenerationAccountStateToRoute(route *PlatformGenerationProviderRoute, state PlatformGenerationProviderAccountState) {
	if route == nil {
		return
	}
	route.AccountStateID = state.ID
	route.RPMWindowSeconds = state.RPMWindowSeconds
	route.RPMLimit = state.RPMLimit
	route.RPMWindowStartedAt = state.RPMWindowStartedAt
	route.RPMWindowCount = state.RPMWindowCount
	route.ActiveCount = state.ActiveCount
	route.ActiveLimit = state.ActiveLimit
	route.CoolingUntil = state.CoolingUntil
	route.ConsecutiveFailures = state.ConsecutiveFailures
	route.LastFailureAt = state.LastFailureAt
	route.LastSuccessAt = state.LastSuccessAt
	route.LastErrorCode = state.LastErrorCode
	if state.UpdatedAt.After(route.UpdatedAt) {
		route.UpdatedAt = state.UpdatedAt
	}
}

func loadPlatformGenerationAccountStateForRouteTx(
	tx *gorm.DB,
	route PlatformGenerationProviderRoute,
	states map[int64]*PlatformGenerationProviderAccountState,
) (*PlatformGenerationProviderAccountState, error) {
	if route.AccountStateID <= 0 {
		return nil, fmt.Errorf("generation provider route %d has no physical account state", route.ID)
	}
	if state, ok := states[route.AccountStateID]; ok {
		if state == nil || !platformGenerationAccountStateMatchesRoute(*state, route) {
			return nil, fmt.Errorf("generation provider route %d physical account mapping is inconsistent", route.ID)
		}
		return state, nil
	}
	var state PlatformGenerationProviderAccountState
	if err := lockForUpdate(tx.Where("id = ?", route.AccountStateID)).First(&state).Error; err != nil {
		return nil, err
	}
	if err := state.Validate(); err != nil {
		return nil, fmt.Errorf("generation provider account state %d is invalid: %w", state.ID, err)
	}
	if !platformGenerationAccountStateMatchesRoute(state, route) {
		return nil, fmt.Errorf("generation provider route %d physical account mapping is inconsistent", route.ID)
	}
	states[state.ID] = &state
	return &state, nil
}

// lockPlatformGenerationCandidateRoutesTx completes the shared runtime lock
// order after a caller has locked its job (when applicable) and admission:
// physical account IDs, channel IDs, then route IDs, each ascending. The
// initial unlocked read is only discovery; every identity and mapping is
// checked again after the locks are held. Newly configured routes may be picked
// up by the next admission, but can never widen the current transaction.
func lockPlatformGenerationCandidateRoutesTx(
	tx *gorm.DB,
	modelName string,
	mode string,
) ([]PlatformGenerationProviderRoute, map[int64]*PlatformGenerationProviderAccountState, map[int]*Channel, error) {
	var discovered []PlatformGenerationProviderRoute
	if err := tx.Where("model = ? AND mode = ?", modelName, mode).
		Order("account_state_id ASC, id ASC").
		Find(&discovered).Error; err != nil {
		return nil, nil, nil, err
	}

	accountIDsSet := make(map[int64]struct{}, len(discovered))
	channelIDsSet := make(map[int]struct{}, len(discovered))
	routeIDs := make([]int64, 0, len(discovered))
	for _, route := range discovered {
		if route.AccountStateID <= 0 {
			return nil, nil, nil, fmt.Errorf("generation provider route %d has no physical account state", route.ID)
		}
		accountIDsSet[route.AccountStateID] = struct{}{}
		channelIDsSet[route.ChannelID] = struct{}{}
		routeIDs = append(routeIDs, route.ID)
	}
	accountIDs := make([]int64, 0, len(accountIDsSet))
	for accountID := range accountIDsSet {
		accountIDs = append(accountIDs, accountID)
	}
	channelIDs := make([]int, 0, len(channelIDsSet))
	for channelID := range channelIDsSet {
		channelIDs = append(channelIDs, channelID)
	}
	sort.Slice(accountIDs, func(i, j int) bool { return accountIDs[i] < accountIDs[j] })
	sort.Ints(channelIDs)
	sort.Slice(routeIDs, func(i, j int) bool { return routeIDs[i] < routeIDs[j] })

	states := make(map[int64]*PlatformGenerationProviderAccountState, len(accountIDs))
	for _, accountID := range accountIDs {
		var state PlatformGenerationProviderAccountState
		if err := lockForUpdate(tx.Where("id = ?", accountID)).First(&state).Error; err != nil {
			return nil, nil, nil, err
		}
		if err := state.Validate(); err != nil {
			return nil, nil, nil, fmt.Errorf("generation provider account state %d is invalid: %w", state.ID, err)
		}
		states[state.ID] = &state
	}
	channels := make(map[int]*Channel, len(channelIDs))
	for _, channelID := range channelIDs {
		var channel Channel
		err := lockForUpdate(tx.Where("id = ?", channelID)).First(&channel).Error
		if errors.Is(err, gorm.ErrRecordNotFound) {
			channels[channelID] = nil
			continue
		}
		if err != nil {
			return nil, nil, nil, err
		}
		channels[channelID] = &channel
	}

	routes := make([]PlatformGenerationProviderRoute, 0, len(routeIDs))
	for _, routeID := range routeIDs {
		var route PlatformGenerationProviderRoute
		if err := lockForUpdate(tx.Where("id = ?", routeID)).First(&route).Error; err != nil {
			return nil, nil, nil, err
		}
		if route.Model != modelName || route.Mode != mode {
			return nil, nil, nil, fmt.Errorf("generation provider route %d changed immutable model or mode", route.ID)
		}
		state, ok := states[route.AccountStateID]
		if !ok || state == nil || !platformGenerationAccountStateMatchesRoute(*state, route) {
			return nil, nil, nil, fmt.Errorf("generation provider route %d physical account mapping is inconsistent", route.ID)
		}
		if _, ok := channels[route.ChannelID]; !ok {
			return nil, nil, nil, fmt.Errorf("generation provider route %d changed immutable channel", route.ID)
		}
		routes = append(routes, route)
	}
	sort.Slice(routes, func(i, j int) bool {
		if routes[i].AccountStateID == routes[j].AccountStateID {
			return routes[i].ID < routes[j].ID
		}
		return routes[i].AccountStateID < routes[j].AccountStateID
	})
	return routes, states, channels, nil
}

// syncPlatformGenerationAccountRouteMirrorsTx maintains the legacy counters on
// route rows for operator visibility and compatibility. Admission decisions
// never trust those mirrors; the independently locked account state is the
// only authority.
func syncPlatformGenerationAccountRouteMirrorsTx(tx *gorm.DB, state PlatformGenerationProviderAccountState, now time.Time) error {
	return tx.Model(&PlatformGenerationProviderRoute{}).
		Where("account_state_id = ?", state.ID).
		Updates(map[string]any{
			"rpm_window_seconds":    state.RPMWindowSeconds,
			"rpm_limit":             state.RPMLimit,
			"rpm_window_started_at": state.RPMWindowStartedAt,
			"rpm_window_count":      state.RPMWindowCount,
			"active_count":          state.ActiveCount,
			"active_limit":          state.ActiveLimit,
			"cooling_until":         state.CoolingUntil,
			"consecutive_failures":  state.ConsecutiveFailures,
			"last_failure_at":       state.LastFailureAt,
			"last_success_at":       state.LastSuccessAt,
			"last_error_code":       state.LastErrorCode,
			"updated_at":            now,
		}).Error
}

func createPlatformGenerationAccountStateForRouteTx(
	tx *gorm.DB,
	route PlatformGenerationProviderRoute,
	now time.Time,
) (*PlatformGenerationProviderAccountState, error) {
	state := PlatformGenerationProviderAccountState{
		ChannelID:           route.ChannelID,
		KeyIndex:            route.KeyIndex,
		KeyFingerprint:      route.KeyFingerprint,
		RPMWindowSeconds:    route.RPMWindowSeconds,
		RPMLimit:            route.RPMLimit,
		RPMWindowStartedAt:  route.RPMWindowStartedAt,
		RPMWindowCount:      route.RPMWindowCount,
		ActiveCount:         route.ActiveCount,
		ActiveLimit:         route.ActiveLimit,
		CoolingUntil:        route.CoolingUntil,
		ConsecutiveFailures: route.ConsecutiveFailures,
		LastFailureAt:       route.LastFailureAt,
		LastSuccessAt:       route.LastSuccessAt,
		LastErrorCode:       route.LastErrorCode,
		CreatedAt:           now,
		UpdatedAt:           now,
	}
	if err := state.Validate(); err != nil {
		return nil, err
	}
	created := tx.Clauses(clause.OnConflict{
		Columns:   []clause.Column{{Name: "channel_id"}, {Name: "key_index"}, {Name: "key_fingerprint"}},
		DoNothing: true,
	}).Create(&state)
	if created.Error != nil {
		return nil, created.Error
	}
	if created.RowsAffected == 0 {
		if err := lockForUpdate(tx.Where(
			"channel_id = ? AND key_index = ? AND key_fingerprint = ?",
			route.ChannelID,
			route.KeyIndex,
			route.KeyFingerprint,
		)).First(&state).Error; err != nil {
			return nil, err
		}
		if !platformGenerationAccountLimitsMatchRoute(state, route) {
			return nil, fmt.Errorf("generation provider account %s has conflicting RPM or active limits", platformGenerationAccountIdentity(route))
		}
		if err := state.Validate(); err != nil {
			return nil, err
		}
		if route.RPMWindowCount != 0 || route.ActiveCount != 0 || route.ConsecutiveFailures != 0 ||
			route.RPMWindowStartedAt != nil || route.CoolingUntil != nil || route.LastFailureAt != nil || route.LastSuccessAt != nil || route.LastErrorCode != "" {
			return nil, fmt.Errorf("generation provider account %s cannot merge non-empty route-local state", platformGenerationAccountIdentity(route))
		}
	}
	return &state, nil
}

func CreatePlatformGenerationProviderRoute(route *PlatformGenerationProviderRoute) error {
	if err := route.Validate(); err != nil {
		return err
	}
	return DB.Transaction(func(tx *gorm.DB) error {
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		route.CreatedAt = now
		route.UpdatedAt = now
		state, err := createPlatformGenerationAccountStateForRouteTx(tx, *route, now)
		if err != nil {
			return err
		}
		applyPlatformGenerationAccountStateToRoute(route, *state)
		return tx.Create(route).Error
	})
}

// ClaimPlatformGenerationProviderRoute selects one eligible route and occupies
// both its fixed-window RPM token and one long-lived active-task slot. The
// returned submission token fences every later mutation of this admission.
func ClaimPlatformGenerationProviderRoute(jobID string, modelName string, mode string) (*PlatformGenerationRouteClaim, error) {
	if parsed, err := uuid.Parse(jobID); err != nil || parsed.String() != jobID {
		return nil, errors.New("generation job id must be a canonical UUID")
	}
	if modelName == "" || strings.TrimSpace(modelName) != modelName || len(modelName) > 128 {
		return nil, errors.New("generation provider route model is invalid")
	}
	if mode == "" || strings.TrimSpace(mode) != mode || len(mode) > 32 {
		return nil, errors.New("generation provider route mode is invalid")
	}

	var claim *PlatformGenerationRouteClaim
	err := DB.Transaction(func(tx *gorm.DB) error {
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}

		guard := PlatformGenerationRouteAdmission{
			JobID:     jobID,
			State:     PlatformGenerationRouteAdmissionSelecting,
			CreatedAt: now,
			UpdatedAt: now,
		}
		createGuard := tx.Clauses(clause.OnConflict{
			Columns:   []clause.Column{{Name: "job_id"}},
			DoNothing: true,
		}).Create(&guard)
		if createGuard.Error != nil {
			return createGuard.Error
		}

		var admission PlatformGenerationRouteAdmission
		if err := lockForUpdate(tx.Where("job_id = ?", jobID)).First(&admission).Error; err != nil {
			return err
		}
		switch admission.State {
		case PlatformGenerationRouteAdmissionSelecting, PlatformGenerationRouteAdmissionReleased:
		case PlatformGenerationRouteAdmissionHeld, PlatformGenerationRouteAdmissionPosting:
			return ErrPlatformGenerationRouteAdmissionHeld
		case PlatformGenerationRouteAdmissionUnknown:
			return ErrPlatformGenerationRouteAdmissionUnknown
		case PlatformGenerationRouteAdmissionFinished:
			return ErrPlatformGenerationRouteAdmissionFinished
		default:
			return fmt.Errorf("generation route admission %d has invalid state %q", admission.ID, admission.State)
		}
		if admission.SlotHeld {
			return fmt.Errorf("generation route admission %d has an inconsistent held slot", admission.ID)
		}
		if admission.State == PlatformGenerationRouteAdmissionReleased && admission.RouteID != 0 {
			var previousRoute PlatformGenerationProviderRoute
			if err := tx.Where("id = ?", admission.RouteID).First(&previousRoute).Error; err != nil {
				return err
			}
			if previousRoute.Model != modelName || previousRoute.Mode != mode {
				return errors.New("a released generation route admission cannot change model or mode")
			}
		}

		routes, states, channels, err := lockPlatformGenerationCandidateRoutesTx(tx, modelName, mode)
		if err != nil {
			return err
		}

		hasEnabledRoute := false
		hasRouteOutsideCooldown := false
		hasActiveCapacity := false
		selectedIndex := -1
		var selectedState PlatformGenerationProviderAccountState
		selectedWindowStart := now
		selectedWindowCount := 0
		for index := range routes {
			route := &routes[index]
			if !route.Enabled {
				continue
			}
			if err := route.Validate(); err != nil {
				return fmt.Errorf("generation provider route %d is invalid: %w", route.ID, err)
			}
			state, err := loadPlatformGenerationAccountStateForRouteTx(tx, *route, states)
			if err != nil {
				return err
			}
			if !platformGenerationAccountLimitsMatchRoute(*state, *route) {
				return fmt.Errorf("generation provider route %d has conflicting physical account limits", route.ID)
			}
			available, err := platformGenerationRouteAcceptsNewWorkTx(tx, *route, channels)
			if err != nil {
				return err
			}
			if !available {
				continue
			}
			hasEnabledRoute = true
			if state.CoolingUntil != nil && now.Before(state.CoolingUntil.UTC()) {
				continue
			}
			hasRouteOutsideCooldown = true
			if state.ActiveCount >= state.ActiveLimit {
				continue
			}
			hasActiveCapacity = true

			windowStart := now
			windowCount := 0
			if state.RPMWindowStartedAt != nil && now.Before(state.RPMWindowStartedAt.UTC().Add(time.Duration(state.RPMWindowSeconds)*time.Second)) {
				windowStart = state.RPMWindowStartedAt.UTC()
				windowCount = state.RPMWindowCount
			}
			if windowCount >= state.RPMLimit {
				continue
			}
			selectedIndex = index
			selectedState = *state
			selectedWindowStart = windowStart
			selectedWindowCount = windowCount
			break
		}

		if selectedIndex < 0 {
			switch {
			case !hasEnabledRoute || !hasRouteOutsideCooldown:
				return ErrPlatformGenerationProviderRouteUnavailable
			case !hasActiveCapacity:
				return ErrPlatformGenerationProviderRouteBusy
			default:
				return ErrPlatformGenerationProviderRouteRateLimited
			}
		}

		selected := routes[selectedIndex]
		stateUpdate := tx.Model(&PlatformGenerationProviderAccountState{}).
			Where("id = ? AND active_count = ? AND rpm_window_count = ?", selectedState.ID, selectedState.ActiveCount, selectedState.RPMWindowCount).
			Updates(map[string]any{
				"rpm_window_started_at": selectedWindowStart,
				"rpm_window_count":      selectedWindowCount + 1,
				"active_count":          gorm.Expr("active_count + 1"),
				"updated_at":            now,
			})
		if stateUpdate.Error != nil {
			return stateUpdate.Error
		}
		if stateUpdate.RowsAffected != 1 {
			return errors.New("generation provider account admission lost its counter fence")
		}
		selectedState.RPMWindowStartedAt = &selectedWindowStart
		selectedState.RPMWindowCount = selectedWindowCount + 1
		selectedState.ActiveCount++
		selectedState.UpdatedAt = now
		if err := syncPlatformGenerationAccountRouteMirrorsTx(tx, selectedState, now); err != nil {
			return err
		}

		submissionToken := uuid.NewString()
		attempt := admission.Attempt + 1
		admissionUpdate := tx.Model(&PlatformGenerationRouteAdmission{}).
			Where("id = ? AND slot_held = ?", admission.ID, false).
			Updates(map[string]any{
				"route_id":              selected.ID,
				"submission_token_hash": platformGenerationSubmissionTokenHash(submissionToken),
				"state":                 PlatformGenerationRouteAdmissionHeld,
				"slot_held":             true,
				"attempt":               attempt,
				"unknown_at":            nil,
				"closed_at":             nil,
				"updated_at":            now,
			})
		if admissionUpdate.Error != nil {
			return admissionUpdate.Error
		}
		if admissionUpdate.RowsAffected != 1 {
			return errors.New("generation provider route assignment lost its admission fence")
		}

		applyPlatformGenerationAccountStateToRoute(&selected, selectedState)
		claim = &PlatformGenerationRouteClaim{
			AdmissionID:     admission.ID,
			JobID:           jobID,
			Route:           selected,
			Attempt:         attempt,
			SubmissionToken: submissionToken,
		}
		return nil
	})
	return claim, err
}

// platformGenerationRouteAcceptsNewWorkTx separates declarative route
// lifecycle from live native-channel availability. The channel row is locked
// in the same transaction as admission, so a disable/key rotation cannot race
// a new slot claim. Existing tasks do not use this check and remain pinned to
// their original credential snapshot for polling.
func platformGenerationRouteAcceptsNewWorkTx(
	tx *gorm.DB,
	route PlatformGenerationProviderRoute,
	channels map[int]*Channel,
) (bool, error) {
	channel, cached := channels[route.ChannelID]
	if !cached {
		var loaded Channel
		err := lockForUpdate(tx.Where("id = ?", route.ChannelID)).First(&loaded).Error
		if errors.Is(err, gorm.ErrRecordNotFound) {
			channels[route.ChannelID] = nil
			return false, nil
		}
		if err != nil {
			return false, err
		}
		channel = &loaded
		channels[route.ChannelID] = channel
	}
	if channel == nil || channel.Status != common.ChannelStatusEnabled {
		return false, nil
	}
	if route.AcceptedChannelType <= constant.ChannelTypeUnknown ||
		channel.Type != route.AcceptedChannelType {
		return false, nil
	}
	if channel.ChannelInfo.IsMultiKey && channel.ChannelInfo.MultiKeyStatusList != nil {
		if status, exists := channel.ChannelInfo.MultiKeyStatusList[route.KeyIndex]; exists && status != common.ChannelStatusEnabled {
			return false, nil
		}
	}
	key, err := channel.GetKeyAt(route.KeyIndex)
	if err != nil {
		return false, nil
	}
	fingerprint := fmt.Sprintf("%x", common.Sha256Raw([]byte(key)))
	if subtle.ConstantTimeCompare([]byte(fingerprint), []byte(route.KeyFingerprint)) != 1 {
		return false, nil
	}
	return true, nil
}

// MarkPlatformGenerationRouteSubmissionUnknown fences the state transition but
// deliberately keeps the active slot. An unknown submission must not be
// released, retried, or failed over until reconciliation proves its outcome.
func MarkPlatformGenerationRouteSubmissionUnknown(jobID string, submissionToken string) (bool, error) {
	if submissionToken == "" {
		return false, nil
	}
	won := false
	err := DB.Transaction(func(tx *gorm.DB) error {
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		var admission PlatformGenerationRouteAdmission
		if err := lockForUpdate(tx.Where("job_id = ?", jobID)).First(&admission).Error; err != nil {
			if errors.Is(err, gorm.ErrRecordNotFound) {
				return nil
			}
			return err
		}
		if !platformGenerationSubmissionTokenMatches(admission.SubmissionTokenHash, submissionToken) || !admission.SlotHeld {
			return nil
		}
		if admission.State == PlatformGenerationRouteAdmissionUnknown {
			won = true
			return nil
		}
		if admission.State != PlatformGenerationRouteAdmissionHeld && admission.State != PlatformGenerationRouteAdmissionPosting {
			return fmt.Errorf("generation route admission %d cannot become unknown from state %q", admission.ID, admission.State)
		}
		result := tx.Model(&PlatformGenerationRouteAdmission{}).
			Where("id = ? AND state IN ? AND slot_held = ? AND submission_token_hash = ?", admission.ID, []string{PlatformGenerationRouteAdmissionHeld, PlatformGenerationRouteAdmissionPosting}, true, admission.SubmissionTokenHash).
			Updates(map[string]any{
				"state":      PlatformGenerationRouteAdmissionUnknown,
				"unknown_at": now,
				"updated_at": now,
			})
		if result.Error != nil {
			return result.Error
		}
		won = result.RowsAffected == 1
		return nil
	})
	return won, err
}

// MarkPlatformGenerationHeldRouteUnknown is the conservative lease-recovery
// path. A replacement submission worker cannot recover the expired worker's
// random token, so a still-held admission is treated as unknown and never sent
// to another route.
func MarkPlatformGenerationHeldRouteUnknown(jobID string) error {
	return DB.Transaction(func(tx *gorm.DB) error {
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		return tx.Model(&PlatformGenerationRouteAdmission{}).Where(
			"job_id = ? AND state IN ? AND slot_held = ?",
			jobID,
			[]string{PlatformGenerationRouteAdmissionHeld, PlatformGenerationRouteAdmissionPosting},
			true,
		).Updates(map[string]any{
			"state":      PlatformGenerationRouteAdmissionUnknown,
			"unknown_at": now,
			"updated_at": now,
		}).Error
	})
}

// ReleasePlatformGenerationProviderRoute releases a slot only when submission
// is still known not to have an unknown outcome. This is the safe failover path
// for validation/preflight errors or an explicit provider non-creation result.
func ReleasePlatformGenerationProviderRoute(jobID string, submissionToken string) (bool, error) {
	return closePlatformGenerationProviderRoute(jobID, submissionToken, false)
}

// FinishPlatformGenerationProviderRoute releases the route after provider work
// is terminal. It may close either a normal or reconciled-unknown admission.
func FinishPlatformGenerationProviderRoute(jobID string, submissionToken string) (bool, error) {
	return closePlatformGenerationProviderRoute(jobID, submissionToken, true)
}

func GetPlatformGenerationProviderRouteAssignment(jobID string) (*PlatformGenerationRouteAdmission, *PlatformGenerationProviderRoute, error) {
	var admission PlatformGenerationRouteAdmission
	if err := DB.Where("job_id = ?", jobID).First(&admission).Error; err != nil {
		return nil, nil, err
	}
	var route PlatformGenerationProviderRoute
	if err := DB.Where("id = ?", admission.RouteID).First(&route).Error; err != nil {
		return nil, nil, err
	}
	var state PlatformGenerationProviderAccountState
	if route.AccountStateID <= 0 {
		return nil, nil, fmt.Errorf("generation provider route %d has no physical account state", route.ID)
	}
	if err := DB.Where("id = ?", route.AccountStateID).First(&state).Error; err != nil {
		return nil, nil, err
	}
	if !platformGenerationAccountStateMatchesRoute(state, route) {
		return nil, nil, fmt.Errorf("generation provider route %d physical account mapping is inconsistent", route.ID)
	}
	applyPlatformGenerationAccountStateToRoute(&route, state)
	return &admission, &route, nil
}

// BeginPlatformGenerationRouteSubmission consumes the short-lived route token
// exactly once before the private bridge can reach a provider. It also checks
// the owning submission worker's database-clock lease, so a delayed internal
// HTTP request cannot start upstream work after its owner has expired.
func BeginPlatformGenerationRouteSubmission(jobID string, routeID int64, workerLeaseToken string, routeSubmissionToken string) (*PlatformGenerationProviderRoute, error) {
	if workerLeaseToken == "" || routeSubmissionToken == "" || routeID <= 0 {
		return nil, errors.New("generation route submission claim is incomplete")
	}
	var route PlatformGenerationProviderRoute
	err := DB.Transaction(func(tx *gorm.DB) error {
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		var job PlatformGenerationJob
		if err := lockForUpdate(tx.Where(
			"id = ? AND status = ? AND submission_lease_token = ? AND submission_lease_expires_at > ?",
			jobID,
			PlatformGenerationStatusSubmitting,
			workerLeaseToken,
			now,
		)).First(&job).Error; err != nil {
			return errors.New("generation submission worker lease is stale")
		}
		var admission PlatformGenerationRouteAdmission
		if err := lockForUpdate(tx.Where("job_id = ?", jobID)).First(&admission).Error; err != nil {
			return err
		}
		if admission.RouteID != routeID || admission.State != PlatformGenerationRouteAdmissionHeld || !admission.SlotHeld ||
			!platformGenerationSubmissionTokenMatches(admission.SubmissionTokenHash, routeSubmissionToken) {
			return errors.New("generation route submission claim is stale")
		}

		var discoveredRoute PlatformGenerationProviderRoute
		if err := tx.Where("id = ?", routeID).First(&discoveredRoute).Error; err != nil {
			return err
		}
		if discoveredRoute.AccountStateID <= 0 {
			return fmt.Errorf("generation provider route %d has no physical account state", discoveredRoute.ID)
		}
		var state PlatformGenerationProviderAccountState
		if err := lockForUpdate(tx.Where("id = ?", discoveredRoute.AccountStateID)).First(&state).Error; err != nil {
			return err
		}
		channels := make(map[int]*Channel, 1)
		var channel Channel
		channelErr := lockForUpdate(tx.Where("id = ?", discoveredRoute.ChannelID)).First(&channel).Error
		if errors.Is(channelErr, gorm.ErrRecordNotFound) {
			channels[discoveredRoute.ChannelID] = nil
		} else if channelErr != nil {
			return channelErr
		} else {
			channels[discoveredRoute.ChannelID] = &channel
		}
		if err := lockForUpdate(tx.Where("id = ?", routeID)).First(&route).Error; err != nil {
			return err
		}
		if route.ChannelID != discoveredRoute.ChannelID || !platformGenerationAccountStateMatchesRoute(state, route) {
			return fmt.Errorf("generation provider route %d physical identity changed during submission", route.ID)
		}
		if state.ActiveCount <= 0 {
			return errors.New("generation provider account active_count invariant is violated")
		}
		available, err := platformGenerationRouteAcceptsNewWorkTx(tx, route, channels)
		if err != nil {
			return err
		}
		if !available {
			return ErrPlatformGenerationProviderRouteUnavailable
		}
		result := tx.Model(&PlatformGenerationRouteAdmission{}).Where(
			"id = ? AND state = ? AND slot_held = ? AND submission_token_hash = ?",
			admission.ID,
			PlatformGenerationRouteAdmissionHeld,
			true,
			admission.SubmissionTokenHash,
		).Updates(map[string]any{
			"state":      PlatformGenerationRouteAdmissionPosting,
			"updated_at": now,
		})
		if result.Error != nil {
			return result.Error
		}
		if result.RowsAffected != 1 {
			return errors.New("generation route submission token was already consumed")
		}
		return nil
	})
	if err != nil {
		return nil, err
	}
	return &route, nil
}

// MigratePlatformGenerationProviderAccountState backfills the physical-account
// mapping for route rows created by earlier Relay candidates. Legacy counters
// are conservatively merged exactly once: active/RPM/failure counts are added,
// and the latest window/cooldown timestamps are retained. Conflicting limits
// for the same channel/key identity abort startup instead of multiplying or
// silently choosing capacity.
func MigratePlatformGenerationProviderAccountState() error {
	return MigratePlatformGenerationProviderAccountStateWithDB(DB)
}

func MigratePlatformGenerationProviderAccountStateWithDB(db *gorm.DB) error {
	if db == nil {
		return errors.New("database is not initialized")
	}
	if err := db.AutoMigrate(&PlatformGenerationProviderAccountState{}, &PlatformGenerationProviderRoute{}); err != nil {
		return err
	}
	return db.Transaction(func(tx *gorm.DB) error {
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		var routes []PlatformGenerationProviderRoute
		if err := lockForUpdate(tx.Order("id ASC")).Find(&routes).Error; err != nil {
			return err
		}
		unmapped := make(map[string][]PlatformGenerationProviderRoute)
		touchedStates := make(map[int64]PlatformGenerationProviderAccountState)
		for index := range routes {
			route := routes[index]
			if err := route.Validate(); err != nil {
				return fmt.Errorf("generation provider route %d cannot be migrated: %w", route.ID, err)
			}
			if route.AccountStateID == 0 {
				identity := platformGenerationAccountIdentity(route)
				unmapped[identity] = append(unmapped[identity], route)
				continue
			}
			state, err := loadPlatformGenerationAccountStateForRouteTx(tx, route, make(map[int64]*PlatformGenerationProviderAccountState))
			if err != nil {
				return err
			}
			touchedStates[state.ID] = *state
		}

		identities := make([]string, 0, len(unmapped))
		for identity := range unmapped {
			identities = append(identities, identity)
		}
		sort.Strings(identities)
		for _, identity := range identities {
			group := unmapped[identity]
			if len(group) == 0 {
				continue
			}
			seed := group[0]
			state := PlatformGenerationProviderAccountState{
				ChannelID:        seed.ChannelID,
				KeyIndex:         seed.KeyIndex,
				KeyFingerprint:   seed.KeyFingerprint,
				RPMWindowSeconds: seed.RPMWindowSeconds,
				RPMLimit:         seed.RPMLimit,
				ActiveLimit:      seed.ActiveLimit,
				CreatedAt:        now,
				UpdatedAt:        now,
			}
			for index := range group {
				route := group[index]
				if route.RPMWindowSeconds != state.RPMWindowSeconds || route.RPMLimit != state.RPMLimit || route.ActiveLimit != state.ActiveLimit {
					return fmt.Errorf("generation provider account %s has conflicting legacy RPM or active limits", identity)
				}
				if route.ActiveCount > 0 && state.ActiveCount > int(^uint(0)>>1)-route.ActiveCount {
					return fmt.Errorf("generation provider account %s active counter overflows during migration", identity)
				}
				if route.RPMWindowCount > 0 && state.RPMWindowCount > int(^uint(0)>>1)-route.RPMWindowCount {
					return fmt.Errorf("generation provider account %s RPM counter overflows during migration", identity)
				}
				if route.ConsecutiveFailures > 0 && state.ConsecutiveFailures > int(^uint(0)>>1)-route.ConsecutiveFailures {
					return fmt.Errorf("generation provider account %s failure counter overflows during migration", identity)
				}
				state.ActiveCount += route.ActiveCount
				state.RPMWindowCount += route.RPMWindowCount
				state.ConsecutiveFailures += route.ConsecutiveFailures
				state.RPMWindowStartedAt = laterPlatformGenerationTime(state.RPMWindowStartedAt, route.RPMWindowStartedAt)
				state.CoolingUntil = laterPlatformGenerationTime(state.CoolingUntil, route.CoolingUntil)
				if later := laterPlatformGenerationTime(state.LastFailureAt, route.LastFailureAt); later != state.LastFailureAt {
					state.LastFailureAt = later
					state.LastErrorCode = route.LastErrorCode
				} else if state.LastFailureAt == nil && route.LastErrorCode != "" {
					state.LastErrorCode = route.LastErrorCode
				}
				state.LastSuccessAt = laterPlatformGenerationTime(state.LastSuccessAt, route.LastSuccessAt)
			}
			if err := state.Validate(); err != nil {
				return err
			}

			var existing PlatformGenerationProviderAccountState
			lookup := lockForUpdate(tx.Where(
				"channel_id = ? AND key_index = ? AND key_fingerprint = ?",
				state.ChannelID,
				state.KeyIndex,
				state.KeyFingerprint,
			)).First(&existing)
			if errors.Is(lookup.Error, gorm.ErrRecordNotFound) {
				create := tx.Clauses(clause.OnConflict{
					Columns:   []clause.Column{{Name: "channel_id"}, {Name: "key_index"}, {Name: "key_fingerprint"}},
					DoNothing: true,
				}).Create(&state)
				if create.Error != nil {
					return create.Error
				}
				if create.RowsAffected == 0 {
					if err := lockForUpdate(tx.Where(
						"channel_id = ? AND key_index = ? AND key_fingerprint = ?",
						state.ChannelID,
						state.KeyIndex,
						state.KeyFingerprint,
					)).First(&existing).Error; err != nil {
						return err
					}
					state = existing
				}
			} else if lookup.Error != nil {
				return lookup.Error
			} else {
				if !platformGenerationAccountLimitsMatchRoute(existing, seed) {
					return fmt.Errorf("generation provider account %s has conflicting migrated RPM or active limits", identity)
				}
				if platformGenerationAccountStateHasRuntimeData(state) && platformGenerationAccountStateHasRuntimeData(existing) {
					return fmt.Errorf("generation provider account %s has ambiguous route-local and shared runtime state", identity)
				}
				if platformGenerationAccountStateHasRuntimeData(existing) {
					state = existing
				} else if err := tx.Model(&existing).Updates(map[string]any{
					"rpm_window_started_at": state.RPMWindowStartedAt,
					"rpm_window_count":      state.RPMWindowCount,
					"active_count":          state.ActiveCount,
					"cooling_until":         state.CoolingUntil,
					"consecutive_failures":  state.ConsecutiveFailures,
					"last_failure_at":       state.LastFailureAt,
					"last_success_at":       state.LastSuccessAt,
					"last_error_code":       state.LastErrorCode,
					"updated_at":            now,
				}).Error; err != nil {
					return err
				} else {
					state.ID = existing.ID
					state.CreatedAt = existing.CreatedAt
				}
			}
			if state.ID == 0 {
				return fmt.Errorf("generation provider account %s migration did not persist a shared state", identity)
			}
			for index := range group {
				if err := tx.Model(&PlatformGenerationProviderRoute{}).
					Where("id = ? AND account_state_id = 0", group[index].ID).
					Updates(map[string]any{"account_state_id": state.ID, "updated_at": now}).Error; err != nil {
					return err
				}
			}
			touchedStates[state.ID] = state
		}
		for _, state := range touchedStates {
			if err := syncPlatformGenerationAccountRouteMirrorsTx(tx, state, now); err != nil {
				return err
			}
		}
		return nil
	})
}

func laterPlatformGenerationTime(left *time.Time, right *time.Time) *time.Time {
	if right == nil {
		return left
	}
	if left != nil && !right.UTC().After(left.UTC()) {
		return left
	}
	value := right.UTC()
	return &value
}

func platformGenerationAccountStateHasRuntimeData(state PlatformGenerationProviderAccountState) bool {
	return state.RPMWindowStartedAt != nil || state.RPMWindowCount != 0 || state.ActiveCount != 0 ||
		state.CoolingUntil != nil || state.ConsecutiveFailures != 0 || state.LastFailureAt != nil ||
		state.LastSuccessAt != nil || state.LastErrorCode != ""
}

// SyncPlatformGenerationProviderRoutes materializes config declarations while
// preserving held rows. Identity changes must use a new RouteKey; mutating a
// credential fingerprint or channel/key tuple in place fails closed.
func SyncPlatformGenerationProviderRoutes(desired []PlatformGenerationProviderRoute) error {
	return DB.Transaction(func(tx *gorm.DB) error {
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		wanted := make(map[string]struct{}, len(desired))
		accountRoutes := make(map[string]PlatformGenerationProviderRoute)
		for index := range desired {
			route := desired[index]
			if err := route.Validate(); err != nil {
				return err
			}
			identity := route.RouteKey + "\x00" + route.Mode
			if _, duplicate := wanted[identity]; duplicate {
				return fmt.Errorf("generation provider route %q mode %q is duplicated", route.RouteKey, route.Mode)
			}
			wanted[identity] = struct{}{}
			accountIdentity := platformGenerationAccountIdentity(route)
			if existing, ok := accountRoutes[accountIdentity]; ok {
				if existing.RPMWindowSeconds != route.RPMWindowSeconds || existing.RPMLimit != route.RPMLimit || existing.ActiveLimit != route.ActiveLimit {
					return fmt.Errorf("generation provider account %s has conflicting RPM or active limits", accountIdentity)
				}
			} else {
				accountRoutes[accountIdentity] = route
			}
		}

		accountIdentities := make([]string, 0, len(accountRoutes))
		for identity := range accountRoutes {
			accountIdentities = append(accountIdentities, identity)
		}
		sort.Strings(accountIdentities)
		accountStates := make(map[string]PlatformGenerationProviderAccountState, len(accountRoutes))
		for _, identity := range accountIdentities {
			route := accountRoutes[identity]
			var state PlatformGenerationProviderAccountState
			lookup := lockForUpdate(tx.Where(
				"channel_id = ? AND key_index = ? AND key_fingerprint = ?",
				route.ChannelID,
				route.KeyIndex,
				route.KeyFingerprint,
			)).First(&state)
			if errors.Is(lookup.Error, gorm.ErrRecordNotFound) {
				state = PlatformGenerationProviderAccountState{
					ChannelID:        route.ChannelID,
					KeyIndex:         route.KeyIndex,
					KeyFingerprint:   route.KeyFingerprint,
					RPMWindowSeconds: route.RPMWindowSeconds,
					RPMLimit:         route.RPMLimit,
					ActiveLimit:      route.ActiveLimit,
					LastErrorCode:    "",
					CreatedAt:        now,
					UpdatedAt:        now,
				}
				create := tx.Clauses(clause.OnConflict{
					Columns:   []clause.Column{{Name: "channel_id"}, {Name: "key_index"}, {Name: "key_fingerprint"}},
					DoNothing: true,
				}).Create(&state)
				if create.Error != nil {
					return create.Error
				}
				if create.RowsAffected == 0 {
					if err := lockForUpdate(tx.Where(
						"channel_id = ? AND key_index = ? AND key_fingerprint = ?",
						route.ChannelID,
						route.KeyIndex,
						route.KeyFingerprint,
					)).First(&state).Error; err != nil {
						return err
					}
				}
			} else if lookup.Error != nil {
				return lookup.Error
			}
			if state.ChannelID != route.ChannelID || state.KeyIndex != route.KeyIndex || state.KeyFingerprint != route.KeyFingerprint {
				return fmt.Errorf("generation provider account %s identity is inconsistent", identity)
			}
			if err := tx.Model(&state).Updates(map[string]any{
				"rpm_window_seconds": route.RPMWindowSeconds,
				"rpm_limit":          route.RPMLimit,
				"active_limit":       route.ActiveLimit,
				"updated_at":         now,
			}).Error; err != nil {
				return err
			}
			state.RPMWindowSeconds = route.RPMWindowSeconds
			state.RPMLimit = route.RPMLimit
			state.ActiveLimit = route.ActiveLimit
			state.UpdatedAt = now
			if err := state.Validate(); err != nil {
				return err
			}
			accountStates[identity] = state
		}

		for index := range desired {
			route := desired[index]
			state := accountStates[platformGenerationAccountIdentity(route)]
			route.AccountStateID = state.ID
			applyPlatformGenerationAccountStateToRoute(&route, state)
			var existing PlatformGenerationProviderRoute
			lookup := lockForUpdate(tx.Where("route_key = ? AND mode = ?", route.RouteKey, route.Mode)).First(&existing)
			if errors.Is(lookup.Error, gorm.ErrRecordNotFound) {
				route.ID = 0
				route.CreatedAt = now
				route.UpdatedAt = now
				create := tx.Clauses(clause.OnConflict{
					Columns:   []clause.Column{{Name: "route_key"}, {Name: "mode"}},
					DoNothing: true,
				}).Create(&route)
				if create.Error != nil {
					return create.Error
				}
				if create.RowsAffected == 1 {
					continue
				}
				if err := lockForUpdate(tx.Where("route_key = ? AND mode = ?", route.RouteKey, route.Mode)).First(&existing).Error; err != nil {
					return err
				}
			} else if lookup.Error != nil {
				return lookup.Error
			}
			if existing.Model != route.Model || existing.ChannelID != route.ChannelID || existing.KeyIndex != route.KeyIndex ||
				existing.KeyFingerprint != route.KeyFingerprint || existing.ChannelClass != route.ChannelClass ||
				existing.ProviderName != route.ProviderName || existing.AccountID != route.AccountID || existing.UpstreamModel != route.UpstreamModel ||
				(existing.AcceptedChannelType != constant.ChannelTypeUnknown && existing.AcceptedChannelType != route.AcceptedChannelType) {
				return fmt.Errorf("generation provider route %q mode %q changed immutable identity; declare a new route id", route.RouteKey, route.Mode)
			}
			if existing.AccountStateID != 0 && existing.AccountStateID != state.ID {
				return fmt.Errorf("generation provider route %q mode %q changed physical account mapping", route.RouteKey, route.Mode)
			}
			if err := tx.Model(&existing).Updates(map[string]any{
				"account_state_id":      state.ID,
				"accepted_channel_type": route.AcceptedChannelType,
				"enabled":               route.Enabled,
				"staging_ready":         route.StagingReady,
				"production_ready":      route.ProductionReady,
				"rpm_window_seconds":    route.RPMWindowSeconds,
				"rpm_limit":             route.RPMLimit,
				"active_limit":          route.ActiveLimit,
				"updated_at":            now,
			}).Error; err != nil {
				return err
			}
		}

		var existingRoutes []PlatformGenerationProviderRoute
		if err := lockForUpdate(tx).Find(&existingRoutes).Error; err != nil {
			return err
		}
		for index := range existingRoutes {
			existing := existingRoutes[index]
			identity := existing.RouteKey + "\x00" + existing.Mode
			if _, ok := wanted[identity]; ok || !existing.Enabled {
				continue
			}
			if err := tx.Model(&existing).Updates(map[string]any{"enabled": false, "updated_at": now}).Error; err != nil {
				return err
			}
		}
		for _, identity := range accountIdentities {
			if err := syncPlatformGenerationAccountRouteMirrorsTx(tx, accountStates[identity], now); err != nil {
				return err
			}
		}
		return nil
	})
}

// CompletePlatformGenerationTerminal closes the provider slot and advances a
// leased processing/reconciliation job atomically. The poll/reconciliation
// token, rather than a persisted provider credential or submission token,
// fences the terminal transition.
func CompletePlatformGenerationTerminal(jobID string, leaseToken string, fromStatus string, updates map[string]any) (bool, error) {
	return CompletePlatformGenerationTerminalWithOutcome(jobID, leaseToken, fromStatus, updates, nil)
}

// CompletePlatformGenerationTerminalWithOutcome additionally persists the
// immutable provider terminal observation in the same fenced transaction.
// This keeps monitoring/cost reconciliation from losing its denominator if a
// process crashes immediately after closing the provider slot.
func CompletePlatformGenerationTerminalWithOutcome(
	jobID string,
	leaseToken string,
	fromStatus string,
	updates map[string]any,
	outcome *PlatformProviderTerminalOutcome,
) (bool, error) {
	return CompletePlatformGenerationTerminalWithOutcomePolicy(
		jobID,
		leaseToken,
		fromStatus,
		updates,
		outcome,
		1,
		0,
	)
}

// CompletePlatformGenerationTerminalWithOutcomePolicy records account-level
// provider failures in the same transaction that closes the active slot.
// Cooling uses the database clock, only extends an existing window, and is
// cleared by an explicit successful terminal outcome. Existing tasks never
// consult CoolingUntil, so only new admissions are affected.
func CompletePlatformGenerationTerminalWithOutcomePolicy(
	jobID string,
	leaseToken string,
	fromStatus string,
	updates map[string]any,
	outcome *PlatformProviderTerminalOutcome,
	failureThreshold int,
	cooldown time.Duration,
) (bool, error) {
	if failureThreshold < 1 || failureThreshold > 100 {
		return false, errors.New("generation provider failure threshold is invalid")
	}
	if cooldown < 0 || cooldown > 24*time.Hour {
		return false, errors.New("generation provider cooldown is invalid")
	}
	won := false
	err := DB.Transaction(func(tx *gorm.DB) error {
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		var job PlatformGenerationJob
		if err := lockForUpdate(tx.Where(
			"id = ? AND status = ? AND poll_lease_token = ? AND poll_lease_expires_at > ?",
			jobID,
			fromStatus,
			leaseToken,
			now,
		)).First(&job).Error; err != nil {
			if errors.Is(err, gorm.ErrRecordNotFound) {
				return nil
			}
			return err
		}
		var admission PlatformGenerationRouteAdmission
		if err := lockForUpdate(tx.Where("job_id = ?", jobID)).First(&admission).Error; err != nil {
			return err
		}
		if !admission.SlotHeld || (admission.State != PlatformGenerationRouteAdmissionHeld && admission.State != PlatformGenerationRouteAdmissionPosting && admission.State != PlatformGenerationRouteAdmissionUnknown) {
			return fmt.Errorf("generation route admission %d is not terminal-closeable", admission.ID)
		}
		var discoveredRoute PlatformGenerationProviderRoute
		if err := tx.Where("id = ?", admission.RouteID).First(&discoveredRoute).Error; err != nil {
			return err
		}
		if discoveredRoute.AccountStateID <= 0 {
			return fmt.Errorf("generation provider route %d has no physical account state", discoveredRoute.ID)
		}
		var state PlatformGenerationProviderAccountState
		if err := lockForUpdate(tx.Where("id = ?", discoveredRoute.AccountStateID)).First(&state).Error; err != nil {
			return err
		}
		var route PlatformGenerationProviderRoute
		if err := lockForUpdate(tx.Where("id = ?", admission.RouteID)).First(&route).Error; err != nil {
			return err
		}
		if !platformGenerationAccountStateMatchesRoute(state, route) {
			return fmt.Errorf("generation provider route %d physical account mapping is inconsistent", route.ID)
		}
		if outcome != nil && (outcome.RelayJobID != jobID || outcome.RouteID != route.ID) {
			return errors.New("provider terminal outcome does not match the fenced generation route")
		}
		if state.ActiveCount <= 0 {
			return fmt.Errorf("generation provider account %d active_count invariant is violated", state.ID)
		}
		stateUpdates := map[string]any{
			"active_count": gorm.Expr("active_count - 1"),
			"updated_at":   now,
		}
		if outcome != nil {
			switch {
			case outcome.Outcome == PlatformProviderOutcomeSucceeded:
				stateUpdates["consecutive_failures"] = 0
				stateUpdates["cooling_until"] = nil
				stateUpdates["last_error_code"] = ""
				stateUpdates["last_success_at"] = now
			case outcome.Outcome == PlatformProviderOutcomeFailed && outcome.FailureOwner == PlatformProviderFailureOwnerProvider:
				failures := state.ConsecutiveFailures + 1
				stateUpdates["consecutive_failures"] = failures
				stateUpdates["last_failure_at"] = now
				stateUpdates["last_error_code"] = outcome.FailureCode
				if failures >= failureThreshold && cooldown > 0 {
					proposed := now.Add(cooldown)
					if state.CoolingUntil == nil || state.CoolingUntil.UTC().Before(proposed) {
						stateUpdates["cooling_until"] = proposed
					}
				}
			}
		}
		stateResult := tx.Model(&PlatformGenerationProviderAccountState{}).
			Where("id = ? AND active_count > 0", state.ID).
			Updates(stateUpdates)
		if stateResult.Error != nil {
			return stateResult.Error
		}
		if stateResult.RowsAffected != 1 {
			return errors.New("generation provider account terminal close lost its counter fence")
		}
		var refreshedState PlatformGenerationProviderAccountState
		if err := tx.Where("id = ?", state.ID).First(&refreshedState).Error; err != nil {
			return err
		}
		if err := syncPlatformGenerationAccountRouteMirrorsTx(tx, refreshedState, now); err != nil {
			return err
		}
		if err := tx.Model(&admission).Updates(map[string]any{
			"state":                 PlatformGenerationRouteAdmissionFinished,
			"slot_held":             false,
			"submission_token_hash": "",
			"closed_at":             now,
			"updated_at":            now,
		}).Error; err != nil {
			return err
		}
		updates["poll_lease_token"] = ""
		updates["poll_lease_expires_at"] = nil
		updates["updated_at"] = now
		result := tx.Model(&PlatformGenerationJob{}).Where(
			"id = ? AND status = ? AND poll_lease_token = ? AND poll_lease_expires_at > ?",
			jobID,
			fromStatus,
			leaseToken,
			now,
		).Updates(updates)
		if result.Error != nil {
			return result.Error
		}
		won = result.RowsAffected == 1
		if !won {
			return errors.New("generation terminal transition lost its lease fence")
		}
		if nextStatus, ok := telemetryStatusFromUpdates(updates); ok {
			if err := RecordPlatformTaskStageTransitionTx(tx, jobID, nextStatus, now); err != nil {
				return err
			}
		}
		if outcome != nil {
			if _, err := createPlatformProviderTerminalOutcomeTx(tx, outcome); err != nil {
				return err
			}
		}
		return nil
	})
	return won, err
}

func closePlatformGenerationProviderRoute(jobID string, submissionToken string, allowUnknown bool) (bool, error) {
	if submissionToken == "" {
		return false, nil
	}
	won := false
	err := DB.Transaction(func(tx *gorm.DB) error {
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		var admission PlatformGenerationRouteAdmission
		if err := lockForUpdate(tx.Where("job_id = ?", jobID)).First(&admission).Error; err != nil {
			if errors.Is(err, gorm.ErrRecordNotFound) {
				return nil
			}
			return err
		}
		if !platformGenerationSubmissionTokenMatches(admission.SubmissionTokenHash, submissionToken) || !admission.SlotHeld {
			return nil
		}
		if admission.State == PlatformGenerationRouteAdmissionUnknown && !allowUnknown {
			return ErrPlatformGenerationRouteAdmissionUnknown
		}
		if admission.State != PlatformGenerationRouteAdmissionHeld && admission.State != PlatformGenerationRouteAdmissionPosting && !(allowUnknown && admission.State == PlatformGenerationRouteAdmissionUnknown) {
			return fmt.Errorf("generation route admission %d cannot close from state %q", admission.ID, admission.State)
		}
		var discoveredRoute PlatformGenerationProviderRoute
		if err := tx.Where("id = ?", admission.RouteID).First(&discoveredRoute).Error; err != nil {
			return err
		}
		if discoveredRoute.AccountStateID <= 0 {
			return fmt.Errorf("generation provider route %d has no physical account state", discoveredRoute.ID)
		}

		var state PlatformGenerationProviderAccountState
		if err := lockForUpdate(tx.Where("id = ?", discoveredRoute.AccountStateID)).First(&state).Error; err != nil {
			return err
		}
		var route PlatformGenerationProviderRoute
		if err := lockForUpdate(tx.Where("id = ?", discoveredRoute.ID)).First(&route).Error; err != nil {
			return err
		}
		if !platformGenerationAccountStateMatchesRoute(state, route) {
			return fmt.Errorf("generation provider route %d physical account mapping is inconsistent", route.ID)
		}
		if admission.RouteID != route.ID {
			return fmt.Errorf("generation route admission %d changed immutable route", admission.ID)
		}
		if state.ActiveCount <= 0 {
			return fmt.Errorf("generation provider account %d active_count invariant is violated", state.ID)
		}
		stateUpdate := tx.Model(&PlatformGenerationProviderAccountState{}).
			Where("id = ? AND active_count > 0", state.ID).
			Updates(map[string]any{
				"active_count": gorm.Expr("active_count - 1"),
				"updated_at":   now,
			})
		if stateUpdate.Error != nil {
			return stateUpdate.Error
		}
		if stateUpdate.RowsAffected != 1 {
			return errors.New("generation provider account release lost its counter fence")
		}
		state.ActiveCount--
		state.UpdatedAt = now
		if err := syncPlatformGenerationAccountRouteMirrorsTx(tx, state, now); err != nil {
			return err
		}

		targetState := PlatformGenerationRouteAdmissionReleased
		if allowUnknown {
			targetState = PlatformGenerationRouteAdmissionFinished
		}
		admissionUpdate := tx.Model(&PlatformGenerationRouteAdmission{}).
			Where("id = ? AND slot_held = ? AND submission_token_hash = ?", admission.ID, true, admission.SubmissionTokenHash).
			Updates(map[string]any{
				"state":                 targetState,
				"slot_held":             false,
				"submission_token_hash": "",
				"closed_at":             now,
				"updated_at":            now,
			})
		if admissionUpdate.Error != nil {
			return admissionUpdate.Error
		}
		if admissionUpdate.RowsAffected != 1 {
			return errors.New("generation provider route release lost its admission fence")
		}
		won = true
		return nil
	})
	return won, err
}

func platformGenerationSubmissionTokenHash(token string) string {
	return fmt.Sprintf("%x", common.Sha256Raw([]byte(token)))
}

func platformGenerationSubmissionTokenMatches(storedHash string, token string) bool {
	actualHash := platformGenerationSubmissionTokenHash(token)
	return subtle.ConstantTimeCompare([]byte(storedHash), []byte(actualHash)) == 1
}
