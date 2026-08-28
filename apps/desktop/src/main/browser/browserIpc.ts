import type { IpcMain, IpcMainInvokeEvent } from 'electron'

import type { BrowserBounds, BrowserRestoreState, BrowserState } from '../../shared/contracts.js'
import { BROWSER_PERSISTENCE_LIMITS, recoverBrowserRestoreState } from '../../shared/browserPersistence.js'
import type { BrowserManager } from './browser.js'

export const BROWSER_RESTORE_CANDIDATE_LIMIT = BROWSER_PERSISTENCE_LIMITS.tabs * 5

type BrowserRestoreTarget = {
  restore: (state: BrowserRestoreState) => Promise<BrowserState>
}

export function prepareBrowserRestoreState(value: unknown): BrowserRestoreState {
  if (!value || typeof value !== 'object') throw new Error('Invalid browser restore state')
  const state = value as Partial<BrowserRestoreState> & { tabs?: unknown[] }
  if (!Array.isArray(state.tabs) || state.tabs.length > BROWSER_RESTORE_CANDIDATE_LIMIT) {
    throw new Error('Invalid browser restore state')
  }
  if (
    state.activeTabId !== null
    && state.activeTabId !== undefined
    && (typeof state.activeTabId !== 'string' || state.activeTabId.length > BROWSER_PERSISTENCE_LIMITS.tabId)
  ) throw new Error('Invalid browser restore state')
  return recoverBrowserRestoreState({
    tabs: state.tabs as BrowserRestoreState['tabs'],
    activeTabId: state.activeTabId ?? null
  })
}

export function registerBrowserRestoreHandler(
  ipc: Pick<IpcMain, 'handle'>,
  trusted: (event: IpcMainInvokeEvent) => BrowserRestoreTarget,
  onRestored: () => void = () => undefined
): void {
  ipc.handle('jobos:browser:restore', async (event, state: unknown) => {
    const restored = await trusted(event).restore(prepareBrowserRestoreState(state))
    onRestored()
    return restored
  })
}

export function registerBrowserIpc(
  ipc: Pick<IpcMain, 'handle'>,
  assertTrustedRenderer: (event: IpcMainInvokeEvent) => void,
  getBrowserManager: () => BrowserManager | null,
  onRestored: () => void = () => undefined
): void {
  const trusted = (event: IpcMainInvokeEvent) => {
    assertTrustedRenderer(event)
    const browserManager = getBrowserManager()
    if (!browserManager) throw new Error('Browser surface unavailable')
    return browserManager
  }
  const tabId = (value: unknown) => {
    if (typeof value !== 'string' || !value || value.length > 128) throw new Error('Invalid browser tab')
    return value
  }
  ipc.handle('jobos:browser:get-state', event => trusted(event).getState())
  registerBrowserRestoreHandler(ipc, trusted, onRestored)
  ipc.handle('jobos:browser:create', (event, url?: string, jobId?: string | null) => {
    if (url !== undefined && (typeof url !== 'string' || url.length > 8192)) throw new Error('Invalid browser address')
    if (jobId !== undefined && jobId !== null && typeof jobId !== 'string') throw new Error('Invalid job association')
    return trusted(event).create(url, jobId)
  })
  ipc.handle('jobos:browser:select', (event, id: string) => trusted(event).select(tabId(id)))
  ipc.handle('jobos:browser:close', (event, id: string) => trusted(event).close(tabId(id)))
  ipc.handle('jobos:browser:reorder', (event, ids: string[]) => {
    if (!Array.isArray(ids) || ids.some(id => typeof id !== 'string') || new Set(ids).size !== ids.length) throw new Error('Invalid tab order')
    return trusted(event).reorder(ids)
  })
  ipc.handle('jobos:browser:navigate', (event, id: string, input: string) => {
    if (typeof input !== 'string' || input.length > 8192) throw new Error('Invalid browser address')
    return trusted(event).navigate(tabId(id), input)
  })
  ipc.handle('jobos:browser:back', (event, id: string) => trusted(event).back(tabId(id)))
  ipc.handle('jobos:browser:forward', (event, id: string) => trusted(event).forward(tabId(id)))
  ipc.handle('jobos:browser:reload', (event, id: string) => trusted(event).reload(tabId(id)))
  ipc.handle('jobos:browser:stop', (event, id: string) => trusted(event).stop(tabId(id)))
  ipc.handle('jobos:browser:associate', (event, id: string, jobId: string | null) => {
    if (jobId !== null && (typeof jobId !== 'string' || jobId.length > 512)) throw new Error('Invalid job association')
    return trusted(event).associate(tabId(id), jobId)
  })
  ipc.handle('jobos:browser:copy-blocked-url', (event, id: string) => trusted(event).copyBlockedUrl(tabId(id)))
  ipc.handle('jobos:browser:set-bounds', (event, bounds: BrowserBounds) => {
    if (!bounds || ['x', 'y', 'width', 'height'].some(key => !Number.isFinite(bounds[key as keyof BrowserBounds]))) throw new Error('Invalid browser bounds')
    trusted(event).setBounds(bounds)
  })
}
