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
import type { TFunction } from 'i18next'
import { KeyRound, Pause, Play, RotateCcw } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { StatusBadge, type StatusVariant } from '@/components/status-badge'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'

import type { ProviderOnboardingProvider } from '../types'

type ProviderOnboardingTableProps = {
  providers: ProviderOnboardingProvider[]
  onCredential: (provider: ProviderOnboardingProvider) => void
  onLifecycle: (
    provider: ProviderOnboardingProvider,
    action: 'disable' | 'resume'
  ) => void
}

type StatusMeta = {
  label: string
  variant: StatusVariant
}

const PROVIDER_ORDER = new Map([
  ['google-gemini-api', 0],
  ['minimax', 1],
  ['volcengine-ark', 2],
])

function sortProviders(providers: ProviderOnboardingProvider[]) {
  return [...providers].sort((left, right) => {
    const leftIndex = PROVIDER_ORDER.get(left.id) ?? Number.MAX_SAFE_INTEGER
    const rightIndex = PROVIDER_ORDER.get(right.id) ?? Number.MAX_SAFE_INTEGER
    return leftIndex - rightIndex || left.id.localeCompare(right.id)
  })
}

function providerName(provider: ProviderOnboardingProvider) {
  if (provider.display_name) return provider.display_name
  if (provider.id === 'google-gemini-api') return 'Google Gemini API'
  if (provider.id === 'minimax') return 'MiniMax'
  if (provider.id === 'volcengine-ark') return 'Volcengine Ark'
  return provider.id
}

function channelStatusMeta(value: string, t: TFunction): StatusMeta {
  switch (value.trim().toLowerCase()) {
    case 'enabled':
      return { label: t('Managed channel enabled'), variant: 'success' }
    case 'manually_disabled':
    case 'manual_disabled':
      return { label: t('Managed channel disabled'), variant: 'neutral' }
    case 'auto_disabled':
      return { label: t('Automatically disabled'), variant: 'danger' }
    case 'not_configured':
      return { label: t('Managed channel not created'), variant: 'neutral' }
    default:
      return { label: t('Channel state unavailable'), variant: 'danger' }
  }
}

function lifecycleStatusMeta(value: string, t: TFunction): StatusMeta {
  switch (value.trim().toLowerCase()) {
    case 'not_configured':
      return { label: t('Onboarding not started'), variant: 'neutral' }
    case 'staged':
      return { label: t('Credential staged'), variant: 'info' }
    case 'route_test_ready':
      return { label: t('Managed route-test marker active'), variant: 'info' }
    case 'disabled':
      return { label: t('Route-test channel disabled'), variant: 'neutral' }
    case 'blocked':
      return { label: t('Onboarding blocked'), variant: 'danger' }
    default:
      return { label: t('Lifecycle state unavailable'), variant: 'danger' }
  }
}

function blockerStatusMeta(value: string, t: TFunction): StatusMeta {
  switch (value.trim().toLowerCase()) {
    case 'managed_status_mismatch':
      return {
        label: t('Managed channel status does not match onboarding state'),
        variant: 'danger',
      }
    case 'managed_marker_invalid':
      return {
        label: t('Managed onboarding marker is invalid'),
        variant: 'danger',
      }
    case 'native_ability_enabled':
      return {
        label: t('Native channel ability was enabled outside onboarding'),
        variant: 'danger',
      }
    case 'channel_conflict':
      return {
        label: t('Reserved channel conflicts with existing configuration'),
        variant: 'danger',
      }
    case 'credential_not_configured':
      return { label: t('Configure a credential first'), variant: 'neutral' }
    case 'control_revision_missing':
      return {
        label: t('Refresh to obtain the current control revision'),
        variant: 'warning',
      }
    case 'route_not_materialized':
      return {
        label: t('Materialize the Relay route before changing channel state'),
        variant: 'warning',
      }
    case 'lifecycle_action_unavailable':
      return {
        label: t('No lifecycle action is currently available'),
        variant: 'neutral',
      }
    default:
      return {
        label: t('Onboarding invariant check failed'),
        variant: 'danger',
      }
  }
}

