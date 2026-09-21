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
import type { AxiosResponse } from 'axios'

import type { SecurityProofBinding } from '@/features/auth/secure-verification'
import { api, type ApiRequestConfig } from '@/lib/api'

import {
  parseProviderOnboardingList,
  parseProviderOnboardingMutation,
} from './lib/provider-onboarding'
import type {
  ProviderCredentialMutationRequest,
  ProviderControlRevision,
  ProviderLifecycleMutationRequest,
  ProviderOnboardingList,
  ProviderOnboardingProvider,
  ProviderOnboardingProviderId,
  ProviderOnboardingAction,
} from './types'

export const providerOnboardingQueryKey = ['provider-onboarding'] as const

function requestConfig(): ApiRequestConfig {
  return {
    skipBusinessError: true,
    skipErrorHandler: true,
  }
}

function secureRequestConfig(proofToken: string): ApiRequestConfig {
  return {
    ...requestConfig(),
    // A bound security proof is one-shot. Retrying this request after a 401
    // would replay both the write-only credential body and the consumed proof.
    skipAuthRefresh: true,
    headers: {
      'Content-Type': 'application/json',
      'X-Security-Proof': proofToken,
    },
  }
}

export type PreparedProviderMutation = {
  body: string
  binding: SecurityProofBinding
}

export function scrubPreparedProviderMutation(
  prepared: PreparedProviderMutation | null
) {
  if (prepared) prepared.body = ''
}

/**
 * Keeps the write-only body out of React state and gives every drawer attempt
 * a single owner. Invalidating an attempt synchronously scrubs the retained
 * body before any UI close, retry, or async completion can continue.
 */
export class PreparedProviderMutationVault {
  private epoch = 0
  private pending: PreparedProviderMutation | null = null

  beginAttempt(): number {
    this.invalidate()
    return this.epoch
  }

  install(epoch: number, prepared: PreparedProviderMutation): boolean {
    if (!this.isCurrent(epoch)) {
      scrubPreparedProviderMutation(prepared)
      return false
    }
    this.release()
    this.pending = prepared
    return true
  }

  isCurrent(epoch: number): boolean {
    return this.epoch === epoch
  }

  owns(epoch: number, prepared: PreparedProviderMutation): boolean {
    return this.isCurrent(epoch) && this.pending === prepared
  }

  release(expected?: PreparedProviderMutation): boolean {
    if (expected) scrubPreparedProviderMutation(expected)
    if (expected && this.pending !== expected) return false
    scrubPreparedProviderMutation(this.pending)
    this.pending = null
    return true
  }

  invalidate(): void {
    this.release()
    this.epoch += 1
  }
}

export class SafeProviderOnboardingError extends Error {
  readonly code: string
  readonly status: number | null

  constructor(
    message: string,
    code = 'PROVIDER_ONBOARDING_REQUEST_FAILED',
    status: number | null = null
  ) {
    super(message)
    this.name = 'SafeProviderOnboardingError'
    this.code = code
    this.status = status
  }
}

const SAFE_PROVIDER_ONBOARDING_ERROR_CODES = new Set([
  'PROVIDER_ONBOARDING_REQUEST_FAILED',
  'PROVIDER_ONBOARDING_REQUEST_INVALID',
  'PROVIDER_ONBOARDING_PROVIDER_NOT_FOUND',
  'PROVIDER_ONBOARDING_ENVIRONMENT_NOT_READY',
  'PROVIDER_ONBOARDING_REVISION_REQUIRED',
  'PROVIDER_ONBOARDING_REVISION_CONFLICT',
  'PROVIDER_ONBOARDING_ACTIVE_TASKS',
  'PROVIDER_ONBOARDING_ROUTE_NOT_READY',
  'PROVIDER_ONBOARDING_CHANNEL_CONFLICT',
  'PROVIDER_ONBOARDING_MANAGED_CHANNEL',
  'PROVIDER_ONBOARDING_NOT_CONFIGURED',
  'PROVIDER_ONBOARDING_INTERNAL_ERROR',
  'SECURITY_PROOF_REQUIRED',
  'SECURITY_PROOF_INVALID',
  'SECURITY_PROOF_EXPIRED',
  'SECURITY_PROOF_CONSUMED',
  'SECURITY_PROOF_REQUEST_MISMATCH',
  'SECURITY_PROOF_SCOPE_MISMATCH',
  'SECURITY_PROOF_METHOD_MISMATCH',
])

