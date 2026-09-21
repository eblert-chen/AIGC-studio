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
import z from 'zod'

import type {
  ProviderOnboardingList,
  ProviderOnboardingProvider,
} from '../types'

const nullableIdentifierSchema = z
  .union([z.string(), z.number().transform(String)])
  .nullable()
  .optional()
  .transform((value) => value ?? null)

const controlRevisionSchema = z
  .union([z.string(), z.number()])
  .nullable()
  .optional()
  .transform((value) => value ?? null)

const providerModelSchema = z.object({
  id: z.string(),
  display_name: z.string().optional().default(''),
})

const credentialSummarySchema = z
  .object({
    configured: z.boolean().optional().default(false),
    fingerprint_prefix: z.string().optional().default(''),
    key_count: z.number().int().nonnegative().optional().default(0),
    updated_at: z.string().nullable().optional().default(null),
  })
  .optional()
  .default({
    configured: false,
    fingerprint_prefix: '',
    key_count: 0,
    updated_at: null,
  })

/**
 * Whitelists the response fields. Zod strips unknown properties, so even an
 * accidental backend credential field can never enter the page's read model.
 */
export const providerOnboardingProviderSchema = z.object({
  id: z.string().min(1),
  display_name: z.string().optional().default(''),
  region: z.string().optional().default(''),
  channel_id: nullableIdentifierSchema,
  account_id: z.string().optional().default(''),
  base_url: z.string().optional().default(''),
  models: z.array(providerModelSchema).optional().default([]),
  credential: credentialSummarySchema,
  lifecycle_state: z.string().optional().default('unknown'),
  channel_status: z.string().optional().default('unknown'),
  control_revision: controlRevisionSchema,
  route_test_ready: z.boolean().optional().default(false),
  route_materialized: z.boolean().optional().default(false),
  route_declaration_verified: z.boolean().optional().default(false),
  route_declaration_fresh: z.boolean().optional().default(false),
  route_declaration_valid_until: z.string().nullable().optional().default(null),
  route_test_verified: z.boolean().optional().default(false),
  route_test_fresh: z.boolean().optional().default(false),
  route_test_latest_at: z.string().nullable().optional().default(null),
  acceptance_verified: z.boolean().optional().default(false),
  acceptance_fresh: z.boolean().optional().default(false),
  acceptance_valid_until: z.string().nullable().optional().default(null),
  platform_publication_status: z
    .string()
    .optional()
    .default('not_recorded'),
  platform_price_status: z.string().optional().default('not_recorded'),
  platform_grant_status: z.string().optional().default('not_recorded'),
  can_disable: z.boolean().optional().default(false),
  can_resume: z.boolean().optional().default(false),
  action_blocker_code: z.string().nullable().optional().default(null),
  blocker_code: z.string().nullable().optional().default(null),
})

const responseEnvelopeSchema = z.object({
  success: z.boolean(),
  message: z.string().optional(),
  data: z.object({
    environment: z.string().optional().default('unknown'),
    providers: z.array(providerOnboardingProviderSchema).optional().default([]),
  }),
})

const providerMutationEnvelopeSchema = z.object({
  success: z.boolean(),
  message: z.string().optional(),
  data: z.union([
    providerOnboardingProviderSchema,
    z
      .object({ provider: providerOnboardingProviderSchema })
      .transform((value) => value.provider),
  ]),
})

function responseError(message?: string) {
  return new Error(message || 'Provider onboarding request failed')
}

export function parseProviderOnboardingList(
  value: unknown
): ProviderOnboardingList {
  const response = responseEnvelopeSchema.parse(value)
  if (!response.success) throw responseError(response.message)
  return response.data as ProviderOnboardingList
}

export function parseProviderOnboardingMutation(
  value: unknown
): ProviderOnboardingProvider {
  const response = providerMutationEnvelopeSchema.parse(value)
  if (!response.success) throw responseError(response.message)
  return response.data as ProviderOnboardingProvider
}

export const providerCredentialFormSchema = z.object({
  apiKey: z
    .string()
    .min(1, 'API key is required')
    .refine((value) => value.trim().length > 0, 'API key is required'),
  reason: z
    .string()
    .trim()
    .min(8, 'Enter a reason with 8 to 500 characters')
    .max(500, 'Enter a reason with 8 to 500 characters')
    .refine((value) => !/[\r\n]/.test(value), 'Reason must be a single line'),
})

export type ProviderCredentialFormValues = z.infer<
  typeof providerCredentialFormSchema
>

export const providerLifecycleFormSchema = z.object({
  reason: z
    .string()
    .trim()
    .min(8, 'Enter a reason with 8 to 500 characters')
    .max(500, 'Enter a reason with 8 to 500 characters')
    .refine((value) => !/[\r\n]/.test(value), 'Reason must be a single line'),
})

export type ProviderLifecycleFormValues = z.infer<
  typeof providerLifecycleFormSchema
>

export function isProviderDisabled(provider: ProviderOnboardingProvider) {
  const value = provider.channel_status.trim().toLowerCase()
  return [
    'disabled',
    'inactive',
    'manual_disabled',
    'manually_disabled',
    'auto_disabled',
  ].includes(value)
}
