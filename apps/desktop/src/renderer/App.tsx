import { AgentPanel } from './components/AgentPanel'
import { CenterWorkspace, type JobListingRequest } from './components/CenterWorkspace'
import { JobNavigator } from './components/JobNavigator'
import { SettingsPanel } from './components/SettingsPanel'
import { StatusBar } from './components/StatusBar'
import { WorkbenchLayout } from './components/WorkbenchLayout'
import { WorkspaceBar } from './components/WorkspaceBar'
import { DocxDocumentEditorShell } from './document-editor/DocxDocumentEditorShell'
import { useConnectivity } from './hooks/useConnectivity'
import { useJobs } from './hooks/useJobs'
import { useWorkspace } from './hooks/useWorkspace'
import { useTheme } from './theme/useTheme'
import { useCallback, useEffect, useRef, useState } from 'react'
import type { DocxOpenResult } from '../shared/docxDocuments'

export function App() {
  const connectivity = useConnectivity()
  const jobState = useJobs()
  const layoutState = useWorkspace(jobState.selectedJobId)
  const theme = useTheme()
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [settingsPreparing, setSettingsPreparing] = useState(false)
  const [agentModalOpen, setAgentModalOpen] = useState(false)
  const [jobListingRequest, setJobListingRequest] = useState<JobListingRequest | null>(null)
  const [documentMutationGeneration, setDocumentMutationGeneration] = useState(0)
  const [documentPreviewMode, setDocumentPreviewMode] = useState<'pdf' | 'docx'>('pdf')
  const [editingDocument, setEditingDocument] = useState<DocxOpenResult | null>(null)
  const nextJobListingRequestId = useRef(0)
  const latestNavigatorSelection = useRef(0)
  const navigatorSelectionQueue = useRef(Promise.resolve())
  const prepareClose = useRef<() => Promise<boolean>>(async () => true)
  const activePreset = layoutState.workspace.selectedPreset
  const activeLayout = layoutState.workspace.layouts[activePreset]

  useEffect(() => window.jobos?.lifecycle?.subscribePrepareClose(
    () => prepareClose.current()
  ), [])

  useEffect(() => setDocumentPreviewMode('pdf'), [jobState.selectedJobId])

  const showPublishedDocument = useCallback(() => {
    setDocumentMutationGeneration(generation => generation + 1)
    return layoutState.showDocument()
  }, [layoutState.showDocument])

  const openSettings = async () => {
    if (settingsOpen || settingsPreparing) return
    const browser = window.jobos?.browser
    if (!browser) {
      setSettingsOpen(true)
      return
    }
    setSettingsPreparing(true)
    try {
      await browser.setBounds({ x: 0, y: 0, width: 0, height: 0, visible: false })
      setSettingsOpen(true)
    } catch {
      // Keep the panel closed rather than rendering it beneath an attached native browser view.
    } finally {
      setSettingsPreparing(false)
    }
  }

  const selectJobFromNavigator = (jobId: string): Promise<void> => {
    const selectionRequest = latestNavigatorSelection.current + 1
    latestNavigatorSelection.current = selectionRequest
    const job = jobState.jobs.find(candidate => candidate.jobId === jobId)
    if (!job) return Promise.resolve()
    const operation = navigatorSelectionQueue.current.then(async () => {
      if (selectionRequest !== latestNavigatorSelection.current) return
      if (!await jobState.selectJob(jobId)) return
      try {
        await layoutState.showBrowser()
      } catch {
        // The visible workspace update already succeeded; navigation should not depend on persistence.
      }
      if (selectionRequest !== latestNavigatorSelection.current) return
      nextJobListingRequestId.current += 1
      setJobListingRequest({
        requestId: nextJobListingRequestId.current,
        jobId: job.jobId,
        canonicalUrl: job.canonicalUrl
      })
    })
    navigatorSelectionQueue.current = operation.catch(() => undefined)
    return operation
  }

  return (
    <div className="app-shell" data-layout={activePreset}>
      <WorkspaceBar
        activePreset={activePreset}
        onPresetChange={layoutState.selectPreset}
        onReset={layoutState.reset}
        onToggleMode={theme.toggleMode}
        themeMode={theme.mode}
      />
      {editingDocument ? (
        <DocxDocumentEditorShell
          opened={editingDocument}
          jobLabel={jobState.selectedJob
            ? `${jobState.selectedJob.company} · ${jobState.selectedJob.title}`
            : 'Selected job'}
          onPrepareClose={handler => { prepareClose.current = handler }}
          onExit={() => {
            setDocumentPreviewMode('docx')
            setEditingDocument(null)
            setDocumentMutationGeneration(generation => generation + 1)
          }}
        />
      ) : (
      <WorkbenchLayout
        agent={<AgentPanel
          apiState={connectivity.state}
          contextLabel={jobState.selectedJob ? `${jobState.selectedJob.company} · ${jobState.selectedJob.title}` : 'No active job'}
          onArtifactRendered={showPublishedDocument}
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
          browserVisible={!activeLayout.collapsed.includes('center') && !agentModalOpen && !settingsOpen && !settingsPreparing}
          documentMutationGeneration={documentMutationGeneration}
          documentPreviewMode={documentPreviewMode}
          jobListingRequest={jobListingRequest}
          jobs={jobState.jobs}
          layoutSignal={`${activePreset}:${activeLayout.order.join(',')}:${activeLayout.collapsed.join(',')}`}
          onBrowserPersist={layoutState.updateBrowserState}
          onDocumentPersist={layoutState.updateDocumentState}
          onDocumentPreviewModeChange={setDocumentPreviewMode}
          onJobSaved={async jobId => {
            await jobState.selectJob(jobId)
            setDocumentMutationGeneration(generation => generation + 1)
          }}
          onOpenEditor={setEditingDocument}
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
          onSelect={selectJobFromNavigator}
          onSortChange={jobState.changeSort}
          onStatusChange={jobState.changeStatus}
          onStatusGroupChange={jobState.setStatusGroup}
          query={jobState.query}
          selectedJobId={jobState.selectedJobId}
          selectedJobDetail={jobState.selectedJobDetail}
          sortMode={jobState.sortMode}
          statusGroup={jobState.statusGroup}
        />}
        onCollapse={layoutState.collapse}
        onMove={layoutState.move}
        onResize={layoutState.resize}
        workspace={layoutState.workspace}
      />
      )}
      <p aria-live="polite" className="layout-announcement">{layoutState.announcement}</p>
      <StatusBar apiVersion={connectivity.apiVersion} message={connectivity.message} onOpenSettings={() => { void openSettings() }} state={connectivity.state} />
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
