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
import { afterAll as after, describe, test } from 'bun:test'
import assert from 'node:assert/strict'

import { Window } from 'happy-dom'

import type { ProviderOnboardingProvider } from '../types'
import { parseProviderOnboardingList } from '../lib/provider-onboarding'

const domWindow = new Window()
for (const key of [
  'window',
  'document',
  'navigator',
  'HTMLElement',
  'HTMLButtonElement',
  'SVGElement',
  'Node',
  'Element',
  'Event',
  'CustomEvent',
  'MutationObserver',
  'ResizeObserver',
  'requestAnimationFrame',
  'cancelAnimationFrame',
  'getComputedStyle',
] as const) {
  Object.defineProperty(globalThis, key, {
    configurable: true,
    value: domWindow[key],
  })
}

const { act } = await import('react')
const { createRoot } = await import('react-dom/client')
const { createInstance } = await import('i18next')
const { I18nextProvider, initReactI18next } = await import('react-i18next')
const { ProviderOnboardingTable } = await import('./provider-onboarding-table')

const i18n = createInstance()
await i18n.use(initReactI18next).init({
  lng: 'en',
  resources: { en: { translation: {} } },
  returnEmptyString: false,
})

const reactTestGlobals = globalThis as typeof globalThis & {
  IS_REACT_ACT_ENVIRONMENT?: boolean
}
reactTestGlobals.IS_REACT_ACT_ENVIRONMENT = true

const baseProvider: ProviderOnboardingProvider = {
  id: 'google-gemini-api',
  display_name: 'Google Gemini API',
  region: 'global',
  channel_id: '990001',
  account_id: 'primary',
  base_url: 'https://generativelanguage.googleapis.com',
  models: [{ id: 'veo', display_name: 'Veo' }],
  credential: {
    configured: true,
    fingerprint_prefix: '123456789abc',
    key_count: 1,
    updated_at: null,
  },
  lifecycle_state: 'route_test_ready',
  channel_status: 'enabled',
  control_revision: `sha256:${'1'.repeat(64)}`,
  route_test_ready: true,
  route_materialized: true,
  route_declaration_verified: true,
  route_declaration_fresh: true,
  route_declaration_valid_until: '2026-09-03T00:00:00Z',
  route_test_verified: false,
  route_test_fresh: false,
  route_test_latest_at: null,
  acceptance_verified: false,
  acceptance_fresh: false,
  acceptance_valid_until: null,
  platform_publication_status: 'not_recorded',
  platform_price_status: 'not_recorded',
  platform_grant_status: 'not_recorded',
  can_disable: true,
  can_resume: false,
  action_blocker_code: null,
  blocker_code: null,
}

