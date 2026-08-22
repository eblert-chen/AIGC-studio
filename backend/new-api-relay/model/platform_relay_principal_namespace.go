package model

import (
	"errors"
	"strings"

	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

const (
	PlatformRelayServicePrincipalTokenNamePrefix = "platform-relay:"
	PlatformRelayServicePrincipalUsernamePrefix  = "rsvc_"
	PlatformRelayServicePrincipalRemark          = "platform-relay-service-v1"
)

var ErrPlatformRelayServicePrincipalNamespaceReserved = errors.New("Platform Relay service principal namespace is reserved")

func protectedPlatformRelayTokenNameIsReserved(name string) bool {
	return RelayDatabaseRoleAttestationRequired() && strings.HasPrefix(name, PlatformRelayServicePrincipalTokenNamePrefix)
}

func PlatformRelayServicePrincipalUsernameIsReserved(username string) bool {
	return strings.HasPrefix(strings.ToLower(username), PlatformRelayServicePrincipalUsernamePrefix)
}

func protectedPlatformRelayUserIdentityIsReserved(username string, remark string) bool {
	return RelayDatabaseRoleAttestationRequired() &&
		(PlatformRelayServicePrincipalUsernameIsReserved(username) || remark == PlatformRelayServicePrincipalRemark)
}

func rejectProtectedPlatformRelayServiceUserMutation(tx *gorm.DB, userID int) error {
	if !RelayDatabaseRoleAttestationRequired() || userID <= 0 {
		return nil
	}
	var current User
	err := tx.Unscoped().Clauses(clause.Locking{Strength: "UPDATE"}).
		Select("id, username, remark").Where("id = ?", userID).First(&current).Error
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return nil
	}
	if err != nil {
		return err
	}
	if protectedPlatformRelayUserIdentityIsReserved(current.Username, current.Remark) {
		return ErrPlatformRelayServicePrincipalNamespaceReserved
	}
	return nil
}

func rejectProtectedPlatformRelayTokenMutation(tx *gorm.DB, tokenID int, proposedName string) error {
	if !RelayDatabaseRoleAttestationRequired() {
		return nil
	}
	if protectedPlatformRelayTokenNameIsReserved(proposedName) {
		return ErrPlatformRelayServicePrincipalNamespaceReserved
	}
	if tokenID <= 0 {
		return nil
	}
	var current Token
	err := tx.Unscoped().Clauses(clause.Locking{Strength: "UPDATE"}).
		Select("id, name").Where("id = ?", tokenID).First(&current).Error
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return nil
	}
	if err != nil {
		return err
	}
	if protectedPlatformRelayTokenNameIsReserved(current.Name) {
		return ErrPlatformRelayServicePrincipalNamespaceReserved
	}
	return nil
}

func withProtectedPlatformRelayServiceUserMutation(
	database *gorm.DB,
	userID int,
	mutation func(*gorm.DB) error,
) error {
	if !RelayDatabaseRoleAttestationRequired() {
		return mutation(database)
	}
	return database.Transaction(func(tx *gorm.DB) error {
		if err := rejectProtectedPlatformRelayServiceUserMutation(tx, userID); err != nil {
			return err
		}
		return mutation(tx)
	})
}

func withProtectedPlatformRelayTokenMutation(
	database *gorm.DB,
	tokenID int,
	proposedName string,
	mutation func(*gorm.DB) error,
) error {
	if !RelayDatabaseRoleAttestationRequired() {
		return mutation(database)
	}
	return database.Transaction(func(tx *gorm.DB) error {
		if err := rejectProtectedPlatformRelayTokenMutation(tx, tokenID, proposedName); err != nil {
			return err
		}
		return mutation(tx)
	})
}
