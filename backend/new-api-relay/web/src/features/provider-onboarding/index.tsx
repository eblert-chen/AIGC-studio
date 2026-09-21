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
import { useQuery } from '@tanstack/react-query'
import { AlertCircle, Loader2, RefreshCw, ShieldCheck } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { SectionPageLayout } from '@/components/layout'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'

import { getProviderOnboarding, providerOnboardingQueryKey } from './api'
import { ProviderCredentialDrawer } from './components/provider-credential-drawer'
import { ProviderLifecycleDialog } from './components/provider-lifecycle-dialog'
import { ProviderOnboardingTable } from './components/provider-onboarding-table'
import type { ProviderOnboardingProvider } from './types'

type LifecycleDialogState = {
  provider: ProviderOnboardingProvider
  action: 'disable' | 'resume'
} | null

export function ProviderOnboarding() {
  const { t } = useTranslation()
  const [credentialProvider, setCredentialProvider] =
    useState<ProviderOnboardingProvider | null>(null)
  const [lifecycleDialog, setLifecycleDialog] =
    useState<LifecycleDialogState>(null)
  const query = useQuery({
    queryKey: providerOnboardingQueryKey,
    queryFn: getProviderOnboarding,
    retry: false,
  })
  const providers = query.data?.providers ?? []

  let content
  if (query.isLoading) {
    content = (
      <div className='grid min-h-56 flex-1 place-items-center'>
        <div className='text-muted-foreground flex items-center gap-2 text-sm'>
          <Loader2 className='size-4 animate-spin' />
          {t('Loading provider onboarding data')}
        </div>
      </div>
    )
  } else if (query.isError) {
    content = (
      <div className='grid min-h-56 flex-1 place-items-center px-6 text-center'>
        <div className='max-w-md'>
          <AlertCircle className='text-destructive mx-auto size-6' />
          <p className='mt-3 text-sm font-medium'>
            {t('Provider onboarding data could not be loaded')}
          </p>
          <p className='text-muted-foreground mt-1 text-sm'>
            {query.error instanceof Error
              ? query.error.message
              : t('Refresh to try again.')}
          </p>
          <Button
            type='button'
            variant='outline'
            size='sm'
            className='mt-4'
            onClick={() => void query.refetch()}
          >
            <RefreshCw className='size-4' />
            {t('Try again')}
          </Button>
        </div>
      </div>
    )
  } else if (providers.length === 0) {
    content = (
      <div className='grid min-h-56 flex-1 place-items-center px-6 text-center'>
        <div className='max-w-md'>
          <p className='text-sm font-medium'>
            {t('No providers are available for onboarding')}
          </p>
          <p className='text-muted-foreground mt-1 text-sm'>
            {t(
              'The server has not published a provider onboarding contract for this environment.'
            )}
          </p>
        </div>
      </div>
    )
  } else {
    content = (
      <ProviderOnboardingTable
        providers={providers}
        onCredential={setCredentialProvider}
        onLifecycle={(provider, action) =>
          setLifecycleDialog({ provider, action })
        }
      />
    )
  }

  return (
    <>
      <SectionPageLayout fixedContent>
        <SectionPageLayout.Title>
          <span className='flex min-w-0 items-center gap-2'>
            <span className='truncate'>{t('Provider onboarding')}</span>
            {query.data?.environment ? (
              <Badge variant='outline' className='shrink-0 font-normal'>
                {query.data.environment}
              </Badge>
            ) : null}
          </span>
        </SectionPageLayout.Title>
        <SectionPageLayout.Actions>
          <Button
            type='button'
            variant='outline'
            size='sm'
            disabled={query.isFetching}
            onClick={() => void query.refetch()}
          >
            <RefreshCw
              className={query.isFetching ? 'size-4 animate-spin' : 'size-4'}
            />
            {t('Refresh')}
          </Button>
        </SectionPageLayout.Actions>
        <SectionPageLayout.Content>
          <section className='bg-background border-border/70 flex h-full min-h-0 flex-col overflow-hidden rounded-xl border'>
            <div className='border-border/70 flex shrink-0 items-start gap-3 border-b px-4 py-3 sm:px-5'>
              <ShieldCheck className='text-primary mt-0.5 size-4 shrink-0' />
              <div className='min-w-0'>
                <p className='text-sm font-medium'>
                  {t('Sealed provider credential control')}
                </p>
                <p className='text-muted-foreground mt-0.5 text-xs leading-5'>
                  {t(
                    'Only super administrators can write or rotate credentials. Stored keys are never returned to the browser.'
                  )}
                </p>
              </div>
            </div>

            {content}
          </section>
        </SectionPageLayout.Content>
      </SectionPageLayout>

      <ProviderCredentialDrawer
        open={credentialProvider !== null}
        provider={credentialProvider}
        onOpenChange={(open) => {
          if (!open) setCredentialProvider(null)
        }}
      />

      <ProviderLifecycleDialog
        open={lifecycleDialog !== null}
        provider={lifecycleDialog?.provider ?? null}
        action={lifecycleDialog?.action ?? 'disable'}
        onOpenChange={(open) => {
          if (!open) setLifecycleDialog(null)
        }}
      />
    </>
  )
}
