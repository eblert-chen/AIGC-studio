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
import { readFile } from 'node:fs/promises'

import { Window } from 'happy-dom'

const domWindow = new Window()
const domGlobals = [
  'window',
  'document',
  'navigator',
  'HTMLElement',
  'HTMLAnchorElement',
  'Node',
  'Element',
  'Event',
  'CustomEvent',
  'MutationObserver',
  'requestAnimationFrame',
  'cancelAnimationFrame',
  'getComputedStyle',
] as const

for (const key of domGlobals) {
  Object.defineProperty(globalThis, key, {
    configurable: true,
    value: domWindow[key],
  })
}

const { act } = await import('react')
const { createRoot } = await import('react-dom/client')
const i18next = (await import('i18next')).default
const { initReactI18next } = await import('react-i18next')
await i18next.use(initReactI18next).init({
  lng: 'zh',
  resources: {
    zh: {
      translation: {
        'Back to Platform': '返回 Platform',
      },
    },
  },
})
const { PlatformReturnLink } = await import('../platform-return-link')
const reactTestGlobals = globalThis as typeof globalThis & {
  IS_REACT_ACT_ENVIRONMENT?: boolean
}
reactTestGlobals.IS_REACT_ACT_ENVIRONMENT = true

describe('PlatformReturnLink', () => {
  after(() => domWindow.close())

  test('renders a same-tab, accessible return button with the exact safe URL', async () => {
    const container = document.createElement('div')
    document.body.append(container)
    const root = createRoot(container)

    await act(async () => {
      root.render(<PlatformReturnLink href='http://127.0.0.1:5173/platform' />)
    })

    const link = container.querySelector<HTMLAnchorElement>(
      '[data-platform-return-link="true"]'
    )
    assert.ok(link)
    assert.equal(link.getAttribute('href'), 'http://127.0.0.1:5173/platform')
    assert.equal(link.getAttribute('target'), null)
    assert.equal(link.getAttribute('aria-label'), '返回 Platform')
    assert.equal(link.textContent?.trim(), '返回 Platform')

    await act(async () => root.unmount())
    container.remove()
  })

  test('keeps search reachable as a compact control in the shared 320px header', async () => {
    const source = await readFile(new URL('../app-header.tsx', import.meta.url), 'utf8')
    const searchSource = await readFile(
      new URL('../../../search.tsx', import.meta.url),
      'utf8'
    )

    assert.match(source, /showSearch\s*&&\s*<Search compactOnNarrow\s*\/>/)
    assert.doesNotMatch(source, /hidden min-\[360px\]:contents/)
    assert.match(searchSource, /compactOnNarrow[\s\S]*h-11 w-11/)
    assert.match(searchSource, /onClick=\{\(\) => setOpen\(true\)\}/)
    assert.match(searchSource, /aria-label=\{resolvedPlaceholder\}/)
  })
})
