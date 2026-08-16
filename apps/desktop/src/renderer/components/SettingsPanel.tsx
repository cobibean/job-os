import { Check, X } from 'lucide-react'
import { useEffect, useRef } from 'react'

import { THEMES, type ThemeMode } from '../theme/themes'
import { DiagnosticsPanel } from '../diagnostics/DiagnosticsPanel'

interface SettingsPanelProps {
  activeThemeId: string
  mode: ThemeMode
  onClose: () => void
  onSelectTheme: (themeId: string) => void
}

const SWATCH_TOKENS = ['bg', 'surface-raised', 'accent', 'text'] as const

export function SettingsPanel({ activeThemeId, mode, onClose, onSelectTheme }: SettingsPanelProps) {
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
        <DiagnosticsPanel />
      </div>
    </div>
  )
}
