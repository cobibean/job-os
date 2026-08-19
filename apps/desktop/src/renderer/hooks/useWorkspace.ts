import { useCallback, useEffect, useRef, useState } from 'react'

import type { BrowseMode, LayoutPreset, PanelId, TopLevelWorkspace, WorkspaceSnapshot } from '../workspaceLayout'
import type { AgentSessionJobContext, JobSortMode } from '../../shared/contracts'
import type { BrowserRestoreState } from '../../shared/contracts'
import type { WorkspaceSnapshot as BridgeWorkspaceSnapshot } from '../../shared/contracts'
import {
  canonicalWorkspace,
  browserRepairMessage,
  movePanel,
  resetActivePreset,
  resizeAdjacentPanels,
  setPanelCollapsed
} from '../workspaceLayout'

type WorkspaceUpdate = (current: WorkspaceSnapshot) => WorkspaceSnapshot

function withBrowseDefaults(snapshot: BridgeWorkspaceSnapshot): WorkspaceSnapshot {
  return {
    ...snapshot,
    activeTopLevelWorkspace: snapshot.activeTopLevelWorkspace ?? snapshot.selectedPreset,
    browseMode: snapshot.browseMode ?? 'list',
    browseFocusJobId: snapshot.browseFocusJobId ?? null,
    browseQuery: snapshot.browseQuery ?? '',
    browseStatusGroup: snapshot.browseStatusGroup ?? '',
    browseSortMode: snapshot.browseSortMode ?? 'manual',
    browseRailWidth: snapshot.browseRailWidth ?? 292
  }
}

