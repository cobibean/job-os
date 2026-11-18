import { Moon, RotateCcw, Sun } from 'lucide-react'
import type { ReactNode } from 'react'
import type { TopLevelWorkspace } from './workspaceLayout'
import type { ThemeMode } from '../app/theme/themes'

export type WorkspaceBarWorkspace = TopLevelWorkspace | 'career-profile'

interface WorkspaceBarProps {
  activeWorkspace: WorkspaceBarWorkspace
  careerProfileEnabled: boolean
  onWorkspaceChange: (workspace: WorkspaceBarWorkspace) => void
  onReset: () => void
  onToggleMode: () => void
  themeMode: ThemeMode
  profileControl: ReactNode
}

// Research, Review and Agent Focus rearrange the same job workbench; Browse and
// Career Profile are separate places. A divider keeps the two kinds apart.
const workspaces: Array<{ id: WorkspaceBarWorkspace; label: string; place?: boolean }> = [
  { id: 'research', label: 'Research' },
  { id: 'review', label: 'Review' },
  { id: 'agent-focus', label: 'Agent Focus' },
  { id: 'browse', label: 'Browse', place: true }
]

export function WorkspaceBar({ activeWorkspace, careerProfileEnabled, onWorkspaceChange, onReset, onToggleMode, themeMode, profileControl }: WorkspaceBarProps) {
  const dark = themeMode === 'dark'
  return (
    <header className="workspace-bar">
      <div className="brand-lockup">
        <span className="brand">JobOS</span>
        {profileControl}
      </div>

      <nav aria-label="Workspace layouts" className="layout-switcher">
        {[...workspaces, ...(careerProfileEnabled ? [{ id: 'career-profile' as const, label: 'Career Profile', place: true }] : [])].map((workspace, index, all) => (
          <button
            aria-pressed={activeWorkspace === workspace.id}
            className={`layout-option${workspace.place && !all[index - 1]?.place ? ' first-place' : ''}`}
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
        <button aria-label="Reset layout" className="icon-button reset-layout" disabled={activeWorkspace === 'browse' || activeWorkspace === 'career-profile'} onClick={onReset} title="Reset this layout's panels to their default sizes and order" type="button">
          <RotateCcw aria-hidden="true" size={16} strokeWidth={1.5} />
        </button>
      </div>
    </header>
  )
}
