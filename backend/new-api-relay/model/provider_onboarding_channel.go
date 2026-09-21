package model

import (
	"bytes"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"strconv"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	"gorm.io/gorm"
)

const (
	ProviderOnboardingManagedChannelTag = "platform-provider-onboarding-v1"
	ProviderOnboardingMarkerKey         = "credential_late_provider"
	ProviderOnboardingStateStaged       = "staged"
	ProviderOnboardingStateRouteReady   = "route_test_ready"

	ProviderOnboardingGoogleChannelID     = 990001
	ProviderOnboardingMiniMaxChannelID    = 990002
	ProviderOnboardingVolcengineChannelID = 990003
)

var (
	ErrProviderOnboardingManagedChannel           = errors.New("managed provider channel must use the provider onboarding API")
	ErrProviderOnboardingChannelConflict          = errors.New("provider onboarding channel identity conflicts with an existing channel")
	ErrProviderOnboardingExpectedRevisionRequired = errors.New("provider onboarding expected revision is required")
	ErrProviderOnboardingRouteNotReady            = errors.New("provider onboarding route is not ready")
)

// ProviderOnboardingMarker is the only lifecycle material persisted in
// Channel.OtherInfo for a credential-late managed provider. It deliberately
// contains provider identity and state only, never credential material.
type ProviderOnboardingMarker struct {
	SchemaVersion int      `json:"schema_version"`
	Provider      string   `json:"provider"`
	Region        string   `json:"region,omitempty"`
	AccountID     string   `json:"account_id"`
	ChannelID     int      `json:"channel_id"`
	PublicModels  []string `json:"public_model_ids"`
	State         string   `json:"state"`
}

// ProviderOnboardingChannelSpec is the immutable reviewed identity of one
// UI-managed provider account. Callers provide only compiled, server-owned
// values; browser input is limited to the credential and concurrency token.
type ProviderOnboardingChannelSpec struct {
	Provider     string
	Region       string
	AccountID    string
	ChannelID    int
	ChannelType  int
	BaseURL      string
	PublicModels []string
}

type ProviderOnboardingCredentialSummary struct {
	Configured        bool
	FingerprintPrefix string
	KeyCount          int
	UpdatedAt         *time.Time
}

type ProviderOnboardingChannelSnapshot struct {
	Channel    Channel
	Marker     ProviderOnboardingMarker
	Credential ProviderOnboardingCredentialSummary
}

// ProviderOnboardingExpectedRoute is the complete, secret-free executable
// identity that the reviewed runtime declaration must materialize before a
// managed provider can be resumed.  It intentionally excludes mutable health
// counters while binding every model/mode/profile/release/environment field.
type ProviderOnboardingExpectedRoute struct {
	RouteKey                       string
	Model                          string
	Mode                           string
	ProviderName                   string
	AccountID                      string
	ChannelID                      int
	AcceptedChannelType            int
	KeyIndex                       int
	KeyFingerprint                 string
	ChannelClass                   string
	UpstreamModel                  string
	CapabilityProfileID            string
	CapabilityProfileRevision      string
	ModelReleaseID                 string
	ModelReleaseRevision           string
	ModelReleaseCapabilityRevision string
	StagingReady                   bool
	ProductionReady                bool
	RPMWindowSeconds               int
	RPMLimit                       int
	ActiveLimit                    int
	AcceptanceDigest               string
	AcceptanceNotBefore            time.Time
	AcceptanceNotAfter             time.Time
}

func IsProviderOnboardingReservedChannelID(channelID int) bool {
	switch channelID {
	case ProviderOnboardingGoogleChannelID,
		ProviderOnboardingMiniMaxChannelID,
		ProviderOnboardingVolcengineChannelID:
		return true
	default:
		return false
	}
}

