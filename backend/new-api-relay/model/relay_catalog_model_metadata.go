package model

import (
	"fmt"
	"sort"
	"strings"

	"gorm.io/gorm"
)

const relayCatalogModelDescription = "Relay catalog entry; disabled until real channel, route and release evidence are configured."

// MaterializeRelayCatalogModelMetadata creates native New API model-directory
// rows for a validated Relay catalog. It never enables a model and never
// creates a channel, credential, ability, vendor or price. Existing rows,
// including operator-deleted rows, are evidence and are never overwritten or
// resurrected by this materializer.
func MaterializeRelayCatalogModelMetadata(db *gorm.DB, modelIDs []string) (int, error) {
	if db == nil {
		return 0, fmt.Errorf("Relay model metadata database is unavailable")
	}
	unique := make(map[string]struct{}, len(modelIDs))
	for _, modelID := range modelIDs {
		if modelID == "" || strings.TrimSpace(modelID) != modelID || len(modelID) > 128 || strings.ContainsAny(modelID, "\x00\r\n\t") {
			return 0, fmt.Errorf("Relay catalog model id %q is invalid", modelID)
		}
		unique[modelID] = struct{}{}
	}
	ordered := make([]string, 0, len(unique))
	for modelID := range unique {
		ordered = append(ordered, modelID)
	}
	sort.Strings(ordered)
	if len(ordered) == 0 {
		return 0, nil
	}

	inserted := 0
	err := db.Transaction(func(tx *gorm.DB) error {
		// Model's historical composite soft-delete index permits more than one
		// NULL deleted_at value on PostgreSQL. Serialize all catalog inserts at
		// the table boundary so concurrent startup materializers cannot create
		// duplicate live metadata rows.
		if tx.Dialector.Name() == "postgres" {
			if err := tx.Exec("LOCK TABLE models IN SHARE ROW EXCLUSIVE MODE").Error; err != nil {
				return fmt.Errorf("lock Relay model metadata table: %w", err)
			}
		}
		var existing []Model
		if err := tx.Unscoped().Where("model_name IN ?", ordered).Find(&existing).Error; err != nil {
			return fmt.Errorf("read Relay model metadata: %w", err)
		}
		present := make(map[string]struct{}, len(existing))
		for _, item := range existing {
			present[item.ModelName] = struct{}{}
		}
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return fmt.Errorf("read Relay model metadata database time: %w", err)
		}
		for _, modelID := range ordered {
			if _, exists := present[modelID]; exists {
				continue
			}
			// A map insert preserves the explicit zero values despite Model's
			// upstream GORM defaults of status=1 and sync_official=1.
			result := tx.Table("models").Create(map[string]any{
				"model_name":    modelID,
				"description":   relayCatalogModelDescription,
				"icon":          "",
				"tags":          "",
				"vendor_id":     0,
				"endpoints":     "",
				"status":        0,
				"sync_official": 0,
				"created_time":  now.Unix(),
				"updated_time":  now.Unix(),
				"name_rule":     NameRuleExact,
			})
			if result.Error != nil {
				return fmt.Errorf("insert Relay model metadata for %q: %w", modelID, result.Error)
			}
			if result.RowsAffected != 1 {
				return fmt.Errorf("insert Relay model metadata for %q affected an unexpected row count", modelID)
			}
			present[modelID] = struct{}{}
			inserted++
		}
		return nil
	})
	return inserted, err
}
