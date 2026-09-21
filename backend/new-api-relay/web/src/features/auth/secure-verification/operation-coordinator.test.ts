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
import { describe, test } from 'bun:test'
import assert from 'node:assert/strict'

import { SecureVerificationOperationCoordinator } from './operation-coordinator'

describe('SecureVerificationOperationCoordinator', () => {
  test('a reopen aborts the old operation and only the new epoch stays current', () => {
    const coordinator = new SecureVerificationOperationCoordinator()
    const first = coordinator.begin()
    const second = coordinator.begin()

    assert.equal(first.controller.signal.aborted, true)
    assert.equal(coordinator.isCurrent(first), false)
    assert.equal(second.controller.signal.aborted, false)
    assert.equal(coordinator.isCurrent(second), true)
    assert.notEqual(first.epoch, second.epoch)
  })

  test('cancel or unmount permanently fences a late completion', async () => {
    const coordinator = new SecureVerificationOperationCoordinator()
    const operation = coordinator.begin()
    let resolveSlow!: (value: string) => void
    const slow = new Promise<string>((resolve) => {
      resolveSlow = resolve
    })
    let observed = ''
    const completion = slow.then((value) => {
      if (coordinator.isCurrent(operation)) observed = value
    })

    coordinator.invalidate()
    resolveSlow('stale result')
    await completion

    assert.equal(operation.controller.signal.aborted, true)
    assert.equal(coordinator.isCurrent(operation), false)
    assert.equal(observed, '')
  })
})
