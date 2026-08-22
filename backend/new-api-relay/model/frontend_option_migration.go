package model

import (
	"errors"
	"fmt"
	"reflect"
	"strings"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/setting/console_setting"
	"gorm.io/gorm"
)

const retiredThemeOptionKey = "theme.frontend"

type legacyOptionTransform func(string) (string, error)

// MigrateRetiredFrontendOptions normalizes options that belonged to the
// removed dashboard frontend. Each legacy console setting is migrated in its
// own transaction so one malformed value cannot block the other settings.
func MigrateRetiredFrontendOptions() error {
	return migrateRetiredFrontendOptionsWithDB(DB, false)
}

// migrateRetiredFrontendOptionsStrictWithDB is the schema-v1 data migration.
// Unlike the legacy runtime compatibility wrapper, malformed source data is a
// migration failure so the enclosing schema transaction cannot be committed
// and recorded as current while a declared step was skipped.
func migrateRetiredFrontendOptionsStrictWithDB(db *gorm.DB) error {
	return migrateRetiredFrontendOptionsWithDB(db, true)
}

func migrateRetiredFrontendOptionsWithDB(db *gorm.DB, strict bool) error {
	if db == nil {
		return errors.New("database is not initialized")
	}

	var migrationErrors []error
	if err := normalizeRetiredThemeOptionWithDB(db); err != nil {
		migrationErrors = append(migrationErrors, fmt.Errorf("normalize %s: %w", retiredThemeOptionKey, err))
	}

	migrations := []struct {
		source          string
		target          string
		transform       legacyOptionTransform
		strictTransform legacyOptionTransform
	}{
		{source: "ApiInfo", target: "console_setting.api_info", transform: transformLegacyAPIInfo, strictTransform: transformLegacyAPIInfoStrict},
		{source: "Announcements", target: "console_setting.announcements", transform: transformLegacyAnnouncements},
		{source: "FAQ", target: "console_setting.faq", transform: transformLegacyFAQ, strictTransform: transformLegacyFAQStrict},
	}
	for _, migration := range migrations {
		if err := migrateLegacyOptionWithDB(db, migration.source, migration.target, migration.transform, migration.strictTransform, strict); err != nil {
			migrationErrors = append(migrationErrors, err)
		}
	}
	if err := migrateLegacyUptimeOptionsWithDB(db, strict); err != nil {
		migrationErrors = append(migrationErrors, err)
	}
	return errors.Join(migrationErrors...)
}

func normalizeRetiredThemeOption() error {
	return normalizeRetiredThemeOptionWithDB(DB)
}

func normalizeRetiredThemeOptionWithDB(db *gorm.DB) error {
	return db.Transaction(func(tx *gorm.DB) error {
		var option Option
		err := tx.Where(&Option{Key: retiredThemeOptionKey}).First(&option).Error
		if errors.Is(err, gorm.ErrRecordNotFound) {
			return tx.Create(&Option{Key: retiredThemeOptionKey, Value: "default"}).Error
		}
		if err != nil {
			return err
		}
		if option.Value == "default" {
			return nil
		}
		return tx.Model(&option).Update("value", "default").Error
	})
}

func migrateLegacyOption(sourceKey, targetKey string, transform legacyOptionTransform) error {
	return migrateLegacyOptionWithDB(DB, sourceKey, targetKey, transform, nil, false)
}