export function useWorkspace(
  conversationId: string | null,
  jobContext: AgentSessionJobContext | null,
  onJobContextChange?: (conversationId: string, context: AgentSessionJobContext) => void
) {
  const [workspace, setWorkspace] = useState<WorkspaceSnapshot>(canonicalWorkspace)
  const [announcement, setAnnouncement] = useState('Layout controls ready')
  const [hydrated, setHydrated] = useState(!window.jobos?.workspace)
  const revision = useRef(0)
  const latest = useRef(workspace)
  const queue = useRef(Promise.resolve())
  const bridge = useRef(window.jobos?.workspace).current
  const hydrating = useRef(Boolean(bridge))
  const pendingHydrationUpdates = useRef<WorkspaceUpdate[]>([])
  const startupRecoveryUpdates = useRef<WorkspaceUpdate[]>([])
  const recoveringStartup = useRef(false)
  const conversationIdRef = useRef(conversationId)
  conversationIdRef.current = conversationId

  const persist = useCallback((next: WorkspaceSnapshot): Promise<void> => {
    if (!bridge) return Promise.resolve()
    latest.current = next
    const operation = queue.current.then(async () => {
      try {
        const saved = await bridge.save({
          ...latest.current,
          revision: revision.current,
          selectedJobId: null,
          activeArtifactId: null,
          activeArtifactPage: 1,
          activeArtifactZoom: 1
        })
        revision.current = saved.revision
        if (recoveringStartup.current) {
          recoveringStartup.current = false
          startupRecoveryUpdates.current = []
        }
      } catch (error) {
        if (!(error instanceof Error) || !error.message.includes('revision conflict')) throw error
        const remote = withBrowseDefaults(await bridge.get())
        const reconciled = recoveringStartup.current
          ? startupRecoveryUpdates.current.reduce((current, update) => update(current), remote)
          : latest.current
        revision.current = remote.revision
        latest.current = reconciled
        setWorkspace(reconciled)
        const saved = await bridge.save({
          ...reconciled,
          revision: remote.revision,
          selectedJobId: null,
          activeArtifactId: null,
          activeArtifactPage: 1,
          activeArtifactZoom: 1
        })
        revision.current = saved.revision
        if (recoveringStartup.current) {
          recoveringStartup.current = false
          startupRecoveryUpdates.current = []
        }
      }
    })
    queue.current = operation.catch(() => {
      setAnnouncement('Layout save failed; changes remain visible')
    })
    return operation
  }, [bridge])

  useEffect(() => {
    if (!bridge) return
    hydrating.current = true
    let active = true
    bridge.get().then(rawRestored => {
      if (!active) return
      const restored = withBrowseDefaults(rawRestored)
      const pending = pendingHydrationUpdates.current
      pendingHydrationUpdates.current = []
      const reconciled = pending.reduce((current, update) => update(current), restored)
      revision.current = restored.revision
      latest.current = reconciled
      hydrating.current = false
      setHydrated(true)
      setWorkspace(reconciled)
      if (restored.repairedPresets.length) {
        setAnnouncement(`Recovered ${restored.repairedPresets.join(', ')} layout`)
      } else if (restored.repairedBrowser) {
        setAnnouncement(browserRepairMessage(restored.browserRepairReasons, true) ?? 'Saved browser metadata was repaired.')
      }
      if (pending.length) {
        recoveringStartup.current = true
        startupRecoveryUpdates.current = pending
        persist(reconciled)
      }
    }).catch(() => {
      if (!active) return
      const pending = pendingHydrationUpdates.current
      pendingHydrationUpdates.current = []
      hydrating.current = false
      setHydrated(true)
      setAnnouncement('Using safe default layout')
      recoveringStartup.current = true
      startupRecoveryUpdates.current = pending
      if (pending.length) {
        persist(latest.current)
      }
    })
    return () => { active = false }
  }, [bridge, persist])

  const commit = useCallback((update: WorkspaceUpdate, message: string): Promise<void> => {
    const next = update(latest.current)
    latest.current = next
    setWorkspace(next)
    let persistence = Promise.resolve()
    if (bridge && hydrating.current) pendingHydrationUpdates.current.push(update)
    else {
      if (recoveringStartup.current) startupRecoveryUpdates.current.push(update)
      persistence = persist(next)
    }
    if (message) setAnnouncement(message)
    return persistence
  }, [bridge, persist])

  useEffect(() => {
    if (!hydrated) return
    const next = {
      ...latest.current,
      selectedJobId: jobContext?.selectedJobId ?? null,
      activeArtifactId: jobContext?.activeArtifactId ?? null,
      activeArtifactPage: jobContext?.activeArtifactPage ?? 1,
      activeArtifactZoom: jobContext?.activeArtifactZoom ?? 1
    }
    latest.current = next
    setWorkspace(next)
  }, [conversationId, hydrated, jobContext])

  const selectPreset = (preset: LayoutPreset) => commit(current => ({
    ...current,
    selectedPreset: preset,
    activeTopLevelWorkspace: preset,
    activeCenterSurface: preset === 'research' ? 'browser' : preset === 'review' ? 'document' : current.activeCenterSurface
  }), `${preset.replace('-', ' ')} layout selected`)

  const selectTopLevelWorkspace = (workspaceId: TopLevelWorkspace) => {
    if (workspaceId !== 'browse') return selectPreset(workspaceId)
    return commit(current => ({ ...current, activeTopLevelWorkspace: 'browse' }), 'Browse workspace selected')
  }

  const updateBrowseState = useCallback((update: Partial<{
    mode: BrowseMode
    focusJobId: string | null
    query: string
    statusGroup: string
    sortMode: JobSortMode
    railWidth: number
  }>, message = '') => commit(current => ({
    ...current,
    browseMode: update.mode ?? current.browseMode,
    browseFocusJobId: update.focusJobId === undefined ? current.browseFocusJobId : update.focusJobId,
    browseQuery: update.query ?? current.browseQuery,
    browseStatusGroup: update.statusGroup ?? current.browseStatusGroup,
    browseSortMode: update.sortMode ?? current.browseSortMode,
    browseRailWidth: update.railWidth ?? current.browseRailWidth
  }), message), [commit])

  const resize = (before: PanelId, after: PanelId, delta: number) => commit(
    current => {
      const next = resizeAdjacentPanels(current, before, after, delta)
      setAnnouncement(`${panelLabel(before)} ${next.layouts[next.selectedPreset].widths[before]} pixels`)
      return next
    },
    ''
  )

  const collapse = (panel: PanelId, collapsed: boolean) => commit(
    current => setPanelCollapsed(current, panel, collapsed),
    `${panelLabel(panel)} ${collapsed ? 'collapsed' : 'reopened'}`
  )

  const move = (panel: PanelId, targetIndex: number) => commit(
    current => movePanel(current, panel, targetIndex),
    `${panelLabel(panel)} moved to position ${targetIndex + 1}`
  )

  const reset = () => commit(resetActivePreset, `${panelLabel('center')} layout reset`)

  const updateBrowserState = useCallback((browser: BrowserRestoreState) => commit(current => ({
    ...current,
    browserTabs: browser.tabs,
    activeBrowserTabId: browser.activeTabId,
    repairedBrowser: false,
    browserRepairReasons: []
  }), ''), [commit])

  const updateDocumentState = useCallback(async (artifactId: string | null, page: number, zoom: number) => {
    const ownerConversationId = conversationId
    if (!ownerConversationId || !bridge) return
    const context = await bridge.saveDocumentView(ownerConversationId, artifactId, page, zoom)
    if (ownerConversationId !== conversationIdRef.current) return
    onJobContextChange?.(ownerConversationId, context)
    const next = { ...latest.current, activeArtifactId: artifactId, activeArtifactPage: page, activeArtifactZoom: zoom }
    latest.current = next
    setWorkspace(next)
  }, [bridge, conversationId, onJobContextChange])

  const showDocument = useCallback(() => commit(current => ({
    ...current,
    activeCenterSurface: 'document'
  }), 'Newest resume opened for review'), [commit])

  const showBrowser = useCallback(() => commit(current => ({
    ...current,
    activeCenterSurface: 'browser'
  }), 'Job listing opened in browser'), [commit])

  return { workspace, announcement, hydrated, selectPreset, selectTopLevelWorkspace, updateBrowseState, resize, collapse, move, reset, updateBrowserState, updateDocumentState, showDocument, showBrowser }
}

function panelLabel(panel: PanelId) {
  return panel === 'jobs' ? 'Job navigation' : panel === 'center' ? 'Center workspace' : 'Agent chat'
}
