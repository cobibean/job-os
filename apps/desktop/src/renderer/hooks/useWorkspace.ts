import { useCallback, useEffect, useRef, useState } from 'react'

import type { LayoutPreset, PanelId, WorkspaceSnapshot } from '../workspaceLayout'
import type { BrowserRestoreState } from '../../shared/contracts'
import {
  canonicalWorkspace,
  browserRepairMessage,
  movePanel,
  resetActivePreset,
  resizeAdjacentPanels,
  setPanelCollapsed
} from '../workspaceLayout'

type WorkspaceUpdate = (current: WorkspaceSnapshot) => WorkspaceSnapshot

export function useWorkspace(selectedJobId: string | null) {
  const [workspace, setWorkspace] = useState<WorkspaceSnapshot>(canonicalWorkspace)
  const [announcement, setAnnouncement] = useState('Layout controls ready')
  const [hydrated, setHydrated] = useState(!window.jobos?.workspace)
  const revision = useRef(0)
  const latest = useRef(workspace)
  const queue = useRef(Promise.resolve())
  const bridge = window.jobos?.workspace
  const hydrating = useRef(Boolean(bridge))
  const pendingHydrationUpdates = useRef<WorkspaceUpdate[]>([])
  const startupRecoveryUpdates = useRef<WorkspaceUpdate[]>([])
  const recoveringStartup = useRef(false)
  const selectedJobIdRef = useRef(selectedJobId)
  selectedJobIdRef.current = selectedJobId

  const persist = useCallback((next: WorkspaceSnapshot) => {
    if (!bridge) return
    latest.current = next
    queue.current = queue.current.then(async () => {
      try {
        const saved = await bridge.save({
          ...latest.current,
          revision: revision.current,
          selectedJobId: selectedJobIdRef.current ?? latest.current.selectedJobId
        })
        revision.current = saved.revision
        if (recoveringStartup.current) {
          recoveringStartup.current = false
          startupRecoveryUpdates.current = []
        }
      } catch (error) {
        if (!(error instanceof Error) || !error.message.includes('revision conflict')) throw error
        const remote = await bridge.get()
        const reconciled = recoveringStartup.current
          ? startupRecoveryUpdates.current.reduce((current, update) => update(current), remote)
          : latest.current
        revision.current = remote.revision
        latest.current = reconciled
        setWorkspace(reconciled)
        const saved = await bridge.save({
          ...reconciled,
          revision: remote.revision,
          selectedJobId: selectedJobIdRef.current ?? reconciled.selectedJobId
        })
        revision.current = saved.revision
        if (recoveringStartup.current) {
          recoveringStartup.current = false
          startupRecoveryUpdates.current = []
        }
      }
    }).catch(() => setAnnouncement('Layout save failed; changes remain visible'))
  }, [bridge])

  useEffect(() => {
    if (!bridge) return
    hydrating.current = true
    let active = true
    bridge.get().then(restored => {
      if (!active) return
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

  const commit = useCallback((update: WorkspaceUpdate, message: string) => {
    const next = update(latest.current)
    latest.current = next
    setWorkspace(next)
    if (bridge && hydrating.current) pendingHydrationUpdates.current.push(update)
    else {
      if (recoveringStartup.current) startupRecoveryUpdates.current.push(update)
      persist(next)
    }
    if (message) setAnnouncement(message)
  }, [bridge, persist])

  const selectPreset = (preset: LayoutPreset) => commit(current => ({
    ...current,
    selectedPreset: preset,
    activeCenterSurface: preset === 'research' ? 'browser' : preset === 'review' ? 'document' : current.activeCenterSurface
  }), `${preset.replace('-', ' ')} layout selected`)

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

  return { workspace, announcement, hydrated, selectPreset, resize, collapse, move, reset, updateBrowserState }
}

function panelLabel(panel: PanelId) {
  return panel === 'jobs' ? 'Job navigation' : panel === 'center' ? 'Center workspace' : 'Agent chat'
}
