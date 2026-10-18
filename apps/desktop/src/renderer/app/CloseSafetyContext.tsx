import { createContext, useContext, useLayoutEffect } from 'react'

export type PrepareClose = (reason: 'window-close' | 'profile-switch') => Promise<boolean>
export const CloseSafetyContext = createContext<((handler: PrepareClose) => () => void) | null>(null)

export function useCloseSafety(handler: PrepareClose) {
  const register = useContext(CloseSafetyContext)
  useLayoutEffect(() => register?.(handler), [handler, register])
}