func migrateLegacyOptionWithDB(
	db *gorm.DB,
	sourceKey, targetKey string,
	transform, strictTransform legacyOptionTransform,
	strict bool,
) error {
	return db.Transaction(func(tx *gorm.DB) error {
		var source Option
		if err := tx.Where(&Option{Key: sourceKey}).First(&source).Error; err != nil {
			if errors.Is(err, gorm.ErrRecordNotFound) {
				return nil
			}
			return fmt.Errorf("read legacy option %s: %w", sourceKey, err)
		}

		var target Option
		err := tx.Where(&Option{Key: targetKey}).First(&target).Error
		if err != nil && !errors.Is(err, gorm.ErrRecordNotFound) {
			return fmt.Errorf("read target option %s: %w", targetKey, err)
		}
		if err == nil && !strict {
			return tx.Delete(&source).Error
		}
		selectedTransform := transform
		if strict && strictTransform != nil {
			selectedTransform = strictTransform
		}
		value, transformErr := selectedTransform(source.Value)
		if transformErr != nil {
			if strict {
				return fmt.Errorf("transform legacy option %s: %w", sourceKey, transformErr)
			}
			common.SysError(fmt.Sprintf("legacy option %s was not migrated: %v", sourceKey, transformErr))
			return nil
		}
		if err == nil {
			if strict && !legacyOptionValuesEquivalent(value, target.Value) {
				return fmt.Errorf("legacy option %s conflicts with existing target %s", sourceKey, targetKey)
			}
			return tx.Delete(&source).Error
		}
		if errors.Is(err, gorm.ErrRecordNotFound) {
			target = Option{Key: targetKey}
		}
		target.Value = value
		if err := tx.Save(&target).Error; err != nil {
			return fmt.Errorf("write target option %s: %w", targetKey, err)
		}
		if err := tx.Delete(&source).Error; err != nil {
			return fmt.Errorf("delete legacy option %s: %w", sourceKey, err)
		}
		return nil
	})
}

func transformLegacyAPIInfo(value string) (string, error) {
	return transformLegacyAPIInfoWithMode(value, false)
}

func transformLegacyAPIInfoStrict(value string) (string, error) {
	return transformLegacyAPIInfoWithMode(value, true)
}

func transformLegacyAPIInfoWithMode(value string, strict bool) (string, error) {
	if strings.TrimSpace(value) == "" {
		return "", errors.New("value is empty")
	}
	var items []map[string]any
	if err := common.UnmarshalJsonStr(value, &items); err != nil {
		return "", err
	}
	if len(items) > 50 {
		if strict {
			return "", fmt.Errorf("ApiInfo contains %d entries; strict migration limit is 50", len(items))
		}
		items = items[:50]
	}
	encoded, err := common.Marshal(items)
	if err != nil {
		return "", err
	}
	result := string(encoded)
	if err := console_setting.ValidateConsoleSettings(result, "ApiInfo"); err != nil {
		return "", err
	}
	return result, nil
}

func transformLegacyAnnouncements(value string) (string, error) {
	if strings.TrimSpace(value) == "" {
		return "", errors.New("value is empty")
	}
	if err := console_setting.ValidateConsoleSettings(value, "Announcements"); err != nil {
		return "", err
	}
	return value, nil
}

func transformLegacyFAQ(value string) (string, error) {
	return transformLegacyFAQWithMode(value, false)
}

func transformLegacyFAQStrict(value string) (string, error) {
	return transformLegacyFAQWithMode(value, true)
}

func transformLegacyFAQWithMode(value string, strict bool) (string, error) {
	if strings.TrimSpace(value) == "" {
		return "", errors.New("value is empty")
	}
	var legacyItems []map[string]any
	if err := common.UnmarshalJsonStr(value, &legacyItems); err != nil {
		return "", err
	}
	items := make([]map[string]any, 0, len(legacyItems))
	for index, item := range legacyItems {
		question, _ := item["question"].(string)
		if strings.TrimSpace(question) == "" {
			question, _ = item["title"].(string)
		}
		answer, _ := item["answer"].(string)
		if strings.TrimSpace(answer) == "" {
			answer, _ = item["content"].(string)
		}
		if strings.TrimSpace(question) == "" || strings.TrimSpace(answer) == "" {
			return "", fmt.Errorf("FAQ entry %d is missing a question or answer", index)
		}
		items = append(items, map[string]any{"question": question, "answer": answer})
	}
	if len(items) > 50 {
		if strict {
			return "", fmt.Errorf("FAQ contains %d entries; strict migration limit is 50", len(items))
		}
		items = items[:50]
	}
	encoded, err := common.Marshal(items)
	if err != nil {
		return "", err
	}
	result := string(encoded)
	if err := console_setting.ValidateConsoleSettings(result, "FAQ"); err != nil {
		return "", err
	}
	return result, nil
}

