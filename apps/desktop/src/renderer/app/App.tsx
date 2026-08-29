import { useEffect, useState } from 'react'

import type { SetupSnapshot } from '../../shared/contracts'
import { OnboardingScreen } from './onboarding/OnboardingScreen'
import { WorkbenchApp } from './WorkbenchApp'

export function App() {
  const [setup, setSetup] = useState<SetupSnapshot | null>(
    window.jobos?.setup ? null : { state: 'ready', message: 'JobOS is configured' }
  )

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
  return <WorkbenchApp />
}
