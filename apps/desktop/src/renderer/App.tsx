import { useState } from 'react'

import { AgentPanel } from './components/AgentPanel'
import { CenterWorkspace } from './components/CenterWorkspace'
import { JobNavigator } from './components/JobNavigator'
import { StatusBar } from './components/StatusBar'
import { LayoutPreset, WorkspaceBar } from './components/WorkspaceBar'
import { useConnectivity } from './hooks/useConnectivity'
import { useJobs } from './hooks/useJobs'

const defaultPreset: LayoutPreset = 'review'

export function App() {
  const [activePreset, setActivePreset] = useState<LayoutPreset>(defaultPreset)
  const connectivity = useConnectivity()
  const jobState = useJobs()

  return (
    <div className="app-shell" data-layout={activePreset}>
      <WorkspaceBar
        activePreset={activePreset}
        onPresetChange={setActivePreset}
        onReset={() => undefined}
      />
      <div className="workbench">
        <JobNavigator
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
        />
        <CenterWorkspace />
        <AgentPanel contextLabel={jobState.selectedJob ? `${jobState.selectedJob.company} · ${jobState.selectedJob.title}` : 'No active job'} />
      </div>
      <StatusBar apiVersion={connectivity.apiVersion} message={connectivity.message} state={connectivity.state} />
    </div>
  )
}
