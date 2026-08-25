import { Check, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import type { CareerProfileBridge, CareerProfileTrustMode, ConnectedCareerProfileAgent } from '../../shared/contracts'
import { AgentAvatar } from '../agent-avatar/AgentAvatar'
import { AGENT_AVATARS, type AgentAvatarId } from '../agent-avatar/agentAvatars'
import { THEMES, type ThemeMode } from '../theme/themes'
import { DiagnosticsPanel } from '../diagnostics/DiagnosticsPanel'
import type { useConnectedAgents } from '../hooks/useConnectedAgents'
import { ConnectedAgentsSettings } from './ConnectedAgentsSettings'

interface SettingsPanelProps {
  activeAgentAvatarId: AgentAvatarId
  activeThemeId: string
  careerProfileBridge?: CareerProfileBridge | null
  connectedAgentsState?: ReturnType<typeof useConnectedAgents>
  mode: ThemeMode
  onClose: () => void
  onConnectedAgentsChanged?: () => Promise<void>
  onSelectAgentAvatar: (avatarId: AgentAvatarId) => void
  onSelectTheme: (themeId: string) => void
}

const SWATCH_TOKENS = ['bg', 'surface-raised', 'accent', 'text'] as const

export function SettingsPanel({
  activeAgentAvatarId,
  activeThemeId,
  careerProfileBridge = null,
  connectedAgentsState,
  mode,
  onClose,
  onConnectedAgentsChanged,
  onSelectAgentAvatar,
  onSelectTheme
}: SettingsPanelProps) {
  const dialogRef = useRef<HTMLDivElement>(null)
  const [connectedAgents, setConnectedAgents] = useState<ConnectedCareerProfileAgent[]>([])
  const [busyAgentId, setBusyAgentId] = useState<string | null>(null)
  const [disconnectAgentId, setDisconnectAgentId] = useState<string | null>(null)
  const [agentMessage, setAgentMessage] = useState('')
  const [agentLoadFailed, setAgentLoadFailed] = useState(false)

  useEffect(() => {
    dialogRef.current?.focus()
  }, [])

  useEffect(() => {
    if (!careerProfileBridge) return
    let active = true
    setAgentLoadFailed(false)
    void careerProfileBridge.listConnectedAgents()
      .then(agents => {
        if (active) setConnectedAgents(agents.filter(agent => agent.active))
      })
      .catch(() => {
        if (active) setAgentLoadFailed(true)
      })
    return () => { active = false }
  }, [careerProfileBridge])

  const changeTrustMode = async (agent: ConnectedCareerProfileAgent, trustMode: CareerProfileTrustMode) => {
    if (!careerProfileBridge || agent.trustMode === trustMode || busyAgentId) return
    setBusyAgentId(agent.agentId)
    setAgentMessage('')
    try {
      const updated = await careerProfileBridge.updateConnectedAgentTrustMode(agent.agentId, trustMode)
      setConnectedAgents(current => current.map(candidate => candidate.agentId === updated.agentId ? updated : candidate))
      setAgentMessage(trustMode === 'direct'
        ? `${agent.displayName} can now make ordinary edits directly.`
        : `${agent.displayName} will ask before every Career Profile change.`)
    } catch {
      setAgentMessage(`Could not change ${agent.displayName}’s edit mode. Try again.`)
    } finally {
      setBusyAgentId(null)
    }
  }

  const disconnectAgent = async (agent: ConnectedCareerProfileAgent) => {
    if (!careerProfileBridge || busyAgentId) return
    setBusyAgentId(agent.agentId)
    setAgentMessage('')
    try {
      await careerProfileBridge.disconnectConnectedAgent(agent.agentId)
      setConnectedAgents(current => current.filter(candidate => candidate.agentId !== agent.agentId))
      setDisconnectAgentId(null)
      setAgentMessage(`${agent.displayName} is disconnected. Your Career Profile was not changed.`)
    } catch {
      setAgentMessage(`Could not disconnect ${agent.displayName}. Try again.`)
    } finally {
      setBusyAgentId(null)
    }
  }

  return (
    <div className="settings-overlay" onClick={onClose} role="presentation">
      <div
        aria-label="Settings"
        aria-modal="true"
        className="settings-panel"
        onClick={event => event.stopPropagation()}
        onKeyDown={event => {
          if (event.key === 'Escape') onClose()
        }}
        ref={dialogRef}
        role="dialog"
        tabIndex={-1}
      >
        <header className="settings-header">
          <strong>Settings</strong>
          <button aria-label="Close settings" className="icon-button" onClick={onClose} type="button">
            <X aria-hidden="true" size={15} strokeWidth={1.5} />
          </button>
        </header>

        <section aria-labelledby="settings-theme-heading" className="settings-section">
          <h2 className="settings-section-title" id="settings-theme-heading">Color theme</h2>
          <p className="settings-section-hint">
            Applies in both light and dark. Switch light/dark from the toggle in the top bar.
          </p>
          <div className="theme-grid" role="radiogroup" aria-labelledby="settings-theme-heading">
            {THEMES.map(theme => {
              const selected = theme.id === activeThemeId
              const tokens = theme.modes[mode]
              return (
                <button
                  aria-checked={selected}
                  className={`theme-option${selected ? ' selected' : ''}`}
                  key={theme.id}
                  onClick={() => onSelectTheme(theme.id)}
                  role="radio"
                  type="button"
                >
                  <span aria-hidden="true" className="theme-swatches" style={{ background: tokens.bg }}>
                    {SWATCH_TOKENS.map(token => (
                      <span className="theme-swatch" key={token} style={{ background: tokens[token] }} />
                    ))}
                  </span>
                  <span className="theme-copy">
                    <strong>{theme.label}</strong>
                    <small>{theme.description}</small>
                  </span>
                  {selected ? <Check aria-hidden="true" className="theme-check" size={15} strokeWidth={2} /> : null}
                </button>
              )
            })}
          </div>
        </section>

        {connectedAgentsState ? <ConnectedAgentsSettings onAgentsChanged={onConnectedAgentsChanged} state={connectedAgentsState} /> : null}

        {careerProfileBridge ? (
          <section aria-labelledby="settings-agent-editing-heading" className="settings-section settings-section-divided">
            <h2 className="settings-section-title" id="settings-agent-editing-heading">Agent editing</h2>
            <p className="settings-section-hint">
              Choose how each connected agent may help update your Career Profile. You can change this anytime.
            </p>

            {agentLoadFailed ? (
              <p className="settings-agent-message error" role="alert">Connected agents could not load. Close Settings and try again.</p>
            ) : connectedAgents.length === 0 ? (
              <p className="settings-agent-empty">No agents are connected to Career Profile.</p>
            ) : (
              <div className="settings-agent-list">
                {connectedAgents.map(agent => {
                  const busy = busyAgentId === agent.agentId
                  const confirmingDisconnect = disconnectAgentId === agent.agentId
                  return (
                    <article className="settings-agent-card" key={agent.agentId}>
                      <div className="settings-agent-heading">
                        <div><strong>{agent.displayName}</strong><small>Connected agent</small></div>
                        <button
                          aria-label={`Disconnect ${agent.displayName}`}
                          className="settings-agent-disconnect"
                          disabled={busy}
                          onClick={() => setDisconnectAgentId(agent.agentId)}
                          type="button"
                        >Remove access from this profile</button>
                      </div>

                      <div aria-label={`${agent.displayName} edit mode`} className="settings-trust-options" role="radiogroup">
                        <button
                          aria-checked={agent.trustMode === 'review'}
                          className={`settings-trust-option${agent.trustMode === 'review' ? ' selected' : ''}`}
                          disabled={busy}
                          onClick={() => { void changeTrustMode(agent, 'review') }}
                          role="radio"
                          type="button"
                        >
                          <span><strong>Review every change</strong><small>You see the complete change and decide before it is saved.</small></span>
                          {agent.trustMode === 'review' ? <Check aria-hidden="true" size={15} /> : null}
                        </button>
                        <button
                          aria-checked={agent.trustMode === 'direct'}
                          className={`settings-trust-option${agent.trustMode === 'direct' ? ' selected' : ''}`}
                          disabled={busy}
                          onClick={() => { void changeTrustMode(agent, 'direct') }}
                          role="radio"
                          type="button"
                        >
                          <span><strong>Allow direct edits</strong><small>Ordinary edits happen right away, with history and Undo. Identity, removals, Evidence, and loosened boundaries still ask.</small></span>
                          {agent.trustMode === 'direct' ? <Check aria-hidden="true" size={15} /> : null}
                        </button>
                      </div>

                      {confirmingDisconnect ? (
                        <div className="settings-disconnect-confirmation">
                          <p>Your Career Profile stays exactly as it is. This removes access only from this JobOS Profile; the installation-level agent connection remains available. External agents may retain information outside JobOS.</p>
                          <div>
                            <button className="settings-agent-disconnect confirm" disabled={busy} onClick={() => { void disconnectAgent(agent) }} type="button" aria-label={`Confirm remove access ${agent.displayName}`}>Remove access from this profile</button>
                            <button className="settings-agent-cancel" disabled={busy} onClick={() => setDisconnectAgentId(null)} type="button">Cancel</button>
                          </div>
                        </div>
                      ) : null}
                    </article>
                  )
                })}
              </div>
            )}
            {agentMessage ? <p aria-live="polite" className="settings-agent-message">{agentMessage}</p> : null}
          </section>
        ) : null}

        <section aria-labelledby="settings-agent-avatar-heading" className="settings-section settings-section-divided">
          <h2 className="settings-section-title" id="settings-agent-avatar-heading">Agent icon</h2>
          <p className="settings-section-hint">
            Choose the character shown throughout Agent Chat.
          </p>
          <div className="agent-avatar-grid" role="radiogroup" aria-labelledby="settings-agent-avatar-heading">
            {AGENT_AVATARS.map(avatar => {
              const selected = avatar.id === activeAgentAvatarId
              return (
                <button
                  aria-checked={selected}
                  className={`agent-avatar-option${selected ? ' selected' : ''}`}
                  key={avatar.id}
                  onClick={() => onSelectAgentAvatar(avatar.id)}
                  role="radio"
                  type="button"
                >
                  <AgentAvatar avatarId={avatar.id} size="settings" />
                  <strong>{avatar.label}</strong>
                  {selected ? <Check aria-hidden="true" className="theme-check" size={15} strokeWidth={2} /> : null}
                </button>
              )
            })}
          </div>
        </section>
        <DiagnosticsPanel />
      </div>
    </div>
  )
}
