package model

import (
	"testing"

	"github.com/glebarez/sqlite"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

func TestMaterializeRelayCatalogModelMetadataIsDisabledAndNonDestructive(t *testing.T) {
	db, err := gorm.Open(sqlite.Open("file:relay_catalog_model_metadata?mode=memory&cache=shared"), &gorm.Config{})
	require.NoError(t, err)
	require.NoError(t, db.AutoMigrate(&Model{}, &Channel{}, &Ability{}, &Vendor{}))

	operatorModel := Model{
		ModelName: "operator-model", Description: "operator owned", Status: 1,
		SyncOfficial: 1, VendorID: 77, CreatedTime: 11, UpdatedTime: 22,
	}
	require.NoError(t, db.Create(&operatorModel).Error)
	deletedModel := Model{ModelName: "operator-deleted", Description: "do not resurrect", Status: 1, SyncOfficial: 1}
	require.NoError(t, db.Create(&deletedModel).Error)
	require.NoError(t, db.Delete(&deletedModel).Error)

	inserted, err := MaterializeRelayCatalogModelMetadata(db, []string{
		"veo-3.1", "seedream-5", "veo-3.1", "operator-model", "operator-deleted",
	})
	require.NoError(t, err)
	assert.Equal(t, 2, inserted)

	for _, modelID := range []string{"seedream-5", "veo-3.1"} {
		var metadata Model
		require.NoError(t, db.Where("model_name = ?", modelID).First(&metadata).Error)
		assert.Zero(t, metadata.Status)
		assert.Zero(t, metadata.SyncOfficial)
		assert.Zero(t, metadata.VendorID)
		assert.Equal(t, NameRuleExact, metadata.NameRule)
		assert.Equal(t, relayCatalogModelDescription, metadata.Description)
		assert.NotZero(t, metadata.CreatedTime)
		assert.Equal(t, metadata.CreatedTime, metadata.UpdatedTime)
	}

	var preserved Model
	require.NoError(t, db.Where("model_name = ?", operatorModel.ModelName).First(&preserved).Error)
	assert.Equal(t, "operator owned", preserved.Description)
	assert.Equal(t, 1, preserved.Status)
	assert.Equal(t, 1, preserved.SyncOfficial)
	assert.Equal(t, 77, preserved.VendorID)
	assert.Equal(t, int64(22), preserved.UpdatedTime)
	var resurrected int64
	require.NoError(t, db.Model(&Model{}).Where("model_name = ?", deletedModel.ModelName).Count(&resurrected).Error)
	assert.Zero(t, resurrected)

	for name, target := range map[string]any{
		"channels": &Channel{}, "abilities": &Ability{}, "vendors": &Vendor{},
	} {
		var count int64
		require.NoError(t, db.Model(target).Count(&count).Error)
		assert.Zero(t, count, name+" must not be synthesized")
	}

	repeated, err := MaterializeRelayCatalogModelMetadata(db, []string{
		"operator-deleted", "operator-model", "seedream-5", "veo-3.1",
	})
	require.NoError(t, err)
	assert.Zero(t, repeated)
	var total int64
	require.NoError(t, db.Unscoped().Model(&Model{}).Count(&total).Error)
	assert.Equal(t, int64(4), total)
}

func TestMaterializeRelayCatalogModelMetadataRejectsInvalidIDWithoutPartialWrites(t *testing.T) {
	db, err := gorm.Open(sqlite.Open("file:relay_catalog_model_metadata_invalid?mode=memory&cache=shared"), &gorm.Config{})
	require.NoError(t, err)
	require.NoError(t, db.AutoMigrate(&Model{}))

	inserted, err := MaterializeRelayCatalogModelMetadata(db, []string{"veo-3.1", " leading-space"})
	require.Error(t, err)
	assert.Zero(t, inserted)
	var count int64
	require.NoError(t, db.Model(&Model{}).Count(&count).Error)
	assert.Zero(t, count)
}
