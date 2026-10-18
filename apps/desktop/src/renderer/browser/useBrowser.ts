import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import type { BrowserRestoreState, BrowserState, BrowserTab } from '../../shared/contracts'
import { normalizeBrowserUrlForPersistence, sanitizeBrowserMetadata } from '../../shared/browserPersistence'

const emptyState: BrowserState = { tabs: [], activeTabId: null, download: null, notice: null }

export function findOpenJobListingTab(
  tabs: BrowserTab[],
  jobId: string,
  canonicalUrl: string
): BrowserTab | null {
  const associated = tabs.find(tab => tab.associatedJobId === jobId)
  if (associated) return associated
  const canonicalKey = normalizeBrowserUrlForPersistence(canonicalUrl, false)
  if (!canonicalKey) return null
  return tabs.find(tab => (
    normalizeBrowserUrlForPersistence(tab.url, false) === canonicalKey
  )) ?? null
}

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
  onPersist: (state: BrowserRestoreState) => void | Promise<void>,
  active = true
) {
  const bridge = useRef(window.jobos?.browser).current
  const activeRef = useRef(active)
  activeRef.current = active
  const [state, setState] = useState<BrowserState>(emptyState)
  const [message, setMessage] = useState('Browser ready')
  const [restorationReady, setRestorationReady] = useState(!bridge)
  const [restorationError, setRestorationError] = useState<string | null>(null)
  const restoreRequest = useRef(0)
  const restoreInput = useRef(restoredState)
  restoreInput.current = restoredState
  const readiness = useRef(restorationReady)
  readiness.current = restorationReady
  const viewportRef = useRef<HTMLDivElement | null>(null)
  const restored = useRef(false)
  const explicitBrowserAction = useRef(false)
  const persistedKey = useRef(JSON.stringify(restoredState))
  const requestedRestoreKey = useRef(JSON.stringify(restoredState))

  const acceptState = useCallback((next: BrowserState, explicit = false): Promise<void> => {
    if (!activeRef.current) return Promise.resolve()
    setState(next)
    if (explicit) explicitBrowserAction.current = true
    if (!restored.current || !explicitBrowserAction.current) return Promise.resolve()
    const durable = browserStateForPersistence(next)
    const key = JSON.stringify(durable)
    if (key === persistedKey.current) return Promise.resolve()
    const previousKey = persistedKey.current
    persistedKey.current = key
    return Promise.resolve(onPersist(durable)).catch(error => {
      if (persistedKey.current === key) persistedKey.current = previousKey
      throw error
    })
  }, [onPersist])

  useEffect(() => {
    if (!active) return undefined
    return bridge?.subscribe(next => { void acceptState(next).catch(() => undefined) })
  }, [acceptState, active, bridge])

  const retryRestore = useCallback(async (): Promise<boolean> => {
    if (!activeRef.current || !bridge) return false
    const request = ++restoreRequest.current
    const input = restoreInput.current
    restored.current = true
    persistedKey.current = JSON.stringify(input)
    requestedRestoreKey.current = JSON.stringify(input)
    setRestorationReady(false)
    readiness.current = false
    setRestorationError(null)
    try {
      const next = await bridge.restore(input)
      if (!activeRef.current || request !== restoreRequest.current) return false
      setState(next)
      persistedKey.current = JSON.stringify(browserStateForPersistence(next))
      setRestorationReady(true)
      readiness.current = true
      setMessage('Browser ready')
      return true
    } catch (error) {
      if (!activeRef.current || request !== restoreRequest.current) return false
      const message = error instanceof Error ? error.message : 'Browser restore failed'
      setMessage(message)
      setRestorationError(message)
      return false
    }
  }, [bridge])

  useEffect(() => {
    if (active && bridge && workspaceHydrated && !restored.current) void retryRestore()
  }, [active, bridge, retryRestore, workspaceHydrated])

  useEffect(() => {
    if (!active || !bridge || !restored.current || explicitBrowserAction.current) return
    if (JSON.stringify(restoredState) !== requestedRestoreKey.current) void retryRestore()
  }, [active, bridge, restoredState, retryRestore])

  useEffect(() => () => { restoreRequest.current += 1 }, [])

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
        visible: active && visible && !activeTab?.error && Boolean(rect?.width && rect?.height)
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
  }, [active, activeTab?.error, bridge, layoutSignal, state.activeTabId, visible])

  const run = useCallback(async (operation: () => Promise<BrowserState>): Promise<boolean> => {
    if (!activeRef.current || !readiness.current) return false
    try {
      const next = await operation()
      if (!activeRef.current) return false
      await acceptState(next, true)
      setMessage('Browser ready')
      return true
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Browser action failed')
      return false
    }
  }, [acceptState])

  const openJobListing = useCallback(async (jobId: string, canonicalUrl: string): Promise<boolean> => {
    if (!bridge) return false
    const existing = findOpenJobListingTab(state.tabs, jobId, canonicalUrl)
    return run(() => existing
      ? bridge.select(existing.tabId)
      : bridge.create(canonicalUrl, jobId))
  }, [bridge, run, state.tabs])

  const reconcileExternalState = useCallback(
    (next: BrowserState) => acceptState(next, true),
    [acceptState]
  )

  return {
    bridgeAvailable: Boolean(bridge),
    restorationReady,
    restorationError,
    retryRestore,
    state,
    activeTab,
    message,
    viewportRef,
    reconcileExternalState,
    openJobListing,
    create: (url?: string, jobId?: string | null) => bridge && run(() => bridge.create(url, jobId)),
    select: (tabId: string) => bridge && run(() => bridge.select(tabId)),
    close: (tabId: string) => bridge && run(() => bridge.close(tabId)),
    reorder: (tabIds: string[]) => bridge && run(() => bridge.reorder(tabIds)),
    navigate: (tabId: string, input: string) => bridge && run(() => bridge.navigate(tabId, input)),
    back: (tabId: string) => bridge && run(() => bridge.back(tabId)),
    forward: (tabId: string) => bridge && run(() => bridge.forward(tabId)),
    reload: (tabId: string) => bridge && run(() => bridge.reload(tabId)),
    stop: (tabId: string) => bridge && run(() => bridge.stop(tabId)),
    associate: async (tabId: string, jobId: string | null) => {
      if (!bridge) throw new Error('Browser surface unavailable')
      const next = await bridge.associate(tabId, jobId)
      await acceptState(next, true)
      setMessage('Browser ready')
      return next
    },
    copyBlockedUrl: (tabId: string) => bridge && run(() => bridge.copyBlockedUrl(tabId))
  }
}

export type BrowserController = ReturnType<typeof useBrowser>
