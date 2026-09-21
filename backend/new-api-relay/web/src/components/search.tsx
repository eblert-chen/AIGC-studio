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
import { SearchIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { useSearch } from '@/context/search-provider'
import { cn } from '@/lib/utils'

import { Button } from './ui/button'

type SearchProps = {
  className?: string
  compactOnNarrow?: boolean
  type?: React.HTMLInputTypeAttribute
  placeholder?: string
}

export function Search({
  className = '',
  compactOnNarrow = false,
  placeholder,
}: SearchProps) {
  const { t } = useTranslation()
  const { setOpen } = useSearch()
  const resolvedPlaceholder = placeholder ?? t('Search')
  return (
    <Button
      variant='outline'
      className={cn(
        'bg-muted/25 group text-muted-foreground hover:bg-accent relative rounded-md text-sm font-normal shadow-none',
        compactOnNarrow
          ? 'h-11 w-11 flex-none justify-center p-0 min-[360px]:h-8 min-[360px]:w-full min-[360px]:flex-1 min-[360px]:justify-start min-[360px]:px-4 sm:w-40 sm:pe-12 md:flex-none lg:w-52 xl:w-64'
          : 'h-8 w-full flex-1 justify-start sm:w-40 sm:pe-12 md:flex-none lg:w-52 xl:w-64',
        className
      )}
      onClick={() => setOpen(true)}
      aria-label={resolvedPlaceholder}
    >
      <SearchIcon
        aria-hidden='true'
        className={cn(
          compactOnNarrow
            ? 'static translate-y-0 min-[360px]:absolute min-[360px]:start-1.5 min-[360px]:top-1/2 min-[360px]:-translate-y-1/2'
            : 'absolute start-1.5 top-1/2 -translate-y-1/2'
        )}
        size={16}
      />
      <span
        className={cn(
          'ms-4',
          compactOnNarrow && 'hidden min-[360px]:inline'
        )}
      >
        {resolvedPlaceholder}
      </span>
      <kbd className='bg-muted group-hover:bg-accent pointer-events-none absolute end-[0.3rem] top-[0.3rem] hidden h-5 items-center gap-1 rounded border px-1.5 font-mono text-[10px] font-medium opacity-100 select-none sm:flex'>
        <span className='text-xs'>⌘</span>
        {t('K')}
      </kbd>
    </Button>
  )
}
