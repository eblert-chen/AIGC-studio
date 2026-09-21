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

import { AxiosHeaders, type AxiosResponse } from 'axios'

import { api } from '@/lib/api'

import {
  executeSensitiveProviderMutation,
  prepareProviderCredentialMutation,
  PreparedProviderMutationVault,
  SafeProviderOnboardingError,
  saveProviderCredential,
} from './api'
import { parseProviderOnboardingList } from './lib/provider-onboarding'

const provider = {
  id: 'google-gemini-api',
  display_name: 'Google Gemini API',
  region: '',
  channel_id: '990001',
  account_id: 'primary',
  base_url: 'https://generativelanguage.googleapis.com',
  models: [],
  credential: {
    configured: true,
    fingerprint_prefix: '123456789abc',
    key_count: 1,
    updated_at: null,
  },
  lifecycle_state: 'staged',
  channel_status: 'manually_disabled',
  control_revision: `sha256:${'1'.repeat(64)}`,
  route_test_ready: false,
  route_materialized: false,
  route_declaration_verified: false,
  route_declaration_fresh: false,
  route_declaration_valid_until: null,
  route_test_verified: false,
  route_test_fresh: false,
  route_test_latest_at: null,
  acceptance_verified: false,
  acceptance_fresh: false,
  acceptance_valid_until: null,
  platform_publication_status: 'not_recorded',
  platform_price_status: 'not_recorded',
  platform_grant_status: 'not_recorded',
  can_disable: false,
  can_resume: false,
  action_blocker_code: 'route_not_materialized',
  blocker_code: null,
}

function response(config: Record<string, unknown>): AxiosResponse {
  return {
    data: { success: true, data: provider },
    status: 200,
    statusText: 'OK',
    headers: {},
    config,
  } as unknown as AxiosResponse
}

describe('provider onboarding response contract', () => {
  test('normalizes the exact unconfigured credential shape emitted by Go omitempty', () => {
    const result = parseProviderOnboardingList({
      success: true,
      data: {
        environment: 'development',
        providers: [
          {
            id: 'google-gemini-api',
            credential: { configured: false, key_count: 0 },
          },
        ],
      },
    })

    assert.deepEqual(result.providers[0].credential, {
      configured: false,
      fingerprint_prefix: '',
      key_count: 0,
      updated_at: null,
    })
  })

  test('normalizes every omitted optional render field without weakening its type', () => {
    const result = parseProviderOnboardingList({
      success: true,
      data: {
        providers: [{ id: 'google-gemini-api' }],
      },
    })

    assert.equal(result.environment, 'unknown')
    assert.equal(result.providers.length, 1)
    assert.deepEqual(result.providers[0], {
      id: 'google-gemini-api',
      display_name: '',
      region: '',
      channel_id: null,
      account_id: '',
      base_url: '',
      models: [],
      credential: {
        configured: false,
        fingerprint_prefix: '',
        key_count: 0,
        updated_at: null,
      },
      lifecycle_state: 'unknown',
      channel_status: 'unknown',
      control_revision: null,
      route_test_ready: false,
      route_materialized: false,
      route_declaration_verified: false,
      route_declaration_fresh: false,
      route_declaration_valid_until: null,
      route_test_verified: false,
      route_test_fresh: false,
      route_test_latest_at: null,
      acceptance_verified: false,
      acceptance_fresh: false,
      acceptance_valid_until: null,
      platform_publication_status: 'not_recorded',
      platform_price_status: 'not_recorded',
      platform_grant_status: 'not_recorded',
      can_disable: false,
      can_resume: false,
      action_blocker_code: null,
      blocker_code: null,
    })

    assert.equal(result.providers[0].credential.fingerprint_prefix.trim(), '')
    assert.equal(result.providers[0].channel_status.trim(), 'unknown')
  })

  for (const [field, invalidProvider] of [
    ['display_name', { id: 'google-gemini-api', display_name: 42 }],
    ['models', { id: 'google-gemini-api', models: 'veo-3.1' }],
    [
      'credential',
      { id: 'google-gemini-api', credential: { fingerprint_prefix: 42 } },
    ],
    [
      'route_test_verified',
      { id: 'google-gemini-api', route_test_verified: 'true' },
    ],
  ] as const) {
    test(`rejects an invalid ${field} type instead of silently defaulting it`, () => {
      assert.throws(() =>
        parseProviderOnboardingList({
          success: true,
          data: { providers: [invalidProvider] },
        })
      )
    })
  }
})

