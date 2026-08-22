package model

import (
	"fmt"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/glebarez/sqlite"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

func useFrontendOptionMigrationDB(t *testing.T) *gorm.DB {
	t.Helper()
	previousDB := DB
	previousType := common.MainDatabaseType()
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	require.NoError(t, err)
	require.NoError(t, db.AutoMigrate(&Option{}))
	DB = db
	common.SetMainDatabaseType(common.DatabaseTypeSQLite)
	t.Cleanup(func() {
		DB = previousDB
		common.SetMainDatabaseType(previousType)
	})
	return db
}

func requireOptionValue(t *testing.T, db *gorm.DB, key string) string {
	t.Helper()
	var option Option
	require.NoError(t, db.Where(&Option{Key: key}).First(&option).Error)
	return option.Value
}

func requireOptionMissing(t *testing.T, db *gorm.DB, key string) {
	t.Helper()
	var option Option
	assert.ErrorIs(t, db.Where(&Option{Key: key}).First(&option).Error, gorm.ErrRecordNotFound)
}

func TestMigrateRetiredFrontendOptionsMigratesValidValuesIdempotently(t *testing.T) {
	db := useFrontendOptionMigrationDB(t)
	legacy := []Option{
		{Key: retiredThemeOptionKey, Value: "classic"},
		{Key: "ApiInfo", Value: `[{"url":"https://api.example.com","route":"primary","description":"API","color":"blue"}]`},
		{Key: "Announcements", Value: `[{"content":"maintenance","publishDate":"2026-07-20T00:00:00Z","type":"warning"}]`},
		{Key: "FAQ", Value: `[{"title":"Question","content":"Answer"}]`},
		{Key: "UptimeKumaUrl", Value: "https://status.example.com"},
		{Key: "UptimeKumaSlug", Value: "status"},
	}
	require.NoError(t, db.Create(&legacy).Error)

	require.NoError(t, MigrateRetiredFrontendOptions())
	assert.Equal(t, "default", requireOptionValue(t, db, retiredThemeOptionKey))
	assert.JSONEq(t, legacy[1].Value, requireOptionValue(t, db, "console_setting.api_info"))
	assert.Equal(t, legacy[2].Value, requireOptionValue(t, db, "console_setting.announcements"))
	assert.JSONEq(t, `[{"question":"Question","answer":"Answer"}]`, requireOptionValue(t, db, "console_setting.faq"))
	assert.JSONEq(t, `[{
		"id":1,"categoryName":"old","url":"https://status.example.com","slug":"status","description":""
	}]`, requireOptionValue(t, db, "console_setting.uptime_kuma_groups"))
	for _, key := range []string{"ApiInfo", "Announcements", "FAQ", "UptimeKumaUrl", "UptimeKumaSlug"} {
		requireOptionMissing(t, db, key)
	}

	before, err := AllOption()
	require.NoError(t, err)
	require.NoError(t, MigrateRetiredFrontendOptions())
	after, err := AllOption()
	require.NoError(t, err)
	assert.ElementsMatch(t, before, after)
}

func TestLegacyConsoleListMigrationCapsAPIInfoAndFAQ(t *testing.T) {
	apiInfo := make([]map[string]any, 51)
	faq := make([]map[string]any, 51)
	for i := range apiInfo {
		apiInfo[i] = map[string]any{
			"url":         fmt.Sprintf("https://api-%d.example.com", i),
			"route":       fmt.Sprintf("route-%d", i),
			"description": "API",
			"color":       "blue",
		}
		faq[i] = map[string]any{"title": fmt.Sprintf("Question %d", i), "content": "Answer"}
	}
	apiBytes, err := common.Marshal(apiInfo)
	require.NoError(t, err)
	faqBytes, err := common.Marshal(faq)
	require.NoError(t, err)

	migratedAPI, err := transformLegacyAPIInfo(string(apiBytes))
	require.NoError(t, err)
	migratedFAQ, err := transformLegacyFAQ(string(faqBytes))
	require.NoError(t, err)
	var apiResult []map[string]any
	require.NoError(t, common.UnmarshalJsonStr(migratedAPI, &apiResult))
	var faqResult []map[string]any
	require.NoError(t, common.UnmarshalJsonStr(migratedFAQ, &faqResult))
	assert.Len(t, apiResult, 50)
	assert.Len(t, faqResult, 50)
}

func TestStrictRetiredFrontendOptionMigrationRejectsOversizedListsWithoutDeletingSource(t *testing.T) {
	apiInfo := make([]map[string]any, 51)
	faq := make([]map[string]any, 51)
	for index := range apiInfo {
		apiInfo[index] = map[string]any{
			"url":         fmt.Sprintf("https://api-%d.example.com", index),
			"route":       fmt.Sprintf("route-%d", index),
			"description": "API",
			"color":       "blue",
		}
		faq[index] = map[string]any{"title": fmt.Sprintf("Question %d", index), "content": "Answer"}
	}
	tests := []struct {
		name   string
		source string
		target string
		value  any
	}{
		{name: "api info", source: "ApiInfo", target: "console_setting.api_info", value: apiInfo},
		{name: "faq", source: "FAQ", target: "console_setting.faq", value: faq},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			db := useFrontendOptionMigrationDB(t)
			encoded, err := common.Marshal(test.value)
			require.NoError(t, err)
			require.NoError(t, db.Create(&Option{Key: test.source, Value: string(encoded)}).Error)

			err = db.Transaction(func(tx *gorm.DB) error {
				return migrateRetiredFrontendOptionsStrictWithDB(tx)
			})
			require.ErrorContains(t, err, "strict migration limit is 50")
			assert.Equal(t, string(encoded), requireOptionValue(t, db, test.source))
			requireOptionMissing(t, db, test.target)
		})
	}
}

