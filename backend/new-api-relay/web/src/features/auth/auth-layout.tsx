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
import { Link } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'

import { Skeleton } from '@/components/ui/skeleton'
import { SystemBrandLockup } from '@/components/layout/components/system-brand-lockup'
import { useSystemConfig } from '@/hooks/use-system-config'

type AuthLayoutProps = {
  children: React.ReactNode
}

export function AuthLayout({ children }: AuthLayoutProps) {
  const { t } = useTranslation()
  const { systemName, logo, loading } = useSystemConfig()

  return (
    <div className='relative grid h-svh max-w-none'>
      <Link
        to='/'
        aria-label={t('Go to home')}
        className='absolute top-4 left-4 z-10 flex min-h-11 items-center transition-opacity hover:opacity-80 sm:top-8 sm:left-8'
      >
        {loading ? (
          <Skeleton className='h-11 w-40 rounded-lg' />
        ) : (
          <SystemBrandLockup
            systemName={systemName}
            logo={logo}
            variant='wordmark'
            imageClassName='h-11 max-w-[10rem] sm:h-12 sm:max-w-[11rem]'
            nameClassName='text-lg font-semibold'
          />
        )}
      </Link>
      <div className='container flex items-center pt-16 sm:pt-0'>
        <div className='mx-auto flex w-full flex-col justify-center space-y-2 px-4 py-8 sm:w-[480px] sm:p-8'>
          {children}
        </div>
      </div>
    </div>
  )
}
