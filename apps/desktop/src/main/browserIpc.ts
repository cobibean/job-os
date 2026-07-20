import type { IpcMain, IpcMainInvokeEvent } from 'electron'

import type { BrowserRestoreState, BrowserState } from '../shared/contracts.js'
import { BROWSER_PERSISTENCE_LIMITS, recoverBrowserRestoreState } from '../shared/browserPersistence.js'

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
  trusted: (event: IpcMainInvokeEvent) => BrowserRestoreTarget
): void {
  ipc.handle('jobos:browser:restore', async (event, state: unknown) => (
    await trusted(event).restore(prepareBrowserRestoreState(state))
  ))
}
