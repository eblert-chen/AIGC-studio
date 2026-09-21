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
import {
  DEFAULT_LOGO,
  PRODUCT_BRAND_NAME,
  DEFAULT_WORDMARK,
} from '@/lib/constants'
import {
  resolveSystemBrand,
  usesApprovedSystemBrand,
} from '@/lib/system-brand'
import { cn } from '@/lib/utils'

type SystemBrandLockupProps = {
  systemName?: string | null
  logo?: string | null
  variant?: 'wordmark' | 'symbol' | 'responsive'
  className?: string
  imageClassName?: string
  nameClassName?: string
  attributionClassName?: string
  showName?: boolean
}

export function SystemBrandLockup(props: SystemBrandLockupProps) {
  const variant = props.variant ?? 'wordmark'
  const brand = resolveSystemBrand({
    systemName: props.systemName,
    logo: props.logo,
  })
  const approvedBrand = usesApprovedSystemBrand(brand)
  const showStandaloneName = props.showName !== false

  let image = (
    <img
      src={brand.logo}
      alt={showStandaloneName ? '' : brand.systemName}
      className={cn(
        'block size-8 shrink-0 object-contain',
        props.imageClassName
      )}
    />
  )

  if (approvedBrand && variant === 'wordmark') {
    image = (
      <img
        src={DEFAULT_WORDMARK}
        alt={PRODUCT_BRAND_NAME}
        className={cn(
          'block h-9 w-auto max-w-[11rem] shrink-0 object-contain',
          props.imageClassName
        )}
      />
    )
  } else if (approvedBrand && variant === 'responsive') {
    image = (
      <picture className='block shrink-0'>
        <source media='(max-width: 639px)' srcSet={DEFAULT_LOGO} />
        <img
          src={DEFAULT_WORDMARK}
          alt={PRODUCT_BRAND_NAME}
          className={cn(
            'block h-8 w-auto max-w-[9rem] object-contain',
            props.imageClassName
          )}
        />
      </picture>
    )
  } else if (approvedBrand && variant === 'symbol') {
    image = (
      <img
        src={DEFAULT_LOGO}
        alt={PRODUCT_BRAND_NAME}
        className={cn(
          'block size-8 shrink-0 object-contain',
          props.imageClassName
        )}
      />
    )
  }

  return (
    <span
      data-system-brand={approvedBrand ? 'approved' : 'custom'}
      data-brand-variant={variant}
      className={cn('inline-flex min-w-0 items-center gap-2', props.className)}
    >
      {image}
      {showStandaloneName && (
        <span
          data-brand-attribution={approvedBrand ? 'upstream' : undefined}
          className={cn(
            'min-w-0 truncate',
            approvedBrand
              ? 'text-muted-foreground text-[11px] font-medium tracking-[0.03em]'
              : props.nameClassName,
            approvedBrand && props.attributionClassName
          )}
        >
          {approvedBrand ? (
            <>
              <span className='hidden sm:inline'>Relay · </span>
              {brand.systemName}
            </>
          ) : (
            brand.systemName
          )}
        </span>
      )}
    </span>
  )
}