describe('ProviderOnboardingTable evidence and actions', () => {
  after(() => domWindow.close())

  test('renders the normalized minimal provider without throwing', async () => {
    const normalized = parseProviderOnboardingList({
      success: true,
      data: {
        providers: [
          {
            id: 'google-gemini-api',
            credential: { configured: false, key_count: 0 },
          },
        ],
      },
    }).providers[0]
    const container = document.createElement('div')
    document.body.append(container)
    const root = createRoot(container)

    try {
      await act(async () => {
        root.render(
          <I18nextProvider i18n={i18n}>
            <ProviderOnboardingTable
              providers={[normalized]}
              onCredential={() => {}}
              onLifecycle={() => {}}
            />
          </I18nextProvider>
        )
      })

      assert.equal(container.textContent?.includes('Not configured'), true)
      assert.equal(container.textContent?.includes('Onboarding not started'), true)
      assert.equal(
        container.textContent?.includes('Configure a credential to begin onboarding.'),
        true
      )
      const addCredentialButtons = [
        ...container.querySelectorAll<HTMLButtonElement>('button'),
      ].filter((button) => button.textContent?.trim() === 'Add credential')
      assert.equal(addCredentialButtons.length, 2)
      assert.equal(
        addCredentialButtons.every((button) =>
          button.classList.contains('h-11')
        ),
        true
      )
    } finally {
      await act(async () => root.unmount())
      container.remove()
    }
  })

  test('keeps marker and declaration distinct from route-test and Platform evidence', async () => {
    const container = document.createElement('div')
    document.body.append(container)
    const root = createRoot(container)
    const renderProvider = async (provider: ProviderOnboardingProvider) => {
      await act(async () => {
        root.render(
          <I18nextProvider i18n={i18n}>
            <ProviderOnboardingTable
              providers={[provider]}
              onCredential={() => {}}
              onLifecycle={() => {}}
            />
          </I18nextProvider>
        )
      })
    }

    try {
      await renderProvider(baseProvider)
      for (const lifecycle of container.querySelectorAll<HTMLElement>(
        '[data-provider-status="lifecycle"]'
      )) {
        assert.equal(
          lifecycle.textContent?.trim(),
          'Managed route-test marker active'
        )
        assert.equal(lifecycle.classList.contains('text-info'), true)
        assert.equal(lifecycle.classList.contains('text-success'), false)
      }
      for (const routeDeclaration of container.querySelectorAll<HTMLElement>(
        '[data-provider-status="route-declaration"]'
      )) {
        assert.equal(
          routeDeclaration.textContent?.trim(),
          'Signed route declaration current'
        )
        assert.equal(routeDeclaration.classList.contains('text-info'), true)
        assert.equal(routeDeclaration.classList.contains('text-success'), false)
      }
      for (const routeTest of container.querySelectorAll<HTMLElement>(
        '[data-provider-status="route-test"]'
      )) {
        assert.equal(
          routeTest.textContent?.trim(),
          'Route test receipt not recorded'
        )
        assert.equal(routeTest.classList.contains('text-success'), false)
        assert.equal(
          routeTest.classList.contains('text-muted-foreground'),
          true
        )
      }
      for (const key of ['publication', 'price', 'grant']) {
        for (const status of container.querySelectorAll<HTMLElement>(
          `[data-provider-status="platform-${key}"]`
        )) {
          assert.equal(status.classList.contains('text-success'), false)
          assert.equal(status.classList.contains('text-muted-foreground'), true)
        }
      }

      await renderProvider({
        ...baseProvider,
        route_test_verified: true,
        route_test_fresh: true,
        route_test_latest_at: '2026-09-02T12:00:00Z',
        acceptance_verified: true,
        acceptance_fresh: true,
      })
      for (const lifecycle of container.querySelectorAll<HTMLElement>(
        '[data-provider-status="lifecycle"]'
      )) {
        assert.equal(lifecycle.classList.contains('text-info'), true)
        assert.equal(lifecycle.classList.contains('text-success'), false)
      }
      for (const routeDeclaration of container.querySelectorAll<HTMLElement>(
        '[data-provider-status="route-declaration"]'
      )) {
        assert.equal(
          routeDeclaration.textContent?.trim(),
          'Signed route declaration current'
        )
        assert.equal(routeDeclaration.classList.contains('text-info'), true)
        assert.equal(routeDeclaration.classList.contains('text-success'), false)
      }
      for (const routeTest of container.querySelectorAll<HTMLElement>(
        '[data-provider-status="route-test"]'
      )) {
        assert.equal(
          routeTest.textContent?.trim(),
          'Route test artifact verified'
        )
        assert.equal(routeTest.classList.contains('text-success'), true)
      }
    } finally {
      await act(async () => root.unmount())
      container.remove()
    }
  })

  test('renders lifecycle controls only when the server permits the action', async () => {
    const container = document.createElement('div')
    document.body.append(container)
    const root = createRoot(container)
    const actions: string[] = []
    const renderProvider = async (provider: ProviderOnboardingProvider) => {
      await act(async () => {
        root.render(
          <I18nextProvider i18n={i18n}>
            <ProviderOnboardingTable
              providers={[provider]}
              onCredential={() => {}}
              onLifecycle={(_provider, action) => actions.push(action)}
            />
          </I18nextProvider>
        )
      })
    }
    const buttonsByText = (text: string) =>
      [...container.querySelectorAll<HTMLButtonElement>('button')].filter(
        (button) => button.textContent?.trim() === text
      )
    const clickButton = async (text: string) => {
      const button = buttonsByText(text).at(0)
      assert.ok(button, `Expected button "${text}"`)
      await act(async () => button.click())
    }

    try {
      await renderProvider(baseProvider)
      assert.equal(buttonsByText('Disable route-test channel').length, 2)
      assert.equal(buttonsByText('Resume route-test channel').length, 0)
      await clickButton('Disable route-test channel')
      assert.deepEqual(actions, ['disable'])

      await renderProvider({
        ...baseProvider,
        route_materialized: false,
        blocker_code: 'route_not_materialized',
        action_blocker_code: 'route_not_materialized',
        can_disable: true,
        can_resume: false,
      })
      assert.equal(buttonsByText('Disable route-test channel').length, 2)
      assert.equal(buttonsByText('Resume route-test channel').length, 0)
      await clickButton('Disable route-test channel')
      assert.deepEqual(actions, ['disable', 'disable'])

      await renderProvider({
        ...baseProvider,
        can_disable: false,
        can_resume: true,
        lifecycle_state: 'disabled',
        channel_status: 'manually_disabled',
      })
      assert.equal(buttonsByText('Disable route-test channel').length, 0)
      assert.equal(buttonsByText('Resume route-test channel').length, 2)
      await clickButton('Resume route-test channel')
      assert.deepEqual(actions, ['disable', 'disable', 'resume'])

      await renderProvider({
        ...baseProvider,
        can_disable: false,
        can_resume: false,
        action_blocker_code: 'route_not_materialized',
      })
      assert.equal(buttonsByText('Disable route-test channel').length, 0)
      assert.equal(buttonsByText('Resume route-test channel').length, 0)
      assert.equal(
        container.textContent?.includes(
          'Materialize the Relay route before changing channel state'
        ),
        true
      )
    } finally {
      await act(async () => root.unmount())
      container.remove()
    }
  })
})