func legacyOptionValuesEquivalent(left, right string) bool {
	var leftValue any
	var rightValue any
	if common.UnmarshalJsonStr(left, &leftValue) == nil && common.UnmarshalJsonStr(right, &rightValue) == nil {
		return reflect.DeepEqual(leftValue, rightValue)
	}
	return strings.TrimSpace(left) == strings.TrimSpace(right)
}

func migrateLegacyUptimeOptions() error {
	return migrateLegacyUptimeOptionsWithDB(DB, false)
}

func migrateLegacyUptimeOptionsWithDB(db *gorm.DB, strict bool) error {
	return db.Transaction(func(tx *gorm.DB) error {
		var urlOption Option
		urlErr := tx.Where(&Option{Key: "UptimeKumaUrl"}).First(&urlOption).Error
		if urlErr != nil && !errors.Is(urlErr, gorm.ErrRecordNotFound) {
			return fmt.Errorf("read legacy option UptimeKumaUrl: %w", urlErr)
		}
		var slugOption Option
		slugErr := tx.Where(&Option{Key: "UptimeKumaSlug"}).First(&slugOption).Error
		if slugErr != nil && !errors.Is(slugErr, gorm.ErrRecordNotFound) {
			return fmt.Errorf("read legacy option UptimeKumaSlug: %w", slugErr)
		}
		if errors.Is(urlErr, gorm.ErrRecordNotFound) && errors.Is(slugErr, gorm.ErrRecordNotFound) {
			return nil
		}

		var target Option
		targetErr := tx.Where(&Option{Key: "console_setting.uptime_kuma_groups"}).First(&target).Error
		if targetErr != nil && !errors.Is(targetErr, gorm.ErrRecordNotFound) {
			return fmt.Errorf("read target option console_setting.uptime_kuma_groups: %w", targetErr)
		}
		if targetErr == nil {
			if strict {
				if urlErr != nil || slugErr != nil || strings.TrimSpace(urlOption.Value) == "" || strings.TrimSpace(slugOption.Value) == "" {
					return errors.New("legacy Uptime Kuma options require both URL and slug before deleting an existing target")
				}
				expected, err := buildLegacyUptimeGroupsValue(urlOption.Value, slugOption.Value)
				if err != nil {
					return err
				}
				if !legacyOptionValuesEquivalent(expected, target.Value) {
					return errors.New("legacy Uptime Kuma options conflict with the existing target")
				}
			}
			if urlErr == nil {
				if err := tx.Delete(&urlOption).Error; err != nil {
					return err
				}
			}
			if slugErr == nil {
				return tx.Delete(&slugOption).Error
			}
			return nil
		}

		if urlErr != nil || slugErr != nil || strings.TrimSpace(urlOption.Value) == "" || strings.TrimSpace(slugOption.Value) == "" {
			if strict {
				return errors.New("legacy Uptime Kuma options require both URL and slug")
			}
			common.SysError("legacy Uptime Kuma options were not migrated: both URL and slug are required")
			return nil
		}
		value, err := buildLegacyUptimeGroupsValue(urlOption.Value, slugOption.Value)
		if err != nil {
			if strict {
				return err
			}
			common.SysError(fmt.Sprintf("legacy Uptime Kuma options were not migrated: %v", err))
			return nil
		}
		if errors.Is(targetErr, gorm.ErrRecordNotFound) {
			target = Option{Key: "console_setting.uptime_kuma_groups"}
		}
		target.Value = value
		if err := tx.Save(&target).Error; err != nil {
			return fmt.Errorf("write target option console_setting.uptime_kuma_groups: %w", err)
		}
		if err := tx.Delete(&urlOption).Error; err != nil {
			return err
		}
		return tx.Delete(&slugOption).Error
	})
}

func buildLegacyUptimeGroupsValue(url, slug string) (string, error) {
	groups := []map[string]any{{
		"id":           1,
		"categoryName": "old",
		"url":          url,
		"slug":         slug,
		"description":  "",
	}}
	encoded, err := common.Marshal(groups)
	if err != nil {
		return "", err
	}
	value := string(encoded)
	if err := console_setting.ValidateConsoleSettings(value, "UptimeKumaGroups"); err != nil {
		return "", fmt.Errorf("validate legacy Uptime Kuma options: %w", err)
	}
	return value, nil
}
