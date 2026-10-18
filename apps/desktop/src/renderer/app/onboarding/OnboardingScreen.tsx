import { CheckCircle2, CircleAlert, LoaderCircle } from 'lucide-react'
import { useState } from 'react'

import type { SetupSnapshot } from '../../../shared/contracts'

interface OnboardingScreenProps {
  initial: SetupSnapshot
}

export function OnboardingScreen({ initial }: OnboardingScreenProps) {
  const [snapshot, setSnapshot] = useState(initial)
  const recovery = initial.recovery === true

  const retryConnection = async () => {
    setSnapshot({ state: 'working', recovery: true, message: 'Connecting to the existing JobOS profile…' })
    try { setSnapshot(await window.jobos.setup.retryConnection()) } catch {
      setSnapshot({ state: 'error', recovery: true, message: 'The existing JobOS profile is still unavailable. Retry the connection or restart JobOS.' })
    }
  }

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
        <h1 id="onboarding-title">{recovery ? 'Reconnect to JobOS' : 'Set up JobOS on this Mac'}</h1>
        <p>{recovery ? 'Your existing configuration is available. JobOS will verify the connection and profile before reopening your workspace.' : 'JobOS will create a local JobOS Profile, use a loopback-only service, and add one clearly labeled fictional demo job. A connected agent is optional.'}</p>
        <p className={`onboarding-status ${snapshot.state}`} role="status">
          {snapshot.state === 'working' ? <LoaderCircle aria-hidden="true" className="spin" /> : null}
          {snapshot.state === 'succeeded' ? <CheckCircle2 aria-hidden="true" /> : null}
          {snapshot.state === 'error' ? <CircleAlert aria-hidden="true" /> : null}
          <span>{snapshot.message}</span>
        </p>
        {recovery ? (
          <>
            <button className="primary-button" disabled={snapshot.state === 'working' || snapshot.state === 'succeeded'} onClick={() => { void retryConnection() }} type="button">Retry connection</button>
            <button disabled={snapshot.state === 'working'} onClick={() => { void window.jobos.setup.restart() }} type="button">Restart JobOS</button>
          </>
        ) : snapshot.state === 'succeeded' ? (
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
