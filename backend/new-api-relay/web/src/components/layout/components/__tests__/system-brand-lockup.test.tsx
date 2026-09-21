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
import { afterAll as after, describe, test } from 'bun:test'

import { Window } from 'happy-dom'

const domWindow = new Window()
for (const key of [
  'window',
  'document',
  'navigator',
  'HTMLElement',
  'HTMLImageElement',
  'Node',
  'Element',
] as const) {
  Object.defineProperty(globalThis, key, {
    configurable: true,
    value: domWindow[key],
  })
}

const { act } = await import('react')
const { createRoot } = await import('react-dom/client')
const { SystemBrandLockup } = await import('../system-brand-lockup')
const reactTestGlobals = globalThis as typeof globalThis & {
  IS_REACT_ACT_ENVIRONMENT?: boolean
}
reactTestGlobals.IS_REACT_ACT_ENVIRONMENT = true

describe('SystemBrandLockup', () => {
  after(() => domWindow.close())

  test('renders the approved horizontal asset without cropping for stock settings', async () => {
    const container = document.createElement('div')
    document.body.append(container)
    const root = createRoot(container)

    await act(async () => {
      root.render(
        <SystemBrandLockup
          systemName='New API'
          logo='/logo.png'
          variant='wordmark'
        />
      )
    })

    const lockup = container.querySelector('[data-system-brand="approved"]')
    const image = container.querySelector<HTMLImageElement>('img')
    assert.ok(lockup)
    assert.ok(image)
    assert.equal(image.getAttribute('src'), '/brand/xutian-ai-studio-wordmark.svg')
    assert.equal(image.getAttribute('alt'), '旭天 AI studio')
    assert.equal(image.classList.contains('object-contain'), true)
    assert.equal(container.textContent?.trim(), 'Relay · New API')
    assert.ok(container.querySelector('[data-brand-attribution="upstream"]'))

    await act(async () => root.unmount())
    container.remove()
  })

  test('preserves a custom logo and exposes its custom name as text', async () => {
    const container = document.createElement('div')
    document.body.append(container)
    const root = createRoot(container)

    await act(async () => {
      root.render(
        <SystemBrandLockup
          systemName='Northwind Relay'
          logo='/northwind.svg'
          variant='wordmark'
        />
      )
    })

    const lockup = container.querySelector('[data-system-brand="custom"]')
    const image = container.querySelector<HTMLImageElement>('img')
    assert.ok(lockup)
    assert.ok(image)
    assert.equal(image.getAttribute('src'), '/northwind.svg')
    assert.equal(image.classList.contains('object-contain'), true)
    assert.equal(container.textContent?.trim(), 'Northwind Relay')

    await act(async () => root.unmount())
    container.remove()
  })
})
