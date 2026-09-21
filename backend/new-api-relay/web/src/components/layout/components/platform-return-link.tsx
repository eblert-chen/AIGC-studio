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
import { ArrowLeft } from 'lucide-react'
import type { MouseEventHandler } from 'react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button'
import { useStatus } from '@/hooks/use-status'
import { normalizePlatformConsoleURL } from '@/lib/platform-console-url'
import { cn } from '@/lib/utils'

type PlatformReturnLinkProps = {
  href: string
  compactOnNarrow?: boolean
  className?: string
  onClick?: MouseEventHandler<HTMLAnchorElement>
}

export function PlatformReturnLink({
  href,
  compactOnNarrow = false,
  className,
  onClick,
}: PlatformReturnLinkProps) {
  const { t } = useTranslation()
  const label = t('Back to Platform')

  return (
    <Button
      variant='outline'
      size='sm'
      className={cn(
        'h-8 gap-1.5 rounded-lg px-3 text-xs font-medium',
        compactOnNarrow && 'w-8 px-0 lg:w-auto lg:px-3',
        className
      )}
      render={
        <a
          href={href}
          aria-label={label}
          data-platform-return-link='true'
          onClick={onClick}
        />
      }
    >
      <ArrowLeft data-icon='inline-start' aria-hidden='true' />
      <span className={cn(compactOnNarrow && 'hidden lg:inline')}>{label}</span>
    </Button>
  )
}

export function ConfiguredPlatformReturnLink(
  props: Omit<PlatformReturnLinkProps, 'href'>
) {
  const { status } = useStatus()
  const href =
    normalizePlatformConsoleURL(status?.platform_console_url) ??
    normalizePlatformConsoleURL(import.meta.env.VITE_PLATFORM_CONSOLE_URL)

  if (!href) {
    return null
  }

  return <PlatformReturnLink {...props} href={href} />
}
