import { AgentPanel } from './components/AgentPanel'
import { CenterWorkspace } from './components/CenterWorkspace'
import { JobNavigator } from './components/JobNavigator'
import { StatusBar } from './components/StatusBar'
import { WorkbenchLayout } from './components/WorkbenchLayout'
import { WorkspaceBar } from './components/WorkspaceBar'
import { useConnectivity } from './hooks/useConnectivity'
import { useJobs } from './hooks/useJobs'
import { useWorkspace } from './hooks/useWorkspace'

export function App() {
  const connectivity = useConnectivity()
  const jobState = useJobs()
  const layoutState = useWorkspace(jobState.selectedJobId)
  const activePreset = layoutState.workspace.selectedPreset

  return (
    <div className="app-shell" data-layout={activePreset}>
      <WorkspaceBar
        activePreset={activePreset}
        onPresetChange={layoutState.selectPreset}
        onReset={layoutState.reset}
      />
      <WorkbenchLayout
        agent={<AgentPanel contextLabel={jobState.selectedJob ? `${jobState.selectedJob.company} · ${jobState.selectedJob.title}` : 'No active job'} />}
        center={<CenterWorkspace activeSurface={layoutState.workspace.activeCenterSurface} />}
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
      <StatusBar apiVersion={connectivity.apiVersion} message={connectivity.message} state={connectivity.state} />
    </div>
  )
}
