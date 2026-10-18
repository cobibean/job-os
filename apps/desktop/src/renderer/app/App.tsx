import { useCallback, useEffect, useRef, useState } from 'react'

import type { SetupSnapshot } from '../../shared/contracts'
import { OnboardingScreen } from './onboarding/OnboardingScreen'
import { WorkbenchApp } from './WorkbenchApp'
import { CloseSafetyContext, type PrepareClose } from './CloseSafetyContext'

export function App() {
  const [setup, setSetup] = useState<SetupSnapshot | null>(
    window.jobos?.setup ? null : { state: 'ready', message: 'JobOS is configured' }
  )
  const setupRef = useRef(setup)
  setupRef.current = setup
  const workbenchSafety = useRef<PrepareClose | null>(null)
  const registerSafety = useCallback((handler: PrepareClose) => {
    workbenchSafety.current = handler
    return () => { if (workbenchSafety.current === handler) workbenchSafety.current = null }
  }, [])

  useEffect(() => window.jobos?.lifecycle?.subscribePrepareClose(async reason => {
    if (setupRef.current === null) return true
    if (setupRef.current.state !== 'ready') return true
    try { return await workbenchSafety.current?.(reason) ?? false } catch { return false }
  }), [])

  useEffect(() => {
    if (!window.jobos?.setup) return
    let active = true
    void window.jobos.setup.get().then(value => { if (active) setSetup(value) }).catch(() => {
      if (active) setSetup({ state: 'required', message: 'JobOS setup is required' })
    })
    return () => { active = false }
  }, [])

  if (setup === null) return <div className="onboarding-loading" role="status">Checking local setup…</div>
  if (setup.state !== 'ready') return <OnboardingScreen initial={setup} />
  return <CloseSafetyContext.Provider value={registerSafety}><WorkbenchApp /></CloseSafetyContext.Provider>
}
