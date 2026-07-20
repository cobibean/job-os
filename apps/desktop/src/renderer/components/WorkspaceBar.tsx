import { PanelLeft, RotateCcw } from 'lucide-react'

export type LayoutPreset = 'research' | 'review' | 'agent-focus'

interface WorkspaceBarProps {
  activePreset: LayoutPreset
  onPresetChange: (preset: LayoutPreset) => void
  onReset: () => void
}

const presets: Array<{ id: LayoutPreset; label: string }> = [
  { id: 'research', label: 'Research' },
  { id: 'review', label: 'Review' },
  { id: 'agent-focus', label: 'Agent Focus' }
]

export function WorkspaceBar({ activePreset, onPresetChange, onReset }: WorkspaceBarProps) {
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

      <button className="reset-layout" onClick={onReset} type="button">
        <RotateCcw aria-hidden="true" size={16} strokeWidth={1.5} />
        Reset layout
      </button>
    </header>
  )
}
