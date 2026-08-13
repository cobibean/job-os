import { Moon, RotateCcw, Sun } from 'lucide-react'
import type { TopLevelWorkspace } from '../workspaceLayout'
import type { ThemeMode } from '../theme/themes'

interface WorkspaceBarProps {
  activeWorkspace: TopLevelWorkspace
  onWorkspaceChange: (workspace: TopLevelWorkspace) => void
  onReset: () => void
  onToggleMode: () => void
  themeMode: ThemeMode
}

const workspaces: Array<{ id: TopLevelWorkspace; label: string }> = [
  { id: 'research', label: 'Research' },
  { id: 'review', label: 'Review' },
  { id: 'agent-focus', label: 'Agent Focus' },
  { id: 'browse', label: 'Browse' }
]

export function WorkspaceBar({ activeWorkspace, onWorkspaceChange, onReset, onToggleMode, themeMode }: WorkspaceBarProps) {
  const dark = themeMode === 'dark'
  return (
    <header className="workspace-bar">
      <div className="brand-lockup">
        <span className="brand">JobOS</span>
      </div>

      <nav aria-label="Workspace layouts" className="layout-switcher">
        {workspaces.map(workspace => (
          <button
            aria-pressed={activeWorkspace === workspace.id}
            className="layout-option"
            key={workspace.id}
            onClick={() => onWorkspaceChange(workspace.id)}
            type="button"
          >
            {workspace.label}
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
        <button className="reset-layout" disabled={activeWorkspace === 'browse'} onClick={onReset} type="button">
          <RotateCcw aria-hidden="true" size={16} strokeWidth={1.5} />
          Reset layout
        </button>
      </div>
    </header>
  )
}
