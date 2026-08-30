import { CheckCircle2, CircleAlert, LoaderCircle } from 'lucide-react'
import { useState } from 'react'

import type { SetupSnapshot } from '../../../shared/contracts'

interface OnboardingScreenProps {
  initial: SetupSnapshot
}

export function OnboardingScreen({ initial }: OnboardingScreenProps) {
  const [snapshot, setSnapshot] = useState(initial)

  const initialize = async () => {
    setSnapshot({ state: 'working', message: 'Creating local profile…' })
    try {
      setSnapshot(await window.jobos.setup.initialize())
    } catch {
      setSnapshot({
        state: 'error',
        message: 'Setup could not finish. Check that the local JobOS tools are installed, then retry.'
      })
    }
  }

  return (
    <main className="onboarding-shell">
      <section aria-labelledby="onboarding-title" className="onboarding-card">
        <span aria-hidden="true" className="onboarding-mark">J</span>
        <h1 id="onboarding-title">Set up JobOS on this Mac</h1>
        <p>JobOS will create a local JobOS Profile, use a loopback-only service, and add one clearly labeled fictional demo job. A connected agent is optional.</p>
        <p className={`onboarding-status ${snapshot.state}`} role="status">
          {snapshot.state === 'working' ? <LoaderCircle aria-hidden="true" className="spin" /> : null}
          {snapshot.state === 'succeeded' ? <CheckCircle2 aria-hidden="true" /> : null}
          {snapshot.state === 'error' ? <CircleAlert aria-hidden="true" /> : null}
          <span>{snapshot.message}</span>
        </p>
        {snapshot.state === 'succeeded' ? (
          <button className="primary-button" onClick={() => { void window.jobos.setup.restart() }} type="button">
            Restart JobOS
          </button>
        ) : (
          <button className="primary-button" disabled={snapshot.state === 'working'} onClick={() => { void initialize() }} type="button">
            {snapshot.state === 'error' ? 'Retry setup' : 'Set up JobOS'}
          </button>
        )}
        <small>No credentials or private file locations are shown here.</small>
      </section>
    </main>
  )
}