function providerActionSuffix(action: ProviderOnboardingAction) {
  switch (action) {
    case 'provider.credential.write':
      return 'credential'
    case 'provider.lifecycle.disable':
      return 'disable'
    case 'provider.lifecycle.resume':
      return 'resume'
  }
}

async function sha256(value: string): Promise<`sha256:${string}`> {
  if (typeof crypto === 'undefined' || !crypto.subtle) {
    throw new SafeProviderOnboardingError(
      'Secure request binding is not available in this browser',
      'PROVIDER_ONBOARDING_CRYPTO_UNAVAILABLE'
    )
  }
  const digest = await crypto.subtle.digest(
    'SHA-256',
    new TextEncoder().encode(value)
  )
  const hex = Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, '0')
  ).join('')
  return `sha256:${hex}`
}

async function prepareProviderMutation(
  providerId: ProviderOnboardingProviderId,
  action: ProviderOnboardingAction,
  body: string,
  expectedRevision?: ProviderControlRevision
): Promise<PreparedProviderMutation> {
  const suffix = providerActionSuffix(action)
  return {
    body,
    binding: {
      action,
      provider: providerId,
      http_method: 'POST',
      http_path: `/api/provider-onboarding/${providerId}/${suffix}`,
      body_sha256: await sha256(body),
      ...(expectedRevision == null
        ? {}
        : { expected_revision: String(expectedRevision) }),
    },
  }
}

export function prepareProviderCredentialMutation(
  providerId: ProviderOnboardingProviderId,
  request: ProviderCredentialMutationRequest
) {
  const normalized = {
    api_key: request.api_key,
    reason: request.reason,
    ...(request.expected_revision == null
      ? {}
      : { expected_revision: String(request.expected_revision) }),
  }
  return prepareProviderMutation(
    providerId,
    'provider.credential.write',
    JSON.stringify(normalized),
    request.expected_revision
  )
}

export function prepareProviderLifecycleMutation(
  providerId: ProviderOnboardingProviderId,
  action: 'disable' | 'resume',
  request: ProviderLifecycleMutationRequest
) {
  const normalized = {
    reason: request.reason,
    expected_revision: String(request.expected_revision),
  }
  return prepareProviderMutation(
    providerId,
    action === 'disable'
      ? 'provider.lifecycle.disable'
      : 'provider.lifecycle.resume',
    JSON.stringify(normalized),
    request.expected_revision
  )
}

type ScrubbableHeaders = Record<string, unknown> & {
  delete?: (header: string | string[]) => boolean
}

type ScrubbableTransport = {
  cause?: unknown
  code?: unknown
  config?: { data?: unknown; headers?: ScrubbableHeaders }
  message?: unknown
  request?: unknown
  response?: {
    config?: { data?: unknown; headers?: ScrubbableHeaders }
    data?: unknown
    headers?: unknown
    request?: unknown
  }
  stack?: unknown
}

function scrubTransportConfig(config: ScrubbableTransport['config']) {
  if (!config) return
  config.data = ''
  const headers = config.headers
  if (!headers) return
  try {
    headers.delete?.('X-Security-Proof')
  } catch {
    // Continue with the enumerable fallback used by plain adapter objects.
  }
  for (const key of Object.keys(headers)) {
    if (key.toLowerCase() === 'x-security-proof') delete headers[key]
  }
}

function clearTransportRequest(candidate: { request?: unknown }) {
  try {
    candidate.request = undefined
  } catch {
    // Some adapters expose a readonly request handle. It is never rethrown.
  }
}

