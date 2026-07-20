import { useCallback, useEffect, useRef, useState } from 'react'

import type { LayoutPreset, PanelId, WorkspaceSnapshot } from '../workspaceLayout'
import {
  canonicalWorkspace,
  movePanel,
  resetActivePreset,
  resizeAdjacentPanels,
  setPanelCollapsed
} from '../workspaceLayout'

export function useWorkspace(selectedJobId: string | null) {
  const [workspace, setWorkspace] = useState<WorkspaceSnapshot>(canonicalWorkspace)
  const [announcement, setAnnouncement] = useState('Layout controls ready')
  const revision = useRef(0)
  const latest = useRef(workspace)
  const queue = useRef(Promise.resolve())
  const bridge = window.jobos?.workspace
  const hydrating = useRef(Boolean(bridge))
  const pendingHydrationUpdates = useRef<Array<(current: WorkspaceSnapshot) => WorkspaceSnapshot>>([])
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
      } catch (error) {
        if (!(error instanceof Error) || !error.message.includes('revision conflict')) throw error
        const remote = await bridge.get()
        revision.current = remote.revision
        const saved = await bridge.save({
          ...latest.current,
          revision: remote.revision,
          selectedJobId: selectedJobIdRef.current ?? latest.current.selectedJobId
        })
        revision.current = saved.revision
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
      setWorkspace(reconciled)
      if (restored.repairedPresets.length) {
        setAnnouncement(`Recovered ${restored.repairedPresets.join(', ')} layout`)
      }
      if (pending.length) persist(reconciled)
    }).catch(() => {
      if (!active) return
      const hadPendingUpdates = pendingHydrationUpdates.current.length > 0
      pendingHydrationUpdates.current = []
      hydrating.current = false
      setAnnouncement('Using safe default layout')
      if (hadPendingUpdates) persist(latest.current)
    })
    return () => { active = false }
  }, [bridge, persist])

  const commit = useCallback((update: (current: WorkspaceSnapshot) => WorkspaceSnapshot, message: string) => {
    const next = update(latest.current)
    latest.current = next
    setWorkspace(next)
    if (bridge && hydrating.current) pendingHydrationUpdates.current.push(update)
    else persist(next)
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

  return { workspace, announcement, selectPreset, resize, collapse, move, reset }
}

function panelLabel(panel: PanelId) {
  return panel === 'jobs' ? 'Job navigation' : panel === 'center' ? 'Center workspace' : 'Agent chat'
}
