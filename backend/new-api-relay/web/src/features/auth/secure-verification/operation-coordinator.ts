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

export type SecureVerificationOperation = {
  epoch: number
  controller: AbortController
}

/**
 * Owns the single live verification operation for a hook instance. Every new
 * start, cancel, or unmount permanently invalidates older async completions.
 */
export class SecureVerificationOperationCoordinator {
  private epoch = 0
  private current: SecureVerificationOperation | null = null

  begin(): SecureVerificationOperation {
    this.invalidate()
    const operation = {
      epoch: this.epoch,
      controller: new AbortController(),
    }
    this.current = operation
    return operation
  }

  invalidate(): void {
    this.epoch += 1
    this.current?.controller.abort()
    this.current = null
  }

  isCurrent(operation: SecureVerificationOperation): boolean {
    return (
      this.current === operation &&
      this.epoch === operation.epoch &&
      !operation.controller.signal.aborted
    )
  }
}