type TestTransportConfig = {
  data?: unknown
  headers: Record<string, unknown> | AxiosHeaders
  authRetry?: boolean
}

type TestTransportError = {
  cause?: unknown
  code?: string
  config: TestTransportConfig
  message?: string
  request?: unknown
  response?: {
    status: number
    data?: { code: string; message: string }
    config?: TestTransportConfig
    headers?: unknown
    request?: unknown
  }
  stack?: string
}

describe('provider onboarding sensitive transport', () => {
  test('scrubs the retained credential on 2FA or Passkey failure, close, retry, and unmount', async () => {
    for (const method of ['2FA', 'Passkey'] as const) {
      const vault = new PreparedProviderMutationVault()
      const prepared = await prepareProviderCredentialMutation(provider.id, {
        api_key: `provider-canary-${method.toLowerCase()}-failure`,
        reason: `${method} failure`,
      })
      const epoch = vault.beginAttempt()
      assert.equal(vault.install(epoch, prepared), true)
      assert.equal(vault.owns(epoch, prepared), true)

      // This is the synchronous operation used by the drawer's verification
      // failure, close/cancel, and unmount paths.
      vault.invalidate()

      assert.equal(prepared.body, '')
      assert.equal(vault.owns(epoch, prepared), false)
    }

    const vault = new PreparedProviderMutationVault()
    const first = await prepareProviderCredentialMutation(provider.id, {
      api_key: 'provider-canary-first-attempt',
      reason: 'first attempt',
    })
    const firstEpoch = vault.beginAttempt()
    assert.equal(vault.install(firstEpoch, first), true)

    const secondEpoch = vault.beginAttempt()
    assert.equal(first.body, '')
    const second = await prepareProviderCredentialMutation(provider.id, {
      api_key: 'provider-canary-second-attempt',
      reason: 'second attempt',
    })
    assert.equal(vault.install(secondEpoch, second), true)

    const lateFirst = await prepareProviderCredentialMutation(provider.id, {
      api_key: 'provider-canary-late-first-attempt',
      reason: 'late first completion',
    })
    assert.equal(vault.install(firstEpoch, lateFirst), false)
    assert.equal(lateFirst.body, '')
    assert.equal(vault.owns(secondEpoch, second), true)

    vault.invalidate()
    assert.equal(second.body, '')
  })

  test('binds the exact request and scrubs body and proof after success', async () => {
    const secret = 'provider-canary-success-42'
    const proof = 'proof-canary-success-42'
    const prepared = await prepareProviderCredentialMutation(provider.id, {
      api_key: secret,
      reason: 'rotate reviewed primary provider credential',
      expected_revision: provider.control_revision,
    })
    assert.match(prepared.binding.body_sha256, /^sha256:[0-9a-f]{64}$/)
    assert.equal(prepared.binding.expected_revision, provider.control_revision)

    let retainedConfig: TestTransportConfig = { headers: new AxiosHeaders() }
    const result = await executeSensitiveProviderMutation(
      prepared.binding.http_path,
      prepared,
      proof,
      async (_path, body, config) => {
        assert.match(body, new RegExp(secret))
        const retainedHeaders = new AxiosHeaders()
        retainedHeaders.set('X-Security-Proof', proof)
        retainedConfig = {
          ...(config as TestTransportConfig),
          headers: retainedHeaders,
        }
        retainedConfig.authRetry = true
        retainedConfig.data = body
        return response(retainedConfig)
      }
    )

    assert.equal(result.id, provider.id)
    assert.equal(prepared.body, '')
    assert.equal(retainedConfig?.data, '')
    assert.equal(
      retainedConfig.headers instanceof AxiosHeaders
        ? retainedConfig.headers.has('X-Security-Proof')
        : retainedConfig.headers['X-Security-Proof'] !== undefined,
      false
    )
    assert.doesNotMatch(
      JSON.stringify(retainedConfig),
      new RegExp(`${secret}|${proof}`)
    )
  })

  for (const status of [400, 401, 500]) {
    test(`returns a safe error and scrubs Axios artifacts after ${status}`, async () => {
      const secret = `provider-canary-error-${status}`
      const proof = `proof-canary-error-${status}`
      const prepared = await prepareProviderCredentialMutation(provider.id, {
        api_key: secret,
        reason: 'rotate reviewed primary provider credential',
      })
      const transportError: TestTransportError = {
        config: {
          data: prepared.body,
          headers: { 'X-Security-Proof': proof },
        },
        request: { body: prepared.body },
        response: {
          status,
          data: {
            code: 'PROVIDER_ONBOARDING_TEST_FAILURE',
            message: 'Safe operator message',
          },
          config: {
            data: prepared.body,
            headers: new AxiosHeaders({ 'X-Security-Proof': proof }),
          },
          request: { body: prepared.body },
        },
      }

      await assert.rejects(
        executeSensitiveProviderMutation(
          prepared.binding.http_path,
          prepared,
          proof,
          async () => {
            throw transportError
          }
        ),
        (error: unknown) => {
          assert.ok(error instanceof SafeProviderOnboardingError)
          assert.equal(error.message, 'Provider onboarding operation failed')
          assert.equal('config' in error, false)
          assert.doesNotMatch(
            JSON.stringify(error),
            new RegExp(`${secret}|${proof}`)
          )
          return true
        }
      )
      assert.equal(prepared.body, '')
      assert.equal(transportError.config.data, '')
      assert.equal(transportError.config.headers['X-Security-Proof'], undefined)
      assert.equal(transportError.request, undefined)
      assert.equal(transportError.response?.config?.data, '')
      assert.equal(
        transportError.response?.config?.headers instanceof AxiosHeaders
          ? transportError.response.config.headers.has('X-Security-Proof')
          : true,
        false
      )
      assert.equal(transportError.response?.request, undefined)
      assert.equal(transportError.response?.data, undefined)
      assert.equal(transportError.response?.headers, undefined)
    })
  }

  test('does not refresh or replay a 401 sensitive mutation through the real interceptor', async () => {
    const secret = 'provider-canary-interceptor-401'
    const proof = 'proof-canary-interceptor-401'
    const prepared = await prepareProviderCredentialMutation(provider.id, {
      api_key: secret,
      reason: 'rotate reviewed primary provider credential',
    })
    const originalAdapter = api.defaults.adapter
    let postCount = 0
    let retainedConfig: TestTransportConfig | null = null
    let retainedError: TestTransportError | null = null

    api.defaults.adapter = async (config) => {
      postCount += 1
      retainedConfig = config as unknown as TestTransportConfig
      retainedError = {
        config: retainedConfig,
        message: `upstream echoed ${secret}`,
        stack: `Error: upstream echoed ${secret}`,
        request: { body: config.data },
        response: {
          status: 401,
          data: {
            code: `MALICIOUS_${secret}`,
            message: `denied ${secret}`,
          },
          config: retainedConfig,
          headers: { 'x-upstream-debug': secret },
          request: { body: config.data },
        },
      }
      throw retainedError
    }

    try {
      await assert.rejects(
        saveProviderCredential(provider.id, prepared, proof),
        (error: unknown) => {
          assert.ok(error instanceof SafeProviderOnboardingError)
          assert.equal(error.code, 'PROVIDER_ONBOARDING_REQUEST_FAILED')
          assert.equal(error.message, 'Provider onboarding operation failed')
          const exposed = [
            error.message,
            error.code,
            JSON.stringify(error),
            JSON.stringify(Object.getOwnPropertyDescriptors(error)),
          ].join(' ')
          assert.doesNotMatch(exposed, new RegExp(`${secret}|${proof}`))
          return true
        }
      )
    } finally {
      api.defaults.adapter = originalAdapter
    }

    assert.equal(postCount, 1)
    assert.equal(prepared.body, '')
    const scrubbedConfig = retainedConfig as TestTransportConfig | null
    const scrubbedError = retainedError as TestTransportError | null
    assert.equal(scrubbedConfig?.data, '')
    assert.equal(scrubbedConfig?.authRetry, undefined)
    assert.equal(scrubbedError?.request, undefined)
    assert.equal(scrubbedError?.response?.request, undefined)
    assert.equal(scrubbedError?.response?.data, undefined)
    assert.equal(scrubbedError?.response?.headers, undefined)
    assert.equal(scrubbedError?.message, undefined)
    assert.equal(scrubbedError?.code, undefined)
    assert.equal(scrubbedError?.stack, undefined)
    assert.doesNotMatch(
      JSON.stringify({ scrubbedConfig, scrubbedError }),
      new RegExp(`${secret}|${proof}`)
    )
  })

  test('rejects a secret-bearing response code without copying it into Error', async () => {
    const secret = 'provider-canary-malicious-code'
    const proof = 'proof-canary-malicious-code'
    const prepared = await prepareProviderCredentialMutation(provider.id, {
      api_key: secret,
      reason: 'rotate reviewed primary provider credential',
    })
    const transportError: TestTransportError = {
      config: {
        data: prepared.body,
        headers: { 'X-Security-Proof': proof },
      },
      message: secret,
      stack: `Error: ${secret}`,
      response: {
        status: 400,
        data: { code: secret, message: secret },
      },
    }

    await assert.rejects(
      executeSensitiveProviderMutation(
        prepared.binding.http_path,
        prepared,
        proof,
        async () => {
          throw transportError
        }
      ),
      (error: unknown) => {
        assert.ok(error instanceof SafeProviderOnboardingError)
        assert.equal(error.code, 'PROVIDER_ONBOARDING_REQUEST_FAILED')
        const exposed = [
          error.message,
          error.code,
          JSON.stringify(error),
          JSON.stringify(Object.getOwnPropertyDescriptors(error)),
        ].join(' ')
        assert.doesNotMatch(exposed, new RegExp(`${secret}|${proof}`))
        return true
      }
    )

    assert.equal(transportError.response?.data, undefined)
    assert.equal(transportError.message, undefined)
    assert.equal(transportError.code, undefined)
    assert.equal(transportError.stack, undefined)
  })

  test('scrubs the same artifacts after cancellation', async () => {
    const secret = 'provider-canary-cancel-42'
    const proof = 'proof-canary-cancel-42'
    const prepared = await prepareProviderCredentialMutation(provider.id, {
      api_key: secret,
      reason: 'rotate reviewed primary provider credential',
    })
    const transportError: TestTransportError = {
      code: 'ERR_CANCELED',
      config: {
        data: prepared.body,
        headers: { 'X-Security-Proof': proof },
      },
      request: { body: prepared.body },
    }
    await assert.rejects(
      executeSensitiveProviderMutation(
        prepared.binding.http_path,
        prepared,
        proof,
        async () => {
          throw transportError
        }
      ),
      SafeProviderOnboardingError
    )
    assert.equal(prepared.body, '')
    assert.equal(transportError.config.data, '')
    assert.equal(transportError.request, undefined)
  })
})