function routeMaterializationStatusMeta(
  provider: ProviderOnboardingProvider,
  t: TFunction
): StatusMeta {
  if (provider.blocker_code) {
    return blockerStatusMeta(provider.blocker_code, t)
  }
  if (provider.route_materialized) {
    return { label: t('Relay route materialized'), variant: 'info' }
  }
  return { label: t('Relay route not materialized'), variant: 'warning' }
}

function routeDeclarationStatusMeta(
  provider: ProviderOnboardingProvider,
  t: TFunction
): StatusMeta {
  if (provider.route_declaration_verified && provider.route_declaration_fresh) {
    return { label: t('Signed route declaration current'), variant: 'info' }
  }
  if (provider.route_declaration_verified) {
    return { label: t('Signed route declaration stale'), variant: 'warning' }
  }
  return {
    label: t('Signed route declaration not recorded'),
    variant: 'neutral',
  }
}

function routeTestStatusMeta(
  provider: ProviderOnboardingProvider,
  t: TFunction
): StatusMeta {
  if (provider.route_test_verified && provider.route_test_fresh) {
    return { label: t('Route test artifact verified'), variant: 'success' }
  }
  if (provider.route_test_verified) {
    return { label: t('Route test receipt stale'), variant: 'warning' }
  }
  return { label: t('Route test receipt not recorded'), variant: 'neutral' }
}

function platformStatusMeta(
  value: string,
  label: string,
  t: TFunction
): StatusMeta {
  switch (value.trim().toLowerCase()) {
    case 'published':
    case 'active':
    case 'configured':
      return { label, variant: 'success' }
    case 'blocked':
    case 'invalid':
      return { label: t('{{label}} blocked', { label }), variant: 'danger' }
    default:
      return {
        label: t('{{label}} not recorded', { label }),
        variant: 'neutral',
      }
  }
}

