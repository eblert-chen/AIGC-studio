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

const domWindow = new Window()
for (const key of [
  'window',
  'document',
  'navigator',
  'HTMLElement',
  'Node',
  'Element',
  'Event',
  'CustomEvent',
  'MutationObserver',
  'requestAnimationFrame',
  'cancelAnimationFrame',
  'getComputedStyle',
] as const) {
  Object.defineProperty(globalThis, key, {
    configurable: true,
    value: domWindow[key],
  })
}

const { act, useEffect } = await import('react')
const { createRoot } = await import('react-dom/client')
const i18next = (await import('i18next')).default
await i18next.init({ lng: 'en', resources: { en: { translation: {} } } })
const { api } = await import('@/lib/api')
const { useSecureVerification } = await import('./use-secure-verification')

const reactTestGlobals = globalThis as typeof globalThis & {
  IS_REACT_ACT_ENVIRONMENT?: boolean
}
reactTestGlobals.IS_REACT_ACT_ENVIRONMENT = true

type HookResult = ReturnType<typeof useSecureVerification>
type PendingMethodRequest = {
  signal?: AbortSignal
  resolve: (response: { data: unknown }) => void
  url: string
}

type HookSink = { current: HookResult | null }

function Harness({ onUpdate }: { onUpdate: (hook: HookResult) => void }) {
  const hook = useSecureVerification()
  useEffect(() => {
    onUpdate(hook)
  }, [hook, onUpdate])
  return <output data-open={String(hook.open)}>{hook.state.description}</output>
}

function readHook(sink: HookSink): HookResult {
  const hook = sink.current
  assert.ok(hook)
  return hook
}

function responseFor(url: string) {
  if (url === '/api/user/2fa/status') {
    return { data: { success: true, data: { enabled: true } } }
  }
  if (url === '/api/user/passkey') {
    return { data: { success: true, data: { configured: false } } }
  }
  throw new Error(`unexpected secure verification request: ${url}`)
}

describe('useSecureVerification async lifecycle', () => {
  after(() => domWindow.close())

  test('cancel, reopen, and unmount abort and fence late method discovery', async () => {
    const apiClient = api as unknown as {
      get: (
        url: string,
        config?: { signal?: AbortSignal }
      ) => Promise<{ data: unknown }>
    }
    const originalGet = apiClient.get
    const pending: PendingMethodRequest[] = []
    let deferMethodDiscovery = false
    apiClient.get = (url: string, config?: { signal?: AbortSignal }) => {
      if (!deferMethodDiscovery) return Promise.resolve(responseFor(url))
      return new Promise((resolve) => {
        pending.push({ url, signal: config?.signal, resolve })
      })
    }

    const container = document.createElement('div')
    document.body.append(container)
    const root = createRoot(container)
    const sink: HookSink = { current: null }
    const updateHook = (hook: HookResult) => {
      sink.current = hook
    }
    try {
      await act(async () => {
        root.render(<Harness onUpdate={updateHook} />)
        await Promise.resolve()
        await Promise.resolve()
      })
      readHook(sink)

      deferMethodDiscovery = true
      const canceledStart = readHook(sink).startVerification(
        async () => 'must not run',
        {
          scope: 'provider.credential.write',
          description: 'canceled attempt',
        }
      )
      await act(async () => {
        await Promise.resolve()
      })
      const canceledRequests = pending.splice(0, 2)
      assert.equal(canceledRequests.length, 2)
      await act(async () => readHook(sink).cancel())
      assert.equal(
        canceledRequests.every((request) => request.signal?.aborted === true),
        true
      )
      for (const request of canceledRequests) {
        request.resolve(responseFor(request.url))
      }
      let canceledStarted = true
      await act(async () => {
        canceledStarted = await canceledStart
      })
      assert.equal(canceledStarted, false)
      assert.equal(readHook(sink).open, false)

      const firstStart = readHook(sink).startVerification(async () => 'first', {
        scope: 'provider.lifecycle.disable',
        description: 'first attempt',
      })
      await act(async () => {
        await Promise.resolve()
      })
      const firstRequests = pending.splice(0, 2)
      assert.equal(firstRequests.length, 2)

      const secondStart = readHook(sink).startVerification(
        async () => 'second',
        {
          scope: 'provider.lifecycle.resume',
          description: 'second attempt',
        }
      )
      await act(async () => {
        await Promise.resolve()
      })
      const secondRequests = pending.splice(0, 2)
      assert.equal(secondRequests.length, 2)
      assert.equal(
        firstRequests.every((request) => request.signal?.aborted === true),
        true
      )

      for (const request of secondRequests) {
        request.resolve(responseFor(request.url))
      }
      let secondStarted = false
      await act(async () => {
        secondStarted = await secondStart
      })
      assert.equal(secondStarted, true)
      assert.equal(readHook(sink).open, true)
      assert.equal(readHook(sink).state.description, 'second attempt')

      for (const request of firstRequests) {
        request.resolve(responseFor(request.url))
      }
      let firstStarted = true
      await act(async () => {
        firstStarted = await firstStart
      })
      assert.equal(firstStarted, false)
      assert.equal(readHook(sink).open, true)
      assert.equal(readHook(sink).state.description, 'second attempt')

      await act(async () => readHook(sink).cancel())
      const unmountedStart = readHook(sink).startVerification(
        async () => 'late',
        {
          scope: 'provider.credential.write',
          description: 'unmounted attempt',
        }
      )
      await act(async () => {
        await Promise.resolve()
      })
      const unmountedRequests = pending.splice(0, 2)
      assert.equal(unmountedRequests.length, 2)
      await act(async () => root.unmount())
      assert.equal(
        unmountedRequests.every((request) => request.signal?.aborted === true),
        true
      )
      for (const request of unmountedRequests) {
        request.resolve(responseFor(request.url))
      }
      let unmountedStarted = true
      await act(async () => {
        unmountedStarted = await unmountedStart
      })
      assert.equal(unmountedStarted, false)
    } finally {
      apiClient.get = originalGet
      if (container.isConnected) {
        try {
          await act(async () => root.unmount())
        } catch {
          // The root was already unmounted in the success path.
        }
        container.remove()
      }
    }
  })
})
