/*
Copyright (C) 2023-2026 QuantumNous

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as
published by the Free Software Foundation, either version 3 of the
License, or (at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this program. If not, see <https://www.gnu.org/licenses/>.

For commercial licensing, please contact support@quantumnous.com
*/
import assert from 'node:assert/strict'
import { describe, test } from 'bun:test'

import {
  DEFAULT_LOGO,
  DEFAULT_SYSTEM_NAME,
} from '@/lib/constants'
import {
  resolveSystemBrand,
  usesApprovedSystemBrand,
} from '@/lib/system-brand'

describe('Relay system brand resolver', () => {
  test('upgrades only the recognizable upstream defaults to the approved Relay brand', () => {
    const brand = resolveSystemBrand({
      systemName: 'New API',
      logo: '/logo.png',
    })

    assert.deepEqual(brand, {
      systemName: DEFAULT_SYSTEM_NAME,
      logo: DEFAULT_LOGO,
    })
    assert.equal(usesApprovedSystemBrand(brand), true)
  })

  test('keeps operator-provided runtime branding authoritative', () => {
    const brand = resolveSystemBrand({
      systemName: 'Northwind Relay',
      logo: 'https://assets.example.test/northwind.svg',
    })

    assert.deepEqual(brand, {
      systemName: 'Northwind Relay',
      logo: 'https://assets.example.test/northwind.svg',
    })
    assert.equal(usesApprovedSystemBrand(brand), false)
  })
})
