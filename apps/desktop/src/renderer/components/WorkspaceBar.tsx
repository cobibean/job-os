import { Moon, RotateCcw, Sun } from 'lucide-react'
import type { TopLevelWorkspace } from '../workspaceLayout'
import type { ThemeMode } from '../theme/themes'

export type WorkspaceBarWorkspace = TopLevelWorkspace | 'career-profile'

interface WorkspaceBarProps {
  activeWorkspace: WorkspaceBarWorkspace
  careerProfileEnabled: boolean
  onWorkspaceChange: (workspace: WorkspaceBarWorkspace) => void
  onReset: () => void
  onToggleMode: () => void
  themeMode: ThemeMode
}

const workspaces: Array<{ id: WorkspaceBarWorkspace; label: string }> = [
  { id: 'research', label: 'Research' },
  { id: 'review', label: 'Review' },
  { id: 'agent-focus', label: 'Agent Focus' },
  { id: 'browse', label: 'Browse' }
]

export function WorkspaceBar({ activeWorkspace, careerProfileEnabled, onWorkspaceChange, onReset, onToggleMode, themeMode }: WorkspaceBarProps) {
  const dark = themeMode === 'dark'
  return (
    <header className="workspace-bar">
      <div className="brand-lockup">
        <span className="brand">JobOS</span>
      </div>

      <nav aria-label="Workspace layouts" className="layout-switcher">
        {[...workspaces, ...(careerProfileEnabled ? [{ id: 'career-profile' as const, label: 'Career Profile' }] : [])].map(workspace => (
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
        <button className="reset-layout" disabled={activeWorkspace === 'browse' || activeWorkspace === 'career-profile'} onClick={onReset} type="button">
          <RotateCcw aria-hidden="true" size={16} strokeWidth={1.5} />
          Reset layout
        </button>
      </div>
    </header>
  )
}