func IsProviderOnboardingManagedChannel(channel *Channel) bool {
	if channel == nil {
		return false
	}
	if IsProviderOnboardingReservedChannelID(channel.Id) ||
		(channel.Tag != nil && *channel.Tag == ProviderOnboardingManagedChannelTag) {
		return true
	}
	// The lifecycle marker is an independent, durable ownership fence.  A
	// managed row must not fall back into native channel administration merely
	// because its tag was lost or corrupted.  Conversely, ordinary channel
	// metadata remains unaffected unless it actually claims the reserved marker
	// key.  Once that key is present, malformed marker material is treated as
	// managed (fail closed) rather than as proof that the row is ordinary.
	if !providerOnboardingMarkerKeyPresent(channel.OtherInfo) {
		return false
	}
	// Run the canonical strict parser for valid markers.  Either result is a
	// managed identity: success proves ownership, while failure means the row is
	// a corrupt ownership claim that native paths must not repair or overwrite.
	_, _ = parseProviderOnboardingMarker(channel.OtherInfo)
	return true
}

func providerOnboardingMarkerKeyPresent(raw string) bool {
	trimmed := strings.TrimSpace(raw)
	if trimmed == "" {
		return false
	}
	var root map[string]json.RawMessage
	if err := json.Unmarshal([]byte(trimmed), &root); err == nil {
		_, present := root[ProviderOnboardingMarkerKey]
		return present
	}
	// Preserve compatibility for ordinary legacy rows whose unrelated
	// other_info is malformed, while refusing a visibly injected/corrupted
	// reserved marker that cannot be parsed.
	return providerOnboardingMarkerKeyLexicallyPresent(trimmed)
}

func providerOnboardingMarkerKeyLexicallyPresent(raw string) bool {
	for start := 0; start < len(raw); {
		if raw[start] != '"' {
			start++
			continue
		}
		end := start + 1
		escaped := false
		for ; end < len(raw); end++ {
			if escaped {
				escaped = false
				continue
			}
			if raw[end] == '\\' {
				escaped = true
				continue
			}
			if raw[end] == '"' {
				break
			}
		}
		if end >= len(raw) {
			return false
		}
		decoded, err := strconv.Unquote(raw[start : end+1])
		next := end + 1
		for next < len(raw) && (raw[next] == ' ' || raw[next] == '\t' || raw[next] == '\r' || raw[next] == '\n') {
			next++
		}
		if err == nil && decoded == ProviderOnboardingMarkerKey && next < len(raw) && raw[next] == ':' {
			return true
		}
		start = end + 1
	}
	return false
}

// ExcludeProviderOnboardingManagedChannels applies the database-side half of
// the native-channel isolation fence.  Callers must still repeat the in-memory
// IsProviderOnboardingManagedChannel check because a row can be corrupted or
// changed between candidate discovery and use.
func ExcludeProviderOnboardingManagedChannels(query *gorm.DB) *gorm.DB {
	if query == nil {
		return query
	}
	return query.
		Where("id NOT IN ?", []int{
			ProviderOnboardingGoogleChannelID,
			ProviderOnboardingMiniMaxChannelID,
			ProviderOnboardingVolcengineChannelID,
		}).
		Where("tag IS NULL OR tag <> ?", ProviderOnboardingManagedChannelTag)
}

func validateProviderOnboardingChannelSpec(spec ProviderOnboardingChannelSpec) error {
	if !IsProviderOnboardingReservedChannelID(spec.ChannelID) || spec.ChannelType <= 0 ||
		strings.TrimSpace(spec.Provider) == "" || strings.TrimSpace(spec.Provider) != spec.Provider ||
		spec.AccountID != "primary" || strings.TrimSpace(spec.BaseURL) == "" ||
		len(spec.PublicModels) == 0 {
		return errors.New("provider onboarding channel identity is invalid")
	}
	for index, modelID := range spec.PublicModels {
		if strings.TrimSpace(modelID) == "" || strings.TrimSpace(modelID) != modelID {
			return errors.New("provider onboarding public model identity is invalid")
		}
		if index > 0 && spec.PublicModels[index-1] >= modelID {
			return errors.New("provider onboarding public models must be unique and sorted")
		}
	}
	return nil
}

func providerOnboardingMarkerForSpec(spec ProviderOnboardingChannelSpec, state string) ProviderOnboardingMarker {
	return ProviderOnboardingMarker{
		SchemaVersion: 1,
		Provider:      spec.Provider,
		Region:        spec.Region,
		AccountID:     spec.AccountID,
		ChannelID:     spec.ChannelID,
		PublicModels:  append([]string(nil), spec.PublicModels...),
		State:         state,
	}
}