function scrubTransportArtifacts(value: unknown) {
  if (!value || typeof value !== 'object') return
  const candidate = value as ScrubbableTransport
  scrubTransportConfig(candidate.config)
  clearTransportRequest(candidate)
  if (candidate.response) {
    scrubTransportConfig(candidate.response.config)
    clearTransportRequest(candidate.response)
    try {
      candidate.response.data = undefined
      candidate.response.headers = undefined
    } catch {
      // The sanitized error returned below never retains the transport object.
    }
  }
  for (const field of ['cause', 'code', 'message', 'stack'] as const) {
    try {
      candidate[field] = undefined
    } catch {
      // Axios adapters can expose readonly diagnostics. They are not rethrown.
    }
  }
}

function safeMutationError(error: unknown): SafeProviderOnboardingError {
  const candidate = error as {
    response?: { status?: number; data?: { code?: string; message?: string } }
  }
  const status = Number.isInteger(candidate?.response?.status)
    ? (candidate.response?.status as number)
    : null
  const responseCode = candidate?.response?.data?.code
  const code = SAFE_PROVIDER_ONBOARDING_ERROR_CODES.has(responseCode ?? '')
    ? (responseCode as string)
    : 'PROVIDER_ONBOARDING_REQUEST_FAILED'
  // Provider and proxy messages are untrusted and may echo request material.
  // Keep only the bounded code/status fields and expose a fixed safe message.
  const message = 'Provider onboarding operation failed'
  scrubTransportArtifacts(error)
  return new SafeProviderOnboardingError(message, code, status)
}

type SensitiveProviderRequest = (
  path: string,
  body: string,
  config: ApiRequestConfig
) => Promise<AxiosResponse>

export async function executeSensitiveProviderMutation(
  path: string,
  prepared: PreparedProviderMutation,
  proofToken: string,
  request: SensitiveProviderRequest = (requestPath, body, config) =>
    api.post(requestPath, body, config),
  signal?: AbortSignal
): Promise<ProviderOnboardingProvider> {
  let response: AxiosResponse | undefined
  try {
    const currentResponse = await request(path, prepared.body, {
      ...secureRequestConfig(proofToken),
      signal,
    })
    response = currentResponse
    return parseProviderOnboardingMutation(currentResponse.data)
  } catch (error) {
    throw safeMutationError(error)
  } finally {
    scrubTransportArtifacts(response)
    scrubPreparedProviderMutation(prepared)
    proofToken = ''
  }
}

export async function getProviderOnboarding(): Promise<ProviderOnboardingList> {
  const response = await api.get('/api/provider-onboarding', requestConfig())
  return parseProviderOnboardingList(response.data)
}

export async function saveProviderCredential(
  providerId: ProviderOnboardingProviderId,
  prepared: PreparedProviderMutation,
  proofToken: string,
  signal?: AbortSignal
): Promise<ProviderOnboardingProvider> {
  return executeSensitiveProviderMutation(
    `/api/provider-onboarding/${encodeURIComponent(providerId)}/credential`,
    prepared,
    proofToken,
    undefined,
    signal
  )
}

async function mutateProviderLifecycle(
  providerId: ProviderOnboardingProviderId,
  action: 'disable' | 'resume',
  prepared: PreparedProviderMutation,
  proofToken: string,
  signal?: AbortSignal
): Promise<ProviderOnboardingProvider> {
  return executeSensitiveProviderMutation(
    `/api/provider-onboarding/${encodeURIComponent(providerId)}/${action}`,
    prepared,
    proofToken,
    undefined,
    signal
  )
}

export function disableProvider(
  providerId: ProviderOnboardingProviderId,
  prepared: PreparedProviderMutation,
  proofToken: string,
  signal?: AbortSignal
) {
  return mutateProviderLifecycle(
    providerId,
    'disable',
    prepared,
    proofToken,
    signal
  )
}

export function resumeProvider(
  providerId: ProviderOnboardingProviderId,
  prepared: PreparedProviderMutation,
  proofToken: string,
  signal?: AbortSignal
) {
  return mutateProviderLifecycle(
    providerId,
    'resume',
    prepared,
    proofToken,
    signal
  )
}
