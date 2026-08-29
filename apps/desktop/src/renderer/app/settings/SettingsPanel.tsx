import { Check, X } from 'lucide-react'
import { useEffect, useRef } from 'react'

import type { CareerProfileBridge } from '../../../shared/contracts'
import { AgentAvatar } from '../../agents/avatar/AgentAvatar'
import { AGENT_AVATARS, type AgentAvatarId } from '../../agents/avatar/agentAvatars'
import { CareerProfileAgentSettings } from '../../career-profile/settings/CareerProfileAgentSettings'
import { THEMES, type ThemeMode } from '../theme/themes'
import { DiagnosticsPanel } from './diagnostics/DiagnosticsPanel'
import type { useConnectedAgents } from '../../agents/connected-agents/useConnectedAgents'
import { ConnectedAgentsSettings } from '../../agents/connected-agents/ConnectedAgentsSettings'
import { SettingsSection } from './SettingsSection'

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
  useEffect(() => {
    dialogRef.current?.focus()
  }, [])

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

        <SettingsSection
          description="Applies in both light and dark. Switch light/dark from the toggle in the top bar."
          id="settings-theme"
          title="Color theme"
        >
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
        </SettingsSection>

        {connectedAgentsState ? <ConnectedAgentsSettings onAgentsChanged={onConnectedAgentsChanged} state={connectedAgentsState} /> : null}

        {careerProfileBridge ? (
          <SettingsSection
            description="Choose how each connected agent may help update your Career Profile. You can change this anytime."
            id="settings-agent-editing"
            title="Agent editing"
          >
            <CareerProfileAgentSettings bridge={careerProfileBridge} />
          </SettingsSection>
        ) : null}

        <SettingsSection
          description="Choose the character shown throughout Agent Chat."
          id="settings-agent-avatar"
          title="Agent icon"
        >
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
        </SettingsSection>
        <DiagnosticsPanel />
      </div>
    </div>
  )
}
