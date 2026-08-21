import { AgentPanel } from './components/AgentPanel'
import { CenterWorkspace, type JobListingRequest } from './components/CenterWorkspace'
import { JobNavigator } from './components/JobNavigator'
import { SettingsPanel } from './components/SettingsPanel'
import { StatusBar } from './components/StatusBar'
import { WorkbenchLayout } from './components/WorkbenchLayout'
import { CareerProfileWorkspace } from './components/CareerProfileWorkspace'
import { hasCachedCareerProfile } from './hooks/useCareerProfile'
import { WorkspaceBar, type WorkspaceBarWorkspace } from './components/WorkspaceBar'
import { BrowseWorkspace } from './components/BrowseWorkspace'
import { useAgentAvatarPreference } from './agent-avatar/useAgentAvatarPreference'
import { DocxDocumentEditorShell } from './document-editor/DocxDocumentEditorShell'
import { useConnectivity } from './hooks/useConnectivity'
import { useJobs } from './hooks/useJobs'
import { useAgentSessions } from './hooks/useAgentSessions'
import { useWorkspace } from './hooks/useWorkspace'
import { useTheme } from './theme/useTheme'
import { useCallback, useEffect, useRef, useState } from 'react'
import type { DocxOpenResult } from '../shared/docxDocuments'
import type { SetupSnapshot } from '../shared/contracts'
import { OnboardingScreen } from './onboarding/OnboardingScreen'

export function App() {
  const [setup, setSetup] = useState<SetupSnapshot | null>(
    window.jobos?.setup ? null : { state: 'ready', message: 'JobOS is configured' }
  )

  useEffect(() => {
    if (!window.jobos?.setup) return
    let active = true
    void window.jobos.setup.get().then(value => { if (active) setSetup(value) }).catch(() => {
      if (active) setSetup({ state: 'required', message: 'JobOS setup is required' })
    })
    return () => { active = false }
  }, [])

  if (setup === null) return <div className="onboarding-loading" role="status">Checking local setup…</div>
  if (setup.state !== 'ready') return <OnboardingScreen initial={setup} />
  return <WorkbenchApp />
}

