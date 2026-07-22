import { Moon, PanelLeft, RotateCcw, Sun } from 'lucide-react'
import type { LayoutPreset } from '../workspaceLayout'
import type { ThemeMode } from '../theme/themes'

interface WorkspaceBarProps {
  activePreset: LayoutPreset
  onPresetChange: (preset: LayoutPreset) => void
  onReset: () => void
  onToggleMode: () => void
  themeMode: ThemeMode
}

const presets: Array<{ id: LayoutPreset; label: string }> = [
  { id: 'research', label: 'Research' },
  { id: 'review', label: 'Review' },
  { id: 'agent-focus', label: 'Agent Focus' }
]

export function WorkspaceBar({ activePreset, onPresetChange, onReset, onToggleMode, themeMode }: WorkspaceBarProps) {
  const dark = themeMode === 'dark'
  return (
    <header className="workspace-bar">
      <div className="brand-lockup">
        <span className="brand">JobOS</span>
        <PanelLeft aria-hidden="true" className="brand-panel-icon" size={17} strokeWidth={1.5} />
      </div>

      <nav aria-label="Workspace layouts" className="layout-switcher">
        {presets.map(preset => (
          <button
            aria-pressed={activePreset === preset.id}
            className="layout-option"
            key={preset.id}
            onClick={() => onPresetChange(preset.id)}
            type="button"
          >
            {preset.label}
          </button>
        ))}
      </nav>

      <div className="workspace-bar-actions">
        <button
          aria-label={dark ? 'Switch to light mode' : 'Switch to dark mode'}
          aria-pressed={!dark}
          className="icon-button mode-toggle"
          onClick={onToggleMode}
          title={dark ? 'Switch to light mode' : 'Switch to dark mode'}
          type="button"
        >
          {dark
            ? <Sun aria-hidden="true" size={16} strokeWidth={1.5} />
            : <Moon aria-hidden="true" size={16} strokeWidth={1.5} />}
        </button>
        <button className="reset-layout" onClick={onReset} type="button">
          <RotateCcw aria-hidden="true" size={16} strokeWidth={1.5} />
          Reset layout
        </button>
      </div>
    </header>
  )
}
