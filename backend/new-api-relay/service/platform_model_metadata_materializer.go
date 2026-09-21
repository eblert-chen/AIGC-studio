package service

import (
	"fmt"

	"github.com/QuantumNous/new-api/model"
)

// MaterializePlatformRelayModelMetadata mirrors the merged, secret-free Relay
// catalog into native New API's model-directory table. It runs only from the
// schema-current startup path after route synchronization; availability and
// channel binding continue to come exclusively from real routes and abilities.
func MaterializePlatformRelayModelMetadata() (int, error) {
	snapshot := loadPlatformRelayConfig()
	if snapshot.err != nil {
		return 0, snapshot.err
	}
	modelIDs := make([]string, 0, len(snapshot.catalog.Data))
	for _, resource := range snapshot.catalog.Data {
		modelIDs = append(modelIDs, resource.ID)
	}
	inserted, err := model.MaterializeRelayCatalogModelMetadata(model.DB, modelIDs)
	if err != nil {
		return 0, fmt.Errorf("materialize Relay model metadata: %w", err)
	}
	return inserted, nil
}
