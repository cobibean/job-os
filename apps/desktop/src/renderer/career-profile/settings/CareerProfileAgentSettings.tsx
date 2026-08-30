import { Check } from 'lucide-react'

import type { CareerProfileBridge } from '../../../shared/contracts'
import { useCareerProfileAgentSettings } from './useCareerProfileAgentSettings'

export function CareerProfileAgentSettings({ bridge }: { bridge: CareerProfileBridge }) {
  const settings = useCareerProfileAgentSettings(bridge)

  return (
    <>
      {settings.loadFailed ? (
        <p className="settings-agent-message error" role="alert">Connected agents could not load. Close Settings and try again.</p>
      ) : settings.connectedAgents.length === 0 ? (
        <p className="settings-agent-empty">No agents are connected to Career Profile.</p>
      ) : (
        <div className="settings-agent-list">
          {settings.connectedAgents.map(agent => {
            const busy = settings.busyAgentId === agent.agentId
            const confirmingDisconnect = settings.disconnectAgentId === agent.agentId
            return (
              <article className="settings-agent-card" key={agent.agentId}>
                <div className="settings-agent-heading">
                  <div><strong>{agent.displayName}</strong><small>Connected agent</small></div>
                  <button aria-label={`Disconnect ${agent.displayName}`} className="settings-agent-disconnect" disabled={busy} onClick={() => settings.setDisconnectAgentId(agent.agentId)} type="button">Remove access from this profile</button>
                </div>
                <div aria-label={`${agent.displayName} edit mode`} className="settings-trust-options" role="radiogroup">
                  <button aria-checked={agent.trustMode === 'review'} className={`settings-trust-option${agent.trustMode === 'review' ? ' selected' : ''}`} disabled={busy} onClick={() => { void settings.changeTrustMode(agent, 'review') }} role="radio" type="button">
                    <span><strong>Review every change</strong><small>You see the complete change and decide before it is saved.</small></span>
                    {agent.trustMode === 'review' ? <Check aria-hidden="true" size={15} /> : null}
                  </button>
                  <button aria-checked={agent.trustMode === 'direct'} className={`settings-trust-option${agent.trustMode === 'direct' ? ' selected' : ''}`} disabled={busy} onClick={() => { void settings.changeTrustMode(agent, 'direct') }} role="radio" type="button">
                    <span><strong>Allow direct edits</strong><small>Ordinary edits happen right away, with history and Undo. Identity, removals, Evidence, and loosened boundaries still ask.</small></span>
                    {agent.trustMode === 'direct' ? <Check aria-hidden="true" size={15} /> : null}
                  </button>
                </div>
                {confirmingDisconnect ? (
                  <div className="settings-disconnect-confirmation">
                    <p>Your Career Profile stays exactly as it is. This removes access only from this JobOS Profile; the installation-level agent connection remains available. External agents may retain information outside JobOS.</p>
                    <div>
                      <button className="settings-agent-disconnect confirm" disabled={busy} onClick={() => { void settings.disconnectAgent(agent) }} type="button" aria-label={`Confirm remove access ${agent.displayName}`}>Remove access from this profile</button>
                      <button className="settings-agent-cancel" disabled={busy} onClick={() => settings.setDisconnectAgentId(null)} type="button">Cancel</button>
                    </div>
                  </div>
                ) : null}
              </article>
            )
          })}
        </div>
      )}
      {settings.message ? <p aria-live="polite" className="settings-agent-message">{settings.message}</p> : null}
    </>
  )
}
