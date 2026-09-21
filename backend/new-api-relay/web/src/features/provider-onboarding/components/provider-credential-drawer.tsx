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
import { zodResolver } from '@hookform/resolvers/zod'
import { useQueryClient } from '@tanstack/react-query'
import { KeyRound, Loader2, ShieldCheck } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useForm } from 'react-hook-form'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import {
  SideDrawerSection,
  SideDrawerSectionHeader,
  sideDrawerContentClassName,
  sideDrawerFooterClassName,
  sideDrawerFormClassName,
  sideDrawerHeaderClassName,
} from '@/components/drawer-layout'
import { StatusBadge } from '@/components/status-badge'
import { Button } from '@/components/ui/button'
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Sheet,
  SheetClose,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import {
  SecureVerificationDialog,
  useSecureVerification,
} from '@/features/auth/secure-verification'

import {
  prepareProviderCredentialMutation,
  PreparedProviderMutationVault,
  providerOnboardingQueryKey,
  saveProviderCredential,
  type PreparedProviderMutation,
} from '../api'
import {
  providerCredentialFormSchema,
  type ProviderCredentialFormValues,
} from '../lib/provider-onboarding'
import type {
  ProviderOnboardingList,
  ProviderOnboardingProvider,
} from '../types'

type ProviderCredentialDrawerProps = {
  open: boolean
  provider: ProviderOnboardingProvider | null
  onOpenChange: (open: boolean) => void
}

const EMPTY_VALUES: ProviderCredentialFormValues = {
  apiKey: '',
  reason: '',
}

