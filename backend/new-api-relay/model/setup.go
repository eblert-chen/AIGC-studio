package model

import "github.com/QuantumNous/new-api/common"

type Setup struct {
	ID            uint   `json:"id" gorm:"primaryKey"`
	Version       string `json:"version" gorm:"type:varchar(50);not null"`
	InitializedAt int64  `json:"initialized_at" gorm:"type:bigint;not null"`
}

func GetSetup() *Setup {
	var setup Setup
	err := DB.First(&setup).Error
	if err != nil {
		return nil
	}
	return &setup
}

func SetupReady() bool {
	if DB == nil {
		return false
	}
	var setups []Setup
	if err := DB.Order("id ASC").Find(&setups).Error; err != nil || len(setups) != 1 ||
		setups[0].Version == "" || setups[0].InitializedAt <= 0 {
		return false
	}
	var roots []User
	if err := DB.Where("role = ?", common.RoleRootUser).Order("id ASC").Find(&roots).Error; err != nil || len(roots) != 1 {
		return false
	}
	return roots[0].Status == common.UserStatusEnabled
}
