import { useState } from 'react'

import { AgentPanel } from './components/AgentPanel'
import { CenterWorkspace } from './components/CenterWorkspace'
import { JobNavigator } from './components/JobNavigator'
import { StatusBar } from './components/StatusBar'
import { LayoutPreset, WorkspaceBar } from './components/WorkspaceBar'
import { useConnectivity } from './hooks/useConnectivity'

const defaultPreset: LayoutPreset = 'review'

export function App() {
  const [activePreset, setActivePreset] = useState<LayoutPreset>(defaultPreset)
  const connectivity = useConnectivity()

  return (
    <div className="app-shell" data-layout={activePreset}>
      <WorkspaceBar
        activePreset={activePreset}
        onPresetChange={setActivePreset}
        onReset={() => setActivePreset(defaultPreset)}
      />
      <div className="workbench">
        <JobNavigator />
        <CenterWorkspace />
        <AgentPanel />
      </div>
      <StatusBar apiVersion={connectivity.apiVersion} state={connectivity.state} />
    </div>
  )
}