function WorkbenchApp() {
  const connectivity = useConnectivity()
  const agentSessions = useAgentSessions()
  const activeJobContext = agentSessions.activeSession?.summary.jobContext ?? null
  const jobState = useJobs(
    agentSessions.activeId,
    activeJobContext,
    agentSessions.updateJobContext
  )
  const layoutState = useWorkspace(
    agentSessions.activeId,
    activeJobContext,
    agentSessions.updateJobContext
  )
  const theme = useTheme()
  const agentAvatar = useAgentAvatarPreference()
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [settingsPreparing, setSettingsPreparing] = useState(false)
  const [careerProfileEnabled, setCareerProfileEnabled] = useState(false)
  const [careerProfileOpen, setCareerProfileOpen] = useState(false)
  const [browseDetachState, setBrowseDetachState] = useState<'idle' | 'preparing' | 'ready' | 'error'>('idle')
  const [jobListingRequest, setJobListingRequest] = useState<JobListingRequest | null>(null)
  const [documentMutationGeneration, setDocumentMutationGeneration] = useState(0)
  const [documentPreviewMode, setDocumentPreviewMode] = useState<'pdf' | 'docx'>('pdf')
  const [editingDocument, setEditingDocument] = useState<DocxOpenResult | null>(null)
  const [panelReorderActive, setPanelReorderActive] = useState(false)
  const nextJobListingRequestId = useRef(0)
  const latestNavigatorSelection = useRef(0)
  const browseTransitionGeneration = useRef(0)
  const panelReorderGeneration = useRef(0)
  const navigatorSelectionQueue = useRef<Promise<unknown>>(Promise.resolve())
  const prepareClose = useRef<() => Promise<boolean>>(async () => true)
  const activePreset = layoutState.workspace.selectedPreset
  const activeTopLevelWorkspace = layoutState.workspace.activeTopLevelWorkspace ?? activePreset
  const activeLayout = layoutState.workspace.layouts[activePreset]
  const browseVisible = activeTopLevelWorkspace === 'browse' && browseDetachState === 'ready'
  const browserTransitionPending = browseDetachState === 'preparing'
  const nativeBrowserVisible = layoutState.hydrated && activeTopLevelWorkspace !== 'browse' && !careerProfileOpen && !browserTransitionPending && !activeLayout.collapsed.includes('center') && !settingsOpen && !settingsPreparing

  useEffect(() => {
    const bridge = window.jobos?.careerProfile
    if (!bridge) return
    let active = true
    void bridge.availability()
      .then(result => { if (active) setCareerProfileEnabled(result.enabled) })
      .catch(async () => {
        const cached = await hasCachedCareerProfile(bridge)
        if (active) setCareerProfileEnabled(cached)
      })
    return () => { active = false }
  }, [])

  const changePanelReorderInteraction = useCallback(async (active: boolean) => {
    if (!active) {
      panelReorderGeneration.current += 1
      setPanelReorderActive(false)
      return true
    }
    const generation = panelReorderGeneration.current + 1
    panelReorderGeneration.current = generation
    setPanelReorderActive(true)
    if (nativeBrowserVisible && window.jobos?.browser) {
      try {
        await window.jobos.browser.setBounds({ x: 0, y: 0, width: 0, height: 0, visible: false })
      } catch {
        if (generation === panelReorderGeneration.current) setPanelReorderActive(false)
        return false
      }
    }
    if (generation !== panelReorderGeneration.current) return false
    return true
  }, [nativeBrowserVisible])

  useEffect(() => window.jobos?.lifecycle?.subscribePrepareClose(
    () => prepareClose.current()
  ), [])

  useEffect(() => setDocumentPreviewMode('pdf'), [jobState.selectedJobId])

  useEffect(() => {
    const handleAgentShortcut = (event: KeyboardEvent) => {
      if (!document.querySelector('.agent-panel')) return
      if (!event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return
      const key = event.key.toLowerCase()
      if (key !== 'n' && !/^[1-5]$/.test(event.key)) return
      if (settingsOpen || settingsPreparing || document.querySelector('[aria-modal="true"], dialog[open], [role="dialog"], [role="alertdialog"]')) {
        event.preventDefault()
        return
      }
      if (key === 'n') {
        event.preventDefault()
        void agentSessions.create().then(handled => {
          if (handled) requestAnimationFrame(() => document.querySelector<HTMLTextAreaElement>('#agent-message')?.focus())
        })
        return
      }
      const index = Number(event.key) - 1
      if (!agentSessions.order[index]) return
      event.preventDefault()
      agentSessions.selectByIndex(index)
      requestAnimationFrame(() => document.querySelector<HTMLTextAreaElement>('#agent-message')?.focus())
    }
    window.addEventListener('keydown', handleAgentShortcut)
    return () => window.removeEventListener('keydown', handleAgentShortcut)
  }, [agentSessions.create, agentSessions.order, agentSessions.selectByIndex, settingsOpen, settingsPreparing])

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

  const selectJobFromNavigator = (jobId: string, openInResearch = false, canonicalUrl?: string): Promise<boolean> => {
    const selectionRequest = latestNavigatorSelection.current + 1
    latestNavigatorSelection.current = selectionRequest
    const job = jobState.jobs.find(candidate => candidate.jobId === jobId)
    const listingUrl = canonicalUrl ?? job?.canonicalUrl
    if (!listingUrl) return Promise.resolve(false)
    const operation = navigatorSelectionQueue.current.then(async () => {
      if (selectionRequest !== latestNavigatorSelection.current) return false
      if (!await jobState.selectJob(jobId)) return false
      try {
        await layoutState.showBrowser()
      } catch {
        // The visible workspace update already succeeded; navigation should not depend on persistence.
      }
      if (selectionRequest !== latestNavigatorSelection.current) return false
      const opened = await new Promise<boolean>(resolve => {
        nextJobListingRequestId.current += 1
        setJobListingRequest({
          requestId: nextJobListingRequestId.current,
          jobId,
          canonicalUrl: listingUrl,
          onComplete: resolve
        })
      })
      if (!opened || selectionRequest !== latestNavigatorSelection.current) return false
      if (openInResearch) {
        try {
          await layoutState.selectPreset('research')
        } catch {
          // The local workspace transition has already committed.
        }
        setBrowseDetachState('idle')
      }
      return true
    })
    navigatorSelectionQueue.current = operation.catch(() => undefined)
    return operation
  }

  const detachBrowserForBrowse = useCallback(async (generation: number) => {
    setBrowseDetachState('preparing')
    try {
      await window.jobos?.browser?.setBounds({ x: 0, y: 0, width: 0, height: 0, visible: false })
      if (generation !== browseTransitionGeneration.current) {
        setBrowseDetachState(current => current === 'preparing' ? 'idle' : current)
        return false
      }
      setBrowseDetachState('ready')
      return true
    } catch {
      if (generation !== browseTransitionGeneration.current) {
        setBrowseDetachState(current => current === 'preparing' ? 'idle' : current)
        return false
      }
      setBrowseDetachState('error')
      return false
    }
  }, [])

  useEffect(() => {
    if (!layoutState.hydrated || activeTopLevelWorkspace !== 'browse' || browseDetachState !== 'idle') return
    const generation = browseTransitionGeneration.current + 1
    browseTransitionGeneration.current = generation
    void detachBrowserForBrowse(generation)
  }, [activeTopLevelWorkspace, browseDetachState, detachBrowserForBrowse, layoutState.hydrated])

  const changeTopLevelWorkspace = async (workspaceId: WorkspaceBarWorkspace) => {
    if (workspaceId === 'career-profile') {
      if (!careerProfileEnabled || careerProfileOpen) return
      browseTransitionGeneration.current += 1
      setBrowseDetachState('idle')
      try {
        await window.jobos?.browser?.setBounds({ x: 0, y: 0, width: 0, height: 0, visible: false })
      } catch {
        return
      }
      setCareerProfileOpen(true)
      return
    }
    setCareerProfileOpen(false)
    if (workspaceId !== 'browse') {
      browseTransitionGeneration.current += 1
      setBrowseDetachState(current => current === 'preparing' ? current : 'idle')
      await layoutState.selectTopLevelWorkspace(workspaceId)
      return
    }
    if (browseDetachState === 'preparing' || browseVisible) return
    const generation = browseTransitionGeneration.current + 1
    browseTransitionGeneration.current = generation
    if (!await detachBrowserForBrowse(generation) || generation !== browseTransitionGeneration.current) return
    if (activeTopLevelWorkspace !== 'browse') await layoutState.selectTopLevelWorkspace('browse')
  }

  return (
    <div className="app-shell" data-layout={activePreset} data-workspace={activeTopLevelWorkspace}>
      <WorkspaceBar
        activeWorkspace={careerProfileOpen ? 'career-profile' : activeTopLevelWorkspace}
        careerProfileEnabled={careerProfileEnabled}
        onWorkspaceChange={workspaceId => { void changeTopLevelWorkspace(workspaceId) }}
        onReset={layoutState.reset}
        onToggleMode={theme.toggleMode}
        themeMode={theme.mode}
      />
      <div className="workspace-content">
      <div className="workbench-layer" hidden={browseVisible || careerProfileOpen}>
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
          avatarId={agentAvatar.avatarId}
          apiState={connectivity.state}
          contextLabel={jobState.selectedJob ? `${jobState.selectedJob.company} · ${jobState.selectedJob.title}` : 'No active job'}
          onArtifactRendered={showPublishedDocument}
          sessions={agentSessions}
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
          browserVisible={nativeBrowserVisible && !panelReorderActive}
          onCreateSaveSession={agentSessions.createJobless}
          documentMutationGeneration={documentMutationGeneration}
          documentPreviewMode={documentPreviewMode}
          jobListingRequest={jobListingRequest}
          jobs={jobState.jobs}
          layoutSignal={`${activePreset}:${activeLayout.order.join(',')}:${activeLayout.collapsed.join(',')}`}
          onBrowserPersist={layoutState.updateBrowserState}
          onDocumentPersist={layoutState.updateDocumentState}
          onDocumentPreviewModeChange={setDocumentPreviewMode}
          onJobSaved={async (jobId, conversationId) => {
            if (!await jobState.selectJobForConversation(conversationId, jobId)) {
              throw new Error('The job was saved, but JobOS could not attach it to the new agent session.')
            }
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
          onRemoveDemo={jobState.removeDemo}
          onStatusGroupChange={jobState.setStatusGroup}
          query={jobState.query}
          selectedJobId={jobState.selectedJobId}
          selectedJobDetail={jobState.selectedJobDetail}
          sortMode={jobState.sortMode}
          statusGroup={jobState.statusGroup}
        />}
        onCollapse={layoutState.collapse}
        onMove={layoutState.move}
        onReorderInteractionChange={changePanelReorderInteraction}
        onResize={layoutState.resize}
        workspace={layoutState.workspace}
      />
      )}
      </div>
      {careerProfileOpen ? (
        <CareerProfileWorkspace
          hasActiveTurn={Boolean(agentSessions.activeSession?.summary.activeTurn)}
          online={connectivity.state === 'connected'}
        />
      ) : null}
      {browseVisible ? (
        <BrowseWorkspace
          activeJobId={jobState.selectedJobId}
          focusJobId={layoutState.workspace.browseFocusJobId}
          mode={layoutState.workspace.browseMode}
          onOpenJob={(jobId, canonicalUrl) => selectJobFromNavigator(jobId, true, canonicalUrl)}
          onUpdate={layoutState.updateBrowseState}
          query={layoutState.workspace.browseQuery}
          railWidth={layoutState.workspace.browseRailWidth}
          sortMode={layoutState.workspace.browseSortMode}
          statusGroup={layoutState.workspace.browseStatusGroup}
        />
      ) : null}
      {activeTopLevelWorkspace === 'browse' && browseDetachState === 'error' ? (
        <div className="browse-startup-error" role="alert">
          <p>Browse could not open because the browser view could not be hidden.</p>
          <button onClick={() => {
            const generation = browseTransitionGeneration.current + 1
            browseTransitionGeneration.current = generation
            void detachBrowserForBrowse(generation)
          }} type="button">Retry Browse</button>
        </div>
      ) : null}
      </div>
      <p aria-live="polite" className="layout-announcement">{layoutState.announcement}</p>
      <StatusBar apiVersion={connectivity.apiVersion} message={connectivity.message} onOpenSettings={() => { void openSettings() }} state={connectivity.state} />
      {settingsOpen ? (
        <SettingsPanel
          activeAgentAvatarId={agentAvatar.avatarId}
          activeThemeId={theme.themeId}
          mode={theme.mode}
          onClose={() => setSettingsOpen(false)}
          onSelectAgentAvatar={agentAvatar.selectAvatar}
          onSelectTheme={theme.selectTheme}
        />
      ) : null}
    </div>
  )
}
