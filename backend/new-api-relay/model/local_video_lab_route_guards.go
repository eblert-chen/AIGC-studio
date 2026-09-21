//go:build relay_local_video_lab

package model

import (
	"encoding/hex"
	"errors"
	"os"
	"path/filepath"
	"strings"

	"github.com/QuantumNous/new-api/common"
	"gorm.io/gorm"
)

// InstallLocalVideoLabRouteBindingGuardsWithDB only wires the existing frozen
// V4 guard installer into the separately built lab. It does not run a schema
// migration, alter columns, rewrite rows, or exist in production binaries.
func InstallLocalVideoLabRouteBindingGuardsWithDB(db *gorm.DB, directory, stateID, username string) error {
	invalid := errors.New("local video route guards require the exact isolated development database")
	if db == nil || db.Dialector.Name() != "sqlite" || RelayDatabaseRoleAttestationRequired() || !filepath.IsAbs(directory) {
		return invalid
	}
	for _, name := range []string{"APP_ENV", "DEPLOYMENT_ENV", "ENVIRONMENT", "RELAY_COMPAT_ENVIRONMENT"} {
		if os.Getenv(name) != "development" {
			return invalid
		}
	}
	mode, digest, found := strings.Cut(stateID, "-")
	decoded, err := hex.DecodeString(digest)
	if !found || (mode != "mock" && mode != "live") || err != nil || len(decoded) != 32 || hex.EncodeToString(decoded) != digest ||
		username != "local-video-"+digest[:16] || os.Getenv("RELAY_COMPAT_DELAY_QUEUE_NAMESPACE") != "local-video-lab-"+stateID {
		return invalid
	}
	var databases []struct {
		Name string
		File string
	}
	if err := db.Raw("PRAGMA database_list").Scan(&databases).Error; err != nil {
		return invalid
	}
	matched := false
	for _, database := range databases {
		if database.Name == "main" && filepath.Clean(database.File) == filepath.Join(filepath.Clean(directory), "relay.sqlite") {
			matched = true
		}
	}
	if !matched {
		return invalid
	}
	var user User
	if err := db.Select("id").Where("username = ? AND role = ? AND status = ?", username, common.RoleCommonUser, common.UserStatusEnabled).First(&user).Error; err != nil {
		return invalid
	}
	var tokens int64
	if err := db.Model(&Token{}).Where("user_id = ? AND name = ? AND status = ?", user.Id, "local-video-platform-external", common.TokenStatusEnabled).Count(&tokens).Error; err != nil || tokens != 1 {
		return invalid
	}
	return db.Transaction(func(tx *gorm.DB) error { return installPlatformGenerationRouteBindingGuardsV4(tx) })
}