func TestStrictRetiredFrontendOptionMigrationRejectsConflictingExistingTargets(t *testing.T) {
	tests := []struct {
		name    string
		options []Option
		sources []string
		target  string
	}{
		{
			name: "api info",
			options: []Option{
				{Key: "ApiInfo", Value: `[{"url":"https://old.example.com","route":"old","description":"old","color":"blue"}]`},
				{Key: "console_setting.api_info", Value: `[{"url":"https://new.example.com","route":"new","description":"new","color":"green"}]`},
			},
			sources: []string{"ApiInfo"},
			target:  "console_setting.api_info",
		},
		{
			name: "uptime",
			options: []Option{
				{Key: "UptimeKumaUrl", Value: "https://old.example.com"},
				{Key: "UptimeKumaSlug", Value: "old"},
				{Key: "console_setting.uptime_kuma_groups", Value: `[{"id":1,"categoryName":"old","url":"https://new.example.com","slug":"new","description":""}]`},
			},
			sources: []string{"UptimeKumaUrl", "UptimeKumaSlug"},
			target:  "console_setting.uptime_kuma_groups",
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			db := useFrontendOptionMigrationDB(t)
			require.NoError(t, db.Create(&test.options).Error)
			targetBefore := requireOptionValue(t, db, test.target)

			err := db.Transaction(func(tx *gorm.DB) error {
				return migrateRetiredFrontendOptionsStrictWithDB(tx)
			})
			require.ErrorContains(t, err, "conflict")
			assert.Equal(t, targetBefore, requireOptionValue(t, db, test.target))
			for _, source := range test.sources {
				requireOptionValue(t, db, source)
			}
		})
	}
}

func TestStrictRetiredFrontendOptionMigrationDeletesOnlyExactReplaySources(t *testing.T) {
	db := useFrontendOptionMigrationDB(t)
	options := []Option{
		{Key: "ApiInfo", Value: `[{"url":"https://api.example.com","route":"primary","description":"API","color":"blue"}]`},
		{Key: "console_setting.api_info", Value: `[{"color":"blue","description":"API","route":"primary","url":"https://api.example.com"}]`},
		{Key: "UptimeKumaUrl", Value: "https://status.example.com"},
		{Key: "UptimeKumaSlug", Value: "status"},
		{Key: "console_setting.uptime_kuma_groups", Value: `[{"slug":"status","url":"https://status.example.com","description":"","categoryName":"old","id":1}]`},
	}
	require.NoError(t, db.Create(&options).Error)

	require.NoError(t, db.Transaction(func(tx *gorm.DB) error {
		return migrateRetiredFrontendOptionsStrictWithDB(tx)
	}))
	for _, source := range []string{"ApiInfo", "UptimeKumaUrl", "UptimeKumaSlug"} {
		requireOptionMissing(t, db, source)
	}
	assert.JSONEq(t, options[1].Value, requireOptionValue(t, db, "console_setting.api_info"))
	assert.JSONEq(t, options[4].Value, requireOptionValue(t, db, "console_setting.uptime_kuma_groups"))
}