export function ProviderCredentialDrawer({
  open,
  provider,
  onOpenChange,
}: ProviderCredentialDrawerProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const isRotation = provider?.credential.configured === true
  const form = useForm<ProviderCredentialFormValues>({
    resolver: zodResolver(providerCredentialFormSchema),
    defaultValues: EMPTY_VALUES,
  })

  const verification = useSecureVerification()

  const [isPreparingVerification, setIsPreparingVerification] = useState(false)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const mutationVaultRef = useRef(new PreparedProviderMutationVault())

  const scrubPendingMutation = useCallback(
    (expected?: PreparedProviderMutation) => {
      return mutationVaultRef.current.release(expected)
    },
    []
  )

  useEffect(() => {
    const mutationVault = mutationVaultRef.current
    return () => {
      mutationVault.invalidate()
    }
  }, [])

  const installUpdatedProvider = useCallback(
    (updatedProvider: ProviderOnboardingProvider) => {
      queryClient.setQueryData<ProviderOnboardingList>(
        providerOnboardingQueryKey,
        (current) =>
          current
            ? {
                ...current,
                providers: current.providers.map((item) =>
                  item.id === updatedProvider.id ? updatedProvider : item
                ),
              }
            : current
      )
      void queryClient.invalidateQueries({
        queryKey: providerOnboardingQueryKey,
      })
    },
    [queryClient]
  )

  useEffect(() => {
    if (open) {
      mutationVaultRef.current.invalidate()
      form.reset(EMPTY_VALUES)
    }
  }, [form, open, provider?.id])

  const clearAndClose = () => {
    mutationVaultRef.current.invalidate()
    setIsPreparingVerification(false)
    form.reset(EMPTY_VALUES)
    verification.cancel()
    onOpenChange(false)
  }

  const handleOpenChange = (nextOpen: boolean) => {
    if (!nextOpen && !isSubmitting && !verification.state.loading) {
      clearAndClose()
    }
  }

  const handleSubmit = async (values: ProviderCredentialFormValues) => {
    if (!provider) return

    let prepared: PreparedProviderMutation | null = null
    const attemptEpoch = mutationVaultRef.current.beginAttempt()
    setIsPreparingVerification(true)
    try {
      prepared = await prepareProviderCredentialMutation(provider.id, {
        api_key: values.apiKey,
        reason: values.reason.trim(),
        expected_revision: provider.control_revision ?? undefined,
      })
      const installedPrepared = prepared
      if (!mutationVaultRef.current.install(attemptEpoch, installedPrepared)) {
        return
      }
      form.reset({ apiKey: '', reason: values.reason })

      const started = await verification.startVerification(
        async (proofToken, signal) => {
          if (!proofToken) {
            throw new Error(
              t('Security proof is missing. Verify and try again.')
            )
          }
          if (!mutationVaultRef.current.owns(attemptEpoch, installedPrepared)) {
            throw new Error(t('Credential input expired. Paste it again.'))
          }
          setIsSubmitting(true)
          try {
            const updatedProvider = await saveProviderCredential(
              provider.id,
              installedPrepared,
              proofToken,
              signal
            )
            installUpdatedProvider(updatedProvider)
            toast.success(
              isRotation
                ? t('Provider credential rotated')
                : t('Provider credential saved')
            )
            onOpenChange(false)
            return updatedProvider
          } finally {
            if (scrubPendingMutation(installedPrepared)) {
              form.reset(EMPTY_VALUES)
            }
            if (mutationVaultRef.current.isCurrent(attemptEpoch)) {
              setIsSubmitting(false)
            }
          }
        },
        {
          scope: 'provider.credential.write',
          binding: installedPrepared.binding,
          preferredMethod: 'passkey',
          title: t('Verify provider credential change'),
          description: t(
            'Confirm your identity before replacing the sealed provider credential.'
          ),
        }
      )
      if (mutationVaultRef.current.owns(attemptEpoch, installedPrepared)) {
        setIsPreparingVerification(false)
      }
      if (!started) {
        if (scrubPendingMutation(installedPrepared)) form.reset(EMPTY_VALUES)
      }
    } catch (error) {
      if (!mutationVaultRef.current.isCurrent(attemptEpoch)) {
        if (prepared) scrubPendingMutation(prepared)
        return
      }
      if (!prepared || scrubPendingMutation(prepared)) form.reset(EMPTY_VALUES)
      setIsPreparingVerification(false)
      toast.error(
        error instanceof Error
          ? error.message
          : t('Provider credential update failed')
      )
    }
  }

  const displayName = provider?.display_name || provider?.id || '-'
  const fingerprintPrefix = provider?.credential.fingerprint_prefix
    .trim()
    .slice(0, 12)

  return (
    <>
      <Sheet open={open} onOpenChange={handleOpenChange}>
        <SheetContent
          className={sideDrawerContentClassName(
            'sm:max-w-lg [&_[data-slot=sheet-close]]:size-11 sm:[&_[data-slot=sheet-close]]:size-7'
          )}
        >
          <SheetHeader className={sideDrawerHeaderClassName()}>
            <SheetTitle>
              {isRotation
                ? t('Rotate provider credential')
                : t('Add provider credential')}
            </SheetTitle>
            <SheetDescription>
              {t(
                'The key is sent once for sealing. It is never returned, displayed, or copied from this console.'
              )}
            </SheetDescription>
          </SheetHeader>

          <Form {...form}>
            <form
              id='provider-credential-form'
              onSubmit={(event) => {
                void form.handleSubmit(handleSubmit)(event)
              }}
              className={sideDrawerFormClassName()}
              autoComplete='off'
            >
              <SideDrawerSection>
                <SideDrawerSectionHeader
                  icon={<ShieldCheck className='size-4' />}
                  iconTone='info'
                  title={t('Credential target')}
                  description={t(
                    'Provider identity, account, and region are controlled by the onboarding contract.'
                  )}
                />

                <div className='grid gap-4 sm:grid-cols-2'>
                  <div className='grid gap-2'>
                    <Label htmlFor='provider-credential-provider'>
                      {t('Provider')}
                    </Label>
                    <Input
                      id='provider-credential-provider'
                      className='h-11 sm:h-8'
                      value={displayName}
                      readOnly
                    />
                  </div>
                  <div className='grid gap-2'>
                    <Label htmlFor='provider-credential-account'>
                      {t('Account ID')}
                    </Label>
                    <Input
                      id='provider-credential-account'
                      className='h-11 sm:h-8'
                      value={provider?.account_id || t('Not assigned')}
                      readOnly
                    />
                  </div>
                  <div className='grid gap-2 sm:col-span-2'>
                    <Label htmlFor='provider-credential-region'>
                      {t('Region')}
                    </Label>
                    <Input
                      id='provider-credential-region'
                      className='h-11 sm:h-8'
                      value={provider?.region || t('Global')}
                      readOnly
                    />
                  </div>
                </div>
              </SideDrawerSection>

              <SideDrawerSection>
                <SideDrawerSectionHeader
                  icon={<KeyRound className='size-4' />}
                  iconTone='warning'
                  title={t('Write-only credential')}
                  description={t(
                    'Paste a new provider key and record why this change is required.'
                  )}
                />

                {provider?.credential.configured ? (
                  <div className='border-border/60 flex flex-wrap items-center justify-between gap-2 border-y py-3'>
                    <span className='text-muted-foreground text-sm'>
                      {t('Current credential')}
                    </span>
                    <div className='flex flex-wrap items-center justify-end gap-2'>
                      <StatusBadge
                        label={t('Sealed')}
                        variant='success'
                        copyable={false}
                      />
                      {fingerprintPrefix ? (
                        <code className='text-muted-foreground text-xs'>
                          sha256:{fingerprintPrefix}
                        </code>
                      ) : null}
                    </div>
                  </div>
                ) : null}

                <FormField
                  control={form.control}
                  name='apiKey'
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>{t('Provider API key')}</FormLabel>
                      <FormControl>
                        <Input
                          {...field}
                          className='h-11 sm:h-8'
                          type='password'
                          autoComplete='new-password'
                          spellCheck={false}
                          data-1p-ignore='true'
                          data-lpignore='true'
                          data-form-type='other'
                          onCopy={(event) => event.preventDefault()}
                          onCut={(event) => event.preventDefault()}
                          placeholder={t('Paste a new key')}
                        />
                      </FormControl>
                      <FormDescription>
                        {t(
                          'This field is cleared when the drawer closes or the save succeeds.'
                        )}
                      </FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                <FormField
                  control={form.control}
                  name='reason'
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>{t('Change reason')}</FormLabel>
                      <FormControl>
                        <Input
                          {...field}
                          className='h-11 sm:h-8'
                          maxLength={500}
                          placeholder={t('Record the operator reason')}
                        />
                      </FormControl>
                      <FormDescription>
                        {t(
                          'An irreversible digest of the reason is retained in the operator audit trail; the text itself is not stored.'
                        )}
                      </FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </SideDrawerSection>
            </form>
          </Form>

          <SheetFooter className={sideDrawerFooterClassName()}>
            <SheetClose
              render={
                <Button
                  type='button'
                  variant='outline'
                  className='h-11 sm:h-8'
                  disabled={isSubmitting || verification.state.loading}
                />
              }
            >
              {t('Cancel')}
            </SheetClose>
            <Button
              type='submit'
              form='provider-credential-form'
              className='h-11 sm:h-8'
              disabled={
                isPreparingVerification ||
                isSubmitting ||
                verification.state.loading
              }
            >
              {(isPreparingVerification ||
                isSubmitting ||
                verification.state.loading) && (
                <Loader2 className='size-4 animate-spin' />
              )}
              {isRotation ? t('Rotate credential') : t('Save credential')}
            </Button>
          </SheetFooter>
        </SheetContent>
      </Sheet>

      <SecureVerificationDialog
        open={verification.open}
        onOpenChange={(nextOpen) => {
          if (!nextOpen) {
            mutationVaultRef.current.invalidate()
            form.reset(EMPTY_VALUES)
            verification.cancel()
          }
        }}
        methods={verification.methods}
        state={verification.state}
        onVerify={async (method, code) => {
          try {
            await verification.executeVerification(method, code)
          } catch {
            mutationVaultRef.current.invalidate()
            form.reset(EMPTY_VALUES)
            setIsPreparingVerification(false)
            verification.cancel()
          }
        }}
        onCancel={() => {
          mutationVaultRef.current.invalidate()
          form.reset(EMPTY_VALUES)
          setIsPreparingVerification(false)
          verification.cancel()
        }}
        onCodeChange={verification.setCode}
        onMethodChange={verification.switchMethod}
      />
    </>
  )
}
