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
import { Loader2, PauseCircle, PlayCircle } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useForm } from 'react-hook-form'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { Dialog } from '@/components/dialog'
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
import {
  SecureVerificationDialog,
  useSecureVerification,
} from '@/features/auth/secure-verification'

import {
  disableProvider,
  prepareProviderLifecycleMutation,
  PreparedProviderMutationVault,
  providerOnboardingQueryKey,
  resumeProvider,
  type PreparedProviderMutation,
} from '../api'
import {
  providerLifecycleFormSchema,
  type ProviderLifecycleFormValues,
} from '../lib/provider-onboarding'
import type {
  ProviderOnboardingList,
  ProviderOnboardingProvider,
} from '../types'

type ProviderLifecycleDialogProps = {
  open: boolean
  provider: ProviderOnboardingProvider | null
  action: 'disable' | 'resume'
  onOpenChange: (open: boolean) => void
}

export function ProviderLifecycleDialog({
  open,
  provider,
  action,
  onOpenChange,
}: ProviderLifecycleDialogProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const isDisable = action === 'disable'
  const form = useForm<ProviderLifecycleFormValues>({
    resolver: zodResolver(providerLifecycleFormSchema),
    defaultValues: { reason: '' },
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
      form.reset({ reason: '' })
    }
  }, [action, form, open, provider?.id])

  const handleOpenChange = (nextOpen: boolean) => {
    if (isSubmitting || verification.state.loading) return
    if (!nextOpen) {
      mutationVaultRef.current.invalidate()
      setIsPreparingVerification(false)
      form.reset({ reason: '' })
      verification.cancel()
    }
    onOpenChange(nextOpen)
  }

  const handleSubmit = async (values: ProviderLifecycleFormValues) => {
    if (!provider) return
    let prepared: PreparedProviderMutation | null = null
    const attemptEpoch = mutationVaultRef.current.beginAttempt()
    setIsPreparingVerification(true)
    try {
      if (provider.control_revision === null) {
        throw new Error(
          t('Control revision is missing. Refresh and try again.')
        )
      }
      const request = {
        reason: values.reason.trim(),
        expected_revision: provider.control_revision,
      }
      prepared = await prepareProviderLifecycleMutation(
        provider.id,
        action,
        request
      )
      const installedPrepared = prepared
      if (!mutationVaultRef.current.install(attemptEpoch, installedPrepared)) {
        return
      }

      const started = await verification.startVerification(
        async (proofToken, signal) => {
          if (!proofToken) {
            throw new Error(
              t('Security proof is missing. Verify and try again.')
            )
          }
          if (!mutationVaultRef.current.owns(attemptEpoch, installedPrepared)) {
            throw new Error(
              t('Security proof is missing. Verify and try again.')
            )
          }
          setIsSubmitting(true)
          try {
            const updatedProvider = isDisable
              ? await disableProvider(
                  provider.id,
                  installedPrepared,
                  proofToken,
                  signal
                )
              : await resumeProvider(
                  provider.id,
                  installedPrepared,
                  proofToken,
                  signal
                )
            installUpdatedProvider(updatedProvider)
            toast.success(
              isDisable ? t('Provider disabled') : t('Provider resumed')
            )
            onOpenChange(false)
            return updatedProvider
          } finally {
            if (scrubPendingMutation(installedPrepared)) {
              form.reset({ reason: '' })
            }
            if (mutationVaultRef.current.isCurrent(attemptEpoch)) {
              setIsSubmitting(false)
            }
          }
        },
        {
          scope: isDisable
            ? 'provider.lifecycle.disable'
            : 'provider.lifecycle.resume',
          binding: installedPrepared.binding,
          preferredMethod: 'passkey',
          title: isDisable
            ? t('Verify provider disable')
            : t('Verify provider resume'),
          description: t(
            'Confirm your identity before changing provider routing availability.'
          ),
        }
      )
      if (mutationVaultRef.current.owns(attemptEpoch, installedPrepared)) {
        setIsPreparingVerification(false)
      }
      if (!started) scrubPendingMutation(installedPrepared)
    } catch (error) {
      if (!mutationVaultRef.current.isCurrent(attemptEpoch)) {
        if (prepared) scrubPendingMutation(prepared)
        return
      }
      if (prepared) scrubPendingMutation(prepared)
      setIsPreparingVerification(false)
      const fallbackMessage = isDisable
        ? t('Provider disable failed')
        : t('Provider resume failed')
      toast.error(error instanceof Error ? error.message : fallbackMessage)
    }
  }

  const providerName = provider?.display_name || provider?.id || '-'

  return (
    <>
      <Dialog
        open={open}
        onOpenChange={handleOpenChange}
        title={
          <span className='flex items-center gap-2'>
            {isDisable ? (
              <PauseCircle className='text-destructive size-5' />
            ) : (
              <PlayCircle className='text-success size-5' />
            )}
            {isDisable ? t('Disable provider') : t('Resume provider')}
          </span>
        }
        description={
          isDisable
            ? t(
                'Disable {{provider}} and remove it from active routing until an operator resumes it.',
                { provider: providerName }
              )
            : t('Resume {{provider}} and return it to managed routing.', {
                provider: providerName,
              })
        }
        contentClassName='sm:max-w-md'
        showCloseButton={!isSubmitting && !verification.state.loading}
        footer={
          <>
            <Button
              type='button'
              variant='outline'
              disabled={isSubmitting || verification.state.loading}
              onClick={() => handleOpenChange(false)}
            >
              {t('Cancel')}
            </Button>
            <Button
              type='submit'
              form='provider-lifecycle-form'
              variant={isDisable ? 'destructive' : 'default'}
              disabled={
                isSubmitting ||
                isPreparingVerification ||
                verification.state.loading ||
                provider?.control_revision == null
              }
            >
              {isSubmitting && <Loader2 className='size-4 animate-spin' />}
              {isDisable ? t('Confirm disable') : t('Confirm resume')}
            </Button>
          </>
        }
      >
        <Form {...form}>
          <form
            id='provider-lifecycle-form'
            onSubmit={(event) => {
              void form.handleSubmit(handleSubmit)(event)
            }}
            className='py-1'
          >
            <FormField
              control={form.control}
              name='reason'
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t('Change reason')}</FormLabel>
                  <FormControl>
                    <Input
                      {...field}
                      maxLength={500}
                      placeholder={t('Record the operator reason')}
                      autoFocus
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
            {provider?.control_revision == null ? (
              <p className='text-destructive mt-3 text-sm'>
                {t('Control revision is missing. Refresh and try again.')}
              </p>
            ) : null}
          </form>
        </Form>
      </Dialog>

      <SecureVerificationDialog
        open={verification.open}
        onOpenChange={(nextOpen) => {
          if (!nextOpen) {
            mutationVaultRef.current.invalidate()
            form.reset({ reason: '' })
            setIsPreparingVerification(false)
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
            form.reset({ reason: '' })
            setIsPreparingVerification(false)
            verification.cancel()
          }
        }}
        onCancel={() => {
          mutationVaultRef.current.invalidate()
          form.reset({ reason: '' })
          setIsPreparingVerification(false)
          verification.cancel()
        }}
        onCodeChange={verification.setCode}
        onMethodChange={verification.switchMethod}
      />
    </>
  )
}