func providerOnboardingMarkerJSON(spec ProviderOnboardingChannelSpec, state string) (string, error) {
	encoded, err := common.Marshal(map[string]ProviderOnboardingMarker{
		ProviderOnboardingMarkerKey: providerOnboardingMarkerForSpec(spec, state),
	})
	if err != nil {
		return "", errors.New("provider onboarding marker could not be encoded")
	}
	return string(encoded), nil
}

func parseProviderOnboardingMarker(raw string) (ProviderOnboardingMarker, error) {
	var root map[string]json.RawMessage
	trimmed := strings.TrimSpace(raw)
	if trimmed == "" || json.Unmarshal([]byte(trimmed), &root) != nil {
		return ProviderOnboardingMarker{}, ErrProviderOnboardingChannelConflict
	}
	rawMarker, ok := root[ProviderOnboardingMarkerKey]
	if !ok {
		return ProviderOnboardingMarker{}, ErrProviderOnboardingChannelConflict
	}
	decoder := json.NewDecoder(bytes.NewReader(rawMarker))
	decoder.DisallowUnknownFields()
	var marker ProviderOnboardingMarker
	if decoder.Decode(&marker) != nil {
		return ProviderOnboardingMarker{}, ErrProviderOnboardingChannelConflict
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		return ProviderOnboardingMarker{}, ErrProviderOnboardingChannelConflict
	}
	if marker.SchemaVersion != 1 ||
		(marker.State != ProviderOnboardingStateStaged && marker.State != ProviderOnboardingStateRouteReady) ||
		strings.TrimSpace(marker.Provider) == "" || strings.TrimSpace(marker.Provider) != marker.Provider ||
		strings.TrimSpace(marker.Region) != marker.Region ||
		strings.TrimSpace(marker.AccountID) == "" || strings.TrimSpace(marker.AccountID) != marker.AccountID ||
		marker.ChannelID <= 0 || len(marker.PublicModels) == 0 {
		return ProviderOnboardingMarker{}, ErrProviderOnboardingChannelConflict
	}
	for index, modelID := range marker.PublicModels {
		if strings.TrimSpace(modelID) == "" || strings.TrimSpace(modelID) != modelID ||
			(index > 0 && marker.PublicModels[index-1] >= modelID) {
			return ProviderOnboardingMarker{}, ErrProviderOnboardingChannelConflict
		}
	}
	return marker, nil
}

func validateProviderOnboardingChannelIdentity(
	channel Channel,
	spec ProviderOnboardingChannelSpec,
) (ProviderOnboardingMarker, error) {
	if channel.Id != spec.ChannelID || channel.Type != spec.ChannelType || channel.ChannelInfo.IsMultiKey ||
		channel.Tag == nil || *channel.Tag != ProviderOnboardingManagedChannelTag ||
		channel.BaseURL == nil || *channel.BaseURL != spec.BaseURL || channel.Group != "default" ||
		channel.Models != strings.Join(spec.PublicModels, ",") {
		return ProviderOnboardingMarker{}, ErrProviderOnboardingChannelConflict
	}
	marker, err := parseProviderOnboardingMarker(channel.OtherInfo)
	if err != nil || marker.Provider != spec.Provider || marker.Region != spec.Region ||
		marker.AccountID != spec.AccountID || marker.ChannelID != spec.ChannelID ||
		strings.Join(marker.PublicModels, ",") != strings.Join(spec.PublicModels, ",") {
		return ProviderOnboardingMarker{}, ErrProviderOnboardingChannelConflict
	}
	return marker, nil
}

func providerOnboardingCredentialRowTx(
	tx *gorm.DB,
	channel Channel,
) (ProviderChannelCredentialSetVersion, error) {
	if strings.TrimSpace(channel.CredentialSetVersion) == "" {
		return ProviderChannelCredentialSetVersion{}, ErrProviderOnboardingChannelConflict
	}
	row, err := loadProviderChannelCredentialSetVersionTx(tx, channel.CredentialSetVersion)
	if err != nil || row.ChannelID != channel.Id || len(row.KeySetFingerprint) != sha256.Size*2 || row.KeyCount != 1 {
		return ProviderChannelCredentialSetVersion{}, ErrProviderOnboardingChannelConflict
	}
	return row, nil
}

