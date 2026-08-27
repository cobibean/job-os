import { useEffect, useState } from 'react'

import type { DiagnosticsSnapshot } from '../../shared/contracts'
import { SettingsSection } from '../components/SettingsSection'

function capabilityLabel(label: string, state: string): string {
  if (state === 'not-configured') return `${label} not configured`
  if (state === 'unavailable') return `${label} unavailable`
  if (state === 'offline') return `${label} offline`
  if (state === 'connecting') return `${label} connecting`
  if (state === 'disconnected') return `${label} disconnected`
  return `${label} available`
}

export function DiagnosticsPanel() {
  const [snapshot, setSnapshot] = useState<DiagnosticsSnapshot | null>(null)
  const [confirmReset, setConfirmReset] = useState(false)
  const [resetMessage, setResetMessage] = useState('')

  useEffect(() => {
    let active = true
    void window.jobos?.diagnostics?.get().then(value => {
      if (active) setSnapshot(value)
    }).catch(() => undefined)
    return () => { active = false }
  }, [])

  return (
    <SettingsSection className="diagnostics-panel" id="diagnostics" title="Diagnostics">
      {!snapshot ? <p>Capability states unavailable</p> : (
        <dl>
          <div><dt>Mode</dt><dd>{snapshot.mode}</dd></div>
          <div><dt>JobOS</dt><dd>{snapshot.appVersion}</dd></div>
          {snapshot.installationProfile ? <>
            <div><dt>JobOS Profile</dt><dd>{snapshot.installationProfile.name}</dd></div>
            <div><dt>Profile ID</dt><dd>{snapshot.installationProfile.id}</dd></div>
            <div><dt>Profile switch</dt><dd>{snapshot.installationProfile.switchStatus}</dd></div>
          </> : null}
          <div><dt>Local service</dt><dd>{capabilityLabel('Local service', snapshot.capabilities.localService)}</dd></div>
          <div><dt>Agent</dt><dd>{capabilityLabel('Agent', snapshot.capabilities.agent)}</dd></div>
          <div><dt>Desktop capability</dt><dd>{capabilityLabel('Desktop capability', snapshot.capabilities.desktop)}</dd></div>
          <div><dt>Renderer</dt><dd>{capabilityLabel('Renderer', snapshot.capabilities.renderer)}</dd></div>
          <div><dt>Artifact storage</dt><dd>{capabilityLabel('Artifact storage', snapshot.capabilities.artifactStorage)}</dd></div>
          <div><dt>Artifact gateway</dt><dd>{capabilityLabel('Artifact gateway', snapshot.capabilities.artifactGateway)}</dd></div>
          <div><dt>Transport</dt><dd>{snapshot.capabilities.transport}</dd></div>
        </dl>
      )}
      <div className="diagnostics-actions">
        <button onClick={() => { void window.jobos.diagnostics.openData() }} type="button">Open JobOS data</button>
        <button onClick={() => { void window.jobos.diagnostics.openLogs() }} type="button">Open logs</button>
      </div>
      <div className="demo-reset">
        {!confirmReset ? (
          <button onClick={() => setConfirmReset(true)} type="button">Reset fictional demo</button>
        ) : (
          <>
            <p>This replaces only the fictional demo job. Your other jobs are unchanged.</p>
            <button onClick={() => {
              void window.jobos.setup.initialize(true, true).then(result => setResetMessage(result.message))
            }} type="button">Confirm demo reset</button>
            <button onClick={() => setConfirmReset(false)} type="button">Cancel</button>
          </>
        )}
        {resetMessage ? <p role="status">{resetMessage}</p> : null}
      </div>
    </SettingsSection>
  )
}