func TestMigrateRetiredFrontendOptionsPreservesMalformedValuesAndContinues(t *testing.T) {
	db := useFrontendOptionMigrationDB(t)
	legacy := []Option{
		{Key: "ApiInfo", Value: `{invalid`},
		{Key: "FAQ", Value: `[{"question":"Question","answer":"Answer"}]`},
		{Key: "UptimeKumaUrl", Value: "https://status.example.com"},
	}
	require.NoError(t, db.Create(&legacy).Error)

	require.NoError(t, MigrateRetiredFrontendOptions())
	assert.Equal(t, `{invalid`, requireOptionValue(t, db, "ApiInfo"))
	requireOptionMissing(t, db, "console_setting.api_info")
	requireOptionMissing(t, db, "FAQ")
	assert.JSONEq(t, legacy[1].Value, requireOptionValue(t, db, "console_setting.faq"))
	assert.Equal(t, "https://status.example.com", requireOptionValue(t, db, "UptimeKumaUrl"))
	requireOptionMissing(t, db, "console_setting.uptime_kuma_groups")
}

func TestMigrateRetiredFrontendOptionsPreservesMixedInvalidFAQ(t *testing.T) {
	db := useFrontendOptionMigrationDB(t)
	legacyFAQ := `[{"question":"Valid question","answer":"Valid answer"},{"question":"Missing answer"}]`
	require.NoError(t, db.Create(&Option{Key: "FAQ", Value: legacyFAQ}).Error)

	require.NoError(t, MigrateRetiredFrontendOptions())
	assert.Equal(t, legacyFAQ, requireOptionValue(t, db, "FAQ"))
	requireOptionMissing(t, db, "console_setting.faq")
}

func TestMigrateRetiredFrontendOptionsKeepsAuthoritativeTargets(t *testing.T) {
	db := useFrontendOptionMigrationDB(t)
	options := []Option{
		{Key: "ApiInfo", Value: `{invalid`},
		{Key: "console_setting.api_info", Value: `[{"url":"https://new.example.com"}]`},
		{Key: "UptimeKumaUrl", Value: "https://old.example.com"},
		{Key: "UptimeKumaSlug", Value: "old"},
		{Key: "console_setting.uptime_kuma_groups", Value: `[{"url":"https://new.example.com"}]`},
	}
	require.NoError(t, db.Create(&options).Error)

	require.NoError(t, MigrateRetiredFrontendOptions())
	assert.Equal(t, options[1].Value, requireOptionValue(t, db, "console_setting.api_info"))
	assert.Equal(t, options[4].Value, requireOptionValue(t, db, "console_setting.uptime_kuma_groups"))
	for _, key := range []string{"ApiInfo", "UptimeKumaUrl", "UptimeKumaSlug"} {
		requireOptionMissing(t, db, key)
	}
}

func TestMigrateRetiredFrontendOptionsKeepsEmptyAuthoritativeTargets(t *testing.T) {
	db := useFrontendOptionMigrationDB(t)
	options := []Option{
		{Key: "ApiInfo", Value: `[{"url":"https://old.example.com"}]`},
		{Key: "console_setting.api_info", Value: ""},
		{Key: "UptimeKumaUrl", Value: "https://old.example.com"},
		{Key: "UptimeKumaSlug", Value: "old"},
		{Key: "console_setting.uptime_kuma_groups", Value: ""},
	}
	require.NoError(t, db.Create(&options).Error)

	require.NoError(t, MigrateRetiredFrontendOptions())
	assert.Empty(t, requireOptionValue(t, db, "console_setting.api_info"))
	assert.Empty(t, requireOptionValue(t, db, "console_setting.uptime_kuma_groups"))
	for _, key := range []string{"ApiInfo", "UptimeKumaUrl", "UptimeKumaSlug"} {
		requireOptionMissing(t, db, key)
	}
}

func TestRetiredThemeOptionIsPersistedButNotPublished(t *testing.T) {
	db := useFrontendOptionMigrationDB(t)
	previousMap := common.OptionMap
	t.Cleanup(func() { common.OptionMap = previousMap })
	common.OptionMap = map[string]string{}

	require.NoError(t, UpdateOption(retiredThemeOptionKey, "default"))
	assert.Equal(t, "default", requireOptionValue(t, db, retiredThemeOptionKey))
	_, published := common.OptionMap[retiredThemeOptionKey]
	assert.False(t, published)
}
