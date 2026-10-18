import { createContext, useContext, useLayoutEffect } from 'react'
import type { CloseSafetyRequest } from '../../shared/contracts'

export type PrepareClose = (reason: 'window-close' | 'profile-switch', request?: CloseSafetyRequest) => Promise<boolean>
export const CloseSafetyContext = createContext<((handler: PrepareClose) => () => void) | null>(null)

export function useCloseSafety(handler: PrepareClose) {
  const register = useContext(CloseSafetyContext)
  useLayoutEffect(() => register?.(handler), [handler, register])
}