func providerOnboardingCredentialSummary(row ProviderChannelCredentialSetVersion) ProviderOnboardingCredentialSummary {
	updatedAt := row.CreatedAt.UTC()
	prefix := row.KeySetFingerprint
	if len(prefix) > 12 {
		prefix = prefix[:12]
	}
	return ProviderOnboardingCredentialSummary{
		Configured:        true,
		FingerprintPrefix: prefix,
		KeyCount:          row.KeyCount,
		UpdatedAt:         &updatedAt,
	}
}

func providerOnboardingChannelQuery(tx *gorm.DB, lock bool) *gorm.DB {
	query := tx.Session(&gorm.Session{NewDB: true, SkipHooks: true}).Omit("key")
	if lock {
		query = lockForUpdate(query)
	}
	return query
}

func loadProviderOnboardingChannelTx(
	tx *gorm.DB,
	spec ProviderOnboardingChannelSpec,
	lock bool,
) (ProviderOnboardingChannelSnapshot, ProviderChannelCredentialSetVersion, error) {
	var channel Channel
	if err := providerOnboardingChannelQuery(tx, lock).Where("id = ?", spec.ChannelID).First(&channel).Error; err != nil {
		return ProviderOnboardingChannelSnapshot{}, ProviderChannelCredentialSetVersion{}, err
	}
	marker, err := validateProviderOnboardingChannelIdentity(channel, spec)
	if err != nil {
		return ProviderOnboardingChannelSnapshot{}, ProviderChannelCredentialSetVersion{}, err
	}
	row, err := providerOnboardingCredentialRowTx(tx, channel)
	if err != nil {
		return ProviderOnboardingChannelSnapshot{}, ProviderChannelCredentialSetVersion{}, err
	}
	return ProviderOnboardingChannelSnapshot{
		Channel: channel, Marker: marker, Credential: providerOnboardingCredentialSummary(row),
	}, row, nil
}

func LoadProviderOnboardingChannel(
	spec ProviderOnboardingChannelSpec,
) (ProviderOnboardingChannelSnapshot, error) {
	if DB == nil {
		return ProviderOnboardingChannelSnapshot{}, errors.New("provider onboarding database is not initialized")
	}
	if err := validateProviderOnboardingChannelSpec(spec); err != nil {
		return ProviderOnboardingChannelSnapshot{}, err
	}
	snapshot, _, err := loadProviderOnboardingChannelTx(DB, spec, false)
	return snapshot, err
}

func updateProviderOnboardingChannelTx(
	tx *gorm.DB,
	channel Channel,
	updates map[string]any,
) error {
	if channel.ControlRevision == math.MaxInt64 {
		return errors.New("provider onboarding channel revision is exhausted")
	}
	updates["control_revision"] = channel.ControlRevision + 1
	result := tx.Session(&gorm.Session{NewDB: true, SkipHooks: true}).Table("channels").
		Where("id = ? AND control_revision = ?", channel.Id, channel.ControlRevision).
		Updates(updates)
	if result.Error != nil {
		return result.Error
	}
	if result.RowsAffected != 1 {
		return ErrPlatformChannelControlRevisionConflict
	}
	return nil
}