function formatUpdatedAt(value: string | null, t: TFunction) {
  if (!value) return t('Never')
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat(undefined, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

function Models({ provider }: { provider: ProviderOnboardingProvider }) {
  const { t } = useTranslation()
  const visible = provider.models.slice(0, 3)
  const remaining = provider.models.length - visible.length

  if (provider.models.length === 0) {
    return <span className='text-muted-foreground text-sm'>{t('None')}</span>
  }

  return (
    <div className='flex max-w-72 flex-wrap gap-1'>
      {visible.map((model) => (
        <Badge key={model.id} variant='outline' className='max-w-56'>
          <span className='truncate'>{model.display_name || model.id}</span>
        </Badge>
      ))}
      {remaining > 0 ? <Badge variant='secondary'>+{remaining}</Badge> : null}
    </div>
  )
}

function CredentialEvidence({
  provider,
}: {
  provider: ProviderOnboardingProvider
}) {
  const { t } = useTranslation()
  const prefix = provider.credential.fingerprint_prefix.trim().slice(0, 12)

  return (
    <div className='grid gap-1.5'>
      <StatusBadge
        label={
          provider.credential.configured ? t('Sealed') : t('Not configured')
        }
        variant={provider.credential.configured ? 'success' : 'warning'}
        copyable={false}
      />
      {provider.credential.configured ? (
        <div className='text-muted-foreground flex flex-wrap gap-x-2 gap-y-1 text-xs'>
          {prefix ? <code>sha256:{prefix}</code> : null}
          <span>
            {t('{{count}} keys', {
              count: provider.credential.key_count,
            })}
          </span>
          <span>{formatUpdatedAt(provider.credential.updated_at, t)}</span>
        </div>
      ) : null}
    </div>
  )
}

function ProviderStatuses({
  provider,
}: {
  provider: ProviderOnboardingProvider
}) {
  const { t } = useTranslation()
  const notConfigured = !provider.credential.configured

  if (notConfigured) {
    return (
      <div className='grid gap-1.5'>
        <StatusBadge
          label={t('Onboarding not started')}
          variant='neutral'
          copyable={false}
        />
        <span className='text-muted-foreground text-xs'>
          {t('Configure a credential to begin onboarding.')}
        </span>
      </div>
    )
  }

  const lifecycle = lifecycleStatusMeta(provider.lifecycle_state, t)
  const channel = channelStatusMeta(provider.channel_status, t)
  const routeMaterialization = routeMaterializationStatusMeta(provider, t)
  const routeDeclaration = routeDeclarationStatusMeta(provider, t)
  const routeTest = routeTestStatusMeta(provider, t)
  const publication = platformStatusMeta(
    provider.platform_publication_status,
    t('Platform publication'),
    t
  )
  const price = platformStatusMeta(
    provider.platform_price_status,
    t('Price'),
    t
  )
  const grant = platformStatusMeta(
    provider.platform_grant_status,
    t('Grant'),
    t
  )

  return (
    <div className='grid gap-1.5'>
      <div className='flex flex-wrap items-center gap-1.5'>
        <StatusBadge
          data-provider-status='lifecycle'
          label={lifecycle.label}
          variant={lifecycle.variant}
          copyable={false}
        />
        <StatusBadge
          data-provider-status='channel'
          label={channel.label}
          variant={channel.variant}
          copyable={false}
          type='text'
        />
      </div>
      <div className='flex flex-wrap items-center gap-1.5'>
        <StatusBadge
          data-provider-status='route-materialized'
          label={routeMaterialization.label}
          variant={routeMaterialization.variant}
          copyable={false}
          type='text'
        />
        <StatusBadge
          data-provider-status='route-declaration'
          label={routeDeclaration.label}
          variant={routeDeclaration.variant}
          copyable={false}
          type='text'
        />
        <StatusBadge
          data-provider-status='route-test'
          label={routeTest.label}
          variant={routeTest.variant}
          copyable={false}
          type='text'
        />
      </div>
      {provider.route_declaration_valid_until ? (
        <span className='text-muted-foreground text-xs'>
          {t('Signed route declaration valid until {{time}}', {
            time: formatUpdatedAt(provider.route_declaration_valid_until, t),
          })}
        </span>
      ) : null}
      {provider.route_test_latest_at ? (
        <span className='text-muted-foreground text-xs'>
          {t('Latest verified route test {{time}}', {
            time: formatUpdatedAt(provider.route_test_latest_at, t),
          })}
        </span>
      ) : null}
      <div className='flex flex-wrap items-center gap-1.5'>
        {[
          { key: 'publication', status: publication },
          { key: 'price', status: price },
          { key: 'grant', status: grant },
        ].map(({ key, status }) => (
          <StatusBadge
            data-provider-status={`platform-${key}`}
            key={key}
            label={status.label}
            variant={status.variant}
            copyable={false}
            type='text'
          />
        ))}
      </div>
    </div>
  )
}

function ProviderActions({
  provider,
  onCredential,
  onLifecycle,
}: {
  provider: ProviderOnboardingProvider
  onCredential: ProviderOnboardingTableProps['onCredential']
  onLifecycle: ProviderOnboardingTableProps['onLifecycle']
}) {
  const { t } = useTranslation()
  // These are server-computed capabilities. In particular, an emergency
  // disable must remain reachable when route materialization or other
  // onboarding evidence has drifted.
  const canDisable = provider.can_disable
  const canResume = provider.can_resume

  return (
    <div className='flex flex-wrap justify-end gap-2'>
      <Button
        type='button'
        size='sm'
        variant='outline'
        className='h-11 md:h-7'
        onClick={() => onCredential(provider)}
      >
        {provider.credential.configured ? (
          <RotateCcw className='size-4' />
        ) : (
          <KeyRound className='size-4' />
        )}
        {provider.credential.configured
          ? t('Rotate credential')
          : t('Add credential')}
      </Button>
      {canDisable || canResume ? (
        <Button
          type='button'
          size='sm'
          variant={canResume ? 'outline' : 'ghost'}
          className='h-11 md:h-7'
          onClick={() =>
            onLifecycle(provider, canResume ? 'resume' : 'disable')
          }
        >
          {canResume ? (
            <Play className='size-4' />
          ) : (
            <Pause className='size-4' />
          )}
          {canResume
            ? t('Resume route-test channel')
            : t('Disable route-test channel')}
        </Button>
      ) : null}
      {!canDisable && !canResume && provider.action_blocker_code ? (
        <span className='text-muted-foreground basis-full text-right text-xs'>
          {blockerStatusMeta(provider.action_blocker_code, t).label}
        </span>
      ) : null}
    </div>
  )
}

function MobileProviderRow({
  provider,
  onCredential,
  onLifecycle,
}: {
  provider: ProviderOnboardingProvider
  onCredential: ProviderOnboardingTableProps['onCredential']
  onLifecycle: ProviderOnboardingTableProps['onLifecycle']
}) {
  const { t } = useTranslation()

  return (
    <article className='grid gap-4 px-4 py-5'>
      <div className='flex items-start justify-between gap-3'>
        <div className='min-w-0'>
          <h3 className='font-semibold'>{providerName(provider)}</h3>
          <p className='text-muted-foreground mt-1 text-xs'>
            {provider.account_id || t('Not assigned')} ·{' '}
            {provider.region || t('Global')}
          </p>
        </div>
        {provider.channel_id ? (
          <Badge variant='outline'>#{provider.channel_id}</Badge>
        ) : null}
      </div>

      <div className='grid gap-1'>
        <span className='text-muted-foreground text-xs font-medium'>
          {t('Official endpoint')}
        </span>
        <code className='break-all whitespace-normal'>
          {provider.base_url || '-'}
        </code>
      </div>

      <div className='grid gap-1'>
        <span className='text-muted-foreground text-xs font-medium'>
          {t('Models')}
        </span>
        <Models provider={provider} />
      </div>

      <div className='grid grid-cols-1 gap-4 min-[420px]:grid-cols-2'>
        <div className='grid gap-1'>
          <span className='text-muted-foreground text-xs font-medium'>
            {t('Credential')}
          </span>
          <CredentialEvidence provider={provider} />
        </div>
        <div className='grid gap-1'>
          <span className='text-muted-foreground text-xs font-medium'>
            {t('Managed channel and route test')}
          </span>
          <ProviderStatuses provider={provider} />
        </div>
      </div>

      <ProviderActions
        provider={provider}
        onCredential={onCredential}
        onLifecycle={onLifecycle}
      />
    </article>
  )
}

export function ProviderOnboardingTable({
  providers,
  onCredential,
  onLifecycle,
}: ProviderOnboardingTableProps) {
  const { t } = useTranslation()
  const orderedProviders = sortProviders(providers)

  return (
    <>
      <div className='hidden min-h-0 flex-1 overflow-auto md:block'>
        <Table className='min-w-[1120px]'>
          <TableHeader className='bg-muted/30 sticky top-0 z-10'>
            <TableRow>
              <TableHead>{t('Provider')}</TableHead>
              <TableHead>{t('Official endpoint')}</TableHead>
              <TableHead>{t('Models')}</TableHead>
              <TableHead>{t('Credential')}</TableHead>
              <TableHead>{t('Managed channel and route test')}</TableHead>
              <TableHead className='text-right'>{t('Actions')}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {orderedProviders.map((provider) => (
              <TableRow key={provider.id} className='h-auto'>
                <TableCell className='w-52 py-4 align-top whitespace-normal'>
                  <div className='grid gap-1'>
                    <span className='font-semibold'>
                      {providerName(provider)}
                    </span>
                    <span className='text-muted-foreground text-xs'>
                      {provider.account_id || t('Not assigned')} ·{' '}
                      {provider.region || t('Global')}
                    </span>
                    {provider.channel_id ? (
                      <span className='text-muted-foreground text-xs'>
                        {t('Channel')} #{provider.channel_id}
                      </span>
                    ) : null}
                  </div>
                </TableCell>
                <TableCell className='max-w-72 py-4 align-top whitespace-normal'>
                  <code className='text-xs break-all'>
                    {provider.base_url || '-'}
                  </code>
                </TableCell>
                <TableCell className='py-4 align-top whitespace-normal'>
                  <Models provider={provider} />
                </TableCell>
                <TableCell className='w-64 py-4 align-top whitespace-normal'>
                  <CredentialEvidence provider={provider} />
                </TableCell>
                <TableCell className='w-64 py-4 align-top whitespace-normal'>
                  <ProviderStatuses provider={provider} />
                </TableCell>
                <TableCell className='w-60 py-4 align-top whitespace-normal'>
                  <ProviderActions
                    provider={provider}
                    onCredential={onCredential}
                    onLifecycle={onLifecycle}
                  />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      <div className='divide-border min-h-0 flex-1 divide-y overflow-y-auto md:hidden'>
        {orderedProviders.map((provider) => (
          <MobileProviderRow
            key={provider.id}
            provider={provider}
            onCredential={onCredential}
            onLifecycle={onLifecycle}
          />
        ))}
      </div>
    </>
  )
}
