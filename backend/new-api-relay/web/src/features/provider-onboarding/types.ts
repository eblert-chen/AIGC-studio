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

export type ProviderOnboardingProviderId = string

export type ProviderControlRevision = string | number

export interface ProviderOnboardingModel {
  id: string
  display_name: string
}

export interface ProviderCredentialSummary {
  configured: boolean
  fingerprint_prefix: string
  key_count: number
  updated_at: string | null
}

/**
 * Read model for the provider onboarding surface. Intentionally contains no
 * credential material: the frontend only accepts secrets in mutation bodies.
 */
export interface ProviderOnboardingProvider {
  id: ProviderOnboardingProviderId
  display_name: string
  region: string
  channel_id: string | null
  account_id: string
  base_url: string
  models: ProviderOnboardingModel[]
  credential: ProviderCredentialSummary
  lifecycle_state: string
  channel_status: string
  control_revision: ProviderControlRevision | null
  route_test_ready: boolean
  route_materialized: boolean
  route_declaration_verified: boolean
  route_declaration_fresh: boolean
  route_declaration_valid_until: string | null
  route_test_verified: boolean
  route_test_fresh: boolean
  route_test_latest_at: string | null
  acceptance_verified: boolean
  acceptance_fresh: boolean
  acceptance_valid_until: string | null
  platform_publication_status: string
  platform_price_status: string
  platform_grant_status: string
  can_disable: boolean
  can_resume: boolean
  action_blocker_code: string | null
  blocker_code: string | null
}

export interface ProviderOnboardingList {
  environment: string
  providers: ProviderOnboardingProvider[]
}

export interface ProviderCredentialMutationRequest {
  api_key: string
  reason: string
  expected_revision?: ProviderControlRevision
}

export interface ProviderLifecycleMutationRequest {
  reason: string
  expected_revision: ProviderControlRevision
}

export type ProviderOnboardingAction =
  | 'provider.credential.write'
  | 'provider.lifecycle.disable'
  | 'provider.lifecycle.resume'
