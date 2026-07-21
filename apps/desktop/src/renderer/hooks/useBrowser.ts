import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import type { BrowserRestoreState, BrowserState } from '../../shared/contracts'
import { sanitizeBrowserMetadata } from '../../shared/browserPersistence'

const emptyState: BrowserState = { tabs: [], activeTabId: null, download: null, notice: null }

export function browserStateForPersistence(state: BrowserState): BrowserRestoreState {
  return {
    tabs: state.tabs.map(tab => sanitizeBrowserMetadata({
      tabId: tab.tabId,
      url: tab.url,
      title: tab.title,
      faviconUrl: tab.faviconUrl,
      associatedJobId: tab.associatedJobId
    })),
    activeTabId: state.activeTabId
  }
}

export function useBrowser(
  restoredState: BrowserRestoreState,
  workspaceHydrated: boolean,
  visible: boolean,
  layoutSignal: string,
  onPersist: (state: BrowserRestoreState) => void
) {
  const bridge = useRef(window.jobos?.browser).current
  const [state, setState] = useState<BrowserState>(emptyState)
  const [message, setMessage] = useState('Browser ready')
  const viewportRef = useRef<HTMLDivElement | null>(null)
  const restored = useRef(false)
  const explicitBrowserAction = useRef(false)
  const persistedKey = useRef(JSON.stringify(restoredState))
  const requestedRestoreKey = useRef(JSON.stringify(restoredState))

  const acceptState = useCallback((next: BrowserState, explicit = false) => {
    setState(next)
    if (explicit) explicitBrowserAction.current = true
    if (!restored.current || !explicitBrowserAction.current) return
    const durable = browserStateForPersistence(next)
    const key = JSON.stringify(durable)
    if (key !== persistedKey.current) {
      persistedKey.current = key
      onPersist(durable)
    }
  }, [onPersist])

  useEffect(() => bridge?.subscribe(next => acceptState(next)), [acceptState, bridge])

  useEffect(() => {
    if (!bridge || !workspaceHydrated || restored.current) return
    restored.current = true
    persistedKey.current = JSON.stringify(restoredState)
    requestedRestoreKey.current = JSON.stringify(restoredState)
    void bridge.restore(restoredState).then(next => {
      setState(next)
      persistedKey.current = JSON.stringify(browserStateForPersistence(next))
    }).catch(error => {
      setMessage(error instanceof Error ? error.message : 'Browser restore failed')
    })
  }, [acceptState, bridge, restoredState, workspaceHydrated])

  useEffect(() => {
    if (!bridge || !restored.current || explicitBrowserAction.current) return
    const requestedKey = JSON.stringify(restoredState)
    if (requestedKey === requestedRestoreKey.current) return
    requestedRestoreKey.current = requestedKey
    void bridge.restore(restoredState).then(next => {
      setState(next)
      persistedKey.current = JSON.stringify(browserStateForPersistence(next))
    }).catch(error => setMessage(error instanceof Error ? error.message : 'Browser recovery failed'))
  }, [bridge, restoredState])

  const activeTab = useMemo(
    () => state.tabs.find(tab => tab.tabId === state.activeTabId) ?? null,
    [state]
  )

  useEffect(() => {
    if (!bridge) return
    const updateBounds = () => {
      const rect = viewportRef.current?.getBoundingClientRect()
      void bridge.setBounds({
        x: rect?.x ?? 0,
        y: rect?.y ?? 0,
        width: rect?.width ?? 0,
        height: rect?.height ?? 0,
        visible: visible && !activeTab?.error && Boolean(rect?.width && rect?.height)
      })
    }
    updateBounds()
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(updateBounds)
    if (viewportRef.current) observer?.observe(viewportRef.current)
    window.addEventListener('resize', updateBounds)
    return () => {
      observer?.disconnect()
      window.removeEventListener('resize', updateBounds)
      void bridge.setBounds({ x: 0, y: 0, width: 0, height: 0, visible: false })
    }
  }, [activeTab?.error, bridge, layoutSignal, state.activeTabId, visible])

  const run = useCallback(async (operation: () => Promise<BrowserState>) => {
    try {
      acceptState(await operation(), true)
      setMessage('Browser ready')
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Browser action failed')
    }
  }, [acceptState])

  return {
    bridgeAvailable: Boolean(bridge),
    state,
    activeTab,
    message,
    viewportRef,
    create: (url?: string, jobId?: string | null) => bridge && run(() => bridge.create(url, jobId)),
    select: (tabId: string) => bridge && run(() => bridge.select(tabId)),
    close: (tabId: string) => bridge && run(() => bridge.close(tabId)),
    reorder: (tabIds: string[]) => bridge && run(() => bridge.reorder(tabIds)),
    navigate: (tabId: string, input: string) => bridge && run(() => bridge.navigate(tabId, input)),
    back: (tabId: string) => bridge && run(() => bridge.back(tabId)),
    forward: (tabId: string) => bridge && run(() => bridge.forward(tabId)),
    reload: (tabId: string) => bridge && run(() => bridge.reload(tabId)),
    stop: (tabId: string) => bridge && run(() => bridge.stop(tabId)),
    extractJob: async (tabId: string) => {
      if (!bridge) throw new Error('Browser surface unavailable')
      return bridge.extractJob(tabId)
    },
    associate: async (tabId: string, jobId: string | null) => {
      if (!bridge) throw new Error('Browser surface unavailable')
      const next = await bridge.associate(tabId, jobId)
      acceptState(next, true)
      setMessage('Browser ready')
      return next
    },
    copyBlockedUrl: (tabId: string) => bridge && run(() => bridge.copyBlockedUrl(tabId))
  }
}