// PutProviderOnboardingCredential creates the reserved managed channel or
// atomically rotates its immutable credential reference. An identical
// credential is a recovery read even when the caller's revision is stale.
// A changed credential always returns the channel to staged/disabled state,
// disables native abilities and retires old route identities in the same
// transaction.
func PutProviderOnboardingCredential(
	spec ProviderOnboardingChannelSpec,
	rawCredential string,
	expectedRevision string,
) (ProviderOnboardingChannelSnapshot, bool, error) {
	if DB == nil || rawCredential == "" {
		return ProviderOnboardingChannelSnapshot{}, false, errors.New("provider onboarding credential is invalid")
	}
	if err := validateProviderOnboardingChannelSpec(spec); err != nil {
		return ProviderOnboardingChannelSnapshot{}, false, err
	}
	candidateFingerprint := providerChannelCredentialFingerprint(rawCredential)
	var snapshot ProviderOnboardingChannelSnapshot
	changed := false
	attemptedCreate := false
	err := DB.Transaction(func(tx *gorm.DB) error {
		current, currentRow, err := loadProviderOnboardingChannelTx(tx, spec, true)
		if errors.Is(err, gorm.ErrRecordNotFound) {
			attemptedCreate = true
			if expectedRevision != "" {
				return ErrPlatformChannelControlRevisionConflict
			}
			marker, markerErr := providerOnboardingMarkerJSON(spec, ProviderOnboardingStateStaged)
			if markerErr != nil {
				return markerErr
			}
			tag := ProviderOnboardingManagedChannelTag
			baseURL := spec.BaseURL
			channel := Channel{
				Id: spec.ChannelID, Type: spec.ChannelType, Key: rawCredential,
				Status:  common.ChannelStatusManuallyDisabled,
				Name:    "Provider onboarding / " + spec.Provider + " / " + spec.AccountID,
				BaseURL: &baseURL, Models: strings.Join(spec.PublicModels, ","), Group: "default",
				Tag: &tag, OtherInfo: marker, CreatedTime: common.GetTimestamp(),
			}
			if err := tx.Create(&channel).Error; err != nil {
				return err
			}
			if err := channel.AddAbilities(tx); err != nil {
				return err
			}
			if err := tx.Model(&Ability{}).Where("channel_id = ?", spec.ChannelID).
				Update("enabled", false).Error; err != nil {
				return err
			}
			snapshot, _, err = loadProviderOnboardingChannelTx(tx, spec, false)
			if err == nil {
				changed = true
			}
			return err
		}
		if err != nil {
			return err
		}
		if subtle.ConstantTimeCompare([]byte(currentRow.KeySetFingerprint), []byte(candidateFingerprint)) == 1 {
			snapshot = current
			return nil
		}
		if expectedRevision == "" {
			return ErrProviderOnboardingExpectedRevisionRequired
		}
		if expectedRevision != PlatformChannelControlRevision(current.Channel) {
			return ErrPlatformChannelControlRevisionConflict
		}
		if err := rejectPlatformGenerationChannelMutationTx(tx, spec.ChannelID); err != nil {
			return err
		}
		version, err := storeProviderChannelCredentialSetVersionTx(tx, spec.ChannelID, rawCredential)
		if err != nil {
			return err
		}
		marker, err := providerOnboardingMarkerJSON(spec, ProviderOnboardingStateStaged)
		if err != nil {
			return err
		}
		if err := updateProviderOnboardingChannelTx(tx, current.Channel, map[string]any{
			"credential_set_version": version,
			"status":                 common.ChannelStatusManuallyDisabled,
			"other_info":             marker,
		}); err != nil {
			return err
		}
		if err := tx.Model(&Ability{}).Where("channel_id = ?", spec.ChannelID).
			Update("enabled", false).Error; err != nil {
			return err
		}
		if tx.Migrator().HasTable(&PlatformGenerationProviderRoute{}) {
			if err := tx.Model(&PlatformGenerationProviderRoute{}).
				Where("channel_id = ? AND key_fingerprint <> ?", spec.ChannelID, candidateFingerprint).
				Update("enabled", false).Error; err != nil {
				return err
			}
		}
		snapshot, _, err = loadProviderOnboardingChannelTx(tx, spec, false)
		if err == nil {
			changed = true
		}
		return err
	})
	if err == nil || !attemptedCreate {
		return snapshot, changed, err
	}

	// A concurrent creator may have won the reserved channel ID. Recover only
	// when the committed row has the exact reviewed identity and fingerprint;
	// every other create error is returned unchanged.
	replayed, row, replayErr := func() (ProviderOnboardingChannelSnapshot, ProviderChannelCredentialSetVersion, error) {
		return loadProviderOnboardingChannelTx(DB, spec, false)
	}()
	if replayErr == nil && subtle.ConstantTimeCompare([]byte(row.KeySetFingerprint), []byte(candidateFingerprint)) == 1 {
		return replayed, false, nil
	}
	return ProviderOnboardingChannelSnapshot{}, false, err
}

