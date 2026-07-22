import { AgentPanel } from './components/AgentPanel'
import { CenterWorkspace } from './components/CenterWorkspace'
import { JobNavigator } from './components/JobNavigator'
import { SettingsPanel } from './components/SettingsPanel'
import { StatusBar } from './components/StatusBar'
import { WorkbenchLayout } from './components/WorkbenchLayout'
import { WorkspaceBar } from './components/WorkspaceBar'
import { useConnectivity } from './hooks/useConnectivity'
import { useJobs } from './hooks/useJobs'
import { useWorkspace } from './hooks/useWorkspace'
import { useTheme } from './theme/useTheme'
import { useState } from 'react'

export function App() {
  const connectivity = useConnectivity()
  const jobState = useJobs()
  const layoutState = useWorkspace(jobState.selectedJobId)
  const theme = useTheme()
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [agentModalOpen, setAgentModalOpen] = useState(false)
  const activePreset = layoutState.workspace.selectedPreset
  const activeLayout = layoutState.workspace.layouts[activePreset]

  return (
    <div className="app-shell" data-layout={activePreset}>
      <WorkspaceBar
        activePreset={activePreset}
        onPresetChange={layoutState.selectPreset}
        onReset={layoutState.reset}
        onToggleMode={theme.toggleMode}
        themeMode={theme.mode}
      />
      <WorkbenchLayout
        agent={<AgentPanel
          apiState={connectivity.state}
          contextLabel={jobState.selectedJob ? `${jobState.selectedJob.company} · ${jobState.selectedJob.title}` : 'No active job'}
          onArtifactRendered={layoutState.showDocument}
          onModalOpenChange={setAgentModalOpen}
        />}
        center={<CenterWorkspace
          activeSurface={layoutState.workspace.activeCenterSurface}
          activeJob={jobState.selectedJob}
          activeArtifactId={layoutState.workspace.activeArtifactId ?? null}
          activeArtifactPage={layoutState.workspace.activeArtifactPage ?? 1}
          activeArtifactZoom={layoutState.workspace.activeArtifactZoom ?? 1}
          browserState={{
            tabs: layoutState.workspace.browserTabs ?? [],
            activeTabId: layoutState.workspace.activeBrowserTabId ?? null
          }}
          browserRepaired={Boolean(layoutState.workspace.repairedBrowser)}
          browserRepairReasons={layoutState.workspace.browserRepairReasons ?? []}
          browserVisible={!activeLayout.collapsed.includes('center') && !agentModalOpen}
          jobs={jobState.jobs}
          layoutSignal={`${activePreset}:${activeLayout.order.join(',')}:${activeLayout.collapsed.join(',')}`}
          onBrowserPersist={layoutState.updateBrowserState}
          onDocumentPersist={layoutState.updateDocumentState}
          onJobSaved={jobState.selectJob}
          workspaceHydrated={layoutState.hydrated}
        />}
        jobs={<JobNavigator
          error={jobState.error}
          feedback={jobState.feedback}
          jobs={jobState.jobs}
          loading={jobState.loading}
          onMove={jobState.reorder}
          onReorder={jobState.reorderTo}
          onQueryChange={jobState.setQuery}
          onSelect={jobState.selectJob}
          onSortChange={jobState.changeSort}
          onStatusChange={jobState.changeStatus}
          onStatusGroupChange={jobState.setStatusGroup}
          query={jobState.query}
          selectedJobId={jobState.selectedJobId}
          sortMode={jobState.sortMode}
          statusGroup={jobState.statusGroup}
        />}
        onCollapse={layoutState.collapse}
        onMove={layoutState.move}
        onResize={layoutState.resize}
        workspace={layoutState.workspace}
      />
      <p aria-live="polite" className="layout-announcement">{layoutState.announcement}</p>
      <StatusBar apiVersion={connectivity.apiVersion} message={connectivity.message} onOpenSettings={() => setSettingsOpen(true)} state={connectivity.state} />
      {settingsOpen ? (
        <SettingsPanel
          activeThemeId={theme.themeId}
          mode={theme.mode}
          onClose={() => setSettingsOpen(false)}
          onSelectTheme={theme.selectTheme}
        />
      ) : null}
    </div>
  )
}
