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
import {
  DEFAULT_LOGO,
  DEFAULT_SYSTEM_NAME,
  DEFAULT_WORDMARK,
  UPSTREAM_DEFAULT_LOGO,
  UPSTREAM_DEFAULT_SYSTEM_NAME,
} from '@/lib/constants'

export type SystemBrandInput = {
  systemName?: string | null
  logo?: string | null
}

export type SystemBrand = {
  systemName: string
  logo: string
}

function normalize(value: string | null | undefined): string {
  return value?.trim() ?? ''
}

/**
 * Upgrades only the recognizable, uncustomized upstream defaults. Any other
 * runtime value remains authoritative, so operators keep their own branding.
 */
export function resolveSystemBrand(input: SystemBrandInput): SystemBrand {
  const configuredName = normalize(input.systemName)
  const configuredLogo = normalize(input.logo)

  return {
    systemName:
      !configuredName || configuredName === UPSTREAM_DEFAULT_SYSTEM_NAME
        ? DEFAULT_SYSTEM_NAME
        : configuredName,
    logo:
      !configuredLogo || configuredLogo === UPSTREAM_DEFAULT_LOGO
        ? DEFAULT_LOGO
        : configuredLogo,
  }
}

export function usesApprovedSystemBrand(input: SystemBrandInput): boolean {
  const resolved = resolveSystemBrand(input)
  return (
    resolved.systemName === DEFAULT_SYSTEM_NAME &&
    (resolved.logo === DEFAULT_LOGO || resolved.logo === DEFAULT_WORDMARK)
  )
}