func providerOnboardingCurrentRouteReadyTx(
	tx *gorm.DB,
	spec ProviderOnboardingChannelSpec,
	credential ProviderChannelCredentialSetVersion,
	expected []ProviderOnboardingExpectedRoute,
) (bool, error) {
	if !tx.Migrator().HasTable(&PlatformGenerationProviderRoute{}) || len(expected) == 0 {
		return false, nil
	}
	expectedByIdentity := make(map[string]ProviderOnboardingExpectedRoute, len(expected))
	now := time.Now().UTC()
	for _, route := range expected {
		identity := route.RouteKey + "\x00" + route.Mode
		if strings.TrimSpace(route.RouteKey) == "" || strings.TrimSpace(route.Model) == "" ||
			strings.TrimSpace(route.Mode) == "" || route.ChannelID != spec.ChannelID ||
			route.ProviderName != spec.Provider || route.AccountID != spec.AccountID ||
			route.KeyIndex != 0 || len(route.KeyFingerprint) != sha256.Size*2 ||
			subtle.ConstantTimeCompare([]byte(route.KeyFingerprint), []byte(credential.KeySetFingerprint)) != 1 ||
			strings.TrimSpace(route.CapabilityProfileID) == "" ||
			strings.TrimSpace(route.CapabilityProfileRevision) == "" ||
			strings.TrimSpace(route.ModelReleaseID) == "" ||
			strings.TrimSpace(route.ModelReleaseRevision) == "" ||
			strings.TrimSpace(route.ModelReleaseCapabilityRevision) == "" ||
			route.RPMWindowSeconds <= 0 || route.RPMLimit <= 0 || route.ActiveLimit <= 0 {
			return false, nil
		}
		if route.StagingReady || route.ProductionReady {
			if len(route.AcceptanceDigest) != len("sha256:")+sha256.Size*2 ||
				!strings.HasPrefix(route.AcceptanceDigest, "sha256:") ||
				route.AcceptanceNotBefore.IsZero() || route.AcceptanceNotAfter.IsZero() ||
				now.Before(route.AcceptanceNotBefore) || !now.Before(route.AcceptanceNotAfter) {
				return false, nil
			}
		}
		if _, duplicate := expectedByIdentity[identity]; duplicate {
			return false, nil
		}
		expectedByIdentity[identity] = route
	}

	var current []PlatformGenerationProviderRoute
	if err := lockForUpdate(tx.Where("channel_id = ? AND enabled = ?", spec.ChannelID, true)).
		Order("route_key ASC, mode ASC").Find(&current).Error; err != nil {
		return false, err
	}
	if len(current) != len(expectedByIdentity) {
		return false, nil
	}
	accountStates := make(map[int64]*PlatformGenerationProviderAccountState)
	for _, route := range current {
		expectedRoute, ok := expectedByIdentity[route.RouteKey+"\x00"+route.Mode]
		if !ok || route.Model != expectedRoute.Model || route.ProviderName != expectedRoute.ProviderName ||
			route.AccountID != expectedRoute.AccountID || route.ChannelID != expectedRoute.ChannelID ||
			route.AcceptedChannelType != expectedRoute.AcceptedChannelType || route.KeyIndex != expectedRoute.KeyIndex ||
			subtle.ConstantTimeCompare([]byte(route.KeyFingerprint), []byte(expectedRoute.KeyFingerprint)) != 1 ||
			route.ChannelClass != expectedRoute.ChannelClass || route.UpstreamModel != expectedRoute.UpstreamModel ||
			route.CapabilityProfileID != expectedRoute.CapabilityProfileID ||
			route.CapabilityProfileRevision != expectedRoute.CapabilityProfileRevision ||
			route.ModelReleaseID != expectedRoute.ModelReleaseID ||
			route.ModelReleaseRevision != expectedRoute.ModelReleaseRevision ||
			route.ModelReleaseCapabilityRevision != expectedRoute.ModelReleaseCapabilityRevision ||
			route.StagingReady != expectedRoute.StagingReady ||
			route.ProductionReady != expectedRoute.ProductionReady ||
			route.RPMWindowSeconds != expectedRoute.RPMWindowSeconds ||
			route.RPMLimit != expectedRoute.RPMLimit || route.ActiveLimit != expectedRoute.ActiveLimit {
			return false, nil
		}
		state, err := loadPlatformGenerationAccountStateForRouteTx(tx, route, accountStates)
		if err != nil || !platformGenerationAccountLimitsMatchRoute(*state, route) ||
			state.RPMWindowSeconds != expectedRoute.RPMWindowSeconds ||
			state.RPMLimit != expectedRoute.RPMLimit || state.ActiveLimit != expectedRoute.ActiveLimit {
			return false, err
		}
	}
	return true, nil
}

