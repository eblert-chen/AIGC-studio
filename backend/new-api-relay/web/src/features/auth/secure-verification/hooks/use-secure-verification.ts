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
import i18next from 'i18next'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'sonner'

import {
  extractVerificationInfo,
  isVerificationRequiredError,
} from '@/lib/secure-verification'

import { checkVerificationMethods, isAbortError, verify } from '../api'
import {
  SecureVerificationOperationCoordinator,
  type SecureVerificationOperation,
} from '../operation-coordinator'
import type {
  SecurityProofBinding,
  SecurityProofScope,
  SecureVerificationState,
  StartVerificationOptions,
  UseSecureVerificationOptions,
  VerificationMethod,
  VerificationMethods,
} from '../types'

type ApiCall = (proofToken?: string, signal?: AbortSignal) => Promise<unknown>

type PendingVerification = {
  apiCall: ApiCall
  binding?: SecurityProofBinding
  operation: SecureVerificationOperation
  scope: SecurityProofScope
}

type InternalState = SecureVerificationState

const defaultMethods: VerificationMethods = {
  has2FA: false,
  hasPasskey: false,
  passkeySupported: false,
}

const initialState: InternalState = {
  method: null,
  loading: false,
  code: '',
  title: undefined,
  description: undefined,
}

export function useSecureVerification(
  options: UseSecureVerificationOptions = {}
) {
  const { onSuccess, onError, successMessage, autoReset = true } = options

  const [methods, setMethods] = useState<VerificationMethods>(defaultMethods)
  const [state, setState] = useState<InternalState>(initialState)
  const [open, setOpen] = useState(false)
  const apiCallRef = useRef<PendingVerification | null>(null)
  const mountedRef = useRef(true)
  const operationCoordinatorRef = useRef(
    new SecureVerificationOperationCoordinator()
  )
  const methodChecksRef = useRef(new Set<AbortController>())

  const fetchVerificationMethods = useCallback(async () => {
    const controller = new AbortController()
    methodChecksRef.current.add(controller)
    try {
      const result = await checkVerificationMethods(controller.signal)
      if (mountedRef.current && !controller.signal.aborted) setMethods(result)
      return result
    } finally {
      methodChecksRef.current.delete(controller)
    }
  }, [])

  useEffect(() => {
    const methodChecks = methodChecksRef.current
    const operationCoordinator = operationCoordinatorRef.current
    mountedRef.current = true
    const controller = new AbortController()
    void checkVerificationMethods(controller.signal)
      .then((result) => {
        if (mountedRef.current && !controller.signal.aborted) setMethods(result)
      })
      .catch(() => undefined)
    return () => {
      mountedRef.current = false
      controller.abort()
      for (const pendingController of methodChecks) {
        pendingController.abort()
      }
      methodChecks.clear()
      operationCoordinator.invalidate()
      apiCallRef.current = null
    }
  }, [])

  const reset = useCallback(() => {
    operationCoordinatorRef.current.invalidate()
    apiCallRef.current = null
    if (mountedRef.current) {
      setState(initialState)
      setOpen(false)
    }
  }, [])

  const updateOpen = useCallback(
    (nextOpen: boolean) => {
      if (!nextOpen) {
        reset()
        return
      }
      if (mountedRef.current) setOpen(true)
    },
    [reset]
  )

  const startVerification = useCallback(
    async (apiCall: ApiCall, config: StartVerificationOptions) => {
      const { preferredMethod, scope, binding, title, description } = config
      const operation = operationCoordinatorRef.current.begin()
      apiCallRef.current = null
      let availableMethods: VerificationMethods
      try {
        availableMethods = await checkVerificationMethods(
          operation.controller.signal
        )
      } catch (error) {
        if (
          isAbortError(error) ||
          !operationCoordinatorRef.current.isCurrent(operation)
        ) {
          return false
        }
        throw error
      }
      if (!operationCoordinatorRef.current.isCurrent(operation)) return false
      if (mountedRef.current) setMethods(availableMethods)

      if (!availableMethods.has2FA && !availableMethods.hasPasskey) {
        toast.error(
          i18next.t(
            'Please enable Two-factor Authentication or Passkey before proceeding'
          )
        )
        onError?.(
          new Error(
            'No verification methods available. Enable 2FA or Passkey to continue.'
          )
        )
        operationCoordinatorRef.current.invalidate()
        return false
      }

      let defaultMethod: VerificationMethod | null = preferredMethod ?? null
      if (
        (defaultMethod === 'passkey' &&
          (!availableMethods.hasPasskey ||
            !availableMethods.passkeySupported)) ||
        (defaultMethod === '2fa' && !availableMethods.has2FA)
      ) {
        defaultMethod = null
      }
      if (!defaultMethod) {
        if (availableMethods.hasPasskey && availableMethods.passkeySupported) {
          defaultMethod = 'passkey'
        } else if (availableMethods.has2FA) {
          defaultMethod = '2fa'
        }
      }

      apiCallRef.current = { apiCall, binding, operation, scope }
      setState((prev) => ({
        ...prev,
        method: defaultMethod,
        scope,
        binding,
        title,
        description,
      }))
      setOpen(true)
      return true
    },
    [onError]
  )

  const executeVerification = useCallback(
    async (method?: VerificationMethod, code?: string) => {
      const pending = apiCallRef.current
      if (!pending) {
        toast.error(i18next.t('Verification is not configured properly'))
        return
      }

      const actualMethod = method ?? state.method
      if (!actualMethod) {
        toast.error(i18next.t('Select a verification method first'))
        return
      }

      if (!operationCoordinatorRef.current.isCurrent(pending.operation)) return
      setState((prev) => ({ ...prev, loading: true }))

      try {
        const proof = await verify(
          actualMethod,
          pending.scope,
          code ?? state.code,
          pending.binding,
          pending.operation.controller.signal
        )
        if (!operationCoordinatorRef.current.isCurrent(pending.operation)) {
          proof.proof_token = ''
          return
        }
        let result: unknown
        try {
          result = await pending.apiCall(
            proof.proof_token,
            pending.operation.controller.signal
          )
        } finally {
          proof.proof_token = ''
        }

        if (!operationCoordinatorRef.current.isCurrent(pending.operation)) {
          return result
        }

        if (successMessage) {
          toast.success(successMessage)
        }

        onSuccess?.(result, actualMethod)

        if (autoReset) {
          reset()
        }

        return result
      } catch (error) {
        if (
          isAbortError(error) ||
          !operationCoordinatorRef.current.isCurrent(pending.operation)
        ) {
          return
        }
        const message =
          error instanceof Error
            ? error.message
            : i18next.t('Verification failed')
        toast.error(message)
        onError?.(error)
        throw error
      } finally {
        if (
          mountedRef.current &&
          operationCoordinatorRef.current.isCurrent(pending.operation)
        ) {
          setState((prev) => ({ ...prev, loading: false }))
        }
      }
    },
    [state, successMessage, onSuccess, onError, autoReset, reset]
  )

  const setCode = useCallback((code: string) => {
    setState((prev) => ({ ...prev, code }))
  }, [])

  const switchMethod = useCallback((method: VerificationMethod) => {
    setState((prev) => ({ ...prev, method, code: '' }))
  }, [])

  const cancel = useCallback(() => {
    reset()
  }, [reset])

  const withVerification = useCallback(
    async (apiCall: ApiCall, config: StartVerificationOptions) => {
      try {
        return await apiCall()
      } catch (error) {
        if (isVerificationRequiredError(error)) {
          const info = extractVerificationInfo(error)
          toast.info(info.message)
          await startVerification(apiCall, config)
          return null
        }
        throw error
      }
    },
    [startVerification]
  )

  const canUseMethod = useCallback(
    (method: VerificationMethod) => {
      if (method === '2fa') return methods.has2FA
      if (method === 'passkey') {
        return methods.hasPasskey && methods.passkeySupported
      }
      return false
    },
    [methods]
  )

  const recommendedMethod = useMemo<VerificationMethod | null>(() => {
    if (methods.hasPasskey && methods.passkeySupported) return 'passkey'
    if (methods.has2FA) return '2fa'
    return null
  }, [methods])

  return {
    open,
    setOpen: updateOpen,
    methods,
    state,
    startVerification,
    executeVerification,
    cancel,
    reset,
    setCode,
    switchMethod,
    withVerification,
    fetchVerificationMethods,
    canUseMethod,
    recommendedMethod,
    hasAnyMethod: methods.has2FA || methods.hasPasskey,
    isLoading: state.loading,
    currentMethod: state.method,
    code: state.code,
  }
}
