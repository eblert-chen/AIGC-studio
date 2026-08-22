package model

import (
	"fmt"
	"time"

	"github.com/QuantumNous/new-api/common"
	"gorm.io/gorm"
)

// GetDBTimeTx returns the database clock for coordination-sensitive work.
// Unlike GetDBTimestamp it never falls back to the process clock: a worker
// must not claim a lease if the shared clock cannot be read.
func GetDBTimeTx(tx *gorm.DB) (time.Time, error) {
	if tx == nil {
		return time.Time{}, fmt.Errorf("database handle is required")
	}
	if common.UsingMainDatabase(common.DatabaseTypePostgreSQL) {
		var timestamp time.Time
		if err := tx.Raw("SELECT clock_timestamp()").Scan(&timestamp).Error; err != nil {
			return time.Time{}, fmt.Errorf("read database clock: %w", err)
		}
		if timestamp.IsZero() {
			return time.Time{}, fmt.Errorf("database returned an invalid clock value")
		}
		return timestamp.UTC(), nil
	}
	var timestamp int64
	var err error
	switch {
	case common.UsingMainDatabase(common.DatabaseTypeSQLite):
		err = tx.Raw("SELECT CAST(strftime('%s','now') AS INTEGER)").Scan(&timestamp).Error
	default:
		err = tx.Raw("SELECT UNIX_TIMESTAMP()").Scan(&timestamp).Error
	}
	if err != nil {
		return time.Time{}, fmt.Errorf("read database clock: %w", err)
	}
	if timestamp <= 0 {
		return time.Time{}, fmt.Errorf("database returned an invalid clock value")
	}
	return time.Unix(timestamp, 0).UTC(), nil
}

func unexpiredDatabaseLeasePredicate(column string) string {
	switch {
	case common.UsingMainDatabase(common.DatabaseTypePostgreSQL):
		return column + " > clock_timestamp()"
	case common.UsingMainDatabase(common.DatabaseTypeSQLite):
		return column + " > CURRENT_TIMESTAMP"
	default:
		return column + " > CURRENT_TIMESTAMP(6)"
	}
}

// GetDBTimestamp returns a UNIX timestamp from database time.
// Falls back to application time on error.
func GetDBTimestamp() int64 {
	var ts int64
	var err error
	switch {
	case common.UsingMainDatabase(common.DatabaseTypePostgreSQL):
		err = DB.Raw("SELECT EXTRACT(EPOCH FROM NOW())::bigint").Scan(&ts).Error
	case common.UsingMainDatabase(common.DatabaseTypeSQLite):
		err = DB.Raw("SELECT strftime('%s','now')").Scan(&ts).Error
	default:
		err = DB.Raw("SELECT UNIX_TIMESTAMP()").Scan(&ts).Error
	}
	if err != nil || ts <= 0 {
		return common.GetTimestamp()
	}
	return ts
}