// ProviderOnboardingCurrentRoutesReady is used by the secret-free management
// view to avoid presenting a stale lifecycle marker as current route evidence.
func ProviderOnboardingCurrentRoutesReady(
	spec ProviderOnboardingChannelSpec,
	expected []ProviderOnboardingExpectedRoute,
) (bool, error) {
	if DB == nil {
		return false, errors.New("provider onboarding database is not initialized")
	}
	var ready bool
	err := DB.Transaction(func(tx *gorm.DB) error {
		_, credential, err := loadProviderOnboardingChannelTx(tx, spec, false)
		if err != nil {
			return err
		}
		ready, err = providerOnboardingCurrentRouteReadyTx(tx, spec, credential, expected)
		return err
	})
	return ready, err
}

// SetProviderOnboardingChannelEnabled is the only manual lifecycle boundary
// for a managed provider channel. Disabling is always safe and does not evict
// already-pinned tasks. Resume requires the route-ready marker and a current,
// enabled route bound to the exact credential fingerprint.
func SetProviderOnboardingChannelEnabled(
	spec ProviderOnboardingChannelSpec,
	expectedRevision string,
	enabled bool,
	expectedRoutes []ProviderOnboardingExpectedRoute,
) (ProviderOnboardingChannelSnapshot, bool, error) {
	if DB == nil || expectedRevision == "" {
		return ProviderOnboardingChannelSnapshot{}, false, ErrProviderOnboardingExpectedRevisionRequired
	}
	if err := validateProviderOnboardingChannelSpec(spec); err != nil {
		return ProviderOnboardingChannelSnapshot{}, false, err
	}
	targetStatus := common.ChannelStatusManuallyDisabled
	if enabled {
		targetStatus = common.ChannelStatusEnabled
	}
	var snapshot ProviderOnboardingChannelSnapshot
	changed := false
	err := DB.Transaction(func(tx *gorm.DB) error {
		current, credential, err := loadProviderOnboardingChannelTx(tx, spec, true)
		if err != nil {
			return err
		}
		if enabled {
			if current.Marker.State != ProviderOnboardingStateRouteReady {
				return ErrProviderOnboardingRouteNotReady
			}
			ready, err := providerOnboardingCurrentRouteReadyTx(tx, spec, credential, expectedRoutes)
			if err != nil {
				return err
			}
			if !ready {
				return ErrProviderOnboardingRouteNotReady
			}
		}
		if current.Channel.Status == targetStatus {
			snapshot = current
			return nil
		}
		if expectedRevision != PlatformChannelControlRevision(current.Channel) {
			return ErrPlatformChannelControlRevisionConflict
		}
		if err := updateProviderOnboardingChannelTx(tx, current.Channel, map[string]any{
			"status": targetStatus,
		}); err != nil {
			return err
		}
		if err := tx.Model(&Ability{}).Where("channel_id = ?", spec.ChannelID).
			Update("enabled", false).Error; err != nil {
			return err
		}
		snapshot, _, err = loadProviderOnboardingChannelTx(tx, spec, false)
		if err == nil {
			changed = true
		}
		return err
	})
	return snapshot, changed, err
}

func ProviderOnboardingManagedChannelError(channelID int) error {
	if !IsProviderOnboardingReservedChannelID(channelID) {
		return nil
	}
	return fmt.Errorf("%w: channel_id=%d", ErrProviderOnboardingManagedChannel, channelID)
}
